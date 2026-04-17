from flask import Blueprint, jsonify, request, render_template
from flask_login import login_required, current_user
import logging
import json
import os
from typing import Dict, Any, Optional, Union

# Import your database connection helper
from CommonUtils import get_db_connection

# Create a blueprint
preferences_bp = Blueprint('preferences', __name__, url_prefix='/preferences')

def convert_value_by_type(value: str, data_type: str) -> Any:
    """Convert string value to appropriate type"""
    if value is None:
        return None
        
    if data_type == 'boolean':
        return value.lower() in ('true', 'yes', '1', 'on')
    elif data_type == 'integer':
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0
    elif data_type == 'float':
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0
    elif data_type == 'json':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    else:  # string or any other type
        return value

def get_preference_definitions(active_only: bool = True) -> Dict[str, Any]:
    """Get all defined preferences with defaults and metadata"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get all preference definitions
        query = """
            SELECT preference_key, display_name, description, default_value, 
                   data_type, category, ui_component, ui_options
            FROM PreferenceDefinitions
        """
        
        if active_only:
            query += " WHERE is_active = 1"
            
        cursor.execute(query)
        
        # Process definitions
        definitions = {}
        for row in cursor.fetchall():
            key, display_name, description, default_value, data_type, category, ui_component, ui_options = row
            
            # Parse UI options if present
            options = None
            if ui_options:
                try:
                    options = json.loads(ui_options)
                except json.JSONDecodeError:
                    options = None
            
            # Convert default value based on data type
            typed_default = convert_value_by_type(default_value, data_type)
            
            definitions[key] = {
                'display_name': display_name,
                'description': description,
                'default_value': typed_default,
                'data_type': data_type,
                'category': category,
                'ui_component': ui_component,
                'ui_options': options
            }
            
        cursor.close()
        conn.close()
        
        return definitions
        
    except Exception as e:
        logging.error(f"Error getting preference definitions: {str(e)}")
        return {}

def get_user_preferences(user_id: int) -> Dict[str, Any]:
    """
    Get all preferences for a user with defaults when not set.
    This implementation uses lazy loading - preferences are only created when needed.
    """
    try:
        # Get preference definitions with defaults
        definitions = get_preference_definitions()
        
        # Initialize with defaults
        preferences = {key: info['default_value'] for key, info in definitions.items()}
        
        # Get user's saved preferences
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get current user preferences
        cursor.execute("""
            SELECT up.preference_key, up.preference_value, pd.data_type
            FROM UserPreferences up
            JOIN PreferenceDefinitions pd ON up.preference_key = pd.preference_key
            WHERE up.user_id = ?
        """, user_id)
        
        # Track which preferences already exist for this user
        existing_keys = set()
        
        # Override defaults with user values
        for row in cursor.fetchall():
            key, value, data_type = row
            preferences[key] = convert_value_by_type(value, data_type)
            existing_keys.add(key)
        
        # Determine missing preferences
        missing_keys = set(definitions.keys()) - existing_keys
        
        # If there are missing preferences, insert them with default values
        if missing_keys:
            logging.info(f"Lazy-loading {len(missing_keys)} missing preferences for user {user_id}")
            
            for key in missing_keys:
                default_value = str(definitions[key]['default_value'])
                cursor.execute("""
                    INSERT INTO UserPreferences (user_id, preference_key, preference_value)
                    VALUES (?, ?, ?)
                """, user_id, key, default_value)
            
            conn.commit()
                
        cursor.close()
        conn.close()
        
        return preferences
        
    except Exception as e:
        logging.error(f"Error getting user preferences: {str(e)}")
        return {}

def get_user_preference(user_id: int, preference_key: str, default_value: Any = None) -> Any:
    """
    Get a specific user preference value with lazy loading.
    If the preference doesn't exist for this user, it will be created with the default value.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get preference definition to know the data type
        cursor.execute("""
            SELECT data_type, default_value
            FROM PreferenceDefinitions
            WHERE preference_key = ?
        """, preference_key)
        
        row = cursor.fetchone()
        if not row:
            if default_value is not None:
                return default_value
            return None
            
        data_type, definition_default = row
        
        # If default_value was not provided, use the one from definition
        if default_value is None:
            default_value = convert_value_by_type(definition_default, data_type)
        
        # Get user preference
        cursor.execute("""
            SELECT preference_value
            FROM UserPreferences
            WHERE user_id = ? AND preference_key = ?
        """, user_id, preference_key)
        
        row = cursor.fetchone()
        
        if row:
            # Preference exists, return its value
            value = row[0]
            cursor.close()
            conn.close()
            return convert_value_by_type(value, data_type)
        else:
            # Preference doesn't exist, create it with default value
            cursor.execute("""
                INSERT INTO UserPreferences (user_id, preference_key, preference_value, created_at, updated_at)
                VALUES (?, ?, ?, getutcdate(), getutcdate())
            """, user_id, preference_key, str(default_value))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return default_value
        
    except Exception as e:
        logging.error(f"Error getting user preference: {str(e)}")
        return default_value

# Routes
@preferences_bp.route('/')
@login_required
def preferences_page():
    """Render user preferences page"""
    return render_template('user_preferences.html')

