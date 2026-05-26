# Document Processor — Summarization (`/document_summarizer`)

Generate, view, and manage AI-produced summaries of documents already loaded into AI Hub. The page is for **document-level summarization** — picking a document, choosing one or more summary types, and producing or refreshing those summaries.

> The URL is `/document_summarizer` but the page title is "Document Processor — Generate and manage document summaries". They refer to the same feature.

---

## Page Layout

### Left sidebar — Summarization Tools
- **Select Document** — dropdown of documents in the knowledge base.
- **Summary Types** — pick one or more types to generate:
  - **Standard** — balanced general-purpose summary.
  - **Brief** — short, high-level overview.
  - **Detailed** — long-form, comprehensive.
  - **Bullet Points** — structured key-takeaways list.
  - **Executive** — executive-summary tone (decisions, outcomes, recommendations).
- **Custom Instructions** — optional free-text prompt to focus the AI (e.g., "Focus on financial information, dates, and key decisions").
- **Overwrite existing summaries** — checkbox; off by default to avoid blowing away prior runs.
- **Action buttons:**
  - **Generate Summaries** — produce summaries for the selected document with the chosen types and instructions.
  - **Refresh View** — reload the displayed summaries (after generation completes or if you want to re-check).
  - **Export Summaries** — download the current document's summaries.
- **Batch Operations:**
  - **Process All Unsummarized** — generate summaries for every document that doesn't already have them.
  - **Clear All Summaries** — delete summaries (destructive; confirm before doing this).

### Main pane (right) — Summary Display
- **Document Info card** — type, page count, processed timestamp, summary count.
- **Summary Statistics** — overall counts and a progress bar showing summarization coverage.
- **Summary cards** — one per generated summary, with type badge, body text, key points, and recognized entities.

## Common Tasks

### "Summarize a single document"
1. Pick the document from the left dropdown.
2. Tick the summary types you want (Standard is on by default).
3. Optionally add custom instructions to bias what the AI focuses on.
4. Click **Generate Summaries**. Wait — large documents may take minutes.
5. Results appear as summary cards in the main pane.

### "Summarize every document that doesn't have a summary yet"
Click **Process All Unsummarized** (under Batch Operations). This kicks off background work; refresh periodically to see progress.

### "I want to redo a summary with different instructions"
Tick **Overwrite existing summaries** before clicking **Generate Summaries**. Otherwise the existing summary is preserved.

### "I want the summary as a file"
Click **Export Summaries** with the document selected.

### "Wipe all summaries and start fresh"
Click **Clear All Summaries** under Batch Operations. **Destructive** — there's no undo, and you'll have to regenerate everything.

## Related Pages

- **`/document_manager`** — manage the documents themselves (upload, delete, metadata).
- **`/document_search`** — search across documents (uses both raw text and summaries).
- **`/document_scheduler`** — schedule recurring document-processor jobs (including bulk summarization runs).

## What This Page Doesn't Do

- It doesn't upload or delete documents — that's `/document_manager`.
- It doesn't schedule summarization runs — for recurring summarization, set up a job in the Document Processor and schedule it from `/document_scheduler`.
- It doesn't summarize chat conversations or workflows — only documents in the knowledge base.
