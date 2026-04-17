"""
Local onboarding state management for AI Hub
Stores per-user onboarding progress in a local JSON file.
No database migrations required - works with all existing installations.
"""
import os
import json
import logging
from datetime import datetime
from threading import Lock
from typing import Optional

logger = logging.getLogger(__name__)

# Store in app data directory (same location as other local config)
ONBOARDING_STATE_FILE = os.path.join(
    os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__))),
    'data',
    'onboarding_state.json'
)

_file_lock = Lock()


def _ensure_data_dir():
    """Ensure the data directory exists"""
    data_dir = os.path.dirname(ONBOARDING_STATE_FILE)
    if not os.path.exists(data_dir):
        os.makedirs(data_dir, exist_ok=True)


def _load_state() -> dict:
    """Load onboarding state from JSON file"""
    try:
        if os.path.exists(ONBOARDING_STATE_FILE):
            with open(ONBOARDING_STATE_FILE, 'r') as f:
                return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in onboarding state file: {e}")
    except Exception as e:
        logger.error(f"Error loading onboarding state: {e}")
    return {}


def _save_state(state: dict):
    """Save onboarding state to JSON file (thread-safe)"""
    try:
        _ensure_data_dir()
        with _file_lock:
            with open(ONBOARDING_STATE_FILE, 'w') as f:
                json.dump(state, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Error saving onboarding state: {e}")


def _get_user_key(user_id: int) -> str:
    """Generate consistent key for user"""
    return f"user_{user_id}"


def get_onboarding_status(user_id: int) -> dict:
    """
    Get complete onboarding status for a user.
    
    Returns dict with:
        - onboarding_completed: bool
        - onboarding_step: int (0-999, where 999 = completed)
        - completed_at: str or None
        - first_seen: str or None
        - skipped: bool
        - selected_goal: str or None ('chat-agent', 'data-agent', 'explore')
        - tour_completed: bool
    """
    state = _load_state()
    user_key = _get_user_key(user_id)
    
    user_state = state.get(user_key, {})
    
    return {
        'onboarding_completed': user_state.get('completed', False),
        'onboarding_step': user_state.get('step', 0),
        'completed_at': user_state.get('completed_at'),
        'first_seen': user_state.get('first_seen'),
        'skipped': user_state.get('skipped', False),
        'selected_goal': user_state.get('selected_goal'),
        'tour_completed': user_state.get('tour_completed', False),
        'tours_taken': user_state.get('tours_taken', [])
    }


def needs_onboarding(user_id: int) -> bool:
    """Check if user needs onboarding (hasn't completed or skipped)"""
    status = get_onboarding_status(user_id)
    return not status['onboarding_completed']


def update_onboarding_progress(user_id: int, step: int, goal: Optional[str] = None):
    """
    Update user's onboarding progress.
    
    Args:
        user_id: The user's ID
        step: Current step number (1, 2, 3, etc.)
        goal: Optional selected goal ('chat-agent', 'data-agent', 'explore')
    """
    state = _load_state()
    user_key = _get_user_key(user_id)
    
    if user_key not in state:
        state[user_key] = {
            'first_seen': datetime.utcnow().isoformat()
        }
    
    state[user_key]['step'] = step
    state[user_key]['last_updated'] = datetime.utcnow().isoformat()
    
    if goal:
        state[user_key]['selected_goal'] = goal
    
    _save_state(state)


def complete_onboarding(user_id: int, via_tour: bool = False):
    """
    Mark onboarding as complete for user.
    
    Args:
        user_id: The user's ID
        via_tour: True if completed by finishing the tour
    """
    state = _load_state()
    user_key = _get_user_key(user_id)
    
    if user_key not in state:
        state[user_key] = {
            'first_seen': datetime.utcnow().isoformat()
        }
    
    state[user_key]['completed'] = True
    state[user_key]['completed_at'] = datetime.utcnow().isoformat()
    state[user_key]['skipped'] = False
    state[user_key]['step'] = 999  # Completed marker
    
    if via_tour:
        state[user_key]['tour_completed'] = True
    
    _save_state(state)


def skip_onboarding(user_id: int):
    """Mark onboarding as skipped (user chose to skip)"""
    state = _load_state()
    user_key = _get_user_key(user_id)
    
    if user_key not in state:
        state[user_key] = {
            'first_seen': datetime.utcnow().isoformat()
        }
    
    state[user_key]['completed'] = True
    state[user_key]['completed_at'] = datetime.utcnow().isoformat()
    state[user_key]['skipped'] = True
    state[user_key]['step'] = -1  # Skipped marker
    
    _save_state(state)


def reset_onboarding(user_id: int):
    """
    Reset onboarding for user (to replay tour from settings/help).
    Preserves first_seen and historical data.
    """
    state = _load_state()
    user_key = _get_user_key(user_id)
    
    if user_key in state:
        # Preserve first_seen but reset completion status
        first_seen = state[user_key].get('first_seen')
        tours_taken = state[user_key].get('tours_taken', [])
        
        state[user_key] = {
            'first_seen': first_seen,
            'completed': False,
            'step': 0,
            'tours_taken': tours_taken
        }
    else:
        state[user_key] = {
            'first_seen': datetime.utcnow().isoformat(),
            'completed': False,
            'step': 0
        }
    
    _save_state(state)


def record_tour_taken(user_id: int, tour_name: str):
    """
    Record that a user has taken a specific tour (dashboard, agent-builder, etc.)
    
    Args:
        user_id: The user's ID
        tour_name: Name of the tour ('dashboard', 'agent-builder', 'workflows', etc.)
    """
    state = _load_state()
    user_key = _get_user_key(user_id)
    
    if user_key not in state:
        state[user_key] = {
            'first_seen': datetime.utcnow().isoformat()
        }
    
    if 'tours_taken' not in state[user_key]:
        state[user_key]['tours_taken'] = []
    
    tour_record = {
        'tour': tour_name,
        'taken_at': datetime.utcnow().isoformat()
    }
    
    state[user_key]['tours_taken'].append(tour_record)
    _save_state(state)


def has_taken_tour(user_id: int, tour_name: str) -> bool:
    """Check if user has already taken a specific tour"""
    status = get_onboarding_status(user_id)
    tours = status.get('tours_taken', [])
    return any(t.get('tour') == tour_name for t in tours)


def get_all_users_status() -> dict:
    """
    Get onboarding status for all users (admin/analytics use).
    Returns dict keyed by user_id with their status.
    """
    state = _load_state()
    return {
        key.replace('user_', ''): value 
        for key, value in state.items() 
        if key.startswith('user_')
    }


def get_checklist_state(user_id: int, checklist_name: str) -> dict:
    """
    Get the state of a specific checklist for a user.
    
    Args:
        user_id: The user's ID
        checklist_name: Name of the checklist (e.g., 'data-assistant')
    
    Returns:
        Dict with checklist state:
        - active: bool
        - completed: list of completed step names
        - dismissed: bool
    """
    state = _load_state()
    user_key = _get_user_key(user_id)
    
    user_state = state.get(user_key, {})
    checklists = user_state.get('checklists', {})
    
    return checklists.get(checklist_name, {
        'active': False,
        'completed': [],
        'dismissed': False
    })


def save_checklist_state(user_id: int, checklist_name: str, checklist_state: dict):
    """
    Save the state of a specific checklist for a user.
    
    Args:
        user_id: The user's ID
        checklist_name: Name of the checklist (e.g., 'data-assistant')
        checklist_state: Dict with active, completed, dismissed fields
    """
    state = _load_state()
    user_key = _get_user_key(user_id)
    
    if user_key not in state:
        state[user_key] = {
            'first_seen': datetime.utcnow().isoformat()
        }
    
    if 'checklists' not in state[user_key]:
        state[user_key]['checklists'] = {}
    
    state[user_key]['checklists'][checklist_name] = {
        'active': checklist_state.get('active', False),
        'completed': checklist_state.get('completed', []),
        'dismissed': checklist_state.get('dismissed', False),
        'last_updated': datetime.utcnow().isoformat()
    }
    
    _save_state(state)


def complete_checklist_step(user_id: int, checklist_name: str, step_name: str) -> dict:
    """
    Mark a step as complete in a checklist.
    
    Args:
        user_id: The user's ID
        checklist_name: Name of the checklist
        step_name: Name of the step to mark complete
    
    Returns:
        Updated checklist state
    """
    checklist_state = get_checklist_state(user_id, checklist_name)
    
    completed = checklist_state.get('completed', [])
    if step_name not in completed:
        completed.append(step_name)
    
    checklist_state['completed'] = completed
    save_checklist_state(user_id, checklist_name, checklist_state)
    
    return checklist_state


def activate_checklist(user_id: int, checklist_name: str):
    """
    Activate a checklist for a user.
    
    Args:
        user_id: The user's ID
        checklist_name: Name of the checklist to activate
    """
    checklist_state = get_checklist_state(user_id, checklist_name)
    checklist_state['active'] = True
    checklist_state['dismissed'] = False
    save_checklist_state(user_id, checklist_name, checklist_state)


def dismiss_checklist(user_id: int, checklist_name: str):
    """
    Dismiss a checklist for a user.
    
    Args:
        user_id: The user's ID
        checklist_name: Name of the checklist to dismiss
    """
    checklist_state = get_checklist_state(user_id, checklist_name)
    checklist_state['dismissed'] = True
    save_checklist_state(user_id, checklist_name, checklist_state)


def is_checklist_complete(user_id: int, checklist_name: str, all_steps: list) -> bool:
    """
    Check if all steps in a checklist are complete.
    
    Args:
        user_id: The user's ID
        checklist_name: Name of the checklist
        all_steps: List of all step names that need to be complete
    
    Returns:
        True if all steps are complete
    """
    checklist_state = get_checklist_state(user_id, checklist_name)
    completed = checklist_state.get('completed', [])
    return all(step in completed for step in all_steps)