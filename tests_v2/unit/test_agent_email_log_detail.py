"""Agent Email (A6) expand-a-row viewer — the seams behind
GET /api/email/log/<event_id> (+ /attachment/<id>).

The Email page's Recent-inbound rows expand to the full body + attachments,
fetched LIVE from the cloud. That needs three things this file pins down:

1. the ledger stores message_key (additive column — an OLD-shape
   processed_emails table must migrate in place and old rows read back
   with an empty key, never an error);
2. email_client.full_message returns the whole message dict (body_html
   included) using the SAME envelope full_body always unwrapped — and
   full_body, now a thin composition over it, keeps its documented
   text-priority chain (regression: James's 2026-08-09 '(empty body)' repro);
3. the poller actually threads each event's message_key into every ledger
   record — processed AND skipped rows (skipped mail is still mail the
   user may want to open).

Runs standalone (python test_agent_email_log_detail.py) or under pytest.
"""
import asyncio
import os
import sqlite3
import sys
import tempfile

import httpx

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

import email_client  # noqa: E402
import email_poller  # noqa: E402
import email_store   # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_RealAsyncClient = httpx.AsyncClient   # patching email_client.httpx patches
                                       # THIS module's httpx too (same object)


def _stub_client(payload, status=200):
    def handler(request):
        return httpx.Response(status, json=payload)
    return lambda **kw: _RealAsyncClient(transport=httpx.MockTransport(handler))


def _with_cloud(payload, coro_factory, status=200):
    orig = email_client.httpx.AsyncClient
    email_client.httpx.AsyncClient = _stub_client(payload, status)
    try:
        return asyncio.run(coro_factory())
    finally:
        email_client.httpx.AsyncClient = orig


class _TempLedger:
    """email_store pointed at a throwaway sqlite file (module-global DB_PATH
    is looked up at call time, so reassigning it is the whole seam)."""

    def __init__(self, old_shape=False):
        self.old_shape = old_shape

    def __enter__(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.path)          # let sqlite create it fresh
        self._orig = email_store.DB_PATH
        email_store.DB_PATH = self.path
        if self.old_shape:
            # The PRE-message_key table, verbatim from the v1 schema.
            with sqlite3.connect(self.path) as c:
                c.execute("""
                CREATE TABLE processed_emails (
                    event_id     INTEGER NOT NULL,
                    address      TEXT NOT NULL,
                    sender       TEXT DEFAULT '',
                    subject      TEXT DEFAULT '',
                    outcome      TEXT NOT NULL,
                    detail       TEXT DEFAULT '',
                    processed_at TEXT NOT NULL,
                    PRIMARY KEY (event_id, address)
                )""")
                c.execute("INSERT INTO processed_emails VALUES "
                          "(48, 'james-agent.1@mail.everiai.ai', 'a@b.c', "
                          "'old row', 'processed', 'tools=', "
                          "'2026-08-01T00:00:00+00:00')")
        email_store.init()
        return self

    def __exit__(self, *exc):
        email_store.DB_PATH = self._orig
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except OSError:
                pass
        return False


# ---------------------------------------------------------------------------
# 1. ledger: message_key column + get_processed
# ---------------------------------------------------------------------------

def test_old_shape_table_migrates_and_old_rows_read_back():
    with _TempLedger(old_shape=True):
        row = email_store.get_processed(48, "James-Agent.1@mail.everiai.ai")
        assert row is not None                      # address is case-folded
        assert row["subject"] == "old row"
        assert (row.get("message_key") or "") == ""  # pre-column row: empty


def test_record_roundtrips_message_key():
    with _TempLedger():
        email_store.record(101, "U@X.io", "processed", "s@e.nd", "hi",
                           "tools=query_database", message_key="mk-abc-123")
        row = email_store.get_processed(101, "u@x.io")
        assert row["message_key"] == "mk-abc-123"
        assert row["outcome"] == "processed"


def test_record_without_key_stores_empty_and_get_missing_is_none():
    with _TempLedger():
        email_store.record(7, "u@x.io", "error", detail="boom")
        assert email_store.get_processed(7, "u@x.io")["message_key"] == ""
        assert email_store.get_processed(7, "OTHER@x.io") is None
        assert email_store.get_processed(999, "u@x.io") is None


