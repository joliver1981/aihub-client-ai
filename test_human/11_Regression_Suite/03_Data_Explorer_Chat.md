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

## B. Dashboard pinning + persistence

> Why this section is not optional any more: on 2026-09-02 the table-toolbar **Pin** silently put the
> tile into the *hidden* dashboard panel — no toast, panel never opened — so "Pin" looked dead. Every
> HTTP call was a 200, so the automated gate missed it. Pack 15 now drives these clicks in a headless
> browser (`de_pin_dashboard`, `de_pin_live`); this section is the human eyes on the same flow.

Pinning has **three entry points**, and every one of them must do the same three things:
**(1)** a tile appears in the dashboard, **(2)** a toast says `… pinned to "<dashboard>"`, and
**(3)** the dashboard panel **slides open from the left** so you can see it. Nothing visible happening
is a ❌, even if the tile turns up later.

**REG-03-B1 — Table toolbar Pin.** On the A3 result table, click the small **Pin** button in the
table's own toolbar (next to **CSV**).
- ✅ All three things. The tile shows the same rows. If no dashboard was selected, the sidebar now
  lists an **Untitled Dashboard**; if a saved one was highlighted, the tile lands in *that* one.

**REG-03-B2 — Chart pin.** On the A4 chart: the thumbtack icon (Chart.js chart) or the
**Pin to Dashboard** / **Pin Chart →** button (image chart).
- ✅ All three things, and the chart tile actually renders (not a blank card).

**REG-03-B3 — Message-level "Pin Table → <name>" button** under any table answer.
- ✅ All three things; the tile lands in the dashboard named on the button.

**REG-03-B4 — Refresh keeps working for toolbar pins.** With B1's tile in the panel, click **Refresh**
in the panel header.
- ✅ Toast reads `Refreshed N of N widgets` and the toolbar-pinned table counts (it carries its SQL).

**REG-03-B5 — Persistence.** Click **Save** in the panel header, name the dashboard, reload the page,
then click it in the sidebar.
- ✅ Every tile from B1–B3 comes back (table rows, chart, image). Skip → N/A only if your build hides
  dashboards.

**REG-03-B6 — Unsaved tiles are never dropped silently.** Pin something, do **not** save, then click a
different dashboard in the sidebar (or the **+** button).
- ✅ An **Unsaved changes** dialog appears first. **Keep editing** leaves everything as it was;
  **Discard changes** proceeds. Reloading the tab with unsaved tiles triggers the browser's
  leave-page prompt; right after **Save** it does not, and switching no longer asks.

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
| B1 toolbar Pin: tile + toast + panel opens | | |
| B2 chart pin: tile + toast + panel opens | | |
| B3 "Pin Table →" button: tile + toast + panel opens | | |
| B4 Refresh counts the toolbar-pinned tile | | |
| B5 dashboard persists across reload (or N/A) | | |
| B6 switching/new/reload with unsaved tiles asks first | | |

**Pass:** A1–A6 ✅ and B1–B3 ✅. A5 confidently wrong or A6 fabricated = release-blocking
(grounding/honesty). Any of B1–B3 with "nothing visible happened" = release-blocking (the pin flow
is the whole point of the dashboards feature).
