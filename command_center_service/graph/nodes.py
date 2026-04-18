"""
Command Center Agent — Graph Nodes
=====================================
Each function is a node in the LangGraph state machine.
Nodes read from and write to CommandCenterState.
"""

import asyncio
import json
import logging
import time as _trace_time
from typing import Any
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from graph import CommandCenterState
from graph.tracing import trace_llm_call

logger = logging.getLogger(__name__)


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
- build: creating, configuring, or modifying platform resources (agents, workflows, connections, tools)
- none: does NOT cleanly match any of the above — includes database queries, delegations to data/general agents, multi-step requests, ambiguous requests, and ordinary chat

User message: "{user_text}"

Reply with ONLY a JSON object, no other text:
{{"capability": "<one of the above>", "confidence": <float 0.0-1.0>}}

Rules:
- Use confidence >= 0.7 ONLY when you are clearly sure this maps to a single CC capability.
- For multi-step requests (e.g. "find the contract AND export it to excel"), use "none" — let the full classifier handle them.
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

    cc_native_chat = {"document_search", "web_search", "map", "image_generation", "run_tool"}
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
    prompt_body = _CAPABILITY_ROUTER_PROMPT.format(
        tool_names=tool_names,
        user_text=(user_text or "").replace('"', '\\"')[:800],
    )
    if not _DSE:
        prompt_body = prompt_body.replace(
            "- document_search: finding documents, contracts, invoices, leases, policies, reports, records in the document repository (not database rows)\n",
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


# ─── Node: classify_intent ────────────────────────────────────────────────

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
    if not active or not active.get("agent_id"):
        from cc_config import USE_ROUTE_MEMORY
        if USE_ROUTE_MEMORY:
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

    # Always scan the platform (cached, so fast after first call)
    try:
        landscape = await scan_platform()
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

        valid_intents = {"chat", "query", "analyze", "delegate", "build", "multi_step", "create_tool"}
        if intent not in valid_intents:
            logger.warning(f"Unknown intent '{intent}', defaulting to 'chat'")
            intent = "chat"

        logger.info(f"Classified intent: {intent}")
        return _intent_result({"intent": intent, "landscape": landscape, "pending_agent_selection": False})

    except Exception as e:
        logger.error(f"Intent classification failed: {e}")
        return _intent_result({"intent": "chat", "landscape": landscape, "pending_agent_selection": False})


# ─── Node: converse ───────────────────────────────────────────────────────

async def converse(state: CommandCenterState) -> dict:
    """General conversation — grounded in real platform data."""
    from cc_config import get_llm, COMMAND_CENTER_SYSTEM_PROMPT, STRUCTURED_RESPONSE_FORMAT, IMAGE_GENERATION_ENABLED, DOCUMENT_SEARCH_ENABLED
    from command_center.orchestration.landscape_scanner import format_landscape_summary

    messages = state.get("messages", [])
    
    # Always scan platform for real data (cached 60s, fast after first call)
    from command_center.orchestration.landscape_scanner import scan_platform
    try:
        landscape = await scan_platform()
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
5. **Build/create request** (create an agent, build a workflow, set up a connection) → call delegate_to_builder_agent. NEVER pretend to create agents yourself.
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
- Example workflow: user says "create an Excel of top 10 customers and email it to bob@example.com"
  1. Call export_data(format="excel", name="top_10_customers", data='[...]') → get artifact_id from result
  2. Call send_email(to_address="bob@example.com", subject="Top 10 Customers", message="Please find the report attached.", artifact_id="<artifact_id from step 1>")

## CUSTOM TOOLS
You can run previously created custom tools using run_generated_tool. Pass the tool_name and parameters (JSON string).
If the user asks to use a tool that was previously created, use run_generated_tool.

## IMAGE GENERATION
{"You CAN generate images using the generate_image tool (DALL-E 3). When a user asks to create/draw/generate an image, use generate_image with a detailed prompt. Available sizes: 1024x1024, 1024x1792 (portrait), 1792x1024 (landscape)." if IMAGE_GENERATION_ENABLED else "Image generation is NOT available on this instance. If asked to create images, politely explain that image generation is not enabled."}

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

## MAP & VISUALIZATION — USE YOUR OWN TOOLS
- YOU have `generate_map` — use it for maps, choropleths, geographic visualizations.
- Data agents CANNOT create maps. They can only retrieve data.
- If the user asks for a map and you already have the data (from a previous query_data_agent response), call generate_map directly with that data. Do NOT re-query the data agent.
- If you need data first, call query_data_agent to get the data, THEN call generate_map with the results.
- For choropleth maps: pass a JSON object with "regions" array (each has "name", "value", "label").

## LIMITATIONS — BE HONEST
- You CANNOT directly create agents, workflows, or platform resources — you MUST use delegate_to_builder_agent for that.
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
                            # Format table data as markdown
                            table_data = block["content"]
                            if isinstance(table_data, list) and table_data:
                                cols = list(table_data[0].keys())
                                header = " | ".join(cols)
                                sep = " | ".join(["---"] * len(cols))
                                rows = "\n".join(" | ".join(str(r.get(c, "")) for c in cols) for r in table_data)
                                response_parts.append(f"\n{header}\n{sep}\n{rows}")
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
        )
        if result.get("status") == "failed":
            error_text = result.get("text", "Unknown error")
            logger.warning(f"[converse/tool] General agent {agent_id} failed: {error_text}")
            return (f"⚠️ The agent (Agent #{agent_id}) could not complete the request. "
                    f"Error: {error_text}. Please try again shortly.")
        return result.get("text", "No response from agent.")

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
            if result.get("status") == "completed" and result.get("text"):
                return result["text"]
            elif result.get("status") == "failed":
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
    async def send_email(to_address: str, subject: str, message: str, artifact_id: str = "") -> str:
        """Send an email, optionally attaching a previously exported file.
        Use this when the user asks to email, send, or share information via email.

        To attach a file: first call export_data to create the file, then pass
        the artifact_id from the export result to this tool.

        Args:
            to_address: Recipient email address
            subject: Email subject line
            message: Email body content
            artifact_id: Optional artifact_id from a previous export_data call to attach as a file
        """
        import os
        import re
        import base64
        import requests as _requests

        # Validate inputs
        if not to_address or not subject or not message:
            return "Error: Please provide to_address, subject, and message."
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

    tools = [query_data_agent, query_general_agent, delegate_to_builder_agent, save_user_preference, recall_all_memories, forget_preference, switch_active_agent, export_data, run_generated_tool, generate_map, search_web, send_email]
    if IMAGE_GENERATION_ENABLED:
        tools.append(generate_image)
    if DOCUMENT_SEARCH_ENABLED:
        tools.append(search_documents)

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

            for tc in response.tool_calls:
                tool_name = tc["name"]
                tool_args = tc["args"]
                logger.info(f"[converse] Tool call: {tool_name}({tool_args})")

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
                    "run_generated_tool": run_generated_tool,
                    "generate_map": generate_map,
                    "generate_image": generate_image,
                    "search_web": search_web,
                    "search_documents": search_documents,
                    "send_email": send_email,
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
                    result = await tool_fn.ainvoke(tool_args)
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

                # Track delegation for agent query/switch tools
                if tool_name in ("query_data_agent", "query_general_agent"):
                    active_deleg = {
                        "agent_id": str(tool_args.get("agent_id")),
                        "agent_name": f"Agent #{tool_args.get('agent_id')}",
                        "agent_type": "data" if tool_name == "query_data_agent" else "general",
                        "started_at": datetime.now().isoformat(),
                        "history": [],
                    }
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
                    builder_sid = existing.get("builder_session_id") or f"cc-builder-{cc_sid}"
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
                        "build_status": "in_progress",
                        "history": [],
                    }

            # Check if any tool returned renderable blocks (map, artifact)
            # that should pass through directly instead of going back to LLM
            direct_block_types = ("map", "artifact", "table", "image", "kpi")
            direct_blocks = []
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

            # If the follow-up ALSO wants to call tools, the content field may contain
            # garbled tool routing text (to=functions.*). Strip it and use only tool results.
            if _has_tc:
                logger.info(f"[converse] Follow-up wants MORE tool calls — executing second round")
                # Execute second round of tool calls
                tool_results_2 = []
                for tc in final_response.tool_calls:
                    tool_name = tc["name"]
                    tool_args = tc["args"]
                    logger.info(f"[converse] Round 2 tool call: {tool_name}({tool_args})")
                    tool_fn = tool_map.get(tool_name)
                    if tool_fn:
                        r2 = await tool_fn.ainvoke(tool_args)
                    else:
                        r2 = f"Unknown tool: {tool_name}"
                    tool_results_2.append(ToolMessage(content=str(r2), tool_call_id=tc["id"]))

                    # Track delegation
                    if tool_name in ("query_data_agent", "query_general_agent"):
                        active_deleg = {
                            "agent_id": str(tool_args.get("agent_id")),
                            "agent_name": f"Agent #{tool_args.get('agent_id')}",
                            "agent_type": "data" if tool_name == "query_data_agent" else "general",
                            "started_at": datetime.now().isoformat(),
                            "history": [],
                        }

                # Check for direct blocks from round 2
                for tr in tool_results_2:
                    try:
                        parsed = json.loads(tr.content)
                        if isinstance(parsed, list) and parsed and all(
                            isinstance(b, dict) and b.get("type") in ("map", "artifact", "table", "image", "kpi")
                            for b in parsed
                        ):
                            direct_blocks.extend(parsed)
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
                    return result

                # Final LLM pass after round 2
                follow_up_2 = follow_up_messages + [final_response] + tool_results_2
                _r2_t0 = _trace_time.perf_counter()
                final_response = await llm.ainvoke(follow_up_2)
                trace_llm_call(state, node="converse", step="converse_round2",
                               messages=follow_up_2, response=final_response,
                               elapsed_ms=int((_trace_time.perf_counter() - _r2_t0) * 1000), model_hint="full")
                logger.info(f"[converse] Round 2 follow-up: {len(final_response.content)} chars")

            # ── Output sanitizer: catch raw JSON / tool metadata leaking to user ──
            final_response = _sanitize_llm_response(final_response, llm)

            result = {"messages": [final_response]}
            if active_deleg:
                result["active_delegation"] = active_deleg
            return result

        # Sanitize even non-tool responses
        response = _sanitize_llm_response(response, llm)
        return {"messages": [response]}
    except Exception as e:
        logger.error(f"Conversation failed: {e}")
        error_content = json.dumps([{"type": "text", "content": f"I encountered an error: {str(e)}"}])
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
        landscape = await scan_platform()
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
                            blocks.append({
                                "type": "table",
                                "title": block.get("title", ""),
                                "headers": headers,
                                "rows": rows,
                            })
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

    # Tracing helpers
    from graph.tracing import trace_log
    import time as _time

    async def _delegate_to_agent(*, agent_id: str, question: str, is_data_agent: bool, session_id: str, conversation_history=None):
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
        return res

    # ── Check active delegation first ──────────────────────────────────
    active = state.get("active_delegation")
    if active and active.get("agent_id"):
        agent_id = active["agent_id"]
        agent_name = active.get("agent_name", "Data Agent")
        logger.info(f"[gather_data] Continuing delegation to {agent_name} [{agent_id}]")

        # Special-case: builder delegation should never go through delegate_to_agent
        if str(active.get("agent_type") or "").lower() == "builder" or str(agent_id).lower() == "builder":
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
            updated = dict(active)
            updated["builder_session_id"] = builder_sid
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
                _fb_landscape = await scan_platform()
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
        landscape = await scan_platform()
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
- Only respond "ASK: <question>" if there are multiple agents with IDENTICAL scope and you truly cannot determine which is right
- NEVER ask the user to pick if only 1-2 agents exist or if the query clearly maps to one agent

