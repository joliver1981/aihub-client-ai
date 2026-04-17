"""
Command Center — Agent Delegator
===================================
Delegates tasks to existing agents (assistants, data agents,
builder agent, MCP agents, workflows) through their respective endpoints.
"""

import json
import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


async def delegate_to_agent(
    agent_id: str,
    question: str,
    user_context: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[list] = None,
    timeout: float = 240.0,
    is_data_agent: bool = True,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Delegate a question to an existing agent via the main app API.

    For data agents, uses the internal Data Explorer endpoint which
    maintains a persistent engine cache (avoids engine re-initialization).
    For general agents, uses the standard /api/agents/{id}/chat endpoint.
    
    Args:
        agent_id: The agent to delegate to
        question: The user's question/prompt
        user_context: Optional user info
        conversation_history: Prior exchanges with this agent [{role, content}]
        timeout: HTTP timeout in seconds
        is_data_agent: If True, use the internal Data Explorer endpoint
        session_id: CC session ID for engine cache reuse
    """
    from cc_config import get_base_url, AI_HUB_API_KEY

    base_url = get_base_url()
    headers = {
        "X-API-Key": AI_HUB_API_KEY,
        "Content-Type": "application/json",
        "Connection": "close",
    }

    if is_data_agent:
        # Use the internal Data Explorer endpoint — persistent engine, no re-init
        url = f"{base_url}/data_explorer/internal/query"

        history = []
        if conversation_history:
            for entry in conversation_history:
                role = entry.get("role", "")
                content = entry.get("content", "")
                if role == "user":
                    history.append({"role": "Q", "content": content})
                elif role == "assistant":
                    history.append({"role": "A", "content": content})

        payload = {
            "agent_id": agent_id,
            "question": question,
            "session_id": session_id or f"cc-{agent_id}",
            "history": history,
        }
        logger.info(f"[delegate_to_agent] Data agent {agent_id} via internal endpoint")
    else:
        # General agents — standard API endpoint
        url = f"{base_url}/api/agents/{agent_id}/chat"

        history = []
        if conversation_history:
            for entry in conversation_history:
                role = entry.get("role", "")
                content = entry.get("content", "")
                if role == "user":
                    history.append({"role": "Q", "content": content})
                elif role == "assistant":
                    history.append({"role": "A", "content": content})

        payload = {"prompt": question, "history": str(history) if history else "[]"}
        logger.info(f"[delegate_to_agent] General agent {agent_id} via standard API")

    if user_context:
        payload["user_id"] = user_context.get("user_id")

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)

            if resp.status_code == 200:
                data = resp.json()
                result = {
                    "text": data.get("response", data.get("answer", str(data))),
                    "status": "completed",
                    "raw": data,
                }
                # Pass through rich content (charts, tables, etc.) from data agents
                if data.get("rich_content"):
                    result["rich_content"] = data["rich_content"]
                if data.get("query"):
                    result["query"] = data["query"]
                if data.get("answer_type"):
                    result["answer_type"] = data["answer_type"]
                return result
            else:
                return {
                    "text": f"Agent returned status {resp.status_code}: {resp.text[:500]}",
                    "status": "failed",
                }

    except httpx.TimeoutException:
        return {"text": f"Agent {agent_id} timed out after {timeout}s", "status": "failed"}
    except Exception as e:
        logger.error(f"Delegation to agent {agent_id} failed: {e}")
        return {"text": f"Delegation failed: {str(e)}", "status": "failed"}


async def delegate_to_builder(
    message: str,
    session_id: Optional[str] = None,
    user_context: Optional[Dict[str, Any]] = None,
    timeout: float = 120.0,
    builder_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Delegate to the Builder Agent service for platform mutations.
    
    Args:
        message: The message to send to the builder
        session_id: CC session ID (used to generate builder session if needed)
        user_context: User context for permissions
        timeout: HTTP timeout
        builder_session_id: Persistent builder session ID for multi-turn conversations.
                           If provided, the builder will maintain conversation history via
                           LangGraph checkpointing. Pass the same ID across all turns.
    """
    from cc_config import get_builder_api_base_url, AI_HUB_API_KEY

    base_url = get_builder_api_base_url()
    headers = {
        "X-API-Key": AI_HUB_API_KEY,
        "Content-Type": "application/json",
        "Connection": "close",
    }

    # If no builder_session_id provided, create a real builder session first.
    # The builder's SessionManager ignores unknown IDs and creates a fresh UUID,
    # so we must call POST /api/sessions to get a builder-recognized session.
    effective_session_id = builder_session_id
    if not effective_session_id:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{base_url}/api/sessions", json={}, headers=headers
                )
                if resp.status_code == 200:
                    data = resp.json()
                    effective_session_id = data.get("session_id")
                    logger.info(f"[delegate_to_builder] Created builder session: {effective_session_id}")
                else:
                    logger.warning(f"[delegate_to_builder] POST /api/sessions returned {resp.status_code}, falling back to cc session_id")
                    effective_session_id = session_id
        except Exception as e:
            logger.warning(f"[delegate_to_builder] Failed to create builder session: {e}, falling back to cc session_id")
            effective_session_id = session_id

    url = f"{base_url}/api/chat"
    payload = {"message": message}
    if effective_session_id:
        payload["session_id"] = effective_session_id
    if user_context:
        payload["user_context"] = user_context

    logger.info(f"[delegate_to_builder] session={effective_session_id}, message={message[:80]}...")

    try:
        # Builder uses SSE (sse_starlette format):
        #   event: status\ndata: {"phase":..., "label":...}
        #   event: token\ndata: {"text": "..."}
        #   event: plan\ndata: {...}
        #   event: done\ndata: {"session_id":...}
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                token_buffer = []
                plan_data = None
                current_event = None

                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        current_event = None
                        continue
                    if line.startswith("event:"):
                        current_event = line[6:].strip()
                    elif line.startswith("data:"):
                        raw = line[5:].strip()
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        if current_event == "token":
                            token_buffer.append(data.get("text", ""))
                        elif current_event == "plan":
                            plan_data = data
                        elif current_event == "status":
                            logger.info(f"Builder status: {data.get('label', '')}")
                        elif current_event == "error":
                            error_msg = data.get("message", data.get("error", str(data)))
                            logger.error(f"Builder error event: {error_msg}")
                            token_buffer.append(f"\n\n⚠️ Builder encountered an error: {error_msg}")
                        elif current_event == "done":
                            logger.info(f"Builder done event: {data}")
                            # Prefer the session_id from the done event (safety net)
                            if data.get("session_id"):
                                effective_session_id = data["session_id"]
                                logger.info(f"[delegate_to_builder] Updated session from done event: {effective_session_id}")

                full_response = "".join(token_buffer)

                # If we got a plan, summarize it
                if plan_data and not full_response:
                    steps = plan_data.get("steps", [])
                    step_lines = []
                    for i, s in enumerate(steps, 1):
                        status_icon = "✅" if s.get("status") == "completed" else "⏳"
                        step_lines.append(f"{status_icon} Step {i}: {s.get('description', 'N/A')}")
                    full_response = (
                        f"**Builder Agent Plan** ({plan_data.get('status', 'unknown')})\n\n"
                        + "\n".join(step_lines)
                    )

                if not full_response:
                    full_response = "Builder Agent processed the request but returned no visible output."

                return {
                    "text": full_response,
                    "status": "completed",
                    "plan": plan_data,
                    "builder_session_id": effective_session_id,
                }

    except Exception as e:
        logger.error(f"Builder delegation failed: {e}")
        return {"text": f"Builder delegation failed: {str(e)}", "status": "failed"}


