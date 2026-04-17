"""
Command Center — Landscape Scanner
=====================================
Scans the AI Hub platform for all available agents, tools,
workflows, connections, MCP servers, and knowledge bases.
Uses REAL API endpoints from the main Flask app.
"""

import logging
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Simple TTL cache
_cache: Dict[str, Any] = {}
_cache_time: float = 0
_CACHE_TTL = 60  # seconds


async def scan_platform() -> Dict[str, Any]:
    """
    Query the main app's REAL API endpoints to discover all available
    agents (with objectives/descriptions), connections, MCP servers, etc.
    Results are cached for 60 seconds.
    """
    global _cache, _cache_time

    if _cache and (time.time() - _cache_time) < _CACHE_TTL:
        return _cache

    import httpx

    try:
        from cc_config import get_base_url, AI_HUB_API_KEY
    except ImportError:
        logger.warning("Could not import cc_config, using defaults")
        return _empty_landscape()

    base_url = get_base_url()
    headers = {
        "X-API-Key": AI_HUB_API_KEY,
        "Content-Type": "application/json",
        "Connection": "close",
    }

    landscape = {
        "agents": [],
        "data_agents": [],
        "all_agents": [],
        "tools": [],
        "workflows": [],
        "connections": [],
        "mcp_servers": [],
        "knowledge_bases": [],
        "scanned_at": time.time(),
    }

    async with httpx.AsyncClient(timeout=15.0) as client:

        # ── Step 1: Get agent names + objectives from /api/agents/list ───
        # This returns enabled agents with descriptions/objectives
        agent_details = {}  # agent_id -> {name, description, ...}
        try:
            resp = await client.get(f"{base_url}/api/agents/list", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                agents_list = data.get("agents", []) if isinstance(data, dict) else data
                for a in agents_list:
                    aid = a.get("agent_id")
                    if aid:
                        agent_details[aid] = {
                            "agent_id": aid,
                            "agent_name": a.get("agent_name", "Unknown"),
                            "description": a.get("agent_description", ""),
                        }
                logger.info(f"Fetched {len(agent_details)} agent details from /api/agents/list")
        except Exception as e:
            logger.warning(f"Failed to fetch agent list: {e}")

        # ── Step 2: Get agent metadata (enabled, is_data_agent) from /api/agents/summary ──
        try:
            resp = await client.get(f"{base_url}/api/agents/summary", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                all_agents_raw = data.get("agents", data) if isinstance(data, dict) else data
                if isinstance(all_agents_raw, list):
                    for a in all_agents_raw:
                        aid = a.get("agent_id")
                        is_data = a.get("is_data_agent", False)
                        enabled = a.get("enabled", True)

                        # Merge with details from step 1
                        detail = agent_details.get(aid, {})
                        agent_info = {
                            "agent_id": aid,
                            "agent_name": detail.get("agent_name") or a.get("agent_name", "Unknown"),
                            "description": detail.get("description", ""),
                            "enabled": enabled,
                            "is_data_agent": is_data,
                        }

                        landscape["all_agents"].append(agent_info)
                        if is_data:
                            landscape["data_agents"].append(agent_info)
                        else:
                            landscape["agents"].append(agent_info)

                logger.info(
                    f"Found {len(landscape['agents'])} general agents, "
                    f"{len(landscape['data_agents'])} data agents"
                )
        except Exception as e:
            logger.warning(f"Failed to fetch agents/summary: {e}")
            # Fallback: use agent_details if summary failed
            if agent_details:
                for aid, detail in agent_details.items():
                    agent_info = {
                        "agent_id": aid,
                        "agent_name": detail.get("agent_name", "Unknown"),
                        "description": detail.get("description", ""),
                        "enabled": True,
                        "is_data_agent": False,
                    }
                    landscape["agents"].append(agent_info)
                    landscape["all_agents"].append(agent_info)

        # ── Step 3: Fetch connections (data sources) ─────────────────────
        try:
            resp = await client.get(f"{base_url}/api/connections", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                conns = data.get("connections", data) if isinstance(data, dict) else data
                if isinstance(conns, list):
                    landscape["connections"] = conns
                    logger.info(f"Found {len(conns)} connections")
            else:
                logger.warning(f"Failed to fetch connections: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"Failed to fetch connections: {e}")

        # ── Step 4: Fetch MCP servers ────────────────────────────────────
        try:
            resp = await client.get(f"{base_url}/api/mcp/servers", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                servers = data.get("servers", data) if isinstance(data, dict) else data
                if isinstance(servers, list):
                    landscape["mcp_servers"] = servers
                    logger.info(f"Found {len(servers)} MCP servers")
            else:
                logger.warning(f"Failed to fetch MCP servers: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"Failed to fetch MCP servers: {e}")

    _cache = landscape
    _cache_time = time.time()

    total = (
        len(landscape["agents"]) + len(landscape["data_agents"])
        + len(landscape["connections"]) + len(landscape["mcp_servers"])
    )
    logger.info(f"Landscape scan complete: {total} total resources discovered")

    return landscape


def format_landscape_summary(landscape: Dict[str, Any], max_agents: int = 0) -> str:
    """Format the landscape into a human-readable summary for LLM prompts.
    
    Includes objectives/descriptions so the LLM can intelligently route.
    If max_agents > 0, limits general agents shown (data agents always shown in full).
    """
    parts = []

    if landscape.get("agents"):
        enabled = [a for a in landscape["agents"] if a.get("enabled")]
        disabled = [a for a in landscape["agents"] if not a.get("enabled")]
        
        if enabled:
            show = enabled if max_agents <= 0 else enabled[:max_agents]
            parts.append(f"**General Agents (Assistants) — {len(enabled)} ENABLED:**")
            for a in show:
                name = a.get("agent_name", "Unknown")
                desc = a.get("description", "")
                aid = a.get("agent_id", "?")
                line = f"- [{aid}] **{name}**"
                if desc:
                    desc_short = desc[:120] + "..." if len(desc) > 120 else desc
                    line += f" — {desc_short}"
                parts.append(line)
            if max_agents > 0 and len(enabled) > max_agents:
                parts.append(f"  ... and {len(enabled) - max_agents} more general agents")
        
        if disabled:
            parts.append(f"({len(disabled)} disabled general agents not shown)")

    if landscape.get("data_agents"):
        enabled = [a for a in landscape["data_agents"] if a.get("enabled")]
        disabled = [a for a in landscape["data_agents"] if not a.get("enabled")]
        
        if enabled:
            parts.append(f"\n**Data Agents (query databases/data sources) — {len(enabled)} ENABLED:**")
            for a in enabled:
                name = a.get("agent_name", "Unknown")
                desc = a.get("description", "")
                aid = a.get("agent_id", "?")
                line = f"- [{aid}] **{name}**"
                if desc:
                    desc_short = desc[:120] + "..." if len(desc) > 120 else desc
                    line += f" — {desc_short}"
                parts.append(line)
        
        if disabled:
            parts.append(f"({len(disabled)} disabled data agents not shown)")

    if landscape.get("connections"):
        parts.append("\n**Data Connections (databases/APIs):**")
        for c in landscape["connections"]:
            name = c.get("name") or c.get("connection_name") or c.get("description", "Unknown")
            ctype = c.get("type") or c.get("connection_type", "")
            host = c.get("host") or c.get("server", "")
            db = c.get("database", "")
            line = f"- **{name}** ({ctype})"
            if host:
                line += f" — {host}"
            if db:
                line += f"/{db}"
            parts.append(line)

    if landscape.get("mcp_servers"):
        parts.append("\n**MCP Servers (external tools):**")
        for s in landscape["mcp_servers"]:
            name = s.get("name") or s.get("server_name", "Unknown")
            stype = s.get("type", "")
            parts.append(f"- **{name}** ({stype})" if stype else f"- **{name}**")

    if not parts:
        return "No agents or resources discovered. The platform may need configuration."

    return "\n".join(parts)


def find_agents_for_query(landscape: Dict[str, Any], query_type: str = "data") -> list:
    """Find agents that could handle a specific query type.
    
    Args:
        landscape: The scanned landscape
        query_type: "data" for data queries, "general" for general tasks
    
    Returns:
        List of matching enabled agents
    """
    if query_type == "data":
        return [a for a in landscape.get("data_agents", []) if a.get("enabled")]
    else:
        return [a for a in landscape.get("agents", []) if a.get("enabled")]


def invalidate_cache():
    """Force a fresh scan on next call."""
    global _cache, _cache_time
    _cache = {}
    _cache_time = 0


def _empty_landscape() -> Dict[str, Any]:
    return {
        "agents": [],
        "data_agents": [],
        "all_agents": [],
        "tools": [],
        "workflows": [],
        "connections": [],
        "mcp_servers": [],
        "knowledge_bases": [],
        "scanned_at": 0,
    }
