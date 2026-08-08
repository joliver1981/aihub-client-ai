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
import sys

import agent_config  # noqa: F401  (must be first: APP_ROOT, .env, secure_config)
from agent_config import (
    HOST, PORT, DEBUG, AGENT_MODEL, AGENT_ALLOW_ALL_USERS, logger, summary,
)

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

import shared_auth  # from APP_ROOT (agent_config put it on sys.path)
from brain import run_turn
import workitem_store
import views_store
import readthrough


@asynccontextmanager
async def lifespan(app):
    workitem_store.init()
    views_store.init()
    import email_store
    email_store.init()
    poller_task = None
    import email_poller
    if email_poller.enabled():
        poller_task = asyncio.get_event_loop().create_task(
            email_poller.run_forever())
    else:
        logger.info("agent email poller disabled (AGENT_EMAIL_ENABLED != true)")
    logger.info(f"The Agent starting: {json.dumps(summary())}")
    yield
    if poller_task:
        poller_task.cancel()
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
        raise HTTPException(401, "Missing token — open The Agent from AI Hub.")
    claims, err = shared_auth.verify_token(token, shared_auth.AUD_CC)
    if err:
        raise HTTPException(401, f"Invalid or expired token ({err}) — reopen from AI Hub.")
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
    return {"user": {k: user[k] for k in ("username", "name", "role")},
            "model": AGENT_MODEL,
            # Deep links into the legacy app (Playbooks/Platform views) target
            # the same hostname the browser used, on the main app's port.
            "main_port": int(os.getenv("HOST_PORT", "5001"))}


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


@app.post("/api/run")
async def headless_run(request: Request):
    """
    Headless brain turn for scheduled agent_session jobs (and future webhook/
    email triggers). Runs AS the principal stored on the trigger — the user
    who created the schedule — and reports the outcome into that user's
    My Work queue as an acknowledge (FYI) item. Auth: platform service key.
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
    }
    job_name = str(body.get("job_name") or "Scheduled agent task")
    logger.info(f"headless run start job={job_name!r} as user "
                f"{user_ctx['user_id']}/{user_ctx['username']}")

    texts, tools_run, final = [], [], {}
    async for ev in run_turn(prompt, None, user_ctx, tool_scope="full"):
        if ev.get("type") == "text":
            texts.append(ev["text"])
        elif ev.get("type") == "tool":
            tools_run.append(ev.get("name", "?").replace("mcp__aihub__", ""))
        elif ev.get("type") in ("result", "error"):
            final = ev
    ok = bool(final.get("ok"))
    summary_text = "\n\n".join(texts).strip()

    item = workitem_store.create_item(
        "acknowledge",
        f"{'✓' if ok else '⚠'} {job_name}",
        summary=(summary_text[:2000] or "(the run produced no text)"),
        payload={"kind": "headless_run", "ok": ok,
                 "subtype": final.get("subtype") or final.get("error"),
                 "session_id": final.get("session_id"),
                 "tools_used": tools_run[:30], "prompt": prompt[:500]},
        addressed_user=user_ctx["user_id"] or None,
        from_kind="agent_headless", from_ref=job_name,
        created_by="scheduler")
    logger.info(f"headless run done job={job_name!r} ok={ok} "
                f"item={item['work_item_id']}")
    return {"ok": ok, "subtype": final.get("subtype") or final.get("error"),
            "session_id": final.get("session_id"),
            "work_item_id": item["work_item_id"]}


@app.post("/api/chat")
async def chat(request: Request):
    user = _verify_request(request)
    body = await request.json()
    message = str(body.get("message") or "").strip()
    session_id = body.get("session_id") or None
    if not message:
        raise HTTPException(400, "Empty message")

    async def event_stream():
        try:
            async for event in run_turn(message, session_id, user):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:  # belt-and-suspenders: never die mid-stream silently
            logger.error(f"/api/chat stream error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
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
        final_body = str((body.get("response") or {}).get("text") or "").strip() \
            or str(payload.get("body") or "")
        result = await email_client.send_reply(
            payload.get("to") or [], str(payload.get("subject") or ""),
            final_body, str(payload.get("from_address") or ""),
            f"{payload.get('from_address', '').split('@')[0]} via The Agent")
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
    prompt = ("You are answering a question asked ON a work item in My Work. "
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
    return {"playbooks": out, "errors": errors}


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
    return {"address": row}


@app.get("/api/email/log")
async def email_log(request: Request):
    user = _verify_request(request)
    import email_store
    row = email_store.get_address(int(user["user_id"] or 0))
    if not row:
        return {"log": []}
    return {"log": email_store.recent(row["email_address"], 20)}


if __name__ == "__main__":
    import uvicorn
    is_frozen = getattr(sys, "frozen", False)
    if is_frozen:
        uvicorn.run(app, host=HOST, port=PORT, reload=False)
    else:
        uvicorn.run("main:app", host=HOST, port=PORT, reload=False)
