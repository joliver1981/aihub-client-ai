"""
Builder Data Service — Pipeline Routes
=========================================
CRUD and execution endpoints for data pipelines.
"""

import json
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
from sse_starlette.sse import EventSourceResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipelines")

pipeline_executor = None


def init_pipeline_routes(_pipeline_executor):
    global pipeline_executor
    pipeline_executor = _pipeline_executor


class PipelineCreateRequest(BaseModel):
    pipeline_id: Optional[str] = None
    name: str
    description: str = ""
    steps: List[Dict[str, Any]]
    metadata: Dict[str, Any] = {}


class PipelineExecuteRequest(BaseModel):
    dry_run: bool = False
    max_rows: Optional[int] = None


@router.post("/")
async def create_pipeline(request: PipelineCreateRequest):
    """Create and validate a pipeline definition."""
    if pipeline_executor is None:
        raise HTTPException(status_code=503, detail="Pipeline executor not initialized")

    try:
        pipeline = pipeline_executor.create_pipeline(request.model_dump())
        return {"pipeline": pipeline.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/")
async def list_pipelines():
    """List all pipeline definitions."""
    if pipeline_executor is None:
        raise HTTPException(status_code=503, detail="Pipeline executor not initialized")

    return {"pipelines": pipeline_executor.list_pipelines()}


@router.get("/{pipeline_id}")
async def get_pipeline(pipeline_id: str):
    """Get a pipeline definition by ID."""
    if pipeline_executor is None:
        raise HTTPException(status_code=503, detail="Pipeline executor not initialized")

    pipeline = pipeline_executor.get_pipeline(pipeline_id)
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return {"pipeline": pipeline.to_dict()}


@router.post("/{pipeline_id}/execute")
async def execute_pipeline(pipeline_id: str, request: PipelineExecuteRequest):
    """
    Execute a pipeline with SSE progress streaming.
    Each step completion emits a progress event.
    """
    if pipeline_executor is None:
        raise HTTPException(status_code=503, detail="Pipeline executor not initialized")

    pipeline = pipeline_executor.get_pipeline(pipeline_id)
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    async def execution_generator():
        try:
            yield {
                "event": "status",
                "data": json.dumps({
                    "phase": "executing",
                    "label": f"Executing pipeline '{pipeline.name}'...",
                    "pipeline_id": pipeline_id,
                }),
            }

            async def progress_callback(step_id, status, step_result):
                # This can't yield directly, but we track progress in the result
                pass

            result = await pipeline_executor.execute_pipeline(
                pipeline_id,
                progress_callback=progress_callback,
                max_rows=request.max_rows,
            )

            # Emit per-step results
            for step_id, step_result in result.step_results.items():
                yield {
                    "event": "step_done",
                    "data": json.dumps({
                        "step_id": step_id,
                        "status": step_result.status,
                        "row_count": step_result.row_count,
                        "duration_ms": step_result.duration_ms,
                        "error": step_result.error,
                    }),
                }

            # Emit final result
            yield {
                "event": "pipeline_result",
                "data": json.dumps(result.to_dict()),
            }

            yield {
                "event": "done",
                "data": json.dumps({"pipeline_id": pipeline_id, "status": result.status}),
            }

        except Exception as e:
            logger.error(f"Pipeline execution error: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({"message": str(e)}),
            }
            yield {
                "event": "done",
                "data": json.dumps({"pipeline_id": pipeline_id, "status": "failed"}),
            }

    return EventSourceResponse(execution_generator())


@router.post("/{pipeline_id}/preview")
async def preview_pipeline(pipeline_id: str):
    """Execute pipeline with row limit and return preview of each step."""
    if pipeline_executor is None:
        raise HTTPException(status_code=503, detail="Pipeline executor not initialized")

    try:
        result = await pipeline_executor.preview_pipeline(pipeline_id)
        return {"result": result.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{pipeline_id}/results")
async def get_pipeline_results(pipeline_id: str):
    """Get results from the last pipeline execution."""
    if pipeline_executor is None:
        raise HTTPException(status_code=503, detail="Pipeline executor not initialized")

    result = pipeline_executor.get_result(pipeline_id)
    if not result:
        raise HTTPException(status_code=404, detail="No results found for this pipeline")
    return {"result": result.to_dict()}


@router.delete("/{pipeline_id}")
async def delete_pipeline(pipeline_id: str):
    """Delete a pipeline definition."""
    if pipeline_executor is None:
        raise HTTPException(status_code=503, detail="Pipeline executor not initialized")

    if pipeline_executor.delete_pipeline(pipeline_id):
        return {"deleted": True}
    raise HTTPException(status_code=404, detail="Pipeline not found")
