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
import re
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

# Brain model (James, plan §8: Claude, env-overridable). Default is
# claude-haiku-5-5 for every role since 2026-10-07 (james): it beat
# claude-haiku-4-5 on pack 20 and the 48-question data battery at ~1/7 the
# cost, and the production relay serves it. Admins can still pick another
# model per install at runtime (data/agent/settings.json, no restart).
AGENT_MODEL = os.getenv("AGENT_MODEL", "claude-haiku-5-5")
# Regular users (role < 2) run their own model chain (james 2026-08-24,
# all-users rollout), admin-settable at runtime; same default today.
AGENT_MODEL_ROLE1 = os.getenv("AGENT_MODEL_ROLE1", "claude-haiku-5-5")
# Optional model code names (james 2026-10-10): "real-id:code-name,..." —
# what people SEE instead of the real ids, Admins included. Absent/empty =
# no mapping = no change. Parsed below (parse_model_codenames).
AGENT_MODEL_CODENAMES = os.getenv("AGENT_MODEL_CODENAMES", "")
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
# Deferred results -> chat (2026-08-22, Level 1). When a scheduled / delayed
# agent task fires, its result is appended as the next turn of the conversation
# it was scheduled from (SDK session resume) and the My Work FYI deep-links to
# it. Flag OFF = exactly the prior behavior (fresh headless session + FYI only).
# Read at call time so tests / ops can flip it without a restart of the module.
# ---------------------------------------------------------------------------

def defer_to_chat_enabled() -> bool:
    return os.getenv("AGENT_DEFER_TO_CHAT", "true").strip().lower() == "true"


# Agent Email READING tools (list_my_email / read_email / attachments) — one
# flag reverts the whole family. It lives HERE (not brain.py) because three
# SDK-free callers need it too: the poller's prompt hints + pre-extraction
# budget and the status tool's pointer line. Default true: these are read
# tools plus one sandboxed save, and inbound email itself stays separately
# gated by AGENT_EMAIL_ENABLED (the poller kill switch, default false).
def email_tools_enabled() -> bool:
    return os.getenv("AGENT_EMAIL_TOOLS", "true").strip().lower() == "true"


# A chat turn that arrives while a deferred run is being appended to the SAME
# conversation waits (bounded) instead of racing it — two writers on one SDK
# transcript is the one real risk of the resume design. 0 disables the wait.
CHAT_BUSY_WAIT_SECONDS = int(os.getenv("AGENT_CHAT_BUSY_WAIT_SECONDS", "90"))


# ---------------------------------------------------------------------------
# Anthropic key for the SDK — same fail-soft decrypt-and-export pattern as
# browser_use_config.ensure_llm_api_key(): AI Hub stores the key only as
# Fernet-encrypted ANTHROPIC_API_KEY_ENCRYPTED; the SDK reads the plain env.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Runtime settings (James 2026-08-09): admin-changeable WITHOUT a restart.
# A tiny JSON file in the service's data dir; build_options reads the
# effective model per turn, so a UI change applies to the very next message.
# AGENT_MODEL (.env) stays the install default; the override sits on top and
# clearing it falls straight back.
# ---------------------------------------------------------------------------

RUNTIME_SETTINGS_PATH = os.path.join(APP_ROOT, "data", "agent", "settings.json")
_MODEL_RE = re.compile(r"^[A-Za-z0-9._:-]{3,80}$")


