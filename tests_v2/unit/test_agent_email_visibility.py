"""Email screen visibility + published-address links (james 2026-11-12).

Two additive changes, both OFF until someone configures them:

1. An Admin can hide The Agent's Email screen from everyone or from the
   members of chosen platform groups (settings key `email_hidden`).
   - absent (the default) = shown to everyone, and NO group lookup happens;
   - "everyone" hides it for every role, Admins included;
   - a group rule hides it for members only, and an unreadable membership
     fails CLOSED (hidden) — strict=True on readthrough.user_group_ids;
   - hidden = /api/me says so (the rail hides the entry), the screen's five
     routes answer 403, and chat will neither create an address nor offer to.
   It is not a mail switch: an address that already exists is untouched.

2. Behind a reverse proxy the two services are not on "this hostname + a
   port" any more:
   - APP_PUBLIC_BASE_URL, while USE_REVERSE_PROXY is on -> /api/me main_url
     + the token-less /api/main-app, so the UI's links back to the main app
     (and the sign-in bounce) work. With the switch off the address is NOT
     used for this, even where it is already set for emailed links;
   - AGENT_PUBLIC_BASE_URL -> the main app's /the-agent redirect.
   Unset, every link is exactly what it was.

Runs standalone (aihub-agent python test_agent_email_visibility.py) or under
pytest. The service tests skip in envs without the real claude_agent_sdk
(main-app sweep); the main app's redirect and the UI checks run everywhere.
Force-add to git (gitignore hides test*.py).
"""
import ast
import asyncio
import functools
import json
import os
import sys
import tempfile
import unittest
import warnings
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for _p in (APP_ROOT, os.path.join(APP_ROOT, "agent_service")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

APP_PY = os.path.join(APP_ROOT, "app.py")
INDEX = os.path.join(APP_ROOT, "agent_service", "static", "index.html")

warnings.filterwarnings("ignore", category=DeprecationWarning)
try:
    import claude_agent_sdk as _sdk             # noqa: E402
    # Sibling test files install a bare stub under this name so readthrough
    # imports in the main env. A stub has no __file__; only the real SDK can
    # import agent_service/main.py for the route tests below.
    if not getattr(_sdk, "__file__", None):
        raise ImportError("claude_agent_sdk is a test stub, not the real SDK")
    import agent_config                         # noqa: E402
    import email_client                         # noqa: E402
    import email_store                          # noqa: E402
    import main                                 # noqa: E402
    import readthrough                          # noqa: E402
    import work_tools                           # noqa: E402
    from platform_tools import CURRENT_USER     # noqa: E402
    from fastapi.testclient import TestClient   # noqa: E402
    HAVE_SDK = True
    _IMPORT_ERR = ""
except ImportError as e:                        # main-env sweep: no claude_agent_sdk
    HAVE_SDK = False
    _IMPORT_ERR = str(e)



def needs_sdk(fn):
    """Skip a test that drives the Agent service where it cannot be imported."""
    @functools.wraps(fn)
    def run():
        if not HAVE_SDK:
            raise unittest.SkipTest(
                f"needs the aihub-agent env (claude_agent_sdk): {_IMPORT_ERR}")
        return fn()
    return run


# never real users or groups on this box
ROLE1 = {"user_id": 515101, "role": 1, "username": "emailvis-u1", "name": "U1", "tenant_id": ""}
ROLE2 = {"user_id": 515102, "role": 2, "username": "emailvis-u2", "name": "U2", "tenant_id": ""}
ADMIN = {"user_id": 515103, "role": 3, "username": "emailvis-u3", "name": "U3", "tenant_id": ""}
G_STORES, G_OFFICE = 9101, 9102

HIDDEN_ROUTES = [("get", "/api/email/address"), ("post", "/api/email/address"),
                 ("get", "/api/email/log"), ("get", "/api/email/log/1"),
                 ("get", "/api/email/log/1/attachment/1")]


def _txt(res) -> str:
    return " ".join(c.get("text", "") for c in res.get("content", []))


def _no_lookup(*a, **k):
    raise AssertionError("group membership must not be looked up")


class settings:
    """A fresh temp settings.json for the duration of a block."""

    def __init__(self, initial=None):
        self.initial = initial

    def __enter__(self):
        self.path = os.path.join(tempfile.mkdtemp(prefix="agent-emailvis-"), "settings.json")
        if self.initial is not None:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.initial, f)
        self._p = mock.patch.object(agent_config, "RUNTIME_SETTINGS_PATH", self.path)
        self._p.start()
        return self

    def __exit__(self, *exc):
        self._p.stop()
        return False

    def stored(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except OSError:
            return {}


def _groups(ids):
    """readthrough.user_group_ids stand-in; records how it was called."""
    calls = []

    def fake(user_id, *, strict=False):
        calls.append((user_id, strict))
        return list(ids)
    fake.calls = calls
    return fake


def _as(user):
    return mock.patch.object(main, "_verify_request", lambda r: dict(user))


# ------------------------------------------------------------- the setting

@needs_sdk
def test_default_is_shown_to_everyone_and_never_looks_up_groups():
    with settings(), mock.patch.object(readthrough, "user_group_ids", _no_lookup):
        assert agent_config.get_email_hidden() == {"everyone": False, "group_ids": []}
        for u in (ROLE1, ROLE2, ADMIN, {}, None):
            assert agent_config.email_hidden_for(u) is False


@needs_sdk
def test_everyone_hides_for_every_role_without_a_group_lookup():
    with settings() as s, mock.patch.object(readthrough, "user_group_ids", _no_lookup):
        assert agent_config.set_email_hidden(True, []) == {"everyone": True, "group_ids": []}
        assert s.stored()["email_hidden"] == {"everyone": True, "group_ids": []}
        for u in (ROLE1, ROLE2, ADMIN):
            assert agent_config.email_hidden_for(u) is True


@needs_sdk
def test_group_rule_hides_members_only_whatever_their_role():
    with settings():
        agent_config.set_email_hidden(False, [G_STORES, G_OFFICE])
        member = _groups([3, G_OFFICE])
        with mock.patch.object(readthrough, "user_group_ids", member):
            assert agent_config.email_hidden_for(ROLE1) is True
            assert agent_config.email_hidden_for(ADMIN) is True       # no admin exemption
        # the lookup is the strict one: "could not look" must not read as "no groups"
        assert member.calls == [(ROLE1["user_id"], True), (ADMIN["user_id"], True)]
        for ids in ([3, 4], []):
            with mock.patch.object(readthrough, "user_group_ids", _groups(ids)):
                assert agent_config.email_hidden_for(ROLE1) is False


@needs_sdk
def test_unreadable_membership_fails_closed():
    def boom(user_id, *, strict=False):
        raise RuntimeError("database unreachable")
    with settings(), mock.patch.object(readthrough, "user_group_ids", boom):
        agent_config.set_email_hidden(False, [G_STORES])
        assert agent_config.email_hidden_for(ROLE1) is True


@needs_sdk
def test_set_validates_and_clearing_removes_the_key():
    with settings({"model": "keep-me"}) as s:
        for everyone, gids in (("yes", []), (1, []), (None, []),
                               (False, "7"), (False, None), (False, [True]),
                               (False, ["7"]), (False, [0]), (False, [-3]),
                               (False, [7.5])):
            try:
                agent_config.set_email_hidden(everyone, gids)
            except ValueError:
                continue
            raise AssertionError(f"accepted {everyone!r}, {gids!r}")
        assert "email_hidden" not in s.stored()                  # nothing half-written

        assert agent_config.set_email_hidden(False, [G_OFFICE, G_STORES, G_OFFICE]) == \
            {"everyone": False, "group_ids": [G_OFFICE, G_STORES]}
        assert s.stored() == {"model": "keep-me",
                              "email_hidden": {"everyone": False,
                                               "group_ids": [G_OFFICE, G_STORES]}}
        # back to the default: the key goes away, other settings stay
        assert agent_config.set_email_hidden(False, []) == {"everyone": False, "group_ids": []}
        assert s.stored() == {"model": "keep-me"}


@needs_sdk
def test_a_hand_edited_setting_is_read_safely():
    with settings({"email_hidden": {"everyone": "true", "group_ids": ["4", 4, "x", 0, -1, 5]}}):
        # only a real true hides everyone; ids are cleaned, never trusted raw
        assert agent_config.get_email_hidden() == {"everyone": False, "group_ids": [4, 5]}
    for junk in ("nonsense", [1, 2], 7, None):
        with settings({"email_hidden": junk}), \
             mock.patch.object(readthrough, "user_group_ids", _no_lookup):
            assert agent_config.get_email_hidden() == {"everyone": False, "group_ids": []}
            assert agent_config.email_hidden_for(ROLE1) is False


@needs_sdk
def test_user_group_ids_strict_raises_and_the_default_still_returns_empty():
    def boom(op, sql_fn, **params):
        raise RuntimeError("database unreachable")
    with mock.patch.object(readthrough, "fetch_or_sql", boom):
        assert readthrough.user_group_ids(ROLE1["user_id"]) == []        # every existing caller
        try:
            readthrough.user_group_ids(ROLE1["user_id"], strict=True)
        except RuntimeError:
            pass
        else:
            raise AssertionError("strict lookup swallowed the failure")
    with mock.patch.object(readthrough, "fetch_or_sql", lambda op, sql_fn, **p: ["4", 5]):
        assert readthrough.user_group_ids(1) == readthrough.user_group_ids(1, strict=True) == [4, 5]


# ------------------------------------------------------------------ routes

@needs_sdk
def test_me_reports_the_rule_and_only_admins_see_the_setting():
    with settings(), mock.patch.dict(os.environ, {"APP_PUBLIC_BASE_URL": ""}):
        client = TestClient(main.app)              # no lifespan (no pollers started)
        with _as(ROLE1), mock.patch.object(readthrough, "user_group_ids", _no_lookup):
            d = client.get("/api/me").json()
            assert d["email_hidden"] is False
            assert "email_visibility" not in d and "main_url" not in d
        agent_config.set_email_hidden(False, [G_STORES])
        with mock.patch.object(readthrough, "user_group_ids", _groups([G_STORES])):
            with _as(ROLE1):
                d = client.get("/api/me").json()
                assert d["email_hidden"] is True and "email_visibility" not in d
            with _as(ROLE2):
                assert "email_visibility" not in client.get("/api/me").json()
            with _as(ADMIN):
                d = client.get("/api/me").json()
                assert d["email_hidden"] is True
                assert d["email_visibility"] == {"everyone": False, "group_ids": [G_STORES]}


@needs_sdk
def test_the_email_screen_routes_refuse_a_hidden_user():
    with settings():
        client = TestClient(main.app)
        agent_config.set_email_hidden(False, [G_STORES])
        with _as(ROLE1), mock.patch.object(readthrough, "user_group_ids", _groups([G_STORES])):
            for method, path in HIDDEN_ROUTES:
                r = (client.post(path, json={"prefix": "x"}) if method == "post"
                     else client.get(path))
                assert r.status_code == 403, (method, path, r.status_code)
                assert "turned off" in r.json()["detail"], (method, path)
        # not a member: the guard lets them through (it raises nothing)
        with mock.patch.object(readthrough, "user_group_ids", _groups([G_OFFICE])):
            main._require_email_screen(dict(ROLE1))
        # and with no rule at all nobody is looked up, let alone refused
        agent_config.set_email_hidden(False, [])
        with mock.patch.object(readthrough, "user_group_ids", _no_lookup):
            main._require_email_screen(dict(ROLE1))


@needs_sdk
def test_the_setting_routes_are_admin_only():
    with settings() as s:
        client = TestClient(main.app)
        for u in (ROLE1, ROLE2):
            with _as(u):
                assert client.get("/api/settings/email-visibility").status_code == 403
                assert client.post("/api/settings/email-visibility",
                                   json={"everyone": True, "group_ids": []}).status_code == 403
        assert "email_hidden" not in s.stored()


@needs_sdk
def test_admin_reads_and_saves_the_setting():
    groups = [{"id": G_OFFICE, "name": "Office"}, {"id": G_STORES, "name": "Stores"}]
    with settings() as s, _as(ADMIN):
        client = TestClient(main.app)
        with mock.patch.object(readthrough, "all_groups", lambda: list(groups)):
            d = client.get("/api/settings/email-visibility").json()
        assert d == {"everyone": False, "group_ids": [], "groups": groups, "groups_error": ""}

        # the Admin is in the group they pick: the answer says it now hides for THEM too
        with mock.patch.object(readthrough, "user_group_ids", _groups([G_STORES])):
            r = client.post("/api/settings/email-visibility",
                            json={"everyone": False, "group_ids": [G_STORES]})
        assert r.status_code == 200, r.text
        assert r.json() == {"everyone": False, "group_ids": [G_STORES], "email_hidden": True}
        assert s.stored()["email_hidden"] == {"everyone": False, "group_ids": [G_STORES]}

        # a group list that cannot be read: the saved rule still comes back, with the reason
        def boom():
            raise RuntimeError("database unreachable")
        with mock.patch.object(readthrough, "all_groups", boom):
            d = client.get("/api/settings/email-visibility").json()
        assert d["group_ids"] == [G_STORES] and d["groups"] == [] and d["groups_error"]

        for bad in ({"everyone": "yes", "group_ids": []},
                    {"everyone": False, "group_ids": ["x"]},
                    {"everyone": False, "group_ids": "7"}):
            r = client.post("/api/settings/email-visibility", json=bad)
            assert r.status_code == 400, (bad, r.status_code)
        assert s.stored()["email_hidden"] == {"everyone": False, "group_ids": [G_STORES]}

        with mock.patch.object(readthrough, "user_group_ids", _no_lookup):
            r = client.post("/api/settings/email-visibility",
                            json={"everyone": False, "group_ids": []})
        assert r.json() == {"everyone": False, "group_ids": [], "email_hidden": False}
        assert "email_hidden" not in s.stored()


# ------------------------------------------------------------------- chat

def _run(tool, args, user, **email_store_fakes):
    CURRENT_USER.set(dict(user))

    async def tenant():
        return {"tenant_id": 77, "domain": "mail.example.com"}
    with mock.patch.multiple(email_store, **email_store_fakes), \
         mock.patch.object(email_client, "tenant_info", tenant):
        return asyncio.run(tool.handler(args))


@needs_sdk
def test_chat_will_not_create_an_address_for_a_hidden_user():
    def never(*a, **k):
        raise AssertionError("an address must not be created")
    with settings():
        agent_config.set_email_hidden(True, [])
        res = _run(work_tools.setup_agent_email, {"confirmed": True, "prefix": "sales"},
                   ROLE1, get_address=lambda uid: None, upsert_address=never)
        assert res.get("is_error") is True
        assert "turned off" in _txt(res) and "administrator" in _txt(res)


@needs_sdk
def test_status_tool_stops_offering_set_up_to_a_hidden_user():
    with settings():
        shown = _txt(_run(work_tools.get_agent_email_status, {}, ROLE1,
                          get_address=lambda uid: None))
        assert "OFFER" in shown                               # unchanged when not hidden

        agent_config.set_email_hidden(True, [])
        hidden = _txt(_run(work_tools.get_agent_email_status, {}, ROLE1,
                           get_address=lambda uid: None))
        assert "turned off" in hidden and "do NOT offer" in hidden
        assert "OFFER TO SET IT UP" not in hidden

        # an address that already exists keeps working and is still reported
        row = {"email_address": "u1-agent.77@mail.example.com", "is_active": 1,
               "outbound_enabled": 1, "auto_send": 0}
        have = _txt(_run(work_tools.get_agent_email_status, {}, ROLE1,
                         get_address=lambda uid: dict(row), recent=lambda addr, n: []))
        assert "u1-agent.77@mail.example.com" in have and "ENABLED" in have


# ------------------------------------------------- published-address links

@needs_sdk
def test_main_url_is_sent_only_for_a_real_http_address():
    with settings(), _as(ROLE1):
        client = TestClient(main.app)
        for raw, want in (("https://hub.example.com/", "https://hub.example.com"),
                          ("  http://hub.example.com:8080  ", "http://hub.example.com:8080"),
                          ("hub.example.com", None), ("javascript:alert(1)", None), ("", None)):
            with mock.patch.dict(os.environ, {"APP_PUBLIC_BASE_URL": raw,
                                              "USE_REVERSE_PROXY": "true"}):
                d = client.get("/api/me").json()
                assert d.get("main_url") == want, (raw, d.get("main_url"))
                assert d["main_port"] == int(os.getenv("HOST_PORT", "5001"))


@needs_sdk
def test_main_url_stays_off_on_a_direct_install_that_set_the_address_for_email():
    """APP_PUBLIC_BASE_URL already exists (emailed View links). Without the
    reverse-proxy switch the links back keep "this hostname + the main app's
    port" — the user's sign-in lives on the address they browsed to."""
    with settings(), _as(ROLE1):
        client = TestClient(main.app)
        for switch in ("false", "", "0", "no"):
            with mock.patch.dict(os.environ, {"APP_PUBLIC_BASE_URL": "http://hubserver:5001",
                                              "USE_REVERSE_PROXY": switch}):
                assert "main_url" not in client.get("/api/me").json(), switch
                assert client.get("/api/main-app").json()["main_url"] == "", switch
        with mock.patch.dict(os.environ, {"APP_PUBLIC_BASE_URL": "http://hubserver:5001"}):
            os.environ.pop("USE_REVERSE_PROXY", None)
            assert "main_url" not in client.get("/api/me").json()
        for switch in ("true", "TRUE", " 1 ", "yes"):
            with mock.patch.dict(os.environ, {"APP_PUBLIC_BASE_URL": "http://hubserver:5001",
                                              "USE_REVERSE_PROXY": switch}):
                assert client.get("/api/me").json()["main_url"] == "http://hubserver:5001"


@needs_sdk
def test_main_app_lookup_needs_no_token():
    def boom(request):
        raise AssertionError("/api/main-app must answer before sign-in")
    with mock.patch.object(main, "_verify_request", boom):
        client = TestClient(main.app)
        with mock.patch.dict(os.environ, {"APP_PUBLIC_BASE_URL": "https://hub.example.com",
                                          "USE_REVERSE_PROXY": "true", "HOST_PORT": "5001"}):
            assert client.get("/api/main-app").json() == \
                {"main_url": "https://hub.example.com", "main_port": 5001}
        with mock.patch.dict(os.environ, {"APP_PUBLIC_BASE_URL": "", "HOST_PORT": "6001"}):
            assert client.get("/api/main-app").json() == {"main_url": "", "main_port": 6001}


def _the_agent_redirect(env, host="hubserver:5001"):
    """Run the main app's /the-agent route body (lifted from app.py, the
    decorators dropped) against fakes — app.py itself cannot be imported in
    this env. Returns the address it redirects to."""
    with open(APP_PY, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "the_agent_redirect")
    fn.decorator_list = []
    warned = []
    ns = {
        "os": os, "redirect": lambda url: url, "url_for": lambda name, **k: f"/{name}",
        "flash": lambda *a, **k: None, "session": {},
        "current_user": mock.Mock(id=1, role=3, TenantId=1, username="u", name="U"),
        "request": mock.Mock(host=host),
        "cleanup_expired_cc_tokens": lambda: None,
        "_mint_cc_token": lambda claims: "TOKEN",
        "logger": mock.Mock(warning=lambda *a, **k: warned.append(a)),
    }
    exec(compile(ast.Module(body=[fn], type_ignores=[]), APP_PY, "exec"), ns)
    base = {"THE_AGENT_ENABLED": "true", "AGENT_ALLOW_ALL_USERS": "true", "HOST_PORT": "5001",
            "AGENT_SERVICE_PORT": "", "AGENT_SERVICE_HOST_PUBLIC": "",
            "AGENT_PUBLIC_BASE_URL": ""}
    with mock.patch.dict(os.environ, dict(base, **env)):
        return ns["the_agent_redirect"](), warned


def test_the_main_app_redirect_uses_the_published_agent_address():
    # unset: exactly the link every direct install has always had
    assert _the_agent_redirect({}) == ("http://hubserver:5111?token=TOKEN", [])
    assert _the_agent_redirect({"AGENT_SERVICE_PORT": "5199"})[0] == \
        "http://hubserver:5199?token=TOKEN"
    # published through a reverse proxy
    assert _the_agent_redirect({"AGENT_PUBLIC_BASE_URL": "https://agent.example.com/"}) == \
        ("https://agent.example.com/?token=TOKEN", [])
    assert _the_agent_redirect({"AGENT_PUBLIC_BASE_URL": "https://hub.example.com:8443"})[0] == \
        "https://hub.example.com:8443/?token=TOKEN"
    # not an address: ignored (and logged), the old link still works
    url, warned = _the_agent_redirect({"AGENT_PUBLIC_BASE_URL": "agent.example.com"})
    assert url == "http://hubserver:5111?token=TOKEN" and warned


def test_the_ui_takes_the_main_app_address_from_the_service():
    with open(INDEX, encoding="utf-8") as f:
        ui = f.read()
    # both sign-in bounces and every platform link go through the one address
    assert "`${mainAppBase()}/logout?then=agent`" in ui
    assert "location.href = `${mainAppBase()}/the-agent`" in ui
    assert ":${mp}/the-agent" not in ui and ":${mp}/logout" not in ui
    assert 'fetch("/api/main-app")' in ui
    assert "mainUrl = d.main_url || \"\"" in ui
    assert "applyEmailHidden(d.email_hidden)" in ui


def _main():
    fails, skipped = 0, []
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except unittest.SkipTest as e:
            skipped.append(name)
            print(f"SKIP {name}: {e}")
        except Exception as e:                    # noqa: BLE001
            fails += 1
            print(f"FAIL {name}: {e!r}")
    print(f"{len(tests) - fails - len(skipped)}/{len(tests)} PASS"
          + (f", {len(skipped)} skipped" if skipped else ""))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(_main())
