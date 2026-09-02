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
import contextlib
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


def agent_rows(api):
    body = api.jbody(api.get("/get/agents")) or {}
    rows = body.get("data") if isinstance(body, dict) else body
    if isinstance(rows, str):
        rows = json.loads(rows)
    return [a for a in (rows or []) if isinstance(a, dict)]


def agent_id_of(row):
    """/get/agents rows key the id as 'agent_id' — 'id' is always absent, which
    silently broke cleanup and detection until 2026-07-31."""
    return row.get("agent_id") or row.get("id")


def agent_reply_text(api, body):
    """The answer only — not the whole JSON envelope, which echoes the prompt."""
    if not isinstance(body, dict):
        return str(body or "")
    for k in ("response", "answer", "message", "text", "reply"):
        v = body.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return json.dumps(body)


def llm_judge(api, text, question, agent_id="84"):
    """Mini-LLM classifier for natural-language judgements.

    STANDING DIRECTIVE (james): never use regex/keyword lists to INTERPRET
    natural language - use a mini-LLM. Regex is fine for format validation, so
    the only pattern-matching here is on the returned YES/NO token.

    Keyword scoring is what made comp_nlq_admits_unanswerable flip PASS->FAIL
    between two runs of the same build on 2026-08-02: an honest refusal that
    happened to restate a number scored as a fabrication.

    Returns True / False / None (None = judge unavailable or ambiguous -> SKIP).
    """
    prompt = (f"{question}\n\n---BEGIN TEXT---\n{str(text)[:4000]}\n---END TEXT---\n\n"
              f"Reply with exactly one word and nothing else.")
    try:
        r = api.post(f"/api/agents/{agent_id}/chat", {"prompt": prompt}, timeout=150)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    verdict = agent_reply_text(api, api.jbody(r) or {}).strip().upper()
    yes, no = bool(re.search(r"\bYES\b", verdict)), bool(re.search(r"\bNO\b", verdict))
    if yes == no:
        return None
    return yes


def make_probe_agent(api, label):
    """Create a throwaway agent ON THE TARGET. Never hardcode a dev-box agent id:
    ids differ per install (agent 36 exists only on the dev tree, which made the
    knowledge check report a false product failure on the install box)."""
    r = api.post("/add/agent", {"agent_id": 0, "agent_description": label,
                                "agent_objective": "regression probe agent",
                                "agent_enabled": True, "tool_names": [],
                                "core_tool_names": []})
    body = api.jbody(r) or {}
    aid = body.get("agent_id") or body.get("id")
    if not aid and str(body.get("message", "")).strip().isdigit():
        aid = int(body["message"])
    if not aid:
        aid = next((agent_id_of(a) for a in agent_rows(api)
                    if (a.get("agent_description") or "") == label), None)
    return aid


def delete_probe_agent(api, aid):
    if aid:
        api.post("/delete/agent", {"agent_id": aid})


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
        return self.s.get(f"{self.base}{path}", timeout=kw.pop("timeout", 90), **kw)

    def post(self, path, payload=None, **kw):
        return self.s.post(f"{self.base}{path}", json=payload,
                           timeout=kw.pop("timeout", 120), **kw)

    def put(self, path, payload=None, **kw):
        return self.s.put(f"{self.base}{path}", json=payload,
                          timeout=kw.pop("timeout", 120), **kw)

    def delete(self, path, **kw):
        return self.s.delete(f"{self.base}{path}", timeout=kw.pop("timeout", 90), **kw)

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


def check(id, area, title, needs=(), llm=False, xfail=None, competency=False):
    """competency=True -> deeper edge-case probe. These do NOT run by default
    (the daily gate stays fast); pass --competency to include them. They are
    still baseline-diffed, so a competency check that starts failing is a
    regression signal too."""
    def deco(fn):
        CHECKS.append({"id": id, "area": area, "title": title,
                       "needs": list(needs), "llm": llm, "xfail": xfail,
                       "competency": competency, "fn": fn})
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

@check("svc_ports", "Services", "the externally-required service endpoints are listening")
def c_svc_ports(ctx):
    """Assert ONLY the endpoints something outside the main app must reach.

    Lesson (2026-07-31): an installed box legitimately runs several components
    IN-PROCESS rather than as separate listeners (the dev tree splits them out).
    Inferring breakage from a closed port produced a FALSE "document ingest is
    dead" claim while ingest actually worked. Ports are an implementation
    detail — CAPABILITY checks (knowledge_ingest_delete, automation_lifecycle,
    the pack-14 workflow runs) are the contract. Everything else is context.
    """
    required = {"main:5001": 5001, "cc:5091": 5091, "browser-use:5101": 5101,
                "builder:8100": 8100}
    optional = {"executor:5061": 5061, "mcp-gw:5071": 5071, "data-api:8200": 8200,
                "doc-api:5011": 5011, "doc-q:5031": 5031, "vector:5041": 5041,
                "knowledge:5051": 5051}
    down = [n for n, p in required.items() if not port_open(p, host=ctx["host"])]
    opt_down = [n for n, p in optional.items() if not port_open(p, host=ctx["host"])]
    return (not down), (f"host={ctx['host']}; required down={down or 'none'}; "
                        f"not-listening (may run in-process on an install): "
                        f"{opt_down or 'none'} — INFORMATIONAL, capability is asserted "
                        f"by the functional checks")


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
        return {str(agent_id_of(a)) for a in agent_rows(api)}
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


@check("code_interpreter_pack22", "Agents",
       "GA code-interpreter competency gate (pack 22) is fully green with lane attribution",
       llm=True)
def c_code_interpreter(ctx):
    """Pre-build gate for the code-interpreter lane
    (docs/code-interpreter-unification-plan.md): runs pack 22's live-oracle
    runner as a subprocess — 9 scenarios over real uploads, each required to
    BOTH answer exactly AND be ledger-attributed to run_python_code (the
    legacy tabular tools produce identical numbers, so attribution is the only
    honest signal). ~90 s of live model turns. The runner degrades to 8/8 when
    the AIRDB2 oracle is unreachable, so a full 'N/N PASS' line is the
    contract either way."""
    if ctx["host"] not in ("127.0.0.1", "localhost"):
        return None, "pack 22 drives the local stack only (remote target)"
    runner = os.path.join(REPO, "test_human", "22_GA_Code_Interpreter", "runner.py")
    p = subprocess.run(
        [sys.executable, runner], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    lines = [ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()]
    verdict = next((ln for ln in reversed(lines) if "PASS" in ln and "/" in ln), "")
    full = bool(re.search(r"\b(\d+)/\1 PASS\b", verdict))
    fails = [ln for ln in lines if ": FAIL" in ln]
    ok = p.returncode == 0 and full
    return ok, (f"rc={p.returncode}, {verdict or 'no verdict line'}"
                + (f"; failing: {'; '.join(fails)[:150]}" if fails else ""))


@check("knowledge_ingest_delete", "Knowledge/Docs",
       "docx ingest pipeline (extract+classify+index) + delete")
def c_knowledge(ctx):
    api = ctx["api"]
    path = os.path.join(REPO, "test_human", "11_Regression_Suite", "fixtures",
                        "vendor_payment_terms.docx")
    aid = make_probe_agent(api, "REGP-knowledge-probe")
    if not aid:
        return None, "SKIP: could not create a probe agent on this target"
    try:
        with open(path, "rb") as fh:
            r = api.s.post(f"{api.base}/add/agent_knowledge",
                           files={"file": ("regp_probe.docx", fh, "application/octet-stream")},
                           data={"agent_id": str(aid), "description": "REGP-probe",
                                 "batch_id": "regp"}, timeout=180)
        body = api.jbody(r) or {}
        kid = body.get("knowledge_id")
        ok_ingest = (body.get("status") == "success" and kid
                     and int(body.get("total_chars") or 0) > 1000)
        if not ok_ingest:
            return False, f"ingest failed: http={r.status_code} body={str(body)[:200]}"
        ok_delete = api.post(f"/delete/agent_knowledge/{kid}").status_code == 200
        return ok_delete, (f"agent={aid}, ingest={body.get('status')}, "
                           f"chars={body.get('total_chars')}, type={body.get('document_type')}, "
                           f"deleted={ok_delete}")
    finally:
        delete_probe_agent(api, aid)


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
    ctx["api"].get("/data_assistants", timeout=45)      # seed the chat session:
    # without it /chat/data replies "Your session may have expired" and this check
    # silently depended on pages_render having run first.
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
    # min_rows must sit under "verify" - a flat key is silently IGNORED
    # (automations/runner.py:217 reads out.get("verify")). It was flat here, so
    # this check only ever asserted the file EXISTED.
    manifest = {"name": name, "outputs": [{"kind": "file", "path": "report.csv",
                                           "verify": {"min_rows": 2}}]}
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
       "one user cannot download another user's agent files")
