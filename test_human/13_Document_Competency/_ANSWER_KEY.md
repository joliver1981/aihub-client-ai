# Pack 13 — Answer Key (derived from battery.py — do not hand-edit)

## Phase A — ingestion integrity (SQL oracle over DocumentPages)

| id | fixture | pages | max blank | must contain | why |
|---|---|---|---|---|---|
| A1 | `DCT13_S001_MarketSquare_base.pdf` | 9 | 0 | Main Street; March 14, 2025 | digital base lease — fast path, zero loss, no regression |
| A2 | `DCT13_S003_a4_amendment.pdf` | 6 | 0 | April 21, 2033; FOURTH AMENDMENT | flattened amendment, 2 pages with images + 4 pure-vector — the blank-page rescue class; before the 2026-07-25 fix this stored 4 empty pages |
| A3 | `DCT13_S003_a1_amendment.pdf` | 4 | 0 | Skyline Clearance Center; FIRST AMENDMENT | FULLY flattened amendment (0 images on every page) — before the fix this stored 100% empty |
| A4 | `DCT13_S003_base_plus_a4_MERGED.pdf` | 16 | 0 | April 21, 2033; Riverdale | digital base + flattened amendment bound as ONE pdf — the mixed-packaging loss case |
| A5 | `DCT13_R001_LargeRetailLease_79pg.pdf` | 79 | 0 | SECURITY DEPOSIT | 79-page digital lease — fast path at size; also proves rescue adds no cost to normal PDFs |
| A6 | `DCT13_S002_a1_amendment.pdf` | 4 | 0 | — | second fully-flattened amendment — rescue must generalize beyond S003 |

## Phases B/C — questions and expected answers

| id | tier | mode | question | expected |
|---|---|---|---|---|
| B1 | 1 | repo | What is the security deposit for the Windy City Outlet lease? | Two months' Base Rent ($68,000) |
| B2 | 1 | repo | When does the Sunshine Outlet lease in Dallas expire? | June 30, 2028 |
| B3 | 2 | repo | One of our leases splits climate-control equipment upkeep between the parties at a $10,000 threshold — which property is that? | Pacific Heights (S011) — the lease says "HVAC", never "climate control": pure meaning-match |
| B4 | 1 | repo | Who is responsible for HVAC maintenance at the Market Square Boston store — the landlord or the tenant? | Landlord — all maintenance, repairs and replacements at Landlord's sole cost |
| B5 | 3 | repo | What is the current expiration date of the Riverdale Center lease? Check for any amendments before answering. | April 21, 2033 (Fourth Amendment supersedes the base lease's April 21, 2026). 2026-only = the pre-fix wrong answer: amendment content was silently unindexed |
| B6 | 2 | repo | What does the Harborview Grocery lease say about rooftop solar panels? | Nothing — the lease is silent on solar. Correct answer is "not addressed"; any invented clause = FAIL |
| B7 | 2 | repo | What security deposit does the Cypress Mall lease require? | None — waived based on tenant credit. Inventing a dollar amount = FAIL |
| C1 | 1 | knowledge | What is the tenant's share percentage in the Riverdale Center lease? | 11.35% (21,000 / 185,000) |
| C2 | 3 | knowledge | Go through every lease in your knowledge and tell me, store by store, whether HVAC maintenance is the landlord's responsibility, the tenant's, or split between them. | Per-store classes per FANOUT_HVAC_KEY; grades BOTH completeness (store mentioned) and correctness (right class). This is the measured FANOUT weak spot — the score is the point |
| C3 | 2 | knowledge | When does the Riverdale Center lease actually expire, taking amendments into account? | April 21, 2033 — the Fourth Amendment is attached as knowledge; connecting it is the test |
| C4 | 2 | knowledge | What does the Central Plaza lease say about who maintains the HVAC units? | Landlord — all HVAC units, maintenance and replacement |
| C5 | 3 | knowledge_after_delete | What does the Lakeside Mall lease say about HVAC responsibility? | The doc was just DELETED. Answering with its content (tenant / 60-ton units) means deleted-document vectors are still served — the is_active leak (KNOWLEDGE_FILTER_INACTIVE_VECTORS) |

## FANOUT portfolio key (C2) — HVAC class per attached knowledge doc

| store | class |
|---|---|
| Market Square | landlord |
| Harborview | split |
| Riverdale | split |
| Lakeside | tenant |
| Central Plaza | landlord |
| Peach Plaza | tenant |
| Sunset | landlord |

## Full per-lease ground truth

| store | property | expiry (base) | current expiry | HVAC | deposit |
|---|---|---|---|---|---|
| S001 | Market Square Boston | March 14, 2025 | = | landlord | — |
| S002 | Harborview Grocery | September 9, 2025 | = | split | None required (creditworthiness + parent guaranty) |
| S003 | Riverdale Center | April 21, 2026 | April 21, 2033 | split | — |
| S004 | Lakeside Mall | May 21, 2027 | = | tenant | $100,000 (Letter of Credit acceptable) |
| S005 | Central Plaza | August 2, 2031 | = | landlord | First month + security deposit due at signing |
| S006 | Windy City Outlet | January 17, 2030 | = | split | Two months' Base Rent ($68,000) |
| S007 | Sunshine Outlet | June 30, 2028 | = | tenant | Letter of Credit for $83,334 (reduces to $50,000 after Year 3) |
| S008 | Cypress Mall | December 11, 2029 | = | landlord | None (based on credit) |
| S009 | Peach Plaza | February 4, 2024 (extended in-document to February 4, 2029) | February 4, 2029 | tenant | $70,000 (2 months) |
| S010 | Bay Plaza | September 9, 2029 | = | split | $294,000 (reducing Letter of Credit) |
| S011 | Pacific Heights | March 24, 2028 | = | split | $110,000 (Letter of Credit) |
| S012 | Sunset Center | November 7, 2026 | = | landlord | $108,000 (cash) |
