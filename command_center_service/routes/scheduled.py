"""
Command Center - headless scheduled-run endpoint.

Invoked by the Job Scheduler service (AIHubJobScheduler) when a 'command_center' job fires.
INTERNAL ONLY: gated by X-API-Key (the scheduler is a trusted caller). Runs the CC graph as
the stored user in a FRESH session, then writes the result (text summary + any artifact
blocks) to the user's per-user schedule_store - the "results thread" the panel shows on next
login. Deliberately separate from /api/chat (which is JWT + SSE) so existing chat is untouched.
"""
import logging
import os
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scheduled", tags=["scheduled"])

_graph = None


def init_scheduled_routes(graph):
    global _graph
    _graph = graph


def _internal_ok(request: Request) -> bool:
    """Trusted-caller gate: the scheduler service posts with the platform API key."""
    expected = ""
    try:
        from cc_config import AI_HUB_API_KEY
        expected = AI_HUB_API_KEY or ""
    except Exception:
        expected = os.getenv("API_KEY", "")
    got = request.headers.get("x-api-key") or request.headers.get("X-API-Key") or ""
    return bool(expected) and got == expected


def _last_ai_text(final_state) -> str:
    """Mirror routes/chat.py: take the content of the last AI/assistant message."""
    messages = final_state.get("messages", []) if isinstance(final_state, dict) else []
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "ai":
            return msg.content if isinstance(msg.content, str) else str(msg.content)
        if getattr(msg, "role", None) == "assistant":
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return ""


@router.post("/run")
async def run_scheduled(request: Request):
    if not _internal_ok(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if not _graph:
        return JSONResponse({"error": "graph not initialized"}, status_code=500)

    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    uc = body.get("user_context") or {}
    uid = uc.get("user_id")
    job_id = body.get("job_id")
    task_name = body.get("task_name") or "Scheduled task"
    agent_id = body.get("agent_id") or None
    agent_name = body.get("agent_name") or None
    if not prompt or not uid:
        return JSONResponse({"error": "prompt and user_context.user_id are required"}, status_code=400)

    from scheduling import schedule_store as store

    # Fresh session per run, owned by the stored user (avoids session-ownership conflicts).
    session_id = f"sched-{job_id or 'x'}-{uuid.uuid4().hex[:8]}"
    graph_input = {
        "messages": [HumanMessage(content=prompt)],
        "session_id": session_id,
        "user_context": uc,
    }
    if agent_id:
        graph_input["active_delegation"] = {"agent_id": agent_id, "agent_name": agent_name}
    config = {"configurable": {"thread_id": session_id}}

    try:
        final_state = await _graph.ainvoke(graph_input, config=config)
    except Exception as e:
        logger.error(f"[scheduled/run] graph failed job={job_id}: {e}", exc_info=True)
        store.add_result(uid, job_id, task_name, "failed", f"Run failed: {e}")
        return JSONResponse({"status": "failed", "error": str(e)})

    from routes.chat import _parse_response_blocks
    blocks = _parse_response_blocks(_last_ai_text(final_state))
    summary_parts, artifact_ids, render_blocks = [], [], []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        bt = b.get("type")
        if bt == "text":
            summary_parts.append(b.get("content", ""))
        elif bt == "artifact" and b.get("artifact_id"):
            artifact_ids.append(b["artifact_id"])
        render_blocks.append(b)
    summary = ("\n".join(s for s in summary_parts if s).strip()
               or "(completed with no text output)")[:2000]
    result = store.add_result(uid, job_id, task_name, "completed", summary,
                              artifact_ids, render_blocks)
    logger.info(f"[scheduled/run] job={job_id} user={uid} artifacts={len(artifact_ids)} ok")
    return JSONResponse({"status": "completed", "summary": summary,
                         "artifact_count": len(artifact_ids), "run_id": result["run_id"]})
