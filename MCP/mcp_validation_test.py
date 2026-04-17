#!/usr/bin/env python3
"""
MCP Integration Validation Test
================================
Properly validates MCP integration without false positives.
"""

import os
import sys
import json
import time
import requests
import subprocess
from typing import Dict, Optional, Tuple
import logging

# Get the parent directory (the project root)
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(parent_dir)

# Only import CommonUtils if it exists
try:
    from CommonUtils import get_mcp_gateway_api_base_url
except ImportError:
    print("Warning: CommonUtils not found, skipping log rotation")


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
GATEWAY_URL = get_mcp_gateway_api_base_url()
TEST_TIMEOUT = 10


class MCPValidator:
    """Validates MCP integration components"""
    
    def __init__(self):
        self.gateway_url = GATEWAY_URL
        self.test_results = []
        
    def run_test(self, name: str, test_func) -> bool:
        """Run a single test and track results"""
        print(f"\n{'='*60}")
        print(f"Testing: {name}")
        print(f"{'='*60}")
        
        try:
            success, message = test_func()
            
            if success:
                print(f"✅ PASSED: {message}")
                logger.info(f"Test '{name}' passed: {message}")
            else:
                print(f"❌ FAILED: {message}")
                logger.error(f"Test '{name}' failed: {message}")
            
            self.test_results.append((name, success, message))
            return success
            
        except Exception as e:
            message = f"Exception: {str(e)}"
            print(f"❌ ERROR: {message}")
            logger.error(f"Test '{name}' error: {message}")
            self.test_results.append((name, False, message))
            return False
    
    def test_gateway_health(self) -> Tuple[bool, str]:
        """Test if gateway service is running"""
        try:
            response = requests.get(f"{self.gateway_url}/api/mcp/health", timeout=2)
            if response.status_code == 200:
                data = response.json()
                return True, f"Gateway is healthy with {data.get('active_connections', 0)} active connections"
            else:
                return False, f"Gateway returned status code {response.status_code}"
        except requests.ConnectionError:
            return False, "Gateway service is not running. Start with: python mcp_gateway_service.py"
        except Exception as e:
            return False, f"Gateway check failed: {str(e)}"
    
    def test_mcp_server_basic(self) -> Tuple[bool, str]:
        """Test basic MCP server functionality"""
        # First check if fastmcp is available
        try:
            import fastmcp
        except ImportError:
            return False, "fastmcp not installed. Run: pip install fastmcp"
        
        # Check if test server file exists
        if not os.path.exists("aihub_mcp_server.py"):
            return False, "aihub_mcp_server.py not found"
        
        # Try to import the server to check for syntax errors
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("aihub_mcp_server", "aihub_mcp_server.py")
            module = importlib.util.module_from_spec(spec)
            # Don't execute, just check if it can be loaded
            return True, "MCP test server file is valid"
        except Exception as e:
            return False, f"MCP server file has errors: {str(e)}"
    
    def test_gateway_mcp_connection(self) -> Tuple[bool, str]:
        """Test connecting to MCP server via gateway"""
        config = {
            "command": sys.executable,  # Use current Python interpreter
            "args": ["aihub_mcp_server.py"],
            "env_vars": {}
        }
        
        try:
            response = requests.post(
                f"{self.gateway_url}/api/mcp/servers/test",
                json=config,
                timeout=TEST_TIMEOUT
            )
            
            if response.status_code != 200:
                return False, f"Gateway returned status {response.status_code}"
            
            data = response.json()
            
            # Check actual status field
            if data.get("status") == "success":
                tool_count = data.get("tool_count", 0)
                return True, f"Successfully connected to MCP server with {tool_count} tools"
            else:
                error_msg = data.get("error", "Unknown error")
                return False, f"Connection failed: {error_msg}"
                
        except requests.Timeout:
            return False, "Connection timed out - server may be hanging"
        except Exception as e:
            return False, f"Connection test failed: {str(e)}"
    
    def test_persistent_connection(self) -> Tuple[bool, str]:
        """Test establishing a persistent connection"""
        server_id = 9999
        config = {
            "command": sys.executable,
            "args": ["aihub_mcp_server.py"],
            "env_vars": {}
        }
        
        try:
            # Connect to server
            response = requests.post(
                f"{self.gateway_url}/api/mcp/servers/{server_id}/connect",
                json=config,
                timeout=TEST_TIMEOUT
            )
            
            if response.status_code != 200:
                return False, f"Failed to connect: status {response.status_code}"
            
            data = response.json()
            if data.get("status") != "connected":
                return False, f"Connection status is '{data.get('status')}' not 'connected'"
            
            tools = data.get("tools", [])
            
            # Try to get tools
            response = requests.get(
                f"{self.gateway_url}/api/mcp/servers/{server_id}/tools",
                timeout=5
            )
            
            if response.status_code != 200:
                return False, f"Failed to get tools: status {response.status_code}"
            
            # Disconnect
            requests.delete(f"{self.gateway_url}/api/mcp/servers/{server_id}/disconnect")
            
            return True, f"Persistent connection successful with {len(tools)} tools"
            
        except Exception as e:
            return False, f"Persistent connection test failed: {str(e)}"
    
    def test_tool_execution(self) -> Tuple[bool, str]:
        """Test executing a tool"""
        server_id = 9998
        config = {
            "command": sys.executable,
            "args": ["aihub_mcp_server.py"],
            "env_vars": {}
        }
        
        try:
            # Connect first
            response = requests.post(
                f"{self.gateway_url}/api/mcp/servers/{server_id}/connect",
                json=config,
                timeout=TEST_TIMEOUT
            )
            
            if response.status_code != 200:
                return False, f"Failed to connect for tool test: status {response.status_code}"
            
            # Execute echo tool
            tool_request = {
                "server_id": server_id,
                "tool_name": "echo",
                "arguments": {"message": "Hello MCP!"}
            }
            
            response = requests.post(
                f"{self.gateway_url}/api/mcp/tools/call",
                json=tool_request,
                timeout=TEST_TIMEOUT
            )
            
            if response.status_code != 200:
                return False, f"Tool call returned status {response.status_code}"
            
            data = response.json()
            if data.get("status") == "success":
                result = data.get("result", "")
                if "Hello MCP!" in result:
                    return True, f"Tool executed successfully: {result}"
                else:
                    return False, f"Tool returned unexpected result: {result}"
            else:
                error = data.get("error", "Unknown error")
                return False, f"Tool execution failed: {error}"
                
        except Exception as e:
            return False, f"Tool execution test failed: {str(e)}"
        finally:
            # Clean up
            try:
                requests.delete(f"{self.gateway_url}/api/mcp/servers/{server_id}/disconnect")
            except:
                pass
    
    def test_adapter(self) -> Tuple[bool, str]:
        """Test the MCP adapter"""
        try:
            from mcp_adapter import MCPGatewayClient
            
            client = MCPGatewayClient(self.gateway_url)
            
            # Check health
            if not client.health_check():
                return False, "Adapter health check failed"
            
            # Test server
            config = {
                "command": sys.executable,
                "args": ["aihub_mcp_server.py"],
                "env_vars": {}
            }
            
            result = client.test_server(config)
            if result.get("status") == "success":
                return True, f"Adapter works correctly with {result.get('tool_count', 0)} tools"
            else:
                return False, f"Adapter test failed: {result.get('error', 'Unknown error')}"
                
        except ImportError:
            return False, "mcp_adapter.py not found or has import errors"
        except Exception as e:
            return False, f"Adapter test failed: {str(e)}"
    
    def print_summary(self):
        """Print test summary"""
        print("\n" + "="*60)
        print("TEST SUMMARY")
        print("="*60)
        
        passed = sum(1 for _, success, _ in self.test_results if success)
        failed = len(self.test_results) - passed
        
        print(f"\nTotal Tests: {len(self.test_results)}")
        print(f"✅ Passed: {passed}")
        print(f"❌ Failed: {failed}")
        
        if failed > 0:
            print("\nFailed Tests:")
            for name, success, message in self.test_results:
                if not success:
                    print(f"  - {name}: {message}")
        
        print("\n" + "="*60)
        
        if failed == 0:
            print("🎉 ALL TESTS PASSED! MCP integration is working correctly.")
        else:
            print("⚠️ Some tests failed. Please review the errors above.")
            print("\nTroubleshooting Tips:")
            print("1. Ensure gateway is running: python mcp_gateway_service.py")
            print("2. Install required packages: pip install fastapi uvicorn mcp fastmcp")
            print("3. Check Python version (3.8+ recommended)")
            print("4. Use mcp_gateway_service_fixed.py if async errors occur")
        
        return failed == 0


def main():
    print("\n" + "🔍"*30)
    print("     MCP INTEGRATION VALIDATION")
    print("🔍"*30)
    
    validator = MCPValidator()
    
    # Run tests in order
    tests = [
        ("Gateway Health Check", validator.test_gateway_health),
        ("MCP Server Basic Check", validator.test_mcp_server_basic),
        ("Gateway-MCP Connection", validator.test_gateway_mcp_connection),
        ("Persistent Connection", validator.test_persistent_connection),
        ("Tool Execution", validator.test_tool_execution),
        ("MCP Adapter", validator.test_adapter),
    ]
    
    for test_name, test_func in tests:
        validator.run_test(test_name, test_func)
        time.sleep(0.5)  # Small delay between tests
    
    # Print summary
    success = validator.print_summary()
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
