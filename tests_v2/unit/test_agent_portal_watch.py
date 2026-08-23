"""Hand-back -> conversation bridge (portal_watch, 2026-08-23) — unit tests.

  * arm/disarm/claim semantics on a temp SQLite DB: arming stores the chat to
    resume into (interactive -> session_id; headless -> none; chained headless ->
    chat_session_id), re-arming refreshes the phase but keeps the conversation,
    disarm only touches live watches, FINISHING is claimed exactly once.
  * decide(): paused -> handback -> finish; consecutive "no such run" polls ->
    gone after GONE_STRIKES (transient miss tolerated); max-age expiry; a later
    needs_human flips back to paused.
  * the supervisor tick drives a fake poller through a whole run and fires ONE
    delivery; a result a tool collected meanwhile (disarm) is never delivered.
  * chat_history: the [PORTAL RUN UPDATE] marker is built/parsed, replay tags it
    kind="portal_update" (scheduled runs keep kind="scheduled_run").
  * _resume_conversation: run_turn is stubbed — the wake-up prompt carries the
    marker + run_id, the session is held in flight during the turn, the version
    bumps, an FYI with chat_session_id is filed; a busy session is waited for;
    a not-owned session falls back to My Work delivery with staged links.
  * portal_tools._poll_run arms a watch when it hands back a paused / still
    running run and disarms it when it collects a finished one.

No live services and no LLM. Runs standalone (aihub-agent python
test_agent_portal_watch.py) or under pytest in an env with claude_agent_sdk;
without the SDK every test self-skips.
"""
import asyncio
import json
import os
import sys
import tempfile
import warnings

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))
warnings.filterwarnings("ignore", category=DeprecationWarning)

try:
    import portal_watch                 # noqa: E402
    import chat_history                 # noqa: E402
    import brain                        # noqa: E402
    import portal_tools                 # noqa: E402
    import workitem_store               # noqa: E402
    from platform_tools import CURRENT_USER  # noqa: E402
    HAVE_SDK = True
except ImportError as e:
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(
            reason=f"needs the aihub-agent env (claude_agent_sdk): {_IMPORT_ERR}")
    except ImportError:
        pass

UID = 987655   # never a real user


