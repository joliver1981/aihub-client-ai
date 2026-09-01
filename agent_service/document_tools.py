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

import asyncio
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
#
# _ALLOWED_EXTS is aligned to what the ENGINE actually handles (LLMDocumentEngine
# process_document dispatch): pdf; images jpg/jpeg/png/gif/webp; word docx/doc;
# excel xlsx/xls; text txt/md/csv/json/xml/html/htm. FIX 2026-08-22: dropped
# bmp/tiff/tif (the engine's image handler rejects them — they were accepted at
# import then failed in the engine) and added the text family + webp the engine
# reads but the old allowlist omitted.
_ALLOWED_EXTS = {
    "pdf", "docx", "doc", "xlsx", "xls",
    "jpg", "jpeg", "png", "gif", "webp",
    "txt", "md", "csv", "json", "xml", "html", "htm",
}

# read_file (2026-08-22): plain-text families are read LOCALLY (instant, zero
# LLM, zero store) — the widest set, not just the engine's. Everything else that
# is a real document type goes through the engine's extract-WITHOUT-store path.
_TEXT_READ_EXTS = {
    "txt", "md", "markdown", "csv", "tsv", "json", "xml", "html", "htm",
    "log", "yaml", "yml", "ini", "cfg", "conf", "py", "js", "ts", "sql",
    "sh", "bat", "ps1", "rtf",
}
_DOC_EXTRACT_EXTS = {"pdf", "docx", "doc", "xlsx", "xls",
                     "jpg", "jpeg", "png", "gif", "webp"}
# Whole-file read; no truncation. This is only a backstop against a pathological
# file (a multi-GB export) that would compound in the SDK transcript every turn.
# Generous by default; raise AGENT_READ_FILE_MAX_MB any time.
_READ_MAX_MB = int(os.getenv("AGENT_READ_FILE_MAX_MB", "25"))

# list_server_files won't enumerate these — the platform's own secret store and
# OS system dirs. Defense-in-depth / hygiene, not a hard boundary (automations
# can read anything); it just keeps the agent from casually walking them.
_FORBIDDEN_DIRS = [
    os.path.normcase(os.path.join(APP_ROOT, "data", "secrets")),
    os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows")),
]


# Role scoping (all-users rollout, james 2026-08-24): the host filesystem is
# Developer+ territory. Regular users (role < 2) keep the delivered-file magic
# — /api/files links, chat attachments — and their own staged tree, nothing
# else. Developer+ behavior is unchanged.
def _own_user_tree(uid: int) -> str:
    """The one server directory a regular user's files live under."""
    return os.path.normcase(os.path.join(APP_ROOT, "data", "agent", "users", str(uid)))


def _under_own_tree(path: str, uid: int) -> bool:
    ncase = os.path.normcase(os.path.abspath(path))
    own = _own_user_tree(uid)
    return ncase == own or ncase.startswith(own + os.sep)


_ROLE1_FS_REFUSAL = ("Browsing arbitrary server paths requires a Developer "
                     "role. I can still read files delivered to you here — an "
                     "/api/files link, a chat attachment, or a file in your "
                     "own agent workspace.")

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


# Under load the document stack's shared Azure SQL tier stalls and every slow
# request holds a waitress thread (main app / doc API — see wsgi*.py); callers
# then queue past our client read timeouts. A raw ReadTimeout traceback read to
# the model like an outage and sent it into retry storms (2026-08-21). Say what
# it actually is: a busy queue. Since 2026-08-21 the servers ALSO answer
# 503 + Retry-After the moment their admission gate is full (the doc API's
# /document/process and the main app's document-search endpoints — see
# docs/doc-api-concurrency-and-fast-busy.md); _busy_text() relays that.
_BUSY_MSG = ("The document stack is BUSY right now — another import or "
             "extraction is holding it. This is a queue, not an outage, and "
             "not evidence the document is missing. Wait about a minute and "
             "call this once more; if it is still busy, tell the user the "
             "document system is working through a backlog rather than "
             "retrying in a loop.")


