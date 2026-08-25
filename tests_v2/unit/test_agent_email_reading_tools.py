"""Agent Email (A6) READING tools — email_tools.py's authz chokepoint and
the five tools (list_my_email / read_email / list_email_attachments /
read_attachment / save_attachment).

Adversarial-first, per the handoff's done-when: every refusal case is pinned
— foreign event ids, an attachment id paired with the wrong (but owned!)
event, path traversal in filenames, a .exe, oversize bytes — alongside the
honest-degradation cases (retention-expired body/bytes) and the live-feed
ownership fallback that makes the tools usable on the very mail an
email-triggered turn is processing (its ledger row lands only AFTER the
turn) and on cooldown-deferred mail. Cloud HTTP is stubbed at the
email_client function seam; the ledger is a throwaway sqlite; saves land in
a temp USERS_DIR.

Standalone (aihub-agent python test_agent_email_reading_tools.py) or pytest;
self-skips in envs without claude_agent_sdk (main-app sweep).
"""
import asyncio
import os
import shutil
import sqlite3
import sys
import tempfile

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import email_tools as et
    import email_client
    import email_store
    from platform_tools import CURRENT_USER
    HAVE_SDK = True
except ImportError as e:
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(reason=f"needs aihub-agent env: {_IMPORT_ERR}")
    except ImportError:
        pass

UID = 424242
ADDR = "unit-agent.1@mail.everiai.ai"
OTHER_ADDR = "someone-else-agent.1@mail.everiai.ai"


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

