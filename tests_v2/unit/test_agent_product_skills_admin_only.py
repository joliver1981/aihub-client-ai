"""The Agent — product skills are visible to admins only (James 2026-10-11).

Product skills (agent_service/product_skills/, the platform know-how shipped
with the service) are still mounted into every user's sessions; only their
LISTING is admin-only:

  * GET /api/skills drops scope=product for role < 3; admins still see them.
  * GET /api/skills/read 404s a product skill for role < 3, and a group skill
    for a non-member (it used to read any group's skill by group_id).
  * POST /api/skills/delete refuses a group the caller is not in (it used to
    delete any group's skill by group_id; /api/skills/save already checked).
  * The list_skills tool omits product rows for role < 3 and says built-in
    platform skills are loaded instead.

Runs standalone (C:\\Users\\james\\miniconda3\\envs\\aihub-agent\\python.exe
tests_v2\\unit\\test_agent_product_skills_admin_only.py) or under pytest in an
env with claude_agent_sdk; without the real SDK every test self-skips.
"""
import asyncio
import os
import shutil
import sys
import tempfile
import warnings
from contextlib import contextmanager
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

warnings.filterwarnings("ignore", category=DeprecationWarning)
try:
    import claude_agent_sdk as _sdk               # noqa: E402
    # Sibling test files install a bare stub under this name; only the real
    # SDK (which has a __file__) can import the service.
    if not getattr(_sdk, "__file__", None):
        raise ImportError("claude_agent_sdk is a test stub, not the real SDK")
    import main                                   # noqa: E402
    import work_tools as W                        # noqa: E402
    import skills_mount as SM                     # noqa: E402
    import readthrough as R                       # noqa: E402
    from platform_tools import CURRENT_USER       # noqa: E402
    from fastapi.testclient import TestClient     # noqa: E402
    HAVE_SDK = True
except ImportError as e:                          # main-env pytest sweep: no claude_agent_sdk
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(
            reason=f"needs the aihub-agent env (claude_agent_sdk): {_IMPORT_ERR}")
    except ImportError:
        pass

UID = 987654                                      # never a real user on this box
MY_GROUP, OTHER_GROUP = 5, 9


def _user(role):
    return {"user_id": UID, "role": role, "username": f"u{UID}", "name": "Test User"}


@contextmanager
def _isolated_skills():
    """Every skills scope in a throwaway directory, one skill per scope, and
    the caller a member of MY_GROUP only."""
    tmp = tempfile.mkdtemp(prefix="skills_")
    saved = {k: getattr(SM, k) for k in
             ("SKILLS_PRODUCT_DIR", "SKILLS_TENANT_DIR", "USERS_DIR", "GROUPS_DIR")}
    SM.SKILLS_PRODUCT_DIR = os.path.join(tmp, "product")
    SM.SKILLS_TENANT_DIR = os.path.join(tmp, "tenant")
    SM.USERS_DIR = os.path.join(tmp, "users")
    SM.GROUPS_DIR = os.path.join(tmp, "groups")
    try:
        SM.write_skill("product", "aihub-platform-map", "Use for the platform map", "body")
        SM.write_skill("tenant", "close-the-month", "Use when closing", "body")
        SM.write_skill("group", "team-proc", "group 5", "body", group_id=MY_GROUP)
        SM.write_skill("group", "other-team", "group 9", "body", group_id=OTHER_GROUP)
        SM.write_skill("user", "my-notes", "private", "body", user_id=UID)
        with mock.patch.object(R, "user_group_ids", lambda uid: [MY_GROUP]):
            yield tmp
    finally:
        for k, v in saved.items():
            setattr(SM, k, v)
        shutil.rmtree(tmp, ignore_errors=True)


@contextmanager
def _client(role):
    with mock.patch.object(main, "_verify_request", lambda r: _user(role)):
        yield TestClient(main.app)                # no lifespan (no pollers started)


def _names(client):
    r = client.get("/api/skills")
    assert r.status_code == 200, r.text
    return {(s["scope"], s["name"]) for s in r.json()["skills"]}


