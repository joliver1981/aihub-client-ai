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
  semantic+field search, scoped to the caller's category ACL). No knowledge
  agent required — the old skill wrongly implied one was.
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
import time
from datetime import datetime
from typing import Any

import httpx

from claude_agent_sdk import tool

from agent_config import (
    APP_ROOT, AI_HUB_API_KEY, get_base_url, get_internal_api_key, logger,
)
from platform_tools import CURRENT_USER, _text, _unwrap

# ---------------------------------------------------------------------------
# How much text one tool result can carry
# ---------------------------------------------------------------------------
# The limit is not ours to choose. The runtime under the SDK replaces any text
# result longer than 50,000 characters with a 2 KB preview and the path of a file
# the model has no tool to open, so a longer result silently becomes a nearly
# empty one. A tool can declare a larger size, up to the runtime's own maximum of
# 500,000 characters. Measured 2026-11-25 with SDK 0.2.132: 49,000 characters were
# delivered whole and 51,000 were replaced by the preview; with the declaration
# 450,000 were delivered whole.
#
# The tools here that return document text declare that maximum and return the
# text WHOLE. Before, search_documents cut every passage at 4,000 characters with
# no sign that it had (12 passages x 4,000 stayed just under the 50,000), and an
# answer that sat further down a long page was simply not there. What still does
# not fit in one result is never cut silently: whole passages are left off the end,
# or a long file is returned in parts, and the result says what is missing and how
# to ask for it.
_RUNTIME_DEFAULT_RESULT_CHARS = 50_000
_RUNTIME_MAX_RESULT_CHARS = 500_000
_RESULT_HEADROOM_CHARS = 5_000      # our own header, notes and code fences


def _result_ceiling():
    """(characters one result may carry, the annotation that declares it).

    The larger size only exists when the installed SDK forwards the declaration to
    the runtime. Checked from the SDK itself, not assumed: with an SDK that does
    not, declaring 500,000 would change nothing and every long result would come
    back as the 2 KB preview. Then the ceiling stays at the runtime's default."""
    try:
        import inspect
        import claude_agent_sdk
        from mcp.types import ToolAnnotations
        if "maxResultSizeChars" in inspect.getsource(claude_agent_sdk.create_sdk_mcp_server):
            return (_RUNTIME_MAX_RESULT_CHARS,
                    ToolAnnotations(maxResultSizeChars=_RUNTIME_MAX_RESULT_CHARS))
    except Exception as e:
        logger.warning(f"tool results stay at the runtime's default size "
                       f"({_RUNTIME_DEFAULT_RESULT_CHARS:,} characters): {e}")
    return _RUNTIME_DEFAULT_RESULT_CHARS, None


RESULT_MAX_CHARS, _WHOLE_RESULT = _result_ceiling()


def _result_budget() -> int:
    """Characters of document text one result has room for."""
    return RESULT_MAX_CHARS - _RESULT_HEADROOM_CHARS


# ---------------------------------------------------------------------------
# How much one search returns, and reading on
# ---------------------------------------------------------------------------
# When the model does not say how many passages it wants, one search returns
# everything that matched, best first, as far as two limits that are not ours
# allow (James 2026-11-25: "set our caps based on the context window; the only
# caps that have ever hurt this application were self imposed"):
#   - what one tool result can carry: the runtime's 500,000 characters (see
#     _result_ceiling). With the full context window this is the limit that binds.
#   - the model's context window (agent_config.context_window_tokens): 1,000,000
#     tokens for the current models. One search may take a quarter of it, so that
#     a conversation holds several before the runtime has to shorten it. On a
#     200,000-token window (the switch off, or an older model) that is 130,000
#     characters.
# It was 12 passages before, whatever their size, and an answer on the 15th was
# never read. What does not fit is stated in the result and the model reads on
# with start_at; nothing is ever cut inside a passage for this size.
#
# Sizes behind it, all measured 2026-11-25 on the default model:
#   - manual pages cost 2.55 to 2.72 characters per token (2.64 over 646,956
#     characters), not the 4 of plain English prose;
#   - a conversation starts at about 53,000 tokens (system prompt and tool
#     descriptions) before the first question is read.
# AGENT_DOC_SEARCH_RESULT_CHARS, when set, replaces the rule with a fixed size
# (for instance to keep a search under a price step of the model).
_CHARS_PER_TOKEN = 2.6
_SEARCH_SHARE_OF_WINDOW = 4


