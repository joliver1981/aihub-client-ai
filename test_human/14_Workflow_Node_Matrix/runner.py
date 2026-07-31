"""
Workflow Node Regression Matrix — runner.

Builds a minimal workflow per node type / node pairing via the SAME endpoints the
Workflow Designer UI calls (/save/workflow), executes it via the Run button's endpoint
(/api/workflow/run), then verifies the REAL artifact (file on disk, execution variables,
approval pipeline, SFTP server contents) against an exact oracle.

Produces:
  results_history/results_<ts>.json   (machine-readable)
  results_history/REPORT_<ts>.md      (human-readable, timestamped)
  REPORT_LATEST.md                    (same report, stable path)

Every run diffs against the previous run's JSON (the baseline): a check that was PASS
and is now FAIL is reported as a REGRESSION and the exit code is non-zero. Known bugs
are registered as XFAIL — when one starts passing it is flagged XPASS ("fix landed,
update the matrix / close the task"), never silently absorbed.

Run (aihub2.1 env):
  C:\\Users\\james\\miniconda3\\envs\\aihub2.1\\python.exe runner.py [--tier 2] [--only substr]
      [--cleanup] [--list] [--base-url http://localhost:5001] [--timeout 90]

Statuses: PASS / FAIL / XFAIL (known bug, still failing) / XPASS (known bug now passing!)
          / SKIP (environment dependency missing — reason recorded) / ERROR (runner fault)
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import socket
import subprocess
import sys
import time

import requests

# Windows consoles default to cp1252 — force utf-8 so report echo never crashes.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
HISTORY_DIR = os.path.join(HERE, "results_history")
PREFIX = "NODEREG-"          # every workflow this runner creates is named PREFIX + check id
OUT_ROOT = r"C:\temp\aihub_test\nodereg"
SFTP_ROOT = os.path.join(REPO, "test_human", "_sftp_test_server", "runtime", "server_root")

# Remote (post-install) mode: --remote points the runner at an INSTALLED app on
# another machine. Node configs still use engine-local paths (C:\temp\...);
# disk verification then goes through the admin share \\<host>\c$\... when it
# is reachable — otherwise disk-dependent checks SKIP with the reason recorded.
REMOTE_UNC = None            # e.g. //10.0.0.6/c$ (set in main when --remote)
SFTP_HOST = "127.0.0.1"      # host the ENGINE box dials for the SFTP checks


def hostpath(path):
    """Translate an engine-local Windows path for verification on THIS machine."""
    p = str(path).replace("/", "\\")
    if REMOTE_UNC and len(p) > 2 and p[1] == ":":
        return (REMOTE_UNC + p[2:]).replace("\\", "/")
    return p


def fexists(path):
    return os.path.exists(hostpath(path))

# ---------------------------------------------------------------------------- helpers

def now_stamp():
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def log(msg):
    print(f"[nodereg] {msg}", flush=True)


class Api:
    """Session against the app, using the same endpoints the Designer UI uses."""

    def __init__(self, base_url, username, password):
        self.base = base_url.rstrip("/")
        self.s = requests.Session()
        self._login(username, password)

    def _login(self, username, password):
        r = self.s.get(f"{self.base}/login", timeout=20)
        hidden = dict(re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', r.text))
        hidden.update(dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"', r.text)))
        data = {"username": username, "password": password}
        data.update(hidden)
        r = self.s.post(f"{self.base}/login", data=data, allow_redirects=True, timeout=30)
        if "/login" in r.url:
            raise RuntimeError(f"login failed (landed on {r.url})")
        log(f"logged in as {username}")

    def get(self, path, **kw):
        return self.s.get(f"{self.base}{path}", timeout=kw.pop("timeout", 30), **kw)

    def post(self, path, payload=None, **kw):
        return self.s.post(f"{self.base}{path}", json=payload, timeout=kw.pop("timeout", 60), **kw)

    @staticmethod
    def jbody(r):
        """Parse a response body; several legacy endpoints double-encode JSON (a JSON
        string containing JSON) — unwrap that transparently."""
        try:
            body = r.json()
        except Exception:
            return None
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except Exception:
                return body
        return body

    # ---- workflow lifecycle -------------------------------------------------
    def save_workflow(self, name, nodes, connections):
        payload = {"filename": f"{name}.json",
                   "workflow": {"nodes": nodes, "connections": connections, "variables": {}}}
        r = self.post("/save/workflow", payload)
        if r.status_code != 200:
            raise RuntimeError(f"save {name} -> HTTP {r.status_code}: {r.text[:200]}")
        body = self.jbody(r) or {}
        if isinstance(body, dict) and str(body.get("status", "")).lower() == "error":
            raise RuntimeError(f"save {name} -> {body.get('message', '')[:200]}")
        wid = self._find_workflow_id(name)
        if wid is None:
            raise RuntimeError(f"save {name} ok but id not found via /get/workflows")
        return wid

    def _workflow_list(self):
        body = self.jbody(self.get("/get/workflows"))
        if isinstance(body, dict):
            for key in ("workflows", "data", "items"):
                if isinstance(body.get(key), list):
                    return body[key]
            return []
        return body if isinstance(body, list) else []

    def _find_workflow_id(self, name):
        best = None
        for w in self._workflow_list():
            wname = w.get("workflow_name") or w.get("name") or ""
            if wname == name:
                wid = w.get("id") or w.get("workflow_id")
                if wid is not None and (best is None or int(wid) > int(best)):
                    best = int(wid)
        return best

    def get_workflow(self, wid):
        r = self.get(f"/get/workflow/{wid}")
        return r.json() if r.status_code == 200 else None

    def delete_workflow(self, wid):
        return self.s.delete(f"{self.base}/delete/workflow/{wid}", timeout=30).status_code

    def run_workflow(self, wid, variables=None):
        r = self.post("/api/workflow/run", {"workflow_id": wid, "initiator": "nodereg-runner",
                                            "variables": variables or {}})
        body = r.json()
        if r.status_code != 200 or body.get("status") != "success":
            raise RuntimeError(f"run wf {wid} -> HTTP {r.status_code}: {json.dumps(body)[:200]}")
        return body.get("execution_id")

    # ---- execution inspection ----------------------------------------------
    def execution(self, eid):
        body = self.jbody(self.get(f"/api/workflow/executions/{eid}"))
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _status_of(body):
        if not isinstance(body, dict):
            return ""
        for k in ("status", "execution_status", "state"):
            if isinstance(body.get(k), str):
                return body[k].lower()
        ex = body.get("execution")
        if isinstance(ex, dict):
            for k in ("status", "execution_status", "state"):
                if isinstance(ex.get(k), str):
                    return ex[k].lower()
        return ""

    def execution_status(self, eid):
        return self._status_of(self.execution(eid))

    def steps(self, eid):
        body = self.jbody(self.get(f"/api/workflow/executions/{eid}/steps"))
        if isinstance(body, dict):
            for key in ("steps", "data", "items"):
                if isinstance(body.get(key), list):
                    return body[key]
            return []
        return body if isinstance(body, list) else []

    def variables(self, eid):
        body = self.jbody(self.get(f"/api/workflow/executions/{eid}/variables"))
        if isinstance(body, dict):
            for key in ("variables", "data"):
                if isinstance(body.get(key), dict):
                    body = body[key]
                    break
        if not isinstance(body, dict):
            return {}
        # values arrive wrapped as {type, updated_at, value} — unwrap transparently
        out = {}
        for k, v in body.items():
            if isinstance(v, dict) and "value" in v and ("type" in v or "updated_at" in v):
                out[k] = v["value"]
            else:
                out[k] = v
        return out

    def logs_tail(self, eid, n=12):
        body = self.jbody(self.get(f"/api/workflow/executions/{eid}/logs"))
        if body is None:
            return []
        entries = body if isinstance(body, list) else (
            body.get("logs") or body.get("data") or [])
        out = []
        for e in entries[-n:]:
            if isinstance(e, dict):
                out.append(str(e.get("message") or e.get("log_message") or e)[:220])
            else:
                out.append(str(e)[:220])
        return out

    def wait_terminal(self, eid, timeout=90, on_tick=None):
        """Poll until terminal status. on_tick(status) may intervene (e.g. approve)."""
        t0 = time.time()
        status = ""
        while time.time() - t0 < timeout:
            status = self.execution_status(eid)
            if status in ("completed", "failed", "cancelled", "canceled", "error", "timeout"):
                return status
            if on_tick:
                on_tick(status)
            time.sleep(1.5)
        return status or "poll-timeout"

    # ---- approvals ----------------------------------------------------------
    def pending_approvals(self):
        r = self.get("/api/workflow/approvals?status=pending")
        try:
            body = r.json()
        except Exception:
            return []
        if isinstance(body, list):
            return body
        for key in ("approvals", "requests", "data", "items"):
            if isinstance(body.get(key), list):
                return body[key]
        return []

    def decide_approval(self, request_id, approve=True):
        return self.post(f"/api/workflow/approvals/{request_id}",
                         {"status": "approved" if approve else "rejected",
                          "comments": "nodereg auto-decision", "user": "admin"})

    # ---- environment lookups ------------------------------------------------
    def connection_id(self, name):
        body = self.jbody(self.get("/get/connections"))
        rows = body if isinstance(body, list) else (
            (body or {}).get("connections") or (body or {}).get("data") or [])
        for c in rows:
            if not isinstance(c, dict):
                continue
            cname = (c.get("connection_name") or c.get("name") or "").strip()
            if cname == name:
                return c.get("id") or c.get("connection_id")
        return None

    def secret_names(self):
        body = self.jbody(self.get("/workflow/secrets/list"))
        rows = body if isinstance(body, list) else (
            (body or {}).get("secrets") or (body or {}).get("data") or [])
        names = []
        for s in rows:
            if isinstance(s, str):
                names.append(s)
            elif isinstance(s, dict):
                names.append(s.get("name") or s.get("secret_name") or s.get("key") or "")
        return [n for n in names if n]


# ------------------------------------------------------------------- node builders

def N(nid, ntype, label, config, start=False, x=100, y=100):
    return {"id": nid, "isStart": start, "type": ntype, "label": label,
            "position": {"left": f"{x}px", "top": f"{y}px"}, "config": config}


def C(src, dst, etype="pass"):
    return {"source": src, "sourceAnchor": "Right", "target": dst,
            "targetAnchor": "Left", "type": etype}


def set_var(nid, var, value, start=False, x=100, y=100):
    return N(nid, "Set Variable", f"set {var}",
             {"variableName": var, "valueSource": "direct", "valueExpression": value,
              "evaluateAsExpression": False, "outputPath": ""}, start=start, x=x, y=y)


def file_node(nid, op, path, content=None, x=100, y=100, extra=None):
    cfg = {"operation": op, "filePath": path}
    if content is not None:
        cfg.update({"contentSource": "direct", "content": content})
    if extra:
        cfg.update(extra)
    return N(nid, "File", f"file {op}", cfg, x=x, y=y)


# ------------------------------------------------------------------- verification

def read_file(path):
    try:
        with open(hostpath(path), "r", encoding="utf-8-sig") as fh:
            return fh.read()
    except Exception:
        return None


def steps_summary(api, eid):
    out = []
    for s in api.steps(eid):
        out.append(f"{s.get('node_type') or s.get('nodeType') or '?'}:"
                   f"{(s.get('status') or '?')}")
    return ",".join(out)


def read_xlsx_rows(path):
    """Return list-of-dicts from first sheet, or None."""
    try:
        import pandas as pd
        df = pd.read_excel(path)
        return df.to_dict(orient="records")
    except Exception as e:
        return None


# ------------------------------------------------------------------- check registry
#
# Each check: dict(id, title, tier, needs (list of env keys), xfail (reason or None),
#                  build(ctx) -> (nodes, connections) | None for non-workflow checks,
#                  verify(ctx, eid, final_status) -> (ok: bool, evidence: str))
# ctx carries: api, out (per-run output dir), env (probe results), run stamp.

CHECKS = []


def check(id, title, tier=1, needs=(), xfail=None, slow=False, disk=False):
    """disk=True -> verification reads the ENGINE box's filesystem; in --remote
    mode these run only when the admin share (\host\c$) is reachable."""
    def deco(fn):
        CHECKS.append({"id": id, "title": title, "tier": tier, "needs": list(needs),
                       "xfail": xfail, "fn": fn, "slow": slow, "disk": disk})
        return fn
    return deco


# ---------------- Tier 1 — core engine ----------------

@check("setvar_file_write", "Set Variable -> File write: variable substitution lands in the file", disk=True)
def c_setvar_file(ctx):
    p = os.path.join(ctx["out"], "setvar.txt").replace("\\", "/")
    nodes = [set_var("node-0", "greeting", "hello-nodereg", start=True),
             file_node("node-1", "write", p, "value=${greeting}", x=320)]
    conns = [C("node-0", "node-1")]

    def verify(eid, status):
        content = read_file(p)
        ok = status == "completed" and content is not None and content.strip() == "value=hello-nodereg"
        return ok, f"status={status}; file={content!r}"
    return nodes, conns, {}, verify


@check("file_write_append", "File write + append: both contents present in order", disk=True)
def c_file_append(ctx):
    p = os.path.join(ctx["out"], "append.txt").replace("\\", "/")
    nodes = [file_node("node-0", "write", p, "line1\n", x=100),
             file_node("node-1", "append", p, "line2\n", x=320)]
    nodes[0]["isStart"] = True
    conns = [C("node-0", "node-1")]

    def verify(eid, status):
        content = read_file(p) or ""
        lines = [l for l in content.splitlines() if l.strip()]
        ok = status == "completed" and lines == ["line1", "line2"]
        return ok, f"status={status}; lines={lines}"
    return nodes, conns, {}, verify


@check("file_check_delete", "File check + delete: check passes on existing file, delete removes it", disk=True)
def c_file_check_delete(ctx):
    p = os.path.join(ctx["out"], "victim.txt").replace("\\", "/")
    nodes = [file_node("node-0", "write", p, "temp\n", x=100),
             file_node("node-1", "check", p, x=320),
             file_node("node-2", "delete", p, x=540)]
    nodes[0]["isStart"] = True
    conns = [C("node-0", "node-1"), C("node-1", "node-2")]

    def verify(eid, status):
        gone = not fexists(p)
        ok = status == "completed" and gone
        return ok, f"status={status}; deleted={gone}; steps={steps_summary(ctx['api'], eid)}"
    return nodes, conns, {}, verify


def _conditional_wf(ctx, xval, suffix):
    # Engine contract (workflow_execution.py ~7050 + dispatch ~591): the Conditional
    # returns success=condition_result — TRUE follows the *pass* edge, FALSE raises and
    # follows the *fail* edge. 'true'/'false' edge types are NEVER followed by the
    # backend. The Designer can only author pass/fail/complete (setArrowType,
    # workflow.js), so dead true/false edges can only come from PROGRAMMATIC authoring —
    # the lint below flags them. (workflow.js also contains an in-browser "simulator"
    # with different edge semantics — per james 2026-07-30 it is DEPRECATED dead code
    # with no paths to it; all workflows run through the backend engine. Ignore it.)
    pt = os.path.join(ctx["out"], f"cond_{suffix}_true.txt").replace("\\", "/")
    pf = os.path.join(ctx["out"], f"cond_{suffix}_false.txt").replace("\\", "/")
    nodes = [set_var("node-0", "x", xval, start=True),
             N("node-1", "Conditional", "x > 5?",
               {"conditionType": "comparison", "leftValue": "${x}",
                "rightValue": "5", "operator": ">"}, x=320),
             file_node("node-2", "write", pt, "TRUE\n", x=540),
             file_node("node-3", "write", pf, "FALSE\n", x=540, extra=None)]
    nodes[3]["position"]["top"] = "220px"
    conns = [C("node-0", "node-1"), C("node-1", "node-2", "pass"), C("node-1", "node-3", "fail")]
    return nodes, conns, pt, pf


@check("conditional_true", "Conditional: true edge fires (and false edge does not)", disk=True)
def c_cond_true(ctx):
    nodes, conns, pt, pf = _conditional_wf(ctx, "10", "t")

    def verify(eid, status):
        t_exists = fexists(pt)
        f_exists = fexists(pf)
        ok = status == "completed" and t_exists and not f_exists
        return ok, f"status={status}; TRUE-file={t_exists}; FALSE-file={f_exists}"
    return nodes, conns, {}, verify


@check("conditional_false", "Conditional: false edge fires (and true edge does not)", disk=True)
def c_cond_false(ctx):
    nodes, conns, pt, pf = _conditional_wf(ctx, "3", "f")

    def verify(eid, status):
        t_exists = fexists(pt)
        f_exists = fexists(pf)
        # FALSE rides the exception path: the Conditional step is marked Failed and the
        # fail edge continues — overall status may be completed or failed; the oracle is
        # WHICH branch file exists.
        ok = f_exists and not t_exists and status in ("completed", "failed")
        return ok, f"status={status}; TRUE-file={t_exists}; FALSE-file={f_exists}"
    return nodes, conns, {}, verify


@check("loop_list_append", "Loop over JSON list -> File append per item -> End Loop", disk=True)
def c_loop(ctx):
    p = os.path.join(ctx["out"], "loop.txt").replace("\\", "/")
    nodes = [set_var("node-0", "items", '["alpha","beta","gamma"]', start=True),
             # sourceType MUST be "variable" — "auto" ignores loopSource entirely and
             # auto-detects from the previous step's output (engine ~7247).
             N("node-1", "Loop", "each item",
               {"sourceType": "variable", "loopSource": "${items}", "itemVariable": "currentItem",
                "indexVariable": "itemIndex", "maxIterations": "10", "emptyBehavior": "skip"}, x=320),
             file_node("node-2", "append", p, "${currentItem}\n", x=540),
             N("node-3", "End Loop", "end", {"loopNodeId": "node-1"}, x=760),
             file_node("node-4", "write",
                       os.path.join(ctx["out"], "loop_done.txt").replace("\\", "/"),
                       "done\n", x=980)]
    conns = [C("node-0", "node-1"), C("node-1", "node-2"), C("node-2", "node-3"),
             C("node-3", "node-4")]

    def verify(eid, status):
        content = read_file(p) or ""
        lines = [l for l in content.splitlines() if l.strip()]
        done = read_file(os.path.join(ctx["out"], "loop_done.txt")) is not None
        ok = status == "completed" and lines == ["alpha", "beta", "gamma"] and done
        return ok, f"status={status}; lines={lines}; continuation-ran={done}"
    return nodes, conns, {}, verify


# ---------------- Tier 2 — integrations ----------------

@check("setvar_expression_eval", "Set Variable evaluateAsExpression: simple expression evaluates", disk=True)
def c_setvar_expr(ctx):
    p = os.path.join(ctx["out"], "expr.txt").replace("\\", "/")
    nodes = [N("node-0", "Set Variable", "calc",
               {"variableName": "n", "valueSource": "direct",
                "valueExpression": "len([1, 2, 3]) * 7",
                "evaluateAsExpression": True}, start=True),
             file_node("node-1", "write", p, "n=${n}", x=320)]
    conns = [C("node-0", "node-1")]

    def verify(eid, status):
        content = (read_file(p) or "").strip()
        ok = status == "completed" and content == "n=21"
        return ok, f"status={status}; file={content!r} (oracle 'n=21')"
    return nodes, conns, {}, verify


@check("setvar_expression_failure_honesty",
       "Set Variable: a FAILING expression must not silently store the literal",
       xfail="Engine Fix-3 backlog (found 2026-07-30, wf 1337): when expression evaluation "
             "fails (e.g. f-string comprehension over a DB envelope), the engine silently "
             "stores the LITERAL source text and the step 'Completes' — dishonest fallback", disk=True)
def c_setvar_expr_failure(ctx):
    p = os.path.join(ctx["out"], "expr_fail.txt").replace("\\", "/")
    nodes = [N("node-0", "Set Variable", "bad calc",
               {"variableName": "t", "valueSource": "direct",
                "valueExpression": "''.join([f\"{row['x']}\" for row in nonexistent_var])",
                "evaluateAsExpression": True}, start=True),
             file_node("node-1", "write", p, "${t}", x=320)]
    conns = [C("node-0", "node-1")]

    def verify(eid, status):
        content = (read_file(p) or "").strip()
        literal_leak = "join(" in content
        # honest behavior = the Set Variable step FAILS (or the run fails);
        # dishonest = completes and the literal expression text leaks into output
        ok = status == "failed" and not literal_leak
        return ok, f"status={status}; literal-leaked={literal_leak}; file={content[:80]!r}"
    return nodes, conns, {}, verify


@check("database_select_vars", "Database (AIRDB select) -> rows land in execution variables",
       tier=2, needs=["airdb"])
def c_db_select(ctx):
    q = ("SELECT l.store_id, l.store_name, COUNT(e.employee_id) AS headcount "
         "FROM TS.location_master l LEFT JOIN TS.employee_data e ON e.store_id = l.store_id "
         "GROUP BY l.store_id, l.store_name ORDER BY l.store_id")
    nodes = [N("node-0", "Database", "headcount",
               {"connection": str(ctx["env"]["airdb"]), "dbOperation": "query", "query": q,
                "saveToVariable": True, "outputVariable": "dbrows", "continueOnError": False},
               start=True)]
    conns = []

    def verify(eid, status):
        v = ctx["api"].variables(eid)
        raw = v.get("dbrows")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                pass
        nrows = None
        if isinstance(raw, dict) and isinstance(raw.get("rows"), list):
            nrows = len(raw["rows"])
        elif isinstance(raw, list):
            nrows = len(raw)
        ok = status == "completed" and nrows == 10
        return ok, f"status={status}; dbrows type={type(raw).__name__}; rows={nrows} (oracle 10)"
    return nodes, conns, {}, verify


@check("database_fail_edge", "Database failure routes the fail edge (honest failure handling)",
       tier=2, needs=["airdb"], disk=True)
def c_db_fail_edge(ctx):
    p_ok = os.path.join(ctx["out"], "db_ok.txt").replace("\\", "/")
    p_fail = os.path.join(ctx["out"], "db_failed.txt").replace("\\", "/")
    nodes = [N("node-0", "Database", "bad query",
               {"connection": str(ctx["env"]["airdb"]), "dbOperation": "query",
                "query": "SELECT * FROM TS.nonexistent_table_nodereg",
                "saveToVariable": True, "outputVariable": "x", "continueOnError": False},
               start=True),
             file_node("node-1", "write", p_ok, "ok\n", x=320),
             file_node("node-2", "write", p_fail, "db-failed\n", x=320)]
    nodes[2]["position"]["top"] = "220px"
    conns = [C("node-0", "node-1", "pass"), C("node-0", "node-2", "fail")]

    def verify(eid, status):
        okf = fexists(p_ok)
        failf = fexists(p_fail)
        ok = failf and not okf and status in ("completed", "failed")
        return ok, f"status={status}; fail-edge-file={failf}; pass-edge-file={okf}"
    return nodes, conns, {}, verify


ROWS_JSON = ('[{"store":"Manhattan","units":1000,"revenue":30000},'
             '{"store":"Brooklyn","units":770,"revenue":23100}]')


@check("setvar_to_excel", "Set Variable (JSON rows) -> Excel Export: xlsx with exact rows", tier=2, disk=True)
def c_setvar_excel(ctx):
    p = os.path.join(ctx["out"], "setvar_excel.xlsx").replace("\\", "/")
    nodes = [set_var("node-0", "rows", ROWS_JSON, start=True),
             N("node-1", "Excel Export", "export",
               {"inputVariable": "${rows}", "excelOperation": "new", "excelOutputPath": p,
                "excelSheetName": "Summary", "flattenArray": True,
                "outputVariable": "excelResult"}, x=320)]
    conns = [C("node-0", "node-1")]

    def verify(eid, status):
        rows = read_xlsx_rows(hostpath(p))
        want = {("Manhattan", 1000, 30000), ("Brooklyn", 770, 23100)}
        got = set()
        if rows:
            for r in rows:
                try:
                    got.add((str(r.get("store")), int(r.get("units")), int(r.get("revenue"))))
                except Exception:
                    pass
        ok = status == "completed" and got == want
        return ok, f"status={status}; xlsx rows={rows}"
    return nodes, conns, {}, verify


@check("database_to_excel", "Database -> Excel Export handoff (the pairing that was silently broken)",
       tier=2, needs=["airdb"], disk=True)
# History: XFAIL until 2026-07-31 — the Database node's {'columns','rows'} envelope
# was a shape Excel Export never accepted, so this pairing had NEVER worked (wf
# 1266/1307 evidence). Fixed by unpack_database_envelope in workflow_execution.py
# (+ honest error propagation); flipped XPASS same day and is now a permanent guard.
def c_db_excel(ctx):
    p = os.path.join(ctx["out"], "db_excel.xlsx").replace("\\", "/")
    q = ("SELECT l.store_id, l.store_name, COUNT(e.employee_id) AS headcount "
         "FROM TS.location_master l LEFT JOIN TS.employee_data e ON e.store_id = l.store_id "
         "GROUP BY l.store_id, l.store_name ORDER BY l.store_id")
    nodes = [N("node-0", "Database", "headcount",
               {"connection": str(ctx["env"]["airdb"]), "dbOperation": "query", "query": q,
                "saveToVariable": True, "outputVariable": "dbrows", "continueOnError": False},
               start=True),
             N("node-1", "Excel Export", "export",
               {"inputVariable": "${dbrows}", "excelOperation": "new", "excelOutputPath": p,
                "excelSheetName": "Headcount", "flattenArray": True,
                "outputVariable": "excelResult"}, x=320)]
    conns = [C("node-0", "node-1")]

    def verify(eid, status):
        rows = read_xlsx_rows(hostpath(p))
        ok = (status == "completed" and rows is not None and len(rows) == 10
              and all(int(r.get("headcount", -1)) == 8 for r in rows))
        return ok, f"status={status}; xlsx-rows={None if rows is None else len(rows)} (oracle 10x headcount=8)"
    return nodes, conns, {}, verify


def _approval_wf(ctx, suffix):
    p = os.path.join(ctx["out"], f"after_approval_{suffix}.txt").replace("\\", "/")
    nodes = [set_var("node-0", "payload", "review-me", start=True),
             N("node-1", "Human Approval", "gate",
               {"approvalTitle": f"NODEREG {suffix} gate",
                "approvalDescription": "auto-test approval; safe to decide",
                "approvalData": "${payload}", "assigneeType": "user",
                "assigneeId": str(ctx["env"].get("admin_id", 13)),
                "dueHours": 4, "priority": 1, "timeoutAction": "fail"}, x=320),
             file_node("node-2", "write", p, "approved-path\n", x=540)]
    conns = [C("node-0", "node-1"), C("node-1", "node-2")]
    return nodes, conns, p


def _decide_when_pending(ctx, eid, approve, seen):
    """on_tick callback: approve/reject our execution's pending approval once."""
    if seen["done"]:
        return
    for row in ctx["api"].pending_approvals():
        # approval rows carry UPPERCASE execution GUIDs; compare case-insensitively
        if str(row.get("execution_id", "")).lower() == str(eid).lower():
            rid = row.get("request_id") or row.get("id")
            if rid:
                r = ctx["api"].decide_approval(rid, approve=approve)
                seen["done"] = True
                seen["request_id"] = rid
                seen["http"] = r.status_code
                return


