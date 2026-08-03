"""probe_connection_query — letting the code generator check its own SQL.

Schema grounding answers "what is this column called". Value enumeration answers
"what is inside it". Neither answers the question a dead filter fails:

    does the query I just wrote actually match anything?

Live failure: a generated dunning automation filtered
`activity_type = 'promise_to_pay'` against a column holding 'ptp'. Zero rows, no
error, a promise-to-pay hold silently disabled, and a customer who had already
promised to pay received a dunning letter. Running that query once during
authoring would have shown 0 rows and ended it there.

Bounded on purpose — not because the deployment is hostile (these are trusted
on-prem installs) but because an unbounded result makes the model worse at its
job: a thousand rows of free text buries the answer and burns the context needed
to write the code.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Read the source by path rather than importing it: builder_service and
# command_center_service both ship a `graph` package, so importing one of them
# here would fight the other suites over sys.modules. These are source contracts,
# so the text is all they need.
NODES = ROOT / "command_center_service" / "graph" / "nodes.py"


def nodes_src():
    return NODES.read_text(encoding="utf-8")


def tool_src():
    src = nodes_src()
    start = src.find("async def probe_connection_query")
    assert start > 0, "probe_connection_query is not defined"
    return src[start:src.find("async def unwire_workflow_nodes", start)]


def app_src():
    return (ROOT / "app.py").read_text(encoding="utf-8")


def endpoint_src():
    s = app_src()
    start = s.find("def discover_query_api")
    assert start > 0, "/api/discover/query endpoint is not defined"
    return s[start:s.find("\n@app.route", start)]


# ------------------------------------------------------------------ read-only
def test_endpoint_runs_everything_through_the_shared_gate():
    """sql_gate is already hardened and already in production for the NLQ agent.
    A second hand-rolled validator is how the two drift apart."""
    e = endpoint_src()
    assert "from sql_gate import gate_sql" in e
    assert "gate_sql(" in e
    assert "gate.ok" in e, "the gate verdict must be checked"
    assert "gate.sql" in e, "the GATED sql must be what executes, not the raw input"


def test_rejection_is_an_answer_not_a_server_error():
    """The agent should read the reason and revise, which it cannot do if the
    call blows up as a 4xx/5xx body it never sees."""
    e = endpoint_src()
    i = e.find("gate.ok")
    seg = e[i:i + 400]
    assert "'rejected': True" in seg
    assert "500" not in seg and "400" not in seg


def test_tool_tells_the_model_how_to_recover_from_a_rejection():
    t = tool_src()
    assert "QUERY REJECTED" in t
    assert "read-only SELECT" in t
    assert "Revise and try again" in t


# --------------------------------------------------------------------- bounds
def test_rows_columns_and_cells_are_all_capped():
    e = endpoint_src()
    assert "cfg.DISCOVER_QUERY_ROW_CAP" in e
    assert "cfg.DISCOVER_QUERY_MAX_COLS" in e
    assert "cfg.DISCOVER_QUERY_MAX_CELL" in e, \
        "a single wide text cell must not be able to dominate the reply"


def test_caller_cannot_raise_the_row_cap_above_the_configured_ceiling():
    e = endpoint_src()
    assert "min(int(body.get('row_cap')" in e and "cfg.DISCOVER_QUERY_ROW_CAP))" in e


def test_caps_are_configurable():
    import config as cfg
    for name in ("DISCOVER_QUERY_ROW_CAP", "DISCOVER_QUERY_MAX_COLS",
                 "DISCOVER_QUERY_MAX_CELL"):
        assert isinstance(getattr(cfg, name), int) and getattr(cfg, name) > 0


def test_truncation_is_reported_rather_than_silent():
    """The failure this whole change exists to fix was silent truncation of a
    different kind. Don't reintroduce it."""
    e = endpoint_src()
    assert "truncated_columns" in e and "cap_applied" in e
    t = tool_src()
    assert "capped at" in t and "more columns not shown" in t and "showing first" in t


# ------------------------------------------------------------- the whole point
def test_zero_rows_is_treated_as_a_finding_not_a_result():
    """0 rows is the shape of the live bug. It must read as a problem, and must
    name the likely cause, or the model will shrug and save the query anyway."""
    t = tool_src()
    i = t.find("if not n:")
    assert i > 0, "there must be an explicit zero-row branch"
    seg = t[i:i + 700]
    assert "0 ROWS" in seg
    assert "matches nothing" in seg
    # Adjacent literals wrap across source lines, so match a contiguous fragment.
    assert "value that does not exist in the data" in seg
    assert "get_connection_schema" in seg, "point at where the real values are"
    assert "Do NOT save" in seg


def test_prompt_orders_the_model_to_prove_the_query():
    src = nodes_src()
    assert "PROVE THE QUERY" in src
    assert "probe_connection_query" in src
    i = src.find("PROVE THE QUERY")
    seg = src[i:i + 500]
    assert "0 rows" in seg and "BEFORE saving" in seg


# ------------------------------------------------------------------- gating
def test_tool_is_developer_gated_like_the_rest_of_discovery():
    t = tool_src()
    assert "_automations_allowed(state)" in t and "_workflow_tools_allowed(state)" in t
    assert "Developer/Admin users only" in t


def test_endpoint_carries_the_same_role_gate_as_schema_discovery():
    s = app_src()
    i = s.find("@app.route('/api/discover/query/<int:connection_id>'")
    assert i > 0
    assert "api_key_or_session_required(min_role=2)" in s[i:i + 300]


def test_tool_is_registered_next_to_schema_grounding():
    src = nodes_src()
    assert "tools.append(probe_connection_query)" in src
    assert '"probe_connection_query": probe_connection_query,' in src, \
        "must also be in the dispatch map or the call will not resolve"


def test_registration_shares_the_schema_tool_condition():
    """Bound under the same gate as get_connection_schema — a probe available on
    a turn that cannot author SQL would be pure surface with no purpose."""
    src = nodes_src()
    i = src.find("tools.append(get_connection_schema)")
    assert 0 < i and "tools.append(probe_connection_query)" in src[i:i + 500]


@pytest.mark.parametrize("bad", [
    "DELETE FROM Invoices",
    "UPDATE t SET x = 1",
    "DROP TABLE t",
    "EXEC sp_who",
    "SELECT 1; DROP TABLE t",
])
def test_gate_actually_rejects_mutations(bad):
    """Not a mock: the real gate, on the real strings."""
    from sql_gate import gate_sql
    assert not gate_sql(bad, database_type="sqlserver", row_cap=50).ok


@pytest.mark.parametrize("good", [
    "SELECT TOP 5 * FROM dbo.Invoices",
    "SELECT DISTINCT activity_type FROM dbo.CG_CollectionActivity",
])
def test_gate_allows_plain_selects(good):
    from sql_gate import gate_sql
    assert gate_sql(good, database_type="sqlserver", row_cap=50).ok
