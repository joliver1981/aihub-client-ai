"""Unread markers on History (james 2026-09-13 — replaced the "N results were
added to your conversations" toast) — unit tests.

  * chat_history: additive result_at / opened_at ledger columns (init migrates
    an existing ledger in place, idempotently). touch(result=True) = the SERVICE
    appended a deferred result -> unread; touch(opened=True) (the user's own
    turn) and mark_opened (history replay) = the owner read it. Unread means
    "a result landed after the last open"; list_sessions flags each row and
    unread_count feeds the badge. Rows from before the columns existed are
    never unread (no backfill of old FYIs).
  * GET /api/chat/history returns the per-conversation flag + the count;
    GET /api/chat/history/<sid> (what the UI opens a conversation with) marks
    it opened — owner only (a non-owner is a 404 and marks nothing).
  * Both producers of deferred turns record the result on the ledger:
    /api/run's resume path and portal_watch's resume path.

Real SQLite in a temp file; no live services and no LLM. Runs standalone
(aihub-agent python test_agent_history_unread.py) or under pytest in an env with
claude_agent_sdk; in an env WITHOUT the SDK (main-app pytest sweep) every test
self-skips.
"""
import asyncio
import os
import sqlite3
import sys
import tempfile
import warnings

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

warnings.filterwarnings("ignore", category=DeprecationWarning)
try:
    import brain                       # noqa: E402
    import chat_history                # noqa: E402
    import main                        # noqa: E402
    import portal_watch                # noqa: E402
    from fastapi.testclient import TestClient  # noqa: E402
    HAVE_SDK = True
except ImportError as e:               # main-env pytest sweep: no claude_agent_sdk
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(
            reason=f"needs the aihub-agent env (claude_agent_sdk): {_IMPORT_ERR}")
    except ImportError:
        pass

UID, OTHER = 987654, 987655            # never real users on this box
SID = "aaaaaaaa-1111-2222-3333-444444444444"


class patched:
    """Set attrs on an object for the duration of a block, then restore."""

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


class ledger:
    """A fresh temp ledger (chat_history.DB_PATH) for the duration of a block."""

    def __enter__(self):
        self.path = os.path.join(tempfile.mkdtemp(prefix="agent-unread-"), "mywork.db")
        self._p = patched(chat_history, DB_PATH=self.path)
        self._p.__enter__()
        chat_history.init()
        return self

    def __exit__(self, *exc):
        return self._p.__exit__(*exc)


def _row(path, sid):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    try:
        r = c.execute("SELECT * FROM chat_sessions WHERE session_id = ?", (sid,)).fetchone()
        return dict(r) if r else {}
    finally:
        c.close()


def _user(uid=UID):
    return {"user_id": uid, "role": 3, "username": "unread-unit", "name": "", "tenant_id": ""}


# ------------------------------------------------------------------ ledger

def test_init_migrates_an_existing_ledger_and_old_rows_are_never_unread():
    path = os.path.join(tempfile.mkdtemp(prefix="agent-unread-old-"), "mywork.db")
    c = sqlite3.connect(path)
    c.executescript(f"""
        CREATE TABLE chat_sessions (
            session_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT DEFAULT '',
            turns INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO chat_sessions VALUES ('old-1', {UID}, 'old chat', 3,
            '2026-08-01T00:00:00+00:00', '2026-08-02T00:00:00+00:00');""")
    c.commit()
    c.close()
    with patched(chat_history, DB_PATH=path):
        chat_history.init()
        chat_history.init()                                   # idempotent
        cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(chat_sessions)")}
        assert {"result_at", "opened_at"} <= cols
        rows = chat_history.list_sessions(UID)
        assert [r["session_id"] for r in rows] == ["old-1"]
        assert rows[0]["unread"] is False and rows[0]["result_at"] is None
        assert chat_history.unread_count(UID) == 0
        chat_history.touch(UID, "old-1", "", result=True)     # a result lands on an OLD conversation
        assert chat_history.unread_count(UID) == 1
        assert _row(path, "old-1")["title"] == "old chat"     # touch never rewrites the title


def test_result_marks_unread_until_the_owner_opens_it():
    with ledger() as L:
        chat_history.touch(UID, SID, "first message", opened=True)   # the user's own turn
        assert chat_history.unread_count(UID) == 0
        assert chat_history.list_sessions(UID)[0]["unread"] is False

        chat_history.touch(UID, SID, "", result=True)                # a deferred result lands
        s = chat_history.list_sessions(UID)[0]
        assert s["unread"] is True and s["turns"] == 2 and s["title"] == "first message"
        assert chat_history.unread_count(UID) == 1

        chat_history.mark_opened(OTHER, SID)                         # not the owner: no effect
        assert chat_history.unread_count(UID) == 1
        chat_history.mark_opened(UID, SID)                           # the owner opens it
        assert chat_history.unread_count(UID) == 0
        assert chat_history.list_sessions(UID)[0]["unread"] is False

        chat_history.touch(UID, SID, "", result=True)                # another result, later
        assert chat_history.list_sessions(UID)[0]["unread"] is True
        chat_history.touch(UID, SID, "reply", opened=True)           # the user replies in it
        assert chat_history.unread_count(UID) == 0

        chat_history.touch(UID, SID, "", result=True)                # a result AFTER that reply
        assert chat_history.unread_count(UID) == 1
        r = _row(L.path, SID)
        assert r["opened_at"] and r["result_at"] and r["result_at"] > r["opened_at"]
        assert r["turns"] == 5

        # unknown / empty ids never raise
        chat_history.mark_opened(UID, "nope")
        chat_history.mark_opened(UID, "")
        chat_history.touch(UID, "", "", result=True)
        assert chat_history.unread_count(UID) == 1


