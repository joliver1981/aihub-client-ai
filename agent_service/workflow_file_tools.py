"""Workflow file drops for The Agent (2026-10-10).

A workflow that takes files in and puts files out runs on the server; the
person is in a browser. File drops (workflow_file_drops.py in the main app)
give such a workflow managed folders: Waiting (inbox), Processed, Failed and
Results (outbox). These tools let The Agent use them for the person in chat:

  list_workflow_files      what is waiting, what came out, recent runs
  setup_workflow_files     create the folders and point the workflow's folder
                           variables at them (Developer; so it never has to ask
                           anyone for a server path)
  add_workflow_files       chat attachments -> the workflow's Waiting folder
  run_workflow_on_files    (attachments ->) Waiting -> run -> results back as
                           download links in the chat
  get_workflow_files       results (or any folder's files) as download links
  retry_workflow_files     failed files back to Waiting, optionally run
  share_workflow_files     who else may drop and collect (Developer)

Every call goes through the main app's /api/workflow-files routes with the
platform key AND the user's signed assertion, so the platform decides access
the same way it does for the Files page: Developers and Admins on every
workflow, anyone else only where the workflow is shared with them or their
group. Nothing here reads the managed folders directly.

Kill switch AGENT_WORKFLOW_FILE_TOOLS=false (brain.py); the platform's own
switch WORKFLOW_FILE_DROPS_ENABLED=false makes every route answer 404.
"""
import asyncio
import os
import re
import time
import uuid
from typing import Any, Optional
from urllib.parse import quote

import httpx
from claude_agent_sdk import tool

from agent_config import get_base_url, logger
from authoring_tools import _authoring_allowed, _user_context
from platform_tools import _headers, _text

_OFF = ("Workflow file drops are turned off on this platform (WORKFLOW_FILE_DROPS_ENABLED), "
        "or this platform version does not have them.")
_MANAGE_DENIED = ("Setting up or sharing a workflow's files needs a Developer role on this install. "
                  "You can still drop files into, run and collect from workflows shared with you.")
_FILES_PAGE = "Workflow Files (in AI Hub's menu, or Files in The Agent's menu)"
RUN_WAIT_SECONDS = float(os.getenv("WORKFLOW_RUN_WAIT_SECONDS", "600"))
_TERMINAL = {"completed", "failed", "cancelled", "error"}
_UUID_PREFIX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}__", re.I)


