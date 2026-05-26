"""Shared fixtures for tests_v2/data_lifecycle.

Drives live HTTP CRUD round-trips against the main AI Hub app on
:5001.  Every fixture here assumes the app is already running; no
service-management is attempted.

All artifacts created by this suite are named ``DLT_v2_<entity>_<uuid>``
so the prefix is unique to this test pack.  A session-scoped pre-clean
fixture walks each entity list endpoint and removes leftovers from
prior aborted runs.  A module-scoped tracker collects everything
created during the module run and DELETEs it on teardown.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest
import requests

# Unicode-safe stdout on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


MAIN_BASE_URL = os.getenv("AIHUB_MAIN_URL", "http://localhost:5001")
API_KEY = os.getenv("AIHUB_API_KEY", "DB27D555-03A8-446E-9C23-8DAAA95EAD21")
PREFIX = "DLT_v2_"


# ---------------------------------------------------------------------------
# Service readiness
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def services_ready() -> bool:
    """Skip the whole suite if the main app on :5001 is unreachable."""
    try:
        r = requests.get(
            f"{MAIN_BASE_URL}/get/workflows",
            headers={"X-API-Key": API_KEY},
            timeout=5,
        )
        if r.status_code != 200:
            pytest.skip(
                f"Main app at {MAIN_BASE_URL} returned {r.status_code} "
                f"for /get/workflows. Skipping data-lifecycle suite."
            )
    except Exception as e:
        pytest.skip(f"Main app at {MAIN_BASE_URL} not reachable: {e}")
    return True


# ---------------------------------------------------------------------------
# HTTP session
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


@pytest.fixture(scope="session")
def main_url() -> str:
    return MAIN_BASE_URL


@pytest.fixture(scope="session")
def unique_name_factory():
    """Session-scoped variant of ``unique_name`` for fixtures that need it
    in module/session scope (the function-scoped ``unique_name`` cannot
    be requested from a module fixture).
    """
    def _make(prefix: str) -> str:
        return f"{PREFIX}{prefix}_{uuid.uuid4().hex[:8]}"
    return _make


# ---------------------------------------------------------------------------
# Naming helper
# ---------------------------------------------------------------------------

@pytest.fixture
def unique_name() -> Callable[[str], str]:
    """Returns ``DLT_v2_<prefix>_<uuid8>``.

    Used by every test to generate collision-free, identifiable names.
    """
    def _make(prefix: str) -> str:
        return f"{PREFIX}{prefix}_{uuid.uuid4().hex[:8]}"
    return _make


# ---------------------------------------------------------------------------
# Cleanup tracker
# ---------------------------------------------------------------------------

class CleanupTracker:
    """Collects ``(entity_type, id, delete_url, delete_method, delete_body)``
    tuples for module teardown."""

    def __init__(self):
        self._items: List[Tuple[str, Any, str, str, Optional[Dict]]] = []

    def add(
        self,
        entity_type: str,
        entity_id: Any,
        delete_url: str,
        method: str = "DELETE",
        body: Optional[Dict] = None,
    ) -> None:
        self._items.append((entity_type, entity_id, delete_url, method, body))

    def items(self):
        return list(self._items)


@pytest.fixture(scope="module")
def cleanup_tracker(api_session) -> CleanupTracker:
    tracker = CleanupTracker()
    yield tracker
    # Teardown: best-effort DELETE every tracked entity.
    for entity_type, entity_id, url, method, body in reversed(tracker.items()):
        try:
            full_url = f"{MAIN_BASE_URL}{url}"
            if method.upper() == "DELETE":
                api_session.delete(full_url, timeout=10)
            elif method.upper() == "POST":
                api_session.post(full_url, json=body or {}, timeout=10)
            else:
                api_session.request(method.upper(), full_url, json=body or {}, timeout=10)
        except Exception:
            pass  # tolerant of already-deleted (404 ok) and network blips


# ---------------------------------------------------------------------------
# Session-level pre-clean
# ---------------------------------------------------------------------------

def _safe_get_json(session: requests.Session, url: str):
    try:
        r = session.get(url, timeout=10)
        if r.status_code != 200:
            return None
        # Some endpoints return a JSON-encoded string. Decode twice if so.
        try:
            data = r.json()
        except Exception:
            return None
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                return None
        return data
    except Exception:
        return None


@pytest.fixture(scope="session", autouse=True)
def preclean_leftovers(services_ready, api_session):
    """Best-effort: at session start, find and DELETE any ``DLT_v2_*``
    artifacts left over from a prior aborted run.

    Failures are silently ignored — pre-clean is convenience, not
    correctness.  The suite still passes if pre-clean fails because
    each test uses a fresh uuid suffix.
    """
    cleanups = [
        # (entity_type, list_url, list_key, id_key, name_key, delete_url_fmt, method, body_fn)
        ("compliance_retailer", "/api/compliance/retailers", "retailers",
         "retailer_id", "name", "/api/compliance/retailers/{id}", "DELETE", None),
        ("compliance_schema", "/api/compliance/schemas", "schemas",
         "schema_id", "name", "/api/compliance/schemas/{id}", "DELETE", None),
        ("mcp_server", "/api/mcp/servers", "",
         "server_id", "server_name", "/api/mcp/servers/{id}", "DELETE", None),
        ("integration", "/api/integrations", "integrations",
         "integration_id", "integration_name", "/api/integrations/{id}", "DELETE", None),
    ]
    for (entity_type, list_url, list_key, id_key, name_key,
         delete_fmt, method, body_fn) in cleanups:
        data = _safe_get_json(api_session, f"{MAIN_BASE_URL}{list_url}")
        if data is None:
            continue
        rows = data.get(list_key) if list_key else data
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get(name_key) or ""
            if not isinstance(name, str) or not name.startswith(PREFIX):
                continue
            rid = row.get(id_key)
            if rid is None:
                continue
            try:
                if method == "DELETE":
                    api_session.delete(
                        f"{MAIN_BASE_URL}{delete_fmt.format(id=rid)}",
                        timeout=10,
                    )
                else:
                    api_session.request(
                        method,
                        f"{MAIN_BASE_URL}{delete_fmt.format(id=rid)}",
                        json=body_fn(rid) if body_fn else {},
                        timeout=10,
                    )
            except Exception:
                pass

    # Special-case: workflows use a different shape
    data = _safe_get_json(api_session, f"{MAIN_BASE_URL}/get/workflows")
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                name = row.get("workflow_name") or ""
                if isinstance(name, str) and name.startswith(PREFIX):
                    wid = row.get("id")
                    if wid is not None:
                        try:
                            api_session.delete(
                                f"{MAIN_BASE_URL}/delete/workflow/{wid}",
                                timeout=10,
                            )
                        except Exception:
                            pass

    yield


# ---------------------------------------------------------------------------
# Response helpers reused by tests
# ---------------------------------------------------------------------------

@pytest.fixture
def maybe_json_string_decode():
    """Some endpoints return ``jsonify(json_string)`` so the client gets a
    JSON-encoded string. This helper double-decodes when needed.
    """
    def _decode(payload):
        if isinstance(payload, str):
            try:
                return json.loads(payload)
            except Exception:
                return payload
        return payload
    return _decode
