# IT — Human Test Plan

**Company:** Northwind Outdoor Co.
**Department:** IT (Asset Management, Security, Integrations)
**Fixtures:** `fixtures/I1_asset_inventory.xlsx`, `fixtures/I2_security_audit.pdf` (3 pages, multi-page findings table), `fixtures/I3_integration_runbook.docx`

---

## How to run

1. Create a new agent named **`HUMAN-TEST-IT`**.
2. Upload all three IT fixtures and wait for indexing.
3. Ask each question and score.

---

## Test cases — I1: Asset Inventory (XLSX)

| # | Question | Expected output | Why this matters |
|---|---|---|---|
| I1-Q1 | *"How many total assets are in the inventory?"* | **70** | Row count |
| I1-Q2 | *"What is the total CapEx represented by the asset inventory?"* | **$341,420** | Sum of cost column |
| I1-Q3 | *"What is the oldest active asset and when was it purchased?"* | **NW-SRV-0007** — **HPE ProLiant DL380** — purchased **14 March 2017** at **HQ Reno** | Min on date column, multiple-field answer |
| I1-Q4 | *"Which site holds the most assets?"* | **HQ Reno** with **30 assets** | Group-by count |
| I1-Q5 | *"How many servers are in the inventory?"* | **13** | Type filter count |
| I1-Q6 | *"What's the breakdown of assets by site?"* | HQ Reno **30**, Western DC **12**, Central DC **11**, Eastern DC **11**, Retail Hub Denver **6** | Full distribution |

## Test cases — I2: Security Audit Report (PDF, multi-page findings)

| # | Question | Expected output | Why this matters |
|---|---|---|---|
| I2-Q1 | *"Who audited Northwind Outdoor and what was the period?"* | **Bluefield Security Partners** (lead auditor **Jordan Park, CISSP**), audit period **1 August 2025 – 15 September 2025** | Header read |
| I2-Q2 | *"How many findings did the audit produce in total?"* | **24** total findings | Headline figure |
| I2-Q3 | *"How many were critical, high, medium, and low?"* | **3 critical, 7 high, 10 medium, 4 low** | Severity breakdown |
| I2-Q4 | *"What is the highest-risk system and which findings target it?"* | **ERP Production** (asset **NW-SRV-0007**); findings **F-001** (EOL Windows Server 2012 R2) and **F-002** (reused local admin password) | Cross-row reasoning |
| I2-Q5 | *"Describe finding F-003 and its current status."* | **EDI Gateway** — SFTP listener accepts weak HMAC-SHA1 in violation of policy. Status: **In progress** | Specific row lookup |
| I2-Q6 | *"How many findings are still in 'Open' status?"* | **15** open (count Open rows across all 3 pages) | Count across multi-page table |
| I2-Q7 | *"What was the phishing test click rate?"* | **8.5%** (across 412 employees) | Specific number in methodology section |
| I2-Q8 | *"When is the company committed to retiring Windows Server 2012 R2?"* | **31 December 2025** | Commitment in remediation section (last page) |

## Test cases — I3: ERP-Shopify Integration Runbook (DOCX)

| # | Question | Expected output | Why this matters |
|---|---|---|---|
| I3-Q1 | *"What is the integration's SLA?"* | **99.5% monthly uptime, mean-time-to-detect ≤ 5 minutes** | Definition lookup |
| I3-Q2 | *"How often do orders sync from Shopify to ERP?"* | **Every 10 minutes** | Cadence fact |
| I3-Q3 | *"How are refunds synced?"* | **Real-time webhook** (Shopify → ERP) | Different stream cadence |
| I3-Q4 | *"What's the P1 alert threshold for the DLQ?"* | **DLQ depth ≥ 25 messages** (or failure rate ≥ 5% over 15 min) | Numeric threshold |
| I3-Q5 | *"Who is the escalation contact and their phone number?"* | **Priya Natarajan, Director of IT, +1-775-555-0188** | Contact extraction |
| I3-Q6 | *"What is the RTO and RPO for this integration?"* | **RTO: 4 hours, RPO: 15 minutes** | Specific numbers |
| I3-Q7 | *"When was the last DR drill and how long did recovery take?"* | **12 July 2025**, recovery in **1h 47m** | Date + duration combo |
| I3-Q8 | *"What is the PagerDuty rotation name for primary on-call?"* | **iops-primary** | Specific identifier |

---

## Cross-document reasoning

| # | Question | Expected output |
|---|---|---|
| I-CROSS-1 | *"The highest-risk system in the audit is NW-SRV-0007. When was it purchased, and where is it located?"* | **14 March 2017** at **HQ Reno** (an HPE ProLiant DL380) — the oldest server on the books |
| I-CROSS-2 | *"If the ERP-Shopify integration's DLQ depth alerts, which findings in the security audit might be contributing factors?"* | Plausibly **F-001** (EOL OS on ERP host) and/or **F-015** (long-lived CI deploy token). Either is acceptable — partial credit for one. |

---

## Scoring table

| Test ID | Pass / Partial / Fail | Notes |
|---|---|---|
| I1-Q1 → I1-Q6 |   |   |
| I2-Q1 → I2-Q8 |   |   |
| I3-Q1 → I3-Q8 |   |   |
| I-CROSS-1 |   |   |
| I-CROSS-2 |   |   |

**Pass criteria:** ≥ 85% (21 / 24 questions).
