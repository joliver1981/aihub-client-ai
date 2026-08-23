"""Deferred results -> chat (Level 1, 2026-08-22) — unit tests.

  * brain.run_turn exposes the conversation id to tools (CURRENT_USER["session_id"])
    — from the resume id, or from the SDK init message on a NEW session — and holds
    the session in flight for the duration of the turn (cleared after).
  * /api/run RESUMES the job's session_id (run_turn is called with the id, not None),
    frames the task as a scheduled firing, and still files the My Work FYI — now
    deep-linked (payload.chat_session_id). Without a session_id it behaves exactly as
    before (None). Flag off -> None. Session in flight -> fresh + FYI (no resume
    attempted). Not owned by the user -> fresh. A resume that dies before any work ->
    fresh retry (the task still runs).
  * /api/chat waits (bounded) while a deferred run is appending to the same session.
  * chat_history.replay tags the deferred-run user line kind="scheduled_run".
  * (The JSS executor's session_id forwarding is pinned in
    test_jss_agent_session_forward.py — it needs job_scheduler's env.)

No live services and no LLM: run_turn / the scheduler / the work-item store are
stubbed on their REAL module objects. Runs standalone (aihub-agent python
test_agent_defer_to_chat.py) or under pytest in an env with claude_agent_sdk; in an
env WITHOUT the SDK (main-app pytest sweep) every test self-skips.
"""
import asyncio
import dataclasses
import json
import os
import sys
import tempfile
import warnings

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

warnings.filterwarnings("ignore", category=DeprecationWarning)
try:
    import brain                       # noqa: E402
    import chat_history                # noqa: E402
    import main                        # noqa: E402
    from platform_tools import CURRENT_USER  # noqa: E402
    from claude_agent_sdk import SystemMessage, ResultMessage  # noqa: E402
    from fastapi.testclient import TestClient  # noqa: E402
    HAVE_SDK = True
except ImportError as e:               # main-env pytest sweep: no claude_agent_sdk
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(
            reason=f"needs the aihub-agent env (claude_agent_sdk): {_IMPORT_ERR}")
    except ImportError:
        pass

TEST_UID = 987654   # never a real user on this box


