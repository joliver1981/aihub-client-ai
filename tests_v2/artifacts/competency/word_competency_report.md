# Word Agent-Knowledge — Competency Report

Generated: 2026-08-26 21:07:47
Agent: id=965 (deleted after run)

## Headline

- **Overall score: 92.6%** (25.0 / 27.0 weighted points)
- Questions asked: **26**
- Fixtures uploaded: **5**
- Leaks / forbidden-pattern hits: **1** 🚨
- Questions that needed a clarification follow-up: **0** of 26

## Per-fixture competency

| Fixture | Questions | Score | Earned/Weight |
|---|---:|---:|---|
| `01_clean_handbook.docx` | 6 | **100.0%** | 6.0/6.0 |
| `02_tables_heavy_report.docx` | 5 | **100.0%** | 5.0/5.0 |
| `03_embedded_charts_kpis.docx` | 5 | **100.0%** | 5.0/5.0 |
| `04_tracked_changes_contract.docx` | 4 | **60.0%** | 3.0/5.0 |
| `05_long_doc_toc_footnotes.docx` | 6 | **100.0%** | 6.0/6.0 |

## Per-dimension competency

| Dimension | Questions | Score | Earned/Weight |
|---|---:|---:|---|
| `tracked_change_accepted` | 1 | **0.0%** | 0.0/2.0 |
| `direct_lookup` | 19 | **100.0%** | 19.0/19.0 |
| `bullet_extract` | 3 | **100.0%** | 3.0/3.0 |
| `table_in_word` | 5 | **100.0%** | 5.0/5.0 |
| `comparison` | 3 | **100.0%** | 3.0/3.0 |
| `chart_caption` | 2 | **100.0%** | 2.0/2.0 |
| `long_doc_retrieval` | 2 | **100.0%** | 2.0/2.0 |
| `footnote_extract` | 1 | **100.0%** | 1.0/1.0 |
| `not_present` | 1 | **100.0%** | 1.0/1.0 |

## Failed / leaked questions

### 🚨 LEAK — `04_tracked_changes_contract.docx` — What is the current penalty per business day for late delivery under Section 5?
- Dimensions: tracked_change_accepted
- Weight: 2.0
- Chat status: 200
- Elapsed: 3.3s
- Answer:
    ## 💼 Late-Delivery Penalty
    
    The current penalty under **Section 5** is **$12,500 USD per business day**, capped at **thirty (30) days**.
    
    **Source:** `04_tracked_changes_contract.docx` — the prior $5,000 amount was removed by tracked deletion.


## All Q&A (for audit)

### ✅ `01_clean_handbook.docx` — Who founded Veridian Labs and when?
- score: 1.0 | dimensions: direct_lookup | 4.0s
- answer:
    ## 🏢 Veridian Labs Founding
    
    **Veridian Labs was founded in March 2014** by **Dr. Anika Vorhees** and **Marcus Holloway** in **Cambridge, Massachusetts**.
    
    **Source:** `01_clean_handbook.docx`

