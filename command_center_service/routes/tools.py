"""
Command Center — Tools Routes
================================
Generated tool management and audit API.
"""

import logging
from fastapi import APIRouter

from command_center.tools.tool_factory import list_generated_tools, get_generated_tool
from command_center.tools.tool_audit import get_audit_log, get_tool_stats, log_tool_status_change

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("/generated")
async def list_tools():
    """List all auto-generated tools."""
    return list_generated_tools()


@router.get("/generated/{tool_name}")
async def get_tool(tool_name: str):
    """Get a specific generated tool's config and code."""
    tool = get_generated_tool(tool_name)
    if tool:
        return tool
    return {"error": "Tool not found"}, 404


@router.post("/generated/{tool_name}/disable")
async def disable_tool(tool_name: str):
    """Disable a generated tool."""
    log_tool_status_change(tool_name, "disabled")
    return {"status": "disabled", "tool_name": tool_name}


@router.get("/audit")
async def audit_log():
    """Get the tool audit log."""
    return {
        "stats": get_tool_stats(),
        "log": get_audit_log(),
    }
