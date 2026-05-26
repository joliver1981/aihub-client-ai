# Operations — Human Test Plan

**Company:** Northwind Outdoor Co.
**Department:** Operations (Fulfillment, Inventory, Returns)
**Fixtures:** `fixtures/O1_inventory_turnover.xlsx` (3 tabs), `fixtures/O2_carrier_manifest.pdf` (5 pages), `fixtures/O3_returns_SOP.docx`

---

## How to run

1. Create a new agent named **`HUMAN-TEST-Operations`**.
2. Upload all three Operations fixtures. The inventory xlsx has **3 sheets** (Q1, Q2, Q3 FY25) — confirm the indexer picks up all 3 tabs.
3. Wait for indexing to complete.
4. Ask each question and score against the expected output.

---

## Test cases — O1: Inventory Turnover (multi-tab XLSX)

| # | Question | Expected output | Why this matters |
|---|---|---|---|
| O1-Q1 | *"How many warehouses are tracked in the inventory turnover file?"* | **3** — Western DC, Central DC, Eastern DC | Schema reading |
| O1-Q2 | *"Which warehouse had the highest turn on Sierra 2P Tent (TNT-2200) in Q3 FY25?"* | **Eastern DC** at **8.4** | Cross-tab lookup |
| O1-Q3 | *"What is the slowest-moving SKU in Q3 across all warehouses?"* | **APR-9001 (Discontinued Jkt)** at **0.7** turn | Filtering for min |
| O1-Q4 | *"What was the total Q3 stock value across all warehouses?"* | **$6,115,000** (sum across Western + Central + Eastern subtotals) | Multi-block aggregation |
| O1-Q5 | *"Did turnover for TNT-2200 increase or decrease at the Eastern DC from Q1 to Q3?"* | **Increased** from **6.6** (Q1) → **7.0** (Q2) → **8.4** (Q3). Trending up. | Cross-tab time-series |
| O1-Q6 | *"How many SKUs are tracked per warehouse?"* | **12** | Sheet-structure question |

## Test cases — O2: Carrier Manifest (multi-page PDF, non-repeating header)

| # | Question | Expected output | Why this matters |
|---|---|---|---|
| O2-Q1 | *"How many total shipments are on the manifest?"* | **90** | Headline summary |
| O2-Q2 | *"What was the heaviest single shipment and where did it go?"* | **142.6 lb** to **Boise, ID** via **FedEx** | Outlier identification |
| O2-Q3 | *"Which carrier had the most shipments?"* | **UPS** with **37** shipments | Group-by counting |
| O2-Q4 | *"How many international shipments were there and to which country?"* | **6** shipments, all to **Canada** | Filtered count |
| O2-Q5 | *"What is the breakdown of shipments by carrier?"* | **UPS: 37, FedEx: 28, USPS: 19, DHL: 6** | Full distribution |
| O2-Q6 | *"What facility and date is this manifest for?"* | **Eastern DC**, **14 October 2025** | Header read |
| O2-Q7 | *"Find the tracking number TR-NW-00057 — what carrier, destination and weight?"* | **FedEx**, **Boise, ID 83702**, **142.6 lb** (this is the heavy one) | Row-specific lookup on a later page |

## Test cases — O3: Returns SOP (DOCX)

| # | Question | Expected output | Why this matters |
|---|---|---|---|
| O3-Q1 | *"What is the return window for an Ecommerce order?"* | **60 days from delivery date** | Channel-specific policy |
| O3-Q2 | *"What is the return window for an in-store retail purchase?"* | **30 days from purchase** | Compare to Ecommerce |
| O3-Q3 | *"Is there a restocking fee on Wholesale returns?"* | **Yes — 15% on non-defective items** | Specific number |
| O3-Q4 | *"How long does Ecommerce refund processing take?"* | **5–7 business days** after inspection | Cycle-time fact |
| O3-Q5 | *"What is the URL of the RMA portal?"* | **rma.northwind.example** | URL extraction |
| O3-Q6 | *"Where do customers ship returns to?"* | **Northwind Returns, 4200 Industrial Dr, Reno NV 89501** | Address extraction |
| O3-Q7 | *"What is the policy on hazardous materials (e.g. camp stove fuel)?"* | **Non-returnable** | Exception lookup |
| O3-Q8 | *"What's the current trailing-90-day average for the inspection-to-refund cycle?"* | **6.1 business days** (against ≤ 7-day target) | KPI fact |

---

## Cross-document reasoning

| # | Question | Expected output |
|---|---|---|
| O-CROSS-1 | *"The SOP allows returns on items in inventory. The slowest-moving SKU APR-9001 is discontinued — is it still returnable under the SOP?"* | **Yes** — discontinued SKUs are returnable, but only within the original return window |
| O-CROSS-2 | *"Looking at the manifest, the heaviest shipment was 142.6 lb. If that customer wanted to return it as Ecommerce, would they get a prepaid label?"* | **No** — prepaid labels are auto-generated for Ecommerce returns **under 10 lb** only; this would require manual handling |

---

## Scoring table

| Test ID | Pass / Partial / Fail | Notes |
|---|---|---|
| O1-Q1 → O1-Q6 |   |   |
| O2-Q1 → O2-Q7 |   |   |
| O3-Q1 → O3-Q8 |   |   |
| O-CROSS-1 |   |   |
| O-CROSS-2 |   |   |

**Pass criteria:** ≥ 85% (20 / 23 questions). The non-repeating-header pages (3, 4, 5 of O2) are the most likely failure mode — make sure the agent can still resolve column meanings on those pages.
