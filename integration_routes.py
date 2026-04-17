# integration_routes.py
"""
Flask API Routes for Universal Integrations
============================================

Provides REST API endpoints for:
- Browsing integration templates
- Creating/managing user integrations  
- Testing connections
- Executing operations
- OAuth callback handling
"""

import json
import logging
import os
import secrets
import urllib.parse
from datetime import datetime, timedelta
from functools import wraps
from flask import Blueprint, request, jsonify, redirect, url_for, session, current_app, g

from integration_manager import (
    IntegrationManager,
    TemplateManager,
    get_integration_manager,
    get_integration_secret_name
)
from local_secrets import set_local_secret, get_local_secret
from logging.handlers import WatchedFileHandler

from CommonUtils import rotate_logs_on_startup, get_log_path


# =============================================================================
# LOGGING SETUP
# =============================================================================

def setup_logging():
    """Configure logging for notification client"""
    logger = logging.getLogger("IntegrationRoutes")
    log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
    log_level = getattr(logging, log_level_name, logging.DEBUG)
    logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    log_file = os.getenv('INTEGRATIONS_LOG', get_log_path('integrations_log.txt'))
    handler = WatchedFileHandler(filename=log_file, encoding='utf-8')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger

# Initialize logging
_log_file = os.getenv('INTEGRATIONS_LOG', get_log_path('integrations_log.txt'))
rotate_logs_on_startup(_log_file)
logger = setup_logging()


# Create Blueprint
integrations_bp = Blueprint('integrations', __name__, url_prefix='/api/integrations')


# =============================================================================
# Auth Decorator
# =============================================================================

from role_decorators import api_key_or_session_required

def login_required(f):
    """Decorator to require login — accepts both API key and session auth.
    Integrations require Developer role (min_role=2) for session-based users.
    API key auth (used by builder service) is trusted."""
    return api_key_or_session_required(min_role=2)(f)


def get_safe_user_id():
    """
    Safely get user_id that works with both API key auth and session auth.

    For API key auth (builder service), returns 1 (system user).
    For session auth, returns current_user.id.

    Returns:
        int: User ID or 1 as default for API key auth
    """
    from flask_login import current_user

    # API key auth: use system user (1)
    if getattr(g, 'auth_method', None) == 'api_key':
        return 1

    # Session auth: use current_user.id
    if hasattr(current_user, 'id'):
        return current_user.id

    # Fallback: system user
    return 1


# =============================================================================
# Template Endpoints
# =============================================================================

