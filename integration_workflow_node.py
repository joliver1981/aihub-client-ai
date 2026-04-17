# integration_workflow_node.py
"""
Integration Node for Workflow Execution
=======================================

Provides a workflow node type for executing integration operations.
This allows workflows to interact with external systems like QuickBooks,
Shopify, Stripe, etc.

Usage in workflows:
    Node Type: "Integration"
    Configuration:
        - integration_id: ID of the user's connected integration
        - operation: Operation key to execute (e.g., 'get_invoices')
        - parameters: Dict of operation parameters
        - outputVariable: Variable to store results
        - continueOnError: Whether to continue workflow on failure
"""

import json
import logging
from typing import Dict, Any, Optional

from integration_manager import get_integration_manager, IntegrationManager

logger = logging.getLogger(__name__)


def execute_integration_node(
    execution_id: str,
    node: Dict,
    variables: Dict,
    log_callback: callable = None
) -> Dict[str, Any]:
    """
    Execute an Integration node in a workflow.
    
    This function is called by the WorkflowExecutionEngine when it encounters
    an Integration node type.
    
    Args:
        execution_id: The workflow execution ID
        node: Node configuration from workflow definition
        variables: Current workflow variables
        log_callback: Function to log execution steps
        
    Returns:
        Dict with 'success', 'data', 'error' keys
    """
    config = node.get('config', {})
    node_id = node.get('id', 'unknown')
    
    # Get configuration
    integration_id = config.get('integration_id') or config.get('integrationId')
    operation_key = config.get('operation') or config.get('operationKey')
    parameters_raw = config.get('parameters', {})
    output_variable = config.get('outputVariable')
    continue_on_error = config.get('continueOnError', False)
    
    # Handle case where parameters is a JSON string instead of dict
    if isinstance(parameters_raw, str):
        try:
            parameters_raw = json.loads(parameters_raw) if parameters_raw else {}
        except json.JSONDecodeError:
            parameters_raw = {}
    
    def log(level: str, message: str):
        if log_callback:
            log_callback(execution_id, node_id, level, message)
        logger.log(getattr(logging, level.upper(), logging.INFO), message)
    
    log('info', f"Executing Integration node: operation={operation_key}")
    
    # Validate required fields
    if not integration_id:
        error_msg = "Integration ID is required"
        log('error', error_msg)
        return {
            'success': False,
            'error': error_msg,
            'data': None
        }
    
    if not operation_key:
        error_msg = "Operation is required"
        log('error', error_msg)
        return {
            'success': False,
            'error': error_msg,
            'data': None
        }
    
    # Resolve parameters - substitute workflow variables
    parameters = resolve_parameters(parameters_raw, variables)
    
    log('debug', f"Resolved parameters: {json.dumps(parameters, default=str)[:500]}")
    
    # Get the integration manager and execute
    try:
        manager = get_integration_manager()
        
        result = manager.execute_operation(
            integration_id=int(integration_id),
            operation_key=operation_key,
            parameters=parameters,
            context={
                'workflow_execution_id': execution_id,
                'node_id': node_id
            }
        )
        
        if result.get('success'):
            log('info', f"Integration operation completed successfully in {result.get('response_time_ms', 0)}ms")
            
            # Store output in variable if specified
            output_data = {
                'success': True,
                'data': result.get('data'),
                'response_time_ms': result.get('response_time_ms'),
                'status_code': result.get('status_code')
            }
            
            if output_variable:
                variables[output_variable] = result.get('data')
                log('debug', f"Stored result in variable: {output_variable}")
            
            return output_data
            
        else:
            error_msg = result.get('error', 'Unknown error')
            log('error', f"Integration operation failed: {error_msg}")
            
            if continue_on_error:
                return {
                    'success': True,  # Continue workflow
                    'data': None,
                    'error': error_msg,
                    'continued_on_error': True
                }
            
            return {
                'success': False,
                'error': error_msg,
                'data': None
            }
            
    except Exception as e:
        error_msg = f"Exception executing integration: {str(e)}"
        log('error', error_msg)
        
        if continue_on_error:
            return {
                'success': True,
                'data': None,
                'error': error_msg,
                'continued_on_error': True
            }
        
        return {
            'success': False,
            'error': error_msg,
            'data': None
        }