def _busy_text(data, retry_after=None) -> str:
    """Honest busy message from a server 503 (fast-busy gate). Uses the server's
    own wording + Retry-After when present, else the generic _BUSY_MSG."""
    msg = data.get("message") if isinstance(data, dict) else None
    ra = (data.get("retry_after") if isinstance(data, dict) else None) or retry_after
    if msg:
        tail = (f" Retry in about {ra} seconds; if it is still busy then, tell the "
                f"user the document system is working through a backlog rather "
                f"than retrying in a loop.") if ra else ""
        return f"{msg}{tail}"
    return _BUSY_MSG


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
    # Role scoping (all-users rollout): regular users may only list their OWN
    # data/agent/users/<uid>/ tree — the host filesystem stays Developer+.
    user = CURRENT_USER.get() or {}
    if int(user.get("role") or 0) < 2 and not _under_own_tree(
            path, int(user.get("user_id") or 0)):
        return _text(_ROLE1_FS_REFUSAL, is_error=True)
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
    "don't claim success for files it didn't import. NOT for chat attachments: "
    "to answer about an attached file use read_file / run_python — "
    "importing publishes it into the shared searchable store, so attachments "
    "are refused unless the user explicitly asked to import them (then pass "
    "force=true).",
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
    # Role scoping (all-users rollout): importing from arbitrary host paths is
    # Developer+ — regular users import only from their own staged/delivered
    # tree (the /api/files resolution above already lands there).
    user = CURRENT_USER.get() or {}
    if int(user.get("role") or 0) < 2 and not _under_own_tree(
            path, int(user.get("user_id") or 0)):
        return _text("Importing from arbitrary server paths requires a "
                     "Developer role. I can import files delivered to you here "
                     "(/api/files links) or your chat attachments.",
                     is_error=True)
    recursive = bool(args.get("recursive"))
    force = bool(args.get("force"))
    force_ai = bool(args.get("force_ai_extraction"))

    # Chat-attachment publication guard (2026-08-27): a file in the caller's
    # PRIVATE chat-uploads area is a one-off read, not a publication. Importing
    # it lands it in the shared document store, whose search ACLs are
    # category(document_type)-based — NOT per-user — so other users' searches
    # can surface it. Reading an attachment never requires an import
    # (read_file / run_python), so this path only proceeds on an
    # explicit force=true, which the model may pass only when the user
    # explicitly asked to import/store the attachment.
    if not force:
        try:
            from file_tools import uploads_dir
            up = os.path.normcase(os.path.abspath(
                uploads_dir(int(user.get("user_id") or 0))))
        except Exception:
            up = None
        pcase = os.path.normcase(path)
        if up and (pcase == up or pcase.startswith(up + os.sep)):
            return _text(
                "That file is a private chat attachment — to answer from it, "
                "use read_file (documents) or run_python (CSV/Excel); "
                "no import is needed for a one-off read. Importing would "
                "publish it into the SHARED searchable document store, where "
                "other users' searches can surface it. Only if the user has "
                "EXPLICITLY asked to import/store this attachment, call "
                "import_documents again with force=true.", is_error=True)

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
            if r.status_code == 503:
                # Fast-busy from the doc API's admission gate: nothing was
                # processed for this file. Report it as busy (retry later),
                # not as a broken import.
                try:
                    busy = _unwrap(r.json())
                except Exception:
                    busy = {}
                failed.append((name, "BUSY (not imported): "
                               + _busy_text(busy, r.headers.get("Retry-After"))))
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
    if status == 503:
        return _text(_busy_text(data), is_error=True)
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
    # Several same-type documents all matched every entity the user named —
    # answer must cover the alternatives, not pick one silently.
    ambiguity = result.get("ambiguity_hint")
    if ambiguity:
        body += f"\n\n{ambiguity}"
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
    if status == 503:
        return _text(_busy_text(data), is_error=True)
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


def _looks_binary(sample: bytes) -> bool:
    """Heuristic: NUL byte or a high proportion of non-text bytes => binary."""
    if b"\x00" in sample:
        return True
    if not sample:
        return False
    texty = sum(1 for b in sample if b in (9, 10, 13) or 32 <= b <= 126 or b >= 128)
    return (texty / len(sample)) < 0.85