@integrations_bp.route('/templates', methods=['GET'])
@api_key_or_session_required(min_role=2)
def list_templates():
    """
    List available integration templates.
    
    Query params:
        category: Filter by category (optional)
    
    Returns:
        {
            "status": "success",
            "templates": [...],
            "categories": [...]
        }
    """
    try:
        manager = get_integration_manager()
        category = request.args.get('category')
        
        templates = manager.get_available_templates(category)
        categories = manager.get_categories()
        
        # Remove sensitive config from templates for display
        safe_templates = []
        for template in templates:
            auth_config = template.get('auth_config', {})
            safe_template = {
                'template_key': template.get('template_key'),
                'platform_name': template.get('platform_name'),
                'platform_category': template.get('platform_category'),
                'description': template.get('description'),
                'logo_url': template.get('logo_url'),
                'auth_type': template.get('auth_type'),
                'documentation_url': template.get('documentation_url'),
                'setup_instructions': template.get('setup_instructions'),
                'is_builtin': template.get('is_builtin', True),
                'is_custom': template.get('is_custom', False),
                'supports_webhooks': template.get('supports_webhooks', False),
                'base_url': template.get('base_url', ''),
            }

            # Include auth_config subset needed for UI (grant_type for OAuth flow detection)
            safe_template['auth_config'] = {
                'grant_type': auth_config.get('grant_type'),
            }
            # Cloud storage templates use credential_fields for dynamic UI
            if auth_config.get('credential_fields'):
                safe_template['auth_config']['credential_fields'] = auth_config['credential_fields']

            # Instance ID config can be at top level or inside auth_config
            safe_template['requires_instance_id'] = (
                template.get('requires_instance_id') or
                auth_config.get('requires_instance_id', False)
            )
            safe_template['instance_id_field'] = (
                template.get('instance_id_field') or
                auth_config.get('instance_id_field')
            )
            safe_template['instance_id_label'] = (
                template.get('instance_id_label') or
                auth_config.get('instance_id_label')
            )
            safe_template['instance_id_placeholder'] = (
                template.get('instance_id_placeholder') or
                auth_config.get('instance_id_placeholder')
            )

            safe_template['additional_instance_fields'] = template.get('additional_instance_fields', [])

            safe_templates.append(safe_template)
        
        return jsonify({
            'status': 'success',
            'templates': safe_templates,
            'categories': categories
        })
        
    except Exception as e:
        logger.error(f"Error listing templates: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@integrations_bp.route('/templates/<template_key>', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_template(template_key):
    """
    Get a specific template with full details.
    
    Returns:
        Full template details including operations
    """
    try:
        manager = get_integration_manager()
        template = manager.get_template(template_key)
        
        if not template:
            return jsonify({
                'status': 'error',
                'message': f"Template '{template_key}' not found"
            }), 404
        
        return jsonify({
            'status': 'success',
            'template': template
        })
        
    except Exception as e:
        logger.error(f"Error getting template: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@integrations_bp.route('/templates/reload', methods=['POST'])
@api_key_or_session_required(min_role=2)
def reload_templates():
    """
    Force reload templates from disk.
    
    Useful after adding new template files without restarting the app.
    
    Returns:
        {
            "status": "success",
            "templates_count": 7,
            "message": "Templates reloaded successfully"
        }
    """
    try:
        templates = TemplateManager.reload()
        
        return jsonify({
            'status': 'success',
            'templates_count': len(templates),
            'message': 'Templates reloaded successfully'
        })
        
    except Exception as e:
        logger.error(f"Error reloading templates: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@integrations_bp.route('/templates/storage-info', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_storage_info():
    """
    Get information about template storage.
    
    Returns:
        {
            "status": "success",
            "storage_info": {
                "integrations_dir": "/path/to/integrations",
                "builtin_count": 7,
                "custom_count": 2,
                ...
            }
        }
    """
    try:
        info = TemplateManager.get_storage_info()
        
        return jsonify({
            'status': 'success',
            'storage_info': info
        })
        
    except Exception as e:
        logger.error(f"Error getting storage info: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@integrations_bp.route('/templates/<template_key>/export', methods=['GET'])
@api_key_or_session_required(min_role=2)
def export_template(template_key):
    """
    Export a template as a downloadable JSON file.
    
    Works for both builtin and custom templates.
    Useful for sharing templates between installations.
    
    Returns:
        JSON file download
    """
    try:
        template = TemplateManager.get_template(template_key)
        
        if not template:
            return jsonify({
                'status': 'error',
                'message': f"Template '{template_key}' not found"
            }), 404
        
        # Create export version (remove internal fields)
        export_template = {
            'template_key': template.get('template_key'),
            'platform_name': template.get('platform_name'),
            'platform_category': template.get('platform_category'),
            'description': template.get('description'),
            'logo_url': template.get('logo_url'),
            'documentation_url': template.get('documentation_url'),
            'auth_type': template.get('auth_type'),
            'auth_config': template.get('auth_config', {}),
            'base_url': template.get('base_url'),
            'default_headers': template.get('default_headers', {}),
            'operations': template.get('operations', []),
            'supports_webhooks': template.get('supports_webhooks', False),
            'webhook_events': template.get('webhook_events', []),
            'setup_instructions': template.get('setup_instructions'),
            'version': template.get('version', '1.0.0'),
            '_exported_at': datetime.utcnow().isoformat(),
            '_exported_from': 'AI Hub Universal Integrations'
        }
        
        # Return as downloadable JSON file
        response = current_app.response_class(
            response=json.dumps(export_template, indent=2),
            status=200,
            mimetype='application/json'
        )
        response.headers['Content-Disposition'] = f'attachment; filename="{template_key}.json"'
        
        return response
        
    except Exception as e:
        logger.error(f"Error exporting template: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@integrations_bp.route('/templates/import', methods=['POST'])
@api_key_or_session_required(min_role=2)
def import_template():
    """
    Import a template from JSON.
    
    Request can be:
    1. JSON body with template definition
    2. Multipart form with file upload
    
    Query params:
        save_to_file: If 'true', save to /integrations/custom/ folder
                      If 'false' (default), save to database
    
    Returns:
        {
            "status": "success",
            "template_key": "imported_template",
            "message": "Template imported successfully",
            "saved_to": "database" or "file"
        }
    """
    try:
        from flask_login import current_user
        save_to_file = request.args.get('save_to_file', 'false').lower() == 'true'
        
        # Get template data from request
        template_data = None
        
        # Check for file upload
        if 'file' in request.files:
            file = request.files['file']
            if file.filename == '':
                return jsonify({
                    'status': 'error',
                    'message': 'No file selected'
                }), 400
            
            if not file.filename.endswith('.json'):
                return jsonify({
                    'status': 'error',
                    'message': 'File must be a JSON file'
                }), 400
            
            try:
                template_data = json.load(file)
            except json.JSONDecodeError as e:
                return jsonify({
                    'status': 'error',
                    'message': f'Invalid JSON in file: {e}'
                }), 400
        
        # Check for JSON body
        elif request.is_json:
            template_data = request.get_json()
        
        else:
            return jsonify({
                'status': 'error',
                'message': 'Request must include either a JSON file upload or JSON body'
            }), 400
        
        if not template_data:
            return jsonify({
                'status': 'error',
                'message': 'No template data provided'
            }), 400
        
        # Validate required fields
        required_fields = ['template_key', 'platform_name', 'auth_type']
        missing = [f for f in required_fields if not template_data.get(f)]
        if missing:
            return jsonify({
                'status': 'error',
                'message': f'Missing required fields: {", ".join(missing)}'
            }), 400
        
        template_key = template_data['template_key']
        
        # Check if template already exists
        existing = TemplateManager.get_template(template_key)
        if existing:
            # Check if it's a builtin template (can't overwrite)
            if existing.get('is_builtin'):
                return jsonify({
                    'status': 'error',
                    'message': f"Cannot overwrite builtin template '{template_key}'. Use a different template_key."
                }), 400
        
        # Remove export metadata if present
        template_data.pop('_exported_at', None)
        template_data.pop('_exported_from', None)
        
        # Set category if not provided
        if not template_data.get('platform_category'):
            template_data['platform_category'] = 'Custom'
        
        # Save the template
        try:
            saved_key = TemplateManager.save_custom_template(template_data, save_to_file=save_to_file)
            
            return jsonify({
                'status': 'success',
                'template_key': saved_key,
                'message': f"Template '{saved_key}' imported successfully",
                'saved_to': 'file' if save_to_file else 'database',
                'is_update': existing is not None
            })
            
        except ValueError as e:
            return jsonify({
                'status': 'error',
                'message': str(e)
            }), 400
        
    except Exception as e:
        logger.error(f"Error importing template: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@integrations_bp.route('/templates/custom/<template_key>', methods=['DELETE'])
@api_key_or_session_required(min_role=2)
def delete_custom_template(template_key):
    """
    Delete a custom template.
    
    Only custom templates can be deleted (not builtin).
    Removes from both file system and database.
    
    Returns:
        {
            "status": "success",
            "message": "Template deleted successfully"
        }
    """
    try:
        # Check if it exists
        template = TemplateManager.get_template(template_key)
        if not template:
            return jsonify({
                'status': 'error',
                'message': f"Template '{template_key}' not found"
            }), 404
        
        # Check if builtin
        if template.get('is_builtin'):
            return jsonify({
                'status': 'error',
                'message': 'Cannot delete builtin templates'
            }), 400
        
        # Check if any integrations use this template
        manager = get_integration_manager()
        integrations_using = manager.get_integrations_by_template(template_key)
        
        if integrations_using:
            return jsonify({
                'status': 'error',
                'message': f"Cannot delete template: {len(integrations_using)} integration(s) are using it",
                'integrations': [i.get('integration_name') for i in integrations_using]
            }), 400
        
        # Delete the template
        deleted = TemplateManager.delete_custom_template(template_key)
        
        if deleted:
            return jsonify({
                'status': 'success',
                'message': f"Template '{template_key}' deleted successfully"
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to delete template'
            }), 500
        
    except Exception as e:
        logger.error(f"Error deleting template: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@integrations_bp.route('/templates/custom', methods=['GET'])
@api_key_or_session_required(min_role=2)
def list_custom_templates():
    """
    List only custom templates (user-created).
    
    Returns templates from both database and custom files folder.
    
    Returns:
        {
            "status": "success",
            "templates": [...],
            "count": 5
        }
    """
    try:
        all_templates = TemplateManager.get_all_templates()
        custom_templates = [t for t in all_templates if not t.get('is_builtin')]
        
        # Add source info
        for template in custom_templates:
            if template.get('source_file'):
                template['source'] = 'file'
            elif template.get('source') == 'database':
                template['source'] = 'database'
            else:
                template['source'] = 'unknown'
        
        return jsonify({
            'status': 'success',
            'templates': custom_templates,
            'count': len(custom_templates)
        })
        
    except Exception as e:
        logger.error(f"Error listing custom templates: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# =============================================================================
# Integration CRUD Endpoints
# =============================================================================

@integrations_bp.route('', methods=['GET'])
@api_key_or_session_required(min_role=2)
def list_integrations():
    """
    List user's integrations.
    
    Returns:
        {
            "status": "success",
            "integrations": [...]
        }
    """
    try:
        from flask_login import current_user
        from flask import g
        manager = get_integration_manager()
        
        # Admins (role >= 2) and API key auth can see all integrations, regular users only see their own
        if getattr(g, 'auth_method', None) == 'api_key':
            user_id_filter = None  # API key auth is trusted
        elif hasattr(current_user, 'role') and current_user.role >= 2:
            user_id_filter = None
        elif hasattr(current_user, 'id'):
            user_id_filter = current_user.id
        else:
            user_id_filter = None
        integrations = manager.list_integrations(user_id=user_id_filter)
        
        return jsonify({
            'status': 'success',
            'integrations': integrations,
            'count': len(integrations)
        })
        
    except Exception as e:
        logger.error(f"Error listing integrations: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@integrations_bp.route('', methods=['POST'])
@api_key_or_session_required(min_role=2)
def create_integration():
    """
    Create a new integration.
    
    Request body:
        {
            "template_key": "quickbooks_online",
            "integration_name": "My QuickBooks",
            "description": "Optional description",
            "instance_config": {"realmId": "123456"},
            "credentials": {"api_key": "xxx"} or {"access_token": "xxx"}
        }
    
    Returns:
        {
            "status": "success",
            "integration_id": 123
        }
    """
    try:
        from flask_login import current_user
        manager = get_integration_manager()
        
        data = request.get_json()
        
        if not data.get('template_key'):
            return jsonify({
                'status': 'error',
                'message': 'template_key is required'
            }), 400
        
        if not data.get('integration_name'):
            return jsonify({
                'status': 'error',
                'message': 'integration_name is required'
            }), 400
        
        # Handle credentials - check for local secret references
        credentials = data.get('credentials', {})
        processed_credentials = {}
        
        for key, value in credentials.items():
            if value and value.startswith('{{LOCAL_SECRET:'):
                # It's a reference to an existing secret - resolve it
                import re
                match = re.match(r'\{\{LOCAL_SECRET:([A-Za-z0-9_]+)\}\}', value)
                if match:
                    secret_name = match.group(1)
                    actual_value = get_local_secret(secret_name)
                    if actual_value:
                        processed_credentials[key] = actual_value
                    else:
                        return jsonify({
                            'status': 'error',
                            'message': f"Secret '{secret_name}' not found"
                        }), 400
            else:
                processed_credentials[key] = value
        
        # Handle base_url override (for custom/configurable templates)
        base_url_override = data.get('base_url_override')

        user_id = get_safe_user_id()
        logger.info(f"Creating integration '{data['integration_name']}' with template '{data['template_key']}' "
                    f"for user {user_id}"
                    f"{' (base_url_override=' + base_url_override + ')' if base_url_override else ''}")

        success, integration_id, message = manager.create_integration(
            template_key=data['template_key'],
            integration_name=data['integration_name'],
            credentials=processed_credentials,
            instance_config=data.get('instance_config', {}),
            user_id=user_id,
            description=data.get('description'),
            base_url_override=base_url_override
        )

        if success:
            logger.info(f"Integration created successfully: id={integration_id}, name='{data['integration_name']}'")
            return jsonify({
                'status': 'success',
                'message': message,
                'integration_id': integration_id
            })
        else:
            logger.warning(f"Integration creation failed: {message}")
            return jsonify({
                'status': 'error',
                'message': message
            }), 400
            
    except Exception as e:
        logger.error(f"Error creating integration: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@integrations_bp.route('/<int:integration_id>', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_integration(integration_id):
    """Get integration details."""
    try:
        manager = get_integration_manager()
        integration = manager.get_integration(integration_id)
        
        if not integration:
            return jsonify({
                'status': 'error',
                'message': 'Integration not found'
            }), 404
        
        # Remove sensitive data
        safe_integration = {
            'integration_id': integration.get('integration_id'),
            'integration_name': integration.get('integration_name'),
            'template_key': integration.get('template_key'),
            'platform_name': integration.get('platform_name'),
            'auth_type': integration.get('auth_type'),
            'is_connected': integration.get('is_connected'),
            'operations': integration.get('operations', [])
        }
        
        return jsonify({
            'status': 'success',
            'integration': safe_integration
        })
        
    except Exception as e:
        logger.error(f"Error getting integration: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@integrations_bp.route('/<int:integration_id>', methods=['PUT'])
@api_key_or_session_required(min_role=2)
def update_integration(integration_id):
    """Update an integration."""
    try:
        manager = get_integration_manager()
        data = request.get_json()
        
        success, message = manager.update_integration(
            integration_id=integration_id,
            integration_name=data.get('integration_name'),
            description=data.get('description'),
            instance_config=data.get('instance_config'),
            credentials=data.get('credentials')
        )
        
        if success:
            return jsonify({
                'status': 'success',
                'message': message
            })
        else:
            return jsonify({
                'status': 'error',
                'message': message
            }), 400
            
    except Exception as e:
        logger.error(f"Error updating integration: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@integrations_bp.route('/<int:integration_id>', methods=['DELETE'])
@api_key_or_session_required(min_role=2)
def delete_integration(integration_id):
    """Delete an integration."""
    try:
        manager = get_integration_manager()
        
        success, message = manager.delete_integration(integration_id)
        
        if success:
            return jsonify({
                'status': 'success',
                'message': message
            })
        else:
            return jsonify({
                'status': 'error',
                'message': message
            }), 400
            
    except Exception as e:
        logger.error(f"Error deleting integration: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# =============================================================================
# Connection Testing
# =============================================================================

@integrations_bp.route('/<int:integration_id>/test', methods=['POST'])
@api_key_or_session_required(min_role=2)
def test_connection(integration_id):
    """Test an integration connection."""
    try:
        logger.info(f"Testing connection for integration {integration_id}")
        manager = get_integration_manager()

        result = manager.test_connection(integration_id)

        if result.get('success'):
            logger.info(f"Connection test PASSED for integration {integration_id} "
                        f"({result.get('response_time_ms', '?')}ms)")
        else:
            logger.warning(f"Connection test FAILED for integration {integration_id}: "
                           f"{result.get('error')}")

        return jsonify({
            'status': 'success' if result.get('success') else 'error',
            'connected': result.get('success', False),
            'message': 'Connection successful' if result.get('success') else result.get('error'),
            'response_time_ms': result.get('response_time_ms')
        })
        
    except Exception as e:
        logger.error(f"Error testing connection: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# =============================================================================
# Operation Execution
# =============================================================================

@integrations_bp.route('/<int:integration_id>/operations', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_operations(integration_id):
    """Get available operations for an integration."""
    try:
        manager = get_integration_manager()
        
        operations = manager.get_operations(integration_id)
        
        return jsonify({
            'status': 'success',
            'operations': operations
        })
        
    except Exception as e:
        logger.error(f"Error getting operations: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@integrations_bp.route('/<int:integration_id>/execute', methods=['POST'])
@api_key_or_session_required(min_role=2)
def execute_operation(integration_id):
    """
    Execute an operation on an integration.
    
    Request body:
        {
            "operation": "get_invoices",
            "parameters": {
                "status": "Unpaid",
                "limit": 50
            }
        }
    
    Returns:
        {
            "status": "success",
            "data": {...},
            "response_time_ms": 234
        }
    """
    try:
        from flask_login import current_user
        manager = get_integration_manager()
        
        data = request.get_json()

        if not data.get('operation'):
            return jsonify({
                'status': 'error',
                'message': 'operation is required'
            }), 400

        user_id = get_safe_user_id()
        logger.info(f"Executing operation '{data['operation']}' on integration {integration_id} "
                    f"for user {user_id}")

        result = manager.execute_operation(
            integration_id=integration_id,
            operation_key=data['operation'],
            parameters=data.get('parameters', {}),
            context={
                'user_id': user_id,
                'workflow_execution_id': data.get('workflow_execution_id'),
                'agent_id': data.get('agent_id')
            }
        )

        if result.get('success'):
            logger.info(f"Operation '{data['operation']}' on integration {integration_id} "
                        f"completed in {result.get('response_time_ms', '?')}ms")
        else:
            logger.warning(f"Operation '{data['operation']}' on integration {integration_id} "
                           f"failed: {result.get('error')}")
        
        return jsonify({
            'status': 'success' if result.get('success') else 'error',
            'data': result.get('data'),
            'error': result.get('error'),
            'response_time_ms': result.get('response_time_ms'),
            'status_code': result.get('status_code')
        })
        
    except Exception as e:
        logger.error(f"Error executing operation: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# =============================================================================
# OAuth Flow Endpoints
# =============================================================================

@integrations_bp.route('/oauth/start/<template_key>', methods=['POST'])
@api_key_or_session_required(min_role=2)
def start_oauth(template_key):
    """
    Start OAuth flow for an integration.
    
    Request body:
        {
            "integration_name": "My QuickBooks",
            "instance_config": {}  // Optional
        }
    
    Returns:
        {
            "status": "success",
            "auth_url": "https://..."
        }
    """
    try:
        from flask_login import current_user
        manager = get_integration_manager()
        
        template = manager.get_template(template_key)
        if not template:
            return jsonify({
                'status': 'error',
                'message': f"Template '{template_key}' not found"
            }), 404
        
        if template.get('auth_type') != 'oauth2':
            return jsonify({
                'status': 'error',
                'message': 'This integration does not use OAuth'
            }), 400
        
        data = request.get_json() or {}
        auth_config = template.get('auth_config', {})
        
        # Generate state token for CSRF protection
        state = secrets.token_urlsafe(32)
        
        # Store OAuth state in session
        session['oauth_state'] = state
        session['oauth_template_key'] = template_key
        session['oauth_integration_name'] = data.get('integration_name', template.get('platform_name'))
        session['oauth_instance_config'] = data.get('instance_config', {})
        user_id = get_safe_user_id()
        session['oauth_user_id'] = user_id

        # Get OAuth client credentials from local secrets
        logger.info(f"Starting OAuth flow for template '{template_key}', user {user_id}")
        client_id = get_local_secret(f'OAUTH_{template_key.upper()}_CLIENT_ID')

        if not client_id:
            logger.warning(f"OAuth client ID not configured for template '{template_key}' "
                           f"(expected secret: OAUTH_{template_key.upper()}_CLIENT_ID)")
            return jsonify({
                'status': 'error',
                'message': f'OAuth client ID not configured. Please add OAUTH_{template_key.upper()}_CLIENT_ID to Local Secrets.'
            }), 400
        
        # Build authorization URL
        auth_url = auth_config.get('authorization_url')
        redirect_uri = url_for('integrations.oauth_callback', _external=True)
        
        params = {
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': ' '.join(auth_config.get('scopes', [])),
            'state': state
        }
        
        # Add any extra auth params
        extra_params = auth_config.get('extra_auth_params', {})
        params.update(extra_params)
        
        full_auth_url = f"{auth_url}?{urllib.parse.urlencode(params)}"
        
        return jsonify({
            'status': 'success',
            'auth_url': full_auth_url
        })
        
    except Exception as e:
        logger.error(f"Error starting OAuth: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@integrations_bp.route('/oauth/callback', methods=['GET'])
def oauth_callback():
    """Handle OAuth callback from provider."""
    try:
        # Verify state
        state = request.args.get('state')
        stored_state = session.get('oauth_state')
        
        if not state or state != stored_state:
            return redirect('/integrations?error=invalid_state')
        
        # Check for error
        error = request.args.get('error')
        if error:
            error_description = request.args.get('error_description', error)
            return redirect(f'/integrations?error={urllib.parse.quote(error_description)}')
        
        # Get authorization code
        code = request.args.get('code')
        if not code:
            return redirect('/integrations?error=no_code')
        
        # Get stored OAuth data
        template_key = session.get('oauth_template_key')
        integration_name = session.get('oauth_integration_name')
        instance_config = session.get('oauth_instance_config', {})
        user_id = session.get('oauth_user_id')
        
        # Get template
        manager = get_integration_manager()
        template = manager.get_template(template_key)
        
        if not template:
            return redirect('/integrations?error=template_not_found')
        
        auth_config = template.get('auth_config', {})
        
        # Exchange code for tokens
        client_id = get_local_secret(f'OAUTH_{template_key.upper()}_CLIENT_ID')
        client_secret = get_local_secret(f'OAUTH_{template_key.upper()}_CLIENT_SECRET')
        
        token_url = auth_config.get('token_url')
        redirect_uri = url_for('integrations.oauth_callback', _external=True)
        
        import requests as http_requests
        token_response = http_requests.post(token_url, data={
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
            'client_id': client_id,
            'client_secret': client_secret
        }, timeout=30)
        
        if token_response.status_code != 200:
            logger.error(f"Token exchange failed: {token_response.text}")
            return redirect('/integrations?error=token_exchange_failed')
        
        token_data = token_response.json()
        
        # Extract tokens
        access_token = token_data.get('access_token')
        refresh_token = token_data.get('refresh_token')
        expires_in = token_data.get('expires_in', 3600)
        
        # Some providers (like QuickBooks) include instance ID in response
        instance_id_field = auth_config.get('instance_id_field')
        if instance_id_field and instance_id_field in token_data:
            instance_config[instance_id_field] = token_data[instance_id_field]
        
        # Create the integration
        credentials = {
            'access_token': access_token,
            'refresh_token': refresh_token,
            'client_id': client_id,
            'client_secret': client_secret
        }
        
        success, integration_id, message = manager.create_integration(
            template_key=template_key,
            integration_name=integration_name,
            credentials=credentials,
            instance_config=instance_config,
            user_id=user_id
        )
        
        if success:
            # Update token expiration
            from CommonUtils import get_db_connection
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            cursor.execute("""
                UPDATE UserIntegrations
                SET oauth_token_expires_at = ?, oauth_scopes = ?
                WHERE integration_id = ?
            """, (
                expires_at,
                json.dumps(auth_config.get('scopes', [])),
                integration_id
            ))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            # Clear OAuth session data
            for key in ['oauth_state', 'oauth_template_key', 'oauth_integration_name', 
                       'oauth_instance_config', 'oauth_user_id']:
                session.pop(key, None)
            
            return redirect(f'/integrations?success=connected&id={integration_id}')
        else:
            return redirect(f'/integrations?error={urllib.parse.quote(message)}')
        
    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        return redirect(f'/integrations?error={urllib.parse.quote(str(e))}')


# =============================================================================
# Local Secrets Helper Endpoints
# =============================================================================

@integrations_bp.route('/secrets/check', methods=['POST'])
@api_key_or_session_required(min_role=2)
def check_secrets():
    """
    Check if required secrets exist for a template.
    
    Request body:
        {
            "template_key": "quickbooks_online"
        }
    
    Returns:
        {
            "status": "success",
            "secrets": {
                "OAUTH_QUICKBOOKS_ONLINE_CLIENT_ID": true,
                "OAUTH_QUICKBOOKS_ONLINE_CLIENT_SECRET": false
            }
        }
    """
    try:
        data = request.get_json()
        template_key = data.get('template_key')
        
        manager = get_integration_manager()
        template = manager.get_template(template_key)
        
        if not template:
            return jsonify({
                'status': 'error',
                'message': 'Template not found'
            }), 404
        
        auth_type = template.get('auth_type')
        secrets_status = {}
        
        if auth_type == 'oauth2':
            # Check for OAuth credentials
            client_id_key = f'OAUTH_{template_key.upper()}_CLIENT_ID'
            client_secret_key = f'OAUTH_{template_key.upper()}_CLIENT_SECRET'
            
            from local_secrets import has_local_secret
            secrets_status[client_id_key] = has_local_secret(client_id_key)
            secrets_status[client_secret_key] = has_local_secret(client_secret_key)
        
        return jsonify({
            'status': 'success',
            'secrets': secrets_status,
            'all_configured': all(secrets_status.values()) if secrets_status else True
        })
        
    except Exception as e:
        logger.error(f"Error checking secrets: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# =============================================================================
# Custom Template Endpoints
# =============================================================================

@integrations_bp.route('/templates/custom', methods=['POST'])
@api_key_or_session_required(min_role=2)
def create_custom_template():
    """
    Create a custom integration template.
    
    Request body:
        {
            "template_key": "my_custom_api",
            "platform_name": "My Custom API",
            "platform_category": "Custom",
            "base_url": "https://api.example.com",
            "auth_type": "api_key",
            "auth_config": {...},
            "operations": [...]
        }
    """
    try:
        from flask_login import current_user
        from CommonUtils import get_db_connection
        
        data = request.get_json()
        
        # Validate required fields
        required = ['template_key', 'platform_name', 'base_url', 'auth_type']
        for field in required:
            if not data.get(field):
                return jsonify({
                    'status': 'error',
                    'message': f'{field} is required'
                }), 400
        
        # Ensure template_key is unique
        template_key = data['template_key'].lower().replace(' ', '_')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Check if exists
        cursor.execute(
            "SELECT 1 FROM IntegrationTemplates WHERE template_key = ?",
            template_key
        )
        if cursor.fetchone():
            return jsonify({
                'status': 'error',
                'message': f"Template key '{template_key}' already exists"
            }), 400
        
        # Insert template using OUTPUT clause for reliable ID retrieval
        cursor.execute("""
            INSERT INTO IntegrationTemplates (
                template_key, platform_name, platform_category, description,
                auth_type, auth_config, base_url, default_headers,
                operations, is_builtin, is_active
            ) 
            OUTPUT INSERTED.template_id
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1)
        """, (
            template_key,
            data['platform_name'],
            data.get('platform_category', 'Custom'),
            data.get('description'),
            data['auth_type'],
            json.dumps(data.get('auth_config', {})),
            data['base_url'],
            json.dumps(data.get('default_headers', {})),
            json.dumps(data.get('operations', []))
        ))
        
        row = cursor.fetchone()
        if row is None:
            return jsonify({
                'status': 'error',
                'message': 'Failed to create template - check RLS policies'
            }), 500
        template_id = int(row[0])
        
        conn.commit()
        cursor.close()
        conn.close()
        
        # Clear template cache
        TemplateManager._templates_cache = None
        
        return jsonify({
            'status': 'success',
            'message': 'Custom template created',
            'template_id': template_id,
            'template_key': template_key
        })
        
    except Exception as e:
        logger.error(f"Error creating custom template: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# =============================================================================
# Execution Log Endpoints
# =============================================================================

@integrations_bp.route('/<int:integration_id>/logs', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_execution_logs(integration_id):
    """Get execution logs for an integration."""
    try:
        from CommonUtils import get_db_connection
        
        limit = request.args.get('limit', 50, type=int)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        cursor.execute("""
            SELECT TOP (?)
                log_id, operation_key, request_method, request_url,
                response_status, response_time_ms, success, error_message,
                executed_at
            FROM IntegrationExecutionLog
            WHERE integration_id = ?
            ORDER BY executed_at DESC
        """, (limit, integration_id))
        
        logs = []
        for row in cursor.fetchall():
            logs.append({
                'log_id': row[0],
                'operation_key': row[1],
                'request_method': row[2],
                'request_url': row[3],
                'response_status': row[4],
                'response_time_ms': row[5],
                'success': bool(row[6]),
                'error_message': row[7],
                'executed_at': row[8].isoformat() if row[8] else None
            })
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'logs': logs
        })
        
    except Exception as e:
        logger.error(f"Error getting logs: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500
