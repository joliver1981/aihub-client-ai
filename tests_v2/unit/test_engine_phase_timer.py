"""LLMDocumentEngine timing instrumentation (2026-08-21): _PhaseTimer and
_TimedCursor — the helpers behind the [ingest-timing] and [sql-store] log lines.

The engine module imports the whole document stack, so this self-skips where
that is not importable and runs for real under the doc API env:
    C:\\Users\\james\\miniconda3\\envs\\aihubant\\python.exe -m unittest tests_v2/unit/test_engine_phase_timer.py
"""
import os
import sys
import time
import unittest

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

try:
    from LLMDocumentEngine import _PhaseTimer, _TimedCursor      # noqa: E402
    HAVE_ENGINE = True
except Exception as e:                                            # pragma: no cover
    HAVE_ENGINE = False
    _IMPORT_ERR = e

if not HAVE_ENGINE:
    try:
        import pytest
        pytestmark = pytest.mark.skip(reason=f"needs the doc API env: {_IMPORT_ERR}")
    except ImportError:
        pass


@unittest.skipUnless(HAVE_ENGINE, "LLMDocumentEngine not importable in this env")
class TestPhaseTimer(unittest.TestCase):
    def test_sequential_phases_and_summary(self):
        t = _PhaseTimer()
        t.begin("detect"); time.sleep(0.02)
        t.begin("sql");    time.sleep(0.02)
        t.end()
        d = t.as_dict()
        self.assertGreaterEqual(d["detect"], 0.01)
        self.assertGreaterEqual(d["sql"], 0.01)
        self.assertGreaterEqual(d["total"], d["detect"] + d["sql"] - 0.01)
        self.assertEqual(list(d.keys())[:2], ["detect", "sql"])      # insertion order
        s = t.summary()
        self.assertTrue(s.startswith("total="))
        self.assertIn("detect=", s)
        self.assertIn("sql=", s)
        self.assertNotIn("*", s)

    def test_unfinished_phase_is_marked_with_star(self):
        t = _PhaseTimer()
        t.begin("vector"); t.begin("sql"); time.sleep(0.01)
        # an exception escaped 'sql' -> summary() must still name it
        d = t.as_dict()
        self.assertIn("vector", d)
        self.assertIn("sql*", d)
        self.assertIn("sql*=", t.summary())
        t.end()
        self.assertIn("sql", t.as_dict())
        self.assertNotIn("sql*", t.as_dict())

    def test_repeated_phase_accumulates(self):
        t = _PhaseTimer()
        t.begin("records"); time.sleep(0.01)
        t.begin("other");   time.sleep(0.005)
        t.begin("records"); time.sleep(0.01)
        t.end()
        self.assertGreaterEqual(t.as_dict()["records"], 0.015)

    def test_end_without_begin_is_noop(self):
        t = _PhaseTimer()
        t.end()
        self.assertEqual(list(t.as_dict().keys()), ["total"])


class _FakeCursor:
    def __init__(self):
        self.calls = []
        self.rows = [("x",)]
        self.rowcount = 7

    def execute(self, sql, *params):
        self.calls.append((sql, params))
        time.sleep(0.002)
        return self            # pyodbc returns the cursor itself

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return list(self.rows)


@unittest.skipUnless(HAVE_ENGINE, "LLMDocumentEngine not importable in this env")
class TestTimedCursor(unittest.TestCase):
    def test_delegates_and_times_by_statement_kind(self):
        raw = _FakeCursor()
        c = _TimedCursor(raw)
        self.assertIs(c.execute("INSERT INTO Documents (a) VALUES (?)", 1), raw)   # chaining works
        c.execute("INSERT   INTO Documents (a)   VALUES (?)", 2)                    # same kind (whitespace-normalized)
        c.execute("SELECT COUNT(*) FROM DocumentFields WHERE page_id = ?", "p")
        self.assertEqual(c.fetchone(), ("x",))            # __getattr__ delegation
        self.assertEqual(c.rowcount, 7)
        self.assertEqual(c.count, 3)
        self.assertEqual(len(raw.calls), 3)
        keys = list(c.stats.keys())
        self.assertEqual(len(keys), 2)
        ins = [k for k in keys if k.startswith("INSERT INTO Documents")][0]
        self.assertEqual(c.stats[ins][0], 2)
        self.assertGreater(c.stats[ins][1], 0)
        top = c.top(3)
        self.assertIn("x2", top)
        self.assertIn("INSERT INTO Documents", top)
        self.assertTrue(all(len(k) <= 48 for k in keys))

    def test_execute_exception_still_recorded_and_reraised(self):
        class Boom(_FakeCursor):
            def execute(self, sql, *p):
                raise RuntimeError("db down")
        c = _TimedCursor(Boom())
        with self.assertRaises(RuntimeError):
            c.execute("UPDATE X SET a=1")
        self.assertEqual(c.count, 1)
        self.assertIn("UPDATE X SET a=1", c.top())

    def test_top_empty(self):
        self.assertEqual(_TimedCursor(_FakeCursor()).top(), "-")


if __name__ == "__main__":
    unittest.main(verbosity=2)
