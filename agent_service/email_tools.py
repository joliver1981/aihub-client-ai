"""
Agent Email (A6) — READING tools: page/search the inbox, open a message's
body, list / read / save its attachments.

The A6 loop could already RECEIVE mail (poller -> headless turn) and
get_agent_email_status could say THAT mail arrived — but nothing let the
model OPEN an email on request: no body, no attachment list, no attachment
text, no file on disk. These five tools close that gap as thin compositions
over the seams that already exist:

- Data path = email_client (the cloud poll/message/attachment routes + the
  main app's extract route). NEVER the cloud DB: InboundEmailAttachments
  lives in the *cloud* database and this service must not import
  CommonUtils/flask — the HTTP client is the whole contract.
- Authz = the expand-a-row viewer's two-step, shared here as ONE chokepoint
  for the tools AND main.py's /api/email/log routes: the event must be in
  the CALLING user's own ledger (scoped by their address) — otherwise any
  signed-in user could pull arbitrary tenant mail by guessing event ids.
  Tools additionally accept a LIVE-FEED fallback (an event currently in the
  cloud feed whose recipient is this user's address): that is what makes the
  tools usable on the very mail an email-triggered headless turn is handling
  (its ledger row is only written AFTER the turn) and on cooldown-deferred
  mail the ledger never saw. Attachments must ALSO appear in the cloud's
  attachment list FOR that event — the cloud attachment routes are
  tenant-scoped, so without the (event_id, attachment_id) pairing an
  attachment id could be fetched through someone else's (or no) email.
- Saving = original bytes into data/agent/users/<uid>/email/<event_id>/ —
  the same per-user tree as uploads/ and downloads/ (NOT the legacy
  GeneralAgent's data/agent_files, which is agent-id-scoped and belongs to
  the main app). Everything under APP_ROOT is reachable by
  list_server_files / import_documents / read_file / offer_file_download,
  so a saved attachment composes with the whole FILES doctrine.

Caps: the main app's extract route does NOT clamp a requested max_chars
(agent_email_routes.py passes it straight to the extractor), so the clamp
lives HERE — min(max(1000, requested), AGENT_EMAIL_ATTACH_MAX_CHARS).
Saved files are size-capped like chat uploads. Retention: the cloud keeps
mail ~3 days; every tool reports an expired body/attachment honestly
instead of erroring. No reaper for saved files yet — deliberately the same
posture as downloads/ staging (revisit together if disk growth bites).

Kill switch: AGENT_EMAIL_TOOLS (agent_config.email_tools_enabled, default
true) — brain.py registers EMAIL_TOOLS only when it is on; flipping it off
reverts to the pre-tool behavior (status view + inbound replies untouched).
"""

import os
import re
from typing import Any, Optional

from claude_agent_sdk import tool

from agent_config import USERS_DIR, logger
from platform_tools import CURRENT_USER, _text
from file_tools import _fmt_size, _NAME_RE
from document_tools import _ALLOWED_EXTS   # single source: what the doc
                                           # engine actually ingests
import email_client
import email_store

# Per-call default vs hard ceiling for attachment text. The ceiling mirrors
# the platform's MAX_ATTACHMENT_CHARS default (config.py, 500k); the default
# is lower on purpose — tool output lands in the SDK transcript and compounds
# every turn, so the model asks for more only when it needs more.
ATTACH_READ_DEFAULT = int(os.getenv("AGENT_EMAIL_ATTACH_READ_CHARS", "100000"))
ATTACH_READ_CEILING = int(os.getenv("AGENT_EMAIL_ATTACH_MAX_CHARS", "500000"))
BODY_CHARS = int(os.getenv("AGENT_EMAIL_BODY_CHARS", "15000"))
SAVE_MAX_MB = int(os.getenv("AGENT_EMAIL_SAVE_MAX_MB", "50"))
_PENDING_SHOWN = 10          # live-feed rows surfaced by list_my_email


class EmailAccess(Exception):
    """Refused email access — the message is honest, user-safe text."""


