"""
Command Center — Artifact Routes
===================================
File download and artifact management API.
"""

import logging
from pathlib import Path
from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["artifacts"])

# Initialized in main.py lifespan
_artifact_mgr = None


def init_artifacts(artifact_mgr):
    global _artifact_mgr
    _artifact_mgr = artifact_mgr


def _get_artifact_manager():
    global _artifact_mgr
    if _artifact_mgr is None:
        from command_center.artifacts.artifact_manager import ArtifactManager
        storage_dir = str(Path(__file__).parent.parent / "data" / "artifacts")
        _artifact_mgr = ArtifactManager(storage_dir)
    return _artifact_mgr


@router.get("/artifacts")
async def list_artifacts(session_id: str = Query(...)):
    """List all artifacts for a session."""
    mgr = _get_artifact_manager()
    artifacts = mgr.list_artifacts(session_id)
    return {"artifacts": artifacts, "count": len(artifacts)}


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(artifact_id: str):
    """Download an artifact file."""
    mgr = _get_artifact_manager()
    meta = mgr.get_metadata(artifact_id)
    if not meta:
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
async def get_artifact_metadata(artifact_id: str):
    """Get metadata for a specific artifact."""
    mgr = _get_artifact_manager()
    meta = mgr.get_metadata(artifact_id)
    if not meta:
        return JSONResponse({"error": "Artifact not found"}, status_code=404)
    return meta.to_dict()


@router.delete("/artifacts/{artifact_id}")
async def delete_artifact(artifact_id: str):
    """Delete an artifact."""
    mgr = _get_artifact_manager()
    if mgr.delete_artifact(artifact_id):
        return {"status": "deleted"}
    return JSONResponse({"error": "Artifact not found"}, status_code=404)
