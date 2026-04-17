"""
Local Secrets API Routes
========================

Flask blueprint providing API endpoints for managing local secrets.
All secrets are stored locally on the user's machine - never in the cloud.

Endpoints:
    GET  /api/local-secrets              - List all secrets (metadata only)
    POST /api/local-secrets              - Add or update a secret
    GET  /api/local-secrets/<name>       - Get secret metadata (not value)
    DELETE /api/local-secrets/<name>     - Delete a secret
    POST /api/local-secrets/<name>/verify - Verify a secret exists
    GET  /api/local-secrets/info         - Get storage information
    GET  /api/local-secrets/categories   - Get list of categories
    POST /api/local-secrets/export       - Export secrets template
    POST /api/local-secrets/import       - Import secrets
    POST /api/local-secrets/test/<name>  - Test a secret (e.g., API key validation)

Integration:
    from local_secrets_routes import secrets_bp
    app.register_blueprint(secrets_bp)
"""

from flask import Blueprint, jsonify, request, current_app
from functools import wraps
import logging
import re
from flask_login import login_required
from local_secrets import (
    get_secrets_manager,
    get_local_secret,
    has_local_secret
)

logger = logging.getLogger(__name__)

secrets_bp = Blueprint('local_secrets', __name__, url_prefix='/api/local-secrets')


# =============================================================================
# Authentication Decorator (optional - add your auth logic)
# =============================================================================

def require_auth(f):
    """
    Optional authentication decorator.
    Replace with your actual authentication logic.
    """
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated


# =============================================================================
# Validation Helpers
# =============================================================================

def validate_secret_name(name: str) -> tuple:
    """
    Validate a secret name.
    
    Returns:
        (is_valid, error_message)
    """
    if not name:
        return False, "Secret name is required"
    
    name = name.strip().upper()
    
    if len(name) > 100:
        return False, "Secret name must be 100 characters or less"
    
    if not re.match(r'^[A-Z][A-Z0-9_]*$', name):
        return False, "Secret name must start with a letter and contain only letters, numbers, and underscores"
    
    # Reserved names
    reserved = ['PATH', 'HOME', 'USER', 'SHELL', 'PWD', 'TEMP', 'TMP']
    if name in reserved:
        return False, f"'{name}' is a reserved name"
    
    return True, None


# =============================================================================
# API Routes
# =============================================================================

