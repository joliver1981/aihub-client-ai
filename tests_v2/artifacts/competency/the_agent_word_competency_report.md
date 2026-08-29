# The Agent (chat attachments) — Word Competency Report

Generated: 2026-08-27 14:49:40
Transport: `POST http://127.0.0.1:5111/api/uploads` + `POST http://127.0.0.1:5111/api/chat` (fresh session per question, whole corpus attached)
Model: **claude-sonnet-5** (role 3)
Battery: imported verbatim from `test_competency_agent_knowledge_word.py` — same questions, regexes and weights as the General Agent run.

## Headline

- **Overall score: 88.9%** (24.0 / 27.0 weighted points)
- Questions asked: **26**
- Fixtures uploaded: **5**
- Leaks / forbidden-pattern hits: **1** 🚨
- Correct answers with **no tool call at all**: **0** ✅
- Turns with a stream error: **0**

## Per-fixture competency

| Fixture | Questions | Score | Earned/Weight |
|---|---:|---:|---|
| `01_clean_handbook.docx` | 6 | **100.0%** | 6.0/6.0 |
| `02_tables_heavy_report.docx` | 5 | **80.0%** | 4.0/5.0 |
| `03_embedded_charts_kpis.docx` | 5 | **100.0%** | 5.0/5.0 |
| `04_tracked_changes_contract.docx` | 4 | **60.0%** | 3.0/5.0 |
| `05_long_doc_toc_footnotes.docx` | 6 | **100.0%** | 6.0/6.0 |

## Per-dimension competency

| Dimension | Questions | Score | Earned/Weight |
|---|---:|---:|---|
| `tracked_change_accepted` | 1 | **0.0%** | 0.0/2.0 |
| `comparison` | 3 | **66.7%** | 2.0/3.0 |
| `table_in_word` | 5 | **80.0%** | 4.0/5.0 |
| `direct_lookup` | 19 | **100.0%** | 19.0/19.0 |
| `bullet_extract` | 3 | **100.0%** | 3.0/3.0 |
| `chart_caption` | 2 | **100.0%** | 2.0/2.0 |
| `long_doc_retrieval` | 2 | **100.0%** | 2.0/2.0 |
| `footnote_extract` | 1 | **100.0%** | 1.0/1.0 |
| `not_present` | 1 | **100.0%** | 1.0/1.0 |

## Tool usage (grounding evidence)

| # | Fixture | Correct | Tools called |
|---:|---|:--:|---|
| 1 | `01_clean_handbook.docx` | ✅ | import_documents, import_documents, import_documents, import_documents, import_documents, search_documents, search_documents, search_documents |
| 2 | `01_clean_handbook.docx` | ✅ | import_documents, import_documents, import_documents, import_documents, import_documents, search_documents, list_documents, search_documents, search_documents, search_documents |
| 3 | `01_clean_handbook.docx` | ✅ | import_documents, search_documents |
| 4 | `01_clean_handbook.docx` | ✅ | import_documents, import_documents, import_documents, import_documents, import_documents, search_documents, search_documents, search_documents, query_document_records |
| 5 | `01_clean_handbook.docx` | ✅ | import_documents, import_documents, import_documents, import_documents, import_documents, search_documents |
| 6 | `01_clean_handbook.docx` | ✅ | import_documents, import_documents, import_documents, import_documents, import_documents, search_documents, search_documents, search_documents, list_documents |
| 7 | `02_tables_heavy_report.docx` | ✅ | list_documents, search_documents, import_documents, import_documents, import_documents, import_documents, import_documents, search_documents, read_file |
| 8 | `02_tables_heavy_report.docx` | ✅ | read_file, read_file |
| 9 | `02_tables_heavy_report.docx` | ✅ | import_documents, import_documents, import_documents, import_documents, import_documents, search_documents, query_document_records, read_file, read_file |
| 10 | `02_tables_heavy_report.docx` | ❌ | list_data_connections, search_documents, list_documents |
| 11 | `02_tables_heavy_report.docx` | ✅ | list_data_connections, read_file, read_file |
| 12 | `03_embedded_charts_kpis.docx` | ✅ | list_data_connections, get_connection_schema, get_connection_schema, get_connection_schema, list_saved_views, list_integrations, get_connection_schema, get_connection_schema, get_connection_schema, read_file, read_file |
| 13 | `03_embedded_charts_kpis.docx` | ✅ | import_documents, import_documents, import_documents, import_documents, import_documents, search_documents, search_documents, list_documents |
| 14 | `03_embedded_charts_kpis.docx` | ✅ | read_file, read_file |
| 15 | `03_embedded_charts_kpis.docx` | ✅ | Skill, read_file, read_file, read_file, read_file, read_file |
| 16 | `03_embedded_charts_kpis.docx` | ✅ | read_file, read_file, read_file |
| 17 | `04_tracked_changes_contract.docx` | ✅ | read_file |
| 18 | `04_tracked_changes_contract.docx` | ✅ | Skill, read_file |
| 19 | `04_tracked_changes_contract.docx` | 🚨 | read_file |
| 20 | `04_tracked_changes_contract.docx` | ✅ | read_file |
| 21 | `05_long_doc_toc_footnotes.docx` | ✅ | read_file, read_file, read_file, read_file, read_file |
| 22 | `05_long_doc_toc_footnotes.docx` | ✅ | read_file, read_file, read_file, read_file, read_file |
| 23 | `05_long_doc_toc_footnotes.docx` | ✅ | import_documents, import_documents, import_documents, import_documents, import_documents, search_documents, search_documents, search_documents, query_document_records |
| 24 | `05_long_doc_toc_footnotes.docx` | ✅ | import_documents, import_documents, import_documents, import_documents, import_documents, search_documents |
| 25 | `05_long_doc_toc_footnotes.docx` | ✅ | read_file, read_file, read_file, read_file, read_file |
| 26 | `05_long_doc_toc_footnotes.docx` | ✅ | read_file, read_file, read_file, read_file, read_file |