def _context_window_tokens() -> int:
    """The context window of the model this turn runs on."""
    try:
        import agent_config
        role = (CURRENT_USER.get() or {}).get("role")
        return int(agent_config.context_window_tokens(agent_config.get_effective_model(role)))
    except Exception as e:
        logger.warning(f"context window unknown, taking the runtime's default: {e}")
        return 200_000


def _default_search_chars() -> int:
    """Characters of passages one search returns when the model names no number
    (before the limit of one tool result is applied)."""
    fixed = os.getenv("AGENT_DOC_SEARCH_RESULT_CHARS")
    if fixed not in (None, ""):
        try:
            if int(fixed) > 0:
                return int(fixed)
        except (TypeError, ValueError):
            pass
        logger.warning(f"AGENT_DOC_SEARCH_RESULT_CHARS={fixed!r} is not a number of characters; "
                       "sizing the search by the context window instead")
    return int(_context_window_tokens() / _SEARCH_SHARE_OF_WINDOW * _CHARS_PER_TOKEN)


# Reading on must continue the list the model saw the start of. Running the search
# a second time is not that: the engine plans every search afresh, so the same
# question can come back with other passages in other positions, and it takes
# another 15 to 30 seconds. The ranked passages of a search are therefore kept for
# a short while, per user, conversation and question, and start_at reads from them.
# The model does not always repeat its query word for word when it reads on
# (measured: one of two forced cases added a word). start_at can only mean "after
# the passages I was shown", so a query that was not searched reads on in the
# conversation's last search, and the result says in which.
_KEPT_SEARCH_SECONDS = 900.0
# Memory this service spends on kept searches, not a limit on any search: the
# oldest go first, and a search that is no longer kept is simply run again.
_KEPT_SEARCH_TOTAL_CHARS = 20_000_000
_kept_searches: dict = {}          # (user id, conversation, question) -> (when, found, characters)


def _kept_key(query: str):
    user = CURRENT_USER.get() or {}
    uid = user.get("user_id")
    if not uid:
        return None                # the service principal is not a person: nothing is kept for it
    return (str(uid), str(user.get("session_id") or ""), " ".join(str(query).lower().split()))


def _keep_search(query: str, found: dict) -> None:
    key = _kept_key(query)
    if key is None:
        return
    now = time.monotonic()
    for k in [k for k, v in _kept_searches.items() if now - v[0] > _KEPT_SEARCH_SECONDS]:
        _kept_searches.pop(k, None)
    # Taken out and put back, so the order of the dict is the order of searching:
    # "the last search" and "the oldest" are read from it, not from the clock.
    _kept_searches.pop(key, None)
    _kept_searches[key] = (now, found, sum(len(p) for p in found.get("passages") or []))
    total = sum(v[2] for v in _kept_searches.values())
    for k in list(_kept_searches):
        if total <= _KEPT_SEARCH_TOTAL_CHARS:
            break
        if k != key:
            total -= _kept_searches.pop(k)[2]


def _kept_search(query: str):
    """The kept search to read on in, or None: this very query when it was
    searched, otherwise this conversation's last search."""
    key = _kept_key(query)
    if key is None:
        return None
    now = time.monotonic()
    mine = [k for k, v in _kept_searches.items()
            if k[:2] == key[:2] and now - v[0] <= _KEPT_SEARCH_SECONDS]
    if key in mine:
        return _kept_searches[key][1]
    if mine and key[1]:            # only inside a conversation we can tell apart
        return _kept_searches[mine[-1]][1]
    return None


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
# Vision (pass 3, 2026-09-02): images are returned to the model as an IMAGE
# content block — the brain is Claude and sees the picture itself (the SDK
# forwards {"type":"image","data","mimeType"} tool-result items). ocr=true
# keeps the old text-extraction path for "transcribe this document photo".
_IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}
_IMAGE_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
               "gif": "image/gif", "webp": "image/webp"}
_IMAGE_INLINE_MAX_MB = float(os.getenv("AGENT_IMAGE_INLINE_MAX_MB", "4"))


