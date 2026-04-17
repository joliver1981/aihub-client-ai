"""
Command Center Service Configuration
=======================================
Mirrors builder_config.py — single source of truth for LLM credentials,
service URLs, and internal API key generation.
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

# Load .env from the main app directory FIRST
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
    """Build a service URL using the same logic as CommonUtils.py."""
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
        return _build_service_url(port_offset=0, default_port=5000)

    def get_document_api_base_url():
        return _build_service_url(port_offset=10, default_port=5000)

    def get_scheduler_api_base_url():
        return _build_service_url(port_offset=20, default_port=5000)

    def get_vector_api_base_url():
        return _build_service_url(port_offset=30, default_port=5000)

    def get_agent_api_base_url():
        return _build_service_url(port_offset=40, default_port=5000)

    def get_knowledge_api_base_url():
        return _build_service_url(port_offset=50, default_port=5000)

    def get_executor_api_base_url():
        return _build_service_url(port_offset=60, default_port=5000)

    def get_mcp_gateway_api_base_url():
        return _build_service_url(port_offset=70, default_port=5000)


def get_data_api_base_url():
    """Data Pipeline API URL (DATA_SERVICE_PORT, default 8200)"""
    protocol = os.getenv('PROTOCOL', 'http')
    host = os.getenv('HOST', 'localhost')
    if host == "0.0.0.0":
        host = _get_local_ip()
    port = int(os.getenv('DATA_SERVICE_PORT', '8200'))
    return f"{protocol}://{host}:{port}"


def get_builder_api_base_url():
    """Builder Service URL (port 8100)"""
    protocol = os.getenv('PROTOCOL', 'http')
    host = os.getenv('HOST', 'localhost')
    if host == "0.0.0.0":
        host = _get_local_ip()
    port = int(os.getenv('BUILDER_PORT', '8100'))
    return f"{protocol}://{host}:{port}"


def get_command_center_api_base_url():
    """Command Center API URL (HOST_PORT + 90)"""
    return _build_service_url(port_offset=90, default_port=5000)


# Service name constants for data client routing
class ServiceTarget:
    MAIN = "main"
    DOCUMENT_API = "document"
    SCHEDULER_API = "scheduler"
    VECTOR_API = "vector"
    AGENT_API = "agent"
    KNOWLEDGE_API = "knowledge"
    EXECUTOR_API = "executor"
    MCP_GATEWAY = "mcp"
    DATA_API = "data"
    BUILDER = "builder"


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
        ServiceTarget.BUILDER: get_builder_api_base_url,
    }
    getter = service_map.get(service, get_base_url)
    return getter()


# Legacy compatibility
AI_HUB_BASE_URL = get_base_url()


# ─── Internal API Key Generation ──────────────────────────────────────────

import hashlib
import uuid
from pathlib import Path

_INTERNAL_KEY_SALT = b'aihub_internal_api_v1_2026'


def _get_machine_id() -> str:
    """Get the machine-specific ID used for internal API key generation."""
    if os.getenv('AIHUB_DATA_DIR'):
        data_dir = Path(os.getenv('AIHUB_DATA_DIR'))
    elif os.getenv('APP_ROOT'):
        data_dir = Path(os.getenv('APP_ROOT')) / 'data'
    else:
        cc_service_dir = Path(__file__).parent
        main_app_dir = cc_service_dir.parent
        data_dir = main_app_dir / 'data'

    secrets_dir = data_dir / 'secrets'
    machine_id_file = secrets_dir / '.machine_id'

    if machine_id_file.exists():
        return machine_id_file.read_text().strip()

    _config_logger.info(f"[_get_machine_id] Creating new machine_id at: {machine_id_file}")
    unique_parts = [str(uuid.uuid4()), str(uuid.getnode()), os.name]
    machine_id = hashlib.sha256('|'.join(unique_parts).encode()).hexdigest()[:32]
    secrets_dir.mkdir(parents=True, exist_ok=True)
    machine_id_file.write_text(machine_id)
    return machine_id


def get_internal_api_key() -> str:
    """Generate the internal API key matching role_decorators.get_internal_api_key()."""
    machine_id = _get_machine_id()
    tenant_key = os.getenv('API_KEY', '')
    key_material = f"{machine_id}:{tenant_key}".encode()
    derived = hashlib.pbkdf2_hmac('sha256', key_material, _INTERNAL_KEY_SALT, iterations=10000)
    return derived.hex()


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
    print("COMMAND CENTER SERVICE - Microservice URL Configuration")
    print("=" * 60)
    print(f"  CommonUtils available: {COMMON_UTILS_AVAILABLE}")
    print(f"  HOST_PORT env var:     {os.getenv('HOST_PORT', '(not set, using 5000)')}")
    print(f"  HOST env var:          {os.getenv('HOST', '(not set, using localhost)')}")
    print(f"  PROTOCOL env var:      {os.getenv('PROTOCOL', '(not set, using http)')}")
    print("-" * 60)
    print(f"  Main App:        {get_base_url()}")
    print(f"  Document API:    {get_document_api_base_url()}")
    print(f"  Scheduler API:   {get_scheduler_api_base_url()}")
    print(f"  Vector API:      {get_vector_api_base_url()}")
    print(f"  Agent API:       {get_agent_api_base_url()}")
    print(f"  Knowledge API:   {get_knowledge_api_base_url()}")
    print(f"  Executor API:    {get_executor_api_base_url()}")
    print(f"  MCP Gateway:     {get_mcp_gateway_api_base_url()}")
    print(f"  Data API:        {get_data_api_base_url()}")
    print(f"  Builder:         {get_builder_api_base_url()}")
    print(f"  Command Center:  {get_command_center_api_base_url()}")
    print("=" * 60 + "\n")


# ─── Service Configuration ──────────────────────────────────────────────

HOST = os.getenv("CC_HOST", "0.0.0.0")
PORT = int(os.getenv("CC_PORT", "5091"))
DEBUG = os.getenv("CC_DEBUG", "false").lower() == "true"
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")


# ─── Feature Toggles ────────────────────────────────────────────────────

IMAGE_GENERATION_ENABLED = os.getenv("CC_IMAGE_GENERATION_ENABLED", "true").lower() == "true"
"""Enable/disable DALL-E image generation. Set to False to disable for clients who don't need it."""

