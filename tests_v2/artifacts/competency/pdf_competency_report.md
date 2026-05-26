# Pdf Agent-Knowledge — Competency Report

Generated: 2026-05-24 20:20:44
Agent: id=751 (deleted after run)

## Headline

- **Overall score: 100.0%** (26.0 / 26.0 weighted points)
- Questions asked: **25**
- Fixtures uploaded: **5**
- Leaks / forbidden-pattern hits: **0** ✅
- Questions that needed a clarification follow-up: **0** of 25

## Per-fixture competency

| Fixture | Questions | Score | Earned/Weight |
|---|---:|---:|---|
| `01_clean_report.pdf` | 5 | **100.0%** | 5.0/5.0 |
| `02_multi_column_newsletter.pdf` | 5 | **100.0%** | 5.0/5.0 |
| `03_invoice_with_tables.pdf` | 5 | **100.0%** | 5.0/5.0 |
| `04_headers_footers_doc.pdf` | 5 | **100.0%** | 5.0/5.0 |
| `05_large_50_page.pdf` | 5 | **100.0%** | 6.0/6.0 |

## Per-dimension competency

| Dimension | Questions | Score | Earned/Weight |
|---|---:|---:|---|
| `direct_lookup` | 19 | **100.0%** | 19.0/19.0 |
| `multi_column_order` | 5 | **100.0%** | 5.0/5.0 |
| `table_in_pdf` | 5 | **100.0%** | 5.0/5.0 |
| `invoice_calc` | 3 | **100.0%** | 3.0/3.0 |
| `header_footer_isolation` | 1 | **100.0%** | 1.0/1.0 |
| `comparison` | 1 | **100.0%** | 1.0/1.0 |
| `long_doc_retrieval` | 2 | **100.0%** | 3.0/3.0 |
| `page_anchor` | 1 | **100.0%** | 2.0/2.0 |
| `not_present` | 1 | **100.0%** | 1.0/1.0 |

## All Q&A (for audit)

### ✅ `01_clean_report.pdf` — Who founded Pelagic Maritime and in what year?
- score: 1.0 | dimensions: direct_lookup | 6.9s
- answer:
    Pelagic Maritime was “founded in 1998 by Captain Ingrid Hellesund.” Source: **01_clean_report.pdf**.

### ✅ `01_clean_report.pdf` — How many vessels does Pelagic Maritime operate?
- score: 1.0 | dimensions: direct_lookup | 7.6s
- answer:
    Pelagic Maritime operates **47 vessels**. The document states: “The company operates a fleet of **47 vessels** across the North Atlantic and Mediterranean.” Source: **01_clean_report.pdf**.

### ✅ `01_clean_report.pdf` — What was Pelagic Maritime's FY2025 revenue?
- score: 1.0 | dimensions: direct_lookup | 6.9s
- answer:
    Pelagic Maritime's FY2025 revenue was **$812M**. Source: **01_clean_report.pdf**.
    
    Exact text: **"FY2025 revenue: $812M (up 6.8% YoY)."**

### ✅ `01_clean_report.pdf` — What is the name of Pelagic Maritime's newest vessel?
- score: 1.0 | dimensions: direct_lookup | 7.1s
- answer:
    Pelagic Maritime’s newest vessel is **“the MV Aurora Heron,”** which the document says “**was commissioned in March 2024**” — source: **01_clean_report.pdf**.

### ✅ `01_clean_report.pdf` — By what percentage did Pelagic Maritime commit to reduce carbon emissions by 2030?
- score: 1.0 | dimensions: direct_lookup | 7.9s
- answer:
    Pelagic Maritime committed to a **38% absolute reduction by 2030, baseline 2020**. Source: **01_clean_report.pdf**.

