"""
Command Center — SSE Chat Route
===================================
Streaming chat endpoint backed by LangGraph.
"""

import asyncio
import json
import logging
import time
import traceback
import uuid
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

from services.trace_store import TraceStore

_trace_store = TraceStore(Path(__file__).parent.parent / "data")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

_graph = None
_session_mgr = None


def init_chat_routes(graph, session_mgr):
    global _graph, _session_mgr
    _graph = graph
    _session_mgr = session_mgr


def _parse_response_blocks(ai_response: str) -> list:
    """
    Parse AI response into content blocks, handling double-encoding.
    The LLM may return:
      1. Raw markdown text
      2. JSON array of blocks: [{"type":"text","content":"..."}]
      3. Double-encoded: [{"type":"text","content":"[{\"type\":\"text\",...}]"}]
    """
    try:
        parsed = json.loads(ai_response)

        # If the response is itself a JSON string containing blocks, decode again.
        if isinstance(parsed, str):
            s = parsed.strip()
            if (s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}")):
                try:
                    parsed = json.loads(s)
                except json.JSONDecodeError:
                    pass

        if isinstance(parsed, list):
            # Check for double-encoding: single text block whose content is itself JSON blocks
            if (len(parsed) == 1 and isinstance(parsed[0], dict)
                    and parsed[0].get("type") == "text"):
                inner = parsed[0].get("content", "")
                if inner.startswith("[{") and inner.endswith("}]"):
                    try:
                        inner_blocks = json.loads(inner)
                        if isinstance(inner_blocks, list) and all(isinstance(b, dict) for b in inner_blocks):
                            return inner_blocks
                    except json.JSONDecodeError:
                        pass
            # Also check each block for double-encoded content
            unwrapped = []
            for block in parsed:
                if isinstance(block, dict) and block.get("type") == "text":
                    content = block.get("content", "")
                    if content.startswith("[{") and content.endswith("}]"):
                        try:
                            inner = json.loads(content)
                            if isinstance(inner, list):
                                unwrapped.extend(inner)
                                continue
                        except json.JSONDecodeError:
                            pass
                unwrapped.append(block)
            return unwrapped
        else:
            return [{"type": "text", "content": ai_response}]
    except json.JSONDecodeError:
        return [{"type": "text", "content": ai_response}]