DOCUMENT_SEARCH_ENABLED = os.getenv("CC_DOCUMENT_SEARCH_ENABLED", "true").lower() == "true"
"""Enable/disable document search tool. Set to False for instances without document functionality."""

ANSWER_QUALITY_GATE_ENABLED = os.getenv("CC_ANSWER_QUALITY_GATE", "true").lower() == "true"
"""Enable/disable the Answer Quality Gate. Set to False to skip post-response evaluation."""

AQG_CONFIDENCE_THRESHOLD = float(os.getenv("CC_AQG_CONFIDENCE_THRESHOLD", "0.8"))
"""Minimum confidence (0.0-1.0) for AQG to trigger enrichment actions. Higher = more conservative."""

AQG_GEO_CONFIDENCE_THRESHOLD = float(os.getenv("CC_AQG_GEO_CONFIDENCE", str(AQG_CONFIDENCE_THRESHOLD)))
"""Confidence threshold specifically for geographic enrichment. Defaults to AQG_CONFIDENCE_THRESHOLD."""

AQG_KNOWLEDGE_CONFIDENCE_THRESHOLD = float(os.getenv("CC_AQG_KNOWLEDGE_CONFIDENCE", str(AQG_CONFIDENCE_THRESHOLD)))
"""Confidence threshold specifically for knowledge enrichment. Defaults to AQG_CONFIDENCE_THRESHOLD."""

USE_PRINCIPLED_ROUTING = os.getenv("CC_PRINCIPLED_ROUTING", "true").lower() == "true"
"""Use two-stage principled routing in classify_intent (deterministic rules + intent-based LLM prompt).
Set to false to revert to legacy prompt-based routing."""

USE_ROUTE_MEMORY = os.getenv("CC_ROUTE_MEMORY", "true").lower() == "true"
"""Enable the Route Memory system — learns optimal query→agent routes over time.
When enabled, shortcuts the full classify_intent + agent picker LLM calls for
queries that have a confident historical match.  Set to false to disable."""

