"""Workflow file drops: the Files page and its API (2026-10-10).

The folders, settings and run bookkeeping live in workflow_file_drops.py; this
blueprint is how people reach them from a browser (the Files page, the
Designer's Files button) and how The Agent reaches them for a user (API key
plus that user's signed X-AIHub-User assertion, the same identity the
document routes honour).

Access is decided here, once, for every caller:
  * Developers / Admins (and trusted service calls without an assertion):
    every workflow; set up, change settings, share.
  * Anyone else: only workflows shared with them or with one of their
    groups, and only to drop files, collect results, retry, delete a waiting
    file and start a run.

app.py injects what lives there (configure()): the Designer's save, the
caller identity resolver and the executor client. Kill switch
WORKFLOW_FILE_DROPS_ENABLED=false answers 404 everywhere.
"""
import json
import logging
import os
import time

from flask import Blueprint, abort, g, jsonify, render_template, request, send_file
from flask_login import current_user, login_required

import workflow_file_drops as wfd
from role_decorators import api_key_or_session_required

logger = logging.getLogger("WorkflowFilesRoutes")

workflow_files_bp = Blueprint("workflow_files", __name__)

_hooks: dict = {}


def configure(**hooks) -> None:
    """save_workflow(name, data, owner_id=), save_mirror(filename, data),
    caller_identity() -> (user_id, role), invalid_assertion (exception class),
    start_run(workflow_id, initiator, requested_by) -> execution_id,
    active_runs() -> [{workflow_id, ...}], run_status(execution_id) -> dict."""
    _hooks.update(hooks)


class _Refused(Exception):
    def __init__(self, message: str, status: int = 403):
        super().__init__(message)
        self.status = status


@workflow_files_bp.errorhandler(_Refused)
def _refused(e):
    return jsonify({"status": "error", "message": str(e)}), e.status


@workflow_files_bp.before_request
def _gate():
    if not wfd.enabled():
        if request.path.startswith("/api/"):
            return jsonify({"status": "error", "disabled": True,
                            "message": "Workflow file drops are turned off on this platform."}), 404
        abort(404)


# ------------------------------------------------------------------ database
def _db():
    from CommonUtils import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
    return conn, cur


_name_cache: dict = {}


def _user_name(uid) -> str:
    if uid in (None, ""):
        return ""
    try:
        if getattr(current_user, "is_authenticated", False) and str(current_user.id) == str(uid):
            return getattr(current_user, "user_name", None) or getattr(current_user, "username", "") or ""
    except Exception:
        pass
    hit = _name_cache.get(str(uid))
    if hit and hit[1] > time.time():
        return hit[0]
    name = ""
    try:
        conn, cur = _db()
        try:
            cur.execute("SELECT user_name FROM [User] WHERE id = ?", int(uid))
            row = cur.fetchone()
            name = (row[0] or "") if row else ""
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"user name for {uid}: {e}")
    _name_cache[str(uid)] = (name, time.time() + 300)
    return name


def _group_ids(uid) -> list:
    if not uid:
        return []
    try:
        conn, cur = _db()
        try:
            cur.execute("SELECT group_id FROM UserGroups WHERE user_id = ?", int(uid))
            return [int(r[0]) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"group lookup for user {uid} failed: {e}")
        return []


def _load_workflow(workflow_id):
    """(name, workflow_data) or raises _Refused(404)."""
    conn, cur = _db()
    try:
        cur.execute("SELECT workflow_name, workflow_data FROM Workflows WHERE id = ?", int(workflow_id))
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise _Refused(f"Workflow {workflow_id} was not found.", 404)
    try:
        data = json.loads(row[1] or "{}")
    except Exception:
        data = {}
    return row[0], data


def _workflow_names(ids) -> dict:
    ids = sorted({int(i) for i in ids})
    if not ids:
        return {}
    conn, cur = _db()
    try:
        cur.execute(f"SELECT id, workflow_name FROM Workflows WHERE id IN ({','.join('?' for _ in ids)})", *ids)
        return {int(r[0]): r[1] for r in cur.fetchall()}
    finally:
        conn.close()


