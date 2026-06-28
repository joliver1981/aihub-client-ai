"""
Browser Use Service - FastAPI entry point.

Isolated microservice that simulates a user logging into a web portal and downloading
files (RPA-style), driven by `browser-use`. Runs under its own conda env (aihub-browseruse)
so its heavy deps never touch the main app. The main app reaches it over HTTP via
CommonUtils.get_browser_use_api_base_url().

Run (dev):   conda run -n aihub-browseruse python main.py
Health:      GET  /health
Fetch:       POST /portal/fetch   (header: X-AIHub-Internal: <API_KEY>)
"""
import asyncio
import logging
import logging.handlers
import os

# browser-use is noisy at INFO; quiet it before any of its modules import.
os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "info")

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import browser_use_config as config
import cobrowse
import portal_runner
import run_registry
import workflow_runner


class _DropCdpFrames(logging.Filter):
    """Drop the per-CDP-frame spam (lines like '🌎 → Event…') regardless of which logger emits it,
    so the agent's step reasoning + our portal/takeover events stay readable. Source-agnostic."""

    def filter(self, record):
        try:
            return "🌎" not in record.getMessage()
        except Exception:
            return True


os.makedirs(config.LOG_DIR, exist_ok=True)
_handlers = [
    logging.handlers.RotatingFileHandler(
        config.LOG_FILE, maxBytes=5_000_000, backupCount=3, encoding="utf-8"),
    logging.StreamHandler(),
]
for _h in _handlers:
    _h.addFilter(_DropCdpFrames())
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [browser_use] %(message)s",
    handlers=_handlers,
)
# Quiet the noisiest third-party loggers so the portal narrative stays readable.
for _noisy in ("cdp_use", "uvicorn.access", "httpx", "httpcore", "openai", "anthropic"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
log = logging.getLogger("browser_use_service")

app = FastAPI(title="AI Hub Browser Use Service", version="0.1.0")

INTERNAL_TOKEN = config.get_secret("API_KEY")
AUTH_ENFORCE = os.getenv("BROWSER_USE_AUTH_ENFORCE", "true").lower() == "true"

# Resolve the driver model's provider key up front (decrypts the encrypted .env value) so a
# misconfiguration is obvious at startup rather than on the first portal run. Never logs it.
_llm_env = config.ensure_llm_api_key()
log.info("LLM driver model=%s provider_key=%s", config.LLM_MODEL,
         "resolved" if _llm_env else "MISSING")

# Surface the bundled-Chromium resolution decision at startup so a missing browser is obvious
# in the log rather than as a cryptic launch failure on the first portal run.
_chrome_exe = getattr(config, "CHROME_EXECUTABLE", None)
if _chrome_exe:
    log.info("Bundled Chromium resolved: %s", _chrome_exe)
elif os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
    log.error(
        "Bundled Chromium NOT resolved under PLAYWRIGHT_BROWSERS_PATH=%s — portal runs will fail to launch a browser. Expected chromium-*\\chrome-win64\\chrome.exe.",
        os.environ.get("PLAYWRIGHT_BROWSERS_PATH"))
else:
    log.warning("No bundled Chromium (PLAYWRIGHT_BROWSERS_PATH unset) — relying on browser-use auto-discovery / system Chrome (dev-box mode).")


def require_internal(x_aihub_internal: str = Header(default="")):
    """Loopback service, but still gate /portal/fetch behind the platform API_KEY so a
    local non-privileged process can't drive a logged-in browser. Disable with
    BROWSER_USE_AUTH_ENFORCE=false for local testing."""
    if not AUTH_ENFORCE:
        return
    if not INTERNAL_TOKEN or x_aihub_internal != INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="invalid or missing internal token")


