"""
Builder Service — Chat Routes
================================
SSE streaming endpoint with rich event types.

Key fix: Instead of trying to extract plan data from on_chain_end events
(which is unreliable), we read the final graph state after streaming
completes to get the plan data. The graph's MemorySaver checkpointer
stores the full state per session.
"""

import json
import logging
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from sse_starlette.sse import EventSourceResponse
from langchain_core.messages import HumanMessage

from builder_config import get_base_url
from services import UserContext
from routes.upload import build_attachment_context, associate_files_to_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    attachments: Optional[list[str]] = None  # List of file_ids from /api/upload
    user_context: Optional[dict] = None  # Optional user context for programmatic callers (e.g., Command Center)


class SessionCreate(BaseModel):
    title: Optional[str] = "New Chat"


graph = None
session_manager = None


def init_routes(_graph, _session_manager):
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
    "query_and_respond": {
        "phase": "querying",
        "label": "Fetching system data...",
        "icon": "search",
    },
    "analyze_and_plan": {
        "phase": "analyzing",
        "label": "Analyzing requirements & building plan...",
        "icon": "search",
    },
    "execute": {
        "phase": "executing",
        "label": "Executing plan steps...",
        "icon": "rocket",
    },
    "handle_rejection": {
        "phase": "responding",
        "label": "Revising approach...",
        "icon": "edit",
    },
    # ─── Agent Response Phase ────────────────────────────────────────────
    # Agent delegations now happen inside the execute node
    # This phase handles follow-up responses to agent questions
    "handle_agent_response": {
        "phase": "delegating",
        "label": "Forwarding to specialist agent...",
        "icon": "users",
    },
}


