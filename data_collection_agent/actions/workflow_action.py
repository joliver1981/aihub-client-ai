"""
Workflow trigger completion action.

Calls the platform's `POST /api/workflow/run` endpoint with the collected data
mapped to workflow variables. Mirrors the trigger pattern from
`scheduler_routes.py` and `workflow_trigger_tools.py`.

Schema config:
    {
      "type": "workflow",
      "label": "Process submission",
      "workflow_id": 42,                           // OR workflow_name (one required)
      "workflow_name": "Process Submission",
      "variable_mapping": {
        "request_type": "{{request_type}}",
        "submitter": "{{submitter_email}}",
        "full_data": "{{__all_data__}}"
      },
      "wait_for_completion": false,                // future: poll for completion
      "continue_on_error": false
    }
"""

import logging
import os
from typing import Dict, List, Optional

import requests

from . import ActionHandler, ActionResult, render_template

logger = logging.getLogger(__name__)


def _get_base_url() -> str:
    """Use the existing CommonUtils helper so we hit the local app correctly."""
    try:
        from CommonUtils import get_base_url
        return get_base_url().rstrip('/')
    except Exception:
        host_port = os.environ.get('HOST_PORT', '5001')
        return f"http://localhost:{host_port}"


def _internal_api_key() -> str:
    return os.environ.get('API_KEY', '') or os.environ.get('INTERNAL_API_KEY', '')


def _resolve_workflow_id_by_name(name: str) -> Optional[int]:
    """Look up a workflow by name. Returns the id or None."""
    try:
        from AppUtils import get_db_connection
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", (os.environ.get('API_KEY', ''),))
        cursor.execute(
            "SELECT TOP 1 id FROM Workflows WHERE workflow_name = ? ORDER BY id DESC",
            (name,),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if row:
            return int(row[0])
    except Exception as e:
        logger.error(f"Error resolving workflow name '{name}': {e}")
    return None


class WorkflowAction(ActionHandler):
    action_type = 'workflow'

    def execute(self, collected_data: Dict, session, config: Dict, schema: Dict) -> ActionResult:
        label = config.get('label') or 'Trigger workflow'

        # Resolve workflow id
        workflow_id = config.get('workflow_id')
        if not workflow_id and config.get('workflow_name'):
            workflow_id = _resolve_workflow_id_by_name(config['workflow_name'])
        if not workflow_id:
            return ActionResult(
                action_type=self.action_type,
                label=label,
                success=False,
                message="Could not resolve workflow_id (provide workflow_id or a valid workflow_name).",
            )

        # Render variables from the mapping
        mapping = config.get('variable_mapping') or {}
        variables = {}
        for var_name, template in mapping.items():
            variables[var_name] = render_template(template, collected_data, session, schema)

        # Always include some helpful defaults so the workflow can identify the source
        variables.setdefault('dca_session_id', session.session_id)
        variables.setdefault('dca_config_id', session.config_id)
        variables.setdefault('dca_user_id', session.user_id)

        payload = {
            'workflow_id': int(workflow_id),
            'initiator': 'data_collection_agent',
            'variables': variables,
        }

        url = f"{_get_base_url()}/api/workflow/run"
        api_key = _internal_api_key()
        headers = {
            'Content-Type': 'application/json',
        }
        if api_key:
            headers['X-API-Key'] = api_key
            headers['X-Internal-API-Key'] = api_key

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
        except requests.exceptions.RequestException as e:
            return ActionResult(
                action_type=self.action_type,
                label=label,
                success=False,
                message=f"Could not reach workflow API: {e}",
            )

        if response.status_code != 200:
            return ActionResult(
                action_type=self.action_type,
                label=label,
                success=False,
                message=f"Workflow API returned HTTP {response.status_code}",
                details={'response': response.text[:500]},
            )

        try:
            body = response.json()
        except Exception:
            body = {}

        execution_id = body.get('execution_id')
        if not execution_id:
            return ActionResult(
                action_type=self.action_type,
                label=label,
                success=False,
                message=body.get('message') or 'Workflow API returned no execution_id',
                details={'response': body},
            )

        return ActionResult(
            action_type=self.action_type,
            label=label,
            success=True,
            message=f"Workflow {workflow_id} started (execution {execution_id}).",
            details={
                'workflow_id': workflow_id,
                'execution_id': execution_id,
                'variables': variables,
            },
        )

    def validate_config(self, config: Dict) -> List[str]:
        errors = []
        if not config.get('workflow_id') and not config.get('workflow_name'):
            errors.append("workflow action requires 'workflow_id' or 'workflow_name'")
        if 'variable_mapping' in config and not isinstance(config['variable_mapping'], dict):
            errors.append("'variable_mapping' must be an object")
        return errors