def _resolve_read_path(raw: str):
    """Map the read_file argument to a concrete server path THIS user may read.
    Accepts an absolute/relative server path, an /api/files/<id> link (or bare
    id) for a download the agent delivered, or a chat-attachment file id — the
    last two resolved owner-scoped through file_tools. Returns (path, err).

    Role scoping (all-users rollout, james 2026-08-24): Developer+ keeps full
    host access (minus _FORBIDDEN_DIRS, as before). Regular users (role < 2)
    resolve delivered/attachment refs first and may only touch raw paths under
    their OWN data/agent/users/<uid>/ tree — everything else is an honest
    refusal, never a silent miss."""
    p = str(raw or "").strip().strip('"')
    if not p:
        return None, "Give me a file path (or an /api/files link / attachment id) to read."
    user = CURRENT_USER.get() or {}
    uid = int(user.get("user_id") or 0)
    role = int(user.get("role") or 0)
    if role < 2:
        # Delivered downloads and chat attachments first — the refs regular
        # users actually hold (owner-scoped resolvers, fail closed).
        try:
            import file_tools
            hit_path, _name = file_tools.resolve_api_files_ref(p, uid)
            if hit_path:
                return hit_path, None
            up = file_tools.resolve_upload(uid, p)
            if up:
                return up[0], None
        except Exception:
            pass
        ap = os.path.abspath(os.path.expanduser(p))
        if os.path.isfile(ap) and _under_own_tree(ap, uid):
            return ap, None
        if os.path.isfile(ap):
            return None, _ROLE1_FS_REFUSAL
        return None, (f"No such file among your delivered files or attachments: "
                      f"{p}. Give the /api/files link of a file delivered to "
                      "you, or an attachment from this chat.")
    ap = os.path.abspath(os.path.expanduser(p))
    if os.path.isfile(ap):
        return ap, None
    # Not a path on disk — try the delivered-download / attachment resolvers.
    try:
        import file_tools
        hit_path, _name = file_tools.resolve_api_files_ref(p, uid)
        if hit_path:
            return hit_path, None
        up = file_tools.resolve_upload(uid, p)
        if up:
            return up[0], None
    except Exception:
        pass
    return None, (f"No such file on the server: {p}. Give a full path "
                  "(list_server_files can help locate it), or the /api/files link "
                  "of a file you delivered.")


