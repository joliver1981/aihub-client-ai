"""
Automations REST API — Developer+ gated, mirrors agent_environments gating.

    GET    /automations/api/list
    POST   /automations/api/create            {name, description, environment_id? | provision_environment?}
    GET    /automations/api/<id>
    DELETE /automations/api/<id>
    GET    /automations/api/<id>/code         ?version=N
    PUT    /automations/api/<id>/code         {code, manifest?}   -> saves new immutable version
    GET    /automations/api/<id>/manifest     ?version=N
    POST   /automations/api/<id>/promote      {version?}          -> default: promote latest
    POST   /automations/api/<id>/run          {inputs?, version?, dry_run?, wait?}
    GET    /automations/api/<id>/runs
    GET    /automations/api/runs/<run_id>
    GET    /automations/api/runs/<run_id>/log
    POST   /automations/api/internal/run      X-API-Key           -> scheduler seam (P1 job type)

Gating: login + role in {2,3} (Developer/Admin) + cfg.AUTOMATIONS_ENABLED.
Runs execute the PINNED version; dry-runs the latest edit. Concurrent runs
are skipped (recorded as status='skipped').
"""

import json
import logging
import os
import threading
import uuid
from functools import wraps
from typing import Dict, Optional

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

import config as cfg
from .manager import AutomationManager, validate_manifest
from .runner import AutomationRunner

logger = logging.getLogger(__name__)

automations_bp = Blueprint("automations", __name__, url_prefix="/automations")

_manager = None
_runner = None
_tables_ensured = False


def _get_manager() -> AutomationManager:
    global _manager, _tables_ensured
    if _manager is None:
        _manager = AutomationManager()
    if not _tables_ensured:
        _manager.ensure_tables()
        _tables_ensured = True
    return _manager


def _get_runner() -> AutomationRunner:
    global _runner
    if _runner is None:
        _runner = AutomationRunner(manager=_get_manager())
    return _runner


