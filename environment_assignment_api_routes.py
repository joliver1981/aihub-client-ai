# environment_assignment_api_routes.py
# Separate blueprint file for agent-environment assignment routes

from flask import Blueprint, jsonify, request, render_template
from flask_login import login_required, current_user
from role_decorators import api_key_or_session_required
import pyodbc
import logging
import os
import json
import tempfile
import subprocess
from CommonUtils import get_db_connection


# Create the blueprint
assignments_bp = Blueprint('assignments', __name__)


@assignments_bp.route('/assignments/manage')
@api_key_or_session_required(min_role=2)
def manage_assignments():
    """Render the assignment management page"""
    return render_template('agent_environment_assignments.html')

@assignments_bp.route('/api/assignments/agents/list', methods=['GET'])
@api_key_or_session_required(min_role=2)
def list_agents_api():
    """Get list of all agents for assignment management"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        cursor.execute("""
            SELECT 
                id,
                description,
                objective,
                enabled
            FROM Agents
            WHERE enabled = 1
            ORDER BY description
        """)
        
        agents = []
        for row in cursor.fetchall():
            agents.append({
                'id': row.id,
                'description': row.description,
                'objective': row.objective,
                'enabled': row.enabled
            })
        
        conn.close()
        
        return jsonify({
            'status': 'success',
            'agents': agents
        })
        
    except Exception as e:
        logging.error(f"Error listing agents: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@assignments_bp.route('/api/assignments/list', methods=['GET'])
@api_key_or_session_required(min_role=2)
def list_assignments():
    """Get all agent-environment assignments"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        cursor.execute("""
            SELECT 
                a.agent_id,
                a.environment_id,
                a.assigned_date,
                a.assigned_by,
                a.is_active,
                ag.description as agent_name,
                e.name as environment_name,
                u.user_name as assigned_by_name
            FROM AgentEnvironmentAssignments a
            INNER JOIN Agents ag ON a.agent_id = ag.id
            INNER JOIN AgentEnvironments e ON a.environment_id = e.environment_id
            LEFT JOIN [User] u ON a.assigned_by = u.id
            WHERE a.is_active = 1 AND e.is_deleted = 0
            ORDER BY a.assigned_date DESC
        """)
        
        assignments = []
        for row in cursor.fetchall():
            assignments.append({
                'agent_id': row.agent_id,
                'environment_id': row.environment_id,
                'assigned_date': row.assigned_date.isoformat() if row.assigned_date else None,
                'assigned_by': row.assigned_by,
                'assigned_by_name': row.assigned_by_name,
                'is_active': row.is_active,
                'agent_name': row.agent_name,
                'environment_name': row.environment_name
            })
        
        conn.close()
        
        return jsonify({
            'status': 'success',
            'assignments': assignments
        })
        
    except Exception as e:
        logging.error(f"Error listing assignments: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@assignments_bp.route('/api/agents/<int:agent_id>/environment', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_agent_environment(agent_id):
    """Get the environment assigned to a specific agent"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        cursor.execute("""
            SELECT 
                a.environment_id,
                e.name,
                e.description,
                e.status
            FROM AgentEnvironmentAssignments a
            INNER JOIN AgentEnvironments e ON a.environment_id = e.environment_id
            WHERE a.agent_id = ? AND a.is_active = 1 AND e.is_deleted = 0
        """, agent_id)
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return jsonify({
                'status': 'success',
                'environment': {
                    'environment_id': row.environment_id,
                    'name': row.name,
                    'description': row.description,
                    'status': row.status
                }
            })
        else:
            return jsonify({
                'status': 'success',
                'environment': None
            })
            
    except Exception as e:
        logging.error(f"Error getting agent environment: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@assignments_bp.route('/api/agents/<int:agent_id>/environment', methods=['POST'])
@api_key_or_session_required(min_role=2)
def assign_agent_environment(agent_id):
    """Assign or update an environment for an agent"""
    try:
        data = request.get_json()
        environment_id = data.get('environment_id')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        if not environment_id:
            # Remove environment assignment
            cursor.execute("""
                UPDATE AgentEnvironmentAssignments 
                SET is_active = 0 
                WHERE agent_id = ? AND is_active = 1
            """, agent_id)
            
            conn.commit()
            conn.close()
            
            return jsonify({
                'status': 'success',
                'message': 'Environment assignment removed'
            })
        else:
            # Check if environment exists
            cursor.execute("""
                SELECT environment_id 
                FROM AgentEnvironments 
                WHERE environment_id = ? AND is_deleted = 0
            """, environment_id)
            
            if not cursor.fetchone():
                conn.close()
                return jsonify({
                    'status': 'error',
                    'message': 'Environment not found'
                }), 404
            
            # Deactivate existing assignment
            cursor.execute("""
                UPDATE AgentEnvironmentAssignments 
                SET is_active = 0 
                WHERE agent_id = ? AND is_active = 1
            """, agent_id)
            
            # Create new assignment
            cursor.execute("""
                INSERT INTO AgentEnvironmentAssignments 
                (agent_id, environment_id, assigned_by)
                VALUES (?, ?, ?)
            """, agent_id, environment_id, current_user.id)
            
            conn.commit()
            conn.close()
            
            return jsonify({
                'status': 'success',
                'message': 'Environment assigned successfully'
            })
            
    except Exception as e:
        logging.error(f"Error assigning environment: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@assignments_bp.route('/api/assignments/bulk', methods=['POST'])
@api_key_or_session_required(min_role=2)
def bulk_assign():
    """Bulk assign environments to multiple agents"""
    try:
        data = request.get_json()
        agent_ids = data.get('agent_ids', [])
        environment_id = data.get('environment_id')
        
        if not agent_ids or not environment_id:
            return jsonify({
                'status': 'error',
                'message': 'Agent IDs and environment ID required'
            }), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        success_count = 0
        failed_agents = []
        
        for agent_id in agent_ids:
            try:
                # Deactivate existing assignment
                cursor.execute("""
                    UPDATE AgentEnvironmentAssignments 
                    SET is_active = 0 
                    WHERE agent_id = ? AND is_active = 1
                """, agent_id)
                
                # Create new assignment
                cursor.execute("""
                    INSERT INTO AgentEnvironmentAssignments 
                    (agent_id, environment_id, assigned_by)
                    VALUES (?, ?, ?)
                """, agent_id, environment_id, current_user.id)
                
                success_count += 1
                
            except Exception as e:
                failed_agents.append({'agent_id': agent_id, 'error': str(e)})
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'message': f'Successfully assigned {success_count} agents',
            'failed': failed_agents
        })
        
    except Exception as e:
        logging.error(f"Error in bulk assignment: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@assignments_bp.route('/api/assignments/summary', methods=['GET'])
@api_key_or_session_required(min_role=2)
def assignment_summary():
    """Get summary statistics for assignments"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        stats = {}
        
        # Total agents
        cursor.execute("SELECT COUNT(*) as count FROM Agents WHERE enabled = 1")
        stats['total_agents'] = cursor.fetchone().count
        
        # Total environments
        cursor.execute("SELECT COUNT(*) as count FROM AgentEnvironments WHERE is_deleted = 0")
        stats['total_environments'] = cursor.fetchone().count
        
        # Active assignments
        cursor.execute("""
            SELECT COUNT(*) as count 
            FROM AgentEnvironmentAssignments a
            INNER JOIN AgentEnvironments e ON a.environment_id = e.environment_id
            WHERE a.is_active = 1 AND e.is_deleted = 0
        """)
        stats['active_assignments'] = cursor.fetchone().count
        
        # Unassigned agents
        cursor.execute("""
            SELECT COUNT(*) as count 
            FROM Agents 
            WHERE enabled = 1 
            AND id NOT IN (
                SELECT agent_id 
                FROM AgentEnvironmentAssignments 
                WHERE is_active = 1
            )
        """)
        stats['unassigned_agents'] = cursor.fetchone().count
        
        # Most used environments
        cursor.execute("""
            SELECT TOP 5
                e.name,
                e.environment_id,
                COUNT(a.agent_id) as agent_count
            FROM AgentEnvironments e
            LEFT JOIN AgentEnvironmentAssignments a 
                ON e.environment_id = a.environment_id AND a.is_active = 1
            WHERE e.is_deleted = 0
            GROUP BY e.name, e.environment_id
            ORDER BY agent_count DESC
        """)
        
        top_environments = []
        for row in cursor.fetchall():
            top_environments.append({
                'name': row.name,
                'environment_id': row.environment_id,
                'agent_count': row.agent_count
            })
        stats['top_environments'] = top_environments
        
        conn.close()
        
        return jsonify({
            'status': 'success',
            'summary': stats
        })
        
    except Exception as e:
        logging.error(f"Error getting assignment summary: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@assignments_bp.route('/chat/with-environment', methods=['POST'])
@api_key_or_session_required(min_role=2)
def chat_with_environment():
    """Enhanced chat endpoint that uses custom environments"""
    try:
        data = request.get_json()
        agent_id = data.get('agent_id')
        prompt = data.get('prompt')
        chat_history = data.get('hist', [])
        environment_id = data.get('environment_id')
        use_environment = data.get('use_environment', False)
        
        if not agent_id or not prompt:
            return jsonify({
                'status': 'error',
                'message': 'Agent ID and prompt are required'
            }), 400
        
        # Check if we should use a custom environment
        if use_environment and environment_id:
            response = execute_agent_in_environment(
                agent_id, 
                environment_id, 
                prompt, 
                chat_history
            )
            
            return jsonify({
                'status': 'success',
                'response': response,
                'used_custom_environment': True
            })
        else:
            # Check if agent has an assigned environment
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            cursor.execute("""
                SELECT environment_id 
                FROM AgentEnvironmentAssignments 
                WHERE agent_id = ? AND is_active = 1
            """, agent_id)
            
            row = cursor.fetchone()
            conn.close()
            
            if row and row.environment_id:
                response = execute_agent_in_environment(
                    agent_id, 
                    row.environment_id, 
                    prompt, 
                    chat_history
                )
                
                return jsonify({
                    'status': 'success',
                    'response': response,
                    'used_custom_environment': True
                })
            else:
                # Fallback to standard chat processing
                response = f"Standard response to: {prompt}"  # Replace with your actual logic
                
                return jsonify({
                    'status': 'success',
                    'response': response,
                    'used_custom_environment': False
                })
        
    except Exception as e:
        logging.error(f"Error in chat with environment: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

def execute_agent_in_environment(agent_id, environment_id, prompt, chat_history):
    """Execute GeneralAgent in a custom Python environment"""
    try:
        from agent_environments import AgentEnvironmentManager
        import subprocess
        import tempfile
        import json
        import os
        
        # Get environment manager
        tenant_id = os.getenv('API_KEY')
        manager = AgentEnvironmentManager(tenant_id)
        
        # Get Python executable for environment
        python_path = manager.get_python_executable(environment_id)
        if not python_path:
            raise Exception("Environment not found or not initialized")
        
        # Get environment base path
        env_base_path = os.path.join(manager.base_path, environment_id)
        
        # Get agent details from database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        cursor.execute("""
            SELECT name, description, objective, system_message, model, temperature
            FROM Agents
            WHERE id = ?
        """, agent_id)
        
        agent = cursor.fetchone()
        
        # Get agent tools
        cursor.execute("""
            SELECT t.name, t.description
            FROM AgentTools at
            JOIN Tools t ON at.tool_id = t.id
            WHERE at.agent_id = ?
        """, agent_id)
        
        tools = [{'name': row.name, 'description': row.description} for row in cursor.fetchall()]
        
        conn.close()
        
        if not agent:
            raise Exception(f"Agent {agent_id} not found")
        
        # Prepare agent configuration
        # Model fallback chain: agent.model (from DB) -> configured Azure default -> 'gpt-4' sentinel
        try:
            import config as cfg
            _default_model = getattr(cfg, 'AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-4')
        except Exception:
            _default_model = 'gpt-4'
        agent_config = {
            'id': agent_id,
            'name': agent.name or f'Agent_{agent_id}',
            'description': agent.description or '',
            'objective': agent.objective or '',
            'system_message': agent.system_message or 'You are a helpful assistant.',
            'model': agent.model or _default_model,
            'temperature': agent.temperature or 0.7,
            'tools': tools
        }
        
        # Create execution script
        script = f"""
import sys
import json
import logging
import os
import traceback
from datetime import datetime

# Add the environment's site-packages to path
import site
site.addsitedir('{env_base_path}/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages')

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    # Import GeneralAgent
    logger.info("Importing GeneralAgent...")
    from GeneralAgent import GeneralAgent
    
    # Agent configuration
    agent_id = {agent_id}
    agent_config = {json.dumps(agent_config)}
    chat_history = {json.dumps(chat_history)}
    user_prompt = {json.dumps(prompt)}
    
    # Create and configure agent
    logger.info(f"Creating agent {agent_id}...")
    agent = GeneralAgent(agent_id)
    
    # Set properties
    if agent_config.get('name'):
        agent.AGENT_NAME = agent_config['name']
    if agent_config.get('description'):
        agent.AGENT_DESCRIPTION = agent_config['description']
    if agent_config.get('objective'):
        agent.AGENT_OBJECTIVE = agent_config['objective']
    
    # Initialize chat history
    logger.info(f"Initializing with {len(chat_history)} chat messages...")
    agent.initialize_chat_history(chat_history)
    
    # Execute
    logger.info("Executing agent...")
    response = agent.run(user_prompt)
    
    # Get updated history
    updated_history = []
    if hasattr(agent, 'get_chat_history'):
        updated_history = agent.get_chat_history()
    
    # Output result
    result = {{
        'response': response,
        'chat_history': updated_history,
        'success': True
    }}
    
    print(json.dumps(result))
    
except ImportError as e:
    logger.error(f"Import error: {e}")
    print(json.dumps({{
        'error': f'Failed to import required modules: {str(e)}',
        'success': False,
        'traceback': traceback.format_exc()
    }}))
    
except Exception as e:
    logger.error(f"Execution error: {e}")
    print(json.dumps({{
        'error': str(e),
        'success': False,
        'traceback': traceback.format_exc()
    }}))
"""
        
        # Write script to temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write(script)
            script_path = f.name
        
        try:
            # Set up environment variables
            env = os.environ.copy()
            env['PYTHONPATH'] = f"{env_base_path}:{env.get('PYTHONPATH', '')}"
            env['AGENT_ENVIRONMENT'] = environment_id
            
            # Execute in environment
            logging.info(f"Executing agent {agent_id} in environment {environment_id}")
            
            result = subprocess.run(
                [python_path, script_path],
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
                env=env,
                cwd=env_base_path
            )
            
            if result.returncode != 0 and result.stdout:
                # Try to parse error from stdout
                try:
                    output = json.loads(result.stdout)
                    if not output.get('success'):
                        logging.error(f"Agent execution failed: {output.get('error')}")
                        return f"Error: {output.get('error', 'Unknown error')}"
                except:
                    pass
            
            if result.returncode != 0:
                logging.error(f"Script execution failed: {result.stderr}")
                raise Exception(f"Execution failed: {result.stderr}")
            
            # Parse response
            response_data = json.loads(result.stdout)
            
            if response_data.get('success'):
                return response_data.get('response', 'No response generated')
            else:
                return f"Error: {response_data.get('error', 'Unknown error')}"
                
        finally:
            # Clean up temp file
            try:
                os.unlink(script_path)
            except:
                pass
        
    except subprocess.TimeoutExpired:
        logging.error(f"Agent {agent_id} execution timed out")
        return "Error: Agent execution timed out (5 minutes)"
        
    except json.JSONDecodeError as e:
        logging.error(f"Failed to parse agent output: {e}")
        if 'result' in locals() and result.stdout:
            return f"Error parsing output: {result.stdout[:500]}"
        return "Error: Failed to parse agent output"
        
    except Exception as e:
        logging.error(f"Error executing agent in environment: {e}")
        return f"Error: {str(e)}"

def execute_agent_in_environment_legacy(agent_id, environment_id, prompt, chat_history):
    """Execute agent code in a custom Python environment"""
    try:
        from agent_environments import AgentEnvironmentManager
        
        # Get environment manager
        tenant_id = os.getenv('API_KEY')
        manager = AgentEnvironmentManager(tenant_id)
        
        # Get Python executable for environment
        python_path = manager.get_python_executable(environment_id)
        if not python_path:
            raise Exception("Environment not found or not initialized")
        
        # Get agent details
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        cursor.execute("""
            SELECT description, objective, system_message
            FROM Agents
            WHERE id = ?
        """, agent_id)
        
        agent = cursor.fetchone()
        conn.close()
        
        if not agent:
            raise Exception("Agent not found")
        
        # Create execution script
        script = f'''
import sys
import json
import logging
import traceback
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    try:
        # Agent configuration passed from parent
        agent_id = {agent_id}
        agent_config = {agent_config}
        chat_history = {chat_history}
        user_prompt = """{user_prompt}"""
        
        logger.info(f"Starting agent {agent_id} execution in custom environment")
        
        # Import GeneralAgent from the environment
        from GeneralAgent import GeneralAgent
        
        # Create agent instance
        agent = GeneralAgent(agent_id)
        
        # Set agent properties if available
        if hasattr(agent, 'AGENT_NAME'):
            agent.AGENT_NAME = agent_config.get('name', f'Agent_{agent_id}')
        
        if hasattr(agent, 'AGENT_DESCRIPTION'):
            agent.AGENT_DESCRIPTION = agent_config.get('description', '')
            
        if hasattr(agent, 'AGENT_OBJECTIVE'):
            agent.AGENT_OBJECTIVE = agent_config.get('objective', '')
        
        # Override system message if provided
        if agent_config.get('system_message') and hasattr(agent, 'system_message'):
            agent.system_message = agent_config['system_message']
        
        # Initialize chat history
        logger.info(f"Initializing chat history with {len(chat_history)} messages")
        agent.initialize_chat_history(chat_history)
        
        # Run the agent
        logger.info(f"Running agent with prompt: {user_prompt[:100]}...")
        response = agent.run(user_prompt)
        
        # Get updated chat history
        updated_chat_history = []
        if hasattr(agent, 'get_chat_history'):
            updated_chat_history = agent.get_chat_history()
        elif hasattr(agent, 'chat_history'):
            # Convert to serializable format
            serializable_history = []
            for msg in agent.chat_history:
                if hasattr(msg, 'content'):
                    serializable_history.append({
                        'role': msg.__class__.__name__.replace('Message', '').lower(),
                        'content': msg.content
                    })
                else:
                    serializable_history.append(str(msg))
            updated_chat_history = serializable_history
        
        # Prepare successful output
        output = {
            'status': 'success',
            'response': response,
            'chat_history': updated_chat_history,
            'execution_info': {
                'agent_id': agent_id,
                'environment': 'custom',
                'timestamp': datetime.now().isoformat(),
                'python_version': sys.version
            }
        }
        
        print(json.dumps(output))
        return 0
        
    except ImportError as e:
        error_output = {
            'status': 'error',
            'error': f'Failed to import GeneralAgent: {str(e)}',
            'type': 'import_error',
            'traceback': traceback.format_exc()
        }
        print(json.dumps(error_output))
        return 1
        
    except Exception as e:
        error_output = {
            'status': 'error', 
            'error': str(e),
            'type': 'execution_error',
            'traceback': traceback.format_exc()
        }
        print(json.dumps(error_output))
        return 1

if __name__ == "__main__":
    sys.exit(main())
'''
        
        # Execute in environment
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(script)
            script_path = f.name
        
        try:
            result = subprocess.run(
                [python_path, script_path],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                raise Exception(f"Script execution failed: {result.stderr}")
            
            response_data = json.loads(result.stdout)
            return response_data['response']
            
        finally:
            os.unlink(script_path)
        
    except Exception as e:
        logging.error(f"Error executing agent in environment: {e}")
        return f"Error: {str(e)}"
