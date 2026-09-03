"""
Authentication & Authorization Matrix — pack 18.

WHY THIS AREA: five of the seven tripwires standing open across packs 14/15/17
are missing-authorization findings, in three unrelated subsystems (approvals,
agent creation, scheduler). That is not five bugs — that is one bug wearing
five hats, and no pack owned the question "who is allowed to do what?"

Root cause, established 2026-08-02 by reading the source and confirmed
behaviourally by a11 below:

  `auth_middleware.py` implements exactly the right thing — a global
  before_request that blocks every unauthenticated request except a 12-entry
  allowlist. It ships with a full unit-test suite (tests/security/
  test_auth_middleware.py) and those tests pass.

  **It is never wired into the application.** `init_auth_middleware` appears in
  no module outside its own file and its tests; app.py never calls it. The only
  @app.before_request in app.py (line ~1370) just sets user-tracking context and
  performs no auth check.

So the app's ONLY protection is per-route decorators, and ~349 of ~929 routes
carry none. The unit tests pass because they exercise the middleware in
isolation, on a Flask app the test itself constructs. Nothing tested the wiring.
This pack tests the wiring.

TIER A (regression, default): deterministic contract checks — the allowlist
hasn't grown, dry-run is off, login/logout/session behave, the role ladder
holds, and THE SWEEP: probe every parameterless GET route anonymously and pin
how many answer. That number is a ratchet; it may fall, never rise.

TIER B (competency, --competency): the questions that decide whether the gap is
theoretical or exploitable — can a low-role user escalate itself? can user A
read user B's data? does an anonymous write actually PERSIST, or merely return
200? Graded on real state changes, never on status codes alone.

SAFETY: the sweep is GET-only, parameterless, and skips anything whose path
matches DANGEROUS_PATH (delete/reset/restart/...). Some GETs still write —
`GET /api/scheduler/jobs/<id>/schedules` INSERTs a row (pack 17 s13) — so the
runner snapshots the scheduler job table and cleans up anything it minted.

Run (aihub2.1 env):
  python runner.py                  # Tier A
  python runner.py --competency     # Tier A + B
"""
import argparse
import concurrent.futures as futures
import datetime as dt
import glob
import io
import json
import os
import re
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
def _target_host():
    """Host of the app under test, from the suite-wide REGP_BASE convention."""
    base = os.environ.get("REGP_BASE", "")
    h = re.sub(r"^https?://", "", base).split(":")[0].split("/")[0]
    return h if h and h not in ("localhost", "127.0.0.1") else None


REMOTE_HOST = _target_host()
# An installed box is a DIFFERENT environment: its history lives in
# results_history/host_<ip>/ and is never diffed against the dev-tree baseline.
# (The first remote run on 2026-09-02 compared the two, reported bogus
# "REGRESSIONS DETECTED", and then BECAME the baseline the next LOCAL run was
# judged against.) Same convention as pack 15.
HISTORY_DIR = (os.path.join(HERE, "results_history", f"host_{REMOTE_HOST}") if REMOTE_HOST
               else os.path.join(HERE, "results_history"))
REPORT_LATEST = os.path.join(HERE, f"REPORT_LATEST_{REMOTE_HOST}.md" if REMOTE_HOST
                             else "REPORT_LATEST.md")
TARGET_LABEL = f" (INSTALLED {REMOTE_HOST})" if REMOTE_HOST else ""
APP = os.environ.get("REGP_BASE", "http://localhost:5001")
PREFIX = "REGA-"

# The documented public surface (auth_middleware.UNPROTECTED_ENDPOINTS). Pinned
# so that quietly widening the allowlist trips a1/a2 instead of shipping.
PINNED_ALLOWLIST = {
    "login", "logout", "home", "index", "landing", "static",
    "environments.static", "initial_setup.setup_page",
    "initial_setup.process_setup", "initial_setup.setup_status", "api_check",
}

# Never probe these, even with GET — names that imply a state change.
DANGEROUS_PATH = re.compile(
    r"(delete|remove|purge|reset|restart|shutdown|stop|kill|drop|wipe|clear"
    r"|logout|revoke|uninstall|rollback|truncate)", re.I)

