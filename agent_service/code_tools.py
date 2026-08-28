"""
The Agent — code interpreter tool (Phase 2 of the unification plan).

Same lane GeneralAgent and CC run: the model writes Python, the shared
code_exec backend executes it (separate interpreter, denylist secret-scrub,
default-open install(), aihub_runtime SDK with a user-parity run token), and
produced files come back as the chat's native download links.

Distinct from the AUTOMATIONS lane: automations are saved scripts with a
draft -> dry-run -> promote -> schedule lifecycle; run_python is immediate,
conversational analysis over the user's uploaded files.

docs/code-interpreter-unification-plan.md §4.2
"""

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from agent_config import APP_ROOT, logger
from file_tools import list_uploads, resolve_upload, stage_offer
from platform_tools import CURRENT_USER, _text

_TIMEOUT_ENV = "AGENT_RUN_PYTHON_TIMEOUT"
_SDK_ENV = "AGENT_RUN_PYTHON_SDK"


def _workdir_root() -> str:
    """Per-run workdirs live UNDER APP_ROOT so stage_offer (which refuses
    paths outside the platform's data area) can deliver harvested files."""
    d = os.path.join(APP_ROOT, "temp", "agent_runpy")
    os.makedirs(d, exist_ok=True)
    return d


def _hidden_sheet_manifest(workdir: str, staged_names: list) -> str:
    """Sheet-visibility manifest for staged Excel workbooks — delegates to the
    shared code_exec helper (one implementation for all three surfaces; the
    stdlib-only reader started here and moved when CC picked it up)."""
    from code_exec.workbooks import hidden_sheet_manifest
    return hidden_sheet_manifest(workdir, staged_names)


async def _connection_names() -> list:
    """Platform connection names for the run token's user-parity scope — via
    the same main-app HTTP index list_data_connections uses (the AppUtils/
    pandas path does not import inside this service's environment)."""
    try:
        from platform_tools import _connections_index
        conns = await _connections_index()
        return sorted({str(c.get("name") or "").strip() for c in (conns or [])
                       if str(c.get("name") or "").strip()})
    except Exception as e:
        logger.warning(f"run_python: connection listing unavailable: {e}")
        return []


