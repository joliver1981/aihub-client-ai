# Word Agent-Knowledge — Competency Report

Generated: 2026-05-24 20:15:02
Agent: id=750 (deleted after run)

## Headline

- **Overall score: 100.0%** (27.0 / 27.0 weighted points)
- Questions asked: **26**
- Fixtures uploaded: **5**
- Leaks / forbidden-pattern hits: **0** ✅
- Questions that needed a clarification follow-up: **0** of 26

## Per-fixture competency

| Fixture | Questions | Score | Earned/Weight |
|---|---:|---:|---|
| `01_clean_handbook.docx` | 6 | **100.0%** | 6.0/6.0 |
| `02_tables_heavy_report.docx` | 5 | **100.0%** | 5.0/5.0 |
| `03_embedded_charts_kpis.docx` | 5 | **100.0%** | 5.0/5.0 |
| `04_tracked_changes_contract.docx` | 4 | **100.0%** | 5.0/5.0 |
| `05_long_doc_toc_footnotes.docx` | 6 | **100.0%** | 6.0/6.0 |

## Per-dimension competency

| Dimension | Questions | Score | Earned/Weight |
|---|---:|---:|---|
| `direct_lookup` | 19 | **100.0%** | 19.0/19.0 |
| `bullet_extract` | 3 | **100.0%** | 3.0/3.0 |
| `table_in_word` | 5 | **100.0%** | 5.0/5.0 |
| `comparison` | 3 | **100.0%** | 3.0/3.0 |
| `chart_caption` | 2 | **100.0%** | 2.0/2.0 |
| `tracked_change_accepted` | 1 | **100.0%** | 2.0/2.0 |
| `long_doc_retrieval` | 2 | **100.0%** | 2.0/2.0 |
| `footnote_extract` | 1 | **100.0%** | 1.0/1.0 |
| `not_present` | 1 | **100.0%** | 1.0/1.0 |

## All Q&A (for audit)

### ✅ `01_clean_handbook.docx` — Who founded Veridian Labs and when?
- score: 1.0 | dimensions: direct_lookup | 3.8s
- answer:
    Veridian Labs “was founded in March 2014 by Dr. Anika Vorhees and Marcus Holloway in Cambridge, Massachusetts.”  
    Source: **01_clean_handbook.docx**.

### ✅ `01_clean_handbook.docx` — How many manufacturing or operating sites does Veridian Labs have?
- score: 1.0 | dimensions: direct_lookup, bullet_extract | 3.7s
- answer:
    Veridian Labs has **5 sites**.
    
    From **01_clean_handbook.docx**: “**As of January 2026 the company employs 412 people across 5 sites.**”
    
    The listed sites are:
    - Cambridge, MA — Headquarters
    - Eindhoven, Netherlands
    - Penang, Malaysia

### ✅ `01_clean_handbook.docx` — How many vacation days does an employee with 7 years of service receive at Veridian Labs?
- score: 1.0 | dimensions: bullet_extract, direct_lookup | 3.6s
- answer:
    An employee with **7 years of service** receives **25 vacation days per year** at Veridian Labs.
    
    Source: **01_clean_handbook.docx**, which states: **“6–10 years of service: 25 days per year.”**

### ✅ `01_clean_handbook.docx` — What is the daily per diem for travel to Tokyo?
- score: 1.0 | dimensions: bullet_extract | 4.3s
- answer:
    The daily per diem for travel to Tokyo is **$115/day**.
    
    Source: **01_clean_handbook.docx** — “**Tier 1 (NYC, SF, London, Tokyo, Singapore): $115/day**”

### ✅ `01_clean_handbook.docx` — How often are performance reviews held at Veridian Labs?
- score: 1.0 | dimensions: direct_lookup | 3.2s
- answer:
    At Veridian Labs, performance reviews are held **semi-annually, in April and October**.  
    Source: **01_clean_handbook.docx** (“Performance reviews are conducted semi-annually, in April and October.”)

### ✅ `01_clean_handbook.docx` — Does Veridian Labs have a code of conduct that protects whistleblowers from retaliation?
- score: 1.0 | dimensions: direct_lookup | 3.8s
- answer:
    Yes. In **01_clean_handbook.docx**, under **“5. Conduct & Reporting,”** it says:
    
    > “Concerns may be reported anonymously to ethics@veridianlabs.com.”  
    > “Retaliation against reporters is grounds for termination.”
    
    So Veridian Labs does have a conduct/reporting policy that protects whistleblowers from retaliation. Source: **01_clean_handbook.docx**.

