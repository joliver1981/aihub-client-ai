# 08 — Artifacts: Command Center & General Agents  (requested item #7)

**Goal:** both surfaces can generate a **real, downloadable artifact** (chart / CSV / Excel) with
**correct values** — not just describe one. A "here's your chart" with no downloadable file, or with
wrong numbers, is a fail.

**Fixture:** `fixtures/daily_sales_sample.csv` (14 rows, 2 stores × 7 days).
**Ground truth (`_ANSWER_KEY.md`):** total revenue **$53,100.00**, total units **1,770**; by store —
**Manhattan $30,000 / 1,000 units**, **Brooklyn $23,100 / 770 units**; highest single day **2026-06-05
Manhattan $6,000**.

Absolute fixture path (paste as-is):
`C:\src\aihub-client-ai-dev\test_human\11_Regression_Suite\fixtures\daily_sales_sample.csv`

---

## A. Command Center artifact (code interpreter / run_python)

**Where:** Command Center (`http://localhost:5091`), as `admin`.

**REG-08-A1 —** Paste:
> Use Python to read the CSV at
> `C:\src\aihub-client-ai-dev\test_human\11_Regression_Suite\fixtures\daily_sales_sample.csv`.
> Compute total revenue and total revenue by store. Then produce two downloadable artifacts:
> (1) a **bar chart** of revenue by store (PNG), and (2) a **summary CSV** with columns
> store, total_units, total_revenue. Show me the numbers and the download links.

- ✅ **Numbers correct:** total revenue **$53,100**, Manhattan **$30,000**, Brooklyn **$23,100**.
- ✅ A **bar chart renders inline** (2 bars, Manhattan taller) and there is a **download link** for
  the PNG and the CSV — actual artifacts, not just a described result.

**REG-08-A2 — The artifact is real.** Click/download the summary CSV (or have the agent report its
path), then confirm on disk:
```bash
# adjust to the download link CC gives you, or the CC outputs/artifacts dir:
cat "<downloaded summary csv>"
```
- ✅ The CSV has Manhattan → 1000 / 30000 and Brooklyn → 770 / 23100. ❌ if the link 404s or the
  numbers differ.

---

## B. General-agent artifact

**Where:** Agent Chat (`/chat`), as `admin`. Select a general agent that has file/artifact tools
(the default assistant is fine).

**REG-08-B1 —** Attach `fixtures/daily_sales_sample.csv` (paperclip), then:
> From this CSV, create a **downloadable Excel (or CSV) file** that summarizes total units and total
> revenue **by store**, one row per store.

- ✅ The agent returns a **downloadable artifact** (a file with a download link/button in the reply),
  and the summary shows Manhattan **1,000 / $30,000** and Brooklyn **770 / $23,100**.
- ✅ **REG-08-B2 — The download works:** click it; a real file downloads and opens with those values.
  ❌ if the "artifact" is just text with no file, or the link is dead.

**REG-08-B3 — Honesty.** If the selected agent has **no** artifact/file tool, it should say so rather
than pretend to attach a file. (Then re-select an agent that does, or mark B1/B2 N/A with a note.)

---

## Scorecard

| Check | ✅/⚠️/❌ | Evidence |
|---|---|---|
| A1 CC numbers correct + chart + links | | |
| A2 CC artifact file real & correct | | |
| B1 general-agent artifact + right values | | |
| B2 download actually works | | |
| B3 honest if no tool (or N/A) | | |

**Pass:** A1–A2 ✅ and B1–B2 ✅ (or B N/A with B3 honest). A described-but-missing artifact, a dead
download link, or wrong totals = fail.
