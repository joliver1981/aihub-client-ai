# File-Creation Tools — BRUTAL Tier Report

Generated: 2026-05-29 13:17:05
Model: `gpt-5.4-mini`  temp=0.1

## Headline

- Cases: **13**
- PASS **13** · PARTIAL **0** · FAIL **0**
- Composite (PASS=1, PARTIAL=0.5): **100.0%**
- Granular checks: **79/79 (100.0%)**

| # | Skill | Verdict | Score | Checks | Elapsed | Failing checks |
|---|---|:--:|--:|--:|--:|---|
| B1 | 4-level nested JSON | **PASS** | 100% | 7/7 | 8s | — |
| B2 | literal newlines inside CSV cells | **PASS** | 100% | 5/5 | 5s | — |
| B3 | formula / CSV-injection preservation | **PASS** | 100% | 5/5 | 6s | — |
| B4 | big-integer & precision preservation | **PASS** | 100% | 6/6 | 7s | — |
| B5 | RTL + CJK + emoji + diacritics | **PASS** | 100% | 8/8 | 6s | — |
| B6 | 30-column wide CSV | **PASS** | 100% | 5/5 | 18s | — |
| B7 | FOUR artifacts in one turn | **PASS** | 100% | 8/8 | 8s | — |
| B8 | 3 heading levels + 2 tables (one computed) | **PASS** | 100% | 6/6 | 9s | — |
| B9 | JSON array of 50 objects | **PASS** | 100% | 6/6 | 22s | — |
| B10 | signed accounting arithmetic | **PASS** | 100% | 3/3 | 8s | — |
| B11 | triple-threat CSV field | **PASS** | 100% | 6/6 | 6s | — |
| B12 | Excel transpose reasoning | **PASS** | 100% | 6/6 | 6s | — |
| B13 | nested markdown + multi-language code blocks | **PASS** | 100% | 8/8 | 7s | — |

## Per-case detail

### B1 — company→regions→stores→departments→{name,headcount} (PASS, 100%)
- Artifacts: ['hierarchy.json']

| Check | Result | Detail |
|---|:--:|---|
| expected type(s) ['.json'] produced | ✅ | got ['.json'] |
| valid JSON | ✅ | — |
| company == Northwind Outdoor | ✅ | Northwind Outdoor |
| regions array len 2 | ✅ | 2 |
| West region present | ✅ | — |
| 4-level leaf: W-01 Sales=4 & Returns=2 | ✅ | sales=4 returns=2 |
| E-09 Sales=7 | ✅ | — |

### B2 — multiline quoted cells must not inflate the row count (PASS, 100%)
- Artifacts: ['notes.csv']

| Check | Result | Detail |
|---|:--:|---|
| expected type(s) ['.csv'] produced | ✅ | got ['.csv'] |
| exactly 3 data rows (newlines did NOT inflate) | ✅ | 3 rows |
| at least 2 cells contain real line breaks | ✅ | 2 multiline |
| two-line note has 'first' & 'second' | ✅ | — |
| list note has milk/eggs/bread | ✅ | — |

### B3 — '=SUM(A1:A2)' kept as literal text, not executed/altered (PASS, 100%)
- Artifacts: ['formulas.csv']

| Check | Result | Detail |
|---|:--:|---|
| expected type(s) ['.csv'] produced | ✅ | got ['.csv'] |
| '=SUM(A1:A2)' preserved literally | ✅ | ['=SUM(A1:A2)', '=1+1', "=cmd|'/c calc'!A1", '+1234'] |
| '=1+1' preserved (not evaluated to 2) | ✅ | — |
| '=cmd|'/c calc'!A1' injection string preserved | ✅ | — |
| '+1234' preserved with leading + | ✅ | — |

### B4 — 20-digit account # and 15-dp decimal kept exactly (PASS, 100%)
- Artifacts: ['precision.csv']

| Check | Result | Detail |
|---|:--:|---|
| expected type(s) ['.csv'] produced | ✅ | got ['.csv'] |
| 20-digit account preserved exactly | ✅ | — |
| pi to 15 dp preserved | ✅ | — |
| card '4111 1111 1111 1111' preserved | ✅ | — |
| zip '00501' keeps leading zeros | ✅ | — |
| no scientific notation leaked | ✅ | — |

### B5 — Arabic/Hebrew/Chinese/Japanese/emoji round-trip (PASS, 100%)
- Artifacts: ['greetings.json']

| Check | Result | Detail |
|---|:--:|---|
| expected type(s) ['.json'] produced | ✅ | got ['.json'] |
| valid JSON | ✅ | — |
| Arabic مرحبا present | ✅ | — |
| Hebrew שלום present | ✅ | — |
| Chinese 你好 present | ✅ | — |
| Japanese こんにちは present | ✅ | — |
| emoji 🚀🔥🎯 present | ✅ | — |
| café accent preserved | ✅ | — |

### B6 — 30 columns in exact order, 5 aligned rows (PASS, 100%)
- Artifacts: ['wide.csv']

