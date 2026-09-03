"""
The Agent's My Connections tools — the user's OWN personal accounts
(Microsoft 365 / Outlook mail + calendar through the in-app Graph server,
and any other per-user OAuth MCP server an admin publishes on
/my-connections) exposed conversationally, AS the signed-in user.

These are thin wrappers over the main app's internal seam
(/api/internal/my-connections*, builder_mcp/routes/my_connections_internal_routes.py)
— the same shape as integration_tools.py over /api/internal/integrations*.
Credentials never appear here: the platform owns the OAuth tokens, refreshes
them, and runs the tool server-side on a gateway connection keyed to THIS
user (server_id + user_id), so one user's token can never serve another.

Identity: CURRENT_USER (the per-turn envelope from the verified JWT) — never
anything the model wrote. Every seam call carries a signed X-AIHub-User
assertion (shared_auth.sign_user_assertion), exactly like document_tools;
the seam refuses calls without one. The contextvar default is the service
principal (user_id 0) and must never mint an assertion.

THREE DIFFERENT "CONNECTIONS" — say the right one:
  list_data_connections  = databases (tenant-wide)
  list_integrations      = platform integrations (SharePoint, Shopify... tenant-wide)
  list_my_connections    = the USER'S OWN accounts (personal, per user)
And the agent-email tools (list_my_email / get_agent_email_status) cover the
AI HUB AGENT MAILBOX only — never the user's Outlook. "What's in my inbox?"
about the user's real mail goes through use_my_connection.

WRITE GATE — DECIDED (James, 2026-09-02): writes through a personal
connection are OFF by default. Reads stay default-open (the standing
denylist-over-allowlist rule); writes are the deliberate carve-out because
Graph send_email lands in the USER'S Sent Items under their name,
indistinguishable and unrecallable, and The Agent already has its own
identifiable mailbox.
  - AGENT_MY_CONNECTIONS_WRITE_TOOLS: comma-separated EXACT tool names
    permitted to mutate (e.g. "send_email"). Empty (default) = read-only.
  - A tool is usable when it is (a) listed there, or (b) declared read-only
    by its server (MCP annotations.readOnlyHint = true). Anything else is
    DENIED — fail closed. We never guess from a name whether something
    writes; an undeclared tool is unrecognized, not inferred.
  - ONE shared guard (tool_permission) — get_connection_tools hides what it
    denies and use_my_connection refuses it; they cannot drift apart.
  - Denials steer: the text tells the model to offer The Agent's own
    mailbox (send_email / draft_email_reply) instead of a bare refusal.
  - Headless (scheduled / email-triggered) sessions: READS are allowed —
    "summarize my inbox each morning" is the daily-routine use case this
    exists for. WRITES additionally need AGENT_MY_CONNECTIONS_HEADLESS_WRITES=true
    (default false): "The Agent sent mail as me at 3am from a schedule I
    forgot about" is the failure to prevent.

SERVER DENYLIST: AGENT_MY_CONNECTIONS_DENY = comma-separated server ids
The Agent must not use even when the user authorized them. Starts empty;
grows by observation (denylist, not allowlist).

Kill switch: AGENT_MY_CONNECTIONS (default true) — brain.py registers
CONNECTION_TOOLS only when on; off = exactly today's behaviour.

Honesty: tool bodies never raise into the loop; empty catalogs, "not
authorized yet", server rejections and gateway errors all come back as
readable text; results are previewed at MAX_RESULT_CHARS with honest
truncation notes.
"""

import json
import os
from typing import Any, Optional

import httpx

from claude_agent_sdk import tool

from agent_config import AI_HUB_API_KEY, get_base_url, logger
from platform_tools import CURRENT_USER, _text, _unwrap

MAX_RESULT_CHARS = 2500
MY_CONNECTIONS_PAGE = "/my-connections"
SEAM = "/api/internal/my-connections"

