"""
portal_workflows_routes.py - Flask Blueprint for "Workflow mode": the visual builder that lets
a user record/curate deterministic portal steps and weave in LLM prompt steps, then save & run
them. This is the second option beside the existing one-shot "Auto-mode" (the CC chat agent).

JSON CRUD over the per-user store (command_center/tools/portal_workflows.py) + a run endpoint
that executes a saved workflow via the isolated Browser Use service (portal_workflow_run, which
posts to /workflow/run). Credentials never touch this layer - workflows store only step anchors
plus a portal_slug reference; the service resolves secrets server-side by key name.

Tool modules are imported at call-time (matching routes/data_explorer.py) to avoid import-order
issues at app startup.
"""
import logging
import os

import requests
from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import login_required, current_user

logger = logging.getLogger(__name__)


def _role():
    return getattr(current_user, "role", 0) or 0


def _browser_use_base():
    from CommonUtils import get_browser_use_api_base_url
    return get_browser_use_api_base_url()


portal_workflows_bp = Blueprint(
    "portal_workflows_bp", __name__,
    template_folder="../templates", static_folder="../static",
)


def _uid():
    if getattr(current_user, "is_authenticated", False):
        return current_user.id
    return None


@portal_workflows_bp.before_request
def _gate_experimental():
    """Portal Workflows rides Command Center's experimental gate — Developer+ (role>=2) OR
    CC_ALLOW_ALL_USERS, exactly like the /command-center route and the base.html menu. So when
    Command Center is disabled for a user, this whole feature (UI + APIs) is too, not just hidden.

    Exempt: the service-to-service endpoints (internal/run, internal/notify-takeover) carry their own
    X-AIHub-Internal / API-key auth, and the unauthenticated case is left to @login_required."""
    ep = request.endpoint or ""
    if ep.endswith(("internal_run", "internal_notify_takeover")):
        return None
    if not getattr(current_user, "is_authenticated", False):
        return None
    try:
        import config as cfg
        if getattr(cfg, "CC_ALLOW_ALL_USERS", False) or _role() >= 2:
            return None
    except Exception:
        if _role() >= 2:
            return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "forbidden — requires Command Center access"}), 403
    return redirect(url_for("home"))


@portal_workflows_bp.route("/portal-workflows")
@login_required
def portal_workflows_page():
    """The visual workflow builder screen."""
    return render_template("portal_workflows.html")


@portal_workflows_bp.route("/api/portal-workflows", methods=["GET"])
@login_required
def api_list():
    from command_center.tools import portal_workflows as store
    return jsonify({"workflows": store.list_workflows(_uid())})


@portal_workflows_bp.route("/api/portal-workflows/<name>", methods=["GET"])
@login_required
def api_get(name):
    from command_center.tools import portal_workflows as store
    wf = store.get_workflow(_uid(), name)
    if not wf:
        return jsonify({"error": f"no workflow named {name!r}"}), 404
    return jsonify({"workflow": wf})


@portal_workflows_bp.route("/api/portal-workflows", methods=["POST"])
@login_required
def api_save():
    from command_center.tools import portal_workflows as store
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    steps = body.get("steps")
    if not name:
        return jsonify({"error": "name is required"}), 400
    problems = store.validate_steps(steps)
    if problems:
        return jsonify({"error": "invalid steps", "problems": problems}), 400

    # Duplicate-name guard: a plain CREATE whose name collides with an existing workflow must not
    # silently overwrite it. `prev_slug` (the workflow currently open in the editor) marks a genuine
    # UPDATE of that same workflow; `overwrite` is the user's explicit confirm-overwrite. Absent
    # both, a collision returns 409 so the builder UI can prompt (overwrite / rename). The guard
    # lives here (the interactive endpoint the builder UI + Import JSON funnel through); CC recording
    # calls store.save_workflow directly and keeps its intended update-by-name behavior.
    overwrite = bool(body.get("overwrite"))
    prev_slug = body.get("prev_slug") or None
    editing_same = bool(prev_slug) and store.slug(prev_slug) == store.slug(name)
    if not overwrite and not editing_same and store.workflow_exists(_uid(), name):
        return jsonify({
            "error": f"A workflow named '{name}' already exists.",
            "code": "name_exists",
            "slug": store.slug(name),
        }), 409

    try:
        saved = store.save_workflow(
            _uid(), name, steps,
            portal_slug=body.get("portal_slug"),
            start_url=body.get("start_url"),
            goal=body.get("goal"),
            agent_oversight=body.get("agent_oversight"),
            takeover_timeout=body.get("takeover_timeout"),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"saved": saved})


@portal_workflows_bp.route("/api/portal-workflows/<name>", methods=["DELETE"])
@login_required
def api_delete(name):
    from command_center.tools import portal_workflows as store
    return jsonify({"deleted": store.delete_workflow(_uid(), name)})


