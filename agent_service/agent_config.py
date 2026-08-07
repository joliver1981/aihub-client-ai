"""
Configuration for The Agent service (agent_service).

The Agent hosts the Claude Agent SDK brain behind AI Hub: a per-user chat
surface whose tools are thin HTTP wrappers over the main app's existing REST
endpoints. Purely additive to the platform — nothing here is imported by the
main app.

Startup order mirrors the other services: resolve APP_ROOT (frozen-aware) ->
load root .env -> load_secure_config (API_KEY from registry) -> logging.
"""

import os
import sys
import json
import uuid
import hashlib
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


# ---------------------------------------------------------------------------
# APP_ROOT resolution (frozen-aware) — same pattern as browser_use_config.py.
# The isdir guard is load-bearing: NSSM AppEnvironmentExtra splits unquoted
# paths on spaces, so a mangled APP_ROOT env value must not win.
# ---------------------------------------------------------------------------

def _find_app_root():
    explicit = os.getenv("APP_ROOT")
    if explicit and os.path.isdir(explicit):
        return os.path.abspath(explicit)
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


APP_ROOT = _find_app_root()
os.environ["APP_ROOT"] = APP_ROOT
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

try:
    from dotenv import load_dotenv as _load_env
    _load_env(os.path.join(APP_ROOT, ".env"))
except Exception:
    pass

try:
    import secure_config as _sc
    _sc.load_secure_config()
except Exception:
    pass


# ---------------------------------------------------------------------------
# Logging — root logs/ folder, <name>_log.txt, 5MB x3 like the other services
# ---------------------------------------------------------------------------

def _setup_logger():
    lg = logging.getLogger("agent_service")
    lg.setLevel(logging.INFO)
    if lg.handlers:
        return lg
    log_dir = os.path.join(APP_ROOT, "logs")
    os.makedirs(log_dir, exist_ok=True)
    handler = RotatingFileHandler(
        os.path.join(log_dir, "agent_service_log.txt"),
        maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    lg.addHandler(handler)
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    lg.addHandler(console)
    return lg


logger = _setup_logger()


# ---------------------------------------------------------------------------
# Host / port — browser-facing like Command Center (users land here via the
# main app's /the-agent token redirect), so bind all interfaces by default.
# ---------------------------------------------------------------------------

HOST = os.getenv("AGENT_SERVICE_HOST", "0.0.0.0")


def resolve_port():
    explicit = os.getenv("AGENT_SERVICE_PORT")
    return int(explicit) if explicit else int(os.getenv("HOST_PORT", "5001")) + 110


PORT = resolve_port()
DEBUG = os.getenv("AGENT_SERVICE_DEBUG", "false").lower() == "true"

# Who may use The Agent: Developer+ (role >= 2) unless the all-users flag is on
AGENT_ALLOW_ALL_USERS = os.getenv("AGENT_ALLOW_ALL_USERS", "false").lower() == "true"

# Brain model (James, plan §8: Claude, default opus, env-overridable)
AGENT_MODEL = os.getenv("AGENT_MODEL", "claude-opus-5")
AGENT_MAX_TURNS = int(os.getenv("AGENT_MAX_TURNS", "40"))


# ---------------------------------------------------------------------------
# Data tree — data/agent/ per plan §8 (rides platform data conventions)
# ---------------------------------------------------------------------------

DATA_DIR = os.path.join(APP_ROOT, "data", "agent")
WORKSPACE_DIR = os.path.join(DATA_DIR, "workspace")   # SDK cwd (session-encoded)
CLAUDE_CONFIG_DIR = os.path.join(DATA_DIR, "claude")  # SDK session storage
SKILLS_PRODUCT_DIR = os.path.join(DATA_DIR, "skills", "product")
SKILLS_TENANT_DIR = os.path.join(DATA_DIR, "skills", "tenant")
USERS_DIR = os.path.join(DATA_DIR, "users")
GROUPS_DIR = os.path.join(DATA_DIR, "groups")

for _d in (WORKSPACE_DIR, CLAUDE_CONFIG_DIR, SKILLS_PRODUCT_DIR,
           SKILLS_TENANT_DIR, USERS_DIR, GROUPS_DIR):
    os.makedirs(_d, exist_ok=True)


def _sync_product_skills():
    """Product skills ship with the service (agent_service/product_skills/) and
    are mirrored into the data tree at startup — repo is the source of truth."""
    import shutil
    src_root = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "product_skills")
    if not os.path.isdir(src_root):
        return
    for name in os.listdir(src_root):
        src = os.path.join(src_root, name)
        if os.path.isfile(os.path.join(src, "SKILL.md")):
            dst = os.path.join(SKILLS_PRODUCT_DIR, name)
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst)


