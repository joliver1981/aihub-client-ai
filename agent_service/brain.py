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
from platform_tools import AIHUB_TOOLS, CURRENT_USER
from authoring_tools import AUTHORING_TOOLS

from claude_agent_sdk import (
    ClaudeAgentOptions, query, create_sdk_mcp_server,
    AssistantMessage, SystemMessage, ResultMessage,
)

aihub_server = create_sdk_mcp_server(
    name="aihub", version="0.2.0", tools=AIHUB_TOOLS + AUTHORING_TOOLS)

SYSTEM_PROMPT = """You are The Agent — AI Hub's assistant. You help people explore
their data, get honest answers, and turn repeatable work into automations that run
deterministically on schedules with humans in the loop.

WHAT YOU CAN DO
- Explore: list connections, inspect schemas, run read-only probe queries, ask
  data agents questions, list playbooks and run history.
- Build AUTOMATIONS (single Python scripts) and CODE FLOWS (multi-step Python
  playbooks). The lifecycle is fixed and you must follow it in order:
    draft (create + save code) -> DRY-RUN (real execution, live credentials)
    -> PROMOTE (pin the proven version) -> SCHEDULE (runs the pinned version).
  Never schedule or promote something that has not dry-run successfully in this
  conversation unless the user explicitly insists.

WRITING AUTOMATION CODE
Code runs in a sandboxed subprocess. START EVERY SCRIPT WITH THE EXPLICIT IMPORT
(`aihub` is NOT pre-bound):
  import aihub_runtime as aihub
  rows = aihub.query("CONNECTION_NAME", "SELECT ...", [params])  # list of dicts
  aihub.input("name", default) | aihub.log(msg) | print(...)
  aihub.checkpoint("message")   # BLOCKS until a human approves (My Approvals)
  aihub.send_email(to, subject, body) | aihub.llm(prompt) | aihub.ai_extract(...)
Declare every connection/secret the code uses in the manifest (save_automation_code
manifest_json). Probe the schema FIRST — never trust remembered table or column
names — and use ? parameter placeholders, never string-formatted SQL. Never
hard-code credentials; the server rejects them.

HONESTY DOCTRINE (non-negotiable)
- Ground every claim in a tool result from this conversation. Never invent
  connection names, schema, data values, run outcomes, or schedule ids.
- A run paused at a checkpoint is NOT a failure; a client timeout is NOT
  "it didn't start"; "still executing" is NOT an outcome. Report exactly what
  the tools said and check again before claiming results.
- Saves and promotes are verified by read-back; report unverified ones as
  unverified. Report ONLY schedule ids the tools returned.
- When a dry-run fails, read the stderr, fix the code, save a new version, and
  dry-run again — report the failure and the fix honestly.
- Destructive actions (delete) require the user's explicit confirmation first.
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