def test_unread_is_per_user_and_counts_every_conversation():
    with ledger():
        chat_history.touch(UID, "a", "one", opened=True)
        chat_history.touch(UID, "b", "two", opened=True)
        chat_history.touch(OTHER, "c", "theirs", opened=True)
        chat_history.touch(UID, "a", "", result=True)
        chat_history.touch(UID, "b", "", result=True)
        chat_history.touch(OTHER, "c", "", result=True)
        assert chat_history.unread_count(UID) == 2
        assert chat_history.unread_count(OTHER) == 1
        assert {s["session_id"] for s in chat_history.list_sessions(UID) if s["unread"]} == {"a", "b"}
        chat_history.mark_opened(UID, "a")
        assert chat_history.unread_count(UID) == 1 and chat_history.unread_count(OTHER) == 1


# ------------------------------------------------------------------ routes

def test_history_route_reports_unread_and_replay_marks_opened():
    with ledger() as L:
        chat_history.touch(UID, "s-2", "other", opened=True)
        c = sqlite3.connect(L.path)                    # age it: updated_at is second-precision
        c.execute("UPDATE chat_sessions SET updated_at = '2026-01-01T00:00:00+00:00' "
                  "WHERE session_id = 's-2'")
        c.commit()
        c.close()
        chat_history.touch(UID, SID, "hello", opened=True)
        chat_history.touch(UID, SID, "", result=True)
        with patched(main, _verify_request=lambda r: _user()), \
             patched(chat_history, replay=lambda sid, max_turns=400: [{"role": "user", "text": "hello"}]):
            client = TestClient(main.app)          # no lifespan (no pollers started)
            d = client.get("/api/chat/history").json()
            assert d["unread"] == 1
            assert {s["session_id"]: s["unread"] for s in d["sessions"]} == {SID: True, "s-2": False}
            assert d["sessions"][0]["session_id"] == SID           # the result floated it to the top

            # someone else replaying it: 404 and NOT marked opened
            with patched(main, _verify_request=lambda r: _user(OTHER)):
                assert TestClient(main.app).get(f"/api/chat/history/{SID}").status_code == 404
            assert chat_history.unread_count(UID) == 1

            # the owner opens it — the history click and the live poll both fetch this
            r = client.get(f"/api/chat/history/{SID}")
            assert r.status_code == 200 and r.json()["turns"]
            d2 = client.get("/api/chat/history").json()
            assert d2["unread"] == 0 and not any(s["unread"] for s in d2["sessions"])


# --------------------------------------------------------------- producers

def test_run_resume_records_the_result_on_the_ledger():
    """/api/run resuming the scheduling conversation = producer #1."""
    touched = []

    def fake_run_turn(prompt, session_id, user_ctx, tool_scope="full"):
        async def gen():
            yield {"type": "text", "text": "done"}
            yield {"type": "result", "session_id": session_id, "ok": True, "subtype": "success"}
        return gen()

    with patched(main, run_turn=fake_run_turn, _service_key_ok=lambda r: True), \
         patched(main.workitem_store, create_item=lambda verb, title, **kw: {"work_item_id": "wi-1"}), \
         patched(chat_history, owns_session=lambda uid, sid: True,
                 touch=lambda uid, sid, msg, **kw: touched.append((uid, sid, kw))):
        r = TestClient(main.app).post("/api/run", json={
            "prompt": "say ok", "user_id": UID, "role": 3, "username": "unread-unit",
            "job_name": "Agent: unread unit", "session_id": SID})
    assert r.status_code == 200, r.text
    assert r.json()["resumed_chat"] is True
    assert touched == [(UID, SID, {"result": True})]
    assert not brain.is_inflight(SID)


def test_portal_watch_resume_records_the_result_on_the_ledger():
    """portal_watch waking the conversation after a hand-off finished = producer #2."""
    touched, fyi = [], []

    def fake_run_turn(prompt, session_id, user_ctx, tool_scope="full"):
        async def gen():
            yield {"type": "text", "text": "collected"}
            yield {"type": "result", "session_id": session_id, "ok": True, "subtype": "success"}
        return gen()

    watch = {"run_id": "run-unread-1", "session_id": SID, "user_id": UID, "role": 3,
             "username": "unread-unit", "label": "unit portal", "handback_at": None,
             "status": portal_watch.FINISHING, "collected_at": None}
    with patched(brain, run_turn=fake_run_turn), \
         patched(portal_watch, get=lambda run_id: dict(watch),
                 _raise_fyi=lambda *a, **k: fyi.append(a) or None), \
         patched(chat_history, owns_session=lambda uid, sid: True,
                 touch=lambda uid, sid, msg, **kw: touched.append((uid, sid, kw))):
        res = asyncio.run(portal_watch._resume_conversation(watch, {"status": "ok", "files": ["x.xlsx"]}))
    assert res.get("resumed") is True, res
    assert touched == [(UID, SID, {"result": True})]
    assert fyi and fyi[0][4] == SID                         # the FYI still deep-links the chat


if __name__ == "__main__":
    if not HAVE_SDK:
        print(f"SKIP: {_IMPORT_ERR}")
        sys.exit(0)
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:                    # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {e!r}")
    sys.exit(1 if failed else 0)