class patched:
    def __init__(self, obj, **attrs):
        self.obj, self.attrs, self.saved = obj, attrs, {}

    def __enter__(self):
        for k, v in self.attrs.items():
            self.saved[k] = getattr(self.obj, k)
            setattr(self.obj, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            setattr(self.obj, k, v)


def _fresh_db():
    d = tempfile.mkdtemp(prefix="agent-watch-")
    portal_watch.DB_PATH = os.path.join(d, "watch.db")
    portal_watch.init()
    return portal_watch.DB_PATH


def _user(mode="", sid="sess-aaaa-1111", chat=None):
    u = {"user_id": UID, "role": 3, "username": "watcher", "name": "Watch Er",
         "tenant_id": "t1", "browser_timezone": "America/New_York", "mode": mode,
         "session_id": sid}
    if chat:
        u["chat_session_id"] = chat
    return u


# ----------------------------------------------------------------- store
def test_arm_records_the_conversation_to_resume_into():
    _fresh_db()
    w = portal_watch.arm("run-1", _user(), "paused", label="Vantage", reason="2FA code")
    assert w["session_id"] == "sess-aaaa-1111" and w["status"] == "active"
    assert w["phase"] == "paused" and w["label"] == "Vantage" and w["reason"] == "2FA code"
    assert w["timezone"] == "America/New_York" and w["user_id"] == UID
    # pure headless turn: no conversation -> My Work delivery later
    w2 = portal_watch.arm("run-2", _user(mode="headless"), "running")
    assert w2["session_id"] is None
    # chained headless (a resumed chat): the chat id wins
    w3 = portal_watch.arm("run-3", _user(mode="headless", chat="chat-zzz"), "running")
    assert w3["session_id"] == "chat-zzz"
    assert [x["run_id"] for x in portal_watch.list_active()] == ["run-1", "run-2", "run-3"]
    assert portal_watch.arm("", _user(), "paused") is None
    assert portal_watch.arm("run-4", {"user_id": 0}, "paused") is None


def test_rearm_refreshes_phase_keeps_conversation_and_label():
    _fresh_db()
    portal_watch.arm("run-1", _user(sid="first"), "paused", label="Vantage")
    w = portal_watch.arm("run-1", _user(sid="other"), "running", label="")
    assert w["session_id"] == "first" and w["phase"] == "running" and w["label"] == "Vantage"
    assert len(portal_watch.list_active()) == 1


def test_disarm_and_claim_semantics():
    _fresh_db()
    portal_watch.arm("run-1", _user(), "paused")
    assert portal_watch.disarm("run-1", "collected") is True
    assert portal_watch.get("run-1")["status"] == "disarmed"
    assert portal_watch.disarm("run-1", "again") is False        # only live watches
    assert portal_watch.disarm("nope") is False
    portal_watch.arm("run-2", _user(), "paused")
    assert portal_watch._claim_finishing("run-2") is True
    assert portal_watch._claim_finishing("run-2") is False       # exactly once
    # a FINISHING watch belongs to the supervisor: a tool collecting the result
    # only stamps collected_at (its own wake-up turn IS the usual collector)
    assert portal_watch.disarm("run-2", "collected") is False
    w2 = portal_watch.get("run-2")
    assert w2["status"] == "finishing" and w2["collected_at"] and w2["disarm_reason"] == "collected"
    assert portal_watch.list_active() == []


def test_disabled_flag_arms_nothing():
    _fresh_db()
    with patched(portal_watch, ENABLED=False):
        assert portal_watch.arm("run-1", _user(), "paused") is None
    assert portal_watch.list_active() == []


# ----------------------------------------------------------------- decide()
def test_decide_paused_handback_finish():
    _fresh_db()
    w = portal_watch.arm("run-1", _user(), "paused")
    a, f = portal_watch.decide(w, {"done": False, "needs_human": True, "reason": "2FA"})
    assert a == "wait" and f.get("phase") == "paused" and f.get("reason") == "2FA"
    a, f = portal_watch.decide(w, {"done": False, "needs_human": False, "status": "running"})
    assert a == "handback" and f["phase"] == "running" and f["handback_at"]
    w["phase"] = "running"
    a, f = portal_watch.decide(w, {"done": False, "needs_human": False})
    assert a == "wait" and "phase" not in f
    # a second human gate later flips it back to paused
    a, f = portal_watch.decide(w, {"done": False, "needs_human": True})
    assert a == "wait" and f["phase"] == "paused"
    a, f = portal_watch.decide(w, {"done": True, "status": "ok", "files": ["x"]})
    assert a == "finish"


def test_decide_gone_after_strikes_and_expiry():
    _fresh_db()
    w = portal_watch.arm("run-1", _user(), "paused")
    for i in range(1, portal_watch.GONE_STRIKES):
        a, f = portal_watch.decide(w, {"error": "service returned 404: no such run"})
        assert a == "wait" and f["strikes"] == i
        w["strikes"] = f["strikes"]
    a, f = portal_watch.decide(w, {"error": "no such run"})
    assert a == "gone" and f["strikes"] == portal_watch.GONE_STRIKES
    # a good poll resets the strike count
    w["strikes"] = 2
    a, f = portal_watch.decide(w, {"done": False, "needs_human": True})
    assert a == "wait" and f["strikes"] == 0
    # max age
    import time
    a, f = portal_watch.decide(w, {"done": False, "needs_human": True},
                               now_ts=time.time() + portal_watch.MAX_MINUTES * 60 + 5)
    assert a == "expire"


# ----------------------------------------------------------------- prompts / replay
def test_portal_update_prompt_and_replay_tagging():
    p = chat_history.build_portal_update_prompt("Vantage", "2026-08-23 13:40 EDT", "run-9",
                                                True, "1 file(s) downloaded", True)
    assert p.startswith(chat_history.PORTAL_UPDATE_MARKER)
    assert chat_history.deferred_kind(p) == "portal_update"
    assert chat_history.deferred_kind(chat_history.build_deferred_prompt("j", "t", "x")) == "scheduled_run"
    assert chat_history.deferred_kind("hello") is None
    header, body = chat_history.split_deferred_prompt(p)
    assert header == "'Vantage' finished 2026-08-23 13:40 EDT (1 file(s) downloaded)"
    assert 'check_portal_run(run_id="run-9")' in body and "handed control back" in p
    # replay: write a transcript with both marker kinds + a normal user line
    tmp = tempfile.mkdtemp(prefix="agent-watch-replay-")
    proj = os.path.join(tmp, "projects", "ws")
    os.makedirs(proj)
    sid = "11111111-2222-3333-4444-555555555555"
    ctx = "[Context: now 2026-08-23 13:40 EDT (America/New_York)]\n\n"
    lines = [
        {"type": "user", "message": {"role": "user", "content": ctx + "download the price list"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "PAUSED — take over here: http://x/cobrowse/run-9"}]}},
        {"type": "user", "message": {"role": "user", "content": ctx + p}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "mcp__aihub__check_portal_run", "id": "t1", "input": {}},
            {"type": "text", "text": "Done — [⤓ price-list.xlsx (12 KB)](/api/files/abc)"}]}},
        {"type": "user", "message": {"role": "user", "content":
            ctx + chat_history.build_deferred_prompt("Agent: nightly", "2026-08-23 21:00 EDT", "say hi")}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}},
    ]
    with open(os.path.join(proj, sid + ".jsonl"), "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(json.dumps(ln) + "\n")
    with patched(chat_history, CLAUDE_CONFIG_DIR=tmp):
        turns = chat_history.replay(sid)
    kinds = [(t["role"], t.get("kind")) for t in turns]
    assert kinds == [("user", None), ("agent", None), ("user", "portal_update"),
                     ("agent", None), ("user", "scheduled_run"), ("agent", None)]
    assert turns[2]["header"].startswith("'Vantage' finished")
    assert turns[3]["tools"] == ["check_portal_run"] and "/api/files/abc" in turns[3]["text"]


# ----------------------------------------------------------------- supervisor tick
def test_tick_drives_a_run_to_one_delivery():
    _fresh_db()
    portal_watch.arm("run-1", _user(), "paused", label="Vantage")
    polls = iter([
        {"done": False, "needs_human": True, "reason": "2FA"},
        {"done": False, "needs_human": False, "status": "running"},     # hand-back
        {"done": True, "status": "ok", "files": ["C:/x/price-list.xlsx"], "final_result": "ok"},
        {"done": True, "status": "ok", "files": ["C:/x/price-list.xlsx"]},  # (would be a 2nd fire)
    ])
    delivered = []

    async def fake_finish(watch, res):
        delivered.append((watch["run_id"], watch["phase"], res.get("status")))
        portal_watch._update(watch["run_id"], status=portal_watch.DONE)

    async def drive():
        with patched(portal_watch, _finish=fake_finish):
            for _ in range(4):
                await portal_watch._tick(lambda rid, t: next(polls))
                await asyncio.sleep(0)     # let the spawned _finish task run
    asyncio.run(drive())
    w = portal_watch.get("run-1")
    assert w["handback_at"] and w["status"] == "done"
    assert delivered == [("run-1", "running", "ok")]     # exactly one delivery


def test_tick_gone_and_disarmed_are_never_delivered():
    _fresh_db()
    portal_watch.arm("run-gone", _user(), "paused")
    portal_watch.arm("run-coll", _user(), "paused")
    portal_watch.disarm("run-coll", "collected")
    fired = []

    async def fake_finish(watch, res):
        fired.append(watch["run_id"])

    async def drive():
        with patched(portal_watch, _finish=fake_finish):
            for _ in range(portal_watch.GONE_STRIKES + 1):
                await portal_watch._tick(lambda rid, t: {"error": "no such run"}
                                         if rid == "run-gone" else {"done": True})
                await asyncio.sleep(0)
    asyncio.run(drive())
    assert portal_watch.get("run-gone")["status"] == "gone"
    assert portal_watch.get("run-coll")["status"] == "disarmed"
    assert fired == []


# ----------------------------------------------------------------- resume path
class FakeTurn:
    """Stand-in for brain.run_turn: records the prompt/session/user, asserts the
    session is held in flight WHILE the turn runs, yields a text + result."""
    def __init__(self, text="Done — [⤓ price-list.xlsx (12 KB)](/api/files/abc)", error=None):
        self.calls, self.text, self.error = [], text, error

    async def __call__(self, prompt, session_id, user_ctx, tool_scope="full"):
        self.calls.append({"prompt": prompt, "session_id": session_id,
                           "user": dict(user_ctx), "inflight": brain.is_inflight(session_id)})
        if self.error:
            yield {"type": "error", "error": self.error, "session_id": session_id}
            return
        yield {"type": "tool", "name": "mcp__aihub__check_portal_run", "input": {}}
        yield {"type": "text", "text": self.text}
        yield {"type": "result", "session_id": session_id, "ok": True, "subtype": "success"}


def test_resume_conversation_wakes_the_chat_and_files_deep_linked_fyi():
    _fresh_db()
    sid = "sess-resume-1"
    w = portal_watch.arm("run-1", _user(sid=sid), "paused", label="Vantage")
    portal_watch._update("run-1", handback_at=portal_watch._now())
    portal_watch._claim_finishing("run-1")
    w = portal_watch.get("run-1")
    turn = FakeTurn()
    items = []
    before = brain.session_version(sid)
    res = {"done": True, "status": "ok", "files": ["C:/x/price-list.xlsx"]}
    with patched(brain, run_turn=turn), \
            patched(chat_history, owns_session=lambda uid, s: (uid == UID and s == sid),
                    touch=lambda *a, **k: None), \
            patched(workitem_store, create_item=lambda *a, **k: items.append((a, k)) or {"work_item_id": "wi-1"}):
        out = asyncio.run(portal_watch._resume_conversation(w, res))
    assert out["resumed"] is True and out["ok"] is True
    call = turn.calls[0]
    assert call["session_id"] == sid and call["inflight"] is True
    assert chat_history.PORTAL_UPDATE_MARKER in call["prompt"] and 'run_id="run-1"' in call["prompt"]
    assert call["prompt"].startswith("[Context: now")           # the clock line rides in front
    assert "handed control back" in call["prompt"]
    assert call["user"]["user_id"] == UID and call["user"]["chat_session_id"] == sid
    assert call["user"]["mode"] != "headless"                   # interactive semantics
    assert call["user"]["browser_timezone"] == "America/New_York"
    assert brain.is_inflight(sid) is False                      # released after the turn
    assert brain.session_version(sid) == before + 1             # live UI signal
    assert len(items) == 1
    a, k = items[0]
    assert a[0] == "acknowledge" and "Portal run finished" in a[1] and "Vantage" in a[1]
    assert k["payload"]["kind"] == "portal_run_update" and k["payload"]["chat_session_id"] == sid
    assert k["payload"]["run_id"] == "run-1" and "/api/files/abc" in k["summary"]
    assert k["addressed_user"] == UID


def test_resume_waits_for_a_busy_conversation_then_delivers():
    _fresh_db()
    sid = "sess-busy-1"
    portal_watch.arm("run-1", _user(sid=sid), "running")
    portal_watch._claim_finishing("run-1")
    w = portal_watch.get("run-1")
    turn = FakeTurn()
    brain.mark_inflight(sid)                      # someone else's turn in flight

    async def release_soon():
        await asyncio.sleep(2.5)
        brain.clear_inflight(sid)

    async def go():
        t = asyncio.get_event_loop().create_task(release_soon())
        out = await portal_watch._resume_conversation(w, {"done": True, "status": "ok", "files": []})
        await t
        return out
    with patched(brain, run_turn=turn), \
            patched(chat_history, owns_session=lambda uid, s: True, touch=lambda *a, **k: None), \
            patched(workitem_store, create_item=lambda *a, **k: {"work_item_id": "wi"}):
        out = asyncio.run(go())
    assert out["resumed"] is True and len(turn.calls) == 1


def test_resume_skipped_when_collected_meanwhile_or_not_owned():
    _fresh_db()
    sid = "sess-x"
    portal_watch.arm("run-1", _user(sid=sid), "running")
    portal_watch._claim_finishing("run-1")
    w = portal_watch.get("run-1")
    portal_watch.disarm("run-1", "collected")     # a tool collected it during the busy wait
    assert portal_watch.get("run-1")["status"] == "finishing"   # still the supervisor's row
    turn = FakeTurn()
    with patched(brain, run_turn=turn), patched(chat_history, owns_session=lambda uid, s: True):
        out = asyncio.run(portal_watch._resume_conversation(w, {"done": True, "status": "ok"}))
    assert out["resumed"] is False and "collected meanwhile" in out["why"] and turn.calls == []
    # not owned -> no resume (caller falls back to My Work)
    portal_watch.arm("run-2", _user(sid=sid), "running")
    portal_watch._claim_finishing("run-2")
    w2 = portal_watch.get("run-2")
    with patched(brain, run_turn=turn), patched(chat_history, owns_session=lambda uid, s: False):
        out = asyncio.run(portal_watch._resume_conversation(w2, {"done": True, "status": "ok"}))
    assert out["resumed"] is False and turn.calls == []


def test_finish_falls_back_to_mywork_with_staged_links():
    _fresh_db()
    tmp = tempfile.mkdtemp(prefix="agent-watch-file-")
    fpath = os.path.join(tmp, "price-list.xlsx")
    with open(fpath, "wb") as f:
        f.write(b"x" * 100)
    portal_watch.arm("run-1", _user(mode="headless", sid=None), "running", label="Vantage")
    portal_watch._claim_finishing("run-1")
    w = portal_watch.get("run-1")
    items = []
    with patched(portal_tools, _stage_files=lambda uid, files: (
                 [f"[⤓ {os.path.basename(p)} (100 B)](/api/files/f-{i})" for i, p in enumerate(files)],
                 list(files), [])), \
            patched(workitem_store, create_item=lambda *a, **k: items.append((a, k)) or {"work_item_id": "wi-2"}):
        asyncio.run(portal_watch._finish(w, {"done": True, "status": "ok", "files": [fpath]}))
    assert portal_watch.get("run-1")["status"] == "done"
    assert len(items) == 1
    a, k = items[0]
    assert "Portal run finished" in a[1] and "/api/files/f-0" in k["summary"]
    assert k["payload"]["chat_session_id"] is None and k["payload"]["run_id"] == "run-1"


# ----------------------------------------------------------------- tool seam
def _txt(out) -> str:
    """The tool's text (str(dict) would repr-escape apostrophes)."""
    return "".join(c.get("text", "") for c in (out or {}).get("content", []))


class FakePF:
    def __init__(self, results):
        self.results = list(results)

    def get_portal_result(self, run_id, timeout=15):
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]

    def cobrowse_link(self, run_id):
        return f"http://main/portal-workflows/cobrowse/{run_id}"


