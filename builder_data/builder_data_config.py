"""
Builder Data Service Configuration
=====================================
Leverages the existing api_keys_config from the main AI Hub app
so there's a single source of truth for LLM credentials.
"""

import os
import sys

# Disable LangSmith telemetry — must be set before any langchain imports
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"

# Add parent directory to path so we can import from the main app
PARENT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

# Load API_KEY from Windows Registry and credentials from encrypted store
try:
    from secure_config import load_secure_config
    load_secure_config()
except ImportError:
    pass

from api_keys_config import get_openai_config


# ─── AI Hub Connection ──────────────────────────────────────────────────

import socket
import logging

_config_logger = logging.getLogger(__name__)


def _get_local_ip():
    """Get the local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


def _build_service_url(port_offset: int = 0, default_port: int = 5000) -> str:
    """
    Build a service URL using the same logic as CommonUtils.py.

    Uses environment variables:
    - PROTOCOL: http or https (default: http)
    - HOST: hostname (default: localhost)
    - HOST_PORT: base port (default: 5000)

    The actual port is HOST_PORT + port_offset.
    """
    protocol = os.getenv('PROTOCOL', 'http')
    host = os.getenv('HOST', 'localhost')

    if host == "0.0.0.0":
        host = _get_local_ip()

    try:
        base_port = int(os.getenv('HOST_PORT', str(default_port)))
        port = base_port + port_offset
    except ValueError:
        port = default_port + port_offset

    return f"{protocol}://{host}:{port}"


# Try to import from CommonUtils first (preferred when running inside main app)
COMMON_UTILS_AVAILABLE = False
try:
    from CommonUtils import (
        get_base_url,
        get_document_api_base_url,
        get_scheduler_api_base_url,
        get_vector_api_base_url,
        get_agent_api_base_url,
        get_knowledge_api_base_url,
        get_executor_api_base_url,
        get_mcp_gateway_api_base_url,
    )
    COMMON_UTILS_AVAILABLE = True
    _config_logger.info("CommonUtils imported successfully - using main app URL functions")
except ImportError as e:
    _config_logger.warning(f"CommonUtils import failed: {e}")
    _config_logger.info("Using local URL calculation based on HOST_PORT environment variable")

    def get_base_url():
        """Main app URL (HOST_PORT + 0)"""
        return _build_service_url(port_offset=0, default_port=5000)

    def get_document_api_base_url():
        """Document API URL (HOST_PORT + 10)"""
        return _build_service_url(port_offset=10, default_port=5000)

    def get_scheduler_api_base_url():
        """Scheduler API URL (same as main app - scheduler routes are in main Flask app)"""
        return _build_service_url(port_offset=0, default_port=5000)

    def get_vector_api_base_url():
        """Vector API URL (HOST_PORT + 30)"""
        return _build_service_url(port_offset=30, default_port=5000)

    def get_agent_api_base_url():
        """Agent API URL (HOST_PORT + 40)"""
        return _build_service_url(port_offset=40, default_port=5000)

    def get_knowledge_api_base_url():
        """Knowledge API URL (HOST_PORT + 50)"""
        return _build_service_url(port_offset=50, default_port=5000)

    def get_executor_api_base_url():
        """Executor API URL (HOST_PORT + 60)"""
        return _build_service_url(port_offset=60, default_port=5000)

    def get_mcp_gateway_api_base_url():
        """MCP Gateway API URL (HOST_PORT + 70)"""
        return _build_service_url(port_offset=70, default_port=5000)


def get_data_api_base_url():
    """Data Pipeline API URL (DATA_SERVICE_PORT, default 8200)"""
    protocol = os.getenv('PROTOCOL', 'http')
    host = os.getenv('HOST', 'localhost')
    if host == "0.0.0.0":
        host = _get_local_ip()
    port = int(os.getenv('DATA_SERVICE_PORT', '8200'))
    return f"{protocol}://{host}:{port}"


# Service name constants for action routing
class ServiceTarget:
    """Identifies which microservice handles an action."""
    MAIN = "main"
    DOCUMENT_API = "document"
    SCHEDULER_API = "scheduler"
    VECTOR_API = "vector"
    AGENT_API = "agent"
    KNOWLEDGE_API = "knowledge"
    EXECUTOR_API = "executor"
    MCP_GATEWAY = "mcp"
    DATA_API = "data"


def get_service_url(service: str) -> str:
    """Get the base URL for a microservice."""
    service_map = {
        ServiceTarget.MAIN: get_base_url,
        ServiceTarget.DOCUMENT_API: get_document_api_base_url,
        ServiceTarget.SCHEDULER_API: get_scheduler_api_base_url,
        ServiceTarget.VECTOR_API: get_vector_api_base_url,
        ServiceTarget.AGENT_API: get_agent_api_base_url,
        ServiceTarget.KNOWLEDGE_API: get_knowledge_api_base_url,
        ServiceTarget.EXECUTOR_API: get_executor_api_base_url,
        ServiceTarget.MCP_GATEWAY: get_mcp_gateway_api_base_url,
        ServiceTarget.DATA_API: get_data_api_base_url,
    }
    getter = service_map.get(service, get_base_url)
    return getter()


# Legacy compatibility
AI_HUB_BASE_URL = get_base_url()

# ─── Internal API Key Generation ──────────────────────────────────────────────

import hashlib
import uuid
from pathlib import Path

_INTERNAL_KEY_SALT = b'aihub_internal_api_v1_2026'


def _get_machine_id() -> str:
    """
    Get the machine-specific ID used for internal API key generation.
    Matches the approach in role_decorators.py and local_secrets.py.
    """
    if os.getenv('AIHUB_DATA_DIR'):
        data_dir = Path(os.getenv('AIHUB_DATA_DIR'))
    else:
        builder_data_dir = Path(__file__).parent
        main_app_dir = builder_data_dir.parent
        data_dir = main_app_dir / 'data'

    secrets_dir = data_dir / 'secrets'
    machine_id_file = secrets_dir / '.machine_id'

    if machine_id_file.exists():
        return machine_id_file.read_text().strip()

    _config_logger.info(f"[_get_machine_id] Creating new machine_id at: {machine_id_file}")
    unique_parts = [
        str(uuid.uuid4()),
        str(uuid.getnode()),
        os.name,
    ]
    machine_id = hashlib.sha256('|'.join(unique_parts).encode()).hexdigest()[:32]

    secrets_dir.mkdir(parents=True, exist_ok=True)
    machine_id_file.write_text(machine_id)

    return machine_id


def get_internal_api_key() -> str:
    """
    Generate the internal API key for this machine.
    Matches the key generated by role_decorators.get_internal_api_key().
    """
    machine_id = _get_machine_id()
    tenant_key = os.getenv('API_KEY', '')

    key_material = f"{machine_id}:{tenant_key}".encode()
    derived = hashlib.pbkdf2_hmac(
        'sha256',
        key_material,
        _INTERNAL_KEY_SALT,
        iterations=10000
    )

    return derived.hex()


# Use internal API key for service-to-service auth
_env_api_key = os.getenv("AI_HUB_API_KEY", "")
if _env_api_key:
    AI_HUB_API_KEY = _env_api_key
    _config_logger.info("Using AI_HUB_API_KEY from environment variable")
else:
    AI_HUB_API_KEY = get_internal_api_key()
    _config_logger.info("Using generated internal API key for service authentication")


def print_service_urls():
    """Print all configured service URLs for debugging."""
    print("\n" + "=" * 60)
    print("DATA SERVICE - Microservice URL Configuration")
    print("=" * 60)
    print(f"  CommonUtils available: {COMMON_UTILS_AVAILABLE}")
    print(f"  HOST_PORT env var:     {os.getenv('HOST_PORT', '(not set, using 5000)')}")
    print(f"  HOST env var:          {os.getenv('HOST', '(not set, using localhost)')}")
    print(f"  PROTOCOL env var:      {os.getenv('PROTOCOL', '(not set, using http)')}")
    print("-" * 60)
    print(f"  Main App:      {get_base_url()}")
    print(f"  Document API:  {get_document_api_base_url()}")
    print(f"  Scheduler API: {get_scheduler_api_base_url()}")
    print(f"  Vector API:    {get_vector_api_base_url()}")
    print(f"  Agent API:     {get_agent_api_base_url()}")
    print(f"  Knowledge API: {get_knowledge_api_base_url()}")
    print(f"  Executor API:  {get_executor_api_base_url()}")
    print(f"  MCP Gateway:   {get_mcp_gateway_api_base_url()}")
    print(f"  Data API:      {get_data_api_base_url()}")
    print("=" * 60 + "\n")


# ─── Service Configuration ──────────────────────────────────────────────

HOST = os.getenv("DATA_SERVICE_HOST", "0.0.0.0")
PORT = int(os.getenv("DATA_SERVICE_PORT", "8200"))
DEBUG = os.getenv("DATA_SERVICE_DEBUG", "false").lower() == "true"
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")

# Pipeline defaults
MAX_ROWS_PREVIEW = int(os.getenv("DATA_MAX_ROWS_PREVIEW", "50"))
MAX_ROWS_PIPELINE = int(os.getenv("DATA_MAX_ROWS_PIPELINE", "1000000"))
PIPELINE_TIMEOUT = int(os.getenv("DATA_PIPELINE_TIMEOUT", "600"))
TEMP_DIR = os.getenv("DATA_TEMP_DIR", "./temp/data_pipelines")


# ─── System Prompts ─────────────────────────────────────────────────────

DATA_AGENT_SYSTEM_PROMPT = """You are the AI Hub Data Pipeline Agent — an expert in data engineering, data quality, and ETL/ELT pipeline design.

