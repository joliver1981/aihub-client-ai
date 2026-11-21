"""Unit pack: The Agent can find out WHICH AGENT knows something.

A General Agent can hold its own knowledge documents. search_documents does not
search them and list_agents used to show a name only, so a question whose answer
sat in an agent's knowledge ended in "not found" without that agent being asked.

Covers, without a live platform:
- the directory itself (agent_builder_tools.knowledge_sources): only enabled
  General Agents the user can see that hold documents; exact counts; names
  without the upload's '<uuid>_' prefix,
- list_agents: with_knowledge=true and the per-agent knowledge note,
- search_documents: the agents are named in a result with passages AND in an
  empty one, and a directory that cannot be read never loses the search result,
- the instructions no longer tell the model that the document search covers
  everything,
- the SQL keeps the rule the agent's own knowledge search applies (active
  documents; agent-level ones plus the asking user's own).

All HTTP and DB seams are monkeypatched. Runs standalone (aihub-agent python
test_agent_knowledge_directory.py) or under pytest; self-skips in envs without
claude_agent_sdk.
"""
import asyncio
import os
import sys
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import agent_builder_tools as B          # noqa: E402
    import document_tools as D               # noqa: E402
    from platform_tools import CURRENT_USER  # noqa: E402
    # A stub claude_agent_sdk (main-app sweep) imports fine but has no handlers.
    HAVE_SDK = hasattr(B.list_agents, "handler")
    _IMPORT_ERR = None if HAVE_SDK else "claude_agent_sdk is a stub here"
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

UUID = "86e87126-bac5-4eb5-84cd-35981f3e4606"


def _run(coro):
    return asyncio.run(coro)


def _txt(res):
    return res["content"][0]["text"]


def _as(role, uid=7):
    return CURRENT_USER.set({"user_id": uid, "role": role, "username": f"u{uid}"})


def _agents():
    return [
        {"id": 10, "name": "Vendor Manuals", "objective": "You answer questions about\n  customers' vendor manuals.",
         "enabled": True, "is_data_agent": False, "group_ids": [5], "connections": []},
        {"id": 11, "name": "HR Policies", "objective": "HR handbook assistant.",
         "enabled": True, "is_data_agent": False, "group_ids": [9], "connections": []},
        {"id": 12, "name": "Empty Helper", "objective": "No documents.",
         "enabled": True, "is_data_agent": False, "group_ids": [5], "connections": []},
        {"id": 13, "name": "Retired Manuals", "objective": "Old.",
         "enabled": False, "is_data_agent": False, "group_ids": [5], "connections": []},
        {"id": 14, "name": "ERP data", "objective": "SQL.",
         "enabled": True, "is_data_agent": True, "group_ids": [5], "connections": ["ERPDB"]},
    ]


def _knowledge():
    return [
        {"agent_id": 10, "documents": 9, "names": [f"{UUID}_Acme Routing Guide 2026.pdf", "Globex Vendor Manual.pdf",
                                                  "c.pdf", "d.pdf", "e.pdf", "f.pdf"]},
        {"agent_id": 11, "documents": 1, "names": ["Handbook.pdf"]},
        {"agent_id": 13, "documents": 4, "names": ["old.pdf"]},
        {"agent_id": 14, "documents": 2, "names": ["schema.pdf"]},
    ]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_display_filename_drops_only_the_upload_prefix():
    assert B.display_filename(f"{UUID}_Acme Routing Guide 2026.pdf") == "Acme Routing Guide 2026.pdf"
    assert B.display_filename("Acme_Routing_Guide.pdf") == "Acme_Routing_Guide.pdf"
    assert B.display_filename("12345678-1234-1234-1234-123456789012.pdf") == "12345678-1234-1234-1234-123456789012.pdf"
    assert B.display_filename(f"{UUID}_") == f"{UUID}_"          # nothing after the prefix: leave it
    assert B.display_filename(None) == ""


def test_about_line_is_one_line_and_bounded():
    assert B.about_line("You answer questions about\n  customers' vendor manuals.") == \
        "You answer questions about customers' vendor manuals."
    long = B.about_line("word " * 200)
    assert len(long) <= B._ABOUT_CHARS + 1 and long.endswith("…")
    assert B.about_line(None) == ""


def test_knowledge_sources_developer_sees_enabled_general_agents_with_documents():
    src = B.knowledge_sources(_agents(), _knowledge(), role=2, user_group_ids=set())
    assert [s["id"] for s in src] == [10, 11]              # most documents first
    assert src[0]["documents"] == 9                        # exact count, not the number of names
    assert src[0]["names"][0] == "Acme Routing Guide 2026.pdf"
    assert src[0]["about"] == "You answer questions about customers' vendor manuals."
    # no documents (12), disabled (13) and data agents (14) are not knowledge sources


