"""
The Agent's authoring tools — A1: create -> dry-run -> promote -> schedule.

Ports of Command Center's automation and code-flow tool bodies, calling the
same governed chokepoints (POST /automations/api/internal/manage and
POST /codeflows/api/internal/manage) with X-API-Key + the session envelope's
user_context. The server re-enforces Developer role on every action — the
client-side gate here is UX, not the security boundary.

Honesty doctrine carried over verbatim from CC (AIHUB-0058/0040/0045 lessons):
- a client timeout on dry_run/run is NEVER "it didn't start" — re-check runs
  and report the run's true state
- a run paused at a checkpoint is NOT failed and NOT timed out
- "still executing" is not an outcome; never claim success or failure early
- saves are verified by read-back (get after save); promotes by pinned_version
- schedule reports ONLY the ids the server returned
- handled fail-edges in a code-flow walk can never read as a clean pass
"""

import json
import os
import re
import asyncio
from typing import Any

import httpx

from agent_config import logger
from platform_tools import (
    CURRENT_USER, _post, _text,
)
from claude_agent_sdk import tool

_GUID_RE = re.compile(r"^[0-9a-fA-F][0-9a-fA-F-]{31,35}$")


def _user_context() -> dict:
    u = CURRENT_USER.get()
    return {"user_id": int(u.get("user_id") or 0),
            "role": int(u.get("role") or 0),
            "username": str(u.get("username") or u.get("name") or "")}


def _authoring_allowed() -> bool:
    if os.getenv("AGENT_BUILD_ALLOW_ALL_USERS", "false").lower() == "true":
        return True
    return _user_context()["role"] >= 2


_DENIED = ("Building or changing automations requires a Developer role on this "
           "install. You can still explore data and inspect playbooks.")


async def _manage(action: str, payload: dict, timeout: float = 900.0):
    """Automations manage envelope; never raises; returns (data, status)."""
    body = {"action": action, "user_context": _user_context(),
            "payload": payload or {}}
    try:
        return await _post("/automations/api/internal/manage", body, timeout=timeout)
    except httpx.TimeoutException:
        return {"error": "client timeout", "timed_out": True}, 504
    except Exception as e:
        return {"error": f"could not reach the automations service: {e}"}, 502


async def _manage_cf(action: str, payload: dict, timeout: float = 900.0):
    """Code-flows manage envelope; same contract as _manage."""
    body = {"action": action, "user_context": _user_context(),
            "payload": payload or {}}
    try:
        return await _post("/codeflows/api/internal/manage", body, timeout=timeout)
    except httpx.TimeoutException:
        return {"error": "client timeout", "timed_out": True}, 504
    except Exception as e:
        return {"error": f"could not reach the code flows service: {e}"}, 502


async def _resolve_automation(ref: str):
    """Accept an automation GUID or an exact (case-insensitive) name."""
    s = str(ref).strip()
    if _GUID_RE.match(s):
        return s, None
    data, status = await _manage("list", {}, timeout=30)
    if status >= 400:
        return None, f"Could not list automations to resolve '{ref}': {data.get('error')}"
    matches = [a for a in (data.get("automations") or [])
               if str(a.get("name", "")).strip().lower() == s.lower()]
    if len(matches) == 1:
        return matches[0].get("automation_id"), None
    if not matches:
        names = ", ".join(str(a.get("name")) for a in (data.get("automations") or [])[:20])
        return None, f"No automation named '{ref}'. Known: {names or '(none)'}"
    return None, f"'{ref}' is ambiguous ({len(matches)} matches) — use the automation_id."


def _summarize_run(data: dict) -> str:
    """Honest chat summary of a terminal run result (port of CC summarize_run)."""
    status = data.get("status", "?")
    lines = [f"Run outcome: **{status}** (exit {data.get('exit_code')}) — "
             f"run_id {data.get('run_id')}, version v{data.get('version')}"]
    if data.get("error"):
        lines.append(f"Error: {data['error']}")
    for rep in (data.get("verify_report") or []):
        for chk in (rep.get("checks") or []):
            mark = "✓" if chk.get("ok") else ("✗" if chk.get("ok") is False else "?")
            lines.append(f"  {mark} verify[{rep.get('kind')}]: {chk.get('check')} — {chk.get('note', '')}")
    if data.get("no_egress_transfer"):
        lines.append("  🚫 declared a remote transfer but NO network egress was observed — "
                     "nothing was transferred (do NOT report the upload as done)")
    files = data.get("output_files") or []
    if files:
        lines.append("  output files: " + ", ".join(files[:10]))
    if data.get("stdout_tail"):
        lines.append("--- stdout (tail) ---\n" + str(data["stdout_tail"])[-800:])
    if status in ("failed", "error", "unverified") and data.get("stderr_tail"):
        lines.append("--- stderr (tail) ---\n" + str(data["stderr_tail"])[-800:])
    return "\n".join(lines)