Respond with ONLY the agent_id number OR "ASK: <brief question listing 2-3 options>"."""
    pick_prompt += _preferences_block(state)

    try:
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
                "when they match the request:\n" + "\n".join(agent_lines)
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
[{{"description": "what to do", "target_agent": "agent_id or null", "target_agent_name": "name or null", "is_data_agent": true_or_false, "target_tool": "tool_name or null"}}]

RULES:
- Each task must have EITHER target_agent OR target_tool — not both, not neither.
- Set is_data_agent to match the agent's type from the list above. General agents are NOT data agents.
- Order matters: if Task 2 needs Task 1's results (e.g., search then export), Task 1 must come first. Results from earlier tasks are automatically passed to later tasks.
- Use CC tools for document search, web search, file export, maps, images, and email — these are NOT agent capabilities.
- Use agents for database queries, domain-specific questions, and building platform resources.
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

        sub_tasks = []
        for i, t in enumerate(tasks_data):
            sub_tasks.append({
                "id": str(uuid.uuid4())[:8],
                "description": t.get("description", ""),
                "target_agent": t.get("target_agent"),
                "target_agent_name": t.get("target_agent_name"),
                "is_data_agent": bool(t.get("is_data_agent", True)),
                "target_tool": t.get("target_tool"),
                "status": "pending",
                "inputs": {},
                "outputs": {},
            })

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
    # Extract recent human/AI exchanges so delegated agents have
    # conversational context (not just a cold question).
    messages = state.get("messages", [])
    conversation_history = []
    for msg in messages[-10:]:
        if hasattr(msg, "type") and hasattr(msg, "content"):
            role = "user" if msg.type == "human" else "assistant"
            conversation_history.append({"role": role, "content": msg.content[:500]})

    try:
        if task.get("target_agent"):
            # ── Mode 1: Agent delegation ──────────────────────────────
            from command_center.orchestration.delegator import delegate_to_agent
            from graph.tracing import trace_log
            import time as _time

            agent_id = str(task["target_agent"])
            question = str(task.get("description") or "")
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

        # ── Merge LLM text + preserved rich blocks ──────────────────────
        if preserved_blocks:
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
        step_result = step.get("result")
        if not isinstance(step_result, dict):
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
        wf_id = data.get("workflow_id") or data.get("saved_workflow_id")
        if wf_id:
            resources.append({
                "type": "workflow",
                "id": wf_id,
                "name": data.get("saved_workflow_name", f"Workflow #{wf_id}"),
            })
    return resources


# ─── Node: build ──────────────────────────────────────────────────────────

async def build(state: CommandCenterState) -> dict:
    """Delegate build/create requests to the Builder Agent service.
    
    Acts as a middleman — relays builder responses (including questions)
    back to the user, and passes user answers back to the builder.
    """
    from command_center.orchestration.delegator import delegate_to_builder

    messages = state.get("messages", [])
    last_msg = messages[-1] if messages else None
    user_text = last_msg.content if last_msg and hasattr(last_msg, 'content') else ""

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

        if result.get("status") == "completed" and result.get("text"):
            response_text = result["text"]
        else:
            response_text = "Builder Agent processed the request but returned no visible output."

        # Detect if the user's message is an affirmative/confirmation.
        # Uses the mini-LLM for robust detection instead of brittle keyword matching.
        async def _is_affirmative(txt: str) -> bool:
            t = (txt or "").strip()
            if not t:
                return False
            # Fast path: very short obvious affirmatives (avoid LLM call overhead)
            if t.lower() in {"y", "yes", "ok", "okay"}:
                return True
            try:
                from cc_config import get_step_llm as _get_step_llm_affirm
                _llm = _get_step_llm_affirm("builder_affirmative_detector")
                _aff_msgs = [HumanMessage(content=(
                    f'Is the following message an affirmative confirmation, agreement, '
                    f'or approval to proceed? Message: "{t}"\n'
                    f'Reply with ONLY "YES" or "NO".'
                ))]
                _aff_t0 = _trace_time.perf_counter()
                _resp = await _llm.ainvoke(_aff_msgs)
                trace_llm_call(state, node="build", step="builder_affirmative_detector",
                               messages=_aff_msgs, response=_resp,
                               elapsed_ms=int((_trace_time.perf_counter() - _aff_t0) * 1000), model_hint="mini")
                return _resp.content.strip().upper().startswith("YES") if hasattr(_resp, 'content') else False
            except Exception:
                # Fallback: basic keyword check if LLM fails
                return t.lower() in {"yep", "yeah", "sure", "proceed", "go ahead", "do it",
                                     "execute", "confirm", "confirmed", "absolutely",
                                     "sounds good", "let's do it", "go for it", "make it happen"}

        # Auto-confirm: either (a) user said "yes" and builder is asking to confirm,
        # or (b) builder returned a draft plan with concrete steps (user already
        # provided all needed info, no reason to make them confirm separately).
        _should_auto_confirm = False
        _auto_confirm_reason = ""

        if (await _is_affirmative(user_text)) and ("shall i go ahead" in response_text.lower() or "shall i proceed" in response_text.lower()):
            _should_auto_confirm = True
            _auto_confirm_reason = "user affirmed; builder still asking to confirm"
        elif latest_plan and isinstance(latest_plan, dict):
            plan_status = (latest_plan.get("status") or "").lower()
            plan_steps = latest_plan.get("steps", [])
            if plan_status == "draft" and len(plan_steps) > 0:
                _should_auto_confirm = True
                _auto_confirm_reason = f"builder returned draft plan with {len(plan_steps)} steps — auto-executing"

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

        # Distill builder output into a user-facing message (never show raw JSON)
        raw_builder_text = response_text
        distilled_text = None
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
            distill_prompt = (
                "You are the AI Hub Command Center. You are the user's representative.\n"
                "You received a message from an internal Builder agent. The Builder message may contain raw JSON, internal tool plans, or repeated confirmation prompts.\n\n"
                "RULES:\n"
                "- NEVER output raw JSON or internal tool-call formats.\n"
                "- If the builder is asking for confirmation and the user has NOT confirmed yet, summarize the plan in plain English and ask the user to confirm.\n"
                "- If the user already confirmed (their last message is affirmative) and the builder is still asking to confirm, assume CC already handled execution; summarize current status or final result.\n"
                "- If the builder reports success, output a concise success message (e.g., ✅ Agent created: <name>).\n"
                "- If the builder reports failure, output a concise failure message (❌ Failed: <reason>) plus what you need from the user if anything.\n"
                "- Use the recent user-facing conversation below to interpret short/ambiguous user messages (e.g. 'yes' may refer to an earlier confirmation prompt).\n"
                "- Keep it short.\n\n"
                f"{_bd_conv_block}"
                f"Last user message: {user_text!r}\n"
                f"Builder message (internal): {raw_builder_text[:6000]!r}\n"
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

        # Use distilled if available; otherwise provide a safe fallback that doesn't leak JSON.
        if distilled_text:
            response_text = distilled_text
        else:
            response_text = "I received an internal response from the Builder Agent but couldn't format it safely for display. Please check the Command Center logs/traces for details."

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

    # ── Auto-enrichment removed ────────────────────────────────────────────
    # Previous layers 2 and 3 auto-appended map visualizations (on
    # GAP_GEOGRAPHIC / GAP_FORMAT) and knowledge-base text (on GAP_KNOWLEDGE)
    # to agent responses. The keyword trigger for the map path was too loose —
    # words like "region" and "state" appear in many non-geographic contexts
    # (e.g. "inventory by region" meaning a grouping field). Rather than
    # tuning heuristics, enrichment now flows through the explicit tool path:
    # users ask for a map via "show as map" / "plot on a map" and the LLM
    # invokes the generate_map tool. The _enrich_geographic and
    # _enrich_knowledge helpers below are kept so they can be wired in as
    # explicit tools in the future if desired.
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


async def _enrich_geographic(user_question: str, ai_response: str, mini_llm, trace_state: dict = None) -> dict:
    """
    Attempt to enrich a response with geographic visualization.
    Returns a map block dict, or None if enrichment isn't possible.
    """
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
            return None

        regions = data.get("regions", [])
        if not regions:
            logger.info("[AQG/geo] No regions extracted")
            return None

        # Build map block
        map_block = {
            "type": "map",
            "title": data.get("title", "Geographic Visualization"),
            "center": [39.8, -98.5],  # US center
            "zoom": 4,
            "regions": regions,
        }

        logger.info(f"[AQG/geo] Enriched with {len(regions)} state regions")
        return map_block

    except Exception as e:
        logger.warning(f"[AQG/geo] Enrichment failed: {e}")
        return None


async def _check_fabrication(text: str, mini_llm, trace_state: dict = None) -> bool:
    """
    Use a lightweight LLM call to check if text fabricates business-specific data.
    Returns True if fabrication is detected, False if clean.
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


async def _enrich_knowledge(user_question: str, ai_response: str, mini_llm, trace_state: dict = None) -> str:
    """
    Attempt to fill a knowledge gap with general LLM knowledge.
    Returns enrichment text, or None if not possible.
    """
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
            return None

        # Safety check: LLM-based fabrication detection
        is_fabricated = await _check_fabrication(result_text, mini_llm, trace_state=trace_state)
        if is_fabricated:
            logger.warning("[AQG/knowledge] Rejected — fabrication detected in enrichment response")
            return None

        logger.info(f"[AQG/knowledge] Enrichment provided ({len(result_text)} chars)")
        return result_text

    except Exception as e:
        logger.warning(f"[AQG/knowledge] Enrichment failed: {e}")
        return None