## Failed / leaked questions

### ❌ FAIL — `02_tables_heavy_report.docx` — What was the top revenue lane (origin → destination) for Eldoria Logistics in Q1?
- Dimensions: table_in_word, comparison | weight 1.0 | status 200 | 29.7s
- Tools: list_data_connections, search_documents, list_documents
- Answer:
    I searched but couldn't find anything about "Eldoria Logistics" anywhere I have access to:
    
    - **Documents store** (143 docs total): no matches for "Eldoria" or "revenue lane" — and the 5 files you just attached (handbook, tables report, embedded-charts KPIs, tracked-changes contract, long-doc TOC) don't look logistics-related either.
    - **Data connections**: none named for Eldoria or freight/logistics lanes (I see EDW, ERPDB, Salesforce, various AIRDB/sales connections, but nothing indicating lane/origin-destination revenue data, and no obvious "Eldoria" entity).
    
    I don't want to guess or fabricate a lane/number. Could you point me to where this data actually lives? For example:
    - A specific data connection/table that has lane-level revenue (origin/destination + Q1 revenue)?
    - A report or spreadsheet I should import (from your uploads, a folder, or a portal)?
    - Is "Eldoria Logistics" a customer name inside one of your existing systems (e.g., Salesforce, ERPDB) that I should query by name?
    
    Once I know where the data is, I can pull it and give you the top revenue lane for Q1.

### 🚨 LEAK — `04_tracked_changes_contract.docx` — What is the current penalty per business day for late delivery under Section 5?
- Dimensions: tracked_change_accepted | weight 2.0 | status 200 | 12.1s
- Tools: read_file
- Answer:
    **$12,500 USD per business day**, capped at thirty (30) days.
    
    This is from the current (post-revision) text of Section 5 in `04_tracked_changes_contract.docx` — the document notes the earlier draft figure of $5,000 was struck by a tracked deletion, and once all tracked changes are accepted, the standing figure is $12,500/business day.


## All Q&A (for audit)

### ✅ `01_clean_handbook.docx` — Who founded Veridian Labs and when?
- score 1.0 | direct_lookup | 377.2s | tools: import_documents, import_documents, import_documents, import_documents, import_documents, search_documents, search_documents, search_documents
- answer:
    I'll check the attached documents for this information.
    All 5 documents imported successfully. Now let me search for the founding information.
    Found it in the employee handbook (`01_clean_handbook.docx`):
    
    **Veridian Labs was founded in March 2014 by Dr. Anika Vorhees and Marcus Holloway** in Cambridge, Massachusetts.
    
    Additional context from the same handbook: the company designs precision optical sensors for industrial automation, ships to 38 countries, and as of January 2026 employs 412 people across 5 sites (Cambridge MA HQ, Eindhoven NL, Penang Malaysia, Bangalore India, and Austin TX).