# ------------------------------------------------------------------ platform
async def _call(method: str, path: str, *, body=None, params=None, timeout: float = 60.0):
    """(status, data) from a main-app route; never raises."""
    try:
        headers = _headers()
    except Exception as e:
        return 0, {"message": f"could not prove who you are to the platform: {e}"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            r = await client.request(method, f"{get_base_url()}{path}", json=body,
                                     params=params, headers=headers)
    except Exception as e:
        return 0, {"message": f"could not reach the platform: {e}"}
    try:
        data = r.json()
    except Exception:
        data = {"message": (r.text or "")[:300]}
    return r.status_code, data if isinstance(data, dict) else {"data": data}


def _err(status: int, data: dict) -> str:
    # The routes' own refusals carry "status"; a bare 404 is the switch being
    # off (or an older platform without the routes).
    if data.get("disabled") or (status == 404 and not data.get("status")):
        return _OFF
    return str(data.get("message") or data.get("error") or f"HTTP {status}")


async def _resolve(ref, include_unset: bool = False):
    """(workflow_id, name, error). Matches an id, an exact name, then a unique
    partial name among the workflows whose files this user can use (plus,
    for Developers with include_unset, workflows not set up yet)."""
    ref = str(ref or "").strip()
    if not ref:
        return None, None, "Name the workflow (its name or id)."
    status, data = await _call("GET", "/api/workflow-files",
                               params={"candidates": "1"} if include_unset else None)
    if status != 200:
        return None, None, _err(status, data)
    rows = [(int(w["workflow_id"]), str(w["name"]), True) for w in data.get("workflows") or []]
    rows += [(int(w["workflow_id"]), str(w["name"]), False) for w in data.get("candidates") or []]
    if ref.isdigit():
        hit = [r for r in rows if r[0] == int(ref)]
    else:
        hit = [r for r in rows if r[1].lower() == ref.lower()] or \
              [r for r in rows if ref.lower() in r[1].lower()]
    if len(hit) == 1:
        return hit[0][0], hit[0][1], None
    if len(hit) > 1:
        return None, None, ("Several workflows match '" + ref + "': "
                            + "; ".join(f"{n} (id {i})" for i, n, _ in hit[:15])
                            + ". Ask which one.")
    usable = [n for _i, n, s in rows if s]
    if not usable:
        return None, None, ("No workflow's files are available to this user"
                            + (" yet. A Developer sets file drops up for a workflow first."
                               if include_unset or data.get("can_manage") else
                               ": none is shared with them. The workflow's owner (a Developer) can "
                               "share it with them or one of their groups."))
    return None, None, (f"No workflow named '{ref}' has files available to this user. Available: "
                        + "; ".join(usable[:30]))


def _display_name(path: str, fallback: str = "") -> str:
    return _UUID_PREFIX.sub("", os.path.basename(fallback or path)) or "file"


def _resolve_file(ref: str):
    """(path, display name, error) for a file the user may hand over: a chat
    attachment, a download the chat delivered, or (Developers) a server path
    — the same owner-scoped rules read_file applies."""
    raw = str(ref or "").strip().strip('"')
    if not raw:
        return None, None, "empty file reference"
    uid = _user_context()["user_id"]
    try:
        import file_tools
        hit_path, name = file_tools.resolve_api_files_ref(raw, uid)
        if hit_path:
            return hit_path, _display_name(hit_path, name), None
        up = file_tools.resolve_upload(uid, raw)
        if up:
            return up[0], _display_name(up[0], up[1]), None
    except Exception as e:
        logger.debug(f"workflow files: attachment lookup for {raw!r}: {e}")
    try:
        from document_tools import _resolve_read_path
        path, err = _resolve_read_path(raw)
    except Exception as e:
        return None, None, f"could not resolve {raw}: {e}"
    if err:
        return None, None, err
    if not os.path.isfile(path):
        return None, None, f"{raw} is not a file"
    return path, _display_name(path), None


async def _upload(wid: int, path: str, name: str):
    """(stored name, error): stream one file into the workflow's Waiting folder
    without starting a run (the caller decides)."""
    try:
        headers = _headers()
    except Exception as e:
        return None, f"could not prove who you are to the platform: {e}"
    headers.update({"X-File-Name": quote(name), "Content-Type": "application/octet-stream",
                    "Content-Length": str(os.path.getsize(path))})

    async def chunks():
        with open(path, "rb") as fh:
            while True:
                block = fh.read(1024 * 1024)
                if not block:
                    break
                yield block
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=900.0, write=900.0)) as client:
            r = await client.post(f"{get_base_url()}/api/workflow-files/{wid}/upload",
                                  params={"run": "0", "via": "the_agent"}, content=chunks(),
                                  headers=headers)
    except Exception as e:
        return None, f"upload failed: {e}"
    try:
        data = r.json()
    except Exception:
        data = {}
    if r.status_code == 200 and data.get("stored"):
        return data["stored"][0]["name"], None
    if data.get("errors"):
        return None, "; ".join(data["errors"])
    return None, _err(r.status_code, data)


async def _offer(wid: int, folder: str, name: str):
    """(markdown link, error): download one file through the platform and
    stage it as this user's private chat download."""
    uid = _user_context()["user_id"]
    if not uid:
        return None, "no signed-in user to hand the file to"
    import file_tools
    safe = file_tools._NAME_RE.sub("_", os.path.basename(name))[:150] or "file"
    fid = str(uuid.uuid4())
    dst = os.path.join(file_tools.downloads_dir(uid), f"{fid}__{safe}")
    cap = file_tools.MAX_OFFER_MB * 1024 * 1024
    try:
        headers = _headers()
        size = 0
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=900.0)) as client:
            async with client.stream("GET", f"{get_base_url()}/api/workflow-files/{wid}/download",
                                     params={"folder": folder, "name": name}, headers=headers) as r:
                if r.status_code != 200:
                    body = (await r.aread()).decode("utf-8", "replace")[:300]
                    return None, f"{name}: could not download (HTTP {r.status_code}) {body}"
                with open(dst, "wb") as fh:
                    async for block in r.aiter_bytes():
                        size += len(block)
                        if size > cap:
                            break
                        fh.write(block)
        if size > cap:
            os.remove(dst)
            return None, (f"{name} is over the {file_tools.MAX_OFFER_MB} MB chat hand-off limit; "
                          f"download it from {_FILES_PAGE}")
    except Exception as e:
        try:
            os.remove(dst)
        except OSError:
            pass
        return None, f"{name}: download failed: {e}"
    return f"[⤓ {safe} ({file_tools._fmt_size(size)})](/api/files/{fid})", None


