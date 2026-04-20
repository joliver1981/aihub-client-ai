"""
Onboarding API Routes for AI Hub
Provides endpoints for managing user onboarding state.
"""
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from onboarding_state import (
    get_onboarding_status,
    needs_onboarding,
    update_onboarding_progress,
    complete_onboarding,
    skip_onboarding,
    reset_onboarding,
    record_tour_taken,
    has_taken_tour,
    get_checklist_state,
    save_checklist_state
)

onboarding_bp = Blueprint('onboarding', __name__, url_prefix='/api/onboarding')


@onboarding_bp.route('/status', methods=['GET'])
@login_required
def get_status():
    """
    Get onboarding status for current user.
    
    Returns:
        JSON with onboarding state including:
        - needs_onboarding: bool
        - current_step: int
        - user_name: str
        - user_role: int
        - skipped_previously: bool
        - selected_goal: str or null
    """
    try:
        status = get_onboarding_status(current_user.id)

        # The welcome / onboarding flow is built around developer paths
        # (Custom Agent + Tools, Data Assistant, Workflow Builder, Import Agent
        # Package). All four destinations require Developer role or above.
        # A role=1 (User) account cannot complete any path, so the modal would
        # trap them behind a static-backdrop overlay with no successful exit.
        # Force needs_onboarding=False for non-developer roles so the modal
        # never shows in the first place.
        role = getattr(current_user, 'role', 0) or 0
        needs_ob = (not status['onboarding_completed']) and role >= 2

        return jsonify({
            'success': True,
            'needs_onboarding': needs_ob,
            'current_step': status['onboarding_step'],
            'user_name': current_user.name,
            'user_role': role,
            'skipped_previously': status['skipped'],
            'selected_goal': status['selected_goal'],
            'tour_completed': status['tour_completed']
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'needs_onboarding': False  # Fail safe - don't block user
        }), 500


@onboarding_bp.route('/progress', methods=['POST'])
@login_required
def update_progress():
    """
    Update onboarding progress for current user.
    
    Request body:
        - step: int (required) - Current step number
        - goal: str (optional) - Selected goal ('chat-agent', 'data-agent', 'explore')
    """
    try:
        data = request.get_json() or {}
        step = data.get('step', 0)
        goal = data.get('goal')
        
        update_onboarding_progress(current_user.id, step, goal)
        
        return jsonify({
            'success': True,
            'step': step,
            'goal': goal
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@onboarding_bp.route('/complete', methods=['POST'])
@login_required
def complete():
    """
    Mark onboarding as complete for current user.
    
    Request body (optional):
        - via_tour: bool - True if completed by finishing the guided tour
    """
    try:
        data = request.get_json() or {}
        via_tour = data.get('via_tour', False)
        
        complete_onboarding(current_user.id, via_tour=via_tour)
        
        return jsonify({
            'success': True,
            'message': 'Onboarding completed'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@onboarding_bp.route('/skip', methods=['POST'])
@login_required
def skip():
    """Skip onboarding for current user."""
    try:
        skip_onboarding(current_user.id)
        
        return jsonify({
            'success': True,
            'message': 'Onboarding skipped'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@onboarding_bp.route('/reset', methods=['POST'])
@login_required
def reset():
    """
    Reset onboarding for current user (to replay welcome/tour).
    Called from settings or help menu.
    """
    try:
        reset_onboarding(current_user.id)
        
        return jsonify({
            'success': True,
            'message': 'Onboarding reset - refresh page to see welcome'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@onboarding_bp.route('/tour/record', methods=['POST'])
@login_required
def record_tour():
    """
    Record that user has completed a specific tour.
    
    Request body:
        - tour_name: str (required) - Name of tour ('dashboard', 'agent-builder', etc.)
    """
    try:
        data = request.get_json() or {}
        tour_name = data.get('tour_name')
        
        if not tour_name:
            return jsonify({
                'success': False,
                'error': 'tour_name is required'
            }), 400
        
        record_tour_taken(current_user.id, tour_name)
        
        return jsonify({
            'success': True,
            'tour_name': tour_name
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@onboarding_bp.route('/tour/check/<tour_name>', methods=['GET'])
@login_required
def check_tour(tour_name):
    """
    Check if user has taken a specific tour.
    
    URL params:
        - tour_name: Name of tour to check
    """
    try:
        taken = has_taken_tour(current_user.id, tour_name)
        
        return jsonify({
            'success': True,
            'tour_name': tour_name,
            'has_taken': taken
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@onboarding_bp.route('/checklist/data-assistant', methods=['GET'])
@login_required
def get_data_assistant_checklist():
    """
    Get the Data Assistant setup checklist state.
    
    Returns:
        JSON with checklist state:
        - active: bool - Is the checklist currently active
        - completed: list - Steps that have been completed
        - dismissed: bool - Has user dismissed the checklist
    """
    try:
        state = get_checklist_state(current_user.id, 'data-assistant')
        
        return jsonify({
            'success': True,
            'active': state.get('active', False),
            'completed': state.get('completed', []),
            'dismissed': state.get('dismissed', False)
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'active': False,
            'completed': [],
            'dismissed': False
        }), 500


@onboarding_bp.route('/checklist/data-assistant', methods=['POST'])
@login_required
def update_data_assistant_checklist():
    """
    Update the Data Assistant setup checklist state.
    
    Request body:
        - active: bool (optional)
        - completed: list (optional)
        - dismissed: bool (optional)
    """
    try:
        data = request.get_json() or {}
        
        state = {
            'active': data.get('active', False),
            'completed': data.get('completed', []),
            'dismissed': data.get('dismissed', False)
        }
        
        save_checklist_state(current_user.id, 'data-assistant', state)
        
        return jsonify({
            'success': True,
            'state': state
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@onboarding_bp.route('/checklist/data-assistant/step/<step_name>', methods=['POST'])
@login_required
def complete_checklist_step(step_name):
    """
    Mark a specific step as complete.
    
    URL params:
        - step_name: 'connection', 'dictionary', 'agent', or 'query'
    """
    try:
        valid_steps = ['connection', 'dictionary', 'agent', 'query']
        
        if step_name not in valid_steps:
            return jsonify({
                'success': False,
                'error': f'Invalid step. Must be one of: {valid_steps}'
            }), 400
        
        # Get current state
        state = get_checklist_state(current_user.id, 'data-assistant')
        
        # Add step if not already completed
        completed = state.get('completed', [])
        if step_name not in completed:
            completed.append(step_name)
        
        state['completed'] = completed
        save_checklist_state(current_user.id, 'data-assistant', state)
        
        return jsonify({
            'success': True,
            'step': step_name,
            'completed': completed,
            'all_complete': len(completed) == len(valid_steps)
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@onboarding_bp.route('/checklist/data-assistant/activate', methods=['POST'])
@login_required
def activate_data_assistant_checklist():
    """
    Activate the Data Assistant checklist (called when user selects this path).
    """
    try:
        state = get_checklist_state(current_user.id, 'data-assistant')
        state['active'] = True
        state['dismissed'] = False
        save_checklist_state(current_user.id, 'data-assistant', state)
        
        return jsonify({
            'success': True,
            'message': 'Checklist activated'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500