### ✅ `02_tables_heavy_report.docx` — Which Eldoria Logistics hub had the highest throughput in Q1 2026?
- score: 1.0 | dimensions: table_in_word, comparison | 4.1s
- answer:
    The highest-throughput Eldoria Logistics hub in Q1 2026 was **Memphis**, with a **Q1 avg of 49,100 parcels/day**.  
    Source: **02_tables_heavy_report.docx**
    
    Direct quote: “**Memphis was Q1's highest-throughput hub at 49,100 parcels/day.**”

### ✅ `02_tables_heavy_report.docx` — What was Memphis's Q1 average daily parcel throughput?
- score: 1.0 | dimensions: table_in_word, direct_lookup | 3.2s
- answer:
    Memphis's **Q1 average daily parcel throughput was 49,100 parcels/day**.  
    Source: **02_tables_heavy_report.docx** (“Memphis | 48200 | 47800 | 51300 | **49100**”)

### ✅ `02_tables_heavy_report.docx` — Which service tier missed its on-time delivery SLA in Q1 2026?
- score: 1.0 | dimensions: table_in_word, comparison | 3.8s
- answer:
    The service tier that missed its on-time delivery SLA in Q1 2026 was **Priority overnight**.
    
    Exact document detail: **“Priority Overnight was the only service tier to miss its SLA (98.6% vs 99.0%, -0.4 pp).”**  
    Source: **02_tables_heavy_report.docx**

### ✅ `02_tables_heavy_report.docx` — What was the top revenue lane (origin → destination) for Eldoria Logistics in Q1?
- score: 1.0 | dimensions: table_in_word, comparison | 4.3s
- answer:
    The top Q1 revenue lane for Eldoria Logistics was **Memphis → Atlanta**.
    
    The report states: **“The Memphis→Atlanta lane generated $2,840K in Q1, our top revenue lane.”**  
    Source: **02_tables_heavy_report.docx**

### ✅ `02_tables_heavy_report.docx` — What was Cologne's average damage claim amount in Q1 2026?
- score: 1.0 | dimensions: table_in_word, direct_lookup | 3.9s
- answer:
    Cologne's **average damage claim amount in Q1 2026 was $215 per claim**.  
    Source: **02_tables_heavy_report.docx**
    
    Exact table entry:
    - **Cologne | 142 | 215 | 30.5** under **"Hub | Claims filed | Avg claim ($) | Total paid ($K)"**

### ✅ `03_embedded_charts_kpis.docx` — How many active accounts did Atlas Networks have in March 2026?
- score: 1.0 | dimensions: chart_caption, direct_lookup | 3.4s
- answer:
    Atlas Networks had **89,700 active accounts in March 2026**.
    
    Source: **03_embedded_charts_kpis.docx** — “**Active accounts: 89,700 (March 2026).**”

### ✅ `03_embedded_charts_kpis.docx` — What is Atlas Networks' FY2026 total ARR?
- score: 1.0 | dimensions: direct_lookup | 3.5s
- answer:
    Atlas Networks' FY2026 total ARR is **$248M**.
    
    Source: **03_embedded_charts_kpis.docx** — “**FY2026 ARR totals $248M, up 18.4% year-on-year.**”

### ✅ `03_embedded_charts_kpis.docx` — What share of Atlas Networks' FY2026 ARR comes from the Atlas Core product line?
- score: 1.0 | dimensions: chart_caption, direct_lookup | 3.8s
- answer:
    Atlas Core accounts for **42% of FY2026 ARR**.  
    Source: **03_embedded_charts_kpis.docx**
    
    Exact text: “**Figure 2 — Atlas Core remains the largest line at 42% of ARR.**”

### ✅ `03_embedded_charts_kpis.docx` — Who is Atlas Networks' CEO and CFO?
- score: 1.0 | dimensions: direct_lookup | 4.1s
- answer:
    Atlas Networks' **CEO is Larissa Verma** and **CFO is Hideo Tanaka**.  
    Source: **03_embedded_charts_kpis.docx** (“Atlas Networks — FY2026 Board Update”) which states: **“CEO: Larissa Verma. CFO: Hideo Tanaka.”**

### ✅ `03_embedded_charts_kpis.docx` — How many customers does Atlas Networks serve, and in how many countries?
- score: 1.0 | dimensions: direct_lookup | 3.3s
- answer:
    Atlas Networks serves **4,200 customers across 23 countries**.  
    Source: **03_embedded_charts_kpis.docx**
    
    Exact text: “**Atlas Networks operates a fleet-management SaaS platform serving 4,200 customers across 23 countries.**”