def _links_block(links: list) -> str:
    if not links:
        return ""
    return ("\n\nFiles — include these links VERBATIM in your reply (they are the user's "
            "downloads):\n" + "\n".join(links))


def _run_lines(rec: dict) -> list:
    lines = [f"Status: {rec.get('status')}"]
    taken = rec.get("consumed") or []
    lines.append("Took from Waiting: " + (", ".join(
        c["name"] + (f" (dropped by {c['uploaded_by']})" if c.get("uploaded_by") else "") for c in taken)
        or "nothing"))
    outs = rec.get("outputs") or []
    lines.append("Results (new or updated): " + (", ".join(o["name"] for o in outs) or "none"))
    if rec.get("failed"):
        lines.append("Failed (set aside in Failed; retry_workflow_files once the cause is fixed): "
                     + ", ".join(rec["failed"]))
    if rec.get("waiting_now"):
        lines.append(f"Still waiting: {rec['waiting_now']} file(s)")
    for sp in rec.get("sharepoint") or []:
        if not sp.get("ok"):
            lines.append(f"SharePoint copy of {sp['name']} FAILED: {sp.get('error')}")
    return lines


async def _run_and_report(wid: int, name: str, wait_seconds) -> dict:
    """Start a run (the user hears from this chat, not My Work), wait, and hand
    back its outcome with the results as download links."""
    status, data = await _call("POST", f"/api/workflow-files/{wid}/run",
                               body={"notify": False, "via": "the_agent"})
    if status != 200:
        return _text(f"The run did not start: {_err(status, data)}. Do NOT tell the user it ran.",
                     is_error=True)
    if data.get("queued"):
        return _text(f"A run of '{name}' is already going. The next run starts when it finishes and "
                     f"processes the waiting files; the user gets the results in My Work then. "
                     f"Say exactly that — no run was started by this call.")
    eid = data.get("execution_id")
    try:
        wait = float(wait_seconds or RUN_WAIT_SECONDS)
    except (TypeError, ValueError):
        wait = RUN_WAIT_SECONDS
    deadline = time.monotonic() + max(5.0, wait)
    rec, run_status, failures = None, "running", 0
    while time.monotonic() < deadline:
        await asyncio.sleep(4)
        st, d = await _call("GET", f"/api/workflow-files/{wid}/runs/{eid}")
        if st != 200:
            failures += 1
            if failures >= 5:
                break
            continue
        failures = 0
        run_status = str(d.get("run_status") or "").lower()
        if d.get("finished") and d.get("run"):
            rec = d["run"]
            break
    if rec is None:
        await _call("POST", f"/api/workflow-files/{wid}/runs/{eid}/notify")
        why = ("it is waiting for a Human Approval (My Approvals)" if run_status == "paused"
               else "it was still running when the wait ended")
        return _text(f"Run {eid} of '{name}' started; {why}. It keeps going, and the user gets the "
                     f"results in My Work when it finishes (list_workflow_files shows progress). "
                     f"Do not say it finished.")
    links, problems = [], []
    for o in rec.get("outputs") or []:
        link, err = await _offer(wid, "outbox", o["name"])
        if link:
            links.append(link)
        else:
            problems.append(err)
    text = f"Run {eid} of '{name}' finished.\n" + "\n".join(_run_lines(rec))
    if problems:
        text += "\nNot handed over: " + "; ".join(problems)
    if not _authoring_allowed():
        text += ("\nThis is the run's outcome as the platform recorded it; step-by-step run "
                 "reports are for Developers (do not request one).")
    return _text(text + _links_block(links))


