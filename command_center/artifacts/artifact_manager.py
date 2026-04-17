"""
Command Center — Artifact Manager
=====================================
Create, store, and serve downloadable file artifacts.
"""

import logging
import os
import uuid
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from command_center.artifacts.artifact_models import ArtifactMetadata, ArtifactType

logger = logging.getLogger(__name__)

# Default artifact TTL: 24 hours
DEFAULT_TTL_SECONDS = 86400


class ArtifactManager:
    """Manages creation, storage, and retrieval of file artifacts."""

    def __init__(self, storage_dir: str, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self._metadata: Dict[str, ArtifactMetadata] = {}

    def create(
        self,
        name: str,
        artifact_type: ArtifactType,
        content_bytes: bytes,
        session_id: str,
    ) -> ArtifactMetadata:
        """
        Store an artifact and return its metadata.

        Args:
            name: Display name (e.g., "Q1_Sales_Report.xlsx")
            artifact_type: Type of artifact (excel, pdf, csv, etc.)
            content_bytes: Raw file bytes
            session_id: Session that created this artifact
        """
        artifact_id = str(uuid.uuid4())[:12]

        # Store in session-scoped directory
        session_dir = self.storage_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        # Write file
        ext = ArtifactMetadata(artifact_id, name, artifact_type, 0, session_id).extension
        if not name.endswith(ext):
            name = name + ext
        file_path = session_dir / f"{artifact_id}{ext}"
        file_path.write_bytes(content_bytes)

        # Create metadata
        metadata = ArtifactMetadata(
            artifact_id=artifact_id,
            name=name,
            artifact_type=artifact_type,
            size_bytes=len(content_bytes),
            session_id=session_id,
        )
        self._metadata[artifact_id] = metadata

        logger.info(f"Created artifact: {name} ({metadata.size_display}) id={artifact_id}")
        return metadata

    def get_metadata(self, artifact_id: str) -> Optional[ArtifactMetadata]:
        """Get metadata for an artifact."""
        return self._metadata.get(artifact_id)

    def get_file_path(self, artifact_id: str) -> Optional[Path]:
        """Get the filesystem path for an artifact."""
        meta = self._metadata.get(artifact_id)
        if not meta:
            return None

        session_dir = self.storage_dir / meta.session_id
        ext = meta.extension
        file_path = session_dir / f"{artifact_id}{ext}"

        if file_path.exists():
            return file_path
        return None

    def list_artifacts(self, session_id: str) -> List[Dict[str, Any]]:
        """List all artifacts for a session."""
        return [
            meta.to_dict()
            for meta in self._metadata.values()
            if meta.session_id == session_id
        ]

    def delete_artifact(self, artifact_id: str) -> bool:
        """Delete an artifact."""
        meta = self._metadata.pop(artifact_id, None)
        if not meta:
            return False

        file_path = self.get_file_path(artifact_id)
        if file_path and file_path.exists():
            file_path.unlink()

        logger.info(f"Deleted artifact: {artifact_id}")
        return True

    def cleanup_expired(self):
        """Remove artifacts older than TTL."""
        now = time.time()
        expired = []
        for aid, meta in self._metadata.items():
            age = now - meta.created_at.timestamp()
            if age > self.ttl_seconds:
                expired.append(aid)

        for aid in expired:
            self.delete_artifact(aid)

        if expired:
            logger.info(f"Cleaned up {len(expired)} expired artifacts")

    def create_from_blocks(
        self,
        blocks: List[Dict[str, Any]],
        artifact_type: ArtifactType,
        name: str,
        session_id: str,
    ) -> Optional[ArtifactMetadata]:
        """
        Create an artifact from content blocks.
        Dispatches to the appropriate renderer.
        """
        content_bytes = None

        if artifact_type == ArtifactType.EXCEL:
            from command_center.renderers.excel_renderer import render_blocks_to_excel
            content_bytes = render_blocks_to_excel(blocks)

        elif artifact_type == ArtifactType.PDF:
            from command_center.renderers.pdf_renderer import render_blocks_to_pdf
            content_bytes = render_blocks_to_pdf(blocks, title=name)

        elif artifact_type == ArtifactType.CSV:
            from command_center.renderers.file_renderer import render_blocks_to_csv
            content_bytes = render_blocks_to_csv(blocks)

        elif artifact_type == ArtifactType.JSON:
            from command_center.renderers.file_renderer import render_to_json
            content_bytes = render_to_json(blocks)

        if content_bytes is None:
            logger.warning(f"No content generated for artifact type {artifact_type}")
            return None

        return self.create(name, artifact_type, content_bytes, session_id)
