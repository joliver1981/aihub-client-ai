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
    1) APP_ROOT env if set AND a real directory. 2) frozen: grandparent of the exe.
    3) dev/Strategy-B: parent of this service folder (browser_use_service/ sits directly
    under the repo/install root).

    The isdir guard is load-bearing: NSSM's AppEnvironmentExtra splits on spaces, so an
    unquoted `APP_ROOT=C:\\Program Files\\AIHub` reaches us truncated as `C:\\Program`
    (invisible on a dev box whose path has no space). Trusting that poisons EVERY derived
    path — .env never loads (LLM provider_key=MISSING), the chromium glob roots in the
    wrong place, and local_secrets can't import. Fall through to the parent-of-this-file
    rule instead, which is always correct for a Strategy-B deployment."""
    explicit = os.getenv("APP_ROOT")
    if explicit and os.path.isdir(explicit):
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

# Load the platform secure config (registry API_KEY + encrypted secrets store) the same way
# every other service does at startup. On a CLIENT install the license API_KEY lives in the
# WINDOWS REGISTRY, not .env — without this call get_secret("API_KEY") is None, so main.py's
# require_internal has no token and EVERY internal call from Command Center is rejected with
# 401 "invalid or missing internal token" (portal runs die before reaching the portal). The
# dev box masks it because the repo .env carries API_KEY. Fail-soft: if secure_config can't
# import (exotic env), .env/env resolution still applies.
try:
    import secure_config as _sc
    _sc.load_secure_config()
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
# The LLM that DRIVES the agent loop. Platform convention: AGENTIC work runs on the OpenAI
# stack (the same get_openai_config()-shaped Azure/OpenAI transport the Command Center agent
# uses — which is also the only transport a CLIENT install can use with BYOK off, because the
# Azure endpoint + encrypted key are baked in via _build_config); Anthropic is reserved for
# document processing and one-off calls. Default therefore follows the platform's Azure
# deployment. Set BROWSER_USE_LLM_MODEL=claude-* to explicitly drive with Claude instead
# (requires BYOK or a provisioned Anthropic key — see ensure_llm_api_key).
LLM_MODEL = (os.getenv("BROWSER_USE_LLM_MODEL")
             or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
             or "gpt-5.4")
# Headless (true) is the DEFAULT. Headless Chrome drops office/binary download-navigations,
# but portal_runner re-pulls them via an in-page fetch (see _make_download_capturer), so
# downloads work headless AND need no interactive desktop - the right mode for an NSSM service
# or scheduled/unattended runs. Set BROWSER_USE_HEADLESS=false only to watch a run in a visible
# window (requires an interactive desktop) or as a fallback for exotic non-link downloads.
HEADLESS = os.getenv("BROWSER_USE_HEADLESS", "true").lower() == "true"
TIMEOUT_SECONDS = int(os.getenv("BROWSER_USE_TIMEOUT", "300"))
MAX_STEPS = int(os.getenv("BROWSER_USE_MAX_STEPS", "50"))
# Max concurrent in-flight portal runs (each drives a real browser); /portal/start and
# /portal/fetch reject with 429 once run_registry.RUNS reaches this.
MAX_SESSIONS = int(os.getenv("BROWSER_USE_MAX_SESSIONS", "5"))

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


# --- Bundled Chromium resolution (client installs) ---
def resolve_chrome_executable():
    """Locate the bundled Chromium binary, or None to let browser-use discover a browser
    itself (dev-box mode: system Chrome / the Playwright cache).

    Why we resolve it OURSELVES: browser-use 0.12.9's local-browser discovery globs the
    LITERAL Windows segment `chromium-*\\chrome-win\\chrome.exe`, but modern Playwright
    builds ship `chrome-win64` — so on a clean client the glob never matches, discovery
    returns None, and the fallback (`uvx playwright install chromium`) is impossible
    offline → every portal run dies at browser launch. Invisible on a dev box because an
    installed system Chrome masks it. portal_runner/workflow_runner pass this value as
    `executable_path`, which takes the watchdog's executable-path-first branch and
    bypasses the broken glob entirely.

    Roots searched: PLAYWRIGHT_BROWSERS_PATH if set, else {APP_ROOT}\\browser_use_chromium
    (where the installer stages the bundle). Newest chromium revision wins."""
    import glob as _glob
    import re as _re
    root = os.getenv("PLAYWRIGHT_BROWSERS_PATH") or os.path.join(APP_ROOT, "browser_use_chromium")
    try:
        hits = _glob.glob(os.path.join(root, "chromium-*", "chrome-win64", "chrome.exe"))
    except Exception:
        hits = []
    if not hits:
        return None

    def _revision(path):
        m = _re.search(r"chromium-(\d+)", path)
        return int(m.group(1)) if m else -1

    return max(hits, key=_revision)


# None on a dev box with no bundle — consumers (main.py startup log, portal_runner,
# workflow_runner) all treat None as "use browser-use's own discovery".
CHROME_EXECUTABLE = resolve_chrome_executable()

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


def _env_or_build(name, default=""):
    """env var if non-empty, else the build-baked _build_config attribute, else default.
    Mirrors encrypt._env_or_build — _build_config ships inside the frozen exes and (from
    v1.7.5) as a loose {app} module, so a CLIENT resolves the platform Azure endpoint/key
    with an empty .env, exactly like the frozen Command Center does."""
    v = os.getenv(name)
    if v:
        return v
    try:
        import _build_config as _bc
        v = getattr(_bc, name, None)
        if v:
            return v
    except Exception:
        pass
    return default


def _decrypt(enc):
    """Decrypt a Fernet-encrypted config value with the platform key; None on any failure."""
    if not enc:
        return None
    try:
        from encrypt import decrypt_value, ENCRYPTION_KEY
        return decrypt_value(enc, ENCRYPTION_KEY)
    except Exception:
        return None


def resolve_openai_driver(model=None):
    """Resolve the OpenAI-stack transport for the driver LLM, mirroring the platform's
    api_keys_config.get_openai_config() priorities WITHOUT importing it (it pulls Flask,
    absent from this isolated env):
      1. BYOK user key (USER_OPENAI_API_KEY, byok_config.json gate) → direct OpenAI
      2. USE_OPENAI_API → direct OpenAI with the system key
      3. default → Azure OpenAI (endpoint + encrypted key via env-or-_build_config — the
         client-native path; how CC runs on a stock client with BYOK off)
    Returns a dict {'api_type': 'open_ai'|'azure', 'api_key', 'model'/'azure_*'...} or None
    when nothing is configured."""
    wanted = (model or "").strip() or None

    # 1. BYOK (same gate as ensure_llm_api_key's anthropic path)
    try:
        import json as _json
        _byok_path = os.path.join(
            os.getenv("AIHUB_DATA_DIR") or os.path.join(APP_ROOT, "data"), "byok_config.json")
        with open(_byok_path, "r") as _fh:
            _byok_on = bool(_json.load(_fh).get("byok_enabled", False))
    except Exception:
        _byok_on = False
    if _byok_on:
        try:
            from local_secrets import get_local_secret
            key = get_local_secret("USER_OPENAI_API_KEY")
        except Exception:
            key = None
        if key:
            return {"api_type": "open_ai", "api_key": key,
                    "base_url": _env_or_build("OPENAI_API_BASE_URL", "https://api.openai.com/v1"),
                    "model": wanted or _env_or_build("OPENAI_DEPLOYMENT_NAME", "gpt-5.4")}

    # 2. Direct OpenAI when the install is configured for it
    if str(_env_or_build("USE_OPENAI_API", "")).lower() in ("true", "1", "yes"):
        key = os.getenv("OPENAI_API_KEY") or _decrypt(_env_or_build("OPENAI_API_KEY_ENCRYPTED"))
        if key:
            return {"api_type": "open_ai", "api_key": key,
                    "base_url": _env_or_build("OPENAI_API_BASE_URL", "https://api.openai.com/v1"),
                    "model": wanted or _env_or_build("OPENAI_DEPLOYMENT_NAME", "gpt-5.4")}

    # 3. Azure OpenAI (platform default)
    endpoint = _env_or_build("AZURE_OPENAI_BASE_URL")
    if endpoint:
        key = os.getenv("AZURE_OPENAI_API_KEY") or _decrypt(_env_or_build("AZURE_OPENAI_API_KEY_ENCRYPTED"))
        if key:
            deployment = wanted or _env_or_build("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-5.4")
            return {"api_type": "azure", "api_key": key,
                    "azure_endpoint": endpoint,
                    "api_version": _env_or_build("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
                    "azure_deployment": deployment, "model": deployment}
    return None


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

    # BYOK next — the key the admin entered ONCE in Settings → API Keys lives in the
    # encrypted local store as USER_<PROVIDER>_API_KEY, gated by data/byok_config.json.
    # This is how a client install drives the LLM with NO vendor key in .env, and it
    # survives upgrades (the store is on the client machine). Mirrors
    # api_keys_config.get_active_anthropic_key(); that module imports Flask (absent from
    # this isolated env), so replicate its two reads here. Its './data' fallback is also
    # cwd-sensitive — anchor on APP_ROOT instead (our cwd is browser_use_service\).
    try:
        import json as _json
        _byok_path = os.path.join(
            os.getenv("AIHUB_DATA_DIR") or os.path.join(APP_ROOT, "data"),
            "byok_config.json")
        with open(_byok_path, "r") as _fh:
            _byok_on = bool(_json.load(_fh).get("byok_enabled", False))
    except Exception:
        _byok_on = False
    if _byok_on:
        try:
            from local_secrets import get_local_secret
            val = get_local_secret(f"USER_{env_name}")
            if val:
                os.environ[env_name] = val
                return env_name
        except Exception:
            pass

    # Encrypted LocalSecretsManager under the plain provider name — lets an admin
    # provision the key without editing .env and without flipping BYOK on.
    try:
        from local_secrets import get_local_secret
        val = get_local_secret(env_name)
        if val:
            os.environ[env_name] = val
            return env_name
    except Exception:
        pass
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
