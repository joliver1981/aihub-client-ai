"""
Code Flow manager — authoring CRUD + compile-on-save + persistence contract
(codeflows/manager.py). The Workflows-table SQL is stubbed with an in-memory
store so the graph/authoring logic is exercised DB-free.
"""
from __future__ import annotations

import sys

import pytest

from codeflows.manager import CodeFlowManager
from codeflows import compiler

pytestmark = pytest.mark.unit


class _MemManager(CodeFlowManager):
    """CodeFlowManager with the four _db_* seams backed by a dict keyed by name.
    Mirrors the real store: values are the full workflow_data blob, id assigned
    on first insert."""

    def __init__(self):
        super().__init__(tenant_id="cftest", connection_string="stub")
        self._store = {}     # name -> {"id": int, "data": workflow_data}
        self._next_id = 100

    def _db_save(self, name, workflow_data):
        if name in self._store:
            self._store[name]["data"] = workflow_data
            return self._store[name]["id"]
        wid = self._next_id
        self._next_id += 1
        self._store[name] = {"id": wid, "data": workflow_data}
        return wid

    def _db_exists(self, name):
        return name in self._store

    def _db_load(self, name):
        row = self._store.get(name)
        return (row["id"], row["data"]) if row else None

    def _db_list(self):
        out = []
        for name, row in sorted(self._store.items()):
            data = row["data"]
            if data.get("kind") != compiler.CODE_FLOW_KIND:
                continue
            defn = data.get("definition") or {}
            out.append({"workflow_id": row["id"], "name": name,
                        "step_count": len(defn.get("steps") or []),
                        "description": defn.get("description", "")})
        return out

    def _db_delete(self, name):
        if name in self._store and self._store[name]["data"].get("kind") == compiler.CODE_FLOW_KIND:
            del self._store[name]
            return True
        return False

    # scheduler seams: jobs = [{job_id, name, type, target_id, params}]
    _jobs: list = []

    def _db_linked_jobs(self, name, workflow_id):
        out = []
        for j in self._jobs:
            p = j.get("params") or {}
            base = {"job_id": j["job_id"], "name": j["name"], "type": j["type"],
                    "is_active": j.get("is_active", True)}
            if j["type"] == "workflow" and j.get("target_id") == workflow_id:
                out.append({**base, "match": "code_flow_schedule", "gist": ""})
            elif j["type"] == "agent_session" and str(p.get("code_flow", "")).lower() == name.lower():
                out.append({**base, "match": "linked", "gist": p.get("prompt", "")[:300]})
            elif j["type"] == "agent_session" and name.lower() in str(p.get("prompt", "")).lower():
                out.append({**base, "match": "mention", "gist": p.get("prompt", "")[:300]})
        return out

    def _db_delete_jobs(self, job_ids):
        ids = {int(i) for i in job_ids}
        before = len(self._jobs)
        self._jobs = [j for j in self._jobs if j["job_id"] not in ids]
        return before - len(self._jobs)


@pytest.fixture
def mgr():
    return _MemManager()


# ------------------------------------------------------------------- lifecycle

def test_create_persists_a_code_flow_marked_workflow(mgr):
    ok, info, err = mgr.create_code_flow("nightly-recon", "reconcile invoices")
    assert ok, err
    assert info["workflow_id"] >= 100
    stored = mgr._store["nightly-recon"]["data"]
    assert stored["kind"] == "code_flow"        # so JSON_VALUE filter finds it
    assert stored["definition"]["name"] == "nightly-recon"
    assert stored["nodes"] == [] and stored["connections"] == []


def test_create_rejects_duplicate_name(mgr):
    mgr.create_code_flow("dupe")
    ok, _info, err = mgr.create_code_flow("dupe")
    assert not ok and "already exists" in err


def test_create_requires_name(mgr):
    ok, _info, err = mgr.create_code_flow("   ")
    assert not ok and "required" in err


def test_create_rejects_collision_with_corrupt_existing_row(mgr):
    # #10: a same-name Workflows row whose data is unparseable (NULL/corrupt) must
    # still be treated as a collision — not silently clobbered by the MERGE. The
    # real _db_exists is parse-independent; the stub mirrors that (name in store).
    mgr._store["taken"] = {"id": 500, "data": None}  # corrupt/NULL workflow_data
    ok, _info, err = mgr.create_code_flow("taken")
    assert not ok and "already exists" in err
    assert mgr._store["taken"]["data"] is None       # untouched


