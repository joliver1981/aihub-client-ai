"""
Portal tools — The Agent's bridge to AI Hub's web-portal (RPA) machinery.
P1 of docs/the-agent-portal-gap-analysis.md.

These drive the SAME body-side seams Command Center's portal tools use: the
isolated Browser Use service (auto-mode LLM browser + deterministic workflow
replay, HOST_PORT+100), the per-user portal registry (data/portal_registry.json,
credential KEY NAMES only), the encrypted LocalSecretsManager, and the saved
portal-workflows store. Reused via CC's client cores in command_center/tools/
(APP_ROOT is on sys.path; browser_use_service already imports those stores
cross-package) — both brains drive one implementation. All CC-core imports are
LAZY inside tool bodies so an import problem degrades to an honest tool error
instead of killing service startup.

Deliberate differences from CC's closures:
- run ids thread EXPLICITLY through tool args/returns. CC tracks the active run
  in an in-process map that dies on restart; our disk-persisted session
  transcript carries the id naturally.
- bounded in-tool wait (AGENT_PORTAL_WAIT_SECONDS, default 120) then an honest
  handoff to check_portal_run — never a silent multi-minute block. A 2FA/human
  gate returns the take-over link IMMEDIATELY instead of holding the turn open.
- downloaded files are staged into the user's private downloads area IN the
  tool body (file_tools.stage_offer) and returned as ready /api/files/ links —
  users live in a web browser; server paths mean nothing to them.

Kill switches (checked in brain.py at registration): AGENT_PORTAL_TOOLS
(agent-local, default true) and the platform's BROWSER_USE_ENABLED (default
true — same env CC's nodes.py honors). Role gate inside every tool body:
Developer+ unless BROWSER_USE_ALLOW_ALL_USERS.
"""

import asyncio
import os
import time
import uuid
from typing import Any

from claude_agent_sdk import tool

from agent_config import APP_ROOT, logger
from platform_tools import CURRENT_USER, _text
from file_tools import stage_offer
from work_tools import (_build_schedule, _bound_was_recorded, _bound_text,
                        _cadence_text, default_zone_label, fmt_local)

# Bounded in-tool waits; runs continue server-side past these and are collected
# with check_portal_run (the dry_run -> check_automation_run doctrine shape).
WAIT_SECONDS = int(os.getenv("AGENT_PORTAL_WAIT_SECONDS", "120"))
CHECK_WAIT_SECONDS = int(os.getenv("AGENT_PORTAL_CHECK_WAIT_SECONDS", "60"))
WORKFLOW_TIMEOUT_SECONDS = int(os.getenv("AGENT_PORTAL_WORKFLOW_TIMEOUT", "600"))

_ALLOW_ALL = os.getenv("BROWSER_USE_ALLOW_ALL_USERS", "false").lower() == "true"
_DENIED = ("Portal access requires a Developer role on this instance. Your "
           "account doesn't have permission to drive web portals.")


def _allowed() -> bool:
    user = CURRENT_USER.get() or {}
    try:
        role = int(user.get("role") or 0)
    except (TypeError, ValueError):
        role = 0
    return _ALLOW_ALL or role >= 2


def _uid():
    return (CURRENT_USER.get() or {}).get("user_id")


def _session_marker() -> str:
    """Unique per-run marker: groups this run's downloads under its own dir on
    the browser service (and never collides across turns/users)."""
    return f"agent-{_uid()}-{uuid.uuid4().hex[:8]}"


def _upload_refusal(path: str):
    """(abs_path, None) for an uploadable server file, else (None, honest reason).
    Accepts an /api/files/<id> link (or bare file id) for a download the agent
    itself staged — resolved owner-scoped to this user's copy — as well as any
    OS-readable server path (CC parity, on-prem install), EXCEPT the platform's
    credential files and the OS tree."""
    p = str(path or "").strip().strip('"')
    if not p:
        return None, "no file specified"
    ap = os.path.abspath(os.path.expanduser(p))
    if not os.path.isfile(ap):
        # The model may hold only the /api/files/ link it delivered earlier.
        try:
            from file_tools import resolve_api_files_ref
            staged, _name = resolve_api_files_ref(p, int(_uid() or 0))
        except Exception:
            staged = None
        if staged:
            ap = staged
    secrets_dir = os.path.join(os.path.abspath(APP_ROOT), "data", "secrets")
    if ap.startswith(secrets_dir + os.sep) or \
            os.path.basename(ap).lower() in (".env", "secrets.json.enc"):
        return None, "that file is part of the platform's credential store and can never be uploaded"
    sysroot = os.path.abspath(os.environ.get("SystemRoot", r"C:\Windows"))
    if ap.lower().startswith(sysroot.lower() + os.sep):
        return None, "files under the OS directory can't be uploaded"
    if not os.path.isfile(ap):
        return None, (f"I couldn't find '{p}' on the server. Give the full path to a "
                      "file on this machine (list_server_files can help locate it), "
                      "or the /api/files link of a download you delivered earlier.")
    return ap, None


def _stage_files(uid, files):
    """Stage each downloaded file for this user. Returns (links, paths, errors) —
    links are user-facing, paths are the staged server copies the MODEL keeps
    as its own handle for import/upload follow-ups."""
    links, paths, errors = [], [], []
    for f in files or []:
        ok, msg, staged = stage_offer(int(uid or 0), f)
        if ok:
            links.append(msg)
            paths.append(staged)
        else:
            errors.append(f"{os.path.basename(str(f))}: {msg}")
    return links, paths, errors


