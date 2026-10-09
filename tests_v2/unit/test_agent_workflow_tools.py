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
        self.schedules = []          # rows of /api/scheduler/types/workflow/schedules
        self.deleted = []
        self.record_zone = True      # False = an old main app that drops the zone
        self.next_run = "2026-10-12T11:00:00"   # what the engine computes (UTC)

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
        if path.startswith("/api/scheduler/types/workflow/schedules"):
            return [dict(s) for s in self.schedules]
        if path.startswith("/api/workflow/builder/run-report"):
            return {"status": "success", "text": "RUN REPORT — 3 files found, 3 rows written", "flags": []}
        if path.startswith("/api/workflow/builder/node-reference"):
            self.reference_paths = getattr(self, "reference_paths", []) + [path]
            loop = ("Loop:\n- sourceType ...\n  EXACT CONFIG KEYS for Loop (the engine ignores any other "
                    "key; unknown keys come back as validation warnings): itemVariable, loopSource\n"
                    "  Required: loopSource")
            fs = ("Folder Selector:\n- folderPath ...\n  EXACT CONFIG KEYS for Folder Selector (the engine "
                  "ignores any other key; unknown keys come back as validation warnings): filePattern, folderPath")
            out = {"status": "success", "node_types": ["Loop", "Folder Selector"],
                   "details": loop + "\n\n" + fs}
            if path.endswith("types=all"):
                out["overview"] = "Loop - repeat over a list"
            return out
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
        if path.startswith("/api/scheduler/jobs/") and path.endswith("/types/workflow/schedules"):
            wid = int(path.split("/")[4])
            sid = 900 + len(self.posts)
            name = next((w["workflow_name"] for w in self.workflows if w["id"] == wid), "?")
            self.schedules.append({
                "id": sid, "workflow_id": wid, "workflow_name": name, "scheduled_job_id": 41,
                "type": body["type"], "cron_expression": body.get("cron_expression"),
                "interval_hours": body.get("interval_hours"), "start_date": body.get("start_date"),
                "end_date": body.get("end_date"), "max_runs": body.get("max_runs"), "current_runs": 0,
                "is_active": True, "next_run_time": self.next_run, "workflow_kind": None,
                "timezone": body.get("timezone") if self.record_zone else None})
            return {"id": sid, "scheduled_job_id": 41}, 201
        raise AssertionError(f"unexpected POST {path}")

    async def delete(self, path):
        sid = int(path.rsplit("/", 1)[1])
        self.deleted.append(sid)
        self.schedules = [s for s in self.schedules if s["id"] != sid]
        return {"message": "deleted"}, 200


def _wire(monkeypatch, api, allowed=True):
    monkeypatch.setattr(wt, "_get", api.get)
    monkeypatch.setattr(wt, "_post", api.post)
    monkeypatch.setattr(wt, "_authoring_allowed", lambda: allowed)
    monkeypatch.setattr(wt, "_user_context", lambda: {"user_id": 7, "role": 2, "username": "jdoe"})
    monkeypatch.setattr(wt, "_delete", api.delete)
    monkeypatch.setattr(wt, "_other_kind_hint", _no_hint)
    wt.CURRENT_USER.set({"user_id": 7, "role": 2, "username": "jdoe",
                         "browser_timezone": "America/Toronto"})
    # No real waiting: sleeps return at once and each clock read moves 3 s on.
    clock = iter(range(0, 10**6, 3))
    monkeypatch.setattr(wt, "asyncio", SimpleNamespace(sleep=_no_sleep))
    monkeypatch.setattr(wt, "time", SimpleNamespace(monotonic=lambda: next(clock)))


async def _no_sleep(_s):
    return None


async def _no_hint(_ref):
    return ""


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
                           (wt.get_workflow_run_report, {"execution_id": "E1"}),
                           (wt.schedule_workflow, {"workflow": "5", "cron_expression": "0 7 * * *"}),
                           (wt.list_workflow_schedules, {}),
                           (wt.cancel_workflow_schedule, {"workflow": "5"})):
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