### ✅ `01_clean_handbook.docx` — How many manufacturing or operating sites does Veridian Labs have?
- score 1.0 | direct_lookup, bullet_extract | 154.4s | tools: import_documents, import_documents, import_documents, import_documents, import_documents, search_documents, list_documents, search_documents, search_documents, search_documents
- answer:
    Already imported previously. Now let me search.
    Per **01_clean_handbook.docx** (Veridian Labs Employee Handbook 2026, p.1):
    
    Veridian Labs operates **5 sites** total, but only **1** is designated as a manufacturing site:
    
    | Site | Function |
    |---|---|
    | Cambridge, MA | Headquarters (engineering + HQ) |

### ✅ `01_clean_handbook.docx` — How many vacation days does an employee with 7 years of service receive at Veridian Labs?
- score 1.0 | bullet_extract, direct_lookup | 31.2s | tools: import_documents, search_documents
- answer:
    Already imported — let me search it.
    At Veridian Labs, vacation accrual is based on tenure brackets (per the Employee Handbook, p.1):
    
    - 0–2 years: 18 days/year
    - 3–5 years: 22 days/year
    - **6–10 years: 25 days/year**
    - 11+ years: 28 days/year
    

### ✅ `01_clean_handbook.docx` — What is the daily per diem for travel to Tokyo?
- score 1.0 | bullet_extract | 167.4s | tools: import_documents, import_documents, import_documents, import_documents, import_documents, search_documents, search_documents, search_documents, query_document_records
- answer:
    Already imported — good, let me search for the per diem info.
    Based on the employee handbook you attached (**01_clean_handbook.docx**, p.1, "Travel & Per Diem"):
    
    **Tokyo is a Tier 1 city, and the per diem is $115/day** (flat rate covering all expenses).
    
    > "Per diem amounts (USD) by city tier are: Tier 1 (NYC, SF, London, Tokyo, Singapore): $115/day"
    
    ⚠️ **Heads up on a conflict:** the document store also has a *different* employee handbook already on file (`stress_01_employee_handbook_22pg`) whose Appendix A also lists Tokyo as a Tier 1 international city but breaks per diem down by category instead of a flat rate:

### ✅ `01_clean_handbook.docx` — How often are performance reviews held at Veridian Labs?
- score 1.0 | direct_lookup | 35.7s | tools: import_documents, import_documents, import_documents, import_documents, import_documents, search_documents
- answer:
    Already indexed. Now let me search.
    At Veridian Labs, performance reviews are held **semi-annually — in April and October**, per the Employee Handbook 2026 (01_clean_handbook.docx, p.1, Section 4: Performance Reviews). Compensation adjustments follow shortly after each cycle (May 1 for the Spring/April review, November 1 for the Fall/October review), and reviews use a 5-point scale: Exceeds, Strong, Meets, Developing, Not Meeting.
    
    (Note: other passages returned belong to an unrelated "Greenfield Technologies" handbook already in the document store — not Veridian Labs — so I excluded those from this answer.)

### ✅ `01_clean_handbook.docx` — Does Veridian Labs have a code of conduct that protects whistleblowers from retaliation?
- score 1.0 | direct_lookup | 61.9s | tools: import_documents, import_documents, import_documents, import_documents, import_documents, search_documents, search_documents, search_documents, list_documents
- answer:
    I'll import these documents so they're searchable, then look for whistleblower/retaliation protections.
    All 5 already indexed. Now let me search for whistleblower/retaliation content.
    Found it in the Veridian Labs Employee Handbook (already in the store). Section 5, "Conduct & Reporting," states:
    
    > "Concerns may be reported anonymously to ethics@veridianlabs.com. Reports are reviewed by the People Operations lead, Sasha Kovalenko, and the General Counsel, Bao Tran. **Retaliation against reporters is grounds for termination.**"
    
    **Answer: Yes.** Veridian Labs' Employee Handbook (2026, effective April 1, 2026) includes a conduct/reporting policy that:
    - Lets employees report concerns anonymously (ethics@veridianlabs.com)

