# Finance — Human Test Plan

**Company:** Northwind Outdoor Co. (omni-channel retail + wholesale + ecommerce)
**Department:** Finance
**Fixtures in this folder:** `fixtures/F1_monthly_sales_by_region.xlsx`, `fixtures/F2_Q3_PnL_statement.pdf`, `fixtures/F3_vendor_payment_terms.docx`

---

## How to run

1. In the AI Hub UI, navigate to **Agent Knowledge** (or create a new Custom Agent if your install doesn't expose a default one).
2. Create a fresh agent named **`HUMAN-TEST-Finance`** so you don't pollute other agents' knowledge.
3. Upload all three Finance fixtures (`F1`, `F2`, `F3`) and wait for indexing to complete (typically 2–4 minutes for these sizes — watch the chunk count).
4. Open the chat panel for the agent and ask each question below verbatim.
5. For each question, score the answer as **PASS / PARTIAL / FAIL** against the "Expected output" column.
6. Record results in the scoring table at the bottom.

---

## Test cases — F1: Monthly Sales by Region (XLSX)

| # | Question | Expected output (key facts the answer MUST include) | Why this matters |
|---|---|---|---|
| F1-Q1 | *"Looking at the monthly sales by region spreadsheet, what was the grand total net revenue?"* | **$3,637,000.00** | Tests grand-total reading at bottom of sheet |
| F1-Q2 | *"Which region had the highest net revenue and what was it?"* | **West** with **$1,114,250** | Tests aggregation across rows |
| F1-Q3 | *"Which channel generated the most revenue?"* | **Wholesale** (~$1,384,700) closely followed by Ecommerce (~$1,362,600). Both acceptable as long as Wholesale is named #1. | Tests grouping & ranking |
| F1-Q4 | *"Which SKU family sold the most units?"* | **Apparel** with **8,980 units** | Tests grouping on a different dimension |
| F1-Q5 | *"What was the single highest-revenue line item?"* | **West / Ecommerce / Tents — 920 units, $165,600** | Tests row-level max |
| F1-Q6 | *"What reporting period does this spreadsheet cover?"* | **October 2025** | Tests reading title/metadata block |

## Test cases — F2: Q3 P&L Statement (multi-page PDF, non-repeating header)

| # | Question | Expected output | Why this matters |
|---|---|---|---|
| F2-Q1 | *"What was Northwind's net revenue for Q3 FY2025?"* | **$12,840,200** | Headline figure on page 1 |
| F2-Q2 | *"What was the total COGS for Q3?"* | **$7,959,400** | Number from inside the multi-page table |
| F2-Q3 | *"What was the gross profit and gross margin %?"* | Gross profit **$4,880,800**, gross margin **38.0%** | Tests cross-row math reasoning |
| F2-Q4 | *"What was EBITDA and EBITDA margin in Q3?"* | EBITDA **$1,314,200**, margin **10.2%** | Number from page 2 or 3 (no repeating header) |
| F2-Q5 | *"What was net income for the quarter?"* | **$742,300** | Last line of P&L table, end of multi-page block |
| F2-Q6 | *"What was the effective tax rate?"* | **24.6%** | Specific fact in narrative |
| F2-Q7 | *"There was a one-time inventory write-down. How much was it and what SKUs were involved?"* | **$180,000** in **August**, related to discontinued **SLP-1100** and **SLP-1102** Sleeping Bag SKUs | Cross-section reasoning (table line + footnote) |
| F2-Q8 | *"What share of Q3 revenue did Ecommerce contribute?"* | **38%** | Fact in executive summary |

## Test cases — F3: Vendor Payment Terms (DOCX)

| # | Question | Expected output | Why this matters |
|---|---|---|---|
| F3-Q1 | *"Which vendor has the longest payment terms?"* | **Acme Textiles** at **Net 90** | Table reasoning |
| F3-Q2 | *"Which vendor offers the highest early-pay discount?"* | **Cascade Down** at **3.5% / 10** | Table reasoning |
| F3-Q3 | *"Which vendor is single-source and what do they supply?"* | **Pacific Zipper Co.** — **zippers & sliders** | Critical-vendor fact in narrative |
| F3-Q4 | *"Which vendors invoice in non-USD currencies?"* | **Alpenwerk GmbH (EUR)** and **Mountain Films Ltd. (GBP)** | Filtered table read |
| F3-Q5 | *"What was the early-pay discount capture rate in Q3?"* | **$46,200 captured of $52,800 available — 87.5%** | Narrative paragraph |
| F3-Q6 | *"Who is the escalation contact for terms negotiation?"* | **Reilly Bauer, VP Finance** (`reilly.bauer@northwind.example`) | Role-based lookup |
| F3-Q7 | *"How many vendors are listed in total?"* | **10** | Table row count |

---

## Cross-document reasoning (harder)

These questions intentionally require the agent to read across two or more fixtures.

| # | Question | Expected output |
|---|---|---|
| F-CROSS-1 | *"In the P&L, an $180K inventory write-down was related to discontinued Sleeping Bag SKUs. The vendor relationship for sleeping bag fill — what are their payment terms and tier?"* | Vendor is **Tundra Tech**, Net 30, 2.5%/10, **Critical** tier (from F3) |
| F-CROSS-2 | *"Northwind has 4 sales regions in the monthly report. The vendor doc mentions Ocean freight is provided by Tradewind Logistics — what are their payment terms?"* | **Net 15, no early-pay discount** |

---

## Scoring table

| Test ID | Pass / Partial / Fail | Notes (what was wrong, if anything) |
|---|---|---|
| F1-Q1 |   |   |
| F1-Q2 |   |   |
| F1-Q3 |   |   |
| F1-Q4 |   |   |
| F1-Q5 |   |   |
| F1-Q6 |   |   |
| F2-Q1 |   |   |
| F2-Q2 |   |   |
| F2-Q3 |   |   |
| F2-Q4 |   |   |
| F2-Q5 |   |   |
| F2-Q6 |   |   |
| F2-Q7 |   |   |
| F2-Q8 |   |   |
| F3-Q1 |   |   |
| F3-Q2 |   |   |
| F3-Q3 |   |   |
| F3-Q4 |   |   |
| F3-Q5 |   |   |
| F3-Q6 |   |   |
| F3-Q7 |   |   |
| F-CROSS-1 |   |   |
| F-CROSS-2 |   |   |

**Pass criteria:** ≥ 85% pass (20 / 23). Below 70% indicates a real retrieval or comprehension problem and should be escalated.
