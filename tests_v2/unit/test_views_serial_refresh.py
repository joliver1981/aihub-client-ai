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


def _run_as_viewer(tile_cache):
    """A viewer who may not start automation runs: the manage seam answers
    403 for every tile. Returns (result, cache writes)."""
    writes = []

    async def refused(action, payload, timeout=0):
        return {"error": "Developer role required"}, 403

    original = (V._manage, V.views_store.set_cache)
    V._manage = refused
    V.views_store.set_cache = lambda *a, **k: writes.append(a)
    try:
        view = {"view_id": "v1", "name": "V", "scope": "group", "version": 1,
                "tiles": [_tile("a", auto="SAME"), _tile("b", auto="SAME")],
                "tile_cache": tile_cache}
        result = asyncio.run(V.run_view(view))
    finally:
        V._manage, V.views_store.set_cache = original
    return result, writes


def test_viewer_who_may_not_refresh_gets_the_saved_numbers_marked_as_such():
    saved = [{"columns": ["n"], "rows": [[7]], "at": "2026-11-25T14:05:00+00:00"},
             {"columns": ["n"], "rows": [[8]], "at": "2026-11-25T14:05:03+00:00"}]
    result, writes = _run_as_viewer(saved)
    for tile, slot in zip(result["tiles"], saved):
        assert tile["viewer_only"] is True
        # still an error for the scheduler, the email and the cache merge
        assert "Developer or Admin" in tile["error"]
        assert tile["cache"]["rows"] == slot["rows"]
        assert tile["cache"]["cached_at"] == slot["at"]
    assert writes == [], "a refused refresh must never overwrite the saved numbers"


def test_viewer_who_may_not_refresh_and_nothing_saved_keeps_the_message():
    result, writes = _run_as_viewer([])
    for tile in result["tiles"]:
        assert tile["viewer_only"] is True
        assert tile["error"]
        assert "cache" not in tile
    assert writes == []


# ---------------------------------------------------------------------------
# A regular user refreshes a View that was shared with them (2026-11-25)
# ---------------------------------------------------------------------------

_OK = ({"status": "success", "run_id": "r1",
        "stdout_tail": '{"value": 4, "label": "n"}'}, 200)


def _refresh_as(role, answer=_OK, owner=5, tile_cache=None, stored_cache=None,
                switch=None, tiles=None):
    """run_view as a user of the given role, with the manage seam recorded.
    Returns (result, calls, cache writes)."""
    calls, writes = [], []

    async def manage(action, payload, timeout=0):
        calls.append((action, dict(payload)))
        got = answer(action, payload) if callable(answer) else answer
        return dict(got[0]), got[1]

    original = (V._manage, V.views_store.set_cache, V.views_store.get_cache)
    before = os.environ.get("VIEW_VIEWER_REFRESH")
    V._manage = manage
    V.views_store.set_cache = lambda *a, **k: writes.append(a)
    V.views_store.get_cache = lambda view_id: stored_cache
    if switch is not None:
        os.environ["VIEW_VIEWER_REFRESH"] = switch
    token = V.CURRENT_USER.set({"user_id": 349, "role": role, "username": "u"})
    try:
        view = {"view_id": "v1", "name": "Team board", "scope": "group",
                "group_id": 3, "version": 1, "owner_user": owner,
                "tiles": tiles or [_tile("a", auto="SAME"), _tile("b", auto="SAME")],
                "tile_cache": tile_cache or []}
        result = asyncio.run(V.run_view(view))
    finally:
        V.CURRENT_USER.reset(token)
        V._manage, V.views_store.set_cache, V.views_store.get_cache = original
        if switch is not None:
            if before is None:
                os.environ.pop("VIEW_VIEWER_REFRESH", None)
            else:
                os.environ["VIEW_VIEWER_REFRESH"] = before
    return result, calls, writes


def test_regular_viewer_refresh_names_who_saved_the_view():
    result, calls, writes = _refresh_as(1)
    assert [a for a, _ in calls] == ["run", "run"]
    for _, payload in calls:
        assert payload["view_tile"] == {"view": "Team board", "scope": "group",
                                        "shared_by": 5}
    for tile in result["tiles"]:
        assert not tile.get("error"), tile
        assert tile["rows"] == [[4]]
    assert len(writes) == 1, "the fresh numbers are saved for everyone"


