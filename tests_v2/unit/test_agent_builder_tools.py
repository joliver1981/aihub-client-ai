"""Unit pack for The Agent's Agent Builder tools (agent_service/agent_builder_tools.py).

Covers the parts that must hold without a live platform:
- the pure merge / resolve / visibility helpers,
- role gates (Developer+ for building, admin for group sharing),
- the two-step confirmation on destructive tools,
- replace-all safety: partial edits re-post the FULL current configuration
  (tools + document types preserved) through /add/agent,
- name/type validation against the catalogs (nothing saved on a typo),
- honest read-back reporting (auto-added platform tools are flagged, a
  mismatch is reported as UNVERIFIED).

All HTTP and DB seams are monkeypatched; nothing here touches the network.
Runs standalone (aihub-agent python test_agent_builder_tools.py) or under
pytest; self-skips in envs without claude_agent_sdk (main-app sweep).
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
    from platform_tools import CURRENT_USER  # noqa: E402
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


def _run(coro):
    return asyncio.run(coro)


def _txt(res):
    return res["content"][0]["text"]


def _as(role, uid=7):
    return CURRENT_USER.set({"user_id": uid, "role": role, "username": f"u{uid}"})


def _agent(**over):
    a = {"id": 968, "name": "Gen Agent 1004", "objective": "Be helpful.",
         "enabled": True, "is_data_agent": False, "allow_personal_connections": True,
         "created": "2026-09-01", "core_tools": ["query_database", "wait_seconds"],
         "custom_tools": ["QR Code Generator"], "document_types": ["invoice"],
         "groups": [{"id": 5, "name": "Analysts"}], "group_ids": [5]}
    a.update(over)
    return a


CATS = {"data_analysis": {"description": "d", "tools": [
            {"name": "query_database", "display_name": "Query DB", "description": "x"},
            {"name": "web_search", "display_name": "Web", "description": "y"}]},
        "communication": {"description": "c", "tools": [
            {"name": "send_email_message", "display_name": "Email", "description": "z"}]}}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_merge_names_semantics():
    cur = ["a", "b"]
    assert B.merge_names(cur, ["c", "a"], "add") == ["a", "b", "c"]
    assert B.merge_names(cur, ["a"], "remove") == ["b"]
    assert B.merge_names(cur, ["z", "z", " y "], "replace") == ["z", "y"]
    assert B.merge_names(cur, [], "replace") == []          # replace-empty clears
    try:
        B.merge_names(cur, ["a"], "bogus")
        assert False, "bad mode must raise"
    except ValueError:
        pass


def test_match_agent_by_id_name_and_ambiguity():
    agents = [{"id": 1, "name": "Alpha"}, {"id": 2, "name": "beta"},
              {"id": 3, "name": "Beta"}, {"id": 4, "name": "Gamma Ray"}]
    assert B.match_agent("1", agents)[0]["id"] == 1
    assert B.match_agent("ALPHA", agents)[0]["id"] == 1
    row, err = B.match_agent("beta", agents)
    assert row is None and "2 agents are named" in err and "id 2" in err and "id 3" in err
    row, err = B.match_agent("gamma", agents)
    assert row is None and "Similar names" in err and "Gamma Ray" in err
    assert B.match_agent("99", agents)[1].startswith("No agent with id 99")
    assert B.match_agent("", agents)[1].startswith("Give me")


def test_visible_to_rule():
    a = {"group_ids": [5]}
    assert B.visible_to(a, 2, set())            # developers see everything
    assert B.visible_to(a, 1, {5, 9})           # regular user in a shared group
    assert not B.visible_to(a, 1, {9})          # regular user not in the group
    assert not B.visible_to({"group_ids": []}, 1, {5})


def test_suggest_and_unknown_names():
    known = {"query_database", "web_search", "send_email_message"}
    assert B.unknown_names(["web_search", "nope"], known) == ["nope"]
    assert "query_database" in B.suggest("query database", known)
    assert "send_email_message" in B.suggest("send_email", known)


# ---------------------------------------------------------------------------
# Role gates and confirmation gates
# ---------------------------------------------------------------------------

def test_builder_writes_need_developer_role():
    tok = _as(1)
    try:
        for handler, args in (
                (B.create_general_agent.handler, {"name": "X"}),
                (B.update_general_agent.handler, {"agent": "968", "name": "Y"}),
                (B.set_agent_tools.handler, {"agent": "968", "core_tools": ["web_search"]}),
                (B.set_agent_document_types.handler, {"agent": "968", "document_types": []}),
                (B.delete_general_agent.handler, {"agent": "968", "confirmed": True}),
                (B.add_agent_knowledge.handler, {"agent": "968", "path": "x.pdf"}),
                (B.delete_agent_knowledge.handler, {"knowledge_id": 1, "confirmed": True})):
            res = _run(handler(args))
            assert res.get("is_error"), handler
            assert "Developer" in _txt(res)
    finally:
        CURRENT_USER.reset(tok)


def test_group_sharing_is_admin_only():
    tok = _as(2)
    try:
        res = _run(B.assign_agent_groups.handler({"agent": "968", "group_ids": [5]}))
        assert res.get("is_error") and "admin" in _txt(res)
    finally:
        CURRENT_USER.reset(tok)


def test_delete_agent_is_two_step_and_reads_back():
    tok = _as(2)
    posts = []

    async def fake_post(path, body, timeout=None):
        posts.append((path, body))
        return {"status": "success"}, 200

    async def fake_fetch(agent_id):
        return None          # read-back after the delete: the row is gone

    async def fake_resolve(ref):
        return _agent(), None

    async def fake_knowledge(agent_id):
        return [{"knowledge_id": 1}]

    try:
        with mock.patch.object(B, "_resolve", fake_resolve), \
             mock.patch.object(B, "_post", fake_post), \
             mock.patch.object(B, "_fetch_agent", fake_fetch), \
             mock.patch.object(B, "_knowledge_list", fake_knowledge):
            first = _run(B.delete_general_agent.handler({"agent": "968"}))
            assert "CONFIRMATION REQUIRED" in _txt(first) and not posts
            assert "1 knowledge document" in _txt(first)
            second = _run(B.delete_general_agent.handler({"agent": "968", "confirmed": True}))
            assert posts == [("/delete/agent", {"agent_id": 968})]
            assert "Deleted agent 968" in _txt(second) and "read-back" in _txt(second)
    finally:
        CURRENT_USER.reset(tok)


def test_data_agents_are_refused_by_general_tools():
    tok = _as(2)

    async def fake_resolve(ref):
        return _agent(is_data_agent=True), None

    try:
        with mock.patch.object(B, "_resolve", fake_resolve):
            res = _run(B.set_agent_tools.handler({"agent": "968", "core_tools": ["web_search"]}))
            assert res.get("is_error") and "DATA agent" in _txt(res)
    finally:
        CURRENT_USER.reset(tok)


# ---------------------------------------------------------------------------
# Create / update / tools / document types — replace-all safety + read-back
# ---------------------------------------------------------------------------

def _catalog_patches(posts, after_agent):
    async def fake_core_catalog():
        return CATS, ["wait_seconds"]

    async def fake_doc_types():
        return [{"name": "invoice", "count": 3}, {"name": "receipt", "count": 1}]

    async def fake_post(path, body, timeout=None):
        posts.append((path, body))
        return {"status": "success", "message": after_agent["id"]}, 200

    async def fake_fetch(agent_id):
        return after_agent

    return [mock.patch.object(B, "_core_catalog", fake_core_catalog),
            mock.patch.object(B, "_document_types", fake_doc_types),
            mock.patch.object(B, "_custom_catalog", lambda: ([{"name": "QR Code Generator"}], True)),
            mock.patch.object(B, "_post", fake_post),
            mock.patch.object(B, "_fetch_agent", fake_fetch)]


def _enter(patches):
    for p in patches:
        p.start()
    return patches


def _exit(patches):
    for p in patches:
        p.stop()


def test_create_with_name_only_uses_default_objective_and_reads_back():
    tok = _as(2)
    posts = []
    after = _agent(id=1010, name="Gen Agent 1005", core_tools=["wait_seconds",
                   "get_the_current_date_and_time"], custom_tools=[], document_types=[],
                   groups=[], group_ids=[])

    async def no_agents():
        return []

    ps = _enter(_catalog_patches(posts, after) + [mock.patch.object(B, "_all_agents", no_agents)])
    try:
        res = _run(B.create_general_agent.handler({"name": "Gen Agent 1005"}))
        assert not res.get("is_error"), _txt(res)
        assert len(posts) == 1 and posts[0][0] == "/add/agent"
        body = posts[0][1]
        assert body["agent_id"] == 0 and body["agent_description"] == "Gen Agent 1005"
        assert "Gen Agent 1005" in body["agent_objective"]     # platform-style default
        assert body["core_tool_names"] == [] and body["tool_names"] == []
        assert body["allowed_document_types"] == [] and body["allow_personal_connections"] is True
        out = _txt(res)
        assert "Created General Agent id 1010" in out and "platform default" in out
        # auto-added platform tools are called out honestly
        assert "added automatically" in out and "wait_seconds" in out
        assert "Not yet shared with any group" in out
    finally:
        _exit(ps)
        CURRENT_USER.reset(tok)


def test_create_refuses_duplicate_name_unless_allowed():
    tok = _as(2)
    posts = []

    async def existing():
        return [{"id": 968, "name": "Gen Agent 1004", "is_data_agent": False}]

    ps = _enter(_catalog_patches(posts, _agent()) + [mock.patch.object(B, "_all_agents", existing)])
    try:
        res = _run(B.create_general_agent.handler({"name": "gen agent 1004"}))
        assert res.get("is_error") and "already exists" in _txt(res) and not posts
        res = _run(B.create_general_agent.handler({"name": "gen agent 1004",
                                                   "allow_duplicate_name": True}))
        assert not res.get("is_error") and len(posts) == 1
    finally:
        _exit(ps)
        CURRENT_USER.reset(tok)


def test_create_validates_tool_and_type_names_before_saving():
    tok = _as(2)
    posts = []

    async def none():
        return []

    ps = _enter(_catalog_patches(posts, _agent()) + [mock.patch.object(B, "_all_agents", none)])
    try:
        res = _run(B.create_general_agent.handler({"name": "N", "core_tools": ["web search"]}))
        assert res.get("is_error") and "not a selectable tool" in _txt(res)
        assert "web_search" in _txt(res) and not posts            # suggestion, nothing saved
        res = _run(B.create_general_agent.handler({"name": "N", "custom_tools": ["Nope"]}))
        assert res.get("is_error") and "not installed" in _txt(res) and not posts
        res = _run(B.create_general_agent.handler({"name": "N",
                                                   "allowed_document_types": ["invoices"]}))
        assert res.get("is_error") and "unknown document type" in _txt(res).lower()
        assert "invoice" in _txt(res) and not posts
    finally:
        _exit(ps)
        CURRENT_USER.reset(tok)


def test_update_preserves_tools_and_document_types():
    """/add/agent is replace-all: a rename must re-post the FULL current tool
    and document-type configuration or the platform would wipe it."""
    tok = _as(2)
    posts = []
    current = _agent()
    after = _agent(name="Renamed")

    async def fake_resolve(ref):
        return current, None

    ps = _enter(_catalog_patches(posts, after) + [mock.patch.object(B, "_resolve", fake_resolve)])
    try:
        res = _run(B.update_general_agent.handler({"agent": "968", "name": "Renamed"}))
        assert not res.get("is_error"), _txt(res)
        body = posts[0][1]
        assert body["agent_id"] == 968 and body["agent_description"] == "Renamed"
        assert body["core_tool_names"] == ["query_database", "wait_seconds"]
        assert body["tool_names"] == ["QR Code Generator"]
        assert body["allowed_document_types"] == ["invoice"]
        assert body["agent_objective"] == "Be helpful."
        assert "verified by read-back" in _txt(res) and "preserved" in _txt(res)
        # no-op update is reported as such, nothing posted
        posts.clear()
        res = _run(B.update_general_agent.handler({"agent": "968", "enabled": True}))
        assert "Nothing to change" in _txt(res) and not posts
    finally:
        _exit(ps)
        CURRENT_USER.reset(tok)


def test_update_flags_readback_mismatch_as_unverified():
    tok = _as(2)
    posts = []
    current = _agent()
    after = _agent(name="Something Else")     # platform stored a different name

    async def fake_resolve(ref):
        return current, None

    ps = _enter(_catalog_patches(posts, after) + [mock.patch.object(B, "_resolve", fake_resolve)])
    try:
        res = _run(B.update_general_agent.handler({"agent": "968", "name": "Renamed"}))
        assert "UNVERIFIED" in _txt(res)
    finally:
        _exit(ps)
        CURRENT_USER.reset(tok)


def test_set_agent_tools_add_remove_replace_and_readback_report():
    tok = _as(2)
    posts = []
    current = _agent()
    after = _agent(core_tools=["query_database", "wait_seconds", "web_search",
                               "get_the_current_date_and_time"])

    async def fake_resolve(ref):
        return current, None

    ps = _enter(_catalog_patches(posts, after) + [mock.patch.object(B, "_resolve", fake_resolve)])
    try:
        # add (default mode): merged with current, custom tools preserved
        res = _run(B.set_agent_tools.handler({"agent": "968", "core_tools": ["web_search"]}))
        assert not res.get("is_error"), _txt(res)
        body = posts[-1][1]
        assert body["core_tool_names"] == ["query_database", "wait_seconds", "web_search"]
        assert body["tool_names"] == ["QR Code Generator"]
        assert body["allowed_document_types"] == ["invoice"]
        out = _txt(res)
        assert "get_the_current_date_and_time" in out and "added automatically" in out
        # remove of a tool the agent doesn't have: honest refusal, nothing posted
        n = len(posts)
        res = _run(B.set_agent_tools.handler({"agent": "968", "core_tools": ["send_email_message"],
                                              "mode": "remove"}))
        assert res.get("is_error") and "doesn't have" in _txt(res) and len(posts) == n
        # remove of a present tool posts the survivors
        res = _run(B.set_agent_tools.handler({"agent": "968", "core_tools": ["query_database"],
                                              "mode": "remove"}))
        assert posts[-1][1]["core_tool_names"] == ["wait_seconds"]
        # replace with an unknown name: nothing saved
        n = len(posts)
        res = _run(B.set_agent_tools.handler({"agent": "968", "core_tools": ["nonsense_tool"],
                                              "mode": "replace"}))
        assert res.get("is_error") and len(posts) == n
        # identical set (includes the MANDATORY wait_seconds, which is not in
        # the selectable catalog — must not be refused): no-op
        res = _run(B.set_agent_tools.handler({"agent": "968",
                                              "core_tools": ["query_database", "wait_seconds"],
                                              "custom_tools": ["QR Code Generator"],
                                              "mode": "replace"}))
        assert "already has exactly" in _txt(res), _txt(res)
        assert len(posts) == n
    finally:
        _exit(ps)
        CURRENT_USER.reset(tok)


def test_set_agent_document_types_replace_add_remove_clear():
    tok = _as(2)
    posts = []
    current = _agent()

    async def fake_resolve(ref):
        return current, None

    # read-back mirrors whatever was posted last
    async def fake_fetch(agent_id):
        return _agent(document_types=posts[-1][1]["allowed_document_types"])

    ps = _enter(_catalog_patches(posts, _agent()) + [
        mock.patch.object(B, "_resolve", fake_resolve),
        mock.patch.object(B, "_fetch_agent", fake_fetch)])
    try:
        res = _run(B.set_agent_document_types.handler({"agent": "968",
                                                       "document_types": ["receipt"],
                                                       "mode": "add"}))
        assert not res.get("is_error"), _txt(res)
        assert posts[-1][1]["allowed_document_types"] == ["invoice", "receipt"]
        assert posts[-1][1]["core_tool_names"] == ["query_database", "wait_seconds"]  # preserved
        assert "restricted to invoice, receipt" in _txt(res)
        res = _run(B.set_agent_document_types.handler({"agent": "968", "document_types": []}))
        assert posts[-1][1]["allowed_document_types"] == []
        assert "unrestricted" in _txt(res)
        n = len(posts)
        res = _run(B.set_agent_document_types.handler({"agent": "968",
                                                       "document_types": ["receipt"],
                                                       "mode": "remove"}))
        assert res.get("is_error") and "isn't restricted to" in _txt(res) and len(posts) == n
        res = _run(B.set_agent_document_types.handler({"agent": "968",
                                                       "document_types": ["bogus"]}))
        assert res.get("is_error") and "Nothing saved" in _txt(res) and len(posts) == n
    finally:
        _exit(ps)
        CURRENT_USER.reset(tok)


def test_save_failure_is_reported_and_nothing_claimed():
    tok = _as(2)
    current = _agent()

    async def fake_resolve(ref):
        return current, None

    async def failing_post(path, body, timeout=None):
        return {"status": "error", "message": "Failed to insert agent"}, 500

    async def fake_core_catalog():
        return CATS, []

    try:
        with mock.patch.object(B, "_resolve", fake_resolve), \
             mock.patch.object(B, "_post", failing_post), \
             mock.patch.object(B, "_core_catalog", fake_core_catalog):
            res = _run(B.set_agent_tools.handler({"agent": "968", "core_tools": ["web_search"]}))
            assert res.get("is_error")
            assert "Save FAILED" in _txt(res) and "nothing changed" in _txt(res)
    finally:
        CURRENT_USER.reset(tok)


# ---------------------------------------------------------------------------
# Group sharing — per-group replace with membership preserved
# ---------------------------------------------------------------------------

def test_assign_agent_groups_preserves_membership_and_reads_back():
    tok = _as(3)
    posts = []
    current = _agent(groups=[{"id": 5, "name": "Analysts"}], group_ids=[5])
    membership = {5: ([11, 12], [968, 42]), 4: ([21], [42])}

    async def fake_resolve(ref):
        return current, None

    async def fake_sql(fn):
        # distinguish the two query builders by their closure name
        name = getattr(fn, "__qualname__", "")
        if name.startswith("_q_groups"):
            return [{"id": 4, "name": "End Users"}, {"id": 5, "name": "Analysts"}]
        if "_q_group_membership" in name:
            gid = fn.__closure__[0].cell_contents
            users, agents = membership[gid]
            return list(users), list(agents)
        raise AssertionError(name)

    async def fake_post(path, body, timeout=None):
        posts.append((path, body))
        gid = body["group_id"]
        membership[gid] = (body["assigned_users"], body["permissions"])
        return {"status": "success"}, 200

    async def fake_fetch(agent_id):
        gids = sorted(g for g, (_u, ags) in membership.items() if 968 in ags)
        return _agent(group_ids=gids, groups=[{"id": g, "name": str(g)} for g in gids])

    try:
        with mock.patch.object(B, "_resolve", fake_resolve), \
             mock.patch.object(B, "_sql", fake_sql), \
             mock.patch.object(B, "_post", fake_post), \
             mock.patch.object(B, "_fetch_agent", fake_fetch):
            # move from {5} to {4}: one add (group 4), one remove (group 5)
            res = _run(B.assign_agent_groups.handler({"agent": "968", "group_ids": [4]}))
            assert not res.get("is_error"), _txt(res)
            by_gid = {b["group_id"]: b for _p, b in posts}
            assert by_gid[4]["assigned_users"] == [21] and by_gid[4]["permissions"] == [42, 968]
            assert by_gid[5]["assigned_users"] == [11, 12] and by_gid[5]["permissions"] == [42]
            assert "End Users" in _txt(res) and "memberships were not changed" in _txt(res)
            # unknown group: refused, nothing posted
            n = len(posts)
            res = _run(B.assign_agent_groups.handler({"agent": "968", "group_ids": [99]}))
            assert res.get("is_error") and "Unknown group" in _txt(res) and len(posts) == n
    finally:
        CURRENT_USER.reset(tok)


# ---------------------------------------------------------------------------
# Knowledge
# ---------------------------------------------------------------------------

def test_delete_agent_knowledge_two_step_and_readback():
    tok = _as(2)
    posts = []
    state = {"active": True}

    async def fake_sql(fn):
        return {"knowledge_id": 12, "agent_id": 968, "document_id": "abc",
                "description": "d", "filename": "policy.pdf", "document_type": "policy",
                "page_count": 3, "is_active": state["active"]}

    async def fake_post(path, body, timeout=None):
        posts.append(path)
        state["active"] = False
        return {"status": "success"}, 200

    try:
        with mock.patch.object(B, "_sql", fake_sql), mock.patch.object(B, "_post", fake_post):
            res = _run(B.delete_agent_knowledge.handler({"knowledge_id": 12}))
            assert "CONFIRMATION REQUIRED" in _txt(res) and "policy.pdf" in _txt(res) and not posts
            res = _run(B.delete_agent_knowledge.handler({"knowledge_id": 12, "confirmed": True}))
            assert posts == ["/delete/agent_knowledge/12"]
            assert "Removed knowledge_id 12" in _txt(res) and "read-back" in _txt(res)
    finally:
        CURRENT_USER.reset(tok)


def test_add_agent_knowledge_rejects_unsupported_and_relays_busy(tmp_path=None):
    import tempfile
    tok = _as(2)
    current = _agent()

    async def fake_resolve(ref):
        return current, None

    d = tempfile.mkdtemp()
    bad = os.path.join(d, "thing.exe")
    open(bad, "wb").write(b"x")
    good = os.path.join(d, "notes.txt")
    open(good, "wb").write(b"hello knowledge")

    async def busy_multipart(path, fields, filename, payload, read_timeout):
        assert fields["agent_id"] == "968" and filename == "notes.txt"
        return {"message": "Document stack busy", "retry_after": 45}, 503, "45"

    async def fake_knowledge(agent_id):
        return []

    try:
        with mock.patch.object(B, "_resolve", fake_resolve), \
             mock.patch.object(B, "_post_multipart", busy_multipart), \
             mock.patch.object(B, "_knowledge_list", fake_knowledge):
            res = _run(B.add_agent_knowledge.handler({"agent": "968", "path": bad}))
            assert res.get("is_error") and "not a supported document type" in _txt(res)
            res = _run(B.add_agent_knowledge.handler({"agent": "968", "path": good}))
            assert res.get("is_error") and "busy" in _txt(res).lower()
            assert "45 seconds" in _txt(res) and "NOT processed" in _txt(res)
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
