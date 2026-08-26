"""
Which Python executes user code. Shared by every surface.

Resolution order:
  1. ``prefer`` — caller-supplied interpreter for special contexts (e.g. the
     agent's assigned custom-environment venv, surfaced to the tool by
     agent_environment_executor via AIHUB_AGENT_ENV_PYTHON) — only if it exists.
  2. ``explicit`` argument, then CODE_INTERPRETER_PYTHON — operator override.
     A stale path (a developer's conda env baked into a client .env) falls
     through with a warning instead of being used blindly.
  3. The shipped ``{APP_ROOT}\\agent_environments\\python-bundle\\python.exe`` —
     a real standalone CPython, NOT the frozen service exe.
  4. ``sys.executable`` — ONLY when the process is not frozen. Under a
     PyInstaller build sys.executable is the service bootloader: launching it
     with a script path re-runs the service and silently ignores the user's
     code (a false "success"). Frozen with nothing else found resolves to None
     and the caller returns an honest error.
"""

import logging
import os
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_BUNDLE_REL = os.path.join("agent_environments", "python-bundle", "python.exe")

NOT_CONFIGURED_MSG = (
    "Code interpreter is not configured: no usable Python interpreter was found. "
    "Set CODE_INTERPRETER_PYTHON to a Python with the data-science stack, or ensure "
    "the bundled Python at agent_environments/python-bundle is installed."
)


def bundle_python() -> Optional[str]:
    """The shipped portable-Python bundle under APP_ROOT, if it exists."""
    app_root = os.environ.get("APP_ROOT")
    if app_root:
        cand = Path(app_root) / _BUNDLE_REL
        if cand.exists():
            return str(cand)
    return None


def resolve_interpreter(explicit: Optional[str] = None,
                        prefer: Optional[str] = None) -> Optional[str]:
    """Resolve the interpreter for a code run; None means nothing usable."""
    for cand in (prefer, explicit, os.environ.get("CODE_INTERPRETER_PYTHON")):
        if not cand:
            continue
        if Path(cand).exists():
            return cand
        logger.warning(
            "[code_exec] configured interpreter %r does not exist; falling through", cand)

    bundled = bundle_python()
    if bundled:
        return bundled

    if not getattr(sys, "frozen", False):
        logger.warning(
            "[code_exec] no CODE_INTERPRETER_PYTHON and no python-bundle; using the "
            "service's own interpreter (dev-only fallback)")
        return sys.executable

    return None