# --------------------------------------------------------------------- tools
@tool(
    "list_workflow_files",
    "Workflow FILE DROPS: workflows that take files in and put files out have managed folders — "
    "Waiting (new files), Results (what the workflow produced), Failed (files it set aside) and "
    "Processed. Without `workflow`: the workflows whose files this user can use, with counts and "
    "the last run. With `workflow`: every file in each folder, who dropped what, recent runs "
    "(what each took and produced) and the email address that feeds it, if any.",
    {"type": "object",
     "properties": {"workflow": {"type": "string", "description": "Workflow name or id (optional)"}},
     "additionalProperties": False},
)
async def list_workflow_files(args: dict[str, Any]) -> dict[str, Any]:
    ref = str(args.get("workflow") or "").strip()
    if not ref:
        status, data = await _call("GET", "/api/workflow-files")
        if status != 200:
            return _text(_err(status, data), is_error=True)
        rows = data.get("workflows") or []
        if not rows:
            return _text("No workflow's files are available to this user"
                         + (". Set one up with setup_workflow_files." if data.get("can_manage")
                            else ": none is shared with them yet."))
        out = []
        for w in rows:
            c = w.get("counts") or {}
            last = w.get("last_run") or {}
            out.append(f"- {w['name']} (id {w['workflow_id']}): Waiting {c.get('inbox', 0)} · Results "
                       f"{c.get('outbox', 0)} · Failed {c.get('failed', 0)}"
                       + (f" · last run {last.get('status')} {last.get('finished_at') or last.get('started_at') or ''}"
                          if last else "")
                       + (" · runs as soon as files arrive" if w.get("run_on_upload") else ""))
        return _text("Workflows with file drops this user can use:\n" + "\n".join(out)
                     + f"\nPeople can also use {_FILES_PAGE}.")
    wid, name, err = await _resolve(ref, include_unset=_authoring_allowed())
    if err:
        return _text(err, is_error=True)
    status, d = await _call("GET", f"/api/workflow-files/{wid}")
    if status != 200:
        return _text(_err(status, d), is_error=True)
    if not d.get("set_up"):
        vars_ = ", ".join(f"{v['name']}={v['value'] or '(empty)'}" for v in d.get("variables") or [])
        return _text(f"'{name}' (id {wid}) does not have file drops set up. Its variables: {vars_ or 'none'}. "
                     "setup_workflow_files points its folder variables at managed folders.")
    c = d.get("counts") or {}
    lines = [f"'{name}' (id {wid}) — Waiting {c.get('inbox', 0)}, Results {c.get('outbox', 0)}, "
             f"Failed {c.get('failed', 0)}, Processed {c.get('processed', 0)}"
             + (" — a run is going now" if d.get("running") else "")]
    s = d.get("settings") or {}
    if s.get("run_on_upload"):
        lines.append("A run starts as soon as files arrive.")
    for a in d.get("email_addresses") or []:
        lines.append(f"Email attachments to {a['address']} land in Waiting"
                     + ("" if a.get("active") else " (address inactive)") + ".")
    for folder, label in (("inbox", "Waiting"), ("outbox", "Results"), ("failed", "Failed"),
                          ("processed", "Processed")):
        files = (d.get("files") or {}).get(folder) or []
        if files:
            lines.append(f"{label}:")
            lines.extend(f"  - {f['name']} ({f['size']} bytes, {f['modified']}"
                         + (f", dropped by {f['uploaded_by']}" if f.get("uploaded_by") else "") + ")"
                         for f in files)
    runs = d.get("runs") or []
    if runs:
        lines.append("Recent runs (newest first):")
        for r in runs[:5]:
            lines.append(f"  - {r.get('execution_id')} {r.get('status')} {r.get('finished_at') or r.get('started_at')}"
                         + (f" by {r['started_by']}" if r.get("started_by") else "")
                         + f": took {len(r.get('consumed') or [])}, results "
                         + (", ".join(o["name"] for o in r.get("outputs") or []) or "none")
                         + (f", failed {', '.join(r['failed'])}" if r.get("failed") else ""))
    roles = d.get("roles") or {}
    off = [f"{role} ({v.get('variable')} now {v.get('value') or 'empty'})" for role, v in roles.items()
           if v.get("variable") and not v.get("managed")]
    if off:
        lines.append("Variables NOT pointing at the managed folders any more: " + "; ".join(off)
                     + ". setup_workflow_files points them back.")
    lines.append("get_workflow_files hands any of these files over as download links.")
    return _text("\n".join(lines))


