"""The Agent's workflow file drops tools (agent_service/workflow_file_tools.py, 2026-10-10).

The platform's /api/workflow-files routes are faked; under test is the tools'
own contract: workflow resolution, the Developer gate on setup and sharing,
"run these files through the workflow" (add, run, wait, results as chat
links), the hand-back to My Work when the wait runs out or a run is queued,
and honest errors when the platform has file drops switched off.

Runs in the aihub-agent env (pytest, or standalone: python <this file>);
self-skips where claude_agent_sdk is missing or is the stub sibling tests
install for main-env imports.
"""
import asyncio
import os
import sys
from types import SimpleNamespace

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import claude_agent_sdk  # noqa: E402
    if not getattr(claude_agent_sdk, "__file__", None):
        raise ImportError("stub claude_agent_sdk (installed by a sibling test)")
    import workflow_file_tools as wft  # noqa: E402
    HAVE_SDK = True
except ImportError as e:
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(
            reason=f"needs the aihub-agent env (claude_agent_sdk): {_IMPORT_ERR}")
    except ImportError:
        pass

WID = 1605


class FakePlatform:
    """/api/workflow-files in memory."""

    def __init__(self, finished_after=2, queued=False, run_on_upload=False, disabled=False):
        self.calls = []
        self.uploads = []
        self.offered = []
        self.polls = 0
        self.finished_after = finished_after
        self.queued = queued
        self.run_on_upload = run_on_upload
        self.disabled = disabled
        self.record = {"execution_id": "E1", "status": "Completed",
                       "consumed": [{"name": "inv1.pdf", "uploaded_by": "jdoe"},
                                    {"name": "inv2.pdf", "uploaded_by": "jdoe"}],
                       "outputs": [{"name": "CI_Lines.xlsx", "size": 10, "new": False}],
                       "failed": ["inv2.pdf"], "processed": ["inv1.pdf"], "waiting_now": 0}

    async def call(self, method, path, *, body=None, params=None, timeout=60.0):
        self.calls.append((method, path, body, params))
        if self.disabled:
            return 404, {"status": "error", "disabled": True, "message": "off"}
        if path == "/api/workflow-files":
            out = {"status": "success", "can_manage": True,
                   "workflows": [{"workflow_id": WID, "name": "Acme Commercial Invoice Lines",
                                  "counts": {"inbox": 1, "outbox": 1, "failed": 0}},
                                 {"workflow_id": 1606, "name": "Acme Credit Notes", "counts": {}}]}
            if params and params.get("candidates"):
                out["candidates"] = [{"workflow_id": 1700, "name": "Acme Returns"}]
            return 200, out
        if path == f"/api/workflow-files/{WID}" and method == "GET":
            return 200, {"status": "success", "set_up": True, "name": "Acme Commercial Invoice Lines",
                         "counts": {"inbox": 0, "outbox": 2},
                         "settings": {"run_on_upload": self.run_on_upload},
                         "files": {"outbox": [{"name": "CI_Lines.xlsx", "size": 10, "modified": "x"},
                                              {"name": "2026/old.xlsx", "size": 5, "modified": "y"}]}}
        if path.endswith("/run"):
            if self.queued:
                return 200, {"status": "success", "queued": True, "message": "queued"}
            return 200, {"status": "success", "execution_id": "E1"}
        if path.endswith("/notify"):
            return 200, {"status": "success", "result": "queued"}
        if "/runs/" in path:
            self.polls += 1
            done = self.polls >= self.finished_after
            return 200, {"status": "success", "run_status": "Completed" if done else "Running",
                         "finished": done, "run": self.record if done else None}
        if path.endswith("/setup"):
            return 200, {"status": "success", "changes": {"inputFolder": {"from": "C:\\old", "to": "M\\inbox"}},
                         "settings": {"run_on_upload": bool((body or {}).get("run_on_upload"))}}
        if path.endswith("/access"):
            return 200, {"status": "success", "shared_with": {"users": [{"name": "Kim"}], "groups": [{"name": "AP"}]}}
        if path.endswith("/retry"):
            return 200, {"status": "success", "moved": [{"from": "inv2.pdf", "to": "inv2.pdf"}], "errors": []}
        return 404, {"status": "error", "message": f"unexpected {method} {path}"}

    async def upload(self, wid, path, name):
        self.uploads.append((wid, name))
        return name, None

    async def offer(self, wid, folder, name):
        self.offered.append((folder, name))
        return f"[⤓ {os.path.basename(name)} (10 B)](/api/files/{len(self.offered)})", None


