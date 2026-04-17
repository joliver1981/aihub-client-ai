# agent_communication_tool.py

from langchain.tools import tool
import json
import logging
from typing import Optional, Dict, Any, List
import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import uuid
from datetime import datetime
import threading
import pyodbc
import os
from CommonUtils import get_db_connection

# Global registry for active agents
AGENT_REGISTRY = {}

# Thread pool for async agent execution
AGENT_EXECUTOR = ThreadPoolExecutor(max_workers=10)

# Global variable to track current agent context
_CURRENT_AGENT_CONTEXT = threading.local()

def set_current_agent_id(agent_id: int):
    """Set the current agent ID in thread-local storage"""
    _CURRENT_AGENT_CONTEXT.agent_id = agent_id

def get_current_agent_id() -> int:
    """Get the current agent ID from thread-local storage"""
    return getattr(_CURRENT_AGENT_CONTEXT, 'agent_id', 0)

# ===== DATABASE LOGGING FUNCTIONS =====

def log_communication_start(request_id: str, from_agent_id: int, to_agent_id: int, 
                          message: str, context: Optional[Dict[str, Any]] = None) -> int:
    """Log the start of an agent communication"""
    try:
        print(f"Logging communication start: {request_id}, {from_agent_id}, {to_agent_id}, {message}, {context}")
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Insert communication record
        cursor.execute("""
            INSERT INTO [dbo].[AgentCommunications] 
            (request_id, from_agent_id, to_agent_id, message, context, status, created_date)
            VALUES (?, ?, ?, ?, ?, 'pending', getutcdate())
        """, (
            request_id,
            from_agent_id,
            to_agent_id,
            message,
            json.dumps(context) if context else None
        ))

        cursor.execute("SELECT @@IDENTITY")
        
        comm_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        
        return comm_id
        
    except Exception as e:
        logging.error(f"Error logging communication start: {str(e)}")
        return -1

def log_communication_complete(comm_id: int, status: str, response: Optional[Dict[str, Any]] = None,
                             error_message: Optional[str] = None, execution_time_ms: Optional[int] = None):
    """Update communication record with completion status"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Update communication record
        cursor.execute("""
            UPDATE [dbo].[AgentCommunications]
            SET status = ?,
                response = ?,
                error_message = ?,
                execution_time_ms = ?,
                completed_date = getutcdate()
            WHERE id = ?
        """, (
            status,
            json.dumps(response) if response else None,
            error_message,
            execution_time_ms,
            comm_id
        ))
        
        conn.commit()
        cursor.close()
        conn.close()
        
    except Exception as e:
        logging.error(f"Error logging communication completion: {str(e)}")

def log_workflow_execution_start(workflow_id: str, total_steps: int, initiated_by: int) -> str:
    """Log the start of a workflow execution"""
    try:
        execution_id = str(uuid.uuid4())
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Insert workflow execution record
        cursor.execute("""
            INSERT INTO [dbo].[AgentWorkflowExecutions]
            (workflow_id, execution_id, status, current_step, total_steps, 
             started_date, initiated_by, tenant_id)
            VALUES (?, ?, 'running', 0, ?, getutcdate(), ?, ?)
        """, (
            workflow_id,
            execution_id,
            total_steps,
            initiated_by,
            int(os.getenv('TENANT_ID', 0))
        ))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return execution_id
        
    except Exception as e:
        logging.error(f"Error logging workflow start: {str(e)}")
        return None

def log_workflow_step_complete(execution_id: str, step_number: int, step_result: Dict[str, Any]):
    """Update workflow execution with step completion"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get current execution log
        cursor.execute("""
            SELECT execution_log FROM [dbo].[AgentWorkflowExecutions]
            WHERE execution_id = ?
        """, execution_id)
        
        row = cursor.fetchone()
        if row:
            current_log = json.loads(row[0]) if row[0] else {"steps": []}
            current_log["steps"].append({
                "step": step_number,
                "timestamp": datetime.now().isoformat(),
                "result": step_result
            })

            # Update execution record
            cursor.execute("""
                UPDATE [dbo].[AgentWorkflowExecutions]
                SET current_step = ?,
                    execution_log = ?
                WHERE execution_id = ?
            """, (
                step_number,
                json.dumps(current_log),
                execution_id
            ))
            
            conn.commit()
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        logging.error(f"Error logging workflow step: {str(e)}")

