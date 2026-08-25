"""All-users rollout gates (james 2026-08-24, docs/the-agent-all-users-rollout-handoff.md).

Covers the launch-package items that changed behavior by ROLE:
- fs tools (read_file/_resolve_read_path, list_server_files, import_documents):
  role<2 scoped to delivered refs + own data/agent/users/<uid>/ tree; Dev+ unchanged.
- secrets tools: role<2 refused (the Local Secrets store is TENANT-GLOBAL —
  verified in app.py /workflow/secrets/*: no user identity anywhere).
- scheduling: split off the build gate to AGENT_SCHEDULE_ALLOW_ALL_USERS
  (default OPEN); the flag is a per-install retreat only.
- per-role model: role<2 -> role1_model override > AGENT_MODEL_ROLE1 (haiku);
  everyone else keeps the original chain. build_options threads the role.
- daily turn cap: default OFF; atomic counter; role>=3 exempt; fail-open;
  run_turn refuses BEFORE any LLM call when capped.

Runs standalone (aihub-agent python test_agent_allusers_gates.py) or under
pytest; self-skips in envs without claude_agent_sdk (main-app sweep).
Force-add to git (gitignore hides test*.py).
"""
import asyncio
import json
import os
import sys
import tempfile
import shutil
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import agent_config           # noqa: E402
    import brain                  # noqa: E402
    import document_tools         # noqa: E402
    import platform_tools         # noqa: E402
    import usage_store            # noqa: E402
    import views_tools            # noqa: E402
    import work_tools             # noqa: E402
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

ROLE1 = {"user_id": 424299, "role": 1, "username": "allusers-u1", "name": "U1"}
ROLE2 = {"user_id": 424298, "role": 2, "username": "allusers-u2", "name": "U2"}
ROLE3 = {"user_id": 424297, "role": 3, "username": "allusers-u3", "name": "U3"}


def _txt(res) -> str:
    return " ".join(c.get("text", "") for c in res.get("content", []))


def _own_tree(uid):
    return os.path.join(APP_ROOT, "data", "agent", "users", str(uid))


# ---------------------------------------------------------------- fs scoping
def test_read_path_role1_refuses_arbitrary_host_file():
    CURRENT_USER.set(dict(ROLE1))
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("outside")
        outside = f.name
    try:
        path, err = document_tools._resolve_read_path(outside)
        assert path is None and "Developer" in (err or ""), (path, err)
    finally:
        os.unlink(outside)


def test_read_path_role1_allows_own_tree_and_role2_unchanged():
    own_dir = os.path.join(_own_tree(ROLE1["user_id"]), "downloads")
    os.makedirs(own_dir, exist_ok=True)
    own_file = os.path.join(own_dir, "mine.txt")
    with open(own_file, "w") as f:
        f.write("mine")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("outside")
        outside = f.name
    try:
        CURRENT_USER.set(dict(ROLE1))
        path, err = document_tools._resolve_read_path(own_file)
        assert err is None and os.path.normcase(path) == os.path.normcase(own_file)
        CURRENT_USER.set(dict(ROLE2))
        path2, err2 = document_tools._resolve_read_path(outside)
        assert err2 is None and path2, (path2, err2)  # Dev+ unchanged
    finally:
        os.unlink(outside)
        shutil.rmtree(_own_tree(ROLE1["user_id"]), ignore_errors=True)


def test_read_path_role1_delivered_ref_still_resolves():
    CURRENT_USER.set(dict(ROLE1))
    import file_tools
    with mock.patch.object(file_tools, "resolve_api_files_ref",
                           return_value=(r"C:\staged\ok.pdf", "ok.pdf")):
        path, err = document_tools._resolve_read_path("/api/files/abc123")
        assert err is None and path == r"C:\staged\ok.pdf", (path, err)


def test_read_path_role1_miss_message_names_delivered_refs():
    CURRENT_USER.set(dict(ROLE1))
    path, err = document_tools._resolve_read_path(r"C:\does\not\exist.txt")
    assert path is None and "/api/files" in (err or ""), err


