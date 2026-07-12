"""
Command Center — Code Interpreter
====================================
Runs user/LLM-authored Python in a subprocess against a per-call working
directory, returning stdout/stderr PLUS any files the code generates
(charts, CSV/XLSX, etc.) as downloadable artifacts + inline image blocks.

Design (see plan): security is intentionally NOT a boundary here — the app
runs inside the client's environment and code needs full network + filesystem
access to be useful. Isolation is process-level only (subprocess + timeout).
The interpreter PATH is configurable so it can target a full data-science env
(pandas/numpy/matplotlib/...) even when the CC service itself runs under a
leaner env.

Reuses existing platform plumbing:
  * input files  → routes.upload (get_files_for_session / get_file_path /
                   _file_is_accessible_to) — respects per-user ownership.
  * output files → command_center.artifacts ArtifactManager via
                   routes.artifacts._get_artifact_manager().
"""
import asyncio
import base64
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Map a generated file's extension → the artifact "type" we register it as.
_EXT_TO_ARTIFACT_TYPE = {
    ".csv": "csv",
    ".xlsx": "excel",
    ".xls": "excel",
    ".pdf": "pdf",
    ".json": "json",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".txt": "text",
    ".md": "text",
}
# Extensions we render inline as image content blocks.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg"}

# Name of the script file we write the user's code into, inside the workdir.
_SCRIPT_NAME = "_cc_run.py"

# Default cap on streamed stdout/stderr characters returned to the model.
# 0 disables truncation. Overridable via CODE_INTERPRETER_MAX_OUTPUT_CHARS.
_DEFAULT_MAX_STREAM_CHARS = 50000


def _max_stream_chars() -> int:
    """Resolve the stdout/stderr cap from env (0 = unlimited). Resolved at call
    time so it tracks config without a process restart, like _default_timeout()."""
    try:
        val = int(os.environ.get("CODE_INTERPRETER_MAX_OUTPUT_CHARS", str(_DEFAULT_MAX_STREAM_CHARS)))
        return val if val >= 0 else _DEFAULT_MAX_STREAM_CHARS
    except (TypeError, ValueError):
        return _DEFAULT_MAX_STREAM_CHARS


def _truncate(text: str, limit: Optional[int] = None) -> str:
    """Trim very long output, keeping head + tail with a marker in the middle.
    limit None → resolve from config; limit 0 → no truncation."""
    if limit is None:
        limit = _max_stream_chars()
    if limit <= 0:
        return text or ""
    if not text or len(text) <= limit:
        return text or ""
    head = limit * 2 // 3
    tail = limit - head
    omitted = len(text) - head - tail
    return text[:head] + f"\n\n... [output truncated — {omitted:,} characters omitted] ...\n\n" + text[-tail:]


_BUNDLE_REL = os.path.join("agent_environments", "python-bundle", "python.exe")


def _bundle_python() -> Optional[str]:
    """The shipped portable-Python bundle under APP_ROOT, if it exists.

    On a client install APP_ROOT points at the install dir (the installer writes
    it to .env), so this resolves to {app}\\agent_environments\\python-bundle\\
    python.exe — a real CPython with the data-science stack, NOT the frozen
    service exe.
    """
    app_root = os.environ.get("APP_ROOT")
    if app_root:
        cand = Path(app_root) / _BUNDLE_REL
        if cand.exists():
            return str(cand)
    return None


def _resolve_interpreter(explicit: Optional[str] = None) -> str:
    """Resolve which Python actually executes user code, validating existence.

    Order: (1) the caller/configured path, but ONLY if it exists — a stale dev
    path baked into a client's .env (e.g. a developer's personal conda env) must
    fall through, not be used blindly; (2) the shipped python-bundle; (3) the
    current interpreter as a last resort. Callers guard case (3) on frozen
    builds via _interpreter_is_runnable(), since there sys.executable is the
    service bootloader exe and cannot run a passed-in script.
    """
    for cand in (explicit, os.environ.get("CODE_INTERPRETER_PYTHON")):
        if not cand:
            continue
        if Path(cand).exists():
            return cand
        logger.warning(
            "[code_interpreter] configured interpreter %r does not exist; falling back to the bundled Python",
            cand,
        )
    bundled = _bundle_python()
    if bundled:
        return bundled
    return sys.executable


def _interpreter_is_runnable(python_exe: str) -> bool:
    """False when the resolved interpreter is the frozen service exe itself.

    Under a PyInstaller-frozen build sys.executable is the service bootloader,
    not a Python interpreter; launching it with a script path re-runs the
    service and silently ignores the user's code (a false 'success'). Detect
    that so the caller can return a clear error instead.
    """
    if not getattr(sys, "frozen", False):
        return True
    try:
        return os.path.abspath(python_exe) != os.path.abspath(sys.executable)
    except Exception:
        return True


