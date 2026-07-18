"""
Command Center Agent — Graph Nodes
=====================================
Each function is a node in the LangGraph state machine.
Nodes read from and write to CommandCenterState.
"""

import asyncio
import json
import logging
import os
import re
import time as _trace_time
from typing import Any, Optional
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from graph import CommandCenterState
from graph.tracing import trace_llm_call

logger = logging.getLogger(__name__)

# Max table rows from a DELEGATED agent's rich_content that get inlined (as
# markdown) into the follow-up LLM call. The UI-bound block can carry far more
# (renderer cap, ~10k) — but the LLM only needs a preview, and inlining
# thousands of rows would blow the context window.
_DELEGATED_TABLE_LLM_ROW_CAP = int(os.getenv("CC_DELEGATED_TABLE_LLM_ROW_CAP", "200"))

# Minimum role for privileged CC operations (platform mutations / tool exec).
# Role model: 1 = User, 2 = Developer, 3 = Admin.
MIN_DEV_ROLE = 2

# The code-flow authoring tools (converse-bound). When any of these runs, converse
# stamps a code_flow_context marker so classify_intent keeps follow-up turns in
# converse (AIHUB-0035).
_CODE_FLOW_TOOL_NAMES = frozenset({
    "create_code_flow", "add_code_step", "wire_steps", "update_step_code",
    "get_code_flow", "dry_run_code_flow", "run_code_flow", "schedule_code_flow",
    "unwire_steps", "remove_code_step",
})

# Native visual-workflow tools (the CC_AGENT="native" A/B agent). Any of these
# running stamps the continuity marker with kind="visual_workflow" so
# classify_intent keeps follow-up turns in converse — the visual_workflow twin
# of the AIHUB-0035 code-flow marker. Only registered/bound on native turns.
_WORKFLOW_TOOL_NAMES = frozenset({
    "list_workflows", "get_workflow_structure", "create_workflow",
    "add_workflow_node", "update_workflow_node", "remove_workflow_node",
    "wire_workflow_nodes", "unwire_workflow_nodes", "set_workflow_start",
    "set_workflow_variable", "run_workflow", "check_workflow_run",
    "insert_workflow_node_between", "list_data_connections",
})

# AIHUB-0048 F1: native mutating tools — a reply claiming a completed build
# change is only honest when one of these actually ran this turn.
_WORKFLOW_MUTATING_TOOL_NAMES = frozenset({
    "create_workflow", "add_workflow_node", "update_workflow_node",
    "remove_workflow_node", "wire_workflow_nodes", "unwire_workflow_nodes",
    "set_workflow_start", "set_workflow_variable",
    "insert_workflow_node_between",
})


def _has_dev_role(state) -> bool:
    """True when the verified user is Developer (2) or Admin (3).

    Identity comes from the JWT-verified user_context stamped into graph state
    (see /api/chat). Missing/low role → deny, so privileged ops stay gated even
    if user_context is absent.
    """
    try:
        role = (state.get("user_context") or {}).get("role")
        return role is not None and int(role) >= MIN_DEV_ROLE
    except (TypeError, ValueError):
        return False


# Platform-mutation (build) access. Mirrors builder_service/permissions.py:
# Developer+ by default, openable to all users via CC_BUILD_ALLOW_ALL_USERS.
# This is the EARLY UX gate; the builder service enforces the authoritative
# check regardless of how a request reaches it.
_BUILD_ALLOW_ALL = os.getenv("CC_BUILD_ALLOW_ALL_USERS", "false").lower() == "true"

# Polite refusal shown to a non-Developer who tries to build/create resources.
_BUILD_DENIED_MSG = (
    "Creating or modifying platform resources (agents, workflows, tools, "
    "documents, knowledge) requires a Developer role. Your account doesn't "
    "have permission to do that — please contact an administrator."
)


def _build_allowed(state) -> bool:
    """True if this user may perform platform mutations via the Builder."""
    return _BUILD_ALLOW_ALL or _has_dev_role(state)


# Code interpreter (run_python) availability. All users by default; flip
# CODE_INTERPRETER_ALLOW_ALL_USERS=false to restrict to Developer+ (role>=2).
_CODE_INTERPRETER_ENABLED = os.getenv("CODE_INTERPRETER_ENABLED", "true").lower() == "true"
_CODE_INTERPRETER_ALLOW_ALL = os.getenv("CODE_INTERPRETER_ALLOW_ALL_USERS", "true").lower() == "true"


def _code_interpreter_allowed(state) -> bool:
    """True if this user may run code via the interpreter."""
    return _CODE_INTERPRETER_ALLOW_ALL or _has_dev_role(state)


# Browser Use (fetch_from_portal) availability. Default Developer+ only — portal automation
# logs into external sites with stored creds — flip BROWSER_USE_ALLOW_ALL_USERS=true to open up.
_PORTAL_FETCH_ENABLED = os.getenv("BROWSER_USE_ENABLED", "true").lower() == "true"
_PORTAL_FETCH_ALLOW_ALL = os.getenv("BROWSER_USE_ALLOW_ALL_USERS", "false").lower() == "true"


def _portal_fetch_allowed(state) -> bool:
    """True if this user may pull files from web portals via the Browser Use service."""
    return _PORTAL_FETCH_ALLOW_ALL or _has_dev_role(state)


# Self-scheduling (schedule_task) availability. Default Developer+ only — a scheduled task
# re-runs the agent unattended; flip CC_SCHEDULE_ALLOW_ALL_USERS=true to open to all users.
_SCHEDULE_ENABLED = os.getenv("CC_SCHEDULE_ENABLED", "true").lower() == "true"
_SCHEDULE_ALLOW_ALL = os.getenv("CC_SCHEDULE_ALLOW_ALL_USERS", "false").lower() == "true"


def _schedule_allowed(state) -> bool:
    """True if this user may schedule recurring Command Center tasks."""
    return _SCHEDULE_ALLOW_ALL or _has_dev_role(state)


def _resolve_schedule_tz(is_cron, spoken, iana_hint, user_context):
    """Resolve the timezone a scheduled CRON time should fire in. Returns (canonical, note):
      canonical - a valid IANA name or 'UTC+HH:MM' offset to store on the schedule, or None to
                  leave it at the engine default (UTC).
      note      - a human string to surface to the user (ambiguity to confirm / fallback used), or ''.

    Only CRON schedules have a wall-clock time, so interval cadences return (None, ''). Priority:
    an explicitly named zone (`spoken`/`iana_hint`) wins; otherwise the user's browser timezone
    (user_context['browser_timezone']) is the default. Never raises - if the resolver module is
    unavailable, returns (None, '') so scheduling still works (firing in UTC, the prior behavior)."""
    if not is_cron:
        return None, ""
    try:
        from schedule_tz import resolve_timezone
    except Exception:
        return None, ""
    browser_tz = (user_context or {}).get("browser_timezone") or ""
    if not (spoken or iana_hint or browser_tz):
        return None, ""
    try:
        canonical, _disp, note = resolve_timezone(spoken, iana_hint, browser_tz)
        return canonical, note
    except Exception:
        return None, ""


# SFTP / FTP file transfer (sftp_list_files / sftp_download / sftp_upload) availability.
# Default Developer+ only — these connect to external servers with user-supplied
# credentials and move files in/out — flip SFTP_ALLOW_ALL_USERS=true to open to all users.
_SFTP_ENABLED = os.getenv("SFTP_ENABLED", "true").lower() == "true"
_SFTP_ALLOW_ALL = os.getenv("SFTP_ALLOW_ALL_USERS", "false").lower() == "true"


def _sftp_allowed(state) -> bool:
    """True if this user may transfer files over SFTP/FTP."""
    return _SFTP_ALLOW_ALL or _has_dev_role(state)


# Automations (persisted AI-generated Python solutions) build/run tools.
# Default Developer+ only — building executable scheduled code is a platform
# mutation; flip CC_AUTOMATIONS_ALLOW_ALL_USERS=true to open to all users.
# The main app re-enforces the role at /automations/api/internal/manage, so
# this gate is UX, not the security boundary.
_AUTOMATIONS_TOOLS_ENABLED = os.getenv("CC_AUTOMATIONS_TOOLS_ENABLED", "true").lower() == "true"
_AUTOMATIONS_ALLOW_ALL = os.getenv("CC_AUTOMATIONS_ALLOW_ALL_USERS", "false").lower() == "true"
# When true (default), CC proactively proposes an aihub.checkpoint() gate
# before irreversible steps (uploads, deletes, sends) while writing automation
# code. Turn off to only add checkpoints when the user asks.
_AUTOMATIONS_PROPOSE_CHECKPOINTS = os.getenv("CC_AUTOMATIONS_PROPOSE_CHECKPOINTS", "true").lower() == "true"


def _automations_allowed(state) -> bool:
    """True if this user may build/run Automations."""
    return _AUTOMATIONS_ALLOW_ALL or _has_dev_role(state)


# ── Native A/B agent (CC_AGENT="native") ──────────────────────────────────
_NATIVE_WORKFLOW_ALLOW_ALL = os.getenv("CC_NATIVE_WORKFLOW_ALLOW_ALL_USERS", "false").lower() == "true"


def _native_impl(state) -> bool:
    """True when this turn runs the 'native' A/B agent implementation
    (set by chat.py from CC_AGENT or the request's agent_impl override).
    Absent/empty/unknown → classic, so every native seam is opt-in."""
    return (state.get("agent_impl") or "classic") == "native"


def _workflow_tools_allowed(state) -> bool:
    """True if this user may build visual workflows with the native tools.
    Mirrors the canvas gate (/save/workflow is min_role=2): Developer+."""
    return _NATIVE_WORKFLOW_ALLOW_ALL or _has_dev_role(state)


# ─── Helpers ──────────────────────────────────────────────────────────────

def _build_delegation_context(messages: list, session_resources: list = None, max_chars: int = 1500) -> str:
    """Build a brief context preamble from the CC conversation for delegation.

    Scans recent messages and session resources to provide context like:
    "Previous context: User created Agent 474 for edwdb sales data.
    User previously asked about total sales and got $2.5M."
    """
    parts = []

    # Include session resources context
    if session_resources:
        res_lines = [f"{r.get('type','').title()} '{r.get('name','')}' (ID:{r.get('id','')})"
                     for r in session_resources[:5]]
        parts.append(f"Resources created this session: {', '.join(res_lines)}")

    # Scan recent conversation for data-relevant context
    recent = messages[-10:] if len(messages) > 10 else messages
    for msg in recent:
        content = msg.content if hasattr(msg, 'content') else str(msg)
        msg_type = getattr(msg, 'type', getattr(msg, 'role', 'unknown'))
        # Only include AI responses that contain data results (not routing messages)
        if msg_type == 'ai' and any(signal in content.lower() for signal in
                                     ['$', 'total', 'sales', 'revenue', 'count', 'rows',
                                      'agent #', 'table', 'query']):
            # Truncate long responses
            snippet = content[:300].replace('\n', ' ').strip()
            if len(content) > 300:
                snippet += '...'
            parts.append(f"Prior result: {snippet}")

    if not parts:
        return ""

    context = "Previous context: " + " | ".join(parts)
    return context[:max_chars]


def _format_history_for_llm(messages: list, max_turns: int = 10, max_chars: int = 4000) -> str:
    """Render the most recent turns as a role-labeled transcript for LLM prompting.

    Unlike _build_delegation_context (which filters by keyword for agent coherence),
    this is a faithful transcript — used when we need the LLM to understand the
    user's intent and the flow of the conversation.
    """
    recent = messages[-max_turns:] if len(messages) > max_turns else messages
    lines = []
    total = 0
    for msg in recent:
        content = msg.content if hasattr(msg, 'content') else str(msg)
        role = getattr(msg, 'type', getattr(msg, 'role', 'unknown'))
        label = {'human': 'User', 'ai': 'Assistant', 'system': 'System'}.get(role, str(role).title())
        # Strip JSON rich-content wrappers if present so the LLM sees plain text
        stripped = content
        if isinstance(content, str) and content.lstrip().startswith('['):
            try:
                blocks = json.loads(content)
                if isinstance(blocks, list):
                    stripped = "\n".join(
                        b.get('content', '') for b in blocks
                        if isinstance(b, dict) and b.get('content')
                    ) or content
            except Exception:
                pass
        line = f"{label}: {stripped}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def _format_session_resources(resources) -> str:
    """Format session_resources for injection into LLM system prompts."""
    if not resources:
        return "No resources created in this session yet."
    lines = []
    for r in resources:
        rtype = (r.get("type") or "resource").title()
        rname = r.get("name", "Unknown")
        rid = r.get("id", "?")
        created = r.get("created_at", "this session")
        lines.append(f"- {rtype} '{rname}' (ID: {rid}) created at {created}")
    return "\n".join(lines)


def _preferences_block(state) -> str:
    """Return a formatted user preferences block for CC-level LLM prompts.

    Reads from state["user_memory"] (already-formatted string set by chat.py
    from get_preferences()). Returns empty string when no preferences exist
    so callers can safely f-string interpolate without conditionals.

    Inject this into any CC-level LLM prompt where user context should
    influence reasoning (agent picking, multi-step decomposition, analysis
    synthesis, etc.). Do NOT forward to child agents — their prompts stay
    prefs-free.
    """
    um = state.get("user_memory")
    if not um:
        return ""
    return (
        "\n\n## USER PREFERENCES & CONTEXT\n"
        f"{um}\n"
        "Respect these when making decisions on the user's behalf.\n"
    )


_REROUTE_EXTRACTION_PROMPT = """You are a routing assistant. The user is mid-conversation with an AI
agent and their LATEST message is an instruction telling the orchestrator
to route to a DIFFERENT agent. That routing instruction is NOT itself
the question to answer — your job is to identify WHICH prior user
question the new agent should now answer.

CORE RULE — match by topic:
The routing instruction usually mentions the TOPIC of the question the
user wants rerouted (e.g. "get INVENTORY from the X agent instead"
refers to the prior inventory question, not an unrelated sales question).
Pick the prior user question whose topic best matches the routing
instruction's keywords.

Tie-breakers (only when topic match is ambiguous):
1. The most recent user question the agent failed or gave an unsatisfying
   answer to.
2. The most recent user question overall.

Special cases:
- If the routing instruction itself carries the full question (e.g.
  "ask the Inventory agent how many widgets are in stock"), return that
  question with the routing phrase stripped.
- If no prior user questions exist and the routing instruction carries no
  question, return nothing.

WORKED EXAMPLE:
Routing instruction: "get inventory from the Inventory data assistant instead"
Transcript:
  User: show me sales by month for last year
  Assistant: [sales data]
  User: now show me the top 10 items in terms of on hand inventory
  Assistant: [inventory data]
Correct answer: "show me the top 10 items in terms of on hand inventory"
(Reason: the word "inventory" in the routing instruction matches the
second question's topic, not the sales question.)

OUTPUT FORMAT:
Return ONLY the question text — one line, no preamble, no quotes, no
commentary. Do not prefix with "Original question:" or similar labels.

---
Routing instruction: "{reroute_message}"

Conversation transcript (chronological, most recent turn last):
{transcript}

Question the new agent should answer:"""


# Per-entry cap when rendering conversation history for prompts. Assistant
# responses in this system are often multi-KB JSON blocks (tables, etc.),
# and without a per-entry cap one giant response can eat the entire char
# budget and drop the actual user questions we need to see.
_TRANSCRIPT_PER_ENTRY_CAP = 400


def _strip_rich_blocks(content) -> str:
    """Flatten a rich-content block list into plain text. Most assistant
    messages in this service are JSON arrays like [{"type":"text","content":...},
    {"type":"table",...}, ...]. We want just the human-readable parts."""
    if not isinstance(content, str):
        return str(content or "")
    stripped = content.strip()
    if not (stripped.startswith("[") or stripped.startswith("{")):
        return content
    try:
        parsed = json.loads(stripped)
    except Exception:
        return content
    if isinstance(parsed, list):
        parts = []
        for b in parsed:
            if not isinstance(b, dict):
                continue
            bc = b.get("content")
            if isinstance(bc, str) and bc.strip():
                parts.append(bc.strip())
            elif isinstance(bc, list):
                # e.g. list content under the "list" block type
                parts.append("; ".join(str(x) for x in bc if x))
        return " | ".join(parts) if parts else content
    if isinstance(parsed, dict):
        bc = parsed.get("content")
        if isinstance(bc, str):
            return bc
    return content


def _format_delegation_history(history: list, max_turns: int = 12, max_chars: int = 3000,
                               per_entry_cap: int = _TRANSCRIPT_PER_ENTRY_CAP) -> str:
    """Render a delegation history list (list of {role, content} dicts) as a
    role-labeled transcript for LLM prompting. Tail-biased so the most recent
    turns always survive truncation. Each entry's content is capped so one
    huge assistant response cannot evict user questions from the transcript."""
    if not history:
        return ""
    recent = history[-max_turns:] if len(history) > max_turns else list(history)
    lines = []
    total = 0
    # Walk in reverse so we keep the freshest turns if we hit the char cap,
    # then flip back to chronological for the final transcript.
    for entry in reversed(recent):
        role = (entry.get("role") or "?").lower()
        label = {"user": "User", "assistant": "Assistant", "ai": "Assistant", "system": "System"}.get(
            role, role.title() or "Unknown"
        )
        content = _strip_rich_blocks(entry.get("content") or "").strip()
        if not content:
            continue
        if len(content) > per_entry_cap:
            content = content[:per_entry_cap].rstrip() + "…"
        line = f"{label}: {content}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    lines.reverse()
    return "\n".join(lines)


def _format_messages_tail_biased(messages: list, max_turns: int = 12, max_chars: int = 3000,
                                 per_entry_cap: int = _TRANSCRIPT_PER_ENTRY_CAP) -> str:
    """Render a LangGraph-style messages list as a role-labeled transcript,
    tail-biased (unlike _format_history_for_llm which is head-biased). Use
    this when the most recent turns matter most (e.g. reroute extraction).
    Each entry is per-capped so one huge response cannot evict the turns
    that follow it."""
    if not messages:
        return ""
    recent = messages[-max_turns:] if len(messages) > max_turns else list(messages)
    lines = []
    total = 0
    for msg in reversed(recent):
        content = msg.content if hasattr(msg, "content") else str(msg)
        role = getattr(msg, "type", getattr(msg, "role", "unknown"))
        label = {"human": "User", "ai": "Assistant", "system": "System",
                 "user": "User", "assistant": "Assistant"}.get(role, str(role).title())
        content = _strip_rich_blocks(content).strip()
        if not content:
            continue
        if len(content) > per_entry_cap:
            content = content[:per_entry_cap].rstrip() + "…"
        line = f"{label}: {content}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    lines.reverse()
    return "\n".join(lines)


def _format_conversation_for_prompt(
    messages: list,
    max_turns: int = 5,
    user_cap: int = 500,
    assistant_cap: int = 200,
    max_chars: int = 2500,
    exclude_latest: bool = True,
) -> str:
    """Render recent conversation for context in CC-level routing/reasoning prompts.

    Returns a compact transcript suitable for dropping into any prompt that
    needs to disambiguate a terse latest-utterance ("use the other agent
    instead", "yes", "now get inventory") by referencing prior turns.

    Design rules (matching the user's guidance):
      - Full user questions (they're short and carry the intent)
      - Trimmed assistant responses (they're often multi-KB JSON tables)
      - Strips rich-content JSON arrays down to the human-readable parts
      - Tail-biased — the MOST RECENT turns always survive truncation
      - `exclude_latest=True` by default because the caller usually also
        includes the latest user text as the primary focus of the prompt

    Returns the empty string when there is no usable context (caller can
    skip injection entirely).
    """
    if not messages:
        return ""
    working = list(messages)
    if exclude_latest and working:
        working = working[:-1]
    if not working:
        return ""
    recent = working[-max_turns * 2:] if len(working) > max_turns * 2 else working

    lines = []
    total = 0
    for msg in reversed(recent):
        content = msg.content if hasattr(msg, "content") else str(msg)
        role = getattr(msg, "type", getattr(msg, "role", "unknown"))
        is_user = role in ("human", "user")
        label = "User" if is_user else ("Assistant" if role in ("ai", "assistant") else str(role).title())

        content = _strip_rich_blocks(content).strip()
        if not content:
            continue
        cap = user_cap if is_user else assistant_cap
        if len(content) > cap:
            content = content[:cap].rstrip() + "…"
        line = f"{label}: {content}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)

    if not lines:
        return ""
    lines.reverse()
    return "\n".join(lines)


async def _extract_reroute_question(
    reroute_message: str,
    delegation_history: list,
    recent_messages: list,
    state=None,
    timeout: float = 2.0,
) -> str:
    """Use a mini-LLM to identify which prior question the user wants rerouted.

    When a user says "use agent X instead" during an active delegation, the
    intended question can be anywhere in the conversation — most often the
    one that just failed, but sometimes an older one, or implicit in the
    reroute message itself. A single mini-LLM call reads the transcript and
    returns the question text.

    Returns the extracted question on success, or empty string on
    timeout/failure. When empty, the caller should fall through to normal
    intent classification rather than guessing with a hardcoded heuristic.
    """
    try:
        from cc_config import get_llm

        # Build ONE unified transcript. Splitting into "delegation" and
        # "conversation" sections (as an earlier version did) biased the LLM
        # toward whichever section came first, even when the needed context
        # only lived in the other. A single tail-biased transcript with
        # per-entry caps keeps the most recent turns intact while giving the
        # LLM a clean linear view of the conversation.
        #
        # Prefer the broader messages list when it covers the delegation
        # history; otherwise fall back to the delegation history directly.
        transcript = _format_messages_tail_biased(
            recent_messages or [], max_turns=14, max_chars=3500
        )
        if not transcript:
            transcript = _format_delegation_history(
                delegation_history or [], max_turns=14, max_chars=3500
            )
        if not transcript:
            transcript = "(no prior conversation)"

        prompt = _REROUTE_EXTRACTION_PROMPT.format(
            reroute_message=reroute_message[:400],
            transcript=transcript,
        )

        llm = get_llm(mini=True, streaming=False)
        _t0 = _trace_time.perf_counter()
        resp = await asyncio.wait_for(llm.ainvoke(prompt), timeout=timeout)
        if state is not None:
            try:
                trace_llm_call(
                    state, node="classify_intent", step="reroute_question_extraction",
                    messages=[HumanMessage(content=prompt)], response=resp,
                    elapsed_ms=int((_trace_time.perf_counter() - _t0) * 1000),
                    model_hint="mini",
                )
            except Exception:
                pass

        result = (resp.content if hasattr(resp, "content") else str(resp)).strip()
        # Strip common wrappers the LLM sometimes adds
        result = result.strip('"').strip("'").strip()
        # Some models prefix with labels like "Original question:" — strip one such prefix
        for prefix in ("Original question:", "Question:", "The question is:"):
            if result.lower().startswith(prefix.lower()):
                result = result[len(prefix):].strip().strip('"').strip("'")
                break

        # Guard: empty response or LLM parroted the reroute instruction back
        if not result:
            return ""
        if result.lower() == reroute_message.strip().lower():
            return ""

        return result[:500]

    except asyncio.TimeoutError:
        logger.warning("[classify_intent] Reroute question extraction timed out")
        return ""
    except Exception as e:
        logger.warning(f"[classify_intent] Reroute question extraction failed: {e}")
        return ""


# ─── Mini-LLM helpers: capability router & export intent detector ────────

_CAPABILITY_ROUTER_PROMPT = """Classify the user's message into ONE of these Command Center capabilities, or "none" if none fits cleanly.

Capabilities:
- document_search: finding documents, contracts, invoices, leases, policies, reports, records in the document repository (not database rows)
- web_search: current news, weather, stock prices, real-time info from the internet
- map: geographic visualization, choropleth, showing locations on a map
- image_generation: generating/drawing images, illustrations, pictures
- run_tool: running a specific custom/generated tool — tool names that may be mentioned: {tool_names}
- portal: a BROWSER / web-automation task on an external website or portal — logging in to UPLOAD or DOWNLOAD a file (RPA), running a SAVED portal workflow, SAVING a portal login for reuse, or SCHEDULING a recurring portal login-and-upload/download. ONE task even when the user spells out the steps (open the site, sign in, upload/download, verify).
- build: creating, configuring, or modifying PLATFORM resources — agents, the platform's own Workflows (Workflow Designer), connections, custom tools. NOTE: a portal login-and-upload/download (even "save it and schedule it daily") is NOT a platform workflow — that is "portal".
- none: does NOT cleanly match any of the above — includes database queries, delegations to data/general agents, multi-step requests, ambiguous requests, and ordinary chat

{recent_conversation}User message: "{user_text}"

Reply with ONLY a JSON object, no other text:
{{"capability": "<one of the above>", "confidence": <float 0.0-1.0>}}

Rules:
- Use confidence >= 0.7 ONLY when you are clearly sure this maps to a single CC capability.
- For multi-step requests (e.g. "find the contract AND export it to excel"), use "none" — let the full classifier handle them.
- BUT a portal / browser-automation task (log in to a website/portal and upload or download a file, an "Upload Bay", a saved portal workflow) is ONE capability — classify it as "portal" even when the user lists the individual steps; do NOT treat it as a multi-step "none".
- A terse follow-up to a PORTAL interaction in the recent conversation — "yes save it", "save it and run it every day at 10am", "schedule it daily", "run it every morning" — is "portal" (saving/scheduling a portal login is done in chat via save_portal / schedule_portal_workflow), NOT "build".
- For ambiguous requests ("show me sales" could be a database query or a dashboard), use "none".
- Database/data-agent queries (sales, revenue, orders, headcount, inventory metrics) → "none"."""


def _parse_capability_router_response(resp) -> dict:
    """Parse the router response into a normalised dict.

    Always returns a dict with capability, confidence, intent. Unknown /
    malformed responses become capability='none' so the caller falls
    through to the main classifier.
    """
    import re as _re

    text = resp.content if hasattr(resp, "content") else str(resp)
    text = (text or "").strip()

    # Strip common code-fence wrappers
    text = text.replace("```json", "").replace("```", "").strip()

    parsed = None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        # Try to pull a JSON object out of the middle of the response
        m = _re.search(r"\{.*\}", text, _re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group(0))
            except (json.JSONDecodeError, TypeError):
                parsed = None

    if not isinstance(parsed, dict):
        return {"capability": "none", "confidence": 0.0, "intent": None}

    cap = str(parsed.get("capability", "none")).strip().lower()
    try:
        conf = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0

    cc_native_chat = {"document_search", "web_search", "map", "image_generation", "run_tool", "portal"}
    if cap == "build":
        intent = "build"
    elif cap in cc_native_chat:
        intent = "chat"
    else:
        cap = "none"
        intent = None

    return {"capability": cap, "confidence": max(0.0, min(1.0, conf)), "intent": intent}


async def _run_capability_router(
    user_text: str,
    state=None,
    *,
    confidence_threshold: float = 0.7,
    timeout: float = 2.5,
) -> dict:
    """Mini-LLM shortcut: map the user's message to a CC-native capability.

    Replaces the USE_INTENT_HEURISTICS keyword shortcuts with a semantic
    classifier that scales as new tools appear in the platform landscape.

    Policy: try mini; on failure retry with full LLM once; on second failure
    return the conservative 'none' default so the caller falls through to
    the main intent classifier.

    Returns: {"capability": str, "confidence": float, "intent": "chat"|"build"|None}
    Callers should shortcut only when intent is not None AND confidence meets
    their threshold (default 0.7).
    """
    # Gather available tool names so the router can recognise "run_tool"
    # requests that name a specific tool. Keep the list short — we only
    # need recognisable names, not full descriptions.
    tool_names = ""
    try:
        from command_center.tools.tool_factory import list_generated_tools
        _gt = list_generated_tools() or []
        names = [str(t.get("name", "")).replace("_", " ") for t in _gt if t.get("name")]
        tool_names = ", ".join(names[:25]) if names else "(no custom tools)"
    except Exception:
        tool_names = "(tool list unavailable)"

    # Honour DOCUMENT_SEARCH_ENABLED by adjusting the prompt — if disabled,
    # document_search should never be picked.
    try:
        from cc_config import DOCUMENT_SEARCH_ENABLED as _DSE
    except Exception:
        _DSE = True
    # Recent conversation so a terse follow-up ("save it and run it daily") resolves to the right
    # capability — e.g. saving/scheduling the PORTAL the prior turn just ran, not a Builder workflow.
    _conv = ""
    try:
        _msgs = state.get("messages", []) if isinstance(state, dict) else []
        _ctext = _format_conversation_for_prompt(_msgs)
        if _ctext:
            _conv = f"Recent conversation (resolve 'it'/'that' + terse follow-ups against this):\n{_ctext}\n\n"
    except Exception:
        _conv = ""
    prompt_body = _CAPABILITY_ROUTER_PROMPT.format(
        tool_names=tool_names,
        user_text=(user_text or "").replace('"', '\\"')[:800],
        recent_conversation=_conv,
    )
    if not _DSE:
        prompt_body = prompt_body.replace(
            "- document_search: finding documents, contracts, invoices, leases, policies, reports, records in the document repository (not database rows)\n",
            "",
        )
    if not _PORTAL_FETCH_ENABLED:
        prompt_body = prompt_body.replace(
            "- portal: a BROWSER / web-automation task on an external website or portal — logging in to UPLOAD or DOWNLOAD a file (RPA), running a SAVED portal workflow, SAVING a portal login for reuse, or SCHEDULING a recurring portal login-and-upload/download. ONE task even when the user spells out the steps (open the site, sign in, upload/download, verify).\n",
            "",
        )

    msgs = [HumanMessage(content=prompt_body)]

    # Attempt 1: mini
    try:
        from cc_config import get_step_llm
        llm = get_step_llm("capability_router")
        _t0 = _trace_time.perf_counter()
        resp = await asyncio.wait_for(llm.ainvoke(msgs), timeout=timeout)
        if state is not None:
            try:
                trace_llm_call(
                    state, node="classify_intent", step="capability_router",
                    messages=msgs, response=resp,
                    elapsed_ms=int((_trace_time.perf_counter() - _t0) * 1000),
                    model_hint="mini",
                )
            except Exception:
                pass
        result = _parse_capability_router_response(resp)
        result["confidence_threshold"] = confidence_threshold
        return result
    except Exception as mini_err:
        logger.warning(
            f"[capability_router] mini-LLM failed ({type(mini_err).__name__}: {mini_err}); "
            f"retrying with full model"
        )

    # Attempt 2: full
    try:
        from cc_config import get_llm
        llm = get_llm(mini=False, streaming=False)
        _t0 = _trace_time.perf_counter()
        resp = await asyncio.wait_for(llm.ainvoke(msgs), timeout=timeout * 2)
        if state is not None:
            try:
                trace_llm_call(
                    state, node="classify_intent", step="capability_router",
                    messages=msgs, response=resp,
                    elapsed_ms=int((_trace_time.perf_counter() - _t0) * 1000),
                    model_hint="full",
                )
            except Exception:
                pass
        result = _parse_capability_router_response(resp)
        result["confidence_threshold"] = confidence_threshold
        return result
    except Exception as full_err:
        logger.warning(
            f"[capability_router] full-LLM retry also failed "
            f"({type(full_err).__name__}: {full_err}); falling through to main classifier"
        )

    # Conservative default: no shortcut — let the main classifier decide
    return {"capability": "none", "confidence": 0.0, "intent": None,
            "confidence_threshold": confidence_threshold}


_EXPORT_INTENT_PROMPT = """Is the user's latest message asking to EXPORT or DOWNLOAD prior data/results as a file? If yes, which format?

User message: "{user_text}"

Reply with ONLY a JSON object, no other text:
{{"is_export": <true|false>, "format": "<excel|csv|pdf|json|text>" or null, "confidence": <float 0.0-1.0>}}

Rules:
- is_export=true only when the user clearly wants the previous result saved/downloaded as a file.
- If is_export is true, format MUST be one of excel, csv, pdf, json, text.
- Default format when the user says "export" / "save" / "download" without naming a format: "excel".
- Mentioning a format in passing ("a PDF explanation would help") does NOT count as an export request.
- Multi-step requests that include an export still count ("search lease docs and export them" → is_export=true, format=excel).

Examples:
- "export to excel" → {{"is_export": true, "format": "excel", "confidence": 0.98}}
- "give me a CSV" → {{"is_export": true, "format": "csv", "confidence": 0.95}}
- "send me as a spreadsheet" → {{"is_export": true, "format": "excel", "confidence": 0.9}}
- "download that" → {{"is_export": true, "format": "excel", "confidence": 0.8}}
- "save as pdf" → {{"is_export": true, "format": "pdf", "confidence": 0.95}}
- "what is revenue this month" → {{"is_export": false, "format": null, "confidence": 1.0}}
- "PDF format is fine by me" → {{"is_export": false, "format": null, "confidence": 0.85}}"""


def _parse_export_intent_response(resp) -> tuple[bool, str, float]:
    """Parse the export-intent response into (is_export, format, confidence).

    Unknown / malformed responses return (False, "excel", 0.0).
    """
    import re as _re

    text = resp.content if hasattr(resp, "content") else str(resp)
    text = (text or "").strip().replace("```json", "").replace("```", "").strip()

    parsed = None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        m = _re.search(r"\{.*\}", text, _re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group(0))
            except (json.JSONDecodeError, TypeError):
                parsed = None

    if not isinstance(parsed, dict):
        return (False, "excel", 0.0)

    is_export = bool(parsed.get("is_export", False))
    fmt = parsed.get("format")
    fmt = str(fmt).lower().strip() if fmt else "excel"
    if fmt not in ("excel", "csv", "pdf", "json", "text"):
        fmt = "excel"
    try:
        conf = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    return (is_export, fmt, max(0.0, min(1.0, conf)))


async def _detect_export_intent(
    user_text: str,
    trace_state=None,
    *,
    confidence_threshold: float = 0.6,
    timeout: float = 2.0,
) -> tuple[bool, str]:
    """Mini-LLM classifier: is this an export request? Which format?

    Replaces the keyword-based export_signals + cascading 'if "csv" in
    ut_lower' detection. Handles natural language variations
    ("spreadsheet", "send me a file") while correctly ignoring passing
    mentions of formats ("a PDF would be nice for this discussion").

    Policy: mini → full LLM on failure → conservative default
    (False, "excel"). A user whose export intent goes undetected will
    simply get the non-export path, which is the correct fallback.

    Returns: (is_export, format) where format ∈ {excel, csv, pdf, json, text}.
    """
    prompt = _EXPORT_INTENT_PROMPT.format(
        user_text=(user_text or "").replace('"', '\\"')[:600],
    )
    msgs = [HumanMessage(content=prompt)]

    # Attempt 1: mini
    try:
        from cc_config import get_step_llm
        llm = get_step_llm("export_intent_detector")
        _t0 = _trace_time.perf_counter()
        resp = await asyncio.wait_for(llm.ainvoke(msgs), timeout=timeout)
        if trace_state is not None:
            try:
                trace_llm_call(
                    trace_state, node="gather_data", step="export_intent_detector",
                    messages=msgs, response=resp,
                    elapsed_ms=int((_trace_time.perf_counter() - _t0) * 1000),
                    model_hint="mini",
                )
            except Exception:
                pass
        is_export, fmt, conf = _parse_export_intent_response(resp)
        if conf < confidence_threshold:
            is_export = False
        return (is_export, fmt)
    except Exception as mini_err:
        logger.warning(
            f"[export_intent_detector] mini-LLM failed "
            f"({type(mini_err).__name__}: {mini_err}); retrying with full model"
        )

    # Attempt 2: full
    try:
        from cc_config import get_llm
        llm = get_llm(mini=False, streaming=False)
        _t0 = _trace_time.perf_counter()
        resp = await asyncio.wait_for(llm.ainvoke(msgs), timeout=timeout * 2)
        if trace_state is not None:
            try:
                trace_llm_call(
                    trace_state, node="gather_data", step="export_intent_detector",
                    messages=msgs, response=resp,
                    elapsed_ms=int((_trace_time.perf_counter() - _t0) * 1000),
                    model_hint="full",
                )
            except Exception:
                pass
        is_export, fmt, conf = _parse_export_intent_response(resp)
        if conf < confidence_threshold:
            is_export = False
        return (is_export, fmt)
    except Exception as full_err:
        logger.warning(
            f"[export_intent_detector] full-LLM retry also failed "
            f"({type(full_err).__name__}: {full_err}); defaulting to non-export"
        )

    # Conservative default: not an export. If the user did mean to export,
    # they will say so again more explicitly and the next attempt picks it up.
    return (False, "excel")


# ─── Deterministic build-request guard ────────────────────────────────────
# AIHUB-0015 F1 / AIHUB-0021 F1: the LLM classifier nondeterministically sent
# explicit build requests to multi_step, where generic chat agents role-played
# the build and CC reported fabricated success. Platform mutations must always
# reach the Builder, so this guard is deterministic and runs before any LLM
# routing layer gets a vote.

# The mutation verb must take the platform resource as its OBJECT (verb,
# then at most 3 non-preposition words, then the noun). Bare co-occurrence
# ("ask the AIRDB agent to delete the duplicate rows") hijacked legitimate
# data-agent traffic — the object-proximity requirement plus the noun-context
# exclusions ("the agents table" is data, not a platform agent) keep the
# guard on genuine build requests.
_BUILD_GUARD_REQUEST_RE = re.compile(
    r"\b(?:creat(?:e|es|ing)|build(?:s|ing)?|register(?:s|ing)?|set\s+up|setup|"
    r"configur(?:e|es|ing)|provision(?:s|ing)?|deploy(?:s|ing)?|"
    r"delet(?:e|es|ing)|remov(?:e|es|ing)|renam(?:e|es|ing)|"
    r"modif(?:y|ies|ying)|updat(?:e|es|ing)(?!\s+me\b)|enabl(?:e|es|ing)|disabl(?:e|es|ing))\s+"
    r"(?:(?!(?:of|for|with|from|about|in|on|by|to|at|and)\b)[\w'\"-]+\s+){0,3}?"
    r"(?:connections?|data\s+agents?|"
    r"agents?(?!\s+(?:table|tables|data|records|rows|list|activity|performance|report|numbers|sales))|"
    r"workflows?|mcp\s+servers?|email\s+triggers?|inbound\s+email)\b",
    re.I,
)
# How-to / informational phrasings are questions about building, not requests
# to build — let the LLM classifier handle those.
_BUILD_GUARD_QUESTION_RE = re.compile(
    r"^\s*(?:how|what|what's|whats|why|when|where|who|which|explain|describe|"
    r"tell\s+me\s+about|do(?:es)?\s|is\s|are\s|should\s)",
    re.I,
)
# "create an image/map/chart of ..." are CC-native converse tools, not builds.
_BUILD_GUARD_MEDIA_RE = re.compile(
    r"\b(?:creat(?:e|es)|mak(?:e|es)|generat(?:e|es)|draw|build)\s+(?:me\s+)?"
    r"(?:an?\s+|the\s+)?(?:image|picture|illustration|photo|map|chart|graph|plot|"
    r"dashboard|visuali[sz]ation)\b",
    re.I,
)
# AUTOMATIONS are converse-tool territory (create/save/dry-run/promote/run via
# the automation tools), NOT Builder Agent territory — the Builder doesn't know
# the asset type and role-plays it (AIHUB-0028 F1: a save-code request routed
# to the Builder and returned no save confirmation). A message that mentions
# automations therefore never takes the deterministic build route; converse can
# still explicitly delegate genuine agent/workflow work via its builder tool.
_BUILD_GUARD_AUTOMATION_RE = re.compile(r"\bautomations?\b", re.I)


def _is_explicit_build_request(text: str) -> bool:
    """Deterministically detect a platform build/mutation request.

    True when the message pairs a mutation verb with a platform-resource noun
    ("create a connection", "build a data agent and a workflow"), unless the
    message is phrased as a how-to question or targets a media artifact
    (image/map/chart) that CC-native converse tools handle. Fail-open toward
    the Builder: its clarify→plan→confirm flow recovers a false positive
    cheaply, while a missed build risks a role-played fabricated success.
    """
    if not text:
        return False
    if _BUILD_GUARD_QUESTION_RE.match(text):
        return False
    if _BUILD_GUARD_MEDIA_RE.search(text):
        return False
    if _BUILD_GUARD_AUTOMATION_RE.search(text):
        return False
    if _BUILD_GUARD_REQUEST_RE.search(text):
        return True
    # Assigning a tool to an agent is a builder operation even without a
    # classic mutation verb ("assign the converter tool to my sales agent").
    tl = text.lower()
    return "assign" in tl and "tool" in tl


# ── Smart build routing (replaces the fragile keyword guards) ──────────────
# CC_SMART_BUILD_ROUTING (default ON): instead of regex keyword guards, a
# STRONGER-model call decides the SHAPE of a build/create request —
# automation / builder / both / neither — reasoning holistically about the
# user's goal. Flag-guarded because this is core routing for ALL CC traffic:
# a bad decision is an instant env revert to the old keyword guards, not a
# redeploy. The cheap intent classifier gates it (only build-family intents
# pay for the strong call — the intelligence-based triage).
_SMART_BUILD_ROUTING = os.getenv("CC_SMART_BUILD_ROUTING", "true").lower() == "true"
_BUILD_FAMILY_INTENTS = {"build", "multi_step", "create_tool"}

_BUILD_SHAPE_PROMPT = (
    "You decide HOW to fulfill a build/create/set-up request in the AI Hub platform. "
    "Reply with ONE word only: automation, builder, both, or neither.\n\n"
    "- automation — a Python SCRIPT the platform generates and OWNS: it runs in its own "
    "dedicated Python environment where ANY pip library can be installed (pdfplumber, "
    "paramiko, pandas, ML packages, vendor API SDKs, …), and the platform versions, runs, "
    "VERIFIES its declared outputs, and schedules it. It USES the tenant's EXISTING AI Hub "
    "connections and secrets by name (no new connection/integration setup needed — the code "
    "calls aihub.connection('NAME') / aihub.secret('NAME')), and it has a BUILT-IN DRY-RUN that "
    "executes against sample data and shows verified results before anything goes live. Choose "
    "this when the goal is a PROCESS: read/parse files (PDF/Excel/CSV), call APIs, transform or "
    "reconcile data, produce and move files (CSV to SFTP/FTP/HTTP), on demand or on a "
    "schedule/trigger — i.e. when writing code with libraries is the path of least resistance "
    "versus assembling a many-step visual workflow. STRONG automation signals: the request "
    "mentions parsing PDFs/Excel, needs a Python library, asks to 'dry-run' or 'verify' outputs, "
    "or moves files to SFTP/FTP using an existing secret. Needing a database CONNECTION or a "
    "SECRET is NOT a reason to pick builder — automations resolve EXISTING ones by name. A "
    "MULTI-STEP code process (distinct stages, fail-branches to an alert step, files passed "
    "between steps) is STILL 'automation' — the family includes multi-step Code Flows; only pick "
    "'builder' for a workflow when the user explicitly wants an EDITABLE VISUAL workflow to click "
    "through, not code that just runs.\n"
    "- builder — creates platform OBJECTS that people interact with or other resources reference: "
    "a data/general AGENT someone chats with, a CONNECTION, an MCP server, a custom TOOL an agent "
    "calls, a KNOWLEDGE base/document set, or a visual WORKFLOW the user explicitly wants to see and edit.\n"
    "- both — the goal genuinely needs an OBJECT and a PROCESS, e.g. 'create an agent and load this "
    "document into its knowledge, and also a nightly export that feeds it'.\n"
    "- neither — not actually a build/create request (a question, a data lookup, or chit-chat).\n\n"
    "Decide by what the DELIVERABLE is, not by what is technically possible (a script can do almost "
    "anything). If someone will TALK TO it, or other resources will REFERENCE it, or they asked for an "
    "editable visual workflow → builder. If it is a script that runs and produces an outcome → automation. "
    "Examples: 'nightly, read expense PDFs, look up employees in the database, make a reconciled CSV, "
    "upload via SFTP, alert if any step fails' → automation (a multi-step code process — a Code Flow); "
    "'reconcile invoices against ERPDB and email a report every morning' → automation; "
    "'create a data agent for the sales team' → builder; "
    "'build me a workflow I can see and edit on the canvas' → builder. "
    "Reply with exactly one word."
)


# Native-agent (CC_AGENT="native") variant of the build-shape prompt: adds a
# fifth label so the ONE intelligent build-shape call also decides
# native-vs-builder for visual workflows (james's directive: model intelligence
# over keyword regexes — build_routing.looks_like_visual_workflow_build is
# demoted to a fail-open fast-path). Classic turns NEVER see this prompt: the
# A/B guarantee keeps their classifier prompt and parse byte-identical.
_BUILD_SHAPE_PROMPT_NATIVE = (
    "You decide HOW to fulfill a build/create/set-up request in the AI Hub platform. "
    "Reply with ONE word only: automation, builder, visual_workflow, both, or neither.\n\n"
    "- automation — a Python SCRIPT the platform generates and OWNS: it runs in its own "
    "dedicated Python environment where ANY pip library can be installed (pdfplumber, "
    "paramiko, pandas, ML packages, vendor API SDKs, …), and the platform versions, runs, "
    "VERIFIES its declared outputs, and schedules it. It USES the tenant's EXISTING AI Hub "
    "connections and secrets by name (no new connection/integration setup needed — the code "
    "calls aihub.connection('NAME') / aihub.secret('NAME')), and it has a BUILT-IN DRY-RUN that "
    "executes against sample data and shows verified results before anything goes live. Choose "
    "this when the goal is a PROCESS: read/parse files (PDF/Excel/CSV), call APIs, transform or "
    "reconcile data, produce and move files (CSV to SFTP/FTP/HTTP), on demand or on a "
    "schedule/trigger — i.e. when writing code with libraries is the path of least resistance "
    "versus assembling a many-step visual workflow. STRONG automation signals: the request "
    "mentions parsing PDFs/Excel, needs a Python library, asks to 'dry-run' or 'verify' outputs, "
    "or moves files to SFTP/FTP using an existing secret. Needing a database CONNECTION or a "
    "SECRET is NOT a reason to pick builder — automations resolve EXISTING ones by name. A "
    "MULTI-STEP code process (distinct stages, fail-branches to an alert step, files passed "
    "between steps) is STILL 'automation' — the family includes multi-step Code Flows; an "
    "EDITABLE VISUAL workflow the user clicks through on the canvas is 'visual_workflow', not "
    "code that just runs.\n"
    "- builder — creates platform OBJECTS that people interact with or other resources reference: "
    "a data/general AGENT someone chats with, a CONNECTION, an MCP server, a custom TOOL an agent "
    "calls, or a KNOWLEDGE base/document set. NOT a solo visual workflow — that is "
    "'visual_workflow' — but a workflow requested TOGETHER WITH any of these objects IS "
    "'builder', which can produce both.\n"
    "- visual_workflow — the deliverable is a VISUAL WORKFLOW itself and nothing else: an "
    "editable canvas of typed nodes (Database, Conditional, Loop, Alert, Excel Export, …) the "
    "user wants created, edited, wired, or run. The Command Center builds these natively with "
    "its own tools.\n"
    "- both — the goal genuinely needs an OBJECT and a PROCESS, e.g. 'create an agent and load this "
    "document into its knowledge, and also a nightly export that feeds it'.\n"
    "- neither — not actually a build/create request (a question, a data lookup, or chit-chat).\n\n"
    "Decide by what the DELIVERABLE is, not by what is technically possible (a script can do almost "
    "anything). If someone will TALK TO it, or other resources will REFERENCE it → builder. If it is "
    "a script that runs and produces an outcome → automation. If it is the editable canvas workflow "
    "itself → visual_workflow. "
    "Examples: 'nightly, read expense PDFs, look up employees in the database, make a reconciled CSV, "
    "upload via SFTP, alert if any step fails' → automation (a multi-step code process — a Code Flow); "
    "'reconcile invoices against ERPDB and email a report every morning' → automation; "
    "'create a data agent for the sales team' → builder; "
    "'build me a workflow I can see and edit on the canvas' → visual_workflow; "
    "'build a workflow that queries AIRDB and emails the result' → visual_workflow; "
    "'create a data agent and a workflow that calls it' → builder. "
    "Reply with exactly one word."
)


async def _classify_build_shape(user_text: str, state: "CommandCenterState") -> str:
    """Strong-model decision: given a build-ish request, what is the right home?
    Returns 'automation' | 'builder' | 'both' | 'neither' — plus, ONLY on
    native-impl turns, 'visual_workflow' (the native prompt/parse are gated on
    agent_impl so classic turns stay byte-identical). Never raises — any
    failure returns 'neither', so the caller safely keeps the cheap classifier's
    own intent."""
    # Deterministic high-precision fast-path (AIHUB-0033 F1): a clear code/data
    # PROCESS (parse files, DB lookup, reconcile, move files, on a schedule,
    # alert on failure) belongs to the automation family — converse owns the
    # automation AND code-flow tools. The LLM shape decision alone misrouted
    # exactly this case to the visual builder (which has no code-flow tools),
    # so pin the unambiguous cases here. Ambiguous requests still fall through
    # to the LLM below.
    try:
        from graph.build_routing import looks_like_code_process
        if looks_like_code_process(user_text):
            logger.info("[classify_intent] build-shape fast-path: code/data process -> automation")
            return "automation"
    except Exception as e:
        logger.debug(f"[classify_intent] code-process fast-path skipped: {e}")

    _native = _native_impl(state)
    _prompt = _BUILD_SHAPE_PROMPT_NATIVE if _native else _BUILD_SHAPE_PROMPT
    try:
        from cc_config import get_llm
        llm = get_llm(mini=False, streaming=False)
        _t0 = _trace_time.perf_counter()
        resp = await llm.ainvoke([
            SystemMessage(content=_prompt),
            HumanMessage(content=user_text),
        ])
        trace_llm_call(state, node="classify_intent", step="build_shape_decision",
                       messages=[SystemMessage(content=_prompt), HumanMessage(content=user_text)],
                       response=resp, elapsed_ms=int((_trace_time.perf_counter() - _t0) * 1000),
                       model_hint="full")
        raw = (resp.content or "").strip().strip('"').lower()
        if _native:
            # tolerate "visual workflow" (space) for the underscore label
            raw = raw.replace("visual workflow", "visual_workflow")
            first = raw.split()[0] if raw else ""
            if first in ("automation", "builder", "visual_workflow", "both", "neither"):
                return first
            for w in ("both", "visual_workflow", "automation", "builder", "neither"):
                if w in raw:
                    return w
            return "neither"
        first = raw.split()[0] if raw else ""
        if first in ("automation", "builder", "both", "neither"):
            return first
        for w in ("both", "automation", "builder", "neither"):
            if w in raw:
                return w
        return "neither"
    except Exception as e:
        logger.warning(f"[classify_intent] build-shape decision failed: {e}")
        return "neither"


async def _native_workflow_shape_divert(user_text: str, state: "CommandCenterState") -> bool:
    """Native A/B agent only: True when this build turn's deliverable is a
    visual workflow ITSELF — converse's native workflow tools own it, not the
    builder delegation. The decision-maker is the intelligent build-shape call
    (label 'visual_workflow'); the deterministic regex is ONLY a fast-path
    accelerator for unambiguous phrasings (same pattern as the code-process
    fast-path inside _classify_build_shape). Classic turns: always False with
    zero LLM cost — the A/B guarantee. Fail-open: any error or any other shape
    → False, and the builder path (which still works) handles the turn."""
    if not _native_impl(state):
        return False
    try:
        from graph.build_routing import looks_like_visual_workflow_build
        if looks_like_visual_workflow_build(user_text):
            logger.info("[classify_intent] native build-shape fast-path: "
                        "visual workflow → converse (native workflow tools)")
            return True
    except Exception as _fp_err:
        logger.debug(f"[classify_intent] native workflow fast-path skipped: {_fp_err}")
    try:
        shape = await _classify_build_shape(user_text, state)
        if shape == "visual_workflow":
            logger.info("[classify_intent] native build-shape: visual_workflow → "
                        "converse (native workflow tools)")
            return True
    except Exception as _nw_err:
        logger.debug(f"[classify_intent] native workflow-shape divert skipped: {_nw_err}")
    return False


def _authoring_marker(state: "CommandCenterState") -> dict:
    """AIHUB-0056 B: the visual_workflow continuity marker, stamped AT THE
    DIVERT — the moment a turn is classified as native visual-workflow
    authoring — not only after a workflow tool runs. Live failure: during a
    multi-turn clarification build no tool had run yet, so the user's ANSWER
    turns re-classified from scratch (their text alone has no build shape) and
    could wander to the builder; and the post-build "run it" found no marker.
    Preserves an existing visual_workflow marker (it may carry the name)."""
    _m = state.get("code_flow_context")
    if isinstance(_m, dict) and _m.get("kind") == "visual_workflow":
        return _m
    return {"name": "", "kind": "visual_workflow"}


async def _is_workflow_followup_llm(user_text: str, marker: dict,
                                    state: "CommandCenterState") -> bool:
    """Mini-LLM continuity check for an ongoing NATIVE visual-workflow authoring
    session (marker kind='visual_workflow'), consulted only when the
    deterministic follow-up cues MISS — keyword regexes are an accelerator
    here, never the decision-maker. YES → the turn stays in converse on the
    same workflow. Fail-open: NO, an ambiguous reply, or any error returns
    False and the turn falls through to normal classification."""
    try:
        from cc_config import get_step_llm
        llm = get_step_llm("workflow_continuity")
        wf_name = (marker or {}).get("name") or "the workflow being edited"
        _msgs = [HumanMessage(content=(
            f'The user is mid-conversation building/editing a visual workflow named '
            f'"{wf_name}" (typed nodes, edges, variables, runs). Is the following '
            f'message a follow-up action or question about THAT workflow — e.g. add/'
            f'change/remove/rename/configure a step, wire or unwire nodes, set the '
            f'start, add a variable, run/test it, or ask about its structure? Treat a '
            f'NEW unrelated request (a different resource, a general data question, '
            f'chit-chat) as NO.\n'
            f'Message: "{user_text}"\n'
            f'Reply with ONLY "YES" or "NO".'))]
        _t0 = _trace_time.perf_counter()
        resp = await llm.ainvoke(_msgs)
        trace_llm_call(state, node="classify_intent", step="workflow_continuity",
                       messages=_msgs, response=resp,
                       elapsed_ms=int((_trace_time.perf_counter() - _t0) * 1000),
                       model_hint="mini")
        return str(getattr(resp, "content", "")).strip().upper().startswith("YES")
    except Exception as e:
        logger.debug(f"[classify_intent] workflow-continuity mini-LLM skipped: {e}")
        return False


# ─── Node: classify_intent ────────────────────────────────────────────────

_MUTATION_CLAIM_RE = None


def _claims_completed_mutation(text: str) -> bool:
    """AIHUB-0048 F1: True when a reply asserts a JUST-COMPLETED build/edit
    mutation (the live fabrication: '✅ Inserted Set Variable node … Current
    persisted structure: […]' with zero tool calls). Tuned tight to
    first-person/checkmarked completion claims so recaps of earlier turns
    don't false-positive."""
    global _MUTATION_CLAIM_RE
    import re as _re
    if _MUTATION_CLAIM_RE is None:
        _MUTATION_CLAIM_RE = _re.compile(
            r"(current persisted structure)"
            r"|(✅\s*(inserted|added|removed|updated|saved|created|wired|rewired|deleted))"
            r"|(\bI(?:'|’)?ve\s+(?:now\s+)?(inserted|added|removed|updated|saved|created|"
            r"wired|rewired|deleted)\b[^.\n]{0,60}\b(node|edge|connection|workflow|step|variable)\b)",
            _re.I)
    return bool(_MUTATION_CLAIM_RE.search(text or ""))


class _ToolRepeatGuard:
    """AIHUB-0028 anti-repeat, made PROGRESS-AWARE for AIHUB-0048 F2.

    The original guard short-circuited any verbatim (tool, args) repeat in a
    turn — which also killed LEGITIMATE retries whose preconditions had been
    fixed by intervening calls (live: wire(Q→MID) failed on a competing edge,
    the agent unwired Q→A, then retried wire(Q→MID) and got the cached STOP —
    leaving the graph edge-less). Rule now: short-circuit ONLY when nothing
    else has executed since that exact call's last attempt (a true no-progress
    loop); if other calls ran in between, allow the retry and re-record it."""

    def __init__(self):
        self._results = {}
        self._seq = {}
        self._counter = 0

    @staticmethod
    def key(name, args):
        try:
            return name + "|" + json.dumps(args, sort_keys=True, default=str)
        except Exception:
            return name + "|" + str(args)

    def record(self, name, args, result):
        k = self.key(name, args)
        self._counter += 1
        self._results[k] = str(result)
        self._seq[k] = self._counter

    def cached_if_no_progress(self, name, args):
        """Return the cached result to short-circuit with, or None to allow."""
        k = self.key(name, args)
        if k not in self._results:
            return None
        if self._seq.get(k) == self._counter:
            return self._results[k]      # nothing ran since — a no-progress loop
        return None                      # world changed — legitimate retry


def _builder_in_flight(active) -> bool:
    """AIHUB-0043: True only for a builder delegation that is genuinely IN
    FLIGHT (mid-build / awaiting an answer). A completed/partial/failed build
    leaves active_delegation in state — treating that stale marker as 'builder
    active' disabled the code-flow routing guards for the REST of the session,
    so every terse follow-up misrouted to the visual Builder."""
    if str((active or {}).get("agent_type") or "").lower() != "builder":
        return False
    return str((active or {}).get("build_status") or "in_progress").lower() == "in_progress"


async def classify_intent(state: CommandCenterState) -> dict:
    """Classify the user's intent using a mini LLM call.
    
    If there's an active delegation (mid-conversation with an agent),
    route directly to continue that conversation unless the user
    explicitly wants to change topic.
    """
    from cc_config import (
        get_llm, INTENT_CLASSIFICATION_PROMPT, DOCUMENT_SEARCH_ENABLED,
        USE_INTENT_HEURISTICS, USE_CAPABILITY_ROUTER,
    )
    from command_center.orchestration.landscape_scanner import scan_platform, format_landscape_summary

    messages = state.get("messages", [])
    if not messages:
        return {"intent": "chat"}

    last_msg = messages[-1]
    user_text = last_msg.content if hasattr(last_msg, 'content') else str(last_msg)

    # If previous turn asked user to pick an agent, treat this reply as agent selection
    if state.get("pending_agent_selection"):
        logger.info("Pending agent selection — routing to gather_data to handle agent pick")
        return {"intent": "query", "pending_agent_selection": False}

    # ── Route Memory shortcut ──────────────────────────────────────────────
    # Before expensive LLM classification, check if we have a confident
    # historical route for this type of query.  Only try when no active
    # delegation (otherwise the delegation routing logic should decide).
    active = state.get("active_delegation")

    # ── Deterministic code/data-process shortcut (AIHUB-0033 F1-R2) ─────
    # A clear code/data PROCESS (parse files, DB look-up, reconcile, move
    # files, on a schedule, alert on failure) must reach the code-flow /
    # automation tools, which live in `converse`. Decide it HERE — ahead of
    # the capability_router (which confidently early-returns intent='build'
    # → the visual Workflow Builder, which has none of those tools, BEFORE
    # the build-shape decision ever runs). High precision: only clear
    # processes with no object-builder signal match; everything else falls
    # through unchanged. Exempt a builder session only while it is genuinely
    # IN FLIGHT (AIHUB-0043: a completed build's stale delegation must not
    # disable this shortcut for the rest of the session).
    if _AUTOMATIONS_TOOLS_ENABLED and not _builder_in_flight(active):
        try:
            from graph.build_routing import looks_like_code_process
            if looks_like_code_process(user_text):
                logger.info("[classify_intent] code-process shortcut → intent=chat "
                            "(converse owns the automation + code-flow tools)")
                _cp_out = {"intent": "chat", "pending_agent_selection": False}
                if active:
                    _cp_out["active_delegation"] = None
                return _cp_out
        except Exception as _cp_err:
            logger.debug(f"[classify_intent] code-process shortcut skipped: {_cp_err}")

    # ── Code-flow authoring continuity (AIHUB-0035) ─────────────────────
    # Once a code flow is being authored in this session (marker set by
    # converse when a code-flow tool ran), keep TERSE follow-ups — "now
    # dry-run it", "wire the fail edge", "schedule it", "continue" — in
    # converse (which owns the code-flow tools). Without this they re-classify
    # as a fresh 'build' and go to the visual Builder, which can't see the
    # flow. Object-build follow-ups don't match the follow-up cues, so they
    # still route to the Builder. Exempt only an IN-FLIGHT builder session
    # (AIHUB-0043 — same stale-delegation trap as the shortcut above).
    _continuity_marker = state.get("code_flow_context") or {}
    # AIHUB-0056 B: marker RECOVERY from the conversation itself. The live
    # pack-10 break: the terse "run it" after a multi-turn build delegated to
    # the builder because the session-state marker was gone — while the
    # message HISTORY (which demonstrably survived; the agent still had full
    # context) carried the native read-back pins. Those pins are deterministic
    # fingerprints only the native workflow path emits, so an absent marker is
    # reconstructed from them. The follow-up regex/mini-LLM still decides
    # whether THIS message continues the authoring — an unrelated ask falls
    # through to normal classification exactly as before.
    if not _continuity_marker and _native_impl(state):
        try:
            for _hist_m in messages[-6:-1]:
                _hc = getattr(_hist_m, "content", "")
                if isinstance(_hc, str) and (
                        "Authoritative persisted state" in _hc
                        or "🧾 Read-back of the saved row" in _hc):
                    _continuity_marker = {"name": "", "kind": "visual_workflow"}
                    logger.info(
                        "[classify_intent] visual-workflow continuity marker RECOVERED "
                        "from the conversation's read-back fingerprint (state marker absent)")
                    break
        except Exception as _rec_err:
            logger.debug(f"[classify_intent] marker recovery skipped: {_rec_err}")
    _marker_is_workflow = (isinstance(_continuity_marker, dict)
                           and _continuity_marker.get("kind") == "visual_workflow")
    if (_continuity_marker and not _builder_in_flight(active)
            and (_marker_is_workflow or _AUTOMATIONS_TOOLS_ENABLED)):
        try:
            from graph.build_routing import (looks_like_code_flow_followup,
                                             looks_like_workflow_followup)
            # kind="visual_workflow" is stamped ONLY by the native agent's
            # workflow tools, so classic sessions take the code-flow matcher
            # under the original condition — byte-for-byte classic behavior.
            if _marker_is_workflow:
                # Deterministic cues are only a fast-path; on a miss, a
                # mini-LLM makes the call (james's directive: intelligence
                # over keyword matching). Fail-open — an uncertain or failed
                # check falls through to normal classification.
                _followup_hit = looks_like_workflow_followup(user_text)
                if not _followup_hit:
                    _followup_hit = await _is_workflow_followup_llm(
                        user_text, _continuity_marker, state)
            else:
                _followup_hit = looks_like_code_flow_followup(user_text)
            if _followup_hit:
                logger.info("[classify_intent] %s continuity → intent=chat "
                            "(mid-authoring; keep follow-up in converse)",
                            "workflow" if _marker_is_workflow else "code-flow")
                _cf_out = {"intent": "chat", "pending_agent_selection": False,
                           "code_flow_context": state.get("code_flow_context")}
                if active:
                    _cf_out["active_delegation"] = None
                return _cf_out
        except Exception as _cf_err:
            logger.debug(f"[classify_intent] code-flow continuity skipped: {_cf_err}")

    # ── Deterministic build guard (AIHUB-0015 F1 / 0021 F1) ─────────────
    # Fires before EVERY routing layer — including the active-delegation
    # mini-LLM router, whose nondeterministic CONTINUE could hand a
    # MID-SESSION build request to a chat/data agent to role-play (the
    # fabricated-success bug). Builder delegations are exempt: their
    # CONTINUE path already returns intent='build' and must keep the
    # builder session state.
    # LEGACY keyword guards — only when smart routing is OFF (instant revert
    # path). When smart routing is ON, the strong build-shape decision after
    # the classifier below replaces both of these with one intelligent call.
    if not _SMART_BUILD_ROUTING:
        if _is_explicit_build_request(user_text) and \
                str((active or {}).get("agent_type") or "").lower() != "builder":
            logger.info(
                "[classify_intent] deterministic build guard matched — forcing intent=build"
            )
            _guard_out = {"intent": "build", "pending_agent_selection": False}
            if active:
                _guard_out["active_delegation"] = None
            return _guard_out
        if (_AUTOMATIONS_TOOLS_ENABLED
                and _BUILD_GUARD_AUTOMATION_RE.search(user_text)
                and str((active or {}).get("agent_type") or "").lower() != "builder"):
            logger.info(
                "[classify_intent] automation guard matched — forcing intent=chat "
                "(converse owns the automation tools)"
            )
            _auto_out = {"intent": "chat", "pending_agent_selection": False}
            if active:
                _auto_out["active_delegation"] = None
            return _auto_out

    if not active or not active.get("agent_id"):
        from cc_config import USE_ROUTE_MEMORY
        # Never let a remembered agent route swallow a build request — a
        # platform mutation must reach the Builder (AIHUB-0015 F1).
        if USE_ROUTE_MEMORY and not _is_explicit_build_request(user_text):
            try:
                user_ctx = state.get("user_context") or {}
                _rm_user_id = user_ctx.get("user_id")
                if _rm_user_id:
                    from command_center.memory.route_memory import find_route, CC_TOOL_PREFIX
                    route_match = await find_route(int(_rm_user_id), user_text)
                    if route_match and route_match.get("success_rate", 0) >= 0.7:
                        is_cc_tool = route_match.get("is_cc_tool", False)
                        logger.info(
                            f"[classify_intent] Route memory hit: '{route_match['normalized_query']}' "
                            f"-> {'CC tool' if is_cc_tool else 'agent'} {route_match.get('agent_id')} "
                            f"({route_match['usage_count']}x, {route_match['success_rate']:.0%} success)"
                        )

                        if is_cc_tool:
                            # CC tool route: shortcut to intent (typically "chat")
                            # so converse handles it with CC-native tools.
                            # Do NOT set route_memory_match — that's for agent shortcuts
                            # in gather_data which would try to delegate to the
                            # synthetic "cc:search_documents" as a real agent.
                            return {
                                "intent": route_match["intent"],
                                "pending_agent_selection": False,
                            }
                        else:
                            # Agent route: pass the match so gather_data can shortcut
                            return {
                                "intent": route_match["intent"],
                                "route_memory_match": route_match,
                                "pending_agent_selection": False,
                            }
            except Exception as rm_err:
                logger.warning(f"[classify_intent] Route memory lookup failed (non-blocking): {rm_err}")

    # ── Active delegation routing ───────────────────────────────────────────
    # If we're mid-conversation with an agent, decide whether to continue
    # or break out.  Two approaches gated by config flag.
    _clear_delegation = False  # set True to return active_delegation=None
    _created_resources = []    # populated when clearing a completed build delegation
    _reroute_detected = False  # set True when REROUTE is detected (vs CC_CAPABLE)

    if active and active.get("agent_id"):
        from cc_config import USE_PRINCIPLED_ROUTING
        agent_name = active.get("agent_name", f"Agent #{active.get('agent_id')}")
        agent_type = str(active.get("agent_type") or "data").lower()
        build_status = active.get("build_status")

        if USE_PRINCIPLED_ROUTING:
            # ── Stage 1: Deterministic rules (no LLM) ────────────────────
            if agent_type == "builder" and build_status == "completed":
                # Build is fully DONE — clear delegation and classify normally.
                # Note: "partial" and "failed" are NOT cleared here — the user
                # may want to retry or fix the build, so Stage 2 LLM routing
                # decides whether "try again" = CONTINUE or "show me sales" = REROUTE.
                logger.info(f"[classify_intent] Build completed — clearing delegation, classifying normally")
                _clear_delegation = True
                # Carry created resources forward so gather_data can use the
                # newly created agent instead of falling back to the default.
                _created_resources = active.get("created_resources") or []
                # Fall through to heuristic / LLM classification below

            else:
                # ── Stage 2: Principled LLM routing ───────────────────────
                _job_descriptions = {
                    "builder": "creating and modifying platform resources (agents, connections, workflows)",
                    "data": "querying databases and analyzing data",
                    "general": "answering domain-specific questions within its scope",
                }
                job_desc = _job_descriptions.get(agent_type, _job_descriptions["general"])

                from cc_config import get_step_llm as _get_step_llm
                try:
                    routing_llm = _get_step_llm("active_delegation_routing")

                    # Sanitize user text to avoid Azure content filter triggering
                    # on credential-like content in the routing prompt
                    import re as _re
                    _sanitized = _re.sub(
                        r'(?i)(password|pwd|secret|token|api.?key)\s*[:=]?\s*\S+',
                        r'\1 [REDACTED]',
                        user_text,
                    )

                    _conv_ctx = _format_conversation_for_prompt(messages)
                    _conv_block = (
                        f"\nRecent conversation (chronological, for reference):\n{_conv_ctx}\n"
                        if _conv_ctx else ""
                    )

                    routing_prompt = f"""You are a routing classifier for an AI orchestrator.

The user is in an active session with "{agent_name}" ({agent_type} agent).

The current agent's job: {job_desc}
{_conv_block}
The user just said: "{_sanitized}"

Classify the user's intent:
- CONTINUE: The user is providing information, answering a question, confirming,
  or making a request directly related to what this agent is doing right now
- REROUTE: The user has shifted to a fundamentally different task — asking for
  something the current agent does not do (e.g., asking a builder to query data,
  asking a data agent to create a workflow), OR is explicitly asking you to
  use a different agent (phrases like "use X instead", "try the Y agent",
  "ask Z"). Use the recent conversation above to understand what the user is
  actually referring to when their latest message is terse or pronoun-heavy.
- CC_CAPABLE: The user needs orchestrator-only capabilities — document search,
  maps, images, web search, file exports, email, or multi-agent coordination.
  IMPORTANT: searching for documents, files, contracts, leases, invoices, or
  policies is ALWAYS CC_CAPABLE — data agents cannot search the document repository.

Reply with ONLY one word: CONTINUE, CC_CAPABLE, or REROUTE."""

                    _rt0 = _trace_time.perf_counter()
                    _rt_msgs = [HumanMessage(content=routing_prompt)]
                    routing_result = await routing_llm.ainvoke(_rt_msgs)
                    trace_llm_call(state, node="classify_intent", step="active_delegation_routing",
                                   messages=_rt_msgs, response=routing_result,
                                   elapsed_ms=int((_trace_time.perf_counter() - _rt0) * 1000), model_hint="mini")
                    routing_category = routing_result.content.strip().upper().split()[0] if hasattr(routing_result, 'content') else "CONTINUE"
                    logger.info(f"[classify_intent] Active delegation routing: '{routing_category}' for: {user_text[:80]}")
                except Exception as e:
                    logger.warning(f"[classify_intent] Routing LLM failed: {e}")
                    # Smarter fallback: for builder delegations with partial/failed
                    # status, default to REROUTE — the user is more likely shifting
                    # to use what was built than continuing a failed build.
                    if agent_type == "builder" and build_status in ("partial", "failed"):
                        logger.info("[classify_intent] LLM failed + builder partial/failed — defaulting to REROUTE")
                        routing_category = "REROUTE"
                    else:
                        logger.info("[classify_intent] LLM failed — defaulting to CONTINUE")
                        routing_category = "CONTINUE"

                if routing_category == "CONTINUE":
                    if agent_type == "builder":
                        logger.info("Active delegation to Builder Agent — routing to build")
                        return {"intent": "build"}
                    logger.info(f"Active delegation to {agent_name} [{active.get('agent_id')}] — continuing")
                    return {"intent": "query"}

                # CC_CAPABLE or REROUTE — break out and clear delegation
                logger.info(f"[classify_intent] Breaking out of {agent_name} delegation — routing_category={routing_category}")
                _clear_delegation = True
                if routing_category == "REROUTE":
                    _reroute_detected = True

        else:
            # ── Legacy routing (rollback path) ────────────────────────────
            from cc_config import get_step_llm as _get_step_llm_legacy
            try:
                routing_llm = _get_step_llm_legacy("active_delegation_routing")

                # Sanitize user text to avoid Azure content filter
                import re as _re
                _sanitized = _re.sub(
                    r'(?i)(password|pwd|secret|token|api.?key)\s*[:=]?\s*\S+',
                    r'\1 [REDACTED]',
                    user_text,
                )

                _conv_ctx_leg = _format_conversation_for_prompt(messages)
                _conv_block_leg = (
                    f"\nRecent conversation (chronological, for reference):\n{_conv_ctx_leg}\n"
                    if _conv_ctx_leg else ""
                )

                routing_prompt = f"""You are a routing classifier for an AI Command Center orchestrator.

The user is currently in conversation with "{agent_name}" (a {agent_type} agent).

WHAT THE CURRENT AGENT CAN DO:
- Data agents: query databases, run SQL, return tables/charts, analyze data
- General agents: answer domain-specific questions within their scope
- Builder agents: create/modify platform resources (agents, workflows, connections)

WHAT THE CC ORCHESTRATOR CAN DO (that delegated agents CANNOT):
- Search the DOCUMENT REPOSITORY for files, contracts, leases, invoices, policies, reports
- Generate interactive maps (choropleth, markers, geographic visualization)
- Generate images (DALL-E)
- Search the web for real-time information (news, weather, prices, events)
- Manage user preferences and system settings
- Export data to files (Excel, CSV, PDF)
- Send emails
- Run custom/generated tools
{_conv_block_leg}
The user just said: "{_sanitized}"

Use the recent conversation above to understand terse or pronoun-heavy
latest messages ("use the other one", "try a different agent", "yes").

Does this request:
A) Belong ENTIRELY to the current agent's capabilities → reply CONTINUE
B) Require CC orchestrator capabilities (listed above) → reply CC_CAPABLE
C) Need a different type of agent, is a completely new topic, OR the user
   is explicitly asking you to switch to a named agent → reply REROUTE

If the request is COMPOUND (needs both data AND visualization/export/etc.), reply CC_CAPABLE — the orchestrator will handle coordination.

Reply with ONLY one word: CONTINUE, CC_CAPABLE, or REROUTE."""

                _rt0_leg = _trace_time.perf_counter()
                _rt_msgs_leg = [HumanMessage(content=routing_prompt)]
                routing_result = await routing_llm.ainvoke(_rt_msgs_leg)
                trace_llm_call(state, node="classify_intent", step="active_delegation_routing_legacy",
                               messages=_rt_msgs_leg, response=routing_result,
                               elapsed_ms=int((_trace_time.perf_counter() - _rt0_leg) * 1000), model_hint="mini")
                routing_category = routing_result.content.strip().upper().split()[0] if hasattr(routing_result, 'content') else "CONTINUE"
                logger.info(f"[classify_intent] Active delegation routing (legacy): '{routing_category}' for: {user_text[:80]}")
            except Exception as e:
                logger.warning(f"[classify_intent] Routing LLM failed: {e}")
                if agent_type == "builder" and build_status in ("partial", "failed"):
                    logger.info("[classify_intent] LLM failed + builder partial/failed — defaulting to REROUTE (legacy)")
                    routing_category = "REROUTE"
                else:
                    logger.info("[classify_intent] LLM failed — defaulting to CONTINUE (legacy)")
                    routing_category = "CONTINUE"

            if routing_category == "CONTINUE":
                if agent_type == "builder":
                    logger.info("Active delegation to Builder Agent — routing to build")
                    return {"intent": "build"}
                logger.info(f"Active delegation to {agent_name} [{active.get('agent_id')}] — continuing")
                return {"intent": "query"}

            logger.info(f"[classify_intent] Breaking out of {agent_name} delegation (legacy) — routing_category={routing_category}")
            _clear_delegation = True
            if routing_category == "REROUTE":
                _reroute_detected = True

    # Helper: include active_delegation=None in the return dict when clearing
    def _intent_result(d: dict) -> dict:
        if _clear_delegation:
            d.setdefault("active_delegation", None)
            # Pass created resources so gather_data can prefer the new agent
            if _created_resources:
                d["recently_created_resources"] = _created_resources
        return d

    # ── REROUTE shortcut — resolve named agent & delegate directly ──────
    # When the user says "ask agent X instead" during an active delegation,
    # resolve agent X from the landscape and extract the original question
    # from the delegation history, then route to gather_data directly.
    # This avoids the multi-step decomposition pipeline which would treat
    # "ask agent X instead" as a meta-task and never return actual results.
    if _reroute_detected and active:
        try:
            from command_center.orchestration.landscape_scanner import scan_platform as _rr_scan
            _rr_landscape = await _rr_scan()
            _rr_all_agents = _rr_landscape.get("all_agents", [])

            # Simple name matching: find the longest agent name present in user text
            _ut_lower = user_text.lower()
            _best_agent = None
            _best_len = 0
            for _a in _rr_all_agents:
                _aname = (_a.get("agent_name") or "").lower()
                if _aname and _aname in _ut_lower and len(_aname) > _best_len:
                    _best_agent = _a
                    _best_len = len(_aname)

            if _best_agent:
                # Ask a mini-LLM to identify which prior question the user
                # wants rerouted. This handles the nuanced cases — failed
                # follow-up in a long delegation, reroute carrying its own
                # question, multi-topic conversations — that a "take-first"
                # or "take-last" heuristic would get wrong.
                original_question = await _extract_reroute_question(
                    reroute_message=user_text,
                    delegation_history=active.get("history", []),
                    recent_messages=messages[:-1] if messages else [],
                    state=state,
                )

                if original_question:
                    logger.info(
                        f"[classify_intent] REROUTE resolved: {_best_agent.get('agent_name')} "
                        f"[{_best_agent.get('agent_id')}], original question: {original_question[:80]}"
                    )
                    return _intent_result({
                        "intent": "query",
                        "reroute_context": {
                            "agent_id": str(_best_agent["agent_id"]),
                            "agent_name": _best_agent.get("agent_name", "Agent"),
                            "is_data_agent": bool(_best_agent.get("is_data_agent", False)),
                            "original_question": original_question,
                        },
                        "landscape": _rr_landscape,
                        "pending_agent_selection": False,
                    })
                else:
                    logger.info("[classify_intent] REROUTE: resolved agent but no original question found")
            else:
                logger.info(f"[classify_intent] REROUTE: could not resolve agent name from: {user_text[:80]}")
        except Exception as _rr_err:
            logger.warning(f"[classify_intent] REROUTE agent resolution failed: {_rr_err}")
        # If resolution failed, fall through to normal LLM classification

    # ── Capability router (mini-LLM shortcut) ──────────────────────────
    # Replaces the old keyword heuristic shortcuts with a semantic
    # classifier that maps the user's message to a CC-native capability
    # (document search, web search, map, image generation, run tool,
    # build) or to 'none' when the full classifier should handle it.
    # On confident match we shortcut here; otherwise we fall through.
    if USE_CAPABILITY_ROUTER:
        router_result = await _run_capability_router(user_text, state=state)
        _cap = router_result.get("capability", "none")
        _conf = float(router_result.get("confidence", 0.0))
        _thresh = float(router_result.get("confidence_threshold", 0.7))
        _intent = router_result.get("intent")
        if _intent and _conf >= _thresh:
            # Native A/B agent: a confident 'build' from the router still defers
            # to the intelligent build-shape decision for the one question the
            # router can't answer — is the deliverable a visual workflow itself
            # (converse's native tools) or a platform object (builder)? Classic
            # turns skip this entirely (zero extra LLM cost, byte-identical).
            if _intent == "build" and await _native_workflow_shape_divert(user_text, state):
                return _intent_result({"intent": "chat", "pending_agent_selection": False,
                                       "code_flow_context": _authoring_marker(state)})
            logger.info(
                f"[classify_intent] capability_router matched: capability={_cap} "
                f"confidence={_conf:.2f} → intent={_intent}"
            )
            return _intent_result({"intent": _intent, "pending_agent_selection": False})
        else:
            logger.info(
                f"[classify_intent] capability_router fall-through: capability={_cap} "
                f"confidence={_conf:.2f} (threshold {_thresh:.2f})"
            )

    # ── Keyword heuristic shortcuts (optional, off by default) ──────────
    # LEGACY: retained behind CC_INTENT_HEURISTICS=true for operators who
    # still rely on the hardcoded keyword matches. Prefer the
    # capability_router above — it replaces this block with a semantic
    # classifier that scales as new tools appear. These keywords cause
    # false positives on multi-step requests (e.g. "search for lease
    # agreements then export to Excel" triggers the web-search shortcut
    # on "search for").
    if USE_INTENT_HEURISTICS:
        ut = user_text.lower()
        # Heuristic override: build/platform mutation requests should *always* route to Builder
        if (
            ("agent" in ut and ("create" in ut or "make" in ut or "build" in ut))
            or ("workflow" in ut and ("create" in ut or "build" in ut))
            or ("connection" in ut and ("create" in ut or "set up" in ut or "configure" in ut))
            or ("assign" in ut and "tool" in ut)
        ):
            return _intent_result({"intent": "build"})

        # Heuristic: "run tool" / "execute tool" / specific tool names → converse (has run_generated_tool)
        if ("run" in ut and "tool" in ut) or ("execute" in ut and "tool" in ut) or ("custom tool" in ut):
            logger.info("Detected 'run tool' request — routing to converse")
            return _intent_result({"intent": "chat"})

        # Heuristic: map/visualization requests → converse (has generate_map tool)
        # Must fire BEFORE LLM classifier which may confuse "create a map" with "build"
        map_signals = ["on a map", "on map", "map of", "show map", "shaded map",
                       "choropleth", "create a map", "create map", "generate a map",
                       "generate map", "make a map", "make map", "presentation-ready"]
        if any(sig in ut for sig in map_signals):
            logger.info("Detected map/visualization request — routing to converse")
            return _intent_result({"intent": "chat"})

        # Heuristic: web search / real-time info → converse (has search_web tool)
        web_signals = ["current price", "latest news", "what's happening", "search the web",
                       "search for", "look up", "google", "weather in", "weather for",
                       "stock price", "breaking news", "recent news", "today's news",
                       "what is the price of", "how much is"]
        if any(sig in ut for sig in web_signals):
            logger.info("Detected web search / real-time request — routing to converse")
            return _intent_result({"intent": "chat"})

        # Heuristic: document search → converse (has search_documents tool)
        if DOCUMENT_SEARCH_ENABLED:
            doc_search_signals = [
                "find document", "search document", "find report", "search report",
                "find file", "search file", "document about", "documents about",
                "find contract", "find invoice", "search invoice", "find policy",
                "look up document", "document search", "search for document",
                "find me document", "search files", "in the documents",
                "in our documents", "document that", "documents that",
                "find records", "search records",
            ]
            if any(sig in ut for sig in doc_search_signals):
                logger.info("Detected document search request — routing to converse")
                return _intent_result({"intent": "chat"})

        # Heuristic: image generation → converse (has generate_image tool)
        if any(sig in ut for sig in ["generate an image", "generate image", "create an image", "draw me",
                                      "make an image", "picture of", "illustration of", "image of a"]):
            logger.info("Detected image generation request — routing to converse")
            return _intent_result({"intent": "chat"})

        # Check if user is asking to run a specific generated tool by name
        from command_center.tools.tool_factory import list_generated_tools
        try:
            gen_tools = list_generated_tools()
            for gt in gen_tools:
                tool_name = gt.get("name", "")
                if tool_name and tool_name.replace("_", " ") in ut:
                    logger.info(f"Detected generated tool name '{tool_name}' in query — routing to converse")
                    return _intent_result({"intent": "chat"})
        except Exception:
            pass

    # Always scan the platform (cached, so fast after first call).
    # Pass user_context so the landscape is filtered to THIS user's agents.
    try:
        landscape = await scan_platform(state.get("user_context"))
    except Exception as e:
        logger.warning(f"Landscape scan failed in classify_intent: {e}")
        landscape = {}

    from datetime import datetime
    now = datetime.now()
    agent_summary = format_landscape_summary(landscape, max_agents=15)
    tool_summary = f"{len(landscape.get('mcp_servers', []))} MCP servers. Current date: {now.strftime('%Y-%m-%d')}"

    prompt = INTENT_CLASSIFICATION_PROMPT.format(
        agent_summary=agent_summary,
        tool_summary=tool_summary,
    )
    prompt += _preferences_block(state)

    _ic_conv = _format_conversation_for_prompt(messages)
    if _ic_conv:
        prompt += (
            "\n\n## Recent conversation (for disambiguating terse follow-ups)\n"
            f"{_ic_conv}\n"
        )

    try:
        from cc_config import get_step_llm
        llm = get_step_llm("intent_classification")
        _ic_msgs = [
            SystemMessage(content=prompt),
            HumanMessage(content=user_text),
        ]
        _ic_t0 = _trace_time.perf_counter()
        response = await llm.ainvoke(_ic_msgs)
        trace_llm_call(state, node="classify_intent", step="intent_classification",
                       messages=_ic_msgs, response=response,
                       elapsed_ms=int((_trace_time.perf_counter() - _ic_t0) * 1000), model_hint="full")
        intent = response.content.strip().strip('"').lower()
        _wf_divert_marker = None  # AIHUB-0056 B: set when shape=visual_workflow

        valid_intents = {"chat", "query", "analyze", "delegate", "build", "multi_step", "create_tool"}
        if intent not in valid_intents:
            logger.warning(f"Unknown intent '{intent}', defaulting to 'chat'")
            intent = "chat"

        # Smart build routing: for build-family intents, a stronger model picks
        # the SHAPE (automation / builder / both) — the one intelligent decision
        # replacing the deleted keyword guards. Cheap classifier gates it, so
        # only build-ish turns pay. Builder delegations mid-session are exempt.
        if (_SMART_BUILD_ROUTING and intent in _BUILD_FAMILY_INTENTS
                and str((active or {}).get("agent_type") or "").lower() != "builder"):
            shape = await _classify_build_shape(user_text, state)
            logger.info(f"[classify_intent] build-shape decision: {shape} (classifier said '{intent}')")
            if shape in ("automation", "both"):
                # converse holds BOTH the automation tools and
                # delegate_to_builder_agent, so it can do either or orchestrate both
                intent = "chat"
            elif shape == "visual_workflow":
                # Native A/B agent only (the label exists only in the native
                # prompt/parse): the deliverable IS a visual workflow —
                # converse's native workflow tools build it, never the builder.
                # AIHUB-0056 B: stamp the authoring marker at the divert so the
                # whole authoring conversation (clarifications included) keeps
                # continuity from turn 1.
                intent = "chat"
                _wf_divert_marker = _authoring_marker(state)
            elif shape == "builder":
                intent = "build"
            # 'neither' → keep the classifier's build-family intent as-is

        logger.info(f"Classified intent: {intent}")
        _final_out = {"intent": intent, "landscape": landscape, "pending_agent_selection": False}
        if _wf_divert_marker is not None:
            _final_out["code_flow_context"] = _wf_divert_marker
        return _intent_result(_final_out)

    except Exception as e:
        logger.error(f"Intent classification failed: {e}")
        return _intent_result({"intent": "chat", "landscape": landscape, "pending_agent_selection": False})


# ─── Node: converse ───────────────────────────────────────────────────────

async def converse(state: CommandCenterState) -> dict:
    """General conversation — grounded in real platform data."""
    from cc_config import get_llm, COMMAND_CENTER_SYSTEM_PROMPT, STRUCTURED_RESPONSE_FORMAT, IMAGE_GENERATION_ENABLED, DOCUMENT_SEARCH_ENABLED
    from command_center.orchestration.landscape_scanner import format_landscape_summary

    messages = state.get("messages", [])

    # W3a (#15): turn-scoped capture of the FULL delegate_to_builder result. The
    # delegate_to_builder_agent tool must return a string (LangChain contract), so it
    # stashes the {text,status,plan,builder_session_id} dict here and the tool-result
    # handler below reads it to derive an honest build_status (was hardcoded 'in_progress').
    _builder_capture: dict = {}
    
    # Always scan platform for real data (cached 60s, fast after first call)
    from command_center.orchestration.landscape_scanner import scan_platform
    try:
        landscape = await scan_platform(state.get("user_context"))
        logger.info(f"[converse] Landscape: {len(landscape.get('agents', []))} agents, {len(landscape.get('data_agents', []))} data agents")
    except Exception as e:
        logger.error(f"[converse] Landscape scan failed: {e}")
        landscape = state.get("landscape", {})
    
    # Build system prompt with landscape data embedded directly
    from datetime import datetime
    landscape_text = format_landscape_summary(landscape, max_agents=30)
    n_all = len(landscape.get("all_agents", []))
    n_data = len(landscape.get("data_agents", []))
    n_gen = len(landscape.get("agents", []))
    now = datetime.now()
    current_date = now.strftime("%A, %B %d, %Y at %I:%M %p")
    current_year = now.year
    last_year = current_year - 1

    # Portal automation context (only when enabled + the user may use it). Lists the user's
    # SAVED portals (names + URLs only, never creds) so the agent reuses them by name.
    _portal_prompt = ""
    if _PORTAL_FETCH_ENABLED and _portal_fetch_allowed(state):
        _saved_line = ""
        try:
            from command_center.tools import portal_registry as _reg
            _pl = _reg.list_portals((state.get("user_context") or {}).get("user_id"))
            if _pl:
                _saved_line = ("\nAlready saved (use by name, no URL/login needed): "
                               + ", ".join(f"{p['name']} ({p['url']})" for p in _pl) + ".")
        except Exception:
            pass
        _portal_prompt = (
            '## PORTAL AUTOMATION (fetch_from_portal / save_portal / lookup_portal / list_portal_workflows / describe_portal_workflow / run_portal_workflow)\nYou can log into external web portals to DOWNLOAD files for the user AND UPLOAD files to them (RPA).\n- DO IT NOW: if the user gives a portal URL and a login, call fetch_from_portal with portal_name, start_url, task, username, password and log in immediately. Don\'t refuse or stall.\n- UPLOAD A FILE: you CAN upload files to portals. Do NOT refuse on the assumption that a file isn\'t reachable, and do NOT try to \'verify the file exists\' yourself first - you CANNOT know in advance whether a given path is readable by the server, so let the TOOL try. Pass the file straight through: for an ad-hoc upload to any portal, call fetch_from_portal with file=<the path, or the name of a file attached to this chat> and a task describing the upload; for a saved portal workflow that already has an upload step, call run_portal_workflow(name, file=...). The tool actually attempts to read the file and reports the truth - NEVER claim you \'can\'t access\' or \'can\'t verify\' a file without having tried it through the tool. ONLY if the tool itself returns a \'couldn\'t find\' message do you ask the user to fix the path or attach the file - and when they correct it, call fetch_from_portal AGAIN with the new file (do not just re-check a previous run). An upload SUCCEEDS when the tool returns an \'Upload completed\' confirmation - there is NO download chip for an upload, so do NOT report a download/file as \'failed\' or \'not captured\' for an upload; just relay the upload confirmation (and any file name/size it includes).\n- CHOOSING a saved workflow vs ad-hoc: when a task might match a SAVED portal workflow, check first with list_portal_workflows (it shows each one\'s target + whether it uploads or downloads); use describe_portal_workflow to inspect its steps. If one clearly matches the site AND the task, prefer running it (deterministic and repeatable). If none fits, or it\'s a one-off/new portal, do it ad-hoc with fetch_from_portal. If your confidence is LOW that a saved workflow matches (only a loose name/target/goal match, or several could fit), ASK the user to confirm which to run (or whether to do it ad-hoc) BEFORE acting - don\'t guess.\n- OFFER TO SAVE: after a successful ad-hoc run where the user supplied a login, offer to save it ("Want me to save this so you don\'t have to share your login next time?"). If they agree, call save_portal(name, url, username, password). Credentials are stored encrypted; only a reference is kept.\n- REUSE SAVED: for a saved portal, call fetch_from_portal with just portal_name and task - the URL and credentials resolve automatically; never ask the user to resend a saved login. Use lookup_portal to list/confirm saved portals.\n- 2FA / TAKE OVER: if fetch_from_portal returns a \'🔐 ... Take over here: <link>\' message, the portal hit a 2-step verification (or similar) it can\'t do alone and has PAUSED for the user. Relay that message and the link VERBATIM - do not paraphrase or drop the link. After the user says they\'ve taken over / handed back (or asks if it\'s ready), call check_portal_download to fetch the file.\n- SAVED PORTAL WORKFLOWS: a *portal* workflow is a recorded login-and-download sequence for THIS portal feature - it is NOT the platform\'s regular Workflows (Workflow Designer / Workflow Agent), which are a different system you do NOT run with these tools. ONLY when the user clearly means a saved PORTAL/download workflow, call run_portal_workflow(name) (use list_portal_workflows to find the exact name). It runs headless/unattended. IMPORTANT: the bare word "workflow" usually means a regular Workflow, NOT a portal one. If the user says "run workflow X" without portal/download context, do NOT assume it\'s a portal workflow - ask which they mean.\n- SCHEDULING A RECURRING PORTAL DOWNLOAD: if the user wants a portal login-and-download to REPEAT on a cadence ("run this every 20 minutes", "every morning", "each weekday at 8am"), call schedule_portal_workflow - do NOT use delegate_to_builder_agent or schedule_task for this. It schedules the portal workflow recorded from the fetch you did THIS chat (saving it first if needed) as a real recurring headless job and returns the real job id. Pass email_after_run=true if the user wants the file emailed after each run. schedule_portal_workflow reuses the run already recorded this chat - when scheduling, do NOT also call fetch_from_portal or run_portal_workflow (that would needlessly repeat the login/2FA and download a duplicate). CRITICAL: to schedule OR reschedule you MUST call schedule_portal_workflow and relay its result verbatim - if it returns an error or no job number, tell the user honestly; NEVER claim a schedule was created/updated just because the workflow exists or is already scheduled. Checking a workflow with describe_portal_workflow is NOT scheduling it. If the workflow is already scheduled (describe_portal_workflow shows this), re-scheduling REPLACES that schedule (no duplicate) - still call the tool. Only if the user has NOT run this portal at all yet this chat, run the portal fetch ONCE first, then schedule.\n- DELIVERY & HONESTY: a downloaded file reaches the user ONLY as the inline download chip that fetch_from_portal returns. If the tool result does not include a downloaded file, then NO file was captured - say the download did not complete; do NOT claim you delivered a file, do NOT invent a download link or a Downloads-folder/\'artifact area\', and do NOT promise to save to the user\'s local disk (e.g. C:\\tmp) - you cannot. Files are delivered only as the chip.' + _saved_line
        )

    # Self-scheduling context (only when enabled + the user may use it). Lists the user's
    # existing scheduled tasks so the agent can reference/avoid duplicating them.
    _schedule_prompt = ""
    if _SCHEDULE_ENABLED and _schedule_allowed(state):
        _existing = ""
        try:
            from scheduling import schedule_logic as _sl
            _ts = _sl.list_cc_schedules(state.get("user_context") or {})
            if _ts:
                _existing = ("\nExisting scheduled tasks: "
                             + ", ".join(f"{t['task_name']} ({t.get('schedule_desc','?')})" for t in _ts) + ".")
        except Exception:
            pass
        _schedule_prompt = (
            '## SCHEDULED TASKS (schedule_task / list_scheduled_tasks / cancel_scheduled_task)\nYou can schedule yourself to re-run a task automatically on a recurring cadence, even when the user is offline.\n- When the user asks for something recurring ("every morning", "each weekday at 8am", "every 6 hours"), call schedule_task with a clear task_name, the prompt to run each time, and EITHER a cron expression OR every_hours/every_days/every_minutes.\n- GROUNDING: you MUST actually call the tool to schedule/reschedule, and report ONLY the real "job #N" it returns. NEVER tell the user something was scheduled or updated unless the tool returned a job number; if it errored, say so plainly.\n- Each run\'s output is saved to the user\'s Scheduled Tasks panel with a notification; the user need not be online to receive it.\n- TIMEZONE: a cron time runs in the user\'s timezone. Default (the user named no zone): leave timezone empty and their browser timezone is used automatically. If the user names a zone ("8am EST", "9am IST", "9am Pacific"), pass timezone=<the exact word they said> AND timezone_iana=<your best-guess IANA name, e.g. Asia/Kolkata>; for ambiguous abbreviations (IST = India/Israel/Ireland) confirm the region. Never compute UTC offsets yourself. For a specific time of day, use a cron ("0 9 * * *"), not every_days.\n- Use list_scheduled_tasks to show what\'s scheduled and cancel_scheduled_task to stop one.\n- EXCEPTION: for a recurring PORTAL download (logging into a website and downloading a file), do NOT use schedule_task - use schedule_portal_workflow instead.' + _existing
        )

    _sftp_prompt = ""
    if _SFTP_ENABLED and _sftp_allowed(state):
        _sftp_prompt = (
            "## SFTP / FTP FILE TRANSFER (sftp_list_files / sftp_download / sftp_upload)\n"
            "You can connect to an SFTP, FTP, or FTPS server to move files, using credentials the "
            "user provides in chat. Nothing is stored — the host/login is used only for that call.\n"
            "- sftp_list_files: list what's on the server (name, size, modified, age) before transferring.\n"
            "- sftp_download: pull a remote file down; it comes back to the user as a download chip "
            "automatically — do NOT fabricate a link or claim delivery if the tool reports an error.\n"
            "- sftp_upload: push a file the user uploaded to THIS chat up to the server (reference it "
            "by its filename).\n"
            "- DO IT NOW: if the user gives a host, login, and path, call the tool right away — do not "
            "stall or ask for confirmation you already have. Default protocol is 'sftp' (port 22); set "
            "protocol='ftp' or 'ftps' (port 21) when the user says so.\n"
            "- If the user shares a password, never repeat it back in your reply."
        )

    _automations_prompt = ""
    if _AUTOMATIONS_TOOLS_ENABLED and not _automations_allowed(state):
        # The user CANNOT build/run Automations. Say so explicitly, because
        # without this the LLM answers an "automations" question from whatever
        # it does have (e.g. the workflow list) instead of refusing —
        # AIHUB-0028 F2.
        _automations_prompt = (
            "## AUTOMATIONS — NOT AVAILABLE TO THIS USER\n"
            "This platform has an Automations feature (persisted AI-built Python solutions), "
            "but it requires a Developer role and THIS user does not have it. If the user asks "
            "to build, list, run, schedule or manage automations, politely explain that "
            "Automations require a Developer role on this instance — do NOT substitute other "
            "data (e.g. the workflow list) as if it were their automations."
        )
    if _AUTOMATIONS_TOOLS_ENABLED and _automations_allowed(state):
        _automations_prompt = (
            "## AUTOMATIONS vs. THE BUILDER — pick the right home for what the user wants\n"
            "You can build in two ways; choose by what the DELIVERABLE is, not by what is "
            "technically possible (a script can do almost anything). Many goals need BOTH — do each "
            "part with the right tool.\n"
            "- AUTOMATION (the tools below) — a Python SCRIPT the platform owns: it runs in its own "
            "DEDICATED Python environment where ANY pip library can be installed (pdfplumber, "
            "paramiko, pandas, ML, vendor SDKs), and the platform versions, runs, VERIFIES its "
            "declared outputs, and schedules it. Reach for this when the goal is a PROCESS — parse "
            "files, call APIs, transform/reconcile data, produce and move files (CSV/PDF → SFTP), on "
            "a schedule or trigger — i.e. when writing code with libraries is more direct than a "
            "many-step visual workflow.\n"
            "- BUILDER (delegate_to_builder_agent) — creates platform OBJECTS people interact with or "
            "other resources reference: a data/general AGENT someone chats with, a CONNECTION, an MCP "
            "server, a custom TOOL an agent calls, a KNOWLEDGE base, or a visual WORKFLOW the user "
            "wants to see and edit. If someone will TALK TO it or other resources will REFERENCE it, "
            "it is a Builder object, not an Automation.\n"
            "- Example needing BOTH: 'create an agent, load this doc into its knowledge, and a nightly "
            "export that feeds it' → agent + knowledge via the Builder, the export as an Automation.\n\n"
            "## AUTOMATIONS — the tools (create_automation / save_automation_code / dry_run_automation "
            "/ promote_automation / run_automation / schedule_automation / list_automations / "
            "get_automation / get_automation_runs)\n"
            "Write the code yourself; the platform versions, runs, verifies, and schedules it.\n"
            "- EXISTING CONNECTIONS & SECRETS: automations USE the tenant's existing AI Hub "
            "connections and secrets by NAME — the code calls aihub.connection('NAME') / "
            "aihub.secret('NAME') and the platform resolves them at run time. You do NOT need to "
            "create a new connection or 'SFTP integration' just to use one that already exists; "
            "declare its name in the manifest. Only create a connection/secret if it genuinely "
            "does not exist yet. When a name is given (e.g. an AIRDB connection, an AUTODEMO_SFTP "
            "secret), use it directly.\n"
            "- BUILD FLOW (follow in order): 1) confirm the process + the connection/secret NAMES to "
            "use, 2) create_automation, 3) save_automation_code with a manifest declaring "
            "inputs/connections/secrets/outputs, 4) dry_run_automation and SHOW the user the verified "
            "result, 5) only after the user confirms the dry-run is right: promote_automation, "
            "6) run now or schedule_automation. The dry-run is BUILT IN — when a user asks to "
            "'dry-run' a process, that is an automation, not a workflow.\n"
            "- CREDENTIALS: generated code must call aihub_runtime (aihub.connection('NAME') / "
            "aihub.secret('NAME')) — NEVER hard-code passwords or connection strings; saves that "
            "contain credential literals are rejected.\n"
            "- DECLARE OUTPUTS in the manifest (files with min_rows, uploads with remote_listing) — "
            "the runner independently verifies them after every run; that is what makes 'success' "
            "trustworthy. A run can be success / failed / unverified / skipped — report the outcome "
            "verbatim, never soften a failed or unverified run into success.\n"
            "- Scheduled runs execute the PROMOTED version only; editing code never changes a "
            "schedule until you promote again.\n"
            "- Do the AUTOMATION parts with these tools (never the Builder Agent — it does not know "
            "this asset type); if the same request ALSO needs an agent/connection/knowledge/MCP or an "
            "editable workflow, use delegate_to_builder_agent for that part. Every save must be "
            "confirmed to the user with the version number the tool returned."
        )
        _automations_prompt += (
            "\n\n## CODE FLOWS — the MULTI-STEP sibling of an Automation\n"
            "Within the same family, pick the SHAPE by the process:\n"
            "- Single self-contained script → an AUTOMATION (above).\n"
            "- A process with DISTINCT STAGES that should route independently — especially where a "
            "FAILURE partway through must branch to an alert/cleanup step, or where later stages "
            "consume earlier stages' files → a CODE FLOW. A Code Flow is a WORKFLOW of inline Code "
            "Step nodes reusing the platform's workflow engine; each step is Python run through the "
            "same runner (dedicated env, pip libraries, aihub_runtime SDK, output verification).\n"
            "- Code Flow tools: create_code_flow → add_code_step (once per stage) → wire_steps "
            "(on='pass'|'fail'|'complete') → dry_run_code_flow → schedule_code_flow. "
            "update_step_code edits a step in place when a run shows it failing. "
            "unwire_steps removes an edge and remove_code_step removes a step+its edges — when "
            "INSERTING a step between two wired steps, wire the two new edges AND unwire the old "
            "direct edge (a step may have at most ONE edge per outcome type; competing edges are "
            "rejected, never silently first-match). NOTE: "
            "dry_run_code_flow (and run_code_flow) EXECUTE the steps for real with live "
            "credentials — there is no sandbox; warn the user before running a flow with "
            "irreversible steps, and gate those with aihub.checkpoint().\n"
            "- STEP IDS: add_code_step RETURNS the step's id (a random 's…' id, NOT 's1'). Use "
            "that returned id in wire_steps and in any '${<returned_id>_files[0]}' reference — an "
            "unresolved ${…} is passed through as a literal string, not an error.\n"
            "- CONTROL FLOW is the point: wire a step's 'fail' edge to a notify/cleanup step so a "
            "partial failure is HANDLED, not silent. Pass files between steps by declaring the "
            "producer's outputs and referencing them in a consumer's input default as "
            "'${<step_id>_files[0]}'.\n"
            "- Same rules as automations: existing connections/secrets by NAME via aihub.*, never "
            "hard-code credentials, DECLARE outputs so each step is verified, report outcomes "
            "verbatim (success/failed per step). Build the flow, dry-run it, show the user, then "
            "schedule.\n"
            "- DATABASE access: use `aihub.query('CONN', sql, params)` (returns rows as dicts) — do "
            "NOT hand-roll pyodbc/SQLAlchemy. INPUT NAMES: the names a step reads with "
            "`aihub.input('X')` must match the input names it declares (a mismatch is rejected on "
            "save); pass a file between steps via an input defaulting to `${<upstream_step_id>_files[0]}`."
        )
        if _AUTOMATIONS_PROPOSE_CHECKPOINTS:
            _automations_prompt += (
                "\n- CHECKPOINTS (do this proactively): whenever the code you write reaches an "
                "IRREVERSIBLE step — uploading to an external system, deleting, sending, or acting "
                "on unusual data — insert aihub.checkpoint(\"<concrete, quantified message>\") "
                "before it, and tell the user you added a safety gate they will approve from "
                "Mission Control. Skip the gate only if the user explicitly says to run "
                "unattended."
            )

    # ── Native visual-workflow prompt (CC_AGENT="native" A/B agent) ─────────
    # On classic turns these three hold the historical text verbatim; on a
    # native Developer turn they teach the LLM to build visual workflows with
    # its OWN tools (delegation stays only for non-workflow platform objects).
    _workflow_native_prompt = ""
    _limitations_build_line = (
        "- You CANNOT directly create agents, workflows, or platform resources — you MUST use delegate_to_builder_agent for that.")
    _build_rule_5 = (
        "5. **Build/create request** (create an agent, build a workflow, set up a connection) → call delegate_to_builder_agent. NEVER pretend to create agents yourself.")
    if _native_impl(state) and _workflow_tools_allowed(state):
        try:
            from system_prompts import WORKFLOW_NODE_TYPES as _WF_NODE_DOC
        except Exception:
            _WF_NODE_DOC = "(node catalog unavailable — use get_workflow_structure on an existing workflow as a reference)"
        _workflow_native_prompt = (
            "## VISUAL WORKFLOWS — BUILD THEM YOURSELF (native workflow tools)\n"
            "You build and edit VISUAL WORKFLOWS directly with your own tools — NEVER delegate a "
            "workflow build to the Builder Agent. A visual workflow is the editable canvas of typed "
            "nodes under /workflow (distinct from: an AUTOMATION = one owned script; a CODE FLOW = a "
            "flow of Code Step nodes; a PORTAL workflow = a saved browser/RPA login-and-download owned "
            "by the portal tools).\n"
            "- THE TOOLS: create_workflow → add_workflow_node (one per step; config_json per the node "
            "catalog below; the first node auto-becomes the start) → wire_workflow_nodes "
            "(on='pass'|'fail'|'complete') → get_workflow_structure to review → run_workflow to test. "
            "Edit with update_workflow_node / remove_workflow_node / unwire_workflow_nodes / "
            "set_workflow_start / set_workflow_variable. check_workflow_run reads a run's honest "
            "per-step outcome later. To INSERT a node between two already-wired nodes ALWAYS use "
            "insert_workflow_node_between (ONE atomic call: adds + rewires from→new→to, rolls back "
            "on failure) — never a manual add+unwire+wire+wire sequence, which can be interrupted "
            "and leave the workflow disconnected.\n"
            "- EDITS RUN TOOLS (non-negotiable): NEVER claim you inserted/added/removed/rewired/"
            "saved anything unless the matching tool ran THIS turn and its result confirms it. If "
            "you did not call a tool, say plainly that no change has been made yet.\n"
            "- GROUNDING (non-negotiable): every save returns a 🧾 read-back of what the saved row "
            "REALLY contains plus the server's validity verdict. Describe the workflow ONLY from that "
            "read-back — never restate the user's request as if it were all built. If it says "
            "DRAFT / EMPTY / ROW MISMATCH, tell the user exactly that. Report run outcomes only from "
            "run_workflow / check_workflow_run; a still-running or paused run is reported as exactly "
            "that — never as success.\n"
            "- SLOT RULE: a node gets ONE outgoing 'pass' OR 'complete' edge (+ at most ONE 'fail'). "
            "Competing edges are rejected — when inserting a node between two wired nodes, wire the "
            "two new edges AND unwire the old direct edge.\n"
            "- CAPABILITY HONESTY: there is NO node for SFTP/FTP/API pushes or custom Python. If a "
            "requested step has no node in the catalog, SAY SO and offer the real homes — a CODE FLOW "
            "(or single AUTOMATION), or an Automation node running a promoted Automation as a workflow "
            "step. NEVER silently drop a requested step.\n"
            "- Database nodes take a NUMERIC connection id (as a string, e.g. \"1\") — never a "
            "connection name. When the user names a connection (\"our AIRDB connection\"), call "
            "list_data_connections and resolve the id YOURSELF; NEVER ask the user for a numeric id. "
            "Workflow variables use ${var} substitution; declare them with set_workflow_variable or "
            "a node's outputVariable.\n"
            "- DRAFT-FIRST (non-negotiable): when the request is clear about WHAT the workflow should "
            "do, BUILD it THIS turn. Resolve connections with list_data_connections; where a detail "
            "is unspecified (exact columns, join keys, query shape), write a reasonable, clearly-"
            "labeled first-cut config and STATE the assumption in your reply — the save's validity "
            "verdict and 🧾 read-back keep you honest, and the user refines from a draft far faster "
            "than from an interrogation. Ask AT MOST ONE focused question per build, and only when "
            "something essential cannot be resolved with your tools (never re-ask for names, paths, "
            "or recipients already given). NEVER stall a clear request behind multiple rounds of "
            "clarification.\n"
            "- After building, show the read-back structure so the user sees exactly what exists.\n\n"
            "### NODE CATALOG — the ONLY valid node types and their config\n"
            + _WF_NODE_DOC
        )
        _limitations_build_line = (
            "- VISUAL WORKFLOWS you build YOURSELF with the workflow tools (create_workflow / "
            "add_workflow_node / …) — do NOT delegate workflow builds. Other platform resources "
            "(agents, connections, MCP servers, knowledge bases, custom tools) you CANNOT create "
            "directly — use delegate_to_builder_agent for those.")
        _build_rule_5 = (
            "5. **Build/create request**: a VISUAL WORKFLOW build/edit → use YOUR OWN workflow tools "
            "(create_workflow / add_workflow_node / wire_workflow_nodes / …) — never delegate a "
            "workflow build. Any OTHER platform object (agent, connection, MCP server, knowledge "
            "base, custom tool) → call delegate_to_builder_agent. NEVER pretend to create agents yourself.")

    system_prompt = COMMAND_CENTER_SYSTEM_PROMPT + f"""

## CURRENT DATE/TIME
Today is {current_date}. The current year is {current_year}. Last year was {last_year}.
When users say "last year" they mean {last_year}. When they say "this year" they mean {current_year}.

## PLATFORM SCAN RESULTS ({n_all} agents found: {n_data} data agents, {n_gen} general agents)

The following is your live platform scan. This data is REAL and CURRENT.
When the user asks about agents, capabilities, or resources — use THIS data to answer.

{landscape_text}

## USER MEMORY (cross-session context)
{state.get("user_memory", "No prior interaction history.")}

## SESSION ACTIVITY (resources created in this session)
{_format_session_resources(state.get("session_resources"))}

## YOUR ROLE — COMMAND CENTER ORCHESTRATOR
You are the COMMAND CENTER — an intelligent orchestrator that sits BETWEEN the user and all platform agents.
- The user talks ONLY to you. You decide what to do with their message.
- When a user asks for DATA → call query_data_agent or query_general_agent tools (REAL API calls)
- When a user gives you a COMMAND (switch agent, remember something, set defaults) → handle it yourself
- When a delegated agent responds → YOU interpret and present the results. Do NOT parrot agent responses as your own words.
- NEVER pretend to be the delegated agent. You are the orchestrator.

## YOUR MEMORY — YOU HAVE PERSISTENT MEMORY
You have persistent memory that improves over time. The USER MEMORY section below shows
memories relevant to this conversation. Your memory tools:
- save_user_preference: Save a preference. Be SPECIFIC about domain (e.g., "Use Agent 14 for sales", not just "Use Agent 14")
- forget_preference: Remove a saved preference (e.g., "forget the dark chart theme")
- recall_all_memories: See ALL saved memories (the USER MEMORY section only shows relevant ones)

When saving preferences, check USER MEMORY first — don't save duplicates.
Your memory learns automatically from successful and failed interactions.
NEVER say "I don't have memory" or "each conversation is a fresh start" — that is FALSE.

## MESSAGE CLASSIFICATION — CRITICAL
Before processing ANY user message, classify it:
1. **User command to YOU** (switch agent, remember X, what can you do, etc.) → handle directly, do NOT forward to any agent
2. **Data query** (show sales, revenue report, how many orders, etc.) → call query_data_agent with the right agent
3. **Platform question** (what agents do I have, list connections, etc.) → answer from platform scan data
4. **Follow-up on previous data** (top 3 from that, drill down, etc.) → forward to the SAME agent via query_data_agent
{_build_rule_5}
6. **Map/visualization request** → use YOUR OWN tools: generate_map for maps/choropleths, generate_image for images. Do NOT ask the data agent to create maps — data agents can only retrieve data, not generate visualizations. If you already have the data from a previous response, use it directly with generate_map instead of querying the data agent again.
7. **Chart request** on existing data → call query_data_agent asking for a chart (data agents CAN produce charts via matplotlib, but NOT maps).
8. **Email request** (send an email, email this to someone, share via email) → use YOUR OWN send_email tool. If files need to be attached, first create them with export_data, then call send_email with the artifact_id. Do NOT delegate email to general agents.
9. **Answer to a builder question** — if the Builder Agent asked a follow-up question and the user is answering it, call delegate_to_builder_agent with the user's answer. The builder maintains conversation history.
10. **Document search request** (find documents, search files/records/reports, look up contracts/invoices/policies) → use YOUR OWN search_documents tool. Do NOT confuse with data queries — document search is for the document repository, not database tables.

If user says "switch to Agent X" or "use Agent X instead" → call switch_active_agent, do NOT forward to the old agent.

## FILE EXPORTS
You have an export_data tool that generates downloadable files (Excel, CSV, PDF, JSON).
- For platform data (agent lists, connections, etc.): pass the data directly using the 'data' parameter as a JSON array of objects. You already have this data from the platform scan above — no need to query a data agent.
- For data agent query results: call export_data without the 'data' parameter — it will find the table in the conversation history.
- Example: to export agents to Excel, call export_data(format="excel", name="general_agents", data='[{{"ID": 1, "Name": "..."}}]')

## EMAIL
You have a send_email tool that sends emails via the platform email service.
- send_email accepts: to_address, subject, message, and an optional artifact_id
- To email a file: FIRST call export_data to create the file, note the artifact_id from the result, THEN call send_email with that artifact_id
- Do NOT delegate email tasks to general agents — use YOUR OWN send_email tool
- To email the USER THEMSELVES ("email me", "send me…"), FIRST call get_my_contact_info to get their email address, THEN send_email to it. Never guess the user's address.
- Example workflow: user says "create an Excel of top 10 customers and email it to bob@example.com"
  1. Call export_data(format="excel", name="top_10_customers", data='[...]') → get artifact_id from result
  2. Call send_email(to_address="bob@example.com", subject="Top 10 Customers", message="Please find the report attached.", artifact_id="<artifact_id from step 1>")

## CUSTOM TOOLS
You can run previously created custom tools using run_generated_tool. Pass the tool_name and parameters (JSON string).
If the user asks to use a tool that was previously created, use run_generated_tool.

## IMAGE GENERATION
{"You CAN generate images using the generate_image tool (DALL-E 3). When a user asks to create/draw/generate an image, use generate_image with a detailed prompt. Available sizes: 1024x1024, 1024x1792 (portrait), 1792x1024 (landscape)." if IMAGE_GENERATION_ENABLED else "Image generation is NOT available on this instance. If asked to create images, politely explain that image generation is not enabled."}

## CODE INTERPRETER (run_python)
{'''You have a run_python tool that executes real Python (pandas, numpy, matplotlib, scipy, seaborn, openpyxl available). USE IT for: calculations, statistics, parsing/transforming data, and creating charts, plots, or spreadsheet/CSV files. PREFER computing real numbers over estimating them.
- Files the user uploaded to this chat are already in the working directory — read them by filename (e.g. pd.read_csv('data.csv')).
- ANY file your code writes (plt.savefig('chart.png'), df.to_excel('out.xlsx'), etc.) is automatically returned to the user as a downloadable artifact; images also display inline. Use print() for text results.
- Write complete, self-contained scripts. You cannot ask the user mid-execution.''' if _CODE_INTERPRETER_ENABLED else "Code execution is not available on this instance."}
{_portal_prompt}
{_schedule_prompt}
{_sftp_prompt}
{_automations_prompt}
{_workflow_native_prompt}

## WEB SEARCH — REAL-TIME INFORMATION
You have a search_web tool that performs live internet searches via Tavily (with DuckDuckGo fallback).
USE search_web when the user asks about:
- Current weather, news, or events
- Recent developments, releases, or announcements
- Anything that requires up-to-date or real-time information
- Facts you're unsure about or that may have changed recently
- Industry benchmarks, trends, or statistics you want to verify
DO NOT try to answer real-time questions from memory alone — call search_web first, then summarize the results for the user.

## DOCUMENT SEARCH
{"You have a search_documents tool that performs intelligent AI-driven search across the platform document repository. USE search_documents when the user asks about:" + chr(10) + "- Finding documents, files, records, or reports in the document repository" + chr(10) + "- Looking up information stored in documents (contracts, invoices, policies, manuals, etc.)" + chr(10) + "- Questions that require searching through document content" + chr(10) + "- 'Find me documents about X', 'What documents mention Y', 'Search for Z'" + chr(10) + "The tool uses AI to determine the best search strategy (semantic, field-based, hybrid, or wide-net) and returns matching documents with relevant content. Pass the user's question directly — the tool handles query analysis internally." + chr(10) + "Do NOT confuse document search with data queries — use query_data_agent for database/data questions, use search_documents for document repository searches." if DOCUMENT_SEARCH_ENABLED else "Document search is NOT available on this instance. If asked to search documents, explain that document search is not enabled."}

## PDF MANIPULATION — USE manipulate_pdf
- When the user wants to split, extract pages from, or rotate a PDF they uploaded, call `manipulate_pdf` with the file_id from the attached files.
- Operations: "split_all" (one PDF per page), "extract_pages" (pages="1,3,5-7"), "rotate" (degrees=90/180/270, pages="all" or a spec).
- DO NOT write Python code for these operations. The tool produces downloadable artifacts directly.
- For a single PDF the user attached, the file_id is in the "Attached Files" reference shown to you.

## MAP & VISUALIZATION — USE YOUR OWN TOOLS
- YOU have `generate_map` — use it for maps, choropleths, geographic visualizations.
- Data agents CANNOT create maps. They can only retrieve data.
- If the user asks for a map and you already have the data (from a previous query_data_agent response), call generate_map directly with that data. Do NOT re-query the data agent.
- If you need data first, call query_data_agent to get the data, THEN call generate_map with the results.
- For choropleth maps: pass a JSON object with "regions" array (each has "name", "value", "label").

## LIMITATIONS — BE HONEST
{_limitations_build_line}
- You CANNOT run arbitrary code outside of custom tools. If you don't have a tool for something, say so.
- NEVER hallucinate capabilities you don't have. If you can't do it, say "I don't have that capability yet."

## RESPONSE INSTRUCTIONS
- Respond in plain markdown format (headers, bullet lists, bold, tables, etc.)
- For simple questions, just answer in clear markdown
- When listing agents, show their ID, name, and objective
- When presenting data from an agent, clearly label it: "📊 Data from [Agent Name]:"
- Never say "no agents found" or "I couldn't retrieve" — the scan results above ARE your data
- Do NOT wrap your response in JSON. Just return plain markdown text.
"""

    llm_messages = [SystemMessage(content=system_prompt)] + list(messages[-20:])

    # Define tools the LLM can call for real data
    from langchain_core.tools import tool as lc_tool

    @lc_tool
    async def query_data_agent(agent_id: int, question: str) -> str:
        """Send a natural language question to a data agent and get real results.
        Use this whenever the user asks for actual data (sales, orders, revenue, etc.).
        Pick the most relevant agent_id from the platform scan results above.
        The agent may return tables, charts, and insights — present them clearly to the user."""
        from command_center.orchestration.delegator import delegate_to_agent
        try:
            agent_id = int(agent_id)
        except Exception:
            return f"Error: agent_id must be an integer (got: {agent_id})."

        logger.info(f"[converse/tool] Calling data agent {agent_id}: {question}")
        result = await delegate_to_agent(
            agent_id=str(agent_id),
            question=question,
            is_data_agent=True,
            session_id=state.get("session_id", "cc-default"),
            user_context=state.get("user_context"),
        )
        if result.get("status") == "failed":
            error_text = result.get("text", "Unknown error")
            logger.warning(f"[converse/tool] Data agent {agent_id} failed: {error_text}")
            return (f"⚠️ The data agent (Agent #{agent_id}) could not complete the request. "
                    f"This is likely a temporary issue (database offline, connection timeout). "
                    f"Error: {error_text}. Please try again shortly.")
        
        # Build response with rich content if available
        response_parts = [result.get("text", "No response from agent.")]
        
        # Include rich content blocks (charts, enhanced tables, insights)
        rich = result.get("rich_content")
        if rich:
            try:
                if isinstance(rich, str):
                    rich = json.loads(rich)
                if isinstance(rich, dict) and rich.get("blocks"):
                    for block in rich["blocks"]:
                        btype = block.get("type", "")
                        if btype == "chart_image" and block.get("content"):
                            response_parts.append(f"\n[CHART_IMAGE:{block['content']}]")
                        elif btype == "table" and block.get("content"):
                            # Format table data as markdown — capped: this string is
                            # fed back into the follow-up LLM call, so inlining every
                            # row of a large table would blow the context window (the
                            # renderer-side block can now carry up to 10k rows).
                            table_data = block["content"]
                            if isinstance(table_data, list) and table_data:
                                meta = block.get("metadata") or {}
                                total_rows = meta.get("total_rows") or len(table_data)
                                shown = table_data[:_DELEGATED_TABLE_LLM_ROW_CAP]
                                cols = list(shown[0].keys())
                                header = " | ".join(cols)
                                sep = " | ".join(["---"] * len(cols))
                                rows = "\n".join(" | ".join(str(r.get(c, "")) for c in cols) for r in shown)
                                response_parts.append(f"\n{header}\n{sep}\n{rows}")
                                if len(table_data) > len(shown) or (isinstance(total_rows, int) and total_rows > len(shown)):
                                    response_parts.append(
                                        f"\n[table preview: showing first {len(shown)} of {total_rows} rows — "
                                        f"do not present this as the complete dataset]")
                        elif btype == "list" and block.get("content"):
                            items = block["content"]
                            if isinstance(items, list):
                                response_parts.append("\n**Key Insights:**\n" + "\n".join(f"- {i}" for i in items))
            except Exception as e:
                logger.warning(f"[converse/tool] Rich content parse error: {e}")
        
        # Include SQL query if available
        query = result.get("query")
        if query:
            response_parts.append(f"\n*SQL used:* `{query}`")

        # Large-result artifact(s): a full-fidelity CSV was persisted to the
        # shared store. Return a REAL artifact block (download chip) alongside
        # the prose so it survives to the user (P5-1) instead of a paraphrasable
        # markdown link. The mixed [text, artifact] array is salvaged by the
        # converse layer's chip-preservation.
        artifacts = [a for a in (result.get("artifacts") or []) if isinstance(a, dict)]
        if artifacts:
            rc = artifacts[0].get("row_count")
            response_parts.append(
                "\nThe full result"
                + (f" ({rc:,} rows)" if isinstance(rc, int) else "")
                + " is available to download below — the table above is a preview, "
                + "not the complete dataset."
            )
            chips = [dict(a, type="artifact") for a in artifacts]
            return json.dumps([{"type": "text", "content": "\n".join(response_parts)}] + chips)

        return "\n".join(response_parts)

    @lc_tool
    async def query_general_agent(agent_id: int, question: str) -> str:
        """Send a question to a general (non-data) agent. Use for agents that don't query databases."""
        from command_center.orchestration.delegator import delegate_to_agent
        try:
            agent_id = int(agent_id)
        except Exception:
            return f"Error: agent_id must be an integer (got: {agent_id})."

        logger.info(f"[converse/tool] Calling general agent {agent_id}: {question}")
        result = await delegate_to_agent(
            agent_id=str(agent_id),
            question=question,
            is_data_agent=False,
            session_id=state.get("session_id", "cc-default"),
            user_context=state.get("user_context"),
        )
        if result.get("status") == "failed":
            error_text = result.get("text", "Unknown error")
            logger.warning(f"[converse/tool] General agent {agent_id} failed: {error_text}")
            return (f"⚠️ The agent (Agent #{agent_id}) could not complete the request. "
                    f"Error: {error_text}. Please try again shortly.")
        parts = [result.get("text", "No response from agent.")]
        # Files the agent produced were re-registered into the shared store.
        # Return real artifact chips (P5-1) so they render as downloads, not a
        # paraphrasable markdown link.
        artifacts = [a for a in (result.get("artifacts") or []) if isinstance(a, dict)]
        if artifacts:
            names = ", ".join(a.get("name", "file") for a in artifacts)
            parts.append(f"\nFile(s) created and available to download below: {names}.")
            chips = [dict(a, type="artifact") for a in artifacts]
            return json.dumps([{"type": "text", "content": "\n".join(parts)}] + chips)
        return "\n".join(parts)

    @lc_tool
    async def save_user_preference(preference_key: str, preference_value: str, agent_id: int = 0) -> str:
        """Save a user preference or memory. Be SPECIFIC about domain/context.
        Use this when the user asks you to remember something, set a default, or always do something.
        Do NOT use query_data_agent for preference/memory operations.
        preference_key: a short descriptive label (e.g., 'Sales Data Agent', 'Chart Style', 'Report Format')
        preference_value: what to remember — include WHAT and FOR WHAT domain (e.g., 'Use EDW Postgres Agent for sales and revenue queries')
        agent_id: if setting a preferred agent, pass its numeric agent_id here (e.g. 14)"""
        try:
            user_ctx = state.get("user_context") or {}
            user_id = user_ctx.get("user_id")
            if user_id:
                from command_center.memory.user_memory import update_preference
                value_dict = {
                    "value": preference_value,
                    "set_by_user": True,
                }
                if agent_id:
                    value_dict["agent_id"] = str(agent_id)
                update_preference(int(user_id), preference_key, value_dict)
                logger.info(f"[converse/tool] Saved preference {preference_key}={preference_value} (agent_id={agent_id}) for user {user_id}")
                return f"Saved preference: {preference_key} = {preference_value}"
            else:
                return f"Got it — I'll remember to use {preference_value} for this session. (Log in via the main app to save preferences permanently.)"
        except Exception as e:
            logger.error(f"[converse/tool] Failed to save preference: {e}")
            return f"I noted your preference but couldn't save it permanently: {e}"

    @lc_tool
    async def delegate_to_builder_agent(request: str) -> str:
        """Delegate a build/create/modify request to the Builder Agent service.
        Use this when the user wants to CREATE, MODIFY, or DELETE agents, workflows, connections,
        or other platform resources. The Builder Agent handles all platform mutations.
        NEVER pretend to create agents yourself — you MUST call this tool.
        The request should describe what to build/create/modify in detail.
        
        MULTI-TURN: The builder maintains conversation history. When the builder asks
        follow-up questions, relay them to the user. When the user answers, call this
        tool again with their answer. The builder will remember the full conversation."""
        # Platform mutations are Developer+ only.
        if not _build_allowed(state):
            return _BUILD_DENIED_MSG
        from command_center.orchestration.delegator import delegate_to_builder

        cc_session_id = state.get("session_id", "cc-default")

        # Get or create a persistent builder session ID tied to this CC session
        active = state.get("active_delegation") or {}
        builder_sid = active.get("builder_session_id") or f"cc-builder-{cc_session_id}"
        
        logger.info(f"[converse/tool] Delegating to Builder Agent (session={builder_sid}): {request[:100]}")
        try:
            result = await delegate_to_builder(
                message=request,
                session_id=cc_session_id,
                user_context=state.get("user_context"),
                timeout=120.0,
                builder_session_id=builder_sid,
            )
            # W3a (#15): surface the full result (plan/status/builder_session_id) to the
            # tool-result handler, which derives build_status from it.
            _builder_capture["result"] = result
            status = result.get("status")
            if status in ("completed", "partial") and result.get("text"):
                return result["text"]
            elif status == "failed":
                return f"Builder Agent error: {result.get('text', 'Unknown error')}. The Builder service may not be running (port 8100)."
            else:
                return "Builder Agent processed the request but returned no visible output."
        except Exception as e:
            return f"Could not reach the Builder Agent service: {str(e)}. It may not be running on port 8100."

    @lc_tool
    async def recall_all_memories() -> str:
        """Show ALL saved preferences and learned routes.
        Use when the user asks 'what do you remember?' or 'show all my preferences'."""
        try:
            user_ctx = state.get("user_context") or {}
            user_id = user_ctx.get("user_id")
            if user_id:
                uid = int(user_id)
                parts = []

                # Preferences
                from command_center.memory.user_memory import get_preferences
                prefs = get_preferences(uid)
                if prefs:
                    pref_lines = []
                    for key, val in prefs.items():
                        display = val.get("value", str(val)) if isinstance(val, dict) else str(val)
                        pref_lines.append(f"- **{key}**: {display}")
                    parts.append("**Saved preferences:**\n" + "\n".join(pref_lines))

                # Route memory stats
                from command_center.memory.route_memory import get_route_stats
                stats = get_route_stats(uid)
                if stats.get("total_routes", 0) > 0:
                    route_lines = [
                        f"- Total logged routes: {stats['total_routes']}",
                        f"- Unique query patterns: {stats['unique_canonical_forms']}",
                        f"- Average success rate: {int(stats.get('avg_success_rate', 0) * 100)}%",
                    ]
                    if stats.get("top_routes"):
                        route_lines.append(f"- Top patterns: {', '.join(stats['top_routes'])}")
                    parts.append("**Learned routes:**\n" + "\n".join(route_lines))

                if not parts:
                    return "No saved preferences or learned routes found for this user."
                return "\n\n".join(parts)
            return "No user context available — preferences require login."
        except Exception as e:
            logger.error(f"[converse/tool] Failed to recall memories: {e}")
            return f"Could not retrieve memories: {e}"

    @lc_tool
    async def forget_preference(description: str) -> str:
        """Forget/remove a saved preference or memory.
        Use when the user says 'forget that', 'remove the default agent', 'delete my chart preference'.
        description: describe what to forget (e.g., 'the default sales agent', 'dark chart theme')"""
        try:
            user_ctx = state.get("user_context") or {}
            user_id = user_ctx.get("user_id")
            if user_id:
                uid = int(user_id)
                from command_center.memory.user_memory import get_preferences, delete_preference
                prefs = get_preferences(uid)
                if not prefs:
                    return "No saved preferences found to forget."

                desc_lower = description.lower()
                # Try to find a matching preference by keyword overlap
                best_match = None
                best_score = 0
                for key in prefs:
                    key_lower = key.lower()
                    # Score: count of description words found in key
                    desc_words = set(desc_lower.split())
                    key_words = set(key_lower.split())
                    overlap = len(desc_words & key_words)
                    # Also check substring
                    if desc_lower in key_lower or key_lower in desc_lower:
                        overlap += 3
                    if overlap > best_score:
                        best_score = overlap
                        best_match = key

                if best_match and best_score >= 1:
                    delete_preference(uid, best_match)
                    logger.info(f"[converse/tool] Forgot preference: {best_match}")
                    return f"Forgotten: '{best_match}'"
                else:
                    # Show available preferences so user can be more specific
                    keys_list = ", ".join(prefs.keys())
                    return f"I couldn't find a preference matching '{description}'. Available preferences: {keys_list}"
            return "Cannot forget preferences without being logged in."
        except Exception as e:
            logger.error(f"[converse/tool] Failed to forget preference: {e}")
            return f"Could not forget preference: {e}"

    @lc_tool
    async def switch_active_agent(agent_id: int, agent_name: str) -> str:
        """Switch the active delegation to a different agent.
        Use this when the user explicitly asks to switch agents, use a different agent,
        or says something like 'use the EDW Postgres Agent instead'."""
        logger.info(f"[converse/tool] Switching active agent to {agent_name} (#{agent_id})")
        return f"✅ Switched active agent to **{agent_name}** (Agent #{agent_id}). Future data queries will be sent to this agent."

    @lc_tool
    async def export_data(format: str, name: str, description: str = "", data: str = "") -> str:
        """Export data as a downloadable file.
        Use this when the user asks to export, download, save as file, or get an Excel/CSV/PDF.

        Two modes:
        1. Pass 'data' as a JSON array of objects to export directly (for platform data like agent lists).
           Example data: '[{"ID": 1, "Name": "Sales Agent", "Type": "data"}]'
        2. Leave 'data' empty to export the most recent table/chart results from the conversation.

        Args:
            format: File format — one of: csv, excel, pdf, json, text
            name: Filename without extension (e.g. 'sales_by_region')
            description: Brief description of the file contents
            data: Optional JSON array of objects to export directly (e.g. platform agent list)
        """
        from command_center.artifacts.artifact_models import ArtifactType

        logger.info(f"[converse/tool] Export requested: format={format}, name={name}, has_inline_data={bool(data)}")

        # Map format string to ArtifactType
        format_map = {
            "csv": ArtifactType.CSV,
            "excel": ArtifactType.EXCEL,
            "pdf": ArtifactType.PDF,
            "json": ArtifactType.JSON,
            "text": ArtifactType.TEXT,
        }
        artifact_type = format_map.get(format.lower())
        if not artifact_type:
            return f"Unsupported format: {format}. Use one of: csv, excel, pdf, json, text"

        table_blocks = []
        chart_blocks = []

        # Mode 1: Inline data provided — convert to table block
        if data and data.strip():
            try:
                rows_data = json.loads(data)
                if isinstance(rows_data, list) and rows_data and isinstance(rows_data[0], dict):
                    headers = list(rows_data[0].keys())
                    rows = [[str(item.get(h, "")) for h in headers] for item in rows_data]
                    table_blocks.append({
                        "type": "table",
                        "title": name.replace("_", " ").title(),
                        "headers": headers,
                        "rows": rows,
                    })
                    logger.info(f"[converse/tool] Built table block from inline data: {len(rows)} rows, {len(headers)} cols")
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"[converse/tool] Failed to parse inline data: {e}")

        # Mode 2: No inline data — search conversation history for table/chart blocks
        if not table_blocks and not chart_blocks:
            messages_list = state.get("messages", [])
            for msg in reversed(messages_list):
                if not hasattr(msg, 'content') or not msg.content:
                    continue
                try:
                    blocks = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
                    if isinstance(blocks, list):
                        for block in blocks:
                            if isinstance(block, dict):
                                if block.get("type") == "table" and block.get("headers"):
                                    table_blocks.append(block)
                                elif block.get("type") == "image" and block.get("src", "").startswith("data:image"):
                                    chart_blocks.append(block)
                        if table_blocks or chart_blocks:
                            break
                except (json.JSONDecodeError, TypeError):
                    continue

        if not table_blocks and not chart_blocks:
            return "No table or chart data found to export. Please query some data first, or pass the data directly in the 'data' parameter."

        # Get the shared artifact manager (same instance the download route uses)
        from routes.artifacts import _get_artifact_manager
        mgr = _get_artifact_manager()

        session_id = state.get("session_id", "cc-default")
        user_ctx = state.get("user_context") or {}
        user_id = str(user_ctx.get("user_id", "anonymous"))

        try:
            if artifact_type in (ArtifactType.CSV, ArtifactType.EXCEL, ArtifactType.JSON):
                if not table_blocks:
                    return f"No tabular data found to export as {format}. Tables are needed for CSV/Excel/JSON export."

                # Use create_from_blocks for table-based exports
                meta = mgr.create_from_blocks(
                    blocks=table_blocks,
                    artifact_type=artifact_type,
                    name=name,
                    session_id=f"{user_id}/{session_id}",
                )
                if not meta:
                    return f"Failed to generate {format} file. The renderer may not be available."

            elif artifact_type == ArtifactType.PDF:
                # PDF can include both tables and text
                from command_center.renderers.pdf_renderer import render_blocks_to_pdf
                all_blocks = table_blocks + [{"type": "text", "content": description}] if description else table_blocks
                pdf_bytes = render_blocks_to_pdf(all_blocks, title=name)
                if not pdf_bytes:
                    return "Failed to generate PDF. The reportlab library may not be configured correctly."
                meta = mgr.create(name, ArtifactType.PDF, pdf_bytes, f"{user_id}/{session_id}")

            elif artifact_type == ArtifactType.TEXT:
                # Plain text export of table data
                text_parts = []
                for tb in table_blocks:
                    headers = tb.get("headers", [])
                    rows = tb.get("rows", [])
                    title = tb.get("title", "")
                    if title:
                        text_parts.append(f"=== {title} ===\n")
                    text_parts.append(" | ".join(headers))
                    text_parts.append("-" * (len(" | ".join(headers))))
                    for row in rows:
                        text_parts.append(" | ".join(str(v) for v in row))
                    text_parts.append("")
                content = "\n".join(text_parts)
                meta = mgr.create(name, ArtifactType.TEXT, content.encode("utf-8"), f"{user_id}/{session_id}")
            else:
                return f"Export format {format} is not yet supported for the current data."

            # Return artifact block info for the UI
            block = meta.to_content_block()
            block["description"] = description or f"{name} exported as {format}"
            logger.info(f"[converse/tool] Artifact created: {meta.artifact_id}, {meta.name}, {meta.size_display}")
            return json.dumps(block)

        except Exception as e:
            logger.error(f"[converse/tool] Export failed: {e}", exc_info=True)
            return f"Export failed: {str(e)}"

    @lc_tool
    async def run_generated_tool(tool_name: str, parameters: str = "{}") -> str:
        """Run a previously created custom tool by name.
        Use this when the user asks to run a tool that was previously created via 'create_tool'.
        Available generated tools are listed in the platform scan.

        Args:
            tool_name: Name of the saved tool (snake_case)
            parameters: JSON string of parameters to pass to the tool
        """
        # Executing generated/custom tool code is Developer+ only.
        if not _build_allowed(state):
            return _BUILD_DENIED_MSG
        from command_center.tools.tool_factory import get_generated_tool
        from command_center.tools.tool_sandbox import test_tool_in_sandbox

        logger.info(f"[converse/tool] Running generated tool: {tool_name}")

        tool_data = get_generated_tool(tool_name)
        if not tool_data:
            from command_center.tools.tool_factory import list_generated_tools
            available = list_generated_tools()
            names = [t.get("name", "?") for t in available]
            return f"Tool '{tool_name}' not found. Available tools: {', '.join(names) if names else 'none'}"

        config = tool_data.get("config", {})
        code = tool_data.get("code", "")

        # Platform DB tools embed {CONN:name} placeholders the platform
        # runtime substitutes with live connection strings — the chat sandbox
        # cannot resolve them, so refuse honestly instead of failing weirdly.
        if config.get("requires_platform_runtime") or "{CONN:" in code:
            return (
                f"Tool '{tool_name}' uses a platform database connection and can "
                f"only run on the platform runtime (agents/workflows) — it cannot "
                f"be executed from chat yet."
            )

        # Parse user-provided parameters
        try:
            params = json.loads(parameters) if parameters and parameters != "{}" else {}
        except json.JSONDecodeError:
            params = {}

        # Build tool spec for sandbox execution
        tool_spec = {
            "tool_name": tool_name,
            "code": code,
            "parameters": config.get("parameters", {}),
            "parameter_types": config.get("parameter_types", {}),
            "parameter_defaults": config.get("parameter_defaults", {}),
        }

        # Override test params with user-provided params
        if params:
            tool_spec["test_params"] = params

        try:
            result = await test_tool_in_sandbox(tool_spec)
            if result.get("success"):
                output = result.get("output", "Tool completed with no output.")
                logger.info(f"[converse/tool] Tool '{tool_name}' output ({len(output)} chars): {output[:200]}")
                return f"**Tool output from `{tool_name}`:**\n\n{output}"
            else:
                return f"Tool '{tool_name}' failed: {result.get('error', 'Unknown error')}"
        except Exception as e:
            logger.error(f"[converse/tool] Generated tool execution failed: {e}")
            return f"Error running tool '{tool_name}': {str(e)}"

    @lc_tool
    async def manipulate_pdf(
        file_id: str,
        operation: str,
        pages: str = "all",
        degrees: int = 90,
    ) -> str:
        """Split, extract pages from, or rotate a PDF file the user uploaded.
        Use this INSTEAD of generating Python code when the user wants PDF
        modifications. Outputs are saved as downloadable artifacts.

        Args:
            file_id: ID of the uploaded PDF (from the attached files in context)
            operation: One of:
                - "split_all"     — one PDF per page
                - "extract_pages" — single PDF containing selected `pages`
                - "rotate"        — rotate selected `pages` by `degrees`
            pages: Page spec like "1,3,5-7" or "all" (1-indexed). Ignored for split_all.
            degrees: 90, 180, or 270 (clockwise). Only used by rotate.
        """
        from command_center_service.routes.upload import get_file_path, get_file_metadata
        import pdf_tools

        logger.info(
            f"[converse/tool] manipulate_pdf: file_id={file_id} op={operation} pages={pages}"
        )

        meta = get_file_metadata(file_id)
        if not meta:
            return f"File '{file_id}' not found. Make sure the user attached the PDF to this chat."
        if not meta.get("filename", "").lower().endswith(".pdf"):
            return f"File '{meta.get('filename')}' is not a PDF."

        path = get_file_path(file_id)
        if not path or not path.exists():
            return f"PDF file is missing from disk (id={file_id})."

        pdf_bytes = path.read_bytes()
        name_stem = path.stem.split("_", 1)[-1].rsplit(".pdf", 1)[0]

        try:
            op = (operation or "").strip().lower()
            if op in ("split_all", "split"):
                outputs = pdf_tools.split_all(pdf_bytes, name_stem)
            elif op in ("extract_pages", "extract"):
                outputs = pdf_tools.extract_pages(pdf_bytes, name_stem, pages)
            elif op == "rotate":
                outputs = pdf_tools.rotate(pdf_bytes, name_stem, int(degrees), pages)
            else:
                return (
                    f"Unknown operation '{operation}'. "
                    "Use one of: split_all, extract_pages, rotate."
                )
        except ValueError as ve:
            return f"PDF operation failed: {ve}"
        except Exception as e:
            logger.error(f"[converse/tool] manipulate_pdf error: {e}", exc_info=True)
            return f"PDF operation failed: {e}"

        from command_center.artifacts.artifact_models import ArtifactType
        from routes.artifacts import _get_artifact_manager

        mgr = _get_artifact_manager()
        session_id = state.get("session_id", "cc-default")
        user_ctx = state.get("user_context") or {}
        user_id = str(user_ctx.get("user_id", "anonymous"))
        scope = f"{user_id}/{session_id}"

        blocks = []
        for fname, fbytes in outputs:
            artifact_meta = mgr.create(fname, ArtifactType.PDF, fbytes, scope)
            block = artifact_meta.to_content_block()
            block["description"] = f"{fname} ({artifact_meta.size_display})"
            blocks.append(block)

        logger.info(
            f"[converse/tool] manipulate_pdf produced {len(blocks)} artifact(s) for {scope}"
        )
        return json.dumps(blocks)

    @lc_tool
    async def generate_map(locations_json: str, title: str = "Map") -> str:
        """Generate an interactive map from location data. Supports two modes:

        MODE 1 - Point markers (cities, stores, etc.):
            locations_json: JSON array with 'lat' and 'lng' for each point.
            Example: [{"lat": 40.7, "lng": -74.0, "label": "New York", "popup": "Sales: $1M"}]

        MODE 2 - Choropleth / region shading (states, regions):
            locations_json: JSON object with "regions" array. Each region has 'name' (US state name) and 'value' (numeric).
            The map will shade each state by its value using a color gradient.
            For named regions like "Northeast", expand them to individual states.
            Example: {"regions": [{"name": "California", "value": 5000000, "label": "CA: $5M"},
                                  {"name": "Texas", "value": 3500000, "label": "TX: $3.5M"}]}

        You can also combine both: {"markers": [...], "regions": [...]}

        Args:
            locations_json: JSON — either an array of marker objects (Mode 1) or an object with "regions" and/or "markers" (Mode 2)
            title: Map title
        """
        try:
            data = json.loads(locations_json)
        except json.JSONDecodeError:
            return "Error: locations_json must be valid JSON"

        # Normalize: support both array (markers only) and object (markers + regions)
        if isinstance(data, list):
            marker_data = data
            region_data = []
        elif isinstance(data, dict):
            marker_data = data.get("markers", [])
            region_data = data.get("regions", [])
        else:
            return "Error: locations_json must be a JSON array or object"

        # Process markers
        markers = []
        lats, lngs = [], []
        for loc in marker_data:
            lat = loc.get("lat")
            lng = loc.get("lng")
            if lat is None or lng is None:
                continue
            lats.append(float(lat))
            lngs.append(float(lng))
            markers.append({
                "lat": float(lat),
                "lng": float(lng),
                "label": loc.get("label", ""),
                "popup": loc.get("popup", loc.get("label", "")),
            })

        # Process regions (for choropleth)
        regions = []
        for r in region_data:
            name = r.get("name", "").strip()
            value = r.get("value", 0)
            label = r.get("label", f"{name}: {value}")
            if name:
                regions.append({"name": name, "value": value, "label": label})

        if not markers and not regions:
            return "Error: No valid markers or regions found"

        # Calculate center and zoom
        if markers:
            center_lat = sum(lats) / len(lats)
            center_lng = sum(lngs) / len(lngs)
            lat_spread = max(lats) - min(lats) if len(lats) > 1 else 0
            lng_spread = max(lngs) - min(lngs) if len(lngs) > 1 else 0
            spread = max(lat_spread, lng_spread)
            if spread > 40: zoom = 3
            elif spread > 20: zoom = 4
            elif spread > 10: zoom = 5
            elif spread > 5: zoom = 6
            elif spread > 2: zoom = 7
            elif spread > 0.5: zoom = 9
            else: zoom = 11
        elif regions:
            # US-centered for state data
            center_lat, center_lng = 39.8, -98.5
            zoom = 4
        else:
            center_lat, center_lng = 0, 0
            zoom = 2

        # Build map block
        map_block = {
            "type": "map",
            "title": title,
            "center": [center_lat, center_lng],
            "zoom": zoom,
        }
        if markers:
            map_block["markers"] = markers
        if regions:
            map_block["regions"] = regions

        parts = []
        if markers: parts.append(f"{len(markers)} markers")
        if regions: parts.append(f"{len(regions)} regions (choropleth)")
        logger.info(f"[converse/tool] Map generated: {', '.join(parts)}, center=[{center_lat:.2f}, {center_lng:.2f}]")
        return json.dumps([map_block])

    @lc_tool
    async def generate_image(prompt: str, size: str = "1024x1024") -> str:
        """Generate an image from a text description using DALL-E.
        Use this when the user asks to create, generate, draw, or make an image/picture/illustration.

        Args:
            prompt: Detailed description of the image to generate
            size: Image size - one of: 1024x1024, 1024x1792, 1792x1024
        """
        from cc_config import IMAGE_GENERATION_ENABLED, CC_IMAGE_MODEL
        if not IMAGE_GENERATION_ENABLED:
            return "Image generation is not enabled for this instance. Contact your administrator to enable it."

        import openai
        import os
        from api_keys_config import get_active_openai_key
        from command_center_service.graph.image_params import build_image_generate_kwargs
        import config as cfg

        api_key = get_active_openai_key() or getattr(cfg, 'OPENAI_API_KEY', None) or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return "Image generation is not available — no OpenAI API key configured."

        # Build model-appropriate kwargs. Different OpenAI image models accept
        # different parameters — see image_params.py for the details. Size will
        # be normalized to a valid value for the chosen model family.
        gen_kwargs = build_image_generate_kwargs(CC_IMAGE_MODEL, prompt, size)
        logger.info(
            f"[converse/tool] Generating image: model={CC_IMAGE_MODEL}, "
            f"prompt='{prompt[:80]}...', size={gen_kwargs['size']}"
        )

        try:
            client = openai.OpenAI(api_key=api_key)
            response = client.images.generate(**gen_kwargs)

            image_data = response.data[0]
            b64 = image_data.b64_json
            revised_prompt = getattr(image_data, "revised_prompt", prompt)

            # Return as an image block
            block = {
                "type": "image",
                "src": f"data:image/png;base64,{b64}",
                "alt": revised_prompt[:200] if revised_prompt else prompt[:200],
            }
            logger.info(f"[converse/tool] Image generated successfully ({len(b64)} chars base64)")
            return json.dumps([block])

        except openai.BadRequestError as e:
            logger.warning(f"[converse/tool] {CC_IMAGE_MODEL} rejected prompt: {e}")
            return f"The image model ({CC_IMAGE_MODEL}) couldn't generate that image: {str(e)}"
        except Exception as e:
            logger.error(f"[converse/tool] Image generation failed: {e}")
            return f"Image generation failed: {str(e)}"

    @lc_tool
    async def search_web(query: str, num_results: int = 5) -> str:
        """Search the internet for current information, news, weather, events, or any real-time data.
        Use this when the user asks about:
        - Current events, news, or real-time information
        - Weather conditions or forecasts
        - Recent developments, releases, or announcements
        - Facts you're not confident about or that may have changed
        - Anything that requires up-to-date information beyond your training data

        Args:
            query: The search query string — be specific for better results
            num_results: Number of search results to return (1-10, default 5)
        """
        logger.info(f"[converse/tool] Web search: query='{query}', num_results={num_results}")
        try:
            import os, requests as _requests

            api_key = os.environ.get("TAVILY_API_KEY", "")
            if not api_key:
                return "Web search is not configured — no TAVILY_API_KEY found in environment."

            num_results = min(max(num_results, 1), 10)

            # Call Tavily API directly (avoids heavy main-app imports)
            resp = _requests.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"query": query, "include_answer": "basic", "max_results": num_results},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            ai_answer = data.get("answer", "")
            results = [
                {"title": r.get("title", ""), "link": r.get("url", ""), "snippet": r.get("content", "")}
                for r in data.get("results", [])[:num_results]
            ]

            # Build a formatted response
            parts = []
            if ai_answer:
                parts.append(f"**Summary:** {ai_answer}")

            if results:
                parts.append("\n**Sources:**")
                for i, r in enumerate(results[:5], 1):
                    title = r.get("title", "Untitled")
                    link = r.get("link", "")
                    snippet = r.get("snippet", "")
                    parts.append(f"{i}. **{title}**")
                    if snippet:
                        parts.append(f"   {snippet[:200]}")
                    if link:
                        parts.append(f"   🔗 {link}")

            if not parts:
                return "No search results found for that query. Try rephrasing."

            response = "\n".join(parts)
            logger.info(f"[converse/tool] Web search returned: ai_answer={len(ai_answer)} chars, {len(results)} results")
            return response

        except Exception as e:
            logger.error(f"[converse/tool] Web search failed: {e}", exc_info=True)
            return f"Web search failed: {str(e)}. The search service may not be configured."

    @lc_tool
    async def search_documents(question: str) -> str:
        """Search the platform's document repository using AI-driven multi-strategy search.
        Use this when the user asks about:
        - Finding documents, files, records, or reports in the document repository
        - Looking up information stored in documents (contracts, invoices, policies, manuals, etc.)
        - Questions that require searching through document content
        - 'Find me documents about X', 'What documents mention Y', 'Search for Z'

        The tool automatically determines the best search strategy (semantic, field-based,
        hybrid, or wide-net) and returns matching documents with relevant content.

        Args:
            question: The user's natural language question — pass it directly, the tool handles query analysis internally
        """
        import httpx as _httpx
        from cc_config import get_base_url, AI_HUB_API_KEY

        logger.info(f"[converse/tool] Document search: question='{question[:100]}'")
        try:
            url = f"{get_base_url()}/api/internal/document-search"
            headers = {
                "X-API-Key": AI_HUB_API_KEY,
                "Content-Type": "application/json",
                "Connection": "close",
            }

            async with _httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, json={"question": question}, headers=headers)

            if resp.status_code != 200:
                error_text = resp.text[:500]
                logger.warning(f"[converse/tool] Document search returned {resp.status_code}: {error_text}")
                return f"Document search failed (HTTP {resp.status_code}). The service may be temporarily unavailable."

            data = resp.json()
            if data.get("status") == "error":
                return f"Document search error: {data.get('message', 'Unknown error')}"

            results = data.get("results", {})
            if isinstance(results, str):
                import json as _json
                try:
                    results = _json.loads(results)
                except (_json.JSONDecodeError, TypeError):
                    pass

            # Format result for the LLM — truncate if very large
            result_str = json.dumps(results, default=str) if isinstance(results, (dict, list)) else str(results)
            if len(result_str) > 50000:
                # Preserve structure but truncate the results array
                if isinstance(results, dict) and "results" in results:
                    search_meta = {k: v for k, v in results.items() if k != "results"}
                    doc_results = results.get("results", [])
                    search_meta["results_truncated"] = True
                    search_meta["total_results_returned"] = len(doc_results)
                    search_meta["results"] = doc_results[:20]
                    result_str = json.dumps(search_meta, default=str)
                else:
                    result_str = result_str[:50000] + "\n... (results truncated)"

            n_results = len(results.get("results", [])) if isinstance(results, dict) else 0
            logger.info(f"[converse/tool] Document search returned {n_results} results")
            return result_str

        except _httpx.TimeoutException:
            logger.warning("[converse/tool] Document search timed out")
            return "Document search timed out. The search involves multiple AI analysis steps and may take longer than usual. Please try a more specific question."
        except Exception as e:
            logger.error(f"[converse/tool] Document search failed: {e}", exc_info=True)
            return f"Document search failed: {str(e)}"

    @lc_tool
    async def send_email(to_address: str = "", subject: str = "", message: str = "", artifact_id: str = "") -> str:
        """Send an email, optionally attaching a previously exported file.
        Use this when the user asks to email, send, or share information via email.

        To attach a file: first call export_data to create the file, then pass
        the artifact_id from the export result to this tool.

        ALL THREE fields are REQUIRED for the email to be sent: to_address (recipient),
        subject (non-empty subject line), and message (non-empty body). If the user
        did not specify a subject/body, infer reasonable ones from the surrounding
        request rather than calling the tool with blank fields.

        Args:
            to_address: Recipient email address (required, non-empty)
            subject: Email subject line (required, non-empty)
            message: Email body content (required, non-empty)
            artifact_id: Optional artifact_id from a previous export_data call to attach as a file
        """
        import os
        import re
        import base64
        import requests as _requests

        # Validate inputs — all three primary fields must be present & non-empty.
        # Defaults allow the tool to fail gracefully instead of raising a Pydantic
        # validation error when the LLM forgets a required argument.
        missing = [f for f, v in (("to_address", to_address), ("subject", subject), ("message", message)) if not v or not str(v).strip()]
        if missing:
            return (
                "Error: send_email was called without required fields: "
                f"{', '.join(missing)}. Please retry with all three — "
                "to_address (recipient), subject (non-empty), and message (non-empty)."
            )
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', to_address.strip()):
            return f"Error: '{to_address}' is not a valid email address."

        # Cloud API config
        api_url = os.environ.get('AI_HUB_API_URL', '').rstrip('/')
        api_key = os.environ.get('API_KEY', '')
        if not api_url or not api_key:
            return "Error: Email service not configured (AI_HUB_API_URL or API_KEY missing)."

        email_data = {
            'to': [to_address.strip()],
            'subject': subject,
            'body': message,
        }

        # Resolve artifact attachment if provided
        if artifact_id and artifact_id.strip():
            artifact_id = artifact_id.strip()
            try:
                from routes.artifacts import _get_artifact_manager
                mgr = _get_artifact_manager()
                meta = mgr.get_metadata(artifact_id)
                if not meta:
                    return f"Error: Artifact '{artifact_id}' not found. Export a file first with export_data."
                file_path = mgr.get_file_path(artifact_id)
                if not file_path or not file_path.exists():
                    return f"Error: Artifact file for '{artifact_id}' not found on disk."

                with open(file_path, 'rb') as f:
                    file_bytes = f.read()

                email_data['attachments'] = [{
                    'filename': meta.name,
                    'content': base64.b64encode(file_bytes).decode('utf-8'),
                    'content_type': meta.mime_type or 'application/octet-stream',
                }]
                logger.info(f"[converse/tool] Email attachment: {meta.name} ({len(file_bytes)} bytes)")
            except Exception as e:
                logger.error(f"[converse/tool] Failed to attach artifact {artifact_id}: {e}")
                return f"Error attaching file: {str(e)}"

        # Send via Cloud API
        try:
            headers = {
                'X-API-Key': api_key,
                'X-License-Key': api_key,
                'Content-Type': 'application/json',
            }
            resp = _requests.post(
                f"{api_url}/api/notifications/email",
                headers=headers,
                json=email_data,
                timeout=30,
            )
            result = resp.json()

            if result.get('success'):
                attachment_note = ""
                if email_data.get('attachments'):
                    attachment_note = f"\nAttachment: {email_data['attachments'][0]['filename']}"
                return f"Email sent successfully to {to_address}.\nSubject: {subject}{attachment_note}"
            else:
                error = result.get('error', result.get('message', 'Unknown error'))
                if result.get('blocked_by_limit'):
                    current = result.get('current_usage', 0)
                    max_allowed = result.get('max_allowed', 0)
                    return f"Email not sent: Daily limit reached ({current}/{max_allowed}). Try again tomorrow."
                return f"Failed to send email: {error}"

        except _requests.exceptions.Timeout:
            return "Email send timed out. The email service may be temporarily unavailable."
        except Exception as e:
            logger.error(f"[converse/tool] Email send failed: {e}")
            return f"Email send failed: {str(e)}"

    @lc_tool
    async def run_python(code: str) -> str:
        """Run Python code to analyze data, do calculations, or create charts/files.

        Use this for ANYTHING computational: math, statistics, parsing or
        transforming data, and especially generating charts/plots (matplotlib),
        spreadsheets (pandas/openpyxl), or other files. PREFER this over guessing
        numbers — compute them.

        The full data-science stack is available (pandas, numpy, matplotlib,
        scipy, seaborn, openpyxl). Files the user uploaded to this chat are
        already present in the working directory — open them by their filename
        (e.g. open('sales.csv') or pd.read_csv('sales.csv')). ANY file your code
        writes to the working directory (e.g. plt.savefig('chart.png'),
        df.to_excel('out.xlsx')) is automatically returned to the user as a
        downloadable artifact, and images are shown inline. Use print() for text
        results.

        Args:
            code: The Python source to execute.
        """
        if not _code_interpreter_allowed(state):
            return ("Running code requires a Developer role on this instance. "
                    "Your account doesn't have permission to do that.")
        from command_center.tools import code_interpreter as _ci
        from cc_config import CODE_INTERPRETER_PYTHON as _CI_PY, CODE_INTERPRETER_TIMEOUT as _CI_TO

        _sid = state.get("session_id", "")
        _uc = state.get("user_context") or {}
        logger.info(f"[converse/tool] run_python ({len(code or '')} chars) session={_sid}")
        try:
            res = await _ci.execute(
                code, session_id=_sid, user_context=_uc,
                python_exe=(_CI_PY or None), timeout=(_CI_TO or None),
            )
        except Exception as e:
            logger.error(f"[converse/tool] run_python failed: {e}", exc_info=True)
            return f"Code execution failed to start: {e}"

        stdout = (res.get("stdout") or "").strip()
        stderr = (res.get("stderr") or "").strip()
        out_blocks = res.get("blocks") or []  # image/artifact blocks from generated files

        # Failure paths (no output files) → return a plain string so the LLM
        # relays it as normal assistant text.
        if res.get("timed_out"):
            return f"The code timed out: {stderr or 'execution exceeded the time limit.'}"
        if res.get("returncode", 0) != 0:
            tail = "\n".join(stderr.splitlines()[-15:]) if stderr else "Unknown error."
            msg = f"The code raised an error:\n```\n{tail}\n```"
            if stdout:
                msg = f"Output before the error:\n```\n{stdout}\n```\n\n" + msg
            return msg

        # Success WITH generated files (chart/CSV/xlsx): return ONLY direct-render
        # block types (image/artifact) as a JSON list so converse renders them
        # inline. converse requires EVERY element of a list to be a direct-block
        # type ("image"/"artifact"/...), so we must NOT mix in a "text" block —
        # the converse layer auto-prepends a short intro for images. Any stdout
        # is logged but not shown when files are produced (the file IS the answer).
        if out_blocks:
            if stdout:
                logger.info(f"[converse/tool] run_python stdout (with {len(out_blocks)} output block(s)): {stdout[:200]}")
            return json.dumps(out_blocks)

        # Success, no files → plain stdout (or a friendly note) as a string.
        if stdout:
            return f"```\n{stdout}\n```"
        return "Code ran successfully (no output)."

    @lc_tool
    async def read_artifact(artifact_id: str, max_rows: int = 200) -> str:
        """Read the CONTENTS of a file/artifact into the conversation so you can
        reason over it — e.g. a CSV a data agent or another agent produced, or a
        file you made earlier with export_data / run_python.

        Use this when you need to SEE what's inside a produced file (inspect
        rows, summarize, answer questions about it). For heavy computation over
        a large file, prefer run_python (the artifact is available there by its
        filename). Binary files (xlsx, pdf, docx) can't be shown as text — use
        run_python or attach them to email instead.

        Args:
            artifact_id: The artifact id (from a download link / prior tool result).
            max_rows: For CSV/tabular text, cap the rows returned (default 200).
        """
        aid = (artifact_id or "").strip()
        if not aid:
            return "Error: artifact_id is required."
        try:
            from routes.artifacts import _get_artifact_manager, _artifact_accessible_to
        except Exception as e:
            return f"Error: artifact store unavailable ({e})."

        mgr = _get_artifact_manager()
        meta = mgr.get_metadata(aid)
        if not meta:
            return f"Error: artifact '{aid}' not found."

        # Ownership — same gate the download route enforces.
        uc = state.get("user_context") or {}
        try:
            _role = int(uc.get("role") or 0)
        except (TypeError, ValueError):
            _role = 0
        if not _artifact_accessible_to(meta, uc.get("user_id"), uc.get("tenant_id"), _role):
            return f"Error: you don't have access to artifact '{aid}'."

        path = mgr.get_file_path(aid)
        if not path or not path.exists():
            return f"Error: artifact file for '{aid}' is missing on disk."

        atype = getattr(meta.artifact_type, "value", str(meta.artifact_type))
        _TEXTUAL = {"csv", "text", "json"}
        if atype not in _TEXTUAL:
            rc = f", {meta.row_count:,} rows" if getattr(meta, "row_count", None) else ""
            return (f"'{meta.name}' is a {atype} file ({meta.size_display}{rc}) — binary, "
                    f"so it can't be shown as text. Use run_python to process it "
                    f"(it's available as '{meta.name}' in the working directory), "
                    f"or attach it to an email with send_email(artifact_id='{aid}').")

        _MAX_CHARS = 20000
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8-sig", errors="replace")
        except Exception as e:
            return f"Error reading artifact '{aid}': {e}"

        header = f"Contents of '{meta.name}' ({atype}, {meta.size_display}):\n"
        if atype == "csv":
            try:
                cap = int(max_rows)
            except (TypeError, ValueError):
                cap = 200
            cap = max(1, min(cap, 2000))
            lines = text.splitlines()
            body = "\n".join(lines[:cap + 1])  # +1 for the header row
            total = len(lines) - 1 if lines else 0
            note = ""
            if total > cap:
                note = (f"\n\n[Showing first {cap} of {total} data rows. "
                        f"Use run_python on '{meta.name}' for the full file.]")
            out = header + "```\n" + body[:_MAX_CHARS] + "\n```" + note
            return out
        # text / json
        truncated = len(text) > _MAX_CHARS
        return header + "```\n" + text[:_MAX_CHARS] + "\n```" + (
            "\n\n[Truncated — file is larger; use run_python for the full content.]" if truncated else "")

    async def _deliver_portal_result(res, _uid, _sid, _uc):
        """A finished auto-run manifest -> download chips (+ a Save-as-workflow button), or an
        honest 'no file' message. Shared by fetch_from_portal and check_portal_download."""
        from command_center.tools import portal_fetch as _pf
        blocks = _pf._register_artifacts(res.get("files") or [], _sid, _uc)
        if blocks:
            draft = res.get("draft_workflow")
            if draft and draft.get("steps"):
                try:
                    from command_center.tools import portal_workflows as _wf
                    from cc_config import get_base_url
                    _saved = await asyncio.to_thread(
                        _wf.save_workflow, _uid, draft.get("name") or "Recorded portal run",
                        draft["steps"], None, draft.get("start_url"), draft.get("goal"))
                    blocks = list(blocks) + [{
                        "type": "action",
                        "action": "open_url",
                        "icon": "route",
                        "url": f"{get_base_url()}/portal-workflows?load={_saved['slug']}",
                        "label": "Save as workflow",
                        "hint": "Edit these recorded steps + add LLM steps for next time",
                        "cta": "Open builder",
                    }]
                except Exception as _e:
                    logger.warning(f"[converse/tool] draft workflow save failed: {_e}")
            return json.dumps(blocks)
        if res.get("is_upload"):
            if res.get("status") == "ok":
                return ("Upload completed. " + (res.get("final_result")
                        or "The file was uploaded to the portal.")).strip()
            return (f"The upload did NOT complete: {res.get('error') or 'unknown error'}. "
                    "Tell the user the upload failed; do NOT claim the file was uploaded.")
        if res.get("status") != "ok":
            return (f"The portal task failed: {res.get('error') or 'unknown error'}. "
                    "No file was downloaded and there is no artifact to give the user.")
        return ("The portal task ran but NO file was captured (0 files downloaded), so there is "
                "no artifact or download to give the user. Tell the user plainly that the "
                "download did not complete — do NOT claim a file was delivered or invent a "
                "download link.")

    def _resolve_portal_upload_file(file, session_id, user_context):
        """Resolve a file the user wants to UPLOAD to a portal into an absolute server path.
        Order: (1) a file the user attached to THIS chat (ownership-scoped, the same check
        sftp_upload / the attachment path use); (2) a path on this machine the environment can
        read — no app-level allowlist, the OS governs access on this on-prem install. Returns
        (path, None) on success or (None, reason) so the caller can tell the user what to do."""
        import os as _os
        f = (file or "").strip()
        if not f:
            return (None, "no file specified")
        uc = user_context or {}
        try:
            from routes.upload import (get_files_for_session, get_file_path,
                                       _file_is_accessible_to)
            uid = uc.get("user_id")
            tid = uc.get("tenant_id")
            try:
                role = int(uc.get("role") or 0)
            except (TypeError, ValueError):
                role = 0
            want = f.lower()
            for meta in (get_files_for_session(session_id) or []):
                if (meta.get("filename") or "").strip().lower() != want:
                    continue
                if uid is not None and not _file_is_accessible_to(meta, uid, tid, role):
                    continue
                fid = meta.get("file_id")
                path = get_file_path(fid) if fid else None
                if path and _os.path.isfile(str(path)):
                    logger.info(f"[portal upload] resolved chat attachment {f!r} -> {path}")
                    return (str(path), None)
        except Exception as e:
            logger.warning(f"[converse/tool] portal upload attachment resolve failed: {e}")
        try:
            p = _os.path.abspath(_os.path.expanduser(f))
            if _os.path.isfile(p):
                logger.info(f"[portal upload] resolved server path {f!r} -> {p}")
                return (p, None)
            logger.info(f"[portal upload] server path NOT a readable file: {f!r} -> {p!r}")
        except Exception as _e:
            logger.warning(f"[portal upload] path check failed for {f!r}: {_e}")
        return (None, f"I couldn't find '{f}'. Attach it to this chat, or give the full path "
                "to a file on this machine that I can read, and I'll upload it.")

    @lc_tool
    async def fetch_from_portal(portal_name: str, task: str, start_url: str = "",
                               username: str = "", password: str = "", totp: str = "",
                               file: str = "") -> str:
        """Log into a web portal and DOWNLOAD files from it, or UPLOAD a file to it (RPA, ad-hoc).
        Three ways to call it:

        1. AD-HOC (first time): the user gives a URL and a login in chat. Pass portal_name,
           start_url, task, username, password (and totp if given). Log in and act right
           away - do NOT refuse or stall.
        2. SAVED portal: the user saved this portal before. Pass just portal_name and task
           (omit start_url/username/password) - the URL and credentials resolve automatically;
           the user does NOT need to share the login again.
        3. After an ad-hoc run, OFFER to save the portal with save_portal so #2 works next time.

        UPLOAD: to upload a file, pass `file` (the name of a file the user attached to this chat,
        or a full path on this machine) and describe the upload in `task` (e.g. 'go to the Upload
        page and upload the file'). This drives the browser to attach the file to the page's file
        input - it works ad-hoc on any portal, no pre-recorded workflow needed. Downloaded files
        come back as downloadable artifacts.

        Args:
            portal_name: a short name for the portal (e.g. 'acme'); used to find saved creds.
            task: what to do once logged in, e.g. 'download my latest receipts' or 'upload the file'.
            start_url: the login URL. Required for an ad-hoc run; optional if the portal is saved.
            username: login username - ONLY for an ad-hoc first run the user typed in chat.
            password: login password - ONLY for an ad-hoc first run.
            totp: TOTP 2FA shared secret, if the user provides one (optional).
            file: a file to UPLOAD - the name of a file attached to this chat, or a server path.
                  Omit for download-only tasks.
        """
        if not _portal_fetch_allowed(state):
            return ("Pulling files from web portals requires a Developer role on this instance. "
                    "Your account doesn't have permission to do that.")
        from command_center.tools import portal_fetch as _pf
        from command_center.tools import portal_registry as _reg
        _sid = state.get("session_id", "")
        _uc = state.get("user_context") or {}
        _uid = _uc.get("user_id")

        upload_files = None
        if file:
            _p, _reason = _resolve_portal_upload_file(file, _sid, _uc)
            if not _p:
                return _reason
            upload_files = [_p]

        entry = _reg.lookup_portal(_uid, portal_name) if portal_name else None
        eff_url = start_url or (entry or {}).get("url") or ""
        inline = None
        overrides = None
        if username and password:
            inline = {"username": username, "password": password, "totp": totp or ""}
        elif entry:
            overrides = {"username_secret": entry.get("username_secret"),
                         "password_secret": entry.get("password_secret"),
                         "totp_secret": entry.get("totp_secret")}
        if not eff_url:
            return (f"I don't have a login URL for '{portal_name}'. Share the portal's URL "
                    "(and your login, unless it's already saved) and I'll do it.")
        _mode = "inline" if inline else ("saved" if overrides else "legacy")
        logger.info(f"[converse/tool] fetch_from_portal portal={portal_name} url={eff_url} "
                    f"mode={_mode} session={_sid}")

        # Start the run, then poll — surfacing progress and a take-over chip if it needs a human.
        start = await asyncio.to_thread(
            _pf.start_portal_fetch, portal_name, eff_url, task, _sid, _uc, overrides, inline,
            upload_files=upload_files)
        if start.get("error"):
            return f"I couldn't start the portal run: {start['error']}"
        run_id = start.get("run_id")
        if not run_id:
            return "I couldn't start the portal run (no run id returned)."

        try:
            from graph.progress import get_queue as _get_pq
            _pq = _get_pq(_sid)
        except Exception:
            _pq = None

        async def _say(phase: str, message: str):
            if _pq is not None:
                try:
                    await _pq.emit("status", {"phase": phase, "message": message})
                except Exception:
                    return None
            return None

        async def _say_blocks(blocks: list):
            if _pq is not None:
                try:
                    await _pq.emit("status", {"blocks": blocks})
                except Exception:
                    return None
            return None

        import time as _time
        # Wait this long before the portal asks for a human; then extend for the take-over.
        _no_human_budget = int(os.getenv("PORTAL_FETCH_WAIT_SECONDS", "180"))
        _human_budget = int(os.getenv("PORTAL_TAKEOVER_WAIT_SECONDS", "900"))
        await _say("portal", f"Opening {portal_name or 'the portal'} and signing in…")
        deadline = _time.time() + _no_human_budget
        announced = False
        last_beat = 0.0
        seen_alive = False
        gone_strikes = 0
        res = {}
        while _time.time() < deadline:
            res = await asyncio.to_thread(_pf.get_portal_result, run_id, 15)
            if res.get("done"):
                break
            _err = str(res.get("error") or "")
            if "404" in _err or "no such run" in _err.lower():
                gone_strikes += 1
                # The run vanished. Tolerate a few transient misses (the worker may be
                # registering it); only give up after several consecutive strikes.
                if gone_strikes >= 5:
                    return ("That portal run is no longer active (the browser service may have "
                            "restarted). Ask me to fetch from the portal again and I'll retry.")
            else:
                if not res.get("error"):
                    seen_alive = True
                gone_strikes = 0
            now = _time.time()
            if res.get("needs_human"):
                if not announced:
                    announced = True
                    deadline = now + _human_budget
                    link = _pf.cobrowse_link(run_id)
                    reason = res.get("reason") or "a verification / login step"
                    await _say_blocks([
                        {"type": "text",
                         "text": f"🔐 **This portal needs you for {reason}.** I've opened it and "
                                 "paused. Click **Take over the browser** below, complete the "
                                 "step (e.g. type the code you received), then click **Hand "
                                 "back** — I'll grab the file automatically. No need to ask me "
                                 "again."},
                        {"type": "action",
                         "action": "open_url",
                         "icon": "route",
                         "url": link,
                         "label": "Take over the browser",
                         "hint": "Opens the live portal session — finish the step, then hand back",
                         "cta": "Take over"},
                    ])
                await _say("takeover", "Waiting for you to finish the verification step in the "
                           "take-over window…")
            elif now - last_beat > 10:
                last_beat = now
                await _say("portal", "Working in the portal…")
            await asyncio.sleep(2)

        if res.get("done"):
            return await _deliver_portal_result(res, _uid, _sid, _uc)

        if res.get("needs_human"):
            link = _pf.cobrowse_link(run_id)
            return (f"The portal is waiting for you to finish the verification step. Take over "
                    f"here: {link} — then, once you've handed back, ask me to \"check the "
                    "download\" and I'll call check_portal_download to fetch the result. I do "
                    "NOT deliver it automatically. Relay this link to the user verbatim; do NOT "
                    "claim the file has downloaded or will arrive on its own.")
        # The wait loop has EXITED — the run may still be going in the browser service, but
        # nothing here will deliver its result later. Instruct the agent to be honest and to
        # route the user to check_portal_download; forbid any auto-delivery / success claim
        # (anti-silent-success: a not-yet-finished run is NOT a delivered file).
        return ("The portal run has not finished yet and NO file has been captured so far. It "
                "will NOT be delivered automatically — there is no background delivery channel. "
                "Tell the user it's still running and to ask you to \"check the download\" in a "
                "moment (you will then call check_portal_download, which returns the file if it "
                "completed or an honest failure if it did not). Do NOT say the file is "
                "downloading, will arrive on its own, or that the task succeeded — none of that "
                "is known yet.")

    @lc_tool
    async def check_portal_download() -> str:
        """Check on a portal download you started earlier — use this after the user has taken over
        for a 2FA/login step and handed back, or to see if a background run finished. Returns the
        downloaded file if it's ready, otherwise the current status."""
        if not _portal_fetch_allowed(state):
            return "Pulling files from web portals requires a Developer role on this instance."
        from command_center.tools import portal_fetch as _pf
        _sid = state.get("session_id", "")
        _uc = state.get("user_context") or {}
        _uid = _uc.get("user_id")
        run_id = _pf._LAST_AUTO_RUN.get(_sid)
        if not run_id:
            return ("I don't have a recent portal run to check. Ask me to fetch from a portal "
                    "first.")

        try:
            from graph.progress import get_queue as _get_pq
            _pq = _get_pq(_sid)
        except Exception:
            _pq = None

        async def _say(phase: str, message: str):
            if _pq is not None:
                try:
                    await _pq.emit("status", {"phase": phase, "message": message})
                except Exception:
                    return None
            return None

        async def _say_blocks(blocks: list):
            if _pq is not None:
                try:
                    await _pq.emit("status", {"blocks": blocks})
                except Exception:
                    return None
            return None

        import time as _time
        _budget = int(os.getenv("PORTAL_CHECK_WAIT_SECONDS", "240"))
        deadline = _time.time() + _budget
        announced = False
        last_beat = 0.0
        seen_alive = False
        gone_strikes = 0
        res = {}
        while _time.time() < deadline:
            res = await asyncio.to_thread(_pf.get_portal_result, run_id, 15)
            if res.get("done"):
                break
            _err = str(res.get("error") or "")
            if "404" in _err or "no such run" in _err.lower():
                gone_strikes += 1
                if gone_strikes >= 5:
                    return ("That portal run is no longer active (the browser service may have "
                            "restarted). Ask me to fetch from the portal again and I'll retry.")
            else:
                if not res.get("error"):
                    seen_alive = True
                gone_strikes = 0
            now = _time.time()
            if res.get("needs_human"):
                if not announced:
                    announced = True
                    link = _pf.cobrowse_link(run_id)
                    reason = res.get("reason") or "a verification / login step"
                    await _say_blocks([
                        {"type": "text",
                         "text": f"🔐 **Still waiting for you on {reason}.** Click **Take over "
                                 "the browser**, finish the step, then click **Hand back** — "
                                 "I'll grab the file automatically."},
                        {"type": "action",
                         "action": "open_url",
                         "icon": "route",
                         "url": link,
                         "label": "Take over the browser",
                         "hint": "Opens the live portal session — finish the step, then hand back",
                         "cta": "Take over"},
                    ])
                await _say("takeover", "Waiting for you to finish the verification step…")
            elif now - last_beat > 10:
                last_beat = now
                await _say("portal", "Checking on the portal download…")
            await asyncio.sleep(2)

        if res.get("done"):
            return await _deliver_portal_result(res, _uid, _sid, _uc)

        if res.get("needs_human"):
            link = _pf.cobrowse_link(run_id)
            return (f"Still waiting for you to take over: {link} — finish the step and click "
                    'Hand back, then say "check the download" again. Relay this link to the '
                    "user.")
        return ("Still working on it in the background — give it a little longer, then ask me "
                "to check again.")

    @lc_tool
    async def save_portal(name: str, url: str, username: str, password: str,
                         totp: str = "", allowed_domains: str = "") -> str:
        """Save a web portal and its credentials so the user doesn't have to share the login
        again. Call this after a successful ad-hoc run when the user agrees to save (or asks
        you to remember a portal). Credentials are stored ENCRYPTED on the server; only a
        reference is kept. Afterwards, fetch_from_portal works with just the portal name.

        Args:
            name: short name to remember the portal by (e.g. 'acme').
            url: the portal's login URL.
            username: login username to store.
            password: login password to store.
            totp: TOTP 2FA shared secret to store (optional).
            allowed_domains: extra comma-separated domains to permit (e.g. an SSO login host);
                usually leave blank - the portal's own domain is allowed automatically.
        """
        if not _portal_fetch_allowed(state):
            return "Saving portals requires a Developer role on this instance."
        from command_center.tools import portal_registry as _reg
        _uc = state.get("user_context") or {}
        _uid = _uc.get("user_id")
        doms = [d.strip() for d in (allowed_domains or "").split(",") if d.strip()] or None
        try:
            entry = await asyncio.to_thread(_reg.save_portal, _uid, name, url, username,
                                            password, totp or None, doms)
        except Exception as e:
            logger.error(f"[converse/tool] save_portal failed: {e}", exc_info=True)
            return f"Couldn't save the portal: {e}"
        return (f"Saved '{entry['name']}' ({entry['url']}). Next time just ask me to use "
                f"{entry['slug']} and I'll log in with the stored credentials - no need to "
                "share them again.")

    @lc_tool
    async def lookup_portal(name: str = "") -> str:
        """List the user's saved portals, or look one up by name, so you can reuse a saved
        portal without asking for the URL or login again. Returns names and URLs only
        (never credentials).

        Args:
            name: a portal name to look up; leave blank to list all saved portals.
        """
        if not _portal_fetch_allowed(state):
            return "Portal access requires a Developer role on this instance."
        from command_center.tools import portal_registry as _reg
        _uc = state.get("user_context") or {}
        _uid = _uc.get("user_id")
        if name:
            e = await asyncio.to_thread(_reg.lookup_portal, _uid, name)
            if not e:
                names = ", ".join(p["name"] for p in _reg.list_portals(_uid)) or "(none saved)"
                return f"No saved portal matches '{name}'. Saved portals: {names}."
            return (f"{e.get('name')} -> {e.get('url')} (credentials stored; ready to use - "
                    "call fetch_from_portal with just the portal name).")
        portals = await asyncio.to_thread(_reg.list_portals, _uid)
        if not portals:
            return "You have no saved portals yet."
        return "Saved portals:\n" + "\n".join(f"- {p['name']} -> {p['url']}" for p in portals)

    @lc_tool
    async def list_portal_workflows() -> str:
        """List the user's saved PORTAL workflows - recorded browser/RPA login-and-download
        sequences for the Portal Workflows feature. These are NOT the platform's regular Workflows
        (built in the Workflow Designer / Workflow Agent); do NOT use this for those. Returns
        portal-workflow names + step counts only (never credentials)."""
        if not _portal_fetch_allowed(state):
            return "Portal workflows require a Developer role on this instance."
        from command_center.tools import portal_workflows as _wf
        _uc = state.get("user_context") or {}
        _uid = _uc.get("user_id")
        wfs = await asyncio.to_thread(_wf.list_workflows, _uid)
        if not wfs:
            return ('You have no saved portal workflows yet. Record one on the Portal Workflows '
                    'page (or run a portal task in chat and choose "Save as workflow").')
        lines = []
        for w in wfs:
            cap = "uploads" if w.get("uploads") else "downloads"
            target = w.get("portal_slug") or w.get("start_url") or "—"
            goal = (w.get("goal") or "").strip()
            goal = (" — " + goal[:80] + ("…" if len(goal) > 80 else "")) if goal else ""
            last = f", last: {w['last_run_status']}" if w.get("last_run_status") else ""
            lines.append(f"- {w['name']} [{cap}] target: {target} "
                         f"({w.get('step_count', 0)} steps{last}){goal}")
        return ("Saved portal workflows (call describe_portal_workflow for one's steps):\n"
                + "\n".join(lines))

    @lc_tool
    async def describe_portal_workflow(name: str) -> str:
        """Show what a saved PORTAL workflow does, so you can decide whether it fits the user's
        request BEFORE running it. Returns its target portal/URL, goal, whether it uploads or
        downloads, and an ordered step summary (never any credentials). Use this when the user's
        intent only loosely matches a saved workflow and you want to confirm it's the right one.

        Args:
            name: the saved portal-workflow's name (use list_portal_workflows to find it).
        """
        if not _portal_fetch_allowed(state):
            return "Portal workflows require a Developer role on this instance."
        from command_center.tools import portal_workflows as _wf
        _uc = state.get("user_context") or {}
        _uid = _uc.get("user_id")
        wf = await asyncio.to_thread(_wf.get_workflow, _uid, name)
        if not wf:
            return (f"No saved portal workflow matches '{name}'. Call list_portal_workflows to "
                    "see the exact names.")
        steps = wf.get("steps") or []
        types = [s.get("type") for s in steps if isinstance(s, dict)]

        def _label(s):
            t = s.get("type")
            a = s.get("anchor") or {}
            who = a.get("text") or a.get("css") or a.get("name") or ""
            if t == "goto":
                return f"go to {s.get('url', '')}"
            if t == "login":
                return "log in"
            if t == "click":
                return f"click {who or 'element'}"
            if t == "fill":
                if s.get("value"):
                    extra = f" = {s.get('value')}"
                elif s.get("secret"):
                    extra = f" ({s.get('secret')})"
                else:
                    extra = ""
                return f"fill {who or 'field'}{extra}"
            if t == "wait":
                return "wait"
            if t == "agent":
                return f"AI step: {(s.get('prompt') or s.get('task') or '')[:60]}"
            if t == "verify":
                return "verify a file downloaded" if s.get("downloaded") else "verify"
            if t == "human":
                return "pause for a person"
            if t == "verify_code":
                return "enter a 2FA / verification code"
            if t == "upload":
                return f"upload the provided file into {who or 'the file input'}"
            return t or "?"

        cap = "uploads a file" if "upload" in types else "downloads file(s)"
        summary = "; ".join(f"{i + 1}. {_label(s)}" for i, s in enumerate(steps)) or "(no steps)"
        target = wf.get("portal_slug") or wf.get("start_url") or "—"
        # Tell the agent whether this workflow is ALREADY scheduled (and the schedule's job id), so
        # a "schedule it" request UPDATES the existing schedule instead of creating a duplicate.
        sched_line = ""
        try:
            from scheduling import schedule_logic as _sl
            for _t in await asyncio.to_thread(_sl.list_cc_schedules, _uc):
                if _t.get("kind") == "portal" and _t.get("slug") == wf.get("slug") and _t.get("job_id"):
                    sched_line = (f"\n- ALREADY SCHEDULED: {_t.get('schedule_desc')} (job #"
                                  f"{_t.get('job_id')}). Re-scheduling REPLACES this — it won't duplicate.")
                    break
        except Exception:
            pass
        return (f"Portal workflow '{wf.get('name', name)}':\n- This workflow {cap}.\n- Target: "
                f"{target}\n- Goal: {wf.get('goal') or '(none)'}\n- Last run: "
                f"{wf.get('last_run_status') or 'never run'}{sched_line}\n- Steps: {summary}")

    @lc_tool
    async def run_portal_workflow(name: str, file: str = "") -> str:
        """Run a saved PORTAL workflow - a recorded browser/RPA sequence that logs into a web
        portal to download (or upload) files - by name, returning any downloads as download chips.

        To UPLOAD with this tool, the saved workflow must contain an 'upload' step; pass `file`
        (a chat attachment name or a server path) and it's handed to that step. For a one-off
        upload to a portal that has NO saved workflow, use fetch_from_portal(file=...) instead.

        This is ONLY for Portal Workflows (the portal login-and-download feature). It is NOT the
        platform's regular Workflows (Workflow Designer / Workflow Agent), which are a different
        system you do NOT run with this tool. Use it when the user clearly means a saved
        PORTAL/download workflow, or to SCHEDULE a portal download (e.g. "every morning run the
        Acme portal workflow and email me the file") - it runs headless/unattended. Replays the
        exact saved steps; credentials resolve automatically from the linked portal (the user
        never reshares a login). If unsure of the exact saved name, call list_portal_workflows
        first. If the user just says "run workflow X" with no portal/download context, do NOT
        assume it's a portal one - ask whether they mean a portal workflow or a regular Workflow.

        Args:
            name: the saved portal-workflow's name (e.g. 'localhost-portal (recorded)').
            file: a file to UPLOAD (chat attachment name or server path), if the workflow has an
                  upload step; omit for download-only workflows.
        """
        if not _portal_fetch_allowed(state):
            return ("Running portal workflows requires a Developer role on this instance. Your "
                    "account doesn't have permission to do that.")
        from command_center.tools import portal_workflow_run as _wfr
        _sid = state.get("session_id", "")
        _uc = state.get("user_context") or {}
        _inputs = None
        if file:
            _p, _reason = _resolve_portal_upload_file(file, _sid, _uc)
            if not _p:
                return _reason
            _inputs = {"files": [_p]}
        logger.info(f"[converse/tool] run_portal_workflow name={name!r} session={_sid} "
                    f"upload={'yes' if _inputs else 'no'}")
        try:
            res = await asyncio.to_thread(_wfr.run_workflow_by_name, name, _sid, _uc, 600,
                                          inputs=_inputs)
        except Exception as e:
            logger.error(f"[converse/tool] run_portal_workflow failed: {e}", exc_info=True)
            return f"Portal workflow run failed to start: {e}"

        blocks = res.get("blocks") or []
        if blocks:
            return json.dumps(blocks)

        if res.get("is_upload"):
            if res.get("status") == "ok":
                return ("Upload completed. " + (res.get("final_result")
                        or f"The portal workflow '{name}' uploaded the file.")).strip()
            return (f"The portal workflow '{name}' upload did NOT complete: "
                    f"{res.get('error') or 'unknown error'}. Tell the user the upload failed; "
                    "do NOT claim the file was uploaded.")
        if res.get("status") != "ok":
            return (f"The portal workflow '{name}' failed: {res.get('error') or 'unknown error'}. "
                    "No file was downloaded and there is no artifact to give the user.")
        return (f"The portal workflow '{name}' ran but NO file was captured (0 files downloaded), "
                "so there is no artifact or download to give the user. Tell the user plainly that "
                "the download did not complete - do NOT claim a file was delivered, and do NOT "
                "invent a download link or a Downloads-folder/'artifact area' to retrieve it "
                "from.")

    @lc_tool
    async def schedule_portal_workflow(every_minutes: int = 0, every_hours: int = 0,
                                       every_days: int = 0, cron: str = "", name: str = "",
                                       email_after_run: bool = False,
                                       timezone: str = "", timezone_iana: str = "") -> str:
        """Schedule a PORTAL login-and-download to run automatically on a recurring cadence,
        headless/unattended on the server. Use THIS — never delegate_to_builder_agent or
        schedule_task — whenever the user wants a portal download to REPEAT (e.g. right after a
        portal fetch they say "run this every 20 minutes" or "every morning").

        ALWAYS CALL THIS TOOL to schedule or reschedule — never just describe a workflow and claim
        it's scheduled. Report ONLY what this tool returns: a real "job #N". If it returns an error
        or no job number, the schedule was NOT created — say so; never fabricate a schedule. If the
        workflow is ALREADY scheduled, calling this REPLACES the existing schedule (no duplicate) —
        so to change the time, just call it again with the new cadence.

        It schedules the portal workflow recorded from the portal fetch the user did in THIS chat
        (saving it first if needed). If there's no recorded run yet, it says so and does NOT invent
        a schedule. Provide ONE cadence: every_minutes OR every_hours OR every_days OR a cron.

        For a specific time of day use a CRON (e.g. 9am daily -> cron="0 9 * * *"), not every_days.
        Cron times are interpreted in the user's timezone:
          - Default (user named no zone): leave timezone/timezone_iana empty -> the user's browser
            timezone is used automatically.
          - User named a zone (e.g. "9am EST", "9am IST", "9am Pacific"): pass the exact word they
            used as `timezone` AND your best-guess IANA name as `timezone_iana` (e.g. timezone="IST",
            timezone_iana="Asia/Kolkata"). For ambiguous abbreviations (IST = India/Israel/Ireland)
            confirm the region with the user. Never do timezone offset math yourself.

        Args:
            every_minutes: run every N minutes (e.g. 20).
            every_hours: run every N hours.
            every_days: run every N days.
            cron: 5-field cron expression (alternative to the interval args).
            name: the saved portal-workflow name to schedule; omit to use this session's last run.
            email_after_run: if true, email the downloaded file to the owner after each run.
            timezone: the timezone word the user said for a cron time (e.g. "EST", "IST", "Pacific"
                or "UTC+5:30"); empty to use the user's browser timezone.
            timezone_iana: your best-guess IANA name for `timezone` (e.g. "Asia/Kolkata"); used as a
                validated fallback for zones not in the built-in table.
        """
        if not _portal_fetch_allowed(state):
            return ("Scheduling portal downloads requires a Developer role on this instance. "
                    "Your account doesn't have permission to do that.")
        if not _schedule_allowed(state):
            return "Scheduling recurring tasks requires a Developer role on this instance."
        _uc = state.get("user_context") or {}
        _uid = _uc.get("user_id")
        _sid = state.get("session_id", "")
        if not _uid:
            return "I can't schedule a task without a signed-in user."

        _tz_name, _tz_note = _resolve_schedule_tz(bool(cron), timezone, timezone_iana, _uc)
        if cron:
            schedule = {"type": "cron", "cron_expression": cron.strip()}
            desc = f"cron '{cron.strip()}'" + (f" ({_tz_name})" if _tz_name else "")
        elif every_minutes or every_hours or every_days:
            schedule = {"type": "interval"}
            if every_days:
                schedule["interval_days"] = int(every_days)
            if every_hours:
                schedule["interval_hours"] = int(every_hours)
            if every_minutes:
                schedule["interval_minutes"] = int(every_minutes)
            desc = "every " + ", ".join(f"{v} {u}" for v, u in (
                (every_days, "day(s)"), (every_hours, "hour(s)"), (every_minutes, "minute(s)"))
                if v)
        else:
            return ("Tell me how often to run it — every_minutes, every_hours, every_days, or a "
                    "cron expression.")

        from command_center.tools import portal_workflows as _wf
        from command_center.tools import portal_fetch as _pf

        if name:
            wf = await asyncio.to_thread(_wf.get_workflow, _uid, name)
            if not wf:
                _names = ", ".join(w.get("name", "") for w in
                                   (await asyncio.to_thread(_wf.list_workflows, _uid) or [])
                                   ) or "(none saved)"
                return (f"I couldn't find a saved portal workflow called '{name}'. Saved: "
                        f"{_names}. Run the portal fetch first, or give me the exact saved name.")
            slug, wf_name = wf.get("slug"), wf.get("name")
        else:
            run_id = _pf._LAST_AUTO_RUN.get(_sid)
            if not run_id:
                return ("I don't have a recent portal run in this chat to schedule. Run the "
                        "portal fetch once first (so I can record its steps), then ask me to "
                        "schedule it.")
            res = await asyncio.to_thread(_pf.get_portal_result, run_id, 15)
            draft = res.get("draft_workflow") if isinstance(res, dict) else None
            if not (draft and draft.get("steps")):
                return ("I can't schedule that portal run yet — the last run didn't produce a "
                        "reusable recorded workflow (it may not have completed a download). Run "
                        "the portal fetch to completion first, then ask me to schedule it.")
            try:
                saved = await asyncio.to_thread(
                    _wf.save_workflow, _uid, draft.get("name") or "Recorded portal run",
                    draft["steps"], None, draft.get("start_url"), draft.get("goal"))
            except Exception as e:
                logger.error(f"[converse/tool] schedule_portal_workflow save failed: {e}",
                             exc_info=True)
                return f"I couldn't save the portal workflow to schedule it: {e}"
            slug, wf_name = saved.get("slug"), saved.get("name")

        from scheduling import schedule_logic as _sl
        try:
            sched = await asyncio.to_thread(
                _sl.create_portal_workflow_schedule, _uc, slug, f"Portal: {wf_name}",
                schedule, desc, bool(email_after_run), _tz_name)
        except Exception as e:
            logger.error(f"[converse/tool] schedule_portal_workflow failed: {e}", exc_info=True)
            return f"Couldn't schedule the portal workflow: {e}"

        if sched.get("status") != "ok" or not sched.get("job_id"):
            return (f"I could NOT create the schedule: {sched.get('error') or 'unknown error'}. "
                    "Nothing was scheduled — do NOT tell the user it was scheduled.")
        logger.info(f"[converse/tool] schedule_portal_workflow slug={slug} "
                    f"job_id={sched['job_id']} cadence={desc} email={bool(email_after_run)} "
                    f"session={_sid}")
        _email_line = (" I'll email the downloaded file to you after each run (if a run produces "
                       "multiple files, the first is attached).") if email_after_run else ""
        _tz_prefix = (_tz_note + " ") if _tz_note else ""
        _replaced = sched.get("replaced") or []
        _replaced_line = (f" (replaced the previous schedule for this workflow)" if _replaced else "")
        _next = sched.get("next_run")
        _next_line = (f" Next run: {_next} UTC." if _next
                      else " Its next run will show in your Scheduled Tasks panel within a minute.")
        return (_tz_prefix + f"Scheduled the '{wf_name}' portal workflow to run {desc} (job #"
                f"{sched['job_id']}){_replaced_line}.{_next_line} It runs headless on the server, so "
                "it works while you're offline." + _email_line + " If a run hits a 2FA step and the "
                "portal has no saved TOTP secret, it pauses and EMAILS you a link to take over and "
                "enter the code (about a 15-minute window) — so it still works, just not fully "
                "hands-off. Save a TOTP secret on the portal for fully unattended runs.")

    @lc_tool
    async def schedule_task(task_name: str, prompt: str, cron: str = "",
                           every_hours: int = 0, every_days: int = 0, every_minutes: int = 0,
                           timezone: str = "", timezone_iana: str = "") -> str:
        """Schedule a recurring task that re-runs this Command Center agent automatically at a
        set cadence, EVEN WHEN THE USER IS OFFLINE. Each run's output is saved to the user's
        Scheduled Tasks panel with a notification.

        ALWAYS CALL THIS TOOL to schedule — never claim a task is scheduled without calling it.
        Report ONLY the real "job #N" it returns; if it returns an error or no job number, nothing
        was scheduled — say so, never fabricate.

        Provide EITHER a 5-field cron expression OR one of every_minutes/every_hours/every_days.
        Examples: weekdays at 8am -> cron="0 8 * * 1-5"; every 6 hours -> every_hours=6.

        Cron times are interpreted in the user's timezone. Default (no zone named): leave
        timezone/timezone_iana empty -> the user's browser timezone is used. If the user named a
        zone (e.g. "8am EST", "8am IST"): pass the word they used as `timezone` AND your best-guess
        IANA name as `timezone_iana` (e.g. timezone="IST", timezone_iana="Asia/Kolkata"). Confirm
        ambiguous abbreviations (IST = India/Israel/Ireland). Never do timezone offset math yourself.

        Args:
            task_name: a short label for the task (e.g. 'Daily Acme orders').
            prompt: the instruction to run each time, phrased as if the user asked it now.
            cron: 5-field cron expression (optional).
            every_hours: run every N hours (interval alternative to cron).
            every_days: run every N days.
            every_minutes: run every N minutes.
            timezone: the timezone word the user said for a cron time ("EST", "IST", "Pacific",
                "UTC+5:30"); empty to use the user's browser timezone.
            timezone_iana: your best-guess IANA name for `timezone` (e.g. "Asia/Kolkata").
        """
        if not _schedule_allowed(state):
            return "Scheduling recurring tasks requires a Developer role on this instance."
        _uc = state.get("user_context") or {}
        if not _uc.get("user_id"):
            return "I can't schedule a task without a signed-in user."
        _tz_name, _tz_note = _resolve_schedule_tz(bool(cron), timezone, timezone_iana, _uc)
        if cron:
            schedule = {"type": "cron", "cron_expression": cron.strip()}
            desc = f"cron '{cron.strip()}'" + (f" ({_tz_name})" if _tz_name else "")
        elif every_minutes or every_hours or every_days:
            schedule = {"type": "interval"}
            if every_days:
                schedule["interval_days"] = int(every_days)
            if every_hours:
                schedule["interval_hours"] = int(every_hours)
            if every_minutes:
                schedule["interval_minutes"] = int(every_minutes)
            desc = "every " + ", ".join(
                f"{v} {u}" for v, u in
                [(every_days, "day(s)"), (every_hours, "hour(s)"), (every_minutes, "minute(s)")] if v)
        else:
            return ("Tell me how often to run it — a cron expression, or "
                    "every_hours / every_days / every_minutes.")
        from scheduling import schedule_logic as _sl
        try:
            res = await asyncio.to_thread(_sl.create_cc_schedule, _uc,
                                          state.get("active_delegation") or {},
                                          task_name, prompt, schedule, desc, _tz_name)
        except Exception as e:
            logger.error(f"[converse/tool] schedule_task failed: {e}", exc_info=True)
            return f"Couldn't schedule the task: {e}"
        if res.get("status") != "ok" or not res.get("job_id"):
            return (f"I could NOT schedule the task: {res.get('error') or 'no job id returned'}. "
                    "Nothing was scheduled — do NOT tell the user it was scheduled.")
        _tz_prefix = (_tz_note + " ") if _tz_note else ""
        _next = res.get("next_run")
        _next_line = (f" Next run: {_next} UTC." if _next
                      else " Its next run will show in your Scheduled Tasks panel within a minute.")
        return (_tz_prefix + f"Scheduled '{task_name}' to run {desc} (job #{res['job_id']})."
                + _next_line + " Each run's output appears in your Scheduled Tasks panel with a "
                "notification — you don't need to be online. Ask me to 'list my scheduled tasks' "
                "anytime.")

    @lc_tool
    async def list_scheduled_tasks() -> str:
        """List this user's scheduled Command Center tasks (name, cadence, last run/status)."""
        if not _schedule_allowed(state):
            return "Scheduling requires a Developer role on this instance."
        from scheduling import schedule_logic as _sl
        tasks = await asyncio.to_thread(_sl.list_cc_schedules_with_next_run, state.get("user_context") or {})
        if not tasks:
            return "You have no scheduled tasks yet."
        lines = [f"- {t['task_name']} ({t.get('schedule_desc','?')}) [job #{t.get('job_id')}] — "
                 f"next run: {t.get('next_run') or '—'}, "
                 f"last run: {t.get('last_run') or 'never'} [{t.get('last_status') or '—'}]"
                 for t in tasks]
        return ("Your scheduled tasks (next run is in UTC):\n" + "\n".join(lines)
                + "\nTo change one, just say the new time — I'll replace it (no duplicate). "
                "To stop one, give its name or job #.")

    @lc_tool
    async def cancel_scheduled_task(task: str) -> str:
        """Cancel/stop a scheduled task by its name or id so it stops running.

        Args:
            task: the task name (or job id) to cancel.
        """
        if not _schedule_allowed(state):
            return "Scheduling requires a Developer role on this instance."
        from scheduling import schedule_logic as _sl
        res = await asyncio.to_thread(_sl.cancel_cc_schedule, state.get("user_context") or {}, task)
        if res.get("status") != "ok":
            return f"Couldn't cancel it: {res.get('error')}"
        return f"Cancelled '{res.get('task_name')}'. It won't run again."

    @lc_tool
    async def get_my_contact_info() -> str:
        """Look up the SIGNED-IN user's own contact info (name, email, phone). Use this to
        resolve "me" / "my email" — e.g. BEFORE emailing the user themselves with send_email
        ("email me a summary"). Returns only the current user's info, never anyone else's.
        Works in scheduled/unattended runs too (it only needs the user id)."""
        _uc = state.get("user_context") or {}
        _uid = _uc.get("user_id")
        if not _uid:
            return "There's no signed-in user in this context."
        # Prefer fields already on the context (scheduled runs snapshot the email in); fall
        # back to a live lookup against the platform user directory.
        email = _uc.get("email") or ""
        name = _uc.get("name") or ""
        phone = _uc.get("phone") or ""
        if not email:
            try:
                import user_lookup
                info = await asyncio.to_thread(user_lookup.get_user_contact, _uid)
            except Exception as e:
                logger.warning(f"[converse/tool] get_my_contact_info lookup failed: {e}")
                info = {}
            email = email or info.get("email", "")
            name = name or info.get("name", "")
            phone = phone or info.get("phone", "")
        if not (email or name or phone):
            return "I couldn't look up your contact info right now."
        return ("Your contact info — name: " + (name or "—") +
                ", email: " + (email or "—") + ", phone: " + (phone or "—"))

    @lc_tool
    async def sftp_list_files(host: str, username: str = "", password: str = "",
                              remote_dir: str = ".", port: int = 0,
                              protocol: str = "sftp") -> str:
        """List files in a directory on an SFTP, FTP, or FTPS server.

        Use this to see what files are on a remote server (name, size, modified,
        age) before downloading. The user must supply the server host and login,
        typically pasted into the chat. Nothing is stored — credentials are used
        only for this one call.

        Args:
            host: server hostname or IP (e.g. 'sftp.example.com').
            username: login username (omit for anonymous FTP).
            password: login password (omit for anonymous FTP).
            remote_dir: directory to list (default '.', the login directory).
            port: server port; 0 = default (22 for sftp, 21 for ftp/ftps).
            protocol: one of 'sftp' (default), 'ftp', or 'ftps'.
        """
        if not _sftp_allowed(state):
            return ("Transferring files over SFTP/FTP requires a Developer role on this "
                    "instance. Your account doesn't have permission to do that.")
        from command_center.tools import sftp_transfer as _sft
        logger.info(f"[converse/tool] sftp_list_files {protocol} host={host} dir={remote_dir}")
        res = await asyncio.to_thread(
            _sft.list_dir, host, username, password, remote_dir or ".",
            (port or None), protocol)
        if not res.get("ok"):
            return f"Could not list the remote directory: {res.get('error')}"
        return res.get("report") or "The directory is empty."

    @lc_tool
    async def sftp_download(host: str, remote_path: str, username: str = "",
                            password: str = "", port: int = 0,
                            protocol: str = "sftp") -> str:
        """Download a file from an SFTP, FTP, or FTPS server and give it to the user.

        The downloaded file is returned to the user as a downloadable artifact (a
        download chip) — you do NOT need to do anything else to deliver it. The user
        must supply the server host, login, and the remote file path. Nothing is
        stored — credentials are used only for this one call.

        Args:
            host: server hostname or IP.
            remote_path: full path to the file on the server (e.g. '/outbox/report.csv').
            username: login username (omit for anonymous FTP).
            password: login password (omit for anonymous FTP).
            port: server port; 0 = default (22 sftp, 21 ftp/ftps).
            protocol: one of 'sftp' (default), 'ftp', or 'ftps'.
        """
        if not _sftp_allowed(state):
            return ("Transferring files over SFTP/FTP requires a Developer role on this "
                    "instance. Your account doesn't have permission to do that.")
        import shutil
        import tempfile
        from command_center.tools import sftp_transfer as _sft
        from command_center.tools import portal_fetch as _pf
        _sid = state.get("session_id", "")
        _uc = state.get("user_context") or {}
        logger.info(f"[converse/tool] sftp_download {protocol} host={host} path={remote_path}")
        tmpdir = tempfile.mkdtemp(prefix="cc_sftp_")
        try:
            res = await asyncio.to_thread(
                _sft.download, host, username, password, remote_path, tmpdir,
                (port or None), protocol)
            if not res.get("ok"):
                return f"Could not download the file: {res.get('error')}"
            # Register the downloaded file as a CC artifact -> download chip
            # (same path code_interpreter/portal downloads use).
            blocks = _pf._register_artifacts([res["local_path"]], _sid, _uc)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        if blocks:
            return json.dumps(blocks)
        return ("The file was downloaded but could not be prepared as a download. Tell the "
                "user plainly that the download did not complete — do NOT invent a link.")

    @lc_tool
    async def sftp_upload(host: str, filename: str, username: str = "",
                          password: str = "", remote_dir: str = ".",
                          port: int = 0, protocol: str = "sftp") -> str:
        """Upload a file the user attached to THIS chat to an SFTP, FTP, or FTPS server.

        `filename` must be the name of a file the user uploaded to this conversation.
        The user supplies the server host, login, and destination directory. Nothing
        is stored — credentials are used only for this one call.

        Args:
            host: server hostname or IP.
            filename: name of a file the user uploaded to this chat to send.
            username: login username (omit for anonymous FTP).
            password: login password (omit for anonymous FTP).
            remote_dir: destination directory on the server (default '.').
            port: server port; 0 = default (22 sftp, 21 ftp/ftps).
            protocol: one of 'sftp' (default), 'ftp', or 'ftps'.
        """
        if not _sftp_allowed(state):
            return ("Transferring files over SFTP/FTP requires a Developer role on this "
                    "instance. Your account doesn't have permission to do that.")
        from command_center.tools import sftp_transfer as _sft
        _sid = state.get("session_id", "")
        _uc = state.get("user_context") or {}
        # Resolve the chat-uploaded file with the SAME ownership check the
        # attachment / code-interpreter paths use.
        local_path = None
        try:
            from routes.upload import (
                get_files_for_session, get_file_path, _file_is_accessible_to,
            )
            uid = _uc.get("user_id")
            tid = _uc.get("tenant_id")
            try:
                role = int(_uc.get("role") or 0)
            except (TypeError, ValueError):
                role = 0
            want = (filename or "").strip().lower()
            for meta in (get_files_for_session(_sid) or []):
                if (meta.get("filename") or "").strip().lower() != want:
                    continue
                if uid is not None and not _file_is_accessible_to(meta, uid, tid, role):
                    continue
                fid = meta.get("file_id")
                path = get_file_path(fid) if fid else None
                if path and os.path.exists(path):
                    local_path = path
                    break
        except Exception as e:
            logger.warning(f"[converse/tool] sftp_upload file resolve failed: {e}")
        if not local_path:
            return (f"I couldn't find a file named '{filename}' that you uploaded to this "
                    "chat. Please upload it here first, then ask me to send it.")
        logger.info(f"[converse/tool] sftp_upload {protocol} host={host} file={filename}")
        res = await asyncio.to_thread(
            _sft.upload, host, username, password, local_path, remote_dir or ".",
            None, (port or None), protocol)
        if not res.get("ok"):
            return f"Could not upload the file: {res.get('error')}"
        where = f"{host}:{port}" if port else host
        return (f"Uploaded '{res.get('name')}' to {protocol}://{where} "
                f"at {res.get('remote_path')}.")

    # ── Automations: persisted, versioned, schedulable AI-generated Python ──
    _AUTOMATIONS_DENIED = ("Building or running Automations requires a Developer role on this "
                           "instance. Your account doesn't have permission to do that.")

    def _am(action, payload=None, timeout=None):
        from . import automation_tools as _at
        kw = {"timeout": timeout} if timeout else {}
        return _at.manage(action, state.get("user_context") or {}, payload or {}, **kw)

    def _studio(**fields):
        """Feed the Studio panel's per-session state (pure UI hint — a failure
        here must never affect the tool's real work)."""
        try:
            import studio_state
            studio_state.update(state.get("session_id", "cc-default"), **fields)
        except Exception:
            pass

    @lc_tool
    async def list_automations() -> str:
        """List the persisted Automations (AI-generated Python solutions) on this
        platform: name, id, versions, what's promoted (live), and environment."""
        if not _automations_allowed(state):
            return _AUTOMATIONS_DENIED
        res = await asyncio.to_thread(_am, "list")
        if not res.get("ok"):
            return f"Could not list automations: {res.get('error')}"
        autos = res.get("automations") or []
        if not autos:
            return "No automations exist yet. Use create_automation to start one."
        lines = []
        for a in autos:
            live = f"v{a['pinned_version']} live" if a.get("pinned_version") else "nothing promoted"
            lines.append(f"- {a['name']} (id {a['automation_id']}): "
                         f"latest v{a['current_version']}, {live}. {a.get('description') or ''}".rstrip())
        return "\n".join(lines)

    @lc_tool
    async def create_automation(name: str, description: str = "") -> str:
        """Create a NEW Automation: a persisted, versioned Python solution that the
        platform owns and can run on a schedule (e.g. 'read PDFs, look up employees
        in the database, produce a CSV, upload it via SFTP').

        This creates the empty asset plus a dedicated Python environment. Next steps
        after creating: save_automation_code, dry_run_automation, promote_automation,
        then run or schedule it.

        Args:
            name: short unique name (e.g. 'payroll-pdf-to-sftp').
            description: one-line description of the business process.
        """
        if not _automations_allowed(state):
            return _AUTOMATIONS_DENIED
        _studio(phase="create", name=name, working=True)
        res = await asyncio.to_thread(_am, "create", {"name": name, "description": description})
        if not res.get("ok"):
            _studio(working=False, error=res.get("error"))
            return f"Could not create the automation: {res.get('error')}"
        auto = res.get("automation") or {}
        _studio(phase="create", working=False, error=None,
                automation_id=auto.get("automation_id"), name=auto.get("name"),
                environment_id=auto.get("environment_id"))
        msg = f"Created automation '{auto.get('name')}' (id {auto.get('automation_id')})."
        if auto.get("environment_id"):
            msg += f" Dedicated environment: {auto['environment_id']}."
        if res.get("warning"):
            msg += f" NOTE: {res['warning']}"
        return msg + " Now save code with save_automation_code."

    @lc_tool
    async def get_automation(automation_id: str) -> str:
        """Show one automation's full state: current code, manifest (inputs,
        connections, secrets, packages, declared outputs), versions, and what's live."""
        if not _automations_allowed(state):
            return _AUTOMATIONS_DENIED
        res = await asyncio.to_thread(_am, "get", {"automation_id": automation_id})
        if not res.get("ok"):
            return f"Could not fetch the automation: {res.get('error')}"
        a = res.get("automation") or {}
        import json as _json
        return (f"{a.get('name')} (id {a.get('automation_id')}) — latest v{a.get('current_version')}, "
                f"pinned v{a.get('pinned_version')} ({'live' if a.get('pinned_version') else 'NOT live'}), "
                f"versions: {a.get('versions')}\n\nMANIFEST:\n{_json.dumps(a.get('manifest'), indent=2)}"
                f"\n\nCODE:\n```python\n{a.get('code') or '(no code saved yet)'}\n```")

    @lc_tool
    async def save_automation_code(automation_id: str, code: str, manifest_json: str = "") -> str:
        """Save Python code (and optionally an updated manifest) as the automation's
        next immutable version. Saving does NOT make it live — dry-run, then promote.

        The code runs as a plain script in the automation's environment. It must read
        credentials via the aihub_runtime SDK, NEVER hard-code them:
            import aihub_runtime as aihub
            conn_str = aihub.connection("ERPDB")   # a platform Connection name
            sftp_url = aihub.secret("ACME_SFTP")   # a local secret name
            period   = aihub.input("period", "current")
        Any connection/secret the code uses MUST be declared in the manifest.

        HUMAN CHECKPOINT GATES: before an irreversible step (upload, delete, send,
        anything unusual), the code can PAUSE for a human decision:
            aihub.checkpoint("About to upload 1,240 rows to acme-sftp")
        The run waits until the user clicks Proceed (returns True) or Abort (the run
        stops with outcome 'aborted'). Keep the message concrete and quantified.

        Args:
            automation_id: the automation's id.
            code: the full Python script.
            manifest_json: optional JSON string replacing the manifest — keys: name,
                entrypoint, timeout_seconds, inputs [{name,type,default}], connections
                [names], secrets [names], packages [pip specs], outputs
                [{kind: file|sftp_upload|ftp_upload, path/name, remote_dir, secret,
                  verify: {min_rows|remote_listing|min_size}}].
        """
        if not _automations_allowed(state):
            return _AUTOMATIONS_DENIED
        payload = {"automation_id": automation_id, "code": code}
        manifest_obj = None
        if manifest_json.strip():
            import json as _json
            try:
                manifest_obj = _json.loads(manifest_json)
                payload["manifest"] = manifest_obj
            except ValueError as e:
                return f"manifest_json is not valid JSON: {e}"
        _studio(phase="code", automation_id=automation_id, working=True)
        res = await asyncio.to_thread(_am, "save_code", payload)
        if not res.get("ok"):
            details = res.get("details")
            _studio(working=False, error=res.get("error"))
            return ("Could not save: " + str(res.get("error"))
                    + (f" Details: {details}" if details else ""))
        _studio(phase="code", automation_id=automation_id, working=False, error=None,
                saved_version=res.get("version"), code=code[:60000],
                **({"manifest": manifest_obj} if manifest_obj else {}))
        return (f"Saved as v{res.get('version')} (not live yet). "
                f"Dry-run it with dry_run_automation, then promote_automation to make it live.")

    @lc_tool
    async def dry_run_automation(automation_id: str, inputs_json: str = "") -> str:
        """Test-run the LATEST saved code (not the promoted version) and report the
        honest outcome including declared-output verification. Always dry-run before
        promoting. Sample files attached to the version are seeded into the workdir.

        Args:
            automation_id: the automation's id.
            inputs_json: optional JSON object of input values, e.g. '{"period": "2026-07"}'.
        """
        if not _automations_allowed(state):
            return _AUTOMATIONS_DENIED
        import json as _json
        inputs = {}
        if inputs_json.strip():
            try:
                inputs = _json.loads(inputs_json)
            except ValueError as e:
                return f"inputs_json is not valid JSON: {e}"
        _studio(phase="dry_run", automation_id=automation_id, working=True)
        res = await asyncio.to_thread(_am, "dry_run",
                                      {"automation_id": automation_id, "inputs": inputs})
        from . import automation_tools as _at
        if not res.get("ok"):
            _studio(working=False, error=res.get("error"))
            return f"Dry-run failed to start: {res.get('error')}"
        _studio(phase=("confirm" if res.get("status") == "success" else "dry_run"),
                automation_id=automation_id, working=False, error=None,
                last_run={"run_id": res.get("run_id"), "status": res.get("status"),
                          "exit_code": res.get("exit_code"),
                          "verify_report": res.get("verify_report"),
                          "output_files": res.get("output_files"), "dry_run": True})
        return "DRY-RUN (latest saved version):\n" + _at.summarize_run(res)

    @lc_tool
    async def promote_automation(automation_id: str, version: int = 0) -> str:
        """Make a version LIVE: scheduled and manual runs execute the promoted
        (pinned) version only. Default promotes the latest saved version. Only
        promote after a successful dry-run the user has confirmed."""
        if not _automations_allowed(state):
            return _AUTOMATIONS_DENIED
        payload = {"automation_id": automation_id}
        if version:
            payload["version"] = version
        res = await asyncio.to_thread(_am, "promote", payload)
        if not res.get("ok"):
            return f"Could not promote: {res.get('error')}"
        _studio(phase="live", automation_id=automation_id,
                pinned_version=res.get("pinned_version"), error=None)
        return (f"v{res.get('pinned_version')} is now live. Runs and schedules execute this "
                f"version until you promote another.")

    @lc_tool
    async def run_automation(automation_id: str, inputs_json: str = "") -> str:
        """Run the LIVE (promoted) version of an automation now and report the honest
        verified outcome. If a run is already in progress it is skipped, not queued."""
        if not _automations_allowed(state):
            return _AUTOMATIONS_DENIED
        import json as _json
        inputs = {}
        if inputs_json.strip():
            try:
                inputs = _json.loads(inputs_json)
            except ValueError as e:
                return f"inputs_json is not valid JSON: {e}"
        _studio(phase="live", automation_id=automation_id, working=True)
        res = await asyncio.to_thread(_am, "run",
                                      {"automation_id": automation_id, "inputs": inputs})
        from . import automation_tools as _at
        if not res.get("ok"):
            _studio(working=False, error=res.get("error"))
            return f"Run failed to start: {res.get('error')}"
        _studio(phase="live", automation_id=automation_id, working=False, error=None,
                last_run={"run_id": res.get("run_id"), "status": res.get("status"),
                          "exit_code": res.get("exit_code"),
                          "verify_report": res.get("verify_report"),
                          "output_files": res.get("output_files"), "dry_run": False})
        return _at.summarize_run(res)

    @lc_tool
    async def get_automation_runs(automation_id: str) -> str:
        """Show an automation's recent run history: when, trigger, outcome
        (success / failed / unverified / skipped), exit code."""
        if not _automations_allowed(state):
            return _AUTOMATIONS_DENIED
        res = await asyncio.to_thread(_am, "runs", {"automation_id": automation_id})
        if not res.get("ok"):
            return f"Could not fetch runs: {res.get('error')}"
        runs = res.get("runs") or []
        if not runs:
            return "No runs yet for this automation."
        lines = []
        for r in runs[:20]:
            lines.append(f"- {r.get('started_at')} [{r.get('trigger_source')}] v{r.get('version')} "
                         f"→ {r.get('status')}"
                         + (f" (exit {r.get('exit_code')})" if r.get("exit_code") is not None else ""))
        return "\n".join(lines)

    @lc_tool
    async def schedule_automation(automation_id: str, cron_expression: str = "",
                                  every_hours: int = 0, every_days: int = 0,
                                  inputs_json: str = "") -> str:
        """Schedule the LIVE (promoted) version of an automation to run automatically.
        Requires a promoted version. GROUNDING: report ONLY the real schedule the tool
        returns — never claim something was scheduled unless this tool succeeded.

        Args:
            automation_id: the automation's id.
            cron_expression: cron like '0 6 * * *' (6:00 daily). Use this OR every_*.
            every_hours: run every N hours (interval schedule).
            every_days: run every N days (interval schedule).
            inputs_json: optional JSON object of input values used for every run.
        """
        if not _automations_allowed(state):
            return _AUTOMATIONS_DENIED
        import json as _json
        inputs = {}
        if inputs_json.strip():
            try:
                inputs = _json.loads(inputs_json)
            except ValueError as e:
                return f"inputs_json is not valid JSON: {e}"
        if cron_expression:
            schedule = {"type": "cron", "cron_expression": cron_expression}
        elif every_hours or every_days:
            schedule = {"type": "interval"}
            if every_hours:
                schedule["interval_hours"] = every_hours
            if every_days:
                schedule["interval_days"] = every_days
        else:
            return "Provide either cron_expression or every_hours/every_days."
        res = await asyncio.to_thread(_am, "schedule",
                                      {"automation_id": automation_id,
                                       "schedule": schedule, "inputs": inputs})
        if not res.get("ok"):
            return f"Could not schedule: {res.get('error')}"
        _studio(phase="live", automation_id=automation_id,
                scheduled={"job_id": res.get("scheduled_job_id"),
                           "schedule_id": res.get("schedule_id")}, error=None)
        return (f"Scheduled (job #{res.get('scheduled_job_id')}, schedule #{res.get('schedule_id')}) — "
                f"runs pinned v{res.get('pinned_version')}. {res.get('note') or ''}".strip())

    # ── Code Flows: multi-step processes = a workflow of inline Code Step nodes ──
    # Same Developer gate and family as Automations; a Code Flow is the
    # multi-step sibling (single-script → Automation; multi-step → Code Flow).
    def _cf(action, payload=None, timeout=None):
        from . import codeflow_tools as _ct
        kw = {"timeout": timeout} if timeout else {}
        return _ct.manage(action, state.get("user_context") or {}, payload or {}, **kw)

    def _cf_list(json_str, field):
        """Parse an optional JSON-array tool arg; returns (list|None, error|None)."""
        import json as _json
        if not (json_str or "").strip():
            return None, None
        try:
            val = _json.loads(json_str)
        except ValueError as e:
            return None, f"{field} is not valid JSON: {e}"
        if not isinstance(val, list):
            return None, f"{field} must be a JSON array"
        return val, None

    async def _cf_walk_timeout(name):
        """Client HTTP timeout for a synchronous walk: the dispatch runs every
        step to completion before responding, so a fixed 900s can trip while a
        multi-step flow is still (really) running server-side, misreporting it as
        unreachable and inviting a double-fire retry. Size the timeout to the sum
        of the flow's declared per-step timeouts (+margin), capped."""
        try:
            got = await asyncio.to_thread(_cf, "get", {"name": name})
            steps = ((got.get("code_flow") or {}).get("definition") or {}).get("steps") or []
            total = sum(int(s.get("timeout") or 600) + 30 for s in steps) + 120
            return max(300, min(total, 3600))
        except Exception:
            return 900

    @lc_tool
    async def list_code_flows() -> str:
        """List the Code Flows on this platform (multi-step AI-authored processes,
        each a workflow of Code Step nodes): name, how many steps, description."""
        if not _automations_allowed(state):
            return _AUTOMATIONS_DENIED
        res = await asyncio.to_thread(_cf, "list")
        if not res.get("ok"):
            return f"Could not list code flows: {res.get('error')}"
        flows = res.get("code_flows") or []
        if not flows:
            return "No code flows exist yet. Use create_code_flow to start one."
        return "\n".join(f"- {f['name']} ({f.get('step_count', 0)} step(s)): "
                         f"{f.get('description') or ''}".rstrip() for f in flows)

    @lc_tool
    async def create_code_flow(name: str, description: str = "") -> str:
        """Create a NEW Code Flow: a multi-step business process built as a workflow
        of inline Code Step nodes (LLM-authored Python), reusing the platform's
        workflow engine. Choose a Code Flow over a single Automation when the process
        has distinct stages that should route independently — e.g. 'pull invoices from
        the ERP → transform → upload via SFTP → on any failure, alert', where a failed
        step follows its 'fail' edge to a notify step.

        After creating: add_code_step for each stage, wire_steps to connect them
        (on='pass'/'fail'/'complete'), then dry_run_code_flow, and schedule_code_flow.

        Args:
            name: short unique name (e.g. 'nightly-invoice-recon').
            description: one-line description of the process.
        """
        if not _automations_allowed(state):
            return _AUTOMATIONS_DENIED
        res = await asyncio.to_thread(_cf, "create", {"name": name, "description": description})
        if not res.get("ok"):
            return f"Could not create the code flow: {res.get('error')}"
        info = res.get("code_flow") or {}
        return (f"Created code flow '{info.get('name')}' (workflow #{info.get('workflow_id')}). "
                f"Now add steps with add_code_step.")

    @lc_tool
    async def add_code_step(name: str, step_name: str, code: str,
                            connections_json: str = "", secrets_json: str = "",
                            packages_json: str = "", inputs_json: str = "",
                            outputs_json: str = "", timeout: int = 600,
                            allow_unverified: bool = False,
                            user_approved_unverified: bool = False) -> str:
        """Add one Code Step to a Code Flow. The step is inline Python run through the
        Automations runner via the read-only aihub_runtime SDK (import aihub_runtime as
        aihub): aihub.connection('NAME'), aihub.secret('NAME'), aihub.input('name'),
        aihub.log(...), and — for SQL — aihub.query('CONN', sql, params) which returns
        rows as a list of dicts. **Use aihub.query for database access — do NOT hand-roll
        pyodbc or SQLAlchemy** (passing an ODBC string to SQLAlchemy create_engine is the
        #1 code-gen failure). Example:
            for row in aihub.query('AIRDB', 'SELECT id, amount FROM expenses WHERE emp = ?', [emp_id]):
                total += row['amount']
        NEVER hard-code credentials — declare them in connections_json/secrets_json and
        the runner injects them. **INPUT NAMES MUST MATCH:** the names you read with
        aihub.input('X') must exactly equal the input names you declare in inputs_json
        (a mismatch is rejected at save). A downstream step consumes an upstream file via
        an input whose default is '${<upstream_step_id>_files[0]}'. Declare produced files in outputs_json so the step's success is
        VERIFIED (a step that declares out.csv but produces nothing is 'failed', not
        silently 'ok'). Downstream steps reference an upstream step's files with the
        input default '${<step_id>_files[0]}', where <step_id> is the id RETURNED by
        the upstream add_code_step call (a random 's…' id, not 's1'). Only string/
        scalar references cross steps reliably — pass a file PATH element like
        ${s_abc_files[0]}, not a whole ${s_abc_out} object.

        Args:
            name: the code flow's name.
            step_name: short label for this step (e.g. 'pull-invoices').
            code: the Python source for this step.
            connections_json: JSON array of platform connection names, e.g. '["ERPDB"]'.
            secrets_json: JSON array of platform secret names, e.g. '["ACME_SFTP"]'.
            packages_json: JSON array of pip packages this step needs, e.g. '["pdfplumber"]'.
            inputs_json: JSON array of input specs, e.g.
                '[{"name":"src","type":"string","default":"${s1_files[0]}"}]'.
            outputs_json: JSON array of declared outputs to verify, e.g.
                '[{"kind":"file","path":"out.csv"}]'. For an sftp_upload/ftp_upload
                output, add "remote_listing" so the upload is VERIFIED (counts as
                pass); without it the outcome is 'unverified' and the step is
                treated as failed unless allow_unverified=True.
            timeout: per-step timeout in seconds (default 600).
            allow_unverified: if True, an 'unverified' outcome (exit 0 but a
                declared output couldn't be checked) still takes the pass edge
                instead of failing the step. REQUIRES the user's explicit
                consent — see user_approved_unverified. Default False.
            user_approved_unverified: set True ONLY after the USER explicitly
                agreed, in this conversation, to skip verification for this
                step (their consent is recorded on the step). NEVER set it on
                your own initiative — the save is rejected without it when
                allow_unverified is True. A "simulated"/placeholder transfer
                step is NEVER acceptable; a step declaring an sftp/ftp output
                must contain the real transfer code (paramiko etc.) and a
                verifiable output 'name' — placeholders are rejected at save.
        """
        if not _automations_allowed(state):
            return _AUTOMATIONS_DENIED
        payload = {"name": name, "step_name": step_name, "code": code, "timeout": timeout,
                   "allow_unverified": bool(allow_unverified),
                   "unverified_consent": bool(user_approved_unverified)}
        for arg, field in ((connections_json, "connections"), (secrets_json, "secrets"),
                           (packages_json, "packages"), (inputs_json, "inputs"),
                           (outputs_json, "outputs")):
            val, err = _cf_list(arg, field)
            if err:
                return err
            if val is not None:
                payload[field] = val
        res = await asyncio.to_thread(_cf, "add_step", payload)
        if not res.get("ok"):
            return f"Could not add the step: {res.get('error')}"
        return (f"Added step '{step_name}' (id {res.get('step_id')}) to '{name}'. "
                f"Wire it with wire_steps, or add the next step.")

    @lc_tool
    async def wire_steps(name: str, from_step: str, to_step: str, on: str = "pass") -> str:
        """Connect two steps in a Code Flow with an edge (the engine's control flow).

        on='pass'  → go to to_step when from_step SUCCEEDS (the happy path).
        on='fail'  → go to to_step when from_step FAILS (route to an alert/notify step).
        on='complete' → go to to_step regardless of outcome.

        Args:
            name: the code flow's name.
            from_step: source step id (from add_code_step).
            to_step: target step id.
            on: 'pass' (default), 'fail', or 'complete'.
        """
        if not _automations_allowed(state):
            return _AUTOMATIONS_DENIED
        res = await asyncio.to_thread(_cf, "wire",
                                      {"name": name, "from_step": from_step,
                                       "to_step": to_step, "on": on})
        if not res.get("ok"):
            return f"Could not wire the steps: {res.get('error')}"
        return f"Wired {from_step} —[{on}]→ {to_step} in '{name}'."

    @lc_tool
    async def unwire_steps(name: str, from_step: str, to_step: str, on: str = "") -> str:
        """Remove an edge between two steps in a Code Flow (AIHUB-0045).

        USE THIS when you insert a step between two existing steps: after wiring
        A→NEW and NEW→B, unwire the old direct A→B edge — otherwise the flow has
        two competing 'pass' edges and the dry-run/wire will REJECT the ambiguity
        (a step may have at most one edge per outcome type).

        Args:
            name: the code flow's name.
            from_step: source step id of the edge to remove.
            to_step: target step id of the edge to remove.
            on: 'pass', 'fail', or 'complete' to remove just that edge type;
                leave empty to remove every edge between the pair.
        """
        if not _automations_allowed(state):
            return _AUTOMATIONS_DENIED
        payload = {"name": name, "from_step": from_step, "to_step": to_step}
        if (on or "").strip():
            payload["on"] = on.strip()
        res = await asyncio.to_thread(_cf, "unwire", payload)
        if not res.get("ok"):
            return f"Could not unwire the steps: {res.get('error')}"
        return f"Removed edge {from_step} → {to_step} in '{name}'."

    @lc_tool
    async def remove_code_step(name: str, step_id: str) -> str:
        """Remove a step from a Code Flow, along with every edge touching it
        (AIHUB-0045). If it was the start step, the start moves to the first
        remaining step. Re-wire around the gap afterwards if needed.

        Args:
            name: the code flow's name.
            step_id: id of the step to remove (from add_code_step / get_code_flow).
        """
        if not _automations_allowed(state):
            return _AUTOMATIONS_DENIED
        res = await asyncio.to_thread(_cf, "remove_step",
                                      {"name": name, "step_id": step_id})
        if not res.get("ok"):
            return f"Could not remove the step: {res.get('error')}"
        return f"Removed step {step_id} (and its edges) from '{name}'."

    @lc_tool
    async def update_step_code(name: str, step_id: str, code: str) -> str:
        """Replace the Python code of an existing step (the canvas is editable — use
        this to fix a step after a dry-run shows it failing). Re-runs the credential
        scan; won't accept hard-coded secrets.

        Args:
            name: the code flow's name.
            step_id: the step id to update.
            code: the new Python source.
        """
        if not _automations_allowed(state):
            return _AUTOMATIONS_DENIED
        res = await asyncio.to_thread(_cf, "update_step_code",
                                      {"name": name, "step_id": step_id, "code": code})
        if not res.get("ok"):
            return f"Could not update the step: {res.get('error')}"
        return f"Updated code for step {step_id} in '{name}'."

    @lc_tool
    async def get_code_flow(name: str) -> str:
        """Show a Code Flow's structure: each step (id, name, declared
        connections/secrets/outputs) and the edges between them."""
        if not _automations_allowed(state):
            return _AUTOMATIONS_DENIED
        res = await asyncio.to_thread(_cf, "get", {"name": name})
        if not res.get("ok"):
            return f"Could not fetch the code flow: {res.get('error')}"
        cf = res.get("code_flow") or {}
        defn = cf.get("definition") or {}
        steps = defn.get("steps") or []
        edges = defn.get("edges") or []
        if not steps:
            return f"Code flow '{name}' has no steps yet."
        lines = [f"Code flow '{name}' (workflow #{cf.get('workflow_id')}): {len(steps)} step(s)"]
        for s in steps:
            bits = []
            if s.get("connections"):
                bits.append("conn=" + ",".join(s["connections"]))
            if s.get("secrets"):
                bits.append("secret=" + ",".join(s["secrets"]))
            if s.get("outputs"):
                bits.append(f"{len(s['outputs'])} declared output(s)")
            lines.append(f"- [{s['id']}] {s.get('name')}"
                         + (f" ({'; '.join(bits)})" if bits else ""))
        for e in edges:
            lines.append(f"  {e.get('from')} —[{e.get('on', 'pass')}]→ {e.get('to')}")
        return "\n".join(lines)

    @lc_tool
    async def dry_run_code_flow(name: str) -> str:
        """Execute the Code Flow end to end RIGHT NOW (walks the graph, honoring
        pass/fail edges) and report the honest per-step outcome — files produced,
        verification results, and the stderr tail of any failing step so you can
        fix it with update_step_code.

        ⚠ THIS REALLY RUNS THE CODE with the flow's LIVE connections and secrets:
        it is NOT a sandbox. Any step that uploads, sends, deletes, or writes to an
        external system performs that side effect for real. Before running a flow
        with irreversible steps, tell the user it will execute for real (and prefer
        adding aihub.checkpoint() gates on irreversible steps). GROUNDING: report
        only what this tool returns; never claim a step succeeded unless its status
        says so.

        Args:
            name: the code flow's name.
        """
        if not _automations_allowed(state):
            return _AUTOMATIONS_DENIED
        from . import codeflow_tools as _ct
        to = await _cf_walk_timeout(name)
        res = await asyncio.to_thread(_cf, "dry_run", {"name": name}, to)
        if not res.get("ok") and res.get("status") is None:
            return f"Could not run the code flow: {res.get('error')}"
        return _ct.summarize_walk(res)

    @lc_tool
    async def run_code_flow(name: str) -> str:
        """Run the Code Flow now and report the per-step outcome. Like
        dry_run_code_flow, this EXECUTES every step for real with the flow's live
        credentials (no sandbox) — the two are the same synchronous graph walk in
        v0. The durable, scheduled path is schedule_code_flow.

        Args:
            name: the code flow's name.
        """
        if not _automations_allowed(state):
            return _AUTOMATIONS_DENIED
        from . import codeflow_tools as _ct
        to = await _cf_walk_timeout(name)
        res = await asyncio.to_thread(_cf, "run", {"name": name}, to)
        if not res.get("ok") and res.get("status") is None:
            return f"Could not run the code flow: {res.get('error')}"
        return _ct.summarize_walk(res)

    @lc_tool
    async def schedule_code_flow(name: str, cron_expression: str = "",
                                 every_hours: int = 0, every_days: int = 0) -> str:
        """Schedule a Code Flow to run automatically. It runs on the platform's
        EXISTING workflow scheduler (the flow is stored as a workflow), so no extra
        setup. GROUNDING: report only the real schedule the tool returns.

        Args:
            name: the code flow's name.
            cron_expression: cron like '0 2 * * *' (2:00 daily). Use this OR every_*.
            every_hours: run every N hours (interval schedule).
            every_days: run every N days (interval schedule).
        """
        if not _automations_allowed(state):
            return _AUTOMATIONS_DENIED
        if cron_expression:
            schedule = {"type": "cron", "cron_expression": cron_expression}
        elif every_hours or every_days:
            schedule = {"type": "interval"}
            if every_hours:
                schedule["interval_hours"] = every_hours
            if every_days:
                schedule["interval_days"] = every_days
        else:
            return "Provide either cron_expression or every_hours/every_days."
        res = await asyncio.to_thread(_cf, "schedule", {"name": name, "schedule": schedule})
        if not res.get("ok"):
            return f"Could not schedule: {res.get('error')}"
        return (f"Scheduled code flow '{name}' (job #{res.get('scheduled_job_id')}, "
                f"schedule #{res.get('schedule_id')}). {res.get('note') or ''}".strip())

    # ── Native visual-workflow tools (CC_AGENT="native" A/B agent) ──────────
    # The Code Flows pattern applied to VISUAL workflows: thin typed wrappers
    # over the deterministic manager (graph/workflow_tools.py), which persists
    # ONLY through the main app's guarded POST /save/workflow (the exact
    # chokepoint the canvas UI uses — the AIHUB-0039 kind-guard and 0016
    # validation protect this path for free) and reads truth back after every
    # save. Registered ONLY on native-impl Developer turns (bind-time gating,
    # the AIHUB-0028 F2 discipline); each tool re-checks the gate in depth.
    _WORKFLOW_DENIED = ("Building visual workflows from chat requires a Developer role on "
                        "this instance. Your account doesn't have permission to do that.")

    def _wf_json_obj(json_str, field):
        """Parse an optional JSON-object tool arg; returns (dict, error|None)."""
        import json as _json
        if not (json_str or "").strip():
            return {}, None
        try:
            val = _json.loads(json_str)
        except ValueError as e:
            return None, f"{field} is not valid JSON: {e}"
        if not isinstance(val, dict):
            return None, f"{field} must be a JSON object"
        return val, None

    async def _wf_mutate_and_save(workflow, mutate):
        """Load → deterministic surgery → save via the guarded chokepoint →
        TRUE read-back. Returns (result_message, mutation_result_dict)."""
        from . import workflow_tools as _wt
        got = await asyncio.to_thread(_wt.get_definition, workflow)
        if not got.get("ok"):
            return f"Could not load workflow '{workflow}': {got.get('error')}", None
        res = mutate(got["definition"])
        if not res.get("ok"):
            return f"❌ {res.get('error')}", None
        saved = await asyncio.to_thread(_wt.save_definition, got["name"], got["definition"])
        msg = _wt.summarize_save(got["name"], saved)
        if res.get("note"):
            msg += f" ({res['note']})"
        return msg, res

    @lc_tool
    async def list_workflows() -> str:
        """List the platform's visual workflows: id, name. Code Flow rows are
        marked — those are edited with the code-flow tools, never these."""
        if not _workflow_tools_allowed(state):
            return _WORKFLOW_DENIED
        from . import workflow_tools as _wt
        res = await asyncio.to_thread(_wt.list_rows)
        if not res.get("ok"):
            return f"Could not list workflows: {res.get('error')}"
        rows = res.get("rows") or []
        if not rows:
            return "No workflows exist yet. Use create_workflow to start one."
        lines = []
        for r in rows:
            tag = "  [CODE FLOW — use the code-flow tools]" if r.get("kind") == "code_flow" else ""
            lines.append(f"- {r['name']} (id {r['id']}){tag}")
        return "\n".join(lines)

    @lc_tool
    async def get_workflow_structure(workflow: str) -> str:
        """Show a visual workflow's REAL persisted structure: every node (id,
        type, label, start flag, config keys), every edge, variables, and any
        structural issues. This read-back is the ground truth — describe the
        workflow to the user from THIS, never from memory.

        Args:
            workflow: the workflow's exact name or numeric id.
        """
        if not _workflow_tools_allowed(state):
            return _WORKFLOW_DENIED
        from . import workflow_tools as _wt
        got = await asyncio.to_thread(_wt.get_definition, workflow)
        if not got.get("ok"):
            return f"Could not load the workflow: {got.get('error')}"
        return _wt.summarize_structure(got["id"], got["name"], got["definition"])

    @lc_tool
    async def create_workflow(name: str, description: str = "") -> str:
        """Create a NEW empty visual workflow (saved as a draft until it has a
        wired, valid node graph). Then add nodes with add_workflow_node and
        connect them with wire_workflow_nodes.

        Args:
            name: unique workflow name (letters/digits/space/_-., e.g. 'invoice-review').
            description: one-line description.
        """
        if not _workflow_tools_allowed(state):
            return _WORKFLOW_DENIED
        from . import workflow_tools as _wt
        existing = await asyncio.to_thread(_wt.resolve, name)
        if existing.get("ok"):
            kind = " (a Code Flow — pick a different name; code flows are off-limits here)" \
                if existing.get("kind") == "code_flow" else ""
            return (f"A workflow named '{existing['name']}' already exists (id {existing['id']}){kind}. "
                    f"Edit it, or choose a different name.")
        definition = {"nodes": [], "connections": [], "variables": {}}
        if description:
            definition["description"] = description
        saved = await asyncio.to_thread(_wt.save_definition, name, definition)
        if not saved.get("ok"):
            return f"❌ Could not create the workflow: {saved.get('error')}"
        return (f"Created workflow '{name}' (id {saved.get('workflow_id')}) as an empty draft. "
                f"Now add nodes with add_workflow_node — the first node becomes the start node.")

    @lc_tool
    async def add_workflow_node(workflow: str, node_type: str, label: str = "",
                                config_json: str = "", make_start: bool = False) -> str:
        """Add ONE node to a visual workflow and save. The node type MUST be one
        of the catalog types in your system prompt (Database, AI Action, AI
        Extract, Document, Loop, End Loop, Conditional, Human Approval, Alert,
        Folder Selector, File, Set Variable, Execute Application, Excel Export,
        Portal, Integration, Automation, …) — there is NO node for SFTP/FTP/API
        pushes or custom Python; offer a Code Flow or an Automation node instead
        of silently substituting. Configure per the catalog: e.g. a Database
        node needs {"connection": "<numeric id as string>", "query": "...",
        "saveToVariable": true, "outputVariable": "rows"}.

        Args:
            workflow: the workflow's exact name or numeric id.
            node_type: a catalog node type (exact name, case-insensitive).
            label: short human label shown on the canvas.
            config_json: JSON object with the node's config fields.
            make_start: set True to make this the start node.
        Returns the new node's id — use it in wire_workflow_nodes.
        """
        if not _workflow_tools_allowed(state):
            return _WORKFLOW_DENIED
        from . import workflow_tools as _wt
        cfg, err = _wf_json_obj(config_json, "config_json")
        if err:
            return err
        _out = {}
        def _mut(definition):
            r = _wt.add_node(definition, node_type, label, cfg,
                             user_context=state.get("user_context"), make_start=make_start)
            _out.update(r)
            return r
        msg, res = await _wf_mutate_and_save(workflow, _mut)
        if res and _out.get("node_id"):
            msg = f"Added node [{_out['node_id']}] {_out.get('type')}. " + msg
        return msg

    @lc_tool
    async def update_workflow_node(workflow: str, node_id: str,
                                   config_json: str = "", label: str = "") -> str:
        """Update an existing node's config (merge) and/or label, then save.

        Args:
            workflow: the workflow's exact name or numeric id.
            node_id: the node id (from add_workflow_node / get_workflow_structure).
            config_json: JSON object of config fields to set/overwrite.
            label: new label (optional).
        """
        if not _workflow_tools_allowed(state):
            return _WORKFLOW_DENIED
        from . import workflow_tools as _wt
        cfg, err = _wf_json_obj(config_json, "config_json")
        if err:
            return err
        msg, _ = await _wf_mutate_and_save(
            workflow, lambda d: _wt.update_node(d, node_id, cfg or None, label or None))
        return msg

    @lc_tool
    async def remove_workflow_node(workflow: str, node_id: str) -> str:
        """Remove a node and every edge touching it, then save. If it was the
        start node, the start moves to the first remaining node — re-wire and
        re-set the start as needed.

        Args:
            workflow: the workflow's exact name or numeric id.
            node_id: the node id to remove.
        """
        if not _workflow_tools_allowed(state):
            return _WORKFLOW_DENIED
        from . import workflow_tools as _wt
        msg, _ = await _wf_mutate_and_save(workflow, lambda d: _wt.remove_node(d, node_id))
        return msg

    @lc_tool
    async def wire_workflow_nodes(workflow: str, from_node: str, to_node: str,
                                  on: str = "pass") -> str:
        """Connect two nodes with an edge, then save. SLOT RULE: a node gets ONE
        outgoing 'pass' OR 'complete' edge (mutually exclusive) plus at most ONE
        'fail' edge — a competing edge is REJECTED; unwire the old edge first
        when inserting a node between two wired nodes.

        Args:
            workflow: the workflow's exact name or numeric id.
            from_node: source node id.
            to_node: target node id.
            on: 'pass' (success), 'fail', or 'complete' (either outcome).
        """
        if not _workflow_tools_allowed(state):
            return _WORKFLOW_DENIED
        from . import workflow_tools as _wt
        msg, _ = await _wf_mutate_and_save(workflow, lambda d: _wt.wire(d, from_node, to_node, on))
        return msg

    @lc_tool
    async def insert_workflow_node_between(workflow: str, node_type: str,
                                           from_node: str, to_node: str,
                                           label: str = "",
                                           config_json: str = "") -> str:
        """Insert a new node BETWEEN two already-wired nodes in ONE atomic
        operation: adds the node, removes the old from→to edge, and wires
        from→new→to — then saves once. ALWAYS use this (never a manual
        add + unwire + wire + wire sequence) when the user asks to insert a
        step between two existing steps; a manual sequence can be interrupted
        and leave the workflow disconnected. On any failure nothing changes.

        Args:
            workflow: the workflow's exact name or numeric id.
            node_type: a catalog node type (exact name, case-insensitive).
            from_node: the existing upstream node id (edge source).
            to_node: the existing downstream node id (edge target).
            label: short human label shown on the canvas.
            config_json: JSON object with the new node's config fields.
        Returns the new node's id and the rewired path.
        """
        if not _workflow_tools_allowed(state):
            return _WORKFLOW_DENIED
        from . import workflow_tools as _wt
        cfg, err = _wf_json_obj(config_json, "config_json")
        if err:
            return err
        msg, _ = await _wf_mutate_and_save(
            workflow,
            lambda d: _wt.insert_between(d, node_type, label, cfg, from_node, to_node,
                                         user_context=state.get("user_context")))
        return msg

    @lc_tool
    async def list_data_connections() -> str:
        """List the platform's data connections (id, name, type, database) so a
        Database node's numeric connection id can be resolved from the
        connection NAME the user mentions. ALWAYS call this yourself instead of
        asking the user for a connection id. Credentials are never included.
        """
        if not _workflow_tools_allowed(state):
            return _WORKFLOW_DENIED
        from . import workflow_tools as _wt
        res = _wt.list_connections()
        if not res.get("ok"):
            return f"❌ Could not list connections: {res.get('error')}"
        conns = res.get("connections") or []
        if not conns:
            return "No data connections exist on this platform yet."
        lines = [f"- id {c['id']} — {c['name']}"
                 + (f" ({c['type']}" + (f", db {c['database']})" if c['database'] else ")")
                    if c['type'] else (f" (db {c['database']})" if c['database'] else ""))
                 for c in conns]
        return "Data connections (use the numeric id in a Database node's config):\n" + "\n".join(lines)

    @lc_tool
    async def unwire_workflow_nodes(workflow: str, from_node: str, to_node: str,
                                    on: str = "") -> str:
        """Remove an edge between two nodes, then save. Leave `on` empty to
        remove every edge between the pair, or 'pass'/'fail'/'complete' for
        just that type.

        Args:
            workflow: the workflow's exact name or numeric id.
            from_node: source node id of the edge to remove.
            to_node: target node id of the edge to remove.
            on: edge type to remove, or empty for all.
        """
        if not _workflow_tools_allowed(state):
            return _WORKFLOW_DENIED
        from . import workflow_tools as _wt
        msg, _ = await _wf_mutate_and_save(
            workflow, lambda d: _wt.unwire(d, from_node, to_node, on or None))
        return msg

    @lc_tool
    async def set_workflow_start(workflow: str, node_id: str) -> str:
        """Mark a node as the workflow's start node (exactly one node runs
        first), then save.

        Args:
            workflow: the workflow's exact name or numeric id.
            node_id: the node to start from.
        """
        if not _workflow_tools_allowed(state):
            return _WORKFLOW_DENIED
        from . import workflow_tools as _wt
        msg, _ = await _wf_mutate_and_save(workflow, lambda d: _wt.set_start(d, node_id))
        return msg

    @lc_tool
    async def set_workflow_variable(workflow: str, name: str, var_type: str = "string",
                                    default_value: str = "", description: str = "") -> str:
        """Declare (or overwrite) a workflow variable, then save. Nodes reference
        variables with ${name} substitution (e.g. a Database query's
        ${customerId}, an Alert's ${total}).

        Args:
            workflow: the workflow's exact name or numeric id.
            name: variable name (referenced as ${name}).
            var_type: 'string' | 'number' | 'boolean' | 'array' | 'object'.
            default_value: default value as a string.
            description: what the variable holds.
        """
        if not _workflow_tools_allowed(state):
            return _WORKFLOW_DENIED
        from . import workflow_tools as _wt
        msg, _ = await _wf_mutate_and_save(
            workflow, lambda d: _wt.set_variable(d, name, var_type, default_value, description))
        return msg

    @lc_tool
    async def run_workflow(workflow: str, variables_json: str = "",
                           wait_seconds: int = 90) -> str:
        """Execute a visual workflow NOW and report the honest outcome. ⚠ THIS
        REALLY RUNS the workflow with live connections — nodes that write, send,
        or upload perform those side effects for real. The engine runs it
        asynchronously: this tool waits up to wait_seconds and then reports the
        REAL status — a run still executing is reported as running (check later
        with check_workflow_run), NEVER as success. GROUNDING: report only what
        this tool returns.

        Args:
            workflow: the workflow's exact name or numeric id.
            variables_json: JSON object of runtime variable values (optional).
            wait_seconds: how long to wait for completion (10–300, default 90).
        """
        if not _workflow_tools_allowed(state):
            return _WORKFLOW_DENIED
        from . import workflow_tools as _wt
        variables, err = _wf_json_obj(variables_json, "variables_json")
        if err:
            return err
        got = await asyncio.to_thread(_wt.get_definition, workflow)
        if not got.get("ok"):
            return f"Could not load the workflow: {got.get('error')}"
        if not got["definition"].get("nodes"):
            return (f"Workflow '{got['name']}' (id {got['id']}) is EMPTY — nothing to run. "
                    f"Add nodes first; do not report this as a successful run.")
        issues = _wt.local_issues(got["definition"])
        started = await asyncio.to_thread(_wt.start_run, got["id"], variables or None)
        if not started.get("ok"):
            pre = f" (known structural issues: {'; '.join(issues)})" if issues else ""
            return f"❌ Run failed to start: {started.get('error')}{pre}"
        exec_id = started.get("execution_id")
        wait = max(10, min(int(wait_seconds or 90), 300))
        outcome = await asyncio.to_thread(_wt.wait_for_outcome, str(exec_id), wait)
        return _wt.summarize_run(outcome)

    @lc_tool
    async def check_workflow_run(execution_id: str) -> str:
        """Read a workflow run's CURRENT status and per-step outcomes (for runs
        started earlier or still executing). GROUNDING: report exactly what this
        returns — running is running, failed is failed.

        Args:
            execution_id: the execution id run_workflow returned.
        """
        if not _workflow_tools_allowed(state):
            return _WORKFLOW_DENIED
        from . import workflow_tools as _wt
        outcome = await asyncio.to_thread(_wt.get_run_status, execution_id)
        return _wt.summarize_run(outcome)

    tools = [query_data_agent, query_general_agent, delegate_to_builder_agent, save_user_preference, recall_all_memories, forget_preference, switch_active_agent, export_data, read_artifact, run_generated_tool, manipulate_pdf, generate_map, search_web, send_email, get_my_contact_info]
    if IMAGE_GENERATION_ENABLED:
        tools.append(generate_image)
    if DOCUMENT_SEARCH_ENABLED:
        tools.append(search_documents)
    if _CODE_INTERPRETER_ENABLED:
        tools.append(run_python)
    if _SFTP_ENABLED:
        tools.append(sftp_list_files)
        tools.append(sftp_download)
        tools.append(sftp_upload)
    # Bind-time role gate (AIHUB-0028 F2): a non-Developer never even SEES the
    # automation tools, so the LLM can't half-answer an automations request
    # from other data instead of refusing. The in-tool _automations_allowed
    # checks stay as defense in depth; the server re-enforces at
    # /automations/api/internal/manage regardless.
    if _AUTOMATIONS_TOOLS_ENABLED and _automations_allowed(state):
        tools.append(list_automations)
        tools.append(create_automation)
        tools.append(get_automation)
        tools.append(save_automation_code)
        tools.append(dry_run_automation)
        tools.append(promote_automation)
        tools.append(run_automation)
        tools.append(get_automation_runs)
        tools.append(schedule_automation)
        # Code Flows ride the same Developer gate (same family as Automations).
        tools.append(list_code_flows)
        tools.append(create_code_flow)
        tools.append(add_code_step)
        tools.append(wire_steps)
        tools.append(unwire_steps)
        tools.append(remove_code_step)
        tools.append(update_step_code)
        tools.append(get_code_flow)
        tools.append(dry_run_code_flow)
        tools.append(run_code_flow)
        tools.append(schedule_code_flow)
    # Native visual-workflow tools: ONLY on native-impl Developer turns. A
    # classic turn's LLM never sees these, so the A/B separation is structural
    # (bind-time), not prompt-dependent — and a native non-Developer is refused
    # the same way automations are (AIHUB-0028 F2 discipline).
    if _native_impl(state) and _workflow_tools_allowed(state):
        tools.append(list_workflows)
        tools.append(get_workflow_structure)
        tools.append(create_workflow)
        tools.append(add_workflow_node)
        tools.append(update_workflow_node)
        tools.append(remove_workflow_node)
        tools.append(wire_workflow_nodes)
        tools.append(insert_workflow_node_between)
        tools.append(unwire_workflow_nodes)
        tools.append(set_workflow_start)
        tools.append(set_workflow_variable)
        tools.append(run_workflow)
        tools.append(check_workflow_run)
        tools.append(list_data_connections)
    if _PORTAL_FETCH_ENABLED:
        tools.append(fetch_from_portal)
        tools.append(check_portal_download)
        tools.append(save_portal)
        tools.append(lookup_portal)
        tools.append(list_portal_workflows)
        tools.append(describe_portal_workflow)
        tools.append(run_portal_workflow)
        if _SCHEDULE_ENABLED:
            tools.append(schedule_portal_workflow)
    if _SCHEDULE_ENABLED:
        tools.append(schedule_task)
        tools.append(list_scheduled_tasks)
        tools.append(cancel_scheduled_task)

    try:
        llm = get_llm(mini=False, streaming=False)
        llm_with_tools = llm.bind_tools(tools)
        _cv_t0 = _trace_time.perf_counter()
        response = await llm_with_tools.ainvoke(llm_messages)
        trace_llm_call(state, node="converse", step="converse_main",
                       messages=llm_messages, response=response,
                       elapsed_ms=int((_trace_time.perf_counter() - _cv_t0) * 1000), model_hint="full")

        # Check if LLM wants to call a tool
        if hasattr(response, 'tool_calls') and response.tool_calls:
            from langchain_core.messages import ToolMessage
            from graph.tracing import trace_log
            tool_results = []
            active_deleg = None
            _used_code_flow_tool = False   # AIHUB-0035: set the continuity marker
            _code_flow_name = None
            _used_workflow_tool = False    # native agent: visual_workflow marker twin
            _workflow_ref = None
            # AIHUB-0048 F1: track whether a MUTATING workflow tool ran this turn
            # and stash the last 🧾 read-back so the final reply's persisted-state
            # claim is pinned to tool evidence, never LLM narration.
            _wf_mutated = False
            _wf_last_readback = None

            for tc in response.tool_calls:
                tool_name = tc["name"]
                tool_args = tc["args"]
                logger.info(f"[converse] Tool call: {tool_name}({tool_args})")
                if tool_name in _CODE_FLOW_TOOL_NAMES:
                    _used_code_flow_tool = True
                    _code_flow_name = (tool_args or {}).get("name") or _code_flow_name
                if tool_name in _WORKFLOW_TOOL_NAMES:
                    _used_workflow_tool = True
                    _workflow_ref = ((tool_args or {}).get("workflow")
                                     or (tool_args or {}).get("name") or _workflow_ref)

                # Execute the tool — ALL tools must be handled
                tool_map = {
                    "query_data_agent": query_data_agent,
                    "query_general_agent": query_general_agent,
                    "delegate_to_builder_agent": delegate_to_builder_agent,
                    "save_user_preference": save_user_preference,
                    "recall_all_memories": recall_all_memories,
                    "forget_preference": forget_preference,
                    "switch_active_agent": switch_active_agent,
                    "export_data": export_data,
                    "read_artifact": read_artifact,
                    "run_generated_tool": run_generated_tool,
                    "manipulate_pdf": manipulate_pdf,
                    "generate_map": generate_map,
                    "generate_image": generate_image,
                    "search_web": search_web,
                    "search_documents": search_documents,
                    "send_email": send_email,
                    "run_python": run_python,
                    "sftp_list_files": sftp_list_files,
                    "sftp_download": sftp_download,
                    "sftp_upload": sftp_upload,
                    "fetch_from_portal": fetch_from_portal,
                    "check_portal_download": check_portal_download,
                    "save_portal": save_portal,
                    "lookup_portal": lookup_portal,
                    "list_portal_workflows": list_portal_workflows,
                    "describe_portal_workflow": describe_portal_workflow,
                    "run_portal_workflow": run_portal_workflow,
                    "schedule_portal_workflow": schedule_portal_workflow,
                    "schedule_task": schedule_task,
                    "list_scheduled_tasks": list_scheduled_tasks,
                    "cancel_scheduled_task": cancel_scheduled_task,
                    "get_my_contact_info": get_my_contact_info,
                    # Automations (AIHUB-0028): these were bound to the LLM + in
                    # the system prompt but MISSING here, so every call fell
                    # through to "Unknown tool: <name>" and nothing executed.
                    # tool_map may hold tools that aren't bound this turn (see
                    # generate_image) — harmless; the LLM only calls bound ones,
                    # and each tool re-checks the Developer gate internally.
                    "list_automations": list_automations,
                    "create_automation": create_automation,
                    "get_automation": get_automation,
                    "save_automation_code": save_automation_code,
                    "dry_run_automation": dry_run_automation,
                    "promote_automation": promote_automation,
                    "run_automation": run_automation,
                    "get_automation_runs": get_automation_runs,
                    "schedule_automation": schedule_automation,
                    # Code Flows — MUST be here too (the AIHUB-0028 dual-
                    # registration trap: a tool bound to the LLM but missing
                    # from tool_map falls through to "Unknown tool" and never
                    # executes). Each re-checks the Developer gate internally.
                    "list_code_flows": list_code_flows,
                    "create_code_flow": create_code_flow,
                    "add_code_step": add_code_step,
                    "wire_steps": wire_steps,
                    "unwire_steps": unwire_steps,
                    "remove_code_step": remove_code_step,
                    "update_step_code": update_step_code,
                    "get_code_flow": get_code_flow,
                    "dry_run_code_flow": dry_run_code_flow,
                    "run_code_flow": run_code_flow,
                    "schedule_code_flow": schedule_code_flow,
                    # Native visual-workflow tools (CC_AGENT="native") — MUST
                    # be here too (the AIHUB-0028 dual-registration trap: a
                    # tool bound to the LLM but missing from tool_map falls
                    # through to "Unknown tool" and never executes). Each
                    # re-checks the Developer gate internally.
                    "list_workflows": list_workflows,
                    "get_workflow_structure": get_workflow_structure,
                    "create_workflow": create_workflow,
                    "add_workflow_node": add_workflow_node,
                    "update_workflow_node": update_workflow_node,
                    "remove_workflow_node": remove_workflow_node,
                    "wire_workflow_nodes": wire_workflow_nodes,
                    "insert_workflow_node_between": insert_workflow_node_between,
                    "unwire_workflow_nodes": unwire_workflow_nodes,
                    "set_workflow_start": set_workflow_start,
                    "set_workflow_variable": set_workflow_variable,
                    "run_workflow": run_workflow,
                    "check_workflow_run": check_workflow_run,
                    "list_data_connections": list_data_connections,
                }

                tool_fn = tool_map.get(tool_name)

                # Trace tool call execution
                trace_log(
                    state,
                    event_type="tool_start",
                    node=f"tool:{tool_name}",
                    payload={"args": tool_args},
                )

                if tool_fn:
                    try:
                        result = await tool_fn.ainvoke(tool_args)
                    except Exception as _tool_err:
                        # Don't leak raw Pydantic validation errors / URLs / internal
                        # field names to the user. Log the detail server-side and
                        # return a friendly, actionable message so the LLM can retry.
                        err_str = str(_tool_err)
                        logger.warning(
                            f"[converse] Tool '{tool_name}' invocation failed: {err_str[:400]} "
                            f"args={str(tool_args)[:300]}"
                        )
                        # Classify the error for a better user-facing hint.
                        lower = err_str.lower()
                        if "validation error" in lower or "field required" in lower:
                            friendly = (
                                f"The {tool_name} tool was called with missing or invalid "
                                f"parameters and could not run. Please retry with all "
                                f"required fields supplied."
                            )
                        elif "timeout" in lower:
                            friendly = (
                                f"The {tool_name} tool timed out. Please retry in a moment."
                            )
                        else:
                            friendly = (
                                f"The {tool_name} tool was unable to complete this request."
                            )
                        result = friendly
                else:
                    result = f"Unknown tool: {tool_name}"

                trace_log(
                    state,
                    event_type="tool_end",
                    node=f"tool:{tool_name}",
                    payload={
                        "result_preview": str(result)[:800],
                    },
                )

                tool_results.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
                # AIHUB-0048 F1: capture mutation + read-back evidence (round 1)
                if tool_name in _WORKFLOW_MUTATING_TOOL_NAMES:
                    _wf_mutated = True
                    if "🧾" in str(result):
                        _wf_last_readback = str(result)
                # Log the tool RESULT preview to the service log (not just the
                # trace, which truncates) so a tool that returns an error string
                # — e.g. "Could not create the automation: <reason>" — is
                # durably diagnosable. Without this, a returned-error (vs raised)
                # failure left no record of the reason (AIHUB-0028 triage).
                logger.info(f"[converse] Tool '{tool_name}' result: {str(result)[:300]}")

                # Track delegation for agent query/switch tools
                if tool_name in ("query_data_agent", "query_general_agent"):
                    active_deleg = {
                        "agent_id": str(tool_args.get("agent_id")),
                        "agent_name": f"Agent #{tool_args.get('agent_id')}",
                        "agent_type": "data" if tool_name == "query_data_agent" else "general",
                        "started_at": datetime.now().isoformat(),
                        "history": [],
                    }
                    # Record this exchange as a side-conversation thread for the UI panel.
                    try:
                        from graph.delegation_log import record_turn
                        record_turn(
                            state.get("session_id", ""),
                            tool_args.get("agent_id"),
                            f"Agent #{tool_args.get('agent_id')}",
                            "data" if tool_name == "query_data_agent" else "general",
                            tool_args.get("question") or tool_args.get("query") or "",
                            str(result),
                        )
                    except Exception:
                        pass
                elif tool_name == "switch_active_agent":
                    active_deleg = {
                        "agent_id": str(tool_args.get("agent_id")),
                        "agent_name": tool_args.get("agent_name", f"Agent #{tool_args.get('agent_id')}"),
                        "agent_type": "data",
                        "started_at": datetime.now().isoformat(),
                        "history": [],
                    }
                elif tool_name == "delegate_to_builder_agent":
                    cc_sid = state.get("session_id", "cc-default")
                    existing = state.get("active_delegation") or {}
                    # W3a (#15): derive the HONEST build state from the captured delegator
                    # result instead of hardcoding 'in_progress' (which pinned the session to
                    # the builder forever and never surfaced created resources).
                    _bstate = _derive_build_state(_builder_capture.get("result") or {})
                    builder_sid = (_bstate["builder_session_id"] or existing.get("builder_session_id")
                                   or f"cc-builder-{cc_sid}")
                    # Append to builder conversation log
                    builder_log = list(existing.get("builder_log", []))
                    builder_log.append({"role": "user_to_builder", "content": tool_args.get("request", ""), "ts": datetime.now().isoformat()})
                    builder_log.append({"role": "builder_response", "content": str(result)[:2000], "ts": datetime.now().isoformat()})
                    active_deleg = {
                        "agent_id": "builder",
                        "agent_name": "Builder Agent",
                        "agent_type": "builder",
                        "started_at": existing.get("started_at", datetime.now().isoformat()),
                        "builder_session_id": builder_sid,
                        "builder_log": builder_log,
                        "build_status": _bstate["build_status"],
                        "history": [],
                    }
                    if _bstate["created_resources"]:
                        active_deleg["created_resources"] = _bstate["created_resources"]
                    if _bstate["completed_at"]:
                        active_deleg["completed_at"] = _bstate["completed_at"]

            # Check if any tool returned renderable blocks (map, artifact)
            # that should pass through directly instead of going back to LLM.
            # "action" rides along with artifact chips (e.g. the portal fetch
            # returns download chips + a Save-as-workflow button) — without it
            # the all() below fails and the chips get paraphrased away.
            direct_block_types = ("map", "artifact", "table", "image", "kpi", "action")
            direct_blocks = []
            # P5-1: renderable chips salvaged from MIXED tool output (e.g. a
            # delegated data/general agent returns [text, artifact]). The pure
            # `all()` gate below misses these, so historically they were
            # paraphrased away by the follow-up LLM. We collect them here and
            # append them to the final answer so a download/CTA chip always
            # reaches the user, even alongside prose.
            preserved_chips = []

            def _salvage_chips(parsed_val):
                if isinstance(parsed_val, dict) and parsed_val.get("type") in direct_block_types:
                    preserved_chips.append(parsed_val)
                elif isinstance(parsed_val, list):
                    for _b in parsed_val:
                        if isinstance(_b, dict) and _b.get("type") in direct_block_types:
                            preserved_chips.append(_b)

            for tr in tool_results:
                try:
                    parsed = json.loads(tr.content)
                    if isinstance(parsed, dict) and parsed.get("type") in direct_block_types:
                        # Single block object (e.g. artifact from export_data)
                        direct_blocks.append(parsed)
                    elif isinstance(parsed, list) and parsed:
                        # Array of CC blocks
                        if all(isinstance(b, dict) and b.get("type") in direct_block_types for b in parsed):
                            direct_blocks.extend(parsed)
                        else:
                            # Mixed array (e.g. [text, artifact]) — salvage the
                            # renderable chips so they aren't paraphrased away.
                            _salvage_chips(parsed)
                except (json.JSONDecodeError, TypeError):
                    pass

            if direct_blocks:
                # Check if blocks contain large data (images) — skip LLM follow-up to avoid token overflow
                has_large_data = any(
                    len(str(b.get("src", ""))) > 10000 or b.get("type") == "image"
                    for b in direct_blocks
                )

                all_blocks = []
                if not has_large_data:
                    # Send back to LLM with tools — the LLM may need to chain
                    # another tool call (e.g. export_data → send_email)
                    try:
                        follow_up_messages = llm_messages + [response] + tool_results
                        _fu1_t0 = _trace_time.perf_counter()
                        db_response = await llm_with_tools.ainvoke(follow_up_messages)
                        trace_llm_call(state, node="converse", step="converse_followup_direct_block",
                                       messages=follow_up_messages, response=db_response,
                                       elapsed_ms=int((_trace_time.perf_counter() - _fu1_t0) * 1000), model_hint="full")

                        # If the LLM wants to call more tools, execute them
                        if hasattr(db_response, 'tool_calls') and db_response.tool_calls:
                            logger.info(f"[converse] Direct-block follow-up has {len(db_response.tool_calls)} tool call(s)")
                            db_tool_results = []
                            for tc in db_response.tool_calls:
                                t_name = tc["name"]
                                t_args = tc["args"]
                                logger.info(f"[converse] Direct-block chained tool: {t_name}({t_args})")
                                t_fn = tool_map.get(t_name)
                                if t_fn:
                                    t_result = await t_fn.ainvoke(t_args)
                                else:
                                    t_result = f"Unknown tool: {t_name}"
                                db_tool_results.append(ToolMessage(content=str(t_result), tool_call_id=tc["id"]))

                            # Get final intro from LLM after chained tools complete
                            final_msgs = follow_up_messages + [db_response] + db_tool_results
                            _ch_t0 = _trace_time.perf_counter()
                            db_final = await llm.ainvoke(final_msgs)
                            trace_llm_call(state, node="converse", step="converse_followup_chained",
                                           messages=final_msgs, response=db_final,
                                           elapsed_ms=int((_trace_time.perf_counter() - _ch_t0) * 1000), model_hint="full")
                            intro_text = db_final.content if hasattr(db_final, 'content') else ""
                        else:
                            intro_text = db_response.content if hasattr(db_response, 'content') else ""

                        if intro_text:
                            all_blocks.append({"type": "text", "content": intro_text})
                    except Exception as e:
                        logger.warning(f"[converse] LLM follow-up failed (skipping intro): {e}")
                else:
                    # For images/large data, generate a simple intro without LLM
                    block_types = [b.get("type") for b in direct_blocks]
                    if "image" in block_types:
                        all_blocks.append({"type": "text", "content": "Here's the generated image:"})
                    elif "map" in block_types:
                        all_blocks.append({"type": "text", "content": "Here's the map:"})

                all_blocks.extend(direct_blocks)
                content = json.dumps(all_blocks)
                result = {"messages": [AIMessage(content=content)]}
                if active_deleg:
                    result["active_delegation"] = active_deleg
                if _used_code_flow_tool:
                    result["code_flow_context"] = {"name": _code_flow_name}
                elif _used_workflow_tool:
                    result["code_flow_context"] = {"name": _workflow_ref,
                                                   "kind": "visual_workflow"}
                return result

            # Send tool results back to LLM for final response
            # MUST use llm_with_tools — using plain llm causes tool calls to
            # leak as garbled text (to=functions.*) instead of structured tool_calls
            follow_up_messages = llm_messages + [response] + tool_results
            _fu2_t0 = _trace_time.perf_counter()
            final_response = await llm_with_tools.ainvoke(follow_up_messages)
            trace_llm_call(state, node="converse", step="converse_followup",
                           messages=follow_up_messages, response=final_response,
                           elapsed_ms=int((_trace_time.perf_counter() - _fu2_t0) * 1000), model_hint="full")

            # Log what the LLM returned for debugging garbled responses
            _fc = final_response.content if hasattr(final_response, 'content') else str(final_response)
            _has_tc = bool(hasattr(final_response, 'tool_calls') and final_response.tool_calls)
            logger.info(f"[converse] Follow-up response: {len(_fc)} chars, has_tool_calls={_has_tc}, preview={_fc[:150]!r}")

            # Keep executing tool rounds while the model keeps asking for tools, BINDING TOOLS
            # each round so the agent can actually ACT. The old code allowed only a single extra
            # round and then forced a TOOL-LESS final pass (llm, not llm_with_tools); an agent
            # that spent its two rounds exploring (e.g. list_portal_workflows then
            # describe_portal_workflow) could never reach the execution call (run_portal_workflow /
            # fetch_from_portal) and would just narrate its intent ("Using the saved portal
            # workflow") with nothing reaching the browser. The loop lets explore->act complete.
            #
            # AIHUB-0050 F1: the cap is PROGRESS-AWARE, not a flat round count. A flat
            # _MAX_TOOL_ROUNDS=6 truncated any one-turn build needing >6 sequential tool calls
            # (a standard branching build — create + 4 nodes + 3 edges — is 8), forcing an
            # honest-but-incomplete DRAFT + a user nudge to finish. The cap's real job is
            # anti-runaway, and a runaway is a loop making NO progress — so rounds that execute
            # at least one live (non-short-circuited) tool call never exhaust the budget; only
            # CONSECUTIVE all-cached rounds do (the 0028 spin, already answered from the
            # _ToolRepeatGuard cache). A generous absolute backstop still bounds the turn
            # against a pathological always-new-calls runaway.
            _MAX_STALLED_ROUNDS = 3    # consecutive rounds with zero live tool executions
            _MAX_TOOL_ROUNDS_ABS = 24  # hard backstop; ~20-node builds fit, runaways cannot
            _round = 1
            _stalled_rounds = 0
            _convo = follow_up_messages

            # Anti-repeat guard (AIHUB-0028, found live): a model that gets a
            # tool ERROR sometimes retries the IDENTICAL call every round until
            # the cap, making no progress (observed: create_automation called
            # 6× after a transient "could not reach" error, then a confabulated
            # wrap-up). Track (tool, args) already tried this turn; a verbatim
            # repeat is answered from cache with a firm "stop repeating" nudge
            # instead of re-executing. Seed from round 1.
            # AIHUB-0048 F2: progress-aware (see _ToolRepeatGuard) — a verbatim
            # repeat is only short-circuited when NOTHING else executed since
            # its last attempt; a retry after intervening calls (fixed
            # preconditions, e.g. re-wiring after an unwire) runs for real.
            _repeat_guard = _ToolRepeatGuard()
            for _tc0, _tr0 in zip(getattr(response, "tool_calls", []) or [], tool_results):
                _repeat_guard.record(_tc0["name"], _tc0["args"], _tr0.content)

            while _has_tc and _round < _MAX_TOOL_ROUNDS_ABS and _stalled_rounds < _MAX_STALLED_ROUNDS:
                _round += 1
                logger.info(f"[converse] Follow-up wants MORE tool calls — executing round {_round}")
                tool_results_n = []
                _live_this_round = 0
                for tc in final_response.tool_calls:
                    tool_name = tc["name"]
                    tool_args = tc["args"]
                    if tool_name in _CODE_FLOW_TOOL_NAMES:
                        _used_code_flow_tool = True
                        _code_flow_name = (tool_args or {}).get("name") or _code_flow_name
                    if tool_name in _WORKFLOW_TOOL_NAMES:
                        _used_workflow_tool = True
                        _workflow_ref = ((tool_args or {}).get("workflow")
                                         or (tool_args or {}).get("name") or _workflow_ref)
                    _cached = _repeat_guard.cached_if_no_progress(tool_name, tool_args)
                    if _cached is not None:
                        logger.warning(
                            f"[converse] Round {_round}: repeated identical call "
                            f"{tool_name}(...) with NO intervening progress — short-circuiting"
                        )
                        rn = (
                            f"STOP — you already called {tool_name} with these exact arguments and "
                            f"nothing else has run since; it returned: {_cached[:600]}. Do not call "
                            f"it again unchanged. Either change the arguments to fix the problem, "
                            f"call a different tool to make progress, or stop and tell the user the "
                            f"real outcome truthfully (including the error above). Never claim the "
                            f"tool is unavailable — it was called and returned the result shown."
                        )
                        tool_results_n.append(ToolMessage(content=str(rn), tool_call_id=tc["id"]))
                        continue
                    logger.info(f"[converse] Round {_round} tool call: {tool_name}({tool_args})")
                    tool_fn = tool_map.get(tool_name)
                    if tool_fn:
                        try:
                            rn = await tool_fn.ainvoke(tool_args)
                        except Exception as _tool_err2:
                            logger.warning(
                                f"[converse] Round-{_round} tool '{tool_name}' failed: {str(_tool_err2)[:400]}"
                            )
                            rn = (
                                f"The {tool_name} tool could not complete — parameters "
                                f"may be missing or invalid. Please retry with complete fields."
                            )
                    else:
                        rn = f"Unknown tool: {tool_name}"
                    _repeat_guard.record(tool_name, tool_args, rn)
                    _live_this_round += 1
                    # AIHUB-0048 F1: capture mutation + read-back evidence (round N)
                    if tool_name in _WORKFLOW_MUTATING_TOOL_NAMES:
                        _wf_mutated = True
                        if "🧾" in str(rn):
                            _wf_last_readback = str(rn)
                    logger.info(f"[converse] Round {_round} '{tool_name}' result: {str(rn)[:300]}")
                    tool_results_n.append(ToolMessage(content=str(rn), tool_call_id=tc["id"]))

                    # Track delegation
                    if tool_name in ("query_data_agent", "query_general_agent"):
                        active_deleg = {
                            "agent_id": str(tool_args.get("agent_id")),
                            "agent_name": f"Agent #{tool_args.get('agent_id')}",
                            "agent_type": "data" if tool_name == "query_data_agent" else "general",
                            "started_at": datetime.now().isoformat(),
                            "history": [],
                        }

                # Check for direct blocks from this round
                for tr in tool_results_n:
                    try:
                        parsed = json.loads(tr.content)
                        if isinstance(parsed, list) and parsed and all(
                            isinstance(b, dict) and b.get("type") in ("map", "artifact", "table", "image", "kpi", "action")
                            for b in parsed
                        ):
                            direct_blocks.extend(parsed)
                        else:
                            # Mixed output this round — salvage chips too (P5-1).
                            _salvage_chips(parsed)
                    except (json.JSONDecodeError, TypeError):
                        pass

                if direct_blocks:
                    all_blocks = []
                    block_types = [b.get("type") for b in direct_blocks]
                    if "map" in block_types:
                        all_blocks.append({"type": "text", "content": "Here's the map:"})
                    all_blocks.extend(direct_blocks)
                    content = json.dumps(all_blocks)
                    result = {"messages": [AIMessage(content=content)]}
                    if active_deleg:
                        result["active_delegation"] = active_deleg
                    if _used_code_flow_tool:
                        result["code_flow_context"] = {"name": _code_flow_name}
                    elif _used_workflow_tool:
                        result["code_flow_context"] = {"name": _workflow_ref,
                                                       "kind": "visual_workflow"}
                    return result

                # AIHUB-0050 F1: progress bookkeeping — a round where every call was served
                # from the repeat-guard cache made no forward progress; a round with any live
                # execution resets the stall counter (large builds run to completion).
                _stalled_rounds = 0 if _live_this_round else _stalled_rounds + 1
                if not _live_this_round:
                    logger.warning(f"[converse] Round {_round} made NO live tool calls "
                                   f"(all short-circuited repeats) — stall {_stalled_rounds}/"
                                   f"{_MAX_STALLED_ROUNDS}")

                _convo = _convo + [final_response] + tool_results_n
                _rN_t0 = _trace_time.perf_counter()
                # Keep tools bound so the agent can still act; only the final capped pass drops
                # them to force a text wrap-up instead of another (impossible) tool round.
                if _round < _MAX_TOOL_ROUNDS_ABS and _stalled_rounds < _MAX_STALLED_ROUNDS:
                    final_response = await llm_with_tools.ainvoke(_convo)
                else:
                    _cap_reason = (
                        f"absolute tool-round backstop ({_MAX_TOOL_ROUNDS_ABS})"
                        if _round >= _MAX_TOOL_ROUNDS_ABS else
                        f"{_MAX_STALLED_ROUNDS} consecutive no-progress rounds "
                        f"(identical repeats, all short-circuited)"
                    )
                    logger.warning(f"[converse] hit tool-round cap — {_cap_reason}; forcing a "
                                   "text wrap-up — the agent may not have finished its tool plan.")
                    # Honest wrap-up (AIHUB-0028, found live): without guidance the
                    # capped, tool-less pass confabulated ("the tools are not
                    # available in the current runtime") when the tools had in fact
                    # been called and returned errors. Force a truthful summary of
                    # what actually happened — never a fabricated capability excuse.
                    _honest_nudge = SystemMessage(content=(
                        "You have reached the tool-call limit for this turn. Write a truthful summary "
                        "based ONLY on what the tool results above actually returned. If any tool "
                        "returned an error, report that error plainly and say the operation did NOT "
                        "complete. NEVER say the tools are unavailable, not enabled, or missing — they "
                        "were called; describe their real results. Do not claim anything was created, "
                        "saved, promoted, or verified unless a tool result explicitly confirms it."
                    ))
                    final_response = await llm.ainvoke(_convo + [_honest_nudge])
                trace_llm_call(state, node="converse", step=f"converse_round{_round}",
                               messages=_convo, response=final_response,
                               elapsed_ms=int((_trace_time.perf_counter() - _rN_t0) * 1000), model_hint="full")
                _has_tc = bool(getattr(final_response, "tool_calls", None))
                logger.info(f"[converse] Round {_round} follow-up: {len(final_response.content)} chars, "
                            f"has_tool_calls={_has_tc}")

            # ── Output sanitizer: catch raw JSON / tool metadata leaking to user ──
            final_response = _sanitize_llm_response(final_response, llm)

            # AIHUB-0048 F1: pin the persisted state to tool evidence. When a
            # mutating workflow tool ran this turn, the reply always ends with
            # the LAST save's 🧾 read-back verbatim (the 0038 output-pin
            # doctrine — the narration above cannot be the only description of
            # what persisted).
            if _wf_mutated and _wf_last_readback:
                _rb_idx = _wf_last_readback.find("🧾")
                _rb_pin = (_wf_last_readback[_rb_idx:] if _rb_idx >= 0
                           else _wf_last_readback)[:900]
                _cur = final_response.content if hasattr(final_response, "content") else ""
                if "🧾" not in (_cur or "")[-1000:]:
                    final_response = AIMessage(content=(
                        (_cur or "") + "\n\n📋 **Authoritative persisted state "
                        "(deterministic read-back of the saved row):**\n" + _rb_pin))

            # P5-1: if a tool returned chips inside MIXED output (e.g. a delegated
            # agent's [text, artifact]), the follow-up LLM only produced prose —
            # append the salvaged chips so the download/CTA still reaches the user
            # as a real block (not a paraphrase). Dedup by artifact_id/url.
            if preserved_chips:
                _seen = set()
                _chips = []
                for _c in preserved_chips:
                    _k = _c.get("artifact_id") or _c.get("url") or _c.get("download_url") or id(_c)
                    if _k in _seen:
                        continue
                    _seen.add(_k)
                    _chips.append(_c)
                _intro = final_response.content if hasattr(final_response, "content") else str(final_response)
                _blocks = ([{"type": "text", "content": _intro}] if _intro else []) + _chips
                final_response = AIMessage(content=json.dumps(_blocks))

            result = {"messages": [final_response]}
            if active_deleg:
                result["active_delegation"] = active_deleg
            if _used_code_flow_tool:
                result["code_flow_context"] = {"name": _code_flow_name}
            elif _used_workflow_tool:
                result["code_flow_context"] = {"name": _workflow_ref,
                                               "kind": "visual_workflow"}
            return result

        # Sanitize even non-tool responses
        response = _sanitize_llm_response(response, llm)

        # AIHUB-0048 F1 (blocker, live): with ZERO tool calls this turn, the
        # model fabricated "✅ Inserted … / Current persisted structure: […]" —
        # a claimed, saved structural edit that never happened (DB read-back
        # proved the row unchanged). Deterministic fail-closed guard: a no-tool
        # reply that claims a just-completed build mutation gets the truth
        # appended — nothing ran, nothing changed.
        _nt_text = response.content if hasattr(response, "content") else ""
        if isinstance(_nt_text, str) and _claims_completed_mutation(_nt_text):
            logger.warning("[converse] fabrication guard: no-tool reply claimed a completed "
                           "mutation — appending the no-changes-were-made correction")
            try:
                from graph.tracing import trace_log as _fg_trace
                _fg_trace(state, event_type="fabrication_guard",
                          node="converse", payload={"preview": _nt_text[:300]}, level="warning")
            except Exception:
                pass
            response = AIMessage(content=(
                _nt_text + "\n\n⚠️ **Correction (automatic honesty check):** no build/edit "
                "tools were executed in this turn, so NO changes were actually made to any "
                "workflow or resource just now. If the message above describes a change as "
                "completed, that description is wrong — tell me to actually perform it and "
                "I will run the real tools."))
        return {"messages": [response]}
    except Exception as e:
        logger.error(f"Conversation failed: {e}", exc_info=True)
        # Never expose raw exception text (may include internal field names, URLs,
        # stack detail). Return a generic, friendly message.
        error_content = json.dumps([{"type": "text", "content": (
            "I ran into a problem handling that request. Please try again or rephrase "
            "it — if the issue keeps happening, let me know and I'll look closer."
        )}])
        return {"messages": [AIMessage(content=error_content)]}


def _sanitize_llm_response(response, llm=None):
    """Catch raw JSON, tool call metadata, or technical garbage before it reaches the user.
    If detected, re-process through mini LLM to generate a human-readable message."""
    if not hasattr(response, 'content') or not response.content:
        return response

    content = response.content.strip()
    if not content:
        return response

    # Quick heuristics: does this look like raw JSON / tool metadata / garbled tool calls?
    is_raw_json = False

    # Check 1: Pure JSON with suspicious keys
    try:
        if content.startswith(("{", "[")):
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                suspicious_keys = {"agent_id", "question", "tool", "tool_name", "parameters",
                                   "tool_call_id", "function", "name", "arguments"}
                if suspicious_keys & set(parsed.keys()):
                    is_raw_json = True
                    logger.warning(f"[sanitize] Caught raw tool metadata JSON: keys={list(parsed.keys())}")
            elif isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                if any(k in parsed[0] for k in ("agent_id", "tool", "function")):
                    is_raw_json = True
                    logger.warning(f"[sanitize] Caught raw JSON array with tool metadata")
    except (json.JSONDecodeError, TypeError):
        pass

    # Check 2: Garbled tool call patterns (partial JSON embedded in text)
    # Only trigger if the content is MOSTLY garbled (short content with tool metadata),
    # not if a long valid response has minor artifacts
    if not is_raw_json:
        import re
        # Patterns that indicate the response is primarily tool call noise
        garble_patterns = [
            r'"agent_id"\s*:\s*\d+.*"question"\s*:',  # agent call JSON embedded in text
            r'"tool"\s*:\s*"[^"]+"\s*,\s*"query"',    # tool call JSON in text
        ]
        for pat in garble_patterns:
            if re.search(pat, content):
                is_raw_json = True
                logger.warning(f"[sanitize] Caught garbled tool call pattern: {pat}")
                break

        # Only flag to=functions patterns if content is SHORT (< 200 chars)
        # or if the garble makes up most of the content. Long valid responses
        # sometimes have minor artifacts that shouldn't trigger sanitization.
        if not is_raw_json and len(content) < 200:
            weak_patterns = [
                r'to=functions\.\w+',           # LangChain tool call routing
                r'to=search_web',               # raw tool routing
            ]
            for pat in weak_patterns:
                if re.search(pat, content):
                    is_raw_json = True
                    logger.warning(f"[sanitize] Caught short garbled response ({len(content)} chars): {pat}")
                    break

    if not is_raw_json:
        return response

    # Re-process through LLM to generate a human-readable message
    logger.info("[sanitize] Re-processing raw JSON through LLM for human-readable output")
    try:
        from cc_config import get_step_llm as _get_step_llm
        mini = _get_step_llm("response_sanitizer")
        import asyncio

        sanitize_prompt = (
            "The AI assistant produced raw JSON instead of a human-readable response. "
            "This was likely a tool call or internal metadata that leaked to the user. "
            "Generate a brief, friendly message explaining that the request is being processed "
            "or that there was an issue connecting to the data source. "
            "Do NOT include any JSON. Do NOT fabricate data. Keep it to 1-2 sentences.\n\n"
            f"Raw content: {content[:500]}"
        )
        # Use sync invocation since we may be in various async contexts
        result = mini.invoke([HumanMessage(content=sanitize_prompt)])
        cleaned = result.content.strip() if hasattr(result, 'content') else ""
        if cleaned:
            logger.info(f"[sanitize] Cleaned response: {cleaned[:100]}")
            return AIMessage(content=cleaned)
    except Exception as e:
        logger.warning(f"[sanitize] Mini LLM cleanup failed: {e}")

    # Last resort: generic error message
    return AIMessage(content="I encountered an issue processing your request. The data source may be temporarily unavailable — please try again in a moment.")


# ─── Node: scan_landscape ─────────────────────────────────────────────────

async def scan_landscape(state: CommandCenterState) -> dict:
    """Scan the platform for all available agents, tools, workflows."""
    try:
        from command_center.orchestration.landscape_scanner import scan_platform
        landscape = await scan_platform(state.get("user_context"))
        logger.info(f"Landscape scan: {len(landscape.get('agents', []))} agents, "
                     f"{len(landscape.get('tools', []))} tools, "
                     f"{len(landscape.get('workflows', []))} workflows")
        return {"landscape": landscape}
    except Exception as e:
        logger.error(f"Landscape scan failed: {e}")
        return {"landscape": {"agents": [], "tools": [], "workflows": [], "connections": [], "error": str(e)}}


# ─── Node: gather_data ────────────────────────────────────────────────────

def _wrap_tool_output(output: str, tool_name: str) -> str:
    """Wrap generated tool output in CC-compatible blocks.

    Handles:
    - Markdown tables → table blocks
    - Plain text / markdown → text blocks
    - JSON objects/arrays → appropriate blocks
    """
    import re

    blocks = []

    # Try to detect if output is a markdown table
    lines = output.strip().split("\n")
    table_lines = [l for l in lines if l.strip().startswith("|") and "|" in l[1:]]

    if len(table_lines) >= 3:  # header + separator + at least one row
        # Parse markdown table
        header_line = table_lines[0]
        headers = [h.strip() for h in header_line.strip("|").split("|")]
        headers = [h for h in headers if h]  # Remove empties

        data_lines = [l for l in table_lines[2:] if not all(c in "-| " for c in l)]
        rows = []
        for dl in data_lines:
            cells = [c.strip() for c in dl.strip("|").split("|")]
            cells = [c for c in cells if c or len(cells) > 1]
            rows.append(cells)

        if headers and rows:
            # Add any non-table text before the table
            pre_table = "\n".join(l for l in lines if l not in table_lines).strip()
            if pre_table:
                blocks.append({"type": "text", "content": pre_table})

            blocks.append({
                "type": "table",
                "title": tool_name.replace("_", " ").title(),
                "headers": headers,
                "rows": rows,
            })
            return json.dumps(blocks)

    # Try JSON
    try:
        parsed = json.loads(output)
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            # Array of objects → table
            headers = list(parsed[0].keys())
            rows = [[str(item.get(h, "")) for h in headers] for item in parsed]
            blocks.append({
                "type": "table",
                "title": tool_name.replace("_", " ").title(),
                "headers": headers,
                "rows": rows,
            })
            return json.dumps(blocks)
        elif isinstance(parsed, dict):
            blocks.append({"type": "text", "content": json.dumps(parsed, indent=2)})
            return json.dumps(blocks)
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: wrap as text block
    blocks.append({"type": "text", "content": output})
    return json.dumps(blocks)


async def _classify_delegation_result(result: dict, question: str, trace_state: dict = None) -> dict:
    """Classify whether a delegation result is a success, failure, or empty.

    Uses mini-LLM for ambiguous cases (HTTP 200 but response says "I can't answer").
    Hard failures (timeout, HTTP error) already have status="failed" — no LLM needed.

    Returns: {"classification": "success"|"failure"|"empty", "reason": str}
    """
    status = result.get("status", "")
    text = result.get("text", "")

    # Hard failures from delegator.py
    if status == "failed":
        return {"classification": "failure", "reason": text[:200]}

    # Empty response
    if not text or not text.strip():
        return {"classification": "empty", "reason": "Agent returned no content"}

    # Use LLM to classify ambiguous responses
    try:
        from cc_config import get_step_llm
        llm = get_step_llm("delegation_result_classifier")
        _cdr_msgs = [HumanMessage(content=(
            f'Did this agent response successfully answer the question?\n\n'
            f'Question: "{question[:200]}"\n'
            f'Response: "{text[:800]}"\n\n'
            f'Reply with ONLY one word: SUCCESS, FAILURE, or EMPTY.'
        ))]
        _cdr_t0 = _trace_time.perf_counter()
        resp = await llm.ainvoke(_cdr_msgs)
        if trace_state:
            trace_llm_call(trace_state, node="gather_data", step="delegation_result_classifier",
                           messages=_cdr_msgs, response=resp,
                           elapsed_ms=int((_trace_time.perf_counter() - _cdr_t0) * 1000), model_hint="mini")
        classification = resp.content.strip().upper().split()[0] if hasattr(resp, 'content') else "SUCCESS"
        if classification not in ("SUCCESS", "FAILURE", "EMPTY"):
            classification = "SUCCESS"
        return {"classification": classification.lower(), "reason": "LLM classified"}
    except Exception:
        # If LLM fails, assume success (don't block on classification errors)
        return {"classification": "success", "reason": "classification skipped"}


async def _find_alternative_agents(question: str, failed_agent_id: str, data_agents: list, trace_state: dict = None) -> list:
    """Score and rank alternative data agents for a question.

    Returns: [{"agent_id": str, "agent_name": str, "confidence": float, "reason": str}]
    Sorted by confidence descending. Only returns agents with confidence >= 0.3.
    """
    candidates = [a for a in data_agents if str(a.get("agent_id")) != str(failed_agent_id)]
    if not candidates:
        return []

    try:
        from cc_config import get_step_llm
        llm = get_step_llm("alternative_agent_finder")

        agent_list = "\n".join([
            f"- ID:{a.get('agent_id')} Name:{a.get('agent_name','')} Desc:{a.get('description','')[:100]}"
            for a in candidates[:15]
        ])

        _faa_msgs = [HumanMessage(content=(
            f'Which of these data agents can best answer this question?\n\n'
            f'Question: "{question[:200]}"\n\n'
            f'Agents:\n{agent_list}\n\n'
            f'Return a JSON array of objects with agent_id, confidence (0.0-1.0), '
            f'and reason (brief). Only include agents with confidence >= 0.3. '
            f'Example: [{{"agent_id":"14","confidence":0.8,"reason":"handles sales data"}}]'
        ))]
        _faa_t0 = _trace_time.perf_counter()
        resp = await llm.ainvoke(_faa_msgs)
        if trace_state:
            trace_llm_call(trace_state, node="gather_data", step="alternative_agent_finder",
                           messages=_faa_msgs, response=resp,
                           elapsed_ms=int((_trace_time.perf_counter() - _faa_t0) * 1000), model_hint="mini")

        import re
        text = resp.content if hasattr(resp, 'content') else "[]"
        # Extract JSON array from response
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            alternatives = json.loads(match.group())
            # Enrich with agent names
            for alt in alternatives:
                agent_match = next((a for a in candidates if str(a.get("agent_id")) == str(alt.get("agent_id"))), None)
                if agent_match:
                    alt["agent_name"] = agent_match.get("agent_name", f"Agent #{alt['agent_id']}")
            # Sort by confidence descending, filter >= 0.3
            alternatives = [a for a in alternatives if float(a.get("confidence", 0)) >= 0.3]
            alternatives.sort(key=lambda x: float(x.get("confidence", 0)), reverse=True)
            return alternatives
    except Exception as e:
        logger.warning(f"[_find_alternative_agents] Failed: {e}")

    return []


def _build_response_blocks(result: dict, agent_name: str, agent_id: str, *, failed: bool = False) -> str:
    """Convert a delegate_to_agent result into a JSON list of CC-compatible blocks.

    When rich_content.blocks are present, they are mapped to the CC renderer
    block types (image, table, text) instead of being flattened to plain text.
    Falls back to a single text block when no rich content is available.

    Returns a JSON string suitable for AIMessage.content.
    """
    response_text = result.get("text", "No response from agent.")

    if failed or result.get("status") == "failed":
        return json.dumps([{"type": "text", "content": (
            f"⚠️ **{agent_name}** (Agent #{agent_id}) encountered an error:\n\n{response_text}"
        )}])

    blocks: list = []

    # Attribution header
    blocks.append({"type": "text", "content": f"📊 **{agent_name}** (Agent #{agent_id}):"})

    # Try to unpack rich_content.blocks from the data agent
    rich = result.get("rich_content")
    has_rich = False
    if rich:
        try:
            if isinstance(rich, str):
                rich = json.loads(rich)
            if isinstance(rich, dict) and rich.get("blocks"):
                for block in rich["blocks"]:
                    btype = block.get("type", "")
                    if btype == "chart_image" and block.get("content"):
                        # base64 PNG → CC image block
                        img_content = block["content"]
                        # Content may already be a data URI or raw base64
                        if img_content.startswith("data:"):
                            src = img_content
                        else:
                            src = f"data:image/png;base64,{img_content}"
                        blocks.append({
                            "type": "image",
                            "src": src,
                            "alt": block.get("title", block.get("metadata", {}).get("title", "Chart")),
                        })
                        has_rich = True
                    elif btype == "table" and block.get("content"):
                        table_data = block["content"]
                        if isinstance(table_data, list) and table_data:
                            headers = list(table_data[0].keys())
                            rows = [[str(r.get(c, "")) for c in headers] for r in table_data]
                            _tmeta = block.get("metadata") or {}
                            cc_table = {
                                "type": "table",
                                "title": block.get("title", ""),
                                "headers": headers,
                                "rows": rows,
                            }
                            # Preview signal — the renderer capped the rows; the
                            # UI shows a banner and (usually) a download chip.
                            if _tmeta.get("truncated"):
                                cc_table["truncated"] = True
                                if _tmeta.get("total_rows") is not None:
                                    cc_table["total_rows"] = _tmeta.get("total_rows")
                            blocks.append(cc_table)
                            has_rich = True
                    elif btype == "list" and block.get("content"):
                        items = block["content"]
                        if isinstance(items, list):
                            md_list = "\n".join(f"- {item}" for item in items)
                            blocks.append({"type": "text", "content": md_list})
                            has_rich = True
                    elif btype == "text" and block.get("content"):
                        blocks.append({"type": "text", "content": block["content"]})
                        has_rich = True
        except Exception as e:
            logger.warning(f"[_build_response_blocks] Rich content parse error: {e}")

    if not has_rich:
        # No rich content — fall back to plain text (replace the attribution block)
        blocks = [{"type": "text", "content": (
            f"📊 **{agent_name}** (Agent #{agent_id}):\n\n{response_text}"
        )}]

    # Large-result artifact handles (full CSV persisted to the shared store).
    # The inline table above is a PREVIEW; these chips are the full download.
    artifacts = result.get("artifacts") or []
    for art in artifacts:
        if not isinstance(art, dict):
            continue
        rc = art.get("row_count")
        note = (f"📎 The full result"
                + (f" ({rc:,} rows)" if isinstance(rc, int) else "")
                + " is available to download — the table above is a preview.")
        blocks.append({"type": "text", "content": note})
        # Ensure it renders as a download chip (artifact is a direct block type).
        art_block = dict(art)
        art_block["type"] = "artifact"
        blocks.append(art_block)

    # Meta block for conversation coherence — the LLM sees this in message history
    # and knows which agent produced which data. Frontend renders as subtle text.
    status = result.get("status", "unknown")
    blocks.append({"type": "meta", "content": f"[Source: {agent_name} (Agent #{agent_id}), status: {status}]"})

    return json.dumps(blocks)


async def gather_data(state: CommandCenterState) -> dict:
    """Route data queries to the appropriate data agent.

    Context layers used:
    1. Session: active_delegation (continue with same agent if mid-conversation)
    2. User memory: preferred agents for query types (future)
    3. System: landscape scan (available agents + objectives)
    """
    from cc_config import get_llm, STRUCTURED_RESPONSE_FORMAT
    from command_center.orchestration.landscape_scanner import scan_platform, format_landscape_summary
    from command_center.orchestration.delegator import delegate_to_agent
    from datetime import datetime

    messages = state.get("messages", [])
    last_msg = messages[-1] if messages else None
    user_text = last_msg.content if last_msg and hasattr(last_msg, 'content') else ""

    # Progress streaming — emit real-time status to the SSE endpoint
    from graph.progress import get_queue as _get_progress_queue
    _pq = _get_progress_queue(state.get("session_id", ""))

    async def _emit_progress(phase: str, message: str):
        if _pq:
            await _pq.emit("status", {"phase": phase, "message": message})

    # Build conversation context for delegation (coherence)
    _delegation_context = _build_delegation_context(
        messages, state.get("session_resources")
    )

    # id -> display name map (for labelling side-conversation threads in the UI panel)
    _landscape_gd = state.get("landscape") or {}
    _all_agents_gd = _landscape_gd.get("all_agents") or _landscape_gd.get("agents") or []
    _agent_name_by_id = {}
    for _a_gd in _all_agents_gd:
        _aid_gd = _a_gd.get("agent_id") or _a_gd.get("id")
        if _aid_gd is not None:
            _agent_name_by_id[str(_aid_gd)] = (
                _a_gd.get("agent_name") or _a_gd.get("name") or f"Agent #{_aid_gd}"
            )

    # Tracing helpers
    from graph.tracing import trace_log
    import time as _time

    async def _delegate_to_agent(*, agent_id: str, question: str, is_data_agent: bool, session_id: str, conversation_history=None):
        # Capture the clean question BEFORE any context prepend, for the UI thread.
        _clean_q = question
        # Prepend conversation context to the question for coherence
        nonlocal _delegation_context
        if _delegation_context and not conversation_history:
            # Only add context for first-time delegations (no existing agent history)
            question = f"{_delegation_context}\n\nCurrent question: {question}"
        trace_log(
            state,
            event_type="delegate_start",
            node="delegate_to_agent",
            payload={
                "agent_id": str(agent_id),
                "is_data_agent": bool(is_data_agent),
                "question_preview": str(question)[:400],
            },
        )
        t0 = _time.perf_counter()
        res = await delegate_to_agent(
            agent_id=str(agent_id),
            question=question,
            conversation_history=conversation_history,
            is_data_agent=is_data_agent,
            session_id=session_id,
            user_context=state.get("user_context"),
        )
        elapsed_ms = int((_time.perf_counter() - t0) * 1000)
        trace_log(
            state,
            event_type="delegate_end",
            node="delegate_to_agent",
            payload={
                "agent_id": str(agent_id),
                "elapsed_ms": elapsed_ms,
                "status": res.get("status"),
                "text_preview": str(res.get("text") or "")[:800],
            },
        )
        # Record this exchange as a side-conversation thread for the UI panel.
        try:
            from graph.delegation_log import record_turn
            record_turn(
                state.get("session_id", ""),
                agent_id,
                _agent_name_by_id.get(str(agent_id), f"Agent #{agent_id}"),
                "data" if is_data_agent else "general",
                _clean_q,
                res.get("text") if isinstance(res, dict) else str(res),
            )
        except Exception:
            pass
        return res

    # ── Check active delegation first ──────────────────────────────────
    active = state.get("active_delegation")
    if active and active.get("agent_id"):
        agent_id = active["agent_id"]
        agent_name = active.get("agent_name", "Data Agent")
        logger.info(f"[gather_data] Continuing delegation to {agent_name} [{agent_id}]")

        # Special-case: builder delegation should never go through delegate_to_agent
        if str(active.get("agent_type") or "").lower() == "builder" or str(agent_id).lower() == "builder":
            # Same Developer+ gate as the build node — a non-dev must not be able
            # to continue a builder delegation either.
            if not _build_allowed(state):
                logger.info("[gather_data] builder delegation refused — user lacks Developer role")
                return {"messages": [AIMessage(content=_BUILD_DENIED_MSG)], "active_delegation": None}
            from command_center.orchestration.delegator import delegate_to_builder
            cc_sid = state.get("session_id", "cc-default")
            builder_sid = active.get("builder_session_id") or f"cc-builder-{cc_sid}"
            result = await delegate_to_builder(
                message=user_text,
                session_id=cc_sid,
                builder_session_id=builder_sid,
                user_context=state.get("user_context"),
                timeout=120.0,
            )
            response_text = result.get("text", "No response from builder.")
            # W3a (#15): this continuation had the full result but only updated the session
            # id — derive the honest build_status/created_resources too, so a completed
            # chat-path build is actually marked complete (not stuck 'in_progress').
            _bstate = _derive_build_state(result)
            updated = dict(active)
            updated["builder_session_id"] = _bstate["builder_session_id"] or builder_sid
            updated["build_status"] = _bstate["build_status"]
            if _bstate["created_resources"]:
                updated["created_resources"] = _bstate["created_resources"]
            if _bstate["completed_at"]:
                updated["completed_at"] = _bstate["completed_at"]
            return {"messages": [AIMessage(content=response_text)], "active_delegation": updated}

        # ── Check for export request before delegating to agent ──────────
        # Uses a mini-LLM (export_intent_detector) to decide whether the
        # user is asking to export prior results, and which format, rather
        # than keyword-matching "export"/"csv"/"pdf" in the raw text.
        # Replaces a brittle cascade that false-positived on e.g.
        # "PDF format explanations would help".
        _is_export, fmt = await _detect_export_intent(user_text, trace_state=state)
        if _is_export:
            # Extract table blocks from prior messages
            table_blocks = []
            for msg in reversed(messages[:-1]):
                if not hasattr(msg, 'content') or not msg.content:
                    continue
                try:
                    parsed = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
                    if isinstance(parsed, list):
                        for b in parsed:
                            if isinstance(b, dict) and b.get("type") == "table" and b.get("headers"):
                                table_blocks.append(b)
                        if table_blocks:
                            break
                except (json.JSONDecodeError, TypeError):
                    continue

            if table_blocks:
                from command_center.artifacts.artifact_models import ArtifactType

                format_map = {"csv": ArtifactType.CSV, "excel": ArtifactType.EXCEL,
                              "pdf": ArtifactType.PDF, "json": ArtifactType.JSON, "text": ArtifactType.TEXT}
                artifact_type = format_map.get(fmt, ArtifactType.EXCEL)

                from routes.artifacts import _get_artifact_manager
                mgr = _get_artifact_manager()
                user_ctx = state.get("user_context") or {}
                user_id = str(user_ctx.get("user_id", "anonymous"))
                session_id = state.get("session_id", "cc-default")

                # Use agent name for the filename
                file_name = agent_name.lower().replace(" ", "_") + "_export"

                try:
                    meta = mgr.create_from_blocks(
                        blocks=table_blocks,
                        artifact_type=artifact_type,
                        name=file_name,
                        session_id=f"{user_id}/{session_id}",
                    )
                    if meta:
                        artifact_block = meta.to_content_block()
                        artifact_block["description"] = f"Exported from {agent_name}"
                        blocks_json = json.dumps([
                            {"type": "text", "content": f"Here's your {fmt.upper()} export:"},
                            artifact_block,
                        ])
                        logger.info(f"[gather_data] Export created: {meta.artifact_id} ({meta.name}, {meta.size_display})")
                        return {"messages": [AIMessage(content=blocks_json)], "active_delegation": active}
                    else:
                        error_content = json.dumps([{"type": "text", "content": f"Sorry, I couldn't generate the {fmt} file. The renderer may not be available."}])
                        return {"messages": [AIMessage(content=error_content)], "active_delegation": active}
                except Exception as e:
                    logger.error(f"[gather_data] Export failed: {e}", exc_info=True)
                    error_content = json.dumps([{"type": "text", "content": f"Export failed: {str(e)}"}])
                    return {"messages": [AIMessage(content=error_content)], "active_delegation": active}
            else:
                error_content = json.dumps([{"type": "text", "content": "No table data found in recent results to export. Please query some data first."}])
                return {"messages": [AIMessage(content=error_content)], "active_delegation": active}

        # Build history for the delegated agent (last few exchanges)
        agent_history = active.get("history", [])

        await _emit_progress("delegating", f"Asking {agent_name}...")
        result = await _delegate_to_agent(
            agent_id=str(agent_id),
            question=user_text,
            conversation_history=agent_history,
            is_data_agent=True,
            session_id=state.get("session_id", "cc-default"),
        )

        response_text = result.get("text", "No response from agent.")

        # ── Intelligent fallback: classify result and try alternatives if needed ──
        classification = await _classify_delegation_result(result, user_text, trace_state=state)

        if classification["classification"] in ("failure", "empty"):
            logger.info(f"[gather_data] Agent {agent_name} [{agent_id}] failed ({classification['classification']}), scanning for alternatives...")
            await _emit_progress("fallback", f"{agent_name} couldn't answer, looking for alternatives...")

            try:
                _fb_landscape = await scan_platform(state.get("user_context"))
                _fb_data_agents = [a for a in _fb_landscape.get('data_agents', []) if a.get('enabled')]
            except Exception:
                _fb_data_agents = []

            alternatives = await _find_alternative_agents(user_text, str(agent_id), _fb_data_agents, trace_state=state)

            if alternatives and float(alternatives[0].get("confidence", 0)) >= 0.7:
                # Auto-try the top alternative
                alt = alternatives[0]
                alt_id = str(alt["agent_id"])
                alt_name = alt.get("agent_name", f"Agent #{alt_id}")
                logger.info(f"[gather_data] Auto-fallback to {alt_name} [{alt_id}] (confidence={alt.get('confidence')})")
                await _emit_progress("fallback", f"Trying {alt_name} instead...")

                fallback_result = await _delegate_to_agent(
                    agent_id=alt_id, question=user_text,
                    is_data_agent=True, session_id=state.get("session_id", "cc-default"),
                )
                fb_class = await _classify_delegation_result(fallback_result, user_text, trace_state=state)

                if fb_class["classification"] == "success":
                    # Fallback succeeded — switch delegation to the new agent
                    new_delegation = {
                        "agent_id": alt_id, "agent_name": alt_name,
                        "agent_type": "data", "started_at": datetime.now().isoformat(),
                        "history": [{"role": "user", "content": user_text},
                                    {"role": "assistant", "content": fallback_result.get("text", "")}],
                    }
                    content = _build_response_blocks(fallback_result, alt_name, alt_id)
                    return {"messages": [AIMessage(content=content)], "active_delegation": new_delegation}

            elif alternatives and float(alternatives[0].get("confidence", 0)) >= 0.3:
                # Ask user to confirm before trying
                alt = alternatives[0]
                ask_content = json.dumps([{"type": "text", "content": (
                    f"**{agent_name}** couldn't answer your question.\n\n"
                    f"I found an alternative: **{alt.get('agent_name')}** — {alt.get('reason', '')}\n\n"
                    f"Would you like me to try this agent instead?"
                )}])
                return {"messages": [AIMessage(content=ask_content)], "active_delegation": active,
                        "pending_agent_selection": True}

            # No viable alternatives or fallback also failed — show original error
            logger.info(f"[gather_data] No viable fallback found, showing original error")

        # Update delegation history
        updated_history = list(agent_history)
        updated_history.append({"role": "user", "content": user_text})
        updated_history.append({"role": "assistant", "content": response_text})
        # Keep last 10 exchanges max
        if len(updated_history) > 20:
            updated_history = updated_history[-20:]

        updated_delegation = dict(active)
        updated_delegation["history"] = updated_history

        content = _build_response_blocks(result, agent_name, agent_id)
        return {"messages": [AIMessage(content=content)], "active_delegation": updated_delegation}

    # ── Handle reroute (user explicitly asked to switch to a named agent) ──
    # This fires when classify_intent detected REROUTE and resolved the target
    # agent + original question.  We delegate directly — no agent picker needed.
    reroute = state.get("reroute_context")
    if reroute and reroute.get("agent_id"):
        rr_agent_id = str(reroute["agent_id"])
        rr_agent_name = reroute.get("agent_name", f"Agent #{rr_agent_id}")
        rr_is_data = reroute.get("is_data_agent", True)
        rr_question = reroute.get("original_question", user_text)

        logger.info(f"[gather_data] Reroute to {rr_agent_name} [{rr_agent_id}]: {rr_question[:80]}")
        await _emit_progress("delegating", f"Asking {rr_agent_name}...")

        result = await _delegate_to_agent(
            agent_id=rr_agent_id,
            question=rr_question,
            is_data_agent=rr_is_data,
            session_id=state.get("session_id", "cc-default"),
        )

        response_text = result.get("text", "No response from agent.")
        new_delegation = {
            "agent_id": rr_agent_id,
            "agent_name": rr_agent_name,
            "agent_type": "data" if rr_is_data else "general",
            "started_at": datetime.now().isoformat(),
            "history": [
                {"role": "user", "content": rr_question},
                {"role": "assistant", "content": response_text},
            ],
        }
        content = _build_response_blocks(result, rr_agent_name, rr_agent_id)
        return {
            "messages": [AIMessage(content=content)],
            "active_delegation": new_delegation,
            "reroute_context": None,  # consumed
        }

    # ── No active delegation — find the right agent ────────────────────
    # Get available data agents
    try:
        landscape = await scan_platform(state.get("user_context"))
    except Exception as e:
        logger.error(f"[gather_data] Landscape scan failed: {e}")
        landscape = {}

    # ── Handle pending agent selection (user is answering "which agent?") ──
    # One LLM call reads the actual conversation and returns BOTH the chosen
    # agent_id AND the user's original question (the one that triggered the
    # disambiguation). This replaces the previous two-step approach (agent-id
    # parser + fragile reverse message scan) which lost the original question
    # whenever the scan fell through to the agent-selection reply.
    if state.get("pending_agent_selection") and not active:
        da_list = [a for a in landscape.get('data_agents', []) if a.get('enabled')]
        agents_list_text = "\n".join(
            f"- [{a.get('agent_id')}] {a.get('agent_name')} — {(a.get('description') or '')[:120]}"
            for a in da_list[:30]
        )
        # Faithful transcript of the conversation BEFORE the latest reply, so the
        # LLM can locate the original question in context.
        history_text = _format_history_for_llm(messages[:-1])

        interp_prompt = f"""You are interpreting a user's reply in an agent-selection
disambiguation flow. Earlier in the conversation the assistant asked the user to
pick a data agent. The user has now replied.

Conversation so far (most recent last):
{history_text}

User's latest reply (the disambiguation answer):
"{user_text}"

Available data agents:
{agents_list_text}

Your job:
1. Decide which agent_id the user chose. If unclear or the user named an agent
   that is not in the list, return "NONE".
2. Identify the user's ORIGINAL question — the question that caused the
   disambiguation, which the chosen agent should now answer. This is NOT the
   latest reply (that reply is just the agent pick). Look earlier in the
   conversation for the real analytical question. If the user's reply contains
   extra details or a refined version of the question, merge them with the
   original question.

Respond with ONLY a JSON object, no prose:
{{"agent_id": "<id or NONE>", "original_question": "<the question to ask the chosen agent>"}}"""

        try:
            from cc_config import get_step_llm
            llm = get_step_llm("agent_selection_parser")
            _asp_msgs = [
                SystemMessage(content="You extract an agent choice and the user's original question from a dialogue. Output strict JSON."),
                HumanMessage(content=interp_prompt),
            ]
            _asp_t0 = _trace_time.perf_counter()
            pick_resp = await llm.ainvoke(_asp_msgs)
            trace_llm_call(state, node="gather_data", step="agent_selection_parser",
                           messages=_asp_msgs, response=pick_resp,
                           elapsed_ms=int((_trace_time.perf_counter() - _asp_t0) * 1000), model_hint="mini")
            raw = pick_resp.content.strip()
            # Tolerant JSON extraction (strip fences / stray prose)
            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.lower().startswith("json"):
                    raw = raw[4:]
            start = raw.find("{"); end = raw.rfind("}")
            parsed = json.loads(raw[start:end+1]) if start != -1 and end != -1 else {}
            chosen = str(parsed.get("agent_id", "")).strip()
            orig_question = (parsed.get("original_question") or "").strip()

            chosen_agent = next((a for a in da_list if str(a.get('agent_id')) == chosen), None)
            if chosen_agent and orig_question:
                agent_id = chosen
                agent_name = chosen_agent.get('agent_name', 'Data Agent')
                logger.info(f"[gather_data] Pending-selection resolved: agent={agent_id} "
                            f"original_question={orig_question[:80]!r}")
                result = await _delegate_to_agent(
                    agent_id=agent_id, question=orig_question,
                    is_data_agent=True, session_id=state.get("session_id", "cc-default"),
                )
                response_text = result.get("text", "No response from agent.")
                new_delegation = {
                    "agent_id": str(agent_id), "agent_name": agent_name,
                    "agent_type": "data", "started_at": datetime.now().isoformat(),
                    "history": [{"role": "user", "content": orig_question},
                                {"role": "assistant", "content": response_text}],
                }
                content = _build_response_blocks(result, agent_name, agent_id)
                return {"messages": [AIMessage(content=content)],
                        "active_delegation": new_delegation,
                        "pending_agent_selection": False}

            # LLM couldn't resolve cleanly — ask the user to clarify rather than
            # silently delegating the wrong text.
            logger.info(f"[gather_data] Pending-selection unresolved: raw={raw[:200]!r}")
            content = json.dumps([{"type": "text", "content": (
                "I couldn't tell which agent you meant or what you'd like me to ask. "
                "Please repeat your original question and include the agent name or id, "
                "e.g. *'Ask EDW Postgres Agent for sales by region last year'*."
            )}])
            return {"messages": [AIMessage(content=content)],
                    "pending_agent_selection": False}
        except Exception as e:
            logger.error(f"Pending-selection interpreter failed: {e}")
            # fall through to the normal picker below

    data_agents = [a for a in landscape.get("data_agents", []) if a.get("enabled")]

    if not data_agents:
        # No data agents available — tell user honestly
        content = json.dumps([{"type": "text", "content": (
            "## No Data Agents Available\n\n"
            "There are no data agents configured on this platform. "
            "To query databases, you'll need to create a data agent first.\n\n"
            "Would you like me to help set one up using the Builder Agent?"
        )}])
        return {"messages": [AIMessage(content=content)]}

    if len(data_agents) == 1:
        # Only one data agent — use it directly
        agent = data_agents[0]
        agent_id = agent.get("agent_id")
        agent_name = agent.get("agent_name", "Data Agent")
        logger.info(f"[gather_data] Auto-delegating to {agent_name} [{agent_id}]")

        await _emit_progress("delegating", f"Asking {agent_name}...")
        result = await _delegate_to_agent(
            agent_id=str(agent_id), question=user_text,
            is_data_agent=True, session_id=state.get("session_id", "cc-default"),
        )
        
        response_text = result.get("text", "No response from agent.")
        new_delegation = {
            "agent_id": str(agent_id),
            "agent_name": agent_name,
            "agent_type": "data",
            "started_at": datetime.now().isoformat(),
            "history": [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": response_text},
            ],
        }
        content = _build_response_blocks(result, agent_name, str(agent_id))
        return {"messages": [AIMessage(content=content)], "active_delegation": new_delegation}

    # ── Check for recently created agent (from a just-completed build) ──
    # This takes highest priority so the user's "now query the data" request
    # goes to the agent they just asked the builder to create, not the default.
    recently_created = state.get("recently_created_resources") or []
    recently_created_agent = next(
        (r for r in recently_created if r.get("type") == "agent" and r.get("id")),
        None,
    )
    if recently_created_agent:
        rc_agent_id = str(recently_created_agent["id"])
        rc_agent_name = recently_created_agent.get("name", f"Agent #{rc_agent_id}")
        # Verify the agent exists in the landscape data agents
        rc_match = next((a for a in data_agents if str(a.get("agent_id")) == rc_agent_id), None)
        if rc_match:
            logger.info(f"[gather_data] Using recently created agent: {rc_agent_name} [{rc_agent_id}]")
            await _emit_progress("delegating", f"Asking {rc_agent_name} (just created)...")
            result = await _delegate_to_agent(
                agent_id=rc_agent_id, question=user_text,
                is_data_agent=True, session_id=state.get("session_id", "cc-default"),
            )
            response_text = result.get("text", "No response from agent.")
            new_delegation = {
                "agent_id": rc_agent_id,
                "agent_name": rc_agent_name,
                "agent_type": "data",
                "started_at": datetime.now().isoformat(),
                "history": [{"role": "user", "content": user_text}, {"role": "assistant", "content": response_text}],
            }
            content = _build_response_blocks(result, f"{rc_agent_name} *(just created)*", rc_agent_id)
            return {
                "messages": [AIMessage(content=content)],
                "active_delegation": new_delegation,
                "recently_created_resources": None,  # consumed — clear for next turn
            }

    # ── Route Memory shortcut — skip LLM agent picker if we have a confident match ──
    route_match = state.get("route_memory_match")
    if route_match and route_match.get("agent_id"):
        rm_agent_id = str(route_match["agent_id"])
        rm_agent = next((a for a in data_agents if str(a.get("agent_id")) == rm_agent_id), None)
        if rm_agent:
            rm_agent_name = rm_agent.get("agent_name", f"Agent #{rm_agent_id}")
            logger.info(f"[gather_data] Route memory shortcut: {rm_agent_name} [{rm_agent_id}]")
            await _emit_progress("delegating", f"Asking {rm_agent_name} (learned route)...")
            result = await _delegate_to_agent(
                agent_id=rm_agent_id, question=user_text,
                is_data_agent=True, session_id=state.get("session_id", "cc-default"),
            )

            # Check if it succeeded — if not, log failure and fall through to full picker
            classification = await _classify_delegation_result(result, user_text, trace_state=state)
            if classification["classification"] in ("success", "partial"):
                response_text = result.get("text", "No response from agent.")
                new_delegation = {
                    "agent_id": rm_agent_id,
                    "agent_name": rm_agent_name,
                    "agent_type": "data",
                    "started_at": datetime.now().isoformat(),
                    "history": [
                        {"role": "user", "content": user_text},
                        {"role": "assistant", "content": response_text},
                    ],
                }
                content = _build_response_blocks(result, f"{rm_agent_name} *(learned route)*", rm_agent_id)
                return {
                    "messages": [AIMessage(content=content)],
                    "active_delegation": new_delegation,
                    "route_memory_match": None,  # consumed
                }
            else:
                # Shortcut failed — log failure for self-healing, fall through to full picker
                logger.info(f"[gather_data] Route memory shortcut failed for {rm_agent_name}, falling through to discovery")
                try:
                    user_ctx = state.get("user_context") or {}
                    _rm_uid = user_ctx.get("user_id")
                    if _rm_uid:
                        from command_center.memory.route_memory import log_route
                        asyncio.ensure_future(log_route(
                            user_id=int(_rm_uid), query_text=user_text,
                            intent=state.get("intent", "query"),
                            agent_id=rm_agent_id, agent_name=rm_agent_name,
                            route_path="gather_data->route_memory_fail",
                            success=False,
                        ))
                except Exception:
                    pass
        # else: agent no longer exists in landscape — silently fall through

    # ── Deterministic exact-name resolution (AIHUB-0021 F3) ─────────────
    # When the user names an agent and exactly one real agent's FULL name
    # appears in the message, pick it without the LLM — the fuzzy picker once
    # mapped a nonexistent 'test-...-SalesAgent' onto the topically-similar
    # 'BU2 Sales Agent'. Substring-on-full-name only (mirrors the proven
    # REROUTE matcher); token overlap would recreate the fuzzy bug.
    _exact_pick = None
    _ut_norm = " ".join(user_text.lower().split())
    _name_hits = []
    for _cand in data_agents:
        _aname = " ".join(str(_cand.get("agent_name") or "").lower().split())
        # Only distinctive names (multi-word or >=8 chars) may auto-pick —
        # a one-word agent called "Sales" must not swallow every sentence
        # containing the word — and the match must be word-bounded, never a
        # partial-word hit.
        if not _aname or (len(_aname) < 8 and " " not in _aname):
            continue
        if re.search(r"(?<![\w-])" + re.escape(_aname) + r"(?![\w-])", _ut_norm):
            _name_hits.append(_cand)
    if len(_name_hits) == 1:
        _exact_pick = str(_name_hits[0].get("agent_id"))
        logger.info(
            f"[gather_data] Exact agent-name match → "
            f"{_name_hits[0].get('agent_name')} [{_exact_pick}] (skipping LLM picker)"
        )

    # Multiple data agents — use LLM to pick the best one or ask user
    await _emit_progress("selecting", f"Selecting best agent from {len(data_agents)} available...")
    agents_info = "\n".join([
        f"- [{a.get('agent_id')}] **{a.get('agent_name')}** — {a.get('description', 'No description')[:150]}"
        for a in data_agents[:20]
    ])

    # Include session resources so the LLM can prefer recently created agents
    session_res = state.get("session_resources") or []
    session_hint = ""
    if session_res:
        session_hint = f"\n\nResources created this session (PREFER these if relevant):\n{_format_session_resources(session_res)}"

    _ap_conv = _format_conversation_for_prompt(messages)
    _ap_conv_block = (
        f"\nRecent conversation (for context if the user's request references prior turns):\n{_ap_conv}\n"
        if _ap_conv else ""
    )

    pick_prompt = f"""Select the BEST data agent for this query. Be DECISIVE — auto-pick unless truly impossible.
{_ap_conv_block}
User request: {user_text}

Available data agents:
{agents_info}{session_hint}

RULES:
- If any agent clearly matches the query topic (sales, inventory, customers, etc.), respond with ONLY its agent_id number (e.g., "14")
- If the user explicitly names an agent (e.g., "use AIRDB", "ask agent 281"), respond with that agent's id
- If the user's request is a short follow-up ("try another one", "use a different agent"), infer the intended TOPIC from the recent conversation above and pick the agent that matches that topic.
- Pick the most specifically relevant agent. When in doubt, pick the first data agent.
- If the user explicitly names an agent and NO agent in the list has that name, do NOT substitute a similar-sounding agent — respond exactly: NONE: I don't have an agent named '<name>'.
- Only respond "ASK: <question>" if there are multiple agents with IDENTICAL scope and you truly cannot determine which is right
- NEVER ask the user to pick if only 1-2 agents exist or if the query clearly maps to one agent

Respond with ONLY the agent_id number OR "ASK: <brief question listing 2-3 options>" OR "NONE: <reason>"."""
    pick_prompt += _preferences_block(state)

    try:
        if _exact_pick is not None:
            pick = _exact_pick
        else:
            from cc_config import get_step_llm
            llm = get_step_llm("agent_picker")
            _ap_msgs = [
                SystemMessage(content="You are a routing assistant. Pick the best agent for the task."),
                HumanMessage(content=pick_prompt),
            ]
            _ap_t0 = _trace_time.perf_counter()
            response = await llm.ainvoke(_ap_msgs)
            trace_llm_call(state, node="gather_data", step="agent_picker",
                           messages=_ap_msgs, response=response,
                           elapsed_ms=int((_trace_time.perf_counter() - _ap_t0) * 1000), model_hint="mini")
            pick = response.content.strip()
        logger.info(f"[gather_data] Agent picker response: {pick}")

        if pick.startswith("ASK:"):
            # Mark session with pending agent selection so next reply is handled correctly
            content = json.dumps([{"type": "text", "content": (
                f"{pick[4:].strip()}\n\n"
                "Just tell me the agent name or number and I'll run your query immediately."
            )}])
            return {"messages": [AIMessage(content=content)], "pending_agent_selection": True}
        elif pick.startswith("NONE:"):
            content = json.dumps([{"type": "text", "content": (
                f"## No Matching Agent\n\n{pick[5:].strip()}"
            )}])
            return {"messages": [AIMessage(content=content)]}
        else:
            # Got an agent_id — delegate
            agent_id = pick.strip().strip('"').strip("'")
            # Find the agent name
            agent_name = "Data Agent"
            for a in data_agents:
                if str(a.get("agent_id")) == agent_id:
                    agent_name = a.get("agent_name", "Data Agent")
                    break

            logger.info(f"[gather_data] Delegating to {agent_name} [{agent_id}]")
            await _emit_progress("delegating", f"Asking {agent_name}...")
            result = await _delegate_to_agent(
                agent_id=agent_id, question=user_text,
                is_data_agent=True, session_id=state.get("session_id", "cc-default"),
            )
            
            response_text = result.get("text", "No response from agent.")
            new_delegation = {
                "agent_id": str(agent_id),
                "agent_name": agent_name,
                "agent_type": "data",
                "started_at": datetime.now().isoformat(),
                "history": [
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": response_text},
                ],
            }

            # NOTE: Agent routing preferences are now handled by the LLM-driven
            # memory extractor (extract_and_save_memory) which runs after each turn.
            # The old auto-write of preferred_agent:{id} was removed to prevent duplicates.

            content = _build_response_blocks(result, agent_name, agent_id)
            return {"messages": [AIMessage(content=content)], "active_delegation": new_delegation}

    except Exception as e:
        logger.error(f"Data gathering failed: {e}")
        error_content = json.dumps([{"type": "text", "content": f"Failed to route query: {str(e)}"}])
        return {"messages": [AIMessage(content=error_content)]}


# ─── Node: analyze ────────────────────────────────────────────────────────

async def analyze(state: CommandCenterState) -> dict:
    """LLM-driven analysis of gathered data."""
    from cc_config import get_llm, STRUCTURED_RESPONSE_FORMAT

    messages = state.get("messages", [])

    system_prompt = (
        "You are an expert data analyst. Analyze the data provided and generate insights.\n"
        "Look for trends, anomalies, comparisons, and actionable recommendations.\n\n"
        + STRUCTURED_RESPONSE_FORMAT
    )
    system_prompt += _preferences_block(state)

    llm_messages = [SystemMessage(content=system_prompt)] + list(messages[-20:])

    try:
        llm = get_llm(mini=False, streaming=False)
        _an_t0 = _trace_time.perf_counter()
        response = await llm.ainvoke(llm_messages)
        trace_llm_call(state, node="analyze", step="analysis",
                       messages=llm_messages, response=response,
                       elapsed_ms=int((_trace_time.perf_counter() - _an_t0) * 1000), model_hint="full")
        return {"messages": [response]}
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        error_content = json.dumps([{"type": "text", "content": f"Analysis failed: {str(e)}"}])
        return {"messages": [AIMessage(content=error_content)]}


# ─── Node: decompose_tasks ────────────────────────────────────────────────

async def decompose_tasks(state: CommandCenterState) -> dict:
    """Break a complex request into sub-tasks targeting different agents/tools."""
    from cc_config import get_llm
    import uuid

    messages = state.get("messages", [])
    landscape = state.get("landscape", {})

    last_msg = messages[-1] if messages else None
    user_text = last_msg.content if last_msg and hasattr(last_msg, 'content') else ""

    all_agents = landscape.get("all_agents", landscape.get("agents", []))
    agents_info = json.dumps([
        {"id": a.get("agent_id"), "name": a.get("agent_name"), "description": a.get("description", ""),
         "is_data_agent": a.get("is_data_agent", False)}
        for a in all_agents if a.get("enabled", True)
    ][:50], indent=2)  # limit to 50 to keep prompt manageable

    # Build hints for recently created / session resources so the LLM
    # strongly prefers agents that were just built in this conversation.
    resource_hint = ""
    recently_created = state.get("recently_created_resources") or []
    session_res = state.get("session_resources") or []
    hint_items = recently_created + session_res
    if hint_items:
        agent_lines = []
        for r in hint_items:
            if r.get("type") == "agent" and r.get("id"):
                agent_lines.append(f"- Agent {r['id']}: {r.get('name', 'Unknown')}")
        if agent_lines:
            resource_hint = (
                "\n\nRECENTLY CREATED IN THIS SESSION — STRONGLY PREFER these agents "
                "when they match the request (they are VALID target_agent ids even "
                "if they do not appear in the Available agents list above):\n"
                + "\n".join(agent_lines)
            )

    _td_conv = _format_conversation_for_prompt(messages)
    _td_conv_block = (
        f"\nRecent conversation (for resolving references in the user request):\n{_td_conv}\n"
        if _td_conv else ""
    )

    decompose_prompt = f"""Break this user request into ordered sub-tasks. Each task targets either a platform agent OR a Command Center tool.

Available agents (set target_agent to the agent id):
{agents_info}{resource_hint}

Command Center tools (set target_tool to the tool name, leave target_agent null):
- search_documents: Search the document repository for files, contracts, invoices, policies, reports
- search_web: Search the internet for current information, news, weather, stock prices
- export_data: Export results to a downloadable file (Excel, CSV, PDF, JSON, text)
- generate_map: Create an interactive map from location or geographic data
- generate_image: Generate an image from a text description (DALL-E)
- send_email: Send an email, optionally with a file attachment from a prior export step
{_td_conv_block}
User request: {user_text}

Return a JSON array of tasks IN EXECUTION ORDER:
[{{"description": "what to do", "agent_input": "clean standalone question to send to the agent", "target_agent": "agent_id or null", "target_agent_name": "name or null", "is_data_agent": true_or_false, "target_tool": "tool_name or null"}}]

RULES:
- Each task must have EITHER target_agent OR target_tool — not both, not neither.
- "description" is a short human-readable summary of the step (shown in the UI).
- "agent_input" is the EXACT question to send to the target agent, phrased as if the end user asked that agent directly. It MUST NOT mention agent names/IDs, "route to", "using ... agent", or "return the results to the user" — those are orchestration details the agent must never see. Example: description "Query sales by state for last year using retail data agent ID 391" → agent_input "What were sales by state for last year?". For target_tool tasks, set agent_input equal to description.
- Set is_data_agent to match the agent's type from the list above. General agents are NOT data agents.
- Order matters: if Task 2 needs Task 1's results (e.g., search then export), Task 1 must come first. Results from earlier tasks are automatically passed to later tasks.
- Use CC tools for document search, web search, file export, maps, images, and email — these are NOT agent capabilities.
- Use agents for database queries and domain-specific questions ONLY. Agents CANNOT create, modify, configure, or delete platform resources (agents, connections, workflows, tools, schedules, MCP servers) — never create a task that asks an agent to build or change a platform resource.
- If the user names an agent that does NOT appear in the Available agents list, do NOT map the task to a similar-sounding agent — set target_agent to null and put the missing name in target_agent_name.
- When the user request references prior turns ("now also do X", "same but for Y"), use the recent conversation above to fill in the implied subject.
Only return the JSON array, nothing else."""
    decompose_prompt += _preferences_block(state)

    try:
        from cc_config import get_step_llm
        llm = get_step_llm("task_decomposition")
        _td_msgs = [
            SystemMessage(content="You are a task decomposition expert for an AI orchestration platform."),
            HumanMessage(content=decompose_prompt),
        ]
        _td_t0 = _trace_time.perf_counter()
        response = await llm.ainvoke(_td_msgs)
        trace_llm_call(state, node="decompose_tasks", step="task_decomposition",
                       messages=_td_msgs, response=response,
                       elapsed_ms=int((_trace_time.perf_counter() - _td_t0) * 1000), model_hint="full")

        raw = response.content.strip()
        # Try to parse JSON
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        tasks_data = json.loads(raw)

        # Known agents = landscape PLUS resources created this session — the
        # platform scan is cached/checkpointed, so an agent built moments ago
        # is often missing from it. Treating that staleness as "invented id"
        # broke the just-built data agent's first query (AIHUB-0015 retest:
        # 'no agent or tool was assigned').
        _known_ids = {str(a.get("agent_id")) for a in all_agents}
        _known_by_name = {
            " ".join(str(a.get("agent_name") or "").lower().split()): str(a.get("agent_id"))
            for a in all_agents if a.get("agent_name")
        }
        for r in hint_items:
            if r.get("type") == "agent" and r.get("id"):
                _known_ids.add(str(r["id"]))
                if r.get("name"):
                    _known_by_name[" ".join(str(r["name"]).lower().split())] = str(r["id"])

        sub_tasks = []
        for i, t in enumerate(tasks_data):
            sub_task = {
                "id": str(uuid.uuid4())[:8],
                "description": t.get("description", ""),
                # Clean question to send to the agent (no orchestration meta-text).
                # Falls back to description for older payloads / tool tasks.
                "agent_input": t.get("agent_input") or t.get("description", ""),
                "target_agent": t.get("target_agent"),
                "target_agent_name": t.get("target_agent_name"),
                "is_data_agent": bool(t.get("is_data_agent", True)),
                "target_tool": t.get("target_tool"),
                "status": "pending",
                "inputs": {},
                "outputs": {},
            }
            # AIHUB-0021 F3: never delegate to an agent id the LLM invented.
            # Repair by exact name first (the LLM may know the agent from the
            # conversation even when the scan is stale); only null when the
            # name is unknown too.
            _tname_norm = " ".join(str(sub_task.get("target_agent_name") or "").lower().split())
            if sub_task["target_agent"] and _known_ids and str(sub_task["target_agent"]) not in _known_ids:
                _repaired = _known_by_name.get(_tname_norm)
                if _repaired:
                    logger.info(
                        f"[decompose_tasks] repaired target_agent "
                        f"{sub_task['target_agent']} → {_repaired} by exact name "
                        f"'{sub_task['target_agent_name']}'"
                    )
                    sub_task["target_agent"] = _repaired
                else:
                    logger.warning(
                        f"[decompose_tasks] target_agent {sub_task['target_agent']} "
                        f"({sub_task['target_agent_name']}) not in landscape or session "
                        f"resources — nulling target"
                    )
                    sub_task["target_agent"] = None
            elif (not sub_task["target_agent"] and not sub_task.get("target_tool")
                    and _tname_norm and _known_by_name.get(_tname_norm)):
                # The LLM followed the "unknown agent → null id, keep name"
                # rule for an agent that DOES exist (created this session) —
                # fill the real id instead of failing the task.
                sub_task["target_agent"] = _known_by_name[_tname_norm]
                logger.info(
                    f"[decompose_tasks] resolved target_agent by name "
                    f"'{sub_task['target_agent_name']}' → {sub_task['target_agent']}"
                )

            # Deterministic backstop (AIHUB-0015 F1): even if the decomposer
            # LLM ignores the rule above, never delegate a platform mutation
            # to a chat agent — it would role-play the build and the aggregate
            # would report resources that were never created. Neuter the
            # target so execute_next_task fails the step honestly instead.
            # Scan agent_input (the clean instruction the agent receives) —
            # descriptions systematically embed "using ... data agent" text
            # that would false-positive the resource-noun match.
            if sub_task["target_agent"] and _is_explicit_build_request(
                sub_task["agent_input"] or sub_task["description"]
            ):
                logger.warning(
                    "[decompose_tasks] blocked platform-mutation delegation to "
                    f"agent {sub_task['target_agent']} ({sub_task['target_agent_name']}): "
                    f"{sub_task['description'][:120]}"
                )
                sub_task["target_agent"] = None
                sub_task["target_agent_name"] = None
                sub_task["builder_required"] = True
            sub_tasks.append(sub_task)

        logger.info(f"Decomposed into {len(sub_tasks)} sub-tasks")
        return {"sub_tasks": sub_tasks, "current_task_index": 0}

    except Exception as e:
        logger.error(f"Task decomposition failed: {e}")
        return {"sub_tasks": [], "current_task_index": 0}


# ─── Multi-step helpers ──────────────────────────────────────────────────


def _build_prior_task_context(completed_tasks: list, delegation_results: dict) -> str:
    """Build a context string from completed prior tasks for inter-task data passing.

    Subsequent tasks receive this so they can reference earlier results
    (e.g. Task 2 "export to Excel" sees Task 1's document search results).
    """
    parts = []
    for task in completed_tasks:
        if task.get("status") != "completed":
            continue
        tid = task.get("id", "")
        result = delegation_results.get(tid, {})
        text = result.get("text", "")
        if not text:
            continue
        source = task.get("target_agent_name") or task.get("target_tool") or "unknown"
        parts.append(
            f"[Step result — {source}: {task.get('description', '')}]\n{text}"
        )
    return "\n\n".join(parts)


async def _execute_cc_tool(
    tool_name: str,
    description: str,
    prior_context: str,
    state: dict,
) -> dict:
    """Execute a Command Center native tool within the multi-step pipeline.

    Unlike the converse node (which binds LangChain tools with structured args),
    multi-step tools receive a natural-language description plus prior task context.
    Simple tools use the description directly as the primary argument; complex tools
    (export, map, email) use a mini-LLM call to extract structured args from
    the description + prior_context.

    Returns a dict compatible with delegation results: {text, status}.
    """
    session_id = state.get("session_id", "cc-default")
    user_ctx = state.get("user_context") or {}
    user_id = str(user_ctx.get("user_id", "anonymous"))

    logger.info(f"[execute_cc_tool] {tool_name}: {description[:120]}")

    try:
        # ── search_documents ──────────────────────────────────────────────
        if tool_name == "search_documents":
            import httpx as _httpx
            from cc_config import get_base_url, AI_HUB_API_KEY

            url = f"{get_base_url()}/api/internal/document-search"
            headers = {
                "X-API-Key": AI_HUB_API_KEY,
                "Content-Type": "application/json",
                "Connection": "close",
            }
            async with _httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, json={"question": description}, headers=headers)

            if resp.status_code != 200:
                return {"text": f"Document search failed (HTTP {resp.status_code})", "status": "failed"}
            data = resp.json()
            if data.get("status") == "error":
                return {"text": f"Document search error: {data.get('message', 'Unknown')}", "status": "failed"}

            results = data.get("results", {})
            if isinstance(results, str):
                try:
                    results = json.loads(results)
                except (json.JSONDecodeError, TypeError):
                    pass

            result_str = json.dumps(results, default=str) if isinstance(results, (dict, list)) else str(results)
            if len(result_str) > 50000:
                if isinstance(results, dict) and "results" in results:
                    doc_results = results.get("results", [])
                    results["results"] = doc_results[:20]
                    results["results_truncated"] = True
                    result_str = json.dumps(results, default=str)
                else:
                    result_str = result_str[:50000] + "\n... (truncated)"
            return {"text": result_str, "status": "completed"}

        # ── search_web ────────────────────────────────────────────────────
        elif tool_name == "search_web":
            import os
            import requests as _requests

            api_key = os.environ.get("TAVILY_API_KEY", "")
            if not api_key:
                return {"text": "Web search not configured — no TAVILY_API_KEY.", "status": "failed"}

            resp = _requests.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"query": description, "include_answer": "basic", "max_results": 5},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            parts = []
            if data.get("answer"):
                parts.append(f"Summary: {data['answer']}")
            for r in data.get("results", [])[:5]:
                parts.append(f"- {r.get('title', '')}: {r.get('content', '')[:200]}")
            return {"text": "\n".join(parts) or "No results found.", "status": "completed"}

        # ── export_data ───────────────────────────────────────────────────
        elif tool_name == "export_data":
            if not prior_context:
                return {"text": "No data from prior steps available to export.", "status": "failed"}

            # Detect format from description
            desc_lower = description.lower()
            fmt = "excel"
            for candidate in ["csv", "pdf", "json", "text"]:
                if candidate in desc_lower:
                    fmt = candidate
                    break

            # Use LLM to structure prior results into exportable rows
            from cc_config import get_step_llm
            llm = get_step_llm("tool_export_structurer")
            _exp_msgs = [HumanMessage(content=(
                f"Extract data from these results into a JSON array of flat objects for "
                f"{fmt.upper()} export.\n\n"
                f"Results:\n{prior_context[:15000]}\n\n"
                f"Task: {description}\n\n"
                f"Return ONLY a JSON array of objects. Example: "
                f'[{{"Title": "Doc 1", "Date": "2024-01-01", "Summary": "..."}}]\n'
                f"If results contain documents, extract key fields (title, date, type, summary). "
                f"If no structured data can be extracted, return []."
            ))]
            _exp_t0 = _trace_time.perf_counter()
            extract_resp = await llm.ainvoke(_exp_msgs)
            trace_llm_call(state, node="execute_next_task", step="tool_export_structurer",
                           messages=_exp_msgs, response=extract_resp,
                           elapsed_ms=int((_trace_time.perf_counter() - _exp_t0) * 1000), model_hint="mini")
            raw = extract_resp.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            try:
                rows_data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return {"text": "Could not structure the data for export.", "status": "failed"}

            if not rows_data or not isinstance(rows_data, list) or not isinstance(rows_data[0], dict):
                return {"text": "No tabular data could be extracted for export.", "status": "failed"}

            from command_center.artifacts.artifact_models import ArtifactType
            format_map = {
                "csv": ArtifactType.CSV, "excel": ArtifactType.EXCEL,
                "pdf": ArtifactType.PDF, "json": ArtifactType.JSON,
                "text": ArtifactType.TEXT,
            }
            artifact_type = format_map.get(fmt, ArtifactType.EXCEL)

            col_headers = list(rows_data[0].keys())
            rows = [[str(item.get(h, "")) for h in col_headers] for item in rows_data]
            table_blocks = [{"type": "table", "title": "Export", "headers": col_headers, "rows": rows}]

            from routes.artifacts import _get_artifact_manager
            mgr = _get_artifact_manager()
            import re as _re
            file_name = _re.sub(r'[^a-z0-9_]+', '_', description.lower())[:50].strip('_') or "export"

            meta = mgr.create_from_blocks(
                blocks=table_blocks, artifact_type=artifact_type,
                name=file_name, session_id=f"{user_id}/{session_id}",
            )
            if not meta:
                return {"text": f"Failed to create {fmt} file.", "status": "failed"}

            block = meta.to_content_block()
            block["description"] = description
            logger.info(f"[execute_cc_tool] Artifact: {meta.artifact_id}, {meta.name}, {meta.size_display}")
            return {"text": json.dumps(block), "status": "completed"}

        # ── generate_map ──────────────────────────────────────────────────
        elif tool_name == "generate_map":
            if not prior_context:
                return {"text": "No location data from prior steps to map.", "status": "failed"}

            from cc_config import get_step_llm
            llm = get_step_llm("tool_map_structurer")
            _map_msgs = [HumanMessage(content=(
                f"Extract location data from these results for a map.\n\n"
                f"Results:\n{prior_context[:15000]}\n\n"
                f"Task: {description}\n\n"
                f"Return a JSON object with 'markers' and/or 'regions' arrays:\n"
                f'- Markers: [{{"lat": 40.7, "lng": -74.0, "label": "NYC", "popup": "Details"}}]\n'
                f'- Regions: [{{"name": "California", "value": 5000, "label": "CA: 5000"}}]\n'
                f"Return ONLY the JSON object."
            ))]
            _map_t0 = _trace_time.perf_counter()
            extract_resp = await llm.ainvoke(_map_msgs)
            trace_llm_call(state, node="execute_next_task", step="tool_map_structurer",
                           messages=_map_msgs, response=extract_resp,
                           elapsed_ms=int((_trace_time.perf_counter() - _map_t0) * 1000), model_hint="mini")
            raw = extract_resp.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return {"text": "Could not extract location data for mapping.", "status": "failed"}

            markers = data.get("markers", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            regions = data.get("regions", []) if isinstance(data, dict) else []

            if not markers and not regions:
                return {"text": "No valid location data found.", "status": "failed"}

            if markers:
                lats = [float(m["lat"]) for m in markers if "lat" in m]
                lngs = [float(m["lng"]) for m in markers if "lng" in m]
                center = [sum(lats) / len(lats), sum(lngs) / len(lngs)] if lats else [39.8, -98.5]
            else:
                center = [39.8, -98.5]

            map_block = {"type": "map", "title": description[:100], "center": center, "zoom": 4}
            if markers:
                map_block["markers"] = markers
            if regions:
                map_block["regions"] = regions
            return {"text": json.dumps([map_block]), "status": "completed"}

        # ── generate_image ────────────────────────────────────────────────
        elif tool_name == "generate_image":
            import openai
            import os
            from api_keys_config import get_active_openai_key
            from cc_config import CC_IMAGE_MODEL
            from command_center_service.graph.image_params import build_image_generate_kwargs
            import config as cfg

            api_key = get_active_openai_key() or getattr(cfg, 'OPENAI_API_KEY', None) or os.environ.get("OPENAI_API_KEY")
            if not api_key:
                return {"text": "Image generation not configured (no OpenAI API key).", "status": "failed"}

            client = openai.OpenAI(api_key=api_key)
            gen_kwargs = build_image_generate_kwargs(CC_IMAGE_MODEL, description, "1024x1024")
            response = client.images.generate(**gen_kwargs)
            b64 = response.data[0].b64_json
            block = {"type": "image", "src": f"data:image/png;base64,{b64}", "alt": description[:200]}
            return {"text": json.dumps([block]), "status": "completed"}

        # ── send_email ────────────────────────────────────────────────────
        elif tool_name == "send_email":
            from cc_config import get_step_llm
            llm = get_step_llm("tool_email_extractor")
            _em_msgs = [HumanMessage(content=(
                f"Extract email parameters from this task.\n\n"
                f"Task: {description}\n"
                f"Prior results:\n{(prior_context or 'none')[:5000]}\n\n"
                f'Return JSON: {{"to_address": "...", "subject": "...", "message": "..."}}\n'
                f"Return ONLY the JSON object."
            ))]
            _em_t0 = _trace_time.perf_counter()
            extract_resp = await llm.ainvoke(_em_msgs)
            trace_llm_call(state, node="execute_next_task", step="tool_email_extractor",
                           messages=_em_msgs, response=extract_resp,
                           elapsed_ms=int((_trace_time.perf_counter() - _em_t0) * 1000), model_hint="mini")
            raw = extract_resp.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            try:
                params = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return {"text": "Could not extract email parameters.", "status": "failed"}

            to_addr = params.get("to_address", "")
            subject = params.get("subject", "")
            message_body = params.get("message", "")
            if not to_addr or not subject:
                return {"text": f"Missing email parameters: to={to_addr}, subject={subject}", "status": "failed"}

            import os
            import requests as _requests
            api_url = os.environ.get("AI_HUB_API_URL", "").rstrip("/")
            api_key_env = os.environ.get("API_KEY", "")
            if not api_url or not api_key_env:
                return {"text": "Email service not configured (AI_HUB_API_URL or API_KEY missing).", "status": "failed"}

            email_data = {"to": [to_addr.strip()], "subject": subject, "body": message_body}
            resp = _requests.post(
                f"{api_url}/api/notifications/email",
                headers={"X-API-Key": api_key_env, "X-License-Key": api_key_env, "Content-Type": "application/json"},
                json=email_data, timeout=30,
            )
            result = resp.json()
            if result.get("success"):
                return {"text": f"Email sent to {to_addr}. Subject: {subject}", "status": "completed"}
            return {"text": f"Email failed: {result.get('error', 'Unknown')}", "status": "failed"}

        # ── unknown tool ──────────────────────────────────────────────────
        else:
            return {
                "text": f"Unknown CC tool: {tool_name}. Available: search_documents, "
                        f"search_web, export_data, generate_map, generate_image, send_email",
                "status": "failed",
            }

    except Exception as e:
        logger.error(f"[execute_cc_tool] {tool_name} failed: {e}", exc_info=True)
        return {"text": f"Tool '{tool_name}' failed: {str(e)}", "status": "failed"}


# ─── Node: execute_next_task ──────────────────────────────────────────────

async def execute_next_task(state: CommandCenterState) -> dict:
    """Execute the next sub-task in the queue.

    Supports three execution modes:
    1. Agent delegation — target_agent is set → delegate_to_agent()
    2. CC-native tool — target_tool is set → _execute_cc_tool()
    3. Fallback — neither set → error (decomposition was incomplete)

    Inter-task data passing: prior completed tasks' outputs are compiled
    into a context string and provided to subsequent tasks so they can
    reference earlier results (e.g. Task 2 "export to Excel" sees
    Task 1's document search results).
    """
    sub_tasks = state.get("sub_tasks", [])
    current_idx = state.get("current_task_index", 0)
    delegation_results = dict(state.get("delegation_results", {}))

    if current_idx >= len(sub_tasks):
        return {"current_task_index": current_idx}

    task = sub_tasks[current_idx]
    task_id = task.get("id", str(current_idx))
    task["status"] = "running"

    logger.info(f"Executing task {current_idx + 1}/{len(sub_tasks)}: {task.get('description', '')}")

    # ── Build context from completed prior tasks ──────────────────────
    prior_context = _build_prior_task_context(sub_tasks[:current_idx], delegation_results)

    # ── Build conversation history for agent coherence ────────────────
    # Forward ONLY the curated side-conversation this CC session has had with
    # THIS specific agent — never CC's own orchestration conversation with the
    # user (which would leak routing meta-text like "use agent 391" into the
    # agent's NLQ classifiers). The per-agent thread is seeded from clean
    # agent_input questions + the agent's answers. Empty for a fresh agent.
    _cur_agent_id = task.get("target_agent")
    conversation_history = []
    if _cur_agent_id:
        try:
            from graph.delegation_log import get_thread_history
            conversation_history = get_thread_history(state.get("session_id", ""), _cur_agent_id)
        except Exception:
            conversation_history = []

    try:
        if task.get("target_agent"):
            # ── Mode 1: Agent delegation ──────────────────────────────
            from command_center.orchestration.delegator import delegate_to_agent
            from graph.tracing import trace_log
            import time as _time

            agent_id = str(task["target_agent"])
            # Send the CLEAN question (agent_input), never the human-readable
            # description which may embed orchestration/routing meta-text.
            question = str(task.get("agent_input") or task.get("description") or "")
            _clean_question = question  # preserved for the side-conversation log
            is_data = task.get("is_data_agent", True)

            # Prepend prior task results so the agent has context
            if prior_context:
                question = (
                    f"Context from prior steps:\n{prior_context}\n\n"
                    f"Current task: {question}"
                )

            trace_log(
                state,
                event_type="delegate_start",
                node="delegate_to_agent",
                payload={
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "is_data_agent": is_data,
                    "question_preview": question[:400],
                },
            )
            t0 = _time.perf_counter()
            result = await delegate_to_agent(
                agent_id=agent_id,
                question=question,
                conversation_history=conversation_history or None,
                is_data_agent=is_data,
                session_id=state.get("session_id", "cc-default"),
                user_context=state.get("user_context"),
            )
            elapsed_ms = int((_time.perf_counter() - t0) * 1000)
            trace_log(
                state,
                event_type="delegate_end",
                node="delegate_to_agent",
                payload={
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "elapsed_ms": elapsed_ms,
                    "status": (result or {}).get("status") if isinstance(result, dict) else None,
                    "text_preview": str((result or {}).get("text") or result or "")[:800],
                },
            )

            # Record this exchange as a side-conversation thread for the UI panel.
            try:
                from graph.delegation_log import record_turn
                record_turn(
                    state.get("session_id", ""),
                    agent_id,
                    task.get("target_agent_name") or f"Agent #{agent_id}",
                    "data" if is_data else "general",
                    _clean_question,
                    (result or {}).get("text") if isinstance(result, dict) else str(result),
                )
            except Exception:
                pass

        elif task.get("target_tool"):
            # ── Mode 2: CC-native tool execution ──────────────────────
            from graph.tracing import trace_log
            import time as _time

            tool_name = str(task["target_tool"])
            description = str(task.get("description") or "")

            trace_log(
                state,
                event_type="tool_start",
                node=f"cc_tool:{tool_name}",
                payload={
                    "task_id": task_id,
                    "description_preview": description[:400],
                    "has_prior_context": bool(prior_context),
                },
            )
            t0 = _time.perf_counter()
            result = await _execute_cc_tool(
                tool_name=tool_name,
                description=description,
                prior_context=prior_context,
                state=state,
            )
            elapsed_ms = int((_time.perf_counter() - t0) * 1000)
            trace_log(
                state,
                event_type="tool_end",
                node=f"cc_tool:{tool_name}",
                payload={
                    "task_id": task_id,
                    "elapsed_ms": elapsed_ms,
                    "status": result.get("status"),
                    "text_preview": str(result.get("text") or "")[:800],
                },
            )

        elif task.get("builder_required"):
            # ── Mode 3a: Platform mutation blocked from agent delegation ──
            # (AIHUB-0015 F1 backstop in decompose_tasks). Fail honestly —
            # never let a chat agent role-play a build.
            logger.warning(f"[execute_next_task] Task {task_id} requires the Builder — not executed")
            result = {
                "text": (
                    f"Not executed — this step creates or modifies a platform resource, "
                    f"which only the Builder can do: {task.get('description', '')}. "
                    f"Send it as its own request (e.g. \"create ...\") and it will be "
                    f"routed to the Builder."
                ),
                "status": "failed",
            }

        else:
            # ── Mode 3: No target — decomposition was incomplete ──────
            logger.warning(f"[execute_next_task] Task {task_id} has no target_agent or target_tool")
            result = {
                "text": f"Task could not be executed — no agent or tool assigned: {task.get('description', '')}",
                "status": "failed",
            }

        task["status"] = "completed" if result.get("status") != "failed" else "failed"
        task["outputs"] = result if isinstance(result, dict) else {"text": str(result)}
        delegation_results[task_id] = task["outputs"]

    except Exception as e:
        logger.error(f"Task execution failed: {e}", exc_info=True)
        task["status"] = "failed"
        task["error"] = str(e)
        delegation_results[task_id] = {"error": str(e)}

    # Update tasks list
    updated_tasks = list(sub_tasks)
    updated_tasks[current_idx] = task

    return {
        "sub_tasks": updated_tasks,
        "current_task_index": current_idx + 1,
        "delegation_results": delegation_results,
    }


# ─── Node: aggregate ──────────────────────────────────────────────────────

async def aggregate(state: CommandCenterState) -> dict:
    """Combine results from multiple delegations into a coherent response.

    Rich content blocks (artifacts, maps, tables, images) are extracted from
    task results BEFORE being sent to the LLM, then appended directly to the
    final response.  This prevents the LLM from fabricating download URLs or
    mangling structured data.
    """
    from cc_config import get_llm, STRUCTURED_RESPONSE_FORMAT

    sub_tasks = state.get("sub_tasks", [])
    delegation_results = state.get("delegation_results", {})
    messages = state.get("messages", [])

    last_user_msg = ""
    for msg in reversed(messages):
        if hasattr(msg, 'type') and msg.type == 'human':
            last_user_msg = msg.content
            break
        elif isinstance(msg, dict) and msg.get('role') == 'user':
            last_user_msg = msg.get('content', '')
            break

    # ── Extract rich blocks from task results ──────────────────────────
    # Artifact, map, table, image, kpi blocks must be preserved verbatim —
    # the LLM cannot faithfully reproduce download URLs, artifact IDs, or
    # encoded image data.  We replace them in the prompt with a placeholder
    # so the LLM knows the content exists but doesn't try to recreate it.
    RICH_BLOCK_TYPES = {"artifact", "map", "table", "image", "kpi"}
    preserved_blocks = []  # Rich blocks to append after LLM synthesis

    results_summary = []
    for task in sub_tasks:
        tid = task.get("id", "")
        result = delegation_results.get(tid, {})
        result_text = result.get("text", result.get("error", str(result)))

        # Try to parse result text as a rich block or array of blocks
        block_placeholder = None
        try:
            parsed = json.loads(result_text) if isinstance(result_text, str) else result_text
            if isinstance(parsed, dict) and parsed.get("type") in RICH_BLOCK_TYPES:
                # Single rich block (e.g., artifact from export_data)
                preserved_blocks.append(parsed)
                block_type = parsed.get("type")
                block_name = parsed.get("name", "file")
                block_placeholder = f"[{block_type.upper()}: {block_name} — included below, do NOT reproduce]"
            elif isinstance(parsed, list):
                rich = [b for b in parsed if isinstance(b, dict) and b.get("type") in RICH_BLOCK_TYPES]
                text = [b for b in parsed if isinstance(b, dict) and b.get("type") not in RICH_BLOCK_TYPES]
                if rich:
                    preserved_blocks.extend(rich)
                    names = ", ".join(b.get("name", b.get("type", "block")) for b in rich)
                    block_placeholder = f"[RICH CONTENT: {names} — included below, do NOT reproduce]"
                    if text:
                        # Keep non-rich blocks as text for the LLM
                        block_placeholder += "\n" + json.dumps(text)
        except (json.JSONDecodeError, TypeError):
            pass

        # Delegated artifact handles (large query results / files a general
        # agent produced) arrive as a separate `artifacts` list, NOT embedded
        # in `text` — preserve them as download chips so the multi-step path
        # doesn't strand them (previously dropped: aggregate read only text).
        for _art in (result.get("artifacts") or []):
            if isinstance(_art, dict):
                _ab = dict(_art)
                _ab["type"] = "artifact"
                preserved_blocks.append(_ab)
                if block_placeholder is None:
                    block_placeholder = f"[FILE: {_art.get('name', 'file')} — attached below, do NOT reproduce]"

        display_result = block_placeholder or str(result_text)[:500]
        results_summary.append({
            "task": task.get("description", ""),
            "agent": task.get("target_agent_name", task.get("target_agent", "self")),
            "status": task.get("status", "unknown"),
            "result": display_result,
        })

    if preserved_blocks:
        logger.info(
            f"[aggregate] Preserved {len(preserved_blocks)} rich block(s): "
            f"{[b.get('type') for b in preserved_blocks]}"
        )

    aggregate_prompt = f"""The user asked: {last_user_msg}

I delegated to multiple agents/tools. Here are the results:
{json.dumps(results_summary, indent=2)}

Synthesize these results into a clear, unified response for the user.
HONESTY RULES (mandatory):
- NEVER state or imply that a platform resource (agent, connection, workflow, tool, schedule, MCP server) was created, configured, or modified — delegated agents cannot do that, and no step here was verified against the platform. If an agent's reply claims it built something, report it as an unconfirmed claim, not a completed action.
- Steps with status "failed" MUST be reported as failed. Do not soften, omit, or reframe them as successes.
- Do not use blanket success language ("everything was set up successfully") unless every step has status "completed".
{"NOTE: Some results contain rich content (artifacts, maps, etc.) that will be attached automatically. Do NOT try to recreate download links, artifact blocks, or map data — just reference them naturally (e.g., 'The Excel file is available for download below')." if preserved_blocks else ""}
{STRUCTURED_RESPONSE_FORMAT}"""
    aggregate_prompt += _preferences_block(state)

    try:
        llm = get_llm(mini=False, streaming=False)
        _agg_msgs = [
            SystemMessage(content="You are the AI Hub Command Center synthesizing results from multiple agents."),
            HumanMessage(content=aggregate_prompt),
        ]
        _agg_t0 = _trace_time.perf_counter()
        response = await llm.ainvoke(_agg_msgs)
        trace_llm_call(state, node="aggregate", step="aggregation",
                       messages=_agg_msgs, response=response,
                       elapsed_ms=int((_trace_time.perf_counter() - _agg_t0) * 1000), model_hint="full")

        # ── Deterministic honesty footer (AIHUB-0015 F1 / 0021 F1) ──────
        # Computed from real task statuses and appended AFTER synthesis so
        # the LLM cannot soften or omit it — mirrors the builder path's
        # _verification_footer guarantee.
        footer_lines = []
        _failed_tasks = [t for t in sub_tasks if t.get("status") == "failed"]
        if _failed_tasks:
            _failed_names = "; ".join(
                (t.get("description") or "step")[:80] for t in _failed_tasks[:5]
            )
            footer_lines.append(
                f"❌ {len(_failed_tasks)} step(s) did not succeed: {_failed_names}"
            )
        if any(t.get("builder_required") for t in sub_tasks):
            footer_lines.append(
                "⚠️ No platform resources were created or modified in this turn — "
                "creating or changing agents, connections, workflows, tools, or "
                "schedules requires the Builder. Send that request on its own and "
                "it will be routed there."
            )
        honesty_footer = "\n\n".join(footer_lines)

        # ── Merge LLM text + preserved rich blocks ──────────────────────
        if preserved_blocks or honesty_footer:
            # Parse the LLM response as blocks, then append rich blocks
            llm_content = response.content if hasattr(response, 'content') else str(response)
            all_blocks = []

            # Try to parse LLM response as JSON blocks
            try:
                parsed_response = json.loads(llm_content)
                if isinstance(parsed_response, list):
                    all_blocks.extend(parsed_response)
                else:
                    all_blocks.append({"type": "text", "content": llm_content})
            except (json.JSONDecodeError, TypeError):
                all_blocks.append({"type": "text", "content": llm_content})

            # Append the preserved rich blocks (artifacts, maps, etc.)
            all_blocks.extend(preserved_blocks)

            if honesty_footer:
                all_blocks.append({"type": "text", "content": honesty_footer})

            merged_content = json.dumps(all_blocks)
            return {"messages": [AIMessage(content=merged_content)]}
        else:
            return {"messages": [response]}

    except Exception as e:
        logger.error(f"Aggregation failed: {e}")
        # Even on failure, return any preserved blocks
        if preserved_blocks:
            error_blocks = [{"type": "text", "content": f"Failed to aggregate results: {str(e)}"}]
            error_blocks.extend(preserved_blocks)
            return {"messages": [AIMessage(content=json.dumps(error_blocks))]}
        error_content = json.dumps([{"type": "text", "content": f"Failed to aggregate results: {str(e)}"}])
        return {"messages": [AIMessage(content=error_content)]}


# ─── Node: render_response ────────────────────────────────────────────────

async def render_response(state: CommandCenterState) -> dict:
    """Convert raw results into rich_content blocks for the frontend."""
    # Pass-through node that preserves important state.
    # Explicitly pass through active_delegation to ensure it's in final state.
    result = {}
    if state.get("active_delegation"):
        result["active_delegation"] = state["active_delegation"]
    return result


def _extract_created_resources(plan_data: dict) -> list:
    """Extract created resources (agents, connections, workflows) from builder plan steps."""
    resources = []
    for step in plan_data.get("steps", []):
        if not isinstance(step, dict):
            continue
        # Fail-closed (Phase 4): never record a resource from a step that failed or
        # whose read-back DISPROVED the creation. (verified=None/UNVERIFIED is still
        # recorded — it may have been created — and the message flags the uncertainty.)
        if str(step.get("status") or "").lower() in ("failed", "error"):
            continue
        step_result = step.get("result")
        if not isinstance(step_result, dict):
            continue
        if step_result.get("verified") is False:
            continue
        data = step_result.get("data", {})
        if not isinstance(data, dict):
            continue
        if data.get("agent_id"):
            resources.append({
                "type": "agent",
                "id": data["agent_id"],
                "name": data.get("agent_description", data.get("agent_name", f"Agent #{data['agent_id']}")),
            })
        if data.get("connection_id"):
            resources.append({
                "type": "connection",
                "id": data["connection_id"],
                "name": data.get("connection_name", f"Connection #{data['connection_id']}"),
            })
        # WorkflowAgent builds put saved_workflow_id/name at the delegation-result TOP
        # LEVEL (builder nodes.py:1860), not under .data — so check both, or workflow
        # builds are never recorded (W5b #17 + a general workflow-tracking gap).
        # The plan-only compile path stores the id ONLY under result["compile_result"]
        # ["workflow_id"] (builder nodes.py:3654), so read that too — but ONLY on a clean
        # compile: a draft/errored compile must never be surfaced as a created resource
        # (stays fail-closed; _plan_has_unready_artifact independently suppresses the
        # success announcement, and the step guards above already drop failed steps).
        cr = step_result.get("compile_result") if isinstance(step_result.get("compile_result"), dict) else {}
        cr_ok = str(cr.get("status") or "").lower() not in ("draft", "error")
        wf_id = (data.get("workflow_id") or data.get("saved_workflow_id")
                 or step_result.get("saved_workflow_id") or step_result.get("workflow_id")
                 or (cr.get("workflow_id") if cr_ok else None))
        if wf_id:
            resources.append({
                "type": "workflow",
                "id": wf_id,
                "name": (data.get("saved_workflow_name")
                         or step_result.get("saved_workflow_name")
                         or (cr.get("workflow_name") if cr_ok else None)
                         or f"Workflow #{wf_id}"),
            })
    return resources


def _plan_has_unready_artifact(plan_data: dict) -> bool:
    """W5b (#17) draft-safety: True if any step indicates a saved-but-not-ready artifact
    (a workflow saved as a DRAFT / compile error). Used to keep the deterministic success
    fallback from announcing "✅ Created" for a draft/invalid build (a draft still has a
    saved_workflow_id and a non-failed step status, so existence alone is not success)."""
    for step in (plan_data or {}).get("steps", []) if isinstance(plan_data, dict) else []:
        if not isinstance(step, dict):
            continue
        res = step.get("result") if isinstance(step.get("result"), dict) else {}
        if res.get("saved_as_draft"):
            return True
        cr = res.get("compile_result") if isinstance(res.get("compile_result"), dict) else {}
        if str(cr.get("status") or "").lower() in ("draft", "error"):
            return True
        # AIHUB-0016 F1: executor steps now carry the read-back verifier's
        # workflow_validation state in result.data.
        data = res.get("data") if isinstance(res.get("data"), dict) else {}
        wfv = data.get("workflow_validation")
        if isinstance(wfv, dict) and wfv.get("is_valid") is False:
            return True
    return False


def _derive_build_state(result: dict) -> dict:
    """W3a (#15): derive the builder-delegation state (build_status, created_resources,
    completed_at, builder_session_id) from a delegate_to_builder result — the same way the
    build node does — so the converse and gather_data build consumers report the HONEST
    plan outcome instead of a hardcoded 'in_progress' (which left the session pinned to the
    builder forever and never surfaced created resources). Safe on a plan-less result
    (returns 'in_progress', matching the build node)."""
    from datetime import datetime as _dt
    out = {"build_status": "in_progress", "created_resources": [],
           "completed_at": None, "builder_session_id": None}
    if not isinstance(result, dict):
        return out
    out["builder_session_id"] = result.get("builder_session_id")
    latest_plan = result.get("plan")
    if isinstance(latest_plan, dict):
        plan_status = str(latest_plan.get("status") or "").lower()
        if plan_status in ("completed", "partial", "failed"):
            out["build_status"] = plan_status
            out["completed_at"] = _dt.now().isoformat()
            out["created_resources"] = _extract_created_resources(latest_plan)
    return out


def _summarize_verification(plan_data: dict) -> dict:
    """Classify builder plan steps by read-back verification outcome (Phase 4 —
    fail-closed messaging). A step is 'unverified' only when a read-back was
    ATTEMPTED but couldn't confirm (executor set verified=None AND a
    verification_detail) — steps with no verification spec (detail is None, e.g.
    reads) are not flagged. Returns {verified, unverified, failed} lists of
    {label, detail}."""
    verified, unverified, failed, drafts, skipped = [], [], [], [], []
    steps = plan_data.get("steps", []) if isinstance(plan_data, dict) else []
    for step in steps:
        if not isinstance(step, dict):
            continue
        status = str(step.get("status") or "").lower()
        label = step.get("description") or step.get("capability_id") or step.get("action") or "step"
        result = step.get("result") if isinstance(step.get("result"), dict) else {}
        v = result.get("verified")
        vd = result.get("verification_detail")
        if status in ("failed", "error"):
            failed.append({"label": label, "detail": result.get("error") or vd})
        elif status == "skipped":
            # AIHUB-0015 retest F2: dependency-skipped steps were invisible in
            # the summary, so the execution record read as more complete than
            # it was.
            skipped.append({"label": label, "detail": result.get("message")})
        elif v is False:            # DISPROVED (defensive — Phase 2 also sets status=failed)
            failed.append({"label": label, "detail": vd})
        elif v is True:
            verified.append({"label": label, "detail": vd})
        elif v is None and vd:      # read-back attempted but inconclusive/errored
            unverified.append({"label": label, "detail": vd})

        # AIHUB-0016 F1: a workflow can save (and read-back-verify) yet be a
        # non-runnable DRAFT. Collect draft state so messaging distinguishes
        # "ready" from "saved as draft — needs fixes".
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        wfv = data.get("workflow_validation") if isinstance(data.get("workflow_validation"), dict) else None
        is_draft = bool(result.get("saved_as_draft")) or (wfv is not None and wfv.get("is_valid") is False)
        if is_draft and status not in ("failed", "error"):
            problems = (wfv or {}).get("problems") or result.get("workflow_validation_errors") or []
            drafts.append({"label": label, "problems": [str(p) for p in problems][:5]})

    return {"verified": verified, "unverified": unverified, "failed": failed,
            "drafts": drafts, "skipped": skipped}


def _render_verification_for_prompt(summary: dict) -> str:
    if not any(summary.get(k) for k in ("verified", "unverified", "failed", "drafts", "skipped")):
        return "(no mutating steps were verified this turn)"
    lines = []
    draft_labels = {d.get("label") for d in summary.get("drafts", [])}
    for v in summary.get("verified", []):
        if v.get("label") in draft_labels:
            lines.append(f"VERIFIED: {v['label']}")
        else:
            # Read-back confirmed and no draft state — the resource is real
            # and usable; say so plainly (AIHUB-0016 retest F1: valid builds
            # were never called ready).
            lines.append(f"VERIFIED (confirmed present by read-back — you may call it ready): {v['label']}")
    for u in summary.get("unverified", []):
        lines.append(f"UNVERIFIED (action returned success but read-back could NOT confirm): {u['label']}")
    for f in summary.get("failed", []):
        lines.append(f"FAILED: {f['label']}")
    for s in summary.get("skipped", []):
        lines.append(f"SKIPPED (a prerequisite failed — this step did NOT run): {s['label']}")
    for d in summary.get("drafts", []):
        probs = "; ".join(d.get("problems") or []) or "validation failed"
        lines.append(
            f"SAVED AS DRAFT (persisted but NOT runnable — never call it ready or "
            f"complete): {d['label']} — needs fixes: {probs}"
        )
    return "\n".join(lines)


def _verification_footer(summary: dict) -> str:
    """Deterministic honesty footer, appended whenever any step failed or could not
    be verified. This is the fail-closed guarantee: the truth is present regardless
    of what the distiller LLM chose to write."""
    blocks = []
    if summary.get("failed"):
        lines = "\n".join(
            f"- {f['label']}" + (f" — {f['detail']}" if f.get("detail") else "")
            for f in summary["failed"][:6]
        )
        blocks.append(f"**❌ {len(summary['failed'])} step(s) did not succeed:**\n{lines}")
    if summary.get("drafts"):
        lines = "\n".join(
            f"- {d['label']}"
            + (f" — needs fixes: {'; '.join(d['problems'])}" if d.get("problems") else "")
            for d in summary["drafts"][:6]
        )
        blocks.append(
            f"**📝 {len(summary['drafts'])} workflow(s) saved as DRAFT — not runnable "
            f"until fixed:**\n{lines}"
        )
    if summary.get("skipped"):
        lines = "\n".join(f"- {s['label']}" for s in summary["skipped"][:6])
        blocks.append(
            f"**⏭️ {len(summary['skipped'])} step(s) skipped because a prerequisite "
            f"failed:**\n{lines}"
        )
    if summary.get("unverified"):
        lines = "\n".join(f"- {u['label']}" for u in summary["unverified"][:6])
        blocks.append(
            f"**⚠️ {len(summary['unverified'])} step(s) reported success but could not be "
            f"independently confirmed** — please double-check:\n{lines}"
        )
    return ("\n\n---\n" + "\n\n".join(blocks)) if blocks else ""


def _persisted_steps_footer(wf_saved: dict) -> str:
    """AIHUB-0038: deterministic saved-workflow read-back footer.

    Appended AFTER the distiller whenever the delegator captured a
    `workflow_saved` read-back, so the authoritative step list survives no
    matter what the distiller LLM wrote (same fail-closed principle as
    _verification_footer, but for the SUCCESS direction's step list)."""
    try:
        node_types = [t for t in (wf_saved.get("node_types") or []) if t]
        if not node_types:
            return ""
        wf_id = wf_saved.get("workflow_id")
        id_part = f" (ID {wf_id})" if wf_id else ""
        status_part = (" — saved as a DRAFT, not yet runnable"
                       if wf_saved.get("status") == "draft" else "")
        return (
            f"\n\n---\n📋 **Saved workflow{id_part}{status_part} — authoritative read-back. "
            f"It contains exactly these {len(node_types)} step(s):** "
            + " → ".join(node_types)
            + ". Any step not listed here is NOT in the workflow."
        )
    except Exception:
        return ""


def _deterministic_builder_summary(summary: dict, plan_data: dict) -> Optional[str]:
    """W5 (#17): fail-closed the SUCCESS direction. When the distiller LLM is unavailable
    (crash/timeout), synthesize an honest user-facing message from the verified plan
    instead of the generic "couldn't format it safely" apology — otherwise a fully-verified
    success shows a scary apology (the Phase-4 footer only fires for failed/unverified).
    Returns None when there is nothing verifiable to report, so the caller keeps its own
    fallback. On a pure verified success this names created resources; on a mixed/failure
    outcome it returns a short neutral lead-in and lets the deterministic footer (appended
    by the caller) carry the ❌/⚠️ specifics."""
    verified = summary.get("verified") or []
    failed = summary.get("failed") or []
    unverified = summary.get("unverified") or []
    if not (verified or failed or unverified):
        # No read-back verification signal — e.g. a delegation-path workflow build
        # (executor steps now verify workflows.create, but delegation auto-saves
        # carry no verified field). W5b (#17): fall through to the plan's created resources so
        # a genuine workflow build isn't shown the generic apology on a distiller crash —
        # but stay fail-closed: announce ONLY a clean success, never a draft/errored/partial
        # build (a saved draft also has an id + a non-failed step status).
        plan_status = str((plan_data or {}).get("status") or "").lower()
        created = _extract_created_resources(plan_data or {})
        if created and plan_status not in ("failed", "partial") and not _plan_has_unready_artifact(plan_data):
            names = ", ".join(f'{c.get("type", "item")} "{c.get("name")}"' for c in created[:6])
            return f"✅ Done. Created: {names}."
        return None  # ambiguous / draft / failed → keep the caller's fallback (never false-claim)

    if verified and not failed and not unverified and not summary.get("drafts"):
        created = _extract_created_resources(plan_data or {})
        if created:
            names = ", ".join(f'{c.get("type", "item")} "{c.get("name")}"' for c in created[:6])
            return f"✅ Done. Created: {names}."
        labels = "; ".join(v.get("label", "step") for v in verified[:6])
        return f"✅ Done — completed and verified: {labels}."

    # Mixed / failure: neutral lead-in; the caller's _verification_footer adds the detail.
    return "Here's the outcome of your request:"


# ─── Node: build ──────────────────────────────────────────────────────────

async def build(state: CommandCenterState) -> dict:
    """Delegate build/create requests to the Builder Agent service.

    Acts as a middleman — relays builder responses (including questions)
    back to the user, and passes user answers back to the builder.
    """
    # Platform mutations are Developer+ only (unless CC_BUILD_ALLOW_ALL_USERS).
    # Refuse early so we never spin up a builder session for a user who can't
    # build. The builder service enforces the same rule authoritatively.
    if not _build_allowed(state):
        logger.info("[build] refused — user lacks Developer role for platform mutations")
        return {"messages": [AIMessage(content=_BUILD_DENIED_MSG)]}

    from command_center.orchestration.delegator import delegate_to_builder

    messages = state.get("messages", [])
    last_msg = messages[-1] if messages else None
    user_text = last_msg.content if last_msg and hasattr(last_msg, 'content') else ""

    # ── Bare confirmation, no pending plan (AIHUB-0016 retest F2) ────────
    # Build completion clears the delegation, so a follow-up "confirm and
    # execute the plan" used to spin up a FRESH builder session that knew
    # nothing and asked for requirements from scratch ("builder lost the
    # original task context"). Answer deterministically instead.
    _existing_bsid = (state.get("active_delegation") or {}).get("builder_session_id")
    if not _existing_bsid and re.fullmatch(
        r"(?:yes|ok(?:ay)?|confirm(?:ed)?|proceed|go ahead|"
        r"confirm and (?:execute|run|proceed)(?: the plan)?|"
        r"(?:execute|run|proceed with) the plan)[.! ]*",
        user_text.strip(), re.I,
    ):
        _recent = state.get("recently_created_resources") or state.get("session_resources") or []
        _recent_names = ", ".join(
            f'{r.get("type", "resource")} "{r.get("name")}"'
            for r in _recent[:6] if r.get("name")
        )
        _note = (
            "There's no plan awaiting confirmation — the previous build already "
            "finished and its outcome was reported above."
        )
        if _recent_names:
            _note += f" Created this session: {_recent_names}."
        _note += " Send a new build request if you'd like to change or add something."
        logger.info("[build] bare confirmation with no pending builder session — answered deterministically")
        return {"messages": [AIMessage(content=_note)]}

    # IMPORTANT: builder maintains its own multi-turn context via builder_session_id.
    # Do NOT re-embed conversation history into the message (it can cause the builder
    # to repeatedly re-plan instead of advancing the state machine on a simple "yes").
    full_message = user_text

    try:
        from graph.tracing import trace_log
        import time as _time
        from datetime import datetime

        cc_sid = state.get("session_id", "cc-default")
        existing = state.get("active_delegation") or {}
        # On Turn 1, pass None so the delegator creates a real builder session.
        # On Turn 2+, reuse the builder-created UUID from active_delegation.
        builder_sid = existing.get("builder_session_id") or None

        # Progress streaming
        from graph.progress import get_queue as _get_build_pq
        _build_pq = _get_build_pq(state.get("session_id", ""))
        if _build_pq:
            await _build_pq.emit("status", {"phase": "building", "message": "Working with Builder Agent..."})

        trace_log(
            state,
            event_type="delegate_start",
            node="delegate_to_builder",
            payload={"builder_session_id": builder_sid, "message_preview": str(full_message)[:600]},
        )
        t0 = _time.perf_counter()
        result = await delegate_to_builder(
            message=full_message,
            session_id=cc_sid,
            builder_session_id=builder_sid,
            user_context=state.get("user_context"),
            timeout=120.0,
        )
        elapsed_ms = int((_time.perf_counter() - t0) * 1000)
        trace_log(
            state,
            event_type="delegate_end",
            node="delegate_to_builder",
            payload={
                "builder_session_id": builder_sid,
                "elapsed_ms": elapsed_ms,
                "status": (result or {}).get("status") if isinstance(result, dict) else None,
                "text_preview": str((result or {}).get("text") or "")[:800],
            },
        )

        # Update builder_sid from the delegator result (may have been None on Turn 1,
        # now it's the real builder-created UUID).
        builder_sid = result.get("builder_session_id") or builder_sid

        # Track latest plan data to detect when execution is finished
        latest_plan = result.get("plan") if isinstance(result, dict) else None

        # Use the builder's text whenever present — including on failed/partial delegations,
        # whose error text must survive to the distiller so the user gets an honest ❌.
        # (Previously gated on status == "completed"; now that delegate_to_builder reports
        # real non-completed statuses (F1), that gate would have discarded the failure text.)
        if result.get("text"):
            response_text = result["text"]
        else:
            response_text = "Builder Agent processed the request but returned no visible output."

        # Detect if the user's message is an affirmative/confirmation.
        # Uses the mini-LLM for robust detection instead of brittle keyword matching.
        # IMPORTANT: An affirmation is a SHORT standalone reply ("yes", "go ahead") —
        # NOT a fresh request like "create an agent...". The mini-LLM has shown it
        # mis-classifies new action requests as affirmative; we gate with a length
        # guard plus a pattern blocklist BEFORE asking the LLM (BUG-R2-017 root cause).
        AFFIRM_KEYWORDS = {
            "y", "yes", "ok", "okay", "yep", "yeah", "sure", "proceed",
            "go ahead", "do it", "execute", "confirm", "confirmed", "absolutely",
            "sounds good", "let's do it", "go for it", "make it happen", "please do",
            "yes please", "go", "👍", "sure thing",
        }
        _NEW_REQUEST_VERBS = (
            "create ", "make ", "build ", "set up ", "setup ", "add ",
            "new ", "generate ", "write ", "send ", "show ", "get ", "give ",
            "find ", "search ", "query ", "ask ", "forget ", "remember ",
            "delete ", "remove ", "update ",
        )

        async def _is_affirmative(txt: str) -> bool:
            t = (txt or "").strip()
            if not t:
                return False
            lt = t.lower()
            # Fast path: exact match to a known affirmative keyword
            if lt in AFFIRM_KEYWORDS:
                return True
            # AIHUB-0046: a reply that STARTS with an explicit confirm token is an
            # affirmation no matter its length or what follows — live, "Yes, I
            # confirm. Execute the plan now and actually create/save the workflow."
            # was rejected by the length guard, so a held plan needed a second,
            # differently-worded confirmation. Deterministic, no LLM. Messages
            # containing a question are never auto-confirms.
            _CONFIRM_PREFIXES = ("yes", "yep", "yeah", "ok", "okay", "confirm",
                                 "confirmed", "sure", "go ahead", "proceed", "do it",
                                 "execute the plan", "run the plan", "approved")
            if "?" not in t and any(
                    lt == p or lt.startswith(p + " ") or lt.startswith(p + ",")
                    or lt.startswith(p + ".") or lt.startswith(p + "!")
                    for p in _CONFIRM_PREFIXES):
                return True
            # Any message starting with a new-request verb is NEVER an affirmation,
            # regardless of how the LLM interprets it.
            if any(lt.startswith(v) for v in _NEW_REQUEST_VERBS):
                return False
            # Messages over 60 characters are unlikely to be pure affirmations.
            if len(t) > 60:
                return False
            try:
                from cc_config import get_step_llm as _get_step_llm_affirm
                _llm = _get_step_llm_affirm("builder_affirmative_detector")
                _aff_msgs = [HumanMessage(content=(
                    f'Does the following short user message mean the user is saying YES '
                    f'(agreeing, confirming, or approving a previously suggested action)? '
                    f'Treat any message that introduces a new request, command, or question as NO. '
                    f'Message: "{t}"\n'
                    f'Reply with ONLY "YES" or "NO".'
                ))]
                _aff_t0 = _trace_time.perf_counter()
                _resp = await _llm.ainvoke(_aff_msgs)
                trace_llm_call(state, node="build", step="builder_affirmative_detector",
                               messages=_aff_msgs, response=_resp,
                               elapsed_ms=int((_trace_time.perf_counter() - _aff_t0) * 1000), model_hint="mini")
                return _resp.content.strip().upper().startswith("YES") if hasattr(_resp, 'content') else False
            except Exception:
                # Fallback: keyword check if LLM fails
                return lt in AFFIRM_KEYWORDS

        # Auto-confirm: either (a) user said "yes" and a builder plan is awaiting
        # confirmation, or (b) builder returned a draft plan with concrete steps
        # (user already provided all needed info, no reason to make them confirm
        # separately).
        _should_auto_confirm = False
        _auto_confirm_reason = ""

        # AIHUB-0046: trigger (a) used to require the literal phrases "shall i go
        # ahead/proceed" in the builder reply — live phrasing varies, so a user's
        # affirmative to a HELD DRAFT PLAN never executed. A held draft plan with
        # steps now counts as "awaiting confirmation" regardless of wording.
        _resp_l = response_text.lower()
        _builder_asks = ("shall i go ahead" in _resp_l or "shall i proceed" in _resp_l
                         or "please confirm" in _resp_l
                         or "do you want me to proceed" in _resp_l)
        _plan_held = (isinstance(latest_plan, dict)
                      and (latest_plan.get("status") or "").lower() == "draft"
                      and bool(latest_plan.get("steps")))

        if (_builder_asks or _plan_held) and (await _is_affirmative(user_text)):
            _should_auto_confirm = True
            _auto_confirm_reason = "user affirmed; builder plan awaiting confirmation"
        elif latest_plan and isinstance(latest_plan, dict):
            plan_status = (latest_plan.get("status") or "").lower()
            plan_steps = latest_plan.get("steps", [])
            # Auto-confirm only for plans that (a) are small AND (b) do NOT mutate
            # user-visible resources. Plans that create agents, connections, workflows,
            # integrations, custom tools, or perform any delete action require explicit
            # user confirmation — otherwise users cannot reject a plan before side
            # effects happen (see BUG-R2-017: "no, I changed my mind" after an auto-
            # created agent triggered a DELETE flow).
            mutating_actions = {
                ("agents", "create"), ("agents", "update"), ("agents", "delete"),
                ("agents", "assign_tools"), ("agents", "assign_email"),
                ("agents", "assign_knowledge"),
                ("connections", "create"), ("connections", "update"), ("connections", "delete"),
                ("workflows", "create"), ("workflows", "update"), ("workflows", "delete"),
                ("integrations", "create"), ("integrations", "update"), ("integrations", "delete"),
                ("custom_tools", "create"), ("custom_tools", "update"), ("custom_tools", "delete"),
            }
            has_mutating = any(
                (s.get("is_destructive") is True) or
                ((s.get("domain", "").lower(), s.get("action", "").lower()) in mutating_actions) or
                # AIHUB-0046: a delegation step to the workflow agent BUILDS/SAVES
                # workflows — it is mutating even though its (domain, action) is
                # ("agent", "workflow_agent") and matches no table entry above.
                # Live, it classified mutating=False, so the confirm gate reasoned
                # about it as read-only.
                (s.get("domain", "").lower() in ("agent", "agents")
                 and "workflow" in str(s.get("action", "")).lower())
                for s in plan_steps
            ) if plan_steps else False
            is_small = 0 < len(plan_steps) <= 2
            builder_is_asking = (
                "shall i go ahead" in response_text.lower()
                or "shall i proceed" in response_text.lower()
                or "please confirm" in response_text.lower()
                or "do you want me to proceed" in response_text.lower()
            )
            if (plan_status == "draft" and is_small and not has_mutating
                    and not builder_is_asking):
                _should_auto_confirm = True
                _auto_confirm_reason = (
                    f"builder returned {len(plan_steps)}-step read-only draft — auto-executing"
                )
            elif plan_status == "draft" and plan_steps:
                logger.info(
                    f"[build] Plan held for user approval: steps={len(plan_steps)}, "
                    f"mutating={has_mutating}, builder_asking={builder_is_asking}"
                )

        if _should_auto_confirm:
            trace_log(
                state,
                event_type="auto_confirm",
                node="delegate_to_builder",
                payload={"builder_session_id": builder_sid, "reason": _auto_confirm_reason},
                level="info",
            )
            logger.info(f"[build] Auto-confirm: {_auto_confirm_reason}")
            t1 = _time.perf_counter()
            result2 = await delegate_to_builder(
                message="Yes, confirmed. Execute the plan now.",
                session_id=cc_sid,
                builder_session_id=builder_sid,
                user_context=state.get("user_context"),
                timeout=120.0,
            )
            elapsed2 = int((_time.perf_counter() - t1) * 1000)
            trace_log(
                state,
                event_type="delegate_end",
                node="delegate_to_builder",
                payload={
                    "builder_session_id": builder_sid,
                    "elapsed_ms": elapsed2,
                    "status": (result2 or {}).get("status") if isinstance(result2, dict) else None,
                    "text_preview": str((result2 or {}).get("text") or "")[:800],
                },
            )
            if isinstance(result2, dict) and result2.get("text"):
                response_text = result2["text"]
            if isinstance(result2, dict) and result2.get("plan"):
                latest_plan = result2["plan"]
            # AIHUB-0038: the execution call's persisted-node read-back supersedes
            # the draft call's — adopt result2 wholesale when it carries one.
            if isinstance(result2, dict) and result2.get("workflow_saved"):
                result = result2

        # Phase 4: classify the executed plan by read-back verification so both the
        # distiller and a deterministic footer can be honest about what actually landed.
        verification_summary = _summarize_verification(latest_plan or {})

        # AIHUB-0038: structured persisted-node read-back from the delegator. When
        # present it is the AUTHORITY on what the saved workflow contains — the
        # user-visible step list must come from it, never from the distiller LLM.
        _wf_saved = result.get("workflow_saved") if isinstance(result, dict) else None
        _dropped_cap = result.get("dropped_capability") if isinstance(result, dict) else None

        # Distill builder output into a user-facing message (never show raw JSON)
        raw_builder_text = response_text
        distilled_text = None
        if _dropped_cap and _wf_saved:
            # AIHUB-0038: the user asked for a capability the visual builder has no
            # node for and it was DROPPED from the saved workflow. The delegator
            # already replaced the builder narration with the authoritative
            # persisted-steps block — deliver that block VERBATIM. No LLM may
            # recompose this reply: the live 09 run showed the builder_distiller
            # rewriting the disclosure back into "✅ created and verified"
            # including the dropped SFTP step.
            trace_log(
                state,
                event_type="distill_bypassed",
                node="delegate_to_builder",
                payload={
                    "reason": f"dropped_capability={_dropped_cap}",
                    "workflow_id": (_wf_saved or {}).get("workflow_id"),
                    "node_types": (_wf_saved or {}).get("node_types"),
                },
                level="info",
            )
            logger.info(
                f"[build] distiller BYPASSED (dropped capability: {_dropped_cap}) — "
                f"replying with the deterministic persisted-steps block"
            )
        else:
            try:
                from cc_config import get_step_llm
                llm2 = get_step_llm("builder_distiller")

                # Keep prompt small but include enough context for judgment.
                log_tail = (existing.get("builder_log") or [])[-6:]
                _bd_conv = _format_conversation_for_prompt(messages)
                _bd_conv_block = (
                    f"Recent user-facing conversation (for understanding what the user was asking):\n{_bd_conv}\n\n"
                    if _bd_conv else ""
                )
                # AIHUB-0038: when a persisted-node read-back exists, its step list is
                # AUTHORITATIVE — the distiller may not restate a different one.
                _auth_steps_rule = ""
                if _wf_saved and _wf_saved.get("node_types"):
                    _auth_steps_rule = (
                        "- AUTHORITATIVE SAVED WORKFLOW (independent read-back): the saved "
                        "workflow contains EXACTLY these steps: "
                        + ", ".join(str(t) for t in _wf_saved["node_types"])
                        + ". NEVER list, claim, or imply any other step was built/"
                        "configured/verified. If the user asked for more than these "
                        "steps, the extra part is NOT in this workflow — do not restate "
                        "the user's request as if it were all built.\n"
                    )
                # AIHUB-0046: a HELD plan (draft, nothing executed) must read as
                # awaiting confirmation — live, the narration speculated "I can't
                # confirm the workflow was created … please double-check", which
                # confuses users when nothing was even attempted yet.
                _plan_still_held = (not _should_auto_confirm and _wf_saved is None
                                    and isinstance(latest_plan, dict)
                                    and (latest_plan.get("status") or "").lower() == "draft"
                                    and bool(latest_plan.get("steps")))
                if _plan_still_held:
                    _auth_steps_rule += (
                        "- PLAN NOT EXECUTED YET: nothing has been created or attempted — "
                        "do NOT speculate about whether anything was created and do NOT "
                        "tell the user to double-check. State plainly that the plan is "
                        "awaiting their confirmation.\n"
                    )
                distill_prompt = (
                    "You are the AI Hub Command Center. You are the user's representative.\n"
                    "You received a message from an internal Builder agent. The Builder message may contain raw JSON, internal tool plans, or repeated confirmation prompts.\n\n"
                    "RULES:\n"
                    "- NEVER output raw JSON or internal tool-call formats.\n"
                    "- If the builder is asking for confirmation and the user has NOT confirmed yet, summarize the plan in plain English and ask the user to confirm.\n"
                    "- If the user already confirmed (their last message is affirmative) and the builder is still asking to confirm, assume CC already handled execution; summarize current status or final result.\n"
                    "- If the builder reports success, output a concise success message (e.g., ✅ Agent created: <name>).\n"
                    "- If the builder reports failure, output a concise failure message (❌ Failed: <reason>) plus what you need from the user if anything.\n"
                    "- VERIFICATION (authoritative): the 'Verification' section below is an independent read-back of platform state. ONLY claim something was created/done for steps marked VERIFIED. For any step marked UNVERIFIED, say it was attempted but could NOT be confirmed and ask the user to double-check — do NOT call it done. For FAILED steps, report the failure. Never present unverified or failed work as success.\n"
                    + _auth_steps_rule +
                    "- Use the recent user-facing conversation below to interpret short/ambiguous user messages (e.g. 'yes' may refer to an earlier confirmation prompt).\n"
                    "- Keep it short.\n\n"
                    f"{_bd_conv_block}"
                    f"Last user message: {user_text!r}\n"
                    f"Builder message (internal): {raw_builder_text[:6000]!r}\n"
                    f"Verification (authoritative read-back of platform state):\n{_render_verification_for_prompt(verification_summary)}\n"
                    f"Recent builder log tail (internal): {json.dumps(log_tail)[:4000]}\n"
                )
                distill_prompt += _preferences_block(state)

                _dist_msgs = [
                    SystemMessage(content="You rewrite internal agent messages into user-facing responses with good judgment."),
                    HumanMessage(content=distill_prompt),
                ]
                _dist_t0 = _trace_time.perf_counter()
                distilled = await llm2.ainvoke(_dist_msgs)
                trace_llm_call(state, node="build", step="builder_distiller",
                               messages=_dist_msgs, response=distilled,
                               elapsed_ms=int((_trace_time.perf_counter() - _dist_t0) * 1000), model_hint="mini")
                if distilled and getattr(distilled, "content", None):
                    distilled_text = str(distilled.content).strip()
            except Exception as _distill_err:
                trace_log(
                    state,
                    event_type="distill_error",
                    node="delegate_to_builder",
                    payload={"error": str(_distill_err)},
                    level="warning",
                )

        # Use distilled if available; otherwise fall back. W5 (#17): before the generic
        # apology, try a DETERMINISTIC summary from the verified plan so a distiller crash
        # on a real success doesn't show a scary "couldn't format it" message. Only use the
        # apology when there is genuinely nothing verifiable to report.
        if _dropped_cap and _wf_saved:
            # AIHUB-0038: deterministic — the delegator's authoritative block IS the reply.
            response_text = raw_builder_text
        elif distilled_text:
            response_text = distilled_text
        else:
            response_text = _deterministic_builder_summary(verification_summary, latest_plan) or (
                "I received an internal response from the Builder Agent but couldn't format "
                "it safely for display. Please check the Command Center logs/traces for details."
            )

        # Phase 4 fail-closed guarantee: append a deterministic honesty footer whenever
        # anything failed or could not be verified, so the truth survives regardless of
        # what the distiller LLM wrote.
        _ver_footer = _verification_footer(verification_summary)
        if _ver_footer:
            response_text = response_text + _ver_footer

        # AIHUB-0038 fail-closed guarantee for the step list: whenever a persisted-node
        # read-back exists, pin the authoritative saved-steps footer AFTER distillation
        # so the distiller can never remove, contradict, or re-add steps. (On the
        # dropped-capability path the whole reply is already the authoritative block.)
        if _wf_saved and not _dropped_cap:
            response_text = response_text + _persisted_steps_footer(_wf_saved)

        # AIHUB-0046 fail-closed: a HELD plan (draft with steps, nothing saved, no
        # auto-confirm fired) always ends with the deterministic awaiting-confirmation
        # footer — the truth ("nothing built yet, reply yes") survives whatever the
        # distiller wrote, same pattern as the verification footer.
        if (not _should_auto_confirm and _wf_saved is None
                and isinstance(latest_plan, dict)
                and (latest_plan.get("status") or "").lower() == "draft"
                and latest_plan.get("steps")):
            response_text = response_text + (
                "\n\n⏸️ **Nothing has been built yet** — this plan is awaiting your "
                "go-ahead. Reply **yes** to build it now.")

        # Persist builder delegation context for multi-turn
        builder_log = list(existing.get("builder_log", []))
        builder_log.append({"role": "user_to_builder", "content": user_text, "ts": datetime.now().isoformat()})
        builder_log.append({"role": "builder_response_raw", "content": raw_builder_text[:2000], "ts": datetime.now().isoformat()})
        builder_log.append({"role": "cc_user_visible", "content": response_text[:2000], "ts": datetime.now().isoformat()})

        # Capture the builder-created session_id from the result (set by the delegator).
        # This ensures Turn 2+ uses the real builder UUID, not a CC-fabricated ID.
        resolved_builder_sid = result.get("builder_session_id") or builder_sid

        # Determine build lifecycle status and extract created resources
        build_status = "in_progress"
        created_resources = []
        completed_at = None

        if latest_plan and isinstance(latest_plan, dict):
            plan_status = (latest_plan.get("status") or "").lower()
            if plan_status in ("completed", "partial", "failed"):
                build_status = plan_status
                completed_at = datetime.now().isoformat()
                logger.info(f"Builder plan execution finished (status={plan_status})")
                # Extract created resources from plan steps
                created_resources = _extract_created_resources(latest_plan)
                if created_resources:
                    logger.info(f"Builder created resources: {created_resources}")

        active_delegation = {
            "agent_id": "builder",
            "agent_name": "Builder Agent",
            "agent_type": "builder",
            "started_at": existing.get("started_at") or datetime.now().isoformat(),
            "history": list(existing.get("history", [])),
            "builder_session_id": resolved_builder_sid,
            "builder_log": builder_log,
            "build_status": build_status,
        }
        if any(verification_summary.get(k) for k in ("verified", "unverified", "failed")):
            active_delegation["verification"] = {
                "verified": len(verification_summary["verified"]),
                "unverified": len(verification_summary["unverified"]),
                "failed": len(verification_summary["failed"]),
            }
        if created_resources:
            active_delegation["created_resources"] = created_resources
        if completed_at:
            active_delegation["completed_at"] = completed_at

        # Merge created resources into session_resources for persistent awareness
        result_dict = {"messages": [AIMessage(content=response_text)], "active_delegation": active_delegation}
        if created_resources:
            existing_session_res = list(state.get("session_resources") or [])
            for cr in created_resources:
                # Add timestamp and deduplicate by (type, id)
                cr_entry = {**cr, "created_at": completed_at or datetime.now().isoformat()}
                if not any(r.get("type") == cr["type"] and str(r.get("id")) == str(cr["id"]) for r in existing_session_res):
                    existing_session_res.append(cr_entry)
            # Cap at 50 entries
            result_dict["session_resources"] = existing_session_res[-50:]

        return result_dict

    except Exception as e:
        logger.error(f"Builder delegation failed: {e}")
        error_text = (
            f"I tried to delegate to the Builder Agent but encountered an error: {str(e)}. "
            f"The Builder Agent service may not be running (port 8100). "
            f"I can still help you design the agent specification — would you like me to describe what the agent would look like?"
        )
        return {"messages": [AIMessage(content=error_text)]}


# ─── Node: design_tool ────────────────────────────────────────────────────

async def design_tool(state: CommandCenterState) -> dict:
    """LLM generates config.json + code.py for a new tool."""
    from cc_config import get_llm

    # Creating new tools mutates the platform — Developer+ only.
    if not _build_allowed(state):
        return {"messages": [AIMessage(content=_BUILD_DENIED_MSG)]}

    messages = state.get("messages", [])
    last_msg = messages[-1] if messages else None
    user_text = last_msg.content if last_msg and hasattr(last_msg, 'content') else ""

    design_prompt = f"""The user needs a new tool capability. Design a Python tool.

User request: {user_text}

Return a JSON object with:
{{
  "tool_name": "snake_case_name",
  "description": "What this tool does",
  "parameters": {{"param_name": "param_description"}},
  "parameter_types": {{"param_name": "str|int|float|bool|List"}},
  "code": "Python function body (NOT wrapped in def)",
  "output_type": "str"
}}

Only return the JSON object, nothing else."""

    try:
        llm = get_llm(mini=False, streaming=False)
        _dt_msgs = [
            SystemMessage(content="You are an expert Python developer creating tools for an AI agent platform."),
            HumanMessage(content=design_prompt),
        ]
        _dt_t0 = _trace_time.perf_counter()
        response = await llm.ainvoke(_dt_msgs)
        trace_llm_call(state, node="design_tool", step="tool_designer",
                       messages=_dt_msgs, response=response,
                       elapsed_ms=int((_trace_time.perf_counter() - _dt_t0) * 1000), model_hint="full")

        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        tool_spec = json.loads(raw)

        return {
            "delegation_results": {
                "_tool_design": tool_spec,
                "_tool_test": {"success": False, "pending": True},
            }
        }
    except Exception as e:
        logger.error(f"Tool design failed: {e}")
        error_content = json.dumps([{"type": "text", "content": f"Failed to design tool: {str(e)}"}])
        return {"messages": [AIMessage(content=error_content)]}


# ─── Node: sandbox_test ───────────────────────────────────────────────────

async def sandbox_test(state: CommandCenterState) -> dict:
    """Run generated tool code in a restricted subprocess."""
    results = state.get("delegation_results", {})
    tool_spec = results.get("_tool_design", {})

    if not tool_spec:
        return {"delegation_results": {**results, "_tool_test": {"success": False, "error": "No tool design found"}}}

    try:
        from command_center.tools.tool_sandbox import test_tool_in_sandbox
        test_result = await test_tool_in_sandbox(tool_spec)
        return {"delegation_results": {**results, "_tool_test": test_result}}
    except Exception as e:
        logger.error(f"Sandbox test failed: {e}")
        return {"delegation_results": {**results, "_tool_test": {"success": False, "error": str(e)}}}


# ─── Node: save_tool ──────────────────────────────────────────────────────

async def save_tool(state: CommandCenterState) -> dict:
    """Persist a successfully tested tool to disk + audit table."""
    # Backstop: persisting a tool is a platform mutation. design_tool already
    # gates entry, but guard here too so the node can't be reached out-of-band.
    if not _build_allowed(state):
        logger.info("[save_tool] refused — user lacks Developer role")
        return {"messages": [AIMessage(content=_BUILD_DENIED_MSG)]}

    results = state.get("delegation_results", {})
    tool_spec = results.get("_tool_design", {})

    try:
        from command_center.tools.tool_factory import save_generated_tool
        save_result = save_generated_tool(tool_spec)

        success_msg = json.dumps([{
            "type": "text",
            "content": f"I've created a new tool: **{tool_spec.get('tool_name', 'unknown')}** — {tool_spec.get('description', '')}. It's now available for future requests."
        }])
        return {"messages": [AIMessage(content=success_msg)]}
    except Exception as e:
        logger.error(f"Tool save failed: {e}")
        error_content = json.dumps([{"type": "text", "content": f"Tool was designed but failed to save: {str(e)}"}])
        return {"messages": [AIMessage(content=error_content)]}


# ─── Node: answer_quality_gate ─────────────────────────────────────────────
# Layer 1: Observe-only. Classifies the quality of the response but takes
# NO enrichment action. Logs classification for review.
# SAFETY: Original response is ALWAYS returned unchanged.

_AQG_CLASSIFICATION_PROMPT = """You are an Answer Quality Gate. Your job is to evaluate whether an AI assistant's response adequately answers the user's question.

Classify the response into EXACTLY ONE of these categories:

PASS — The response reasonably answers the question. Even partial or approximate answers count as PASS.
GAP_GEOGRAPHIC — The response contains geographic/location data that could be enhanced (e.g., region names that could be mapped to coordinates/states for visualization).
GAP_FORMAT — The response has the right data but in the wrong format for what the user asked (e.g., user asked for a chart but got a table).
GAP_KNOWLEDGE — The response admits it doesn't know something that general knowledge could answer (NOT business-specific data).
ERROR_INFRA — The response describes a technical failure: database down, agent timeout, connection error, service unavailable, etc. These are NOT gaps — they are infrastructure problems.
UNCLEAR — You cannot confidently classify this. Default to this if unsure.

CRITICAL RULES:
- If the response is raw JSON, tool call metadata, internal system output, or unformatted technical content that a normal user would NOT understand → GAP_FORMAT (confidence=1.0). Examples: {"agent_id": 14, "question": "..."}, [{"tool": "...", "args": {...}}], or any JSON blob with keys like agent_id, tool, function, parameters.
- If the response mentions ANY error, connection issue, timeout, or service problem → ERROR_INFRA
- If the response is reasonable even if imperfect → PASS
- If the response proposes a plan and asks for user confirmation before executing (e.g., "Shall I proceed?", "Here's the plan...", "Do you want me to...", "I plan to create..."), this is a DELIBERATE workflow step, NOT a gap. Classify as PASS.
- If the response acknowledges the request and describes what it WILL do or is ABOUT to do, that is PASS — not a gap.
- Only classify as a GAP if you are >80% confident there is a specific, actionable improvement
- When in doubt → PASS or UNCLEAR (never a GAP)

DISTINGUISHING GAP_FORMAT vs GAP_GEOGRAPHIC:
- GAP_FORMAT: The response HAS the right data but displayed it in the wrong format (e.g., user asked for a map/chart but got a table/text). The data exists, just needs re-rendering.
- GAP_GEOGRAPHIC: Geographic identifiers in the data need ENRICHMENT to be useful (e.g., "Northeast" needs to be expanded to component states with coordinates, or region names need lat/lng to be plottable). The data itself is incomplete for geographic visualization.

Reply with ONLY a JSON object:
{"classification": "<CATEGORY>", "confidence": <0.0-1.0>, "reason": "<brief explanation>"}"""


async def answer_quality_gate(state: CommandCenterState) -> dict:
    """
    Evaluate the quality of the response relative to the user's question.
    Layer 1: OBSERVE ONLY — logs classification, returns response unchanged.
    """
    from cc_config import (
        get_llm, ANSWER_QUALITY_GATE_ENABLED,
        AQG_GEO_CONFIDENCE_THRESHOLD, AQG_KNOWLEDGE_CONFIDENCE_THRESHOLD,
    )

    if not ANSWER_QUALITY_GATE_ENABLED:
        return {}

    messages = state.get("messages", [])

    # Find the user's CURRENT question (last HumanMessage, not first).
    # Using the first HumanMessage caused the AQG to compare the latest AI
    # response against Turn 1's question in multi-turn conversations, leading
    # to false GAP_KNOWLEDGE classifications and stale enrichments.
    user_question = None
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            user_question = msg.content
            break

    # Find the last AI response
    ai_response = None
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            ai_response = msg.content
            break

    # If we don't have both, skip silently
    if not user_question or not ai_response:
        logger.debug("[AQG] Skipping — missing user question or AI response")
        return {}

    # Truncate to avoid token overflow (only need enough to classify)
    user_q_short = user_question[:500]
    ai_resp_short = ai_response[:2000]

    try:
        from cc_config import get_step_llm
        mini_llm = get_step_llm("answer_quality_gate")

        eval_messages = [
            SystemMessage(content=_AQG_CLASSIFICATION_PROMPT),
            HumanMessage(content=f"USER QUESTION:\n{user_q_short}\n\nASSISTANT RESPONSE:\n{ai_resp_short}"),
        ]

        _aqg_t0 = _trace_time.perf_counter()
        result = await mini_llm.ainvoke(eval_messages)
        trace_llm_call(state, node="answer_quality_gate", step="quality_gate_classification",
                       messages=eval_messages, response=result,
                       elapsed_ms=int((_trace_time.perf_counter() - _aqg_t0) * 1000), model_hint="mini")
        result_text = result.content.strip() if hasattr(result, 'content') else str(result)

        # Parse classification
        try:
            # Handle markdown-wrapped JSON
            if "```" in result_text:
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
                result_text = result_text.strip()

            classification = json.loads(result_text)
            category = classification.get("classification", "UNCLEAR")
            confidence = classification.get("confidence", 0)
            reason = classification.get("reason", "")
        except (json.JSONDecodeError, KeyError):
            category = "UNCLEAR"
            confidence = 0
            reason = f"Failed to parse: {result_text[:100]}"

        # Log the classification
        logger.info(
            f"[AQG] Classification: {category} (confidence={confidence:.2f}) "
            f"| Question: {user_q_short[:80]}... "
            f"| Reason: {reason[:120]}"
        )

    except Exception as e:
        logger.warning(f"[AQG] Gate evaluation failed (passing through): {e}")
        category = "ERROR"
        confidence = 0
        reason = str(e)

    # ── Auto-enrichment (WS3, opt-in) ───────────────────────────────────────
    # Behavior is controlled by the CC_ENRICHMENT env var:
    #   - "off"   → no enrichment runs at all
    #   - "tools" → DEFAULT; preserves the previous behavior where the LLM's
    #               explicit tool calls (generate_map, search_web, …) are the
    #               only source of enrichment
    #   - "auto"  → runs the gap-specific helpers below on the AQG
    #               classification, with a strict per-turn budget so we
    #               don't blow up costs
    #
    # The auto path was previously disabled because the keyword trigger
    # was too loose. The fix here is twofold: (1) we now trigger off the
    # mini-LLM's classification (semantic, not keyword), and (2) the
    # bounded budget keeps damage low if the gate misclassifies.
    import os as _os
    enrichment_mode = (_os.environ.get("CC_ENRICHMENT") or "tools").strip().lower()
    if enrichment_mode not in {"auto", "tools", "off"}:
        logger.warning(
            f"[AQG] Unknown CC_ENRICHMENT={enrichment_mode!r}; falling back to 'tools'"
        )
        enrichment_mode = "tools"

    if enrichment_mode != "auto":
        return {}

    # Per-turn budget — keep this conservative. Each helper call counts.
    MAX_ENRICHMENT_CALLS = 3
    MAX_ENRICHMENT_TOTAL_SECONDS = 8.0
    budget_t0 = _trace_time.perf_counter()
    calls_used = 0

    def _budget_exhausted() -> bool:
        if calls_used >= MAX_ENRICHMENT_CALLS:
            return True
        if (_trace_time.perf_counter() - budget_t0) >= MAX_ENRICHMENT_TOTAL_SECONDS:
            return True
        return False

    extra_blocks: list[dict] = []

    # GAP_GEOGRAPHIC → try to attach a map block with real, geocoded markers.
    if category == "GAP_GEOGRAPHIC" and not _budget_exhausted():
        _t0 = _trace_time.perf_counter()
        try:
            calls_used += 1
            map_block, geo_prov = await _enrich_geographic(
                user_q_short, ai_resp_short, mini_llm, trace_state=state,
            )
        except Exception as e:
            logger.warning(f"[AQG/auto] _enrich_geographic raised: {e}")
            map_block, geo_prov = None, None
        latency_ms = int((_trace_time.perf_counter() - _t0) * 1000)
        success = map_block is not None
        logger.info(
            "[AQG/auto] helper=_enrich_geographic input=%r source=%s latency=%dms success=%s",
            user_q_short[:120], "geocoder/nominatim", latency_ms, success,
        )
        if success:
            try:
                from provenance import attach_to_block as _attach
                if geo_prov is not None:
                    _attach(map_block, geo_prov)
            except Exception as e:
                logger.warning(f"[AQG/auto] provenance attach failed: {e}")
            extra_blocks.append(map_block)

    # GAP_KNOWLEDGE → attach a model-knowledge text block (provenance-stamped).
    if category == "GAP_KNOWLEDGE" and not _budget_exhausted():
        _t0 = _trace_time.perf_counter()
        try:
            calls_used += 1
            knowledge_text, k_prov = await _enrich_knowledge(
                user_q_short, ai_resp_short, mini_llm, trace_state=state,
            )
        except Exception as e:
            logger.warning(f"[AQG/auto] _enrich_knowledge raised: {e}")
            knowledge_text, k_prov = None, None
        latency_ms = int((_trace_time.perf_counter() - _t0) * 1000)
        success = bool(knowledge_text)
        logger.info(
            "[AQG/auto] helper=_enrich_knowledge input=%r source=%s latency=%dms success=%s",
            user_q_short[:120], "model_knowledge", latency_ms, success,
        )
        if success:
            block = {
                "type": "text",
                "content": f"**Additional context from general knowledge**\n\n{knowledge_text}",
            }
            try:
                from provenance import attach_to_block as _attach
                if k_prov is not None:
                    _attach(block, k_prov)
            except Exception as e:
                logger.warning(f"[AQG/auto] provenance attach failed: {e}")
            extra_blocks.append(block)

    if not extra_blocks:
        return {}

    # Append a separate AIMessage carrying the enrichment blocks. This keeps
    # the original agent response intact and lets the renderer / aggregator
    # treat the enrichment as a follow-up message — no breakage to the
    # classic UI's existing parsing.
    try:
        enrichment_msg = AIMessage(content=json.dumps(extra_blocks))
        return {"messages": [enrichment_msg]}
    except Exception as e:
        logger.warning(f"[AQG/auto] failed to serialize enrichment blocks: {e}")
        return {}


_GEO_ENRICHMENT_PROMPT = """You are a geographic data enrichment assistant. Given a user's question about geographic/regional data and the assistant's response, extract the data and convert it into map-ready format.

Your job:
1. Extract any geographic names and associated values from the response
2. Map region/state names to their component US states if needed (e.g., "Northeast" → New York, Connecticut, Massachusetts, etc.)
3. For each state, provide the data value

Reply with ONLY a JSON object:
{
  "title": "Map title",
  "regions": [
    {"name": "State Name", "value": <numeric_value>, "label": "Display label"}
  ]
}

RULES:
- State names must match US state names exactly (e.g., "New York", "California")
- Values must be numeric (parse "$1.5M" → 1500000, "15%" → 15)
- If a region covers multiple states (e.g., "Northeast"), distribute the value equally across component states OR use the same value for all
- If no geographic data can be extracted, return {"error": "No geographic data found"}
- NEVER invent business data — only use values from the response"""


async def _enrich_geographic(user_question: str, ai_response: str, mini_llm, trace_state: dict = None):
    """
    Attempt to enrich a response with geographic visualization.

    Returns a tuple ``(map_block, provenance)`` where ``provenance`` is a
    :class:`command_center_service.provenance.Provenance` object stamping
    every geocoded marker's lat/lng. Returns ``(None, None)`` if
    enrichment isn't possible. The provenance contract is documented in
    ``docs/data-provenance.md``.

    Wired to the real geocoder (WS2) — each region name is geocoded so
    the resulting markers carry real coordinates, never ``(0, 0)``.
    """
    # Lazy imports — keep module import-time light and avoid a hard
    # dependency cycle with plugins/.
    from provenance import (
        Provenance, SOURCE_GEOCODER, SOURCE_MODEL_KNOWLEDGE,
    )
    try:
        from plugins.web_intelligence.geocoder import geocode as _geocode_query
    except Exception:  # pragma: no cover - defensive
        _geocode_query = None

    try:
        messages = [
            SystemMessage(content=_GEO_ENRICHMENT_PROMPT),
            HumanMessage(content=f"USER QUESTION:\n{user_question}\n\nASSISTANT RESPONSE:\n{ai_response}"),
        ]

        _geo_t0 = _trace_time.perf_counter()
        result = await mini_llm.ainvoke(messages)
        if trace_state:
            trace_llm_call(trace_state, node="answer_quality_gate", step="geographic_enrichment",
                           messages=messages, response=result,
                           elapsed_ms=int((_trace_time.perf_counter() - _geo_t0) * 1000), model_hint="mini")
        result_text = result.content.strip() if hasattr(result, 'content') else str(result)

        # Parse JSON
        if "```" in result_text:
            result_text = result_text.split("```")[1]
            if result_text.startswith("json"):
                result_text = result_text[4:]
            result_text = result_text.strip()

        data = json.loads(result_text)

        if data.get("error"):
            logger.info(f"[AQG/geo] Enrichment declined: {data['error']}")
            return None, None

        regions = data.get("regions", [])
        if not regions:
            logger.info("[AQG/geo] No regions extracted")
            return None, None

        provenance = Provenance()

        # Stamp each region's `value` field as model-knowledge until we
        # corroborate with a real data agent — the LLM is reformatting the
        # numbers from the AI response, which is itself untrusted.
        for i, region in enumerate(regions):
            provenance.stamp(
                f"regions[{i}].value",
                source=SOURCE_MODEL_KNOWLEDGE,
                source_detail="answer_quality_gate/geo",
                confidence=0.5,
                notes="extracted from assistant response by mini LLM",
            )

        # Build markers via the real geocoder. Failures here are
        # non-fatal — we just skip that marker.
        markers: list[dict] = []
        if _geocode_query is not None:
            for i, region in enumerate(regions):
                name = (region.get("name") or "").strip() if isinstance(region, dict) else ""
                if not name:
                    continue
                try:
                    gres = _geocode_query(name)
                except Exception as e:
                    logger.warning(f"[AQG/geo] geocode failed for {name!r}: {e}")
                    continue
                if gres is None:
                    continue
                marker_idx = len(markers)
                markers.append({
                    "lat": gres.lat,
                    "lng": gres.lng,
                    "label": name,
                    "popup": f"{name}: {region.get('label', region.get('value', ''))}",
                })
                # Stamp lat/lng provenance with the geocoder source.
                for fld in ("lat", "lng"):
                    provenance.stamp(
                        f"markers[{marker_idx}].{fld}",
                        source=SOURCE_GEOCODER,
                        source_detail=gres.source,
                        confidence=gres.confidence,
                        notes=f"geocoded from query={gres.query!r}",
                        timestamp=gres.fetched_at,
                    )

        # Build map block
        map_block = {
            "type": "map",
            "title": data.get("title", "Geographic Visualization"),
            "center": [39.8, -98.5],  # US center
            "zoom": 4,
            "regions": regions,
        }
        if markers:
            map_block["markers"] = markers

        logger.info(
            f"[AQG/geo] Enriched with {len(regions)} regions and {len(markers)} geocoded markers"
        )
        return map_block, provenance

    except Exception as e:
        logger.warning(f"[AQG/geo] Enrichment failed: {e}")
        return None, None


async def _check_fabrication(text: str, mini_llm, trace_state: dict = None) -> bool:
    """
    Use a lightweight LLM call to check if text fabricates business-specific data.
    Returns True if fabrication is detected, False if clean.

    The caller is responsible for acting on the result. Per WS4, a True
    result should *downgrade* the field's provenance confidence and add a
    note rather than silently drop the content — see
    :func:`_apply_fabrication_downgrade` below for the helper that does
    that against a :class:`Provenance` map.
    """
    try:
        check_prompt = (
            "Does the following text fabricate or invent specific business data? "
            "Look for: references to 'your data/database/records/system', invented sales figures, "
            "made-up financial numbers, fake statistics attributed to the user's company, "
            "or any claim to have accessed the user's internal systems.\n\n"
            "Reply with ONLY 'YES' (fabrication detected) or 'NO' (clean general knowledge).\n\n"
            f"TEXT:\n{text[:1000]}"
        )
        _fab_msgs = [HumanMessage(content=check_prompt)]
        _fab_t0 = _trace_time.perf_counter()
        result = await mini_llm.ainvoke(_fab_msgs)
        if trace_state:
            trace_llm_call(trace_state, node="answer_quality_gate", step="fabrication_check",
                           messages=_fab_msgs, response=result,
                           elapsed_ms=int((_trace_time.perf_counter() - _fab_t0) * 1000), model_hint="mini")
        answer = result.content.strip().upper() if hasattr(result, 'content') else ""
        return answer.startswith("YES")
    except Exception as e:
        logger.warning(f"[AQG/fabrication] Check failed, allowing through: {e}")
        return False  # Fail-open: if check fails, allow the enrichment


_KNOWLEDGE_ENRICHMENT_PROMPT = """You are a knowledge enrichment assistant. The main AI assistant couldn't fully answer a question. Your job is to provide helpful additional context using general knowledge.

RULES:
- Provide factual, widely-known information only
- NEVER fabricate specific business data, financial figures, sales numbers, or proprietary information
- NEVER pretend to have access to the user's databases or systems
- Keep your response concise (2-4 paragraphs max)
- If the gap is about real-time data (weather, stock prices, live events), say so — don't guess
- If you genuinely cannot help with general knowledge, reply with exactly: NO_ENRICHMENT
- Format your response in markdown

You are supplementing the original answer, not replacing it. The user will see your response labeled as "Additional context from general knowledge." """


async def _enrich_knowledge(user_question: str, ai_response: str, mini_llm, trace_state: dict = None):
    """
    Attempt to fill a knowledge gap with general LLM knowledge.

    Returns a tuple ``(text, provenance)``. Provenance stamps the
    ``content`` field with ``source="model_knowledge"`` and a default
    confidence of 0.5. If the fabrication check fires, confidence is
    downgraded to 0.2 and a note is added (the caller can still decide
    to render or hide low-confidence content). Returns ``(None, None)``
    when there's nothing useful to add.
    """
    from provenance import Provenance, SOURCE_MODEL_KNOWLEDGE

    try:
        messages = [
            SystemMessage(content=_KNOWLEDGE_ENRICHMENT_PROMPT),
            HumanMessage(
                content=(
                    f"The user asked: {user_question}\n\n"
                    f"The assistant responded: {ai_response}\n\n"
                    f"Provide additional helpful context if possible."
                )
            ),
        ]

        _ke_t0 = _trace_time.perf_counter()
        result = await mini_llm.ainvoke(messages)
        if trace_state:
            trace_llm_call(trace_state, node="answer_quality_gate", step="knowledge_enrichment",
                           messages=messages, response=result,
                           elapsed_ms=int((_trace_time.perf_counter() - _ke_t0) * 1000), model_hint="mini")
        result_text = result.content.strip() if hasattr(result, 'content') else str(result)

        if not result_text or result_text == "NO_ENRICHMENT":
            logger.info("[AQG/knowledge] Enrichment declined — no useful context to add")
            return None, None

        # Try to pull the model id off the LLM client for the source_detail field.
        model_id = getattr(mini_llm, "model_name", None) or getattr(mini_llm, "model", None) or "mini"

        provenance = Provenance()
        provenance.stamp(
            "content",
            source=SOURCE_MODEL_KNOWLEDGE,
            source_detail=str(model_id),
            confidence=0.5,
            notes="LLM parametric knowledge; not corroborated against live sources",
        )

        # Safety check: LLM-based fabrication detection. WS4 says don't
        # drop on flag — downgrade confidence and note the reason instead,
        # so the UI can still render with a red badge.
        is_fabricated = await _check_fabrication(result_text, mini_llm, trace_state=trace_state)
        if is_fabricated:
            logger.warning("[AQG/knowledge] Fabrication flag — downgrading provenance confidence")
            provenance.downgrade_confidence(
                "content",
                new_confidence=0.2,
                note="fabrication_check flagged this content",
            )

        logger.info(f"[AQG/knowledge] Enrichment provided ({len(result_text)} chars)")
        return result_text, provenance

    except Exception as e:
        logger.warning(f"[AQG/knowledge] Enrichment failed: {e}")
        return None, None
