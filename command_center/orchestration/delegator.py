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


def _derive_delegation_status(saw_error_event: bool, plan_status: Optional[str]) -> str:
    """Fail-closed status for a builder delegation (Phase 0). An in-stream error
    event or a failed plan is a failure; a partial plan is partial; everything else
    (completed / delegated / draft / skipped / None) is a successful delegation."""
    if saw_error_event or plan_status == "failed":
        return "failed"
    if plan_status == "partial":
        return "partial"
    return "completed"


async def delegate_to_agent(
    agent_id: str,
    question: str,
    user_context: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[list] = None,
    timeout: Optional[float] = None,
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
    from cc_config import get_base_url, AI_HUB_API_KEY, DELEGATION_TIMEOUT_SECONDS

    if timeout is None:
        timeout = DELEGATION_TIMEOUT_SECONDS

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

        # Include user_id in the fallback session key so two concurrent users
        # delegating to the same data agent get distinct engine cache entries
        # (prevents cross-user state mixing in _internal_engines).
        delegator_user_id = (user_context or {}).get("user_id", "anon")
        payload = {
            "agent_id": agent_id,
            "question": question,
            "session_id": session_id or f"cc-{delegator_user_id}-{agent_id}",
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
        # Pass the CC session so the route can scope any files this agent
        # produces to the shared store and hand them back as artifact handles.
        if session_id:
            payload["session_id"] = session_id
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
                # Artifact handles (large results persisted to the shared
                # store) — the by-reference channel of the artifact plan.
                if data.get("artifacts"):
                    result["artifacts"] = data["artifacts"]
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
        # AIHUB-0047: a bare float timeout made `read` an idle cap — a long
        # builder LLM call with no streamed bytes killed the delegation mid-
        # build ("network error"). The builder now pings every 15s, and the
        # read timeout is generous so only a genuinely dead stream trips it.
        _timeouts = httpx.Timeout(connect=15.0, read=max(float(timeout), 300.0),
                                  write=60.0, pool=60.0)
        async with httpx.AsyncClient(timeout=_timeouts) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                # Fail-closed: a non-2xx response never raises during streaming and would
                # otherwise be reported as a completed delegation. Detect it before
                # consuming the body as SSE. (The sibling delegate_* helpers all gate on
                # status_code == 200; this streaming path historically did not — F1.)
                if resp.status_code != 200:
                    try:
                        await resp.aread()
                        err_body = resp.text[:500]
                    except Exception:
                        err_body = ""
                    logger.error(
                        f"[delegate_to_builder] Builder /api/chat returned HTTP "
                        f"{resp.status_code}: {err_body}"
                    )
                    return {
                        "text": f"Builder returned HTTP {resp.status_code}: {err_body}",
                        "status": "failed",
                        "plan": None,
                        "builder_session_id": effective_session_id,
                    }

                token_buffer = []
                plan_data = None
                workflow_saved = None   # AIHUB-0034: persisted node read-back
                current_event = None
                saw_error_event = False

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
                        elif current_event == "workflow_saved":
                            workflow_saved = data   # AIHUB-0034: persisted node types (read-back)
                        elif current_event == "status":
                            logger.info(f"Builder status: {data.get('label', '')}")
                        elif current_event == "error":
                            error_msg = data.get("message", data.get("error", str(data)))
                            logger.error(f"Builder error event: {error_msg}")
                            token_buffer.append(f"\n\n⚠️ Builder encountered an error: {error_msg}")
                            saw_error_event = True
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

                # AIHUB-0034: PREPEND the authoritative step list built from the
                # ACTUALLY-PERSISTED nodes (the builder's read-back), so the reply
                # the CC composes cannot headline a step (e.g. an SFTP upload) that
                # is not in the saved workflow. Deterministic — not LLM-narrated.
                dropped_cap_label = None
                if workflow_saved and workflow_saved.get("node_types") is not None:
                    try:
                        from command_center.orchestration.build_reply import (
                            persisted_steps_block, dropped_capability)
                        _plan_text = " ".join(
                            str(s.get("description", "")) for s in (plan_data or {}).get("steps", []))
                        # AIHUB-0038 R2: per-node configured-ness (from the true
                        # read-back) makes coverage evidence-based — a hollow
                        # placeholder node can no longer suppress the disclosure.
                        _nodes_info = workflow_saved.get("nodes")
                        _block = persisted_steps_block(
                            workflow_saved.get("workflow_id"), workflow_saved.get("status"),
                            workflow_saved.get("node_types"), _plan_text, nodes=_nodes_info)
                        dropped_cap_label = dropped_capability(
                            workflow_saved.get("node_types"), _plan_text, nodes=_nodes_info)
                        if dropped_cap_label:
                            # The builder's own narration confabulates the dropped step
                            # as built — REPLACE it with the authoritative block so the
                            # CC has only honest data to recompose from.
                            full_response = _block
                        else:
                            full_response = _block + "\n\n---\n" + full_response
                    except Exception as _bre:
                        logger.warning(f"[delegate_to_builder] persisted-steps block failed: {_bre}")

                # Fail-closed status derivation. Historically this returned a hard-coded
                # 'completed' for any stream that didn't raise, so in-stream errors and
                # failed plans were reported as success. Derive the real outcome from the
                # signals we have: an error event, or the builder plan's own aggregated
                # status (completed/delegated/partial/skipped/failed).
                # completed / delegated / draft / skipped / None → the delegation
                # itself succeeded (a draft plan awaiting confirmation is a success).
                plan_status = (plan_data or {}).get("status")
                delegation_status = _derive_delegation_status(saw_error_event, plan_status)

                return {
                    "text": full_response,
                    "status": delegation_status,
                    "plan": plan_data,
                    "builder_session_id": effective_session_id,
                    # AIHUB-0038: expose the persisted-node read-back STRUCTURED so the
                    # CC reply layer can pin the final user-visible step list
                    # deterministically (the text alone gets rewritten by the
                    # builder_distiller LLM).
                    "workflow_saved": workflow_saved,
                    "dropped_capability": dropped_cap_label,
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
