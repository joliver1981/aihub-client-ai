"""
Document tools (James, 2026-08-10): documents are one of AI Hub's five nouns,
but The Agent shipped with none — the model had to hand-roll a probe automation,
port-scan loopback, and juggle an API key just to import a folder of PDFs. These
four tools give it a first-class surface, all thin wrappers over EXISTING
platform endpoints (same brain/body doctrine as platform_tools):

- list_server_files : read-only directory listing on the host (kills the
  "I have no filesystem tool" complaint; strictly weaker than what automations
  can already do, so no new blast radius — just convenience + honesty).
- import_documents  : POST the doc API's /document/process for each file
  (extracts text, stores the doc, indexes it for search). IDEMPOTENT — skips a
  file already imported from the same server path, so a re-run never duplicates
  (the duplicate-row mess that motivated this).
- search_documents  : POST /api/internal/document-search (the CC-consumed
  whole-store semantic+field search). No knowledge agent required — the old
  skill wrongly implied one was.
- list_documents / get_document : wrap /api/documents (GET) for read-back and
  "what's in the system?".

Auth: AI_HUB_API_KEY resolves to the machine-derived internal key on this host
(env unset -> get_internal_api_key()), which satisfies both api_key_or_session
and internal_api_key endpoints — exactly how the existing automations-internal
call authenticates. We also send X-Internal-API-Key explicitly on the strict
internal endpoint so it keeps working even if someone pins AI_HUB_API_KEY to a
tenant key later.

Kill switch: brain.py includes DOCUMENT_TOOLS only when AGENT_DOCUMENT_TOOLS is
true (default true) — flip to false to revert to pre-tool behavior.
"""

import fnmatch
import os
from datetime import datetime
from typing import Any

import httpx

from claude_agent_sdk import tool

from agent_config import (
    APP_ROOT, AI_HUB_API_KEY, get_base_url, get_internal_api_key, logger,
)
from platform_tools import CURRENT_USER, _text, _unwrap

# Doc API lives at HOST_PORT+10 (matches CommonUtils.get_document_api_base_url
# and wsgi_doc_api.py's bind). Import (/document/process) is the only tool that
# talks to it; everything else talks to the main app.
_ALLOWED_EXTS = {
    "pdf", "docx", "doc", "txt", "csv", "xls", "xlsx",
    "jpg", "jpeg", "png", "bmp", "gif", "tiff", "tif",
}

# list_server_files won't enumerate these — the platform's own secret store and
# OS system dirs. Defense-in-depth / hygiene, not a hard boundary (automations
# can read anything); it just keeps the agent from casually walking them.
_FORBIDDEN_DIRS = [
    os.path.normcase(os.path.join(APP_ROOT, "data", "secrets")),
    os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows")),
]

# Importing many files in one call: cap the batch so a single tool call stays
# bounded. Because import is idempotent, calling again simply continues with the
# not-yet-imported files.
_IMPORT_BATCH_CAP = int(os.getenv("AGENT_IMPORT_BATCH_CAP", "60"))


def _doc_api_base() -> str:
    protocol = os.getenv("PROTOCOL", "http")
    host = os.getenv("INTERNAL_HOST", "127.0.0.1")
    try:
        port = int(os.getenv("HOST_PORT", "5001")) + 10
    except ValueError:
        port = 5011
    return f"{protocol}://{host}:{port}"


def _headers(internal: bool = False) -> dict:
    h = {"X-API-Key": AI_HUB_API_KEY, "Connection": "close"}
    if internal:
        # strict internal endpoints only accept the machine-derived key
        h["X-Internal-API-Key"] = get_internal_api_key()
    # Caller identity (v3 category ACL): mint a short-lived assertion when this
    # request runs on behalf of a real signed-in user. Absent identity keeps
    # today's unrestricted posture server-side, so this is additive.
    try:
        user = CURRENT_USER.get() or {}
        uid = user.get("user_id")
        # The contextvar default is the service principal (user_id=0) — that is
        # NOT a user identity and must not mint an assertion.
        if uid not in (None, "", 0):
            import shared_auth
            h["X-AIHub-User"] = shared_auth.sign_user_assertion(
                uid, (user or {}).get("tenant_id"), (user or {}).get("role"))
    except Exception:
        pass   # identity is an enhancement; a doc call must never fail over it
    return h