def image_block(path: str, ext: str, size: int):
    """The image content item for read_file, or None when the file is over the
    inline cap (the model API rejects very large images; fall back to text)."""
    if ext not in _IMAGE_EXTS or size > _IMAGE_INLINE_MAX_MB * 1024 * 1024:
        return None
    import base64
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    return {"type": "image", "data": base64.b64encode(data).decode("ascii"),
            "mimeType": _IMAGE_MIME[ext]}
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


async def _knowledge_agents_note() -> str:
    """Text added to a search_documents result: the agents this user can ask that
    hold their own knowledge documents ('' when there are none, or when the
    directory cannot be read — the search result itself is never lost to it)."""
    try:
        import agent_builder_tools as abt
        sources = await abt.knowledge_sources_for_user()
        if not sources:
            return ""
        return ("\n\nNOT SEARCHED HERE — agents that hold their own knowledge documents:\n"
                + abt.format_knowledge_sources(sources, limit=abt._KNOWLEDGE_INLINE_AGENTS)
                + "\nIf one of them covers the subject of the question, ask it with ask_agent "
                  "(give it the complete question — it does not see this conversation) before "
                  "you conclude that the answer is not in the documents.")
    except Exception as e:
        logger.warning(f"knowledge agents note unavailable: {e}")
        return ""