def _autosave_draft(res: dict, uid) -> str:
    """CC parity: a successful ad-hoc run may carry a recorded, re-runnable
    draft workflow — save it so next time is a deterministic replay."""
    draft = res.get("draft_workflow") or {}
    steps = draft.get("steps") or []
    if not steps:
        return ""
    try:
        from command_center.tools import portal_workflows as wf_store
        saved = wf_store.save_workflow(uid, draft.get("name") or "Recorded portal run",
                                       steps, None, draft.get("start_url"),
                                       draft.get("goal"))
        return (f"This run was RECORDED as reusable portal workflow "
                f"'{saved['name']}' ({saved['step_count']} steps) — next time the user "
                f"can just ask to run that portal workflow for a deterministic replay "
                "(run_portal_workflow), or edit it on the Portal Workflows page.")
    except Exception as e:
        logger.warning(f"portal draft-workflow save failed: {e}")
        return ""


def _finish_run(res: dict, uid, kind: str = "portal task") -> dict:
    """Turn a FINISHED run manifest into an honest tool result: staged download
    links on success; exact failure / upload / no-file texts otherwise (ported
    from CC's _deliver_portal_result — never invent a delivery)."""
    files = res.get("files") or []
    links, staged_paths, stage_errors = _stage_files(uid, files)
    if links:
        out = ["The portal run finished. Include each download link VERBATIM "
               "in your reply:"]
        out += links
        if staged_paths:
            out.append("Server copies — YOUR OWN handle for follow-ups "
                       "(import_documents and upload_file accept the "
                       "/api/files link above or these paths; NEVER show a "
                       "raw path to the user): " + "; ".join(staged_paths))
        if stage_errors:
            out.append("Some downloaded files could NOT be staged — tell the user "
                       "honestly: " + "; ".join(stage_errors))
        final = str(res.get("final_result") or "").strip()
        if final:
            out.append(f"Browser agent's note: {final[:400]}")
        note = _autosave_draft(res, uid)
        if note:
            out.append(note)
        return _text("\n".join(out))
    if res.get("is_upload"):
        if res.get("status") == "ok":
            return _text("Upload completed. " + str(res.get("final_result")
                         or "The file was uploaded to the portal.").strip())
        return _text(f"The upload did NOT complete: {res.get('error') or 'unknown error'}. "
                     "Tell the user the upload failed; do NOT claim the file was uploaded.",
                     is_error=True)
    if res.get("status") != "ok":
        return _text(f"The {kind} failed: {res.get('error') or 'unknown error'}. "
                     "No file was downloaded and there is no download link to give "
                     "the user.", is_error=True)
    if files and stage_errors:
        return _text("The run downloaded file(s) but they could not be staged for the "
                     "user: " + "; ".join(stage_errors) + ". Tell the user honestly — "
                     "do NOT invent a download link.", is_error=True)
    # Read-only tasks ("tell me the balance shown on the account page"): the
    # browser agent reads the DOM/screenshots and its answer arrives as
    # final_result. Relay it — framed as a READING of on-screen text, never as a
    # document — instead of dropping it (James 2026-08-22; CC's wrapper still
    # drops it).
    reading = str(res.get("final_result") or "").strip()
    if reading:
        return _text(
            f"The {kind} finished with NO file downloaded. Browser agent's READING "
            "of the page — text it saw on screen, NOT a downloaded document, and "
            "only as reliable as its interpretation of the page:\n"
            f"{reading[:1500]}\n"
            "If the user asked for a FILE, say plainly that none was downloaded. If "
            "they asked for information shown on the page, answer from this "
            "reading and say it came from reading the portal page, not from a "
            "statement or document.")
    return _text(f"The {kind} ran but NO file was captured (0 files downloaded), so "
                 "there is nothing to deliver. Tell the user plainly that the download "
                 "did not complete — do NOT claim a file was delivered and do NOT "
                 "invent a download link.")


def _raise_takeover_item_if_headless(run_id: str, link: str, reason: str) -> str:
    """P2 item 2: a HEADLESS run (scheduled agent task, email-triggered turn) has
    no human watching the chat — put the take-over link in the owner's My Work
    so the pause is recoverable. Interactive turns just relay the link."""
    user = CURRENT_USER.get() or {}
    if str(user.get("mode") or "") != "headless":
        return ""
    try:
        import workitem_store
        item = workitem_store.create_item(
            "do_offline", f"Portal run needs you — {reason}"[:140],
            summary=(f"A headless portal run paused waiting for a person ({reason}).\n\n"
                     f"Take over the browser: {link}\n\nFinish the step there (e.g. type "
                     f"the code you received), click Hand back, and the run continues. "
                     f"run_id: {run_id}"),
            payload={"kind": "portal_takeover", "run_id": run_id, "link": link,
                     "reason": reason},
            addressed_user=int(user.get("user_id") or 0) or None,
            from_kind="agent_headless", from_ref="portal_fetch", priority=1,
            created_by=str(user.get("username") or "agent"))
        return (f"\nA My Work item (#{item['work_item_id']}) with this take-over link "
                "was raised for the user, since this is a headless run.")
    except Exception as e:
        logger.warning(f"portal take-over work item failed: {e}")
        return ""