def test_reference_overview_is_compact_and_details_come_per_type(monkeypatch):
    api = FakePlatform()
    _wire(monkeypatch, api)
    text, is_err = _call(wt.get_workflow_node_reference, {})
    assert not is_err and "OVERVIEW" in text and "repeat over a list" in text
    assert "- Loop: itemVariable, loopSource  [required: loopSource]" in text
    assert "- Folder Selector: filePattern, folderPath" in text
    assert "sourceType ..." not in text            # full details not in the overview
    text, _ = _call(wt.get_workflow_node_reference, {"node_types": "Loop"})
    assert "NODE DETAILS" in text and "sourceType ..." in text and "OVERVIEW" not in text
    assert api.reference_paths == ["/api/workflow/builder/node-reference?types=all",
                                   "/api/workflow/builder/node-reference?types=Loop"]


def test_contract_regex_tracks_the_platform_wording():
    # The overview's key index is parsed out of CommonUtils._node_contract_line's
    # text; if that wording changes, this fails instead of the index going empty.
    src = open(os.path.join(APP_ROOT, "CommonUtils.py"), encoding="utf-8").read()
    assert 'EXACT CONFIG KEYS for {node_type} (the engine ignores any other key; unknown "' in src
    assert 'f"keys come back as validation warnings): {' in src
    assert 'line += f"\\n  Required: {' in src
    sample = ("\n  EXACT CONFIG KEYS for Loop (the engine ignores any other key; unknown keys come back "
              "as validation warnings): itemVariable, loopSource\n  Required: loopSource")
    assert wt._CONTRACT_RE.findall(sample) == [("Loop", "itemVariable, loopSource", "loopSource")]


def test_unknown_workflow_name_suggests_near_matches(monkeypatch):
    api = FakePlatform(workflows=[{"id": 5, "workflow_name": "Acme Invoice Lines"}])
    _wire(monkeypatch, api)
    text, is_err = _call(wt.get_workflow, {"workflow": "Invoice"})
    assert is_err and "Acme Invoice Lines" in text


# ------------------------------------------------------------ visual only

ROWS = [{"id": 5, "workflow_name": "Acme Lines", "kind": "workflow"},
        {"id": 6, "workflow_name": "Invoice Aging", "kind": "code_flow"}]


def test_code_flows_are_refused_and_left_out(monkeypatch):
    api = FakePlatform(workflows=ROWS)
    _wire(monkeypatch, api)
    for ref in ("Invoice Aging", "6"):
        text, is_err = _call(wt.get_workflow, {"workflow": ref})
        assert is_err and "CODE FLOW" in text and "schedule_code_flow" in text, ref
    text, _ = _call(wt.list_workflows, {})
    assert "Acme Lines" in text and "Invoice Aging" not in text and "1 Code Flow(s)" in text
    text, is_err = _call(wt.save_workflow, {"name": "invoice aging", "definition_json": json.dumps(DEFN),
                                            "replace_existing": True})
    assert is_err and "CODE FLOW" in text and api.posts == []


def test_a_portal_or_automation_name_is_answered_with_its_tool(monkeypatch):
    api = FakePlatform(workflows=ROWS)
    _wire(monkeypatch, api)

    async def portal_hint(ref):
        return f" '{ref}' is a recorded PORTAL workflow (a browser replay) — use schedule_portal_workflow."
    monkeypatch.setattr(wt, "_other_kind_hint", portal_hint)
    text, is_err = _call(wt.schedule_workflow, {"workflow": "vendor_invoice_download",
                                                "cron_expression": "0 6 * * *"})
    assert is_err and "No visual workflow named" in text and "schedule_portal_workflow" in text
    assert api.posts == []                      # nothing scheduled anywhere


# ------------------------------------------------------------ scheduling

def test_cron_is_scheduled_in_the_users_zone(monkeypatch):
    api = FakePlatform(workflows=ROWS)
    _wire(monkeypatch, api)
    text, is_err = _call(wt.schedule_workflow, {"workflow": "Acme Lines", "cron_expression": "0 7 * * 1-5"})
    assert not is_err, text
    path, body = api.posts[-1]
    assert path == "/api/scheduler/jobs/5/types/workflow/schedules"
    assert body["cron_expression"] == "0 7 * * 1-5" and body["timezone"] == "America/Toronto"
    assert "schedule #901" in text and "2026-10-12 07:00" in text and "WARNING" not in text


