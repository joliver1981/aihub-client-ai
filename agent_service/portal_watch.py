"""
Portal run watch — the hand-back -> conversation bridge (james, 2026-08-23).

The gap: when a portal tool hands a STILL-RUNNING run back to the model — a
2FA / verification take-over pause, or a run that outlived the in-tool wait —
the turn ends and nobody is left watching the run. The user finishes the
take-over in another tab, the browser service completes the task, and the
conversation never learns it (james's repro: price-list.xlsx downloaded, chat
silent) — the result was only collected if the user came back and said "done".

This module keeps a DB-backed WATCH per run (mywork.db, table portal_watches)
and ONE supervisor loop (started from main.py's lifespan; survives restarts
because the watches are rows, not tasks) that polls the Browser Use service
for every active watch:

  paused (awaiting_human)  --hand back-->  running  --finish-->  resume the chat

When the run FINISHES, the originating conversation is RESUMED with a
"[PORTAL RUN UPDATE]" turn — the same guarded resume the deferred-results path
(/api/run) uses: owned session, not in flight (busy -> wait, bounded). The
model calls check_portal_run, delivers the links (or the honest failure), and
the UI shows it live (session version bump -> /api/chat/version poll). The FYI
in My Work deep-links to the conversation. A run with no conversation to
resume into (pure headless turn) lands an FYI with staged download links
instead. A watch is DISARMED the moment any tool collects the finished result,
so nothing is ever delivered twice.

Kill switch: AGENT_PORTAL_WATCH=false (tools then behave exactly as before:
the user has to say "done").
"""

import asyncio
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from agent_config import logger
from workitem_store import DB_PATH as _DEFAULT_DB_PATH

DB_PATH = _DEFAULT_DB_PATH          # tests point this at a temp file before init()
_LOCK = threading.Lock()

ENABLED = os.getenv("AGENT_PORTAL_WATCH", "true").lower() == "true"
POLL_SECONDS = float(os.getenv("AGENT_PORTAL_WATCH_POLL_SECONDS", "3"))
# a take-over the user never completes: the browser service's own human-step
# timeout (15 min) fails the run first; this is the backstop for a run that
# somehow never reports done at all
MAX_MINUTES = int(os.getenv("AGENT_PORTAL_WATCH_MAX_MINUTES", "45"))
# how long a finished watch waits for a BUSY conversation (a turn in flight)
# before delivering into My Work instead
BUSY_WAIT_SECONDS = int(os.getenv("AGENT_PORTAL_WATCH_BUSY_WAIT_SECONDS", "600"))
GONE_STRIKES = 5                    # consecutive "no such run" polls before giving up

ACTIVE, FINISHING, DONE, DISARMED, EXPIRED, GONE = (
    "active", "finishing", "done", "disarmed", "expired", "gone")
PHASE_PAUSED, PHASE_RUNNING = "paused", "running"

_COLUMNS = ("run_id", "user_id", "session_id", "username", "name", "role",
            "tenant_id", "timezone", "mode", "label", "kind", "phase", "reason",
            "status", "strikes", "created_at", "updated_at", "handback_at",
            "done_at", "outcome", "disarm_reason", "collected_at")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    with _LOCK, _connect() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS portal_watches (
            run_id        TEXT PRIMARY KEY,
            user_id       INTEGER NOT NULL,
            session_id    TEXT,
            username      TEXT DEFAULT '',
            name          TEXT DEFAULT '',
            role          INTEGER DEFAULT 2,
            tenant_id     TEXT,
            timezone      TEXT,
            mode          TEXT DEFAULT '',
            label         TEXT DEFAULT '',
            kind          TEXT DEFAULT 'portal task',
            phase         TEXT DEFAULT 'paused',
            reason        TEXT DEFAULT '',
            status        TEXT DEFAULT 'active',
            strikes       INTEGER DEFAULT 0,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            handback_at   TEXT,
            done_at       TEXT,
            outcome       TEXT,
            disarm_reason TEXT,
            collected_at  TEXT
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_portal_watches_status "
                  "ON portal_watches(status)")
        cols = {r[1] for r in c.execute("PRAGMA table_info(portal_watches)")}
        if "collected_at" not in cols:          # additive migration for early rows
            c.execute("ALTER TABLE portal_watches ADD COLUMN collected_at TEXT")


