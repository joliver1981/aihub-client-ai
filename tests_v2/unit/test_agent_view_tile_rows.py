"""
A View tile keeps the rows its table has, not 50 (2026-11-25).

A tile is a table a person pages through on the Views screen (15 rows a page,
with filter, sort and CSV). It was fed through limits made for something else:
  - a SQL tile through the model's query probe: 50 rows, 25 columns, 200
    characters a cell;
  - an automation tile through the last 2,000 characters of the run's output,
    so its JSON line had to stay under about 1,900 characters, and 50 rows.
Now a tile keeps up to VIEW_TILE_ROW_CAP rows (10,000; 0 = every row), every
column and every cell whole, and says so when rows were left out. The Agent
also shows the model an automation run's output whole (it was the last 800
characters). Exports write up to 150,000 rows.

Run with The Agent's python:  python tests_v2/unit/test_agent_view_tile_rows.py
"""
import asyncio
import io
import json
import os
import sys
import types
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "agent_service"))
sys.path.insert(0, REPO)
os.chdir(os.path.join(REPO, "agent_service"))

import authoring_tools as A                      # noqa: E402
import export_tools as X                         # noqa: E402
import views_tools as V                          # noqa: E402
from platform_tools import CURRENT_USER          # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _as_developer():
    return CURRENT_USER.set({"user_id": 7, "username": "dev", "role": 2, "session_id": "s"})


def _json_line(n_rows, n_cols=6):
    cols = [f"col_{i}" for i in range(n_cols)]
    rows = [[f"ORD-{r:06d}"] + [f"value {r}-{c}" for c in range(1, n_cols)] for r in range(n_rows)]
    return cols, rows, json.dumps({"columns": cols, "rows": rows})


# ----------------------------------------------------------------- the tile's own row setting
def test_a_tile_keeps_ten_thousand_rows_by_default_and_the_setting_changes_it():
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("VIEW_TILE_ROW_CAP", None)
        assert V._tile_row_cap() == 10000
        os.environ["VIEW_TILE_ROW_CAP"] = "250"
        assert V._tile_row_cap() == 250
        os.environ["VIEW_TILE_ROW_CAP"] = "0"            # no limit
        assert V._tile_row_cap() == 0
        os.environ["VIEW_TILE_ROW_CAP"] = "many"         # unreadable: the default, never a crash
        assert V._tile_row_cap() == 10000


def test_rows_left_out_are_said_so_and_rows_that_fit_are_not_marked():
    cols, rows, _ = _json_line(300)
    with mock.patch.dict(os.environ, {"VIEW_TILE_ROW_CAP": "250"}):
        tile = {}
        V._keep_tile_rows(tile, cols, rows)
        assert len(tile["rows"]) == 250 and tile["row_count"] == 250 and tile["cap_applied"] is True
        tile = {}
        V._keep_tile_rows(tile, cols, rows[:250])        # exactly the limit: nothing left out
        assert len(tile["rows"]) == 250 and "cap_applied" not in tile
    with mock.patch.dict(os.environ, {"VIEW_TILE_ROW_CAP": "0"}):
        tile = {}
        V._keep_tile_rows(tile, cols, rows)
        assert len(tile["rows"]) == 300 and "cap_applied" not in tile


# ----------------------------------------------------------------- automation tiles
def test_the_tile_line_is_read_whole_however_long():
    cols, rows, line = _json_line(4000)
    assert len(line) > 300_000                           # it had to stay under 1,900 characters
    got_cols, got_rows = V._parse_tile_stdout("working...\n" + line + "\n", whole=True)
    assert got_cols == cols and got_rows == rows         # all 4,000 (it kept 50)
    # a list of objects, the other accepted form
    objs = [{"sku": f"S{i}", "qty": i} for i in range(1200)]
    c, r = V._parse_tile_stdout(json.dumps(objs), whole=True)
    assert c == ["sku", "qty"] and len(r) == 1200 and r[-1] == ["S1199", 1199]


def test_no_tile_line_says_what_is_expected_and_names_the_old_main_app_only_when_that_is_the_case():
    for whole in (True, False):
        try:
            V._parse_tile_stdout("no json here\n", whole=whole)
        except ValueError as e:
            text = str(e)
        else:
            raise AssertionError("expected a ValueError")
        assert "LAST stdout line must be JSON" in text
        assert ("2,000 characters" in text) is (not whole)