def _read_runtime_settings() -> dict:
    try:
        with open(RUNTIME_SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get_effective_model(role=None) -> str:
    """Model for a turn. No role (or Developer+) keeps the original chain:
    runtime override > AGENT_MODEL. Regular users (role < 2) get their own
    chain: role1_model override > AGENT_MODEL_ROLE1 (all-users rollout D4)."""
    settings = _read_runtime_settings()
    if role is not None and int(role) < 2:
        override = str(settings.get("role1_model") or "").strip()
        return override if override else AGENT_MODEL_ROLE1
    override = str(settings.get("model") or "").strip()
    return override if override else AGENT_MODEL


# ---------------------------------------------------------------------------
# Platform version (James 2026-08-20): surface the same APP_VERSION the legacy
# UI badges, without importing the main app. Dev tree: parse app_config.py.
# Frozen installs bundle app_config inside the onedir exe (not on disk), so
# AGENT_APP_VERSION env is the install-time override; unresolvable -> "" and
# the UI hides the badge.
# ---------------------------------------------------------------------------

def _read_app_version() -> str:
    env = os.getenv("AGENT_APP_VERSION", "").strip()
    if env:
        return env
    try:
        with open(os.path.join(APP_ROOT, "app_config.py"), encoding="utf-8") as f:
            for line in f:
                m = re.match(r"""^APP_VERSION\s*=\s*["']([^"']+)["']""", line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return ""


APP_VERSION = _read_app_version()


def _write_runtime_settings(settings: dict) -> None:
    os.makedirs(os.path.dirname(RUNTIME_SETTINGS_PATH), exist_ok=True)
    tmp = RUNTIME_SETTINGS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=1)
    os.replace(tmp, RUNTIME_SETTINGS_PATH)


def _set_model_key(key: str, model, label: str) -> None:
    settings = _read_runtime_settings()
    # an Admin sees (and may type) code names; the store keeps the real id
    m = resolve_model(model)
    if m:
        if not _MODEL_RE.match(m):
            raise ValueError("model id may only contain letters, digits, "
                             "dots, colons, underscores and hyphens")
        settings[key] = m
    else:
        settings.pop(key, None)
    _write_runtime_settings(settings)
    logger.info(f"{label} override {'set to ' + m if m else 'CLEARED'}")


def set_model_override(model) -> str:
    """Set (or clear, with None/'') the runtime model override. Returns the
    now-effective model. Raises ValueError on a malformed id."""
    _set_model_key("model", model, "model")
    return get_effective_model()


def set_role1_model_override(model) -> str:
    """Same, for the regular-user (role < 2) model. Clearing falls back to
    AGENT_MODEL_ROLE1. Returns the now-effective role-1 model."""
    _set_model_key("role1_model", model, "role1 model")
    return get_effective_model(role=1)


def get_turn_cap() -> int:
    """Per-user daily turn cap (all-users rollout D6). 0 = OFF (the default);
    admins (role >= 3) are always exempt. Stored in the runtime settings."""
    try:
        return max(0, int(_read_runtime_settings().get("turns_per_day") or 0))
    except (TypeError, ValueError):
        return 0


def set_turn_cap(value) -> int:
    """Set (or clear, with 0/None/'') the daily turn cap. Returns the
    now-effective cap. Raises ValueError on a non-integer."""
    settings = _read_runtime_settings()
    raw = str(value if value is not None else "").strip()
    n = int(raw) if raw else 0
    if n < 0:
        raise ValueError("turns_per_day must be 0 (off) or a positive integer")
    if n:
        settings["turns_per_day"] = n
    else:
        settings.pop("turns_per_day", None)
    _write_runtime_settings(settings)
    logger.info(f"daily turn cap {'set to ' + str(n) if n else 'turned OFF'}")
    return n


# ---------------------------------------------------------------------------
# Model code names (james 2026-10-10). With AGENT_MODEL_CODENAMES set, every
# user-facing model id passes through display_model() — /api/me, the Admin
# pickers, /health, chat events, error text — so nobody (Admins included)
# sees a real id, and a turn on a mapped model gets one system-prompt line
# telling The Agent to go by that name. Display-only: the SDK call, the
# runtime settings file and the server logs keep the real ids (a code name
# an Admin picks is stored as its real id via resolve_model()). Absent or
# empty = no mapping = exactly the prior behavior. Fail-open: a malformed
# pair is logged and skipped, never fatal.
# ---------------------------------------------------------------------------

# A code name rides in the UI, the system prompt and a prompt file name:
# letters, digits, dots, underscores and hyphens (a model id minus colons).
_CODENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def parse_model_codenames(raw) -> dict:
    """AGENT_MODEL_CODENAMES -> {real_id: code_name}. Comma-separated
    real-id:code-name pairs, whitespace trimmed, split on the LAST colon (a
    model id may contain one, e.g. Bedrock's '...-v1:0'). A pair is skipped
    and logged when it has no colon or an empty side, a real id the model
    setting would refuse, a code name outside _CODENAME_RE, or a real id or
    code name an earlier pair already uses (either way round, any case) — so
    each code name maps back to exactly one real id. Never raises."""
    mapping = {}
    try:
        taken = set()
        for chunk in str(raw or "").split(","):
            pair = chunk.strip()
            if not pair:
                continue
            real, sep, code = pair.rpartition(":")
            real, code = real.strip(), code.strip()
            if not sep or not real or not code:
                problem = "expected real-id:code-name"
            elif not _MODEL_RE.match(real):
                problem = "the model id has characters a model id cannot have"
            elif not _CODENAME_RE.match(code):
                problem = ("a code name may only contain letters, digits, dots, "
                           "underscores and hyphens")
            elif {real.lower(), code.lower()} & taken:
                problem = "the model id or code name is already used by an earlier pair"
            else:
                taken |= {real.lower(), code.lower()}
                mapping[real] = code
                continue
            logger.warning(f"AGENT_MODEL_CODENAMES: ignoring {pair!r} ({problem})")
    except Exception as e:
        logger.error(f"AGENT_MODEL_CODENAMES could not be read ({e}); "
                     "keeping the pairs read so far")
    return mapping


MODEL_CODENAMES = parse_model_codenames(AGENT_MODEL_CODENAMES)
if MODEL_CODENAMES:
    logger.info(f"model code names active: {MODEL_CODENAMES}")


def display_model(model_id):
    """The name to SHOW for a model id: its code name when mapped, else the
    id unchanged. Never feed the result to the SDK or the settings store."""
    if not MODEL_CODENAMES or not isinstance(model_id, str):
        return model_id
    return MODEL_CODENAMES.get(model_id.strip(), model_id)


def resolve_model(name) -> str:
    """The reverse, for what an Admin types or picks: a code name (any case)
    becomes its real id; anything else — an unmapped real id, '' to clear —
    comes back trimmed and unchanged."""
    m = str(name or "").strip()
    low = m.lower()
    for real, code in MODEL_CODENAMES.items():
        if code.lower() == low:
            return real
    return m


def mask_model_ids(text):
    """display_model() for free text — an error message the UI shows. A
    mapped id is replaced only where it stands as a whole id: not inside a
    longer one (claude-opus-5 in claude-opus-5-5), though a sentence's
    closing period may follow it."""
    if not MODEL_CODENAMES or not isinstance(text, str) or not text:
        return text
    ids = sorted(MODEL_CODENAMES, key=len, reverse=True)
    pattern = re.compile(r"(?<![A-Za-z0-9._-])(?:" + "|".join(map(re.escape, ids))
                         + r")(?![A-Za-z0-9_-]|[.:][A-Za-z0-9])")
    return pattern.sub(lambda m: display_model(m.group(0)), text)


def model_suggestions() -> list:
    """The Admin pickers' suggestions while code names are on: the code
    names, in .env order. [] when off (the UI keeps its built-in list)."""
    return list(MODEL_CODENAMES.values())


def codename_prompt_line(model_id) -> str:
    """The one system-prompt line for a turn on a mapped model; '' for an
    unmapped one, whose prompt stays byte-identical."""
    code = MODEL_CODENAMES.get(str(model_id or "").strip())
    if not code:
        return ""
    return (f'Your model name is "{code}". If asked which model, AI or provider '
            f"powers you, say {code}; never name the underlying model, its maker "
            "or its provider.")


# Where the LAST ensure_anthropic_key() call resolved the key from. The bare
# anthropic_key_present bool can't answer "is this client on their own key?"
# — relay mode stuffs the tenant LicenseKey into ANTHROPIC_API_KEY, so it is
# always true. "none" until the first call (or when nothing resolved).
_anthropic_key_source = "none"


def anthropic_key_source() -> str:
    return _anthropic_key_source


def _byok_anthropic_key():
    """BYOK reads, reimplemented from api_keys_config (which imports Flask —
    absent from the isolated aihub-agent env): the data/byok_config.json gate
    plus USER_ANTHROPIC_API_KEY (api_keys_config.ANTHROPIC_API_KEY_SECRET)
    from the encrypted local store. api_keys_config._get_config_file_path()
    falls back to a RELATIVE './data', which only works from the Flask app's
    cwd — The Agent runs from data/agent/workspace, so anchor on APP_ROOT.
    Returns (byok_enabled, key_or_empty); never raises.
    """
    try:
        path = os.path.join(
            os.getenv("AIHUB_DATA_DIR") or os.path.join(APP_ROOT, "data"),
            "byok_config.json")
        with open(path, "r", encoding="utf-8") as fh:
            enabled = bool(json.load(fh).get("byok_enabled", False))
    except Exception:
        return False, ""
    if not enabled:
        return False, ""
    try:
        from local_secrets import get_local_secret
        return True, (get_local_secret("USER_ANTHROPIC_API_KEY") or "").strip()
    except Exception as e:
        logger.error(f"BYOK is enabled but the local secret store could not "
                     f"be read: {e}")
        return True, ""


def ensure_anthropic_key() -> bool:
    global _anthropic_key_source
    # BYOK (highest precedence): the client's OWN Anthropic key, saved in
    # Settings -> API Keys. When present it beats every other source — the
    # relay AND a hand-pinned ANTHROPIC_API_KEY — because the client's
    # explicit choice to run on their own key/bill wins. A key that is
    # configured but rejected upstream must surface Anthropic's 401, never
    # silently fall back onto the relay (that would bill us while the client
    # believes they are on their own key); only an EMPTY key slot falls
    # through to the sources below.
    byok_on, byok_key = _byok_anthropic_key()
    if byok_key:
        os.environ["ANTHROPIC_API_KEY"] = byok_key
        # This runs per turn, so a mid-process flip from relay mode would
        # otherwise leave the relay base URL in the env — and a real
        # sk-ant-... key posted to the relay fails the tenant-LicenseKey
        # lookup with a 401. Clear it: BYOK talks to api.anthropic.com.
        os.environ.pop("ANTHROPIC_BASE_URL", None)
        _anthropic_key_source = "byok"
        logger.info("Anthropic key source: BYOK (client's own key from the "
                    "local secret store; direct api.anthropic.com)")
        return True
    if byok_on:
        logger.warning("BYOK is enabled but no Anthropic key is saved in "
                       "Settings -> API Keys — falling back to the next "
                       "configured source (relay/env/encrypted)")
    # RELAY MODE (production posture): route the SDK through the aihub-api
    # Anthropic relay instead of holding a raw Anthropic key on this box.
    # The tenant LicenseKey authenticates; the cloud swaps in the real key
    # and meters usage. Default OFF — behavior is byte-identical unless the
    # flag is set. AGENT_RELAY_URL overrides the base (e.g. a local relay
    # instance during testing); it defaults to AI_HUB_API_URL.
    if os.getenv("AGENT_ANTHROPIC_RELAY", "false").lower() == "true":
        relay_base = (os.getenv("AGENT_RELAY_URL")
                      or os.getenv("AI_HUB_API_URL") or "").rstrip("/")
        tenant_key = os.getenv("API_KEY", "")
        if not relay_base or not tenant_key:
            logger.error("AGENT_ANTHROPIC_RELAY=true but AGENT_RELAY_URL/"
                         "AI_HUB_API_URL or API_KEY is missing — falling back "
                         "to the direct key path")
        else:
            os.environ["ANTHROPIC_BASE_URL"] = f"{relay_base}/api/agent-llm"
            os.environ["ANTHROPIC_API_KEY"] = tenant_key
            _anthropic_key_source = "relay"
            logger.info(f"Anthropic RELAY mode: base {relay_base}/api/agent-llm "
                        "(tenant-key auth; no raw Anthropic key on this box)")
            return True
    if os.getenv("ANTHROPIC_API_KEY"):
        _anthropic_key_source = "env"
        return True
    enc = os.getenv("ANTHROPIC_API_KEY_ENCRYPTED")
    if not enc:
        logger.warning("No ANTHROPIC_API_KEY or ANTHROPIC_API_KEY_ENCRYPTED configured")
        _anthropic_key_source = "none"
        return False
    try:
        from encrypt import decrypt_value, ENCRYPTION_KEY
        plain = decrypt_value(enc, ENCRYPTION_KEY)
        if plain:
            os.environ["ANTHROPIC_API_KEY"] = plain
            _anthropic_key_source = "encrypted"
            logger.info("ANTHROPIC_API_KEY decrypted and exported for the SDK")
            return True
    except Exception as e:
        logger.error(f"Failed to decrypt ANTHROPIC_API_KEY_ENCRYPTED: {e}")
    _anthropic_key_source = "none"
    return False


def summary() -> dict:
    """Startup summary for logs / the health route (never includes secrets)."""
    return {
        "service": "agent_service",
        "host": HOST,
        "port": PORT,
        "model": get_effective_model(),
        "model_default": AGENT_MODEL,
        "model_role1": get_effective_model(role=1),
        "model_role1_default": AGENT_MODEL_ROLE1,
        "turns_per_day": get_turn_cap(),
        "app_version": APP_VERSION,
        "app_root": APP_ROOT,
        "main_app": get_base_url(),
        "allow_all_users": AGENT_ALLOW_ALL_USERS,
        "anthropic_key_present": bool(os.getenv("ANTHROPIC_API_KEY")
                                      or os.getenv("ANTHROPIC_API_KEY_ENCRYPTED")),
        # byok | relay | env | encrypted | none — reflects the LAST
        # ensure_anthropic_key() call ("none" until the first turn).
        "anthropic_key_source": anthropic_key_source(),
    }


logger.info(f"agent_config loaded: {json.dumps(summary())}")
