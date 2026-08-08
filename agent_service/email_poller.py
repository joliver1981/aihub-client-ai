"""
Agent Email (A6) — the inbound poll loop.

Mirrors the legacy EmailAgentDispatcher's shape (poll the cloud feed, match
recipients against OUR addresses, dedupe locally, rate-limit, process) as a
fourth consumer of the same per-tenant queue. Differences by design:

- match set = per-USER addresses from email_store (never AgentEmailAddresses,
  which belongs to the legacy dispatcher);
- processing = one headless brain turn AS the address owner (same contract as
  POST /api/run), with attachment text injected the way the legacy dispatcher
  injects it into drafting prompts;
- replying is NEVER direct: the brain uses draft_email_reply, which files an
  editable approval into the owner's My Work — the respond hook does the
  actual send (the legacy require_approval contract, enforced structurally);
- self-loop guard: mail FROM any of our own addresses is recorded and
  skipped, so an approved reply landing back in the queue can't re-trigger.

Flag-gated: AGENT_EMAIL_ENABLED (default false) — kill switch doctrine.
"""

import asyncio
import os
from typing import Awaitable, Callable, Optional

from agent_config import logger
import email_client
import email_store
import workitem_store

POLL_SECONDS = max(int(os.getenv("AGENT_EMAIL_POLL_SECONDS", "60")), 30)
COOLDOWN_MINUTES = int(os.getenv("AGENT_EMAIL_COOLDOWN_MINUTES", "2"))
DAILY_CAP = int(os.getenv("AGENT_EMAIL_MAX_PER_DAY", "100"))
MAX_ATTACHMENTS = int(os.getenv("AGENT_EMAIL_MAX_ATTACHMENTS", "10"))
ATTACH_CHARS_EACH = int(os.getenv("AGENT_EMAIL_ATTACH_CHARS", "20000"))


def enabled() -> bool:
    return os.getenv("AGENT_EMAIL_ENABLED", "false").lower() == "true"


def _cooldown_blocked(address: str) -> bool:
    if COOLDOWN_MINUTES <= 0:
        return False
    last = email_store.last_processed_at(address)
    if not last:
        return False
    from datetime import datetime, timezone
    try:
        then = datetime.fromisoformat(last)
        delta = (datetime.now(timezone.utc) - then).total_seconds() / 60.0
        return delta < COOLDOWN_MINUTES
    except Exception:
        return False


def _event_field(ev: dict, *names, default=""):
    for n in names:
        v = ev.get(n)
        if v not in (None, ""):
            return v
    return default


async def build_prompt(ev: dict, owner: dict) -> str:
    sender = _event_field(ev, "sender_email", "sender", "from")
    subject = _event_field(ev, "subject")
    key = _event_field(ev, "message_key")
    body = await email_client.full_body(key) or \
        _event_field(ev, "body_preview", "body_plain", "stripped_text")

    parts = [
        "You are handling an INBOUND EMAIL sent to this user's personal agent "
        "address. Act on it with your tools exactly as if the user asked in "
        "chat, then follow the email doctrine: if a reply is warranted, use "
        "draft_email_reply (it files an editable approval in My Work — you "
        "cannot and must not send directly, and you must never claim a reply "
        "was SENT). If no reply is needed, just do the work and summarize.",
        f"From: {sender}",
        f"To: {_event_field(ev, 'recipient_email', 'recipient')}",
        f"Subject: {subject}",
        "",
        str(body or "(empty body)")[:15000],
    ]

    event_id = int(_event_field(ev, "event_id", "id", default=0) or 0)
    if event_id and _event_field(ev, "has_attachments", "attachment_count",
                                 default=None):
        atts = (await email_client.attachments_for(event_id))[:MAX_ATTACHMENTS]
        if atts:
            parts.append("\nAttachments (extracted text):")
            for a in atts:
                aid = a.get("attachment_id") or a.get("id")
                name = a.get("filename", f"attachment {aid}")
                ext = await email_client.extract_attachment_text(
                    int(aid), ATTACH_CHARS_EACH)
                if ext.get("success"):
                    parts.append(f"--- {name} ---\n"
                                 f"{str(ext.get('text') or '')[:ATTACH_CHARS_EACH]}")
                else:
                    parts.append(f"--- {name} --- (extraction failed: "
                                 f"{ext.get('error')})")
    return "\n".join(parts)