def resolve_parameters(parameters: Dict, variables: Dict) -> Dict:
    """
    Resolve workflow variables in parameter values.
    
    Handles ${variableName} syntax and nested variable references.
    
    Args:
        parameters: Raw parameters with potential variable references
        variables: Current workflow variables
        
    Returns:
        Parameters with variables resolved
    """
    import re
    
    # Safety check: parse if string
    if isinstance(parameters, str):
        try:
            parameters = json.loads(parameters) if parameters else {}
        except json.JSONDecodeError:
            parameters = {}
    
    # Ensure we have a dict
    if not isinstance(parameters, dict):
        return {}
    
    def resolve_value(value):
        if isinstance(value, str):
            # Find all ${variableName} patterns
            pattern = r'\$\{([^}]+)\}'
            
            def replacer(match):
                var_path = match.group(1)
                resolved = get_nested_value(variables, var_path)
                return str(resolved) if resolved is not None else match.group(0)
            
            # Check if entire string is a variable reference
            full_match = re.fullmatch(pattern, value.strip())
            if full_match:
                var_path = full_match.group(1)
                resolved = get_nested_value(variables, var_path)
                if resolved is not None:
                    return resolved  # Return actual type, not string
            
            # Otherwise do string substitution
            return re.sub(pattern, replacer, value)
            
        elif isinstance(value, dict):
            return {k: resolve_value(v) for k, v in value.items()}
            
        elif isinstance(value, list):
            return [resolve_value(item) for item in value]
            
        return value
    
    return resolve_value(parameters)


def get_nested_value(data: Dict, path: str) -> Any:
    """
    Get a nested value from a dictionary using dot notation.
    
    Supports:
        - Simple keys: "variableName"
        - Nested keys: "data.results.count"
        - Array indices: "items[0].name"
        - Special variable: "_previousStepOutput.data"
    
    Args:
        data: Dictionary to search
        path: Dot-notation path
        
    Returns:
        The value at the path, or None if not found
    """
    import re
    
    if not path:
        return None
    
    current = data
    
    # Split path by dots, but handle array indices
    parts = re.split(r'\.(?![^\[]*\])', path)
    
    for part in parts:
        if current is None:
            return None
        
        # Check for array index: items[0]
        array_match = re.match(r'(\w+)\[(\d+)\]', part)
        
        if array_match:
            key = array_match.group(1)
            index = int(array_match.group(2))
            
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
            
            if isinstance(current, list) and len(current) > index:
                current = current[index]
            else:
                return None
        else:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
    
    return current


def get_integration_node_config_schema() -> Dict:
    """
    Get the configuration schema for the Integration node.
    Used by the workflow designer UI.
    """
    return {
        "type": "Integration",
        "label": "Integration",
        "icon": "bi-plug-fill",
        "category": "External",
        "description": "Execute operations on connected integrations (QuickBooks, Shopify, etc.)",
        "config_fields": [
            {
                "name": "integration_id",
                "label": "Integration",
                "type": "select",
                "required": True,
                "dynamic_options": True,
                "options_url": "/api/integrations",
                "option_value": "integration_id",
                "option_label": "integration_name",
                "placeholder": "Select an integration"
            },
            {
                "name": "operation",
                "label": "Operation",
                "type": "select",
                "required": True,
                "depends_on": "integration_id",
                "dynamic_options": True,
                "options_url": "/api/integrations/{integration_id}/operations",
                "option_value": "key",
                "option_label": "name",
                "placeholder": "Select an operation"
            },
            {
                "name": "parameters",
                "label": "Parameters",
                "type": "dynamic_form",
                "required": False,
                "depends_on": "operation",
                "description": "Operation-specific parameters"
            },
            {
                "name": "outputVariable",
                "label": "Output Variable",
                "type": "variable",
                "required": False,
                "placeholder": "e.g., integrationResult",
                "description": "Variable to store the operation result"
            },
            {
                "name": "continueOnError",
                "label": "Continue on Error",
                "type": "checkbox",
                "required": False,
                "default": False,
                "description": "Continue workflow execution even if operation fails"
            }
        ],
        "connections": {
            "inputs": 1,
            "outputs": {
                "pass": "Operation succeeded",
                "fail": "Operation failed"
            }
        }
    }


# =============================================================================
# Webhook Trigger Integration
# =============================================================================

