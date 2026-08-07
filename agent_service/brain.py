"""
The Agent's brain — Claude Agent SDK wiring.

A0 design: stateless service, disk-persisted sessions. Each chat turn runs
one `query(prompt, options(resume=session_id))`; the SDK persists the session
under data/agent/claude/ and returns the session id on the ResultMessage,
which the UI carries to the next turn. No in-memory client lifecycle to leak.

Lockdown posture (A0, read-only):
- tools=[] removes every built-in tool (no Bash/Read/Write on the server)
- only the in-process aihub MCP tools are exposed and pre-approved
- permission_mode="dontAsk" denies anything not pre-approved
- setting_sources=[] so nothing from ~/.claude on the host leaks in
"""

import json
import os
from typing import AsyncIterator, Optional

from agent_config import (
    AGENT_MODEL, AGENT_MAX_TURNS, CLAUDE_CONFIG_DIR, WORKSPACE_DIR,
    ensure_anthropic_key, logger,
)
from platform_tools import aihub_server, CURRENT_USER

from claude_agent_sdk import (
    ClaudeAgentOptions, query,
    AssistantMessage, SystemMessage, ResultMessage,
)

SYSTEM_PROMPT = """You are The Agent — AI Hub's assistant. You help people explore
their data, understand what exists in their AI Hub platform, and get honest answers.

This is your read-only preview release. You can: list data connections, inspect
schemas, run small read-only probe queries, ask configured data agents questions,
and list playbooks (workflows/automations) and their run history. You cannot yet
build or change anything — when asked to, say so plainly and describe what you
WOULD do once authoring is enabled.

Operating rules:
- Ground every claim in a tool result from this conversation. Never invent
  connection names, table names, column names, or data values.
- Probe before you answer: check schema before writing SQL; verify filter values
  when a query returns 0 rows.
- Report failures and empty results honestly and specifically. Never imply an
  action happened when it did not.
- Be concise and readable. Lead with the answer, then the evidence.
"""


def build_options(session_id: Optional[str] = None) -> ClaudeAgentOptions:
    ensure_anthropic_key()
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        model=AGENT_MODEL,
        tools=[],                                   # remove ALL built-ins
        mcp_servers={"aihub": aihub_server},
        allowed_tools=["mcp__aihub__*"],
        permission_mode="dontAsk",
        setting_sources=[],                         # no host-user settings bleed
        max_turns=AGENT_MAX_TURNS,
        cwd=WORKSPACE_DIR,
        env={"CLAUDE_CONFIG_DIR": CLAUDE_CONFIG_DIR},
        resume=session_id or None,
        stderr=lambda line: logger.debug(f"[sdk] {line}"),
    )


async def run_turn(prompt: str, session_id: Optional[str],
                   user_ctx: dict) -> AsyncIterator[dict]:
    """
    Run one conversation turn; yield UI events:
      {"type": "text", "text": ...}
      {"type": "tool", "name": ..., "input": {...}}
      {"type": "result", "session_id": ..., "ok": bool, "subtype": ..., "cost_usd": ...}
      {"type": "error", "error": ...}
    """
    CURRENT_USER.set(user_ctx)
    logger.info(f"turn start user={user_ctx.get('username')} "
                f"session={session_id or '(new)'} prompt={prompt[:200]!r}")
    new_session_id = session_id
    try:
        async for message in query(prompt=prompt, options=build_options(session_id)):
            if isinstance(message, SystemMessage):
                if getattr(message, "subtype", "") == "init":
                    sid = (getattr(message, "data", {}) or {}).get("session_id")
                    if sid:
                        new_session_id = sid
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if hasattr(block, "text") and getattr(block, "text", None):
                        yield {"type": "text", "text": block.text}
                    elif hasattr(block, "name"):
                        yield {"type": "tool", "name": getattr(block, "name", "?"),
                               "input": getattr(block, "input", {}) or {}}
            elif isinstance(message, ResultMessage):
                new_session_id = getattr(message, "session_id", None) or new_session_id
                subtype = getattr(message, "subtype", "")
                ok = subtype == "success"
                cost = getattr(message, "total_cost_usd", None)
                logger.info(f"turn done user={user_ctx.get('username')} "
                            f"session={new_session_id} subtype={subtype} cost={cost}")
                yield {"type": "result", "session_id": new_session_id,
                       "ok": ok, "subtype": subtype, "cost_usd": cost}
    except Exception as e:
        # query() raises after yielding an error result; surface honestly.
        logger.error(f"turn error user={user_ctx.get('username')} "
                     f"session={new_session_id}: {e}")
        yield {"type": "error", "error": str(e), "session_id": new_session_id}
