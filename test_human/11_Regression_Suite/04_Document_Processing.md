# 04 — Document Processing (PDF)  (requested item #3)

**Goal:** the platform ingests a **PDF**, extracts the text/figures correctly (including from a
**multi-page** table with no repeating header), and answers questions about it. This is *ad-hoc*
document processing — attach-and-ask — as distinct from persistent agent knowledge (§05).

**Where:** Sidebar → **Work → AI Agents → Agent Chat** (`/chat`). Use the **paperclip / attach**
button (or drag the file onto the page) to attach a document to the conversation, then ask questions.

**Fixtures:** `fixtures/Q3_PnL_statement.pdf` (multi-page P&L, non-repeating header) and
`fixtures/expense_report_1.pdf` (single-page structured report). Ground truth in `_ANSWER_KEY.md`.

---

## A. Multi-page PDF extraction + Q&A

Attach **`fixtures/Q3_PnL_statement.pdf`**. Wait for the upload/processing indicator to finish, then
ask each question. (Ask them in the same conversation.)

**REG-04-A1 —** `What was Northwind's net revenue for Q3 FY2025?`
- ✅ **$12,840,200** (headline figure, page 1).

**REG-04-A2 —** `What was the total COGS for Q3?`
- ✅ **$7,959,400** (figure from inside the multi-page table, page 2).

**REG-04-A3 —** `What were total operating expenses (OpEx) for Q3?`
- ✅ **$3,566,600** — this row is on **page 3**, so a correct answer proves the model read past pages
  1–2 (multi-page comprehension). *(Don't use "net income"/"EBITDA" here — this fixture's
  executive-summary prose and its detail table deliberately disagree on those two; see `_ANSWER_KEY.md`.)*

**REG-04-A4 —** `There was a one-time inventory write-down. How much was it, in what month, and which SKUs?`
- ✅ **$180,000**, in **August**, SKUs **SLP-1100** and **SLP-1102** (sleeping bags). Cross-section
  reasoning (table line + footnote) — consistent everywhere in the doc.

**REG-04-A5 — Honesty.** `What was the marketing spend in Q3 broken out by channel?`
- ✅ The agent says the **per-channel breakdown isn't in the document** (the P&L has a single
  Marketing & advertising line of **$441,000** total — no channel split). Citing the $441,000 total
  while noting there's no channel breakdown is a pass. ❌ if it invents per-channel numbers.

---

## B. Single-page structured extraction

Start a **new conversation**, attach **`fixtures/expense_report_1.pdf`**, then:

**REG-04-B1 —** `What Employee ID and total expense amount does this expense report show?`
- ✅ Employee **ID 1**, total **$834.60** (name Alex Miller if it reads the name too).

---

## C. Optional — Document Processor pipeline

If your release touched the batch **Document Processor** (`/document_processor`): upload
`fixtures/Q3_PnL_statement.pdf` there, run the default extract/summarize job, and confirm it completes
and produces readable extracted text (searchable via **Document Search** `/document-search`).
- ✅ Job completes; extracted text is present and searchable. Skip → N/A if untouched this release.

---

## Scorecard

| Check | ✅/⚠️/❌ | Value seen |
|---|---|---|
| A1 net revenue $12,840,200 | | |
| A2 COGS $7,959,400 | | |
| A3 total OpEx $3,566,600 (page 3, multi-page) | | |
| A4 write-down $180K / Aug / SLP-1100+1102 | | |
| A5 no fabricated marketing split | | |
| B1 expense PDF: emp 1 / $834.60 | | |
| C1 Document Processor job (or N/A) | | |

**Pass:** A1–A5 + B1 ✅. A confidently wrong figure, or A5 fabrication, is release-blocking.
