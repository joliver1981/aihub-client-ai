"""Unit pack for the 2026-09-05 scope-honesty fixes
(docs/handoff-three-usability-defects.md):

#1 connection resolver ladder (exact -> base name -> unique prefix/substring,
   honest ambiguity), per-turn search coverage, and brain's coverage guard;
#2 doctrine: skills are enrichment, never silent scope (prompt + save_skill);
#3 automation / code-flow runs hand produced files over as /api/files links
   deterministically (the run summary carries them, like run_python does).

Runs standalone (aihub-agent python test_agent_scope_honesty.py) or under
pytest; self-skips without the SDK.
"""
import asyncio
import os
import shutil
import sys
import tempfile
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import platform_tools as P                 # noqa: E402
    import authoring_tools as A                # noqa: E402
    import export_tools as X                   # noqa: E402
    import work_tools as W                     # noqa: E402
    import brain as B                          # noqa: E402
    import file_tools                          # noqa: E402
    from platform_tools import CURRENT_USER    # noqa: E402
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

# The box's real connection names on 2026-09-05 — the shapes that broke live.
LIVE = [
    {"id": 5, "name": "EDW (SQL Server)", "type": None, "database": "LLMDB"},
    {"id": 20, "name": "ERPDB", "type": None, "database": "ERPDB"},
    {"id": 28, "name": "EDWDB (Postgres)", "type": None, "database": "edwdb"},
    {"id": 58, "name": "AIRDB", "type": None, "database": "AIRDB"},
    {"id": 59, "name": "AIRDB2", "type": None, "database": "AIRDB2"},
    {"id": 168, "name": "PHARMA", "type": None, "database": "PHARMA"},
]
KNOWN = [c["name"] for c in LIVE]
TEST_UID = 990001   # throwaway user id for staging checks (removed after)


def _run(coro):
    return asyncio.run(coro)


def _txt(res):
    return " ".join(p.get("text", "") for p in res.get("content", [])
                    if p.get("type") == "text")


async def _fake_index():
    return list(LIVE)


def _tmp_under_root():
    base = os.path.join(APP_ROOT, "temp", "agent_tests")
    os.makedirs(base, exist_ok=True)
    return tempfile.mkdtemp(prefix="scope_", dir=base)


def _cleanup_user(uid):
    shutil.rmtree(os.path.join(file_tools.USERS_DIR, str(uid)), ignore_errors=True)


# ---------------------------------------------------------------------------
# #1 resolver ladder
# ---------------------------------------------------------------------------

def test_match_connection_ladder():
    m = P.match_connection
    assert m("ERPDB", LIVE)[0]["id"] == 20                 # exact
    assert m("erpdb", LIVE)[0]["id"] == 20                 # case-insensitive
    assert m("20", LIVE)[0]["id"] == 20                    # numeric id
    assert m("EDW", LIVE)[0]["id"] == 5                    # base name beats prefix-of-two
    assert m("edwdb", LIVE)[0]["id"] == 28                 # base name
    assert m("AIRDB", LIVE)[0]["id"] == 58                 # exact beats prefix of AIRDB2
    assert m("PHAR", LIVE)[0]["id"] == 168                 # unique prefix
    assert m("Postgres", LIVE)[0]["id"] == 28              # unique substring
    row, err = m("AIR", LIVE)                              # ambiguous prefix
    assert row is None and "ambiguous" in err
    assert "AIRDB (id 58)" in err and "AIRDB2 (id 59)" in err
    row, err = m("Nope", LIVE)
    assert row is None and err.startswith("No connection named 'Nope'")
    assert "EDWDB (Postgres)" in err
    row, err = m("999", LIVE)
    assert row is None and "No connection with id 999" in err
    assert m("7", [])[0] == {"id": "7", "name": "7"}       # index unavailable: pass-through
    row, err = m("", LIVE)
    assert row is None and "No connection given" in err
    row, err = m("E", LIVE)                                # single chars never fuzzy-match
    assert row is None and "No connection named" in err
    # duplicate names: first exact wins (pre-ladder behavior preserved)
    dup = LIVE + [{"id": 900, "name": "ERPDB", "type": None, "database": "x"}]
    assert m("ERPDB", dup)[0]["id"] == 20
    # the old resolver's contract still holds for callers that want the id
    with mock.patch.object(P, "_connections_index", _fake_index):
        assert _run(P._resolve_connection("EDWDB")) == ("28", None)
        cid, err = _run(P._resolve_connection("AIR"))
        assert cid is None and "ambiguous" in err