def c_file_isolation(ctx):
    api = ctx["api"]
    path = os.path.join(REPO, "test_human", "11_Regression_Suite", "fixtures",
                        "vendor_payment_terms.docx")
    aid = make_probe_agent(api, "REGP-isolation-probe")
    if not aid:
        return None, "SKIP: could not create a probe agent on this target"
    kid = uid = None
    try:
        with open(path, "rb") as fh:
            r = api.s.post(f"{api.base}/add/agent_knowledge",
                           files={"file": ("regp_iso.docx", fh, "application/octet-stream")},
                           data={"agent_id": str(aid), "description": "REGP-iso",
                                 "batch_id": "regp-iso"}, timeout=180)
        body = api.jbody(r) or {}
        kid, doc_id = body.get("knowledge_id"), body.get("document_id")
        if not doc_id:
            return None, f"SKIP: probe upload did not return a document id ({str(body)[:120]})"
        admin_id = next((u.get("id") for u in (api.jbody(api.get("/get/users")) or [])
                         if (u.get("user_name") or "").lower() == "admin"), 13)
        dl = f"/api/chat/agent_files/{aid}/{admin_id}/{doc_id}/download"
        ra = api.get(dl)
        if ra.status_code != 200:
            return None, (f"SKIP: owner download not available to probe against "
                          f"(admin http={ra.status_code})")
        uname, pw = "regp-userc", "RegpTemp!2026"
        api.post("/add/user", {"user_id": 0, "user_name": uname, "name": "REGP User C",
                               "email": "regp-userc@example.com", "password": pw,
                               "role": 1, "phone": ""})
        uid = next((u.get("id") for u in (api.jbody(api.get("/get/users")) or [])
                    if (u.get("user_name") or "") == uname), None)
        b, ok = login_as(ctx["base"], uname, pw)
        if not ok:
            return None, "SKIP: probe user could not log in"
        rb = b.get(f"{ctx['base']}{dl}", allow_redirects=False, timeout=20)
        denied = rb.status_code in (302, 401, 403, 404)
        return denied, (f"owner-download=200, other-user-download={rb.status_code} "
                        f"(must be denied)")
    finally:
        if kid:
            api.post(f"/delete/agent_knowledge/{kid}")
        if uid:
            api.post("/delete/user", {"user_id": uid})
        delete_probe_agent(api, aid)


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
        for a in agent_rows(api):
            if "REGP-ESC-PROBE" in json.dumps(a):
                created_id = agent_id_of(a)
                delete_probe_agent(api, created_id)
        return (blocked and created_id is None), (
            f"role-1 POST /add/agent -> http={r.status_code}; agent actually created="
            f"{created_id is not None} (must be blocked, nothing created)")
    finally:
        if uid:
            api.post("/delete/user", {"user_id": uid})


# ================================================================= CONNECTIONS
# Deepened 2026-07-31. WHY THIS AREA: every data capability sits on it (data
# agents, Data Explorer, NLQ, the workflow Database node), and it has the worst
# proven regression history in the repo — the masked-password save bug was fixed
# in June, LOST to source drift, and re-fixed 2026-07-30. Coverage before today
# was ONE check (create -> SELECT 42 -> delete): no edit path, no masked-password
# round-trip, no egress masking, no honest-failure assertions.
#
# ERPDB oracles (verified live 2026-07-31): Invoices=17 rows, LFA1(vendors)=5.

ERP = {"connection_name": None, "database_type": "SQL Server", "server": "10.0.0.6",
       "database_name": "ERPDB", "user_name": "ai_user", "password": "Bradynov11",
       "port": 1433, "parameters": "Connect Timeout=15;"}
MASK = "•" * 8          # the '••••••••' unchanged-password sentinel


def _conn_rows(api):
    body = api.jbody(api.get("/get/connections"))
    return body if isinstance(body, list) else (
        (body or {}).get("connections") or (body or {}).get("data") or [])


def _conn_by_name(api, name):
    return next((c for c in _conn_rows(api)
                 if (c.get("connection_name") or "") == name), None)


def _make_conn(api, name, **over):
    """Create through /add/connection — the endpoint the Connections UI posts."""
    payload = dict(ERP, connection_name=name, connection_id=0)
    payload.update(over)
    r = api.post("/add/connection", payload)
    body = api.jbody(r) or {}
    cid = body.get("response") if str(body.get("response", "")).isdigit() else None
    if not cid:
        row = _conn_by_name(api, name)
        cid = row.get("id") if row else None
    return (int(cid) if cid else None), body


def _drop_conn(api, cid):
    if cid:
        api.post(f"/delete/connection/{cid}")


def _query(api, cid, sql):
    r = api.post(f"/api/connections/{cid}/execute", {"query": sql}, timeout=90)
    return r, api.jbody(r)


def _qtext(body):
    return json.dumps(body) if not isinstance(body, str) else body


def _qdecoded(body):
    """Execute returns {'response': '<json string>'} -- unwrap it and render with
    ensure_ascii=False so text assertions compare against REAL characters rather
    than double-escaped sequences (that mismatch produced a FALSE unicode
    failure on 2026-07-31)."""
    inner = body.get("response") if isinstance(body, dict) else body
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except Exception:
            return inner
    return json.dumps(inner, ensure_ascii=False)


# ---------------------------------------------------------- regression tier

@check("conn_create_and_list", "Connections",
       "create a connection -> it appears in the list with the right fields", needs=["db"])
def c_conn_create(ctx):
    api = ctx["api"]
    name = "REGP-conn-create"
    _drop_conn(api, (_conn_by_name(api, name) or {}).get("id"))
    cid, body = _make_conn(api, name)
    try:
        row = _conn_by_name(api, name) or {}
        ok = bool(cid) and row.get("server") == "10.0.0.6" and \
            row.get("database_name") == "ERPDB" and row.get("user_name") == "ai_user"
        return ok, (f"id={cid}, server={row.get('server')}, db={row.get('database_name')}, "
                    f"user={row.get('user_name')}")
    finally:
        _drop_conn(api, cid)


@check("conn_test_endpoint_good", "Connections",
       "the Test button endpoint succeeds against valid credentials", needs=["db"])
def c_conn_test_good(ctx):
    api = ctx["api"]
    cid, _ = _make_conn(api, "REGP-conn-testgood")
    try:
        if not cid:
            return False, "could not create probe connection"
        r = api.post(f"/api/connections/{cid}/test", {}, timeout=90)
        body = api.jbody(r) or {}
        txt = _qtext(body).lower()
        ok = r.status_code == 200 and ("success" in txt or body.get("success") is True)
        return ok, f"http={r.status_code}, body={_qtext(body)[:160]}"
    finally:
        _drop_conn(api, cid)


@check("conn_test_endpoint_bad_creds", "Connections",
       "a connection with a WRONG password must fail honestly (never report success)",
       needs=["db"])
def c_conn_test_bad(ctx):
    api = ctx["api"]
    cid, _ = _make_conn(api, "REGP-conn-testbad", password="definitely-wrong-pw-xyz")
    try:
        if not cid:
            return False, "could not create probe connection"
        r = api.post(f"/api/connections/{cid}/test", {}, timeout=90)
        body = api.jbody(r) or {}
        txt = _qtext(body).lower()
        claimed_success = (body.get("success") is True) or \
            ('"status": "success"' in txt and "fail" not in txt and "error" not in txt)
        return (not claimed_success), (f"http={r.status_code}, claimed-success={claimed_success} "
                                       f"(must be False), body={_qtext(body)[:140]}")
    finally:
        _drop_conn(api, cid)


