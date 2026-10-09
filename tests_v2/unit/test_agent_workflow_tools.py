"""The Agent's visual-workflow tools (agent_service/workflow_tools.py, 2026-10-09).

The platform routes are faked; what is under test is the tools' own contract:
the definition normaliser, the Developer gate (the main app trusts The Agent's
API key, so the role check lives in the tool), the no-silent-overwrite rule,
the save read-back, the run's per-run variables and its waiting/reporting.

Runs in the aihub-agent env (pytest, or standalone: python <this file>);
self-skips where claude_agent_sdk is missing or is the stub sibling tests
install for main-env imports.
"""
import asyncio
import json
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
    import workflow_tools as wt  # noqa: E402
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


class FakePlatform:
    """The main-app routes the tools call, in memory."""

    def __init__(self, workflows=None, statuses=None, run_resp=None, save_resp=None):
        self.workflows = list(workflows or [])
        self.statuses = list(statuses or [])
        self.run_resp = run_resp if run_resp is not None else {"status": "success", "execution_id": "E1"}
        self.save_resp = save_resp
        self.stored = None
        self.posts = []

    async def get(self, path, timeout=None):
        if path.startswith("/api/workflows/list"):
            return {"workflows": self.workflows}
        if path.startswith("/get/workflow/"):
            return self.stored
        if path.startswith("/api/workflow/executions/"):
            st = self.statuses.pop(0) if self.statuses else "Running"
            if isinstance(st, Exception):
                raise st
            return {"execution_id": "E1", "status": st}
        if path.startswith("/api/workflow/builder/run-report"):
            return {"status": "success", "text": "RUN REPORT — 3 files found, 3 rows written", "flags": []}
        raise AssertionError(f"unexpected GET {path}")

    async def post(self, path, body, timeout=None):
        self.posts.append((path, body))
        if path == "/save/workflow":
            if self.save_resp is not None:
                return self.save_resp
            self.stored = json.loads(json.dumps(body["workflow"]))
            return {"status": "success", "workflow_id": 42, "is_valid": True,
                    "validation_errors": [], "validation_warnings": []}, 200
        if path == "/api/workflow/run":
            return self.run_resp, 200
        raise AssertionError(f"unexpected POST {path}")


def _wire(monkeypatch, api, allowed=True):
    monkeypatch.setattr(wt, "_get", api.get)
    monkeypatch.setattr(wt, "_post", api.post)
    monkeypatch.setattr(wt, "_authoring_allowed", lambda: allowed)
    monkeypatch.setattr(wt, "_user_context", lambda: {"user_id": 7, "role": 2, "username": "jdoe"})
    # No real waiting: sleeps return at once and each clock read moves 3 s on.
    clock = iter(range(0, 10**6, 3))
    monkeypatch.setattr(wt, "asyncio", SimpleNamespace(sleep=_no_sleep))
    monkeypatch.setattr(wt, "time", SimpleNamespace(monotonic=lambda: next(clock)))


async def _no_sleep(_s):
    return None


def _call(tool_obj, args):
    out = asyncio.run(tool_obj.handler(args))
    return out["content"][0]["text"], bool(out.get("is_error"))


DEFN = {"nodes": [{"id": "a", "type": "Folder Selector", "isStart": True,
                   "config": {"folderPath": "${inputFolder}"}},
                  {"id": "b", "type": "File", "config": {"operation": "move"}}],
        "connections": [{"from": "a", "to": "b"}],
        "variables": {"inputFolder": "C:\\in"}}


# ------------------------------------------------------------ normaliser

def test_normalise_fills_designer_shape():
    out, err = wt._normalise_definition(json.loads(json.dumps(DEFN)))
    assert err is None
    a, b = out["nodes"]
    assert a["label"] == "Folder Selector" and isinstance(a["position"], dict)
    assert b["isStart"] is False and b["position"] != a["position"]
    assert out["connections"] == [{"source": "a", "target": "b", "type": "pass",
                                   "sourceAnchor": "Right", "targetAnchor": "Left"}]
    assert out["variables"]["inputFolder"] == {"name": "inputFolder", "type": "string",
                                               "defaultValue": "C:\\in"}


def test_normalise_rejects_dangling_and_duplicate_ids():
    bad = {"nodes": [{"id": "a", "type": "File"}], "connections": [{"source": "a", "target": "zz"}]}
    _, err = wt._normalise_definition(bad)
    assert err and "zz" in err
    dup = {"nodes": [{"id": "a", "type": "File"}, {"id": "a", "type": "Loop"}], "connections": []}
    _, err = wt._normalise_definition(dup)
    assert err and "unique" in err


def test_normalise_keeps_other_keys_and_stamps_portal_owner():
    d = {"description": "keep me", "nodes": [
        {"id": "p", "type": "Portal", "config": {}},
        {"id": "q", "type": "Portal", "config": {"ownerUserId": "3"}}], "connections": []}
    out, err = wt._normalise_definition(d, user_id=7)
    assert err is None and out["description"] == "keep me"
    assert out["nodes"][0]["config"]["ownerUserId"] == "7"
    assert out["nodes"][1]["config"]["ownerUserId"] == "3"      # never overwritten
    out, _ = wt._normalise_definition(json.loads(json.dumps(d)), user_id=0)
    assert "ownerUserId" not in out["nodes"][0]["config"]       # no identity, no stamp