@tool(
    "setup_workflow_files",
    "Set up FILE DROPS for a visual workflow that reads files from a folder (Developer): the "
    "platform creates its managed folders (Waiting, Processed, Failed, Results) and points the "
    "workflow variables you name at them — saved like a Designer edit. Use it whenever you build "
    "or change a workflow that takes files in or puts files out, instead of asking anyone for "
    "server paths. An output variable that names a FILE (a workbook the workflow appends to) "
    "keeps its file name inside Results. Safe to repeat (it points the variables back).",
    {"type": "object",
     "properties": {
         "workflow": {"type": "string", "description": "Workflow name or id"},
         "inbox_variable": {"type": "string",
                            "description": "Variable the workflow reads new files from (e.g. inputFolder)"},
         "processed_variable": {"type": "string", "description": "Variable for finished files (optional)"},
         "failed_variable": {"type": "string", "description": "Variable for files it sets aside (optional)"},
         "outbox_variable": {"type": "string",
                             "description": "Variable for its results: a folder or an output file (optional)"},
         "run_on_upload": {"type": "boolean", "description": "Start a run as soon as files arrive"},
     },
     "required": ["workflow", "inbox_variable"], "additionalProperties": False},
)
async def setup_workflow_files(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_MANAGE_DENIED, is_error=True)
    wid, name, err = await _resolve(args.get("workflow"), include_unset=True)
    if err:
        return _text(err, is_error=True)
    body: dict[str, Any] = {"variables": {
        "inbox": args.get("inbox_variable") or "", "processed": args.get("processed_variable") or "",
        "failed": args.get("failed_variable") or "", "outbox": args.get("outbox_variable") or ""}}
    if "run_on_upload" in args:
        body["run_on_upload"] = bool(args.get("run_on_upload"))
    status, d = await _call("POST", f"/api/workflow-files/{wid}/setup", body=body)
    if status != 200:
        return _text(f"File drops were NOT set up: {_err(status, d)}", is_error=True)
    changes = "; ".join(f"{v}: {c.get('from') or '(empty)'} -> {c.get('to')}"
                        for v, c in (d.get("changes") or {}).items())
    s = d.get("settings") or {}
    return _text(
        f"File drops are set up for '{name}' (id {wid}). Variables now pointing at its managed folders: "
        f"{changes}. Runs start as soon as files arrive: {'yes' if s.get('run_on_upload') else 'no'}.\n"
        f"Tell the user, in plain words: they (and anyone they share it with) drop files on "
        f"{_FILES_PAGE} or attach them here and ask you to run them; results come back as download "
        f"links. Do not show server folder paths unless they ask where the files live. If the "
        f"workflow is open in the Designer, they should reload it there before saving it again.")


@tool(
    "add_workflow_files",
    "Put files into a workflow's Waiting folder (file drops) without running it: chat attachments "
    "(their paths from the attachment note), files you delivered earlier (/api/files links) or, "
    "for Developers, server paths. The next run (scheduled, or one you start with "
    "run_workflow_on_files) picks them up; a workflow set to run on arrival starts one now.",
    {"type": "object",
     "properties": {
         "workflow": {"type": "string", "description": "Workflow name or id"},
         "files": {"type": "array", "items": {"type": "string"}},
     },
     "required": ["workflow", "files"], "additionalProperties": False},
)
async def add_workflow_files(args: dict[str, Any]) -> dict[str, Any]:
    wid, name, err = await _resolve(args.get("workflow"))
    if err:
        return _text(err, is_error=True)
    stored, problems = await _put_files(wid, args.get("files") or [])
    if not stored:
        return _text("Nothing was added: " + "; ".join(problems or ["no files given"]), is_error=True)
    text = f"Added to Waiting for '{name}': " + ", ".join(stored) + "."
    if problems:
        text += " Not added: " + "; ".join(problems) + "."
    status, d = await _call("GET", f"/api/workflow-files/{wid}")
    if status == 200 and (d.get("settings") or {}).get("run_on_upload"):
        st, r = await _call("POST", f"/api/workflow-files/{wid}/run", body={"via": "the_agent"})
        if st == 200 and r.get("queued"):
            text += " A run is already going; the next one picks these up. Results reach the user in My Work."
        elif st == 200:
            text += " This workflow runs on arrival: a run started; the results reach the user in My Work."
        else:
            text += f" The automatic run did not start: {_err(st, r)}."
    else:
        text += " They wait for the next run (run_workflow_on_files starts one now)."
    return _text(text)