### ✅ `04_tracked_changes_contract.docx` — What is the monthly fee Cyrene Industries pays Westbrook & Vale under the MSA?
- score: 1.0 | dimensions: direct_lookup | 7.5s
- answer:
    The monthly fee is **“$48,500 USD”**.  
    Source: **04_tracked_changes_contract.docx**
    
    Exact text: **“Client shall pay Provider a monthly fee of $48,500 USD.”**

### ✅ `04_tracked_changes_contract.docx` — What is the initial term of the Master Services Agreement?
- score: 1.0 | dimensions: direct_lookup | 4.9s
- answer:
    The initial term is **“twenty-four (24) months, commencing on the Effective Date.”**  
    Source: **04_tracked_changes_contract.docx**

### ✅ `04_tracked_changes_contract.docx` — What is the current penalty per business day for late delivery under Section 5?
- score: 2.0 | dimensions: tracked_change_accepted | 3.7s
- answer:
    The current penalty under Section 5 is **"$12,500 USD per business day"**. Source: **04_tracked_changes_contract.docx**.
    
    The document states:  
    **“After accepting all tracked changes, Section 5 reads: 'The penalty for late delivery is $12,500 USD per business day, capped at thirty (30) days.'”**

### ✅ `04_tracked_changes_contract.docx` — How long does the confidentiality obligation survive after termination?
- score: 1.0 | dimensions: direct_lookup | 3.3s
- answer:
    The confidentiality obligation survives for **“five (5) years following termination.”**  
    Source: **04_tracked_changes_contract.docx**
    
    Exact text from Section 3:
    > “Both parties agree to keep Confidential Information secret for a period of five (5) years following termination.”

### ✅ `05_long_doc_toc_footnotes.docx` — What is the peak event throughput of the Hyperion Logistics Network?
- score: 1.0 | dimensions: direct_lookup | 3.4s
- answer:
    The Hyperion Logistics Network peak event throughput is **28,000 events/sec**.
    
    Source: **05_long_doc_toc_footnotes.docx** — “Hyperion Logistics Network is … designed for high-throughput dispatch (**peak 28,000 events/sec**)” and “HLN peak throughput: **28,000 events/sec**.”

### ✅ `05_long_doc_toc_footnotes.docx` — Which 3 cloud regions does Hyperion Logistics Network deploy to?
- score: 1.0 | dimensions: direct_lookup | 3.7s
- answer:
    Hyperion Logistics Network deploys to these 3 regions:
    
    - `us-east-1`
    - `eu-central-1`
    - `ap-southeast-2`
    
    Source: `05_long_doc_toc_footnotes.docx` — “HLN is built around 3 primary regions: us-east-1, eu-central-1, and ap-southeast-2.”

### ✅ `05_long_doc_toc_footnotes.docx` — When was the last successful disaster-recovery drill performed?
- score: 1.0 | dimensions: long_doc_retrieval, direct_lookup | 3.1s
- answer:
    The last successful disaster-recovery drill was performed on **February 14, 2026**.  
    Source: **05_long_doc_toc_footnotes.docx** (“Last successful DR drill: February 14, 2026.”)

### ✅ `05_long_doc_toc_footnotes.docx` — Who authored RFC-HLN-031 and when was it approved?
- score: 1.0 | dimensions: footnote_extract, long_doc_retrieval | 7.6s
- answer:
    RFC-HLN-031 was authored by **Mei-Ling Park**.
    
    I found two different approval dates in **05_long_doc_toc_footnotes.docx**:
    
    - Main text: “**This RFC was authored by Mei-Ling Park and approved on October 18, 2025.**”
    - Footnote [fn1]: “**RFC-HLN-031: 'Stateless Multi-Region Router' — published October 18, 2025 by Mei-Ling Park. Approved by Platform Engineering Council on October 25, 2025.**”
    
    So the document gives a conflicting answer on the approval date:

### ✅ `05_long_doc_toc_footnotes.docx` — Does Hyperion Logistics Network use Azure as one of its cloud providers?
- score: 1.0 | dimensions: not_present | 3.3s
- answer:
    No. The document says Hyperion Logistics Network “is a multi-region, multi-tenant fleet routing platform deployed across **AWS and GCP**.” It does not mention Azure as a cloud provider.  
    Source: `05_long_doc_toc_footnotes.docx`

### ✅ `05_long_doc_toc_footnotes.docx` — What programming language is the router-core service written in, and what is its p95 latency?
- score: 1.0 | dimensions: direct_lookup | 3.7s
- answer:
    The `router-core` service is written in **Rust**, and its **latency p95 = 84 ms end-to-end**.  
    Source: `05_long_doc_toc_footnotes.docx` (“The routing layer is implemented as a Rust service named router-core… Latency p95 = 84 ms end-to-end.”)