def test_load_and_get_ignore_non_code_flow_rows(mgr):
    # a plain workflow row must not be loadable/editable/gettable as a code flow
    mgr._store["plain"] = {"id": 501, "data": {"kind": "workflow", "nodes": [{"id": "n"}]}}
    assert mgr.get_code_flow("plain") is None
    ok, _sid, err = mgr.add_step("plain", "s", "print(1)")
    assert not ok and "not found" in err


# ----------------------------------------------------------------- add / wire

def test_add_step_compiles_into_a_code_step_node_and_sets_start(mgr):
    mgr.create_code_flow("flow")
    ok, sid, err = mgr.add_step("flow", "pull", "print(1)", connections=["ERPDB"],
                                outputs=[{"kind": "file", "path": "out.csv"}])
    assert ok, err
    data = mgr._store["flow"]["data"]
    node = next(n for n in data["nodes"] if n["id"] == sid)
    assert node["type"] == "Code Step" and node["isStart"] is True
    assert node["config"]["connections"] == ["ERPDB"]
    assert node["config"]["outputVariable"] == f"{sid}_out"
    # definition kept in sync for editing
    assert data["definition"]["steps"][0]["id"] == sid
    assert data["definition"]["start"] == sid


def test_second_step_does_not_steal_start(mgr):
    mgr.create_code_flow("flow")
    _ok, s1, _ = mgr.add_step("flow", "a", "print(1)")
    _ok, s2, _ = mgr.add_step("flow", "b", "print(2)")
    data = mgr._store["flow"]["data"]
    assert data["definition"]["start"] == s1
    starts = [n["id"] for n in data["nodes"] if n["isStart"]]
    assert starts == [s1] and s2 not in starts


def test_wire_adds_a_flat_source_target_type_edge(mgr):
    mgr.create_code_flow("flow")
    _ok, s1, _ = mgr.add_step("flow", "a", "print(1)")
    _ok, s2, _ = mgr.add_step("flow", "b", "print(2)")
    ok, err = mgr.wire("flow", s1, s2, on="pass")
    assert ok, err
    conns = mgr._store["flow"]["data"]["connections"]
    assert {"source": s1, "target": s2, "type": "pass",
            "sourceAnchor": "Right", "targetAnchor": "Left"} in conns


def test_wire_rejects_unknown_step(mgr):
    mgr.create_code_flow("flow")
    _ok, s1, _ = mgr.add_step("flow", "a", "print(1)")
    ok, err = mgr.wire("flow", s1, "ghost", on="pass")
    assert not ok and "existing step ids" in err


def test_wire_rejects_bad_edge_type(mgr):
    mgr.create_code_flow("flow")
    _ok, s1, _ = mgr.add_step("flow", "a", "print(1)")
    _ok, s2, _ = mgr.add_step("flow", "b", "print(2)")
    ok, err = mgr.wire("flow", s1, s2, on="sideways")
    assert not ok


# ------------------------------------------------------------ editability (v1)

def test_update_step_code_rewrites_node_and_definition(mgr):
    mgr.create_code_flow("flow")
    _ok, sid, _ = mgr.add_step("flow", "a", "print('v1')")
    ok, err = mgr.update_step_code("flow", sid, "print('v2 — dev fixed it')")
    assert ok, err
    data = mgr._store["flow"]["data"]
    node = next(n for n in data["nodes"] if n["id"] == sid)
    assert "v2" in node["config"]["code"]
    assert "v2" in data["definition"]["steps"][0]["code"]   # kept in sync


def test_update_step_code_unknown_step(mgr):
    mgr.create_code_flow("flow")
    mgr.add_step("flow", "a", "print(1)")
    ok, err = mgr.update_step_code("flow", "nope", "print(2)")
    assert not ok and "not found" in err


# ------------------------------------------------------- credential guardrail

def test_add_step_blocks_hardcoded_credentials(mgr):
    mgr.create_code_flow("flow")
    ok, _sid, err = mgr.add_step("flow", "leak", 'password = "hunter2-secret-pw"')
    assert not ok and "credential" in err.lower()


def test_update_step_code_blocks_hardcoded_credentials(mgr):
    mgr.create_code_flow("flow")
    _ok, sid, _ = mgr.add_step("flow", "a", "print(1)")
    ok, err = mgr.update_step_code("flow", sid, 'api_key = "sk-live-abcdef0123456789abcdef"')
    assert not ok and "credential" in err.lower()


