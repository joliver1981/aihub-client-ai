"""
Initial Setup Routes for AI Hub

Provides the setup wizard for first-time configuration.
This replaces the default admin credentials with user-chosen ones.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_user
import logging
import re

from initial_setup import (
    needs_initial_setup,
    complete_initial_setup,
    get_setup_status,
    get_default_admin_info,
    DEFAULT_ADMIN_USERNAME
)

logger = logging.getLogger(__name__)

initial_setup_bp = Blueprint('initial_setup', __name__)


def validate_password(password: str) -> tuple[bool, str]:
    """
    Validate password meets security requirements.
    
    Returns:
        (is_valid, error_message)
    """
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least one uppercase letter"
    
    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least one lowercase letter"
    
    if not re.search(r'\d', password):
        return False, "Password must contain at least one number"
    
    return True, ""


def validate_username(username: str) -> tuple[bool, str]:
    """
    Validate username meets requirements.
    
    Returns:
        (is_valid, error_message)
    """
    if len(username) < 3:
        return False, "Username must be at least 3 characters long"
    
    if len(username) > 50:
        return False, "Username must be less than 50 characters"
    
    if not re.match(r'^[a-zA-Z0-9_.-]+$', username):
        return False, "Username can only contain letters, numbers, underscores, dots, and hyphens"
    
    return True, ""


@initial_setup_bp.route('/setup', methods=['GET'])
def setup_page():
    """
    Display the initial setup wizard.
    
    If setup is already complete, redirects to login.
    """
    if not needs_initial_setup():
        return redirect(url_for('login'))
    
    return render_template('initial_setup.html')


@initial_setup_bp.route('/setup', methods=['POST'])
def process_setup():
    """
    Process the initial setup form submission.
    
    Either updates the existing default admin account or creates a new admin user.
    """
    if not needs_initial_setup():
        return jsonify({
            'success': False,
            'error': 'Setup has already been completed'
        }), 400
    
    try:
        data = request.get_json()
        
        username = data.get('username', '').strip()
        email = data.get('email', '').strip()
        password = data.get('password', '')
        confirm_password = data.get('confirm_password', '')
        name = data.get('name', '').strip() or username  # Default name to username
        
        # Validate username
        is_valid, error = validate_username(username)
        if not is_valid:
            return jsonify({'success': False, 'error': error}), 400
        
        # Validate password
        is_valid, error = validate_password(password)
        if not is_valid:
            return jsonify({'success': False, 'error': error}), 400
        
        # Check passwords match
        if password != confirm_password:
            return jsonify({
                'success': False,
                'error': 'Passwords do not match'
            }), 400
        
        # Validate email if provided
        if email and not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
            return jsonify({
                'success': False,
                'error': 'Please enter a valid email address'
            }), 400
        
        # Import here to avoid circular imports
        from DataUtils import Get_Users, Add_User
        from app import hash_the_password
        
        # Check existing users
        existing_users = Get_Users()
        
        # Hash the password
        hashed_password = hash_the_password(password)
        
        # Determine if we're updating the default admin or creating new
        default_admin_exists = False
        default_admin_id = None
        
        if existing_users is not None and not existing_users.empty:
            # Check if default admin exists (by username only - IDs are unpredictable in shared tenant DB)
            for idx, user in existing_users.iterrows():
                user_name = user.get('user_name', '').lower()
                user_id = user.get('id', 0)
                
                if user_name == DEFAULT_ADMIN_USERNAME.lower():
                    default_admin_exists = True
                    default_admin_id = user_id
                    break
            
            # Check if the new username conflicts with a non-default user
            if username.lower() != DEFAULT_ADMIN_USERNAME.lower():
                for idx, user in existing_users.iterrows():
                    existing_username = user.get('user_name', '').lower()
                    existing_id = user.get('id', 0)
                    
                    # Skip the default admin when checking for conflicts
                    if existing_id == default_admin_id:
                        continue
                    
                    if existing_username == username.lower():
                        return jsonify({
                            'success': False,
                            'error': 'This username is already taken'
                        }), 400
        
        if default_admin_exists and default_admin_id:
            # Update the existing default admin account
            logger.info(f"Updating default admin account (ID: {default_admin_id}) with new credentials")
            
            new_id, result = Add_User(
                user_id=default_admin_id,  # Pass existing ID to update
                user_name=username,
                role=3,  # Admin role
                email=email or None,
                phone=None,
                name=name,
                password=hashed_password
            )
            
            if not result:
                logger.error("Failed to update default admin user during initial setup")
                return jsonify({
                    'success': False,
                    'error': 'Failed to update admin account. Please try again.'
                }), 500
            
            new_id = default_admin_id  # Keep the original ID
            
        else:
            # No default admin - create a new admin user
            logger.info("No default admin found - creating new admin account")
            
            new_id, result = Add_User(
                user_id=0,  # 0 means create new
                user_name=username,
                role=3,  # Admin role
                email=email or None,
                phone=None,
                name=name,
                password=hashed_password
            )
            
            if not result:
                logger.error("Failed to create admin user during initial setup")
                return jsonify({
                    'success': False,
                    'error': 'Failed to create admin account. Please try again.'
                }), 500
        
        # Mark setup as complete
        complete_initial_setup(admin_username=username, admin_email=email)
        
        action = "updated" if default_admin_exists else "created"
        logger.info(f"Initial setup completed. Admin user '{username}' {action} with ID {new_id}")
        
        return jsonify({
            'success': True,
            'message': 'Setup complete! Redirecting to login...',
            'redirect': url_for('login')
        })
        
    except Exception as e:
        logger.exception(f"Error during initial setup: {e}")
        return jsonify({
            'success': False,
            'error': 'An unexpected error occurred. Please try again.'
        }), 500


@initial_setup_bp.route('/api/setup/status', methods=['GET'])
def setup_status():
    """
    API endpoint to check setup status.
    Useful for health checks and debugging.
    """
    status = get_setup_status()
    status['needs_setup'] = needs_initial_setup()
    return jsonify(status)