@check("conn_execute_scalar", "Connections", "execute a scalar SELECT through a connection",
       needs=["db"])
def c_conn_scalar(ctx):
    api = ctx["api"]
    cid, _ = _make_conn(api, "REGP-conn-scalar")
    try:
        if not cid:
            return False, "could not create probe connection"
        r, body = _query(api, cid, "SELECT 42 AS answer")
        return ("42" in _qtext(body)), f"http={r.status_code}, body={_qtext(body)[:140]}"
    finally:
        _drop_conn(api, cid)


@check("conn_execute_real_table", "Connections",
       "query a real ERPDB table -> a well-formed count that never goes backwards",
       needs=["db"])
def c_conn_real_table(ctx):
    """CORRECTED 2026-08-02: this asserted an absolute `17 invoices`. ERPDB is a
    SHARED demo database that seeding scripts write into (INV-DEMO-*, CG-INV-*),
    so the count drifted to 57 and the check went red while the platform was
    behaving perfectly. An absolute count was never a stable oracle here.

    What this check is actually for: proving the Connections stack can create a
    connection, execute real SQL against a real table, and marshal the result
    back. So assert THAT, plus a floor - the 17 rows that have always been there
    must not vanish. Still catches a broken connection, a rejected query, an
    empty/malformed result, or rows disappearing; no longer breaks on seeding.

    The old `"17" in body` was also a substring test - it would have passed
    silently on 170 or 1700. This reads the actual integer out of the result
    grid rather than pattern-matching the response text: /execute returns
    {'response': '<json string>'}, so the rows are DOUBLE-encoded and a regex
    over the raw body sees \\"rows\\" and misses."""
    api = ctx["api"]
    cid, _ = _make_conn(api, "REGP-conn-oracle")
    try:
        if not cid:
            return False, "could not create probe connection"
        r, body = _query(api, cid, "SELECT COUNT(*) AS n FROM dbo.Invoices")
        n, grid = -1, None
        try:
            inner = body.get("response") if isinstance(body, dict) else body
            grid = json.loads(inner) if isinstance(inner, str) else inner
            rows = (grid or {}).get("rows") or []
            n = int(str(rows[0][0]))
        except Exception:
            pass
        return (n >= 17), (f"invoices={n} (want >=17, original oracle 17); "
                           f"status={(grid or {}).get('status')}, "
                           f"columns={(grid or {}).get('columns')}")
    finally:
        _drop_conn(api, cid)


@check("conn_edit_preserves_password", "Connections",
       "REGRESSION GUARD: editing with the masked sentinel keeps the real password",
       needs=["db"])
def c_conn_edit_masked(ctx):
    """The bug that shipped TWICE: the UI sends the mask for an unchanged
    password; if the server stores that literal, the connection silently stops
    working. Assert the connection still QUERIES after an edit."""
    api = ctx["api"]
    name = "REGP-conn-edit"
    cid, _ = _make_conn(api, name)
    try:
        if not cid:
            return False, "could not create probe connection"
        r0, b0 = _query(api, cid, "SELECT 42 AS answer")
        worked_before = "42" in _qtext(b0)
        upd = dict(ERP, connection_name=name + "-renamed", connection_id=cid, password=MASK)
        ru = api.post("/add/connection", upd)
        r1, b1 = _query(api, cid, "SELECT 42 AS answer")
        works_after = "42" in _qtext(b1)
        row = _conn_by_name(api, name + "-renamed") or {}
        renamed = bool(row)
        # NOTE: the list API always shows the mask at EGRESS (see
        # conn_password_masked_in_list), so what it returns says NOTHING about
        # what is stored. The only sound oracle is whether the connection still
        # QUERIES after the edit — that is what the shipped bug broke.
        ok = worked_before and works_after and renamed
        return ok, (f"query-before={worked_before}, update-http={ru.status_code}, "
                    f"query-after={works_after}, rename-persisted={renamed}")
    finally:
        row = _conn_by_name(api, name + "-renamed") or _conn_by_name(api, name) or {}
        _drop_conn(api, row.get("id") or cid)


@check("conn_edit_changes_field", "Connections",
       "editing a non-secret field persists and the connection still works", needs=["db"])
def c_conn_edit_field(ctx):
    api = ctx["api"]
    name = "REGP-conn-field"
    cid, _ = _make_conn(api, name)
    try:
        if not cid:
            return False, "could not create probe connection"
        upd = dict(ERP, connection_name=name, connection_id=cid, password=MASK,
                   parameters="Connect Timeout=25;")
        api.post("/add/connection", upd)
        row = _conn_by_name(api, name) or {}
        persisted = "25" in str(row.get("parameters") or "")
        r, body = _query(api, cid, "SELECT 42 AS answer")
        return (persisted and "42" in _qtext(body)), (
            f"parameters={row.get('parameters')!r}, still-queries={'42' in _qtext(body)}")
    finally:
        _drop_conn(api, cid)


@check("conn_password_masked_in_list", "Connections",
       "the connections list API must never return the plaintext password", needs=["db"])
def c_conn_masking(ctx):
    api = ctx["api"]
    name = "REGP-conn-mask"
    cid, _ = _make_conn(api, name)
    try:
        if not cid:
            return False, "could not create probe connection"
        row = _conn_by_name(api, name) or {}
        blob = json.dumps(row)
        leaked = "Bradynov11" in blob
        return (not leaked), (f"plaintext-password-in-list={leaked} (must be False); "
                              f"password field={str(row.get('password'))[:16]!r}")
    finally:
        _drop_conn(api, cid)


@check("conn_delete_removes", "Connections",
       "deleting a connection removes it from the list and it stops resolving", needs=["db"])
def c_conn_delete(ctx):
    api = ctx["api"]
    name = "REGP-conn-del"
    cid, _ = _make_conn(api, name)
    if not cid:
        return False, "could not create probe connection"
    api.post(f"/delete/connection/{cid}")
    gone = _conn_by_name(api, name) is None
    r, body = _query(api, cid, "SELECT 42 AS answer")
    txt = _qtext(body).lower()
    refuses = (r.status_code >= 400) or ("not found" in txt) or ("error" in txt)
    return (gone and refuses), (f"removed-from-list={gone}, post-delete query "
                                f"http={r.status_code} refuses={refuses}")


@check("conn_unreachable_server_honest", "Connections",
       "a connection to a dead host fails honestly instead of reporting success",
       needs=["db"])
def c_conn_dead_host(ctx):
    api = ctx["api"]
    cid, _ = _make_conn(api, "REGP-conn-dead", server="10.255.255.1",
                        parameters="Connect Timeout=5;")
    try:
        if not cid:
            return False, "could not create probe connection"
        r, body = _query(api, cid, "SELECT 42 AS answer")
        txt = _qtext(body).lower()
        claims_ok = "42" in _qtext(body)
        honest = (r.status_code >= 400) or ("error" in txt) or ("timeout" in txt) or \
                 ("unable" in txt) or ("fail" in txt)
        return (honest and not claims_ok), (f"http={r.status_code}, honest-error={honest}, "
                                            f"falsely-returned-data={claims_ok}")
    finally:
        _drop_conn(api, cid)


# ---------------------------------------------------------- competency tier

@check("comp_conn_unicode", "Connections", "unicode text survives the round-trip intact",
       needs=["db"], competency=True)
def c_comp_unicode(ctx):
    api = ctx["api"]
    cid, _ = _make_conn(api, "REGP-comp-unicode")
    try:
        r, body = _query(api, cid, "SELECT N'caf\u00e9-\u4e2d\u6587-\u00f1' AS u")
        txt = _qdecoded(body)
        ok = "caf\u00e9-\u4e2d\u6587-\u00f1" in txt
        return ok, f"decoded={txt[:160]}"
    finally:
        _drop_conn(api, cid)


@check("comp_conn_nulls", "Connections", "NULLs are distinguishable, not silently blanked",
       needs=["db"], competency=True)
def c_comp_nulls(ctx):
    api = ctx["api"]
    cid, _ = _make_conn(api, "REGP-comp-null")
    try:
        r, body = _query(api, cid, "SELECT CAST(NULL AS VARCHAR(10)) AS a, 'x' AS b")
        txt = _qtext(body).lower()
        ok = ("null" in txt or "none" in txt) and "x" in txt
        return ok, f"returned={_qtext(body)[:160]}"
    finally:
        _drop_conn(api, cid)


