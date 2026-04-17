"""
AI Hub Client Telemetry Module
==============================

Captures crashes and usage events, sends to your Cloud API for forwarding to Sentry.

This module integrates with your existing AI Hub architecture:
- Uses config.py for configuration
- Works with Flask-Login for user context
- Respects your existing logging patterns
- Uses admin_tier_usage for tier information

Installation:
    pip install requests --break-system-packages
    (requests is likely already installed)

Configuration (add to config.py):
    # Telemetry Configuration
    TELEMETRY_ENABLED = True
    CLOUD_API_BASE_URL = os.getenv('AI_HUB_API_URL', '')

Integration (in app.py):
    from telemetry import init_telemetry, telemetry_blueprint
    
    # After Flask app creation (after line ~283)
    init_telemetry(app)
    
    # Register the settings UI blueprint
    app.register_blueprint(telemetry_blueprint)
"""

import os
import sys
import json
import uuid
import queue
import atexit
import logging
from logging.handlers import WatchedFileHandler
import platform
import threading
import traceback
import hashlib
from datetime import datetime
from functools import wraps
from typing import Optional, Dict, Any, Callable, List
from contextlib import contextmanager
from CommonUtils import rotate_logs_on_startup, get_log_path
import requests
import app_config
# Import your existing config
import config as cfg

# Configure logging
def setup_logging():
    """Configure logging for the workflow execution"""
    logger = logging.getLogger("Telemetry")
    log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
    log_level = getattr(logging, log_level_name, logging.DEBUG)
    logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler = WatchedFileHandler(filename=os.getenv('TELEMETRY_LOG', get_log_path('telemetry_log.txt')), encoding='utf-8')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger

rotate_logs_on_startup(os.getenv('TELEMETRY_LOG', get_log_path('telemetry_log.txt')))

logger = setup_logging()



# =============================================================================
# Configuration
# =============================================================================

class TelemetryConfig:
    """Centralized telemetry configuration"""
    
    def __init__(self):
        # Cloud API base URL (your existing API endpoint)
        self.cloud_api_base = os.getenv('AI_HUB_API_URL', '').rstrip('/')
        
        # Telemetry endpoints (relative to cloud API)
        self.crash_endpoint = f"{self.cloud_api_base}/api/telemetry/crash" if self.cloud_api_base else ''
        self.events_endpoint = f"{self.cloud_api_base}/api/telemetry/events" if self.cloud_api_base else ''
        self.health_endpoint = f"{self.cloud_api_base}/api/telemetry/health" if self.cloud_api_base else ''
        
        # App metadata
        self.app_version = app_config.APP_VERSION or '0.0.0'
        self.app_environment = os.getenv('APP_ENVIRONMENT', 'production')
        self.tenant_id = os.getenv('API_KEY', '')
        
        # Enable/disable (can be controlled via config or environment)
        self.telemetry_enabled = os.getenv('TELEMETRY_ENABLED', 'true').lower() in ['true', '1', 'yes']
        
        # Also check config.py if it has a TELEMETRY_ENABLED setting
        if hasattr(cfg, 'TELEMETRY_ENABLED'):
            self.telemetry_enabled = cfg.TELEMETRY_ENABLED
        
        # Queue settings
        self.queue_max_size = 1000
        self.flush_interval = 60  # seconds
        self.batch_size = 50
        self.request_timeout = 10  # seconds
    
    def is_configured(self) -> bool:
        """Check if telemetry is properly configured"""
        return bool(self.cloud_api_base and self.telemetry_enabled)


telemetry_config = TelemetryConfig()


# =============================================================================
# Consent Management
# =============================================================================

