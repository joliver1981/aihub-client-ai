"""Shared fixtures for tests_v2/workflow.

These tests drive the live AI Hub workflow execution engine over HTTP on
localhost:5001. They:
  * build minimal workflow JSON in memory,
  * save it via POST /save/workflow (which returns a workflow_id),
  * trigger execution via POST /api/workflow/run,
  * poll /api/workflow/executions/{id} until terminal,
  * fetch /steps and /variables, and
  * clean up at module teardown.

All test workflow names start with ``TEST_v2_`` so the cleanup fixture can
find and delete them without disturbing real data.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import pytest
import requests

# Some node outputs (notably AI Action) can contain unicode that surprises
# Windows' default cp1252 stdout. Reconfigure once at import time.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover - older Python or non-tty
    pass


MAIN_BASE_URL = os.getenv("AIHUB_MAIN_URL", "http://localhost:5001")
API_KEY = os.getenv("AIHUB_API_KEY", "DB27D555-03A8-446E-9C23-8DAAA95EAD21")
TEST_PREFIX = "TEST_v2_"

# Terminal statuses the executor may report. Compared case-insensitively.
TERMINAL_STATUSES = {
    "completed",
    "failed",
    "error",
    "errored",
    "cancelled",
    "canceled",
    "succeeded",
    "success",
}


# ---------------------------------------------------------------------------
# Service readiness
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def services_ready() -> bool:
    """Skip the whole module if the main app is not reachable on :5001."""
    try:
        r = requests.get(
            f"{MAIN_BASE_URL}/get/workflows",
            headers={"X-API-Key": API_KEY},
            timeout=5,
        )
        if r.status_code != 200:
            pytest.skip(
                f"Main app at {MAIN_BASE_URL} not responding to /get/workflows "
                f"(got {r.status_code}). Skipping workflow suite."
            )
    except Exception as e:
        pytest.skip(f"Main app at {MAIN_BASE_URL} not reachable: {e}")
    return True


# ---------------------------------------------------------------------------
# HTTP session with API key
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def api_session(services_ready) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "X-API-Key": API_KEY,
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    })
    return s


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------

def _node(node_id: str, node_type: str, config: Optional[Dict] = None,
          label: Optional[str] = None, is_start: bool = False,
          left: int = 200, top: int = 200) -> Dict:
    """Build a workflow node dict in the shape the engine expects."""
    return {
        "id": node_id,
        "type": node_type,
        "label": label or node_type,
        "isStart": is_start,
        "position": {"left": f"{left}px", "top": f"{top}px"},
        "config": config or {},
    }


def _conn(source: str, target: str, conn_type: str = "pass") -> Dict:
    return {
        "source": source,
        "sourceAnchor": "Right",
        "target": target,
        "targetAnchor": "Left",
        "type": conn_type,
    }


@pytest.fixture
def make_node():
    """Expose the node builder as a fixture for tests."""
    return _node


@pytest.fixture
def make_conn():
    """Expose the connection builder as a fixture for tests."""
    return _conn


@pytest.fixture
def make_workflow():
    """Return a builder that yields a workflow dict.

    Usage:
        wf = make_workflow(nodes=[...], connections=[...], variables=[...])
    """
    def _build(nodes: List[Dict],
               connections: Optional[List[Dict]] = None,
               variables: Optional[Dict] = None) -> Dict:
        # The engine expects `variables` to be a dict (it calls .items()),
        # not a list. See workflow 285 for the canonical empty shape.
        return {
            "nodes": nodes,
            "connections": connections or [],
            "variables": variables if variables is not None else {},
        }
    return _build


# ---------------------------------------------------------------------------
# Save / Run / Poll
# ---------------------------------------------------------------------------

class _WorkflowTracker:
    """Tracks workflow_ids created during a module for cleanup."""
    def __init__(self):
        self.ids: List[int] = []

    def add(self, wid: int) -> None:
        if wid is not None and wid not in self.ids:
            self.ids.append(wid)


@pytest.fixture(scope="module")
def cleanup_workflows(api_session) -> _WorkflowTracker:
    """Module-scoped tracker. After the module finishes, deletes every
    workflow whose name starts with TEST_v2_ that was created during the
    module (plus any leftover TEST_v2_ workflows from prior aborted runs
    discovered via /get/workflows). Cleanup failures are swallowed so a
    flaky teardown doesn't mask test results.
    """
    tracker = _WorkflowTracker()
    yield tracker

    # Pull current list to also catch ones we lost track of
    try:
        r = api_session.get(f"{MAIN_BASE_URL}/get/workflows", timeout=10)
        if r.status_code == 200:
            payload = r.json()
            if isinstance(payload, str):
                payload = json.loads(payload)
            for row in payload or []:
                name = (row.get("workflow_name") or "")
                if name.startswith(TEST_PREFIX):
                    tracker.add(row.get("id"))
    except Exception:
        pass

    for wid in tracker.ids:
        try:
            api_session.delete(
                f"{MAIN_BASE_URL}/delete/workflow/{wid}",
                timeout=10,
            )
        except Exception:
            pass


@pytest.fixture
def save_workflow(api_session, cleanup_workflows, make_workflow):
    """Save a workflow via /save/workflow. Returns the workflow_id.

    Names are auto-prefixed with TEST_v2_ if the caller didn't already do it,
    and a short uuid suffix is added to avoid collisions across parameterized
    cases.
    """
    def _save(name: str,
              nodes: List[Dict],
              connections: Optional[List[Dict]] = None,
              variables: Optional[Dict] = None) -> int:
        if not name.startswith(TEST_PREFIX):
            name = TEST_PREFIX + name
        # Add short uuid so reruns/parametrize don't collide
        unique_name = f"{name}_{uuid.uuid4().hex[:8]}"
        wf = make_workflow(nodes, connections, variables)
        payload = {
            "filename": f"{unique_name}.json",
            "workflow": wf,
        }
        r = api_session.post(
            f"{MAIN_BASE_URL}/save/workflow",
            json=payload,
            timeout=15,
        )
        assert r.status_code == 200, (
            f"save_workflow failed: {r.status_code} {r.text[:400]}"
        )
        body = r.json()
        wid = body.get("workflow_id") or body.get("database_version")
        assert wid, f"No workflow_id in save response: {body}"
        cleanup_workflows.add(int(wid))
        return int(wid)
    return _save


def _poll_execution(api_session: requests.Session,
                    exec_id: str,
                    timeout: int) -> Dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = api_session.get(
            f"{MAIN_BASE_URL}/api/workflow/executions/{exec_id}",
            timeout=10,
        )
        if r.status_code == 200:
            last = r.json()
            status = (last.get("status")
                      or last.get("execution", {}).get("status")
                      or "").lower()
            if status in TERMINAL_STATUSES:
                return last
        time.sleep(1)
    if last is None:
        raise TimeoutError(
            f"Execution {exec_id} never returned 200 within {timeout}s"
        )
    return last


@pytest.fixture
def run_workflow(api_session):
    """Trigger a workflow run and poll until terminal.

    Returns a dict with keys:
        execution_id, status, execution, steps, variables, start_response
    """
    def _run(workflow_id: int, timeout: int = 60) -> Dict[str, Any]:
        r = api_session.post(
            f"{MAIN_BASE_URL}/api/workflow/run",
            json={"workflow_id": int(workflow_id), "initiator": "tests_v2"},
            timeout=30,
        )
        start_body: Any
        try:
            start_body = r.json()
        except Exception:
            start_body = {"raw": r.text}

        # Bubble up the start failure so the caller can assert on it
        if r.status_code != 200:
            return {
                "execution_id": None,
                "status": "start_failed",
                "start_status_code": r.status_code,
                "start_response": start_body,
                "execution": None,
                "steps": [],
                "variables": {},
            }

        exec_id = start_body.get("execution_id")
        if not exec_id:
            return {
                "execution_id": None,
                "status": "no_execution_id",
                "start_status_code": r.status_code,
                "start_response": start_body,
                "execution": None,
                "steps": [],
                "variables": {},
            }

        final = _poll_execution(api_session, exec_id, timeout)
        # Fetch steps
        steps: List[Dict] = []
        sr = api_session.get(
            f"{MAIN_BASE_URL}/api/workflow/executions/{exec_id}/steps",
            timeout=10,
        )
        if sr.status_code == 200:
            sj = sr.json()
            steps = sj.get("steps") or []
        # Fetch variables (best-effort)
        variables: Dict[str, Any] = {}
        vr = api_session.get(
            f"{MAIN_BASE_URL}/api/workflow/executions/{exec_id}/variables",
            timeout=10,
        )
        if vr.status_code == 200:
            try:
                vj = vr.json()
                raw_vars = vj.get("variables") or vj.get("data") or vj
                if isinstance(raw_vars, list):
                    for v in raw_vars:
                        if isinstance(v, dict) and "variable_name" in v:
                            variables[v["variable_name"]] = v.get("variable_value")
                elif isinstance(raw_vars, dict):
                    variables = raw_vars
            except Exception:
                pass

        status = (final.get("status")
                  or final.get("execution", {}).get("status")
                  or "unknown").lower()
        return {
            "execution_id": exec_id,
            "status": status,
            "start_status_code": r.status_code,
            "start_response": start_body,
            "execution": final,
            "steps": steps,
            "variables": variables,
        }
    return _run


# ---------------------------------------------------------------------------
# Misc utilities for individual tests
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_text_file() -> str:
    """Absolute path to a known-existing sample text file."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "tests", "fixtures", "sample_invoice.txt"
    )
    if not os.path.exists(path):
        pytest.skip(f"Sample fixture missing: {path}")
    return path


def _step_label(step: Dict) -> str:
    """Pick a usable label out of a step record.

    The /steps endpoint returns ``node_name`` (the node label set in the
    workflow JSON). Older variants use ``node_label`` / ``step_name``.
    """
    return (step.get("node_name")
            or step.get("node_label")
            or step.get("step_name")
            or step.get("name")
            or "?")


@pytest.fixture
def step_status_by_label(run_workflow):
    """Return helper that maps step labels -> status from a run result."""
    def _map(result: Dict) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for s in result.get("steps") or []:
            out[_step_label(s)] = s.get("status", "?")
        return out
    return _map


@pytest.fixture
def MAIN_URL() -> str:
    return MAIN_BASE_URL


@pytest.fixture
def TEST_API_KEY() -> str:
    return API_KEY