### ✅ `01_clean_handbook.docx` — How many manufacturing or operating sites does Veridian Labs have?
- score: 1.0 | dimensions: direct_lookup, bullet_extract | 3.3s
- answer:
    ## 🏭 Veridian Labs Sites
    
    Veridian Labs has **5 operating sites** as of January 2026.
    
    However, it has **1 manufacturing site**: **Penang, Malaysia** (“Manufacturing site #1 — optical assemblies”).
    
    **Source:** `01_clean_handbook.docx`

### ✅ `01_clean_handbook.docx` — How many vacation days does an employee with 7 years of service receive at Veridian Labs?
- score: 1.0 | dimensions: bullet_extract, direct_lookup | 3.1s
- answer:
    ## 🌴 Vacation Entitlement
    
    An employee with **7 years of service** receives **25 vacation days per year**.
    
    Source: **01_clean_handbook.docx** — “6–10 years of service: 25 days per year.”

### ✅ `01_clean_handbook.docx` — What is the daily per diem for travel to Tokyo?
- score: 1.0 | dimensions: bullet_extract | 3.5s
- answer:
    ## ✈️ Tokyo Per Diem
    
    The daily per diem for travel to **Tokyo** is **$115 USD per day**.
    
    Tokyo is listed as a **Tier 1** city. International travel also requires **VP-level approval**.
    
    **Source:** `01_clean_handbook.docx`

### ✅ `01_clean_handbook.docx` — How often are performance reviews held at Veridian Labs?
- score: 1.0 | dimensions: direct_lookup | 3.4s
- answer:
    ## 📋 Performance Reviews
    
    Veridian Labs holds performance reviews **semi-annually**—in **April** and **October**.
    
    **Source:** `01_clean_handbook.docx`

### ✅ `01_clean_handbook.docx` — Does Veridian Labs have a code of conduct that protects whistleblowers from retaliation?
- score: 1.0 | dimensions: direct_lookup | 3.5s
- answer:
    ## 🛡️ Whistleblower Protection
    
    **Yes.** Veridian Labs’ Employee Handbook states that concerns may be reported anonymously to **ethics@veridianlabs.com**, and that **“Retaliation against reporters is grounds for termination.”**
    
    Reports are reviewed by the People Operations lead, **Sasha Kovalenko**, and the General Counsel, **Bao Tran**.
    
    **Source:** `01_clean_handbook.docx` (Veridian Labs — Employee Handbook 2026)

### ✅ `02_tables_heavy_report.docx` — Which Eldoria Logistics hub had the highest throughput in Q1 2026?
- score: 1.0 | dimensions: table_in_word, comparison | 2.9s
- answer:
    ## 🚚 Highest Q1 Throughput
    
    **Memphis** had the highest throughput in Q1 2026, averaging **49,100 parcels per day**.
    
    *Source: `02_tables_heavy_report.docx`*

### ✅ `02_tables_heavy_report.docx` — What was Memphis's Q1 average daily parcel throughput?
- score: 1.0 | dimensions: table_in_word, direct_lookup | 2.9s
- answer:
    ## 📦 Memphis Q1 Throughput
    
    Memphis’s **Q1 average daily parcel throughput was 49,100 parcels per day**.
    
    *Source: `02_tables_heavy_report.docx`*

### ✅ `02_tables_heavy_report.docx` — Which service tier missed its on-time delivery SLA in Q1 2026?
- score: 1.0 | dimensions: table_in_word, comparison | 3.3s
- answer:
    ## 📦 Q1 2026 SLA Miss
    
    **Priority Overnight** missed its on-time delivery SLA.
    
    - **Target:** 99.0%
    - **Q1 actual:** 98.6%
    - **Variance:** -0.4 percentage points
    

### ✅ `02_tables_heavy_report.docx` — What was the top revenue lane (origin → destination) for Eldoria Logistics in Q1?
- score: 1.0 | dimensions: table_in_word, comparison | 3.1s
- answer:
    ## 🚚 Top Q1 Revenue Lane
    
    **Memphis → Atlanta** was Eldoria Logistics’ top revenue lane in Q1, generating **$2,840K** in revenue.
    
    **Source:** `02_tables_heavy_report.docx`

### ✅ `02_tables_heavy_report.docx` — What was Cologne's average damage claim amount in Q1 2026?
- score: 1.0 | dimensions: table_in_word, direct_lookup | 3.7s
- answer:
    ## 📊 Cologne Damage Claims
    
    Cologne’s average damage claim amount in **Q1 2026** was **$215 per claim**.
    
    **Source:** `02_tables_heavy_report.docx`

### ✅ `03_embedded_charts_kpis.docx` — How many active accounts did Atlas Networks have in March 2026?
- score: 1.0 | dimensions: chart_caption, direct_lookup | 3.1s
- answer:
    ## 📊 Active Accounts
    
    Atlas Networks had **89,700 active accounts** in **March 2026**.
    
    **Source:** `03_embedded_charts_kpis.docx`

### ✅ `03_embedded_charts_kpis.docx` — What is Atlas Networks' FY2026 total ARR?
- score: 1.0 | dimensions: direct_lookup | 3.3s
- answer:
    ## 📊 Atlas Networks FY2026 ARR
    
    Atlas Networks’ **FY2026 total ARR is $248M**.
    
    **Source:** `03_embedded_charts_kpis.docx` — “FY2026 ARR totals $248M.”

### ✅ `03_embedded_charts_kpis.docx` — What share of Atlas Networks' FY2026 ARR comes from the Atlas Core product line?
- score: 1.0 | dimensions: chart_caption, direct_lookup | 2.8s
- answer:
    ## 📊 Atlas Core ARR Share
    
    **Atlas Core contributes 42% of Atlas Networks’ FY2026 ARR.**
    
    Source: **03_embedded_charts_kpis.docx** — “Atlas Core remains the largest line at 42% of ARR.”

### ✅ `03_embedded_charts_kpis.docx` — Who is Atlas Networks' CEO and CFO?
- score: 1.0 | dimensions: direct_lookup | 4.0s
- answer:
    ## 👥 Atlas Networks Leadership
    
    - **CEO:** Larissa Verma  
    - **CFO:** Hideo Tanaka  
    
    Source: **03_embedded_charts_kpis.docx**.

### ✅ `03_embedded_charts_kpis.docx` — How many customers does Atlas Networks serve, and in how many countries?
- score: 1.0 | dimensions: direct_lookup | 4.3s
- answer:
    ## 📊 Atlas Networks
    
    Atlas Networks serves **4,200 customers** across **23 countries**.
    
    **Source:** `03_embedded_charts_kpis.docx`

### ✅ `04_tracked_changes_contract.docx` — What is the monthly fee Cyrene Industries pays Westbrook & Vale under the MSA?
- score: 1.0 | dimensions: direct_lookup | 3.5s
- answer:
    ## 💼 Monthly Fee
    
    Cyrene Industries pays Westbrook & Vale LLP a **monthly fee of $48,500 USD** under the MSA.
    
    **Source:** `04_tracked_changes_contract.docx`, Section 2 — Fees and Payment.

### ✅ `04_tracked_changes_contract.docx` — What is the initial term of the Master Services Agreement?
- score: 1.0 | dimensions: direct_lookup | 3.4s
- answer:
    ## 📄 Initial Term
    
    The Master Services Agreement has an initial term of **twenty-four (24) months**, commencing on the **Effective Date**.
    
    **Source:** `04_tracked_changes_contract.docx`

### 🚨 `04_tracked_changes_contract.docx` — What is the current penalty per business day for late delivery under Section 5?
- score: 0.0 | dimensions: tracked_change_accepted | 3.3s
- answer:
    ## 💼 Late-Delivery Penalty
    
    The current penalty under **Section 5** is **$12,500 USD per business day**, capped at **thirty (30) days**.
    
    **Source:** `04_tracked_changes_contract.docx` — the prior $5,000 amount was removed by tracked deletion.

### ✅ `04_tracked_changes_contract.docx` — How long does the confidentiality obligation survive after termination?
- score: 1.0 | dimensions: direct_lookup | 4.0s
- answer:
    ## 🔒 Confidentiality Survival Period
    
    The confidentiality obligation survives for **five (5) years following termination**.
    
    **Source:** `04_tracked_changes_contract.docx`, Section 3.

### ✅ `05_long_doc_toc_footnotes.docx` — What is the peak event throughput of the Hyperion Logistics Network?
- score: 1.0 | dimensions: direct_lookup | 4.7s
- answer:
    ## 🚀 Peak Event Throughput
    
    The Hyperion Logistics Network’s peak event throughput is **28,000 events per second**.
    
    Source: **05_long_doc_toc_footnotes.docx**.

### ✅ `05_long_doc_toc_footnotes.docx` — Which 3 cloud regions does Hyperion Logistics Network deploy to?
- score: 1.0 | dimensions: direct_lookup | 3.4s
- answer:
    ## 🌐 Hyperion Logistics Network Regions
    
    Hyperion Logistics Network deploys to these **3 primary regions**:
    
    - **us-east-1**
    - **eu-central-1**
    - **ap-southeast-2**
    

### ✅ `05_long_doc_toc_footnotes.docx` — When was the last successful disaster-recovery drill performed?
- score: 1.0 | dimensions: long_doc_retrieval, direct_lookup | 3.2s
- answer:
    ## 🛡️ Disaster-Recovery Drill
    
    The last successful disaster-recovery drill was performed on **February 14, 2026**.
    
    **Source:** `05_long_doc_toc_footnotes.docx`

### ✅ `05_long_doc_toc_footnotes.docx` — Who authored RFC-HLN-031 and when was it approved?
- score: 1.0 | dimensions: footnote_extract, long_doc_retrieval | 3.6s
- answer:
    ## 📄 RFC-HLN-031
    
    - **Author:** Mei-Ling Park  
    - **Approval date:** October 18, 2025  
    
    **Source:** `05_long_doc_toc_footnotes.docx`
    
    > Note: The document’s footnote separately says it was “Approved by Platform Engineering Council on October 25, 2025,” which conflicts with the main text and anchor facts.

### ✅ `05_long_doc_toc_footnotes.docx` — Does Hyperion Logistics Network use Azure as one of its cloud providers?
- score: 1.0 | dimensions: not_present | 4.0s
- answer:
    ## ☁️ Cloud Providers
    
    **No.** Hyperion Logistics Network is described as deployed across **AWS and GCP**; Azure is not listed as a cloud provider.
    
    **Source:** `05_long_doc_toc_footnotes.docx` — “Hyperion Logistics Network is a multi-region, multi-tenant fleet routing platform deployed across AWS and GCP.”

### ✅ `05_long_doc_toc_footnotes.docx` — What programming language is the router-core service written in, and what is its p95 latency?
- score: 1.0 | dimensions: direct_lookup | 3.1s
- answer:
    ## 🚀 router-core Service
    
    - **Programming language:** Rust  
    - **p95 latency:** **84 ms** end-to-end  
    
    Source: **05_long_doc_toc_footnotes.docx**