def _automation_tile(result, env=None):
    seen = {}

    async def fake_manage(action, payload, timeout=0):
        seen["action"], seen["payload"] = action, dict(payload)
        return result, 200

    tile = {}
    token = _as_developer()
    try:
        with mock.patch.object(V, "_manage", fake_manage), \
                mock.patch.dict(os.environ, env or {}):
            _run(V._run_automation_tile({"automation_id": "a-1", "inputs": {"day": "today"}}, tile))
    finally:
        CURRENT_USER.reset(token)
    return tile, seen


def test_an_automation_tile_asks_for_the_whole_output_and_shows_every_row():
    cols, rows, line = _json_line(4000)
    result = {"status": "success", "run_id": "r-1", "exit_code": 0,
              "stdout": "step 1 done\n" + line + "\n", "stdout_tail": line[-2000:]}
    tile, seen = _automation_tile(result)
    assert seen["action"] == "run" and seen["payload"]["full_output"] is True
    assert seen["payload"]["inputs"] == {"day": "today"}
    assert tile.get("error") is None
    assert tile["columns"] == cols and tile["rows"] == rows and tile["row_count"] == 4000
    assert "cap_applied" not in tile and tile["run_id"] == "r-1"


def test_an_automation_tile_with_more_rows_than_a_tile_keeps_says_so():
    _cols, _rows, line = _json_line(600)
    tile, _seen = _automation_tile({"status": "success", "run_id": "r-2", "stdout": line},
                                   env={"VIEW_TILE_ROW_CAP": "500"})
    assert tile["row_count"] == 500 and len(tile["rows"]) == 500 and tile["cap_applied"] is True


def test_with_a_main_app_that_sends_only_the_tail_a_short_line_still_works_and_a_cut_one_explains():
    cols, rows, line = _json_line(5)
    tile, _seen = _automation_tile({"status": "success", "run_id": "r-3", "stdout_tail": "x\n" + line})
    assert tile["rows"] == rows and tile.get("error") is None
    _c, _r, long_line = _json_line(4000)
    tile, _seen = _automation_tile({"status": "success", "run_id": "r-4", "stdout_tail": long_line[-2000:]})
    assert "rows" not in tile
    assert "only the final 2,000 characters" in tile["error"]


# ----------------------------------------------------------------- SQL tiles
def test_a_sql_tile_asks_the_query_seam_for_a_tiles_limits():
    seen = {}
    cols = ["order_no", "notes"]
    back = [{"order_no": f"ORD-{i}", "notes": "n" * 900} for i in range(1500)]

    async def fake_post(path, body, **_kw):
        seen["path"], seen["body"] = path, dict(body)
        return {"success": True, "columns": cols, "rows": back, "row_count": 1500,
                "cap_applied": False, "more_rows": False}, 200

    async def fake_conn(_ref):
        return 7, None

    tile = {}
    with mock.patch.object(V, "_post", fake_post), mock.patch.object(V, "_resolve_connection", fake_conn):
        _run(V._run_sql_tile({"connection": "ERP", "sql": " select order_no, notes from orders "}, tile))
    assert seen["path"] == "/api/discover/query/7"
    assert seen["body"] == {"sql": "select order_no, notes from orders", "purpose": "view_tile"}
    assert len(tile["rows"]) == 1500 and tile["rows"][-1] == ["ORD-1499", "n" * 900]
    assert tile["cap_applied"] is False and tile["row_count"] == 1500


# ----------------------------------------------------------------- the main app's side, from its source
def _source(rel):
    return io.open(os.path.join(REPO, rel), encoding="utf-8").read()


def test_the_query_seam_gives_a_tile_its_own_limits_and_leaves_the_probe_as_it_was():
    src = _source("app.py")
    start = src.index("def discover_query_api(connection_id):")
    body = src[start:src.index("@app.route('/api/discover/schema/<int:connection_id>')", start)]
    assert "tile_mode = str(body.get('purpose') or '') == 'view_tile'" in body
    for name in ("VIEW_TILE_ROW_CAP", "VIEW_TILE_MAX_COLS", "VIEW_TILE_MAX_CELL"):
        assert f"cfg.{name}" in body
    assert "gate_cap = (row_cap + 1) if row_cap else None" in body      # knows whether more rows exist
    assert "more_rows = bool(tile_mode and row_cap and len(df) > row_cap)" in body
    # the probe: still clamped to its own setting, columns and cells from its own settings
    assert "cfg.DISCOVER_QUERY_ROW_CAP))" in body
    assert "max_cols, max_cell, gate_cap = cfg.DISCOVER_QUERY_MAX_COLS, cfg.DISCOVER_QUERY_MAX_CELL, row_cap" in body
    cfg_src = _source("config.py")
    assert "VIEW_TILE_ROW_CAP = int(os.getenv('VIEW_TILE_ROW_CAP', '10000'))" in cfg_src
    assert "VIEW_TILE_MAX_COLS = int(os.getenv('VIEW_TILE_MAX_COLS', '0'))" in cfg_src
    assert "VIEW_TILE_MAX_CELL = int(os.getenv('VIEW_TILE_MAX_CELL', '0'))" in cfg_src
    assert "DISCOVER_QUERY_ROW_CAP = int(os.getenv('DISCOVER_QUERY_ROW_CAP', '50'))" in cfg_src