def _row(r) -> Optional[dict]:
    return dict(r) if r is not None else None


def chat_session_for(user: dict) -> Optional[str]:
    """The conversation a finished run should land in: the chat this turn is
    chained from, else the live chat's own session; a pure headless turn
    (scheduled / email) has none -> My Work delivery. (Same derivation as
    work_tools' schedule chaining.)"""
    user = user or {}
    sid = (user.get("chat_session_id")
           or (user.get("session_id") if str(user.get("mode") or "") != "headless" else None))
    sid = str(sid or "").strip()
    safe = "".join(ch for ch in sid if ch.isalnum() or ch == "-")
    return sid if sid and safe == sid else None


def arm(run_id: str, user: dict, phase: str, label: str = "", kind: str = "portal task",
        reason: str = "") -> Optional[dict]:
    """Start (or refresh) watching a run for this turn's user. Returns the
    watch row, or None when watching is disabled / the run id is unusable."""
    if not ENABLED:
        return None
    run_id = str(run_id or "").strip()
    if not run_id or not "".join(ch for ch in run_id if ch.isalnum() or ch in "-_") == run_id:
        return None
    user = user or {}
    try:
        uid = int(user.get("user_id") or 0)
    except (TypeError, ValueError):
        uid = 0
    if not uid:
        return None
    now = _now()
    sid = chat_session_for(user)
    with _LOCK, _connect() as c:
        cur = _row(c.execute("SELECT * FROM portal_watches WHERE run_id=?", (run_id,)).fetchone())
        if cur and cur["status"] in (ACTIVE, FINISHING):
            # refresh: keep the original conversation + label, update the phase
            c.execute("UPDATE portal_watches SET phase=?, reason=?, updated_at=?, "
                      "label=COALESCE(NULLIF(label,''), ?) WHERE run_id=?",
                      (phase, str(reason or "")[:300], now, str(label or "")[:160], run_id))
        else:
            c.execute(
                "INSERT OR REPLACE INTO portal_watches (run_id, user_id, session_id, "
                "username, name, role, tenant_id, timezone, mode, label, kind, phase, "
                "reason, status, strikes, created_at, updated_at) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
                (run_id, uid, sid, str(user.get("username") or ""), str(user.get("name") or ""),
                 int(user.get("role") or 2), (str(user.get("tenant_id")) if user.get("tenant_id")
                                              not in (None, "") else None),
                 str(user.get("browser_timezone") or "") or None, str(user.get("mode") or ""),
                 str(label or "")[:160], str(kind or "portal task")[:60], phase,
                 str(reason or "")[:300], ACTIVE, now, now))
        row = _row(c.execute("SELECT * FROM portal_watches WHERE run_id=?", (run_id,)).fetchone())
    logger.info(f"portal watch armed run={run_id} user={uid} phase={phase} "
                f"chat={sid or '-'} label={label!r}")
    return row


def disarm(run_id: str, reason: str = "collected") -> bool:
    """A tool collected the finished result (or the run is gone). An ACTIVE
    watch stops here (the conversation must not be woken for something already
    delivered). A FINISHING watch belongs to the supervisor — its own wake-up
    turn is usually the collector — so it only gets `collected_at` stamped:
    the pre-turn check in _resume_conversation reads that to skip a result a
    user turn collected during the busy wait."""
    run_id = str(run_id or "").strip()
    if not run_id:
        return False
    now = _now()
    with _LOCK, _connect() as c:
        n = c.execute("UPDATE portal_watches SET status=?, disarm_reason=?, collected_at=?, "
                      "updated_at=? WHERE run_id=? AND status=?",
                      (DISARMED, str(reason or "")[:120], now, now, run_id, ACTIVE)).rowcount
        if not n:
            n2 = c.execute("UPDATE portal_watches SET collected_at=COALESCE(collected_at, ?), "
                           "disarm_reason=?, updated_at=? WHERE run_id=? AND status=?",
                           (now, str(reason or "")[:120], now, run_id, FINISHING)).rowcount
            if n2:
                logger.info(f"portal watch run={run_id}: result collected while finishing ({reason})")
            return False
    logger.info(f"portal watch disarmed run={run_id} ({reason})")
    return True