async def process_event(ev: dict, owner: dict, own_addresses: set,
                        run_turn_fn: Optional[Callable[..., object]] = None) -> str:
    """Process one inbound email for its owning user; returns the outcome
    string recorded in the ledger. run_turn_fn injectable for the test pack."""
    address = str(_event_field(ev, "recipient_email", "recipient")).lower()
    event_id = int(_event_field(ev, "event_id", "id", default=0) or 0)
    sender = str(_event_field(ev, "sender_email", "sender", "from")).lower()
    subject = _event_field(ev, "subject")

    if email_store.already_processed(event_id, address):
        return "skipped_duplicate"
    if sender in own_addresses:
        email_store.record(event_id, address, "skipped_self", sender, subject,
                           "mail from one of our own agent addresses")
        return "skipped_self"
    if email_store.processed_today(address) >= DAILY_CAP:
        email_store.record(event_id, address, "skipped_rate_limited", sender,
                           subject, f"daily cap {DAILY_CAP} reached")
        return "skipped_rate_limited"
    if _cooldown_blocked(address):
        # NOT recorded as processed — the next poll after the cooldown
        # window retries it (the 3-day cloud retention gives us slack).
        return "deferred_cooldown"

    user_ctx = {"user_id": int(owner["user_id"]), "role": int(owner.get("role") or 2),
                "username": owner.get("username") or f"user{owner['user_id']}",
                "name": owner.get("username") or ""}
    prompt = await build_prompt(ev, owner)

    if run_turn_fn is None:
        from brain import run_turn as run_turn_fn  # late import (test seam)

    texts, tools_run, final = [], [], {}
    async for evt in run_turn_fn(prompt, None, user_ctx, tool_scope="full"):
        if evt.get("type") == "text":
            texts.append(evt["text"])
        elif evt.get("type") == "tool":
            tools_run.append(str(evt.get("name", "")).replace("mcp__aihub__", ""))
        elif evt.get("type") in ("result", "error"):
            final = evt
    ok = bool(final.get("ok"))
    summary = "\n\n".join(texts).strip()
    drafted = "draft_email_reply" in tools_run

    if drafted:
        outcome = "reply_drafted"
        # the draft tool already filed the editable approval item — that item
        # is the surface; no extra FYI noise.
    else:
        outcome = "processed" if ok else "error"
        workitem_store.create_item(
            "acknowledge",
            f"{'✉' if ok else '⚠'} Email: {subject or '(no subject)'}",
            summary=(f"From {sender} to {address}.\n\n"
                     + (summary[:2000] or "(the session produced no text)")),
            payload={"kind": "agent_email_fyi", "event_id": event_id,
                     "sender": sender, "subject": subject, "ok": ok,
                     "tools_used": tools_run[:20],
                     "session_id": final.get("session_id")},
            addressed_user=int(owner["user_id"]),
            from_kind="agent_email", from_ref=address,
            created_by="email_poller")

    email_store.record(event_id, address, outcome, sender, subject,
                       f"tools={','.join(tools_run[:10])}")
    logger.info(f"agent email {outcome}: event {event_id} -> {address} "
                f"(sender {sender}, subject {subject!r})")
    return outcome


async def poll_once() -> dict:
    """One poll cycle; returns counts for logging/tests."""
    addrs = email_store.active_addresses()
    counts = {"events": 0, "matched": 0, "processed": 0}
    if not addrs:
        return counts
    events = await email_client.poll()
    counts["events"] = len(events)
    own = set(addrs.keys())
    for ev in reversed(events):          # poll returns newest first
        address = str(_event_field(ev, "recipient_email", "recipient")).lower()
        owner = addrs.get(address)
        if not owner:
            continue                     # someone else's mail (legacy agents)
        counts["matched"] += 1
        try:
            outcome = await process_event(ev, owner, own)
            if outcome in ("processed", "reply_drafted"):
                counts["processed"] += 1
        except Exception as e:
            event_id = int(_event_field(ev, "event_id", "id", default=0) or 0)
            email_store.record(event_id, address, "error", detail=str(e)[:400])
            logger.error(f"agent email processing error event {event_id}: {e}")
    return counts


async def run_forever():
    logger.info(f"agent email poller started (every {POLL_SECONDS}s, "
                f"cooldown {COOLDOWN_MINUTES}m, cap {DAILY_CAP}/day)")
    while True:
        try:
            counts = await poll_once()
            if counts["matched"]:
                logger.info(f"agent email poll: {counts}")
        except Exception as e:
            logger.error(f"agent email poll cycle failed: {e}")
        await asyncio.sleep(POLL_SECONDS)
