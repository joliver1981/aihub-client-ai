"""Sign out from The Agent (james 2026-11-22).

The Agent had no way to sign out; the classic app did. The Agent's rail now
carries a Sign out button. It does not add a second logout: it drops what The
Agent holds in the browser (the token) and then goes through AI Hub's own
/logout, the route behind the classic app's Logout, so local, directory and
single sign-on accounts all end the same way.

What is pinned here (no live services needed):

1. AI Hub's /logout, as The Agent uses it: it signs the user out, ends the
   sticky Classic choice, and with ?then=agent lands on the sign-in form with
   the way back into The Agent. `then` is a fixed keyword, never an address.
2. The Agent's page: the button exists and is wired; signing out removes the
   token BEFORE leaving the page; a signed-out browser stops on a plain
   "signed out" screen instead of taking the silent re-auth bounce.

3. Logout in the CLASSIC app ends The Agent in that browser too. The Agent's
   token sits in the browser storage of The Agent's own address, out of the
   main app's reach, so a classic Logout used to leave The Agent working.
   Now /logout leaves a one-time note in the session, the landing page it
   lands on loads The Agent's "signed out" page in a hidden frame, and that
   page removes the token. Nothing is added where The Agent is off.

The whole flow in a real browser (click, sign-in form, Back, reload, a second
tab, Remember Me, signing in again) is the live journey test
tests_v2/journeys/test_journey_07_agent_sign_out.py.

Runs standalone (python test_agent_sign_out.py) or under pytest, in either
env. Force-add to git (gitignore hides test*.py).
"""
import ast
import os
import re
import sys
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
APP_PY = os.path.join(APP_ROOT, "app.py")
INDEX = os.path.join(APP_ROOT, "agent_service", "static", "index.html")
BASE_HTML = os.path.join(APP_ROOT, "templates", "base.html")
LANDING_HTML = os.path.join(APP_ROOT, "templates", "landing.html")
SIGNED_OUT_PAGE = os.path.join(APP_ROOT, "agent_service", "static", "signed-out.html")


# ---------------------------------------------------------------- AI Hub /logout
def _logout(args, session=None):
    """Run the main app's /logout route body (lifted from app.py, the route
    decorator dropped) against fakes: app.py itself cannot be imported here.
    Returns (where it redirects to, the session afterwards, logout_user mock)."""
    with open(APP_PY, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "logout")
    fn.decorator_list = []
    session = {} if session is None else session
    logout_user = mock.Mock()

    def url_for(name, **kw):
        url = f"/{name}"
        if kw:
            url += "?" + "&".join(f"{k}={v}" for k, v in sorted(kw.items()))
        return url

    ns = {"logout_user": logout_user, "session": session,
          "request": mock.Mock(args=args), "redirect": lambda url: url,
          "url_for": url_for}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), APP_PY, "exec"), ns)
    return ns["logout"](), session, logout_user


def test_logout_from_the_agent_lands_on_the_sign_in_form_with_the_way_back():
    where, session, logout_user = _logout({"then": "agent"},
                                          {"classic_mode": True, "other": 1})
    logout_user.assert_called_once_with()
    assert where == "/login?next=/the_agent_redirect"
    # the sticky Classic choice ends with the session; nothing else is touched
    # here (The Agent has already dropped its own token on this path)
    assert session == {"other": 1}


def test_plain_logout_lands_where_it_always_did_and_leaves_the_note_for_the_agent():
    where, session, logout_user = _logout({}, {"classic_mode": True})
    logout_user.assert_called_once_with()
    assert where == "/home"
    assert session == {"agent_sign_out": True}


def test_then_is_a_keyword_not_an_address():
    for value in ("https://elsewhere.example.com", "//elsewhere.example.com",
                  "/the-agent", "Agent", ""):
        where, _, logout_user = _logout({"then": value})
        logout_user.assert_called_once_with()
        assert where == "/home", value


def test_the_classic_logout_link_is_the_same_route():
    with open(BASE_HTML, encoding="utf-8") as f:
        assert "url_for('logout')" in f.read()


