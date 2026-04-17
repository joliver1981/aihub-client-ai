"""
Feedback Routes - In-App Feedback System
=========================================
Provides API endpoints for submitting and managing user feedback.
These routes call the Cloud API for database operations.

Routes:
    POST /api/feedback/submit - Submit new feedback
    GET  /api/feedback/list - List feedback (admin)
    GET  /api/feedback/<id> - Get feedback details
    PUT  /api/feedback/<id> - Update feedback status/response
    GET  /api/feedback/stats - Get feedback statistics
    GET  /api/feedback/my-feedback - Get current user's feedback
"""

from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
import os
import logging
import requests
from functools import wraps

try:
    import config as cfg
    CLOUD_API_TIMEOUT = getattr(cfg, 'CLOUD_API_REQUESTS_TIMEOUT', 30)
except ImportError:
    CLOUD_API_TIMEOUT = 30

# Create Blueprint
feedback_bp = Blueprint('feedback', __name__, url_prefix='/api/feedback')

logger = logging.getLogger(__name__)


def get_cloud_api_url():
    """Get the Cloud API base URL from environment"""
    return os.getenv('AI_HUB_API_URL', '').rstrip('/')


def get_api_key():
    """Get the API/License key from environment"""
    return os.getenv('API_KEY', '')


def get_app_version():
    """Get the application version"""
    try:
        from CommonUtils import get_app_version as common_get_version
        return common_get_version()
    except:
        return os.getenv('APP_VERSION', 'unknown')


def admin_required(f):
    """Decorator to require admin role for certain endpoints"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({'success': False, 'error': 'Authentication required'}), 401
        if not getattr(current_user, 'is_admin', False):
            return jsonify({'success': False, 'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated_function


def call_cloud_api(method, endpoint, data=None, params=None):
    """
    Make a request to the Cloud API.
    
    Args:
        method: HTTP method (GET, POST, PUT, DELETE)
        endpoint: API endpoint (e.g., '/feedback/submit')
        data: JSON body for POST/PUT requests
        params: Query parameters
    
    Returns:
        tuple: (response_dict, status_code)
    """
    api_url = get_cloud_api_url()
    api_key = get_api_key()
    
    if not api_url:
        logger.error("AI_HUB_API_URL not configured")
        return {'success': False, 'error': 'Cloud API not configured'}, 500
    
    if not api_key:
        logger.error("API_KEY not configured")
        return {'success': False, 'error': 'API key not configured'}, 500
    
    # Build full URL
    url = f"{api_url}{endpoint}"
    
    # Authenticate via header (not query string)
    headers = {
        'X-API-Key': api_key,
        'Content-Type': 'application/json'
    }

    try:
        # Make request with timeout
        if method.upper() == 'GET':
            response = requests.get(url, headers=headers, params=params, timeout=CLOUD_API_TIMEOUT)
        elif method.upper() == 'POST':
            response = requests.post(url, headers=headers, json=data, params=params, timeout=CLOUD_API_TIMEOUT)
        elif method.upper() == 'PUT':
            response = requests.put(url, headers=headers, json=data, params=params, timeout=CLOUD_API_TIMEOUT)
        elif method.upper() == 'DELETE':
            response = requests.delete(url, headers=headers, params=params, timeout=CLOUD_API_TIMEOUT)
        else:
            return {'success': False, 'error': f'Unsupported HTTP method: {method}'}, 400
        
        # Parse response
        try:
            result = response.json()
        except:
            result = {'success': False, 'error': 'Invalid response from Cloud API'}
        
        return result, response.status_code
        
    except requests.exceptions.Timeout:
        logger.error(f"Cloud API timeout: {endpoint}")
        return {'success': False, 'error': 'Request timed out. Please try again.'}, 504
        
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Cloud API connection error: {endpoint} - {str(e)}")
        return {'success': False, 'error': 'Could not connect to server. Please try again.'}, 503
        
    except Exception as e:
        logger.error(f"Cloud API error: {endpoint} - {str(e)}")
        return {'success': False, 'error': 'An error occurred. Please try again.'}, 500


@feedback_bp.route('/submit', methods=['POST'])
@login_required
def submit_feedback():
    """
    Submit new user feedback.
    
    Expected JSON body:
    {
        "feedback_type": "bug" | "feature" | "question" | "general",
        "subject": "Brief subject line (optional)",
        "description": "Detailed description",
        "priority": "low" | "medium" | "high" | "critical" (optional),
        "page_url": "/current/page/url",
        "page_title": "Current Page Title",
        "include_diagnostics": true | false,
        "browser_info": "Browser user agent string",
        "screen_resolution": "1920x1080",
        "recent_errors": ["error1", "error2"] (optional),
        "console_logs": "Recent console output" (optional)
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        # Add user context
        data['user_id'] = current_user.id
        data['app_version'] = get_app_version()
        data['user_tier'] = getattr(current_user, 'tier', None) or getattr(current_user, 'user_tier', None)
        
        # Call Cloud API
        result, status_code = call_cloud_api('POST', '/feedback/submit', data=data)
        
        if result.get('success'):
            logger.info(f"Feedback submitted: {result.get('feedback_id')} by user {current_user.id}")
        
        return jsonify(result), status_code
        
    except Exception as e:
        logger.error(f"Error submitting feedback: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'An error occurred while submitting your feedback. Please try again.'
        }), 500


