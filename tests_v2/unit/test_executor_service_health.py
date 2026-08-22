"""Executor service /health contract (app_executor_service.py).

Regression for 2026-08-21: the route built its payload with
``len(workflow_engine.active_executions)`` while WorkflowExecutionEngine only
defined ``_active_executions``, so once the engine was initialised every
/health call on the live executor (127.0.0.1:5061) raised AttributeError and
answered HTTP 500 with Flask's generic error page. Now:

* WorkflowExecutionEngine exposes a read-only ``active_executions`` snapshot
  (a copy of ``_active_executions``; no setter);
* /health reads it defensively (property, then the private dict) and
  degrades any unreadable section to ``None`` + an ``errors`` entry instead
  of failing the probe - ``status`` stays the liveness flag that
  WorkflowAPIClient.is_available() keys on;
* /api/workflow/executions/active iterates the snapshot, not the live dict.

The executor module is imported hermetically: the engine, recovery service,
email dispatcher and telemetry are stubbed through sys.modules (monkeypatch,
so nothing leaks into other tests), the DB connection string is faked and the
module's log is redirected to tmp_path. No DB / network access.
"""
from __future__ import annotations

import importlib
import logging
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

EXPECTED_KEYS = {"status", "service", "timestamp", "workflow_engine",
                 "active_executions", "email_dispatcher"}
FAKE_CONN = "DRIVER={SQL Server};SERVER=stub;DATABASE=stub;UID=u;PWD=p"


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------
class StubEngine:
    """Mirrors the surface of the real engine the executor routes use."""

    def __init__(self, connection_string):
        self.connection_string = connection_string
        self._active_executions = {}

    @property
    def active_executions(self):
        return dict(self._active_executions)


class LegacyEngine:
    """An engine that predates the property - only the private dict."""

    def __init__(self):
        self._active_executions = {"legacy-1": {"status": "Running"}}


class BrokenEngine:
    """Exposes neither view: the property raises and there is no dict."""

    @property
    def active_executions(self):
        raise RuntimeError("engine view exploded")


class StubDispatcher:
    def __init__(self, running=True, stats=None, stats_error=None):
        self._running = running
        self._stats = {"polls": 3, "processed": 1} if stats is None else stats
        self._stats_error = stats_error

    def is_running(self):
        return self._running

    def get_stats(self):
        if self._stats_error is not None:
            raise self._stats_error
        return self._stats


# ---------------------------------------------------------------------------
# Fixture: hermetic import of app_executor_service
# ---------------------------------------------------------------------------
@pytest.fixture
def executor(monkeypatch, tmp_path):
    """Import app_executor_service with its collaborators stubbed; yield it."""
    # Keep the import-time log handler + rotate_logs_on_startup away from the
    # live logs/app_workflow_executor_log.txt (env is read at import).
    monkeypatch.setenv("APP_WORKFLOW_EXECUTOR_LOG", str(tmp_path / "executor_unit_log.txt"))

    engine_mod = types.ModuleType("workflow_execution")
    engine_mod.WorkflowExecutionEngine = StubEngine
    monkeypatch.setitem(sys.modules, "workflow_execution", engine_mod)

    recovery_mod = types.ModuleType("workflow_recovery_service")
    recovery_mod.initialize_recovery_service = lambda app, engine: None
    monkeypatch.setitem(sys.modules, "workflow_recovery_service", recovery_mod)

    dispatcher_mod = types.ModuleType("email_agent_dispatcher")
    dispatcher_mod.EmailAgentDispatcher = StubDispatcher
    dispatcher_mod.get_dispatcher = lambda flask_app=None: None
    monkeypatch.setitem(sys.modules, "email_agent_dispatcher", dispatcher_mod)

    telemetry_mod = types.ModuleType("telemetry")
    telemetry_mod.capture_exception = lambda *a, **k: None
    telemetry_mod.add_breadcrumb = lambda *a, **k: None
    telemetry_mod.track_workflow_executed = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "telemetry", telemetry_mod)

    import CommonUtils
    import config as cfg
    monkeypatch.setattr(CommonUtils, "get_db_connection_string", lambda: FAKE_CONN)
    monkeypatch.setattr(cfg, "EMAIL_DISPATCHER_ENABLED", False, raising=False)

    log = logging.getLogger("AppWorkflowExecutor")
    handlers_before = list(log.handlers)
    monkeypatch.delitem(sys.modules, "app_executor_service", raising=False)
    module = importlib.import_module("app_executor_service")
    try:
        yield module
    finally:
        sys.modules.pop("app_executor_service", None)
        for h in list(log.handlers):
            if h not in handlers_before:
                log.removeHandler(h)
                h.close()


def _get(executor, path):
    return executor.app.test_client().get(path)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------
