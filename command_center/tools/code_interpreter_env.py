"""
Command Center — Code Interpreter environment provisioning
==========================================================
The shipped ``python-bundle`` carries only the baked-in CORE stack
(numpy/pandas/matplotlib/openpyxl) to keep the installer lean. The richer
data-science extras (scipy, scikit-learn, seaborn, statsmodels, requests,
beautifulsoup4, lxml) are installed INTO the bundle at CC service startup, in a
background thread, so they are ready before the first chat that needs them
without bloating the installer. See code_interpreter_requirements.txt.

Mechanism (works on a stock client — no conda, no external interpreter):
  the bundle python + ``os.add_dll_directory(<bundle>\\Library\\bin)`` (the
  conda-extracted bundle keeps its native stdlib DLLs there) → bootstrap pip via
  get-pip.py if absent → ``pip install -r code_interpreter_requirements.txt``.

Properties:
  * Idempotent — a probe-import skips all work when the extras already import.
  * Non-fatal — every failure is logged and swallowed; the baked-in core still
    works, so the code interpreter degrades gracefully (charts/Excel keep
    working; only the extras are unavailable until a later successful run).
  * Bundle-only — never touches a user-configured external interpreter
    (CODE_INTERPRETER_PYTHON pointing at e.g. a developer's conda env is left to
    the operator).
"""
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

# Probe modules whose presence means "extras already provisioned".
PROBE_MODULES = ["scipy", "sklearn", "seaborn", "statsmodels", "requests", "bs4", "lxml"]

_REQ_REL = os.path.join(
    "agent_environments", "python-bundle-requirements", "code_interpreter_requirements.txt"
)

# A tiny driver script run *by the bundle python* so the heavy lifting (DLL dir,
# pip bootstrap, install) happens inside the bundle's own interpreter.
_DRIVER = '''
import os, sys, runpy, importlib
_lib = os.path.join(os.path.dirname(sys.executable), "Library", "bin")
if hasattr(os, "add_dll_directory") and os.path.isdir(_lib):
    try:
        os.add_dll_directory(_lib)
    except Exception:
        pass
mode = sys.argv[1]
if mode == "probe":
    missing = []
    for m in sys.argv[2:]:
        try:
            importlib.import_module(m)
        except Exception:
            missing.append(m)
    sys.stdout.write("MISSING:" + ",".join(missing))
elif mode == "has_pip":
    try:
        import pip  # noqa: F401
        sys.stdout.write("PIP:ok")
    except Exception:
        sys.stdout.write("PIP:no")
elif mode == "getpip":
    sys.argv = ["get-pip.py", "--no-warn-script-location"]
    runpy.run_path(sys.argv0_getpip, run_name="__main__")
elif mode == "install":
    req = sys.argv[2]
    sys.argv = ["pip", "install", "--no-warn-script-location", "--no-input", "-r", req]
    runpy.run_module("pip", run_name="__main__")
'''
_DRIVER = _DRIVER.replace("sys.argv0_getpip", "sys.argv[2]")

_started = False
_lock = threading.Lock()


def _run_driver(python_exe: str, workdir: str, driver: str, *args: str,
                timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        [python_exe, driver, *args],
        cwd=workdir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


def _parse_missing(stdout: Optional[str]) -> List[str]:
    for line in (stdout or "").splitlines():
        if not line.startswith("MISSING:"):
            continue
        rest = line[len("MISSING:"):].strip()
        return [m for m in rest.split(",") if m]
    return []


def _download(url: str, dest: Path) -> bool:
    """Download `url` to `dest` using the CC service process (which has working
    network/ssl), so the bundle python doesn't have to."""
    try:
        import urllib.request
        urllib.request.urlretrieve(url, str(dest))
        return dest.exists() and dest.stat().st_size > 0
    except Exception as e:
        logger.warning("[cc-env] could not download %s: %s", url, e)
        return False


def provision(python_exe: str, requirements_file: str) -> bool:
    """Install the code-interpreter extras into `python_exe`'s environment.

    Idempotent and non-fatal. Returns True if, after the run, all PROBE_MODULES
    import. Public so it can be called directly (e.g. tests / manual runs)
    without the bundle-only startup guard.
    """
    workdir = tempfile.mkdtemp(prefix="cc_env_prov_")
    try:
        driver = Path(workdir) / "_cc_env_driver.py"
        driver.write_text(_DRIVER, encoding="utf-8")

        # 1) Probe — if everything already imports, we're done.
        probe = _run_driver(python_exe, workdir, str(driver), "probe", *PROBE_MODULES, timeout=120)
        missing = _parse_missing(probe.stdout)
        if not missing:
            logger.info("[cc-env] code-interpreter extras already present")
            return True

        logger.info("[cc-env] provisioning code-interpreter extras (missing: %s)", ", ".join(missing))

        # 2) Ensure pip is available in the bundle python.
        has_pip = _run_driver(python_exe, workdir, str(driver), "has_pip", timeout=60)
        if "PIP:ok" not in (has_pip.stdout or ""):
            getpip = Path(workdir) / "get-pip.py"
            if not _download(GET_PIP_URL, getpip):
                return False
            boot = _run_driver(python_exe, workdir, str(driver), "getpip", str(getpip), timeout=300)
            if boot.returncode != 0:
                logger.warning("[cc-env] pip bootstrap failed: %s", (boot.stderr or "")[-800:])
                return False

        # 3) Install the extras.
        inst = _run_driver(python_exe, workdir, str(driver), "install", requirements_file, timeout=1800)
        if inst.returncode != 0:
            logger.warning("[cc-env] extras install exited %s: %s", inst.returncode, (inst.stderr or "")[-800:])

        # 4) Re-probe to confirm.
        probe2 = _run_driver(python_exe, workdir, str(driver), "probe", *PROBE_MODULES, timeout=120)
        still = _parse_missing(probe2.stdout)
        if still:
            logger.warning("[cc-env] still missing after provisioning: %s", ", ".join(still))
            return False

        logger.info("[cc-env] code-interpreter extras provisioned successfully")
        return True
    except Exception as e:
        logger.warning("[cc-env] provisioning error (non-fatal): %s", e)
        return False
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _ensure_main() -> None:
    """Background worker: provision the SHIPPED bundle only."""
    try:
        from command_center.tools.code_interpreter import _resolve_interpreter, _bundle_python
        bundle = _bundle_python()
        if not bundle:
            logger.info("[cc-env] no shipped python-bundle found; skipping provisioning")
            return
        resolved = _resolve_interpreter(None)
        if os.path.abspath(resolved) != os.path.abspath(bundle):
            logger.info("[cc-env] interpreter is external (%s); not provisioning", resolved)
            return
        app_root = os.environ.get("APP_ROOT") or ""
        req = os.path.join(app_root, _REQ_REL)
        if not os.path.isfile(req):
            logger.warning("[cc-env] requirements file not found, skipping: %s", req)
            return
        provision(bundle, req)
    except Exception as e:
        logger.warning("[cc-env] startup provisioning failed (non-fatal): %s", e)


def ensure_async() -> None:
    """Kick off code-interpreter env provisioning in a daemon thread (once).

    Safe to call unconditionally at startup: it returns immediately, never
    blocks the service, and is a no-op when the extras are already installed or
    when an external interpreter is configured.
    """
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_ensure_main, name="cc-env-provision", daemon=True).start()
    logger.info("[cc-env] code-interpreter env provisioning started (background)")