@router.post("/chat")
async def chat(request: ChatRequest):
    if graph is None:
        raise HTTPException(status_code=503, detail="Builder agent not initialized")

    session = session_manager.get_or_create(request.session_id)
    session.touch()

    # If caller provides user_context and session doesn't have one, set it
    # This enables programmatic callers (e.g., Command Center) to pass auth context
    if request.user_context and not session.user_context:
        from services import UserContext as _UC
        session.user_context = _UC(
            user_id=request.user_context.get("user_id"),
            role=request.user_context.get("role"),
            tenant_id=request.user_context.get("tenant_id"),
            username=request.user_context.get("username"),
            name=request.user_context.get("name"),
        )
        logger.info(f"Set user context on session {session.session_id} from request: user_id={session.user_context.user_id}")

    if session.message_count <= 1:
        title = request.message[:50]
        if len(request.message) > 50:
            title += "..."
        session_manager.update_title(session.session_id, title)

    thread_config = {"configurable": {"thread_id": session.session_id}}

    # Build the message content, including attachment context if files are attached
    message_content = request.message
    if request.attachments:
        associate_files_to_session(request.attachments, session.session_id)
        attachment_ctx = build_attachment_context(request.attachments)
        if attachment_ctx:
            message_content = request.message + attachment_ctx

    # Build input state with user context for permission-aware planning/execution
    input_state = {"messages": [HumanMessage(content=message_content)]}
    if session.user_context:
        input_state["user_context"] = session.user_context.to_dict()

    # Persist the user message (original text, not the attachment-augmented version)
    session_manager.add_message(session.session_id, "user", request.message)

    async def event_generator():
        try:
            current_node = ""
            emitted_first_token = False
            nodes_visited = []
            response_buffer = []  # Accumulate AI response tokens for persistence

            logger.info(f"═══ Chat request: session={session.session_id} message={request.message[:80]}...")

            async for event in graph.astream_events(
                input_state,
                config=thread_config,
                version="v2",
            ):
                kind = event.get("event", "")
                name = event.get("name", "")

                # ── Node starts ──
                if kind == "on_chain_start" and name in PHASE_CONFIG:
                    current_node = name
                    nodes_visited.append(name)
                    config = PHASE_CONFIG[name]
                    logger.info(f"  ▶ Node started: {name} ({config['label']})")
                    yield {
                        "event": "status",
                        "data": json.dumps({
                            "phase": config["phase"],
                            "label": config["label"],
                            "icon": config["icon"],
                            "node": name,
                        }),
                    }

                # ── Token streaming ──
                elif kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        token = chunk.content
                        # Only stream tokens from nodes that produce user-facing responses
                        # Skip: classify_intent (internal routing)
                        streamable_nodes = {"converse", "query_and_respond", "analyze_and_plan", "handle_rejection", "execute", "handle_agent_response"}
                        if current_node in streamable_nodes:
                            if not emitted_first_token:
                                emitted_first_token = True
                                logger.info(f"  ✎ First token from {current_node}")
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
                            response_buffer.append(token)

            # ── Stream complete — persist AI response ──
            if response_buffer:
                full_response = "".join(response_buffer)
                session_manager.add_message(session.session_id, "assistant", full_response)

            # ── Read final state from checkpointer ──
            logger.info(f"  ✓ Stream complete. Nodes visited: {nodes_visited}")

            # Get the final graph state to extract plan data
            try:
                state_snapshot = graph.get_state(thread_config)
                final_state = state_snapshot.values if state_snapshot else {}

                intent = final_state.get("intent", "unknown")
                plan = final_state.get("current_plan")

                logger.info(f"  ℹ Final state — intent: {intent}, has_plan: {plan is not None}")

                if plan:
                    steps = plan.get("steps", [])
                    status = plan.get("status", "unknown")
                    logger.info(f"  📋 Plan: status={status}, steps={len(steps)}")
                    for i, step in enumerate(steps):
                        logger.info(f"     Step {i+1}: [{step.get('domain','?')}/{step.get('action','?')}] "
                                     f"{step.get('description','')[:60]} — {step.get('status','?')}")

                    # Emit plan event with current status
                    # - "draft" = new plan awaiting confirmation
                    # - "completed/partial/failed" = execution finished, update UI
                    yield {
                        "event": "plan",
                        "data": json.dumps(plan),
                    }

                # ─── Agent Conversation Events ────────────────────────────────
                agent_conversations = final_state.get("agent_conversations", {})
                current_agent_conv = final_state.get("current_agent_conversation_id")
                pending_agent_question = final_state.get("pending_agent_question")

                if agent_conversations:
                    logger.info(f"  🤖 Agent conversations: {len(agent_conversations)} active")
                    # Emit update for each conversation
                    for conv_id, conv_state in agent_conversations.items():
                        messages = conv_state.get("messages", [])
                        logger.info(f"     → {conv_state.get('agent_name', 'Unknown')}: "
                                   f"status={conv_state.get('status', '?')}, messages={len(messages)}")
                        yield {
                            "event": "agent_conversation",
                            "data": json.dumps({
                                "conversation_id": conv_id,
                                "agent_id": conv_state.get("agent_id"),
                                "agent_name": conv_state.get("agent_name"),
                                "status": conv_state.get("status"),
                                "task_summary": conv_state.get("task_summary"),
                                "message_count": conv_state.get("message_count", 0),
                                "messages": messages,  # Include actual messages for UI
                                "is_current": conv_id == current_agent_conv,
                            }),
                        }

                # If an agent is waiting for user input, emit a special event
                if pending_agent_question and current_agent_conv:
                    logger.info(f"  ❓ Agent waiting for input: {pending_agent_question[:60]}...")
                    yield {
                        "event": "agent_question",
                        "data": json.dumps({
                            "conversation_id": current_agent_conv,
                            "question": pending_agent_question,
                        }),
                    }

            except Exception as e:
                logger.warning(f"  ⚠ Could not read final state: {e}")

            # Done
            yield {
                "event": "done",
                "data": json.dumps({"session_id": session.session_id}),
            }
            logger.info(f"═══ Done: session={session.session_id}")

        except Exception as e:
            logger.error(f"  ✗ Stream error: {e}", exc_info=True)
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


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    """Get all messages for a session (for restoring chat history)."""
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"messages": session_manager.get_messages(session_id)}


@router.get("/health")
async def health():
    return {
        "status": "healthy",
        "graph_loaded": graph is not None,
    }


@router.get("/auth/config")
async def auth_config():
    """Return the main app URL so the frontend can auto-authenticate."""
    return {"main_app_url": get_base_url()}


# ─── Agent Conversations ──────────────────────────────────────