async def _put_files(wid: int, refs) -> tuple:
    stored, problems = [], []
    for ref in refs:
        path, display, err = _resolve_file(ref)
        if err:
            problems.append(f"{ref}: {err}")
            continue
        got, uerr = await _upload(wid, path, display)
        if got:
            stored.append(got)
        else:
            problems.append(f"{display}: {uerr}")
    return stored, problems


@tool(
    "run_workflow_on_files",
    "Run a file-drops workflow on its Waiting files and hand the results back IN THIS CHAT as "
    "download links — optionally putting chat attachments (or /api/files links, or Developer "
    "server paths) into Waiting first. This is how 'run these invoices through the CI workflow' "
    "works. The run is real (files move, rows are written). Waits up to wait_seconds; a run still "
    "going then keeps going and the user gets the results in My Work.",
    {"type": "object",
     "properties": {
         "workflow": {"type": "string", "description": "Workflow name or id"},
         "files": {"type": "array", "items": {"type": "string"},
                   "description": "Files to add to Waiting first (optional)"},
         "wait_seconds": {"type": "integer"},
     },
     "required": ["workflow"], "additionalProperties": False},
)
async def run_workflow_on_files(args: dict[str, Any]) -> dict[str, Any]:
    wid, name, err = await _resolve(args.get("workflow"))
    if err:
        return _text(err, is_error=True)
    note = ""
    if args.get("files"):
        stored, problems = await _put_files(wid, args.get("files") or [])
        if not stored:
            return _text("Nothing was run: none of the files could be added: " + "; ".join(problems),
                         is_error=True)
        note = "Added to Waiting: " + ", ".join(stored) + ". "
        if problems:
            note += "Not added: " + "; ".join(problems) + ". "
    out = await _run_and_report(wid, name, args.get("wait_seconds"))
    if note:
        out["content"][0]["text"] = note + out["content"][0]["text"]
    return out


@tool(
    "get_workflow_files",
    "Hand a file-drops workflow's files to the user as download links in the chat: by default "
    "everything in Results (newest first); or the files named; or one run's results "
    "(execution_id); `folder` picks Results (outbox), Failed, Processed or Waiting (inbox).",
    {"type": "object",
     "properties": {
         "workflow": {"type": "string", "description": "Workflow name or id"},
         "folder": {"type": "string", "enum": ["outbox", "failed", "processed", "inbox"]},
         "names": {"type": "array", "items": {"type": "string"}},
         "execution_id": {"type": "string", "description": "Hand over this run's results"},
     },
     "required": ["workflow"], "additionalProperties": False},
)
async def get_workflow_files(args: dict[str, Any]) -> dict[str, Any]:
    wid, name, err = await _resolve(args.get("workflow"))
    if err:
        return _text(err, is_error=True)
    folder = args.get("folder") or "outbox"
    if args.get("execution_id"):
        st, d = await _call("GET", f"/api/workflow-files/{wid}/runs/{args['execution_id']}")
        if st != 200 or not d.get("run"):
            return _text(f"That run's record was not found: {_err(st, d)}", is_error=True)
        names, folder = [o["name"] for o in d["run"].get("outputs") or []], "outbox"
    elif args.get("names"):
        names = [str(n) for n in args["names"]]
    else:
        st, d = await _call("GET", f"/api/workflow-files/{wid}")
        if st != 200:
            return _text(_err(st, d), is_error=True)
        names = [f["name"] for f in (d.get("files") or {}).get(folder) or []]
    if not names:
        return _text(f"There are no files to hand over ({folder} is empty for that selection).")
    links, problems = [], []
    for n in names:
        link, e = await _offer(wid, folder, n)
        if link:
            links.append(link)
        else:
            problems.append(e)
    text = f"{len(links)} file(s) from '{name}'."
    if problems:
        text += " Not handed over: " + "; ".join(problems)
    return _text(text + _links_block(links), is_error=not links)


