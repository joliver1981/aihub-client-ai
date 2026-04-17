"""
Authentication Middleware for AI Hub

Global authentication enforcement for all routes.
Ensures all endpoints require login unless explicitly whitelisted.

Usage:
    from auth_middleware import init_auth_middleware
    init_auth_middleware(app)
"""

import os
import logging
from logging.handlers import RotatingFileHandler
from functools import wraps

from flask import request, redirect, url_for, jsonify
from flask_login import current_user


# =============================================================================
# Auth Middleware Logger Setup
# =============================================================================

def setup_auth_logger():
    """
    Create a dedicated logger for authentication middleware.
    Logs to a separate file for easy monitoring of auth events.
    """
    auth_logger = logging.getLogger('auth_middleware')
    auth_logger.setLevel(logging.INFO)
    
    # Prevent duplicate handlers if called multiple times
    if auth_logger.handlers:
        return auth_logger
    
    # Create logs directory if it doesn't exist
    log_dir = os.getenv('LOG_DIR_AUTH', './logs')
    if log_dir.endswith('.txt') or log_dir.endswith('.log'):
        log_dir = os.path.dirname(log_dir)
    
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, 'auth_middleware_log.txt')
    
    # Rotating file handler - 5MB max, keep 3 backups
    handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding='utf-8'
    )
    
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    )
    handler.setFormatter(formatter)
    auth_logger.addHandler(handler)
    
    # Also log to console for visibility during development
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.WARNING)  # Only warnings+ to console
    auth_logger.addHandler(console_handler)
    
    return auth_logger


# Initialize logger
auth_logger = setup_auth_logger()


# =============================================================================
# Configuration
# =============================================================================

# Endpoints that don't require authentication
UNPROTECTED_ENDPOINTS = {
    # === Authentication Flow ===
    'login',
    'logout',
    
    # === Public Pages ===
    'home',
    'index',
    'landing',
    
    # === Static Files ===
    'static',
    'environments.static',
    
    # === Initial Setup (First-Run Wizard) ===
    'initial_setup.setup_page',
    'initial_setup.process_setup',
    'initial_setup.setup_status',
    
    # === Health Check ===
    'api_check',
}

# Endpoints that use alternative authentication (scheduler secrets, localhost-only, etc.)
ALTERNATIVE_AUTH_ENDPOINTS = {
    'execute_document_job_api',  # Scheduler endpoint - uses secret or localhost
}

# Set to True to log blocked requests without actually blocking (for testing)
# Set to False for production enforcement
AUTH_MIDDLEWARE_DRY_RUN = os.getenv('AUTH_MIDDLEWARE_DRY_RUN', 'false').lower() == 'true'


# =============================================================================
# Helper Functions
# =============================================================================

def is_local_request():
    """Check if request originates from localhost"""
    return request.remote_addr in ('127.0.0.1', '::1', 'localhost')


def is_api_request():
    """Determine if this is an API request (expects JSON response)"""
    return (
        request.is_json or
        request.path.startswith('/api/') or
        request.headers.get('Accept') == 'application/json' or
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    )


def check_scheduler_auth():
    """
    Check alternative authentication for scheduler endpoints.
    Returns True if authenticated, False otherwise.
    """
    # Allow localhost requests (scheduler running on same machine)
    if is_local_request():
        auth_logger.debug(f"Scheduler endpoint allowed from localhost: {request.path}")
        return True
    
    # Check for scheduler secret header
    scheduler_secret = request.headers.get('X-Scheduler-Secret')
    expected_secret = os.getenv('SCHEDULER_SECRET')
    
    if expected_secret and scheduler_secret == expected_secret:
        auth_logger.debug(f"Scheduler endpoint authenticated via secret: {request.path}")
        return True
    
    return False


# =============================================================================
# Main Middleware
# =============================================================================

def require_login_middleware():
    """
    Global authentication middleware.
    Ensures all routes require authentication unless explicitly whitelisted.
    
    This function is registered as a before_request handler.
    """
    endpoint = request.endpoint
    
    # Safety check - no endpoint means something unusual
    if not endpoint:
        return
    
    # Allow explicitly whitelisted public endpoints
    if endpoint in UNPROTECTED_ENDPOINTS:
        return
    
    # Handle alternative auth endpoints (scheduler, webhooks, etc.)
    if endpoint in ALTERNATIVE_AUTH_ENDPOINTS:
        if endpoint == 'execute_document_job_api':
            if check_scheduler_auth():
                return
            # Fall through to require normal auth if scheduler auth fails
        else:
            # Other alternative auth endpoints - allow through
            # (they should implement their own security)
            return
    
    # Check if user is authenticated
    if current_user.is_authenticated:
        return
    
    # === User is NOT authenticated and endpoint is NOT whitelisted ===
    
    # Log the blocked request
    log_message = (
        f"Blocked unauthenticated access: {request.method} {request.path} "
        f"(endpoint: {endpoint}, ip: {request.remote_addr})"
    )
    
    if AUTH_MIDDLEWARE_DRY_RUN:
        auth_logger.warning(f"[DRY RUN] {log_message}")
        return  # Don't actually block in dry run mode
    
    auth_logger.warning(log_message)
    
    # Return appropriate response based on request type
    if is_api_request():
        return jsonify({
            'error': 'Authentication required',
            'message': 'Please log in to access this resource',
            'login_url': url_for('login')
        }), 401
    
    # Browser request - redirect to login with return URL
    return redirect(url_for('login', next=request.url))


# =============================================================================
# Initialization
# =============================================================================

def init_auth_middleware(app):
    """
    Initialize the authentication middleware on a Flask app.
    
    Args:
        app: Flask application instance
    
    Usage:
        from auth_middleware import init_auth_middleware
        
        app = Flask(__name__)
        # ... other setup ...
        init_auth_middleware(app)
    """
    # Register the before_request handler
    app.before_request(require_login_middleware)
    
    # Log startup configuration
    mode = "DRY RUN (logging only)" if AUTH_MIDDLEWARE_DRY_RUN else "ENFORCING"
    auth_logger.info(f"Auth middleware initialized - Mode: {mode}")
    auth_logger.info(f"Unprotected endpoints: {len(UNPROTECTED_ENDPOINTS)}")
    auth_logger.info(f"Alternative auth endpoints: {len(ALTERNATIVE_AUTH_ENDPOINTS)}")
    
    print(f"✓ Auth middleware initialized ({mode})")
    print(f"  - {len(UNPROTECTED_ENDPOINTS)} public endpoints")
    print(f"  - {len(ALTERNATIVE_AUTH_ENDPOINTS)} alternative auth endpoints")


# =============================================================================
# Utility: Add endpoint to whitelist at runtime (use sparingly)
# =============================================================================

def add_unprotected_endpoint(endpoint_name: str):
    """
    Add an endpoint to the unprotected list at runtime.
    Use sparingly - prefer adding to UNPROTECTED_ENDPOINTS directly.
    """
    UNPROTECTED_ENDPOINTS.add(endpoint_name)
    auth_logger.info(f"Added endpoint to whitelist: {endpoint_name}")


def add_alternative_auth_endpoint(endpoint_name: str):
    """
    Add an endpoint to the alternative auth list at runtime.
    """
    ALTERNATIVE_AUTH_ENDPOINTS.add(endpoint_name)
    auth_logger.info(f"Added endpoint to alternative auth: {endpoint_name}")
