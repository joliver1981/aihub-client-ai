"""
File handoff (James, 2026-08-09): The Agent talks to users in a WEB BROWSER.
When a task produces a file (SharePoint download, automation output, export),
the user expects a download link in the chat -- not a server filesystem path.

Mechanics:
- offer_file_download stages a COPY of the server-side file into the user's
  private downloads area (data/agent/users/<uid>/downloads/<uuid>__<name>)
  and returns a markdown link to /api/files/<id>.
- main.py serves GET /api/files/{file_id} with the same Bearer auth as every
  other route, scoped to the requesting user's own downloads dir -- users can
  never fetch each other's files, and only files the agent DELIBERATELY
  offered ever exist in the served tree (copy-on-offer).
- The chat UI intercepts /api/files/ links and fetches them with the auth
  header, so no token ever appears in a URL or transcript.

Safety rails: the source must live under APP_ROOT (uploads/, data/agent/ --
where platform operations legitimately write), size-capped, filename
sanitized, uuid prefix prevents collisions and path tricks.
"""

import os
import re
import shutil
import uuid
from typing import Any, Optional

from claude_agent_sdk import tool

from agent_config import APP_ROOT, USERS_DIR, logger
from platform_tools import CURRENT_USER, _text

MAX_OFFER_MB = int(os.getenv("AGENT_FILE_OFFER_MAX_MB", "200"))
_NAME_RE = re.compile(r"[^A-Za-z0-9._ ()-]")


def downloads_dir(user_id: int) -> str:
    d = os.path.join(USERS_DIR, str(int(user_id)), "downloads")
    os.makedirs(d, exist_ok=True)
    return d


def resolve_offer(user_id: int, file_id: str) -> Optional[tuple]:
    """(path, original_name) for a served file THIS user owns, else None.
    file_id is a uuid — never trusted as a path segment."""
    safe = "".join(ch for ch in str(file_id) if ch.isalnum() or ch == "-")
    if safe != file_id or len(safe) < 8:
        return None
    d = downloads_dir(user_id)
    for name in os.listdir(d):
        if name.startswith(safe + "__"):
            return os.path.join(d, name), name.split("__", 1)[1]
    return None


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n} B"


def stage_offer(user_id: int, server_path: str,
                display_name: Optional[str] = None) -> tuple:
    """Stage a private copy of a server file for this user and return
    (True, markdown_link, staged_path) or (False, honest_error_text, None).
    The staging core of offer_file_download, shared with portal_tools so
    downloaded files become working chat links in one deterministic step.
    The staged_path is returned so a delivering tool can hand the MODEL its
    own server-side handle — without it, the transcript holds only the
    user-facing /api/files/ link and later "read that file" asks degenerate
    into filesystem hunts (James's Alpaca-statement repro, 2026-08-21)."""
    raw = str(server_path or "").strip().strip('"')
    src = os.path.abspath(raw)
    root = os.path.abspath(APP_ROOT)
    if not src.startswith(root + os.sep):
        return False, ("Refused: that path is outside the platform's data area "
                       "— only files produced under the AI Hub root can be "
                       "offered."), None
    if not os.path.isfile(src):
        return False, (f"No file exists at {raw} — nothing to offer. Check the "
                       "download step's actual output path."), None
    size = os.path.getsize(src)
    if size > MAX_OFFER_MB * 1024 * 1024:
        return False, (f"File is {_fmt_size(size)} — over the "
                       f"{MAX_OFFER_MB} MB handoff cap."), None
    name = _NAME_RE.sub("_", str(display_name
                                 or os.path.basename(src)))[:150] or "file"
    fid = str(uuid.uuid4())
    dst = os.path.join(downloads_dir(int(user_id)), f"{fid}__{name}")
    try:
        shutil.copy2(src, dst)
    except OSError as e:
        return False, f"Could not stage the file: {e}", None
    logger.info(f"file offered: user {int(user_id)} <- {src} ({_fmt_size(size)})")
    return True, f"[⤓ {name} ({_fmt_size(size)})](/api/files/{fid})", dst


UPLOAD_MAX_MB = int(os.getenv("AGENT_UPLOAD_MAX_MB", "50"))


def uploads_dir(user_id: int) -> str:
    d = os.path.join(USERS_DIR, str(int(user_id)), "uploads")
    os.makedirs(d, exist_ok=True)
    return d