def test_the_runner_hands_out_the_whole_output_only_to_a_caller_that_asks():
    src = _source("automations/runner.py")
    assert '"_output": (stdout, stderr),' in src
    assert 'whole = result.pop("_output", None)' in src
    assert 'if full_output and whole:' in src
    assert src.count('result.pop("_output", None)') == 2           # run() and run_code_step()
    assert '"stdout_tail": stdout[-2000:], "stderr_tail": stderr[-2000:],' in src   # everyone else: unchanged
    api = _source("automations/api.py")
    assert 'whole_output = {"full_output": True} if payload.get("full_output") else {}' in api
    assert "full_output=bool(full_output))" in api


# ----------------------------------------------------------------- what the model is shown of a run
def test_the_model_sees_a_runs_output_whole():
    out = "\n".join(f"line {i:05d} of the run" for i in range(3000)) + "\nRESULT: 41 files moved"
    text = A._summarize_run({"status": "success", "exit_code": 0, "run_id": "r", "version": 3,
                             "stdout": out, "stdout_tail": out[-2000:]})
    assert "--- stdout ---\n" + out in text                        # it was the last 800 characters
    assert "line 00000 of the run" in text and text.endswith("RESULT: 41 files moved")
    # stderr only for a run that did not succeed, and whole
    err = "Traceback...\n" + "x" * 5000 + "\nValueError: bad input"
    ok = A._summarize_run({"status": "success", "stdout": "fine", "stderr": err})
    assert "stderr" not in ok
    bad = A._summarize_run({"status": "failed", "stdout": "", "stderr": err})
    assert "--- stderr ---\n" + err in bad and "--- stdout" not in bad


def test_from_a_main_app_that_sends_only_tails_the_model_gets_all_of_the_tail():
    tail = "t" * 1990 + "END-OF-RUN"
    text = A._summarize_run({"status": "failed", "stdout_tail": tail, "stderr_tail": "E" * 2000})
    assert "--- stdout (the last 2,000 characters) ---\n" + tail in text
    assert "--- stderr (the last 2,000 characters) ---\n" + "E" * 2000 in text


def test_running_an_automation_asks_for_the_whole_output():
    seen = {}

    async def fake_manage(action, payload, timeout=0):
        seen[action] = dict(payload)
        return {"status": "success", "run_id": "r-9", "exit_code": 0, "version": 1,
                "stdout": "A" * 60_000 + "\nDONE"}, 200

    async def fake_resolve(ref):
        return "auto-guid", None

    async def no_epilogue(_auto_id):
        return ""

    token = _as_developer()
    try:
        with mock.patch.object(A, "_manage", fake_manage), \
                mock.patch.object(A, "_resolve_automation", fake_resolve), \
                mock.patch.object(A, "_authoring_allowed", lambda: True), \
                mock.patch.object(A, "_ephemeral_epilogue", no_epilogue):
            res = _run(A._run_action("run", "my automation", ""))
    finally:
        CURRENT_USER.reset(token)
    assert seen["run"]["full_output"] is True
    text = res["content"][0]["text"]
    assert "A" * 60_000 + "\nDONE" in text


# ----------------------------------------------------------------- exports
def test_an_export_writes_up_to_150000_rows():
    src = _source("agent_service/export_tools.py")
    assert 'MAX_ROWS = int(os.getenv("AGENT_EXPORT_MAX_ROWS", "150000"))' in src
    if not os.getenv("AGENT_EXPORT_MAX_ROWS"):
        assert X.MAX_ROWS == 150000
    code = X.compose_export_code("csv", "orders.csv", connection="ERP", sql="select 1")
    assert f"_rows[:{X.MAX_ROWS}]" in code and f"truncated = len(_rows) > {X.MAX_ROWS}" in code


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS", name)
        except Exception as e:            # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
