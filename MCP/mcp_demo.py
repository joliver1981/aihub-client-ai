#!/usr/bin/env python3
"""
MCP Integration Demo Script
============================
Simple demonstration of how MCP components work together in your AI Hub platform.

This script shows the complete flow from:
1. Starting the gateway service
2. Connecting to an MCP server
3. Using tools from the server
4. Integration with agents
"""

import os
import sys
import json
import time
import requests
import subprocess
from typing import Dict, List, Any

# Add parent directory to path if running standalone
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


def print_section(title: str):
    """Print a formatted section header"""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def step(number: int, description: str):
    """Print a step indicator"""
    print(f"\n{number}️⃣  {description}")


class MCPIntegrationDemo:
    """Demonstrates MCP integration workflow"""
    
    def __init__(self):
        self.gateway_url = "http://localhost:5555"
        self.test_server_id = 999
        
    def check_gateway(self) -> bool:
        """Check if gateway service is running"""
        try:
            response = requests.get(f"{self.gateway_url}/api/mcp/health", timeout=2)
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Gateway is running: {data}")
                return True
        except:
            pass
        
        print("❌ Gateway service is not running")
        print("   Please start it with: python mcp_gateway_service.py")
        return False
    
    def demo_adapter_workflow(self):
        """Demonstrate using the MCP adapter (recommended approach)"""
        print_section("ADAPTER WORKFLOW (via Gateway)")
        
        from mcp_adapter import MCPGatewayClient
        
        # Initialize client
        step(1, "Initialize MCP Gateway Client")
        client = MCPGatewayClient(self.gateway_url)
        print(f"   Client initialized: {client.gateway_url}")
        
        # Test server configuration
        step(2, "Test MCP Server Configuration")
        config = {
            "command": "python",
            "args": ["aihub_mcp_server.py"],
            "env_vars": {}
        }
        
        test_result = client.test_server(config)
        print(f"   Test result: {test_result['status']}")
        if test_result["status"] == "success":
            print(f"   Found {test_result.get('tool_count', 0)} tools")
        
        # Connect to server
        step(3, "Connect to MCP Server")
        connect_result = client.connect_server(self.test_server_id, config)
        print(f"   Connection status: {connect_result['status']}")
        
        if connect_result["status"] == "connected":
            tools = connect_result.get("tools", [])
            print(f"   Available tools: {len(tools)}")
            
            # Show first few tools
            for tool in tools[:3]:
                print(f"     - {tool['name']}: {tool['description'][:50]}...")
        
        # Execute some tools
        step(4, "Execute MCP Tools")
        
        # Tool 1: Echo
        print("\n   🔧 Testing echo tool:")
        result = client.call_tool(
            self.test_server_id,
            "echo",
            {"message": "Hello from AI Hub!"}
        )
        print(f"      Result: {result}")
        
        # Tool 2: Add numbers
        print("\n   🔧 Testing add tool:")
        result = client.call_tool(
            self.test_server_id,
            "add",
            {"a": 42, "b": 58}
        )
        print(f"      Result: {result}")
        
        # Tool 3: Query sample data
        print("\n   🔧 Testing data query:")
        result = client.call_tool(
            self.test_server_id,
            "query_sample_data",
            {"table": "agents"}
        )
        data = json.loads(result) if result else {}
        print(f"      Found {data.get('count', 0)} agents")
        
        # Tool 4: Generate test data
        print("\n   🔧 Testing data generation:")
        result = client.call_tool(
            self.test_server_id,
            "generate_test_data",
            {"count": 3}
        )
        if result:
            test_data = json.loads(result)
            print(f"      Generated {len(test_data)} records")
    
    def demo_direct_client_workflow(self):
        """Demonstrate using the direct MCP client"""
        print_section("DIRECT CLIENT WORKFLOW")
        
        from mcp_user_client import SimpleMCPClient
        
        # Initialize client
        step(1, "Initialize Direct MCP Client")
        client = SimpleMCPClient(
            command="python",
            args=["aihub_mcp_server.py"],
            env_vars={}
        )
        
        # Start server
        step(2, "Start MCP Server Process")
        if client.start():
            print("   ✅ Server started successfully")
        else:
            print("   ❌ Failed to start server")
            return
        
        # List tools
        step(3, "List Available Tools")
        tools = client.list_tools()
        print(f"   Found {len(tools)} tools")
        
        for tool in tools[:3]:
            print(f"     - {tool['name']}: {tool.get('description', 'No description')[:50]}...")
        
        # Call tools
        step(4, "Execute Tools Directly")
        
        print("\n   🔧 Testing arithmetic:")
        result = client.call_tool("add", {"a": 10, "b": 20})
        print(f"      10 + 20 = {result}")
        
        print("\n   🔧 Testing time function:")
        result = client.call_tool("get_current_time", {})
        print(f"      Current time: {result}")
        
        # Clean up
        step(5, "Close Connection")
        client.close()
        print("   ✅ Connection closed")
    
    def demo_agent_integration(self):
        """Demonstrate how agents use MCP tools"""
        print_section("AGENT INTEGRATION (Simulated)")
        
        step(1, "Agent Initialization")
        print("   Agent ID: 123")
        print("   Agent Name: Data Analysis Agent")
        
        step(2, "Load MCP Servers for Agent")
        print("   Querying database for assigned MCP servers...")
        print("   Found 2 MCP servers assigned to agent:")
        print("     - Server 1: GitHub MCP Server")
        print("     - Server 2: AI Hub Test Server")
        
        step(3, "Create LangChain Tools from MCP")
        print("   Converting MCP tools to LangChain format...")
        print("   Created tools:")
        print("     - mcp_github_list_repos")
        print("     - mcp_github_create_issue")
        print("     - mcp_aihub_query_data")
        print("     - mcp_aihub_generate_report")
        
        step(4, "Agent Execution with MCP Tools")
        print("   User Query: 'Analyze the agent performance data'")
        print("   Agent selects tool: mcp_aihub_query_data")
        print("   Executing via MCP Gateway...")
        print("   Result: Retrieved 15 agent performance records")
        print("   Agent generates response with analysis")
    
    def show_architecture_diagram(self):
        """Display architecture diagram"""
        print_section("ARCHITECTURE OVERVIEW")
        print("""
    ┌─────────────────────────────────────────────────────┐
    │                  AI HUB MAIN APP                    │
    │                                                     │
    │  ┌─────────────┐        ┌───────────────────┐     │
    │  │GeneralAgent │◄──────►│  mcp_adapter.py   │     │
    │  └─────────────┘        └─────────┬─────────┘     │
    │                                    │                │
    └────────────────────────────────────┼────────────────┘
                                         │ HTTP/REST
                    ┌────────────────────┼────────────────┐
                    │                    ▼                │
                    │  ┌──────────────────────────────┐  │
                    │  │  MCP GATEWAY SERVICE         │  │
                    │  │  (mcp_gateway_service.py)    │  │
                    │  │  Port: 5555                  │  │
                    │  └──────────┬───────────────────┘  │
                    │             │                       │
                    │   MCP Environment (Isolated)       │
                    └─────────────┼───────────────────────┘
                                  │ MCP Protocol
                    ┌─────────────┼───────────────────────┐
                    │             ▼                       │
                    │  ┌──────────────────────────────┐  │
                    │  │    MCP SERVERS               │  │
                    │  ├──────────────────────────────┤  │
                    │  │ • aihub_mcp_server.py       │  │
                    │  │ • GitHub MCP Server          │  │
                    │  │ • Filesystem MCP Server      │  │
                    │  │ • Custom MCP Servers         │  │
                    │  └──────────────────────────────┘  │
                    │                                     │
                    │         External Systems            │
                    └─────────────────────────────────────┘
        """)
        
        print("\n📊 Database Tables:")
        print("   • MCPServers - Server configurations")
        print("   • AgentMCPServers - Agent-to-server mappings")
        print("   • MCPServerCredentials - Encrypted credentials")