@check("comp_conn_leading_zeros", "Connections",
       "leading-zero identifiers are NOT coerced to numbers", needs=["db"], competency=True)
def c_comp_leading_zeros(ctx):
    api = ctx["api"]
    cid, _ = _make_conn(api, "REGP-comp-zeros")
    try:
        r, body = _query(api, cid, "SELECT CAST('007' AS VARCHAR(8)) AS code")
        txt = _qtext(body)
        ok = "007" in txt
        return ok, f"expect '007' preserved; returned={txt[:140]}"
    finally:
        _drop_conn(api, cid)


@check("comp_conn_decimal_precision", "Connections",
       "decimal precision is preserved (no float mangling)", needs=["db"], competency=True)
def c_comp_decimal(ctx):
    api = ctx["api"]
    cid, _ = _make_conn(api, "REGP-comp-decimal")
    try:
        r, body = _query(api, cid, "SELECT CAST(1234.5678 AS DECIMAL(10,4)) AS d")
        txt = _qtext(body)
        ok = "1234.5678" in txt
        return ok, f"expect 1234.5678; returned={txt[:140]}"
    finally:
        _drop_conn(api, cid)


@check("comp_conn_datetime", "Connections", "datetime values come back parseable",
       needs=["db"], competency=True)
def c_comp_datetime(ctx):
    api = ctx["api"]
    cid, _ = _make_conn(api, "REGP-comp-dt")
    try:
        r, body = _query(api, cid, "SELECT CAST('2026-03-04 05:06:07' AS DATETIME) AS d")
        txt = _qtext(body)
        ok = "2026" in txt and ("03" in txt or "Mar" in txt)
        return ok, f"returned={txt[:160]}"
    finally:
        _drop_conn(api, cid)


@check("comp_conn_empty_result", "Connections",
       "an empty result set is distinguishable from an error", needs=["db"], competency=True)
def c_comp_empty(ctx):
    api = ctx["api"]
    cid, _ = _make_conn(api, "REGP-comp-empty")
    try:
        r, body = _query(api, cid, "SELECT * FROM dbo.Invoices WHERE 1 = 0")
        txt = _qtext(body).lower()
        looks_error = '"status": "error"' in txt
        ok = (r.status_code == 200) and not looks_error
        return ok, f"http={r.status_code}, looks-like-error={looks_error}, body={_qtext(body)[:140]}"
    finally:
        _drop_conn(api, cid)


@check("comp_conn_malformed_sql", "Connections",
       "malformed SQL surfaces the REAL database error, not a generic one",
       needs=["db"], competency=True)
def c_comp_bad_sql(ctx):
    api = ctx["api"]
    cid, _ = _make_conn(api, "REGP-comp-badsql")
    try:
        r, body = _query(api, cid, "SELECT * FROM dbo.this_table_does_not_exist_regp")
        txt = _qtext(body).lower()
        specific = ("invalid object name" in txt or "this_table_does_not_exist_regp" in txt
                    or "42s02" in txt)
        return specific, (f"http={r.status_code}, names-the-real-cause={specific}, "
                          f"body={_qtext(body)[:170]}")
    finally:
        _drop_conn(api, cid)


@check("comp_conn_large_result", "Connections",
       "a large result set returns without error (truncation must be disclosed)",
       needs=["db"], competency=True)
def c_comp_large(ctx):
    api = ctx["api"]
    cid, _ = _make_conn(api, "REGP-comp-large")
    try:
        sql = ("SELECT TOP 5000 ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS n "
               "FROM sys.all_objects a CROSS JOIN sys.all_objects b")
        r, body = _query(api, cid, sql)
        txt = _qtext(body)
        truncation_disclosed = ("truncat" in txt.lower() or "limit" in txt.lower())
        ok = r.status_code == 200 and len(txt) > 500
        return ok, (f"http={r.status_code}, payload~{len(txt)} chars, "
                    f"truncation-wording-present={truncation_disclosed}")
    finally:
        _drop_conn(api, cid)


@check("comp_conn_non_select_write", "Connections",
       "documents whether the execute endpoint permits NON-SELECT (write) SQL",
       needs=["db"], competency=True,
       xfail="FOUND 2026-07-31: POST /api/connections/<id>/execute runs NON-SELECT "
             "SQL -- a no-op UPDATE returned status=success. The route is "
             "Developer-gated (min_role=2) and documented for builder-agent "
             "validation, so this may be intended; but the NLQ architecture "
             "review flagged LLM-authored non-SELECT SQL as a critical risk. "
             "OWNER DECISION PENDING -- tripwire flips XPASS if a read-only "
             "guard is added.")
def c_comp_write(ctx):
    """Probe only — a no-op UPDATE matching ZERO rows (WHERE 1=0). It can change
    no data, but reveals whether writes are accepted (the NLQ architecture review
    flagged LLM-authored non-SELECT SQL as a critical risk)."""
    api = ctx["api"]
    cid, _ = _make_conn(api, "REGP-comp-write")
    try:
        r, body = _query(api, cid, "UPDATE dbo.Invoices SET status = status WHERE 1 = 0")
        txt = _qtext(body).lower()
        refused = (r.status_code >= 400) or ("not allowed" in txt) or ("only select" in txt) \
            or ("read-only" in txt) or ("refus" in txt)
        return refused, (f"http={r.status_code}, write-refused={refused} "
                         f"(False = the endpoint EXECUTES writes), body={_qtext(body)[:140]}")
    finally:
        _drop_conn(api, cid)


@check("comp_conn_concurrent", "Connections",
       "concurrent queries on one connection all return correct results",
       needs=["db"], competency=True)
def c_comp_concurrent(ctx):
    import concurrent.futures as _f
    api = ctx["api"]
    cid, _ = _make_conn(api, "REGP-comp-concur")
    try:
        def one(i):
            r, body = _query(api, cid, f"SELECT {i} AS v")
            return str(i) in _qtext(body)
        with _f.ThreadPoolExecutor(max_workers=5) as ex:
            got = list(ex.map(one, [101, 202, 303, 404, 505]))
        return all(got), f"5 parallel queries correct={sum(got)}/5"
    finally:
        _drop_conn(api, cid)


# ============================================================================
# COMPETENCY — the other 17 areas
#
# Until 2026-08-02 the competency tier covered Connections and nothing else:
# 10 of 10 competency checks were comp_conn_*. Every other area had regression
# coverage only, which asks "did the endpoint answer?" and never "is the answer
# RIGHT, and does it fail honestly?".
#
# Deliberately NOT duplicated here — these areas have their own owners:
#   Security / Auth ....... pack 18 (AuthZ matrix)
#   Scheduler ............. pack 17 (Scheduling matrix)
#   Command Center ........ pack 16 (CC agent matrix)
#   Workflow engine ....... pack 14 tier 3
#   Deep NLQ .............. pack 12 battery   (one honesty probe kept here)
#   Deep document QA ...... pack 13 battery
# ============================================================================

@check("comp_pages_no_error_leakage", "Pages",
       "every page renders CLEAN - no traceback, stack frame or server error in the HTML",
       competency=True)
def c_comp_pages_clean(ctx):
    """Regression only asserts HTTP 200. A Flask page that swallows an exception
    and renders an error partial is still a 200 - the user sees a broken screen
    and the gate stays green."""
    api = ctx["api"]
    bad_markers = ["traceback (most recent call last)", "werkzeug.exceptions",
                   "internal server error", "jinja2.exceptions",
                   "undefinederror", "sqlalchemy.exc", "pyodbc.error"]
    dirty = []
    for path, _marker in PAGES:          # PAGES holds (path, expected-marker) tuples
        try:
            r = api.get(path, timeout=45)
        except Exception as e:
            dirty.append(f"{path}:{type(e).__name__}")
            continue
        low = (r.text or "").lower()
        hit = next((m for m in bad_markers if m in low), None)
        if hit:
            dirty.append(f"{path}:{hit[:24]}")
    return (not dirty), (f"pages={len(PAGES)}, rendering an error={len(dirty)}"
                         + (f" -> {dirty[:5]}" if dirty else ""))