def test_resolution_note_only_for_fuzzy_hits():
    row = LIVE[0]
    assert P._resolution_note("EDW (SQL Server)", row) == ""
    assert P._resolution_note("edw (sql server)", row) == ""
    assert P._resolution_note("5", row) == ""
    assert "resolved to 'EDW (SQL Server)', id 5" in P._resolution_note("EDW", row)


def test_schema_and_probe_resolve_fuzzy_names_and_echo_them():
    tok = CURRENT_USER.set({"user_id": 7, "role": 2, "username": "dev"})
    calls = []

    async def fake_get(path):
        calls.append(path)
        return {"tables": [{"TABLE_NAME": "Sales"}]}

    async def fake_post(path, body, timeout=None):
        calls.append(path)
        return {"success": True, "columns": ["n"], "rows": [{"n": 1}],
                "row_count": 1}, 200

    try:
        with mock.patch.object(P, "_connections_index", _fake_index), \
             mock.patch.object(P, "_get", fake_get), \
             mock.patch.object(P, "_post", fake_post):
            res = _run(P.get_connection_schema.handler({"connection": "EDWDB"}))
            assert not res.get("is_error"), _txt(res)
            out = _txt(res)
            assert "resolved to 'EDWDB (Postgres)', id 28" in out
            assert calls[-1] == "/api/discover/tables/28"
            assert "Tables on connection 28 (EDWDB (Postgres))" in out
            res = _run(P.get_connection_schema.handler({"connection": "EDW"}))
            assert calls[-1] == "/api/discover/tables/5"
            assert "resolved to 'EDW (SQL Server)'" in _txt(res)
            res = _run(P.probe_connection_query.handler({"connection": "edw",
                                                         "sql": "select 1"}))
            assert not res.get("is_error") and calls[-1] == "/api/discover/query/5"
            assert _txt(res).startswith(
                "(connection 'edw' resolved to 'EDW (SQL Server)', id 5)")
            # exact name: no resolution line, output shape unchanged
            res = _run(P.probe_connection_query.handler({"connection": "ERPDB",
                                                         "sql": "select 1"}))
            assert _txt(res).startswith("n\n1")
            res = _run(P.get_connection_schema.handler({"connection": "AIR"}))
            assert res.get("is_error") and "ambiguous" in _txt(res)
            res = _run(P.probe_connection_query.handler({"connection": "Nope",
                                                         "sql": "select 1"}))
            assert res.get("is_error") and "No connection named 'Nope'" in _txt(res)
    finally:
        CURRENT_USER.reset(tok)


# ---------------------------------------------------------------------------
# #1 per-turn coverage record
# ---------------------------------------------------------------------------

def test_coverage_record_lives_on_the_turn_envelope_only():
    # the module default envelope is never written to (no cross-user leak)
    P.note_known_connections(LIVE)
    P.note_queried_connection("ERPDB")
    assert P.coverage_snapshot(P._DEFAULT_USER) == ([], [])
    assert "_coverage" not in P._DEFAULT_USER
    ctx = {"user_id": 7, "role": 2, "username": "dev"}
    tok = CURRENT_USER.set(ctx)
    try:
        assert P.coverage_snapshot(ctx) == ([], [])
        P.note_known_connections(LIVE)
        P.note_queried_connection("ERPDB")
        P.note_queried_connection("ERPDB")           # dedupe
        P.note_queried_connection("EDW (SQL Server)")
        assert P.coverage_snapshot(ctx) == (KNOWN, ["ERPDB", "EDW (SQL Server)"])
        assert P.coverage_snapshot() == (KNOWN, ["ERPDB", "EDW (SQL Server)"])
        P.reset_coverage(ctx)
        assert P.coverage_snapshot(ctx) == ([], [])
    finally:
        CURRENT_USER.reset(tok)


def test_probe_records_coverage_including_zero_rows():
    ctx = {"user_id": 7, "role": 2, "username": "dev"}
    tok = CURRENT_USER.set(ctx)

    async def fake_post(path, body, timeout=None):
        return {"success": True, "columns": ["n"], "rows": [], "row_count": 0}, 200

    try:
        with mock.patch.object(P, "_connections_index", _fake_index), \
             mock.patch.object(P, "_post", fake_post):
            res = _run(P.probe_connection_query.handler({"connection": "AIRDB",
                                                         "sql": "select 1"}))
            out = _txt(res)
            assert "0 rows returned from AIRDB" in out and "OTHER connections" in out
            assert P.coverage_snapshot(ctx) == (KNOWN, ["AIRDB"])
            # an unresolved name is NOT a checked source
            _run(P.probe_connection_query.handler({"connection": "Nope", "sql": "select 1"}))
            assert P.coverage_snapshot(ctx)[1] == ["AIRDB"]
    finally:
        CURRENT_USER.reset(tok)