@router.post("/chat")
async def chat(request: Request):
    """
    SSE streaming chat endpoint.

    Request body:
        {
            "message": "user message",
            "session_id": "optional existing session id",
            "user_context": {"user_id": 1, "role": 2, "tenant_id": 1, "username": "...", "name": "..."}
        }
    """
    _request_start = time.time()

    if not _graph:
        return {"error": "Command Center graph not initialized"}

    body = await request.json()
    user_message = body.get("message", "").strip()
    session_id = body.get("session_id")
    user_context = body.get("user_context")
    attachments = body.get("attachments")  # Optional list of file_ids

    # Ensure user_context includes role — required for builder permission checks.
    # When the CC token expires, the frontend falls back to cached user_id without
    # role, causing the builder to default to User (role 1) and deny Developer actions.
    if user_context and user_context.get("user_id") and not user_context.get("role"):
        try:
            from cc_config import get_base_url, AI_HUB_API_KEY
            import httpx
            base_url = get_base_url()
            resp = httpx.get(
                f"{base_url}/get/users",
                headers={"X-API-Key": AI_HUB_API_KEY},
                timeout=5.0,
            )
            if resp.status_code == 200:
                users = resp.json() if isinstance(resp.json(), list) else resp.json().get("users", [])
                uid = int(user_context["user_id"])
                for u in users:
                    if int(u.get("user_id", u.get("id", 0))) == uid:
                        user_context["role"] = u.get("role", u.get("role_id", 1))
                        user_context.setdefault("username", u.get("username", ""))
                        user_context.setdefault("name", u.get("name", u.get("display_name", "")))
                        logger.info(f"[chat] Resolved missing role for user {uid}: role={user_context['role']}")
                        break
        except Exception as e:
            logger.warning(f"[chat] Could not resolve user role: {e}")

    if not user_message:
        return {"error": "Message is required"}

    # Get or create session
    session = _session_mgr.get_or_create(session_id)
    session_id = session.session_id

    # Stamp the session's owner so subsequent session-list / get / delete
    # calls can enforce ownership (BUG-R3-001..007 fix). Only populates
    # when missing — we never overwrite an existing owner.
    if user_context and isinstance(user_context, dict):
        try:
            from services import UserContext as _UC
            _ctx = _UC(
                user_id=int(user_context.get("user_id") or 0),
                role=int(user_context.get("role") or 0),
                tenant_id=int(user_context.get("tenant_id") or 0),
                username=str(user_context.get("username") or ""),
                name=str(user_context.get("name") or ""),
            )
            _session_mgr.attach_user_context_if_missing(session_id, _ctx)
        except Exception as _own_err:
            logger.warning(f"[chat] failed to stamp session owner: {_own_err}")

    # Handle file attachments — associate with session and append context.
    # Ownership is enforced inside build_attachment_context: files owned by
    # a different user are silently skipped (BUG-R3-005 fix).
    if attachments and isinstance(attachments, list):
        try:
            from routes.upload import associate_files_to_session, build_attachment_context
            associate_files_to_session(attachments, session_id)
            _req_uid = (user_context or {}).get("user_id")
            _req_tid = (user_context or {}).get("tenant_id")
            _req_role = int((user_context or {}).get("role") or 0)
            attachment_ctx = build_attachment_context(
                attachments,
                user_message=user_message,
                user_id=int(_req_uid) if _req_uid is not None else None,
                tenant_id=int(_req_tid) if _req_tid is not None else None,
                role=_req_role,
            )
            if attachment_ctx:
                user_message = user_message + attachment_ctx
                logger.info(f"[chat] {len(attachments)} file(s) attached to session {session_id}")
        except Exception as e:
            logger.warning(f"[chat] Failed to process attachments: {e}")

    # Store user message with compact attachment reference (not full extracted text)
    stored_msg = body.get("message", "").strip()
    if attachments and isinstance(attachments, list):
        try:
            from routes.upload import get_attachment_refs
            refs = get_attachment_refs(attachments)
            if refs:
                stored_msg = stored_msg + "\n" + refs
        except Exception:
            pass
    _session_mgr.add_message(session_id, "user", stored_msg)

    # Auto-title on first message (session still has "New Chat")
    session_title = session.title
    if session_title == "New Chat" and user_message:
        # Use first ~50 chars of user message as title
        auto_title = user_message[:50].strip()
        if len(user_message) > 50:
            # Break at word boundary
            auto_title = auto_title.rsplit(' ', 1)[0] + '…'
        # Strip HTML tags and control chars before storing (defense-in-depth for XSS)
        import re
        auto_title = re.sub(r"<[^>]*>", "", auto_title)
        auto_title = re.sub(r"[\x00-\x1f\x7f]", "", auto_title).strip() or "New Chat"
        _session_mgr.update_title(session_id, auto_title)
        session_title = auto_title

    # ── Start execution trace (file-based) ─────────────────────────
    trace_meta = None
    try:
        user_id = (user_context or {}).get("user_id")
        trace_meta = _trace_store.start_trace(
            user_id=user_id,
            session_id=session_id,
            user_message=user_message,
            user_context=user_context,
            system_prompts=None,
        )
    except Exception as _:
        trace_meta = None

    async def event_stream():
        try:
            # Trace id (for Inspector UI)
            if trace_meta is not None:
                yield _sse_event("trace", {"trace_id": trace_meta.trace_id})
                _trace_store.log_event(trace_meta, event_type="sse", node="/api/chat", summary="trace_id sent")

            # Send session info (including auto-generated title)
            yield _sse_event("session", {"session_id": session_id, "title": session_title})

            # Load conversation history for context continuity
            from langchain_core.messages import AIMessage as _AIMessage
            history_msgs = []
            try:
                raw_history = _session_mgr.get_messages(session_id, limit=20)
                # Skip the last message (the current user message we just added)
                for entry in raw_history[:-1]:
                    role = entry.get("role", "")
                    content = entry.get("content", "")
                    if role == "user":
                        history_msgs.append(HumanMessage(content=content))
                    elif role == "assistant":
                        history_msgs.append(_AIMessage(content=content))
            except Exception as e:
                logger.warning(f"Could not load history: {e}")

            # Load session-level state (active delegation, preferences, etc.)
            session_state = _session_mgr.get_session_state(session_id)

            # ── Token-safe history: truncate old messages to stay within limits ──
            # Rough estimate: 1 token ≈ 4 chars. Target max ~150K tokens of history.
            MAX_HISTORY_CHARS = 600_000  # ~150K tokens
            total_chars = sum(len(m.content) for m in history_msgs) + len(user_message)
            if total_chars > MAX_HISTORY_CHARS:
                logger.warning(f"[chat] History too large ({total_chars} chars / ~{total_chars//4} tokens). Trimming oldest messages.")
                # Keep the most recent messages, drop oldest until under limit
                trimmed = []
                running = len(user_message)
                for msg in reversed(history_msgs):
                    msg_len = len(msg.content)
                    if running + msg_len > MAX_HISTORY_CHARS:
                        break
                    trimmed.insert(0, msg)
                    running += msg_len
                dropped = len(history_msgs) - len(trimmed)
                if dropped > 0:
                    logger.info(f"[chat] Dropped {dropped} oldest messages to fit within {MAX_HISTORY_CHARS} chars")
                history_msgs = trimmed

            # Build graph input with full conversation context
            all_messages = history_msgs + [HumanMessage(content=user_message)]
            graph_input = {
                "messages": all_messages,
                "session_id": session_id,
            }
            if user_context:
                graph_input["user_context"] = user_context
            # Restore active delegation from session state
            if session_state.get("active_delegation"):
                graph_input["active_delegation"] = session_state["active_delegation"]
            # Restore session resources (persistent resource awareness)
            if session_state.get("session_resources"):
                graph_input["session_resources"] = session_state["session_resources"]

            # ── Load user preferences (Layer 2: cross-session, simple DB read) ─
            user_memory_context = ""
            try:
                user_id = (user_context or {}).get("user_id")
                if user_id:
                    from command_center.memory.user_memory import get_preferences
                    prefs = get_preferences(int(user_id))
                    if prefs:
                        lines = []
                        for key, val in prefs.items():
                            display = val.get("value", str(val)) if isinstance(val, dict) else str(val)
                            lines.append(f"- {key}: {display}")
                        user_memory_context = "Your preferences:\n" + "\n".join(lines)
            except Exception as mem_err:
                logger.warning(f"User preference load failed (non-blocking): {mem_err}")

            # ── Load session insights (Layer 3: cross-session discovered knowledge) ─
            try:
                user_id = (user_context or {}).get("user_id")
                if user_id:
                    from command_center.memory.route_memory import get_insights_for_context
                    insights_context = get_insights_for_context(int(user_id), limit=10)
                    if insights_context:
                        if user_memory_context:
                            user_memory_context += "\n\n" + insights_context
                        else:
                            user_memory_context = insights_context
            except Exception as insight_err:
                logger.warning(f"Insight load failed (non-blocking): {insight_err}")

            graph_input["user_memory"] = user_memory_context

            if trace_meta is not None:
                _trace_store.log_event(
                    trace_meta,
                    event_type="graph_input",
                    node="graph",
                    payload={
                        "history_len": len(all_messages),
                        "has_user_context": bool(user_context),
                        "active_delegation_present": bool(session_state.get("active_delegation")),
                    },
                )

            config = {"configurable": {"thread_id": session_id}}

            yield _sse_event("status", {"phase": "thinking", "message": "Analyzing your request..."})
            if trace_meta is not None:
                _trace_store.log_event(trace_meta, event_type="status", node="/api/chat", payload={"phase": "thinking"})

            # Quick landscape scan to show what we're working with
            try:
                from command_center.orchestration.landscape_scanner import scan_platform
                landscape = await scan_platform()
                n_agents = len(landscape.get("agents", [])) + len(landscape.get("data_agents", []))
                n_conns = len(landscape.get("connections", []))
                if n_agents > 0:
                    yield _sse_event("status", {
                        "phase": "scanning",
                        "message": f"Scanning platform: {n_agents} agents, {n_conns} connections found..."
                    })
                    if trace_meta is not None:
                        _trace_store.log_event(trace_meta, event_type="landscape", node="scan_platform", payload={"agents": n_agents, "connections": n_conns})
            except Exception as _scan_err:
                if trace_meta is not None:
                    _trace_store.log_event(trace_meta, event_type="landscape_error", node="scan_platform", level="warning", payload={"error": str(_scan_err)})

            yield _sse_event("status", {"phase": "processing", "message": "Processing with AI..."})
            if trace_meta is not None:
                _trace_store.log_event(trace_meta, event_type="status", node="/api/chat", payload={"phase": "processing"})

            # Attach trace info so graph nodes/routers can log per-step events
            if trace_meta is not None:
                graph_input["trace"] = {
                    "trace_id": trace_meta.trace_id,
                    "user_id": trace_meta.user_id,
                    "session_id": trace_meta.session_id,
                    "user_message": trace_meta.user_message,
                    "created_at": trace_meta.created_at,
                }

            # Run the graph with concurrent progress event streaming.
            # ainvoke() runs as an asyncio task while we poll a shared queue
            # for real-time progress events emitted by graph nodes.
            from graph.progress import register_queue, cleanup_queue
            _progress_queue = register_queue(session_id)
            try:
                _invoke_task = asyncio.create_task(
                    _graph.ainvoke(graph_input, config=config)
                )
                while not _invoke_task.done():
                    _pev = await _progress_queue.get(timeout=0.3)
                    if _pev is not None:
                        yield _sse_event("status", _pev["data"])
                final_state = await _invoke_task
                # Drain any remaining queued events
                while True:
                    _pev = await _progress_queue.get(timeout=0.1)
                    if _pev is None:
                        break
                    yield _sse_event("status", _pev["data"])
            finally:
                cleanup_queue(session_id)

            if trace_meta is not None:
                try:
                    _trace_store.log_event(
                        trace_meta,
                        event_type="graph_done",
                        node="graph",
                        payload={
                            "final_state_keys": list(final_state.keys()),
                            "intent": final_state.get("intent"),
                        },
                    )
                except Exception:
                    pass

            # Extract the last AI message
            messages = final_state.get("messages", [])
            ai_response = ""
            for msg in reversed(messages):
                if hasattr(msg, 'type') and msg.type == 'ai':
                    ai_response = msg.content
                    break
                elif hasattr(msg, 'role') and msg.role == 'assistant':
                    ai_response = msg.content
                    break

            # Persist session-level state (active delegation, etc.)
            new_state = {}
            active_deleg = final_state.get("active_delegation")
            intent = final_state.get("intent", "chat")
            logger.info(f"[chat] Final state keys: {list(final_state.keys())}")
            logger.info(f"[chat] Final state active_delegation: {type(active_deleg).__name__} = {str(active_deleg)[:200] if active_deleg else 'None'}")
            logger.info(f"[chat] Final state intent: {intent}")
            if active_deleg:
                new_state["active_delegation"] = active_deleg
            else:
                new_state["active_delegation"] = None
            # Persist session resources (what was built in this session)
            session_res = final_state.get("session_resources")
            if session_res:
                new_state["session_resources"] = session_res
            _session_mgr.save_session_state(session_id, new_state)
            logger.info(f"[chat] Saved session state for {session_id}")

            # ── Log route to Route Memory (non-blocking) ──
            try:
                user_id = (user_context or {}).get("user_id")
                if user_id:
                    from cc_config import USE_ROUTE_MEMORY
                    if USE_ROUTE_MEMORY:
                        from command_center.memory.route_memory import log_route, CC_TRACKABLE_TOOLS

                        # ── Decide what to log ──
                        # Pass the raw user message + recent conversation
                        # transcript to log_route. Inside log_route a mini-LLM
                        # picks the substantive question to log — this
                        # correctly handles reroute instructions ("use X
                        # instead"), refinements, and clarifications where
                        # the raw latest message is NOT the right question.
                        # If no transcript is available (first turn), the
                        # raw message is logged directly.
                        from graph.nodes import _format_conversation_for_prompt
                        conversation_transcript = _format_conversation_for_prompt(
                            messages, max_turns=5, exclude_latest=True
                        )

                        agent_id_log = active_deleg.get("agent_id") if active_deleg else None
                        agent_name_log = active_deleg.get("agent_name") if active_deleg else None
                        latency_ms = int((time.time() - _request_start) * 1000)
                        route_path = f"classify_intent->{intent}"
                        if agent_id_log:
                            route_path += f"->agent_{agent_id_log}"

                        # ── Extract CC tool name from conversation ──
                        # When the converse node uses CC-native tools (search_documents,
                        # export_data, etc.), capture the tool name so route memory can
                        # learn CC tool routes (not just agent delegations).
                        cc_tool_name_log = None
                        if not agent_id_log and intent == "chat":
                            for msg in reversed(messages):
                                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                                    for tc in msg.tool_calls:
                                        tc_name = tc.get("name", "")
                                        if tc_name in CC_TRACKABLE_TOOLS:
                                            cc_tool_name_log = tc_name
                                            route_path += f"->tool:{tc_name}"
                                            break
                                    if cc_tool_name_log:
                                        break

                        asyncio.ensure_future(log_route(
                            user_id=int(user_id),
                            query_text=user_message,
                            intent=intent,
                            agent_id=agent_id_log,
                            agent_name=agent_name_log,
                            route_path=route_path,
                            latency_ms=latency_ms,
                            response_text=ai_response or None,
                            cc_tool_name=cc_tool_name_log,
                            conversation_transcript=conversation_transcript,
                        ))

                    # ── Session Insight Extraction (non-blocking) ──
                    # After multi-turn conversations, extract factual discoveries
                    # and store them for future sessions.
                    from cc_config import USE_SESSION_INSIGHTS
                    if USE_SESSION_INSIGHTS:
                        from command_center.memory.route_memory import extract_session_insights
                        asyncio.ensure_future(extract_session_insights(
                            user_id=int(user_id),
                            session_id=session_id,
                            conversation_messages=messages,
                        ))
            except Exception as mem_err:
                logger.warning(f"Route logging failed (non-blocking): {mem_err}")

            if ai_response:
                # Store AI response
                _session_mgr.add_message(session_id, "assistant", ai_response)

                # Try to parse as JSON blocks — handle double-encoding
                blocks = _parse_response_blocks(ai_response)

                # Mask credentials/secrets that may have leaked into the
                # user-facing response text (defense-in-depth for BUG-R2-015).
                # QUOTE class matches plain quotes (", ', `) OR JSON-escaped ones (\", \', \`)
                # so that patterns hit both "password \"value\"" (raw LLM JSON) and
                # "Password: `value`" (markdown-formatted plan preview).
                import re as _rrm
                _QC = r"""(?:\\"|\\'|\\`|["'`])?"""
                _secret_patterns_resp = [
                    (_rrm.compile(r"(\*{0,3}password\*{0,3}\s*[:=]\s*\*{0,3}\s*" + _QC + r")([A-Za-z0-9_@.!#$%+\-]{3,})" + _QC, _rrm.IGNORECASE), r"\1***"),
                    (_rrm.compile(r"(\"password\"\s*:\s*\")([^\"]+)(\")", _rrm.IGNORECASE), r"\1***\3"),
                    (_rrm.compile(r"(\bpassword\s+" + _QC + r")([A-Za-z0-9_@.!#$%+\-]{6,})" + _QC, _rrm.IGNORECASE), r"\1***"),
                    (_rrm.compile(r"(\*{0,3}api[_-]?key\*{0,3}\s*[:=]\s*\*{0,3}\s*" + _QC + r")([A-Za-z0-9_@.!#$%+\-]{3,})" + _QC, _rrm.IGNORECASE), r"\1***"),
                    (_rrm.compile(r"(\"api[_-]?key\"\s*:\s*\")([^\"]+)(\")", _rrm.IGNORECASE), r"\1***\3"),
                    (_rrm.compile(r"(\*{0,3}secret\*{0,3}\s*[:=]\s*\*{0,3}\s*" + _QC + r")([A-Za-z0-9_@.!#$%+\-]{3,})" + _QC, _rrm.IGNORECASE), r"\1***"),
                    (_rrm.compile(r"(\*{0,3}token\*{0,3}\s*[:=]\s*\*{0,3}\s*" + _QC + r")([A-Za-z0-9_@.!#$%+\-]{3,})" + _QC, _rrm.IGNORECASE), r"\1***"),
                ]
                def _mask_resp(v):
                    if not isinstance(v, str):
                        return v
                    for pat, repl in _secret_patterns_resp:
                        v = pat.sub(repl, v)
                    return v
                if isinstance(blocks, list):
                    for _b in blocks:
                        if isinstance(_b, dict) and isinstance(_b.get("content"), str):
                            _b["content"] = _mask_resp(_b["content"])

                resp_payload = {"blocks": blocks, "session_id": session_id}
                if trace_meta is not None:
                    resp_payload["trace_id"] = trace_meta.trace_id
                yield _sse_event("response", resp_payload)

                if trace_meta is not None:
                    _trace_store.log_event(
                        trace_meta,
                        event_type="response",
                        node="/api/chat",
                        payload={"blocks_count": len(blocks)},
                    )

            # Send sub-task info if any
            sub_tasks = final_state.get("sub_tasks", [])
            if sub_tasks:
                yield _sse_event("tasks", {
                    "tasks": [
                        {"id": t.get("id"), "description": t.get("description"),
                         "agent": t.get("target_agent_name", t.get("target_agent")),
                         "status": t.get("status")}
                        for t in sub_tasks
                    ]
                })

            # Send builder conversation log if present (for Task Progress panel).
            # Mask any literal passwords/secrets that may have been echoed in the log.
            if active_deleg and active_deleg.get("builder_log"):
                import re as _re
                # The regexes below handle:
                #   password: value       password=value
                #   **password:** value   **password:**\nvalue
                #   password `value`      password "value"
                #   password value        (plain prose — 6+ char value to limit false positives)
                #   "password": "value"   (JSON)
                # QUOTE class matches plain quotes (", ', `) OR JSON-escaped (\", \', \`)
                # so that patterns hit both the raw LLM JSON and markdown-formatted text.
                _QC2 = r"""(?:\\"|\\'|\\`|["'`])?"""
                _secret_patterns = [
                    # Markdown-aware "Password: value" / "Password=value"
                    (_re.compile(r"(\*{0,3}password\*{0,3}\s*[:=]\s*\*{0,3}\s*" + _QC2 + r")([A-Za-z0-9_@.!#$%+\-]{3,})" + _QC2, _re.IGNORECASE), r"\1***"),
                    # JSON "password": "value"
                    (_re.compile(r"(\"password\"\s*:\s*\")([^\"]+)(\")", _re.IGNORECASE), r"\1***\3"),
                    # Plain-prose "password value" (6+ chars to avoid matching "password" in text)
                    (_re.compile(r"(\bpassword\s+" + _QC2 + r")([A-Za-z0-9_@.!#$%+\-]{6,})" + _QC2, _re.IGNORECASE), r"\1***"),
                    # api_key / apikey / api-key
                    (_re.compile(r"(\*{0,3}api[_-]?key\*{0,3}\s*[:=]\s*\*{0,3}\s*" + _QC2 + r")([A-Za-z0-9_@.!#$%+\-]{3,})" + _QC2, _re.IGNORECASE), r"\1***"),
                    (_re.compile(r"(\"api[_-]?key\"\s*:\s*\")([^\"]+)(\")", _re.IGNORECASE), r"\1***\3"),
                    # secret/token as key-value
                    (_re.compile(r"(\*{0,3}secret\*{0,3}\s*[:=]\s*\*{0,3}\s*" + _QC2 + r")([A-Za-z0-9_@.!#$%+\-]{3,})" + _QC2, _re.IGNORECASE), r"\1***"),
                    (_re.compile(r"(\*{0,3}token\*{0,3}\s*[:=]\s*\*{0,3}\s*" + _QC2 + r")([A-Za-z0-9_@.!#$%+\-]{3,})" + _QC2, _re.IGNORECASE), r"\1***"),
                ]
                def _mask(v):
                    if not isinstance(v, str):
                        return v
                    for pat, repl in _secret_patterns:
                        v = pat.sub(repl, v)
                    return v
                masked_log = []
                for entry in active_deleg["builder_log"]:
                    me = dict(entry) if isinstance(entry, dict) else entry
                    if isinstance(me, dict) and isinstance(me.get("content"), str):
                        me["content"] = _mask(me["content"])
                    masked_log.append(me)
                yield _sse_event("builder_log", {
                    "log": masked_log,
                    "builder_session_id": active_deleg.get("builder_session_id"),
                })

            yield _sse_event("done", {"session_id": session_id})

        except Exception as e:
            logger.error(f"Chat error: {e}\n{traceback.format_exc()}")
            if trace_meta is not None:
                _trace_store.log_event(
                    trace_meta,
                    event_type="error",
                    node="/api/chat",
                    level="error",
                    payload={"message": str(e), "traceback": traceback.format_exc()},
                )
            yield _sse_event("error", {"message": str(e)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_event(event_type: str, data: dict) -> str:
    """Format an SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