async def _portal_schedule_jobs(client, base: str, headers: dict, uid, slug=None) -> list:
    """This user's portal_workflow scheduler jobs via the main-app scheduler REST
    (read-only): [{id, name, slug, active, next_run}], optionally only `slug`."""
    out = []
    r = await client.get(f"{base}/api/scheduler/jobs", headers=headers)
    jobs = r.json() if r.status_code < 400 else []
    if not isinstance(jobs, list):
        jobs = []
    for j in jobs:
        if str(j.get("type")) != "portal_workflow":
            continue
        jid = j.get("id")
        jr = await client.get(f"{base}/api/scheduler/jobs/{jid}", headers=headers)
        job = jr.json() if jr.status_code < 400 else {}
        params = job.get("parameters") or {}

        def _pv(k):
            v = params.get(k)
            return v.get("value") if isinstance(v, dict) else v
        if str(_pv("user_id")) != str(uid):
            continue
        if slug and str(_pv("workflow_slug")) != str(slug):
            continue
        scheds = job.get("schedules") or []
        out.append({"id": jid, "name": job.get("name") or j.get("name"),
                    "slug": _pv("workflow_slug"),
                    "active": any(s.get("is_active") for s in scheds),
                    "next_run": next((s.get("next_run_time") or s.get("next_run")
                                      for s in scheds
                                      if s.get("next_run_time") or s.get("next_run")),
                                     None)})
    return out


async def _poll_run(pf, run_id: str, budget_seconds: int, uid) -> dict:
    """Poll an async auto-run until done / needs-human / budget exhausted.
    Every branch returns honest, run_id-carrying text (the model threads the id
    into check_portal_run — no hidden in-process run map, unlike CC)."""
    deadline = time.time() + budget_seconds
    gone_strikes = 0
    res = {}
    while time.time() < deadline:
        res = await asyncio.to_thread(pf.get_portal_result, run_id, 15)
        if res.get("done"):
            return _finish_run(res, uid)
        err = str(res.get("error") or "")
        if "404" in err or "no such run" in err.lower():
            gone_strikes += 1
            # Tolerate a few transient misses (worker may still be registering);
            # only give up after consecutive strikes (CC parity).
            if gone_strikes >= 5:
                return _text(f"Portal run {run_id} is no longer active (the browser "
                             "service may have restarted). Start the portal task "
                             "again.", is_error=True)
        else:
            gone_strikes = 0
        if res.get("needs_human"):
            link = pf.cobrowse_link(run_id)
            reason = res.get("reason") or "a verification / login step"
            raised = _raise_takeover_item_if_headless(run_id, link, reason)
            return _text(
                f"PAUSED — the portal needs the user for {reason}.\n"
                f"Take-over link (relay it to the user VERBATIM): {link}\n"
                f"run_id: {run_id}\n"
                "Tell the user to open the link, finish the step (e.g. type the code "
                "they received), then click Hand back. When they say they're done, call "
                f'check_portal_run(run_id="{run_id}") to collect the result. Do NOT '
                "claim the file has downloaded — nothing is delivered yet." + raised)
        await asyncio.sleep(2)
    return _text(
        f"The portal run has NOT finished yet (waited {budget_seconds}s) and no file "
        f"has been captured so far. run_id: {run_id}\n"
        "It keeps running in the background, but nothing is delivered automatically — "
        f'call check_portal_run(run_id="{run_id}") in a moment to collect the result. '
        "Do NOT tell the user the file is downloading or that the task succeeded — "
        "that is not known yet.")


@tool(
    "lookup_portal",
    "List the user's saved web portals, or look one up by name — names and URLs only, "
    "NEVER credentials. Call this FIRST for any portal request ('download X from the Y "
    "portal', 'log into ...') to see whether the portal is already saved: a saved "
    "portal runs with just its name via portal_fetch, and the user must never be asked "
    "to re-share a saved login.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string",
                     "description": "Portal name to look up; omit or leave blank to "
                                    "list all saved portals."},
        },
        "additionalProperties": False,
    },
)
async def lookup_portal(args: dict[str, Any]) -> dict[str, Any]:
    if not _allowed():
        return _text(_DENIED, is_error=True)
    try:
        from command_center.tools import portal_registry as reg
    except Exception as e:
        return _text(f"Portal registry unavailable: {e}", is_error=True)
    uid = _uid()
    name = str(args.get("name") or "").strip()
    if name:
        e = await asyncio.to_thread(reg.lookup_portal, uid, name)
        if not e:
            names = ", ".join(p["name"] for p in reg.list_portals(uid)) or "(none saved)"
            return _text(f"No saved portal matches '{name}'. Saved portals: {names}. "
                         "For a new portal, the user can give the URL + login for an "
                         "ad-hoc portal_fetch run.")
        creds = "credentials stored" if e.get("password_secret") else "NO credentials stored"
        return _text(f"{e.get('name')} -> {e.get('url')} ({creds}; ready to use — call "
                     "portal_fetch with just the portal name and the task).")
    portals = await asyncio.to_thread(reg.list_portals, uid)
    if not portals:
        return _text("No saved portals yet. For a first run the user gives the portal "
                     "URL and login in chat (portal_fetch ad-hoc); after it succeeds, "
                     "offer save_portal so next time the name alone is enough.")
    lines = "\n".join(f"- {p['name']} -> {p['url']}" for p in portals)
    return _text("Saved portals (portal_fetch works with just the name):\n" + lines)


