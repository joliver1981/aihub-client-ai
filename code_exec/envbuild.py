"""
Child-process environment for LLM-authored code: DENYLIST secret-scrub.

Philosophy (docs/code-interpreter-unification-plan.md §5.2): pass everything
through EXCEPT the platform's own secrets. What is dropped:

  * every key name the app's own ``.env`` defines — that set IS the platform's
    config-and-secrets surface, derived automatically from the same files the
    app loads, so the list maintains itself;
  * names matching KEY|SECRET|TOKEN|PASSWORD|PWD|CONN (fixed safety net for
    machine-level vars);
  * PYTHONHOME/PYTHONPATH — correctness, not policy: a frozen parent's values
    corrupt the child interpreter's import system.

Everything else (PATH, proxies, whatever a dev box has) passes untouched.
Deliberate grants — the aihub_runtime run token, SDK/package PYTHONPATH — are
ADDED by the caller via ``extra`` AFTER the scrub: the scrub removes inherited
accidents; ``extra`` is an intentional, scoped, single-run grant.
"""

import logging
import os
import re
from pathlib import Path
from typing import Dict, Iterable, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_SECRET_PATTERN = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|PWD|CONN", re.IGNORECASE)
_ALWAYS_DROP = {"PYTHONHOME", "PYTHONPATH"}
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")

# (path, mtime) -> frozenset of key names, so repeated runs don't re-read .env.
_dotenv_cache: Dict[str, Tuple[float, frozenset]] = {}


def _env_file_candidates() -> Iterable[Path]:
    app_root = os.environ.get("APP_ROOT")
    if app_root:
        yield Path(app_root) / ".env"
        yield Path(app_root) / "dist_env" / ".env"
    yield Path.cwd() / ".env"


def dotenv_key_names() -> Set[str]:
    """Key names defined in the app's .env file(s) — the self-maintaining half
    of the denylist. Never raises; unreadable files contribute nothing."""
    names: Set[str] = set()
    seen_paths = set()
    for path in _env_file_candidates():
        try:
            key = str(path.resolve())
        except Exception:
            key = str(path)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        try:
            if not path.is_file():
                continue
            mtime = path.stat().st_mtime
            cached = _dotenv_cache.get(key)
            if cached and cached[0] == mtime:
                names |= cached[1]
                continue
            found = set()
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                m = _ENV_LINE.match(line)
                if m:
                    found.add(m.group(1))
            _dotenv_cache[key] = (mtime, frozenset(found))
            names |= found
        except Exception as e:
            logger.debug("[code_exec] could not read %s for env scrub: %s", path, e)
    return names


def build_child_env(workdir: str,
                    extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Scrubbed environment for one code run inside ``workdir``."""
    dropped = dotenv_key_names()
    env: Dict[str, str] = {}
    for name, value in os.environ.items():
        if name in _ALWAYS_DROP or name in dropped or _SECRET_PATTERN.search(name):
            continue
        env[name] = value

    # Workdir-scoped basics: temp files become harvestable artifacts and are
    # cleaned up with the workdir; matplotlib stays headless with a private
    # config dir so it never touches (or needs) the user profile.
    mpl_dir = Path(workdir) / ".mpl"
    try:
        mpl_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    env["TEMP"] = workdir
    env["TMP"] = workdir
    env["MPLBACKEND"] = "Agg"
    env["MPLCONFIGDIR"] = str(mpl_dir)
    env["PYTHONIOENCODING"] = "utf-8"

    if extra:
        env.update({k: str(v) for k, v in extra.items() if v is not None})
    return env