class PortalFetchRequest(BaseModel):
    task: str = Field(..., description="Goal in plain English, e.g. 'download the latest invoice PDFs'")
    start_url: str = Field(..., description="Portal URL to open first")
    portal_name: str | None = Field(None, description="Logical portal id (for logging / cred lookup)")
    username_secret: str | None = Field(None, description="local_secrets key holding the username")
    password_secret: str | None = Field(None, description="local_secrets key holding the password")
    totp_secret: str | None = Field(None, description="local_secrets key holding the TOTP shared secret")
    # Inline raw creds for a ONE-OFF ad-hoc run (before a portal is saved). When present they
    # take precedence over the *_secret key names. Used only for the first run; once a portal
    # is saved the caller reverts to key names and creds never transit in the clear again.
    username: str | None = Field(None, description="raw username (one-off ad-hoc run)")
    password: str | None = Field(None, description="raw password (one-off ad-hoc run)")
    totp: str | None = Field(None, description="raw TOTP shared secret (one-off ad-hoc run)")
    session_id: str | None = None
    user_id: str | None = None
    max_steps: int | None = None
    timeout: int | None = None
    # Files the operator attached in chat for this run; the agent may upload them into the portal.
    upload_files: list | None = None


@app.get("/health")
def health():
    return {"status": "ok", "service": "browser_use", "port": config.PORT, "enabled": config.ENABLED}


def _portal_call_kwargs(req: "PortalFetchRequest", run_id=None):
    """Resolve creds + build the run_portal_fetch kwargs shared by the sync and async endpoints.
    Inline raw creds (ad-hoc) win; otherwise resolve from the encrypted store by KEY NAME."""
    creds = {}
    if req.username and req.password:
        creds["username"] = req.username
        creds["password"] = req.password
        if req.totp:
            creds["totp_secret"] = req.totp
    else:
        if req.username_secret:
            creds["username"] = config.get_secret(req.username_secret)
        if req.password_secret:
            creds["password"] = config.get_secret(req.password_secret)
        if req.totp_secret:
            creds["totp_secret"] = config.get_secret(req.totp_secret)
    return dict(
        task=req.task,
        start_url=req.start_url,
        creds=creds,
        download_dir=os.path.join(config.DOWNLOAD_DIR, req.session_id or "adhoc"),
        llm_model=config.LLM_MODEL,
        headless=config.HEADLESS,
        max_steps=req.max_steps or config.MAX_STEPS,
        timeout=req.timeout or config.TIMEOUT_SECONDS,
        allowed_domains=config.resolve_allowed_domains(req.start_url),
        run_id=run_id,
        user_id=req.user_id,
        available_file_paths=req.upload_files or None,
    )


@app.post("/portal/fetch")
async def portal_fetch(req: PortalFetchRequest, _: None = Depends(require_internal)):
    """Synchronous auto-mode run (blocks until done). Takeover-enabled: a person can take over via
    Live runs if it pauses, but the HTTP call stays open. The chat uses /portal/start instead."""
    if not config.ENABLED:
        raise HTTPException(status_code=403, detail="browser_use disabled (BROWSER_USE_ENABLED=false)")
    log.info("portal/fetch portal=%s url=%s session=%s",
             req.portal_name, req.start_url, req.session_id)
    try:
        return await portal_runner.run_portal_fetch(**_portal_call_kwargs(req))
    except Exception as e:  # fail closed with a clean error, never leak a stack to the caller
        log.exception("portal/fetch failed")
        raise HTTPException(status_code=500, detail=f"portal fetch failed: {e}")


async def _run_auto_and_store(run_id: str, kwargs: dict):
    """Background auto-mode run for the chat: execute and stash the manifest for /portal/result."""
    await asyncio.sleep(0.1)
    try:
        result = await portal_runner.run_portal_fetch(**kwargs)
    except Exception as e:
        result = {"status": "error", "error": f"portal fetch failed: {e}", "files": [],
                  "file_count": 0, "final_result": None, "draft_workflow": None}
    run_registry.store_result(run_id, result)