@tool(
    "run_python",
    "Execute Python NOW in the platform's sandboxed code interpreter and get "
    "its output back. THE tool for any computation over files the user "
    "uploaded in chat (CSV/Excel/JSON/text): row counts, totals, averages, "
    "group-bys, joins, dedup, reformatting, and chart generation — never "
    "count or total from a preview; compute here. The user's chat uploads are "
    "copied into the working directory under their ORIGINAL filenames (e.g. "
    "pd.read_csv('sales.csv')). pandas/numpy/matplotlib/openpyxl are "
    "preinstalled; call install('pkg') inside the code for anything else. Any "
    "NEW file the code writes to the working directory is delivered to the "
    "user as a download link — include the returned links VERBATIM in your "
    "reply. The aihub_runtime SDK works here too (import aihub_runtime as "
    "aihub; aihub.query/help/...). This runs code IMMEDIATELY — for saved, "
    "scheduled, repeatable work use the automations lifecycle instead.",
    {
        "type": "object",
        "properties": {
            "code": {"type": "string",
                     "description": "Python source to run. print() everything "
                                    "you want to see."},
            "files": {"type": "array", "items": {"type": "string"},
                      "description": "Optional extra server-side files (paths "
                                     "under the AI Hub root, e.g. a portal "
                                     "download) to stage into the working "
                                     "directory by filename."},
        },
        "required": ["code"],
        "additionalProperties": False,
    },
)
async def run_python(args: dict[str, Any]) -> dict[str, Any]:
    from code_exec import (
        NOT_CONFIGURED_MSG,
        adhoc_package_dir,
        build_child_env,
        build_preamble,
        new_files,
        policy_files,
        resolve_interpreter,
        run_script,
        snapshot,
    )
    from code_exec import sdkwire

    user = CURRENT_USER.get() or {}
    uid = int(user.get("user_id") or 0)

    try:
        timeout = int(os.getenv(_TIMEOUT_ENV, "120"))
    except (TypeError, ValueError):
        timeout = 120

    python_exe = resolve_interpreter()
    if not python_exe:
        return _text(NOT_CONFIGURED_MSG, is_error=True)

    workdir = tempfile.mkdtemp(prefix="run_", dir=_workdir_root())
    try:
        # -- stage this user's chat uploads by original filename
        staged_names = []

        def _stage(src, name):
            try:
                dest = Path(workdir) / Path(name or os.path.basename(src)).name
                if src and os.path.isfile(src) and not dest.exists():
                    shutil.copyfile(src, dest)
                    staged_names.append(dest.name)
            except Exception as e:
                logger.warning(f"run_python: could not stage {name}: {e}")

        try:
            for meta in list_uploads(uid):
                hit = resolve_upload(uid, meta.get("file_id"))
                if hit:
                    _stage(hit[0], hit[1])
        except Exception as e:
            logger.warning(f"run_python: upload staging unavailable: {e}")

        # optional extra server files — same containment rule as stage_offer
        root = os.path.abspath(APP_ROOT)
        for extra in (args.get("files") or []):
            src = os.path.abspath(str(extra).strip().strip('"'))
            if src.startswith(root + os.sep) and os.path.isfile(src):
                _stage(src, os.path.basename(src))
            else:
                logger.warning(f"run_python: refused extra file outside root: {extra}")

        # Sheet-visibility manifest — computed NOW, before the user's code can
        # rewrite the staged files.
        visibility_manifest = _hidden_sheet_manifest(workdir, staged_names)

        # -- aihub_runtime SDK (user parity; kill switch AGENT_RUN_PYTHON_SDK)
        extra_env = {}
        sdk_path = None
        if os.getenv(_SDK_ENV, "true").strip().lower() != "false":
            sdk_path = sdkwire.sdk_dir()
            extra_env = sdkwire.sdk_env(
                "the-agent",
                connections=await _connection_names(),
                ttl_seconds=timeout + 180,
                user_id=uid,
            )

        pkg_dir = adhoc_package_dir(python_exe)
        denylist_path, constraints_path = policy_files()
        preamble = build_preamble(sdk_dir=sdk_path, pkg_dir=pkg_dir,
                                  denylist_path=denylist_path,
                                  constraints_path=constraints_path)
        env = build_child_env(workdir, extra=extra_env)

        baseline = snapshot(workdir)
        started = time.time()
        res = run_script(args.get("code") or "", workdir, python_exe,
                         timeout=timeout, env=env, preamble=preamble)

        if res["timed_out"]:
            return _text(f"Execution timed out after {timeout} seconds.",
                         is_error=True)

        out = res["stdout"] or ""
        if res["returncode"] != 0:
            tail = (res["stderr"] or "").strip()[-4000:]
            out += ("\n" if out else "") + f"Error (exit {res['returncode']}): {tail}"

        # -- deliver produced files as the chat's native download links
        links, produced_names = [], []
        for produced in new_files(workdir, baseline):
            try:
                if produced.stat().st_size == 0:
                    continue
                ok, msg, _staged = stage_offer(uid, str(produced), produced.name)
                if ok:
                    links.append(msg)
                    produced_names.append(produced.name)
                else:
                    logger.warning(f"run_python: could not offer {produced.name}: {msg}")
            except Exception as e:
                logger.warning(f"run_python: offer failed for {produced.name}: {e}")

        # invocation ledger — shared with GA's lane so packs/forensics can
        # attribute WHICH lane answered (surface marks this one)
        try:
            from CommonUtils import get_log_path
            rec = {"ts": time.time(), "surface": "the-agent", "agent": None,
                   "user": uid, "staged": staged_names,
                   "rc": res["returncode"], "timed_out": res["timed_out"],
                   "duration_s": round(time.time() - started, 1),
                   "produced": produced_names}
            with open(get_log_path("run_python_code_invocations.jsonl"),
                      "a", encoding="utf-8") as ledger:
                ledger.write(json.dumps(rec) + "\n")
        except Exception as e:
            logger.debug(f"run_python: ledger write failed: {e}")

        reply = out.strip() or ("(no output)" if not links else "")
        reply = reply[:18000]
        if visibility_manifest:
            reply += visibility_manifest
        if links:
            reply += (("\n\n" if reply else "") +
                      "Files created — include these links VERBATIM in your reply:\n" +
                      "\n".join(links))
        return _text(reply)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


CODE_TOOLS = [run_python]
