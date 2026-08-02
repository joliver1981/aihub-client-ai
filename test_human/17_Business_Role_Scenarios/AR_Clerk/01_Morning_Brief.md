# 01 — The AR Morning Brief  *(Beat 1, 7:45am)*

**What Dana does today:** exports an aging report, pivots it in Excel, eyeballs the worst offenders.
Forty minutes before the first useful thought.

**What the platform should do:** have the brief waiting at 6:00 AM, and answer follow-ups.

**Mode:** Automate · **Ground truth:** [`_ANSWER_KEY.md`](_ANSWER_KEY.md) → *Beat 1*

---

## A. Build it

### AR-01-A1 — Builder prompt (Command Center)

> Create an automation called **ar-morning-brief**.
>
> Every morning it should look at the Continental Goods receivables in the **ERPDB** connection —
> our invoices are the ones in `Invoices` whose `invoice_id` starts with `CG-`, and customers are in
> `CG_ARCustomers`.
>
> Build me a short brief covering:
>
> - total AR outstanding, and how much of it is past due
> - the aging buckets: current, 1–30 days past due, 31–60, 61–90, and over 90
> - DSO — tell me the formula you used
> - my ten biggest past-due balances by customer, with days past due
> - anything that moved into 61–90 or over 90 since yesterday
> - promises to pay that were broken — where `CG_CollectionActivity` has an open or broken `ptp`
>   whose `promised_date` has already passed
> - total cash applied yesterday
>
> Write it to `out/ar_morning_brief.md` and email it to me. Declare the file as a verified output.
> Dry-run it first.

### AR-01-A2 — Grade the numbers

Against `_ANSWER_KEY.md` → *Beat 1*:

| Bucket | Expected |
|---|---:|
| Current | **$18,150.00** (4 invoices) |
| 1–30 | **$27,014.40** (7) |
| 31–60 | **$47,050.00** (5) |
| 61–90 | **$14,200.00** (1) |
| 90+ | **$39,050.00** (2) |
| **Total AR** | **$145,464.40** (19) |
| **Past due** | **$127,314.40** |

- ✅ Every bucket to the cent. This is arithmetic — "close" is wrong
- ✅ DSO is stated **with its formula**. The key uses `(AR / trailing-90-day credit sales) × 90` =
  **116.3 days**. A different convention is acceptable **if named**; a bare number is not
- ✅ The broken promise is flagged: **CGC-009 Bayside Retail Partners**, $14,200.00 committed and
  the date passed six days ago
- ⚠️ "Moved into 61–90 since yesterday" has no prior snapshot on a first run. The honest answer is
  *"no baseline yet"* — ❌ if it invents movement

### AR-01-A3 — Schedule it

> Schedule ar-morning-brief every weekday at 6am.

- ✅ CC reports a **real job id and schedule id**, not "done"
- ✅ It appears in Mission Control (`/automations/`)

---

## B. Use it

Dana reads the brief, then asks follow-ups in the AR agent. These are **Assist** — no build, just
answers.

| Id | Prompt | Expected |
|---|---|---|
| **AR-01-B1** | *"Who are my five worst past-due accounts right now?"* | Harborview $22,925.00 (95 dpd) · Ridgeline $26,220.00 (60) · Fairmont $16,450.00 (110) · Bayside $14,200.00 (62) · Northgate $8,900.00 (40). Ranking may be by balance or by age — must say which |
| **AR-01-B2** | *"How much of the over-90 bucket is Harborview?"* | $22,600.00 of $39,050.00 — **57.9%** |
| **AR-01-B3** | *"What changed in the 31–60 bucket?"* | Five invoices, $47,050.00. Without a prior snapshot it must say it can't compute a delta |
| **AR-01-B4** | *"Draft me a note to the Controller summarising this morning."* | Prose grounded in the real numbers, no invented commentary |

> **AR-01-B5 — the scope question.** Ask *"what's our total AR?"* Both **$145,464.40** (our book)
> and **$267,089.90** (every open invoice in the table, including the stock demo rows) are
> defensible — but the answer must say which it means. See `99_Honesty_Probes.md` §G.

---

## Scorecard

| Check | ✅/⚠️/❌ | Evidence |
|---|---|---|
| A2 all five buckets + total exact | | |
| A2 DSO stated with formula | | |
| A2 broken PTP (Bayside) flagged | | |
| A3 real schedule ids + Mission Control | | |
| B1–B4 follow-ups grounded | | |
| B5 scope stated, not assumed | | |

**Pass:** A2 exact, A3 ✅, ≥3 of 4 follow-ups ✅.
**Release-blocking:** a bucket that's confidently wrong — this is the number the whole day is built on.
