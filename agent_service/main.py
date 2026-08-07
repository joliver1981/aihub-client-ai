"""
The Agent service — FastAPI app.

Serves the next-gen chat UI and streams brain turns over SSE. Users arrive via
the main app's /the-agent token redirect (same shared_auth JWT pattern as
Command Center); every API call re-verifies the token, and the resulting user
context becomes the session envelope the tools read.

Run (dev):  conda activate aihub-agent && python main.py
Health:     GET /health
"""

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
import readthrough


@asynccontextmanager
async def lifespan(app):
    workitem_store.init()
    logger.info(f"The Agent starting: {json.dumps(summary())}")
    yield
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
            "model": AGENT_MODEL}


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
    """Close an agent-raised item with the human's response."""
    user = _verify_request(request)
    body = await request.json()
    item, err = workitem_store.respond(str(body.get("id")),
                                       int(user["user_id"]),
                                       body.get("response") or {})
    if err:
        raise HTTPException(409, err)
    return {"item": _agent_item_view(item)}


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


if __name__ == "__main__":
    import uvicorn
    is_frozen = getattr(sys, "frozen", False)
    if is_frozen:
        uvicorn.run(app, host=HOST, port=PORT, reload=False)
    else:
        uvicorn.run("main:app", host=HOST, port=PORT, reload=False)