try:
    _sync_product_skills()
except Exception as _e:
    pass  # never block startup on skill sync


# ---------------------------------------------------------------------------
# Main-app base URL (port offset 0) — same env contract as cc_config
# ---------------------------------------------------------------------------

def get_base_url():
    protocol = os.getenv("PROTOCOL", "http")
    host = os.getenv("SERVICE_HOST", "127.0.0.1")
    port = int(os.getenv("HOST_PORT", "5001"))
    return f"{protocol}://{host}:{port}"


# ---------------------------------------------------------------------------
# Service-to-service API key — mirrors cc_config.get_internal_api_key(),
# which mirrors role_decorators.get_internal_api_key() server-side. Carried
# locally because role_decorators imports Flask, which this env doesn't have.
# ---------------------------------------------------------------------------

_INTERNAL_KEY_SALT = b"aihub_internal_api_v1_2026"


def _get_machine_id() -> str:
    if os.getenv("AIHUB_DATA_DIR"):
        data_dir = Path(os.getenv("AIHUB_DATA_DIR"))
    else:
        data_dir = Path(APP_ROOT) / "data"
    machine_id_file = data_dir / "secrets" / ".machine_id"
    if machine_id_file.exists():
        return machine_id_file.read_text().strip()
    logger.info(f"[machine_id] creating new machine id at {machine_id_file}")
    unique_parts = [str(uuid.uuid4()), str(uuid.getnode()), os.name]
    machine_id = hashlib.sha256("|".join(unique_parts).encode()).hexdigest()[:32]
    machine_id_file.parent.mkdir(parents=True, exist_ok=True)
    machine_id_file.write_text(machine_id)
    return machine_id


def get_internal_api_key() -> str:
    key_material = f"{_get_machine_id()}:{os.getenv('API_KEY', '')}".encode()
    return hashlib.pbkdf2_hmac("sha256", key_material, _INTERNAL_KEY_SALT,
                               iterations=10000).hex()


AI_HUB_API_KEY = os.getenv("AI_HUB_API_KEY", "") or get_internal_api_key()


# ---------------------------------------------------------------------------
# Anthropic key for the SDK — same fail-soft decrypt-and-export pattern as
# browser_use_config.ensure_llm_api_key(): AI Hub stores the key only as
# Fernet-encrypted ANTHROPIC_API_KEY_ENCRYPTED; the SDK reads the plain env.
# ---------------------------------------------------------------------------

def ensure_anthropic_key() -> bool:
    if os.getenv("ANTHROPIC_API_KEY"):
        return True
    enc = os.getenv("ANTHROPIC_API_KEY_ENCRYPTED")
    if not enc:
        logger.warning("No ANTHROPIC_API_KEY or ANTHROPIC_API_KEY_ENCRYPTED configured")
        return False
    try:
        from encrypt import decrypt_value, ENCRYPTION_KEY
        plain = decrypt_value(enc, ENCRYPTION_KEY)
        if plain:
            os.environ["ANTHROPIC_API_KEY"] = plain
            logger.info("ANTHROPIC_API_KEY decrypted and exported for the SDK")
            return True
    except Exception as e:
        logger.error(f"Failed to decrypt ANTHROPIC_API_KEY_ENCRYPTED: {e}")
    return False


def summary() -> dict:
    """Startup summary for logs / the health route (never includes secrets)."""
    return {
        "service": "agent_service",
        "host": HOST,
        "port": PORT,
        "model": AGENT_MODEL,
        "app_root": APP_ROOT,
        "main_app": get_base_url(),
        "allow_all_users": AGENT_ALLOW_ALL_USERS,
        "anthropic_key_present": bool(os.getenv("ANTHROPIC_API_KEY")
                                      or os.getenv("ANTHROPIC_API_KEY_ENCRYPTED")),
    }


logger.info(f"agent_config loaded: {json.dumps(summary())}")