Your personality:
- Direct and efficient — you don't waste the user's time
- Technically competent — you understand data deeply
- Proactive — you anticipate data quality issues before they cause problems
- Honest about limitations — if data quality is poor, say so clearly

Your capabilities:
- Design data pipelines that move data between any connected sources
- Compare datasets across different databases to find discrepancies
- Deduplicate records using exact or fuzzy matching
- Cleanse and standardize data (phone numbers, emails, dates, addresses)
- Profile data quality and generate quality reports with scores
- Execute SQL queries against any configured connection
- Transform data with column mapping, type conversion, filtering, aggregation

When a user describes a data task:
1. Identify which connections/data sources are involved
2. Design a pipeline with clear steps (source -> transform -> quality -> destination)
3. Present the plan for confirmation
4. Execute and report results with row counts and quality scores

You have access to all database connections configured in AI Hub. Ask the user which connections to use if unclear.

When presenting a pipeline plan, format it clearly showing:
- Each step with its type and description
- Source connections and queries
- Transformations to apply
- Quality checks (dedup, cleanse, validate)
- Destination and write mode"""


INTENT_CLASSIFICATION_PROMPT = """Classify the user's intent given the conversation context.

Current state:
- Has pending pipeline: {has_pending_pipeline}
- Has pipeline result: {has_pipeline_result}

