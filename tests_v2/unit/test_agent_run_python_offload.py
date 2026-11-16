"""The Agent's Python lane no longer holds the service's event loop (2026-11-16).

code_tools.execute_python (run_python, and export_data / manipulate_pdf through
it) used to call the script runner inline. For as long as the script ran, the
whole service answered nothing: other users' turns, the UI's own calls, and the
chat stream's keepalive — so behind a reverse proxy a Python step longer than
the proxy's idle timeout still lost the turn. The runner now runs in a worker
thread; AGENT_RUN_PYTHON_OFFLOAD=false restores the inline call.

Pinned here:
  * while a script runs, the event loop keeps running (and with the kill switch
    it does not — the test can tell the difference);
  * the result handed back is the same either way;
  * two runs overlap instead of queueing, each in its own work dir;
  * a turn cancelled mid-script still lets the script end before its work dir
    is removed, and the cancel then goes through;
  * a real script still runs end to end (skipped where no interpreter is set up).

The runner is stubbed (no interpreter) except in the last test. Runs standalone
(aihub-agent python test_agent_run_python_offload.py) or under pytest in an env
with claude_agent_sdk; in an env WITHOUT the SDK every test self-skips.
"""
import asyncio
import os
import sys
import tempfile
import threading
import time
import warnings
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

warnings.filterwarnings("ignore", category=DeprecationWarning)
try:
    import claude_agent_sdk as _sdk    # noqa: E402
    # Sibling test files install a bare stub under this name in the main env.
    if not getattr(_sdk, "__file__", None):
        raise ImportError("claude_agent_sdk is a test stub, not the real SDK")
    import code_exec                   # noqa: E402
    import code_tools as C             # noqa: E402
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
SCRIPT_SECONDS = 0.6


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


class FakeRunner:
    """Stands in for code_exec.run_script: blocks like a real script would."""

    def __init__(self, seconds=SCRIPT_SECONDS, stdout="ok\n", returncode=0, timed_out=False):
        self.seconds, self.stdout = seconds, stdout
        self.returncode, self.timed_out = returncode, timed_out
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, code, workdir, python_exe, timeout, env, preamble="", **kw):
        call = {"thread": threading.current_thread(), "workdir": workdir, "code": code,
                "start": time.time(), "workdir_at_start": os.path.isdir(workdir)}
        with self.lock:
            self.calls.append(call)
        time.sleep(self.seconds)                         # a script that takes a while
        call["workdir_at_end"] = os.path.isdir(workdir)
        call["end"] = time.time()
        return {"stdout": self.stdout, "stderr": "boom" if self.returncode else "",
                "returncode": self.returncode, "timed_out": self.timed_out}


def _sandbox(runner, offload):
    """Everything execute_python needs except a real interpreter and the SDK
    token; the invocation ledger goes to a temp file, not the shared log."""
    ledger = os.path.join(tempfile.mkdtemp(prefix="agent-runpy-test-"), "ledger.jsonl")
    stack = [
        mock.patch("code_exec.resolve_interpreter", lambda *a, **k: sys.executable),
        mock.patch("code_exec.run_script", runner),
        env(AGENT_RUN_PYTHON_SDK="false",
            AGENT_RUN_PYTHON_OFFLOAD=None if offload else "false"),
    ]
    try:
        import CommonUtils  # noqa: F401
        stack.append(mock.patch("CommonUtils.get_log_path", lambda name: ledger))
    except Exception:
        pass
    return stack


class sandbox:
    def __init__(self, runner, offload=True):
        self.stack = _sandbox(runner, offload)

    def __enter__(self):
        for s in self.stack:
            s.__enter__()
        return self

    def __exit__(self, *exc):
        for s in reversed(self.stack):
            s.__exit__(*exc)
        return False


async def _run_with_heartbeat(make_coro):
    """Await the coroutine while a 20 ms heartbeat counts how often the event
    loop got to run in the meantime."""
    beats = {"n": 0}

    async def heartbeat():
        while True:
            await asyncio.sleep(0.02)
            beats["n"] += 1

    hb = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    t0 = time.time()
    try:
        result = await make_coro()
    finally:
        seen = beats["n"]
        hb.cancel()
    return result, seen, time.time() - t0


def _execute(code="print('ok')"):
    return C.execute_python(TEST_UID, code, stage_uploads=False, lane="unit")


# --------------------------------------------------------------- event loop

def test_event_loop_keeps_running_while_the_script_runs():
    runner = FakeRunner()
    with sandbox(runner):
        r, beats, elapsed = asyncio.run(_run_with_heartbeat(_execute))
    assert r["ok"] is True and r["output"] == "ok\n" and r["error"] is None
    assert elapsed >= SCRIPT_SECONDS
    assert beats >= 10, f"the event loop ran only {beats} time(s) during a {SCRIPT_SECONDS}s script"
    assert runner.calls[0]["thread"] is not threading.main_thread()


