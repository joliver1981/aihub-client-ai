# -*- coding: utf-8 -*-
"""Decided work: a user's approval / rejection history across the sources
My Work reads (agent items, workflow approvals, automation review items),
read-only, most recent first. James 2026-09-25: "is there any way for a
user to view their approval/rejection history? My Approvals shows
everything but I don't see that in My Work."

An item is listed when this user decided it (the platform records the
decider as a user id when the decision came from My Work and as a username
when it came from the classic Approvals page) or when it was addressed to
this user directly. Group items decided by someone else are not their
history. Email drafts are not covered: their history lives in the email
store.
"""
import json
import logging

import readthrough
import workitem_store

logger = logging.getLogger("work_history")


def _parse(raw):
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}") or {}
    except Exception:
        return {}


def _decided_by_me(responded_by, uid: int, username: str) -> bool:
    who = str(responded_by or "").strip().lower()
    if not who:
        return False
    if who == str(uid):
        return True
    return bool(username) and who == username.strip().lower()


def _label(status) -> str:
    s = str(status or "").strip()
    return (s[:1].upper() + s[1:]) if s else "Decided"


def decided_items(user: dict, limit: int = 100) -> list:
    """The caller's decided items, newest decision first. `limit` is the
    caller's page size (a parameter, not a cap)."""
    uid = int(user.get("user_id") or 0)
    role = int(user.get("role") or 0)
    username = str(user.get("username") or "").strip()
    try:
        limit = max(1, int(limit or 100))
    except (TypeError, ValueError):
        limit = 100
    gids = readthrough.user_group_ids(uid)
    items = []

    # agent-raised items: closed, decided by this user or addressed to them
    try:
        agent_rows = workitem_store.list_items(uid, role=role, group_ids=gids,
                                               include_closed=True)
    except Exception as e:
        logger.warning(f"work_history: agent items unavailable: {e}")
        agent_rows = []
    for it in agent_rows:
        if it.get("status") in workitem_store.OPEN_STATUSES:
            continue
        mine = str(it.get("responded_by") or "") == str(uid)
        if not mine and it.get("addressed_user") != uid:
            continue
        resp = it.get("response") if isinstance(it.get("response"), dict) else _parse(it.get("response"))
        decision = resp.get("decision") or resp.get("status") or it.get("status")
        comment = (resp.get("comment") or resp.get("comments") or resp.get("answer")
                   or resp.get("text") or "")
        items.append({
            "source": "agent", "id": it["work_item_id"], "verb": it.get("verb"),
            "title": it.get("title"), "summary": it.get("summary") or "",
            "status": "decided", "decision": _label(decision),
            "decided_by": "you" if mine else str(it.get("responded_by") or ""),
            "decided_at": str(it.get("responded_at") or ""), "comments": str(comment or ""),
            "corrections": None, "outcome": None,
            "requested_at": it.get("created_at"), "from": it.get("created_by") or "agent",
            "payload": it.get("payload") or {}})

    # workflow approvals (ApprovalRequests)
    for row in readthrough.workflow_decided(uid, role=role, username=username, limit=limit):
        mine = _decided_by_me(row.get("responded_by"), uid, username)
        direct = (row.get("assigned_to_type") == "user"
                  and str(row.get("assigned_to_id")) == str(uid))
        if not (mine or direct):
            continue
        ad = _parse(row.get("approval_data"))
        items.append({
            "source": "workflow", "id": row.get("request_id"), "verb": "approve_deny",
            "title": row.get("title") or "Approval", "summary": row.get("description") or "",
            "status": "decided", "decision": _label(row.get("status")),
            "decided_by": "you" if mine else str(row.get("responded_by") or ""),
            "decided_at": str(row.get("response_at") or ""), "comments": row.get("comments") or "",
            "corrections": (ad.get("corrections") if isinstance(ad.get("corrections"), dict) else None),
            "outcome": None,
            "requested_at": str(row.get("requested_at") or ""),
            "from": ad.get("workflow_name") or "workflow", "payload": ad})

    # automation review items / checkpoints (file-backed rows)
    for row in readthrough.automation_decided(uid, gids, role=role, username=username, limit=limit):
        mine = _decided_by_me(row.get("responded_by"), uid, username)
        direct = row.get("assigned_to_type") == "user" and row.get("assigned_to_id") == uid
        if not (mine or direct):
            continue
        ad = _parse(row.get("approval_data"))
        is_review = ad.get("kind") == "review" or not ad.get("checkpoint_id")
        outcome = row.get("outcome") if isinstance(row.get("outcome"), dict) else None
        items.append({
            "source": "automation", "id": row.get("request_id"),
            "verb": "review" if is_review else "approve_deny",
            "title": row.get("title") or "Automation checkpoint",
            "summary": row.get("description") or "",
            "status": "decided", "decision": _label(row.get("status")),
            "decided_by": "you" if mine else str(row.get("responded_by") or ""),
            "decided_at": str(row.get("response_at") or ""), "comments": row.get("comments") or "",
            "corrections": (row.get("corrections") if isinstance(row.get("corrections"), dict) else None),
            "outcome": outcome,
            "requested_at": row.get("requested_at"),
            "from": ad.get("automation_name") or "automation",
            "payload": {"run_id": ad.get("run_id"), "checkpoint_id": ad.get("checkpoint_id"),
                        "automation_id": ad.get("automation_id"),
                        "automation_name": ad.get("automation_name"),
                        "kind": ad.get("kind"), "dry_run": ad.get("dry_run"),
                        "correctable": (ad.get("correctable")
                                        if isinstance(ad.get("correctable"), dict) else None),
                        "attachments": ad.get("attachments") or []}})

    items.sort(key=lambda i: str(i.get("decided_at") or ""), reverse=True)
    return items[:limit]


def as_text(items: list, limit: int = 30) -> str:
    """The list_my_work tool's rendering of decided items."""
    if not items:
        return "No decided items yet: nothing in your approval / rejection history."
    lines = []
    for i in items[:limit]:
        line = (f"- [{i['decision']}] {i['title']} ({i['source']}; decided "
                f"{i.get('decided_at') or '?'} by {i.get('decided_by') or '?'})")
        if i.get("comments"):
            line += f"; comment: {i['comments']}"
        if i.get("corrections"):
            line += "; corrections: " + ", ".join(f"{k}={v}" for k, v in i["corrections"].items())
        o = i.get("outcome") or {}
        if o.get("label") or o.get("note"):
            line += "; outcome: " + " - ".join(x for x in (o.get("label"), o.get("note")) if x)
        lines.append(line)
    more = f" (showing {limit} of {len(items)})" if len(items) > limit else ""
    return f"Decided items, most recent first ({len(items)}){more}:\n" + "\n".join(lines)
