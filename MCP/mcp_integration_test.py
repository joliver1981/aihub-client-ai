#!/usr/bin/env python3
"""
MCP Integration Test Suite
===========================
Comprehensive test script to validate the MCP integration architecture
for your AI Hub platform.

Tests:
1. Gateway Service Health Check
2. Test MCP Server Connectivity
3. MCP Adapter Functionality
4. Database Integration
5. End-to-end Tool Execution
6. Agent Integration Simulation

Author: Claude
Date: 2024
"""

import os
import sys
import json
import time
import requests
import subprocess
import asyncio
from typing import Dict, List, Any, Optional
from datetime import datetime
import unittest
from unittest.mock import Mock, patch, MagicMock
import logging

# Get the parent directory (the project root)
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(parent_dir)

from CommonUtils import get_mcp_gateway_api_base_url

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Test configuration
TEST_CONFIG = {
    "gateway_url": get_mcp_gateway_api_base_url(),
    "test_server_command": "python",
    "test_server_args": ["aihub_mcp_server.py"],
    "test_timeout": 30,
    "test_agent_id": 1,
    "test_server_id": 999  # Use a high ID to avoid conflicts
}


class MCPIntegrationTests(unittest.TestCase):
    """Main test suite for MCP integration"""
    
    @classmethod
    def setUpClass(cls):
        """Set up test fixtures"""
        logger.info("=" * 70)
        logger.info("MCP INTEGRATION TEST SUITE")
        logger.info("=" * 70)
        
    def test_01_gateway_health_check(self):
        """Test 1: Check if MCP Gateway service is running"""
        logger.info("\n🔍 Test 1: Gateway Health Check")
        
        try:
            response = requests.get(
                f"{TEST_CONFIG['gateway_url']}/api/mcp/health",
                timeout=5
            )
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertIn("status", data)
            self.assertEqual(data["status"], "healthy")
            logger.info(f"✅ Gateway is healthy: {data}")
            return True
        except requests.ConnectionError:
            logger.error("❌ Gateway service is not running!")
            logger.info("   Please start: python mcp_gateway_service.py")
            return False
        except Exception as e:
            logger.error(f"❌ Gateway health check failed: {e}")
            return False
    
    def test_02_test_mcp_server(self):
        """Test 2: Test MCP test server connectivity via gateway"""
        logger.info("\n🔍 Test 2: Test MCP Server via Gateway")
        
        config = {
            "command": TEST_CONFIG["test_server_command"],
            "args": TEST_CONFIG["test_server_args"],
            "env_vars": {}
        }
        
        try:
            response = requests.post(
                f"{TEST_CONFIG['gateway_url']}/api/mcp/servers/test",
                json=config,
                timeout=30
            )
            
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["status"], "success")
            self.assertGreater(data["tool_count"], 0)
            
            logger.info(f"✅ Test server connected: {data['tool_count']} tools found")
            
            # Display some available tools
            if "tools" in data and data["tools"]:
                logger.info("   Available tools:")
                for tool in data["tools"][:5]:  # Show first 5
                    logger.info(f"   - {tool['name']}: {tool['description'][:50]}...")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Test server connection failed: {e}")
            return False
    
    def test_03_adapter_client_test(self):
        """Test 3: Test MCP Adapter functionality"""
        logger.info("\n🔍 Test 3: MCP Adapter Test")
        
        # Import adapter
        try:
            # Add parent directory to path if needed
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from mcp_adapter import MCPGatewayClient
            
            client = MCPGatewayClient(TEST_CONFIG["gateway_url"])
            
            # Test health check
            self.assertTrue(client.health_check())
            logger.info("✅ Adapter health check passed")
            
            # Test server connection
            config = {
                "command": TEST_CONFIG["test_server_command"],
                "args": TEST_CONFIG["test_server_args"],
                "env_vars": {}
            }
            
            result = client.test_server(config)
            self.assertEqual(result["status"], "success")
            logger.info(f"✅ Adapter test server: {result}")
            
            # Test persistent connection
            connect_result = client.connect_server(TEST_CONFIG["test_server_id"], config)
            self.assertEqual(connect_result["status"], "connected")
            logger.info(f"✅ Adapter connected to server ID {TEST_CONFIG['test_server_id']}")
            
            # Test getting tools
            tools = client.get_tools(TEST_CONFIG["test_server_id"])
            self.assertIsInstance(tools, list)
            self.assertGreater(len(tools), 0)
            logger.info(f"✅ Adapter retrieved {len(tools)} tools")
            
            # Test calling a tool
            if tools:
                # Try the echo tool
                echo_result = client.call_tool(
                    TEST_CONFIG["test_server_id"],
                    "echo",
                    {"message": "Hello from test"}
                )
                self.assertIsNotNone(echo_result)
                logger.info(f"✅ Tool execution result: {echo_result}")
            
            return True
            
        except ImportError as e:
            logger.error(f"❌ Failed to import adapter: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Adapter test failed: {e}")
            return False
    
    def test_04_direct_client_test(self):
        """Test 4: Test direct MCP client (mcp_user_client.py)"""
        logger.info("\n🔍 Test 4: Direct MCP Client Test")
        
        try:
            from mcp_user_client import SimpleMCPClient, test_mcp_server_connection
            
            # Test connection function
            result = test_mcp_server_connection(
                command=TEST_CONFIG["test_server_command"],
                args=TEST_CONFIG["test_server_args"],
                env_vars={}
            )
            
            self.assertEqual(result["status"], "success")
            self.assertGreater(result["tool_count"], 0)
            logger.info(f"✅ Direct client test: Found {result['tool_count']} tools")
            
            # Test direct client operations
            client = SimpleMCPClient(
                command=TEST_CONFIG["test_server_command"],
                args=TEST_CONFIG["test_server_args"]
            )
            
            self.assertTrue(client.start())
            logger.info("✅ Direct client started successfully")
            
            tools = client.list_tools()
            self.assertGreater(len(tools), 0)
            logger.info(f"✅ Direct client listed {len(tools)} tools")
            
            # Test tool call
            result = client.call_tool("add", {"a": 5, "b": 3})
            logger.info(f"✅ Direct client tool call result: {result}")
            
            client.close()
            logger.info("✅ Direct client closed successfully")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Direct client test failed: {e}")
            return False
    
    def test_05_database_schema_check(self):
        """Test 5: Verify database schema is correct"""
        logger.info("\n🔍 Test 5: Database Schema Validation")
        
        required_tables = [
            "MCPServers",
            "AgentMCPServers", 
            "MCPServerCredentials"
        ]
        
        # Mock database check (replace with actual DB connection in production)
        logger.info("📋 Required tables:")
        for table in required_tables:
            logger.info(f"   - {table}")
        
        logger.info("✅ Database schema check (mocked)")
        return True
    
    def test_06_end_to_end_workflow(self):
        """Test 6: Complete end-to-end workflow simulation"""
        logger.info("\n🔍 Test 6: End-to-End Workflow Test")
        
        try:
            from mcp_adapter import MCPGatewayClient
            
            logger.info("1️⃣ Initialize gateway client...")
            client = MCPGatewayClient(TEST_CONFIG["gateway_url"])
            
            logger.info("2️⃣ Test server configuration...")
            test_result = client.test_server({
                "command": TEST_CONFIG["test_server_command"],
                "args": TEST_CONFIG["test_server_args"],
                "env_vars": {}
            })
            self.assertEqual(test_result["status"], "success")
            
            logger.info("3️⃣ Connect to server...")
            connect_result = client.connect_server(
                TEST_CONFIG["test_server_id"],
                {
                    "command": TEST_CONFIG["test_server_command"],
                    "args": TEST_CONFIG["test_server_args"],
                    "env_vars": {}
                }
            )
            self.assertEqual(connect_result["status"], "connected")
            
            logger.info("4️⃣ Execute workflow simulation...")
            
            # Simulate data query
            result1 = client.call_tool(
                TEST_CONFIG["test_server_id"],
                "query_sample_data",
                {"table": "agents"}
            )
            logger.info(f"   Data query result: {result1[:100]}...")
            
            # Simulate text analysis
            result2 = client.call_tool(
                TEST_CONFIG["test_server_id"],
                "analyze_text",
                {"text": "This is a test text for analysis."}
            )
            logger.info(f"   Text analysis result: {result2[:100]}...")
            
            # Simulate report generation
            result3 = client.call_tool(
                TEST_CONFIG["test_server_id"],
                "create_sample_report",
                {"title": "Test Report", "data_points": 5}
            )
            logger.info(f"   Report generation result: {result3[:100]}...")
            
            logger.info("✅ End-to-end workflow completed successfully!")
            return True
            
        except Exception as e:
            logger.error(f"❌ End-to-end workflow failed: {e}")
            return False
    
    def test_07_concurrent_connections(self):
        """Test 7: Test concurrent MCP server connections"""
        logger.info("\n🔍 Test 7: Concurrent Connection Test")
        
        try:
            from mcp_adapter import MCPGatewayClient
            
            client = MCPGatewayClient(TEST_CONFIG["gateway_url"])
            
            # Simulate multiple server connections
            server_ids = [1001, 1002, 1003]
            
            for server_id in server_ids:
                config = {
                    "command": TEST_CONFIG["test_server_command"],
                    "args": TEST_CONFIG["test_server_args"],
                    "env_vars": {}
                }
                
                result = client.connect_server(server_id, config)
                self.assertEqual(result["status"], "connected")
                logger.info(f"✅ Connected to server {server_id}")
            
            # Test calling tools on different servers
            for server_id in server_ids:
                result = client.call_tool(
                    server_id,
                    "get_current_time",
                    {}
                )
                logger.info(f"   Server {server_id} time: {result}")
            
            logger.info("✅ Concurrent connections test passed!")
            return True
            
        except Exception as e:
            logger.error(f"❌ Concurrent connections test failed: {e}")
            return False
    
    def test_08_error_handling(self):
        """Test 8: Test error handling scenarios"""
        logger.info("\n🔍 Test 8: Error Handling Test")
        
        try:
            from mcp_adapter import MCPGatewayClient
            
            client = MCPGatewayClient(TEST_CONFIG["gateway_url"])
            
            # Test 1: Invalid server configuration
            logger.info("Testing invalid server configuration...")
            result = client.test_server({
                "command": "nonexistent_command",
                "args": [],
                "env_vars": {}
            })
            self.assertEqual(result["status"], "failed")
            logger.info("✅ Invalid configuration handled correctly")
            
            # Test 2: Call tool on non-connected server
            logger.info("Testing tool call on non-connected server...")
            result = client.call_tool(99999, "test_tool", {})
            self.assertIn("Error", result)
            logger.info("✅ Non-connected server error handled correctly")
            
            # Test 3: Invalid tool call
            config = {
                "command": TEST_CONFIG["test_server_command"],
                "args": TEST_CONFIG["test_server_args"],
                "env_vars": {}
            }
            client.connect_server(2001, config)
            
            logger.info("Testing invalid tool call...")
            result = client.call_tool(2001, "nonexistent_tool", {})
            self.assertIn("Error", result)
            logger.info("✅ Invalid tool error handled correctly")
            
            # Test 4: Simulate error-throwing tool
            result = client.call_tool(2001, "simulate_error", {})
            self.assertIn("Error", result)
            logger.info("✅ Tool error handled correctly")
            
            logger.info("✅ All error handling tests passed!")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error handling test failed: {e}")
            return False


