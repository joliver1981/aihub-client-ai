"""
Conversation-scoped file storage for /chat.

Two roots per conversation:
  data/chat_files/{conversation_id}/inputs/   — files the user uploaded
  data/chat_files/{conversation_id}/outputs/  — files created by tools

Storage convention: `{id}_{safe_name}` so the directory can be scanned to
reconstruct metadata after a restart. No DB, no in-memory index that drifts
from disk. Per the project's "keep it simple" direction.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).parent / "data" / "chat_files"
_AGENT_BASE_DIR = Path(__file__).parent / "data" / "agent_files"

DEFAULT_MAX_SIZE_MB = 50
_ID_RE = re.compile(r"^([0-9a-f]{12})_(.+)$")


def _max_size_bytes() -> int:
    try:
        mb = int(os.environ.get("CHAT_FILE_MAX_SIZE_MB", DEFAULT_MAX_SIZE_MB))
    except (TypeError, ValueError):
        mb = DEFAULT_MAX_SIZE_MB
    return max(1, mb) * 1024 * 1024


def _safe_name(name: str) -> str:
    return "".join(
        c if c.isalnum() or c in ".-_ " else "_" for c in (name or "unnamed")
    ).strip() or "unnamed"


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _conv_dir(conv_id: str) -> Path:
    return _BASE_DIR / conv_id


def _inputs_dir(conv_id: str) -> Path:
    d = _conv_dir(conv_id) / "inputs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _outputs_dir(conv_id: str) -> Path:
    d = _conv_dir(conv_id) / "outputs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file_size_ok(size: int) -> bool:
    return size <= _max_size_bytes()


def _describe(path: Path) -> Dict:
    stat = path.stat()
    # Stored as `{id}_{display_name}`. ID may be a random hex (chat_files)
    # or an agent_knowledge document_id (agent_files) — both are
    # underscore-free, so a permissive split on the first underscore works
    # for either format.
    if "_" in path.name:
        file_id, display = path.name.split("_", 1)
    else:
        file_id, display = "", path.name
    return {
        "id": file_id,
        "filename": display,
        "size_bytes": stat.st_size,
        "size_display": _size_display(stat.st_size),
        "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


def _size_display(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


class ChatFileError(Exception):
    pass


class FileTooLargeError(ChatFileError):
    pass


def save_input(conv_id: str, file_bytes: bytes, original_name: str) -> Dict:
    """
    Persist a user-uploaded file under inputs/. Returns a metadata dict
    including file_id. Raises FileTooLargeError if the file exceeds the
    configured per-file cap.
    """
    if not conv_id:
        raise ChatFileError("conversation_id is required to save an input file")
    if len(file_bytes) > _max_size_bytes():
        raise FileTooLargeError(
            f"File '{original_name}' is {_size_display(len(file_bytes))}; "
            f"exceeds CHAT_FILE_MAX_SIZE_MB={_max_size_bytes() // (1024 * 1024)} MB."
        )

    file_id = _new_id()
    stored_name = f"{file_id}_{_safe_name(original_name)}"
    path = _inputs_dir(conv_id) / stored_name
    path.write_bytes(file_bytes)
    logger.info(
        f"[chat_file_manager] saved input conv={conv_id} file_id={file_id} "
        f"name={original_name} size={len(file_bytes)}"
    )
    return {
        "file_id": file_id,
        "filename": original_name,
        "size_bytes": len(file_bytes),
        "size_display": _size_display(len(file_bytes)),
        "path": str(path),
    }


def save_output(conv_id: str, file_bytes: bytes, output_name: str) -> Dict:
    """
    Persist a tool-generated artifact under outputs/. Returns metadata
    including artifact_id. No size cap on outputs — tool-generated.
    """
    if not conv_id:
        raise ChatFileError("conversation_id is required to save an output file")

    artifact_id = _new_id()
    stored_name = f"{artifact_id}_{_safe_name(output_name)}"
    path = _outputs_dir(conv_id) / stored_name
    path.write_bytes(file_bytes)
    logger.info(
        f"[chat_file_manager] saved output conv={conv_id} artifact_id={artifact_id} "
        f"name={output_name} size={len(file_bytes)}"
    )
    return {
        "artifact_id": artifact_id,
        "filename": output_name,
        "size_bytes": len(file_bytes),
        "size_display": _size_display(len(file_bytes)),
        "path": str(path),
        "download_url": f"/api/chat/artifacts/{conv_id}/{artifact_id}/download",
    }


def get_input_path(conv_id: str, file_id: str) -> Optional[Path]:
    if not conv_id or not file_id:
        return None
    inputs = _conv_dir(conv_id) / "inputs"
    if not inputs.exists():
        return None
    for path in inputs.iterdir():
        if path.is_file() and path.name.startswith(f"{file_id}_"):
            return path
    return None


def get_output_path(conv_id: str, artifact_id: str) -> Optional[Path]:
    if not conv_id or not artifact_id:
        return None
    outputs = _conv_dir(conv_id) / "outputs"
    if not outputs.exists():
        return None
    for path in outputs.iterdir():
        if path.is_file() and path.name.startswith(f"{artifact_id}_"):
            return path
    return None


def list_files(conv_id: str) -> Dict[str, List[Dict]]:
    """Return both inputs and outputs for the given conversation."""
    result: Dict[str, List[Dict]] = {"inputs": [], "outputs": []}
    if not conv_id:
        return result
    base = _conv_dir(conv_id)
    if not base.exists():
        return result

    for kind, key in (("inputs", "file_id"), ("outputs", "artifact_id")):
        sub = base / kind
        if not sub.exists():
            continue
        for path in sorted(sub.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not path.is_file():
                continue
            entry = _describe(path)
            entry[key] = entry.pop("id")
            if kind == "outputs":
                entry["download_url"] = (
                    f"/api/chat/artifacts/{conv_id}/{entry['artifact_id']}/download"
                )
            result[kind].append(entry)
    return result


def delete_file(conv_id: str, file_id: str, kind: str = "inputs") -> bool:
    """Remove a single input or output file. Returns True if a file was deleted."""
    if kind not in ("inputs", "outputs"):
        raise ChatFileError(f"kind must be 'inputs' or 'outputs', got {kind!r}")
    sub = _conv_dir(conv_id) / kind
    if not sub.exists():
        return False
    for path in sub.iterdir():
        if path.is_file() and path.name.startswith(f"{file_id}_"):
            path.unlink()
            logger.info(
                f"[chat_file_manager] deleted {kind[:-1]} conv={conv_id} id={file_id}"
            )
            return True
    return False


# ─── Agent + user scoped storage ─────────────────────────────────────────
# Durable file storage scoped to (agent_id, user_id). Same lifecycle as
# agent_knowledge: files persist until explicitly deleted. Lets tools like
# manipulate_pdf access the raw bytes from any conversation the user has
# with that agent.

def _agent_dir(agent_id, user_id) -> Path:
    d = _AGENT_BASE_DIR / str(agent_id) / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_agent_input(
    agent_id, user_id, file_bytes: bytes, original_name: str,
    file_id: Optional[str] = None,
) -> Dict:
    """
    Persist a file under agent_files/{agent_id}/{user_id}/.
    If file_id is supplied (e.g., the agent_knowledge document_id), use it
    so downloads can be addressed by document_id. Otherwise generate one.
    """
    if agent_id is None or user_id is None:
        raise ChatFileError("agent_id and user_id are required to save an agent file")
    if len(file_bytes) > _max_size_bytes():
        raise FileTooLargeError(
            f"File '{original_name}' is {_size_display(len(file_bytes))}; "
            f"exceeds CHAT_FILE_MAX_SIZE_MB={_max_size_bytes() // (1024 * 1024)} MB."
        )

    # Sanitize file_id — the storage convention is `{id}_{name}` with split
    # on the first underscore, so the id itself cannot contain one.
    file_id = (file_id or _new_id()).replace("_", "-")
    stored_name = f"{file_id}_{_safe_name(original_name)}"
    path = _agent_dir(agent_id, user_id) / stored_name
    path.write_bytes(file_bytes)
    logger.info(
        f"[chat_file_manager] saved agent file agent={agent_id} user={user_id} "
        f"file_id={file_id} name={original_name} size={len(file_bytes)}"
    )
    return {
        "file_id": file_id,
        "filename": original_name,
        "size_bytes": len(file_bytes),
        "size_display": _size_display(len(file_bytes)),
        "path": str(path),
        "download_url": f"/api/chat/agent_files/{agent_id}/{user_id}/{file_id}/download",
    }


def get_agent_input_path(agent_id, user_id, file_id: str) -> Optional[Path]:
    if agent_id is None or user_id is None or not file_id:
        return None
    file_id = str(file_id).replace("_", "-")
    d = _AGENT_BASE_DIR / str(agent_id) / str(user_id)
    if not d.exists():
        return None
    for path in d.iterdir():
        if path.is_file() and path.name.startswith(f"{file_id}_"):
            return path
    return None


def list_agent_files(agent_id, user_id) -> List[Dict]:
    """List durable files for an (agent_id, user_id)."""
    out: List[Dict] = []
    if agent_id is None or user_id is None:
        return out
    d = _AGENT_BASE_DIR / str(agent_id) / str(user_id)
    if not d.exists():
        return out
    for path in sorted(d.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_file():
            continue
        entry = _describe(path)
        entry["file_id"] = entry.pop("id")
        entry["download_url"] = (
            f"/api/chat/agent_files/{agent_id}/{user_id}/{entry['file_id']}/download"
        )
        out.append(entry)
    return out


def delete_agent_file(agent_id, user_id, file_id: str) -> bool:
    file_id = str(file_id).replace("_", "-")
    d = _AGENT_BASE_DIR / str(agent_id) / str(user_id)
    if not d.exists():
        return False
    for path in d.iterdir():
        if path.is_file() and path.name.startswith(f"{file_id}_"):
            path.unlink()
            logger.info(
                f"[chat_file_manager] deleted agent file agent={agent_id} "
                f"user={user_id} file_id={file_id}"
            )
            return True
    return False


def migrate_chat_files_to_agent_files(
    conversation_lookup, knowledge_lookup,
) -> int:
    """
    One-shot migration: copy any files in data/chat_files/{conv}/inputs/ to
    data/agent_files/{agent_id}/{user_id}/{document_id}_{name}.

    Args:
        conversation_lookup: callable(conv_id) -> {agent_id, user_id} or None
        knowledge_lookup:    callable(agent_id, user_id) -> list[{filename, document_id}]

    Idempotent — sentinel file at data/agent_files/.migration_v1_done skips
    subsequent runs. Returns the number of files migrated this run.
    """
    sentinel = _AGENT_BASE_DIR / ".migration_v1_done"
    if sentinel.exists():
        return 0
    if not _BASE_DIR.exists():
        _AGENT_BASE_DIR.mkdir(parents=True, exist_ok=True)
        sentinel.write_text(datetime.utcnow().isoformat())
        return 0

    migrated = 0
    for conv_dir in _BASE_DIR.iterdir():
        if not conv_dir.is_dir():
            continue
        inputs_dir = conv_dir / "inputs"
        if not inputs_dir.exists():
            continue
        try:
            ctx = conversation_lookup(conv_dir.name)
        except Exception as e:
            logger.debug(f"migration: conversation_lookup failed for {conv_dir.name}: {e}")
            continue
        if not ctx or not ctx.get("agent_id") or not ctx.get("user_id"):
            continue
        agent_id, user_id = ctx["agent_id"], ctx["user_id"]
        try:
            knowledge = knowledge_lookup(agent_id, user_id)
        except Exception as e:
            logger.debug(f"migration: knowledge_lookup failed agent={agent_id}: {e}")
            knowledge = []
        by_name = {k["filename"]: k["document_id"] for k in (knowledge or []) if k.get("filename")}

        for path in inputs_dir.iterdir():
            if not path.is_file():
                continue
            original_name = path.name.split("_", 1)[-1] if "_" in path.name else path.name
            doc_id = by_name.get(original_name)
            if not doc_id:
                continue
            doc_id_safe = str(doc_id).replace("_", "-")
            target = _agent_dir(agent_id, user_id) / f"{doc_id_safe}_{_safe_name(original_name)}"
            if target.exists():
                continue
            try:
                target.write_bytes(path.read_bytes())
                migrated += 1
                logger.info(
                    f"[migration] {path.name} -> agent_files/{agent_id}/{user_id}/{target.name}"
                )
            except Exception as e:
                logger.warning(f"[migration] failed to copy {path}: {e}")

    _AGENT_BASE_DIR.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(datetime.utcnow().isoformat())
    logger.info(f"[migration] complete — migrated {migrated} file(s)")
    return migrated


def delete_conversation(conv_id: str) -> int:
    """Wipe all files for a conversation. Returns count deleted."""
    base = _conv_dir(conv_id)
    if not base.exists():
        return 0
    count = 0
    for sub in ("inputs", "outputs"):
        sub_dir = base / sub
        if not sub_dir.exists():
            continue
        for path in sub_dir.iterdir():
            if path.is_file():
                path.unlink()
                count += 1
        sub_dir.rmdir()
    try:
        base.rmdir()
    except OSError:
        pass
    logger.info(f"[chat_file_manager] deleted conversation conv={conv_id} files={count}")
    return count