def get(run_id: str) -> Optional[dict]:
    with _LOCK, _connect() as c:
        return _row(c.execute("SELECT * FROM portal_watches WHERE run_id=?",
                              (str(run_id or ""),)).fetchone())


def list_active() -> list:
    with _LOCK, _connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM portal_watches WHERE status=? ORDER BY created_at", (ACTIVE,))]


def list_for_user(user_id: int, limit: int = 20) -> list:
    with _LOCK, _connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM portal_watches WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (int(user_id or 0), int(limit)))]


def _update(run_id: str, **fields) -> None:
    fields["updated_at"] = _now()
    cols = [k for k in fields if k in _COLUMNS]
    with _LOCK, _connect() as c:
        c.execute(f"UPDATE portal_watches SET {', '.join(k + '=?' for k in cols)} "
                  "WHERE run_id=?", [fields[k] for k in cols] + [run_id])


def _claim_finishing(run_id: str) -> bool:
    """ACTIVE -> FINISHING exactly once (the supervisor ticks every few seconds;
    a completion must fire a single delivery)."""
    with _LOCK, _connect() as c:
        n = c.execute("UPDATE portal_watches SET status=?, updated_at=? "
                      "WHERE run_id=? AND status=?", (FINISHING, _now(), run_id, ACTIVE)).rowcount
    return bool(n)


# ---------------------------------------------------------------- decision (pure)
def _age_seconds(watch: dict, now_ts: float) -> float:
    try:
        created = datetime.fromisoformat(str(watch.get("created_at")))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return now_ts - created.timestamp()
    except Exception:
        return 0.0


def decide(watch: dict, res: dict, now_ts: Optional[float] = None) -> tuple:
    """What one poll result means for a watch. Pure: returns
    (action, fields) with action in finish | gone | expire | handback | wait.
    The poll result is the Browser Use /portal/result shape that
    command_center.tools.portal_fetch.get_portal_result returns."""
    now_ts = time.time() if now_ts is None else now_ts
    res = res or {}
    if res.get("done"):
        return "finish", {}
    err = str(res.get("error") or "")
    if "404" in err or "no such run" in err.lower():
        strikes = int(watch.get("strikes") or 0) + 1
        if strikes >= GONE_STRIKES:
            return "gone", {"strikes": strikes}
        return "wait", {"strikes": strikes}
    if _age_seconds(watch, now_ts) > MAX_MINUTES * 60:
        return "expire", {}
    fields: dict = {}
    if int(watch.get("strikes") or 0):
        fields["strikes"] = 0
    if res.get("needs_human"):
        fields["phase"] = PHASE_PAUSED
        if res.get("reason"):
            fields["reason"] = str(res.get("reason"))[:300]
        return "wait", fields
    if watch.get("phase") == PHASE_PAUSED:
        # awaiting_human -> running: the user handed control back
        fields["phase"] = PHASE_RUNNING
        fields["handback_at"] = _now()
        return "handback", fields
    return "wait", fields


# ---------------------------------------------------------------- delivery
def _outcome_of(res: dict) -> tuple:
    files = [f for f in (res.get("files") or []) if f]
    ok = str(res.get("status") or "") == "ok"
    if res.get("is_upload"):
        what = "upload completed" if ok else "upload failed"
    elif files:
        what = f"{len(files)} file(s) downloaded"
    elif ok:
        what = "finished (no file — the browser agent may have read the page instead)"
    else:
        what = "failed"
    return ok, what, files


def _finished_local(watch: dict) -> str:
    import work_tools
    user = {"browser_timezone": watch.get("timezone") or None}
    zone = work_tools.default_zone_label(user)[0]
    return work_tools.fmt_local(datetime.utcnow(), zone)