# ------------------------------------------- classic Logout ends The Agent as well
def _agent_sign_out_page(session, env=None, host="hubserver:5001", request=None):
    """Run the main app's agent_sign_out_page() (lifted from app.py, the
    decorator dropped) against fakes. Returns (the address, log warnings)."""
    with open(APP_PY, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "agent_sign_out_page")
    assert any("template_global" in ast.unparse(d) for d in fn.decorator_list), (
        "the landing page calls it by name, so it must be a template global")
    fn.decorator_list = []
    warned = []
    ns = {"os": os, "session": session,
          "request": request if request is not None else mock.Mock(host=host),
          "logger": mock.Mock(warning=lambda *a, **k: warned.append(a))}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), APP_PY, "exec"), ns)
    base = {"THE_AGENT_ENABLED": "true", "HOST_PORT": "5001", "AGENT_SERVICE_PORT": "",
            "AGENT_SERVICE_HOST_PUBLIC": "", "AGENT_PUBLIC_BASE_URL": ""}
    with mock.patch.dict(os.environ, dict(base, **(env or {}))):
        return ns["agent_sign_out_page"](), warned


def test_the_address_is_given_once_right_after_a_logout():
    session = {"agent_sign_out": True, "other": 1}
    assert _agent_sign_out_page(session) == (
        "http://hubserver:5111/static/signed-out.html", [])
    assert session == {"other": 1}                      # the note is used up
    assert _agent_sign_out_page(session) == ("", [])   # a later visit gets nothing


def test_no_logout_means_no_address_and_nothing_is_looked_up():
    class Boom:
        @property
        def host(self):
            raise AssertionError("the request was read without a logout")
    with mock.patch.object(os, "getenv", side_effect=AssertionError("settings were read")):
        assert _agent_sign_out_page({"other": 1}, request=Boom()) == ("", [])


def test_nothing_is_added_where_the_agent_is_off():
    for off in ("false", "", "0"):
        session = {"agent_sign_out": True}
        assert _agent_sign_out_page(session, {"THE_AGENT_ENABLED": off}) == ("", [])
        assert session == {}


def test_the_address_follows_the_same_settings_as_the_way_in():
    # every form the /the-agent link can take (test_agent_email_visibility)
    def page(env):
        return _agent_sign_out_page({"agent_sign_out": True}, env)[0]
    assert page({"AGENT_SERVICE_PORT": "5199"}) == "http://hubserver:5199/static/signed-out.html"
    assert page({"HOST_PORT": "6001"}) == "http://hubserver:6111/static/signed-out.html"
    assert page({"AGENT_SERVICE_HOST_PUBLIC": "agents.internal"}) == (
        "http://agents.internal:5111/static/signed-out.html")
    assert page({"AGENT_PUBLIC_BASE_URL": "https://agent.example.com/"}) == (
        "https://agent.example.com/static/signed-out.html")
    # not an address: ignored, the direct link is used
    assert page({"AGENT_PUBLIC_BASE_URL": "agent.example.com"}) == (
        "http://hubserver:5111/static/signed-out.html")


def test_a_failure_here_never_breaks_the_landing_page():
    url, warned = _agent_sign_out_page({"agent_sign_out": True}, {"HOST_PORT": "not-a-number"})
    assert url == "" and warned


def test_the_landing_page_loads_it_in_a_hidden_frame_only_when_given():
    with open(LANDING_HTML, encoding="utf-8") as f:
        page = f.read()
    start = page.index("{% set agent_page = agent_sign_out_page() %}")
    block = page[start:page.index("{% endif %}", start)]
    assert block.index("{% if agent_page %}") < block.index("<iframe")
    assert 'src="{{ agent_page }}"' in block
    assert "display:none" in block and 'aria-hidden="true"' in block
    # scripts + its own storage, nothing else (it cannot move the page it is in)
    assert 'sandbox="allow-scripts allow-same-origin"' in block
    assert page.count("agent_sign_out_page()") == 1     # the note is read once