def _wire(monkeypatch, api, allowed=True, files=None):
    monkeypatch.setattr(wft, "_call", api.call)
    monkeypatch.setattr(wft, "_upload", api.upload)
    monkeypatch.setattr(wft, "_offer", api.offer)
    monkeypatch.setattr(wft, "_authoring_allowed", lambda: allowed)
    monkeypatch.setattr(wft, "_user_context", lambda: {"user_id": 7, "role": 2 if allowed else 1,
                                                        "username": "jdoe"})
    monkeypatch.setattr(wft, "_resolve_file",
                        lambda ref: (files or {}).get(ref, (None, None, f"{ref} is not yours")))
    clock = iter(range(0, 10**6, 3))
    monkeypatch.setattr(wft, "asyncio", SimpleNamespace(sleep=_no_sleep))
    monkeypatch.setattr(wft, "time", SimpleNamespace(monotonic=lambda: next(clock)))


async def _no_sleep(_s):
    return None


def _run(tool_obj, args):
    out = asyncio.run(tool_obj.handler(args))
    return out["content"][0]["text"], bool(out.get("is_error"))


ATTACHED = {r"C:\app\data\agent\users\7\uploads\u1__inv1.pdf": (r"C:\x\inv1.pdf", "inv1.pdf", None),
            r"C:\app\data\agent\users\7\uploads\u2__inv2.pdf": (r"C:\x\inv2.pdf", "inv2.pdf", None)}


# ------------------------------------------------------------------ resolve
def test_resolve_by_id_name_and_partial(monkeypatch):
    api = FakePlatform()
    _wire(monkeypatch, api)
    assert asyncio.run(wft._resolve("1605"))[:2] == (WID, "Acme Commercial Invoice Lines")
    assert asyncio.run(wft._resolve("acme commercial invoice lines"))[0] == WID
    assert asyncio.run(wft._resolve("credit"))[0] == 1606
    _wid, _n, err = asyncio.run(wft._resolve("Acme"))
    assert "Several workflows match" in err
    _wid, _n, err = asyncio.run(wft._resolve("Payroll"))
    assert "No workflow named 'Payroll'" in err and "Acme Credit Notes" in err
    assert asyncio.run(wft._resolve("Returns"))[0] is None                       # not set up
    assert asyncio.run(wft._resolve("Returns", include_unset=True))[0] == 1700   # ...a Developer may set it up


def test_switched_off_platform_says_so(monkeypatch):
    api = FakePlatform(disabled=True)
    _wire(monkeypatch, api)
    text, is_err = _run(wft.list_workflow_files, {})
    assert is_err and "turned off" in text


# ---------------------------------------------------------------- the run
def test_run_these_files_through_the_workflow(monkeypatch):
    api = FakePlatform()
    _wire(monkeypatch, api, allowed=False, files=ATTACHED)       # a regular user it is shared with
    text, is_err = _run(wft.run_workflow_on_files, {"workflow": "Acme Commercial Invoice Lines",
                                                     "files": list(ATTACHED)})
    assert not is_err, text
    assert api.uploads == [(WID, "inv1.pdf"), (WID, "inv2.pdf")]
    run = [c for c in api.calls if c[1].endswith("/run")]
    assert run == [("POST", f"/api/workflow-files/{WID}/run", {"notify": False, "via": "the_agent"}, None)]
    assert text.startswith("Added to Waiting: inv1.pdf, inv2.pdf.")
    assert "finished" in text and "Failed (set aside" in text and "inv2.pdf" in text
    assert "include these links VERBATIM" in text and "(/api/files/1)" in text
    assert api.offered == [("outbox", "CI_Lines.xlsx")]
    assert not any(c[1].endswith("/notify") for c in api.calls)


def test_wait_runs_out_hands_over_to_my_work(monkeypatch):
    api = FakePlatform(finished_after=10**9)
    _wire(monkeypatch, api)
    text, is_err = _run(wft.run_workflow_on_files, {"workflow": "1605", "wait_seconds": 30})
    assert not is_err
    assert any(c[1] == f"/api/workflow-files/{WID}/runs/E1/notify" for c in api.calls)
    assert "My Work" in text and "Do not say it finished" in text and api.offered == []


def test_queued_run_is_reported_as_queued(monkeypatch):
    api = FakePlatform(queued=True)
    _wire(monkeypatch, api)
    text, _ = _run(wft.run_workflow_on_files, {"workflow": "1605"})
    assert "already going" in text and "My Work" in text and api.polls == 0


def test_files_that_are_not_the_users_are_refused(monkeypatch):
    api = FakePlatform()
    _wire(monkeypatch, api, files={})
    text, is_err = _run(wft.run_workflow_on_files, {"workflow": "1605", "files": [r"C:\Windows\x.pdf"]})
    assert is_err and "not yours" in text
    assert not any(c[1].endswith("/run") for c in api.calls)       # nothing ran