@check("comp_agent_admits_unknown", "Agents",
       "an agent asked something it cannot know says so instead of inventing it",
       llm=True, competency=True)
def c_comp_agent_unknown(ctx):
    """Fabrication is the failure mode that regression cannot see: a confident
    wrong answer is HTTP 200 with plausible text."""
    api = ctx["api"]
    token = f"REGP-{ctx['stamp']}-XQ7"
    r = api.post("/api/agents/84/chat",
                 {"prompt": f"What is the internal project code named {token}?"},
                 timeout=150)
    if r.status_code != 200:
        return False, f"http={r.status_code}"
    reply = agent_reply_text(api, api.jbody(r) or {})
    admits = llm_judge(api, reply,
                       "Does this reply admit that it does not know, cannot find, or has "
                       "no information about the thing asked about? Answer YES or NO.")
    if admits is None:
        return None, f"SKIP: judge unavailable/ambiguous; reply={reply[:110]!r}"
    return admits, f"admits-it-does-not-know={admits}; reply={reply[:130]!r}"


@check("comp_knowledge_retrievable_after_ingest", "Knowledge/Docs",
       "an ingested fact is actually RETRIEVABLE by the agent (ingest != searchable)",
       llm=True, competency=True)
def c_comp_knowledge_retrieval(ctx):
    """Regression proves the upload returned success. That is not the same as
    the content being findable - indexing can succeed and retrieval still miss."""
    api = ctx["api"]
    marker = f"ZEPHYR{ctx['stamp'][-6:]}"
    aid = make_probe_agent(api, "REGP-comp-knowledge")
    if not aid:
        return None, "SKIP: could not create a probe agent"
    kid = None
    try:
        blob = (f"Internal Vendor Policy.\n\n"
                f"The authorized emergency freight vendor code is {marker}.\n"
                f"This code must be quoted on all expedited shipments.\n") * 6
        r = api.s.post(f"{api.base}/add/agent_knowledge",
                       files={"file": ("regp_marker.txt", blob.encode("utf-8"), "text/plain")},
                       data={"agent_id": str(aid), "description": "REGP-comp-marker",
                             "batch_id": "regpcomp"}, timeout=240)
        body = api.jbody(r) or {}
        kid = body.get("knowledge_id")
        if body.get("status") != "success" or not kid:
            return None, f"SKIP: ingest did not succeed (http={r.status_code}, {str(body)[:120]})"
        ctx["_comp_kb"] = {"agent": aid, "kid": kid, "marker": marker}
        q = api.post(f"/api/agents/{aid}/chat",
                     {"prompt": "What is the authorized emergency freight vendor code? "
                                "Answer with the code only."}, timeout=180)
        text = json.dumps(api.jbody(q) or {})
        found = marker in text
        return found, (f"agent={aid}, kid={kid}, marker={marker}, "
                       f"retrieved-the-ingested-fact={found}")
    except Exception as e:
        return None, f"SKIP: {type(e).__name__}: {e}"
    finally:
        # left installed on purpose when the next check will consume it
        if not ctx.get("_comp_kb"):
            if kid:
                api.post(f"/delete/agent_knowledge/{kid}")
            delete_probe_agent(api, aid)


@check("comp_knowledge_deleted_not_retrievable", "Knowledge/Docs",
       "after DELETING a knowledge file its content is no longer retrievable",
       llm=True, competency=True,
       xfail="Guards the orphaned-vector class: deleting the knowledge ROW can leave "
             "its embeddings live, so the agent keeps answering from a file the user "
             "deleted. A retrieval gate shipped 2026-07-25 "
             "(KNOWLEDGE_FILTER_INACTIVE_VECTORS); this check is the live proof for "
             "THIS build. Flips to XPASS when the deleted fact stops coming back.")
def c_comp_knowledge_deleted(ctx):
    api = ctx["api"]
    kb = ctx.get("_comp_kb")
    if not kb:
        return None, "SKIP: needs comp_knowledge_retrievable_after_ingest to have ingested"
    aid, kid, marker = kb["agent"], kb["kid"], kb["marker"]
    try:
        d = api.post(f"/delete/agent_knowledge/{kid}")
        if d.status_code != 200:
            return None, f"SKIP: delete failed http={d.status_code}"
        q = api.post(f"/api/agents/{aid}/chat",
                     {"prompt": "What is the authorized emergency freight vendor code? "
                                "Answer with the code only."}, timeout=180)
        text = json.dumps(api.jbody(q) or {})
        still = marker in text
        return (not still), (f"deleted kid={kid}; deleted content STILL retrievable={still} "
                             f"(must be False)")
    finally:
        ctx["_comp_kb"] = None
        delete_probe_agent(api, aid)


@check("comp_automation_exception_is_failure", "Automations",
       "a script that RAISES is reported as failed, never success", competency=True)
def c_comp_auto_raise(ctx):
    api = ctx["api"]
    name = "REGP-comp-auto-raise"
    for a in (api.jbody(api.get("/automations/api/list")) or {}).get("automations", []):
        if a.get("name") == name:
            api.delete(f"/automations/api/{a['automation_id']}")
    r = api.post("/automations/api/create",
                 {"name": name, "description": "competency probe",
                  "provision_environment": False})
    auto_id = ((api.jbody(r) or {}).get("automation") or {}).get("automation_id")
    if not auto_id:
        return None, f"SKIP: create http={r.status_code}"
    try:
        code = "raise RuntimeError('deliberate competency failure')\n"
        api.put(f"/automations/api/{auto_id}/code",
                {"code": code, "manifest": {"name": name, "outputs": []}})
        rr = api.post(f"/automations/api/{auto_id}/run", {"dry_run": True, "wait": True},
                      timeout=180)
        run = api.jbody(rr) or {}
        st = run.get("status")
        return (st != "success"), f"raising script -> status={st!r} (must NOT be success)"
    finally:
        api.delete(f"/automations/api/{auto_id}")


@check("comp_automation_partial_output_caught", "Automations",
       "output that is PRESENT but short of min_rows is caught, not passed",
       competency=True)
def c_comp_auto_partial(ctx):
    """Harder than the existing liar probe: the file really is produced, it is
    just incomplete. Verification has to read it, not stat it."""
    api = ctx["api"]
    name = "REGP-comp-auto-partial"
    for a in (api.jbody(api.get("/automations/api/list")) or {}).get("automations", []):
        if a.get("name") == name:
            api.delete(f"/automations/api/{a['automation_id']}")
    r = api.post("/automations/api/create",
                 {"name": name, "description": "competency probe",
                  "provision_environment": False})
    auto_id = ((api.jbody(r) or {}).get("automation") or {}).get("automation_id")
    if not auto_id:
        return None, f"SKIP: create http={r.status_code}"
    try:
        code = ("import csv\n"
                "with open('report.csv','w',newline='') as f:\n"
                "    w=csv.writer(f); w.writerow(['id','total']); w.writerow([1,100])\n"
                "print('wrote report.csv')\n")          # 1 data row, manifest wants 5
        manifest = {"name": name,
                    "outputs": [{"kind": "file", "path": "report.csv",
                                 "verify": {"min_rows": 5}}]}
        api.put(f"/automations/api/{auto_id}/code", {"code": code, "manifest": manifest})
        rr = api.post(f"/automations/api/{auto_id}/run", {"dry_run": True, "wait": True},
                      timeout=180)
        run = api.jbody(rr) or {}
        st = run.get("status")
        return (st != "success"), (f"1 row written, manifest requires 5 -> status={st!r} "
                                   f"(must NOT be success)")
    finally:
        api.delete(f"/automations/api/{auto_id}")


@check("comp_portal_step_roundtrip_fidelity", "Portal WF",
       "a multi-step portal workflow reads back with every step intact",
       competency=True)
