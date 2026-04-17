#!/usr/bin/env python3
"""
MCP Diagnostic Tool
===================
Diagnoses issues with MCP integration setup.
"""

import os
import sys
import subprocess
import json
import asyncio
import time
from pathlib import Path
from typing import Dict, List, Optional

# Get the parent directory (the project root)
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(parent_dir)

# Only import CommonUtils if it exists
try:
    from CommonUtils import get_mcp_gateway_api_base_url
except ImportError:
    print("Warning: CommonUtils not found, skipping log rotation")


class MCPDiagnostic:
    """Diagnostic tool for MCP integration issues"""
    
    def __init__(self):
        self.issues = []
        self.warnings = []
        self.successes = []
        
    def print_header(self, text: str):
        """Print formatted header"""
        print("\n" + "=" * 60)
        print(f"  {text}")
        print("=" * 60)
    
    def check_python_version(self):
        """Check Python version compatibility"""
        self.print_header("Python Version Check")
        
        version = sys.version_info
        print(f"Python version: {version.major}.{version.minor}.{version.micro}")
        
        if version.major == 3 and version.minor >= 8:
            self.successes.append("Python version is compatible")
            print("✅ Python version is compatible (3.8+)")
        else:
            self.issues.append("Python version should be 3.8 or higher")
            print("❌ Python version should be 3.8 or higher")
    
    def check_dependencies(self):
        """Check if required packages are installed"""
        self.print_header("Dependencies Check")
        
        packages = {
            "Core": {
                "requests": "For HTTP communication",
                "pyodbc": "For database access",
                "langchain": "For agent framework"
            },
            "MCP Gateway": {
                "fastapi": "For REST API",
                "uvicorn": "For ASGI server",
                "mcp": "For MCP protocol",
                "pydantic": "For data validation"
            },
            "Test Server": {
                "fastmcp": "For test MCP server"
            }
        }
        
        for category, deps in packages.items():
            print(f"\n{category}:")
            for package, description in deps.items():
                try:
                    # Try importing the package
                    __import__(package.replace("-", "_"))
                    print(f"  ✅ {package}: {description}")
                    self.successes.append(f"{package} installed")
                except ImportError:
                    print(f"  ❌ {package}: Missing - {description}")
                    self.issues.append(f"{package} not installed")
                    print(f"     Install with: pip install {package}")
    
    def check_test_server(self):
        """Check if the test MCP server can be started"""
        self.print_header("Test MCP Server Check")
        
        server_file = "aihub_mcp_server.py"
        
        if not os.path.exists(server_file):
            print(f"❌ {server_file} not found")
            self.issues.append(f"{server_file} not found")
            return
        
        print(f"Found {server_file}")
        
        # Try to start the server in check mode
        try:
            # Check if fastmcp is installed
            try:
                import fastmcp
                print("✅ fastmcp is installed")
            except ImportError:
                print("❌ fastmcp not installed")
                print("   Install with: pip install fastmcp")
                self.issues.append("fastmcp not installed")
                return
            
            # Try running the server with --help to test it
            result = subprocess.run(
                [sys.executable, server_file, "--help"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if "AI Hub Test Server" in result.stdout or result.returncode == 0:
                print("✅ Test server can be executed")
                self.successes.append("Test server is executable")
            else:
                print("⚠️ Test server may have issues")
                self.warnings.append("Test server may have configuration issues")
                
        except subprocess.TimeoutExpired:
            print("⚠️ Test server timed out (may be starting in interactive mode)")
            self.warnings.append("Test server timeout")
        except Exception as e:
            print(f"❌ Failed to test server: {e}")
            self.issues.append(f"Test server error: {e}")
    
    def test_stdio_communication(self):
        """Test basic stdio communication with a simple echo server"""
        self.print_header("STDIO Communication Test")
        
        # Create a simple echo test script
        test_script = """
import sys
import json

# Simple JSON-RPC echo server for testing
while True:
    try:
        line = sys.stdin.readline()
        if not line:
            break
        request = json.loads(line)
        
        # Echo back with result
        response = {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "echo": request
            }
        }
        
        print(json.dumps(response))
        sys.stdout.flush()
        
    except Exception as e:
        error_response = {
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": -32603,
                "message": str(e)
            }
        }
        print(json.dumps(error_response))
        sys.stdout.flush()
"""
        
        # Write test script
        test_file = "test_echo.py"
        with open(test_file, "w") as f:
            f.write(test_script)
        
        try:
            # Start the echo server
            proc = subprocess.Popen(
                [sys.executable, test_file],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Send test message
            test_message = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "test",
                "params": {}
            }
            
            proc.stdin.write(json.dumps(test_message) + "\n")
            proc.stdin.flush()
            
            # Read response with timeout
            import select
            if sys.platform == "win32":
                # Windows doesn't support select on pipes
                time.sleep(0.5)
                line = proc.stdout.readline()
            else:
                # Unix-like systems
                readable, _, _ = select.select([proc.stdout], [], [], 2)
                if readable:
                    line = proc.stdout.readline()
                else:
                    line = None
            
            if line:
                response = json.loads(line)
                if "result" in response:
                    print("✅ STDIO communication works")
                    self.successes.append("STDIO communication verified")
                else:
                    print("⚠️ Unexpected response format")
                    self.warnings.append("STDIO response format issue")
            else:
                print("❌ No response from STDIO test")
                self.issues.append("STDIO communication failed")
            
            proc.terminate()
            
        except Exception as e:
            print(f"❌ STDIO test failed: {e}")
            self.issues.append(f"STDIO test error: {e}")
        finally:
            # Clean up test file
            if os.path.exists(test_file):
                os.remove(test_file)
    
    def check_gateway_port(self):
        """Check if gateway port is available"""
        self.print_header("Gateway Port Check")
        
        import socket
        
        port = 5061
        
        # Try to bind to the port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(('', port))
            sock.close()
            print(f"✅ Port {port} is available")
            self.successes.append(f"Port {port} available")
        except OSError:
            print(f"⚠️ Port {port} is already in use")
            self.warnings.append(f"Port {port} in use")
            
            # Try to connect to see if it's the gateway
            try:
                import requests
                response = requests.get(f"{get_mcp_gateway_api_base_url()}/api/mcp/health", timeout=2)
                if response.status_code == 200:
                    print("   Gateway service appears to be running")
                    self.successes.append("Gateway already running")
            except:
                print("   Another service is using this port")
                self.issues.append(f"Port {port} blocked by another service")
    
    def check_file_structure(self):
        """Check if all required files are present"""
        self.print_header("File Structure Check")
        
        required_files = {
            "mcp_gateway_service.py": "Gateway service",
            "mcp_adapter.py": "Application adapter",
            "aihub_mcp_server.py": "Test MCP server",
            "mcp_user_client.py": "User client"
        }
        
        for file, description in required_files.items():
            if os.path.exists(file):
                print(f"✅ {file}: {description}")
                self.successes.append(f"{file} present")
            else:
                print(f"❌ {file}: Missing - {description}")
                self.issues.append(f"{file} missing")
    
    def test_async_patterns(self):
        """Test async/await compatibility"""
        self.print_header("Async Pattern Test")
        
        test_code = """
import asyncio
import sys

async def test_async():
    # Test basic async functionality
    await asyncio.sleep(0.1)
    return "success"

async def test_context_manager():
    # Test async context manager
    class TestContext:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
    
    async with TestContext():
        pass
    return "success"

async def main():
    result1 = await test_async()
    result2 = await test_context_manager()
    return result1 == "success" and result2 == "success"

if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result else 1)
"""
        
        test_file = "test_async.py"
        with open(test_file, "w") as f:
            f.write(test_code)
        
        try:
            result = subprocess.run(
                [sys.executable, test_file],
                capture_output=True,
                timeout=5
            )
            
            if result.returncode == 0:
                print("✅ Async patterns work correctly")
                self.successes.append("Async patterns verified")
            else:
                print("❌ Async pattern issues detected")
                self.issues.append("Async pattern problems")
        except Exception as e:
            print(f"❌ Async test failed: {e}")
            self.issues.append(f"Async test error: {e}")
        finally:
            if os.path.exists(test_file):
                os.remove(test_file)
    
    def suggest_fixes(self):
        """Provide fix suggestions based on issues found"""
        self.print_header("Diagnostic Summary")
        
        print(f"\n✅ Successes: {len(self.successes)}")
        for success in self.successes[:5]:  # Show first 5
            print(f"   - {success}")
        
        if self.warnings:
            print(f"\n⚠️ Warnings: {len(self.warnings)}")
            for warning in self.warnings:
                print(f"   - {warning}")
        
        if self.issues:
            print(f"\n❌ Issues: {len(self.issues)}")
            for issue in self.issues:
                print(f"   - {issue}")
            
            print("\n" + "=" * 60)
            print("  RECOMMENDED FIXES")
            print("=" * 60)
            
            # Provide specific fixes
            if any("not installed" in i for i in self.issues):
                print("\n1. Install missing packages:")
                print("   pip install fastapi uvicorn mcp pydantic fastmcp")
            
            if any("STDIO" in i for i in self.issues):
                print("\n2. STDIO communication issues:")
                print("   - Check Python buffering settings")
                print("   - Ensure subprocess module is working")
                print("   - Try running with: python -u (unbuffered)")
            
            if any("async" in i.lower() for i in self.issues):
                print("\n3. Async issues detected:")
                print("   - Use the fixed gateway service: mcp_gateway_service.py")
                print("   - Ensure Python 3.8+ with proper asyncio support")
            
            if any("missing" in i for i in self.issues):
                print("\n4. Missing files:")
                print("   - Ensure all files are in the same directory")
                print("   - Check file permissions")
        else:
            print("\n🎉 No critical issues found! System should be ready.")
        
        print("\n" + "=" * 60)
        print("  NEXT STEPS")
        print("=" * 60)
        print("1. Fix any critical issues listed above")
        print("2. Use mcp_gateway_service.py instead of original")
        print("3. Start gateway: python mcp_gateway_service.py")
        print("4. Run integration tests again")


def main():
    print("\n" + "🔧" * 30)
    print("     MCP DIAGNOSTIC TOOL")
    print("🔧" * 30)
    
    diagnostic = MCPDiagnostic()
    
    # Run all diagnostics
    diagnostic.check_python_version()
    diagnostic.check_dependencies()
    diagnostic.check_file_structure()
    diagnostic.check_gateway_port()
    diagnostic.check_test_server()
    diagnostic.test_stdio_communication()
    diagnostic.test_async_patterns()
    
    # Provide summary and fixes
    diagnostic.suggest_fixes()


if __name__ == "__main__":
    main()
