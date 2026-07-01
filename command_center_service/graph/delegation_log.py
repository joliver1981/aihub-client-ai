"""
Command Center — Delegation Conversation Log Store
=====================================================
Accumulates the *side conversations* CC holds with delegated DATA / GENERAL
agents on the user's behalf, so the classic UI can display them as threads in
the unified "Agent Activity" panel (alongside the Builder conversation).

Design: kept OUTSIDE LangGraph state (module-level, exactly like
`graph.progress`) to avoid threading a growing structure through every return
point of the intricate `gather_data` node. Keyed by CC `session_id`; each
session holds an ordered map of `agent_id -> thread`. Threads persist for the
process lifetime of the session (consistent with the data/general agents being
themselves stateless / process-lifetime).

The Builder agent has its own richer log (`active_delegation.builder_log`) that
is emitted separately; this store is only for data/general agents. The frontend
renders both in one panel.

Usage in a graph node (record a turn):
    from graph.delegation_log import record_turn
    record_turn(session_id, agent_id="391", agent_name="Retail Data Agent",
                agent_type="data", question="sales by state last year",
                answer="...the agent's reply...")

Usage in a graph node (forward a CLEAN per-agent history instead of CC chatter):
    from graph.delegation_log import get_thread_history
    history = get_thread_history(session_id, agent_id)  # [{role, content}, ...]

Usage in chat.py (after the graph run, emit to the browser):
    from graph.delegation_log import get_threads
    threads = get_threads(session_id)
    if threads:
        yield _sse_event("delegation_logs", {"threads": threads})
"""

import logging
import time
from collections import OrderedDict
from datetime import datetime
from typing import Dict, List

logger = logging.getLogger(__name__)

# session_id -> {"updated_at": float, "threads": OrderedDict[agent_id -> thread]}
# thread = {"agent_id", "agent_name", "agent_type", "turns": [{role, content, ts}]}
_store: Dict[str, dict] = {}

# Bounds to prevent unbounded growth
_MAX_TURNS_PER_THREAD = 40      # keep the most recent N turn entries per agent
_MAX_THREADS_PER_SESSION = 20   # keep the most recently active N agents
_SESSION_TTL = 60 * 60          # drop a session's logs after 1h idle


def _prune_stale():
    now = time.time()
    stale = [sid for sid, s in _store.items() if (now - s.get("updated_at", now)) > _SESSION_TTL]
    for sid in stale:
        _store.pop(sid, None)


def record_turn(session_id, agent_id, agent_name, agent_type, question, answer):
    """Append a question/answer pair to the given agent's thread for this session.

    Fail-safe: never raises — recording a UI thread must not break a delegation.
    """
    try:
        if not session_id or agent_id is None:
            return
        _prune_stale()
        sid = str(session_id)
        aid = str(agent_id)
        sess = _store.get(sid)
        if sess is None:
            sess = {"updated_at": time.time(), "threads": OrderedDict()}
            _store[sid] = sess
        threads = sess["threads"]
        thread = threads.get(aid)
        if thread is None:
            thread = {
                "agent_id": aid,
                "agent_name": agent_name or f"Agent #{aid}",
                "agent_type": agent_type or "data",
                "turns": [],
            }
            threads[aid] = thread
        else:
            # Refresh display metadata if a better name/type arrived later.
            if agent_name:
                thread["agent_name"] = agent_name
            if agent_type:
                thread["agent_type"] = agent_type

        ts = datetime.now().isoformat()
        if question is not None and str(question).strip():
            thread["turns"].append({"role": "user", "content": str(question), "ts": ts})
        if answer is not None and str(answer).strip():
            thread["turns"].append({"role": "assistant", "content": str(answer), "ts": ts})

        # Trim per-thread turns
        if len(thread["turns"]) > _MAX_TURNS_PER_THREAD:
            thread["turns"] = thread["turns"][-_MAX_TURNS_PER_THREAD:]

        # Most-recently-active agent goes last; cap the number of threads.
        threads.move_to_end(aid)
        while len(threads) > _MAX_THREADS_PER_SESSION:
            threads.popitem(last=False)

        sess["updated_at"] = time.time()
    except Exception as e:  # pragma: no cover — defensive
        logger.debug(f"[delegation_log] record_turn failed: {e}")


def get_threads(session_id) -> List[dict]:
    """Return the agent threads for this session, least- to most-recently-active.

    Returns shallow copies so callers (e.g. secret-masking in chat.py) can mutate
    turn content without corrupting the store.
    """
    sess = _store.get(str(session_id))
    if not sess:
        return []
    out = []
    for t in sess["threads"].values():
        out.append({
            "agent_id": t["agent_id"],
            "agent_name": t["agent_name"],
            "agent_type": t["agent_type"],
            "turns": [dict(turn) for turn in t["turns"]],
        })
    return out


def get_thread_history(session_id, agent_id) -> List[dict]:
    """Return prior turns for a specific agent as [{role, content}].

    Used to forward a CLEAN per-agent history to a data/general agent instead of
    CC's own orchestration conversation (which would leak routing meta-text into
    the agent's NLQ classifiers).
    """
    sess = _store.get(str(session_id))
    if not sess:
        return []
    thread = sess["threads"].get(str(agent_id))
    if not thread:
        return []
    return [{"role": t["role"], "content": t["content"]} for t in thread["turns"]]


def clear(session_id):
    """Drop all threads for a session (e.g. when the conversation is reset)."""
    _store.pop(str(session_id), None)
