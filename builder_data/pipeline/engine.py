"""
Pipeline Engine — orchestrates execution of a full pipeline DAG.
Resolves step dependencies, executes in topological order, and
passes DataFrames between steps.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Tuple

import pandas as pd

from pipeline.models import PipelineDefinition, PipelineResult, StepDefinition, StepResult, StepType
from pipeline.steps.base import BaseStep
from pipeline.steps.source import SourceStep
from pipeline.steps.transform import TransformStep
from pipeline.steps.filter import FilterStep
from pipeline.steps.compare import CompareStep
from pipeline.steps.scrub import ScrubStep
from pipeline.steps.destination import DestinationStep

logger = logging.getLogger(__name__)


class PipelineEngine:
    """
    Executes a PipelineDefinition by running steps in dependency order.
    Each step receives DataFrames from its upstream dependencies and
    produces one output DataFrame.
    """

    def __init__(self, connection_bridge):
        self.connection_bridge = connection_bridge
        self._step_handlers: Dict[StepType, BaseStep] = {
            StepType.SOURCE: SourceStep(connection_bridge),
            StepType.TRANSFORM: TransformStep(),
            StepType.FILTER: FilterStep(),
            StepType.COMPARE: CompareStep(),
            StepType.SCRUB: ScrubStep(),
            StepType.DESTINATION: DestinationStep(connection_bridge),
        }

    async def execute(
        self,
        pipeline: PipelineDefinition,
        progress_callback: Optional[Callable] = None,
        max_rows: Optional[int] = None,
    ) -> PipelineResult:
        """
        Execute the full pipeline.

        Args:
            pipeline: The pipeline definition to execute.
            progress_callback: Optional async callback(step_id, status, step_result)
                              called after each step completes.
            max_rows: If set, limit source reads to this many rows (for preview).

        Returns:
            PipelineResult with status and per-step results.
        """
        # Validate first
        errors = pipeline.validate()
        if errors:
            return PipelineResult(
                pipeline_id=pipeline.pipeline_id,
                status="failed",
                error=f"Validation errors: {'; '.join(errors)}",
            )

        result = PipelineResult(
            pipeline_id=pipeline.pipeline_id,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        overall_start = time.time()
        step_outputs: Dict[str, pd.DataFrame] = {}
        execution_order = pipeline.get_execution_order()

        logger.info(
            f"Executing pipeline '{pipeline.name}' ({pipeline.pipeline_id}): "
            f"{len(execution_order)} steps"
        )

        all_succeeded = True

        for step in execution_order:
            logger.info(f"  ▶ Step '{step.step_id}' ({step.step_type.value}): {step.name}")

            # Inject max_rows into source steps for preview mode
            if max_rows and step.step_type == StepType.SOURCE:
                step_config = {**step.config}
                if "max_rows" not in step_config:
                    step_config["max_rows"] = max_rows
                step = StepDefinition(
                    step_id=step.step_id,
                    step_type=step.step_type,
                    name=step.name,
                    description=step.description,
                    config=step_config,
                    depends_on=step.depends_on,
                    enabled=step.enabled,
                )

            # Gather input frames from dependencies
            input_frames: Dict[str, pd.DataFrame] = {}
            missing_dep = False
            for dep_id in step.depends_on:
                if dep_id in step_outputs:
                    input_frames[dep_id] = step_outputs[dep_id]
                else:
                    # Dependency failed — skip this step
                    logger.warning(
                        f"  ⚠ Step '{step.step_id}' skipped: dependency '{dep_id}' not available"
                    )
                    step_result = StepResult(
                        step_id=step.step_id,
                        status="skipped",
                        error=f"Dependency '{dep_id}' not available",
                    )
                    result.step_results[step.step_id] = step_result
                    missing_dep = True
                    all_succeeded = False
                    break

            if missing_dep:
                if progress_callback:
                    await progress_callback(step.step_id, "skipped", step_result)
                continue

            # Execute the step
            handler = self._step_handlers.get(step.step_type)
            if handler is None:
                step_result = StepResult(
                    step_id=step.step_id,
                    status="failed",
                    error=f"No handler for step type: {step.step_type.value}",
                )
                result.step_results[step.step_id] = step_result
                all_succeeded = False
                if progress_callback:
                    await progress_callback(step.step_id, "failed", step_result)
                continue

            try:
                output_df, step_result = await handler.execute(step, input_frames)
                step_outputs[step.step_id] = output_df
                result.step_results[step.step_id] = step_result

                if step_result.status == "failed":
                    all_succeeded = False

                logger.info(
                    f"  ✓ Step '{step.step_id}': {step_result.status} "
                    f"({step_result.row_count} rows, {step_result.duration_ms}ms)"
                )

            except Exception as e:
                logger.error(f"  ✗ Step '{step.step_id}' exception: {e}")
                step_result = StepResult(
                    step_id=step.step_id,
                    status="failed",
                    error=str(e),
                )
                result.step_results[step.step_id] = step_result
                all_succeeded = False

            if progress_callback:
                await progress_callback(step.step_id, step_result.status, step_result)

        # Finalize
        result.completed_at = datetime.now(timezone.utc).isoformat()
        result.total_duration_ms = int((time.time() - overall_start) * 1000)

        if all_succeeded:
            result.status = "success"
        elif any(sr.status == "success" for sr in result.step_results.values()):
            result.status = "partial"
        else:
            result.status = "failed"

        logger.info(
            f"Pipeline '{pipeline.name}' {result.status}: "
            f"{result.total_duration_ms}ms total"
        )

        return result

    async def preview(
        self,
        pipeline: PipelineDefinition,
        max_rows: int = 50,
    ) -> PipelineResult:
        """Execute pipeline with a row limit for quick preview."""
        return await self.execute(pipeline, max_rows=max_rows)