@tool(
    "save_portal",
    "Save a web portal and its login so the user never has to share it again — "
    "credentials go to the ENCRYPTED server-side store, never echoed back; only key "
    "references are kept. Call after a successful ad-hoc portal_fetch when the user "
    "agrees to save, or when they ask you to remember a portal login. Afterwards "
    "portal_fetch works with just the portal name.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Short name to remember the portal by (e.g. 'acme')."},
            "url": {"type": "string", "description": "The portal's login URL."},
            "username": {"type": "string", "description": "Login username to store."},
            "password": {"type": "string", "description": "Login password to store."},
            "totp": {"type": "string", "description": "TOTP 2FA shared secret to store (optional)."},
            "allowed_domains": {"type": "string",
                                "description": "Extra comma-separated domains to permit (e.g. an "
                                               "SSO host); usually blank — the portal's own domain "
                                               "is allowed automatically."},
        },
        "required": ["name", "url", "username", "password"],
        "additionalProperties": False,
    },
)
async def save_portal(args: dict[str, Any]) -> dict[str, Any]:
    if not _allowed():
        return _text(_DENIED, is_error=True)
    try:
        from command_center.tools import portal_registry as reg
        from local_secrets import get_local_secret
    except Exception as e:
        return _text(f"Portal registry unavailable: {e}", is_error=True)
    uid = _uid()
    name = str(args.get("name") or "").strip()
    url = str(args.get("url") or "").strip()
    doms = [d.strip() for d in str(args.get("allowed_domains") or "").split(",")
            if d.strip()] or None
    try:
        entry = await asyncio.to_thread(
            reg.save_portal, uid, name, url, str(args.get("username") or ""),
            str(args.get("password") or ""), str(args.get("totp") or "") or None, doms)
    except Exception as e:
        logger.error(f"save_portal failed: {e}", exc_info=True)
        return _text(f"Couldn't save the portal: {e}", is_error=True)
    # Read-back verification (never echoes a value): registry entry exists AND
    # the stored password decrypts to something non-empty.
    try:
        back = await asyncio.to_thread(reg.lookup_portal, uid, name)
        verified = bool(back) and bool(get_local_secret(back.get("password_secret") or ""))
    except Exception as e:
        logger.warning(f"save_portal read-back failed: {e}")
        verified = False
    if not verified:
        return _text(f"The save did NOT verify: '{name}' was written but the read-back "
                     "check could not confirm the stored credentials. Report this "
                     "honestly and do not claim the portal is ready.", is_error=True)
    return _text(f"Saved '{entry['name']}' ({entry['url']}) — read-back verified. Next "
                 f"time just ask for '{entry['slug']}' and the stored credentials are "
                 "used automatically; the user never needs to share them again.")


@tool(
    "portal_fetch",
    "Sign into a web portal with a real browser and DO a task there: DOWNLOAD files "
    "('get my latest invoice from the Meridian portal') or UPLOAD one. Three call modes: "
    "(1) SAVED portal — pass portal_name + task only; the URL and credentials resolve "
    "automatically (never ask the user to re-share a saved login). "
    "(2) AD-HOC first run — the user gives the URL and login in chat: pass start_url, "
    "username, password (and totp if provided) and act right away, don't stall; after "
    "success OFFER save_portal. "
    "(3) UPLOAD — additionally pass upload_file (a server file path) and describe the "
    "upload in task. "
    "READ-ONLY tasks work too: ask it to REPORT what is on the page ('open the account "
    "summary and report the current balance shown') — the result carries the browser "
    "agent's reading of the screen; no file is produced. "
    "Waits in-tool up to ~2 minutes; a longer run hands back a run_id to collect with "
    "check_portal_run. If the portal pauses for 2FA/verification, this returns a "
    "take-over LINK to relay to the user verbatim. Downloaded files come back as "
    "/api/files/ links — include them verbatim in your reply.",
    {
        "type": "object",
        "properties": {
            "portal_name": {"type": "string",
                            "description": "Short portal name (e.g. 'acme') — matches a saved "
                                           "portal, or labels an ad-hoc run."},
            "task": {"type": "string",
                     "description": "What to do once signed in, e.g. 'download the most recent "
                                    "invoice' or 'upload the file on the Documents page'."},
            "start_url": {"type": "string",
                          "description": "Login URL — required for ad-hoc runs; omit for a "
                                         "saved portal."},
            "username": {"type": "string", "description": "ONLY for an ad-hoc first run the user typed in chat."},
            "password": {"type": "string", "description": "ONLY for an ad-hoc first run."},
            "totp": {"type": "string", "description": "TOTP 2FA shared secret, if the user provides one."},
            "upload_file": {"type": "string",
                            "description": "Server path of a file to UPLOAD; omit for downloads."},
        },
        "required": ["portal_name", "task"],
        "additionalProperties": False,
    },
)
async def portal_fetch(args: dict[str, Any]) -> dict[str, Any]:
    if not _allowed():
        return _text(_DENIED, is_error=True)
    try:
        from command_center.tools import portal_fetch as pf
        from command_center.tools import portal_registry as reg
    except Exception as e:
        return _text(f"Portal client unavailable: {e}", is_error=True)
    uid = _uid()
    portal_name = str(args.get("portal_name") or "").strip()
    task = str(args.get("task") or "").strip()
    start_url = str(args.get("start_url") or "").strip()
    username = str(args.get("username") or "")
    password = str(args.get("password") or "")
    totp = str(args.get("totp") or "")

    upload_files = None
    if str(args.get("upload_file") or "").strip():
        path, reason = _upload_refusal(args.get("upload_file"))
        if not path:
            return _text(f"Upload refused: {reason}", is_error=True)
        upload_files = [path]

    entry = await asyncio.to_thread(reg.lookup_portal, uid, portal_name) if portal_name else None
    eff_url = start_url or (entry or {}).get("url") or ""
    inline = None
    overrides = None
    if username and password:
        inline = {"username": username, "password": password, "totp": totp}
    elif entry:
        overrides = {"username_secret": entry.get("username_secret"),
                     "password_secret": entry.get("password_secret"),
                     "totp_secret": entry.get("totp_secret")}
    if not eff_url:
        return _text(f"I don't have a login URL for '{portal_name}'. Ask the user for the "
                     "portal's URL (and their login, unless it's already saved) and call "
                     "this again.", is_error=True)
    mode = "inline" if inline else ("saved" if overrides else "legacy")
    logger.info(f"portal_fetch user={uid} portal={portal_name!r} url={eff_url} "
                f"mode={mode} upload={'yes' if upload_files else 'no'}")

    start = await asyncio.to_thread(
        pf.start_portal_fetch, portal_name, eff_url, task, _session_marker(),
        {"user_id": uid}, overrides, inline, upload_files)
    if start.get("error"):
        return _text(f"I couldn't start the portal run: {start['error']}", is_error=True)
    run_id = start.get("run_id")
    if not run_id:
        return _text("I couldn't start the portal run (no run id returned).", is_error=True)
    return await _poll_run(pf, run_id, WAIT_SECONDS, uid)


