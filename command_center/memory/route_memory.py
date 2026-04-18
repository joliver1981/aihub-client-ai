"""
Command Center — Route Memory
=================================
Lightweight query-to-route log that learns optimal routing over time.

Architecture:
  - log_route(): After each response, fire-and-forget log with one mini-LLM
    call to normalize the query into a canonical form (3-6 words).
  - find_route(): At query time, normalize the incoming query the same way,
    then SQL-match on normalized_query.  Returns a confident match only when
    usage >= 3 AND success_rate >= 70%.
  - If no match or the shortcut fails, the full discovery path runs as usual.

CC Tool Routing:
  When the Command Center handles a query with its own tools (search_documents,
  export_data, etc.) instead of delegating to an agent, the route is logged with
  a synthetic agent_id (e.g., "cc:search_documents") so that future similar
  queries shortcut to the correct intent (chat) rather than routing to a data
  agent.  Entries with neither agent_id nor cc_tool are filtered as noise.

Session Insights:
  After multi-turn conversations (3+ user turns), extract_session_insight()
  uses LLM to distill factual discoveries from the conversation.  These are
  stored in cc_UserMemory as memory_type="insight" and surfaced in future
  conversations to avoid repeating discovery processes.

Uses SQL Server with RLS for multi-tenant safety.
Falls back to in-memory storage when DB is unavailable.
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

# Add project root to path for imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

logger = logging.getLogger(__name__)

# ─── Configuration ────────────────────────────────────────────────────────

# In-memory fallback storage
_route_store: Dict[int, List[dict]] = {}
_use_db = True

# Mini-LLM timeout for the normalization call
_NORMALIZE_TIMEOUT = 2.0  # seconds

# Insight extraction timeout (longer — more tokens to process)
_INSIGHT_TIMEOUT = 8.0  # seconds

# Confidence thresholds for route shortcuts
MIN_USAGE_COUNT = 2
MIN_SUCCESS_RATE = 0.7

# CC tool synthetic agent prefix — used to distinguish CC-native tool routes
# from real agent delegations.  agent_id = "cc:<tool_name>"
CC_TOOL_PREFIX = "cc:"

# CC tools that should be tracked in route memory when used
CC_TRACKABLE_TOOLS = frozenset({
    "search_documents", "search_web", "export_data",
    "generate_map", "generate_image", "send_email",
})

# Human-readable names for CC tools (used as agent_name in route entries)
CC_TOOL_DISPLAY_NAMES = {
    "search_documents": "CC Document Search",
    "search_web": "CC Web Search",
    "export_data": "CC Data Export",
    "generate_map": "CC Map Generator",
    "generate_image": "CC Image Generator",
    "send_email": "CC Email",
}

# Queries that carry no routing information — skip logging
TRIVIAL_QUERIES = frozenset({
    "yes", "no", "ok", "okay", "sure", "proceed", "go ahead", "thanks",
    "thank you", "please", "yes please", "that's right", "correct",
    "no thanks", "cancel", "stop", "never mind", "nvm", "y", "n",
    "go", "do it", "continue", "right", "yep", "nope", "agreed",
})

_NORMALIZE_PROMPT = """Reduce this user query to a short canonical form (3-6 lowercase words).
Keep the core topic and action. Strip dates, names, numbers, and filler words.
Examples:
  "Show me Q4 sales by region" → "sales by region"
  "How many units are in warehouse 7?" → "inventory unit count"
  "What was our revenue last month compared to this month?" → "revenue comparison over time"
  "Create a new agent for tracking expenses" → "create expense tracking agent"
  "List all available agents" → "list agents"
  "Search the web for latest AI news" → "web search news"
Query: "{query}"
Reply with ONLY the canonical form, nothing else."""


# ─── Database Helper ──────────────────────────────────────────────────────

def _db_execute(query, params=None, fetch=False):
    """Execute a query against cc_RouteMemory with proper RLS context.

    Same pattern as user_memory._cc_memory_db_execute:
      1. get_db_connection()
      2. EXEC tenant.sp_setTenantContext
      3. Execute
      4. Commit + close
    """
    try:
        from CommonUtils import get_db_connection

        conn = get_db_connection()
        cursor = conn.cursor()

        api_key = os.environ.get("API_KEY") or os.environ.get("AI_HUB_API_KEY") or ""
        cursor.execute("EXEC tenant.sp_setTenantContext ?", api_key)

        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)

        if fetch:
            rows = cursor.fetchall()
            conn.commit()
            conn.close()
            return rows
        else:
            conn.commit()
            conn.close()
            return None

    except Exception as e:
        logger.error(f"[route_memory] Database error: {e}")
        global _use_db
        _use_db = False
        raise


# ─── LLM Normalizer ──────────────────────────────────────────────────────

def _get_mini_llm():
    """Get the mini LLM instance for normalization calls."""
    from command_center_service.cc_config import get_llm
    return get_llm(mini=True, streaming=False)


async def _normalize_query(query: str) -> str:
    """Use mini-LLM to produce a short canonical form (3-6 words).

    Returns the canonical form on success, or empty string on failure/timeout.
    The caller should still log with normalized_query=NULL when this returns "".
    """
    try:
        llm = _get_mini_llm()
        prompt = _NORMALIZE_PROMPT.format(query=query[:300])
        resp = await asyncio.wait_for(llm.ainvoke(prompt), timeout=_NORMALIZE_TIMEOUT)
        canonical = resp.content.strip().strip('"').strip("'").lower()
        # Enforce reasonable length — if the LLM returned something too long, truncate
        words = canonical.split()
        if len(words) > 8:
            canonical = " ".join(words[:6])
        return canonical[:200]  # DB column is NVARCHAR(200)
    except asyncio.TimeoutError:
        logger.warning("[route_memory] Normalization timed out")
        return ""
    except Exception as e:
        logger.warning(f"[route_memory] Normalization failed: {e}")
        return ""


_SUCCESS_PROMPT = """Did this AI assistant response successfully help the user?
Answer "yes" if the response provided useful data, took a requested action, or gave a substantive answer.
Answer "no" if the response failed to retrieve data, reported an error, asked the user to try again, or could not fulfill the request.
Reply with ONLY "yes" or "no".

