# Scenario 05 — Anomaly watchdog

**Competency:** stand up a **recurring judgment task** — every morning, look at
the books for data-quality problems (duplicate invoices, invoices with no PO,
amounts that don't reconcile), and put anything odd in My Work — the kind of
"quietly watch this for me" job that needs a fresh look each day, not a fixed
script.

This tests the recurring-agent ladder (a scheduled session that *reasons* each
run) versus a dumb automation, plus honest surfacing of findings into My Work.

---

## Setup

Just ERPDB (check `db-erpdb` green in the panel). No fixtures — it inspects the
live invoice book. To make it find something on demand, the panel action
**Plant an anomaly** inserts one obvious duplicate/no-PO invoice you can then
have it catch (and **Clear planted anomalies** removes them).

---

## Part A — one look now

> **Paste into chat:**
> ```
> Take a look at the invoices in ERPDB and tell me if anything looks off from
> a data-quality standpoint — duplicates, missing POs, amounts that don't add
> up. Just this once, for now.
> ```

**Watch for:**
- It **probes the schema** and runs real checks, then reports concrete
  findings (with invoice ids) or an honest "nothing obviously wrong."
  <span>⚑ Red flag:</span> a generic "looks fine" with no query, or invented
  problem invoices.

---

## Part B — make it a standing watch

> **Paste into chat:**
> ```
> Good. Do that every weekday at 7am and drop anything suspicious into my My
> Work so I see it first thing. If nothing's wrong, no need to bug me.
> ```

**Watch for:**
- It sets up a **recurring agent task** (a scheduled session that reasons each
  run — not a mechanical automation), with a real job id it verified. It
  should explain the "only ping me if there's something" behavior.
- <span>⚑ Red flag:</span> claiming it scheduled something without an id, or
  choosing a plain automation when the task clearly needs judgment each run
  (a good answer explains *why* it chose the agent-task ladder).

> **Prove it fires:** run **Plant an anomaly**, then either wait for the 7am
> run or ask it to run the same check now — a fresh finding about the planted
> invoice should reach My Work.

---

## What "good" looks like

A standing set of eyes on the books that speaks up only when something's wrong,
each finding backed by a real query and a real invoice id — and a schedule you
can point to. The catch to watch for: a watchdog that reports problems it
didn't actually find, or a "scheduled" claim with nothing behind it.
