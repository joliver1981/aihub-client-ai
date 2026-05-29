"""
Command Center — Artifact Models
===================================
Data models for downloadable file artifacts.
"""

from enum import Enum
from typing import Optional
from datetime import datetime


class ArtifactType(str, Enum):
    EXCEL = "excel"
    PDF = "pdf"
    CSV = "csv"
    JSON = "json"
    IMAGE = "image"
    PPTX = "pptx"
    TEXT = "text"


# Map artifact types to file extensions and MIME types
ARTIFACT_EXTENSIONS = {
    ArtifactType.EXCEL: (".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ArtifactType.PDF: (".pdf", "application/pdf"),
    ArtifactType.CSV: (".csv", "text/csv"),
    ArtifactType.JSON: (".json", "application/json"),
    ArtifactType.IMAGE: (".png", "image/png"),
    ArtifactType.PPTX: (".pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ArtifactType.TEXT: (".txt", "text/plain"),
}


class ArtifactMetadata:
    """Metadata for a stored artifact."""

    def __init__(
        self,
        artifact_id: str,
        name: str,
        artifact_type: ArtifactType,
        size_bytes: int,
        session_id: str,
        created_at: Optional[datetime] = None,
    ):
        self.artifact_id = artifact_id
        self.name = name
        self.artifact_type = artifact_type
        self.size_bytes = size_bytes
        self.session_id = session_id
        self.created_at = created_at or datetime.utcnow()

    @property
    def extension(self) -> str:
        return ARTIFACT_EXTENSIONS.get(self.artifact_type, (".bin", "application/octet-stream"))[0]

    @property
    def mime_type(self) -> str:
        return ARTIFACT_EXTENSIONS.get(self.artifact_type, (".bin", "application/octet-stream"))[1]

    @property
    def size_display(self) -> str:
        if self.size_bytes < 1024:
            return f"{self.size_bytes} B"
        elif self.size_bytes < 1024 * 1024:
            return f"{self.size_bytes / 1024:.1f} KB"
        else:
            return f"{self.size_bytes / (1024 * 1024):.1f} MB"

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "name": self.name,
            "artifact_type": self.artifact_type.value,
            "size": self.size_display,
            "size_bytes": self.size_bytes,
            "mime_type": self.mime_type,
            "download_url": f"/api/artifacts/{self.artifact_id}/download",
            "created_at": self.created_at.isoformat(),
        }

    def to_content_block(self) -> dict:
        """Create a rich content block for the chat UI."""
        return {
            "type": "artifact",
            "name": self.name,
            "artifactType": self.artifact_type.value,
            "size": self.size_display,
            "artifact_id": self.artifact_id,
            "download_url": f"/api/artifacts/{self.artifact_id}/download",
        }

    def persist_dict(self) -> dict:
        """Full, reload-able representation written to the on-disk sidecar so
        metadata survives restarts and is shared across ArtifactManager
        instances (the in-memory cache alone is lost on restart and not
        shared between separately-imported instances)."""
        return {
            "artifact_id": self.artifact_id,
            "name": self.name,
            "artifact_type": self.artifact_type.value,
            "size_bytes": self.size_bytes,
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_persist(cls, d: dict) -> "ArtifactMetadata":
        created = d.get("created_at")
        try:
            created_dt = datetime.fromisoformat(created) if created else None
        except (TypeError, ValueError):
            created_dt = None
        return cls(
            artifact_id=d["artifact_id"],
            name=d.get("name", d["artifact_id"]),
            artifact_type=ArtifactType(d.get("artifact_type", "text")),
            size_bytes=int(d.get("size_bytes", 0) or 0),
            session_id=d.get("session_id", ""),
            created_at=created_dt,
        )
