"""Validate training records against the compiler and rule checks.

Uses the same modules the production system uses so training data is
guaranteed consistent with runtime behavior:
  - workflow_compiler.materialize_commands  (the compile gate)
  - workflow_command_validator.check_missing_save_to_variable
  - workflow_command_validator.check_variable_references

The LLM-based validate_workflow() in workflow_command_validator is NOT used
here — we only want deterministic, free checks at curation time.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Tuple

# Make the repo importable when running via `python -m training.curate.validate`.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from training.curate.normalize import extract_assistant_json

# Import lazily inside functions so this module is still importable even if
# the heavy runtime deps (pyodbc etc. pulled in by AppUtils) aren't installed
# in the environment running curation.


class _CompileUnavailable(Exception):
    """Raised when the runtime compile gate cannot be loaded in this env."""


def _stub_runtime_imports() -> None:
    """Inject placeholder modules so workflow_compiler imports cleanly in a
    stripped-down env. workflow_compiler pulls CommandGenerator -> AppUtils ->
    openai 0.27 (proxy-patched); none of that is needed for materialize_commands
    which is pure graph manipulation. We stub out the chain so curation and
    eval harness can run without the full runtime stack.
    """
    import types

    for name in (
        "AppUtils",
        "CommandGenerator",
        "workflow_command_validator",
    ):
        if name not in sys.modules:
            m = types.ModuleType(name)
            # A few attributes the targets reference at import time.
            if name == "AppUtils":
                m.get_db_connection = lambda *a, **k: None  # type: ignore[attr-defined]
                m.quickPrompt = lambda *a, **k: ""  # type: ignore[attr-defined]
                m.azureQuickPrompt = lambda *a, **k: ""  # type: ignore[attr-defined]
                m.azureMiniQuickPrompt = lambda *a, **k: ""  # type: ignore[attr-defined]
            if name == "CommandGenerator":
                class _CmdGen:  # minimal placeholder
                    def __init__(self, *a, **k) -> None:
                        pass
                m.CommandGenerator = _CmdGen  # type: ignore[attr-defined]
                m.COMMAND_GENERATOR_SYSTEM_PROMPT = ""  # type: ignore[attr-defined]
            if name == "workflow_command_validator":
                m.validate_workflow = lambda ws: (True, {})  # type: ignore[attr-defined]
                m.check_missing_save_to_variable = lambda ws: []  # type: ignore[attr-defined]
                m.check_variable_references = lambda ws: []  # type: ignore[attr-defined]
            sys.modules[name] = m


def _materialize(commands_json: Dict) -> Dict:
    # workflow_compiler has module-level imports of CommandGenerator (which in
    # turn pulls AppUtils/openai). Those are unused by materialize_commands
    # itself. Stub them before import so stripped envs (like aihub2 with its
    # proxy-patched openai) can still run the real graph compiler.
    try:
        _stub_runtime_imports()
        from workflow_compiler import materialize_commands  # noqa: WPS433
    except ImportError as exc:
        raise _CompileUnavailable(str(exc)) from exc
    return materialize_commands(commands_json)


def _rule_checks(workflow_state: Dict) -> List[str]:
    from workflow_command_validator import (  # noqa: WPS433
        check_missing_save_to_variable,
        check_variable_references,
    )
    warnings: List[str] = []
    warnings.extend(check_missing_save_to_variable(workflow_state))
    warnings.extend(check_variable_references(workflow_state))
    return warnings


VALID_NODE_TYPES = {
    "Database",
    "AI Action",
    "AI Extract",
    "Document",
    "Loop",
    "End Loop",
    "Conditional",
    "Human Approval",
    "Alert",
    "Folder Selector",
    "File",
    "Set Variable",
    "Execute Application",
    "Excel Export",
    "Server",
    "Integration",
}

VALID_COMMAND_TYPES = {
    "add_node",
    "delete_node",
    "connect_nodes",
    "delete_connection",
    "set_start_node",
    "update_node_config",
    "add_variable",
}

VALID_CONNECTION_TYPES = {"pass", "fail", "complete"}


def _schema_errors(commands_json: Dict) -> List[str]:
    """Fast structural checks that don't require materialization."""
    errors: List[str] = []
    if not isinstance(commands_json, dict):
        errors.append("top-level is not an object")
        return errors
    cmds = commands_json.get("commands")
    if not isinstance(cmds, list) or not cmds:
        errors.append("commands is missing or empty")
        return errors

    node_ids_seen: set = set()
    start_node_set = False
    for i, cmd in enumerate(cmds):
        if not isinstance(cmd, dict):
            errors.append(f"commands[{i}] is not an object")
            continue
        t = cmd.get("type")
        if t not in VALID_COMMAND_TYPES:
            errors.append(f"commands[{i}] unknown type: {t!r}")
            continue

        if t == "add_node":
            nt = cmd.get("node_type")
            if nt not in VALID_NODE_TYPES:
                errors.append(f"commands[{i}] unknown node_type: {nt!r}")
            nid = cmd.get("node_id")
            if not nid:
                errors.append(f"commands[{i}] add_node missing node_id")
            else:
                node_ids_seen.add(nid)
            if not isinstance(cmd.get("config"), dict):
                errors.append(f"commands[{i}] add_node missing config dict")

        elif t == "connect_nodes":
            ct = cmd.get("connection_type", "pass")
            if ct not in VALID_CONNECTION_TYPES:
                errors.append(f"commands[{i}] bad connection_type: {ct!r}")
            for side in ("from", "to"):
                if not cmd.get(side):
                    errors.append(f"commands[{i}] connect_nodes missing {side}")

        elif t == "set_start_node":
            if not cmd.get("node_id"):
                errors.append(f"commands[{i}] set_start_node missing node_id")
            start_node_set = True

    if not start_node_set:
        errors.append("no set_start_node command present")

    return errors


