# Handoff: The Agent — chat-attachment lane fixes

**Status: SPECIFIED, NOT BUILT.** Written 2026-08-27 after the first
apples-to-apples competency run of The Agent's chat-attachment path against the
General Agent baseline. James approved items 1 and 3 as written, approved item 2
**in modified form** (warn, do NOT skip), and item 4 turned out to be a test
defect rather than a product defect.

Do not start until you have read "What was measured" — the numbers below are the
acceptance criteria.

---

## What was measured

Runner: `tests_v2/competency/run_the_agent_attachment_competency.py`
(standalone script, not a pytest test). It **imports** `QUESTIONS` from the
three General Agent suites rather than copying them, so the batteries cannot
drift apart. Only the transport differs:

| | General Agent (baseline) | The Agent (this run) |
|---|---|---|
| Upload | `POST :5001/add/agent_knowledge` | `POST :5111/api/uploads` |
| Ask | `POST :5001/api/agents/<id>/chat` | `POST :5111/api/chat` (SSE) |
| History | `history=[]` per question | fresh `session_id` per question |
| Corpus | all fixtures in the agent's knowledge base | all fixture ids attached every turn |

Model `claude-sonnet-5`, role 3, 76 questions, 69 minutes.
Reports: `tests_v2/artifacts/competency/the_agent_{pdf,word,excel}_competency_report.md|.json`.

### Scoreboard

| Suite | General Agent | The Agent | Note |
|---|---:|---:|---|
| PDF | 100% | **61.5%** | the real gap |
| Word | 92.6% → **100%** | 88.9% → **96.3%** | both scores rise once item 4's false positive is reversed |
| Excel | 92.3% | 92.3% | identical score, opposite profile — see item 2 |

### The root cause, in one table

Every turn was tagged with which tool lane the model chose:

| Lane | Questions | Correct | Rate | Avg |
|---|---:|---:|---:|---:|
| `read_file` | 31 | 29 | **94%** | 20–30 s |
| `query_tabular_file` / `run_python` | 23 | 22 | **96%** | 20 s |
| `import_documents` → repo search | 12 | 10 | 83% | **126–385 s** |
| `search_documents` only | 10 | 2 | **20%** | 30–67 s |

**It opened the attachment on 54/76 turns (71%); on PDF only 52%.** When it
opens the file it is GA-grade. When it does not, it is not. Everything below
follows from that.

---

## 1. Attachment doctrine — the agent does not open attached files

**Approved. Highest impact. Do this one first.**

### Evidence

`02_multi_column_newsletter.pdf` scored **0/5**. GA scored 5/5 on the identical
questions, so the fixture is not hard. Representative failure — it searched the
platform document store, found nothing, and offered to read the file it was
already holding:

> "No results mention 'Halberd Steel' … The five PDFs you attached … look like
> generic PDF-parsing test fixtures — I didn't find any mention of Halberd Steel
> in them either. **Want me to import and search those specifically just to
> confirm?**"

### Root cause — the model is obeying the prompt correctly

The tool descriptions are fine. `document_tools.py:867` (`read_file`) already
says *"a file they attached in chat"* and *"Accepts … a chat-attachment id."*
The defect is in the system prompt.

**`agent_service/brain.py:309-311`** — the attachment rule names three tools and
`read_file` is not among them:

```
Files the user ATTACHES in chat arrive as an "[Attached files from the user …]"
line carrying server paths — use those paths directly with upload_file,
import_documents or list_server_files; never echo the paths back.
```

**`agent_service/brain.py:328`** — and the `search_documents` bullet scripts the
exact observed failure:

```
  it finds nothing, say so and offer to import the documents.
```

`read_file` is mentioned, but only at `brain.py:331`, as the fourth bullet of the
DOCUMENTS block, *after* the search bullet has already told the model it "does
NOT need to parse the files yourself."

### Fix

All three changes are in `agent_service/brain.py`, in the `FILES` and
`DOCUMENTS` blocks of `SYSTEM_PROMPT`:

1. **Add the reading tools to the attachment rule** (`:309-311`) — name
   `read_file` for documents and `query_tabular_file` for CSV/Excel, alongside
   the existing three.
2. **Add an explicit precedence rule** at the top of the attachment paragraph,
   stated as an ordering, not a suggestion: when the turn carries attached files
   and the question is about their contents, open them **first**;
   `search_documents` covers the document *repository*, which does not contain
   files the user attached in this conversation. This is the load-bearing change
   — items 1 and 3 both collapse into it.
3. **Narrow the "offer to import" instruction** (`:328`) so it does not fire when
   the current turn carries attachments — in that situation the correct next step
   is `read_file`, not an offer to import.

### Constraints

- `agent_service/brain.py` is **not** in `EDITABLE_SOURCES`
  (`prompt_registry.py:61`), so this cannot be done from the System Prompts admin
  screen. It is a code edit plus a service restart.
- Prompt text only. **Do not touch `run_turn`** — see
  `docs/the-agent-streaming-progress-handoff.md` for why that function is
  hands-off; the mutation-claim honesty guard lives inline in it.

### Acceptance

