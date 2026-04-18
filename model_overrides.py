"""
model_overrides.py
------------------
Persists admin-UI-set LLM model overrides to data/model_overrides.json and
applies them to os.environ at startup so config.py's existing env-first
resolution picks them up.

There are ONLY 6 override keys, enumerated in KEY_TO_ENV_VARS below. Each
key maps to one or more environment variable names that config.py reads.

Resolution order (unchanged from prior work):
    user_config.py    > .env / Windows env > config.py default
Where this module fits in:
    data/model_overrides.json is read at startup and used to set the env
    vars in os.environ BEFORE config.py reads them via os.getenv. Because
    apply_overrides_to_env() uses os.environ[...] = value (which wins over
    any existing Windows/.env value on that process), an admin-set override
    is effectively a .env-equivalent source of truth for that env var.

A blank/empty override value is treated as "no override" — the env var is
NOT set by this module in that case, so the existing .env/Windows env/config
default chain remains intact.

Used by:
    - config.py: calls apply_overrides_to_env() right after load_dotenv()
    - api_keys_config.py: GET/POST/DELETE /api/api-keys/model-overrides routes
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# File location — data/model_overrides.json in the app root.
# -----------------------------------------------------------------------------
_APP_ROOT = Path(os.getenv('APP_ROOT', '')) if os.getenv('APP_ROOT') else None
if _APP_ROOT is None or not _APP_ROOT.is_dir():
    _APP_ROOT = Path(__file__).resolve().parent

OVERRIDES_PATH = _APP_ROOT / 'data' / 'model_overrides.json'


# -----------------------------------------------------------------------------
# Allow-list and env-var mapping.
#
# Each override key → list of env vars that should be set from it.
# This is the single source of truth for what gets overridden.
# -----------------------------------------------------------------------------
KEY_TO_ENV_VARS: Dict[str, List[str]] = {
    # OpenAI-direct AND Azure deployment share the same model name by convention.
    # NOTE: config.py reads the OpenAI-direct model from env var 'OPENAI_MODEL'
    # (NOT 'OPENAI_DEPLOYMENT_NAME' — that's the config attribute name), so
    # the env var names below intentionally differ between direct and Azure.
    'openai_primary': [
        'OPENAI_MODEL',
        'AZURE_OPENAI_DEPLOYMENT_NAME',
        'AZURE_OPENAI_DEPLOYMENT_NAME_ALTERNATE',
    ],
    'openai_mini': [
        'OPENAI_MODEL_MINI',
        'AZURE_OPENAI_DEPLOYMENT_NAME_MINI',
        'AZURE_OPENAI_DEPLOYMENT_NAME_ALTERNATE_MINI',
    ],
    'openai_vision': [
        'OPENAI_VISION_MODEL',
    ],
    'openai_embedding': [
        'AZURE_OPENAI_DEPLOYMENT_NAME_EMBEDDING',
    ],
    'openai_image': [
        'CC_IMAGE_MODEL',
    ],
    'anthropic_primary': [
        'ANTHROPIC_MODEL',
        'ANTHROPIC_ADVANCED',
    ],
    'anthropic_mini': [
        'ANTHROPIC_MINI',
    ],
}

ALLOWED_KEYS = frozenset(KEY_TO_ENV_VARS.keys())


# -----------------------------------------------------------------------------
# Load / save / clear
# -----------------------------------------------------------------------------
def load_overrides() -> Dict[str, str]:
    """Return the dict persisted in data/model_overrides.json, or {} if missing."""
    if not OVERRIDES_PATH.exists():
        return {}
    try:
        with OVERRIDES_PATH.open('r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning(f"{OVERRIDES_PATH} is not a JSON object; ignoring")
            return {}
        # Keep only known keys and string values; drop anything else.
        return {
            k: ('' if v is None else str(v))
            for k, v in data.items()
            if k in ALLOWED_KEYS
        }
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to read {OVERRIDES_PATH}: {e}")
        return {}


def save_overrides(overrides: Dict[str, Any]) -> Dict[str, str]:
    """Persist overrides. Accepts a partial dict (only given keys updated).
    Unknown keys raise ValueError. Returns the new full dict after merging.
    """
    bad = [k for k in overrides if k not in ALLOWED_KEYS]
    if bad:
        raise ValueError(f"Unknown override keys: {sorted(bad)}")

    merged = load_overrides()
    for k, v in overrides.items():
        merged[k] = '' if v is None else str(v).strip()

    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Write atomically — temp file + rename — so we don't leave a partial file
    # if we crash mid-write.
    tmp_path = OVERRIDES_PATH.with_suffix('.json.tmp')
    with tmp_path.open('w', encoding='utf-8') as f:
        json.dump(merged, f, indent=2, sort_keys=True)
    tmp_path.replace(OVERRIDES_PATH)
    logger.info(f"Wrote {OVERRIDES_PATH} ({len([v for v in merged.values() if v])} active override(s))")
    return merged


def clear_overrides() -> None:
    """Delete the overrides file entirely."""
    try:
        OVERRIDES_PATH.unlink()
        logger.info(f"Deleted {OVERRIDES_PATH}")
    except FileNotFoundError:
        pass


# -----------------------------------------------------------------------------
# Startup hook — populate os.environ from the file.
# -----------------------------------------------------------------------------
def apply_overrides_to_env() -> Dict[str, str]:
    """Read data/model_overrides.json and set os.environ[<env_var>] for every
    override that has a non-empty value. Empty/missing keys are skipped so
    existing .env / Windows env / config defaults continue to apply.

    Call this ONCE at startup, immediately after load_dotenv() and before any
    code reads the affected env vars.

    Returns a dict of {env_var: value} that was actually set, for logging.
    """
    overrides = load_overrides()
    applied: Dict[str, str] = {}
    for key, value in overrides.items():
        if not value:
            continue
        for env_var in KEY_TO_ENV_VARS.get(key, []):
            os.environ[env_var] = value
            applied[env_var] = value
    if applied:
        logger.info(f"Applied {len(applied)} model-override env var(s) from {OVERRIDES_PATH}")
    return applied


# -----------------------------------------------------------------------------
# Status — used by the admin UI.
# -----------------------------------------------------------------------------
def get_override_status() -> Dict[str, Any]:
    """Return the payload the admin UI needs:
        - overrides:        the JSON file contents (user intent)
        - effective_values: what each role actually resolves to right now
                            (by checking the first env var in the mapping)
        - defaults:         the config.py default for each role (what you'd
                            get if you cleared every override and restarted)
        - dropdowns:        supported-model choices per role
        - restart_required: True if the file on disk differs from what's
                            currently loaded in os.environ (admin should
                            restart services for changes to take effect)
    """
    # Import lazily to avoid circular imports
    from supported_models import DROPDOWNS

    overrides = load_overrides()

    # Look up the "effective" value for each role by reading the first env
    # var in its mapping. If not set, report blank (config default will fill in).
    effective: Dict[str, str] = {}
    restart_required = False
    for key, env_vars in KEY_TO_ENV_VARS.items():
        first_env = env_vars[0] if env_vars else None
        current = os.environ.get(first_env, '') if first_env else ''
        effective[key] = current
        # If the file disagrees with what's live, a restart is needed.
        desired = overrides.get(key, '')
        if desired and desired != current:
            restart_required = True
        if not desired and current and _value_came_from_previous_override(key, current):
            # overrides file was cleared but the old value is still live
            restart_required = True

    # "Defaults" == what's in the env right now if NO override were active.
    # We can't know that perfectly without restarting, but we approximate by
    # reading the first env var and flagging it as "current effective".
    # For a cleaner UX we also expose the bare config.py defaults.
    defaults = _read_config_defaults()

    # BYOK state — used by the UI to decide whether to show the extra warning.
    try:
        from api_keys_config import is_using_byok_openai, is_using_byok_anthropic
        byok_openai = bool(is_using_byok_openai())
        byok_anthropic = bool(is_using_byok_anthropic())
    except Exception:
        byok_openai = False
        byok_anthropic = False

    return {
        'overrides': overrides,
        'effective_values': effective,
        'defaults': defaults,
        'dropdowns': DROPDOWNS,
        'byok_openai_enabled': byok_openai,
        'byok_anthropic_enabled': byok_anthropic,
        'restart_required': restart_required,
        'any_override_active': any(bool(v) for v in overrides.values()),
    }


def _read_config_defaults() -> Dict[str, str]:
    """Return config.py's current in-memory values for each role.
    These reflect the currently-running resolution (env + overrides + code
    defaults), not the bare code defaults. For UI display as "current value".
    """
    try:
        import config as cfg
    except Exception:
        return {k: '' for k in KEY_TO_ENV_VARS}

    # CC_IMAGE_MODEL lives in command_center_service/cc_config.py, not the
    # main config.py. Read it lazily; OK if CC service isn't importable here.
    cc_image_model = ''
    try:
        import sys, os as _os
        _cc_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'command_center_service')
        if _cc_path not in sys.path:
            sys.path.insert(0, _cc_path)
        import cc_config  # type: ignore
        cc_image_model = getattr(cc_config, 'CC_IMAGE_MODEL', '') or _os.environ.get('CC_IMAGE_MODEL', '')
    except Exception:
        cc_image_model = os.environ.get('CC_IMAGE_MODEL', '')

    return {
        'openai_primary':    getattr(cfg, 'OPENAI_DEPLOYMENT_NAME', '') or getattr(cfg, 'AZURE_OPENAI_DEPLOYMENT_NAME', ''),
        'openai_mini':       getattr(cfg, 'OPENAI_DEPLOYMENT_NAME_MINI', '') or getattr(cfg, 'AZURE_OPENAI_DEPLOYMENT_NAME_MINI', ''),
        'openai_vision':     getattr(cfg, 'OPENAI_VISION_MODEL', ''),
        'openai_embedding':  getattr(cfg, 'AZURE_OPENAI_DEPLOYMENT_NAME_EMBEDDING', ''),
        'openai_image':      cc_image_model,
        'anthropic_primary': getattr(cfg, 'ANTHROPIC_ADVANCED', '') or getattr(cfg, 'ANTHROPIC_MODEL', ''),
        'anthropic_mini':    getattr(cfg, 'ANTHROPIC_MINI', ''),
    }


def _value_came_from_previous_override(key: str, current_value: str) -> bool:
    """Heuristic used by get_override_status(): if the current env value doesn't
    match the config.py default AND there's no override set right now, it
    probably came from a previously-applied override that was cleared but the
    process hasn't restarted. This makes the UI's "Restart required" banner
    show up after a delete.

    This is best-effort — it's perfectly possible for a user to set a matching
    .env override, in which case we'll incorrectly suggest a restart. That's
    acceptable: false positives are safer than false negatives for this UX.
    """
    # Defer import — config may not always be importable in arbitrary contexts.
    try:
        import config as cfg
    except Exception:
        return False

    # Baseline default is whatever config.py would have resolved to without
    # any override in os.environ. We can't cheaply compute that without
    # reloading, so approximate by checking against hardcoded literals in
    # config.py (read via getattr with known fallback values).
    HARDCODED_DEFAULTS = {
        'openai_primary':    'gpt-5.2',
        'openai_mini':       'gpt-5.4-mini',
        'openai_vision':     'gpt-4o',
        'openai_embedding':  'text-embedding-3-small',
        'openai_image':      'dall-e-3',
        'anthropic_primary': 'claude-opus-4-6',
        'anthropic_mini':    'claude-sonnet-4-6',
    }
    return current_value != HARDCODED_DEFAULTS.get(key, '')