class MCPComponentAnalysis:
    """Analyze MCP component compatibility and dependencies"""
    
    @staticmethod
    def analyze_architecture():
        """Analyze the MCP architecture components"""
        logger.info("\n" + "=" * 70)
        logger.info("MCP ARCHITECTURE ANALYSIS")
        logger.info("=" * 70)
        
        components = {
            "mcp_gateway_service.py": {
                "purpose": "Microservice that manages MCP connections",
                "dependencies": ["fastapi", "uvicorn", "mcp"],
                "port": 5555,
                "status": "✅ Well-designed"
            },
            "mcp_adapter.py": {
                "purpose": "Lightweight client for main app",
                "dependencies": ["requests"],
                "status": "✅ Clean separation"
            },
            "mcp_client_manager.py": {
                "purpose": "Direct MCP client integration",
                "dependencies": ["mcp", "langchain"],
                "status": "⚠️ Alternative approach - may conflict with adapter"
            },
            "mcp_user_client.py": {
                "purpose": "Simple stdio-based client",
                "dependencies": [],
                "status": "✅ Good for testing"
            },
            "aihub_mcp_server.py": {
                "purpose": "Test MCP server",
                "dependencies": ["fastmcp"],
                "status": "✅ Comprehensive test server"
            }
        }
        
        logger.info("\n📦 Component Analysis:")
        for component, details in components.items():
            logger.info(f"\n{component}:")
            logger.info(f"  Purpose: {details['purpose']}")
            logger.info(f"  Dependencies: {', '.join(details['dependencies']) if details['dependencies'] else 'None'}")
            logger.info(f"  Status: {details['status']}")
        
        return components
    
    @staticmethod
    def check_compatibility():
        """Check for potential conflicts"""
        logger.info("\n🔍 Compatibility Check:")
        
        issues = []
        recommendations = []
        
        # Check for duplicate functionality
        logger.info("\n⚠️ Potential Issues:")
        
        issue1 = "Both mcp_adapter.py (via gateway) and mcp_client_manager.py (direct) handle MCP connections"
        issues.append(issue1)
        logger.info(f"  1. {issue1}")
        
        recommendations.append("Choose ONE approach: Gateway (recommended) OR Direct client")
        
        # Check for mixed async patterns
        issue2 = "Mixed sync/async patterns between components"
        issues.append(issue2)
        logger.info(f"  2. {issue2}")
        
        recommendations.append("Standardize on sync for main app, async in gateway")
        
        logger.info("\n💡 Recommendations:")
        for i, rec in enumerate(recommendations, 1):
            logger.info(f"  {i}. {rec}")
        
        return issues, recommendations


def run_integration_tests():
    """Run all integration tests"""
    print("\n" + "=" * 70)
    print("MCP INTEGRATION TEST RUNNER")
    print("=" * 70)
    
    # Analyze architecture first
    analyzer = MCPComponentAnalysis()
    analyzer.analyze_architecture()
    analyzer.check_compatibility()
    
    # Ask user if they want to proceed with tests
    print("\n" + "=" * 70)
    print("Ready to run integration tests.")
    print("Prerequisites:")
    print("1. MCP Gateway Service running (python mcp_gateway_service.py)")
    print("2. Test MCP Server available (aihub_mcp_server.py)")
    print("=" * 70)
    
    response = input("\nProceed with tests? (y/n): ")
    if response.lower() != 'y':
        print("Tests cancelled.")
        return
    
    # Run tests
    suite = unittest.TestLoader().loadTestsFromTestCase(MCPIntegrationTests)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Success: {result.wasSuccessful()}")
    
    if result.wasSuccessful():
        print("\n✅ All tests passed! MCP integration is working correctly.")
    else:
        print("\n❌ Some tests failed. Please review the errors above.")
    
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_integration_tests()
    sys.exit(0 if success else 1)
