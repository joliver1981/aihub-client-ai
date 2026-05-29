# File-Creation Tools — HARD Competency Report (human-style)

Generated: 2026-05-29 13:15:02
Model: `gpt-5.4-mini`  temp=0.1

## Headline

- Cases: **14**
- PASS **14** · PARTIAL **0** · FAIL **0**
- Composite (PASS=1, PARTIAL=0.5): **100.0%**
- Granular check pass-rate: **100.0%** (83/83 checks)

## Results

| # | Skill tested | Verdict | Score | Checks | Elapsed | Failing checks |
|---|---|:--:|--:|--:|--:|---|
| H1 | data fidelity (special chars/unicode) | **PASS** | 100% | 8/8 | 6s | — |
| H2 | computed values (arithmetic correctness) | **PASS** | 100% | 6/6 | 16s | — |
| H3 | code preservation (leading zeros) | **PASS** | 100% | 5/5 | 5s | — |
| H4 | cross-sheet aggregation | **PASS** | 100% | 6/6 | 9s | — |
| H5 | group-by / pivot reasoning | **PASS** | 100% | 4/4 | 11s | — |
| H6 | structured document (table fidelity) | **PASS** | 100% | 7/7 | 7s | — |
| H7 | computed values in a document table | **PASS** | 100% | 4/4 | 8s | — |
| H8 | nested JSON schema fidelity | **PASS** | 100% | 8/8 | 8s | — |
| H9 | HTML structural correctness | **PASS** | 100% | 7/7 | 8s | — |
| H10 | markdown structural correctness | **PASS** | 100% | 6/6 | 9s | — |
| H11 | multi-artifact orchestration | **PASS** | 100% | 7/7 | 10s | — |
| H12 | wide-schema fidelity | **PASS** | 100% | 5/5 | 9s | — |
| H13 | data cleaning judgment | **PASS** | 100% | 5/5 | 8s | — |
| H14 | JSON escaping fidelity | **PASS** | 100% | 5/5 | 5s | — |

## Per-case detail

### H1 — CSV with commas, quotes, apostrophes & umlauts inside fields  (PASS, 100%)
- Skill: data fidelity (special chars/unicode)
- Artifacts: ['customers.csv']

| Check | Result | Detail |
|---|:--:|---|
| expected file type(s) ['.csv'] produced | ✅ | got ['.csv'] |
| 4 data rows | ✅ | 4 rows |
| headers name/city/note | ✅ | ['name', 'city', 'note'] |
| 'Acme, Inc.' kept as one field (comma not split) | ✅ | names=['Acme, Inc.', "O'Brien & Sons", 'Cafe Düsseldorf', 'Globex'] |
| apostrophe value 'O'Brien & Sons' intact | ✅ | — |
| embedded double-quotes preserved | ✅ | — |
| umlauts ä ö ü ß preserved | ✅ | — |
| Zürich / Düsseldorf accents preserved | ✅ | — |

### H2 — CSV invoice with a computed line_total column  (PASS, 100%)
- Skill: computed values (arithmetic correctness)
- Artifacts: ['invoice_lines.csv']

| Check | Result | Detail |
|---|:--:|---|
| expected file type(s) ['.csv'] produced | ✅ | got ['.csv'] |
| has line_total column | ✅ | ['item', 'quantity', 'unit_price', 'line_total'] |
| Widget A line_total == 54.0 | ✅ | got 54.0 |
| Widget B line_total == 139.93 | ✅ | got 139.93 |
| Widget C line_total == 85.0 | ✅ | got 85.0 |
| TOTAL row == 278.93 | ✅ | got 278.93 |

### H3 — CSV product codes 00123 / 00045 must keep leading zeros  (PASS, 100%)
- Skill: code preservation (leading zeros)
- Artifacts: ['product_codes.csv']

