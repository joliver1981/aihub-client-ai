"""
AIHUB-0047 — long, quiet builder delegations no longer drop the SSE transport.

Live failure (0022/0041 retests): multi-step builder turns died with
"network error" / "Failed to fetch" while every service stayed HTTP-200 —
idle-stream drops on two hops:
  1. CC routes/chat.py yielded NOTHING to the browser while the graph ran a
     long delegation (the progress-queue poll loop had no heartbeat);
  2. the builder's EventSourceResponse had no ping, so the delegator's httpx
     read sat idle through long LLM calls, with a bare float timeout making
     `read` a 120s idle cap.

Covered here:
  - the delegator's SSE line parser tolerates ping/comment lines (the builder
    now sends them every 15s) — full live-path drive with comments injected;
  - the delegator uses a granular httpx.Timeout with a generous read;
  - the builder response construction carries ping=15 (source contract);
  - the CC heartbeat loop exists with the tunable interval (source contract —
    the route module itself needs the FastAPI stack, too heavy for unit import).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]


class TestDelegatorPingTolerance:
    async def test_sse_comments_are_ignored_end_to_end(self, monkeypatch):
        """Drive the REAL delegate_to_builder with sse_starlette-style ping
        comments interleaved — parsing must be unaffected."""
        import importlib.util
        import types
        import json as _json

        fake_cfg = types.ModuleType("cc_config")
        fake_cfg.get_builder_api_base_url = lambda: "http://localhost:65535"
        fake_cfg.AI_HUB_API_KEY = "TEST-KEY"
        monkeypatch.setitem(sys.modules, "cc_config", fake_cfg)

        from command_center.orchestration import delegator

        lines = [
            ": ping - 2026-07-18 03:40:00",       # sse_starlette ping comment
            "",
            "event: token",
            'data: {"text": "Working on it."}',
            "",
            ": ping - 2026-07-18 03:40:15",
            "",
            "event: plan",
            'data: {"status": "completed", "steps": [{"description": "build it", "status": "completed"}]}',
            "",
            ": keepalive",
            "",
            "event: done",
            'data: {"session_id": "test-session"}',
            "",
        ]

        class _Resp:
            status_code = 200
            text = ""

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def aiter_lines(self):
                for ln in lines:
                    yield ln

            async def aread(self):
                return b""

        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def stream(self, *a, **k):
                return _Resp()

        monkeypatch.setattr(delegator.httpx, "AsyncClient", _Client)
        result = await delegator.delegate_to_builder(
            message="build", builder_session_id="test-session")
        assert result["status"] == "completed"
        assert "Working on it." in result["text"]
        assert result["plan"]["status"] == "completed"

    async def test_granular_timeout_with_generous_read(self, monkeypatch):
        """The client must be constructed with an httpx.Timeout whose read is
        generous (>=300s) — a bare float made read a short idle cap."""
        import types

        fake_cfg = types.ModuleType("cc_config")
        fake_cfg.get_builder_api_base_url = lambda: "http://localhost:65535"
        fake_cfg.AI_HUB_API_KEY = "TEST-KEY"
        monkeypatch.setitem(sys.modules, "cc_config", fake_cfg)

        from command_center.orchestration import delegator
        import httpx as _httpx

        captured = {}

        class _Client:
            def __init__(self, *a, timeout=None, **k):
                captured["timeout"] = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def stream(self, *a, **k):
                raise RuntimeError("stop here")

        monkeypatch.setattr(delegator.httpx, "AsyncClient", _Client)
        result = await delegator.delegate_to_builder(
            message="build", builder_session_id="s", timeout=120.0)
        assert result["status"] == "failed"          # our sentinel stop
        t = captured["timeout"]
        assert isinstance(t, _httpx.Timeout)
        assert t.read >= 300.0
        assert t.connect <= 30.0


class TestSourceContracts:
    def test_builder_stream_pings(self):
        src = (_ROOT / "builder_service" / "routes" / "chat.py").read_text(encoding="utf-8")
        assert re.search(r"EventSourceResponse\(event_generator\(\),\s*ping=15\)", src)

    def test_cc_stream_heartbeat_loop(self):
        src = (_ROOT / "command_center_service" / "routes" / "chat.py").read_text(encoding="utf-8")
        assert "_SSE_HEARTBEAT_SECONDS" in src
        assert 'os.getenv("CC_SSE_HEARTBEAT_SECONDS"' in src
        assert '"heartbeat": True' in src
        # the heartbeat rides the status channel inside the graph-poll loop
        loop = re.search(r"while not _invoke_task\.done\(\):(.*?)final_state = await _invoke_task",
                         src, re.S)
        assert loop and "heartbeat" in loop.group(1)