class ConsentManager:
    """
    Manages user consent for telemetry collection.
    Consent is stored locally on the user's machine.
    """
    
    def __init__(self):
        # Store consent file in user's home directory
        self.consent_file = os.path.join(os.path.expanduser('~'), '.aihub_telemetry_consent.json')
        self._consent_state = self._load_consent()
    
    def _load_consent(self) -> Dict[str, Any]:
        """Load consent state from file"""
        try:
            if os.path.exists(self.consent_file):
                with open(self.consent_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.debug(f"Could not load consent file: {e}")
        
        # Default: crash reporting on, analytics off
        return {
            'crash_reporting': True,
            'usage_analytics': False,
            'performance_monitoring': False,
            'consent_timestamp': None,
            'consent_version': '1.0'
        }
    
    def _save_consent(self):
        """Persist consent state to file"""
        try:
            with open(self.consent_file, 'w') as f:
                json.dump(self._consent_state, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save consent file: {e}")
    
    def get_consent(self, category: str) -> bool:
        """Check if user has consented to a category"""
        return self._consent_state.get(category, False)
    
    def set_consent(self, category: str, consented: bool):
        """Update consent for a category"""
        self._consent_state[category] = consented
        self._consent_state['consent_timestamp'] = datetime.utcnow().isoformat()
        self._save_consent()
        logger.info(f"Telemetry consent updated: {category}={consented}")
    
    def set_all_consent(self, crash_reporting: bool, usage_analytics: bool, 
                        performance_monitoring: bool):
        """Update all consent settings"""
        self._consent_state.update({
            'crash_reporting': crash_reporting,
            'usage_analytics': usage_analytics,
            'performance_monitoring': performance_monitoring,
            'consent_timestamp': datetime.utcnow().isoformat()
        })
        self._save_consent()
    
    def get_all_consent(self) -> Dict[str, Any]:
        """Get all consent settings"""
        return self._consent_state.copy()
    
    def has_made_choice(self) -> bool:
        """Check if user has made a consent choice"""
        return self._consent_state.get('consent_timestamp') is not None


consent_manager = ConsentManager()


# =============================================================================
# Privacy Utilities
# =============================================================================

def _hash_value(value: str, length: int = 16) -> str:
    """Create a one-way hash of a value"""
    if not value:
        return 'unknown'
    return hashlib.sha256(value.encode()).hexdigest()[:length]


def _hash_tenant_id(tenant_id: str) -> str:
    """Hash tenant ID for privacy"""
    return _hash_value(tenant_id, 12)


def _scrub_pii(text: str) -> str:
    """Remove potential PII from text"""
    import re
    
    if not text:
        return text
    
    # Email addresses
    text = re.sub(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', '[EMAIL]', text)
    
    # IP addresses
    text = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '[IP]', text)
    
    # API keys (long alphanumeric strings)
    text = re.sub(r'[a-zA-Z0-9]{32,}', '[KEY]', text)
    
    # Connection string passwords
    text = re.sub(r'(password|pwd|pass|secret|key)=[^;\s]+', r'\1=[REDACTED]', text, flags=re.IGNORECASE)
    
    # File paths that might contain usernames
    text = re.sub(r'C:\\Users\\[^\\]+', r'C:\\Users\\[USER]', text)
    text = re.sub(r'/home/[^/]+', r'/home/[USER]', text)
    
    return text


def _scrub_dict(d: Optional[Dict]) -> Dict:
    """Scrub potential PII from dictionary"""
    if not d:
        return {}
    
    sensitive_keys = {'password', 'pwd', 'secret', 'key', 'token', 'api_key', 
                      'email', 'phone', 'address', 'ssn', 'credit_card',
                      'license_key', 'connection_string', 'conn_str'}
    
    scrubbed = {}
    for key, value in d.items():
        key_lower = key.lower()
        
        if any(s in key_lower for s in sensitive_keys):
            scrubbed[key] = '[REDACTED]'
        elif isinstance(value, str):
            scrubbed[key] = _scrub_pii(value)
        elif isinstance(value, dict):
            scrubbed[key] = _scrub_dict(value)
        elif isinstance(value, list):
            scrubbed[key] = [_scrub_pii(str(v)) if isinstance(v, str) else v for v in value[:10]]
        else:
            scrubbed[key] = value
    
    return scrubbed


def _get_current_tier() -> str:
    """Get current subscription tier from your tier system"""
    try:
        from admin_tier_usage import get_tier_cache_status, _tier_cache
        
        # Try to get cached tier info
        if _tier_cache.get('data'):
            subscription = _tier_cache['data'].get('subscription', {})
            return subscription.get('current_tier', 'unknown')
    except:
        pass
    
    return 'unknown'


# =============================================================================
# Breadcrumb Storage (tracks what happened before a crash)
# =============================================================================

_breadcrumbs: List[Dict[str, Any]] = []
_breadcrumbs_lock = threading.Lock()
_max_breadcrumbs = 50

# User context for crash reports
_user_context: Dict[str, Any] = {}


def add_breadcrumb(message: str, category: str = "app", level: str = "info",
                   data: Optional[Dict] = None):
    """
    Add a breadcrumb to trace the path to an error.
    
    Args:
        message: What happened
        category: Category (ui, http, navigation, agent, workflow, etc.)
        level: Severity (debug, info, warning, error)
        data: Additional context data
    """
    global _breadcrumbs
    
    if not telemetry_config.telemetry_enabled:
        return
    
    with _breadcrumbs_lock:
        breadcrumb = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'message': _scrub_pii(message),
            'category': category,
            'level': level,
            'data': _scrub_dict(data) if data else None
        }
        
        _breadcrumbs.append(breadcrumb)
        
        # Keep only the last N breadcrumbs
        if len(_breadcrumbs) > _max_breadcrumbs:
            _breadcrumbs = _breadcrumbs[-_max_breadcrumbs:]


def set_user_context(user_id: Optional[int] = None, username: Optional[str] = None,
                     role: Optional[int] = None):
    """
    Set user context for crash reports (anonymized).
    
    Call this after login to associate crashes with user sessions.
    """
    global _user_context
    
    _user_context = {}
    if user_id:
        _user_context['id'] = _hash_value(str(user_id))
    if role is not None:
        _user_context['role'] = str(role)


def clear_user_context():
    """Clear user context on logout"""
    global _user_context
    _user_context = {}


# =============================================================================
# Crash Reporting
# =============================================================================

def capture_exception(error: Exception, extra_context: Optional[Dict] = None):
    """
    Capture an exception and send to Cloud API.
    
    Args:
        error: The exception to capture
        extra_context: Additional context to include
    """
    if not telemetry_config.is_configured():
        return
    
    if not consent_manager.get_consent('crash_reporting'):
        return
    
    try:
        # Extract exception info
        exc_type = type(error).__name__
        exc_value = str(error)
        exc_traceback = traceback.format_exception(type(error), error, error.__traceback__)
        
        # Build crash report
        crash_data = {
            'exception': {
                'type': exc_type,
                'value': _scrub_pii(exc_value),
                'stacktrace': [_scrub_pii(line) for line in exc_traceback]
            },
            'breadcrumbs': _breadcrumbs.copy(),
            'context': _scrub_dict(extra_context) if extra_context else {},
            'user': _user_context.copy() if _user_context else None,
            'app_version': telemetry_config.app_version,
            'tenant_hash': _hash_tenant_id(telemetry_config.tenant_id),
            'tier': _get_current_tier(),
            'os': f"{platform.system()} {platform.release()}",
            'python_version': platform.python_version(),
            'timestamp': datetime.utcnow().isoformat() + 'Z'
        }
        
        # Send in background thread
        threading.Thread(
            target=_send_crash_report,
            args=(crash_data,),
            daemon=True
        ).start()
        
    except Exception as e:
        logger.error(f"Failed to capture exception: {e}")


def _send_crash_report(crash_data: Dict[str, Any]):
    """Send crash report to Cloud API (runs in background thread)"""
    try:
        response = requests.post(
            telemetry_config.crash_endpoint,
            json=crash_data,
            headers={'Content-Type': 'application/json'},
            timeout=telemetry_config.request_timeout
        )
        
        if response.status_code == 200:
            logger.debug("Crash report sent successfully")
        else:
            logger.warning(f"Crash report failed: {response.status_code}")
            
    except requests.RequestException as e:
        logger.debug(f"Failed to send crash report (network): {e}")


def capture_message(message: str, level: str = "error", extra_context: Optional[Dict] = None):
    """
    Capture a message as a crash event (for important non-exception events).
    """
    if not telemetry_config.is_configured():
        return
    
    if not consent_manager.get_consent('crash_reporting'):
        return
    
    try:
        crash_data = {
            'exception': {
                'type': 'Message',
                'value': _scrub_pii(message),
                'stacktrace': []
            },
            'breadcrumbs': _breadcrumbs.copy(),
            'context': _scrub_dict(extra_context) if extra_context else {},
            'user': _user_context.copy() if _user_context else None,
            'app_version': telemetry_config.app_version,
            'tenant_hash': _hash_tenant_id(telemetry_config.tenant_id),
            'tier': _get_current_tier(),
            'os': f"{platform.system()} {platform.release()}",
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'level': level
        }
        
        threading.Thread(
            target=_send_crash_report,
            args=(crash_data,),
            daemon=True
        ).start()
        
    except Exception as e:
        logger.error(f"Failed to capture message: {e}")


# =============================================================================
# Global Exception Hook
# =============================================================================

_original_excepthook = sys.excepthook

def _telemetry_excepthook(exc_type, exc_value, exc_traceback):
    """Global exception hook to capture unhandled exceptions"""
    if consent_manager.get_consent('crash_reporting'):
        try:
            capture_exception(exc_value)
        except:
            pass
    
    # Call original hook
    _original_excepthook(exc_type, exc_value, exc_traceback)


# =============================================================================
# Usage Analytics (Optional)
# =============================================================================

class TelemetryClient:
    """
    Client for usage analytics.
    Batches events and sends to Cloud API.
    """
    
    def __init__(self):
        self._queue = queue.Queue(maxsize=telemetry_config.queue_max_size)
        self._session_id = str(uuid.uuid4())
        self._event_sequence = 0
        self._lock = threading.Lock()
        
        # Start background sender thread
        self._stop_event = threading.Event()
        self._sender_thread = None
        
        # Register shutdown handler
        atexit.register(self._shutdown)
    
    def _ensure_sender_running(self):
        """Start sender thread if not running"""
        if self._sender_thread is None or not self._sender_thread.is_alive():
            self._sender_thread = threading.Thread(target=self._sender_loop, daemon=True)
            self._sender_thread.start()
    
    def track_event(self, event_type: str, properties: Optional[Dict] = None,
                    metrics: Optional[Dict] = None):
        """Track a usage event"""
        if not telemetry_config.is_configured():
            return
        
        if not consent_manager.get_consent('usage_analytics'):
            return
        
        self._ensure_sender_running()
        
        with self._lock:
            self._event_sequence += 1
            sequence = self._event_sequence
        
        event = {
            'event_type': event_type,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'session_id': self._session_id,
            'sequence': sequence,
            'app_version': telemetry_config.app_version,
            'tenant_hash': _hash_tenant_id(telemetry_config.tenant_id),
            'tier': _get_current_tier(),
            'os': f"{platform.system()} {platform.release()}",
            'properties': _scrub_dict(properties) if properties else {},
            'metrics': metrics or {}
        }
        
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            logger.debug("Telemetry queue full - dropping event")
    
    def _sender_loop(self):
        """Background loop that batches and sends events"""
        while not self._stop_event.is_set():
            try:
                self._flush_batch()
            except Exception as e:
                logger.debug(f"Telemetry sender error: {e}")
            
            self._stop_event.wait(timeout=telemetry_config.flush_interval)
    
    def _flush_batch(self):
        """Collect and send a batch of events"""
        batch = []
        while len(batch) < telemetry_config.batch_size:
            try:
                event = self._queue.get_nowait()
                batch.append(event)
            except queue.Empty:
                break
        
        if not batch:
            return
        
        try:
            response = requests.post(
                telemetry_config.events_endpoint,
                json={'events': batch},
                headers={'Content-Type': 'application/json'},
                timeout=telemetry_config.request_timeout
            )
            
            if response.status_code != 200:
                logger.debug(f"Telemetry send failed: {response.status_code}")
        
        except requests.RequestException as e:
            logger.debug(f"Telemetry network error: {e}")
    
    def _shutdown(self):
        """Graceful shutdown"""
        self._stop_event.set()
        if self._sender_thread and self._sender_thread.is_alive():
            self._sender_thread.join(timeout=5)


# Global telemetry client instance
telemetry = TelemetryClient()


# =============================================================================
# Convenience Tracking Functions
# =============================================================================

def track_login(success: bool, user_id: Optional[int] = None, method: str = "password"):
    """Track login attempts"""
    if success and user_id:
        set_user_context(user_id=user_id)
    
    telemetry.track_event(
        event_type="login_attempt",
        properties={"success": str(success), "method": method}
    )
    add_breadcrumb(
        f"Login {'successful' if success else 'failed'}",
        category="auth",
        level="info" if success else "warning"
    )


def track_logout():
    """Track logout"""
    telemetry.track_event(event_type="logout")
    clear_user_context()
    add_breadcrumb("User logged out", category="auth")


def track_agent_created(agent_id: int, agent_type: str = "general"):
    """Track agent creation"""
    telemetry.track_event(
        event_type="agent_created",
        properties={"agent_type": agent_type}
    )
    add_breadcrumb(f"Agent created: {agent_type}", category="agent")


def track_agent_executed(agent_id: int, success: bool, duration_ms: float,
                         input_tokens: int = 0, output_tokens: int = 0):
    """Track agent execution"""
    telemetry.track_event(
        event_type="agent_executed",
        properties={"success": str(success)},
        metrics={
            "duration_ms": duration_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens
        }
    )


def track_workflow_executed(workflow_id: int, success: bool, duration_ms: float,
                            node_count: int = 0):
    """Track workflow execution"""
    telemetry.track_event(
        event_type="workflow_executed",
        properties={"success": str(success)},
        metrics={
            "duration_ms": duration_ms,
            "node_count": node_count
        }
    )
    add_breadcrumb(
        f"Workflow executed: {'success' if success else 'failed'}",
        category="workflow",
        level="info" if success else "error"
    )


def track_document_processed(doc_type: str, page_count: int, success: bool):
    """Track document processing"""
    telemetry.track_event(
        event_type="document_processed",
        properties={"doc_type": doc_type, "success": str(success)},
        metrics={"page_count": page_count}
    )


def track_tier_limit_hit(limit_type: str, current_value: int, max_value: int):
    """Track when users hit tier limits (upgrade opportunity)"""
    telemetry.track_event(
        event_type="tier_limit_hit",
        properties={"limit_type": limit_type},
        metrics={
            "current_value": current_value,
            "max_value": max_value,
            "utilization_pct": (current_value / max_value * 100) if max_value > 0 else 0
        }
    )
    add_breadcrumb(f"Hit tier limit: {limit_type}", category="tier", level="warning")


def track_feature_usage(feature: str, action: str = "used"):
    """Track feature usage"""
    telemetry.track_event(
        event_type="feature_usage",
        properties={"feature": feature, "action": action}
    )


def track_error(error_type: str, component: str, is_user_facing: bool = False):
    """Track error occurrences (separate from crash reports)"""
    telemetry.track_event(
        event_type="error_occurrence",
        properties={
            "error_type": error_type,
            "component": component,
            "user_facing": str(is_user_facing)
        }
    )

def track_data_agent_created(agent_id: int):
    """Track data agent creation"""
    telemetry.track_event(
        event_type="agent_created",
        properties={"agent_type": "data"}
    )
    add_breadcrumb(f"Data agent created", category="agent")


def track_workflow_created(workflow_id: int, is_update: bool = False):
    """Track workflow creation/update"""
    action = "updated" if is_update else "created"
    telemetry.track_event(
        event_type="workflow_created",
        properties={"action": action}
    )
    add_breadcrumb(f"Workflow {action}", category="workflow")


def track_document_job_created(job_id: int):
    """Track document job creation"""
    telemetry.track_event(
        event_type="document_job_created",
        properties={"job_type": "document_processing"}
    )
    add_breadcrumb(f"Document job created", category="document")


def track_document_job_executed(job_id: int, execution_id: int = None):
    """Track document job execution start"""
    telemetry.track_event(
        event_type="document_job_executed",
        properties={"job_type": "document_processing"}
    )
    add_breadcrumb(f"Document job executed", category="document")


def track_custom_tool_created(tool_name: str):
    """Track custom tool creation"""
    telemetry.track_event(
        event_type="custom_tool_created",
        properties={"tool_type": "custom"}
    )
    add_breadcrumb(f"Custom tool created: {tool_name}", category="tool")


def track_environment_created(environment_id: str, python_version: str = None):
    """Track environment creation"""
    properties = {"environment_type": "custom"}
    if python_version:
        properties["python_version"] = python_version
    
    telemetry.track_event(
        event_type="environment_created",
        properties=properties
    )
    add_breadcrumb(f"Environment created", category="environment")


def track_environment_cloned(source_env_id: str, new_env_id: str):
    """Track environment cloning"""
    telemetry.track_event(
        event_type="environment_cloned",
        properties={"operation": "clone"}
    )
    add_breadcrumb(f"Environment cloned", category="environment")


def track_environment_deleted(environment_id: str):
    """Track environment deletion"""
    telemetry.track_event(
        event_type="environment_deleted",
        properties={"operation": "delete"}
    )
    add_breadcrumb(f"Environment deleted", category="environment")


def track_agent_exported(agent_id: int, include_knowledge: bool = False):
    """Track agent export"""
    telemetry.track_event(
        event_type="agent_exported",
        properties={
            "export_type": "full" if include_knowledge else "basic"
        }
    )
    add_breadcrumb(f"Agent exported", category="agent")


def track_agent_imported(agent_id: int, tools_imported: int = 0, knowledge_imported: int = 0):
    """Track agent import"""
    telemetry.track_event(
        event_type="agent_imported",
        properties={"import_operation": "complete"},
        metrics={
            "tools_imported": tools_imported,
            "knowledge_imported": knowledge_imported
        }
    )
    add_breadcrumb(f"Agent imported", category="agent")


def track_tool_exported(tool_name: str):
    """Track custom tool export"""
    telemetry.track_event(
        event_type="tool_exported",
        properties={"tool_type": "custom"}
    )
    add_breadcrumb(f"Tool exported: {tool_name}", category="tool")

# =============================================================================
# Decorators
# =============================================================================

def track_errors(component: str = "unknown"):
    """
    Decorator to capture exceptions and optionally re-raise them.
    
    Usage:
        @track_errors(component="agent_execution")
        def execute_agent(...):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                capture_exception(e, {'component': component, 'function': func.__name__})
                raise
        return wrapper
    return decorator


# =============================================================================
# Flask Integration
# =============================================================================

def init_telemetry(app):
    """
    Initialize telemetry for a Flask application.
    
    Call this after Flask app creation:
        app = Flask(__name__)
        init_telemetry(app)
    
    Args:
        app: Flask application instance
    """
    if not telemetry_config.is_configured():
        print(f"[Telemetry] Not configured (AI_HUB_API_URL: {telemetry_config.cloud_api_base or 'not set'})")
        return
    
    # Install global exception hook
    sys.excepthook = _telemetry_excepthook
    
    # Track app startup
    telemetry.track_event(
        event_type="app_startup",
        properties={
            "version": telemetry_config.app_version,
            "environment": telemetry_config.app_environment
        }
    )
    add_breadcrumb("Application started", category="app", level="info")
    
    # Add request context
    @app.before_request
    def telemetry_before_request():
        from flask import g
        g.telemetry_request_start = datetime.utcnow()
    
    @app.after_request
    def telemetry_after_request(response):
        try:
            from flask import g, request
            from flask_login import current_user
            
            # Set user context if logged in
            if current_user and hasattr(current_user, 'is_authenticated') and current_user.is_authenticated:
                set_user_context(
                    user_id=current_user.id if hasattr(current_user, 'id') else None,
                    role=current_user.role if hasattr(current_user, 'role') else None
                )
        except:
            pass
        
        return response
    
    print(f"[Telemetry] Initialized (version: {telemetry_config.app_version})")


# =============================================================================
# Flask Blueprint for Settings UI
# =============================================================================

from flask import Blueprint, render_template, jsonify, request as flask_request

telemetry_blueprint = Blueprint('telemetry', __name__, url_prefix='/settings')


@telemetry_blueprint.route('/telemetry')
def telemetry_settings():
    """Render telemetry settings page"""
    return render_template('telemetry_settings.html')


@telemetry_blueprint.route('/api/telemetry/consent', methods=['GET'])
def get_consent():
    """Get current consent settings"""
    return jsonify({
        'status': 'success',
        'consent': consent_manager.get_all_consent(),
        'telemetry_configured': telemetry_config.is_configured()
    })


@telemetry_blueprint.route('/api/telemetry/consent', methods=['POST'])
def update_consent():
    """Update consent settings"""
    try:
        data = flask_request.get_json()
        
        consent_manager.set_all_consent(
            crash_reporting=data.get('crash_reporting', True),
            usage_analytics=data.get('usage_analytics', False),
            performance_monitoring=data.get('performance_monitoring', False)
        )
        
        return jsonify({
            'status': 'success',
            'consent': consent_manager.get_all_consent()
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400


@telemetry_blueprint.route('/api/telemetry/health', methods=['GET'])
def local_health():
    """Check telemetry connectivity"""
    if not telemetry_config.is_configured():
        return jsonify({
            'status': 'not_configured',
            'cloud_api_url': telemetry_config.cloud_api_base or 'not set'
        })
    
    try:
        response = requests.get(
            telemetry_config.health_endpoint,
            timeout=5
        )
        if response.status_code == 200:
            return jsonify({
                'status': 'connected',
                'cloud_status': response.json()
            })
        else:
            return jsonify({
                'status': 'error',
                'http_status': response.status_code
            })
    except requests.RequestException as e:
        return jsonify({
            'status': 'unreachable',
            'error': str(e)
        })


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Configuration
    'telemetry_config',
    
    # Consent
    'consent_manager',
    
    # Crash reporting
    'capture_exception',
    'capture_message',
    'add_breadcrumb',
    'set_user_context',
    'clear_user_context',
    
    # Usage analytics
    'telemetry',
    
    # Convenience functions
    'track_login',
    'track_logout',
    'track_agent_created',
    'track_agent_executed',
    'track_workflow_executed',
    'track_document_processed',
    'track_tier_limit_hit',
    'track_feature_usage',
    'track_error',
    
    # Decorators
    'track_errors',
    
    # Flask integration
    'init_telemetry',
    'telemetry_blueprint',

    'track_data_agent_created',
    'track_workflow_created',
    'track_document_job_created',
    'track_document_job_executed',
    'track_custom_tool_created',
    'track_environment_created',
    'track_environment_cloned',
    'track_environment_deleted',
    'track_agent_exported',
    'track_agent_imported',
    'track_tool_exported',
]
