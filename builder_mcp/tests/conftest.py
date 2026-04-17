"""
Shared pytest fixtures for MCP integration tests.
"""
import os
import sys
import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

# Ensure project root is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


# ============================================================================
# Mock Database Fixtures
# ============================================================================

@pytest.fixture
def mock_db_connection():
    """Mock database connection with cursor"""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn, mock_cursor


@pytest.fixture
def mock_get_db_connection(mock_db_connection):
    """Patch get_db_connection to return mock"""
    mock_conn, mock_cursor = mock_db_connection
    with patch('CommonUtils.get_db_connection', return_value=mock_conn) as mock_func:
        yield mock_func, mock_conn, mock_cursor


# ============================================================================
# Mock Gateway Client Fixtures
# ============================================================================

@pytest.fixture
def mock_gateway():
    """Mock MCPGatewayClient"""
    gateway = MagicMock()
    gateway.health_check.return_value = True
    gateway.base_url = 'http://localhost:5071'
    return gateway


@pytest.fixture
def mock_gateway_unhealthy():
    """Mock MCPGatewayClient that is not available"""
    gateway = MagicMock()
    gateway.health_check.return_value = False
    return gateway


# ============================================================================
# Sample MCP Tool Definitions
# ============================================================================

@pytest.fixture
def sample_mcp_tools():
    """Sample MCP tool definitions as returned by gateway"""
    return [
        {
            "name": "read_file",
            "description": "Read the contents of a file",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file"},
                    "encoding": {"type": "string", "description": "File encoding"}
                },
                "required": ["path"]
            }
        },
        {
            "name": "write_file",
            "description": "Write content to a file",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file"},
                    "content": {"type": "string", "description": "Content to write"}
                },
                "required": ["path", "content"]
            }
        },
        {
            "name": "list_directory",
            "description": "List files in a directory",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path"},
                    "recursive": {"type": "boolean", "description": "Recurse into subdirs"},
                    "max_depth": {"type": "integer", "description": "Max depth"}
                },
                "required": ["path"]
            }
        },
        {
            "name": "search",
            "description": "Search for text in files",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Search pattern"},
                    "path": {"type": "string", "description": "Starting path"},
                    "file_types": {"type": "array", "description": "File extensions to include"},
                    "options": {"type": "object", "description": "Search options"}
                },
                "required": ["pattern"]
            }
        }
    ]


@pytest.fixture
def sample_mcp_tool_no_params():
    """MCP tool with no parameters"""
    return {
        "name": "get_time",
        "description": "Get current time",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    }


@pytest.fixture
def sample_mcp_tool_all_types():
    """MCP tool with all JSON Schema types"""
    return {
        "name": "complex_tool",
        "description": "Tool with all types",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "A string"},
                "count": {"type": "integer", "description": "An integer"},
                "ratio": {"type": "number", "description": "A float"},
                "active": {"type": "boolean", "description": "A boolean"},
                "items": {"type": "array", "description": "A list"},
                "config": {"type": "object", "description": "A dict"}
            },
            "required": ["text", "count"]
        }
    }


# ============================================================================
# Sample Server Database Rows
# ============================================================================

@pytest.fixture
def sample_local_server_row():
    """Sample database row for a local MCP server"""
    return (
        1,                      # server_id
        'filesystem',           # server_name
        'local',                # server_type
        None,                   # server_url
        None,                   # auth_type
        json.dumps({            # connection_config
            "command": "python",
            "args": ["server.py"],
            "env_vars": {"DEBUG": "1"}
        })
    )


@pytest.fixture
def sample_remote_server_row():
    """Sample database row for a remote MCP server"""
    return (
        2,                      # server_id
        'github',               # server_name
        'remote',               # server_type
        'https://api.github.com/mcp',  # server_url
        'bearer',               # auth_type
        None                    # connection_config
    )


# ============================================================================
# Flask App Fixture (for route testing)
# ============================================================================

@pytest.fixture
def flask_app():
    """Create a minimal Flask app with the MCP blueprint registered"""
    from flask import Flask
    from flask_cors import CORS

    app = Flask(__name__)
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret-key'

    CORS(app)

    # Register MCP blueprint
    from builder_mcp.routes.mcp_routes import mcp_bp
    app.register_blueprint(mcp_bp)

    return app


@pytest.fixture
def flask_client(flask_app):
    """Flask test client"""
    return flask_app.test_client()


@pytest.fixture
def auth_flask_client(flask_app):
    """Flask test client with mocked authentication"""
    with flask_app.test_request_context():
        with patch('flask_login.utils._get_user') as mock_user:
            mock_user_obj = MagicMock()
            mock_user_obj.is_authenticated = True
            mock_user.return_value = mock_user_obj

            with flask_app.test_client() as client:
                with client.session_transaction() as sess:
                    sess['user_email'] = 'test@example.com'
                    sess['_user_id'] = '1'
                yield client