def automations_gate(f):
    """Feature flag + Developer/Admin role, same shape as agent_environments."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not getattr(cfg, "AUTOMATIONS_ENABLED", False):
            return jsonify({"error": "Automations feature is disabled"}), 403
        if not hasattr(current_user, "role") or current_user.role not in [2, 3]:
            return jsonify({"error": "Access denied — Developer role required"}), 403
        return f(*args, **kwargs)
    return decorated


# ----------------------------------------------------------------- runs UI

_RUNS_PAGE = """<!DOCTYPE html>
<html><head><title>Automations</title>
<style>
 body{font-family:Segoe UI,Arial,sans-serif;margin:24px;background:#f7f8fa;color:#1c2430}
 h1{font-size:20px} h2{font-size:16px;margin-top:20px}
 table{border-collapse:collapse;width:100%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.08)}
 th,td{padding:7px 10px;border-bottom:1px solid #e5e8ee;text-align:left;font-size:13px}
 th{background:#eef1f6} tr:hover td{background:#f4f7fb;cursor:pointer}
 .success{color:#177245;font-weight:600}.failed{color:#b3261e;font-weight:600}
 .unverified{color:#9a6700;font-weight:600}.skipped,.running{color:#5f6b7a;font-weight:600}
 pre{background:#101418;color:#d8e0ea;padding:12px;overflow:auto;max-height:420px;font-size:12px}
 .muted{color:#5f6b7a;font-size:12px}
</style></head><body>
<h1>Automations — runs</h1>
<div class="muted">Read-only view. Build and manage automations in Command Center; API under /automations/api/.</div>
<div id="autos"></div><h2 id="runsTitle" style="display:none">Runs</h2><div id="runs"></div>
<h2 id="logTitle" style="display:none">Run log</h2><pre id="log" style="display:none"></pre>
<script>
async function j(u){const r=await fetch(u);return r.json();}
function cls(s){return ['success','failed','unverified','skipped','running'].includes(s)?s:'';}
async function loadAutos(){
 const d=await j('/automations/api/list');const a=d.automations||[];
 let h='<table><tr><th>Name</th><th>Description</th><th>Latest</th><th>Live</th></tr>';
 for(const x of a){h+=`<tr onclick="loadRuns('${x.automation_id}','${x.name}')"><td>${x.name}</td><td>${x.description||''}</td><td>v${x.current_version}</td><td>${x.pinned_version?('v'+x.pinned_version):'—'}</td></tr>`;}
 document.getElementById('autos').innerHTML=h+'</table>'+(a.length?'':'<p class="muted">No automations yet.</p>');}
async function loadRuns(id,name){
 const d=await j('/automations/api/'+id+'/runs');const rs=d.runs||[];
 document.getElementById('runsTitle').style.display='block';
 document.getElementById('runsTitle').textContent='Runs — '+name;
 let h='<table><tr><th>Started</th><th>Trigger</th><th>Version</th><th>Outcome</th><th>Exit</th></tr>';
 for(const r of rs){h+=`<tr onclick="loadLog('${r.run_id}')"><td>${r.started_at||''}</td><td>${r.trigger_source}</td><td>v${r.version}</td><td class="${cls(r.status)}">${r.status}</td><td>${r.exit_code??''}</td></tr>`;}
 document.getElementById('runs').innerHTML=h+'</table>'+(rs.length?'':'<p class="muted">No runs yet.</p>');
 document.getElementById('log').style.display='none';document.getElementById('logTitle').style.display='none';}
async function loadLog(runId){
 const d=await j('/automations/api/runs/'+runId+'/log');
 document.getElementById('logTitle').style.display='block';
 const el=document.getElementById('log');el.style.display='block';
 el.textContent=d.log||d.error||'(no log)';}
loadAutos();
</script></body></html>"""


@automations_bp.route("/", methods=["GET"])
@automations_gate
def runs_ui():
    """Minimal read-only runs dashboard (P1 deliverable): automations, run
    history with honest outcomes, and per-run logs. Building/managing happens
    in Command Center."""
    return _RUNS_PAGE


# ------------------------------------------------------------------- CRUD

@automations_bp.route("/api/list", methods=["GET"])
@automations_gate
def list_automations():
    return jsonify({"automations": _get_manager().list_automations()})


@automations_bp.route("/api/create", methods=["POST"])
@automations_gate
def create_automation():
    data = request.get_json(silent=True) or {}
    name = data.get("name", "")
    description = data.get("description", "")
    environment_id = data.get("environment_id")

    ok, auto, error = _get_manager().create_automation(
        name=name, description=description,
        owner_user_id=current_user.id, environment_id=environment_id,
    )
    if not ok:
        return jsonify({"error": error}), 400

    warning = None
    if not environment_id and data.get("provision_environment", True):
        # one dedicated agent environment per automation (design decision).
        # Provisioning failure is non-fatal: the runner falls back to the
        # bundle python; the warning tells the caller honestly.
        env_id, warning = _provision_environment(auto)
        if env_id:
            _get_manager().set_environment(auto["automation_id"], env_id)
            auto = _get_manager().get_automation(auto["automation_id"])

    resp = {"automation": auto}
    if warning:
        resp["warning"] = warning
    return jsonify(resp), 201


def _provision_environment(auto):
    """Create the automation's dedicated agent environment. Returns
    (environment_id | None, warning | None)."""
    try:
        from agent_environments.environment_api import get_manager as get_env_manager
        env_manager = get_env_manager()
        env_name = f"automation-{auto['name']}"[:100]
        success, env_id, message = env_manager.create_environment(
            name=env_name,
            description=f"Dedicated environment for automation '{auto['name']}'",
            created_by=auto["owner_user_id"],
        )
        if success:
            return env_id, None
        return None, f"environment not provisioned ({message}); runs will use the bundled Python"
    except Exception as e:
        logger.warning(f"environment provisioning failed for automation {auto['automation_id']}: {e}")
        return None, f"environment not provisioned ({e}); runs will use the bundled Python"


@automations_bp.route("/api/<automation_id>", methods=["GET"])
@automations_gate
def get_automation(automation_id):
    auto = _get_manager().get_automation(automation_id)
    if not auto:
        return jsonify({"error": "not found"}), 404
    auto["versions"] = _get_manager().list_versions(automation_id)
    return jsonify({"automation": auto})


@automations_bp.route("/api/<automation_id>", methods=["DELETE"])
@automations_gate
def delete_automation(automation_id):
    ok, error = _get_manager().delete_automation(automation_id)
    if not ok:
        return jsonify({"error": error}), 404
    return jsonify({"deleted": automation_id})


# ---------------------------------------------------------- code / manifest

@automations_bp.route("/api/<automation_id>/code", methods=["GET"])
@automations_gate
def get_code(automation_id):
    version = request.args.get("version", type=int)
    code = _get_manager().get_code(automation_id, version)
    if code is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"code": code, "version": version})


@automations_bp.route("/api/<automation_id>/code", methods=["PUT"])
@automations_gate
def save_code(automation_id):
    data = request.get_json(silent=True) or {}
    code = data.get("code")
    if not isinstance(code, str) or not code.strip():
        return jsonify({"error": "'code' is required"}), 400
    manifest = data.get("manifest")  # optional: update manifest in the same version
    if manifest is not None:
        ok, errors = validate_manifest(manifest)
        if not ok:
            return jsonify({"error": "invalid manifest", "details": errors}), 400
    ok, new_version, errors = _get_manager().save_version(automation_id, code, manifest)
    if not ok:
        return jsonify({"error": errors[0] if errors else "save failed", "details": errors}), 400
    return jsonify({"version": new_version,
                    "note": "saved but not promoted — dry-run then promote to make it live"})


@automations_bp.route("/api/<automation_id>/manifest", methods=["GET"])
@automations_gate
def get_manifest(automation_id):
    version = request.args.get("version", type=int)
    manifest = _get_manager().get_manifest(automation_id, version)
    if manifest is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"manifest": manifest, "version": version})


@automations_bp.route("/api/<automation_id>/promote", methods=["POST"])
@automations_gate
def promote(automation_id):
    data = request.get_json(silent=True) or {}
    ok, version, error = _get_manager().promote(automation_id, data.get("version"))
    if not ok:
        return jsonify({"error": error}), 400
    return jsonify({"pinned_version": version})


# --------------------------------------------------------------------- runs

def _start_run(automation_id, inputs, trigger, version, requested_by, dry_run, wait):
    """Shared by the user route and the internal (scheduler) route."""
    runner = _get_runner()
    if wait or dry_run:  # dry-run UX needs the result inline
        result = runner.run(automation_id, inputs=inputs, trigger=trigger,
                            version=version, requested_by=requested_by, dry_run=dry_run)
        code = 200 if result.get("status") != "error" else 400
        return jsonify(result), code

    run_id = str(uuid.uuid4())
    thread = threading.Thread(
        target=runner.run, daemon=True,
        kwargs=dict(automation_id=automation_id, inputs=inputs, trigger=trigger,
                    version=version, requested_by=requested_by,
                    dry_run=False, run_id=run_id),
    )
    thread.start()
    return jsonify({"run_id": run_id, "status": "started",
                    "note": f"poll /automations/api/runs/{run_id}"}), 202


@automations_bp.route("/api/<automation_id>/run", methods=["POST"])
@automations_gate
def run_automation(automation_id):
    data = request.get_json(silent=True) or {}
    return _start_run(
        automation_id,
        inputs=data.get("inputs") or {},
        trigger="manual",
        version=data.get("version"),
        requested_by=current_user.id,
        dry_run=bool(data.get("dry_run")),
        wait=bool(data.get("wait")),
    )


@automations_bp.route("/api/<automation_id>/runs", methods=["GET"])
@automations_gate
def list_runs(automation_id):
    limit = min(request.args.get("limit", default=50, type=int), 500)
    return jsonify({"runs": _get_runner().list_runs(automation_id, limit)})


@automations_bp.route("/api/runs/<run_id>", methods=["GET"])
@automations_gate
def get_run(run_id):
    run = _get_runner().get_run(run_id)
    if not run:
        return jsonify({"error": "not found"}), 404
    return jsonify({"run": run})


@automations_bp.route("/api/runs/<run_id>/log", methods=["GET"])
@automations_gate
def get_run_log(run_id):
    log = _get_runner().get_run_log(run_id)
    if log is None:
        return jsonify({"error": "no log for this run"}), 404
    return jsonify({"log": log})


# ---------------------------------------------------------------- scheduling

@automations_bp.route("/api/<automation_id>/schedule", methods=["POST"])
@automations_gate
def schedule_automation(automation_id):
    """Create a scheduler job (job_type='automation') + schedule for this
    automation. Reuses the platform scheduler's tables and _create_schedule
    helper; the engine's _execute_automation_job fires it. Requires a
    PROMOTED version — a schedule must never point at nothing. The automation
    GUID travels in ScheduledJobParameters (TargetId is int-typed).
    Body: {"schedule": {"type": "cron"|"interval"|"date", ...}, "inputs": {}}"""
    auto = _get_manager().get_automation(automation_id)
    if not auto:
        return jsonify({"error": "not found"}), 404
    if auto["pinned_version"] < 1:
        return jsonify({"error": "nothing promoted — dry-run and promote a version before scheduling"}), 400

    data = request.get_json(silent=True) or {}
    payload, code = _create_automation_schedule(
        auto, data.get("schedule"), data.get("inputs") or {},
        user_id=current_user.id,
        username=str(getattr(current_user, "username", current_user.id)),
    )
    return jsonify(payload), code


def _create_automation_schedule(auto, schedule_data, inputs, user_id, username):
    """Write the ScheduledJobs + params + ScheduleDefinitions rows for an
    automation schedule. Shared by the user route and the CC internal manage
    endpoint. Returns (payload_dict, http_code)."""
    automation_id = auto["automation_id"]
    if not isinstance(schedule_data, dict):
        return {"error": "'schedule' object is required (type: cron|interval|date)"}, 400

    from scheduler_routes import _create_schedule
    conn = _get_manager()._db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO ScheduledJobs
               (JobName, JobType, TargetId, Description, CreatedBy, CreatedAt, IsActive)
               VALUES (?, 'automation', 0, ?, ?, getutcdate(), 1)""",
            f"Automation: {auto['name']}",
            f"Scheduled run of automation '{auto['name']}' ({automation_id}), pinned v{auto['pinned_version']}",
            username,
        )
        cursor.execute("SELECT @@IDENTITY")
        job_id = int(cursor.fetchone()[0])

        params = {
            "automation_id": (automation_id, "string"),
            "inputs": (json.dumps(inputs or {}), "json"),
            "user_id": (str(user_id), "int"),
        }
        for name, (value, ptype) in params.items():
            cursor.execute(
                """INSERT INTO ScheduledJobParameters
                   (ScheduledJobId, ParameterName, ParameterValue, ParameterType)
                   VALUES (?, ?, ?, ?)""",
                job_id, name, value, ptype,
            )

        schedule_id = _create_schedule(cursor, job_id, schedule_data)
        if not schedule_id:
            conn.rollback()
            return {"error": "invalid schedule definition"}, 400
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"schedule_automation({automation_id}) failed: {e}")
        return {"error": str(e)}, 500
    finally:
        conn.close()

    return {
        "scheduled_job_id": job_id,
        "schedule_id": schedule_id,
        "pinned_version": auto["pinned_version"],
        "note": "the scheduler engine picks this up on its next poll "
                "(engine restart required if it predates the 'automation' job type)",
    }, 201


@automations_bp.route("/api/<automation_id>/schedules", methods=["GET"])
@automations_gate
def list_schedules(automation_id):
    """List scheduler jobs/schedules attached to this automation."""
    conn = _get_manager()._db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT j.ScheduledJobId, j.JobName, j.IsActive,
                      s.ScheduleId, s.ScheduleType, s.CronExpression,
                      s.NextRunTime, s.LastRunTime, s.IsActive AS ScheduleActive
               FROM ScheduledJobs j
               JOIN ScheduledJobParameters p
                 ON p.ScheduledJobId = j.ScheduledJobId
                AND p.ParameterName = 'automation_id' AND p.ParameterValue = ?
               LEFT JOIN ScheduleDefinitions s ON s.ScheduledJobId = j.ScheduledJobId
               WHERE j.JobType = 'automation'
               ORDER BY j.ScheduledJobId""",
            automation_id,
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    return jsonify({"schedules": [
        {
            "scheduled_job_id": r.ScheduledJobId, "job_name": r.JobName,
            "job_active": bool(r.IsActive), "schedule_id": r.ScheduleId,
            "schedule_type": r.ScheduleType, "cron_expression": r.CronExpression,
            "next_run_time": r.NextRunTime.isoformat() if r.NextRunTime else None,
            "last_run_time": r.LastRunTime.isoformat() if r.LastRunTime else None,
            "schedule_active": bool(r.ScheduleActive) if r.ScheduleActive is not None else None,
        }
        for r in rows
    ]})


# --------------------------------------------------- live runs (Studio feed)

def _run_workdir(run: Dict) -> Optional[str]:
    log_path = (run or {}).get("log_path")
    return os.path.dirname(log_path) if log_path else None


def _read_events(run: Dict, after: int = 0, limit: int = 500):
    """Tail the run's events.jsonl sidecar starting after seq `after`."""
    workdir = _run_workdir(run)
    if not workdir:
        return []
    path = os.path.join(workdir, "events.jsonl")
    events = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("seq", 0) > after:
                    events.append(ev)
                    if len(events) >= limit:
                        break
    except FileNotFoundError:
        pass
    return events


def _run_live_payload(run: Dict, after: int = 0) -> Dict:
    """The Studio's poll payload: run state + new events + open checkpoints."""
    from .checkpoints import list_checkpoints
    events = _read_events(run, after)
    workdir = _run_workdir(run)
    checkpoints = list_checkpoints(workdir) if workdir else []
    return {
        "run": run,
        "events": events,
        "next": events[-1]["seq"] if events else after,
        "checkpoints": checkpoints,
        "pending_checkpoint": next((c for c in checkpoints if not c.get("decision")), None),
    }


@automations_bp.route("/api/runs/<run_id>/events", methods=["GET"])
@automations_gate
def run_events(run_id):
    run = _get_runner().get_run(run_id)
    if not run:
        return jsonify({"error": "not found"}), 404
    after = request.args.get("after", default=0, type=int)
    return jsonify(_run_live_payload(run, after))


@automations_bp.route("/api/active", methods=["GET"])
@automations_gate
def active_runs():
    return jsonify({"active": _get_runner().list_active_runs()})


@automations_bp.route("/api/runs/<run_id>/abort", methods=["POST"])
@automations_gate
def abort_run(run_id):
    ok, note = _get_runner().request_abort(run_id)
    return jsonify({"ok": ok, "note": note}), (200 if ok else 409)


@automations_bp.route("/api/runs/<run_id>/checkpoints/<checkpoint_id>/decision", methods=["POST"])
@automations_gate
def checkpoint_decision(run_id, checkpoint_id):
    """Human decision on a checkpoint gate: proceed resumes the script's poll
    loop; abort flips the run to 'aborting' (the supervision loop kills it)."""
    decision = (request.get_json(silent=True) or {}).get("decision")
    if decision not in ("proceed", "abort"):
        return jsonify({"error": "decision must be 'proceed' or 'abort'"}), 400
    result, code = _decide_checkpoint(run_id, checkpoint_id, decision, current_user.id)
    return jsonify(result), code


def _decide_checkpoint(run_id, checkpoint_id, decision, decided_by):
    from .checkpoints import decide_checkpoint
    runner = _get_runner()
    run = runner.get_run(run_id)
    if not run:
        return {"error": "run not found"}, 404
    workdir = _run_workdir(run)
    if not workdir:
        return {"error": "run has no workdir"}, 409
    checkpoint = decide_checkpoint(workdir, checkpoint_id, decision, decided_by)
    if checkpoint is None:
        return {"error": "checkpoint not found"}, 404
    if decision == "proceed":
        runner._db_set_run_status(run_id, "running", only_if_in=("waiting",))
    else:
        runner._db_set_run_status(run_id, "aborting", only_if_in=("waiting", "running"))
    try:
        from .runner import RunEventLog
        RunEventLog(workdir).emit("checkpoint_decided",
                                  checkpoint_id=checkpoint_id, decision=decision)
    except Exception:
        pass
    return {"checkpoint": checkpoint}, 200


# --------------------------------------------- runtime checkpoint (SDK side)

@automations_bp.route("/api/runtime/checkpoint", methods=["POST", "GET"])
def runtime_checkpoint():
    """SDK side of a checkpoint gate (run-token auth, like runtime/resolve).

    POST {token, message} → creates the gate, flips the run to 'waiting',
    notifies the requesting user (in-app always; SMS/email behind env flags),
    returns {checkpoint_id}.
    GET ?token=...&checkpoint_id=... → {decision: null|proceed|abort} for the
    SDK's poll loop."""
    if not getattr(cfg, "AUTOMATIONS_ENABLED", False):
        return jsonify({"error": "Automations feature is disabled"}), 403
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        token, message = data.get("token"), data.get("message") or "Checkpoint"
    else:
        token, message = request.args.get("token"), None

    from shared_auth import verify_automation_run_token
    claims, err = verify_automation_run_token(token or "")
    if err:
        return jsonify({"error": f"invalid run token: {err}"}), 403
    run = _get_runner().get_run(claims.get("run_id", ""))
    from .runner import LIVE_STATUSES
    if (not run or run.get("automation_id") != claims.get("automation_id")
            or run.get("status") not in LIVE_STATUSES):
        return jsonify({"error": "run token does not match a live run"}), 403
    workdir = _run_workdir(run)
    if not workdir:
        return jsonify({"error": "run has no workdir"}), 409

    from . import checkpoints as cp
    if request.method == "GET":
        checkpoint = cp.get_checkpoint(workdir, request.args.get("checkpoint_id", ""))
        if checkpoint is None:
            return jsonify({"error": "checkpoint not found"}), 404
        return jsonify({"decision": checkpoint.get("decision")})

    checkpoint = cp.create_checkpoint(workdir, message)
    _get_runner()._db_set_run_status(run["run_id"], "waiting", only_if_in=("running",))
    try:
        from .runner import RunEventLog
        RunEventLog(workdir).emit("checkpoint", checkpoint_id=checkpoint["checkpoint_id"],
                                  message=checkpoint["message"])
    except Exception:
        pass
    _notify_checkpoint(run, checkpoint)
    return jsonify({"checkpoint_id": checkpoint["checkpoint_id"], "poll_seconds": 2})


def _notify_checkpoint(run: Dict, checkpoint: Dict):
    """In-app is implicit (Mission Control + Studio show 'waiting' instantly).
    SMS/email are opt-in via env flags and go to the run's requesting user.
    Never fatal — a notification failure must not affect the gate."""
    auto = _get_manager().get_automation(run.get("automation_id", "")) or {}
    text = (f"AI Hub: automation '{auto.get('name', run.get('automation_id'))}' is paused "
            f"and waiting on you: {checkpoint.get('message')} — decide in Mission Control.")
    user_id = run.get("requested_by")
    if not user_id:
        return
    email, phone = _user_contact(user_id)
    if os.getenv("AUTOMATIONS_CHECKPOINT_NOTIFY_SMS", "false").lower() == "true" and phone:
        try:
            from AppUtils import sms_text_message_alert
            sms_text_message_alert(text, phone)
        except Exception as e:
            logger.warning(f"checkpoint SMS notify failed: {e}")
    if os.getenv("AUTOMATIONS_CHECKPOINT_NOTIFY_EMAIL", "false").lower() == "true" and email:
        try:
            from AppUtils import send_email_notification
            send_email_notification(email, "Automation waiting on your decision", text)
        except Exception as e:
            logger.warning(f"checkpoint email notify failed: {e}")


def _user_contact(user_id):
    """(email, phone) for a user id; missing columns/rows are simply None."""
    email = phone = None
    try:
        conn = _get_manager()._db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM [User] WHERE id = ?", int(user_id))
        row = cursor.fetchone()
        email = row[0] if row else None
        for col in ("phone", "phone_number", "mobile"):
            try:
                cursor.execute(f"SELECT {col} FROM [User] WHERE id = ?", int(user_id))
                row = cursor.fetchone()
                if row and row[0]:
                    phone = row[0]
                    break
            except Exception:
                continue
        conn.close()
    except Exception as e:
        logger.warning(f"user contact lookup failed for {user_id}: {e}")
    return email, phone


# ------------------------------------------------------------ webhook trigger

def _webhook_token(automation_id: str) -> Optional[str]:
    """Derived (never stored) per-automation webhook token:
    HMAC(jwt_secret, automation_id). Rotating CC_JWT_SECRET rotates all hooks."""
    import hashlib
    import hmac as _hmac
    try:
        from shared_auth import get_jwt_secret
        secret = get_jwt_secret()
    except Exception:
        secret = None
    if not secret:
        return None
    return _hmac.new(secret.encode("utf-8"),
                     f"automation-hook:{automation_id}".encode("utf-8"),
                     hashlib.sha256).hexdigest()[:32]


@automations_bp.route("/api/<automation_id>/webhook", methods=["GET"])
@automations_gate
def get_webhook(automation_id):
    """Return the automation's webhook trigger path (derived token, no storage)."""
    if not _get_manager().get_automation(automation_id):
        return jsonify({"error": "not found"}), 404
    token = _webhook_token(automation_id)
    if not token:
        return jsonify({"error": "webhook unavailable: no JWT secret configured"}), 503
    return jsonify({
        "url_path": f"/automations/api/hook/{automation_id}/{token}",
        "method": "POST",
        "body": {"inputs": {"<declared input>": "<value>"}},
        "note": "POST JSON to this path to trigger the promoted version; "
                "rotating CC_JWT_SECRET rotates the token",
    })


@automations_bp.route("/api/hook/<automation_id>/<token>", methods=["POST"])
def webhook_trigger(automation_id, token):
    """External webhook trigger (P4): fire the PROMOTED version. Auth is the
    derived per-automation token in the path — constant-time compared, no
    session. Runs async; responds 202 with the run_id to poll. Skip-if-running
    and input validation behave exactly like any other trigger."""
    import hmac as _hmac
    if not getattr(cfg, "AUTOMATIONS_ENABLED", False):
        return jsonify({"error": "Automations feature is disabled"}), 403
    expected = _webhook_token(automation_id)
    if not expected or not _hmac.compare_digest(token, expected):
        return jsonify({"error": "invalid webhook token"}), 403
    auto = _get_manager().get_automation(automation_id)
    if not auto:
        return jsonify({"error": "not found"}), 404
    if auto["pinned_version"] < 1:
        return jsonify({"error": "nothing promoted"}), 409

    inputs = (request.get_json(silent=True) or {}).get("inputs") or {}
    # validate inputs BEFORE going async so the caller gets a real 400
    from .runner import resolve_inputs
    manifest = _get_manager().get_manifest(automation_id, auto["pinned_version"]) or {}
    _, err = resolve_inputs(manifest, inputs)
    if err:
        return jsonify({"error": err}), 400

    run_id = str(uuid.uuid4())
    threading.Thread(
        target=_get_runner().run, daemon=True,
        kwargs=dict(automation_id=automation_id, inputs=inputs, trigger="webhook",
                    run_id=run_id),
    ).start()
    return jsonify({"run_id": run_id, "status": "started"}), 202


# ------------------------------------------------ runtime credential resolve

@automations_bp.route("/api/runtime/resolve", methods=["POST"])
def runtime_resolve():
    """Resolve ONE connection/secret for a live automation run (P2 SDK path).

    Called by the aihub_runtime SDK inside the automation subprocess. Auth is
    the signed run token itself (scoped to one run + an allowlist of names) —
    no session. Defense in depth: signature + audience + expiry (shared_auth),
    name-in-allowlist, and the run must still be 'running' in AutomationRuns
    (a leaked token is useless once the run finishes). Values are returned
    once over localhost and never logged."""
    if not getattr(cfg, "AUTOMATIONS_ENABLED", False):
        return jsonify({"error": "Automations feature is disabled"}), 403
    data = request.get_json(silent=True) or {}
    token, kind, name = data.get("token"), data.get("kind"), data.get("name")
    if not token or kind not in ("connection", "secret") or not name:
        return jsonify({"error": "token, kind (connection|secret), and name are required"}), 400

    from shared_auth import verify_automation_run_token
    claims, err = verify_automation_run_token(token)
    if err:
        return jsonify({"error": f"invalid run token: {err}"}), 403

    allowed = claims.get("connections" if kind == "connection" else "secrets") or []
    if name not in allowed:
        logger.warning(f"runtime_resolve: run {claims.get('run_id')} asked for undeclared {kind} '{name}'")
        return jsonify({"error": f"{kind} '{name}' is not declared in this automation's manifest"}), 403

    from .runner import LIVE_STATUSES
    run = _get_runner().get_run(claims.get("run_id", ""))
    if (not run or run.get("status") not in LIVE_STATUSES
            or run.get("automation_id") != claims.get("automation_id")):
        return jsonify({"error": "run token does not match a live run"}), 403

    if kind == "connection":
        value = _get_runner()._resolve_connection(name)
    else:
        value = _get_runner()._resolve_secret(name)
    if not value:
        return jsonify({"error": f"{kind} '{name}' could not be resolved"}), 404
    return jsonify({"value": value})


# --------------------------------------------- internal (CC builder tools)

@automations_bp.route("/api/internal/manage", methods=["POST"])
def internal_manage():
    """Service-to-service management dispatch for the Command Center's
    automation tools (P3). Auth: X-API-Key (service trust anchor) + the
    CC-verified user_context; role >= 2 (Developer) is enforced HERE too so
    the chokepoint doesn't rely on CC-side gating alone (same lesson as
    CC_BUILD_ALLOW_ALL_USERS).

    Body: {"action": ..., "user_context": {"user_id": int, "role": int,
           "username": str}, "payload": {...}}"""
    if request.headers.get("X-API-Key") != os.getenv("API_KEY", ""):
        return jsonify({"error": "unauthorized"}), 401
    if not getattr(cfg, "AUTOMATIONS_ENABLED", False):
        return jsonify({"error": "Automations feature is disabled"}), 403
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    uc = data.get("user_context") or {}
    payload = data.get("payload") or {}
    try:
        role = int(uc.get("role") or 0)
        user_id = int(uc.get("user_id") or 0)
    except (TypeError, ValueError):
        role, user_id = 0, 0
    if role < 2 or user_id <= 0:
        return jsonify({"error": "Developer role required"}), 403
    username = str(uc.get("username") or user_id)

    mgr = _get_manager()
    runner = _get_runner()
    aid = payload.get("automation_id")

    def _need_auto():
        a = mgr.get_automation(aid) if aid else None
        return a

    try:
        if action == "list":
            return jsonify({"automations": mgr.list_automations()})

        if action == "get":
            auto = _need_auto()
            if not auto:
                return jsonify({"error": "automation not found"}), 404
            auto["versions"] = mgr.list_versions(aid)
            auto["manifest"] = mgr.get_manifest(aid)
            auto["code"] = mgr.get_code(aid)
            return jsonify({"automation": auto})

        if action == "create":
            ok, auto, error = mgr.create_automation(
                name=payload.get("name", ""), description=payload.get("description", ""),
                owner_user_id=user_id, environment_id=payload.get("environment_id"))
            if not ok:
                return jsonify({"error": error}), 400
            warning = None
            if not payload.get("environment_id") and payload.get("provision_environment", True):
                env_id, warning = _provision_environment(auto)
                if env_id:
                    mgr.set_environment(auto["automation_id"], env_id)
                    auto = mgr.get_automation(auto["automation_id"])
            resp = {"automation": auto}
            if warning:
                resp["warning"] = warning
            return jsonify(resp), 201

        if action == "save_code":
            code = payload.get("code")
            if not isinstance(code, str) or not code.strip():
                return jsonify({"error": "'code' is required"}), 400
            manifest = payload.get("manifest")
            if manifest is not None:
                ok, errors = validate_manifest(manifest)
                if not ok:
                    return jsonify({"error": "invalid manifest", "details": errors}), 400
            ok, new_version, errors = mgr.save_version(aid, code, manifest)
            if not ok:
                return jsonify({"error": errors[0] if errors else "save failed",
                                "details": errors}), 400
            return jsonify({"version": new_version})

        if action == "promote":
            ok, version, error = mgr.promote(aid, payload.get("version"))
            if not ok:
                return jsonify({"error": error}), 400
            return jsonify({"pinned_version": version})

        if action in ("dry_run", "run"):
            auto = _need_auto()
            if not auto:
                return jsonify({"error": "automation not found"}), 404
            result = runner.run(aid, inputs=payload.get("inputs") or {},
                                trigger="manual", version=payload.get("version"),
                                requested_by=user_id, dry_run=(action == "dry_run"))
            code = 200 if result.get("status") != "error" else 400
            return jsonify(result), code

        if action == "runs":
            runs = runner.list_runs(aid, min(int(payload.get("limit", 20)), 100))
            return jsonify({"runs": runs})

        if action == "run_log":
            log = runner.get_run_log(payload.get("run_id", ""))
            if log is None:
                return jsonify({"error": "no log for this run"}), 404
            return jsonify({"log": log})

        if action == "active":
            return jsonify({"active": runner.list_active_runs()})

        if action == "run_events":
            run = runner.get_run(payload.get("run_id", ""))
            if not run:
                return jsonify({"error": "run not found"}), 404
            return jsonify(_run_live_payload(run, int(payload.get("after", 0))))

        if action == "abort":
            ok, note = runner.request_abort(payload.get("run_id", ""))
            return jsonify({"ok": ok, "note": note}), (200 if ok else 409)

        if action == "checkpoint_decision":
            decision = payload.get("decision")
            if decision not in ("proceed", "abort"):
                return jsonify({"error": "decision must be 'proceed' or 'abort'"}), 400
            result, code = _decide_checkpoint(payload.get("run_id", ""),
                                              payload.get("checkpoint_id", ""),
                                              decision, user_id)
            return jsonify(result), code

        if action == "schedule":
            auto = _need_auto()
            if not auto:
                return jsonify({"error": "automation not found"}), 404
            if auto["pinned_version"] < 1:
                return jsonify({"error": "nothing promoted — dry-run and promote first"}), 400
            resp, code = _create_automation_schedule(
                auto, payload.get("schedule"), payload.get("inputs") or {},
                user_id=user_id, username=username)
            return jsonify(resp), code

        return jsonify({"error": f"unknown action '{action}'"}), 400
    except Exception as e:
        logger.exception(f"internal_manage action={action} failed")
        return jsonify({"error": str(e)}), 500


# ------------------------------------------------- internal (scheduler seam)

@automations_bp.route("/api/internal/run", methods=["POST"])
def internal_run():
    """Service-to-service run trigger for the P1 'automation' scheduler job
    type. X-API-Key auth, same as the CC scheduled-run pattern. Runs the
    PINNED version, waits, and returns the honest outcome so the scheduler
    can record it."""
    if request.headers.get("X-API-Key") != os.getenv("API_KEY", ""):
        return jsonify({"error": "unauthorized"}), 401
    if not getattr(cfg, "AUTOMATIONS_ENABLED", False):
        return jsonify({"error": "Automations feature is disabled"}), 403
    data = request.get_json(silent=True) or {}
    automation_id = data.get("automation_id")
    if not automation_id:
        return jsonify({"error": "'automation_id' is required"}), 400
    result = _get_runner().run(
        automation_id,
        inputs=data.get("inputs") or {},
        trigger=data.get("trigger", "schedule"),
        requested_by=data.get("requested_by"),
    )
    code = 200 if result.get("status") != "error" else 400
    return jsonify(result), code
