"""
AI Hub Test MCP Server
=======================

A comprehensive MCP server that serves two purposes:
1. Testing server for developing your MCP client integration
2. Foundation for future AI Hub application integration

This server provides:
- Test tools for validating client functionality
- Sample data for testing
- Future: Direct integration with your AI Hub platform

Usage:
    # Install dependencies
    pip install fastmcp

    # Run in stdio mode (for testing with Claude Desktop)
    python aihub_mcp_server.py

    # Run in HTTP/SSE mode (for remote access)
    python aihub_mcp_server.py --http

    # Or use FastMCP CLI
    fastmcp run aihub_mcp_server.py
    fastmcp dev aihub_mcp_server.py  # With inspector UI

Author: Your Team
Version: 1.0.0
"""

from fastmcp import FastMCP
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import json
import random

# Create the MCP server
mcp = FastMCP("AI Hub Test Server")

# ============================================================================
# SECTION 1: BASIC TEST TOOLS
# These help you test that your client is working correctly
# ============================================================================

@mcp.tool()
def echo(message: str) -> str:
    """
    Echo back the input message. Useful for basic connectivity testing.
    
    Args:
        message: Any string to echo back
        
    Returns:
        The same message that was sent
    """
    return f"Echo: {message}"


@mcp.tool()
def add(a: int, b: int) -> int:
    """
    Add two numbers together. Tests basic parameter passing.
    
    Args:
        a: First number
        b: Second number
        
    Returns:
        Sum of a and b
    """
    return a + b


@mcp.tool()
def get_current_time() -> str:
    """
    Get the current server time. Tests tool execution with no parameters.
    
    Returns:
        Current timestamp in ISO format
    """
    return datetime.now().isoformat()


@mcp.tool()
def generate_test_data(count: int = 10) -> str:
    """
    Generate test data for testing large responses.
    
    Args:
        count: Number of test records to generate (1-100)
        
    Returns:
        JSON string with test data
    """
    if count < 1 or count > 100:
        return json.dumps({"error": "Count must be between 1 and 100"})
    
    data = []
    for i in range(count):
        data.append({
            "id": i + 1,
            "name": f"Test Record {i + 1}",
            "value": random.randint(1, 1000),
            "timestamp": (datetime.now() - timedelta(days=i)).isoformat(),
            "status": random.choice(["active", "pending", "completed"])
        })
    
    return json.dumps(data, indent=2)


@mcp.tool()
def simulate_error() -> str:
    """
    Simulate an error condition. Tests error handling in your client.
    
    Returns:
        Raises an exception to test error handling
    """
    raise Exception("This is a simulated error for testing error handling!")


# ============================================================================
# SECTION 2: DATA QUERY SIMULATION TOOLS
# These simulate database queries similar to what your agents do
# ============================================================================

# Sample database
SAMPLE_DATABASE = {
    "users": [
        {"id": 1, "name": "John Doe", "email": "john@example.com", "role": "admin"},
        {"id": 2, "name": "Jane Smith", "email": "jane@example.com", "role": "user"},
        {"id": 3, "name": "Bob Wilson", "email": "bob@example.com", "role": "user"},
    ],
    "orders": [
        {"id": 101, "user_id": 1, "product": "Widget A", "amount": 99.99, "status": "completed"},
        {"id": 102, "user_id": 2, "product": "Widget B", "amount": 149.99, "status": "pending"},
        {"id": 103, "user_id": 1, "product": "Widget C", "amount": 79.99, "status": "completed"},
    ],
    "agents": [
        {"id": 1, "name": "Data Analysis Agent", "description": "Analyzes data", "enabled": True},
        {"id": 2, "name": "Report Generator", "description": "Generates reports", "enabled": True},
        {"id": 3, "name": "File Manager", "description": "Manages files", "enabled": False},
    ]
}


@mcp.tool()
def query_sample_data(table: str, filter_key: Optional[str] = None, 
                     filter_value: Optional[str] = None) -> str:
    """
    Query sample data from simulated database tables.
    
    Args:
        table: Table name (users, orders, or agents)
        filter_key: Optional field to filter on
        filter_value: Optional value to filter by
        
    Returns:
        JSON string with query results
    """
    if table not in SAMPLE_DATABASE:
        return json.dumps({
            "error": f"Table '{table}' not found. Available: {list(SAMPLE_DATABASE.keys())}"
        })
    
    results = SAMPLE_DATABASE[table]
    
    # Apply filter if provided
    if filter_key and filter_value:
        results = [
            row for row in results 
            if str(row.get(filter_key, "")).lower() == filter_value.lower()
        ]
    
    return json.dumps({
        "table": table,
        "count": len(results),
        "results": results
    }, indent=2)