# ------------------------------------------------------------------ identity
def _caller() -> dict:
    """{'user_id', 'role', 'name', 'service'} for this request."""
    ident = _hooks.get("caller_identity")
    try:
        uid, role = ident() if ident else (None, None)
    except Exception as e:
        bad = _hooks.get("invalid_assertion")
        if bad is not None and isinstance(e, bad):
            raise _Refused("Your identity could not be verified.", 403)
        raise
    if uid in (None, ""):
        if getattr(g, "auth_method", None) == "api_key":
            return {"user_id": None, "role": 3, "name": "service", "service": True}
        raise _Refused("Sign in to use workflow files.", 401)
    try:
        role = int(role or 0)
    except (TypeError, ValueError):
        role = 0
    return {"user_id": int(uid), "role": role, "name": _user_name(uid), "service": False}


def _settings_for_use(workflow_id, caller) -> dict:
    """The workflow's settings when the caller may use its files; otherwise
    the same 404 whether it is not set up or not shared (no probing)."""
    settings = wfd.load_settings(workflow_id)
    if not settings or not settings.get("enabled"):
        if wfd.can_manage(caller):
            raise _Refused("File drops are not set up for this workflow yet.", 404)
        raise _Refused("No workflow files are shared with you under that id.", 404)
    if not wfd.can_use(caller, settings, _group_ids(caller.get("user_id"))):
        raise _Refused("No workflow files are shared with you under that id.", 404)
    return settings


def _require_manager(caller, message: str = "") -> None:
    if not wfd.can_manage(caller):
        raise _Refused(message or "Only a Developer or Admin can change how a workflow's files are set up.", 403)


def _requester(caller, via: str, notify: bool = True) -> dict:
    if caller.get("service"):
        return {"via": via, "notify": False}
    return {"user_id": caller["user_id"], "name": caller.get("name") or "", "via": via,
            "notify": bool(notify)}


def requester_for_run(initiator: str, notify: bool = False) -> dict:
    """Who is behind a run started through /api/workflow/run (Designer, AI
    Builder, The Agent): their own results reach them where they are watching
    unless they ask for a notice. Never raises."""
    try:
        ident = _hooks.get("caller_identity")
        uid, _role = ident() if ident else (None, None)
    except Exception:
        return {}
    if uid in (None, ""):
        return {}
    return {"user_id": int(uid), "name": _user_name(uid), "via": str(initiator or "api"),
            "notify": bool(notify)}


# ------------------------------------------------------------------- running
def _running(workflow_id) -> bool:
    active = _hooks.get("active_runs")
    if not active:
        return False
    try:
        for run in active() or []:
            if str(run.get("workflow_id")) == str(workflow_id):
                return True
    except Exception as e:
        logger.warning(f"could not read active runs: {e}")
    return False


def _start_run(workflow_id, caller, via: str, notify: bool = True) -> dict:
    """Start a run now, or, while one of this workflow is going, ask that one
    to start it when it ends (two runs never work the same inbox)."""
    req = _requester(caller, via, notify)
    if _running(workflow_id):
        # Nobody is waiting on a queued run (not even The Agent): its results
        # reach the person through My Work.
        wfd.request_run_when_free(workflow_id, dict(req, notify=True) if req.get("user_id") else req)
        return {"queued": True,
                "message": "A run of this workflow is in progress; the next run starts when it "
                           "finishes and picks up the waiting files."}
    start = _hooks.get("start_run")
    if not start:
        raise _Refused("Runs cannot be started from here on this install.", 503)
    initiator = f"files:{caller.get('name') or caller.get('user_id') or 'service'}"
    try:
        eid = start(int(workflow_id), initiator, req)
    except Exception as e:
        raise _Refused(f"The run did not start: {e}", 502)
    if not eid:
        raise _Refused("The run did not start (no execution id came back).", 502)
    return {"execution_id": eid}


