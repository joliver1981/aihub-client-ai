"""
Command Center — File Upload Routes
========================================
Handles file uploads for the CC chat. Files are staged so the LLM can
reference them in conversation context.

Mirrors the builder_service upload pattern but stores files in the CC
artifacts directory for persistence.
"""

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, UploadFile, File, HTTPException, Form

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["upload"])

# ─── Upload Storage ──────────────────────────────────────────────────────

UPLOAD_DIR = Path(__file__).parent.parent / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# In-memory file metadata store (keyed by file_id)
_file_store: Dict[str, dict] = {}


def _reconstruct_file_store():
    """Scan uploads dir on startup to rebuild _file_store from disk."""
    import re
    count = 0
    for fpath in UPLOAD_DIR.iterdir():
        if fpath.name.endswith("_analysis.txt") or fpath.is_dir():
            continue
        # Files are stored as: {file_id}_{original_name}
        # file_id is first 12 chars of a UUID (e.g., "2ced4636-b85")
        match = re.match(r'^([0-9a-f]{8}-[0-9a-f]{3})_(.+)$', fpath.name)
        if not match:
            continue
        file_id = match.group(1)
        original_name = match.group(2)
        if file_id in _file_store:
            continue
        # Guess content type from extension
        ext = os.path.splitext(original_name)[1].lower()
        ct_map = {
            ".pdf": "application/pdf", ".csv": "text/csv", ".txt": "text/plain",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".json": "application/json", ".html": "text/html", ".xml": "text/xml",
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        }
        _file_store[file_id] = {
            "file_id": file_id,
            "filename": original_name,
            "original_filename": original_name,
            "stored_name": fpath.name,
            "size": fpath.stat().st_size,
            "content_type": ct_map.get(ext, "application/octet-stream"),
            "path": str(fpath),
            "session_id": None,
        }
        count += 1
    if count:
        logger.info(f"[upload] Reconstructed {count} file entries from disk")


# Run on import to restore state after restart
_reconstruct_file_store()

# Max file size: 50MB
MAX_FILE_SIZE = 50 * 1024 * 1024

# Allowed content types (broad — Layer 2 will handle parsing)
ALLOWED_EXTENSIONS = {
    ".csv", ".txt", ".xlsx", ".xls", ".pdf", ".json",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
    ".docx", ".doc", ".md", ".html", ".xml",
}


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


def associate_files_to_session(file_ids: List[str], session_id: str):
    """Associate uploaded files with a session."""
    for fid in file_ids:
        if fid in _file_store:
            _file_store[fid]["session_id"] = session_id


def _is_image_file(filename: str, content_type: str = None) -> bool:
    """Check if a file is an image based on extension or content type."""
    image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
    ext = os.path.splitext(filename)[1].lower()
    if ext in image_extensions:
        return True
    if content_type and content_type.startswith("image/"):
        return True
    return False


def _analyze_image(file_bytes: bytes, filename: str, user_message: str = "") -> str:
    """
    Analyze an image using Anthropic Claude Vision (existing API key).
    Falls back to OpenAI GPT-4o if Anthropic is unavailable.
    Returns a text description/analysis of the image.
    """
    import base64

    # Determine MIME type
    ext = os.path.splitext(filename)[1].lower()
    mime_map = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    }
    mime = mime_map.get(ext, "image/png")
    b64 = base64.b64encode(file_bytes).decode("utf-8")

    prompt = (
        "Analyze this image in detail. "
        "If it contains text, extract all readable text. "
        "If it contains charts/graphs, describe the data and trends. "
        "If it contains a screenshot, describe the UI and any visible content. "
        "Otherwise, provide a thorough description."
    )
    if user_message:
        prompt = f"The user uploaded this image with the message: '{user_message}'. {prompt}"

    # Try Anthropic Claude Vision first (BYOK-aware)
    try:
        import config as cfg
        from api_keys_config import create_anthropic_client
        client, anthropic_config = create_anthropic_client()
        if anthropic_config['use_direct_api'] and client:
            anthropic_model = anthropic_config.get('model') or getattr(cfg, 'ANTHROPIC_MODEL', 'claude-sonnet-4-20250514')
            anthropic_max_tokens = min(int(getattr(cfg, 'ANTHROPIC_MAX_TOKENS', 2000)), 4000)
            response = client.messages.create(
                model=anthropic_model,
                max_tokens=anthropic_max_tokens,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": mime,
                                    "data": b64,
                                },
                            },
                            {
                                "type": "text",
                                "text": prompt,
                            },
                        ],
                    }
                ],
            )
            analysis = response.content[0].text
            logger.info(f"[upload] Image analyzed via Claude Vision (source: {anthropic_config['source']}): {filename} ({len(analysis)} chars)")
            return analysis

    except Exception as e:
        logger.warning(f"[upload] Claude Vision failed for {filename}, falling back to OpenAI Vision: {e}")

    # Fallback: OpenAI Vision (BYOK-aware)
    try:
        import openai
        import config as cfg_module  # aliased to avoid shadowing by local 'config' dict below
        from api_keys_config import get_openai_config
        config = get_openai_config(use_alternate_api=False)

        if config['api_type'] == 'open_ai':
            client = openai.OpenAI(api_key=config['api_key'])
            # Priority: OPENAI_VISION_MODEL env -> BYOK config model -> cfg.OPENAI_VISION_MODEL
            # (which itself reads env and defaults to 'gpt-4o' — preserves original behavior)
            vision_model = os.environ.get(
                "OPENAI_VISION_MODEL",
                config.get('model') or getattr(cfg_module, 'OPENAI_VISION_MODEL', 'gpt-4o'),
            )
        else:
            client = openai.AzureOpenAI(
                api_key=config['api_key'],
                api_version=config['api_version'],
                azure_endpoint=config['api_base'],
            )
            vision_model = config['deployment_id']

        response = client.chat.completions.create(
            model=vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"},
                        },
                    ],
                }
            ],
            max_tokens=2000,
        )
        analysis = response.choices[0].message.content
        logger.info(f"[upload] Image analyzed via OpenAI Vision (source: {config['source']}): {filename} ({len(analysis)} chars)")
        return analysis

    except Exception as e:
        logger.error(f"[upload] All image analysis methods failed for {filename}: {e}")
        return f"[Image analysis failed: {str(e)}]"


