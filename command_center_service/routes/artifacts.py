"""
Command Center — Artifact Routes
===================================
File download and artifact management API.

Ownership: artifacts are created within a chat session; the session carries
the owning user_context. An artifact is accessible when the requester owns
its parent session (or is an admin viewing a legacy session with no owner).
This closes BUG-R3-006 (cross-user artifact download).
"""

import logging
from typing import Optional
from pathlib import Path
from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["artifacts"])

# Initialized in main.py lifespan
_artifact_mgr = None
_session_mgr = None


def init_artifacts(artifact_mgr, session_mgr=None):
    global _artifact_mgr, _session_mgr
    _artifact_mgr = artifact_mgr
    _session_mgr = session_mgr


def _get_artifact_manager():
    global _artifact_mgr
    if _artifact_mgr is None:
        from command_center.artifacts.artifact_manager import ArtifactManager
        storage_dir = str(Path(__file__).parent.parent / "data" / "artifacts")
        _artifact_mgr = ArtifactManager(storage_dir)
    return _artifact_mgr


def _artifact_accessible_to(meta, user_id: Optional[int], tenant_id: Optional[int], role: int) -> bool:
    """Return True if the caller is allowed to read this artifact.

    An artifact is accessible when the caller owns (or admins of the same
    tenant own) its parent session. The SessionManager ownership check
    already enforces cross-tenant isolation and legacy-session rules —
    we just defer to it via get_session_for.

    If the artifact has no session_id (orphan), it's admin-only and only
    when the caller has claimed a tenant (no unauthenticated fallthroughs).
    """
    if user_id is None or tenant_id is None:
        return False
    session_id = getattr(meta, "session_id", None) if meta else None
    if not session_id:
        return role >= 2
    if _session_mgr is None:
        return False
    owned = _session_mgr.get_session_for(session_id, user_id, tenant_id, role)
    return owned is not None


@router.get("/artifacts")
async def list_artifacts(
    session_id: str = Query(...),
    user_id: Optional[int] = Query(None),
    tenant_id: Optional[int] = Query(None),
    role: int = Query(0),
):
    """List all artifacts for a session (must be owned by the requester)."""
    if _session_mgr is not None:
        owned = _session_mgr.get_session_for(session_id, user_id, tenant_id, role)
        if not owned and role < 2:
            return JSONResponse({"error": "Session not found"}, status_code=404)
    mgr = _get_artifact_manager()
    artifacts = mgr.list_artifacts(session_id)
    return {"artifacts": artifacts, "count": len(artifacts)}


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(
    artifact_id: str,
    user_id: Optional[int] = Query(None),
    tenant_id: Optional[int] = Query(None),
    role: int = Query(0),
):
    """Download an artifact file (owner-only)."""
    mgr = _get_artifact_manager()
    meta = mgr.get_metadata(artifact_id)
    if not meta:
        return JSONResponse({"error": "Artifact not found"}, status_code=404)

    if not _artifact_accessible_to(meta, user_id, tenant_id, role):
        logger.warning(f"[artifacts] Blocked cross-user download: artifact={artifact_id} session={getattr(meta,'session_id',None)} requester={user_id}/{tenant_id}")
        return JSONResponse({"error": "Artifact not found"}, status_code=404)

    file_path = mgr.get_file_path(artifact_id)
    if not file_path or not file_path.exists():
        return JSONResponse({"error": "Artifact file not found"}, status_code=404)

    return FileResponse(
        path=str(file_path),
        filename=meta.name,
        media_type=meta.mime_type,
    )


@router.get("/artifacts/{artifact_id}")
async def get_artifact_metadata(
    artifact_id: str,
    user_id: Optional[int] = Query(None),
    tenant_id: Optional[int] = Query(None),
    role: int = Query(0),
):
    """Get metadata for a specific artifact (owner-only)."""
    mgr = _get_artifact_manager()
    meta = mgr.get_metadata(artifact_id)
    if not meta:
        return JSONResponse({"error": "Artifact not found"}, status_code=404)
    if not _artifact_accessible_to(meta, user_id, tenant_id, role):
        return JSONResponse({"error": "Artifact not found"}, status_code=404)
    return meta.to_dict()


@router.delete("/artifacts/{artifact_id}")
async def delete_artifact(
    artifact_id: str,
    user_id: Optional[int] = Query(None),
    tenant_id: Optional[int] = Query(None),
    role: int = Query(0),
):
    """Delete an artifact (owner-only)."""
    mgr = _get_artifact_manager()
    meta = mgr.get_metadata(artifact_id)
    if meta and not _artifact_accessible_to(meta, user_id, tenant_id, role):
        return JSONResponse({"error": "Artifact not found"}, status_code=404)
    if mgr.delete_artifact(artifact_id):
        return {"status": "deleted"}
    return JSONResponse({"error": "Artifact not found"}, status_code=404)
