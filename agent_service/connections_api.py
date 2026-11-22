"""
The Agent's My Connections screen — backend logic for /api/my-connections*.

The screen lets the signed-in user see their OWN personal accounts (Microsoft
365 / Outlook mail and calendar, and any other per-user sign-in an admin has
published), connect one, and disconnect it — without leaving The Agent for the
classic My Connections page.

Nothing here owns state or credentials. Every call goes through the main app's
internal seam (/api/internal/my-connections*, the one connection_tools.py
already uses) with the service key AND a signed assertion naming the user from
their verified token, so the list is exactly the classic page's list for that
user and a disconnect can only ever remove their own authorization.

Connect itself is NOT here: the sign-in at Microsoft (or another provider) has
to run in the user's browser on the main app's address, where the redirect and
its signed state are checked. The screen opens the main app's
/my-connections/connect/<id> in a popup and re-reads this list when it closes.

What the screen is told about The Agent's own rules, so it can say them plainly:
  - agent_blocked: the server is on AGENT_MY_CONNECTIONS_DENY — the user may
    connect it (other agents can use it) but The Agent will not.
  - write_tools: the exact tool names an admin allowed The Agent to use for
    changes (AGENT_MY_CONNECTIONS_WRITE_TOOLS). Empty = reading only.

Switch: AGENT_MY_CONNECTIONS (the same one that registers the chat tools in
brain.py). Off = no screen and these routes refuse, exactly as before it existed.
"""

import os

from fastapi import HTTPException

from agent_config import logger
from connection_tools import (
    SEAM, _get, _post, _seam_error, denied_servers, write_tools_allowed,
)

PUBLIC_FIELDS = ("server_id", "name", "description", "category",
                 "connected", "last_connected", "scope")


def enabled() -> bool:
    """The same switch brain.py reads to register the chat tools."""
    return os.getenv("AGENT_MY_CONNECTIONS", "true").lower() == "true"


def _require(user: dict) -> int:
    if not enabled():
        raise HTTPException(404, "Personal connections are turned off for The "
                                 "Agent on this install.")
    try:
        uid = int(user.get("user_id") or 0)
    except (TypeError, ValueError):
        uid = 0
    if uid <= 0:
        raise HTTPException(403, "Personal connections belong to a signed-in "
                                 "user, and this request does not name one.")
    return uid


async def list_for(user: dict) -> dict:
    """This user's personal connections, connected or not, plus what The
    Agent is allowed to do with them on this install."""
    uid = _require(user)
    data, status = await _get(SEAM, user)
    if status != 200 or not isinstance(data, dict) or data.get("status") != "success":
        raise HTTPException(502, _seam_error(data, status, "Listing your connections"))
    deny = denied_servers()
    connections = []
    for c in data.get("connections") or []:
        entry = {k: c.get(k) for k in PUBLIC_FIELDS}
        try:
            entry["agent_blocked"] = int(c.get("server_id") or 0) in deny
        except (TypeError, ValueError):
            entry["agent_blocked"] = False
        connections.append(entry)
    return {"connections": connections,
            # who this list is for: the Connect popup hands it to AI Hub, which
            # refuses when a DIFFERENT user is signed in there (an account is
            # attached to AI Hub's signed-in user, so the two must agree)
            "user_id": uid,
            "write_tools": sorted(write_tools_allowed()),
            # an Admin is the one who can publish an account type; the empty
            # state points them at the page where that is done
            "can_publish": int(user.get("role") or 0) >= 3}


async def disconnect(user: dict, server_id: int) -> dict:
    """Withdraw this user's own authorization for one server."""
    uid = _require(user)
    sid = int(server_id)
    data, status = await _post(f"{SEAM}/{sid}/disconnect", {}, user, timeout=30.0)
    if status == 404:
        # The main app answers 404 only when it does not have this action at
        # all (an older main service next to a newer Agent service).
        raise HTTPException(502, "AI Hub's main service does not offer Disconnect "
                                 "to The Agent yet. Disconnect on My Connections in "
                                 "the classic app, or ask an administrator to "
                                 "restart AI Hub.")
    if status != 200 or not isinstance(data, dict) or data.get("status") != "success":
        raise HTTPException(502, _seam_error(data, status, "Disconnecting"))
    logger.info(f"my connections: user {uid} disconnected server {sid}")
    return {"status": "success", "server_id": sid, "connected": False}