def test_developer_refresh_runs_in_their_own_right():
    _, calls, _ = _refresh_as(2)
    assert calls and all("view_tile" not in p for _, p in calls)


def test_viewer_refresh_switch_off_sends_no_claim():
    _, calls, _ = _refresh_as(1, switch="false")
    assert calls and all("view_tile" not in p for _, p in calls)


def test_view_with_no_recorded_saver_sends_no_claim():
    _, calls, _ = _refresh_as(1, owner=0)
    assert calls and all("view_tile" not in p for _, p in calls)


def test_viewer_refresh_abort_of_a_checkpoint_carries_the_claim():
    def answer(action, payload):
        if action == "run":
            return ({"waiting_on_checkpoint": True, "run_id": "r9",
                     "pending_checkpoint": {"checkpoint_id": "c1"}}, 200)
        return ({"ok": True}, 200)

    result, calls, _ = _refresh_as(1, answer=answer, tiles=[_tile("a")])
    aborts = [p for a, p in calls if a == "checkpoint_decision"]
    assert len(aborts) == 1
    assert aborts[0]["decision"] == "abort"
    assert aborts[0]["view_tile"]["shared_by"] == 5
    assert "no approval left pending" in result["tiles"][0]["error"]


def test_two_refreshes_at_once_show_the_saved_numbers_without_a_fault():
    saved = [{"columns": ["n"], "rows": [[7]], "at": "2026-11-25T14:05:00+00:00"},
             {"columns": ["n"], "rows": [[8]], "at": "2026-11-25T14:05:03+00:00"}]
    skipped = ({"status": "skipped", "run_id": "r2",
                "note": "a run is already in progress"}, 200)
    result, _, writes = _refresh_as(1, answer=skipped, tile_cache=saved)
    for tile, slot in zip(result["tiles"], saved):
        assert tile["refresh_in_flight"] is True
        assert "running right now" in tile["error"]
        assert tile["cache"]["rows"] == slot["rows"]
    assert writes == [], "a skipped run must never overwrite the saved numbers"


def test_merge_keeps_the_slots_another_refresh_wrote_meanwhile():
    """This refresh read the cache when it started; while it ran, another one
    wrote tile b. Tile a succeeds here, tile b comes back skipped."""
    at_start = [{"columns": ["n"], "rows": [[1]], "at": "2026-11-25T14:00:00+00:00"},
                {"columns": ["n"], "rows": [[2]], "at": "2026-11-25T14:00:00+00:00"}]
    meanwhile = [dict(at_start[0]),
                 {"columns": ["n"], "rows": [[99]], "at": "2026-11-25T14:15:00+00:00"}]
    seen = []

    def answer(action, payload):
        seen.append(action)
        return _OK if len(seen) == 1 else ({"status": "skipped", "run_id": "r"}, 200)

    result, _, writes = _refresh_as(2, answer=answer, tile_cache=at_start,
                                    stored_cache=meanwhile)
    assert result["tiles"][0]["rows"] == [[4]]
    assert result["tiles"][1]["cache"]["rows"] == [[99]]
    written = writes[0][1]
    assert written[0]["rows"] == [[4]]
    assert written[1]["rows"] == [[99]], "the other refresh's numbers were lost"


def test_the_saver_of_the_current_tiles_is_recorded_on_a_shared_view():
    """Regular viewers refresh on the standing of whoever saved the tiles the
    View holds now — not of whoever first created a View of that name."""
    import tempfile
    store = V.views_store
    original = store.DB_PATH
    store.DB_PATH = os.path.join(tempfile.mkdtemp(), "views_test.db")
    try:
        store.init()
        sql_tile = [{"title": "t", "connection": "c", "sql": "SELECT 1"}]
        member = lambda uid: [3]
        store.save("Board", "", sql_tile, 11, scope="group", group_id=3,
                   member_check=member)
        assert store.get("Board", 11, [3])["owner_user"] == 11
        store.save("Board", "", sql_tile, 22, scope="group", group_id=3,
                   member_check=member)
        view = store.get("Board", 11, [3])
        assert view["owner_user"] == 22 and view["version"] == 2
        assert store.get_cache(view["view_id"]) is None
        store.set_cache(view["view_id"], [{"rows": [[1]]}])
        assert store.get_cache(view["view_id"]) == [{"rows": [[1]]}]
    finally:
        store.DB_PATH = original


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
