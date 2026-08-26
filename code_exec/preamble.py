"""
Script preamble injected ahead of LLM-authored code, plus the paths that
parameterize it.

The preamble (child-side, stdlib-only):
  * keeps matplotlib headless and exposes the bundle's native DLL dir;
  * puts the aihub_runtime SDK and the shared ad-hoc package cache on sys.path;
  * defines ``install("pkg")`` — the DEFAULT-OPEN runtime installer
    (docs/code-interpreter-unification-plan.md §5.1): anything on PyPI installs
    into the shared per-interpreter cache unless it is on the observation-driven
    denylist; a pip constraints file pins the core stack so an install can never
    move numpy/pandas out from under everyone; every install is logged.
"""

import hashlib
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_REQ_DIR_REL = ("agent_environments", "python-bundle-requirements")
_DENYLIST_NAME = "code_interpreter_package_denylist.txt"
_CONSTRAINTS_NAME = "code_interpreter_constraints.txt"


def _app_root() -> Path:
    app_root = os.environ.get("APP_ROOT")
    if app_root and Path(app_root).is_dir():
        return Path(app_root)
    # source tree: this file lives at <root>/code_exec/preamble.py
    return Path(__file__).resolve().parents[1]


def policy_files() -> Tuple[Optional[str], Optional[str]]:
    """(denylist_path, constraints_path) — either may be None if absent."""
    base = _app_root()
    deny = base.joinpath(*_REQ_DIR_REL, _DENYLIST_NAME)
    cons = base.joinpath(*_REQ_DIR_REL, _CONSTRAINTS_NAME)
    return (str(deny) if deny.is_file() else None,
            str(cons) if cons.is_file() else None)


def adhoc_package_dir(python_exe: str) -> str:
    """Per-interpreter ad-hoc package cache (persistent across runs, shared
    across sessions; delete the folder = factory reset). Lives beside the
    automations package cache so runtime installs converge on one place."""
    tag = "py"
    try:
        v = subprocess.run([python_exe, "-c", "import sys;print('%d%d' % sys.version_info[:2])"],
                           capture_output=True, text=True, timeout=20)
        if v.returncode == 0 and v.stdout.strip().isdigit():
            tag = "py" + v.stdout.strip()
    except Exception:
        pass
    h = hashlib.sha1(os.path.abspath(python_exe).lower().encode("utf-8")).hexdigest()[:8]
    return str(_app_root() / "automations" / "_pkg_cache" / "_adhoc" / f"{tag}_{h}")


def build_preamble(sdk_dir: Optional[str] = None,
                   pkg_dir: Optional[str] = None,
                   denylist_path: Optional[str] = None,
                   constraints_path: Optional[str] = None) -> str:
    """Return the preamble source to prepend to the user code."""
    return f'''# --- AI Hub code-interpreter preamble (auto-generated) ---
import os as _os, sys as _sys
_os.environ.setdefault('MPLBACKEND', 'Agg')
try:
    _libbin = _os.path.join(_os.path.dirname(_sys.executable), 'Library', 'bin')
    if hasattr(_os, 'add_dll_directory') and _os.path.isdir(_libbin):
        _os.add_dll_directory(_libbin)
except Exception:
    pass
_AIHUB_SDK_DIR = {sdk_dir!r}
_AIHUB_PKG_DIR = {pkg_dir!r}
_AIHUB_DENYLIST = {denylist_path!r}
_AIHUB_CONSTRAINTS = {constraints_path!r}
if _AIHUB_SDK_DIR and _os.path.isdir(_AIHUB_SDK_DIR) and _AIHUB_SDK_DIR not in _sys.path:
    _sys.path.insert(0, _AIHUB_SDK_DIR)
if _AIHUB_PKG_DIR:
    try:
        _os.makedirs(_AIHUB_PKG_DIR, exist_ok=True)
        if _AIHUB_PKG_DIR not in _sys.path:
            _sys.path.insert(0, _AIHUB_PKG_DIR)
    except Exception:
        pass


def install(*packages):
    """Install PyPI package(s) for this and future runs: install("pmdarima").
    Returns True on success. Blocked only by the platform's package denylist."""
    import json as _json, re as _re, subprocess as _sp, time as _time
    if not packages:
        print("install(): give at least one package name"); return False
    if not _AIHUB_PKG_DIR:
        print("install(): no package directory configured for this run"); return False
    denied = set()
    try:
        if _AIHUB_DENYLIST and _os.path.isfile(_AIHUB_DENYLIST):
            for _ln in open(_AIHUB_DENYLIST, encoding='utf-8'):
                _ln = _ln.split('#', 1)[0].strip()
                if _ln:
                    denied.add(_ln.lower())
    except Exception:
        pass
    names = [str(p).strip() for p in packages if str(p).strip()]
    bad = [p for p in names
           if _re.split(r'[<>=!~\\[; ]', p, 1)[0].strip().lower() in denied]
    if bad:
        print("install(): blocked by the platform package denylist: " + ", ".join(bad))
        return False
    cmd = [_sys.executable, '-m', 'pip', 'install', '--no-input',
           '--disable-pip-version-check', '--target', _AIHUB_PKG_DIR]
    if _AIHUB_CONSTRAINTS and _os.path.isfile(_AIHUB_CONSTRAINTS):
        cmd += ['-c', _AIHUB_CONSTRAINTS]
    cmd += names
    try:
        _r = _sp.run(cmd, capture_output=True, text=True, timeout=600)
    except Exception as _e:
        print(f"install() failed to launch pip: {{_e}}"); return False
    try:
        with open(_os.path.join(_AIHUB_PKG_DIR, '_install_log.jsonl'), 'a', encoding='utf-8') as _f:
            _f.write(_json.dumps({{'ts': _time.time(), 'packages': names,
                                   'ok': _r.returncode == 0}}) + '\\n')
    except Exception:
        pass
    if _r.returncode != 0:
        print("install() failed:\\n" + ((_r.stderr or _r.stdout or '')[-2000:]))
        return False
    import importlib as _il
    _il.invalidate_caches()
    print("install(): installed " + ", ".join(names))
    return True
# --- end preamble ---
'''