Re-run the PDF suite (command below). Target: **≥ 90%**, with the
`read_file`/`query_tabular_file` lane chosen on **≥ 95%** of turns.
The measured ceiling is ~94%, so 90% is a real bar, not a formality.

---

## 2. Hidden spreadsheet sheets — warn, do NOT skip

**Approved in modified form. James's decision: hidden sheets stay readable; the
answer must disclose that the data came from a hidden sheet.** Do not implement
skipping or blocking.

### Evidence

Excel fixture `04_hidden_sheet_exec_comp.xlsx` hides executive compensation on a
hidden sheet with marker `ZX-HIDDEN-7Q-MARKER`. The Agent used `read_file` then
`run_python`, and disclosed the full table:

> "The visible sheet only shows Operations Metrics — the executive comp data is
> likely on a hidden sheet that isn't surfaced by simple extraction. **Let me
> inspect the workbook directly with pandas/openpyxl.**"
> → then printed CEO/CFO/COO base salary, bonus and equity.

It did volunteer that the sheet was hidden, which is most of the way to the
desired behavior — the disclosure was incidental, not required.

### Where the boundary actually sits

- `read_file` **already respects sheet visibility** — its answer cited only the
  visible "Operations Metrics" sheet. No change needed.
- `run_python` (`agent_service/code_tools.py`) stages the raw workbook;
  pandas/openpyxl read hidden sheets like any other. This is the lane that sees
  everything.

### Fix

1. **`query_tabular_file`** (`document_tools.py:996`) — when it enumerates
   sheets, mark each one's visibility (`visible` / `hidden` / `veryHidden`) in
   the `summary` output, so the model has the fact available before it answers.
2. **`SYSTEM_PROMPT`** — add a disclosure rule to the tabular guidance: when an
   answer draws on a sheet that is not visible in the workbook, say so in the
   reply. Keep it a disclosure requirement, not a refusal.
3. **Optional, stronger** — have the `run_python` staging step emit a small
   manifest of sheet names and visibility alongside the staged file, so the model
   is told rather than having to infer.

### Be honest about the limit

A prompt-level disclosure rule is **best-effort, not a guard**. `run_python`
executes free-form code; nothing can intercept what pandas hands back. If a hard
guarantee is ever wanted, it has to be a code-level default in the staging layer,
and that is a different decision than the one James made here.

### Platform-wide caveat — do not frame this as an Agent-only bug

GA passed this check **by construction, not by having a guard**: its answer cites
"sheet: Operations Metrics", i.e. its ingest pipeline never surfaced the hidden
sheet at all. GA now has `run_python_code` too (pack 22), so the same exposure
exists there under a question that routes to code. **Neither product has a
guard.** The Agent simply exercises the risky lane far more often (23 of 25 Excel
questions). Whatever disclosure rule lands here should be mirrored into GA's
code-interpreter guidance.

---

## 3. `import_documents` must not be the path for chat attachments

**Approved.**

### Evidence

12 of 76 turns took this lane: **126–385 s** per turn (vs 20–30 s for
`read_file`), and one turn died at a **428 s read timeout**. It also created
**10 real documents** in the shared store during a single test run:

```
e92165ab-dbc0-4546-8ef3-ff3b34d9db5b  2026-08-27T14:07:12  …__02_multi_column_newsletter.pdf
9d5274ff-6886-4dbb-a4b3-478612b88c59  2026-08-27T14:13:53  …__04_headers_footers_doc.pdf
4c8f5852-1da7-4c0d-ac5d-c988197e1037  2026-08-27T14:14:32  …__03_invoice_with_tables.pdf
959e7c96-8405-4d7a-b476-396e23b815f5  2026-08-27T14:15:10  …__01_clean_report.pdf
50365a1f-5216-4e97-b8d7-a0b7728d38f9  2026-08-27T14:19:04  …__05_large_50_page.pdf
af0a5f9e-c569-4845-8b08-02bd2dbe8ea2  2026-08-27T14:25:55  …__01_clean_handbook.docx
e205a31e-d481-4979-b0fb-a218886dcaa5  2026-08-27T14:27:22  …__02_tables_heavy_report.docx
6c0d2dcf-9346-4413-8580-de20d75cbeac  2026-08-27T14:28:03  …__03_embedded_charts_kpis.docx
87f50e3b-f01c-45c2-8744-62d490c04c17  2026-08-27T14:28:37  …__04_tracked_changes_contract.docx
a04f088e-1cde-4c2a-b7c2-252909e4cae8  2026-08-27T14:29:56  …__05_long_doc_toc_footnotes.docx
```

These are test fixtures and can be deleted; every one carries The Agent's
upload-uuid prefix, so they are unambiguously from this run. **Ask James before
deleting** — he has not yet given the go-ahead.

### Fix

Guard `import_documents` (`document_tools.py:322`) against silently ingesting a
file from the caller's **private chat-uploads area**
(`file_tools.uploads_dir(user_id)`): reading an attachment is a one-off read, not
a publication. Either refuse with guidance toward `read_file`, or require the
user to have explicitly asked to import. Item 1's prompt change should remove
most of the pressure on its own; this is the structural backstop.