def _default_python() -> str:
    """Back-compat shim: resolve the interpreter with no explicit override."""
    return _resolve_interpreter(None)


def _default_timeout() -> int:
    try:
        return int(os.environ.get("CODE_INTERPRETER_TIMEOUT", "60"))
    except (TypeError, ValueError):
        return 60


def prepare_workdir(session_id: str,
                    user_context: Optional[Dict[str, Any]] = None) -> Tuple[str, List[str]]:
    """Create a temp working directory and copy the user's accessible uploaded
    files into it (so code can open them by their original filename).

    Returns (workdir_path, [copied_filenames]). Never raises on a per-file
    copy error — it just skips that file.
    """
    workdir = tempfile.mkdtemp(prefix="cc_interp_")
    copied = []
    if not session_id:
        return workdir, copied

    uc = user_context or {}
    uid = uc.get("user_id")
    tid = uc.get("tenant_id")
    role = 0
    try:
        role = int(uc.get("role") or 0)
    except (TypeError, ValueError):
        role = 0

    # Seed user uploads (best-effort; failure here must NOT skip artifact
    # seeding below).
    files = []
    get_file_path = None
    _file_is_accessible_to = None
    try:
        from routes.upload import get_files_for_session, get_file_path, _file_is_accessible_to
        files = get_files_for_session(session_id) or []
    except Exception as e:
        logger.warning("[code_interpreter] upload seeding unavailable: " + f"{e}")
        files = []

    for meta in files:
        try:
            # Respect per-user ownership when we have an identity to check against.
            if uid is not None and not _file_is_accessible_to(meta, uid, tid, role):
                continue
            fid = meta.get("file_id")
            src = get_file_path(fid) if fid else None
            if not src or not Path(src).exists():
                continue
            name = meta.get("filename") or Path(src).name
            dest = Path(workdir) / _safe_name(name)
            shutil.copyfile(src, dest)
            copied.append(dest.name)
        except Exception as e:
            logger.warning("[code_interpreter] skip input copy: " + f"{e}")

    # Also seed artifacts produced earlier in THIS session (a data agent's big
    # CSV, a prior run_python output, a file another agent made) so code can
    # compute over them by filename — not just user uploads. Session-scoped via
    # the shared store; existing uploads of the same name win (not clobbered).
    # docs/agent-artifact-sharing-plan.md Phase 4.
    try:
        from command_center.artifacts.artifact_manager import get_shared_artifact_manager
        amgr = get_shared_artifact_manager()
        existing = {c.lower() for c in copied}
        for art in amgr.list_artifacts(session_id):
            try:
                aid = art.get("artifact_id")
                asrc = amgr.get_file_path(aid) if aid else None
                if not asrc or not Path(asrc).exists():
                    continue
                aname = _safe_name(art.get("name") or Path(asrc).name)
                if aname.lower() in existing:
                    continue
                shutil.copyfile(asrc, Path(workdir) / aname)
                copied.append(aname)
                existing.add(aname.lower())
            except Exception as e:
                logger.warning("[code_interpreter] skip artifact copy: " + f"{e}")
    except Exception as e:
        logger.warning("[code_interpreter] artifact seeding unavailable: " + f"{e}")

    return workdir, copied


def _safe_name(name: str) -> str:
    """Strip path separators from a filename (defensive)."""
    return Path(str(name)).name or "file"