@tool(
    "read_file",
    "Read the CONTENTS of a single file on the AI Hub server and return its text "
    "— plainly, for ANY common type: TXT, CSV, JSON, Markdown, code/config, and "
    "documents (PDF, Word, Excel, images). Use this when the user wants you to "
    "LOOK AT or answer from one specific file (a file they attached in chat, a "
    "file you just downloaded, or a path they gave). It does NOT store or index "
    "the file — it's a one-off read, the fast path for 'what's in this file?'. "
    "(To make many files SEARCHABLE later, use import_documents instead; to list "
    "a folder, list_server_files.) Accepts a server path, an /api/files link you "
    "delivered, or a chat-attachment id.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string",
                     "description": "Server file path, an /api/files/<id> link, or "
                                    "a chat-attachment file id."},
            "ocr": {"type": "boolean",
                    "description": "Force AI vision/OCR for a scanned PDF or a "
                                   "photo of text (default false — native text "
                                   "extraction, which is free and fast)."},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
)
async def read_file(args: dict[str, Any]) -> dict[str, Any]:
    path, err = _resolve_read_path(args.get("path"))
    if err:
        return _text(err, is_error=True)
    ncase = os.path.normcase(path)
    for bad in _FORBIDDEN_DIRS:
        if ncase == bad or ncase.startswith(bad + os.sep):
            return _text(f"Refused: that file is in a protected system/secret "
                         "location and won't be read.", is_error=True)
    name = os.path.basename(path)
    ext = name.rsplit(".", 1)[1].lower() if "." in name else ""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return _text(f"Could not read {name}: {e}", is_error=True)
    # Whole-file read, no truncation — the cap only guards against a pathological
    # file that would compound in the transcript. Refuse honestly, never halve.
    if size > _READ_MAX_MB * 1024 * 1024:
        return _text(f"'{name}' is {_fmt_size(size)} — too large to read inline "
                     f"(over the {_READ_MAX_MB} MB read cap; a file this size "
                     "would bloat every later turn). Import it with "
                     "import_documents and query it with search_documents "
                     "instead, or raise AGENT_READ_FILE_MAX_MB.", is_error=True)

    ocr = bool(args.get("ocr"))
    is_doc = ext in _DOC_EXTRACT_EXTS

    # Plain-text path: read locally — instant, zero LLM, nothing stored. Covers
    # the widest set (any text/code/config), which is why a 44-byte CSV no longer
    # detours through the doc engine.
    if not is_doc:
        try:
            raw = await asyncio.to_thread(_read_bytes, path)
        except OSError as e:
            return _text(f"Could not read {name}: {e}", is_error=True)
        if ext not in _TEXT_READ_EXTS and _looks_binary(raw[:4096]):
            return _text(f"'{name}' looks like a binary file I can't read as text. "
                         "If it's a document (PDF, Word, Excel, image), tell me and "
                         "I'll extract it; otherwise it can only be downloaded or "
                         "uploaded, not read.", is_error=True)
        text = raw.decode("utf-8-sig", errors="replace")
        return _text(f"Contents of '{name}' ({_fmt_size(size)}):\n```\n{text}\n```")

    # Document path: extract text via the engine WITHOUT storing or indexing
    # (do_not_store=true), and without the LLM field/type passes (extract_fields
    # + detect_document_type false) — native text only unless ocr=true.
    import httpx
    doc_url = f"{_doc_api_base()}/document/process"
    form = {"filePath": path, "do_not_store": "true", "extract_fields": "false",
            "detect_document_type": "false",
            "force_ai_extraction": "true" if ocr else "false"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0)) as client:
            r = await client.post(doc_url, data=form)
    except httpx.TimeoutException:
        return _text(_BUSY_MSG, is_error=True)
    except Exception as e:
        return _text(f"Could not read {name}: {type(e).__name__}: {e}", is_error=True)
    if r.status_code == 503:
        try:
            return _text(_busy_text(r.json()), is_error=True)
        except Exception:
            return _text(_BUSY_MSG, is_error=True)
    if r.status_code != 200:
        return _text(f"Could not read {name} (HTTP {r.status_code}: "
                     f"{r.text[:200]}).", is_error=True)
    try:
        j = _unwrap(r.json())
    except Exception:
        return _text(f"Could not read {name}: non-JSON response from the "
                     "document engine.", is_error=True)
    # /document/process returns the full result; concatenate page full_text.
    text = ""
    if isinstance(j, dict):
        if isinstance(j.get("document_text"), str):
            text = j["document_text"]
        else:
            pages = j.get("pages") or j.get("extracted_pages") or []
            if isinstance(pages, list):
                text = "\n\n".join(str(p.get("full_text") or p.get("text") or "")
                                   for p in pages if isinstance(p, dict)).strip()
    if not text.strip():
        hint = "" if ocr else " If it's a scanned PDF or a photo, retry with ocr=true."
        return _text(f"'{name}' extracted no text.{hint}", is_error=True)
    if len(text.encode("utf-8")) > _READ_MAX_MB * 1024 * 1024:
        return _text(f"'{name}' extracted more text than the {_READ_MAX_MB} MB read "
                     "cap — import it and use search_documents, or raise "
                     "AGENT_READ_FILE_MAX_MB.", is_error=True)
    kind = "OCR/vision" if ocr else "native"
    return _text(f"Contents of '{name}' ({_fmt_size(size)}, {kind} extraction, "
                 f"not stored):\n```\n{text}\n```")


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


# query_tabular_file removed 2026-08-29: single-lane analysis — run_python is
# the one lane for computation over a file. The lane-choice was a measured
# failure source (haiku Q33/Q41 answered wide-file math from summary stats);
# the /api/internal/tabular/query endpoint it consumed stays in the main app.

DOCUMENT_TOOLS = [
    list_server_files, import_documents, search_documents,
    list_documents, get_document, query_document_records, read_file,
]
