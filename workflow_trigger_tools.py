"""
Workflow Trigger Tools for AI Agents
====================================

Tools that allow agents to trigger visual workflow designer workflows.

Usage:
    1. Add to your agent's tool list in the database:
       - trigger_workflow
       - list_workflows (optional)
       
    2. Import in GeneralAgent.py or wherever tools are loaded:
       from workflow_trigger_tools import trigger_workflow, list_workflows
       
    3. Register in CORE_TOOLS or the tool registry

Example agent conversation:
    User: "Process the invoice I just uploaded"
    Agent: [uses trigger_workflow(workflow_id=42, variables={"document_path": "/uploads/invoice.pdf"})]
    Agent: "I've started the invoice processing workflow. Execution ID: abc-123"
"""

import os
import json
import logging
import requests
from typing import Optional, Dict, Any, List
from langchain.tools import tool

from role_decorators import get_internal_api_key

logger = logging.getLogger(__name__)


def _get_executor_url() -> str:
    """Get the workflow executor service URL for API calls."""
    try:
        from CommonUtils import get_executor_api_base_url
        return get_executor_api_base_url()
    except ImportError:
        return os.getenv('EXECUTOR_API_URL', 'http://localhost:5001')


def _get_auth_headers() -> Dict[str, str]:
    """Get authentication headers for internal API calls."""
    return {
        'Content-Type': 'application/json',
        'X-Internal-API-Key': get_internal_api_key()
    }


@tool
def trigger_workflow(
    workflow_id: int,
    variables: Optional[Dict[str, Any]] = None,
    wait_for_completion: bool = False,
    timeout_seconds: int = 300
) -> str:
    """
    Trigger a workflow execution from the visual workflow designer.
    
    Use this tool when you need to start an automated workflow. This is useful for:
    - Starting document processing pipelines
    - Triggering approval workflows
    - Running data operations on-demand
    - Orchestrating multi-step business processes
    
    Parameters
    ----------
    workflow_id : int
        The ID of the workflow to trigger. Use list_workflows to find available IDs.
    variables : dict, optional
        Initial variables to pass to the workflow. These will be available
        as ${variable_name} in workflow nodes. Example:
        {"customer_id": "C123", "amount": 500.00, "email": "user@example.com"}
    wait_for_completion : bool, default False
        If True, wait for the workflow to complete before returning.
        If False, return immediately with the execution ID.
    timeout_seconds : int, default 300
        Maximum seconds to wait if wait_for_completion is True.
    
    Returns
    -------
    str
        JSON with execution_id, status, and any results.
    
    Examples
    --------
    Start a workflow immediately:
        trigger_workflow(workflow_id=42)
    
    Start with variables:
        trigger_workflow(workflow_id=42, variables={"customer_id": "C123"})
    
    Start and wait for completion:
        trigger_workflow(workflow_id=42, wait_for_completion=True)
    """
    try:
        api_url = f"{_get_executor_url()}/api/workflow/run"
        headers = _get_auth_headers()
        
        payload = {
            'workflow_id': workflow_id,
            'initiator': 'agent_tool',
            'variables': variables or {}
        }
        
        logger.info(f"Triggering workflow {workflow_id} with {len(variables or {})} variables")
        
        response = requests.post(api_url, json=payload, headers=headers, timeout=30)
        
        if response.status_code != 200:
            error_data = response.json() if response.text else {}
            return json.dumps({
                'success': False,
                'error': error_data.get('message', f'HTTP {response.status_code}'),
                'workflow_id': workflow_id
            })
        
        result = response.json()
        execution_id = result.get('execution_id')
        
        if not wait_for_completion:
            return json.dumps({
                'success': True,
                'execution_id': execution_id,
                'workflow_id': workflow_id,
                'status': 'started',
                'message': f'Workflow {workflow_id} started successfully. Track with execution ID: {execution_id}'
            })
        
        # Poll for completion
        import time
        status_url = f"{_get_executor_url()}/api/workflow/executions/{execution_id}/status"
        start_time = time.time()
        last_status = 'Running'
        
        while time.time() - start_time < timeout_seconds:
            try:
                status_response = requests.get(status_url, headers=headers, timeout=10)
                if status_response.status_code == 200:
                    status_data = status_response.json()
                    last_status = status_data.get('status', 'Unknown')
                    
                    if last_status in ['Completed', 'Failed', 'Cancelled']:
                        return json.dumps({
                            'success': last_status == 'Completed',
                            'execution_id': execution_id,
                            'workflow_id': workflow_id,
                            'status': last_status,
                            'result': status_data.get('result'),
                            'error': status_data.get('error') if last_status == 'Failed' else None,
                            'message': f'Workflow {last_status.lower()}'
                        })
            except requests.RequestException as e:
                logger.warning(f"Status check failed: {e}")
            
            time.sleep(2)  # Poll every 2 seconds
        
        return json.dumps({
            'success': False,
            'execution_id': execution_id,
            'workflow_id': workflow_id,
            'status': 'timeout',
            'last_known_status': last_status,
            'message': f'Workflow did not complete within {timeout_seconds} seconds. It may still be running.'
        })
        
    except requests.RequestException as e:
        logger.error(f"Request error triggering workflow {workflow_id}: {str(e)}")
        return json.dumps({
            'success': False,
            'error': f'Connection error: {str(e)}',
            'workflow_id': workflow_id
        })
    except Exception as e:
        logger.error(f"Error triggering workflow {workflow_id}: {str(e)}")
        return json.dumps({
            'success': False,
            'error': str(e),
            'workflow_id': workflow_id
        })


