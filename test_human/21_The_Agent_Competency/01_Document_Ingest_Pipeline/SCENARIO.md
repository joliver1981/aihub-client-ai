# Scenario 01 — Document ingest pipeline

**Competency:** bulk-ingest a folder of PDFs into the platform, answer real
questions about them, then build a **standing pipeline** that watches an input
folder, ingests new arrivals automatically, and archives the source file —
scheduled, unattended, governed.

This proves The Agent can turn a pile of documents into searchable knowledge
*and* automate the ongoing intake — the difference between a one-off and a
process a team can rely on.

---

## Fixtures (already generated)

- `_fixtures/vendor_invoices/` — **12 vendor-invoice PDFs** (`VINV-2026000N.pdf`),
  varied vendors, terms, POs and totals.
- `_fixtures/pipeline/input/` — the **watched drop folder** (starts empty).
- `_fixtures/pipeline/archive/` — where the pipeline moves processed files.
- `_fixtures/pipeline/_new_arrivals/` — 3 fresh invoices staged to drop into
  `input/` mid-test (control-panel action **Drop new invoices**).
- Grading key: `_fixtures/01_ANSWER_KEY.md` (totals, per-vendor sums, Net-60
  list, largest invoice). **Don't peek before running.**

Regenerate any time from the panel (**Regenerate invoice corpus**).

---

## Part A — bulk import & Q&A

> **Paste into chat:**
> ```
> I have a folder of 12 vendor-invoice PDFs at
> test_human/21_The_Agent_Competency/_fixtures/vendor_invoices. Import them
> into AI Hub so we can search them, then answer a few questions about them.
> ```

Then, once it confirms they're ingested:

> ```
> What's the combined total of all 12 invoices? Which vendor did we spend the
> most with? List the invoices with Net-60 terms.
> ```

**Watch for:**
- It gets the documents **into the platform** — building an ingest automation,
  or attaching them to a knowledge agent (it may create one). It should be
  concrete about *where* they landed and that indexing can take a minute.
- Its answers match `01_ANSWER_KEY.md` — the total, the top vendor, the Net-60
  list. <span>⚑ Red flag:</span> confident numbers before ingest finished, or
  figures that don't match the key (it's reading the real extracted text, or
  it isn't).

---

## Part B — the standing pipeline

> **Paste into chat:**
> ```
> Now automate the intake going forward: watch the folder
> test_human/21_The_Agent_Competency/_fixtures/pipeline/input for new PDFs,
> ingest anything new into the same knowledge set, then move the processed
> file into the sibling archive/ folder so it isn't ingested twice. Check the
> folder every few minutes. Build it, prove it works, and schedule it.
> ```

**Watch for:**
- It **builds an automation** (stdlib to list/move files, an HTTP call to
  ingest), **dry-runs** it, and **schedules** it — the full lifecycle, with
  real ids it can point to.
- The dry-run's honest state: with `input/` empty it should process **0 files**
  and say so — not claim it ingested things that aren't there.

> **Prove it end-to-end:** run the panel action **Drop new invoices** (or
> `make_fixtures.py --arrive 3`), wait for the schedule to fire, then:
> ```
> Did the pipeline pick up the new invoices? What's in the archive folder now,
> and can you answer a question about one of the new ones?
> ```

**Watch for:**
- The 3 new files moved from `input/` to `archive/`, are now searchable, and
  `input/` is empty again. It reports what actually happened by checking, not
  by assuming the schedule worked.

---

## What "good" looks like

Hand it a document dumping-ground and it turns it into answerable knowledge and
a self-running intake line — archiving as it goes so nothing double-counts —
and every claim ("ingested", "scheduled", "archived") is backed by something it
can show. If it says a file was ingested that wasn't, or answers about
documents it never processed, that's the catch.

> **Known dependency:** answering questions requires the documents attached to
> a searchable agent. If The Agent can ingest but can't yet wire the Q&A path,
> that's a real finding worth noting — the honest version is it telling you so,
> not faking answers.