@tool(
    "search_documents",
    "Search the AI Hub document library and get the most relevant passages to "
    "answer a question about imported documents. It searches every document in "
    "the LIBRARY that this user can access (semantic + field search) — no API "
    "key needed. Returns matching passages with their source filename and page "
    "so you can cite them. It does NOT search the knowledge documents that "
    "belong to an agent (manuals, policies, customer requirements given to a "
    "General Agent): the result names the agents that hold such documents, and "
    "when one of them covers the subject you ask it with ask_agent. Only when "
    "the library has no passage AND no agent covers the subject, say you found "
    "nothing among the documents you can search and suggest importing them. "
    "Each passage is a WHOLE page, best match first. One result carries as many "
    "of the matching passages as fit and says when more matched. When the ones "
    "shown do not answer the question and more matched, READ ON (the same query "
    "with start_at) before you reword the search or conclude the documents do "
    "not say.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "The natural-language question to search for"},
            "max_results": {"type": "integer",
                            "description": "Leave it out to get everything that "
                                           "matched, as far as one result can carry "
                                           "it. Give a number only when you want "
                                           "exactly that many passages."},
            "start_at": {"type": "integer",
                         "description": "Position of the first passage to return, "
                                        "counting from 1 (default 1). With the SAME "
                                        "query it reads on: the passages after the "
                                        "ones you already have."},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    annotations=_WHOLE_RESULT,
)
async def search_documents(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    if not query:
        return _text("Give me a question to search the documents for.",
                     is_error=True)
    # No max_results: everything that matched, best first, as far as one result
    # and the model's context window allow (see _default_search_chars). A number:
    # that many, as far as one result carries them.
    try:
        limit = int(args.get("max_results") or 0)
    except (TypeError, ValueError):
        limit = 0
    limit = limit if limit > 0 else None
    try:
        start_at = int(args.get("start_at") or 1)
    except (TypeError, ValueError):
        start_at = 1
    start_at = max(1, start_at)

    # Reading on continues the list the model saw the start of (see _keep_search).
    found = _kept_search(query) if start_at > 1 else None
    if found is not None:
        return _text(_render_passages(query, found, start_at, limit,
                                      await _knowledge_agents_note(), reran=False))

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
    # Agents that hold their own knowledge documents — a source this search
    # does not cover. Named in EVERY result (hit or miss), so the model sees
    # who else can answer at the moment it would otherwise say "not found".
    agents_note = await _knowledge_agents_note()
    if not rows:
        # The engine may synthesize a text answer with no structured passages,
        # or genuinely find nothing — surface either honestly.
        answer = (result.get("answer") or result.get("text") or "").strip()
        if answer:
            room = max(0, _result_budget() - len(agents_note))
            left = len(answer) - room
            if left > 0:
                # the runtime cannot carry more in one result; say so, never cut silently
                answer = (answer[:room] + f"\n\n[NOT ALL SHOWN: the last {left:,} characters "
                          "of this answer do not fit in one result. Ask a narrower question "
                          "to read the rest.]")
            return _text(f"Results for \"{query}\":\n{answer}{agents_note}")
        if agents_note:
            return _text(f"No document in the library matched \"{query}\". That is the "
                         "document library only." + agents_note)
        return _text(f"No documents matched \"{query}\". If you expected hits, "
                     "the documents may not be imported yet — check with "
                     "list_documents or import them with import_documents.")

    # Dedupe near-identical passages by (filename, page); keep first (ranked).
    # Every passage is rendered WHOLE: it is a full page (2026-08-14), and a cut
    # inside it loses whatever sits further down the page without a trace.
    seen, passages = set(), []
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
        snippet = " ".join(str(text).split())
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
        passages.append(line)

    found = {
        "query": query,
        "passages": passages,
        # Discovery bridge: the server flags when these documents carry structured
        # record rows — the right tool for which/how-many questions.
        "records_hint": result.get("records_hint"),
        # Several same-type documents all matched every entity the user named —
        # answer must cover the alternatives, not pick one silently.
        "ambiguity_hint": result.get("ambiguity_hint"),
        "confidence": qa.get("confidence"),
    }
    _keep_search(query, found)
    return _text(_render_passages(query, found, start_at, limit, agents_note,
                                  reran=start_at > 1))


def _render_passages(query: str, found: dict, start_at: int, limit, agents_note: str,
                     reran: bool) -> str:
    """One result from the ranked passages of a search: the ones asked for, each
    WHOLE, and in words what is not in it and how to read it.

    ``limit`` None = everything from start_at on, as far as one result and the
    context window allow (_default_search_chars).
    ``reran`` = the model asked to read on (start_at > 1) but no search of this
    conversation was kept, so these are positions in a search run just now.
    ``query`` is what the model sent; the list may be an earlier search's
    (reading on with other words), and then the result names that search."""
    asked, query = query, str(found.get("query") or query)
    other_words = " ".join(asked.lower().split()) != " ".join(query.lower().split())
    passages = found.get("passages") or []
    tail = ""
    if found.get("records_hint"):
        tail += f"\n\n{found['records_hint']}"
    if found.get("ambiguity_hint"):
        tail += f"\n\n{found['ambiguity_hint']}"
    tail += agents_note

    total = len(passages)
    if start_at > total:
        if reran:
            return (f"No earlier search of this conversation is kept, so this one was run now "
                    f"and matched {total} passage(s): there is none at position {start_at}. "
                    f"Read them from the first one (leave start_at out)." + tail)
        return (f"Only {total} passage(s) matched \"{query}\", so there is none at "
                f"position {start_at}. You have seen them all."
                + (f" (start_at reads on in your last search, and that is this one. To search "
                   f"for \"{asked}\", leave start_at out.)" if other_words else "") + tail)

    # What one result CAN carry is the runtime's limit, not ours (_result_ceiling).
    # Passages go in whole, in rank order, until the next one would not fit; the
    # rest are counted and named below, never cut.
    hard_room = _result_budget() - len(tail)
    if limit is None:
        wanted = passages[start_at - 1:]
        room = min(hard_room, _default_search_chars())
    else:
        wanted = passages[start_at - 1:start_at - 1 + limit]
        room = hard_room
    out, used = [], 0
    for line in wanted:
        need = len(line) + 1
        if not out and need > hard_room:
            # One page longer than a whole result. The only case in which text
            # inside a passage is left out, and the passage says so itself.
            keep = max(0, hard_room - 300)
            out.append(line[:keep] + f"\n    [THIS PAGE IS CUT HERE: it has {len(line):,} "
                       f"characters and one result carries {RESULT_MAX_CHARS:,}. The rest of "
                       "the page is not shown.]")
            break
        if out and used + need > room:
            break
        out.append(line)        # the first one always goes in whole when a result can carry it
        used += need
    first, last = start_at, start_at + len(out) - 1
    held_back = (len(wanted) - len(out)) if limit is not None else 0    # asked for, did not fit
    after = total - last                        # everything ranked after the last one shown

    if total == len(out):
        header = f"{total} passage(s) for \"{query}\""
    else:
        header = f"Passages {first} to {last} of {total} for \"{query}\""
    conf = found.get("confidence")
    if conf and conf != "unknown":
        header += f" · search confidence: {conf}"
    body = header + ":\n" + "\n".join(out)
    if reran:
        body = (f"NOTE: no earlier search of this conversation is kept (a search is kept for "
                f"{int(_KEPT_SEARCH_SECONDS // 60)} minutes), so this one was run now. The "
                f"positions below are positions in THIS run, and its first {start_at - 1} "
                f"passage(s) are not shown: read them too (leave start_at out).\n\n") + body
    elif other_words:
        body = (f"NOTE: this reads on in your last search, \"{query}\". start_at continues a "
                f"list you have seen the start of, so \"{asked}\" was not searched. To search "
                f"for it, leave start_at out.\n\n") + body
    if held_back:
        body += (f"\n\nNOT ALL SHOWN: {held_back} of the {len(wanted)} passages you asked for "
                 f"did not fit in one result (it carries {RESULT_MAX_CHARS:,} characters). They "
                 f"were left out whole, nothing was cut inside a passage. {after} passage(s) in "
                 f"all rank after the last one shown: read on with the same query and "
                 f"start_at={last + 1}.")
    elif after:
        body += (f"\n\nMORE MATCHED, NOT SHOWN: {after} further passage(s) rank after these. "
                 f"If the passages above do not answer the question, read on before you reword "
                 f"the search or tell the user the documents do not say: call search_documents "
                 f"with the same query and start_at={last + 1}.")
    return body + tail


# ---------------------------------------------------------------------------
# list_documents / get_document
# ---------------------------------------------------------------------------

# Scope honesty for a RESTRICTED caller (RU-06b/RU-06c, james 2026-09-07,
# "option B"). /api/documents' totals are computed inside the caller's category
# ACL, so the model saw "11" and told a regular user "the store contains 11
# documents" -- a per-user slice reported as the platform's state, in a way the
# user cannot detect. The listing wording now says "you can see N"; this footer
# supplies the other half of the frame -- the boundary of what the model can
# search -- so it can answer "I can't find it in what I can search; my access
# covers X and Y" without confirming or denying that anything exists beyond
# that boundary (existence-hiding is deliberate and stays). Names only, as DATA
# at the end of the result: the coverage-ledger pattern (platform_tools
# coverage_footer), NOT a guard over the model's reply. The server emits
# `stats.accessible_document_types` ONLY for a caller whose grants leave part
# of the store hidden, so an admin or any unrestricted caller gets no line and
# no change at all.
_SCOPE_MAX_NAMES = 20


def access_scope_line(stats) -> str:
    """'\\n(Your access covers: a, b.)' for a restricted caller, else ''."""
    names = stats.get("accessible_document_types") if isinstance(stats, dict) else None
    if not isinstance(names, list):
        return ""
    names = [str(n) for n in names if n not in (None, "")]
    if not names:
        # Restricted, and none of the types currently holding documents is
        # inside the grant (the grant covers only empty types).
        return ("\n(Your access covers none of the document types that "
                "currently hold documents.)")
    shown = ", ".join(names[:_SCOPE_MAX_NAMES])
    more = (f", +{len(names) - _SCOPE_MAX_NAMES} more"
            if len(names) > _SCOPE_MAX_NAMES else "")
    return f"\n(Your access covers: {shown}{more}.)"


@tool(
    "list_documents",
    "List the documents this user can see in the AI Hub document store, most "
    "recent first — optionally filtered by a filename search or document type. "
    "Read-only; its totals cover the user's access, not the whole platform. Use "
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
    if data.get("access") == "denied":
        # Category ACL deny-all (doc-acl G2): the server answers an EMPTY
        # listing with HTTP 200 plus this marker. Say what it is — an access
        # boundary — instead of "the store is empty" (james 2026-09-03); same
        # wording the search / records tools relay.
        return _text(data.get("message")
                     or "You do not have access to any document categories — this is "
                        "an access restriction, not an empty store. An administrator "
                        "can grant access on the Groups page.")

    docs = data.get("documents") or []
    stats = data.get("stats") or {}
    total = (data.get("pagination") or {}).get("total_count", len(docs))
    # Scope honesty (RU-06b, james 2026-09-07 "option B"): the server's totals
    # are computed inside the caller's category ACL, so they describe what the
    # CALLER can see -- never "the store". A restricted caller's result also
    # ends with the types their access covers, as data (access_scope_line).
    visible = stats.get("total_documents", 0)
    scope = access_scope_line(stats)
    if not docs:
        return _text("No match among the documents you can search. "
                     f"(You can see {visible} document(s).)" + scope)
    lines = [f"{len(docs)} of {total} matching document(s) "
             f"(you can see {visible}):"]
    for d in docs:
        when = (d.get("processed_at") or "")[:16].replace("T", " ")
        lines.append(f"  {d.get('filename')}  ·  {d.get('document_type') or '?'}  "
                     f"·  {d.get('page_count') or '?'}p  ·  {when}  "
                     f"·  id {d.get('document_id')}")
    return _text("\n".join(lines) + scope)


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
        # Hidden == missing server-side (404 either way, no id oracle), so
        # this can only honestly describe the caller's view, never the store.
        return _text(f"No document with id {did} is among the documents you can "
                     "see. Use list_documents to see valid ids.", is_error=True)
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
    "Read the CONTENTS of a single file on the AI Hub server — plainly, for ANY "
    "common type: TXT, CSV, JSON, Markdown, code/config, and documents (PDF, "
    "Word, Excel). IMAGES (png/jpg/gif/webp — a screenshot, a photo, a chart) "
    "come back as the PICTURE ITSELF: you can see it, so describe it, read the "
    "chart or screenshot, answer what is in it; pass ocr=true instead when the "
    "user wants the TEXT transcribed from a document photo. Use this when the "
    "user wants you to LOOK AT or answer from one specific file (a file they "
    "attached in chat, a file you just downloaded, or a path they gave). It does "
    "NOT store or index the file — it's a one-off read, the fast path for "
    "'what's in this file?'. (To make many files SEARCHABLE later, use "
    "import_documents instead; to list a folder, list_server_files.) Accepts a "
    "server path, an /api/files link you delivered, or a chat-attachment id.",
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
            "start": {"type": "integer",
                      "description": "Character position to start reading from "
                                     "(default 0). Only needed when an earlier read "
                                     "of the same file said more characters follow."},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    annotations=_WHOLE_RESULT,
)
async def read_file(args: dict[str, Any]) -> dict[str, Any]:
    path, err = _resolve_read_path(args.get("path"))
    if err:
        return _text(err, is_error=True)
    try:
        start = max(0, int(args.get("start") or 0))
    except (TypeError, ValueError):
        start = 0
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

    # Images: hand the model the picture (vision), unless a transcription was
    # asked for. Over the inline cap -> the extraction path below, with a note.
    if ext in _IMAGE_EXTS and not ocr:
        block = image_block(path, ext, size)
        if block:
            return {"content": [block, {"type": "text", "text":
                    f"Image '{name}' ({_fmt_size(size)}) is attached above — you can "
                    "SEE it. Describe it, read any chart, table or screenshot in it, "
                    "and answer from what is visible. For a text transcription of a "
                    "document photo, call read_file again with ocr=true."}]}
        ocr = True     # too large to show inline — extract text instead

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
        return _file_text_result(name, _fmt_size(size), text, start)

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
    return _file_text_result(name, f"{_fmt_size(size)}, {kind} extraction, not stored",
                             text, start)


def _file_text_result(name: str, label: str, text: str, start: int = 0) -> dict[str, Any]:
    """A file's text as one tool result: whole when it fits in what the runtime
    carries (see _result_ceiling), otherwise the part from ``start`` that fits,
    with where it stands in the file and how to read on. Never cut silently."""
    total = len(text)
    if start >= total > 0:
        return _text(f"'{name}' has {total:,} characters of text; there is nothing at "
                     f"position {start:,}. You have read it all.")
    room = _result_budget()
    part = text[start:start + room]
    end = start + len(part)
    if start == 0 and end >= total:
        return _text(f"Contents of '{name}' ({label}):\n```\n{text}\n```")
    more = (f"\n\nNOT ALL SHOWN: {total - end:,} more characters follow. One result "
            f"carries {RESULT_MAX_CHARS:,} characters; read on by calling read_file "
            f"again with start={end}."
            if end < total else "\n\nThat is the end of the file.")
    return _text(f"Contents of '{name}' ({label}) — characters {start + 1:,} to {end:,} "
                 f"of {total:,}:\n```\n{part}\n```{more}")


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
