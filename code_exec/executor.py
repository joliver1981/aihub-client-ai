"""
Run one script in a subprocess and diff the workdir for produced files.

Surface-neutral: no staging, no artifact registration — callers own those.
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_SCRIPT_NAME = "_exec_script.py"
_DEFAULT_MAX_CHARS = 50_000


def _max_chars() -> int:
    try:
        return int(os.environ.get("CODE_INTERPRETER_MAX_OUTPUT_CHARS", str(_DEFAULT_MAX_CHARS)))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_CHARS


def truncate(text: str, limit: Optional[int] = None) -> str:
    limit = limit or _max_chars()
    if text and len(text) > limit:
        return text[:limit] + f"\n... [truncated at {limit} characters]"
    return text


def snapshot(workdir: str) -> Dict[str, Tuple[int, float]]:
    """Map of relative file path -> (size, mtime) for later diffing."""
    result: Dict[str, Tuple[int, float]] = {}
    root = Path(workdir)
    try:
        for p in root.rglob("*"):
            if p.is_file():
                try:
                    st = p.stat()
                    result[str(p.relative_to(root))] = (st.st_size, st.st_mtime)
                except OSError:
                    continue
    except Exception as e:
        logger.debug("[code_exec] snapshot failed for %s: %s", workdir, e)
    return result


def new_files(workdir: str, baseline: Dict[str, Tuple[int, float]],
              exclude: Iterable[str] = ()) -> List[Path]:
    """Files created (or changed) since ``baseline``, excluding the script,
    dot-directories (.mpl config, package internals) and any extra names."""
    root = Path(workdir)
    skip = {_SCRIPT_NAME, *exclude}
    produced: List[Path] = []
    for rel, sig in snapshot(workdir).items():
        parts = Path(rel).parts
        if parts and parts[0].startswith("."):
            continue
        if Path(rel).name in skip:
            continue
        if baseline.get(rel) == sig:
            continue
        produced.append(root / rel)
    produced.sort(key=lambda p: p.name.lower())
    return produced


def run_script(code: str, workdir: str, python_exe: str, timeout: int,
               env: Dict[str, str], preamble: str = "",
               max_output_chars: Optional[int] = None) -> Dict:
    """Execute ``preamble + code`` as a script in ``workdir``.

    Returns {stdout, stderr, returncode, timed_out}. Never raises.
    """
    script_path = Path(workdir) / _SCRIPT_NAME
    try:
        script_path.write_text((preamble or "") + (code or ""), encoding="utf-8")
    except Exception as e:
        return {"stdout": "", "stderr": f"Could not write script: {e}",
                "returncode": -1, "timed_out": False}

    try:
        result = subprocess.run(
            [python_exe, str(script_path)],
            cwd=workdir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
        return {
            "stdout": truncate(result.stdout or "", max_output_chars),
            "stderr": truncate(result.stderr or "", max_output_chars),
            "returncode": result.returncode,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Execution timed out after {timeout}s.",
                "returncode": -1, "timed_out": True}
    except Exception as e:
        return {"stdout": "", "stderr": f"Execution failed: {e}",
                "returncode": -1, "timed_out": False}
