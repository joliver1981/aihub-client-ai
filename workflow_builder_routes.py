# workflow_builder_routes.py
# Flask routes for the AI Workflow Builder Guide

from flask import Blueprint, request, jsonify, session
from WorkflowAgent import WorkflowAgent
from workflow_command_validator import validate_workflow
from workflow_compiler import compile_workflow
import logging
from logging.handlers import WatchedFileHandler
from CommonUtils import rotate_logs_on_startup, get_log_path
import os

### TRAINING CAPTURE - START ###
try:
    from workflow_training_capture import (
        capture_from_agent, get_statistics, capture_plan_to_commands,
        capture_bad_plan_to_commands,
    )
except ImportError:
    def capture_from_agent(*args, **kwargs): return False
    def get_statistics(): return {"available": False}
    def capture_plan_to_commands(*args, **kwargs): return False
    def capture_bad_plan_to_commands(*args, **kwargs): return False
### TRAINING CAPTURE - END ###


# Create blueprint
workflow_builder_bp = Blueprint('workflow_builder', __name__)

rotate_logs_on_startup(os.getenv('WORKFLOW_BUILDER_ROUTES_LOG', get_log_path('workflow_builder_log.txt')))

# Configure logging
logger = logging.getLogger("WorkflowBuilderRoutes")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=os.getenv('WORKFLOW_BUILDER_ROUTES_LOG', get_log_path('workflow_builder_log.txt')), encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)

# Store active builder sessions
builder_sessions = {}
training_builder_sessions = {}

@workflow_builder_bp.route('/api/workflow/builder/guide', methods=['POST'])
def workflow_builder_guide():
    """
    Handle guided workflow building requests
    """
    try:
        data = request.get_json()
        
        if not data or 'message' not in data:
            return jsonify({
                'status': 'error',
                'error': 'Message is required'
            }), 400
        
        message = data.get('message')
        session_id = data.get('session_id')  # This is not and should not be the true session id or it will persist the conversation across multiple conversations
        workflow_state = data.get('workflow_state')
        is_validation_fix = data.get('is_validation_fix', False)
        is_builder_delegation = data.get('is_builder_delegation', False)

        # Get or create agent for this session
        if session_id not in builder_sessions:
            logger.info(f"Creating new WorkflowAgent for session {session_id}")
            # Pass workflow_state during initialization
            builder_sessions[session_id] = WorkflowAgent(
                session_id=session_id,
                workflow_state=workflow_state,
                is_builder_delegation=is_builder_delegation,
            )
        
        agent = builder_sessions[session_id]

        logger.debug(f"Processing message '{message}' for session {session_id}")
        
        # Process the message
        response_text, metadata = agent.process_message(message, workflow_state)

        logger.debug(f"Response text: {response_text}")
        logger.debug(f"Metadata: {metadata}")

        try:
            training_session_id = session['session_id']
            training_builder_sessions[training_session_id] = agent
            
            # Capture plan-to-commands format if both are available
            # if metadata.get('workflow_commands') and metadata.get('workflow_plan'):
            #     capture_plan_to_commands(
            #         workflow_plan=metadata['workflow_plan'],
            #         commands=metadata['workflow_commands'],
            #         session_id=training_session_id
            #     )
        except Exception as e:
            logger.debug(f"Session capture failed: {e}")

        # Mark if this is a validation fix request
        if is_validation_fix:
            agent.is_validation_fix = True
            logger.info(f"Validation fix request for session {session_id}")
            # Stash the fix message + the BAD commands the agent had just
            # produced so we can persist them later for validator analysis.
            try:
                fix_msgs = list(getattr(agent, 'fix_messages', None) or [])
                fix_msgs.append(message)
                agent.fix_messages = fix_msgs
                # The current_json_commands attribute holds the most recent
                # commands the agent generated (the bad ones, if a fix was
                # needed). Save a copy so a later good build can't overwrite
                # them in memory before finalize-capture runs.
                bad_cmds = getattr(agent, 'current_json_commands', None) or getattr(agent, 'generated_commands', None)
                if bad_cmds and not getattr(agent, 'first_bad_commands', None):
                    if isinstance(bad_cmds, list):
                        agent.first_bad_commands = {"action": "build_workflow", "commands": bad_cmds}
                    else:
                        agent.first_bad_commands = bad_cmds
            except Exception as stash_e:
                logger.debug(f"could not stash bad-output context: {stash_e}")
        
        # Build response
        result = {
            'status': 'success',
            'response': response_text,
            'phase': metadata.get('phase'),
            'requirements': metadata.get('requirements'),
            'workflow_plan': metadata.get('workflow_plan'),
            'session_id': session_id,
            'is_refine_mode': metadata.get('is_refine_mode', False),  # New flag
            'has_workflow': metadata.get('has_workflow', False)  # New flag
        }
        
        # Include workflow commands if generated
        if metadata.get('workflow_commands'):
            result['workflow_commands'] = metadata['workflow_commands']
            logger.info(f"Generated workflow commands for session {session_id}")
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error in workflow builder guide: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500
    


