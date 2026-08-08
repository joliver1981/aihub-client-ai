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
import re
from typing import AsyncIterator, Optional

from agent_config import (
    AGENT_MODEL, AGENT_MAX_TURNS, CLAUDE_CONFIG_DIR, WORKSPACE_DIR,
    ensure_anthropic_key, logger,
)
from platform_tools import AIHUB_TOOLS, CURRENT_USER
from authoring_tools import AUTHORING_TOOLS
from work_tools import WORK_TOOLS
from views_tools import VIEWS_TOOLS

from claude_agent_sdk import (
    ClaudeAgentOptions, query, create_sdk_mcp_server,
    AssistantMessage, SystemMessage, ResultMessage,
)
try:  # tool results ride on UserMessage blocks; import defensively across SDK versions
    from claude_agent_sdk import UserMessage, ToolResultBlock
except ImportError:  # pragma: no cover
    UserMessage = ToolResultBlock = None

aihub_server = create_sdk_mcp_server(
    name="aihub", version="0.4.0",
    tools=AIHUB_TOOLS + AUTHORING_TOOLS + WORK_TOOLS + VIEWS_TOOLS)

# Mutation-claim guard (port of CC nodes.py _claims_completed_mutation,
# AIHUB-0048 F1): a reply asserting a JUST-COMPLETED change is only honest when
# a mutating tool actually succeeded this turn. Deterministic fail-closed
# regex — tuned to first-person/checkmarked completion claims so recaps and
# honest failure reports don't false-positive.
MUTATING_TOOLS = frozenset({
    "create_automation", "save_automation_code", "promote_automation",
    "schedule_automation", "delete_automation", "decide_automation_checkpoint",
    "run_automation", "dry_run_automation",
    "create_code_flow", "add_code_step", "wire_steps", "schedule_code_flow",
    "run_code_flow", "dry_run_code_flow",
    "raise_work_item", "save_skill", "schedule_agent_task",
    "save_view", "delete_view", "store_platform_secret",
    "schedule_view_refresh", "draft_email_reply",
})

# Tool inputs are streamed to the UI (chip click-to-peek) and would otherwise
# display a pasted credential — redact sensitive fields at the event seam.
# The SDK transcript already holds the user's own paste; this keeps OUR
# surfaces from re-displaying it.
SENSITIVE_TOOL_FIELDS = {"store_platform_secret": ("value",)}

MUTATION_CLAIM_RE = re.compile(
    r"(✅\s*(created|saved|scheduled|promoted|deleted|inserted|added|updated|wired))"
    r"|(\bI(?:'|’)?ve\s+(?:now\s+)?(created|saved|scheduled|promoted|deleted|"
    r"added|updated|wired)\b[^.\n]{0,80}\b(automation|code\s*flow|workflow|"
    r"schedule|job|skill|work\s*item|playbook|step|checkpoint|view|secret)\b)"
    r"|(\b(?:is|are)\s+now\s+(?:live|scheduled|promoted|running\s+on\s+a\s+schedule)\b)",
    re.I)


def claims_completed_mutation(text: str) -> bool:
    return bool(MUTATION_CLAIM_RE.search(text or ""))


# Side-threads on work items run READ-ONLY: the thread answers questions with
# evidence; it never mutates. Anything consequential goes through the item's
# own action buttons or the main Assistant.
_READ_TOOL_NAMES = [
    "list_data_connections", "get_connection_schema", "probe_connection_query",
    "ask_data_agent", "list_playbooks", "list_recent_runs",
    "check_automation_run", "get_automation", "get_code_flow", "list_my_work",
    "list_saved_views", "list_secret_names",
]
_READ_ALLOWED = [f"mcp__aihub__{n}" for n in _READ_TOOL_NAMES]

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

SKILLS — YOUR PROCEDURAL MEMORY
When you solve something non-obvious (a process, a data model's quirks, a
client convention), SAVE IT as a skill (save_skill) so future sessions start
from know-how instead of rediscovery. Default scope is the user's private one;
share to a group only after they confirm; tenant-wide sharing files an admin
approval into My Work. Skills record procedure and gotchas — but always verify
current facts (schema, values) with discovery tools; never trust a skill's
frozen facts over a live probe. Loaded skills appear to you automatically when
relevant.

