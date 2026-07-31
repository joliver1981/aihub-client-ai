"""
Platform Regression Matrix — the ALL-AREAS pre-build gate.

Pack 14 answers "does every workflow node still execute correctly?".
This pack answers the same question for EVERY OTHER subsystem: auth, pages,
agents (CRUD + live chat), knowledge ingest, connections (CRUD + query),
NL->SQL data chat, documents API, automations (lifecycle + output-verification
honesty), code flows, portal workflows, approvals, scheduler, secrets,
users/groups, MCP, Command Center + Builder services — each exercised through
the SAME HTTP surface the UI calls, with deterministic oracles and cleanup.
The workflow-engine leg is pack 14 itself: this runner executes it and merges
its rows, so ONE command produces ONE report for the whole platform.

Run (aihub2.1 env):
  C:\\Users\\james\\miniconda3\\envs\\aihub2.1\\python.exe runner.py
      [--only substr] [--skip-wf14] [--skip-llm] [--timeout 120]

Statuses: PASS / FAIL / XFAIL / XPASS / SKIP (reason recorded) / ERROR.
Baseline: every run diffs against the previous results JSON — PASS->FAIL is a
REGRESSION (exit 2). Reports: REPORT_LATEST.md + results_history/.
Namespace: everything this runner creates is prefixed REGP- and deleted.
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

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
PACK14 = os.path.join(REPO, "test_human", "14_Workflow_Node_Matrix")
HISTORY_DIR = os.path.join(HERE, "results_history")
PYTHON = sys.executable


def now_stamp():
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def log(msg):
    print(f"[platreg] {msg}", flush=True)


def port_open(port, host="127.0.0.1", timeout=2):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def hidden_fields(html):
    """Scrape hidden form inputs (csrf_token etc.). BOTH attribute orderings —
    the login page renders name-first, and missing the token makes Flask-WTF's
    validate_on_submit() fail SILENTLY (re-renders /login with no flash), which
    is indistinguishable from a bad password unless you check for the flash."""
    out = dict(re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', html))
    out.update(dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"', html)))
    return out


def login_as(base, username, password):
    """Log a fresh session in the way the browser does. Returns (session, ok)."""
    s = requests.Session()
    r = s.get(f"{base}/login", timeout=15)
    data = {"username": username, "password": password, "submit": "Login"}
    data.update(hidden_fields(r.text))
    r = s.post(f"{base}/login", data=data, allow_redirects=True, timeout=20)
    return s, ("/login" not in r.url)


class Api:
    def __init__(self, base_url, username, password):
        self.base = base_url.rstrip("/")
        self.s = requests.Session()
        r = self.s.get(f"{self.base}/login", timeout=20)
        data = {"username": username, "password": password, "submit": "Login"}
        data.update(hidden_fields(r.text))
        r = self.s.post(f"{self.base}/login", data=data, allow_redirects=True, timeout=30)
        if "/login" in r.url:
            raise RuntimeError(f"admin login failed (landed on {r.url})")

    def get(self, path, **kw):
        return self.s.get(f"{self.base}{path}", timeout=kw.pop("timeout", 30), **kw)

    def post(self, path, payload=None, **kw):
        return self.s.post(f"{self.base}{path}", json=payload,
                           timeout=kw.pop("timeout", 60), **kw)

    def put(self, path, payload=None, **kw):
        return self.s.put(f"{self.base}{path}", json=payload,
                          timeout=kw.pop("timeout", 60), **kw)

    def delete(self, path, **kw):
        return self.s.delete(f"{self.base}{path}", timeout=kw.pop("timeout", 30), **kw)

    @staticmethod
    def jbody(r):
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


# ---------------------------------------------------------------- registry

CHECKS = []


def check(id, area, title, needs=(), llm=False, xfail=None):
    def deco(fn):
        CHECKS.append({"id": id, "area": area, "title": title,
                       "needs": list(needs), "llm": llm, "xfail": xfail, "fn": fn})
        return fn
    return deco


# Registered-but-not-automated rows (kept visible so coverage gaps are explicit)
NOT_AUTOMATED = [
    ("email_inbound", "Email", "inbound email pipeline needs a mail fixture/sink (owner decision: no email automation)"),
    ("integrations_api", "Integrations", "API is internal-token only; the page render is covered under Pages"),
    ("compliance_pipeline", "Compliance", "needs a retailer document set; page render covered under Pages"),
    ("solutions_install", "Solutions", "installing a bundle mutates shared tenant assets; gallery page covered under Pages"),
    ("environments_provision", "Environments", "conda env provisioning is minutes-slow; page render covered under Pages"),
    ("data_explorer_battery", "Data/NLQ", "deep NLQ competency lives in pack 12 (battery.py); one live probe runs here"),
    ("document_qa_battery", "Documents", "deep doc-QA competency lives in pack 13; ingest+API-list run here"),
]

PAGES = [
    ("/dashboard", "Welcome"), ("/chat", "agent-dropdown"),
    ("/data_assistants", "Data Agent Chat"), ("/data_explorer", "Data Explorer"),
    ("/jobs", "Intelligent Jobs"), ("/approvals", "My Approvals"),
    ("/portal-workflows", "Portal Workflows"), ("/solutions", "Solutions Gallery"),
    ("/solutions/author", "Solutions Author"), ("/my-connections", "My Connections"),
    ("/custom_agent_enhanced", "Agent Builder"), ("/custom_data_agent", "Data Agent Builder"),
    ("/connections", "Database Connections"), ("/data_dictionary", "Data Dictionary"),
    ("/local-secrets", "Local Secrets"), ("/custom", "Tool Builder"),
    ("/document_processor", "Document Processor"), ("/document-search", "Document Search"),
    ("/document-manager", "Document Manager"), ("/workflow_tool", "Workflow Designer"),
    ("/monitoring", "Workflow Monitor"), ("/mcp_servers", "MCP Server"),
    ("/integrations", "Integrations"), ("/compliance", "Retailer Compliance"),
    ("/environments/", "Agent Environments"), ("/users", "User Management"),
    ("/groups", "Group"), ("/system_logs", "Log Viewer"), ("/admin/tier", "Tier"),
    ("/admin/api-keys", "API Keys"), ("/admin/identity/settings", "Identity"),
    ("/email-processing/history", "Email Processing"),
    ("/admin/feedback-analysis", "Feedback"),
]


# ---------------------------------------------------------------- checks

@check("svc_ports", "Services", "all service ports listening")
def c_svc_ports(ctx):
    ports = {"main:5001": 5001, "cc:5091": 5091, "browser-use:5101": 5101,
             "executor:5061": 5061, "mcp-gw:5071": 5071, "builder:8100": 8100,
             "data-api:8200": 8200}
    down = [name for name, p in ports.items() if not port_open(p, host=ctx["host"])]
    doc_ports = {"doc-api:5011": 5011, "doc-q:5031": 5031, "vector:5041": 5041,
                 "knowledge:5051": 5051}
    doc_down = [n for n, p in doc_ports.items() if not port_open(p, host=ctx["host"])]
    return (not down), (f"host={ctx['host']}; down={down or 'none'} of {len(ports)}; "
                        f"doc-stack down={doc_down or 'none'} (informational)")


@check("auth_bad_password", "Auth", "wrong password is rejected")
def c_auth_bad(ctx):
    s = requests.Session()
    r0 = s.get(f"{ctx['base']}/login", timeout=15)
    data = {"username": "admin", "password": "definitely-wrong-password-xyz",
            "submit": "Login"}
    data.update(hidden_fields(r0.text))
    r = s.post(f"{ctx['base']}/login", data=data, allow_redirects=True, timeout=20)
    # a VALID form with a bad password must produce the explicit rejection flash
    # (a silent re-render would mean the form never validated — a false pass)
    ok = "/dashboard" not in r.url and "Login Unsuccessful" in r.text
    return ok, f"landed={r.url}, rejection-flash={'Login Unsuccessful' in r.text}"


@check("auth_anonymous_gate", "Auth", "admin page redirects anonymous users to login")
def c_auth_anon(ctx):
    r = requests.get(f"{ctx['base']}/users", allow_redirects=True, timeout=15)
    ok = "/login" in r.url or 'name="password"' in r.text
    return ok, f"landed={r.url} http={r.status_code}"


@check("pages_render", "Pages", "every page renders its own content (33 pages)")
def c_pages(ctx):
    bad = []
    for url, marker in PAGES:
        try:
            r = ctx["api"].get(url)
            if r.status_code != 200 or marker not in r.text or "Internal Server Error" in r.text:
                bad.append(f"{url}(http={r.status_code},marker={marker in r.text})")
        except Exception as e:
            bad.append(f"{url}({e})")
    return (not bad), f"{len(PAGES) - len(bad)}/{len(PAGES)} ok" + (f"; failures: {bad[:4]}" if bad else "")


@check("agent_crud", "Agents", "create agent -> listed -> delete -> gone")
def c_agent_crud(ctx):
    api = ctx["api"]
    r = api.post("/add/agent", {"agent_id": 0, "agent_description": "REGP-agent-temp",
                                "agent_objective": "You are a temporary regression-test agent.",
                                "agent_enabled": True, "tool_names": [], "core_tool_names": []})
    body = api.jbody(r) or {}
    # /add/agent returns the new id in 'message' (str) on success
    aid = body.get("agent_id") or body.get("id")
    if not aid and str(body.get("message", "")).strip().isdigit():
        aid = int(body["message"])
    if not aid:
        return False, f"create failed: http={r.status_code} body={str(body)[:150]}"
    def agent_ids():
        body = api.jbody(api.get("/get/agents")) or {}
        rows = body.get("data") if isinstance(body, dict) else body
        if isinstance(rows, str):
            rows = json.loads(rows)
        return {str(a.get("id") or a.get("agent_id"))
                for a in (rows or []) if isinstance(a, dict)}
    listed = str(aid) in agent_ids()
    api.post("/delete/agent", {"agent_id": aid})
    gone = str(aid) not in agent_ids()
    ok = bool(aid) and listed and gone
    return ok, f"id={aid}, listed={listed}, deleted={gone}"


@check("agent_chat_math", "Agents", "live agent chat answers deterministic math", llm=True)
def c_agent_chat(ctx):
    r = ctx["api"].post("/api/agents/84/chat",
                        {"prompt": "What is 1875 divided by 25? Reply with just the number."},
                        timeout=150)
    body = ctx["api"].jbody(r) or {}
    text = json.dumps(body)
    ok = r.status_code == 200 and "75" in text
    return ok, f"http={r.status_code}, contains-75={'75' in text}, tail={text[-120:]}"


@check("agent_artifact_csv", "Agents", "agent creates a real CSV artifact server-side (UI chat path)",
       llm=True, needs=["local_disk"])
def c_agent_artifact(ctx):
    # Artifact tools bind to UI chat conversations — use the UI's own endpoint
    # (/chat/general creates the conversation and tees outputs to chat_files).
    marker = f"regp_{ctx['stamp']}"
    r = ctx["api"].post("/chat/general",
                        {"agent_id": "84", "hist": [], "conversation_id": None,
                         "prompt": f"Create a downloadable CSV file named {marker}.csv with "
                                   f"header (a,b) and one data row: 1,2."}, timeout=180)
    hits = glob.glob(os.path.join(REPO, "data", "chat_files", "*", "outputs", f"*{marker}.csv"))
    ok = r.status_code == 200 and bool(hits)
    content = open(hits[0], encoding="utf-8-sig").read().strip() if hits else ""
    return ok, f"http={r.status_code}, file={'yes' if hits else 'NO'}, content={content[:60]!r}"


@check("knowledge_ingest_delete", "Knowledge/Docs",
       "docx ingest pipeline (extract+classify+index) + delete", needs=["doc_stack"])
def c_knowledge(ctx):
    api = ctx["api"]
    path = os.path.join(REPO, "test_human", "11_Regression_Suite", "fixtures",
                        "vendor_payment_terms.docx")
    with open(path, "rb") as fh:
        r = api.s.post(f"{api.base}/add/agent_knowledge",
                       files={"file": ("vendor_payment_terms.docx", fh, "application/octet-stream")},
                       data={"agent_id": "36", "description": "REGP-probe", "batch_id": "regp"},
                       timeout=180)
    body = api.jbody(r) or {}
    kid = body.get("knowledge_id")
    ok_ingest = (body.get("status") == "success" and kid
                 and int(body.get("total_chars") or 0) > 1000)
    if not ok_ingest:
        return False, f"ingest failed: http={r.status_code} body={str(body)[:200]}"
    ok_delete = False
    if kid:
        ok_delete = api.post(f"/delete/agent_knowledge/{kid}").status_code == 200
    return (bool(ok_ingest) and ok_delete), (
        f"ingest={body.get('status')}, chars={body.get('total_chars')}, "
        f"type={body.get('document_type')}, deleted={ok_delete}")


@check("connection_crud_query", "Connections", "create connection -> execute SELECT -> delete",
       needs=["db"])
def c_connection(ctx):
    api = ctx["api"]
    r = api.post("/api/connections", {
        "connection_name": "REGP-ERPDB-temp", "database_type": "SQL Server",
        "server": "10.0.0.6", "database_name": "ERPDB", "user_name": "ai_user",
        "password": "Bradynov11", "port": 1433, "parameters": "Connect Timeout=15;"})
    body = api.jbody(r) or {}
    cid = (body.get("connection") or {}).get("id") or body.get("id") or body.get("connection_id")
    if not cid:
        rows = api.jbody(api.get("/get/connections")) or []
        cid = next((c.get("id") for c in rows
                    if c.get("connection_name") == "REGP-ERPDB-temp"), None)
    if not cid:
        return False, f"create failed http={r.status_code} body={str(body)[:150]}"
    r2 = api.post(f"/api/connections/{cid}/execute", {"query": "SELECT 42 AS answer"})
    b2 = api.jbody(r2) or {}
    got42 = "42" in json.dumps(b2)
    r3 = api.post(f"/delete/connection/{cid}")
    rows = api.jbody(api.get("/get/connections")) or []
    gone = not any(c.get("connection_name") == "REGP-ERPDB-temp" for c in rows)
    return (got42 and gone), f"id={cid}, query-42={got42}, deleted={gone} (del http={r3.status_code})"


@check("nlq_data_chat", "Data/NLQ", "NL->SQL data chat answers a deterministic question",
       needs=["db"], llm=True)
def c_nlq(ctx):
    body = ctx["api"].jbody(ctx["api"].get("/get/data_agents")) or []
    rows = body.get("data") if isinstance(body, dict) else body
    if isinstance(rows, str):
        rows = json.loads(rows)
    has_281 = any(str(a.get("id") or a.get("agent_id")) == "281"
                  for a in (rows or []) if isinstance(a, dict))
    if not has_281:
        return None, ("SKIP: known NLQ oracle agent (id 281, AIRDB2) not present "
                      "on this target — no deterministic oracle available")
    r = ctx["api"].post("/chat/data",
                        {"agent_id": "281", "question": "How many stores are there in total?",
                         "history": [], "format_table_as_json": False,
                         "caution_level": "medium"}, timeout=150)
    body = ctx["api"].jbody(r) or {}
    text = json.dumps(body)
    ok = r.status_code == 200 and re.search(r"\b15\b", text)
    return bool(ok), f"http={r.status_code}, contains-15={bool(re.search(r'15', text))}, tail={text[-120:]}"


@check("documents_api", "Knowledge/Docs", "document manager API lists the corpus")
def c_documents(ctx):
    r = ctx["api"].get("/api/documents")
    body = ctx["api"].jbody(r)
    docs = body if isinstance(body, list) else ((body or {}).get("documents")
                                               or (body or {}).get("data") or [])
    ok = r.status_code == 200 and isinstance(docs, list) and len(docs) >= 10
    return ok, f"http={r.status_code}, documents={len(docs) if isinstance(docs, list) else '?'}"


@check("automation_lifecycle", "Automations", "create -> save code -> dry-run verified -> promote -> delete")
def c_automation(ctx):
    api = ctx["api"]
    name = "REGP-auto-lifecycle"
    for a in (api.jbody(api.get("/automations/api/list")) or {}).get("automations", []):
        if a.get("name") == name:
            api.delete(f"/automations/api/{a['automation_id']}")
    r = api.post("/automations/api/create",
                 {"name": name, "description": "platform regression probe",
                  "provision_environment": False})
    auto_id = ((api.jbody(r) or {}).get("automation") or {}).get("automation_id")
    if not auto_id:
        return False, f"create http={r.status_code}"
    code_ok = ("import csv, os\n"
               "with open('report.csv', 'w', newline='') as f:\n"
               "    w = csv.writer(f); w.writerow(['id', 'total'])\n"
               "    w.writerow([1, 100]); w.writerow([2, 200])\n"
               "print('wrote report.csv')\n")
    manifest = {"name": name, "outputs": [{"kind": "file", "path": "report.csv", "min_rows": 2}]}
    rv = api.put(f"/automations/api/{auto_id}/code", {"code": code_ok, "manifest": manifest})
    version = (api.jbody(rv) or {}).get("version")
    rr = api.post(f"/automations/api/{auto_id}/run", {"dry_run": True, "wait": True}, timeout=180)
    run = api.jbody(rr) or {}
    dry_ok = run.get("status") == "success"
    rp = api.post(f"/automations/api/{auto_id}/promote", {})
    promoted = (api.jbody(rp) or {}).get("pinned_version")
    rd = api.delete(f"/automations/api/{auto_id}")
    return (bool(version) and dry_ok and bool(promoted) and rd.status_code in (200, 204)), (
        f"v={version}, dry-run={run.get('status')}, promoted={promoted}, "
        f"deleted-http={rd.status_code}")


@check("automation_verify_honesty", "Automations",
       "a script that LIES about its output is caught by verification (never 'success')")
def c_automation_liar(ctx):
    api = ctx["api"]
    name = "REGP-auto-liar"
    for a in (api.jbody(api.get("/automations/api/list")) or {}).get("automations", []):
        if a.get("name") == name:
            api.delete(f"/automations/api/{a['automation_id']}")
    r = api.post("/automations/api/create",
                 {"name": name, "description": "honesty probe", "provision_environment": False})
    auto_id = ((api.jbody(r) or {}).get("automation") or {}).get("automation_id")
    if not auto_id:
        return False, f"create http={r.status_code}"
    liar = "print('I claim to write report.csv but I do not.')\n"
    manifest = {"name": name, "outputs": [{"kind": "file", "path": "report.csv", "min_rows": 2}]}
    api.put(f"/automations/api/{auto_id}/code", {"code": liar, "manifest": manifest})
    rr = api.post(f"/automations/api/{auto_id}/run", {"dry_run": True, "wait": True}, timeout=180)
    run = api.jbody(rr) or {}
    caught = run.get("status") != "success"
    api.delete(f"/automations/api/{auto_id}")
    return caught, f"liar-run status={run.get('status')} (must NOT be success), exit0-but-caught={caught}"


@check("codeflows_registry", "Code Flows", "code-flow registry lists saved flows")
def c_codeflows(ctx):
    r = ctx["api"].get("/codeflows/api/list")
    body = ctx["api"].jbody(r)
    flows = body if isinstance(body, list) else ((body or {}).get("flows")
                                                 or (body or {}).get("code_flows")
                                                 or (body or {}).get("workflows") or [])
    ok = r.status_code == 200 and isinstance(flows, list)
    return ok, (f"http={r.status_code}, flows={len(flows) if isinstance(flows, list) else '?'} "
                f"(count informational — fresh installs legitimately have 0)")


@check("portal_wf_persist", "Portal WF", "save -> persisted -> duplicate-name 409 -> delete")
def c_portal(ctx):
    api = ctx["api"]
    payload = {"name": "REGP-portal-temp", "portal_slug": None,
               "start_url": f"{ctx['base']}/login", "goal": "persistence probe",
               "steps": [{"type": "goto", "url": f"{ctx['base']}/login"}]}
    r1 = api.post("/api/portal-workflows", payload)
    slug = ((api.jbody(r1) or {}).get("saved") or {}).get("slug")
    listed = any(w.get("slug") == slug for w in
                 ((api.jbody(api.get("/api/portal-workflows")) or {}).get("workflows") or []))
    r2 = api.post("/api/portal-workflows", payload)
    r3 = api.delete(f"/api/portal-workflows/{slug}") if slug else None
    ok = (r1.status_code == 200 and slug and listed and r2.status_code == 409
          and r3 is not None and r3.status_code == 200)
    return bool(ok), (f"save={r1.status_code}, slug={slug}, listed={listed}, "
                      f"dup={r2.status_code} (want 409), del={r3.status_code if r3 else '-'}")


@check("approvals_api", "Approvals", "pending approvals API parses")
def c_approvals(ctx):
    r = ctx["api"].get("/api/workflow/approvals?status=pending")
    body = ctx["api"].jbody(r) or {}
    rows = body.get("approvals") if isinstance(body, dict) else None
    ok = r.status_code == 200 and isinstance(rows, list)
    return ok, f"http={r.status_code}, pending={len(rows) if isinstance(rows, list) else '?'}"


@check("scheduler_jobs", "Scheduler", "scheduler backend + jobs list respond")
def c_scheduler(ctx):
    r1 = ctx["api"].get("/api/quickjob/scheduler/backend")
    r2 = ctx["api"].get("/get/jobs")
    jobs = ctx["api"].jbody(r2)
    n = len(jobs) if isinstance(jobs, list) else len((jobs or {}).get("jobs", []) if isinstance(jobs, dict) else [])
    ok = r1.status_code == 200 and r2.status_code == 200
    return ok, f"backend={r1.status_code} {str(ctx['api'].jbody(r1))[:60]}, jobs-http={r2.status_code}, jobs={n}"


@check("secrets_list", "Secrets", "local secrets store lists the expected test secret")
def c_secrets(ctx):
    body = ctx["api"].jbody(ctx["api"].get("/workflow/secrets/list")) or {}
    rows = body.get("secrets") if isinstance(body, dict) else body
    names = [(s.get("name") if isinstance(s, dict) else str(s)) for s in (rows or [])]
    ok = "SFTP_TEST_PASSWORD" in names or "AUTODEMO_SFTP" in names
    return ok, f"secrets={len(names)}, has-test-secret={ok}"


@check("users_groups", "Users/Groups", "users + groups APIs parse; admin present with role 3")
def c_users(ctx):
    users = ctx["api"].jbody(ctx["api"].get("/get/users")) or []
    admin = next((u for u in users if (u.get("user_name") or "").lower() == "admin"), None)
    r2 = ctx["api"].get("/groups")
    ok = bool(admin) and int(admin.get("role") or 0) == 3 and r2.status_code == 200
    return ok, f"users={len(users)}, admin-role={admin.get('role') if admin else '-'}"


@check("mcp_servers_api", "MCP", "MCP servers API lists (gateway reachable)")
def c_mcp(ctx):
    r = ctx["api"].get("/api/mcp/servers")
    body = ctx["api"].jbody(r)
    rows = body if isinstance(body, list) else ((body or {}).get("servers")
                                                or (body or {}).get("data") or [])
    ok = r.status_code == 200 and isinstance(rows, list)
    return ok, (f"http={r.status_code}, servers={len(rows) if isinstance(rows, list) else '?'}, "
                f"gw-port={port_open(5071, host=ctx['host'])}")


@check("cc_service", "Command Center", "CC service up + auto-token endpoint issues a token")
def c_cc(ctx):
    r1 = requests.get(f"http://{ctx['host']}:5091/", timeout=15)
    r2 = ctx["api"].get("/api/cc-auto-token")
    body = ctx["api"].jbody(r2) or {}
    has_token = bool(body.get("token") or body.get("cc_token") or body.get("access_token"))
    ok = r1.status_code == 200 and r2.status_code == 200 and has_token
    return ok, f"cc-http={r1.status_code}, token-http={r2.status_code}, token={has_token}"


@check("builder_service", "Builder", "builder service responds")
def c_builder(ctx):
    r = requests.get(f"http://{ctx['host']}:8100/", timeout=15)
    return r.status_code == 200, f"http={r.status_code}"



@check("users_role1_authz", "Users/Groups",
       "role-1 user is blocked from users page / save-workflow / automations-create")
def c_role1_authz(ctx):
    api = ctx["api"]
    uname, pw = "regp-userb", "RegpTemp!2026"
    _r_add = api.post("/add/user", {"user_id": 0, "user_name": uname,
                                    "name": "REGP User B",
                                    "email": "regp-userb@example.com", "password": pw,
                                    "role": 1, "phone": ""})
    users = api.jbody(api.get("/get/users")) or []
    uid = next((u.get("id") for u in users
                if (u.get("user_name") or "") == uname), None)
    if not uid:
        return False, f"could not create role-1 probe user (add-user http={_r_add.status_code} body={str(ctx['api'].jbody(_r_add))[:160]})"
    b, logged_in = login_as(ctx["base"], uname, pw)
    blocked = {}
    if logged_in:
        pr = b.get(f"{ctx['base']}/users", allow_redirects=True, timeout=15)
        blocked["users_page"] = ("/login" in pr.url or pr.status_code in (302, 401, 403)
                                 or "User Management" not in pr.text)
        wr = b.post(f"{ctx['base']}/save/workflow",
                    json={"filename": "regp-b-probe.json",
                          "workflow": {"nodes": [], "connections": []}}, timeout=20)
        wbody = wr.text[:80]
        blocked["save_workflow"] = wr.status_code in (302, 401, 403) or "login" in wbody.lower()
        ar = b.post(f"{ctx['base']}/automations/api/create",
                    json={"name": "regp-b-probe", "provision_environment": False}, timeout=20)
        blocked["automations_create"] = ar.status_code in (302, 401, 403)

    api.post("/delete/user", {"user_id": uid})
    gone = not any((u.get("user_name") or "") == uname
                   for u in (api.jbody(api.get("/get/users")) or []))
    all_blocked = logged_in and all(blocked.values()) and len(blocked) == 3
    return (all_blocked and gone), (
        f"login={logged_in}, blocked={blocked}, user-deleted={gone}")


@check("user_file_isolation", "Users/Groups",
       "one user cannot download another user's agent files", needs=["doc_stack"])
def c_file_isolation(ctx):
    api = ctx["api"]
    path = os.path.join(REPO, "test_human", "11_Regression_Suite", "fixtures",
                        "vendor_payment_terms.docx")
    with open(path, "rb") as fh:
        r = api.s.post(f"{api.base}/add/agent_knowledge",
                       files={"file": ("regp_iso_probe.docx", fh, "application/octet-stream")},
                       data={"agent_id": "36", "description": "REGP-iso", "batch_id": "regp-iso"},
                       timeout=180)
    body = api.jbody(r) or {}
    kid, doc_id = body.get("knowledge_id"), body.get("document_id")
    if not doc_id:
        return False, f"fixture upload failed: {str(body)[:120]}"
    try:
        admin_users = api.jbody(api.get("/get/users")) or []
        admin_id = next((u.get("id") for u in admin_users
                         if (u.get("user_name") or "").lower() == "admin"), 13)
        dl = f"/api/chat/agent_files/36/{admin_id}/{doc_id}/download"
        ra = api.get(dl)
        if ra.status_code != 200:
            return None, (f"SKIP: could not establish an owned file to probe "
                          f"(admin download http={ra.status_code})")
        uname, pw = "regp-userc", "RegpTemp!2026"
        api.post("/add/user", {"user_id": 0, "user_name": uname,
                               "name": "REGP User C",
                               "email": "regp-userc@example.com", "password": pw,
                               "role": 1, "phone": ""})
        uid = next((u.get("id") for u in (api.jbody(api.get("/get/users")) or [])
                    if (u.get("user_name") or "") == uname), None)
        b, b_logged_in = login_as(ctx["base"], uname, pw)
        if not b_logged_in:
            if uid:
                api.post("/delete/user", {"user_id": uid})
            return None, "SKIP: probe user could not log in — cannot test isolation"
        rb = b.get(f"{ctx['base']}{dl}", allow_redirects=False, timeout=20)
        denied = rb.status_code in (302, 401, 403, 404)
        if uid:
            api.post("/delete/user", {"user_id": uid})
        return denied, (f"admin-download=200, other-user-download={rb.status_code} "
                        f"(must be denied)")
    finally:
        if kid:
            api.post(f"/delete/agent_knowledge/{kid}")


@check("sec_approvals_get_unauth", "Security",
       "approvals list API must require authentication",
       xfail="FOUND 2026-07-31: GET /api/workflow/approvals has no auth decorator — "
             "anonymous users can read approval titles/descriptions (business data). "
             "Fix pending; flips XPASS when auth is added.")
def c_sec_approvals_get(ctx):
    r = requests.get(f"{ctx['base']}/api/workflow/approvals?status=pending",
                     allow_redirects=False, timeout=15)
    ok = r.status_code in (302, 401, 403)
    return ok, f"anonymous GET -> http={r.status_code} (must be 302/401/403)"


@check("sec_approvals_decide_unauth", "Security",
       "approvals decide API must require authentication",
       xfail="FOUND 2026-07-31: POST /api/workflow/approvals/<id> has no auth decorator — "
             "anonymous users can approve/reject workflow approvals (and bridged "
             "automation checkpoints). Fix pending; flips XPASS when auth is added.")
def c_sec_approvals_post(ctx):
    r = requests.post(f"{ctx['base']}/api/workflow/approvals/regp-bogus-id",
                      json={"status": "approved", "comments": "regp-probe"},
                      allow_redirects=False, timeout=15)
    ok = r.status_code in (302, 401, 403)
    return ok, (f"anonymous POST -> http={r.status_code} (must be 302/401/403; "
                f"404 means the request REACHED business logic unauthenticated)")



@check("sec_role1_can_create_agents", "Security",
       "role-1 user must NOT be able to create agents",
       xfail="FOUND 2026-07-31 on the shipped 1.8.1 build: POST /add/agent carries "
             "@api_key_or_session_required() with NO min_role (compare /save/workflow "
             "min_role=2), so a basic role-1 User can create agents — VERIFIED live "
             "(agent id 6011 persisted). Flips XPASS when a min_role gate is added.")
def c_sec_role1_agents(ctx):
    api = ctx["api"]
    uname, pw = "regp-escprobe", "RegpTemp!2026"
    api.post("/add/user", {"user_id": 0, "user_name": uname, "name": "REGP Esc",
                           "email": "regp-esc@example.com", "password": pw,
                           "role": 1, "phone": ""})
    uid = next((u.get("id") for u in (api.jbody(api.get("/get/users")) or [])
                if (u.get("user_name") or "") == uname), None)
    try:
        b, ok = login_as(ctx["base"], uname, pw)
        if not ok:
            return None, "SKIP: probe user could not log in"
        r = b.post(f"{ctx['base']}/add/agent",
                   json={"agent_id": 0, "agent_description": "REGP-ESC-PROBE",
                         "agent_objective": "probe", "agent_enabled": False}, timeout=30)
        blocked = r.status_code in (302, 401, 403)
        created_id = None
        body = api.jbody(api.get("/get/agents")) or {}
        rows = body.get("data") if isinstance(body, dict) else body
        if isinstance(rows, str):
            rows = json.loads(rows)
        for a in (rows or []):
            if isinstance(a, dict) and "REGP-ESC-PROBE" in json.dumps(a):
                created_id = a.get("id")
                api.post("/delete/agent", {"agent_id": created_id})
        return (blocked and created_id is None), (
            f"role-1 POST /add/agent -> http={r.status_code}; agent actually created="
            f"{created_id is not None} (must be blocked, nothing created)")
    finally:
        if uid:
            api.post("/delete/user", {"user_id": uid})


# ---------------------------------------------------------------- pack-14 leg

def run_pack14(args, remote=False, host="localhost"):
    """Execute the workflow-engine matrix and fold its rows in."""
    if args.skip_wf14:
        return [{"id": "wf14", "area": "Workflow engine", "status": "SKIP",
                 "evidence": "--skip-wf14"}]
    log("running pack-14 workflow node matrix ...")
    cmd = [PYTHON, "runner.py", "--tier", "2", "--base-url", args.base_url]
    if remote:
        # SFTP checks: the engine box must dial BACK to this dev machine's
        # server — learn our outbound IP toward the target
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect((host, 1))
            my_ip = probe.getsockname()[0]
            probe.close()
        except OSError:
            my_ip = "127.0.0.1"
        cmd += ["--remote", "--sftp-host", my_ip]
    try:
        proc = subprocess.run(cmd, cwd=PACK14,
                              capture_output=True, text=True, timeout=900)
        files = sorted(glob.glob(os.path.join(PACK14, "results_history", "results_*.json")))
        run = json.load(open(files[-1], encoding="utf-8"))
        rows = []
        for r in run.get("results", []):
            rows.append({"id": f"wf14:{r['id']}", "area": "Workflow engine",
                         "status": r["status"], "evidence": (r.get("evidence") or "")[:200]})
        rows.append({"id": "wf14:baseline", "area": "Workflow engine",
                     "status": "PASS" if proc.returncode in (0,) else
                               ("FAIL" if proc.returncode else "PASS"),
                     "evidence": f"pack-14 exit={proc.returncode} "
                                 f"(0=clean, 1=failures, 2=regressions)"})
        return rows
    except Exception as e:
        return [{"id": "wf14", "area": "Workflow engine", "status": "ERROR",
                 "evidence": f"pack-14 run failed: {e}"}]


# ---------------------------------------------------------------- engine

def probe_env(base, host="localhost"):
    env = {"db": port_open("10.0.0.6", timeout=3) if False else None}
    # db probe: TCP 1433 on the test SQL host
    try:
        with socket.create_connection(("10.0.0.6", 1433), timeout=3):
            env["db"] = True
    except OSError:
        env["db"] = None
    # document pipeline: ingest requires the doc/vector/knowledge services
    env["doc_stack"] = all(port_open(pt, host=host) for pt in (5011, 5031, 5041, 5051))
    return env


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=None,
                    help="explicit base URL; usually derived from --host")
    ap.add_argument("--host", default=None,
                    help="target machine for a POST-INSTALL gate run, e.g. 10.0.0.6 "
                         "(derives all service URLs; omit for the local dev app)")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="admin")
    ap.add_argument("--only")
    ap.add_argument("--skip-wf14", action="store_true")
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args()

    host = args.host or "localhost"
    if not args.base_url:
        args.base_url = os.environ.get("REGP_BASE") or f"http://{host}:5001"
    remote = args.host is not None and args.host not in ("localhost", "127.0.0.1")

    stamp = now_stamp()
    global HISTORY_DIR
    if remote:
        # an installed box is a DIFFERENT environment — never diff it against
        # the dev-tree baseline (that produced bogus "REGRESSIONS")
        HISTORY_DIR = os.path.join(HERE, "results_history", f"host_{host}")
    api = Api(args.base_url, args.user, args.password)
    env = probe_env(args.base_url, host=host)
    env["local_disk"] = not remote
    log(f"env: target={host} remote={remote} db-reachable={bool(env['db'])}")
    ctx = {"api": api, "base": args.base_url, "stamp": stamp, "env": env, "host": host}

    results = []
    for spec in CHECKS:
        cid = spec["id"]
        if args.only and args.only not in cid:
            continue
        if spec["llm"] and args.skip_llm:
            results.append({"id": cid, "area": spec["area"], "status": "SKIP",
                            "evidence": "--skip-llm"})
            continue
        missing = [k for k in spec["needs"] if not env.get(k)]
        if missing:
            results.append({"id": cid, "area": spec["area"], "status": "SKIP",
                            "evidence": f"env missing: {missing}"})
            continue
        t0 = time.time()
        try:
            ok, evidence = spec["fn"](ctx)
            if ok is None:
                results.append({"id": cid, "area": spec["area"], "status": "SKIP",
                                "evidence": evidence})
                log(f"SKIP   {cid} — {evidence[:120]}")
                continue
            if spec["xfail"]:
                st = "XPASS" if ok else "XFAIL"
            else:
                st = "PASS" if ok else "FAIL"
            results.append({"id": cid, "area": spec["area"], "status": st,
                            "evidence": evidence, "duration_s": round(time.time() - t0, 1)})
            log(f"{st:6} {cid} ({round(time.time()-t0,1)}s) — {evidence[:130]}")
        except Exception as e:
            results.append({"id": cid, "area": spec["area"], "status": "ERROR",
                            "evidence": f"runner error: {e}"})
            log(f"ERROR  {cid} — {e}")

    if not args.only:
        results.extend(run_pack14(args, remote=remote, host=host))
        for cid, area, reason in NOT_AUTOMATED:
            results.append({"id": cid, "area": area, "status": "SKIP",
                            "evidence": f"not automated: {reason}"})

    # ---------------- baseline diff + report
    os.makedirs(HISTORY_DIR, exist_ok=True)
    baseline, baseline_name = None, None
    files = sorted(glob.glob(os.path.join(HISTORY_DIR, "results_*.json")))
    if files:
        baseline = json.load(open(files[-1], encoding="utf-8"))
        baseline_name = os.path.basename(files[-1])
    prev = {r["id"]: r["status"] for r in (baseline or {}).get("results", [])}
    regressions = [(r["id"], prev.get(r["id"]), r["status"]) for r in results
                   if prev.get(r["id"]) == "PASS" and r["status"] in ("FAIL", "ERROR")]
    fixed = [(r["id"], prev.get(r["id"]), r["status"]) for r in results
             if prev.get(r["id"]) in ("FAIL", "ERROR") and r["status"] == "PASS"]

    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    verdict = ("REGRESSIONS DETECTED" if regressions else
               ("FAILURES (no baseline regression)" if any(
                   r["status"] in ("FAIL", "ERROR") for r in results) else "CLEAN"))

    try:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                         cwd=REPO, text=True).strip()
    except Exception:
        commit = "?"

    target_label = f"INSTALLED {host}" if remote else "local dev"
    lines = [f"# Platform Regression Report — {stamp} ({target_label})", "",
             f"- Build: `{commit}` | Base: `{args.base_url}` | Baseline: "
             f"`{baseline_name or 'none (first run)'}`", "",
             f"## Verdict: **{verdict}** — "
             + " / ".join(f"{v} {k}" for k, v in sorted(counts.items())), ""]
    if regressions:
        lines += ["## 🔴 REGRESSIONS vs baseline", "",
                  "| check | was | now |", "|---|---|---|"]
        lines += [f"| {c} | {w} | **{n}** |" for c, w, n in regressions]
        lines.append("")
    if fixed:
        lines += ["## 🟢 Fixed vs baseline", ""]
        lines += [f"- {c}: {w} → {n}" for c, w, n in fixed]
        lines.append("")
    lines += ["## Full matrix (by area)", "",
              "| area | check | status | evidence |", "|---|---|---|---|"]
    for r in results:
        badge = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "XFAIL": "⚠️ XFAIL",
                 "XPASS": "🟡 XPASS", "SKIP": "⏭ SKIP", "ERROR": "💥 ERROR"}[r["status"]]
        ev = (r.get("evidence") or "").replace("|", "\\|")
        lines.append(f"| {r['area']} | {r['id']} | {badge} | {ev[:230]} |")
    lines.append("")
    report = "\n".join(lines)

    run_doc = {"stamp": stamp, "commit": commit,
               "target": (host if remote else "local"), "results": results}
    with open(os.path.join(HISTORY_DIR, f"results_{stamp}.json"), "w", encoding="utf-8") as fh:
        json.dump(run_doc, fh, indent=1, default=str)
    with open(os.path.join(HISTORY_DIR, f"REPORT_{stamp}.md"), "w", encoding="utf-8") as fh:
        fh.write(report)
    with open(os.path.join(HERE, "REPORT_LATEST.md"), "w", encoding="utf-8") as fh:
        fh.write(report)

    print("\n" + "=" * 72)
    print(report.split("## Full matrix")[0])
    print(f"Report: {os.path.join(HERE, 'REPORT_LATEST.md')}")
    if regressions:
        return 2
    if any(r["status"] in ("FAIL", "ERROR") for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
