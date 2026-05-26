# Planning — Human Test Plan

**Company:** Northwind Outdoor Co.
**Department:** Planning (Demand Planning, S&OP, Capacity)
**Fixtures:** `fixtures/P1_demand_forecast.xlsx`, `fixtures/P2_annual_SOP.pdf` (multi-page capacity table), `fixtures/P3_capacity_policy.docx`

---

## How to run

1. Create a new agent named **`HUMAN-TEST-Planning`**.
2. Upload all three Planning fixtures and wait for indexing.
3. Ask each question and score.

---

## Test cases — P1: 12-Month Demand Forecast (XLSX)

| # | Question | Expected output | Why this matters |
|---|---|---|---|
| P1-Q1 | *"What's the total 12-month forecast across all families and regions?"* | **1,397,000 units** (Grand Total) | Bottom of file |
| P1-Q2 | *"Which region has the highest 12-month forecast?"* | **West** with **488,950 units** | Aggregation |
| P1-Q3 | *"What's the peak month for Tents demand and the units in that month?"* | **July** with **39,600 units** | Seasonality detection |
| P1-Q4 | *"What's the lowest-demand month overall across all families?"* | **February** with **68,800 units** | Trough detection |
| P1-Q5 | *"What YoY growth assumption did the forecast use?"* | **6.5%** | Header metadata |
| P1-Q6 | *"What is the forecast horizon — start and end?"* | **November 2025 – October 2026** | Date range |

## Test cases — P2: Annual S&OP Plan (PDF, multi-page)

| # | Question | Expected output | Why this matters |
|---|---|---|---|
| P2-Q1 | *"What is the total planned production for FY2026?"* | **1,805,000 units** | Headline number |
| P2-Q2 | *"What share of annual production happens in Q4?"* | **28%** | Quarterly mix |
| P2-Q3 | *"What is the bottleneck process and where?"* | **Tent assembly on the Reno line** | Constraint identification |
| P2-Q4 | *"What is the target capacity utilization?"* | **85%** with 15% headroom | Policy number |
| P2-Q5 | *"What is the Reno tent line's quarterly capacity?"* | **130,000 units / quarter** | Capacity spec |
| P2-Q6 | *"In which quarter is the Tent assembly line loaded most heavily, and to what %?"* | **Q3** at **92% utilization** | Cross-section reasoning |
| P2-Q7 | *"Who owns the S&OP plan?"* | **Avery Chen, VP Planning** | Owner field |
| P2-Q8 | *"What mitigations are proposed if Tent capacity is binding?"* | Adding a **third shift for 6 weeks (+24,000 units)** OR **outsourcing 8,000 units to the Tijuana partner facility** | Options analysis |
| P2-Q9 | *"What FY2026 channel mix is planned for Ecommerce?"* | **39%** of total volume (up from 36%) | Channel mix |
| P2-Q10 | *"What peak-week order rate is Fulfillment preparing for?"* | **18,500 orders/day** in early December | Operational target on a later page |

## Test cases — P3: Capacity & Replenishment Policy (DOCX)

| # | Question | Expected output | Why this matters |
|---|---|---|---|
| P3-Q1 | *"What service-level targets are set for A, B, C class SKUs?"* | A: **95%**, B: **90%**, C: **85%** | Tier policy |
| P3-Q2 | *"What is the safety-stock formula?"* | **SS = z × σ_demand × √(lead_time)** | Formula extraction |
| P3-Q3 | *"What z-score is used for a 95% service level?"* | **1.65** | Lookup value |
| P3-Q4 | *"What is the standard lead time for a domestic supplier (truck mode)?"* | **14 days** plus a **+3 day** variance buffer | Combined fact |
| P3-Q5 | *"What is the standard lead time for ocean freight from Asia?"* | **35 days** plus a **+7 day** variance buffer | Compare to domestic |
| P3-Q6 | *"When is the weekly replenishment review held and when are POs released?"* | Review **Monday**, proposal generated **Tuesday**, POs released by close of business **Wednesday** | Process cadence |
| P3-Q7 | *"What minimum forward cover does Ecommerce maintain at Eastern DC?"* | **21 days** | Channel rule |
| P3-Q8 | *"What is the forecast accuracy (MAPE) target for A-class SKUs?"* | **25%** | KPI |
| P3-Q9 | *"What is the inventory-turns target across all DCs?"* | **6.5x** | KPI |
| P3-Q10 | *"How are discontinued SKUs handled in replenishment?"* | **Run-down only — no replenishment** | Exception |

---

## Cross-document reasoning

| # | Question | Expected output |
|---|---|---|
| P-CROSS-1 | *"Looking at the demand forecast, July is the peak for Tents at 39,600 units. The S&OP plan caps Reno Tent line capacity at 130,000 per quarter. Is that month's demand absorbable within the Q3 plan?"* | Yes — Q3 (Jul/Aug/Sep) total demand for Tents is roughly the 39,600 July peak plus shoulder months ≈ 80,000-100,000 units, well under 130,000 capacity. Plan allocates **124,000 units** to Tents in Q3 FY26. |
| P-CROSS-2 | *"The capacity policy says A-class SKUs need 95% fill rate with z=1.65. If average demand is 1,000/week with σ=200 and lead time is 4 weeks (Reno production at 21 days ~ 3 weeks, plus buffer), what's the safety stock?"* | SS = 1.65 × 200 × √4 = 1.65 × 200 × 2 = **660 units**. (Tester should verify the agent can both retrieve the formula AND apply it.) |

---

## Scoring table

| Test ID | Pass / Partial / Fail | Notes |
|---|---|---|
| P1-Q1 → P1-Q6 |   |   |
| P2-Q1 → P2-Q10 |   |   |
| P3-Q1 → P3-Q10 |   |   |
| P-CROSS-1 |   |   |
| P-CROSS-2 |   |   |

**Pass criteria:** ≥ 85% (24 / 28 questions). P-CROSS-2 is a stretch — partial credit if agent retrieves formula but doesn't compute, or vice versa.