@app.post("/portal/start")
async def portal_start(req: PortalFetchRequest, _: None = Depends(require_internal)):
    """Start an auto-mode run in the BACKGROUND and return its run_id immediately, so the chat can
    poll /portal/result and surface a 'take over' prompt the moment the run pauses for 2FA."""
    if not config.ENABLED:
        raise HTTPException(status_code=403, detail="browser_use disabled (BROWSER_USE_ENABLED=false)")
    if len(run_registry.RUNS) >= config.MAX_SESSIONS:
        raise HTTPException(status_code=429, detail="too many concurrent portal runs; try again shortly")
    import uuid as _uuid
    run_id = _uuid.uuid4().hex
    log.info("portal/start run=%s url=%s session=%s", run_id, req.start_url, req.session_id)
    asyncio.create_task(_run_auto_and_store(run_id, _portal_call_kwargs(req, run_id=run_id)))
    return {"run_id": run_id, "status": "started"}


@app.get("/portal/result/{run_id}")
def portal_result(run_id: str, _: None = Depends(require_internal)):
    """Poll an async auto run: while live, report status (incl. needs_human + reason); once done,
    return the manifest (files/error/draft)."""
    run = run_registry.get(run_id)
    if run is not None:
        return {"done": False, "status": run.status,
                "needs_human": run.status == run_registry.AWAITING_HUMAN,
                "reason": run.reason, "files": [], "file_count": 0}
    result = run_registry.get_result(run_id)
    if result is not None:
        return {"done": True, **result}
    raise HTTPException(status_code=404, detail="no such run")


class WorkflowRunRequest(BaseModel):
    workflow: dict = Field(..., description="Saved workflow: {name, start_url?, goal?, steps:[...]}")
    portal_name: str | None = None
    username_secret: str | None = None
    password_secret: str | None = None
    totp_secret: str | None = None
    # Inline raw creds (ad-hoc one-off, e.g. the very first run before the portal is saved).
    username: str | None = None
    password: str | None = None
    totp: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    timeout: int | None = None
    max_steps: int | None = None
    agent_fallback: bool = True
    # Per-run inputs the deterministic steps may reference (e.g. {form_value} substitutions).
    inputs: dict | None = None


def _resolve_creds(req: WorkflowRunRequest) -> dict:
    """Inline raw creds win (ad-hoc); otherwise resolve from the encrypted store by KEY NAME.
    Mirrors portal_fetch so saved workflows never put secrets on the wire. Computes the live
    TOTP code here (the runner types/substitutes it) so the shared secret stays server-side."""
    creds = {}
    if req.username and req.password:
        creds["username"] = req.username
        creds["password"] = req.password
        totp_secret = req.totp
    else:
        if req.username_secret:
            creds["username"] = config.get_secret(req.username_secret)
        if req.password_secret:
            creds["password"] = config.get_secret(req.password_secret)
        totp_secret = config.get_secret(req.totp_secret) if req.totp_secret else None
    if totp_secret:
        creds["totp_secret"] = totp_secret
        try:
            import pyotp
            creds["totp"] = pyotp.TOTP(totp_secret).now()
        except Exception:
            pass
    return creds


@app.post("/workflow/run")
async def workflow_run(req: WorkflowRunRequest, _: None = Depends(require_internal)):
    if not config.ENABLED:
        raise HTTPException(status_code=403, detail="browser_use disabled (BROWSER_USE_ENABLED=false)")
    wf = req.workflow or {}
    steps = wf.get("steps") or []
    if not steps:
        raise HTTPException(status_code=400, detail="workflow has no steps")
    creds = _resolve_creds(req)
    # Allowlist from the workflow's start_url (or the first goto/login step) so the agent can't roam.
    start_url = wf.get("start_url")
    if not start_url:
        for s in steps:
            if s.get("type") in ("goto", "login") and s.get("url"):
                start_url = s["url"]
                break
    allowed_domains = config.resolve_allowed_domains(start_url) if start_url else None
    log.info("workflow/run name=%s portal=%s steps=%d session=%s allowlist=%s",
             wf.get("name"), req.portal_name, len(steps), req.session_id, allowed_domains or "off")
    try:
        return await workflow_runner.run_workflow(
            workflow=wf,
            creds=creds,
            download_dir=os.path.join(config.DOWNLOAD_DIR, req.session_id or "adhoc"),
            llm_model=config.LLM_MODEL,
            headless=config.HEADLESS,
            allowed_domains=allowed_domains,
            timeout=req.timeout or config.TIMEOUT_SECONDS,
            max_steps=req.max_steps or config.MAX_STEPS,
            agent_fallback=req.agent_fallback,
            user_id=req.user_id,
            inputs=req.inputs,
        )
    except Exception as e:
        log.exception("workflow/run failed")
        raise HTTPException(status_code=500, detail=f"workflow run failed: {e}")


