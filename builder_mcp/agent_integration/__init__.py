"""
MCP Agent Integration
Provides MCP server tools to AI Hub agents.
"""
from .mcp_agent_tools import get_mcp_tools_for_agent, get_mcp_system_prompt_addition

__all__ = ['get_mcp_tools_for_agent', 'get_mcp_system_prompt_addition']
