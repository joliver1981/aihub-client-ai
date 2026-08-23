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
from integration_tools import INTEGRATION_TOOLS
from file_tools import FILE_TOOLS
from document_tools import DOCUMENT_TOOLS
from portal_tools import PORTAL_TOOLS

from claude_agent_sdk import (
    ClaudeAgentOptions, query, create_sdk_mcp_server,
    AssistantMessage, SystemMessage, ResultMessage,
)
try:  # tool results ride on UserMessage blocks; import defensively across SDK versions
    from claude_agent_sdk import UserMessage, ToolResultBlock
except ImportError:  # pragma: no cover
    UserMessage = ToolResultBlock = None

# ---------------------------------------------------------------------------
# Per-session in-flight registry (deferred-results-to-chat, 2026-08-22).
# A scheduled/delayed run may RESUME the chat session it was asked from, which
# makes two writers on one SDK transcript possible. run_turn marks the session
# it is driving; main.py /api/run resumes only an IDLE session (else it falls
# back to the old fresh-session + My Work FYI), and /api/chat waits (bounded)
# for an in-flight deferred run on the same conversation. Counter-based so
# nested mark/clear pairs are safe; stale marks self-expire (crash safety).
# ---------------------------------------------------------------------------
import time as _time

_INFLIGHT: dict = {}          # session_id -> [count, last_mark_monotonic]
INFLIGHT_STALE_SECONDS = int(os.getenv("AGENT_INFLIGHT_STALE_SECONDS", "7200"))


def mark_inflight(session_id: Optional[str]) -> None:
    if not session_id:
        return
    ent = _INFLIGHT.setdefault(session_id, [0, 0.0])
    ent[0] += 1
    ent[1] = _time.monotonic()


def clear_inflight(session_id: Optional[str]) -> None:
    if not session_id:
        return
    ent = _INFLIGHT.get(session_id)
    if not ent:
        return
    ent[0] -= 1
    if ent[0] <= 0:
        _INFLIGHT.pop(session_id, None)


def is_inflight(session_id: Optional[str]) -> bool:
    if not session_id:
        return False
    ent = _INFLIGHT.get(session_id)
    if not ent or ent[0] <= 0:
        return False
    if _time.monotonic() - ent[1] > INFLIGHT_STALE_SECONDS:
        _INFLIGHT.pop(session_id, None)   # a crashed turn must never wedge a chat
        return False
    return True

# Document tools are additive and reversible: flip AGENT_DOCUMENT_TOOLS=false to
# ship without them (reverts to the pre-tool, automation-only ingest behavior).
_DOCUMENT_TOOLS_ON = os.getenv("AGENT_DOCUMENT_TOOLS", "true").lower() == "true"

# Portal tools bridge the SAME Browser Use machinery CC's portal tools drive.
# Two switches: AGENT_PORTAL_TOOLS (agent-local kill switch) and the platform's
# BROWSER_USE_ENABLED (default true — the same env CC's nodes.py honors). With
# the browser service disabled platform-wide the tools unregister, so the agent
# honestly reports no portal capability instead of erroring against a dead port.
_PORTAL_TOOLS_ON = (os.getenv("AGENT_PORTAL_TOOLS", "true").lower() == "true"
                    and os.getenv("BROWSER_USE_ENABLED", "true").lower() == "true")

aihub_server = create_sdk_mcp_server(
    name="aihub", version="0.6.0",
    tools=AIHUB_TOOLS + AUTHORING_TOOLS + WORK_TOOLS + VIEWS_TOOLS
          + INTEGRATION_TOOLS + FILE_TOOLS
          + (DOCUMENT_TOOLS if _DOCUMENT_TOOLS_ON else [])
          + (PORTAL_TOOLS if _PORTAL_TOOLS_ON else []))

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
    "save_view", "delete_view", "rename_view", "store_platform_secret",
    "schedule_view_refresh", "schedule_view_email",
    "draft_email_reply", "setup_agent_email",
    "execute_integration_operation", "assign_integration_groups",
    "import_documents",
    "portal_fetch", "save_portal", "run_portal_workflow",
    "schedule_portal_workflow", "cancel_portal_workflow_schedule",
})

# Tool inputs are streamed to the UI (chip click-to-peek) and would otherwise
# display a pasted credential — redact sensitive fields at the event seam.
# The SDK transcript already holds the user's own paste; this keeps OUR
# surfaces from re-displaying it.
SENSITIVE_TOOL_FIELDS = {
    "store_platform_secret": ("value",),
    "save_portal": ("password", "totp"),
    "portal_fetch": ("password", "totp"),
}

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
    "check_automation_run", "get_automation", "list_code_flows",
    "get_code_flow", "list_my_work", "list_skills",
    "list_saved_views", "get_view", "list_secret_names",
    "get_agent_email_status", "list_integrations", "get_integration_operations",
    "list_server_files", "search_documents", "list_documents", "get_document",
    "query_document_records", "read_file",
    "lookup_portal", "list_portal_workflows", "describe_portal_workflow",
]
_READ_ALLOWED = [f"mcp__aihub__{n}" for n in _READ_TOOL_NAMES]

