"""
MCP Client Adapter
==================
Lightweight adapter that communicates with the MCP Gateway microservice.
Works with existing database schema.
No FastMCP dependencies - just requests!
"""
import sys
import requests
import json
from typing import Dict, List, Any, Optional
import logging
from logging.handlers import WatchedFileHandler
import os

import asyncio
import aiohttp
from dataclasses import dataclass
import uuid


# Get the parent directory (the project root)
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(parent_dir)

from CommonUtils import rotate_logs_on_startup, get_mcp_gateway_api_base_url, get_log_path

rotate_logs_on_startup(os.getenv('MCP_ADAPTER_LOG', get_log_path('mcp_adapter_log.txt')))

# Configure logging
logger = logging.getLogger("MCPAdapterLog")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=os.getenv('MCP_ADAPTER_LOG', get_log_path('mcp_adapter_log.txt')), encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)


class MCPGatewayClient:
    """Client for communicating with the MCP Gateway microservice."""
    
    def __init__(self, gateway_url: str = None):
        self.gateway_url = gateway_url or os.getenv('MCP_GATEWAY_URL', get_mcp_gateway_api_base_url())
        self.session = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})
        
    def test_server(self, connection_config: Dict) -> Dict:
        """Test if an MCP server configuration works."""
        try:
            response = self.session.post(
                f"{self.gateway_url}/api/mcp/servers/test",
                json=connection_config,
                timeout=30
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error testing MCP server: {e}")
            return {"status": "failed", "error": str(e)}
    
    def connect_server(self, server_id: int, connection_config: Dict) -> Dict:
        """Connect to an MCP server via the gateway."""
        try:
            response = self.session.post(
                f"{self.gateway_url}/api/mcp/servers/{server_id}/connect",
                json=connection_config,
                timeout=30
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error connecting to MCP server: {e}")
            return {"status": "failed", "error": str(e)}
    
    def get_tools(self, server_id: int) -> List[Dict]:
        """Get available tools from a connected server."""
        try:
            response = self.session.get(
                f"{self.gateway_url}/api/mcp/servers/{server_id}/tools",
                timeout=10
            )
            response.raise_for_status()
            return response.json()["tools"]
        except Exception as e:
            logger.error(f"Error getting tools from server {server_id}: {e}")
            return []
    
    def call_tool(self, server_id: int, tool_name: str, arguments: Dict) -> str:
        """Execute a tool on an MCP server."""
        try:
            response = self.session.post(
                f"{self.gateway_url}/api/mcp/tools/call",
                json={
                    "server_id": server_id,
                    "tool_name": tool_name,
                    "arguments": arguments
                },
                timeout=60
            )
            response.raise_for_status()
            result = response.json()
            
            if result["status"] == "success":
                return result["result"]
            else:
                return f"Error: {result.get('error', 'Unknown error')}"
        except Exception as e:
            logger.error(f"Error calling tool {tool_name}: {e}")
            return f"Error executing MCP tool: {str(e)}"
    
    def health_check(self) -> bool:
        """Check if the MCP Gateway is accessible."""
        try:
            response = self.session.get(f"{self.gateway_url}/api/mcp/health", timeout=5)
            return response.status_code == 200
        except:
            return False


class MCPToolLoader:
    """Loads MCP tools and converts them to LangChain tools."""
    
    def __init__(self, gateway_client: MCPGatewayClient):
        self.gateway = gateway_client
        
    def load_tools_for_agent(self, agent_id: int) -> List:
        """Load all MCP tools assigned to an agent."""
        from langchain.tools import tool
        from AppUtils import get_db_connection
        
        # Get MCP servers assigned to this agent
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        cursor.execute("""
            SELECT ms.server_id, ms.server_name, ms.connection_config
            FROM MCPServers ms
            INNER JOIN AgentMCPServers ams ON ms.server_id = ams.server_id
            WHERE ams.agent_id = ? AND ms.enabled = 1 AND ams.enabled = 1
        """, agent_id)
        
        langchain_tools = []
        
        for row in cursor.fetchall():
            server_id, server_name, connection_config_json = row
            
            try:
                connection_config = json.loads(connection_config_json)
                
                # Connect to server via gateway
                connect_result = self.gateway.connect_server(server_id, connection_config)
                
                if connect_result['status'] != 'connected':
                    logger.error(f"Failed to connect to MCP server {server_name}")
                    continue
                
                # Get tools from this server
                tools = connect_result.get('tools', [])
                
                # Convert each MCP tool to a LangChain tool
                for tool_schema in tools:
                    langchain_tool = self._create_langchain_tool(
                        server_id=server_id,
                        tool_schema=tool_schema
                    )
                    langchain_tools.append(langchain_tool)
                    
            except Exception as e:
                logger.error(f"Error loading MCP server {server_name}: {e}")
                continue
        
        cursor.close()
        conn.close()
        
        return langchain_tools
    
    def _create_langchain_tool(self, server_id: int, tool_schema: Dict):
        """Create a LangChain tool that proxies to the MCP Gateway."""
        from langchain.tools import tool
        
        tool_name = f"mcp_{tool_schema['name']}"
        tool_description = tool_schema.get('description', 'MCP tool')
        
        # Create closure to capture server_id and tool name
        gateway = self.gateway
        original_tool_name = tool_schema['name']
        
        @tool(name=tool_name, description=tool_description)
        def mcp_tool_wrapper(**kwargs) -> str:
            """Execute MCP tool via gateway"""
            return gateway.call_tool(
                server_id=server_id,
                tool_name=original_tool_name,
                arguments=kwargs
            )
        
        return mcp_tool_wrapper
    