@tool(
    "check_portal_run",
    "Check on / collect a portal run started earlier by portal_fetch. Use it after the "
    "user finishes a take-over (2FA/verification) step, or when a run outlived the "
    "in-tool wait. Pass the run_id from the portal_fetch result. Returns staged "
    "/api/files/ download links when the run finished, the take-over link again if it "
    "still needs the user, or an honest still-running status.",
    {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "The run_id from portal_fetch."},
        },
        "required": ["run_id"],
        "additionalProperties": False,
    },
)
async def check_portal_run(args: dict[str, Any]) -> dict[str, Any]:
    if not _allowed():
        return _text(_DENIED, is_error=True)
    try:
        from command_center.tools import portal_fetch as pf
    except Exception as e:
        return _text(f"Portal client unavailable: {e}", is_error=True)
    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        return _text("No run_id given — pass the run_id from the portal_fetch result.",
                     is_error=True)
    return await _poll_run(pf, run_id, CHECK_WAIT_SECONDS, _uid())


@tool(
    "list_portal_workflows",
    "List the user's saved PORTAL workflows — recorded browser/RPA login-and-download "
    "(or upload) sequences replayed deterministically. These are NOT the platform's "
    "regular workflows/playbooks (list_playbooks); never confuse the two. Returns names, "
    "targets and step counts only — never credentials.",
    {"type": "object", "properties": {}, "additionalProperties": False},
)
async def list_portal_workflows(args: dict[str, Any]) -> dict[str, Any]:
    if not _allowed():
        return _text(_DENIED, is_error=True)
    try:
        from command_center.tools import portal_workflows as wf_store
    except Exception as e:
        return _text(f"Portal workflow store unavailable: {e}", is_error=True)
    wfs = await asyncio.to_thread(wf_store.list_workflows, _uid())
    if not wfs:
        return _text("No saved portal workflows yet. A successful ad-hoc portal_fetch "
                     "is auto-recorded as one when possible, or the user can record one "
                     "on the Portal Workflows page.")
    lines = []
    for w in wfs:
        cap = "uploads" if w.get("uploads") else "downloads"
        target = w.get("portal_slug") or w.get("start_url") or "—"
        goal = str(w.get("goal") or "").strip()
        goal = (" — " + goal[:80] + ("…" if len(goal) > 80 else "")) if goal else ""
        last = f", last: {w['last_run_status']}" if w.get("last_run_status") else ""
        lines.append(f"- {w['name']} [{cap}] target: {target} "
                     f"({w.get('step_count', 0)} steps{last}){goal}")
    return _text("Saved portal workflows (describe_portal_workflow shows one's steps):\n"
                 + "\n".join(lines))


