"""
Workflow Executor Service
Standalone Flask application for handling workflow execution
Designed to run as a separate process to prevent blocking the main application
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, rely on environment variables being set

import sys
import json
import traceback
from datetime import datetime
import logging
from logging.handlers import WatchedFileHandler

from flask import Flask, request, jsonify
from flask_cors import CORS, cross_origin

from workflow_execution import WorkflowExecutionEngine
from workflow_recovery_service import initialize_recovery_service
from email_agent_dispatcher import EmailAgentDispatcher, get_dispatcher

from CommonUtils import rotate_logs_on_startup, get_db_connection_string, get_db_connection, Timer, get_log_path
import config as cfg

# Telemetry
from telemetry import (
    capture_exception,
    add_breadcrumb,
    track_workflow_executed
)

# Configure logging
def setup_logging():
    """Configure logging for the workflow executor service"""
    logger = logging.getLogger("AppWorkflowExecutor")
    log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
    log_level = getattr(logging, log_level_name, logging.DEBUG)
    logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler = WatchedFileHandler(filename=os.getenv('APP_WORKFLOW_EXECUTOR_LOG', get_log_path('app_workflow_executor_log.txt')), encoding='utf-8')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger

rotate_logs_on_startup(os.getenv('APP_WORKFLOW_EXECUTOR_LOG', get_log_path('app_workflow_executor_log.txt')))

logger = setup_logging()

# Create Flask app
app = Flask(__name__)
CORS(app, supports_credentials=True)
app.config['SECRET_KEY'] = cfg.SECRET_KEY

# Initialize the workflow execution engine
workflow_engine = None

# Email dispatcher instance
email_dispatcher = None

def initialize_workflow_engine():
    """Initialize the workflow execution engine"""
    global workflow_engine
    
    try:
        connection_string = get_db_connection_string()
        workflow_engine = WorkflowExecutionEngine(connection_string)
        logger.info("Workflow execution engine initialized successfully")
        
        # Initialize recovery service
        try:
            initialize_recovery_service(app, workflow_engine)
            logger.info("Workflow recovery service initialized")
        except Exception as e:
            logger.warning(f"Could not initialize recovery service: {e}")
        
        return True
    except Exception as e:
        logger.error(f"Failed to initialize workflow engine: {e}")
        return False


# Initialize on module load
initialize_workflow_engine()


def initialize_email_dispatcher():
    """Initialize the email agent dispatcher if enabled"""
    global email_dispatcher
    
    # Check if email dispatcher is enabled (from config.py)
    enabled = getattr(cfg, 'EMAIL_DISPATCHER_ENABLED', False)
    
    if not enabled:
        logger.info("Email dispatcher is disabled (set EMAIL_DISPATCHER_ENABLED=True in config.py to enable)")
        return False
    
    # Check if enterprise features are enabled for this tenant
    try:
        from admin_tier_usage import get_cached_tier_data
        with app.app_context():
            tier_data = get_cached_tier_data()
        if tier_data:
            tier_features = tier_data.get('tier_features', {})
            if not tier_features.get('enterprise_features_enabled', False):
                logger.info("Email dispatcher not started - enterprise features not enabled for this tenant")
                return False
    except Exception as e:
        logger.warning(f"Could not verify enterprise features, proceeding with dispatcher: {e}")
    
    try:
        # Get poll interval from config, default to 60 seconds
        poll_interval = getattr(cfg, 'EMAIL_POLL_INTERVAL', 60)
        
        # Enforce minimum of 60 seconds to prevent overwhelming Cloud API
        if poll_interval < 60:
            logger.warning(f"EMAIL_POLL_INTERVAL of {poll_interval}s is below minimum. Using 60s.")
            poll_interval = 60
        
        # Pass Flask app for context management in background thread
        email_dispatcher = EmailAgentDispatcher(poll_interval=poll_interval, flask_app=app)
        email_dispatcher.start()
        logger.info(f"Email dispatcher initialized and started (poll_interval={poll_interval}s)")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize email dispatcher: {e}")
        return False


initialize_email_dispatcher()


# ============================================================================
# Health Check
# ============================================================================

@app.route('/health', methods=['GET'])
@cross_origin()
def health_check():
    """Health check endpoint."""
    status = {
        "status": "healthy",
        "service": "workflow-executor",
        "timestamp": datetime.now().isoformat(),
        "workflow_engine": workflow_engine is not None,
        "active_executions": len(workflow_engine.active_executions) if workflow_engine else 0,
        "email_dispatcher": {
            "enabled": email_dispatcher is not None,
            "running": email_dispatcher.is_running() if email_dispatcher else False,
            "stats": email_dispatcher.get_stats() if email_dispatcher else None
        }
    }
    return jsonify(status)


# ============================================================================
# Workflow Execution Endpoints
# ============================================================================

@app.route('/api/workflow/run', methods=['POST'])
@cross_origin()
def run_workflow():
    """Run a workflow with the execution engine"""
    try:
        logger.info("Received workflow run request")
        timer = Timer()
        timer.start()

        data = request.json
        if not data:
            return jsonify({
                "status": "error",
                "message": "Missing request data"
            }), 400
        
        workflow_id = data.get('workflow_id')
        if not workflow_id:
            return jsonify({
                "status": "error",
                "message": "workflow_id is required"
            }), 400
        
        # Check if workflow_data was provided directly
        workflow_data = data.get('workflow_data')
        
        if not workflow_data:
            # Get workflow definition from database
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            cursor.execute("SELECT workflow_data FROM Workflows WHERE id = ?", int(workflow_id))
            row = cursor.fetchone()
            
            if not row:
                cursor.close()
                conn.close()
                return jsonify({
                    "status": "error",
                    "message": f"Workflow with ID {workflow_id} not found"
                }), 404
            
            workflow_data = json.loads(row[0])
            cursor.close()
            conn.close()

        # =====================================================================
        # NEW: Inject runtime variables into workflow_data
        # =====================================================================
        runtime_variables = data.get('variables', {})
        if runtime_variables:
            logger.info(f"Injecting {len(runtime_variables)} runtime variables into workflow")
            
            # Ensure variables dict exists in workflow_data
            if 'variables' not in workflow_data:
                workflow_data['variables'] = {}
            
            # Inject each runtime variable
            for var_name, var_value in runtime_variables.items():
                # Convert value to string if not already (workflow variables are strings)
                str_value = str(var_value) if var_value is not None else ''
                
                workflow_data['variables'][var_name] = {
                    'name': var_name,
                    'type': 'string',
                    'defaultValue': str_value
                }
                logger.debug(f"  Injected variable: {var_name} = {str_value[:100]}...")
        # =====================================================================
        
        initiator = data.get('initiator', 'api')
        
        logger.info(f"Starting workflow {workflow_id} for initiator {initiator}")
        execution_id = workflow_engine.start_workflow(workflow_id, workflow_data, initiator)
        logger.info(f"Workflow execution started with ID: {execution_id}")

        timer.stop()
        try:
            track_workflow_executed(workflow_id, True, timer.elapsed_ms, len(workflow_data.get('nodes', [])))
        except:
            logger.warning(f"Failed to track workflow executed")
        
        return jsonify({
            "status": "success",
            "message": "Workflow execution started",
            "execution_id": execution_id
        })
        
    except Exception as e:
        logger.error(f"Error starting workflow execution: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "status": "error",
            "message": f"Error starting workflow execution: {str(e)}"
        }), 500


@app.route('/api/workflow/executions/<execution_id>/pause', methods=['POST'])
@cross_origin()
def pause_workflow_execution(execution_id):
    """Pause a workflow execution"""
    try:
        logger.info(f"Pause request for execution: {execution_id}")
        result = workflow_engine.pause_workflow(execution_id)
        
        if result:
            return jsonify({"status": "success", "message": "Workflow paused successfully"})
        return jsonify({"status": "error", "message": "Failed to pause workflow"}), 400
            
    except Exception as e:
        logger.error(f"Error pausing workflow: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/workflow/executions/<execution_id>/resume', methods=['POST'])
@cross_origin()
def resume_workflow_execution(execution_id):
    """Resume a paused workflow execution"""
    try:
        logger.info(f"Resume request for execution: {execution_id}")
        result = workflow_engine.resume_workflow(execution_id)
        
        if result:
            return jsonify({"status": "success", "message": "Workflow resumed successfully"})
        return jsonify({"status": "error", "message": "Failed to resume workflow"}), 400
            
    except Exception as e:
        logger.error(f"Error resuming workflow: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/workflow/executions/<execution_id>/cancel', methods=['POST'])
@cross_origin()
def cancel_workflow_execution(execution_id):
    """Cancel a workflow execution"""
    try:
        logger.info(f"Cancel request for execution: {execution_id}")
        result = workflow_engine.cancel_workflow(execution_id)
        
        if result:
            return jsonify({"status": "success", "message": "Workflow cancelled successfully"})
        return jsonify({"status": "error", "message": "Failed to cancel workflow"}), 400
            
    except Exception as e:
        logger.error(f"Error cancelling workflow: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/workflow/executions/<execution_id>/status', methods=['GET'])
@cross_origin()
def get_workflow_status(execution_id):
    """Get workflow execution status from in-memory state"""
    try:
        if execution_id in workflow_engine._active_executions:
            state = workflow_engine._active_executions[execution_id]
            return jsonify({
                "status": "success",
                "execution_id": execution_id,
                "workflow_status": state.get('status', 'Unknown'),
                "current_node": state.get('current_node'),
                "paused": state.get('paused', False),
                "in_memory": True
            })
        
        # Check database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        cursor.execute("SELECT status, workflow_name FROM WorkflowExecutions WHERE execution_id = ?", execution_id)
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if row:
            return jsonify({
                "status": "success",
                "execution_id": execution_id,
                "workflow_status": row[0],
                "workflow_name": row[1],
                "in_memory": False
            })
        
        return jsonify({"status": "error", "message": "Execution not found"}), 404
                
    except Exception as e:
        logger.error(f"Error getting workflow status: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/workflow/executions/active', methods=['GET'])
@cross_origin()
def get_active_executions():
    """Get list of active workflow executions"""
    try:
        active = []
        for exec_id, state in workflow_engine._active_executions.items():
            active.append({
                "execution_id": exec_id,
                "workflow_id": state.get('workflow_id'),
                "workflow_name": state.get('workflow_name'),
                "status": state.get('status'),
                "current_node": state.get('current_node'),
                "started_at": state.get('started_at'),
                "paused": state.get('paused', False)
            })
        
        return jsonify({
            "status": "success",
            "active_executions": active,
            "count": len(active)
        })
        
    except Exception as e:
        logger.error(f"Error getting active executions: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/workflow/log', methods=['POST'])
@cross_origin()
def log_workflow_event():
    """Log an event for a workflow execution"""
    try:
        data = request.json
        execution_id = data.get('execution_id')
        
        if not execution_id:
            return jsonify({"status": "error", "message": "execution_id is required"}), 400
        
        workflow_engine.log_execution(
            execution_id,
            data.get('node_id'),
            data.get('level', 'info'),
            data.get('message', ''),
            data.get('details')
        )
        
        return jsonify({"status": "success", "message": "Event logged"})
        
    except Exception as e:
        logger.error(f"Error logging workflow event: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ============================================================================
# Email Dispatcher Endpoints
# ============================================================================

@app.route('/api/email-dispatcher/status', methods=['GET'])
@cross_origin()
def get_email_dispatcher_status():
    """Get email dispatcher status."""
    global email_dispatcher
    
    if email_dispatcher is None:
        return jsonify({
            "status": "not_initialized",
            "running": False,
            "message": "Email dispatcher has not been initialized"
        })
    
    return jsonify({
        "status": "initialized",
        "running": email_dispatcher.is_running(),
        "stats": email_dispatcher.get_stats()
    })


@app.route('/api/email-dispatcher/start', methods=['POST'])
@cross_origin()
def start_email_dispatcher():
    """Start the email dispatcher."""
    global email_dispatcher
    
    try:
        # Check if enterprise features are enabled
        try:
            from admin_tier_usage import get_cached_tier_data
            tier_data = get_cached_tier_data()
            if tier_data:
                tier_features = tier_data.get('tier_features', {})
                if not tier_features.get('enterprise_features_enabled', False):
                    return jsonify({
                        "status": "error",
                        "message": "Email processing requires enterprise features"
                    }), 403
        except Exception as e:
            logger.warning(f"Could not verify enterprise features: {e}")
        
        if email_dispatcher is None:
            # Initialize if not already done
            poll_interval = getattr(cfg, 'EMAIL_POLL_INTERVAL', 60)
            if poll_interval < 60:
                poll_interval = 60
            email_dispatcher = EmailAgentDispatcher(poll_interval=poll_interval, flask_app=app)
        
        if email_dispatcher.is_running():
            return jsonify({
                "status": "success",
                "message": "Email dispatcher already running",
                "stats": email_dispatcher.get_stats()
            })
        
        email_dispatcher.start()
        logger.info("Email dispatcher started via API")
        
        return jsonify({
            "status": "success",
            "message": "Email dispatcher started",
            "stats": email_dispatcher.get_stats()
        })
        
    except Exception as e:
        logger.error(f"Error starting email dispatcher: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/email-dispatcher/stop', methods=['POST'])
@cross_origin()
def stop_email_dispatcher():
    """Stop the email dispatcher."""
    global email_dispatcher
    
    try:
        if email_dispatcher is None:
            return jsonify({
                "status": "success",
                "message": "Email dispatcher not initialized"
            })
        
        if not email_dispatcher.is_running():
            return jsonify({
                "status": "success",
                "message": "Email dispatcher already stopped"
            })
        
        email_dispatcher.stop()
        logger.info("Email dispatcher stopped via API")
        
        return jsonify({
            "status": "success",
            "message": "Email dispatcher stopped"
        })
        
    except Exception as e:
        logger.error(f"Error stopping email dispatcher: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/email-dispatcher/poll-now', methods=['POST'])
@cross_origin()
def trigger_email_poll():
    """Manually trigger an email poll cycle (for testing)."""
    global email_dispatcher
    
    try:
        if email_dispatcher is None:
            return jsonify({
                "status": "error",
                "message": "Email dispatcher not initialized"
            }), 400
        
        # Call poll directly (outside the normal loop)
        email_dispatcher._poll_and_process()
        
        return jsonify({
            "status": "success",
            "message": "Poll cycle completed",
            "stats": email_dispatcher.get_stats()
        })
        
    except Exception as e:
        logger.error(f"Error in manual poll: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500
    