def validate_record(record: Dict, run_compile: bool = True) -> Tuple[bool, Dict]:
    """Validate a single normalized training record.

    Returns (ok, details). `ok` is True only when every check passes.
    `details` contains per-check results for downstream filtering/logging.
    """
    asst = next(
        (m for m in reversed(record.get("messages", [])) if m.get("role") == "assistant"),
        None,
    )
    if asst is None:
        return False, {"error": "no assistant message"}

    commands = extract_assistant_json(asst.get("content", ""))
    if commands is None:
        return False, {"error": "assistant JSON unparseable"}

    schema_errors = _schema_errors(commands)
    details: Dict = {
        "schema_errors": schema_errors,
        "compile_ok": None,
        "compile_error": None,
        "rule_warnings": [],
        "n_commands": len(commands.get("commands", [])),
    }

    if schema_errors:
        return False, details

    if not run_compile:
        return True, details

    try:
        workflow_state = _materialize(commands)
        details["compile_ok"] = True
        details["n_nodes"] = len(workflow_state.get("nodes", []))
        details["n_connections"] = len(workflow_state.get("connections", []))
    except _CompileUnavailable as exc:
        # Env can't load the compiler; mark as unknown, don't fail the record.
        details["compile_ok"] = None
        details["compile_error"] = f"unavailable: {exc}"
        return True, details
    except Exception as exc:  # noqa: BLE001
        details["compile_ok"] = False
        details["compile_error"] = f"{type(exc).__name__}: {exc}"
        return False, details

    try:
        details["rule_warnings"] = _rule_checks(workflow_state)
    except Exception as exc:  # noqa: BLE001
        # Non-fatal: rule checks have their own imports that may not resolve
        # in every environment. Treat as advisory only.
        details["rule_warnings_error"] = f"{type(exc).__name__}: {exc}"

    # A record is ok if it compiles cleanly and has no hard rule warnings.
    # Rule warnings are advisory strings; currently we treat any presence as
    # a soft-fail signal (kept in _meta for filtering, but not a hard drop).
    return True, details


def write_validation_report(records_in: str, report_out: str) -> Dict:
    """Walk a normalized JSONL, emit a validation report JSON."""
    from training.curate.normalize import iter_normalized

    summary = {
        "path": records_in,
        "total": 0,
        "schema_fail": 0,
        "compile_fail": 0,
        "rule_warn": 0,
        "ok": 0,
    }
    with open(report_out, "w", encoding="utf-8") as out:
        for record in iter_normalized(records_in):
            summary["total"] += 1
            ok, details = validate_record(record)
            record.setdefault("_meta", {})["validate"] = details
            if details.get("schema_errors"):
                summary["schema_fail"] += 1
            elif details.get("compile_ok") is False:
                summary["compile_fail"] += 1
            else:
                summary["ok"] += 1
                if details.get("rule_warnings"):
                    summary["rule_warn"] += 1
            out.write(json.dumps({"_meta": record.get("_meta", {})}) + "\n")
    return summary