DENIED_TEXT = (
    "'{name}' is DENIED on this install: it is not declared read-only by its "
    "server and is not listed in AGENT_MY_CONNECTIONS_WRITE_TOOLS. Writes "
    "through a user's personal account are OFF by default (an admin can allow "
    "this exact tool name in that setting on The Agent service and restart it). "
    "Do NOT claim it was done. For email, offer to send from The Agent's OWN "
    "address instead (send_email / draft_email_reply) — never from the user's "
    "account."
)
HEADLESS_WRITE_TEXT = (
    "'{name}' is a WRITE through the user's personal account and this is a "
    "scheduled / email-triggered session: writes are interactive-only unless "
    "AGENT_MY_CONNECTIONS_HEADLESS_WRITES=true is set on The Agent service. "
    "Nothing was sent. Report this honestly; reads still work here."
)


# ---------------------------------------------------------------------------
# Policy (read at call time so ops/tests can flip env without a reload)
# ---------------------------------------------------------------------------

def _csv(name: str) -> list:
    raw = os.getenv(name, "") or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


def write_tools_allowed() -> set:
    """Exact tool names permitted to mutate through a personal connection."""
    return set(_csv("AGENT_MY_CONNECTIONS_WRITE_TOOLS"))


def write_allowed(tool_name: str) -> bool:
    """Exact-match only — never a pattern, never case-folded."""
    return str(tool_name or "") in write_tools_allowed()


def denied_servers() -> set:
    out = set()
    for p in _csv("AGENT_MY_CONNECTIONS_DENY"):
        try:
            out.add(int(p))
        except ValueError:
            logger.warning(f"AGENT_MY_CONNECTIONS_DENY: ignoring non-numeric '{p}'")
    return out


def headless_writes_allowed() -> bool:
    return (os.getenv("AGENT_MY_CONNECTIONS_HEADLESS_WRITES", "false")
            .lower() == "true")


def is_headless(user: Optional[dict] = None) -> bool:
    user = user if user is not None else (CURRENT_USER.get() or {})
    return str(user.get("mode") or "") == "headless"


def is_read_only(tool: dict) -> bool:
    ann = (tool or {}).get("annotations") or {}
    return ann.get("readOnlyHint") is True


def tool_permission(tool: dict, user: Optional[dict] = None) -> tuple:
    """THE shared guard: (permitted: bool, reason_if_denied: str, is_write: bool).

    permitted when the tool is exactly listed as an allowed write (and, in a
    headless session, headless writes are on), or declares itself read-only.
    Everything else is denied — fail closed.
    """
    name = str((tool or {}).get("name") or "")
    if write_allowed(name):
        if is_headless(user) and not headless_writes_allowed():
            return False, HEADLESS_WRITE_TEXT.format(name=name), True
        return True, "", True
    if is_read_only(tool):
        return True, "", False
    return False, DENIED_TEXT.format(name=name), True


def split_tools(tools: list, user: Optional[dict] = None) -> tuple:
    """(usable, denied) — each a list of tool dicts; denied tools carry
    '_reason'."""
    usable, denied = [], []
    for t in tools or []:
        ok, reason, is_write = tool_permission(t, user)
        entry = dict(t)
        entry["_is_write"] = is_write
        if ok:
            usable.append(entry)
        else:
            entry["_reason"] = reason
            denied.append(entry)
    return usable, denied


# ---------------------------------------------------------------------------
# HTTP (service key + signed user assertion)
# ---------------------------------------------------------------------------

def _identity() -> tuple:
    """(user dict, uid) — uid 0 means no real user (service principal)."""
    user = CURRENT_USER.get() or {}
    try:
        uid = int(user.get("user_id") or 0)
    except (TypeError, ValueError):
        uid = 0
    return user, uid


def _headers(user: dict) -> dict:
    h = {"X-API-Key": AI_HUB_API_KEY, "Connection": "close"}
    uid = user.get("user_id")
    if uid not in (None, "", 0, "0"):
        import shared_auth
        h["X-AIHub-User"] = shared_auth.sign_user_assertion(
            uid, user.get("tenant_id"), user.get("role"))
    return h


