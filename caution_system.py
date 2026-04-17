# caution_system.py

import logging
import json
from flask import request, jsonify
import config as cfg
from config_db_client import ConfigDatabaseClient
import os


# logging.basicConfig(filename=cfg.LOG_DIR, level=logging.DEBUG, format='%(asctime)s [%(levelname)s] - %(message)s')

class CautionManager:
    """
    Manages caution levels in the NLQ application.
    Controls how cautious the AI is in providing answers and when to ask for clarification.
    """
    
    # Define caution level presets
    DEFAULT_CAUTION_LEVELS = cfg.DATA_AGENT_DEFAULT_CAUTION_LEVELS
    
    def __init__(self, logger=None, default_level='medium'):
        """
        Initialize the caution manager.
        
        Args:
            db_connection: Database connection for storing caution settings
            logger: Logger for recording events (optional)
            default_level: Default caution level to use
        """
        self.db = ConfigDatabaseClient()
        self.logger = logger or logging.getLogger(__name__)
        self.default_level = default_level
        self.caution_levels = self.DEFAULT_CAUTION_LEVELS.copy()
        
        # Attempt to load custom caution levels from the database
        self._load_custom_caution_levels()
    
    
    def _initialize_default_caution_levels(self):
        """Initialize the database with default caution levels if empty."""
        try:
            # Check if table is empty
            check_sql = "SELECT COUNT(*) FROM [dbo].[caution_settings]"
            result = self.db.fetch_query(check_sql)
            
            if result and result[0][0] == 0:
                # Table is empty, insert default values
                for level_name, settings in self.DEFAULT_CAUTION_LEVELS.items():
                    insert_sql = """
                    INSERT INTO [dbo].[caution_settings]
                        ([level_name], [description], [confidence_threshold], 
                         [clarification_threshold], [max_assumption_count], [allow_extrapolation])
                    VALUES (?, ?, ?, ?, ?, ?)
                    """
                    
                    params = (
                        level_name,
                        settings['description'],
                        settings['confidence_threshold'],
                        settings['clarification_threshold'],
                        settings['max_assumption_count'],
                        1 if settings['allow_extrapolation'] else 0
                    )
                    
                    self.db.execute_query(insert_sql, params)
                
                self.logger.info("Default caution levels initialized in database.")
            
            return True
        except Exception as e:
            self.logger.error(f"Error initializing default caution levels: {e}")
            return False
    
    def _load_custom_caution_levels(self):
        """Load any custom caution levels from the database."""
        try:
            # Load active caution levels from database
            query = """
            SELECT 
                [level_name], [description], [confidence_threshold], 
                [clarification_threshold], [max_assumption_count], [allow_extrapolation]
            FROM [dbo].[caution_settings]
            WHERE [is_active] = 1
            """
            
            results = self.db.fetch_query(query)
            
            if results:
                # Update caution_levels with database values
                for row in results:
                    level_name = row[0]
                    self.caution_levels[level_name] = {
                        'description': row[1],
                        'confidence_threshold': row[2],
                        'clarification_threshold': row[3],
                        'max_assumption_count': row[4],
                        'allow_extrapolation': bool(row[5])
                    }
                
                self.logger.info("Custom caution levels loaded from database.")
            
            return True
        except Exception as e:
            self.logger.error(f"Error loading custom caution levels: {e}")
            # Continue with default levels
            return False
        
    # Add to caution_system.py
    def get_tenant_caution_config(self):
        """Get caution system configuration for a specific tenant."""
        try:
            query = """
            SELECT [enable_caution_system], [default_caution_level]
            FROM [dbo].[tenant_configuration]
            """
            
            row = self.db.fetch_one(query)
            
            if row:
                return {
                    'enabled': bool(row[0]),
                    'default_level': row[1]
                }
            else:
                # Return system defaults if no tenant-specific config
                return {
                    'enabled': cfg.ENABLE_CAUTION_SYSTEM,
                    'default_level': cfg.DEFAULT_CAUTION_LEVEL
                }
                
        except Exception as e:
            self.logger.error(f"Error getting tenant caution config: {e}")
            # Fall back to system defaults
            return {
                'enabled': cfg.ENABLE_CAUTION_SYSTEM,
                'default_level': cfg.DEFAULT_CAUTION_LEVEL
            }
        
    # Add to caution_system.py
    def record_caution_event(self, event_type, data):
        """
        Record a caution system event for monitoring and analytics.
        
        Args:
            event_type: Type of event (e.g., 'clarification_requested', 'assumption_made')
            data: Dictionary of event data
        """
        if not cfg.ENABLE_CAUTION_SYSTEM:
            return
            
        try:
            query = """
            INSERT INTO [dbo].[caution_system_telemetry]
                ([event_type], [event_data], [timestamp])
            VALUES (?, ?, GETUTCDATE())
            """
            
            self.db.execute_query(query, (
                event_type,
                json.dumps(data)
            ))
            
        except Exception as e:
            self.logger.error(f"Error recording caution event: {e}")
    
    def get_caution_levels(self):
        """Get all available caution levels and their settings."""
        return self.caution_levels
    
    def get_caution_level(self, level_name=None):
        """
        Get the settings for a specific caution level.
        
        Args:
            level_name: Name of the caution level (or None for default)
            
        Returns:
            dict: Caution level settings
        """
        if not level_name:
            level_name = self.default_level
        
        return self.caution_levels.get(level_name, self.caution_levels[self.default_level])
    
    def save_caution_level(self, level_name, settings):
        """
        Save custom caution level settings to the database.
        
        Args:
            level_name: Name of the caution level
            settings: Dictionary of settings
            
        Returns:
            bool: True if successful
        """
        try:
            # Validate required settings
            required_fields = ['description', 'confidence_threshold', 'clarification_threshold', 
                             'max_assumption_count', 'allow_extrapolation']
            
            for field in required_fields:
                if field not in settings:
                    raise ValueError(f"Missing required field: {field}")
                
            # Check if this level already exists
            check_sql = "SELECT COUNT(*) FROM [dbo].[caution_settings] WHERE [level_name] = ?"
            result = self.db.fetch_query(check_sql, (level_name,))
            
            if result and result[0][0] > 0:
                # Update existing level
                update_sql = """
                UPDATE [dbo].[caution_settings]
                SET 
                    [description] = ?,
                    [confidence_threshold] = ?,
                    [clarification_threshold] = ?,
                    [max_assumption_count] = ?,
                    [allow_extrapolation] = ?,
                    [modified_at] = getutcdate()
                WHERE [level_name] = ?
                """
                
                params = (
                    settings['description'],
                    float(settings['confidence_threshold']),
                    float(settings['clarification_threshold']),
                    int(settings['max_assumption_count']),
                    1 if settings['allow_extrapolation'] else 0,
                    level_name
                )
                
                self.db.execute_query(update_sql, params)
            else:
                # Insert new level
                insert_sql = """
                INSERT INTO [dbo].[caution_settings]
                    ([level_name], [description], [confidence_threshold], 
                     [clarification_threshold], [max_assumption_count], [allow_extrapolation])
                VALUES (?, ?, ?, ?, ?, ?)
                """
                
                params = (
                    level_name,
                    settings['description'],
                    float(settings['confidence_threshold']),
                    float(settings['clarification_threshold']),
                    int(settings['max_assumption_count']),
                    1 if settings['allow_extrapolation'] else 0
                )
                
                self.db.execute_query(insert_sql, params)
            
            # Update in-memory caution levels
            self.caution_levels[level_name] = settings
            self.logger.info(f"Caution level '{level_name}' saved to database.")
            
            return True
        except Exception as e:
            self.logger.error(f"Error saving caution level: {e}")
            return False
    
    def get_user_caution_level(self, user_id):
        """
        Get the caution level setting for a specific user.

        Args:
            user_id: User ID

        Returns:
            str: Caution level name
        """
        try:
            query = """
            SELECT [caution_level]
            FROM [dbo].[user_caution_settings]
            WHERE [user_id] = ?
            ORDER BY [modified_at] DESC
            """

            row = self.db.fetch_one(query, (user_id,)) # Fetch the first result

            if row:
                return row[0]
            else:
                return self.default_level
        except Exception as e:
            self.logger.error(f"Error getting user caution level: {e}")
            return self.default_level

    
    def set_user_caution_level(self, user_id, level_name):
        """
        Set the caution level for a specific user.
        
        Args:
            user_id: User ID
            level_name: Caution level name
            
        Returns:
            bool: True if successful
        """
        try:
            # Validate level name
            if level_name not in self.caution_levels:
                raise ValueError(f"Invalid caution level: {level_name}")
            
            # Check if user already has a setting

            check_sql = "SELECT COUNT(*) FROM [dbo].[user_caution_settings] WHERE [user_id] = ?"
            result = self.db.fetch_query(check_sql, (user_id,))
            
            if result and result[0][0] > 0:
                # Update existing setting
                update_sql = """
                UPDATE [dbo].[user_caution_settings]
                SET 
                    [caution_level] = ?,
                    [modified_at] = getutcdate()
                WHERE [user_id] = ?
                """
                
                self.db.execute_query(update_sql, (level_name, user_id))
            else:
                # Insert new setting
                insert_sql = """
                INSERT INTO [dbo].[user_caution_settings]
                    ([user_id], [caution_level])
                VALUES (?, ?)
                """
                
                self.db.execute_query(insert_sql, (user_id, level_name))
            
            self.logger.info(f"User {user_id} caution level set to '{level_name}'.")
            
            return True
        except Exception as e:
            self.logger.error(f"Error setting user caution level: {e}")
            return False

