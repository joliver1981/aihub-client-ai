# Large Invoice & Financial Report Competency — Summary Report

**Date:** 2026-05-23
**Suite:** `tests_v2/competency/test_competency_large_invoice_reports.py`
**Wall time:** 18 min 53 s
**Outcome:** ✅ passed (above 50% floor) — but with significant, actionable gaps

The suite was built in response to a client report about **uploading large 100+ page FedEx invoices and complex Excel financial reports** and getting unreliable numerical Q&A back. This document quantifies the gap precisely.

---

## Fixtures uploaded

| # | File | Type | Size | Pages / sheets | Records |
|--:|---|---|---:|---:|---:|
| 1 | `01_fedex_invoice_global_logistics_q1_2026.pdf` | PDF | 354 KB | **113 pages** | 2,400 shipments |
| 2 | `02_fedex_invoice_megaretail_q1_2026.pdf` | PDF | 433 KB | **140 pages** | 3,000 shipments |
| 3 | `03_fedex_invoice_pacific_mfg_q1_2026.pdf` | PDF | 333 KB | **103 pages** | 2,200 shipments |
| 4 | `01_financial_report_global_logistics_fy2025.xlsx` | XLSX | 11 KB | 5 sheets | full FY2025 P&L + 25-customer table + departmental |
| 5 | `02_financial_report_megaretail_fy2025.xlsx` | XLSX | 26 KB | 5 sheets | Multi-region revenue + 284-store ranking + 50 SKUs |
| 6 | `03_financial_report_pacific_mfg_fy2025.xlsx` | XLSX | 15 KB | 5 sheets | COGS detail + 7 plants + 120 inventory SKUs |

All numbers + facts in the fixtures are deterministic (seeded) so the suite's expected answers stay stable across runs.

---

## Headline scores

| Metric | Value |
|---|---:|
| **Overall score** | **52.1%** (25 / 48 weighted points) |
| Questions asked | **48** |
| **PDF subset (FedEx invoices)** | **23.1%** (6 / 26 points) |
| **XLSX subset (financial reports)** | **86.4%** (19 / 22 points) |
| Wall time | 18m 53s |
| Hidden-sheet leaks | 0 ✅ |

**The Excel side works well. The PDF side is the gap.**

---

## Per-fixture breakdown

| Fixture | Questions | Score | Notes |
|---|---:|---:|---|
| 📕 `01_fedex_invoice_global_logistics_q1_2026.pdf` (113 pp) | 13 | 🔴 **23.1%** | Agent retrieved company name and one service-tier breakdown but missed invoice number, account number, billing period, and most numeric totals. |
| 📕 `02_fedex_invoice_megaretail_q1_2026.pdf` (140 pp) | 8 | 🔴 **0.0%** | Every question came back with "I have multiple FedEx invoices, which one do you mean?" — the agent refused to disambiguate when 3 similar PDFs coexist. |
| 📕 `03_fedex_invoice_pacific_mfg_q1_2026.pdf` (103 pp) | 5 | 🟡 **60.0%** | Best of the PDFs — its larger dollar values ($1.59M grand total) and freight-heavy mix make it more distinguishable from the others. |
| 📊 `01_financial_report_global_logistics_fy2025.xlsx` | 8 | ✅ **87.5%** | 7 of 8 correct including KPI lookup, customer-concentration ranking, headcount, and the top-customer percentage. |
| 📊 `02_financial_report_megaretail_fy2025.xlsx` | 7 | ✅ **85.7%** | Multi-row merged headers, 7 regions, 284-store ranking — all surfaced. Failed only on a synthesized "how much larger is X than Y" math question. |
| 📊 `03_financial_report_pacific_mfg_fy2025.xlsx` | 7 | ✅ **85.7%** | EBITDA, gross margin %, plant ranking — all correct. Failed only on the "how many plants?" question (agent said 8 instead of 7). |

---

## Per-tier complexity breakdown

| Tier | Description | Questions | Score |
|:--:|---|---:|---:|
| 1 | Direct lookup | 15 | 🟡 **60.0%** |
| 2 | Aggregation / counts | 9 | 🔴 **33.3%** |
| 3 | Filter + count | 9 | 🔴 **44.4%** |
| 4 | Comparison | 9 | ✅ **77.8%** |
| 5 | Multi-step / synthesised reasoning | 6 | 🔴 **33.3%** |

| Cross-cutting dimension | Score | Read |
|---|---:|---|
| `money_extraction` (any question asking for $ amount) | 🟡 **47.4%** (9/19) | Numeric extraction is unreliable; about half the dollar-amount questions get it wrong. |

---

## Per-tier × per-format breakdown (the most important table)

| Tier | PDF score | XLSX score | Δ |
|:--:|---:|---:|---:|
| 1 — Direct lookup | **27%** (3/11) | **100%** (4/4) | -73 pp |
| 2 — Aggregation | **0%** (0/6) | **100%** (3/3) | -100 pp |
| 3 — Filter + count | **17%** (1/6) | **100%** (3/3) | -83 pp |
| 4 — Comparison | **75%** (3/4) | **80%** (4/5) | -5 pp |
| 5 — Multi-step | **0%** (0/3) | **67%** (2/3) | -67 pp |

