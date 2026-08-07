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


@asynccontextmanager
async def lifespan(app):
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


if __name__ == "__main__":
    import uvicorn
    is_frozen = getattr(sys, "frozen", False)
    if is_frozen:
        uvicorn.run(app, host=HOST, port=PORT, reload=False)
    else:
        uvicorn.run("main:app", host=HOST, port=PORT, reload=False)