def _get_analysis_path(file_id: str) -> Path:
    """Path to the cached analysis artifact for a given file."""
    return UPLOAD_DIR / f"{file_id}_analysis.txt"


def _extract_and_cache(file_id: str, meta: dict, user_message: str = "") -> dict:
    """
    Extract or analyze a file, caching the result to disk.
    Returns {"content": str, "chars": int, "method": str, "is_image": bool}
    On failure returns {"content": None, "error": str}
    """
    analysis_path = _get_analysis_path(file_id)

    # Return cached result if available
    if analysis_path.exists():
        cached = analysis_path.read_text(encoding="utf-8")
        logger.info(f"[upload] Using cached analysis for {meta.get('filename', file_id)} ({len(cached)} chars)")
        is_img = _is_image_file(meta.get("filename", ""), meta.get("content_type"))
        return {"content": cached, "chars": len(cached), "method": "cached", "is_image": is_img}

    filename = meta["filename"]
    file_path = Path(meta["path"])
    if not file_path.exists():
        return {"content": None, "error": "File not found on disk"}

    file_bytes = file_path.read_bytes()

    if _is_image_file(filename, meta.get("content_type")):
        analysis = _analyze_image(file_bytes, filename, user_message)
        # Cache to disk
        analysis_path.write_text(analysis, encoding="utf-8")
        logger.info(f"[upload] Image analyzed and cached: {filename} ({len(analysis)} chars)")
        return {"content": analysis, "chars": len(analysis), "method": "vision", "is_image": True}
    else:
        try:
            from attachment_text_extractor import extract_text_from_attachment
            result = extract_text_from_attachment(
                file_bytes=file_bytes,
                filename=filename,
                content_type=meta.get("content_type"),
                max_chars=50000,
            )
            if result.get("success") and result.get("text"):
                text = result["text"]
                method = result.get("extraction_method", "unknown")
                truncated = result.get("truncated", False)
                # Cache to disk
                analysis_path.write_text(text, encoding="utf-8")
                logger.info(f"[upload] Extracted {len(text)} chars from {filename} via {method} (truncated={truncated})")
                return {"content": text, "chars": len(text), "method": method, "is_image": False}
            elif result.get("error"):
                logger.warning(f"[upload] Extraction failed for {filename}: {result['error']}")
                return {"content": None, "error": result["error"]}
            else:
                return {"content": None, "error": "No text content could be extracted"}
        except ImportError as e:
            logger.warning(f"[upload] attachment_text_extractor not available: {e}")
            return {"content": None, "error": f"Missing dependency: {e}"}
        except Exception as e:
            logger.error(f"[upload] Error extracting content from {filename}: {e}")
            return {"content": None, "error": str(e)}


def build_attachment_context(file_ids: List[str], user_message: str = "",
                             user_id: Optional[int] = None,
                             tenant_id: Optional[int] = None,
                             role: int = 0) -> str:
    """
    Build a context block with extracted file content for the LLM.
    Content is extracted once, cached to disk as an artifact, and read from cache
    on subsequent turns. Only the CURRENT turn gets full content; stored history
    gets a compact reference via get_attachment_refs().

    When user_id/tenant_id are supplied, files whose owner doesn't match are
    silently skipped — this prevents one user from referencing another user's
    file_id via the `attachments` parameter (BUG-R3-005 fix).
    """
    if not file_ids:
        return ""

    lines = ["\n\n---\n**Attached Files:**"]

    for fid in file_ids:
        meta = _file_store.get(fid)
        if not meta:
            continue
        # Skip files the current caller doesn't own. When no user context is
        # provided we keep the legacy behavior so internal callers that don't
        # pass user_id still work.
        if user_id is not None and not _file_is_accessible_to(meta, user_id, tenant_id, role):
            logger.warning(f"[upload] Blocked cross-user access: file_id={fid} owner={meta.get('user_id')}/{meta.get('tenant_id')} requester={user_id}/{tenant_id}")
            continue

        filename = meta["filename"]
        size_kb = meta["size"] / 1024
        size_str = f"{size_kb / 1024:.1f} MB" if size_kb >= 1024 else f"{size_kb:.1f} KB"

        lines.append(f"\n### 📎 {filename} ({size_str})")

        result = _extract_and_cache(fid, meta, user_message)

        if result.get("content"):
            content = result["content"]
            method = result["method"]
            if result.get("is_image"):
                lines.append(f"*Image analysis ({method}):*")
            else:
                lines.append(f"*Extracted via {method}:*")
            lines.append(f"```\n{content}\n```")
        elif result.get("error"):
            lines.append(f"*Could not extract content: {result['error']}*")
        else:
            lines.append("*No content could be extracted from this file.*")

    lines.append("\n---\nAnalyze the attached file content above to answer the user's question.")
    return "\n".join(lines)


