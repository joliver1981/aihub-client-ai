"""
The Agent's own data query hands the model what the query returned (2026-11-26).

probe_connection_query went through the limits of the code-writing probe (50
rows, 25 columns, 200 characters a cell), and then showed the model only the
first 15 rows while the note under them said "50 rows returned". A long cell
arrived cut, a 26th column did not arrive, and the model was told it had rows
it had never seen.

Now: every row that came back is shown, every column and every cell is whole,
the model can ask for more rows (max_rows), and "the query returns more than
these" is said only when it is true. The code-writing probe is unchanged.

Run with The Agent's python:  python tests_v2/unit/test_agent_data_query_whole.py
"""
import asyncio
import io
import logging
import os
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "agent_service"))
sys.path.insert(0, REPO)
os.chdir(os.path.join(REPO, "agent_service"))

import platform_tools as P  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _txt(res):
    return res["content"][0]["text"]


def _rows(n, cols=("order_no", "customer", "notes"), note_len=30):
    return [{cols[0]: f"ORD-{i:05d}", cols[1]: f"Customer {i}", cols[2]: f"note {i} " + "x" * note_len}
            for i in range(1, n + 1)]


def _ask(reply, args=None, env=None):
    """Call the tool with the main app's reply faked; returns (text, request body sent)."""
    seen = {}

    async def fake_resolve(ref):
        return {"id": "20", "name": "ERPDB"}, None

    async def fake_post(path, body, timeout=None):
        seen["path"], seen["body"] = path, dict(body)
        return (reply(body) if callable(reply) else reply), 200

    with mock.patch.object(P, "_resolve_connection_row", fake_resolve), \
            mock.patch.object(P, "_post", fake_post), \
            mock.patch.dict(os.environ, env or {}):
        res = _run(P.probe_connection_query.handler(dict({"connection": "ERPDB", "sql": " select * from orders "},
                                                         **(args or {}))))
    return _txt(res), seen.get("body")


def _new_main_app(total, ceiling=10000, cols=("order_no", "customer", "notes"), note_len=30):
    """A main app that knows The Agent's query: returns what was asked for, says whether more exist."""
    def reply(body):
        cap = min(int(body.get("row_cap") or 50), ceiling)
        rows = _rows(min(total, cap), cols, note_len)
        return {"success": True, "columns": list(cols), "rows": rows, "row_count": len(rows),
                "cap_applied": total > cap, "more_rows": total > cap, "row_cap": cap,
                "row_ceiling": ceiling, "truncated_columns": 0}
    return reply


# ----------------------------------------------------------------- what is asked of the main app
def test_it_asks_for_the_agents_own_limits_and_50_rows_by_default():
    _out, body = _ask(_new_main_app(3))
    assert body == {"sql": "select * from orders", "purpose": "agent_query", "row_cap": 50}


def test_the_model_can_ask_for_more_rows_and_a_bad_number_falls_back_to_50():
    assert _ask(_new_main_app(3), {"max_rows": 4000})[1]["row_cap"] == 4000
    assert _ask(_new_main_app(3), {"max_rows": "lots"})[1]["row_cap"] == 50
    assert _ask(_new_main_app(3), {"max_rows": 0})[1]["row_cap"] == 50
    assert _ask(_new_main_app(3), {"max_rows": -5})[1]["row_cap"] == 1


def test_the_switch_sends_the_request_the_old_way():
    _out, body = _ask(_new_main_app(3), {"max_rows": 500}, env={"AGENT_QUERY_SIZED": "false"})
    assert body == {"sql": "select * from orders"}


# ----------------------------------------------------------------- what the model is shown
def test_every_row_that_came_back_is_shown_not_the_first_15():
    out, _b = _ask(_new_main_app(50))
    lines = [ln for ln in out.splitlines() if ln.startswith("ORD-")]
    assert len(lines) == 50 and lines[0].startswith("ORD-00001") and lines[-1].startswith("ORD-00050")
    assert "(50 rows returned, all of them shown)" in out


def test_a_long_cell_and_every_column_arrive_whole():
    cols = tuple(f"c{i:02d}" for i in range(40))

    def reply(body):
        row = {c: f"value of {c}" for c in cols}
        row["c05"] = "START " + "long text " * 400 + "END-OF-CELL"
        return {"success": True, "columns": list(cols), "rows": [row], "row_count": 1,
                "cap_applied": False, "more_rows": False, "row_cap": 50, "row_ceiling": 10000,
                "truncated_columns": 0}
    out, _b = _ask(reply)
    assert out.splitlines()[0] == " | ".join(cols)                 # all 40 column names
    assert "END-OF-CELL" in out and "value of c39" in out