def _wants(value) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _int_arg(name: str, default: int) -> int:
    try:
        return max(1, int(request.args.get(name) or default))
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------- shaping
def _email_addresses(workflow_id) -> list:
    try:
        conn, cur = _db()
        try:
            cur.execute("SELECT email_address, is_active, inbound_enabled FROM AgentEmailAddresses "
                        "WHERE workflow_id = ? AND workflow_trigger_enabled = 1", int(workflow_id))
            return [{"address": r[0], "active": bool(r[1]) and bool(r[2])} for r in cur.fetchall() if r[0]]
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"email addresses for workflow {workflow_id}: {e}")
        return []


def _public_settings(settings: dict, manager: bool) -> dict:
    out = {"run_on_upload": bool(settings.get("run_on_upload")),
           "email_to_inbox": bool(settings.get("email_to_inbox", True)),
           "owner": (settings.get("owner") or {}).get("name") or ""}
    sp = settings.get("sharepoint") or None
    out["sharepoint"] = ({k: sp.get(k) for k in ("site_name", "drive_name", "folder_path")}
                         if sp else None)
    if manager:
        out.update({"notify_owner": bool(settings.get("notify_owner", True)),
                    "variables": settings.get("variables") or {},
                    "grants": settings.get("grants") or {"users": [], "groups": []},
                    "sharepoint": sp, "enabled": bool(settings.get("enabled"))})
    return out


def _run_summary(rec: dict) -> dict:
    keep = ("execution_id", "status", "started_at", "finished_at", "initiator", "consumed",
            "processed", "failed", "outputs", "waiting_now", "sharepoint", "waiting_at_start")
    out = {k: rec.get(k) for k in keep if k in rec}
    req = rec.get("requested_by") or {}
    out["started_by"] = req.get("name") or req.get("email") or ""
    out["via"] = req.get("via") or ""
    return out


def _grants_view(grants: dict) -> dict:
    users = [int(u) for u in (grants.get("users") or []) if str(u).lstrip("-").isdigit()]
    groups = [int(x) for x in (grants.get("groups") or []) if str(x).lstrip("-").isdigit()]
    out = {"users": [], "groups": []}
    if not (users or groups):
        return out
    try:
        conn, cur = _db()
        try:
            if users:
                cur.execute(f"SELECT id, user_name, name FROM [User] WHERE id IN "
                            f"({','.join('?' for _ in users)})", *users)
                out["users"] = [{"id": r[0], "user_name": r[1], "name": r[2] or r[1]} for r in cur.fetchall()]
            if groups:
                cur.execute(f"SELECT id, group_name FROM [Groups] WHERE id IN "
                            f"({','.join('?' for _ in groups)})", *groups)
                out["groups"] = [{"id": r[0], "name": r[1]} for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"grant names: {e}")
    return out


# --------------------------------------------------------------------- pages
@workflow_files_bp.route("/workflow-files")
@workflow_files_bp.route("/workflow-files/<int:workflow_id>")
@login_required
def files_page(workflow_id=None):
    return render_template("workflow_files.html", workflow_id=workflow_id or "",
                           can_manage=int(getattr(current_user, "role", 0) or 0) >= 2)


