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
import logging
import os

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

import browser_use_config as config
import portal_runner

os.makedirs(config.LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [browser_use] %(message)s",
    handlers=[logging.FileHandler(config.LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("browser_use_service")

app = FastAPI(title="AI Hub Browser Use Service", version="0.1.0")

INTERNAL_TOKEN = config.get_secret("API_KEY")
AUTH_ENFORCE = os.getenv("BROWSER_USE_AUTH_ENFORCE", "true").lower() == "true"

# Resolve the driver model's provider key up front (decrypts the encrypted .env value) so a
# misconfiguration is obvious at startup rather than on the first portal run. Never logs it.
_llm_env = config.ensure_llm_api_key()
log.info("LLM driver model=%s provider_key=%s", config.LLM_MODEL,
         "resolved" if _llm_env else "MISSING")


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
    session_id: str | None = None
    user_id: str | None = None
    max_steps: int | None = None
    timeout: int | None = None


@app.get("/health")
def health():
    return {"status": "ok", "service": "browser_use", "port": config.PORT, "enabled": config.ENABLED}


@app.post("/portal/fetch")
async def portal_fetch(req: PortalFetchRequest, _: None = Depends(require_internal)):
    if not config.ENABLED:
        raise HTTPException(status_code=403, detail="browser_use disabled (BROWSER_USE_ENABLED=false)")

    # Resolve creds from the encrypted store by KEY NAME - the caller never sends raw secrets.
    creds = {}
    if req.username_secret:
        creds["username"] = config.get_secret(req.username_secret)
    if req.password_secret:
        creds["password"] = config.get_secret(req.password_secret)
    if req.totp_secret:
        creds["totp_secret"] = config.get_secret(req.totp_secret)

    allowed_domains = config.resolve_allowed_domains(req.start_url)
    log.info("portal/fetch portal=%s url=%s session=%s allowlist=%s",
             req.portal_name, req.start_url, req.session_id, allowed_domains or "off")
    try:
        return await portal_runner.run_portal_fetch(
            task=req.task,
            start_url=req.start_url,
            creds=creds,
            download_dir=os.path.join(config.DOWNLOAD_DIR, req.session_id or "adhoc"),
            llm_model=config.LLM_MODEL,
            headless=config.HEADLESS,
            max_steps=req.max_steps or config.MAX_STEPS,
            timeout=req.timeout or config.TIMEOUT_SECONDS,
            allowed_domains=allowed_domains,
        )
    except Exception as e:  # fail closed with a clean error, never leak a stack to the caller
        log.exception("portal/fetch failed")
        raise HTTPException(status_code=500, detail=f"portal fetch failed: {e}")


if __name__ == "__main__":
    import uvicorn
    log.info("Browser Use Service on %s:%s (enabled=%s, auth=%s)",
             config.HOST, config.PORT, config.ENABLED, AUTH_ENFORCE)
    # Bind 0.0.0.0 so NSSM/host can reach it; HOST is the address callers use.
    uvicorn.run(app, host="0.0.0.0", port=config.PORT)