@check("human_approval_approve", "Human Approval pauses -> approve via approvals API -> workflow resumes",
       tier=2, slow=True, disk=True)
def c_approval_approve(ctx):
    nodes, conns, p = _approval_wf(ctx, "ok")
    seen = {"done": False}

    def on_tick(status):
        _decide_when_pending(ctx, ctx["_eid"], True, seen)

    def verify(eid, status):
        wrote = fexists(p)
        ok = status == "completed" and wrote and seen["done"]
        return ok, (f"status={status}; decided={seen.get('done')} "
                    f"(req={seen.get('request_id', '?')}, http={seen.get('http', '?')}); "
                    f"post-approval file={wrote}")
    return nodes, conns, {"on_tick": on_tick, "timeout": 150}, verify


@check("human_approval_reject", "Human Approval rejected -> downstream does NOT run",
       tier=2, slow=True, disk=True)
def c_approval_reject(ctx):
    nodes, conns, p = _approval_wf(ctx, "no")
    seen = {"done": False}

    def on_tick(status):
        _decide_when_pending(ctx, ctx["_eid"], False, seen)

    def verify(eid, status):
        wrote = fexists(p)
        ok = (not wrote) and seen["done"] and status in ("failed", "cancelled", "canceled", "completed")
        return ok, (f"status={status}; decided={seen.get('done')}; "
                    f"downstream-file={wrote} (must be False)")
    return nodes, conns, {"on_tick": on_tick, "timeout": 150}, verify


