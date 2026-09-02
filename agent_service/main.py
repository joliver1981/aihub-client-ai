"""
The Agent service — FastAPI app.

Serves the next-gen chat UI and streams brain turns over SSE. Users arrive via
the main app's /the-agent token redirect (same shared_auth JWT pattern as
Command Center); every API call re-verifies the token, and the resulting user
context becomes the session envelope the tools read.

Run (dev):  conda activate aihub-agent && python main.py
Health:     GET /health
"""

import asyncio
import json
import os
import re
import sys

import agent_config  # noqa: F401  (must be first: APP_ROOT, .env, secure_config)
from agent_config import (
    HOST, PORT, DEBUG, AGENT_MODEL, AGENT_ALLOW_ALL_USERS, logger, summary,
    defer_to_chat_enabled, CHAT_BUSY_WAIT_SECONDS,
)

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import (FileResponse, StreamingResponse, JSONResponse,
                               Response)
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

import shared_auth  # from APP_ROOT (agent_config put it on sys.path)
from brain import (run_turn, is_inflight, mark_inflight, clear_inflight,
                   bump_session_version, session_version)
import workitem_store
import views_store
import readthrough


@asynccontextmanager
async def lifespan(app):
    workitem_store.init()
    views_store.init()
    import usage_store
    usage_store.init()
    import email_store
    email_store.init()
    import chat_history
    chat_history.init()
    poller_task = None
    import email_poller
    if email_poller.enabled():
        poller_task = asyncio.get_event_loop().create_task(
            email_poller.run_forever())
    else:
        logger.info("agent email poller disabled (AGENT_EMAIL_ENABLED != true)")
    # Hand-back -> conversation bridge (2026-08-23): DB-backed watches over
    # portal runs the model handed off; the supervisor wakes the originating
    # conversation when a run finishes (portal_watch.py).
    watch_task = None
    import portal_watch
    portal_watch.init()
    if portal_watch.ENABLED:
        watch_task = asyncio.get_event_loop().create_task(portal_watch.run_forever())
    else:
        logger.info("portal watch disabled (AGENT_PORTAL_WATCH != true)")
    logger.info(f"The Agent starting: {json.dumps(summary())}")
    yield
    if poller_task:
        poller_task.cancel()
    if watch_task:
        watch_task.cancel()
    logger.info("The Agent stopped")


app = FastAPI(title="AI Hub — The Agent", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"], allow_headers=["*"],
)

_static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


def _verify_request(request: Request) -> dict:
    """
    Verify the shared_auth JWT (same audience/secret as Command Center's token
    redirect) and return the user context — the session envelope's principal.
    """
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else request.query_params.get("token", "")
    if not token:
        # Keep "token" phrasing bare of advice: the UI's silent re-auth handles
        # the normal case, and "reopen from AI Hub" became circular once AI
        # Hub's front door started redirecting INTO The Agent.
        raise HTTPException(401, "Missing token — no access token presented.")
    claims, err = shared_auth.verify_token(token, shared_auth.AUD_CC)
    if err:
        raise HTTPException(401, f"Invalid or expired token ({err}).")
    role = int(claims.get("role") or 0)
    if role < 2 and not AGENT_ALLOW_ALL_USERS:
        raise HTTPException(403, "The Agent preview is Developer+ only on this install.")
    return {
        "user_id": claims.get("sub"),
        "role": role,
        "username": claims.get("username") or "",
        "name": claims.get("name") or "",
        "tenant_id": claims.get("tenant_id"),
    }


def _turn_envelope(user: dict, body: dict) -> str:
    """Stamp the user's BROWSER timezone (sent by the UI as body.timezone, the
    IANA zone from Intl — exactly the Command Center contract) onto the
    envelope the tools read, and return the one-line [Context: now … (zone)]
    the model needs for any time arithmetic. Invalid/missing zone -> the
    server-side default order (AGENT_DEFAULT_TZ, then the server's zone)."""
    import work_tools
    tz = str((body or {}).get("timezone") or "").strip()[:64]
    if tz:
        canon = work_tools._zone_canonical(tz)
        if canon:
            user["browser_timezone"] = canon
        else:
            logger.info(f"ignoring unusable browser timezone {tz!r}")
    zone, _src = work_tools.default_zone_label(user)
    return work_tools.now_line(zone)


