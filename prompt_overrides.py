"""
prompt_overrides.py
-------------------
Persists admin-UI-set SYSTEM PROMPT overrides to data/prompt_overrides.json and
applies them on top of the code defaults at import time.

This is the prompt-level twin of model_overrides.py and follows the same proven
shape: a separate JSON file, an allow-list, an atomic write, and a
"restart required" flag in the admin UI.

DESIGN CONTRACT — this layer is strictly ADDITIVE
=================================================
Nothing in the codebase is modified or removed. Each prompt module keeps its
original constants exactly as written; a small hook at the BOTTOM of the module
(after every definition, and after any existing user_prompts.py loader) asks
this module to overlay admin-set values onto the module globals.

Guarantees, in priority order:

  1. NO FILE  =>  NO-OP. With data/prompt_overrides.json absent, every call
     returns immediately and runtime behavior is byte-for-byte what it was
     before this feature existed. Deleting the file is the kill switch.

  2. FAIL-OPEN, ALWAYS. apply_prompt_overrides() can never raise. A corrupt,
     truncated, half-written, or hand-mangled JSON file leaves the code
     defaults intact. A prompt module that failed to import would take the app
     down, so this function swallows everything and logs.

  3. VALIDATED ON BOTH SIDES. An override is rejected at SAVE time and skipped
     again at LOAD time if it is not a string, or if it drops a {placeholder}
     that the default contains. Call sites do prompt.format(**kwargs); losing a
     placeholder would be a latent KeyError in production.

  4. ONLY REPLACES PLAIN STRINGS. A name is overridden only when it already
     exists in the module and already holds a str. Anything else is skipped.

The hot path (apply_prompt_overrides) deliberately imports NOTHING beyond the
stdlib — no prompt_registry, no config — so that adding the hook to a module
cannot slow down or destabilize any service's startup.

Resolution order for a prompt, lowest to highest precedence:
    code default  <  user_prompts.py (legacy hook)  <  data/prompt_overrides.json

Used by:
    - the bottom-of-module hook in system_prompts.py and friends
    - system_prompts_admin_routes.py : GET/POST/DELETE /settings/api/system-prompts
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# File location — data/prompt_overrides.json in the app root.
# Mirrors model_overrides.py:41-45 so frozen PyInstaller builds resolve to the
# install root, not the temporary extraction directory.
# -----------------------------------------------------------------------------
_APP_ROOT = Path(os.getenv('APP_ROOT', '')) if os.getenv('APP_ROOT') else None
if _APP_ROOT is None or not _APP_ROOT.is_dir():
    _APP_ROOT = Path(__file__).resolve().parent

APP_ROOT = _APP_ROOT
OVERRIDES_PATH = APP_ROOT / 'data' / 'prompt_overrides.json'

KEY_SEPARATOR = '::'

# Local copy of the placeholder pattern. Deliberately NOT imported from
# prompt_registry: the import-time hot path must stay dependency-free so that
# a sub-service can apply overrides even if the repo root isn't importable.
# Matches only simple {identifier} placeholders — the ones str.format() needs —
# and ignores the raw JSON braces that appear throughout these prompts.
_PLACEHOLDER_RE = re.compile(r'(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})')


def _placeholders(text: str) -> set:
    if not isinstance(text, str):
        return set()
    return set(_PLACEHOLDER_RE.findall(text))


def missing_placeholders(default_text: str, new_text: str) -> List[str]:
    """Placeholders present in the default but dropped by the override.

    A non-empty result means the override would break a .format() call site.
    """
    return sorted(_placeholders(default_text) - _placeholders(new_text))


def split_key(key: str) -> Tuple[str, str]:
    """'system_prompts.py::FOO_SYSTEM' -> ('system_prompts.py', 'FOO_SYSTEM')."""
    if KEY_SEPARATOR not in key:
        return ('', key)
    module, name = key.split(KEY_SEPARATOR, 1)
    return (module.replace('\\', '/'), name)


# -----------------------------------------------------------------------------
# Load / save / clear
# -----------------------------------------------------------------------------
def load_overrides() -> Dict[str, str]:
    """Return {key: text} from data/prompt_overrides.json, or {} if absent.

    Never raises. Non-string values and blank keys are dropped here so that
    every consumer downstream can assume a clean dict of str -> str.
    """
    try:
        if not OVERRIDES_PATH.exists():
            return {}
        with OVERRIDES_PATH.open('r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning(f"{OVERRIDES_PATH} is not a JSON object; ignoring")
            return {}
        clean: Dict[str, str] = {}
        for k, v in data.items():
            if not isinstance(k, str) or not k.strip():
                continue
            if not isinstance(v, str):
                # Silently drop non-strings; a prompt is always text.
                logger.warning(f"prompt override {k!r} is not a string; ignoring")
                continue
            clean[k] = v
        return clean
    except (OSError, json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Failed to read {OVERRIDES_PATH}: {e}")
        return {}
    except Exception as e:  # defensive: this must never break an import
        logger.warning(f"Unexpected error reading {OVERRIDES_PATH}: {e}")
        return {}


def save_overrides(overrides: Dict[str, Any]) -> Dict[str, str]:
    """Persist overrides. Accepts a partial dict (only the given keys change).

    An empty-string value CLEARS that key, matching model_overrides semantics.

    Raises ValueError when a key is not in the registry allow-list, when the
    referenced prompt is not editable, or when the new text drops a
    {placeholder} that the default relies on.

    Returns the merged dict actually written.
    """
    from prompt_registry import editable_keys  # lazy: admin path only

    allowed = editable_keys()

    for key, value in overrides.items():
        if key not in allowed:
            raise ValueError(f"Unknown or non-editable prompt key: {key}")
        if value is None or (isinstance(value, str) and not value.strip()):
            continue  # a clear, not a set
        if not isinstance(value, str):
            raise ValueError(f"Prompt override for {key} must be a string")
        missing = missing_placeholders(allowed[key].get('default_text', ''), value)
        if missing:
            raise ValueError(
                f"Override for {key} is missing required placeholder(s): "
                f"{', '.join('{' + m + '}' for m in missing)}. "
                f"The code fills these in at runtime, so they must be kept."
            )

    merged = load_overrides()
    for key, value in overrides.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            merged.pop(key, None)
        else:
            merged[key] = value

    _write_atomic(merged)
    return merged


def _write_atomic(payload: Dict[str, str]) -> None:
    """Temp file + replace, so a crash mid-write can't leave a partial file."""
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = OVERRIDES_PATH.with_suffix('.json.tmp')
    with tmp_path.open('w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, sort_keys=True, ensure_ascii=False)
    tmp_path.replace(OVERRIDES_PATH)
    logger.info(f"Wrote {OVERRIDES_PATH} ({len(payload)} prompt override(s))")


def clear_override(key: str) -> Dict[str, str]:
    """Remove a single override, reverting that prompt to its code default."""
    merged = load_overrides()
    if key in merged:
        merged.pop(key, None)
        _write_atomic(merged)
    return merged


def clear_overrides() -> None:
    """Delete the overrides file entirely — the kill switch."""
    try:
        OVERRIDES_PATH.unlink()
        logger.info(f"Deleted {OVERRIDES_PATH}")
    except FileNotFoundError:
        pass


# -----------------------------------------------------------------------------
# THE HOT PATH — called from the bottom of each prompt module at import time.
# -----------------------------------------------------------------------------
def apply_prompt_overrides(module_globals: dict, namespace: str) -> int:
    """Overlay admin-set prompt overrides onto one module's globals.

    Call this at the very BOTTOM of a prompt module, after every constant is
    defined and after any existing user_*.py loader:

        from prompt_overrides import apply_prompt_overrides
        apply_prompt_overrides(globals(), 'system_prompts.py')

    `namespace` is the module's repo-relative path — the same prefix used in
    the catalog key `<namespace>::<NAME>`.

    Safety, restated because this runs inside every service's import:
      * returns 0 immediately when no override file exists
      * only replaces a name that ALREADY exists and ALREADY holds a str
      * re-checks the placeholder contract against the real default that is
        sitting in globals right now — so even a hand-edited JSON file cannot
        introduce a runtime KeyError
      * catches everything; an override problem must never break an import

    Returns the number of prompts actually replaced (0 on any failure).
    """
    try:
        overrides = load_overrides()
        if not overrides:
            return 0

        ns = (namespace or '').replace('\\', '/')
        prefix = ns + KEY_SEPARATOR
        applied = 0

        for key, new_text in overrides.items():
            if not key.startswith(prefix):
                continue
            name = key[len(prefix):]
            if not name or name not in module_globals:
                continue

            current = module_globals[name]
            if not isinstance(current, str):
                logger.warning(
                    f"prompt override {key}: target is {type(current).__name__}, "
                    f"not a string; skipping")
                continue
            if not isinstance(new_text, str) or not new_text.strip():
                continue

            missing = missing_placeholders(current, new_text)
            if missing:
                logger.warning(
                    f"prompt override {key}: dropped placeholder(s) "
                    f"{missing}; keeping the default to avoid a runtime error")
                continue

            module_globals[name] = new_text
            applied += 1

        if applied:
            logger.info(f"Applied {applied} prompt override(s) to {ns}")
        return applied

    except Exception as e:
        # Deliberately broad: a failure here must leave defaults intact, never
        # propagate out of a module-level import.
        logger.warning(f"apply_prompt_overrides({namespace}) failed, "
                       f"using code defaults: {e}")
        return 0


# -----------------------------------------------------------------------------
# Status — used by the admin UI.
# -----------------------------------------------------------------------------
def get_override_status() -> Dict[str, Any]:
    """Return the payload the admin screen needs:

        overrides:        {key: text} as persisted (admin intent)
        active_count:     how many prompts are currently overridden
        restart_required: True when the file on disk disagrees with what this
                          process actually loaded at import time
        stale_keys:       overrides whose target is no longer in the registry
                          (e.g. the constant was renamed in a later release)
        path:             where the file lives, for support/debugging
    """
    overrides = load_overrides()
    stale: List[str] = []
    restart_required = False

    try:
        from prompt_registry import editable_keys
        allowed = editable_keys()
        for key, text in overrides.items():
            entry = allowed.get(key)
            if entry is None:
                stale.append(key)
                continue
            # The registry reads the SOURCE FILE, so it always reports the code
            # default. If an override exists for a key, this process can only be
            # running it if it was imported after the file was written.
            if not _is_live(entry, text):
                restart_required = True
    except Exception as e:
        logger.debug(f"get_override_status: registry unavailable: {e}")

    return {
        'overrides': overrides,
        'active_count': len(overrides),
        'stale_keys': sorted(stale),
        'restart_required': restart_required,
        'any_override_active': bool(overrides),
        'path': str(OVERRIDES_PATH),
        'exists': OVERRIDES_PATH.exists(),
    }


def _is_live(entry: Dict[str, Any], override_text: str) -> bool:
    """Is this override already in effect in THIS process?

    Best-effort: import the module only if it is already loaded (never import
    it fresh — that could be an expensive or side-effectful service module) and
    compare the live value.
    """
    import sys

    module_path = str(entry.get('module', ''))
    name = str(entry.get('name', ''))
    if not module_path or not name:
        return False

    mod_name = module_path[:-3] if module_path.endswith('.py') else module_path
    mod_name = mod_name.replace('/', '.').replace('\\', '.')

    module = sys.modules.get(mod_name)
    if module is None:
        # Not loaded here (e.g. a Command Center prompt viewed from the web
        # app). We cannot observe it, so don't claim a restart is pending.
        return True

    live = getattr(module, name, None)
    return isinstance(live, str) and live == override_text


if __name__ == '__main__':  # pragma: no cover - manual inspection helper
    import json as _json
    print(_json.dumps(get_override_status(), indent=2)[:2000])
