import os
import config as cfg

# Force matplotlib to use the Agg backend
import matplotlib
matplotlib.use('Agg', force=True)  # Set non-interactive backend BEFORE any other matplotlib imports
# Disable Matplotlib's tkinter support entirely
os.environ['MPLBACKEND'] = 'Agg'

# Make sure no interactive mode is used
matplotlib.interactive(False)

# Configure global Matplotlib settings to avoid any GUI dependencies
import matplotlib.pyplot as plt
plt.ioff()  # Turn off interactive mode

from flask import Flask, redirect, render_template, request, url_for, jsonify, send_file, flash, session, Response, abort, make_response
from flask_session import Session

from AppUtils import *
from DataUtils import *

import app_config
#import os
import logging
from logging.handlers import WatchedFileHandler
import ast

from GeneralAgent import GeneralAgent
from LLMDataEngineV2 import LLMDataEngine
import pandas as pd
from flask_cors import CORS, cross_origin
import shutil
import tempfile

from werkzeug.security import generate_password_hash, check_password_hash
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_sqlalchemy import SQLAlchemy
import pickle
import uuid
import sys
from system_prompts import SYS_PROMPT_UNAUTH_EMAIL_SYSTEM, SYS_PROMPT_UNAUTH_EMAIL_PROMPT
import warnings

warnings.filterwarnings("ignore", category=UserWarning)


from llm_unit_test import get_column_descriptions, sample_data_from_db, get_chatgpt_questions, run_tests, save_results, summarize_results, get_agent_connection_info
import threading
from request_tracking import RequestTracking

# RLS
from sqlalchemy.orm import relationship, Session, scoped_session, sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import text

import pyodbc

import glob
import fnmatch
import random

from tool_dependency_manager import (
    load_tool_dependencies, 
    get_tools_for_agent,
    get_user_selectable_tools_filtered
)

from CommonUtils import build_filter_conditions, get_base_url, get_agent_api_base_url, estimate_token_count, Timer
from admin_tier_usage import tier_allows_feature
from tier_template_context import create_tier_context_processor
from role_decorators import admin_required, developer_required, internal_api_key_required

# Telemetry
from telemetry import (
    init_telemetry, 
    telemetry_blueprint,
    capture_exception,
    add_breadcrumb,
    track_login,
    track_logout,
    track_agent_executed,
    track_workflow_executed,
    track_tier_limit_hit,
    track_agent_created,
    track_feature_usage
)

from SmartContentRenderer import SmartContentRenderer
smart_renderer = SmartContentRenderer()

#################################################################
# Agent initialization
from agent_api_client import AgentAPIClient, AgentAPIAdapter

# Initialize client
AGENT_API_TIMEOUT = int(cfg.AGENT_API_TIMEOUT)
agent_client = AgentAPIClient(get_agent_api_base_url(), timeout=AGENT_API_TIMEOUT)
#################################################################

from workflow_api_client import WorkflowAPIClient, WorkflowServiceError, get_workflow_executor_url

from connection_secrets import (
    store_connection_password,
    retrieve_connection_password,
    delete_connection_secret,
    update_connection_secret_id,
    is_secret_reference,
    create_secret_reference,
    get_connection_secret_name,
    process_connection_for_use,
    resolve_connection_string_secrets
)
from local_secrets import get_secrets_manager

from universal_assistant import setup_assistant_routes

from local_history_routes import (
    save_chat_message,
    get_or_create_conversation,
    create_new_conversation,
    is_history_enabled_for_user
)

# BYOK (Bring Your Own Key) Configuration
from api_keys_config import api_keys_bp, register_page_route, init_byok

from role_decorators import api_key_or_session_required


#################################################################
# Agent environment initialization
AGENT_ENVIRONMENTS_ENABLED = cfg.AGENT_ENVIRONMENTS_ENABLED
SHOW_DOCUMENT_FEATURES = cfg.SHOW_DOCUMENT_FEATURES
SHOW_WORKFLOW_FEATURES = cfg.SHOW_WORKFLOW_FEATURES
AGENT_ENVIRONMENTS_SETTINGS = {}

# Workflow Executor Service client
WORKFLOW_EXECUTOR_TIMEOUT = int(cfg.WORKFLOW_EXECUTION_TIMEOUT)

workflow_client = WorkflowAPIClient(
    base_url=get_workflow_executor_url(),
    timeout=WORKFLOW_EXECUTOR_TIMEOUT
)

# Set to False to use in-process execution (original behavior)
USE_WORKFLOW_EXECUTOR_SERVICE = os.getenv('USE_WORKFLOW_EXECUTOR_SERVICE', 'true').lower() == 'true'


def initialize_agent_environments(app):
    """Initialize Agent Environments module using cloud configuration"""
    global AGENT_ENVIRONMENTS_ENABLED, AGENT_ENVIRONMENTS_SETTINGS, SHOW_DOCUMENT_FEATURES, SHOW_WORKFLOW_FEATURES
    
    try:
        # Use APP_ROOT for reliable path resolution
        app_root = os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__)))
        env_module_path = os.path.join(app_root, 'agent_environments')

        # First check if module exists
        if not os.path.exists(env_module_path):
            print(f"[WARN] Agent Environments module not installed at {env_module_path}")
            # Register dummy blueprint to prevent crashes
            register_dummy_environments_blueprint(app)
            return False
        
        # Import required components
        from agent_environments.cloud_config_manager import CloudConfigManager

        # Build connection string
        connection_string = f"DRIVER={{SQL Server}};SERVER={cfg.DATABASE_SERVER};DATABASE={cfg.DATABASE_NAME};UID={cfg.DATABASE_UID};PWD={cfg.DATABASE_PWD}"
        
        # Get tenant ID (from config or environment)
        tenant_id = os.getenv('API_KEY')
        
        if not tenant_id:
            print("[WARN] No tenant license found - using local config")
            # Fall back to local config for development
            AGENT_ENVIRONMENTS_ENABLED = cfg.AGENT_ENVIRONMENTS_ENABLED
            SHOW_DOCUMENT_FEATURES = cfg.SHOW_DOCUMENT_FEATURES
            SHOW_WORKFLOW_FEATURES = cfg.SHOW_WORKFLOW_FEATURES
            AGENT_ENVIRONMENTS_SETTINGS = {
                'max_environments': 10,
                'tier': 'development',
                'tier_display': 'Development Mode'
            }
        else:
            # Get settings from cloud database
            print(f"[INFO] Checking Agent Environments access for Tenant {tenant_id}...")
            config_manager = CloudConfigManager(tenant_id)
            settings = config_manager.get_tenant_settings()
            
            # Check if enabled for this tenant
            AGENT_ENVIRONMENTS_ENABLED = settings.get('environments_enabled', False)
            SHOW_DOCUMENT_FEATURES = settings.get('documents_enabled', cfg.SHOW_DOCUMENT_FEATURES)
            SHOW_WORKFLOW_FEATURES = settings.get('workflows_enabled', cfg.SHOW_WORKFLOW_FEATURES)
            AGENT_ENVIRONMENTS_SETTINGS = settings

            if SHOW_WORKFLOW_FEATURES:
                tier = settings.get('tier_display', 'Unknown')
                print(f"[OK] Workflow ENABLED for tenant {tenant_id}")
                print(f"   Subscription: {tier}")
            else:
                print(f"[WARN] Workflow DISABLED for tenant {tenant_id}")
                print(f"   Current tier: {settings.get('tier_display', 'Free')}")

            if SHOW_DOCUMENT_FEATURES:
                tier = settings.get('tier_display', 'Unknown')
                print(f"[OK] Documents ENABLED for tenant {tenant_id}")
                print(f"   Subscription: {tier}")
            else:
                print(f"[WARN] Documents DISABLED for tenant {tenant_id}")
                print(f"   Current tier: {settings.get('tier_display', 'Free')}")
            
            if AGENT_ENVIRONMENTS_ENABLED:
                tier = settings.get('tier_display', 'Unknown')
                max_envs = settings.get('max_environments', 0)
                print(f"[OK] Agent Environments ENABLED for tenant {tenant_id}")
                print(f"   Subscription: {tier}")
                print(f"   Max Environments: {max_envs if max_envs != -1 else 'Unlimited'}")
            else:
                print(f"[WARN] Agent Environments DISABLED for tenant {tenant_id}")
                print(f"   Current tier: {settings.get('tier_display', 'Free')}")
                print(f"   Upgrade required to access this feature")
        
        # Store configuration in app config
        app.config['DB_CONNECTION_STRING'] = connection_string
        app.config['AGENT_ENVIRONMENTS_ENABLED'] = AGENT_ENVIRONMENTS_ENABLED
        app.config['AGENT_ENVIRONMENTS_SETTINGS'] = AGENT_ENVIRONMENTS_SETTINGS
        app.config['SHOW_DOCUMENT_FEATURES'] = SHOW_DOCUMENT_FEATURES
        app.config['SHOW_WORKFLOW_FEATURES'] = SHOW_WORKFLOW_FEATURES
        app.config['TENANT_ID'] = tenant_id
        
        return True
        
    except Exception as e:
        print(f"[ERROR] Error initializing Agent Environments: {e}")
        import traceback
        traceback.print_exc()
        
        # Register dummy blueprint to prevent crashes
        register_dummy_environments_blueprint(app)
        return False

def register_dummy_environments_blueprint(app):
    """Register a dummy blueprint when module fails to load"""
    from flask import Blueprint, render_template_string
    
    dummy_bp = Blueprint('environments_fallback', __name__, url_prefix='/environments')
    
    @dummy_bp.route('/')
    def index():
        return render_template_string('''
            <div class="container mt-5">
                <div class="alert alert-danger">
                    <h4>Module Not Available</h4>
                    <p>The Agent Environments module is not properly installed.</p>
                    <a href="/" class="btn btn-primary">Back to Dashboard</a>
                </div>
            </div>
        '''), 503
    
    @dummy_bp.route('/sandbox')
    def sandbox():
        return index()
    
    app.register_blueprint(dummy_bp)
    print("[INFO] Registered dummy environments blueprint to prevent crashes")
#################################################################


def rotate_logs_on_startup(log_file=None):
    """
    Check and rotate log files at application startup
    to avoid permission issues during runtime
    """
    try:
        # Get log file path from config
        if not log_file:
            log_file = cfg.LOG_DIR
        
        # Get rotation settings
        max_bytes = getattr(cfg, 'LOG_MAX_BYTES', 1 * 1024 * 1024)  # 1 MB default
        backup_count = getattr(cfg, 'LOG_BACKUP_COUNT', 5)          # 5 backups default
        
        # Create log directory if it doesn't exist
        log_dir = os.path.dirname(log_file)
        if not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        
        # Check if current log file is too large
        if os.path.exists(log_file) and os.path.getsize(log_file) > max_bytes:
            print(f"Rotating log file {log_file} at startup")
            
            # Delete the oldest log file if it exists
            oldest_log = f"{log_file}.{backup_count}"
            if os.path.exists(oldest_log):
                os.remove(oldest_log)
            
            # Shift all existing log files up by one
            for i in range(backup_count-1, 0, -1):
                src = f"{log_file}.{i}"
                dst = f"{log_file}.{i+1}"
                if os.path.exists(src):
                    # Ensure destination file doesn't exist
                    if os.path.exists(dst):
                        os.remove(dst)
                    os.rename(src, dst)
            
            # Rename the current log file
            if os.path.exists(log_file):
                # Ensure destination file doesn't exist
                if os.path.exists(f"{log_file}.1"):
                    os.remove(f"{log_file}.1")
                os.rename(log_file, f"{log_file}.1")
            
            # Create an empty log file
            try:
                with open(log_file, 'w') as f:
                    f.write(f"Log file rotated at {datetime.datetime.now().isoformat()}\n")
            except:
                with open(log_file, 'w') as f:
                    f.write(f"Log file rotated at {datetime.now().isoformat()}\n")
                
            print(f"Log rotation complete")
    except Exception as e:
        print(f"Error rotating logs: {str(e)}")


# Call the function before ANY logging setup happens
print('Rotating log file...')
rotate_logs_on_startup()
rotate_logs_on_startup(log_file=os.getenv('LOG_DIR_AGENT', os.path.join(os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__))), 'logs', 'agent_log.txt')))

# Import knowledge routes
#from agent_knowledge_routes import register_knowledge_routes

print(sys.getrecursionlimit())
print('')

# Will segfault without this line.
#sys.setrecursionlimit(50000)

app = Flask(__name__)

try:
    # Conditional reverse proxy support
    from werkzeug.middleware.proxy_fix import ProxyFix
    behind_proxy = os.getenv('USE_REVERSE_PROXY', 'false').lower() in ('true', '1', 'yes')
    if behind_proxy:
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=1,
            x_proto=1,
            x_host=1,
            x_port=1
        )
        print('Reverse proxy support enabled')
    else:
        print('Running in direct mode (no reverse proxy)')
except Exception as e:
    print('Reverse Proxy Error:', str(e))

cors = CORS(app)
app.config.from_object(app_config)
app.config['SECRET_KEY'] = cfg.SECRET_KEY
app.config['SQLALCHEMY_DATABASE_URI'] = f'mssql+pyodbc://{cfg.DB_USER}:{cfg.DB_PWD}@{cfg.DB_SERVER}/{cfg.DB_NAME}?driver={cfg.DB_DRIVER}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
UPLOAD_FOLDER = os.path.join(os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__))), cfg.APP_UPLOADS_FOLDER)
# Create directory if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Create tmp directory for CSV exports (large dataframe downloads)
TMP_FOLDER = os.path.join(os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__))), 'tmp')
os.makedirs(TMP_FOLDER, exist_ok=True)

# AUTH: login manager
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

print('Initializing agent environments...')
initialize_agent_environments(app)

Session(app)

# Initialize telemetry (crash reporting)
init_telemetry(app)

# Register telemetry settings blueprint
app.register_blueprint(telemetry_blueprint)


class User(db.Model, UserMixin):
    __tablename__ = 'User'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column('user_name', db.String(150), unique=True, nullable=False)
    name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150))
    phone = db.Column(db.String(50))
    role = db.Column(db.Integer, nullable=False)
    password = db.Column(db.String(255))
    #TenantId = db.Column(db.Integer, db.ForeignKey('Tenants.TenantId'))  #
    TenantId = db.Column(db.Integer)  #
    # Enterprise identity fields
    auth_provider = db.Column(db.String(50), nullable=False, default='local')
    external_id = db.Column(db.String(255), nullable=True)
    external_email = db.Column(db.String(255), nullable=True)
    last_sso_login = db.Column(db.DateTime, nullable=True)
    mfa_enabled = db.Column(db.Boolean, nullable=False, default=False)
    mfa_secret = db.Column(db.String(255), nullable=True)
    mfa_backup_codes = db.Column(db.Text, nullable=True)
    mfa_enrolled_at = db.Column(db.DateTime, nullable=True)

    @classmethod
    def get_by_username(cls, username):
        """Get user by username after setting tenant context"""
        # Get current database session
        session = db.session
        
        try:
            # Set tenant context using raw SQL
            session.execute(text("EXEC tenant.sp_setTenantContext :api_key"), 
                        {'api_key': os.getenv('API_KEY')})
            
            # Now query will be tenant-aware due to RLS
            user = cls.query.filter_by(username=username).first()
            
            return user
            
        except Exception as e:
            print(f"Error in get_by_username: {str(e)}")
            return None


@login_manager.user_loader
def load_user(user_id):
    # Get current database session
    session = db.session
    # Set tenant context using raw SQL
    session.execute(text("EXEC tenant.sp_setTenantContext :api_key"), 
                {'api_key': os.getenv('API_KEY')})
    return User.query.get(int(user_id))


from forms import RegistrationForm, LoginForm


###############################################
# Add a cleanup function to handle matplotlib resources properly
import atexit

def cleanup_matplotlib():
    # Close all open figures
    plt.close('all')

# Register the cleanup function
atexit.register(cleanup_matplotlib)
###############################################


########## LOGGING ##########
# logger.basicConfig(filename=cfg.LOG_DIR, level=logger.DEBUG, format='%(asctime)s [%(levelname)s] - %(message)s')

# Create a logging object (root logger by default)
logger = logging.getLogger()

# Get log level from config with a default fallback
log_level_name = getattr(cfg, 'LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)

# Set the logging level
logger.setLevel(log_level)

# ── Credential redaction (BUG-R3-004 fix) ──────────────────────────────────
# The app sends api_key as a URL query parameter to the OpenAI proxy
# endpoint. urllib3's DEBUG-level connection logging captures the full URL
# including the query string, so api_key values were being written into
# app_log.txt verbatim. Two-layer defense:
#   1. Silence urllib3.connectionpool at DEBUG (INFO+ still logs status).
#   2. Attach a redaction filter to the root logger so any api_key= / token=
#      / password= pattern is masked before write, regardless of source.
import re as _rlog
logging.getLogger("urllib3.connectionpool").setLevel(logging.INFO)
logging.getLogger("urllib3").setLevel(logging.INFO)

_SECRET_LOG_PATTERNS = [
    (_rlog.compile(r"(api[_-]?key=)[A-Za-z0-9_\-]{6,}", _rlog.IGNORECASE), r"\1***"),
    (_rlog.compile(r"(password=)[^&\s\"']+", _rlog.IGNORECASE), r"\1***"),
    (_rlog.compile(r"(token=)[A-Za-z0-9_\-\.]{6,}", _rlog.IGNORECASE), r"\1***"),
    (_rlog.compile(r"(secret=)[^&\s\"']+", _rlog.IGNORECASE), r"\1***"),
    (_rlog.compile(r"(\"password\"\s*:\s*\")([^\"]+)(\")", _rlog.IGNORECASE), r"\1***\3"),
    (_rlog.compile(r"(\"api[_-]?key\"\s*:\s*\")([^\"]+)(\")", _rlog.IGNORECASE), r"\1***\3"),
    (_rlog.compile(r"(Authorization:\s*Bearer\s+)[A-Za-z0-9_\-\.]{6,}", _rlog.IGNORECASE), r"\1***"),
]

class _SecretRedactionFilter(logging.Filter):
    """Strip common credential patterns from log records before they're
    handed to any handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        redacted = msg
        for pat, repl in _SECRET_LOG_PATTERNS:
            redacted = pat.sub(repl, redacted)
        if redacted != msg:
            record.msg = redacted
            record.args = ()  # we already interpolated into record.msg
        return True

_redaction_filter = _SecretRedactionFilter()
logger.addFilter(_redaction_filter)

# Create an instance of your custom handler
db_handler = SQLLogHandler(job_id=0)

# Optionally, set a formatter
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
db_handler.setFormatter(formatter)
db_handler.addFilter(_redaction_filter)

# Add the custom handler to the logger
logger.addHandler(db_handler)

# WatchedFileHandler will detect external rotations but won't try to rotate itself
handler = WatchedFileHandler(filename=cfg.LOG_DIR, encoding='utf-8')
handler.setFormatter(formatter)
handler.addFilter(_redaction_filter)

# Add the handler to the logger
logger.addHandler(handler)

monitoring_agent = None
#monitoring_agent = GeneralAgent(1)   # Used to demo email feature only

# Load active agents
active_agents = {}
if cfg.USE_AGENT_API:
    # New Approach
    active_agents = {}
    for agent_id in get_agent_ids():
        active_agents[agent_id] = AgentAPIAdapter(agent_id, agent_client)
        print(f'Initialized adapter for agent {agent_id}')
else:
    # Legacy Approach
    active_agents = {}
    agent_ids = get_agent_ids()
    for id in agent_ids:
        print(f'Initializing agent {id}...')
        temp_agent = GeneralAgent(id)
        active_agents[id] = temp_agent

# Dictionary for user-specific agents (keyed by agent_id and user_id)
user_specific_agents = {}

def get_agent_for_user(agent_id, user_id):
    """
    Get or create an agent for a specific user.
    Returns either a user-specific agent or the general agent.
    Validates cached agents by comparing document count to detect stale state.
    """
    # Check if user has specific knowledge documents
    user_knowledge = get_agent_knowledge_for_user(agent_id, user_id)

    if not user_knowledge:
        # No user-specific documents - use general agent
        # Also evict any stale cached agent for this user (docs may have been deleted)
        agent_key = (agent_id, str(user_id))
        if agent_key in user_specific_agents:
            logger.info(f"Evicting cached user-specific agent {agent_id} for user {user_id} (no docs remain)")
            del user_specific_agents[agent_key]
        logger.info(f"Using general agent {agent_id} for user {user_id}")
        return active_agents.get(agent_id)

    current_doc_count = len(user_knowledge)

    # User has specific documents - check for existing user-specific agent
    agent_key = (agent_id, str(user_id))

    if agent_key in user_specific_agents:
        cached_agent = user_specific_agents[agent_key]
        # Validate cached agent: compare document count to detect stale state
        cached_doc_count = getattr(cached_agent, '_user_doc_count', None)
        if cached_doc_count == current_doc_count:
            logger.info(f"Reusing user-specific agent {agent_id} for user {user_id} (doc count {current_doc_count} matches)")
            return cached_agent
        else:
            logger.info(f"Evicting stale user-specific agent {agent_id} for user {user_id} "
                        f"(cached doc count {cached_doc_count} != current {current_doc_count})")
            del user_specific_agents[agent_key]

    # Create new user-specific agent
    logger.info(f"Creating user-specific agent {agent_id} for user {user_id} ({current_doc_count} docs)")
    try:
        new_agent = GeneralAgent(agent_id, user_id=str(user_id))
        new_agent._user_doc_count = current_doc_count
        user_specific_agents[agent_key] = new_agent
    except Exception as e:
        logger.error(f"Failed to create user-specific agent: {str(e)}")
        # Fallback to general agent
        return active_agents.get(agent_id)

    return user_specific_agents[agent_key]

###########################################
# NLQ Enhancement Wrappers
###########################################
from nlq_enhancements import initialize_enhancements
from engine_enhancements import enhance_engines

# Initialize all enhancement systems
nlq_systems = initialize_enhancements(app, logger=app.logger)

# Enhance the existing chat routes
# enhance_chat_data_route(app, nlq_systems)
# enhance_explain_route(app, nlq_systems)

# Enhance the LLMDataEngine (when initializing it)
# TODO: Implement dict for data agents
llm_data_engines = {}

# Original initialization
llm_data_engine = LLMDataEngine(provider=cfg.NLQ_PROVIDER)

# Enhance its sub-engines
enhanced_query_engine, enhanced_analytical_engine = enhance_engines(llm_data_engine, nlq_systems)

# Inject the enhancements back into your data_engine
llm_data_engine.query_engine = enhanced_query_engine
llm_data_engine.analytical_engine = enhanced_analytical_engine

print(f"Caution System Enabled: {str(cfg.ENABLE_CAUTION_SYSTEM)}")


#############################################################################
# HELPERS
#############################################################################
def load_agents_legacy():
    print('Reloading agents...')
    logger.info('Reloading agents...')
    temp_active_agents = {}
    if cfg.USE_AGENT_API:
        # New Approach
        for agent_id in get_agent_ids():
            temp_active_agents[agent_id] = AgentAPIAdapter(agent_id, agent_client)
            print(f'Initialized adapter for temp agent {agent_id}')
    else:
        # Legacy Approach
        agent_ids = get_agent_ids()
        for id in agent_ids:
            print(f'Initializing temp agent {id}...')
            temp_agent = GeneralAgent(id)
            temp_active_agents[id] = temp_agent

    return temp_active_agents


def load_agents(agent_id=None):
    """
    Load agents into the global active_agents dictionary.
    
    Args:
        agent_id: Optional. If provided, loads only this specific agent.
                 If None, loads all available agents.
                 
    Returns:
        Dictionary of loaded agents (only when agent_id is None)
    """
    global active_agents, agent_client
    
    available_agent_ids = get_agent_ids()
    
    # Single agent reload
    if agent_id is not None:
        agent_id = int(agent_id)
        print(f'Reloading agent {agent_id}...')
        logger.info(f'Reloading agent {agent_id}...')
        
        # Validate that the agent_id exists
        if agent_id not in available_agent_ids:
            logger.warning(f'Agent ID {agent_id} not found in available agents')
        
        if cfg.USE_AGENT_API:
            # New Approach
            active_agents[agent_id] = AgentAPIAdapter(agent_id, agent_client)
            print(f'Initialized adapter for agent {agent_id}')
        else:
            # Legacy Approach
            print(f'Initializing agent {agent_id}...')
            active_agents[agent_id] = GeneralAgent(agent_id)
        
        logger.info(f'Successfully reloaded agent {agent_id}')
        return active_agents
    
    # Load all agents (original behavior)
    print('Reloading agents...')
    logger.info('Reloading agents...')
    temp_active_agents = {}
    
    if cfg.USE_AGENT_API:
        # New Approach
        for agent_id in available_agent_ids:
            temp_active_agents[agent_id] = AgentAPIAdapter(agent_id, agent_client)
            print(f'Initialized adapter for temp agent {agent_id}')
    else:
        # Legacy Approach
        for id in available_agent_ids:
            print(f'Initializing temp agent {id}...')
            temp_agent = GeneralAgent(id)
            temp_active_agents[id] = temp_agent

    return temp_active_agents


def get_current_windows_user():
    try:
        user = os.getlogin()
        return user
    except Exception as e:
        print(str(e))
        return str('unknown')


def split_params(input_list):
    list1 = []
    list2 = []
    
    for item in input_list:
        # Splitting each item at '(' and then removing the ')' from the end of string2
        parts = item.split('(')
        if len(parts) == 2:
            str1 = parts[0]
            str2 = parts[1].rstrip(')')
            
            list1.append(str1)
            list2.append(str2)
        else:
            # Handle the case where the string does not follow the expected format
            print(f"Warning: '{item}' does not follow the expected 'string1(string2)' format.")
    
    return list1, list2

    
def save_custom_tool(name, description, params, paramTypes, modules, code, output, paramOptional, paramDefault):
    try:
        # Create a directory with the specified name if it doesn't exist
        new_tool_folder = os.path.join(cfg.CUSTOM_TOOLS_FOLDER, name)

        is_new = False
        if not os.path.exists(new_tool_folder):
            os.makedirs(new_tool_folder)
            is_new = True

        config = {    
            "function_name": name,
            "description": description,
            "parameters": params,
            "parameter_types": paramTypes,
            "parameter_optional": paramOptional,
            "parameter_defaults": paramDefault,
            "output_type": output,
            "code": "code.py",
            "modules": modules,
            "decorators": ["tool"]
        }

        print("Saving param config:", config)

        # Write the configuration to a JSON file
        with open(os.path.join(new_tool_folder, 'config.json'), 'w') as config_file:
            json.dump(config, config_file, indent=4)

        # Write the code to a Python file (will likely be deprecated)
        with open(os.path.join(new_tool_folder, 'code.py'), 'w') as code_file:
            code_file.write(code)

        # Write complete function to a python file (for pyc conversion) # TODO: Add code to convert to .pyc file
        function = build_custom_tool_function(config, code)
        with open(os.path.join(new_tool_folder, 'function.py'), 'w') as fn_file:
            fn_file.write(function)

        # Save function as byte code
        if not compile_python_script(os.path.join(new_tool_folder, 'function.py'), os.path.join(new_tool_folder, 'function.pyc')):
            logger.error('Encountered a problem while converting to byte-code')

        try:
            from telemetry import track_custom_tool_created
            if is_new:
                track_custom_tool_created(name)
        except:
            pass

        return True
    except Exception as e:
        print(str(e))
        logger.error(str(e))
        return False


def hash_the_password(password):
    hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
    return hashed_password


def remove_html_tags(text):
    """
    Remove HTML tags from a string.

    Args:
    text (str): The string containing HTML tags.

    Returns:
    str: The string with HTML tags removed.
    """
    clean_text = re.sub(r'<.*?>', '', text)
    clean_text = clean_text.replace('&quot;','"')
    return clean_text


def clean_email_input_text(text):
    """
    Remove HTML tags from a string.

    Args:
    text (str): The string containing HTML tags.

    Returns:
    str: The string with HTML tags removed.
    """
    clean_text = re.sub(r'<.*?>', '', text)
    clean_text = re.sub(r"<[^>]*>", "", text)
    clean_text = re.sub(r"\&nbsp;.*$", "", clean_text)
    clean_text = clean_text.replace('&quot;','"')
    return clean_text


def get_session_llm_data_engine_legacy(session_id):
    try:
        logger.debug(f"process_chat_data_request - Session ID: {session['session_id']}")
        _serialized_llm_data_engine = llm_data_engines.get(session['session_id'])
        logger.debug('Loaded object from dict...')
        _deserialized_llm_data_engine = pickle.loads(_serialized_llm_data_engine)
        logger.debug('Loaded object from pickle (deserialized)...')
        return _deserialized_llm_data_engine
    except Exception as e:
        print(str(e))
        logger.error(f"Error loading LLMDataEngine in session: {e}")
        return None

def get_session_llm_data_engine(session_id):
    try:
        logger.debug(f"process_chat_data_request - Session ID: {session_id}")
        _serialized_llm_data_engine = llm_data_engines.get(session_id)

        if _serialized_llm_data_engine is None:
            logger.warning(f"No LLMDataEngine found for session {session_id}. It may have expired.")
            return None

        logger.debug('Loaded object from dict...')
        _deserialized_llm_data_engine = pickle.loads(_serialized_llm_data_engine)
        logger.debug('Loaded object from pickle (deserialized)...')
        return _deserialized_llm_data_engine
    except Exception as e:
        logger.error(f"Error loading LLMDataEngine in session {session_id}: {e}")
        return None


def update_session_llm_data_engine(session_id, updated_llm_data_engine):
    try:
        llm_data_engines[session_id] = pickle.dumps(updated_llm_data_engine)
        logger.debug(f"Updated LLMDataEngine for session ID: {session_id}")
    except Exception as e:
        logger.error(f"Error updating LLMDataEngine in session: {e}")


def process_chat_data_explain_request():
    try:
        explain = ''
        deserialized_llm_data_engine = get_session_llm_data_engine(session['session_id'])
        if deserialized_llm_data_engine is None:
            logger.warning(f"Session {session['session_id']} may have expired, informing user to start a new chat (happened during explain)...")
            explain = 'Your session may have expired. Please refresh or start a new chat.'
        else:
            explain = deserialized_llm_data_engine.explain()
    except Exception as e:
        explain = ''
        logger.error(str(e))
        print(str(e))

    response = {
            "explanation": explain,
        }
    return response


def fix_conversation_history(conversation_history_str):
    """
    A direct, focused approach to fix conversation history
    with special handling for entries containing backticks.
    
    Args:
        conversation_history_str: The conversation history string
        
    Returns:
        list: The parsed conversation history
    """
    print(f"Analyzing conversation history input ({len(conversation_history_str)} chars)")
    
    # If input is already a list, return it as is
    if isinstance(conversation_history_str, list):
        print("Input is already a list, no parsing needed")
        return conversation_history_str
    
    # Try standard parsing first
    try:
        import ast
        result = ast.literal_eval(conversation_history_str)
        print("Standard parsing successful")
        return result
    except Exception as e:
        print(f"Standard parsing failed: {str(e)[:100]}")
    
    # Direct approach - handle the specific issue
    result = []
    
    # Simple string-based identification of entries
    try:
        # First remove outer brackets and whitespace
        clean_str = conversation_history_str.strip()
        if clean_str.startswith("["):
            clean_str = clean_str[1:]
        if clean_str.endswith("]"):
            clean_str = clean_str[:-1]
        
        # Insert a special marker before every entry
        marked_str = clean_str.replace("{'role':", "###ENTRY###{'role':")
        
        # Split by the marker
        entries = marked_str.split("###ENTRY###")
        
        # Process each entry
        for i, entry_text in enumerate(entries):
            # Skip empty entries
            if not entry_text.strip():
                continue
                
            # Make sure entry starts properly
            entry_text = entry_text.strip()
            if not entry_text.startswith("{"):
                entry_text = "{" + entry_text
                
            # Check if this is an entry with backticks
            is_problematic = "`" in entry_text
            
            if is_problematic:
                print(f"Found problematic entry {i} with backtick")
                
                # Extract role
                import re
                role_match = re.search(r"'role':\s*'([QA])'", entry_text)
                if role_match:
                    role = role_match.group(1)
                else:
                    # Default to 'A' if we can't determine
                    role = 'A'
                    print(f"Could not determine role for entry {i}, using 'A'")
                
                # Extract the content directly - we know the structure
                content_start = entry_text.find("'content': '") + len("'content': '")
                
                # Extract everything after the content marker up to the last possible content end
                content_end = entry_text.rfind("'}")
                if content_end > content_start:
                    content = entry_text[content_start:content_end]
                else:
                    # If we can't find the end, take a more aggressive approach
                    # Find where the next entry likely begins by looking for a comma followed by role marker
                    next_entry = entry_text.find("}, {")
                    if next_entry > content_start:
                        content = entry_text[content_start:next_entry]
                    else:
                        # Last resort - take everything after content_start
                        content = entry_text[content_start:]
                
                # Clean up the content - fix the prefix issue
                if content.startswith("\', \'content\': \""):
                    content = content[len("\', \'content\': \""):]
                    print(f"Removed incorrect prefix from problematic entry")
                
                # Add the problematic entry
                result.append({"role": role, "content": content})
                print(f"Successfully extracted problematic entry content ({len(content)} chars)")
            else:
                # For normal entries, try standard parsing
                try:
                    # Make sure the entry text is properly formatted
                    if not entry_text.endswith("}"):
                        # Find the proper end of this entry
                        entry_end = entry_text.rfind("}")
                        if entry_end > 0:
                            entry_text = entry_text[:entry_end+1]
                    
                    # Normalize the entry text to help with parsing
                    entry_text = entry_text.replace("\n", " ").strip()
                    
                    # Try to parse with ast.literal_eval
                    import ast
                    entry = ast.literal_eval(entry_text)
                    
                    if isinstance(entry, dict) and 'role' in entry and 'content' in entry:
                        result.append(entry)
                        print(f"Successfully parsed entry {i} with role {entry['role']}")
                    else:
                        print(f"Entry {i} missing required fields: {entry.keys() if isinstance(entry, dict) else type(entry)}")
                except Exception as e:
                    print(f"Error parsing entry {i}: {str(e)[:100]}")
                    
                    # Fallback for this entry - manual extraction
                    try:
                        import re
                        role_match = re.search(r"'role':\s*'([QA])'", entry_text)
                        content_match = re.search(r"'content':\s*'([^']*)'", entry_text)
                        
                        if role_match and content_match:
                            role = role_match.group(1)
                            content = content_match.group(1)
                            result.append({"role": role, "content": content})
                            print(f"Manually extracted entry {i} with role {role}")
                        else:
                            print(f"Could not extract role/content from entry {i}")
                    except Exception as e:
                        print(f"Manual extraction failed for entry {i}: {str(e)[:100]}")
        
        print(f"Processed {len(result)} entries successfully")
        
        # As a safety check, verify we have question/answer pairs
        q_count = sum(1 for entry in result if entry.get('role') == 'Q')
        a_count = sum(1 for entry in result if entry.get('role') == 'A')
        
        print(f"Entry counts: {q_count} questions, {a_count} answers")
        
        return result
    
    except Exception as e:
        print(f"Processing failed: {str(e)[:100]}")
        # Return empty list as fallback
        return []


def process_chat_data_request(llm_data_engine, agent_id, question, conversation_history, format_table_as_json=False):
    try:
        _llm_data_engine = llm_data_engine

        # Clean the HTML email string
        if conversation_history is None or conversation_history == '':
            conversation_history = []
        else:
            conversation_history = remove_html_tags(conversation_history)

        question = clean_email_input_text(question)
        
        logger.debug("=================================")
        logger.debug("Input Question:" + str(question))
        logger.debug("Input History:" + str(conversation_history))
        logger.debug("=================================")

        if _llm_data_engine is not None:
            _llm_data_engine.clear_chat_hist()
        
        if conversation_history is None or conversation_history == '' or conversation_history == []:
            logger.debug("Received empty conversation history.")
            conversation_history = []
        else:
            logger.debug("Received conversation history, updating agent conversation history...")
            try:
                conversation_history = ast.literal_eval(conversation_history)
            except:
                logger.warning(f'Problem detected parsing conversation history, attempting to repair...')
                conversation_history = fix_conversation_history(conversation_history)

            for entry in conversation_history:
                is_user = entry['role'] == 'Q'
                _llm_data_engine.add_message_to_hist(entry['content'], is_user=is_user)
                logger.debug("Adding to history - Content:" + str(entry['content']) + ' Is User: ' + str(is_user))
                #print("Adding to history - Content:" + str(entry['content']) + ' Is User: ' + str(is_user))
            # for item in conversation_history:
            #     logger.info('Conversation item:' + str(item))
            #     for key, value in item.items():
            #         logger.info('Item Key:' + str(key))
            #         logger.info('Item Value:' + str(value))
            #         if key == "Q":
            #             logger.info("Adding - Q:" + str(value))
            #             _llm_data_engine.add_message_to_hist(value, is_user=True)
            #         elif key == "A":
            #             logger.info("Adding - A:" + str(value))
            #             _llm_data_engine.add_message_to_hist(value, is_user=False)

        logger.info("Processing request...")
        # Submit question
        #answer, explain, clarify, answer_type, special_message, original_prompt, revised_prompt, query = _llm_data_engine.get_answer(agent_id, question)
        result = _llm_data_engine.get_answer(agent_id, question)

        # NEW: Handle both return formats
        if isinstance(result, dict):
            # Rich content enabled - extract values from dictionary
            answer = result['answer']
            explain = result.get('explain', '')
            clarify = result.get('clarify', '')
            answer_type = result.get('answer_type', 'string')
            special_message = result.get('special_message', '')
            original_prompt = question  # Use the input question
            revised_prompt = ''  # Not in dict, default to empty
            query = result.get('query', '')
            
            # Store rich content info
            rich_content = result.get('rich_content')
            rich_content_enabled = result.get('rich_content_enabled', False)
            
        else:
            # Original tuple format (8 values)
            answer, explain, clarify, answer_type, special_message, original_prompt, revised_prompt, query = result
            rich_content = None
            rich_content_enabled = False

        logger.debug("Revised Question:" + str(revised_prompt))
        logger.debug("Answer:" + str(answer))
        logger.debug("Answer Type:" + str(answer_type))
        logger.debug("Explain:" + str(explain))
        logger.debug("Clarify:" + str(clarify))
        logger.debug("Special Message:" + str(special_message))

        #conversation_history.append({"Q": question})

        # Add this handling for the new multi_dataframe answer type in your process_chat_data_request function
        # Find the section that starts with "if answer_type == "dataframe":"
        # and add this new condition before it:

        if answer_type == "multi_dataframe":
            logger.warning("MULTIPLE DATAFRAMES DETECTED")
            print(45 * '*', ' MULTIPLE DATAFRAMES DETECTED ', 45 * '*')
            # We already have the first dataframe in 'answer' and the combined HTML in 'special_message'
            # Add the first dataframe to conversation history for replay
            df_string = answer.to_string(index=False)
            conversation_history.append({"role": "A", "content": df_string})
            
            # Already have HTML in special_message, so we don't need to modify that
            
            # Convert answer to HTML for UI display
            if format_table_as_json:
                answer = answer.to_json(orient='table', index=False)
            else:
                answer = answer.to_html()
                
            # Keep the answer_type as "multi_dataframe" to signal special handling in the UI

        if answer_type == "dataframe":
            # Ensure answer is a pandas DataFrame
            if not isinstance(answer, pd.DataFrame):
                # Save the column headers from the nonstandard dataframe
                headers = answer.columns if hasattr(answer, 'columns') else None

                # Convert answer to a pandas DataFrame
                answer = pd.DataFrame(answer)

                # Manually set the headers if they were saved
                if headers is not None:
                    answer.columns = headers
                
            if len(answer) > cfg.DISPLAY_ROW_LIMIT or len(answer.columns) > cfg.DISPLAY_COLUMN_LIMIT:
                print(f'Dataframe too large, saving to file and generating URL...')
                df_string = answer.to_string(index=False)
                if format_table_as_json:
                    special_message = answer.to_json(orient='table', index=False)
                else:
                    special_message = answer.to_html()

                file_id = str(uuid.uuid4())
                file_path = resource_path(f'tmp/{file_id}.csv')
                answer.to_csv(file_path, index=False)
                download_url = url_for('download_file', file_id=file_id, _external=True)
                conversation_history.append({"role": "A", "content": "The result set is large. Click the link to download the file: " + download_url})
                answer = f'Results too large to show: <a href="{download_url}" target="_blank">Download CSV file</a>'
            else:
                print(f'Dataframe OK, formatting for display...')
                df_string = answer.to_string(index=False)
                conversation_history.append({"role": "A", "content": df_string})
                if format_table_as_json:
                    answer = answer.to_json(orient='table', index=False)
                else:
                    answer = answer.to_html()
        else:
            conversation_history.append({"role": "A", "content": answer})

        logger.debug("Conversation History:" + str(conversation_history))

        # Create response object
        response = {
            "answer": str(answer),
            "answer_type": answer_type,
            "explanation": explain,
            "clarification_questions": clarify,
            "special_message": special_message,
            "original_prompt": original_prompt,
            "revised_prompt": revised_prompt,
            "conversation_history": conversation_history,
            "query": query,
        }

        # NEW: Add rich content fields if available
        if rich_content_enabled and rich_content:
            response["rich_content"] = rich_content
            response["rich_content_enabled"] = True
        else:
            response["rich_content_enabled"] = False

        logger.debug("Response to Client:" + str(response))

        print(86 * '+')
        print('QUESTION COUNT:')
        print(_llm_data_engine.question_count)
        print(86 * '+')
    except Exception as e:
        print(str(e))
        logger.error("Error:" + str(e))
        response = {
            "answer": "ERROR",
            "answer_type": "string",
            "explanation": str(e),
            "clarification_questions": "",
            "special_message": "ERROR",
            "original_prompt": question,
            "revised_prompt": "",
            "conversation_history": conversation_history,
            "query": "",
            "rich_content_enabled": False
        }
    
    return response, _llm_data_engine


#############################################################################
# ROUTES
#############################################################################
@app.before_request
def before_request():
    """Set user context in RequestTracking before each request"""
    if current_user.is_authenticated:
        RequestTracking.set_user_id(current_user.id)


def set_user_id_for_tracking(module_name, request_id=None, user_id=None):
    try:
        if current_user.is_authenticated:
            # Generate or extract request ID
            if not request_id:
                request_id = str(uuid.uuid4())

            # Set in Flask's g object - this is globally accessible for this request only
            if not user_id:
                user_id = current_user.id

            RequestTracking.set_tracking(request_id, module_name, user_id)

            print(f'Tracking Set - request id {request_id} for module {module_name} for user id {user_id}')
        else:
            print(f"User is not authenticated - id for tracking not set")
    except Exception as e:
        print(f"Error setting user id for tracking: {str(e)}")

#####################
# AUTH ROUTES
#####################
@app.route("/login", methods=['GET', 'POST'])
def login():
    # Check for initial setup first
    from initial_setup import needs_initial_setup
    if needs_initial_setup():
        return redirect(url_for('initial_setup.setup_page'))

    if current_user.is_authenticated:
        print('User already authenticated, returning to home...')
        return redirect(url_for('home'))
    form = LoginForm()
    if form.validate_on_submit():
        print('validate on submit == True...')

        # Use the provider chain: tries LDAP (if configured) then local auth
        try:
            from auth import authenticate_user
            auth_result = authenticate_user(
                username=form.username.data,
                password=form.password.data,
                bcrypt_instance=bcrypt,
                user_class=User,
                db=db
            )

            if auth_result['success']:
                user = auth_result['user']
                print(f"User authenticated via {auth_result['provider']}: {user.username}")
                login_user(user, remember=form.remember.data)

                next_page = request.args.get('next')
                return redirect(next_page) if next_page else redirect(url_for('home'))
            else:
                # Track failed login
                track_login(success=False)
                flash('Login Unsuccessful. Please check email and password', 'danger')

        except Exception as e:
            # Fallback to original local auth if provider chain has an unexpected error
            print(f"Provider chain error, falling back to local auth: {str(e)}")
            user = User.get_by_username(form.username.data)
            if user and user.password and bcrypt.check_password_hash(user.password, form.password.data):
                login_user(user, remember=form.remember.data)
                next_page = request.args.get('next')
                return redirect(next_page) if next_page else redirect(url_for('home'))
            else:
                track_login(success=False)
                flash('Login Unsuccessful. Please check email and password', 'danger')

    return render_template('login.html', title='Login', form=form)


@app.route("/logout")
def logout():
    #track_logout()  # telemetry
    logout_user()
    return redirect(url_for('home'))
#####################
# END AUTH ROUTES
#####################

#--------------------

#####################
# ERROR ROUTES
#####################
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    logger.error(f'Server Error: {str(e)}')
    capture_exception(e)
    return render_template('500.html'), 500

@app.errorhandler(Exception)
def unhandled_exception(e):
    logger.error(f'Unhandled Exception: {str(e)}')
    capture_exception(e)
    return render_template('500.html'), 500
#####################
# END ERROR ROUTES
#####################

#--------------------

#####################
# PAGE ROUTES
#####################
@app.route('/custom_tool', methods=['GET', 'POST'])
@cross_origin()
@developer_required()
def custom_tool():
    return render_template('custom_tool.html')


@app.route('/users')
@admin_required()
def users():
    return render_template('users.html')


@app.route('/connections')
@developer_required()
def connections():
    return render_template('connections.html')


@app.route('/system_logs')
@developer_required()
def system_logs():
    return render_template('system_logs.html')

@app.route('/data_dictionary')
@developer_required()
def data_dictionary():
    return render_template('data_dictionary.html')

@app.route('/groups')
@admin_required()
def groups():
    return render_template('groups.html')


@app.route('/')
def home():
    """
    Main landing page route - serves different content based on authentication
    """
    try:
        # Assign a unique session ID if not already assigned
        if 'session_id' not in session:
            session['session_id'] = str(uuid.uuid4())
            app.logger.debug(f"New session created with ID: {session['session_id']}")
        print('Session created:', session['session_id'])
        
        # If user is authenticated, redirect to dashboard
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))
        else:
            # Show public landing page for non-authenticated users
            return render_template('landing.html')
            
    except Exception as e:
        print(str(e))
        logger.error(str(e))
        # Fallback to public landing page
        return render_template('landing.html')

@app.route('/dashboard')
@login_required
def dashboard():
    """
    Authenticated user dashboard - shows personalized AI Hub interface
    """
    return render_template('ai_hub_dashboard.html')

# Optional: Keep your original index route for backward compatibility
@app.route('/index')
def index():
    """
    Original index page (kept for backward compatibility)
    """
    return render_template('index.html')

# Optional: Public landing page as separate route
@app.route('/landing')
def landing():
    """
    Public marketing/landing page
    """
    return render_template('landing.html')


@app.route('/jobs', methods=['GET', 'POST'])
@login_required
def jobs():
    print('Current User Authenticated:', current_user.is_authenticated)
    if request.method == 'POST':
        job_name = request.form.get('job_name')
        description = request.form.get('description')
        is_on = request.form.get('is_on') == 'on'
        # Save the job details to the database or perform other actions
        return redirect(url_for('jobs'))
    return render_template('jobs.html')

@app.route('/chat')
@login_required
def chat_modern():
    """Modern agent chat UI."""
    return render_template('chat.html')


@app.route('/data_chat')
@login_required
def data_chat_modern():
    """Modern data chat UI - same backend logic as data_assistants."""
    try:
        if 'session_id' not in session:
            session['session_id'] = str(uuid.uuid4())

        engine = LLMDataEngine(provider=cfg.NLQ_PROVIDER)
        enhanced_query_engine, enhanced_analytical_engine = enhance_engines(engine, nlq_systems)
        engine.query_engine = enhanced_query_engine
        engine.analytical_engine = enhanced_analytical_engine
        llm_data_engines[session['session_id']] = pickle.dumps(engine)
    except Exception as e:
        print(str(e))
        logger.error(str(e))
    return render_template('data_chat.html')


@app.route('/assistants')
@login_required
def assistants():
    return render_template('assistants.html')


@app.route('/data_assistants')
@login_required
def data_assistants():
    print('Reinitializing LLMDataEngine...')
    print('WARNING: Implement dict of LLMDataEngine objects to handle multiple sessions.')
    try:
        #global llm_data_engine
        #session['llm_data_engine'] = pickle.dumps(LLMDataEngine())

        # Assign a unique session ID if not already assigned
        if 'session_id' not in session:
            session['session_id'] = str(uuid.uuid4())

        # Create a new Assistant for the session if not already created
        if session['session_id'] not in llm_data_engines:
            engine = LLMDataEngine(provider=cfg.NLQ_PROVIDER)
            enhanced_query_engine, enhanced_analytical_engine = enhance_engines(engine, nlq_systems)
            engine.query_engine = enhanced_query_engine
            engine.analytical_engine = enhanced_analytical_engine
            llm_data_engines[session['session_id']] = pickle.dumps(engine)
        else:
            engine = LLMDataEngine(provider=cfg.NLQ_PROVIDER)
            enhanced_query_engine, enhanced_analytical_engine = enhance_engines(engine, nlq_systems)
            engine.query_engine = enhanced_query_engine
            engine.analytical_engine = enhanced_analytical_engine
            llm_data_engines[session['session_id']] = pickle.dumps(engine)

    except Exception as e:
        print(str(e))
        logger.error(str(e))
    return render_template('data_assistants.html')


@app.route('/submit', methods=['POST'])
@login_required
def submit():
    data = request.form.get('data')
    # Process the data as needed
    print(f'Received data: {data}')
    return redirect(url_for('index'))


@app.route('/custom_data_agent', methods=['GET', 'POST'])
@developer_required()
def custom_data_agent():
    return render_template('custom_data_agent.html')


#####################
# BUILDER SERVICE INTEGRATION
#####################
import secrets
import datetime as dt

# In-memory token store for builder service authentication
# In production, consider using Redis or database storage
builder_tokens = {}

def cleanup_expired_builder_tokens():
    """Remove expired tokens from the store"""
    now = dt.datetime.now(dt.timezone.utc)
    expired = [token for token, data in builder_tokens.items() if now > data['expires']]
    for token in expired:
        del builder_tokens[token]

@app.route('/builder')
@developer_required()
def builder_redirect():
    """
    Generate a secure token and redirect to the builder service.
    The builder service will validate this token to get user context.
    """
    # Clean up expired tokens periodically
    cleanup_expired_builder_tokens()

    # Generate secure token
    token = secrets.token_urlsafe(32)

    # Store token with user context (expires in 1 hour)
    builder_tokens[token] = {
        'user_id': current_user.id,
        'role': current_user.role,
        'tenant_id': current_user.TenantId,
        'username': current_user.username,
        'name': current_user.name,
        'expires': dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
    }

    # Get builder service URL from config or use default
    builder_port = os.environ.get('BUILDER_SERVICE_PORT', '8100')
    builder_host = os.environ.get('BUILDER_SERVICE_HOST', 'localhost')
    builder_url = f'http://{builder_host}:{builder_port}?token={token}'

    return redirect(builder_url)


@app.route('/api/validate-builder-token', methods=['POST'])
def validate_builder_token():
    """
    Endpoint for builder service to validate a token and retrieve user context.
    This allows the builder service to know who the authenticated user is.
    """
    data = request.get_json()
    if not data:
        return jsonify({'valid': False, 'error': 'No data provided'}), 400

    token = data.get('token')
    if not token:
        return jsonify({'valid': False, 'error': 'No token provided'}), 400

    # Check if token exists
    if token not in builder_tokens:
        return jsonify({'valid': False, 'error': 'Invalid token'}), 401

    token_data = builder_tokens[token]

    # Check if token has expired
    if dt.datetime.now(dt.timezone.utc) > token_data['expires']:
        del builder_tokens[token]
        return jsonify({'valid': False, 'error': 'Token expired'}), 401

    # Token is valid - return user context
    # Note: We don't delete the token here so it can be revalidated during the session
    # The token will expire naturally after 1 hour
    return jsonify({
        'valid': True,
        'user_id': token_data['user_id'],
        'role': token_data['role'],
        'tenant_id': token_data['tenant_id'],
        'username': token_data['username'],
        'name': token_data['name']
    })


@app.route('/api/builder-auto-token', methods=['GET'])
@cross_origin(supports_credentials=True)
@login_required
def builder_auto_token():
    """
    Generate a builder token for the currently logged-in user.
    Called by the builder frontend (cross-origin) to auto-authenticate
    when accessed directly at port 8100 without going through /builder.
    The browser sends the Flask session cookie with credentials: 'include'.
    """
    cleanup_expired_builder_tokens()

    token = secrets.token_urlsafe(32)

    builder_tokens[token] = {
        'user_id': current_user.id,
        'role': current_user.role,
        'tenant_id': current_user.TenantId,
        'username': current_user.username,
        'name': current_user.name,
        'expires': dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
    }

    return jsonify({'token': token})

#####################
# END BUILDER SERVICE INTEGRATION
#####################


#####################
# COMMAND CENTER SERVICE INTEGRATION
#####################

# In-memory token store for command center service authentication
cc_tokens = {}

def cleanup_expired_cc_tokens():
    """Remove expired tokens from the store"""
    now = dt.datetime.now(dt.timezone.utc)
    expired = [token for token, data in cc_tokens.items() if now > data['expires']]
    for token in expired:
        del cc_tokens[token]

@app.route('/command-center')
@developer_required()
def command_center_redirect():
    """
    Generate a secure token and redirect to the command center service.
    The command center service will validate this token to get user context.
    """
    # Clean up expired tokens periodically
    cleanup_expired_cc_tokens()

    # Generate secure token
    token = secrets.token_urlsafe(32)

    # Store token with user context (expires in 4 hours)
    cc_tokens[token] = {
        'user_id': current_user.id,
        'role': current_user.role,
        'tenant_id': current_user.TenantId,
        'username': current_user.username,
        'name': current_user.name,
        'expires': dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=4)
    }

    # Get command center service URL from config or use default
    cc_port = os.environ.get('CC_SERVICE_PORT', '5091')
    cc_host = os.environ.get('CC_SERVICE_HOST', '') or request.host.split(':')[0]
    cc_url = f'http://{cc_host}:{cc_port}?token={token}'

    return redirect(cc_url)


@app.route('/api/validate-cc-token', methods=['POST'])
def validate_cc_token():
    """
    Endpoint for command center service to validate a token and retrieve user context.
    This allows the command center service to know who the authenticated user is.
    """
    data = request.get_json()
    if not data:
        return jsonify({'valid': False, 'error': 'No data provided'}), 400

    token = data.get('token')
    if not token:
        return jsonify({'valid': False, 'error': 'No token provided'}), 400

    # Check if token exists
    if token not in cc_tokens:
        return jsonify({'valid': False, 'error': 'Invalid token'}), 401

    token_data = cc_tokens[token]

    # Check if token has expired
    if dt.datetime.now(dt.timezone.utc) > token_data['expires']:
        del cc_tokens[token]
        return jsonify({'valid': False, 'error': 'Token expired'}), 401

    # Token is valid - return user context
    # Note: We don't delete the token here so it can be revalidated during the session
    # The token will expire naturally after 1 hour
    return jsonify({
        'valid': True,
        'user_id': token_data['user_id'],
        'role': token_data['role'],
        'tenant_id': token_data['tenant_id'],
        'username': token_data['username'],
        'name': token_data['name']
    })


@app.route('/api/cc-generate-token', methods=['POST'])
@cross_origin()
@api_key_or_session_required()
def cc_generate_token():
    """
    Server-to-server endpoint for the CC service to generate a fresh token
    on behalf of a user. Uses API key auth (no Flask session needed).
    This enables seamless token refresh without requiring the user to re-login.
    """
    data = request.get_json()
    if not data or not data.get('user_id'):
        return jsonify({'error': 'user_id required'}), 400

    user_id = data['user_id']

    try:
        user_df = Get_Users(str(user_id))
        if user_df is None or user_df.empty:
            return jsonify({'error': 'User not found'}), 404

        user = user_df.iloc[0]
        cleanup_expired_cc_tokens()

        token = secrets.token_urlsafe(32)
        user_context = {
            'user_id': int(user_id),
            'role': int(user.get('role', 1)),
            'tenant_id': int(user.get('TenantId', 1)),
            'username': str(user.get('user_name', '')),
            'name': str(user.get('name', '')),
        }

        cc_tokens[token] = {
            **user_context,
            'expires': dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=4)
        }

        return jsonify({
            'token': token,
            'user_context': user_context,
            'expires_in': 14400,
        })

    except Exception as e:
        logger.error(f"Error generating CC token: {str(e)}")
        return jsonify({'error': 'Failed to generate token'}), 500


@app.route('/api/cc-auto-token', methods=['GET'])
@cross_origin(supports_credentials=True)
@login_required
def cc_auto_token():
    """
    Generate a command center token for the currently logged-in user.
    Called by the command center frontend (cross-origin) to auto-authenticate
    when accessed directly at port 5091 without going through /command-center.
    The browser sends the Flask session cookie with credentials: 'include'.
    """
    cleanup_expired_cc_tokens()

    token = secrets.token_urlsafe(32)

    cc_tokens[token] = {
        'user_id': current_user.id,
        'role': current_user.role,
        'tenant_id': current_user.TenantId,
        'username': current_user.username,
        'name': current_user.name,
        'expires': dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=4)
    }

    return jsonify({'token': token})

#####################
# END COMMAND CENTER SERVICE INTEGRATION
#####################


# Enhanced version of get_core_tool_details that includes category info
def get_core_tool_details_with_categories():
    """Get core tools with their category information"""
    import yaml
    import config as cfg
    from tool_dependency_manager import load_tool_dependencies
    
    # Load the core tools YAML file
    with open(cfg.CORE_TOOLS_FILE, 'r') as file:
        tools_data = yaml.safe_load(file)
    
    all_tools = tools_data.get('tools', [])
    
    # Load dependency manager
    manager = load_tool_dependencies()
    selectable_tools = manager.get_user_selectable_tools()
    
    # Build a map of tool categories
    category_map = {}
    for category_name, category_info in manager.config.get('categories', {}).items():
        for tool_name in category_info.get('tools', []):
            category_map[tool_name] = category_name
    
    # Filter and enhance tools
    filtered_tools = []
    for tool in all_tools:
        if tool['name'] in selectable_tools:
            # Add category information
            tool['category'] = category_map.get(tool['name'], 'Uncategorized')
            
            # Ensure display_name exists
            if 'display_name' not in tool:
                tool['display_name'] = tool['name'].replace('_', ' ').title()
            
            filtered_tools.append(tool)
    
    return filtered_tools

@app.route('/custom_agent_enhanced', methods=['GET', 'POST'])
@developer_required()
def custom_agent_enhanced():
    try:
        # Get custom tools with descriptions
        custom_tools = get_custom_tool_details()
        
        # Get filtered core tools (only user-selectable ones)
        core_tools = get_core_tool_details_with_categories()
    except Exception as e:
        custom_tools = []
        core_tools = []
        print(f"Error in custom_agent route: {e}")
        logger.error(f"Error in custom_agent route: {str(e)}")
    
    return render_template('custom_agent_enhanced.html', custom_tools=custom_tools, core_tools=core_tools)

@app.route('/api/tool/dependencies', methods=['POST'])
@api_key_or_session_required(min_role=2)
def get_tool_dependencies():
    """Get dependencies for selected tools"""
    try:
        data = request.get_json()
        selected_tools = data.get('tools', [])
        include_optional = data.get('include_optional', False)
        
        manager = load_tool_dependencies()
        final_tools, dependency_map = manager.resolve_tool_list(selected_tools, include_optional)
        
        # Get info about added dependencies
        added_tools = list(set(final_tools) - set(selected_tools))
        added_tool_info = []
        
        for tool in added_tools:
            tool_info = manager.get_tool_info(tool)
            if tool_info:
                added_tool_info.append({
                    'name': tool,
                    'display_name': tool_info.display_name,
                    'description': tool_info.description,
                    'reason': 'dependency'
                })
        
        # Get mandatory tools info
        mandatory_tools = []
        for tool_name in manager.get_mandatory_tools():
            tool_info = manager.get_tool_info(tool_name)
            if tool_info:
                mandatory_tools.append({
                    'name': tool_name,
                    'display_name': tool_info.display_name,
                    'description': tool_info.description
                })
        
        return jsonify({
            'status': 'success',
            'selected_tools': selected_tools,
            'final_tools': final_tools,
            'added_dependencies': added_tool_info,
            'dependency_map': dependency_map,
            'mandatory_tools': mandatory_tools
        })
        
    except Exception as e:
        logger.error(f"Error in get_tool_dependencies: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


# Add this route to your app.py to serve dependency groups

@app.route('/api/tool/dependency-groups', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_dependency_groups():
    """Get tool dependency groups from configuration"""
    try:
        from tool_dependency_manager import load_tool_dependencies
        
        manager = load_tool_dependencies()
        groups = manager.config.get('dependency_groups', {})
        
        # Format groups for frontend
        formatted_groups = {}
        for group_key, group_info in groups.items():
            formatted_groups[group_key] = {
                'name': group_key.replace('_', ' ').title(),
                'description': group_info.get('description', ''),
                'tools': group_info.get('tools', [])
            }
        
        # Add tool details to each group
        for group_key, group in formatted_groups.items():
            tool_details = []
            for tool_name in group['tools']:
                tool_info = manager.get_tool_info(tool_name)
                if tool_info:
                    tool_details.append({
                        'name': tool_name,
                        'display_name': tool_info.display_name,
                        'description': tool_info.description
                    })
            group['tool_details'] = tool_details
        
        return jsonify({
            'status': 'success',
            'groups': formatted_groups
        })
        
    except Exception as e:
        logger.error(f"Error getting dependency groups: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

# Add this route to get tools organized by category
@app.route('/api/tools/by-category', methods=['GET'])
@api_key_or_session_required()
def get_tools_by_category():
    """Get tools organized by their categories"""
    try:
        from tool_dependency_manager import load_tool_dependencies
        
        manager = load_tool_dependencies()
        categories = manager.get_all_categories()
        
        # Build response with tools in each category
        categorized_tools = {}
        
        for category_name, category_desc in categories.items():
            tools_in_category = []
            
            # Get all tools in this category
            for tool_name in manager.get_tools_by_category(category_name):
                tool_info = manager.get_tool_info(tool_name)
                if tool_info and tool_info.visibility in ['user_selectable', 'dual_purpose']:
                    tools_in_category.append({
                        'name': tool_name,
                        'display_name': tool_info.display_name,
                        'description': tool_info.description,
                        'category': category_name
                    })
            
            if tools_in_category:
                categorized_tools[category_name] = {
                    'description': category_desc,
                    'tools': tools_in_category
                }
        
        return jsonify({
            'status': 'success',
            'categories': categorized_tools
        })
        
    except Exception as e:
        logger.error(f"Error getting tools by category: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and PyInstaller one-dir """
    # Use APP_ROOT env var (set by installer) for reliable path resolution,
    # fallback to app.py's directory so path is stable regardless of CWD
    base_path = os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__)))

    # Construct the full path
    return os.path.join(base_path, relative_path)


@app.route('/download/<file_id>')
def download_file(file_id):
    file_path = resource_path(f'tmp/{file_id}.csv')
    return send_file(file_path, as_attachment=True)


@app.route('/custom', methods=['GET', 'POST'])
@login_required
def custom():
    logger.info('Received API call to /custom...')
    if request.method == 'POST':
        name = request.form['name']
        modules = request.form.getlist('moduleItems')
        params = request.form.getlist('paramItems')
        paramOptional = request.form.getlist('paramOptional')
        description = request.form['description']
        code = request.form['code']
        output = request.form['output']

        print(params)

        parameters, types = split_params(params)

        print(parameters, types)

        result = save_custom_tool(name, description, params, types, modules, code, output, paramOptional)

        if result:
            tool_path = os.path.join(cfg.CUSTOM_TOOLS_FOLDER, name)
            return render_template('custom_tool_success.html', name=name, tool_path=tool_path)
        else:
            return jsonify(status="error", message="Failed to save custom tool")
    else:
        logger.info('Getting packages...')
        packages = [d for d in os.listdir(cfg.CUSTOM_TOOLS_FOLDER) if os.path.isdir(os.path.join(cfg.CUSTOM_TOOLS_FOLDER, d))]
        logger.info('Packages:' + str(packages))
        return render_template('custom_tool.html', packages=packages)


@app.route('/get_packages', methods=['GET'])
@login_required
def get_packages():
    packages = [d for d in os.listdir(cfg.CUSTOM_TOOLS_FOLDER) if os.path.isdir(os.path.join(cfg.CUSTOM_TOOLS_FOLDER, d))]
    return jsonify(packages=packages)


@app.route('/load_package/<package_name>', methods=['GET'])
@login_required
def load_package(package_name):
    logger.info('Loading package...')
    config_path = os.path.join(cfg.CUSTOM_TOOLS_FOLDER, package_name, 'config.json')
    logger.info('Path:' + str(config_path))
    if os.path.exists(config_path):
        logger.info('Package found...')
        with open(config_path, 'r') as f:
            config = json.load(f)
        code_path = os.path.join(cfg.CUSTOM_TOOLS_FOLDER, package_name, 'code.py')
        with open(code_path, 'r') as f:
            code = f.read()
        config['code'] = code
        config['name'] = package_name
        return jsonify(config)
    return jsonify(status="error", message="Package not found")


@app.route('/save_package', methods=['POST'])
@developer_required(api=True)
def save_package():
    data = request.get_json()

    name = data['name']
    modules = data.get('modules', [])
    params = data.get('parameters', [])
    description = data['description']
    code = data['code']
    output = data['output_type']
    parameter_types = data.get('parameter_types', [])
    parameter_optional = data.get('parameter_optional', [])
    parameter_defaults = data.get('parameter_defaults', [])

    parameters, types = split_params(params)

    print(name, description, parameters, parameter_types, modules, code, output)

    result = save_custom_tool(name, description, params, parameter_types, modules, code, output, parameter_optional, parameter_defaults)
    print('Done saving...')
    print(result)

    try:
        track_feature_usage('custom_tool', 'created')
    except:
        pass

    if result:
        return jsonify({"status": "success"})
    else:
        return jsonify({"status": "error"})


@app.route('/delete_package/<package_name>', methods=['DELETE'])
@api_key_or_session_required()
def delete_package(package_name):
    package_path = os.path.join(cfg.CUSTOM_TOOLS_FOLDER, package_name)
    if os.path.exists(package_path):
        import shutil
        shutil.rmtree(package_path)

        try:
            track_feature_usage('custom_tool', 'deleted')
        except:
            pass

        return jsonify(status="success")
    return jsonify(status="error", message="Package not found")


@app.route('/export', methods=['POST'])
@login_required
def export():
    name = request.form['name']
    folder_path = request.form['folderPath']
    if not os.path.isdir(folder_path):
        return "Folder not found.", 400
    
    # Temporary file setup
    output_file_name = name + '.zip'
    temp_dir = tempfile.mkdtemp()  # Create a temporary directory
    zip_path = os.path.join(temp_dir, output_file_name)

    # Create zip file
    shutil.make_archive(base_name=os.path.join(temp_dir, name), format='zip', root_dir=folder_path)

    # Send file to user and clean up
    response = send_file(zip_path, as_attachment=True, download_name=output_file_name)
    #shutil.rmtree(temp_dir)  # Cleanup the temp directory
    print('=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-')
    print(response)
    print('=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-')

    return response


@app.route('/export_package', methods=['POST'])
@login_required
def export_package():
    # Get data from the request
    data = request.get_json()
    name = data['name']
    folder_path = os.path.join(cfg.CUSTOM_TOOLS_FOLDER, name)
    print('=-=-=-=-=-=-=-=-=-=-=-=- EXPORT =-=-=-=-=-=-=-=-=-=-=-=-=-')
    print(name)
    print(folder_path)
    print('=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-')
    if not os.path.isdir(folder_path):
        return "Folder not found.", 400
    
    # Temporary file setup
    output_file_name = name + '.zip'
    temp_dir = tempfile.mkdtemp()  # Create a temporary directory
    zip_path = os.path.join(temp_dir, output_file_name)

    # Create zip file
    shutil.make_archive(base_name=os.path.join(temp_dir, name), format='zip', root_dir=folder_path)

    # Send file to user and clean up
    response = send_file(zip_path, as_attachment=True, download_name=output_file_name)
    #shutil.rmtree(temp_dir)  # Cleanup the temp directory
    print('=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-')
    print(response)
    print('=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-')

    try:
        track_feature_usage('custom_tool', 'exported')
    except:
        pass

    return response

#####################
# END PAGE ROUTES
#####################

#--------------------

#####################
# DATA ROUTES
#####################
@app.route("/api_check")
def api_check():
    return render_template('api_check.html', app_version=app_config.APP_VERSION)


@app.route('/get/agents', methods=['GET'])
@login_required
def get_agents():
    try:        
        # Call the function to select all agents and tools
        agents_and_tools = select_all_agents_and_tools()
        
        if agents_and_tools is not None:
            return jsonify({'status': 'success', 'data': agents_and_tools})
        else:
            return jsonify({'status': 'error', 'message': 'Failed to fetch data'}), 500
    
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/agents/summary', methods=['GET'])
@api_key_or_session_required()
def list_agents_summary():
    """Lightweight agent listing with metadata for the builder agent.

    Returns all agents (enabled AND disabled) with fields needed for
    analytics, charting, and plan generation.  Does NOT return heavy
    fields like objective or full config.
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", (os.getenv('API_KEY'),))
            cursor.execute("""
                SELECT
                    id             AS agent_id,
                    description    AS agent_name,
                    enabled,
                    create_date,
                    is_data_agent
                FROM Agents
                ORDER BY description
            """)
            agents = []
            for row in cursor.fetchall():
                agents.append({
                    'agent_id': row.agent_id,
                    'agent_name': row.agent_name,
                    'enabled': bool(row.enabled) if row.enabled is not None else True,
                    'created_date': row.create_date.isoformat() if row.create_date else None,
                    'is_data_agent': bool(row.is_data_agent) if row.is_data_agent is not None else False,
                })
            return jsonify({'status': 'success', 'agents': agents})
    except Exception as e:
        logger.error("Error listing agents summary: %s", e)
        return jsonify({'status': 'error', 'message': str(e), 'agents': []}), 500


@app.route('/api/agents/<int:agent_id>/chat', methods=['POST'])
@cross_origin()
@api_key_or_session_required()
def api_agent_chat(agent_id):
    """Unified chat endpoint — auto-detects agent type and routes accordingly.

    Accepts a consistent payload for both general and data agents:
        { "prompt": "...", "history": [] }

    For general agents  → delegates to /chat/general_system logic
    For data agents     → initialises a temporary LLMDataEngine and runs the query
    """
    try:
        data = request.get_json() or {}
        prompt = data.get('prompt', '')
        history = data.get('history', '[]')

        if not prompt:
            return jsonify({'status': 'error', 'response': 'prompt is required'}), 400

        # Determine agent type
        from DataUtils import is_data_agent as _is_data_agent
        is_data = _is_data_agent(agent_id)

        if is_data:
            # ── Data agent path ──────────────────────────────────────
            logger.info(f'[api_agent_chat] Data agent {agent_id} — using LLMDataEngine')
            engine = LLMDataEngine(provider=cfg.NLQ_PROVIDER)
            enhanced_qe, enhanced_ae = enhance_engines(engine, nlq_systems)
            engine.query_engine = enhanced_qe
            engine.analytical_engine = enhanced_ae

            conversation_history = str(history) if history else '[]'
            response_obj, _ = process_chat_data_request(
                engine, agent_id, prompt, conversation_history,
                format_table_as_json=True,
            )

            # Normalise data-agent response to the same shape as general
            answer = response_obj.get('answer', '') if isinstance(response_obj, dict) else str(response_obj)
            return jsonify({
                'status': 'success',
                'response': answer,
                'chat_history': response_obj.get('conversation_history', []) if isinstance(response_obj, dict) else [],
                'agent_type': 'data',
            })
        else:
            # ── General agent path ───────────────────────────────────
            logger.info(f'[api_agent_chat] General agent {agent_id} — using active_agents')

            # Lazy-load if agent not yet in memory
            if int(agent_id) not in active_agents:
                logger.info(f'[api_agent_chat] Agent {agent_id} not in active_agents — loading')
                load_agents(agent_id=int(agent_id))
                if int(agent_id) not in active_agents:
                    return jsonify({
                        'status': 'error',
                        'response': f'Agent {agent_id} could not be loaded.',
                    }), 404

            hist_str = str(history) if history else '[]'
            agent_instance = active_agents[int(agent_id)]
            agent_instance.initialize_chat_history(eval(hist_str))
            response_text = agent_instance.run(prompt, use_smart_render=False)
            chat_history = agent_instance.get_chat_history()

            return jsonify({
                'status': 'success',
                'response': response_text,
                'chat_history': chat_history,
                'agent_type': 'general',
            })

    except Exception as e:
        logger.error(f'[api_agent_chat] Error chatting with agent {agent_id}: {e}')
        return jsonify({
            'status': 'error',
            'response': str(e),
            'chat_history': [],
        }), 500


@app.route('/api/agents/list', methods=['GET'])
@api_key_or_session_required()
def list_agents_for_selection():
    """Get list of agents for dropdown selections"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", (os.getenv('API_KEY'),))
            
            # Query to get all active agents
            # Adjust the table/column names if your schema is different
            cursor.execute("""
                SELECT 
                    id agent_id,
                    description agent_name,
                    objective agent_description
                FROM Agents
                WHERE enabled = 1
                ORDER BY description
            """)
            
            agents = []
            for row in cursor.fetchall():
                agents.append({
                    'value': str(row.agent_id),  # Value for the select option
                    'label': f"{row.agent_name} (ID: {row.agent_id})",  # Display text
                    'agent_id': row.agent_id,
                    'agent_name': row.agent_name,
                    'agent_description': row.agent_description if row.agent_description else ''
                })
            
            return jsonify({
                'status': 'success',
                'agents': agents
            })
    
    except Exception as e:
        logger.error(f"Error listing agents: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e),
            'agents': []
        }), 500
    

@app.route('/get/data_agents', methods=['GET'])
@login_required
def get_data_agents():
    try:        
        # Call the function to select all agents and tools
        agents_and_conns_df = select_all_agents_and_connections()

        if agents_and_conns_df is not None:
            agents_and_conns = dataframe_to_json(agents_and_conns_df)
            return jsonify({'status': 'success', 'data': agents_and_conns})
        else:
            return jsonify({'status': 'error', 'message': 'Failed to fetch data agents'}), 500
    
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/get/user_data_agents', methods=['GET'])
@login_required
def get_user_data_agents():
    try:        
        # Call the function to select all agents and tools
        agents_and_conns_df = select_user_agents_and_connections(current_user.id, current_user.role)

        if agents_and_conns_df is not None:
            agents_and_conns = dataframe_to_json(agents_and_conns_df)
            return jsonify({'status': 'success', 'data': agents_and_conns})
        else:
            return jsonify({'status': 'error', 'message': 'Failed to fetch data agents'}), 500
    
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    

@app.route('/get/user_agents/<int:user_id>', methods=['GET'])
@login_required
def get_agents_by_user(user_id):
    try:        
        # Call the function to select all agents and tools
        agents_and_tools = select_user_agents_and_tools(user_id, current_user.role)
        
        if agents_and_tools is not None:
            return jsonify({'status': 'success', 'data': agents_and_tools})
        else:
            return jsonify({'status': 'error', 'message': 'Failed to fetch data'}), 500
    
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/get/group_permissions', methods=['POST'])
@api_key_or_session_required(min_role=3)
def get_group_assigned_permissions():
    group_id = request.json['group_id']
    assigned_permissions = get_group_permissions(group_id)
    return jsonify({'assigned_permissions': assigned_permissions})


@app.route('/save/permissions', methods=['POST'])
@api_key_or_session_required(min_role=3)
def save_group_permissions():
    data = request.json
    group_id = data['group_id']
    assigned_users = data['assigned_users']
    permissions = data['permissions']
    result = save_permissions(group_id, assigned_users, permissions)
    if result:
        return jsonify({'status': 'success'})
    else:
        return jsonify({'status': 'error'})


@app.route('/get/agent_info', methods=['GET'])
@api_key_or_session_required()
def get_agent_info_for_permissions():
    agent_info = get_agent_info()
    if agent_info is None:
        return jsonify({'status': 'error', 'message': 'Failed to get agent info'}), 500
    return jsonify(agent_info)


@app.route('/add/agent', methods=['POST'])
@api_key_or_session_required()
def add_agent():
    try:
        # Get data from the request
        data = request.get_json()
        agent_id = data.get('agent_id', 0)
        agent_description = data.get('agent_description', 'New Agent')
        # Default objective if not provided - makes API more forgiving for builder service
        agent_objective = data.get('agent_objective', f"You are a helpful AI assistant named {agent_description}. Help users with their requests.")
        agent_enabled = data.get('agent_enabled', True)
        tool_names = data.get('tool_names', [])  # Custom tools
        core_tool_names = data.get('core_tool_names', [])  # Core tools
        
        # Resolve dependencies for core tools
        from tool_dependency_manager import get_tools_for_agent
        final_core_tools = get_tools_for_agent(core_tool_names, include_optional_deps=False)
        
        # Log what dependencies were added
        added_deps = set(final_core_tools) - set(core_tool_names)
        if added_deps:
            logger.info(f"Auto-adding tool dependencies for new agent: {added_deps}")
        
        # Call the function to insert/update agent with resolved tools
        if agent_id == 0:
            agent_id = insert_agent_with_tools(agent_description, agent_objective, 
                                             agent_enabled, tool_names, final_core_tools)
        else:
            agent_id = update_agent_with_tools(agent_id, agent_description, agent_objective, 
                                             agent_enabled, tool_names, final_core_tools)
        
        if agent_id:
            # Reload agents
            #global active_agents 
            #active_agents = load_agents()
            load_agents(agent_id=agent_id)

            try:
                track_agent_created(agent_id, 'general')  # telemetry
            except:
                pass

            return jsonify({'status': 'success', 'message': agent_id})
        else:
            return jsonify({'status': 'error', 'message': 'Failed to insert agent'}), 500
    
    except Exception as e:
        logger.error(f"Error in add_agent: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/tool/diagnostic', methods=['GET'])
@api_key_or_session_required(min_role=2)
def tool_diagnostic():
    """Diagnostic endpoint to check tool dependency configuration"""
    try:
        manager = load_tool_dependencies()
        
        # Get all configured information
        diagnostic_info = {
            'config_loaded': True,
            'total_tools': len(manager.tool_cache),
            'mandatory_tools': manager.get_mandatory_tools(),
            'user_selectable_count': len(manager.get_user_selectable_tools()),
            'hidden_tools': [],
            'categories': manager.get_all_categories(),
            'dependencies_configured': len(manager.config.get('dependencies', {}))
        }
        
        # Get hidden tools
        for tool_name, tool_info in manager.tool_cache.items():
            if tool_info.visibility == 'hidden':
                diagnostic_info['hidden_tools'].append(tool_name)
        
        return jsonify({
            'status': 'success',
            'diagnostic': diagnostic_info
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'diagnostic': {
                'config_loaded': False,
                'error': str(e)
            }
        }), 500
    

@app.route('/add/data_agent', methods=['POST'])
@api_key_or_session_required()
def add_data_agent():
    try:
        # Get data from the request
        data = request.get_json()
        agent_id = data['agent_id']
        agent_description = data['agent_description']
        agent_objective = data['agent_objective']
        agent_enabled = data['agent_enabled']
        connection_id = data['connection_id']

        print('Saving agent...', agent_id, agent_description, agent_objective)
        
        # Call the function to insert agent and tools
        is_new = False
        if agent_id == 0:
            agent_id = insert_agent_with_connection(agent_description, agent_objective, agent_enabled, connection_id)
            is_new = True
        else:
            agent_id = update_agent_with_connection(agent_id, agent_description, agent_objective, agent_enabled, connection_id)

        print('Agent ID:', agent_id)
        
        if agent_id:
            # Reload agent
            load_agents(agent_id=agent_id)

            try:
                from telemetry import track_data_agent_created
                # Track data agent creation (only for new agents, not updates)
                if is_new:
                    track_data_agent_created(agent_id=agent_id)
            except Exception as e:
                logger.warning(f'Failed to track data agent created: {e}')
                
            return jsonify({'status': 'success', 'message': agent_id})
        else:
            return jsonify({'status': 'error', 'message': 'Failed to insert/update data agent'}), 500
    
    except Exception as e:
        print(str(e))
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/delete/agent', methods=['POST'])
@api_key_or_session_required()
def agent_delete():
    try:
        # Get data from the request
        data = request.get_json()
        agent_id = data['agent_id']
        
        # Call the function to delete agent and tools
        ret = delete_agent(agent_id)
        
        if ret:
            return jsonify({'status': 'success', 'message': 'Successfully deleted agent'})
        else:
            return jsonify({'status': 'error', 'message': 'Failed to delete agent'}), 500
    
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/save', methods=['GET', 'POST'])
@cross_origin()
@api_key_or_session_required(min_role=2)
def save():
    try:
        logger.debug('Save custom tool called...')
        modules = request.json.get('modules', [])
        params = request.json['params']
        paramTypes = request.json['paramTypes']
        name = request.json['name']
        description = request.json['description']
        code = request.json['code']
        output = request.json['output']
        paramOptional = request.json.get('paramOptional', [])
        paramDefault = request.json.get('paramDefault', [])
        #session['items'] = items  # Save items to session

        logger.debug(str(name))
        logger.debug(str(description))
        logger.debug(str(params))
        logger.debug(str(paramTypes))
        logger.debug(str(modules))
        logger.debug(str(code))
        logger.debug(str(output))

        result = save_custom_tool(name, description, params, paramTypes, modules, code, output, paramOptional, paramDefault)
    except Exception as e:
        print(str(e))
        logger.error(str(e))

    if result:
        tool_path = os.path.join(cfg.CUSTOM_TOOLS_FOLDER, name)
        logger.info(f'Custom tool {name} saved successfully to {tool_path}')
        #return jsonify(status="error", message="Failed to save custom tool")
        return render_template('custom_tool_success.html', name=name, tool_path=tool_path)
    else:
        return jsonify(status="error", message="Failed to save custom tool")


@app.route("/get/jobs")
@cross_origin()
@login_required
def get_jobs():
    df = Get_Job()
    json_df = dataframe_to_json(df)
    return jsonify(json_df)


@app.route("/get/log", methods = ['GET', 'POST'])
@cross_origin()
def get_log():
    try:
        date = request.args.get('date')
    except:
        # Get JSON data from the POST request
        data = request.get_json()
        date = data['date']

    try:
        df = Get_Logs(date=date)
        json_df = dataframe_to_json(df)
    except Exception as e:
        print(str(e))

    return jsonify(json_df)


@app.route("/get/quickjoblog", methods = ['GET', 'POST'])
@cross_origin()
@login_required
def get_quickjob_log():
    logger.info('Received API request at /get/quickjoblog...')
    timezone_offset_minutes = 0  # Default to UTC if not provided
    
    try:
        # POST request
        data = request.get_json()
        job_id = data['job_id']
        date = data['date']
        # Extract timezone offset if provided (in minutes)
        timezone_offset_minutes = data.get('timezone_offset_minutes', 0)
    except:
        # GET request
        job_id = request.args.get('job_id')
        date = request.args.get('date')
        timezone_offset_minutes = int(request.args.get('timezone_offset_minutes', 0))

    logger.info('========== INPUT ==========')
    logger.info('job_id:' + str(job_id))
    logger.info('date:' + str(date))
    logger.info('timezone_offset_minutes:' + str(timezone_offset_minutes))

    try:
        df = Get_QuickJob_Logs(job_id=job_id, date=date, timezone_offset_minutes=timezone_offset_minutes)
        json_df = dataframe_to_json(df)
    except Exception as e:
        print(str(e))
        logger.error(str(e))

    return jsonify(json_df)


@app.route("/get/job/<job_id>")
@cross_origin()
@login_required
def get_job(job_id):
    df = Get_Job(job_id)
    json_df = dataframe_to_json(df)
    return jsonify(json_df)


@app.route("/get/collection/<collection_id>")
@cross_origin()
@login_required
def get_collection(collection_id=None):
    df = Get_Collection(collection_id)
    json_df = dataframe_to_json(df)
    return jsonify(json_df)


@app.route("/get/collections")
@cross_origin()
@login_required
def get_collections():
    df = Get_Collection()
    json_df = dataframe_to_json(df)
    return jsonify(json_df)


@app.route("/get/connections")
@cross_origin()
@api_key_or_session_required(min_role=2)
def get_connections():
    df = Get_Connection()
    
    # =========================================================================
    # NEW: Process passwords for display (BACKWARD COMPATIBLE)
    # =========================================================================
    from connection_secrets import is_secret_reference
    from local_secrets import get_secrets_manager
    
    if not df.empty and 'password' in df.columns:
        manager = get_secrets_manager()
        
        # Add metadata columns
        df['_password_local'] = False
        df['_password_type'] = 'none'
        
        for idx, row in df.iterrows():
            password = row.get('password', '')
            
            if password:
                if is_secret_reference(password):
                    # NEW STYLE: Reference to local secrets
                    secret_name = password.replace('{{LOCAL_SECRET:', '').replace('}}', '')
                    if manager.exists(secret_name):
                        df.at[idx, 'password'] = '••••••••'
                        df.at[idx, '_password_local'] = True
                        df.at[idx, '_password_type'] = 'local'
                    else:
                        # Reference exists but secret missing
                        df.at[idx, 'password'] = ''
                        df.at[idx, '_password_type'] = 'missing'
                else:
                    # LEGACY: Plain text password in database
                    df.at[idx, 'password'] = '••••••••'
                    df.at[idx, '_password_type'] = 'legacy'
    # =========================================================================
    # END NEW CODE
    # =========================================================================
    
    json_df = dataframe_to_json(df)
    return jsonify(json_df)


@app.route("/get/users")
@cross_origin()
@api_key_or_session_required(min_role=3)
def get_users():
    df = Get_Users()
    json_df = dataframe_to_json(df)
    return jsonify(json_df)


@app.route("/get/user/<int:user_id>", methods=['GET'])
@cross_origin()
@admin_required(api=True)
def get_user(user_id):
    df = Get_Users(str(user_id))
    json_df = dataframe_to_json(df)
    print(86 * '+')
    print((json_df))
    print(86 * '+')
    return jsonify(json_df)


@app.route("/get/schedules/<int:job_id>")
@cross_origin()
@login_required
def get_schedules(job_id):
    try:
        id = job_id
    except:
        data = request.get_json()
        id = data['id']
        
    if id is None:
        return jsonify({"status": "error", "response": "No job id found"})
    else:
        df = Get_QuickJob_Schedule(id=str(id))

    if df is not None:
        json_df = dataframe_to_json(df)
    else:
        return jsonify({"status": "success", "response": "No schedules found"})

    return jsonify(json_df)


@app.route("/schedule/quickjob_legacy", methods = ['GET', 'POST'])
@cross_origin()
@login_required
def schedule_quickjob_legacy():
    try:
        logger.info('Received request at /schedule/quickjob...')

        # Get JSON data from the POST request
        data = request.get_json()

        id = data['id']
        job_id = data['job_id']
        task_name = data['task_name']
        start_time = data['start_time']
        frequency = data['frequency']
        enabled = data['enabled']

        logger.debug('id:' + str(id))
        logger.debug('job_id:' + str(job_id))
        logger.debug('task_name:' + str(task_name))
        logger.debug('start_time:' + str(start_time))
        logger.debug('frequency:' + str(frequency))
        logger.debug('enabled:' + str(enabled))

        # try:
        #     # Use the ast.literal_eval function to safely evaluate the input string.
        #     enabled = ast.literal_eval(enabled)
        # except (SyntaxError, ValueError):
        #     # If an error occurs during evaluation, return False.
        #     enabled = False

        logger.info('Adding Windows scheduled task...')
        result = add_quickjob_task(job_id, task_name, start_time, frequency, enabled)

        if result:
            logger.info('Adding schdedule to database...')
            if enabled:
                enabled_int = 1
            else:
                enabled_int = 0
            id = Add_Quick_Job_Schedule(id, job_id, task_name, start_time, frequency, enabled_int)
        else:
            logger.info('Failed to add Windows scheduled task...')
    except Exception as e:
        print(str(e))
        response = {"status": "error", "response": str(e)}
        logger.error(str(e))
        result = False

    if result:
        response = {"status": "success", "response": str(id)}
    else:
        response = {"status": "error", "response": "Failed to add scheduled job"}

    logger.info('Response to client:' + str(response))

    return jsonify(response)


@app.route("/schedule/quickjob", methods=['POST'])
@cross_origin()
@login_required
def schedule_quickjob():
    """Enhanced QuickJob scheduling that can use APScheduler"""
    try:
        logger.info('Received request at /schedule/quickjob...')
        
        # Get JSON data from the POST request
        data = request.get_json()
        id = data.get('id', '')
        job_id = data['job_id']
        task_name = data['task_name']
        start_time = data['start_time']
        frequency = data['frequency']
        enabled = data['enabled']
        
        if cfg.USE_APSCHEDULER_FOR_QUICKJOBS:
            # Use APScheduler backend
            result = schedule_quickjob_with_apscheduler(id, job_id, task_name, start_time, frequency, enabled)
        else:
            # Use existing Windows Task scheduler
            result = schedule_quickjob_with_windows_task(id, job_id, task_name, start_time, frequency, enabled)
        
        if result:
            response = {"status": "success", "response": str(result)}
        else:
            response = {"status": "error", "response": "Failed to add scheduled job"}

    except Exception as e:
        print(str(e))
        response = {"status": "error", "response": str(e)}
        logger.error(str(e))

    logger.info('Response to client:' + str(response))
    return jsonify(response)

def schedule_quickjob_with_windows_task(id, job_id, task_name, start_time, frequency, enabled):
    """Original Windows Task Scheduler implementation"""
    try:
        logger.info('Adding Windows scheduled task...')
        
        # Handle the id parameter properly for new records
        if id == '' or id is None or id == '0':
            id = 0
        
        result = add_quickjob_task(job_id, task_name, start_time, frequency, enabled)

        if result:
            logger.info('Adding schedule to database...')
            enabled_int = 1 if enabled else 0
            schedule_id = Add_Quick_Job_Schedule(id, job_id, task_name, start_time, frequency, enabled_int)
            return schedule_id
        else:
            logger.info('Failed to add Windows scheduled task...')
            return False
            
    except Exception as e:
        logger.error(f'Error scheduling with Windows Task: {str(e)}')
        return False

def schedule_quickjob_with_apscheduler(id, job_id, task_name, start_time, frequency, enabled):
    """APScheduler implementation using existing JobSchedulerService"""
    try:
        logger.info('Scheduling QuickJob with APScheduler...')
        
        # Handle the id parameter properly for new records
        # The existing Add_Quick_Job_Schedule function expects id=0 for new records
        if id == '' or id is None or id == '0':
            id = 0
        
        logger.info(f'Calling Add_Quick_Job_Schedule with id={id}, job_id={job_id}')
        
        # First, save to QuickJobSchedule table (for compatibility)
        enabled_int = 1 if enabled else 0
        quickjob_schedule_id = Add_Quick_Job_Schedule(id, job_id, task_name, start_time, frequency, enabled_int)
        
        if not quickjob_schedule_id:
            logger.error('Failed to create QuickJobSchedule record')
            return False
        
        # Create ScheduledJob and Schedule records for APScheduler if enabled
        apscheduler_job_id = create_apscheduler_job_for_quickjob(
            job_id, task_name, start_time, frequency, enabled_int, quickjob_schedule_id
        )
        
        if apscheduler_job_id:
            logger.info(f'Successfully created APScheduler job: {apscheduler_job_id}')
        else:
            logger.error('Failed to create APScheduler job')
            return False
        
        return quickjob_schedule_id
            
    except Exception as e:
        logger.error(f'Error scheduling with APScheduler: {str(e)}')
        return False

def create_apscheduler_job_for_quickjob(job_id, task_name, start_time, frequency, enabled_int, quickjob_schedule_id):
    """Create ScheduledJob and Schedule records for APScheduler using existing functions"""
    try:
        # TODO: This is a hack to get the helper functions from scheduler_routes.py - replace with API calls
        # Import the helper functions from scheduler_routes
        from scheduler_routes import _create_schedule, _update_schedule
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Check if ScheduledJob already exists for this job_id
        cursor.execute("""
            SELECT ScheduledJobId FROM ScheduledJobs 
            WHERE TargetId = ? AND JobType = 'agent'
        """, (job_id,))
        existing_job = cursor.fetchone()
        
        if existing_job:
            # Update existing ScheduledJob record
            scheduled_job_id = existing_job[0]
            cursor.execute("""
                UPDATE ScheduledJobs 
                SET JobName = ?, Description = ?, IsActive = ?, ModifiedAt = getutcdate()
                WHERE ScheduledJobId = ?
            """, (task_name, f"QuickJob schedule: {task_name}", enabled_int, scheduled_job_id))
        else:
            # Create new ScheduledJob record
            cursor.execute("""
                INSERT INTO ScheduledJobs (
                    JobName, JobType, TargetId, Description, CreatedBy, CreatedAt, IsActive
                )
                VALUES (?, 'agent', ?, ?, 'quickjob_scheduler', getutcdate(), ?)
            """, (task_name, job_id, f"QuickJob schedule: {task_name}", enabled_int))
            
            # Get the ID of the newly created scheduler job
            cursor.execute("SELECT @@IDENTITY")
            scheduled_job_id = cursor.fetchone()[0]
        
        conn.commit()
        
        # Parse start_time and map frequency to schedule data format
        try:
            start_date = datetime.datetime.strptime(start_time, '%Y-%m-%d %H:%M')
        except:
            from datetime import datetime as dt
            start_date = dt.strptime(start_time, '%Y-%m-%d %H:%M')

        # Create schedule data in the format expected by the schedule functions
        schedule_data = {
            'type': 'interval',
            'start_date': start_date.isoformat(),
            'is_active': True
        }
        
        # Map frequency to interval settings
        freq_lower = frequency.lower()
        if freq_lower == 'daily':
            schedule_data['interval_days'] = 1
        elif freq_lower == 'hourly':
            schedule_data['interval_hours'] = 1
        elif freq_lower == 'weekly':
            schedule_data['interval_days'] = 7
        else:
            # Default to daily if unknown
            schedule_data['interval_days'] = 1
        
        # Check if there's already a schedule for this scheduled job
        cursor.execute("""
            SELECT ScheduleId FROM ScheduleDefinitions 
            WHERE ScheduledJobId = ? AND IsActive = 1
        """, (scheduled_job_id,))
        existing_schedule = cursor.fetchone()
        
        if existing_schedule:
            # Update existing schedule
            schedule_id = existing_schedule[0]
            success = _update_schedule(cursor, schedule_id, schedule_data)
            
            if not success:
                conn.rollback()
                cursor.close()
                conn.close()
                logger.error('Failed to update schedule using _update_schedule function')
                return None
                
            logger.info(f'Updated Schedule {schedule_id} for ScheduledJob {scheduled_job_id} and QuickJob {job_id}')
        else:
            # Create new schedule
            schedule_id = _create_schedule(cursor, scheduled_job_id, schedule_data)
            
            if not schedule_id:
                conn.rollback()
                cursor.close()
                conn.close()
                logger.error('Failed to create schedule using _create_schedule function')
                return None
                
            logger.info(f'Created ScheduledJob {scheduled_job_id} and Schedule {schedule_id} for QuickJob {job_id}')
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return scheduled_job_id
        
    except Exception as e:
        logger.error(f'Error creating/updating APScheduler job records: {str(e)}')
        if conn:
            conn.rollback()
            cursor.close()
            conn.close()
        return None

# Optional: Add status endpoint to show which backend is being used
@app.route('/api/quickjob/scheduler/backend', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_quickjob_scheduler_backend():
    """Get current QuickJob scheduler backend"""
    try:
        backend = "APScheduler" if cfg.USE_APSCHEDULER_FOR_QUICKJOBS else "Windows Task"
        
        return jsonify({
            'status': 'success',
            'backend': backend,
            'use_apscheduler': cfg.USE_APSCHEDULER_FOR_QUICKJOBS
        })
        
    except Exception as e:
        logger.error(f"Error getting QuickJob scheduler backend: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Failed to get scheduler backend: {str(e)}'
        }), 500

@app.route("/delete/user", methods = ['GET', 'POST'])
@cross_origin()
@api_key_or_session_required(min_role=3)
def delete_user():
    try:
        logger.info('Received request at /delete/user...')

        # Get JSON data from the POST request
        data = request.get_json()
        user_id = data['user_id']
        result = Delete_User(user_id)

        if result:
            response = {"status": "success", "response": "Successfully deleted user"}
        else:
            response = {"status": "error", "response": "Failed to delete user"}
    except Exception as e:
        print(str(e))
        logger.error(str(e))
        response = {"status": "error", "response": str(e)}
        result = False

    logger.info('Response to client:' + str(response))

    return jsonify(response)


@app.route("/delete/collection", methods = ['GET', 'POST'])
@cross_origin()
@login_required
def delete_collection():
    try:
        logger.info('Received request at /delete/collection...')

        try:
            # Get JSON data from the POST request
            data = request.get_json()
            collection_id = data['collection_id']
        except:
            collection_id = request.args.get('collection_id')

        result = Delete_Collection(collection_id)

        if result:
            response = {"status": "success", "response": "Successfully deleted collection"}
        else:
            response = {"status": "error", "response": "Failed to delete collection"}
    except Exception as e:
        print(str(e))
        logger.error(str(e))
        response = {"status": "error", "response": str(e)}
        result = False

    logger.info('Response to client:' + str(response))

    return jsonify(response)


@app.route("/delete/connection/<int:connection_id>", methods = ['GET', 'POST'])
@cross_origin()
@api_key_or_session_required(min_role=2)
def delete_connection(connection_id=None):
    try:
        logger.info('Received request at /delete/connection...')

        # =========================================================================
        # Delete the local secret first
        # =========================================================================
        delete_connection_secret(connection_id)

        if connection_id is None:
            try:
                # Get JSON data from the POST request
                data = request.get_json()
                connection_id = data['connection_id']
            except:
                connection_id = request.args.get('connection_id')

        result = Delete_Connection(connection_id)

        if result:
            response = {"status": "success", "response": "Successfully deleted connection"}
        else:
            response = {"status": "error", "response": "Failed to delete connection"}
    except Exception as e:
        print(str(e))
        logger.error(str(e))
        response = {"status": "error", "response": str(e)}
        result = False

    logger.info('Response to client:' + str(response))

    return jsonify(response)


@app.route("/add/user", methods = ['GET', 'POST'])
@cross_origin()
@api_key_or_session_required(min_role=3)
def add_update_user():
    try:
        logger.info('Received request at /add/user...')

        # Get JSON data from the POST request
        data = request.get_json()

        user_id = data.get('user_id')
        user_name = data.get('user_name')
        role = data.get('role')
        name = data.get('name')
        email = data.get('email')
        phone = data.get('phone')
        password = data.get('password')
        
        logger.debug('User ID:' + str(user_id))
        logger.debug('User Name:' + str(user_name))
        logger.debug('Role:' + str(role))
        logger.debug('email:' + str(email))
        logger.debug('phone:' + str(phone))
        logger.debug('name:' + str(name))

        # Check if the password is already hashed
        if not password.startswith('$2b$') and password != '':
            logger.debug('Password is not hashed. Hashing the password...')
            password = hash_the_password(password)
        
        logger.debug('Password: [SET]')

        new_id, result = Add_User(user_id, user_name, role, email, phone, name, password)

        logger.debug('New ID:' + str(new_id))
        logger.debug('DB Result:' + str(result))
    except Exception as e:
        logger.error(str(e))
        response = {"status": "error", "response": "Failed to add user"}
        result = False

    if result:
        response = {"status": "success", "response": str(new_id)}
    else:
        response = {"status": "error", "response": "Failed to add user"}

    logger.info('Response to client:' + str(response))

    return jsonify(response)


@app.route("/add/collection", methods = ['GET', 'POST'])
@cross_origin()
@login_required
def add_update_collection():
    try:
        logger.info('Received request at /add/collection...')

        # Get JSON data from the POST request
        data = request.get_json()

        collection_id = data['collection_id']
        collection_name = data['collection_name']

        logger.debug('Collection ID:' + str(collection_id))
        logger.debug('Collection Name:' + str(collection_name))

        new_id, result = Add_Collection(collection_id, collection_name)

        logger.debug('New ID:' + str(new_id))
        logger.debug('DB Result:' + str(result))
    except:
        response = {"status": "error", "response": "Failed to add group"}
        result = False

    if result:
        response = {"status": "success", "response": str(new_id)}
    else:
        response = {"status": "error", "response": "Failed to add group"}

    logger.info('Response to client:' + str(response))

    return jsonify(response)


def clean_empty_to_none(value, convert_to_int=False):
    """Convert empty strings and 'null' strings to None"""
    if value == '' or value == 'null' or value is None:
        return None
    if convert_to_int and value is not None:
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
    return value


@app.route("/add/connection-legacy", methods = ['GET', 'POST'])
@cross_origin()
@api_key_or_session_required(min_role=2)
def add_update_connection_legacy():
    try:
        logger.info('Received request at /add/connection...')

        # Get JSON data from the POST request
        data = request.get_json()
        print("Data:", data)
        
        connection_id = data['connection_id']
        connection_name = data['connection_name']
        server = clean_empty_to_none(data.get('server', ''))
        port = clean_empty_to_none(data.get('port', '0'), convert_to_int=True)
        database_name = clean_empty_to_none(data.get('database_name', ''))
        database_type = data.get('database_type', 'unknown')
        user_name = clean_empty_to_none(data.get('user_name', ''))
        password = clean_empty_to_none(data.get('password', ''))
        parameters = data.get('parameters', '')  # Can be empty string
        connection_string = data.get('connection_string', '')  # Should not be None
        odbc_driver = data.get('odbc_driver', '')  # Can be empty string

        instance_url = data.get('instance_url', '')
        token = data.get('token', '')
        api_key = data.get('api_key', '')
        dsn = data.get('dsn', '')

        # Default port for SQL Server if not specified or zero
        if database_type == 'SQL Server' and (not port or port == 0):
            port = 1433

        print('ODBC Driver:', odbc_driver)
        print('Port:', port)
        print('Parameters:', parameters)
        print('Instance URL:', instance_url)

        logger.debug('Connection ID:' + str(connection_id))
        logger.debug('Connection Name:' + str(connection_name))
        logger.debug('ODBC Driver:' + str(odbc_driver))
        logger.debug('Instance URL:' + str(instance_url))

        new_id, result = Add_Connection(connection_id, connection_name, server, port, database_name, database_type, user_name, password, parameters, connection_string, odbc_driver, instance_url, token, api_key, dsn)

        logger.debug('New ID:' + str(new_id))
        logger.debug('DB Result:' + str(result))
    except Exception as e:
        print(str(e))
        response = {"status": "error", "response": "Failed to add connection"}
        result = False

    if result:
        response = {"status": "success", "response": str(new_id)}
    else:
        response = {"status": "error", "response": "Failed to add connection"}

    logger.info('Response to client:' + str(response))

    return jsonify(response)


def get_connection_by_id(connection_id):
    """Fetch a single connection by ID to get existing password."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context for RLS
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        cursor.execute("SELECT password FROM Connections WHERE id = ?", (connection_id,))
        row = cursor.fetchone()
        
        conn.close()
        return {'password': row[0]} if row else None
    except Exception as e:
        logger.error(f"Error fetching connection: {e}")
        return None


@app.route("/add/connection", methods = ['GET', 'POST'])
@cross_origin()
@api_key_or_session_required(min_role=2)
def add_update_connection():
    try:
        logger.info('Received request at /add/connection...')

        data = request.get_json()
        print("Data:", data)
        
        connection_id = data['connection_id']
        connection_name = data['connection_name']
        server = clean_empty_to_none(data.get('server', ''))
        port = clean_empty_to_none(data.get('port', '0'), convert_to_int=True)
        database_name = clean_empty_to_none(data.get('database_name', ''))
        database_type = data.get('database_type', 'unknown')
        user_name = clean_empty_to_none(data.get('user_name', ''))
        password = clean_empty_to_none(data.get('password', ''))
        parameters = data.get('parameters', '')
        connection_string = data.get('connection_string', '')
        odbc_driver = data.get('odbc_driver', '')

        instance_url = data.get('instance_url', '')
        token = data.get('token', '')
        api_key = data.get('api_key', '')
        dsn = data.get('dsn', '')
        
        is_update = connection_id and int(connection_id) > 0
        password_to_store = password
        needs_secret_id_update = False
        
        if password:
            if password == '••••••••':
                # Password unchanged - fetch existing from database
                if is_update:
                    existing_password = get_connection_password_by_id(connection_id)
                    password_to_store = existing_password if existing_password else ''
                else:
                    password_to_store = ''
            elif not is_secret_reference(password):
                # New password entered - store in local secrets
                temp_id = int(connection_id) if is_update else 0
                password_to_store = store_connection_password(temp_id, password, connection_name)
                needs_secret_id_update = not is_update
        
        password = password_to_store
        # =====================================================================
        # END NEW CODE
        # =====================================================================

        print('ODBC Driver:', odbc_driver)
        print('Port:', port)
        print('Parameters:', parameters)
        print('Instance URL:', instance_url)

        logger.debug('Connection ID:' + str(connection_id))
        logger.debug('Connection Name:' + str(connection_name))
        logger.debug('ODBC Driver:' + str(odbc_driver))
        logger.debug('Instance URL:' + str(instance_url))

        new_id, result = Add_Connection(connection_id, connection_name, server, port, database_name, database_type, user_name, password, parameters, connection_string, odbc_driver, instance_url, token, api_key, dsn)
        new_id = int(new_id) 
        # =====================================================================
        # NEW: Update secret ID for new connections
        # =====================================================================
        print('result', result)
        print('needs_secret_id_update:', needs_secret_id_update)
        print('new_id:', new_id)

        if result and needs_secret_id_update and new_id:
            update_connection_secret_id(0, int(new_id))
            new_reference = create_secret_reference(get_connection_secret_name(int(new_id)))
            print('new_reference:', new_reference)
            print('new_id:', new_id)
            update_connection_password_only(new_id, new_reference)
        # =====================================================================
        # END NEW CODE
        # =====================================================================

        logger.debug('New ID:' + str(new_id))
        logger.debug('DB Result:' + str(result))
    except Exception as e:
        print(str(e))
        response = {"status": "error", "response": "Failed to add connection"}
        result = False

    if result:
        response = {"status": "success", "response": str(new_id)}
    else:
        response = {"status": "error", "response": "Failed to add connection"}

    logger.info('Response to client:' + str(response))

    return jsonify(response)


@app.route('/api/connections', methods=['GET', 'POST'])
@cross_origin()
@api_key_or_session_required(min_role=2)
def api_add_update_connection():
    """
    Unified /api/connections endpoint for builder agent and external API access.

    For POST (create/update):
    Normalises field names from the builder's action registry schema
    (name, connection_type, database, username) to the legacy form field
    names (connection_name, database_type, database_name, user_name)
    expected by add_update_connection().

    IMPORTANT: We call the __wrapped__ (unwrapped) versions of the inner
    functions to bypass their own @developer_required / @login_required
    decorators. Auth is already handled by THIS route's decorator.
    """
    # Fully unwrap the inner functions to bypass their decorator chains
    # (get_connections / add_update_connection have @developer_required which
    #  wraps @login_required, causing 302 redirects for API-key-only requests).
    # Auth is already handled by THIS route's @api_key_or_session_required.
    import inspect
    _get_connections = inspect.unwrap(get_connections)
    _add_update_connection = inspect.unwrap(add_update_connection)

    if request.method == 'GET':
        return _get_connections()

    # ── Normalise builder field names → legacy field names ──────────
    data = request.get_json(silent=True) or {}
    normalised = {}
    # Map builder-style → legacy-style field names
    field_map = {
        'name': 'connection_name',
        'connection_type': 'database_type',
        'database': 'database_name',
        'username': 'user_name',
    }
    for key, value in data.items():
        mapped_key = field_map.get(key, key)
        normalised[mapped_key] = value

    # Ensure required fields have defaults for create
    normalised.setdefault('connection_id', 0)  # 0 = new connection
    normalised.setdefault('connection_name', normalised.get('name', ''))

    # Map connection_type enum values to database_type display names
    type_display = {
        'sql_server': 'SQL Server',
        'mysql': 'MySQL',
        'postgresql': 'Postgres',
        'sqlite': 'SQLite',
        'oracle': 'Oracle',
        'odbc': 'ODBC',
    }
    db_type = normalised.get('database_type', '')
    if db_type in type_display:
        normalised['database_type'] = type_display[db_type]

    # Determine if trusted (Windows) auth vs SQL auth
    auth_type = normalised.pop('authentication', '')
    if 'trusted' in str(auth_type).lower() or 'windows' in str(auth_type).lower():
        normalised['use_trusted'] = True
    else:
        normalised.setdefault('use_trusted', False)

    # Patch the request JSON so add_update_connection() sees normalised fields
    request._cached_json = (normalised, normalised)
    return _add_update_connection()

@app.route('/api/connections/<int:connection_id>/execute', methods=['POST'])
@cross_origin()
@api_key_or_session_required(min_role=2)
def api_execute_connection_query(connection_id):
    """Execute a SQL query against a connection. Used by the builder agent for validation."""
    try:
        data = request.get_json() or {}
        query = data.get('query', '')
        if not query:
            return jsonify({"status": "error", "response": "No query provided"}), 400

        conn_str, _, database_type = get_database_connection_string(connection_id)
        if not conn_str:
            return jsonify({"status": "error", "response": f"Connection {connection_id} not found or invalid"}), 404

        result = execute_sql_for_llm(conn_str, query)
        return jsonify({"status": "success", "response": result})
    except Exception as e:
        logger.error(f"[api_execute_connection_query] Error: {e}")
        return jsonify({"status": "error", "response": str(e)}), 500


@app.route('/api/connections/<int:connection_id>/test', methods=['POST'])
@cross_origin()
@api_key_or_session_required()
def api_test_connection(connection_id):
    import inspect

    # Fetch connection details from database
    query = "SELECT * FROM Connections WHERE id = ?"
    conn_result = query_app_database(query, (connection_id,))

    if not conn_result:
        return jsonify({
            "status": "error",
            "message": "Connection not found"
        }), 404

    conn_info = conn_result[0]

    # Get data from request body (may have overrides)
    data = request.get_json() or {}

    # Populate with database fields (request data takes precedence if provided)
    data.setdefault('connection_id', connection_id)
    data.setdefault('database_type', conn_info.get('database_type'))
    data.setdefault('server', conn_info.get('server'))
    data.setdefault('port', conn_info.get('port'))
    data.setdefault('database_name', conn_info.get('database_name'))
    data.setdefault('user_name', conn_info.get('user_name'))
    data.setdefault('password', conn_info.get('password'))
    data.setdefault('parameters', conn_info.get('parameters'))
    data.setdefault('connection_string', conn_info.get('connection_string'))
    data.setdefault('odbc_driver', conn_info.get('odbc_driver'))

    request._cached_json = (data, data)
    _test_connection = inspect.unwrap(test_connection)
    return _test_connection()


@app.route("/add/group", methods = ['GET', 'POST'])
@cross_origin()
@api_key_or_session_required(min_role=3)
def add_update_group():
    try:
        logger.info('Received request at /add/group...')
        result = False
        # Get JSON data from the POST request
        data = request.get_json()

        group_id = data['id']
        group_name = data['group_name']

        logger.debug('group_id:' + str(group_id))
        logger.debug('group_name:' + str(group_name))

        new_id = add_group(group_name, group_id)
        result = True
        logger.debug('New ID:' + str(new_id))
    except:
        response = {"status": "error", "response": "Failed to add group"}
        result = False

    if result:
        response = {"status": "success", "response": str(new_id)}
    else:
        response = {"status": "error", "response": "Failed to add group"}

    logger.info('Response to client:' + str(response))

    return jsonify(response)


@app.route("/delete/group/<int:group_id>", methods = ['GET', 'POST'])
@cross_origin()
@api_key_or_session_required(min_role=3)
def delete_groups(group_id=None):
    try:
        logger.info('Received request at /delete/gruop...')

        if group_id is None:
            try:
                # Get JSON data from the POST request
                data = request.get_json()
                group_id = data['group_id']
            except:
                group_id = request.args.get('group_id')

        result = delete_group(group_id)

        if result:
            response = {"status": "success", "response": "Successfully deleted group"}
        else:
            response = {"status": "error", "response": "Failed to delete group"}
    except Exception as e:
        print(str(e))
        logger.error(str(e))
        response = {"status": "error", "response": str(e)}
        result = False

    logger.info('Response to client:' + str(response))

    return jsonify(response)


@app.route("/get/groups")
@cross_origin()
@api_key_or_session_required(min_role=3)
def get_group():
    df = get_groups()
    json_df = dataframe_to_json(df)
    return jsonify(json_df)


@app.route("/get/user_groups/<int:group_id>", methods = ['GET', 'POST'])
@cross_origin()
@api_key_or_session_required(min_role=3)
def get_user_group(group_id=None):
    if group_id is None:
        try:
            # Get JSON data from the POST request
            data = request.get_json()
            group_id = data['group_id']
        except:
            group_id = request.args.get('group_id')

    assigned_users, unassigned_users = get_user_group_assigned_unassigned(group_id)

    return jsonify({
        'assigned_users': [{'id': user[0], 'user_name': user[1], 'name': user[2]} for user in assigned_users],
        'unassigned_users': [{'id': user[0], 'user_name': user[1], 'name': user[2]} for user in unassigned_users]
    })


@app.route("/get/quickjobs")
@cross_origin()
@login_required
def get_quick_jobs():
    df = Get_Quick_Job_DF(user_id=current_user.id)
    json_df = dataframe_to_json(df)
    return jsonify(json_df)


@app.route("/get/quickjob/<job_id>")
@cross_origin()
@login_required
def get_quick_job(job_id=None):
    df = Get_Quick_Job_DF(job_id)
    json_df = dataframe_to_json(df)
    return jsonify(json_df)


@app.route("/add/quickjob", methods = ['GET', 'POST'])
@cross_origin()
@login_required
def add_quick_job():
    try:
        logger.info('Received request @ /add/quickjob...')

        # Get payload data
        try:
            # Get JSON data from the POST request
            data = request.get_json()

            job_id = data['job_id']
            job_desc = data['job_desc']
            ai_system = data['ai_system']
            enabled = data['enabled']
            agent_id = data['agent_id']

            if not enabled:
                enabled_int = 0
            else:
                enabled_int = 1

            collection_id = data['collection_id']

            db_handler.set_job_id(job_id)
        except:
            logger.info('Failed to get data from POST, trying GET method...')

            job_id = request.args.get('job_id')
            job_desc = request.args.get('job_desc')
            ai_system = request.args.get('ai_system')
            enabled = request.args.get('enabled')
            enabled_int = enabled
            collection_id = request.args.get('collection_id')
            agent_id = request.args.get('agent_id')

        logger.debug('job_id:' + str(job_id))
        logger.debug('job_desc:' + str(job_desc))
        logger.debug('ai_system:' + str(ai_system))
        logger.debug('enabled:' + str(enabled_int))
        logger.debug('collection_id:' + str(collection_id))
        logger.debug('agent_id:' + str(agent_id))

        result = Add_Quick_Job(job_id, job_desc, ai_system, enabled_int, collection_id, agent_id)

        logger.info('Result:' + str(result))

        if result:
            response = {"status": "success", "response": str(result)}
        else:
            response = {"status": "error", "response": "Failed to add quick job"}
    except Exception as e:
        print(str(e))
        response = {"status": "error", "response": str(e)}

    logger.debug('Response to client:' + str(response))

    return jsonify(response)


@app.route("/delete/quickjob", methods = ['GET', 'POST'])
@cross_origin()
@login_required
def delete_quick_job():
    try:
        logger.info('Received request @ /delete/quickjob...')

        # Get payload data
        try:
            # Get JSON data from the POST request
            data = request.get_json()

            job_id = data['job_id']

            db_handler.set_job_id(job_id)
        except:
            logger.info('Failed to get data from POST, trying GET method...')

            job_id = request.args.get('job_id')

        logger.debug('job_id:' + str(job_id))

        result = Delete_Quick_Job(job_id)

        logger.info('Result:' + str(result))

        if result:
            response = {"status": "success", "response": "Successfully deleted quick job"}
        else:
            response = {"status": "error", "response": "Failed to delete quick job"}
    except Exception as e:
        print(str(e))
        response = {"status": "error", "response": str(e)}

    logger.debug('Response to client:' + str(response))

    return jsonify(response)


@app.route("/add/job", methods = ['GET', 'POST'])
@cross_origin()
@login_required
def add_job():
    #job_id, job_desc, ai_system, ai_prompt, enabled, fn_type, fn_text, fn_pass_type, fn_pass_text, fn_fail_type, fn_fail_text, fn_finish_type, fn_finish_text, collection_id
    try:
        job_id = request.args.get('job_id')
        job_desc = request.args.get('job_desc')
        ai_system = request.args.get('ai_system')
        ai_prompt = request.args.get('ai_prompt')
        enabled = request.args.get('enabled')
        fn_type = request.args.get('fn_type')
        fn_text = request.args.get('fn_text')
        fn_pass_type = request.args.get('fn_pass_type')
        fn_pass_text = request.args.get('fn_pass_text')
        fn_fail_type = request.args.get('fn_fail_type')
        fn_fail_text = request.args.get('fn_fail_text')
        fn_finish_type = request.args.get('fn_finish_type')
        fn_finish_text = request.args.get('fn_finish_text')
        collection_id = request.args.get('collection_id')
        pass_fail = request.args.get('pass_fail', "")

        result = Add_Job(job_id, job_desc, ai_system, ai_prompt, enabled, fn_type, fn_text, fn_pass_type, fn_pass_text, fn_fail_type, fn_fail_text, fn_finish_type, fn_finish_text, collection_id, pass_fail)

        if result:
            response = {"status": "success", "response": "Job added successfully"}
        else:
            response = {"status": "error", "response": "Failed to add job"}
    except Exception as e:
        print(str(e))
        response = {"status": "error", "response": str(e)}

    return jsonify(response)


def remove_html_tags(text):
    """
    Remove HTML tags from a string.

    Args:
    text (str): The string containing HTML tags.

    Returns:
    str: The string with HTML tags removed.
    """
    clean_text = re.sub(r'<.*?>', '', text)
    clean_text = clean_text.replace('&quot;','"')
    return clean_text


def clean_email_input_text(text):
    """
    Remove HTML tags from a string.

    Args:
    text (str): The string containing HTML tags.

    Returns:
    str: The string with HTML tags removed.
    """
    clean_text = re.sub(r'<.*?>', '', text)
    clean_text = re.sub(r"<[^>]*>", "", text)
    clean_text = re.sub(r"\&nbsp;.*$", "", clean_text)
    clean_text = clean_text.replace('&quot;','"')
    return clean_text


@app.route("/chat/general_system", methods = ['GET', 'POST'])
@cross_origin()
@api_key_or_session_required()
def chat_general_system():
    """ Receives string formatted as normal OpenAI message history.
    INPUT:
        prompt = String prompt
        hist = String formatted as normal OpenAI message history
    OUTPUT:
        {status: "", response: "", chat_history: []}
    RETURNS THE COMPLETE HIST
    """
    # API Call
    try:
        db_handler.set_job_id(999)
        logger.info('Received API request at /chat/general_system...')

        try:
            # Get JSON data from the POST request
            data = request.get_json()
            agent_id = data['agent_id']
            prompt = data['prompt']
            hist = data['hist']

            # NEW: Environment parameters
            environment_id = data.get('environment_id', None)
            use_environment = data.get('use_environment', False)
        except:
            logger.info('Failed to get data from POST, trying GET params...')
            agent_id = request.args.get('agent_id')
            prompt = request.args.get('prompt')
            hist = request.args.get('hist')

            # NEW: Environment parameters
            environment_id = request.args.get('environment_id', None)
            use_environment = request.args.get('use_environment', False)

        hist = str(hist)

        logger.info('========== INPUT ==========')
        logger.info('Agent ID:' + str(agent_id))
        logger.info('Prompt (input):' + str(prompt))
        #logger.info('Chat History (input):' + str(hist))

        logger.info(f'Agent ID: {agent_id}, Use Environment: {use_environment}, Env ID: {environment_id}')

        # If no history
        if hist is None or hist == '':
            hist = '[]'

        # Auto-detect if agent has environment
        if not use_environment and not environment_id:
            # Check for agent's assigned environment
            use_environment, environment_id = should_use_environment(agent_id)
            logger.info(f"Auto-detected environment {environment_id} for agent {agent_id}")

         # Check if we should use custom environment
        if use_environment and environment_id:
            logger.info(f'Custom Environment: Executing agent {agent_id} in environment {environment_id}')
            
            # Import the enhanced executor
            from agent_environment_executor import AgentEnvironmentExecutor
            
            # Get connection info
            connection_string = get_db_connection_string()
            tenant_id = os.getenv('API_KEY')
            
            # Create executor
            executor = AgentEnvironmentExecutor(connection_string, tenant_id)
            
            # Execute in environment
            result = executor.execute_in_environment(
                agent_id=int(agent_id),
                prompt=prompt,
                chat_history=hist,
                use_smart_render=False,
                timeout=300
            )
            
            # Handle the response
            if result.get('status') == 'success':
                return jsonify({
                    'status': 'success',
                    'response': result.get('response', ''),
                    'chat_history': result.get('chat_history', hist),
                    'used_custom_environment': True,
                    'environment_name': result.get('environment', 'custom')
                })
            elif result.get('status') == 'no_environment':
                # No environment assigned, fall back to standard execution
                logger.info('No environment found, falling back to standard execution')
            else:
                # Error in environment execution
                return jsonify({
                    'status': 'error',
                    'response': f"Environment execution error: {result.get('error', 'Unknown error')}",
                    'chat_history': hist,
                    'used_custom_environment': False
                })

        # Lazy-load agent if not yet in active_agents (e.g., just created by builder)
        if int(agent_id) not in active_agents:
            logger.info(f'Agent {agent_id} not in active_agents — loading on demand')
            load_agents(agent_id=int(agent_id))
            if int(agent_id) not in active_agents:
                return jsonify({
                    "status": "error",
                    "response": f"Agent {agent_id} could not be loaded. It may not exist.",
                    "chat_history": []
                })

        # Initialize agent
        timer = Timer()
        timer.start()
        print(86 * '=')
        print('Init chat hist...')
        print('=== Agent Info ===')
        print('Agent ID:', agent_id)
        print('Agent Name:', active_agents[int(agent_id)].AGENT_NAME)
        active_agents[int(agent_id)].initialize_chat_history(eval(hist))
        print(86 * '=')
        print('Run...')
        response = active_agents[int(agent_id)].run(prompt, use_smart_render=False)
        print(86 * '=')
        print('Get chat hist...')
        chat_history = active_agents[int(agent_id)].get_chat_history()

        logger.info('========== OUTPUT ==========')
        logger.info('Response:' + str(response))

        result = {"status": "success", "response": response, "chat_history": chat_history}
        timer.stop()
        try:
            track_agent_executed(agent_id, True, timer.elapsed_ms, estimate_token_count(prompt), estimate_token_count(response))
        except:
            logger.warning('Failed to track agent executed.')
    except Exception as e:
        print(str(e))
        logger.error(str(e))
        result = {"status": "error", "response": str(e), "chat_history": []}
        capture_exception(e, {'agent_id': agent_id})  # telemetry tracking

    return jsonify(result)


@app.route("/chat/general", methods = ['GET', 'POST'])
@cross_origin()
@api_key_or_session_required()
def chat_general():
    """ 
    Receives string formatted as normal OpenAI message history.
    Modified to support rich content rendering.
    INPUT:
        prompt = String prompt
        hist = String formatted as normal OpenAI message history
    OUTPUT:
        {status: "", response: "", chat_history: []}
    RETURNS THE COMPLETE HIST
    """
    # API Call
    try:
        db_handler.set_job_id(999)
        logger.info('Received API request at /chat/general...')
        timer = Timer()
        timer.start()
        try:
            set_user_id_for_tracking('agent_chat')

            # Get JSON data from the POST request
            data = request.get_json()
            agent_id = data['agent_id']
            prompt = data['prompt']
            hist = data['hist']

            # Environment parameters (optional)
            environment_id = data.get('environment_id', None)
            use_environment = data.get('use_environment', False)

            # NEW: Get conversation_id for history continuation
            conversation_id = data.get('conversation_id', None)

            add_breadcrumb(
                message="Received API request at /chat/general",
                category="general",
                level="info",
                data={"agent_id": agent_id, "environment_id": environment_id}
                )
        except:
            logger.info('Failed to get data from POST, trying GET params...')
            agent_id = request.args.get('agent_id')
            prompt = request.args.get('prompt')
            hist = request.args.get('hist')

            # Environment parameters (optional)
            environment_id = request.args.get('environment_id', None)
            use_environment = request.args.get('use_environment', False)
            conversation_id = request.args.get('conversation_id', None)

        hist = str(hist)

        logger.info('========== INPUT ==========')
        logger.info('Agent ID:' + str(agent_id))
        logger.info('Prompt (input):' + str(prompt))

        # If no history
        if hist is None or hist == '':
            hist = '[]'

        # Initialize agent - USING YOUR EXISTING active_agents DICTIONARY OR user-specific dictionary

        # Get the appropriate agent for this user
        print(86 * '=')
        print('Aquiring agent for chat...')
        print('=== Agent Info ===')
        print('Agent ID:', agent_id)
        # GOOD - Check authentication first
        try:
            if current_user.is_authenticated:
                user_id = current_user.id
            else:
                user_id = None  # or handle anonymous case
        except:
            user_id = None
        print('User ID:', user_id)

        try:
            # =====================================================================
            # Initialize conversation history tracking
            # =====================================================================
            if user_id and not conversation_id:
                conversation_id = get_or_create_conversation(int(agent_id), user_id)
            
            # Save user message to local history
            if conversation_id:
                save_chat_message(
                    conversation_id=conversation_id,
                    role='user',
                    content=prompt,
                    user_id=user_id
                )
            # =====================================================================
        except Exception as e:
            print(f"Error saving conversation history: {str(e)}")

        # Auto-detect if agent has environment
        if not use_environment and not environment_id:
            # Check for agent's assigned environment
            use_environment, environment_id = should_use_environment(agent_id)
            logger.info(f"Auto-detected environment {environment_id} for agent {agent_id} with use environment value of {use_environment}")
            print('Env ID:', environment_id)
            print('Use Env:', use_environment)

        print('=== Env Info ===')
        print('Env ID:', environment_id)
        print('Use Env:', use_environment)

        # Check if we should use environment execution
        if use_environment or environment_id:
            logger.info('========== Environment Execution ==========')
            print('Executing agent from custom environment...')

            # Use the EXISTING AgentEnvironmentExecutor
            from agent_environment_executor import AgentEnvironmentExecutor
            
            connection_string = get_db_connection_string()
            tenant_id = os.getenv('API_KEY')
            
            executor = AgentEnvironmentExecutor(connection_string, tenant_id)

            add_breadcrumb(
                message="Executing in environment",
                category="general",
                level="info",
                data={"agent_id": agent_id, "environment_id": environment_id}
                )
            
            # Execute with smart rendering enabled
            result = executor.execute_in_environment(
                agent_id=int(agent_id),
                prompt=prompt,
                chat_history=hist,
                use_smart_render=True,
                timeout=cfg.ENVIRONMENT_PROCESSES_TIMEOUT,
                user_id=user_id
            )

            print('Environment execution result:', result)
            logger.info(f'Environment execution result: {result}')
            
            # Handle the response
            if result.get('status') == 'success':
                print('Environment execution successful')
                try:
                    response = result.get('response')
                
                    # Save assistant response to history - PRESERVE RICH CONTENT
                    if conversation_id:
                        if isinstance(response, dict) and (response.get('type') == 'rich_content' or 'blocks' in response):
                            save_chat_message(
                                conversation_id=conversation_id,
                                role='assistant',
                                content=response,
                                content_type='rich_content',
                                user_id=user_id
                            )
                        elif isinstance(response, dict):
                            save_chat_message(
                                conversation_id=conversation_id,
                                role='assistant',
                                content=response,
                                content_type='json',
                                user_id=user_id
                            )
                        else:
                            save_chat_message(
                                conversation_id=conversation_id,
                                role='assistant',
                                content=str(response),
                                content_type='text',
                                user_id=user_id
                            )
                except Exception as e:
                    print(f"Failed to save env conversation: {str(e)}")
                # The response already contains smart-rendered content
                return jsonify({
                    'status': 'success',
                    'response': result.get('response'),
                    'chat_history': result.get('chat_history', hist),
                    'conversation_id': conversation_id,
                    'response_type': 'rich_content' if result.get('render_type', '') == 'smart' else 'text',
                    'used_custom_environment': True,
                    'environment': result.get('environment', 'custom')
                })
            elif result.get('status') == 'no_environment':
                # No environment assigned, fall back to standard execution
                logger.info('No environment found, using standard execution')
                print('No environment found, using standard execution')
            else:
                logger.info('Environment error.')
                print('Environment error.')

                # Error in environment execution
                error_msg = result.get('error', 'Unknown error')
                
                # Return error as rich content
                if smart_renderer:
                    error_response = smart_renderer.analyze_and_render(
                        f"Environment execution error: {error_msg}",
                        {"type": "error"}
                    )
                else:
                    error_response = f"Error: {error_msg}"
                
                return jsonify({
                    'status': 'error',
                    'response': error_response,
                    'chat_history': hist,
                    'response_type': 'rich_content' if isinstance(error_response, dict) else 'text'
                })

        # Normal process within main environment
        active_agent = get_agent_for_user(agent_id, user_id)

        add_breadcrumb(
                message="Running agent",
                category="general",
                level="info",
                data={"agent_id": agent_id}
                )
        
        if active_agent:
            print('Agent Name:', active_agent.AGENT_NAME)
            active_agent.initialize_chat_history(eval(hist))
            print('Running active agent...')
            response = active_agent.run(prompt, use_smart_render=True, user_id=user_id)
            chat_history = active_agents[int(agent_id)].get_chat_history()
        else:
            print('Agent Name:', active_agents[int(agent_id)].AGENT_NAME)
            active_agents[int(agent_id)].initialize_chat_history(eval(hist))
            print(86 * '-')
            print('Running regular agent...')
            # Run the agent - this will now return structured content if modified
            response = active_agents[int(agent_id)].run(prompt, use_smart_render=True, user_id=user_id)
            print('Finished running regular agent, updating chat history...')
            chat_history = active_agents[int(agent_id)].get_chat_history()
            print('Done.')

        logger.info('========== OUTPUT ==========')
        logger.info('Response:' + str(response))

        try:
            # =====================================================================
            # Save assistant response to local history - PRESERVE RICH CONTENT
            # =====================================================================
            if conversation_id:
                if isinstance(response, dict) and response.get('type') == 'rich_content':
                    # Save the FULL rich content structure as-is
                    save_chat_message(
                        conversation_id=conversation_id,
                        role='assistant',
                        content=response,  # Save entire structure, not flattened
                        content_type='rich_content',
                        user_id=user_id
                    )
                elif isinstance(response, dict):
                    # Other dict responses - save as JSON
                    save_chat_message(
                        conversation_id=conversation_id,
                        role='assistant',
                        content=response,
                        content_type='json',
                        user_id=user_id
                    )
                else:
                    # Plain text response
                    save_chat_message(
                        conversation_id=conversation_id,
                        role='assistant',
                        content=str(response),
                        content_type='text',
                        user_id=user_id
                    )
            # =====================================================================
        except Exception as e:
            print(f"Error saving conversation response to history: {str(e)}")

        # Check if response is already structured (from modified GeneralAgent)
        if isinstance(response, dict) and response.get('type') == 'rich_content':
            # Return the rich content structure
            result = {
                "status": "success", 
                "response": response,  # This is now the structured content
                "chat_history": chat_history,
                "conversation_id": conversation_id,
                "response_type": "rich_content"  # Flag for frontend
            }
        else:
            # Legacy: plain text response (if GeneralAgent not modified yet)
            # Or optionally analyze it here for rich content
            if smart_renderer:  # If you've initialized the SmartContentRenderer
                add_breadcrumb(
                message="Analyze and render",
                category="general",
                level="info",
                data={"agent_id": agent_id}
                )

                structured_response = smart_renderer.analyze_and_render(response, {
                    "agent_id": agent_id,
                    "agent_name": active_agents[int(agent_id)].AGENT_NAME,
                    "query": prompt
                })
                result = {
                    "status": "success",
                    "response": structured_response,
                    "chat_history": chat_history,
                    "conversation_id": conversation_id,
                    "response_type": "rich_content"
                }
            else:
                # Fallback to original text response
                result = {
                    "status": "success", 
                    "response": response, 
                    "chat_history": chat_history,
                    "conversation_id": conversation_id,
                    "response_type": "text"
                }
        timer.stop()
        try:
            track_agent_executed(agent_id, True, timer.elapsed_ms, estimate_token_count(prompt), estimate_token_count(response))
        except:
            logger.warning('Failed to track agent executed.')
    except Exception as e:
        print(str(e))
        logger.error(str(e))
        
        # Return error as rich content if possible
        if smart_renderer:
            error_response = {
                "type": "rich_content",
                "blocks": [{
                    "type": "error",
                    "content": str(e),
                    "metadata": {"recoverable": True}
                }]
            }
            result = {
                "status": "error", 
                "response": error_response, 
                "chat_history": [],
                "conversation_id": None,
                "response_type": "rich_content"
            }
        else:
            result = {"status": "error", "response": str(e), "chat_history": []}

        try:
            capture_exception(e, {'agent_id': agent_id})  # telemetry tracking
        except:
            pass

    return jsonify(result)


@app.route('/api/conversations/create', methods=['POST'])
@cross_origin()
def create_conversation_endpoint():
    '''
    Create a new conversation for a chat session.
    Called by frontend when user selects an agent to start chatting.
    
    Request body:
        agent_id: Required - ID of the agent
        
    Returns:
        conversation_id: The new conversation ID
    '''
    try:
        data = request.get_json() or {}
        agent_id = data.get('agent_id')
        
        if not agent_id:
            return jsonify({'status': 'error', 'message': 'agent_id required'}), 400
        
        try:
            if current_user.is_authenticated:
                user_id = current_user.id
            else:
                return jsonify({'status': 'error', 'message': 'Authentication required'}), 401
        except:
            return jsonify({'status': 'error', 'message': 'Authentication required'}), 401
        
        # Always create a new conversation when this endpoint is called
        conversation_id = create_new_conversation(
            agent_id=int(agent_id),
            user_id=user_id
        )
        
        if conversation_id is None:
            return jsonify({'status': 'error', 'message': 'History is disabled'}), 400
        
        return jsonify({
            'status': 'success',
            'conversation_id': conversation_id
        })
        
    except Exception as e:
        logger.error(f"Error creating conversation: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    

@app.route('/chat/general/text', methods=['POST'])
@cross_origin()
def chat_general_text():
    """
    Handle general agent chat with plain text response (backward compatibility)
    """
    try:
        data = request.get_json()
        agent_id = data.get('agent_id')
        prompt = data.get('prompt')
        
        from GeneralAgent import GeneralAgent
        agent = GeneralAgent(agent_id)
        
        # Use the text-only method if you added it, or the original run method
        if hasattr(agent, 'run_text_only'):
            response = agent.run_text_only(prompt)
        else:
            # If running old version without modifications
            result = agent.agent_executor.invoke({"input": prompt, "chat_history": agent.chat_history})
            response = result.get("output", str(result))
        
        return jsonify({"response": response, "type": "text"})
        
    except Exception as e:
        capture_exception(e, {'agent_id': agent_id or None})
        return jsonify({"response": str(e), "type": "error"})


@app.route("/chat/email", methods=['GET'])
@cross_origin()
def chat_email():
    """ Receives string formatted as normal OpenAI message history.
    INPUT:
        messages = [{"role": "system", "content": system}]
        messages.append({"role": "user", "content": prompt})
    OUTPUT:
        messages.append({"role": "assistant", "content": response})
    RETURNS THE COMPLETE HIST
    """
    # API Call
    try:
        logger.info('Received API request at /chat/email...')

        prompt = request.args.get('prompt')
        hist = request.args.get('hist')
        email_from = request.args.get('email_from')

        logger.info('========== INPUT ==========')
        logger.info('Prompt (input):' + str(prompt))
        logger.info('Chat History (input):' + str(hist))
        logger.info('From Email (input):' + str(email_from))

        # Check user permissions
        available_agents = fetch_user_agents_by_email(email_from)

        if len(available_agents) > 0:
            # Clean the HTML email string
            if hist is None or hist == '':
                hist = '[]'
            else:
                hist = remove_html_tags(hist)

            prompt = clean_email_input_text(prompt)

            logger.info('Prompt (converted):' + str(prompt))
            #logger.info('Chat History (converted):' + str(hist))

            monitoring_agent.initialize_chat_history(eval(hist))

            response = monitoring_agent.run(prompt)

            chat_history = monitoring_agent.get_chat_history()

            logger.info('========== OUTPUT ==========')
            logger.info('Response:' + str(response))
            #logger.info('Chat History (updated):' + str(chat_history))
        else:
            logger.info('========== UNAUTHORIZED OUTPUT ==========')
            unauth_system = SYS_PROMPT_UNAUTH_EMAIL_SYSTEM
            unauth_prompt = SYS_PROMPT_UNAUTH_EMAIL_PROMPT.replace('{prompt}', prompt)         
            response = azureQuickPrompt(prompt=unauth_prompt, system=unauth_system, use_alternate_api=True)
            chat_history = []

        result = {"status": "success", "response": response, "chat_history": chat_history}
    except Exception as e:
        print(str(e))
        logger.error(str(e))
        result = {"status": "error", "response": str(e), "chat_history": []}

    return jsonify(result)


@app.route("/add/table", methods = ['GET', 'POST'])
@cross_origin()
@developer_required(api=True)
def add_table():
    #job_id, job_desc, ai_system, ai_prompt, enabled, fn_type, fn_text, fn_pass_type, fn_pass_text, fn_fail_type, fn_fail_text, fn_finish_type, fn_finish_text, collection_id
    try:
        logger.info('Received call to /add/table...')
        data = request.get_json()

        table_id = data['table_id']
        table_name = data['table_name']
        table_desc = data['table_desc']
        connection_id = data['connection_id']

        logger.debug("=================================")
        logger.debug("Input table_id:" + str(table_id))
        logger.debug("Input table_name:" + str(table_name))
        logger.debug("Input table_desc:" + str(table_desc))
        logger.debug("Input connection_id:" + str(connection_id))
        logger.debug("=================================")

        result = Add_Table(table_id, table_name, table_desc, connection_id)

        if result:
            response = {"status": "success", "response": "Table added successfully"}
        else:
            response = {"status": "error", "response": "Failed to add table"}
    except Exception as e:
        print(str(e))
        response = {"status": "error", "response": str(e)}
        logger.error(str(e))

    return jsonify(response)


@app.route("/delete/table", methods = ['DELETE'])
@cross_origin()
@developer_required(api=True)
def delete_table():
    try:
        logger.info('Received call to /delete/table...')
        data = request.get_json()

        table_id = data['table_id']

        logger.debug("=================================")
        logger.debug("Input table_id:" + str(table_id))
        logger.debug("=================================")

        result = Delete_Table(table_id)

        if result:
            response = {"status": "success", "response": "Table deleted successfully"}
        else:
            response = {"status": "error", "response": "Failed to delete table"}
    except Exception as e:
        print(str(e))
        response = {"status": "error", "response": str(e)}
        logger.error(str(e))

    return jsonify(response)


@app.route("/get/tables/<int:connection_id>", methods = ['GET'])
@cross_origin()
@developer_required(api=True)
def get_table(connection_id=None):
    try:
        if connection_id is None:
            data = request.get_json()
            connection_id = data['connection_id']
        df = Get_Tables(connection_id)
        response = dataframe_to_json(df)
    except Exception as e:
        print(str(e))
        response = {"status": "error", "response": str(e)}

    return jsonify(response)

# TODO: Finish this function to exec db action...
@app.route("/execute/query/<int:connection_id>/<string:query>", methods = ['GET'])
@cross_origin()
@developer_required(api=True)
def execute_query(connection_id=None, query=None):
    try:
        if connection_id is None:
            data = request.get_json()
            connection_id = data['connection_id']

        if query is None:
            data = request.get_json()
            query = data['query']

        # Get connection info
        conn_str, connection_id, database_type = get_database_connection_string(connection_id)

        # Execute query
        result = execute_sql_no_results(conn_str, query)

        response = {"status": "success", "response": "Query was executed successfully"}
    except Exception as e:
        print(str(e))
        response = {"status": "error", "response": str(e)}

    return jsonify(response)


@app.route("/execute/query_result/<int:connection_id>/<string:query>", methods = ['GET'])
@cross_origin()
@developer_required(api=True)
def execute_query_result(connection_id=None, query=None):
    try:
        if connection_id is None:
            data = request.get_json()
            connection_id = data['connection_id']

        if query is None:
            data = request.get_json()
            query = data['query']

        # Get connection info
        conn_str, connection_id, database_type = get_database_connection_string(connection_id)

        # Execute query
        result = execute_sql_for_llm(conn_str, query)

        response = {"status": "success", "response": result}
    except Exception as e:
        print(str(e))
        response = {"status": "error", "response": str(e)}

    return jsonify(response)


# ─── Internal API Endpoints ──────────────────────────────────────────────────
# Used by builder_data and other microservices for service-to-service communication.
# These endpoints use internal API key auth (machine-bound PBKDF2 key) instead of
# session-based auth, so microservices can call them without a browser login.
# ─────────────────────────────────────────────────────────────────────────────


@app.route("/api/internal/connections", methods=['GET'])
@cross_origin()
@internal_api_key_required()
def internal_list_connections():
    """
    List all database connections (for microservice consumption).
    Returns sanitized connection list — no passwords or connection strings.
    """
    try:
        df = Get_Connection()

        if df is None or df.empty:
            return jsonify({"status": "success", "connections": []})

        connections = []
        for _, row in df.iterrows():
            connections.append({
                "id": int(row.get("id", 0)),
                "connection_name": row.get("connection_name", ""),
                "database_type": row.get("database_type", ""),
                "server": row.get("server", ""),
                "database_name": row.get("database_name", ""),
                "is_active": True,
                "source_type": "database",
            })

        return jsonify({"status": "success", "connections": connections})
    except Exception as e:
        logger.error(f"[internal_list_connections] Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/internal/connection-string/<int:connection_id>", methods=['GET'])
@cross_origin()
@internal_api_key_required()
def internal_get_connection_string(connection_id):
    """
    Get the ODBC connection string for a database connection.
    Used by builder_data to execute queries via pyodbc.

    Returns: { connection_string, connection_id, database_type }
    """
    try:
        conn_str, conn_id, db_type = get_database_connection_string(connection_id)

        if conn_str is None:
            return jsonify({
                "status": "error",
                "message": f"Connection {connection_id} not found"
            }), 404

        # Resolve any local secret references in the connection string
        from connection_secrets import resolve_connection_string_secrets
        conn_str = resolve_connection_string_secrets(conn_str)

        return jsonify({
            "status": "success",
            "connection_string": conn_str,
            "connection_id": conn_id,
            "database_type": db_type or "sql_server",
        })
    except Exception as e:
        logger.error(f"[internal_get_connection_string] Error for ID {connection_id}: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/internal/connection-tables/<int:connection_id>", methods=['GET'])
@cross_origin()
@internal_api_key_required()
def internal_get_tables(connection_id):
    """
    Get the list of tables for a database connection.
    """
    try:
        df = Get_Tables(connection_id)

        if df is None or df.empty:
            return jsonify({"status": "success", "tables": []})

        tables = []
        for _, row in df.iterrows():
            tables.append({
                "table_name": row.get("table_name", row.get("TABLE_NAME", "")),
                "schema": row.get("schema", row.get("TABLE_SCHEMA", "dbo")),
            })

        return jsonify({"status": "success", "tables": tables})
    except Exception as e:
        logger.error(f"[internal_get_tables] Error for connection {connection_id}: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/internal/connection-schema/<int:connection_id>", methods=['GET'])
@cross_origin()
@internal_api_key_required()
def internal_get_schema(connection_id):
    """
    Get table and column metadata for a database connection.
    Returns a YAML-like text summary of all tables and their columns.
    """
    try:
        conn_str, conn_id, db_type = get_database_connection_string(connection_id)
        if conn_str is None:
            return jsonify({"status": "error", "message": f"Connection {connection_id} not found"}), 404

        from connection_secrets import resolve_connection_string_secrets
        conn_str = resolve_connection_string_secrets(conn_str)

        import pyodbc
        conn = pyodbc.connect(conn_str, timeout=15)
        cursor = conn.cursor()

        # Get tables
        tables = []
        for row in cursor.tables(tableType='TABLE'):
            tables.append({"schema": row.table_schem, "name": row.table_name})

        # Build schema summary
        schema_lines = []
        for table in tables[:50]:  # Limit to 50 tables
            schema_lines.append(f"{table['schema']}.{table['name']}:")
            try:
                for col in cursor.columns(table=table['name'], schema=table['schema']):
                    schema_lines.append(f"  - {col.column_name}: {col.type_name}")
            except Exception:
                schema_lines.append("  - (columns unavailable)")

        cursor.close()
        conn.close()

        return jsonify({
            "status": "success",
            "schema_yaml": "\n".join(schema_lines),
            "table_count": len(tables),
        })
    except Exception as e:
        logger.error(f"[internal_get_schema] Error for connection {connection_id}: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/internal/integrations", methods=['GET'])
@cross_origin()
@internal_api_key_required()
def internal_list_integrations():
    """
    List all user integrations (for microservice consumption).
    Returns integration metadata including available operations.
    """
    try:
        from integration_manager import IntegrationManager
        manager = IntegrationManager()
        integrations = manager.list_integrations()

        result = []
        for intg in integrations:
            result.append({
                "integration_id": intg.get("integration_id"),
                "integration_name": intg.get("integration_name", ""),
                "description": intg.get("description", ""),
                "platform_name": intg.get("platform_name", ""),
                "platform_category": intg.get("platform_category", ""),
                "template_key": intg.get("template_key", ""),
                "auth_type": intg.get("auth_type", ""),
                "is_connected": intg.get("is_connected", False),
                "source_type": "integration",
            })

        return jsonify({"status": "success", "integrations": result})
    except Exception as e:
        logger.error(f"[internal_list_integrations] Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/internal/integrations/<int:integration_id>/operations", methods=['GET'])
@cross_origin()
@internal_api_key_required()
def internal_get_integration_operations(integration_id):
    """
    Get available operations for an integration.
    """
    try:
        from integration_manager import IntegrationManager
        manager = IntegrationManager()
        operations = manager.get_operations(integration_id)
        return jsonify({"status": "success", "operations": operations})
    except Exception as e:
        logger.error(f"[internal_get_integration_operations] Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/internal/integrations/<int:integration_id>/execute", methods=['POST'])
@cross_origin()
@internal_api_key_required()
def internal_execute_integration(integration_id):
    """
    Execute an operation on an integration (for microservice consumption).
    Body: { "operation": "get_invoices", "parameters": { ... } }
    """
    try:
        from integration_manager import IntegrationManager
        data = request.get_json() or {}
        operation_key = data.get("operation", "")
        parameters = data.get("parameters", {})
        context = data.get("context", {})

        if not operation_key:
            return jsonify({"status": "error", "message": "operation is required"}), 400

        manager = IntegrationManager()
        result = manager.execute_operation(
            integration_id=integration_id,
            operation_key=operation_key,
            parameters=parameters,
            context=context,
        )

        return jsonify({
            "status": "success" if result.get("success") else "error",
            **result,
        })
    except Exception as e:
        logger.error(f"[internal_execute_integration] Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/internal/document-search", methods=['POST'])
@cross_origin()
@internal_api_key_required()
def internal_document_search():
    """
    AI-driven document search for microservice consumption (Command Center).
    Body: { "question": "user's natural language question" }
    """
    try:
        data = request.get_json() or {}
        question = data.get("question", "").strip()

        if not question:
            return jsonify({"status": "error", "message": "question is required"}), 400

        from DocUtils import document_search_super_enhanced_debug
        conn_str = get_db_connection_string()
        result_json = document_search_super_enhanced_debug(
            conn_str,
            user_question=question,
            max_results=cfg.DOC_SEARCH_LIMIT,
            check_completeness=cfg.DOC_CHECK_COMPLETENESS,
        )

        # result_json is a JSON string — parse it so the response is proper JSON
        import json as _json
        try:
            parsed = _json.loads(result_json)
        except (TypeError, _json.JSONDecodeError):
            parsed = result_json

        return jsonify({"status": "success", "results": parsed})

    except Exception as e:
        logger.error(f"[internal_document_search] Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ─── End Internal API Endpoints ──────────────────────────────────────────────


@app.route("/add/column", methods = ['GET', 'POST'])
@cross_origin()
@developer_required(api=True)
def add_column():
    #job_id, job_desc, ai_system, ai_prompt, enabled, fn_type, fn_text, fn_pass_type, fn_pass_text, fn_fail_type, fn_fail_text, fn_finish_type, fn_finish_text, collection_id
    try:
        logger.info('Received call to /add/column...')

        # Get JSON data from the POST request
        data = request.get_json()

        table_id = data['table_id']
        column_id = data['column_id']
        column_name = data['column_name']
        column_description = data['column_description']
        column_values = data['column_values']

        logger.debug("=================================")
        logger.debug("Input table_id:" + str(table_id))
        logger.debug("Input column_id:" + str(column_id))
        logger.debug("Input column_name:" + str(column_name))
        logger.debug("Input column_description:" + str(column_description))
        logger.debug("Input column_values:" + str(column_values))
        logger.debug("=================================")

        result = Add_Column(table_id, column_id, column_name, column_description, column_values)

        if result:
            response = {"status": "success", "response": "Column added successfully"}
        else:
            response = {"status": "error", "response": "Failed to add column"}
    except Exception as e:
        print(str(e))
        response = {"status": "error", "response": str(e)}
        logger.error(str(e))

    return jsonify(response)


@app.route("/get/columns", methods = ['GET'])
@cross_origin()
@login_required
def get_column():
    try:
        logger.info('Received call to /get/column...')

        try:
            table_id = request.args.get('table_id')
        except:
            # Get JSON data from the POST request
            data = request.get_json()
            table_id = data['table_id']

        logger.debug("=================================")
        logger.debug("Input table_id:" + str(table_id))
        logger.debug("=================================")
        
        df = Get_Columns(table_id)

        response = dataframe_to_json(df)
    except Exception as e:
        print(str(e))
        response = {"status": "error", "response": str(e)}
        logger.error(str(e))

    return jsonify(response)


@app.route("/delete/column", methods = ['DELETE'])
@cross_origin()
@developer_required(api=True)
def delete_column():
    try:
        logger.info('Received call to /delete/column...')
        data = request.get_json()

        column_id = data['id']

        logger.debug("=================================")
        logger.debug("Input column_id:" + str(column_id))
        logger.debug("=================================")

        result = Delete_Column(column_id)

        if result:
            response = {"status": "success", "response": "Column deleted successfully"}
        else:
            response = {"status": "error", "response": "Failed to delete column"}
    except Exception as e:
        print(str(e))
        response = {"status": "error", "response": str(e)}
        logger.error(str(e))

    return jsonify(response)


@app.route("/delete/table_columns", methods = ['DELETE'])
@cross_origin()
@developer_required(api=True)
def delete_table_columns():
    try:
        logger.info('Received call to /delete/table_columns...')
        data = request.get_json()

        table_id = data['table_id']

        logger.debug("=================================")
        logger.debug("Input table_id:" + str(table_id))
        logger.debug("=================================")

        result = Delete_Table_Columns(table_id)

        if result:
            response = {"status": "success", "response": "Columns deleted successfully"}
        else:
            response = {"status": "error", "response": "Failed to delete columns"}
    except Exception as e:
        print(str(e))
        response = {"status": "error", "response": str(e)}
        logger.error(str(e))

    return jsonify(response)


@app.route('/get_user_agents/<int:user_id>', methods=['GET', 'POST'])
@login_required
def get_user_agents(user_id=None):
    try:
        if user_id is None:
            user_id = request.json['user_id']
        agents = fetch_user_agents(user_id, current_user.role)
        print('AGENTS -=-==--=--=>>>', agents)
        if agents is None:
            return jsonify([])
    except Exception as e:
        print(str(e))
        return jsonify([])
    return jsonify(agents)


@app.route("/test")
def chat_test():
    # API Call
    prompt = 'Hello.'
    completion = azureQuickPrompt(prompt)

    # Extract response
    #response = str(completion["choices"][0]["message"]["content"])
    response = completion

    return response

@app.route('/chat/data/explain', methods = ['GET'])
@cross_origin()
def chat_data_explain():
    try:
        logger.info("Received request at /chat/data/explain...")
        print("Received request at /chat/data/explain...")
        response = process_chat_data_explain_request()
    except Exception as e:
        print(str(e))
        response = {
            "explanation": str(e),
        }
    return jsonify(response)

@app.route('/chat/data', methods = ['GET', 'POST'])
@cross_origin()
def chat():
    try:
        logger.info("Received request at /chat/data...")
        timer = Timer()
        timer.start()
        try:
            # Get JSON data from the POST request
            data = request.get_json()
            agent_id = data['agent_id']
            question = data['question']
            conversation_history = data['history']
            format_table_as_json_string = data['format_table_as_json']
        except:
            # Params
            agent_id = request.args.get('agent_id')
            question = request.args.get('question')
            conversation_history = request.args.get('history')  # Retrieve conversation history from request
            format_table_as_json_string = request.args.get('format_table_as_json')

        conversation_history = str(conversation_history)

        logger.debug(agent_id)
        logger.debug(question)
        logger.debug(str(conversation_history))
        logger.debug(str(format_table_as_json_string))

        if format_table_as_json_string == "True":
            format_table_as_json = True
        else:
            format_table_as_json = False

        # Get deserialized LLMDataEngine
        deserialized_llm_data_engine = get_session_llm_data_engine(session['session_id'])

        if deserialized_llm_data_engine is None:
            logger.warning(f"Session {session['session_id']} may have expired, informing user to start a new chat...")
            response = {
            "answer": "Your session may have expired. Please refresh or start a new chat.",
            "answer_type": "string",
            "explanation": "Session expired.",
            "clarification_questions": "",
            "special_message": "ERROR",
            "original_prompt": question,
            "revised_prompt": "",
            "conversation_history": conversation_history,
            }
            return jsonify(response)

        print('CALLING PROCESS CHAT REQUEST...')
        response, deserialized_llm_data_engine = process_chat_data_request(deserialized_llm_data_engine, agent_id, question, conversation_history, format_table_as_json)

        # Update the serialized engine in the session
        print('UPDATING DATA ENGINE IN SESSION PARAM...')
        print(type(deserialized_llm_data_engine))
        update_session_llm_data_engine(session['session_id'], deserialized_llm_data_engine)

        timer.stop()
        try:
            track_agent_executed(agent_id, True, timer.elapsed_ms, estimate_token_count(question), estimate_token_count(response))
        except:
            logger.warning('Failed to track data agent executed.')
    except Exception as e:
        print(str(e))
        logger.error("Error:" + str(e))
        response = {
            "answer": "ERROR",
            "answer_type": "string",
            "explanation": str(e),
            "clarification_questions": "",
            "special_message": "ERROR",
            "original_prompt": question,
            "revised_prompt": "",
            "conversation_history": conversation_history,
        }

    return jsonify(response)

#####################
# END DATA ROUTES
#####################

#--------------------

#####################
# LLM TEST ROUTES
#####################


# Global variables to hold test state
test_results = []
test_summary = {}
test_running = False
test_stop_signal = False
test_lock = threading.Lock()
# Global variables to hold test results
test_results = []


@app.route('/export_results', methods=['GET'])
def export_results():
    return jsonify(test_results)


def run_test_thread(num_questions,agent_id):
    global test_results, test_summary, test_running, test_stop_signal
    logger.debug("Starting test thread for %d questions", num_questions)
    #agent_id = 14  # Example agent ID
    conn_str, connection_id, database_type = get_agent_connection_info(agent_id)
    schema = get_column_descriptions(connection_id)
    data_sample = sample_data_from_db(conn_str, schema, database_type)
    if data_sample:
        questions = get_chatgpt_questions(schema, data_sample, num_questions=num_questions)
        
        engine = LLMDataEngine(provider=cfg.NLQ_PROVIDER)
        enhanced_query_engine, enhanced_analytical_engine = enhance_engines(engine, nlq_systems)
        engine.query_engine = enhanced_query_engine
        engine.analytical_engine = enhanced_analytical_engine

        for result in run_tests(engine, agent_id, questions):
            with test_lock:
                if test_stop_signal:
                    logger.info("Test stopped by user")
                    test_results.append({"error": "Test stopped by user"})
                    break
                test_results.append(result)
        if not test_stop_signal:
            test_summary = summarize_results(test_results)
            save_results(test_results)
    else:
        logger.error("Failed to sample data from the database.")
        with test_lock:
            test_results.append({"error": "Failed to sample data from the database."})
    with test_lock:
        test_running = False
        test_stop_signal = False


@app.route('/llm_unit_test', methods=['GET', 'POST'])
@cross_origin()
@developer_required()
def llm_unit_test():
    return render_template('llm_unit_test.html')


@app.route('/workflow_tool', methods=['GET', 'POST'])
@developer_required()
@cross_origin()
@tier_allows_feature('workflows')
def workflow_tool():
    return render_template('workflow_tool.html')


@app.route('/start_test', methods=['POST'])
def start_test():
    global test_running, test_results, test_summary
    num_questions = int(request.form['num_questions'])
    agent_id = int(request.form['agent_id'])
    logger.debug("Received start test request with %d questions", num_questions)
    with test_lock:
        if test_running:
            logger.warning("Test already running")
            return jsonify({"status": "Test already running"}), 409
        test_running = True
        test_results = []
        test_summary = {}
    threading.Thread(target=run_test_thread, args=(num_questions,agent_id,)).start()
    return jsonify({"status": "Test started"})

@app.route('/stop_test', methods=['POST'])
def stop_test():
    global test_stop_signal
    with test_lock:
        test_stop_signal = True
    return jsonify({"status": "Test stopped"})

@app.route('/get_results', methods=['GET'])
def get_results():
    global test_results, test_summary, test_running
    with test_lock:
        return jsonify({"results": test_results, "summary": test_summary, "running": test_running})

@app.errorhandler(Exception)
def handle_exception(e):
    logger.error("An error occurred: %s", e)
    return str(e), 500



#####################
# END LLM TEST ROUTES
#####################

#--------------------

#####################
# WORKFLOW ROUTES
#####################
@app.route("/api/workflows/list")
@cross_origin()
@api_key_or_session_required(min_role=2)
def list_workflows_summary():
    """Lightweight workflow listing — returns only id, name, and category."""
    try:
        df = get_workflows()
        if df is None or df.empty:
            return jsonify({"workflows": []})
        # Return only the fields needed for display and selection
        cols = []
        if "id" in df.columns:
            cols.append("id")
        if "workflow_name" in df.columns:
            cols.append("workflow_name")
        if "category" in df.columns:
            cols.append("category")
        if "enabled" in df.columns:
            cols.append("enabled")
        summary = df[cols].to_dict(orient="records") if cols else []
        return jsonify({"workflows": summary})
    except Exception as e:
        logger.error("Error listing workflows: %s", e)
        return jsonify({"workflows": [], "error": str(e)}), 500


@app.route("/get/workflows")
@cross_origin()
@api_key_or_session_required(min_role=2)
def get_workflow():
    df = get_workflows()
    json_df = dataframe_to_json(df)
    return jsonify(json_df)


@app.route("/delete/workflow/<int:workflow_id>", methods=['DELETE'])
@cross_origin()
@api_key_or_session_required()
def del_workflow(workflow_id):
    result = delete_workflow(workflow_id)
    if result:
        return jsonify({"status": "success"})
    else:
        return jsonify({"error": "Error deleting workflow"}), 500


@app.route("/notification/email/<string:email_to>/<string:subject>/<string:message>", methods=['GET', 'POST'])
@app.route("/notification/email/<string:email_to>/<string:subject>", defaults={'message': None}, methods=['GET', 'POST'])
@cross_origin()
def email_notification(email_to, subject, message=None):
    print('Sending email notification...', email_to)
    
    # Priority order for message:
    # 1. JSON payload (for POST requests or GET with body)
    # 2. Query parameter
    # 3. Path parameter
    # 4. Empty string as fallback
    
    final_message = message or ''  # Start with path parameter or empty
    
    # Check for query parameter
    if request.args.get('message'):
        final_message = request.args.get('message')
    
    # Check for JSON payload (highest priority)
    if request.is_json:
        data = request.get_json()
        if data and 'message' in data:
            final_message = data['message']
    
    result = send_email_notification(email_to, subject, final_message)
    print('Result:', result)
    
    if result:
        return jsonify({"status": "success"})
    else:
        return jsonify({"status": "failure"})


@app.route("/get/workflow/<int:workflow_id>")
@cross_origin()
@api_key_or_session_required(min_role=2)
def get_workflow_by_id(workflow_id):
    try:
        # Get workflow data from database
        df = get_workflows(workflow_id)
        
        if df.empty:
            return jsonify({"error": "Workflow not found"}), 404
        
        df_vars = get_workflow_variables(workflow_id=workflow_id)
        
        # Convert DataFrame to dictionary
        workflow_row = df.iloc[0].to_dict()
        
        # Ensure workflow_data is properly formatted
        if 'workflow_data' in workflow_row:
            # If workflow_data is stored as a string, parse it
            if isinstance(workflow_row['workflow_data'], str):
                try:
                    import json
                    workflow_data = json.loads(workflow_row['workflow_data'])
                except json.JSONDecodeError:
                    return jsonify({"error": "Invalid workflow data format"}), 500
            else:
                workflow_data = workflow_row['workflow_data']
                
            # Ensure the workflow data has the required structure
            if not isinstance(workflow_data, dict) or 'nodes' not in workflow_data:
                return jsonify({"error": "Invalid workflow structure"}), 500
                
            return jsonify(workflow_data)
        else:
            return jsonify({"error": "No workflow data found"}), 404
            
    except Exception as e:
        print(f"Error loading workflow {workflow_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500
    

@app.route("/get/workflow/categories")
@cross_origin()
@developer_required(api=True)
def get_workflow_categories():
    df = get_workflowCategories()
    json_df = dataframe_to_json(df)
    return jsonify(json_df)


@app.route("/update/workflows/<int:workflow_id>/category", methods=['PUT'])
@cross_origin()
@developer_required(api=True)
def update_workflow_category(workflow_id):
    try:
        print(f'Updating workflow {workflow_id} category')
        # Initialize database connection
        conn = get_db_connection()
        cursor = conn.cursor()

        # Get the category ID from the request body
        data = request.get_json()
        category_id = data.get('categoryId')
        
        # If category_id is empty string, set it to None (Uncategorized)
        if category_id == "":
            category_id = None

        # Get current timestamp for the update
        #current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # First, get the tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        print('Workflow ID', workflow_id)
        print('New Category ID', category_id)
        # Update the workflow's category in the database
        query = """
            UPDATE Workflows 
            SET category_id = ?,
                last_modified = getutcdate()
            WHERE id = ?
        """

        # Execute and update the workflow
        result = cursor.execute(query, (category_id, workflow_id))
        print('Category Update Result:', result)
        
        # Commit all changes
        conn.commit()
        
        # Check if any row was updated
        if not result:
            return jsonify({
                "error": f"Workflow with ID {workflow_id} not found"
            }), 404

        return jsonify({
            "message": "Category updated successfully",
            "workflow_id": workflow_id,
            "category_id": category_id
        })

    except Exception as e:
        # Log the error for debugging
        print(f"Error updating workflow {workflow_id} category: {str(e)}")
        return jsonify({
            "error": "Failed to update workflow category",
            "details": str(e)
        }), 500


def save_workflow_to_database(workflow_name, workflow_data):
    """Helper function to save workflow and its variables to SQL Server"""
    try:
        workflow_id = None
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Convert workflow data to JSON string
        workflow_json = json.dumps(workflow_data)
        
        # First, get the tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # SQL to insert/update workflow
        workflow_sql = """
        MERGE INTO Workflows AS target
        USING (VALUES (?, ?, getutcdate(), ?)) AS source (workflow_name, workflow_data, last_modified, version)
        ON target.workflow_name = source.workflow_name
        WHEN MATCHED THEN
            UPDATE SET 
                workflow_data = source.workflow_data,
                last_modified = source.last_modified,
                version = target.version + 1
        WHEN NOT MATCHED THEN
            INSERT (workflow_name, workflow_data, last_modified, version)
            VALUES (source.workflow_name, source.workflow_data, source.last_modified, source.version);
        """
        print('Updating workflow in database', workflow_name)

        # Execute and update the workflow
        cursor.execute(workflow_sql, (workflow_name, workflow_json, 1))

        workflow_sql = "SELECT id workflow_id FROM Workflows WHERE workflow_name = ?;"

        # Execute and get the workflow_id
        cursor.execute(workflow_sql, (workflow_name))
        
        # Get the workflow_id from the query result
        row = cursor.fetchone()
        if row:
            workflow_id = row[0]
        print('Workflow updated id', workflow_id)

        # If we have workflow variables and a valid workflow_id, save them
        if workflow_id and 'variables' in workflow_data and workflow_data['variables']:
            # First delete existing variables for this workflow
            delete_vars_sql = "DELETE FROM Workflow_Variables WHERE workflow_id = ?"
            cursor.execute(delete_vars_sql, (workflow_id,))
            
            # Insert each variable
            variables = workflow_data['variables']
            for var_name, var_info in variables.items():
                var_type = var_info.get('type', 'string')
                default_value = var_info.get('defaultValue', '')
                description = var_info.get('description', '')
                
                # Convert complex default values to strings if needed
                if not isinstance(default_value, str):
                    try:
                        default_value = json.dumps(default_value)
                    except:
                        default_value = str(default_value)
                
                # Insert the variable
                var_sql = """
                INSERT INTO Workflow_Variables 
                (workflow_id, variable_name, variable_type, default_value, description, created_date, last_modified)
                VALUES (?, ?, ?, ?, ?, getutcdate(), getutcdate())
                """
                cursor.execute(var_sql, (workflow_id, var_name, var_type, default_value, description))
        
        # Commit all changes
        conn.commit()
        
        return workflow_id
            
    except Exception as e:
        logger.error(f"Database error saving workflow: {str(e)}")
        # Try to rollback if there was an error
        try:
            capture_exception(e)  # telemetry
            conn.rollback()
        except:
            pass
        raise

def save_to_file(filename, workflow_data):
    """Helper function to save workflow to file"""
    try:
        # Create workflows directory if it doesn't exist
        workflows_dir = os.path.join(os.path.dirname(__file__), 'workflows')
        os.makedirs(workflows_dir, exist_ok=True)
        
        # Full path for the workflow file
        file_path = os.path.join(workflows_dir, filename)
        
        # Save to file with pretty printing
        with open(file_path, 'w') as f:
            json.dump(workflow_data, f, indent=2)
            
        return True
        
    except Exception as e:
        logger.error(f"File save error: {str(e)}")
        raise

@app.route("/save/workflow", methods=['POST'])
@cross_origin()
@api_key_or_session_required(min_role=2)
def save_workflow():
    """
    Save workflow to both file system and database
    
    Expected JSON payload:
    {
        "filename": "workflow_name.json",
        "workflow": {
            "nodes": [...],
            "connections": [...]
        }
    }
    
    Returns:
    {
        "status": "success/error",
        "message": "Success/error message",
        "file_path": "Path where file was saved",
        "database_version": "Version number in database"
    }
    """
    try:
        logger.info("Received workflow save request")
        
        # Get request data
        data = request.get_json()
        if not data:
            raise ValueError("No data provided")
            
        filename = data.get('filename')
        workflow_data = data.get('workflow')
        
        if not filename or not workflow_data:
            raise ValueError("Missing required fields (filename or workflow)")
            
        # Validate workflow data structure
        if not isinstance(workflow_data, dict) or \
           'nodes' not in workflow_data or \
           'connections' not in workflow_data:
            raise ValueError("Invalid workflow data structure")

        # Check if workflow is new
        workflows_dir = os.path.join(os.path.dirname(__file__), 'workflows')
        file_path = os.path.join(workflows_dir, filename)
        is_new = False
        if os.path.exists(file_path):
            is_new = True
            
        # Save to file
        logger.info(f"Saving workflow to file: {filename}")
        save_to_file(filename, workflow_data)
        
        # Save to database
        workflow_name = os.path.splitext(filename)[0]  # Remove .json extension
        logger.info(f"Saving workflow to database: {workflow_name}")
        workflow_id = None
        workflow_id = save_workflow_to_database(workflow_name, workflow_data)
        
        response = {
            "status": "success",
            "message": "Workflow saved successfully",
            "workflow_id": workflow_id,
            "file_path": file_path,
            "database_version": workflow_id  # Return actual workflow ID for cross-step reference
        }
        
        logger.info("Workflow saved successfully")

        try:
            track_feature_usage('workflow', 'created' if is_new else 'updated')
        except:
            pass

        return jsonify(response), 200
        
    except ValueError as ve:
        error_message = str(ve)
        logger.error(f"Validation error: {error_message}")
        capture_exception(ve)  # telemetry
        return jsonify({
            "status": "error",
            "message": error_message
        }), 400
        
    except Exception as e:
        error_message = str(e)
        logger.error(f"Unexpected error: {error_message}")
        capture_exception(e)  # telemetry
        return jsonify({
            "status": "error",
            "message": f"An unexpected error occurred: {error_message}"
        }), 500

@app.route("/api/workflows/<int:workflow_id>/rename", methods=['PUT'])
@cross_origin()
@api_key_or_session_required(min_role=2)
def rename_workflow(workflow_id):
    """
    Rename a workflow by its ID
    
    Expected JSON payload:
    {
        "name": "New Workflow Name"
    }
    """
    try:
        logger.info(f'Renaming workflow {workflow_id}')
        
        # Get the new name from the request body
        data = request.get_json()
        new_name = data.get('name')
        
        if not new_name or not new_name.strip():
            return jsonify({
                "error": "Workflow name cannot be empty"
            }), 400
        
        new_name = new_name.strip()
        
        # Initialize database connection
        conn = get_db_connection()
        cursor = conn.cursor()

        # Set the tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # First, get the old workflow name for file rename
        cursor.execute("SELECT workflow_name FROM Workflows WHERE id = ?", (workflow_id,))
        row = cursor.fetchone()
        old_name = row[0] if row else None
        
        if not old_name:
            return jsonify({
                "error": f"Workflow with ID {workflow_id} not found"
            }), 404
        
        # Update the workflow's name in the database
        query = """
            UPDATE Workflows 
            SET workflow_name = ?,
                last_modified = GETUTCDATE()
            WHERE id = ?
        """

        # Execute the update
        cursor.execute(query, (new_name, workflow_id))
        
        # Commit the changes
        conn.commit()
        
        # Try to rename the file on disk (non-critical if it fails)
        try:
            workflows_dir = os.path.join(os.path.dirname(__file__), 'workflows')
            old_file_path = os.path.join(workflows_dir, old_name)
            new_file_path = os.path.join(workflows_dir, new_name)
            
            # Check various possible file extensions
            for ext in ['', '.json']:
                old_path = old_file_path + ext
                new_path = new_file_path + ext
                if os.path.exists(old_path) and not os.path.exists(new_path):
                    os.rename(old_path, new_path)
                    logger.info(f'Renamed workflow file from "{old_path}" to "{new_path}"')
                    break
        except Exception as file_error:
            # Log but don't fail - database is the source of truth
            logger.warning(f'Could not rename workflow file: {str(file_error)}')

        logger.info(f'Workflow {workflow_id} renamed from "{old_name}" to "{new_name}"')
        
        return jsonify({
            "status": "success",
            "message": "Workflow renamed successfully",
            "workflow_id": workflow_id,
            "name": new_name
        })

    except Exception as e:
        # Log the error for debugging
        logger.error(f"Error renaming workflow {workflow_id}: {str(e)}")
        capture_exception(e)  # telemetry
        return jsonify({
            "error": "Failed to rename workflow",
            "details": str(e)
        }), 500

#####################
# END WORKFLOW ROUTES
#####################

#--------------------

#--------------------

#####################
# DOCUMENT ROUTES
#####################
from LLMDocumentSearchEngine import LLMDocumentSearch

def process_document_job(job, execution_id):
    """
    Process a document job - this runs in a background thread
    
    Args:
        job: Dictionary with job details
        execution_id: ID of the execution record
    """
    import os
    import glob
    import time
    from datetime import datetime
    from LLMDocumentEngine import LLMDocumentProcessor
    
    # Get a new DB connection for this thread
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Statistics
    start_time = time.time()
    total_files = 0
    successful_files = 0
    failed_files = 0
    total_pages = 0
    
    try:
        # Get files to process
        file_pattern = os.path.join(job['InputDirectory'], job['FilePattern'])
        files = glob.glob(file_pattern, recursive=job['ProcessSubdirectories'])
        
        # Process each file
        for file_path in files:
            if not os.path.isfile(file_path):
                continue
                
            file_name = os.path.basename(file_path)
            total_files += 1
            
            # Insert file processing record
            cursor.execute("""
                INSERT INTO DocumentJobFileDetails (
                    ExecutionID, ProcessedAt, FileName, OriginalPath, Status
                ) VALUES (?, getutcdate(), ?, ?, 'PROCESSING')
            """, (execution_id, file_name, file_path))
            conn.commit()
            
            # Get the file detail ID
            cursor.execute("SELECT @@IDENTITY")
            file_detail_id = cursor.fetchone()[0]
            
            try:
                # This is where you would call your document processor
                # For demonstration, we'll just simulate processing
                file_start_time = time.time()
                
                # Create a document processor instance
                processor = LLMDocumentProcessor()
                
                # Process the files
                document_type = job['DefaultDocumentType'] or None  # TODO: Currently not being used, AI is setting the type
                archived_path = os.path.join(job['ArchiveDirectory'], file_name) if job['ArchiveDirectory'] else None
                force_ai = bool(job['ForceAIExtraction'])  # TODO: For future usage

                if document_type is not None:
                    result = processor.process_document(file_path, document_type=document_type, force_ai_extraction=True)
                else:
                    result = processor.process_document(file_path, force_ai_extraction=True)

                page_count = result['page_count']
                #document_id = f"doc_{file_detail_id}"
                document_id = result['document_id']
                
                # Update file processing record with success
                processing_time = time.time() - file_start_time
                cursor.execute("""
                    UPDATE DocumentJobFileDetails
                    SET Status = 'SUCCEEDED', 
                        DocumentType = ?,
                        DocumentID = ?,
                        PageCount = ?,
                        ArchivedPath = ?,
                        ProcessingDurationSeconds = ?
                    WHERE ExecutionID = ? AND FileName = ?
                """, (document_type, document_id, page_count, archived_path, processing_time, execution_id, file_name))
                
                # Archive the file if needed
                if archived_path:
                    os.makedirs(os.path.dirname(archived_path), exist_ok=True)
                    # Use shutil.move() in actual implementation
                    shutil.move(file_path, archived_path)
                
                # Update statistics
                successful_files += 1
                total_pages += page_count
                
                conn.commit()
                
            except Exception as e:
                # Update file processing record with failure
                cursor.execute("""
                    UPDATE DocumentJobFileDetails
                    SET Status = 'FAILED', 
                        ErrorMessage = ?
                    WHERE ExecutionID = ? AND FileName = ?
                """, (str(e), execution_id, file_name))
                
                failed_files += 1
                conn.commit()
        
        # Update execution record with completion
        total_duration = time.time() - start_time
        cursor.execute("""
            UPDATE DocumentJobExecutions
            SET CompletedAt = getutcdate(),
                Status = 'COMPLETED',
                DocumentsProcessed = ?,
                DocumentsSucceeded = ?,
                DocumentsFailed = ?,
                TotalPages = ?,
                ExecutionDurationSeconds = ?
            WHERE ExecutionID = ?
        """, (total_files, successful_files, failed_files, total_pages, total_duration, execution_id))
        
        conn.commit()
        
    except Exception as e:
        # Update execution record with failure
        cursor.execute("""
            UPDATE DocumentJobExecutions
            SET CompletedAt = getutcdate(),
                Status = 'FAILED',
                DocumentsProcessed = ?,
                DocumentsSucceeded = ?,
                DocumentsFailed = ?,
                ErrorMessage = ?
            WHERE ExecutionID = ?
        """, (total_files, successful_files, failed_files, str(e), execution_id))
        
        conn.commit()
        
    finally:
        cursor.close()
        conn.close()

@app.route('/document_processor')
@login_required
@tier_allows_feature('documents')
def document_processor():
    """Main page - show list of jobs"""
    conn = get_db_connection()
    cursor = conn.cursor()
    # RLS
    cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
    cursor.execute("""
        SELECT JobID, JobName, Description, CreatedBy, CreatedAt, 
               LastRunAt, NextScheduledRunAt, IsActive
        FROM DocumentJobs
        ORDER BY CreatedAt DESC
    """)
    jobs = []
    for row in cursor.fetchall():
        job = {
            'JobID': row[0],
            'JobName': row[1],
            'Description': row[2],
            'CreatedBy': row[3],
            'CreatedAt': row[4],
            'LastRunAt': row[5],
            'NextScheduledRunAt': row[6],
            'IsActive': row[7]
        }
        jobs.append(job)
    
    cursor.close()
    conn.close()
    return render_template('document_processor/index.html', jobs=jobs)

@app.route('/document_processor/job/new', methods=['GET'])
@login_required
def document_processor_new_job():
    """Show form to create a new job"""
    return render_template('document_processor/job_form.html', job=None)

@app.route('/document_processor/job/<int:job_id>', methods=['GET'])
@login_required
def document_processor_view_job(job_id):
    """View details of a specific job"""
    conn = get_db_connection()
    cursor = conn.cursor()
    # RLS
    cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
    cursor.execute("SELECT * FROM DocumentJobs WHERE JobID = ?", (job_id,))
    row = cursor.fetchone()
    
    if row is None:
        cursor.close()
        conn.close()
        flash('Job not found!', 'danger')
        return redirect(url_for('index'))
    
    # Convert row to dictionary
    columns = [column[0] for column in cursor.description]
    job = dict(zip(columns, row))
    
    # Get execution history
    cursor.execute("""
        SELECT ExecutionID, StartedAt, CompletedAt, Status, 
               DocumentsProcessed, DocumentsSucceeded, DocumentsFailed, 
               TotalPages, ExecutionDurationSeconds
        FROM DocumentJobExecutions
        WHERE JobID = ?
        ORDER BY StartedAt DESC
    """, (job_id,))
    
    executions = []
    for row in cursor.fetchall():
        execution = {
            'ExecutionID': row[0],
            'StartedAt': row[1],
            'CompletedAt': row[2],
            'Status': row[3],
            'DocumentsProcessed': row[4],
            'DocumentsSucceeded': row[5],
            'DocumentsFailed': row[6],
            'TotalPages': row[7],
            'DurationSeconds': row[8]
        }
        executions.append(execution)
    
    cursor.close()
    conn.close()

    now = datetime.datetime.now()
    
    return render_template('document_processor/job_detail.html', job=job, executions=executions, now=now)

@app.route('/document_processor/job/<int:job_id>/edit', methods=['GET'])
@login_required
def document_processor_edit_job(job_id):
    """Show form to edit an existing job"""
    conn = get_db_connection()
    cursor = conn.cursor()
    # RLS
    cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
    cursor.execute("SELECT * FROM DocumentJobs WHERE JobID = ?", (job_id,))
    row = cursor.fetchone()
    
    if row is None:
        cursor.close()
        conn.close()
        flash('Job not found!', 'danger')
        return redirect(url_for('document_processor'))
    
    # Convert row to dictionary
    columns = [column[0] for column in cursor.description]
    job = dict(zip(columns, row))
    
    cursor.close()
    conn.close()
    
    return render_template('document_processor/job_form.html', job=job)

@app.route('/document_processor/job/save', methods=['POST'])
@login_required
def document_processor_save_job():
    print('Saving document job...')

    try:
        """Create or update a job"""
        job_id = request.form.get('JobID')
        job_name = request.form.get('JobName')
        description = request.form.get('Description', '')
        created_by = request.form.get('CreatedBy')
        input_directory = request.form.get('InputDirectory')
        archive_directory = request.form.get('ArchiveDirectory', '')
        file_pattern = request.form.get('FilePattern', '*.pdf')
        process_subdirectories = 1 if request.form.get('ProcessSubdirectories') == 'on' or request.form.get('ProcessSubdirectories') == '1' else 0
        default_document_type = request.form.get('DefaultDocumentType', '')
        force_ai_extraction = 1 if request.form.get('ForceAIExtraction') == 'on' or request.form.get('ForceAIExtraction') == '1' else 0
        use_batch_processing = 1 if request.form.get('UseBatchProcessing') == 'on' or request.form.get('UseBatchProcessing') == '1' else 0
        batch_size = request.form.get('BatchSize', 3)
        notify_on_completion = 1 if request.form.get('NotifyOnCompletion') == 'on' or request.form.get('NotifyOnCompletion') == '1' else 0
        notification_email = request.form.get('NotificationEmail', '')
        is_active = 1 if request.form.get('IsActive') == 'on' or request.form.get('IsActive') == '1' else 0
        
        conn = get_db_connection()
        cursor = conn.cursor()

        # RLS
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        if job_id and job_id.isdigit() and int(job_id) > 0:
            # Update existing job
            cursor.execute("""
                UPDATE DocumentJobs SET
                    JobName = ?,
                    Description = ?,
                    LastModifiedBy = ?,
                    LastModifiedAt = getutcdate(),
                    IsActive = ?,
                    InputDirectory = ?,
                    ArchiveDirectory = ?,
                    FilePattern = ?,
                    ProcessSubdirectories = ?,
                    DefaultDocumentType = ?,
                    ForceAIExtraction = ?,
                    UseBatchProcessing = ?,
                    BatchSize = ?,
                    NotifyOnCompletion = ?,
                    NotificationEmail = ?
                WHERE JobID = ?
            """, (
                job_name, description, created_by, is_active,
                input_directory, archive_directory, file_pattern,
                process_subdirectories, default_document_type,
                force_ai_extraction, use_batch_processing, batch_size,
                notify_on_completion, notification_email, job_id
            ))
            flash('Job updated successfully!', 'success')
        else:
            # Create new job
            cursor.execute("""
                INSERT INTO DocumentJobs (
                    JobName, Description, CreatedBy, CreatedAt,
                    IsActive, InputDirectory, ArchiveDirectory,
                    FilePattern, ProcessSubdirectories, DefaultDocumentType,
                    ForceAIExtraction, UseBatchProcessing, BatchSize,
                    NotifyOnCompletion, NotificationEmail
                ) VALUES (?, ?, ?, getutcdate(), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job_name, description, created_by, is_active,
                input_directory, archive_directory, file_pattern,
                process_subdirectories, default_document_type,
                force_ai_extraction, use_batch_processing, batch_size,
                notify_on_completion, notification_email
            ))
            flash('Job created successfully!', 'success')

            try:
                track_feature_usage('document_job', 'created')
            except:
                pass
        
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f'Error saving job: {str(e)}')
        flash(f'Error saving job: {str(e)}', 'danger')
    finally:
        cursor.close()
        conn.close()
    
    print('Returning from function...')
    return redirect(url_for('document_processor'))


@app.route('/document_processor/job/<int:job_id>/delete', methods=['POST'])
@login_required
def document_processor_delete_job(job_id):
    """Delete a job"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # RLS
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        # Check if there are any executions for this job
        cursor.execute("SELECT COUNT(*) FROM DocumentJobExecutions WHERE JobID = ?", (job_id,))
        execution_count = cursor.fetchone()[0]
        
        if execution_count > 0:
            # Don't actually delete, just mark as inactive
            cursor.execute("""
                UPDATE DocumentJobs
                SET IsActive = 0, 
                    LastModifiedAt = getutcdate(),
                    LastModifiedBy = 'system_delete'
                WHERE JobID = ?
            """, (job_id,))
            flash('Job marked as inactive due to existing execution history.', 'warning')
        else:
            # No executions, we can safely delete
            cursor.execute("DELETE FROM DocumentJobs WHERE JobID = ?", (job_id,))
            flash('Job deleted successfully!', 'success')
        
        conn.commit()
    except Exception as e:
        conn.rollback()
        flash(f'Error deleting job: {str(e)}', 'danger')
    finally:
        cursor.close()
        conn.close()
    
    return redirect(url_for('document_processor'))

@app.route('/document_processor/job/<int:job_id>/run', methods=['POST'])
@login_required
def document_processor_run_job(job_id):
    """Trigger a job to run immediately"""
    print('Running job...')

    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # RLS
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        print('Getting job details...')
        # Get the job details
        cursor.execute("SELECT * FROM DocumentJobs WHERE JobID = ?", (job_id,))
        job_row = cursor.fetchone()
        
        if not job_row:
            flash('Job not found!', 'danger')
            return redirect(url_for('document_processor'))
            
        # Convert row to dictionary
        columns = [column[0] for column in cursor.description]
        job = dict(zip(columns, job_row))

        # Update the last run time
        cursor.execute("""
            UPDATE DocumentJobs
            SET LastRunAt = getutcdate()
            WHERE JobID = ?
        """, (job_id,))
        
        # Create a new execution record
        cursor.execute("""
            INSERT INTO DocumentJobExecutions (
                JobID, StartedAt, Status, DocumentsProcessed
            ) VALUES (?, getutcdate(), 'QUEUED', 0)
        """, (job_id,))

        conn.commit()

        # Run the job
        # Get the execution ID
        cursor.execute("SELECT @@IDENTITY")
        execution_id = cursor.fetchone()[0]

        cursor.close()
        conn.close()
        print('Job started successfully!', execution_id)
        
        flash('Job started successfully!', 'success')
    except Exception as e:
        print(f'Error queuing job: {str(e)}')
        conn.rollback()
        flash(f'Error queuing job: {str(e)}', 'danger')
        cursor.close()
        conn.close()
    
    return redirect(url_for('document_processor_view_job', job_id=job_id))

@app.route('/api/scheduler/execute_document_job/<int:job_id>', methods=['POST'])
def execute_document_job_api(job_id):
    """API endpoint for the scheduler to trigger a document job"""
    try:
        # Verify API key if provided
        data = request.json or {}
        api_key = data.get('api_key')
        execution_id = data.get('execution_id')  # Get execution_id if provided
        
        if api_key and api_key != os.getenv('API_KEY'):
            return jsonify({
                'status': 'error',
                'message': 'Invalid API key'
            }), 401
        
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get the job details
        cursor.execute("SELECT * FROM DocumentJobs WHERE JobID = ?", (job_id,))
        job_row = cursor.fetchone()
        
        if not job_row:
            return jsonify({
                'status': 'error',
                'message': f'Job {job_id} not found'
            }), 404
            
        # Convert row to dictionary
        columns = [column[0] for column in cursor.description]
        job = dict(zip(columns, job_row))

        # Check for existing queued executions
        cursor.execute("""
            SELECT ExecutionID 
            FROM DocumentJobExecutions 
            WHERE JobID = ? AND Status = 'QUEUED'
        """, (job_id,))
        existing_execution = cursor.fetchone()

        if existing_execution:
            # If an execution_id was provided and it matches the existing one, use it
            if execution_id and int(execution_id) == existing_execution[0]:
                execution_id = existing_execution[0]
            else:
                logger.info(f'Job {job_id} already has a queued execution')
                print(f"=====>>>>> Job {job_id} already has a queued execution")
                return jsonify({
                    'status': 'error',
                    'message': f'Job {job_id} already has a queued execution'
                }), 409

        # If no existing execution or we're using the provided execution_id
        if not existing_execution:
            # Update the last run time
            cursor.execute("""
                UPDATE DocumentJobs
                SET LastRunAt = getutcdate()
                WHERE JobID = ?
            """, (job_id,))
            print(f"=====>>>>> Inserting execution record for job {job_id}")
            cursor.execute("""
                INSERT INTO DocumentJobExecutions (
                    JobID, StartedAt, Status, DocumentsProcessed
                ) VALUES (?, getutcdate(), 'QUEUED', 0)
            """, (job_id,))
            conn.commit()

            # Get the execution ID
            cursor.execute("SELECT @@IDENTITY")
            execution_id = cursor.fetchone()[0]

        # Close database connection
        cursor.close()
        conn.close()
        
        # Log success
        logger.info(f'Job {job_id} scheduled successfully with execution ID {execution_id}')

        try:
            track_feature_usage('document_job', 'executed')
        except Exception as e:
            logger.warning(f'Failed to track document job executed: {e}')
        
        # Return success response
        return jsonify({
            'status': 'success',
            'message': f'Job {job_id} scheduled successfully',
            'execution_id': execution_id
        })
        
    except Exception as e:
        logger.error(f'Error scheduling job {job_id}: {str(e)}')
        
        # Close database connection if it exists
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()
            
        return jsonify({
            'status': 'error',
            'message': f'Error scheduling job: {str(e)}'
        }), 500

@app.route('/document_processor/job/<int:job_id>/executions/<int:execution_id>', methods=['GET'])
@login_required
def document_processor_view_execution(job_id, execution_id):
    """View details of a specific job execution"""
    conn = get_db_connection()
    cursor = conn.cursor()
    # RLS
    cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
    # Get execution details
    cursor.execute("""
        SELECT * FROM DocumentJobExecutions
        WHERE ExecutionID = ? AND JobID = ?
    """, (execution_id, job_id))
    row = cursor.fetchone()
    
    if row is None:
        cursor.close()
        conn.close()
        flash('Execution not found!', 'danger')
        return redirect(url_for('view_job', job_id=job_id))
    
    # Convert row to dictionary
    columns = [column[0] for column in cursor.description]
    execution = dict(zip(columns, row))
    
    # Get file details for this execution
    cursor.execute("""
        SELECT * FROM DocumentJobFileDetails
        WHERE ExecutionID = ?
        ORDER BY ProcessedAt DESC
    """, (execution_id,))
    
    files = []
    for row in cursor.fetchall():
        columns = [column[0] for column in cursor.description]
        file_data = dict(zip(columns, row))
        files.append(file_data)
    
    cursor.close()
    conn.close()
    
    return render_template('document_processor/execution_detail.html', execution=execution, files=files, job_id=job_id)

@app.route('/directories', methods=['GET'])
@login_required
def list_directories():
    """API endpoint to list directories for directory picker"""
    base_path = request.args.get('path', '').strip()

    try:
        # If no path or root requested, return available drive letters on Windows
        if not base_path or base_path == '/':
            if os.name == 'nt':
                import string
                drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
                return jsonify({
                    'path': '',
                    'directories': drives,
                    'is_root': True
                })
            else:
                base_path = '/'

        # Normalize the path for the current OS (UNC-aware)
        base_path = normalize_path(base_path)

        directories = sorted([d for d in os.listdir(base_path) if os.path.isdir(os.path.join(base_path, d))], key=str.lower)
        return jsonify({
            'path': base_path,
            'directories': directories,
            'is_root': False
        })
    except PermissionError:
        return jsonify({
            'error': f'Access denied: {base_path}'
        }), 403
    except FileNotFoundError:
        return jsonify({
            'error': f'Directory not found: {base_path}'
        }), 404
    except Exception as e:
        return jsonify({
            'error': str(e)
        }), 400
    
#-------------------------------------------
# Search
#-------------------------------------------
from math import ceil

@app.route('/document-search-legacy')
@login_required
def document_search_page_legacy():
    try:
        """Render the document search page with server-side search functionality"""
        # Get basic search parameters from query string
        search_query = request.args.get('query', '')
        document_type = request.args.get('document_type', '')
        min_score = float(request.args.get('min_score', 0.5))
        max_results = int(request.args.get('max_results', 10))
        page = int(request.args.get('page', 1))
        advanced_search = 'advanced' in request.args
        print('advanced_search:', advanced_search)
        print('request.args:', request.args)
        
        # Collect field search filters if present
        field_filters = []
        field_names = request.args.getlist('field_name[]')
        field_operators = request.args.getlist('field_operator[]')
        field_values = request.args.getlist('field_value[]')

        print('Field Name:', field_names)
        print('Field Operators:', field_operators)
        print('Field Values:', field_values)
        
        print('Processing field filters...')
        # Process field filters
        if field_names and field_operators and field_values:
            for i in range(len(field_names)):
                if i < len(field_operators) and i < len(field_values) and field_names[i] and field_values[i]:
                    # Get display name for the field
                    display_name = field_names[i].split('.')[-1] if '.' in field_names[i] else field_names[i]
                    display_name = display_name.replace('_', ' ').title()
                    
                    field_filters.append({
                        'field_path': field_names[i],
                        'display_name': display_name,
                        'operator': field_operators[i],
                        'value': field_values[i]
                    })
                    print('Appending -->>', str({
                        'field_path': field_names[i],
                        'display_name': display_name,
                        'operator': field_operators[i],
                        'value': field_values[i]
                    }))
        
        print('Field Filters:', field_filters)
        
        # Default values
        search_results = []
        error_message = None
        pagination = None
        available_fields = []
        common_fields = []
    except Exception as e:
        print(f'Search error: {str(e)}')
        abort(404)
    
    try:
        # Get all document types for filters (regardless of search)
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context if needed
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get document types
        cursor.execute("SELECT DISTINCT document_type FROM Documents WHERE is_knowledge_document = 0 ORDER BY document_type")
        document_types = [row[0] for row in cursor.fetchall()]
        
        # Get document counts
        cursor.execute("""
            SELECT document_type, COUNT(*) as doc_count 
            FROM Documents 
            WHERE is_knowledge_document = 0 
            GROUP BY document_type 
            ORDER BY doc_count DESC
        """)
        document_counts = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Get available fields for search
        if document_type:
            # If document type is selected, get fields specific to that type
            cursor.execute("""
                SELECT df.field_name, df.field_path, COUNT(*) as field_count
                FROM DocumentFields df
                JOIN DocumentPages dp ON df.page_id = dp.page_id
                JOIN Documents d ON dp.document_id = d.document_id
                WHERE d.document_type = ?
                AND d.is_knowledge_document = 0 
                GROUP BY df.field_name, df.field_path
                ORDER BY field_count DESC, field_name
            """, (document_type,))
        else:
            # Get all available fields
            cursor.execute("""
                SELECT df.field_name, df.field_path, COUNT(*) as field_count
                FROM DocumentFields df
                JOIN DocumentPages dp ON df.page_id = dp.page_id
                JOIN Documents d ON dp.document_id = d.document_id
                WHERE d.is_knowledge_document = 0 
                GROUP BY df.field_name, df.field_path
                ORDER BY field_count DESC, field_name
            """)
        
        # Process fields into a hierarchical structure
        field_data = cursor.fetchall()
        fields_by_group = defaultdict(list)
        
        for field_name, field_path, field_count in field_data:
            # Get group from path (first part before the dot)
            group = field_path.split('.')[0] if field_path and '.' in field_path else 'General'
            display_name = field_name.replace('_', ' ').title()
            
            fields_by_group[group].append({
                'name': field_name,
                'path': field_path or field_name,
                'display_name': display_name,
                'count': field_count
            })
        
        # Create structured list of field groups
        available_fields = [
            {'group': group, 'fields': fields} 
            for group, fields in fields_by_group.items()
        ]
        
        # Sort groups alphabetically except put 'General' first
        available_fields.sort(key=lambda x: (0 if x['group'] == 'General' else 1, x['group']))
        
        # Get common fields (top 10 most used)
        cursor.execute("""
            SELECT TOP 10 df.field_name, df.field_path, COUNT(*) as field_count
            FROM DocumentFields df
            JOIN DocumentPages dp ON df.page_id = dp.page_id
            JOIN Documents d ON dp.document_id = d.document_id
            WHERE d.is_knowledge_document = 0
            GROUP BY df.field_name, df.field_path
            ORDER BY field_count DESC
        """)
        
        common_fields = []
        for field_name, field_path, field_count in cursor.fetchall():
            display_name = field_name.replace('_', ' ').title()
            common_fields.append({
                'name': field_name,
                'path': field_path or field_name,
                'display_name': display_name,
                'count': field_count
            })
        
        # Perform search if query or field filters are provided
        if search_query or field_filters:
            # Create processor instance
            processor = LLMDocumentSearch()
            
            # Set up filters
            filters = {}
            if document_type:
                # No need to add to filters - will be handled directly in search_documents
                pass
            
            # First step: If we have field filters, find matching documents
            matching_page_ids = set()
            matching_fields_by_page = {}
            
            if field_filters:
                query_parts = []
                params = []
                
                for filter in field_filters:
                    print('Filter:', filter)
                    field_path = filter['field_path']
                    operator = filter['operator']
                    value = filter['value']
                    
                    # Build SQL condition based on operator
                    if operator == 'equals':
                        if field_path == '%':
                            query_parts.append("(df.field_path LIKE ? AND df.field_value = ?)")
                        else:
                            query_parts.append("(df.field_path = ? AND df.field_value = ?)")
                        params.extend([field_path, value])
                    elif operator == 'contains':
                        if field_path == '%':
                            query_parts.append("(df.field_path LIKE ? AND df.field_value LIKE ?)")
                        else:
                            query_parts.append("(df.field_path = ? AND df.field_value LIKE ?)")
                        params.extend([field_path, f'%{value}%'])
                    elif operator == 'starts_with':
                        if field_path == '%':
                            query_parts.append("(df.field_path LIKE ? AND df.field_value LIKE ?)")
                        else:
                            query_parts.append("(df.field_path = ? AND df.field_value LIKE ?)")
                        params.extend([field_path, f'{value}%'])
                    elif operator == 'ends_with':
                        if field_path == '%':
                            query_parts.append("(df.field_path LIKE ? AND df.field_value LIKE ?)")
                        else:
                            query_parts.append("(df.field_path = ? AND df.field_value LIKE ?)")
                        params.extend([field_path, f'%{value}'])

                    print('Params:', params)
                
                # Build the complete SQL query for field filtering
                if query_parts:
                    field_filter_sql = f"""
                        SELECT dp.page_id, df.field_name, df.field_path, df.field_value
                        FROM DocumentFields df
                        JOIN DocumentPages dp ON df.page_id = dp.page_id
                        JOIN Documents d ON dp.document_id = d.document_id
                        WHERE ({' OR '.join(query_parts)}) AND d.is_knowledge_document = 0 
                        {f"AND d.document_type = '{document_type}'" if document_type else ""}
                    """

                    print('SQL Filter Query:', field_filter_sql)
                    print('Params:', params)
                    
                    cursor.execute(field_filter_sql, params)
                    field_matches = cursor.fetchall()
                    
                    # Process matched pages
                    for page_id, field_name, field_path, field_value in field_matches:
                        matching_page_ids.add(page_id)
                        
                        if page_id not in matching_fields_by_page:
                            matching_fields_by_page[page_id] = []
                            
                        matching_fields_by_page[page_id].append({
                            'name': field_name.replace('_', ' ').title(),
                            'path': field_path,
                            'value': field_value
                        })
            
            # If we have field matches or just a text search, proceed with search
            if matching_page_ids or search_query:
                print('Performing search...')
                # Perform search with vector DB for text query
                if search_query:
                    results = processor.search_documents(
                        query=search_query,
                        document_type=document_type if document_type else None,
                        filters=filters if filters else None,
                        n_results=1000,  # Get more results for pagination
                        min_score=min_score
                    )
                    print('Search Results:', str(results))
                else:
                    # If no text query but we have field filters, get basic info for all matching pages
                    results = []
                    if matching_page_ids:
                        # Get page info for all matching pages
                        page_ids_str = "', '".join(matching_page_ids)
                        cursor.execute(f"""
                            SELECT dp.page_id, d.document_id, d.filename, d.document_type, dp.page_number, dp.full_text
                            FROM DocumentPages dp
                            JOIN Documents d ON dp.document_id = d.document_id
                            WHERE dp.page_id IN ('{page_ids_str}') AND d.is_knowledge_document = 0 
                        """)
                        
                        for page_id, document_id, filename, doc_type, page_number, full_text in cursor.fetchall():
                            # Create a result structure similar to what the processor would return
                            snippet = full_text[:250] + "..." if full_text and len(full_text) > 250 else (full_text or "")
                            results.append({
                                "page_id": page_id,
                                "document_id": document_id,
                                "filename": filename,
                                "document_type": doc_type,
                                "page_number": page_number,
                                "relevance_score": 1.0,  # Perfect match since it matches exact field criteria
                                "snippet": snippet
                            })
                
                # For combined search (text + fields), filter by matching page IDs
                if search_query and field_filters:
                    # Filter text search results to only include pages that also match field criteria
                    results = [r for r in results if r["page_id"] in matching_page_ids]
                
                # Format results
                formatted_results = []
                for result in results:
                    # Enhance with additional metadata from SQL if needed
                    formatted_result = {
                        "document_id": result["document_id"],
                        "page_id": result["page_id"],
                        "filename": result["filename"],
                        "document_type": result["document_type"],
                        "page_number": result["page_number"],
                        "relevance_score": result["relevance_score"],
                        "snippet": highlight_snippet(result["snippet"], search_query),
                    }
                    
                    # Get additional document info
                    cursor.execute("""
                        SELECT processed_at, reference_number, customer_id, vendor_id, document_date
                        FROM Documents 
                        WHERE document_id = ? AND is_knowledge_document = 0 
                    """, (result["document_id"],))
                    
                    doc_info = cursor.fetchone()
                    if doc_info:
                        formatted_result["processed_at"] = doc_info[0].strftime("%Y-%m-%d %H:%M") if doc_info[0] else ""
                        formatted_result["reference_number"] = doc_info[1] or ""
                        formatted_result["customer_id"] = doc_info[2] or ""
                        formatted_result["vendor_id"] = doc_info[3] or ""
                        formatted_result["document_date"] = doc_info[4] if doc_info[4] else ""
                    
                    # Add matching fields if this was a field search
                    if result["page_id"] in matching_fields_by_page:
                        formatted_result["matching_fields"] = matching_fields_by_page[result["page_id"]]
                    
                    formatted_results.append(formatted_result)
                
                # Implement pagination
                items_per_page = max_results
                total_items = len(formatted_results)
                total_pages = ceil(total_items / items_per_page)
                
                # Verify page is within bounds
                if page < 1:
                    page = 1
                elif page > total_pages and total_pages > 0:
                    page = total_pages
                    
                # Calculate slice indices
                start_idx = (page - 1) * items_per_page
                end_idx = start_idx + items_per_page
                
                # Get current page of results
                current_page_results = formatted_results[start_idx:end_idx]
                
                # Create pagination object
                pagination = {
                    'page': page,
                    'per_page': items_per_page,
                    'total': total_items,
                    'pages': total_pages,
                    'has_prev': page > 1,
                    'has_next': page < total_pages,
                    'prev_num': page - 1,
                    'next_num': page + 1,
                    'iter_pages': lambda left_edge=2, right_edge=2, left_current=2, right_current=2: iter_pages(
                        page, total_pages, left_edge, right_edge, left_current, right_current
                    )
                }
                
                search_results = current_page_results
                processor.close()
            
        conn.close()
            
    except Exception as e:
        print('Error: ', str(e))
        error_message = str(e)
        document_types = []
        document_counts = {}
        
    # Render the template with search results
    return render_template(
        'document_search.html',
        search_query=search_query,
        selected_type=document_type,
        min_score=min_score,
        max_results=max_results,
        document_types=document_types,
        document_counts=document_counts,
        search_results=search_results,
        error_message=error_message,
        pagination=pagination,
        advanced_search=advanced_search,
        field_filters=field_filters,
        available_fields=available_fields,
        common_fields=common_fields
    )


@app.route('/document-search')
@login_required
@tier_allows_feature('documents')
def document_search_page():
    try:
        """Render the document search page with server-side search functionality"""
        # Get basic search parameters from query string
        search_query = request.args.get('query', '')
        document_type = request.args.get('document_type', '')
        min_score = float(request.args.get('min_score', 0.5))
        max_results = int(request.args.get('max_results', 10))
        page = int(request.args.get('page', 1))
        advanced_search = 'advanced' in request.args
        print('advanced_search:', advanced_search)
        print('request.args:', request.args)
        
        # Collect field search filters if present
        field_filters = []
        field_names = request.args.getlist('field_name[]')
        field_operators = request.args.getlist('field_operator[]')
        field_values = request.args.getlist('field_value[]')

        # NEW: Process attribute search parameters
        search_mode = request.args.get('search_mode', 'fields')
        attribute_filters = []

        attribute_names = request.args.getlist('attribute_name[]')
        attribute_operators = request.args.getlist('attribute_operator[]')
        attribute_values = request.args.getlist('attribute_value[]')

        print('Search Mode:', search_mode)
        print('Attribute Names:', attribute_names)
        print('Attribute Operators:', attribute_operators)
        print('Attribute Values:', attribute_values)

        # Process attribute filters
        if attribute_names and attribute_operators and attribute_values:
            for i in range(len(attribute_names)):
                if i < len(attribute_operators) and i < len(attribute_values) and attribute_names[i] and attribute_values[i]:
                    attribute_filters.append({
                        'attribute_name': attribute_names[i],
                        'operator': attribute_operators[i],
                        'value': attribute_values[i]
                    })

        print('Attribute Filters:', attribute_filters)

        print('Field Name:', field_names)
        print('Field Operators:', field_operators)
        print('Field Values:', field_values)
        
        print('Processing field filters...')
        # Process field filters
        if field_names and field_operators and field_values:
            for i in range(len(field_names)):
                if i < len(field_operators) and i < len(field_values) and field_names[i] and field_values[i]:
                    # Get display name for the field
                    display_name = field_names[i].split('.')[-1] if '.' in field_names[i] else field_names[i]
                    display_name = display_name.replace('_', ' ').title()
                    
                    field_filters.append({
                        'field_path': field_names[i],
                        'display_name': display_name,
                        'operator': field_operators[i],
                        'value': field_values[i]
                    })
                    print('Appending -->>', str({
                        'field_path': field_names[i],
                        'display_name': display_name,
                        'operator': field_operators[i],
                        'value': field_values[i]
                    }))
        
        print('Field Filters:', field_filters)

        # Create connection
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context if needed
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Default values
        # Determine which search to perform
        if search_mode == 'attributes' and attribute_filters:
            print("Performing ATTRIBUTE search...")
            search_results = perform_attribute_search(attribute_filters, document_type, search_query, cursor, max_results)
        else:
            print("Using existing search logic...")
            search_results = []  # Your existing logic will populate this

        error_message = None
        pagination = None
        available_fields = []
        common_fields = []
    except Exception as e:
        print(f'Search error: {str(e)}')
        abort(404)
    
    try:
        # Get all document types for filters (regardless of search)
        #conn = get_db_connection()
        #cursor = conn.cursor()
        
        # Set tenant context if needed
        #cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get document types
        cursor.execute("SELECT DISTINCT document_type FROM Documents WHERE is_knowledge_document = 0 ORDER BY document_type")
        document_types = [row[0] for row in cursor.fetchall()]
        
        # Get document counts
        cursor.execute("""
            SELECT document_type, COUNT(*) as doc_count 
            FROM Documents 
            WHERE is_knowledge_document = 0 
            GROUP BY document_type 
            ORDER BY doc_count DESC
        """)
        document_counts = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Get available fields for search
        if document_type:
            # If document type is selected, get fields specific to that type
            cursor.execute("""
                SELECT df.field_name, df.field_path, COUNT(*) as field_count
                FROM DocumentFields df
                JOIN DocumentPages dp ON df.page_id = dp.page_id
                JOIN Documents d ON dp.document_id = d.document_id
                WHERE d.document_type = ?
                AND d.is_knowledge_document = 0 
                GROUP BY df.field_name, df.field_path
                ORDER BY field_count DESC, field_name
            """, (document_type,))
        else:
            # Get all available fields
            cursor.execute("""
                SELECT df.field_name, df.field_path, COUNT(*) as field_count
                FROM DocumentFields df
                JOIN DocumentPages dp ON df.page_id = dp.page_id
                JOIN Documents d ON dp.document_id = d.document_id
                WHERE d.is_knowledge_document = 0 
                GROUP BY df.field_name, df.field_path
                ORDER BY field_count DESC, field_name
            """)
        
        # Process fields into a hierarchical structure
        field_data = cursor.fetchall()
        fields_by_group = defaultdict(list)
        
        for field_name, field_path, field_count in field_data:
            # Get group from path (first part before the dot)
            group = field_path.split('.')[0] if field_path and '.' in field_path else 'General'
            display_name = field_name.replace('_', ' ').title()
            
            fields_by_group[group].append({
                'name': field_name,
                'path': field_path or field_name,
                'display_name': display_name,
                'count': field_count
            })
        
        # Create structured list of field groups
        available_fields = [
            {'group': group, 'fields': fields} 
            for group, fields in fields_by_group.items()
        ]
        
        # Sort groups alphabetically except put 'General' first
        available_fields.sort(key=lambda x: (0 if x['group'] == 'General' else 1, x['group']))
        
        # Get common fields (top 10 most used)
        cursor.execute("""
            SELECT TOP 10 df.field_name, df.field_path, COUNT(*) as field_count
            FROM DocumentFields df
            JOIN DocumentPages dp ON df.page_id = dp.page_id
            JOIN Documents d ON dp.document_id = d.document_id
            WHERE d.is_knowledge_document = 0
            GROUP BY df.field_name, df.field_path
            ORDER BY field_count DESC
        """)
        
        common_fields = []
        for field_name, field_path, field_count in cursor.fetchall():
            display_name = field_name.replace('_', ' ').title()
            common_fields.append({
                'name': field_name,
                'path': field_path or field_name,
                'display_name': display_name,
                'count': field_count
            })
        
        # Perform search if query or field filters are provided
        if search_query or field_filters:
            # Create processor instance
            processor = LLMDocumentSearch()
            
            # Set up filters
            filters = {}
            if document_type:
                # No need to add to filters - will be handled directly in search_documents
                pass
            
            # First step: If we have field filters, find matching documents
            matching_page_ids = set()
            matching_fields_by_page = {}
            
            if field_filters:
                query_parts = []
                params = []
                
                for filter in field_filters:
                    print('Filter:', filter)
                    field_path = filter['field_path']
                    operator = filter['operator']
                    value = filter['value']
                    
                    # Build SQL condition based on operator
                    if operator == 'equals':
                        if field_path == '%':
                            query_parts.append("(df.field_path LIKE ? AND df.field_value = ?)")
                        else:
                            query_parts.append("(df.field_path = ? AND df.field_value = ?)")
                        params.extend([field_path, value])
                    elif operator == 'contains':
                        if field_path == '%':
                            query_parts.append("(df.field_path LIKE ? AND df.field_value LIKE ?)")
                        else:
                            query_parts.append("(df.field_path = ? AND df.field_value LIKE ?)")
                        params.extend([field_path, f'%{value}%'])
                    elif operator == 'starts_with':
                        if field_path == '%':
                            query_parts.append("(df.field_path LIKE ? AND df.field_value LIKE ?)")
                        else:
                            query_parts.append("(df.field_path = ? AND df.field_value LIKE ?)")
                        params.extend([field_path, f'{value}%'])
                    elif operator == 'ends_with':
                        if field_path == '%':
                            query_parts.append("(df.field_path LIKE ? AND df.field_value LIKE ?)")
                        else:
                            query_parts.append("(df.field_path = ? AND df.field_value LIKE ?)")
                        params.extend([field_path, f'%{value}'])

                    print('Params:', params)
                
                # Build the complete SQL query for field filtering
                if query_parts:
                    field_filter_sql = f"""
                        SELECT dp.page_id, df.field_name, df.field_path, df.field_value
                        FROM DocumentFields df
                        JOIN DocumentPages dp ON df.page_id = dp.page_id
                        JOIN Documents d ON dp.document_id = d.document_id
                        WHERE ({' OR '.join(query_parts)}) AND d.is_knowledge_document = 0 
                        {f"AND d.document_type = '{document_type}'" if document_type else ""}
                    """

                    print('SQL Filter Query:', field_filter_sql)
                    print('Params:', params)
                    
                    cursor.execute(field_filter_sql, params)
                    field_matches = cursor.fetchall()
                    
                    # Process matched pages
                    for page_id, field_name, field_path, field_value in field_matches:
                        matching_page_ids.add(page_id)
                        
                        if page_id not in matching_fields_by_page:
                            matching_fields_by_page[page_id] = []
                            
                        matching_fields_by_page[page_id].append({
                            'name': field_name.replace('_', ' ').title(),
                            'path': field_path,
                            'value': field_value
                        })
            
            # If we have field matches or just a text search, proceed with search
            if matching_page_ids or search_query:
                print('Performing search...')
                # Perform search with vector DB for text query
                # TODO: Implement this in agent search tools
                if search_query:
                    results = processor.search_documents(
                        query=search_query,
                        document_type=document_type if document_type else None,
                        filters=filters if filters else None,
                        n_results=1000,  # Get more results for pagination
                        min_score=min_score
                    )
                    print('Search Results:', str(results))
                else:
                    # If no text query but we have field filters, get basic info for all matching pages
                    results = []
                    if matching_page_ids:
                        # Get page info for all matching pages
                        page_ids_str = "', '".join(matching_page_ids)
                        cursor.execute(f"""
                            SELECT dp.page_id, d.document_id, d.filename, d.document_type, dp.page_number, dp.full_text
                            FROM DocumentPages dp
                            JOIN Documents d ON dp.document_id = d.document_id
                            WHERE dp.page_id IN ('{page_ids_str}') AND d.is_knowledge_document = 0 
                        """)
                        
                        for page_id, document_id, filename, doc_type, page_number, full_text in cursor.fetchall():
                            # Create a result structure similar to what the processor would return
                            snippet = full_text[:250] + "..." if full_text and len(full_text) > 250 else (full_text or "")
                            results.append({
                                "page_id": page_id,
                                "document_id": document_id,
                                "filename": filename,
                                "document_type": doc_type,
                                "page_number": page_number,
                                "relevance_score": 1.0,  # Perfect match since it matches exact field criteria
                                "snippet": snippet
                            })
                
                # For combined search (text + fields), filter by matching page IDs
                if search_query and field_filters:
                    # Filter text search results to only include pages that also match field criteria
                    results = [r for r in results if r["page_id"] in matching_page_ids]
                
                # Format results
                formatted_results = []
                for result in results:
                    # Enhance with additional metadata from SQL if needed
                    formatted_result = {
                        "document_id": result["document_id"],
                        "page_id": result["page_id"],
                        "filename": result["filename"],
                        "document_type": result["document_type"],
                        "page_number": result["page_number"],
                        "relevance_score": result["relevance_score"],
                        "snippet": highlight_snippet(result["snippet"], search_query),
                    }
                    
                    # Get additional document info
                    cursor.execute("""
                        SELECT processed_at, reference_number, customer_id, vendor_id, document_date
                        FROM Documents 
                        WHERE document_id = ? AND is_knowledge_document = 0 
                    """, (result["document_id"],))
                    
                    doc_info = cursor.fetchone()
                    if doc_info:
                        formatted_result["processed_at"] = doc_info[0].strftime("%Y-%m-%d %H:%M") if doc_info[0] else ""
                        formatted_result["reference_number"] = doc_info[1] or ""
                        formatted_result["customer_id"] = doc_info[2] or ""
                        formatted_result["vendor_id"] = doc_info[3] or ""
                        formatted_result["document_date"] = doc_info[4] if doc_info[4] else ""
                    
                    # Add matching fields if this was a field search
                    if result["page_id"] in matching_fields_by_page:
                        formatted_result["matching_fields"] = matching_fields_by_page[result["page_id"]]
                    
                    formatted_results.append(formatted_result)
                
                # Implement pagination
                items_per_page = max_results
                total_items = len(formatted_results)
                total_pages = ceil(total_items / items_per_page)
                
                # Verify page is within bounds
                if page < 1:
                    page = 1
                elif page > total_pages and total_pages > 0:
                    page = total_pages
                    
                # Calculate slice indices
                start_idx = (page - 1) * items_per_page
                end_idx = start_idx + items_per_page
                
                # Get current page of results
                current_page_results = formatted_results[start_idx:end_idx]
                
                # Create pagination object
                pagination = {
                    'page': page,
                    'per_page': items_per_page,
                    'total': total_items,
                    'pages': total_pages,
                    'has_prev': page > 1,
                    'has_next': page < total_pages,
                    'prev_num': page - 1,
                    'next_num': page + 1,
                    'iter_pages': lambda left_edge=2, right_edge=2, left_current=2, right_current=2: iter_pages(
                        page, total_pages, left_edge, right_edge, left_current, right_current
                    )
                }
                
                search_results = current_page_results
                processor.close()
            
        conn.close()
            
    except Exception as e:
        print('Error: ', str(e))
        error_message = str(e)
        document_types = []
        document_counts = {}
        
    # Render the template with search results
    return render_template(
        'document_search.html',
        search_query=search_query,
        selected_type=document_type,
        min_score=min_score,
        max_results=max_results,
        document_types=document_types,
        document_counts=document_counts,
        search_results=search_results,
        attribute_filters=attribute_filters,
        search_mode=search_mode,
        error_message=error_message,
        pagination=pagination,
        advanced_search=advanced_search,
        field_filters=field_filters,
        available_fields=available_fields,
        common_fields=common_fields
    )

@app.route('/document/view/<string:document_id>')
def document_view_page(document_id):
    """Render the document view page"""
    page_number = request.args.get('page', 1, type=int)
    
    try:
        print('Viewing document/page:', document_id, page_number)
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context if needed
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get document info
        cursor.execute("""
            SELECT d.filename, d.document_type, d.page_count, d.processed_at, 
                   d.reference_number, d.customer_id, d.vendor_id, d.document_date, d.original_path, d.archived_path
            FROM Documents d
            WHERE d.document_id = ?
        """, (document_id,))
        
        print('Check 1...')
        doc_row = cursor.fetchone()
        if not doc_row:
            abort(404)
            
        document = {
            "document_id": document_id,
            "filename": doc_row[0],
            "document_type": doc_row[1],
            "page_count": doc_row[2],
            "processed_at": doc_row[3].strftime("%Y-%m-%d %H:%M") if doc_row[3] else "",
            "reference_number": doc_row[4] or "",
            "customer_id": doc_row[5] or "",
            "vendor_id": doc_row[6] or "",
            "document_date": doc_row[7] if doc_row[7] else "",
            "original_path": doc_row[8] or "",
            "archived_path": doc_row[9] or ""
        }
        
        # Get page content
        cursor.execute("""
            SELECT page_id, page_number, full_text
            FROM DocumentPages
            WHERE document_id = ? AND page_number = ?
        """, (document_id, page_number))
        print('Check 2...')
        page_row = cursor.fetchone()
        if not page_row:
            # If specific page not found, try to get the first page
            cursor.execute("""
                SELECT page_id, page_number, full_text
                FROM DocumentPages
                WHERE document_id = ?
                ORDER BY page_number
                OFFSET 0 ROWS FETCH NEXT 1 ROWS ONLY
            """, (document_id,))
            page_row = cursor.fetchone()
            print('Check 3...')
            if not page_row:
                abort(404)
                
            page_number = page_row[1]  # Update page number to the one we found
        
        page = {
            "page_id": page_row[0],
            "page_number": page_row[1],
            "content": page_row[2],
        }
        
        # Get extracted fields for this page
        cursor.execute("""
            SELECT field_name, field_value, field_path
            FROM DocumentFields
            WHERE page_id = ?
        """, (page["page_id"],))
        print('Check 4...')
        fields = []
        for row in cursor.fetchall():
            fields.append({
                "name": row[0],
                "value": row[1],
                "path": row[2]
            })
        
        page["fields"] = fields
        
        # Check if search highlight is needed
        search_query = request.args.get('highlight', '')
        if search_query:
            page["content"] = highlight_document_content(page["content"], search_query)
        
        conn.close()
        
        print('Rendering document view template...')
        # Render document view page
        return render_template(
            'document_view.html',  # Create this template to display the document
            document=document,
            page=page,
            current_page=page_number,
            has_prev_page=page_number > 1,
            has_next_page=page_number < document["page_count"],
            search_query=request.args.get('query', ''),  # Pass back the search query for navigation
            return_url=request.args.get('return_url', url_for('document_search_page'))
        )
        
    except Exception as e:
        print('Document view error:', str(e))
        return render_template('error.html', error=str(e))

# Helper functions

def get_agent_environment_for_export(agent_id):
    """
    Get the environment details assigned to an agent for export purposes.
    Returns environment metadata and package list, or None if no environment assigned.
    
    Args:
        agent_id: The ID of the agent to get environment for
        
    Returns:
        dict with environment details and packages, or None if no environment
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get assigned environment details
        cursor.execute("""
            SELECT 
                e.environment_id,
                e.name,
                e.description,
                e.python_version,
                e.status
            FROM AgentEnvironmentAssignments a
            INNER JOIN AgentEnvironments e ON a.environment_id = e.environment_id
            WHERE a.agent_id = ? AND a.is_active = 1 AND e.is_deleted = 0
        """, agent_id)
        
        env_row = cursor.fetchone()
        
        if not env_row:
            cursor.close()
            conn.close()
            return None
        
        # Get packages for this environment
        cursor.execute("""
            SELECT 
                package_name,
                version,
                requested_version
            FROM AgentEnvironmentPackages
            WHERE environment_id = ? AND is_active = 1
            ORDER BY package_name
        """, env_row.environment_id)
        
        packages = []
        for pkg_row in cursor.fetchall():
            packages.append({
                'name': pkg_row.package_name,
                'version': pkg_row.version,
                'requested_version': pkg_row.requested_version
            })
        
        cursor.close()
        conn.close()
        
        return {
            'environment_id': env_row.environment_id,
            'name': env_row.name,
            'description': env_row.description,
            'python_version': env_row.python_version,
            'status': env_row.status,
            'packages': packages,
            'package_count': len(packages)
        }
        
    except Exception as e:
        logger.error(f"Error getting agent environment for export: {str(e)}")
        return None


def create_environment_from_import(env_data, created_by):
    """
    Create an environment from imported data.
    
    Args:
        env_data: Dictionary containing environment details and packages
        created_by: User ID of the person importing
        
    Returns:
        Tuple of (success: bool, environment_id: str or None, message: str)
    """
    try:
        from agent_environments import AgentEnvironmentManager
        
        tenant_id = os.getenv('API_KEY')
        
        manager = AgentEnvironmentManager(tenant_id)
        
        # Check if environment already exists by name
        environments = manager.list_environments()
        existing = next((e for e in environments if e['name'] == env_data['name']), None)
        
        if existing:
            return True, existing['environment_id'], f"Environment '{env_data['name']}' already exists, using existing"
        
        # Create new environment
        success, env_id, message = manager.create_environment(
            name=env_data['name'],
            description=env_data.get('description', f"Imported environment"),
            created_by=created_by,
            python_version=env_data.get('python_version')
        )
        
        if not success:
            return False, None, message
        
        # Install packages
        packages_installed = 0
        packages_failed = []
        
        for pkg in env_data.get('packages', []):
            try:
                pkg_success, pkg_msg = manager.add_package(
                    env_id,
                    pkg['name'],
                    pkg.get('version'),
                    created_by
                )
                if pkg_success:
                    packages_installed += 1
                else:
                    packages_failed.append({'name': pkg['name'], 'error': pkg_msg})
            except Exception as e:
                packages_failed.append({'name': pkg['name'], 'error': str(e)})
        
        result_message = f"Environment created with {packages_installed} packages"
        if packages_failed:
            result_message += f" ({len(packages_failed)} failed: {', '.join([p['name'] for p in packages_failed])})"
        
        return True, env_id, result_message
        
    except Exception as e:
        logger.error(f"Error creating environment from import: {e}")
        return False, None, str(e)


def assign_environment_to_imported_agent(agent_id, env_id, user_id):
    """
    Assign an environment to an imported agent.
    
    Args:
        agent_id: The ID of the agent
        env_id: The environment_id to assign
        user_id: The ID of the user making the assignment
        
    Returns:
        Tuple of (success: bool, message: str)
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Deactivate any existing assignment for this agent
        cursor.execute("""
            UPDATE AgentEnvironmentAssignments 
            SET is_active = 0 
            WHERE agent_id = ? AND is_active = 1
        """, agent_id)
        
        # Create new assignment
        cursor.execute("""
            INSERT INTO AgentEnvironmentAssignments 
            (agent_id, environment_id, assigned_by)
            VALUES (?, ?, ?)
        """, agent_id, env_id, user_id)
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return True, "Environment assigned to agent"
        
    except Exception as e:
        logger.error(f"Error assigning environment to agent: {e}")
        return False, str(e)


def check_environment_exists_by_name(env_name):
    """
    Check if an environment with the given name already exists.
    
    Args:
        env_name: The name of the environment to check
        
    Returns:
        dict with environment info if exists, None otherwise
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        cursor.execute("""
            SELECT environment_id, name, description, status
            FROM AgentEnvironments
            WHERE name = ? AND is_deleted = 0
        """, env_name)
        
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if row:
            return {
                'environment_id': row.environment_id,
                'name': row.name,
                'description': row.description,
                'status': row.status
            }
        return None
        
    except Exception as e:
        logger.error(f"Error checking environment existence: {e}")
        return None

def highlight_snippet(snippet, search_query):
    """Highlight search terms in the snippet"""
    if not search_query or not snippet:
        return snippet
    
    # Simple implementation: split search terms and wrap matches in highlight spans
    terms = search_query.lower().split()
    
    # Only highlight terms with 3+ characters
    terms = [term for term in terms if len(term) >= 3]
    
    if not terms:
        return snippet
    
    # If string is too long, try to extract a relevant portion
    if len(snippet) > 500:
        # Try to find a portion containing search terms
        relevant_portion = None
        for term in terms:
            match = re.search(f"(?i).{{0,200}}{re.escape(term)}.{{0,200}}", snippet)
            if match:
                relevant_portion = match.group(0)
                break
        
        if relevant_portion:
            snippet = "..." + relevant_portion + "..."
    
    # This is a simple approach - for production, consider using a proper HTML parser
    for term in terms:
        # Escape the term for regex
        escaped_term = re.escape(term)
        # Simple case-insensitive replace
        pattern = f"(?i)({escaped_term})"
        snippet = re.sub(pattern, r'<span class="highlight">\1</span>', snippet)
    
    return snippet

def highlight_document_content(content, search_query):
    """Highlight search terms in document content"""
    # Similar to highlight_snippet but for full document content
    # Don't truncate the content like we do in highlight_snippet
    if not search_query or not content:
        return content
    
    # Simple implementation: split search terms and wrap matches in highlight spans
    terms = search_query.lower().split()
    
    # Only highlight terms with 3+ characters
    terms = [term for term in terms if len(term) >= 3]
    
    if not terms:
        return content
    
    # This is a simple approach - for production, consider using a proper HTML parser
    for term in terms:
        # Escape the term for regex
        escaped_term = re.escape(term)
        # Simple case-insensitive replace
        pattern = f"(?i)({escaped_term})"
        content = re.sub(pattern, r'<mark>\1</mark>', content)
    
    return content

def iter_pages(current_page, total_pages, left_edge=2, right_edge=2, left_current=2, right_current=2):
    """Iterator for page numbers to display in pagination
    
    This mimics the behavior of Flask-SQLAlchemy's Pagination.iter_pages
    """
    last = 0
    for num in range(1, total_pages + 1):
        if (
            num <= left_edge or
            (num > current_page - left_current - 1 and num < current_page + right_current) or
            num > total_pages - right_edge
        ):
            if last + 1 != num:
                yield None
            yield num
            last = num
            

@app.route('/document/serve/<path:filepath>')
def serve_document(filepath):
    # Print debug information
    print('Requested document:', filepath)
    
    # For UNC paths, don't use os.path.abspath which prepends C: drive
    if filepath.startswith('\\\\'):
        print('UNC path found...')
        abs_path = filepath.replace('\\\\', '\\').replace('\\', '/')  # Keep the UNC path as is
    else:
        print('Local path found...')
        # For local paths, use abspath as before
        abs_path = os.path.abspath(filepath)
    
    print('Resolved path:', abs_path)
    
    # Verify the file exists
    if not os.path.exists(abs_path):
        print('Error: File not found:', abs_path)
        #return render_template('error.html', error='File not found')
    
    # Verify it's a regular file (not a directory)
    if not os.path.isfile(abs_path):
        print('Error: Not a file:', abs_path)
        #return render_template('error.html', error='Not a valid file')
    
    try:
        print('Opening document:', abs_path)
        return send_file(abs_path, as_attachment=False)
    except Exception as e:
        print('Error serving file:', str(e))
        return render_template('error.html', error='Error serving file')


@app.route('/document/serve')
def serve_document_2():
    encoded_path = request.args.get('path')
    if not encoded_path:
        return "No path provided", 400

    # Decode the percent-encoded path
    from urllib.parse import unquote
    filepath = unquote(encoded_path)

    print('Requested document:', filepath)

    # UNC path handling
    if filepath.startswith('\\\\'):
        print('UNC path found...')
        #abs_path = filepath.replace('\\\\', '\\').replace('\\', '/')
        abs_path = filepath
    else:
        print('Local path found...')
        abs_path = os.path.abspath(filepath)

    print('Resolved path:', abs_path)

    if not os.path.exists(abs_path):
        print('Path not found, attempting to correct path...')
        if not filepath.startswith('\\\\'):
            abs_path = '\\\\' + filepath
            print('Corrected path:', abs_path)

    if not os.path.exists(abs_path):
        print('Path not found, attempting to correct path...')
        if filepath.startswith('\\\\'):
            abs_path = filepath.replace('\\\\', '\\').replace('\\', '/')
            print('Corrected path:', abs_path)

    if not os.path.exists(abs_path):
        return "File not found", 404

    if not os.path.isfile(abs_path):
        return "Not a valid file", 400

    try:
        return send_file(abs_path, as_attachment=False)
    except Exception as e:
        return f"Error serving file: {e}", 500

    

import socket

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    ip = s.getsockname()[0]
    s.close()
    return ip


# In your Flask/Django/FastAPI app
@app.route('/document/config')
@cross_origin()
def get_document_config():
    """Return document service configuration including full base URL
    
    Returns:
        JSON with port, base URL, and other configuration details
    """
    # Get the protocol from environment or default to http
    protocol = os.getenv('PROTOCOL', 'http')
    
    # Get host from environment
    host = os.getenv('HOST', 'localhost')
    if host == "0.0.0.0":
        host = get_local_ip()
    
    # Calculate document API port by adding 10 to the current port
    try:
        current_port = int(os.getenv('HOST_PORT', '3001'))
        document_port = current_port + 10
    except ValueError:
        # Fallback to default port if HOST_PORT is not a valid integer
        document_port = 3011
    
    # Construct the base URL
    base_url = f"{protocol}://{host}:{document_port}"
    
    # Return configuration as JSON
    return jsonify({
        'port': document_port,
        'baseUrl': base_url,
        'protocol': protocol,
        'host': host
    })


###########################################
# Document Attribution Routes
###########################################

def perform_attribute_search(attribute_filters, document_type, search_query, cursor, max_results):
    """Perform document search based on attributes"""
    try:
        # Build attribute filter conditions
        attribute_conditions = []
        attribute_params = []
        
        for attr_filter in attribute_filters:
            attr_name = attr_filter.get('attribute_name', '')
            operator = attr_filter.get('operator', 'equals')
            value = attr_filter.get('value', '')
            
            if operator == 'equals':
                attribute_conditions.append("(da.attribution_type = ? AND da.attribution_value = ?)")
                attribute_params.extend([attr_name, value])
            elif operator == 'contains':
                attribute_conditions.append("(da.attribution_type = ? AND da.attribution_value LIKE ?)")
                attribute_params.extend([attr_name, f'%{value}%'])
            elif operator == 'starts_with':
                attribute_conditions.append("(da.attribution_type = ? AND da.attribution_value LIKE ?)")
                attribute_params.extend([attr_name, f'{value}%'])
            elif operator == 'ends_with':
                attribute_conditions.append("(da.attribution_type = ? AND da.attribution_value LIKE ?)")
                attribute_params.extend([attr_name, f'%{value}'])
        
        if not attribute_conditions:
            return []
        
        # Build the main query
        base_query = """
            SELECT DISTINCT 
                d.document_id,
                d.filename,
                d.document_type,
                d.page_count,
                d.reference_number,
                d.processed_at,
                d.archived_path,
                dp.page_id,
                dp.page_number,
                SUBSTRING(dp.full_text, 1, 500) as snippet
            FROM Documents d
            JOIN DocumentPages dp ON d.document_id = dp.document_id
            JOIN DocumentAttributions da ON d.document_id = da.document_id
            WHERE d.is_knowledge_document = 0
        """
        
        params = []
        
        # Add document type filter
        if document_type:
            base_query += " AND d.document_type = ?"
            params.append(document_type)
        
        # Add attribute filters
        base_query += f" AND ({' OR '.join(attribute_conditions)})"
        params.extend(attribute_params)
        
        # Add text search if provided
        if search_query:
            base_query += " AND dp.full_text LIKE ?"
            params.append(f'%{search_query}%')
        
        # Add ordering and limit
        base_query += " ORDER BY d.processed_at DESC"
        
        if max_results:
            base_query += f" OFFSET 0 ROWS FETCH NEXT {max_results} ROWS ONLY"
        
        print(f"Attribute search query: {base_query}")
        print(f"Parameters: {params}")
        
        cursor.execute(base_query, params)
        results = cursor.fetchall()
        
        # Format results
        search_results = []
        for row in results:
            result = {
                'document_id': row[0],
                'filename': row[1],
                'document_type': row[2],
                'page_count': row[3],
                'reference_number': row[4],
                'processed_at': row[5].isoformat() if row[5] else None,
                'archived_path': row[6],
                'page_id': row[7],
                'page_number': row[8],
                'snippet': row[9] or '',
                'search_method': 'attribute_based',
                'relevance_score': 1.0,  # NEW: Add a default relevance score
                'matched_via': ['attributes'],  # NEW: Add matched_via for consistency
                'attributes': []  # NEW: Add empty attributes list (will be populated later if needed)
            }
            search_results.append(result)
            
        return search_results
        
    except Exception as e:
        print(f"Error in attribute search: {str(e)}")
        return []

@app.route('/api/documents/<document_id>/attributions', methods=['GET'])
@api_key_or_session_required(min_role=2)
def api_get_document_attributions(document_id):
    """Get attributions for a specific document"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get attributions for the document
        cursor.execute("""
            SELECT 
                attribution_id,
                attribution_type,
                attribution_value,
                attribution_order,
                created_at
            FROM DocumentAttributions 
            WHERE document_id = ?
            ORDER BY attribution_order, created_at
        """, (document_id,))
        
        attributions = []
        for row in cursor.fetchall():
            attributions.append({
                'attribution_id': row.attribution_id,
                'attribution_type': row.attribution_type,
                'attribution_value': row.attribution_value,
                'attribution_order': row.attribution_order,
                'created_at': row.created_at.isoformat() if row.created_at else None
            })
        
        return jsonify(attributions)
        
    except Exception as e:
        logger.error(f"Error getting document attributions: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
        
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()


@app.route('/api/documents/<document_id>/attributions', methods=['POST'])
@api_key_or_session_required(min_role=2)
def api_save_document_attributions(document_id):
    """Save attributions for a specific document"""
    try:
        data = request.json
        attributions = data.get('attributions', [])
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # First, delete existing attributions for this document
        cursor.execute("DELETE FROM DocumentAttributions WHERE document_id = ?", (document_id,))
        
        # Insert new attributions
        for attribution in attributions:
            cursor.execute("""
                INSERT INTO DocumentAttributions 
                (document_id, attribution_type, attribution_value, attribution_order, created_by)
                VALUES (?, ?, ?, ?, ?)
            """, (
                document_id,
                attribution['attribution_type'],
                attribution['attribution_value'],
                attribution.get('attribution_order', 1),
                session.get('username', 'system')
            ))
        
        conn.commit()
        
        return jsonify({
            "status": "success",
            "message": "Attributions saved successfully"
        })
        
    except Exception as e:
        logger.error(f"Error saving document attributions: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
        
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()


# Update the existing document update route to handle attributions
@app.route('/api/documents/<document_id>', methods=['PUT'])
@api_key_or_session_required(min_role=2)
def api_update_document_with_attributions(document_id):
    """Update document details including attributions"""
    try:
        data = request.json
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Update document basic info
        update_fields = []
        params = []
        
        if 'document_type' in data:
            update_fields.append("document_type = ?")
            params.append(data['document_type'])
        
        if 'reference_number' in data:
            update_fields.append("reference_number = ?")
            params.append(data['reference_number'])
        
        if 'customer_id' in data:
            update_fields.append("customer_id = ?")
            params.append(data['customer_id'])
        
        if 'vendor_id' in data:
            update_fields.append("vendor_id = ?")
            params.append(data['vendor_id'])
        
        if 'document_date' in data:
            update_fields.append("document_date = ?")
            params.append(data['document_date'] if data['document_date'] else None)
        
        if update_fields:
            params.append(document_id)
            update_query = f"UPDATE Documents SET {', '.join(update_fields)} WHERE document_id = ?"
            cursor.execute(update_query, params)
        
        # Handle attributions if provided
        if 'attributions' in data:
            # Delete existing attributions
            cursor.execute("DELETE FROM DocumentAttributions WHERE document_id = ?", (document_id,))
            
            # Insert new attributions
            for attribution in data['attributions']:
                cursor.execute("""
                    INSERT INTO DocumentAttributions 
                    (document_id, attribution_type, attribution_value, attribution_order, created_by)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    document_id,
                    attribution['attribution_type'],
                    attribution['attribution_value'],
                    attribution.get('attribution_order', 1),
                    session.get('username', 'system')
                ))
        
        conn.commit()
        
        return jsonify({
            "status": "success",
            "message": "Document updated successfully"
        })
        
    except Exception as e:
        logger.error(f"Error updating document: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
        
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()


@app.route('/api/documents/<document_id>/attribution-summary', methods=['GET'])
@api_key_or_session_required(min_role=2)
def api_get_document_attribution_summary(document_id):
    """Get a brief attribution summary for display in tables/lists"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get attributions summary
        cursor.execute("""
            SELECT 
                attribution_type,
                attribution_value
            FROM DocumentAttributions 
            WHERE document_id = ?
            ORDER BY attribution_order, created_at
        """, (document_id,))
        
        attributions = cursor.fetchall()
        
        if not attributions:
            return jsonify({'summary': None})
        
        # Create a concise summary
        summary_parts = []
        for attr in attributions[:2]:  # Show only first 2 attributions
            if attr.attribution_type == 'author':
                summary_parts.append(f"By: {attr.attribution_value}")
            elif attr.attribution_type == 'source':
                summary_parts.append(f"Source: {attr.attribution_value}")
            elif attr.attribution_type == 'organization':
                summary_parts.append(f"Org: {attr.attribution_value}")
            else:
                summary_parts.append(f"{attr.attribution_type}: {attr.attribution_value}")
        
        if len(attributions) > 2:
            summary_parts.append(f"(+{len(attributions) - 2} more)")
        
        return jsonify({
            'summary': ' | '.join(summary_parts),
            'count': len(attributions)
        })
        
    except Exception as e:
        logger.error(f"Error getting attribution summary: {str(e)}")
        return jsonify({'summary': None, 'count': 0})
        
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()


@app.route('/api/documents/<string:document_id>', methods=['GET'])
@api_key_or_session_required(min_role=2)
def api_get_document(document_id):
    """API endpoint to get paginated documents list"""
    try:
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Build the query
        query = """
            SELECT 
                d.document_id,
                d.filename,
                d.document_type,
                d.page_count,
                d.reference_number,
                d.customer_id,
                d.vendor_id,
                d.document_date,
                d.processed_at,
                d.original_path,
                d.archived_path
            FROM Documents d
            WHERE d.is_knowledge_document = 0
            AND d.document_id = ?
        """
        
        params = [document_id]
        
        # Execute main query
        cursor.execute(query, params)
        
        # Fetch results
        row = cursor.fetchone()
        document = {
                'document_id': row[0],
                'filename': row[1],
                'document_type': row[2],
                'page_count': row[3],
                'reference_number': row[4],
                'customer_id': row[5],
                'vendor_id': row[6],
                'document_date': row[7] if row[7] else None,
                'processed_at': row[8].isoformat() if row[8] else None,
                'original_path': row[9],
                'archived_path': row[10]
            }
        
        conn.close()
        
        return jsonify(document)
        
    except Exception as e:
        print(f"Error fetching document: {str(e)}")
        return jsonify({'error': str(e)}), 500


###########################################
# Document Attribute Search API Routes
###########################################

def build_attribute_filter_query(attribute_filters, document_type=None):
    """
    Build SQL query and parameters for document attribute filtering.
    
    Args:
        attribute_filters: List of attribute filter dictionaries with structure:
            {
                'attribute_name': str,
                'operator': str,  # equals, contains, starts_with, ends_with
                'value': str
            }
        document_type: Optional document type filter
        
    Returns:
        tuple: (sql_query, parameters_list) or (None, None) if no valid filters
    """
    if not attribute_filters:
        return None, None
        
    attribute_conditions = []
    attribute_params = []
    
    for attr_filter in attribute_filters:
        attr_name = attr_filter.get('attribute_name', '').strip()
        operator = attr_filter.get('operator', 'equals')
        value = attr_filter.get('value', '').strip()
        
        if not attr_name or not value:
            continue
        
        if operator == 'equals':
            attribute_conditions.append("(da.attribution_type = ? AND da.attribution_value = ?)")
            attribute_params.extend([attr_name, value])
        elif operator == 'contains':
            attribute_conditions.append("(da.attribution_type = ? AND da.attribution_value LIKE ?)")
            attribute_params.extend([attr_name, f'%{value}%'])
        elif operator == 'starts_with':
            attribute_conditions.append("(da.attribution_type = ? AND da.attribution_value LIKE ?)")
            attribute_params.extend([attr_name, f'{value}%'])
        elif operator == 'ends_with':
            attribute_conditions.append("(da.attribution_type = ? AND da.attribution_value LIKE ?)")
            attribute_params.extend([attr_name, f'%{value}'])
        else:
            print(f"Warning: Unknown attribute operator '{operator}', using 'equals'")
            attribute_conditions.append("(da.attribution_type = ? AND da.attribution_value = ?)")
            attribute_params.extend([attr_name, value])
    
    if not attribute_conditions:
        return None, None
    
    # Build the attribute filter query
    attribute_query = f"""
        SELECT DISTINCT d.document_id
        FROM Documents d
        JOIN DocumentAttributions da ON d.document_id = da.document_id
        WHERE d.is_knowledge_document = 0
        AND ({' OR '.join(attribute_conditions)})
        {f"AND d.document_type = '{document_type}'" if document_type else ""}
    """
    
    return attribute_query, attribute_params

def execute_attribute_filters(cursor, attribute_filters, document_type=None):
    """
    Execute attribute filters and return matching document IDs.
    
    Args:
        cursor: Database cursor
        attribute_filters: List of attribute filter dictionaries
        document_type: Optional document type filter
        
    Returns:
        set: Set of matching document IDs
    """
    if not attribute_filters:
        return set()
    
    # Build the query
    attribute_query, params = build_attribute_filter_query(attribute_filters, document_type)
    
    if not attribute_query:
        return set()
    
    print('Executing attribute filter sql...')
    print(f"SQL: {attribute_query}")
    print(f"Params: {params}")
    
    try:
        cursor.execute(attribute_query, params)
        results = cursor.fetchall()
        
        matching_document_ids = {row[0] for row in results}
        print(f"Attribute filters matched {len(matching_document_ids)} documents")
        return matching_document_ids
        
    except Exception as e:
        print(f"Error executing attribute filters: {str(e)}")
        return set()

@app.route('/api/search-documents-by-attributes', methods=['POST'])
@api_key_or_session_required(min_role=2)
def api_search_documents_by_attributes():
    """Search documents using attribute filters"""
    try:
        data = request.json
        attribute_filters = data.get('attribute_filters', [])
        document_type = data.get('document_type', '')
        search_query = data.get('search_query', '')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Execute attribute filters to get matching document IDs
        matching_document_ids = execute_attribute_filters(cursor, attribute_filters, document_type)
        
        if not matching_document_ids and not search_query:
            return jsonify({
                'results': [],
                'total_results': 0,
                'search_type': 'attribute_search',
                'message': 'No documents match the specified attributes'
            })
        
        # Build the main query to get document details
        base_query = """
            SELECT DISTINCT 
                d.document_id,
                d.filename,
                d.document_type,
                d.page_count,
                d.reference_number,
                d.processed_at,
                d.archived_path,
                dp.page_id,
                dp.page_number,
                SUBSTRING(dp.full_text, 1, 500) as snippet
            FROM Documents d
            JOIN DocumentPages dp ON d.document_id = dp.document_id
            WHERE d.is_knowledge_document = 0
        """
        
        params = []
        conditions = []
        
        # Add document type filter
        if document_type:
            conditions.append("d.document_type = ?")
            params.append(document_type)
        
        # Add text search if provided
        if search_query:
            conditions.append("dp.full_text LIKE ?")
            params.append(f'%{search_query}%')
        
        # Add document ID filter if we have attribute matches
        if matching_document_ids:
            doc_id_placeholders = ','.join(['?' for _ in matching_document_ids])
            conditions.append(f"d.document_id IN ({doc_id_placeholders})")
            params.extend(list(matching_document_ids))
        
        # Apply conditions
        if conditions:
            base_query += " AND " + " AND ".join(conditions)
        
        # Add ordering and execute
        base_query += " ORDER BY d.processed_at DESC"
        
        cursor.execute(base_query, params)
        results = cursor.fetchall()
        
        # Get attributes for each document
        documents = []
        for row in results:
            doc_id = row[0]
            
            # Get attributes for this document
            cursor.execute("""
                SELECT attribution_type, attribution_value
                FROM DocumentAttributions
                WHERE document_id = ?
                ORDER BY attribution_order, created_at
            """, (doc_id,))
            
            attributes = []
            for attr_row in cursor.fetchall():
                attributes.append({
                    'name': attr_row[0],
                    'value': attr_row[1]
                })
            
            doc_data = {
                'document_id': row[0],
                'filename': row[1],
                'document_type': row[2],
                'page_count': row[3],
                'reference_number': row[4],
                'processed_at': row[5].isoformat() if row[5] else None,
                'archived_path': row[6],
                'page_id': row[7],
                'page_number': row[8],
                'snippet': row[9] or '',
                'attributes': attributes,
                'search_method': 'attribute_based',
                'matched_via': ['attributes'],
                'relevance_score': 0.6 + (0.2 if search_query and search_query.lower() in row[9].lower() else 0)
            }
            documents.append(doc_data)
        
        return jsonify({
            'results': documents,
            'total_results': len(documents),
            'search_type': 'attribute_search',
            'filters_applied': attribute_filters,
            'attribute_matches': len(matching_document_ids),
            'text_search_applied': bool(search_query)
        })
        
    except Exception as e:
        logger.error(f"Error in attribute search: {str(e)}")
        return jsonify({
            'error': str(e),
            'results': [],
            'total_results': 0
        }), 500
        
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()


@app.route('/api/search-documents-hybrid', methods=['POST'])
@api_key_or_session_required(min_role=2)
def api_search_documents_hybrid():
    """Hybrid search combining field content and attributes"""
    try:
        data = request.json
        field_filters = data.get('field_filters', [])
        attribute_filters = data.get('attribute_filters', [])
        document_type = data.get('document_type', '')
        search_query = data.get('search_query', '')
        
        # Get results from your EXISTING field search
        field_results = []
        if field_filters or search_query:
            # Call your existing document_search function from DocUtils
            from DocUtils import document_search
            conn_str = get_db_connection_string()
            
            field_search_json = document_search(
                conn_str, 
                document_type=document_type, 
                field_filters=field_filters,
                search_query=search_query,
                include_metadata=False, 
                max_results=50,
                user_question=search_query,
                check_completeness=False
            )
            
            try:
                field_data = json.loads(field_search_json)
                field_results = field_data.get('results', [])
            except json.JSONDecodeError:
                logger.error("Failed to parse field search results")
                field_results = []
        
        # Get results from attribute search (using our new function)
        attribute_results = []
        if attribute_filters:
            # Use the attribute search we just created
            with app.test_request_context(json=data):
                attr_response = api_search_documents_by_attributes()
                if hasattr(attr_response, 'get_json'):
                    attr_data = attr_response.get_json()
                    attribute_results = attr_data.get('results', [])
        
        # Combine and deduplicate results
        all_results = {}
        
        # Add field results
        for result in field_results:
            doc_id = result.get('document_id')
            if doc_id:
                result['matched_via'] = result.get('matched_via', []) + ['field_content']
                all_results[doc_id] = result
        
        # Add attribute results (merge if document already exists)
        for result in attribute_results:
            doc_id = result.get('document_id')
            if doc_id:
                if doc_id in all_results:
                    # Merge results - document matched both criteria
                    all_results[doc_id]['matched_via'] = list(set(
                        all_results[doc_id].get('matched_via', []) + ['attributes']
                    ))
                    all_results[doc_id]['attributes'] = result.get('attributes', [])
                    all_results[doc_id]['relevance_score'] = all_results[doc_id].get('relevance_score', 0.5) + 0.3
                else:
                    result['matched_via'] = ['attributes']
                    all_results[doc_id] = result
        
        # Convert back to list and sort by relevance
        final_results = list(all_results.values())
        final_results.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)
        
        return jsonify({
            'results': final_results,
            'total_results': len(final_results),
            'search_type': 'hybrid',
            'field_filters_applied': field_filters,
            'attribute_filters_applied': attribute_filters,
            'search_summary': {
                'field_matches': len(field_results),
                'attribute_matches': len(attribute_results),
                'dual_matches': len([r for r in final_results if len(r.get('matched_via', [])) > 1]),
                'total_unique_documents': len(final_results)
            }
        })
        
    except Exception as e:
        logger.error(f"Error in hybrid search: {str(e)}")
        return jsonify({
            'error': str(e),
            'results': [],
            'total_results': 0
        }), 500


@app.route('/api/document-attributes/metadata', methods=['GET'])
@api_key_or_session_required(min_role=2)
def api_get_document_attributes_metadata():
    """Get metadata about all available document attributes"""
    try:
        from DocUtils import get_document_attributes_metadata
        document_type = request.args.get('document_type', '')
        
        # Use the reusable function
        result = get_document_attributes_metadata(
            document_type=document_type if document_type else None,
            return_format='json'
        )
        
        # Parse JSON to return as proper JSON response
        import json
        return jsonify(json.loads(result))
            
    except Exception as e:
        logger.error(f"Error getting attribute metadata: {str(e)}")
        return jsonify({
            'error': str(e),
            'attribute_metadata': [],
            'common_combinations': [],
            'total_unique_attributes': 0
        }), 500


#####################
# END DOCUMENT ROUTES
#####################

#--------------------

#####################
# FILE ROUTES
#####################

@app.route('/workflow/file/read', methods=['POST'])
@cross_origin()
def read_file():
    """Read a file and return its contents"""
    try:
        data = request.json
        if not data or 'filePath' not in data:
            return jsonify({
                "status": "error",
                "message": "Missing required parameter: filePath"
            }), 400
            
        file_path = data['filePath']
        
        if not os.path.exists(file_path):
            return jsonify({
                "status": "error",
                "message": f"File not found: {file_path}"
            }), 404
            
        # Determine file encoding - try UTF-8 first
        encoding = data.get('encoding', 'utf-8')
        
        try:
            # First try to read with specified encoding
            with open(file_path, 'r', encoding=encoding) as file:
                content = file.read()
        except UnicodeDecodeError:
            # If that fails, try with fallback encoding
            try:
                with open(file_path, 'r', encoding='latin-1') as file:
                    content = file.read()
            except:
                # Last resort: read as binary and convert to base64
                with open(file_path, 'rb') as file:
                    binary_content = file.read()
                    import base64
                    content = base64.b64encode(binary_content).decode('ascii')
                    return jsonify({
                        "status": "success",
                        "content": content,
                        "is_binary": True,
                        "size": len(binary_content)
                    })
        
        return jsonify({
            "status": "success",
            "content": content,
            "is_binary": False,
            "size": len(content)
        })
        
    except Exception as e:
        logger.error(f"Error reading file: {str(e)}")
        #logger.error(traceback.format_exc())
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500


@app.route('/workflow/file/write', methods=['POST'])
@cross_origin()
def write_file():
    """Write content to a file"""
    try:
        data = request.json
        if not data or 'filePath' not in data or 'content' not in data:
            return jsonify({
                "status": "error",
                "message": "Missing required parameters: filePath and content"
            }), 400
            
        file_path = data['filePath']
        content = data['content']
        overwrite = data.get('overwrite', True)
        
        # Check if file exists and we don't want to overwrite
        if os.path.exists(file_path) and not overwrite:
            return jsonify({
                "status": "error",
                "message": f"File already exists: {file_path}"
            }), 409
            
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        
        # Determine if content is binary (base64 encoded)
        is_binary = data.get('is_binary', False)
        
        if is_binary:
            # Decode base64 content
            import base64
            binary_content = base64.b64decode(content)
            with open(file_path, 'wb') as file:
                file.write(binary_content)
            bytes_written = len(binary_content)
        else:
            # Write text content
            encoding = data.get('encoding', 'utf-8')
            with open(file_path, 'w', encoding=encoding) as file:
                file.write(content)
            bytes_written = len(content.encode(encoding))
        
        return jsonify({
            "status": "success",
            "message": f"File written successfully: {file_path}",
            "bytes_written": bytes_written
        })
        
    except Exception as e:
        logger.error(f"Error writing file: {str(e)}")
        #logger.error(traceback.format_exc())
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500


@app.route('/workflow/file/append', methods=['POST'])
@cross_origin()
def append_file():
    """Append content to a file"""
    try:
        data = request.json
        if not data or 'filePath' not in data or 'content' not in data:
            return jsonify({
                "status": "error",
                "message": "Missing required parameters: filePath and content"
            }), 400
            
        file_path = data['filePath']
        content = data['content']
        
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        
        # Determine if content is binary (base64 encoded)
        is_binary = data.get('is_binary', False)
        
        if is_binary:
            # Decode base64 content
            import base64
            binary_content = base64.b64decode(content)
            with open(file_path, 'ab') as file:
                file.write(binary_content)
            bytes_written = len(binary_content)
        else:
            # Append text content
            encoding = data.get('encoding', 'utf-8')
            with open(file_path, 'a', encoding=encoding) as file:
                file.write(content)
            bytes_written = len(content.encode(encoding))
        
        return jsonify({
            "status": "success",
            "message": f"Content appended successfully to: {file_path}",
            "bytes_written": bytes_written
        })
        
    except Exception as e:
        logger.error(f"Error appending to file: {str(e)}")
        #logger.error(traceback.format_exc())
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500


@app.route('/workflow/file/check', methods=['POST'])
@cross_origin()
def check_file():
    """Check if a file exists"""
    try:
        data = request.json
        if not data or 'filePath' not in data:
            return jsonify({
                "status": "error",
                "message": "Missing required parameter: filePath"
            }), 400
            
        file_path = data['filePath']
        exists = os.path.exists(file_path)
        
        file_info = {}
        if exists:
            try:
                stat_info = os.stat(file_path)
                file_info = {
                    "size": stat_info.st_size,
                    "modified": stat_info.st_mtime,
                    "created": stat_info.st_ctime,
                    "is_directory": os.path.isdir(file_path)
                }
            except:
                # If stat fails, just return exists flag
                pass
        
        return jsonify({
            "status": "success",
            "exists": exists,
            "file_info": file_info
        })
        
    except Exception as e:
        logger.error(f"Error checking file: {str(e)}")
        #logger.error(traceback.format_exc())
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500


@app.route('/workflow/file/delete', methods=['POST'])
@cross_origin()
def delete_file():
    """Delete a file"""
    try:
        data = request.json
        if not data or 'filePath' not in data:
            return jsonify({
                "status": "error",
                "message": "Missing required parameter: filePath"
            }), 400
            
        file_path = data['filePath']
        
        if not os.path.exists(file_path):
            return jsonify({
                "status": "error",
                "message": f"File not found: {file_path}"
            }), 404
            
        # Delete file or directory
        if os.path.isdir(file_path):
            if data.get('recursive', False):
                import shutil
                shutil.rmtree(file_path)
            else:
                os.rmdir(file_path)
        else:
            os.remove(file_path)
        
        return jsonify({
            "status": "success",
            "message": f"File deleted successfully: {file_path}"
        })
        
    except OSError as e:
        # Handle specific OS errors
        if e.errno == 39:  # Directory not empty
            return jsonify({
                "status": "error",
                "message": f"Directory not empty: {file_path}. Set recursive=true to delete."
            }), 400
        else:
            logger.error(f"Error deleting file: {str(e)}")
            #logger.error(traceback.format_exc())
            return jsonify({
                "status": "error",
                "message": f"Server error: {str(e)}"
            }), 500
            
    except Exception as e:
        logger.error(f"Error deleting file: {str(e)}")
        #logger.error(traceback.format_exc())
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500



# Add these functions to your Flask app
@app.route('/folder/list_files', methods=['POST'])
@cross_origin()
def list_folder_files_route():
    """List files in a folder with various selection methods"""
    try:
        data = request.json
        if not data or 'folderPath' not in data:
            return jsonify({
                "status": "error",
                "message": "Missing required parameter: folderPath"
            }), 400
            
        folder_path = data['folderPath']
        selection_mode = data.get('selectionMode', 'first')
        file_pattern = data.get('filePattern', '*.*')
        
        # Check if folder exists
        if not os.path.isdir(folder_path):
            return jsonify({
                "status": "error",
                "message": f"Directory not found: {folder_path}"
            }), 404
        
        # Get files matching the pattern
        matching_files = []
        
        if '*' in file_pattern:
            # Use glob for wildcard patterns
            pattern_path = os.path.join(folder_path, file_pattern)
            matching_files = [f for f in glob.glob(pattern_path) if os.path.isfile(f)]
        else:
            # If no wildcards, just check if the exact file exists
            file_path = os.path.join(folder_path, file_pattern)
            if os.path.isfile(file_path):
                matching_files = [file_path]
        
        # If no files found, return empty result
        if not matching_files:
            return jsonify({
                "status": "success",
                "message": "No files found matching the criteria",
                "selectedFile": None,
                "allFiles": []
            })
        
        # Sort or select files based on the selection mode
        selected_file = None
        
        if selection_mode == 'first':
            # First file in alphabetical order
            matching_files.sort()
            selected_file = matching_files[0]
            
        elif selection_mode == 'latest':
            # Latest modified file
            matching_files.sort(key=os.path.getmtime, reverse=True)
            selected_file = matching_files[0]
            
        elif selection_mode == 'pattern':
            # First file matching pattern (already filtered above)
            matching_files.sort()
            selected_file = matching_files[0] if matching_files else None
            
        elif selection_mode == 'largest':
            # Largest file by size
            matching_files.sort(key=os.path.getsize, reverse=True)
            selected_file = matching_files[0]
            
        elif selection_mode == 'smallest':
            # Smallest file by size
            matching_files.sort(key=os.path.getsize)
            selected_file = matching_files[0]
            
        elif selection_mode == 'random':
            # Random file
            selected_file = random.choice(matching_files)
        
        # Return the selected file and all matching files
        return jsonify({
            "status": "success",
            "selectedFile": selected_file,
            "allFiles": matching_files,
            "totalFiles": len(matching_files)
        })
        
    except Exception as e:
        logger.error(f"Error listing folder files: {str(e)}")
        #logger.error(traceback.format_exc())
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500

# Optional: Add a /folder/info endpoint for getting folder metadata
@app.route('/folder/info', methods=['POST'])
@cross_origin()
def get_folder_info_route():
    """Get information about a folder"""
    try:
        data = request.json
        if not data or 'folderPath' not in data:
            return jsonify({
                "status": "error",
                "message": "Missing required parameter: folderPath"
            }), 400
            
        folder_path = data['folderPath']
        
        # Check if folder exists
        if not os.path.isdir(folder_path):
            return jsonify({
                "status": "error",
                "message": f"Directory not found: {folder_path}"
            }), 404
        
        # Get folder stats
        folder_stat = os.stat(folder_path)
        creation_time = datetime.fromtimestamp(folder_stat.st_ctime).isoformat()
        modification_time = datetime.fromtimestamp(folder_stat.st_mtime).isoformat()
        
        # Count files and sub-folders
        files = []
        folders = []
        
        try:
            with os.scandir(folder_path) as entries:
                for entry in entries:
                    if entry.is_file():
                        file_stat = entry.stat()
                        files.append({
                            "name": entry.name,
                            "path": entry.path,
                            "size": file_stat.st_size,
                            "modified": datetime.fromtimestamp(file_stat.st_mtime).isoformat()
                        })
                    elif entry.is_dir():
                        folders.append({
                            "name": entry.name,
                            "path": entry.path
                        })
        except PermissionError:
            return jsonify({
                "status": "error",
                "message": f"Permission denied accessing folder: {folder_path}"
            }), 403
        
        return jsonify({
            "status": "success",
            "folderPath": folder_path,
            "created": creation_time,
            "modified": modification_time,
            "fileCount": len(files),
            "folderCount": len(folders),
            "files": files[:20],  # Limit to first 20 files to avoid large responses
            "folders": folders[:20]
        })
        
    except Exception as e:
        logger.error(f"Error getting folder info: {str(e)}")
        #logger.error(traceback.format_exc())
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500
#####################
# END FILE ROUTES
#####################

#####################
# WORKFLOW ROUTES
#####################
from CommonUtils import get_document_api_base_url, generate_connection_string
# Add these imports
from workflow_execution import WorkflowExecutionEngine
import datetime
import json

# Workflow engine - only initialize for local mode
workflow_engine = None
if not USE_WORKFLOW_EXECUTOR_SERVICE:
    workflow_engine = WorkflowExecutionEngine(get_db_connection_string())

# API endpoints for workflow execution and monitoring
@app.route('/api/workflow/executions', methods=['GET'])
@cross_origin()
@api_key_or_session_required()
def get_workflow_executions():
    """Get list of workflow executions with optional filters"""
    try:
        # Parse query parameters
        status = request.args.get('status')
        workflow_id = request.args.get('workflow_id')
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))
        
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # First, get the tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Build query with filters
        query = "SELECT * FROM WorkflowExecutions WHERE 1=1"
        params = []
        
        if status:
            query += " AND status = ?"
            params.append(status)
        
        if workflow_id:
            query += " AND workflow_id = ?"
            params.append(workflow_id)
        
        # Add sorting and pagination
        query += " ORDER BY started_at DESC OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"
        params.append(offset)
        params.append(limit)
        
        # Execute query
        cursor.execute(query, *params)
        
        # Process results
        executions = []
        for row in cursor.fetchall():
            # Convert row to dictionary
            execution = {}
            for i, column in enumerate(cursor.description):
                value = row[i]
                # Convert datetime to string
                if isinstance(value, datetime.datetime):
                    value = value.isoformat()
                execution[column[0]] = value
            
            executions.append(execution)
        
        cursor.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "count": len(executions),
            "executions": executions
        })
        
    except Exception as e:
        logger.error(f"Error retrieving workflow executions: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error retrieving workflow executions: {str(e)}"
        }), 500

@app.route('/api/workflow/executions/<execution_id>', methods=['GET'])
@cross_origin()
def get_workflow_execution_details(execution_id):
    """Get detailed information about a specific workflow execution"""
    try:
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # First, get the tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get execution details
        cursor.execute("SELECT * FROM WorkflowExecutions WHERE execution_id = ?", execution_id)
        row = cursor.fetchone()
        
        if not row:
            return jsonify({
                "status": "error",
                "message": f"Execution {execution_id} not found"
            }), 404
        
        # Convert row to dictionary
        execution = {}
        for i, column in enumerate(cursor.description):
            value = row[i]
            # Convert datetime to string
            if isinstance(value, datetime.datetime):
                value = value.isoformat()
            execution[column[0]] = value
        
        # Get current step information
        cursor.execute("""
            SELECT TOP 1 *
            FROM StepExecutions
            WHERE execution_id = ? AND status IN ('Running', 'Paused')
            ORDER BY started_at DESC
        """, execution_id)
        
        current_step = None
        row = cursor.fetchone()
        if row:
            current_step = {}
            for i, column in enumerate(cursor.description):
                value = row[i]
                # Convert datetime to string
                if isinstance(value, datetime.datetime):
                    value = value.isoformat()
                current_step[column[0]] = value
            
            # Check if this step is waiting for approval
            if current_step['status'] == 'Paused':
                cursor.execute("""
                    SELECT TOP 1 1
                    FROM ApprovalRequests
                    WHERE step_execution_id = ? AND status = 'Pending'
                """, current_step['step_execution_id'])
                
                current_step['waiting_for_approval'] = cursor.fetchone() is not None
        
        execution['current_step'] = current_step
        
        cursor.close()
        conn.close()
        
        return jsonify(execution)
        
    except Exception as e:
        logger.error(f"Error retrieving execution details: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error retrieving execution details: {str(e)}"
        }), 500

@app.route('/api/workflow/executions/<execution_id>/steps', methods=['GET'])
@cross_origin()
def get_workflow_execution_steps(execution_id):
    """Get steps for a specific workflow execution"""
    try:
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # First, get the tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Verify execution exists
        cursor.execute("SELECT 1 FROM WorkflowExecutions WHERE execution_id = ?", execution_id)
        if not cursor.fetchone():
            return jsonify({
                "status": "error",
                "message": f"Execution {execution_id} not found"
            }), 404
        
        # Get all steps
        cursor.execute("""
            SELECT *
            FROM StepExecutions
            WHERE execution_id = ?
            ORDER BY started_at ASC
        """, execution_id)
        
        steps = []
        for row in cursor.fetchall():
            # Convert row to dictionary
            step = {}
            for i, column in enumerate(cursor.description):
                value = row[i]
                # Convert datetime to string
                if isinstance(value, datetime.datetime):
                    value = value.isoformat()
                step[column[0]] = value
            
            steps.append(step)
        
        cursor.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "count": len(steps),
            "steps": steps
        })
        
    except Exception as e:
        logger.error(f"Error retrieving execution steps: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error retrieving execution steps: {str(e)}"
        }), 500

##################################
##### For Workflow Dashboard #####
##################################
@app.route('/api/workflow/executions/<execution_id>/variables', methods=['GET'])
@cross_origin()
def get_workflow_execution_variables(execution_id):
    """Get variables for a specific workflow execution"""
    try:
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # First, get the tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Verify execution exists
        cursor.execute("SELECT 1 FROM WorkflowExecutions WHERE execution_id = ?", execution_id)
        if not cursor.fetchone():
            return jsonify({
                "status": "error",
                "message": f"Execution {execution_id} not found"
            }), 404
        
        # Get all variables
        cursor.execute("""
            SELECT variable_name, variable_type, variable_value, last_updated
            FROM WorkflowVariables
            WHERE execution_id = ?
            ORDER BY last_updated DESC
        """, execution_id)
        
        variables = {}
        for row in cursor.fetchall():
            variable_name, variable_type, variable_value, last_updated = row
            
            # Try to parse JSON value
            try:
                value = json.loads(variable_value)
            except:
                value = variable_value
                
            variables[variable_name] = {
                "type": variable_type,
                "value": value,
                "updated_at": last_updated.isoformat() if last_updated else None
            }
        
        cursor.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "variables": variables
        })
        
    except Exception as e:
        logger.error(f"Error retrieving execution variables: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error retrieving execution variables: {str(e)}"
        }), 500



@app.route('/api/workflow/executions/<execution_id>/logs', methods=['GET'])
@cross_origin()
def get_workflow_execution_logs(execution_id):
    """Get logs for a specific workflow execution"""
    try:
        # Parse query parameters
        level = request.args.get('level')
        search = request.args.get('search')
        limit = int(request.args.get('limit', 1000))
        
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # First, get the tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Verify execution exists
        cursor.execute("SELECT 1 FROM WorkflowExecutions WHERE execution_id = ?", execution_id)
        if not cursor.fetchone():
            return jsonify({
                "status": "error",
                "message": f"Execution {execution_id} not found"
            }), 404
        
        # Build query with filters
        query = "SELECT * FROM ExecutionLogs WHERE execution_id = ?"
        params = [execution_id]
        
        if level:
            query += " AND log_level = ?"
            params.append(level)
        
        if search:
            query += " AND message LIKE ?"
            params.append(f"%{search}%")
        
        # Add sorting and limit
        query += " ORDER BY timestamp DESC"
        
        if limit:
            query += " OFFSET 0 ROWS FETCH NEXT ? ROWS ONLY"
            params.append(limit)
        
        # Execute query
        cursor.execute(query, *params)
        
        logs = []
        for row in cursor.fetchall():
            # Convert row to dictionary
            log = {}
            for i, column in enumerate(cursor.description):
                value = row[i]
                # Convert datetime to string
                if isinstance(value, datetime.datetime):
                    value = value.isoformat()
                log[column[0]] = value
            
            logs.append(log)
        
        cursor.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "count": len(logs),
            "logs": logs
        })
        
    except Exception as e:
        logger.error(f"Error retrieving execution logs: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error retrieving execution logs: {str(e)}"
        }), 500


@app.route('/api/workflow/approvals', methods=['GET'])
@cross_origin()
def get_approval_requests():
    """Get list of approval requests that need attention"""
    try:
        # Parse query parameters
        status = request.args.get('status', 'pending')
        assignee = request.args.get('assignee')
        
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # First, get the tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Build query with filters
        query = """
            SELECT ar.*, se.node_name, se.node_type, 
                   we.workflow_name, we.execution_id, we.started_at as execution_started_at
            FROM ApprovalRequests ar
            JOIN StepExecutions se ON ar.step_execution_id = se.step_execution_id
            JOIN WorkflowExecutions we ON se.execution_id = we.execution_id
            WHERE 1=1
        """
        params = []
        
        if status:
            query += " AND ar.status = ?"
            params.append(status)
        
        if assignee:
            query += " AND (ar.assigned_to = ? OR ar.assigned_to IS NULL)"
            params.append(assignee)
        
        # Add sorting
        query += " ORDER BY ar.requested_at DESC"
        
        # Execute query
        cursor.execute(query, *params)
        
        approvals = []
        for row in cursor.fetchall():
            # Convert row to dictionary
            approval = {}
            for i, column in enumerate(cursor.description):
                value = row[i]
                # Convert datetime to string
                if isinstance(value, datetime.datetime):
                    value = value.isoformat()
                approval[column[0]] = value
            
            approvals.append(approval)
        
        cursor.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "count": len(approvals),
            "approvals": approvals
        })
        
    except Exception as e:
        logger.error(f"Error retrieving approval requests: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error retrieving approval requests: {str(e)}"
        }), 500

@app.route('/api/workflow/approvals/<request_id>', methods=['GET'])
@cross_origin()
def get_approval_request(request_id):
    """Get details of a specific approval request"""
    try:
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # First, get the tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get approval request details
        cursor.execute("""
            SELECT ar.*, se.node_name, se.node_type, 
                   we.workflow_name, we.execution_id, we.started_at as execution_started_at
            FROM ApprovalRequests ar
            JOIN StepExecutions se ON ar.step_execution_id = se.step_execution_id
            JOIN WorkflowExecutions we ON se.execution_id = we.execution_id
            WHERE ar.request_id = ?
        """, request_id)
        
        row = cursor.fetchone()
        if not row:
            return jsonify({
                "status": "error",
                "message": f"Approval request {request_id} not found"
            }), 404
        
        # Convert row to dictionary
        approval = {}
        for i, column in enumerate(cursor.description):
            value = row[i]
            # Convert datetime to string
            if isinstance(value, datetime.datetime):
                value = value.isoformat()
            approval[column[0]] = value
        
        cursor.close()
        conn.close()
        
        return jsonify(approval)
        
    except Exception as e:
        logger.error(f"Error retrieving approval request: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error retrieving approval request: {str(e)}"
        }), 500

@app.route('/api/workflow/approvals/<request_id>', methods=['POST'])
@cross_origin()
def process_approval_request(request_id):
    """Approve or reject a workflow approval request"""
    try:
        data = request.json
        if not data:
            return jsonify({
                "status": "error",
                "message": "Missing request data"
            }), 400
        
        status = data.get('status')
        if not status or status not in ['approved', 'rejected']:
            return jsonify({
                "status": "error",
                "message": "Invalid status. Must be 'approved' or 'rejected'"
            }), 400
        
        comments = data.get('comments', '')
        responded_by = data.get('user', 'system')  # Use authenticated user in real app
        
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # First, get the tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get approval request details to verify it's pending
        cursor.execute("""
            SELECT ar.step_execution_id, se.execution_id
            FROM ApprovalRequests ar
            JOIN StepExecutions se ON ar.step_execution_id = se.step_execution_id
            WHERE ar.request_id = ? AND ar.status = 'Pending'
        """, request_id)
        
        row = cursor.fetchone()
        if not row:
            return jsonify({
                "status": "error",
                "message": f"Approval request {request_id} not found or already processed"
            }), 404
        
        step_execution_id, execution_id = row
        
        # Update the approval request
        cursor.execute("""
            UPDATE ApprovalRequests
            SET status = ?, response_at = getutcdate(), responded_by = ?, comments = ?
            WHERE request_id = ?
        """, status.capitalize(), responded_by, comments, request_id)
        
        conn.commit()
        
        # Notify the workflow execution engine of the approval response
        # This will cause the workflow to continue execution
        if status == 'approved':
            message = f"Approval request {request_id} approved by {responded_by}"
        else:
            message = f"Approval request {request_id} rejected by {responded_by}"

        if USE_WORKFLOW_EXECUTOR_SERVICE:
            try:
                workflow_client.log_event(
                    execution_id=execution_id,
                    message=message,
                    level="info",
                    details={"request_id": request_id, "status": status, "comments": comments}
                )
            except WorkflowServiceError as e:
                logger.warning(f"Could not log to workflow service: {e.message}")
        else:
            # Log the approval response
            workflow_engine.log_execution(
                execution_id, None, "info", message,
                {"request_id": request_id, "status": status, "comments": comments}
            )
        
        cursor.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "message": f"Approval request processed ({status})"
        })
        
    except Exception as e:
        logger.error(f"Error processing approval request: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error processing approval request: {str(e)}"
        }), 500

@app.route('/api/workflow/executions/<execution_id>/pause-legacy', methods=['POST'])
@cross_origin()
def pause_workflow_execution_legacy(execution_id):
    """Manually pause a workflow execution"""
    try:
        result = workflow_engine.pause_workflow(execution_id)
        
        if result:
            return jsonify({
                "status": "success",
                "message": "Workflow execution paused successfully"
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Failed to pause workflow execution. Check if it exists and is running."
            }), 400
            
    except Exception as e:
        logger.error(f"Error pausing workflow execution: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error pausing workflow execution: {str(e)}"
        }), 500

@app.route('/api/workflow/executions/<execution_id>/resume-legacy', methods=['POST'])
@cross_origin()
def resume_workflow_execution_legacy(execution_id):
    """Resume a paused workflow execution"""
    try:
        result = workflow_engine.resume_workflow(execution_id)
        
        if result:
            return jsonify({
                "status": "success",
                "message": "Workflow execution resumed successfully"
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Failed to resume workflow execution. Check if it exists and is paused."
            }), 400
            
    except Exception as e:
        logger.error(f"Error resuming workflow execution: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error resuming workflow execution: {str(e)}"
        }), 500

@app.route('/api/workflow/executions/<execution_id>/cancel-legacy', methods=['POST'])
@cross_origin()
def cancel_workflow_execution_legacy(execution_id):
    """Cancel a workflow execution"""
    try:
        result = workflow_engine.cancel_workflow(execution_id)
        
        if result:
            return jsonify({
                "status": "success",
                "message": "Workflow execution cancelled successfully"
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Failed to cancel workflow execution. Check if it exists and is active."
            }), 400
            
    except Exception as e:
        logger.error(f"Error cancelling workflow execution: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error cancelling workflow execution: {str(e)}"
        }), 500



@app.route('/api/workflow/executions/<execution_id>/pause', methods=['POST'])
@cross_origin()
def pause_workflow_execution(execution_id):
    """Pause a workflow execution"""
    try:
        if USE_WORKFLOW_EXECUTOR_SERVICE:
            result = workflow_client.pause_workflow(execution_id)
            return jsonify(result)
        else:
            result = workflow_engine.pause_workflow(execution_id)
            if result:
                return jsonify({"status": "success", "message": "Workflow paused"})
            return jsonify({"status": "error", "message": "Failed to pause"}), 400
    except WorkflowServiceError as e:
        return jsonify({"status": "error", "message": e.message}), e.status_code or 500
    except Exception as e:
        logger.error(f"Error pausing workflow: {str(e)}")
        capture_exception(e)  # telemetry
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/workflow/executions/<execution_id>/resume', methods=['POST'])
@cross_origin()
def resume_workflow_execution(execution_id):
    """Resume a paused workflow execution"""
    try:
        if USE_WORKFLOW_EXECUTOR_SERVICE:
            result = workflow_client.resume_workflow(execution_id)
            return jsonify(result)
        else:
            result = workflow_engine.resume_workflow(execution_id)
            if result:
                return jsonify({"status": "success", "message": "Workflow resumed"})
            return jsonify({"status": "error", "message": "Failed to resume"}), 400
    except WorkflowServiceError as e:
        return jsonify({"status": "error", "message": e.message}), e.status_code or 500
    except Exception as e:
        logger.error(f"Error resuming workflow: {str(e)}")
        capture_exception(e)  # telemetry
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/workflow/executions/<execution_id>/cancel', methods=['POST'])
@cross_origin()
def cancel_workflow_execution(execution_id):
    """Cancel a workflow execution"""
    try:
        if USE_WORKFLOW_EXECUTOR_SERVICE:
            result = workflow_client.cancel_workflow(execution_id)
            return jsonify(result)
        else:
            result = workflow_engine.cancel_workflow(execution_id)
            if result:
                return jsonify({"status": "success", "message": "Workflow cancelled"})
            return jsonify({"status": "error", "message": "Failed to cancel"}), 400
    except WorkflowServiceError as e:
        return jsonify({"status": "error", "message": e.message}), e.status_code or 500
    except Exception as e:
        logger.error(f"Error cancelling workflow: {str(e)}")
        capture_exception(e)  # telemetry
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/workflow/stats/counts', methods=['GET'])
@cross_origin()
def get_workflow_stats():
    """Get workflow execution statistics"""
    try:
        # Calculate the server's timezone offset from UTC
        local_now = datetime.datetime.now()
        utc_now = datetime.datetime.utcnow()
        
        # Calculate offset in minutes
        offset_delta = local_now - utc_now
        timezone_offset_minutes = int(offset_delta.total_seconds() / 60)
        
        logger.debug(f'Server timezone offset from UTC: {timezone_offset_minutes} minutes')
        logger.debug(f'UTC now: {utc_now}')
        logger.debug(f'Server local now: {local_now}')

        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # First, get the tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        # Initialize all expected fields with 0
        status_counts = {
            'completed': 0,
            'failed': 0,
            'paused': 0,
            'running': 0,
            'active': 0,
            'completed_today': 0,
            'failed_today': 0,
            'pending_approvals': 0,
            'total_workflows': 0
        }

        # Get counts of workflows by status
        cursor.execute("""
            SELECT status, COUNT(*) as count
            FROM WorkflowExecutions
            GROUP BY status
        """)
        
        for row in cursor.fetchall():
            status_counts[row[0].lower()] = row[1]

        # Get start of "today" in server's local time
        server_today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Convert to UTC by subtracting the offset
        utc_today_start = server_today_start - datetime.timedelta(minutes=timezone_offset_minutes)
        
        logger.debug(f'Server today start (local): {server_today_start}')
        logger.debug(f'UTC today start (for query): {utc_today_start}')
        
        # Get count of today's completed and failed workflows
        #today = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today = utc_today_start
        
        print('Today:', utc_today_start)

        cursor.execute("""
            SELECT status, COUNT(*) as count
            FROM WorkflowExecutions
            WHERE completed_at >= ? AND status IN ('Completed', 'Failed')
            GROUP BY status
        """, today)
        
        for row in cursor.fetchall():
            status_key = f"{row[0].lower()}_today"
            status_counts[status_key] = row[1]
        
        # Get count of pending approvals
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM ApprovalRequests
            WHERE status = 'Pending'
        """)
        
        status_counts['pending_approvals'] = cursor.fetchone()[0]

        # Get count of active workflows
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM Workflows
            WHERE is_active = 1
        """)
        
        status_counts['total_workflows'] = cursor.fetchone()[0]

        # Get count of active workflows
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM WorkflowExecutions
            WHERE status = 'Running'
        """)
        
        status_counts['active'] = cursor.fetchone()[0]

        print('total_workflows:', status_counts)
        
        cursor.close()
        conn.close()
        
        return jsonify(status_counts)
        
    except Exception as e:
        logger.error(f"Error retrieving workflow statistics: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error retrieving workflow statistics: {str(e)}"
        }), 500

@app.route('/api/workflow/run-legacy', methods=['POST'])
@cross_origin()
@api_key_or_session_required()
def run_workflow_legacy():
    """Run a workflow with the execution engine"""
    try:
        set_user_request_id(module_name='workflow_executor')
        
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
        
        # Get workflow definition
        conn = get_db_connection()
        cursor = conn.cursor()

        # First, get the tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        cursor.execute("SELECT workflow_data FROM Workflows WHERE id = ?", int(workflow_id))
        row = cursor.fetchone()
        print('Row:', row)
        
        if not row:
            return jsonify({
                "status": "error",
                "message": f"Workflow with ID {workflow_id} not found"
            }), 404
        
        workflow_data = json.loads(row[0])
        
        cursor.close()
        conn.close()
        
        # Get initiator
        initiator = data.get('initiator', 'api')
        
        # Start the workflow execution
        print('Starting workflow...')
        execution_id = workflow_engine.start_workflow(
            workflow_id, workflow_data, initiator)
        
        return jsonify({
            "status": "success",
            "message": "Workflow execution started",
            "execution_id": execution_id
        })
        
    except Exception as e:
        logger.error(f"Error starting workflow execution: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error starting workflow execution: {str(e)}"
        }), 500


@app.route('/api/workflow/run', methods=['POST'])
@cross_origin()
@api_key_or_session_required()
def run_workflow():
    """Run a workflow - proxied to workflow executor service"""
    try:
        set_user_request_id(module_name='workflow_executor')
        
        data = request.json
        if not data:
            return jsonify({"status": "error", "message": "Missing request data"}), 400
        
        workflow_id = data.get('workflow_id')
        if not workflow_id:
            return jsonify({"status": "error", "message": "workflow_id is required"}), 400
        
        initiator = data.get('initiator', 'api')
        
        if USE_WORKFLOW_EXECUTOR_SERVICE:
            try:
                result = workflow_client.start_workflow(
                    workflow_id=int(workflow_id),
                    initiator=initiator
                )
                return jsonify({
                    "status": "success",
                    "message": "Workflow execution started",
                    "execution_id": result.get('execution_id')
                })
            except WorkflowServiceError as e:
                logger.error(f"Workflow service error: {e.message}")
                return jsonify({"status": "error", "message": e.message}), e.status_code or 500
        else:
            # Fallback to local execution (requires workflow_engine to be initialized)
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            cursor.execute("SELECT workflow_data FROM Workflows WHERE id = ?", int(workflow_id))
            row = cursor.fetchone()
            
            if not row:
                cursor.close()
                conn.close()
                return jsonify({"status": "error", "message": f"Workflow {workflow_id} not found"}), 404
            
            workflow_data = json.loads(row[0])
            cursor.close()
            conn.close()
            
            execution_id = workflow_engine.start_workflow(workflow_id, workflow_data, initiator)
            return jsonify({
                "status": "success",
                "message": "Workflow execution started",
                "execution_id": execution_id
            })
        
    except Exception as e:
        logger.error(f"Error starting workflow: {str(e)}")
        capture_exception(e)  # telemetry
        return jsonify({"status": "error", "message": str(e)}), 500

# Add route for the monitoring dashboard UI
@app.route('/monitoring', methods=['GET'])
@cross_origin()
@developer_required()
@tier_allows_feature('workflows')
def monitoring_dashboard():
    """Render the monitoring dashboard UI"""
    return render_template('workflow_monitor.html')

# Add route for the monitoring dashboard UI
@app.route('/agent_communication', methods=['GET'])
@cross_origin()
@login_required
def agent_communication():
    """Render the agent communication dashboard UI"""
    return render_template('agent_communication.html')


@app.route('/api/workflow/steps/<step_execution_id>', methods=['GET'])
@cross_origin()
def get_step_execution_details(step_execution_id):
    """Get detailed information about a specific workflow step execution"""
    try:
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # First, get the tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get step details
        cursor.execute("""
            SELECT se.*, we.workflow_name
            FROM StepExecutions se
            JOIN WorkflowExecutions we ON se.execution_id = we.execution_id
            WHERE se.step_execution_id = ?
        """, step_execution_id)
        
        row = cursor.fetchone()
        if not row:
            return jsonify({
                "status": "error",
                "message": f"Step execution {step_execution_id} not found"
            }), 404
        
        # Convert row to dictionary
        columns = [column[0] for column in cursor.description]
        step = dict(zip(columns, row))
        
        # Convert datetime to string
        for key, value in step.items():
            if isinstance(value, datetime.datetime):
                step[key] = value.isoformat()
        
        # Check if there's an approval request for this step
        cursor.execute("""
            SELECT request_id, title, description, status, requested_at, 
                   response_at, assigned_to, responded_by, comments
            FROM ApprovalRequests
            WHERE step_execution_id = ?
        """, step_execution_id)
        
        approval_row = cursor.fetchone()
        if approval_row:
            approval_columns = [column[0] for column in cursor.description]
            approval = dict(zip(approval_columns, approval_row))
            
            # Convert datetime to string
            for key, value in approval.items():
                if isinstance(value, datetime.datetime):
                    approval[key] = value.isoformat()
                    
            step['approval_request'] = approval
        
        cursor.close()
        conn.close()
        
        return jsonify(step)
        
    except Exception as e:
        logger.error(f"Error retrieving step details: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error retrieving step details: {str(e)}"
        }), 500

@app.route('/api/workflow/analytics', methods=['GET'])
@cross_origin()
def get_workflow_analytics():
    """Get analytics data for workflows executions"""
    try:
        from dateutil import parser
        logger.info(f'Workflow Analytics server timezone conversion...')

        # Calculate the server's timezone offset from UTC
        local_now = datetime.datetime.now()
        utc_now = datetime.datetime.utcnow()

        # Calculate offset in minutes
        offset_delta = local_now - utc_now
        timezone_offset_minutes = int(offset_delta.total_seconds() / 60)

        logger.info(f'Server timezone offset from UTC: {timezone_offset_minutes} minutes')

        # Parse query parameters (they come as strings from the request)
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')

        logger.info(f'Start Date Arg: {start_date_str}')
        logger.info(f'End Date Arg: {end_date_str}')

        # Convert strings to datetime objects and adjust for timezone
        if start_date_str:
            # Parse the string to datetime
            start_date = parser.parse(start_date_str)
            # Convert from server local time to UTC
            start_date_utc = start_date - datetime.timedelta(minutes=timezone_offset_minutes)
            start_date = start_date_utc
            logger.info(f'Start date (local): {start_date_str}, Start date (UTC): {start_date}')
        else:
            start_date = None

        if end_date_str:
            # Parse the string to datetime
            end_date = parser.parse(end_date_str)
            # Convert from server local time to UTC
            end_date_utc = end_date - datetime.timedelta(minutes=timezone_offset_minutes)
            end_date = end_date_utc
            logger.info(f'End date (local): {end_date_str}, End date (UTC): {end_date}')
        else:
            end_date = None

        workflow_id = request.args.get('workflow_id')
        
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # First, get the tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Build the execution trends data
        query_params = []
        date_filter = ""
        workflow_filter = ""
        
        if start_date:
            date_filter += " AND started_at >= ?"
            query_params.append(start_date)
        
        if end_date:
            date_filter += " AND started_at <= DATEADD(day, 1, ?)"  # Include the entire end date
            query_params.append(end_date)
            
        if workflow_id and workflow_id != 'all':
            workflow_filter = " AND workflow_id = ?"
            query_params.append(workflow_id)
        
        # Get daily execution counts by status
        cursor.execute(f"""
            SELECT 
                CAST(started_at AS DATE) as execution_date,
                status,
                COUNT(*) as count
            FROM WorkflowExecutions
            WHERE 1=1 {date_filter} {workflow_filter}
            GROUP BY CAST(started_at AS DATE), status
            ORDER BY CAST(started_at AS DATE)
        """, *query_params)
        
        # Process the results for the execution trends chart
        execution_dates = set()
        status_counts = {}
        
        for row in cursor.fetchall():
            date_val = row[0]
            if isinstance(date_val, str):
                date_str = date_val
            else:
                date_str = date_val.strftime('%Y-%m-%d')

            status = row[1].lower()
            count = row[2]
            
            execution_dates.add(date_str)
            
            if status not in status_counts:
                status_counts[status] = {}
            
            status_counts[status][date_str] = count
        
        # Sort dates for proper display
        sorted_dates = sorted(list(execution_dates))
        
        # Create datasets for the execution trend chart
        execution_trend_datasets = []
        status_colors = {
            'completed': {'bg': 'rgba(40, 167, 69, 0.2)', 'border': '#28a745'},
            'failed': {'bg': 'rgba(220, 53, 69, 0.2)', 'border': '#dc3545'},
            'running': {'bg': 'rgba(0, 123, 255, 0.2)', 'border': '#007bff'},
            'paused': {'bg': 'rgba(255, 193, 7, 0.2)', 'border': '#ffc107'},
            'cancelled': {'bg': 'rgba(108, 117, 125, 0.2)', 'border': '#6c757d'}
        }
        
        for status, dates in status_counts.items():
            if status in status_colors:
                dataset = {
                    'label': status.capitalize(),
                    'data': [dates.get(date_str, 0) for date_str in sorted_dates],
                    'backgroundColor': status_colors[status]['bg'],
                    'borderColor': status_colors[status]['border'],
                    'borderWidth': 2,
                    'fill': True
                }
                execution_trend_datasets.append(dataset)
        
        # Build the execution trends data structure
        execution_trends = {
            'labels': sorted_dates,
            'datasets': execution_trend_datasets
        }
        
        # Get status distribution
        cursor.execute(f"""
            SELECT 
                status,
                COUNT(*) as count
            FROM WorkflowExecutions
            WHERE 1=1 {date_filter} {workflow_filter}
            GROUP BY status
        """, *query_params)
        
        status_distribution_data = []
        status_distribution_labels = []
        status_distribution_colors = []
        
        status_color_map = {
            'completed': '#28a745',
            'failed': '#dc3545',
            'running': '#007bff',
            'paused': '#ffc107',
            'cancelled': '#6c757d'
        }
        
        for row in cursor.fetchall():
            status = row[0].lower()
            count = row[1]
            
            status_distribution_labels.append(status.capitalize())
            status_distribution_data.append(count)
            status_distribution_colors.append(status_color_map.get(status, '#6c757d'))
        
        # Build the status distribution data structure
        status_distribution = {
            'labels': status_distribution_labels,
            'datasets': [{
                'data': status_distribution_data,
                'backgroundColor': status_distribution_colors,
                'borderWidth': 1
            }]
        }
        
        # Get top workflows by execution count
        top_workflows_query = f"""
            SELECT 
                w.workflow_name,
                COUNT(e.execution_id) as execution_count
            FROM WorkflowExecutions e
            JOIN Workflows w ON e.workflow_id = w.id
            WHERE 1=1 {date_filter}
            GROUP BY w.workflow_name
            ORDER BY execution_count DESC
            OFFSET 0 ROWS FETCH NEXT 10 ROWS ONLY
        """
        
        # If specific workflow is selected, modify the query
        if workflow_id and workflow_id != 'all':
            top_workflows_query = f"""
                SELECT 
                    w.workflow_name,
                    COUNT(e.execution_id) as execution_count
                FROM WorkflowExecutions e
                JOIN Workflows w ON e.workflow_id = w.id
                WHERE e.workflow_id = ? {date_filter}
                GROUP BY w.workflow_name
            """
        
        cursor.execute(top_workflows_query, *query_params)
        
        top_workflows_labels = []
        top_workflows_data = []
        
        for row in cursor.fetchall():
            workflow_name = row[0]
            execution_count = row[1]
            
            top_workflows_labels.append(workflow_name)
            top_workflows_data.append(execution_count)
        
        # Build the top workflows data structure
        top_workflows = {
            'labels': top_workflows_labels,
            'datasets': [{
                'label': 'Execution Count',
                'data': top_workflows_data,
                'backgroundColor': '#36a2eb',
                'borderWidth': 1
            }]
        }
        
        # Get average duration by workflow
        duration_query = f"""
            SELECT 
                w.workflow_name,
                AVG(DATEDIFF(second, e.started_at, 
                    COALESCE(e.completed_at, getutcdate()))) as avg_duration
            FROM WorkflowExecutions e
            JOIN Workflows w ON e.workflow_id = w.id
            WHERE 1=1 {date_filter}
            GROUP BY w.workflow_name
            ORDER BY avg_duration DESC
            OFFSET 0 ROWS FETCH NEXT 10 ROWS ONLY
        """
        
        # If specific workflow is selected, modify the query
        if workflow_id and workflow_id != 'all':
            duration_query = f"""
                SELECT 
                    w.workflow_name,
                    AVG(DATEDIFF(second, e.started_at, 
                        COALESCE(e.completed_at, getutcdate()))) as avg_duration
                FROM WorkflowExecutions e
                JOIN Workflows w ON e.workflow_id = w.id
                WHERE e.workflow_id = ? {date_filter}
                GROUP BY w.workflow_name
            """
        
        cursor.execute(duration_query, *query_params)
        
        duration_labels = []
        duration_data = []
        
        for row in cursor.fetchall():
            workflow_name = row[0]
            avg_duration = row[1] if row[1] is not None else 0
            
            duration_labels.append(workflow_name)
            duration_data.append(avg_duration)
        
        # Build the duration data structure
        duration_data_struct = {
            'labels': duration_labels,
            'datasets': [{
                'label': 'Average Duration (seconds)',
                'data': duration_data,
                'backgroundColor': '#ff9f40',
                'borderWidth': 1
            }]
        }
        
        # Get performance table data
        performance_query = f"""
            SELECT 
                w.workflow_name,
                COUNT(e.execution_id) as executions,
                SUM(CASE WHEN e.status = 'Completed' THEN 1 ELSE 0 END) as successful,
                AVG(DATEDIFF(second, e.started_at, 
                    COALESCE(e.completed_at, getutcdate()))) as avg_duration,
                MAX(e.started_at) as last_execution
            FROM WorkflowExecutions e
            JOIN Workflows w ON e.workflow_id = w.id
            WHERE 1=1 {date_filter}
            GROUP BY w.workflow_name
            ORDER BY executions DESC
        """
        
        # If specific workflow is selected, modify the query
        if workflow_id and workflow_id != 'all':
            performance_query = f"""
                SELECT 
                    w.workflow_name,
                    COUNT(e.execution_id) as executions,
                    SUM(CASE WHEN e.status = 'Completed' THEN 1 ELSE 0 END) as successful,
                    AVG(DATEDIFF(second, e.started_at, 
                        COALESCE(e.completed_at, getutcdate()))) as avg_duration,
                    MAX(e.started_at) as last_execution
                FROM WorkflowExecutions e
                JOIN Workflows w ON e.workflow_id = w.id
                WHERE e.workflow_id = ? {date_filter}
                GROUP BY w.workflow_name
            """
        
        cursor.execute(performance_query, *query_params)
        
        performance_table = []
        
        for row in cursor.fetchall():
            workflow_name = row[0]
            executions = row[1]
            successful = row[2]
            avg_duration = row[3] if row[3] is not None else 0
            last_execution = row[4].isoformat() if row[4] else None
            
            # Calculate success rate
            success_rate = round((successful / executions) * 100) if executions > 0 else 0
            
            # Get trend data for this workflow
            trend_query = f"""
                SELECT 
                    COUNT(*) as count
                FROM WorkflowExecutions e
                JOIN Workflows w ON e.workflow_id = w.id
                WHERE w.workflow_name = ? {date_filter}
                GROUP BY DATEPART(week, e.started_at)
                ORDER BY DATEPART(week, e.started_at)
            """
            
            if workflow_id and workflow_id != 'all':
                trend_params = [workflow_name] + query_params[:-1]
            else:
                trend_params = [workflow_name] + query_params

            cursor.execute(trend_query, *trend_params)
            
            trend_data = [row[0] for row in cursor.fetchall()]
            
            # Ensure we have at least 5 data points for the trend
            while len(trend_data) < 5:
                trend_data.append(0)
            
            performance_table.append({
                'name': workflow_name,
                'executions': executions,
                'successRate': success_rate,
                'avgDuration': avg_duration,
                'lastExecution': last_execution,
                'trendData': trend_data
            })
        
        # Get overall metrics
        cursor.execute(f"""
            SELECT 
                COUNT(*) as total_executions,
                SUM(CASE WHEN status = 'Completed' THEN 1 ELSE 0 END) as successful_executions,
                AVG(DATEDIFF(second, started_at, 
                    COALESCE(completed_at, getutcdate()))) as avg_duration
            FROM WorkflowExecutions
            WHERE 1=1 {date_filter} {workflow_filter}
        """, *query_params)
        
        row = cursor.fetchone()
        total_executions = row[0] if row[0] else 0
        successful_executions = row[1] if row[1] else 0
        overall_avg_duration = row[2] if row[2] else 0
        
        # Calculate overall success rate
        overall_success_rate = round((successful_executions / total_executions) * 100) if total_executions > 0 else 0
        
        # Get pending approvals count
        cursor.execute("""
            SELECT COUNT(*) as pending_approvals
            FROM ApprovalRequests
            WHERE status = 'Pending'
        """)
        
        pending_approvals = cursor.fetchone()[0]
        
        # Close database connection
        cursor.close()
        conn.close()
        
        # Return the analytics data
        return jsonify({
            'executionTrends': execution_trends,
            'statusDistribution': status_distribution,
            'topWorkflows': top_workflows,
            'durationData': duration_data_struct,
            'performanceTable': performance_table,
            'overallMetrics': {
                'totalExecutions': total_executions,
                'successRate': overall_success_rate,
                'avgDuration': overall_avg_duration,
                'pendingApprovals': pending_approvals
            }
        })
        
    except Exception as e:
        print(f"Error retrieving workflow analytics: {str(e)}")
        logger.error(f"Error retrieving workflow analytics: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error retrieving workflow analytics: {str(e)}"
        }), 500
    

@app.route('/api/workflow/logs', methods=['GET'])
@cross_origin()
def get_all_workflow_logs():
    """Get logs from all workflow executions with filtering and pagination"""
    try:
        # Parse query parameters
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('pageSize', 50))
        execution_id = request.args.get('execution_id', '')
        level = request.args.get('level', '')
        search = request.args.get('search', '')
        date_from = request.args.get('dateFrom', '')
        date_to = request.args.get('dateTo', '')
        
        # Validate page and page_size
        if page < 1:
            page = 1
        if page_size < 1 or page_size > 500:
            page_size = 50
        
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # First, get the tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Build query with filters
        query = """
            SELECT el.log_id, el.timestamp, el.log_level, el.message, el.details,
                   el.execution_id, el.step_execution_id, se.node_id, se.node_name
            FROM ExecutionLogs el
            LEFT JOIN StepExecutions se ON el.step_execution_id = se.step_execution_id
            WHERE 1=1
        """
        count_query = """
            SELECT COUNT(*)
            FROM ExecutionLogs el
            LEFT JOIN StepExecutions se ON el.step_execution_id = se.step_execution_id
            WHERE 1=1
        """
        params = []
        
        # Add filters to query
        if execution_id:
            query += " AND el.execution_id = ?"
            count_query += " AND el.execution_id = ?"
            params.append(execution_id)
        
        if level:
            query += " AND el.log_level = ?"
            count_query += " AND el.log_level = ?"
            params.append(level)
        
        if search:
            query += " AND el.message LIKE ?"
            count_query += " AND el.message LIKE ?"
            params.append(f'%{search}%')
        
        if date_from:
            query += " AND el.timestamp >= ?"
            count_query += " AND el.timestamp >= ?"
            params.append(f'{date_from}T00:00:00')
        
        if date_to:
            query += " AND el.timestamp <= ?"
            count_query += " AND el.timestamp <= ?"
            params.append(f'{date_to}T23:59:59')
        
        # Get total count for pagination
        cursor.execute(count_query, *params)
        total_count = cursor.fetchone()[0]
        
        # Calculate total pages
        total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
        
        # Adjust page if out of bounds
        if page < 1:
            page = 1
        elif page > total_pages:
            page = total_pages
        
        # Add sorting and pagination
        query += " ORDER BY el.timestamp DESC OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"
        offset = (page - 1) * page_size
        params.append(offset)
        params.append(page_size)
        
        # Execute query
        cursor.execute(query, *params)
        
        logs = []
        for row in cursor.fetchall():
            # Convert row to dictionary
            log = {
                'log_id': row[0],
                'timestamp': row[1].isoformat() if row[1] else None,
                'log_level': row[2],
                'message': row[3],
                'details': row[4],
                'execution_id': row[5],
                'step_execution_id': row[6],
                'node_id': row[7],
                'node_name': row[8]
            }
            
            logs.append(log)
        
        cursor.close()
        conn.close()
        
        # Construct pagination info
        pagination = {
            'page': page,
            'page_size': page_size,
            'total_count': total_count,
            'total_pages': total_pages,
            'has_previous': page > 1,
            'has_next': page < total_pages
        }
        
        return jsonify({
            'status': 'success',
            'logs': logs,
            'pagination': pagination
        })
        
    except Exception as e:
        logger.error(f"Error retrieving workflow logs: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error retrieving workflow logs: {str(e)}"
        }), 500
    
#####################
# END WORKFLOW ROUTES
#####################


#####################
# AGENT KB ROUTES
#####################
# Function to get agent knowledge items
def get_agent_knowledge(agent_id):
    """Get knowledge items associated with an agent"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get knowledge items
        cursor.execute("""
            SELECT ak.knowledge_id, ak.agent_id, ak.document_id, ak.description, ak.added_date, 
                   d.filename, d.document_type, d.page_count
            FROM AgentKnowledge ak
            JOIN Documents d ON ak.document_id = d.document_id
            WHERE ak.agent_id = ? AND ak.is_active = 1
                    AND ISNULL(ak.added_by, 'USER') = 'USER'
            ORDER BY ak.added_date DESC
        """, agent_id)
        
        # Format results
        knowledge_items = []
        for row in cursor.fetchall():
            knowledge_items.append({
                'knowledge_id': row[0],
                'agent_id': row[1],
                'document_id': row[2],
                'description': row[3],
                'added_date': row[4].strftime('%Y-%m-%d %H:%M:%S') if row[4] else None,
                'filename': row[5],
                'document_type': row[6],
                'page_count': row[7]
            })

        try:
            # Get knowledge items for this user if available
            cursor.execute("""
                SELECT ak.knowledge_id, ak.agent_id, ak.document_id, ak.description, ak.added_date, 
                    d.filename, d.document_type, d.page_count
                FROM AgentKnowledge ak
                JOIN Documents d ON ak.document_id = d.document_id
                WHERE ak.agent_id = ? AND ak.is_active = 1
                    AND ak.added_by = ?
                ORDER BY ak.added_date DESC
            """, agent_id, str(current_user.id))

            for row in cursor.fetchall():
                knowledge_items.append({
                    'knowledge_id': row[0],
                    'agent_id': row[1],
                    'document_id': row[2],
                    'description': row[3],
                    'added_date': row[4].strftime('%Y-%m-%d %H:%M:%S') if row[4] else None,
                    'filename': row[5],
                    'document_type': row[6],
                    'page_count': row[7]
                })
        except Exception as e:
            print(f"Error getting user specific agent knowledge: {str(e)}")
            logger.error(f"Error getting user specific agent knowledge: {str(e)}")
        
        cursor.close()
        conn.close()
        
        return knowledge_items
    except Exception as e:
        logger.error(f"Error getting user specific agent knowledge: {str(e)}")
        return []


def get_agent_knowledge_for_user(agent_id, user_id=None):
    """Get knowledge items associated with an agent"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Format results
        knowledge_items = []

        try:
            if not user_id:
                user_id = current_user.id

            # Get knowledge items for this user if available
            cursor.execute("""
                SELECT ak.knowledge_id, ak.agent_id, ak.document_id, ak.description, ak.added_date,
                    d.filename, d.document_type, d.page_count, d.batch_id, d.document_metadata
                FROM AgentKnowledge ak
                JOIN Documents d ON ak.document_id = d.document_id
                WHERE ak.agent_id = ? AND ak.is_active = 1
                    AND ak.added_by = ?
                ORDER BY ak.added_date DESC
            """, agent_id, str(user_id))

            for row in cursor.fetchall():
                # Extract total_rows from metadata for Excel files
                total_rows = None
                if row[9]:
                    try:
                        meta = json.loads(row[9])
                        total_rows = meta.get('total_rows')
                    except (json.JSONDecodeError, TypeError):
                        pass

                knowledge_items.append({
                    'knowledge_id': row[0],
                    'agent_id': row[1],
                    'document_id': row[2],
                    'description': row[3],
                    'added_date': row[4].strftime('%Y-%m-%d %H:%M:%S') if row[4] else None,
                    'filename': row[5],
                    'document_type': row[6],
                    'page_count': row[7],
                    'batch_id': row[8] if row[8] else '',
                    'total_rows': total_rows
                })
        except Exception as e:
            print(f"Error getting user specific agent knowledge by user: {str(e)}")
            logger.error(f"Error getting user specific agent knowledge by user: {str(e)}")
        
        cursor.close()
        conn.close()
        
        return knowledge_items
    except Exception as e:
        logger.error(f"Error getting user specific agent knowledge by user: {str(e)}")
        return []

# Function to add a knowledge item to an agent
def add_agent_knowledge(agent_id, document_id, description='', user_id=None):
    """Add a knowledge item to an agent"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Insert knowledge item
        if not user_id:
            cursor.execute("""
                INSERT INTO AgentKnowledge (agent_id, document_id, description, added_date, is_active)
                VALUES (?, ?, ?, getutcdate(), 1)
            """, agent_id, document_id, description)
        else:
            cursor.execute("""
                INSERT INTO AgentKnowledge (agent_id, document_id, description, added_date, is_active, added_by)
                VALUES (?, ?, ?, getutcdate(), 1, ?)
            """, agent_id, document_id, description, str(user_id))
        
        # Get the new knowledge_id
        cursor.execute("SELECT @@IDENTITY")
        knowledge_id = cursor.fetchone()[0]
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return knowledge_id
    except Exception as e:
        logger.error(f"Error adding agent knowledge: {str(e)}")
        return None

# Function to update a knowledge item
def update_agent_knowledge(knowledge_id, description):
    """Update a knowledge item's description"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Update knowledge item
        cursor.execute("""
            UPDATE AgentKnowledge
            SET description = ?
            WHERE knowledge_id = ?
        """, description, knowledge_id)
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return True
    except Exception as e:
        logger.error(f"Error updating agent knowledge: {str(e)}")
        return False

# Function to delete a knowledge item
def delete_agent_knowledge(knowledge_id):
    """Delete a knowledge item (soft delete)"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get document_id before soft delete (for vector cleanup)
        cursor.execute("SELECT document_id FROM AgentKnowledge WHERE knowledge_id = ?", knowledge_id)
        row = cursor.fetchone()
        doc_id = row[0] if row else None
        
        # Soft delete
        cursor.execute("""
            UPDATE AgentKnowledge
            SET is_active = 0
            WHERE knowledge_id = ?
        """, knowledge_id)
        
        conn.commit()
        cursor.close()
        conn.close()
        
        # Clean up vector store
        if doc_id:
            try:
                from agent_knowledge_integration import remove_knowledge_document_vectors
                remove_knowledge_document_vectors(doc_id)
            except Exception as vec_err:
                logger.warning(f"Knowledge vector cleanup failed (non-fatal): {vec_err}")
        
        return True
    except Exception as e:
        logger.error(f"Error deleting agent knowledge: {str(e)}")
        return False

def get_agent_id_from_agent_knowledge(knowledge_id):
    """Get agent id from knowledge item"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Soft delete
        cursor.execute("""
            SELECT agent_id
            FROM AgentKnowledge
            WHERE knowledge_id = ?
        """, knowledge_id)
        
        agent_id = cursor.fetchone()[0]

        cursor.close()
        conn.close()
        
        return agent_id
    except Exception as e:
        logger.error(f"Error getting agent id from knowledge: {str(e)}")
        return None

def update_documents_batch_id(document_ids, batch_id=None):
    """
    Update batch_id for multiple documents at once (useful for grouping).
    
    Args:
        connection: Database connection object
        document_ids: List of document IDs to update with the same batch_id
        batch_id: The batch ID to set (if None, generates one based on current timestamp)
    
    Returns:
        The batch_id that was set
    """
    try:
        import time

        # Ensure document_ids is a list
        if isinstance(document_ids, str):
            document_ids = [document_ids]  # Convert single string to list

        logger.debug(f"Updating batch id {batch_id} for document {document_ids}")

        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Generate batch_id if not provided
        if batch_id is None:
            batch_id = f"batch_{int(time.time())}"

        params = [batch_id]
        params.extend(document_ids)
        
        # Update all documents with the same batch_id
        placeholders = ','.join('?' * len(document_ids))

        cursor.execute(f"""
            UPDATE Documents 
            SET batch_id = ?
            WHERE document_id IN ({placeholders})
        """, params)
        
        conn.commit()
        conn.close()

        logger.debug(f"Batch id updated for documents with params: {params}")
    except Exception as e:
        print(str(e))
        logger.error(f"Error updating document batch id: {str(e)}")
        return None
    
    return batch_id

# Function to process a document and add it as knowledge using the API
def process_document_as_knowledge(file_path, agent_id, description='', user_id=None, batch_id=None):
    """
    Process a document and add it as knowledge for an agent using the document API
    
    Args:
        file_path: Path to the document file
        agent_id: Agent ID to associate the document with
        description: Optional description of the knowledge
        
    Returns:
        Dictionary with processing result
    """
    try:
        # Get document API base URL
        doc_api_url = get_document_api_base_url()
        
        # Create the API endpoint URL
        process_url = f"{doc_api_url}/document/process"
        
        # Prepare the form data (NOTE: Field level data is not used as knowledge, only page text)
        form_data = {
            'filePath': file_path,
            'force_ai_extraction': 'true',
            'is_knowledge_document': 'true',
            'extract_fields': 'false',
            'detect_document_type': 'true'
        }
        print('Process Document URL:', process_url)
        print('Form Data:', form_data)
        # Make the API request (timeout matches DOC_PROCESSING_TIMEOUT_MINUTES)
        doc_timeout = cfg.DOC_PROCESSING_TIMEOUT_MINUTES * 60
        response = requests.post(process_url, data=form_data, timeout=doc_timeout)
        # print('Response:', response)
        # print('Response Code:', response.status_code)
        # Check if the request was successful
        if response.status_code == 200:
            result = response.json()
            
            # Add as knowledge if document was processed successfully
            if result['status'] == 'success' and 'document_id' in result:
                knowledge_id = add_agent_knowledge(
                    agent_id=agent_id,
                    document_id=result['document_id'],
                    description=description,
                    user_id=user_id
                )

                # Set batch id if provided
                if result['document_id'] and batch_id:
                    _ = update_documents_batch_id(result['document_id'], batch_id)
                
                if knowledge_id:
                    # Index in knowledge vector collection for smart retrieval (async — non-blocking)
                    try:
                        from agent_knowledge_integration import queue_knowledge_indexing
                        queue_knowledge_indexing(
                            document_id=result['document_id'],
                            agent_id=agent_id,
                            user_id=user_id
                        )
                    except Exception as vec_err:
                        logger.warning(f"Knowledge vector indexing queue failed (non-fatal): {vec_err}")
                    
                    page_count = result.get('page_count', 0)
                    total_chars = result.get('total_chars', 0)
                    resp = {
                        "status": "success",
                        "message": "Document processed and added as knowledge",
                        "knowledge_id": knowledge_id,
                        "document_id": result['document_id'],
                        "document_type": result.get('document_type', 'unknown'),
                        "page_count": page_count
                    }
                    # Hint for large docs: advanced search indexing runs in background
                    if page_count > 5 or total_chars > 100_000:
                        resp["background_processing"] = True
                        resp["message"] = (
                            "Document processed and added as knowledge. "
                            "Advanced search indexing is running in the background — "
                            "full search results may take a few minutes for large documents."
                        )
                    return resp
                else:
                    return {
                        "status": "error",
                        "message": "Failed to add document as knowledge"
                    }
            else:
                return {
                    "status": "error",
                    "message": result.get('message', 'Failed to process document')
                }
        else:
            return {
                "status": "error",
                "message": f"Request failed - likely due to invalid file type"
            }
            
    except requests.exceptions.Timeout as te:
        logger.warning(f"Document processing timed out after {cfg.DOC_PROCESSING_TIMEOUT_MINUTES} min: {str(te)}")
        return {
            "status": "error",
            "message": f"Document processing timed out after {cfg.DOC_PROCESSING_TIMEOUT_MINUTES} minutes. The document may still be processing in the background."
        }
    except Exception as e:
        logger.error(f"Error processing document as knowledge: {str(e)}")
        return {
            "status": "error",
            "message": f"Error: {str(e)}"
        }

# Route to get agent knowledge
@app.route('/get/agent_knowledge/<int:agent_id>', methods=['GET'])
@cross_origin()
@api_key_or_session_required()
def get_agent_knowledge_route(agent_id):
    """Get knowledge items for an agent"""
    knowledge_items = get_agent_knowledge(agent_id)
    return jsonify(knowledge_items)

# Route to get agent knowledge for a specific user
@app.route('/get/agent_knowledge_user/<int:agent_id>', methods=['GET'])
@cross_origin()
@login_required
def get_agent_knowledge_user_route(agent_id):
    """Get knowledge items for an agent"""
    knowledge_items = get_agent_knowledge_for_user(agent_id)
    return jsonify(knowledge_items)

# Route to check PDF page count before uploading (lightweight preflight)
@app.route('/api/document/preflight', methods=['POST'])
@cross_origin()
@api_key_or_session_required()
def document_preflight():
    """Quick page count check for PDF files without processing them."""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        filename = file.filename or ''
        
        result = {
            'filename': filename,
            'size': 0,
            'page_count': None,
            'is_pdf': filename.lower().endswith('.pdf'),
            'warning': None
        }
        
        if result['is_pdf']:
            try:
                import PyPDF2
                import io
                file_bytes = file.read()
                result['size'] = len(file_bytes)
                reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
                result['page_count'] = len(reader.pages)
                
                if result['page_count'] > 100:
                    result['warning'] = (
                        f"This PDF has {result['page_count']} pages. "
                        f"Documents over 100 pages may take 10-30+ minutes to process "
                        f"and may exceed context limits when used in agent chat. "
                        f"For best results with very large documents, consider using the Document Processor instead."
                    )
            except Exception as pdf_err:
                logger.warning(f"Preflight PDF page count failed: {pdf_err}")
                result['page_count'] = None
        else:
            file_bytes = file.read()
            result['size'] = len(file_bytes)
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Document preflight error: {e}")
        return jsonify({'error': str(e)}), 500


# Route to add knowledge to an agent
@app.route('/add/agent_knowledge', methods=['POST'])
@cross_origin()
@api_key_or_session_required()
def add_agent_knowledge_route():
    """Add knowledge to an agent"""
    try:
        print(f'Adding agent knowledge - TRIGGERS RELOAD!!!')
        logger.info(f"Adding agent knowledge - TRIGGERS RELOAD!!!")

        # Get form data
        agent_id = request.form.get('agent_id')
        description = request.form.get('description', '')
        user_id = None
        user_id = request.form.get('user_id')
        batch_id = request.form.get('batch_id', None)

        if user_id == '':
            user_id = None

        print(f'Agent ID: {agent_id}')
        print(f'Description: {description}')
        print(f'Batch ID: {batch_id}')
        
        logger.info(f"Adding document in batch: {batch_id}")
        
        # Check for file
        if 'file' not in request.files:
            print('No file part')
            return jsonify({
                "status": "error",
                "message": "No file part"
            }), 400
            
        file = request.files['file']
        if file.filename == '':
            print('No selected file')
            return jsonify({
                "status": "error",
                "message": "No selected file"
            }), 400
        
        # Check file size against configurable limit
        file.seek(0, 2)  # Seek to end
        file_size_mb = file.tell() / (1024 * 1024)
        file.seek(0)     # Reset to start
        max_size_mb = cfg.DOC_MAX_UPLOAD_SIZE_MB
        if file_size_mb > max_size_mb:
            logger.warning(f"File {file.filename} rejected: {file_size_mb:.1f} MB exceeds {max_size_mb} MB limit")
            return jsonify({
                "status": "error",
                "message": f"File too large ({file_size_mb:.1f} MB). Maximum allowed size is {max_size_mb} MB."
            }), 400
        
        print('Uploading file...')
        print('Upload folder:', str(app.config['UPLOAD_FOLDER']))
        # Save file temporarily
        filename = str(uuid.uuid4()) + '_' + file.filename
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        print('File uploaded successfully', str(file_path))

        # Default description to filename if empty
        if not description.strip():
            description = file.filename

        # Process document and add as knowledge
        if not user_id:
            result = process_document_as_knowledge(
                file_path=file_path,
                agent_id=agent_id,
                description=description,
                batch_id=batch_id
            )
        else:
            result = process_document_as_knowledge(
                file_path=file_path,
                agent_id=agent_id,
                description=description,
                user_id=user_id,   # Links knowledge to a specific user
                batch_id=batch_id
            )
        
        # Clean up temporary file and reloading agents
        # NOTE: Only delete the temp file if processing succeeded or returned a definitive error.
        # If it timed out, the Doc API may still be processing — leave the file for it to finish.
        processing_timed_out = (result.get('status') == 'error' and 'timed out' in str(result.get('message', '')).lower())
        
        try:
            # Reload agents
            print(f'Reloading agent {agent_id}...')
            load_agents(agent_id=agent_id)

            # For Excel files: persist original and generate metadata
            original_filename = file.filename
            is_excel = original_filename.lower().endswith(('.xlsx', '.xls'))

            if is_excel and result.get('status') == 'success' and result.get('document_id'):
                import shutil
                from agent_excel_tools import generate_excel_metadata

                doc_id = result['document_id']
                persist_dir = os.path.join(
                    os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__))),
                    cfg.EXCEL_KNOWLEDGE_FILES_DIR, doc_id
                )
                os.makedirs(persist_dir, exist_ok=True)
                persistent_path = os.path.join(persist_dir, original_filename)
                shutil.copy2(file_path, persistent_path)
                print(f'Excel file persisted to: {persistent_path}')

                # Update Documents.original_path to the persistent location
                try:
                    conn_persist = get_db_connection()
                    cursor_persist = conn_persist.cursor()
                    cursor_persist.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
                    cursor_persist.execute(
                        "UPDATE Documents SET original_path = ? WHERE document_id = ?",
                        persistent_path, doc_id
                    )
                    conn_persist.commit()
                    cursor_persist.close()
                    conn_persist.close()
                except Exception as path_err:
                    logger.warning(f"Failed to update persistent path: {path_err}")

                # Generate and store metadata profile
                try:
                    metadata = generate_excel_metadata(persistent_path)
                    conn_meta = get_db_connection()
                    cursor_meta = conn_meta.cursor()
                    cursor_meta.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
                    cursor_meta.execute(
                        "UPDATE Documents SET document_metadata = ? WHERE document_id = ?",
                        json.dumps(metadata, default=str), doc_id
                    )
                    conn_meta.commit()
                    cursor_meta.close()
                    conn_meta.close()
                    print(f'Excel metadata stored ({metadata.get("total_rows", 0)} total rows)')
                except Exception as meta_err:
                    logger.warning(f"Failed to generate Excel metadata: {meta_err}")

            # Remove the temp upload file (always, including Excel - we already copied it)
            # But NOT if processing timed out — the Doc API may still be working on it
            if processing_timed_out:
                logger.info(f"Skipping temp file cleanup — processing timed out, Doc API may still be working: {file_path}")
            else:
                print('Removing temporary files...')
                os.remove(file_path)
                print('Done.')
        except Exception as cleanup_err:
            logger.warning(f"Cleanup error: {cleanup_err}")

        return jsonify(result)

    except Exception as e:
        print(f"Error adding agent knowledge: {str(e)}")
        logger.error(f"Error adding agent knowledge: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error: {str(e)}"
        }), 500

# Route to update knowledge
@app.route('/update/agent_knowledge/<int:knowledge_id>', methods=['POST'])
@cross_origin()
@login_required
def update_agent_knowledge_route(knowledge_id):
    """Update knowledge description"""
    try:
        data = request.json
        description = data.get('description', '')
        
        result = update_agent_knowledge(knowledge_id, description)
        
        if result:
            return jsonify({
                "status": "success",
                "message": "Knowledge updated successfully"
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Failed to update knowledge"
            }), 500
            
    except Exception as e:
        logger.error(f"Error updating agent knowledge: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error: {str(e)}"
        }), 500

# Route to delete knowledge
@app.route('/delete/agent_knowledge/<int:knowledge_id>', methods=['POST'])
@cross_origin()
@api_key_or_session_required()
def delete_agent_knowledge_route(knowledge_id):
    """Delete knowledge"""
    try:
        try:
            agent_id = get_agent_id_from_agent_knowledge(knowledge_id=knowledge_id)
        except:
            pass

        result = delete_agent_knowledge(knowledge_id)

        try:
            # Reload agents
            print(f'Reloading agent {agent_id}...')
            load_agents(agent_id=agent_id)
            print('Done.')
        except:
            pass
        
        if result:
            return jsonify({
                "status": "success",
                "message": "Knowledge deleted successfully"
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Failed to delete knowledge"
            }), 500
            
    except Exception as e:
        logger.error(f"Error deleting agent knowledge: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error: {str(e)}"
        }), 500

# Route for knowledge management UI
@app.route('/agent_knowledge/<int:agent_id>')
@login_required
def agent_knowledge_page(agent_id):
    """Render agent knowledge management page"""
    return render_template('agent_knowledge.html', agent_id=agent_id)


#####################
# END AGENT KB ROUTES
#####################

#####################
# Workflow Cat
#####################
@app.route('/add/workflow/category', methods=['POST'])
@cross_origin()
@developer_required(api=True)
def add_workflow_category():
    try:
        data = request.get_json()
        name = data.get('name')
        
        if not name:
            return jsonify({"status": "error", "message": "Category name is required"}), 400
        
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # First, get the tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Insert the category
        cursor.execute("""
            INSERT INTO WorkflowCategories (name, created_date, last_modified, is_active)
            VALUES (?, getutcdate(), getutcdate(), 1)
        """, name)
        
        conn.commit()
        
        # Get the new ID
        cursor.execute("SELECT @@IDENTITY")
        new_id = cursor.fetchone()[0]
        
        cursor.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "message": "Category added successfully",
            "id": new_id
        })
        
    except Exception as e:
        logger.error(f"Error adding workflow category: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error adding category: {str(e)}"
        }), 500

@app.route('/update/workflow/category/<int:category_id>', methods=['PUT'])
@cross_origin()
@developer_required(api=True)
def update_workflow_category_name(category_id):
    try:
        data = request.get_json()
        name = data.get('name')
        
        if not name:
            return jsonify({"status": "error", "message": "Category name is required"}), 400
        
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # First, get the tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Update the category
        cursor.execute("""
            UPDATE WorkflowCategories
            SET name = ?, last_modified = getutcdate()
            WHERE id = ?
        """, name, category_id)
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "message": "Category updated successfully"
        })
        
    except Exception as e:
        logger.error(f"Error updating workflow category: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error updating category: {str(e)}"
        }), 500

@app.route('/delete/workflow/category/<int:category_id>', methods=['DELETE'])
@cross_origin()
@developer_required(api=True)
def delete_workflow_category(category_id):
    try:
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # First, get the tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Update all workflows with this category to have no category
        cursor.execute("""
            UPDATE Workflows
            SET category_id = NULL, last_modified = getutcdate()
            WHERE category_id = ?
        """, category_id)
        
        # Delete the category
        cursor.execute("""
            DELETE FROM WorkflowCategories
            WHERE id = ?
        """, category_id)
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "message": "Category deleted successfully"
        })
        
    except Exception as e:
        logger.error(f"Error deleting workflow category: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Error deleting category: {str(e)}"
        }), 500
#####################
# End Workflow Cat
#####################


@app.route('/chat/data/reset', methods=['POST'])
@login_required
def reset_data_chat():
    try:
        # Clear the chat history in your data engine instance
        if 'data_engine' in session:
            data_engine = session['data_engine']
            data_engine.clear_chat_hist()
            data_engine.environment.question_count = 0
            data_engine.environment.is_first_question = True
            session['data_engine'] = data_engine
        
        return jsonify({'status': 'success', 'message': 'Conversation reset successfully'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})
    

@app.route('/chat/data/status', methods=['GET'])
@login_required
def chat_data_status():
    print('Checking data status...')
    if 'data_engine' in session:
        print('Checking data session...')
        data_engine = session['data_engine']
        conversation_length = len(data_engine.environment.chat_history)
        max_length = cfg.MAX_CONVERSATION_LENGTH
        is_max_reached = conversation_length >= max_length
        print('conversation_length', conversation_length)
        print('max_length', max_length)
        print('is_max_reached', is_max_reached)
        return jsonify({
            'conversation_length': conversation_length,
            'max_length': max_length,
            'is_max_reached': is_max_reached
        })
    else:
        return jsonify({
            'conversation_length': 0,
            'max_length': cfg.MAX_CONVERSATION_LENGTH,
            'is_max_reached': False
        })


def test_odbc_connection(connection_string, database_type, autocommit=None):
    """
    Test database connection using ODBC
    
    Args:
        connection_string: Connection string for the database
        database_type: Type of database (for reference only)
        
    Returns:
        tuple: (success_boolean, message_string)
    """
    try:
        # Log the connection attempt
        logger.info(f"Testing connection to {database_type} database")
        print(f"Testing connection to {database_type} database")
        
        # Set timeout for connection attempt (5 seconds)
        connection_timeout = 10
        
        # For SQL Server connection strings, add timeout if not present
        if database_type == 'SQL Server' and 'Connection Timeout=' not in connection_string:
            connection_string += f";Connection Timeout={connection_timeout};"

        print(f"Autocommit: {autocommit}")
        print(f"Testing connection string: {connection_string}")
            
        # Attempt to connect
        if autocommit:
            conn = pyodbc.connect(connection_string, timeout=connection_timeout, autocommit=autocommit)
        else:
            conn = pyodbc.connect(connection_string, timeout=connection_timeout)
        
        # Execute a simple test query based on database type
        cursor = conn.cursor()
        
        if database_type == 'Oracle':
            cursor.execute("SELECT 1 FROM DUAL")
        else:
            cursor.execute("SELECT 1")
            
        # Close connections
        cursor.close()
        conn.close()
        
        return True, "Connection successful"
        
    except pyodbc.Error as e:
        error_msg = f"ODBC Error: {str(e)}"
        logger.error(error_msg)
        return False, error_msg
        
    except Exception as e:
        error_msg = f"Connection test failed: {str(e)}"
        logger.error(error_msg)
        return False, error_msg

@app.route("/test/connection", methods=['POST'])
@cross_origin()
@api_key_or_session_required()
def test_connection():
    try:
        logger.info('Received request at /test/connection...')
        
        # Get JSON data from the request
        data = request.get_json()
        
        database_type = data.get('database_type')
        server = data.get('server')
        port = data.get('port')
        database_name = data.get('database_name')
        user_name = data.get('user_name')
        password = data.get('password')
        parameters = data.get('parameters')
        connection_string = data.get('connection_string')
        odbc_driver = data.get('odbc_driver', '')
        connection_id = data.get('connection_id')  # NEW: Get connection ID
        
        # Log request details (excluding password)
        logger.debug(f"Testing connection to {database_type} database on {server}:{port}/{database_name}")
        logger.debug(f"Connection ID: {connection_id}")
        
        # Validate required fields
        if not all([database_type, server, database_name]) and not connection_string:
            return jsonify({
                "status": "error",
                "message": "Missing required connection parameters"
            }), 400
        
        # Build connection string if not provided
        if not connection_string or connection_string.strip() == '':
            connection_string = generate_connection_string(
                database_type, server, port, database_name, user_name, password, parameters, odbc_driver
            )

        # =========================================================================
        # NEW: Handle masked passwords for existing connections
        # =========================================================================
        has_masked_password = '••••••••' in connection_string
        
        if has_masked_password and connection_id:
            # Existing connection with masked password - resolve it
            logger.debug(f"Resolving masked password for connection {connection_id}")
            stored_password = get_connection_password_by_id(connection_id)
            
            if stored_password:
                # Resolve if it's a secret reference, or use directly if legacy
                actual_password = retrieve_connection_password(stored_password)
                logger.debug(f"Password resolved: {'Yes' if actual_password else 'No'}")
                
                # Replace masked password in connection string
                connection_string = connection_string.replace('Pwd=••••••••', f'Pwd={actual_password}')
                connection_string = connection_string.replace('Password=••••••••', f'Password={actual_password}')
            else:
                logger.warning(f"No stored password found for connection {connection_id}")
                return jsonify({
                    "status": "error",
                    "message": "Password not found. Please re-enter the password and save the connection."
                })
        elif has_masked_password and not connection_id:
            # New connection shouldn't have masked password
            logger.warning("New connection has masked password - this shouldn't happen")
            return jsonify({
                "status": "error",
                "message": "Password is required for testing. Please enter a password."
            })
        # =========================================================================
        # END NEW CODE
        # =========================================================================

        # =========================================================================
        # Resolve any secret references in connection string
        # This handles BOTH:
        # - New style: {{LOCAL_SECRET:xxx}} gets replaced with actual password
        # - Legacy: Plain text passwords pass through unchanged
        # =========================================================================
        connection_string = resolve_connection_string_secrets(connection_string)

        if 'excel' in connection_string.lower():
            success, message = test_odbc_connection(connection_string, database_type, autocommit=True)
        else:
            success, message = test_odbc_connection(connection_string, database_type)
        
        if success:
            return jsonify({
                "status": "success",
                "message": "Connection successful"
            })
        else:
            return jsonify({
                "status": "error",
                "message": message
            })
    
    except Exception as e:
        logger.error(f"Error testing connection: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

def get_available_odbc_drivers():
    """Get list of installed ODBC drivers on the system"""
    try:
        drivers = pyodbc.drivers()
        return drivers
    except:
        return []

@app.route("/get/odbc_drivers", methods=['GET'])
@cross_origin()
def get_odbc_drivers():
    """API endpoint to get available ODBC drivers"""
    drivers = get_available_odbc_drivers()
    return jsonify(drivers)


###########################################
# External Route Integration with Main App
###########################################

# Import Initial Setup routes
from initial_setup_routes import initial_setup_bp
app.register_blueprint(initial_setup_bp)

# Register user feedback routes
from feedback_routes import register_feedback_routes
register_feedback_routes(app)

# Register application updater routes
from github_updater import register_updater_routes
register_updater_routes(app)

# Register assistant routes
setup_assistant_routes(app, docs_dir='assistant_docs')

# Import the scheduler blueprint
from scheduler_routes import scheduler_bp
app.register_blueprint(scheduler_bp)

# Import the preferences blueprint
from preferences_routes import preferences_bp, get_preference
app.register_blueprint(preferences_bp)

# Import the agent communication blueprint
from agent_communication_routes import agent_comm_bp
app.register_blueprint(agent_comm_bp)

# Import summarizaion wrappers
from document_summarization_wrapper_routes import summarization_bp
app.register_blueprint(summarization_bp)

# Import environment assignment blueprint
from environment_assignment_api_routes import assignments_bp
app.register_blueprint(assignments_bp)

# Import agent environments blueprint
from agent_environments.environment_api import environments_bp
app.register_blueprint(environments_bp)

# Import tier usage blueprint
from admin_tier_usage import admin_tier_bp
app.register_blueprint(admin_tier_bp)

# Import the custom tool import blueprint
from custom_tool_import_routes import custom_tool_import_bp
app.register_blueprint(custom_tool_import_bp)

# Import Whisper backend blueprint
from whisper_routes import whisper_bp
app.register_blueprint(whisper_bp)

# Import Workfow Builder backend blueprint
from workflow_builder_routes import workflow_builder_bp
app.register_blueprint(workflow_builder_bp)

# Import Agent Email backend blueprint
from agent_email_routes import agent_email_bp
app.register_blueprint(agent_email_bp)

# Import email processing routes
from email_processing_routes import email_processing_bp
app.register_blueprint(email_processing_bp)

# Import Agent extract backend blueprint (for AI extract workflow node execution)
from ai_extract_routes import register_ai_extract_routes
register_ai_extract_routes(app)

# Import Onboarding backend blueprint
from onboarding_routes import onboarding_bp
app.register_blueprint(onboarding_bp)

# Import local secrets routes
from local_secrets_routes import secrets_bp
app.register_blueprint(secrets_bp)

# Import local history routes
from local_history_routes import history_bp
app.register_blueprint(history_bp)

# Register BYOK API routes
app.register_blueprint(api_keys_bp)
register_page_route(app)
# Initialize BYOK configuration
init_byok()

# Import the identity provider admin blueprint
from auth_identity_routes import identity_bp
app.register_blueprint(identity_bp)

# Import the integrations blueprint
from integration_routes import integrations_bp

# Register the blueprint
app.register_blueprint(integrations_bp)

# Add route for the page
@app.route('/integrations')
@developer_required()
def integrations_page():
    return render_template('integrations.html')

# Import and register the Data Explorer blueprint
# NOTE: We use importlib to avoid making routes/ a package (which would shadow
# builder_service/routes and builder_data/routes when they share sys.path).
import importlib.util as _de_importlib
_de_spec = _de_importlib.spec_from_file_location(
    "data_explorer_routes",
    os.path.join(os.path.dirname(__file__), "routes", "data_explorer.py"),
)
_de_mod = _de_importlib.module_from_spec(_de_spec)
_de_spec.loader.exec_module(_de_mod)
data_explorer_bp = _de_mod.data_explorer_bp
app.register_blueprint(data_explorer_bp)

# Import and register the MCP blueprint
from builder_mcp.routes.mcp_routes import mcp_bp
app.register_blueprint(mcp_bp)

# Import and register the Builder Document Search blueprint
try:
    from builder_service.routes.builder_document_routes import builder_document_bp
    app.register_blueprint(builder_document_bp)
    print("[INFO] Registered builder document search blueprint")
except ImportError as e:
    print(f"[WARN] Could not load builder document search blueprint: {e}")

# Import and register the Cloud Storage blueprint
try:
    from builder_cloud.routes.cloud_routes import cloud_bp
    app.register_blueprint(cloud_bp)
except ImportError:
    pass  # Cloud storage module not available

# MCP Server Management page (new builder_mcp UI)
@app.route('/mcp_servers')
@developer_required()
def mcp_servers_page():
    """Render the MCP Server Management UI"""
    return render_template('mcp_servers.html')

@preferences_bp.route('/')
@login_required
def preferences_page():
    """Render user preferences page"""
    return render_template('user_preferences.html')

# Document job scheduler UI
@app.route('/document_scheduler')
@login_required
def document_scheduler():
    """Render the document job scheduler UI"""
    return render_template('document_scheduler.html')

# Document summarizer UI
@app.route('/document_summarizer')
@login_required
def document_summarizer():
    """Render the document job scheduler UI"""
    return render_template('document_summarization.html')

# @app.context_processor
# def inject_config():
#     """Make configuration settings available to all templates"""
#     return {
#         'SHOW_DOCUMENT_FEATURES': SHOW_DOCUMENT_FEATURES,
#         'SHOW_WORKFLOW_FEATURES': SHOW_WORKFLOW_FEATURES,
#         'AGENT_ENVIRONMENTS_ENABLED': AGENT_ENVIRONMENTS_ENABLED,
#         'ENABLE_CAUTION_SYSTEM': cfg.ENABLE_CAUTION_SYSTEM,
#         'SHOW_DATA_AGENT_TEST_FEATURES': cfg.SHOW_DATA_AGENT_TEST_FEATURES,
#         'AGENT_ENVIRONMENTS_TIER': AGENT_ENVIRONMENTS_SETTINGS.get('tier_display', ''),
        
#         # TOKEN WARNING SETTINGS
#         'ENABLE_TOKEN_WARNING': getattr(cfg, 'ENABLE_TOKEN_WARNING', True),
#         'TOKEN_WARNING_THRESHOLD': getattr(cfg, 'TOKEN_WARNING_THRESHOLD', 10000),
#         'TOKEN_WARNING_MESSAGE': getattr(cfg, 'TOKEN_WARNING_MESSAGE', "Your request is estimated to use {token_count} tokens, which may result in slower processing or failed requests."),
#         'CHARS_PER_TOKEN': getattr(cfg, 'CHARS_PER_TOKEN', 4)
#     }

@app.context_processor
def inject_config():
    """Make configuration settings available to all templates"""
    try:
        # Get tier-based feature flags
        tier_context = create_tier_context_processor()()
        
        # Get local feature flags (two-tier: effective = cloud AND local)
        try:
            from feature_flags import get_local_flags
            local_flags = get_local_flags()
        except Exception:
            local_flags = {}
        
        return {
            # Tier-based feature flags (from Cloud API)
            **tier_context,  # Unpacks all tier variables
            
            # Local feature flags for sidebar visibility
            'FLAG_COMMAND_CENTER': local_flags.get('command_center_enabled', True),
            'FLAG_BUILDER': local_flags.get('builder_enabled', True),
            'FLAG_ENVIRONMENTS': local_flags.get('environments_enabled', True),
            'FLAG_MCP_SERVERS': local_flags.get('mcp_servers_enabled', True),
            'FLAG_INTEGRATIONS': local_flags.get('integrations_enabled', True),
            
            # Your existing static config
            'ENABLE_CAUTION_SYSTEM': cfg.ENABLE_CAUTION_SYSTEM,
            'SHOW_DATA_AGENT_TEST_FEATURES': cfg.SHOW_DATA_AGENT_TEST_FEATURES,
            'ENABLE_TOKEN_WARNING': getattr(cfg, 'ENABLE_TOKEN_WARNING', True),
            'TOKEN_WARNING_THRESHOLD': getattr(cfg, 'TOKEN_WARNING_THRESHOLD', 10000),
            'TOKEN_WARNING_MESSAGE': getattr(cfg, 'TOKEN_WARNING_MESSAGE', "..."),
            'CHARS_PER_TOKEN': getattr(cfg, 'CHARS_PER_TOKEN', 4),
            'ENABLE_WORKFLOW_ASSISTANT': cfg.ENABLE_WORKFLOW_ASSISTANT,
            'SHOW_EXPERIMENTAL_FEATURES': getattr(cfg, 'SHOW_EXPERIMENTAL_FEATURES', False),
            'WORKFLOW_TRAINING_CAPTURE_ENABLED': getattr(cfg, 'WORKFLOW_TRAINING_CAPTURE_ENABLED', False),
            'DOC_PROCESSING_TIMEOUT_MINUTES': getattr(cfg, 'DOC_PROCESSING_TIMEOUT_MINUTES', 60),
            'DOC_MAX_UPLOAD_SIZE_MB': getattr(cfg, 'DOC_MAX_UPLOAD_SIZE_MB', 50),
            'USE_MODERN_NAV': getattr(cfg, 'USE_MODERN_NAV', True),
            'SHOW_LEGACY_DATA_CHAT': getattr(cfg, 'SHOW_LEGACY_DATA_CHAT', False),
            'APP_VERSION': app_config.APP_VERSION,
        }
        
    except Exception as e:
        logger.error(f"Error in inject_config: {e}")
        # Return safe defaults on error
        return {
            'SHOW_DOCUMENT_FEATURES': False,
            'SHOW_WORKFLOW_FEATURES': False,
            'AGENT_ENVIRONMENTS_ENABLED': False,
            'ENABLE_CAUTION_SYSTEM': getattr(cfg, 'ENABLE_CAUTION_SYSTEM', False),
            'SHOW_DATA_AGENT_TEST_FEATURES': getattr(cfg, 'SHOW_DATA_AGENT_TEST_FEATURES', False),
            'SHOW_EXPERIMENTAL_FEATURES': getattr(cfg, 'SHOW_EXPERIMENTAL_FEATURES', False),
            'WORKFLOW_TRAINING_CAPTURE_ENABLED': getattr(cfg, 'WORKFLOW_TRAINING_CAPTURE_ENABLED', False),
            'AGENT_ENVIRONMENTS_TIER': '',
            'ENABLE_TOKEN_WARNING': True,
            'TOKEN_WARNING_THRESHOLD': 10000,
            'TOKEN_WARNING_MESSAGE': "Your request may use many tokens",
            'CHARS_PER_TOKEN': 4,
            'TIER_FEATURES': {},
            'SUBSCRIPTION_INFO': {},
            'CURRENT_TIER': 'free',
            'CURRENT_USAGE': {},
            'tier_allows': lambda x: False,
            'tier_limit': lambda x: 0,
            'APP_VERSION': app_config.APP_VERSION,
            'FLAG_COMMAND_CENTER': True,
            'FLAG_BUILDER': True,
            'FLAG_ENVIRONMENTS': True,
            'FLAG_MCP_SERVERS': True,
            'FLAG_INTEGRATIONS': True,
        }


@app.route('/api/system/config', methods=['GET'])
def get_system_config():
    param = request.args.get('param')
    if param == 'caution_system':
        return jsonify({
            'enabled': cfg.ENABLE_CAUTION_SYSTEM
        })
    return jsonify({'error': 'Invalid parameter'}), 400

@app.route("/api/send_email", methods=['POST'])
@cross_origin()
def api_send_email():
    """
    API endpoint for sending emails.
    Expects a JSON payload with:
    {
        "recipients": ["email1@example.com", "email2@example.com"] or "email1@example.com",
        "subject": "Email subject",
        "body": "Email body",
        "html_content": false (optional)
    }
    """
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['recipients', 'subject', 'body']
        for field in required_fields:
            if field not in data:
                return jsonify({"status": "error", "message": f"Missing required field: {field}"}), 400
        
        # Get optional fields with defaults
        html_content = data.get('html_content', False)
        
        # Send email using AppUtils
        success = send_email(
            recipients=data['recipients'],
            subject=data['subject'],
            body=data['body'],
            html_content=html_content
        )
        
        if success:
            return jsonify({"status": "success", "message": "Email sent successfully"})
        else:
            return jsonify({"status": "error", "message": "Failed to send email"}), 500
            
    except Exception as e:
        logger.error(f"Error in api_send_email: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


def purge_document(document_id):
    """Purge a document from the vector database and SQL database"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # First check if document exists
        cursor.execute("SELECT document_id FROM Documents WHERE document_id = ?", (document_id,))
        if not cursor.fetchone():
            return  "error", f"Document not found: {document_id}", 404
            
        # Get all page_ids for this document
        cursor.execute("""
            SELECT page_id FROM DocumentPages 
            WHERE document_id = ?
            ORDER BY page_number
        """, (document_id,))
        
        page_ids = [row[0] for row in cursor.fetchall()]
        
        if page_ids:
            # Delete from vector database using VectorEngineClient for each page_id
            from vector_engine_client import VectorEngineClient
            from CommonUtils import get_vector_api_base_url
            
            vector_client = VectorEngineClient(
                base_url=get_vector_api_base_url(),
                api_key=os.getenv('VECTOR_API_KEY')
            )
            
            # Delete each page from vector database
            vector_errors = []
            for page_id in page_ids:
                response = vector_client.delete_document(page_id)
                if response.get('status') != 'success':
                    vector_errors.append(f"Page {page_id}: {response.get('message')}")
            
            if vector_errors:
                return  "error", f"Failed to delete some pages from vector database: {'; '.join(vector_errors)}", 500
        
        # Check if database connection is still alive before SQL deletion
        try:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        except Exception as conn_error:
            logger.warning(f"Database connection lost during document deletion: {str(conn_error)}")
            # Attempt to reconnect
            try:
                cursor.close()
                conn.close()
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
                logger.info("Successfully reconnected to database")
            except Exception as reconnect_error:
                logger.error(f"Failed to reconnect to database: {str(reconnect_error)}")
                return  "error", f"Database connection lost and reconnection failed: {str(reconnect_error)}", 500
            
        # Delete from SQL database
        cursor.execute("DELETE FROM Documents WHERE document_id = ?", (document_id,))
        conn.commit()
        
        return  "success", f"Document {document_id} deleted successfully", 200
        
    except Exception as e:
        logger.error(f"Error deleting document: {str(e)}")
        return  "error", str(e), 500
        
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()
        

@app.route('/document/delete/<document_id>', methods=['POST'])
@login_required
def delete_document(document_id):
    """Delete a document from both vector and SQL databases"""
    status, message, status_code = purge_document(document_id)
    return jsonify({
        "status": status,
        "message": message
    }), status_code

###########################################
# Document Manager Routes
###########################################

@app.route('/document-manager')
@login_required
@tier_allows_feature('documents')
def document_manager():
    """Render the document manager page"""
    # Only show vector maintenance to admins
    show_vector_maintenance = current_user.role >= 2  # Assuming role 2+ are admins
    return render_template('document_manager.html', 
                         show_vector_maintenance=show_vector_maintenance)

@app.route('/api/documents', methods=['GET'])
@api_key_or_session_required(min_role=2)
def api_get_documents():
    """API endpoint to get paginated documents list"""
    try:
        # Get query parameters
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 20))
        document_type = request.args.get('document_type', '')
        search_query = request.args.get('search', '')
        date_range = request.args.get('date_range', '')
        start_date = request.args.get('start_date', '')
        end_date = request.args.get('end_date', '')
        
        # Calculate offset
        offset = (page - 1) * per_page
        
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Build the query
        query = """
            SELECT 
                d.document_id,
                d.filename,
                d.document_type,
                d.page_count,
                d.reference_number,
                d.customer_id,
                d.vendor_id,
                d.document_date,
                d.processed_at,
                d.original_path,
                d.archived_path
            FROM Documents d
            WHERE d.is_knowledge_document = 0
        """
        
        params = []
        
        # Add filters
        if document_type:
            query += " AND d.document_type = ?"
            params.append(document_type)
            
        if search_query:
            query += " AND d.filename LIKE ?"
            params.append(f'%{search_query}%')
            
        # Date range filters
        if date_range == 'today':
            query += " AND CAST(d.processed_at AS DATE) = CAST(getutcdate() AS DATE)"
        elif date_range == 'week':
            query += " AND d.processed_at >= DATEADD(day, -7, getutcdate())"
        elif date_range == 'month':
            query += " AND d.processed_at >= DATEADD(day, -30, getutcdate())"
        elif date_range == 'custom' and start_date and end_date:
            query += " AND d.processed_at >= ? AND d.processed_at <= ?"
            params.extend([start_date, end_date + ' 23:59:59'])
        
        # Get total count
        count_query = f"SELECT COUNT(*) FROM ({query}) AS cnt"
        cursor.execute(count_query, params)
        total_count = cursor.fetchone()[0]
        
        # Add pagination
        query += " ORDER BY d.processed_at DESC OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"
        params.extend([offset, per_page])
        
        # Execute main query
        cursor.execute(query, params)
        
        # Fetch results
        documents = []
        for row in cursor.fetchall():
            documents.append({
                'document_id': row[0],
                'filename': row[1],
                'document_type': row[2],
                'page_count': row[3],
                'reference_number': row[4],
                'customer_id': row[5],
                'vendor_id': row[6],
                'document_date': row[7] if row[7] else None,
                'processed_at': row[8].isoformat() if row[8] else None,
                'original_path': row[9],
                'archived_path': row[10]
            })
        
        # Get statistics
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT document_id) as total_documents,
                SUM(page_count) as total_pages,
                COUNT(DISTINCT document_type) as document_types,
                MAX(processed_at) as last_updated
            FROM Documents
            WHERE is_knowledge_document = 0
        """)
        
        stats_row = cursor.fetchone()
        stats = {
            'total_documents': stats_row[0] or 0,
            'total_pages': stats_row[1] or 0,
            'document_types': stats_row[2] or 0,
            'last_updated': stats_row[3].isoformat() if stats_row[3] else None
        }
        
        # Calculate pagination info
        total_pages = (total_count + per_page - 1) // per_page
        
        conn.close()
        
        return jsonify({
            'documents': documents,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total_count': total_count,
                'total_pages': total_pages,
                'has_prev': page > 1,
                'has_next': page < total_pages
            },
            'stats': stats
        })
        
    except Exception as e:
        print(f"Error fetching documents: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/document-types', methods=['GET'])
@api_key_or_session_required(min_role=2)
def api_get_document_types():
    """Get all document types with counts"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get document types with counts
        cursor.execute("""
            SELECT document_type, COUNT(*) as count
            FROM Documents
            WHERE is_knowledge_document = 0 AND document_type IS NOT NULL
            GROUP BY document_type
            ORDER BY count DESC
        """)
        
        types = []
        for row in cursor.fetchall():
            types.append({
                'name': row[0],
                'count': row[1]
            })
        
        conn.close()
        
        return jsonify(types)
        
    except Exception as e:
        print(f"Error fetching document types: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/documents/<string:document_id>', methods=['PUT'])
@api_key_or_session_required(min_role=2)
def api_update_document(document_id):
    """Update a single document"""
    try:
        data = request.json
        
        # Validate required fields
        if 'document_type' not in data or not data['document_type']:
            return jsonify({'error': 'Document type is required'}), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Update document
        cursor.execute("""
            UPDATE Documents
            SET 
                document_type = ?,
                reference_number = ?,
                customer_id = ?,
                vendor_id = ?,
                document_date = ?,
                last_modified_by = ?,
                last_modified_at = getutcdate()
            WHERE document_id = ?
        """, (
            data.get('document_type'),
            data.get('reference_number'),
            data.get('customer_id'),
            data.get('vendor_id'),
            data.get('document_date'),
            current_user.username,
            document_id
        ))
        
        if cursor.rowcount == 0:
            conn.close()
            return jsonify({'error': 'Document not found'}), 404
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Document updated successfully'})
        
    except Exception as e:
        print(f"Error updating document: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/documents/<string:document_id>', methods=['DELETE'])
@api_key_or_session_required(min_role=2)
def api_delete_document(document_id):
    """Delete a single document"""
    try:
        status, message, status_code = purge_document(document_id)
        if status == "success":
            return jsonify({'success': True, 'message': 'Document deleted successfully'})
        else:
            return jsonify({'error': message}), status_code
    except Exception as e:
        print(f"Error deleting document: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/documents/bulk-update', methods=['PUT'])
@developer_required(api=True)
def api_bulk_update_documents():
    """Bulk update documents"""
    try:
        data = request.json
        document_ids = data.get('document_ids', [])
        document_type = data.get('document_type')
        
        if not document_ids:
            return jsonify({'error': 'No documents selected'}), 400
            
        if not document_type:
            return jsonify({'error': 'Document type is required'}), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Build placeholders for IN clause
        placeholders = ','.join(['?' for _ in document_ids])
        
        # Update documents
        query = f"""
            UPDATE Documents
            SET 
                document_type = ?,
                last_modified_by = ?,
                last_modified_at = getutcdate()
            WHERE document_id IN ({placeholders})
        """
        
        params = [document_type, current_user.username] + document_ids
        cursor.execute(query, params)
        
        updated_count = cursor.rowcount
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': f'Successfully updated {updated_count} documents',
            'updated_count': updated_count
        })
        
    except Exception as e:
        print(f"Error bulk updating documents: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/documents/bulk-delete', methods=['DELETE'])
@developer_required(api=True)
def api_bulk_delete_documents():
    """Bulk delete documents"""
    try:
        data = request.json
        document_ids = data.get('document_ids', [])
        
        if not document_ids:
            return jsonify({'error': 'No documents selected'}), 400
        
        deleted_count = 0
        for document_id in document_ids:
            status, message, status_code = purge_document(document_id)
            if status == "success":
                deleted_count += 1
            else:
                print(f"Error deleting document {document_id}: {message} ({status_code})")
        
        return jsonify({
            'success': True,
            'message': f'Successfully deleted {deleted_count} documents',
            'deleted_count': deleted_count
        })
        
    except Exception as e:
        print(f"Error bulk deleting documents: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/document/reprocess-vectors', methods=['POST'])
@api_key_or_session_required()
@cross_origin()
def proxy_reprocess_vectors():
    """
    Proxy route to forward vector reprocessing requests to the document API
    """
    try:
        # Get the document API base URL
        doc_api_base_url = get_document_api_base_url()
        
        # Forward the request to the document API
        response = requests.post(
            f"{doc_api_base_url}/document/reprocess-vectors",
            json=request.get_json(),
            headers={'Content-Type': 'application/json'},
            timeout=cfg.DOC_PROCESSING_TIMEOUT_MINUTES * 60
        )
        
        # Return the response from the document API
        return jsonify(response.json()), response.status_code
        
    except requests.exceptions.Timeout:
        logger.error("Timeout calling document API for vector reprocessing")
        return jsonify({
            "status": "error",
            "message": "Request timed out - operation may still be running in background",
            "pages_processed": 0,
            "documents_processed": 0,
            "errors": ["Request timed out"]
        }), 500
        
    except requests.exceptions.ConnectionError:
        logger.error("Connection error calling document API for vector reprocessing")
        return jsonify({
            "status": "error", 
            "message": "Could not connect to document processing service",
            "pages_processed": 0,
            "documents_processed": 0,
            "errors": ["Document API service unavailable"]
        }), 503
        
    except Exception as e:
        logger.error(f"Error proxying vector reprocessing request: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Proxy error: {str(e)}",
            "pages_processed": 0,
            "documents_processed": 0,
            "errors": [str(e)]
        }), 500


@app.route('/document/reprocess-vectors/all', methods=['POST'])
@login_required
@cross_origin()
def proxy_reprocess_all_vectors():
    """
    Proxy route to forward "reprocess all vectors" requests to the document API
    """
    try:
        # Get the document API base URL
        doc_api_base_url = get_document_api_base_url()
        
        # Forward the request to the document API
        response = requests.post(
            f"{doc_api_base_url}/document/reprocess-vectors/all",
            json=request.get_json(),
            headers={'Content-Type': 'application/json'},
            timeout=cfg.DOC_PROCESSING_TIMEOUT_MINUTES * 60
        )
        
        # Return the response from the document API
        return jsonify(response.json()), response.status_code
        
    except requests.exceptions.Timeout:
        logger.error("Timeout calling document API for reprocess all vectors")
        return jsonify({
            "status": "error",
            "message": "Request timed out - operation may still be running in background", 
            "pages_processed": 0,
            "documents_processed": 0,
            "errors": ["Request timed out"]
        }), 500
        
    except requests.exceptions.ConnectionError:
        logger.error("Connection error calling document API for reprocess all vectors")
        return jsonify({
            "status": "error",
            "message": "Could not connect to document processing service",
            "pages_processed": 0,
            "documents_processed": 0,
            "errors": ["Document API service unavailable"]
        }), 503
        
    except Exception as e:
        logger.error(f"Error proxying reprocess all vectors request: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Proxy error: {str(e)}",
            "pages_processed": 0,
            "documents_processed": 0,
            "errors": [str(e)]
        }), 500




@app.route('/agent_dashboard')
@login_required
def agent_dashboard():
    """Render the agent management dashboard"""
    return render_template('agent_dashboard.html')


@app.route('/api/current_user', methods=['GET'])
@cross_origin()
@login_required
def get_current_user():
    """
    Get current user information from session.
    Returns user_id and basic user info for the logged-in user.
    """
    try:
        # Get user info from current_user (Flask-Login)
        user_data = {
            'user_id': str(current_user.id),
            'username': current_user.username,
            'name': current_user.name,
            'email': current_user.email if hasattr(current_user, 'email') else None
        }
        
        return jsonify(user_data)
        
    except AttributeError as e:
        # If current_user doesn't have expected attributes
        logger.warning(f"Error accessing current_user attributes: {str(e)}")
        
        # Try to get from session as fallback
        if 'user_id' in session:
            return jsonify({
                'user_id': session.get('user_id'),
                'username': session.get('username', 'Unknown')
            })
        else:
            return jsonify({
                'error': 'User information not available',
                'user_id': None
            }), 401
            
    except Exception as e:
        logger.error(f"Error in get_current_user: {str(e)}")
        return jsonify({
            'error': 'Failed to get user information',
            'user_id': None
        }), 500
    

@app.route('/import/agent', methods=['POST'])
@api_key_or_session_required()
def import_agent():
    """Import an agent from an exported package"""
    try:
        if 'file' not in request.files:
            return jsonify({"status": "error", "message": "No file provided"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"status": "error", "message": "No file selected"}), 400
        
        # Save and extract zip file
        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, 'import.zip')
        file.save(zip_path)
        
        extract_dir = os.path.join(temp_dir, 'extracted')
        os.makedirs(extract_dir)
        
        import zipfile
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        
        # Find metadata file
        metadata_path = None
        for root, dirs, files in os.walk(extract_dir):
            if 'metadata.json' in files:
                metadata_path = os.path.join(root, 'metadata.json')
                break
        
        if not metadata_path:
            return jsonify({"status": "error", "message": "Invalid package: metadata.json not found"}), 400
        
        # Load metadata
        with open(metadata_path, 'r') as f:
            metadata = json.load(f)
        
        # Validate metadata version
        if metadata.get('version') != '1.0':
            return jsonify({"status": "error", "message": "Unsupported package version"}), 400
        
        # Import custom tools first
        imported_tools = []
        custom_tools_dir = os.path.join(os.path.dirname(metadata_path), 'custom_tools')
        if os.path.exists(custom_tools_dir):
            for tool_name in os.listdir(custom_tools_dir):
                tool_source = os.path.join(custom_tools_dir, tool_name)
                tool_dest = os.path.join(cfg.CUSTOM_TOOLS_FOLDER, tool_name)
                
                # Check if tool already exists
                if os.path.exists(tool_dest):
                    # Optionally handle conflicts
                    tool_name = f"{tool_name}_imported_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
                    tool_dest = os.path.join(cfg.CUSTOM_TOOLS_FOLDER, tool_name)
                
                shutil.copytree(tool_source, tool_dest)
                imported_tools.append(tool_name)
        
        # Create new agent
        new_agent_id = None
        agent_info = metadata['agent']
        # new_agent_id = insert_agent(
        #     agent_info['name'],
        #     agent_info['objective'],
        #     agent_info.get('enabled', True)
        # )
        
        if not new_agent_id:
            return jsonify({"status": "error", "message": "Failed to create agent"}), 500
        
        # Add tools to agent
        # Core tools
        # for tool_name in metadata['tools']['core_tools']:
        #     add_agent_tool(new_agent_id, tool_name, False)
        
        # Custom tools
        # for tool_name in imported_tools:
        #     add_agent_tool(new_agent_id, tool_name, True)
        
        # Import knowledge items (if any)
        # Note: This would require document files to be included in export
        
        # Cleanup
        shutil.rmtree(temp_dir)
        
        # Reload agents
        global active_agents
        active_agents = load_agents()
        
        return jsonify({
            "status": "success",
            "message": "Agent imported successfully",
            "agent_id": new_agent_id,
            "imported_tools": imported_tools
        })
        
    except Exception as e:
        logger.error(f"Error importing agent: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500



def find_agent_knowledge_documents(agent_id, user_id=None):
    """Get knowledge documents for an agent"""
    try:
        import pyodbc
        
        conn = pyodbc.connect(
            f"DRIVER={{SQL Server}};SERVER={cfg.DATABASE_SERVER};DATABASE={cfg.DATABASE_NAME};UID={cfg.DATABASE_UID};PWD={cfg.DATABASE_PWD}"
        )
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        if user_id is None:
            user_id = 'USER'
        else:
            user_id = str(user_id)
        
        # Get knowledge items
        cursor.execute("""
            SELECT ak.knowledge_id, ak.agent_id, ak.document_id, ak.description, 
                   d.filename, d.document_type
            FROM AgentKnowledge ak
            JOIN Documents d ON ak.document_id = d.document_id
            WHERE ak.agent_id = ? AND ak.is_active = 1
            AND (
				ISNULL(ak.added_by, 'USER') = 'USER'
					OR
				ISNULL(ak.added_by, 'USER') = ?
				)
        """, agent_id, str(user_id))
        
        # Format results
        documents = []
        for row in cursor.fetchall():
            documents.append({
                'knowledge_id': row[0],
                'agent_id': row[1],
                'document_id': row[2],
                'description': row[3],
                'filename': row[4],
                'document_type': row[5]
            })
        
        cursor.close()
        conn.close()
        
        return documents
    except Exception as e:
        logger.error(f"Error getting agent knowledge documents: {str(e)}")
        return []


@app.route('/export/agent/<int:agent_id>', methods=['GET','POST'])
@cross_origin()
@api_key_or_session_required()
def export_agent(agent_id):
    """Export an agent with all its configurations, tools, and knowledge"""
    try:
        import tempfile
        import shutil
        import json
        import base64
        from datetime import datetime
        
        # Get agent details
        agent_data = get_agent_by_id(agent_id)
        #print(86 * '-')
        #print('agent_data', agent_data)
        if not agent_data:
            return jsonify({"status": "error", "message": "Agent not found"}), 404
        else:
            agent_data = agent_data[0]
        
        # Create temporary directory for export
        temp_dir = tempfile.mkdtemp()
        agent_dir = os.path.join(temp_dir, f"agent_{agent_id}")
        os.makedirs(agent_dir)
        
        # Create subdirectories
        os.makedirs(os.path.join(agent_dir, "custom_tools"))
        os.makedirs(os.path.join(agent_dir, "knowledge"))
        os.makedirs(os.path.join(agent_dir, "knowledge", "documents"))
        
        # Get agent tools
        agent_tools = agent_data['tool_names']
        print('agent_tools', agent_tools)

        # Separate core and custom tools
        core_tools = []
        custom_tools = []
        custom_tools_metadata = []

        print('Loading tools...')
        for idx, tool in enumerate(agent_tools):
            print('Tool:', tool)
            if agent_data['custom_tool'][idx]:
                print('Adding as custom tool:', tool)
                custom_tools.append(tool)
                
                # Export custom tool using existing functionality
                tool_source = os.path.join(cfg.CUSTOM_TOOLS_FOLDER, tool)
                if os.path.exists(tool_source):
                    # Method 1: Copy the entire tool directory
                    tool_dest = os.path.join(agent_dir, "custom_tools", tool)
                    shutil.copytree(tool_source, tool_dest)
                    
                    # Method 2: Also create a zip of each tool for easier individual import
                    tool_zip_path = os.path.join(agent_dir, "custom_tools", f"{tool}.zip")
                    shutil.make_archive(
                        base_name=os.path.join(agent_dir, "custom_tools", tool),
                        format='zip',
                        root_dir=tool_source
                    )
                    
                    # Load tool configuration for metadata
                    config_path = os.path.join(tool_source, 'config.json')
                    if os.path.exists(config_path):
                        with open(config_path, 'r') as f:
                            tool_config = json.load(f)
                            custom_tools_metadata.append({
                                "name": tool,
                                "description": tool_config.get('description', ''),
                                "parameters": tool_config.get('parameters', []),
                                "parameter_types": tool_config.get('parameter_types', []),
                                "output_type": tool_config.get('output_type', 'str'),
                                "has_directory": True,
                                "has_zip": True
                            })
                    else:
                        custom_tools_metadata.append({
                            "name": tool,
                            "has_directory": True,
                            "has_zip": True
                        })
                else:
                    print(f"Warning: Custom tool {tool} directory not found")
                    custom_tools_metadata.append({
                        "name": tool,
                        "error": "Tool directory not found",
                        "has_directory": False,
                        "has_zip": False
                    })
            else:
                print('Adding as core tool:', tool)
                core_tools.append(tool)

        print('Core Tools:', core_tools)
        print('Custom Tools:', custom_tools)
        print('Custom Tools Metadata:', custom_tools_metadata)
        
        # Get agent knowledge with document handling
        knowledge_items = []
        knowledge_export_summary = {
            "total_items": 0,
            "exported_documents": 0,
            "failed_documents": 0,
            "items": []
        }
        
        # Knowledge Export
        knowledge_docs = None
        knowledge_docs = find_agent_knowledge_documents(agent_id)
        if knowledge_docs:
            print('Loading agent knowledge...')
            knowledge = get_agent_knowledge(agent_id)
            knowledge_export_summary["total_items"] = len(knowledge)
            print('Knowledge:', knowledge)
            for item in knowledge:
                knowledge_entry = {
                    "knowledge_id": item.get('knowledge_id', ''),
                    "description": item.get('description', ''),
                    "document_id": item.get('document_id', ''),
                    "created_date": str(item.get('created_date', '')),
                    "document_type": item.get('document_type', ''),
                    "document_exported": False,
                    "document_filename": None,
                    "export_mode": None  # 'full', 'metadata_only', or 'failed'
                }
                print('Processing Knowledge Entry:', item.get('knowledge_id', ''))
                
                # Export the document content
                if item.get('document_id'):
                    try:
                        print('Getting document data using id...', item['document_id'])
                        # Get full document with all pages
                        doc_response = get_document_by_id(item['document_id'])
                        print('Document Data:', doc_response)
                        
                        if doc_response:
                            # Save document with all pages
                            doc_filename = f"doc_{item['document_id']}.json"
                            doc_path = os.path.join(agent_dir, "knowledge", "documents", doc_filename)

                            # Export full document with all pages
                            doc_export = {
                                "document_id": item['document_id'],
                                "document_type": doc_response.get('document_type', ''),
                                "filename": doc_response.get('filename', ''),
                                "description": item.get('description', ''),
                                "page_count": doc_response.get('page_count', 0),
                                "is_knowledge_document": doc_response.get('is_knowledge_document', True),
                                "export_mode": "full",
                                "pages": doc_response.get('pages', []),
                                "full_content": doc_response.get('full_content', ''),
                                "metadata": doc_response.get('metadata', {})
                            }
                            knowledge_entry["export_mode"] = "full"
                            
                            print('Writing knowledge file...', doc_path)
                            # Write document export file
                            with open(doc_path, 'w', encoding='utf-8') as f:
                                json.dump(doc_export, f, indent=2, ensure_ascii=False)
                            
                            knowledge_entry["document_exported"] = True
                            knowledge_entry["document_filename"] = doc_filename
                            knowledge_entry["pages_exported"] = len(doc_response.get('pages', []))
                            knowledge_export_summary["exported_documents"] += 1
                            
                            print(f"Exported document {item['document_id']}: {len(doc_response.get('pages', []))} pages")
                            print(f"{doc_export}")
                            
                    except Exception as e:
                        print(f"Failed to export document {item.get('document_id')}: {str(e)}")
                        knowledge_entry["export_error"] = str(e)
                        knowledge_entry["export_mode"] = "failed"
                        knowledge_export_summary["failed_documents"] += 1
                
                knowledge_items.append(knowledge_entry)
                knowledge_export_summary["items"].append(knowledge_entry)
        
        # Write knowledge export summary
        knowledge_summary_path = os.path.join(agent_dir, "knowledge", "knowledge_summary.json")
        with open(knowledge_summary_path, 'w') as f:
            json.dump(knowledge_export_summary, f, indent=2)

        # Export environment if agent has one assigned
        environment_data = None
        if AGENT_ENVIRONMENTS_ENABLED:
            print('Checking for assigned environment...')
            environment_data = get_agent_environment_for_export(agent_id)
            if environment_data:
                print(f"Found environment: {environment_data['name']} with {environment_data['package_count']} packages")
                
                # Create environment subdirectory
                env_export_dir = os.path.join(agent_dir, "environment")
                os.makedirs(env_export_dir, exist_ok=True)
                
                # Write environment details
                env_export_path = os.path.join(env_export_dir, "environment.json")
                with open(env_export_path, 'w') as f:
                    json.dump(environment_data, f, indent=2)
                
                # Also create a requirements.txt for convenience
                requirements_path = os.path.join(env_export_dir, "requirements.txt")
                with open(requirements_path, 'w') as f:
                    for pkg in environment_data['packages']:
                        if pkg['version']:
                            f.write(f"{pkg['name']}=={pkg['version']}\\n")
                        else:
                            f.write(f"{pkg['name']}\\n")
                
                print(f"Environment exported: {environment_data['name']}")
            else:
                print('No environment assigned to this agent')
        else:
            print('Agent environments feature not enabled, skipping environment export')
        
        # Create metadata file with enhanced information
        metadata = {
            "version": app_config.AGENT_EXPORT_VERSION,
            "platform": {
                "name": app_config.APP_NAME,
                "export_version": app_config.APP_VERSION,
                "export_date": datetime.now().isoformat(),
                "export_user": session.get('user', {}).get('name', 'unknown')
            },
            "agent": {
                "id": agent_data['agent_id'],
                "name": agent_data['agent_description'],
                "objective": agent_data['agent_objective'],
                "enabled": agent_data['agent_enabled'],
                "is_data_agent": agent_data.get('is_data_agent', False),
                "created_date": str(agent_data.get('agent_create_date', ''))
            },
            "tools": {
                "core_tools": core_tools,
                "custom_tools": custom_tools,
                "custom_tools_metadata": custom_tools_metadata,
                "total_tools": len(agent_tools)
            },
            "knowledge": {
                "enabled": cfg.ENABLE_AGENT_KNOWLEDGE_MANAGEMENT,
                "summary": knowledge_export_summary,
                "items": knowledge_items
            },
            "environment": {
                "included": environment_data is not None,
                "data": environment_data
            } if environment_data else {
                "included": False,
                "data": None
            }
        }
        
        print('Resolving dependencies...')
        # Resolve dependencies for core tools
        from tool_dependency_manager import load_tool_dependencies
        manager = load_tool_dependencies()
        final_tools, dependency_map = manager.resolve_tool_list(core_tools, False)
        
        metadata["dependencies"] = {
            "required_core_tools": list(final_tools),
            "dependency_map": dependency_map,
            "added_dependencies": list(set(final_tools) - set(core_tools))
        }
        
        # Write metadata
        metadata_path = os.path.join(agent_dir, "metadata.json")
        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2)

        env_info = ""
        if environment_data:
            env_info = f"""
                            ## Environment
                            - **Name**: {environment_data['name']}
                            - **Description**: {environment_data.get('description', 'N/A')}
                            - **Python Version**: {environment_data.get('python_version', 'N/A')}
                            - **Packages**: {environment_data['package_count']} packages
                            - See `/environment/requirements.txt` for package list
                            """
        
        # Create README file for the export
        readme_content = f"""# Agent Export: {agent_data['agent_description']}

                            ## Export Information
                            - **Export Date**: {datetime.now().isoformat()}
                            - **Agent ID**: {agent_data['agent_id']}
                            - **Platform Version**: {app_config.APP_VERSION}

                            ## Contents
                            - `/metadata.json` - Complete agent configuration
                            - `/custom_tools/` - Custom tool implementations ({len(custom_tools)} tools)
                            - `/knowledge/` - Agent knowledge base ({knowledge_export_summary['total_items']} items)
                            - `/knowledge/documents/` - Exported documents ({knowledge_export_summary['exported_documents']} documents)
                            - `/environment/` - Python environment configuration
                            {env_info}

                            ## Tools Summary
                            ### Core Tools ({len(core_tools)})
                            {chr(10).join(['- ' + tool for tool in core_tools])}

                            ### Custom Tools ({len(custom_tools)})
                            {chr(10).join(['- ' + tool for tool in custom_tools])}

                            ## Import Instructions
                            1. Use the Import Agent feature in the platform
                            2. Select this ZIP file
                            3. The system will create a new agent with all configurations
                            4. If an environment is included, you can choose to create or skip it

                            ## Notes
                            - Custom tools will be imported if they don't already exist
                            - Knowledge documents can be re-imported or linked to existing documents
                            - Core tool dependencies will be automatically resolved
                            - Environment packages will be installed automatically if environment is created
                            """
        
        readme_path = os.path.join(agent_dir, "README.md")
        with open(readme_path, 'w') as f:
            f.write(readme_content)
        
        print('Creating zip file...')
        # Sanitize filename
        safe_name = agent_data['agent_description'].replace(' ', '_').replace('/', '_').replace('\\', '_')
        safe_name = ''.join(c for c in safe_name if c.isalnum() or c in ('_', '-'))
        output_filename = f"{safe_name}_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        
        local_zip_path = os.path.join(temp_dir, f"agent_{agent_id}")
        shutil.make_archive(
            base_name=local_zip_path,
            format='zip',
            root_dir=temp_dir,
            base_dir=f"agent_{agent_id}"
        )
        
        print('Sending file...', local_zip_path + '.zip')
        # Send file
        response = send_file(
            local_zip_path + '.zip',
            as_attachment=True,
            download_name=output_filename,
            mimetype='application/zip'
        )
        
        # Cleanup will happen after response is sent
        @response.call_on_close
        def cleanup():
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
        
        print(86 * '-')
        return response
        
    except Exception as e:
        logger.error(f"Error exporting agent: {str(e)}")
        # Try to cleanup on error
        try:
            if 'temp_dir' in locals():
                shutil.rmtree(temp_dir)
            capture_exception(e, {'agent_id': agent_id})  # telemetry
        except:
            pass
        return jsonify({"status": "error", "message": str(e)}), 500


def get_document_by_id(document_id):
    """
    Retrieve document content and metadata by document ID.
    Returns a document with all its pages consolidated.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        cursor.execute("""
            SELECT d.document_id, d.filename, d.document_type, d.page_count, d.[is_knowledge_document],
                   p.page_id, p.page_number, p.full_text AS content
            FROM Documents d
                JOIN DocumentPages p ON p.document_id = d.document_id
            WHERE d.document_id = ?
            ORDER BY p.page_number
        """, (document_id,))
        
        rows = cursor.fetchall()
        
        if not rows:
            return None
        
        # Extract document metadata from first row
        first_row = rows[0]
        document_data = {
            'document_id': first_row[0],
            'filename': first_row[1],
            'document_type': first_row[2],
            'page_count': first_row[3],
            'is_knowledge_document': True if first_row[4] else False,
            'pages': [],
            'full_content': '',  # Concatenated content from all pages
            'metadata': {
                'total_pages': first_row[3]
            }
        }
        
        # Process all pages
        content_parts = []
        for row in rows:
            page_data = {
                'page_id': row[5],
                'page_number': row[6],
                'content': row[7] or ''
            }
            document_data['pages'].append(page_data)
            
            # Add page content to full content with page separator
            if row[7]:
                content_parts.append(f"{row[7]}")
        
        # Combine all page content
        document_data['full_content'] = '\n\n'.join(content_parts)
        
        # Add summary statistics
        document_data['metadata']['actual_pages_retrieved'] = len(rows)
        document_data['metadata']['has_content'] = bool(document_data['full_content'].strip())
        document_data['metadata']['total_characters'] = len(document_data['full_content'])
        
        # Check if we have all pages
        if len(rows) != document_data['page_count']:
            logger.warning(f"Document {document_id}: Expected {document_data['page_count']} pages, got {len(rows)}")
            document_data['metadata']['missing_pages'] = True
            document_data['metadata']['missing_page_numbers'] = [
                i for i in range(1, document_data['page_count'] + 1) 
                if i not in [p['page_number'] for p in document_data['pages']]
            ]
        
        cursor.close()
        conn.close()
        
        return document_data
        
    except Exception as e:
        logger.error(f"Error retrieving document {document_id}: {str(e)}")
        print(f"Error retrieving document {document_id}: {str(e)}")
        return None


def get_document_metadata(document_id):
    """
    Retrieve only document metadata without page content.
    Useful for checking document existence or getting basic info.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        cursor.execute("""
            SELECT document_id, filename, document_type, page_count, [is_knowledge_document]
            FROM Documents
            WHERE document_id = ?
        """, (document_id,))
        
        result = cursor.fetchone()
        
        if result:
            return {
                'document_id': result[0],
                'filename': result[1],
                'document_type': result[2],
                'page_count': result[3],
                'is_knowledge_document': True if result[4] else False
            }
        
        cursor.close()
        conn.close()
        
        return None
        
    except Exception as e:
        logger.error(f"Error retrieving document metadata {document_id}: {str(e)}")
        return None


def import_document(doc_data):
    """
    Import a document with all its pages from the export data.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        # Generate new document ID for the import
        new_doc_id = str(uuid.uuid4())
        
        # Insert main document record
        cursor.execute("""
            INSERT INTO Documents (document_id, filename, document_type, page_count, is_knowledge_document)
            VALUES (?, ?, ?, ?, ?)
        """, (
            new_doc_id,
            doc_data.get('filename', 'imported_document'),
            doc_data.get('document_type', 'unknown'),
            doc_data.get('page_count', 0),
            1 if doc_data.get('is_knowledge_document', False) else 0
        ))
        
        # Insert pages if we have full export
        if doc_data.get('export_mode') == 'full' and doc_data.get('pages'):
            for page in doc_data['pages']:
                page_id = str(uuid.uuid4())
                cursor.execute("""
                    INSERT INTO DocumentPages (page_id, document_id, page_number, full_text)
                    VALUES (?, ?, ?, ?)
                """, (
                    page_id,
                    new_doc_id,
                    page.get('page_number', 1),
                    page.get('content', '')
                ))
            
            logger.info(f"Imported document {new_doc_id} with {len(doc_data['pages'])} pages")
        
        elif doc_data.get('export_mode') == 'metadata_only':
            # Document was too large, only metadata was exported
            # Create a placeholder page or reference
            page_id = str(uuid.uuid4())
            placeholder_text = f"Document metadata imported. Original document had {doc_data.get('page_count', 0)} pages.\n"
            placeholder_text += f"Original filename: {doc_data.get('filename', 'unknown')}\n"
            placeholder_text += doc_data.get('size_warning', '')
            
            cursor.execute("""
                INSERT INTO DocumentPages (page_id, document_id, page_number, full_text)
                VALUES (?, ?, ?, ?)
            """, (page_id, new_doc_id, 1, placeholder_text))
            
            logger.info(f"Imported document {new_doc_id} (metadata only)")
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return new_doc_id
        
    except Exception as e:
        logger.error(f"Error importing document: {str(e)}")
        if conn:
            conn.rollback()
            conn.close()
        return None



@app.route('/import/agent/analyze', methods=['POST'])
@cross_origin()
@login_required
def analyze_agent_package():
    """Analyze an agent export package without importing"""
    try:
        import tempfile
        import zipfile
        import json
        
        if 'file' not in request.files:
            return jsonify({"status": "error", "message": "No file provided"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"status": "error", "message": "No file selected"}), 400
        
        # Create temporary directory
        temp_dir = tempfile.mkdtemp()
        
        try:
            # Save and extract zip file
            zip_path = os.path.join(temp_dir, 'analyze.zip')
            file.save(zip_path)
            
            extract_dir = os.path.join(temp_dir, 'extracted')
            os.makedirs(extract_dir)
            
            print('Extracting zip...')
            # Extract ZIP
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            
            print('Finding metadata...')
            # Find metadata file
            metadata_path = None
            for root, dirs, files in os.walk(extract_dir):
                if 'metadata.json' in files:
                    metadata_path = os.path.join(root, 'metadata.json')
                    agent_root = root
                    break
            
            if not metadata_path:
                return jsonify({
                    "status": "error",
                    "message": "Invalid package: metadata.json not found"
                }), 400

            print('Metadata', metadata_path)
            
            print('Loading metadata file...')
            # Load metadata
            with open(metadata_path, 'r') as f:
                metadata = json.load(f)
            
            # Analyze package contents
            analysis = {
                "status": "success",
                "package_info": {
                    "version": metadata.get('version', 'unknown'),
                    "export_date": metadata.get('platform', {}).get('export_date', 'unknown'),
                    "platform_version": metadata.get('platform', {}).get('export_version', 'unknown')
                },
                "agent_info": metadata.get('agent', {}),
                "tools": {
                    "core_tools": metadata.get('tools', {}).get('core_tools', []),
                    "custom_tools": metadata.get('tools', {}).get('custom_tools', []),
                    "custom_tools_found": [],
                    "total_count": metadata.get('tools', {}).get('total_tools', 0)
                },
                "knowledge": {
                    "enabled": metadata.get('knowledge', {}).get('enabled', False),
                    "total_items": metadata.get('knowledge', {}).get('summary', {}).get('total_items', 0),
                    "exported_documents": metadata.get('knowledge', {}).get('summary', {}).get('exported_documents', 0),
                    "documents_found": []
                },
                "dependencies": metadata.get('dependencies', {}),
                "conflicts": {
                    "agent_name_exists": False,
                    "existing_custom_tools": []
                }
            }
            print('Package contents:', analysis)
            
            # Check for custom tools
            print('Checking custom tools...')
            custom_tools_dir = os.path.join(agent_root, 'custom_tools')
            if os.path.exists(custom_tools_dir):
                for item in os.listdir(custom_tools_dir):
                    if os.path.isdir(os.path.join(custom_tools_dir, item)):
                        analysis['tools']['custom_tools_found'].append(item)
                        # Check if tool already exists
                        if os.path.exists(os.path.join(cfg.CUSTOM_TOOLS_FOLDER, item)):
                            analysis['conflicts']['existing_custom_tools'].append(item)

            # =================================================================
            # Analyze environment
            # =================================================================
            environment_analysis = {
                "included": False,
                "environment_name": None,
                "package_count": 0,
                "packages": [],
                "existing_environment": None,
                "can_create": False,
                "status": "none",
                "feature_enabled": AGENT_ENVIRONMENTS_ENABLED
            }
            
            if metadata.get('environment', {}).get('included'):
                env_data = metadata['environment']['data']
                environment_analysis["included"] = True
                environment_analysis["environment_name"] = env_data.get('name')
                environment_analysis["python_version"] = env_data.get('python_version')
                environment_analysis["description"] = env_data.get('description')
                environment_analysis["packages"] = env_data.get('packages', [])
                environment_analysis["package_count"] = len(env_data.get('packages', []))
                
                # Check if environment feature is enabled
                if AGENT_ENVIRONMENTS_ENABLED:
                    # Check if environment with same name exists
                    try:
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
                        
                        cursor.execute("""
                            SELECT environment_id, name, description, status
                            FROM AgentEnvironments
                            WHERE name = ? AND is_deleted = 0
                        """, env_data.get('name'))
                        
                        existing = cursor.fetchone()
                        if existing:
                            environment_analysis["existing_environment"] = {
                                "environment_id": existing.environment_id,
                                "name": existing.name,
                                "description": existing.description,
                                "status": existing.status
                            }
                            environment_analysis["status"] = "exists"
                        else:
                            environment_analysis["can_create"] = True
                            environment_analysis["status"] = "new"
                        
                        cursor.close()
                        conn.close()
                    except Exception as e:
                        logger.error(f"Error checking environment: {e}")
                        environment_analysis["status"] = "error"
                        environment_analysis["error"] = str(e)
                else:
                    environment_analysis["status"] = "feature_disabled"

            analysis["environment"] = environment_analysis
            
            # Check for knowledge documents
            print('Checking knowledge documents...')
            knowledge_docs_dir = os.path.join(agent_root, 'knowledge', 'documents')
            if os.path.exists(knowledge_docs_dir):
                for doc_file in os.listdir(knowledge_docs_dir):
                    if doc_file.endswith('.json'):
                        analysis['knowledge']['documents_found'].append(doc_file)
            
            # Check if agent name already exists
            print('Checking if agent exists...')
            existing_agents = get_all_agents()
            existing_names = [a for a in existing_agents] if existing_agents else []
            if metadata.get('agent', {}).get('name', '') in existing_names:
                analysis['conflicts']['agent_name_exists'] = True
            
            return jsonify(analysis)
            
        finally:
            # Cleanup
            shutil.rmtree(temp_dir)
            
    except Exception as e:
        logger.error(f"Error analyzing agent package: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/import/agent/execute', methods=['POST'])
@cross_origin()
@login_required
def import_agent_execute():
    """Execute the actual agent import with step-by-step progress"""
    try:
        import tempfile
        import shutil
        import json
        import zipfile
        from datetime import datetime
        
        if 'file' not in request.files:
            return jsonify({"status": "error", "message": "No file provided"}), 400
        
        file = request.files['file']
        import_options = json.loads(request.form.get('options', '{}'))
        
        # Create temporary directory for extraction
        temp_dir = tempfile.mkdtemp()
        import_steps = []
        
        def add_step(step_name, status, message, details=None):
            step = {
                "step": step_name,
                "status": status,
                "message": message,
                "timestamp": datetime.now().isoformat()
            }
            if details:
                step["details"] = details
            
            # Check if a step with this name already exists
            existing_step_index = None
            for i, existing_step in enumerate(import_steps):
                if existing_step["step"] == step_name:
                    existing_step_index = i
                    break
            
            if existing_step_index is not None:
                # Replace the existing step with the updated version
                import_steps[existing_step_index] = step
            else:
                # Add new step if it doesn't exist
                import_steps.append(step)
            
            return step
        
        try:
            print('Extracting package...')
            # Step 1: Extract package
            add_step("extract", "processing", "Extracting package...")
            
            zip_path = os.path.join(temp_dir, 'import.zip')
            file.save(zip_path)
            
            extract_dir = os.path.join(temp_dir, 'extracted')
            os.makedirs(extract_dir)
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            
            add_step("extract", "success", "Package extracted successfully")
            
            print('Loading metadata...')
            # Step 2: Load metadata
            add_step("metadata", "processing", "Loading metadata...")
            
            metadata_path = None
            for root, dirs, files in os.walk(extract_dir):
                if 'metadata.json' in files:
                    metadata_path = os.path.join(root, 'metadata.json')
                    agent_root = root
                    break
            
            if not metadata_path:
                add_step("metadata", "error", "metadata.json not found")
                return jsonify({
                    "status": "error",
                    "message": "Invalid package",
                    "steps": import_steps
                }), 400
            
            with open(metadata_path, 'r') as f:
                metadata = json.load(f)
            
            add_step("metadata", "success", f"Metadata loaded (v{metadata.get('version', 'unknown')})")
            
            print('Create/update agent...')
            # Step 3: Create/Update Agent
            add_step("agent", "processing", "Creating agent...")
            
            agent_info = metadata['agent']
            agent_name = agent_info['name']
            
            # Handle name conflicts
            if import_options.get('rename_if_exists', True):
                existing_agents = get_all_agents()
                existing_names = [a for a in existing_agents] if existing_agents else []
                
                if agent_name in existing_names:
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    agent_name = f"{agent_name}_imported_{timestamp}"
                    add_step("agent", "info", f"Renamed agent to avoid conflict: {agent_name}")
            
            # TODO: Consider moving to the last step
            # Create new agent
            agent_id = insert_agent_with_tools(
                agent_name,
                agent_info['objective'],
                agent_info.get('enabled', True),
                [],
                []
            )
            
            if not agent_id:
                add_step("agent", "error", "Failed to create agent")
                return jsonify({
                    "status": "error",
                    "message": "Failed to create agent",
                    "steps": import_steps
                }), 500
            
            add_step("agent", "success", f"Agent created successfully (ID: {agent_id})", {"agent_id": agent_id, "name": agent_name})
            
            print('Import custom tools...')
            # Step 4: Import Custom Tools
            custom_tools_imported = []
            custom_tools_skipped = []
            custom_tools_failed = []
            
            custom_tools_dir = os.path.join(agent_root, 'custom_tools')
            if os.path.exists(custom_tools_dir) and metadata.get('tools', {}).get('custom_tools', []):
                add_step("custom_tools", "processing", f"Importing {len(metadata['tools']['custom_tools'])} custom tools...")
                
                for tool_name in os.listdir(custom_tools_dir):
                    if not os.path.isdir(os.path.join(custom_tools_dir, tool_name)):
                        continue
                    
                    try:
                        tool_source = os.path.join(custom_tools_dir, tool_name)
                        tool_dest = os.path.join(cfg.CUSTOM_TOOLS_FOLDER, tool_name)
                        
                        # Handle conflicts
                        if os.path.exists(tool_dest):
                            if import_options.get('overwrite_tools', False):
                                shutil.rmtree(tool_dest)
                                shutil.copytree(tool_source, tool_dest)
                                custom_tools_imported.append(tool_name)
                            else:
                                custom_tools_skipped.append(tool_name)
                        else:
                            shutil.copytree(tool_source, tool_dest)
                            custom_tools_imported.append(tool_name)
                            
                    except Exception as e:
                        custom_tools_failed.append({"tool": tool_name, "error": str(e)})
                        logger.error(f"Failed to import tool {tool_name}: {str(e)}")
                
                add_step("custom_tools", "success", 
                        f"Custom tools processed: {len(custom_tools_imported)} imported, {len(custom_tools_skipped)} skipped",
                        {
                            "imported": custom_tools_imported,
                            "skipped": custom_tools_skipped,
                            "failed": custom_tools_failed
                        })
            
            print('Adding tools...')
            # Step 5: Add Core Tools
            core_tools = metadata.get('tools', {}).get('core_tools', [])
            if core_tools:
                add_step("core_tools", "processing", f"Adding {len(core_tools)} core tools...")
                
                core_tools_added = []
                core_tools_failed = []
                
                for tool_name in core_tools:
                    try:
                        #add_agent_tool(agent_id, tool_name, False)
                        core_tools_added.append(tool_name)
                    except Exception as e:
                        core_tools_failed.append({"tool": tool_name, "error": str(e)})
                
                add_step("core_tools", "success", 
                        f"Core tools added: {len(core_tools_added)} successful",
                        {"added": core_tools_added, "failed": core_tools_failed})
            
            # Step 6: Add Custom Tools to Agent
            if custom_tools_imported:
                add_step("link_tools", "processing", "Linking custom tools to agent...")
                
                linked_tools = []
                for tool_name in custom_tools_imported:
                    try:
                        #add_agent_tool(agent_id, tool_name, True)
                        linked_tools.append(tool_name)
                    except Exception as e:
                        logger.error(f"Failed to link tool {tool_name}: {str(e)}")

                add_step("link_tools", "success", f"Linked {len(linked_tools)} custom tools to agent")

            # Update agent tools after import steps
            if core_tools or custom_tools_imported:
                try:
                    agent_id = update_agent_with_tools(
                        agent_id,
                        agent_name,
                        agent_info['objective'],
                        agent_info.get('enabled', True),
                        metadata.get('tools', {}).get('custom_tools', []),
                        metadata.get('tools', {}).get('core_tools', [])
                        )
                    if agent_id:
                        print('Successfully updated agent tools')
                except Exception as e:
                    logger.error(f"Failed to link tools {tool_name}: {str(e)}")
            
            print('Importing knowledge...')
            # Step 7: Import Knowledge/Documents
            knowledge_imported = []
            knowledge_failed = []
            
            if metadata.get('knowledge', {}).get('items', []):
                add_step("knowledge", "processing", f"Importing {len(metadata['knowledge']['items'])} knowledge items...")
                
                knowledge_docs_dir = os.path.join(agent_root, 'knowledge', 'documents')
                
                for item in metadata['knowledge']['items']:
                    if item.get('document_exported') and item.get('document_filename'):
                        try:
                            doc_path = os.path.join(knowledge_docs_dir, item['document_filename'])
                            
                            if os.path.exists(doc_path):
                                with open(doc_path, 'r', encoding='utf-8') as f:
                                    doc_data = json.load(f)
                                
                                # Import the document
                                new_doc_id = import_document(doc_data)
                                
                                if new_doc_id:
                                    # Add knowledge entry
                                    knowledge_id = add_agent_knowledge(
                                        agent_id=agent_id,
                                        document_id=new_doc_id,
                                        description=item.get('description', '')
                                    )
                                    
                                    if knowledge_id:
                                        knowledge_imported.append({
                                            'description': item.get('description', ''),
                                            'document_id': new_doc_id
                                        })
                                    else:
                                        knowledge_failed.append({
                                            'description': item.get('description', ''),
                                            'error': 'Failed to add knowledge entry'
                                        })
                                else:
                                    knowledge_failed.append({
                                        'description': item.get('description', ''),
                                        'error': 'Failed to import document'
                                    })
                        except Exception as e:
                            knowledge_failed.append({
                                'description': item.get('description', ''),
                                'error': str(e)
                            })
                
                add_step("knowledge", "success", 
                        f"Knowledge import complete: {len(knowledge_imported)} items imported",
                        {"imported": knowledge_imported, "failed": knowledge_failed})

            # =================================================================
            # Step 8: Import Environment (if included and requested)
            # =================================================================
            print('Importing environment...')
            environment_imported = False
            environment_id = None
            
            if metadata.get('environment', {}).get('included') and import_options.get('import_environment', True):
                env_data = metadata['environment']['data']
                add_step("environment", "processing", f"Processing environment '{env_data.get('name')}'...")
                
                if AGENT_ENVIRONMENTS_ENABLED:
                    try:
                        # Check import option for environment handling
                        env_action = import_options.get('environment_action', 'create_or_reuse')
                        
                        if env_action == 'skip':
                            add_step("environment", "info", "Environment import skipped by user")
                        else:
                            # Check if environment with same name exists
                            existing_env = check_environment_exists_by_name(env_data.get('name'))
                            
                            if existing_env and env_action in ['create_or_reuse', 'reuse_only']:
                                # Use existing environment
                                environment_id = existing_env['environment_id']
                                environment_imported = True
                                add_step("environment", "success", 
                                        f"Using existing environment '{env_data.get('name')}'",
                                        {"environment_id": environment_id, "action": "reused"})
                            
                            elif not existing_env and env_action in ['create_or_reuse', 'create_only']:
                                # Create new environment
                                success, env_id, message = create_environment_from_import(
                                    env_data, 
                                    current_user.id
                                )
                                
                                if success:
                                    environment_id = env_id
                                    environment_imported = True
                                    add_step("environment", "success", 
                                            f"Created environment '{env_data.get('name')}': {message}",
                                            {"environment_id": environment_id, "action": "created"})
                                else:
                                    add_step("environment", "warning", 
                                            f"Failed to create environment: {message}",
                                            {"action": "failed"})
                            
                            elif existing_env and env_action == 'create_only':
                                add_step("environment", "info", 
                                        f"Environment '{env_data.get('name')}' exists, skipping (create_only mode)",
                                        {"existing": True})
                            
                            elif not existing_env and env_action == 'reuse_only':
                                add_step("environment", "info", 
                                        f"Environment '{env_data.get('name')}' not found, skipping (reuse_only mode)",
                                        {"existing": False})
                                        
                    except Exception as e:
                        logger.error(f"Error processing environment: {e}")
                        add_step("environment", "warning", f"Environment processing error: {str(e)}")
                else:
                    add_step("environment", "info", "Environment feature not enabled, skipping")
            
            # =================================================================
            # Step 9: Assign Environment to Agent
            # =================================================================
            if environment_imported and environment_id and agent_id:
                add_step("assign_environment", "processing", "Assigning environment to agent...")
                
                try:
                    success, message = assign_environment_to_imported_agent(
                        agent_id, 
                        environment_id, 
                        current_user.id
                    )
                    
                    if success:
                        add_step("assign_environment", "success", message)
                    else:
                        add_step("assign_environment", "warning", f"Failed to assign: {message}")
                except Exception as e:
                    add_step("assign_environment", "warning", f"Assignment error: {str(e)}")
            
            # Step 10: Finalize
            add_step("finalize", "processing", "Finalizing import...")
            
            # Reload agents
            #global active_agents
            #active_agents = load_agents()

            # Before reload
            logger.info(f"active_agents before reload: {type(active_agents)}, keys: {list(active_agents.keys()) if active_agents else 'None'}")

            load_agents(agent_id=agent_id)

            # After reload
            logger.info(f"active_agents after reload: {list(active_agents.keys())}")
            logger.info(f"New agent {agent_id} loaded: {agent_id in active_agents}")
            
            add_step("finalize", "success", "Import completed successfully!")
            
            # Prepare final response
            return jsonify({
                "status": "success",
                "message": "Agent imported successfully",
                "agent_id": agent_id,
                "agent_name": agent_name,
                "steps": import_steps,
                "summary": {
                    "agent_created": True,
                    "custom_tools_imported": len(custom_tools_imported),
                    "custom_tools_skipped": len(custom_tools_skipped),
                    "core_tools_added": len(core_tools),
                    "knowledge_items_imported": len(knowledge_imported),
                    "environment_imported": environment_imported,
                    "environment_id": environment_id,
                    "total_time": (datetime.now() - datetime.fromisoformat(import_steps[0]['timestamp'])).total_seconds()
                }
            })
            
        except Exception as e:
            add_step("error", "error", f"Import failed: {str(e)}")
            logger.error(f"Error during import: {str(e)}")
            return jsonify({
                "status": "error",
                "message": str(e),
                "steps": import_steps
            }), 500
            
        finally:
            # Cleanup temporary directory
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
                
    except Exception as e:
        logger.error(f"Error importing agent: {str(e)}")
        try:
            capture_exception(e, {'agent_id': agent_id})
        except:
            pass
        return jsonify({"status": "error", "message": str(e)}), 500
    



######################################################
# AGENT ENVIRONMENT ROUTES
######################################################
@app.route('/api/agents/<int:agent_id>/environment', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_agent_environment_setting(agent_id):
    """Get the environment assigned to an agent"""
    try:
        if not AGENT_ENVIRONMENTS_ENABLED:
            return jsonify({'status': 'error', 'message': 'Feature not enabled'}), 403
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get assigned environment
        cursor.execute("""
            SELECT 
                a.environment_id,
                e.name,
                e.description,
                e.status
            FROM AgentEnvironmentAssignments a
            INNER JOIN AgentEnvironments e ON a.environment_id = e.environment_id
            WHERE a.agent_id = ? AND a.is_active = 1 AND e.is_deleted = 0
        """, agent_id)
        
        row = cursor.fetchone()
        
        if row:
            return jsonify({
                'status': 'success',
                'environment': {
                    'environment_id': row.environment_id,
                    'name': row.name,
                    'description': row.description,
                    'status': row.status
                }
            })
        else:
            return jsonify({
                'status': 'success',
                'environment': None
            })
            
    except Exception as e:
        logger.error(f"Error getting agent environment: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if 'conn' in locals():
            conn.close()

@app.route('/api/agents/<int:agent_id>/environment', methods=['POST'])
@api_key_or_session_required(min_role=2)
def assign_agent_environment(agent_id):
    """Assign an environment to an agent"""
    try:
        if not AGENT_ENVIRONMENTS_ENABLED:
            return jsonify({'status': 'error', 'message': 'Feature not enabled'}), 403
        
        data = request.get_json()
        environment_id = data.get('environment_id')
        
        if not environment_id:
            # Remove environment assignment
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            cursor.execute("""
                UPDATE AgentEnvironmentAssignments 
                SET is_active = 0 
                WHERE agent_id = ? AND is_active = 1
            """, agent_id)
            conn.commit()
            conn.close()
            return jsonify({'status': 'success', 'message': 'Environment unassigned'})
        
        # Use the environment manager to assign
        from agent_environments import AgentEnvironmentManager
        
        tenant_id = os.getenv('API_KEY')
        
        manager = AgentEnvironmentManager(tenant_id)
        success, message = manager.assign_environment_to_agent(
            environment_id, 
            agent_id, 
            current_user.id
        )
        
        return jsonify({
            'status': 'success' if success else 'error',
            'message': message
        })
        
    except Exception as e:
        logger.error(f"Error assigning environment: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

# Update your existing add/edit agent route to handle environment assignment
# In your existing add_agent or update_agent routes, add:
def save_agent_with_environment(agent_id, environment_id=None):
    """Helper function to save agent with optional environment"""
    try:
        if AGENT_ENVIRONMENTS_ENABLED and environment_id:
            from agent_environments import AgentEnvironmentManager
            
            tenant_id = os.getenv('API_KEY')
            connection_string = app.config['DB_CONNECTION_STRING']
            
            manager = AgentEnvironmentManager(tenant_id)
            manager.assign_environment_to_agent(
                environment_id, 
                agent_id, 
                current_user.id
            )
    except Exception as e:
        logger.warning(f"Failed to assign environment to agent: {e}")

def refresh_tenant_features():
    """Refresh feature flags from cloud (call periodically or on admin action)"""
    global AGENT_ENVIRONMENTS_ENABLED, AGENT_ENVIRONMENTS_SETTINGS
    
    try:
        from agent_environments.cloud_config_manager import CloudConfigManager
        
        connection_string = app.config.get('DB_CONNECTION_STRING')
        tenant_id = app.config.get('TENANT_ID', 0)
        
        if tenant_id > 0:
            config_manager = CloudConfigManager(tenant_id)
            settings = config_manager.get_tenant_settings(force_refresh=True)
            
            # Update global flags
            AGENT_ENVIRONMENTS_ENABLED = settings.get('environments_enabled', False)
            AGENT_ENVIRONMENTS_SETTINGS = settings
            
            # Update app config
            app.config['AGENT_ENVIRONMENTS_ENABLED'] = AGENT_ENVIRONMENTS_ENABLED
            app.config['AGENT_ENVIRONMENTS_SETTINGS'] = AGENT_ENVIRONMENTS_SETTINGS
            
            print(f"[INFO] Refreshed tenant features - Environments: {AGENT_ENVIRONMENTS_ENABLED}")
    except Exception as e:
        print(f"Error refreshing tenant features: {e}")

@app.route('/admin/refresh-features')
@login_required
def refresh_features():
    if current_user.role != 3:  # Admin only
        return "Unauthorized", 403
    
    refresh_tenant_features()
    flash('Feature flags refreshed from cloud', 'success')
    return redirect(url_for('home'))

@app.route('/api/environments/status')
@api_key_or_session_required(min_role=2)
def check_environments_status():
    """Check if Agent Environments module is loaded"""
    return jsonify({
        'enabled': AGENT_ENVIRONMENTS_ENABLED,
        'message': 'Agent Environments module is active' if AGENT_ENVIRONMENTS_ENABLED else 'Module not loaded'
    })

@app.route('/api/debug/blueprints')
@developer_required(api=True)
def list_blueprints():
    """Debug route to list all registered blueprints"""
    blueprints = []
    for name, blueprint in app.blueprints.items():
        blueprints.append({
            'name': name,
            'url_prefix': blueprint.url_prefix,
            'import_name': blueprint.import_name
        })
    return jsonify({'blueprints': blueprints})


@app.route('/environments/assignments')
@developer_required()
def environment_assignments():
    """Render the agent-environment assignment management page"""
    
    # Check if environments feature is enabled
    if not AGENT_ENVIRONMENTS_ENABLED:
        flash('Agent Environments feature is not enabled for your account', 'warning')
        return redirect(url_for('index'))
    
    # Check user permissions (developer or admin)
    if current_user.role < 2:  # Assuming role 2+ is developer/admin
        flash('You do not have permission to access this page', 'danger')
        return redirect(url_for('index'))
    
    context = {
        'user_role': current_user.role,
        'is_admin': current_user.role == 3,
        'tenant_id': os.getenv('API_KEY')
    }
    
    return render_template('agent_environment_assignments.html', **context)


@app.route("/api/connection-types", methods=['GET'])
def get_connection_types():
    """API endpoint to get all connection types with icons"""
    types = get_connection_types_with_icons()
    return jsonify(types)


@app.route("/api/available-icons", methods=['GET'])
def get_available_icons():
    """List all available icon files in static/icons"""
    icons_folder = os.path.join(app.static_folder, 'icons')
    icons = []
    
    if os.path.exists(icons_folder):
        for file in os.listdir(icons_folder):
            if file.lower().endswith(('.png', '.svg', '.jpg', '.jpeg', '.ico')):
                icons.append({
                    'filename': file,
                    'path': f'/static/icons/{file}',
                    'name': os.path.splitext(file)[0]
                })
    
    return jsonify({'icons': icons, 'count': len(icons)})


@app.route('/approvals')
@cross_origin()
@login_required
def approvals():
    """Display the approvals page"""
    return render_template('approvals.html')


# API endpoints for approval management
@app.route('/api/workflow/assignees', methods=['GET'])
@cross_origin()
@developer_required(api=True)
def get_workflow_assignees():
    """Get available users and groups for workflow assignment"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        result = {
            'users': [],
            'groups': []
        }
        
        # Get users with End User role or higher (permissions 1, 2, 3)
        cursor.execute("""
            SELECT id, user_name, name, role AS permissions
            FROM [User] 
            WHERE role IN (1, 2, 3)
            ORDER BY name
        """)
        
        for row in cursor.fetchall():
            role_map = {1: 'End User', 2: 'Developer', 3: 'Admin'}
            result['users'].append({
                'id': row[0],
                'username': row[1],
                'name': row[2],
                'role': role_map.get(row[3], 'Unknown'),
                'value': f"user:{row[0]}",
                'label': f"{row[2]} ({row[1]}) - {role_map.get(row[3], 'Unknown')}"
            })
        
        # Get all active groups with member count
        cursor.execute("""
            SELECT g.id AS group_id, g.group_name,
                   COUNT(ug.user_id) as member_count
            FROM [Groups] g
            LEFT JOIN [UserGroups] ug ON g.id = ug.group_id
            GROUP BY g.id, g.group_name
            ORDER BY g.group_name
        """)
        
        for row in cursor.fetchall():
            result['groups'].append({
                'id': row[0],
                'name': row[1],
                'memberCount': row[2],
                'value': f"group:{row[0]}",
                'label': f"{row[1]} ({row[2]} members)"
            })
        
        cursor.close()
        conn.close()
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error getting assignees: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500



import datetime
import json
from decimal import Decimal

@app.route('/api/workflow/user-approvals', methods=['GET'])
@cross_origin()
def get_user_approvals():
    """Get approval requests for the current user including group assignments"""
    try:
        # Get current user (implement your authentication logic)
        user_id = current_user.id
        if not user_id:
            return jsonify({
                "status": "error",
                "message": "User not authenticated"
            }), 401
        
        # Parse filters
        status_filter = request.args.get('status', 'pending')
        assignment_filter = request.args.get('assignment', 'all')
        date_filter = request.args.get('dateRange', 'all')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Build the query
        query = """
            SELECT DISTINCT
                ar.request_id,
                ar.title,
                ar.description,
                ar.status,
                ar.requested_at,
                ar.response_at,
                ar.due_date,
                ar.priority,
                ar.approval_data,
                ar.comments,
                ar.responded_by,
                se.node_name,
                se.node_type,
                we.workflow_name,
                we.execution_id,
                we.started_at as execution_started_at,
                CASE 
                    WHEN ar.assigned_to_type = 'user' AND ar.assigned_to_id = ? THEN 'Direct'
                    WHEN ar.assigned_to_type = 'group' THEN 'Group: ' + g.group_name
                    WHEN ar.assigned_to_type = 'unassigned' OR ar.assigned_to_type IS NULL THEN 'Available to All'
                    ELSE 'Unassigned'
                END as assignment_type
            FROM ApprovalRequests ar
            JOIN StepExecutions se ON ar.step_execution_id = se.step_execution_id
            JOIN WorkflowExecutions we ON se.execution_id = we.execution_id
            LEFT JOIN [Groups] g ON ar.assigned_to_type = 'group' AND ar.assigned_to_id = g.id
            LEFT JOIN [UserGroups] ug ON ar.assigned_to_type = 'group' AND ar.assigned_to_id = ug.group_id
            WHERE 1=1
        """
        
        params = [user_id]
        
        # Apply status filter
        if status_filter != 'all':
            query += " AND ar.status = ?"
            params.append(status_filter.title())
        
        # Apply assignment filter
        if assignment_filter == 'direct':
            query += " AND ar.assigned_to_type = 'user' AND ar.assigned_to_id = ?"
            params.append(user_id)
        elif assignment_filter == 'group':
            query += " AND ar.assigned_to_type = 'group' AND ug.user_id = ?"
            params.append(user_id)
        else:
            # All assignments - user can see direct, group, and unassigned
            query += """ AND (
                (ar.assigned_to_type = 'user' AND ar.assigned_to_id = ?)
                OR (ar.assigned_to_type = 'group' AND ug.user_id = ?)
                OR ar.assigned_to_type = 'unassigned'
                OR ar.assigned_to_type IS NULL
            )"""
            params.extend([user_id, user_id])
        
        # Apply date filter
        if date_filter == 'today':
            query += " AND CAST(ar.requested_at AS DATE) = CAST(getutcdate() AS DATE)"
        elif date_filter == 'week':
            query += " AND ar.requested_at >= DATEADD(DAY, -7, getutcdate())"
        elif date_filter == 'month':
            query += " AND ar.requested_at >= DATEADD(MONTH, -1, getutcdate())"
        
        query += " ORDER BY ar.priority DESC, ar.requested_at DESC"
        
        cursor.execute(query, *params)
        
        approvals = []
        for row in cursor.fetchall():
            approval = {}
            for i, column in enumerate(cursor.description):
                value = row[i]
                # Fix: Use datetime.datetime instead of just datetime
                if isinstance(value, datetime.datetime):
                    value = value.isoformat()
                # Handle Decimal objects
                elif isinstance(value, Decimal):
                    value = float(value)
                # Handle UUID objects
                elif hasattr(value, 'hex'):
                    value = str(value)
                approval[column[0]] = value
            approvals.append(approval)
        
        # Get statistics
        stats_query = """
            SELECT 
                SUM(CASE WHEN ar.status = 'Pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN ar.status = 'Approved' AND 
                    CAST(ar.response_at AS DATE) = CAST(getutcdate() AS DATE) 
                    THEN 1 ELSE 0 END) as approved_today,
                SUM(CASE WHEN ar.status = 'Rejected' AND 
                    CAST(ar.response_at AS DATE) = CAST(getutcdate() AS DATE) 
                    THEN 1 ELSE 0 END) as rejected_today,
                SUM(CASE WHEN ar.status = 'Pending' AND 
                    ar.due_date < getutcdate() 
                    THEN 1 ELSE 0 END) as overdue
            FROM ApprovalRequests ar
            LEFT JOIN [UserGroups] ug ON ar.assigned_to_type = 'group' AND ar.assigned_to_id = ug.group_id
            WHERE (
                (ar.assigned_to_type = 'user' AND ar.assigned_to_id = ?)
                OR (ar.assigned_to_type = 'group' AND ug.user_id = ?)
                OR ar.assigned_to_type = 'unassigned'
                OR ar.assigned_to_type IS NULL
            )
        """
        
        cursor.execute(stats_query, user_id, user_id)
        stats_row = cursor.fetchone()
        
        statistics = {
            'pending': int(stats_row[0] or 0),
            'approved_today': int(stats_row[1] or 0),
            'rejected_today': int(stats_row[2] or 0),
            'overdue': int(stats_row[3] or 0)
        }
        
        cursor.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "approvals": approvals,
            "statistics": statistics
        })
        
    except Exception as e:
        logger.error(f"Error getting user approvals: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({
            "status": "error",
            "message": str(e),
            "approvals": [],
            "statistics": {
                'pending': 0,
                'approved_today': 0,
                'rejected_today': 0,
                'overdue': 0
            }
        }), 500

@app.route('/api/current-user', methods=['GET'])
@cross_origin()
def get_current_user_w_role():
    """Get current authenticated user information"""
    try:
        if current_user.is_authenticated:
            return jsonify({
                'id': current_user.id,
                'username': current_user.username,
                'name': current_user.name,
                'role': current_user.role,
                'email': current_user.email if hasattr(current_user, 'email') else None
            })
        
        return jsonify({'error': 'Not authenticated'}), 401
        
    except Exception as e:
        logger.error(f"Error getting current user: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/workflow/approvals/bulk', methods=['POST'])
@cross_origin()
def process_bulk_approvals():
    """Process multiple approval requests at once"""
    try:
        data = request.json
        if not data or 'approvals' not in data:
            return jsonify({
                "status": "error",
                "message": "Missing approvals data"
            }), 400
        
        approvals = data['approvals']
        user_id = session.get('user_id') or data.get('user_id')
        
        if not user_id:
            return jsonify({
                "status": "error",
                "message": "User not authenticated"
            }), 401
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        results = []
        for approval in approvals:
            request_id = approval.get('request_id')
            status = approval.get('status')
            comments = approval.get('comments', '')
            
            if not request_id or status not in ['approved', 'rejected']:
                results.append({
                    'request_id': request_id,
                    'success': False,
                    'error': 'Invalid parameters'
                })
                continue
            
            try:
                # Update the approval request
                cursor.execute("""
                    UPDATE ApprovalRequests
                    SET status = ?, 
                        response_at = getutcdate(),
                        responded_by = ?,
                        comments = ?
                    WHERE request_id = ? AND status = 'Pending'
                """, status.title(), user_id, comments, request_id)
                
                if cursor.rowcount > 0:
                    conn.commit()
                    results.append({
                        'request_id': request_id,
                        'success': True
                    })
                else:
                    results.append({
                        'request_id': request_id,
                        'success': False,
                        'error': 'Request not found or already processed'
                    })
                    
            except Exception as e:
                results.append({
                    'request_id': request_id,
                    'success': False,
                    'error': str(e)
                })
        
        cursor.close()
        conn.close()
        
        return jsonify({
            "status": "success",
            "results": results
        })
        
    except Exception as e:
        logger.error(f"Error processing bulk approvals: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


#############################################################################
# ENHANCED DATA DICTIONARY ROUTES
#############################################################################
import json
from ai_metadata_generator import (
    discover_tables_from_database,
    analyze_table_with_ai,
    query_app_database,
    execute_app_database_command
)
from AppUtils import execute_sql_query_v2
from DataUtils import get_database_connection_string

# =============================================
# TABLE MANAGEMENT ROUTES
# =============================================

@app.route('/api/tables/<int:connection_id>')
@api_key_or_session_required(min_role=2)
def get_tables_api(connection_id):
    """Get all tables with enhanced metadata for a connection."""
    try:
        query = """
            SELECT 
                id, table_name, table_alias, table_description,
                table_type, table_category, primary_key_columns,
                business_rules, row_count_estimate, refresh_frequency,
                data_owner, is_deprecated, related_tables, common_filters
            FROM llm_Tables
            WHERE connection_id = ?
            ORDER BY table_name
        """
        
        # Query app database with tenant context
        tables = query_app_database(query, (connection_id,))
        
        return jsonify({'tables': tables})
        
    except Exception as e:
        logger.error(f"Error in get_tables_api: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/connection/stats/<int:connection_id>')
@api_key_or_session_required(min_role=2)
def get_connection_stats(connection_id):
    """Get statistics for a connection (table count, column count, etc)."""
    try:
        # Get total column count for this connection
        query = """
            SELECT COUNT(*) as column_count
            FROM llm_Columns c
            INNER JOIN llm_Tables t ON c.table_id = t.id
            WHERE t.connection_id = ?
        """
        
        result = query_app_database(query, (connection_id,))
        column_count = result[0]['column_count'] if result else 0
        
        # Get table count
        table_query = "SELECT COUNT(*) as table_count FROM llm_Tables WHERE connection_id = ?"
        table_result = query_app_database(table_query, (connection_id,))
        table_count = table_result[0]['table_count'] if table_result else 0
        
        return jsonify({
            'success': True,
            'column_count': column_count,
            'table_count': table_count
        })
        
    except Exception as e:
        logger.error(f"Error in get_connection_stats: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/table/update', methods=['POST'])
@developer_required(api=True)
def update_table():
    try:
        print('Updating table...')

        data = request.get_json()
        table_id = data.get('id')

        print(f'Table ID: {table_id}')
        
        if table_id is None or table_id == '':
            print('Inserting table...')
            # CREATE MODE - New table
            # Required fields for creation
            connection_id = data.get('connection_id')
            table_name = data.get('table_name')
            table_schema = data.get('table_schema', 'public')
            
            if not connection_id or not table_name:
                print('Connection_id and table_name are required for new tables')
                return jsonify({'error': 'connection_id and table_name are required for new tables'}), 400

            result = query_app_database("SELECT id FROM llm_Tables WHERE connection_id = ? AND table_name = ? AND table_schema = ?", (connection_id, table_name, table_schema))
            
            # Check if table already exists
            existing = result[0]['id'] if result else None
            
            if existing:
                print('Table already exists in the data dictionary')
                return jsonify({'error': 'Table already exists in the data dictionary'}), 400

            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Set tenant context - CRITICAL for RLS
            api_key = os.getenv('API_KEY')
            cursor.execute("EXEC tenant.sp_setTenantContext ?", api_key)
            
            # Insert new table
            cursor.execute("""
                INSERT INTO llm_Tables (
                    connection_id, table_name, table_schema, table_type, 
                    table_description, table_alias, table_category,
                    primary_key_columns, business_rules, row_count_estimate,
                    refresh_frequency, data_owner, is_deprecated,
                    related_tables, common_filters, record_create_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, getutcdate())
            """, (
                connection_id,
                table_name,
                table_schema,
                data.get('table_type'),
                data.get('table_description', ''),
                data.get('table_alias'),
                data.get('table_category'),
                data.get('primary_key_columns'),
                data.get('business_rules'),
                data.get('row_count_estimate'),
                data.get('refresh_frequency'),
                data.get('data_owner'),
                data.get('is_deprecated', 0),
                data.get('related_tables'),
                data.get('common_filters')
            ))

            cursor.execute("SELECT @@IDENTITY")
            new_id = cursor.fetchone()[0]

            conn.commit()
            conn.close()
            
            return jsonify({'success': True, 'id': new_id, 'message': 'Table created successfully'}), 201
            
        else:
            print('Updating table...')
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Set tenant context - CRITICAL for RLS
            api_key = os.getenv('API_KEY')
            cursor.execute("EXEC tenant.sp_setTenantContext ?", api_key)

            # UPDATE MODE - Existing table
            cursor.execute("""
                UPDATE llm_Tables SET
                    table_alias = ?,
                    table_description = ?,
                    table_type = ?,
                    table_category = ?,
                    primary_key_columns = ?,
                    business_rules = ?,
                    row_count_estimate = ?,
                    refresh_frequency = ?,
                    data_owner = ?,
                    is_deprecated = ?,
                    related_tables = ?,
                    common_filters = ?
                WHERE id = ?
            """, (
                data.get('table_alias'),
                data.get('table_description'),
                data.get('table_type'),
                data.get('table_category'),
                data.get('primary_key_columns'),
                data.get('business_rules'),
                data.get('row_count_estimate'),
                data.get('refresh_frequency'),
                data.get('data_owner'),
                data.get('is_deprecated', 0),
                data.get('related_tables'),
                data.get('common_filters'),
                table_id
            ))

            # Check affected rows before closing connection
            if cursor.rowcount == 0:
                conn.rollback()  # optional, but clean
                conn.close()
                return jsonify({'error': 'Table not found'}), 404

            conn.commit()
            conn.close()
                
            return jsonify({'success': True, 'message': 'Table updated successfully'}), 200
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    

# =============================================
# COLUMN MANAGEMENT ROUTES
# =============================================

@app.route('/api/columns/<int:table_id>')
@api_key_or_session_required(min_role=2)
def get_columns_api(table_id):
    """Get all columns with enhanced metadata for a table."""
    try:
        query = """
            SELECT 
                id, column_name, column_alias, column_description,
                data_type, data_type_precision, is_primary_key, is_foreign_key,
                foreign_key_table, foreign_key_column, is_nullable, default_value,
                is_calculated, calculation_formula, calculation_dependencies,
                value_format, value_range, common_aggregations, semantic_type,
                units, is_sensitive, synonyms, examples, column_values
            FROM llm_Columns
            WHERE table_id = ?
            ORDER BY id
        """
        
        # Query app database with tenant context
        columns = query_app_database(query, (table_id,))
        
        return jsonify({'columns': columns})
        
    except Exception as e:
        logger.error(f"Error in get_columns_api: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/column/update', methods=['POST'])
@developer_required(api=True)
def update_column():
    try:
        print('Saving column...')
        data = request.get_json()
        column_id = data.get('id')
        
        if column_id is None or column_id == '':
            # CREATE MODE - New column
            # Required fields for creation
            table_id = data.get('table_id')
            column_name = data.get('column_name')
            
            if not table_id or not column_name:
                return jsonify({'error': 'table_id and column_name are required for new columns'}), 400
            
            # Set tenant context - CRITICAL for RLS
            api_key = os.getenv('API_KEY')
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    # Set tenant context - CRITICAL for RLS
                    cursor.execute("EXEC tenant.sp_setTenantContext ?", (api_key,))

                    # Verify table exists (correct table!)
                    cursor.execute("SELECT 1 FROM llm_Tables WHERE id = ?", (table_id,))
                    row = cursor.fetchone()
                    if not row:
                        return jsonify({'error': 'Table not found'}), 404

                    # Check if column already exists for this table
                    cursor.execute(
                        "SELECT 1 FROM llm_Columns WHERE table_id = ? AND column_name = ?",
                        (table_id, column_name)
                    )
                    existing = cursor.fetchone()
                    if existing:
                        return jsonify({'error': 'Column already exists for this table'}), 400
            
            # Insert new column
            cursor.execute("""
                INSERT INTO llm_Columns (
                    table_id, column_name, data_type, column_alias,
                    column_description, data_type_precision, is_primary_key,
                    is_foreign_key, foreign_key_table, foreign_key_column,
                    is_nullable, default_value, is_calculated, calculation_formula,
                    calculation_dependencies, semantic_type, value_format,
                    units, value_range, common_aggregations, is_sensitive,
                    synonyms, examples, record_create_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, getutcdate())
            """, (
                table_id,
                column_name,
                data.get('data_type', 'VARCHAR'),
                data.get('column_alias'),
                data.get('column_description', ''),
                data.get('data_type_precision'),
                data.get('is_primary_key', 0),
                data.get('is_foreign_key', 0),
                data.get('foreign_key_table'),
                data.get('foreign_key_column'),
                data.get('is_nullable', 1),
                data.get('default_value'),
                data.get('is_calculated', 0),
                data.get('calculation_formula'),
                data.get('calculation_dependencies'),
                data.get('semantic_type'),
                data.get('value_format'),
                data.get('units'),
                data.get('value_range'),
                data.get('common_aggregations'),
                data.get('is_sensitive', 0),
                data.get('synonyms'),
                data.get('examples')
            ))
            cursor.execute("SELECT @@IDENTITY")
            new_id = cursor.fetchone()[0]

            conn.commit()
            conn.close()
            
            return jsonify({'success': True, 'id': new_id, 'message': 'Column created successfully'}), 201
        else:
            if not column_id:
                return jsonify({'error': 'column_id is required'}), 400

            # Prepare fields (ensure correct types / serialization)
            def as_int_flag(v, default=0):
                # Accepts True/False/"0"/"1"/0/1/None
                if v is None:
                    return default
                return int(bool(int(v))) if isinstance(v, str) and v.isdigit() else int(bool(v))

            is_primary_key    = as_int_flag(data.get('is_primary_key'), 0)
            is_foreign_key    = as_int_flag(data.get('is_foreign_key'), 0)
            is_nullable       = as_int_flag(data.get('is_nullable'), 1)
            is_calculated     = as_int_flag(data.get('is_calculated'), 0)
            is_sensitive      = as_int_flag(data.get('is_sensitive'), 0)

            # If these may come in as dict/list, store as JSON text (optional)
            def maybe_json(v):
                if isinstance(v, (dict, list)):
                    return json.dumps(v)
                return v

            calculation_dependencies = maybe_json(data.get('calculation_dependencies'))
            common_aggregations      = maybe_json(data.get('common_aggregations'))
            synonyms                 = maybe_json(data.get('synonyms'))
            examples                 = maybe_json(data.get('examples'))
            value_range              = maybe_json(data.get('value_range'))

            api_key = os.getenv('API_KEY')

            sql = """
            UPDATE llm_Columns
            SET
                column_alias = ?,
                column_description = ?,
                data_type = ?,
                data_type_precision = ?,
                is_primary_key = ?,
                is_foreign_key = ?,
                foreign_key_table = ?,
                foreign_key_column = ?,
                is_nullable = ?,
                default_value = ?,
                is_calculated = ?,
                calculation_formula = ?,
                calculation_dependencies = ?,
                semantic_type = ?,
                value_format = ?,
                units = ?,
                value_range = ?,
                common_aggregations = ?,
                is_sensitive = ?,
                synonyms = ?,
                examples = ?
            WHERE id = ?
            """

            params = (
                data.get('column_alias'),
                data.get('column_description'),
                data.get('data_type'),
                data.get('data_type_precision'),
                is_primary_key,
                is_foreign_key,
                data.get('foreign_key_table'),
                data.get('foreign_key_column'),
                is_nullable,
                data.get('default_value'),
                is_calculated,
                data.get('calculation_formula'),
                calculation_dependencies,
                data.get('semantic_type'),
                data.get('value_format'),
                data.get('units'),
                value_range,
                common_aggregations,
                is_sensitive,
                synonyms,
                examples,
                column_id
            )

            try:
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        # Tenant context (pass as a tuple!)
                        cursor.execute("EXEC tenant.sp_setTenantContext ?", (api_key,))

                        cursor.execute(sql, params)

                        # Check affected rows before commit/close
                        if cursor.rowcount == 0:
                            conn.rollback()  # not strictly required, but tidy
                            return jsonify({'error': 'Column not found'}), 404

                        conn.commit()
                return jsonify({'success': True, 'message': 'Column updated successfully'}), 200
            except Exception as e:
                # Optionally log e
                return jsonify({'error': 'Unexpected error while updating column'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    

# =============================================
# AI DISCOVERY ROUTES
# =============================================

@app.route('/api/discover/tables/<int:connection_id>')
@api_key_or_session_required(min_role=2)
def discover_tables_api(connection_id):
    """Discover tables from the actual database (works with all database types)."""
    try:
        # Get TARGET database connection info from app database
        query = "SELECT * FROM Connections WHERE id = ?"
        conn_result = query_app_database(query, (connection_id,))
        
        if not conn_result:
            return jsonify({'success': False, 'error': 'Connection not found'}), 404
        
        conn_info = conn_result[0]
        
        # Get connection string using your existing function
        target_conn_str, conn_id, db_type = get_database_connection_string(connection_id)
        
        if not target_conn_str:
            return jsonify({'success': False, 'error': 'Could not build connection string'}), 404
        
        logger.info(f"Discovering tables for connection {connection_id} (type: {db_type})")
        
        # Discover from target database
        db_tables = discover_tables_from_database(execute_sql_query_v2, target_conn_str)
        
        if not db_tables:
            logger.warning("No tables discovered from database")
            return jsonify({
                'success': True,
                'tables': [],
                'message': 'No tables found in database'
            })
        
        # Check which tables already exist in data dictionary
        query = "SELECT table_name FROM llm_Tables WHERE connection_id = ?"
        documented_tables = query_app_database(query, (connection_id,))
        
        documented_names = {t['table_name'] for t in documented_tables}
        
        # Mark tables as documented or not
        for table in db_tables:
            table['is_documented'] = table['TABLE_NAME'] in documented_names
        
        logger.info(f"Discovered {len(db_tables)} tables, {len(documented_names)} already documented")
        
        return jsonify({
            'success': True,
            'tables': db_tables
        })
        
    except Exception as e:
        logger.error(f"Error discovering tables: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f"Unexpected error: {str(e)}"
        }), 500



import uuid
from threading import Thread

# Store for tracking AI analysis progress
# Format: {task_id: {'current': 0, 'total': 5, 'status': 'processing', 'table': 'Customers', 'results': []}}
ai_analysis_progress = {}

def process_tables_in_background(task_id, connection_id, table_names, target_conn_str):
    """
    Process tables in background and update progress.
    This runs in a separate thread.
    """
    from ai_metadata_generator import analyze_table_with_ai
    from AppUtils import execute_sql_query_v2
    
    # Initialize progress
    ai_analysis_progress[task_id] = {
        'current': 0,
        'total': len(table_names),
        'status': 'processing',
        'table': None,
        'results': [],
        'errors': []
    }
    
    try:
        for i, table_name in enumerate(table_names):
            # Update progress - currently processing this table
            ai_analysis_progress[task_id]['current'] = i
            ai_analysis_progress[task_id]['table'] = table_name
            ai_analysis_progress[task_id]['status'] = 'processing'
            
            try:
                logger.info(f"[Task {task_id}] Analyzing table: {table_name}")
                
                # Analyze with AI (this takes 10-30 seconds)
                metadata = analyze_table_with_ai(execute_sql_query_v2, table_name, target_conn_str)
                
                # Save to database
                result = save_ai_generated_metadata(connection_id, table_name, metadata)
                
                # Record success
                ai_analysis_progress[task_id]['results'].append({
                    'table_name': table_name,
                    'success': True,
                    'columns_updated': result.get('columns_updated', 0),
                    'metrics_created': result.get('metrics_created', 0)
                })
                
                logger.info(f"[Task {task_id}] Successfully analyzed {table_name}")
                
            except Exception as e:
                logger.error(f"[Task {task_id}] Error analyzing {table_name}: {str(e)}", exc_info=True)
                
                # Record error
                ai_analysis_progress[task_id]['errors'].append({
                    'table_name': table_name,
                    'error': str(e)
                })
        
        # Mark as completed
        ai_analysis_progress[task_id]['current'] = len(table_names)
        ai_analysis_progress[task_id]['status'] = 'completed'
        ai_analysis_progress[task_id]['table'] = None
        
        logger.info(f"[Task {task_id}] Analysis complete. Processed {len(table_names)} tables.")
        
    except Exception as e:
        logger.error(f"[Task {task_id}] Fatal error in background processing: {str(e)}", exc_info=True)
        ai_analysis_progress[task_id]['status'] = 'error'
        ai_analysis_progress[task_id]['error_message'] = str(e)

@app.route('/api/ai/analyze-tables-batch', methods=['POST'])
@api_key_or_session_required(min_role=2)
def ai_analyze_tables_batch_api():
    """
    Start AI analysis in background and return task ID for progress tracking.
    """
    try:
        data = request.json
        connection_id = data.get('connection_id')
        table_names = data.get('table_names', [])
        
        if not connection_id or not table_names:
            return jsonify({'success': False, 'error': 'Missing parameters'}), 400
        
        logger.info(f"Starting AI analysis for {len(table_names)} tables")
        
        # Get TARGET database connection string
        from DataUtils import get_database_connection_string
        target_conn_str, conn_id, db_type = get_database_connection_string(connection_id)
        
        if not target_conn_str:
            return jsonify({'success': False, 'error': 'Connection not found'}), 404
        
        # Generate unique task ID
        task_id = str(uuid.uuid4())
        
        logger.info(f"Created analysis task: {task_id}")
        
        # Start background processing
        thread = Thread(
            target=process_tables_in_background,
            args=(task_id, connection_id, table_names, target_conn_str)
        )
        thread.daemon = True
        thread.start()
        
        # Return task ID immediately
        return jsonify({
            'success': True,
            'task_id': task_id,
            'message': 'Analysis started in background'
        })
        
    except Exception as e:
        logger.error(f"Error starting AI analysis: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ai/progress/<task_id>', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_ai_analysis_progress(task_id):
    """
    Get current progress of an AI analysis task.
    """
    try:
        progress_data = ai_analysis_progress.get(task_id)
        
        if not progress_data:
            return jsonify({'error': 'Task not found'}), 404
        
        # Return current progress
        return jsonify({
            'success': True,
            'current': progress_data['current'],
            'total': progress_data['total'],
            'status': progress_data['status'],
            'table': progress_data.get('table'),
            'results': progress_data.get('results', []),
            'errors': progress_data.get('errors', []),
            'error_message': progress_data.get('error_message')
        })
        
    except Exception as e:
        logger.error(f"Error getting progress for task {task_id}: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500
    
@app.route('/api/ai/cleanup/<task_id>', methods=['DELETE'])
@api_key_or_session_required(min_role=2)
def cleanup_ai_analysis_task(task_id):
    """
    Clean up progress data after task is complete.
    """
    try:
        if task_id in ai_analysis_progress:
            del ai_analysis_progress[task_id]
            return jsonify({'success': True, 'message': 'Task cleaned up'})
        else:
            return jsonify({'success': False, 'error': 'Task not found'}), 404
            
    except Exception as e:
        logger.error(f"Error cleaning up task {task_id}: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ai/analyze-tables-batch-legacy', methods=['POST'])
@api_key_or_session_required(min_role=2)
def ai_analyze_tables_batch_api_legacy():
    """Analyze multiple tables with AI in batch."""
    try:
        data = request.json
        connection_id = data.get('connection_id')
        table_names = data.get('table_names', [])
        
        if not connection_id or not table_names:
            return jsonify({'success': False, 'error': 'Missing parameters'}), 400
        
        logger.info(f"Starting AI analysis for {len(table_names)} tables")
        
        # Get TARGET database connection string
        target_conn_str, conn_id, db_type = get_database_connection_string(connection_id)
        
        if not target_conn_str:
            return jsonify({'success': False, 'error': 'Connection not found'}), 404
        
        logger.info(f"Target database type: {db_type}")
        
        results = []
        errors = []
        
        for table_name in table_names:
            try:
                logger.info(f"Analyzing table: {table_name}")
                
                # Analyze with AI
                metadata = analyze_table_with_ai(execute_sql_query_v2, table_name, target_conn_str)
                
                # Save to database
                result = save_ai_generated_metadata(
                    connection_id, 
                    table_name, 
                    metadata
                )
                
                results.append({
                    'table_name': table_name,
                    'success': True,
                    'columns_updated': result.get('columns_updated', 0),
                    'metrics_created': result.get('metrics_created', 0)
                })
                
                logger.info(f"Successfully analyzed {table_name}")
                
            except Exception as e:
                logger.error(f"Error analyzing {table_name}: {str(e)}", exc_info=True)
                errors.append({
                    'table_name': table_name,
                    'error': str(e)
                })
        
        return jsonify({
            'success': True,
            'results': results,
            'errors': errors,
            'total_processed': len(results),
            'total_errors': len(errors)
        })
        
    except Exception as e:
        logger.error(f"Error in batch analysis: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# =============================================
# HELPER FUNCTION FOR SAVING AI METADATA
# Uses proper app database queries with tenant context
# =============================================

def save_ai_generated_metadata(connection_id, table_name, metadata):
    """
    Save AI-generated metadata to the database.
    Uses query_app_database and execute_app_database_command for proper tenant context.
    """
    
    logger.info(f"Saving AI-generated metadata for {table_name}")
    
    # Step 1: Check if table exists
    check_query = "SELECT id FROM llm_Tables WHERE connection_id = ? AND table_name = ?"
    existing_tables = query_app_database(check_query, (connection_id, table_name))
    
    table_id = None
    if existing_tables:
        table_id = existing_tables[0]['id']
    else:
        # Create new table entry
        insert_query = """
            INSERT INTO llm_Tables (connection_id, table_name, table_description)
            VALUES (?, ?, ?)
        """
        execute_app_database_command(insert_query, (connection_id, table_name, f'Table: {table_name}'))
        
        # Get the ID
        existing_tables = query_app_database(check_query, (connection_id, table_name))
        if existing_tables:
            table_id = existing_tables[0]['id']
    
    if not table_id:
        raise ValueError(f"Could not get or create table ID for {table_name}")
    
    # Step 2: Update table metadata
    table_meta = metadata['table_metadata']
    
    update_query = """
        UPDATE llm_Tables
        SET 
            table_description = ?,
            table_type = ?,
            table_category = ?,
            primary_key_columns = ?,
            refresh_frequency = ?,
            row_count_estimate = ?,
            business_rules = ?,
            common_filters = ?,
            related_tables = ?
        WHERE id = ?
    """
    
    params = (
        table_meta.get('table_description'),
        table_meta.get('table_type'),
        table_meta.get('table_category'),
        table_meta.get('primary_key_columns'),
        table_meta.get('refresh_frequency'),
        metadata.get('row_count'),
        json.dumps(table_meta.get('business_rules')) if table_meta.get('business_rules') else None,
        json.dumps(table_meta.get('common_filters')) if table_meta.get('common_filters') else None,
        json.dumps(metadata.get('related_tables')) if metadata.get('related_tables') else None,
        table_id
    )
    
    execute_app_database_command(update_query, params)
    
    # Step 3: Save columns
    columns_updated = 0
    for col_meta in metadata['columns']:
        save_column_metadata(table_id, col_meta)
        columns_updated += 1
    
    # Step 4: Save calculated metrics
    metrics_created = 0
    if 'calculated_metrics' in metadata and metadata['calculated_metrics']:
        for metric in metadata['calculated_metrics']:
            save_calculated_metric(table_id, metric)
            metrics_created += 1
    
    logger.info(f"Saved metadata for {table_name}: {columns_updated} columns, {metrics_created} metrics")
    
    return {
        'table_id': table_id,
        'columns_updated': columns_updated,
        'metrics_created': metrics_created
    }


def save_column_metadata(table_id, col_meta):
    """Save or update column metadata using proper app database queries."""
    
    # Check if column exists
    check_query = "SELECT id FROM llm_Columns WHERE table_id = ? AND column_name = ?"
    existing_columns = query_app_database(check_query, (table_id, col_meta['column_name']))
    
    if existing_columns:
        # Update existing
        column_id = existing_columns[0]['id']
        
        update_query = """
            UPDATE llm_Columns
            SET 
                column_description = ?,
                data_type = ?,
                data_type_precision = ?,
                is_primary_key = ?,
                is_foreign_key = ?,
                foreign_key_table = ?,
                foreign_key_column = ?,
                is_nullable = ?,
                default_value = ?,
                semantic_type = ?,
                value_format = ?,
                units = ?,
                common_aggregations = ?,
                synonyms = ?,
                examples = ?,
                is_sensitive = ?,
                value_range = ?
            WHERE id = ?
        """
        
        params = (
            col_meta.get('column_description'),
            col_meta.get('data_type'),
            col_meta.get('data_type_precision'),
            col_meta.get('is_primary_key', 0),
            col_meta.get('is_foreign_key', 0),
            col_meta.get('foreign_key_table'),
            col_meta.get('foreign_key_column'),
            col_meta.get('is_nullable', 1),
            col_meta.get('default_value'),
            col_meta.get('semantic_type'),
            col_meta.get('value_format'),
            col_meta.get('units'),
            col_meta.get('common_aggregations'),
            col_meta.get('synonyms'),
            col_meta.get('examples'),
            1 if col_meta.get('is_sensitive') else 0,
            col_meta.get('value_range'),
            column_id
        )
        
        execute_app_database_command(update_query, params)
    else:
        # Create new
        insert_query = """
            INSERT INTO llm_Columns (
                table_id, column_name, column_description,
                data_type, data_type_precision,
                is_primary_key, is_foreign_key, foreign_key_table, foreign_key_column,
                is_nullable, default_value,
                semantic_type, value_format, units, common_aggregations,
                synonyms, examples, is_sensitive, value_range
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        
        params = (
            table_id,
            col_meta['column_name'],
            col_meta.get('column_description'),
            col_meta.get('data_type'),
            col_meta.get('data_type_precision'),
            col_meta.get('is_primary_key', 0),
            col_meta.get('is_foreign_key', 0),
            col_meta.get('foreign_key_table'),
            col_meta.get('foreign_key_column'),
            col_meta.get('is_nullable', 1),
            col_meta.get('default_value'),
            col_meta.get('semantic_type'),
            col_meta.get('value_format'),
            col_meta.get('units'),
            col_meta.get('common_aggregations'),
            col_meta.get('synonyms'),
            col_meta.get('examples'),
            1 if col_meta.get('is_sensitive') else 0,
            col_meta.get('value_range')
        )
        
        execute_app_database_command(insert_query, params)


def save_calculated_metric(table_id, metric):
    """Save a calculated/virtual metric using proper app database queries."""
    
    # Check if metric already exists
    check_query = "SELECT id FROM llm_Columns WHERE table_id = ? AND column_name = ? AND is_calculated = 1"
    existing_metrics = query_app_database(check_query, (table_id, metric['metric_name']))
    
    if existing_metrics:
        # Update existing
        column_id = existing_metrics[0]['id']
        
        update_query = """
            UPDATE llm_Columns
            SET 
                column_description = ?,
                calculation_formula = ?,
                calculation_dependencies = ?,
                semantic_type = ?,
                value_format = ?,
                units = ?
            WHERE id = ?
        """
        
        params = (
            metric.get('description'),
            metric.get('calculation_formula'),
            json.dumps(metric.get('calculation_dependencies')) if metric.get('calculation_dependencies') else None,
            metric.get('semantic_type'),
            metric.get('value_format'),
            metric.get('units'),
            column_id
        )
        
        execute_app_database_command(update_query, params)
    else:
        # Create new
        insert_query = """
            INSERT INTO llm_Columns (
                table_id, column_name, column_description,
                is_calculated, calculation_formula, calculation_dependencies,
                semantic_type, value_format, units
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        
        params = (
            table_id,
            metric['metric_name'],
            metric.get('description'),
            1,  # is_calculated = True
            metric.get('calculation_formula'),
            json.dumps(metric.get('calculation_dependencies')) if metric.get('calculation_dependencies') else None,
            metric.get('semantic_type'),
            metric.get('value_format'),
            metric.get('units')
        )
        
        execute_app_database_command(insert_query, params)

@app.route('/api/table/<int:table_id>', methods=['DELETE'])
@api_key_or_session_required(min_role=2)
def delete_table_new(table_id):
    """Delete a table and all its columns."""
    try:
        # Delete table
        delete_table_query = "DELETE FROM llm_Tables WHERE id = ?"
        rows_affected = execute_app_database_command(delete_table_query, (table_id,))
        
        if rows_affected > 0:
            return jsonify({'success': True, 'message': 'Table and associated columns deleted'})
        else:
            return jsonify({'success': False, 'error': 'Table not found'}), 404
            
    except Exception as e:
        logging.error(f"Error deleting table: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/column/<int:column_id>', methods=['DELETE'])
@api_key_or_session_required(min_role=2)
def delete_column_new(column_id):
    """Delete a column."""
    try:
        delete_query = "DELETE FROM llm_Columns WHERE id = ?"
        rows_affected = execute_app_database_command(delete_query, (column_id,))
        
        if rows_affected > 0:
            return jsonify({'success': True, 'message': 'Column deleted'})
        else:
            return jsonify({'success': False, 'error': 'Column not found'}), 404
            
    except Exception as e:
        logging.error(f"Error deleting column: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500



# AI Hub Assistant
from knowledge_api_client import KnowledgeAPIClient

# Initialize Knowledge API client
knowledge_client = KnowledgeAPIClient()

@app.route('/api/workflow/assistant', methods=['POST'])
@cross_origin()
def workflow_assistant_proxy():
    """
    Proxy endpoint for workflow assistant
    Forwards requests to the Knowledge API microservice
    """
    try:
        print(f'Workflow assistant called...')
        data = request.get_json()
        
        # Forward request to Knowledge API
        result = knowledge_client.ask_workflow_assistant(
            question=data.get('question'),
            workflow_context=data.get('workflow_context'),
            session_id=data.get('session_id'),
            include_history=data.get('include_history', False)
        )
        
        return jsonify(result)
    
    except Exception as e:
        logger.error(f"Workflow assistant proxy error: {e}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


@app.route('/api/workflow/resolve-ids', methods=['POST'])
@cross_origin()
def resolve_workflow_ids_proxy():
    """
    Proxy endpoint for workflow ID resolution
    Forwards requests to the Knowledge API microservice
    """
    try:
        print(f'Workflow ID resolution called...')
        data = request.get_json()
        
        # Forward request to Knowledge API
        result = knowledge_client.resolve_workflow_ids(
            commands=data.get('commands')
        )
        
        return jsonify(result)
    
    except Exception as e:
        logger.error(f"Workflow ID resolution proxy error: {e}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


@app.route('/api/workflow/assistant/result', methods=['POST'])
@cross_origin()
def workflow_assistant_result_proxy():
    """
    Proxy endpoint for workflow execution results
    Forwards requests to the Knowledge API microservice
    """
    try:
        print(f'Workflow assistant result called...')
        data = request.get_json()
        
        # Forward request to Knowledge API
        result = knowledge_client.send_execution_result(
            session_id=data.get('session_id'),
            commands=data.get('commands'),
            result=data.get('result')
        )
        
        return jsonify(result)
    
    except Exception as e:
        logger.error(f"Workflow assistant result proxy error: {e}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


@app.route('/api/workflow/assistant/history', methods=['GET'])
@cross_origin()
def get_conversation_history_proxy():
    """
    Proxy endpoint for getting conversation history
    Forwards requests to the Knowledge API microservice
    """
    try:
        print(f'Get conversation history called...')
        session_id = request.args.get('session_id')
        
        # Forward request to Knowledge API
        result = knowledge_client.get_conversation_history(session_id)
        
        return jsonify(result)
    
    except Exception as e:
        logger.error(f"Get conversation history proxy error: {e}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


@app.route('/api/workflow/assistant/history', methods=['DELETE'])
@cross_origin()
def clear_conversation_history_proxy():
    """
    Proxy endpoint for clearing conversation history
    Forwards requests to the Knowledge API microservice
    """
    try:
        print(f'Clear conversation history called...')
        session_id = request.args.get('session_id')
        
        # Forward request to Knowledge API
        result = knowledge_client.clear_conversation_history(session_id)
        
        return jsonify(result)
    
    except Exception as e:
        logger.error(f"Clear conversation history proxy error: {e}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


@app.route('/api/workflow/validate', methods=['POST'])
@cross_origin()
def validate_workflow_proxy():
    """
    Proxy endpoint for workflow validation
    Forwards requests to the Knowledge API microservice
    """
    try:
        print(f'Workflow validation called...')
        data = request.get_json()
        
        # Forward request to Knowledge API
        result = knowledge_client.validate_workflow(
            workflow_context=data.get('workflow_context')
        )
        
        return jsonify(result)
    
    except Exception as e:
        logger.error(f"Workflow validation proxy error: {e}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


##########################################################
# MCP
##########################################################
# MCP Server User Config UI
@app.route('/mcp_user_servers')
@developer_required()
def mcp_user_servers():
    """Render the MCP server config UI"""
    return render_template('mcp_user_servers.html')

@app.route('/api/mcp/test_v1', methods=['POST'])
@cross_origin()
def test_mcp_server_v1():
    """Test an MCP server configuration"""
    from MCP.mcp_user_client import test_mcp_server_connection
    
    data = request.json
    command = data.get('command')
    args = data.get('args', [])
    env_vars = data.get('env_vars', {})
    
    result = test_mcp_server_connection(command, args, env_vars)
    return jsonify(result)

@app.route('/api/mcp/servers_v1', methods=['GET'])
@cross_origin()
def get_mcp_servers_v1():
    """Get all MCP servers for the current user"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
    
    cursor.execute("""
        SELECT server_id, server_name, server_type, command, args, 
               description, enabled, last_test_date, last_test_status
        FROM UserMCPServers
        ORDER BY server_name
    """)
    
    servers = []
    for row in cursor.fetchall():
        servers.append({
            'server_id': row[0],
            'server_name': row[1],
            'server_type': row[2],
            'command': row[3],
            'args': json.loads(row[4] or '[]'),
            'description': row[5],
            'enabled': row[6],
            'last_test_date': row[7].strftime('%Y-%m-%d %H:%M') if row[7] else None,
            'last_test_status': row[8]
        })
    
    cursor.close()
    conn.close()
    
    return jsonify(servers)

@app.route('/api/mcp/servers_v1', methods=['POST'])
@cross_origin()
def create_mcp_server_v1():
    """Create a new MCP server configuration"""
    data = request.json
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
    
    cursor.execute("""
        INSERT INTO UserMCPServers 
            (server_name, server_type, command, args, env_vars, description, added_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, 
        data['server_name'],
        data['server_type'],
        data['command'],
        json.dumps(data['args']),
        json.dumps(data.get('env_vars', {})),
        data.get('description'),
        current_user.id
    )
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return jsonify({'status': 'success', 'message': 'MCP server created'})

@app.route('/api/mcp/servers/<int:server_id>/tools_v1', methods=['GET'])
@cross_origin()
def get_mcp_server_tools_v1(server_id):
    """Get tools available from an MCP server"""
    from MCP.mcp_user_client import SimpleMCPClient
    
    # Get server config
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
    cursor.execute("""
        SELECT command, args, env_vars 
        FROM UserMCPServers 
        WHERE server_id = ? AND added_by = ?
    """, server_id, current_user.id)
    
    row = cursor.fetchone()
    if not row:
        return jsonify({'error': 'Server not found'}), 404
    
    command, args_json, env_vars_json = row
    args = json.loads(args_json or '[]')
    env_vars = json.loads(env_vars_json or '{}')
    
    cursor.close()
    conn.close()
    
    # Get tools from server
    client = SimpleMCPClient(command, args, env_vars)
    try:
        if client.start():
            tools = client.list_tools()
            return jsonify({'tools': tools})
        else:
            return jsonify({'error': 'Failed to connect to server'}), 500
    finally:
        client.close()

@app.route('/api/agents/<int:agent_id>/mcp-servers_v1', methods=['POST'])
@cross_origin()
@api_key_or_session_required(min_role=2)
def assign_mcp_server_to_agent_v1(agent_id):
    """Assign an MCP server to an agent"""
    data = request.json
    server_id = data.get('server_id')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
    
    # Check if assignment already exists
    cursor.execute("""
        SELECT COUNT(*) FROM AgentMCPServers 
        WHERE agent_id = ? AND server_id = ?
    """, agent_id, server_id)
    
    if cursor.fetchone()[0] > 0:
        cursor.close()
        conn.close()
        return jsonify({'status': 'error', 'message': 'Server already assigned to agent'})
    
    # Create assignment
    cursor.execute("""
        INSERT INTO AgentMCPServers (agent_id, server_id)
        VALUES (?, ?)
    """, agent_id, server_id)
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return jsonify({'status': 'success', 'message': 'MCP server assigned to agent'})


"""
Flask API Routes for MCP Server Management
==========================================
Add these routes to your app.py file to support the MCP UI.
"""
from MCP.mcp_user_client import test_mcp_server_connection
from MCP.mcp_adapter import MCPGatewayClient


# ============================================================================
# MCP Server Management Routes - Enhanced for Remote Servers
# ============================================================================

@app.route('/api/mcp/servers', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_mcp_servers():
    """Get all MCP servers for the current tenant"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Enhanced query to include remote server fields
        cursor.execute("""
            SELECT 
                ms.server_id,
                ms.server_name,
                ms.server_type,  -- 'local' or 'remote'
                ms.server_url,   -- For remote servers
                ms.auth_type,    -- Authentication method
                ms.connection_config,  -- For local servers
                ms.description,
                ms.category,
                ms.icon,
                ms.enabled,
                ms.created_by,
                ms.created_date,
                ms.last_tested_date,
                ms.last_test_status,
                ms.tool_count,
                ms.request_timeout,
                ms.max_retries,
                ms.verify_ssl,
                (SELECT COUNT(*) FROM AgentMCPServers ams 
                 WHERE ams.server_id = ms.server_id AND ams.enabled = 1) as agent_count,
                CASE 
                    WHEN ms.last_test_status = 'success' 
                    AND DATEDIFF(minute, ms.last_tested_date, getutcdate()) < 30 
                    THEN 'active'
                    ELSE 'inactive'
                END as status
            FROM MCPServers ms
            ORDER BY ms.server_type DESC, ms.server_name  -- Remote first
        """)
        
        servers = []
        for row in cursor.fetchall():
            server = {
                'server_id': row[0],
                'server_name': row[1],
                'server_type': row[2],
                'server_url': row[3],
                'auth_type': row[4],
                'connection_config': row[5],  # For backward compatibility
                'description': row[6],
                'category': row[7],
                'icon': row[8],
                'enabled': row[9],
                'created_by': row[10],
                'created_date': row[11].isoformat() if row[11] else None,
                'last_tested_date': row[12].isoformat() if row[12] else None,
                'last_test_status': row[13],
                'tool_count': row[14],
                'request_timeout': row[15],
                'max_retries': row[16],
                'verify_ssl': row[17],
                'agent_count': row[18],
                'status': row[19]
            }
            
            # For local servers, parse connection_config
            if server['server_type'] == 'local' and server['connection_config']:
                try:
                    config = json.loads(server['connection_config'])
                    server['command'] = config.get('command')
                    server['args'] = config.get('args', [])
                except:
                    pass
            
            servers.append(server)
        
        cursor.close()
        conn.close()
        
        return jsonify(servers)
        
    except Exception as e:
        logger.error(f"Error getting MCP servers: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/mcp/servers', methods=['POST'])
@api_key_or_session_required(min_role=2)
def create_mcp_server():
    """Create a new MCP server configuration (remote or local)"""
    try:
        data = request.json
        server_type = data.get('server_type', 'local')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        if server_type == 'remote':
            # Remote server configuration
            auth_config = data.get('auth_config', {})
            
            # Store sensitive auth data separately (encrypted)
            cursor.execute("""
                INSERT INTO MCPServers (
                    server_name, server_type, server_url, auth_type,
                    description, category, icon, enabled, created_by, created_date,
                    request_timeout, max_retries, verify_ssl
                ) 
                OUTPUT INSERTED.server_id
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, getutcdate(), ?, ?, ?)
            """, (
                data.get('server_name'),
                'remote',
                data.get('server_url'),
                data.get('auth_type'),
                data.get('description', ''),
                data.get('category', ''),
                data.get('icon', ''),
                1,  # enabled by default
                session.get('user_email', 'unknown'),
                data.get('request_timeout', 30),
                data.get('max_retries', 3),
                data.get('verify_ssl', True)
            ))
            
            server_id = cursor.fetchone()[0]
            
            # Store auth credentials separately (encrypted)
            if auth_config:
                for key, value in auth_config.items():
                    cursor.execute("""
                        INSERT INTO MCPServerCredentials (server_id, credential_key, credential_value)
                        VALUES (?, ?, ENCRYPTBYPASSPHRASE(?, ?))
                    """, (server_id, key, get_encryption_key(), value))
            
        else:
            # Local server configuration (backward compatible)
            connection_config = {
                'command': data.get('command'),
                'args': data.get('args', []),
                'env_vars': data.get('env_vars', {})
            }
            
            cursor.execute("""
                INSERT INTO MCPServers (
                    server_name, server_type, connection_config,
                    description, category, icon, enabled, created_by, created_date
                ) 
                OUTPUT INSERTED.server_id
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, getutcdate())
            """, (
                data.get('server_name'),
                'local',
                json.dumps(connection_config),
                data.get('description', ''),
                data.get('category', ''),
                data.get('icon', ''),
                1,
                session.get('user_email', 'unknown')
            ))
            
            server_id = cursor.fetchone()[0]
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'server_id': server_id,
            'message': 'MCP server created successfully'
        })
        
    except Exception as e:
        logger.error(f"Error creating MCP server: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


def test_remote_mcp_server(config):
    """Test a remote MCP server connection"""
    try:
        url = config.get('url')
        auth_type = config.get('authType')
        auth_config = config.get('authConfig', {})
        timeout = config.get('timeout', 30)
        verify_ssl = config.get('verifySsl', True)
        
        # Build headers based on auth type
        headers = {'Content-Type': 'application/json'}
        
        if auth_type == 'bearer':
            headers['Authorization'] = f"Bearer {auth_config.get('token', '')}"
        elif auth_type == 'apikey':
            header_name = auth_config.get('header', 'X-API-Key')
            headers[header_name] = auth_config.get('key', '')
        elif auth_type == 'basic':
            import base64
            username = auth_config.get('username', '')
            password = auth_config.get('password', '')
            credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
            headers['Authorization'] = f"Basic {credentials}"
        elif auth_type == 'custom':
            custom_headers = auth_config.get('headers', {})
            headers.update(custom_headers)
        
        # Try to connect to the MCP endpoint
        # First, try to get server info
        info_url = url.rstrip('/') + '/info'
        try:
            response = requests.get(
                info_url,
                headers=headers,
                timeout=timeout,
                verify=verify_ssl
            )
            if response.status_code == 200:
                server_info = response.json()
            else:
                server_info = None
        except:
            server_info = None
        
        # Try to list tools
        tools_url = url.rstrip('/') + '/tools'
        response = requests.get(
            tools_url,
            headers=headers,
            timeout=timeout,
            verify=verify_ssl
        )
        
        if response.status_code == 200:
            tools_data = response.json()
            tools = tools_data.get('tools', []) if isinstance(tools_data, dict) else tools_data
            
            return {
                'status': 'success',
                'tool_count': len(tools),
                'tools': tools[:50],  # Limit to first 50 tools
                'server_info': server_info,
                'message': f'Successfully connected to {url}'
            }
        elif response.status_code == 401:
            return {
                'status': 'failed',
                'error': 'Authentication failed. Check your credentials.',
                'details': 'HTTP 401 Unauthorized'
            }
        elif response.status_code == 404:
            return {
                'status': 'failed',
                'error': 'MCP endpoint not found at this URL.',
                'details': 'HTTP 404 Not Found'
            }
        else:
            return {
                'status': 'failed',
                'error': f'Server returned status code {response.status_code}',
                'details': response.text[:200] if response.text else None
            }
            
    except requests.Timeout:
        return {
            'status': 'failed',
            'error': 'Connection timed out',
            'details': 'The server did not respond within the timeout period'
        }
    except requests.ConnectionError as e:
        return {
            'status': 'failed',
            'error': 'Could not connect to server',
            'details': str(e)
        }
    except Exception as e:
        return {
            'status': 'failed',
            'error': 'Unexpected error',
            'details': str(e)
        }


def get_encryption_key():
    """Get encryption key for credentials (implement your own secure key management)"""
    # This is a placeholder - use proper key management in production
    # Consider using Azure Key Vault, AWS KMS, or similar
    from encrypt import ENCRYPTION_KEY
    return os.environ.get('MCP_ENCRYPTION_KEY', ENCRYPTION_KEY)


# Additional helper routes...

@app.route('/api/mcp/directory', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_mcp_directory():
    """Get directory of known MCP servers"""
    directory = [
        {
            'name': 'Salesforce CRM',
            'category': 'CRM',
            'url_template': 'https://{instance}.salesforce.com/services/mcp/v1',
            'auth_type': 'oauth2',
            'description': 'Customer relationship management',
            'provider': 'Salesforce',
            'documentation': 'https://developer.salesforce.com/mcp'
        },
        {
            'name': 'SAP ERP',
            'category': 'ERP',
            'url_template': 'https://{hostname}/sap/opu/mcp/v1',
            'auth_type': 'oauth2',
            'description': 'Enterprise resource planning',
            'provider': 'SAP',
            'documentation': 'https://help.sap.com/mcp'
        },
        {
            'name': 'Microsoft Azure',
            'category': 'Cloud',
            'url_template': 'https://management.azure.com/mcp/v1',
            'auth_type': 'oauth2',
            'description': 'Azure cloud services',
            'provider': 'Microsoft',
            'documentation': 'https://docs.microsoft.com/mcp'
        },
        # Add more as they become available
    ]
    
    return jsonify(directory)



# ============================================================================
# MCP Protocol Communication (for remote servers)
# ============================================================================

def send_mcp_request(url: str, method: str, params: Dict = None, headers: Dict = None) -> Dict:
    """
    Send a JSON-RPC request to an MCP server over HTTP/SSE
    
    MCP uses JSON-RPC 2.0 protocol, not REST endpoints!
    """
    # Build JSON-RPC request
    rpc_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or {}
    }
    
    # Default headers
    if headers is None:
        headers = {}
    headers['Content-Type'] = 'application/json'
    
    try:
        # MCP servers use a single endpoint for all JSON-RPC calls
        response = requests.get(
            url,
            json=rpc_request,
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            if 'result' in result:
                return {'status': 'success', 'data': result['result']}
            elif 'error' in result:
                return {'status': 'error', 'error': result['error']}
        else:
            return {'status': 'error', 'error': f'HTTP {response.status_code}'}
            
    except Exception as e:
        return {'status': 'error', 'error': str(e)}

# ============================================================================
# MISSING ROUTES - Agents Management
# ============================================================================

@app.route('/api/agents', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_agents_mcp():
    """Get all agents for the current tenant"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get agents
        cursor.execute("""
            SELECT
                a.id,
                a.description,
                a.objective,
                a.enabled,
                a.create_date
            FROM [dbo].[Agents] a
            WHERE a.enabled = 1
            ORDER BY a.description
        """)

        agents = []
        for row in cursor.fetchall():
            agents.append({
                'agent_id': row[0],
                'agent_name': row[1],
                'description': row[2],
                'enabled': row[3],
                'created_date': row[4].isoformat() if row[4] else None
            })
        
        cursor.close()
        conn.close()
        
        return jsonify(agents)
        
    except Exception as e:
        logger.error(f"Error getting agents: {e}")
        return jsonify({'error': str(e)}), 500

# ============================================================================
# ENHANCED MCP Server Routes with Edit Support
# ============================================================================

@app.route('/api/mcp/servers/<int:server_id>', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_mcp_server(server_id):
    """Get a specific MCP server configuration for editing"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        cursor.execute("""
            SELECT 
                server_id,
                server_name,
                server_type,
                server_url,
                auth_type,
                connection_config,
                description,
                category,
                request_timeout,
                max_retries,
                verify_ssl
            FROM MCPServers
            WHERE server_id = ? 
        """, server_id)
        
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'Server not found'}), 404
        
        server = {
            'server_id': row[0],
            'server_name': row[1],
            'server_type': row[2],
            'server_url': row[3],
            'auth_type': row[4],
            'connection_config': row[5],
            'description': row[6],
            'category': row[7],
            'request_timeout': row[8],
            'max_retries': row[9],
            'verify_ssl': row[10]
        }
        
        # Get credentials if it's a remote server (sanitized)
        if server['server_type'] == 'remote' and server['auth_type'] != 'none':
            cursor.execute("""
                SELECT credential_key
                FROM MCPServerCredentials
                WHERE server_id = ?
            """, server_id)
            
            # Only return credential keys, not values
            server['auth_keys'] = [row[0] for row in cursor.fetchall()]
        
        cursor.close()
        conn.close()
        
        return jsonify(server)
        
    except Exception as e:
        logger.error(f"Error getting MCP server: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/mcp/servers/<int:server_id>', methods=['PUT'])
@api_key_or_session_required(min_role=2)
def update_mcp_server(server_id):
    """Update an existing MCP server configuration"""
    try:
        data = request.json
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Check if server exists and belongs to tenant
        cursor.execute("""
            SELECT server_id FROM MCPServers 
            WHERE server_id = ?
        """, server_id)
        
        if not cursor.fetchone():
            return jsonify({'error': 'Server not found'}), 404
        
        if data.get('server_type') == 'remote':
            # Update remote server
            cursor.execute("""
                UPDATE MCPServers
                SET server_name = ?,
                    server_url = ?,
                    auth_type = ?,
                    description = ?,
                    category = ?,
                    request_timeout = ?,
                    max_retries = ?,
                    verify_ssl = ?,
                    modified_date = getutcdate(),
                    modified_by = ?
                WHERE server_id = ?
            """, (
                data.get('server_name'),
                data.get('server_url'),
                data.get('auth_type'),
                data.get('description', ''),
                data.get('category', ''),
                data.get('request_timeout', 30),
                data.get('max_retries', 3),
                data.get('verify_ssl', True),
                session.get('user_email', 'unknown'),
                server_id
            ))
            
            # Update credentials if provided
            auth_config = data.get('auth_config', {})
            if auth_config:
                # Delete old credentials
                cursor.execute("DELETE FROM MCPServerCredentials WHERE server_id = ?", server_id)
                
                # Insert new credentials
                for key, value in auth_config.items():
                    if value:  # Only store non-empty values
                        cursor.execute("""
                            INSERT INTO MCPServerCredentials (server_id, credential_key, credential_value)
                            VALUES (?, ?, ENCRYPTBYPASSPHRASE(?, ?))
                        """, (server_id, key, get_encryption_key(), value))
        else:
            # Update local server
            connection_config = {
                'command': data.get('command'),
                'args': data.get('args', []),
                'env_vars': data.get('env_vars', {})
            }
            
            cursor.execute("""
                UPDATE MCPServers
                SET server_name = ?,
                    connection_config = ?,
                    description = ?,
                    category = ?,
                    modified_date = getutcdate(),
                    modified_by = ?
                WHERE server_id = ?
            """, (
                data.get('server_name'),
                json.dumps(connection_config),
                data.get('description', ''),
                data.get('category', ''),
                session.get('user_email', 'unknown'),
                server_id
            ))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'message': 'Server updated successfully'
        })
        
    except Exception as e:
        logger.error(f"Error updating MCP server: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/mcp/servers/<int:server_id>', methods=['DELETE'])
@api_key_or_session_required(min_role=2)
def delete_mcp_server(server_id):
    """Delete an MCP server configuration"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Check if server exists and belongs to tenant
        cursor.execute("""
            SELECT server_id FROM MCPServers 
            WHERE server_id = ? 
        """, server_id)
        
        if not cursor.fetchone():
            return jsonify({'error': 'Server not found'}), 404
        
        # Delete agent assignments first
        cursor.execute("DELETE FROM AgentMCPServers WHERE server_id = ?", server_id)
        
        # Delete credentials
        cursor.execute("DELETE FROM MCPServerCredentials WHERE server_id = ?", server_id)
        
        # Delete server
        cursor.execute("DELETE FROM MCPServers WHERE server_id = ?", server_id)
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({'status': 'success', 'message': 'Server deleted'})
        
    except Exception as e:
        logger.error(f"Error deleting MCP server: {e}")
        return jsonify({'error': str(e)}), 500

# ============================================================================
# MCP Server Agent Assignment Routes
# ============================================================================

@app.route('/api/mcp/servers/<int:server_id>/agents', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_mcp_server_agents(server_id):
    """Get agents assigned to an MCP server"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        cursor.execute("""
            SELECT agent_id
            FROM AgentMCPServers
            WHERE server_id = ? AND enabled = 1
        """, server_id)
        
        agent_ids = [row[0] for row in cursor.fetchall()]
        
        cursor.close()
        conn.close()
        
        return jsonify(agent_ids)
        
    except Exception as e:
        logger.error(f"Error getting server agents: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/mcp/servers/<int:server_id>/agents', methods=['POST'])
@api_key_or_session_required(min_role=2)
def update_mcp_server_agents(server_id):
    """Update agent assignments for an MCP server"""
    try:
        data = request.json
        agent_ids = data.get('agent_ids', [])
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Verify server belongs to tenant
        cursor.execute("""
            SELECT server_id FROM MCPServers 
            WHERE server_id = ? 
        """, server_id)
        
        if not cursor.fetchone():
            return jsonify({'error': 'Server not found'}), 404
        
        # Delete existing assignments
        cursor.execute("DELETE FROM AgentMCPServers WHERE server_id = ?", server_id)
        
        # Insert new assignments
        for agent_id in agent_ids:
            cursor.execute("""
                INSERT INTO AgentMCPServers (agent_id, server_id, enabled, assigned_date, assigned_by)
                VALUES (?, ?, 1, getutcdate(), ?)
            """, (agent_id, server_id, session.get('user_email', 'unknown')))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({'status': 'success', 'message': f'Assigned to {len(agent_ids)} agents'})
        
    except Exception as e:
        logger.error(f"Error updating server agents: {e}")
        return jsonify({'error': str(e)}), 500

# ============================================================================
# MCP Server Tools Route - Fixed for proper MCP protocol
# ============================================================================
# routes_mcp.py
import asyncio
import base64
# import os
# from flask import request, jsonify
# from flask_login import login_required
from MCP.mcp_user_client import MCPClient  # from the file above

def run_async(coro):
    """Run an async coroutine from sync Flask context."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()

@app.route('/api/mcp/test', methods=['POST'])
@api_key_or_session_required(min_role=2)
def test_mcp_server():
    """
    Test a remote MCP server:
      - initialize (JSON-RPC)
      - list tools (JSON-RPC)
    """
    try:
        data = request.json or {}
        if data.get('type') != 'remote':
            return jsonify({'status': 'failed', 'error': 'This endpoint is for remote servers only'})

        url = data.get('url')
        auth_type = data.get('authType')
        auth_config = data.get('authConfig', {})

        headers = {}
        if auth_type == 'bearer':
            headers['Authorization'] = f"Bearer {auth_config.get('token', '')}"
        elif auth_type == 'apikey':
            header_name = auth_config.get('header', 'X-API-Key')
            headers[header_name] = auth_config.get('key', '')
        elif auth_type == 'basic':
            username = auth_config.get('username', '')
            password = auth_config.get('password', '')
            credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
            headers['Authorization'] = f"Basic {credentials}"
        elif auth_type == 'oauth2':
            headers['Authorization'] = f"Bearer {auth_config.get('access_token', '')}"

        async def test_connection():
            client = MCPClient(url, headers=headers)
            try:
                await client.connect()
                init_result = await client.initialize()
                tools = await client.list_tools()
                await client.disconnect()
                return {
                    'status': 'success',
                    'tool_count': len(tools),
                    'tools': tools[:50],
                    'server_info': init_result,
                    'message': f'Successfully connected to {url}'
                }
            except Exception as e:
                await client.disconnect()
                return {'status': 'failed', 'error': str(e)}

        return jsonify(run_async(test_connection()))

    except Exception as e:
        return jsonify({'status': 'failed', 'error': str(e)}), 500


@app.route('/api/mcp/servers/<int:server_id>/tools', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_mcp_server_tools(server_id):
    """
    Load server config, then fetch tools via JSON-RPC.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        cursor.execute("""
            SELECT server_type, server_url, auth_type
            FROM MCPServers 
            WHERE server_id = ?
        """, server_id)
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'Server not found'}), 404

        server_type, server_url, auth_type = row
        if server_type != 'remote' or not server_url:
            return jsonify({'server_id': server_id, 'tools': [], 'error': 'This endpoint is for remote servers only'})

        headers = {}
        if auth_type and auth_type != 'none':
            cursor.execute("""
                SELECT credential_key, 
                       CONVERT(varchar, DECRYPTBYPASSPHRASE(?, credential_value)) as credential_value
                FROM MCPServerCredentials
                WHERE server_id = ?
            """, (get_encryption_key(), server_id))
            auth_config = {}
            for cred_key, cred_val in cursor.fetchall():
                auth_config[cred_key] = cred_val

            if auth_type == 'bearer':
                headers['Authorization'] = f"Bearer {auth_config.get('token', '')}"
            elif auth_type == 'apikey':
                header_name = auth_config.get('header', 'X-API-Key')
                headers[header_name] = auth_config.get('key', '')
            elif auth_type == 'oauth2':
                headers['Authorization'] = f"Bearer {auth_config.get('access_token', '')}"

        cursor.close()
        conn.close()

        async def get_tools():
            client = MCPClient(server_url, headers=headers)
            try:
                await client.connect()
                await client.initialize()
                tools = await client.list_tools()
                await client.disconnect()
                return {'server_id': server_id, 'tools': tools}
            except Exception as e:
                await client.disconnect()
                return {'server_id': server_id, 'tools': [], 'error': str(e)}

        return jsonify(run_async(get_tools()))

    except Exception as e:
        return jsonify({'error': str(e)}), 500



@app.route('/local-secrets')
@developer_required()
def local_secrets_page():
    return render_template('local_secrets.html')


@app.route('/test-bare')
def test_bare():
    return "<html><body><h1>Bare test</h1></body></html>"

@app.route('/test-template')
def test_template():
    return render_template('base.html')

@app.route('/test-crash')
def test_crash():
    print("Testing crash from client...")
    try:
        print("Attempting division by zero...")
        division_by_zero = 1 / 0
        print(division_by_zero)
    except Exception as e:
        print(f"Caught an exception: {e}")
        capture_exception(e)
        return jsonify({'status': 'crashed'})


@app.route('/debug/endpoints')
@login_required  # Protect this!
def list_endpoints():
    endpoints = []
    for rule in app.url_map.iter_rules():
        endpoints.append({
            'endpoint': rule.endpoint,
            'methods': list(rule.methods - {'HEAD', 'OPTIONS'}),
            'path': rule.rule
        })
    return jsonify(sorted(endpoints, key=lambda x: x['endpoint']))


@app.route('/api/settings/db-logging', methods=['GET'])
@admin_required(api=True)
def get_db_logging_setting():
    try:
        from CommonUtils import get_cloud_db_connection
        conn = get_cloud_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        cursor.execute("SELECT setting_value FROM TenantSettings WHERE setting_key = 'database_logging_enabled'")
        row = cursor.fetchone()
        conn.close()
        # Handle NULL or missing row - default to False
        enabled = False
        if row and row[0]:
            enabled = row[0].lower() in ('true', '1', 'yes')
        return jsonify({'status': 'success', 'enabled': enabled})
    except Exception as e:
        logger.error(f"Error getting db-logging setting: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/settings/db-logging', methods=['POST'])
@admin_required(api=True)
def set_db_logging_setting():
    try:
        data = request.get_json() or {}
        enabled = 'true' if data.get('enabled') else 'false'

        from CommonUtils import get_cloud_db_connection
        conn = get_cloud_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Check if row exists
        cursor.execute("SELECT 1 FROM TenantSettings WHERE setting_key = 'database_logging_enabled'")
        exists = cursor.fetchone()
        
        if exists:
            cursor.execute("""
                UPDATE TenantSettings 
                SET setting_value = ?, updated_date = GETUTCDATE(), updated_by = ?
                WHERE setting_key = 'database_logging_enabled'
            """, enabled, current_user.id)
        else:
            cursor.execute("""
                INSERT INTO TenantSettings (setting_key, setting_value, setting_type, updated_date, updated_by) 
                VALUES ('database_logging_enabled', ?, 'boolean', GETUTCDATE(), ?)
            """, enabled, current_user.id)
        
        conn.commit()
        conn.close()
        return jsonify({'status': 'success', 'enabled': enabled == 'true'})
    except Exception as e:
        logger.error(f"Error setting db-logging: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
