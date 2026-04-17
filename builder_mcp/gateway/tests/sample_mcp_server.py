"""
Sample MCP Server for Testing
Implements stdio transport with 3 test tools.

Usage:
    python builder_mcp/gateway/tests/sample_mcp_server.py

This server communicates via stdin/stdout using JSON-RPC 2.0 (MCP protocol).
"""
import sys
import json
import datetime


# Tool definitions
TOOLS = [
    {
        "name": "echo",
        "description": "Echo back the provided message",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message to echo back"
                }
            },
            "required": ["message"]
        }
    },
    {
        "name": "add_numbers",
        "description": "Add two numbers together and return the sum",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {
                    "type": "number",
                    "description": "First number"
                },
                "b": {
                    "type": "number",
                    "description": "Second number"
                }
            },
            "required": ["a", "b"]
        }
    },
    {
        "name": "get_current_time",
        "description": "Get the current date and time",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]

# Server info
SERVER_INFO = {
    "name": "AIHub Sample MCP Server",
    "version": "1.0.0"
}


def handle_initialize(request_id, params):
    """Handle the initialize request"""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {}
            },
            "serverInfo": SERVER_INFO
        }
    }


def handle_tools_list(request_id, params):
    """Handle the tools/list request"""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "tools": TOOLS
        }
    }


def handle_tools_call(request_id, params):
    """Handle the tools/call request"""
    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})

    try:
        if tool_name == "echo":
            message = arguments.get("message", "")
            result_text = f"Echo: {message}"

        elif tool_name == "add_numbers":
            a = float(arguments.get("a", 0))
            b = float(arguments.get("b", 0))
            result_text = f"The sum of {a} and {b} is {a + b}"

        elif tool_name == "get_current_time":
            now = datetime.datetime.now()
            result_text = f"Current time: {now.strftime('%Y-%m-%d %H:%M:%S')}"

        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                    "isError": True
                }
            }

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": result_text}],
                "isError": False
            }
        }

    except Exception as e:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                "isError": True
            }
        }


def handle_request(request):
    """Route a JSON-RPC request to the appropriate handler"""
    method = request.get("method", "")
    request_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        return handle_initialize(request_id, params)
    elif method == "tools/list":
        return handle_tools_list(request_id, params)
    elif method == "tools/call":
        return handle_tools_call(request_id, params)
    elif method == "notifications/initialized":
        # Notification — no response needed
        return None
    else:
        if request_id is not None:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                }
            }
        return None


def main():
    """Main loop: read JSON-RPC messages from stdin, write responses to stdout"""
    sys.stderr.write("Sample MCP Server started (stdio mode)\n")
    sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"Invalid JSON: {e}\n")
            sys.stderr.flush()
            continue

        response = handle_request(request)

        if response is not None:
            output = json.dumps(response) + "\n"
            sys.stdout.write(output)
            sys.stdout.flush()


if __name__ == "__main__":
    main()
