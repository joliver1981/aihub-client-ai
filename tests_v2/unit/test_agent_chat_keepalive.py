"""The Agent chat stream keepalive (2026-11-13) — unit tests.

POST /api/chat is a text/event-stream that sends nothing while a tool step runs.
Behind a reverse proxy an idle connection is dropped at the proxy's read timeout
and the turn is lost. main.KeepaliveStreamingResponse writes an SSE comment
line (": keepalive") whenever nothing has gone out for
AGENT_CHAT_KEEPALIVE_SECONDS (default 15, 0 = off).

Pinned here:
  * a keepalive comment is emitted during a silent gap, and only there;
  * the data events — content and order — are exactly what they are with the
    keepalive off (the stream minus comment lines is byte-for-byte the same);
  * real events are not held back by the timer, and its write never lands
    inside or after a real one;
  * the turn still runs in the task that serves the request, so an error in it
    surfaces as before and a dropped client stops it as before;
  * the setting parses safely (0 / negative = off, junk = default);
  * the UI reader skips anything that is not a "data: " chunk.

No live services and no LLM: run_turn is stubbed on the REAL main module. Runs
standalone (aihub-agent python test_agent_chat_keepalive.py) or under pytest in
an env with claude_agent_sdk; in an env WITHOUT the SDK every test self-skips.
"""
import asyncio
import json
import os
import sys
import time
import warnings

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

warnings.filterwarnings("ignore", category=DeprecationWarning)
try:
    import claude_agent_sdk as _sdk    # noqa: E402
    # Sibling test files install a bare stub under this name in the main env.
    # A stub has no __file__; only the real SDK can import agent_service/main.py.
    if not getattr(_sdk, "__file__", None):
        raise ImportError("claude_agent_sdk is a test stub, not the real SDK")
    import agent_config                # noqa: E402
    import chat_history                # noqa: E402
    import main                        # noqa: E402
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
USER = {"user_id": TEST_UID, "role": 3, "username": "keepalive-unit", "name": "", "tenant_id": ""}

EVENTS = [
    {"type": "init", "tools": ["query_database"], "mcp_servers": [], "model": "m"},
    {"type": "text", "text": "Running it now.\n\nTwo paragraphs: a blank line inside an event."},
    {"type": "tool", "id": "t1", "name": "mcp__aihub__query_database", "input": {"sql": "SELECT 1"}},
    # ---- the silent tool step sits here ----
    {"type": "tool_result", "id": "t1", "ok": True, "preview": ": keepalive is just text here"},
    {"type": "text", "text": "Done."},
    {"type": "result", "session_id": "s-keepalive", "ok": True, "subtype": "success", "cost_usd": 0.01},
]
GAP_AFTER = 3        # events sent before the silent step
GAP_SECONDS = 0.6
INTERVAL = 0.1


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


def fake_run_turn(prompt, session_id, user, tool_scope="full"):
    async def gen():
        for i, ev in enumerate(EVENTS):
            if i == GAP_AFTER:
                await asyncio.sleep(GAP_SECONDS)       # a tool step: nothing to send
            yield ev
    return gen()


def ui_events(body: str):
    """Read the stream the way static/index.html does: split on a blank line,
    skip any chunk that does not start with 'data: ', JSON-parse the rest."""
    out = []
    for chunk in body.split("\n\n"):
        if not chunk.startswith("data: "):
            continue
        try:
            out.append(json.loads(chunk[6:]))
        except ValueError:
            continue
    return out


def script_events(body: str):
    """Read the stream the way the test packs do (requests.iter_lines)."""
    return [json.loads(line[6:]) for line in body.splitlines()
            if line and line.startswith("data: ")]