def test_list_connections_carries_the_coverage_rule():
    with mock.patch.object(P, "_connections_index", _fake_index):
        out = _txt(_run(P.list_data_connections.handler({})))
    assert "id 5 — EDW (SQL Server)" in out and "Coverage rule" in out
    assert "None" not in out


# ---------------------------------------------------------------------------
# #1 brain coverage guard
# ---------------------------------------------------------------------------

def test_absence_claim_regex():
    yes = [
        "There's no sales data for August 2026 yet.",
        "Nothing has been recorded for August.",
        "August 2026 has no recorded sales in the warehouse.",
        "Summit Provisions (CGC-010): no open balance.",
        "The table doesn't contain any rows for August.",
        "Sales for August have not been loaded yet.",
        "There are no invoices past due for that customer.",
        "The query returned zero rows for August.",
    ]
    no = [
        "July revenue was $18,150 across 3 stores.",
        "I found 27 past-due invoices totalling $267,089.90.",
        "No problem — here is the August total: $5.4M.",
        "Fairmont is on credit hold; do not dun.",
    ]
    for t in yes:
        assert B.claims_absence(t), t
    for t in no:
        assert not B.claims_absence(t), t


def test_coverage_warning_fires_on_the_live_shape_and_stays_quiet_otherwise():
    prompt = ("[Context: now 2026-09-05 10:00 (America/New_York)]\n\n"
              "How were sales last month?")
    reply = ("**There's no sales data for August 2026 yet** — nothing has been "
             "recorded for August.")
    queried = ["ERPDB", "EDW (SQL Server)"]
    note = B.coverage_warning(prompt, reply, KNOWN, queried,
                              ["list_data_connections", "probe_connection_query"])
    assert note and "only 2 of 6 connections were queried" in note
    assert "(EDW (SQL Server), ERPDB)" in note
    assert "Not queried: EDWDB (Postgres), AIRDB, AIRDB2, PHARMA" in note
    # no absence claim -> silent
    assert B.coverage_warning(prompt, "August revenue was $5,406,701.82 (AIRDB).",
                              KNOWN, queried) is None
    # every connection queried -> silent
    assert B.coverage_warning(prompt, reply, KNOWN, KNOWN) is None
    # nothing queried this turn (answer rests on an earlier turn) -> silent
    assert B.coverage_warning(prompt, reply, KNOWN, []) is None
    # a single connection on the platform -> nothing to cover
    assert B.coverage_warning(prompt, reply, ["ERPDB"], ["ERPDB"]) is None
    # run_python / ask_agent can query anything -> coverage unknowable -> silent
    assert B.coverage_warning(prompt, reply, KNOWN, queried, ["run_python"]) is None
    assert B.coverage_warning(prompt, reply, KNOWN, queried,
                              ["mcp__aihub__ask_agent"]) is None
    # the user scoped the question themselves (TA-20 shape) -> silent
    scoped = ("Build me today's collections worklist from ERPDB: every customer "
              "invoice that's past due")
    assert B.coverage_warning(scoped, "CGC-010: no open balance.", KNOWN, ["ERPDB"]) is None
    # ...but naming a DIFFERENT connection is not scoping the queried one
    assert B.coverage_warning("check EDWDB for August", reply, KNOWN,
                              ["EDW (SQL Server)"]) is not None
    # a base-name mention counts as scoping ('EDW' for 'EDW (SQL Server)')
    assert B.coverage_warning("what does EDW say about August?", reply, KNOWN,
                              ["EDW (SQL Server)"]) is None


def test_coverage_guard_wired_into_run_turn_with_kill_switch():
    src = open(os.path.join(APP_ROOT, "agent_service", "brain.py"),
               encoding="utf-8").read()
    assert "reset_coverage(user_ctx)" in src
    assert 'yield {"type": "guard", "warning": cov}' in src
    assert 'os.getenv("AGENT_COVERAGE_GUARD", "true")' in src


# ---------------------------------------------------------------------------
# #2 doctrine
# ---------------------------------------------------------------------------

