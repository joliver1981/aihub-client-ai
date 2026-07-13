# builder_session_store.py
# Durable store for in-progress AI Workflow Builder sessions (P2 hardening).
#
# The WorkflowAgent's build conversation used to live only in a per-process
# in-memory dict (workflow_builder_routes.builder_sessions), so a process
# restart — or a follow-up request routed to a different worker — silently lost
# the whole build (requirements, plan, phase) and re-planned the user's next
# answer as a fresh goal. This module persists the agent's serialized state to
# disk (atomic write) keyed by session id, so builds survive restarts and are
# shared across workers on the same host.
#
# Disk-backed (not DB) to avoid schema/DDL coupling; the sessions dir sits under
# APP_ROOT and is shared by all workers of a single host deployment. Gated by
# WORKFLOW_DURABLE_SESSIONS (default True).

import json
import logging
import os
import tempfile

import config as cfg

logger = logging.getLogger("builder_session_store")

_SUBDIR = "workflow_builder_sessions"


def _enabled() -> bool:
    return bool(getattr(cfg, "WORKFLOW_DURABLE_SESSIONS", True))


def _dir() -> str:
    base = os.getenv("APP_ROOT") or os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(base, _SUBDIR)
    os.makedirs(d, exist_ok=True)
    return d


def _safe_name(session_id) -> str:
    return "".join(c for c in str(session_id) if c.isalnum() or c in "-_.") or "unnamed"


def _path(session_id) -> str:
    return os.path.join(_dir(), f"{_safe_name(session_id)}.json")


def save_session(session_id, state: dict) -> bool:
    """Persist a serialized build state atomically. Never raises."""
    if not _enabled() or session_id is None:
        return False
    try:
        path = _path(session_id)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, default=str)
            os.replace(tmp, path)  # atomic on same filesystem
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        return True
    except Exception as e:  # pragma: no cover - best-effort persistence
        logger.warning(f"save_session failed for {session_id}: {e}")
        return False


def load_session(session_id):
    """Return the serialized build state, or None if absent/unreadable. Never raises."""
    if not _enabled() or session_id is None:
        return None
    try:
        path = _path(session_id)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"load_session failed for {session_id}: {e}")
        return None


def delete_session(session_id) -> bool:
    """Remove a persisted session (e.g. on /clear). Never raises."""
    try:
        path = _path(session_id)
        if os.path.exists(path):
            os.remove(path)
        return True
    except Exception as e:  # pragma: no cover
        logger.debug(f"delete_session failed for {session_id}: {e}")
        return False