def test_more_rows_than_shown_is_said_with_what_to_do():
    out, _b = _ask(_new_main_app(700))
    assert len([ln for ln in out.splitlines() if ln.startswith("ORD-")]) == 50
    assert "The first 50 rows are shown and the query returns MORE than these." in out
    assert "You have not seen all of them: call again with a larger max_rows (up to 10,000)" in out
    out, _b = _ask(_new_main_app(700), {"max_rows": 1000})
    assert len([ln for ln in out.splitlines() if ln.startswith("ORD-")]) == 700
    assert "(700 rows returned, all of them shown)" in out


def test_at_the_most_one_query_returns_it_points_to_aggregates_and_export():
    out, _b = _ask(_new_main_app(900, ceiling=300), {"max_rows": 5000})
    assert len([ln for ln in out.splitlines() if ln.startswith("ORD-")]) == 300
    assert "The first 300 rows are shown and the query returns MORE than these." in out
    assert "300 is the most one query returns here" in out and "export_data" in out
    assert "call again with a larger max_rows" not in out


def test_with_a_main_app_that_does_not_know_the_agents_query_all_its_rows_are_still_shown():
    old = {"success": True, "columns": ["order_no", "customer", "notes"], "rows": _rows(50),
           "row_count": 50, "cap_applied": True, "row_cap": 50, "truncated_columns": 3}
    out, body = _ask(old)
    assert body["purpose"] == "agent_query"                        # asked for; that main app ignores it
    assert len([ln for ln in out.splitlines() if ln.startswith("ORD-")]) == 50      # it used to be 15
    assert "(50 rows returned, server cap 50 applied; 3 more column(s) were NOT returned" in out


def test_zero_rows_and_a_chart_work_as_before():
    out, _b = _ask(_new_main_app(0))
    assert out.startswith("0 rows returned from ERPDB.")

    def reply(body):
        return {"success": True, "columns": ["status", "n"],
                "rows": [{"status": "open", "n": 5}, {"status": "closed", "n": 12}], "row_count": 2,
                "more_rows": False, "row_ceiling": 10000}
    out, _b = _ask(reply, {"chart": "pie", "chart_title": "Orders by status"})
    assert "status | n" in out and "open | 5" in out and "aihub-chart" in out


def test_the_tool_tells_the_model_how_to_get_more():
    desc = P.probe_connection_query.description
    assert "every column and every cell whole" in desc and "max_rows" in desc
    assert "never answer as if the rows shown were all" in desc
    assert "caps rows (~50)" not in desc
    assert "max_rows" in P.probe_connection_query.input_schema["properties"]


# ----------------------------------------------------------------- the main app's side, from its source
def _source(rel):
    return io.open(os.path.join(REPO, rel), encoding="utf-8").read()


def test_the_seam_sizes_the_agents_query_and_leaves_the_code_writing_probe_alone():
    src = _source("app.py")
    start = src.index("def discover_query_api(connection_id):")
    body = src[start:src.index("@app.route('/api/discover/schema/<int:connection_id>')", start)]
    assert "agent_mode = str(body.get('purpose') or '') == 'agent_query'" in body
    assert "row_cap = min(asked, row_ceiling) if row_ceiling else asked" in body
    for name in ("AGENT_QUERY_MAX_ROWS", "AGENT_QUERY_MAX_COLS", "AGENT_QUERY_MAX_CELL"):
        assert f"cfg.{name}" in body
    assert "more_rows = bool(sized_mode and row_cap and len(df) > row_cap)" in body
    assert "'row_ceiling': row_ceiling," in body
    # the probe: still clamped to its own row setting before either purpose is looked at
    assert body.index("cfg.DISCOVER_QUERY_ROW_CAP))") < body.index("tile_mode =") < body.index("agent_mode =")
    cfg_src = _source("config.py")
    assert "AGENT_QUERY_MAX_ROWS = int(os.getenv('AGENT_QUERY_MAX_ROWS', '10000'))" in cfg_src
    assert "AGENT_QUERY_MAX_COLS = int(os.getenv('AGENT_QUERY_MAX_COLS', '0'))" in cfg_src
    assert "AGENT_QUERY_MAX_CELL = int(os.getenv('AGENT_QUERY_MAX_CELL', '0'))" in cfg_src
    assert "DISCOVER_QUERY_MAX_CELL = int(os.getenv('DISCOVER_QUERY_MAX_CELL', '200'))" in cfg_src


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS", name)
        except Exception as e:            # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {str(e)[:300]}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
