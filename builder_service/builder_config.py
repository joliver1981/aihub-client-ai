"""
Builder Service Configuration
===============================
Leverages the existing api_keys_config from the main AI Hub app
so there's a single source of truth for LLM credentials.
"""

import os
import sys

# Disable LangSmith telemetry - must be set before any langchain imports
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"

# Add parent directory to path so we can import from the main app
PARENT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

# Load .env from the main app directory FIRST - this ensures APP_ROOT, API_KEY,
# HOST_PORT, and other env vars are available before secure_config or key generation.
# This mirrors the main app's config.py which calls load_dotenv() at the top.
from dotenv import load_dotenv
_env_path = os.path.join(PARENT_DIR, '.env')
if os.path.isfile(_env_path):
    load_dotenv(_env_path)

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
# Falls back to local implementation if CommonUtils has import issues
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

    # Local implementations matching CommonUtils.py logic
    def get_base_url():
        """Main app URL (HOST_PORT + 0)"""
        return _build_service_url(port_offset=0, default_port=5000)

    def get_document_api_base_url():
        """Document API URL (HOST_PORT + 10)"""
        return _build_service_url(port_offset=10, default_port=5000)

    def get_scheduler_api_base_url():
        """Scheduler API URL (HOST_PORT + 20)"""
        return _build_service_url(port_offset=20, default_port=5000)

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
    MAIN = "main"               # Main app (get_base_url)
    DOCUMENT_API = "document"   # Document processing service
    SCHEDULER_API = "scheduler" # Jobs/scheduling service
    VECTOR_API = "vector"       # Vector search service
    AGENT_API = "agent"         # Agent execution service
    KNOWLEDGE_API = "knowledge" # Knowledge/RAG service
    EXECUTOR_API = "executor"   # Workflow executor service
    MCP_GATEWAY = "mcp"         # MCP gateway service
    DATA_API = "data"           # Data pipeline service


def get_service_url(service: str) -> str:
    """
    Get the base URL for a microservice.

    Args:
        service: One of the ServiceTarget constants

    Returns:
        Base URL for the service
    """
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
# This generates the same internal API key as role_decorators.py in the main app.
# Used for service-to-service authentication when builder makes API calls.

import hashlib
import uuid
from pathlib import Path

_INTERNAL_KEY_SALT = b'aihub_internal_api_v1_2026'


def _get_machine_id() -> str:
    """
    Get the machine-specific ID used for internal API key generation.
    This matches the approach used in role_decorators.py and local_secrets.py.
    """
    # Use AIHUB_DATA_DIR if set, otherwise APP_ROOT, otherwise calculate from __file__
    if os.getenv('AIHUB_DATA_DIR'):
        data_dir = Path(os.getenv('AIHUB_DATA_DIR'))
    elif os.getenv('APP_ROOT'):
        data_dir = Path(os.getenv('APP_ROOT')) / 'data'
    else:
        # Existing fallback: calculate from __file__ location
        builder_service_dir = Path(__file__).parent
        main_app_dir = builder_service_dir.parent
        data_dir = main_app_dir / 'data'

    secrets_dir = data_dir / 'secrets'
    machine_id_file = secrets_dir / '.machine_id'

    if machine_id_file.exists():
        return machine_id_file.read_text().strip()

    # Generate new machine ID if doesn't exist
    _config_logger.info(f"[_get_machine_id] Creating new machine_id at: {machine_id_file}")
    unique_parts = [
        str(uuid.uuid4()),
        str(uuid.getnode()),  # MAC address based
        os.name,
    ]
    machine_id = hashlib.sha256('|'.join(unique_parts).encode()).hexdigest()[:32]

    # Ensure directory exists
    secrets_dir.mkdir(parents=True, exist_ok=True)
    machine_id_file.write_text(machine_id)

    return machine_id