@app.get("/")
async def index():
    index_path = os.path.join(_static_dir, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return {"message": "The Agent service running. UI not found."}


@app.get("/health")
async def health():
    return {"status": "ok", **summary()}


@app.get("/api/me")
async def me(request: Request):
    user = _verify_request(request)
    from agent_config import (get_effective_model, get_turn_cap, APP_VERSION,
                              AGENT_MODEL_ROLE1)
    return {"user": {k: user[k] for k in ("username", "name", "role")},
            "model": get_effective_model(),
            "model_default": AGENT_MODEL,
            "model_role1": get_effective_model(role=1),
            "model_role1_default": AGENT_MODEL_ROLE1,
            "turns_per_day": get_turn_cap(),
            "app_version": APP_VERSION,
            # Deep links into the legacy app (Playbooks/Platform views) target
            # the same hostname the browser used, on the main app's port.
            "main_port": int(os.getenv("HOST_PORT", "5001")),
            # the zone the service falls back to when the browser sends none
            "server_timezone": __import__("work_tools").server_zone_label()}


def _service_key_ok(request: Request) -> bool:
    """X-API-Key auth for machine callers (the scheduler engine)."""
    import hmac as _hmac
    key = request.headers.get("X-API-Key", "") or ""
    if not key:
        return False
    tenant = os.getenv("API_KEY", "")
    from agent_config import get_internal_api_key
    return ((bool(tenant) and _hmac.compare_digest(key, tenant))
            or _hmac.compare_digest(key, get_internal_api_key()))


def _resume_target(want_sid, user_id):
    """Decide whether a deferred run may RESUME the chat it was scheduled from.
    Returns (session_id | None, reason_when_none). Fails closed on every doubt
    so the fallback is always the old fresh-session + My Work FYI behavior."""
    if not want_sid:
        return None, "no session_id on the job"
    if not defer_to_chat_enabled():
        return None, "AGENT_DEFER_TO_CHAT is off"
    safe = "".join(ch for ch in str(want_sid) if ch.isalnum() or ch == "-")
    if safe != want_sid:
        return None, "malformed session id"
    import chat_history
    if not chat_history.owns_session(int(user_id or 0), want_sid):
        return None, "not a conversation owned by this user"
    if is_inflight(want_sid):
        return None, "conversation is busy (a turn is in flight)"
    return want_sid, ""


@app.post("/api/run")
async def headless_run(request: Request):
    """
    Headless brain turn for scheduled agent_session jobs (and future webhook/
    email triggers). Runs AS the principal stored on the trigger — the user
    who created the schedule — and reports the outcome into that user's
    My Work queue as an acknowledge (FYI) item. Auth: platform service key.

    Deferred results -> chat (2026-08-22, AGENT_DEFER_TO_CHAT, Level 1): when
    the job carries the `session_id` of the conversation it was scheduled
    from, the turn RESUMES that SDK session, so the result becomes the next
    turn of that conversation (history replay renders it; no push channel)
    and the FYI deep-links to it. Guarded: resume only a conversation this
    user owns that is not in flight — anything else falls back to exactly the
    old fresh-session behavior. A resume that fails before doing any work
    (missing/corrupt transcript) also falls back, so the task still runs.
    """
    if not _service_key_ok(request):
        raise HTTPException(401, "service key required")
    body = await request.json()
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt is required")
    user_ctx = {
        "user_id": int(body.get("user_id") or 0),
        "role": int(body.get("role") or 2),
        "username": str(body.get("username") or "scheduler"),
        "name": str(body.get("username") or "scheduler"),
        "mode": "headless",   # tools route human-needed pauses to My Work
    }
    job_name = str(body.get("job_name") or "Scheduled agent task")
    # the zone the user thinks in (stored on the job by schedule_agent_task as
    # user_timezone; forwarded by the JSS) — times in this run are stated in it
    # and any schedule the run creates defaults to it
    ctx_line = _turn_envelope(user_ctx, body)
    import work_tools as _wt
    user_zone = _wt.default_zone_label(user_ctx)[0]
    want_sid = str(body.get("session_id") or "").strip() or None
    resume_sid, skip_reason = _resume_target(want_sid, user_ctx["user_id"])
    logger.info(f"headless run start job={job_name!r} as user "
                f"{user_ctx['user_id']}/{user_ctx['username']} "
                f"resume={resume_sid or '-'}"
                + (f" (no resume: {skip_reason})" if want_sid and not resume_sid else ""))

    async def _drive(sid, model_prompt):
        texts, tools_run, final = [], [], {}
        async for ev in run_turn(model_prompt, sid, user_ctx, tool_scope="full"):
            if ev.get("type") == "text":
                texts.append(ev["text"])
            elif ev.get("type") == "tool":
                tools_run.append(ev.get("name", "?").replace("mcp__aihub__", ""))
            elif ev.get("type") in ("result", "error"):
                final = ev
        return texts, tools_run, final

    resumed = False
    if resume_sid:
        import chat_history
        import datetime as _dt
        fired_at = _wt.fmt_local(_dt.datetime.utcnow(), user_zone)   # user's zone
        # claim the conversation BEFORE the first await (no race with /api/chat)
        mark_inflight(resume_sid)
        try:
            user_ctx["chat_session_id"] = resume_sid   # tools: chaining keeps the thread
            texts, tools_run, final = await _drive(
                resume_sid, ctx_line + "\n\n"
                + chat_history.build_deferred_prompt(job_name, fired_at, prompt))
        finally:
            clear_inflight(resume_sid)
        resumed = True
        if final.get("type") == "error" and not texts and not tools_run:
            # the resume itself failed before any work: run it the old way so
            # the task still happens (never lose a scheduled run to a transcript)
            logger.warning(f"headless resume of {resume_sid} failed before any work "
                           f"({final.get('error')}) — falling back to a fresh session")
            resumed = False
            user_ctx.pop("chat_session_id", None)
            texts, tools_run, final = await _drive(None, ctx_line + "\n\n" + prompt)
        else:
            chat_history.touch(user_ctx["user_id"], resume_sid, "")  # float to the top of history
            bump_session_version(resume_sid)      # live UI: the conversation changed
    else:
        texts, tools_run, final = await _drive(None, ctx_line + "\n\n" + prompt)
    ok = bool(final.get("ok"))
    summary_text = "\n\n".join(texts).strip()

    item = workitem_store.create_item(
        "acknowledge",
        f"{'✓' if ok else '⚠'} {job_name}",
        summary=(summary_text[:2000] or "(the run produced no text)"),
        payload={"kind": "headless_run", "ok": ok,
                 "subtype": final.get("subtype") or final.get("error"),
                 "session_id": final.get("session_id"),
                 # deep-link: the conversation this result was appended to
                 "chat_session_id": resume_sid if resumed else None,
                 "tools_used": tools_run[:30], "prompt": prompt[:500]},
        addressed_user=user_ctx["user_id"] or None,
        from_kind="agent_headless", from_ref=job_name,
        created_by="scheduler")
    logger.info(f"headless run done job={job_name!r} ok={ok} "
                f"item={item['work_item_id']} resumed_chat={resumed}")
    return {"ok": ok, "subtype": final.get("subtype") or final.get("error"),
            "session_id": final.get("session_id"),
            "work_item_id": item["work_item_id"],
            "resumed_chat": resumed,
            "chat_session_id": resume_sid if resumed else None}


@app.post("/api/work/internal/raise")
async def internal_raise_work(request: Request):
    """Service-key seam for OTHER services (the main app's portal routes) to put
    an item in a user's My Work — scheduled portal outcomes, 2FA take-over
    requests (P2 items 1+2, 2026-08-22). Optional `files` (server paths under
    APP_ROOT) are staged into the user's private downloads and appended as
    working links, so the item delivers the actual file."""
    if not _service_key_ok(request):
        raise HTTPException(401, "service key required")
    body = await request.json()
    uid = int(body.get("user_id") or 0)
    title = str(body.get("title") or "").strip()
    if not uid or not title:
        raise HTTPException(400, "user_id and title are required")
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
    if payload.get("kind") in ("skill_promotion", "view_promotion", "agent_email_reply"):
        raise HTTPException(400, "reserved payload kind")
    summary = str(body.get("summary") or "")
    import file_tools
    links = []
    for p in (body.get("files") or [])[:20]:
        ok, link, _path = file_tools.stage_offer(uid, str(p))
        if ok:
            links.append(link)
    if links:
        summary += "\n\nDownloads:\n" + "\n".join(f"- {ln}" for ln in links)
    try:
        item = workitem_store.create_item(
            str(body.get("verb") or "acknowledge"), title[:160],
            summary=summary[:6000], payload=payload, addressed_user=uid,
            from_kind=str(body.get("from_kind") or "platform"),
            from_ref=str(body.get("from_ref") or payload.get("kind") or "portal"),
            priority=int(body.get("priority") or 0),
            created_by=str(body.get("created_by") or "platform"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    logger.info(f"internal work item raised for user {uid}: {title!r} "
                f"({item['work_item_id']}, {len(links)} file link(s))")
    return {"work_item_id": item["work_item_id"], "links": len(links)}


@app.post("/api/uploads")
async def upload_attachment(request: Request):
    """Chat attachment (P2 item 4, 2026-08-22): raw bytes in the body plus an
    X-File-Name header (URL-encoded) — no multipart dependency. Stored in the
    caller's private uploads area; the returned file_id is what /api/chat's
    `attachments` refers to."""
    user = _verify_request(request)
    from urllib.parse import unquote
    import file_tools
    name = unquote(request.headers.get("X-File-Name") or "").strip() or "file"
    data = await request.body()
    if not data:
        raise HTTPException(400, "empty upload")
    try:
        fid, _path, size = file_tools.save_upload(int(user["user_id"] or 0), name, data)
    except ValueError as e:
        raise HTTPException(413, str(e))
    return {"file_id": fid, "name": name, "size": size}


@app.get("/api/uploads")
async def list_attachments(request: Request):
    user = _verify_request(request)
    import file_tools
    return {"uploads": file_tools.list_uploads(int(user["user_id"] or 0))}


@app.post("/api/chat")
async def chat(request: Request):
    user = _verify_request(request)
    body = await request.json()
    message = str(body.get("message") or "").strip()
    session_id = body.get("session_id") or None
    attachments = [str(a) for a in (body.get("attachments") or []) if a]
    if not message and not attachments:
        raise HTTPException(400, "Empty message")
    if not message:
        message = "Please look at the attached file(s)."
    # Attachments ride into the turn as a model-facing line of server paths
    # (the FILES doctrine keeps them out of the user's view); the history
    # ledger keeps the user's own words.
    prompt = message
    if attachments:
        import file_tools
        block = file_tools.attachments_prompt_block(int(user["user_id"] or 0), attachments)
        if block:
            prompt = f"{block}\n\n{message}"
    # Current time + the user's zone ride in front of every turn (the UI sends
    # the browser's IANA zone as body.timezone); replay strips the line.
    prompt = _turn_envelope(user, body) + "\n\n" + prompt

    async def event_stream():
        final_sid = session_id
        try:
            if session_id and CHAT_BUSY_WAIT_SECONDS > 0 and is_inflight(session_id):
                # A deferred run is appending its result to THIS conversation:
                # wait (bounded) rather than race it onto the same transcript.
                yield "data: " + json.dumps({
                    "type": "status",
                    "text": "A scheduled task is adding its result to this "
                            "conversation — waiting for it to finish…"}) + "\n\n"
                waited = 0.0
                while is_inflight(session_id) and waited < CHAT_BUSY_WAIT_SECONDS:
                    await asyncio.sleep(1.0)
                    waited += 1.0
            async for event in run_turn(prompt, session_id, user):
                if event.get("type") == "result" and event.get("session_id"):
                    final_sid = event["session_id"]
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:  # belt-and-suspenders: never die mid-stream silently
            logger.error(f"/api/chat stream error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
        # Chat-history ledger (CC-parity): title = the conversation's FIRST
        # message (only the INSERT sets it; later turns just bump counters).
        import chat_history
        chat_history.touch(int(user["user_id"] or 0), final_sid or "", message)
        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# My Work API (A2)
# ---------------------------------------------------------------------------

def _agent_item_view(it: dict) -> dict:
    return {"source": "agent", "id": it["work_item_id"], "verb": it["verb"],
            "title": it["title"], "summary": it.get("summary") or "",
            "status": it["status"], "priority": it.get("priority") or 0,
            "requested_at": it.get("created_at"),
            "from": it.get("created_by") or "agent",
            "claimed_by": it.get("claimed_by"),
            "addressed_user": it.get("addressed_user"),
            "payload": it.get("payload") or {}}


def _parse_approval_data(raw):
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}


@app.get("/api/work/list")
async def work_list(request: Request):
    user = _verify_request(request)
    uid = int(user["user_id"] or 0)
    items = [_agent_item_view(i) for i in workitem_store.list_items(uid)]

    group_ids = readthrough.user_group_ids(uid)
    for row in readthrough.workflow_pending(uid):
        ad = _parse_approval_data(row.get("approval_data"))
        items.append({
            "source": "workflow", "id": row.get("request_id"),
            "verb": "approve_deny", "title": row.get("title") or "Approval",
            "summary": row.get("description") or "",
            "status": "open", "priority": int(row.get("priority") or 0),
            "requested_at": str(row.get("requested_at") or ""),
            "due_at": str(row.get("due_date") or "") or None,
            "from": ad.get("workflow_name") or "workflow",
            "payload": ad})
    for row in readthrough.automation_pending(uid, group_ids):
        ad = _parse_approval_data(row.get("approval_data"))
        is_review = ad.get("kind") == "review" or not ad.get("checkpoint_id")
        items.append({
            "source": "automation", "id": row.get("request_id"),
            "verb": "review" if is_review else "approve_deny",
            "title": row.get("title") or "Automation checkpoint",
            "summary": row.get("description") or "",
            "status": "open", "priority": int(row.get("priority") or 0),
            "requested_at": row.get("requested_at"),
            "from": ad.get("automation_name") or "automation",
            "payload": {"run_id": ad.get("run_id"),
                        "checkpoint_id": ad.get("checkpoint_id"),
                        "automation_id": ad.get("automation_id"),
                        "dry_run": ad.get("dry_run"),
                        "attachments": ad.get("attachments") or []}})
    for row in await readthrough.email_pending():
        items.append({
            "source": "email", "id": row.get("approval_id"),
            "verb": "edit_and_return",
            "title": f"Send: {row.get('subject') or '(no subject)'}",
            "summary": f"To: {', '.join(row.get('to_addresses') or [])}",
            "status": "open", "priority": 0,
            "requested_at": row.get("created_at"),
            "from": row.get("agent_name") or f"agent {row.get('agent_id')}",
            "payload": {"to": row.get("to_addresses") or [],
                        "subject": row.get("subject"),
                        "body": row.get("final_body") or row.get("draft_body") or "",
                        "agent": row.get("agent_name")}})

    items.sort(key=lambda i: (-(i.get("priority") or 0),
                              str(i.get("requested_at") or "")), reverse=False)
    counts = {}
    for i in items:
        counts[i["verb"]] = counts.get(i["verb"], 0) + 1
    return {"items": items, "counts": counts, "total": len(items)}


@app.post("/api/work/claim")
async def work_claim(request: Request):
    user = _verify_request(request)
    body = await request.json()
    item, err = workitem_store.claim(str(body.get("id")), int(user["user_id"]))
    if err:
        raise HTTPException(409, err)
    return {"item": _agent_item_view(item)}


@app.post("/api/work/release")
async def work_release(request: Request):
    user = _verify_request(request)
    body = await request.json()
    item, err = workitem_store.release(str(body.get("id")), int(user["user_id"]))
    if err:
        raise HTTPException(409, err)
    return {"item": _agent_item_view(item)}


@app.post("/api/work/respond")
async def work_respond(request: Request):
    """Close an agent-raised item with the human's response. Approving a
    skill-promotion item is what actually publishes the skill to tenant scope
    (James's Round-3 policy: tenant promotion ALWAYS requires this approval)."""
    user = _verify_request(request)
    body = await request.json()
    before = workitem_store.get_item(str(body.get("id")))
    payload = (before or {}).get("payload") or {}
    decision = str((body.get("response") or {}).get("decision") or "")
    # Gate BEFORE closing the item: a non-admin approval of a promotion item
    # must leave the item open for a real admin, not close-without-publishing.
    is_promotion = payload.get("kind") in ("skill_promotion", "view_promotion")
    if is_promotion and decision == "approved":
        if int(user.get("role") or 0) < 3:
            raise HTTPException(403, "Tenant promotion requires an admin.")
        # PUBLISH FIRST, close after (review finding, 2026-08-07): if the
        # publish fails, the item must stay open and retryable — never a
        # closed item whose audit trail says approved with nothing published.
        # Publishes are idempotent upserts, so a rare respond() failure after
        # a successful publish is safely re-approvable.
        try:
            if payload.get("kind") == "skill_promotion":
                import skills_mount
                skills_mount.write_skill("tenant", payload.get("name", ""),
                                         payload.get("description", ""),
                                         payload.get("content", ""))
                logger.info(f"skill '{payload.get('name')}' promoted to tenant "
                            f"by user {user['user_id']}")
            else:
                # The ONLY path that publishes a tenant view (views-v2-spec §3.2).
                views_store.publish_tenant(
                    payload.get("name", ""), payload.get("description", ""),
                    payload.get("tiles") or [],
                    int(payload.get("requested_by_user") or 0))
                logger.info(f"view '{payload.get('name')}' promoted to tenant "
                            f"by user {user['user_id']}")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"promotion publish failed (item left open): {e}")
            raise HTTPException(500, f"Publish failed — the item remains open "
                                     f"to retry: {e}")
    if (payload.get("kind") == "agent_email_reply"
            and decision in ("answered", "approved")):
        # SEND BEFORE CLOSE (v2 lesson): a failed send must leave the item
        # open and retryable — never a closed item whose audit says approved
        # with nothing sent. Only the address owner (it is THEIR from-
        # address) or an admin may approve.
        if (int(user.get("user_id") or 0) != int(payload.get("from_user") or -1)
                and int(user.get("role") or 0) < 3):
            raise HTTPException(403, "Only the address owner (or an admin) "
                                     "can approve this email.")
        import email_client
        import email_render
        final_body = str((body.get("response") or {}).get("text") or "").strip() \
            or str(payload.get("body") or "")
        # Render the APPROVED text, not the drafted text: the human may have
        # edited it, and what they approved is what must send — in both formats.
        # The payload carries the INTENT ("rich"), never a pre-rendered artifact,
        # so an edit can't leave stale HTML attached to fresh prose. Items queued
        # before this feature existed have no flag and stay plain, and the
        # install-wide kill switch applies to already-queued items too.
        rich = bool(payload.get("rich", False)) and email_render.html_enabled()
        # An embedded View is RE-RUN at approval time, not carried over from the
        # draft: the whole point of a dashboard is current numbers, and an
        # approval can sit for days. It runs as the DRAFTER's stored principal
        # (payload.view.as_user), because the approver may be an admin who
        # cannot see the drafter's private View — resolving as them would 404 a
        # view the drafter legitimately embedded.
        view_html = view_text = ""
        vref = payload.get("view") or None
        if vref:
            from work_tools import render_view_for_email
            view_html, view_text, verr, _vstatus = await render_view_for_email(
                str(vref.get("name") or ""), str(vref.get("scope") or ""),
                int(vref.get("group_id") or 0),
                vref.get("as_user") or {"user_id": payload.get("from_user"),
                                        "role": 2, "username": "approver"})
            if verr:
                logger.error(f"agent email view render failed (item left open): {verr}")
                raise HTTPException(502, f"The embedded View could not be "
                                         f"refreshed, so nothing was sent — the "
                                         f"item remains open to retry: {verr}")
        # Attachments (send_email's approval path, 2026-09-02): the draft
        # stores server paths, the bytes are read at SEND time. A missing file
        # fails closed with the item left open — never a mail with a silently
        # dropped attachment.
        atts = None
        if payload.get("attachments"):
            from work_tools import build_email_attachments
            atts, aerr = build_email_attachments(payload.get("attachments") or [])
            if aerr:
                raise HTTPException(502, f"Attachment problem — nothing was sent, "
                                         f"the item remains open: {aerr}")
        result = await email_client.send_reply(
            payload.get("to") or [], str(payload.get("subject") or ""),
            final_body + (("\n\n" + view_text) if view_text else ""),
            str(payload.get("from_address") or ""),
            f"{payload.get('from_address', '').split('@')[0]} via The Agent",
            html_body=email_render.render_email_with_view(
                final_body, view_html, title=str(payload.get("subject") or ""))
            if rich else None,
            attachments=atts)
        if not result.get("success"):
            logger.error(f"agent email send failed (item left open): {result}")
            raise HTTPException(502, f"Send failed — the item remains open to "
                                     f"retry: {result.get('error', result)}")
        logger.info(f"agent email sent: to={payload.get('to')} "
                    f"from={payload.get('from_address')} "
                    f"result={json.dumps(result)[:200]}")
    item, err = workitem_store.respond(str(body.get("id")),
                                       int(user["user_id"]),
                                       body.get("response") or {})
    if err:
        raise HTTPException(409, err)
    return {"item": _agent_item_view(item)}


# ---------------------------------------------------------------------------
# Skills admin API (A3)
# ---------------------------------------------------------------------------

@app.get("/api/skills")
async def skills_list(request: Request):
    user = _verify_request(request)
    import skills_mount
    uid = int(user["user_id"] or 0)
    gids = readthrough.user_group_ids(uid)
    return {"skills": skills_mount.list_skills(uid, gids)}


@app.get("/api/skills/read")
async def skills_read(request: Request):
    user = _verify_request(request)
    import skills_mount
    q = request.query_params
    content = skills_mount.read_skill(q.get("scope", ""), q.get("name", ""),
                                      user_id=int(user["user_id"] or 0),
                                      group_id=int(q.get("group_id") or 0))
    if not content:
        raise HTTPException(404, "skill not found")
    return {"content": content}


@app.post("/api/skills/delete")
async def skills_delete(request: Request):
    user = _verify_request(request)
    import skills_mount
    body = await request.json()
    scope = str(body.get("scope") or "")
    if scope in ("tenant", "product") and int(user.get("role") or 0) < 3:
        raise HTTPException(403, "Deleting tenant/product skills requires an admin.")
    ok = skills_mount.delete_skill(scope, str(body.get("name") or ""),
                                   user_id=int(user["user_id"] or 0),
                                   group_id=int(body.get("group_id") or 0))
    if not ok:
        raise HTTPException(404, "skill not found")
    return {"deleted": True}


@app.post("/api/work/decide")
async def work_decide(request: Request):
    """Act on a read-through item via the EXISTING platform endpoints."""
    user = _verify_request(request)
    body = await request.json()
    source = body.get("source")
    decision = str(body.get("decision") or "")
    comments = str(body.get("comments") or "")
    uid = int(user["user_id"] or 0)

    if source in ("workflow", "automation"):
        if decision not in ("approved", "rejected"):
            raise HTTPException(400, "decision must be approved|rejected")
        data, status = await readthrough.decide_generic(
            str(body.get("id")), decision, comments, uid,
            body.get("corrections"))
    elif source == "email":
        if decision not in ("approve", "reject"):
            raise HTTPException(400, "decision must be approve|reject")
        data, status = await readthrough.decide_email(
            int(body.get("id")), decision, body.get("final_body"), comments)
    else:
        raise HTTPException(400, f"unknown source '{source}'")

    if status >= 400:
        raise HTTPException(status, str(data.get("error") or data.get("message")
                                        or data))
    # Mirror the decision into the lifecycle log (day-1 Flow dataset).
    shadow = workitem_store.shadow_item(source, str(body.get("id")),
                                        str(body.get("title") or source))
    workitem_store.log_decision(shadow["work_item_id"], uid, decision, comments)
    return {"result": data}


@app.post("/api/work/thread")
async def work_thread(request: Request):
    """Ask the agent a question ON a work item; read-only tools, honest answers."""
    user = _verify_request(request)
    body = await request.json()
    source = str(body.get("source") or "agent")
    ref = str(body.get("id") or "")
    question = str(body.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "empty question")

    if source == "agent":
        item = workitem_store.get_item(ref)
        if not item:
            raise HTTPException(404, "work item not found")
    else:
        item = workitem_store.shadow_item(source, ref,
                                          str(body.get("title") or source))
    anchor_id = item["work_item_id"]
    context = body.get("context") or item.get("payload") or {}

    workitem_store.append_thread(anchor_id, "human", question,
                                 actor=user.get("username"))
    prompt = (_turn_envelope(user, body) + "\n\n"
              "You are answering a question asked ON a work item in My Work. "
              "Answer with evidence from your read-only tools; you cannot and "
              "must not change anything from this thread. Work item context:\n"
              f"{json.dumps({'source': source, 'title': item.get('title'), 'summary': item.get('summary'), 'payload': context}, default=str)[:3000]}\n\n"
              f"Question: {question}")
    reply_parts, session_id = [], item.get("thread_session")
    async for ev in run_turn(prompt, session_id, user, tool_scope="read"):
        if ev.get("type") == "text":
            reply_parts.append(ev["text"])
        elif ev.get("type") == "result" and ev.get("session_id"):
            workitem_store.set_thread_session(anchor_id, ev["session_id"])
    reply = "\n\n".join(reply_parts).strip() or "(no answer produced)"
    workitem_store.append_thread(anchor_id, "agent", reply)
    return {"reply": reply, "thread": workitem_store.thread(anchor_id)}


@app.get("/api/work/thread")
async def work_thread_get(request: Request):
    _verify_request(request)
    source = request.query_params.get("source", "agent")
    ref = request.query_params.get("id", "")
    if source == "agent":
        item = workitem_store.get_item(ref)
    else:
        with_shadow = workitem_store.shadow_item(source, ref, source)
        item = with_shadow
    if not item:
        return {"thread": []}
    return {"thread": workitem_store.thread(item["work_item_id"])}


@app.post("/api/views/edit-chat")
async def views_edit_chat(request: Request):
    """Inline 'Edit with AI' on the Views screen (James 2026-08-09): a chat
    scoped to ONE view. Full tool access (so save_view works), with the
    view's CURRENT definition injected so edits preserve untouched tiles.
    The client holds the session id per view for follow-ups; the screen
    re-runs the view after each reply so changes appear immediately."""
    user = _verify_request(request)
    uid = int(user["user_id"] or 0)
    body = await request.json()
    message = str(body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "empty message")
    view = views_store.get(str(body.get("name") or ""), uid,
                           readthrough.user_group_ids(uid),
                           str(body.get("scope") or ""),
                           int(body.get("group_id") or 0))
    if not view:
        raise HTTPException(404, "view not found (or not visible to you)")
    session_id = body.get("session_id") or None
    preamble = ""
    if not session_id:
        preamble = (
            "You are editing ONE saved View for this user, inline from the "
            "Views screen. Its CURRENT definition:\n"
            + json.dumps({"name": view["name"], "scope": view["scope"],
                          "group_id": view.get("group_id"),
                          "version": view["version"],
                          "tiles": view.get("tiles") or []}, indent=1)
            + "\nRULES: apply the user's change by re-saving with save_view "
            "using the SAME name and scope, passing the COMPLETE tile list — "
            "preserve every tile they didn't ask to change, INCLUDING each "
            "tile's 'layout' key ({w,h} spans from the user's arrangement). "
            "If they ask to RENAME the view, call rename_view — never "
            "save_view under a different name (that forks a copy). Verify any "
            "new SQL with probe_connection_query first. Automation tiles need "
            "a PROMOTED automation. Keep replies short — the screen refreshes "
            "the view after each of your turns.\n\nUser: ")
    from platform_tools import CURRENT_USER
    CURRENT_USER.set(user)
    ctx_line = _turn_envelope(user, body)
    reply_parts, out_session = [], session_id
    async for ev in run_turn(ctx_line + "\n\n" + preamble + message, session_id, user,
                             tool_scope="full"):
        if ev.get("type") == "text":
            reply_parts.append(ev["text"])
        elif ev.get("type") in ("result", "error"):
            out_session = ev.get("session_id") or out_session
    return {"reply": "\n\n".join(reply_parts).strip() or "(no reply produced)",
            "session_id": out_session}


# ---------------------------------------------------------------------------
# Playbooks inventory (A4 feedback #6) — the deterministic assets that exist,
# with deep links so builders can jump to the legacy designer/Mission Control.
# ---------------------------------------------------------------------------

@app.get("/api/playbooks")
async def playbooks(request: Request):
    user = _verify_request(request)
    from platform_tools import _get, _post, _pick
    out, errors = [], []
    try:
        rows = await _get("/get/workflows")
        for row in (rows or []):
            if not isinstance(row, dict):
                continue
            wd = row.get("workflow_data") or row.get("WORKFLOW_DATA")
            kind = "code_flow" if (isinstance(wd, str) and '"code_flow"' in wd) \
                else "workflow"
            out.append({"kind": kind,
                        "id": _pick(row, "id", "workflow_id"),
                        "name": _pick(row, "workflow_name", "name"),
                        "description": _pick(row, "description") or ""})
    except Exception as e:
        errors.append(f"workflows: {e}")
    try:
        body = {"action": "list",
                "user_context": {"user_id": int(user.get("user_id") or 0),
                                 "role": int(user.get("role") or 2),
                                 "username": str(user.get("username") or "")},
                "payload": {}}
        data, status = await _post("/automations/api/internal/manage", body)
        if status < 400:
            for a in (data.get("automations") or []):
                out.append({"kind": "automation",
                            "id": a.get("automation_id"),
                            "name": a.get("name"),
                            "description": a.get("description") or "",
                            "version": a.get("current_version"),
                            "pinned": a.get("pinned_version")})
        else:
            errors.append(f"automations: HTTP {status}")
    except Exception as e:
        errors.append(f"automations: {e}")
    try:
        # Portal workflows live in their own per-user store (NOT /get/workflows);
        # list_workflows scopes to the caller and never returns a secret. Keyed
        # by slug — there is no numeric id.
        from command_center.tools import portal_workflows as _pwf
        for w in _pwf.list_workflows(int(user.get("user_id") or 0)):
            out.append({"kind": "portal_workflow",
                        "id": w.get("slug"),
                        "name": w.get("name"),
                        "description": w.get("goal") or w.get("start_url") or ""})
    except Exception as e:
        errors.append(f"portal workflows: {e}")
    return {"playbooks": out, "errors": errors}


# ---------------------------------------------------------------------------
# Schedules surface (2026-08-30) — see/run/pause/cancel/create every scheduled
# job from one place. schedules_api talks to the main-app scheduler REST and
# the automations manage seam; the JSS engine stays the single execution path
# ("Run now" queues an engine-native one-shot row, so manual runs land in
# ScheduleExecutionHistory exactly like scheduled fires). Visibility: Dev+ sees
# all jobs (classic-page parity); role-1 sees only jobs whose user_id is theirs.
# ---------------------------------------------------------------------------

@app.get("/api/schedules")
async def schedules_list(request: Request):
    user = _verify_request(request)
    import schedules_api
    return await schedules_api.list_jobs(user)


@app.get("/api/schedules/{job_id}/history")
async def schedules_history(request: Request, job_id: int, limit: int = 25):
    user = _verify_request(request)
    import schedules_api
    return await schedules_api.history(user, job_id, limit)


@app.post("/api/schedules/{job_id}/run")
async def schedules_run_now(request: Request, job_id: int):
    user = _verify_request(request)
    import schedules_api
    return await schedules_api.run_now(user, job_id)


@app.post("/api/schedules/{job_id}/active")
async def schedules_set_active(request: Request, job_id: int):
    user = _verify_request(request)
    body = await request.json()
    import schedules_api
    return await schedules_api.set_active(user, job_id, bool(body.get("active")))


@app.delete("/api/schedules/{job_id}")
async def schedules_delete(request: Request, job_id: int):
    user = _verify_request(request)
    import schedules_api
    return await schedules_api.delete_job(user, job_id)


@app.post("/api/schedules")
async def schedules_create(request: Request):
    user = _verify_request(request)
    body = await request.json()
    import schedules_api
    return await schedules_api.create(user, body)


# ---------------------------------------------------------------------------
# Views API (A5) — deterministic dashboards. Refresh runs the pinned SQL
# through the governed probe seam; no LLM is involved anywhere on this path.
# ---------------------------------------------------------------------------

@app.get("/api/views")
async def views_list(request: Request):
    user = _verify_request(request)
    uid = int(user["user_id"] or 0)
    return {"views": views_store.list_views(uid, readthrough.user_group_ids(uid))}


@app.post("/api/views/run")
async def views_run(request: Request):
    user = _verify_request(request)
    uid = int(user["user_id"] or 0)
    body = await request.json()
    # Visibility is enforced in the store: a guessed group/user view name
    # resolves to nothing, not to data.
    view = views_store.get(str(body.get("name") or ""), uid,
                           readthrough.user_group_ids(uid),
                           str(body.get("scope") or ""),
                           int(body.get("group_id") or 0))
    if not view:
        raise HTTPException(404, "view not found (or not visible to you)")
    # Automation tiles run through the manage seam AS the refreshing user —
    # the run rows show who refreshed (audit-true).
    from platform_tools import CURRENT_USER
    CURRENT_USER.set(user)
    from views_tools import run_view
    tile_index = body.get("tile_index")
    return await run_view(view, int(tile_index) if tile_index is not None else None)


@app.post("/api/views/refresh-cache")
async def views_refresh_cache(request: Request):
    """Headless cache refresh for view_refresh JSS jobs (service-key auth,
    same contract as /api/run). Runs AS the stored principal — the user who
    created the schedule — and updates the tile cache every viewer sees.
    Zero LLM involvement; this is how plain users get current automation-tile
    data without holding a Developer role themselves."""
    if not _service_key_ok(request):
        raise HTTPException(401, "service key required")
    body = await request.json()
    principal = {
        "user_id": int(body.get("user_id") or 0),
        "role": int(body.get("role") or 2),
        "username": str(body.get("username") or "scheduler"),
        "name": str(body.get("username") or "scheduler"),
    }
    uid = principal["user_id"]
    view = views_store.get(str(body.get("name") or ""), uid,
                           readthrough.user_group_ids(uid),
                           str(body.get("scope") or ""),
                           int(body.get("group_id") or 0))
    if not view:
        raise HTTPException(404, "view not found (or not visible to the "
                                 "stored principal)")
    from platform_tools import CURRENT_USER
    CURRENT_USER.set(principal)
    from views_tools import run_view
    result = await run_view(view)
    ok_tiles = sum(1 for t in result["tiles"] if not t.get("error"))
    errs = [f"tile {t['index']} ({t.get('title')}): {t['error']}"
            for t in result["tiles"] if t.get("error")]
    logger.info(f"view refresh-cache '{view['name']}' [{view['scope']}] as "
                f"user {uid}: {ok_tiles}/{len(result['tiles'])} tiles ok")
    return {"ok": not errs, "tiles_ok": ok_tiles,
            "tiles_total": len(result["tiles"]), "errors": errs[:5]}


@app.post("/api/views/email")
async def views_email(request: Request):
    """Headless dashboard EMAIL for view_email JSS jobs — service-key auth, same
    contract as /api/views/refresh-cache, and it refreshes the shared tile cache
    on the way through (run_view does that itself).

    NO APPROVAL QUEUE (James, 2026-08-13): a user asking for "email me this
    dashboard every weekday at 9am" has given consent once, at schedule time,
    along with the recipient list — making them approve the same email every
    morning defeats the feature. auto_send / require_approval are therefore not
    consulted here.

    outbound_enabled IS still honored: that switch means "stop all outbound mail
    from this address", which is a global stop the user has NOT given.
    """
    if not _service_key_ok(request):
        raise HTTPException(401, "service key required")
    body = await request.json()
    principal = {
        "user_id": int(body.get("user_id") or 0),
        "role": int(body.get("role") or 2),
        "username": str(body.get("username") or "scheduler"),
        "name": str(body.get("username") or "scheduler"),
    }
    uid = principal["user_id"]
    to = [str(a).strip() for a in (body.get("to") or []) if str(a).strip()]
    if not to:
        raise HTTPException(400, "no recipients on the job")

    import email_client
    import email_render
    import email_store
    from work_tools import render_view_for_email

    addr = email_store.get_address(uid)
    if not addr or not addr.get("is_active"):
        raise HTTPException(400, "the scheduling user has no active agent email "
                                 "address — nothing sent")
    if not addr.get("outbound_enabled", 1):
        raise HTTPException(403, "outbound email is DISABLED for this address — "
                                 "nothing sent")

    name = str(body.get("name") or "")
    view_html, view_text, err, refresh = await render_view_for_email(
        name, str(body.get("view_scope") or ""), int(body.get("view_group_id") or 0),
        principal)
    if err:
        raise HTTPException(404, err)

    subject = str(body.get("subject") or f"{name} — dashboard")[:300]
    note = str(body.get("note") or f"Your scheduled '{name}' dashboard.")
    result = await email_client.send_reply(
        to, subject, note + "\n\n" + view_text, addr["email_address"],
        f"{addr.get('prefix', 'agent')} via The Agent",
        html_body=(email_render.render_email_with_view(note, view_html,
                                                       title=subject)
                   if email_render.html_enabled() else None))
    ok = bool(result.get("success"))
    logger.info(f"view email '{name}' as user {uid} -> {to}: "
                f"{'sent' if ok else 'FAILED'} ({refresh}); "
                f"{json.dumps(result)[:200]}")
    return {"ok": ok, "refresh": refresh,
            "error": None if ok else str(result.get("error", result))[:300]}


@app.post("/api/views/layout")
async def views_layout(request: Request):
    """Persist the user's tile arrangement (drag-reorder + resize on the Views
    screen, James 2026-08-09). Presentation only: version does not bump, and
    the positional tile cache is permuted alongside the tiles so cached
    results stay attached to the right tile."""
    user = _verify_request(request)
    uid = int(user["user_id"] or 0)
    body = await request.json()
    view, err = views_store.update_layout(
        str(body.get("name") or ""), uid, readthrough.user_group_ids(uid),
        int(user.get("role") or 0), str(body.get("scope") or ""),
        int(body.get("group_id") or 0),
        order=body.get("order"), layouts=body.get("layouts"))
    if err:
        code = 403 if ("admin" in err or "owner" in err) else \
               (404 if "not found" in err else 400)
        raise HTTPException(code, err)
    return {"ok": True, "name": view["name"],
            "tiles": [{"index": i, "layout": t.get("layout")}
                      for i, t in enumerate(view.get("tiles") or [])]}


@app.post("/api/views/rename")
async def views_rename(request: Request):
    """Rename a view IN PLACE (id/version/cache preserved) and re-point any
    view_refresh scheduler jobs — they reference the view by NAME, so leaving
    them behind would 404 every future scheduled refresh."""
    user = _verify_request(request)
    uid = int(user["user_id"] or 0)
    body = await request.json()
    view, err = views_store.rename(
        str(body.get("name") or ""), str(body.get("new_name") or ""), uid,
        readthrough.user_group_ids(uid), int(user.get("role") or 0),
        str(body.get("scope") or ""), int(body.get("group_id") or 0))
    if err:
        code = 403 if ("admin" in err or "owner" in err) else \
               (404 if "not found" in err else 400)
        raise HTTPException(code, err)
    from views_tools import rewrite_view_refresh_jobs
    sched = await rewrite_view_refresh_jobs(
        view["old_name"], view["name"], view["scope"],
        int(view.get("group_id") or 0),
        modified_by=str(user.get("username") or "user"))
    return {"ok": True, "name": view["name"], "old_name": view["old_name"],
            "scope": view["scope"], "group_id": view.get("group_id"),
            "version": view["version"],
            "schedules_updated": sched["updated"],
            "schedules_failed": sched["failed"]}


# ---------------------------------------------------------------------------
# Agent Email (A6) — per-user address provisioning + activity log
# ---------------------------------------------------------------------------

@app.get("/api/email/address")
async def email_address_get(request: Request):
    user = _verify_request(request)
    import email_store
    import email_client
    import email_poller
    info = await email_client.tenant_info()
    row = email_store.get_address(int(user["user_id"] or 0))
    return {
        "address": row,
        "suffix": (f"-agent.{info['tenant_id']}@{info['domain']}"
                   if info else None),
        # Default to the USERNAME (sanitized for email), not the user id —
        # fall back to the id only when nothing email-safe survives.
        "default_prefix": (email_store.sanitize_prefix(user.get("username"))
                           or str(user["user_id"])),
        "poller_enabled": email_poller.enabled(),
        "poll_seconds": email_poller.POLL_SECONDS,
        "tenant_ok": bool(info),
    }


@app.post("/api/email/address")
async def email_address_set(request: Request):
    """Create/update the CURRENT user's agent address. Same contract as the
    legacy config page: user picks the prefix, the suffix is fixed per
    install (numeric TenantId + domain from the cloud — readonly)."""
    user = _verify_request(request)
    import email_store
    import email_client
    body = await request.json()
    # Normalize instead of rejecting (spaces -> hyphens, invalid chars
    # stripped); default = sanitized username, then user id.
    prefix = email_store.sanitize_prefix(
        body.get("prefix")
        or email_store.sanitize_prefix(user.get("username"))
        or str(user["user_id"]))
    if not email_store.valid_prefix(prefix):
        raise HTTPException(400, "That prefix has no email-safe characters "
                                 "(a-z, 0-9, hyphen) — pick another.")
    info = await email_client.tenant_info()
    if not info:
        raise HTTPException(502, "Could not reach the cloud email service to "
                                 "resolve this install's address suffix — "
                                 "nothing was saved.")
    address = email_client.compose_address(prefix, info["tenant_id"],
                                           info["domain"])
    try:
        row = email_store.upsert_address(
            int(user["user_id"] or 0), prefix, address,
            str(user.get("username") or ""), int(user.get("role") or 2),
            bool(body.get("enabled", True)))
    except ValueError as e:
        raise HTTPException(409, str(e))
    # Per-address parity options (James 2026-08-09): only fields present in
    # the request are updated; absent fields keep their stored values.
    opts = {}
    if "auto_send" in body:
        opts["auto_send"] = 1 if body["auto_send"] else 0
    if "outbound_enabled" in body:
        opts["outbound_enabled"] = 1 if body["outbound_enabled"] else 0
    if "notify_on_receive" in body:
        opts["notify_on_receive"] = 1 if body["notify_on_receive"] else 0
    if "notification_email" in body:
        opts["notification_email"] = str(body["notification_email"] or "").strip()[:200]
    if "cooldown_minutes" in body:
        cm = body["cooldown_minutes"]
        opts["cooldown_minutes"] = max(0, min(int(cm), 1440)) if cm not in (None, "") else None
    if "reply_instructions" in body:
        opts["reply_instructions"] = str(body["reply_instructions"] or "")[:2000]
    if opts:
        row = email_store.set_options(int(user["user_id"] or 0), **opts)
    return {"address": row}


# ---------------------------------------------------------------------------
# Runtime settings (James 2026-08-09): admin-set model, no restart needed
# ---------------------------------------------------------------------------

@app.post("/api/settings/model")
async def settings_model(request: Request):
    """Set (or clear with empty string) the runtime model override. Admin
    only; applies from the very next turn — no restart. AGENT_MODEL in .env
    remains the install default underneath."""
    user = _verify_request(request)
    if int(user.get("role") or 0) < 3:
        raise HTTPException(403, "Changing the model requires an admin.")
    from agent_config import set_model_override
    body = await request.json()
    try:
        effective = set_model_override(body.get("model"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    logger.info(f"model override changed by user {user['user_id']} "
                f"({user.get('username')}) -> effective {effective}")
    return {"model": effective, "model_default": AGENT_MODEL,
            "override_active": effective != AGENT_MODEL}


@app.post("/api/settings/role1-model")
async def settings_role1_model(request: Request):
    """Set (or clear with empty string) the model REGULAR users (role < 2) run
    on (all-users rollout D4). Admin only; applies from the very next turn.
    Clearing falls back to AGENT_MODEL_ROLE1 (haiku by default)."""
    user = _verify_request(request)
    if int(user.get("role") or 0) < 3:
        raise HTTPException(403, "Changing the model requires an admin.")
    from agent_config import set_role1_model_override, AGENT_MODEL_ROLE1
    body = await request.json()
    try:
        effective = set_role1_model_override(body.get("model"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    logger.info(f"role1 model override changed by user {user['user_id']} "
                f"({user.get('username')}) -> effective {effective}")
    return {"model_role1": effective, "model_role1_default": AGENT_MODEL_ROLE1,
            "override_active": effective != AGENT_MODEL_ROLE1}


@app.post("/api/settings/turn-cap")
async def settings_turn_cap(request: Request):
    """Set (or clear with 0/empty) the per-user daily turn cap (all-users
    rollout D6 — DEFAULT OFF). Admin only; admins are always exempt from the
    cap itself; applies from the very next turn."""
    user = _verify_request(request)
    if int(user.get("role") or 0) < 3:
        raise HTTPException(403, "Changing the turn cap requires an admin.")
    from agent_config import set_turn_cap
    body = await request.json()
    try:
        cap = set_turn_cap(body.get("turns_per_day"))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, f"turns_per_day must be 0 (off) or a positive "
                                 f"integer: {e}")
    logger.info(f"daily turn cap changed by user {user['user_id']} "
                f"({user.get('username')}) -> {cap or 'OFF'}")
    return {"turns_per_day": cap}


# ---------------------------------------------------------------------------
# File handoff (James 2026-08-09): download links in chat
# ---------------------------------------------------------------------------

@app.get("/api/files/{file_id}")
async def serve_offered_file(file_id: str, request: Request):
    """Serve a file the agent explicitly offered to THIS user
    (offer_file_download stages copies per-user; cross-user ids 404)."""
    user = _verify_request(request)
    import file_tools
    hit = file_tools.resolve_offer(int(user["user_id"] or 0), file_id)
    if not hit:
        raise HTTPException(404, "file not found")
    path, original_name = hit
    return FileResponse(path, filename=original_name,
                        media_type="application/octet-stream")


# ---------------------------------------------------------------------------
# Chat history (CC-parity, James 2026-08-09)
# ---------------------------------------------------------------------------

@app.get("/api/chat/history")
async def chat_history_list(request: Request):
    user = _verify_request(request)
    import chat_history
    return {"sessions": chat_history.list_sessions(int(user["user_id"] or 0))}


@app.get("/api/chat/history/{hist_session_id}")
async def chat_history_replay(hist_session_id: str, request: Request):
    user = _verify_request(request)
    import chat_history
    if not chat_history.owns_session(int(user["user_id"] or 0), hist_session_id):
        raise HTTPException(404, "conversation not found")
    return {"session_id": hist_session_id,
            "turns": chat_history.replay(hist_session_id)}


@app.get("/api/chat/version")
async def chat_version(request: Request, session_id: str = ""):
    """Live-update channel for an OPEN conversation (2026-08-23, hand-back
    bridge): the UI polls this while idle on a conversation and re-renders the
    thread when `version` changes — a portal-run update or a deferred scheduled
    result was appended by the service, not by the user. `inflight` lets the
    UI show "The Agent is adding a result…" while such a turn runs."""
    user = _verify_request(request)
    import chat_history
    sid = str(session_id or "").strip()
    if not sid or not chat_history.owns_session(int(user["user_id"] or 0), sid):
        raise HTTPException(404, "conversation not found")
    return {"session_id": sid, "version": session_version(sid), "inflight": is_inflight(sid)}


@app.get("/api/portal/watches")
async def portal_watches(request: Request):
    """This user's portal-run watches (hand-back bridge): which runs the
    service is following, their phase (paused = waiting for the user's
    take-over, running = handed back / still working), and how each ended."""
    user = _verify_request(request)
    import portal_watch
    rows = portal_watch.list_for_user(int(user["user_id"] or 0))
    keep = ("run_id", "session_id", "label", "kind", "phase", "reason", "status",
            "created_at", "updated_at", "handback_at", "done_at", "outcome")
    return {"watches": [{k: r.get(k) for k in keep} for r in rows],
            "enabled": portal_watch.ENABLED}


@app.get("/api/email/log")
async def email_log(request: Request):
    user = _verify_request(request)
    import email_store
    row = email_store.get_address(int(user["user_id"] or 0))
    if not row:
        return {"log": []}
    return {"log": email_store.recent(row["email_address"], 20)}


def _own_ledger_row(user: dict, event_id: int) -> dict:
    """The expand-a-row viewer's authz: the event must be in the CALLING
    user's own ledger (scoped by their address) — otherwise any signed-in
    user could pull arbitrary tenant mail by guessing event ids. The lookup
    itself lives in email_tools.ledger_entry_for — ONE chokepoint shared
    with the email READING tools (which additionally accept live-feed
    ownership; these routes deliberately stay ledger-only, matching what
    the Email page lists)."""
    from email_tools import ledger_entry_for, EmailAccess
    try:
        entry, _address = ledger_entry_for(int(user["user_id"] or 0),
                                           int(event_id))
    except EmailAccess as e:
        raise HTTPException(404, str(e))
    return entry


@app.get("/api/email/log/{event_id}")
async def email_log_detail(event_id: int, request: Request):
    """Expand one logged inbound email: full body + attachment list, fetched
    LIVE from the cloud (which retains mail ~3 days — the ledger keeps no
    content by design). retained=false when the body is gone; metadata and
    the agent's outcome still come back from the ledger row."""
    user = _verify_request(request)
    import email_client
    entry = _own_ledger_row(user, event_id)
    key = str(entry.get("message_key") or "")
    if not key and event_id:
        # Rows recorded before the message_key column: recover the key from
        # the live feed while the cloud still retains the event. poll() has
        # no internal catch (its other caller wants the error) — here a
        # cloud outage must degrade to retained:false, not a 500.
        try:
            for ev in await email_client.poll():
                if int(ev.get("event_id") or ev.get("id") or 0) == int(event_id):
                    key = str(ev.get("message_key") or "")
                    break
        except Exception as e:
            logger.warning(f"email log-detail poll fallback failed: {e}")
    message = await email_client.full_message(key) if key else None
    atts = await email_client.attachments_for(int(event_id)) if event_id else []
    return {
        "entry": entry,
        "retained": bool(message),
        "body_html": str((message or {}).get("body_html")
                         or (message or {}).get("body-html") or ""),
        "body_text": str(email_client.body_text_of(message) or ""),
        "attachments": [{
            "attachment_id": a.get("attachment_id") or a.get("id"),
            "filename": a.get("filename")
                        or f"attachment-{a.get('attachment_id') or a.get('id')}",
            "content_type": a.get("content_type") or "",
            "size": a.get("size") or 0,
        } for a in atts],
    }


@app.get("/api/email/log/{event_id}/attachment/{attachment_id}")
async def email_log_attachment(event_id: int, attachment_id: int,
                               request: Request):
    """Download one attachment from a logged email. Ownership = the ledger
    row; membership = the cloud's attachment list for that event. Both are
    checked before any bytes are proxied, so an attachment id can never be
    fetched through someone else's (or no) email."""
    user = _verify_request(request)
    import email_client
    _own_ledger_row(user, event_id)
    atts = await email_client.attachments_for(int(event_id))
    match = next((a for a in atts
                  if int(a.get("attachment_id") or a.get("id") or 0)
                  == int(attachment_id)), None)
    if not match:
        raise HTTPException(404, "That attachment is not on that email.")
    fetched = await email_client.attachment_bytes(int(attachment_id))
    if not fetched:
        raise HTTPException(502, "The cloud mailbox could not serve the "
                                 "attachment (retention may have expired).")
    content, content_type = fetched
    # Filename originates from inbound mail (attacker-controlled): reduce to
    # a header-safe charset — no quotes, no CR/LF, nothing exotic.
    safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_",
                       str(match.get("filename") or ""))[:150].strip() \
        or f"attachment-{attachment_id}"
    return Response(
        content=content,
        media_type=content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'})


if __name__ == "__main__":
    import uvicorn
    is_frozen = getattr(sys, "frozen", False)
    if is_frozen:
        uvicorn.run(app, host=HOST, port=PORT, reload=False)
    else:
        uvicorn.run("main:app", host=HOST, port=PORT, reload=False)