@check("folder_selector_count", "Folder Selector: lists exactly the fixture files", tier=2, disk=True)
def c_folder_selector(ctx):
    if REMOTE_UNC:
        # seed the probe files onto the ENGINE box via the admin share and point
        # the node at the engine-local path
        fix = "C:/temp/aihub_test/nodereg_folder_probe"
        unc_dir = hostpath(fix)
        os.makedirs(unc_dir, exist_ok=True)
        for name, body in (("probe1.txt", "one"), ("probe2.txt", "two"),
                           ("probe3.txt", "three"), ("decoy.pdf", "decoy")):
            with open(os.path.join(unc_dir, name), "w", encoding="utf-8") as fh:
                fh.write(body)
    else:
        fix = os.path.join(HERE, "fixtures", "folder_probe").replace("\\", "/")
    nodes = [N("node-0", "Folder Selector", "probe",
               {"folderPath": fix, "filePattern": "*.txt", "selectionMode": "all",
                "failIfEmpty": True, "outputVariable": "found"}, start=True)]

    def verify(eid, status):
        v = ctx["api"].variables(eid)
        raw = v.get("found")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                pass
        n = len(raw) if isinstance(raw, list) else None
        ok = status == "completed" and n == 3
        return ok, f"status={status}; files-found={n} (oracle 3)"
    return nodes, [], {}, verify