def get_attachment_refs(file_ids: List[str]) -> str:
    """
    Build compact references for storing in session history.
    Returns something like: [📎 resume.pdf — 5,420 chars extracted | ref: abc123]
    """
    if not file_ids:
        return ""
    refs = []
    for fid in file_ids:
        meta = _file_store.get(fid)
        if not meta:
            continue
        filename = meta.get("original_filename", meta.get("filename", fid))
        analysis_path = _get_analysis_path(fid)
        if analysis_path.exists():
            chars = len(analysis_path.read_text(encoding="utf-8"))
            refs.append(f"[📎 {filename} — {chars:,} chars extracted | ref: {fid}]")
        else:
            size_kb = meta.get("size", 0) / 1024
            refs.append(f"[📎 {filename} — {size_kb:.0f} KB | ref: {fid}]")
    return " ".join(refs)


# ─── Routes ──────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_files(
    files: List[UploadFile] = File(...),
    session_id: Optional[str] = Form(None),
    user_id: Optional[int] = Form(None),
    tenant_id: Optional[int] = Form(None),
):
    """
    Upload one or more files. Returns metadata for each uploaded file.
    Files are stored locally and can be referenced in chat.
    The uploader's user_id/tenant_id are recorded so cross-user access can
    be blocked (BUG-R3-005 fix).
    """
    results = []

    for upload_file in files:
        # Validate file size
        content = await upload_file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File '{upload_file.filename}' exceeds maximum size of 50MB",
            )

        # Validate extension
        filename = upload_file.filename or "unnamed"
        ext = os.path.splitext(filename)[1].lower()
        if ext and ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"File type '{ext}' is not supported. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
            )

        # Generate unique file ID
        file_id = str(uuid.uuid4())[:12]

        # Sanitize filename
        safe_name = "".join(
            c if c.isalnum() or c in ".-_ " else "_"
            for c in filename
        ).strip() or "unnamed"

        # Store file
        stored_name = f"{file_id}_{safe_name}"
        file_path = UPLOAD_DIR / stored_name
        file_path.write_bytes(content)

        # Build metadata
        meta = {
            "file_id": file_id,
            "filename": filename,
            "original_filename": filename,
            "stored_name": stored_name,
            "size": len(content),
            "content_type": upload_file.content_type or "application/octet-stream",
            "path": str(file_path),
            "session_id": session_id,
            "user_id": int(user_id) if user_id is not None else None,
            "tenant_id": int(tenant_id) if tenant_id is not None else None,
            "uploaded_at": datetime.utcnow().isoformat(),
        }

        _file_store[file_id] = meta
        results.append({
            "file_id": file_id,
            "filename": meta["filename"],
            "size": meta["size"],
            "content_type": meta["content_type"],
        })

        logger.info(f"[upload] File uploaded: {meta['filename']} ({len(content)} bytes) -> {file_id} (owner={meta['user_id']}/{meta['tenant_id']})")

    return {"files": results}


def _file_is_accessible_to(meta: dict, user_id: Optional[int], tenant_id: Optional[int], role: int = 0) -> bool:
    """Ownership check for uploaded files (same rules as session ownership).

    - Caller must supply both user_id and tenant_id; missing either → deny.
    - Legacy files with no owner metadata are visible to admins/devs only.
    - Cross-tenant access is absolutely blocked — even admins of a
      different tenant cannot reach this tenant's files.
    - Within the caller's tenant: admins see all, regular users see only
      their own.
    """
    if not isinstance(meta, dict):
        return False
    if user_id is None or tenant_id is None:
        return False
    owner_uid = meta.get("user_id")
    owner_tid = meta.get("tenant_id")
    if owner_uid is None and owner_tid is None:
        return role >= 2
    try:
        req_uid = int(user_id)
        req_tid = int(tenant_id)
        owner_tid_i = int(owner_tid) if owner_tid is not None else None
        owner_uid_i = int(owner_uid) if owner_uid is not None else None
    except (TypeError, ValueError):
        return False
    if owner_tid_i is not None and owner_tid_i != req_tid:
        return False
    if role >= 2:
        return True
    return owner_uid_i is not None and owner_uid_i == req_uid


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

    logger.info(f"[upload] File deleted: {meta['filename']} ({file_id})")
    return {"deleted": True}
