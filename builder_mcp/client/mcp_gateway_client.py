"""
MCP Gateway Client
Client module for communicating with the MCP Gateway service.
Used by the main application to interact with MCP servers.

Dependencies: requests only (no MCP SDK needed)
"""
import os
import requests
import logging
from typing import Dict, List, Optional
from urllib.parse import urljoin
from CommonUtils import get_mcp_gateway_api_base_url

logger = logging.getLogger(__name__)


class MCPGatewayClient:
    """Client for the MCP Gateway microservice"""

    def __init__(self, base_url: str = None, timeout: int = 30, max_retries: int = 3):
        """
        Initialize the MCP Gateway client.

        Args:
            base_url: Base URL of the MCP Gateway service
            timeout: Default request timeout in seconds
            max_retries: Maximum number of retry attempts
        """
        self.base_url = base_url or os.getenv('MCP_GATEWAY_URL', get_mcp_gateway_api_base_url())
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()

        # Set up retry logic
        from requests.adapters import HTTPAdapter
        from requests.packages.urllib3.util.retry import Retry

        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict:
        """
        Make a request to the MCP Gateway.

        Args:
            method: HTTP method
            endpoint: API endpoint path
            **kwargs: Additional request parameters

        Returns:
            Response data as dictionary

        Raises:
            Exception: If request fails after retries
        """
        url = urljoin(self.base_url + '/', endpoint.lstrip('/'))

        # Set default timeout if not provided
        if 'timeout' not in kwargs:
            kwargs['timeout'] = self.timeout

        # Force connection close
        if 'headers' not in kwargs:
            kwargs['headers'] = {}
        kwargs['headers']['Connection'] = 'close'

        try:
            # Don't use session - create fresh connection each time (matching existing pattern)
            response = requests.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            logger.error(f"MCP Gateway timeout: {url}")
            raise Exception(f"MCP Gateway request timed out after {kwargs.get('timeout', self.timeout)}s")
        except requests.exceptions.ConnectionError:
            logger.error(f"MCP Gateway connection error: {url}")
            raise Exception(f"Could not connect to MCP Gateway at {self.base_url}")
        except requests.exceptions.RequestException as e:
            logger.error(f"MCP Gateway request failed: {e}")
            raise Exception(f"MCP Gateway request failed: {str(e)}")

    def health_check(self) -> bool:
        """Check if the MCP Gateway service is accessible"""
        try:
            r = self._make_request('GET', '/health')
            return r.get('status') == 'ok'
        except Exception:
            return False

    def test_server(self, config: dict) -> dict:
        """
        Test a server configuration without persisting.

        Args:
            config: Server configuration dict with type, command/url, etc.

        Returns:
            {status, tool_count, tools} or {status, error}
        """
        return self._make_request('POST', '/api/mcp/test', json=config)

    def connect_server(self, server_id: int, config: dict) -> dict:
        """
        Connect to an MCP server via the gateway.

        Args:
            server_id: Server ID from database
            config: Connection configuration

        Returns:
            {status, tool_count, tools}
        """
        payload = {'server_id': str(server_id)}
        payload.update(config)
        return self._make_request('POST', '/api/mcp/connect', json=payload)

    def disconnect_server(self, server_id: int) -> dict:
        """Disconnect from a server"""
        return self._make_request('POST', '/api/mcp/disconnect',
                                  json={'server_id': str(server_id)})

    def list_tools(self, server_id: int) -> list:
        """
        Get available tools from a connected server.

        Returns:
            List of tool definitions [{name, description, inputSchema}]
        """
        r = self._make_request('GET', f'/api/mcp/servers/{server_id}/tools')
        return r.get('tools', [])

    def call_tool(self, server_id: int, tool_name: str, arguments: dict) -> dict:
        """
        Execute a tool on a connected server.

        Args:
            server_id: The connected server ID
            tool_name: Name of the tool to call
            arguments: Tool arguments dict

        Returns:
            {status, result} or {status, error}
        """
        return self._make_request(
            'POST',
            f'/api/mcp/servers/{server_id}/tools/call',
            json={'tool_name': tool_name, 'arguments': arguments},
            timeout=60  # tool calls may take longer
        )

    def get_server_status(self, server_id: int) -> dict:
        """Get connection status for a server"""
        return self._make_request('GET', f'/api/mcp/servers/{server_id}/status')

    def get_all_connections(self) -> dict:
        """Get all active gateway connections"""
        return self._make_request('GET', '/api/mcp/connections')