# --------------------------------------------------------------- get / list / delete

def test_get_returns_definition_and_compiled_nodes(mgr):
    mgr.create_code_flow("flow", "desc")
    _ok, sid, _ = mgr.add_step("flow", "a", "print(1)")
    got = mgr.get_code_flow("flow")
    assert got["workflow_id"] >= 100
    assert got["definition"]["steps"][0]["id"] == sid
    assert got["nodes"][0]["type"] == "Code Step"


def test_get_missing_returns_none(mgr):
    assert mgr.get_code_flow("nope") is None


def test_list_only_returns_code_flows(mgr):
    mgr.create_code_flow("cf-a")
    mgr.create_code_flow("cf-b")
    # a plain (non-code-flow) workflow row must be invisible to the list
    mgr._store["plain-wf"] = {"id": 999, "data": {"kind": "workflow", "nodes": []}}
    listed = mgr.list_code_flows()
    names = {r["name"] for r in listed}
    assert names == {"cf-a", "cf-b"}


def test_delete_removes_and_reports(mgr):
    mgr.create_code_flow("flow")
    ok, err = mgr.delete_code_flow("flow")
    assert ok and err is None
    assert mgr.get_code_flow("flow") is None
    ok2, err2 = mgr.delete_code_flow("flow")
    assert not ok2 and err2 == "not found"


# ---------------------------------------------- delete sweeps the flow's schedules
# (docs/handoff-codeflow-send-email-403.md §5: jobs #649/#650 — agent tasks
# scheduled against a flow — survived the flow's deletion and kept firing.)

def _seed_flow_with_jobs(mgr):
    _ok, info, _err = mgr.create_code_flow("past-due-aging")
    wid = info["workflow_id"]
    mgr._jobs = [
        {"job_id": 700, "name": "Code Flow: past-due-aging", "type": "workflow", "target_id": wid},
        {"job_id": 701, "name": "Code Flow: other", "type": "workflow", "target_id": wid + 1},
        {"job_id": 648, "name": "Agent: aging (linked)", "type": "agent_session",
         "params": {"prompt": "Run the aging flow and email me", "code_flow": "Past-Due-Aging"}},
        {"job_id": 649, "name": "Agent: weekday aging", "type": "agent_session",
         "params": {"prompt": "Run code flow 'past-due-aging' and email the workbook to me"}},
        {"job_id": 650, "name": "Agent: aging summary", "type": "agent_session",
         "params": {"prompt": "Summarize yesterday's past-due-aging run and the sales flow"}},
        {"job_id": 651, "name": "Agent: unrelated", "type": "agent_session",
         "params": {"prompt": "Summarize my inbox"}},
    ]
    return wid


def test_linked_schedules_classifies_every_link(mgr):
    wid = _seed_flow_with_jobs(mgr)
    got_wid, jobs = mgr.linked_schedules("past-due-aging")
    assert got_wid == wid
    by_id = {j["job_id"]: j["match"] for j in jobs}
    assert by_id == {700: "code_flow_schedule", 648: "linked", 649: "mention", 650: "mention"}
    assert mgr.linked_schedules("nope") == (None, [])


def test_delete_with_schedules_removes_linked_and_only_named_mentions(mgr):
    _seed_flow_with_jobs(mgr)
    ok, err, report = mgr.delete_code_flow_with_schedules("past-due-aging",
                                                          also_delete_job_ids=["649"])
    assert ok and err is None, err
    assert sorted(j["job_id"] for j in report["removed_schedules"]) == [648, 649, 700]
    assert [j["job_id"] for j in report["kept_mentions"]] == [650]
    assert report["removed_count"] == 3
    assert mgr.get_code_flow("past-due-aging") is None
    # other flows' schedules, the kept mention and unrelated jobs are untouched
    assert sorted(j["job_id"] for j in mgr._jobs) == [650, 651, 701]
    ok2, err2, _r = mgr.delete_code_flow_with_schedules("past-due-aging")
    assert not ok2 and err2 == "not found"


def test_plain_delete_sweeps_provable_links_but_never_guesses_at_mentions(mgr):
    _seed_flow_with_jobs(mgr)
    ok, err = mgr.delete_code_flow("past-due-aging")
    assert ok and err is None
    assert sorted(j["job_id"] for j in mgr._jobs) == [649, 650, 651, 701]