def get_internal_api_key() -> str:
    """
    Generate the internal API key for this machine.
    This key is derived from machine ID + tenant key + app salt.
    It matches the key generated by role_decorators.get_internal_api_key().
    """
    machine_id = _get_machine_id()
    tenant_key = os.getenv('API_KEY', '')

    # Combine machine ID + tenant key + salt for a unique internal key
    key_material = f"{machine_id}:{tenant_key}".encode()

    # Use PBKDF2-like derivation for the internal key
    derived = hashlib.pbkdf2_hmac(
        'sha256',
        key_material,
        _INTERNAL_KEY_SALT,
        iterations=10000
    )

    return derived.hex()


# Use internal API key for service-to-service auth, fall back to env var if set
_env_api_key = os.getenv("AI_HUB_API_KEY", "")
if _env_api_key:
    AI_HUB_API_KEY = _env_api_key
    _config_logger.info("Using AI_HUB_API_KEY from environment variable")
else:
    AI_HUB_API_KEY = get_internal_api_key()
    _config_logger.info("Using generated internal API key for service authentication")

# Diagnostic: log key derivation inputs so mismatches can be debugged
_diag_machine_id = _get_machine_id()
_diag_tenant_key = os.getenv('API_KEY', '')
_diag_data_dir = (
    Path(os.getenv('AIHUB_DATA_DIR')) if os.getenv('AIHUB_DATA_DIR')
    else Path(os.getenv('APP_ROOT')) / 'data' if os.getenv('APP_ROOT')
    else Path(__file__).parent.parent / 'data'
)
_diag_tenant_prefix = f"{_diag_tenant_key[:8]}..." if _diag_tenant_key else "(empty)"
_config_logger.info(
    f"Internal API key diagnostics: "
    f"key_prefix={AI_HUB_API_KEY[:12]}..., "
    f"machine_id={_diag_machine_id[:12]}..., "
    f"tenant_key_set={bool(_diag_tenant_key)}, "
    f"tenant_key_prefix={_diag_tenant_prefix}, "
    f"data_dir={_diag_data_dir.resolve()}, "
    f"machine_id_file={(_diag_data_dir / 'secrets' / '.machine_id').resolve()}"
)


def print_service_urls():
    """Print all configured service URLs for debugging."""
    print("\n" + "=" * 60)
    print("BUILDER SERVICE - Microservice URL Configuration")
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

HOST = os.getenv("BUILDER_HOST", "0.0.0.0")
PORT = int(os.getenv("BUILDER_PORT", "8100"))
DEBUG = os.getenv("BUILDER_DEBUG", "false").lower() == "true"
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")


# ─── System Prompts ─────────────────────────────────────────────────────

BUILDER_SYSTEM_PROMPT = """You are the AI Hub Builder Agent - an expert solutions architect that helps users build, configure, and manage AI agents, workflows, tools, and integrations on the AI Hub platform.

Your personality:
- Direct and efficient - you don't waste the user's time
- Technically competent - you understand the platform deeply
- Proactive - you anticipate what the user needs and suggest it
- Resourceful - you exhaust every possibility to achieve the user's goal
- Honest about limitations - if something can't be done, say so clearly
- Never fabricate or invent data. If you cannot retrieve real data from a connected system, politely inform the user.

Your capabilities:
- Create and configure AI agents (general chatbots and data agents)
- Build workflow automations with triggers, conditions, and approvals
- Set up database connections for natural language querying
- Manage custom tools, knowledge bases, and integrations
- Configure email endpoints, MCP servers, and scheduled jobs
- Import/export agents as portable packages
- Create custom Python tools when a needed capability doesn't exist yet

When the user wants to build something:
1. Think like a solutions architect - analyze the full picture
2. Identify ALL prerequisites and dependencies
3. Check what already exists vs. what needs to be created
4. Identify any missing information you need from the user
5. Present a clear plan with the specific steps you'll take
6. Wait for their approval before executing
7. Execute each step and report results
8. If something fails, analyze why and try to fix it

Important notes:
- For knowledge.attach: the system supports BOTH uploaded files AND direct filesystem paths. If the user provides a full filesystem path (e.g., "C:\\path\\to\\file.txt"), use it directly in the plan — do NOT ask them to upload the file separately.
- For custom tools (tools.create): the "code" field must contain the function BODY ONLY — do NOT wrap it in a def statement. The system automatically wraps the code in a function with the correct signature.

When the user is chatting generally:
- Answer questions about the platform's features and capabilities
- Provide guidance on best practices for agent design and workflow architecture
- Help troubleshoot issues with existing configurations

Always be conversational and natural. Don't list capabilities unprompted. Respond to what the user actually asked."""