@check("portal_node_run", "Portal node: runs a saved portal workflow via browser-use, result lands in variables",
       tier=2, needs=["portal"], slow=True)
def c_portal_node(ctx):
    # The Portal node runs a per-user SAVED portal workflow (slug) through the
    # browser-use service, entirely server-side. ownerUserId is stamped into the
    # node config by /save/workflow from the authenticated session (app.py ~6051).
    # agentFallback off => purely deterministic steps (no LLM cost, no variance).
    nodes = [N("node-0", "Portal", "portal probe",
               {"portalWorkflowSlug": ctx["env"]["portal"],
                "outputVariable": "portalResult", "filesVariable": "portalFiles",
                "timeout": 150, "agentFallback": False, "continueOnError": False},
               start=True)]

    def verify(eid, status):
        v = ctx["api"].variables(eid)
        res = v.get("portalResult")
        if isinstance(res, str):
            try:
                res = json.loads(res)
            except Exception:
                pass
        pstatus = res.get("status") if isinstance(res, dict) else None
        nfiles = res.get("file_count") if isinstance(res, dict) else None
        perr = res.get("error") if isinstance(res, dict) else None
        ok = status == "completed" and pstatus == "ok"
        return ok, (f"status={status}; portal-status={pstatus}; files={nfiles}"
                    + (f"; portal-error={str(perr)[:120]}" if perr else ""))
    return nodes, [], {"timeout": 210}, verify


