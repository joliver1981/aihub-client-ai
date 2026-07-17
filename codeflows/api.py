"""
Code Flows REST + internal API — Developer+ gated, same trust model as the
Automations API (they are one family; a Code Flow is the multi-step sibling of
a single-script Automation).

    GET    /codeflows/api/list
    POST   /codeflows/api/create            {name, description?}
    GET    /codeflows/api/<name>
    DELETE /codeflows/api/<name>
    POST   /codeflows/api/<name>/dry_run
    POST   /codeflows/api/internal/manage   X-API-Key   -> the CC authoring tools

Gating: login + role in {2,3} (Developer/Admin) + cfg.AUTOMATIONS_ENABLED
(Code Flows ride the Automations feature flag — no separate switch). A Code
Flow is PERSISTED as a workflow (Workflows table, kind='code_flow'), so it
schedules on the EXISTING 'workflow' scheduler job type (TargetId = the flow's
workflow id) with no new job type and no scheduler restart.
"""

import json
import logging
from functools import wraps
from typing import Dict, Optional, Tuple

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

import config as cfg
from .manager import CodeFlowManager

logger = logging.getLogger(__name__)

code_flows_bp = Blueprint("code_flows", __name__, url_prefix="/codeflows")

_manager = None


def _get_manager() -> CodeFlowManager:
    global _manager
    if _manager is None:
        _manager = CodeFlowManager()
    return _manager


def _service_key_ok(provided: Optional[str]) -> bool:
    # Single source of truth — reuse the Automations service-key check (accepts
    # the raw API_KEY OR the machine-derived internal key; constant-time).
    from automations.api import _service_key_ok as _auto_key_ok
    return _auto_key_ok(provided)


