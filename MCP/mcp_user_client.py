"""
Simple MCP client for user-configured servers
"""
import sys
import subprocess
import json
import os
from typing import Dict, List, Any, Optional
import logging
from logging.handlers import WatchedFileHandler

# Get the parent directory (the project root)
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(parent_dir)

from CommonUtils import rotate_logs_on_startup, get_log_path

rotate_logs_on_startup(os.getenv('MCP_CLIENT_LOG', get_log_path('mcp_client_log.txt')))

# Configure logging
logger = logging.getLogger("MCPClientLog")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=os.getenv('MCP_CLIENT_LOG', get_log_path('mcp_client_log.txt')), encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)


class SimpleMCPClient:
    """Simple stdio-based MCP client"""
    
    def __init__(self, command: str, args: List[str], env_vars: Dict[str, str] = None):
        self.command = command
        self.args = args
        self.env_vars = env_vars or {}
        self.process = None
        
    def start(self) -> bool:
        """Start the MCP server process"""
        try:
            # Merge environment variables
            env = os.environ.copy()
            env.update(self.env_vars)
            
            self.process = subprocess.Popen(
                [self.command] + self.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                bufsize=0
            )
            
            # Send initialize request
            init_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "AIHub",
                        "version": "1.0.0"
                    }
                }
            }
            
            self._send_request(init_request)
            response = self._read_response()
            
            if response.get("result"):
                logger.info(f"MCP server initialized successfully")
                return True
            else:
                logger.error(f"Failed to initialize MCP server: {response}")
                return False
                
        except Exception as e:
            logger.error(f"Error starting MCP server: {str(e)}")
            return False
    
    def list_tools(self) -> List[Dict]:
        """Get list of available tools"""
        try:
            request = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list"
            }
            
            self._send_request(request)
            response = self._read_response()
            
            if "result" in response:
                return response["result"].get("tools", [])
            else:
                logger.error(f"Error listing tools: {response}")
                return []
                
        except Exception as e:
            logger.error(f"Error listing tools: {str(e)}")
            return []
    
    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call a tool on the MCP server"""
        try:
            request = {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments
                }
            }
            
            self._send_request(request)
            response = self._read_response()
            
            if "result" in response:
                return response["result"]
            else:
                error = response.get("error", {})
                raise Exception(f"Tool call failed: {error.get('message', 'Unknown error')}")
                
        except Exception as e:
            logger.error(f"Error calling tool {tool_name}: {str(e)}")
            raise
    
    def _send_request(self, request: Dict):
        """Send a JSON-RPC request"""
        if not self.process or self.process.poll() is not None:
            raise Exception("MCP server process not running")
            
        request_str = json.dumps(request) + "\n"
        self.process.stdin.write(request_str)
        self.process.stdin.flush()
    
    def _read_response(self) -> Dict:
        """Read a JSON-RPC response"""
        if not self.process:
            raise Exception("MCP server process not running")
            
        line = self.process.stdout.readline()
        if not line:
            raise Exception("No response from MCP server")
            
        return json.loads(line)
    
    def close(self):
        """Close the MCP server connection"""
        if self.process:
            self.process.terminate()
            self.process.wait(timeout=5)
            self.process = None


def test_mcp_server_connection(command: str, args: List[str], env_vars: Dict = None) -> Dict:
    """
    Test an MCP server connection and return available tools
    
    Returns:
        {
            "status": "success" | "failed",
            "message": "...",
            "tools": [...],
            "server_info": {...}
        }
    """
    client = SimpleMCPClient(command, args, env_vars)
    
    try:
        # Try to start the server
        if not client.start():
            return {
                "status": "failed",
                "message": "Failed to initialize MCP server",
                "tools": []
            }
        
        # Try to list tools
        tools = client.list_tools()
        
        return {
            "status": "success",
            "message": f"Successfully connected. Found {len(tools)} tools.",
            "tools": tools,
            "tool_count": len(tools)
        }
        
    except Exception as e:
        return {
            "status": "failed",
            "message": f"Error: {str(e)}",
            "tools": []
        }
    finally:
        client.close()


# mcp_user_client.py
import asyncio
import json
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
import urllib.parse as _url

import aiohttp
from aiohttp import ClientSession, ClientTimeout

Json = Dict[str, Any]
EventHandler = Callable[[str, Dict[str, Any]], Awaitable[None] | None]

import urllib.parse as _url

def _split_origin_path(url: str) -> Tuple[str, str]:
    """Return (origin, absolute_path). origin = scheme://host[:port]"""
    p = _url.urlsplit(url)
    origin = _url.urlunsplit((p.scheme, p.netloc, "", "", ""))
    path = p.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    return origin, path

