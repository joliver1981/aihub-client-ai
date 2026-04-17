"""
Builder Data Service — Chat Routes
=====================================
SSE streaming endpoint for the data agent, mirroring
the builder_service/routes/chat.py pattern exactly.
"""

import json
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from sse_starlette.sse import EventSourceResponse
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class SessionCreate(BaseModel):
    title: Optional[str] = "New Chat"


graph = None
session_manager = None


def init_chat_routes(_graph, _session_manager):
    global graph, session_manager
    graph = _graph
    session_manager = _session_manager


PHASE_CONFIG = {
    "classify_intent": {
        "phase": "thinking",
        "label": "Understanding your request...",
        "icon": "brain",
    },
    "converse": {
        "phase": "responding",
        "label": "Composing response...",
        "icon": "chat",
    },
    "design_pipeline": {
        "phase": "analyzing",
        "label": "Designing data pipeline...",
        "icon": "search",
    },
    "execute_pipeline": {
        "phase": "executing",
        "label": "Executing pipeline steps...",
        "icon": "rocket",
    },
    "analyze_quality": {
        "phase": "analyzing",
        "label": "Analyzing data quality...",
        "icon": "search",
    },
    "present_results": {
        "phase": "responding",
        "label": "Formatting results...",
        "icon": "chart",
    },
    "handle_rejection": {
        "phase": "responding",
        "label": "Revising approach...",
        "icon": "edit",
    },
}


@router.post("/chat")
async def chat(request: ChatRequest):
    if graph is None:
        raise HTTPException(status_code=503, detail="Data agent not initialized")

    session = session_manager.get_or_create(request.session_id)
    session.touch()

    if session.message_count <= 1:
        title = request.message[:50]
        if len(request.message) > 50:
            title += "..."
        session_manager.update_title(session.session_id, title)

    thread_config = {"configurable": {"thread_id": session.session_id}}

    async def event_generator():
        try:
            current_node = ""
            emitted_first_token = False
            nodes_visited = []

            logger.info(f"=== Data chat: session={session.session_id} message={request.message[:80]}...")

            async for event in graph.astream_events(
                {"messages": [HumanMessage(content=request.message)]},
                config=thread_config,
                version="v2",
            ):
                kind = event.get("event", "")
                name = event.get("name", "")

                # Node starts
                if kind == "on_chain_start" and name in PHASE_CONFIG:
                    current_node = name
                    nodes_visited.append(name)
                    config = PHASE_CONFIG[name]
                    logger.info(f"  > Node started: {name} ({config['label']})")
                    yield {
                        "event": "status",
                        "data": json.dumps({
                            "phase": config["phase"],
                            "label": config["label"],
                            "icon": config["icon"],
                            "node": name,
                        }),
                    }

                # Token streaming
                elif kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        token = chunk.content
                        streamable_nodes = {
                            "converse", "design_pipeline", "analyze_quality",
                            "present_results", "handle_rejection",
                        }
                        if current_node in streamable_nodes:
                            if not emitted_first_token:
                                emitted_first_token = True
                                logger.info(f"  First token from {current_node}")
                                yield {
                                    "event": "status",
                                    "data": json.dumps({
                                        "phase": "streaming",
                                        "label": "Responding...",
                                        "icon": "stream",
                                        "node": current_node,
                                    }),
                                }
                            yield {
                                "event": "token",
                                "data": json.dumps({"text": token}),
                            }

            # Stream complete — read final state
            logger.info(f"  Stream complete. Nodes visited: {nodes_visited}")

            try:
                state_snapshot = graph.get_state(thread_config)
                final_state = state_snapshot.values if state_snapshot else {}

                intent = final_state.get("intent", "unknown")
                pipeline = final_state.get("current_pipeline")
                pipeline_result = final_state.get("pipeline_result")
                quality_report = final_state.get("quality_report")

                logger.info(f"  Final state: intent={intent}")

                # Emit pipeline plan for confirmation
                if pipeline and not pipeline_result:
                    yield {
                        "event": "pipeline",
                        "data": json.dumps(pipeline),
                    }

                # Emit pipeline result
                if pipeline_result:
                    yield {
                        "event": "pipeline_result",
                        "data": json.dumps(pipeline_result),
                    }

                # Emit quality report
                if quality_report:
                    yield {
                        "event": "quality_report",
                        "data": json.dumps(quality_report),
                    }

            except Exception as e:
                logger.warning(f"  Could not read final state: {e}")

            # Done
            yield {
                "event": "done",
                "data": json.dumps({"session_id": session.session_id}),
            }
            logger.info(f"=== Done: session={session.session_id}")

        except Exception as e:
            logger.error(f"  Stream error: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({"message": str(e)}),
            }
            yield {
                "event": "done",
                "data": json.dumps({"session_id": session.session_id}),
            }

    return EventSourceResponse(event_generator())


# ─── Session Management ──────────────────────────────────

@router.get("/sessions")
async def list_sessions():
    return {"sessions": session_manager.list_sessions()}


@router.post("/sessions")
async def create_session(request: SessionCreate):
    session = session_manager.create_session(title=request.title)
    return session.to_dict()


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    if session_manager.delete_session(session_id):
        return {"deleted": True}
    raise HTTPException(status_code=404, detail="Session not found")


@router.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "builder_data",
        "graph_loaded": graph is not None,
    }
