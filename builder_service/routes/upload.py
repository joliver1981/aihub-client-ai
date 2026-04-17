"""
Builder Service — File Upload Routes
========================================
Handles file uploads for the builder agent chat.
Files are staged temporarily so the builder agent can reference them
for operations like adding knowledge to an agent.
"""

import logging
import os
import uuid
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, UploadFile, File, HTTPException, Form

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# ─── Upload Storage ──────────────────────────────────────────────────────

UPLOAD_DIR = Path(__file__).parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# In-memory file metadata store (keyed by file_id)
_file_store: Dict[str, dict] = {}

# Max file size: 50MB
MAX_FILE_SIZE = 50 * 1024 * 1024


def get_file_metadata(file_id: str) -> Optional[dict]:
    """Get metadata for an uploaded file."""
    return _file_store.get(file_id)


def get_files_for_session(session_id: str) -> List[dict]:
    """Get all files associated with a session."""
    return [f for f in _file_store.values() if f.get("session_id") == session_id]


def get_file_path(file_id: str) -> Optional[Path]:
    """Get the filesystem path for an uploaded file."""
    meta = _file_store.get(file_id)
    if not meta:
        return None
    return Path(meta["path"])


def get_file_path_by_filename(filename: str) -> Optional[Path]:
    """Reverse lookup: find a file by its original filename.

    Returns the path for the most recently uploaded file with this name,
    or None if no match. Case-insensitive.
    """
    matches = [
        meta for meta in _file_store.values()
        if meta.get("filename", "").lower() == filename.lower()
    ]
    if not matches:
        return None
    # Most recent upload wins (by uploaded_at timestamp)
    best = max(matches, key=lambda m: m.get("uploaded_at", ""))
    path = Path(best["path"])
    return path if path.exists() else None


def associate_files_to_session(file_ids: List[str], session_id: str):
    """Associate uploaded files with a session."""
    for fid in file_ids:
        if fid in _file_store:
            _file_store[fid]["session_id"] = session_id


def build_attachment_context(file_ids: List[str]) -> str:
    """
    Build a text context block describing attached files for the agent.
    This is appended to the user's message so the agent knows about the files.
    """
    if not file_ids:
        return ""

    lines = ["\n\n---\n**Attached Files:**"]
    for fid in file_ids:
        meta = _file_store.get(fid)
        if meta:
            size_kb = meta["size"] / 1024
            if size_kb >= 1024:
                size_str = f"{size_kb / 1024:.1f} MB"
            else:
                size_str = f"{size_kb:.1f} KB"
            lines.append(
                f"- File ID: `{fid}` — {meta['filename']} "
                f"(Type: {meta['content_type']}, Size: {size_str})"
            )

    lines.append(
        "\nThese files have been uploaded and are available on the server. "
        "When using these files in actions, ALWAYS reference them by their "
        "**File ID** (the value after 'File ID:'). Do NOT use the filename."
    )
    return "\n".join(lines)


# ─── Routes ──────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_files(
    files: List[UploadFile] = File(...),
    session_id: Optional[str] = Form(None),
):
    """
    Upload one or more files. Returns metadata for each uploaded file.
    Files are stored locally and can be referenced by the builder agent.
    """
    results = []

    for upload_file in files:
        # Validate file size by reading content
        content = await upload_file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File '{upload_file.filename}' exceeds maximum size of 50MB",
            )

        # Generate unique file ID
        file_id = str(uuid.uuid4())[:12]

        # Sanitize filename
        safe_name = "".join(
            c if c.isalnum() or c in ".-_ " else "_"
            for c in (upload_file.filename or "unnamed")
        ).strip()
        if not safe_name:
            safe_name = "unnamed"

        # Store file
        stored_name = f"{file_id}_{safe_name}"
        file_path = UPLOAD_DIR / stored_name
        file_path.write_bytes(content)

        # Build metadata
        meta = {
            "file_id": file_id,
            "filename": upload_file.filename or safe_name,
            "stored_name": stored_name,
            "size": len(content),
            "content_type": upload_file.content_type or "application/octet-stream",
            "path": str(file_path),
            "session_id": session_id,
            "uploaded_at": datetime.utcnow().isoformat(),
        }

        _file_store[file_id] = meta
        results.append({
            "file_id": file_id,
            "filename": meta["filename"],
            "size": meta["size"],
            "content_type": meta["content_type"],
        })

        logger.info(f"File uploaded: {meta['filename']} ({len(content)} bytes) -> {file_id}")

    return {"files": results}


@router.get("/uploads")
async def list_uploads(session_id: Optional[str] = None):
    """List uploaded files, optionally filtered by session."""
    if session_id:
        files = get_files_for_session(session_id)
    else:
        files = list(_file_store.values())

    return {
        "files": [
            {
                "file_id": f["file_id"],
                "filename": f["filename"],
                "size": f["size"],
                "content_type": f["content_type"],
                "uploaded_at": f.get("uploaded_at"),
            }
            for f in files
        ]
    }


@router.delete("/uploads/{file_id}")
async def delete_upload(file_id: str):
    """Delete an uploaded file."""
    meta = _file_store.pop(file_id, None)
    if not meta:
        raise HTTPException(status_code=404, detail="File not found")

    # Remove from filesystem
    file_path = Path(meta["path"])
    if file_path.exists():
        file_path.unlink()

    logger.info(f"File deleted: {meta['filename']} ({file_id})")
    return {"deleted": True}