class patched:
    """Set attrs on an object for the duration of a block, then restore."""

    def __init__(self, obj, **attrs):
        self.obj, self.attrs, self.saved = obj, attrs, {}

    def __enter__(self):
        for k, v in self.attrs.items():
            self.saved[k] = getattr(self.obj, k)
            setattr(self.obj, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            setattr(self.obj, k, v)
        return False


class env:
    """Temporarily set/unset environment variables."""

    def __init__(self, **kv):
        self.kv, self.saved = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


def _result_message(session_id, ok=True):
    """Build a ResultMessage robustly against dataclass-field drift."""
    kw = {}
    for f in dataclasses.fields(ResultMessage):
        required = (f.default is dataclasses.MISSING
                    and f.default_factory is dataclasses.MISSING)
        if not required:
            continue
        kw[f.name] = {"subtype": "success" if ok else "error_during_execution",
                      "duration_ms": 1, "duration_api_ms": 1, "is_error": not ok,
                      "num_turns": 1, "session_id": session_id}.get(f.name, None)
    return ResultMessage(**kw)


# ----------------------------------------------------------- brain.run_turn

def _drive_turn(prompt, session_id, init_sid, user_ctx):
    """Run brain.run_turn with the SDK query stubbed; capture what a TOOL would
    read from the envelope right after the init message."""
    seen = {}

    async def fake_query(prompt=None, options=None, **kw):
        yield SystemMessage(subtype="init", data={"session_id": init_sid})
        seen["envelope"] = dict(CURRENT_USER.get())
        seen["inflight_during"] = brain.is_inflight(init_sid)
        yield _result_message(init_sid)

    async def go():
        events = []
        async for ev in brain.run_turn(prompt, session_id, user_ctx):
            events.append(ev)
        return events

    with patched(brain, query=fake_query, build_options=lambda *a, **k: None):
        events = asyncio.run(go())
    return events, seen


def test_run_turn_exposes_new_session_id_to_tools_and_marks_inflight():
    ctx = {"user_id": TEST_UID, "role": 3, "username": "defer-unit"}
    events, seen = _drive_turn("hi", None, "new-sid-1", ctx)
    assert seen["envelope"].get("session_id") == "new-sid-1"
    assert seen["inflight_during"] is True
    assert brain.is_inflight("new-sid-1") is False          # cleared after the turn
    assert [e["type"] for e in events][-1] == "result"
    assert events[-1]["session_id"] == "new-sid-1"


def test_run_turn_exposes_resumed_session_id_to_tools():
    ctx = {"user_id": TEST_UID, "role": 3, "username": "defer-unit"}
    events, seen = _drive_turn("again", "old-sid-9", "old-sid-9", ctx)
    assert seen["envelope"].get("session_id") == "old-sid-9"
    assert seen["inflight_during"] is True
    assert brain.is_inflight("old-sid-9") is False


def test_inflight_registry_is_counted_and_self_expiring():
    brain.mark_inflight("x-1"); brain.mark_inflight("x-1")
    assert brain.is_inflight("x-1")
    brain.clear_inflight("x-1")
    assert brain.is_inflight("x-1")                  # one holder still active
    brain.clear_inflight("x-1")
    assert not brain.is_inflight("x-1")
    brain.clear_inflight("x-1")                      # over-clear is harmless
    assert not brain.is_inflight("")
    brain.mark_inflight("stale-1")
    brain._INFLIGHT["stale-1"][1] -= brain.INFLIGHT_STALE_SECONDS + 5
    assert not brain.is_inflight("stale-1")          # a crashed turn never wedges a chat


# ------------------------------------------------------------ /api/run

class Harness:
    """/api/run with run_turn + the store + ownership stubbed; records everything."""

    def __init__(self, owns=True, fail_first_resume=False):
        self.calls, self.items, self.touched = [], [], []
        self.owns, self.fail_first_resume = owns, fail_first_resume

    def fake_run_turn(self, prompt, session_id, user_ctx, tool_scope="full"):
        calls, fail_first = self.calls, self.fail_first_resume

        async def gen():
            calls.append({"prompt": prompt, "session_id": session_id,
                          "user_ctx": dict(user_ctx), "tool_scope": tool_scope})
            resumes = [c for c in calls if c["session_id"]]
            if fail_first and session_id and len(resumes) == 1:
                yield {"type": "error", "error": "No conversation found with session ID",
                       "session_id": session_id}
                return
            yield {"type": "text", "text": "ran: " + prompt[-24:]}
            yield {"type": "result", "session_id": session_id or "fresh-sid",
                   "ok": True, "subtype": "success"}
        return gen()

    def fake_create_item(self, verb, title, **kw):
        rec = {"verb": verb, "title": title, **kw, "work_item_id": f"wi-{len(self.items) + 1}"}
        self.items.append(rec)
        return rec

    def post(self, body):
        with patched(main, run_turn=self.fake_run_turn,
                     _service_key_ok=lambda r: True), \
             patched(main.workitem_store, create_item=self.fake_create_item), \
             patched(chat_history, owns_session=lambda uid, sid: self.owns,
                     touch=lambda uid, sid, msg: self.touched.append((uid, sid))):
            client = TestClient(main.app)          # no lifespan (no pollers started)
            r = client.post("/api/run", json=body)
        return r


_RUN = {"prompt": "say the words pack ok", "user_id": TEST_UID, "role": 3,
        "username": "defer-unit", "job_name": "Agent: defer unit"}


def test_run_without_session_id_is_fresh_as_before():
    h = Harness()
    with env(AGENT_DEFER_TO_CHAT=None):
        r = h.post(dict(_RUN))
    assert r.status_code == 200, r.text
    assert h.calls[0]["session_id"] is None
    assert h.calls[0]["prompt"] == _RUN["prompt"]            # no scheduled-run framing
    d = r.json()
    assert d["ok"] and d["work_item_id"] == "wi-1" and d["resumed_chat"] is False
    assert h.items[0]["verb"] == "acknowledge"
    assert h.items[0]["payload"]["chat_session_id"] is None
    assert h.items[0]["addressed_user"] == TEST_UID and not h.touched


def test_run_with_session_id_resumes_that_conversation():
    h = Harness(owns=True)
    with env(AGENT_DEFER_TO_CHAT=None):
        r = h.post(dict(_RUN, session_id="conv-1"))
    assert r.status_code == 200, r.text
    c = h.calls[0]
    assert c["session_id"] == "conv-1"
    assert c["prompt"].startswith(chat_history.SCHEDULED_RUN_MARKER)
    assert "Agent: defer unit" in c["prompt"] and c["prompt"].endswith(_RUN["prompt"])
    assert c["user_ctx"].get("chat_session_id") == "conv-1"   # chaining seam for tools
    assert c["user_ctx"].get("mode") == "headless"
    d = r.json()
    assert d["resumed_chat"] is True and d["chat_session_id"] == "conv-1"
    item = h.items[0]
    assert item["payload"]["chat_session_id"] == "conv-1"     # deep-link
    assert item["payload"]["kind"] == "headless_run" and item["verb"] == "acknowledge"
    assert h.touched == [(TEST_UID, "conv-1")]                # floats to the top of history
    assert not brain.is_inflight("conv-1")


def test_run_flag_off_is_exactly_the_old_behavior():
    h = Harness(owns=True)
    with env(AGENT_DEFER_TO_CHAT="false"):
        r = h.post(dict(_RUN, session_id="conv-2"))
    assert r.status_code == 200
    assert h.calls[0]["session_id"] is None and h.calls[0]["prompt"] == _RUN["prompt"]
    assert h.items[0]["payload"]["chat_session_id"] is None
    assert r.json()["resumed_chat"] is False


def test_run_busy_session_falls_back_to_fresh():
    h = Harness(owns=True)
    brain.mark_inflight("conv-3")                # a chat turn is in flight on it
    try:
        with env(AGENT_DEFER_TO_CHAT=None):
            r = h.post(dict(_RUN, session_id="conv-3"))
        still_held = brain.is_inflight("conv-3")  # the live turn's mark is untouched
    finally:
        brain.clear_inflight("conv-3")
    assert r.status_code == 200
    assert [c["session_id"] for c in h.calls] == [None]       # no resume attempted
    assert h.items[0]["payload"]["chat_session_id"] is None
    assert still_held and not brain.is_inflight("conv-3")


def test_run_unowned_or_malformed_session_falls_back():
    h = Harness(owns=False)
    with env(AGENT_DEFER_TO_CHAT=None):
        r = h.post(dict(_RUN, session_id="conv-4"))
    assert r.status_code == 200 and h.calls[0]["session_id"] is None
    h = Harness(owns=True)
    with env(AGENT_DEFER_TO_CHAT=None):
        r = h.post(dict(_RUN, session_id="../../etc"))
    assert r.status_code == 200 and h.calls[0]["session_id"] is None


def test_run_resume_failure_before_any_work_retries_fresh():
    h = Harness(owns=True, fail_first_resume=True)
    with env(AGENT_DEFER_TO_CHAT=None):
        r = h.post(dict(_RUN, session_id="conv-5"))
    assert r.status_code == 200, r.text
    assert [c["session_id"] for c in h.calls] == ["conv-5", None]
    assert h.calls[1]["prompt"] == _RUN["prompt"]
    d = r.json()
    assert d["ok"] is True and d["resumed_chat"] is False
    assert h.items[0]["payload"]["chat_session_id"] is None
    assert not h.touched and not brain.is_inflight("conv-5")


# ------------------------------------------------------------ /api/chat wait

def test_chat_waits_while_a_deferred_run_holds_the_session():
    """While conv-6 is in flight, /api/chat emits a status line and waits until it
    clears (here: a background task clears it) before running the turn."""
    calls = []

    def fake_run_turn(prompt, session_id, user, tool_scope="full"):
        async def gen():
            calls.append({"session_id": session_id, "inflight_at_start": brain.is_inflight(session_id)})
            yield {"type": "text", "text": "ok"}
            yield {"type": "result", "session_id": session_id, "ok": True, "subtype": "success"}
        return gen()

    user = {"user_id": TEST_UID, "role": 3, "username": "defer-unit", "name": "", "tenant_id": ""}
    brain.mark_inflight("conv-6")
    import threading, time as _t
    threading.Timer(1.5, lambda: brain.clear_inflight("conv-6")).start()
    with patched(main, run_turn=fake_run_turn, _verify_request=lambda r: user,
                 CHAT_BUSY_WAIT_SECONDS=10), \
         patched(chat_history, touch=lambda *a, **k: None):
        client = TestClient(main.app)
        t0 = _t.time()
        r = client.post("/api/chat", json={"message": "hello", "session_id": "conv-6"})
        elapsed = _t.time() - t0
    assert r.status_code == 200
    body = r.text
    assert '"type": "status"' in body and "waiting for it to finish" in body
    assert calls and calls[0]["session_id"] == "conv-6"
    assert calls[0]["inflight_at_start"] is False         # it really waited
    assert 1.0 <= elapsed < 9.0, elapsed
    assert not brain.is_inflight("conv-6")


# --------------------------------------------------------------- replay tag

def test_replay_tags_scheduled_run_turns():
    tmp = tempfile.mkdtemp(prefix="agent-replay-")
    sid = "11111111-2222-3333-4444-555555555555"
    proj = os.path.join(tmp, "projects", "C--x-ws")
    os.makedirs(proj)
    deferred = chat_history.build_deferred_prompt("Agent: nightly", "2026-08-22 20:00",
                                                  "Check ERPDB and summarize.")
    lines = [
        {"type": "user", "message": {"role": "user", "content": "schedule a nightly check"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Scheduled."}]}},
        {"type": "user", "message": {"role": "user", "content": deferred}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "mcp__aihub__probe_connection_query", "input": {}}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "rows"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "ERPDB looks fine."}]}},
    ]
    with open(os.path.join(proj, f"{sid}.jsonl"), "w", encoding="utf-8") as f:
        for rec in lines:
            f.write(json.dumps(rec) + "\n")
    with patched(chat_history, CLAUDE_CONFIG_DIR=tmp):
        turns = chat_history.replay(sid)
    # (a tool_result user line closes an agent turn — existing replay contract —
    # so the tool round and the final text replay as two agent turns)
    assert [t["role"] for t in turns] == ["user", "agent", "user", "agent", "agent"]
    assert "kind" not in turns[0]
    d = turns[2]
    assert d["kind"] == "scheduled_run"
    assert "Agent: nightly" in d["header"] and "2026-08-22 20:00" in d["header"]
    assert d["text"] == "Check ERPDB and summarize."
    assert turns[3]["tools"] == ["probe_connection_query"]
    assert "ERPDB looks fine." in turns[4]["text"]


# -------------------------------------------------------------------- runner

if __name__ == "__main__":
    if not HAVE_SDK:
        print(f"SKIP-ALL: {_IMPORT_ERR}")
        sys.exit(0)
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in fns:
        try:
            f()
            print(f"PASS  {n}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {n}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {n}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