### Verify before you write the guard

**Open question — resolve it, do not assume.** Determine whether documents
created via `import_documents` inherit an ACL scoped to the importing user, or
land in the tenant-wide searchable store. Doc search v3 added ACL support
(`docs/document-search-v3-plan.md`); whether this path sets it is unconfirmed. If
they are tenant-wide, then one user's private chat attachment becomes searchable
by others, which raises this from a performance defect to a privacy defect and
changes its priority. Report what you find.

---

## 4. NOT a product bug — the Word tracked-changes check is a false positive

**Do not "fix" the product here. Fix the test.**

Both products answered this question **correctly** and both were scored as leaks.

Question: *"What is the current penalty per business day for late delivery under
Section 5?"* The fixture uses real Word revision XML: `$5,000` inside `<w:del>`,
`$12,500` inside `<w:ins>`. Correct current answer: **$12,500**.

| | Answer | Scored |
|---|---|---|
| GA | "$12,500 USD per business day … the prior $5,000 amount was removed by tracked deletion" | 🚨 leak |
| The Agent | "$12,500 USD per business day … the earlier draft figure of $5,000 was struck by a tracked deletion" | 🚨 leak |

Both gave the right number *and* correctly explained the old one — a better
answer than the bare figure. The negative pattern at
**`tests_v2/competency/test_competency_agent_knowledge_word.py:216`** is too
narrow:

```python
[r"\$?\s*5[,.]?000(?!\s*USD\s+per\s+day\s+was\s+removed)"]
```

The negative lookahead only forgives the exact phrase "USD per day was removed".
Any other correct way of describing the deletion trips it.

### Fix

Rewrite the negative pattern so it fires only when `$5,000` is presented as the
**current** value — not when it is correctly identified as the removed or prior
one. A `not_present`-style construction (accept an explicit
deleted/prior/struck/removed/superseded qualifier near the figure) is the
straightforward form.

### Then restate both baselines

Re-run the GA Word suite after the regex change so the published baseline is
correct. Expected outcome:

| | Reported | Corrected |
|---|---:|---:|
| GA Word | 92.6% (25.0/27.0) | **100%** (27.0/27.0) |
| The Agent Word | 88.9% (24.0/27.0) | **96.3%** (26.0/27.0) |

The Excel `hidden_security` negative pattern was audited at the same time and is
**sound** — it matches the unique marker and the actual figures, and item 2's
leak is real. No change there.

---

## Verification

Environment: `%USERPROFILE%\miniconda3\envs\aihub2.1\python.exe`.
Services required: main app `:5001`, The Agent `:5111`.

```bash
python tests_v2/competency/run_the_agent_attachment_competency.py --suites pdf,word,excel --role 3
```

Single suite while iterating (PDF is the sensitive one, ~35 min):

```bash
python tests_v2/competency/run_the_agent_attachment_competency.py --suites pdf --role 3
```

The runner prints a per-turn lane trace and writes a **Tool usage (grounding
evidence)** table into each report — use it to confirm the lane mix moved, not
just the score.

### Targets

| Metric | Now | Target |
|---|---:|---:|
| PDF | 61.5% | **≥ 90%** |
| Word | 96.3%\* | ≥ 96% (no regression) |
| Excel | 92.3% | ≥ 92%, with hidden-sheet answers **labelled** |
| Attachment-opening lane chosen | 71% | **≥ 95%** |
| Mean seconds/question, PDF | 85.7 s | **≤ 40 s** |

\* corrected figure, after item 4.

### Two runs still unperformed — worth doing

1. **Nudge A/B** — re-run PDF with "read the attached files" appended to each
   question. Converts the ~94% ceiling from an estimate into a measurement and
   proves item 1 is prompt-shaped before any code changes. ~35 min, zero code.
2. **Role 1** — `--role 1` runs as a regular user on `claude-haiku-4-5`, which is
   what non-Developer users actually get. Completely untested today.

---

## Do not break

- **`run_turn` is off-limits.** The mutation-claim honesty guard lives inline in
  it; see `docs/the-agent-streaming-progress-handoff.md`.
- **Never place a helper function between an `@tool()` decorator and its
  function** — it silently unregisters the tool.
- **`brain.py` is not UI-editable.** Code edit, then restart the service — dev:
  its own console window; installed: `nssm restart AIHubTheAgent`.
- **Do not overwrite the GA baselines.** GA reports are
  `{pdf,word,excel}_competency_report.*`; The Agent's carry the `the_agent_`
  prefix. The runner keeps them separate by design.
- **Do not edit the imported batteries to make a score move.** The only sanctioned
  battery change is item 4's negative pattern, and it must be justified by the
  false positive documented above.

---

## Caveats on the evidence

Single run, no repeats. `claude-sonnet-5`, which is the dev default — the shipped
`dist/.env` pins `claude-opus-5`, so client behavior may differ. Role 3 only.
Fixtures are generated, not real customer documents, and contain no scanned/OCR
PDFs.

Related: `docs/chat-upload-admit-or-deny-plan.md` (the P3 conversation-budget item
remains open and is **not** covered here), `docs/code-interpreter-unification-plan.md`.