class _ScriptedCursor:
    """Answers each SELECT from a queue of row lists; records every statement."""
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []
        self.rowcount = 0

    def execute(self, sql, *params):
        self.calls.append((" ".join(sql.split()), params))
        if sql.lstrip().upper().startswith("DELETE"):
            self.rowcount = len(params)

    def fetchall(self):
        return self.answers.pop(0) if self.answers else []

    def close(self):
        pass


class _ScriptedConn:
    def __init__(self, cursor):
        self._c = cursor
        self.committed = False

    def cursor(self):
        return self._c

    def commit(self):
        self.committed = True

    def close(self):
        pass


def test_real_sql_seams_classify_dedupe_and_escape():
    """The SQL side of the sweep: three lookups, first match wins for a job
    that hits several, LIKE wildcards in the flow name are escaped, and the
    delete is one IN-list statement."""
    m = CodeFlowManager(tenant_id="t", connection_string="stub")
    cur = _ScriptedCursor([
        [(700, "Code Flow: x", 1)],                       # workflow-type by TargetId
        [(648, "Agent: linked", 1)],                      # code_flow parameter
        [(648, "Agent: linked", 1, "Run 100%_x flow"),    # also mentions → stays 'linked'
         (649, "Agent: mention", 0, "Run 100%_x and mail me")],
    ])
    m._db_conn = lambda: _ScriptedConn(cur)
    jobs = m._db_linked_jobs("100%_x", 55)
    assert [(j["job_id"], j["match"], j["is_active"]) for j in jobs] == [
        (648, "linked", True), (649, "mention", False), (700, "code_flow_schedule", True)]
    assert jobs[1]["gist"] == "Run 100%_x and mail me"
    assert cur.calls[0][1] == (55,)
    assert "ParameterName = 'code_flow'" in cur.calls[1][0] and cur.calls[1][1] == ("100%_x",)
    assert "ParameterName = 'prompt'" in cur.calls[2][0]
    assert cur.calls[2][1] == ("%100[%][_]x%",)          # wildcards escaped
    n = m._db_delete_jobs([700, 648])
    assert n == 2
    assert cur.calls[-1][0] == "DELETE FROM ScheduledJobs WHERE ScheduledJobId IN (?, ?)"
    assert cur.calls[-1][1] == (700, 648)
    assert m._db_delete_jobs([]) == 0


# ------------------------------------------------------------------- dry-run

def _live_runner():
    from automations.runner import AutomationRunner
    import automations.runner as runner_mod

    class _CfgStub:
        AUTOMATIONS_ENV_CRED_INJECTION = False
    runner_mod._load_cfg = lambda: _CfgStub
    r = AutomationRunner.__new__(AutomationRunner)
    r.manager = None
    r.tenant_id = "cftest"
    r.connection_string = "stub"
    r._resolve_python = lambda env_id: sys.executable
    r._resolve_connection = lambda name: None
    r._resolve_secret = lambda name: None
    return r


def test_dry_run_executes_a_saved_flow_end_to_end(mgr, tmp_path):
    mgr.create_code_flow("chain")
    _ok, s1, _ = mgr.add_step(
        "chain", "produce", "with open('data.txt','w') as f: f.write('hi')\n",
        outputs=[{"kind": "file", "path": "data.txt"}])
    _ok, s2, _ = mgr.add_step(
        "chain", "consume",
        "import aihub_runtime as aihub\n"
        "src = aihub.input('src')\n"
        "open('echo.txt','w').write(open(src).read().upper())\n",
        inputs=[{"name": "src", "type": "string", "default": f"${{{s1}_files[0]}}"}],
        outputs=[{"kind": "file", "path": "echo.txt"}])
    mgr.wire("chain", s1, s2, on="pass")

    res = mgr.dry_run("chain", runner=_live_runner())
    assert res["status"] == "success", res
    assert [s["status"] for s in res["steps"]] == ["success", "success"]


def test_dry_run_missing_flow(mgr):
    res = mgr.dry_run("nope", runner=_live_runner())
    assert res["status"] == "error" and "not found" in res["error"]


def test_dry_run_empty_flow(mgr):
    mgr.create_code_flow("empty")
    res = mgr.dry_run("empty", runner=_live_runner())
    assert res["status"] == "error" and "no steps" in res["error"]