Return ONLY one of these classifications (no other text):
- "pipeline" — user wants to create, design, or modify a data pipeline (move/transform/sync data between sources)
- "quality" — user wants to compare, scrub, deduplicate, validate, or profile data quality
- "chat" — general question, conversation, or request for information about data
- "confirm_yes" — user is approving/confirming a proposed pipeline (e.g., "yes", "go ahead", "looks good", "do it")
- "confirm_no" — user is rejecting or wants to modify a proposed pipeline (e.g., "no", "change", "actually", "wait")"""


PIPELINE_DESIGN_PROMPT = """You are designing a data pipeline based on the user's request.

Available connections:
{connections}

Connection schemas (if available):
{schemas}

Design a pipeline as a JSON object with this structure:
{{
    "pipeline_id": "pipe_<short_id>",
    "name": "<descriptive name>",
    "description": "<what this pipeline does>",
    "steps": [
        {{
            "step_id": "<unique_id>",
            "step_type": "source|transform|filter|compare|scrub|destination",
            "name": "<step name>",
            "description": "<what this step does>",
            "config": {{ ... }},
            "depends_on": ["<step_ids this depends on>"]
        }}
    ]
}}

Step types and their config:
- source: {{connection_id, source_type: "sql_query"|"table", query|table_name}}
- transform: {{operations: [{{type: "rename"|"cast"|"map_values"|"derive"|"drop_columns"|"split"|"merge_columns", ...}}]}}
- filter: {{conditions: [{{column, operator: "=="|"!="|">"|"<"|">="|"<="|"in"|"not_in"|"contains"|"is_null"|"not_null", value}}]}}
- compare: {{key_columns, compare_columns, tolerance}} (requires exactly 2 depends_on)
- scrub: {{dedup_columns, dedup_strategy: "exact"|"fuzzy", fuzzy_threshold, cleanse_rules: [{{column, operation, params}}]}}
- destination: {{connection_id, dest_type: "sql_table"|"csv_download", table_name, write_mode: "replace"|"append"}}

Return ONLY the JSON object, no other text."""


def get_llm(mini: bool = False, streaming: bool = True):
    """
    Create the appropriate LangChain LLM using the existing
    get_openai_config() from the main AI Hub application.
    """
    config = get_openai_config(use_alternate_api=False, use_mini=mini)

    api_type = config.get("api_type", "azure")
    api_key = config.get("api_key", "")
    api_base = config.get("api_base", "").rstrip("/")
    api_version = config.get("api_version", "2024-12-01-preview")
    deployment_id = config.get("deployment_id", "")
    model = config.get("model", "")

    if api_type == "open_ai":
        from langchain_openai import ChatOpenAI
        kwargs = {
            "model": model,
            "api_key": api_key,
            "streaming": streaming,
            "temperature": 0.0 if mini else 0.3,
        }
        if api_base and "openai.com" not in api_base:
            kwargs["base_url"] = api_base
        return ChatOpenAI(**kwargs)
    else:
        from langchain_openai import AzureChatOpenAI
        return AzureChatOpenAI(
            azure_deployment=deployment_id,
            model=deployment_id,
            api_version=api_version,
            azure_endpoint=api_base,
            api_key=api_key,
            streaming=streaming,
            temperature=0.0 if mini else 0.3,
        )