def test_knowledge_sources_regular_user_sees_only_shared_agents():
    assert [s["id"] for s in B.knowledge_sources(_agents(), _knowledge(), 1, {5})] == [10]
    assert [s["id"] for s in B.knowledge_sources(_agents(), _knowledge(), 1, {9})] == [11]
    assert B.knowledge_sources(_agents(), _knowledge(), 1, set()) == []      # in no group: nothing


def test_knowledge_line_states_the_exact_count():
    src = B.knowledge_sources(_agents(), _knowledge(), 2, set())
    line = B.knowledge_line(src[0])
    assert line.startswith("9 knowledge documents (e.g. Acme Routing Guide 2026.pdf; Globex Vendor Manual.pdf")
    assert line.endswith("; +3 more)")
    assert B.knowledge_line(src[1]) == "1 knowledge document (Handbook.pdf)"
    assert B.knowledge_line({"documents": 3, "names": []}) == "3 knowledge documents"


def test_format_knowledge_sources_says_how_many_are_left_out():
    src = [{"id": i, "name": f"A{i}", "about": "x", "documents": 2, "names": ["n.pdf"]} for i in range(1, 6)]
    full = B.format_knowledge_sources(src)
    assert full.count("- id ") == 5 and "more agents" not in full
    cut = B.format_knowledge_sources(src, limit=2)
    assert cut.count("- id ") == 2 and "3 more agents hold knowledge documents" in cut
    assert "with_knowledge=true" in cut


# ---------------------------------------------------------------------------
# list_agents
# ---------------------------------------------------------------------------

def _patches(agents=None, knowledge=None):
    async def fake_agents():
        return agents if agents is not None else _agents()

    async def fake_rt(op, sql_fn, **params):
        assert op == "knowledge_agents"
        assert params == {"user_id": 7}                 # the asking user, for private knowledge
        return knowledge if knowledge is not None else _knowledge()

    return [mock.patch.object(B, "_all_agents", fake_agents), mock.patch.object(B, "_rt", fake_rt)]


def test_list_agents_with_knowledge_lists_who_knows_what():
    tok = _as(2)
    ps = _patches()
    for p in ps:
        p.start()
    try:
        out = _txt(_run(B.list_agents.handler({"with_knowledge": True})))
    finally:
        for p in ps:
            p.stop()
        CURRENT_USER.reset(tok)
    assert "Agents that hold their own knowledge documents (2)" in out
    assert "id 10 — Vendor Manuals: 9 knowledge documents" in out
    assert "about: You answer questions about customers' vendor manuals." in out
    assert "id 11 — HR Policies: 1 knowledge document (Handbook.pdf)" in out
    assert "Empty Helper" not in out and "Retired Manuals" not in out and "ERP data" not in out
    assert "ask_agent" in out


def test_list_agents_with_knowledge_honours_group_visibility():
    tok = _as(1)
    ps = _patches() + [mock.patch("readthrough.user_group_ids", lambda uid: [9])]
    for p in ps:
        p.start()
    try:
        out = _txt(_run(B.list_agents.handler({"with_knowledge": True})))
        none = _txt(_run(B.list_agents.handler({"with_knowledge": True, "name_contains": "vendor"})))
    finally:
        for p in ps:
            p.stop()
        CURRENT_USER.reset(tok)
    assert "HR Policies" in out and "Vendor Manuals" not in out
    assert none.startswith("No agent you can ask holds knowledge documents")


def test_plain_list_agents_notes_which_agents_hold_documents():
    tok = _as(2)
    ps = _patches()
    for p in ps:
        p.start()
    try:
        out = _txt(_run(B.list_agents.handler({"kind": "all"})))
    finally:
        for p in ps:
            p.stop()
        CURRENT_USER.reset(tok)
    assert "id 10 — Vendor Manuals (general, groups [5]) — 9 knowledge documents" in out
    assert "id 12 — Empty Helper (general, groups [5])\n" in out + "\n"       # no note without documents
    line14 = next(ln for ln in out.splitlines() if "id 14 " in ln)
    assert "data: ERPDB" in line14 and "knowledge document" not in line14     # data agents: connections only


def test_plain_list_agents_survives_an_unreadable_directory():
    tok = _as(2)

    async def fake_agents():
        return _agents()

    async def broken_rt(op, sql_fn, **params):
        raise RuntimeError("database unavailable")

    ps = [mock.patch.object(B, "_all_agents", fake_agents), mock.patch.object(B, "_rt", broken_rt)]
    for p in ps:
        p.start()
    try:
        res = _run(B.list_agents.handler({}))
    finally:
        for p in ps:
            p.stop()
        CURRENT_USER.reset(tok)
    assert not res.get("is_error") and "id 10 — Vendor Manuals" in _txt(res)