### ✅ `02_multi_column_newsletter.pdf` — What was Halberd Steel's Q1 2026 revenue?
- score: 1.0 | dimensions: multi_column_order, direct_lookup | 9.1s
- answer:
    Halberd Steel’s Q1 2026 revenue was **$1.42 billion**. The document states: **“Halberd posted quarterly revenue of $1.42 billion, up 9.1% year-on-year.”** Source: **02_multi_column_newsletter.pdf**.

### ✅ `02_multi_column_newsletter.pdf` — When did Halberd light the first heat at its new Chongqing electric-arc furnace?
- score: 1.0 | dimensions: multi_column_order, direct_lookup | 7.7s
- answer:
    Halberd “lit the first heat” at its new Chongqing electric-arc furnace on **February 28, 2026**. Source: **02_multi_column_newsletter.pdf**.

### ✅ `02_multi_column_newsletter.pdf` — How much did the Chongqing furnace cost to build?
- score: 1.0 | dimensions: multi_column_order, direct_lookup | 6.5s
- answer:
    The Chongqing furnace “cost $680 million” to build, according to **02_multi_column_newsletter.pdf**.

### ✅ `02_multi_column_newsletter.pdf` — Who is Halberd Steel's new Chief Technology Officer?
- score: 1.0 | dimensions: multi_column_order, direct_lookup | 7.5s
- answer:
    Halberd Steel's new Chief Technology Officer is **Dr. Wynne Rasmussen**. The newsletter says: **“Dr. Wynne Rasmussen joined Halberd as Chief Technology Officer on January 6, 2026”**.  
    Source: **02_multi_column_newsletter.pdf**

### ✅ `02_multi_column_newsletter.pdf` — Which Halberd mill recently hit 1,000 consecutive days without a lost-time injury?
- score: 1.0 | dimensions: multi_column_order, direct_lookup | 9.2s
- answer:
    It was the **Bremen mill**. The newsletter states: “**Our Bremen mill achieved 1,000 consecutive days without a lost-time injury on March 18, 2026**.” Source: **02_multi_column_newsletter.pdf**.