@tool(
    "describe_portal_workflow",
    "Show what a saved PORTAL workflow does BEFORE running it — target portal/URL, goal, "
    "whether it uploads or downloads, and an ordered step summary (never credentials). "
    "Use when the user's ask only loosely matches a saved workflow and you want to "
    "confirm it's the right one.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string",
                     "description": "The saved portal-workflow's name (list_portal_workflows "
                                    "shows the exact names)."},
        },
        "required": ["name"],
        "additionalProperties": False,
    },
)
async def describe_portal_workflow(args: dict[str, Any]) -> dict[str, Any]:
    if not _allowed():
        return _text(_DENIED, is_error=True)
    try:
        from command_center.tools import portal_workflows as wf_store
    except Exception as e:
        return _text(f"Portal workflow store unavailable: {e}", is_error=True)
    name = str(args.get("name") or "").strip()
    wf = await asyncio.to_thread(wf_store.get_workflow, _uid(), name)
    if not wf:
        return _text(f"No saved portal workflow matches '{name}'. Call "
                     "list_portal_workflows to see the exact names.", is_error=True)
    steps = wf.get("steps") or []
    types = [s.get("type") for s in steps if isinstance(s, dict)]

    def _label(s):
        t = s.get("type")
        a = s.get("anchor") or {}
        who = a.get("text") or a.get("css") or a.get("name") or ""
        if t == "goto":
            return f"go to {s.get('url', '')}"
        if t == "login":
            return "log in"
        if t == "click":
            return f"click {who or 'element'}"
        if t == "fill":
            if s.get("value"):
                extra = f" = {s.get('value')}"
            elif s.get("secret"):
                extra = f" ({s.get('secret')})"
            else:
                extra = ""
            return f"fill {who or 'field'}{extra}"
        if t == "wait":
            return "wait"
        if t == "agent":
            return f"AI step: {(s.get('prompt') or s.get('task') or '')[:60]}"
        if t == "verify":
            return "verify a file downloaded" if s.get("downloaded") else "verify"
        if t == "human":
            return "pause for a person"
        if t == "verify_code":
            return "enter a 2FA / verification code"
        if t == "upload":
            return f"upload the provided file into {who or 'the file input'}"
        return t or "?"

    cap = "uploads a file" if "upload" in types else "downloads file(s)"
    summary = "; ".join(f"{i + 1}. {_label(s)}" for i, s in enumerate(steps)) or "(no steps)"
    target = wf.get("portal_slug") or wf.get("start_url") or "—"
    # Tell the model whether this workflow is ALREADY scheduled (and the job
    # id) so a "schedule it" ask REPLACES rather than duplicates.
    sched_line = ""
    try:
        import httpx
        from platform_tools import _headers
        from agent_config import get_base_url
        async with httpx.AsyncClient(timeout=30) as client:
            jobs = await _portal_schedule_jobs(client, get_base_url(), _headers(),
                                               _uid(), wf.get("slug"))
        if jobs:
            j0 = jobs[0]
            nxt = ""
            if j0.get("next_run"):
                try:
                    import datetime as _dt2
                    _nr = _dt2.datetime.fromisoformat(str(j0["next_run"]).replace("Z", ""))
                    nxt = f", next run {fmt_local(_nr, default_zone_label(CURRENT_USER.get() or {})[0])}"
                except (ValueError, TypeError):
                    nxt = f", next run {j0['next_run']} UTC"
            sched_line = (f"\n- ALREADY SCHEDULED: job #{j0['id']} "
                          f"({'active' if j0['active'] else 'inactive'}{nxt}). "
                          "Re-scheduling REPLACES it; cancel_portal_workflow_schedule "
                          "removes it.")
    except Exception as e:
        logger.warning(f"describe_portal_workflow schedule lookup failed: {e}")
    return _text(f"Portal workflow '{wf.get('name', name)}':\n- This workflow {cap}.\n"
                 f"- Target: {target}\n- Goal: {wf.get('goal') or '(none)'}\n"
                 f"- Last run: {wf.get('last_run_status') or 'never run'}{sched_line}\n"
                 f"- Steps: {summary}")


@tool(
    "run_portal_workflow",
    "Run a saved PORTAL workflow by name — a recorded browser/RPA sequence that signs "
    "into a web portal and downloads (or uploads) files, replayed deterministically; "
    "credentials resolve automatically from the linked saved portal. ONLY for portal "
    "workflows (list_portal_workflows) — NOT the platform's regular workflows/playbooks. "
    "To upload, the workflow must contain an 'upload' step; pass upload_file (server "
    "path). Blocks until the replay finishes (can take a few minutes). Downloads come "
    "back as /api/files/ links — include them verbatim.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The saved portal-workflow's name."},
            "upload_file": {"type": "string",
                            "description": "Server path of a file to hand to the workflow's "
                                           "upload step; omit for download-only workflows."},
        },
        "required": ["name"],
        "additionalProperties": False,
    },
)
async def run_portal_workflow(args: dict[str, Any]) -> dict[str, Any]:
    if not _allowed():
        return _text(_DENIED, is_error=True)
    try:
        from command_center.tools import portal_workflow_run as wfr
    except Exception as e:
        return _text(f"Portal workflow client unavailable: {e}", is_error=True)
    uid = _uid()
    name = str(args.get("name") or "").strip()
    inputs = None
    if str(args.get("upload_file") or "").strip():
        path, reason = _upload_refusal(args.get("upload_file"))
        if not path:
            return _text(f"Upload refused: {reason}", is_error=True)
        inputs = {"files": [path]}
    logger.info(f"run_portal_workflow user={uid} name={name!r} "
                f"upload={'yes' if inputs else 'no'}")
    try:
        res = await asyncio.to_thread(
            wfr.run_workflow_by_name, name, _session_marker(), {"user_id": uid},
            WORKFLOW_TIMEOUT_SECONDS, inputs=inputs)
    except Exception as e:
        logger.error(f"run_portal_workflow failed: {e}", exc_info=True)
        return _text(f"Portal workflow run failed to start: {e}", is_error=True)
    err = str(res.get("error") or "")
    if err.startswith("no saved workflow"):
        return _text(f"{err}. Call list_portal_workflows to see the exact names.",
                     is_error=True)
    return _finish_run(res, uid, kind=f"portal workflow '{name}'")