async def _get(path: str, params: dict | None = None):
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=60.0)) as client:
        r = await client.get(f"{get_base_url()}{path}", params=params or {},
                             headers=_headers())
        try:
            return _unwrap(r.json()), r.status_code
        except Exception:
            return {"error": (r.text or "")[:500]}, r.status_code


async def _post_main(path: str, body: dict, internal: bool = False,
                     read_timeout: float = 120.0):
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=read_timeout)) as client:
        r = await client.post(f"{get_base_url()}{path}", json=body,
                             headers=_headers(internal=internal))
        try:
            return _unwrap(r.json()), r.status_code
        except Exception:
            return {"error": (r.text or "")[:500]}, r.status_code


# The document stack serializes concurrent work (4-16 waitress threads across
# main app / doc API / vector API — see wsgi*.py); under a burst of imports or
# a running records extraction, calls queue past our client read timeouts. A
# raw ReadTimeout traceback read to the model like an outage and sent it into
# retry storms (2026-08-21). Say what it actually is: a busy queue.
_BUSY_MSG = ("The document stack is BUSY right now — another import or "
             "extraction is holding it. This is a queue, not an outage, and "
             "not evidence the document is missing. Wait about a minute and "
             "call this once more; if it is still busy, tell the user the "
             "document system is working through a backlog rather than "
             "retrying in a loop.")


def _ext_ok(name: str) -> bool:
    return "." in name and name.rsplit(".", 1)[1].lower() in _ALLOWED_EXTS


def _fmt_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.1f} {unit}" if unit != "B" else f"{int(f)} B"
        f /= 1024.0
    return f"{f} B"


def _mtime(path: str) -> str:
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return "?"


# ---------------------------------------------------------------------------
# list_server_files
# ---------------------------------------------------------------------------

@tool(
    "list_server_files",
    "List the files and folders in a directory ON THE AI HUB SERVER so you can "
    "confirm what's actually there before importing. Read-only: returns names, "
    "sizes and modified dates only — never file contents. Use this the moment a "
    "user tells you where their files live (a folder path); then point "
    "import_documents at the same path. You DO have server filesystem access "
    "through this tool — don't tell users you can't see files.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string",
                     "description": "Absolute directory (or file) path on the "
                                    "server, e.g. C:\\\\data\\\\invoices"},
            "pattern": {"type": "string",
                        "description": "Optional glob to filter names, e.g. *.pdf"},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
)
async def list_server_files(args: dict[str, Any]) -> dict[str, Any]:
    raw = str(args.get("path", "")).strip().strip('"')
    if not raw:
        return _text("Give me a directory path to list.", is_error=True)
    path = os.path.abspath(raw)
    ncase = os.path.normcase(path)
    for bad in _FORBIDDEN_DIRS:
        if ncase == bad or ncase.startswith(bad + os.sep):
            return _text(f"Refused: {raw} is a protected system/secret location "
                         "and won't be listed.", is_error=True)

    if os.path.isfile(path):
        size = os.path.getsize(path)
        ok = "supported for import" if _ext_ok(path) else "NOT a supported import type"
        return _text(f"{path}\n  1 file · {_fmt_size(size)} · modified "
                     f"{_mtime(path)} · {ok}")
    if not os.path.isdir(path):
        return _text(f"No such directory on the server: {raw}", is_error=True)

    pattern = str(args.get("pattern") or "").strip()
    try:
        names = sorted(os.listdir(path))
    except OSError as e:
        return _text(f"Could not read {raw}: {e}", is_error=True)

    dirs, files, importable = [], [], 0
    CAP = 500
    for name in names:
        full = os.path.join(path, name)
        if os.path.isdir(full):
            if not pattern or fnmatch.fnmatch(name, pattern):
                dirs.append(name)
        else:
            if pattern and not fnmatch.fnmatch(name, pattern):
                continue
            files.append((name, os.path.getsize(full) if os.path.exists(full) else 0,
                          _mtime(full), _ext_ok(name)))
            if _ext_ok(name):
                importable += 1

    lines = [f"{path}",
             f"  {len(dirs)} folder(s), {len(files)} file(s)"
             + (f" matching {pattern}" if pattern else "")
             + f" · {importable} importable document(s)"]
    for d in dirs[:CAP]:
        lines.append(f"  [dir]  {d}")
    for name, size, mt, ok in files[:CAP]:
        flag = "" if ok else "  (unsupported)"
        lines.append(f"  {name}  ·  {_fmt_size(size)}  ·  {mt}{flag}")
    shown = len(dirs[:CAP]) + len(files[:CAP])
    if len(dirs) + len(files) > shown:
        lines.append(f"  … {len(dirs) + len(files) - shown} more not shown")
    if importable:
        lines.append(f"\nTo bring these into AI Hub for search, call "
                     f"import_documents with path={path!r}.")
    return _text("\n".join(lines))


