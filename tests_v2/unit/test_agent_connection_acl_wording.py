"""The Agent's connection tools under the platform's connection ACL
(docs/handoff-the-agent-connection-acl.md §5 item 3, 2026-09-22).

The platform scopes /get/connections and /api/discover/* to the connections
behind the Data Assistants shared with a regular user's groups. The tools are
NOT the boundary — they must describe what they see honestly:
  * a regular user's listing is labelled as scoped to their access; an empty
    listing is "not shared with you", never "none configured" (RU pack F-2)
  * an unknown name/id for a regular user is an ACCESS restriction, and an
    EMPTY index refuses a numeric id instead of passing it through (the
    fail-open the unrestricted path keeps for listing hiccups)
  * a platform 403 access:denied is relayed in the platform's own words
  * Developers / admins see exactly the pre-existing wording

Runs standalone (aihub-agent python test_agent_connection_acl_wording.py —
that env has no pytest) or under pytest; self-skips where claude_agent_sdk is
absent (the main-app sweep). Force-add to git (gitignore hides test*.py).
"""
import asyncio
import os
import sys
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import platform_tools as P                 # noqa: E402
    from platform_tools import CURRENT_USER    # noqa: E402
    import export_tools                        # noqa: E402,F401  (import wiring)
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

LIVE = [
    {"id": 20, "name": "ERPDB", "type": "SQL Server", "database": "ERPDB"},
    {"id": 59, "name": "AIRDB2", "type": "SQL Server", "database": "AIRDB2"},
]


def _run(coro):
    return asyncio.run(coro)


def _txt(res):
    return " ".join(p.get("text", "") for p in res.get("content", [])
                    if p.get("type") == "text")


class _as_user:
    def __init__(self, role, uid=13):
        self.ctx = {"user_id": uid, "role": role, "username": f"u{uid}", "tenant_id": "t1"}

    def __enter__(self):
        self.tok = CURRENT_USER.set(self.ctx)
        return self

    def __exit__(self, *exc):
        CURRENT_USER.reset(self.tok)


class _floor:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        self.saved = os.environ.get("CONNECTION_ACL_UNRESTRICTED_ROLE")
        if self.value is None:
            os.environ.pop("CONNECTION_ACL_UNRESTRICTED_ROLE", None)
        else:
            os.environ["CONNECTION_ACL_UNRESTRICTED_ROLE"] = str(self.value)

    def __exit__(self, *exc):
        if self.saved is None:
            os.environ.pop("CONNECTION_ACL_UNRESTRICTED_ROLE", None)
        else:
            os.environ["CONNECTION_ACL_UNRESTRICTED_ROLE"] = self.saved


async def _idx_full():
    return list(LIVE)


async def _idx_empty():
    return []


# ---------------------------------------------------------------------------

def test_restricted_caller_follows_the_floor():
    with _floor(None):
        with _as_user(1):
            assert P.restricted_caller()
        with _as_user(2):
            assert not P.restricted_caller()
        with _as_user(3):
            assert not P.restricted_caller()
        # the contextvar default (service principal, role 2) is never restricted
        assert not P.restricted_caller()
    with _floor(3):
        with _as_user(2):
            assert P.restricted_caller()
        with _as_user(3):
            assert not P.restricted_caller()
    with _floor("garbage"):
        with _as_user(1):
            assert P.restricted_caller()
        with _as_user(2):
            assert not P.restricted_caller()


def test_match_connection_unrestricted_wording_is_unchanged():
    m = P.match_connection
    row, err = m("Nope", LIVE)
    assert row is None and err.startswith("No connection named 'Nope'")
    row, err = m("999", LIVE)
    assert row is None and "No connection with id 999" in err
    assert m("7", [])[0] == {"id": "7", "name": "7"}       # listing hiccup pass-through
    assert m("ERPDB", LIVE)[0]["id"] == 20


def test_match_connection_restricted_wording_and_no_passthrough():
    m = P.match_connection
    row, err = m("Nope", LIVE, restricted=True)
    assert row is None
    assert "not among the connections available to your account" in err
    assert "access restriction" in err and "Groups page" in err
    assert "ERPDB" in err and "AIRDB2" in err
    assert "No connection named" not in err
    row, err = m("999", LIVE, restricted=True)
    assert row is None and "Connection id 999 is not among" in err
    # an EMPTY index is deny-all for a restricted caller — never a pass-through
    row, err = m("7", [], restricted=True)
    assert row is None and "none are shared with you" in err
    row, err = m("PHARMA", [], restricted=True)
    assert row is None and "none are shared with you" in err
    # hits are unchanged
    assert m("ERPDB", LIVE, restricted=True)[0]["id"] == 20
    assert m("20", LIVE, restricted=True)[0]["id"] == 20
    assert m("erp", LIVE, restricted=True)[0]["id"] == 20