def _urljoin(origin: str, path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return origin + path

def _candidate_rpc_urls(input_url: str) -> List[str]:
    """
    IMPORTANT: In FastMCP SSE mode, the MCP endpoint is the SAME path for GET (SSE) and POST (JSON-RPC).
    So we try POST to the exact input URL FIRST, then common fallbacks.
    """
    origin, path = _split_origin_path(input_url)

    # 1) Try POST to the exact path the user gave (works for FastMCP: /sse)
    candidates = [_urljoin(origin, path)]

    # 2) If user passed an SSE-ish path, also try a sibling /rpc
    if path.endswith("/sse"):
        candidates.append(_urljoin(origin, path[:-4] + "rpc"))

    # 3) Common fallbacks used by other servers
    candidates += [
        _urljoin(origin, "/rpc"),
        _urljoin(origin, "/jsonrpc"),
        _urljoin(origin, "/mcp/rpc"),
    ]

    # de-dupe preserving order
    seen, out = set(), []
    for u in candidates:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

def _candidate_sse_urls(input_url: str) -> List[str]:
    """
    Prefer the exact input URL if it looks like SSE; otherwise try common SSE paths.
    """
    origin, path = _split_origin_path(input_url)
    candidates = []
    # If caller passed /sse (FastMCP default), use it first
    if path.endswith("/sse"):
        candidates.append(_urljoin(origin, path))

    # Common SSE endpoints
    candidates += [
        _urljoin(origin, "/sse"),
        _urljoin(origin, "/events"),
        _urljoin(origin, "/stream"),
        _urljoin(origin, "/mcp/events"),
    ]

    # de-dupe preserving order
    seen, out = set(), []
    for u in candidates:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


class MCPClient:
    """
    Proper MCP client:
      • JSON-RPC over HTTP POST for control-plane calls (initialize/list/call)
      • SSE (GET) for server-initiated events (tool.update, logs, etc.)
      • Endpoint auto-discovery so it works against FastMCP and other servers
    """

    def __init__(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 30.0,
        verify_ssl: bool = True,     # False or path to CA bundle if using private CA
        client_cert: Optional[str | tuple[str, str]] = None,  # "/path/cert.pem" or ("cert.pem", "key.pem")
    ):
        self.input_url = url.rstrip("/")
        self.headers = {"Accept": "application/json", **(headers or {})}
        self._timeout = ClientTimeout(total=timeout)
        self._verify_ssl = verify_ssl
        self._client_cert = client_cert
        self._session: Optional[ClientSession] = None

        self.rpc_url: Optional[str] = None
        self.sse_url: Optional[str] = None
        self.capabilities: Dict[str, Any] = {}

        # SSE runner state
        self._sse_task: Optional[asyncio.Task] = None
        self._stop_sse = asyncio.Event()

    # ---------- lifecycle ----------
    async def connect(self) -> None:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers=self.headers,
                json_serialize=json.dumps,
            )
        # discover endpoints lazily (but here is a good time)
        if self.rpc_url is None or self.sse_url is None:
            await self._discover_endpoints()

    async def disconnect(self) -> None:
        await self.stop_events()
        if self._session:
            await self._session.close()
            self._session = None

    # ---------- discovery ----------
    # --- discovery: replace your _discover_endpoints / _probe_* with this ---
    async def _discover_endpoints(self) -> None:
        assert self._session is not None

        # Find SSE (optional, but nice to have)
        for u in _candidate_sse_urls(self.input_url):
            try:
                async with self._session.get(u, ssl=self._verify_ssl, timeout=aiohttp.ClientTimeout(sock_read=5, total=5)) as r:
                    if r.status == 200 and "text/event-stream" in r.headers.get("Content-Type", ""):
                        self.sse_url = u
                        break
            except Exception:
                pass  # keep probing

        # Find RPC (required)
        probe = {
            "jsonrpc": "2.0",
            "id": "probe",
            "method": "initialize",
            "params": {"protocolVersion": "1.0.0"},
        }
        last_error = None
        for u in _candidate_rpc_urls(self.input_url):
            try:
                async with self._session.post(u, json=probe, ssl=self._verify_ssl) as r:
                    if r.status // 100 != 2:
                        last_error = f"{u} -> HTTP {r.status}"
                        continue
                    # Some servers return JSON-RPC envelope; some return bare result
                    data = await r.json(content_type=None)
                    if isinstance(data, dict) and ("result" in data or "capabilities" in data):
                        self.rpc_url = u
                        return
                    # Accept bare dict as result
                    if isinstance(data, dict):
                        self.rpc_url = u
                        return
            except Exception as e:
                last_error = f"{u} -> {e}"

        tried = ", ".join(_candidate_rpc_urls(self.input_url))
        raise RuntimeError(f"Could not find a working MCP RPC endpoint (tried: {tried})"
                        + (f"; last error: {last_error}" if last_error else ""))


    async def _probe_sse(self, urls: List[str]) -> Optional[str]:
        assert self._session is not None
        for u in urls:
            try:
                async with self._session.get(u, ssl=self._verify_ssl, timeout=ClientTimeout(sock_read=5, total=5)) as r:
                    ctype = r.headers.get("Content-Type", "")
                    if r.status == 200 and "text/event-stream" in ctype:
                        return u
            except Exception:
                pass
        return None

    async def _probe_rpc(self, urls: List[str]) -> Optional[str]:
        """
        Send a harmless JSON-RPC 'initialize' (no side-effects) to see who answers.
        """
        assert self._session is not None
        probe_payload = {"jsonrpc": "2.0", "id": "probe", "method": "initialize", "params": {"protocolVersion": "1.0.0"}}
        for u in urls:
            try:
                async with self._session.post(u, json=probe_payload, ssl=self._verify_ssl) as r:
                    if r.status // 100 != 2:
                        continue
                    data = await r.json(content_type=None)
                    # Accept either full JSON-RPC envelope or bare result
                    if isinstance(data, dict) and ("result" in data or "capabilities" in data):
                        return u
            except Exception:
                continue
        return None

    # ---------- JSON-RPC ----------
    async def jsonrpc(self, method: str, params: Optional[Json] = None, *, rpc_id: str | int | None = None) -> Json:
        if not self._session:
            raise RuntimeError("Call connect() first")
        if not self.rpc_url:
            await self._discover_endpoints()

        payload = {
            "jsonrpc": "2.0",
            "id": rpc_id if rpc_id is not None else str(uuid.uuid4()),
            "method": method,
            "params": params or {},
        }
        async with self._session.post(self.rpc_url, json=payload, ssl=self._verify_ssl) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
            if "error" in data:
                raise RuntimeError(f"JSON-RPC error: {data['error']}")
            return data.get("result", data)

    async def initialize(self, protocol_version: str = "1.0.0", capabilities: Optional[Json] = None) -> Json:
        caps = capabilities or {"tools": {"call": True}, "resources": {"read": True}}
        result = await self.jsonrpc("initialize", {"protocolVersion": protocol_version, "capabilities": caps})
        # Some servers return bare result; others wrap in {result:{...}}
        self.capabilities = result.get("capabilities", result.get("server", {}))
        return result

    async def list_tools(self) -> List[Json]:
        # Try common method names
        try:
            result = await self.jsonrpc("listTools", {})
        except Exception:
            result = await self.jsonrpc("tools/list", {})
        tools = result.get("tools", result)
        return tools if isinstance(tools, list) else [tools]

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        try:
            result = await self.jsonrpc("callTool", {"name": name, "arguments": arguments})
        except Exception:
            result = await self.jsonrpc("tools/call", {"name": name, "arguments": arguments})
        return result

    # ---------- SSE (optional) ----------
    async def start_events(
        self,
        on_event: EventHandler,
        *,
        last_event_id: Optional[str] = None,
        backoff_max: float = 30.0,
        ping_timeout: float = 65.0,
    ) -> None:
        """
        Subscribe to SSE (if available) and dispatch on_event(event_name, payload_dict).
        Call stop_events() to stop. Auto-reconnects with backoff.
        """
        if not self._session:
            raise RuntimeError("Call connect() first")
        if self._sse_task and not self._sse_task.done():
            return
        if not self.sse_url:
            # Try discover once more; still optional if missing
            await self._discover_endpoints()
            if not self.sse_url:
                raise RuntimeError("No SSE endpoint found on server")

        self._stop_sse.clear()
        self._sse_task = asyncio.create_task(
            self._sse_loop(on_event, last_event_id=last_event_id, backoff_max=backoff_max, ping_timeout=ping_timeout)
        )

    async def stop_events(self) -> None:
        self._stop_sse.set()
        if self._sse_task:
            try:
                await asyncio.wait_for(self._sse_task, timeout=2.0)
            except asyncio.TimeoutError:
                self._sse_task.cancel()
            self._sse_task = None

    async def _sse_loop(
        self,
        on_event: EventHandler,
        *,
        last_event_id: Optional[str],
        backoff_max: float,
        ping_timeout: float,
    ) -> None:
        assert self._session is not None
        backoff = 1.0
        while not self._stop_sse.is_set():
            try:
                headers = {
                    **self.headers,
                    "Accept": "text/event-stream",
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                }
                if last_event_id:
                    headers["Last-Event-ID"] = last_event_id

                async with self._session.get(
                    self.sse_url,  # type: ignore[arg-type]
                    headers=headers,
                    ssl=self._verify_ssl,
                    timeout=ClientTimeout(sock_read=ping_timeout, total=None),
                ) as resp:
                    if resp.status != 200 or "text/event-stream" not in resp.headers.get("Content-Type", ""):
                        text = await resp.text()
                        raise RuntimeError(f"SSE bad response {resp.status}: {text[:200]}")

                    backoff = 1.0
                    event_name: Optional[str] = None
                    data_lines: List[str] = []

                    async for chunk in resp.content:
                        if self._stop_sse.is_set():
                            return
                        line = chunk.decode("utf-8").rstrip("\r\n")

                        if line == "":
                            if data_lines:
                                payload_str = "\n".join(data_lines)
                                try:
                                    payload = json.loads(payload_str)
                                except json.JSONDecodeError:
                                    payload = {"raw": payload_str}
                                name = event_name or "message"
                                res = on_event(name, payload)
                                if asyncio.iscoroutine(res):
                                    await res
                            event_name = None
                            data_lines = []
                            continue

                        if line.startswith(":"):  # heartbeat
                            continue
                        if line.startswith("event:"):
                            event_name = line[len("event:"):].strip()
                            continue
                        if line.startswith("id:"):
                            last_event_id = line[len("id:"):].strip()
                            continue
                        if line.startswith("data:"):
                            data_lines.append(line[len("data:"):].lstrip())
                            continue

            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, backoff_max)
