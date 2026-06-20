"""
Browser Use Service - configuration.

Runs as an ISOLATED microservice (Strategy B: dedicated conda env `aihub-browseruse`) so
browser-use's heavy / aggressively-pinned deps (Playwright/Chromium, openai, pydantic, ...)
never touch the main app's `aihub2.1` environment. See .claude/skills/aihub-new-service.

Port: BROWSER_USE_PORT if set, else HOST_PORT + 100 (default 5101). The same override is
honored in CommonUtils.get_browser_use_api_base_url() so a production change takes effect
end-to-end. `import os` is at module top on purpose - this module reads env at import time.
"""
import os
import sys

# --- .env loading (best effort; the shared {app}\.env is the source of truth) ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def _find_app_root():
    """Resolve the AIHub install/repo root (where logs/, data/, local_secrets.py live).
    1) APP_ROOT env if set. 2) frozen: grandparent of the exe. 3) dev: parent of this
    service folder (browser_use_service/ sits directly under the repo root)."""
    explicit = os.getenv("APP_ROOT")
    if explicit:
        return os.path.abspath(explicit)
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


APP_ROOT = _find_app_root()
# Make the repo-root modules (local_secrets, shared_auth, secure_config) importable even
# though we run from browser_use_service/ under a different conda env. Mirrors the
# "config.py must add APP_ROOT to sys.path for PyInstaller compatibility" rule.
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

# Also load {APP_ROOT}\.env explicitly so the service finds it under NSSM (where cwd differs
# from the repo root). This is what makes OPENAI_API_KEY etc. available to browser-use's LLM.
try:
    from dotenv import load_dotenv as _load_env
    _load_env(os.path.join(APP_ROOT, ".env"))
except Exception:
    pass

# --- Networking ---
HOST = os.getenv("BROWSER_USE_HOST", "127.0.0.1")  # loopback; the main app calls it over HTTP


def resolve_port():
    """BROWSER_USE_PORT overrides the default; default preserves HOST_PORT + 100 (5101)."""
    explicit = os.getenv("BROWSER_USE_PORT")
    return int(explicit) if explicit else int(os.getenv("HOST_PORT", "5001")) + 100


PORT = resolve_port()

# --- Feature gating (mirror the CC_* flag style) ---
ENABLED = os.getenv("BROWSER_USE_ENABLED", "true").lower() == "true"
ALLOW_ALL_USERS = os.getenv("BROWSER_USE_ALLOW_ALL_USERS", "false").lower() == "true"

# --- browser-use runtime ---
# The LLM that DRIVES the agent loop. Default is Claude (matches the Command Center agent and
# keeps portal content inside the platform's existing Anthropic trust boundary rather than
# sending it to a second vendor). browser-use's ChatAnthropic/ChatOpenAI read their RAW api
# key from os.environ — ensure_llm_api_key() populates it from the encrypted .env value.
LLM_MODEL = os.getenv("BROWSER_USE_LLM_MODEL", "claude-opus-4-8")
# Headed (false) is the DEFAULT: headless Chrome silently blocks office/binary file downloads
# (.docx/.xlsx/.zip) it deems risky with no UI context, so portal downloads only work headed
# (verified live). Override to true only where downloads aren't needed or a virtual display exists.
HEADLESS = os.getenv("BROWSER_USE_HEADLESS", "false").lower() == "true"
TIMEOUT_SECONDS = int(os.getenv("BROWSER_USE_TIMEOUT", "300"))
MAX_STEPS = int(os.getenv("BROWSER_USE_MAX_STEPS", "50"))