# ---------------------------------------------------------------------------
# import_documents
# ---------------------------------------------------------------------------

async def _existing_paths_for(basename: str) -> set:
    """Server paths already in the store for docs whose filename matches basename
    (used to skip re-imports). Returns a set of normcased original_paths."""
    data, status = await _get("/api/documents",
                              {"search": basename, "per_page": 200})
    out = set()
    if status == 200 and isinstance(data, dict):
        for d in (data.get("documents") or []):
            op = d.get("original_path")
            if op:
                out.add(os.path.normcase(os.path.abspath(str(op))))
    return out


@tool(
    "import_documents",
    "Import one or more documents from the server INTO the AI Hub document "
    "system so they become searchable (answerable with search_documents). Pass "
    "a FOLDER path to import every supported document in it, or a single FILE "
    "path. It extracts text, stores each document, and indexes it. It is "
    "IDEMPOTENT: a file already imported from the same path is skipped, so "
    "re-running is safe and never creates duplicates (pass force=true to import "
    "anyway). Supported: PDF, Word, Excel, CSV, TXT, and common images. Report "
    "the per-file outcome it returns (imported / already-present / failed) — "
    "don't claim success for files it didn't import.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string",
                     "description": "Absolute folder or file path on the server"},
            "recursive": {"type": "boolean",
                          "description": "Also import supported files in "
                                         "subfolders (default false)"},
            "force": {"type": "boolean",
                      "description": "Re-import even if the same path is already "
                                     "in the store (default false)"},
            "force_ai_extraction": {"type": "boolean",
                                    "description": "Use AI vision/OCR — set true "
                                                   "for scanned or handwritten "
                                                   "docs (default false)"},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
)
async def import_documents(args: dict[str, Any]) -> dict[str, Any]:
    raw = str(args.get("path", "")).strip().strip('"')
    if not raw:
        return _text("Give me a folder or file path to import.", is_error=True)
    path = os.path.abspath(raw)
    if not os.path.exists(path):
        # After delivering a download, the model often holds only the
        # user-facing /api/files/ link (portal downloads are staged inside the
        # tool body, so no server path ever entered the transcript). Resolve
        # the link to THIS user's staged copy instead of failing into a
        # filesystem hunt (James's Alpaca-statement repro, 2026-08-21).
        try:
            from file_tools import resolve_api_files_ref
            uid = int((CURRENT_USER.get() or {}).get("user_id") or 0)
            staged, _display = resolve_api_files_ref(raw, uid)
        except Exception:
            staged = None
        if staged:
            path = staged
        elif "/api/files/" in raw:
            return _text("That /api/files/ link doesn't match any download "
                         "staged for this user (wrong owner, or it was cleaned "
                         "up). Re-run the download, or use the 'Server copies' "
                         "path from the tool result that delivered it.",
                         is_error=True)
    recursive = bool(args.get("recursive"))
    force = bool(args.get("force"))
    force_ai = bool(args.get("force_ai_extraction"))

    # Resolve the candidate file list.
    candidates = []
    if os.path.isfile(path):
        if not _ext_ok(path):
            return _text(f"{os.path.basename(path)} isn't a supported type. "
                         f"Supported: {', '.join(sorted(_ALLOWED_EXTS))}.",
                         is_error=True)
        candidates = [path]
    elif os.path.isdir(path):
        if recursive:
            for root, _dirs, files in os.walk(path):
                for name in files:
                    if _ext_ok(name):
                        candidates.append(os.path.join(root, name))
        else:
            for name in sorted(os.listdir(path)):
                full = os.path.join(path, name)
                if os.path.isfile(full) and _ext_ok(name):
                    candidates.append(full)
        candidates.sort()
    else:
        return _text(f"No such file or folder on the server: {raw}", is_error=True)

    if not candidates:
        return _text(f"No supported documents found in {raw}. Supported types: "
                     f"{', '.join(sorted(_ALLOWED_EXTS))}.", is_error=True)

    # Dedupe against what's already stored (unless force).
    already = set()
    if not force:
        seen_basenames = set()
        for f in candidates:
            b = os.path.basename(f)
            if b not in seen_basenames:
                seen_basenames.add(b)
                already |= await _existing_paths_for(b)

    to_import, skipped = [], []
    for f in candidates:
        if not force and os.path.normcase(os.path.abspath(f)) in already:
            skipped.append(os.path.basename(f))
        else:
            to_import.append(f)

    capped = to_import[:_IMPORT_BATCH_CAP]
    doc_url = f"{_doc_api_base()}/document/process"
    imported, failed = [], []

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0)) as client:
        for f in capped:
            name = os.path.basename(f)
            try:
                r = await client.post(doc_url, data={
                    "filePath": f,
                    "detect_document_type": "true",
                    "force_ai_extraction": "true" if force_ai else "false",
                })
            except httpx.TimeoutException:
                # Observed 2026-08-21: the server often FINISHES these late —
                # the doc lands despite our timeout. Re-import is idempotent
                # by path, so verifying beats blind retry.
                failed.append((name, "TIMEOUT waiting for the document stack "
                                     "(busy queue) — the import may still "
                                     "complete on the server. Check "
                                     "list_documents in a minute before "
                                     "re-importing; re-import is safe "
                                     "(idempotent by path)."))
                continue
            except Exception as e:
                failed.append((name, f"{type(e).__name__}: {e}"))
                continue
            if r.status_code != 200:
                failed.append((name, f"HTTP {r.status_code}"))
                continue
            try:
                j = _unwrap(r.json())
            except Exception:
                failed.append((name, "non-JSON response"))
                continue
            if isinstance(j, dict) and j.get("status") == "success":
                imported.append((name, j.get("document_id"), j.get("page_count")))
            else:
                msg = (j.get("message") if isinstance(j, dict) else str(j)) or "unknown"
                failed.append((name, str(msg)[:160]))

    logger.info(f"import_documents user={CURRENT_USER.get().get('username')} "
                f"path={path} imported={len(imported)} skipped={len(skipped)} "
                f"failed={len(failed)}")

    # Compact, honest report — leads with the count, lists outcomes.
    lines = [f"Imported {len(imported)} of {len(candidates)} document(s) from {path}."]
    if imported:
        lines.append("Imported (now searchable):")
        for name, did, pages in imported:
            lines.append(f"  ✓ {name}  ({pages} page(s), id {did})")
    if skipped:
        lines.append(f"Already in the store, skipped {len(skipped)} "
                     f"(pass force=true to re-import): "
                     + ", ".join(skipped[:20])
                     + (" …" if len(skipped) > 20 else ""))
    if len(to_import) > len(capped):
        lines.append(f"Batch cap {_IMPORT_BATCH_CAP}: {len(to_import) - len(capped)} "
                     f"file(s) not yet imported — call import_documents again on the "
                     f"same path to continue (already-imported files are skipped).")
    if failed:
        lines.append(f"Failed {len(failed)} (left un-imported):")
        for name, why in failed[:20]:
            lines.append(f"  ✗ {name}: {why}")
    if imported:
        lines.append("\nAsk questions with search_documents; verify with "
                     "list_documents.")
    return _text("\n".join(lines), is_error=bool(failed and not imported))