@preferences_bp.route('/api/get', methods=['GET'])
@login_required
def get_user_preferences_api():
    """Get all preferences for the current user"""
    try:
        preferences = get_user_preferences(current_user.id)
        definitions = get_preference_definitions()
        
        return jsonify({
            "status": "success",
            "preferences": preferences,
            "definitions": definitions
        })
        
    except Exception as e:
        logging.error(f"Error getting user preferences: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error: {str(e)}"
        }), 500

@preferences_bp.route('/api/get/<preference_key>', methods=['GET'])
@login_required
def get_single_preference_api(preference_key):
    """Get a single preference value for the current user"""
    try:
        value = get_user_preference(current_user.id, preference_key)
        
        return jsonify({
            "status": "success",
            "key": preference_key,
            "value": value
        })
        
    except Exception as e:
        logging.error(f"Error getting preference: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error: {str(e)}"
        }), 500

@preferences_bp.route('/api/update', methods=['POST'])
@login_required
def update_user_preference_api():
    """Update a single preference for the current user"""
    try:
        data = request.json
        key = data.get('key')
        value = data.get('value')
        
        if not key:
            return jsonify({
                "status": "error",
                "message": "Preference key is required"
            }), 400
            
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Validate that the preference exists
        cursor.execute("""
            SELECT id FROM PreferenceDefinitions
            WHERE preference_key = ?
        """, key)
        
        if cursor.fetchone() is None:
            return jsonify({
                "status": "error",
                "message": f"Preference key '{key}' does not exist"
            }), 400
        
        # Check if preference exists for this user
        cursor.execute("""
            SELECT id FROM UserPreferences
            WHERE user_id = ? AND preference_key = ?
        """, current_user.id, key)
        
        preference_exists = cursor.fetchone() is not None
        
        if preference_exists:
            # Update existing preference
            cursor.execute("""
                UPDATE UserPreferences
                SET preference_value = ?, updated_at = getutcdate()
                WHERE user_id = ? AND preference_key = ?
            """, str(value), current_user.id, key)
        else:
            # Insert new preference
            cursor.execute("""
                INSERT INTO UserPreferences (user_id, preference_key, preference_value)
                VALUES (?, ?, ?)
            """, current_user.id, key, str(value))
            
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "message": "Preference updated successfully"
        })
        
    except Exception as e:
        logging.error(f"Error updating user preference: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error: {str(e)}"
        }), 500

@preferences_bp.route('/api/reset', methods=['POST'])
@login_required
def reset_preferences_api():
    """Reset all preferences to default values for the current user"""
    try:
        definitions = get_preference_definitions()
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Update all preferences to default values
        for key, info in definitions.items():
            # Check if the preference exists
            cursor.execute("""
                SELECT id FROM UserPreferences
                WHERE user_id = ? AND preference_key = ?
            """, current_user.id, key)
            
            preference_exists = cursor.fetchone() is not None
            
            if preference_exists:
                # Update existing preference
                cursor.execute("""
                    UPDATE UserPreferences
                    SET preference_value = ?, updated_at = getutcdate()
                    WHERE user_id = ? AND preference_key = ?
                """, str(info['default_value']), current_user.id, key)
            else:
                # Insert new preference with default value
                cursor.execute("""
                    INSERT INTO UserPreferences (user_id, preference_key, preference_value)
                    VALUES (?, ?, ?)
                """, current_user.id, key, str(info['default_value']))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "message": "All preferences reset to default values"
        })
        
    except Exception as e:
        logging.error(f"Error resetting preferences: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error: {str(e)}"
        }), 500

@preferences_bp.route('/admin/synchronize', methods=['POST'])
@login_required
def admin_synchronize_preferences():
    """Ensure all active users have all defined preferences (admin only)"""
    # Requires admin role
    if current_user.role != 3:
        return jsonify({
            "status": "error",
            "message": "Administrator privileges required"
        }), 403
        
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get all active users
        cursor.execute("SELECT id FROM [User] WHERE role > 0")
        user_ids = [row[0] for row in cursor.fetchall()]
        
        # Get all active preference definitions
        cursor.execute("""
            SELECT preference_key, default_value
            FROM PreferenceDefinitions
            WHERE is_active = 1
        """)
        
        preferences = {row[0]: row[1] for row in cursor.fetchall()}
        
        # For each user, make sure they have all preference keys
        total_added = 0
        for user_id in user_ids:
            # Get existing preference keys for this user
            cursor.execute("""
                SELECT preference_key
                FROM UserPreferences
                WHERE user_id = ?
            """, user_id)
            
            existing_keys = {row[0] for row in cursor.fetchall()}
            
            # Determine missing keys
            missing_keys = set(preferences.keys()) - existing_keys
            
            # Add missing preferences
            for key in missing_keys:
                cursor.execute("""
                    INSERT INTO UserPreferences 
                    (user_id, preference_key, preference_value, created_at, updated_at)
                    VALUES (?, ?, ?, getutcdate(), getutcdate())
                """, user_id, key, preferences[key])
                total_added += 1
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "message": f"Synchronized preferences for {len(user_ids)} users. Added {total_added} missing preferences."
        })
        
    except Exception as e:
        logging.error(f"Error synchronizing user preferences: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error: {str(e)}"
        }), 500

# Export callable functions for use in other parts of the application
def get_preference(user_id: int, preference_key: str, default_value: Any = None) -> Any:
    """Public helper function to get a user preference value"""
    return get_user_preference(user_id, preference_key, default_value)

# Export other useful functions
__all__ = ['get_preference', 'get_preference_definitions']