"""
Microsoft Graph tool definitions — shared between the two MCP server hosts:

  - builder_mcp/servers/graph_stdio_server.py     (stdio subprocess; dev/test rig)
  - builder_mcp/routes/mcp_internal_routes.py     (in-process Flask endpoint; production)

Each handler takes (args, get_token) where get_token is a zero-arg callable
returning a Graph bearer token. The two callers each supply their own get_token —
the stdio server fetches via oauth_manager.get_access_token(server_id); the
in-process endpoint pulls the token from the incoming Authorization header
(which the MCP gateway populated via auth_headers built from MCPServerCredentials).
"""
from datetime import datetime, timedelta, timezone

import requests

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _graph(get_token, method, path, json_body=None, params=None):
    token = get_token()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    url = path if path.startswith("http") else f"{GRAPH_BASE}{path}"
    resp = requests.request(method, url, headers=headers, json=json_body,
                            params=params, timeout=30)
    if not resp.ok:
        raise RuntimeError(
            f"Graph {method} {path} failed (HTTP {resp.status_code}): "
            f"{resp.text[:500]}"
        )
    if resp.status_code == 204 or not resp.content:
        return {}
    return resp.json()


def get_my_profile(args, get_token):
    me = _graph(get_token, "GET", "/me")
    return {
        "displayName": me.get("displayName"),
        "userPrincipalName": me.get("userPrincipalName"),
        "mail": me.get("mail"),
        "jobTitle": me.get("jobTitle"),
        "id": me.get("id"),
    }


def list_recent_emails(args, get_token):
    limit = int(args.get("limit", 10))
    folder = args.get("folder", "inbox")
    data = _graph(
        get_token, "GET",
        f"/me/mailFolders/{folder}/messages",
        params={
            "$top": min(max(1, limit), 50),
            "$select": "subject,from,receivedDateTime,bodyPreview,isRead,webLink",
            "$orderby": "receivedDateTime desc",
        },
    )
    out = []
    for m in data.get("value", []):
        out.append({
            "subject": m.get("subject"),
            "from": ((m.get("from") or {}).get("emailAddress") or {}).get("address"),
            "received": m.get("receivedDateTime"),
            "preview": (m.get("bodyPreview") or "")[:300],
            "is_read": m.get("isRead"),
            "web_link": m.get("webLink"),
        })
    return {"count": len(out), "messages": out}


def send_email(args, get_token):
    to = args.get("to")
    subject = args.get("subject", "")
    body = args.get("body", "")
    body_type = args.get("body_type", "Text")
    if not to:
        raise ValueError("'to' is required")
    if isinstance(to, str):
        to = [s.strip() for s in to.split(",")] if "," in to else [to]
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": body_type, "content": body},
            "toRecipients": [{"emailAddress": {"address": a}} for a in to],
        },
        "saveToSentItems": True,
    }
    _graph(get_token, "POST", "/me/sendMail", json_body=payload)
    return {"status": "sent", "to": to, "subject": subject}


def list_upcoming_meetings(args, get_token):
    days = int(args.get("days", 7))
    limit = int(args.get("limit", 20))
    now = datetime.now(timezone.utc).replace(microsecond=0)
    end = now + timedelta(days=days)
    data = _graph(
        get_token, "GET",
        "/me/calendarView",
        params={
            "startDateTime": now.isoformat().replace("+00:00", "Z"),
            "endDateTime": end.isoformat().replace("+00:00", "Z"),
            "$top": min(max(1, limit), 50),
            "$orderby": "start/dateTime",
            "$select": "subject,start,end,organizer,location,onlineMeetingUrl,webLink",
        },
    )
    out = []
    for ev in data.get("value", []):
        out.append({
            "subject": ev.get("subject"),
            "start": (ev.get("start") or {}).get("dateTime"),
            "end": (ev.get("end") or {}).get("dateTime"),
            "organizer": ((ev.get("organizer") or {}).get("emailAddress") or {}).get("address"),
            "location": (ev.get("location") or {}).get("displayName"),
            "online_meeting_url": ev.get("onlineMeetingUrl"),
            "web_link": ev.get("webLink"),
        })
    return {"count": len(out), "meetings": out}


_TOOLS = [
    {
        "name": "get_my_profile",
        "description": "Get the signed-in user's Microsoft 365 profile (name, email, job title).",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": get_my_profile,
    },
    {
        "name": "list_recent_emails",
        "description": "List the user's most recent emails. Returns subject, sender, received timestamp, and preview.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50,
                          "description": "Max number of messages to return."},
                "folder": {"type": "string", "default": "inbox",
                           "description": "Mail folder id (default 'inbox')."},
            },
            "additionalProperties": False,
        },
        "handler": list_recent_emails,
    },
    {
        "name": "send_email",
        "description": "Send an email from the signed-in user's mailbox.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string",
                       "description": "Recipient email address. Comma-separate for multiple."},
                "subject": {"type": "string", "description": "Email subject line."},
                "body": {"type": "string", "description": "Email body content."},
                "body_type": {"type": "string", "enum": ["Text", "HTML"], "default": "Text",
                              "description": "Body content type."},
            },
            "required": ["to", "subject", "body"],
            "additionalProperties": False,
        },
        "handler": send_email,
    },
    {
        "name": "list_upcoming_meetings",
        "description": "List the user's upcoming calendar events within the next N days.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 7, "minimum": 1, "maximum": 90,
                         "description": "Window in days from now."},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 50,
                          "description": "Max number of events to return."},
            },
            "additionalProperties": False,
        },
        "handler": list_upcoming_meetings,
    },
]

TOOL_HANDLERS = {t["name"]: t["handler"] for t in _TOOLS}
TOOL_SCHEMAS = [{k: v for k, v in t.items() if k != "handler"} for t in _TOOLS]
