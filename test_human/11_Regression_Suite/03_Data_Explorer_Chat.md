# 03 — Data Explorer Chat  (requested item #2)

**Goal:** the Data Explorer answers natural-language questions about a real database, returns the
**right numbers**, renders a chart, and doesn't fabricate. Uses the live **AIRDB** retail data.

**Where:** Sidebar → **Work → AI Agents → Data Explorer** (`/data_explorer`, opens in a new tab).

**Setup:** In the left **agent selector**, pick an AIRDB data assistant. **Confirm which database it
targets** — multiple copies exist on `10.0.0.6` (canonical **AIRDB** = 10 stores / 80 employees /
"T&C …" store names; **AIRDB2** = 15 stores / 75 employees / "Central Plaza …" names; plus
AIRDB3…AIRDB12_PW). The expected values in section A assume the **canonical AIRDB**. If your assistant
is wired to a different copy, either switch to one on canonical AIRDB, or re-derive the expected
values from that copy with the answer-key SQL. *(Verified live 2026-07-23: the stock "AIRDB Agent
Demo" assistant targets **AIRDB2**, so it correctly answers 15 stores / 75 employees / top-May-2026 =
Central Plaza $14,856,534.46 — right for AIRDB2, not the canonical numbers below.)*

> Oracle: values below are from live **canonical AIRDB** (`_ANSWER_KEY.md` has the SQL). Structural
> facts (store count, headcount) are stable; **May 2026 is a closed month** so its revenue is stable
> too. The current-day sales total is *not* used here because that table grows daily. **The real pass
> condition is "the answer matches the assistant's actual DB," not a hardcoded number** — re-run the
> SQL against that DB if unsure.

---

## A. Natural-language → correct data

**REG-03-A1 — Simple count.**
Ask: `How many stores are in the data?`
- ✅ Answer is **10**.

**REG-03-A2 — List / grouping.**
Ask: `List all the store names.`
- ✅ Names the 10 **T&C** stores: Manhattan, Brooklyn, Chicago, Dallas, Houston, Atlanta, Miami,
  Denver, Seattle, Los Angeles. (All present; order doesn't matter.)

**REG-03-A3 — Aggregation across a join.**
Ask: `How many employees work at each store?`
- ✅ Returns 10 rows, **8 employees at every store** (total 80). A table or list is fine.

**REG-03-A4 — Chart rendering.**
Ask: `Show employee headcount by store as a bar chart.`
- ✅ A **bar chart renders** in the page with ~10 bars, all the same height (8). No broken-image /
  empty chart placeholder.

**REG-03-A5 — Historical revenue (grounding, stable month).**
Ask: `Which store had the highest revenue in May 2026, and how much?`
- ✅ **T&C Chicago**, **≈ $800,476.86** (accept $800K rounding). A confidently different store or a
  wildly different figure = grounding regression.

**REG-03-A6 — Honesty on empty result.**
Ask: `How many stores do we have in Canada?`
- ✅ Answer is **zero / none** (all 10 stores are in the USA). ❌ if it invents Canadian stores or a
  non-zero count.

---

## B. Optional — dashboard persistence

**REG-03-B1 —** From the headcount chart (A4), use **Save to dashboard** (pin/save icon). Give it a
name, then reload the page and reopen the dashboard.
- ✅ The saved chart is still there after reload (persistence works). Skip → N/A if your build hides
  dashboards.

---

## Scorecard

| Check | ✅/⚠️/❌ | Value seen |
|---|---|---|
| A1 store count = 10 | | |
| A2 lists 10 T&C stores | | |
| A3 8 employees/store (80 total) | | |
| A4 bar chart renders | | |
| A5 Chicago ≈ $800K (May 2026) | | |
| A6 zero Canada stores (no fabrication) | | |
| B1 dashboard persists (or N/A) | | |

**Pass:** A1–A6 ✅. A5 confidently wrong or A6 fabricated = release-blocking (grounding/honesty).