def save_upload(user_id: int, filename: str, data: bytes) -> tuple:
    """Chat attachment (P2 item 4, 2026-08-22): persist bytes the user attached
    in chat into their private uploads area and return (file_id, path, size).
    Same rails as downloads staging: per-user dir, sanitized name, uuid prefix,
    size cap. Raises ValueError over the cap."""
    size = len(data or b"")
    if size > UPLOAD_MAX_MB * 1024 * 1024:
        raise ValueError(f"File is {_fmt_size(size)} — over the {UPLOAD_MAX_MB} MB "
                         "upload cap.")
    name = _NAME_RE.sub("_", str(filename or "file"))[:150] or "file"
    fid = str(uuid.uuid4())
    dst = os.path.join(uploads_dir(int(user_id)), f"{fid}__{name}")
    with open(dst, "wb") as fh:
        fh.write(data or b"")
    logger.info(f"chat upload: user {int(user_id)} -> {name} ({_fmt_size(size)})")
    return fid, dst, size


def resolve_upload(user_id: int, file_id: str) -> Optional[tuple]:
    """(path, original_name) for an upload THIS user made, else None."""
    safe = "".join(ch for ch in str(file_id) if ch.isalnum() or ch == "-")
    if safe != file_id or len(safe) < 8:
        return None
    d = uploads_dir(user_id)
    for name in os.listdir(d):
        if name.startswith(safe + "__"):
            return os.path.join(d, name), name.split("__", 1)[1]
    return None


def list_uploads(user_id: int) -> list:
    d = uploads_dir(user_id)
    out = []
    for name in sorted(os.listdir(d)):
        if "__" not in name:
            continue
        p = os.path.join(d, name)
        fid, orig = name.split("__", 1)
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({"file_id": fid, "name": orig, "size": st.st_size,
                    "uploaded_at": st.st_mtime})
    out.sort(key=lambda r: -r["uploaded_at"])
    return out


def attachments_prompt_block(user_id: int, file_ids) -> str:
    """Model-facing line prepended to a chat turn that carries attachments:
    the server paths of THIS user's uploads, so every path-taking tool
    (upload_file, import_documents, list_server_files) just works. Never shown
    to the user — the FILES doctrine forbids echoing server paths."""
    rows = []
    for fid in (file_ids or []):
        hit = resolve_upload(int(user_id), str(fid))
        if hit:
            try:
                size = _fmt_size(os.path.getsize(hit[0]))
            except OSError:
                size = "?"
            rows.append(f"{hit[1]} ({size}) -> {hit[0]}")
    if not rows:
        return ""
    return ("[Attached files from the user — server paths for YOUR tools "
            "(upload_file, import_documents, list_server_files); never show "
            "these paths back to the user:\n  " + "\n  ".join(rows) + "\n]")


_FID_RE = re.compile(
    r"(?:/api/files/)?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})", re.I)


def resolve_api_files_ref(text: str, user_id: int) -> tuple:
    """If `text` references a staged download — an /api/files/<id> link or a
    bare file id — return (staged_path, original_name) for THIS user's copy,
    else (None, None). Owner-scoped through resolve_offer: another user's id
    resolves to nothing (fail closed). Lets tools accept the exact handle the
    model holds after delivering a file, instead of erroring into a hunt."""
    m = _FID_RE.search(str(text or ""))
    if not m:
        return None, None
    hit = resolve_offer(int(user_id or 0), m.group(1).lower())
    return (hit[0], hit[1]) if hit else (None, None)


@tool(
    "offer_file_download",
    "Give the user a DOWNLOAD LINK in the chat for a file that exists on the "
    "AI Hub server (a SharePoint/integration download, automation output, "
    "export...). You are talking to users in a WEB BROWSER: never hand them "
    "a server filesystem path — call this instead and include the returned "
    "markdown link VERBATIM in your reply; the chat renders it as a working "
    "download button. The file is staged privately for this user only.",
    {
        "type": "object",
        "properties": {
            "server_path": {"type": "string",
                            "description": "Absolute path of the file on the "
                                           "server (e.g. from a download "
                                           "operation's result)"},
            "display_name": {"type": "string",
                             "description": "Optional nicer filename"},
        },
        "required": ["server_path"],
        "additionalProperties": False,
    },
)
async def offer_file_download(args: dict[str, Any]) -> dict[str, Any]:
    user = CURRENT_USER.get()
    uid = int(user.get("user_id") or 0)
    ok, msg, _path = stage_offer(uid, str(args["server_path"]),
                                 args.get("display_name"))
    if not ok:
        return _text(msg, is_error=True)
    return _text(f"Download ready. Include this link verbatim in your reply:\n{msg}")


FILE_TOOLS = [offer_file_download]