@check("file_transfer_sftp_upload", "File Transfer node: SFTP upload lands on the test server",
       tier=2, needs=["sftp", "sftp_secret"])
def c_file_transfer(ctx):
    stamp = ctx["stamp"]
    local = os.path.join(ctx["out"], f"xfer_{stamp}.txt").replace("\\", "/")
    remote_name = f"nodereg_{stamp}.txt"
    nodes = [file_node("node-0", "write", local, f"nodereg-{stamp}\n", x=100),
             # remotePath is the target DIRECTORY (must exist); uploads keep the local
             # basename — the node errors "Remote directory ... does not exist" otherwise.
             N("node-1", "File Transfer", "upload",
               {"protocol": "sftp", "host": SFTP_HOST, "port": "2222",
                "username": "testuser", "secretName": ctx["env"]["sftp_secret"],
                "operation": "upload", "localPath": local,
                "remotePath": "outgoing",
                "overwrite": "overwrite", "zeroMatchPolicy": "fail",
                "outputVariable": "xfer", "continueOnError": False}, x=320)]
    nodes[0]["isStart"] = True
    conns = [C("node-0", "node-1")]

    def verify(eid, status):
        remote = os.path.join(SFTP_ROOT, "outgoing", os.path.basename(local))
        landed = os.path.exists(remote)
        content_ok = landed and (read_file(remote) or "").strip() == f"nodereg-{stamp}"
        ok = status == "completed" and landed and content_ok
        return ok, f"status={status}; remote-file={landed} ({os.path.basename(local)}); content-ok={content_ok}"
    return nodes, conns, {}, verify


# ---------------- Tier 3 — registered but skipped by default (coverage honesty) ---

TIER3_PLANNED = [
    ("alert_email", "Alert (email) node",
     "excluded by owner decision (james 2026-07-30) — do NOT automate (sends real email)"),
    ("ai_extract", "AI Extract node",
     "excluded by owner decision (james 2026-07-30) — do NOT automate (live LLM cost)"),
    ("ai_action", "AI Action node",
     "excluded by owner decision (james 2026-07-30) — do NOT automate (live LLM cost)"),
    ("document_node", "Document node", "not automated (needs a document-pipeline fixture)"),
    ("excel_update", "Excel Update node", "not automated (needs a template .xlsx fixture)"),
    ("execute_application", "Execute Application node",
     "not automated (needs a harmless fixture app to run)"),
    ("integration_node", "Integration node", "not automated (needs a configured integration instance)"),
    ("compliance_process", "Compliance Process node", "not automated (needs a retailer document set)"),
    ("compliance_excel_export", "Compliance Excel Export node", "not automated (needs compliance fixtures)"),
    ("automation_node", "Automation node", "not automated (needs a promoted automation)"),
    ("code_step", "Code Step node", "not automated (needs a saved code flow)"),
]

