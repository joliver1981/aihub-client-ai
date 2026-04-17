"""
Initial Setup State Management for AI Hub

Tracks whether the initial admin account setup has been completed.
Uses a local JSON file - no database migrations required.

This is specifically for the FREE TIER which is single-user.
On first launch, instead of having a default password, we prompt
the user to customize their admin credentials.

Handles the case where a default admin account already exists in the database.
"""
import os
import json
import logging
from datetime import datetime
from threading import Lock
from typing import Optional

logger = logging.getLogger(__name__)

# Store in app data directory (same location as other local config)
INITIAL_SETUP_STATE_FILE = os.path.join(
    os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__))),
    'data',
    'initial_setup_state.json'
)

# Default admin account username - customize this to match your database seed
# Note: We only check by username since user IDs are unpredictable in shared tenant databases
DEFAULT_ADMIN_USERNAME = os.getenv('DEFAULT_ADMIN_USERNAME', 'admin')

_file_lock = Lock()


def _ensure_data_dir():
    """Ensure the data directory exists"""
    data_dir = os.path.dirname(INITIAL_SETUP_STATE_FILE)
    if not os.path.exists(data_dir):
        os.makedirs(data_dir, exist_ok=True)


def _load_state() -> dict:
    """Load initial setup state from JSON file"""
    try:
        if os.path.exists(INITIAL_SETUP_STATE_FILE):
            with open(INITIAL_SETUP_STATE_FILE, 'r') as f:
                return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in initial setup state file: {e}")
    except Exception as e:
        logger.error(f"Error loading initial setup state: {e}")
    return {}


def _save_state(state: dict):
    """Save initial setup state to JSON file (thread-safe)"""
    try:
        _ensure_data_dir()
        with _file_lock:
            with open(INITIAL_SETUP_STATE_FILE, 'w') as f:
                json.dump(state, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Error saving initial setup state: {e}")


def needs_initial_setup() -> bool:
    """
    Check if the application needs initial setup.
    
    Returns True if:
    - Setup has never been completed AND
    - Either no users exist OR only the default admin account exists
    
    Returns False if:
    - Setup has been completed previously
    - OR there are non-default users in the database (setup was done manually)
    """
    state = _load_state()
    
    # If setup was already completed, we're done
    if state.get('setup_completed', False):
        return False
    
    # Check the database for users
    try:
        from DataUtils import Get_Users
        users_df = Get_Users()
        
        if users_df is None or users_df.empty:
            # No users at all - definitely need setup
            logger.info("No users found in database - initial setup required")
            return True
        
        # Check if only the default admin exists
        user_count = len(users_df)
        
        if user_count == 1:
            # Only one user - check if it's the default admin (by username only)
            user = users_df.iloc[0]
            username = user.get('user_name', '').lower()
            
            if username == DEFAULT_ADMIN_USERNAME.lower():
                logger.info(f"Only default admin account found (username: {username}) - initial setup required")
                return True
        
        # Multiple users exist or a non-default user exists
        # This means setup was done some other way (manual, migration, etc.)
        logger.info(f"Found {user_count} users in database - marking setup as complete")
        
        # Auto-complete the setup state since users already exist
        state['setup_completed'] = True
        state['completed_at'] = datetime.utcnow().isoformat()
        state['admin_username'] = users_df.iloc[0].get('user_name', 'unknown')
        state['setup_version'] = 1
        state['auto_completed'] = True  # Flag that this was auto-detected
        _save_state(state)
        
        return False
        
    except Exception as e:
        logger.error(f"Error checking users during setup check: {e}")
        # On error, check state file only
        return not state.get('setup_completed', False)


def get_default_admin_info() -> dict:
    """
    Get information about the default admin account.
    
    Returns:
        Dict with default admin settings
    """
    return {
        'username': DEFAULT_ADMIN_USERNAME
    }


def get_setup_status() -> dict:
    """
    Get complete initial setup status.
    
    Returns dict with:
        - setup_completed: bool
        - completed_at: str or None
        - admin_username: str or None (the username created during setup)
    """
    state = _load_state()
    
    return {
        'setup_completed': state.get('setup_completed', False),
        'completed_at': state.get('completed_at'),
        'admin_username': state.get('admin_username'),
        'setup_version': state.get('setup_version', 1)
    }


def complete_initial_setup(admin_username: str, admin_email: Optional[str] = None):
    """
    Mark initial setup as complete.
    
    Args:
        admin_username: The username of the admin account created
        admin_email: Optional email of the admin account
    """
    state = _load_state()
    
    state['setup_completed'] = True
    state['completed_at'] = datetime.utcnow().isoformat()
    state['admin_username'] = admin_username
    state['admin_email'] = admin_email
    state['setup_version'] = 1
    
    _save_state(state)
    logger.info(f"Initial setup completed for admin user: {admin_username}")


def reset_initial_setup():
    """
    Reset initial setup state (for testing/development only).
    
    WARNING: This does NOT delete the admin user from the database.
    It only resets the setup wizard state.
    """
    state = _load_state()
    
    # Preserve history
    if 'reset_history' not in state:
        state['reset_history'] = []
    
    state['reset_history'].append({
        'reset_at': datetime.utcnow().isoformat(),
        'previous_admin': state.get('admin_username')
    })
    
    state['setup_completed'] = False
    state['completed_at'] = None
    state['admin_username'] = None
    state['admin_email'] = None
    
    _save_state(state)
    logger.warning("Initial setup state has been reset")
