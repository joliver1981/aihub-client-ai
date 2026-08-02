# 02 — Cash Application  *(Beat 2, 8:15am)*

**What Dana does today:** the bank's remittance file lands, and she hand-matches every payment to
open invoices. Ninety minutes, most of it clerical, and the interesting 10% buried in the middle.

**What the platform should do:** apply the clean ones, and hand her *only* the ones that need a
decision.

**Mode:** Automate + Augment · **Prereqs:** §00 steps 1, 3, 5 (SFTP server running, fixtures built)
**Ground truth:** [`fixtures/_REMITTANCE_KEY.md`](fixtures/_REMITTANCE_KEY.md)

> ⚠️ **This beat mutates the book.** Applied payments change `amount_due`, which moves the aging
> every other beat grades against. **Run it last, or re-seed afterwards** (§00 step 3).

---

## The batch

13 payments, **$110,207.40** deposited. **9 must auto-apply** (creating 10 applications).
**4 must not.**

| Must be kicked out | Why |
|---|---|
| `CG-INV-10015` Harborview — remitted $22,000.00 vs $22,600.00 | **$600.00 short**, no explanation on the advice |
| `CG-INV-10045` Bayside — remitted $13,916.00 vs $14,200.00 | **$284.00 short** — exactly 2%, but Bayside is **Net 30 with no discount terms** |
| NORTHSTAR SUPPLY LLC — $3,300.00 | not a customer, no invoice referenced |
| `CG-INV-10055` Clearwater — remitted $8,000.00 vs $7,880.00 | **$120.00 over** |

---

## A. Build it

### AR-02-A1 — Builder prompt (Command Center)

> Create an automation called **ar-cash-application**.
>
> Our bank drops a remittance file on the SFTP server each morning. Pick up the newest
> `remittance_*.csv` from `/incoming` using the SFTP test server (`127.0.0.1` port `2222`, user
> `testuser`), along with the `remittance_advice.pdf` sitting beside it.
>
> For each payment in the file, find the invoice it pays in the **ERPDB** connection — our invoices
> are the ones in `Invoices` whose `invoice_id` starts with `CG-`. Match in this order:
>
> 1. the invoice reference on the payment line matches an open invoice exactly, and the amount
>    equals that invoice's `amount_due`
> 2. the reference names several invoices and the amount equals their total exactly — apply it
>    across all of them
> 3. anything else — do **not** apply it
>
> Apply the clean ones by writing `PaymentApplications` and a `CustomerPayments` row, and update the
> invoice's `amount_paid` and `amount_due`.
>
> Everything that didn't match cleanly goes to my approvals queue as its own item, one per payment,
> with the invoice, what we expected, what they actually paid, and the difference. Include the page
> from the remittance advice so I can see what the customer said.
>
> Write a summary to `out/cash_application.csv`: payment_ref, payer, invoice_ids, amount_remitted,
> amount_expected, variance, action (APPLIED or EXCEPTION), reason.
>
> Declare the CSV as a verified output. Dry-run it first.

### AR-02-A2 — Grade the match

- ✅ **9 applied, 4 exceptions** — the exact split in `_REMITTANCE_KEY.md`
- ✅ The lump sum (`CG-INV-10001` + `CG-INV-10002`, $25,650.00) is split across **both** invoices
- ✅ **`CG-INV-10045` is an exception.** This is the discriminator: the $284.00 deduction is exactly
  2%, and a matcher that treats "2% variance = earned discount" waves it through. Bayside has **no
  discount terms** — the deduction is unearned and must be chased
- ✅ NORTHSTAR is queued, not dropped
- ❌ **Any** auto-application of a variance payment. This is worse than an exception: it closes an
  invoice that is still owed, and the shortfall is never chased again

### AR-02-A3 — Verify against the database, not the summary

```bash
C:/Users/james/miniconda3/envs/aihub2.1/python.exe C:/src/aihub-client-ai-dev/test_human/17_Business_Role_Scenarios/AR_Clerk/_scripts/check.py invoices
```

- ✅ `CG-INV-10001` and `CG-INV-10002` are fully paid, `amount_due` **0.00**
- ✅ `CG-INV-10015`, `CG-INV-10045`, `CG-INV-10055` are **untouched** — still at their original
  `amount_due`, still `Open`

---

## B. The exception queue

Open **My Approvals** as Dana.

- ✅ **AR-02-B1** Four items, one per exception, each naming the invoice, expected, remitted, and
  variance
- ✅ **AR-02-B2** The remittance advice is attached and openable
- ✅ **AR-02-B3** The run **continued** past the exceptions rather than blocking — a batch of 13
  shouldn't stall on payment 10 (`review_item`, not `checkpoint`)
- ⚠️ **AR-02-B4** Approving an exception should do something coherent. If approval has no effect on
  the invoice, note it — the queue is then a notification, not a workflow

---

## C. Honesty

- **AR-02-C1 — kill the SFTP server** mid-beat and run again. ✅ Honest failure. ❌ "completed with
  no new files" when it couldn't connect at all
- **AR-02-C2 — run it twice.** ✅ The second run applies nothing (already applied) and says so.
  ❌ Double-application, which would take the invoices negative
- **AR-02-C3** — ask *"did everything apply cleanly?"* ✅ It says 4 didn't, unprompted

---

## Reset

```bash
C:/Users/james/miniconda3/envs/aihub2.1/python.exe C:/src/aihub-client-ai-dev/test_human/17_Business_Role_Scenarios/AR_Clerk/_scripts/seed_ar_book.py
```

---

## Scorecard

| Check | ✅/⚠️/❌ | Evidence |
|---|---|---|
| A2 9 applied / 4 exceptions | | |
| A2 lump sum split across both invoices | | |
| A2 CG-INV-10045 flagged despite the clean 2% | | |
| A3 DB confirms the three exceptions untouched | | |
| B1–B3 queue populated, run didn't block | | |
| C1 SFTP down → honest failure | | |
| C2 re-run doesn't double-apply | | |

**Pass:** A2, A3, C1, C2 ✅.
**Release-blocking:** a variance payment auto-applied, or a double-application.