@mcp.tool()
def get_table_schema(table: str) -> str:
    """
    Get the schema for a sample table.
    
    Args:
        table: Table name (users, orders, or agents)
        
    Returns:
        JSON string describing the table schema
    """
    schemas = {
        "users": {
            "columns": [
                {"name": "id", "type": "int", "description": "User ID"},
                {"name": "name", "type": "string", "description": "User full name"},
                {"name": "email", "type": "string", "description": "Email address"},
                {"name": "role", "type": "string", "description": "User role (admin/user)"}
            ]
        },
        "orders": {
            "columns": [
                {"name": "id", "type": "int", "description": "Order ID"},
                {"name": "user_id", "type": "int", "description": "Foreign key to users"},
                {"name": "product", "type": "string", "description": "Product name"},
                {"name": "amount", "type": "float", "description": "Order amount"},
                {"name": "status", "type": "string", "description": "Order status"}
            ]
        },
        "agents": {
            "columns": [
                {"name": "id", "type": "int", "description": "Agent ID"},
                {"name": "name", "type": "string", "description": "Agent name"},
                {"name": "description", "type": "string", "description": "Agent description"},
                {"name": "enabled", "type": "boolean", "description": "Whether agent is enabled"}
            ]
        }
    }
    
    if table not in schemas:
        return json.dumps({
            "error": f"Schema for '{table}' not found. Available: {list(schemas.keys())}"
        })
    
    return json.dumps(schemas[table], indent=2)


# ============================================================================
# SECTION 3: AGENT WORKFLOW SIMULATION TOOLS
# These simulate typical AI Hub agent workflows
# ============================================================================

@mcp.tool()
def create_sample_report(title: str, data_points: int = 5) -> str:
    """
    Generate a sample report with random data. Simulates report generation.
    
    Args:
        title: Report title
        data_points: Number of data points to include (1-20)
        
    Returns:
        Formatted report as markdown string
    """
    if data_points < 1 or data_points > 20:
        return "Error: data_points must be between 1 and 20"
    
    report = f"# {title}\n\n"
    report += f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    report += "## Summary\n\n"
    report += "This is a sample report generated by the AI Hub Test MCP Server.\n\n"
    report += "## Data Points\n\n"
    
    for i in range(data_points):
        metric = f"Metric {chr(65 + i)}"
        value = random.randint(100, 1000)
        trend = random.choice(["↑", "↓", "→"])
        report += f"- **{metric}:** {value} {trend}\n"
    
    report += "\n## Analysis\n\n"
    report += "Based on the data points above, we can observe various trends. "
    report += "This analysis section would contain insights from the agent.\n\n"
    report += "## Recommendations\n\n"
    report += "1. Continue monitoring key metrics\n"
    report += "2. Investigate any anomalies\n"
    report += "3. Review performance quarterly\n"
    
    return report


@mcp.tool()
def check_workflow_status(workflow_id: str) -> str:
    """
    Check the status of a workflow. Simulates workflow monitoring.
    
    Args:
        workflow_id: The workflow ID to check
        
    Returns:
        JSON string with workflow status
    """
    # Simulate different workflow states
    states = ["pending", "running", "completed", "failed", "waiting_approval"]
    status = random.choice(states)
    
    result = {
        "workflow_id": workflow_id,
        "status": status,
        "progress": random.randint(0, 100) if status == "running" else 100,
        "started_at": (datetime.now() - timedelta(minutes=random.randint(5, 60))).isoformat(),
        "updated_at": datetime.now().isoformat(),
        "steps_completed": random.randint(0, 5),
        "steps_total": 5
    }
    
    if status == "completed":
        result["completed_at"] = datetime.now().isoformat()
        result["result"] = "Workflow completed successfully"
    elif status == "failed":
        result["error"] = "Simulated error in step 3"
    elif status == "waiting_approval":
        result["approval_pending_from"] = "admin@example.com"
    
    return json.dumps(result, indent=2)