### ✅ `02_tables_heavy_report.docx` — Which Eldoria Logistics hub had the highest throughput in Q1 2026?
- score 1.0 | table_in_word, comparison | 39.4s | tools: list_documents, search_documents, import_documents, import_documents, import_documents, import_documents, import_documents, search_documents, read_file
- answer:
    Already in the store — but the search still found nothing. Let me re-check directly by reading the files (they're already imported, and the search index should cover them; let me search again more broadly, and also just read the tables-heavy report directly since it's most likely to hold hub throughput data).
    **Memphis** had the highest throughput in Q1 2026 among Eldoria Logistics hubs, averaging **49,100 parcels/day** (Jan 48,200 → Feb 47,800 → Mar 51,300).
    
    For context, the next closest hubs were Louisville (32,600/day) and Cologne (29,467/day) — Memphis led by a wide margin and also topped the Q1 revenue-lane and damage-claims tables (source: *02_tables_heavy_report.docx*, Q1 2026 Operations Report).

### ✅ `02_tables_heavy_report.docx` — What was Memphis's Q1 average daily parcel throughput?
- score 1.0 | table_in_word, direct_lookup | 12.7s | tools: read_file, read_file
- answer:
    Found it in **Eldoria Logistics — Q1 2026 Operations Report**: Memphis's Q1 2026 average daily parcel throughput was **49,100 parcels/day** (Jan 48,200 → Feb 47,800 → Mar 51,300), making it the highest-throughput hub for the quarter.

### ✅ `02_tables_heavy_report.docx` — Which service tier missed its on-time delivery SLA in Q1 2026?
- score 1.0 | table_in_word, comparison | 42.4s | tools: import_documents, import_documents, import_documents, import_documents, import_documents, search_documents, query_document_records, read_file, read_file
- answer:
    All 5 files were already in the store. Now let me search for the SLA data.
    **Priority Overnight** was the only service tier that missed its Q1 2026 on-time delivery SLA.
    
    From Eldoria Logistics' Q1 2026 Operations Report ("02_tables_heavy_report.docx"):
    
    | Tier | Target SLA | Q1 Actual | Variance |
    |---|---|---|---|
    | **Priority Overnight** | 99.0% | **98.6%** | **-0.4 pp (missed)** |

### ❌ `02_tables_heavy_report.docx` — What was the top revenue lane (origin → destination) for Eldoria Logistics in Q1?
- score 0.0 | table_in_word, comparison | 29.7s | tools: list_data_connections, search_documents, list_documents
- answer:
    I searched but couldn't find anything about "Eldoria Logistics" anywhere I have access to:
    
    - **Documents store** (143 docs total): no matches for "Eldoria" or "revenue lane" — and the 5 files you just attached (handbook, tables report, embedded-charts KPIs, tracked-changes contract, long-doc TOC) don't look logistics-related either.
    - **Data connections**: none named for Eldoria or freight/logistics lanes (I see EDW, ERPDB, Salesforce, various AIRDB/sales connections, but nothing indicating lane/origin-destination revenue data, and no obvious "Eldoria" entity).
    
    I don't want to guess or fabricate a lane/number. Could you point me to where this data actually lives? For example:
    - A specific data connection/table that has lane-level revenue (origin/destination + Q1 revenue)?
    - A report or spreadsheet I should import (from your uploads, a folder, or a portal)?

### ✅ `02_tables_heavy_report.docx` — What was Cologne's average damage claim amount in Q1 2026?
- score 1.0 | table_in_word, direct_lookup | 22.1s | tools: list_data_connections, read_file, read_file
- answer:
    Found it in the report you attached — **Eldoria Logistics — Q1 2026 Operations Report** (`02_tables_heavy_report.docx`), Section 4 "Damage & Loss Claims":
    
    **Cologne's average damage claim in Q1 2026 was $215 per claim** — the highest average of any hub (142 claims filed, $30.5K total paid).
    
    For context, other hubs' Q1 average claims: Memphis $187, Louisville $162, Rotterdam $198, Singapore $224, Dubai $256.

### ✅ `03_embedded_charts_kpis.docx` — How many active accounts did Atlas Networks have in March 2026?
- score 1.0 | chart_caption, direct_lookup | 45.1s | tools: list_data_connections, get_connection_schema, get_connection_schema, get_connection_schema, list_saved_views, list_integrations, get_connection_schema, get_connection_schema, get_connection_schema, read_file, read_file
- answer:
    Found it in your attached document.
    
    **Atlas Networks had 89,700 active accounts in March 2026** (paid logos), per the *"Atlas Networks — FY2026 Board Update"* (03_embedded_charts_kpis.docx). That's up from 82,400 in July 2025 — an 8.9% sequential increase over the 9 months.

