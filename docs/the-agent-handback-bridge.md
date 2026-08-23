# The Agent — portal hand-back → conversation bridge

**Shipped 2026-08-23** (james's report: the 2FA take-over replaced the chat tab,
and after the hand-back the conversation never learned the run had finished).

## The two symptoms and what was actually wrong

1. **Take-over link replaced the conversation tab.** DOMPurify 3.1.6 strips
   `target` and marked never emits one, so every markdown link The Agent
   rendered navigated the current tab. Fixed in `static/index.html`
   (`newTabLinks()` after sanitizing + a capture-phase click safety net for any
   other anchor). Pack 20 **UI-1** (`ui_smoke_links.py`, headless Chromium on
   the real page) pins it.
2. **The conversation did not pick up the result.** The model's turn ended at
   "let me know once you've handed back"; nothing watched the run afterwards.
   The browser service DID finish the job (james's run `5f36f870…` downloaded
   `price-list.xlsx`) but the result was only collected if the user came back
   and said "done". This document covers the fix for that.

## Design — `agent_service/portal_watch.py`

```
portal_fetch / check_portal_run
   └─ _poll_run hands a STILL-RUNNING run back to the model
        (PAUSED for 2FA, or outlived the in-tool wait)
        └─ portal_watch.arm(run_id, user, phase, label)   → row in mywork.db
                                                             (table portal_watches)
supervisor loop (main.py lifespan, every 3 s, DB-backed → survives restarts)
   └─ get_portal_result(run_id) per active watch → decide():
        needs_human            → phase paused          (wait)
        paused → running       → phase running, handback_at   ("the user handed back")
        done                   → FINISHING (claimed once) → _finish()
        5× "no such run"       → gone   (browser service restarted)
        > AGENT_PORTAL_WATCH_MAX_MINUTES → expired
_finish()
   ├─ conversation to resume into (interactive chat, or the chat a headless
   │  turn was chained from)  → _resume_conversation():
   │      wait while the chat is busy (bounded), re-check the watch, then
   │      brain.run_turn(sid, "[PORTAL RUN UPDATE] 'label' finished <local time>
   │                           (1 file(s) downloaded) — … call check_portal_run …")
   │      → the model collects + delivers the /api/files link (or the failure)
   │      → chat_history.touch, brain.bump_session_version(sid), FYI
   │        (payload.kind=portal_run_update, chat_session_id deep link)
   └─ no conversation (pure headless turn) / resume impossible
          → stage the files → FYI in My Work with working links
```

**Never twice.** `_poll_run` calls `portal_watch.disarm(run_id)` the moment a
tool collects a finished result: an ACTIVE watch is closed (the chat is not
woken for something already delivered); a FINISHING watch — the supervisor's
own wake-up turn is usually the collector — only gets `collected_at` stamped,
and `_resume_conversation` re-reads that right before resuming (a user turn
that collected during the busy wait wins). `/api/chat` already waits (bounded,
SSE `status`) while such a turn is in flight.

**Model guidance.** The PAUSED / still-running tool texts now say AUTOMATIC
FOLLOW-UP IS ON and tell the model to say the result will appear *here*; the
PORTALS prompt section and the `aihub-portals` skill say the same. When the
watch could not be armed (flag off, no user id) the old "say when you're done"
contract is used verbatim.

**Live UI.** `GET /api/chat/version?session_id=` → `{version, inflight}`.
The page polls it every 4 s while idle on a conversation: `inflight` shows
"⏳ The Agent is adding a result to this conversation…", a version change
re-renders the thread from the replay (`renderTurns`) and adds an "↻ Updated"
note; the `[PORTAL RUN UPDATE]` line replays as a **Portal run update** bubble
(header only — its body is the instruction to the model). `/api/run`'s resumed
scheduled results bump the same version, so they appear live too. The My Work
toast covers `portal_run_update` and is suppressed when that conversation is
already open here. `GET /api/portal/watches` lists the user's watches.

## Knobs

| env | default | meaning |
|---|---|---|
| `AGENT_PORTAL_WATCH` | `true` | kill switch (tools revert to the old contract) |
| `AGENT_PORTAL_WATCH_POLL_SECONDS` | `3` | supervisor cadence |
| `AGENT_PORTAL_WATCH_MAX_MINUTES` | `45` | backstop expiry for a run that never reports done |
| `AGENT_PORTAL_WATCH_BUSY_WAIT_SECONDS` | `600` | how long a finished watch waits for a busy chat before My Work delivery |

## Proof

* `tests_v2/unit/test_agent_portal_watch.py` — 17 tests (aihub-agent env; stubbed
  brain / poller / work-item store; temp SQLite).
* Pack 20 **PT-13** (live): a real chat turn against the Vantage test portal's
  `/login-2fa` → PAUSED + take-over link + armed watch; `cobrowse_human.py`
  (headless Chromium on the REAL co-browse page, aihub2.1 env) types `123456`
  and clicks Hand back; the watch records the hand-back, the run finishes, the
  conversation is woken, the model delivers the link (bytes served), version
  bumps, deep-linked FYI. First live result 2026-08-23: *"Done — here's the
  file you asked for: [⤓ price-list.xlsx (9.7 KB)](/api/files/…)"* with no
  user message in between.
* Pack 20 **UI-1** now also covers the live-update rendering (20/20).

## Known limits / next

* `run_portal_workflow` (deterministic replay) still blocks in-tool for up to
  `AGENT_PORTAL_WORKFLOW_TIMEOUT` and surfaces no take-over link in chat — a
  `verify_code` human step inside a saved workflow reaches the user only via
  the take-over email / My Work item (CC parity). Making replay async through
  the same watch is the natural follow-up.
* Session versions are in-memory (reset on restart): the UI re-baselines on
  the next poll; nothing is lost (the transcript is the truth).
* The Run Monitor / co-browse page says "Any result will appear where you
  started it" — now true for The Agent.