# ----------------------------------------------------------------------- API
@workflow_files_bp.route("/api/workflow-files", methods=["GET"])
@api_key_or_session_required()
def list_workflow_files():
    caller = _caller()
    manager = wfd.can_manage(caller)
    groups = [] if manager else _group_ids(caller.get("user_id"))
    rows = []
    usable = [s for s in wfd.all_settings() if s.get("enabled")
              and (manager or wfd.can_use(caller, s, groups))]
    names = _workflow_names([s["workflow_id"] for s in usable]) if usable else {}
    for s in usable:
        wid = int(s["workflow_id"])
        if wid not in names:          # the workflow was deleted
            continue
        runs = wfd.recent_runs(wid, limit=1)
        rows.append({"workflow_id": wid, "name": names[wid], "counts": wfd.counts(wid),
                     "run_on_upload": bool(s.get("run_on_upload")),
                     "owner": (s.get("owner") or {}).get("name") or "",
                     "last_run": _run_summary(runs[0]) if runs else None})
    rows.sort(key=lambda r: r["name"].lower())
    out = {"status": "success", "workflows": rows, "can_manage": manager}
    if manager and _wants(request.args.get("candidates")):
        conn, cur = _db()
        try:
            cur.execute("SELECT id, workflow_name FROM Workflows "
                        "WHERE ISNULL(JSON_VALUE(workflow_data, '$.kind'), '') <> 'code_flow' "
                        "ORDER BY workflow_name")
            taken = {r["workflow_id"] for r in rows}
            out["candidates"] = [{"workflow_id": r[0], "name": r[1]} for r in cur.fetchall()
                                 if r[0] not in taken]
        finally:
            conn.close()
    return jsonify(out)


@workflow_files_bp.route("/api/workflow-files/<int:workflow_id>", methods=["GET"])
@api_key_or_session_required()
def workflow_files_detail(workflow_id):
    caller = _caller()
    manager = wfd.can_manage(caller)
    settings = wfd.load_settings(workflow_id)
    set_up = bool(settings and settings.get("enabled"))
    if not set_up and not manager:
        raise _Refused("No workflow files are shared with you under that id.", 404)
    if set_up and not manager:
        _settings_for_use(workflow_id, caller)
    name, data = _load_workflow(workflow_id)
    out = {"status": "success", "workflow_id": workflow_id, "name": name, "set_up": set_up,
           "can_manage": manager}
    if manager:
        variables = data.get("variables") or {}
        out["variables"] = [{"name": k, "value": wfd._var_value(v)} for k, v in variables.items()]
        out["suggested"] = wfd.suggest_mapping(data)
        out["is_code_flow"] = str(data.get("kind") or "") == "code_flow"
        out["paths"] = {f: wfd.folder_path(workflow_id, f) for f in wfd.FOLDERS}
    if not set_up:
        if settings:
            out["settings"] = _public_settings(settings, manager)
        return jsonify(out)
    out["settings"] = _public_settings(settings, manager)
    out["counts"] = wfd.counts(workflow_id)
    out["files"] = {f: wfd.list_files(workflow_id, f) for f in wfd.FOLDERS}
    out["runs"] = [_run_summary(r) for r in wfd.recent_runs(workflow_id, limit=_int_arg("runs", 10))]
    out["running"] = _running(workflow_id)
    out["email_addresses"] = _email_addresses(workflow_id)
    if manager:
        out["roles"] = wfd.variable_roles(settings, data)
        out["shared_with"] = _grants_view(settings.get("grants") or {})
    return jsonify(out)