@tool(
    "retry_workflow_files",
    "Move a file-drops workflow's FAILED files back to Waiting (names, or all=true) so a run "
    "processes them again — after the cause is fixed. run=true also runs it now and hands the "
    "results back here.",
    {"type": "object",
     "properties": {
         "workflow": {"type": "string", "description": "Workflow name or id"},
         "names": {"type": "array", "items": {"type": "string"}},
         "all": {"type": "boolean"},
         "run": {"type": "boolean"},
         "wait_seconds": {"type": "integer"},
     },
     "required": ["workflow"], "additionalProperties": False},
)
async def retry_workflow_files(args: dict[str, Any]) -> dict[str, Any]:
    wid, name, err = await _resolve(args.get("workflow"))
    if err:
        return _text(err, is_error=True)
    body = {"all": True} if args.get("all") else {"names": [str(n) for n in args.get("names") or []]}
    st, d = await _call("POST", f"/api/workflow-files/{wid}/retry", body=body)
    if st != 200:
        return _text(f"Nothing was moved back: {_err(st, d)}"
                     + (" — " + "; ".join(d.get("errors") or []) if d.get("errors") else ""), is_error=True)
    moved = ", ".join(m["to"] for m in d.get("moved") or [])
    text = f"Back in Waiting for '{name}': {moved}."
    if d.get("errors"):
        text += " Not moved: " + "; ".join(d["errors"]) + "."
    if not args.get("run"):
        return _text(text + " They wait for the next run.")
    out = await _run_and_report(wid, name, args.get("wait_seconds"))
    out["content"][0]["text"] = text + " " + out["content"][0]["text"]
    return out


@tool(
    "share_workflow_files",
    "Choose who else may use a workflow's file drops (Developer): people (user name, email or id) "
    "and groups (name or id) it is shared with can drop files, collect results, retry and run it — "
    "without Designer access. Developers and Admins always can.",
    {"type": "object",
     "properties": {
         "workflow": {"type": "string", "description": "Workflow name or id"},
         "add_users": {"type": "array", "items": {"type": "string"}},
         "remove_users": {"type": "array", "items": {"type": "string"}},
         "add_groups": {"type": "array", "items": {"type": "string"}},
         "remove_groups": {"type": "array", "items": {"type": "string"}},
     },
     "required": ["workflow"], "additionalProperties": False},
)
async def share_workflow_files(args: dict[str, Any]) -> dict[str, Any]:
    if not _authoring_allowed():
        return _text(_MANAGE_DENIED, is_error=True)
    wid, name, err = await _resolve(args.get("workflow"))
    if err:
        return _text(err, is_error=True)
    body = {k: [str(x) for x in args[k]] for k in ("add_users", "remove_users", "add_groups", "remove_groups")
            if args.get(k)}
    if not body:
        return _text("Say whom to add or remove.", is_error=True)
    st, d = await _call("PUT", f"/api/workflow-files/{wid}/access", body=body)
    if st != 200:
        return _text(f"Sharing was NOT changed: {_err(st, d)}", is_error=True)
    sw = d.get("shared_with") or {}
    who = [u.get("name") or u.get("user_name") for u in sw.get("users") or []] + \
          [f"group {g.get('name')}" for g in sw.get("groups") or []]
    return _text(f"'{name}' files are now shared with: {', '.join(who) or 'nobody (Developers and Admins only)'}."
                 f" They use {_FILES_PAGE}, or ask you here.")


WORKFLOW_FILE_TOOLS = [list_workflow_files, setup_workflow_files, add_workflow_files,
                       run_workflow_on_files, get_workflow_files, retry_workflow_files,
                       share_workflow_files]