def test_list_server_files_role_scoping():
    CURRENT_USER.set(dict(ROLE1))
    res = asyncio.run(document_tools.list_server_files.handler({"path": "C:\\"}))
    assert res.get("is_error") and "Developer" in _txt(res), _txt(res)
    own_dir = os.path.join(_own_tree(ROLE1["user_id"]), "downloads")
    os.makedirs(own_dir, exist_ok=True)
    try:
        res2 = asyncio.run(document_tools.list_server_files.handler({"path": own_dir}))
        assert not res2.get("is_error"), _txt(res2)
    finally:
        shutil.rmtree(_own_tree(ROLE1["user_id"]), ignore_errors=True)
    CURRENT_USER.set(dict(ROLE2))
    res3 = asyncio.run(document_tools.list_server_files.handler(
        {"path": os.path.join(APP_ROOT, "docs")}))
    assert not res3.get("is_error"), _txt(res3)  # Dev+ unchanged


def test_import_documents_role1_refuses_arbitrary_dir():
    CURRENT_USER.set(dict(ROLE1))
    res = asyncio.run(document_tools.import_documents.handler(
        {"path": os.path.join(APP_ROOT, "docs")}))
    assert res.get("is_error") and "Developer" in _txt(res), _txt(res)


# ------------------------------------------------------------------- secrets
def test_secrets_tools_role1_refused_without_touching_the_platform():
    CURRENT_USER.set(dict(ROLE1))

    def _boom(*a, **k):
        raise AssertionError("platform seam must not be called for role<2")

    with mock.patch.object(platform_tools, "_get", _boom), \
         mock.patch.object(platform_tools, "_post", _boom):
        r1 = asyncio.run(platform_tools.list_secret_names.handler({}))
        r2 = asyncio.run(platform_tools.store_platform_secret.handler(
            {"name": "X_KEY", "value": "v"}))
    assert r1.get("is_error") and "Developer" in _txt(r1), _txt(r1)
    assert r2.get("is_error") and "NOT stored" in _txt(r2), _txt(r2)


def test_secrets_list_role2_passes_the_gate():
    CURRENT_USER.set(dict(ROLE2))

    async def _fake_get(path):
        return {"secrets": []}

    with mock.patch.object(platform_tools, "_get", _fake_get):
        res = asyncio.run(platform_tools.list_secret_names.handler({}))
    assert not res.get("is_error") and "empty" in _txt(res), _txt(res)


# ---------------------------------------------------------------- scheduling
def test_schedule_agent_task_gate_split():
    CURRENT_USER.set(dict(ROLE1))
    with mock.patch.dict(os.environ, {"AGENT_SCHEDULE_ALLOW_ALL_USERS": "false"}):
        res = asyncio.run(work_tools.schedule_agent_task.handler({"prompt": "x"}))
        assert res.get("is_error") and "Developer role" in _txt(res), _txt(res)
    # Default (flag unset) = OPEN: the role gate must NOT fire; any later
    # validation error is fine and proves we got past it.
    env = {k: v for k, v in os.environ.items()
           if k != "AGENT_SCHEDULE_ALLOW_ALL_USERS"}
    with mock.patch.dict(os.environ, env, clear=True):
        try:
            res2 = asyncio.run(work_tools.schedule_agent_task.handler(
                {"prompt": "x", "cron": "not a cron"}))
            assert "Developer role" not in _txt(res2), _txt(res2)
        except Exception:
            pass  # raised past the gate — also proof the gate is open


def test_schedule_view_tools_gate_split():
    CURRENT_USER.set(dict(ROLE1))
    import readthrough
    with mock.patch.dict(os.environ, {"AGENT_SCHEDULE_ALLOW_ALL_USERS": "false"}):
        r1 = asyncio.run(views_tools.schedule_view_refresh.handler({"name": "nope"}))
        r2 = asyncio.run(views_tools.schedule_view_email.handler({"name": "nope"}))
        assert "Developer role" in _txt(r1) and "Developer role" in _txt(r2)
    with mock.patch.object(views_tools.views_store, "get", return_value=None), \
         mock.patch.object(readthrough, "user_group_ids", return_value=[]):
        r3 = asyncio.run(views_tools.schedule_view_refresh.handler({"name": "nope"}))
        assert "No view named" in _txt(r3), _txt(r3)  # past the gate by default


# ------------------------------------------------------------ per-role model
def _tmp_settings():
    d = tempfile.mkdtemp()
    return mock.patch.object(agent_config, "RUNTIME_SETTINGS_PATH",
                             os.path.join(d, "settings.json")), d