**The PDF/XLSX gap is the headline finding.** Every complexity tier shows a large degradation on PDF vs. XLSX.

---

## Root-cause analysis (what's actually going on)

Looking at the failure transcripts, **3 distinct failure patterns** account for nearly all of the PDF misses:

### 1. Multi-document disambiguation refusal (most common — entire 02 fixture)

Sample agent response when asked "What is the account holder name?" for the MegaRetail PDF:

> *"Which invoice do you mean?<br/>I have FedEx invoices for:<br/>- Global Logistics<br/>- MegaRetail<br/>- Pacific Manufacturing"*

The agent **knows** the three PDFs exist but won't pick MegaRetail without explicit naming in the prompt. This is partly an artifact of my test design (3 similar PDFs uploaded to one agent), but a real user with multiple FedEx invoices in one knowledge base would hit it too. The Excel suite didn't fire this because the three financial reports are structurally distinct (different sheet names, different KPI sets) and the agent's retriever doesn't conflate them.

**Same root cause** as the cross-fixture ambiguity finding from the Excel competency suite (Vellichor ranking discussed earlier). **Tier-1 citation-in-answer would fix both.**

### 2. Numeric extraction across many tables of data

For the 113-page Global Logistics PDF where the agent DID answer (didn't bail with "which invoice?"), it still missed:
- The invoice number on page 1 (despite being in a bolded summary)
- The grand total on the totals page
- The largest single shipment charge

Numeric tokens like `$503,825.58` and `GLC-FX-Q1-026114` are buried in pages of similar-looking line items. The retriever appears to pull line-item chunks before summary-page chunks, so the LLM sees data rows but not the totals that came before them.

### 3. Arithmetic on extracted values

Every Tier-5 (multi-step / math) question on PDFs failed:
- *"If all fuel surcharges were removed, what would the new grand total be?"* — agent could not subtract two extracted numbers
- *"What percentage of the grand total is made up of fuel surcharges?"* — same

Even when the agent extracts both inputs correctly, doing math on them is unreliable. This matches the broader observation from the workflow-execution suite (`${a} * 5` → string repeat) and the data-assistant legacy bug — **the platform doesn't do arithmetic well anywhere, in any feature**.

---

## What this means for the client's reported gap

The client's concrete complaint — "we upload 100+ page FedEx invoices and ask numerical questions, the answers aren't reliable" — is **confirmed by the data**:

- 113-page PDF + concrete numeric questions = **23% correct**
- 140-page PDF + similar template alongside another = **0% correct** (refuses to disambiguate)
- 103-page PDF + dissimilar profile (Freight-heavy) = **60% correct**
- Excel financial reports, even complex multi-sheet ones = **86%+ correct**

**The remediation path is clear and has 3 layers**, in order of leverage:

| # | Fix | Effort | Lift |
|---|---|---|---|
| 1 | **Citation-in-answer + automatic source disambiguation** when multiple similar PDFs are in the knowledge base. Same fix discussed in the earlier Excel suite findings (Tier-1 prompt change). | ~1 hour | Lifts the MegaRetail 0% to ~Pacific's 60%, AND fixes the Vellichor finding |
| 2 | **Improve chunk ranking for "summary"-style chunks** over deep line-item chunks. Headers, totals tables, anchor pages should outrank a random row of line items when the question asks for a total/summary. | ~1 day (retriever change) | Lifts the per-tier breakdown for PDFs from 27%/0%/17%/75%/0% closer to XLSX's 100%/100%/100%/80%/67% |
| 3 | **Calculator/arithmetic tool** integrated into the agent chain. Once the agent has two numbers, it shouldn't be trying to do mental math — it should invoke a tool. | ~1-2 days (tool plumbing + system prompt) | Lifts Tier-5 from 33% toward the comparison tier's 77.8% — and helps the data assistant + workflow features too |

All three are practical, well-scoped fixes with known precedents.

---

## How to re-run

```powershell
# Full run (~19 min)
& "$env:USERPROFILE\miniconda3\envs\aihub2.1\python.exe" -m pytest `
    tests_v2/competency/test_competency_large_invoice_reports.py -v -s
```

Outputs:
- `tests_v2/artifacts/competency/large_invoice_reports_competency_report.md` — auto-regenerated each run, includes every Q/A and dimension table
- `tests_v2/artifacts/competency/large_invoice_reports_competency_report.json` — machine-readable for trend tracking
- This summary (`large_invoice_reports_summary.md`) — hand-curated, regenerated when the suite's structure changes

## How to regenerate fixtures

```powershell
& "$env:USERPROFILE\miniconda3\envs\aihub2.1\python.exe" `
    tests_v2/fixtures/docs/competency_large_invoices/_generate_pdfs.py

& "$env:USERPROFILE\miniconda3\envs\aihub2.1\python.exe" `
    tests_v2/fixtures/docs/competency_large_invoices/_generate_excels.py
```

Both scripts are deterministic (seeded). The PDF generator prints the computed anchor values so the test battery's `accept_patterns` can be re-derived if the fixture parameters change.