| Check | Result | Detail |
|---|:--:|---|
| expected type(s) ['.csv'] produced | ✅ | got ['.csv'] |
| exactly 30 columns | ✅ | 30 |
| columns c1..c30 in order | ✅ | first5=['c1', 'c2', 'c3', 'c4', 'c5'] last3=['c28', 'c29', 'c30'] |
| 5 data rows | ✅ | 5 |
| cell c7/row1 == 'c7r1' (alignment) | ✅ | got c7r1 |

### B7 — csv + xlsx + docx + json from one request, consistent (PASS, 100%)
- Artifacts: ['q_data.json', 'q_brief.docx', 'q_sheet.xlsx', 'q_raw.csv']

| Check | Result | Detail |
|---|:--:|---|
| expected type(s) ['.csv', '.xlsx', '.docx', '.json'] produced | ✅ | got ['.csv', '.docx', '.json', '.xlsx'] |
| .csv artifact produced | ✅ | got ['.csv', '.docx', '.json', '.xlsx'] |
| .xlsx artifact produced | ✅ | got ['.csv', '.docx', '.json', '.xlsx'] |
| .docx artifact produced | ✅ | got ['.csv', '.docx', '.json', '.xlsx'] |
| .json artifact produced | ✅ | got ['.csv', '.docx', '.json', '.xlsx'] |
| CSV has 3 rows incl. Tent/120 | ✅ | 3 rows |
| JSON array has 3 product objects | ✅ | [{'product': 'Tent', 'units': 120}, {'product': 'Pack', 'units': 80}, {'product': 'Stove', |
| Word brief names best-seller 'Tent' | ✅ | Quarter Brief
Summary
The best-selling product is Tent with 120 units.
Other products in t |

### B8 — Word H1/H2/H3 nesting, two tables, second has correct total (PASS, 100%)
- Artifacts: ['report.docx']

| Check | Result | Detail |
|---|:--:|---|
| expected type(s) ['.docx'] produced | ✅ | got ['.docx'] |
| has Heading 1 | ✅ | ['Annual Report'] |
| has Heading 2 'Financials' | ✅ | ['Financials'] |
| has Heading 3 'Revenue' & 'Expenses' | ✅ | ['Revenue', 'Expenses'] |
| two tables | ✅ | 2 tables |
| Expenses TOTAL row == 420 | ✅ | — |

### B9 — 50 contiguous objects (id 1..50), valid JSON (PASS, 100%)
- Artifacts: ['series.json']

| Check | Result | Detail |
|---|:--:|---|
| expected type(s) ['.json'] produced | ✅ | got ['.json'] |
| valid JSON | ✅ | — |
| is an array | ✅ | list |
| exactly 50 objects | ✅ | 50 |
| ids 1..50 contiguous | ✅ | first=[1.0, 2.0, 3.0] last=[48.0, 49.0, 50.0] |
| square == id*id for all | ✅ | — |

### B10 — net total is correctly NEGATIVE (PASS, 100%)
- Artifacts: ['ledger.csv']

| Check | Result | Detail |
|---|:--:|---|
| expected type(s) ['.csv'] produced | ✅ | got ['.csv'] |
| NET row present | ✅ | — |
| NET == -550 (signed sum correct) | ✅ | got -550.0 |

### B11 — one cell with comma + double-quote + newline together (PASS, 100%)
- Artifacts: ['tricky.csv']

| Check | Result | Detail |
|---|:--:|---|
| expected type(s) ['.csv'] produced | ✅ | got ['.csv'] |
| exactly 2 data rows | ✅ | 2 rows |
| row1 has a comma | ✅ | 'Hello, he said "go"\nthen left' |
| row1 has double-quotes | ✅ | — |
| row1 has a newline | ✅ | — |
| row1 reads 'Hello, he said "go"\nthen left' | ✅ | 'Hello, he said "go"\nthen left' |

### B12 — Sheet2 is Sheet1 transposed; cross-checked values match (PASS, 100%)
- Artifacts: ['pivot.xlsx']

| Check | Result | Detail |
|---|:--:|---|
| expected type(s) ['.xlsx'] produced | ✅ | got ['.xlsx'] |
| has ByRow sheet | ✅ | ['ByRow', 'Transposed'] |
| has Transposed sheet | ✅ | ['ByRow', 'Transposed'] |
| month headers Jan/Feb/Mar in transposed sheet | ✅ | — |
| values 100/200/300 present in transposed sheet | ✅ | — |
| 'Sales' row carries 100,200,300 across month columns | ✅ | — |

### B13 — 2-level bullet nesting + table + python AND json fences (PASS, 100%)
- Artifacts: ['guide.md']

| Check | Result | Detail |
|---|:--:|---|
| expected type(s) ['.md'] produced | ✅ | got ['.md'] |
| '# Guide' heading | ✅ | — |
| '## Steps' / '## Schema' / '## Snippets' | ✅ | — |
| 2-level nested bullet list | ✅ | — |
| markdown table present | ✅ | — |
| python fenced block | ✅ | — |
| json fenced block | ✅ | — |
| >=2 fenced blocks total (>=4 backticks-fences) | ✅ | 4 fences |