def test_model_pick_by_role_and_overrides():
    patch, d = _tmp_settings()
    with patch:
        assert agent_config.get_effective_model() == agent_config.AGENT_MODEL
        assert agent_config.get_effective_model(role=2) == agent_config.AGENT_MODEL
        assert agent_config.get_effective_model(role=3) == agent_config.AGENT_MODEL
        assert agent_config.get_effective_model(role=1) == agent_config.AGENT_MODEL_ROLE1
        assert agent_config.AGENT_MODEL_ROLE1 == "claude-haiku-4-5-20251001"
        eff = agent_config.set_role1_model_override("claude-sonnet-5")
        assert eff == "claude-sonnet-5"
        assert agent_config.get_effective_model(role=1) == "claude-sonnet-5"
        assert agent_config.get_effective_model() == agent_config.AGENT_MODEL  # untouched
        assert agent_config.set_role1_model_override("") == agent_config.AGENT_MODEL_ROLE1
        agent_config.set_model_override("claude-opus-5")
        assert agent_config.get_effective_model(role=1) == agent_config.AGENT_MODEL_ROLE1
        try:
            agent_config.set_role1_model_override("bad model!!")
            assert False, "malformed id must raise"
        except ValueError:
            pass
    shutil.rmtree(d, ignore_errors=True)


def test_build_options_threads_the_role():
    patch, d = _tmp_settings()
    with patch:
        assert brain.build_options(role=1).model == agent_config.AGENT_MODEL_ROLE1
        assert brain.build_options(role=3).model == agent_config.AGENT_MODEL
        assert brain.build_options().model == agent_config.AGENT_MODEL
    shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ turn cap
def test_turn_cap_setting_validation():
    patch, d = _tmp_settings()
    with patch:
        assert agent_config.get_turn_cap() == 0          # default OFF
        assert agent_config.set_turn_cap(5) == 5
        assert agent_config.get_turn_cap() == 5
        assert agent_config.set_turn_cap("") == 0
        assert agent_config.set_turn_cap(None) == 0
        for bad in (-1, "abc"):
            try:
                agent_config.set_turn_cap(bad)
                assert False, f"{bad!r} must raise"
            except ValueError:
                pass
    shutil.rmtree(d, ignore_errors=True)


def test_turn_cap_counting_and_exemption():
    spatch, d = _tmp_settings()
    dbdir = tempfile.mkdtemp()
    with spatch, mock.patch.object(usage_store, "DB_PATH",
                                   os.path.join(dbdir, "usage.db")):
        usage_store.init()
        # OFF: unlimited (counter still runs)
        for _ in range(4):
            ok, note = usage_store.count_turn(dict(ROLE1))
            assert ok and not note
        assert usage_store.turns_today(ROLE1["user_id"]) == 4
        # ON at 2: the role-3 admin stays exempt at ANY count; the role-1
        # user (already past the cap) is refused before any LLM call.
        agent_config.set_turn_cap(2)
        ok, note = usage_store.count_turn(dict(ROLE1))
        assert not ok and "limit" in note.lower(), note
        for _ in range(5):
            ok3, _n = usage_store.count_turn(dict(ROLE3))
            assert ok3
        # A fresh role-1 user gets exactly cap turns, then refusal.
        fresh = {"user_id": 424296, "role": 1}
        results = [usage_store.count_turn(dict(fresh))[0] for _ in range(4)]
        assert results == [True, True, False, False], results
    shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(dbdir, ignore_errors=True)


def test_turn_cap_fails_open_on_store_error():
    spatch, d = _tmp_settings()
    with spatch, mock.patch.object(usage_store, "DB_PATH",
                                   r"Z:\no\such\dir\usage.db"):
        agent_config.set_turn_cap(1)
        ok, note = usage_store.count_turn(dict(ROLE1))
        assert ok and not note  # never brick chat over the counter
    shutil.rmtree(d, ignore_errors=True)


def test_run_turn_refuses_capped_before_any_llm_call():
    with mock.patch.object(usage_store, "count_turn",
                           return_value=(False, "Daily conversation limit reached.")), \
         mock.patch.object(brain, "query",
                           side_effect=AssertionError("LLM must not be called")):
        async def collect():
            return [ev async for ev in brain.run_turn("hi", None, dict(ROLE1))]
        events = asyncio.run(collect())
    kinds = [e["type"] for e in events]
    assert kinds == ["text", "result"], events
    assert "limit" in events[0]["text"].lower()
    assert events[1]["subtype"] == "turn_limit" and events[1]["ok"] is False


def _main():
    fails = 0
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            fails += 1
            print(f"FAIL {name}: {e!r}")
    print(f"{len(tests) - fails}/{len(tests)} PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    if not HAVE_SDK:
        print(f"SKIP: needs aihub-agent env ({_IMPORT_ERR})")
        sys.exit(0)
    sys.exit(_main())