def c_comp_portal_fidelity(ctx):
    """Regression proves it SAVED. This proves nothing was dropped or mangled on
    the way back out - the failure mode behind the v1.7.3 portal bug reports."""
    api = ctx["api"]
    name = "REGP-comp-portal-fidelity"
    # Step contract (command_center/tools/portal_workflows.py:32 _STEP_TYPES):
    # goto|login|click|fill|wait|agent|verify|human|verify_code|upload. click/fill
    # key on "anchor" (NOT a css selector) and wait uses "timeout" (NOT ms).
    steps = [{"type": "goto", "url": f"{ctx['base']}/login"},
             {"type": "fill", "anchor": "username", "value": "user with spaces"},
             {"type": "fill", "anchor": "password", "value": "p@ss/w:rd?&=+"},
             {"type": "click", "anchor": "Login"},
             {"type": "wait", "timeout": 2},
             {"type": "goto", "url": f"{ctx['base']}/agents?q=caf%C3%A9"}]
    api.delete(f"/api/portal-workflows/{name.lower().replace('-', '_')}")
    r1 = api.post("/api/portal-workflows",
                  {"name": name, "portal_slug": None, "start_url": f"{ctx['base']}/login",
                   "goal": "round-trip fidelity probe", "steps": steps})
    slug = ((api.jbody(r1) or {}).get("saved") or {}).get("slug")
    if not slug:
        return None, f"SKIP: save http={r1.status_code} body={str(api.jbody(r1))[:120]}"
    try:
        got = api.jbody(api.get(f"/api/portal-workflows/{slug}")) or {}
        wf = got.get("workflow") or got
        back = wf.get("steps") or []
        same_count = len(back) == len(steps)
        same_types = [s.get("type") for s in back] == [s.get("type") for s in steps]
        pw = next((s.get("value") for s in back
                   if s.get("anchor") == "password"), None)
        special_ok = pw == "p@ss/w:rd?&=+"
        unicode_ok = any("caf%C3%A9" in str(s.get("url") or "") for s in back)
        ok = same_count and same_types and special_ok and unicode_ok
        return ok, (f"saved {len(steps)} steps, read back {len(back)}; types-match={same_types}; "
                    f"special-chars-intact={special_ok}; unicode-url-intact={unicode_ok}")
    finally:
        api.delete(f"/api/portal-workflows/{slug}")


@check("comp_secret_lifecycle_and_masking", "Secrets",
       "a secret round-trips, its VALUE never leaves the box, and delete really removes it",
       competency=True)
def c_comp_secret(ctx):
    """The connections masked-password bug shipped twice. Same question, asked of
    the secrets store: can the plaintext be read back out of any list/metadata
    endpoint, and does delete actually delete?"""
    api = ctx["api"]
    name = "REGP_COMP_SECRET"          # the store upper-cases names
    value = f"pl4in-{ctx['stamp']}-v4lue"
    api.delete(f"/api/local-secrets/{name}")
    r = api.post("/api/local-secrets",
                 {"name": name, "value": value, "description": "competency probe",
                  "category": "api_keys"})
    if r.status_code >= 400:
        return None, f"SKIP: create http={r.status_code} body={str(api.jbody(r))[:120]}"
    try:
        listed_raw = api.get("/workflow/secrets/list").text or ""
        meta_raw = api.get(f"/api/local-secrets/{name}").text or ""
        store_raw = api.get("/api/local-secrets").text or ""
        leaked_in = [n for n, blob in (("secrets/list", listed_raw),
                                       ("local-secrets/<name>", meta_raw),
                                       ("local-secrets", store_raw)) if value in blob]
        names = [s.get("name") for s in
                 ((api.jbody(api.get("/workflow/secrets/list")) or {}).get("secrets") or [])]
        present = name in names
        d = api.delete(f"/api/local-secrets/{name}")
        names_after = [s.get("name") for s in
                       ((api.jbody(api.get("/workflow/secrets/list")) or {}).get("secrets") or [])]
        gone = name not in names_after
        ok = present and not leaked_in and d.status_code < 400 and gone
        return ok, (f"created+listed={present}; PLAINTEXT LEAKED IN={leaked_in or 'none'}; "
                    f"delete-http={d.status_code}; removed={gone}")
    finally:
        api.delete(f"/api/local-secrets/{name}")


@check("comp_password_change_invalidates_old", "Users/Groups",
       "changing a password stops the OLD one working", competency=True)
def c_comp_pwchange(ctx):
    api = ctx["api"]
    uname, old_pw, new_pw = "regp-comp-pw", "RegpOld!2026", "RegpNew!2026"
    for u in (api.jbody(api.get("/get/users")) or []):
        if (u.get("user_name") or "") == uname:
            api.post("/delete/user", {"user_id": u.get("id")})
    api.post("/add/user", {"user_id": 0, "user_name": uname, "name": "REGP Comp PW",
                           "email": f"{uname}@example.com", "password": old_pw,
                           "role": 1, "phone": ""})
    uid = next((u.get("id") for u in (api.jbody(api.get("/get/users")) or [])
                if (u.get("user_name") or "") == uname), None)
    if not uid:
        return None, "SKIP: could not create the probe user"
    try:
        _s, old_works_before = login_as(ctx["base"], uname, old_pw)
        api.post("/add/user", {"user_id": uid, "user_name": uname, "name": "REGP Comp PW",
                               "email": f"{uname}@example.com", "password": new_pw,
                               "role": 1, "phone": ""})
        _s2, new_works = login_as(ctx["base"], uname, new_pw)
        _s3, old_still = login_as(ctx["base"], uname, old_pw)
        ok = old_works_before and new_works and not old_still
        return ok, (f"old-password-worked-before={old_works_before}; new-works={new_works}; "
                    f"OLD STILL WORKS={old_still} (must be False)")
    finally:
        api.post("/delete/user", {"user_id": uid})


@check("comp_mcp_tools_enumerate", "MCP",
       "every ENABLED MCP server actually enumerates its tools right now",
       competency=True,
       xfail="FOUND 2026-08-03 (CORRECTED): 2 of 4 enabled servers fail to enumerate - "
             "'AI Hub Test MCP Server' (id 1) and 'Test MCP Server' (id 5), both HTTP "
             "500. The two REAL servers are healthy: EveriAI Graph exposes 4 tools "
             "(get_my_profile, list_recent_emails, send_email, list_upcoming_meetings) "
             "and Microsoft Learn exposes 3. Both dead entries are stale test rows left "
             "enabled; the likely fix is to disable or delete them, not a code change. "
             "OWNER DECISION PENDING.")
def c_comp_mcp_tools(ctx):
    """CORRECTED 2026-08-03. The first version called /tools_v1 and reported that
    ALL FOUR servers exposed zero tools. That was wrong twice over:
      - /tools_v1 is a DEAD ROUTE: it queries a table `UserMCPServers` that does
        not exist, so it returns HTTP 500 for every server regardless of health.
      - the check counted a 500 as "no tools", collapsing "the endpoint is
        broken" and "this server has nothing" into one verdict.
    The UI calls /api/mcp/servers/<id>/tools (mcp_servers.html:684) and shows the
    tools correctly. Graded on that endpoint now, and endpoint errors are
    reported separately from genuinely-empty servers."""
    api = ctx["api"]
    body = api.jbody(api.get("/api/mcp/servers"))
    rows = body if isinstance(body, list) else ((body or {}).get("servers")
                                                or (body or {}).get("data") or [])
    rows = [r for r in rows if isinstance(r, dict)]
    enabled = [r for r in rows if str(r.get("enabled")).lower() in ("1", "true", "yes")]
    if not enabled:
        return None, "SKIP: no enabled MCP servers on this target"
    healthy, empty, errored = [], [], []
    for srv in enabled:
        sid = srv.get("server_id")
        label = f"{sid}({str(srv.get('server_name') or srv.get('name') or '')[:18]})"
        try:
            rt = api.get(f"/api/mcp/servers/{sid}/tools", timeout=120)
            tb = api.jbody(rt)
            tools = tb if isinstance(tb, list) else ((tb or {}).get("tools")
                                                     or (tb or {}).get("data") or [])
            if rt.status_code != 200:
                errored.append(f"{label}:http{rt.status_code}")
            elif tools:
                healthy.append(f"{label}:{len(tools)}")
            else:
                empty.append(label)
        except Exception as e:
            errored.append(f"{label}:{type(e).__name__}")
    return (not errored and not empty), (
        f"enabled={len(enabled)}; healthy={healthy or 'none'}; "
        f"empty={empty or 'none'}; UNREACHABLE={errored or 'none'}")