User: "{query}"
Response: "{response}"
"""


_LOG_QUESTION_PROMPT = """You are selecting what question to log to route memory — a system that
learns which AI agent handles which types of user questions.

The user's latest message may be a substantive question OR an orchestration
instruction like "use a different agent". Identify the SUBSTANTIVE QUESTION
that best represents what the AI actually answered in this turn.

Patterns:
- Simple question ("show me sales by region") → return it as-is
- Routing instruction ("use the Inventory agent instead", "try a different agent",
  "get X from the Y agent") → return the UNDERLYING question from the prior
  conversation that is actually being rerouted, NOT the routing instruction
- Topic shift ("now show me inventory") → return it as-is
- Refinement ("I meant 2024 not 2023") → return the full corrected question
  by combining with the prior turn

The logged question must be a STANDALONE query a future user could ask with
no context. "Use X agent instead" is not standalone. "Show me inventory top
10 items by quantity" is.

Recent conversation (chronological, most recent last):
{transcript}

User's latest message: "{user_message}"

Reply with ONLY the question text to log. No preamble, no quotes, no labels,
no commentary.
"""


async def _extract_question_to_log(
    user_message: str,
    conversation_transcript: str,
    timeout: float = 2.0,
) -> str:
    """Use a mini-LLM to pick the substantive question that should be stored
    in route memory for this turn.

    Handles the case where the user's latest message is a routing instruction
    ("use X agent instead") rather than a substantive question — the LLM
    returns the underlying question from prior context instead.

    Returns the extracted question text. Falls back to the raw user_message
    on timeout, error, empty response, or if there's no prior context to
    reason about (an empty transcript means the latest message IS the question).
    """
    # No prior context → latest message IS the question, skip the LLM call.
    if not conversation_transcript or not conversation_transcript.strip():
        return user_message

    try:
        llm = _get_mini_llm()
        prompt = _LOG_QUESTION_PROMPT.format(
            transcript=conversation_transcript[:3000],
            user_message=user_message[:400],
        )
        resp = await asyncio.wait_for(llm.ainvoke(prompt), timeout=timeout)
        result = (resp.content if hasattr(resp, "content") else str(resp)).strip()

        # Strip common LLM wrappers
        result = result.strip('"').strip("'").strip()
        for prefix in ("Question:", "Logged question:", "Substantive question:",
                       "The question is:"):
            if result.lower().startswith(prefix.lower()):
                result = result[len(prefix):].strip().strip('"').strip("'")
                break

        # Guards
        if not result:
            return user_message
        if len(result) > 500:
            return user_message  # LLM rambled; fall back to raw text
        # If the LLM just echoed the latest message verbatim, that's fine —
        # return what we got.
        return result

    except asyncio.TimeoutError:
        logger.warning("[route_memory] Question-to-log extraction timed out — falling back to raw user message")
        return user_message
    except Exception as e:
        logger.warning(f"[route_memory] Question-to-log extraction failed: {e} — falling back to raw user message")
        return user_message


async def _classify_success(query: str, response_text: str) -> bool:
    """Use mini-LLM to determine if the response successfully answered the query.

    Returns True (success) on LLM failure/timeout — conservative default so that
    a classification failure doesn't poison route confidence.
    """
    try:
        llm = _get_mini_llm()
        prompt = _SUCCESS_PROMPT.format(query=query[:200], response=response_text[:500])
        resp = await asyncio.wait_for(llm.ainvoke(prompt), timeout=_NORMALIZE_TIMEOUT)
        answer = resp.content.strip().lower().strip('"').strip("'")
        return answer != "no"
    except asyncio.TimeoutError:
        logger.warning("[route_memory] Success classification timed out — defaulting to True")
        return True
    except Exception as e:
        logger.warning(f"[route_memory] Success classification failed: {e} — defaulting to True")
        return True


# ─── Trivial Query Detection ─────────────────────────────────────────────

def _is_trivial(query: str) -> bool:
    """Check if a query is a trivial confirmation/rejection that carries no routing info."""
    stripped = query.strip().lower().rstrip("!?.,:;")
    if len(stripped) < 10 and stripped in TRIVIAL_QUERIES:
        return True
    # Also catch very short queries that are just numbers or single words
    if len(stripped) < 4:
        return True
    return False


# ─── Logging (fire-and-forget, after response) ───────────────────────────

async def log_route(
    user_id: int,
    query_text: str,
    intent: str,
    agent_id: str = None,
    agent_name: str = None,
    route_path: str = None,
    success: bool = True,
    latency_ms: int = None,
    response_text: str = None,
    cc_tool_name: str = None,
    conversation_transcript: str = None,
) -> None:
    """Log a route entry after a response is sent.  Background, non-blocking.

    Steps:
      1. Skip trivial queries
      2. Skip entries with no routing information (no agent, no CC tool)
      3. If conversation_transcript is provided, ask a mini-LLM to pick the
         substantive question to log (handles reroute instructions and other
         cases where the latest user message is not the true question).
      4. In parallel: normalize query + classify success (two mini-LLM calls, 2s timeout each)
      5. INSERT into cc_RouteMemory

    Args:
        cc_tool_name: When the CC handled the query with a native tool (e.g.,
            "search_documents"), pass the tool name here.  A synthetic agent_id
            like "cc:search_documents" is created so route memory can learn
            CC tool routes alongside agent routes.
        conversation_transcript: Optional formatted transcript of recent turns
            (excluding the latest). When provided, a mini-LLM is used to
            identify the substantive question being answered — this prevents
            logging routing instructions ("use X agent instead") as if they
            were the question. Without a transcript, query_text is logged as-is.
    """
    try:
        if _is_trivial(query_text):
            return

        # ── CC tool → synthetic agent_id ──────────────────────────────────
        # When the Command Center used its own tools (search_documents, etc.),
        # create a synthetic agent_id so route memory captures the pattern.
        # Without this, intent=chat entries with agent_id=None are pure noise
        # (find_route requires agent_id IS NOT NULL).
        if cc_tool_name and cc_tool_name in CC_TRACKABLE_TOOLS and not agent_id:
            agent_id = f"{CC_TOOL_PREFIX}{cc_tool_name}"
            agent_name = CC_TOOL_DISPLAY_NAMES.get(cc_tool_name, f"CC {cc_tool_name}")

        # ── Noise filter: skip entries with no routing information ─────────
        # Entries with neither an agent nor a CC tool can never produce a
        # route shortcut — they just add dead weight to the database.
        if not agent_id:
            logger.debug(
                f"[route_memory] Skipping noise entry: intent={intent}, "
                f"no agent_id or cc_tool for query '{query_text[:60]}...'"
            )
            return

        # ── Pick the substantive question to log ──────────────────────────
        # When the caller supplies a conversation transcript, ask a mini-LLM
        # to identify the real question being answered. This handles reroute
        # instructions ("use X agent instead"), clarifications, and
        # refinements — the raw latest message isn't always the right thing
        # to log. The extractor falls back to query_text on failure, so this
        # is always safe to enable.
        original_query_text = query_text
        if conversation_transcript:
            query_text = await _extract_question_to_log(
                user_message=query_text,
                conversation_transcript=conversation_transcript,
            )
            if query_text != original_query_text:
                logger.info(
                    f"[route_memory] Logged question replaced: "
                    f"'{original_query_text[:60]}...' → '{query_text[:60]}...'"
                )

        # Run normalize + success classification in parallel (two mini-LLM calls)
        if response_text:
            normalized, success = await asyncio.gather(
                _normalize_query(query_text),
                _classify_success(query_text, response_text),
            )
        else:
            normalized = await _normalize_query(query_text)

        if _use_db:
            _db_execute(
                "INSERT INTO cc_RouteMemory "
                "(user_id, query_text, normalized_query, intent, agent_id, agent_name, "
                "route_path, success, latency_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    user_id,
                    query_text[:500],
                    normalized or None,  # NULL if normalization failed
                    intent[:50],
                    str(agent_id)[:50] if agent_id else None,
                    agent_name[:200] if agent_name else None,
                    route_path[:200] if route_path else None,
                    1 if success else 0,
                    latency_ms,
                ],
            )
        else:
            # In-memory fallback
            entry = {
                "id": int(time.time() * 1000) % 2_000_000_000,  # pseudo-id
                "user_id": user_id,
                "query_text": query_text[:500],
                "normalized_query": normalized or None,
                "intent": intent,
                "agent_id": str(agent_id) if agent_id else None,
                "agent_name": agent_name,
                "route_path": route_path,
                "success": success,
                "latency_ms": latency_ms,
                "created_at": datetime.utcnow(),
            }
            _route_store.setdefault(user_id, []).append(entry)

        logger.info(
            f"[route_memory] Logged: '{normalized or '(raw)'}' "
            f"intent={intent} agent={agent_id} success={success}"
        )

    except Exception as e:
        logger.warning(f"[route_memory] log_route failed (non-blocking): {e}")


# ─── Lookup (at query time) ──────────────────────────────────────────────

async def find_route(user_id: int, query: str) -> Optional[Dict[str, Any]]:
    """Find a confident route match for a query.

    Steps:
      1. Normalize incoming query via mini-LLM (~100-200ms)
      2. SQL exact match on normalized_query
      3. Return match only if usage >= MIN_USAGE_COUNT and success_rate >= MIN_SUCCESS_RATE

    Returns dict with {agent_id, agent_name, intent, normalized_query,
    usage_count, success_rate, is_cc_tool} or None.

    When agent_id starts with "cc:" (e.g., "cc:search_documents"), the match
    is a CC-native tool route.  The caller should use intent (typically "chat")
    to route correctly — the CC converse node handles the tool natively.
    """
    try:
        if _is_trivial(query):
            return None

        # Normalize the incoming query
        normalized = await _normalize_query(query)
        if not normalized:
            return None  # Normalization failed — fall through to full discovery

        if _use_db:
            rows = _db_execute(
                "SELECT TOP 1 agent_id, agent_name, intent, "
                "  COUNT(*) as usage_count, "
                "  CAST(SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) as success_rate "
                "FROM cc_RouteMemory "
                "WHERE user_id = ? AND normalized_query = ? AND agent_id IS NOT NULL "
                "GROUP BY agent_id, agent_name, intent "
                "HAVING COUNT(*) >= ? "
                "ORDER BY "
                "  CAST(SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) DESC, "
                "  COUNT(*) DESC",
                [user_id, normalized, MIN_USAGE_COUNT],
                fetch=True,
            )
            if rows:
                agent_id, agent_name, intent, usage_count, success_rate = rows[0]
                if success_rate >= MIN_SUCCESS_RATE:
                    is_cc = bool(agent_id and str(agent_id).startswith(CC_TOOL_PREFIX))
                    return {
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "intent": intent,
                        "normalized_query": normalized,
                        "usage_count": usage_count,
                        "success_rate": success_rate,
                        "is_cc_tool": is_cc,
                    }
            return None
        else:
            # In-memory fallback
            entries = _route_store.get(user_id, [])
            matching = [e for e in entries
                        if e.get("normalized_query") == normalized and e.get("agent_id")]
            if len(matching) < MIN_USAGE_COUNT:
                return None

            # Group by agent_id and pick the best
            from collections import Counter
            agent_counts: Dict[str, Dict[str, Any]] = {}
            for e in matching:
                aid = e["agent_id"]
                if aid not in agent_counts:
                    agent_counts[aid] = {
                        "agent_id": aid,
                        "agent_name": e.get("agent_name"),
                        "intent": e.get("intent"),
                        "total": 0,
                        "successes": 0,
                    }
                agent_counts[aid]["total"] += 1
                if e.get("success"):
                    agent_counts[aid]["successes"] += 1

            for aid, stats in sorted(
                agent_counts.items(),
                key=lambda x: (x[1]["successes"] / max(x[1]["total"], 1), x[1]["total"]),
                reverse=True,
            ):
                if stats["total"] >= MIN_USAGE_COUNT:
                    rate = stats["successes"] / stats["total"]
                    if rate >= MIN_SUCCESS_RATE:
                        is_cc = bool(stats["agent_id"] and str(stats["agent_id"]).startswith(CC_TOOL_PREFIX))
                        return {
                            "agent_id": stats["agent_id"],
                            "agent_name": stats["agent_name"],
                            "intent": stats["intent"],
                            "normalized_query": normalized,
                            "usage_count": stats["total"],
                            "success_rate": rate,
                            "is_cc_tool": is_cc,
                        }
            return None

    except Exception as e:
        logger.warning(f"[route_memory] find_route failed (non-blocking): {e}")
        return None


# ─── Suggestion Chips ─────────────────────────────────────────────────────

def get_route_suggestions(user_id: int, limit: int = 5) -> List[dict]:
    """Top distinct routes by success_count * recency, for suggestion chips.

    Groups by normalized_query, returns the most recent query_text as
    the display prompt.
    """
    if not _use_db:
        entries = _route_store.get(user_id, [])
        if not entries:
            return []
        # Group by normalized_query
        groups: Dict[str, List[dict]] = {}
        for e in entries:
            nq = e.get("normalized_query")
            if nq:
                groups.setdefault(nq, []).append(e)

        suggestions = []
        now = time.time()
        for nq, group in groups.items():
            total = len(group)
            successes = sum(1 for e in group if e.get("success"))
            if total < 2:
                continue
            rate = successes / total
            latest = max(group, key=lambda e: e.get("created_at", datetime.min))
            age_hours = (now - latest["created_at"].timestamp()) / 3600 if latest.get("created_at") else 24
            recency = max(0.1, 1.0 / (1 + age_hours / 24))
            score = successes * recency

            suggestions.append({
                "prompt": latest.get("query_text", nq),
                "description": f"{nq} | {latest.get('agent_name', '')} | {total}x used | {int(rate*100)}% success",
                "score": score,
                "source": "route",
                "normalized_query": nq,
                "success_rate": rate,
                "route_id": latest.get("id"),
            })

        suggestions.sort(key=lambda s: s["score"], reverse=True)
        return suggestions[:limit]

    try:
        rows = _db_execute(
            "SELECT normalized_query, "
            "  MAX(agent_name) as agent_name, "
            "  COUNT(*) as usage_count, "
            "  SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as success_count, "
            "  CAST(SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) as success_rate, "
            "  MAX(created_at) as last_used, "
            "  MAX(id) as latest_id "
            "FROM cc_RouteMemory "
            "WHERE user_id = ? AND normalized_query IS NOT NULL "
            "GROUP BY normalized_query "
            "HAVING COUNT(*) >= 2 "
            "ORDER BY SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) DESC, MAX(created_at) DESC",
            [user_id],
            fetch=True,
        )

        if not rows:
            return []

        suggestions = []
        now = time.time()
        for row in rows[:limit * 2]:  # fetch extra, we'll filter + sort
            nq, agent_name, usage_count, success_count, success_rate, last_used, latest_id = row

            # Get the most recent query_text for display
            text_rows = _db_execute(
                "SELECT TOP 1 query_text FROM cc_RouteMemory "
                "WHERE user_id = ? AND normalized_query = ? ORDER BY created_at DESC",
                [user_id, nq],
                fetch=True,
            )
            prompt_text = text_rows[0][0] if text_rows else nq

            # Recency scoring
            recency = 1.0
            if last_used:
                if isinstance(last_used, str):
                    last_used = datetime.fromisoformat(last_used.replace('Z', '+00:00'))
                age_hours = (now - last_used.timestamp()) / 3600
                recency = max(0.1, 1.0 / (1 + age_hours / 24))

            score = (success_count or 0) * recency

            suggestions.append({
                "prompt": prompt_text,
                "description": f"{nq} | {agent_name or ''} | {usage_count}x used | {int((success_rate or 0)*100)}% success",
                "score": score,
                "source": "route",
                "normalized_query": nq,
                "success_rate": success_rate or 0,
                "route_id": latest_id,
            })

        suggestions.sort(key=lambda s: s["score"], reverse=True)
        return suggestions[:limit]

    except Exception as e:
        logger.error(f"[route_memory] get_route_suggestions failed: {e}")
        return []


# ─── Management ───────────────────────────────────────────────────────────

def get_all_routes(user_id: int, limit: int = 100) -> List[dict]:
    """Aggregated route stats for the management UI.

    Groups by normalized_query → shows canonical form, agent, intent,
    usage count, success rate, last used, sample queries.
    """
    if not _use_db:
        entries = _route_store.get(user_id, [])
        groups: Dict[str, List[dict]] = {}
        for e in entries:
            nq = e.get("normalized_query") or "(unclassified)"
            groups.setdefault(nq, []).append(e)

        result = []
        for nq, group in groups.items():
            total = len(group)
            successes = sum(1 for e in group if e.get("success"))
            latest = max(group, key=lambda e: e.get("created_at", datetime.min))
            sample_queries = list({e.get("query_text", "") for e in group[-5:]})
            result.append({
                "normalized_query": nq,
                "agent_name": latest.get("agent_name"),
                "agent_id": latest.get("agent_id"),
                "intent": latest.get("intent"),
                "usage_count": total,
                "success_count": successes,
                "success_rate": successes / total if total > 0 else 0,
                "last_used": latest.get("created_at", "").isoformat() if isinstance(latest.get("created_at"), datetime) else str(latest.get("created_at", "")),
                "sample_queries": sample_queries[:3],
            })
        result.sort(key=lambda r: r["usage_count"], reverse=True)
        return result[:limit]

    try:
        rows = _db_execute(
            "SELECT normalized_query, "
            "  MAX(agent_name) as agent_name, "
            "  MAX(agent_id) as agent_id, "
            "  MAX(intent) as intent, "
            "  COUNT(*) as usage_count, "
            "  SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as success_count, "
            "  CAST(SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) as success_rate, "
            "  MAX(created_at) as last_used "
            "FROM cc_RouteMemory "
            "WHERE user_id = ? "
            "GROUP BY normalized_query "
            "ORDER BY COUNT(*) DESC",
            [user_id],
            fetch=True,
        )

        if not rows:
            return []

        result = []
        for row in rows[:limit]:
            nq, agent_name, agent_id, intent, usage_count, success_count, success_rate, last_used = row

            # Get sample queries for this canonical form
            sample_rows = _db_execute(
                "SELECT TOP 3 query_text FROM cc_RouteMemory "
                "WHERE user_id = ? AND normalized_query = ? ORDER BY created_at DESC",
                [user_id, nq],
                fetch=True,
            )
            sample_queries = [r[0] for r in (sample_rows or [])]

            result.append({
                "normalized_query": nq or "(unclassified)",
                "agent_name": agent_name,
                "agent_id": agent_id,
                "intent": intent,
                "usage_count": usage_count,
                "success_count": success_count,
                "success_rate": success_rate or 0,
                "last_used": last_used.isoformat() if hasattr(last_used, 'isoformat') else str(last_used or ""),
                "sample_queries": sample_queries,
            })

        return result

    except Exception as e:
        logger.error(f"[route_memory] get_all_routes failed: {e}")
        return []


def delete_route(user_id: int, route_id: int) -> bool:
    """Delete a single route entry by its id."""
    if not _use_db:
        entries = _route_store.get(user_id, [])
        original_len = len(entries)
        _route_store[user_id] = [e for e in entries if e.get("id") != route_id]
        return len(_route_store[user_id]) < original_len

    try:
        _db_execute(
            "DELETE FROM cc_RouteMemory WHERE user_id = ? AND id = ?",
            [user_id, route_id],
        )
        return True
    except Exception as e:
        logger.error(f"[route_memory] delete_route failed: {e}")
        return False


def delete_routes_by_canonical(user_id: int, normalized_query: str) -> int:
    """Delete all entries for a canonical form (user clears a learned route group)."""
    if not _use_db:
        entries = _route_store.get(user_id, [])
        original_len = len(entries)
        _route_store[user_id] = [e for e in entries if e.get("normalized_query") != normalized_query]
        return original_len - len(_route_store[user_id])

    try:
        count_rows = _db_execute(
            "SELECT COUNT(*) FROM cc_RouteMemory WHERE user_id = ? AND normalized_query = ?",
            [user_id, normalized_query],
            fetch=True,
        )
        count = count_rows[0][0] if count_rows else 0
        _db_execute(
            "DELETE FROM cc_RouteMemory WHERE user_id = ? AND normalized_query = ?",
            [user_id, normalized_query],
        )
        return count
    except Exception as e:
        logger.error(f"[route_memory] delete_routes_by_canonical failed: {e}")
        return 0


def delete_all_routes(user_id: int) -> int:
    """Delete all route entries for a user. Returns count deleted."""
    if not _use_db:
        entries = _route_store.get(user_id, [])
        count = len(entries)
        _route_store[user_id] = []
        return count

    try:
        count_rows = _db_execute(
            "SELECT COUNT(*) FROM cc_RouteMemory WHERE user_id = ?",
            [user_id],
            fetch=True,
        )
        count = count_rows[0][0] if count_rows else 0
        _db_execute(
            "DELETE FROM cc_RouteMemory WHERE user_id = ?",
            [user_id],
        )
        return count
    except Exception as e:
        logger.error(f"[route_memory] delete_all_routes failed: {e}")
        return 0


def get_route_stats(user_id: int) -> dict:
    """Aggregate stats for the recall_all_memories tool and admin display."""
    if not _use_db:
        entries = _route_store.get(user_id, [])
        if not entries:
            return {"total_routes": 0, "unique_canonical_forms": 0, "top_routes": [], "avg_success_rate": 0}
        canonicals = {e.get("normalized_query") for e in entries if e.get("normalized_query")}
        successes = sum(1 for e in entries if e.get("success"))
        return {
            "total_routes": len(entries),
            "unique_canonical_forms": len(canonicals),
            "top_routes": list(canonicals)[:5],
            "avg_success_rate": successes / len(entries) if entries else 0,
        }

    try:
        rows = _db_execute(
            "SELECT COUNT(*) as total, "
            "  COUNT(DISTINCT normalized_query) as unique_forms, "
            "  CAST(SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS FLOAT) / NULLIF(COUNT(*), 0) as avg_rate "
            "FROM cc_RouteMemory WHERE user_id = ?",
            [user_id],
            fetch=True,
        )
        total, unique_forms, avg_rate = rows[0] if rows else (0, 0, 0)

        top_rows = _db_execute(
            "SELECT TOP 5 normalized_query, COUNT(*) as cnt "
            "FROM cc_RouteMemory WHERE user_id = ? AND normalized_query IS NOT NULL "
            "GROUP BY normalized_query ORDER BY COUNT(*) DESC",
            [user_id],
            fetch=True,
        )
        top_routes = [r[0] for r in (top_rows or [])]

        return {
            "total_routes": total or 0,
            "unique_canonical_forms": unique_forms or 0,
            "top_routes": top_routes,
            "avg_success_rate": avg_rate or 0,
        }
    except Exception as e:
        logger.error(f"[route_memory] get_route_stats failed: {e}")
        return {"total_routes": 0, "unique_canonical_forms": 0, "top_routes": [], "avg_success_rate": 0}


# ─── Session Insight Extraction ──────────────────────────────────────────
#
# After multi-turn conversations, extract factual discoveries that should be
# remembered across sessions.  Unlike route memory (which learns "query X →
# agent Y"), insights capture domain knowledge learned through exploration:
#   - "Store S009 lease is document type 'commercial_lease_agreement'"
#   - "Northeast region includes NY, CT, MA, RI, VT, NH, ME"
#   - "Agent 14 handles sales data, not inventory"
#
# Insights are stored in cc_UserMemory (memory_type="insight") and surfaced
# in future conversations via the user_memory context string.

_INSIGHT_EXTRACTION_PROMPT = """You are a knowledge extraction assistant. Given a multi-turn conversation between a user and an AI assistant, extract any FACTUAL DISCOVERIES that would be useful to remember for future similar queries.