@router.get("/sessions/{session_id}/agent-conversations")
async def get_agent_conversations(session_id: str):
    """Get all agent conversations for a session."""
    if graph is None:
        raise HTTPException(status_code=503, detail="Builder agent not initialized")

    try:
        thread_config = {"configurable": {"thread_id": session_id}}
        state_snapshot = graph.get_state(thread_config)
        final_state = state_snapshot.values if state_snapshot else {}

        agent_conversations = final_state.get("agent_conversations", {})
        current_conv_id = final_state.get("current_agent_conversation_id")
        pending_question = final_state.get("pending_agent_question")

        return {
            "conversations": list(agent_conversations.values()),
            "current_conversation_id": current_conv_id,
            "pending_question": pending_question,
        }
    except Exception as e:
        logger.error(f"Error getting agent conversations: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/agents")
async def list_available_agents():
    """List all available agents that can be delegated to."""
    try:
        import sys
        from pathlib import Path
        BUILDER_AGENT_DIR = Path(__file__).parent.parent.parent / "builder_agent"
        if str(BUILDER_AGENT_DIR) not in sys.path:
            sys.path.insert(0, str(BUILDER_AGENT_DIR))

        from builder_agent.registry.agent_registry import get_enabled_agents

        agents = get_enabled_agents()
        return {
            "agents": [
                {
                    "id": agent.id,
                    "name": agent.name,
                    "description": agent.description,
                    "specializations": agent.specializations,
                    "protocol": agent.protocol,
                    "enabled": agent.enabled,
                }
                for agent in agents
            ]
        }
    except Exception as e:
        logger.error(f"Error listing agents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Token Validation ──────────────────────────────────────

class TokenValidationRequest(BaseModel):
    token: str


@router.post("/auth/validate-token")
async def validate_token(request: TokenValidationRequest):
    """
    Validate a token from the main Flask app and return user context.
    This endpoint calls back to the Flask app to validate the token.
    """
    flask_base_url = get_base_url()
    validation_url = f"{flask_base_url}/api/validate-builder-token"

    logger.info(f"Validating token with Flask app at {validation_url}")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                validation_url,
                json={"token": request.token},
                headers={"Content-Type": "application/json"}
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("valid"):
                    logger.info(f"Token validated for user: {data.get('username')}")
                    return {
                        "valid": True,
                        "user": {
                            "user_id": data.get("user_id"),
                            "role": data.get("role"),
                            "tenant_id": data.get("tenant_id"),
                            "username": data.get("username"),
                            "name": data.get("name"),
                        }
                    }
                else:
                    logger.warning(f"Token validation failed: {data.get('error', 'unknown')}")
                    return {"valid": False, "error": data.get("error", "Invalid token")}
            else:
                logger.warning(f"Token validation request failed with status {response.status_code}")
                return {"valid": False, "error": "Validation request failed"}

    except httpx.TimeoutException:
        logger.error("Timeout while validating token with Flask app")
        return {"valid": False, "error": "Validation timeout"}
    except httpx.RequestError as e:
        logger.error(f"Error connecting to Flask app for token validation: {e}")
        return {"valid": False, "error": "Could not connect to main app"}
    except Exception as e:
        logger.error(f"Unexpected error during token validation: {e}")
        return {"valid": False, "error": "Validation error"}


@router.post("/sessions/with-user")
async def create_session_with_user(token: str, title: Optional[str] = "New Chat"):
    """
    Create a new session with user context from a validated token.
    This combines token validation and session creation in one call.
    """
    # First validate the token
    flask_base_url = get_base_url()
    validation_url = f"{flask_base_url}/api/validate-builder-token"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                validation_url,
                json={"token": token},
                headers={"Content-Type": "application/json"}
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("valid"):
                    # Create user context
                    user_context = UserContext(
                        user_id=data.get("user_id"),
                        role=data.get("role"),
                        tenant_id=data.get("tenant_id"),
                        username=data.get("username"),
                        name=data.get("name"),
                    )

                    # Create session with user context
                    session = session_manager.create_session(title=title, user_context=user_context)
                    logger.info(f"Created authenticated session {session.session_id} for user {user_context.username}")
                    return session.to_dict()
                else:
                    raise HTTPException(status_code=401, detail=data.get("error", "Invalid token"))
            else:
                raise HTTPException(status_code=401, detail="Token validation failed")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating session with user: {e}")
        raise HTTPException(status_code=500, detail="Could not create session")
