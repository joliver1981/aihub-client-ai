"""
Role-Based Access Control and API Key Authentication Decorators for AI Hub

Usage:
    # For HTML page routes (redirects on failure):
    @app.route('/users')
    @admin_required()
    def users():
        return render_template('users.html')

    # For API routes (returns JSON 403 on failure):
    @app.route('/get/users')
    @admin_required(api=True)
    def get_users():
        ...

    # Developer+ access for pages:
    @app.route('/monitoring')
    @developer_required()
    def monitoring():
        return render_template('monitoring.html')

    # Developer+ access for APIs:
    @app.route('/get/connections')
    @developer_required(api=True)
    def get_connections():
        ...

    # API Key authentication (for external integrations):
    @app.route('/api/workflow/<int:workflow_id>/trigger', methods=['POST'])
    @api_key_required()
    def trigger_workflow(workflow_id):
        # g.api_key_context contains key info
        ...

    # Internal service-to-service authentication (machine-bound):
    @app.route('/api/internal/sync', methods=['POST'])
    @internal_api_key_required()
    def internal_sync():
        ...

    # Combined: API key OR session auth:
    @app.route('/api/workflow/run', methods=['POST'])
    @api_key_or_session_required()
    def run_workflow():
        ...

Role Levels:
    1 = User (basic access)
    2 = Developer (connections, monitoring, logs)
    3 = Admin (user management, groups, permissions)
"""

import os
import hashlib
import base64
import uuid
import logging
from functools import wraps
from pathlib import Path
from flask import jsonify, redirect, url_for, flash, request, g
from flask_login import login_required, current_user

logger = logging.getLogger(__name__)


# =============================================================================
# Role-Based Access Control Decorators
# =============================================================================