def test_health_returns_200_json_with_expected_keys(executor):
    # The module-level init built a (stub) engine from the faked conn string.
    assert isinstance(executor.workflow_engine, StubEngine)
    executor.workflow_engine._active_executions.update({
        "exec-1": {"status": "Running"},
        "exec-2": {"status": "Paused"},
    })

    r = _get(executor, "/health")

    assert r.status_code == 200
    assert r.is_json
    body = r.get_json()
    assert EXPECTED_KEYS <= set(body), f"missing keys: {EXPECTED_KEYS - set(body)}"
    assert body["status"] == "healthy"
    assert body["service"] == "workflow-executor"
    assert body["workflow_engine"] is True
    assert body["active_executions"] == 2
    assert body["email_dispatcher"] == {"enabled": False, "running": False, "stats": None}
    assert "errors" not in body


def test_health_when_engine_not_initialised(executor):
    executor.workflow_engine = None

    r = _get(executor, "/health")

    assert r.status_code == 200
    body = r.get_json()
    assert body["workflow_engine"] is False
    assert body["active_executions"] == 0
    assert "errors" not in body


def test_health_accepts_engine_with_only_the_private_dict(executor):
    executor.workflow_engine = LegacyEngine()

    r = _get(executor, "/health")

    assert r.status_code == 200
    body = r.get_json()
    assert body["workflow_engine"] is True
    assert body["active_executions"] == 1
    assert "errors" not in body


def test_health_degrades_to_200_when_engine_view_is_unreadable(executor):
    """The 2026-08-21 failure shape: engine present, its view unreadable.
    Must be a 200 with partial info, never a 500."""
    executor.workflow_engine = BrokenEngine()

    r = _get(executor, "/health")

    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "healthy"          # liveness flag untouched
    assert body["workflow_engine"] is True
    assert body["active_executions"] is None
    assert "active_executions" in body["errors"]


def test_health_reports_dispatcher_state(executor):
    executor.email_dispatcher = StubDispatcher(running=True, stats={"polls": 3})

    body = _get(executor, "/health").get_json()

    assert body["email_dispatcher"] == {"enabled": True, "running": True, "stats": {"polls": 3}}
    assert "errors" not in body


def test_health_degrades_when_dispatcher_stats_raise(executor):
    executor.email_dispatcher = StubDispatcher(
        running=True, stats_error=RuntimeError("stats exploded"))

    r = _get(executor, "/health")

    assert r.status_code == 200
    body = r.get_json()
    assert body["email_dispatcher"]["enabled"] is True
    assert body["email_dispatcher"]["running"] is True
    assert body["email_dispatcher"]["stats"] is None
    assert "stats exploded" in body["errors"]["email_dispatcher.stats"]


def test_health_survives_non_json_stats(executor):
    """A stat value jsonify() cannot serialise must not turn into a 500."""
    executor.email_dispatcher = StubDispatcher(stats={"last_poll": object()})

    r = _get(executor, "/health")

    assert r.status_code == 200
    stats = r.get_json()["email_dispatcher"]["stats"]
    assert isinstance(stats["last_poll"], str)


# ---------------------------------------------------------------------------
# /api/workflow/executions/active (same snapshot helper)
# ---------------------------------------------------------------------------
def test_active_executions_route_lists_from_snapshot(executor):
    executor.workflow_engine._active_executions["exec-1"] = {
        "workflow_id": 7, "workflow_name": "WF", "status": "Running",
        "current_node": "n1", "started_at": "2026-08-21T00:00:00", "paused": False,
    }

    r = _get(executor, "/api/workflow/executions/active")

    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "success"
    assert body["count"] == 1
    assert body["active_executions"][0] == {
        "execution_id": "exec-1", "workflow_id": 7, "workflow_name": "WF",
        "status": "Running", "current_node": "n1",
        "started_at": "2026-08-21T00:00:00", "paused": False,
    }


def test_active_executions_route_errors_without_engine(executor):
    executor.workflow_engine = None

    r = _get(executor, "/api/workflow/executions/active")

    assert r.status_code == 500
    assert r.get_json()["status"] == "error"


# ---------------------------------------------------------------------------
# The real engine's property (guarded: the engine module pulls the full stack)
# ---------------------------------------------------------------------------
def test_real_engine_exposes_read_only_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKFLOW_EXECUTION_LOG", str(tmp_path / "engine_unit_log.txt"))
    leaked = sys.modules.get("workflow_execution")      # a types.ModuleType stub has no __file__
    assert leaked is None or hasattr(leaked, "__file__"), "engine stub leaked from the fixture"
    try:
        we = importlib.import_module("workflow_execution")
    except Exception as e:  # pragma: no cover - env-dependent
        pytest.skip(f"real workflow_execution not importable here: {e}")

    engine = we.WorkflowExecutionEngine(FAKE_CONN)      # ctor does no DB I/O
    assert engine.active_executions == {}

    engine._active_executions["exec-1"] = {"status": "Running"}
    snap = engine.active_executions
    assert snap == {"exec-1": {"status": "Running"}}
    assert len(snap) == engine.get_active_executions_count()

    snap["exec-2"] = {}                                  # a copy, not the live dict
    assert "exec-2" not in engine._active_executions

    with pytest.raises(AttributeError):
        engine.active_executions = {}                    # read-only