ALL_NODE_TYPES = ["Database", "Folder Selector", "Document", "AI Action", "Set Variable",
                  "Alert", "Conditional", "Loop", "End Loop", "Execute Application", "File",
                  "AI Extract", "Excel Export", "Integration", "Compliance Process",
                  "Compliance Excel Export", "Automation", "Code Step", "File Transfer",
                  "Portal", "Human Approval"]

# node types exercised per check id (for the coverage map)
COVERAGE = {
    "setvar_file_write": ["Set Variable", "File"],
    "setvar_expression_eval": ["Set Variable", "File"],
    "setvar_expression_failure_honesty": ["Set Variable", "File"],
    "file_write_append": ["File"],
    "file_check_delete": ["File"],
    "conditional_true": ["Set Variable", "Conditional", "File"],
    "conditional_false": ["Set Variable", "Conditional", "File"],
    "loop_list_append": ["Set Variable", "Loop", "End Loop", "File"],
    "database_select_vars": ["Database"],
    "database_fail_edge": ["Database", "File"],
    "setvar_to_excel": ["Set Variable", "Excel Export"],
    "database_to_excel": ["Database", "Excel Export"],
    "human_approval_approve": ["Set Variable", "Human Approval", "File"],
    "human_approval_reject": ["Set Variable", "Human Approval", "File"],
    "folder_selector_count": ["Folder Selector"],
    "file_transfer_sftp_upload": ["File", "File Transfer"],
    "portal_node_run": ["Portal"],
}


# ------------------------------------------------------------------- lint checks

EXCEL_REQUIRED = ["inputVariable", "excelOutputPath"]
EXCEL_VALID_OPS = {"new", "template", "append"}
# The backend engine follows ONLY these edge types (workflow_execution.py ~656-700).
# Any other type (e.g. 'true'/'false' from programmatic authoring) is a DEAD edge the
# engine silently never follows — the workflow quietly ends instead. The Designer UI
# can only author these three (setArrowType in workflow.js), so dead edges always come
# from API/builder/generated JSON.
ENGINE_EDGE_TYPES = {"pass", "fail", "complete"}


def lint_workflows(api):
    """Config lint over ALL persisted workflows (the /get/workflows list carries each
    workflow_data inline, so this is a single request). Informational."""
    excel_bad, unknown_types, dead_edges = [], [], []
    scanned = 0
    for w in api._workflow_list():
        wid = w.get("id") or w.get("workflow_id") or "?"
        name = w.get("workflow_name") or w.get("name") or ""
        wf = w.get("workflow_data") or w.get("workflow")
        if isinstance(wf, str):
            try:
                wf = json.loads(wf)
            except Exception:
                continue
        nodes = wf.get("nodes") if isinstance(wf, dict) else None
        if not isinstance(nodes, list):
            continue
        scanned += 1
        for n in nodes:
            if not isinstance(n, dict):
                continue
            ntype = n.get("type")
            cfg = n.get("config") or {}
            if ntype and ntype not in ALL_NODE_TYPES:
                unknown_types.append(f"wf {wid} '{name}': unknown node type '{ntype}'")
            if ntype == "Excel Export":
                missing = [k for k in EXCEL_REQUIRED if not cfg.get(k)]
                op = cfg.get("excelOperation")
                bad_op = op not in EXCEL_VALID_OPS
                if missing or bad_op:
                    excel_bad.append(
                        f"wf {wid} '{name}': Excel Export "
                        + (f"missing {missing} " if missing else "")
                        + (f"invalid excelOperation={op!r}" if bad_op else ""))
        conns = wf.get("connections") if isinstance(wf, dict) else None
        if isinstance(conns, list):
            bad = sorted({str(c.get("type")) for c in conns
                          if isinstance(c, dict) and c.get("type")
                          and c.get("type") not in ENGINE_EDGE_TYPES})
            if bad:
                dead_edges.append(f"wf {wid} '{name}': dead edge type(s) {bad} "
                                  f"(engine only follows pass/fail/complete)")
    return {"scanned": scanned, "excel_bad": excel_bad,
            "unknown_types": unknown_types, "dead_edges": dead_edges}


# ------------------------------------------------------------------- environment

def probe_env(api):
    env = {}
    if REMOTE_UNC:
        try:
            env["remote_disk"] = os.path.isdir(REMOTE_UNC + "/temp") or os.path.isdir(REMOTE_UNC)
        except Exception:
            env["remote_disk"] = False
        if not env["remote_disk"]:
            log(f"admin share {REMOTE_UNC} NOT reachable — disk-verified checks will SKIP")
    else:
        env["remote_disk"] = True
    env["airdb"] = api.connection_id("AIRDB")
    # A registered connection isn't enough — the DB server must actually be reachable,
    # otherwise DB checks would FAIL for environmental reasons. Probe TCP 1433 and
    # demote to SKIP when the box is down (recorded as the skip reason).
    if env["airdb"]:
        try:
            with socket.create_connection(("10.0.0.6", 1433), timeout=3):
                pass
        except OSError:
            log("AIRDB connection row exists but 10.0.0.6:1433 is UNREACHABLE — DB checks will SKIP")
            env["airdb"] = None
            env["airdb_note"] = "10.0.0.6:1433 unreachable"
    # SFTP server up?
    try:
        with socket.create_connection(("127.0.0.1", 2222), timeout=2):
            env["sftp"] = True
    except OSError:
        env["sftp"] = None
    secrets = api.secret_names()
    env["sftp_secret"] = next((s for s in ("SFTP_TEST_PASSWORD", "AUTODEMO_SFTP") if s in secrets), None)
    # Portal node: needs the browser-use service (:5101) AND a saved portal workflow to
    # invoke. The probe ensures a deterministic one-step (goto the app's own login page)
    # portal workflow exists for the current user; env["portal"] holds its slug.
    env["portal"] = None
    try:
        # browser-use runs on the TARGET box (the engine calls it in-process)
        _target = re.sub(r"^https?://", "", api.base).split(":")[0].split("/")[0]
        with socket.create_connection((_target, 5101), timeout=2):
            pass
        payload = {"name": "NODEREG-portal-probe", "portal_slug": None,
                   "start_url": f"{api.base}/login",
                   "goal": "Open the AI Hub login page (deterministic regression probe)",
                   "steps": [{"type": "goto", "url": f"{api.base}/login"}],
                   "overwrite": True}
        r = api.post("/api/portal-workflows", payload)
        body = api.jbody(r) or {}
        saved = body.get("saved") if isinstance(body, dict) else None
        if r.status_code == 200 and isinstance(saved, dict) and saved.get("slug"):
            env["portal"] = saved["slug"]
        else:
            env["portal_note"] = f"portal workflow save failed: HTTP {r.status_code} {str(body)[:120]}"
    except OSError:
        env["portal_note"] = "browser-use service 127.0.0.1:5101 unreachable"
    # admin user id (for approval assignee)
    try:
        body = api.jbody(api.get("/get/users"))
        users = body if isinstance(body, list) else ((body or {}).get("users") or (body or {}).get("data") or [])
        for u in users:
            uname = (u.get("user_name") or u.get("username") or "").lower()
            if uname == "admin":
                env["admin_id"] = u.get("id") or u.get("user_id")
    except Exception:
        pass
    env.setdefault("admin_id", 13)
    return env