# ---------------------------------------------------------------------------
# search_documents names the agents
# ---------------------------------------------------------------------------

def _search(result, sources):
    async def fake_post(path, body, internal=False, read_timeout=None):
        assert path == "/api/internal/document-search-unified"
        return {"result": result}, 200

    async def fake_sources():
        if isinstance(sources, Exception):
            raise sources
        return sources

    tok = _as(1)
    ps = [mock.patch.object(D, "_post_main", fake_post),
          mock.patch.object(B, "knowledge_sources_for_user", fake_sources)]
    for p in ps:
        p.start()
    try:
        return _run(D.search_documents.handler({"query": "carton weight limit"}))
    finally:
        for p in ps:
            p.stop()
        CURRENT_USER.reset(tok)


SRC = [{"id": 10, "name": "Vendor Manuals", "about": "Customers' vendor manuals.", "documents": 9,
        "names": ["Acme Routing Guide 2026.pdf"]}]


def test_search_with_no_library_hit_points_at_the_agents():
    res = _search({"passages": []}, SRC)
    out = _txt(res)
    assert not res.get("is_error")
    assert "No document in the library matched" in out and "document library only" in out
    assert "NOT SEARCHED HERE" in out and "id 10 — Vendor Manuals: 9 knowledge documents" in out
    assert "ask_agent" in out
    assert "import them with import_documents" not in out      # the old dead end


def test_search_with_passages_still_names_the_agents():
    out = _txt(_search({"passages": [{"filename": "lease.pdf", "page_number": 3, "snippet": "Rent is due monthly."}]}, SRC))
    assert "1 passage(s)" in out and "lease.pdf p.3" in out
    assert out.index("lease.pdf") < out.index("NOT SEARCHED HERE")
    assert "id 10 — Vendor Manuals" in out


def test_search_without_any_knowledge_agent_is_unchanged():
    out = _txt(_search({"passages": []}, []))
    assert out.startswith("No documents matched") and "import_documents" in out
    assert "NOT SEARCHED HERE" not in out


def test_search_result_survives_an_unreadable_directory():
    out = _txt(_search({"passages": [{"filename": "lease.pdf", "page_number": 3, "snippet": "Rent."}]},
                       RuntimeError("database unavailable")))
    assert "lease.pdf p.3" in out and "NOT SEARCHED HERE" not in out


def test_knowledge_sources_for_user_never_raises():
    tok = _as(2)

    async def broken():
        raise RuntimeError("down")

    with mock.patch.object(B, "_all_agents", broken):
        try:
            assert _run(B.knowledge_sources_for_user()) == []
        finally:
            CURRENT_USER.reset(tok)


# ---------------------------------------------------------------------------
# The instructions and the query
# ---------------------------------------------------------------------------

def test_instructions_no_longer_say_a_knowledge_agent_is_never_needed():
    import brain
    assert "you do NOT need a knowledge" not in brain.SYSTEM_PROMPT
    assert "AGENTS AS SOURCES OF KNOWLEDGE" in brain.SYSTEM_PROMPT
    assert "with_knowledge=true" in brain.SYSTEM_PROMPT
    assert "you do NOT need a" not in D.search_documents.description
    assert "ask_agent" in D.search_documents.description


class _Cur:
    def __init__(self, rows):
        self.rows, self.sql, self.args = rows, None, None

    def execute(self, sql, *args):
        self.sql, self.args = sql, args

    def fetchall(self):
        return self.rows


def test_query_counts_active_documents_for_the_asking_user():
    cur = _Cur([(10, "new.pdf"), (10, "older.pdf"), (11, "h.pdf")] + [(12, f"d{i}.pdf") for i in range(9)])
    out = B._q_knowledge_agents(7)(cur)
    assert cur.args == ("7",)
    assert "ak.is_active = 1" in cur.sql and "ISNULL(ak.added_by, 'USER') = 'USER'" in cur.sql
    by = {r["agent_id"]: r for r in out}
    assert by[10] == {"agent_id": 10, "documents": 2, "names": ["new.pdf", "older.pdf"]}
    assert by[12]["documents"] == 9 and len(by[12]["names"]) == B._KNOWLEDGE_NAMES_SHOWN


if __name__ == "__main__":
    if not HAVE_SDK:
        print(f"SKIP: {_IMPORT_ERR}")
        sys.exit(0)
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                import traceback
                print(f"FAIL {name}: {e}")
                traceback.print_exc()
    print(f"{'FAILED' if failed else 'OK'}: {failed} failure(s)")
    sys.exit(1 if failed else 0)