@tool(
    "schedule_portal_workflow",
    "Schedule a saved PORTAL workflow to run automatically on a cadence — a headless, "
    "deterministic replay of its recorded steps (zero AI per run) with credentials from "
    "the linked saved portal. Each run's outcome lands in the user's My Work as an FYI "
    "with working download links (and the file by email when email_after_run is true). "
    "Re-scheduling the same workflow REPLACES its existing schedule. Give cron_expression "
    "(+timezone) OR every_minutes/every_hours/every_days; add for_minutes or occurrences "
    "for a BOUNDED repeat ('every 10 minutes for an hour' = every_minutes=10, "
    "for_minutes=60 — one job the engine stops on its own). The cron fires in `timezone` "
    "(engine applies the zone at fire time, DST-aware). Unattended 2FA needs a "
    "TOTP-seeded portal; a run that still pauses for a human raises a take-over item in "
    "My Work. Report ONLY the job id and cadence/bound facts this returns.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The saved portal-workflow's name."},
            "cron_expression": {"type": "string",
                                "description": "5-field cron in the LOCAL timezone given "
                                               "by `timezone`."},
            "timezone": {"type": "string",
                         "description": "IANA name (America/New_York), alias (Eastern), "
                                        "'UTC' or '-05:00'; omit for the server's zone."},
            "every_minutes": {"type": "integer",
                              "description": "Interval in minutes (min 1)."},
            "every_hours": {"type": "integer"},
            "every_days": {"type": "integer"},
            "for_minutes": {"type": "integer",
                            "description": "BOUND: stop this many minutes from now."},
            "occurrences": {"type": "integer",
                            "description": "BOUND: stop after this many runs."},
            "email_after_run": {"type": "boolean",
                                "description": "Also email the downloaded file to the "
                                               "user after each run (default false)."},
        },
        "required": ["name"],
        "additionalProperties": False,
    },
)
async def schedule_portal_workflow(args: dict[str, Any]) -> dict[str, Any]:
    if not _allowed():
        return _text(_DENIED, is_error=True)
    user = CURRENT_USER.get() or {}
    if int(user.get("role") or 0) < 2 and os.getenv(
            "AGENT_BUILD_ALLOW_ALL_USERS", "false").lower() != "true":
        return _text("Scheduling portal workflows requires a Developer role.",
                     is_error=True)
    import datetime as _dt
    import httpx
    from platform_tools import _headers
    from agent_config import get_base_url
    try:
        from command_center.tools import portal_workflows as wf_store
    except Exception as e:
        return _text(f"Portal workflow store unavailable: {e}", is_error=True)
    uid = _uid()
    name = str(args.get("name") or "").strip()
    wf = await asyncio.to_thread(wf_store.get_workflow, uid, name)
    if not wf:
        return _text(f"No saved portal workflow matches '{name}'. Call "
                     "list_portal_workflows to see the exact names.", is_error=True)
    slug = wf["slug"]

    # Same schedule builder as schedule_agent_task: engine-native cron timezone
    # (store the cron as written + the zone; the engine applies it, DST-aware —
    # pre-shifting to UTC double-shifts) and engine-native bounds
    # (end_date / max_runs) for "every N minutes for M minutes".
    _now = _dt.datetime.utcnow()
    _dz, _dsrc = default_zone_label(user)      # browser > AGENT_DEFAULT_TZ > server
    try:
        plan = _build_schedule(args, now=_now, default_tz=_dz, default_src=_dsrc)
    except ValueError as e:
        return _text(f"Nothing was scheduled: {e}", is_error=True)
    if plan["kind"] == "one_shot":
        return _text("Portal workflow schedules are recurring — give cron_expression "
                     "(+timezone) or every_minutes/every_hours/every_days (optionally "
                     "with for_minutes/occurrences).", is_error=True)
    schedule = plan["schedule"]
    tz_note = plan["note"]

    email_after = bool(args.get("email_after_run"))
    body = {
        "name": f"Portal workflow: {wf.get('name') or slug}"[:80],
        "type": "portal_workflow",
        "target_id": int(uid) if str(uid).isdigit() else 0,
        "description": f"Scheduled portal workflow '{slug}' for user {uid}",
        "created_by": str(user.get("username") or "agent"),
        "is_active": True,
        "parameters": {
            "workflow_slug": {"value": str(slug), "type": "string"},
            "user_id": {"value": str(uid), "type": "string"},
            "tenant_id": {"value": str(user.get("tenant_id") or ""), "type": "string"},
            "email_after": {"value": "1" if email_after else "0", "type": "string"},
            "user_timezone": {"value": str(plan.get("display_tz") or ""), "type": "string"},
        },
        "schedule": schedule,
    }
    for k, v in (plan.get("params") or {}).items():
        # cron provenance; the engine reads `timezone` to fire in that zone
        body["parameters"][k] = {"value": str(v), "type": "string"}

    base, hdrs = get_base_url(), _headers()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # UPDATE-NOT-DUPLICATE (CC parity): replace any existing schedule
            # for this workflow instead of stacking a second job.
            replaced = []
            for j in await _portal_schedule_jobs(client, base, hdrs, uid, slug):
                dr = await client.delete(f"{base}/api/scheduler/jobs/{j['id']}",
                                         headers=hdrs)
                if dr.status_code < 400:
                    replaced.append(j["id"])
            r = await client.post(f"{base}/api/scheduler/jobs", json=body, headers=hdrs)
            data = r.json() if r.status_code < 500 else {}
            if r.status_code >= 400 or not data.get("id"):
                return _text(f"Nothing was scheduled (HTTP {r.status_code}: "
                             f"{data.get('error', r.text[:200])}). Do NOT tell the "
                             "user it was scheduled.", is_error=True)
            job_id = data["id"]
            rb = await client.get(f"{base}/api/scheduler/jobs/{job_id}", headers=hdrs)
            rbd = rb.json() if rb.status_code < 400 else {}
            if not any(s.get("is_active") for s in (rbd.get("schedules") or [])):
                return _text(f"Job #{job_id} was created but NO active schedule row "
                             "exists — report this as NOT scheduled.", is_error=True)
            if not _bound_was_recorded(plan, rbd.get("schedules")):
                try:   # fail closed: a bounded ask must never leave an unbounded job
                    await client.delete(f"{base}/api/scheduler/jobs/{job_id}", headers=hdrs)
                except Exception:
                    pass
                return _text(f"Job #{job_id} was created but the engine did NOT record "
                             "the requested bound (end_date/max_runs) — it was removed "
                             "so nothing runs forever. Report this as NOT scheduled.",
                             is_error=True)
    except Exception as e:
        return _text(f"Scheduling failed: {e}", is_error=True)
    rep = f" Replaced previous job(s) #{', #'.join(str(x) for x in replaced)}." if replaced else ""
    bound = _bound_text(plan, _now)
    return _text(f"Scheduled portal workflow '{wf.get('name') or slug}' (job #{job_id}, "
                 f"verified active by read-back): {_cadence_text(plan)}"
                 + (f", {bound}; the engine stops it on its own" if bound else "")
                 + f".{rep} Each run replays the recorded "
                 f"steps headless as {user.get('username')} and lands an FYI with "
                 "download links in their My Work"
                 + (" and emails the file." if email_after else ".") + tz_note)


