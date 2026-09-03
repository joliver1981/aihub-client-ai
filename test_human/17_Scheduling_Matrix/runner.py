"""
Scheduling & Jobs Matrix — pack 17.

WHY THIS AREA: scheduling fails INVISIBLY. Nobody notices a job that did not
fire until the business impact lands, which makes it the worst area to leave at
one shallow check ("does the backend endpoint answer?"). It also has a
documented history of exactly the silent kind:
  - the portal_workflow job type had to be added to the scheduler (f84f5c8),
  - AIHUB-0065: the Schedule button posted a slug STRING into an INT TargetId,
  - AIHUB-0061: scheduled automations never appeared in the CC panel.
Six job types (document, agent, workflow, command_center, portal_workflow,
automation) share one scheduler, so a type mismatch hides easily.

TIER A (regression, default): deterministic contract checks - job CRUD, the
integer-TargetId guard, schedule creation/retrieval for interval+cron, listing
by job and by type, cleanup on delete, and the route-auth contract. No waiting.

TIER B (competency, --competency): the questions that actually matter -
DOES A SCHEDULE FIRE? does a DISABLED schedule stay silent? does deleting the
job stop it firing? Graded on real execution rows, never on the schedule record
alone. These wait on wall-clock, so they are opt-in.

>>> ROUTE CONTRACT (verified 2026-08-01 by reading scheduler_routes.py) <<<
The scheduler blueprint exposes TWO parallel families and they are NOT aliases:

  /jobs/<id>/types/<type>/schedules   <id> is the *TARGET* id (workflow id,
      agent id, ...). The route FINDS-OR-CREATES the ScheduledJobs row for
      (type, target) and hangs the schedule off it. Guarded (min_role=2).
      This is the real path, and the one this pack uses.

  /jobs/<id>/schedules                LEGACY, DOCUMENT-ONLY. It reads <id> as a
      *document* target id no matter what you meant, and mints a phantom
      `document` job for any id you hand it. Unauthenticated. Using it for a
      workflow id silently attaches the schedule to a phantom document job, so
      the workflow never fires and every read-back still looks correct.
      An earlier draft of this pack fell into exactly that trap.

Run (aihub2.1 env):
  python runner.py                  # Tier A
  python runner.py --competency     # Tier A + B (adds ~10 min of waiting)
"""
import argparse
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
PREFIX = "REGS-"          # everything this runner creates is named PREFIX + id

# The scheduler implements exactly these (job_scheduler.py self.job_types).
EXPECTED_JOB_TYPES = {"document", "agent", "workflow", "command_center",
                      "portal_workflow", "automation"}

# Synthetic target ids for auth probes — far outside any real id range so a
# find-or-create can never collide with a real job.
AUTH_PROBE_TARGET = 987650


def log(m):
    print(f"[schedreg] {m}", flush=True)


def now_stamp():
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


