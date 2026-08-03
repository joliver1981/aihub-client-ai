"""aihub.query() dead-predicate detection.

A filter written against a value that does not exist returns zero rows forever,
raises nothing, and silently disables the rule it implements. Live failure: a
dunning automation filtered `activity_type = 'promise_to_pay'` on a column
holding 'ptp' -- the promise-to-pay hold never fired and a customer who had
already promised to pay was sent a dunning letter.

Value grounding prevents that when the code is written. This is the runtime half:
it fires even when the model ignored the grounding, and it keeps working months
later when someone adds a code the automation was never taught about.

The tension these tests pin down: a query returning nothing is usually FINE
("any exceptions today? none"). Only a statement that matches nothing across
several executions is evidence of a filter that cannot match.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

SDK = Path(__file__).resolve().parents[2] / "automations" / "sdk"
sys.path.insert(0, str(SDK))


@pytest.fixture()
def sdk(monkeypatch):
    import aihub_runtime
    importlib.reload(aihub_runtime)
    logged = []
    monkeypatch.setattr(aihub_runtime, "log", lambda m: logged.append(str(m)))
    aihub_runtime._QUERY_STATS.clear()
    aihub_runtime.logged = logged
    return aihub_runtime


def report(sdk):
    """End of run — the only point at which 'never matched' can be asserted."""
    sdk._report_dead_queries()
    return [m for m in sdk.logged if "0 rows every time" in m]


def test_single_empty_result_is_not_flagged(sdk):
    """The commonest honest case: asked for exceptions, there were none."""
    sdk._note_query_result("SELECT * FROM t WHERE status = 'x'", 0)
    assert report(sdk) == []


def test_two_empty_results_still_below_threshold(sdk):
    for _ in range(2):
        sdk._note_query_result("SELECT * FROM t WHERE status = 'x'", 0)
    assert report(sdk) == []


def test_three_empty_results_are_flagged(sdk):
    for _ in range(3):
        sdk._note_query_result("SELECT * FROM t WHERE activity_type = 'promise_to_pay'", 0)
    r = report(sdk)
    assert len(r) == 1 and "promise_to_pay" in r[0]


def test_the_real_bug_shape_is_caught(sdk):
    """The PTP check ran once per customer -- twelve times, nothing every time."""
    for _ in range(12):
        sdk._note_query_result(
            "SELECT TOP 1 activity_id FROM dbo.CG_CollectionActivity "
            "WHERE customer_id = ? AND activity_type = 'promise_to_pay'", 0)
    r = report(sdk)
    assert len(r) == 1, "one line for the statement, not one per execution"
    assert "12x" in r[0]


def test_a_query_that_ever_matched_is_never_flagged(sdk):
    """The dispute check next to it: twelve runs, one hit. Working correctly."""
    sql = "SELECT TOP 1 activity_id FROM x WHERE activity_type = 'dispute'"
    for i in range(12):
        sdk._note_query_result(sql, 1 if i == 3 else 0)
    assert report(sdk) == []


def test_early_misses_then_a_hit_do_not_warn(sdk):
    """THE false-positive case: a per-row loop whose first three iterations
    legitimately find nothing and whose fourth hits. Warning inline at the
    threshold would fire here; deferring the verdict to end-of-run does not."""
    sql = "SELECT 1 FROM t WHERE customer_id = ? AND status = 'dispute'"
    for _ in range(3):
        sdk._note_query_result(sql, 0)
    sdk._note_query_result(sql, 1)
    for _ in range(8):
        sdk._note_query_result(sql, 0)
    assert report(sdk) == []


def test_distinct_statements_are_tracked_separately(sdk):
    for _ in range(3):
        sdk._note_query_result("SELECT * FROM a WHERE c = 'dead'", 0)
        sdk._note_query_result("SELECT * FROM b WHERE c = 'live'", 1)
    r = report(sdk)
    assert len(r) == 1 and "'dead'" in r[0]


def test_whitespace_variants_are_the_same_statement(sdk):
    """Loop-built SQL is often re-indented per iteration; that must not reset
    the counter and hide a dead filter."""
    for i in range(3):
        sdk._note_query_result(f"SELECT *\n{'  ' * i}FROM t\nWHERE c = 'x'", 0)
    assert len(report(sdk)) == 1


def test_parameterised_queries_group_by_statement_not_by_value(sdk):
    sql = "SELECT 1 FROM t WHERE customer_id = ? AND activity_type = 'promise_to_pay'"
    for _ in range(5):
        sdk._note_query_result(sql, 0)
    assert len(report(sdk)) == 1


def test_several_dead_queries_are_all_listed(sdk):
    for _ in range(3):
        sdk._note_query_result("SELECT * FROM a WHERE c = 'x'", 0)
        sdk._note_query_result("SELECT * FROM b WHERE c = 'y'", 0)
    assert len(report(sdk)) == 2


def test_clean_run_says_nothing_at_all(sdk):
    for _ in range(5):
        sdk._note_query_result("SELECT * FROM t", 3)
    sdk._report_dead_queries()
    assert sdk.logged == [], "a healthy run must stay silent"


def test_instrumentation_never_raises(sdk):
    class Hostile:
        def __str__(self):
            raise RuntimeError("nope")
    sdk._note_query_result(Hostile(), 0)      # must not propagate
    sdk._report_dead_queries()


def test_report_names_the_remedy(sdk):
    for _ in range(3):
        sdk._note_query_result("SELECT * FROM t WHERE c = 'bogus'", 0)
    sdk._report_dead_queries()
    assert any("get_connection_schema" in m for m in sdk.logged), \
        "the warning has to say how to find the real values"


def test_report_actually_fires_at_process_exit():
    """End-to-end on the real atexit path: a script that never calls the reporter
    itself must still emit it when the process ends, because that is how a real
    automation run works."""
    import subprocess
    script = (
        "import sys; sys.path.insert(0, r'%s')\n"
        "import aihub_runtime as a\n"
        "[a._note_query_result(\"SELECT 1 FROM t WHERE c = 'ghost'\", 0) for _ in range(4)]\n"
        "[a._note_query_result('SELECT 1 FROM t2', 7) for _ in range(4)]\n"
        "print('script finished')\n" % SDK)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True,
                         text=True, timeout=60).stdout
    assert "script finished" in out
    assert "0 rows every time" in out, "atexit reporter did not run"
    assert "'ghost'" in out
    assert "t2" not in out, "the healthy query must not be reported"
