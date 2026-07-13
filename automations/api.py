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