async def run_python(code: str, workdir: str, python_exe: Optional[str] = None,
                     timeout: Optional[int] = None) -> Dict[str, Any]:
    """Execute `code` as a script in `workdir` via a subprocess.

    Returns {stdout, stderr, returncode, timed_out}. Never raises.
    """
    python_exe = _resolve_interpreter(python_exe)
    timeout = timeout or _default_timeout()

    if not _interpreter_is_runnable(python_exe):
        return {
            "stdout": "",
            "stderr": (
                "Code interpreter is not configured: no usable Python interpreter was found. "
                "Set CODE_INTERPRETER_PYTHON to a Python with the data-science stack, or ensure "
                "the bundled Python at agent_environments/python-bundle is installed."
            ),
            "returncode": -1,
            "timed_out": False,
        }

    script_path = Path(workdir) / _SCRIPT_NAME
    try:
        # Preamble: keep matplotlib headless and expose the bundle's native DLLs
        # so compiled extensions (numpy/scipy/...) load when run under the bundle.
        preamble = (
            "import os as _os, sys as _sys\n"
            "_os.environ.setdefault('MPLBACKEND', 'Agg')\n"
            "try:\n"
            "    _libbin = _os.path.join(_os.path.dirname(_sys.executable), 'Library', 'bin')\n"
            "    if hasattr(_os, 'add_dll_directory') and _os.path.isdir(_libbin):\n"
            "        _os.add_dll_directory(_libbin)\n"
            "except Exception:\n"
            "    pass\n"
        )
        script_path.write_text(preamble + (code or ""), encoding="utf-8")
    except Exception as e:
        return {"stdout": "", "stderr": f"Could not write script: {e}", "returncode": -1, "timed_out": False}

    def _run():
        return subprocess.run(
            [python_exe, str(script_path)],
            cwd=workdir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**os.environ, "MPLBACKEND": "Agg", "PYTHONIOENCODING": "utf-8"},
        )

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, _run)
        return {
            "stdout": _truncate(result.stdout or ""),
            "stderr": _truncate(result.stderr or ""),
            "returncode": result.returncode,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Execution timed out after {timeout}s.", "returncode": -1, "timed_out": True}
    except Exception as e:
        return {"stdout": "", "stderr": f"Execution failed: {e}", "returncode": -1, "timed_out": False}


def _snapshot(workdir: str) -> Dict[str, tuple]:
    """Fingerprint workdir files as name → (size, mtime_ns) so harvest can
    detect both NEW files and MODIFIED inputs (e.g. 'clean this CSV in place')."""
    snap = {}
    try:
        for p in Path(workdir).iterdir():
            if not p.is_file():
                continue
            try:
                st = p.stat()
                snap[p.name] = (st.st_size, st.st_mtime_ns)
            except Exception:
                snap[p.name] = (None, None)
    except Exception:
        return snap
    return snap


def harvest_outputs(workdir: str, baseline: Dict[str, tuple], session_id: str,
                    user_context: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Register NEW or MODIFIED files in `workdir` as artifacts and return
    content blocks (image blocks rendered inline + artifact download chips).

    `baseline` is the name → (size, mtime_ns) fingerprint captured BEFORE the
    run. A file is treated as a generated output if it is new OR its fingerprint
    changed (so a result that overwrites an uploaded input is still returned).
    """
    blocks = []
    try:
        entries = sorted(Path(workdir).iterdir(), key=lambda p: p.name)
    except Exception:
        entries = []

    uc = user_context or {}
    user_id = str(uc.get("user_id", "anonymous"))
    scoped_session = f"{user_id}/{session_id}" if session_id else user_id

    mgr = None
    try:
        from routes.artifacts import _get_artifact_manager
        mgr = _get_artifact_manager()
    except Exception as e:
        logger.warning("[code_interpreter] artifact manager unavailable: " + f"{e}")

    try:
        from command_center.artifacts.artifact_models import ArtifactType
    except Exception:
        ArtifactType = None

    for path in entries:
        try:
            if not path.is_file():
                continue
            if path.name == _SCRIPT_NAME:
                continue
            prev = baseline.get(path.name)
            if prev is not None:
                try:
                    st = path.stat()
                    if (st.st_size, st.st_mtime_ns) == prev:
                        continue
                except Exception:
                    pass

            ext = path.suffix.lower()
            data = path.read_bytes()

            # Inline image block for renderable images.
            if ext in _IMAGE_EXTS:
                mime = "image/png" if ext == ".png" else "image/jpeg"
                b64 = base64.b64encode(data).decode("ascii")
                blocks.append({
                    "type": "image",
                    "src": f"data:{mime};base64,{b64}",
                    "alt": path.name,
                })

            # Register every output (including images) as a downloadable artifact.
            if mgr is not None and ArtifactType is not None:
                type_val = _EXT_TO_ARTIFACT_TYPE.get(ext, "text")
                try:
                    atype = ArtifactType(type_val)
                except Exception:
                    atype = ArtifactType.TEXT
                try:
                    meta = mgr.create(path.name, atype, data, scoped_session)
                    blocks.append(meta.to_content_block())
                except Exception as e:
                    logger.warning("[code_interpreter] could not save artifact " + f"{path.name}: {e}")
        except Exception as e:
            logger.warning("[code_interpreter] harvest skip " + f"{path}: {e}")

    return blocks


def cleanup_workdir(workdir: str) -> None:
    """Best-effort removal of the temp working directory."""
    try:
        shutil.rmtree(workdir, ignore_errors=True)
    except Exception:
        return None


async def execute(code: str, session_id: str, user_context: Optional[Dict[str, Any]],
                  python_exe: Optional[str] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
    """High-level entry: prepare workdir (with inputs) → run → harvest outputs.

    Returns {stdout, stderr, returncode, timed_out, blocks, inputs}.
    `blocks` is the list of image/artifact content blocks for new outputs.
    Always cleans up the working directory.
    """
    workdir, inputs = prepare_workdir(session_id, user_context)
    baseline = _snapshot(workdir)
    try:
        run = await run_python(code, workdir, python_exe=python_exe, timeout=timeout)
        blocks = harvest_outputs(workdir, baseline, session_id, user_context)
        return {**run, "blocks": blocks, "inputs": inputs}
    finally:
        cleanup_workdir(workdir)
