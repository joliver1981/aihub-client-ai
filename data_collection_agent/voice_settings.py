"""
Voice mode settings resolver.

Settings are configurable at three levels (most-specific wins):

    1. JWT prefill `voice` claim                   — per-session override
    2. Schema's top-level `voice` block            — per agent/form
    3. App-level `_app_voice.json` or env vars     — per deployment
    4. Hardcoded defaults                          — fallback

This mirrors the branding resolution pattern so the experience is consistent
for solution authors and admins.

Settings:
    default_on               — auto-enable voice mode on session start
    auto_listen              — re-open the mic automatically after the AI
                               finishes speaking (hands-free turn-taking)
    silence_threshold_ms     — how many ms of silence after the user stops
                               speaking before we auto-stop the mic and send
                               the transcript
    listen_timeout_ms        — max time to keep the mic open waiting for the
                               user to start speaking
    auto_listen_only_when_collecting
                             — only auto-open the mic when the agent is in a
                               data-collection phase (not at submission /
                               review). Defaults to True; turning this off
                               makes the mic re-open after every AI turn.
"""

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

HARDCODED_DEFAULTS: Dict[str, Any] = {
    'default_on':                       False,
    'auto_listen':                      True,
    'silence_threshold_ms':             1500,
    'listen_timeout_ms':                30000,
    'auto_listen_only_when_collecting': True,
}

CONFIGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'configs')
APP_VOICE_FILE = os.path.join(CONFIGS_DIR, '_app_voice.json')

ALLOWED_KEYS = set(HARDCODED_DEFAULTS.keys())


# ---------- Type-coercion helpers ----------
def _to_bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ('1', 'true', 'yes', 'on')
    if isinstance(v, (int, float)):
        return bool(v)
    return None


def _to_int(v: Any, lo: int = 0, hi: int = 600_000) -> Optional[int]:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, n))


_COERCERS = {
    'default_on':                       _to_bool,
    'auto_listen':                      _to_bool,
    'silence_threshold_ms':             _to_int,
    'listen_timeout_ms':                _to_int,
    'auto_listen_only_when_collecting': _to_bool,
}


def _coerce(key: str, value: Any) -> Any:
    fn = _COERCERS.get(key)
    if not fn:
        return None
    out = fn(value)
    return out


# ---------- Loaders ----------
def load_app_voice_settings() -> Dict[str, Any]:
    """App-level settings from configs/_app_voice.json plus DCA_VOICE_*
    env-var overrides."""
    out: Dict[str, Any] = {}

    # 1. JSON file
    if os.path.exists(APP_VOICE_FILE):
        try:
            with open(APP_VOICE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f) or {}
            if isinstance(data, dict):
                for k in ALLOWED_KEYS:
                    if k in data:
                        coerced = _coerce(k, data[k])
                        if coerced is not None:
                            out[k] = coerced
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in {APP_VOICE_FILE}: {e}")
        except Exception as e:
            logger.warning(f"Could not read {APP_VOICE_FILE}: {e}")

    # 2. Env vars
    env_map = {
        'default_on':                       'DCA_VOICE_DEFAULT_ON',
        'auto_listen':                      'DCA_VOICE_AUTO_LISTEN',
        'silence_threshold_ms':             'DCA_VOICE_SILENCE_MS',
        'listen_timeout_ms':                'DCA_VOICE_LISTEN_TIMEOUT_MS',
        'auto_listen_only_when_collecting': 'DCA_VOICE_AUTO_LISTEN_COLLECTING_ONLY',
    }
    for key, env_name in env_map.items():
        raw = os.getenv(env_name)
        if raw is None or raw == '':
            continue
        coerced = _coerce(key, raw)
        if coerced is not None:
            out[key] = coerced

    return out


def _from_block(block: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract & coerce allowed keys from a `voice` block."""
    out: Dict[str, Any] = {}
    if not isinstance(block, dict):
        return out
    for k in ALLOWED_KEYS:
        if k in block:
            coerced = _coerce(k, block[k])
            if coerced is not None:
                out[k] = coerced
    return out


def resolve_voice_settings(
    schema: Optional[Dict[str, Any]] = None,
    jwt_claims: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Walk the override hierarchy and return a fully-resolved settings dict.
    All keys in HARDCODED_DEFAULTS are guaranteed to be present in the result.
    """
    resolved: Dict[str, Any] = dict(HARDCODED_DEFAULTS)

    # 3. App-level
    resolved.update(load_app_voice_settings())

    # 2. Schema-level
    if schema:
        resolved.update(_from_block(schema.get('voice')))

    # 1. JWT claim — most specific
    if jwt_claims:
        resolved.update(_from_block(jwt_claims.get('voice')))

    return resolved