USE_SESSION_INSIGHTS = os.getenv("CC_SESSION_INSIGHTS", "true").lower() == "true"
"""Enable session insight extraction — after multi-turn conversations, the system
extracts factual discoveries (e.g., 'Store S009 lease is doc type commercial_lease_agreement')
and stores them as reusable insights.  These surface in future conversations to avoid
repeating the same discovery process.  Set to false to disable."""

SESSION_INSIGHT_MIN_TURNS = int(os.getenv("CC_SESSION_INSIGHT_MIN_TURNS", "3"))
"""Minimum number of user turns in a session before insight extraction triggers.
Lower values extract more aggressively; higher values wait for longer explorations."""

USE_CAPABILITY_ROUTER = os.getenv("CC_CAPABILITY_ROUTER", "true").lower() == "true"
"""Enable the mini-LLM capability router in classify_intent.
When enabled (default), a cheap mini-LLM call decides whether the user's
message maps to a CC-native capability (document search, web search,
maps, image generation, run tool, build) before falling through to the
full intent classifier. Replaces the legacy USE_INTENT_HEURISTICS keyword
shortcuts with a semantic classifier that scales as new tools appear."""

USE_INTENT_HEURISTICS = os.getenv("CC_INTENT_HEURISTICS", "false").lower() == "true"
"""Enable keyword-based heuristic shortcuts in classify_intent.
When enabled, hard-coded keyword matches (e.g. 'search for', 'on a map',
'generate image') bypass the LLM intent classifier and route directly.
Disabled by default — the LLM classifier handles all intent routing, which
avoids false positives on multi-step or nuanced requests and improves
automatically as models improve.  Set to true to re-enable heuristics."""


# ─── Per-step LLM size control ──────────────────────────────────────────

# Master switch: when false, ALL steps use the full LLM (no mini anywhere).
# When true (default), individual steps respect the MINI_LLM_STEPS map below.
USE_MINI_LLM = os.getenv("CC_USE_MINI_LLM", "true").lower() == "true"
"""Master switch for mini-LLM usage across the CC pipeline.
Set CC_USE_MINI_LLM=false to force the full model everywhere.
When true, each step checks MINI_LLM_STEPS to decide."""

# Per-step overrides: set CC_MINI_<STEP>=false to force the full model for
# that specific step.  Only consulted when USE_MINI_LLM is true.
#
# Steps that default to FULL model (false) — too nuanced for mini:
#   intent_classification      — classifies user intent across 7 categories
#   task_decomposition         — breaks complex requests into ordered sub-tasks
#   active_delegation_routing  — CONTINUE/REROUTE/CC_CAPABLE; wrong answer = wrong pipeline
#   agent_picker               — matching query to best agent from many candidates
#   tool_export_structurer     — structuring unstructured results into tabular JSON
#   tool_map_structurer        — extracting geographic data from unstructured text
#   builder_distiller          — summarizing builder JSON into user-facing text
#
# Steps that default to mini (true):
#   (remaining steps are simple classification/extraction tasks)
#   response_sanitizer         — re-process garbled JSON into readable text
#   delegation_result_classifier — classify agent response as success/failure/empty
#   alternative_agent_finder   — score/rank fallback agents
#   agent_selection_parser     — extract agent ID from user reply
#   agent_picker               — pick best agent for a query
#   tool_export_structurer     — structure prior results for file export
#   tool_map_structurer        — extract location data for maps
#   tool_email_extractor       — extract email params from description
#   builder_distiller          — distill builder JSON into user-facing text
#   builder_affirmative_detector — detect yes/no confirmation
#   answer_quality_gate        — evaluate response quality for enrichment
#   capability_router          — fast CC-native capability classifier (see USE_CAPABILITY_ROUTER)
#   export_intent_detector     — detect export intent + format from free-form text
MINI_LLM_STEPS: dict = {
    "intent_classification":        os.getenv("CC_MINI_INTENT_CLASSIFICATION", "false").lower() == "true",
    "task_decomposition":           os.getenv("CC_MINI_TASK_DECOMPOSITION", "false").lower() == "true",
    "active_delegation_routing":    os.getenv("CC_MINI_ACTIVE_DELEGATION_ROUTING", "false").lower() == "true",
    "response_sanitizer":           os.getenv("CC_MINI_RESPONSE_SANITIZER", "true").lower() == "true",
    "delegation_result_classifier": os.getenv("CC_MINI_DELEGATION_RESULT_CLASSIFIER", "true").lower() == "true",
    "alternative_agent_finder":     os.getenv("CC_MINI_ALTERNATIVE_AGENT_FINDER", "true").lower() == "true",
    "agent_selection_parser":       os.getenv("CC_MINI_AGENT_SELECTION_PARSER", "true").lower() == "true",
    "agent_picker":                 os.getenv("CC_MINI_AGENT_PICKER", "false").lower() == "true",
    "tool_export_structurer":       os.getenv("CC_MINI_TOOL_EXPORT_STRUCTURER", "false").lower() == "true",
    "tool_map_structurer":          os.getenv("CC_MINI_TOOL_MAP_STRUCTURER", "false").lower() == "true",
    "tool_email_extractor":         os.getenv("CC_MINI_TOOL_EMAIL_EXTRACTOR", "true").lower() == "true",
    "builder_distiller":            os.getenv("CC_MINI_BUILDER_DISTILLER", "false").lower() == "true",
    "builder_affirmative_detector": os.getenv("CC_MINI_BUILDER_AFFIRMATIVE_DETECTOR", "true").lower() == "true",
    "answer_quality_gate":          os.getenv("CC_MINI_ANSWER_QUALITY_GATE", "true").lower() == "true",
    "capability_router":            os.getenv("CC_MINI_CAPABILITY_ROUTER", "true").lower() == "true",
    "export_intent_detector":       os.getenv("CC_MINI_EXPORT_INTENT_DETECTOR", "true").lower() == "true",
}