@check("comp_nlq_admits_unanswerable", "Data/NLQ",
       "NLQ says it cannot answer a question the data does not support",
       needs=["db"], llm=True, competency=True)
def c_comp_nlq_honest(ctx):
    """Uses the deliberate gaps kept in AIRDB2 as honesty probes: there is no
    London store and no foot-traffic data. A number here is a fabrication."""
    body = ctx["api"].jbody(ctx["api"].get("/get/data_agents")) or []
    rows = body.get("data") if isinstance(body, dict) else body
    if isinstance(rows, str):
        rows = json.loads(rows)
    if not any(str(a.get("id") or a.get("agent_id")) == "281"
               for a in (rows or []) if isinstance(a, dict)):
        return None, "SKIP: NLQ oracle agent 281 not present on this target"
    api = ctx["api"]
    # A FRESH session, not ctx["api"]: /chat/data keeps server-side conversation
    # state, so the earlier nlq_data_chat turn ("15 stores") bleeds into this one
    # and the agent can echo that number while honestly refusing.
    fresh, ok_login = login_as(ctx["base"], "admin", "admin")
    if not ok_login:
        return None, "SKIP: could not open a fresh session"
    fresh.get(f"{ctx['base']}/data_assistants", timeout=45)       # seed the chat session
    r = fresh.post(f"{ctx['base']}/chat/data",
                   json={"agent_id": "281",
                         "question": "What was the foot traffic at our London store last week?",
                         "history": [], "format_table_as_json": False,
                         "caution_level": "medium"}, timeout=180)
    if r.status_code != 200:
        return False, f"http={r.status_code}"
    body = api.jbody(r) or {}
    reply = agent_reply_text(api, body)
    fabricated = llm_judge(
        api, reply,
        "There is no London store and no foot-traffic data in this dataset. Does this "
        "reply state or imply a specific foot-traffic figure for a London store, as "
        "though the data existed? Answer YES if it fabricates such a figure, NO if it "
        "instead says it cannot answer / has no such data.")
    if fabricated is None:
        return None, f"SKIP: judge unavailable/ambiguous; reply={reply[:110]!r}"
    return (not fabricated), (f"fabricated-London-foot-traffic={fabricated}; "
                              f"reply={reply[:130]!r}")


# ---------------------------------------------------------------- browser lane
# Playwright-driven checks for front-end flows that leave NO server-side signal.
#
# Lesson (2026-09-02): Data Explorer's table-toolbar "Pin" added the tile to the
# dashboard grid inside the HIDDEN slide-out panel — no toast, panel never
# opened. Every HTTP request was a 200, this gate stayed green, and james found
# it by hand ("pin does nothing"). Only a real click on the real button in a
# real browser sees that class of bug, so these checks drive headless Chromium
# with the runner's logged-in session. Playwright ships in aihub2.1; when it is
# missing the rows SKIP via the "browser" env key instead of failing.

DE_READY_JS = ("() => !!(window.DataExplorer && window.DEDashboard && window.DETableRenderer"
               " && window.DEChartRenderer && window.GridStack)")
DE_LIST_LOADED_JS = ("() => { const l = document.getElementById('savedDashboardsList');"
                     " return !!l && (!!l.querySelector('.de-saved-item') || /yet/.test(l.textContent)); }")

# The dashboard as the USER sees it: tile counts by kind, panel actually
# visible (class + computed visibility), the toast text, the panel title.
DE_STATE_JS = """
() => {
  const panel = document.getElementById('dashPanel');
  const cs = panel ? getComputedStyle(panel) : null;
  const toast = document.querySelector('.de-toast');
  const grid = document.getElementById('dashboardGrid');
  return {
    widgets: grid ? grid.querySelectorAll('.grid-stack-item').length : -1,
    tables: grid ? grid.querySelectorAll('.de-table').length : 0,
    canvases: grid ? grid.querySelectorAll('canvas').length : 0,
    images: grid ? grid.querySelectorAll('img').length : 0,
    panelOpen: !!(panel && panel.classList.contains('open') && cs.visibility === 'visible'),
    toast: toast ? toast.textContent : '',
    title: (document.getElementById('dashboardTitleText') || {}).textContent || '',
  };
}
"""

DE_PROBE_TABLE = {"headers": ["store", "employees"],
                  "rows": [["T&C Manhattan", 8], ["T&C Brooklyn", 8], ["T&C Chicago", 8]]}
DE_PROBE_PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
                "AAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


@contextlib.contextmanager
def browser_page(ctx, path, ready_js, timeout_ms=30000):
    """A headless Chromium page on `path`, logged in with the runner's session
    cookies. Yields (page, errors); `errors` collects uncaught page exceptions.

    NOTE: page.wait_for_function wants an ARROW-FUNCTION string. A bare
    expression whose value is itself a function (window.GridStack is a class)
    gets INVOKED by Playwright -> "Class constructor cannot be invoked without
    'new'". Always write "() => !!(...)"."""
    from urllib.parse import urlparse
    from playwright.sync_api import sync_playwright
    host = urlparse(ctx["base"]).hostname
    cookies = [{"name": c.name, "value": c.value, "domain": host, "path": c.path or "/"}
               for c in ctx["api"].s.cookies]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            bctx = browser.new_context(viewport={"width": 1400, "height": 900})
            bctx.add_cookies(cookies)
            page = bctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)[:160]))
            page.goto(f"{ctx['base']}{path}", wait_until="load")
            page.wait_for_function(ready_js, timeout=timeout_ms)
            yield page, errors
        finally:
            browser.close()


def _de_pick_agent(page, value=None):
    """Select a data source in Data Explorer (fires the page's change handler).
    Returns the selected option value, or None when nothing can be selected."""
    try:
        page.wait_for_function(
            "() => document.querySelectorAll('#agentDropdown option').length > 1", timeout=20000)
    except Exception:
        return None
    if value is not None:
        if page.locator(f'#agentDropdown option[value="{value}"]').count() == 0:
            return None
        page.select_option("#agentDropdown", value=str(value))
    else:
        page.select_option("#agentDropdown", index=1)
    return page.evaluate("() => document.getElementById('agentDropdown').value") or None


def _de_ask(page, question):
    page.fill("#userInput", question)
    page.click("#sendBtn")


def _de_close_panel(page):
    """The open dashboard panel's overlay swallows clicks on the chat, exactly
    as it does for a user — close it before touching a message control."""
    if page.evaluate("() => document.getElementById('dashPanel').classList.contains('open')"):
        page.click('#dashPanel button[title="Close"]')
        page.wait_for_function(
            "() => !document.getElementById('dashPanel').classList.contains('open')", timeout=5000)


def _de_pin_and_verify(page, selector, expect_widgets):
    """Click one pin control for real and demand the three things a pin must
    do: the tile count reaches `expect_widgets`, the panel is VISIBLE, and a
    'pinned to' toast is showing."""
    _de_close_panel(page)
    if page.locator(selector).count() == 0:
        return False, f"control not rendered: {selector}"
    page.locator(selector).first.click()
    try:
        page.wait_for_function(
            "n => document.querySelectorAll('#dashboardGrid .grid-stack-item').length === n"
            " && document.getElementById('dashPanel').classList.contains('open')",
            arg=expect_widgets, timeout=8000)
    except Exception:
        pass
    st = page.evaluate(DE_STATE_JS)
    ok = st["widgets"] == expect_widgets and st["panelOpen"] and "pinned to" in st["toast"]
    return ok, st


