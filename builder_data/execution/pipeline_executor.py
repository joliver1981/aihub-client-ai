"""
Pipeline Executor — thin wrapper around PipelineEngine for the service layer.
Manages pipeline storage (in-memory) and execution history.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from pipeline.models import PipelineDefinition, PipelineResult
from pipeline.engine import PipelineEngine
from execution.connection_bridge import ConnectionBridge

logger = logging.getLogger(__name__)


class PipelineExecutor:
    """
    Manages pipeline definitions and their execution via PipelineEngine.
    Stores pipelines and results in-memory (replace with DB for production).
    """

    def __init__(self, connection_bridge: ConnectionBridge):
        self.engine = PipelineEngine(connection_bridge)
        self._pipelines: Dict[str, PipelineDefinition] = {}
        self._results: Dict[str, PipelineResult] = {}

    # ─── Pipeline CRUD ──────────────────────────────────────────────────

    def create_pipeline(self, data: Dict[str, Any]) -> PipelineDefinition:
        """Create and validate a pipeline definition."""
        if "pipeline_id" not in data or not data["pipeline_id"]:
            data["pipeline_id"] = f"pipe_{uuid.uuid4().hex[:8]}"

        data["created_at"] = datetime.now(timezone.utc).isoformat()
        data["updated_at"] = data["created_at"]

        pipeline = PipelineDefinition.from_dict(data)

        errors = pipeline.validate()
        if errors:
            raise ValueError(f"Pipeline validation failed: {'; '.join(errors)}")

        self._pipelines[pipeline.pipeline_id] = pipeline
        logger.info(f"Created pipeline '{pipeline.name}' ({pipeline.pipeline_id})")
        return pipeline

    def get_pipeline(self, pipeline_id: str) -> Optional[PipelineDefinition]:
        return self._pipelines.get(pipeline_id)

    def list_pipelines(self) -> List[Dict[str, Any]]:
        return [p.to_dict() for p in self._pipelines.values()]

    def delete_pipeline(self, pipeline_id: str) -> bool:
        if pipeline_id in self._pipelines:
            del self._pipelines[pipeline_id]
            self._results.pop(pipeline_id, None)
            logger.info(f"Deleted pipeline {pipeline_id}")
            return True
        return False

    # ─── Execution ──────────────────────────────────────────────────────

    async def execute_pipeline(
        self,
        pipeline_id: str,
        progress_callback: Optional[Callable] = None,
        max_rows: Optional[int] = None,
    ) -> PipelineResult:
        """Execute a pipeline and store the result."""
        pipeline = self._pipelines.get(pipeline_id)
        if not pipeline:
            raise ValueError(f"Pipeline '{pipeline_id}' not found")

        result = await self.engine.execute(
            pipeline,
            progress_callback=progress_callback,
            max_rows=max_rows,
        )

        self._results[pipeline_id] = result
        return result

    async def preview_pipeline(self, pipeline_id: str, max_rows: int = 50) -> PipelineResult:
        """Execute a pipeline in preview mode with row limits."""
        return await self.execute_pipeline(pipeline_id, max_rows=max_rows)

    def get_result(self, pipeline_id: str) -> Optional[PipelineResult]:
        return self._results.get(pipeline_id)