### ✅ `03_embedded_charts_kpis.docx` — What is Atlas Networks' FY2026 total ARR?
- score 1.0 | direct_lookup | 87.2s | tools: import_documents, import_documents, import_documents, import_documents, import_documents, search_documents, search_documents, list_documents
- answer:
    Already imported — now let's search for the answer.
    Found it — in **03_embedded_charts_kpis.docx** (Atlas Networks FY2026 Board Update, p.1):
    
    **Atlas Networks' FY2026 total ARR is $248M**, up 18.4% year-over-year, with Atlas Core making up the largest share at 42% of that ARR (Professional Services the smallest at 7%).

### ✅ `03_embedded_charts_kpis.docx` — What share of Atlas Networks' FY2026 ARR comes from the Atlas Core product line?
- score 1.0 | chart_caption, direct_lookup | 12.1s | tools: read_file, read_file
- answer:
    Atlas Core makes up **42% of Atlas Networks' FY2026 ARR** (total ARR = $248M, up 18.4% YoY), per the CEO office's FY2026 Board Update (03_embedded_charts_kpis.docx, Section 2 "ARR Composition"). Professional Services is the smallest line at 7%.

### ✅ `03_embedded_charts_kpis.docx` — Who is Atlas Networks' CEO and CFO?
- score 1.0 | direct_lookup | 19.6s | tools: Skill, read_file, read_file, read_file, read_file, read_file
- answer:
    This is a one-off lookup, so I'll just read the attached files directly rather than importing them.
    Per Atlas Networks' FY2026 Board Update (03_embedded_charts_kpis.docx):
    
    - **CEO:** Larissa Verma
    - **CFO:** Hideo Tanaka

### ✅ `03_embedded_charts_kpis.docx` — How many customers does Atlas Networks serve, and in how many countries?
- score 1.0 | direct_lookup | 14.0s | tools: read_file, read_file, read_file
- answer:
    Found it in **03_embedded_charts_kpis.docx** (Atlas Networks — FY2026 Board Update):
    
    Atlas Networks serves **4,200 customers across 23 countries**.

### ✅ `04_tracked_changes_contract.docx` — What is the monthly fee Cyrene Industries pays Westbrook & Vale under the MSA?
- score 1.0 | direct_lookup | 10.8s | tools: read_file
- answer:
    Under the MSA (Master Services Agreement, effective June 1, 2026) between Cyrene Industries (Client) and Westbrook & Vale LLP (Provider), Cyrene Industries pays a **monthly fee of $48,500 USD**, per Section 2 (Fees and Payment). Invoices are net-30, with 1.5%/month interest on late payments.

### ✅ `04_tracked_changes_contract.docx` — What is the initial term of the Master Services Agreement?
- score 1.0 | direct_lookup | 13.0s | tools: Skill, read_file
- answer:
    This is a one-off question about a specific attached file, so I'll just read the tracked-changes contract directly (the most likely candidate for a Master Services Agreement).
    The initial term of the Master Services Agreement (between Cyrene Industries and Westbrook & Vale LLP, effective June 1, 2026) is **twenty-four (24) months**, commencing on the Effective Date. Either party may terminate for convenience with 60 days' written notice.

### 🚨 `04_tracked_changes_contract.docx` — What is the current penalty per business day for late delivery under Section 5?
- score 0.0 | tracked_change_accepted | 12.1s | tools: read_file
- answer:
    **$12,500 USD per business day**, capped at thirty (30) days.
    
    This is from the current (post-revision) text of Section 5 in `04_tracked_changes_contract.docx` — the document notes the earlier draft figure of $5,000 was struck by a tracked deletion, and once all tracked changes are accepted, the standing figure is $12,500/business day.

### ✅ `04_tracked_changes_contract.docx` — How long does the confidentiality obligation survive after termination?
- score 1.0 | direct_lookup | 9.7s | tools: read_file
- answer:
    The confidentiality obligation in the Master Services Agreement (Cyrene Industries / Westbrook & Vale LLP, Section 3) survives for **five (5) years following termination**.
    
    For reference, permitted disclosures during that period are limited to legal compulsion and disclosure to professional advisors bound by similar confidentiality obligations.