@workflow_files_bp.route("/api/workflow-files/<int:workflow_id>/setup", methods=["POST"])
@api_key_or_session_required()
def setup_workflow_files(workflow_id):
    """Create the folders and point the chosen variables at them (saved like
    a Designer edit)."""
    caller = _caller()
    _require_manager(caller)
    body = request.get_json(silent=True) or {}
    mapping = body.get("variables") or {}
    if not isinstance(mapping, dict):
        raise _Refused("variables must be {inbox, processed, failed, outbox: variable name}.", 400)
    name, data = _load_workflow(workflow_id)
    if str(data.get("kind") or "") == "code_flow":
        raise _Refused("File drops are for visual workflows; this is a code flow.", 400)
    new_data, changes, errors = wfd.plan_variables(workflow_id, data, mapping)
    if errors:
        return jsonify({"status": "error", "message": " ".join(errors), "errors": errors}), 400
    paths = wfd.ensure_folders(workflow_id)
    save = _hooks.get("save_workflow")
    if not save:
        raise _Refused("Saving workflows is not available here.", 503)
    try:
        save(name, new_data, owner_id=caller.get("user_id"))
    except ValueError as e:
        raise _Refused(str(e), 400)
    mirror = _hooks.get("save_mirror")
    if mirror:
        try:
            mirror(f"{name}.json", new_data)
        except Exception as e:      # the database is the source of truth
            logger.warning(f"workflow files setup: mirror write for '{name}' failed: {e}")
    settings = wfd.load_settings(workflow_id) or wfd.default_settings(caller)
    settings["enabled"] = True
    settings["variables"] = {r: str(v).strip() for r, v in mapping.items() if r in wfd.FOLDERS and v}
    previous = settings.setdefault("previous_values", {})
    for var, (old, _new) in changes.items():
        if var not in previous and not wfd._inside(os.path.abspath(old or "."),
                                                    wfd.workflow_root(workflow_id)):
            previous[var] = old
    if "run_on_upload" in body:
        settings["run_on_upload"] = bool(body.get("run_on_upload"))
    if not (settings.get("owner") or {}).get("user_id") and caller.get("user_id"):
        settings["owner"] = {"user_id": caller["user_id"], "name": caller.get("name") or ""}
    settings = wfd.save_settings(workflow_id, settings)
    logger.info(f"workflow files set up for workflow {workflow_id} ('{name}') by "
                f"{caller.get('name') or caller.get('user_id')}: "
                + ", ".join(f"{v} -> {n}" for v, (_o, n) in changes.items()))
    return jsonify({"status": "success", "workflow_id": workflow_id, "name": name, "paths": paths,
                    "changes": {v: {"from": o, "to": n} for v, (o, n) in changes.items()},
                    "settings": _public_settings(settings, True)})


@workflow_files_bp.route("/api/workflow-files/<int:workflow_id>/settings", methods=["PUT"])
@api_key_or_session_required()
def update_workflow_files_settings(workflow_id):
    caller = _caller()
    _require_manager(caller)
    settings = wfd.load_settings(workflow_id)
    if not settings:
        raise _Refused("Set up file drops for this workflow first.", 404)
    body = request.get_json(silent=True) or {}
    for key in ("run_on_upload", "notify_owner", "email_to_inbox", "enabled"):
        if key in body:
            settings[key] = bool(body[key])
    if "sharepoint" in body:
        sp = body.get("sharepoint")
        if sp:
            if not isinstance(sp, dict) or not sp.get("integration_id") or not sp.get("drive_id"):
                raise _Refused("SharePoint needs an integration and a document library.", 400)
            behavior = sp.get("conflict_behavior") or "replace"
            if behavior not in ("replace", "rename", "fail"):
                raise _Refused("conflict_behavior is replace, rename or fail.", 400)
            settings["sharepoint"] = {
                "integration_id": int(sp["integration_id"]), "drive_id": str(sp["drive_id"]),
                "folder_path": str(sp.get("folder_path") or "").strip().strip("/"),
                "parent_item_id": str(sp.get("parent_item_id") or ""),
                "site_name": str(sp.get("site_name") or ""), "drive_name": str(sp.get("drive_name") or ""),
                "conflict_behavior": behavior}
        else:
            settings["sharepoint"] = None
    settings = wfd.save_settings(workflow_id, settings)
    return jsonify({"status": "success", "settings": _public_settings(settings, True)})