@mcp.tool()
def send_test_notification(recipient: str, message: str, 
                          notification_type: str = "info") -> str:
    """
    Simulate sending a notification. Tests notification workflows.
    
    Args:
        recipient: Email or user ID to send to
        message: Notification message
        notification_type: Type of notification (info, warning, error, success)
        
    Returns:
        JSON string with notification status
    """
    valid_types = ["info", "warning", "error", "success"]
    if notification_type not in valid_types:
        return json.dumps({
            "error": f"Invalid type. Must be one of: {valid_types}"
        })
    
    result = {
        "status": "sent",
        "notification_id": f"notif_{random.randint(1000, 9999)}",
        "recipient": recipient,
        "message": message,
        "type": notification_type,
        "sent_at": datetime.now().isoformat(),
        "delivery_method": random.choice(["email", "sms", "push"])
    }
    
    return json.dumps(result, indent=2)


# ============================================================================
# SECTION 4: FILE/DOCUMENT SIMULATION TOOLS
# These simulate document processing capabilities
# ============================================================================

SAMPLE_DOCUMENTS = {
    "doc1.txt": "This is a sample text document.\nIt has multiple lines.\nYou can read it!",
    "report.md": "# Sample Report\n\n## Overview\n\nThis is a markdown document.",
    "data.json": '{"name": "Test Data", "values": [1, 2, 3, 4, 5]}'
}


@mcp.tool()
def list_sample_documents() -> str:
    """
    List available sample documents.
    
    Returns:
        JSON string with list of available documents
    """
    docs = []
    for filename, content in SAMPLE_DOCUMENTS.items():
        docs.append({
            "filename": filename,
            "size": len(content),
            "type": filename.split('.')[-1] if '.' in filename else "unknown"
        })
    
    return json.dumps({"documents": docs, "count": len(docs)}, indent=2)


@mcp.tool()
def read_sample_document(filename: str) -> str:
    """
    Read a sample document by filename.
    
    Args:
        filename: Name of the document to read
        
    Returns:
        Document content as string
    """
    if filename not in SAMPLE_DOCUMENTS:
        return json.dumps({
            "error": f"Document '{filename}' not found",
            "available": list(SAMPLE_DOCUMENTS.keys())
        })
    
    return SAMPLE_DOCUMENTS[filename]


@mcp.tool()
def analyze_text(text: str) -> str:
    """
    Analyze text and return statistics. Simulates text analysis.
    
    Args:
        text: Text to analyze
        
    Returns:
        JSON string with text statistics
    """
    words = text.split()
    lines = text.split('\n')
    
    analysis = {
        "character_count": len(text),
        "word_count": len(words),
        "line_count": len(lines),
        "average_word_length": sum(len(word) for word in words) / len(words) if words else 0,
        "longest_word": max(words, key=len) if words else "",
        "unique_words": len(set(word.lower() for word in words))
    }
    
    return json.dumps(analysis, indent=2)


# ============================================================================
# SECTION 5: RESOURCES (Read-only data endpoints)
# ============================================================================

@mcp.resource("config://server")
def get_server_config() -> str:
    """Server configuration information"""
    config = {
        "server_name": "AI Hub Test Server",
        "version": "1.0.0",
        "features": [
            "basic_tools",
            "data_query",
            "workflow_simulation",
            "document_processing"
        ],
        "max_request_size": "10MB",
        "supported_formats": ["json", "text", "markdown"]
    }
    return json.dumps(config, indent=2)


@mcp.resource("stats://usage")
def get_usage_stats() -> str:
    """Simulated usage statistics"""
    stats = {
        "total_requests": random.randint(100, 1000),
        "successful_requests": random.randint(90, 95),
        "failed_requests": random.randint(5, 10),
        "average_response_time_ms": random.randint(50, 200),
        "uptime_hours": random.randint(24, 720),
        "last_reset": (datetime.now() - timedelta(days=30)).isoformat()
    }
    return json.dumps(stats, indent=2)


@mcp.resource("sample://data/users")
def get_sample_users() -> str:
    """Sample user data"""
    return json.dumps(SAMPLE_DATABASE["users"], indent=2)


# ============================================================================
# SECTION 6: PROMPTS (Reusable templates)
# ============================================================================

@mcp.prompt()
def analyze_data_prompt(data_description: str) -> str:
    """Template for data analysis requests"""
    return f"""Please analyze the following data:

{data_description}

Provide:
1. Summary of key findings
2. Notable patterns or trends
3. Any anomalies or concerns
4. Recommendations

Format your response as a professional report."""


@mcp.prompt()
def generate_report_prompt(report_type: str, data_source: str) -> str:
    """Template for report generation"""
    return f"""Generate a {report_type} report using data from {data_source}.

Include:
- Executive summary
- Detailed analysis
- Visualizations (describe them)
- Recommendations
- Next steps

Use professional business language."""