@tool(
    "cancel_portal_workflow_schedule",
    "Cancel the recurring schedule of a saved PORTAL workflow (deletes its scheduler "
    "job). Two-step: call once to see what exists, then again with confirmed=true after "
    "the user agrees. Says plainly when no schedule exists.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The saved portal-workflow's name."},
            "confirmed": {"type": "boolean",
                          "description": "true only after the user confirmed the cancel."},
        },
        "required": ["name"],
        "additionalProperties": False,
    },
)
async def cancel_portal_workflow_schedule(args: dict[str, Any]) -> dict[str, Any]:
    if not _allowed():
        return _text(_DENIED, is_error=True)
    import httpx
    from platform_tools import _headers
    from agent_config import get_base_url
    try:
        from command_center.tools import portal_workflows as wf_store
    except Exception as e:
        return _text(f"Portal workflow store unavailable: {e}", is_error=True)
    uid = _uid()
    name = str(args.get("name") or "").strip()
    wf = await asyncio.to_thread(wf_store.get_workflow, uid, name)
    if not wf:
        return _text(f"No saved portal workflow matches '{name}'. Call "
                     "list_portal_workflows to see the exact names.", is_error=True)
    base, hdrs = get_base_url(), _headers()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            jobs = await _portal_schedule_jobs(client, base, hdrs, uid, wf["slug"])
            if not jobs:
                return _text(f"No schedule exists for portal workflow '{wf.get('name')}' "
                             "— nothing to cancel.")
            desc = ", ".join(f"job #{j['id']} ({'active' if j['active'] else 'inactive'})"
                             for j in jobs)
            if not args.get("confirmed"):
                return _text(f"Found {desc} for '{wf.get('name')}'. Ask the user to "
                             "confirm, then call again with confirmed=true to delete it.")
            removed, failed = [], []
            for j in jobs:
                dr = await client.delete(f"{base}/api/scheduler/jobs/{j['id']}",
                                         headers=hdrs)
                chk = await client.get(f"{base}/api/scheduler/jobs/{j['id']}", headers=hdrs)
                gone = dr.status_code < 400 and chk.status_code >= 400
                (removed if gone else failed).append(j["id"])
    except Exception as e:
        return _text(f"Cancel failed: {e}", is_error=True)
    if failed:
        return _text(f"Could not verify removal of job(s) #{', #'.join(map(str, failed))}"
                     f"{' (removed: ' + ', '.join(map(str, removed)) + ')' if removed else ''}"
                     " — report this honestly.", is_error=True)
    return _text(f"Cancelled the schedule for '{wf.get('name')}': removed job(s) "
                 f"#{', #'.join(map(str, removed))} (verified gone by read-back).")


PORTAL_TOOLS = [
    lookup_portal, save_portal, portal_fetch, check_portal_run,
    list_portal_workflows, describe_portal_workflow, run_portal_workflow,
    schedule_portal_workflow, cancel_portal_workflow_schedule,
]