class App:
    def __init__(self):
        self.base = APP
        self.s = requests.Session()
        last = None
        for attempt in range(1, 4):
            try:
                r = self.s.get(f"{self.base}/login", timeout=20)
                hid = dict(re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', r.text))
                hid.update(dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"', r.text)))
                d = {"username": "admin", "password": "admin", "submit": "Login"}
                d.update(hid)
                r = self.s.post(f"{self.base}/login", data=d, allow_redirects=True, timeout=30)
                if "/login" in r.url:
                    raise RuntimeError("admin login failed")
                return
            except requests.ConnectionError as e:
                last = e
                time.sleep(3 * attempt)
        raise RuntimeError(f"cannot reach {self.base}: {last}")

    def _req(self, method, p, **kw):
        """Every request goes through here. A CONNECT failure (the socket never
        reached the server — includes ConnectTimeout) is retried; a read timeout
        is not, because the request may have been applied. 2026-09-02: c2..c6
        ERRORed on the installed box with 'Max retries exceeded ... Connection to
        10.0.0.6 timed out' while the box answered every retry — five bogus
        REGRESSIONS from one flaky connect each."""
        last = None
        for attempt in range(1, 4):
            try:
                return getattr(self.s, method)(f"{self.base}{p}", **kw)
            except requests.ConnectionError as e:
                last = e
                time.sleep(2 * attempt)
        raise last

    def get(self, p, **kw):
        return self._req("get", p, timeout=kw.pop("timeout", 30), **kw)

    def post(self, p, payload=None, **kw):
        return self._req("post", p, json=payload, timeout=kw.pop("timeout", 60), **kw)

    def put(self, p, payload=None, **kw):
        return self._req("put", p, json=payload, timeout=kw.pop("timeout", 30), **kw)

    def delete(self, p, **kw):
        return self._req("delete", p, timeout=kw.pop("timeout", 30), **kw)

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

    # ---- scheduler jobs ----------------------------------------------------
    def jobs(self):
        b = self.j(self.get("/api/scheduler/jobs"))
        return b if isinstance(b, list) else ((b or {}).get("jobs") or [])

    def make_job(self, name, jtype="workflow", target_id=1, **extra):
        payload = {"name": name, "type": jtype, "target_id": target_id,
                   "description": "pack-17 probe", "created_by": "regs"}
        payload.update(extra)
        r = self.post("/api/scheduler/jobs", payload)
        b = self.j(r) or {}
        jid = b.get("id") or b.get("job_id") or (b.get("job") or {}).get("id")
        if not jid:
            jid = next((j.get("id") for j in self.jobs()
                        if (j.get("name") or "") == name), None)
        return r, b, jid

    def drop_job(self, jid):
        if jid:
            try:
                self.delete(f"/api/scheduler/jobs/{jid}")
            except Exception:
                pass

    def job_by_name(self, name):
        return next((j for j in self.jobs() if (j.get("name") or "") == name), None)

    def job_for_target(self, job_type, target_id):
        return next((j for j in self.jobs()
                     if str(j.get("type")) == str(job_type)
                     and str(j.get("target_id")) == str(target_id)), None)

    def drop_jobs_for_target(self, job_type, target_id):
        """Remove every ScheduledJobs row pointing at this (type, target).

        The type-aware schedule route find-or-creates its own job row, so tests
        must clean up by TARGET, not by the id they happened to create.
        """
        n = 0
        if target_id is None:
            return 0
        for j in self.jobs():
            if str(j.get("type")) == str(job_type) and str(j.get("target_id")) == str(target_id):
                self.drop_job(j.get("id"))
                n += 1
        return n

    # ---- schedules (TYPE-AWARE path only — see ROUTE CONTRACT above) -------
    def add_schedule(self, target_id, job_type, payload):
        r = self.post(f"/api/scheduler/jobs/{target_id}/types/{job_type}/schedules", payload)
        return r, self.j(r)

    def schedules_for(self, target_id, job_type):
        b = self.j(self.get(f"/api/scheduler/jobs/{target_id}/types/{job_type}/schedules"))
        return b if isinstance(b, list) else ((b or {}).get("schedules") or [])

    def schedules_by_type(self, job_type):
        b = self.j(self.get(f"/api/scheduler/types/{job_type}/schedules"))
        return b if isinstance(b, list) else ((b or {}).get("schedules") or [])

    # ---- workflow helpers (for the "does it actually fire?" checks) --------
    def workflows(self):
        b = self.j(self.get("/get/workflows")) or []
        return b if isinstance(b, list) else (b.get("workflows") or [])

    def make_probe_workflow(self, name):
        nodes = [{"id": "node-0", "isStart": True, "type": "Set Variable",
                  "label": "probe", "position": {"left": "100px", "top": "100px"},
                  "config": {"variableName": "regsProbe", "valueSource": "direct",
                             "valueExpression": "REGS-FIRED",
                             "evaluateAsExpression": False, "outputPath": ""}}]
        self.post("/save/workflow", {"filename": f"{name}.json",
                                     "workflow": {"nodes": nodes, "connections": [],
                                                  "variables": {}}})
        best = None
        for w in self.workflows():
            if (w.get("workflow_name") or w.get("name") or "") == name:
                wid = w.get("id")
                if wid and (best is None or int(wid) > int(best)):
                    best = int(wid)
        return best

    def drop_workflow(self, wid):
        if wid:
            try:
                self.delete(f"/delete/workflow/{wid}")
            except Exception:
                pass

    def executions_for(self, wid):
        b = self.j(self.get(f"/api/workflow/executions?workflow_id={wid}")) or []
        rows = b if isinstance(b, list) else (b.get("executions") or b.get("data") or [])
        return [e for e in rows if str(e.get("workflow_id") or "") == str(wid)] or rows

    def cleanup_probe(self, wid, job_type="workflow"):
        self.drop_jobs_for_target(job_type, wid)
        self.drop_workflow(wid)


CHECKS = []


def check(id, title, competency=False, xfail=None, slow=False):
    def deco(fn):
        CHECKS.append({"id": id, "title": title, "fn": fn,
                       "competency": competency, "xfail": xfail, "slow": slow})
        return fn
    return deco


# ================================================================== TIER A

@check("s1_backend_healthy", "the scheduler backend reports a real backend")
def s1(ctx):
    b = ctx["app"].j(ctx["app"].get("/api/quickjob/scheduler/backend")) or {}
    ok = b.get("status") == "success" and bool(b.get("backend"))
    return ok, f"backend={b.get('backend')}, use_apscheduler={b.get('use_apscheduler')}"


@check("s2_job_type_contract", "the scheduler still implements every supported job type")
def s2(ctx):
    src = io.open(os.path.join(REPO, "job_scheduler.py"), encoding="utf-8").read()
    block = src[src.index("self.job_types = {"):src.index("}", src.index("self.job_types = {"))]
    found = set(re.findall(r"'([a-z_]+)':", block))
    missing = sorted(EXPECTED_JOB_TYPES - found)
    extra = sorted(found - EXPECTED_JOB_TYPES)
    return (not missing), f"implemented={sorted(found)}; missing={missing or 'none'}; new={extra or 'none'}"


@check("s3_job_crud", "create a scheduler job -> listed with the right type/target -> delete")
def s3(ctx):
    app = ctx["app"]
    name = PREFIX + "job-crud"
    old = app.job_by_name(name)
    app.drop_job(old.get("id") if old else None)
    r, b, jid = app.make_job(name, "workflow", 1)
    try:
        row = app.job_by_name(name) or {}
        ok = bool(jid) and str(row.get("type") or row.get("job_type")) == "workflow" \
            and str(row.get("target_id")) == "1"
        return ok, f"http={r.status_code}, id={jid}, type={row.get('type')}, target={row.get('target_id')}"
    finally:
        app.drop_job(jid)


@check("s4_target_id_contract",
       "AIHUB-0065 GUARD: a non-numeric target is coerced for slug-carrying types "
       "and cleanly rejected for the rest - never a raw SQL 500")
def s4(ctx):
    """CORRECTED 2026-08-01: my first version asserted that a slug must be
    REJECTED for portal_workflow. The AIHUB-0065 fix deliberately does the
    opposite - portal_workflow and automation carry their real target in
    `parameters`, so a non-numeric target is coerced to the 0 placeholder;
    only OTHER job types reject. Both halves are asserted here."""
    app = ctx["app"]
    coerced_name, rejected_name = PREFIX + "slug-coerce", PREFIX + "slug-reject"
    for n in (coerced_name, rejected_name):
        row = app.job_by_name(n)
        app.drop_job((row or {}).get("id"))
    r1, b1, jid1 = app.make_job(coerced_name, "portal_workflow", "some_workflow_slug")
    r2, b2, _ = app.make_job(rejected_name, "workflow", "some_workflow_slug")
    created2 = app.job_by_name(rejected_name)
    try:
        row1 = app.job_by_name(coerced_name) or {}
        coerced_ok = (r1.status_code < 400 and str(row1.get("target_id")) == "0")
        rejected_ok = (r2.status_code == 400
                       and "integer" in str((b2 or {}).get("error", "")).lower()
                       and created2 is None)
        no_sql_leak = "conversion failed" not in json.dumps(b1 or {}).lower()
        return (coerced_ok and rejected_ok and no_sql_leak), (
            f"portal_workflow slug -> http={r1.status_code} target_id={row1.get('target_id')} "
            f"(want 0); workflow slug -> http={r2.status_code} rejected={created2 is None}; "
            f"no-raw-sql-error={no_sql_leak}")
    finally:
        app.drop_job(jid1)
        app.drop_job((created2 or {}).get("id"))


@check("s5_missing_fields_rejected", "a job with missing required fields is rejected")
def s5(ctx):
    r = ctx["app"].post("/api/scheduler/jobs", {"description": "no name/type/target"})
    b = ctx["app"].j(r) or {}
    ok = r.status_code >= 400 and "required" in str(b.get("error", "")).lower()
    return ok, f"http={r.status_code}, error={str(b.get('error'))[:80]!r}"


@check("s6_interval_schedule_persists",
       "an interval schedule attaches to the REAL workflow job and reads back")
def s6(ctx):
    app = ctx["app"]
    wid = app.make_probe_workflow(PREFIX + "interval-wf")
    try:
        if not wid:
            return None, "SKIP: could not create probe workflow"
        rs, bs = app.add_schedule(wid, "workflow",
                                  {"type": "interval", "interval_minutes": 30,
                                   "is_active": True})
        rows = app.schedules_for(wid, "workflow")
        job = app.job_for_target("workflow", wid)
        # The schedule must hang off a job of type `workflow` targeting OUR
        # workflow — not a phantom document job (the legacy-route trap).
        ok = rs.status_code < 400 and len(rows) >= 1 and job is not None
        return ok, (f"schedule-http={rs.status_code}, schedules={len(rows)}, "
                    f"job-type={(job or {}).get('type')}, target={(job or {}).get('target_id')} "
                    f"(want workflow/{wid})")
    finally:
        app.cleanup_probe(wid)


@check("s7_cron_schedule_persists", "a cron schedule is created and retrievable")
def s7(ctx):
    app = ctx["app"]
    wid = app.make_probe_workflow(PREFIX + "cron-wf")
    try:
        if not wid:
            return None, "SKIP: could not create probe workflow"
        # The cron branch reads schedule_data['cron_expression'] and validates it
        # with is_valid_cron; hour/minute keys are silently NOT the contract.
        rs, bs = app.add_schedule(wid, "workflow",
                                  {"type": "cron", "cron_expression": "0 6 * * *",
                                   "is_active": True})
        rows = app.schedules_for(wid, "workflow")
        ok = rs.status_code < 400 and len(rows) >= 1
        return ok, f"schedule-http={rs.status_code}, schedules-on-job={len(rows)}"
    finally:
        app.cleanup_probe(wid)


@check("s8_invalid_schedule_type_rejected", "an unsupported schedule type is refused")
def s8(ctx):
    app = ctx["app"]
    wid = app.make_probe_workflow(PREFIX + "badsched-wf")
    try:
        if not wid:
            return None, "SKIP: could not create probe workflow"
        rs, bs = app.add_schedule(wid, "workflow", {"type": "whenever", "is_active": True})
        rows = app.schedules_for(wid, "workflow")
        ok = rs.status_code >= 400 or len(rows) == 0
        return ok, f"http={rs.status_code}, schedules-created={len(rows)} (must be 0)"
    finally:
        app.cleanup_probe(wid)


@check("s9_list_by_type", "schedules are listable by job type")
def s9(ctx):
    r = ctx["app"].get("/api/scheduler/types/workflow/schedules")
    b = ctx["app"].j(r)
    rows = b if isinstance(b, list) else ((b or {}).get("schedules") or [])
    ok = r.status_code == 200 and isinstance(rows, list)
    return ok, f"http={r.status_code}, workflow-schedules={len(rows) if isinstance(rows, list) else '?'}"


@check("s10_delete_removes_job_and_schedules",
       "deleting a scheduler job removes the job AND its schedules disappear from the listing")
def s10(ctx):
    """The DELETE route relies on an FK cascade (scheduler_routes.py:436). The
    by-type listing INNER JOINs ScheduledJobs, so a surviving orphan row would
    be invisible there — which is why the question that actually matters
    (can a deleted job still FIRE?) is answered empirically by c3, not here."""
    app = ctx["app"]
    wid = app.make_probe_workflow(PREFIX + "cleanup-wf")
    try:
        if not wid:
            return None, "SKIP: could not create probe workflow"
        app.add_schedule(wid, "workflow", {"type": "interval", "interval_minutes": 60,
                                           "is_active": True})
        had = len(app.schedules_for(wid, "workflow"))
        job = app.job_for_target("workflow", wid)
        sched_ids = {str(s.get("id")) for s in app.schedules_by_type("workflow")
                     if str(s.get("workflow_id")) == str(wid)}
        app.drop_job((job or {}).get("id"))
        gone = app.job_for_target("workflow", wid) is None
        still = {str(s.get("id")) for s in app.schedules_by_type("workflow")
                 if str(s.get("workflow_id")) == str(wid)} & sched_ids
        return (gone and not still), (f"schedules-before={had}, job-removed={gone}, "
                                      f"schedules-still-listed={len(still)} (want 0)")
    finally:
        app.cleanup_probe(wid)


@check("s11_unknown_job_type_handled", "an unknown job type does not silently create a job",
       xfail="FOUND 2026-08-01: POST /api/scheduler/jobs accepts ANY job type string "
             "(http 201). The scheduler only implements 6 types and logs 'Unsupported "
             "job type' at run time, so the row is created but can never execute - a "
             "schedule that silently never runs, which is this pack's core failure "
             "mode. OWNER DECISION PENDING.")
def s11(ctx):
    app = ctx["app"]
    name = PREFIX + "unknown-type"
    r, b, _ = app.make_job(name, "definitely_not_a_type", 1)
    row = app.job_by_name(name)
    try:
        # Either refused outright, or stored but NEVER executable — the scheduler
        # logs "Unsupported job type" and skips. Refusal is the honest outcome.
        refused = bool(r.status_code >= 400 or (isinstance(b, dict) and b.get("error")))
        return refused, (f"http={r.status_code}, row-created={row is not None}, "
                         f"error={str((b or {}).get('error'))[:60]!r}")
    finally:
        app.drop_job((row or {}).get("id"))


@check("s12_scheduler_writes_require_auth",
       "SECURITY: every scheduler mutation requires authentication",
       xfail="FOUND 2026-08-01: the scheduler blueprint carries TWO parallel route "
             "families. Every `/types/<job_type>/` variant is guarded with "
             "@api_key_or_session_required(min_role=2); every legacy variant has NO "
             "decorator at all - 6 unauthenticated writes (POST/PUT/DELETE on /jobs "
             "and /jobs/<id>/schedules[/<sid>]) plus 5 unauthenticated reads. Verified "
             "live with a credential-free session: created, renamed and deleted a "
             "scheduled job, and enumerated all 125. OWNER DECISION PENDING.")
def s12(ctx):
    """Runs with NO credentials on purpose. Everything it manages to create is
    torn down with the admin session in the finally block."""
    app = ctx["app"]
    anon = requests.Session()
    made = []
    try:
        probes = []
        r = anon.post(f"{APP}/api/scheduler/jobs",
                      json={"name": PREFIX + "anon-authprobe", "type": "workflow",
                            "target_id": 1}, timeout=30)
        jid = (App.j(r) or {}).get("id")
        if jid:
            made.append(jid)
        probes.append(("POST /jobs", r.status_code))

        if jid:
            probes.append(("PUT /jobs/<id>",
                           anon.put(f"{APP}/api/scheduler/jobs/{jid}",
                                    json={"name": PREFIX + "anon-renamed"},
                                    timeout=30).status_code))
        r = anon.post(f"{APP}/api/scheduler/jobs/{AUTH_PROBE_TARGET}/schedules",
                      json={"type": "interval", "interval_minutes": 5, "is_active": True},
                      timeout=30)
        probes.append(("POST /jobs/<id>/schedules", r.status_code))
        if jid:
            probes.append(("DELETE /jobs/<id>",
                           anon.delete(f"{APP}/api/scheduler/jobs/{jid}",
                                       timeout=30).status_code))
            if probes[-1][1] < 400:
                made.remove(jid)

        # The guarded twin, for contrast — proves the fix pattern already exists.
        guarded = anon.post(
            f"{APP}/api/scheduler/jobs/{AUTH_PROBE_TARGET}/types/workflow/schedules",
            json={"type": "interval", "interval_minutes": 5}, timeout=30).status_code

        open_writes = [name for name, code in probes if code < 400]
        ok = not open_writes
        return ok, ("anon: " + ", ".join(f"{n}={c}" for n, c in probes)
                    + f"; guarded twin /types/workflow/schedules={guarded}"
                    + f"; UNAUTH WRITES={len(open_writes)}")
    finally:
        for j in made:
            app.drop_job(j)
        app.drop_jobs_for_target("document", AUTH_PROBE_TARGET)
        app.drop_jobs_for_target("workflow", AUTH_PROBE_TARGET)


@check("s13_get_schedules_has_no_write_side_effect",
       "reading a job's schedules must not CREATE anything",
       xfail="FOUND 2026-08-01: GET /api/scheduler/jobs/<id>/schedules INSERTs a "
             "ScheduledJobs row ('Document Job <id>', type=document, target=<id>, "
             "created_by=system) whenever no document job matches - see "
             "scheduler_routes.py:464-489. It is unauthenticated, so any anonymous "
             "caller can mint scheduler rows by iterating ids; 54 such rows already "
             "exist on this box. Inert (no schedule attached => the scheduler's "
             "INNER JOIN skips them) but unbounded. OWNER DECISION PENDING.")
def s13(ctx):
    app = ctx["app"]
    target = AUTH_PROBE_TARGET + 3
    app.drop_jobs_for_target("document", target)
    before = len(app.jobs())
    r = app.get(f"/api/scheduler/jobs/{target}/schedules")
    after_rows = app.jobs()
    created = [x for x in after_rows if str(x.get("target_id")) == str(target)]
    try:
        return (not created), (f"GET http={r.status_code}, jobs {before}->{len(after_rows)}, "
                               f"rows created by a READ={len(created)}"
                               + (f" ({created[0].get('name')}, type={created[0].get('type')})"
                                  if created else ""))
    finally:
        app.drop_jobs_for_target("document", target)


# ================================================================== TIER B

def _wait_for_execution(app, wid, baseline, timeout=240, poll=10):
    """Wait for a NEW execution row to appear for this workflow."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        rows = app.executions_for(wid)
        if len(rows) > baseline:
            return rows, round(time.time() - t0, 1)
        time.sleep(poll)
    return app.executions_for(wid), round(time.time() - t0, 1)


@check("c1_schedule_actually_fires",
       "THE HEADLINE: an interval schedule actually EXECUTES the target",
       competency=True, slow=True)
def c1(ctx):
    """Graded on a real execution row, never on the schedule record — a stored
    schedule that never fires is the exact silent failure this pack exists for.

    Budget: the scheduler re-reads the DB every poll_interval (60s default,
    job_scheduler.py:74) and only then arms the 60s interval, so the honest
    worst case is ~120s. 240s gives headroom without hiding a real stall."""
    app = ctx["app"]
    wid = app.make_probe_workflow(PREFIX + "fire-wf")
    try:
        if not wid:
            return None, "SKIP: could not create probe workflow"
        before = len(app.executions_for(wid))
        rs, _ = app.add_schedule(wid, "workflow",
                                 {"type": "interval", "interval_seconds": 60,
                                  "is_active": True})
        job = app.job_for_target("workflow", wid)
        rows, waited = _wait_for_execution(app, wid, before, timeout=240)
        sched = app.schedules_for(wid, "workflow")
        nrt = (sched[0].get("next_run_time") if sched else None)
        fired = len(rows) > before
        return fired, (f"schedule-http={rs.status_code}, job={(job or {}).get('id')}"
                       f"/{(job or {}).get('type')}, next_run_time={nrt}, "
                       f"executions {before}->{len(rows)} after {waited}s (interval 60s)")
    finally:
        app.cleanup_probe(wid)


@check("c2_disabled_schedule_stays_silent",
       "an INACTIVE schedule must not execute anything", competency=True, slow=True)
def c2(ctx):
    app = ctx["app"]
    wid = app.make_probe_workflow(PREFIX + "quiet-wf")
    try:
        if not wid:
            return None, "SKIP: could not create probe workflow"
        before = len(app.executions_for(wid))
        app.add_schedule(wid, "workflow", {"type": "interval", "interval_seconds": 60,
                                           "is_active": False})
        time.sleep(180)
        after = len(app.executions_for(wid))
        return (after == before), (f"executions {before}->{after} after 180s "
                                   f"(inactive schedule must NOT fire)")
    finally:
        app.cleanup_probe(wid)


@check("c3_deleted_job_stops_firing",
       "deleting a scheduled job stops it firing (no orphaned APScheduler entry)",
       competency=True, slow=True)
def c3(ctx):
    """This is the empirical answer to 'does DELETE really cascade?' — an
    orphaned ScheduleDefinitions row would be invisible to every listing but
    would still be picked up by the scheduler's poll and keep firing."""
    app = ctx["app"]
    wid = app.make_probe_workflow(PREFIX + "orphan-wf")
    try:
        if not wid:
            return None, "SKIP: could not create probe workflow"
        app.add_schedule(wid, "workflow", {"type": "interval", "interval_seconds": 60,
                                           "is_active": True})
        job = app.job_for_target("workflow", wid)
        app.drop_job((job or {}).get("id"))     # delete BEFORE it can run
        baseline = len(app.executions_for(wid))
        time.sleep(180)
        after = len(app.executions_for(wid))
        return (after == baseline), (f"after delete, executions {baseline}->{after} "
                                     f"over 180s (must not grow)")
    finally:
        app.cleanup_probe(wid)


@check("c4_invalid_cron_rejected", "a malformed cron expression is refused honestly",
       competency=True)
def c4(ctx):
    app = ctx["app"]
    wid = app.make_probe_workflow(PREFIX + "badcron-wf")
    try:
        if not wid:
            return None, "SKIP: could not create probe workflow"
        rs, bs = app.add_schedule(wid, "workflow", {"type": "cron", "hour": 99,
                                                    "minute": 99, "is_active": True})
        rows = app.schedules_for(wid, "workflow")
        # Honest = refuse, or store something that cannot silently masquerade as
        # a valid daily schedule.
        refused = rs.status_code >= 400 or len(rows) == 0
        return refused, (f"http={rs.status_code}, schedules-created={len(rows)}; "
                         f"body={str(bs)[:90]!r}")
    finally:
        app.cleanup_probe(wid)


@check("c5_schedule_listing_consistent",
       "the same schedule is visible via the job listing and the by-type listing",
       competency=True)
def c5(ctx):
    app = ctx["app"]
    wid = app.make_probe_workflow(PREFIX + "consistency-wf")
    try:
        if not wid:
            return None, "SKIP: could not create probe workflow"
        app.add_schedule(wid, "workflow", {"type": "interval", "interval_minutes": 45,
                                           "is_active": True})
        by_job = app.schedules_for(wid, "workflow")
        seen = any(str(s.get("workflow_id")) == str(wid)
                   for s in app.schedules_by_type("workflow"))
        ok = len(by_job) >= 1 and seen
        return ok, f"by-job={len(by_job)}, visible-in-by-type-listing={seen}"
    finally:
        app.cleanup_probe(wid)


@check("c6_many_schedules_one_job", "a job accepts multiple schedules without clobbering",
       competency=True)
def c6(ctx):
    app = ctx["app"]
    wid = app.make_probe_workflow(PREFIX + "multi-wf")
    try:
        if not wid:
            return None, "SKIP: could not create probe workflow"
        for mins in (15, 30, 45):
            app.add_schedule(wid, "workflow", {"type": "interval", "interval_minutes": mins,
                                               "is_active": True})
        rows = app.schedules_for(wid, "workflow")
        return (len(rows) >= 3), f"schedules created=3, retrieved={len(rows)}"
    finally:
        app.cleanup_probe(wid)


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

    lines = [f"# Scheduling & Jobs Matrix - {stamp}{TARGET_LABEL}", "",
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
    report = "\n".join(lines)

    json.dump({"stamp": stamp, "tier": "AB" if args.competency else "A",
               "results": results},
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