async def _get(path: str, user: dict) -> tuple:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=90.0)) as client:
            r = await client.get(f"{get_base_url()}{path}", headers=_headers(user))
            try:
                return _unwrap(r.json()), r.status_code
            except Exception:
                return {"message": (r.text or "")[:500]}, r.status_code
    except Exception as e:
        return {"message": f"main app unreachable: {e}"}, 0


async def _post(path: str, body: dict, user: dict, timeout: float = 120.0) -> tuple:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=timeout)) as client:
            r = await client.post(f"{get_base_url()}{path}", json=body,
                                  headers=_headers(user))
            try:
                return _unwrap(r.json()), r.status_code
            except Exception:
                return {"message": (r.text or "")[:500]}, r.status_code
    except Exception as e:
        return {"message": f"main app unreachable: {e}"}, 0


def _seam_error(data, status: int, what: str) -> str:
    msg = (data.get("message") if isinstance(data, dict) else None) or str(data)[:300]
    if status == 401:
        return (f"{what} refused: the platform did not accept this session's "
                f"identity ({msg}). Nothing was read.")
    if status == 0:
        return f"{what} failed: {msg}"
    return f"{what} failed (HTTP {status}): {msg}"


_NO_USER = ("No signed-in user identity on this turn, so personal connections "
            "cannot be used (they act AS a specific user).")


def _fmt_params(schema: dict) -> str:
    props = (schema or {}).get("properties") or {}
    req = set((schema or {}).get("required") or [])
    parts = []
    for k, v in props.items():
        typ = (v or {}).get("type") if isinstance(v, dict) else None
        parts.append(f"{k}{'*' if k in req else ''}" + (f":{typ}" if typ else ""))
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool(
    "list_my_connections",
    "List the signed-in user's OWN personal accounts connected on My "
    "Connections (their Microsoft 365 / Outlook mailbox and calendar, etc.) "
    "and the ones they COULD connect but have not yet. These are PERSONAL "
    "(per user) — different from list_data_connections (databases) and "
    "list_integrations (tenant-wide platform integrations) — and different "
    "from the agent-email tools, which cover only the AI Hub AGENT mailbox. "
    "Call this FIRST whenever the user asks about THEIR inbox, their "
    "calendar, or 'my email/Outlook/Microsoft 365'. Never assume what is "
    "connected. A connection they have not authorized cannot be used until "
    "they connect it themselves at /my-connections.",
    {},
)
async def list_my_connections(args: dict[str, Any]) -> dict[str, Any]:
    user, uid = _identity()
    if not uid:
        return _text(_NO_USER, is_error=True)
    data, status = await _get(SEAM, user)
    if status != 200 or not isinstance(data, dict) or data.get("status") != "success":
        return _text(_seam_error(data, status, "Listing personal connections"),
                     is_error=True)
    deny = denied_servers()
    conns = [c for c in (data.get("connections") or [])
             if int(c.get("server_id") or 0) not in deny]
    if not conns:
        return _text("No personal connections are published on this install (an "
                     "admin publishes OAuth servers to users on the MCP Servers "
                     "page), so the user's own Outlook / Microsoft 365 mail is NOT "
                     "reachable from here. The agent-email tools cover only the AI "
                     "Hub agent mailbox — say so plainly rather than answering "
                     "from the wrong inbox.")
    lines, ready = [], 0
    for c in conns:
        sid = c.get("server_id")
        name = c.get("name")
        scope = str(c.get("scope") or "").strip()
        if c.get("connected"):
            ready += 1
            when = c.get("last_connected")
            lines.append(f"- id {sid} — {name} — CONNECTED"
                         + (f" (authorized {str(when)[:10]})" if when else "")
                         + (f"; scope: {scope}" if scope else ""))
        else:
            lines.append(f"- id {sid} — {name} — NOT CONNECTED: the user must "
                         f"connect it at {MY_CONNECTIONS_PAGE} (My Connections in "
                         "the rail) before it can be used"
                         + (f"; scope: {scope}" if scope else ""))
    head = (f"Personal connections for {user.get('username') or 'this user'} "
            f"({ready} of {len(conns)} connected):\n")
    tail = ("\nNext: get_connection_tools(server_id) to see what a connected "
            "account can do, then use_my_connection to act. Reads only unless "
            "an admin has allowed specific write tools.")
    return _text(head + "\n".join(lines) + tail)