@portal_workflows_bp.route("/api/portal-workflows/<name>/run", methods=["POST"])
@login_required
def api_run(name):
    """Execute a saved workflow against its portal (real browser run via the service)."""
    from command_center.tools import portal_workflow_run as runner
    body = request.get_json(silent=True) or {}
    user_context = {"user_id": _uid()}
    try:
        result = runner.run_workflow_by_name(
            name,
            session_id="workflow_builder",
            user_context=user_context,
            timeout=int(body.get("timeout") or 1200),
            agent_fallback=bool(body.get("agent_fallback", True)),
        )
    except Exception as e:
        logger.exception("workflow run failed")
        return jsonify({"status": "error", "error": str(e)}), 500
    return jsonify(result)


@portal_workflows_bp.route("/api/portal-workflows/internal/run", methods=["POST"])
def internal_run():
    """Scheduler-callable run: execute a saved workflow by slug for a given OWNER user_id, gated by
    the platform API_KEY (no user session). The Job Scheduler's `portal_workflow` job type posts
    here. For an unattended run, the workflow's 2FA must be a `verify_code` step with a TOTP secret
    on the linked portal (a `human` step would just time out)."""
    import hmac
    expected = os.getenv("API_KEY") or ""
    provided = request.headers.get("X-AIHub-Internal") or request.headers.get("X-API-Key") or ""
    if not expected or not hmac.compare_digest(provided, expected):
        return jsonify({"error": "forbidden"}), 403
    body = request.get_json(silent=True) or {}
    slug = (body.get("slug") or body.get("name") or "").strip()
    user_id = body.get("user_id")
    if not slug or user_id in (None, ""):
        return jsonify({"error": "missing slug/user_id"}), 400
    from command_center.tools import portal_workflow_run as runner
    try:
        result = runner.run_workflow_by_name(
            slug,
            session_id=f"scheduler-{slug}",
            user_context={"user_id": user_id},
            timeout=int(body.get("timeout") or 1200),
            agent_fallback=bool(body.get("agent_fallback", True)),
        )
    except Exception as e:
        logger.exception("[portal_workflows] scheduled run failed")
        return jsonify({"status": "error", "error": str(e)}), 500

    logger.info(
        "[portal_workflows] AUDIT scheduled-run slug=%s user=%s status=%s files=%s",
        slug, user_id, result.get("status"), result.get("file_count"),
    )

    if str(body.get("email_after") or "").lower() in ("1", "true", "yes"):
        try:
            email = _user_email(user_id)
            files = result.get("files") or []
            if email and result.get("status") == "ok" and files:
                from EmailUtils import send_email
                send_email(
                    [email], f"[AI Hub] Portal download — {slug}",
                    f"<p>Your scheduled portal workflow '<b>{slug}</b>' ran and downloaded "
                    f"{len(files)} file(s)." + (" The first is attached." if len(files) > 1 else "") + "</p>",
                    attachment_path=files[0], html_content=True,
                )
            elif email:
                from EmailUtils import send_email
                why = _why_no_files(result)
                summary = result.get("final_result") or ""
                bdy = f"<p>Your scheduled portal workflow '<b>{slug}</b>' ran but downloaded no file — {why}</p>"
                if summary:
                    bdy += f'<p style="color:#666;font-size:13px">Run summary: {summary}</p>'
                base = (os.getenv("APP_PUBLIC_BASE_URL") or "").rstrip("/")
                if base.startswith("http"):
                    bdy += f'<p>If it stopped at a verification/2FA step, open the <a href="{base}/portal-workflows/runs">Run Monitor</a> while the run is live to take over and finish it.</p>'
                send_email(
                    [email], f"[AI Hub] Portal run — {slug} (no file downloaded)",
                    bdy, html_content=True,
                )
        except Exception as e:
            logger.warning("[portal_workflows] scheduled-run email failed: %s", e)
    return jsonify(result)


@portal_workflows_bp.route("/api/portals", methods=["GET"])
@login_required
def api_portals():
    """Saved portals for this user (for the builder's portal dropdown - drives cred resolution)."""
    from command_center.tools import portal_registry
    return jsonify({"portals": portal_registry.list_portals(_uid())})


@portal_workflows_bp.route("/portal-workflows/runs")
@login_required
def runs_monitor_page():
    """Live list of in-flight portal runs; a 'Take over' button appears on blocked ones."""
    return render_template("portal_workflows_runs.html")


@portal_workflows_bp.route("/api/portal-workflows/runs", methods=["GET"])
@login_required
def api_runs():
    """Proxy the Browser Use service's /runs with the caller's identity so it filters to the
    runs this user owns (plus all runs for Developer+). Server-to-server, behind login."""
    base = _browser_use_base()
    try:
        r = requests.get(
            f"{base}/runs",
            params={"user_id": _uid(), "role": _role()},
            headers={"X-AIHub-Internal": os.getenv("API_KEY", "")},
            timeout=10,
        )
        return jsonify(r.json() if r.status_code == 200 else {"runs": [], "error": r.text[:200]})
    except Exception as e:
        return jsonify({"runs": [], "error": str(e)})


