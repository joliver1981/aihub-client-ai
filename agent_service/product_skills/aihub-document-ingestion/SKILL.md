---
name: aihub-document-ingestion
description: Use when a user wants to import/ingest documents (PDFs, scans,
  Office files) into AI Hub so the platform can search and answer questions
  about them — a one-time bulk import, or a standing folder-watch pipeline.
---

# Ingesting documents into AI Hub

AI Hub reads documents (AI vision + OCR for scans), stores their text, and
makes them searchable across the whole document store. For anything a user
asks in the moment — "import this folder", "what do these invoices say?" —
use the document TOOLS directly. Only a *standing, scheduled* ingest needs an
automation.

## One-time import + Q&A — use the tools (no automation, no API key)

You have first-class document tools. Reach for them; do not hand-build a probe
automation or talk about API keys — that's plumbing the tools already handle.

1. **`list_server_files(path)`** — confirm what's actually in the folder the
   user named (names, sizes, types). You DO have server filesystem access
   through this tool; never claim you can't see files.
2. **`import_documents(path)`** — point it at the folder (or a single file). It
   extracts, stores, and indexes each supported document. It is **idempotent**:
   a file already imported from the same path is skipped, so re-running never
   creates duplicates. Report its per-file result honestly (imported /
   already-present / failed). Pass `force_ai_extraction=true` for scanned or
   handwritten pages; `recursive=true` to include subfolders.
3. **`search_documents(question)`** — answer questions against the whole store
   (semantic + field search). **No knowledge agent is required** — search hits
   the document store directly and returns passages with filename + page (and
   any extracted fields like totals/dates) to cite. If it returns nothing, say
   so and offer to import.
4. **`list_documents` / `get_document`** — see what's in the store / verify an
   import landed.

Supported types: PDF, Word, Excel, CSV, TXT/Markdown/JSON/XML/HTML, and common
images (jpg/png/gif/webp).

## Just READING one file (not importing it)

When the user wants you to **look at one specific file** — a file they attached
in chat, a file you just downloaded, or a path they gave — call
**`read_file(path)`**, NOT `import_documents`. It returns the text of any common
type (TXT/CSV/JSON/Markdown/code and PDF/Word/Excel/images) without storing or
indexing it: the fast, one-off "what's in this file?" path. Text/code/config are
read directly (instant); documents are extracted natively (pass `ocr=true` only
for a scanned PDF or a photo of text). It accepts a server path, an `/api/files`
link you delivered, or a chat-attachment id. Reserve `import_documents` for when
the user wants files to be **searchable later** across the whole store.

## Standing folder-watch pipeline — this is where an automation belongs

When the user wants *ongoing* ingestion ("import new files from this folder
going forward, archive the originals"), build an AUTOMATION and schedule it —
deterministic, governed, zero tokens per run. Automations run on the platform
host, so the loopback document API is reachable with no auth:

    import os, glob, shutil, requests
    import aihub_runtime as aihub

    IN  = aihub.input("input_dir")
    ARC = aihub.input("archive_dir")
    # Document API = main port + 10 (e.g. 5001 -> 5011). No API key needed.
    DOC = "http://127.0.0.1:5011/document/process"

    os.makedirs(ARC, exist_ok=True)
    new = 0
    for path in glob.glob(os.path.join(IN, "*.pdf")):
        r = requests.post(DOC, data={
            "filePath": path,
            "detect_document_type": "true",
            "force_ai_extraction": "false",
        }, timeout=600)
        if r.ok and r.json().get("status") == "success":
            shutil.move(path, os.path.join(ARC, os.path.basename(path)))
            aihub.log(f"ingested + archived {os.path.basename(path)}")
            new += 1
        else:
            aihub.log(f"left in place (extract failed): {os.path.basename(path)}")
    print(f"{new} new document(s) ingested")

Declare `requests` in the manifest packages. Dry-run, promote, then **schedule**
(e.g. every 15 minutes). **Archiving the processed source is what prevents
re-ingesting it** — the doc API always inserts, so a file left in the input
folder would be imported again on the next run. Widen the glob (`*.pdf`,
`*.docx`, …) to whatever the user drops in.

## Rules

- Confirm the folder path first (`list_server_files`) — never guess.
- Report ingest results honestly: imported, skipped-as-duplicate, failed. If a
  file fails extraction, leave it in place and say so — don't claim it landed.
- For a one-time job, `import_documents` already dedupes; you don't need to
  build anything.
