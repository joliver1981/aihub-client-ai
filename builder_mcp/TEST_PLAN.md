# MCP Integration Test Plan

**Project:** AI Hub Platform -- MCP (Model Context Protocol) Integration
**Version:** 1.0
**Last Updated:** 2026-02-09
**Status:** Active

---

## Table of Contents

1. [Test Strategy Overview](#1-test-strategy-overview)
2. [Test Categories](#2-test-categories)
3. [Test Case Matrix](#3-test-case-matrix)
   - 3a. [Protocol](#3a-protocol-protocolpy)
   - 3b. [Stdio Transport](#3b-stdio-transport-stdio_transportpy)
   - 3c. [SSE Transport](#3c-sse-transport-sse_transportpy)
   - 3d. [Server Manager](#3d-server-manager-server_managerpy)
   - 3e. [Gateway API](#3e-gateway-api-app_mcp_gatewaypy)
   - 3f. [Gateway Client](#3f-gateway-client-mcp_gateway_clientpy)
   - 3g. [Tool Converter](#3g-tool-converter-tool_converterpy)
   - 3h. [Agent Integration](#3h-agent-integration-mcp_agent_toolspy)
   - 3i. [Flask Routes](#3i-flask-routes-mcp_routespy)
   - 3j. [Management UI](#3j-management-ui-mcp_servershtml)
4. [Test Prerequisites](#4-test-prerequisites)
5. [Running Tests](#5-running-tests)
6. [Test Data](#6-test-data)
7. [Known Limitations and Future Testing](#7-known-limitations-and-future-testing)

---

## 1. Test Strategy Overview

### 1.1 Testing Pyramid

The MCP integration follows a standard testing pyramid:

```
        /  E2E  \          Few, slow, high-confidence
       /----------\
      / Integration \      Moderate count, require gateway
     /----------------\
    /    Unit Tests     \  Many, fast, isolated
   /---------------------\
```

- **Unit Tests** form the base. They run without any external services and validate individual classes and functions in isolation. All external dependencies (database, HTTP, subprocess) are mocked.
- **Integration Tests** sit in the middle. They require the MCP Gateway service to be running and validate cross-component communication (client to gateway, gateway to sample server).
- **End-to-End Tests** sit at the top. They require the gateway, database, and at least one real MCP server, validating full user workflows.

### 1.2 Tools and Frameworks

| Tool | Purpose |
|------|---------|
| `pytest` | Test runner, assertions, fixtures, parametrize |
| `pytest-asyncio` | Async test support for gateway internals (transport, server manager) |
| `unittest.mock` / `MagicMock` | Mocking DB connections, HTTP requests, subprocesses |
| `requests_mock` or `responses` | HTTP-level mocking for gateway client tests |
| `httpx` (mock) | Async HTTP mocking for SSE transport tests |
| `Flask test_client` | Flask route testing without a running server |

### 1.3 Mock Strategy

| Dependency | Mock Approach |
|------------|---------------|
| Database (pyodbc) | `MagicMock` for `get_db_connection()`, cursor, and fetchall/fetchone |
| HTTP to gateway | `unittest.mock.patch('requests.request')` or `responses` library |
| Subprocess (stdio) | `asyncio.create_subprocess_exec` patched with mock process objects |
| httpx (SSE) | `unittest.mock.patch` on `httpx.AsyncClient` |
| Flask session | `session` dict injection via test client |
| `CommonUtils` | `MagicMock` for `get_db_connection`, `get_mcp_gateway_api_base_url` |
| `encrypt` module | `MagicMock` or environment variable override |

### 1.4 Coverage Goals

| Metric | Target |
|--------|--------|
| Line coverage (unit tests) | >= 85% |
| Branch coverage (unit tests) | >= 75% |
| Protocol module | 100% line coverage |
| Tool Converter module | 100% line coverage |
| Gateway Client module | >= 90% line coverage |
| Agent Integration module | >= 85% line coverage |
| Flask Routes module | >= 80% line coverage |

---

## 2. Test Categories

### 2.1 Unit Tests (No External Dependencies)

**Location:** `builder_mcp/tests/` and `builder_mcp/gateway/tests/`
**Marker:** None (default)
**Runtime:** < 10 seconds total
**Dependencies:** Python environment with project packages installed

These tests mock all external I/O and validate:
- JSON-RPC protocol encoding/decoding
- Tool name sanitization and schema conversion
- Gateway client request construction and error handling
- Agent integration logic with mocked DB and gateway
- Flask route handlers with mocked DB and gateway client

### 2.2 Integration Tests (Require Gateway Running)

**Location:** `builder_mcp/tests/test_integration.py`
**Marker:** `@pytest.mark.skipif(not os.getenv('MCP_GATEWAY_RUNNING'), ...)`
**Runtime:** < 30 seconds total
**Dependencies:** MCP Gateway service running on configured port

These tests validate:
- Gateway client health check against a live gateway
- Test connection flow through the gateway to the sample MCP server
- Tool listing through the gateway
- Tool execution round-trip

### 2.3 End-to-End Tests (Require Gateway + DB + Sample Server)

**Location:** `builder_mcp/tests/test_e2e.py` (planned)
**Marker:** `@pytest.mark.e2e`
**Runtime:** < 60 seconds total
**Dependencies:** Gateway running, database accessible, sample server available

These tests validate:
- Creating a server in the database, connecting via gateway, listing tools, calling a tool, and deleting
- Agent assignment flow: assign server to agent, load tools for agent, verify tool availability
- Full Flask route round-trip with actual HTTP requests

### 2.4 UI Tests (Manual Checklist)

**Location:** Section 3j of this document
**Runtime:** Manual, approximately 15-20 minutes
**Dependencies:** Running application with browser access

---

## 3. Test Case Matrix

### 3a. Protocol (`protocol.py`)

**Test file:** `builder_mcp/gateway/tests/test_gateway.py` (TestMCPProtocol class)
**Additional file:** `builder_mcp/tests/test_protocol.py`

| ID | Test Case | Type | Input | Expected Outcome | Status |
|----|-----------|------|-------|-------------------|--------|
| P-01 | Create request with explicit ID | Unit | `method="tools/list", params={"cursor": None}, request_id=1` | `jsonrpc=2.0`, `id=1`, `method="tools/list"`, params present | Exists |
| P-02 | Create request with auto ID | Unit | `method="tools/list"` (no request_id) | `id` is auto-incremented integer > 0 | Planned |
| P-03 | Create request without params | Unit | `method="ping", params=None` | No `params` key in output dict | Planned |
| P-04 | Create notification | Unit | `method="notifications/initialized"` | No `id` key, `method` set, `jsonrpc=2.0` | Exists |
| P-05 | Create notification with params | Unit | `method="progress", params={"pct": 50}` | `params` present, no `id` | Planned |
| P-06 | Create initialize request (defaults) | Unit | No arguments | `method="initialize"`, `clientInfo.name="AIHub"`, `protocolVersion="2024-11-05"` | Exists |
| P-07 | Create initialize request (custom) | Unit | `client_name="Custom", client_version="2.0"` | `clientInfo.name="Custom"`, `clientInfo.version="2.0"` | Exists |
| P-08 | Create initialized notification | Unit | None | `method="notifications/initialized"`, no `id` | Planned |
| P-09 | Create list tools request | Unit | None | `method="tools/list"`, `params={}` | Exists |
| P-10 | Create call tool request | Unit | `tool_name="echo", arguments={"message":"hi"}` | `method="tools/call"`, `params.name="echo"`, `params.arguments.message="hi"` | Exists |
| P-11 | Parse success response | Unit | `{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}` | `error=False`, `result={"tools":[]}` | Exists |
| P-12 | Parse error response | Unit | `{"jsonrpc":"2.0","id":1,"error":{"code":-32600,"message":"Invalid"}}` | `error=True`, `error_code=-32600` | Exists |
| P-13 | Parse error with data field | Unit | Error response with `data` key | `error_data` populated | Planned |
| P-14 | Parse response missing result and error | Unit | `{"jsonrpc":"2.0","id":1}` | `error=True`, `error_message` contains "Invalid JSON-RPC", `raw` present | Planned |
| P-15 | Encode message | Unit | `{"jsonrpc":"2.0","id":1,"method":"test"}` | bytes, ends with `\n`, valid JSON when decoded | Exists |
| P-16 | Decode valid message | Unit | `'{"jsonrpc":"2.0","id":1,"result":{}}'` | dict with `id=1` | Exists |
| P-17 | Decode empty string | Unit | `""` | `None` | Exists |
| P-18 | Decode whitespace-only string | Unit | `"   "` | `None` | Exists |
| P-19 | Decode invalid JSON | Unit | `"not json"` | `None` | Exists |
| P-20 | Encode/decode roundtrip | Unit | Any valid message dict | Encode then decode returns original dict | Planned |
| P-21 | Request counter increments | Unit | Two sequential `create_request` calls without explicit ID | Second ID > first ID | Planned |

### 3b. Stdio Transport (`stdio_transport.py`)

**Test file:** `builder_mcp/tests/test_stdio_transport.py` (planned)

| ID | Test Case | Type | Input | Expected Outcome | Status |
|----|-----------|------|-------|-------------------|--------|
| ST-01 | Start subprocess and MCP init handshake | Integration | `command=sys.executable, args=[sample_server]` | `is_connected=True`, `_server_capabilities` populated | Covered via ServerManager |
| ST-02 | Send request and receive response | Integration | `send_request("tools/list", {})` | Returns parsed response with tools | Covered via ServerManager |
| ST-03 | Command not found raises FileNotFoundError | Unit | `command="nonexistent_xyz"` | `FileNotFoundError` raised | Planned |
| ST-04 | Windows .cmd fallback for npx | Unit | Mock `sys.platform='win32'`, `command="npx"` | First tries `npx.cmd`, then falls back to `npx` | Planned |
| ST-05 | Windows .cmd fallback for node | Unit | Mock `sys.platform='win32'`, `command="node"` | First tries `node.cmd` | Planned |
| ST-06 | No .cmd fallback for non-npm commands on Windows | Unit | Mock `sys.platform='win32'`, `command="python"` | Uses `python` directly (no `.cmd` variant for start) | Planned |
| ST-07 | Init timeout raises TimeoutError | Unit | Mock slow process, `timeout=1` | `TimeoutError` with message containing timeout value | Planned |
| ST-08 | Init error response raises ConnectionError | Unit | Mock process returns error on initialize | `ConnectionError` raised | Planned |
| ST-09 | Send request when not connected | Unit | Call `send_request` before `start` | `ConnectionError` raised | Planned |
| ST-10 | Request timeout raises TimeoutError | Unit | Mock stalled response, `timeout=1` | `TimeoutError` with method name in message | Planned |
| ST-11 | Close terminates process gracefully | Unit | Start then close | `process=None`, `_connected=False`, `_pending_responses` empty | Planned |
| ST-12 | Close kills process after terminate timeout | Unit | Mock process that ignores SIGTERM | `process.kill()` called after 5s timeout | Planned |
| ST-13 | Close cancels pending request futures | Unit | Pending future exists when close called | Future receives `ConnectionError` | Planned |
| ST-14 | Read loop handles server notifications | Unit | Mock server sends notification (no id) | Notification logged, no error | Planned |
| ST-15 | Read loop sets error on all futures on exception | Unit | Mock read loop crash | All pending futures get `ConnectionError` | Planned |
| ST-16 | Environment variables passed to subprocess | Unit | `env_vars={"FOO":"bar"}` | `os.environ` copy with `FOO=bar` passed to subprocess | Planned |
| ST-17 | `is_connected` reflects process state | Unit | Various process states | `True` when connected and running, `False` otherwise | Planned |
| ST-18 | `send_request_raw` with missing id | Unit | Request dict without `id` key | `ValueError` raised | Planned |

### 3c. SSE Transport (`sse_transport.py`)

**Test file:** `builder_mcp/tests/test_sse_transport.py` (planned)

| ID | Test Case | Type | Input | Expected Outcome | Status |
|----|-----------|------|-------|-------------------|--------|
| SSE-01 | Connect via SSE with endpoint discovery | Unit | Mock SSE stream with `event:endpoint` | `_message_endpoint` set, `_connected=True` | Planned |
| SSE-02 | SSE endpoint as relative URL | Unit | Endpoint event returns `/messages` | `_message_endpoint` = `base_url + /messages` | Planned |
| SSE-03 | SSE endpoint as absolute URL | Unit | Endpoint event returns `https://other.com/msg` | `_message_endpoint` = `https://other.com/msg` | Planned |
| SSE-04 | HTTP POST fallback on SSE failure | Unit | SSE connection raises exception | Falls back to `_connect_http_fallback`, `_message_endpoint` = base URL | Planned |
| SSE-05 | SSE endpoint timeout | Unit | No endpoint event within timeout | `TimeoutError` raised, SSE task cancelled | Planned |
| SSE-06 | Auth headers passed to httpx client | Unit | `auth_headers={"Authorization":"Bearer abc"}` | `httpx.AsyncClient` created with auth header | Planned |
| SSE-07 | Send request in SSE mode | Unit | Connected via SSE, send request | POST to message endpoint, response via SSE | Planned |
| SSE-08 | Send request in HTTP fallback mode | Unit | Connected via fallback, send request | POST to base URL, response from HTTP body | Planned |
| SSE-09 | Send notification (no response expected) | Unit | Send notification after connect | POST sent, no error on failure | Planned |
| SSE-10 | Close disconnects cleanly | Unit | Connected, then close | `_connected=False`, SSE task cancelled, client closed | Planned |
| SSE-11 | Close cancels pending futures | Unit | Pending response futures exist | Futures receive `ConnectionError` | Planned |
| SSE-12 | httpx not installed raises ImportError | Unit | Mock `HTTPX_AVAILABLE=False` | `ImportError` with install instructions | Planned |
| SSE-13 | Initialize failure raises ConnectionError | Unit | Mock server returns error on initialize | `ConnectionError` raised | Planned |
| SSE-14 | Request when not connected | Unit | Call `send_request` before connect | `ConnectionError` raised | Planned |
| SSE-15 | Request timeout | Unit | Mock stalled response | `TimeoutError` with method name | Planned |
| SSE-16 | SSE reader handles invalid JSON | Unit | SSE stream contains non-JSON data line | Warning logged, no crash | Planned |

### 3d. Server Manager (`server_manager.py`)

**Test file:** `builder_mcp/gateway/tests/test_gateway.py` (TestServerManager class)

| ID | Test Case | Type | Input | Expected Outcome | Status |
|----|-----------|------|-------|-------------------|--------|
| SM-01 | Connect local server | Integration | Sample server config | `status="connected"`, `tool_count=3`, tools listed | Exists |
| SM-02 | Connect remote server | Unit | Mock SSETransport | `status="connected"`, transport is SSETransport | Planned |
| SM-03 | Connect unknown server type | Unit | `type="unknown"` | `status="error"`, error message contains type | Planned |
| SM-04 | Reconnect replaces existing connection | Integration | Connect same server_id twice | First connection cleaned up, second active | Planned |
| SM-05 | Disconnect connected server | Integration | Connect then disconnect | `status="disconnected"`, connection removed | Exists |
| SM-06 | Disconnect non-connected server | Unit | Disconnect unknown server_id | `status="not_connected"` | Planned |
| SM-07 | List tools from connected server | Integration | Connected server | Returns 3 tools with name, description, inputSchema | Exists |
| SM-08 | List tools uses cache | Unit | Call list_tools twice within TTL | Second call returns cached result, no transport call | Planned |
| SM-09 | List tools refreshes expired cache | Unit | Set cache timestamp to past | Transport `send_request` called again | Planned |
| SM-10 | List tools from disconnected server | Unit | Unknown server_id | `ConnectionError` raised | Planned |
| SM-11 | Call tool success | Integration | `echo` tool with message | `status="success"`, result contains message | Exists |
| SM-12 | Call tool server returns error | Integration | Call unknown tool name | `status="error"` | Planned |
| SM-13 | Call tool timeout | Unit | Mock slow transport | `status="error"`, error mentions timeout | Planned |
| SM-14 | Call tool on disconnected server | Unit | Unknown server_id | `status="error"`, error mentions not connected | Exists |
| SM-15 | Call tool with isError=True in result | Unit | Mock transport returns `isError: true` | `status="error"`, error text from content | Planned |
| SM-16 | Call tool with image content type | Unit | Mock result with `type="image"` content | Result includes `[Image: mime/type]` | Planned |
| SM-17 | Test connection (ephemeral) | Integration | Sample server config | `status="success"`, `tool_count=3`, no residual connections | Exists |
| SM-18 | Test connection failure | Unit | Invalid config | `status="error"`, connection cleaned up | Planned |
| SM-19 | Get status for connected server | Integration | Connected server | `status="connected"`, `tool_count=3` | Exists |
| SM-20 | Get status for unknown server | Unit | Unknown server_id | `status="disconnected"` | Planned |
| SM-21 | Get all connections | Unit | Multiple connected servers | Dict with all server statuses | Planned |
| SM-22 | Cleanup all connections | Integration | Multiple connected servers | All connections removed | Planned |
| SM-23 | Connect with FileNotFoundError | Unit | Bad command | `status="error"`, error mentions command not found | Exists |
| SM-24 | Connect with TimeoutError | Unit | Mock timeout in transport | `status="error"`, connection cleaned up | Planned |
| SM-25 | Concurrent connections (different IDs) | Integration | Connect 3 servers simultaneously | All 3 connected independently | Planned |
| SM-26 | Per-server locking | Unit | Concurrent operations on same server_id | Operations serialized via lock | Planned |

### 3e. Gateway API (`app_mcp_gateway.py`)

**Test file:** `builder_mcp/gateway/tests/test_api.py` (planned, uses `httpx.AsyncClient` with FastAPI TestClient)

| ID | Test Case | Type | Input | Expected Outcome | Status |
|----|-----------|------|-------|-------------------|--------|
| GA-01 | Health endpoint | Unit | `GET /health` | 200, `status="ok"`, `service="mcp-gateway"`, timestamp present | Planned |
| GA-02 | Health endpoint shows connection counts | Unit | Mock manager with connections | `active_connections` and `total_connections` correct | Planned |
| GA-03 | Connect local server | Unit | `POST /api/mcp/connect` with local config | 200, `status="connected"` | Planned |
| GA-04 | Connect remote server | Unit | `POST /api/mcp/connect` with remote config | 200, config forwarded with `url` and `auth_headers` | Planned |
| GA-05 | Connect server failure | Unit | Mock manager returns error | 200 (error in body), `status="error"` | Planned |
| GA-06 | Disconnect server | Unit | `POST /api/mcp/disconnect` with server_id | 200, `status="disconnected"` | Planned |
| GA-07 | Get server status | Unit | `GET /api/mcp/servers/{id}/status` | 200, status dict returned | Planned |
| GA-08 | List server tools | Unit | `GET /api/mcp/servers/{id}/tools` | 200, `tools` array, `tool_count` | Planned |
| GA-09 | List tools for disconnected server | Unit | Mock `ConnectionError` | 404 with detail message | Planned |
| GA-10 | List tools internal error | Unit | Mock generic exception | 500 with detail | Planned |
| GA-11 | Call tool | Unit | `POST /api/mcp/servers/{id}/tools/call` | 200, tool result returned | Planned |
| GA-12 | Test server config | Unit | `POST /api/mcp/test` with config | 200, `status="success"`, `tool_count`, `tools` | Planned |
| GA-13 | List all connections | Unit | `GET /api/mcp/connections` | 200, dict of connections | Planned |
| GA-14 | Pydantic validation error | Unit | POST with missing required fields | 422, validation error details | Planned |
| GA-15 | CORS headers present | Unit | Any request with Origin header | `Access-Control-Allow-Origin: *` in response | Planned |

### 3f. Gateway Client (`mcp_gateway_client.py`)

**Test file:** `builder_mcp/tests/test_gateway_client.py`

| ID | Test Case | Type | Input | Expected Outcome | Status |
|----|-----------|------|-------|-------------------|--------|
| GC-01 | Init with default URL | Unit | No arguments | `base_url` from env/CommonUtils | Planned |
| GC-02 | Init with custom URL | Unit | `base_url="http://custom:9999"` | `base_url` set to custom value | Planned |
| GC-03 | Init with custom timeout and retries | Unit | `timeout=60, max_retries=5` | Fields set, retry adapter configured with 5 retries | Planned |
| GC-04 | Health check success | Unit | Mock `GET /health` returns `{"status":"ok"}` | Returns `True` | Planned |
| GC-05 | Health check failure (non-ok status) | Unit | Mock returns `{"status":"error"}` | Returns `False` | Planned |
| GC-06 | Health check connection error | Unit | Mock raises `ConnectionError` | Returns `False` (no exception) | Planned |
| GC-07 | Test server | Unit | Mock `POST /api/mcp/test` returns success | Returns `{"status":"success", "tool_count":3}` | Planned |
| GC-08 | Connect server | Unit | `server_id=42, config={...}` | POST body includes `server_id="42"` plus config fields | Planned |
| GC-09 | Disconnect server | Unit | `server_id=42` | POST body is `{"server_id":"42"}` | Planned |
| GC-10 | List tools | Unit | `server_id=42` | GET to `/api/mcp/servers/42/tools`, returns `tools` list | Planned |
| GC-11 | Call tool | Unit | `server_id=42, tool_name="echo", arguments={...}` | POST with correct body, timeout=60 | Planned |
| GC-12 | Get server status | Unit | `server_id=42` | GET to `/api/mcp/servers/42/status` | Planned |
| GC-13 | Get all connections | Unit | None | GET to `/api/mcp/connections` | Planned |
| GC-14 | Timeout handling | Unit | Mock raises `requests.Timeout` | Exception with timeout message | Planned |
| GC-15 | Connection error handling | Unit | Mock raises `requests.ConnectionError` | Exception with "Could not connect" message | Planned |
| GC-16 | HTTP error handling | Unit | Mock returns 500 | Exception with request failure message | Planned |
| GC-17 | Connection: close header | Unit | Any request | `Connection: close` header present in request | Planned |
| GC-18 | Retry adapter configuration | Unit | `max_retries=3` | HTTPAdapter mounted with retry on 429, 500, 502, 503, 504 | Planned |
| GC-19 | URL construction with trailing slash | Unit | `base_url="http://host:5071/"`, endpoint="/health" | Correct URL without double slashes | Planned |

### 3g. Tool Converter (`tool_converter.py`)

**Test file:** `builder_mcp/tests/test_tool_converter.py` and `builder_mcp/tests/test_integration.py` (TestToolConverter)

| ID | Test Case | Type | Input | Expected Outcome | Status |
|----|-----------|------|-------|-------------------|--------|
| TC-01 | Sanitize name: hyphens to underscores | Unit | `"my-server"` | `"my_server"` | Exists (partial) |
| TC-02 | Sanitize name: spaces to underscores | Unit | `"my server"` | `"my_server"` | Planned |
| TC-03 | Sanitize name: dots to underscores | Unit | `"my.server"` | `"my_server"` | Planned |
| TC-04 | Sanitize name: strip special chars | Unit | `"my@server!"` | `"myserver"` | Planned |
| TC-05 | Sanitize name: strip leading digits | Unit | `"123abc"` | `"abc"` | Exists |
| TC-06 | Sanitize name: strip leading underscores | Unit | `"__name"` | `"name"` | Planned |
| TC-07 | Sanitize name: lowercase | Unit | `"MyServer"` | `"myserver"` | Planned |
| TC-08 | Sanitize name: all invalid chars | Unit | `"---"` | `"mcp_server"` (fallback) | Exists |
| TC-09 | Sanitize name: complex mixed | Unit | `"My Server-Name.v2"` | `"my_server_name_v2"` | Exists |
| TC-10 | Sanitize name: empty string | Unit | `""` | `"mcp_server"` | Planned |
| TC-11 | Sanitize name: only digits | Unit | `"12345"` | `"mcp_server"` | Planned |
| TC-12 | JSON Schema type: string | Unit | `{"type":"string"}` | `str` | Planned |
| TC-13 | JSON Schema type: number | Unit | `{"type":"number"}` | `float` | Planned |
| TC-14 | JSON Schema type: integer | Unit | `{"type":"integer"}` | `int` | Planned |
| TC-15 | JSON Schema type: boolean | Unit | `{"type":"boolean"}` | `bool` | Planned |
| TC-16 | JSON Schema type: array | Unit | `{"type":"array"}` | `list` | Planned |
| TC-17 | JSON Schema type: object | Unit | `{"type":"object"}` | `dict` | Planned |
| TC-18 | JSON Schema type: unknown defaults to str | Unit | `{"type":"custom"}` | `str` | Planned |
| TC-19 | JSON Schema type: missing type defaults to str | Unit | `{}` | `str` | Planned |
| TC-20 | Pydantic model: required fields | Unit | Schema with `required: ["path"]` | Field `path` has no default | Exists (partial) |
| TC-21 | Pydantic model: optional fields | Unit | Schema with field not in required | Field has `default=None`, type is `Optional` | Exists |
| TC-22 | Pydantic model: all JSON types | Unit | Properties with every type | Model created with correct Python types | Planned |
| TC-23 | Pydantic model: empty properties | Unit | `{"type":"object","properties":{}}` | Model with `__placeholder__` field | Planned |
| TC-24 | Pydantic model: field descriptions | Unit | Properties with `description` key | Pydantic `Field` has matching description | Planned |
| TC-25 | Pydantic model: creation failure fallback | Unit | Mock `create_model` raises exception | Fallback model with single `input` field | Planned |
| TC-26 | Convert tool: name prefix | Unit | `server_name="test", tool.name="echo"` | `tool.name = "test_echo"` | Exists |
| TC-27 | Convert tool: description preserved | Unit | `description="Echo a message"` | `tool.description = "Echo a message"` | Exists |
| TC-28 | Convert tool: default description | Unit | No description in MCP tool | Description contains tool name | Planned |
| TC-29 | Convert tool: callable invokes gateway | Unit | Call `tool.func(message="hi")` | `gateway.call_tool` called with original name and kwargs | Exists |
| TC-30 | Convert tool: gateway success result | Unit | Gateway returns `status=success, result="data"` | Tool returns `"data"` | Planned |
| TC-31 | Convert tool: gateway error result | Unit | Gateway returns `status=error, error="fail"` | Tool returns string containing "Error" and "fail" | Planned |
| TC-32 | Convert tool: gateway exception | Unit | Gateway raises `Exception("Connection lost")` | Tool returns string containing "Error" and "Connection lost" | Exists |
| TC-33 | Convert tool: overall conversion exception | Unit | Tool def causes exception in convert_tool | Returns `None` | Planned |
| TC-34 | Convert all tools: multiple tools | Unit | List of 2 tool defs | Returns list of 2 StructuredTool objects | Exists |
| TC-35 | Convert all tools: skip failed conversions | Unit | One valid, one invalid tool def | Returns list of 1 (skips None) | Planned |
| TC-36 | Convert all tools: empty list | Unit | `[]` | Returns `[]` | Planned |

### 3h. Agent Integration (`mcp_agent_tools.py`)

**Test file:** `builder_mcp/tests/test_agent_integration.py`

| ID | Test Case | Type | Input | Expected Outcome | Status |
|----|-----------|------|-------|-------------------|--------|
| AI-01 | Gateway health check fails | Unit | Mock `health_check()` returns `False` | Returns empty list, no DB queries | Planned |
| AI-02 | No servers assigned to agent | Unit | Mock DB returns empty resultset | Returns empty list | Planned |
| AI-03 | Server connect failure: skip and continue | Unit | Mock gateway connect returns error | Skipped, other servers still processed | Planned |
| AI-04 | Successful tool loading | Unit | Mock: 1 server, gateway connects, 2 tools | Returns 2 LangChain StructuredTool objects | Planned |
| AI-05 | Multiple servers, partial failure | Unit | 2 servers, first fails connect, second succeeds | Returns tools from second server only | Planned |
| AI-06 | Tools from connect response used | Unit | `connect_result` includes `tools` list | No separate `list_tools` call made | Planned |
| AI-07 | Tools fetched via list_tools when not in connect response | Unit | `connect_result` has empty `tools` | `gateway.list_tools` called | Planned |
| AI-08 | ImportError returns empty list | Unit | Mock `ImportError` on client import | Returns `[]`, no crash | Planned |
| AI-09 | Generic exception returns empty list | Unit | Mock DB throws exception | Returns `[]`, logged as warning | Planned |
| AI-10 | Per-server exception skips server | Unit | One server throws during conversion | Other servers still processed | Planned |
| AI-11 | System prompt: no servers | Unit | Mock DB returns empty resultset | Returns `""` | Planned |
| AI-12 | System prompt: single server with description | Unit | 1 server with name and description | Returns markdown with server listed as `- name -- desc` | Planned |
| AI-13 | System prompt: multiple servers | Unit | 3 servers assigned | All listed in prompt text | Planned |
| AI-14 | System prompt: server without description | Unit | Server with `None` description | Listed without ` -- ` suffix | Planned |
| AI-15 | System prompt: DB error | Unit | Mock DB exception | Returns `""` | Planned |
| AI-16 | Build config: local server | Unit | `server_type="local"`, valid JSON config | `{"type":"local","command":"...","args":[...],"env_vars":{...}}` | Planned |
| AI-17 | Build config: local with invalid JSON | Unit | `connection_config_json="not json"` | Returns config with empty command/args/env | Planned |
| AI-18 | Build config: local with None JSON | Unit | `connection_config_json=None` | Returns config with empty defaults | Planned |
| AI-19 | Build config: remote server | Unit | `server_type="remote"`, `server_url="https://..."` | `{"type":"remote","url":"https://...","auth_headers":{}}` | Planned |
| AI-20 | Build config: unknown type | Unit | `server_type="custom"` | Returns `{"type":"custom"}` | Planned |
| AI-21 | Auth headers: bearer token | Unit | `auth_type="bearer"`, creds with `token` | `{"Authorization":"Bearer <token>"}` | Planned |
| AI-22 | Auth headers: API key (default header) | Unit | `auth_type="apikey"`, creds with `key` | `{"X-API-Key":"<key>"}` | Planned |
| AI-23 | Auth headers: API key (custom header) | Unit | `auth_type="apikey"`, creds with `header` and `key` | `{"<custom_header>":"<key>"}` | Planned |
| AI-24 | Auth headers: basic auth | Unit | `auth_type="basic"`, creds with `username` and `password` | `{"Authorization":"Basic <base64>"}` | Planned |
| AI-25 | Auth headers: custom headers | Unit | `auth_type="custom"`, multiple key-value creds | All creds returned as headers | Planned |
| AI-26 | Auth headers: none | Unit | `auth_type="none"` | `{}` | Planned |
| AI-27 | Auth headers: null auth_type | Unit | `auth_type=None` | `{}` | Planned |
| AI-28 | Auth headers: DB error | Unit | Mock DB exception | Returns `{}`, logged | Planned |
| AI-29 | Auth headers: decryption failure | Unit | `credential_value` is `None` after decrypt | Credential skipped | Planned |
| AI-30 | Tenant context set on DB queries | Unit | Any DB query | `sp_setTenantContext` called with `API_KEY` env var | Planned |

### 3i. Flask Routes (`mcp_routes.py`)

**Test file:** `builder_mcp/tests/test_routes.py` (planned)

| ID | Test Case | Type | Input | Expected Outcome | Status |
|----|-----------|------|-------|-------------------|--------|
| FR-01 | List servers | Unit | `GET /api/mcp/servers` | 200, JSON array of server objects | Planned |
| FR-02 | List servers: local server has parsed config | Unit | Local server with `connection_config` JSON | Response includes `command` and `args` fields | Planned |
| FR-03 | List servers: DB error | Unit | Mock DB exception | 500, `{"error":"..."}` | Planned |
| FR-04 | Create local server | Unit | `POST /api/mcp/servers` with local config | 200, `server_id` returned, `connection_config` stored as JSON | Planned |
| FR-05 | Create remote server | Unit | `POST /api/mcp/servers` with remote config | 200, credentials encrypted and stored | Planned |
| FR-06 | Create server: DB error | Unit | Mock DB exception | 500, `{"status":"error"}` | Planned |
| FR-07 | Get server by ID | Unit | `GET /api/mcp/servers/1` | 200, server object with all fields | Planned |
| FR-08 | Get server: not found | Unit | `GET /api/mcp/servers/999` | 404, `{"error":"Server not found"}` | Planned |
| FR-09 | Get local server: config parsed | Unit | Local server with connection_config | Response includes `command`, `args`, `env_vars` | Planned |
| FR-10 | Get remote server: credential keys included | Unit | Remote server with credentials | Response includes `credential_keys` array | Planned |
| FR-11 | Update local server | Unit | `PUT /api/mcp/servers/1` with updated fields | 200, DB updated | Planned |
| FR-12 | Update remote server with new credentials | Unit | `PUT` with `auth_config` | Old creds deleted, new creds inserted encrypted | Planned |
| FR-13 | Update server: not found | Unit | `PUT /api/mcp/servers/999` | 404 | Planned |
| FR-14 | Delete server | Unit | `DELETE /api/mcp/servers/1` | 200, related records (AgentMCPServers, Credentials) deleted first | Planned |
| FR-15 | Delete server: not found | Unit | `DELETE /api/mcp/servers/999` | 404 | Planned |
| FR-16 | Delete server: gateway disconnect best-effort | Unit | Mock gateway disconnect fails | 200 still returned | Planned |
| FR-17 | Test config (pre-save) | Unit | `POST /api/mcp/test` with config | 200, proxied to gateway | Planned |
| FR-18 | Test server by ID | Unit | `POST /api/mcp/servers/1/test` | 200, config built from DB, proxied to gateway, test status updated | Planned |
| FR-19 | Test server by ID: not found | Unit | `POST /api/mcp/servers/999/test` | 404 | Planned |
| FR-20 | List tools: server already connected | Unit | `GET /api/mcp/servers/1/tools`, mock status="connected" | 200, tools returned from gateway | Planned |
| FR-21 | List tools: server not connected, auto-connect | Unit | Mock status="disconnected" | Server connected from DB config, then tools listed | Planned |
| FR-22 | List tools: server not found in DB | Unit | Unknown server_id, not connected | 404 | Planned |
| FR-23 | List tools: connect failure | Unit | Auto-connect returns error | 500 with error | Planned |
| FR-24 | Call tool | Unit | `POST /api/mcp/servers/1/tools/call` | 200, result from gateway | Planned |
| FR-25 | Call tool: missing tool_name | Unit | POST without `tool_name` | 400, `"tool_name is required"` | Planned |
| FR-26 | Get server agents | Unit | `GET /api/mcp/servers/1/agents` | 200, array of agent assignments | Planned |
| FR-27 | Update server agents | Unit | `POST /api/mcp/servers/1/agents` with `agent_ids` | 200, old assignments deleted, new inserted | Planned |
| FR-28 | Update server agents: server not found | Unit | `POST /api/mcp/servers/999/agents` | 404 | Planned |
| FR-29 | Server directory | Unit | `GET /api/mcp/directory` | 200, array of directory templates (Salesforce, SAP, etc.) | Planned |
| FR-30 | Gateway health proxy | Unit | `GET /api/mcp/gateway/health` | 200, `status="ok"` or `"unavailable"` | Planned |
| FR-31 | Gateway health: exception | Unit | Mock gateway client raises | 500 | Planned |
| FR-32 | Auth required: unauthenticated request | Unit | Request without login | 401 or redirect | Planned |
| FR-33 | Tenant context isolation | Unit | Verify `sp_setTenantContext` called | Called with `API_KEY` env var on every DB operation | Planned |
| FR-34 | Update test status helper | Unit | Call `_update_test_status(1, {"status":"success","tool_count":3})` | DB updated with status and tool count | Planned |

### 3j. Management UI (`mcp_servers.html`)

**Manual test checklist.** Execute in a browser with the full application running.

#### Page Load
- [ ] Page loads without JavaScript errors
- [ ] Gateway health indicator shows correct status (green for online, red for offline)
- [ ] Server list loads and displays all configured servers
- [ ] Server cards show correct type icon (local vs. remote)
- [ ] Agent count badges display on server cards

#### Add Local Server
- [ ] "Add Server" button opens modal
- [ ] "Local Server" tab is active by default
- [ ] Required fields are enforced (server name, command)
- [ ] Args field accepts comma-separated or JSON array format
- [ ] Environment variables field accepts key=value format
- [ ] Description and category fields are optional
- [ ] Submit creates server and refreshes list
- [ ] Toast notification shows success message
- [ ] Cancel closes modal without changes

#### Add Remote Server
- [ ] "Remote Server" tab switches form fields
- [ ] URL field is required
- [ ] Auth type dropdown shows options: none, bearer, apikey, basic, custom
- [ ] Selecting "bearer" shows token field
- [ ] Selecting "apikey" shows header name and key fields
- [ ] Selecting "basic" shows username and password fields
- [ ] Selecting "custom" shows custom headers field
- [ ] Submit creates server with encrypted credentials
- [ ] Toast notification shows success message

#### Test Connection
- [ ] "Test" button appears on each server card
- [ ] Clicking "Test" shows loading spinner
- [ ] Successful test shows green toast with tool count
- [ ] Failed test shows red toast with error message
- [ ] Test updates `last_tested_date` and `last_test_status` in card

#### Tool Browser
- [ ] "Tools" button opens tool browser modal
- [ ] Modal shows list of tools with names and descriptions
- [ ] Each tool shows expandable input schema
- [ ] "Try it" button allows entering arguments and executing tool
- [ ] Tool execution result displays in modal
- [ ] Tool execution error displays appropriately

#### Agent Assignment
- [ ] "Agents" button opens assignment modal
- [ ] Modal lists all available agents with checkboxes
- [ ] Currently assigned agents are pre-checked
- [ ] Saving updates assignments
- [ ] Toast notification confirms save

#### Server Directory
- [ ] "Directory" button opens directory modal
- [ ] Directory shows available server templates
- [ ] Clicking a template pre-fills the add server form
- [ ] Categories are displayed and filterable

#### Edit / Delete Server
- [ ] "Edit" button opens modal with pre-filled fields
- [ ] Editing and saving updates the server
- [ ] "Delete" button shows confirmation dialog
- [ ] Confirming delete removes the server from list
- [ ] Delete disconnects from gateway (best effort)

#### Error Handling
- [ ] Gateway offline: appropriate warning displayed on page
- [ ] Network error: toast notification with retry option
- [ ] Session expired: redirects to login

---

## 4. Test Prerequisites

### 4.1 Unit Tests

| Requirement | Details |
|-------------|---------|
| Python | 3.9+ |
| Virtual environment | With project dependencies installed |
| Packages | `pytest`, `pytest-asyncio`, `pydantic`, `langchain`, `requests` |
| No external services | All dependencies mocked |

### 4.2 Integration Tests

| Requirement | Details |
|-------------|---------|
| All unit test prerequisites | -- |
| MCP Gateway running | `python builder_mcp/gateway/app_mcp_gateway.py` on port 5071 |
| Environment variable | `MCP_GATEWAY_RUNNING=1` |
| Sample MCP server available | `builder_mcp/gateway/tests/sample_mcp_server.py` (used automatically) |

### 4.3 End-to-End Tests

| Requirement | Details |
|-------------|---------|
| All integration test prerequisites | -- |
| Database accessible | SQL Server with MCP tables created |
| Environment variables | `API_KEY`, `DB_CONNECTION_STRING` (or equivalent) |
| Tables exist | `MCPServers`, `MCPServerCredentials`, `AgentMCPServers`, `UserMCPServers` |

### 4.4 UI Tests

| Requirement | Details |
|-------------|---------|
| Full application running | Flask app + MCP Gateway |
| Browser | Chrome 120+ or Firefox 120+ |
| Authenticated session | Logged in with valid user |
| At least one configured MCP server | For test/tools/assignment flows |

---

## 5. Running Tests

### 5.1 Unit Tests Only

```bash
# Run all unit tests (no external services needed)
python -m pytest builder_mcp/tests/ -v --ignore=builder_mcp/tests/test_integration.py

# Run gateway-specific unit tests
python -m pytest builder_mcp/gateway/tests/test_gateway.py -v -k "TestMCPProtocol"

# Run tool converter tests
python -m pytest builder_mcp/tests/test_tool_converter.py -v

# Run with coverage report
python -m pytest builder_mcp/tests/ builder_mcp/gateway/tests/ -v --cov=builder_mcp --cov-report=html --ignore=builder_mcp/tests/test_integration.py
```

### 5.2 Integration Tests

```bash
# Start the MCP Gateway first (in a separate terminal)
cd builder_mcp/gateway
python app_mcp_gateway.py

# Run integration tests
MCP_GATEWAY_RUNNING=1 python -m pytest builder_mcp/tests/test_integration.py -v

# Run gateway server manager tests (uses sample_mcp_server.py subprocess)
python -m pytest builder_mcp/gateway/tests/test_gateway.py -v -k "TestServerManager"
```

### 5.3 All Tests

```bash
# Run everything (unit + integration if gateway is available)
MCP_GATEWAY_RUNNING=1 python -m pytest builder_mcp/ -v --tb=short

# Run with parallel execution (requires pytest-xdist)
MCP_GATEWAY_RUNNING=1 python -m pytest builder_mcp/ -v -n auto
```

### 5.4 Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `MCP_GATEWAY_RUNNING` | (unset) | Set to `1` to enable integration tests |
| `MCP_GATEWAY_URL` | `http://localhost:5071` | Gateway base URL for client |
| `MCP_GATEWAY_PORT` | `5071` | Port for the gateway service |
| `MCP_CONNECT_TIMEOUT` | `30` | Connection timeout in seconds |
| `MCP_TOOL_CALL_TIMEOUT` | `60` | Tool call timeout in seconds |
| `MCP_TOOL_CACHE_TTL` | `300` | Tool cache TTL in seconds |
| `API_KEY` | (required for DB) | Tenant API key for DB context |
| `MCP_ENCRYPTION_KEY` | (from encrypt module) | Key for credential encryption |

### 5.5 CI/CD Integration Notes

- **Unit tests** should run on every commit/PR. They require no external services.
- **Integration tests** should run on merge to main or in nightly builds. They require a gateway process to be started as a pre-test step.
- **End-to-end tests** should run in a staging environment with database access.
- The `MCP_GATEWAY_RUNNING` skip-if guard ensures integration tests do not fail in CI environments where the gateway is not available.
- For CI, start the gateway as a background process:
  ```bash
  cd builder_mcp/gateway && python app_mcp_gateway.py &
  sleep 5  # Wait for startup
  export MCP_GATEWAY_RUNNING=1
  python -m pytest builder_mcp/ -v
  ```
- On Windows CI (e.g., Azure DevOps), use `start /B` instead of `&` for background process launch. The `WindowsProactorEventLoopPolicy` in the gateway handles subprocess support.

---

## 6. Test Data

### 6.1 Sample MCP Tool Definitions

```python
SAMPLE_ECHO_TOOL = {
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
}

SAMPLE_ADD_TOOL = {
    "name": "add_numbers",
    "description": "Add two numbers together and return the sum",
    "inputSchema": {
        "type": "object",
        "properties": {
            "a": {"type": "number", "description": "First number"},
            "b": {"type": "number", "description": "Second number"}
        },
        "required": ["a", "b"]
    }
}

SAMPLE_TIME_TOOL = {
    "name": "get_current_time",
    "description": "Get the current date and time",
    "inputSchema": {
        "type": "object",
        "properties": {},
        "required": []
    }
}

SAMPLE_COMPLEX_TOOL = {
    "name": "search_documents",
    "description": "Search documents with filters",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "limit": {"type": "integer", "description": "Max results"},
            "include_metadata": {"type": "boolean", "description": "Include metadata"},
            "tags": {"type": "array", "description": "Filter by tags"},
            "options": {"type": "object", "description": "Additional options"}
        },
        "required": ["query"]
    }
}
```

### 6.2 Sample Server Configurations

```python
SAMPLE_LOCAL_CONFIG = {
    "type": "local",
    "command": "python",
    "args": ["path/to/sample_mcp_server.py"],
    "env_vars": {}
}

SAMPLE_REMOTE_CONFIG = {
    "type": "remote",
    "url": "https://mcp.example.com/v1",
    "auth_headers": {
        "Authorization": "Bearer test-token-12345"
    }
}

SAMPLE_REMOTE_APIKEY_CONFIG = {
    "type": "remote",
    "url": "https://mcp.example.com/v1",
    "auth_headers": {
        "X-API-Key": "key-abc-123"
    }
}
```

### 6.3 Mock Database Rows

```python
# MCPServers table row (tuple from cursor.fetchone)
MOCK_LOCAL_SERVER_ROW = (
    1,                          # server_id
    "Sample Local Server",      # server_name
    "local",                    # server_type
    None,                       # server_url
    None,                       # auth_type
    '{"command":"python","args":["server.py"],"env_vars":{}}',  # connection_config
    "A test server",            # description
    "Development",              # category
    "terminal",                 # icon
    1,                          # enabled
    "admin@example.com",        # created_by
    datetime(2025, 1, 15),      # created_date
    datetime(2025, 1, 16),      # last_tested_date
    "success",                  # last_test_status
    3,                          # tool_count
    30,                         # request_timeout
    3,                          # max_retries
    True,                       # verify_ssl
    2                           # agent_count (from subquery)
)

MOCK_REMOTE_SERVER_ROW = (
    2,                          # server_id
    "GitHub MCP",               # server_name
    "remote",                   # server_type
    "https://api.github.com/mcp/v1",  # server_url
    "bearer",                   # auth_type
    None,                       # connection_config
    "GitHub integration",       # description
    "Development",              # category
    "github",                   # icon
    1,                          # enabled
    "admin@example.com",        # created_by
    datetime(2025, 1, 15),      # created_date
    None,                       # last_tested_date
    None,                       # last_test_status
    0,                          # tool_count
    30,                         # request_timeout
    3,                          # max_retries
    True,                       # verify_ssl
    0                           # agent_count
)

# AgentMCPServers join result row
MOCK_AGENT_SERVER_ROW = (
    1,                          # server_id
    "Sample Local Server",      # server_name
    "local",                    # server_type
    None,                       # server_url
    None,                       # auth_type
    '{"command":"python","args":["server.py"],"env_vars":{}}',  # connection_config
)

# MCPServerCredentials rows
MOCK_BEARER_CRED_ROW = ("token", "my-secret-token-123")
MOCK_APIKEY_CRED_ROWS = [
    ("header", "X-Custom-Key"),
    ("key", "abc-123-def")
]
MOCK_BASIC_CRED_ROWS = [
    ("username", "admin"),
    ("password", "secret123")
]
```

---

## 7. Known Limitations and Future Testing

### 7.1 Areas Not Yet Covered

| Area | Description | Priority |
|------|-------------|----------|
| SSE transport with real remote server | No real remote MCP server available for integration testing | Medium |
| Concurrent load testing | No tests for multiple simultaneous tool calls across servers | Medium |
| WebSocket transport | MCP spec may add WebSocket transport in future versions | Low |
| OAuth2 auth flow | `auth_type="oauth2"` is in the directory but not implemented in `_get_auth_headers` | High |
| UserMCPServers table | Per-user server access control not yet implemented in routes | Medium |
| Database transaction rollback | Routes do not test rollback behavior on partial failures | Medium |
| Credential rotation | No tests for updating credentials on an existing server | Low |

### 7.2 Performance Testing Considerations

| Test | Description | Recommendation |
|------|-------------|----------------|
| Gateway startup time | Measure time from process start to first successful health check | Target < 3 seconds |
| Tool cache effectiveness | Measure response time with warm vs. cold cache | Cache should reduce latency by > 80% |
| Concurrent connections | Connect 10+ MCP servers simultaneously | Should complete within 60 seconds, no deadlocks |
| Tool call latency | Measure round-trip time for tool calls through gateway | Target < 500ms for local servers |
| Memory under load | Monitor gateway memory with many active connections | Should not grow unbounded |
| Connection cleanup | Verify all resources freed after disconnect/cleanup_all | No zombie subprocesses |

### 7.3 Security Testing Considerations

| Test | Description | Priority |
|------|-------------|----------|
| Credential encryption at rest | Verify `ENCRYPTBYPASSPHRASE` is used for all credential storage | High |
| Credential not in logs | Verify auth headers and tokens are not logged at INFO level | High |
| SQL injection in server CRUD | Verify parameterized queries prevent SQL injection | High |
| Tenant isolation | Verify `sp_setTenantContext` prevents cross-tenant data access | Critical |
| Auth header leakage | Verify auth headers are not returned in API responses | High |
| Gateway CORS policy | Current policy is `allow_origins=["*"]`; tighten for production | Medium |
| Input validation | Verify Pydantic models reject malformed input at gateway level | Medium |
| Subprocess command injection | Verify `command` and `args` cannot inject shell commands | High |
| SSL verification | Verify `verify_ssl` setting is respected in SSE transport | Medium |

### 7.4 Reliability Testing

| Test | Description | Priority |
|------|-------------|----------|
| Gateway restart recovery | Client reconnects after gateway restart | High |
| Subprocess crash recovery | Server manager handles subprocess crash gracefully | Medium |
| Network partition (SSE) | SSE transport handles network interruption | Medium |
| Stale connection detection | Manager detects and cleans up stale connections | Medium |
| Graceful shutdown | All connections cleaned up on `SIGTERM` / shutdown event | High |

---

## Appendix A: Test File Inventory

| File | Component | Test Count | Status |
|------|-----------|------------|--------|
| `builder_mcp/gateway/tests/test_gateway.py` | Protocol + ServerManager | 18 (11 + 7) | Exists |
| `builder_mcp/gateway/tests/sample_mcp_server.py` | Test fixture | N/A | Exists |
| `builder_mcp/tests/test_integration.py` | GatewayClient + ToolConverter | 6 (1 + 5) | Exists |
| `builder_mcp/tests/conftest.py` | Shared fixtures | N/A | In Progress |
| `builder_mcp/tests/test_tool_converter.py` | Tool Converter | ~36 | In Progress |
| `builder_mcp/tests/test_gateway_client.py` | Gateway Client (mocked HTTP) | ~19 | In Progress |
| `builder_mcp/tests/test_agent_integration.py` | Agent Integration | ~30 | In Progress |
| `builder_mcp/tests/test_protocol.py` | Protocol (extended) | ~21 | In Progress |
| `builder_mcp/tests/test_routes.py` | Flask Routes | ~34 | Planned |
| `builder_mcp/tests/test_stdio_transport.py` | Stdio Transport | ~18 | Planned |
| `builder_mcp/tests/test_sse_transport.py` | SSE Transport | ~16 | Planned |
| `builder_mcp/gateway/tests/test_api.py` | FastAPI endpoints | ~15 | Planned |

**Total planned test cases:** ~213+

## Appendix B: Test Naming Convention

All test functions follow the pattern:

```
test_<component>_<behavior>_<scenario>
```

Examples:
- `test_sanitize_name_hyphens_to_underscores`
- `test_health_check_connection_error_returns_false`
- `test_connect_local_server_success`
- `test_call_tool_timeout_returns_error`

Test classes follow the pattern: `Test<ComponentName>` (e.g., `TestMCPProtocol`, `TestToolConverter`, `TestServerManager`).
