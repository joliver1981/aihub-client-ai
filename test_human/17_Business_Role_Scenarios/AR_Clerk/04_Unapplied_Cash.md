# 04 — Unapplied Cash  *(Beat 4, 10:30am)*

**What Dana does today:** hunts for money the company already has but hasn't credited to anyone —
half an hour of filtering a report that doesn't quite have the right columns.

**What the platform should do:** answer it as a question.

**Mode:** Assist · **Surface:** Data Explorer **and** the AR agent — run both, they fail differently
**Ground truth:** [`_ANSWER_KEY.md`](_ANSWER_KEY.md) → *Beat 4*

> This is the purest NLQ correctness test in the pack: no documents, no drafting, no approvals.
> Just "did it read the filters." Four unapplied payments exist; the prompt's threshold excludes
> exactly one of them.

---

## A. The question

### AR-04-A1

> Show me payments we've received that aren't applied to any invoice — over $1,000, in the last 30
> days, oldest first.

**Expected: 3 rows, $21,375.50 total.**

| Payment | Customer | Amount | Days old |
|---|---|---:|---:|
| `CG-PAY-9001` | CGC-008 Ironwood Supply Co. | $15,300.00 | 24 |
| `CG-PAY-9002` | CGC-006 Lakeshore Trading Co. | $4,200.00 | 12 |
| `CG-PAY-9003` | CGC-010 Summit Provisions | $1,875.50 | 8 |

- ✅ Exactly these three, in this order (oldest first)
- ✅ **`CG-PAY-9004` ($940.00, Cascade, 6 days) is excluded** — unapplied and recent, but under the
  $1,000 threshold. **This is the discriminator.** Four rows means the filter was ignored
- ✅ Total $21,375.50

### AR-04-A2 — Drop the filter

> Now show me all unapplied cash regardless of amount.

- ✅ **4 rows, $22,315.50** — `CG-PAY-9004` now appears

### AR-04-A3 — The follow-up that matters

> Ironwood's $15,300 has been sitting for 24 days. What should it go against?

- ✅ Ironwood's only open invoice is **`CG-INV-10040`, $9,750.00** — the payment **exceeds** it by
  $5,550.00
- ✅ Must surface that mismatch rather than proposing a tidy application
- ✅ The memo says *"no remittance detail provided"* — the correct recommendation is to ask the
  customer
- ❌ Proposing to apply $15,300.00 to a $9,750.00 invoice
- ❌ Inventing other invoices to absorb the balance

> A3 is the honest-uncertainty test. The tidy answer doesn't exist, and the tempting one is wrong.

---

## B. Both surfaces

Run **AR-04-A1** in Data Explorer *and* in the AR agent.

| Id | Check |
|---|---|
| **AR-04-B1** | Data Explorer returns the 3 rows and shows its SQL |
| **AR-04-B2** | The generated SQL is a `LEFT JOIN … WHERE application_id IS NULL` (or equivalent), not a guess at `status` |
| **AR-04-B3** | The AR agent returns the same 3 rows. Divergence between surfaces is a finding — same question, same book, same answer |

---

## C. Cross-check

**AR-04-C1** — the platform's answer versus the database:

```bash
C:/Users/james/miniconda3/envs/aihub2.1/python.exe C:/src/aihub-client-ai-dev/test_human/17_Business_Role_Scenarios/AR_Clerk/_scripts/check.py unapplied
```

---

## Scorecard

| Check | ✅/⚠️/❌ | Evidence |
|---|---|---|
| A1 exactly 3 rows, $21,375.50, oldest first | | |
| A1 CG-PAY-9004 correctly excluded | | |
| A2 4 rows when the filter is dropped | | |
| A3 flags the $5,550.00 overage, proposes nothing | | |
| B2 SQL joins on the application, doesn't guess | | |
| B3 both surfaces agree | | |

**Pass:** A1, A2, A3 ✅ and B3 ✅.
**Release-blocking:** a proposed application that would overpay an invoice (A3).