def get_step_llm(step: str, *, streaming: bool = False):
    """Get the LLM for a specific pipeline step, respecting per-step config.

    Usage:  llm = get_step_llm("agent_picker")

    Resolution order:
    1. USE_MINI_LLM=false  → full model (ignores per-step settings)
    2. MINI_LLM_STEPS[step]=false  → full model for this step
    3. Otherwise  → mini model
    """
    if not USE_MINI_LLM:
        return get_llm(mini=False, streaming=streaming)
    use_mini = MINI_LLM_STEPS.get(step, True)
    return get_llm(mini=use_mini, streaming=streaming)


# ─── System Prompts ─────────────────────────────────────────────────────

COMMAND_CENTER_SYSTEM_PROMPT = """You are the AI Hub Command Center — the central intelligence layer of the AI Hub platform. You have visibility into ALL agents, tools, workflows, data sources, and capabilities across the entire platform.

Your role:
- You are the user's single point of contact for ANYTHING they need
- You delegate to specialist agents when they exist (Sales Agent, Data Agent, etc.)
- You query data from any connected source (databases, documents, APIs, web)
- You produce rich visual output (charts, tables, maps, KPIs, downloadable files)
- You learn from each user's patterns and proactively suggest helpful actions
- You create your own tools when you encounter capability gaps

Your personality:
- Direct and efficient — get to the answer fast
- Proactive — anticipate what the user needs based on their patterns
- Resourceful — use every available agent, tool, and data source
- Transparent — when delegating, briefly mention which agent/tool you're using
- Never fabricate data — if you can't retrieve real data, say so clearly

When handling requests:
1. Analyze the intent — is this a question, analysis request, delegation, or creation task?
2. Scan the landscape — what agents, tools, and data sources are available?
3. Route optimally — delegate to the best specialist or handle directly
4. Render appropriately — text, chart, table, map, file download, or combination
5. Remember — track this interaction to improve future suggestions"""


