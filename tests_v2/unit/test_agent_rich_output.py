"""Unit pack for pass 2 — rich output (agent_service/rich_blocks.py, the
probe_connection_query chart parameter, run_python's inline image lines, and
the frontend contract pinned as text).

Runs standalone (aihub-agent python) or under pytest; self-skips without the SDK.
"""
import asyncio
import json
import os
import re
import sys
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import rich_blocks as RB                   # noqa: E402
    import platform_tools as P                 # noqa: E402
    HAVE_SDK = True
except ImportError as e:
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(
            reason=f"needs the aihub-agent env (claude_agent_sdk): {_IMPORT_ERR}")
    except ImportError:
        pass

INDEX = os.path.join(APP_ROOT, "agent_service", "static", "index.html")


def _run(coro):
    return asyncio.run(coro)


def _txt(res):
    return res["content"][0]["text"]


def _spec(block, uid=0):
    """(kind, spec) — a {"ref"} fence (tool-built) resolves through the store."""
    import rich_blocks
    m = re.search(r"```aihub-(\w+)\n(.*?)\n```", block, re.S)
    assert m, block
    kind, spec = m.group(1), json.loads(m.group(2))
    if "ref" in spec:
        hit = rich_blocks.get_block(uid, spec["ref"])
        assert hit and hit["kind"] == kind, spec
        return kind, hit["spec"]
    return kind, spec


# ---------------------------------------------------------------------------
# rich_blocks
# ---------------------------------------------------------------------------

def test_chart_from_rows_picks_label_and_numeric_series():
    cols = ["region", "revenue", "orders", "note"]
    rows = [{"region": "East", "revenue": "1,200.5", "orders": 10, "note": "x"},
            {"region": "West", "revenue": 900, "orders": None, "note": "y"}]
    block, note = RB.chart_from_rows(cols, rows, "bar", title="Revenue by region")
    kind, spec = _spec(block)
    assert kind == "chart" and spec["type"] == "bar" and spec["title"] == "Revenue by region"
    assert spec["labels"] == ["East", "West"]
    assert [s["name"] for s in spec["series"]] == ["revenue", "orders"]   # 'note' is text -> not a series
    assert spec["series"][0]["data"] == [1200.5, 900.0]
    assert spec["series"][1]["data"] == [10.0, None]
    assert "2 point(s)" in note


def test_chart_from_rows_handles_lists_pies_caps_and_refusals():
    cols = ["month", "sales"]
    rows = [[f"M{i}", i * 10] for i in range(80)]
    block, note = RB.chart_from_rows(cols, rows, "line")
    _k, spec = _spec(block)
    assert len(spec["labels"]) == RB.MAX_POINTS and "first 60 rows" in note
    assert spec["yLabel"] == "sales"                                     # single series names the axis
    block, note = RB.chart_from_rows(["a", "b", "c"], [[1, 2, 3]], "pie")
    _k, spec = _spec(block)
    assert spec["type"] == "pie" and len(spec["series"]) == 1             # pies take one series
    assert spec["labels"] == ["1"]                                        # all-numeric: first column labels
    block, note = RB.chart_from_rows(["name", "city"], [["a", "b"]], "bar")
    assert block is None and "Nothing numeric" in note
    block, note = RB.chart_from_rows(["a"], [], "bar")
    assert block is None and "No rows" in note
    block, note = RB.chart_from_rows(["a", "b"], [[1, 2]], "sparkline")
    assert block is None and "not a chart type" in note


def test_kpi_block_and_image_lines():
    kind, spec = _spec(RB.kpi_block([{"label": "Open orders", "value": 1204, "trend": "+5%", "direction": "UP"},
                                     {"label": "", "value": 1}, {"label": "Late", "trendDirection": "down"}]))
    assert kind == "kpi"
    assert spec["cards"][0] == {"label": "Open orders", "value": "1204", "trend": "+5%", "direction": "up"}
    assert spec["cards"][1] == {"label": "Late", "value": "", "direction": "down"}
    links = ["[⤓ chart.png (12.3 KB)](/api/files/0f1e2d3c-1111-2222-3333-444455556666)",
             "[⤓ data.csv (1.0 KB)](/api/files/0f1e2d3c-1111-2222-3333-444455556677)",
             "not a link"]
    assert RB.image_lines(links) == ["![chart.png](/api/files/0f1e2d3c-1111-2222-3333-444455556666)"]