def test_poll_run_arms_on_pause_and_disarms_on_collect():
    _fresh_db()
    CURRENT_USER.set(_user(sid="sess-tool-1"))
    pf = FakePF([{"done": False, "needs_human": True, "reason": "a 2FA code"}])
    out = asyncio.run(portal_tools._poll_run(pf, "run-t1", 5, UID, label="Vantage"))
    text = _txt(out)
    assert "PAUSED" in text and "cobrowse/run-t1" in text
    assert "AUTOMATIC FOLLOW-UP IS ON" in text and "WAKE YOU IN THIS CONVERSATION" in text
    assert "When they say they're done" not in text
    w = portal_watch.get("run-t1")
    assert w and w["status"] == "active" and w["phase"] == "paused" and w["session_id"] == "sess-tool-1"
    assert w["label"] == "Vantage" and w["reason"] == "a 2FA code"
    # later the model (or the wake-up turn) collects it -> disarmed, links delivered
    pf2 = FakePF([{"done": True, "status": "ok", "files": []}])
    out2 = asyncio.run(portal_tools._poll_run(pf2, "run-t1", 5, UID))
    assert portal_watch.get("run-t1")["status"] == "disarmed"
    assert not out2.get("is_error") or "NO file" in _txt(out2)


def test_poll_run_arms_running_watch_when_budget_runs_out():
    _fresh_db()
    CURRENT_USER.set(_user(sid="sess-tool-2"))
    pf = FakePF([{"done": False, "needs_human": False, "status": "running"}])
    with patched(asyncio, sleep=_fast_sleep):
        out = asyncio.run(portal_tools._poll_run(pf, "run-t2", 1, UID, label="Vantage"))
    text = _txt(out)
    assert "NOT finished yet" in text and "AUTOMATIC FOLLOW-UP IS ON" in text
    w = portal_watch.get("run-t2")
    assert w and w["phase"] == "running" and w["status"] == "active"