def admin_required(api=False):
    """
    Decorator that requires admin role (role == 3).
    
    Args:
        api: If True, returns JSON 403 on failure. 
             If False, redirects to home with flash message.
    
    Usage:
        @admin_required()       # For HTML pages
        @admin_required(api=True)  # For API endpoints
    """
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if not hasattr(current_user, 'role') or current_user.role < 3:
                if api:
                    return jsonify({'error': 'Admin access required', 'required_role': 3}), 403
                flash('You do not have permission to access this page. Admin access required.', 'danger')
                return redirect(url_for('home'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def developer_required(api=False):
    """
    Decorator that requires developer role or higher (role >= 2).
    
    Args:
        api: If True, returns JSON 403 on failure.
             If False, redirects to home with flash message.
    
    Usage:
        @developer_required()       # For HTML pages
        @developer_required(api=True)  # For API endpoints
    """
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if not hasattr(current_user, 'role') or current_user.role < 2:
                if api:
                    return jsonify({'error': 'Developer access required', 'required_role': 2}), 403
                flash('You do not have permission to access this page. Developer access required.', 'danger')
                return redirect(url_for('home'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def role_required(min_role, api=False):
    """
    Generic decorator that requires a minimum role level.
    
    Args:
        min_role: Minimum role level required (1=User, 2=Developer, 3=Admin)
        api: If True, returns JSON 403 on failure.
             If False, redirects to home with flash message.
    
    Usage:
        @role_required(2)           # Developer+ for pages
        @role_required(3, api=True) # Admin only for APIs
    """
    role_names = {1: 'User', 2: 'Developer', 3: 'Admin'}
    
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if not hasattr(current_user, 'role') or current_user.role < min_role:
                required_name = role_names.get(min_role, f'Role {min_role}')
                if api:
                    return jsonify({
                        'error': f'{required_name} access required',
                        'required_role': min_role
                    }), 403
                flash(f'You do not have permission to access this page. {required_name} access required.', 'danger')
                return redirect(url_for('home'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# =============================================================================
# API Key Authentication
# =============================================================================

# App-specific salt for internal key derivation
_INTERNAL_KEY_SALT = b'aihub_internal_api_v1_2026'


def _get_machine_id_path() -> str:
    """Get the resolved path to the machine_id file (for diagnostics)."""
    if os.getenv('AIHUB_DATA_DIR'):
        data_dir = Path(os.getenv('AIHUB_DATA_DIR'))
    elif os.getenv('APP_ROOT'):
        data_dir = Path(os.getenv('APP_ROOT')) / 'data'
    else:
        data_dir = Path(__file__).parent / 'data'
    return str((data_dir / 'secrets' / '.machine_id').resolve())


def _get_machine_id() -> str:
    """
    Get the machine-specific ID used for internal API key generation.

    This matches the approach used in local_secrets.py for consistency.
    The machine ID is stored in the secrets directory and is unique per installation.
    """
    if os.getenv('AIHUB_DATA_DIR'):
        data_dir = Path(os.getenv('AIHUB_DATA_DIR'))
    elif os.getenv('APP_ROOT'):
        data_dir = Path(os.getenv('APP_ROOT')) / 'data'
    else:
        # Use __file__ location instead of CWD for reliable resolution
        # This ensures the same machine_id file is found regardless of
        # which directory the process was started from (important for
        # Windows services via NSSM where CWD may be system32).
        data_dir = Path(__file__).parent / 'data'
    secrets_dir = data_dir / 'secrets'
    machine_id_file = secrets_dir / '.machine_id'
    
    if machine_id_file.exists():
        return machine_id_file.read_text().strip()
    
    # Generate new machine ID if doesn't exist
    # This should already exist if local_secrets has been used
    unique_parts = [
        str(uuid.uuid4()),
        str(uuid.getnode()),  # MAC address based
        os.name,
    ]
    machine_id = hashlib.sha256('|'.join(unique_parts).encode()).hexdigest()[:32]
    
    # Ensure directory exists
    secrets_dir.mkdir(parents=True, exist_ok=True)
    machine_id_file.write_text(machine_id)
    
    return machine_id


def get_internal_api_key() -> str:
    """
    Generate the internal API key for this machine.
    
    This key is deterministic based on the machine ID and can be used for:
    - Service-to-service communication within the same installation
    - Scheduler to API authentication
    - Agent API to main app authentication
    
    The key is derived from:
    - Machine ID (unique per installation)
    - App salt (unique to AI Hub)
    - Tenant API key (unique per customer)
    
    Returns:
        str: The internal API key (64 character hex string)
    """
    machine_id = _get_machine_id()
    tenant_key = os.getenv('API_KEY', '')
    
    # Combine machine ID + tenant key + salt for a unique internal key
    key_material = f"{machine_id}:{tenant_key}".encode()
    
    # Use PBKDF2-like derivation for the internal key
    derived = hashlib.pbkdf2_hmac(
        'sha256',
        key_material,
        _INTERNAL_KEY_SALT,
        iterations=10000
    )
    
    return derived.hex()


# Log internal key diagnostics at module load time
try:
    _startup_internal_key = get_internal_api_key()
    _startup_machine_id = _get_machine_id()
    _startup_tenant_key = os.getenv('API_KEY', '')
    _startup_machine_id_path = _get_machine_id_path()
    _startup_tenant_prefix = f"{_startup_tenant_key[:8]}..." if _startup_tenant_key else "(empty)"
    logger.info(
        f"[role_decorators] Internal API key diagnostics at startup: "
        f"key_prefix={_startup_internal_key[:12]}..., "
        f"machine_id={_startup_machine_id[:12]}..., "
        f"tenant_key_set={bool(_startup_tenant_key)}, "
        f"tenant_key_prefix={_startup_tenant_prefix}, "
        f"machine_id_file={_startup_machine_id_path}"
    )
except Exception as _diag_err:
    logger.warning(f"[role_decorators] Could not log internal key diagnostics: {_diag_err}")


def _get_api_key_from_request() -> str:
    """
    Extract API key from the current request.
    
    Checks in order:
    1. Authorization: Bearer <key>
    2. X-API-Key header
    3. X-Internal-API-Key header (for internal service calls)
    4. api_key query parameter
    5. api_key in JSON body
    
    Returns:
        str: The API key or None if not found
    """
    # Priority 1: Authorization header (Bearer token)
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        return auth_header[7:]
    
    # Priority 2: X-API-Key header
    api_key = request.headers.get('X-API-Key')
    if api_key:
        return api_key
    
    # Priority 3: X-Internal-API-Key header
    api_key = request.headers.get('X-Internal-API-Key')
    if api_key:
        return api_key
    
    # Priority 4: Query parameter
    api_key = request.args.get('api_key')
    if api_key:
        return api_key
    
    # Priority 5: Request body (for backwards compatibility)
    if request.is_json:
        data = request.get_json(silent=True)
        if data:
            return data.get('api_key')
    
    return None


def _validate_tenant_api_key(api_key: str) -> dict:
    """
    Validate a tenant/license API key.
    
    Args:
        api_key: The API key to validate
        
    Returns:
        dict with validation result:
        {
            'valid': True/False,
            'source': 'tenant',
            'tenant_id': str,
            'permissions': ['workflows', 'agents', ...]
        }
    """
    tenant_api_key = os.getenv('API_KEY', '')
    
    if not tenant_api_key or api_key != tenant_api_key:
        return {'valid': False}
    
    return {
        'valid': True,
        'source': 'tenant',
        'tenant_id': os.getenv('TENANT_ID', ''),
        'permissions': ['workflows', 'agents', 'documents', 'scheduler', 'integrations']
    }


def _validate_internal_api_key(api_key: str) -> dict:
    """
    Validate an internal machine-bound API key.

    Args:
        api_key: The API key to validate

    Returns:
        dict with validation result
    """
    expected_key = get_internal_api_key()

    if api_key != expected_key:
        # Log mismatch for debugging
        logger.debug(f"[_validate_internal_api_key] Key mismatch - provided: {api_key[:16]}..., expected: {expected_key[:16]}...")
        return {'valid': False}

    return {
        'valid': True,
        'source': 'internal',
        'tenant_id': os.getenv('TENANT_ID', ''),
        'permissions': ['workflows', 'agents', 'documents', 'scheduler', 'integrations', 'internal']
    }


def validate_api_key(api_key: str) -> dict:
    """
    Validate an API key and return context information.
    
    Checks in order:
    1. Tenant/License API key (API_KEY env var)
    2. Internal machine-bound API key
    3. Future: Database-stored API keys for external integrations
    
    Args:
        api_key: The API key to validate
        
    Returns:
        dict with 'valid', 'source', 'permissions' etc. or {'valid': False}
    """
    if not api_key:
        return {'valid': False}
    
    # Check 1: Tenant API key (the license key)
    result = _validate_tenant_api_key(api_key)
    if result.get('valid'):
        return result
    
    # Check 2: Internal machine-bound key
    result = _validate_internal_api_key(api_key)
    if result.get('valid'):
        return result
    
    # Check 3: Future - database lookup for user-generated API keys
    # This could be extended to look up API keys from a database table
    # result = _validate_database_api_key(api_key)
    # if result.get('valid'):
    #     return result
    
    return {'valid': False}


# =============================================================================
# API Key Decorators
# =============================================================================

def api_key_required(permissions: list = None):
    """
    Decorator to require API key authentication.
    
    Accepts keys via:
    - Authorization: Bearer <key>
    - X-API-Key header
    - api_key query parameter
    - api_key in JSON body
    
    On success, sets g.api_key_context with key information.
    
    Args:
        permissions: Optional list of required permissions.
                    If None, any valid key is accepted.
    
    Usage:
        @app.route('/api/workflow/<int:id>/trigger', methods=['POST'])
        @api_key_required()
        def trigger_workflow(id):
            initiator = g.api_key_context.get('source', 'api')
            ...
        
        @app.route('/api/admin/keys', methods=['POST'])
        @api_key_required(permissions=['admin'])
        def create_api_key():
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            api_key = _get_api_key_from_request()
            
            if not api_key:
                return jsonify({
                    'status': 'error',
                    'message': 'API key required',
                    'hint': 'Provide via X-API-Key header, Authorization: Bearer <key>, or api_key parameter'
                }), 401
            
            key_context = validate_api_key(api_key)
            
            if not key_context.get('valid'):
                logger.warning(f"Invalid API key attempt: {api_key[:8]}..." if len(api_key) > 8 else "Invalid API key attempt")
                return jsonify({
                    'status': 'error',
                    'message': 'Invalid API key'
                }), 401
            
            # Check permissions if specified
            if permissions:
                key_permissions = key_context.get('permissions', [])
                missing = [p for p in permissions if p not in key_permissions]
                if missing:
                    return jsonify({
                        'status': 'error',
                        'message': f'Insufficient permissions. Missing: {missing}'
                    }), 403
            
            # Store context for route handler
            g.api_key_context = key_context
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def internal_api_key_required():
    """
    Decorator to require internal (machine-bound) API key authentication.
    
    This is stricter than api_key_required() - it ONLY accepts the internal
    machine-derived key, not the tenant API key.
    
    Use this for sensitive internal endpoints that should only be callable
    from services running on the same machine.
    
    The internal key is derived from:
    - Machine ID (unique per installation)
    - Tenant API key
    - App-specific salt
    
    Usage:
        @app.route('/api/internal/sync', methods=['POST'])
        @internal_api_key_required()
        def internal_sync():
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Check X-Internal-API-Key header first, then fall back to standard extraction
            api_key = request.headers.get('X-Internal-API-Key')
            if not api_key:
                api_key = _get_api_key_from_request()
            
            if not api_key:
                return jsonify({
                    'status': 'error',
                    'message': 'Internal API key required',
                    'hint': 'Provide via X-Internal-API-Key header'
                }), 401
            
            # Only validate as internal key
            key_context = _validate_internal_api_key(api_key)
            
            if not key_context.get('valid'):
                logger.warning("Invalid internal API key attempt")
                return jsonify({
                    'status': 'error',
                    'message': 'Invalid internal API key'
                }), 401
            
            g.api_key_context = key_context
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def api_key_or_session_required(permissions: list = None, min_role: int = None):
    """
    Decorator that accepts either API key OR session authentication.

    This is useful for endpoints that need to work both:
    - From the UI (session-based auth via flask-login)
    - From external systems (API key auth)

    On success, sets:
    - g.auth_method = 'api_key' or 'session'
    - g.api_key_context (if API key auth)

    Args:
        permissions: Optional list of required permissions for API key auth.
                    Session auth uses role-based checks instead.
        min_role: Optional minimum role level for session-based auth
                  (1=User, 2=Developer, 3=Admin). API key auth is trusted
                  (internal service) and bypasses role checks.

    Usage:
        @app.route('/api/workflow/run', methods=['POST'])
        @api_key_or_session_required()
        def run_workflow():
            if g.auth_method == 'api_key':
                initiator = f"api:{g.api_key_context.get('source')}"
            else:
                initiator = current_user.username
            ...

        @app.route('/get/users')
        @api_key_or_session_required(min_role=3)  # Admin for sessions, trusted for API keys
        def get_users():
            ...
    """
    role_names = {1: 'User', 2: 'Developer', 3: 'Admin'}

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # First, check for API key
            api_key = _get_api_key_from_request()

            if api_key:
                key_context = validate_api_key(api_key)

                if key_context.get('valid'):
                    # Check permissions if specified
                    if permissions:
                        key_permissions = key_context.get('permissions', [])
                        missing = [p for p in permissions if p not in key_permissions]
                        if missing:
                            return jsonify({
                                'status': 'error',
                                'message': f'Insufficient permissions. Missing: {missing}'
                            }), 403

                    g.api_key_context = key_context
                    g.auth_method = 'api_key'
                    return f(*args, **kwargs)
                else:
                    # Key was provided but didn't validate — log diagnostics
                    logger.warning(
                        f"[api_key_or_session_required] API key validation FAILED for {request.path}. "
                        f"Key prefix: {api_key[:12]}..., "
                        f"Key length: {len(api_key)}, "
                        f"Expected internal key prefix: {get_internal_api_key()[:12]}..., "
                        f"Tenant key set: {bool(os.getenv('API_KEY', ''))}, "
                        f"Machine ID file: {_get_machine_id_path()}"
                    )

            # Fall back to session auth
            if hasattr(current_user, 'is_authenticated') and current_user.is_authenticated:
                # Check minimum role if specified
                if min_role and (not hasattr(current_user, 'role') or current_user.role < min_role):
                    required_name = role_names.get(min_role, f'Role {min_role}')
                    return jsonify({
                        'error': f'{required_name} access required',
                        'required_role': min_role
                    }), 403

                g.api_key_context = None
                g.auth_method = 'session'
                return f(*args, **kwargs)

            # Neither API key nor session
            logger.debug(f"[api_key_or_session_required] Auth failed for {request.path}")
            return jsonify({
                'status': 'error',
                'message': 'Authentication required',
                'hint': 'Provide API key via X-API-Key header or login via session'
            }), 401

        return decorated_function
    return decorator


# =============================================================================
# Utility Functions
# =============================================================================

def get_current_auth_context() -> dict:
    """
    Get the current authentication context.
    
    Returns:
        dict with auth info:
        {
            'method': 'api_key' | 'session' | None,
            'user_id': int or None,
            'username': str or None,
            'tenant_id': str or None,
            'permissions': list or None,
            'source': str or None
        }
    """
    context = {
        'method': getattr(g, 'auth_method', None),
        'user_id': None,
        'username': None,
        'tenant_id': os.getenv('TENANT_ID', ''),
        'permissions': None,
        'source': None
    }
    
    if context['method'] == 'api_key':
        api_context = getattr(g, 'api_key_context', {})
        context['permissions'] = api_context.get('permissions', [])
        context['source'] = api_context.get('source')
    elif context['method'] == 'session':
        if hasattr(current_user, 'id'):
            context['user_id'] = current_user.id
        if hasattr(current_user, 'username'):
            context['username'] = current_user.username
        if hasattr(current_user, 'role'):
            context['permissions'] = _role_to_permissions(current_user.role)
    
    return context


def _role_to_permissions(role: int) -> list:
    """Convert a role level to permission list."""
    base = ['workflows', 'agents', 'documents']
    if role >= 2:
        base.extend(['scheduler', 'integrations', 'monitoring'])
    if role >= 3:
        base.extend(['admin', 'users', 'settings'])
    return base


def print_internal_api_key():
    """
    Utility function to print the internal API key.
    Useful for debugging or setting up service configurations.
    
    Usage:
        python -c "from role_decorators import print_internal_api_key; print_internal_api_key()"
    """
    key = get_internal_api_key()
    print(f"Internal API Key: {key}")
    print(f"Machine ID: {_get_machine_id()}")
    print(f"Tenant Key: {os.getenv('API_KEY', 'NOT SET')[:8]}...")