def code_flows_gate(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not getattr(cfg, "AUTOMATIONS_ENABLED", False):
            return jsonify({"error": "Automations feature is disabled"}), 403
        if not hasattr(current_user, "role") or current_user.role not in [2, 3]:
            return jsonify({"error": "Access denied — Developer role required"}), 403
        return f(*args, **kwargs)
    return decorated


# ------------------------------------------------------------------- REST

@code_flows_bp.route("/api/list", methods=["GET"])
@code_flows_gate
def list_code_flows():
    return jsonify({"code_flows": _get_manager().list_code_flows()})


@code_flows_bp.route("/api/create", methods=["POST"])
@code_flows_gate
def create_code_flow():
    data = request.get_json(silent=True) or {}
    ok, info, err = _get_manager().create_code_flow(data.get("name", ""), data.get("description", ""))
    if not ok:
        return jsonify({"error": err}), 400
    return jsonify({"code_flow": info}), 201


@code_flows_bp.route("/api/<name>", methods=["GET"])
@code_flows_gate
def get_code_flow(name):
    cf = _get_manager().get_code_flow(name)
    if not cf:
        return jsonify({"error": "not found"}), 404
    return jsonify({"code_flow": cf})


@code_flows_bp.route("/api/<name>", methods=["DELETE"])
@code_flows_gate
def delete_code_flow(name):
    ok, err = _get_manager().delete_code_flow(name)
    if not ok:
        return jsonify({"error": err}), 404
    return jsonify({"deleted": name})


@code_flows_bp.route("/api/<name>/dry_run", methods=["POST"])
@code_flows_gate
def dry_run_code_flow(name):
    result = _get_manager().dry_run(name)
    code = 200 if result.get("status") != "error" else 400
    return jsonify(result), code


# ------------------------------------------------------ scheduling helper

def _create_code_flow_schedule(name, workflow_id, schedule_data, variables,
                               user_id, username) -> Tuple[Dict, int]:
    """Write ScheduledJobs (JobType='workflow', TargetId=the code flow's
    workflow id) + params + ScheduleDefinitions. Reuses the EXISTING 'workflow'
    job type — the engine's _execute_workflow_job POSTs /api/workflow/run with
    the saved code-flow workflow, so no new job type and no scheduler restart.
    Params become the run's `variables`."""
    if not isinstance(schedule_data, dict):
        return {"error": "'schedule' object is required (type: cron|interval|date)"}, 400

    from scheduler_routes import _create_schedule
    conn = _get_manager()._db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO ScheduledJobs
               (JobName, JobType, TargetId, Description, CreatedBy, CreatedAt, IsActive)
               VALUES (?, 'workflow', ?, ?, ?, getutcdate(), 1)""",
            f"Code Flow: {name}", int(workflow_id),
            f"Scheduled run of code flow '{name}' (workflow #{workflow_id})",
            username,
        )
        cursor.execute("SELECT @@IDENTITY")
        job_id = int(cursor.fetchone()[0])

        # workflow params are the run variables (typed as the engine reads them)
        for pname, pvalue in (variables or {}).items():
            cursor.execute(
                """INSERT INTO ScheduledJobParameters
                   (ScheduledJobId, ParameterName, ParameterValue, ParameterType)
                   VALUES (?, ?, ?, ?)""",
                job_id, pname,
                pvalue if isinstance(pvalue, str) else json.dumps(pvalue),
                "string" if isinstance(pvalue, str) else "json",
            )

        schedule_id = _create_schedule(cursor, job_id, schedule_data)
        if not schedule_id:
            conn.rollback()
            return {"error": "invalid schedule definition"}, 400
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"schedule_code_flow({name}) failed: {e}")
        return {"error": str(e)}, 500
    finally:
        conn.close()

    return {
        "scheduled_job_id": job_id, "schedule_id": schedule_id, "workflow_id": int(workflow_id),
        "note": "runs on the existing 'workflow' scheduler job type — no scheduler restart needed",
    }, 201


# --------------------------------------------- internal (CC authoring tools)

@code_flows_bp.route("/api/internal/manage", methods=["POST"])
def internal_manage():
    """Service-to-service authoring dispatch for the Command Center's code-flow
    tools. Auth: X-API-Key + the CC-verified user_context; role >= 2 enforced
    HERE (authoritative, not just CC-side). Same chokepoint pattern as the
    Automations internal manage.

    Body: {"action", "user_context": {user_id, role, username}, "payload"}"""
    if not _service_key_ok(request.headers.get("X-API-Key")):
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
    name = payload.get("name")

    try:
        if action == "list":
            return jsonify({"code_flows": mgr.list_code_flows()})

        if action == "get":
            cf = mgr.get_code_flow(name)
            if not cf:
                return jsonify({"error": "code flow not found"}), 404
            return jsonify({"code_flow": cf})

        if action == "create":
            ok, info, err = mgr.create_code_flow(name or "", payload.get("description", ""))
            if not ok:
                return jsonify({"error": err}), 400
            return jsonify({"code_flow": info}), 201

        if action == "add_step":
            ok, step_id, err = mgr.add_step(
                name, payload.get("step_name", "step"), payload.get("code", ""),
                connections=payload.get("connections"), secrets=payload.get("secrets"),
                packages=payload.get("packages"), inputs=payload.get("inputs"),
                outputs=payload.get("outputs"), timeout=int(payload.get("timeout", 600)),
                continue_on_error=bool(payload.get("continue_on_error", False)),
                allow_unverified=bool(payload.get("allow_unverified", False)),
                unverified_consent=bool(payload.get("unverified_consent", False)))
            if not ok:
                return jsonify({"error": err}), 400
            return jsonify({"step_id": step_id}), 201

        if action == "update_step_code":
            ok, err = mgr.update_step_code(name, payload.get("step_id", ""), payload.get("code", ""))
            if not ok:
                return jsonify({"error": err}), 400
            return jsonify({"ok": True})

        if action == "wire":
            ok, err = mgr.wire(name, payload.get("from_step", ""), payload.get("to_step", ""),
                               on=payload.get("on", "pass"))
            if not ok:
                return jsonify({"error": err}), 400
            return jsonify({"ok": True})

        if action in ("dry_run", "run"):
            result = mgr.dry_run(name)
            code = 200 if result.get("status") != "error" else 400
            return jsonify(result), code

        if action == "delete":
            ok, err = mgr.delete_code_flow(name)
            if not ok:
                return jsonify({"error": err}), 404
            return jsonify({"deleted": name})

        if action == "schedule":
            cf = mgr.get_code_flow(name)
            if not cf:
                return jsonify({"error": "code flow not found"}), 404
            if not (cf.get("definition") or {}).get("steps"):
                return jsonify({"error": "code flow has no steps yet — add steps before scheduling"}), 400
            resp, code = _create_code_flow_schedule(
                name, cf["workflow_id"], payload.get("schedule"),
                payload.get("variables") or {}, user_id=user_id, username=username)
            return jsonify(resp), code

        return jsonify({"error": f"unknown action '{action}'"}), 400
    except Exception as e:
        logger.exception(f"code flow internal_manage action={action} failed")
        return jsonify({"error": str(e)}), 500