def log_workflow_complete(execution_id: str, status: str, final_results: List[Dict[str, Any]]):
    """Mark workflow execution as complete"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Update execution record
        cursor.execute("""
            UPDATE [dbo].[AgentWorkflowExecutions]
            SET status = ?,
                completed_date = getutcdate(),
                execution_log = ?
            WHERE execution_id = ?
        """, (
            status,
            json.dumps({"steps": final_results, "final_status": status}),
            execution_id
        ))
        
        # Update workflow last executed date and count
        cursor.execute("""
            UPDATE w
            SET w.last_executed_date = getutcdate(),
                w.execution_count = w.execution_count + 1
            FROM [dbo].[AgentWorkflows] w
            JOIN [dbo].[AgentWorkflowExecutions] e ON e.workflow_id = w.workflow_id
            WHERE e.execution_id = ?
        """, execution_id)
        
        conn.commit()
        cursor.close()
        conn.close()
        
    except Exception as e:
        logging.error(f"Error logging workflow completion: {str(e)}")

# ===== AGENT COMMUNICATION TOOLS =====

@tool
def communicate_with_agent(
    target_agent_id: int, 
    message: str, 
    context: Optional[Dict[str, Any]] = None,
    timeout: int = 300
) -> str:
    """
    Send a message to another agent and receive their response.
    
    Parameters:
    -----------
    target_agent_id : int
        The ID of the agent to communicate with
    message : str
        The message or task to send to the target agent
    context : Optional[Dict[str, Any]]
        Additional context to pass to the target agent
    timeout : int
        Maximum time in seconds to wait for response (default: 300)
        
    Returns:
    --------
    str
        JSON response containing the target agent's reply or error message
    """
    try:
        request_id = str(uuid.uuid4())
        from_agent_id = get_current_agent_id()  # Helper function to get current agent ID
        start_time = datetime.now()

        # Log the communication attempt to database
        comm_id = log_communication_start(
            request_id=request_id,
            from_agent_id=from_agent_id,
            to_agent_id=target_agent_id,
            message=message,
            context=context
        )

        # Check if target agent exists and is active
        if target_agent_id not in AGENT_REGISTRY:
            error_msg = f"Agent {target_agent_id} is not currently active"
            log_communication_complete(
                comm_id=comm_id,
                status='error',
                error_message=error_msg,
                execution_time_ms=int((datetime.now() - start_time).total_seconds() * 1000)
            )
            return json.dumps({
                "status": "error",
                "message": error_msg,
                "suggestion": "Use get_active_agents() to see available agents"
            })
        
        target_agent = AGENT_REGISTRY[target_agent_id]
        
        # Check if target agent is enabled
        if not target_agent.get('enabled', True):
            error_msg = f"Agent {target_agent_id} is currently disabled"
            log_communication_complete(
                comm_id=comm_id,
                status='error',
                error_message=error_msg,
                execution_time_ms=int((datetime.now() - start_time).total_seconds() * 1000)
            )
            return json.dumps({
                "status": "error",
                "message": error_msg
            })

        # Create communication request
        
        request = {
            "request_id": request_id,
            "from_agent": "current_agent",  # Will be replaced with actual agent ID
            "to_agent": target_agent_id,
            "message": message,
            "context": context or {},
            "timestamp": datetime.now().isoformat()
        }
        
        # Log the communication attempt
        logging.info(f"Agent communication request: {request}")
        
        # Execute agent in separate thread with timeout
        future = AGENT_EXECUTOR.submit(
            _execute_agent_task,
            target_agent,
            message,
            context
        )
        
        try:
            response = future.result(timeout=timeout)
            execution_time_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            
            log_communication_complete(
                comm_id=comm_id,
                status='success',
                response=response,
                execution_time_ms=execution_time_ms
            )
            
            return json.dumps({
                "status": "success",
                "request_id": request_id,
                "from_agent": target_agent_id,
                "response": response,
                "execution_time": f"{(datetime.now() - datetime.fromisoformat(request['timestamp'])).total_seconds():.2f}s"
            })
            
        except TimeoutError:
            error_msg = f"Agent {target_agent_id} took too long to respond (timeout: {timeout}s)"
            log_communication_complete(
                comm_id=comm_id,
                status='timeout',
                error_message=error_msg,
                execution_time_ms=timeout * 1000
            )
            return json.dumps({
                "status": "error",
                "request_id": request_id,
                "message": error_msg
            })
            
    except Exception as e:
        error_msg = f"Failed to communicate with agent: {str(e)}"
        logging.error(f"Error in agent communication: {str(e)}")
        
        # Log the error
        if 'comm_id' in locals():
            log_communication_complete(
                comm_id=comm_id,
                status='error',
                error_message=error_msg,
                execution_time_ms=int((datetime.now() - start_time).total_seconds() * 1000)
            )
        return json.dumps({
            "status": "error",
            "message": error_msg
        })

def communicate_with_agent_directly(
    target_agent_id: int, 
    message: str, 
    context: Optional[Dict[str, Any]] = None,
    timeout: int = 300
) -> str:
    """
    Send a message to another agent and receive their response.
    
    Parameters:
    -----------
    target_agent_id : int
        The ID of the agent to communicate with
    message : str
        The message or task to send to the target agent
    context : Optional[Dict[str, Any]]
        Additional context to pass to the target agent
    timeout : int
        Maximum time in seconds to wait for response (default: 300)
        
    Returns:
    --------
    str
        JSON response containing the target agent's reply or error message
    """
    try:
        request_id = str(uuid.uuid4())
        from_agent_id = get_current_agent_id()  # Helper function to get current agent ID
        start_time = datetime.now()

        # Log the communication attempt to database
        comm_id = log_communication_start(
            request_id=request_id,
            from_agent_id=from_agent_id,
            to_agent_id=target_agent_id,
            message=message,
            context=context
        )

        # Check if target agent exists and is active
        if target_agent_id not in AGENT_REGISTRY:
            error_msg = f"Agent {target_agent_id} is not currently active"
            log_communication_complete(
                comm_id=comm_id,
                status='error',
                error_message=error_msg,
                execution_time_ms=int((datetime.now() - start_time).total_seconds() * 1000)
            )
            return json.dumps({
                "status": "error",
                "message": error_msg,
                "suggestion": "Use get_active_agents() to see available agents"
            })
        
        target_agent = AGENT_REGISTRY[target_agent_id]
        
        # Check if target agent is enabled
        if not target_agent.get('enabled', True):
            error_msg = f"Agent {target_agent_id} is currently disabled"
            log_communication_complete(
                comm_id=comm_id,
                status='error',
                error_message=error_msg,
                execution_time_ms=int((datetime.now() - start_time).total_seconds() * 1000)
            )
            return json.dumps({
                "status": "error",
                "message": error_msg
            })

        # Create communication request
        
        request = {
            "request_id": request_id,
            "from_agent": "current_agent",  # Will be replaced with actual agent ID
            "to_agent": target_agent_id,
            "message": message,
            "context": context or {},
            "timestamp": datetime.now().isoformat()
        }
        
        # Log the communication attempt
        logging.info(f"Agent communication request: {request}")
        
        # Execute agent in separate thread with timeout
        future = AGENT_EXECUTOR.submit(
            _execute_agent_task,
            target_agent,
            message,
            context
        )
        
        try:
            response = future.result(timeout=timeout)
            execution_time_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            
            log_communication_complete(
                comm_id=comm_id,
                status='success',
                response=response,
                execution_time_ms=execution_time_ms
            )
            
            return json.dumps({
                "status": "success",
                "request_id": request_id,
                "from_agent": target_agent_id,
                "response": response,
                "execution_time": f"{(datetime.now() - datetime.fromisoformat(request['timestamp'])).total_seconds():.2f}s"
            })
            
        except TimeoutError:
            error_msg = f"Agent {target_agent_id} took too long to respond (timeout: {timeout}s)"
            log_communication_complete(
                comm_id=comm_id,
                status='timeout',
                error_message=error_msg,
                execution_time_ms=timeout * 1000
            )
            return json.dumps({
                "status": "error",
                "request_id": request_id,
                "message": error_msg
            })
            
    except Exception as e:
        error_msg = f"Failed to communicate with agent: {str(e)}"
        logging.error(f"Error in agent communication: {str(e)}")
        
        # Log the error
        if 'comm_id' in locals():
            log_communication_complete(
                comm_id=comm_id,
                status='error',
                error_message=error_msg,
                execution_time_ms=int((datetime.now() - start_time).total_seconds() * 1000)
            )
        return json.dumps({
            "status": "error",
            "message": error_msg
        })

@tool
def broadcast_to_agents(
    agent_ids: list[int], 
    message: str, 
    context: Optional[Dict[str, Any]] = None,
    wait_for_responses: bool = True,
    timeout: int = 60
) -> str:
    """
    Broadcast a message to multiple agents simultaneously.
    
    Parameters:
    -----------
    agent_ids : list[int]
        List of agent IDs to send the message to
    message : str
        The message to broadcast
    context : Optional[Dict[str, Any]]
        Additional context to pass to agents
    wait_for_responses : bool
        Whether to wait for responses from all agents
    timeout : int
        Maximum time to wait for each agent's response
        
    Returns:
    --------
    str
        JSON response containing results from all agents
    """
    results = []
    
    for agent_id in agent_ids:
        if wait_for_responses:
            response = communicate_with_agent_directly(agent_id, message, context, timeout)
            results.append(json.loads(response))
        else:
            # Fire and forget mode
            AGENT_EXECUTOR.submit(
                _execute_agent_task,
                AGENT_REGISTRY.get(agent_id),
                message,
                context
            )
            results.append({
                "agent_id": agent_id,
                "status": "dispatched"
            })
    
    return json.dumps({
        "broadcast_id": str(uuid.uuid4()),
        "total_agents": len(agent_ids),
        "results": results
    })

@tool
def delegate_task_to_best_agent(
    task: str,
    required_capabilities: Optional[list[str]] = None,
    context: Optional[Dict[str, Any]] = None
) -> str:
    """
    Intelligently delegate a task to the most suitable agent based on capabilities.
    
    Parameters:
    -----------
    task : str
        The task to delegate
    required_capabilities : Optional[list[str]]
        List of required tools/capabilities for the task
    context : Optional[Dict[str, Any]]
        Additional context for task execution
        
    Returns:
    --------
    str
        JSON response with the selected agent's response
    """
    try:
        # Find the best agent for the task
        best_agent_id = _find_best_agent_for_task(task, required_capabilities)
        
        if not best_agent_id:
            return json.dumps({
                "status": "error",
                "message": "No suitable agent found for this task",
                "suggestion": "Check required_capabilities or create a new agent"
            })
        
        # Delegate to the best agent
        return communicate_with_agent_directly(best_agent_id, task, context)
        
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": f"Failed to delegate task: {str(e)}"
        })

@tool
def get_active_agents() -> str:
    """
    Get a list of all currently active agents with their capabilities.
    
    Returns:
    --------
    str
        JSON list of active agents with their descriptions and tools
    """
    active_agents = []
    
    for agent_id, agent_info in AGENT_REGISTRY.items():
        active_agents.append({
            "agent_id": agent_id,
            "description": agent_info.get('description', 'No description'),
            "objective": agent_info.get('objective', 'No objective'),
            "enabled": agent_info.get('enabled', True),
            "tools": agent_info.get('tools', []),
            "specialization": _determine_agent_specialization(agent_info)
        })
    
    return json.dumps({
        "total_agents": len(active_agents),
        "agents": active_agents
    })

@tool
def create_agent_workflow(
    workflow_steps: list[Dict[str, Any]],
    initial_context: Optional[Dict[str, Any]] = None
) -> str:
    """
    Create a multi-agent workflow where agents pass results to each other.
    
    Parameters:
    -----------
    workflow_steps : list[Dict[str, Any]]
        List of workflow steps, each containing:
        - agent_id: ID of the agent to execute
        - task: Task for the agent
        - pass_result_to_next: Whether to pass results to next agent
    initial_context : Optional[Dict[str, Any]]
        Initial context for the workflow
        
    Returns:
    --------
    str
        JSON response with workflow execution results
    """
    workflow_id = str(uuid.uuid4())
    results = []
    current_context = initial_context or {}
    
    try:
        for i, step in enumerate(workflow_steps):
            agent_id = step.get('agent_id')
            task = step.get('task')
            pass_result = step.get('pass_result_to_next', True)
            
            # Include previous results in context if specified
            if i > 0 and pass_result:
                current_context['previous_result'] = results[-1]
            
            # Execute step
            response = communicate_with_agent_directly(
                agent_id, 
                task, 
                current_context,
                timeout=step.get('timeout', 300)
            )
            
            result = json.loads(response)
            results.append({
                "step": i + 1,
                "agent_id": agent_id,
                "task": task,
                "result": result
            })
            
            # Stop workflow on error
            if result.get('status') == 'error':
                break
        
        return json.dumps({
            "workflow_id": workflow_id,
            "total_steps": len(workflow_steps),
            "completed_steps": len(results),
            "results": results
        })
        
    except Exception as e:
        return json.dumps({
            "workflow_id": workflow_id,
            "status": "error",
            "message": f"Workflow failed: {str(e)}",
            "completed_steps": len(results)
        })

# Helper functions

def _execute_agent_task(agent_info, message, context):
    """Execute an agent task using the actual GeneralAgent instance"""
    try:
        agent_executor = agent_info.get('executor')
        
        if not agent_executor:
            # Fallback: create a new agent instance if not cached
            from GeneralAgent import GeneralAgent
            agent_executor = GeneralAgent(agent_id=agent_info['id'])
            agent_info['executor'] = agent_executor
        
        # Use the agent's handle_agent_request method
        result = agent_executor.handle_agent_request(message, context)
        return result
        
    except Exception as e:
        logging.error(f"Error executing agent task: {str(e)}")
        return {
            "status": "error",
            "error": str(e),
            "agent_id": agent_info.get('id')
        }

def _find_best_agent_for_task(task: str, required_capabilities):
    """Find the best agent for a given task based on capabilities using AI"""
    import json
    from AppUtils import azureQuickPrompt, azureMiniQuickPrompt
    import config as cfg
    import system_prompts as sp
    
    # Check if we should use mini model
    use_mini = getattr(cfg, 'USE_MINI_MODELS_WHEN_POSSIBLE', False)
    
    # Prepare agent information for the prompt
    agents_info = []
    for agent_id, agent_info in AGENT_REGISTRY.items():
        if agent_info.get('enabled', True):
            agent_summary = {
                'id': agent_id,
                'description': agent_info.get('description', 'No description'),
                'objective': agent_info.get('objective', 'No objective'),
                'tools': agent_info.get('tools', [])[:10],  # Limit tools list for prompt
                'specialization': _determine_agent_specialization(agent_info)
            }
            agents_info.append(agent_summary)
    
    # Format agents info as a string
    agents_str = ""
    for agent in agents_info:
        agents_str += f"\nAgent ID: {agent['id']}\n"
        agents_str += f"Description: {agent['description']}\n"
        agents_str += f"Objective: {agent['objective']}\n"
        agents_str += f"Specialization: {agent['specialization']}\n"
        agents_str += f"Key Tools: {', '.join(agent['tools'][:5])}\n"
        agents_str += "-" * 40
    
    # Format required capabilities
    capabilities_str = ', '.join(required_capabilities) if required_capabilities else 'None specified'
    
    # Prepare the prompt
    prompt = sp.AGENT_SELECTION_PROMPT.format(
        task=task,
        required_capabilities=capabilities_str,
        agents_info=agents_str
    )
    
    try:
        # Call the appropriate Azure function
        if use_mini:
            response = azureMiniQuickPrompt(
                prompt=prompt,
                system=sp.AGENT_SELECTION_SYSTEM,
                temp=0.0  # Deterministic for consistency
            )
        else:
            response = azureQuickPrompt(
                prompt=prompt,
                system=sp.AGENT_SELECTION_SYSTEM,
                use_alternate_api=False,
                temp=0.0
            )
        
        # Parse the JSON response
        try:
            result = json.loads(response)
            selected_id = result.get('selected_agent_id')
            confidence = result.get('confidence', 0.0)
            reasoning = result.get('reasoning', '')
            alternative_id = result.get('alternative_agent_id')
            
            logging.info(f"AI selected agent {selected_id} with confidence {confidence}: {reasoning}")
            print(f"AI selected agent {selected_id} with confidence {confidence}: {reasoning}")
            
            # If confidence is low and there's an alternative, consider it
            if confidence < 0.5 and alternative_id:
                logging.info(f"Low confidence, considering alternative agent {alternative_id}")
                selected_id = alternative_id
            
            return selected_id
            
        except json.JSONDecodeError:
            logging.error(f"Failed to parse AI response as JSON: {response}")
            # Fall back to the original logic
            
    except Exception as e:
        logging.error(f"Error calling Azure AI for agent selection: {str(e)}")
    
    # Fallback to original logic if AI fails
    logging.info("Falling back to rule-based agent selection")
    best_score = 0
    best_agent_id = None
    
    for agent_id, agent_info in AGENT_REGISTRY.items():
        if not agent_info.get('enabled', True):
            continue
            
        score = 0
        agent_tools = agent_info.get('tools', [])
        
        # Check required capabilities
        if required_capabilities:
            matching_capabilities = sum(
                1 for cap in required_capabilities 
                if cap in agent_tools
            )
            score += matching_capabilities * 10
        
        # Check objective/description relevance (simple keyword matching)
        objective = agent_info.get('objective', '').lower()
        description = agent_info.get('description', '').lower()
        task_lower = task.lower()
        
        for word in task_lower.split():
            if word in objective:
                score += 2
            if word in description:
                score += 1
        
        if score > best_score:
            best_score = score
            best_agent_id = agent_id
    
    return best_agent_id

def _determine_agent_specialization(agent_info):
    """Determine agent's specialization based on tools and objective"""
    tools = agent_info.get('tools', [])
    objective = agent_info.get('objective', '').lower()
    
    # Determine specialization based on tools
    if any('data' in tool or 'query' in tool for tool in tools):
        return "Data Analysis"
    elif any('document' in tool for tool in tools):
        return "Document Processing"
    elif any('email' in tool or 'phone' in tool or 'text' in tool for tool in tools):
        return "Communication"
    elif any('workflow' in tool for tool in tools):
        return "Workflow Management"
    elif 'knowledge' in objective:
        return "Knowledge Management"
    else:
        return "General Purpose"

def register_agent(agent_id, agent_info):
    """Register an agent in the global registry"""
    AGENT_REGISTRY[agent_id] = agent_info

def unregister_agent(agent_id):
    """Remove an agent from the global registry"""
    if agent_id in AGENT_REGISTRY:
        del AGENT_REGISTRY[agent_id]