def test_prompt_and_skill_tool_carry_the_scope_doctrine():
    sp = B.SYSTEM_PROMPT
    assert "SCOPE, COVERAGE AND NEGATIVE CLAIMS" in sp
    assert "query EVERY connection that" in sp and "I have not checked AIRDB" in sp
    assert "get the UNFILTERED total first" in sp
    assert "never silent scope" in sp
    assert "enrichment lookup, not a population filter" in sp
    assert "Email is an extra delivery, never a" in sp
    desc = getattr(W.save_skill, "description", None) or open(
        os.path.join(APP_ROOT, "agent_service", "work_tools.py"), encoding="utf-8").read()
    assert "never a silent filter" in desc and "must not" in desc
    skill = open(os.path.join(APP_ROOT, "agent_service", "product_skills",
                              "aihub-playbook-lifecycle", "SKILL.md"), encoding="utf-8").read()
    assert "Delivering produced files" in skill and "says nothing about the others" in skill


# ---------------------------------------------------------------------------
# #3 produced-file handoff
# ---------------------------------------------------------------------------

def test_offer_run_files_stages_real_outputs_and_skips_noise():
    ctx = {"user_id": TEST_UID, "role": 2, "username": "dev"}
    tok = CURRENT_USER.set(ctx)
    work = _tmp_under_root()
    try:
        xlsx = os.path.join(work, "past_due_invoice_aging.xlsx")
        with open(xlsx, "wb") as fh:
            fh.write(b"PK\x03\x04 fake workbook")
        png = os.path.join(work, "chart.png")
        with open(png, "wb") as fh:
            fh.write(b"\x89PNG fake")
        empty = os.path.join(work, "empty.csv")
        open(empty, "wb").close()
        log = os.path.join(work, "run.log")
        with open(log, "w", encoding="utf-8") as fh:
            fh.write("log")
        outside = os.path.join(os.path.dirname(APP_ROOT), "outside.txt")
        links = A._offer_run_files([xlsx, png, empty, log, xlsx, "relative.txt",
                                    os.path.join(work, "missing.xlsx"), outside])
        assert len(links) == 2, links
        assert links[0].startswith("[⤓ past_due_invoice_aging.xlsx (")
        assert "](/api/files/" in links[0] and "chart.png" in links[1]
        staged = os.listdir(file_tools.downloads_dir(TEST_UID))
        assert any(n.endswith("__past_due_invoice_aging.xlsx") for n in staged)
        block = A._files_block(links)
        assert "Files produced — include these links VERBATIM" in block
        assert "never instead" in block and "![chart.png](/api/files/" in block
        assert A._files_block([]) == ""
        # no signed-in user -> nothing staged, nothing raised
        CURRENT_USER.set({"user_id": 0, "role": 2, "username": "svc"})
        assert A._offer_run_files([xlsx]) == []
    finally:
        CURRENT_USER.reset(tok)
        shutil.rmtree(work, ignore_errors=True)
        _cleanup_user(TEST_UID)


def test_run_output_paths_absolutize_from_workdir_or_log_path():
    wd = os.path.join(APP_ROOT, "automations", "tenant_x", "_runs", "r1")
    assert A._run_output_paths({"output_files": ["a.xlsx", "sub/b.csv"], "workdir": wd}) == [
        os.path.join(wd, "a.xlsx"), os.path.join(wd, "sub/b.csv")]
    assert A._run_output_paths({"output_files": ["a.xlsx"],
                                "log_path": os.path.join(wd, "run.log")}) == [
        os.path.join(wd, "a.xlsx")]
    assert A._run_output_paths({"output_files": []}) == []
    assert A._run_output_paths({}) == []
    assert A._run_output_paths({"output_files": ["rel.txt", os.path.join(wd, "abs.txt")]}) == [
        os.path.join(wd, "abs.txt")]
    assert A._walk_output_paths({"steps": [{"output_files": [os.path.join(wd, "x.xlsx")]},
                                           {}, {"output_files": None}]}) == [
        os.path.join(wd, "x.xlsx")]


def test_dry_run_code_flow_hands_produced_files_over():
    tok = CURRENT_USER.set({"user_id": TEST_UID, "role": 2, "username": "dev"})
    work = _tmp_under_root()
    try:
        xlsx = os.path.join(work, "past_due_invoice_aging.xlsx")
        with open(xlsx, "wb") as fh:
            fh.write(b"PK fake")
        walk = {"status": "success", "steps": [
            {"name": "extract", "status": "success", "exit_code": 0, "output_files": []},
            {"name": "excel", "status": "success", "exit_code": 0, "output_files": [xlsx]},
            {"name": "email", "status": "success", "exit_code": 0, "output_files": [],
             "stdout_tail": "[aihub] email sent to james@example.com"}]}

        async def fake_manage(action, payload, timeout=900.0):
            if action == "get":
                return {"code_flow": {"nodes": [{"config": {"timeout": 60}}]}}, 200
            assert action == "dry_run", action
            return walk, 200

        with mock.patch.object(A, "_manage_cf", fake_manage):
            out = _txt(_run(A.dry_run_code_flow.handler({"name": "Aging"})))
        assert "✓ step 2 — excel" in out and "files: " in out
        assert "Files produced — include these links VERBATIM" in out
        assert "[⤓ past_due_invoice_aging.xlsx (" in out and "](/api/files/" in out
        assert "never instead" in out
        # a walk without files adds no block
        walk["steps"][1]["output_files"] = []
        with mock.patch.object(A, "_manage_cf", fake_manage):
            out = _txt(_run(A.dry_run_code_flow.handler({"name": "Aging"})))
        assert "Files produced" not in out
    finally:
        CURRENT_USER.reset(tok)
        shutil.rmtree(work, ignore_errors=True)
        _cleanup_user(TEST_UID)