def _resolve_people(values, kind: str) -> tuple:
    """Ids for user/group references given as ids, user names, emails or
    group names. Returns (ids, unknown)."""
    ids, unknown = [], []
    values = [v for v in (values or []) if str(v).strip()]
    if not values:
        return ids, unknown
    conn, cur = _db()
    try:
        for v in values:
            s = str(v).strip()
            if s.lstrip("-").isdigit():
                if kind == "user":
                    cur.execute("SELECT id FROM [User] WHERE id = ?", int(s))
                else:
                    cur.execute("SELECT id FROM [Groups] WHERE id = ?", int(s))
            elif kind == "user":
                cur.execute("SELECT id FROM [User] WHERE LOWER(user_name) = ? OR LOWER(email) = ? "
                            "OR LOWER(name) = ?", s.lower(), s.lower(), s.lower())
            else:
                cur.execute("SELECT id FROM [Groups] WHERE LOWER(group_name) = ?", s.lower())
            rows = cur.fetchall()
            if len(rows) == 1:
                ids.append(int(rows[0][0]))
            else:
                unknown.append(s if not rows else f"{s} (matches {len(rows)})")
    finally:
        conn.close()
    return ids, unknown


@workflow_files_bp.route("/api/workflow-files/<int:workflow_id>/access", methods=["PUT"])
@api_key_or_session_required()
def update_workflow_files_access(workflow_id):
    """Share a workflow's files. {users, groups} replaces the list;
    {add_users, remove_users, add_groups, remove_groups} changes it. People
    are ids, user names or emails; groups are ids or names."""
    caller = _caller()
    _require_manager(caller)
    settings = wfd.load_settings(workflow_id)
    if not settings or not settings.get("enabled"):
        raise _Refused("Set up file drops for this workflow first.", 404)
    body = request.get_json(silent=True) or {}
    grants = settings.get("grants") or {"users": [], "groups": []}
    users = {int(u) for u in grants.get("users") or [] if str(u).lstrip("-").isdigit()}
    groups = {int(x) for x in grants.get("groups") or [] if str(x).lstrip("-").isdigit()}
    unknown = []
    if "users" in body:
        ids, bad = _resolve_people(body.get("users"), "user")
        users, unknown = set(ids), unknown + bad
    if "groups" in body:
        ids, bad = _resolve_people(body.get("groups"), "group")
        groups, unknown = set(ids), unknown + bad
    for key, target, kind, add in (("add_users", users, "user", True), ("remove_users", users, "user", False),
                                   ("add_groups", groups, "group", True), ("remove_groups", groups, "group", False)):
        if key in body:
            ids, bad = _resolve_people(body.get(key), kind)
            unknown += bad
            (target.update if add else target.difference_update)(ids)
    if unknown:
        return jsonify({"status": "error", "unknown": unknown,
                        "message": "Not found (or not unique): " + ", ".join(unknown)
                                   + ". Nothing was changed."}), 400
    settings["grants"] = {"users": sorted(users), "groups": sorted(groups)}
    settings = wfd.save_settings(workflow_id, settings)
    return jsonify({"status": "success", "shared_with": _grants_view(settings["grants"])})


@workflow_files_bp.route("/api/workflow-files/<int:workflow_id>/people", methods=["GET"])
@api_key_or_session_required()
def workflow_files_people(workflow_id):
    """Users and groups to share with (the share dialog)."""
    caller = _caller()
    _require_manager(caller)
    conn, cur = _db()
    try:
        cur.execute("SELECT id, user_name, name, role FROM [User] WHERE role IN (1, 2, 3) ORDER BY name")
        users = [{"id": r[0], "user_name": r[1], "name": r[2] or r[1], "role": r[3]} for r in cur.fetchall()]
        cur.execute("SELECT g.id, g.group_name, COUNT(ug.user_id) FROM [Groups] g "
                    "LEFT JOIN [UserGroups] ug ON g.id = ug.group_id GROUP BY g.id, g.group_name "
                    "ORDER BY g.group_name")
        groups = [{"id": r[0], "name": r[1], "members": r[2]} for r in cur.fetchall()]
    finally:
        conn.close()
    return jsonify({"status": "success", "users": users, "groups": groups})