# ---------------------------------------------------------------------------
# probe_connection_query(chart=…)
# ---------------------------------------------------------------------------

def test_probe_query_chart_parameter_appends_a_verbatim_block():
    async def fake_resolve(ref):
        return "7", None

    async def fake_post(path, body, timeout=None):
        assert path == "/api/discover/query/7"
        return {"success": True, "columns": ["status", "n"],
                "rows": [{"status": "open", "n": 5}, {"status": "closed", "n": 12}],
                "row_count": 2}, 200

    with mock.patch.object(P, "_resolve_connection", fake_resolve), \
         mock.patch.object(P, "_post", fake_post):
        res = _run(P.probe_connection_query.handler({"connection": "ERPDB", "sql": "select 1",
                                                     "chart": "pie", "chart_title": "Orders by status"}))
        out = _txt(res)
        assert "status | n" in out and "EXACTLY" in out and '{"ref": "' in out
        kind, spec = _spec(out)
        assert kind == "chart" and spec["type"] == "pie" and spec["labels"] == ["open", "closed"]
        assert spec["series"][0]["data"] == [5.0, 12.0] and spec["title"] == "Orders by status"
        # no chart requested -> unchanged plain output
        res = _run(P.probe_connection_query.handler({"connection": "ERPDB", "sql": "select 1"}))
        assert "aihub-chart" not in _txt(res)
        # unchartable shape -> honest note, table still returned
        res = _run(P.probe_connection_query.handler({"connection": "ERPDB", "sql": "select 1",
                                                     "chart": "sparkline"}))
        assert "not a chart type" in _txt(res) and "status | n" in _txt(res)


# ---------------------------------------------------------------------------
# Frontend contract (pinned as text so a refactor can't silently drop it)
# ---------------------------------------------------------------------------

def test_build_options_scopes_the_skill_tool_to_mounted_skills():
    import brain
    opts = brain.build_options(None, "full", skill_names=["aihub-rich-output", "my-preferences"])
    assert opts.skills == ["aihub-rich-output", "my-preferences"]
    assert "Skill" not in opts.allowed_tools            # bare Skill would allow the CLI's bundled skills
    with mock.patch.dict(os.environ, {"AGENT_SKILLS_SCOPE": "all"}):
        opts = brain.build_options(None, "full", skill_names=["x"])
        assert opts.skills == "all" and "Skill" in opts.allowed_tools
    opts = brain.build_options(None, "read", skill_names=None)          # no mount info -> old posture
    assert opts.skills == "all" and "Skill" in opts.allowed_tools


def test_frontend_has_the_renderer_and_vendored_chartjs():
    html = open(INDEX, encoding="utf-8").read()
    assert 'src="/static/vendor/chart.umd.min.js"' in html
    assert os.path.getsize(os.path.join(APP_ROOT, "agent_service", "static", "vendor",
                                        "chart.umd.min.js")) > 150_000
    for needle in ("aihub-chart", "aihub-kpi", "function mountRichBlocks", "function inlineImages",
                   'img[src^="/api/files/"]', "rblock-src", "Chart.getChart"):
        assert needle in html, needle
    # images are fetched with the auth header, never with a token in the URL
    assert "URL.createObjectURL" in html and "?token=" not in html.split("function inlineImages")[1][:2500]


if __name__ == "__main__":
    if not HAVE_SDK:
        print(f"SKIP-ALL: {_IMPORT_ERR}")
        sys.exit(0)
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in fns:
        try:
            f()
            print(f"PASS  {n}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {n}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {n}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