@secrets_bp.route('', methods=['GET'])
@require_auth
def list_secrets():
    """
    List all secrets (metadata only, values are never returned via API).
    
    Query params:
        category: Filter by category (optional)
    
    Returns:
        {
            "success": true,
            "secrets": [...],
            "storage_info": {...}
        }
    """
    try:
        manager = get_secrets_manager()
        category = request.args.get('category')
        
        secrets = manager.list(category=category, include_value=False)
        storage_info = manager.get_storage_info()
        
        return jsonify({
            'success': True,
            'secrets': secrets,
            'count': len(secrets),
            'storage_info': storage_info
        })
    except Exception as e:
        logger.error(f"Error listing secrets: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@secrets_bp.route('', methods=['POST'])
@require_auth
def add_secret():
    """
    Add or update a secret.
    
    Request body:
        {
            "name": "OPENWEATHERMAP_API_KEY",
            "value": "your-api-key",
            "description": "OpenWeatherMap API key for weather tool",
            "category": "api_keys"
        }
    
    Returns:
        {
            "success": true,
            "message": "Secret saved locally",
            "name": "OPENWEATHERMAP_API_KEY"
        }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': 'Request body is required'
            }), 400
        
        name = data.get('name', '').strip().upper()
        value = data.get('value', '')
        description = data.get('description', '')
        category = data.get('category', 'api_keys')
        
        # Validate name
        is_valid, error = validate_secret_name(name)
        if not is_valid:
            return jsonify({
                'success': False,
                'error': error
            }), 400
        
        # Validate value
        if not value:
            return jsonify({
                'success': False,
                'error': 'Secret value is required'
            }), 400
        
        if len(value) > 10000:
            return jsonify({
                'success': False,
                'error': 'Secret value must be 10,000 characters or less'
            }), 400
        
        # Check if updating existing
        manager = get_secrets_manager()
        is_update = manager.exists(name)
        
        # Save the secret
        manager.set(name, value, description, category)
        
        return jsonify({
            'success': True,
            'message': f"Secret '{name}' {'updated' if is_update else 'saved'} locally",
            'name': name,
            'is_update': is_update,
            'note': 'This secret is stored only on your local machine and is never transmitted to the cloud.'
        })
        
    except Exception as e:
        logger.error(f"Error saving secret: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@secrets_bp.route('/<name>', methods=['GET'])
@require_auth
def get_secret_info(name):
    """
    Get secret metadata (does NOT return the actual value for security).
    
    Returns:
        {
            "success": true,
            "secret": {
                "name": "...",
                "description": "...",
                "category": "...",
                "exists": true/false
            }
        }
    """
    try:
        name = name.strip().upper()
        manager = get_secrets_manager()
        
        secrets = manager.list()
        secret_data = next((s for s in secrets if s['name'] == name), None)
        
        if secret_data:
            return jsonify({
                'success': True,
                'secret': secret_data
            })
        else:
            return jsonify({
                'success': True,
                'secret': {
                    'name': name,
                    'exists': False
                }
            })
            
    except Exception as e:
        logger.error(f"Error getting secret info: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@secrets_bp.route('/<name>', methods=['DELETE'])
@require_auth
def delete_secret(name):
    """
    Delete a secret.
    
    Returns:
        {
            "success": true,
            "message": "Secret deleted"
        }
    """
    try:
        name = name.strip().upper()
        manager = get_secrets_manager()
        
        if manager.delete(name):
            return jsonify({
                'success': True,
                'message': f"Secret '{name}' deleted",
                'name': name
            })
        else:
            return jsonify({
                'success': False,
                'error': f"Secret '{name}' not found"
            }), 404
            
    except Exception as e:
        logger.error(f"Error deleting secret: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@secrets_bp.route('/<name>/verify', methods=['POST'])
@require_auth
def verify_secret(name):
    """
    Verify that a secret exists and has a value (without revealing the value).
    
    Returns:
        {
            "success": true,
            "name": "...",
            "exists": true/false,
            "has_value": true/false
        }
    """
    try:
        name = name.strip().upper()
        manager = get_secrets_manager()
        
        exists = manager.exists(name)
        
        return jsonify({
            'success': True,
            'name': name,
            'exists': exists,
            'has_value': exists
        })
        
    except Exception as e:
        logger.error(f"Error verifying secret: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@secrets_bp.route('/info', methods=['GET'])
@require_auth
def get_storage_info():
    """
    Get information about secrets storage location and status.
    
    Returns:
        {
            "success": true,
            "info": {
                "location": "/path/to/secrets",
                "encrypted": true,
                "cloud_sync": false,
                ...
            }
        }
    """
    try:
        manager = get_secrets_manager()
        info = manager.get_storage_info()
        
        return jsonify({
            'success': True,
            'info': info
        })
        
    except Exception as e:
        logger.error(f"Error getting storage info: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@secrets_bp.route('/categories', methods=['GET'])
@require_auth
def get_categories():
    """
    Get list of all categories in use.
    
    Returns:
        {
            "success": true,
            "categories": ["api_keys", "credentials", ...]
        }
    """
    try:
        manager = get_secrets_manager()
        categories = manager.get_categories()
        
        # Add default categories if not present
        default_categories = ['api_keys', 'credentials', 'database', 'other']
        all_categories = list(set(categories + default_categories))
        
        return jsonify({
            'success': True,
            'categories': sorted(all_categories)
        })
        
    except Exception as e:
        logger.error(f"Error getting categories: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@secrets_bp.route('/export', methods=['POST'])
@require_auth
def export_template():
    """
    Export a template of secret names (without values) for sharing setup requirements.
    
    Returns:
        {
            "success": true,
            "template": {
                "SECRET_NAME": {"description": "...", "category": "...", "value": ""}
            }
        }
    """
    try:
        manager = get_secrets_manager()
        template = manager.export_template()
        
        return jsonify({
            'success': True,
            'template': template,
            'note': 'This template contains secret names and descriptions only. Values must be filled in by the recipient.'
        })
        
    except Exception as e:
        logger.error(f"Error exporting template: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@secrets_bp.route('/import', methods=['POST'])
@require_auth
def import_secrets():
    """
    Import secrets from a dict.
    
    Request body:
        {
            "secrets": {
                "SECRET_NAME": {"value": "...", "description": "...", "category": "..."}
            },
            "overwrite": false
        }
    
    Returns:
        {
            "success": true,
            "imported": 3,
            "skipped": 1
        }
    """
    try:
        data = request.get_json()
        
        if not data or 'secrets' not in data:
            return jsonify({
                'success': False,
                'error': 'secrets field is required'
            }), 400
        
        secrets_dict = data['secrets']
        overwrite = data.get('overwrite', False)
        
        manager = get_secrets_manager()
        
        imported = 0
        skipped = 0
        errors = []
        
        for name, secret_data in secrets_dict.items():
            try:
                # Validate name
                is_valid, error = validate_secret_name(name)
                if not is_valid:
                    errors.append(f"{name}: {error}")
                    skipped += 1
                    continue
                
                # Check if exists and overwrite is False
                if manager.exists(name) and not overwrite:
                    skipped += 1
                    continue
                
                # Get value
                if isinstance(secret_data, str):
                    value = secret_data
                    description = ''
                    category = 'api_keys'
                elif isinstance(secret_data, dict):
                    value = secret_data.get('value', '')
                    description = secret_data.get('description', '')
                    category = secret_data.get('category', 'api_keys')
                else:
                    skipped += 1
                    continue
                
                if value:
                    manager.set(name, value, description, category)
                    imported += 1
                else:
                    skipped += 1
                    
            except Exception as e:
                errors.append(f"{name}: {str(e)}")
                skipped += 1
        
        return jsonify({
            'success': True,
            'imported': imported,
            'skipped': skipped,
            'errors': errors if errors else None
        })
        
    except Exception as e:
        logger.error(f"Error importing secrets: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@secrets_bp.route('/test/<name>', methods=['POST'])
@require_auth
def test_secret(name):
    """
    Test a secret by making a simple API call (for supported secret types).
    
    Currently supports:
        - OPENWEATHERMAP_API_KEY
        - SENDGRID_API_KEY
        
    Returns:
        {
            "success": true,
            "valid": true/false,
            "message": "API key is valid"
        }
    """
    try:
        name = name.strip().upper()
        manager = get_secrets_manager()
        
        if not manager.exists(name):
            return jsonify({
                'success': False,
                'error': f"Secret '{name}' not found"
            }), 404
        
        value = manager.get(name)
        
        # Test based on secret type
        if name == 'OPENWEATHERMAP_API_KEY':
            result = _test_openweathermap_key(value)
        elif name == 'SENDGRID_API_KEY':
            result = _test_sendgrid_key(value)
        else:
            return jsonify({
                'success': True,
                'valid': None,
                'message': f"No test available for '{name}'. Secret exists and has a value."
            })
        
        return jsonify({
            'success': True,
            **result
        })
        
    except Exception as e:
        logger.error(f"Error testing secret: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# =============================================================================
# Secret Testing Helpers
# =============================================================================

def _test_openweathermap_key(api_key: str) -> dict:
    """Test OpenWeatherMap API key validity."""
    try:
        import requests
        
        response = requests.get(
            'https://api.openweathermap.org/data/2.5/weather',
            params={'q': 'London', 'appid': api_key},
            timeout=10
        )
        
        if response.status_code == 200:
            return {'valid': True, 'message': 'API key is valid'}
        elif response.status_code == 401:
            return {'valid': False, 'message': 'Invalid API key'}
        else:
            return {'valid': False, 'message': f'API returned status {response.status_code}'}
            
    except requests.exceptions.Timeout:
        return {'valid': None, 'message': 'Request timed out - could not verify'}
    except Exception as e:
        return {'valid': None, 'message': f'Could not verify: {str(e)}'}


def _test_sendgrid_key(api_key: str) -> dict:
    """Test SendGrid API key validity."""
    try:
        import requests
        
        response = requests.get(
            'https://api.sendgrid.com/v3/user/profile',
            headers={'Authorization': f'Bearer {api_key}'},
            timeout=10
        )
        
        if response.status_code == 200:
            return {'valid': True, 'message': 'API key is valid'}
        elif response.status_code == 401:
            return {'valid': False, 'message': 'Invalid API key'}
        else:
            return {'valid': False, 'message': f'API returned status {response.status_code}'}
            
    except requests.exceptions.Timeout:
        return {'valid': None, 'message': 'Request timed out - could not verify'}
    except Exception as e:
        return {'valid': None, 'message': f'Could not verify: {str(e)}'}