@workflow_files_bp.route("/api/workflow-files/<int:workflow_id>/upload", methods=["POST"])
@api_key_or_session_required()
def upload_workflow_files(workflow_id):
    """Drop files into the inbox: multipart `files` (several), or one raw body
    named by X-File-Name (URL-encoded). ?run=1 / ?run=0 decide whether a run
    starts afterwards; without it, the workflow's run-on-upload setting does."""
    caller = _caller()
    settings = _settings_for_use(workflow_id, caller)
    by = {"user_id": caller.get("user_id"), "name": caller.get("name") or "",
          "via": request.args.get("via") or "upload"}
    stored, errors = [], []
    if request.headers.get("X-File-Name"):        # raw body (The Agent); read before any form parsing
        from urllib.parse import unquote
        try:
            stored.append(wfd.store_stream(workflow_id, unquote(request.headers["X-File-Name"]),
                                           request.stream, by))
        except ValueError as e:
            errors.append(str(e))
    else:
        uploads = request.files.getlist("files") or request.files.getlist("file")
        if not uploads:
            raise _Refused("No file was sent.", 400)
        for f in uploads:
            try:
                stored.append(wfd.store_stream(workflow_id, f.filename, f.stream, by))
            except ValueError as e:
                errors.append(f"{f.filename or '(no name)'}: {e}")
    out = {"status": "success" if stored else "error", "stored": stored, "errors": errors}
    if stored:
        logger.info(f"workflow files: {len(stored)} file(s) into workflow {workflow_id}'s inbox by "
                    f"{by['name'] or by['user_id'] or 'service'}")
    # ?run=1 runs after this upload, ?run=0 does not (The Agent adds several
    # files, then starts one run itself); otherwise the workflow's setting.
    run_arg = request.args.get("run", request.form.get("run"))
    should_run = _wants(run_arg) if run_arg not in (None, "") else bool(settings.get("run_on_upload"))
    if stored and should_run:
        out["run"] = _start_run(workflow_id, caller, "upload")
    return jsonify(out), (200 if stored else 400)


@workflow_files_bp.route("/api/workflow-files/<int:workflow_id>/download", methods=["GET"])
@api_key_or_session_required()
def download_workflow_file(workflow_id):
    caller = _caller()
    _settings_for_use(workflow_id, caller)
    folder = request.args.get("folder") or "outbox"
    try:
        path = wfd.resolve_in_folder(workflow_id, folder, request.args.get("name") or "")
    except ValueError as e:
        raise _Refused(str(e), 400)
    if not os.path.isfile(path):
        raise _Refused("That file is no longer there.", 404)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


@workflow_files_bp.route("/api/workflow-files/<int:workflow_id>/retry", methods=["POST"])
@api_key_or_session_required()
def retry_workflow_files(workflow_id):
    """Move failed files back to the inbox ({names: [...]} or {all: true});
    {run: true} starts a run afterwards."""
    caller = _caller()
    _settings_for_use(workflow_id, caller)
    body = request.get_json(silent=True) or {}
    names = [r["name"] for r in wfd.list_files(workflow_id, "failed")] if body.get("all") \
        else [str(n) for n in body.get("names") or []]
    if not names:
        raise _Refused("Name the failed files to retry, or send all: true.", 400)
    by = {"user_id": caller.get("user_id"), "name": caller.get("name") or ""}
    moved, errors = [], []
    for n in names:
        try:
            moved.append({"from": n, "to": wfd.retry_file(workflow_id, n, by)})
        except FileNotFoundError:
            errors.append(f"{n}: not in Failed")
        except ValueError as e:
            errors.append(f"{n}: {e}")
    out = {"status": "success" if moved else "error", "moved": moved, "errors": errors}
    if moved and body.get("run"):
        out["run"] = _start_run(workflow_id, caller, "retry", notify=body.get("notify", True))
    return jsonify(out), (200 if moved else 400)


