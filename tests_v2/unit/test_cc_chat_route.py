"""FastAPI route tests for command_center_service/routes/chat.py.

Approach: mount the chat router on a fresh FastAPI app and drive it with
TestClient. The LangGraph executor, session_mgr, scan_platform, and
TraceStore are all mocked at the boundary so the test stays under 1s.

Covers:
- _parse_response_blocks single-encoded and double-encoded payloads
- Streaming event order (trace, session, status, response, done)
- error event when graph raises
- session title auto-generation strips HTML tags (BUG-R2-013 fix)
- password / api-key masking in response blocks (BUG-R2-015 fix)
- password masking in builder_log payloads
- session continuity: client-supplied session_id used; blank session_id
  triggers creation
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_SVC_ROOT = Path(__file__).resolve().parents[2] / "command_center_service"
sys.path = [str(_SVC_ROOT)] + [p for p in sys.path if p != str(_SVC_ROOT)]
for _m in [m for m in list(sys.modules) if m == "routes" or m.startswith("routes.")]:
    del sys.modules[_m]

from langchain_core.messages import AIMessage  # noqa: E402

from routes import chat as chat_module  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers — parse the SSE stream into a list of (event, data) tuples
# ---------------------------------------------------------------------------

def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    events = []
    current_event = None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            current_event = None
            continue
        if line.startswith("event:"):
            current_event = line[6:].strip()
        elif line.startswith("data:") and current_event:
            try:
                events.append((current_event, json.loads(line[5:].strip())))
            except json.JSONDecodeError:
                pass
    return events


def _make_session():
    s = MagicMock()
    s.session_id = "sess-1"
    s.title = "New Chat"
    return s


def _make_mocked_session_mgr(session=None):
    sm = MagicMock()
    sm.get_or_create.return_value = session or _make_session()
    sm.get_messages.return_value = []
    sm.get_session_state.return_value = {}
    sm.add_message = MagicMock()
    sm.save_session_state = MagicMock()
    sm.update_title = MagicMock()
    sm.attach_user_context_if_missing = MagicMock()
    # Ownership pre-check (BUG-CC-SESSION-ID-FORGERY): anything but
    # "mismatch" lets the request proceed; be explicit rather than relying
    # on MagicMock's auto-attribute happening to compare unequal.
    sm.check_session_ownership.return_value = "match"
    return sm


def _make_graph_returning(messages, **extra):
    graph = MagicMock()

    async def _ainvoke(*a, **kw):
        return {"messages": messages, "intent": "chat", **extra}

    graph.ainvoke = _ainvoke
    return graph


def _make_graph_that_raises(exc):
    graph = MagicMock()

    async def _ainvoke(*a, **kw):
        raise exc

    graph.ainvoke = _ainvoke
    return graph


@pytest.fixture
def app_factory(monkeypatch):
    """Returns a function that builds a fresh TestClient with the given
    graph and session_mgr mocked in."""

    # Stub scan_platform to avoid real I/O
    async def _stub_scan_platform(*a, **kw):
        return {"agents": [], "data_agents": [], "connections": [], "tools": []}

    # Patch landscape_scanner
    try:
        import command_center.orchestration.landscape_scanner as ls
        monkeypatch.setattr(ls, "scan_platform", _stub_scan_platform)
    except Exception:
        pass

    # Disable the route_memory + insights side-effects
    try:
        import cc_config as _cc_cfg
        monkeypatch.setattr(_cc_cfg, "USE_ROUTE_MEMORY", False, raising=False)
        monkeypatch.setattr(_cc_cfg, "USE_SESSION_INSIGHTS", False, raising=False)
    except Exception:
        pass

    # Stub the user-pref/insights memory imports to no-op
    fake_user_memory = types.ModuleType("command_center.memory.user_memory")
    fake_user_memory.get_preferences = lambda uid: {}
    fake_route_memory = types.ModuleType("command_center.memory.route_memory")
    fake_route_memory.get_insights_for_context = lambda uid, limit=10: ""
    fake_route_memory.CC_TRACKABLE_TOOLS = set()

    async def _stub_log_route(**kw): return None
    async def _stub_extract(**kw): return None

    fake_route_memory.log_route = _stub_log_route
    fake_route_memory.extract_session_insights = _stub_extract
    monkeypatch.setitem(sys.modules, "command_center.memory.user_memory",
                        fake_user_memory)
    monkeypatch.setitem(sys.modules, "command_center.memory.route_memory",
                        fake_route_memory)

    # Trace store: redirect to tmp dir
    import tempfile
    tmpdir = Path(tempfile.mkdtemp(prefix="cc_chat_test_"))
    from services.trace_store import TraceStore
    monkeypatch.setattr(chat_module, "_trace_store", TraceStore(tmpdir))

    # The ops broadcaster should be a fresh no-op; the chat module already
    # uses a defensive import — we patch it to a Mock so .begin/.end never
    # raise inside the streamer.
    monkeypatch.setattr(chat_module, "_ops_broadcaster", MagicMock())
    monkeypatch.setattr(chat_module, "_ops_broadcast", lambda *a, **kw: None)

    # ---- Auth: /api/chat enforces a signed CC JWT (CC_REQUIRE_JWT default
    # "1"; identity comes from claims, never the request body —
    # BUG-CC-SESSION-ID-FORGERY). Sign REAL tokens with a throwaway unit
    # secret so these tests exercise the true verify path instead of mocking
    # auth away. shared_auth resolves the secret from CC_JWT_SECRET at call
    # time, so sign and verify agree within this process.
    monkeypatch.setenv("CC_JWT_SECRET", "unit-test-jwt-secret-0123456789abcdef")
    monkeypatch.setenv("CC_REQUIRE_JWT", "1")
    import shared_auth

    def _build(graph, session_mgr, user=None):
        chat_module.init_chat_routes(graph, session_mgr)
        app = FastAPI()
        app.include_router(chat_module.router)
        client = TestClient(app)
        token = shared_auth.sign_cc_token(user or {
            "user_id": 13, "role": 3, "tenant_id": 1,
            "username": "admin", "name": "Admin",
        })
        client.headers.update({"Authorization": f"Bearer {token}"})
        return client

    return _build


# ---------------------------------------------------------------------------
# _parse_response_blocks (pure helper)
# ---------------------------------------------------------------------------

class TestParseResponseBlocks:
    def test_plain_text(self):
        out = chat_module._parse_response_blocks("Hello world")
        assert out == [{"type": "text", "content": "Hello world"}]

    def test_json_array_of_blocks(self):
        out = chat_module._parse_response_blocks(json.dumps([
            {"type": "text", "content": "a"},
            {"type": "table", "rows": []},
        ]))
        assert len(out) == 2
        assert out[1]["type"] == "table"

    def test_double_encoded_single_block(self):
        inner = json.dumps([{"type": "text", "content": "real"},
                            {"type": "chart", "chartType": "bar"}])
        outer = json.dumps([{"type": "text", "content": inner}])
        out = chat_module._parse_response_blocks(outer)
        # Inner blocks get unwrapped
        types_ = [b["type"] for b in out]
        assert "chart" in types_

    def test_malformed_json_passes_through(self):
        out = chat_module._parse_response_blocks("[not json")
        assert out == [{"type": "text", "content": "[not json"}]

    def test_json_string_wrapping_array(self):
        # JSON-encoded string that itself contains a JSON array → unwrapped
        inner = json.dumps([{"type": "text", "content": "x"}])
        outer = json.dumps(inner)  # double-encoded as a string
        out = chat_module._parse_response_blocks(outer)
        assert out[0]["type"] == "text"


# ---------------------------------------------------------------------------
# Streaming happy path
# ---------------------------------------------------------------------------

class TestChatStreaming:
    def test_event_order_session_response_done(self, app_factory):
        graph = _make_graph_returning([AIMessage(content="Plain text answer")])
        sm = _make_mocked_session_mgr()
        client = app_factory(graph, sm)

        r = client.post("/api/chat", json={"message": "hello"})
        assert r.status_code == 200
        events = _parse_sse(r.text)
        types_ = [e[0] for e in events]
        # We expect at minimum: trace, session, status..., response, done
        assert "session" in types_
        assert "response" in types_
        assert types_[-1] == "done"
        # Session emitted before response
        assert types_.index("session") < types_.index("response")
        # The user message persists
        sm.add_message.assert_any_call("sess-1", "user", "hello")
        sm.add_message.assert_any_call("sess-1", "assistant", "Plain text answer")

    def test_response_payload_includes_blocks_and_session_id(self, app_factory):
        graph = _make_graph_returning([AIMessage(content="answer text")])
        sm = _make_mocked_session_mgr()
        client = app_factory(graph, sm)

        events = _parse_sse(client.post("/api/chat", json={"message": "q"}).text)
        resp = next(e[1] for e in events if e[0] == "response")
        assert resp["session_id"] == "sess-1"
        assert isinstance(resp["blocks"], list)
        assert resp["blocks"][0]["type"] == "text"
        assert "answer text" in resp["blocks"][0]["content"]

    def test_missing_message_returns_error(self, app_factory):
        graph = _make_graph_returning([AIMessage(content="x")])
        sm = _make_mocked_session_mgr()
        client = app_factory(graph, sm)
        r = client.post("/api/chat", json={"message": "   "})
        assert r.status_code == 200
        assert r.json() == {"error": "Message is required"}

    def test_no_graph_returns_error(self, app_factory):
        sm = _make_mocked_session_mgr()
        # Pass None as the graph — init_chat_routes accepts None
        client = app_factory(None, sm)
        r = client.post("/api/chat", json={"message": "hi"})
        assert r.status_code == 200
        assert r.json() == {"error": "Command Center graph not initialized"}


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------

class TestChatErrorEvent:
    def test_graph_exception_emits_error_event(self, app_factory):
        graph = _make_graph_that_raises(RuntimeError("LLM blew up"))
        sm = _make_mocked_session_mgr()
        client = app_factory(graph, sm)
        events = _parse_sse(client.post("/api/chat", json={"message": "hi"}).text)
        types_ = [e[0] for e in events]
        assert "error" in types_
        err_data = next(e[1] for e in events if e[0] == "error")
        assert "LLM blew up" in err_data["message"]


# ---------------------------------------------------------------------------
# Session title sanitization — BUG-R2-013
# ---------------------------------------------------------------------------

class TestSessionTitleSanitization:
    def test_html_tags_stripped_from_auto_title(self, app_factory):
        graph = _make_graph_returning([AIMessage(content="x")])
        sm = _make_mocked_session_mgr()
        client = app_factory(graph, sm)
        client.post("/api/chat", json={
            "message": "<script>alert('xss')</script> normal question text",
        })
        # update_title should have been called with the cleaned title
        assert sm.update_title.called
        call_args = sm.update_title.call_args
        title = call_args[0][1]
        assert "<script>" not in title
        assert "alert" in title or "normal" in title  # text content preserved

    def test_control_chars_stripped_from_title(self, app_factory):
        graph = _make_graph_returning([AIMessage(content="x")])
        sm = _make_mocked_session_mgr()
        client = app_factory(graph, sm)
        client.post("/api/chat", json={"message": "hello\x00\x01\x02 there"})
        title = sm.update_title.call_args[0][1]
        # Control chars never reach the title
        assert "\x00" not in title and "\x01" not in title

    def test_pure_html_message_results_in_new_chat_fallback(self, app_factory):
        graph = _make_graph_returning([AIMessage(content="x")])
        sm = _make_mocked_session_mgr()
        client = app_factory(graph, sm)
        client.post("/api/chat", json={"message": "<b></b><i></i>"})
        # When everything strips out, it falls back to "New Chat"
        title = sm.update_title.call_args[0][1]
        assert title == "New Chat"

    def test_long_message_truncated_at_word_boundary(self, app_factory):
        graph = _make_graph_returning([AIMessage(content="x")])
        sm = _make_mocked_session_mgr()
        client = app_factory(graph, sm)
        long_msg = "word " * 30  # ~150 chars
        client.post("/api/chat", json={"message": long_msg})
        title = sm.update_title.call_args[0][1]
        assert len(title) <= 51  # 50 + ellipsis
        assert title.endswith("…")


# ---------------------------------------------------------------------------
# Password / secret masking — BUG-R2-015
# ---------------------------------------------------------------------------

class TestSecretMasking:
    def test_password_masked_in_response_block(self, app_factory):
        leaked = '[{"type":"text","content":"Password: supersecret123"}]'
        graph = _make_graph_returning([AIMessage(content=leaked)])
        sm = _make_mocked_session_mgr()
        client = app_factory(graph, sm)
        events = _parse_sse(client.post("/api/chat", json={"message": "x"}).text)
        resp = next(e[1] for e in events if e[0] == "response")
        # The literal secret value must be masked
        joined = json.dumps(resp["blocks"])
        assert "supersecret123" not in joined
        assert "***" in joined

    def test_api_key_masked_in_response(self, app_factory):
        leaked = '[{"type":"text","content":"api_key: ABC123def456"}]'
        graph = _make_graph_returning([AIMessage(content=leaked)])
        client = app_factory(graph, _make_mocked_session_mgr())
        events = _parse_sse(client.post("/api/chat", json={"message": "x"}).text)
        resp = next(e[1] for e in events if e[0] == "response")
        joined = json.dumps(resp["blocks"])
        assert "ABC123def456" not in joined

    def test_secret_masked_in_response(self, app_factory):
        leaked = '[{"type":"text","content":"secret = MyS3cretKey"}]'
        graph = _make_graph_returning([AIMessage(content=leaked)])
        client = app_factory(graph, _make_mocked_session_mgr())
        events = _parse_sse(client.post("/api/chat", json={"message": "x"}).text)
        resp = next(e[1] for e in events if e[0] == "response")
        joined = json.dumps(resp["blocks"])
        assert "MyS3cretKey" not in joined

    def test_password_masked_in_builder_log(self, app_factory):
        graph = _make_graph_returning(
            [AIMessage(content="ok")],
            active_delegation={
                "agent_id": "builder",
                "builder_session_id": "b-1",
                "builder_log": [
                    {"role": "assistant",
                     "content": "I'll configure with password: 'leakedpassword' for the user"},
                    {"role": "system",
                     "content": "**api_key:** secretXYZ"},
                ],
            },
        )
        client = app_factory(graph, _make_mocked_session_mgr())
        events = _parse_sse(client.post("/api/chat", json={"message": "build"}).text)
        bl = next((e[1] for e in events if e[0] == "builder_log"), None)
        assert bl is not None
        log_text = json.dumps(bl["log"])
        assert "leakedpassword" not in log_text
        assert "secretXYZ" not in log_text
        assert "***" in log_text


# ---------------------------------------------------------------------------
# Session continuity
# ---------------------------------------------------------------------------

class TestSessionContinuity:
    def test_client_supplied_session_id_is_honored(self, app_factory):
        provided = MagicMock(session_id="sess-from-client", title="Existing Chat")
        sm = _make_mocked_session_mgr(session=provided)
        graph = _make_graph_returning([AIMessage(content="continuing")])
        client = app_factory(graph, sm)

        r = client.post("/api/chat", json={
            "message": "next turn",
            "session_id": "sess-from-client",
        })
        events = _parse_sse(r.text)
        session_evt = next(e[1] for e in events if e[0] == "session")
        assert session_evt["session_id"] == "sess-from-client"
        # get_or_create was called with the provided id
        sm.get_or_create.assert_called_with("sess-from-client")
        # Existing chat keeps its title — update_title shouldn't trigger
        sm.update_title.assert_not_called()

    def test_blank_session_id_triggers_creation(self, app_factory):
        # Default fixture session has id "sess-1" and title "New Chat"
        graph = _make_graph_returning([AIMessage(content="ok")])
        sm = _make_mocked_session_mgr()
        client = app_factory(graph, sm)
        client.post("/api/chat", json={"message": "first message"})
        # get_or_create called with None
        called_args = sm.get_or_create.call_args[0]
        assert called_args[0] is None
        # And the new session gets its title set
        assert sm.update_title.called

    def test_user_context_stamped_from_jwt_claims_not_body(self, app_factory):
        """CONTRACT CHANGE (CC security hardening Phase 0-1): identity comes
        from the SIGNED token's claims. A body `user_context` is ignored even
        when supplied — it can be forged (BUG-CC-SESSION-ID-FORGERY). The
        claims-built context is what gets stamped onto the session.

        (This test previously asserted the body context was honored; that was
        the pre-hardening contract.)"""
        graph = _make_graph_returning([AIMessage(content="x")])
        sm = _make_mocked_session_mgr()
        client = app_factory(graph, sm, user={"user_id": 7, "role": 2, "tenant_id": 1,
                                              "username": "u", "name": "U"})
        client.post("/api/chat", json={
            "message": "hi",
            # A forged body identity — must be ignored in favor of the claims.
            "user_context": {"user_id": 999, "role": 3, "tenant_id": 9,
                             "username": "forged", "name": "Forged"},
        })
        sm.attach_user_context_if_missing.assert_called_once()
        sid_arg, ctx_arg = sm.attach_user_context_if_missing.call_args[0]
        assert sid_arg == "sess-1"
        # The ctx was built into a UserContext-like object FROM THE CLAIMS
        assert getattr(ctx_arg, "user_id", None) == 7
        assert getattr(ctx_arg, "user_id", None) != 999