@workflow_builder_bp.route('/api/workflow/builder/validate', methods=['POST'])
def validate_workflow_state():
    """
    Validate a workflow state AFTER commands have been executed.
    Returns issues for WorkflowAgent to fix with full context.
    
    Usage:
        1. Frontend executes build commands
        2. Frontend calls this endpoint with the NEW workflow state
        3. If errors returned, frontend sends them to WorkflowAgent
        4. WorkflowAgent generates fix commands with full context
    """
    try:
        data = request.get_json()
        
        if not data or 'workflow_state' not in data:
            return jsonify({
                'status': 'error',
                'error': 'workflow_state is required'
            }), 400
        
        workflow_state = data.get('workflow_state')
        
        # Validate the workflow
        is_valid, validation_result = validate_workflow(workflow_state)

        # Log results
        if validation_result.get('errors'):
            logger.info(f"Workflow validation found {len(validation_result['errors'])} errors")

        response = {
            'status': 'success',
            'is_valid': is_valid,
            'errors': validation_result.get('errors', []),
            'warnings': validation_result.get('warnings', []),
            # Structured per-warning details so the designer UI can decorate
            # the offending node (red ring + tooltip). Each entry is
            # {code, node_id, message, extra}. Falls back to [] when the
            # validator doesn't populate it (e.g. LLM-only fallback path).
            'warning_details': validation_result.get('warning_details', []),
        }
        # Surface fix_commands so the frontend command executor can apply them
        # directly without bouncing back to the agent. The deterministic
        # pre-pass populates this for issues with unambiguous fixes; the LLM
        # fallback may also return fix_commands. The frontend's
        # workflow_command_executor.js already knows this shape.
        if validation_result.get('fix_commands'):
            response['fix_commands'] = validation_result['fix_commands']
            logger.info(
                f"Validation emitted "
                f"{len(validation_result['fix_commands'].get('commands', []))} "
                f"fix command(s) for direct application"
            )

        return jsonify(response)
        
    except Exception as e:
        logger.error(f"Error validating workflow: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


@workflow_builder_bp.route('/api/workflow/builder/compile', methods=['POST'])
def compile_workflow_endpoint():
    """
    Compile a workflow plan into a saved workflow in a single call.
    Server-side equivalent of the full frontend build pipeline.

    Supports both CREATE (new workflow) and EDIT (modify existing) modes.
    Edit mode is triggered by including workflow_id in the request.

    Request body:
    {
        "workflow_plan": "1. Folder Selector node: ...\n2. AI Extract node: ...",
        "workflow_name": "Invoice Processor",
        "workflow_id": 42,            // optional — triggers edit mode
        "requirements": {},           // optional
        "save": true,                 // optional, default true
        "max_fix_attempts": 2         // optional, default 2
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({'status': 'error', 'error': 'Request body is required'}), 400

        workflow_plan = data.get('workflow_plan')
        workflow_name = data.get('workflow_name')
        workflow_id = data.get('workflow_id')  # None = create, int = edit

        if not workflow_plan:
            return jsonify({'status': 'error', 'error': 'workflow_plan is required'}), 400
        if not workflow_name and not workflow_id:
            return jsonify({'status': 'error', 'error': 'workflow_name or workflow_id is required'}), 400

        # Strip <workflow_plan> tags if the caller included them
        workflow_plan = workflow_plan.replace('<workflow_plan>', '').replace('</workflow_plan>', '').strip()

        # Default workflow_name for edit mode if not provided
        if not workflow_name and workflow_id:
            workflow_name = str(workflow_id)  # compile_workflow will resolve the real name from DB

        mode = "edit" if workflow_id else "create"
        logger.info(f"Compile request [{mode}]: '{workflow_name}' ({len(workflow_plan)} chars)")

        # Run the full compilation pipeline
        result = compile_workflow(
            workflow_plan=workflow_plan,
            workflow_name=workflow_name,
            requirements=data.get('requirements'),
            save=data.get('save', True),
            max_fix_attempts=data.get('max_fix_attempts', 2),
            workflow_id=workflow_id
        )

        if result["success"]:
            response = {
                'status': 'success',
                'workflow_id': result['workflow_id'],
                'workflow_name': result['workflow_name'],
                'workflow_data': result['workflow_data'],
                'validation': result['validation'],
                'fix_attempts': result['fix_attempts'],
                'mode': result['mode'],
                'node_count': len(result['workflow_data'].get('nodes', [])),
                'connection_count': len(result['workflow_data'].get('connections', []))
            }
            logger.info(f"Compile success [{mode}]: {workflow_name} (ID: {result['workflow_id']})")
            return jsonify(response)
        else:
            response = {
                'status': 'error',
                'error': result['error'],
                'workflow_name': result['workflow_name'],
                'workflow_data': result['workflow_data'],
                'validation': result['validation'],
                'fix_attempts': result['fix_attempts'],
                'mode': result['mode']
            }
            logger.error(f"Compile failed [{mode}]: {workflow_name} - {result['error']}")
            return jsonify(response), 500

    except Exception as e:
        logger.error(f"Error in compile endpoint: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


@workflow_builder_bp.route('/api/workflow/builder/status', methods=['GET'])
def workflow_builder_status():
    """
    Get status of a builder session
    """
    try:
        session_id = request.args.get('session_id')
        
        if not session_id:
            return jsonify({
                'status': 'error',
                'error': 'Session ID is required'
            }), 400
        
        if session_id not in builder_sessions:
            return jsonify({
                'status': 'error',
                'error': 'Session not found'
            }), 404
        
        agent = builder_sessions[session_id]
        summary = agent.get_session_summary()
        
        return jsonify({
            'status': 'success',
            'summary': summary
        })
        
    except Exception as e:
        logger.error(f"Error getting builder status: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500

@workflow_builder_bp.route('/api/workflow/builder/clear', methods=['POST'])
def workflow_builder_clear():
    """
    Clear a builder session
    """
    try:
        data = request.get_json()
        session_id = data.get('session_id')
        
        if not session_id:
            return jsonify({
                'status': 'error',
                'error': 'Session ID is required'
            }), 400
        
        if session_id in builder_sessions:
            del builder_sessions[session_id]
            logger.info(f"Cleared builder session {session_id}")
        
        return jsonify({
            'status': 'success',
            'message': 'Session cleared'
        })
        
    except Exception as e:
        logger.error(f"Error clearing builder session: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500
    
@workflow_builder_bp.route('/api/workflow/builder/check-mode', methods=['POST'])
def check_builder_mode():
    """
    Check if the builder should start in refine mode based on workflow state
    """
    try:
        data = request.get_json()
        workflow_state = data.get('workflow_state')
        
        has_workflow = False
        node_count = 0
        
        if workflow_state:
            nodes = workflow_state.get('nodes', [])
            node_count = len(nodes)
            has_workflow = node_count > 0
        
        return jsonify({
            'status': 'success',
            'should_refine': has_workflow,
            'node_count': node_count,
            'message': f'Workflow has {node_count} nodes' if has_workflow else 'No existing workflow'
        })
        
    except Exception as e:
        logger.error(f"Error checking builder mode: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500

# Optional: Clean up old sessions periodically
def cleanup_old_sessions(max_age_hours=24):
    """
    Remove builder sessions older than max_age_hours
    """
    from datetime import datetime, timedelta
    
    current_time = datetime.now()
    sessions_to_remove = []
    
    for session_id, agent in builder_sessions.items():
        # Parse session_id timestamp if it's in timestamp format
        try:
            session_time = datetime.fromtimestamp(float(session_id))
            if current_time - session_time > timedelta(hours=max_age_hours):
                sessions_to_remove.append(session_id)
        except:
            # If session_id isn't a timestamp, skip cleanup for this session
            pass
    
    for session_id in sessions_to_remove:
        del builder_sessions[session_id]
        logger.info(f"Cleaned up old session {session_id}")
    
    return len(sessions_to_remove)


@workflow_builder_bp.route('/api/workflow/builder/history', methods=['GET'])
def get_builder_conversation_history():
    """
    Get conversation history for a workflow builder session.
    """
    try:
        session_id = request.args.get('session_id')
        
        if not session_id:
            return jsonify({
                'status': 'error',
                'error': 'session_id is required'
            }), 400
        
        if session_id not in builder_sessions:
            return jsonify({
                'status': 'success',
                'session_id': session_id,
                'history': [],
                'message_count': 0
            })
        
        agent = builder_sessions[session_id]
        history = agent.conversation_context.copy() if agent.conversation_context else []
        
        logger.info(f"Retrieved {len(history)} messages for session {session_id}")
        
        return jsonify({
            'status': 'success',
            'session_id': session_id,
            'history': history,
            'message_count': len(history)
        })
        
    except Exception as e:
        logger.error(f"Error retrieving conversation history: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


### TRAINING CAPTURE - START ###
@workflow_builder_bp.route('/api/workflow/builder/finalize-capture', methods=['POST'])
def finalize_training_capture_endpoint():
    """
    Capture training data when workflow is saved.
    
    Extracts conversation history directly from the WorkflowAgent session.
    
    Request body:
    {
        "session_id": "required - the builder session ID",
        "workflow_type": "document_processing",  // optional classification
        "success": true  // whether workflow was successful
        "is_validation_fix": false  // whether this is a validation fix request
    }
    """
    try:
        data = request.get_json()
        session_id = session['session_id']
        
        logger.info(f"Training capture requested for session {session_id}")
        
        if not session_id:
            return jsonify({
                'status': 'error',
                'error': 'session_id is required'
            }), 400
        
        # Get the agent from builder_sessions
        if session_id not in training_builder_sessions:
            logger.warning(f"Session {session_id} not found in training_builder_sessions")
            return jsonify({
                'status': 'error',
                'error': 'Session not found'
            }), 404
        
        agent = training_builder_sessions[session_id]
        
        # Skip capture if validation fixes were needed (dirty data)
        if getattr(agent, 'is_validation_fix', False):
            logger.info(f"Skipping training capture for session {session_id} - validation fixes were required")

            # But DO save the bad output to a separate file for later analysis.
            # These are exactly the cases a deterministic validator needs to catch.
            try:
                workflow_plan = getattr(agent, 'workflow_plan', None)
                # Prefer the FIRST-pass commands (the actual bad output) if we
                # stashed them when validation_fix arrived; otherwise fall back
                # to whatever the agent currently has.
                first_bad = getattr(agent, 'first_bad_commands', None)
                generated_commands = getattr(agent, 'generated_commands', None)
                current_json_commands = getattr(agent, 'current_json_commands', None)
                if first_bad:
                    commands_dict = first_bad
                elif generated_commands:
                    commands_dict = {"action": "build_workflow", "commands": generated_commands}
                elif current_json_commands:
                    commands_dict = current_json_commands
                else:
                    commands_dict = None
                if workflow_plan and commands_dict:
                    val_errors = getattr(agent, 'last_validation_errors', None) or []
                    fix_msgs = getattr(agent, 'fix_messages', None) or []
                    capture_bad_plan_to_commands(
                        workflow_plan=workflow_plan,
                        commands=commands_dict,
                        session_id=session_id,
                        validation_errors=val_errors,
                        fix_messages=fix_msgs,
                    )
            except Exception as bad_e:
                logger.warning(f"bad-output capture failed: {bad_e}")

            try:
                del training_builder_sessions[session_id]
            except:
                pass
            return jsonify({
                'status': 'success',
                'captured': False,
                'reason': 'validation_fixes_required',
                'bad_output_saved': True,
            })
        
        # Capture training data directly from the agent
        result = capture_from_agent(
            agent=agent,
            workflow_type=data.get('workflow_type'),
            success=data.get('success', True)
        )

        # Also capture plan-to-commands format if available
        plan_result = False
        workflow_plan = getattr(agent, 'workflow_plan', None)
        generated_commands = getattr(agent, 'generated_commands', None)
        current_json_commands = getattr(agent, 'current_json_commands', None)
        if workflow_plan and (generated_commands or current_json_commands):
            if generated_commands:
                commands_dict = {"action": "build_workflow", "commands": generated_commands}
            else:
                commands_dict = current_json_commands
            plan_result = capture_plan_to_commands(
                workflow_plan=workflow_plan,
                commands=commands_dict,
                session_id=session_id
            )
            if plan_result:
                logger.info(f"Captured plan-to-commands training data for session {session_id}")

        # Consider success if either capture method worked
        captured = result or plan_result

        try:
            if captured:
                del training_builder_sessions[session_id]
        except:
            pass
        
        logger.info(f"Training capture result: conversation={result}, plan_to_commands={plan_result}")
        
        return jsonify({
            'status': 'success',
            'captured': captured
        })
        
    except Exception as e:
        logger.error(f"Error capturing training data: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


@workflow_builder_bp.route('/api/workflow/builder/training-stats', methods=['GET'])
def get_training_statistics():
    """Get statistics about captured training data."""
    try:
        stats = get_statistics()
        return jsonify({
            'status': 'success',
            'statistics': stats
        })
    except Exception as e:
        logger.error(f"Error getting training statistics: {str(e)}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500
### TRAINING CAPTURE - END ###