def test_the_agents_signed_out_page_drops_the_token_and_leaves_the_mark():
    with open(SIGNED_OUT_PAGE, encoding="utf-8") as f:
        page = f.read()
    script = page[page.index("<script>"):page.index("</script>")]
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("//"))
    cleared = ('localStorage.removeItem("agent_token")',
               'localStorage.removeItem("agent_draft")',
               'localStorage.setItem("agent_signed_out", String(Date.now()))')
    for done in cleared:
        assert done in code, done
    # it only clears this browser: no request, no navigation, no other script
    for absent in ("fetch(", "XMLHttpRequest", "location", "src=", "import"):
        assert absent not in code, absent
    # the same three things The Agent's own Sign out does to this storage
    own = _function_body(_ui(), "signOut")
    for shared in cleared:
        assert shared in own, shared


# ---------------------------------------------------------------- The Agent's page
def _ui():
    with open(INDEX, encoding="utf-8") as f:
        return f.read()


def _function_body(ui, name):
    """The source of one top-level function in the page's script."""
    m = re.search(r"\n  (?:async )?function " + re.escape(name) + r"\([^)]*\) \{\n(.*?)\n  \}\n",
                  ui, re.S)
    assert m, f"function {name} not found in the page"
    return m.group(1)


def test_the_rail_has_a_sign_out_button_wired_to_sign_out():
    ui = _ui()
    boot = _function_body(ui, "boot")
    assert 'el("button", "signout", "⇥ Sign out")' in boot
    assert "out.onclick = signOut;" in boot
    assert '<div class="who" id="who"></div>' in ui      # where it is placed
    assert ".signout{" in ui                             # and it is styled
    # the name beside it is put on the page as text, never as markup
    assert 'who.appendChild(el("b", "", d.user.name || d.user.username))' in boot
    assert 'getElementById("who").innerHTML' not in ui


def test_sign_out_drops_the_token_before_it_leaves_the_page():
    body = _function_body(_ui(), "signOut")
    leave = body.index("location.href = `${mainAppBase()}/logout?then=agent`")
    for dropped in ('localStorage.removeItem("agent_token")',
                    'localStorage.removeItem("agent_draft")',
                    'sessionStorage.removeItem("agent_session")',
                    'localStorage.setItem("agent_signed_out"'):
        assert dropped in body, dropped
        assert body.index(dropped) < leave, dropped
    # one logout for the platform: the page calls no sign-out of its own
    assert "fetch(" not in body


def test_a_signed_out_browser_stops_instead_of_signing_itself_back_in():
    ui = _ui()
    boot = _function_body(ui, "boot")
    stop = boot.index("if (isSignedOut()) { signedOutGate(); return; }")
    assert stop < boot.index('reauth("no access token")')
    gate = _function_body(ui, "signedOutGate")
    assert "You're signed out" in gate
    # its Sign in replaces this page: links here open a new tab unless told otherwise
    assert "href: `${mainAppBase()}/the-agent`, sameTab: true" in gate
    assert 'if (action.sameTab) a.target = "_self";' in _function_body(ui, "gate")
    # the mark goes when AI Hub next sends a token (a completed sign-in)
    arrive = ui.index('localStorage.setItem("agent_token", params.get("token"))')
    assert 'localStorage.removeItem("agent_signed_out")' in ui[arrive:arrive + 300]


def test_other_tabs_and_pages_restored_by_back_are_signed_out_too():
    ui = _ui()
    assert re.search(r'addEventListener\("storage", \(e\) => \{\s*'
                     r'if \(e\.key === "agent_signed_out" && e\.newValue\) location\.reload\(\);', ui)
    assert re.search(r'addEventListener\("pageshow", \(e\) => \{\s*'
                     r'if \(e\.persisted && isSignedOut\(\) && !token\(\)\) location\.reload\(\);', ui)


def test_the_session_expired_screen_is_unchanged():
    body = _function_body(_ui(), "sessionExpired")
    assert "href: `${mainAppBase()}/logout?then=agent`" in body
    assert "agent_signed_out" not in body


def _main():
    fails = 0
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:                    # noqa: BLE001
            fails += 1
            print(f"FAIL {name}: {e!r}")
    print(f"{len(tests) - fails}/{len(tests)} PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(_main())