# How many parameterless GET routes answered anonymously when this pack was
# written. A RATCHET: the check fails if it grows. Lower it when you fix some.
SWEEP_BASELINE = 29         # measured 2026-08-02 on 0dcdc24; may fall, never rise
                            # (a first run said 28 with errors=1 - the timed-out
                            #  route resolved on retry and IS reachable)


def log(m):
    print(f"[authz] {m}", flush=True)


def now_stamp():
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _hidden(text):
    h = dict(re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', text))
    h.update(dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"', text)))
    return h


def login_as(base, username, password):
    s = requests.Session()
    last = None
    for attempt in range(1, 4):
        # Retry a CONNECT failure (a LAN box occasionally drops one); a rejected
        # login is returned as (s, False) exactly as before.
        try:
            r = s.get(f"{base}/login", timeout=20)
            d = {"username": username, "password": password, "submit": "Login"}
            d.update(_hidden(r.text))
            r = s.post(f"{base}/login", data=d, allow_redirects=True, timeout=30)
            return s, ("/login" not in r.url)
        except requests.ConnectionError as e:
            last = e
            time.sleep(3 * attempt)
    raise RuntimeError(f"cannot reach {base}: {last}")


class App:
    def __init__(self):
        self.base = APP
        self.s, ok = login_as(self.base, "admin", "admin")
        if not ok:
            raise RuntimeError("admin login failed")

    def get(self, p, **kw):
        return self.s.get(f"{self.base}{p}", timeout=kw.pop("timeout", 30), **kw)

    def post(self, p, payload=None, **kw):
        return self.s.post(f"{self.base}{p}", json=payload, timeout=kw.pop("timeout", 60), **kw)

    def delete(self, p, **kw):
        return self.s.delete(f"{self.base}{p}", timeout=kw.pop("timeout", 30), **kw)

    @staticmethod
    def j(r):
        try:
            b = r.json()
        except Exception:
            return None
        if isinstance(b, str):
            try:
                b = json.loads(b)
            except Exception:
                return b
        return b

    def rows(self, path):
        """Normalize the app's THREE list shapes into a list of dicts.

        /get/users and /get/connections return a JSON *string* holding a list;
        /get/agents returns {'data': [...], 'status': ...}. Iterating the raw
        body gave dict KEYS (strings) and blew up b2/b5 with
        "'str' object has no attribute 'get'" on the first run."""
        b = self.j(self.get(path))
        if isinstance(b, dict):
            for k in ("data", "rows", "items", "agents", "users", "connections", "jobs"):
                if isinstance(b.get(k), list):
                    b = b[k]
                    break
        if not isinstance(b, list):
            return []
        return [x for x in b if isinstance(x, dict)]

    # ---- probe users -------------------------------------------------------
    def make_user(self, uname, role, pw="RegaTemp!2026"):
        self.post("/add/user", {"user_id": 0, "user_name": uname,
                                "name": f"REGA {uname}", "email": f"{uname}@example.com",
                                "password": pw, "role": role, "phone": ""})
        users = self.rows("/get/users")
        uid = next((u.get("id") for u in users
                    if (u.get("user_name") or "") == uname), None)
        return uid, pw

    def drop_user(self, uid):
        if uid:
            try:
                self.post("/delete/user", {"user_id": uid})
            except Exception:
                pass

    # ---- probe agents ------------------------------------------------------
    # The identifying field is agent_DESCRIPTION, not agent_name, and the new id
    # can come back as agent_id, id, or a bare numeric `message`. Getting this
    # wrong made b2 skip and made b5 under-report (it read a payload rejection
    # as "role-1 was blocked").
    AGENT_PAYLOAD = {"agent_objective": "pack-18 probe agent", "agent_enabled": True,
                     "tool_names": [], "core_tool_names": []}

    def make_agent(self, session, label):
        payload = dict(self.AGENT_PAYLOAD, agent_id=0, agent_description=label)
        r = (session or self.s).post(f"{self.base}/add/agent", json=payload, timeout=30)
        body = self.j(r) or {}
        aid = body.get("agent_id") or body.get("id")
        if not aid and str(body.get("message", "")).strip().isdigit():
            aid = int(body["message"])
        if not aid:
            aid = next((a.get("agent_id") or a.get("id") for a in self.rows("/get/agents")
                        if (a.get("agent_description") or "") == label), None)
        return r, aid

    def drop_agent(self, aid):
        if aid:
            try:
                self.post("/delete/agent", {"agent_id": aid})
            except Exception:
                pass

    def scheduler_job_ids(self):
        b = self.j(self.get("/api/scheduler/jobs"))
        rows = b if isinstance(b, list) else ((b or {}).get("jobs") or [])
        return {str(x.get("id")) for x in rows}

    def drop_scheduler_jobs(self, ids):
        for i in ids:
            try:
                self.delete(f"/api/scheduler/jobs/{i}")
            except Exception:
                pass


def discover_get_routes():
    """Parameterless GET routes declared anywhere in the app source."""
    pat = re.compile(r"@(?:app|\w+_bp)\.route\(\s*['\"]([^'\"]+)['\"](.*)")
    out = set()
    for f in glob.glob(os.path.join(REPO, "*.py")) + glob.glob(os.path.join(REPO, "*", "*.py")):
        base = os.path.basename(f)
        if base.startswith("test") or f"{os.sep}test" in f:
            continue
        try:
            src = io.open(f, encoding="utf-8").read()
        except Exception:
            continue
        for m in pat.finditer(src):
            path, rest = m.group(1), m.group(2)
            mm = re.search(r"methods\s*=\s*\[([^\]]*)\]", rest)
            meths = ([x.strip().strip("'\"").upper() for x in mm.group(1).split(",")]
                     if mm else ["GET"])
            if "GET" in meths and "<" not in path and path.startswith("/"):
                out.add(path)
    return sorted(out)


def anon_reachable(path, timeout=12):
    """True if an anonymous GET gets real content (not a login gate)."""
    try:
        r = requests.get(f"{APP}{path}", timeout=timeout, allow_redirects=False)
    except Exception as e:
        return None, f"ERR {type(e).__name__}"
    code = r.status_code
    if code in (301, 302, 303, 307, 308):
        return False, f"{code}->{(r.headers.get('Location') or '')[:40]}"
    if code in (401, 403):
        return False, str(code)
    if code == 200:
        body = (r.text or "")[:4000].lower()
        # A 200 that is really the login page is still a gate.
        if 'name="password"' in body or "please log in" in body:
            return False, "200(login page)"
        return True, f"200 {len(r.text)}b"
    return False, str(code)


CHECKS = []


def check(id, title, competency=False, xfail=None):
    def deco(fn):
        CHECKS.append({"id": id, "title": title, "fn": fn,
                       "competency": competency, "xfail": xfail})
        return fn
    return deco


# ================================================================== TIER A

@check("a1_global_auth_middleware_wired",
       "the global auth middleware is actually INSTALLED on the running app",
       xfail="FOUND 2026-08-02: auth_middleware.py implements a correct global "
             "before_request auth gate and has a passing unit-test suite, but "
             "init_auth_middleware() is never called - the string appears in no "
             "module outside auth_middleware.py itself. app.py's only "
             "before_request sets user-tracking context and checks nothing. So "
             "per-route decorators are the ONLY protection and ~349 of ~929 "
             "routes carry none. The unit tests pass because they build their own "
             "Flask app; nothing tested the WIRING. OWNER DECISION PENDING.")
def a1(ctx):
    """Static + behavioural. The static half explains it, the behavioural half
    proves it on the live process: an undecorated, non-allowlisted endpoint must
    not answer an anonymous caller."""
    src = io.open(os.path.join(REPO, "auth_middleware.py"), encoding="utf-8").read()
    module_exists = "def require_login_middleware" in src
    callers = []
    for f in glob.glob(os.path.join(REPO, "*.py")) + glob.glob(os.path.join(REPO, "*", "*.py")):
        if os.path.basename(f) == "auth_middleware.py" or "test" in os.path.basename(f):
            continue
        try:
            if "init_auth_middleware" in io.open(f, encoding="utf-8").read():
                callers.append(os.path.basename(f))
        except Exception:
            pass
    # Behavioural: /api/scheduler/jobs has no decorator and is not allowlisted.
    reach, ev = anon_reachable("/api/scheduler/jobs")
    wired = bool(callers) and not reach
    return wired, (f"middleware-module-present={module_exists}, "
                   f"init_auth_middleware called by={callers or 'NOBODY'}, "
                   f"anon GET /api/scheduler/jobs -> {ev} (reachable={reach})")


@check("a2_allowlist_contract", "the public (unauthenticated) allowlist has not grown")
def a2(ctx):
    src = io.open(os.path.join(REPO, "auth_middleware.py"), encoding="utf-8").read()
    blk = src[src.index("UNPROTECTED_ENDPOINTS = {"):]
    blk = blk[:blk.index("}")]
    found = set(re.findall(r"'([\w.]+)'", blk))
    added = sorted(found - PINNED_ALLOWLIST)
    removed = sorted(PINNED_ALLOWLIST - found)
    return (not added), (f"allowlist={len(found)} (pinned {len(PINNED_ALLOWLIST)}); "
                         f"ADDED={added or 'none'}; removed={removed or 'none'}")


@check("a3_dry_run_disabled", "auth enforcement is not in log-only dry-run mode",
       xfail="FOUND 2026-08-02: AUTH_MIDDLEWARE_DRY_RUN=true at MACHINE scope on "
             "this box, so every process inherits it. Compounds a1 - even if "
             "init_auth_middleware() were wired up tomorrow it would come up in "
             "log-only mode and still block nothing. Remedy is one command: "
             "setx /M AUTH_MIDDLEWARE_DRY_RUN false (or remove it). Means auth "
             "enforcement has never actually run on this machine. OWNER DECISION PENDING.")
def a3(ctx):
    env = os.environ.get("AUTH_MIDDLEWARE_DRY_RUN")
    dotenv = ""
    p = os.path.join(REPO, ".env")
    if os.path.exists(p):
        for ln in io.open(p, encoding="utf-8", errors="replace"):
            if ln.strip().startswith("AUTH_MIDDLEWARE_DRY_RUN"):
                dotenv = ln.strip()
    on = str(env or "").lower() == "true" or "true" in dotenv.lower()
    return (not on), f"env={env!r}, .env={dotenv or 'unset'} (must not be true)"


@check("a4_login_rejects_bad_password", "a wrong password does not authenticate")
def a4(ctx):
    s, ok = login_as(APP, "admin", "definitely-not-the-password")
    return (not ok), f"authenticated-with-wrong-password={ok} (must be False)"


@check("a5_anonymous_browser_redirects", "an anonymous browser hit on a protected page gates")
def a5(ctx):
    r = requests.get(f"{APP}/users", timeout=20, allow_redirects=True)
    gated = "/login" in r.url or "User Management" not in r.text
    return gated, f"landed={r.url[:70]}, http={r.status_code}"


@check("a6_anonymous_api_401", "an anonymous hit on a GUARDED api route returns 401/403")
def a6(ctx):
    # This route carries @api_key_or_session_required(min_role=2) — the pattern
    # that WORKS. It is the control for a1.
    r = requests.post(f"{APP}/api/scheduler/jobs/1/types/workflow/schedules",
                      json={"type": "interval", "interval_minutes": 5}, timeout=20,
                      allow_redirects=False)
    return (r.status_code in (401, 403)), f"http={r.status_code} (want 401/403)"


@check("a7_session_cookie_flags", "the session cookie is HttpOnly (and SameSite set)")
def a7(ctx):
    s = requests.Session()
    r = s.get(f"{APP}/login", timeout=20)
    d = {"username": "admin", "password": "admin", "submit": "Login"}
    d.update(_hidden(r.text))
    r = s.post(f"{APP}/login", data=d, allow_redirects=True, timeout=30)
    raw = "; ".join(v for k, v in r.raw.headers.items() if k.lower() == "set-cookie") \
        if hasattr(r, "raw") else ""
    hist = [h for h in ([*r.history, r])]
    setc = " | ".join(x.headers.get("Set-Cookie", "") for x in hist)
    blob = (raw + " " + setc).lower()
    httponly = "httponly" in blob
    samesite = "samesite" in blob
    return httponly, f"HttpOnly={httponly}, SameSite={samesite}, hdr={setc[:90]!r}"


@check("a8_logout_invalidates_session", "after logout the same session can no longer read data")
def a8(ctx):
    s, ok = login_as(APP, "admin", "admin")
    if not ok:
        return False, "could not log in"
    before = s.get(f"{APP}/get/users", timeout=20)
    s.get(f"{APP}/logout", timeout=20, allow_redirects=True)
    after = s.get(f"{APP}/get/users", timeout=20, allow_redirects=False)
    still = after.status_code == 200 and "user_name" in (after.text or "")
    return (not still), (f"pre-logout http={before.status_code}, "
                         f"post-logout http={after.status_code}, still-readable={still}")


@check("a9_role1_blocked_from_admin", "a role-1 user is blocked from admin surfaces")
def a9(ctx):
    app = ctx["app"]
    uname = PREFIX.lower() + "role1"
    uid, pw = app.make_user(uname, 1)
    try:
        if not uid:
            return None, "SKIP: could not create role-1 probe user"
        s, ok = login_as(APP, uname, pw)
        if not ok:
            return False, "role-1 user could not log in"
        blocked = {}
        pr = s.get(f"{APP}/users", timeout=20, allow_redirects=True)
        blocked["users_page"] = "/login" in pr.url or "User Management" not in pr.text
        wr = s.post(f"{APP}/save/workflow",
                    json={"filename": f"{PREFIX}probe.json",
                          "workflow": {"nodes": [], "connections": []}}, timeout=20)
        blocked["save_workflow"] = wr.status_code in (302, 401, 403)
        ar = s.post(f"{APP}/automations/api/create",
                    json={"name": f"{PREFIX}probe", "provision_environment": False},
                    timeout=20)
        blocked["automations_create"] = ar.status_code in (302, 401, 403)
        return all(blocked.values()), f"blocked={blocked}"
    finally:
        app.drop_user(uid)


@check("a10_bad_api_key_rejected", "a bogus API key does not authenticate")
def a10(ctx):
    r = requests.post(f"{APP}/api/scheduler/jobs/1/types/workflow/schedules",
                      json={"type": "interval", "interval_minutes": 5},
                      headers={"X-API-Key": "not-a-real-key-" + "x" * 24},
                      timeout=20, allow_redirects=False)
    return (r.status_code in (401, 403)), f"http={r.status_code} (want 401/403)"


@check("a11_anonymous_get_sweep_ratchet",
       "THE SWEEP: the count of anonymously-reachable GET routes has not GROWN")
def a11(ctx):
    """A RATCHET, not a clean bill of health. PASS means "no NEW doors opened
    since SWEEP_BASELINE" - today that baseline is 29 open doors, which is the
    finding a1/b6 own. Lower SWEEP_BASELINE as they get closed; the check then
    holds the new floor. Deliberately not an XFAIL: xfail would flip to XPASS
    the moment the ratchet held, which reads as "fixed" when nothing was."""
    app = ctx["app"]
    routes = [p for p in discover_get_routes() if not DANGEROUS_PATH.search(p)]
    before_jobs = app.scheduler_job_ids()
    reachable, errors = [], 0

    def probe(p):
        ok, ev = anon_reachable(p)
        return p, ok, ev

    with futures.ThreadPoolExecutor(max_workers=8) as ex:
        for p, ok, ev in ex.map(probe, routes):
            if ok is None:
                errors += 1
            elif ok:
                reachable.append((p, ev))

    # Some GETs write (pack 17 s13) — clean up anything the sweep minted.
    minted = app.scheduler_job_ids() - before_jobs
    app.drop_scheduler_jobs(minted)

    reachable.sort()
    ctx["_sweep"] = reachable
    n = len(reachable)
    pinned = SWEEP_BASELINE
    ok = (n == 0) if pinned is None else (n <= pinned)
    top = ", ".join(p for p, _ in reachable[:6])
    return ok, (f"probed={len(routes)}, ANONYMOUSLY REACHABLE={n} vs pinned {pinned} "
                f"(ratchet: must not grow - PASS is NOT 'no problem'), errors={errors}, "
                f"cleaned-up-minted-rows={len(minted)}; first: {top}")


# ================================================================== TIER B

@check("b1_role1_cannot_escalate_own_role",
       "a role-1 user cannot raise its own role", competency=True)
def b1(ctx):
    app = ctx["app"]
    uname = PREFIX.lower() + "esc"
    uid, pw = app.make_user(uname, 1)
    try:
        if not uid:
            return None, "SKIP: could not create role-1 probe user"
        s, ok = login_as(APP, uname, pw)
        if not ok:
            return False, "probe user could not log in"
        s.post(f"{APP}/add/user", json={"user_id": uid, "user_name": uname,
                                        "name": "REGA esc", "email": f"{uname}@example.com",
                                        "role": 3, "phone": ""}, timeout=20)
        users = app.rows("/get/users")
        row = next((u for u in users if (u.get("user_name") or "") == uname), {})
        now_role = row.get("role")
        return (str(now_role) == "1"), f"role after self-escalation attempt={now_role} (want 1)"
    finally:
        app.drop_user(uid)


@check("b2_no_horizontal_agent_access",
       "user A cannot read user B's agent through the API", competency=True)
def b2(ctx):
    app = ctx["app"]
    ua, ub = PREFIX.lower() + "ha", PREFIX.lower() + "hb"
    uida, pwa = app.make_user(ua, 2)
    uidb, pwb = app.make_user(ub, 2)
    aid = None
    try:
        if not (uida and uidb):
            return None, "SKIP: could not create both probe users"
        sb, okb = login_as(APP, ub, pwb)
        sa, oka = login_as(APP, ua, pwa)
        if not (oka and okb):
            return None, f"SKIP: login a={oka} b={okb}"
        label = PREFIX + "b-owned-secret"
        r, aid = app.make_agent(sb, label)
        if not aid:
            return None, f"SKIP: could not create B's agent (http={r.status_code})"
        ra = sa.get(f"{APP}/get/agent/{aid}", timeout=20, allow_redirects=False)
        leaked = ra.status_code == 200 and label in (ra.text or "")
        return (not leaked), (f"A reading B's agent {aid} -> http={ra.status_code}, "
                              f"leaked={leaked}")
    finally:
        app.drop_agent(aid)
        app.drop_user(uida)
        app.drop_user(uidb)


@check("b3_anonymous_write_actually_persists",
       "does an unauthenticated write really change state, or just return 200?",
       competency=True,
       xfail="FOUND 2026-08-02: consequence of a1. An anonymous POST to "
             "/api/scheduler/jobs creates a REAL, listable ScheduledJobs row - the "
             "200 is not cosmetic. This is the check that separates 'theoretical "
             "gap' from 'exploitable'. OWNER DECISION PENDING.")
def b3(ctx):
    app = ctx["app"]
    name = PREFIX + "anon-persist"
    anon = requests.Session()
    jid = None
    try:
        r = anon.post(f"{APP}/api/scheduler/jobs",
                      json={"name": name, "type": "workflow", "target_id": 1}, timeout=30)
        jid = (App.j(r) or {}).get("id")
        # Read it back with an AUTHENTICATED session — proof it really persisted.
        rows = app.rows("/api/scheduler/jobs")
        persisted = any((x.get("name") or "") == name for x in rows)
        return (not persisted), (f"anon POST http={r.status_code}, id={jid}, "
                                 f"visible to an authenticated reader={persisted}")
    finally:
        app.drop_scheduler_jobs([jid] if jid else [])


@check("b4_unauth_sensitive_reads", "sensitive collections are not readable anonymously",
       competency=True,
       xfail="FOUND 2026-08-02: /get/users and /get/connections correctly 401 and "
             "/get/agents 302s - their decorators work. But /api/scheduler/jobs "
             "(~31KB, every scheduled job) and /api/workflow/approvals (~300KB, the "
             "whole pending-approval queue) return real records to an anonymous "
             "caller. Quantifies pack-15 sec_approvals_get_unauth. OWNER DECISION PENDING.")
def b4(ctx):
    """Reachability alone is not the point — does the body contain real records?"""
    probes = {
        "/get/users": "user_name",
        "/get/connections": "server",
        "/get/agents": "agent_name",
        "/api/scheduler/jobs": "target_id",
        # The biggest single leak found by the a11 sweep: ~300KB of pending
        # approval records to an anonymous caller.
        "/api/workflow/approvals": "approval",
    }
    leaked = {}
    for path, marker in probes.items():
        try:
            r = requests.get(f"{APP}{path}", timeout=20, allow_redirects=False)
            body = r.text or ""
            leaked[path] = (r.status_code == 200 and marker in body, r.status_code,
                            len(body))
        except Exception as e:
            leaked[path] = (None, type(e).__name__, 0)
    bad = [p for p, (hit, _, _) in leaked.items() if hit]
    return (not bad), ("; ".join(f"{p}={c}/{n}b/leak={hit}"
                                 for p, (hit, c, n) in leaked.items())
                       + f"; LEAKING={len(bad)}")


@check("b5_role1_write_sweep",
       "a role-1 user cannot perform privileged writes", competency=True,
       xfail="KNOWN (pack 15 sec_role1_can_create_agents): role-1 can POST "
             "/add/agent and the agent is really created. Re-measured here across "
             "a wider set of writes to size the blast radius. OWNER DECISION PENDING.")
def b5(ctx):
    app = ctx["app"]
    uname = PREFIX.lower() + "wsweep"
    uid, pw = app.make_user(uname, 1)
    created = []
    try:
        if not uid:
            return None, "SKIP: could not create role-1 probe user"
        s, ok = login_as(APP, uname, pw)
        if not ok:
            return False, "probe user could not log in"
        results = {}
        r, made = app.make_agent(s, PREFIX + "r1agent")
        if made:
            created.append(("agent", made))
        results["add_agent"] = (r.status_code, made is not None)

        r = s.post(f"{APP}/api/scheduler/jobs",
                   json={"name": PREFIX + "r1job", "type": "workflow", "target_id": 1},
                   timeout=30)
        jid = (App.j(r) or {}).get("id")
        if jid:
            created.append(("job", jid))
        results["scheduler_job"] = (r.status_code, jid is not None)

        r = s.post(f"{APP}/add/connection", json={"connection_id": 0,
                                                  "connection_name": PREFIX + "r1conn",
                                                  "server": "10.0.0.6", "database": "ERPDB",
                                                  "username": "ai_user", "password": "x"},
                   timeout=30)
        results["add_connection"] = (r.status_code, None)

        succeeded = [k for k, (_, made) in results.items() if made]
        return (not succeeded), ("; ".join(f"{k}=http{c}/created={m}"
                                           for k, (c, m) in results.items())
                                 + f"; PRIVILEGED WRITES THAT LANDED={len(succeeded)}")
    finally:
        for kind, ident in created:
            if kind == "agent":
                app.drop_agent(ident)
            else:
                app.drop_scheduler_jobs([ident])
        for c in app.rows("/get/connections"):
            if str(c.get("connection_name") or "").startswith(PREFIX):
                try:
                    app.post("/delete/connection", {"connection_id": c.get("id")})
                except Exception:
                    pass
        app.drop_user(uid)


@check("b6_sweep_reachable_are_harmless",
       "every anonymously-reachable route is a genuinely public page, not data",
       competency=True,
       xfail="FOUND 2026-08-02: of the routes a11 finds reachable, several are not "
             "public pages - /admin/caution-settings renders a full ADMIN page (~25KB) "
             "and /api/caution/user exposes per-user context. Also reachable: four "
             "/test* debug routes that should not exist in a shipped build. "
             "OWNER DECISION PENDING.")
def b6(ctx):
    """a11 counts doors. This asks whether anything behind them is sensitive —
    a reachable marketing page is fine, a reachable data grid is not."""
    reachable = ctx.get("_sweep")
    if reachable is None:
        return None, "SKIP: run a11 first (it populates the sweep)"
    SENSITIVE = re.compile(r"(user|connection|secret|credential|api[_-]?key|agent|token"
                           r"|password|admin|setting|schedul)", re.I)
    flagged = [p for p, _ in reachable if SENSITIVE.search(p)]
    return (not flagged), (f"reachable={len(reachable)}, sensitive-looking={len(flagged)}: "
                           + ", ".join(flagged[:10]))


# ------------------------------------------------------------------ engine

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--competency", action="store_true")
    ap.add_argument("--only")
    args = ap.parse_args()

    stamp = now_stamp()
    ctx = {"app": App()}
    results = []
    for spec in CHECKS:
        cid = spec["id"]
        if args.only and args.only not in cid:
            continue
        if spec["competency"] and not args.competency:
            results.append({"id": cid, "status": "SKIP",
                            "evidence": "competency tier (run with --competency)"})
            continue
        t0 = time.time()
        try:
            ok, ev = spec["fn"](ctx)
            if ok is None:
                st = "SKIP"
            elif spec["xfail"]:
                st = "XPASS" if ok else "XFAIL"
            else:
                st = "PASS" if ok else "FAIL"
            results.append({"id": cid, "status": st, "evidence": ev,
                            "duration_s": round(time.time() - t0, 1)})
            log(f"{st:6} {cid} ({round(time.time()-t0,1)}s) - {str(ev)[:150]}")
        except Exception as e:
            results.append({"id": cid, "status": "ERROR", "evidence": f"runner error: {e}"})
            log(f"ERROR  {cid} - {e}")

    os.makedirs(HISTORY_DIR, exist_ok=True)
    files = sorted(glob.glob(os.path.join(HISTORY_DIR, "results_*.json")))
    baseline = json.load(io.open(files[-1], encoding="utf-8")) if files else None
    prev = {r["id"]: r["status"] for r in (baseline or {}).get("results", [])}
    regressions = [(r["id"], prev.get(r["id"]), r["status"]) for r in results
                   if prev.get(r["id"]) == "PASS" and r["status"] in ("FAIL", "ERROR")]

    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    verdict = ("REGRESSIONS DETECTED" if regressions else
               ("FAILURES (no baseline regression)"
                if any(r["status"] in ("FAIL", "ERROR") for r in results) else "CLEAN"))

    lines = [f"# AuthZ Matrix - {stamp}{TARGET_LABEL}", "",
             f"- Tier: {'A+B' if args.competency else 'A'} | Baseline: "
             f"`{os.path.basename(files[-1]) if files else 'none'}`", "",
             f"## Verdict: **{verdict}** - "
             + " / ".join(f"{v} {k}" for k, v in sorted(counts.items())), ""]
    if regressions:
        lines += ["## REGRESSIONS", "", "| check | was | now |", "|---|---|---|"]
        lines += [f"| {c} | {w} | **{n}** |" for c, w, n in regressions]
        lines.append("")
    lines += ["## Matrix", "", "| check | status | evidence |", "|---|---|---|"]
    for r in results:
        ev = str(r.get("evidence") or "").replace("|", "\\|")
        lines.append(f"| {r['id']} | {r['status']} | {ev[:230]} |")
    if ctx.get("_sweep"):
        lines += ["", "## Anonymously reachable routes (a11)", "",
                  "| route | response |", "|---|---|"]
        lines += [f"| `{p}` | {ev} |" for p, ev in ctx["_sweep"]]
    report = "\n".join(lines)

    json.dump({"stamp": stamp, "tier": "AB" if args.competency else "A",
               "results": results, "sweep": ctx.get("_sweep")},
              io.open(os.path.join(HISTORY_DIR, f"results_{stamp}.json"), "w",
                      encoding="utf-8"), indent=1, default=str)
    io.open(os.path.join(HISTORY_DIR, f"REPORT_{stamp}.md"), "w",
            encoding="utf-8").write(report)
    io.open(REPORT_LATEST, "w", encoding="utf-8").write(report)
    print("\n" + report.split("## Matrix")[0])
    return 2 if regressions else (1 if any(r["status"] in ("FAIL", "ERROR")
                                           for r in results) else 0)


if __name__ == "__main__":
    sys.exit(main())
