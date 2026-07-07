"""Unit tests for command_center/orchestration/delegator.py.

We mock httpx.AsyncClient so no real HTTP traffic occurs and the
tests are deterministic. Covers:

- Data agent vs general agent routing (correct URL, payload shape)
- API-key + Connection:close headers (tenant context propagation)
- conversation_history role mapping (user→Q, assistant→A)
- Timeout handling
- 500-class errors return a failure dict (no exception)
- delegate_to_builder consumes SSE events, accumulates tokens, surfaces
  builder errors as content
- delegate_to_mcp_tool happy path
- execute_workflow happy path
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path = [str(_ROOT)] + [p for p in sys.path if p != str(_ROOT)]
# Push CC service path to enable its `cc_config` module
_SVC = _ROOT / "command_center_service"
sys.path = [str(_SVC)] + [p for p in sys.path if p != str(_SVC)]

# Clear any cached duplicates
for _m in [m for m in list(sys.modules) if m == "routes" or m.startswith("routes.")]:
    del sys.modules[_m]

from command_center.orchestration import delegator  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text

    def json(self):
        return self._json


class _FakeAsyncClient:
    """Stand-in for httpx.AsyncClient that records calls and returns canned
    responses. Constructable as `_FakeAsyncClient(post_response=...)`."""

    def __init__(self, post_response=None, post_exc=None, stream_lines=None,
                 stream_status=200, stream_text=""):
        self.post_response = post_response or _FakeResponse(200, {})
        self.post_exc = post_exc
        self.stream_lines = stream_lines or []
        self.stream_status = stream_status   # F1: builder /api/chat streamed HTTP status
        self.stream_text = stream_text
        self.posts: list = []
        self.streams: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        if self.post_exc:
            raise self.post_exc
        return self.post_response

    def stream(self, method, url, json=None, headers=None):
        self.streams.append({"method": method, "url": url, "json": json,
                             "headers": headers})
        client = self

        class _Ctx:
            async def __aenter__(self_inner):
                class _Resp:
                    status_code = client.stream_status   # F1 guard reads this
                    text = client.stream_text

                    async def aread(_self):
                        return client.stream_text.encode()

                    async def aiter_lines(_self):
                        for ln in client.stream_lines:
                            yield ln

                return _Resp()

            async def __aexit__(self_inner, *a):
                return False

        return _Ctx()


@pytest.fixture
def patch_httpx(monkeypatch):
    """Returns a factory that lets the test set the AsyncClient stub."""
    holder = {"client": None}

    def _set(client):
        holder["client"] = client
        # Patch httpx.AsyncClient AFTER setting the holder so the patched
        # factory returns the test's client every time.
        monkeypatch.setattr(delegator.httpx, "AsyncClient",
                            lambda *args, **kw: holder["client"])

    return _set


# ---------------------------------------------------------------------------
# delegate_to_agent — data-agent path
# ---------------------------------------------------------------------------

class TestDelegateToAgent:
    def test_data_agent_uses_internal_query_endpoint(self, patch_httpx, monkeypatch):
        monkeypatch.setenv("AI_HUB_API_KEY", "test-key")
        client = _FakeAsyncClient(post_response=_FakeResponse(
            200, {"response": "42 sales", "rich_content": [{"type": "table"}],
                  "query": "SELECT...", "answer_type": "table"}))
        patch_httpx(client)

        result = asyncio.run(delegator.delegate_to_agent(
            agent_id="data-1", question="how many sales?",
            user_context={"user_id": 7, "tenant_id": 1},
            conversation_history=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
            is_data_agent=True,
            session_id="sess-xyz",
        ))

        assert result["status"] == "completed"
        assert result["text"] == "42 sales"
        assert result["rich_content"] == [{"type": "table"}]
        assert result["query"] == "SELECT..."
        assert result["answer_type"] == "table"

        # URL & payload sanity
        post = client.posts[0]
        assert post["url"].endswith("/data_explorer/internal/query")
        body = post["json"]
        assert body["agent_id"] == "data-1"
        assert body["question"] == "how many sales?"
        assert body["session_id"] == "sess-xyz"
        # History gets transformed user→Q / assistant→A
        assert body["history"] == [{"role": "Q", "content": "hi"},
                                   {"role": "A", "content": "hello"}]
        # user_context propagates user_id
        assert body["user_id"] == 7
        # Tenant context propagation via API key header
        assert post["headers"]["X-API-Key"]
        assert post["headers"]["Connection"] == "close"

    def test_general_agent_uses_standard_api_endpoint(self, patch_httpx):
        client = _FakeAsyncClient(post_response=_FakeResponse(
            200, {"answer": "answered"}))
        patch_httpx(client)

        result = asyncio.run(delegator.delegate_to_agent(
            agent_id="general-9", question="hi",
            is_data_agent=False,
        ))
        assert result["text"] == "answered"
        post = client.posts[0]
        assert "/api/agents/general-9/chat" in post["url"]
        # Standard payload uses "prompt"/"history" not "question"/"agent_id"
        assert post["json"]["prompt"] == "hi"
        assert post["json"]["history"] == "[]"

    def test_data_agent_falls_back_to_str_for_unknown_response_keys(self, patch_httpx):
        client = _FakeAsyncClient(post_response=_FakeResponse(
            200, {"completely_unknown": "field"}))
        patch_httpx(client)
        out = asyncio.run(delegator.delegate_to_agent(
            agent_id="d", question="x", is_data_agent=True,
        ))
        assert out["status"] == "completed"
        assert "completely_unknown" in out["text"]

    def test_non_200_returns_failed(self, patch_httpx):
        client = _FakeAsyncClient(post_response=_FakeResponse(
            500, text="internal error"))
        patch_httpx(client)
        out = asyncio.run(delegator.delegate_to_agent(
            agent_id="d", question="x", is_data_agent=True,
        ))
        assert out["status"] == "failed"
        assert "500" in out["text"]
        assert "internal error" in out["text"]

    def test_timeout_returns_failed_with_message(self, patch_httpx):
        import httpx
        client = _FakeAsyncClient(post_exc=httpx.TimeoutException("slow"))
        patch_httpx(client)
        out = asyncio.run(delegator.delegate_to_agent(
            agent_id="d", question="x", timeout=12.0, is_data_agent=True,
        ))
        assert out["status"] == "failed"
        assert "timed out" in out["text"]
        assert "12.0" in out["text"]

    def test_generic_exception_returns_failed(self, patch_httpx):
        client = _FakeAsyncClient(post_exc=RuntimeError("net dead"))
        patch_httpx(client)
        out = asyncio.run(delegator.delegate_to_agent(
            agent_id="d", question="x", is_data_agent=True,
        ))
        assert out["status"] == "failed"
        assert "net dead" in out["text"]

    def test_no_user_context_omits_user_id(self, patch_httpx):
        client = _FakeAsyncClient(post_response=_FakeResponse(200, {"response": "ok"}))
        patch_httpx(client)
        asyncio.run(delegator.delegate_to_agent(
            agent_id="d", question="x", is_data_agent=True,
        ))
        body = client.posts[0]["json"]
        # user_id key was never added
        assert "user_id" not in body

    def test_default_session_id_for_data_agent(self, patch_httpx):
        client = _FakeAsyncClient(post_response=_FakeResponse(200, {"response": "ok"}))
        patch_httpx(client)
        asyncio.run(delegator.delegate_to_agent(
            agent_id="d-7", question="x", is_data_agent=True, session_id=None,
        ))
        body = client.posts[0]["json"]
        # No user_context -> user_id defaults to "anon" (delegator.py:82)
        assert body["session_id"] == "cc-anon-d-7"


# ---------------------------------------------------------------------------
# delegate_to_builder
# ---------------------------------------------------------------------------

class TestDelegateToBuilder:
    def test_streams_tokens_and_returns_full_response(self, patch_httpx):
        lines = [
            "event: status",
            "data: {\"label\": \"thinking\"}",
            "",
            "event: token",
            "data: {\"text\": \"Hel\"}",
            "",
            "event: token",
            "data: {\"text\": \"lo\"}",
            "",
            "event: done",
            "data: {\"session_id\": \"builder-1\"}",
            "",
        ]
        # post() creates the builder session; stream() runs the chat
        client = _FakeAsyncClient(
            post_response=_FakeResponse(200, {"session_id": "builder-1"}),
            stream_lines=lines,
        )
        patch_httpx(client)
        out = asyncio.run(delegator.delegate_to_builder(
            message="build me an agent",
            session_id="cc-sess",
            user_context={"user_id": 1, "role": 2},
        ))
        assert out["status"] == "completed"
        assert out["text"] == "Hello"
        assert out["builder_session_id"] == "builder-1"

    def test_error_event_marks_failed_and_surfaces_text(self, patch_httpx):
        # F1 (silent-success fix): an in-stream `event: error` frame must make the
        # delegation status 'failed' (it was historically reported 'completed'),
        # while still surfacing the error text so the distiller can report it.
        lines = [
            "event: token", "data: {\"text\": \"Doing thing...\"}", "",
            "event: error", "data: {\"message\": \"DB unreachable\"}", "",
            "event: done", "data: {\"session_id\": \"b\"}", "",
        ]
        client = _FakeAsyncClient(
            post_response=_FakeResponse(200, {"session_id": "b"}),
            stream_lines=lines,
        )
        patch_httpx(client)
        out = asyncio.run(delegator.delegate_to_builder(
            message="build", session_id="s",
        ))
        assert out["status"] == "failed"
        assert "Doing thing" in out["text"]
        assert "DB unreachable" in out["text"]

    def test_non_200_stream_returns_failed(self, patch_httpx):
        # F1: a 4xx/5xx from the builder /api/chat stream must be reported 'failed',
        # not consumed as an empty SSE stream and reported 'completed'.
        client = _FakeAsyncClient(
            post_response=_FakeResponse(200, {"session_id": "b"}),
            stream_lines=[],
            stream_status=500,
            stream_text='{"error": "builder crashed"}',
        )
        patch_httpx(client)
        out = asyncio.run(delegator.delegate_to_builder(
            message="build", session_id="s",
        ))
        assert out["status"] == "failed"
        assert "500" in out["text"]

    def test_plan_event_renders_when_no_token_buffer(self, patch_httpx):
        lines = [
            "event: plan",
            "data: " + json.dumps({
                "status": "draft",
                "steps": [
                    {"description": "create agent X", "status": "pending"},
                    {"description": "test it", "status": "completed"},
                ],
            }),
            "",
            "event: done", "data: {\"session_id\": \"b\"}", "",
        ]
        client = _FakeAsyncClient(
            post_response=_FakeResponse(200, {"session_id": "b"}),
            stream_lines=lines,
        )
        patch_httpx(client)
        out = asyncio.run(delegator.delegate_to_builder(message="x"))
        assert out["status"] == "completed"
        assert "Builder Agent Plan" in out["text"]
        assert "create agent X" in out["text"]
        assert "test it" in out["text"]
        assert out["plan"]["status"] == "draft"

    def test_uses_provided_builder_session_id_directly(self, patch_httpx):
        lines = [
            "event: token", "data: {\"text\": \"hi\"}", "",
            "event: done", "data: {\"session_id\": \"prov-9\"}", "",
        ]
        client = _FakeAsyncClient(stream_lines=lines)
        patch_httpx(client)
        out = asyncio.run(delegator.delegate_to_builder(
            message="x", builder_session_id="prov-9",
        ))
        # When builder_session_id is provided, no POST /api/sessions is made
        assert client.posts == []
        assert out["builder_session_id"] == "prov-9"

    def test_empty_stream_returns_fallback_message(self, patch_httpx):
        client = _FakeAsyncClient(
            post_response=_FakeResponse(200, {"session_id": "b"}),
            stream_lines=[],
        )
        patch_httpx(client)
        out = asyncio.run(delegator.delegate_to_builder(message="x"))
        assert "no visible output" in out["text"]

    def test_exception_returns_failed(self, patch_httpx, monkeypatch):
        def _boom(*a, **kw):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(delegator.httpx, "AsyncClient", _boom)
        out = asyncio.run(delegator.delegate_to_builder(message="x"))
        assert out["status"] == "failed"
        assert "connection refused" in out["text"]


# ---------------------------------------------------------------------------
# delegate_to_mcp_tool
# ---------------------------------------------------------------------------

class TestDelegateToMcpTool:
    def test_success(self, patch_httpx):
        client = _FakeAsyncClient(post_response=_FakeResponse(
            200, {"result": {"hello": "world"}}))
        patch_httpx(client)
        out = asyncio.run(delegator.delegate_to_mcp_tool(
            server_id=3, tool_name="lookup", arguments={"q": "x"},
        ))
        assert out["status"] == "completed"
        assert out["raw"] == {"result": {"hello": "world"}}
        post = client.posts[0]
        assert "/api/tools/3/call" in post["url"]
        assert post["json"] == {"tool_name": "lookup", "arguments": {"q": "x"}}

    def test_non_200_returns_failed(self, patch_httpx):
        client = _FakeAsyncClient(post_response=_FakeResponse(503))
        patch_httpx(client)
        out = asyncio.run(delegator.delegate_to_mcp_tool(
            server_id=1, tool_name="t", arguments={},
        ))
        assert out["status"] == "failed"

    def test_exception_returns_failed(self, monkeypatch):
        def _boom(*a, **kw):
            raise RuntimeError("dns")

        monkeypatch.setattr(delegator.httpx, "AsyncClient", _boom)
        out = asyncio.run(delegator.delegate_to_mcp_tool(
            server_id=1, tool_name="t", arguments={},
        ))
        assert out["status"] == "failed"
        assert "dns" in out["text"]


# ---------------------------------------------------------------------------
# execute_workflow
# ---------------------------------------------------------------------------

class TestExecuteWorkflow:
    def test_success(self, patch_httpx):
        client = _FakeAsyncClient(post_response=_FakeResponse(
            200, {"run_id": "r1", "status": "completed"}))
        patch_httpx(client)
        out = asyncio.run(delegator.execute_workflow(
            workflow_id="wf-1", inputs={"x": 1},
        ))
        assert out["status"] == "completed"
        post = client.posts[0]
        assert "/api/workflows/wf-1/execute" in post["url"]
        assert post["json"] == {"x": 1}

    def test_no_inputs_sends_empty_payload(self, patch_httpx):
        client = _FakeAsyncClient(post_response=_FakeResponse(200, {"x": 1}))
        patch_httpx(client)
        asyncio.run(delegator.execute_workflow(workflow_id="w"))
        assert client.posts[0]["json"] == {}

    def test_non_200_returns_failed(self, patch_httpx):
        client = _FakeAsyncClient(post_response=_FakeResponse(404))
        patch_httpx(client)
        out = asyncio.run(delegator.execute_workflow(workflow_id="w"))
        assert out["status"] == "failed"
        assert "404" in out["text"]