def post_chat(keepalive_seconds):
    with patched(main, run_turn=fake_run_turn, _verify_request=lambda r: dict(USER),
                 CHAT_KEEPALIVE_SECONDS=keepalive_seconds), \
         patched(chat_history, touch=lambda *a, **k: None):
        r = TestClient(main.app).post("/api/chat", json={"message": "run the report"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    return r.text


# ------------------------------------------------------------------ endpoint

def test_keepalive_is_sent_during_a_silent_gap_and_the_events_are_unchanged():
    off = post_chat(0)
    on = post_chat(INTERVAL)

    # Off: the stream as it always was — not one comment line.
    assert main.SSE_KEEPALIVE not in off
    expected = EVENTS + [{"type": "done"}]
    assert ui_events(off) == expected

    # On: comment lines appear...
    n = on.count(main.SSE_KEEPALIVE)
    assert n >= 2, f"expected keepalives during a {GAP_SECONDS}s gap at {INTERVAL}s, got {n}"
    # ...the events every reader sees are the same, in the same order...
    assert ui_events(on) == expected
    assert script_events(on) == expected
    # ...and with the comment lines taken out the stream is byte-for-byte the old one.
    assert on.replace(main.SSE_KEEPALIVE, "") == off


def test_keepalives_sit_only_in_the_silent_gap():
    on = post_chat(INTERVAL)
    frames = [f for f in on.split("\n\n") if f]
    kinds = ["k" if f == main.SSE_KEEPALIVE.strip() else "d" for f in frames]
    first_k, last_k = kinds.index("k"), len(kinds) - 1 - kinds[::-1].index("k")
    assert first_k == GAP_AFTER, kinds                    # right after the tool event
    assert set(kinds[first_k:last_k + 1]) == {"k"}, kinds  # one unbroken run
    assert kinds[last_k + 1:] == ["d"] * (len(EVENTS) - GAP_AFTER + 1), kinds
    # a comment line never carries data and never looks like an event
    assert main.SSE_KEEPALIVE.startswith(":") and main.SSE_KEEPALIVE.endswith("\n\n")
    assert "data:" not in main.SSE_KEEPALIVE


def test_busy_wait_is_covered_too():
    """The bounded wait for a deferred run (silent 1 s sleeps) gets keepalives."""
    import brain
    import threading
    brain.mark_inflight("conv-keepalive")
    threading.Timer(0.8, lambda: brain.clear_inflight("conv-keepalive")).start()
    with patched(main, run_turn=fake_run_turn, _verify_request=lambda r: dict(USER),
                 CHAT_KEEPALIVE_SECONDS=INTERVAL, CHAT_BUSY_WAIT_SECONDS=10), \
         patched(chat_history, touch=lambda *a, **k: None):
        r = TestClient(main.app).post("/api/chat", json={"message": "hello",
                                                         "session_id": "conv-keepalive"})
    assert not brain.is_inflight("conv-keepalive")
    frames = [f for f in r.text.split("\n\n") if f]
    assert json.loads(frames[0][6:])["type"] == "status"
    assert frames[1] == main.SSE_KEEPALIVE.strip()         # quiet while waiting
    assert [e["type"] for e in ui_events(r.text)] == \
        ["status"] + [e["type"] for e in EVENTS] + ["done"]


# ------------------------------------------------------------------ response

def _drive(make_gen, interval, send_delay=0.0, cancel_after=None, linger=0.0, send=None):
    """Run KeepaliveStreamingResponse.stream_response against a recording send.
    Returns (messages, error, tasks_left, info)."""
    sent, info = [], {"in_send": 0, "overlap": False, "request_task": None}

    async def recording_send(message):
        info["in_send"] += 1
        info["overlap"] = info["overlap"] or info["in_send"] > 1
        try:
            if send_delay:
                await asyncio.sleep(send_delay)
            if send is not None:
                send(message)
            sent.append(message)
        finally:
            info["in_send"] -= 1

    async def run():
        resp = main.KeepaliveStreamingResponse(make_gen(), interval,
                                               media_type="text/event-stream")
        error = None

        async def request():
            info["request_task"] = asyncio.current_task()
            await resp.stream_response(recording_send)

        task = asyncio.create_task(request())
        if cancel_after is not None:
            await asyncio.sleep(cancel_after)
            task.cancel()
        try:
            await task
        except BaseException as e:      # the test inspects it
            error = e
        info["sent_when_over"] = len(sent)
        await asyncio.sleep(linger)
        return error, len(asyncio.all_tasks())

    error, tasks_left = asyncio.run(run())
    return sent, error, tasks_left, info


def _bodies(sent):
    return [m["body"].decode("utf-8") for m in sent if m["type"] == "http.response.body"]


def test_real_frames_are_not_held_back_by_the_timer():
    """A long interval must not slow a stream that has something to send."""
    frames = [f"data: {i}\n\n" for i in range(500)]

    async def src():
        for f in frames:
            yield f
            await asyncio.sleep(0)

    t0 = time.time()
    sent, error, tasks_left, _ = _drive(src, 30.0)
    assert error is None and time.time() - t0 < 5.0
    assert sent[0]["type"] == "http.response.start"
    assert _bodies(sent) == frames + [""]                 # nothing added, lost or reordered
    assert sent[-1].get("more_body", False) is False
    assert tasks_left == 1


def test_timer_restarts_after_each_frame():
    """Frames arriving faster than the interval -> no keepalive at all."""
    async def src():
        for i in range(6):
            await asyncio.sleep(0.05)
            yield f"data: {i}\n\n"

    sent, error, _, _ = _drive(src, 0.5)
    assert error is None
    assert _bodies(sent) == [f"data: {i}\n\n" for i in range(6)] + [""]


def test_keepalive_never_lands_inside_or_after_a_real_write():
    """Slow writes (a slow client): the timer's write waits its turn, and
    nothing at all is written once the final chunk has gone out."""
    async def src():
        yield "data: 1\n\n"
        await asyncio.sleep(0.5)
        yield "data: 2\n\n"

    sent, error, tasks_left, info = _drive(src, 0.1, send_delay=0.03, linger=0.5)
    assert error is None and info["overlap"] is False
    bodies = _bodies(sent)
    assert bodies[0] == "data: 1\n\n" and bodies[-2:] == ["data: 2\n\n", ""]
    middle = bodies[1:-2]
    assert len(middle) >= 2 and set(middle) == {main.SSE_KEEPALIVE}, bodies
    assert sent[0]["type"] == "http.response.start"       # never before the response starts
    assert len(sent) == info["sent_when_over"]            # ...and never after it ends
    assert tasks_left == 1, "the keepalive timer outlived the response"


def test_turn_runs_in_the_request_task():
    """The turn must be produced by the task that serves the request — exactly
    as a plain StreamingResponse does it — so stopping the request stops the
    turn the way it always has. (A first design moved the turn to a relay task;
    live, a dropped client then left the turn's work running.)"""
    seen = {}

    async def src():
        seen["task"] = asyncio.current_task()
        yield "data: 1\n\n"
        await asyncio.sleep(0.25)
        seen["task_after_gap"] = asyncio.current_task()
        yield "data: 2\n\n"

    sent, error, _, info = _drive(src, 0.1)
    assert error is None and main.SSE_KEEPALIVE in _bodies(sent)
    assert seen["task"] is info["request_task"]
    assert seen["task_after_gap"] is info["request_task"]


def test_stream_error_still_reaches_the_caller_after_its_frames():
    async def src():
        yield "data: 1\n\n"
        await asyncio.sleep(0.25)
        raise RuntimeError("boom")

    sent, error, tasks_left, _ = _drive(src, 0.1, linger=0.3)
    assert isinstance(error, RuntimeError) and str(error) == "boom"
    bodies = _bodies(sent)
    assert bodies[0] == "data: 1\n\n" and len(bodies) >= 2
    assert set(bodies[1:]) == {main.SSE_KEEPALIVE}
    assert tasks_left == 1, "the keepalive timer outlived the response"


def test_cancelling_the_request_stops_the_turn_and_the_timer():
    """Client gone mid-step: the turn is cancelled where it stands (its finally
    runs), and no keepalive is written afterwards."""
    state = {"cancelled": False, "finalized": False, "after_gap": False}

    async def src():
        try:
            yield "data: 1\n\n"
            await asyncio.sleep(30)
            state["after_gap"] = True
            yield "data: 2\n\n"
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
        finally:
            state["finalized"] = True

    t0 = time.time()
    sent, error, tasks_left, info = _drive(src, 0.05, cancel_after=0.3, linger=0.4)
    assert time.time() - t0 < 5.0
    assert isinstance(error, asyncio.CancelledError)
    assert state == {"cancelled": True, "finalized": True, "after_gap": False}, state
    assert main.SSE_KEEPALIVE in _bodies(sent)
    assert len(sent) == info["sent_when_over"], "a keepalive was written after the cancel"
    assert tasks_left == 1, "the keepalive timer outlived the response"


def test_a_failed_keepalive_write_does_not_end_the_turn():
    """If the timer's own write fails, the turn carries on and finishes."""
    def failing(message):
        if message.get("body") == main.SSE_KEEPALIVE.encode("utf-8"):
            raise OSError("write failed")

    async def src():
        yield "data: 1\n\n"
        await asyncio.sleep(0.3)
        yield "data: 2\n\n"

    sent, error, tasks_left, _ = _drive(src, 0.1, send=failing, linger=0.2)
    assert error is None
    assert _bodies(sent) == ["data: 1\n\n", "data: 2\n\n", ""]
    assert tasks_left == 1


def test_off_uses_the_plain_response():
    """0 = off: the endpoint returns the plain StreamingResponse it always did."""
    made = []
    real_init = main.StreamingResponse.__init__

    def spy(self, *a, **k):
        made.append(type(self))
        real_init(self, *a, **k)

    with patched(main.StreamingResponse, __init__=spy):
        post_chat(0)
        post_chat(INTERVAL)
    assert made == [main.StreamingResponse, main.KeepaliveStreamingResponse], made


# ------------------------------------------------------------------- setting

def test_setting_parses_safely():
    saved = os.environ.get("AGENT_CHAT_KEEPALIVE_SECONDS")
    try:
        os.environ.pop("AGENT_CHAT_KEEPALIVE_SECONDS", None)
        assert agent_config._chat_keepalive_seconds() == 15.0     # default
        for raw, want in (("0", 0.0), ("-5", 0.0), ("7.5", 7.5), (" 20 ", 20.0),
                          ("abc", 15.0), ("", 15.0)):
            os.environ["AGENT_CHAT_KEEPALIVE_SECONDS"] = raw
            assert agent_config._chat_keepalive_seconds() == want, raw
    finally:
        if saved is None:
            os.environ.pop("AGENT_CHAT_KEEPALIVE_SECONDS", None)
        else:
            os.environ["AGENT_CHAT_KEEPALIVE_SECONDS"] = saved
    assert "chat_keepalive_seconds" in agent_config.summary()   # visible on /health


# ------------------------------------------------------------------ UI reader

def test_ui_reader_skips_non_data_chunks():
    """The wire format relies on this line of the chat reader: a chunk that does
    not start with 'data: ' (a comment line) is ignored."""
    with open(os.path.join(APP_ROOT, "agent_service", "static", "index.html"),
              encoding="utf-8") as f:
        html = f.read()
    at = html.index('fetch("/api/chat", { method: "POST"')
    reader = html[at:at + 2500]
    assert 'buf.indexOf("\\n\\n")' in reader
    assert 'if (!chunk.startsWith("data: ")) continue;' in reader


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
