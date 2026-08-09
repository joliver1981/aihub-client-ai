---
name: aihub-document-ingestion
description: Use when a user wants to import/ingest documents (PDFs, scans,
  Office files) into AI Hub so the platform can search and answer questions
  about them — a one-time bulk import or a standing folder-watch pipeline.
---

# Ingesting documents into AI Hub

AI Hub reads documents (AI vision + OCR for scans), stores their text, and
makes them searchable. You don't have a direct ingest tool — you ingest by
**building an automation** (deterministic, governed, schedulable), which is
exactly right for a repeatable pipeline.

## The two endpoints (call from automation code with `import requests`)

Automations run on the platform host, so loopback URLs work.

- **Process a document** (extract text, no auth — loopback doc API):
  `POST http://127.0.0.1:<HOST_PORT+10>/document/process`
  form: `filePath=<server path>`, `detect_document_type=true`,
  `force_ai_extraction=false` (set true for scanned/handwritten).
  Returns `{status, document_id, page_count, total_chars, document_text}`.
- **Ingest AND attach to a searchable agent** (so Q&A works):
  `POST http://127.0.0.1:<HOST_PORT>/add/agent_knowledge`
  multipart: `file=@<path>`, `agent_id=<knowledge agent id>`,
  `description=…`, `user_id=<uid>`. Auth: header `X-API-Key`. Store the
  platform key once as a secret (`store_platform_secret AI_HUB_API_KEY`) and
  read it in code with `aihub.secret("AI_HUB_API_KEY")`. Indexing is async —
  search can lag a couple minutes for large docs.

To answer questions about ingested docs, the docs must be attached to an
agent; then you query it with `ask_data_agent` (that agent's id).

## Folder-watch ingest pipeline (the standing process)

Automation `main.py` shape — plain stdlib for files, `requests` for ingest:

    import os, glob, shutil, requests
    import aihub_runtime as aihub
    IN  = aihub.input("input_dir")
    ARC = aihub.input("archive_dir")
    KEY = aihub.secret("AI_HUB_API_KEY")
    AID = aihub.input("agent_id")
    new = 0
    for path in glob.glob(os.path.join(IN, "*.pdf")):
        with open(path, "rb") as f:
            r = requests.post("http://127.0.0.1:5001/add/agent_knowledge",
                files={"file": (os.path.basename(path), f)},
                data={"agent_id": AID, "description": "auto-ingested",
                      "user_id": aihub.input("user_id")},
                headers={"X-API-Key": KEY}, timeout=600)
        if r.ok:
            shutil.move(path, os.path.join(ARC, os.path.basename(path)))
            aihub.log(f"ingested + archived {os.path.basename(path)}")
            new += 1
    print(f"{new} new document(s) ingested")

Declare `requests` in the manifest packages and `AI_HUB_API_KEY` in secrets.
Dry-run, promote, then **schedule** it (e.g. every 15 minutes) so it keeps
watching the folder. Archiving the source is what stops re-ingesting.

## Rules

- Probe/confirm the folder paths and the target agent id before building —
  never guess. Offer to create a knowledge agent if none fits.
- Report ingest results honestly: files processed, attached, archived. If a
  file fails extraction, leave it in the input folder and say so — don't
  archive an un-ingested file.
- Large PDF? Preflight with `POST /api/document/preflight` (multipart file)
  to get page_count before committing.