| Check | Result | Detail |
|---|:--:|---|
| expected file type(s) ['.csv'] produced | ✅ | got ['.csv'] |
| code 00123 present with leading zeros | ✅ | — |
| code 00045 present with leading zeros | ✅ | — |
| code 01000 present with leading zeros | ✅ | — |
| code 000007 present with leading zeros | ✅ | — |

### H4 — Excel Detail + Summary; summary totals must match detail  (PASS, 100%)
- Skill: cross-sheet aggregation
- Artifacts: ['regional_report.xlsx']

| Check | Result | Detail |
|---|:--:|---|
| expected file type(s) ['.xlsx'] produced | ✅ | got ['.xlsx'] |
| has Detail sheet | ✅ | ['Detail', 'Summary'] |
| has Summary sheet | ✅ | ['Detail', 'Summary'] |
| Summary West total == 2000 | ✅ | got 2000.0 |
| Summary East total == 2000 | ✅ | got 2000.0 |
| Summary North total == 2000 | ✅ | got 2000.0 |

### H5 — Excel summarise orders by category (count + total)  (PASS, 100%)
- Skill: group-by / pivot reasoning
- Artifacts: ['category_summary.xlsx']

| Check | Result | Detail |
|---|:--:|---|
| expected file type(s) ['.xlsx'] produced | ✅ | got ['.xlsx'] |
| Tents: count 3 & total 1000 present | ✅ | nums=[3.0, 1000.0] |
| Packs: count 2 & total 400 present | ✅ | nums=[2.0, 400.0] |
| Cookware: count 3 & total 300 present | ✅ | nums=[3.0, 300.0] |

### H6 — Word Q3 report: title + 3 sections + accurate data table  (PASS, 100%)
- Skill: structured document (table fidelity)
- Artifacts: ['q3_report.docx']

| Check | Result | Detail |
|---|:--:|---|
| expected file type(s) ['.docx'] produced | ✅ | got ['.docx'] |
| title 'Q3 Regional Performance' present near top | ✅ | Q3 Regional Performance | Executive Summary | Results by Region | Recommendation |
| section 'Executive Summary' | ✅ | Q3 Regional Performance | Executive Summary | Results by Region | Recommendation |
| section 'Results by Region' | ✅ | Q3 Regional Performance | Executive Summary | Results by Region | Recommendation |
| section 'Recommendations' | ✅ | Q3 Regional Performance | Executive Summary | Results by Region | Recommendation |
| has a table | ✅ | 1 tables |
| table contains West / 2.87M / 12% | ✅ | Region | Revenue | YoY% | West | 2.87M | 12% | East | 2.43M | 8% | North | 2.10M |

### H7 — Word doc whose table TOTAL row sums the line items  (PASS, 100%)
- Skill: computed values in a document table
- Artifacts: ['budget.docx']

| Check | Result | Detail |
|---|:--:|---|
| expected file type(s) ['.docx'] produced | ✅ | got ['.docx'] |
| has a table | ✅ | — |
| four line items present | ✅ | [120000.0, 18000.0, 9500.0, 22500.0] |
| TOTAL row == 170000 | ✅ | got 170000.0 |

### H8 — JSON org chart: nested departments→employees arrays  (PASS, 100%)
- Skill: nested JSON schema fidelity
- Artifacts: ['org.json']

| Check | Result | Detail |
|---|:--:|---|
| expected file type(s) ['.json'] produced | ✅ | got ['.json'] |
| valid JSON | ✅ | — |
| company == Northwind Outdoor | ✅ | Northwind Outdoor |
| departments is a non-empty array | ✅ | — |
| Engineering dept present | ✅ | — |
| Engineering employees have name+role objects | ✅ | — |
| Ada & Linus present | ✅ | {'Linus', 'Ada'} |
| Sales→Grace present | ✅ | — |

### H9 — HTML with h1, an h2+table, and an h2+3-item list  (PASS, 100%)
- Skill: HTML structural correctness
- Artifacts: ['metrics_page.html']