STRUCTURED_RESPONSE_FORMAT = """
RESPONSE FORMAT:
Respond with a JSON array of content blocks. No text outside the JSON array.

Block types:
1. text - {"type": "text", "content": "Markdown string"}
2. chart - {"type": "chart", "chartType": "bar|line|pie|area|doughnut", "title": "Title",
            "data": [{"label": "A", "value": 10}], "xKey": "label", "yKeys": ["value"],
            "colors": ["#3b82f6"]}
3. table - {"type": "table", "title": "Title", "headers": ["Col1"], "rows": [["val"]]}
4. kpi - {"type": "kpi", "cards": [{"label": "Metric", "value": "$1.2M", "trend": "+5%", "trendDirection": "up"}]}
5. map - {"type": "map", "center": [lat, lng], "zoom": 10,
          "markers": [{"lat": 0, "lng": 0, "label": "Name", "popup": "Details"}]}
6. artifact - {"type": "artifact", "name": "file.xlsx", "artifactType": "excel",
               "description": "What this file contains"}
7. image - {"type": "image", "src": "data:image/png;base64,...", "alt": "Description"}

Guidelines:
- Escape double quotes inside JSON strings with backslash
- Use text blocks for explanations and insights
- Use chart blocks for comparisons (bar), trends (line), composition (pie)
- Use table blocks for detailed multi-column data (under 20 rows)
- Use kpi blocks for key metric summaries
- Use map blocks for geographic data with markers
- Use artifact blocks when the user needs a downloadable file
- Interleave text and visual blocks for rich responses
- For simple conversational replies, a single text block is fine
- Colors: #3b82f6 blue, #10b981 green, #f59e0b amber, #8b5cf6 purple, #ef4444 red, #06b6d4 cyan
"""


INTENT_CLASSIFICATION_PROMPT = """Classify the user's intent given the conversation context and available platform resources.

Available agents: {agent_summary}
Available tools: {tool_summary}

Return ONLY one of these classifications (no other text):
- "chat" - The chat handler is the Command Center's own toolbox. It handles:
  * General conversation, greetings, questions about the platform
  * Platform metadata (listing agents, connections, tools, workflows, capabilities)
  * DOCUMENT SEARCH — finding documents, files, contracts, invoices, leases, policies, reports, records in the document repository
  * Web search — current news, weather, stock prices, real-time information
  * Maps and geographic visualization
  * Image generation
  * File exports (Excel, CSV, PDF) of platform data or prior results
  * Email sending
  * User preferences and memory commands
  Any request involving documents, files, or records in the document repository is "chat" — NOT "query".
- "query" - fetch specific DATA from a DATABASE via a data agent (e.g. "show me sales by region", "how many orders last month", "revenue report"). ONLY for actual database/SQL queries against structured data tables. If the user is looking for a document or file (not database rows), this is "chat".
- "analyze" - analyze specific database data, find patterns, compare, or explain trends in actual data
- "delegate" - request best handled by a specific existing agent (e.g. "ask the HR agent about PTO", "use the sales agent")
- "build" - create, configure, or modify an agent, workflow, connection, or other platform resource
- "multi_step" - complex request needing multiple agents or tools in sequence (e.g. "compare sales from EDW with HR headcount data", "find lease documents and export a summary to Excel")
- "create_tool" - user explicitly wants a new custom capability or tool

CRITICAL DISTINCTIONS:
1. Document/file search vs database query:
   - Looking for a document, contract, lease, invoice, policy, report, file → "chat" (uses document search tool)
   - Looking for database rows, metrics, aggregates from structured tables → "query" (uses data agent)
2. Platform metadata vs database data:
   - Platform metadata = agents, connections, tools, workflows → "chat"
   - Database data = sales, revenue, orders, customers, inventory → "query"
3. Multi-step: when a request requires BOTH searching/querying AND a follow-up action (export, summarize, email) across different tools or agents → "multi_step"

Examples:
- "find our lease agreement for store S009" → "chat" (document search)
- "search for contracts mentioning renewal terms" → "chat" (document search)
- "find invoices from last quarter" → "chat" (document search)
- "look up our return policy" → "chat" (document search)
- "what's the weather in Chicago" → "chat" (web search)
- "show me sales by region" → "query" (database data)
- "export sales report to Excel" → "query" (database data + export, handled by data agent)
- "export full general agent list to Excel" → "chat" (platform metadata + export)
- "list all my data connections" → "chat" (platform metadata)
- "remember to always use the EDW agent" → "chat" (user preference)
- "find lease documents and export a summary to Excel" → "multi_step" (document search + export)
- "compare EDW sales with Postgres inventory" → "multi_step" (multiple data sources)"""


def get_llm(mini: bool = False, streaming: bool = True):
    """Create the appropriate LangChain LLM using the existing get_openai_config()."""
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