def test_kill_switch_runs_inline_as_before():
    """AGENT_RUN_PYTHON_OFFLOAD=false: the old inline call — and proof that the
    heartbeat above really detects a held event loop."""
    runner = FakeRunner()
    with sandbox(runner, offload=False):
        r, beats, _ = asyncio.run(_run_with_heartbeat(_execute))
    assert r["ok"] is True and r["output"] == "ok\n"
    assert runner.calls[0]["thread"] is threading.main_thread()
    assert beats <= 2, f"inline run should hold the loop, yet it ran {beats} time(s)"


def test_result_is_the_same_either_way():
    cases = (dict(stdout="42\n", returncode=0),
             dict(stdout="partial", returncode=1),
             dict(stdout="", returncode=-1, timed_out=True))
    for kw in cases:
        got = []
        for offload in (True, False):
            with sandbox(FakeRunner(seconds=0.05, **kw), offload=offload):
                got.append(asyncio.run(_execute()))
        assert got[0] == got[1], (kw, got)
    assert got[0]["timed_out"] is True and "timed out" in got[0]["error"]


# -------------------------------------------------------------- concurrency

def test_two_runs_overlap_each_in_its_own_work_dir():
    runner = FakeRunner()

    async def both():
        return await asyncio.gather(_execute("print(1)"), _execute("print(2)"))

    with sandbox(runner):
        results = asyncio.run(both())
    assert all(r["ok"] for r in results)
    a, b = runner.calls
    assert a["workdir"] != b["workdir"]
    # they overlapped in time (inline, the second could only start after the first ended)
    assert a["start"] < b["end"] and b["start"] < a["end"], "the two runs queued"
    assert a["workdir_at_end"] and b["workdir_at_end"]
    assert not os.path.isdir(a["workdir"]) and not os.path.isdir(b["workdir"])


def _wait_for(cond, seconds=5.0):
    deadline = time.time() + seconds
    while not cond() and time.time() < deadline:
        time.sleep(0.02)
    return cond()


async def _script_started(runner, seconds=10.0):
    """Stop the turn only once its script is really running (mid-script)."""
    deadline = time.time() + seconds
    while not runner.calls and time.time() < deadline:
        await asyncio.sleep(0.01)
    assert runner.calls, "the script never started"
    await asyncio.sleep(0.1)


def test_turn_stopped_by_the_server_waits_for_the_script_then_cleans_up():
    """Client gone mid-script, the way the service stops a turn (a cancel
    scope): the wait is not abandoned — the script ends first, its work dir
    still there, and only then is the turn let go and the work dir removed."""
    import anyio
    runner = FakeRunner()
    seen = {}

    async def turn():
        try:
            await _execute()
        finally:
            seen["turn_over"] = time.time()

    async def run():
        async with anyio.create_task_group() as tg:
            tg.start_soon(turn)
            await _script_started(runner)
            tg.cancel_scope.cancel()

    with sandbox(runner):
        asyncio.run(run())
    call = runner.calls[0]
    assert "end" in call, "the script was abandoned mid-run"
    assert call["workdir_at_end"] is True, "the work dir was removed under a running script"
    assert seen["turn_over"] >= call["end"] - 0.01     # the stop waited for the script
    assert not os.path.isdir(call["workdir"])          # ...and the work dir is gone after


def test_plain_task_cancel_never_removes_the_work_dir_under_a_running_script():
    """A bare task.cancel() cannot be held off: the turn ends at once. The
    script must still find its work dir until it ends, and the work dir must
    be removed afterwards (by the script's own thread)."""
    runner = FakeRunner()

    async def run():
        task = asyncio.create_task(_execute())
        await _script_started(runner)
        task.cancel()
        try:
            await task
            return "not cancelled"
        except asyncio.CancelledError:
            return "cancelled"

    with sandbox(runner):
        outcome = asyncio.run(run())
        call = runner.calls[0]
        assert outcome == "cancelled"
        assert _wait_for(lambda: "end" in call), "the script never ended"
        assert call["workdir_at_end"] is True, "the work dir was removed under a running script"
        assert _wait_for(lambda: not os.path.isdir(call["workdir"])), \
            "the work dir was left behind after the script ended"


# ------------------------------------------------------------ real interpreter

def test_a_real_script_still_runs_end_to_end():
    python_exe = code_exec.resolve_interpreter()
    if not python_exe:
        print("      (no code interpreter configured on this box — real run not exercised)")
        return
    ledger = os.path.join(tempfile.mkdtemp(prefix="agent-runpy-test-"), "ledger.jsonl")
    patches = [env(AGENT_RUN_PYTHON_SDK="false", AGENT_RUN_PYTHON_OFFLOAD=None)]
    try:
        import CommonUtils  # noqa: F401
        patches.append(mock.patch("CommonUtils.get_log_path", lambda name: ledger))
    except Exception:
        pass
    for p in patches:
        p.__enter__()
    try:
        code = "import time\ntime.sleep(0.5)\nprint(6 * 7)\n"
        r, beats, _ = asyncio.run(_run_with_heartbeat(lambda: _execute(code)))
    finally:
        for p in reversed(patches):
            p.__exit__(None, None, None)
    assert r["configured"] is True and r["ok"] is True, r
    assert "42" in r["output"]
    assert beats >= 10, f"the event loop ran only {beats} time(s) during a real script"


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