### ✅ `03_invoice_with_tables.pdf` — What is the invoice number?
- score: 1.0 | dimensions: table_in_pdf, direct_lookup | 7.1s
- answer:
    The invoice number is **INV-2026-04827**. Source: **03_invoice_with_tables.pdf** (“**INVOICE #INV-2026-04827**”).

### ✅ `03_invoice_with_tables.pdf` — Who is the invoice issued to (customer name and city)?
- score: 1.0 | dimensions: direct_lookup | 6.7s
- answer:
    The invoice is issued to **“Aurora Bioplastics Inc.”** in **“Munich, Germany”**. Source: **03_invoice_with_tables.pdf**.

### ✅ `03_invoice_with_tables.pdf` — What is the line total for SKU QC-PLT-018?
- score: 1.0 | dimensions: table_in_pdf, invoice_calc | 7.6s
- answer:
    The line total for **SKU `QC-PLT-018`** is **`11400.0`**.  
    Quoted row from **03_invoice_with_tables.pdf**:
    
    `QC-PLT-018 | Platinum-on-glass electrode, 50 mm | 80 | 142.5 | 11400.0`
    
    Source: **03_invoice_with_tables.pdf**

### ✅ `03_invoice_with_tables.pdf` — What is the VAT amount on the Quasar invoice?
- score: 1.0 | dimensions: table_in_pdf, invoice_calc | 6.7s
- answer:
    The VAT amount on the Quasar invoice is **£6,160.00**. Source: **03_invoice_with_tables.pdf** (“VAT (20%) £6,160.00”).

### ✅ `03_invoice_with_tables.pdf` — What is the TOTAL DUE on the Quasar invoice?
- score: 1.0 | dimensions: table_in_pdf, invoice_calc | 7.2s
- answer:
    The Quasar invoice shows **“TOTAL DUE £36,960.00”**.  
    Source: **03_invoice_with_tables.pdf**.

### ✅ `04_headers_footers_doc.pdf` — What is the total budget for Project Greenline?
- score: 1.0 | dimensions: direct_lookup, header_footer_isolation | 7.7s
- answer:
    The total budget for Project Greenline is **$42M**.
    
    Source: **04_headers_footers_doc.pdf** — “**The total budget is $42M**” and “**Project Greenline total budget: $42M**.”

### ✅ `04_headers_footers_doc.pdf` — Who is the project sponsor for Project Greenline?
- score: 1.0 | dimensions: direct_lookup | 7.9s
- answer:
    The project sponsor for Project Greenline is **COO Aldous Marchand**. Source: **04_headers_footers_doc.pdf**.

### ✅ `04_headers_footers_doc.pdf` — When does the Cologne pilot equipment installation begin?
- score: 1.0 | dimensions: direct_lookup | 7.8s
- answer:
    The Cologne pilot equipment installation begins on **May 18, 2026**. Source: **04_headers_footers_doc.pdf**.
    
    Exact text: **“Equipment installation begins May 18, 2026.”**

### ✅ `04_headers_footers_doc.pdf` — What is the expected unplanned-downtime reduction from the Cologne pilot?
- score: 1.0 | dimensions: direct_lookup | 7.2s
- answer:
    The expected unplanned-downtime reduction from the Cologne pilot is **14%**.
    
    Source: **04_headers_footers_doc.pdf** — “Expected benefits: **14% reduction in unplanned downtime** and 3% improvement in yield.”

### ✅ `04_headers_footers_doc.pdf` — Which is the highest-severity risk identified in Project Greenline?
- score: 1.0 | dimensions: table_in_pdf, comparison | 6.8s
- answer:
    The highest-severity risk in Project Greenline is **“R2 Cybersecurity exposure on OT network”** with severity **High**.  
    Source: **04_headers_footers_doc.pdf**
    
    The document’s anchor facts also state: **“Highest-severity risk: R2 cybersecurity exposure on OT network.”**

### ✅ `05_large_50_page.pdf` — Who authored RFC-OPAL-007 and when was it approved?
- score: 2.0 | dimensions: long_doc_retrieval, page_anchor | 6.9s
- answer:
    RFC-OPAL-007 was authored by **Verena Strauss** and was **approved by the Architecture Council on November 4, 2025**. Source: **05_large_50_page.pdf**.

### ✅ `05_large_50_page.pdf` — Which AWS regions does Opal Networks deploy to?
- score: 1.0 | dimensions: direct_lookup | 6.6s
- answer:
    Opal Networks “runs on AWS in three regions: us-west-2, eu-west-1, ap-southeast-1.” Source: 05_large_50_page.pdf.

### ✅ `05_large_50_page.pdf` — What are the RTO and RPO targets for Opal Networks?
- score: 1.0 | dimensions: direct_lookup | 6.6s
- answer:
    Opal Networks’ targets are **“RTO 25 minutes, RPO 60 seconds.”** Source: **05_large_50_page.pdf** (Chapter 8 / Appendix A).

### ✅ `05_large_50_page.pdf` — Which third-party firm performed Opal Networks' most recent penetration test?
- score: 1.0 | dimensions: long_doc_retrieval, direct_lookup | 8.2s
- answer:
    Opal Networks’ most recent third-party penetration test was performed by **Praesidium Security** — the document states: **“Quarterly third-party pen-test (latest: Praesidium Security, March 2026).”**  
    Source: **05_large_50_page.pdf** (Chapter 9 / Appendix A)

### ✅ `05_large_50_page.pdf` — Does Opal Networks use Azure as one of its cloud providers?
- score: 1.0 | dimensions: not_present | 7.1s
- answer:
    No. The document says Opal Networks “runs on AWS in three regions: us-west-2, eu-west-1, ap-southeast-1.” It does not mention Azure as a cloud provider. Source: 05_large_50_page.pdf.