@tool(
    "get_connection_tools",
    "List the tools one of the user's personal connections offers (e.g. "
    "list_recent_emails, list_upcoming_meetings, get_my_profile on a "
    "Microsoft 365 connection) with their parameters. Check this before "
    "executing — never guess a tool name or its parameters. Only tools this "
    "install permits are listed (writes such as sending mail from the user's "
    "account are off by default; the tool says so and what to offer instead).",
    {
        "type": "object",
        "properties": {"server_id": {"type": "integer",
                                     "description": "From list_my_connections"}},
        "required": ["server_id"],
        "additionalProperties": False,
    },
)
async def get_connection_tools(args: dict[str, Any]) -> dict[str, Any]:
    user, uid = _identity()
    if not uid:
        return _text(_NO_USER, is_error=True)
    try:
        sid = int(args["server_id"])
    except (KeyError, TypeError, ValueError):
        return _text("server_id must be a number (from list_my_connections).",
                     is_error=True)
    if sid in denied_servers():
        return _text(f"Personal connection {sid} is not available to The Agent on "
                     "this install (AGENT_MY_CONNECTIONS_DENY).", is_error=True)
    data, status = await _get(f"{SEAM}/{sid}/tools", user)
    if status == 404:
        return _text(f"No personal connection with id {sid} is published to this "
                     "user — list_my_connections shows what exists.", is_error=True)
    if status != 200 or not isinstance(data, dict):
        return _text(_seam_error(data, status, "Listing connection tools"),
                     is_error=True)
    if data.get("status") == "needs_authorization":
        return _text(data.get("message") or "The user has not authorized this "
                     f"connection — they must connect it at {MY_CONNECTIONS_PAGE}.")
    if data.get("status") != "success":
        return _text(f"Connection tools unavailable: "
                     f"{data.get('message') or data}", is_error=True)
    tools = data.get("tools") or []
    if not tools:
        return _text(f"'{data.get('name')}' reports no tools right now.")
    usable, denied = split_tools(tools, user)
    lines = [f"Tools on '{data.get('name')}' (id {sid}) usable as "
             f"{user.get('username') or 'this user'}:"]
    for t in usable:
        params = _fmt_params(t.get("inputSchema") or {})
        kind = "WRITE" if t.get("_is_write") else "read"
        desc = str(t.get("description") or "").strip()
        lines.append(f"- {t.get('name')} [{kind}] — {desc}"
                     + (f" (params: {params})" if params else ""))
    if not usable:
        lines.append("- (none usable on this install)")
    lines.append("(* = required parameter; call use_my_connection with the exact "
                 "tool name and a JSON object of parameters)")
    if denied:
        names = ", ".join(str(t.get("name")) for t in denied)
        lines.append(f"NOT available here ({len(denied)}): {names} — writes "
                     "through the user's personal account are off on this install "
                     "(admin setting AGENT_MY_CONNECTIONS_WRITE_TOOLS). For email, "
                     "offer to send from The Agent's own address (send_email) "
                     "instead; never claim to have sent from the user's account.")
    return _text("\n".join(lines))