SYSTEM_PROMPT = """You are The Agent — AI Hub's assistant. You help people explore
their data, get honest answers, and turn repeatable work into automations that run
deterministically on schedules with humans in the loop.

WHAT YOU CAN DO
- Explore: list connections, inspect schemas, run read-only probe queries, ask
  data agents questions, list playbooks and run history.
- Sign into web portals with a real browser (RPA) to download or upload files —
  ad-hoc, from saved portals, or as recorded portal workflows.
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
TIME AND TIMEZONE: every turn begins with a "[Context: now … (zone)]" line —
the current wall-clock time in the USER'S timezone (their browser) and that
zone's name. Use it for all time arithmetic ("in 20 minutes", "tomorrow 9am",
"end of day"); every time the user says is in that zone unless they name
another; state every time back to them in that zone (never raw UTC).
One-shot delayed actions ("check my email in 2 minutes", "follow up in an
hour"): schedule_agent_task with run_in_minutes — a headless session fires
ONCE at that time as this user; an ABSOLUTE time ("at 3pm", "tomorrow at
9am") is run_at='YYYY-MM-DD HH:MM' computed from the Context line. You
cannot sleep or wait inside a conversation turn; the scheduler is how you
defer work. BOUNDED repetition
("every 10 minutes for the next hour", "every 5 minutes, 12 times"):
schedule_agent_task with every_minutes PLUS for_minutes (or occurrences) —
ONE job the engine stops on its own; never schedule an unbounded job for a
bounded ask and never fan out one-shots. Results of deferred and scheduled
runs are appended to THIS conversation (when scheduled from a chat) and land
as an FYI in My Work — say so when you confirm, and relay the cadence/bound
facts the tool returns (first run, stop time, about how many runs).
Three ladders, pick deliberately: something to LOOK AT repeatedly (numbers,
top-N lists, a pulse) -> save a VIEW (save_view: the exact recipe you verified
is pinned; the Views screen refreshes it deterministically, zero AI per
refresh). Tiles can be frozen SELECTs or PROMOTED automations that print JSON
tile data (for scraped/API/computed sources — never use checkpoints in a tile
automation). View scopes mirror skills: private by default, group after the
user confirms which group, tenant files an admin approval. Mechanical
repetition that DOES something -> build an AUTOMATION (deterministic, zero
tokens per run). Recurring judgment ('check X each morning, flag what's odd')
-> schedule_agent_task (a headless session runs the prompt as this user
and reports into their My Work and this conversation). After an analysis the user liked, offer to
pin it as a View.
To RENAME a view use rename_view (in place, schedules follow) — never
save_view under a new name, which forks a copy. When re-saving tiles,
preserve each tile's 'layout' key: it holds the sizes/positions the user
arranged by hand on the Views screen.

FILES — YOU LIVE IN A WEB BROWSER
Your users talk to you through a web page; server filesystem paths mean
NOTHING to them. Whenever a task produces a file for the user (a SharePoint
or integration download, an automation output, an export), call
offer_file_download with the server path and include the returned markdown
link VERBATIM in your reply — the chat renders it as a working download
button. Never tell a user to fetch a file from a server path.
Those /api/files/ links ALSO work as inputs for YOUR OWN tools:
import_documents and the portal upload_file argument accept an /api/files/
link (or the "Server copies" path a portal tool returned) and resolve it to
this user's staged file. Asked about a file you just delivered? Import it via
its link, then search/query it — NEVER hunt the filesystem for it.
Files the user ATTACHES in chat arrive as an "[Attached files from the user …]"
line carrying server paths — use those paths directly with upload_file,
import_documents or list_server_files; never echo the paths back.

DOCUMENTS (import, search, answer)
You have first-class document tools — use them; do NOT hand-build an automation
or probe endpoints just to import or search files, and never mention API keys.
- When a user tells you where files are, call list_server_files on that path to
  confirm what's there. You DO have server filesystem access through this tool —
  never say you can't see files.
- To bring documents into AI Hub, call import_documents with the folder (or a
  single file). It extracts, stores, and indexes each one for search, and it is
  IDEMPOTENT — it skips files already imported from the same path, so re-running
  never duplicates. Report its per-file outcome exactly (imported / already-
  present / failed); don't claim a file imported if it didn't.
- To answer questions about imported documents, call search_documents with the
  question. It searches the WHOLE document store (semantic + field) and returns
  passages with filename and page — you do NOT need a knowledge agent, and you
  do NOT need to parse the files yourself. Cite the filename/page it returns. If
  it finds nothing, say so and offer to import the documents.
- list_documents / get_document show what's in the store — use them to verify an
  import landed or to answer "what documents do I have?".
- To just LOOK AT one specific file — a chat attachment, a file you downloaded,
  or a path the user gives — call read_file. It returns the text of ANY common
  type (TXT/CSV/JSON/Markdown/code and PDF/Word/Excel/images) without storing or
  indexing it — the fast path for "what's in this file?". Do NOT import a file
  just to read it once; import is for making many files searchable later.
- For "WHICH documents require X" / "HOW MANY documents state Y" / "list every
  requirement about Z", call query_document_records — structured rows extracted
  from repeating content (a guide's requirements, an invoice's line items), each
  citing its page and a verbatim excerpt. NEVER answer which/how-many questions
  by counting search_documents passages: passages are a relevance sample, not a
  census. Always RELAY the COVERAGE line it returns (how many documents were
  actually extracted); if it says no records exist, fall back to search_documents
  and say the answer comes from reading pages, not from a structured table.
A standing "watch a folder and ingest new files on a schedule" pipeline is still
an AUTOMATION (see the document-ingestion skill) — build that when the user wants
ongoing ingestion, but for a one-time import or any Q&A, use the tools directly.

INTEGRATIONS (SharePoint, Shopify, Stripe, external APIs)
When a request involves an external system, call list_integrations FIRST —
never assume what's connected. You see only what this user may use
(developers/admins see everything; regular users see integrations assigned
to their groups). Check get_integration_operations for exact keys/params,
then execute_integration_operation — the platform owns all auth and runs
operations server-side. Instances and their credentials are configured on
the Integrations page, never through chat. delete_* operations need the
user's explicit confirmation. Admins can share an integration with a group
via assign_integration_groups (ask which groups first).

WEB PORTALS (browser RPA — sign in, download / upload files)
YES — you can log into vendor/customer web portals with a real browser and
fetch or deliver files. When a user wants a file from (or sent to) a website
that needs a login, call lookup_portal FIRST:
- Saved portal -> portal_fetch(portal_name, task). The URL and credentials
  resolve server-side; NEVER ask the user to re-share a saved login.
- First time (ad-hoc) -> the user gives the URL and login in chat: portal_fetch
  with start_url/username/password. Act right away — don't refuse or stall.
  After a successful ad-hoc run, OFFER save_portal so the name alone works
  next time.
- Recorded portal workflows (deterministic replay of saved browser steps) ->
  list_portal_workflows / describe_portal_workflow / run_portal_workflow.
  These are DIFFERENT from the regular workflows in Playbooks.
- Information shown ON a page (a balance, a status, a list) -> portal_fetch
  with a task that says what to REPORT; relay the browser agent's reading and
  say it came from reading the page — not from a downloaded document.
- Recurring portal downloads ("every Monday pull the statement") ->
  schedule_portal_workflow on a saved workflow (deterministic headless replay;
  each run lands an FYI with download links in My Work; re-scheduling
  replaces). cancel_portal_workflow_schedule stops it (two-step confirm).
2FA / verification pauses: the tool returns a take-over LINK — relay it to the
user VERBATIM, let them finish the step, then call check_portal_run with the
run_id to collect the result. A run that hasn't finished is NOT a delivered
file; repeat exactly what the tools said (the run_id lines matter — keep them).
Downloads arrive as /api/files/ links — include them VERBATIM (FILES rules).
Uploads: pass upload_file with a server path (list_server_files helps find it).

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
YES — you can receive email. Every user gets a personal agent address (Email
screen in the rail); mail sent there reaches you as a headless session run as
them, and your results land in their My Work. When a user asks whether you can
get/receive/handle email, the answer is YES: call get_agent_email_status
FIRST and answer from their actual state — show their address and recent
activity, or if none exists OFFER TO SET IT UP yourself: propose the default
address, note they can pick a different prefix, and after they explicitly
agree call setup_agent_email with confirmed=true (never create it without
their permission).
get_agent_email_status IS your inbox view: "did you get any email?" / "any
mail?" = call it and answer from the activity it reports — directly, with NO
capability disclaimers or preambles about what you can't do. (Full message
bodies arrive only in the per-message sessions; if asked for an old email's
contents, say the activity log has sender/subject/outcome and offer to act
on future mail instead.) To send, use draft_email_reply — it honors the address's
settings: auto-send OFF (default) files an EDITABLE approval in My Work and
nothing sends until they approve; auto-send ON sends immediately and the tool
says so. Report exactly what the tool result says — "sent" only when it says
sent, otherwise "awaiting approval". Outbound can be disabled entirely; if
the tool refuses, say so and point to the Email screen.

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
    from agent_config import get_effective_model
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        model=get_effective_model(),   # admin runtime override > AGENT_MODEL
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
    # Expose the conversation id to tools (schedule_agent_task captures "the
    # chat I was asked from"). On a NEW session the id arrives with the SDK's
    # init message and is set then — the contextvar holds this same dict, so
    # tools called later in the turn see it.
    user_ctx["session_id"] = session_id or None
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
    marked = None                # session id this turn holds in-flight
    if session_id:
        mark_inflight(session_id)
        marked = session_id
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
                        user_ctx["session_id"] = sid
                        if not marked:
                            mark_inflight(sid)
                            marked = sid
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
    finally:
        if marked:
            clear_inflight(marked)
