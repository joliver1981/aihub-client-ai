"""inflight_gate.InflightGate — the fast-busy admission gate (2026-08-21).

Pure unit tests + a tiny Flask app for the decorator / 503 contract. No live
services. Runs under pytest (aihub2.1) or standalone:
    python tests_v2/unit/test_inflight_gate.py
"""
import os
import sys
import threading
import time
import unittest

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from inflight_gate import InflightGate, limit_from_env, gated, flask_busy_response  # noqa: E402


class TestGateCore(unittest.TestCase):
    def test_admits_up_to_limit_then_rejects_without_blocking(self):
        g = InflightGate("t", limit=2)
        t1 = g.try_enter()
        t2 = g.try_enter()
        self.assertIsNotNone(t1)
        self.assertIsNotNone(t2)
        t0 = time.perf_counter()
        self.assertIsNone(g.try_enter())          # full -> immediate None
        self.assertLess(time.perf_counter() - t0, 0.05)
        snap = g.snapshot()
        self.assertEqual(snap["in_flight"], 2)
        self.assertTrue(snap["busy"])
        self.assertEqual(snap["rejected_total"], 1)
        self.assertEqual(snap["admitted_total"], 2)
        self.assertEqual(snap["peak_in_flight"], 2)
        g.leave(t1)
        self.assertFalse(g.snapshot()["busy"])
        self.assertIsNotNone(g.try_enter())       # slot reusable

    def test_leave_is_idempotent_and_ignores_unknown_tokens(self):
        g = InflightGate("t", limit=1)
        t = g.try_enter()
        self.assertIsNotNone(g.leave(t))
        self.assertIsNone(g.leave(t))             # double release ignored
        self.assertIsNone(g.leave(999))
        self.assertIsNone(g.leave(None))
        self.assertEqual(g.snapshot()["in_flight"], 0)

    def test_slot_context_manager_releases_on_exception(self):
        g = InflightGate("t", limit=1)
        with self.assertRaises(RuntimeError):
            with g.slot() as tok:
                self.assertIsNotNone(tok)
                raise RuntimeError("boom")
        self.assertEqual(g.snapshot()["in_flight"], 0)
        with g.slot() as tok:
            self.assertIsNotNone(tok)
            with g.slot() as tok2:
                self.assertIsNone(tok2)           # nested caller sees busy

    def test_retry_after_default_then_adaptive_and_clamped(self):
        g = InflightGate("t", limit=1, retry_after_default=30, retry_after_min=10,
                         retry_after_max=300)
        self.assertEqual(g.retry_after_seconds(), 30)
        # feed held durations: median 120 s -> half = 60
        for held in (100.0, 120.0, 140.0):
            g._recent_durations.append(held)
        self.assertEqual(g.retry_after_seconds(), 60)
        g._recent_durations.clear()
        g._recent_durations.append(2.0)           # tiny -> clamped to min
        self.assertEqual(g.retry_after_seconds(), 10)
        g._recent_durations.clear()
        g._recent_durations.append(5000.0)        # huge -> clamped to max
        self.assertEqual(g.retry_after_seconds(), 300)

    def test_busy_payload_shape(self):
        g = InflightGate("document/process", limit=3)
        g.try_enter(); g.try_enter(); g.try_enter()
        p = g.busy_payload("document import")
        self.assertEqual(p["status"], "busy")
        self.assertEqual(p["in_flight"], 3)
        self.assertEqual(p["max_in_flight"], 3)
        self.assertIn("document import", p["message"])
        self.assertIn("Nothing was processed", p["message"])
        self.assertIsInstance(p["retry_after"], int)

    def test_thread_safety_never_exceeds_limit(self):
        g = InflightGate("t", limit=4)
        peak = [0]
        lock = threading.Lock()
        admitted = [0]

        def worker():
            for _ in range(200):
                tok = g.try_enter()
                if tok is None:
                    continue
                with lock:
                    admitted[0] += 1
                    cur = g.snapshot()["in_flight"]
                    peak[0] = max(peak[0], cur)
                time.sleep(0.0005)
                g.leave(tok)

        ts = [threading.Thread(target=worker) for _ in range(12)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertLessEqual(peak[0], 4)
        self.assertEqual(g.snapshot()["in_flight"], 0)
        self.assertGreater(admitted[0], 0)


class TestLimitFromEnv(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("X_LIMIT", "X_THREADS")}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_explicit_wins_else_threads_minus_headroom_with_floor(self):
        os.environ.pop("X_LIMIT", None)
        os.environ["X_THREADS"] = "16"
        self.assertEqual(limit_from_env("X_LIMIT", "X_THREADS", 10, headroom=2), 14)
        os.environ["X_THREADS"] = "4"
        self.assertEqual(limit_from_env("X_LIMIT", "X_THREADS", 10, headroom=2), 2)
        os.environ["X_THREADS"] = "1"
        self.assertEqual(limit_from_env("X_LIMIT", "X_THREADS", 10, headroom=2, floor=1), 1)
        os.environ["X_LIMIT"] = "3"
        self.assertEqual(limit_from_env("X_LIMIT", "X_THREADS", 10, headroom=2), 3)
        os.environ["X_LIMIT"] = "garbage"
        os.environ.pop("X_THREADS", None)
        self.assertEqual(limit_from_env("X_LIMIT", "X_THREADS", 10, headroom=2), 8)


class TestFlaskContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from flask import Flask, jsonify
        except ImportError:          # pragma: no cover
            raise unittest.SkipTest("flask not installed in this env")
        cls.gate = InflightGate("search", limit=1, retry_after_default=42)
        app = Flask(__name__)
        release = threading.Event()
        started = threading.Event()

        @app.route("/slow")
        @gated(cls.gate, what="document search")
        def slow():
            started.set()
            release.wait(5)
            return jsonify({"ok": True})

        @app.route("/busy-direct")
        def busy_direct():
            return flask_busy_response(cls.gate, what="document import")

        cls.app, cls.release, cls.started = app, release, started

    def test_second_caller_gets_503_with_retry_after_while_first_runs(self):
        client1 = self.app.test_client()
        client2 = self.app.test_client()
        results = {}

        def first():
            results["first"] = client1.get("/slow")

        t = threading.Thread(target=first)
        t.start()
        self.assertTrue(self.started.wait(5))
        r2 = client2.get("/slow")                 # gate full -> immediate 503
        self.assertEqual(r2.status_code, 503)
        self.assertEqual(r2.headers.get("Retry-After"), "42")
        self.assertEqual(r2.headers.get("X-Inflight"), "1/1")
        body = r2.get_json()
        self.assertEqual(body["status"], "busy")
        self.assertEqual(body["retry_after"], 42)
        self.assertIn("document search", body["message"])
        self.release.set()
        t.join(5)
        self.assertEqual(results["first"].status_code, 200)
        self.assertEqual(self.gate.snapshot()["in_flight"], 0)
        # slot freed -> next call is admitted
        self.release.set()
        r3 = client2.get("/slow")
        self.assertEqual(r3.status_code, 200)

    def test_direct_busy_response_helper(self):
        with self.app.test_request_context():
            resp = flask_busy_response(self.gate, what="document import")
            self.assertEqual(resp.status_code, 503)
            self.assertIn("Retry-After", resp.headers)
            self.assertEqual(resp.get_json()["status"], "busy")


if __name__ == "__main__":
    unittest.main(verbosity=2)