async def _run_action(action: str, automation_ref: str, inputs_json: str,
                      version: int = 0) -> dict:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    auto_id, err = await _resolve_automation(automation_ref)
    if err:
        return _text(err, is_error=True)
    payload: dict[str, Any] = {"automation_id": auto_id, "inputs": {}}
    if inputs_json:
        try:
            payload["inputs"] = json.loads(inputs_json)
        except Exception:
            return _text("inputs_json is not valid JSON", is_error=True)
    if version and int(version) > 0:
        payload["version"] = int(version)

    data, status = await _manage(action, payload, timeout=900)

    # AIHUB-0058: client timeout must never become "it didn't start".
    if data.get("timed_out"):
        runs, rstat = await _manage("runs", {"automation_id": auto_id, "limit": 1},
                                    timeout=15)
        latest = (runs.get("runs") or [{}])[0] if rstat < 400 else {}
        return _text(
            f"⏳ The {action.replace('_', '-')} timed out CLIENT-SIDE, but the run "
            f"itself is '{latest.get('status', 'unknown')}' "
            f"(run_id {latest.get('run_id', '?')}). Never claim it did not start — "
            f"use check_automation_run to follow it.")

    # Pause-pin: paused at a human checkpoint is NOT failed, NOT timed out.
    if data.get("waiting_on_checkpoint"):
        cp = data.get("pending_checkpoint") or {}
        return _text(
            "⏸️ RUN PAUSED — human approval required (this is not a failure).\n"
            f"run_id {data.get('run_id')} | checkpoint_id {cp.get('checkpoint_id')}\n"
            f"Checkpoint message: {cp.get('message', '')}\n"
            "The approval is also waiting in My Approvals. To decide it here, "
            "call decide_automation_checkpoint with proceed or abort.")

    if data.get("inline_wait_elapsed"):
        return _text(
            f"⏳ STILL EXECUTING after the inline wait (run_id {data.get('run_id')}). "
            "This is NOT a failure and NOT a success — do not claim an outcome yet; "
            "use check_automation_run to get the real result.")

    if status >= 400 and not data.get("run_id"):
        return _text(f"{action} failed (HTTP {status}): {data.get('error', data)}",
                     is_error=True)
    return _text(_summarize_run(data))


# ---------------------------------------------------------------------------
# Automation authoring tools
# ---------------------------------------------------------------------------