def main():
    """Main demo execution"""
    print("\n" + "🚀" * 30)
    print("     MCP INTEGRATION DEMONSTRATION")
    print("🚀" * 30)
    
    demo = MCPIntegrationDemo()
    
    # Show architecture
    demo.show_architecture_diagram()
    
    # Check prerequisites
    print_section("PREREQUISITES CHECK")
    
    print("\n✅ Required components:")
    print("  1. mcp_gateway_service.py - Gateway microservice")
    print("  2. mcp_adapter.py - Main app adapter")
    print("  3. aihub_mcp_server.py - Test MCP server")
    print("  4. Database tables created (run SQL script)")
    
    # Check gateway
    print("\n🔍 Checking gateway service...")
    if not demo.check_gateway():
        print("\n⚠️  Please start the gateway service first!")
        print("    Run: python mcp_gateway_service.py")
        print("\n    Then run this demo again.")
        return
    
    # Run demos
    try:
        # Demo 1: Adapter workflow (recommended)
        demo.demo_adapter_workflow()
        
        # Demo 2: Direct client workflow
        input("\n\nPress Enter to continue with direct client demo...")
        demo.demo_direct_client_workflow()
        
        # Demo 3: Agent integration
        input("\n\nPress Enter to see agent integration simulation...")
        demo.demo_agent_integration()
        
    except Exception as e:
        print(f"\n❌ Error during demo: {e}")
        import traceback
        traceback.print_exc()
    
    # Summary
    print_section("DEMO COMPLETE")
    print("""
✅ You've seen:
   1. Gateway-based MCP integration (recommended)
   2. Direct client integration (for testing)
   3. How agents use MCP tools
   
📝 Next Steps:
   1. Add your own MCP servers to the database
   2. Assign servers to agents
   3. Use MCP tools in your agent workflows
   
🔧 Tips:
   • Use the gateway approach for production
   • Keep MCP dependencies isolated
   • Test servers before assigning to agents
    """)


if __name__ == "__main__":
    main()