async def delegate_to_mcp_tool(
    server_id: int,
    tool_name: str,
    arguments: Dict[str, Any],
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """Delegate to an MCP tool via the MCP gateway."""
    from cc_config import get_mcp_gateway_api_base_url, AI_HUB_API_KEY

    base_url = get_mcp_gateway_api_base_url()
    url = f"{base_url}/api/tools/{server_id}/call"
    headers = {
        "X-API-Key": AI_HUB_API_KEY,
        "Content-Type": "application/json",
        "Connection": "close",
    }

    payload = {"tool_name": tool_name, "arguments": arguments}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return {"text": str(data.get("result", data)), "status": "completed", "raw": data}
            else:
                return {"text": f"MCP tool returned {resp.status_code}", "status": "failed"}

    except Exception as e:
        logger.error(f"MCP delegation failed: {e}")
        return {"text": f"MCP delegation failed: {str(e)}", "status": "failed"}


async def execute_workflow(
    workflow_id: str,
    inputs: Optional[Dict[str, Any]] = None,
    timeout: float = 120.0,
) -> Dict[str, Any]:
    """Execute a workflow via the executor service."""
    from cc_config import get_executor_api_base_url, AI_HUB_API_KEY

    base_url = get_executor_api_base_url()
    url = f"{base_url}/api/workflows/{workflow_id}/execute"
    headers = {
        "X-API-Key": AI_HUB_API_KEY,
        "Content-Type": "application/json",
        "Connection": "close",
    }

    payload = inputs or {}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return {"text": str(data), "status": "completed", "raw": data}
            else:
                return {"text": f"Workflow returned {resp.status_code}", "status": "failed"}

    except Exception as e:
        logger.error(f"Workflow execution failed: {e}")
        return {"text": f"Workflow execution failed: {str(e)}", "status": "failed"}