@workflow_files_bp.route("/api/workflow-files/<int:workflow_id>/delete", methods=["POST"])
@api_key_or_session_required()
def delete_workflow_files(workflow_id):
    """Remove files. Anyone the files are shared with can remove WAITING
    files (a wrong drop); other folders need a Developer or Admin."""
    caller = _caller()
    _settings_for_use(workflow_id, caller)
    body = request.get_json(silent=True) or {}
    folder = body.get("folder") or "inbox"
    if folder not in wfd.FOLDERS:
        raise _Refused(f"Unknown folder '{folder}'.", 400)
    if folder != "inbox":
        _require_manager(caller, "You can remove files that are waiting; removing results, processed or "
                                 "failed files needs a Developer or Admin.")
    deleted, errors = [], []
    for n in [str(x) for x in body.get("names") or []]:
        try:
            wfd.delete_file(workflow_id, folder, n)
            deleted.append(n)
        except FileNotFoundError:
            errors.append(f"{n}: not found")
        except (ValueError, OSError) as e:
            errors.append(f"{n}: {e}")
    if deleted:
        logger.info(f"workflow files: {caller.get('name') or caller.get('user_id')} removed "
                    f"{len(deleted)} file(s) from {folder} of workflow {workflow_id}")
    return jsonify({"status": "success" if deleted else "error", "deleted": deleted,
                    "errors": errors}), (200 if deleted else 400)


@workflow_files_bp.route("/api/workflow-files/<int:workflow_id>/run", methods=["POST"])
@api_key_or_session_required()
def run_workflow_files(workflow_id):
    """Run the workflow on what is waiting. {notify: false} when the caller
    reports the outcome itself (The Agent in chat)."""
    caller = _caller()
    _settings_for_use(workflow_id, caller)
    body = request.get_json(silent=True) or {}
    via = str(body.get("via") or "files")[:40]
    out = _start_run(workflow_id, caller, via, notify=body.get("notify", True) is not False)
    return jsonify(dict(out, status="success"))


@workflow_files_bp.route("/api/workflow-files/<int:workflow_id>/runs", methods=["GET"])
@api_key_or_session_required()
def workflow_files_runs(workflow_id):
    caller = _caller()
    _settings_for_use(workflow_id, caller)
    return jsonify({"status": "success",
                    "runs": [_run_summary(r) for r in wfd.recent_runs(workflow_id, limit=_int_arg("limit", 20))]})


@workflow_files_bp.route("/api/workflow-files/<int:workflow_id>/runs/<execution_id>", methods=["GET"])
@api_key_or_session_required()
def workflow_files_run(workflow_id, execution_id):
    """One run: its live status and, once it ends, what it took and made."""
    caller = _caller()
    _settings_for_use(workflow_id, caller)
    rec = wfd.load_run(workflow_id, execution_id)
    status = None
    getter = _hooks.get("run_status")
    if getter:
        try:
            status = (getter(execution_id) or {}).get("workflow_status")
        except Exception as e:
            status = None
            logger.debug(f"run status {execution_id}: {e}")
    if rec is None and status is None:
        raise _Refused("That run was not found for this workflow.", 404)
    return jsonify({"status": "success", "run_status": status or (rec or {}).get("status"),
                    "finished": bool((rec or {}).get("finished_at")),
                    "run": _run_summary(rec) if rec else None})


@workflow_files_bp.route("/api/workflow-files/<int:workflow_id>/runs/<execution_id>/notify",
                         methods=["POST"])
@api_key_or_session_required()
def workflow_files_run_notify(workflow_id, execution_id):
    """Tell the person who started this run through My Work when it ends
    (The Agent stopped waiting for it)."""
    caller = _caller()
    _settings_for_use(workflow_id, caller)
    rec = wfd.load_run(workflow_id, execution_id)
    if rec is None:
        raise _Refused("That run was not found for this workflow.", 404)
    req = rec.get("requested_by") or {}
    if not wfd.can_manage(caller) and str(req.get("user_id")) != str(caller.get("user_id")):
        raise _Refused("Only the person who started the run can ask for its notice.", 403)
    return jsonify({"status": "success", "result": wfd.notify_when_done(workflow_id, execution_id)})