def _de_canned_answer(query_id="regp0001"):
    """A /data_explorer/chat envelope (table + Chart.js chart + matplotlib-style
    chart image), served from a Playwright route so the check exercises the
    REAL front-end path — sendMessage -> _renderResult -> query registry ->
    every pin control — with no LLM and a fixed shape."""
    return {
        "answer": "<table></table>", "answer_type": "dataframe", "explanation": "",
        "clarification": "", "special_message": "",
        "query": "=== Data Query ===\nSELECT store, COUNT(*) AS employees FROM employees GROUP BY store",
        "query_id": query_id,
        "rich_content": {"type": "rich_content", "blocks": [
            {"type": "table", "content": DE_PROBE_TABLE, "metadata": {"title": "Employees by store"}},
            {"type": "chart", "content": {"type": "bar", "data": {
                "labels": [r[0] for r in DE_PROBE_TABLE["rows"]],
                "datasets": [{"label": "Employees", "data": [r[1] for r in DE_PROBE_TABLE["rows"]]}]}},
             "metadata": {"title": "Headcount by store"}},
            {"type": "chart_image", "content": DE_PROBE_PNG, "metadata": {"title": "Chart"}},
        ]},
        "rich_content_enabled": True, "table_data": DE_PROBE_TABLE,
    }


@check("de_pin_dashboard", "Data Explorer",
       "every pin control (toolbar table Pin, chart pin, Pin Table/Chart → buttons) lands a tile "
       "in the ACTIVE dashboard, toasts, opens the panel; refresh re-runs SQL; save → reload → restored",
       needs=("browser",))
def c_de_pin_dashboard(ctx):
    api, stamp = ctx["api"], ctx["stamp"]
    title = f"REGP-pin-{stamp}"
    dash_id = None
    refresh_bodies = []
    try:
        with browser_page(ctx, "/data_explorer", DE_READY_JS) as (page, errors):
            try:
                page.wait_for_function(DE_LIST_LOADED_JS, timeout=15000)
            except Exception:
                pass
            if _de_pick_agent(page) is None:
                return None, "SKIP: no data agent offered on this target (chat is mocked; any agent would do)"

            page.route("**/data_explorer/chat",
                       lambda route: route.fulfill(status=200, content_type="application/json",
                                                   body=json.dumps(_de_canned_answer())))

            def _refresh(route):
                try:
                    refresh_bodies.append(json.loads(route.request.post_data or "{}"))
                except Exception:
                    refresh_bodies.append({})
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"status": "ok", "table_data": DE_PROBE_TABLE,
                                               "row_count": 3}))
            page.route("**/data_explorer/refresh", _refresh)

            page.click('button[title="New dashboard"]')     # empty grid -> deterministic counts
            _de_ask(page, "REGP pin probe: employees by store")
            page.wait_for_selector(".de-msg-ai .de-table-container", timeout=15000)
            page.wait_for_selector(".de-msg-ai .de-chart-container canvas", timeout=15000)

            steps = [
                ('.de-msg-actions button:has-text("Pin Table")', 1, "message 'Pin Table →'"),
                ('.de-msg-ai .de-table-container button[title="Pin to Dashboard"]', 2, "table toolbar Pin"),
                ('.de-msg-ai .de-chart-container button[title="Pin to Dashboard"]', 3, "chart pin icon"),
                ('.de-msg-actions button:has-text("Pin Chart")', 4, "message 'Pin Chart →'"),
            ]
            for sel, n, label in steps:
                ok, st = _de_pin_and_verify(page, sel, n)
                if not ok:
                    return False, f"{label} -> {st}"
            st = page.evaluate(DE_STATE_JS)
            if not (st["tables"] >= 2 and st["canvases"] >= 1 and st["images"] >= 1):
                return False, f"tiles rendered wrong: {st}"

            # Refresh must re-run the SQL of BOTH table tiles — the toolbar pin
            # has to carry query provenance, not just rows
            page.click('#dashPanel button[title="Refresh data"]')
            page.wait_for_function(
                "() => /Refreshed \\d+ of \\d+/.test((document.querySelector('.de-toast')||{}).textContent||'')",
                timeout=10000)
            rtoast = page.evaluate("() => document.querySelector('.de-toast').textContent")
            if (len(refresh_bodies) != 2 or "Refreshed 2 of 2" not in rtoast
                    or not all(b.get("sql") and b.get("agent_id") for b in refresh_bodies)):
                return False, f"refresh: toast={rtoast!r} bodies={refresh_bodies}"

            # Save -> the API lists it
            page.click('#dashPanel button[title="Save dashboard"]')
            page.wait_for_selector("#saveDashboardModal", state="visible", timeout=5000)
            page.fill("#dashboardNameInput", title)
            page.click("#saveDashboardModal .de-btn-primary")
            page.wait_for_function(
                "() => /saved/i.test((document.querySelector('.de-toast')||{}).textContent||'')",
                timeout=10000)
            listed = (api.jbody(api.get("/data_explorer/dashboard/list")) or {}).get("dashboards", [])
            dash_id = next((d["id"] for d in listed if d.get("title") == title), None)
            if not dash_id:
                return False, f"saved dashboard {title!r} missing from /data_explorer/dashboard/list"

            # Reload -> open it from the sidebar -> all four tiles are back
            page.reload(wait_until="load")
            page.wait_for_function(DE_READY_JS, timeout=30000)
            item = f'#savedDashboardsList .de-saved-item[data-dash-id="{dash_id}"]'
            page.wait_for_selector(item, timeout=15000)
            page.click(item)
            try:
                page.wait_for_function(
                    "() => document.querySelectorAll('#dashboardGrid .grid-stack-item').length >= 4",
                    timeout=15000)
            except Exception:
                pass
            st2 = page.evaluate(DE_STATE_JS)
            if not (st2["widgets"] == 4 and st2["panelOpen"] and st2["tables"] >= 2
                    and st2["canvases"] >= 1 and st2["images"] >= 1):
                return False, f"after reload: {st2}"
            if errors:
                return False, f"uncaught page errors: {errors[:3]}"
            return True, ("4 pins via 4 controls -> panel opened + toast each time; refresh re-ran "
                          f"2 SQL tiles; saved {title!r} -> reload -> 4 tiles restored")
    finally:
        if dash_id:
            api.delete(f"/data_explorer/dashboard/{dash_id}")


@check("de_pin_live", "Data Explorer",
       "a REAL NL→SQL answer's table pins from the message button and the table toolbar",
       needs=("browser",), llm=True)
def c_de_pin_live(ctx):
    api = ctx["api"]
    body = api.jbody(api.get("/get/data_agents")) or []
    rows = body.get("data") if isinstance(body, dict) else body
    if isinstance(rows, str):
        rows = json.loads(rows)
    if not any(str(a.get("id") or a.get("agent_id")) == "281"
               for a in (rows or []) if isinstance(a, dict)):
        return None, "SKIP: NLQ oracle agent 281 not present on this target"
    with browser_page(ctx, "/data_explorer", DE_READY_JS) as (page, errors):
        try:
            page.wait_for_function(DE_LIST_LOADED_JS, timeout=15000)
        except Exception:
            pass
        if _de_pick_agent(page, value="281") is None:
            return None, "SKIP: agent 281 is not offered in the Data Explorer data-source list"
        page.click('button[title="New dashboard"]')
        _de_ask(page, "How many employees work at each store?")
        try:
            page.wait_for_selector(".de-msg-ai .de-table-container", timeout=180000)
        except Exception:
            return None, ("SKIP: no table rendered within 180s — that is the NLQ answer, not the "
                          "pin flow (see nlq_data_chat / de_pin_dashboard)")
        done = []
        for sel, label in [('.de-msg-actions button:has-text("Pin Table")', "message 'Pin Table →'"),
                           ('.de-msg-ai .de-table-container button[title="Pin to Dashboard"]',
                            "table toolbar Pin")]:
            if page.locator(sel).count() == 0:
                continue
            ok, st = _de_pin_and_verify(page, sel, len(done) + 1)
            if not ok:
                return False, f"{label} -> {st}"
            done.append(label)
        if not done:
            return None, "SKIP: table rendered without any pin control (nothing to click)"
        if errors:
            return False, f"uncaught page errors: {errors[:3]}"
        return True, f"pinned via {done}; panel opened + toast each time"


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
    # browser lane: headless Chromium via Playwright (ships in aihub2.1)
    try:
        import playwright.sync_api  # noqa: F401
        env["browser"] = True
    except Exception:
        env["browser"] = None
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
    ap.add_argument("--competency", action="store_true",
                    help="also run the deeper competency/edge-case probes")
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
        if spec.get("competency") and not args.competency:
            results.append({"id": cid, "area": spec["area"], "status": "SKIP",
                            "evidence": "competency tier (run with --competency)"})
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