@tool(
    "use_my_connection",
    "Execute ONE tool on one of the user's personal connections, AS that user "
    "(e.g. list_recent_emails on their Microsoft 365 connection to read THEIR "
    "Outlook inbox, list_upcoming_meetings for THEIR calendar). Call "
    "get_connection_tools first for the exact tool name and parameters. Reads "
    "are allowed; writes (sending mail from the user's account, etc.) are "
    "refused unless an admin allowed that exact tool — when refused, offer "
    "The Agent's own mailbox instead and never claim the action happened. "
    "Large results are previewed truncated — report counts honestly and never "
    "invent content beyond the preview. The result is the user's PRIVATE "
    "data: answer them, don't echo more than they asked for.",
    {
        "type": "object",
        "properties": {
            "server_id": {"type": "integer", "description": "From list_my_connections"},
            "tool_name": {"type": "string", "description": "Exact tool name"},
            "arguments_json": {"type": "string",
                               "description": "JSON object of parameters (optional)"},
        },
        "required": ["server_id", "tool_name"],
        "additionalProperties": False,
    },
)
async def use_my_connection(args: dict[str, Any]) -> dict[str, Any]:
    user, uid = _identity()
    if not uid:
        return _text(_NO_USER, is_error=True)
    try:
        sid = int(args["server_id"])
    except (KeyError, TypeError, ValueError):
        return _text("server_id must be a number (from list_my_connections).",
                     is_error=True)
    tool_name = str(args.get("tool_name") or "").strip()
    if not tool_name:
        return _text("tool_name is required (see get_connection_tools).", is_error=True)
    if sid in denied_servers():
        return _text(f"Personal connection {sid} is not available to The Agent on "
                     "this install (AGENT_MY_CONNECTIONS_DENY).", is_error=True)
    params: dict = {}
    raw = args.get("arguments_json")
    if raw not in (None, ""):
        try:
            params = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            return _text("arguments_json is not a JSON object.", is_error=True)
        if not isinstance(params, dict):
            return _text("arguments_json must be a JSON object.", is_error=True)

    # The permission check needs the server's own declaration for this tool
    # (annotations), so resolve the tool from the live list — this also
    # confirms the name exists and the connection is authorized.
    data, status = await _get(f"{SEAM}/{sid}/tools", user)
    if status == 404:
        return _text(f"No personal connection with id {sid} is published to this "
                     "user — list_my_connections shows what exists.", is_error=True)
    if status != 200 or not isinstance(data, dict):
        return _text(_seam_error(data, status, "Resolving the connection"), is_error=True)
    if data.get("status") == "needs_authorization":
        return _text(data.get("message") or "The user has not authorized this "
                     f"connection — they must connect it at {MY_CONNECTIONS_PAGE}.")
    if data.get("status") != "success":
        return _text(f"Connection unavailable: {data.get('message') or data}",
                     is_error=True)
    tools = data.get("tools") or []
    hit = next((t for t in tools if str(t.get("name")) == tool_name), None)
    if hit is None:
        names = ", ".join(str(t.get("name")) for t in tools) or "(none)"
        return _text(f"'{data.get('name')}' has no tool named '{tool_name}'. "
                     f"Its tools: {names}. Names are exact.", is_error=True)
    ok, reason, is_write = tool_permission(hit, user)
    if not ok:
        return _text(reason, is_error=True)

    body = {"tool_name": tool_name, "arguments": params,
            "context": {"source": "the_agent", "user_id": uid,
                        "username": str(user.get("username") or "")}}
    res, status = await _post(f"{SEAM}/{sid}/call", body, user, timeout=180.0)
    if status != 200 or not isinstance(res, dict):
        return _text(_seam_error(res, status, f"'{tool_name}'"), is_error=True)
    if res.get("status") == "needs_authorization":
        return _text(res.get("message") or "The user must (re)connect this account "
                     f"at {MY_CONNECTIONS_PAGE}.")
    if res.get("status") != "success":
        return _text(f"'{tool_name}' on '{data.get('name')}' FAILED: "
                     f"{res.get('message') or res}. Report the failure; do not "
                     "claim a result.", is_error=True)
    raw_out = res.get("result")
    text = raw_out if isinstance(raw_out, str) else json.dumps(raw_out, default=str)
    preview = text[:MAX_RESULT_CHARS]
    suffix = (f"\n…(truncated — {len(text)} chars total; report only what you "
              "can see, and say the list is partial)"
              if len(text) > MAX_RESULT_CHARS else "")
    label = ("WRITE done through the user's personal account"
             if is_write else "Read from the user's personal account")
    return _text(f"{label} '{data.get('name')}' — {tool_name} as "
                 f"{user.get('username') or 'this user'}:\n{preview}{suffix}")


CONNECTION_TOOLS = [list_my_connections, get_connection_tools, use_my_connection]