@feedback_bp.route('/list', methods=['GET'])
@login_required
@admin_required
def list_feedback():
    """
    List feedback submissions (admin only).
    
    Query parameters:
        status: Filter by status (new, reviewed, in_progress, resolved, closed)
        type: Filter by feedback type (bug, feature, question, general)
        priority: Filter by priority (low, medium, high, critical)
        days: Filter by last N days (default: 30)
        page: Page number (default: 1)
        per_page: Items per page (default: 20, max: 100)
    """
    try:
        # Forward query parameters
        params = {
            'status': request.args.get('status', ''),
            'type': request.args.get('type', ''),
            'priority': request.args.get('priority', ''),
            'days': request.args.get('days', '30'),
            'page': request.args.get('page', '1'),
            'per_page': request.args.get('per_page', '20')
        }
        
        # Remove empty params
        params = {k: v for k, v in params.items() if v}
        
        result, status_code = call_cloud_api('GET', '/feedback/list', params=params)
        return jsonify(result), status_code
        
    except Exception as e:
        logger.error(f"Error listing feedback: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to retrieve feedback list'}), 500


@feedback_bp.route('/<feedback_id>', methods=['GET'])
@login_required
@admin_required
def get_feedback(feedback_id):
    """Get detailed feedback information (admin only)."""
    try:
        result, status_code = call_cloud_api('GET', f'/feedback/{feedback_id}')
        return jsonify(result), status_code
        
    except Exception as e:
        logger.error(f"Error getting feedback {feedback_id}: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to retrieve feedback'}), 500


@feedback_bp.route('/<feedback_id>', methods=['PUT'])
@login_required
@admin_required
def update_feedback(feedback_id):
    """
    Update feedback status and admin response (admin only).
    
    Expected JSON body:
    {
        "status": "new" | "reviewed" | "in_progress" | "resolved" | "closed",
        "priority": "low" | "medium" | "high" | "critical",
        "admin_notes": "Internal notes",
        "admin_response": "Response to send to user (optional)"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        # Add responder info
        data['responded_by_user_id'] = current_user.id
        
        result, status_code = call_cloud_api('PUT', f'/feedback/{feedback_id}', data=data)
        
        if result.get('success'):
            logger.info(f"Feedback updated: {feedback_id} by admin {current_user.id}")
        
        return jsonify(result), status_code
        
    except Exception as e:
        logger.error(f"Error updating feedback {feedback_id}: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to update feedback'}), 500


@feedback_bp.route('/stats', methods=['GET'])
@login_required
@admin_required
def get_feedback_stats():
    """Get feedback statistics for dashboard (admin only)."""
    try:
        params = {
            'days': request.args.get('days', '30')
        }
        
        result, status_code = call_cloud_api('GET', '/feedback/stats', params=params)
        return jsonify(result), status_code
        
    except Exception as e:
        logger.error(f"Error getting feedback stats: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to retrieve feedback statistics'}), 500


@feedback_bp.route('/my-feedback', methods=['GET'])
@login_required
def get_my_feedback():
    """Get current user's feedback history."""
    try:
        params = {
            'page': request.args.get('page', '1'),
            'per_page': request.args.get('per_page', '10')
        }
        
        result, status_code = call_cloud_api('GET', f'/feedback/user/{current_user.id}', params=params)
        return jsonify(result), status_code
        
    except Exception as e:
        logger.error(f"Error getting user feedback: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to retrieve your feedback history'}), 500


def register_feedback_routes(app):
    """Register feedback blueprint with the Flask app."""
    app.register_blueprint(feedback_bp)
    logger.info("Feedback routes registered")
