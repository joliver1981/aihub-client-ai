"""AI Hub Demo Control Panel — local demo-ops console (http://127.0.0.1:3100).

One screen to prepare any demo: live health checks for every dependency (services,
database servers/databases, seeded data, platform objects, fixture files, playbooks),
start/reset actions, and one-click access to the playbook docs. Everything is driven by
registry.json — add a demo or a resource there and the panel picks it up on restart.

Run:  Start_Demo_Control_Panel.bat   (aihub2.1 python; Flask + requests + pyodbc)
"""
import fnmatch
import glob
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Flask, jsonify, request, Response

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
LOGS = os.path.join(HERE, "logs")
PORT = int(os.getenv("DEMO_PANEL_PORT", "3100"))

with open(os.path.join(HERE, "registry.json"), encoding="utf-8") as fh:
    REG = json.load(fh)
SET = REG.get("settings", {})

app = Flask(__name__)

RESULTS = {}          # resource_id -> {status, detail, ts}
JOBS = {}             # job_id -> {action, name, status, output, started, ended}
_LOCK = threading.Lock()
_POOL = ThreadPoolExecutor(max_workers=12)


# ------------------------------------------------------------------ AI Hub admin session
class Hub:
    def __init__(self):
        self.base = SET.get("aihub_base", "http://127.0.0.1:5001")
        self.s = None
        self.lock = threading.Lock()

    @staticmethod
    def _hidden(html):
        out = dict(re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', html))
        out.update(dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"', html)))
        return out

    def _login(self):
        s = requests.Session()
        r = s.get(f"{self.base}/login", timeout=8)
        data = {"username": SET.get("admin_user", "admin"),
                "password": SET.get("admin_pass", "admin"), "submit": "Login"}
        data.update(self._hidden(r.text))
        r = s.post(f"{self.base}/login", data=data, allow_redirects=True, timeout=12)
        if "/login" in r.url:
            raise RuntimeError("admin login failed")
        return s

    def session(self, fresh=False):
        with self.lock:
            if fresh or self.s is None:
                self.s = self._login()
            return self.s

    def get_json(self, path):
        for attempt in (0, 1):
            s = self.session(fresh=attempt == 1)
            r = s.get(f"{self.base}{path}", timeout=15, allow_redirects=False)
            if r.status_code in (301, 302):
                continue
            try:
                b = r.json()
                return json.loads(b) if isinstance(b, str) else b
            except Exception:
                return None
        return None

    def call(self, method, path):
        s = self.session()
        r = s.request(method, f"{self.base}{path}", timeout=30, allow_redirects=False)
        if r.status_code in (301, 302):
            s = self.session(fresh=True)
            r = s.request(method, f"{self.base}{path}", timeout=30, allow_redirects=False)
        return r


HUB = Hub()


def _rows(body, *keys):
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for k in keys:
            if isinstance(body.get(k), list):
                return body[k]
    return []


# ------------------------------------------------------------------ check engine
def _ok(detail):
    return ("ok", detail)


def _down(detail):
    return ("down", detail)


def check_http(c):
    try:
        r = requests.get(c["url"], timeout=5)
        if c.get("any_response") or r.status_code < 500:
            return _ok(f"HTTP {r.status_code}")
        return _down(f"HTTP {r.status_code}")
    except Exception as e:
        return _down(f"no response ({type(e).__name__})")


def check_tcp(c):
    try:
        with socket.create_connection((c["host"], int(c["port"])), timeout=4):
            return _ok(f"port {c['port']} open")
    except Exception:
        return _down(f"port {c['port']} unreachable")


def _sql_value(cs, query, tenant=False):
    """One SQL scalar with ONE retry — parallel pre-flight fan-out can hit a transient
    08001 on the shared test server, and a monitoring panel must not flap on those."""
    import pyodbc
    last = None
    for attempt in (0, 1):
        try:
            cn = pyodbc.connect(cs, timeout=6)
            cur = cn.cursor()
            if tenant:
                try:
                    cur.execute("EXEC tenant.sp_setTenantContext ?",
                                os.getenv("API_KEY") or "DB27D555-03A8-446E-9C23-8DAAA95EAD21")
                except Exception:
                    pass
            cur.execute(query)
            val = cur.fetchone()[0]
            cn.close()
            return val, None
        except Exception as e:
            last = e
            time.sleep(1.2)
    return None, last


def check_sql(c):
    try:
        import pyodbc  # noqa: F401
    except ImportError:
        return ("error", "pyodbc not available in panel environment")
    sql = SET.get("sql", {})
    cs = ("DRIVER={ODBC Driver 17 for SQL Server};"
          f"SERVER={sql.get('server')};DATABASE={c['database']};"
          f"UID={sql.get('user')};PWD={sql.get('password')};TrustServerCertificate=yes")
    val, err = _sql_value(cs, c.get("query", "SELECT 1"))
    if err is not None:
        return _down(f"connect/query failed after retry ({str(err)[:90]})")
    label = c.get("label", "value")
    if "expect" in c:
        if int(val) == int(c["expect"]):
            return _ok(f"{label} = {val}")
        return _down(f"{label} = {val}, expected {c['expect']}")
    if "min" in c:
        if int(val) >= int(c["min"]):
            return _ok(f"{label} = {val}")
        return _down(f"{label} = {val}, expected >= {c['min']}")
    return _ok(f"{label} = {val}")


_APP_CS = {"cs": None}


def check_app_sql(c):
    """Query the AI Hub APPLICATION database (via the app's own CommonUtils connection
    string) — for objects /get/* APIs don't expose, e.g. data agents (is_data_agent=1)."""
    try:
        import pyodbc  # noqa: F401
        if not _APP_CS["cs"]:
            import sys
            if REPO not in sys.path:
                sys.path.insert(0, REPO)
            from CommonUtils import get_db_connection_string
            _APP_CS["cs"] = get_db_connection_string()
    except Exception as e:
        return _down(f"app-db connection unavailable ({str(e)[:90]})")
    val, err = _sql_value(_APP_CS["cs"], c["query"], tenant=True)
    if err is not None:
        return _down(f"app-db query failed after retry ({str(err)[:90]})")
    label = c.get("label", "value")
    if "expect" in c:
        if int(val) == int(c["expect"]):
            return _ok(f"{label} = {val}")
        return ("missing", c.get("fail_detail", f"{label} = {val}, expected {c['expect']}"))
    return _ok(f"{label} = {val}")


def check_agent(c):
    body = HUB.get_json("/get/agents")
    if body is None:
        return ("error", "could not query /get/agents (main app / login?)")
    for a in _rows(body, "data", "agents"):
        if isinstance(a, dict) and (a.get("description") or a.get("agent_description") or "").strip() == c["name"]:
            aid = a.get("agent_id") or a.get("id")
            enabled = a.get("enabled", a.get("agent_enabled", True))
            if enabled in (False, 0, "0", "false"):
                return ("warn", f"agent {aid} exists but is DISABLED")
            return _ok(f"agent id {aid}")
    return ("missing", "agent not found")


def check_workflow(c):
    body = HUB.get_json("/get/workflows")
    if body is None:
        return ("error", "could not query /get/workflows")
    for w in _rows(body, "workflows", "data", "items"):
        if isinstance(w, dict) and (w.get("workflow_name") or w.get("name") or "") == c["name"]:
            return _ok(f"workflow id {w.get('id') or w.get('workflow_id')}")
    return ("missing", "workflow not found")


def check_automation(c, want_present=True):
    body = HUB.get_json("/automations/api/list")
    if body is None:
        return ("error", "could not query /automations/api/list")
    names = [(a.get("name") or a.get("id") or "") for a in _rows(body, "automations")
             if isinstance(a, dict)]
    present = c["name"] in names
    if want_present:
        return _ok("installed") if present else ("missing", "automation not installed")
    if present:
        return ("warn", "still installed — delete it so the live build starts clean")
    return _ok("not installed (clean)")


def _portal_store(fname):
    try:
        with open(os.path.join(REPO, "data", fname), encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def check_portal_workflow(c):
    data = _portal_store("portal_workflows.json")
    wfs = (data.get("users", {}).get(str(c.get("user", "13")), {}) or {}).get("workflows", {}) or {}
    if c["slug"] in wfs:
        n = len(wfs[c["slug"]].get("steps") or [])
        return _ok(f"saved ({n} steps, last: {wfs[c['slug']].get('last_run_status') or 'never run'})")
    return ("missing", "portal workflow not saved")


def check_portal_registration(c):
    data = _portal_store("portal_registry.json")
    portals = (data.get("users", {}).get(str(c.get("user", "13")), {}) or {}).get("portals", {}) or {}
    if c["slug"] not in portals:
        return ("missing", "portal not registered")
    has_totp = bool(portals[c["slug"]].get("totp_secret"))
    if c.get("expect_totp") is False:
        # A takeover-demo portal must NOT have a seed, or the pause never happens.
        if has_totp:
            return ("warn", "has a TOTP seed — the 2FA-takeover demo would auto-pass; remove the seed")
        return _ok("registered, manual 2FA (no seed) — as intended")
    if has_totp:
        return _ok("registered with TOTP seed")
    return ("warn", "registered but NO TOTP seed — 2FA would need human takeover")


def check_files(c):
    d = c["dir"]
    if not os.path.isdir(d):
        return ("missing", "folder does not exist")
    names = [f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))]
    match = [f for f in names if fnmatch.fnmatch(f, c.get("pattern", "*"))]
    n = len(match)
    if "exact" in c and n != int(c["exact"]):
        return ("missing", f"{n} matching file(s), demo expects exactly {c['exact']}")
    if n < int(c.get("min", 1)):
        return ("missing", f"only {n} file(s), need {c.get('min', 1)}")
    if c.get("stray_warn"):
        stray = [f for f in names if not fnmatch.fnmatch(f, c.get("pattern", "*"))
                 and fnmatch.fnmatch(f, c.get("stray_pattern", "*"))]
        if stray:
            return ("warn", f"{n} demo file(s) OK, but {len(stray)} stray file(s) present "
                            f"(e.g. {stray[0]}) — a run would process them too; stage them aside")
    return _ok(f"{n} file(s)")


def check_file(c):
    if os.path.isfile(c["path"]):
        return _ok(f"{os.path.getsize(c['path']):,} bytes")
    return ("missing", "file not found")


CHECKS = {
    "http": check_http, "tcp": check_tcp, "sql": check_sql, "app_sql": check_app_sql,
    "agent": check_agent,
    "workflow": check_workflow,
    "automation": lambda c: check_automation(c, True),
    "automation_absent": lambda c: check_automation(c, False),
    "portal_workflow": check_portal_workflow,
    "portal_registration": check_portal_registration,
    "files": check_files, "file": check_file,
}


def run_check(rid):
    res = REG["resources"].get(rid)
    if not res:
        return
    c = res.get("check", {})
    fn = CHECKS.get(c.get("kind"))
    try:
        status, detail = fn(c) if fn else ("error", f"unknown check kind {c.get('kind')}")
    except Exception as e:
        status, detail = "error", f"{type(e).__name__}: {str(e)[:120]}"
    if status == "down" and res.get("severity") == "warn":
        status = "warn"
    with _LOCK:
        RESULTS[rid] = {"status": status, "detail": detail, "ts": time.time()}


def run_checks(ids):
    list(_POOL.map(run_check, ids))


# ------------------------------------------------------------------ actions
def _py(key):
    return SET.get("pythons", {}).get(key) or "python"


def _job(action_id, name):
    jid = uuid.uuid4().hex[:8]
    with _LOCK:
        JOBS[jid] = {"id": jid, "action": action_id, "name": name, "status": "running",
                     "output": "", "started": time.time(), "ended": None}
    return jid


def _finish(jid, status, output):
    with _LOCK:
        JOBS[jid].update({"status": status, "output": str(output)[-4000:], "ended": time.time()})


def do_action(jid, aid):
    a = REG["actions"][aid]
    kind = a.get("kind")
    try:
        if kind == "spawn":
            os.makedirs(LOGS, exist_ok=True)
            logf = open(os.path.join(LOGS, f"{aid}.log"), "ab")
            flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_PROCESS_GROUP | NO_WINDOW
            subprocess.Popen([_py(a["python"]), a["script"]], cwd=a.get("cwd") or HERE,
                             stdout=logf, stderr=subprocess.STDOUT, creationflags=flags)
            time.sleep(3)
            out = f"started (log: logs/{aid}.log)"
            status = "done"
        elif kind == "run":
            r = subprocess.run([_py(a["python"]), a["script"]], cwd=a.get("cwd") or HERE,
                               capture_output=True, text=True, timeout=a.get("timeout", 240))
            out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
            status = "done" if r.returncode == 0 else "error"
            out = f"[exit {r.returncode}]\n{out}"
        elif kind == "http_admin":
            r = HUB.call(a.get("method", "POST"), a["path"])
            out = f"HTTP {r.status_code}: {r.text[:400]}"
            status = "done" if r.status_code < 400 else "error"
        elif kind == "delete_automation":
            body = HUB.get_json("/automations/api/list") or {}
            target = None
            for row in _rows(body, "automations"):
                if isinstance(row, dict) and (row.get("name") or row.get("id")) == a["automation"]:
                    # List rows key the id as `automation_id` (a UUID) — the DELETE route
                    # takes that, never the name (name → 404).
                    target = row.get("automation_id") or row.get("id")
            if not target:
                out, status = "already absent — nothing to delete", "done"
            else:
                r = HUB.call("DELETE", f"/automations/api/{target}")
                out = f"DELETE {a['automation']} ({target}): HTTP {r.status_code} {r.text[:300]}"
                status = "done" if r.status_code < 400 else "error"
        elif kind == "restore_scans":
            src, dst, pat = a["src"], a["dst"], a.get("pattern", "*.pdf")
            os.makedirs(dst, exist_ok=True)
            have = {f.lower() for f in os.listdir(dst)} if os.path.isdir(dst) else set()
            copied = []
            for p in glob.glob(os.path.join(src, pat)) if os.path.isdir(src) else []:
                if os.path.basename(p).lower() not in have:
                    shutil.copy2(p, dst)
                    copied.append(os.path.basename(p))
            n = len([f for f in os.listdir(dst) if fnmatch.fnmatch(f, pat)])
            out = f"copied {copied or 'nothing (already present)'}; {n} demo scan(s) now in input_docs"
            status = "done" if n >= 3 else "error"
            if n < 3 and not os.path.isdir(src):
                out += f" — source folder missing: {src}"
        elif kind == "delete_portal_jobs":
            body = None
            for attempt in (0, 1):
                r = HUB.call("GET", "/api/scheduler/jobs")
                try:
                    body = r.json()
                    body = json.loads(body) if isinstance(body, str) else body
                    break
                except Exception:
                    continue
            rows = []
            if isinstance(body, list):
                rows = body
            elif isinstance(body, dict):
                for v in body.values():
                    if isinstance(v, list):
                        rows.extend(v)
            hits = [j for j in rows if isinstance(j, dict)
                    and "portal_workflow" in json.dumps(j, default=str)]
            deleted = []
            for j in hits:
                jid = j.get("id") or j.get("job_id")
                if jid is not None:
                    dr = HUB.call("DELETE", f"/api/scheduler/jobs/{jid}")
                    deleted.append(f"#{jid} (http={dr.status_code})")
            out = (f"deleted {len(deleted)} portal-workflow schedule(s): {', '.join(deleted)}"
                   if deleted else "no portal-workflow schedules found")
            status = "done"
        elif kind == "curate_dir":
            d, keep, aside = a["dir"], a["keep"], a["aside"]
            os.makedirs(aside, exist_ok=True)
            moved = []
            for f in os.listdir(d):
                p = os.path.join(d, f)
                if os.path.isfile(p) and not fnmatch.fnmatch(f, keep) \
                        and fnmatch.fnmatch(f, a.get("match", "*")):
                    shutil.move(p, os.path.join(aside, f))
                    moved.append(f)
            out = (f"moved {len(moved)} stray file(s) to {aside}"
                   + (f": {', '.join(moved[:6])}{'…' if len(moved) > 6 else ''}" if moved
                      else " (nothing to move)"))
            status = "done"
        else:
            out, status = f"unknown action kind {kind}", "error"
    except Exception as e:
        out, status = f"{type(e).__name__}: {e}", "error"
    _finish(jid, status, out)
    for rid in a.get("recheck", []):
        run_check(rid)


# ------------------------------------------------------------------ API
PLAYBOOKS_DIR = os.path.join(HERE, "playbooks")


def _web_url(doc):
    stem = os.path.splitext(os.path.basename(doc or ""))[0]
    if stem and os.path.isfile(os.path.join(PLAYBOOKS_DIR, stem + ".html")):
        return f"/playbooks/{stem}.html"
    return None


@app.route("/playbooks/<path:fname>")
def playbooks(fname):
    from flask import send_from_directory
    full = os.path.join(PLAYBOOKS_DIR, fname)
    if not os.path.isfile(full):
        return Response("Web playbook not generated yet — run export_playbooks.py "
                        "(or the “Regenerate web playbooks” action).",
                        status=404, mimetype="text/plain")
    return send_from_directory(PLAYBOOKS_DIR, fname)


@app.route("/api/state")
def api_state():
    with _LOCK:
        results = dict(RESULTS)
        jobs = sorted(JOBS.values(), key=lambda j: j["started"], reverse=True)[:12]
    return jsonify({
        "demos": [{**d, "web": _web_url(d.get("doc"))} for d in REG["demos"]],
        "resources": {k: {kk: v.get(kk) for kk in
                          ("name", "short", "group", "severity", "fix", "actions", "links")}
                      for k, v in REG["resources"].items()},
        "actions": {k: {"name": v.get("name"), "confirm": v.get("confirm"), "kind": v.get("kind")}
                    for k, v in REG["actions"].items()},
        "results": results, "jobs": jobs, "now": time.time(),
    })


@app.route("/api/check", methods=["POST"])
def api_check():
    body = request.get_json(silent=True) or {}
    ids = body.get("ids")
    if body.get("demo"):
        demo = next((d for d in REG["demos"] if d["id"] == body["demo"]), None)
        ids = demo["resources"] if demo else []
    if not ids:
        ids = list(REG["resources"].keys())
    run_checks(ids)
    return api_state()


@app.route("/api/action/<aid>", methods=["POST"])
def api_action(aid):
    if aid not in REG["actions"]:
        return jsonify({"error": "unknown action"}), 404
    jid = _job(aid, REG["actions"][aid].get("name", aid))
    threading.Thread(target=do_action, args=(jid, aid), daemon=True).start()
    return jsonify({"job": jid})


@app.route("/api/open", methods=["POST"])
def api_open():
    body = request.get_json(silent=True) or {}
    demo = next((d for d in REG["demos"] if d["id"] == body.get("demo")), None)
    path = (demo or {}).get("doc") or body.get("path")
    if not path or not os.path.isfile(path):
        return jsonify({"error": f"doc not found: {path}"}), 404
    os.startfile(path)  # noqa: S606 — local ops tool, opens the playbook in Word
    return jsonify({"opened": path})


@app.route("/healthz")
def healthz():
    return Response("ok", mimetype="text/plain")


# ------------------------------------------------------------------ UI
PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>AI Hub Demo Control Panel</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 * { box-sizing: border-box; margin: 0; }
 body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0e151d; color: #d7e1ea;
        padding-bottom: 60px; }
 header { background: #101c28; border-bottom: 1px solid #1f3140; padding: 14px 26px;
          display: flex; align-items: center; gap: 16px; position: sticky; top: 0; z-index: 5; }
 header h1 { font-size: 18px; color: #e8f0f7; font-weight: 600; }
 header .sub { font-size: 12px; color: #6d8296; }
 .btn { background: #14405a; color: #cfe6f5; border: 1px solid #1f5f85; border-radius: 7px;
        padding: 7px 14px; font-size: 13px; cursor: pointer; font-weight: 600; }
 .btn:hover { background: #175177; }
 .btn.small { padding: 4px 10px; font-size: 12px; font-weight: 500; }
 .btn.ghost { background: transparent; border-color: #2a4356; color: #9db4c6; }
 .btn.danger { background: #4a1f24; border-color: #7a343c; color: #f0c9cd; }
 .right { margin-left: auto; display: flex; gap: 10px; align-items: center; font-size: 12px; color: #6d8296; }
 main { padding: 20px 26px; max-width: 1500px; margin: 0 auto; }
 #warnings { margin-bottom: 18px; }
 .warnbar { background: #3a1519; border: 1px solid #7a343c; border-left: 5px solid #e05561;
            color: #f4d7da; border-radius: 8px; padding: 10px 14px; font-size: 13.5px; margin-bottom: 8px; }
 .warnbar.amber { background: #33270f; border-color: #7a6023; border-left-color: #d9a53c; color: #f0e3c0; }
 .okbar { background: #12291c; border: 1px solid #23573a; border-left: 5px solid #3fae6d;
          color: #cfe9d9; border-radius: 8px; padding: 10px 14px; font-size: 13.5px; }
 h2 { font-size: 15px; color: #8fb4cf; text-transform: uppercase; letter-spacing: .8px;
      margin: 26px 0 12px; }
 .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(430px, 1fr)); gap: 14px; }
 .card { background: #121d29; border: 1px solid #1f3140; border-radius: 10px; padding: 16px 18px;
         min-width: 0; overflow: hidden; }
 .card .top { display: flex; align-items: flex-start; gap: 10px; }
 .card h3 { font-size: 15.5px; color: #e8f0f7; overflow-wrap: anywhere; }
 .card .cat { font-size: 11px; color: #6d8296; }
 .card .tag { font-size: 12.5px; color: #9db4c6; font-style: italic; margin: 6px 0 10px;
              overflow-wrap: anywhere; }
 .chip { margin-left: auto; font-size: 11.5px; font-weight: 700; padding: 3px 11px;
         border-radius: 20px; white-space: nowrap; }
 .chip.ready { background: #14402a; color: #6fe0a1; }
 .chip.caution { background: #4a3a11; color: #ecc45c; }
 .chip.notready { background: #4a1f24; color: #f08a94; }
 .chip.unknown { background: #223140; color: #8aa2b5; }
 .deps { display: flex; flex-wrap: wrap; gap: 3px 12px; font-size: 12px; color: #7d93a6;
         margin: 4px 0 10px; line-height: 1.7; }
 .deps span { white-space: nowrap; max-width: 100%; overflow: hidden; text-overflow: ellipsis; }
 .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 5px; }
 .ok { background: #3fae6d; } .warn { background: #d9a53c; }
 .down, .error { background: #e05561; } .missing { background: #e05561; }
 .unknown { background: #4a5f70; }
 .row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 6px; }
 a.link { color: #6db7e8; font-size: 12px; text-decoration: none; margin-right: 10px; }
 a.link:hover { text-decoration: underline; }
 table { width: 100%; border-collapse: collapse; background: #121d29; border: 1px solid #1f3140;
         border-radius: 10px; overflow: hidden; }
 th { text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: .6px;
      color: #6d8296; padding: 9px 12px; border-bottom: 1px solid #1f3140; background: #101a24; }
 td { padding: 8px 12px; border-bottom: 1px solid #18242f; font-size: 13px; vertical-align: middle; }
 td.detail { color: #8aa2b5; font-family: Consolas, monospace; font-size: 12px; }
 td .demochip { display: inline-block; background: #1a2c3c; color: #9dc4de; font-size: 10.5px;
                padding: 1px 8px; border-radius: 10px; margin: 1px 3px 1px 0; }
 .grouphead td { background: #101a24; color: #8fb4cf; font-weight: 700; font-size: 12px; }
 #log { position: fixed; bottom: 0; left: 0; right: 0; background: #0b1117; border-top: 1px solid #1f3140;
        max-height: 40vh; overflow: auto; padding: 10px 26px; display: none; }
 #log pre { font-family: Consolas, monospace; font-size: 12px; color: #b7c9d8; white-space: pre-wrap; }
 #log .jobhead { color: #8fb4cf; font-size: 12.5px; font-weight: 700; margin-top: 8px; }
 .spin { display: inline-block; animation: r 1s linear infinite; }
 @keyframes r { to { transform: rotate(360deg); } }
</style></head>
<body>
<header>
  <div><h1>🎛 AI Hub Demo Control Panel</h1>
       <div class="sub">pre-flight · start/reset · playbooks — registry-driven (registry.json)</div></div>
  <div class="right">
    <span id="stamp">checks not yet run</span>
    <button class="btn" id="runall" onclick="checkAll()">▶ Run all checks</button>
    <button class="btn ghost" onclick="toggleLog()">Activity log</button>
  </div>
</header>
<main>
  <div id="warnings"></div>
  <div id="demos"></div>
  <h2>Resource matrix — every dependency, and which demos need it</h2>
  <div style="overflow-x:auto"><table id="matrix"></table></div>
</main>
<div id="log"><div class="jobhead">Recent actions</div><div id="jobs"></div></div>
<script>
let S = null;
const label = {ok:'OK', warn:'WARN', down:'DOWN', missing:'MISSING', error:'ERROR', unknown:'—'};

async function state(){ S = await (await fetch('/api/state')).json(); render(); }
async function checkAll(){ busy(true); S = await (await fetch('/api/check',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json(); busy(false); render(); }
async function checkDemo(id){ busy(true); S = await (await fetch('/api/check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({demo:id})})).json(); busy(false); render(); }
function busy(b){ const x=document.getElementById('runall'); x.innerHTML = b?'<span class="spin">◐</span> Checking…':'▶ Run all checks'; x.disabled=b; }
async function act(id){
  const a = S.actions[id];
  if(a.confirm && !confirm(a.name + '\n\n' + a.confirm)) return;
  await fetch('/api/action/'+id, {method:'POST'});
  document.getElementById('log').style.display='block';
  pollJobs();
}
async function openDoc(id){ await fetch('/api/open',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({demo:id})}); }
function toggleLog(){ const l=document.getElementById('log'); l.style.display = l.style.display==='block'?'none':'block'; }
let poller=null;
function pollJobs(){ if(poller) return; poller=setInterval(async()=>{ S=await (await fetch('/api/state')).json(); render(); if(!S.jobs.some(j=>j.status==='running')){clearInterval(poller);poller=null;} },2000); }

function st(rid){ return (S.results[rid]||{status:'unknown',detail:'not checked yet'}); }
function rollup(demo){
  let unknown=0, bad=0, warn=0;
  for(const rid of demo.resources){ const s=st(rid).status;
    if(s==='unknown') unknown++;
    else if(s==='warn') warn++;
    else if(s!=='ok') bad++; }
  if(bad) return {cls:'notready', text:'NOT READY — '+bad+' issue'+(bad>1?'s':'')};
  if(unknown) return {cls:'unknown', text:'RUN PRE-FLIGHT'};
  if(warn) return {cls:'caution', text:'CAUTION — '+warn+' warning'+(warn>1?'s':'')};
  return {cls:'ready', text:'READY'};
}

function render(){
  const el = document.getElementById('demos');
  const cats = [...new Set(S.demos.map(d=>d.category))];
  el.innerHTML = cats.map(cat => '<h2>'+cat+'</h2><div class="cards">' +
    S.demos.filter(d=>d.category===cat).map(d=>{
      const r = rollup(d);
      const deps = d.resources.map(rid=>{ const s=st(rid); const rr=S.resources[rid];
        return '<span title="'+rr.name+' — '+(s.detail||'')+'"><i class="dot '+s.status+'"></i>'+(rr.short||rr.name.split(' (')[0])+'</span>';}).join('');
      const resets = (d.reset||[]).map(a=>'<button class="btn small danger" onclick="act(\''+a+'\')">♻ '+S.actions[a].name+'</button>').join('');
      const links = (d.links||[]).map(l=>'<a class="link" target="_blank" href="'+l.url+'">'+l.label+' ↗</a>').join('');
      const play = d.web ? '<a class="btn small ghost" style="text-decoration:none" target="_blank" href="'+d.web+'">📖 Playbook</a>'
                         : '<button class="btn small ghost" onclick="alert(\'Web playbook not generated yet — run the Regenerate web playbooks action (Documents section).\')">📖 Playbook</button>';
      return '<div class="card"><div class="top"><div><h3>'+d.name+'</h3>'+
        '<div class="cat">'+d.duration+'</div></div><span class="chip '+r.cls+'">'+r.text+'</span></div>'+
        '<div class="tag">'+d.tagline+'</div><div class="deps">'+deps+'</div>'+
        '<div class="row"><button class="btn small" onclick="checkDemo(\''+d.id+'\')">▶ Pre-flight</button>'+
        play+resets+'</div>'+
        '<div class="row">'+links+'</div></div>';
    }).join('') + '</div>').join('');

  const usedBy = {};
  S.demos.forEach(d=>d.resources.forEach(rid=>{(usedBy[rid]=usedBy[rid]||[]).push(d.name.split(' —')[0].split(' —')[0]);}));
  const groups = [...new Set(Object.values(S.resources).map(r=>r.group))];
  let rowsHtml = '<tr><th style="width:26%">Resource</th><th style="width:8%">Status</th><th style="width:30%">Detail</th><th style="width:20%">Needed by</th><th>Actions</th></tr>';
  for(const g of groups){
    rowsHtml += '<tr class="grouphead"><td colspan="5">'+g+'</td></tr>';
    for(const [rid,r] of Object.entries(S.resources).filter(([,r])=>r.group===g)){
      const s = st(rid);
      const acts = (r.actions||[]).map(a=>'<button class="btn small" onclick="act(\''+a+'\')">'+S.actions[a].name+'</button>').join(' ');
      const links = (r.links||[]).map(l=>'<a class="link" target="_blank" href="'+l.url+'">'+l.label+' ↗</a>').join('');
      const fix = (s.status!=='ok' && r.fix) ? '<div style="color:#d9a53c;font-size:11.5px;margin-top:2px">fix: '+r.fix+'</div>' : '';
      rowsHtml += '<tr><td>'+r.name+'</td>'+
        '<td><i class="dot '+s.status+'"></i>'+label[s.status]+'</td>'+
        '<td class="detail">'+ (s.detail||'') + fix +'</td>'+
        '<td>'+(usedBy[rid]||[]).map(n=>'<span class="demochip">'+n+'</span>').join('')+'</td>'+
        '<td>'+acts+' '+links+'</td></tr>';
    }
  }
  document.getElementById('matrix').innerHTML = rowsHtml;

  const bad=[], warns=[];
  for(const [rid,r] of Object.entries(S.resources)){
    const s = st(rid);
    if(['down','missing','error'].includes(s.status)) bad.push(r.name+' — '+s.detail);
    else if(s.status==='warn') warns.push(r.name+' — '+s.detail);
  }
  const anyChecked = Object.keys(S.results).length>0;
  document.getElementById('warnings').innerHTML =
    bad.map(t=>'<div class="warnbar">⛔ '+t+'</div>').join('') +
    warns.map(t=>'<div class="warnbar amber">⚠ '+t+'</div>').join('') +
    (anyChecked && !bad.length && !warns.length ? '<div class="okbar">✅ All checked resources healthy.</div>' : '');

  if(anyChecked){
    const newest = Math.max(...Object.values(S.results).map(r=>r.ts||0));
    document.getElementById('stamp').textContent = 'last checked ' + new Date(newest*1000).toLocaleTimeString();
  }
  document.getElementById('jobs').innerHTML = (S.jobs||[]).map(j=>{
    const dur = j.ended? (j.ended-j.started).toFixed(0)+'s' : '<span class="spin">◐</span> running';
    return '<div class="jobhead">'+j.name+' — '+j.status+' ('+dur+')</div><pre>'+(j.output||'')+'</pre>';
  }).join('');
}

state().then(()=>checkAll());
setInterval(()=>{ if(!poller) state(); }, 20000);
</script>
</body></html>"""


@app.route("/")
def home():
    return Response(PAGE, mimetype="text/html")


if __name__ == "__main__":
    print(f"AI Hub Demo Control Panel on http://127.0.0.1:{PORT}")
    app.run(host="127.0.0.1", port=PORT, debug=False)
