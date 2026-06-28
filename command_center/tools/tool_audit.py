"""
Command Center — Tool Audit
================================
Audit log for tool creation, usage, and lifecycle.
In-memory for now; will use cc_ToolAudit table when DB is available.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# In-memory audit log (replace with DB-backed store later).
_audit_log: List[Dict[str, Any]] = []


def log_tool_creation(
    tool_name: str,
    creation_method: str = "auto",
    created_by: Optional[int] = None,
    config_json: Optional[str] = None,
    code_hash: Optional[str] = None,
):
    """Record a tool creation event."""
    entry = {
        "tool_name": tool_name,
        "event": "created",
        "creation_method": creation_method,
        "created_by": created_by,
        "config_json": config_json,
        "code_hash": code_hash,
        "usage_count": 0,
        "status": "active",
        "created_at": datetime.utcnow().isoformat(),
    }
    _audit_log.append(entry)
    logger.info(f"Tool audit: created {tool_name} (method={creation_method})")


def log_tool_usage(tool_name: str):
    """Record a tool usage event."""
    for entry in reversed(_audit_log):
        if entry["tool_name"] == tool_name and entry["event"] == "created":
            entry["usage_count"] = entry.get("usage_count", 0) + 1
            entry["last_used"] = datetime.utcnow().isoformat()
            return


def log_tool_status_change(tool_name: str, new_status: str):
    """Record a tool status change (disable, delete, etc.)."""
    entry = {
        "tool_name": tool_name,
        "event": "status_change",
        "status": new_status,
        "timestamp": datetime.utcnow().isoformat(),
    }
    _audit_log.append(entry)
    logger.info(f"Tool audit: {tool_name} status -> {new_status}")


def get_audit_log(limit: int = 50) -> List[Dict[str, Any]]:
    """Get the audit log, newest first."""
    return list(reversed(_audit_log[-limit:]))


def get_tool_stats() -> Dict[str, Any]:
    """Get summary statistics about generated tools."""
    created = [e for e in _audit_log if e["event"] == "created"]
    active = [e for e in created if e.get("status") == "active"]
    total_usage = sum(e.get("usage_count", 0) for e in created)
    return {
        "total_created": len(created),
        "active": len(active),
        "total_usage": total_usage,
    }