# ------------------------------------------------------------------- run engine

def run_checks(api, args):
    stamp = now_stamp()
    out_dir = os.path.join(OUT_ROOT, stamp)
    env = probe_env(api)
    if not REMOTE_UNC:
        os.makedirs(out_dir, exist_ok=True)
    elif env.get("remote_disk"):
        os.makedirs(hostpath(out_dir), exist_ok=True)
    log(f"env probe: AIRDB conn id={env['airdb']}, sftp-up={bool(env['sftp'])}, "
        f"sftp-secret={env['sftp_secret']}, admin_id={env['admin_id']}")

    results = []
    created = []  # (wid, name)
    for spec in CHECKS:
        cid = spec["id"]
        if args.only and args.only not in cid:
            continue
        if spec["tier"] > args.tier:
            results.append({"id": cid, "title": spec["title"], "tier": spec["tier"],
                            "status": "SKIP", "evidence": f"tier {spec['tier']} > --tier {args.tier}"})
            continue
        missing = [k for k in spec["needs"] if not env.get(k)]
        if missing:
            results.append({"id": cid, "title": spec["title"], "tier": spec["tier"],
                            "status": "SKIP", "evidence": f"env missing: {missing}"})
            continue
        if spec.get("disk") and REMOTE_UNC and not env.get("remote_disk"):
            results.append({"id": cid, "title": spec["title"], "tier": spec["tier"],
                            "status": "SKIP", "evidence":
                            f"remote mode: engine-box disk not reachable via {REMOTE_UNC}"})
            continue

        ctx = {"api": api, "out": out_dir, "env": env, "stamp": stamp}
        t0 = time.time()
        try:
            nodes, conns, opts, verify = spec["fn"](ctx)
            name = PREFIX + cid
            wid = api.save_workflow(name, nodes, conns)
            created.append((wid, name))
            eid = api.run_workflow(wid)
            ctx["_eid"] = eid
            on_tick = opts.get("on_tick")
            timeout = opts.get("timeout", args.timeout)
            status = api.wait_terminal(eid, timeout=timeout,
                                       on_tick=(lambda s: on_tick(s)) if on_tick else None)
            ok, evidence = verify(eid, status)
            dur = round(time.time() - t0, 1)
            if spec["xfail"]:
                st = "XPASS" if ok else "XFAIL"
            else:
                st = "PASS" if ok else "FAIL"
            if st in ("FAIL", "XFAIL"):
                tail = api.logs_tail(eid, 4)
                if tail:
                    evidence += " | log-tail: " + " ~ ".join(tail[-2:])
            results.append({"id": cid, "title": spec["title"], "tier": spec["tier"],
                            "status": st, "evidence": evidence, "workflow_id": wid,
                            "execution_id": eid, "duration_s": dur,
                            **({"xfail_reason": spec["xfail"]} if spec["xfail"] else {})})
            log(f"{st:6} {cid} ({dur}s) — {evidence[:140]}")
        except Exception as e:
            results.append({"id": cid, "title": spec["title"], "tier": spec["tier"],
                            "status": "ERROR", "evidence": f"runner error: {e}"})
            log(f"ERROR  {cid} — {e}")

    # Tier-3 planned entries (coverage honesty)
    for cid, title, reason in TIER3_PLANNED:
        if args.only and args.only not in cid:
            continue
        results.append({"id": cid, "title": title, "tier": 3, "status": "SKIP",
                        "evidence": f"not yet automated: {reason}"})

    lint = lint_workflows(api) if not args.only else None

    if args.cleanup:
        for wid, name in created:
            code = api.delete_workflow(wid)
            log(f"cleanup: deleted wf {wid} {name} (HTTP {code})")

    return {"stamp": stamp, "out_dir": out_dir, "env": {k: str(v) for k, v in env.items()},
            "results": results, "lint": lint, "created": created}


# ------------------------------------------------------------------- report

def load_baseline():
    files = sorted(glob.glob(os.path.join(HISTORY_DIR, "results_*.json")))
    if not files:
        return None, None
    with open(files[-1], "r", encoding="utf-8") as fh:
        return json.load(fh), os.path.basename(files[-1])


def diff_baseline(results, baseline):
    if not baseline:
        return [], [], []
    prev = {r["id"]: r["status"] for r in baseline.get("results", [])}
    regressions, fixed, attention = [], [], []
    for r in results:
        was = prev.get(r["id"])
        if was is None:
            continue
        if was == "PASS" and r["status"] in ("FAIL", "ERROR"):
            regressions.append((r["id"], was, r["status"]))
        elif was in ("FAIL", "ERROR") and r["status"] == "PASS":
            fixed.append((r["id"], was, r["status"]))
        if r["status"] == "XPASS":
            attention.append((r["id"], was or "-", "XPASS"))
    return regressions, fixed, attention


def coverage_map(results):
    executed, skipped = {}, {}
    for r in results:
        for nt in COVERAGE.get(r["id"], []):
            if r["status"] in ("PASS", "FAIL", "XFAIL", "XPASS", "ERROR"):
                executed.setdefault(nt, []).append(f"{r['id']}:{r['status']}")
            elif r["status"] == "SKIP":
                skipped.setdefault(nt, []).append(r["id"])
    rows = []
    for nt in ALL_NODE_TYPES:
        if nt in executed:
            rows.append((nt, "; ".join(executed[nt])))
        elif nt in skipped:
            rows.append((nt, f"check exists, SKIPPED this run: {', '.join(skipped[nt])}"))
        else:
            planned = next((f"planned ({reason})" for cid, t, reason in TIER3_PLANNED
                            if nt.lower().replace(" ", "_") in cid or cid in nt.lower()), None)
            rows.append((nt, planned or "NOT COVERED"))
    return rows


def git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       cwd=REPO, text=True).strip()
    except Exception:
        return "?"