def _verify_cobrowse(token: str, run_id: str):
    """Validate a run-scoped co-browse token: signature + aud + run_id match. Returns
    (claims, error)."""
    try:
        from shared_auth import verify_cobrowse_token
    except Exception as e:
        return None, f"auth unavailable: {e}"
    claims, err = verify_cobrowse_token(token)
    if err:
        return None, err
    if str(claims.get("run_id")) != str(run_id):
        return None, "token not valid for this run"
    return claims, None


def _authz_run(token: str, run_id: str):
    """Control-plane gate: a valid run-scoped token AND the token identity is allowed to access
    the run (owner or Developer+). Returns (run, claims, error). The path run_id is NEVER trusted
    on its own — this closes the cross-user takeover hole."""
    claims, err = _verify_cobrowse(token, run_id)
    if err:
        return None, None, err
    run = run_registry.get(run_id)
    if not run:
        return None, claims, "no such run"
    try:
        from shared_auth import claim_user_id
        uid = claim_user_id(claims)
    except Exception:
        uid = claims.get("sub")
    if not run_registry.can_access(run, uid, claims.get("role") or 0):
        return None, claims, "not authorized for this run"
    return run, claims, None


@app.get("/runs")
def list_runs(user_id: str | None = None, role: int = 0, _: None = Depends(require_internal)):
    """In-flight runs (called server-side by the main app's Run Monitor with X-AIHub-Internal;
    the main app passes the logged-in user_id/role so the registry filters owner + Developer+)."""
    return {"runs": run_registry.list_runs(user_id=user_id, role=role)}


@app.get("/runs/{run_id}")
def run_status(run_id: str, _: None = Depends(require_internal)):
    run = run_registry.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="no such run")
    return run.to_dict()


@app.post("/runs/{run_id}/takeover")
async def run_takeover(run_id: str, token: str = ""):
    """Operator claims the session → pause the run at the next boundary; input opens once it
    actually suspends. Owner or Developer+ only."""
    run, claims, err = _authz_run(token, run_id)
    if err:
        raise HTTPException(status_code=403, detail=err)
    run_registry.pause_for_takeover(run)
    try:
        await cobrowse.broadcast_status(run)
    except Exception:
        pass
    log.info("AUDIT takeover run=%s by_user=%s portal=%s", run_id, claims.get("sub"), run.portal)
    return {"ok": True, "status": run.status}


@app.post("/runs/{run_id}/resume")
async def run_resume(run_id: str, token: str = ""):
    """Hand back / resume: release a `human` step or a takeover so the run continues. Owner or
    Developer+ only."""
    run, claims, err = _authz_run(token, run_id)
    if err:
        raise HTTPException(status_code=403, detail=err)
    run_registry.release(run)
    try:
        await cobrowse.broadcast_status(run)
    except Exception:
        pass
    log.info("AUDIT resume run=%s by_user=%s", run_id, claims.get("sub"))
    return {"ok": True, "status": run.status}


@app.get("/cobrowse")
def cobrowse_page(run: str, token: str, builder: str = ""):
    _run, _claims, err = _authz_run(token, run)
    if err:
        raise HTTPException(status_code=403, detail=err)
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "cobrowse.html"))


