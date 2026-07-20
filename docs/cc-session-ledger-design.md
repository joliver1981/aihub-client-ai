# CC Session Ledger — deterministic hidden state for the LLM

**Status:** design approved by james 2026-07-20 → implementing behind `CC_SESSION_LEDGER`.
**Origin:** james's design question after the expense-audit checkpoint tests (AIHUB-0057/0058):
the agent's visible reply is currently the ONLY durable memory between turns, so whatever the
LLM drops while composing prose (run ids, checkpoint ids) is gone — the "approve" turn could
not resume a run its own previous message had paused. Leading harnesses separate the
user-visible transcript from the model context (tool results persist for the model); the CC
rebuilds context from visible texts + a lossy session store. The ledger closes that gap.

## Principle

**The user sees what the LLM intended; the LLM additionally sees the technical facts it may
need — carried deterministically, never via its own prose.** The ledger is written by CODE
after tool calls (the LLM never writes it), injected each turn as a clearly-labeled hidden
context block, and bounded so it cannot bloat.

## What it is

A small structured dict on `CommandCenterState` (declared channel `session_ledger` — the
undeclared-channel-drop lesson applies) and persisted in the server-side session state beside
`code_flow_context` (same sticky rules in `routes/chat.py`).

```json
{
  "paused_run":        [ {"ts", "automation_id", "automation_name", "run_id",
                          "checkpoint_id", "question", "dry_run"} ],
  "automation_version":[ {"ts", "automation_id", "name", "version", "last_run_id",
                          "last_run_status"} ],
  "workflow_row":      [ {"ts", "workflow_id", "name", "readback_head"} ]
}
```

v1 kinds only — each capped at the **3 newest** entries, every string value truncated
(question 200 chars, readback_head 300). Adding a kind later = one `record()` call at the
producing tool site. Candidates for v2: artifact handles, resolved schemas.

## Write path (deterministic)

In converse, immediately after a tool result is obtained (round 1 and round N — the same two
capture sites as the continuity markers):

- `dry_run_automation` / `run_automation` result with `waiting_on_checkpoint` → record
  `paused_run` (ids + question). A terminal result for the same run **clears** matching
  `paused_run` entries (a decided/finished run must not linger as pending).
- `decide_automation_checkpoint` result → clear the matching `paused_run` entry; record the
  outcome on `automation_version.last_run_status`.
- `save_automation_code` / `create_automation` results → record `automation_version`.
- mutating workflow tools with a 🧾 read-back → record `workflow_row` (id + name + read-back
  head).

Pure helpers live in `graph/session_ledger.py` (`record`, `clear_paused_run`, `render`) so
they unit-test without the graph.

## Read path (injection)

When `CC_SESSION_LEDGER` is on and the ledger is non-empty, converse appends one block to the
SYSTEM prompt:

```
## SESSION STATE (deterministic — recorded from prior tool results; the user does NOT see
this block. These are PAST facts: verify with tools before acting where freshness matters,
and never claim an action happened THIS turn because it appears here.)
- PAUSED RUN awaiting decision: automation 'expense-audit' run f5b69a7f… checkpoint
  d93dd431… — "About to upload 11 rows…" (dry-run, 2026-07-20T01:12Z)
- automation 'expense-audit' latest saved v7; last run waiting (2026-07-20T01:12Z)
- workflow 'daily-store-headcount' row 1297 — 🧾 Read-back: … (2026-07-19T…)
```

Injection is converse-only in v1 (the composition layer, where the loss happened).
classify_intent keeps its deterministic gates; the trace JSONL already logs full llm_call
messages, so the hidden block is auditable.

## Layering (the ledger does NOT replace the pins)

1. **Ledger** — rich primary; survives normal turns; dies with session-state loss.
2. **Visible-text pins/footers** (read-back pin, session footers, NEW pause pin) — crash-safe
   backup riding the UI-round-tripped messages; recovery re-synthesizes markers from them.
3. **Self-resolving tools** — final floor: `decide_automation_checkpoint` with ids omitted
   resolves the automation's newest `waiting` run + pending checkpoint itself and READS BACK
   the question it is deciding.

The AIHUB-0058 A+B fixes (pause pin + self-resolving decide) ship in the same round as
layers 2 and 3 for the checkpoint case specifically.

## Guardrails / downsides accepted

- **Staleness:** entries are timestamped PAST facts; the block's header says to verify where
  freshness matters. Read-backs remain the only authority for persisted-state claims.
- **Fabrication interplay:** the 0048 no-tool fabrication guard is unchanged — ledger facts
  are prior state; claiming them as this-turn actions still trips the correction footer.
- **Size:** ≤3 entries/kind, hard truncation ⇒ ~1–2KB worst case.
- **Session-store fragility transfers:** accepted — that's why layers 2–3 exist.
- **Injection surface:** entries contain tool-derived text (checkpoint questions can embed
  user/script text) — values are truncated and the block instructs the model these are data,
  not instructions.

## Flag & rollback

`CC_SESSION_LEDGER` env, **default ON** (prior practice: flags default true with a verified
instant-off). OFF ⇒ no injection and no recording; all pre-existing behavior (pins, recovery,
markers) is unaffected — the ledger is purely additive.

## Test plan

- `session_ledger.py` unit tests: record caps (oldest evicted), truncation, clear-on-decide,
  render shape incl. the PAST-facts header, empty-ledger renders nothing.
- Converse wiring source contracts: record sites at both tool rounds; system-prompt injection
  gated on the flag; chat.py persistence beside `code_flow_context`.
- A: pause pin appended deterministically post-compose (idempotent; carries both ids).
- B: decide tool with omitted ids resolves newest waiting run + reads back the question;
  ambiguity (no waiting run / multiple automations) returns an honest ask, never a guess.
- Flag-off: no injection, no recording, suites unchanged.
- Live retest (board task): james's transcript — build → pause (ids visible in the pin) →
  "approve" resumes even in a FRESH chat (layer 3), ledger block visible in the trace.