def _as_int(value, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise EmailAccess(f"{name} must be a number (got {value!r}).")


def _user_address(user_id: int) -> dict:
    row = email_store.get_address(int(user_id))
    if not row:
        raise EmailAccess("No agent address set up for this user.")
    return row


def ledger_entry_for(user_id: int, event_id: int) -> tuple:
    """(ledger entry, address) for an event the user OWNS, else EmailAccess.
    THE authz chokepoint — main.py's /api/email/log routes and every tool
    here converge on it (message text is part of the route contract)."""
    row = _user_address(user_id)
    address = row["email_address"]
    entry = email_store.get_processed(int(event_id), address)
    if not entry:
        raise EmailAccess("That email is not in your activity log.")
    return entry, address


async def _live_event_for(address: str, event_id: int) -> Optional[dict]:
    """The event from the CURRENT cloud feed, only if addressed to `address`."""
    try:
        events = await email_client.poll()
    except Exception as e:
        logger.warning(f"email tools live-feed check failed: {e}")
        return None
    for ev in events:
        if int(ev.get("event_id") or ev.get("id") or 0) != int(event_id):
            continue
        recipient = str(ev.get("recipient_email") or ev.get("recipient")
                        or "").lower()
        return ev if recipient == address.lower() else None
    return None


def _pseudo_entry(ev: dict, address: str) -> dict:
    """A ledger-shaped view of a live-feed event (outcome 'pending')."""
    return {"event_id": int(ev.get("event_id") or ev.get("id") or 0),
            "address": address.lower(),
            "sender": str(ev.get("sender_email") or ev.get("sender")
                          or ev.get("from") or ""),
            "subject": str(ev.get("subject") or ""),
            "outcome": "pending", "detail": "", "processed_at": "",
            "message_key": str(ev.get("message_key") or "")}


async def _owned_event(user_id: int, event_id: int) -> tuple:
    """(entry, address, pending) — ledger first, live-feed fallback."""
    row = _user_address(user_id)
    address = row["email_address"]
    entry = email_store.get_processed(int(event_id), address)
    if entry:
        return entry, address, False
    ev = await _live_event_for(address, int(event_id))
    if ev:
        return _pseudo_entry(ev, address), address, True
    raise EmailAccess(
        "That email is not in your activity log or the live inbound feed — "
        "you can only open mail addressed to this user's own agent address. "
        "Call list_my_email to see what you can open.")


async def _owned_attachment(user_id: int, event_id: int,
                            attachment_id: int) -> tuple:
    """(attachment row, entry, address, pending) — ownership of the EVENT,
    then membership of the attachment ON that event. Both checks run before
    any attachment bytes/text are fetched."""
    entry, address, pending = await _owned_event(user_id, event_id)
    atts = await email_client.attachments_for(int(event_id))
    match = next((a for a in atts
                  if int(a.get("attachment_id") or a.get("id") or 0)
                  == int(attachment_id)), None)
    if not match:
        raise EmailAccess(
            "That attachment is not on that email (or the cloud's ~3-day "
            "retention has expired its attachment list). Call "
            "list_email_attachments with the event_id to see what exists.")
    return match, entry, address, pending


async def _recover_message_key(event_id: int) -> str:
    """Ledger rows recorded before the message_key column carry '' — recover
    the key from the live feed while retention lasts (ownership was already
    established; this only re-finds the key for the SAME event id)."""
    try:
        for ev in await email_client.poll():
            if int(ev.get("event_id") or ev.get("id") or 0) == int(event_id):
                return str(ev.get("message_key") or "")
    except Exception as e:
        logger.warning(f"email tools message-key recovery failed: {e}")
    return ""


def _att_line(a: dict) -> str:
    aid = a.get("attachment_id") or a.get("id")
    return (f"  - attachment_id={aid} "
            f"{a.get('filename') or f'attachment-{aid}'} "
            f"({a.get('content_type') or 'unknown type'}, "
            f"{_fmt_size(int(a.get('size') or 0))})")


def _html_to_text(html: str) -> str:
    """Crude fallback for HTML-only mail: tags out, entities left alone."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", str(html or ""))
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    return re.sub(r"\s{3,}", "\n", re.sub(r"<[^>]+>", " ", text)).strip()


# ---------------------------------------------------------------------------
# list_my_email
# ---------------------------------------------------------------------------

@tool(
    "list_my_email",
    "Page and search the current user's AGENT-email inbox history — the AI "
    "Hub agent address people send mail TO (mail for the agent), NOT the "
    "user's own Outlook / Microsoft 365 inbox (that is a personal "
    "connection: list_my_connections / use_my_connection). Returns the full "
    "ledger get_agent_email_status shows only the last 5 rows of, plus any "
    "PENDING mail sitting in the live cloud feed that the poller has not "
    "processed yet. Each row carries the event_id that read_email / "
    "list_email_attachments / read_attachment / save_attachment take. "
    "Filters combine; times are UTC ISO.",
    {
        "type": "object",
        "properties": {
            "limit": {"type": "integer",
                      "description": "Rows per page, 1-100 (default 20)"},
            "offset": {"type": "integer",
                       "description": "Rows to skip (default 0)"},
            "since": {"type": "string",
                      "description": "Only rows processed at/after this UTC "
                                     "ISO date or datetime, e.g. 2026-08-20"},
            "sender": {"type": "string",
                       "description": "Substring match on the sender address"},
            "subject_contains": {"type": "string",
                                 "description": "Substring match on the subject"},
            "include_skipped": {"type": "boolean",
                                "description": "Also show skipped_* rows "
                                               "(self-mail, rate-limited); "
                                               "default false"},
            "include_pending": {"type": "boolean",
                                "description": "Merge unprocessed mail from "
                                               "the live cloud feed (default "
                                               "true; first page only)"},
        },
        "required": [],
        "additionalProperties": False,
    },
)
async def list_my_email(args: dict[str, Any]) -> dict[str, Any]:
    user = CURRENT_USER.get()
    try:
        row = _user_address(int(user.get("user_id") or 0))
        limit = max(1, min(_as_int(args.get("limit") or 20, "limit"), 100))
        offset = max(0, _as_int(args.get("offset") or 0, "offset"))
    except EmailAccess as e:
        return _text(f"{e} Offer to create one via setup_agent_email (with "
                     "the user's permission) — there is no mail to list "
                     "until an address exists.", is_error=True)
    address = row["email_address"]
    sender = str(args.get("sender") or "").strip()
    subject = str(args.get("subject_contains") or "").strip()
    since = str(args.get("since") or "").strip()
    include_skipped = bool(args.get("include_skipped"))
    rows, total = email_store.search(
        address, limit=limit, offset=offset, since=since, sender=sender,
        subject_contains=subject, include_skipped=include_skipped)

    lines = [f"Inbox for {address} — {total} logged row(s)"
             + ("" if include_skipped else " (skipped_* rows hidden; "
                "include_skipped=true to show them)")
             + (f", showing {len(rows)} from offset {offset}." if rows
                else ".")]

    # Live-feed merge: mail addressed to me the ledger has no row for —
    # cooldown-deferred, or simply not yet reached by the 60s poll cycle.
    if args.get("include_pending", True) and offset == 0:
        try:
            events = await email_client.poll()
        except Exception as e:
            events = []
            lines.append(f"(live-feed check unavailable: {e})")
        pending = []
        for ev in events:
            recipient = str(ev.get("recipient_email") or ev.get("recipient")
                            or "").lower()
            eid = int(ev.get("event_id") or ev.get("id") or 0)
            if recipient != address.lower() or not eid \
                    or email_store.already_processed(eid, address):
                continue
            p = _pseudo_entry(ev, address)
            if sender and sender.lower() not in p["sender"].lower():
                continue
            if subject and subject.lower() not in p["subject"].lower():
                continue
            pending.append(p)
        if pending:
            shown = pending[:_PENDING_SHOWN]
            lines.append(f"PENDING — in the cloud feed, not processed yet "
                         f"({len(pending)} waiting"
                         + (f", first {len(shown)} shown" if
                            len(pending) > len(shown) else "")
                         + "); you can read_email these now:")
            for p in shown:
                lines.append(f"  event_id={p['event_id']} [pending] from "
                             f"{p['sender'] or '?'}: "
                             f"{(p['subject'] or '(no subject)')[:80]}")

    for e in rows:
        lines.append(f"  event_id={e['event_id']} {e['processed_at'][:16]} "
                     f"[{e['outcome']}] from {e.get('sender') or '?'}: "
                     f"{(e.get('subject') or '(no subject)')[:80]}")
    if not rows and total == 0:
        lines.append("No logged inbound mail matches.")
    elif offset + len(rows) < total:
        lines.append(f"More rows exist — call again with "
                     f"offset={offset + len(rows)}.")
    lines.append("Open one with read_email(event_id=...). Note: "
                 "'reply_drafted' means draft_email_reply ran — with the "
                 "address's auto-send ON that reply went out immediately.")
    return _text("\n".join(lines))


# ---------------------------------------------------------------------------
# read_email
# ---------------------------------------------------------------------------

@tool(
    "read_email",
    "Open ONE inbound email from the current user's agent inbox: full "
    "message body plus its attachment list (with the attachment_ids that "
    "read_attachment / save_attachment take). event_id comes from "
    "list_my_email, get_agent_email_status activity, or an inbound-email "
    "session's context line. The cloud retains bodies ~3 days — an expired "
    "body is reported honestly (ledger metadata still returns).",
    {
        "type": "object",
        "properties": {
            "event_id": {"type": "integer",
                         "description": "The email's event id"},
        },
        "required": ["event_id"],
        "additionalProperties": False,
    },
)
async def read_email(args: dict[str, Any]) -> dict[str, Any]:
    user = CURRENT_USER.get()
    try:
        event_id = _as_int(args.get("event_id"), "event_id")
        entry, address, pending = await _owned_event(
            int(user.get("user_id") or 0), event_id)
    except EmailAccess as e:
        return _text(str(e), is_error=True)

    key = str(entry.get("message_key") or "") \
        or await _recover_message_key(event_id)
    message = await email_client.full_message(key) if key else None
    atts = await email_client.attachments_for(event_id)

    status = ("pending — in the cloud feed, not yet processed by the poller"
              if pending else entry.get("outcome", "?"))
    lines = [f"Email event_id={event_id} [{status}]",
             f"From: {entry.get('sender') or '?'}",
             f"To: {address}",
             f"Subject: {entry.get('subject') or '(no subject)'}"]
    if entry.get("processed_at"):
        lines.append(f"Processed at: {entry['processed_at']} (UTC) — "
                     f"outcome detail: {entry.get('detail') or '(none)'}")

    if message:
        body = email_client.body_text_of(message)
        via_html = False
        if not body and (message.get("body_html") or message.get("body-html")):
            body = _html_to_text(message.get("body_html")
                                 or message.get("body-html"))
            via_html = True
        body = str(body or "")
        lines.append("--- body" + (" (converted from HTML)" if via_html
                                   else "") + " ---")
        lines.append(body[:BODY_CHARS] if body else "(empty body)")
        if len(body) > BODY_CHARS:
            lines.append(f"(body truncated at {BODY_CHARS} of {len(body)} "
                         "chars)")
    else:
        lines.append("Body NOT RETAINED: the cloud keeps mail ~3 days and "
                     "this message's body is no longer available — the "
                     "metadata above is what remains. Say so honestly.")

    if atts:
        lines.append(f"Attachments ({len(atts)}):")
        lines += [_att_line(a) for a in atts]
        lines.append(f"read_attachment(event_id={event_id}, "
                     "attachment_id=...) returns extracted text; "
                     "save_attachment writes the original file to the "
                     "server for import_documents / offer_file_download.")
    else:
        lines.append("Attachments: none listed (the email had none, or "
                     "cloud retention has expired them).")
    return _text("\n".join(lines))


# ---------------------------------------------------------------------------
# list_email_attachments
# ---------------------------------------------------------------------------

@tool(
    "list_email_attachments",
    "List the attachments on ONE inbound email in the current user's agent "
    "inbox: filenames, content types, sizes, and the attachment_ids that "
    "read_attachment / save_attachment require. Cheaper than read_email "
    "when only the files matter.",
    {
        "type": "object",
        "properties": {
            "event_id": {"type": "integer",
                         "description": "The email's event id"},
        },
        "required": ["event_id"],
        "additionalProperties": False,
    },
)
async def list_email_attachments(args: dict[str, Any]) -> dict[str, Any]:
    user = CURRENT_USER.get()
    try:
        event_id = _as_int(args.get("event_id"), "event_id")
        entry, _address, pending = await _owned_event(
            int(user.get("user_id") or 0), event_id)
    except EmailAccess as e:
        return _text(str(e), is_error=True)
    atts = await email_client.attachments_for(event_id)
    if not atts:
        return _text(f"No attachments listed for event_id={event_id} "
                     f"(subject: {entry.get('subject') or '(no subject)'}) — "
                     "the email had none, or the cloud's ~3-day retention "
                     "has expired them.")
    lines = [f"Attachments on event_id={event_id} "
             f"(subject: {(entry.get('subject') or '(no subject)')[:60]}):"]
    lines += [_att_line(a) for a in atts]
    lines.append(f"read_attachment(event_id={event_id}, attachment_id=...) "
                 "for extracted text; save_attachment for the original file.")
    return _text("\n".join(lines))


# ---------------------------------------------------------------------------
# read_attachment
# ---------------------------------------------------------------------------

@tool(
    "read_attachment",
    "Extract and return the TEXT of one attachment on an email in the "
    "current user's agent inbox (PDF/Word/Excel/CSV/images — OCR fallback "
    "for scans, all server-side). Both ids are required and must belong "
    "together: event_id owns the mail, attachment_id names the file on it "
    "(get both from read_email or list_email_attachments). Default "
    "max_chars is generous; raise it (up to the platform cap) only when "
    "the full text truly matters. For the original FILE, use "
    "save_attachment instead.",
    {
        "type": "object",
        "properties": {
            "event_id": {"type": "integer",
                         "description": "The email's event id"},
            "attachment_id": {"type": "integer",
                              "description": "The attachment's id ON that "
                                             "email"},
            "max_chars": {"type": "integer",
                          "description": f"Max characters returned (default "
                                         f"{ATTACH_READ_DEFAULT}, ceiling "
                                         f"{ATTACH_READ_CEILING})"},
        },
        "required": ["event_id", "attachment_id"],
        "additionalProperties": False,
    },
)
async def read_attachment(args: dict[str, Any]) -> dict[str, Any]:
    user = CURRENT_USER.get()
    try:
        event_id = _as_int(args.get("event_id"), "event_id")
        attachment_id = _as_int(args.get("attachment_id"), "attachment_id")
        att, _entry, _address, _pending = await _owned_attachment(
            int(user.get("user_id") or 0), event_id, attachment_id)
    except EmailAccess as e:
        return _text(str(e), is_error=True)

    # The main-app extract route does NOT clamp — this clamp is the cap.
    requested = args.get("max_chars")
    try:
        requested = int(requested) if requested is not None \
            else ATTACH_READ_DEFAULT
    except (TypeError, ValueError):
        requested = ATTACH_READ_DEFAULT
    max_chars = min(max(1000, requested), ATTACH_READ_CEILING)

    result = await email_client.extract_attachment_text(attachment_id,
                                                        max_chars)
    name = att.get("filename") or f"attachment-{attachment_id}"
    if not result.get("success"):
        return _text(f"Could not extract text from {name}: "
                     f"{result.get('error') or 'unknown error'}. If the "
                     "cloud's ~3-day retention expired the file this is "
                     "permanent; otherwise save_attachment + read_file is "
                     "the alternate path.", is_error=True)
    text = str(result.get("text") or "")
    header = (f"{name} ({att.get('content_type') or 'unknown type'}, "
              f"{_fmt_size(int(att.get('size') or 0))}) — extracted via "
              f"{result.get('extraction_method') or 'text extractor'}")
    if result.get("truncated"):
        header += (f"; TRUNCATED at {max_chars} of "
                   f"{result.get('original_length') or '?'} chars — call "
                   f"again with a higher max_chars (ceiling "
                   f"{ATTACH_READ_CEILING}) if the rest matters")
    if not text.strip():
        return _text(f"{header}\n(extraction succeeded but produced no text "
                     "— likely an image-only or empty file; OCR already ran "
                     "if applicable)")
    return _text(f"{header}\n---\n{text}")


# ---------------------------------------------------------------------------
# save_attachment
# ---------------------------------------------------------------------------

@tool(
    "save_attachment",
    "Save one email attachment's ORIGINAL bytes to the server, into the "
    "current user's private area — so the file can then be ingested with "
    "import_documents (making it searchable), read with read_file, or "
    "handed to the user with offer_file_download. Save-only: nothing is "
    "imported until you call import_documents on the returned path. Both "
    "ids must belong together (see read_email / list_email_attachments). "
    "Only document/image types are savable; for anything else use "
    "read_attachment for the text instead.",
    {
        "type": "object",
        "properties": {
            "event_id": {"type": "integer",
                         "description": "The email's event id"},
            "attachment_id": {"type": "integer",
                              "description": "The attachment's id ON that "
                                             "email"},
            "filename": {"type": "string",
                         "description": "Optional nicer name (no folders); "
                                        "defaults to the original"},
        },
        "required": ["event_id", "attachment_id"],
        "additionalProperties": False,
    },
)
async def save_attachment(args: dict[str, Any]) -> dict[str, Any]:
    user = CURRENT_USER.get()
    uid = int(user.get("user_id") or 0)
    try:
        event_id = _as_int(args.get("event_id"), "event_id")
        attachment_id = _as_int(args.get("attachment_id"), "attachment_id")
        att, _entry, _address, _pending = await _owned_attachment(
            uid, event_id, attachment_id)
    except EmailAccess as e:
        return _text(str(e), is_error=True)

    original = str(att.get("filename") or f"attachment-{attachment_id}")
    requested = str(args.get("filename") or "").strip()
    if requested and (os.path.basename(requested) != requested
                      or ".." in requested):
        return _text("Refused: filename may not contain folders or '..' — "
                     "give a bare name; the file always lands in this "
                     "user's own email area.", is_error=True)
    name = requested or original
    # No extension on the requested name -> inherit the original's.
    if "." not in name and "." in original:
        name += "." + original.rsplit(".", 1)[1]
    ext = name.rsplit(".", 1)[1].lower() if "." in name else ""
    if ext not in _ALLOWED_EXTS:
        return _text(f"Refused: '.{ext or '(none)'}' is not a savable type — "
                     f"allowed: {', '.join(sorted(_ALLOWED_EXTS))}. These "
                     "match what import_documents can ingest; for other "
                     "text-bearing files use read_attachment instead.",
                     is_error=True)

    listed_size = int(att.get("size") or 0)
    cap = SAVE_MAX_MB * 1024 * 1024
    if listed_size > cap:
        return _text(f"Refused: attachment is {_fmt_size(listed_size)} — "
                     f"over the {SAVE_MAX_MB} MB save cap "
                     "(AGENT_EMAIL_SAVE_MAX_MB).", is_error=True)
    fetched = await email_client.attachment_bytes(attachment_id)
    if not fetched:
        return _text("The cloud mailbox could not serve the attachment "
                     "bytes — its ~3-day retention has likely expired. "
                     "Nothing was saved; say so honestly.", is_error=True)
    content, _content_type = fetched
    if len(content) > cap:
        return _text(f"Refused: attachment is {_fmt_size(len(content))} — "
                     f"over the {SAVE_MAX_MB} MB save cap.", is_error=True)

    # Inbound filenames are attacker-controlled: sanitize, then defuse any
    # residue with the attachment_id prefix (a bare '..' can never become a
    # path segment). Containment assert is belt-and-suspenders.
    safe = _NAME_RE.sub("_", name)[:150].strip() or f"attachment-{attachment_id}"
    dest_dir = os.path.join(USERS_DIR, str(uid), "email", str(event_id))
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.abspath(os.path.join(dest_dir, f"{attachment_id}__{safe}"))
    if not dest.startswith(os.path.abspath(dest_dir) + os.sep):
        return _text("Refused: resolved path escaped the user's email area.",
                     is_error=True)
    try:
        with open(dest, "wb") as fh:
            fh.write(content)
    except OSError as e:
        return _text(f"Could not write the file: {e}", is_error=True)
    logger.info(f"email attachment saved: user {uid} event {event_id} "
                f"attachment {attachment_id} -> {dest} "
                f"({_fmt_size(len(content))})")
    return _text(f"Saved {_fmt_size(len(content))} to {dest}\n"
                 "That server path now works with import_documents (ingest "
                 "for search/Q&A), read_file (look at it once), and "
                 "offer_file_download (give the user a download link). "
                 "Nothing has been imported yet.")


EMAIL_TOOLS = [list_my_email, read_email, list_email_attachments,
               read_attachment, save_attachment]