# ---------------------------------------------------------------------------
# search_documents
# ---------------------------------------------------------------------------

def _pick(row: dict, *keys, default=None):
    low = {str(k).lower(): v for k, v in row.items()}
    for k in keys:
        v = low.get(k)
        if v not in (None, ""):
            return v
    return default


@tool(
    "search_documents",
    "Search the AI Hub document library and get the most relevant passages to "
    "answer a question about imported documents. It searches the WHOLE store "
    "(semantic + field search) — you do NOT need a knowledge agent or an API "
    "key. Use it to answer any question about documents that were imported. "
    "Returns matching passages with their source filename and page so you can "
    "cite them. If it returns nothing, say so honestly and suggest importing "
    "the documents first.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "The natural-language question to search for"},
            "max_results": {"type": "integer",
                            "description": "Max passages to return (default 12)"},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)
async def search_documents(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    if not query:
        return _text("Give me a question to search the documents for.",
                     is_error=True)
    try:
        limit = int(args.get("max_results") or 12)
    except (TypeError, ValueError):
        limit = 12
    limit = max(1, min(limit, 50))

    # The UNIFIED endpoint (additive) normalizes the engine's variable return
    # (JSON dict for field/hybrid, [Source …] text blob for semantic) into one
    # stable schema server-side, so we consume a consistent shape here.
    try:
        data, status = await _post_main("/api/internal/document-search-unified",
                                        {"question": query}, internal=True,
                                        read_timeout=180.0)
    except httpx.TimeoutException:
        return _text(_BUSY_MSG, is_error=True)
    if status != 200:
        msg = data.get("message") or data.get("error") if isinstance(data, dict) else data
        return _text(f"Document search failed (HTTP {status}): {msg}", is_error=True)

    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        return _text(f"Document search returned an unexpected response: "
                     f"{str(data)[:300]}", is_error=True)
    if result.get("error"):
        return _text(f"Document search error: {result['error']}", is_error=True)

    rows = result.get("passages") or []
    qa = result.get("query_analysis") or {}
    if not rows:
        # The engine may synthesize a text answer with no structured passages,
        # or genuinely find nothing — surface either honestly.
        answer = (result.get("answer") or result.get("text") or "").strip()
        if answer:
            return _text(f"Results for \"{query}\":\n{answer[:4000]}")
        return _text(f"No documents matched \"{query}\". If you expected hits, "
                     "the documents may not be imported yet — check with "
                     "list_documents or import them with import_documents.")

    # Dedupe near-identical passages by (filename, page); keep first (ranked).
    seen, out = set(), []
    for row in rows:
        if not isinstance(row, dict):
            continue
        fname = _pick(row, "filename", "file_name", "document_name", "source",
                      default="(unknown file)")
        page = _pick(row, "page_number", "page", "page_no")
        key = (str(fname), str(page))
        if key in seen:
            continue
        seen.add(key)
        text = _pick(row, "snippet", "matched_text", "highlight", "full_text",
                     "page_text", "text", "content", "chunk", default="")
        did = _pick(row, "document_id", "doc_id")
        # Passages are FULL PAGES now (2026-08-14), not 512-char chunks — the old
        # [:800] squeeze silently undid that upgrade on this surface. 4,000 chars
        # keeps a real lease page (1,500-3,200 chars) intact; 12 passages ≈ 12K
        # tokens, well within an agent turn.
        passage_cap = int(os.getenv("AGENT_DOC_PASSAGE_CHARS", "4000"))
        snippet = " ".join(str(text).split())[:passage_cap]
        loc = f" p.{page}" if page not in (None, "") else ""
        line = (f"• {fname}{loc}"
                + (f"  [id {did}]" if did else ""))
        # structured fields the extractor pulled (total_due, invoice_number …)
        rf = row.get("fields") or row.get("relevant_fields")
        if isinstance(rf, dict) and rf:
            kv = ", ".join(f"{k}={v}" for k, v in list(rf.items())[:8]
                           if v not in (None, ""))
            if kv:
                line += f"\n    fields: {kv}"
        if snippet:
            line += f"\n    {snippet}"
        out.append(line)
        if len(out) >= limit:
            break

    header = f"{len(out)} passage(s) for \"{query}\""
    conf = qa.get("confidence")
    if conf and conf != "unknown":
        header += f" · search confidence: {conf}"
    body = header + ":\n" + "\n".join(out)
    # Discovery bridge: the server flags when these documents carry structured
    # record rows — the right tool for which/how-many questions.
    hint = result.get("records_hint")
    if hint:
        body += f"\n\n{hint}"
    return _text(body)


# ---------------------------------------------------------------------------
# list_documents / get_document
# ---------------------------------------------------------------------------

@tool(
    "list_documents",
    "List documents currently in the AI Hub document store, most recent first — "
    "optionally filtered by a filename search or document type. Read-only. Use "
    "it to see what's been imported or to verify an import landed.",
    {
        "type": "object",
        "properties": {
            "search": {"type": "string",
                       "description": "Filter by filename substring"},
            "document_type": {"type": "string",
                              "description": "Filter by document type "
                                             "(e.g. vendor_invoice)"},
            "limit": {"type": "integer",
                      "description": "Max rows to return (default 25)"},
        },
        "additionalProperties": False,
    },
)
async def list_documents(args: dict[str, Any]) -> dict[str, Any]:
    try:
        limit = int(args.get("limit") or 25)
    except (TypeError, ValueError):
        limit = 25
    limit = max(1, min(limit, 200))
    params = {"page": 1, "per_page": limit}
    if str(args.get("search") or "").strip():
        params["search"] = str(args["search"]).strip()
    if str(args.get("document_type") or "").strip():
        params["document_type"] = str(args["document_type"]).strip()

    data, status = await _get("/api/documents", params)
    if status != 200 or not isinstance(data, dict):
        msg = data.get("error") if isinstance(data, dict) else data
        return _text(f"Could not list documents (HTTP {status}): {msg}",
                     is_error=True)

    docs = data.get("documents") or []
    stats = data.get("stats") or {}
    total = (data.get("pagination") or {}).get("total_count", len(docs))
    if not docs:
        return _text("No documents in the store match that. "
                     f"(Store total: {stats.get('total_documents', 0)} document(s).)")
    lines = [f"{len(docs)} of {total} matching document(s) "
             f"(store holds {stats.get('total_documents', 0)}):"]
    for d in docs:
        when = (d.get("processed_at") or "")[:16].replace("T", " ")
        lines.append(f"  {d.get('filename')}  ·  {d.get('document_type') or '?'}  "
                     f"·  {d.get('page_count') or '?'}p  ·  {when}  "
                     f"·  id {d.get('document_id')}")
    return _text("\n".join(lines))


@tool(
    "get_document",
    "Get the stored metadata for one document by its document_id (filename, "
    "type, page count, dates, source path). Read-only.",
    {
        "type": "object",
        "properties": {
            "document_id": {"type": "string",
                            "description": "The document_id (as returned by "
                                           "import_documents or list_documents)"},
        },
        "required": ["document_id"],
        "additionalProperties": False,
    },
)
async def get_document(args: dict[str, Any]) -> dict[str, Any]:
    did = str(args.get("document_id", "")).strip()
    if not did:
        return _text("Give me a document_id.", is_error=True)
    data, status = await _get(f"/api/documents/{did}")
    if status != 200 or not isinstance(data, dict) or data.get("error") \
            or not data.get("filename"):
        return _text(f"No document with id {did} is in the store. Use "
                     "list_documents to see valid ids.", is_error=True)
    fields = [
        ("filename", data.get("filename")),
        ("document_type", data.get("document_type")),
        ("pages", data.get("page_count")),
        ("reference_number", data.get("reference_number")),
        ("document_date", data.get("document_date")),
        ("processed_at", data.get("processed_at")),
        ("source_path", data.get("original_path")),
        ("archived_path", data.get("archived_path")),
    ]
    body = "\n".join(f"  {k}: {v}" for k, v in fields if v not in (None, ""))
    return _text(f"Document {did}:\n{body}")


@tool(
    "query_document_records",
    "Query the STRUCTURED RECORD ROWS extracted from documents — a compliance "
    "guide's requirements, an invoice's line items. Use this for questions whose "
    "answer is a LIST or COUNT across documents: 'which guides require X', "
    "'how many documents state Y', 'list every requirement about Z'. NEVER answer "
    "such questions by counting search_documents passages — passages are a "
    "relevance sample, not a census. Call with NO arguments first to see which "
    "record sets exist. Every response includes a COVERAGE line saying how many "
    "documents were actually extracted — relay it, because unextracted documents "
    "are absent from the rows, not absent from reality. If it reports no records "
    "exist (fallback: true), answer via search_documents instead and say the "
    "answer comes from reading pages, not a structured table.",
    {
        "type": "object",
        "properties": {
            "record_set": {"type": "string",
                           "description": "Which set to query (e.g. "
                                          "'vendor_requirements'). Omit to list "
                                          "available sets."},
            "search": {"type": "string",
                       "description": "Text filter over the rows (e.g. '856 ASN', "
                                      "'carton marking')"},
            "topic": {"type": "string",
                      "description": "Exact topic from the set's controlled "
                                     "vocabulary (shown in list mode)"},
            "document_type": {"type": "string",
                              "description": "Restrict to one document type"},
            "limit": {"type": "integer",
                      "description": "Max rows (default 50, max 200)"},
        },
        "additionalProperties": False,
    },
)
async def query_document_records(args: dict[str, Any]) -> dict[str, Any]:
    payload = {k: args.get(k) for k in
               ("record_set", "search", "topic", "document_type", "limit")
               if args.get(k) not in (None, "")}
    try:
        data, status = await _post_main("/api/internal/document-records", payload,
                                        internal=True, read_timeout=60.0)
    except httpx.TimeoutException:
        return _text(_BUSY_MSG, is_error=True)
    if status != 200:
        msg = data.get("message") if isinstance(data, dict) else data
        return _text(f"Record query failed (HTTP {status}): {msg}. Fall back to "
                     f"search_documents for a page-text answer.", is_error=True)
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        return _text(f"Record query returned an unexpected response: "
                     f"{str(data)[:300]}", is_error=True)
    if not result.get("ok"):
        return _text(f"Record query error: {result.get('error')}. Fall back to "
                     f"search_documents.", is_error=True)
    return _text(result.get("text") or "No output.")


DOCUMENT_TOOLS = [
    list_server_files, import_documents, search_documents,
    list_documents, get_document, query_document_records,
]