# Flask route handlers for caution level system

def setup_caution_routes(app, caution_manager):
    """Set up Flask routes for the caution level system."""

    # Skip setting up routes if caution system is disabled
    if not cfg.ENABLE_CAUTION_SYSTEM:
        # Register fallback routes that return valid JSON when caution system is disabled
        @app.route('/api/caution/levels', methods=['GET'])
        def get_caution_levels_disabled():
            """Fallback endpoint for getting caution levels when system is disabled."""
            return jsonify({"status": "disabled", "message": "Caution system is disabled", "levels": {}})
        
        @app.route('/api/caution/level', methods=['GET'])
        def get_caution_level_disabled():
            """Fallback endpoint for getting a specific caution level when system is disabled."""
            return jsonify({"status": "disabled", "message": "Caution system is disabled", "level": {}})
        
        @app.route('/api/caution/level', methods=['POST'])
        def save_caution_level_disabled():
            """Fallback endpoint for saving a caution level when system is disabled."""
            return jsonify({"status": "disabled", "message": "Caution system is disabled"})
        
        @app.route('/api/caution/user', methods=['GET'])
        def get_user_caution_level_disabled():
            """Fallback endpoint for getting a user's caution level when system is disabled."""
            return jsonify({
                "status": "disabled", 
                "message": "Caution system is disabled",
                "level_name": "medium",  # Default value
                "level": {}
            })
        
        @app.route('/api/caution/user', methods=['POST'])
        def set_user_caution_level_disabled():
            """Fallback endpoint for setting a user's caution level when system is disabled."""
            return jsonify({"status": "disabled", "message": "Caution system is disabled"})
        
        # Exit early - we've registered the fallback routes
        return
    
    @app.route('/api/caution/levels', methods=['GET'])
    def get_caution_levels():
        """API endpoint for getting all caution levels."""
        try:
            levels = caution_manager.get_caution_levels()
            return jsonify({"status": "success", "levels": levels})
        except Exception as e:
            logging.error(f"Error getting caution levels: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500
    
    @app.route('/api/caution/level', methods=['GET'])
    def get_caution_level():
        """API endpoint for getting a specific caution level."""
        try:
            level_name = request.args.get('level_name')
            level = caution_manager.get_caution_level(level_name)
            return jsonify({"status": "success", "level": level})
        except Exception as e:
            logging.error(f"Error getting caution level: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500
    
    @app.route('/api/caution/level', methods=['POST'])
    def save_caution_level():
        """API endpoint for saving a caution level."""
        try:
            data = request.get_json()
            level_name = data.get('level_name')
            settings = data.get('settings')
            
            if not level_name or not settings:
                return jsonify({"status": "error", "message": "Missing level_name or settings"}), 400
            
            success = caution_manager.save_caution_level(level_name, settings)
            
            if success:
                return jsonify({"status": "success", "message": f"Caution level '{level_name}' saved successfully"})
            else:
                return jsonify({"status": "error", "message": "Failed to save caution level"}), 500
        except Exception as e:
            logging.error(f"Error saving caution level: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500
    
    @app.route('/api/caution/user', methods=['GET'])
    def get_user_caution_level():
        """API endpoint for getting a user's caution level."""
        try:
            user_id = request.args.get('user_id')
            
            if not user_id or not user_id.isdigit():
                return jsonify({"status": "error", "message": "Invalid user_id"}), 400
            
            level_name = caution_manager.get_user_caution_level(int(user_id))
            level = caution_manager.get_caution_level(level_name)
            
            return jsonify({
                "status": "success", 
                "level_name": level_name,
                "level": level
            })
        except Exception as e:
            logging.error(f"Error getting user caution level: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500
    
    @app.route('/api/caution/user', methods=['POST'])
    def set_user_caution_level():
        """API endpoint for setting a user's caution level."""
        try:
            print('Setting caution user level...')
            data = request.get_json()
            user_id = data.get('user_id')
            level_name = data.get('level_name')

            print('User:', user_id)
            print('Level:', level_name)
            
            if not user_id or not level_name:
                print("Missing user_id or level_name")
                return jsonify({"status": "error", "message": "Missing user_id or level_name"}), 400
            
            print('Setting level...')
            success = caution_manager.set_user_caution_level(int(user_id), level_name)
            print('Result:', success)
            if success:
                print(f"User caution level set to '{level_name}'")
                return jsonify({"status": "success", "message": f"User caution level set to '{level_name}'"})
            else:
                print("Failed to set user caution level")
                return jsonify({"status": "error", "message": "Failed to set user caution level"}), 500
        except Exception as e:
            print(f"Error setting user caution level: {e}")
            logging.error(f"Error setting user caution level: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500