def test_poll_run_headless_pause_arms_mywork_watch_and_keeps_old_text():
    _fresh_db()
    CURRENT_USER.set(_user(mode="headless", sid="headless-sess"))
    pf = FakePF([{"done": False, "needs_human": True, "reason": "2FA"}])
    with patched(portal_tools, _raise_takeover_item_if_headless=lambda *a: "\n(item raised)"):
        out = asyncio.run(portal_tools._poll_run(pf, "run-t3", 5, UID, label="Vantage"))
    text = _txt(out)
    assert "filed in the user's My Work" in text and "(item raised)" in text
    w = portal_watch.get("run-t3")
    assert w and w["session_id"] is None


async def _fast_sleep(s):
    return None


def test_poll_run_watch_disabled_keeps_old_contract():
    _fresh_db()
    CURRENT_USER.set(_user(sid="sess-tool-4"))
    pf = FakePF([{"done": False, "needs_human": True, "reason": "2FA"}])
    with patched(portal_watch, ENABLED=False):
        out = asyncio.run(portal_tools._poll_run(pf, "run-t4", 5, UID))
    text = _txt(out)
    assert "When they say they're done" in text and "AUTOMATIC FOLLOW-UP" not in text, text
    assert portal_watch.get("run-t4") is None


if __name__ == "__main__":
    if not HAVE_SDK:
        print(f"SKIP: {_IMPORT_ERR}")
        sys.exit(0)
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"FAIL {name}: {e}")
            traceback.print_exc()
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