# ─── Structured Response Format ─────────────────────────────────────────
# Appended to all user-facing node system prompts so the LLM returns
# a JSON array of typed content blocks that the frontend can render as
# rich mixed content (text, charts, tables).

STRUCTURED_RESPONSE_FORMAT = """
RESPONSE FORMAT:
Please respond with a JSON array of content blocks. Each block has a "type" field.
Do not include any text outside the JSON array - no preamble, no trailing text, no markdown fences.

Block types:
1. text - {"type": "text", "content": "Markdown string here"}
2. chart - {"type": "chart", "chartType": "bar|line|pie|area", "title": "Chart Title",
            "data": [{"label": "A", "value": 10}, ...], "xKey": "label", "yKeys": ["value"],
            "colors": ["#3b82f6"]}
3. table - {"type": "table", "title": "Table Title", "headers": ["Col1", "Col2"],
            "rows": [["val1", "val2"], ...]}

Guidelines:
- Important: Inside JSON string values, escape all double quotes with a backslash: \" - never use raw " inside a "content" value. For example: "content": "Found results for \\"lease\\"" NOT "content": "Found results for "lease""
- Use text blocks for explanations, insights, and conversational responses
- Use chart blocks when comparing categories (bar), showing trends (line), composition (pie), or volume over time (area)
- Use table blocks for detailed data with multiple columns
- Interleave text and visual blocks for richer responses: explanation → chart → insight → table
- Keep chart data to 3-15 data points. Keep tables under 20 rows.
- For simple conversational replies, a single text block is fine
- Format currency as "$1.2M", percentages as "45.2%"
- Colors palette: #3b82f6 blue, #10b981 green, #f59e0b amber, #8b5cf6 purple, #ef4444 red, #06b6d4 cyan, #ec4899 pink, #f97316 orange

Example response:
[{"type": "text", "content": "## Overview\\n\\nHere's a summary of your agents."},{"type": "chart", "chartType": "bar", "title": "Agents by Type", "data": [{"name": "General", "count": 5}, {"name": "Data", "count": 3}], "xKey": "name", "yKeys": ["count"], "colors": ["#3b82f6", "#10b981"]},{"type": "text", "content": "Most of your agents are general-purpose."}]
"""


INTENT_CLASSIFICATION_PROMPT = """Classify the user's intent given the conversation context.

Current state:
- Pending confirmation: {has_pending_confirmation}
- Active plan: {has_active_plan}
- Execution in progress: {is_executing}

Return ONLY one of these classifications (no other text):
- "build" - user wants to create, configure, set up, modify, delete, or remove something on the platform
- "query" - user wants to list, show, view, search, check, or get information about existing resources (e.g., "show me all my agents", "list my workflows", "what connections do I have", "search documents for X")
- "chat" - general question, conversation, or request for information about how the platform works (not about specific existing resources)
- "confirm_yes" - user is approving/confirming a proposed plan (e.g., "yes", "go ahead", "looks good", "do it")
- "confirm_no" - user is rejecting or wants to modify a proposed plan (e.g., "no", "change", "actually", "wait")
- "provide_context" - user is providing information that was requested (e.g., answering questions about database credentials, server addresses, table names, agent names, business rules, configuration values, or any other details the assistant asked for)

PRIORITY RULE: If the message contains action verbs like "update", "modify", "change", "add", "remove", "delete", "assign", "rename", "enable", "disable", or "provision", classify as "build" even if it also contains discovery verbs like "find", "show", "list", or "get". The user's goal is the mutation, not the lookup."""