class patched:
    def __init__(self, obj, **attrs):
        self.obj, self.attrs, self.saved = obj, attrs, {}

    def __enter__(self):
        for k, v in self.attrs.items():
            self.saved[k] = getattr(self.obj, k)
            setattr(self.obj, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            setattr(self.obj, k, v)
        return False


class temp_ledger:
    """email_store on a throwaway sqlite file, with UID's address seeded."""

    def __enter__(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.path)
        self._orig = email_store.DB_PATH
        email_store.DB_PATH = self.path
        email_store.init()
        email_store.upsert_address(UID, "unit", ADDR, "unit-user", 2, True)
        return self

    def __exit__(self, *exc):
        email_store.DB_PATH = self._orig
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except OSError:
                pass
        return False


class temp_users_dir:
    def __enter__(self):
        self.path = tempfile.mkdtemp(prefix="agent_email_tools_")
        self._p = patched(et, USERS_DIR=self.path).__enter__()
        return self

    def __exit__(self, *exc):
        self._p.__exit__()
        shutil.rmtree(self.path, ignore_errors=True)
        return False


def _tools():
    return {t.name: t for t in et.EMAIL_TOOLS}


def _run(name, args):
    CURRENT_USER.set({"user_id": UID, "role": 2, "username": "unit-user"})
    return asyncio.run(_tools()[name].handler(args))


def _txt(res):
    return res["content"][0]["text"]


def _is_err(res):
    return bool(res.get("is_error"))


def _seed(event_id, outcome="processed", sender="boss@corp.com",
          subject="Q3 numbers", at="2026-08-20T10:00:00+00:00",
          message_key="mk-1", address=ADDR):
    with sqlite3.connect(email_store.DB_PATH) as c:
        c.execute("INSERT OR REPLACE INTO processed_emails (event_id, address,"
                  " sender, subject, outcome, detail, processed_at,"
                  " message_key) VALUES (?, ?, ?, ?, ?, '', ?, ?)",
                  (event_id, address.lower(), sender, subject, outcome, at,
                   message_key))


def _async(value):
    async def fn(*a, **k):
        return value
    return fn


def _async_capture(value, calls):
    async def fn(*a, **k):
        calls.append((a, k))
        return value
    return fn


_ATTS = [{"attachment_id": 9001, "filename": "invoice.pdf",
          "content_type": "application/pdf", "size": 1234}]
_MSG = {"body_text": "the full body text", "body_html": "<p>the full body text</p>"}
_LIVE_MINE = {"event_id": 800100, "recipient_email": ADDR,
              "sender_email": "vendor@ext.com", "subject": "pending invoice",
              "message_key": "mk-live-100"}
_LIVE_OTHER = {"event_id": 800200, "recipient_email": OTHER_ADDR,
               "sender_email": "vendor@ext.com", "subject": "not yours",
               "message_key": "mk-live-200"}


# ---------------------------------------------------------------------------
# authz: ownership
# ---------------------------------------------------------------------------

def test_read_email_foreign_event_refused():
    with temp_ledger(), patched(email_client, poll=_async([]),
                                attachments_for=_async([]),
                                full_message=_async(_MSG)):
        res = _run("read_email", {"event_id": 999999})
        assert _is_err(res)
        assert "not in your activity log" in _txt(res)


def test_read_email_no_address_refused():
    with temp_ledger(), patched(email_client, poll=_async([])):
        email_store.delete_address(UID)
        res = _run("read_email", {"event_id": 800001})
        assert _is_err(res)
        assert "No agent address" in _txt(res)


def test_event_owned_by_other_address_refused():
    """A row that exists in the tenant ledger under ANOTHER user's address
    must be invisible — the address scoping IS the authz."""
    with temp_ledger(), patched(email_client, poll=_async([]),
                                attachments_for=_async([]),
                                full_message=_async(_MSG)):
        _seed(800005, address=OTHER_ADDR)
        res = _run("read_email", {"event_id": 800005})
        assert _is_err(res)


def test_live_feed_ownership_accepts_my_pending_mail():
    with temp_ledger(), patched(email_client,
                                poll=_async([_LIVE_MINE, _LIVE_OTHER]),
                                attachments_for=_async([]),
                                full_message=_async(_MSG)):
        res = _run("read_email", {"event_id": 800100})
        assert not _is_err(res)
        assert "pending" in _txt(res)
        assert "the full body text" in _txt(res)


def test_live_feed_other_recipient_refused():
    with temp_ledger(), patched(email_client,
                                poll=_async([_LIVE_MINE, _LIVE_OTHER]),
                                attachments_for=_async([]),
                                full_message=_async(_MSG)):
        res = _run("read_email", {"event_id": 800200})
        assert _is_err(res)


def test_ledger_entry_for_keeps_route_contract_messages():
    """main.py's /api/email/log routes surface these strings verbatim."""
    with temp_ledger():
        try:
            et.ledger_entry_for(UID, 12345)
            assert False, "expected EmailAccess"
        except et.EmailAccess as e:
            assert str(e) == "That email is not in your activity log."
        email_store.delete_address(UID)
        try:
            et.ledger_entry_for(UID, 12345)
            assert False, "expected EmailAccess"
        except et.EmailAccess as e:
            assert str(e) == "No agent address set up for this user."


# ---------------------------------------------------------------------------
# authz: attachment membership (the event_id+attachment_id pairing)
# ---------------------------------------------------------------------------

def test_attachment_on_wrong_owned_event_refused():
    """Both events are MINE, but the attachment belongs to the other one —
    the pairing check must still refuse (tenant-scoped cloud routes)."""
    async def atts_for(event_id):
        return _ATTS if int(event_id) == 800001 else []
    with temp_ledger(), patched(email_client, poll=_async([]),
                                attachments_for=atts_for,
                                extract_attachment_text=_async(
                                    {"success": True, "text": "secret"})):
        _seed(800001)
        _seed(800002)
        ok = _run("read_attachment", {"event_id": 800001,
                                      "attachment_id": 9001})
        assert not _is_err(ok) and "secret" in _txt(ok)
        res = _run("read_attachment", {"event_id": 800002,
                                       "attachment_id": 9001})
        assert _is_err(res)
        assert "not on that email" in _txt(res)


def test_save_attachment_foreign_event_refused():
    with temp_ledger(), temp_users_dir(), \
            patched(email_client, poll=_async([]),
                    attachments_for=_async(_ATTS),
                    attachment_bytes=_async((b"x", "application/pdf"))):
        res = _run("save_attachment", {"event_id": 777777,
                                       "attachment_id": 9001})
        assert _is_err(res)


def test_non_numeric_ids_refused_honestly():
    with temp_ledger(), patched(email_client, poll=_async([]),
                                attachments_for=_async(_ATTS)):
        _seed(800001)
        res = _run("read_attachment", {"event_id": "abc",
                                       "attachment_id": 9001})
        assert _is_err(res) and "must be a number" in _txt(res)


# ---------------------------------------------------------------------------
# read_email: body, retention, key recovery
# ---------------------------------------------------------------------------

def test_read_email_body_and_attachment_ids():
    with temp_ledger(), patched(email_client, poll=_async([]),
                                attachments_for=_async(_ATTS),
                                full_message=_async(_MSG)):
        _seed(800001)
        res = _run("read_email", {"event_id": 800001})
        text = _txt(res)
        assert not _is_err(res)
        assert "the full body text" in text
        assert "attachment_id=9001" in text and "invoice.pdf" in text
        assert "boss@corp.com" in text and "Q3 numbers" in text


def test_read_email_retention_expired_is_honest_metadata():
    with temp_ledger(), patched(email_client, poll=_async([]),
                                attachments_for=_async([]),
                                full_message=_async(None)):
        _seed(800001)
        res = _run("read_email", {"event_id": 800001})
        assert not _is_err(res)
        assert "NOT RETAINED" in _txt(res)
        assert "Q3 numbers" in _txt(res)      # metadata still present


def test_read_email_recovers_key_for_pre_column_rows():
    calls = []
    with temp_ledger(), patched(
            email_client,
            poll=_async([{"event_id": 800002, "recipient_email": ADDR,
                          "message_key": "mk-recovered"}]),
            attachments_for=_async([]),
            full_message=_async_capture(_MSG, calls)):
        _seed(800002, message_key="")          # pre-message_key ledger row
        res = _run("read_email", {"event_id": 800002})
        assert not _is_err(res)
        assert calls and calls[0][0][0] == "mk-recovered"
        assert "the full body text" in _txt(res)


def test_read_email_html_only_falls_back_to_stripped_html():
    with temp_ledger(), patched(
            email_client, poll=_async([]), attachments_for=_async([]),
            full_message=_async({"body_html":
                                 "<div>only <b>html</b> here</div>"})):
        _seed(800001)
        res = _run("read_email", {"event_id": 800001})
        assert "only" in _txt(res) and "html" in _txt(res)
        assert "converted from HTML" in _txt(res)
        assert "<div>" not in _txt(res)


def test_read_email_body_truncated_at_cap():
    with temp_ledger(), \
            patched(et, BODY_CHARS=50), \
            patched(email_client, poll=_async([]), attachments_for=_async([]),
                    full_message=_async({"body_text": "x" * 500})):
        _seed(800001)
        text = _txt(_run("read_email", {"event_id": 800001}))
        assert "truncated at 50 of 500" in text


# ---------------------------------------------------------------------------
# list_email_attachments
# ---------------------------------------------------------------------------

def test_list_attachments_owned_and_foreign():
    with temp_ledger(), patched(email_client, poll=_async([]),
                                attachments_for=_async(_ATTS)):
        _seed(800001)
        res = _run("list_email_attachments", {"event_id": 800001})
        assert not _is_err(res)
        assert "attachment_id=9001" in _txt(res)
        res2 = _run("list_email_attachments", {"event_id": 999999})
        assert _is_err(res2)


def test_list_attachments_empty_mentions_retention():
    with temp_ledger(), patched(email_client, poll=_async([]),
                                attachments_for=_async([])):
        _seed(800001)
        res = _run("list_email_attachments", {"event_id": 800001})
        assert not _is_err(res)
        assert "retention" in _txt(res)


# ---------------------------------------------------------------------------
# read_attachment: clamp + honesty
# ---------------------------------------------------------------------------

def _extract_capture(calls, result=None):
    async def fn(attachment_id, max_chars):
        calls.append(max_chars)
        return result or {"success": True, "text": "extracted words",
                          "truncated": False, "original_length": 15,
                          "extraction_method": "pdfplumber"}
    return fn


def test_read_attachment_clamps_max_chars():
    calls = []
    with temp_ledger(), patched(email_client, poll=_async([]),
                                attachments_for=_async(_ATTS),
                                extract_attachment_text=_extract_capture(calls)):
        _seed(800001)
        _run("read_attachment", {"event_id": 800001, "attachment_id": 9001,
                                 "max_chars": 99999999})
        _run("read_attachment", {"event_id": 800001, "attachment_id": 9001})
        _run("read_attachment", {"event_id": 800001, "attachment_id": 9001,
                                 "max_chars": 10})
        assert calls[0] == et.ATTACH_READ_CEILING     # over-ask clamps down
        assert calls[1] == et.ATTACH_READ_DEFAULT     # default
        assert calls[2] == 1000                       # floor


def test_read_attachment_truncation_reported():
    calls = []
    result = {"success": True, "text": "t" * 20, "truncated": True,
              "original_length": 123456, "extraction_method": "pdfplumber"}
    with temp_ledger(), patched(email_client, poll=_async([]),
                                attachments_for=_async(_ATTS),
                                extract_attachment_text=_extract_capture(
                                    calls, result)):
        _seed(800001)
        text = _txt(_run("read_attachment", {"event_id": 800001,
                                             "attachment_id": 9001}))
        assert "TRUNCATED" in text and "123456" in text


def test_read_attachment_extraction_failure_honest():
    with temp_ledger(), patched(
            email_client, poll=_async([]), attachments_for=_async(_ATTS),
            extract_attachment_text=_async({"success": False,
                                            "error": "Attachment not found"})):
        _seed(800001)
        res = _run("read_attachment", {"event_id": 800001,
                                       "attachment_id": 9001})
        assert _is_err(res)
        assert "Attachment not found" in _txt(res)


# ---------------------------------------------------------------------------
# save_attachment: sandbox, traversal, ext gate, caps, retention
# ---------------------------------------------------------------------------

def test_save_attachment_writes_into_user_email_area():
    with temp_ledger(), temp_users_dir() as td, \
            patched(email_client, poll=_async([]),
                    attachments_for=_async(_ATTS),
                    attachment_bytes=_async((b"%PDF-1.4 bytes",
                                             "application/pdf"))):
        _seed(800001)
        res = _run("save_attachment", {"event_id": 800001,
                                       "attachment_id": 9001})
        assert not _is_err(res), _txt(res)
        expected_dir = os.path.join(td.path, str(UID), "email", "800001")
        files = os.listdir(expected_dir)
        assert files == ["9001__invoice.pdf"]
        with open(os.path.join(expected_dir, files[0]), "rb") as fh:
            assert fh.read() == b"%PDF-1.4 bytes"
        assert "import_documents" in _txt(res)


def test_save_attachment_path_traversal_refused():
    with temp_ledger(), temp_users_dir() as td, \
            patched(email_client, poll=_async([]),
                    attachments_for=_async(_ATTS),
                    attachment_bytes=_async((b"x", "application/pdf"))):
        _seed(800001)
        for evil in ("../../etc/passwd", "..\\..\\evil.pdf", "a/b.pdf",
                     "c:\\windows\\x.pdf"):
            res = _run("save_attachment", {"event_id": 800001,
                                           "attachment_id": 9001,
                                           "filename": evil})
            assert _is_err(res), f"{evil!r} was not refused"
        assert os.listdir(td.path) == []       # nothing was written


def test_save_attachment_ext_gate():
    exe = [{"attachment_id": 9002, "filename": "run.exe",
            "content_type": "application/octet-stream", "size": 10}]
    with temp_ledger(), temp_users_dir() as td, \
            patched(email_client, poll=_async([]),
                    attachments_for=_async(_ATTS + exe),
                    attachment_bytes=_async((b"x", "application/pdf"))):
        _seed(800001)
        res = _run("save_attachment", {"event_id": 800001,
                                       "attachment_id": 9002})
        assert _is_err(res) and "not a savable type" in _txt(res)
        res2 = _run("save_attachment", {"event_id": 800001,
                                        "attachment_id": 9001,
                                        "filename": "renamed.exe"})
        assert _is_err(res2)
        # A bare rename inherits the original's (allowed) extension.
        res3 = _run("save_attachment", {"event_id": 800001,
                                        "attachment_id": 9001,
                                        "filename": "renamed"})
        assert not _is_err(res3), _txt(res3)
        saved = os.listdir(os.path.join(td.path, str(UID), "email", "800001"))
        assert saved == ["9001__renamed.pdf"]


def test_save_attachment_size_caps():
    big_listed = [{"attachment_id": 9003, "filename": "big.pdf",
                   "content_type": "application/pdf",
                   "size": 999 * 1024 * 1024}]
    with temp_ledger(), temp_users_dir(), \
            patched(email_client, poll=_async([]),
                    attachments_for=_async(_ATTS + big_listed),
                    attachment_bytes=_async((b"y" * 2048, "application/pdf"))):
        _seed(800001)
        res = _run("save_attachment", {"event_id": 800001,
                                       "attachment_id": 9003})
        assert _is_err(res) and "save cap" in _txt(res)   # pre-fetch, listed
        with patched(et, SAVE_MAX_MB=0):
            res2 = _run("save_attachment", {"event_id": 800001,
                                            "attachment_id": 9001})
            assert _is_err(res2)                          # post-fetch, actual


def test_save_attachment_retention_expired_honest():
    with temp_ledger(), temp_users_dir() as td, \
            patched(email_client, poll=_async([]),
                    attachments_for=_async(_ATTS),
                    attachment_bytes=_async(None)):
        _seed(800001)
        res = _run("save_attachment", {"event_id": 800001,
                                       "attachment_id": 9001})
        assert _is_err(res) and "retention" in _txt(res)
        assert os.listdir(td.path) == []


# ---------------------------------------------------------------------------
# list_my_email: store query + pending merge
# ---------------------------------------------------------------------------

def _seed_history():
    _seed(810001, "processed", "boss@corp.com", "Q3 numbers",
          "2026-08-10T09:00:00+00:00")
    _seed(810002, "reply_drafted", "client@ext.com", "invoice attached",
          "2026-08-12T09:00:00+00:00")
    _seed(810003, "skipped_self", ADDR, "loop", "2026-08-13T09:00:00+00:00")
    _seed(810004, "error", "boss@corp.com", "urgent Q3 follow-up",
          "2026-08-14T09:00:00+00:00")
    _seed(810005, "processed", "noreply@shop.com", "receipt",
          "2026-08-21T09:00:00+00:00")


def test_search_filters_and_pagination():
    with temp_ledger():
        _seed_history()
        rows, total = email_store.search(ADDR, include_skipped=False)
        assert total == 4
        assert [r["event_id"] for r in rows] == [810005, 810004, 810002,
                                                 810001]
        rows, total = email_store.search(ADDR, include_skipped=True)
        assert total == 5
        rows, total = email_store.search(ADDR, sender="BOSS")
        assert total == 2
        rows, total = email_store.search(ADDR, subject_contains="q3")
        assert total == 2
        rows, total = email_store.search(ADDR, since="2026-08-14")
        assert {r["event_id"] for r in rows} == {810004, 810005}
        rows, total = email_store.search(ADDR, limit=2, offset=2,
                                         include_skipped=False)
        assert total == 4
        assert [r["event_id"] for r in rows] == [810002, 810001]
        rows, total = email_store.search("other@x.io")
        assert total == 0 and rows == []


def test_list_my_email_pages_and_hides_skipped():
    with temp_ledger(), patched(email_client, poll=_async([])):
        _seed_history()
        text = _txt(_run("list_my_email", {"limit": 2}))
        assert "4 logged row(s)" in text and "skipped_* rows hidden" in text
        assert "event_id=810005" in text and "event_id=810004" in text
        assert "event_id=810002" not in text
        assert "offset=2" in text                      # more-pages hint
        text2 = _txt(_run("list_my_email", {"limit": 2, "offset": 2}))
        assert "event_id=810002" in text2 and "event_id=810001" in text2
        text3 = _txt(_run("list_my_email", {"include_skipped": True}))
        assert "event_id=810003" in text3 and "5 logged row(s)" in text3


def test_list_my_email_merges_pending_from_live_feed():
    processed_live = {"event_id": 810005, "recipient_email": ADDR,
                      "sender_email": "noreply@shop.com", "subject": "receipt"}
    with temp_ledger(), patched(email_client,
                                poll=_async([_LIVE_MINE, _LIVE_OTHER,
                                             processed_live])):
        _seed_history()
        text = _txt(_run("list_my_email", {}))
        assert "PENDING" in text
        assert "event_id=800100" in text               # mine, unprocessed
        assert "not yours" not in text                 # other recipient
        assert text.count("event_id=810005") == 1      # already in ledger
        text2 = _txt(_run("list_my_email", {"include_pending": False}))
        assert "PENDING" not in text2
        text3 = _txt(_run("list_my_email", {"offset": 2}))
        assert "PENDING" not in text3                  # first page only


def test_list_my_email_no_address_offers_setup():
    with temp_ledger(), patched(email_client, poll=_async([])):
        email_store.delete_address(UID)
        res = _run("list_my_email", {})
        assert _is_err(res) and "setup_agent_email" in _txt(res)


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
        except Exception as e:
            failed += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    if not HAVE_SDK:
        print(f"SKIP-ALL: {_IMPORT_ERR}")
        sys.exit(0)
    sys.exit(_main())