# ------------------------------------------------------------ gate

def test_everything_but_the_reference_needs_a_developer(monkeypatch):
    api = FakePlatform(workflows=[{"id": 5, "workflow_name": "X"}])
    _wire(monkeypatch, api, allowed=False)
    for tool_obj, args in ((wt.list_workflows, {}), (wt.get_workflow, {"workflow": "5"}),
                           (wt.save_workflow, {"name": "Y", "definition_json": json.dumps(DEFN)}),
                           (wt.run_workflow, {"workflow": "5"}),
                           (wt.get_workflow_run_report, {"execution_id": "E1"})):
        text, is_err = _call(tool_obj, args)
        assert is_err and "Developer" in text, tool_obj.name
    assert api.posts == []


# ------------------------------------------------------------ save

def test_save_refuses_to_overwrite_without_replace_existing(monkeypatch):
    api = FakePlatform(workflows=[{"id": 9, "workflow_name": "Invoice Lines"}])
    _wire(monkeypatch, api)
    text, is_err = _call(wt.save_workflow, {"name": "invoice lines", "definition_json": json.dumps(DEFN)})
    assert is_err and "already exists" in text and "id 9" in text
    assert api.posts == []


def test_save_reports_validation_and_read_back(monkeypatch):
    api = FakePlatform()
    _wire(monkeypatch, api)
    text, is_err = _call(wt.save_workflow, {"name": "New One.json", "definition_json": json.dumps(DEFN)})
    assert not is_err and "id 42" in text and "verified by read-back" in text
    assert "no errors, no warnings" in text
    path, body = api.posts[0]
    assert body["filename"] == "New One.json"          # .json not doubled
    assert body["workflow"]["connections"][0]["source"] == "a"


def test_save_relays_errors_as_draft_and_warnings(monkeypatch):
    api = FakePlatform()
    api.save_resp = ({"status": "success", "workflow_id": 43, "is_valid": False,
                      "validation_errors": ["Folder Selector 'a' has no folderPath"],
                      "validation_warnings": ["Excel Export 'x': unknown key 'outputVar'"]}, 200)
    api.stored = {"nodes": [{"id": "a"}, {"id": "b"}], "connections": [{}]}
    _wire(monkeypatch, api)
    text, is_err = _call(wt.save_workflow, {"name": "Draft", "definition_json": json.dumps(DEFN)})
    assert "SAVED AS A DRAFT" in text and "no folderPath" in text and "outputVar" in text


def test_save_failure_is_never_reported_as_saved(monkeypatch):
    api = FakePlatform()
    api.save_resp = ("<html>proxy error</html>", 502)     # non-dict body
    _wire(monkeypatch, api)
    text, is_err = _call(wt.save_workflow, {"name": "Z", "definition_json": json.dumps(DEFN)})
    assert is_err and "Not saved (HTTP 502)" in text and "Do NOT tell the user" in text


# ------------------------------------------------------------ run

def test_run_passes_variables_waits_and_returns_the_report(monkeypatch):
    api = FakePlatform(workflows=[{"id": 5, "workflow_name": "Invoice Lines"}],
                       statuses=["Running", "Running", "Completed"])
    _wire(monkeypatch, api)
    text, is_err = _call(wt.run_workflow, {"workflow": "Invoice Lines",
                                           "variables_json": '{"inputFolder": "C:\\\\test"}'})
    assert not is_err and "finished with status completed" in text and "3 rows written" in text
    path, body = api.posts[0]
    assert body == {"workflow_id": 5, "variables": {"inputFolder": "C:\\test"},
                    "initiator": "the-agent:jdoe"}


def test_run_paused_and_unreadable_status(monkeypatch):
    api = FakePlatform(workflows=[{"id": 5, "workflow_name": "W"}], statuses=["Paused"])
    _wire(monkeypatch, api)
    text, _ = _call(wt.run_workflow, {"workflow": "5"})
    assert "PAUSED" in text
    api = FakePlatform(workflows=[{"id": 5, "workflow_name": "W"}],
                       statuses=[RuntimeError("503")] * 5 + ["Completed"])
    _wire(monkeypatch, api)
    text, _ = _call(wt.run_workflow, {"workflow": "5"})
    assert "could not be read" in text and "keeps running" in text


def test_run_that_did_not_start_says_so(monkeypatch):
    api = FakePlatform(workflows=[{"id": 5, "workflow_name": "W"}],
                       run_resp={"status": "error", "message": "Workflow with ID 5 not found"})
    _wire(monkeypatch, api)
    text, is_err = _call(wt.run_workflow, {"workflow": "5"})
    assert is_err and "did not start" in text and "not found" in text
    text, is_err = _call(wt.run_workflow, {"workflow": "5", "variables_json": "[1]"})
    assert is_err and "JSON object" in text


def test_unknown_workflow_name_suggests_near_matches(monkeypatch):
    api = FakePlatform(workflows=[{"id": 5, "workflow_name": "Acme Invoice Lines"}])
    _wire(monkeypatch, api)
    text, is_err = _call(wt.get_workflow, {"workflow": "Invoice"})
    assert is_err and "Acme Invoice Lines" in text


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