def test_dry_run_automation_hands_produced_files_over():
    tok = CURRENT_USER.set({"user_id": TEST_UID, "role": 2, "username": "dev"})
    work = _tmp_under_root()
    guid = "11111111-2222-3333-4444-555555555555"
    try:
        with open(os.path.join(work, "report.csv"), "w", encoding="utf-8") as fh:
            fh.write("a,b\n1,2\n")
        result = {"status": "success", "exit_code": 0, "run_id": "r-1", "version": 3,
                  "output_files": ["report.csv"], "workdir": work, "stdout_tail": "done"}

        async def fake_manage(action, payload, timeout=900.0):
            if action == "list":
                return {"automations": [{"automation_id": guid, "name": "Aging"}]}, 200
            if action == "dry_run":
                return result, 200
            if action == "get":
                return {"automation": {"automation_id": guid, "name": "Aging",
                                       "manifest": {}}}, 200
            return {}, 200

        with mock.patch.object(A, "_manage", fake_manage):
            out = _txt(_run(A.dry_run_automation.handler({"automation_id": "Aging"})))
        assert "Run outcome: **success**" in out and "output files: report.csv" in out
        assert "[⤓ report.csv (" in out and "](/api/files/" in out
        # check_automation_run: a terminal run row (log_path, relative names)
        row = {"run": {"run_id": "r-1", "status": "success", "exit_code": 0, "version": 3,
                       "trigger_source": "manual", "automation_id": guid,
                       "output_files": ["report.csv"],
                       "log_path": os.path.join(work, "run.log")}, "events": []}

        async def fake_events(action, payload, timeout=900.0):
            if action == "run_events":
                return row, 200
            if action == "get":
                return {"automation": {"automation_id": guid, "name": "Aging",
                                       "manifest": {}}}, 200
            return {}, 200

        with mock.patch.object(A, "_manage", fake_events):
            out = _txt(_run(A.check_automation_run.handler({"run_id": "r-1"})))
        assert "status **success**" in out and "[⤓ report.csv (" in out
    finally:
        CURRENT_USER.reset(tok)
        shutil.rmtree(work, ignore_errors=True)
        _cleanup_user(TEST_UID)


def test_export_data_uses_the_shared_resolver_and_records_coverage():
    ctx = {"user_id": 7, "role": 2, "username": "dev"}
    tok = CURRENT_USER.set(ctx)
    calls = []
    link = "[⤓ sales.xlsx (1.0 KB)](/api/files/0f1e2d3c-1111-2222-3333-444455556666)"

    async def fake_exec(uid, code, **kw):
        calls.append(code)
        return {"configured": True, "ok": True, "timed_out": False, "returncode": 0,
                "output": "ROWS=3\nCOLS=2\nTRUNCATED=0\nFILE=sales.xlsx",
                "links": [link], "produced": [], "manifest": "", "error": None}

    try:
        with mock.patch.object(X, "execute_python", fake_exec), \
             mock.patch("platform_tools._connections_index", _fake_index):
            res = _run(X.export_data.handler({"name": "sales", "format": "xlsx",
                                              "connection": "edwdb", "sql": "SELECT 1"}))
            assert not res.get("is_error"), _txt(res)
            assert 'aihub.query("EDWDB (Postgres)"' in calls[-1]
            assert P.coverage_snapshot(ctx)[1] == ["EDWDB (Postgres)"]
            res = _run(X.export_data.handler({"name": "x", "format": "csv",
                                              "connection": "AIR", "sql": "SELECT 1"}))
            assert res.get("is_error") and "ambiguous" in _txt(res)
            res = _run(X.export_data.handler({"name": "x", "format": "csv",
                                              "connection": "Nope", "sql": "SELECT 1"}))
            assert res.get("is_error") and "No connection named" in _txt(res)
    finally:
        CURRENT_USER.reset(tok)


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
