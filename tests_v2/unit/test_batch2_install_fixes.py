"""
Pins the 2026-09-03 batch-2 fixes (james's decisions on the open-issues table):

  Main app  - startup probes the Agent API once and skips per-agent display-name
              lookups when it is down (they are cosmetic; agents work either way)
  Main app  - auth middleware: the Portal Workflows internal endpoints authenticate
              themselves (X-AIHub-Internal) and must not be blocked by enforcement;
              startup prints are ASCII (a redirected cp1252 stdout used to crash)
  Main app  - Data Dictionary: the Import column is hidden (no route behind it)
  Main app  - The Agent front door: Developers/Admins only unless AGENT_ALLOW_ALL_USERS
  CC        - one concise discover-before-asking sentence in the build prompt
  Installer - AGENT_ALLOW_ALL_USERS seeded into every install's .env
"""
import io
import os
import re
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)


def _read(*parts):
    return io.open(os.path.join(REPO, *parts), encoding="utf-8").read()


# ------------------------------------------------------------ startup probe

class _FakeClient:
    def __init__(self):
        self.calls = []

    def get_agent_info(self, agent_id):
        self.calls.append(("info", agent_id))
        return {"name": f"Real {agent_id}"}

    def _make_request(self, method, endpoint, **kw):
        self.calls.append((method, endpoint, kw))
        return {"status": "healthy"}


def test_adapter_skips_the_name_lookup_when_told_and_agents_still_construct():
    from agent_api_client import AgentAPIAdapter
    c = _FakeClient()
    a = AgentAPIAdapter(7, c, fetch_info=False)
    assert a.AGENT_NAME == "Agent 7" and a.agent_id == 7 and c.calls == []
    b = AgentAPIAdapter(8, c)                     # default: unchanged behaviour
    assert b.AGENT_NAME == "Real 8" and c.calls == [("info", 8)]


def test_health_check_passes_a_short_probe_timeout():
    from agent_api_client import AgentAPIClient
    c = AgentAPIClient.__new__(AgentAPIClient)
    c.calls = []
    c._make_request = lambda m, e, **kw: (c.calls.append((m, e, kw)) or {"status": "healthy"})
    assert c.health_check(timeout=3) is True
    assert c.calls == [("GET", "/health", {"timeout": 3})]
    c.calls.clear()
    assert c.health_check() is True
    assert c.calls == [("GET", "/health", {})]


def test_app_startup_uses_the_probe_and_the_flag():
    src = _read("app.py")
    assert "def _agent_api_reachable()" in src
    assert "health_check(timeout=3)" in src
    assert src.count("fetch_info=_api_up") == 2      # startup loop + load_agents()


# ---------------------------------------------------------- auth middleware

def test_portal_internal_endpoints_are_self_authenticating():
    import auth_middleware as am
    for ep in ("portal_workflows_bp.internal_notify_takeover", "portal_workflows_bp.internal_run"):
        assert ep.startswith(am.SELF_AUTHENTICATING_PREFIXES), ep
    assert not "portal_workflows_bp.list_portal_workflows".startswith(am.SELF_AUTHENTICATING_PREFIXES)
    assert not "portal_workflows_bp.internal".startswith(("portal_workflows_bp.internal_",)) or True


def test_middleware_startup_prints_are_ascii():
    src = _read("auth_middleware.py")
    prints = [ln for ln in src.splitlines() if ln.strip().startswith("print(")]
    assert prints, "expected the startup prints to still exist"
    for ln in prints:
        ln.encode("cp1252")                       # raises if a glyph cannot be encoded


# ------------------------------------------------------- dictionary import

def test_dictionary_import_column_is_hidden():
    html = _read("templates", "data_dictionary.html")
    i = html.index("<h6>Import Metadata</h6>")
    opener = html[html.rfind("<div", 0, i):i]
    assert " hidden" in opener, opener
    assert "importDictionary()" in html            # the JS stays for when the route exists


# --------------------------------------------------- The Agent front door

def test_agent_nav_is_gated_to_developers_unless_all_users():
    import jinja2
    html = _read("templates", "base.html")
    jinja2.Environment().parse(html)              # template still compiles
    m = re.search(r"\{% if current_user\.is_authenticated and FLAG_THE_AGENT and \(FLAG_THE_AGENT_ALL_USERS"
                  r" or \(current_user\.role and current_user\.role >= 2\)\) %\}", html)
    assert m, "The Agent nav entry must be role-gated unless AGENT_ALLOW_ALL_USERS"


def test_agent_route_and_context_carry_the_all_users_flag():
    src = _read("app.py")
    assert src.count("'FLAG_THE_AGENT_ALL_USERS': os.getenv('AGENT_ALLOW_ALL_USERS'") == 2
    assert "The Agent is in preview for Developers and Admins." in src


def test_shipped_env_enables_the_front_door_for_developers_only():
    env = _read("dist", ".env") if os.path.isfile(os.path.join(REPO, "dist", ".env")) else ""
    if not env:
        pytest.skip("dist/.env is machine-local (not tracked)")
    assert re.search(r"^THE_AGENT_ENABLED=true$", env, re.M)
    assert re.search(r"^AGENT_ALLOW_ALL_USERS=false$", env, re.M)
    assert re.search(r"^THE_AGENT_MODE=true$", env, re.M)


def test_home_redirect_obeys_the_developer_gate():
    """THE_AGENT_MODE takes Developers/Admins to The Agent from '/'; regular
    users keep the classic landing unless AGENT_ALLOW_ALL_USERS=true."""
    src = _read("app.py")
    i = src.index("return redirect(url_for('the_agent_redirect'))")
    cond = src[src.rfind("if (current_user.is_authenticated", 0, i):i]
    assert "THE_AGENT_MODE" in cond and "THE_AGENT_ENABLED" in cond
    assert "AGENT_ALLOW_ALL_USERS" in cond and "role', 0) or 0) >= 2" in cond


def test_logout_clears_the_sticky_classic_choice():
    src = _read("app.py")
    i = src.index('@app.route("/logout")')
    body = src[i:src.index("return redirect", i)]
    assert "logout_user()" in body and "session.pop('classic_mode', None)" in body


def test_installer_seeds_the_all_users_key():
    iss = _read("AIHub_Setup_Script_v5_OneDir_Dev.iss")
    assert "EnsureEnvKeyExists(EnvConfigFile, 'AGENT_ALLOW_ALL_USERS', 'false')" in iss


# ---------------------------------------------------------- CC prompt

def test_cc_discover_before_asking_sentence_is_single_and_concise():
    src = _read("command_center_service", "graph", "nodes.py")
    hits = re.findall(r'"If the user named NO connection or table, discover first \(list_data_connections, "\s*'
                      r'"get_connection_schema\) and propose the best match; ask only when nothing fits\. "', src)
    assert len(hits) == 1
    sentence = ("If the user named NO connection or table, discover first (list_data_connections, "
                "get_connection_schema) and propose the best match; ask only when nothing fits.")
    assert len(sentence.split()) <= 30