def process_integration_webhook(
    webhook_token: str,
    event_type: str,
    payload: Dict,
    headers: Dict
) -> Dict[str, Any]:
    """
    Process an incoming webhook from an integration.
    
    This is called when an external system (Shopify, Stripe, etc.) sends
    a webhook to our endpoint.
    
    Args:
        webhook_token: The unique webhook token from URL
        event_type: Event type from headers
        payload: Webhook payload
        headers: Request headers
        
    Returns:
        Dict with processing result
    """
    import os
    from CommonUtils import get_db_connection
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Look up webhook configuration
        cursor.execute("""
            SELECT 
                w.webhook_id, w.integration_id, w.workflow_id,
                w.variable_mappings, w.signing_secret, w.verify_signature,
                i.integration_name, t.template_key
            FROM IntegrationWebhooks w
            INNER JOIN UserIntegrations i ON w.integration_id = i.integration_id
            INNER JOIN IntegrationTemplates t ON i.template_id = t.template_id
            WHERE w.webhook_token = ? AND w.is_active = 1
        """, webhook_token)
        
        row = cursor.fetchone()
        if not row:
            return {'success': False, 'error': 'Invalid webhook token'}
        
        webhook_id = row[0]
        integration_id = row[1]
        workflow_id = row[2]
        variable_mappings = json.loads(row[3]) if row[3] else {}
        signing_secret = row[4]
        verify_signature = row[5]
        integration_name = row[6]
        template_key = row[7]
        
        # Verify signature if required
        if verify_signature and signing_secret:
            if not verify_webhook_signature(payload, headers, signing_secret, template_key):
                logger.warning(f"Webhook signature verification failed for {webhook_token}")
                return {'success': False, 'error': 'Invalid signature'}
        
        # Update webhook stats
        cursor.execute("""
            UPDATE IntegrationWebhooks
            SET last_received_at = GETUTCDATE(), total_received = total_received + 1
            WHERE webhook_id = ?
        """, webhook_id)
        conn.commit()
        
        # If a workflow is configured, trigger it
        if workflow_id:
            # Map webhook data to workflow variables
            workflow_variables = {
                'webhook_event': event_type,
                'webhook_payload': payload,
                'webhook_received_at': datetime.utcnow().isoformat(),
                'integration_name': integration_name
            }
            
            # Apply custom variable mappings
            for var_name, json_path in variable_mappings.items():
                value = get_nested_value(payload, json_path)
                if value is not None:
                    workflow_variables[var_name] = value
            
            # Trigger the workflow
            from workflow_execution import WorkflowExecutionEngine
            from DataUtils import get_database_connection_string
            
            engine = WorkflowExecutionEngine(get_database_connection_string())
            
            # Get workflow definition
            cursor.execute("""
                SELECT workflow_data FROM Workflows WHERE id = ?
            """, workflow_id)
            
            wf_row = cursor.fetchone()
            if wf_row:
                workflow_data = json.loads(wf_row[0])
                workflow_data['initialVariables'] = workflow_variables
                
                execution_id = engine.start_workflow(
                    workflow_id=workflow_id,
                    workflow_data=workflow_data,
                    initiator=f"webhook:{webhook_token}"
                )
                
                return {
                    'success': True,
                    'execution_id': execution_id,
                    'message': 'Workflow triggered'
                }
        
        return {
            'success': True,
            'message': 'Webhook received'
        }
        
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return {'success': False, 'error': str(e)}
    
    finally:
        if 'cursor' in dir():
            cursor.close()
        if 'conn' in dir():
            conn.close()


def verify_webhook_signature(
    payload: Dict,
    headers: Dict,
    signing_secret: str,
    template_key: str
) -> bool:
    """
    Verify webhook signature based on integration type.
    
    Different platforms use different signature schemes:
    - Shopify: HMAC-SHA256 in X-Shopify-Hmac-Sha256
    - Stripe: HMAC-SHA256 in Stripe-Signature
    - etc.
    """
    import hmac
    import hashlib
    
    try:
        if template_key == 'shopify':
            # Shopify uses HMAC-SHA256 base64
            signature = headers.get('X-Shopify-Hmac-Sha256', '')
            computed = hmac.new(
                signing_secret.encode(),
                json.dumps(payload).encode(),
                hashlib.sha256
            ).digest()
            import base64
            return hmac.compare_digest(base64.b64encode(computed).decode(), signature)
        
        elif template_key == 'stripe':
            # Stripe uses timestamp.signature format
            signature_header = headers.get('Stripe-Signature', '')
            # Parse: t=timestamp,v1=signature
            parts = dict(part.split('=') for part in signature_header.split(','))
            timestamp = parts.get('t', '')
            signature = parts.get('v1', '')
            
            signed_payload = f"{timestamp}.{json.dumps(payload)}"
            computed = hmac.new(
                signing_secret.encode(),
                signed_payload.encode(),
                hashlib.sha256
            ).hexdigest()
            
            return hmac.compare_digest(computed, signature)
        
        # Default: simple HMAC-SHA256
        signature = headers.get('X-Webhook-Signature', '')
        computed = hmac.new(
            signing_secret.encode(),
            json.dumps(payload).encode(),
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(computed, signature)
        
    except Exception as e:
        logger.error(f"Signature verification error: {e}")
        return False


# Import datetime for webhook processing
from datetime import datetime