# --- Navigation allowlist (prompt-injection containment) ---
# When RESTRICT_DOMAINS is on, the browser is hard-limited (at the browser layer, OUTSIDE the
# LLM) to the portal's own domain plus any extras below. This blocks a malicious/compromised
# portal page from steering the agent off-site to exfiltrate the authenticated session.
# SSO/identity portals redirect to a separate login host — add those auth domains to
# BROWSER_USE_ALLOWED_DOMAINS (e.g. "login.microsoftonline.com,*.okta.com") or set
# BROWSER_USE_RESTRICT_DOMAINS=false for that deployment.
RESTRICT_DOMAINS = os.getenv("BROWSER_USE_RESTRICT_DOMAINS", "true").lower() == "true"
ALLOWED_DOMAINS_EXTRA = [
    d.strip() for d in os.getenv("BROWSER_USE_ALLOWED_DOMAINS", "").split(",") if d.strip()
]

# Where portal downloads land before they're handed back to the main app's ArtifactManager.
DOWNLOAD_DIR = os.getenv(
    "BROWSER_USE_DOWNLOAD_DIR", os.path.join(APP_ROOT, "data", "browser_use_downloads")
)

# --- Logging ---
LOG_DIR = os.path.join(APP_ROOT, "logs")
LOG_FILE = os.getenv("BROWSER_USE_LOG", os.path.join(LOG_DIR, "browser_use_service.log"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


def get_secret(name, default=None):
    """Resolve a secret without hardcoding it: encrypted LocalSecretsManager first, then
    process env. Returns default if unset. Never store portal creds in .env or source."""
    if not name:
        return default
    try:
        from local_secrets import get_local_secret
        val = get_local_secret(name)
        if val:
            return val
    except Exception:
        pass
    return os.getenv(name, default)


def _provider_for_model(model):
    """Map a model id to its (provider, raw-env-var-name) the browser-use wrapper expects."""
    m = (model or "").lower()
    if m.startswith("claude") or m.startswith("anthropic"):
        return "anthropic", "ANTHROPIC_API_KEY"
    return "openai", "OPENAI_API_KEY"


def ensure_llm_api_key(model=None):
    """Make the driver model's RAW provider key available in os.environ.

    browser-use's ChatAnthropic / ChatOpenAI read ANTHROPIC_API_KEY / OPENAI_API_KEY straight
    from the environment, but AI Hub stores these only as Fernet-encrypted
    <NAME>_API_KEY_ENCRYPTED in .env. secure_config._SECRET_KEYS does NOT include the LLM
    keys, so they are never auto-loaded for this isolated service. Decrypt the right one with
    the same helper config.py uses (encrypt.decrypt_value) and export the plaintext var.

    Idempotent and fail-soft: if the plaintext var is already set (explicit override or a
    previous call) or the key can't be resolved, leave os.environ untouched and let the
    provider SDK raise its own clear "missing api key" error. Returns the env-var name on
    success, else None. Never returns or logs the secret value.
    """
    _, env_name = _provider_for_model(model or LLM_MODEL)
    if os.getenv(env_name):
        return env_name  # already plaintext (explicit override or a prior call)
    enc = os.getenv(f"{env_name}_ENCRYPTED")
    if not enc:
        return None
    try:
        from encrypt import decrypt_value, ENCRYPTION_KEY
        val = decrypt_value(enc, ENCRYPTION_KEY)
    except Exception:
        val = None
    if val:
        os.environ[env_name] = val
        return env_name
    return None


def resolve_allowed_domains(start_url):
    """Compute the navigation allowlist for a run as browser-use domain patterns, or None for
    no restriction. Honors BROWSER_USE_RESTRICT_DOMAINS + BROWSER_USE_ALLOWED_DOMAINS. The
    portal's own host is always allowed (plus a wildcard on its registrable domain so www/apex
    and sibling subdomains resolve); bare IPs get no wildcard."""
    if not RESTRICT_DOMAINS:
        return None
    from urllib.parse import urlparse
    allowed = set(ALLOWED_DOMAINS_EXTRA)
    host = (urlparse(start_url).hostname or "").strip().lower()
    if host:
        allowed.add(host)
        if not host.replace(".", "").isdigit():  # skip wildcard for bare IPs
            labels = host.split(".")
            if len(labels) >= 2:
                allowed.add("*." + ".".join(labels[-2:]))
    return sorted(allowed) or None