@portal_workflows_bp.route("/portal-workflows/cobrowse/<run_id>")
@login_required
def cobrowse_redirect(run_id):
    """Verify the caller can access this run, then mint a short-lived run-scoped token and send
    them to the co-browse live view. The service ALSO enforces can_access — this is the mint-side
    half: never hand out a token for a run the user doesn't own (or isn't Developer+ for)."""
    try:
        r = requests.get(
            f"{_browser_use_base()}/runs/{run_id}",
            headers={"X-AIHub-Internal": os.getenv("API_KEY", "")},
            timeout=8,
        )
    except Exception as e:
        return f"Could not verify run access: {e}", 502
    if r.status_code != 200:
        return "Run not found or already finished.", 404
    owner = str((r.json() or {}).get("owner_id") or "")
    if not (_role() >= 2 or (owner and owner == str(_uid()))):
        logger.warning(
            "[portal_workflows] AUDIT cobrowse-DENIED run=%s user=%s owner=%s",
            run_id, _uid(), owner,
        )
        return "You don't have access to this run.", 403
    try:
        from shared_auth import sign_cobrowse_token
        token = sign_cobrowse_token(run_id, _uid(), _role())
    except Exception as e:
        logger.warning("[portal_workflows] cobrowse token mint failed: " + f"{e}")
        return "Co-browse is unavailable (auth not configured).", 503
    public_base = os.getenv("BROWSER_USE_PUBLIC_BASE_URL") or _browser_use_base()
    from urllib.parse import quote
    from flask import url_for
    builder = url_for("portal_workflows_bp.portal_workflows_page", _external=True)
    logger.info(
        "[portal_workflows] AUDIT cobrowse-open run=%s user=%s role=%s",
        run_id, _uid(), _role(),
    )
    return redirect(f"{public_base}/cobrowse?run={run_id}&token={token}&builder={quote(builder, safe='')}")


def _user_email(user_id):
    """Resolve a user's email from the Users table (best-effort)."""
    try:
        import DataUtils
        df = DataUtils.Get_Users(str(user_id))
        if df is not None and not df.empty and "email" in df.columns:
            email = df.iloc[0]["email"]
            return email or None
    except Exception as e:
        logger.warning("[portal_workflows] user email lookup failed: %s", e)
    return None


def _why_no_files(result):
    """A short, human reason a portal run produced no file, derived from the step manifest, so a
    'no files' email can say WHERE it stopped (e.g. an un-completed 2FA take-over)."""
    steps = result.get("steps") or []
    blocking = next((s for s in reversed(steps) if s.get("status") == "failed"), None) or (steps[-1] if steps else None)
    if not blocking:
        return result.get("error") or "the run finished without reaching a download."
    label = {
        "verify_code": "the 2-step verification (2FA) step",
        "human": "a step that needed a person to take over",
        "login": "the login step",
        "click": "a navigation/click step",
        "agent": "the AI step that looks for the file",
        "verify": "the download-verification step",
    }.get(blocking.get("type"), f"the '{blocking.get('type')}' step")
    detail = (blocking.get("detail") or "").strip()
    why = f"it got stuck at {label}"
    return why + (f" ({detail})" if detail else "") + "."


@portal_workflows_bp.route("/api/portal-workflows/internal/notify-takeover", methods=["POST"])
def internal_notify_takeover():
    """Called by the Browser Use service (X-AIHub-Internal) when a run needs a human. Emails the
    owner a link to take over. Best-effort: returns {sent} and never raises on a delivery failure."""
    import hmac
    expected = os.getenv("API_KEY") or ""
    provided = request.headers.get("X-AIHub-Internal") or ""
    if not expected or not hmac.compare_digest(provided, expected):
        return jsonify({"error": "forbidden"}), 403
    body = request.get_json(silent=True) or {}
    user_id, run_id = body.get("user_id"), body.get("run_id")
    portal = body.get("portal") or "portal"
    if not user_id or not run_id:
        return jsonify({"error": "missing user_id/run_id"}), 400
    email = _user_email(user_id)
    base = os.getenv("APP_PUBLIC_BASE_URL") or request.host_url.rstrip("/")
    link = f"{base}/portal-workflows/cobrowse/{run_id}"
    monitor = f"{base}/portal-workflows/runs"
    logger.info(
        "[portal_workflows] AUDIT takeover-needed run=%s user=%s portal=%s email=%s",
        run_id, user_id, portal, "yes" if email else "no",
    )
    sent = False
    if email:
        try:
            from EmailUtils import send_email
            subject = f"[AI Hub] Portal run needs you — {portal}"
            html = (
                f"<p>A portal automation for <b>{portal}</b> is waiting for you (for example, a 2-step verification code).</p><p><a href=\""
                f"{link}\">Take over now</a> — or open the <a href=\""
                f"{monitor}\">Run Monitor</a>.</p>"
            )
            sent = bool(send_email([email], subject, html, html_content=True))
        except Exception as e:
            logger.warning("[portal_workflows] takeover email failed: %s", e)
    return jsonify({"sent": sent})