# ─────────────────────────────────────────────── the Skills page API
def test_skills_page_lists_product_skills_for_admins_only():
    with _isolated_skills():
        for role in (1, 2):
            with _client(role) as c:
                names = _names(c)
                assert not any(scope == "product" for scope, _ in names), (role, names)
                assert {("tenant", "close-the-month"), ("group", "team-proc"),
                        ("user", "my-notes")} <= names
        with _client(3) as c:
            assert ("product", "aihub-platform-map") in _names(c)


def test_skills_read_hides_product_from_non_admins_and_other_groups():
    with _isolated_skills():
        with _client(1) as c:
            r = c.get("/api/skills/read", params={"scope": "product",
                                                  "name": "aihub-platform-map"})
            assert r.status_code == 404
            r = c.get("/api/skills/read", params={"scope": "group", "name": "other-team",
                                                  "group_id": OTHER_GROUP})
            assert r.status_code == 404
            r = c.get("/api/skills/read", params={"scope": "group", "name": "team-proc",
                                                  "group_id": MY_GROUP})
            assert r.status_code == 200 and "group 5" in r.json()["content"]
            r = c.get("/api/skills/read", params={"scope": "tenant", "name": "close-the-month"})
            assert r.status_code == 200
        with _client(3) as c:
            r = c.get("/api/skills/read", params={"scope": "product",
                                                  "name": "aihub-platform-map"})
            assert r.status_code == 200 and "platform map" in r.json()["content"]


def test_skills_delete_refuses_a_group_the_caller_is_not_in():
    with _isolated_skills():
        other = os.path.join(SM.GROUPS_DIR, str(OTHER_GROUP), "skills", "other-team")
        mine = os.path.join(SM.GROUPS_DIR, str(MY_GROUP), "skills", "team-proc")
        for role in (1, 3):                       # admins included: membership, like save
            with _client(role) as c:
                r = c.post("/api/skills/delete", json={"scope": "group", "name": "other-team",
                                                       "group_id": OTHER_GROUP})
                assert r.status_code == 403, (role, r.text)
                assert os.path.isdir(other)
        with _client(1) as c:
            r = c.post("/api/skills/delete", json={"scope": "group", "name": "team-proc",
                                                   "group_id": MY_GROUP})
            assert r.status_code == 200 and not os.path.exists(mine)
            r = c.post("/api/skills/delete", json={"scope": "product",
                                                   "name": "aihub-platform-map"})
            assert r.status_code == 403


# ─────────────────────────────────────────────── the list_skills tool
def _list_as(role):
    tok = CURRENT_USER.set(_user(role))
    try:
        return asyncio.run(W.list_skills_tool.handler({}))["content"][0]["text"]
    finally:
        CURRENT_USER.reset(tok)


def test_list_skills_tool_names_product_skills_for_admins_only():
    with _isolated_skills():
        for role in (1, 2):
            text = _list_as(role)
            assert "aihub-platform-map" not in text and "[product]" not in text, text
            assert "my-notes" in text and "close-the-month" in text
            assert "Built-in platform skills are also loaded" in text
        text = _list_as(3)
        assert "[product] aihub-platform-map" in text
        assert "Built-in platform skills" not in text


def test_list_skills_tool_with_only_product_skills():
    with _isolated_skills():
        for scope, base in (("tenant", SM.SKILLS_TENANT_DIR),
                            ("user", os.path.join(SM.USERS_DIR, str(UID), "skills")),
                            ("group", SM.GROUPS_DIR)):
            shutil.rmtree(base, ignore_errors=True)
        text = _list_as(1)
        assert "No skills of your own" in text and "aihub-platform-map" not in text
        assert "Built-in platform skills are also loaded" in text


def test_product_skills_still_mount_for_regular_users():
    """Hiding the listing must not take the know-how away from the agent."""
    with _isolated_skills():
        ws = SM.build_user_workspace(UID, [MY_GROUP])
        assert os.path.isfile(os.path.join(ws, ".claude", "skills",
                                           "aihub-platform-map", "SKILL.md"))


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
            except Exception as e:                    # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {e!r}")
    sys.exit(1 if failed else 0)