def test_list_data_connections_wording_by_role():
    with _floor(None), mock.patch.object(P, "_connections_index", _idx_full):
        with _as_user(2):
            out = _txt(_run(P.list_data_connections.handler({})))
            assert out.startswith("Data connections:")
            assert "scoped to your access" not in out
        with _as_user(1):
            out = _txt(_run(P.list_data_connections.handler({})))
            assert out.startswith("Data connections available to your account:")
            assert "id 20 — ERPDB" in out
            assert "scoped to your access" in out and "Groups page" in out
            assert "Coverage rule" in out
    with _floor(None), mock.patch.object(P, "_connections_index", _idx_empty):
        with _as_user(2):
            res = _run(P.list_data_connections.handler({}))
            assert _txt(res) == "No data connections are configured."
        with _as_user(1):
            res = _run(P.list_data_connections.handler({}))
            out = _txt(res)
            assert "No data connections are shared with your account" in out
            assert "access restriction" in out and "not an empty platform" in out
            assert "Groups page" in out and "list_agents" in out
            assert "configured" not in out


def test_resolution_uses_the_callers_restriction():
    with _floor(None), mock.patch.object(P, "_connections_index", _idx_full):
        with _as_user(1):
            row, err = _run(P._resolve_connection_row("PHARMA"))
            assert row is None and "not among the connections available to your account" in err
            row, err = _run(P._resolve_connection_row("ERPDB"))
            assert row["id"] == 20 and err is None
        with _as_user(2):
            row, err = _run(P._resolve_connection_row("PHARMA"))
            assert row is None and "No connection named 'PHARMA'" in err


def test_denied_message_only_for_acl_403s():
    class R:
        def __init__(self, status, body):
            self.status_code = status
            self._b = body

        def json(self):
            if isinstance(self._b, Exception):
                raise self._b
            return self._b

    assert P._denied_message(R(403, {"access": "denied", "error": "nope"})) == "nope"
    assert "not shared" in P._denied_message(R(403, {"access": "denied"}))
    assert P._denied_message(R(403, {"error": "forbidden"})) is None
    assert P._denied_message(R(200, {"access": "denied"})) is None
    assert P._denied_message(R(403, ValueError("no json"))) is None
    assert P._denied_message(R(403, "a string body")) is None


def test_schema_and_probe_relay_the_platform_denial():
    denial = ("Connection 20 is not shared with your account — this is an access "
              "restriction, not a missing connection. An administrator shares a Data "
              "Assistant that uses it with one of your groups (Groups page).")

    async def fake_get(path, timeout=None):
        raise P.ConnectionAccessDenied(denial)

    async def fake_post(path, body, timeout=None):
        return {"success": False, "access": "denied", "error": denial}, 403

    with _floor(None), _as_user(1), \
            mock.patch.object(P, "_connections_index", _idx_full), \
            mock.patch.object(P, "_get", fake_get), \
            mock.patch.object(P, "_post", fake_post):
        res = _run(P.get_connection_schema.handler({"connection": "ERPDB"}))
        assert res.get("is_error") and _txt(res).startswith("Access denied:")
        assert "not shared with your account" in _txt(res)
        res = _run(P.get_connection_schema.handler({"connection": "ERPDB", "table": "dbo.X"}))
        assert res.get("is_error") and _txt(res).startswith("Access denied:")
        res = _run(P.probe_connection_query.handler({"connection": "ERPDB",
                                                     "sql": "select 1"}))
        assert res.get("is_error") and "Access denied:" in _txt(res)
        assert "Groups page" in _txt(res)
        # a denied probe is NOT a checked source for the coverage ledger
        known, queried = P.coverage_snapshot()
        assert "ERPDB" in known and queried == []


def test_search_tables_wording_by_role():
    async def fake_get(path, timeout=None):
        return {"tables": [{"TABLE_NAME": "dbo.Invoices"}]}

    with _floor(None), mock.patch.object(P, "_connections_index", _idx_full), \
            mock.patch.object(P, "_get", fake_get):
        with _as_user(1):
            out = _txt(_run(P.search_tables.handler({"pattern": "invoice"})))
            assert "connection(s) available to your account:" in out
            assert "ERPDB (id 20): dbo.Invoices" in out
            res = _run(P.search_tables.handler({"pattern": "invoice",
                                                "connections": ["PHARMA"]}))
            assert res.get("is_error")
            assert "not among the connections available to your account" in _txt(res)
        with _as_user(2):
            out = _txt(_run(P.search_tables.handler({"pattern": "invoice"})))
            assert "available to your account" not in out
            res = _run(P.search_tables.handler({"pattern": "invoice",
                                                "connections": ["PHARMA"]}))
            assert res.get("is_error") and "No connection named 'PHARMA'" in _txt(res)
    with _floor(None), mock.patch.object(P, "_connections_index", _idx_empty):
        with _as_user(1):
            out = _txt(_run(P.search_tables.handler({"pattern": "invoice"})))
            assert "No data connections are shared with your account" in out
        with _as_user(2):
            out = _txt(_run(P.search_tables.handler({"pattern": "invoice"})))
            assert out == "No data connections are configured."


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