# ============================================================================
# SECTION 7: FUTURE AI HUB INTEGRATION STUBS
# These are placeholder tools for future integration with your platform
# ============================================================================

@mcp.tool()
def get_aihub_agent_info(agent_id: int) -> str:
    """
    Get information about an AI Hub agent.
    
    FUTURE: This will connect to your actual database.
    Currently returns sample data.
    
    Args:
        agent_id: The agent ID to query
        
    Returns:
        JSON string with agent information
    """
    # TODO: Replace with actual database query
    # conn = get_db_connection()
    # cursor.execute("SELECT * FROM Agents WHERE id = ?", agent_id)
    
    sample_agent = {
        "id": agent_id,
        "name": f"Agent {agent_id}",
        "description": "Sample agent from AI Hub",
        "enabled": True,
        "create_date": datetime.now().isoformat(),
        "tool_count": random.randint(5, 15),
        "tools": ["query_data", "send_email", "create_report"],
        "note": "⚠️ This is sample data. Future: Connect to real AI Hub database"
    }
    
    return json.dumps(sample_agent, indent=2)


@mcp.tool()
def execute_aihub_workflow(workflow_name: str, parameters: str) -> str:
    """
    Execute an AI Hub workflow.
    
    FUTURE: This will trigger actual workflows in your platform.
    Currently returns simulated response.
    
    Args:
        workflow_name: Name of the workflow to execute
        parameters: JSON string with workflow parameters
        
    Returns:
        JSON string with execution result
    """
    # TODO: Replace with actual workflow execution
    # from workflow_execution import execute_workflow
    # result = execute_workflow(workflow_name, json.loads(parameters))
    
    result = {
        "status": "started",
        "workflow_id": f"wf_{random.randint(10000, 99999)}",
        "workflow_name": workflow_name,
        "parameters": json.loads(parameters) if parameters else {},
        "started_at": datetime.now().isoformat(),
        "estimated_completion": (datetime.now() + timedelta(minutes=5)).isoformat(),
        "note": "⚠️ This is simulated. Future: Execute real AI Hub workflows"
    }
    
    return json.dumps(result, indent=2)


@mcp.tool()
def query_aihub_database(query_description: str) -> str:
    """
    Query the AI Hub database using natural language.
    
    FUTURE: This will use your LLMDataEngine to execute actual queries.
    Currently returns sample data.
    
    Args:
        query_description: Natural language description of what to query
        
    Returns:
        JSON string with query results
    """
    # TODO: Replace with actual LLMDataEngine integration
    # from LLMDataEngineV2 import LLMDataEngine
    # engine = LLMDataEngine(...)
    # result = engine.execute_natural_language_query(query_description)
    
    result = {
        "query_description": query_description,
        "interpreted_sql": "SELECT * FROM Agents WHERE enabled = 1 LIMIT 10",
        "results": [
            {"id": 1, "name": "Agent A", "enabled": True},
            {"id": 2, "name": "Agent B", "enabled": True}
        ],
        "row_count": 2,
        "execution_time_ms": random.randint(50, 200),
        "note": "⚠️ This is sample data. Future: Use real LLMDataEngine"
    }
    
    return json.dumps(result, indent=2)


# ============================================================================
# MAIN: Run the server
# ============================================================================

if __name__ == "__main__":
    import sys
    
    # Check for HTTP mode flag
    if "--http" in sys.argv or "--sse" in sys.argv:
        # HTTP mode - safe to print to stdout
        print("=" * 70)
        print("🚀 Starting AI Hub Test MCP Server in HTTP/SSE mode")
        print("=" * 70)
        print()
        print("Server will be available at: http://localhost:8000/sse")
        print("Use this for remote access and testing")
        print()
        print("To test with your client:")
        print("  async with Client('http://localhost:8000/sse') as client:")
        print("      tools = await client.list_tools()")
        print()
        
        #mcp.run(transport="sse", port=8000)
        mcp.run(transport="http", port=8000)
    else:
        # STDIO mode - print to stderr only (stdout is for JSON-RPC messages)
        import sys
        sys.stderr.write("=" * 70 + "\n")
        sys.stderr.write("🚀 AI Hub Test MCP Server - STDIO Mode\n")
        sys.stderr.write("=" * 70 + "\n")
        sys.stderr.write("Server is running and waiting for JSON-RPC messages.\n")
        sys.stderr.write("Logs will appear here. JSON-RPC communication is on stdout.\n")
        sys.stderr.write("=" * 70 + "\n")
        sys.stderr.flush()
        
        mcp.run()