# ---------------------------------------------------------------------------
# 2. client: full_message / body_text_of / full_body composition
# ---------------------------------------------------------------------------

_LIVE_ENVELOPE = {"success": True, "message": {
    "body_text": "full thread text", "stripped_text": "stripped",
    "body_plain": "plain", "body_html": "<div>rich <b>html</b></div>"}}


def test_full_message_returns_whole_dict_with_html():
    msg = _with_cloud(_LIVE_ENVELOPE,
                      lambda: email_client.full_message("some-key"))
    assert msg["body_html"] == "<div>rich <b>html</b></div>"
    assert msg["body_text"] == "full thread text"


def test_full_message_none_on_error_status_and_empty_key():
    assert _with_cloud(_LIVE_ENVELOPE,
                       lambda: email_client.full_message("k"),
                       status=404) is None
    assert asyncio.run(email_client.full_message("")) is None


def test_body_text_of_priority_chain():
    assert email_client.body_text_of(_LIVE_ENVELOPE["message"]) \
        == "full thread text"
    assert email_client.body_text_of(
        {"stripped_text": "s", "body_plain": "p"}) == "s"
    assert email_client.body_text_of({"body-plain": "hyphen"}) == "hyphen"
    assert email_client.body_text_of({}) is None
    assert email_client.body_text_of(None) is None


def test_full_body_still_prefers_body_text():
    """The poller's body source must not regress from the delegation."""
    body = _with_cloud(_LIVE_ENVELOPE, lambda: email_client.full_body("k"))
    assert body == "full thread text"


# ---------------------------------------------------------------------------
# 3. poller: message_key flows into every ledger record
# ---------------------------------------------------------------------------

def _capture_records(monkey_calls):
    def fake_record(event_id, address, outcome, sender="", subject="",
                    detail="", message_key=""):
        monkey_calls.append({"event_id": event_id, "outcome": outcome,
                             "message_key": message_key})
    return fake_record


async def _fake_turn(prompt, session, user_ctx, tool_scope="full"):
    yield {"type": "text", "text": "done"}
    yield {"type": "result", "ok": True, "session_id": "s1"}


def _run_process_event(ev, owner, own_addresses):
    calls = []
    orig = {"record": email_store.record,
            "already": email_store.already_processed,
            "today": email_store.processed_today,
            "last": email_store.last_processed_at,
            "body": email_client.full_body,
            "create": email_poller.workitem_store.create_item}
    email_store.record = _capture_records(calls)
    email_store.already_processed = lambda e, a: False
    email_store.processed_today = lambda a: 0
    email_store.last_processed_at = lambda a: None

    async def _no_body(key):
        return "the body"
    email_client.full_body = _no_body
    email_poller.workitem_store.create_item = lambda *a, **k: {"id": 1}
    try:
        outcome = asyncio.run(email_poller.process_event(
            ev, owner, own_addresses, run_turn_fn=_fake_turn))
    finally:
        email_store.record = orig["record"]
        email_store.already_processed = orig["already"]
        email_store.processed_today = orig["today"]
        email_store.last_processed_at = orig["last"]
        email_client.full_body = orig["body"]
        email_poller.workitem_store.create_item = orig["create"]
    return outcome, calls


_OWNER = {"user_id": 5, "role": 2, "username": "james",
          "cooldown_minutes": 0, "notify_on_receive": 0}
_EV = {"event_id": 300, "recipient_email": "james-agent.1@mail.everiai.ai",
       "sender_email": "boss@corp.com", "subject": "Q3",
       "message_key": "mk-300"}


def test_processed_record_carries_message_key():
    outcome, calls = _run_process_event(_EV, _OWNER, set())
    assert outcome == "processed"
    assert calls and calls[-1]["message_key"] == "mk-300"


def test_skipped_self_record_carries_message_key():
    outcome, calls = _run_process_event(
        dict(_EV, sender_email="james-agent.1@mail.everiai.ai"), _OWNER,
        {"james-agent.1@mail.everiai.ai"})
    assert outcome == "skipped_self"
    assert calls and calls[-1]["message_key"] == "mk-300"


def _main():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