@app.post("/runs/{run_id}/save_workflow")
def run_save_workflow(run_id: str, token: str = "", name: str = ""):
    """Merge the run's base steps with the operator's recorded takeover actions into a draft
    workflow and persist it under the AUTHORIZED caller. Owner or Developer+ only. Recorded steps
    splice at the pause point so a mid-workflow takeover draft stays in order."""
    run, claims, err = _authz_run(token, run_id)
    if err:
        raise HTTPException(status_code=403, detail=err)
    import recorder as _rec
    recorded = run.recorder.finalize() if getattr(run, "recorder", None) else []
    base = list(run.base_steps or [])
    pi = run.pause_index
    if isinstance(pi, int) and 0 <= pi < len(base):
        merged = base[:pi + 1] + recorded + base[pi + 1:]
    else:
        merged = base + recorded
    steps = _rec.collapse(merged)
    if not steps:
        raise HTTPException(status_code=400, detail="nothing recorded to save")
    final_name = (name or "").strip() or f"{run.portal or 'Portal'} (recorded)"
    try:
        from command_center.tools import portal_workflows as store
        saved = store.save_workflow(
            claims.get("sub"), final_name, steps,
            portal_slug=run.portal_slug, start_url=run.start_url,
            goal=run.goal,
        )
    except Exception as e:
        log.exception("save_workflow failed")
        raise HTTPException(status_code=500, detail=f"save failed: {e}")
    log.info("AUDIT save_workflow run=%s by_user=%s slug=%s steps=%d recorded=%d",
             run_id, claims.get("sub"), saved.get("slug"), len(steps), len(recorded))
    return {"saved": saved, "recorded_steps": len(recorded)}


@app.websocket("/runs/{run_id}/stream")
async def run_stream(ws: WebSocket, run_id: str, token: str = ""):
    """Live screencast stream (server→client frames). Phase A ignores inbound input. A single
    sender task drains a bounded queue so frame pushes never race the control sends; the queue
    drops stale frames under backpressure to keep latency low."""
    run, claims, err = _authz_run(token, run_id)
    if err:
        await ws.close(code=4403)
        return
    await ws.accept()
    # Bounded queue + single sender so control frames never interleave with screencast frames.
    q = asyncio.Queue(maxsize=4)

    class _QViewer:
        async def send_json(self, m):
            try:
                q.put_nowait(m)
            except asyncio.QueueFull:
                # Drop the oldest frame so the newest paint wins under backpressure.
                try:
                    q.get_nowait()
                except Exception:
                    pass
                try:
                    q.put_nowait(m)
                except Exception:
                    pass

    viewer = _QViewer()

    async def sender():
        try:
            while True:
                m = await q.get()
                if m is None:
                    return
                await ws.send_json(m)
        except Exception:
            return

    sender_task = asyncio.create_task(sender())
    await cobrowse.add_viewer(run, viewer)
    await viewer.send_json({"t": "status", **run.to_dict()})
    conn_id = id(viewer)
    # Phase B: a single controller (the operator who took over) may send input frames.
    try:
        while True:
            msg = await ws.receive_json()
            if isinstance(msg, dict) and msg.get("t") in ("mouse", "text", "key"):
                if run.status in (run_registry.AWAITING_HUMAN, run_registry.TAKEN_OVER):
                    if run.controller is None:
                        run.controller = conn_id
                    if run.controller == conn_id:
                        await cobrowse.dispatch_input(run, msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if run.controller == conn_id:
            run.controller = None
        await cobrowse.remove_viewer(run, viewer)
        await q.put(None)
        try:
            await asyncio.wait_for(sender_task, timeout=2)
        except Exception:
            sender_task.cancel()


if __name__ == "__main__":
    import uvicorn
    log.info("Browser Use Service on %s:%s (enabled=%s, auth=%s)",
             config.HOST, config.PORT, config.ENABLED, AUTH_ENFORCE)
    # Bind 0.0.0.0 so NSSM/host can reach it; HOST is the address callers use.
    uvicorn.run(app, host="0.0.0.0", port=config.PORT)