| Check | Result | Detail |
|---|:--:|---|
| expected file type(s) ['.html'] produced | ✅ | got ['.html'] |
| exactly one <h1> | ✅ | 1 |
| >=2 <h2> | ✅ | 2 |
| has <table> | ✅ | — |
| >=3 table rows | ✅ | 4 |
| <ul> with exactly 3 <li> | ✅ | li=3 |
| metric values 9.48M / 12500 / 62 present | ✅ | — |

### H10 — Markdown with a parameter table + fenced bash code block  (PASS, 100%)
- Skill: markdown structural correctness
- Artifacts: ['api_guide.md']

| Check | Result | Detail |
|---|:--:|---|
| expected file type(s) ['.md'] produced | ✅ | got ['.md'] |
| level-1 heading '#' | ✅ | — |
| level-2 '## Parameters' | ✅ | — |
| level-2 '## Example' | ✅ | — |
| markdown table (pipes + --- separator) | ✅ | — |
| fenced code block tagged bash | ✅ | — |

### H11 — One turn → BOTH a CSV of raw data AND a Word summary  (PASS, 100%)
- Skill: multi-artifact orchestration
- Artifacts: ['sales_summary.docx', 'sales_raw.csv']

| Check | Result | Detail |
|---|:--:|---|
| expected file type(s) ['.csv', '.docx'] produced | ✅ | got ['.csv', '.docx'] |
| a .csv artifact was produced | ✅ | ['6bc867eb7330_sales_summary.docx', '0b93af8e56e5_sales_raw.csv'] |
| a .docx artifact was produced | ✅ | ['6bc867eb7330_sales_summary.docx', '0b93af8e56e5_sales_raw.csv'] |
| CSV has all 5 rows | ✅ | 5 rows |
| CSV contains the data (Ann/Di/2100) | ✅ | — |
| Word names top performer 'Di' | ✅ | Sales Summary
Top Performer
The top-performing rep is Di with sales of 2100. |
| Word states amount 2100 | ✅ | — |

### H12 — CSV with 15 columns, 3 rows, all headers present  (PASS, 100%)
- Skill: wide-schema fidelity
- Artifacts: ['employee_master.csv']

| Check | Result | Detail |
|---|:--:|---|
| expected file type(s) ['.csv'] produced | ✅ | got ['.csv'] |
| all 15 columns present | ✅ | missing [] |
| column order preserved | ✅ | got ['emp_id', 'first_name', 'last_name', 'email', 'department', 'title', 'hire_ |
| 3 data rows | ✅ | 3 rows |
| emails look valid | ✅ | — |

### H13 — Excel: normalise messy casing/spacing/decimals  (PASS, 100%)
- Skill: data cleaning judgment
- Artifacts: ['cleaned.xlsx']

| Check | Result | Detail |
|---|:--:|---|
| expected file type(s) ['.xlsx'] produced | ✅ | got ['.xlsx'] |
| 3 data rows | ✅ | 3 rows |
| names Title-Cased (John Smith / Jane Doe / Bob Lee) | ✅ | ['John Smith', 'Jane Doe', 'Bob Lee'] |
| departments Title-Cased (Sales/Ops) | ✅ | ['Sales', 'Sales', 'Ops'] |
| amounts numeric & 2-decimal (1200.50/980.00/1500.00) | ✅ | ['1200.50', '980.00', '1500.00'] |

### H14 — JSON containing quotes, backslashes & a Windows path  (PASS, 100%)
- Skill: JSON escaping fidelity
- Artifacts: ['config.json']

| Check | Result | Detail |
|---|:--:|---|
| expected file type(s) ['.json'] produced | ✅ | got ['.json'] |
| valid JSON (escaping correct) | ✅ | — |
| log_path == C:\Logs\app\trace.txt | ✅ | 'C:\\Logs\\app\\trace.txt' |
| regex == ^\d{3}-\d{4}$ | ✅ | '^\\d{3}-\\d{4}$' |
| quote_sample == He said "hello" | ✅ | 'He said "hello"' |