def get_llm(mini: bool = False, streaming: bool = True):
    """
    Create the appropriate LangChain LLM using the existing
    get_openai_config() from the main AI Hub application.

    Args:
        mini: If True, use the smaller/cheaper model for routing tasks.
        streaming: If False, disable streaming (for internal extraction calls).
    """
    config = get_openai_config(use_alternate_api=False, use_mini=mini)

    api_type = config.get("api_type", "azure")
    api_key = config.get("api_key", "")
    api_base = config.get("api_base", "").rstrip("/")
    api_version = config.get("api_version", "2024-12-01-preview")
    deployment_id = config.get("deployment_id", "")
    model = config.get("model", "")

    if api_type == "open_ai":
        # Direct OpenAI or OpenAI-compatible endpoint
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
        # Azure OpenAI
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


def is_azure_content_filter_error(error: Exception) -> bool:
    """
    Detect if an error is an Azure OpenAI content filter rejection.

    Azure content filter errors typically include:
    - 'content_filter' in error message
    - 'filtered': True
    - 'ResponsibleAIPolicyViolation'
    - status code 400
    """
    error_str = str(error).lower()
    error_repr = repr(error).lower()

    # Check for common content filter indicators
    filter_indicators = [
        'content_filter',
        'jailbreak',
        'filtered',
        'responsibleaipolicyviolation',
        'content_management_policy',
    ]

    for indicator in filter_indicators:
        if indicator in error_str or indicator in error_repr:
            return True

    # Check status code in error
    if hasattr(error, 'status_code') and error.status_code == 400:
        if any(ind in error_str for ind in ['filter', 'policy', 'responsible']):
            return True

    return False