def test_add_only_and_run_on_arrival(monkeypatch):
    api = FakePlatform()
    _wire(monkeypatch, api, files=ATTACHED)
    text, _ = _run(wft.add_workflow_files, {"workflow": "1605", "files": list(ATTACHED)[:1]})
    assert "wait for the next run" in text and not any(c[1].endswith("/run") for c in api.calls)
    api = FakePlatform(run_on_upload=True)
    _wire(monkeypatch, api, files=ATTACHED)
    text, _ = _run(wft.add_workflow_files, {"workflow": "1605", "files": list(ATTACHED)})
    runs = [c for c in api.calls if c[1].endswith("/run")]
    assert len(runs) == 1 and runs[0][2] == {"via": "the_agent"}    # ONE run, results via My Work
    assert "My Work" in text


def test_get_files_hands_over_results(monkeypatch):
    api = FakePlatform()
    _wire(monkeypatch, api)
    text, is_err = _run(wft.get_workflow_files, {"workflow": "1605"})
    assert not is_err and api.offered == [("outbox", "CI_Lines.xlsx"), ("outbox", "2026/old.xlsx")]
    api.offered.clear()
    api.finished_after = 0                                    # that run has ended
    _run(wft.get_workflow_files, {"workflow": "1605", "execution_id": "E1"})
    assert api.offered == [("outbox", "CI_Lines.xlsx")]


def test_retry_then_run(monkeypatch):
    api = FakePlatform()
    _wire(monkeypatch, api)
    text, _ = _run(wft.retry_workflow_files, {"workflow": "1605", "names": ["inv2.pdf"], "run": True})
    assert text.startswith("Back in Waiting for 'Acme Commercial Invoice Lines': inv2.pdf.")
    assert "finished" in text


# ----------------------------------------------------- Developer-only tools
def test_setup_and_share_need_a_developer(monkeypatch):
    api = FakePlatform()
    _wire(monkeypatch, api, allowed=False)
    for t, args in ((wft.setup_workflow_files, {"workflow": "1605", "inbox_variable": "inputFolder"}),
                    (wft.share_workflow_files, {"workflow": "1605", "add_users": ["kim"]})):
        text, is_err = _run(t, args)
        assert is_err and "Developer" in text
    assert api.calls == []


def test_setup_points_variables_and_says_what_to_tell_the_user(monkeypatch):
    api = FakePlatform()
    _wire(monkeypatch, api)
    text, is_err = _run(wft.setup_workflow_files, {"workflow": "Returns", "inbox_variable": "inputFolder",
                                                    "outbox_variable": "outputWorkbook", "run_on_upload": True})
    assert not is_err
    setup = [c for c in api.calls if c[1].endswith("/setup")][0]
    assert setup[1] == "/api/workflow-files/1700/setup"
    assert setup[2] == {"variables": {"inbox": "inputFolder", "processed": "", "failed": "",
                                      "outbox": "outputWorkbook"}, "run_on_upload": True}
    assert "Workflow Files (in AI Hub's menu" in text and "Do not show server folder paths" in text


def test_share(monkeypatch):
    api = FakePlatform()
    _wire(monkeypatch, api)
    text, _ = _run(wft.share_workflow_files, {"workflow": "1605", "add_users": ["kim"], "add_groups": ["AP"]})
    put = [c for c in api.calls if c[1].endswith("/access")][0]
    assert put[2] == {"add_users": ["kim"], "add_groups": ["AP"]} and "Kim" in text and "group AP" in text


# ------------------------------------------------------------- registration
def test_brain_registers_the_tools_with_their_kill_switch():
    src = open(os.path.join(APP_ROOT, "agent_service", "brain.py"), encoding="utf-8").read()
    assert "(WORKFLOW_FILE_TOOLS if _WORKFLOW_FILE_TOOLS_ON else [])" in src
    assert 'os.getenv("AGENT_WORKFLOW_FILE_TOOLS", "true")' in src
    for name in ("setup_workflow_files", "add_workflow_files", "run_workflow_on_files",
                 "retry_workflow_files", "share_workflow_files", "list_workflow_files"):
        assert f'"{name}"' in src, name
    if HAVE_SDK:
        names = [t.name for t in wft.WORKFLOW_FILE_TOOLS]
        assert len(names) == len(set(names)) == 7


if __name__ == "__main__":
    # The aihub-agent env has no pytest: run each test with a minimal
    # monkeypatch stand-in.
    import inspect

    class _MonkeyPatch:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, old in reversed(self._undo):
                setattr(obj, name, old)
            self._undo.clear()

    if not HAVE_SDK:
        print(f"SKIP-ALL: {_IMPORT_ERR}")
        sys.exit(0)
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in fns:
        mp = _MonkeyPatch()
        try:
            f(mp) if "monkeypatch" in inspect.signature(f).parameters else f()
            print(f"PASS  {n}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {n}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {n}: {type(e).__name__}: {e}")
        finally:
            mp.undo()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
