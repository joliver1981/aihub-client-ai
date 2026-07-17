"""
AIHUB-0042 — the self-heal retry can no longer drop workflow_id, and a doomed
workflows.execute is refused locally with the truth.

Live failure (test pack 09): the first POST /api/workflow/run failed (real
cause: "No start node defined"), the self-heal RETRY_WITH_ENRICHMENT strategy
re-extracted ALL params from the step description via a mini-LLM and lost
workflow_id, so every retry POSTed {'input_data': {}} → 400 "workflow_id is
required" — and that secondary error replaced the real one in chat.

Three fixes, tested here:
  1. Executor fail-fast: a required REFERENCE body field still missing after
     filtering+defaults (e.g. workflows.execute's workflow_id) fails BEFORE any
     HTTP call, naming the param ("the request was not sent").
  2. The correction loop MERGES corrected params over the originals (source
     contract — an omitted param survives, a corrected one overwrites).
  3. First-error preservation: if the retry also fails, the surfaced error
     leads with the FIRST real failure (source contract).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "builder_service")):
    if p not in sys.path:
        sys.path.insert(0, p)

from execution.executor import ActionExecutor, ExecutionStatus  # noqa: E402
from builder_agent.actions.definitions import (  # noqa: E402
    FieldSchema, FieldType, PayloadEncoding, RouteMapping,
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def _workflows_execute_route():
    """Mirror of the real workflows.execute route (platform_actions.py)."""
    return RouteMapping(
        method="POST",
        path="/api/workflow/run",
        encoding=PayloadEncoding.JSON,
        description="Execute a workflow",
        input_fields=[
            FieldSchema("workflow_id", FieldType.REFERENCE, required=True,
                        reference_domain="workflows"),
            FieldSchema("input_data", FieldType.DICT, required=False, default={}),
        ],
    )


def _executor_with_client():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"status": "success", "execution_id": 7}
    client = AsyncMock()
    client.post = AsyncMock(return_value=mock_response)
    ex = ActionExecutor(api_key="test-key")
    ex._clients = {"main": client}
    return ex, client


class TestRequiredReferenceFailFast:
    def test_missing_workflow_id_fails_before_http(self):
        """The live retry payload {'input_data': {}} — refused locally, no POST."""
        ex, client = _executor_with_client()
        result = _run(ex._execute_route(
            route=_workflows_execute_route(),
            parameters={"input_data": {}},          # workflow_id gone (the live case)
            capability_id="workflows.execute",
            service="main",
        ))
        assert result.status == ExecutionStatus.FAILED
        assert "workflow_id" in (result.error or "")
        assert "was not sent" in (result.error or "")
        client.post.assert_not_called()

    def test_dropped_undeclared_alias_also_fails_fast(self):
        """Params under wrong names get filtered out — the belt still refuses."""
        ex, client = _executor_with_client()
        result = _run(ex._execute_route(
            route=_workflows_execute_route(),
            parameters={"workflow": 1257, "id": 1257, "inputs": {}},  # all undeclared
            capability_id="workflows.execute",
            service="main",
        ))
        assert result.status == ExecutionStatus.FAILED
        assert "workflow_id" in (result.error or "")
        client.post.assert_not_called()

    def test_present_workflow_id_posts_normally(self):
        ex, client = _executor_with_client()
        result = _run(ex._execute_route(
            route=_workflows_execute_route(),
            parameters={"workflow_id": 1259, "input_data": {}},
            capability_id="workflows.execute",
            service="main",
        ))
        assert result.is_success
        client.post.assert_called_once()
        body = client.post.call_args.kwargs.get("json", {})
        assert body.get("workflow_id") == 1259

    def test_optional_dict_default_still_applies(self):
        """input_data has a default — omitting it must not trip the belt."""
        ex, client = _executor_with_client()
        result = _run(ex._execute_route(
            route=_workflows_execute_route(),
            parameters={"workflow_id": 1259},
            capability_id="workflows.execute",
            service="main",
        ))
        assert result.is_success
        body = client.post.call_args.kwargs.get("json", {})
        assert body.get("input_data") == {}


class TestCorrectionLoopContracts:
    """The correction loop lives inline in the (huge) execute() node; assert the
    two behavioral contracts on the source so a refactor can't silently revert
    them, mirroring the AIHUB-0041 fallback contract test."""

    def _src(self):
        return (Path(_ROOT) / "builder_service" / "graph" / "nodes.py").read_text(encoding="utf-8")

    def test_corrected_params_are_merged_not_replaced(self):
        src = self._src()
        assert "corrected_params = {**parameters, **correction_result.new_parameters}" in src
        assert "corrected_params = correction_result.new_parameters\n" not in src

    def test_first_error_is_preserved_on_failed_retry(self):
        src = self._src()
        assert "_first_error = result.error" in src
        assert "a self-heal retry was attempted and" in src