async def safe_llm_invoke(llm, messages: list, strip_structured_format: bool = False):
    """
    Wrapper around llm.ainvoke() with retry logic for Azure content filter errors.

    Retry strategy:
    1. First attempt: Full messages
    2. Second attempt (on filter error): Strip older conversation history + retry
    3. Third attempt (on filter error): Use ALTERNATE Azure deployment (may have different content filter)
    4. Fourth attempt (on filter error): Strip STRUCTURED_RESPONSE_FORMAT + alternate deployment
    5. Final fallback: Return user-friendly error message

    Args:
        llm: LangChain LLM instance
        messages: List of messages to send
        strip_structured_format: If True, removes STRUCTURED_RESPONSE_FORMAT from system prompts

    Returns:
        LLM response or error message
    """
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

    # Attempt 1: Try with full messages
    try:
        _config_logger.debug(f"[safe_llm_invoke] Attempt 1: Sending {len(messages)} messages")
        response = await llm.ainvoke(messages)
        return response
    except Exception as e:
        if not is_azure_content_filter_error(e):
            raise
        _config_logger.warning(f"[safe_llm_invoke] Attempt 1 failed - Azure content filter triggered: {e}")

    # Build minimal messages for retries
    system_msg = None
    user_messages = []
    ai_messages = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            system_msg = msg
        elif isinstance(msg, HumanMessage):
            user_messages.append(msg)
        elif isinstance(msg, AIMessage):
            ai_messages.append(msg)
        elif isinstance(msg, dict):
            if msg.get("role") == "user":
                user_messages.append(HumanMessage(content=msg.get("content", "")))
            elif msg.get("role") == "system":
                system_msg = SystemMessage(content=msg.get("content", ""))

    minimal_messages = []
    if system_msg:
        minimal_messages.append(system_msg)
    # Add last 2 user messages
    for msg in user_messages[-2:]:
        minimal_messages.append(msg)

    # Attempt 2: Strip older conversation history, same LLM
    try:
        _config_logger.info(f"[safe_llm_invoke] Attempt 2: Stripped to {len(minimal_messages)} messages")
        response = await llm.ainvoke(minimal_messages)
        _config_logger.info("[safe_llm_invoke] Attempt 2 succeeded")
        return response
    except Exception as e:
        if not is_azure_content_filter_error(e):
            raise
        _config_logger.warning(f"[safe_llm_invoke] Attempt 2 failed: {e}")

    # Attempt 3: Try with ALTERNATE Azure deployment (may have different content filter settings)
    try:
        _config_logger.info("[safe_llm_invoke] Attempt 3: Trying alternate Azure deployment")
        alt_llm = get_llm_alternate()
        if alt_llm:
            response = await alt_llm.ainvoke(minimal_messages)
            _config_logger.info("[safe_llm_invoke] Attempt 3 succeeded with alternate deployment")
            return response
        else:
            _config_logger.warning("[safe_llm_invoke] No alternate deployment available, skipping attempt 3")
    except Exception as e:
        if not is_azure_content_filter_error(e):
            raise
        _config_logger.warning(f"[safe_llm_invoke] Attempt 3 failed with alternate: {e}")

    # Attempt 4: Strip STRUCTURED_RESPONSE_FORMAT + use alternate or primary
    try:
        _config_logger.info("[safe_llm_invoke] Attempt 4: Stripping STRUCTURED_RESPONSE_FORMAT")
        stripped_messages = []
        for msg in minimal_messages:
            if isinstance(msg, SystemMessage):
                cleaned_content = msg.content
                for marker in ["RESPONSE FORMAT - CRITICAL INSTRUCTION:", "RESPONSE FORMAT:"]:
                    if marker in cleaned_content:
                        parts = cleaned_content.split(marker)
                        cleaned_content = parts[0].strip()
                        _config_logger.debug(f"[safe_llm_invoke] Stripped STRUCTURED_RESPONSE_FORMAT (marker: {marker})")
                        break
                stripped_messages.append(SystemMessage(content=cleaned_content))
            else:
                stripped_messages.append(msg)

        retry_llm = alt_llm if 'alt_llm' in dir() and alt_llm else llm
        response = await retry_llm.ainvoke(stripped_messages)
        _config_logger.info("[safe_llm_invoke] Attempt 4 succeeded")

        # Wrap response in JSON format since we stripped the structured format instruction
        if hasattr(response, 'content') and not response.content.strip().startswith('['):
            escaped = response.content.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
            response.content = f'[{{"type": "text", "content": "{escaped}"}}]'

        return response
    except Exception as e:
        _config_logger.error(f"[safe_llm_invoke] All retry attempts failed: {e}")

    # Final fallback
    fallback_message = "I had trouble processing that request. Could you try rephrasing or providing more context?"
    fallback_content = f'[{{"type": "text", "content": "{fallback_message}"}}]'
    return AIMessage(content=fallback_content)


def get_llm_alternate():
    """
    Try to create an LLM using the alternate Azure deployment.
    Returns None if no alternate is configured.
    """
    try:
        config = get_openai_config(use_alternate_api=True, use_mini=False)
        api_type = config.get("api_type", "azure")
        api_key = config.get("api_key", "")
        api_base = config.get("api_base", "").rstrip("/")
        api_version = config.get("api_version", "2024-12-01-preview")
        deployment_id = config.get("deployment_id", "")

        if not api_key or not deployment_id:
            _config_logger.debug("[get_llm_alternate] No alternate deployment configured")
            return None

        if api_type == "open_ai":
            from langchain_openai import ChatOpenAI
            kwargs = {"model": config.get("model", ""), "api_key": api_key, "streaming": False, "temperature": 0.3}
            if api_base and "openai.com" not in api_base:
                kwargs["base_url"] = api_base
            return ChatOpenAI(**kwargs)
        else:
            from langchain_openai import AzureChatOpenAI
            return AzureChatOpenAI(
                azure_deployment=deployment_id, model=deployment_id,
                api_version=api_version, azure_endpoint=api_base,
                api_key=api_key, streaming=False, temperature=0.3,
            )
    except Exception as e:
        _config_logger.warning(f"[get_llm_alternate] Failed to create alternate LLM: {e}")
        return None
