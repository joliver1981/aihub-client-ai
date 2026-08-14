"""Views refresh — tiles backed by the SAME automation must run one at a time.

Why this exists: the automation runner's skip-if-running lock is keyed on
automation_id ALONE (automations/runner.py _db_has_live_run counts live
AutomationRuns rows for that id; `inputs` are not part of the key). run_view
used to fire every tile concurrently, so a View whose tiles are panels of one
parameterized automation lost all but one tile on every refresh — observed live
on 'AP Invoice Aging': six tiles, one automation, five "run skipped".

The fix serializes per automation rather than relaxing the lock, so the
platform's no-concurrency guarantee for stateful automations is untouched.

Runs standalone (python test_views_serial_refresh.py) or under pytest.
"""
import asyncio
import os
import sys
import time

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

import views_tools as V  # noqa: E402

TICK = 0.05


def _tile(title, kind="automation", auto="A", **extra):
    t = {"title": title, "type": kind, "viz": "table"}
    if kind == "automation":
        t["automation_id"] = auto
    else:
        t["sql"] = "SELECT 1"
        t["connection"] = "c"
    t.update(extra)
    return t


def _run(tiles, budget=None):
    """Drive run_view with the real grouping but stubbed tile execution,
    recording when each tile started and finished."""
    spans = []

    async def fake(t, tile):
        start = time.monotonic()
        await asyncio.sleep(TICK)
        spans.append((tile["title"], start, time.monotonic()))
        tile["columns"], tile["rows"] = ["a"], [[1]]

    original = (V._run_automation_tile, V._run_sql_tile,
                V.views_store.set_cache, V.VIEW_SERIAL_BUDGET)
    V._run_automation_tile = fake
    V._run_sql_tile = fake
    V.views_store.set_cache = lambda *a, **k: None
    if budget is not None:
        V.VIEW_SERIAL_BUDGET = budget
    try:
        view = {"view_id": "v1", "name": "V", "scope": "user", "version": 1,
                "tiles": tiles, "tile_cache": []}
        result = asyncio.run(V.run_view(view))
    finally:
        (V._run_automation_tile, V._run_sql_tile,
         V.views_store.set_cache, V.VIEW_SERIAL_BUDGET) = original
    return result, spans


def _overlap(a, b):
    return a[1] < b[2] and b[1] < a[2]


def test_tiles_sharing_an_automation_run_strictly_one_at_a_time():
    _, spans = _run([_tile(f"panel {i}", auto="SAME") for i in range(4)])
    assert len(spans) == 4
    for i in range(len(spans)):
        for j in range(i + 1, len(spans)):
            assert not _overlap(spans[i], spans[j]), (spans[i], spans[j])


def test_different_automations_still_run_concurrently():
    """Serializing everything would slow every ordinary board for nothing."""
    _, spans = _run([_tile("a", auto="A"), _tile("b", auto="B"),
                     _tile("c", auto="C")])
    assert any(_overlap(spans[i], spans[j])
               for i in range(len(spans)) for j in range(i + 1, len(spans)))


def test_sql_tiles_are_never_serialized():
    """The read-only probe seam has no skip-if-running lock."""
    _, spans = _run([_tile(f"q{i}", kind="sql") for i in range(4)])
    assert any(_overlap(spans[i], spans[j])
               for i in range(len(spans)) for j in range(i + 1, len(spans)))


def test_a_shared_automation_group_does_not_block_other_tiles():
    tiles = [_tile("s1", auto="SAME"), _tile("s2", auto="SAME"),
             _tile("q", kind="sql")]
    _, spans = _run(tiles)
    by_title = {s[0]: s for s in spans}
    assert not _overlap(by_title["s1"], by_title["s2"])
    # the SQL tile overlaps one of them rather than queueing behind the group
    assert (_overlap(by_title["q"], by_title["s1"])
            or _overlap(by_title["q"], by_title["s2"]))


def test_every_tile_still_gets_its_data():
    result, _ = _run([_tile(f"panel {i}", auto="SAME") for i in range(6)])
    assert len(result["tiles"]) == 6
    for t in result["tiles"]:
        assert not t.get("error"), t
        assert t["rows"] == [[1]]


def test_budget_exhaustion_is_reported_not_silently_dropped():
    result, spans = _run([_tile(f"panel {i}", auto="SAME") for i in range(5)],
                         budget=0.001)
    # First tile always runs; the budget is only checked BETWEEN tiles, so one
    # slow tile is never cut off midway.
    assert len(spans) == 1
    errors = [t.get("error") for t in result["tiles"][1:]]
    assert all(e and "not refreshed" in e for e in errors), errors
    assert all("one at a time" in e for e in errors)
    assert not result["tiles"][0].get("error")


def test_single_tile_refresh_is_unaffected():
    """The per-tile timer path (tile_index) must behave exactly as before."""
    original = (V._run_automation_tile, V.views_store.set_cache)

    async def fake(t, tile):
        tile["columns"], tile["rows"] = ["a"], [[9]]

    V._run_automation_tile = fake
    V.views_store.set_cache = lambda *a, **k: None
    try:
        view = {"view_id": "v1", "name": "V", "tiles":
                [_tile("a", auto="SAME"), _tile("b", auto="SAME")],
                "tile_cache": []}
        result = asyncio.run(V.run_view(view, only_index=1))
    finally:
        V._run_automation_tile, V.views_store.set_cache = original
    assert len(result["tiles"]) == 1
    assert result["tiles"][0]["index"] == 1
    assert result["tiles"][0]["rows"] == [[9]]


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                passed += 1
                print(f"PASS {name}")
            except Exception as e:
                failed += 1
                print(f"FAIL {name}: {e}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
