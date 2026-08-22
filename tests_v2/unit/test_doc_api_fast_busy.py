"""Doc API fast-busy contract (app_doc_api.py, 2026-08-21):

* /document/process answers 503 + Retry-After IMMEDIATELY when the admission
  gate is full (no queueing in waitress), and admits normally otherwise;
* /document/health exposes the gate (in_flight / limit / busy / rejected);
* DocumentProcessor.process_document passes the engine's per-phase `timings`
  through (additive field).

app_doc_api imports the full engine stack (pyodbc, chromadb, anthropic, ...),
so this module self-skips where that stack is not importable (the main-app
pytest sweep) and runs for real under the doc API's env:
    C:\\Users\\james\\miniconda3\\envs\\aihubant\\python.exe -m unittest tests_v2/unit/test_doc_api_fast_busy.py
"""
import os
import sys
import threading
import unittest

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

try:
    import app_doc_api                                   # noqa: E402
    HAVE_DOC_API = True
except Exception as e:                                   # pragma: no cover
    HAVE_DOC_API = False
    _IMPORT_ERR = e

if not HAVE_DOC_API:
    try:
        import pytest
        pytestmark = pytest.mark.skip(reason=f"needs the doc API env: {_IMPORT_ERR}")
    except ImportError:
        pass


@unittest.skipUnless(HAVE_DOC_API, "app_doc_api not importable in this env")
class TestProcessGate(unittest.TestCase):
    def setUp(self):
        self.client = app_doc_api.app.test_client()
        self.gate = app_doc_api._PROCESS_GATE
        self._saved_limit = self.gate.limit
        # Never let the test touch the real engine: replace the processor body.
        self._saved_body = app_doc_api._process_document_route_body

    def tearDown(self):
        self.gate.limit = self._saved_limit
        app_doc_api._process_document_route_body = self._saved_body

    def test_gate_limit_defaults_below_server_threads(self):
        # SERVER_THREADS - 2 by default (or DOC_PROCESS_MAX_INFLIGHT). Either
        # way it must leave headroom so the 503 itself can be served.
        threads = int(os.getenv("SERVER_THREADS", 10))
        explicit = os.getenv("DOC_PROCESS_MAX_INFLIGHT")
        if explicit:
            self.assertEqual(self.gate.limit, max(1, int(explicit)))
        else:
            self.assertEqual(self.gate.limit, max(1, threads - 2))

    def test_full_gate_returns_503_with_retry_after_and_does_not_run_body(self):
        ran = []
        app_doc_api._process_document_route_body = lambda: ran.append(1) or ("never", 200)
        self.gate.limit = 1
        tok = self.gate.try_enter()                      # occupy the only slot
        try:
            r = self.client.post("/document/process", data={"filePath": "C:/x/y.txt"})
        finally:
            self.gate.leave(tok)
        self.assertEqual(r.status_code, 503)
        self.assertIn("Retry-After", r.headers)
        body = r.get_json()
        self.assertEqual(body["status"], "busy")
        self.assertEqual(body["in_flight"], 1)
        self.assertEqual(body["max_in_flight"], 1)
        self.assertIn("retry", body["message"].lower())
        self.assertEqual(ran, [])                        # nothing processed
        self.assertGreaterEqual(self.gate.snapshot()["rejected_total"], 1)

    def test_admitted_request_runs_body_and_releases_slot(self):
        from flask import jsonify
        seen = {}

        def fake_body():
            seen["in_flight_during"] = self.gate.snapshot()["in_flight"]
            return jsonify({"status": "success", "document_id": "d1"})

        app_doc_api._process_document_route_body = fake_body
        self.gate.limit = 2
        r = self.client.post("/document/process", data={"filePath": "C:/x/y.txt"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["document_id"], "d1")
        self.assertEqual(seen["in_flight_during"], 1)    # counted while running
        self.assertEqual(self.gate.snapshot()["in_flight"], 0)   # released after

    def test_slot_released_even_when_body_raises(self):
        def boom():
            raise RuntimeError("engine exploded")

        app_doc_api._process_document_route_body = boom
        self.gate.limit = 1
        app_doc_api.app.config["PROPAGATE_EXCEPTIONS"] = False
        r = self.client.post("/document/process", data={"filePath": "C:/x/y.txt"})
        self.assertEqual(r.status_code, 500)
        self.assertEqual(self.gate.snapshot()["in_flight"], 0)

    def test_health_exposes_gate(self):
        r = self.client.get("/document/health")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertIn("process_gate", j)
        self.assertIn("busy", j)
        for k in ("in_flight", "limit", "busy", "rejected_total", "retry_after_s"):
            self.assertIn(k, j["process_gate"])
        self.assertEqual(j["process_gate"]["name"], "document/process")


@unittest.skipUnless(HAVE_DOC_API, "app_doc_api not importable in this env")
class TestTimingsPassthrough(unittest.TestCase):
    def test_process_document_includes_engine_timings(self):
        class FakeProcessor:
            def process_document(self, **kw):
                return {"filename": "f.txt", "document_id": "d1", "document_type": "x",
                        "page_count": 1, "processing_error": None,
                        "pages": [{"full_text": "hello", "extracted_data": {"a": 1}}],
                        "timings": {"detect": 1.5, "sql": 0.4, "total": 2.0}}

        saved = app_doc_api.get_document_processor
        app_doc_api.get_document_processor = lambda: FakeProcessor()
        try:
            out = app_doc_api.DocumentProcessor.process_document("C:/x/f.txt")
        finally:
            app_doc_api.get_document_processor = saved
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["timings"]["sql"], 0.4)
        self.assertEqual(out["page_count"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