### ✅ `05_long_doc_toc_footnotes.docx` — What is the peak event throughput of the Hyperion Logistics Network?
- score 1.0 | direct_lookup | 19.3s | tools: read_file, read_file, read_file, read_file, read_file
- answer:
    This document (03_embedded_charts_kpis.docx) is about "Atlas Networks," not "Hyperion Logistics Network." Let me check the other attached files.
    The Hyperion Logistics Network (HLN) has a peak event throughput of **28,000 events/sec**, designed for high-throughput dispatch with sub-second routing decisions.
    
    Source: *"05_long_doc_toc_footnotes.docx"* — Hyperion Logistics Network — Technical Reference (v4.2), Section 1 (Introduction) and confirmed again in Section 10 (Anchor Fingerprint Facts): "Hyperion Logistics Network (HLN) peak throughput: 28,000 events/sec."

### ✅ `05_long_doc_toc_footnotes.docx` — Which 3 cloud regions does Hyperion Logistics Network deploy to?
- score 1.0 | direct_lookup | 15.7s | tools: read_file, read_file, read_file, read_file, read_file
- answer:
    Hyperion Logistics Network (HLN) deploys to 3 regions:
    
    1. **us-east-1**
    2. **eu-central-1**
    3. **ap-southeast-2**
    
    Source: `05_long_doc_toc_footnotes.docx` — Section 2 (Network Topology) and confirmed again in Section 10 (Anchor Fingerprint Facts).

### ✅ `05_long_doc_toc_footnotes.docx` — When was the last successful disaster-recovery drill performed?
- score 1.0 | long_doc_retrieval, direct_lookup | 128.3s | tools: import_documents, import_documents, import_documents, import_documents, import_documents, search_documents, search_documents, search_documents, query_document_records
- answer:
    Already imported previously — good, they're in the store. Now let me search for the disaster-recovery drill info.
    According to **05_long_doc_toc_footnotes.docx** (Hyperion Logistics Network Technical Reference v4.2), Section 8 – Disaster Recovery:
    
    > **Last successful DR drill: February 14, 2026**
    
    Additional context from that same section:
    - RPO (Recovery Point Objective): 60 seconds for the data plane, 5 minutes for control-plane state
    - RTO (Recovery Time Objective): 30 minutes for full regional failover

### ✅ `05_long_doc_toc_footnotes.docx` — Who authored RFC-HLN-031 and when was it approved?
- score 1.0 | footnote_extract, long_doc_retrieval | 87.7s | tools: import_documents, import_documents, import_documents, import_documents, import_documents, search_documents
- answer:
    Already imported. Let me search for the RFC.
    I found RFC‑HLN‑031 in **05_long_doc_toc_footnotes.docx** (Hyperion Logistics Network Technical Reference).
    
    - **Author:** Mei-Ling Park
    - **Approved:** October 25, 2025 — by the Platform Engineering Council
    
    Note there's a small internal inconsistency in the document worth flagging: the body text (Section 3) says the RFC "was authored by Mei-Ling Park and approved on October 18, 2025," but footnote 1 — which is more specific — clarifies that October 18, 2025 was the **publish** date, and the actual **approval** by the Platform Engineering Council came a week later, on **October 25, 2025**. I'd go with the footnote's more detailed account for the approval date.

### ✅ `05_long_doc_toc_footnotes.docx` — Does Hyperion Logistics Network use Azure as one of its cloud providers?
- score 1.0 | not_present | 17.0s | tools: read_file, read_file, read_file, read_file, read_file
- answer:
    I'll check the attached documents for mentions of Hyperion Logistics Network and cloud providers.
    No, according to the technical reference document (`05_long_doc_toc_footnotes.docx`), Hyperion Logistics Network (HLN) is deployed across **AWS and GCP** only — Azure is not mentioned as one of its cloud providers.

### ✅ `05_long_doc_toc_footnotes.docx` — What programming language is the router-core service written in, and what is its p95 latency?
- score 1.0 | direct_lookup | 20.8s | tools: read_file, read_file, read_file, read_file, read_file
- answer:
    Found it in **05_long_doc_toc_footnotes.docx** (Hyperion Logistics Network Technical Reference, v4.2):
    
    - **Language:** Rust
    - **p95 latency:** 84 ms (end-to-end)
    
    Both figures are stated twice in the doc — once in Section 3 ("Routing Subsystem") and again confirmed in the "Anchor Fingerprint Facts" section.