RECURRING WORK
Three ladders, pick deliberately: something to LOOK AT repeatedly (numbers,
top-N lists, a pulse) -> save a VIEW (save_view: the exact recipe you verified
is pinned; the Views screen refreshes it deterministically, zero AI per
refresh). Tiles can be frozen SELECTs or PROMOTED automations that print JSON
tile data (for scraped/API/computed sources — never use checkpoints in a tile
automation). View scopes mirror skills: private by default, group after the
user confirms which group, tenant files an admin approval. Mechanical
repetition that DOES something -> build an AUTOMATION (deterministic, zero
tokens per run). Recurring judgment ('check X each morning, flag what's odd')
-> schedule_agent_task (a fresh headless session runs the prompt as this user
and reports into their My Work). After an analysis the user liked, offer to
pin it as a View.

SECRETS AND CREDENTIALS
When a user hands you an API key, password, or token in chat, store it
IMMEDIATELY with store_platform_secret (check list_secret_names first — it may
already exist). From then on refer to it ONLY by its UPPER_SNAKE_CASE name:
automations declare it in the manifest and the server injects the value at run
time (hard-coded credentials are rejected). Never echo a secret back, in full
or in part, and never write one into automation code, a skill, a work item, or
a View. If they'd rather not paste it in chat at all, point them to Settings ->
Local Secrets and agree on the name.

EMAIL
Users can have a personal agent address; mail sent there becomes a headless
session run as them (you may be in one now — the prompt says so). To send any
email, use draft_email_reply: it files an EDITABLE approval into the user's
My Work, and only their approval sends it, from their agent address. You can
never send directly. Never say an email "was sent" — say a draft is awaiting
their approval in My Work.

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


def build_options(session_id: Optional[str] = None,
                  tool_scope: str = "full",
                  cwd: Optional[str] = None) -> ClaudeAgentOptions:
    ensure_anthropic_key()
    allowed = (_READ_ALLOWED if tool_scope == "read" else ["mcp__aihub__*"])
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        model=AGENT_MODEL,
        tools=["Skill"],            # ONLY the Skill loader — no Bash/Read/Write
        mcp_servers={"aihub": aihub_server},
        allowed_tools=allowed + ["Skill"],
        permission_mode="dontAsk",
        setting_sources=["project"],  # load skills from the session workspace
        skills="all",
        max_turns=AGENT_MAX_TURNS,
        cwd=cwd or WORKSPACE_DIR,
        env={"CLAUDE_CONFIG_DIR": CLAUDE_CONFIG_DIR},
        resume=session_id or None,
        stderr=lambda line: logger.debug(f"[sdk] {line}"),
    )


async def run_turn(prompt: str, session_id: Optional[str],
                   user_ctx: dict, tool_scope: str = "full") -> AsyncIterator[dict]:
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
    # Mount this user's skills view: product + tenant + their groups + private.
    uid = int(user_ctx.get("user_id") or 0)
    try:
        import readthrough
        import skills_mount
        ws = skills_mount.build_user_workspace(uid, readthrough.user_group_ids(uid))
    except Exception as e:
        logger.warning(f"skills mount failed (continuing without): {e}")
        ws = None
    new_session_id = session_id
    all_text = []                # for the mutation-claim guard
    tool_names = {}              # tool_use_id -> tool name
    mutation_succeeded = False
    try:
        async for message in query(prompt=prompt,
                                   options=build_options(session_id, tool_scope,
                                                         cwd=ws)):
            if isinstance(message, SystemMessage):
                if getattr(message, "subtype", "") == "init":
                    sid = (getattr(message, "data", {}) or {}).get("session_id")
                    if sid:
                        new_session_id = sid
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if hasattr(block, "text") and getattr(block, "text", None):
                        all_text.append(block.text)
                        yield {"type": "text", "text": block.text}
                    elif hasattr(block, "name"):
                        bid = getattr(block, "id", "") or ""
                        bname = getattr(block, "name", "?")
                        short = bname.replace("mcp__aihub__", "")
                        tool_names[bid] = short
                        tool_input = dict(getattr(block, "input", {}) or {})
                        for field in SENSITIVE_TOOL_FIELDS.get(short, ()):
                            if field in tool_input:
                                tool_input[field] = "•••redacted•••"
                        yield {"type": "tool", "id": bid, "name": bname,
                               "input": tool_input}
            elif UserMessage is not None and isinstance(message, UserMessage):
                # Tool results: surface completion + honest ok/failed per call
                for block in (getattr(message, "content", None) or []):
                    if ToolResultBlock is not None and isinstance(block, ToolResultBlock):
                        parts = getattr(block, "content", None)
                        preview = ""
                        if isinstance(parts, str):
                            preview = parts
                        elif isinstance(parts, list):
                            preview = " ".join(
                                p.get("text", "") if isinstance(p, dict)
                                else str(getattr(p, "text", "")) for p in parts)
                        rid = getattr(block, "tool_use_id", "") or ""
                        ok = not bool(getattr(block, "is_error", False))
                        if ok and tool_names.get(rid) in MUTATING_TOOLS:
                            mutation_succeeded = True
                        yield {"type": "tool_result", "id": rid, "ok": ok,
                               "preview": preview.strip()[:600]}
            elif isinstance(message, ResultMessage):
                new_session_id = getattr(message, "session_id", None) or new_session_id
                subtype = getattr(message, "subtype", "")
                ok = subtype == "success"
                cost = getattr(message, "total_cost_usd", None)
                # AIHUB-0048-class guard: claimed change without a successful
                # mutating tool this turn — flag it, loudly and deterministically.
                if claims_completed_mutation("\n".join(all_text)) and not mutation_succeeded:
                    warning = ("This reply claims a completed change, but no "
                               "mutating tool succeeded in this turn — treat the "
                               "claim as UNVERIFIED and ask the agent to show the "
                               "tool evidence.")
                    logger.warning(f"MUTATION-CLAIM GUARD user="
                                   f"{user_ctx.get('username')} session="
                                   f"{new_session_id}: {warning}")
                    yield {"type": "guard", "warning": warning}
                logger.info(f"turn done user={user_ctx.get('username')} "
                            f"session={new_session_id} subtype={subtype} cost={cost}")
                yield {"type": "result", "session_id": new_session_id,
                       "ok": ok, "subtype": subtype, "cost_usd": cost}
    except Exception as e:
        # query() raises after yielding an error result; surface honestly.
        logger.error(f"turn error user={user_ctx.get('username')} "
                     f"session={new_session_id}: {e}")
        yield {"type": "error", "error": str(e), "session_id": new_session_id}