def _raise_fyi(watch: dict, ok: bool, what: str, summary: str, resumed_chat: Optional[str],
               links: Optional[list] = None) -> Optional[dict]:
    import workitem_store
    label = watch.get("label") or "portal run"
    body = summary.strip() or f"The portal run {what}."
    if links:
        body += "\n\nDownloads:\n" + "\n".join(f"- {ln}" for ln in links)
    try:
        return workitem_store.create_item(
            "acknowledge",
            f"{'✓' if ok else '⚠'} Portal run {'finished' if ok else 'failed'} — {label}"[:160],
            summary=body[:6000],
            payload={"kind": "portal_run_update", "run_id": watch["run_id"], "ok": ok,
                     "outcome": what, "label": label,
                     "chat_session_id": resumed_chat},
            addressed_user=int(watch.get("user_id") or 0) or None,
            from_kind="agent_portal_watch", from_ref=str(watch["run_id"]), priority=0,
            created_by="portal_watch")
    except Exception as e:
        logger.warning(f"portal watch FYI failed run={watch.get('run_id')}: {e}")
        return None


def _deliver_to_mywork(watch: dict, res: dict) -> dict:
    """No conversation to resume into (or it could not be resumed): stage the
    files for the owner and file an FYI carrying the working links."""
    from portal_tools import _stage_files
    ok, what, files = _outcome_of(res)
    links, _paths, errors = _stage_files(watch.get("user_id"), files)
    text = f"The portal run ({watch.get('label') or 'portal run'}) {what}."
    if not ok and res.get("error"):
        text += f" Error: {str(res.get('error'))[:400]}"
    final = str(res.get("final_result") or "").strip()
    if final and not files:
        text += f"\n\nBrowser agent's note: {final[:600]}"
    if errors:
        text += "\n\nSome files could not be staged: " + "; ".join(errors)
    item = _raise_fyi(watch, ok and not errors, what, text, None, links)
    return {"ok": ok, "what": what, "links": len(links),
            "work_item_id": (item or {}).get("work_item_id")}


async def _resume_conversation(watch: dict, res: dict) -> dict:
    """Wake the originating conversation with a [PORTAL RUN UPDATE] turn: the
    model collects the run (check_portal_run) and tells the user. Waits
    (bounded) while the conversation is busy; re-checks the watch right before
    resuming so a result a tool collected meanwhile is never delivered twice."""
    import brain
    import chat_history
    sid = watch.get("session_id")
    uid = int(watch.get("user_id") or 0)
    if not sid or not chat_history.owns_session(uid, sid):
        return {"resumed": False, "why": "no owned conversation"}
    waited = 0.0
    while brain.is_inflight(sid) and waited < BUSY_WAIT_SECONDS:
        await asyncio.sleep(2.0)
        waited += 2.0
    cur = get(watch["run_id"]) or {}
    if cur.get("status") != FINISHING or cur.get("collected_at"):
        return {"resumed": False, "why": f"watch {cur.get('status')}"
                + (" collected" if cur.get("collected_at") else "") + " meanwhile"}
    if brain.is_inflight(sid):
        return {"resumed": False, "why": "conversation stayed busy"}

    ok, what, files = _outcome_of(res)
    user_ctx = {
        "user_id": uid, "role": int(watch.get("role") or 2),
        "username": watch.get("username") or f"user{uid}",
        "name": watch.get("name") or watch.get("username") or "",
        "tenant_id": watch.get("tenant_id"),
        "mode": "portal_watch",          # interactive semantics (links go to the chat)
        "chat_session_id": sid,          # chained schedules keep this thread
    }
    if watch.get("timezone"):
        user_ctx["browser_timezone"] = watch["timezone"]
    import work_tools
    zone = work_tools.default_zone_label(user_ctx)[0]
    prompt = (work_tools.now_line(zone) + "\n\n"
              + chat_history.build_portal_update_prompt(
                  watch.get("label") or "portal run", _finished_local(watch),
                  watch["run_id"], ok, what, watch.get("handback_at") is not None))
    texts, tools_run, final = [], [], {}
    brain.mark_inflight(sid)
    try:
        async for ev in brain.run_turn(prompt, sid, user_ctx, tool_scope="full"):
            if ev.get("type") == "text":
                texts.append(ev["text"])
            elif ev.get("type") == "tool":
                tools_run.append(str(ev.get("name", "")).replace("mcp__aihub__", ""))
            elif ev.get("type") in ("result", "error"):
                final = ev
    finally:
        brain.clear_inflight(sid)
    if final.get("type") == "error" and not texts and not tools_run:
        return {"resumed": False, "why": f"resume failed: {final.get('error')}"}
    chat_history.touch(uid, sid, "")          # float the conversation to the top
    brain.bump_session_version(sid)           # live UI: "this conversation changed"
    summary = "\n\n".join(texts).strip()
    _raise_fyi(watch, ok, what, summary or f"The portal run {what}.", sid)
    return {"resumed": True, "ok": ok, "what": what, "tools": tools_run[:20]}