def write_report(run, args, baseline_name, regressions, fixed, attention):
    ts = run["stamp"]
    counts = {}
    for r in run["results"]:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    executed = [r for r in run["results"] if r["status"] in ("PASS", "FAIL", "XFAIL", "XPASS", "ERROR")]

    verdict = "REGRESSIONS DETECTED" if regressions else (
        "FAILURES (no baseline regression)" if any(r["status"] in ("FAIL", "ERROR") for r in executed)
        else "CLEAN")

    lines = []
    lines.append(f"# Workflow Node Regression Report — {ts}")
    lines.append("")
    lines.append(f"- Build: `{git_commit()}` | Base: `{args.base_url}` | Tier <= {args.tier}"
                 f" | Baseline: `{baseline_name or 'none (first run)'}`")
    lines.append(f"- Outputs: `{run['out_dir']}`")
    lines.append("")
    lines.append(f"## Verdict: **{verdict}** — "
                 + " / ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    lines.append("")
    if regressions:
        lines.append("## 🔴 REGRESSIONS vs baseline (was PASS, now broken)")
        lines.append("")
        lines.append("| check | was | now |")
        lines.append("|---|---|---|")
        for cid, was, now in regressions:
            lines.append(f"| {cid} | {was} | **{now}** |")
        lines.append("")
    if attention:
        lines.append("## 🟡 ATTENTION — known bugs now PASSING (fix landed: update matrix / close task)")
        lines.append("")
        for cid, was, now in attention:
            lines.append(f"- **{cid}**: was {was}, now {now}")
        lines.append("")
    if fixed:
        lines.append("## 🟢 Fixed vs baseline")
        lines.append("")
        for cid, was, now in fixed:
            lines.append(f"- {cid}: {was} → {now}")
        lines.append("")

    lines.append("## Full matrix")
    lines.append("")
    lines.append("| check | tier | status | evidence |")
    lines.append("|---|---|---|---|")
    for r in run["results"]:
        ev = (r.get("evidence") or "").replace("|", "\\|")
        badge = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "XFAIL": "⚠️ XFAIL", "XPASS": "🟡 XPASS",
                 "SKIP": "⏭ SKIP", "ERROR": "💥 ERROR"}[r["status"]]
        lines.append(f"| {r['id']} | {r['tier']} | {badge} | {ev[:260]} |")
    lines.append("")

    lines.append("## Node-type coverage map (all 21 engine node types)")
    lines.append("")
    lines.append("| node type | covered by |")
    lines.append("|---|---|")
    for nt, cov in coverage_map(run["results"]):
        mark = ("🚫 " if cov == "NOT COVERED"
                else "📋 " if cov.startswith("planned")
                else "⏭ " if cov.startswith("check exists") else "")
        lines.append(f"| {nt} | {mark}{cov} |")
    lines.append("")

    if run.get("lint"):
        lint = run["lint"]
        lines.append(f"## Config lint (informational) — scanned ALL {lint['scanned']} persisted workflows")
        lines.append("")
        lines.append(f"- Excel Export nodes with broken config: **{len(lint['excel_bad'])}**")
        for b in lint["excel_bad"][:15]:
            lines.append(f"  - {b}")
        if len(lint["excel_bad"]) > 15:
            lines.append(f"  - … {len(lint['excel_bad']) - 15} more")
        lines.append(f"- Unknown node types: **{len(lint['unknown_types'])}**")
        for b in lint["unknown_types"][:10]:
            lines.append(f"  - {b}")
        lines.append(f"- Dead edge types (engine follows only pass/fail/complete): "
                     f"**{len(lint.get('dead_edges', []))}**")
        for b in lint.get("dead_edges", [])[:10]:
            lines.append(f"  - {b}")
        lines.append("")

    lines.append("## XFAIL registry (known bugs the matrix tracks)")
    lines.append("")
    for spec in CHECKS:
        if spec["xfail"]:
            lines.append(f"- **{spec['id']}** — {spec['xfail']}")
    lines.append("")
    report = "\n".join(lines)

    os.makedirs(HISTORY_DIR, exist_ok=True)
    with open(os.path.join(HISTORY_DIR, f"results_{ts}.json"), "w", encoding="utf-8") as fh:
        json.dump(run, fh, indent=1, default=str)
    with open(os.path.join(HISTORY_DIR, f"REPORT_{ts}.md"), "w", encoding="utf-8") as fh:
        fh.write(report)
    with open(os.path.join(HERE, "REPORT_LATEST.md"), "w", encoding="utf-8") as fh:
        fh.write(report)
    return verdict, report


# ------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Workflow node regression matrix")
    ap.add_argument("--base-url", default=os.environ.get("NODEREG_BASE", "http://localhost:5001"))
    ap.add_argument("--user", default=os.environ.get("NODEREG_USER", "admin"))
    ap.add_argument("--password", default=os.environ.get("NODEREG_PASS", "admin"))
    ap.add_argument("--tier", type=int, default=2, help="run checks up to this tier (default 2)")
    ap.add_argument("--only", help="run only checks whose id contains this substring")
    ap.add_argument("--timeout", type=int, default=90, help="per-check execution timeout (s)")
    ap.add_argument("--cleanup", action="store_true", help="delete NODEREG workflows afterwards")
    ap.add_argument("--list", action="store_true", help="list checks and exit")
    ap.add_argument("--remote", action="store_true",
                    help="target is an INSTALLED app on another machine (derive host "
                         "from --base-url; verify engine-box files via \host\c$)")
    ap.add_argument("--sftp-host", default=None,
                    help="host the ENGINE box dials for SFTP checks (remote mode: "
                         "this dev machine's LAN IP; default 127.0.0.1)")
    args = ap.parse_args()

    global REMOTE_UNC, SFTP_HOST
    if args.remote:
        _host = re.sub(r"^https?://", "", args.base_url).split(":")[0].split("/")[0]
        REMOTE_UNC = f"//{_host}/c$"
        log(f"remote mode: target={_host}, disk via {REMOTE_UNC}")
    if args.sftp_host:
        SFTP_HOST = args.sftp_host

    if args.list:
        for spec in CHECKS:
            flag = " [XFAIL]" if spec["xfail"] else ""
            print(f"T{spec['tier']}  {spec['id']:<28} {spec['title']}{flag}")
        for cid, title, reason in TIER3_PLANNED:
            print(f"T3  {cid:<28} {title}  [planned: {reason}]")
        return 0

    api = Api(args.base_url, args.user, args.password)
    baseline, baseline_name = load_baseline()
    run = run_checks(api, args)
    regressions, fixed, attention = diff_baseline(run["results"], baseline)
    verdict, report = write_report(run, args, baseline_name, regressions, fixed, attention)

    print("\n" + "=" * 72)
    print(report.split("## Full matrix")[0])
    print(f"Report: {os.path.join(HERE, 'REPORT_LATEST.md')}")
    if regressions:
        return 2
    if any(r["status"] in ("FAIL", "ERROR") for r in run["results"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