Focus on:
1. **Entity-specific facts**: "Store S009 is at Peach Plaza, 87 Peachtree Street, Atlanta, GA 30303"
2. **Classification corrections**: "S009's lease is document type 'commercial_lease_agreement', not 'lease_agreement'"
3. **Successful approaches**: "To find store leases, search by document type 'commercial_lease_agreement'"
4. **Entity relationships**: "Store S009 = Peach Plaza = 87 Peachtree Street"
5. **Tool/agent routing**: "Document searches should use search_documents, not data agents"

Do NOT extract:
- Vague observations ("the user searched for a document")
- Obvious facts derivable from the query itself
- Temporary state ("the search returned 200 results")
- Anything speculative or uncertain

If the conversation contains no reusable factual discoveries, reply with ONLY: {"insights": []}

Otherwise reply with ONLY a JSON object:
{
  "insights": [
    {
      "topic": "short topic label (3-8 words)",
      "insight": "the factual discovery in a clear, complete sentence",
      "entities": ["key entities mentioned — names, IDs, addresses, doc types"],
      "tool_hint": "optional: which tool or approach works best for this type of query"
    }
  ]
}"""


async def extract_session_insights(
    user_id: int,
    session_id: str,
    conversation_messages: list,
) -> List[dict]:
    """Extract factual insights from a multi-turn conversation.

    Called after route logging when the session has enough turns to warrant
    insight extraction.  Uses LLM to identify reusable knowledge.

    Args:
        user_id: The user's ID for storing insights
        session_id: The session these messages came from
        conversation_messages: List of LangChain message objects (HumanMessage, AIMessage, etc.)

    Returns:
        List of extracted insight dicts, or empty list if none found.
    """
    try:
        from command_center_service.cc_config import USE_SESSION_INSIGHTS, SESSION_INSIGHT_MIN_TURNS

        if not USE_SESSION_INSIGHTS:
            return []

        # Count user turns (HumanMessage instances)
        user_turns = sum(
            1 for m in conversation_messages
            if hasattr(m, 'type') and m.type == 'human'
        )

        if user_turns < SESSION_INSIGHT_MIN_TURNS:
            logger.debug(f"[session_insight] Only {user_turns} user turns, need {SESSION_INSIGHT_MIN_TURNS} — skipping")
            return []

        # Build a compact conversation transcript for the LLM
        transcript_lines = []
        for msg in conversation_messages:
            if hasattr(msg, 'type'):
                if msg.type == 'human':
                    transcript_lines.append(f"USER: {msg.content[:500]}")
                elif msg.type == 'ai':
                    # Truncate AI responses more aggressively — they're verbose
                    content = msg.content[:800] if msg.content else ""
                    transcript_lines.append(f"ASSISTANT: {content}")
                elif msg.type == 'tool':
                    # Include tool results briefly — they contain factual data
                    content = msg.content[:300] if msg.content else ""
                    tool_name = getattr(msg, 'name', 'tool')
                    transcript_lines.append(f"TOOL({tool_name}): {content}")

        if len(transcript_lines) < 4:
            return []

        transcript = "\n".join(transcript_lines)
        # Cap total transcript length to avoid token overflow
        if len(transcript) > 6000:
            transcript = transcript[:6000] + "\n... (truncated)"

        # Call LLM for insight extraction
        llm = _get_mini_llm()
        prompt = (
            f"{_INSIGHT_EXTRACTION_PROMPT}\n\n"
            f"CONVERSATION ({user_turns} user turns):\n{transcript}"
        )

        resp = await asyncio.wait_for(llm.ainvoke(prompt), timeout=_INSIGHT_TIMEOUT)
        result_text = resp.content.strip() if hasattr(resp, 'content') else str(resp)

        # Parse response
        if "```" in result_text:
            result_text = result_text.split("```")[1]
            if result_text.startswith("json"):
                result_text = result_text[4:]
            result_text = result_text.strip()

        parsed = json.loads(result_text)
        insights = parsed.get("insights", [])

        if not insights:
            logger.debug("[session_insight] No insights extracted from conversation")
            return []

        # Store each insight
        stored = []
        for insight_data in insights[:5]:  # Cap at 5 insights per session
            topic = insight_data.get("topic", "").strip()
            insight_text = insight_data.get("insight", "").strip()
            entities = insight_data.get("entities", [])
            tool_hint = insight_data.get("tool_hint", "")

            if not topic or not insight_text:
                continue

            try:
                _save_insight(
                    user_id=user_id,
                    topic=topic,
                    insight_text=insight_text,
                    entities=entities,
                    tool_hint=tool_hint,
                    session_id=session_id,
                )
                stored.append(insight_data)
                logger.info(f"[session_insight] Stored: '{topic}' — {insight_text[:80]}...")
            except Exception as save_err:
                logger.warning(f"[session_insight] Failed to save insight '{topic}': {save_err}")

        if stored:
            logger.info(f"[session_insight] Extracted {len(stored)} insight(s) from session {session_id}")
        return stored

    except asyncio.TimeoutError:
        logger.warning("[session_insight] Insight extraction timed out")
        return []
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"[session_insight] Failed to parse insight response: {e}")
        return []
    except Exception as e:
        logger.warning(f"[session_insight] Extraction failed (non-blocking): {e}")
        return []


def _save_insight(
    user_id: int,
    topic: str,
    insight_text: str,
    entities: list,
    tool_hint: str = "",
    session_id: str = "",
) -> None:
    """Store an insight in cc_UserMemory. Upserts by topic (memory_key)."""
    memory_value = json.dumps({
        "insight": insight_text,
        "entities": entities,
        "tool_hint": tool_hint,
        "session_id": session_id,
        "extracted_at": datetime.utcnow().isoformat(),
    })

    if not _use_db:
        # In-memory fallback — store alongside preferences
        from command_center.memory.user_memory import update_preference
        update_preference(user_id, f"insight:{topic}", json.loads(memory_value))
        return

    try:
        from command_center.memory.user_memory import _cc_memory_db_execute

        # Check for existing insight with same topic
        existing = _cc_memory_db_execute(
            "SELECT id, memory_value FROM cc_UserMemory "
            "WHERE user_id = ? AND memory_type = 'insight' AND memory_key = ?",
            [user_id, topic[:200]],
            fetch=True,
        )

        if existing:
            # Update existing — merge entities, keep latest insight
            old_id = existing[0][0]
            try:
                old_val = json.loads(existing[0][1]) if isinstance(existing[0][1], str) else {}
            except Exception:
                old_val = {}
            old_entities = old_val.get("entities", [])
            merged_entities = list(set(old_entities + entities))

            updated_value = json.dumps({
                "insight": insight_text,
                "entities": merged_entities,
                "tool_hint": tool_hint or old_val.get("tool_hint", ""),
                "session_id": session_id,
                "extracted_at": datetime.utcnow().isoformat(),
                "previous_insight": old_val.get("insight", ""),
            })

            _cc_memory_db_execute(
                "UPDATE cc_UserMemory SET memory_value = ?, last_used = GETUTCDATE(), "
                "usage_count = usage_count + 1 WHERE id = ?",
                [updated_value, old_id],
            )
        else:
            # Insert new insight
            _cc_memory_db_execute(
                "INSERT INTO cc_UserMemory "
                "(user_id, memory_type, memory_key, memory_value, usage_count, created_at, last_used) "
                "VALUES (?, 'insight', ?, ?, 1, GETUTCDATE(), GETUTCDATE())",
                [user_id, topic[:200], memory_value],
            )

    except Exception as e:
        logger.error(f"[session_insight] DB save failed: {e}")
        # Fall back to in-memory via user_memory
        try:
            from command_center.memory.user_memory import update_preference
            update_preference(user_id, f"insight:{topic}", json.loads(memory_value))
        except Exception:
            pass


def get_insights_for_context(user_id: int, limit: int = 10) -> str:
    """Load stored insights and format them for injection into conversation context.

    Returns a formatted string suitable for the user_memory field, or empty string
    if no insights exist.  Insights are ordered by recency (most recent first).
    """
    try:
        insights = _load_insights(user_id, limit=limit)
        if not insights:
            return ""

        lines = ["Your discovered knowledge (from previous conversations):"]
        for topic, data in insights:
            insight_text = data.get("insight", "")
            entities = data.get("entities", [])
            tool_hint = data.get("tool_hint", "")

            line = f"- **{topic}**: {insight_text}"
            if tool_hint:
                line += f" (approach: {tool_hint})"
            lines.append(line)

        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"[session_insight] get_insights_for_context failed: {e}")
        return ""


def _load_insights(user_id: int, limit: int = 10) -> List[tuple]:
    """Load raw insights from DB or in-memory store.

    Returns list of (topic, data_dict) tuples.
    """
    if not _use_db:
        # In-memory: pull from user_memory preference store
        try:
            from command_center.memory.user_memory import get_preferences
            prefs = get_preferences(user_id)
            insights = []
            for key, val in prefs.items():
                if key.startswith("insight:"):
                    topic = key[8:]  # Remove "insight:" prefix
                    insights.append((topic, val if isinstance(val, dict) else {"insight": str(val)}))
            # Sort by extracted_at (most recent first)
            insights.sort(
                key=lambda x: x[1].get("extracted_at", ""),
                reverse=True,
            )
            return insights[:limit]
        except Exception:
            return []

    try:
        from command_center.memory.user_memory import _cc_memory_db_execute

        rows = _cc_memory_db_execute(
            "SELECT TOP (?) memory_key, memory_value FROM cc_UserMemory "
            "WHERE user_id = ? AND memory_type = 'insight' "
            "ORDER BY last_used DESC",
            [limit, user_id],
            fetch=True,
        )

        if not rows:
            return []

        insights = []
        for topic, val_json in rows:
            try:
                data = json.loads(val_json) if isinstance(val_json, str) else (val_json or {})
            except Exception:
                data = {"insight": str(val_json)}
            insights.append((topic, data))

        return insights

    except Exception as e:
        logger.warning(f"[session_insight] _load_insights failed: {e}")
        return []


def delete_all_insights(user_id: int) -> int:
    """Delete all insight entries for a user. Returns count deleted."""
    if not _use_db:
        try:
            from command_center.memory.user_memory import get_preferences, delete_preference
            prefs = get_preferences(user_id)
            count = 0
            for key in list(prefs.keys()):
                if key.startswith("insight:"):
                    delete_preference(user_id, key)
                    count += 1
            return count
        except Exception:
            return 0

    try:
        from command_center.memory.user_memory import _cc_memory_db_execute
        count_rows = _cc_memory_db_execute(
            "SELECT COUNT(*) FROM cc_UserMemory WHERE user_id = ? AND memory_type = 'insight'",
            [user_id],
            fetch=True,
        )
        count = count_rows[0][0] if count_rows else 0
        _cc_memory_db_execute(
            "DELETE FROM cc_UserMemory WHERE user_id = ? AND memory_type = 'insight'",
            [user_id],
        )
        return count
    except Exception as e:
        logger.error(f"[session_insight] delete_all_insights failed: {e}")
        return 0