async def _finish(watch: dict, res: dict) -> None:
    run_id = watch["run_id"]
    try:
        result = await _resume_conversation(watch, res)
        if not result.get("resumed"):
            why = result.get("why") or ""
            if "meanwhile" in why:
                # a tool already collected and delivered it (user turn during
                # the busy wait): close the watch without waking the chat
                _update(run_id, status=DISARMED, done_at=_now(),
                        outcome="collected by a tool before the wake-up")
                logger.info(f"portal watch run={run_id}: not delivered ({why})")
                return
            logger.info(f"portal watch run={run_id}: chat resume skipped ({why}) "
                        "-> My Work delivery")
            result = _deliver_to_mywork(watch, res)
        _update(run_id, status=DONE, done_at=_now(),
                outcome=(result.get("what") or "")[:200])
        logger.info(f"portal watch finished run={run_id}: {result}")
    except Exception as e:
        logger.error(f"portal watch delivery failed run={run_id}: {e}", exc_info=True)
        _update(run_id, status=DONE, done_at=_now(), outcome=f"delivery error: {e}"[:200])


async def _tick(poll_fn) -> None:
    for watch in list_active():
        run_id = watch["run_id"]
        try:
            res = await asyncio.to_thread(poll_fn, run_id, 15)
        except Exception as e:
            res = {"error": str(e)}
        action, fields = decide(watch, res)
        if action == "finish":
            if _claim_finishing(run_id):
                asyncio.get_event_loop().create_task(_finish(watch, res))
        elif action == "gone":
            _update(run_id, status=GONE, done_at=_now(),
                    outcome="run no longer known to the browser service", **fields)
            logger.warning(f"portal watch run={run_id}: gone (browser service restarted?)")
        elif action == "expire":
            _update(run_id, status=EXPIRED, done_at=_now(),
                    outcome=f"no completion within {MAX_MINUTES} min")
            logger.warning(f"portal watch run={run_id}: expired")
        else:
            if action == "handback":
                logger.info(f"portal watch run={run_id}: user handed back")
            if fields:
                _update(run_id, **fields)


async def run_forever(poll_fn=None) -> None:
    """Supervisor loop (main.py lifespan). poll_fn(run_id, timeout) -> dict;
    defaults to the CC client core's get_portal_result (lazy import so a
    missing browser client degrades to a logged error, never a dead service)."""
    if poll_fn is None:
        try:
            from command_center.tools import portal_fetch as pf
            poll_fn = pf.get_portal_result
        except Exception as e:
            logger.error(f"portal watch: browser client unavailable ({e}); supervisor idle")
            return
    logger.info(f"portal watch supervisor running (poll {POLL_SECONDS}s, "
                f"max {MAX_MINUTES} min, busy-wait {BUSY_WAIT_SECONDS}s)")
    while True:
        try:
            await _tick(poll_fn)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"portal watch tick failed: {e}", exc_info=True)
        await asyncio.sleep(POLL_SECONDS)