def test_existing_schedules_stop_it_until_the_user_says_add(monkeypatch):
    api = FakePlatform(workflows=ROWS)
    api.schedules = [{"id": 800, "workflow_id": 5, "workflow_name": "Acme Lines", "type": "interval",
                      "interval_hours": 4, "is_active": True, "next_run_time": None, "current_runs": 3}]
    _wire(monkeypatch, api)
    text, is_err = _call(wt.schedule_workflow, {"workflow": "Acme Lines", "every_days": 1})
    assert is_err and "already has 1 active schedule" in text and "#800" in text
    assert api.posts == [] and api.deleted == []          # nothing changed, nothing removed
    text, is_err = _call(wt.schedule_workflow, {"workflow": "Acme Lines", "every_days": 1,
                                                "add_alongside": True})
    assert not is_err and len(api.schedules) == 2 and api.deleted == []


def test_a_zone_the_scheduler_did_not_record_is_rolled_back(monkeypatch):
    api = FakePlatform(workflows=ROWS)
    api.record_zone = False
    _wire(monkeypatch, api)
    text, is_err = _call(wt.schedule_workflow, {"workflow": "Acme Lines", "cron_expression": "0 7 * * *"})
    assert is_err and "NOT scheduled" in text and api.deleted == [901] and api.schedules == []


def test_next_run_off_the_requested_time_is_flagged(monkeypatch):
    api = FakePlatform(workflows=ROWS)
    api.next_run = "2026-10-12T07:00:00"       # 03:00 in Toronto: the zone was not applied
    _wire(monkeypatch, api)
    text, is_err = _call(wt.schedule_workflow, {"workflow": "Acme Lines", "cron_expression": "0 7 * * 1-5"})
    assert "WARNING" in text and "03:00" in text


def test_cancel_is_two_step_and_removes_only_that_schedule(monkeypatch):
    api = FakePlatform(workflows=ROWS)
    api.schedules = [{"id": 900, "workflow_id": 5, "workflow_name": "Acme Lines", "type": "cron",
                      "cron_expression": "0 7 * * 1-5", "timezone": "America/Toronto", "is_active": True},
                     {"id": 901, "workflow_id": 5, "workflow_name": "Acme Lines", "type": "interval",
                      "interval_hours": 4, "is_active": True}]
    _wire(monkeypatch, api)
    text, _ = _call(wt.cancel_workflow_schedule, {"workflow": "Acme Lines"})
    assert "has 2 schedules" in text and api.deleted == []
    text, _ = _call(wt.cancel_workflow_schedule, {"workflow": "Acme Lines", "schedule_id": 900})
    assert "Ask the user to confirm" in text and api.deleted == []
    text, is_err = _call(wt.cancel_workflow_schedule, {"workflow": "Acme Lines", "schedule_id": 900,
                                                       "confirmed": True})
    assert not is_err and "Removed schedule #900" in text and api.deleted == [900]
    assert [s["id"] for s in api.schedules] == [901]


def test_code_flow_schedule_carries_the_users_zone(monkeypatch):
    import authoring_tools as at
    sent = []

    async def fake_manage_cf(action, payload, timeout=None):
        sent.append((action, payload))
        if payload["name"] == "Acme Lines":
            return {"error": "code flow not found"}, 404
        return {"scheduled_job_id": 55, "schedule_id": 901}, 201
    monkeypatch.setattr(at, "_manage_cf", fake_manage_cf)
    monkeypatch.setattr(at, "_authoring_allowed", lambda: True)
    api = FakePlatform(workflows=ROWS)
    _wire(monkeypatch, api)
    text, is_err = _call(at.schedule_code_flow, {"name": "Invoice Aging", "cron_expression": "30 7 * * 1-5"})
    assert not is_err and sent[-1][1]["timezone"] == "America/Toronto"
    assert sent[-1][1]["schedule"]["cron_expression"] == "30 7 * * 1-5"
    text, is_err = _call(at.schedule_code_flow, {"name": "Acme Lines", "cron_expression": "0 7 * * *"})
    assert is_err and "schedule_workflow" in text       # a visual workflow's name -> the right tool


def test_portal_tools_name_a_visual_workflow_or_code_flow(monkeypatch):
    import portal_tools as pt
    api = FakePlatform(workflows=ROWS)
    _wire(monkeypatch, api)
    assert "schedule_workflow" in asyncio.run(pt._portal_not_found("Acme Lines"))
    assert "schedule_code_flow" in asyncio.run(pt._portal_not_found("Invoice Aging"))
    assert "VISUAL" not in asyncio.run(pt._portal_not_found("nothing by this name"))


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
