"""
Command Center — Universal Data Client
=========================================
Single facade for accessing data from any source:
databases, documents, web, MCP tools, internal services.
"""

import logging
from typing import Any, Dict, List, Optional

from command_center.data_access.service_client import ServiceClient

logger = logging.getLogger(__name__)


class UniversalDataClient:
    """Unified interface for all data sources."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._clients: Dict[str, ServiceClient] = {}

    def _get_client(self, service: str) -> ServiceClient:
        """Get or create a ServiceClient for a given service."""
        if service not in self._clients:
            from cc_config import get_service_url
            url = get_service_url(service)
            self._clients[service] = ServiceClient(url, self.api_key)
        return self._clients[service]

    async def query_agent(self, agent_id: str, question: str, user_id: Optional[int] = None) -> Dict[str, Any]:
        """Delegate a question to an existing agent."""
        from command_center.orchestration.delegator import delegate_to_agent
        user_ctx = {"user_id": user_id} if user_id else None
        return await delegate_to_agent(agent_id, question, user_context=user_ctx)

    async def search_documents(self, query: str, limit: int = 10) -> Dict[str, Any]:
        """Search documents via the document/vector service."""
        client = self._get_client("vector")
        return await client.post("/api/search", {"query": query, "limit": limit})

    async def call_mcp_tool(self, server_id: int, tool_name: str, arguments: Dict) -> Dict[str, Any]:
        """Call an MCP tool via the gateway."""
        from command_center.orchestration.delegator import delegate_to_mcp_tool
        return await delegate_to_mcp_tool(server_id, tool_name, arguments)

    async def call_service_api(
        self, service: str, endpoint: str, method: str = "GET", payload: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Call any internal service API."""
        client = self._get_client(service)
        if method.upper() == "POST":
            return await client.post(endpoint, payload)
        return await client.get(endpoint)

    async def list_agents(self) -> List[Dict]:
        """Get all available agents."""
        client = self._get_client("main")
        result = await client.get("/api/agents/list")
        return result if isinstance(result, list) else result.get("agents", [])

    async def list_connections(self) -> List[Dict]:
        """Get all available data connections."""
        client = self._get_client("main")
        result = await client.get("/api/connections/list")
        return result if isinstance(result, list) else result.get("connections", [])