@tool
def list_workflows(
    active_only: bool = True,
    include_details: bool = False
) -> str:
    """
    List available workflows that can be triggered.
    
    Use this to find workflow IDs for the trigger_workflow tool.
    
    Parameters
    ----------
    active_only : bool, default True
        If True, only return active workflows. If False, return all.
    include_details : bool, default False
        If True, include additional details like variable definitions.
    
    Returns
    -------
    str
        JSON list of workflows with id, name, and description.
    
    Examples
    --------
    List all active workflows:
        list_workflows()
    
    List all workflows including inactive:
        list_workflows(active_only=False)
    """
    try:
        # Direct database query is more reliable than API for internal use
        from DataUtils import get_db_connection
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        query = """
            SELECT id, workflow_name, description, is_active, created_at, 
                   updated_at, workflow_data
            FROM Workflows
        """
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY workflow_name"
        
        cursor.execute(query)
        rows = cursor.fetchall()
        
        workflows = []
        for row in rows:
            wf = {
                'id': row[0],
                'name': row[1] or 'Unnamed Workflow',
                'description': row[2] or '',
                'is_active': bool(row[3]) if row[3] is not None else True,
                'created_at': row[4].isoformat() if row[4] else None,
                'updated_at': row[5].isoformat() if row[5] else None
            }
            
            if include_details and row[6]:
                try:
                    workflow_data = json.loads(row[6])
                    # Extract variable definitions for reference
                    variables = workflow_data.get('variables', {})
                    wf['variables'] = {
                        name: {
                            'type': var.get('type', 'string'),
                            'description': var.get('description', '')
                        }
                        for name, var in variables.items()
                    }
                    wf['node_count'] = len(workflow_data.get('nodes', []))
                except json.JSONDecodeError:
                    pass
            
            workflows.append(wf)
        
        cursor.close()
        conn.close()
        
        return json.dumps({
            'success': True,
            'count': len(workflows),
            'workflows': workflows,
            'hint': 'Use trigger_workflow(workflow_id=<id>) to start a workflow'
        })
        
    except Exception as e:
        logger.error(f"Error listing workflows: {str(e)}")
        return json.dumps({
            'success': False,
            'error': str(e),
            'workflows': []
        })


@tool  
def get_workflow_status(execution_id: str) -> str:
    """
    Get the current status of a workflow execution.
    
    Use this to check on a workflow that was previously triggered.
    
    Parameters
    ----------
    execution_id : str
        The execution ID returned from trigger_workflow.
    
    Returns
    -------
    str
        JSON with current status, progress, and any results.
    
    Examples
    --------
    Check workflow status:
        get_workflow_status(execution_id="abc-123-def")
    """
    try:
        api_url = f"{_get_executor_url()}/api/workflow/executions/{execution_id}/status"
        headers = _get_auth_headers()
        
        response = requests.get(api_url, headers=headers, timeout=10)
        
        if response.status_code == 404:
            return json.dumps({
                'success': False,
                'error': f'Execution {execution_id} not found',
                'execution_id': execution_id
            })
        
        if response.status_code != 200:
            return json.dumps({
                'success': False,
                'error': f'HTTP {response.status_code}',
                'execution_id': execution_id
            })
        
        data = response.json()
        
        return json.dumps({
            'success': True,
            'execution_id': execution_id,
            'status': data.get('status', 'Unknown'),
            'workflow_name': data.get('workflow_name'),
            'started_at': data.get('started_at'),
            'completed_at': data.get('completed_at'),
            'current_node': data.get('current_node'),
            'progress': data.get('progress'),
            'result': data.get('result'),
            'error': data.get('error')
        })
        
    except Exception as e:
        logger.error(f"Error getting workflow status: {str(e)}")
        return json.dumps({
            'success': False,
            'error': str(e),
            'execution_id': execution_id
        })


# =============================================================================
# Registration Helper
# =============================================================================

def get_workflow_tools() -> List:
    """
    Get all workflow trigger tools for registration.
    
    Usage in GeneralAgent.py or tool registry:
        from workflow_trigger_tools import get_workflow_tools
        
        CORE_TOOLS.update({
            tool.name: tool for tool in get_workflow_tools()
        })
    """
    return [trigger_workflow, list_workflows, get_workflow_status]


# Tool metadata for UI display
TOOL_METADATA = {
    'trigger_workflow': {
        'display_name': 'Trigger Workflow',
        'category': 'Automation',
        'description': 'Start a visual workflow from the Workflow Tool',
        'icon': 'bi-diagram-3'
    },
    'list_workflows': {
        'display_name': 'List Workflows',
        'category': 'Automation',
        'description': 'List available workflows that can be triggered',
        'icon': 'bi-list-ul'
    },
    'get_workflow_status': {
        'display_name': 'Get Workflow Status',
        'category': 'Automation',
        'description': 'Check the status of a running workflow',
        'icon': 'bi-activity'
    }
}