@tool(
    "create_automation",
    "Create a new (empty) automation. Code is added separately with "
    "save_automation_code. Names must be unique.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Unique name, <=200 chars"},
            "description": {"type": "string"},
        },
        "required": ["name"],
        "additionalProperties": False,
    },
)
async def create_automation(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    data, status = await _manage("create", {"name": str(args["name"]).strip(),
                                            "description": args.get("description") or ""},
                                 timeout=60)
    if status >= 400:
        return _text(f"Create failed (HTTP {status}): {data.get('error', data)}",
                     is_error=True)
    a = data.get("automation") or {}
    msg = (f"Created automation '{a.get('name')}' — automation_id "
           f"{a.get('automation_id')} (v0, nothing saved or promoted yet). "
           "Next: save_automation_code, then dry_run_automation.")
    if data.get("warning"):
        msg += f"\nNote: {data['warning']}"
    return _text(msg)


@tool(
    "save_automation_code",
    "Save a new code version for an automation (append-only; the pinned/live "
    "version does not change until you promote). Start the code with the "
    "explicit import — `import aihub_runtime as aihub` — then:\n"
    "  aihub.query('CONNECTION_NAME', 'SELECT ...', [params]) -> list of dicts\n"
    "  aihub.connection(name) / aihub.secret(name) -> resolved values\n"
    "  aihub.input(name, default) | aihub.log(msg) | print() for output\n"
    "  aihub.checkpoint('message') -> BLOCKS until a human approves in My Approvals\n"
    "  aihub.send_email(to, subject, body) | aihub.llm(prompt) | aihub.ai_extract(...)\n"
    "Every connection/secret used MUST be declared in the manifest, e.g. "
    "manifest_json='{\"connections\": [\"ERPDB\"]}'. Never hard-code credentials "
    "(the server rejects them). Saves are verified by read-back.",
    {
        "type": "object",
        "properties": {
            "automation_id": {"type": "string", "description": "GUID or exact name"},
            "code": {"type": "string", "description": "Full Python source for main.py"},
            "manifest_json": {"type": "string",
                              "description": "Optional manifest JSON: connections, "
                                             "secrets, packages, inputs, outputs, "
                                             "timeout_seconds"},
        },
        "required": ["automation_id", "code"],
        "additionalProperties": False,
    },
)
async def save_automation_code(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    auto_id, err = await _resolve_automation(args["automation_id"])
    if err:
        return _text(err, is_error=True)
    payload: dict[str, Any] = {"automation_id": auto_id, "code": str(args["code"])}
    if args.get("manifest_json"):
        try:
            base_manifest = {}
            got, gstat = await _manage("get", {"automation_id": auto_id}, timeout=30)
            if gstat < 400:
                base_manifest = (got.get("automation") or {}).get("manifest") or {}
            base_manifest.update(json.loads(args["manifest_json"]))
            payload["manifest"] = base_manifest
        except Exception:
            return _text("manifest_json is not valid JSON", is_error=True)
    data, status = await _manage("save_code", payload, timeout=60)
    if status >= 400:
        details = data.get("details")
        extra = f"\nDetails: {json.dumps(details)[:500]}" if details else ""
        return _text(f"Save failed (HTTP {status}): {data.get('error', data)}{extra}",
                     is_error=True)
    saved_version = data.get("version")

    # Read-back verification: the saved version must exist and carry our code.
    got, gstat = await _manage("get", {"automation_id": auto_id}, timeout=30)
    if gstat >= 400:
        return _text(f"Code saved as v{saved_version}, but READ-BACK FAILED "
                     f"({got.get('error')}) — report the save as UNVERIFIED.")
    a = got.get("automation") or {}
    verified = (a.get("current_version") == saved_version
                and saved_version in (a.get("versions") or []))
    if not verified:
        return _text(f"🚨 Save reported v{saved_version} but read-back shows "
                     f"current v{a.get('current_version')} with versions "
                     f"{a.get('versions')} — do NOT claim the save is verified.")
    return _text(f"Saved v{saved_version} (verified by read-back; pinned is still "
                 f"v{a.get('pinned_version')}). Not live until you promote — "
                 "dry_run_automation first.")


@tool(
    "get_automation",
    "Fetch an automation's full state: versions, pinned version, manifest, and "
    "current code. Use for read-back and before editing.",
    {
        "type": "object",
        "properties": {"automation_id": {"type": "string",
                                         "description": "GUID or exact name"}},
        "required": ["automation_id"],
        "additionalProperties": False,
    },
)
async def get_automation(args: dict[str, Any]) -> dict[str, Any]:
    auto_id, err = await _resolve_automation(args["automation_id"])
    if err:
        return _text(err, is_error=True)
    data, status = await _manage("get", {"automation_id": auto_id}, timeout=30)
    if status >= 400:
        return _text(f"Get failed (HTTP {status}): {data.get('error', data)}",
                     is_error=True)
    a = data.get("automation") or {}
    code = a.get("code") or ""
    lines = [
        f"Automation '{a.get('name')}' — id {a.get('automation_id')}",
        f"status {a.get('status')} | current v{a.get('current_version')} | "
        f"pinned v{a.get('pinned_version')} | versions {a.get('versions')}",
        f"description: {a.get('description', '')}",
        f"manifest: {json.dumps(a.get('manifest') or {})[:600]}",
        "--- code (current) ---",
        code[:4000] + ("…(truncated)" if len(code) > 4000 else ""),
    ]
    return _text("\n".join(lines))


@tool(
    "dry_run_automation",
    "Execute the LATEST SAVED version for real (live credentials, real side "
    "effects) to prove it works before promoting. May pause at a human "
    "checkpoint — that is not a failure. Zero-token replays only happen after "
    "promote + schedule; dry-run is the proving step.",
    {
        "type": "object",
        "properties": {
            "automation_id": {"type": "string", "description": "GUID or exact name"},
            "inputs_json": {"type": "string", "description": "Optional inputs JSON object"},
        },
        "required": ["automation_id"],
        "additionalProperties": False,
    },
)
async def dry_run_automation(args: dict[str, Any]) -> dict[str, Any]:
    return await _run_action("dry_run", args["automation_id"],
                             args.get("inputs_json") or "")


@tool(
    "run_automation",
    "Execute the PINNED (promoted) version — what schedules and webhooks run. "
    "Fails honestly if nothing is promoted yet.",
    {
        "type": "object",
        "properties": {
            "automation_id": {"type": "string", "description": "GUID or exact name"},
            "inputs_json": {"type": "string", "description": "Optional inputs JSON object"},
        },
        "required": ["automation_id"],
        "additionalProperties": False,
    },
)
async def run_automation(args: dict[str, Any]) -> dict[str, Any]:
    return await _run_action("run", args["automation_id"],
                             args.get("inputs_json") or "")


@tool(
    "check_automation_run",
    "Poll a run's true state: status, pending checkpoint, recent events, and "
    "the run row. Use after 'still executing' or a client timeout — never "
    "guess an outcome.",
    {
        "type": "object",
        "properties": {"run_id": {"type": "string"}},
        "required": ["run_id"],
        "additionalProperties": False,
    },
)
async def check_automation_run(args: dict[str, Any]) -> dict[str, Any]:
    data, status = await _manage("run_events", {"run_id": str(args["run_id"])},
                                 timeout=30)
    if status >= 400:
        return _text(f"Could not fetch run (HTTP {status}): {data.get('error', data)}",
                     is_error=True)
    run = data.get("run") or {}
    lines = [f"Run {run.get('run_id')} — status **{run.get('status')}** "
             f"(exit {run.get('exit_code')}), v{run.get('version')}, "
             f"trigger {run.get('trigger_source')}, started {run.get('started_at')}, "
             f"finished {run.get('finished_at')}"]
    if run.get("error"):
        lines.append(f"error: {run['error']}")
    cp = data.get("pending_checkpoint")
    if cp:
        lines.append(f"⏸️ waiting on checkpoint {cp.get('checkpoint_id')}: "
                     f"{cp.get('message', '')} (also in My Approvals)")
    events = data.get("events") or []
    for ev in events[-5:]:
        lines.append(f"  event: {json.dumps(ev)[:200]}")
    return _text("\n".join(lines))


@tool(
    "promote_automation",
    "Pin a saved version as the LIVE one (what schedules/webhooks execute). "
    "Omit version to promote the latest. Verified by read-back.",
    {
        "type": "object",
        "properties": {
            "automation_id": {"type": "string", "description": "GUID or exact name"},
            "version": {"type": "integer", "description": "Version to pin (0 = latest)"},
        },
        "required": ["automation_id"],
        "additionalProperties": False,
    },
)
async def promote_automation(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    auto_id, err = await _resolve_automation(args["automation_id"])
    if err:
        return _text(err, is_error=True)
    payload: dict[str, Any] = {"automation_id": auto_id}
    if args.get("version"):
        payload["version"] = int(args["version"])
    data, status = await _manage("promote", payload, timeout=30)
    if status >= 400:
        return _text(f"Promote failed (HTTP {status}): {data.get('error', data)}",
                     is_error=True)
    pinned = data.get("pinned_version")
    got, gstat = await _manage("get", {"automation_id": auto_id}, timeout=30)
    if gstat < 400 and (got.get("automation") or {}).get("pinned_version") == pinned:
        return _text(f"Promoted: v{pinned} is now live (verified by read-back). "
                     "Scheduled and API runs execute this pinned version.")
    return _text(f"Promote returned v{pinned} but read-back could not confirm — "
                 "report the promote as UNVERIFIED.")


@tool(
    "schedule_automation",
    "Schedule the PINNED version to run automatically. Provide cron_expression "
    "(e.g. '0 8 * * 1' = Mondays 8am) OR every_hours/every_days. Optional IANA "
    "timezone for cron. Requires a promoted version. Report ONLY the ids this "
    "returns — never invent a schedule.",
    {
        "type": "object",
        "properties": {
            "automation_id": {"type": "string", "description": "GUID or exact name"},
            "cron_expression": {"type": "string"},
            "every_hours": {"type": "integer"},
            "every_days": {"type": "integer"},
            "inputs_json": {"type": "string", "description": "Optional inputs JSON object"},
            "timezone": {"type": "string", "description": "IANA tz for cron, e.g. America/New_York"},
        },
        "required": ["automation_id"],
        "additionalProperties": False,
    },
)
async def schedule_automation(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    auto_id, err = await _resolve_automation(args["automation_id"])
    if err:
        return _text(err, is_error=True)
    if args.get("cron_expression"):
        schedule = {"type": "cron", "cron_expression": str(args["cron_expression"])}
    elif args.get("every_hours") or args.get("every_days"):
        schedule = {"type": "interval"}
        if args.get("every_hours"):
            schedule["interval_hours"] = int(args["every_hours"])
        if args.get("every_days"):
            schedule["interval_days"] = int(args["every_days"])
    else:
        return _text("Provide either cron_expression or every_hours/every_days.",
                     is_error=True)
    payload: dict[str, Any] = {"automation_id": auto_id, "schedule": schedule,
                               "inputs": {}}
    if args.get("inputs_json"):
        try:
            payload["inputs"] = json.loads(args["inputs_json"])
        except Exception:
            return _text("inputs_json is not valid JSON", is_error=True)
    if args.get("timezone"):
        payload["timezone"] = str(args["timezone"])
    data, status = await _manage("schedule", payload, timeout=60)
    if status >= 400:
        return _text(f"Schedule failed (HTTP {status}): {data.get('error', data)}",
                     is_error=True)
    if not data.get("scheduled_job_id"):
        return _text("Nothing was scheduled — the server returned no job id. "
                     "Do NOT tell the user it was scheduled.", is_error=True)
    return _text(f"Scheduled automation '{data.get('automation_name')}' "
                 f"(job #{data.get('scheduled_job_id')}, schedule "
                 f"#{data.get('schedule_id')}, runs pinned v{data.get('pinned_version')}). "
                 f"{data.get('note', '')}")


@tool(
    "decide_automation_checkpoint",
    "Decide a paused run's human checkpoint on the user's behalf: proceed or "
    "abort. Only do this when the user in this conversation explicitly decided.",
    {
        "type": "object",
        "properties": {
            "run_id": {"type": "string"},
            "checkpoint_id": {"type": "string"},
            "decision": {"type": "string", "enum": ["proceed", "abort"]},
        },
        "required": ["run_id", "checkpoint_id", "decision"],
        "additionalProperties": False,
    },
)
async def decide_automation_checkpoint(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    data, status = await _manage("checkpoint_decision",
                                 {"run_id": str(args["run_id"]),
                                  "checkpoint_id": str(args["checkpoint_id"]),
                                  "decision": str(args["decision"])},
                                 timeout=30)
    if status >= 400:
        return _text(f"Decision failed (HTTP {status}): {data.get('error', data)}",
                     is_error=True)
    # Follow the run briefly so the user hears the real aftermath.
    terminal = {"success", "failed", "unverified", "aborted", "skipped", "error"}
    last = {}
    for _ in range(20):  # ~60s
        ev, estat = await _manage("run_events", {"run_id": str(args["run_id"])},
                                  timeout=15)
        if estat < 400:
            last = ev.get("run") or {}
            if str(last.get("status")) in terminal:
                return _text(f"Checkpoint {args['decision']} recorded. "
                             f"Run finished: **{last.get('status')}** "
                             f"(exit {last.get('exit_code')}).")
        await asyncio.sleep(3)
    return _text(f"Checkpoint {args['decision']} recorded. Run is still "
                 f"'{last.get('status', 'running')}' — use check_automation_run "
                 "for the outcome; do not claim one yet.")


@tool(
    "delete_automation",
    "Delete an automation (soft delete; schedules are deactivated first). "
    "TWO-STEP: first call without confirmed to get a confirmation summary; "
    "call again with confirmed=true only after the user explicitly confirms.",
    {
        "type": "object",
        "properties": {
            "automation_id": {"type": "string", "description": "GUID or exact name"},
            "confirmed": {"type": "boolean"},
        },
        "required": ["automation_id"],
        "additionalProperties": False,
    },
)
async def delete_automation(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    auto_id, err = await _resolve_automation(args["automation_id"])
    if err:
        return _text(err, is_error=True)
    if not args.get("confirmed"):
        got, gstat = await _manage("get", {"automation_id": auto_id}, timeout=30)
        a = (got.get("automation") or {}) if gstat < 400 else {}
        return _text("⚠️ CONFIRMATION REQUIRED — nothing was deleted.\n"
                     f"Target: '{a.get('name', auto_id)}' (id {auto_id}), "
                     f"pinned v{a.get('pinned_version')}. Deleting deactivates its "
                     "schedules and removes it from lists (run history survives).\n"
                     "Ask the user to confirm, then call again with confirmed=true.")
    data, status = await _manage("delete", {"automation_id": auto_id}, timeout=60)
    if status >= 400:
        return _text(f"Delete failed (HTTP {status}): {data.get('error', data)}",
                     is_error=True)
    return _text(f"Deleted '{data.get('name')}' — {data.get('schedules_deactivated', 0)} "
                 "schedule(s) deactivated. Run history is retained.")


# ---------------------------------------------------------------------------
# Code-flow authoring tools
# ---------------------------------------------------------------------------

def _cf_list_arg(raw: str, what: str):
    if not raw:
        return [], None
    try:
        v = json.loads(raw)
        if not isinstance(v, list):
            return None, f"{what} must be a JSON array"
        return v, None
    except Exception:
        return None, f"{what} is not valid JSON (expected an array)"


def _summarize_walk(data: dict) -> str:
    """Port of CC summarize_walk — handled fail-edges never read as clean pass."""
    if data.get("status") == "error":
        return f"Code flow could not run: {data.get('error')}"
    steps = data.get("steps") or []
    bad = [s for s in steps if s.get("status") != "success"]
    if data.get("status") == "success" and bad:
        headline = f"**success — but {len(bad)} step(s) did not pass (handled via fail-edge)**"
    else:
        headline = f"**{data.get('status', '?')}**"
    lines = [f"Walk outcome: {headline} — {len(steps)} step(s) executed for real "
             "(live credentials, real side effects)"]
    marks = {"success": "✓", "failed": "✗", "error": "✗", "unverified": "⚠"}
    for i, s in enumerate(steps, 1):
        mark = marks.get(s.get("status"), "?")
        lines.append(f"{mark} step {i} — {s.get('name')} (exit {s.get('exit_code')}): "
                     f"{s.get('status')}")
        if s.get("output_files"):
            lines.append("    files: " + ", ".join(s["output_files"][:6]))
        if s.get("no_egress_transfer"):
            lines.append("    🚫 declared a remote transfer but NO network egress was "
                         "observed — nothing was transferred (do NOT report this "
                         "upload as attempted or done)")
        if s.get("status") in ("failed", "error", "unverified") and s.get("stderr_tail"):
            lines.append("    stderr: " + str(s["stderr_tail"])[-500:])
    return "\n".join(lines)


async def _cf_walk_timeout(name: str) -> float:
    """Size the client wait to the flow's own step timeouts (port of CC logic)."""
    data, status = await _manage_cf("get", {"name": name}, timeout=30)
    if status >= 400:
        return 900.0
    nodes = ((data.get("code_flow") or {}).get("nodes")) or []
    total = sum(int((n.get("config") or {}).get("timeout") or 600) + 30
                for n in nodes) + 120
    return float(max(300, min(total, 3600)))


@tool(
    "list_code_flows",
    "List code flows (multi-step Python playbooks stored as workflows).",
    {},
)
async def list_code_flows(args: dict[str, Any]) -> dict[str, Any]:
    data, status = await _manage_cf("list", {}, timeout=30)
    if status >= 400:
        return _text(f"Could not list code flows (HTTP {status}): "
                     f"{data.get('error', data)}", is_error=True)
    flows = data.get("code_flows") or []
    if not flows:
        return _text("No code flows exist yet.")
    return _text("Code flows:\n" + "\n".join(
        f"- {f.get('name')} (workflow id {f.get('workflow_id')}, "
        f"{f.get('step_count')} steps) — {f.get('description', '')}"
        for f in flows))


@tool(
    "create_code_flow",
    "Create a new empty code flow (a multi-step Python playbook). Add steps "
    "with add_code_step, connect them with wire_steps.",
    {
        "type": "object",
        "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    },
)
async def create_code_flow(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    data, status = await _manage_cf("create", {"name": str(args["name"]).strip(),
                                               "description": args.get("description") or ""},
                                    timeout=30)
    if status >= 400:
        return _text(f"Create failed (HTTP {status}): {data.get('error', data)}",
                     is_error=True)
    cf = data.get("code_flow") or {}
    return _text(f"Created code flow '{cf.get('name')}' (workflow id "
                 f"{cf.get('workflow_id')}). Next: add_code_step.")


@tool(
    "add_code_step",
    "Add a Python step to a code flow. Same aihub SDK as automations "
    "(aihub.query/connection/secret/input/log/checkpoint/send_email). Declare "
    "connections/secrets/packages the step uses as JSON arrays.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Code flow name"},
            "step_name": {"type": "string"},
            "code": {"type": "string"},
            "connections_json": {"type": "string", "description": "e.g. [\"ERPDB\"]"},
            "secrets_json": {"type": "string"},
            "packages_json": {"type": "string"},
            "timeout": {"type": "integer", "description": "Seconds (default 600)"},
        },
        "required": ["name", "step_name", "code"],
        "additionalProperties": False,
    },
)
async def add_code_step(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    payload: dict[str, Any] = {"name": str(args["name"]),
                               "step_name": str(args["step_name"]),
                               "code": str(args["code"])}
    for key, arg in (("connections", "connections_json"),
                     ("secrets", "secrets_json"), ("packages", "packages_json")):
        val, err = _cf_list_arg(args.get(arg) or "", arg)
        if err:
            return _text(err, is_error=True)
        if val:
            payload[key] = val
    if args.get("timeout"):
        payload["timeout"] = int(args["timeout"])
    data, status = await _manage_cf("add_step", payload, timeout=60)
    if status >= 400:
        return _text(f"Add step failed (HTTP {status}): {data.get('error', data)}",
                     is_error=True)
    return _text(f"Added step '{args['step_name']}' (id {data.get('step_id')}) "
                 f"to '{args['name']}'.")


@tool(
    "wire_steps",
    "Connect two steps in a code flow: on='pass' (success path) or 'fail' "
    "(handled-failure path).",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "from_step": {"type": "string"},
            "to_step": {"type": "string"},
            "on": {"type": "string", "enum": ["pass", "fail"]},
        },
        "required": ["name", "from_step", "to_step"],
        "additionalProperties": False,
    },
)
async def wire_steps(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    on = args.get("on") or "pass"
    data, status = await _manage_cf("wire", {"name": str(args["name"]),
                                             "from_step": str(args["from_step"]),
                                             "to_step": str(args["to_step"]),
                                             "on": on}, timeout=30)
    if status >= 400:
        return _text(f"Wire failed (HTTP {status}): {data.get('error', data)}",
                     is_error=True)
    return _text(f"Wired {args['from_step']} —[{on}]→ {args['to_step']} "
                 f"in '{args['name']}'.")


@tool(
    "unwire_steps",
    "Remove an edge between two steps of a code flow. Use it when INSERTING a "
    "step between two existing ones: after wiring A->NEW and NEW->B, unwire the "
    "old direct A->B edge — otherwise two competing 'pass' edges make the "
    "dry-run reject the flow. Leave `on` empty to remove every edge between the "
    "pair, or 'pass'/'fail' for just that type. Verified by read-back.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Code flow name"},
            "from_step": {"type": "string"},
            "to_step": {"type": "string"},
            "on": {"type": "string", "enum": ["pass", "fail"]},
        },
        "required": ["name", "from_step", "to_step"],
        "additionalProperties": False,
    },
)
async def unwire_steps(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    name = str(args["name"])
    src, dst = str(args["from_step"]), str(args["to_step"])
    on = str(args.get("on") or "").strip()
    payload = {"name": name, "from_step": src, "to_step": dst}
    if on:
        payload["on"] = on
    data, status = await _manage_cf("unwire", payload, timeout=30)
    if status >= 400:
        return _text(f"Unwire failed (HTTP {status}): {data.get('error', data)}",
                     is_error=True)
    got, gstat = await _manage_cf("get", {"name": name}, timeout=30)
    if gstat < 400:
        left = [e for e in ((got.get("code_flow") or {}).get("connections") or [])
                if e.get("source") == src and e.get("target") == dst
                and (not on or e.get("type") == on)]
        if left:
            return _text(f"Unwire reported success but read-back still shows "
                         f"{len(left)} edge(s) {src} -> {dst} — report as "
                         "UNVERIFIED.", is_error=True)
    return _text(f"Removed edge {src} → {dst}" + (f" [{on}]" if on else " (all types)")
                 + f" in '{name}' (verified by read-back). Dry-run again before "
                 "promoting or scheduling.")


@tool(
    "remove_code_step",
    "Remove a step from a code flow together with every edge touching it. If it "
    "was the start step, the start moves to the first remaining step — re-wire "
    "around the gap afterwards if needed. Step ids come from get_code_flow / "
    "add_code_step. Verified by read-back.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Code flow name"},
            "step_id": {"type": "string"},
        },
        "required": ["name", "step_id"],
        "additionalProperties": False,
    },
)
async def remove_code_step(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    name, step_id = str(args["name"]), str(args["step_id"])
    data, status = await _manage_cf("remove_step", {"name": name, "step_id": step_id},
                                    timeout=30)
    if status >= 400:
        return _text(f"Remove step failed (HTTP {status}): {data.get('error', data)}",
                     is_error=True)
    got, gstat = await _manage_cf("get", {"name": name}, timeout=30)
    if gstat < 400:
        nodes = (got.get("code_flow") or {}).get("nodes") or []
        if any(str(n.get("id")) == step_id for n in nodes):
            return _text(f"Remove reported success but step {step_id} is still in "
                         f"'{name}' on read-back — report as UNVERIFIED.", is_error=True)
        return _text(f"Removed step {step_id} (and its edges) from '{name}' — "
                     f"{len(nodes)} step(s) remain (verified by read-back). Check "
                     "the wiring with get_code_flow, then dry-run again.")
    return _text(f"Removed step {step_id} (and its edges) from '{name}'. "
                 "(Read-back unavailable — verify with get_code_flow.)")


@tool(
    "update_step_code",
    "Replace the Python code of an EXISTING step in a code flow — the way to "
    "fix a step after a dry-run shows it failing, instead of adding a duplicate "
    "step. Pass the complete new source. The server re-runs its credential "
    "scan and rejects hard-coded secrets. Dry-run again afterwards; promoted / "
    "scheduled runs keep using the pinned version until you promote.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Code flow name"},
            "step_id": {"type": "string"},
            "code": {"type": "string", "description": "Complete new Python source"},
        },
        "required": ["name", "step_id", "code"],
        "additionalProperties": False,
    },
)
async def update_step_code(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    name, step_id = str(args["name"]), str(args["step_id"])
    code = str(args.get("code") or "")
    if not code.strip():
        return _text("The new code is empty — nothing changed.", is_error=True)
    data, status = await _manage_cf("update_step_code",
                                    {"name": name, "step_id": step_id, "code": code},
                                    timeout=60)
    if status >= 400:
        return _text(f"Update failed (HTTP {status}): {data.get('error', data)} — "
                     "the step's previous code is unchanged.", is_error=True)
    got, gstat = await _manage_cf("get", {"name": name}, timeout=30)
    if gstat < 400:
        nodes = (got.get("code_flow") or {}).get("nodes") or []
        if not any(str(n.get("id")) == step_id for n in nodes):
            return _text(f"Update reported success but step {step_id} is not in "
                         f"'{name}' on read-back — report as UNVERIFIED.", is_error=True)
    return _text(f"Updated the code of step {step_id} in '{name}' ({len(code)} chars). "
                 "Dry-run the flow again before promoting or scheduling.")


@tool(
    "delete_code_flow",
    "Delete a code flow (its steps, wiring and schedules). TWO-STEP: first call "
    "without confirmed to get a summary of what would be deleted; call again "
    "with confirmed=true only after the user explicitly confirms. Verified by "
    "read-back.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Code flow name"},
            "confirmed": {"type": "boolean"},
        },
        "required": ["name"],
        "additionalProperties": False,
    },
)
async def delete_code_flow(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    name = str(args["name"]).strip()
    got, gstat = await _manage_cf("get", {"name": name}, timeout=30)
    if gstat >= 400:
        return _text(f"No code flow named '{name}' (HTTP {gstat}): "
                     f"{got.get('error', got)}", is_error=True)
    cf = got.get("code_flow") or {}
    if not args.get("confirmed"):
        return _text("⚠️ CONFIRMATION REQUIRED — nothing was deleted.\n"
                     f"Target: code flow '{cf.get('name', name)}' (workflow id "
                     f"{cf.get('workflow_id')}), {len(cf.get('nodes') or [])} step(s). "
                     "Deleting removes the flow and its schedules; run history is "
                     "retained. Ask the user to confirm, then call again with "
                     "confirmed=true.")
    data, status = await _manage_cf("delete", {"name": name}, timeout=60)
    if status >= 400:
        return _text(f"Delete failed (HTTP {status}): {data.get('error', data)} — "
                     "the code flow still exists.", is_error=True)
    again, astat = await _manage_cf("get", {"name": name}, timeout=30)
    if astat < 400 and (again.get("code_flow") or {}):
        return _text(f"Delete reported success but '{name}' can still be read back — "
                     "report as UNVERIFIED.", is_error=True)
    return _text(f"Deleted code flow '{name}' (verified by read-back: it no longer "
                 "exists).")


@tool(
    "get_code_flow",
    "Fetch a code flow's structure: steps, wiring, and per-step config.",
    {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    },
)
async def get_code_flow(args: dict[str, Any]) -> dict[str, Any]:
    data, status = await _manage_cf("get", {"name": str(args["name"])}, timeout=30)
    if status >= 400:
        return _text(f"Get failed (HTTP {status}): {data.get('error', data)}",
                     is_error=True)
    cf = data.get("code_flow") or {}
    nodes = cf.get("nodes") or []
    lines = [f"Code flow '{cf.get('name')}' (workflow id {cf.get('workflow_id')}) — "
             f"{len(nodes)} step(s)"]
    for n in nodes:
        cfgn = n.get("config") or {}
        lines.append(f"- {n.get('label')} (id {n.get('id')}"
                     f"{', START' if n.get('isStart') else ''}) — "
                     f"connections {cfgn.get('connections') or []}, "
                     f"timeout {cfgn.get('timeout')}s")
    for e in (cf.get("connections") or []):
        lines.append(f"  {e.get('source')} —[{e.get('type')}]→ {e.get('target')}")
    return _text("\n".join(lines))


async def _cf_run(action: str, name: str) -> dict:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    timeout = await _cf_walk_timeout(name)
    data, status = await _manage_cf(action, {"name": name}, timeout=timeout)
    if data.get("timed_out"):
        return _text(f"⏳ The walk timed out CLIENT-SIDE after {int(timeout)}s — the "
                     "flow may still be executing. Do NOT claim an outcome; check "
                     "run history before reporting, and never re-fire blindly "
                     "(a retry could double side effects).")
    if status >= 400 and data.get("status") != "error":
        return _text(f"{action} failed (HTTP {status}): {data.get('error', data)}",
                     is_error=True)
    return _text(_summarize_walk(data))


@tool(
    "dry_run_code_flow",
    "Execute a code flow's steps FOR REAL (live credentials, real side effects) "
    "to prove it works. Handled fail-edges are reported distinctly — never as a "
    "clean pass.",
    {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    },
)
async def dry_run_code_flow(args: dict[str, Any]) -> dict[str, Any]:
    return await _cf_run("dry_run", str(args["name"]))


@tool(
    "run_code_flow",
    "Run a code flow now (same real execution as dry_run in the current engine).",
    {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    },
)
async def run_code_flow(args: dict[str, Any]) -> dict[str, Any]:
    return await _cf_run("run", str(args["name"]))


@tool(
    "schedule_code_flow",
    "Schedule a code flow (cron_expression OR every_hours/every_days). It runs "
    "on the scheduler's existing workflow job type. Report ONLY the returned ids.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "cron_expression": {"type": "string"},
            "every_hours": {"type": "integer"},
            "every_days": {"type": "integer"},
        },
        "required": ["name"],
        "additionalProperties": False,
    },
)
async def schedule_code_flow(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_DENIED, is_error=True)
    if args.get("cron_expression"):
        schedule = {"type": "cron", "cron_expression": str(args["cron_expression"])}
    elif args.get("every_hours") or args.get("every_days"):
        schedule = {"type": "interval"}
        if args.get("every_hours"):
            schedule["interval_hours"] = int(args["every_hours"])
        if args.get("every_days"):
            schedule["interval_days"] = int(args["every_days"])
    else:
        return _text("Provide either cron_expression or every_hours/every_days.",
                     is_error=True)
    data, status = await _manage_cf("schedule", {"name": str(args["name"]),
                                                 "schedule": schedule}, timeout=60)
    if status >= 400:
        return _text(f"Schedule failed (HTTP {status}): {data.get('error', data)}",
                     is_error=True)
    if not data.get("scheduled_job_id"):
        return _text("Nothing was scheduled — the server returned no job id. "
                     "Do NOT tell the user it was scheduled.", is_error=True)
    return _text(f"Scheduled code flow '{args['name']}' "
                 f"(job #{data.get('scheduled_job_id')}, schedule "
                 f"#{data.get('schedule_id')}). {data.get('note', '')}")


AUTHORING_TOOLS = [
    create_automation, save_automation_code, get_automation,
    dry_run_automation, run_automation, check_automation_run,
    promote_automation, schedule_automation, decide_automation_checkpoint,
    delete_automation,
    list_code_flows, create_code_flow, add_code_step, wire_steps, unwire_steps, remove_code_step, update_step_code, delete_code_flow,
    get_code_flow, dry_run_code_flow, run_code_flow, schedule_code_flow,
]
