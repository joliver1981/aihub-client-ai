"""
Unit tests for the usage-count cache in admin_tier_usage.py.

Background (2026-08-21): get_agent_user_env_info() ran
    SELECT COUNT(DISTINCT RequestId) FROM PlatformUsageLog WHERE ... current month
against the S1 Azure SQL tier UNCACHED on every call - every @tier_allows_feature
route hit, every tier-dashboard load and the email dispatcher's 5-minute enterprise
re-check - at 30-230 s of IO budget per run (logs/admin_tier_usage_log.txt
"TIMING:" lines). It is now:

  * cached with its own TTL (USAGE_CACHE_TTL <- config.TIER_USAGE_CACHE_TTL, env
    TIER_USAGE_CACHE_TTL), refreshed by one thread at a time, last-known values
    served to concurrent callers, invalidated by invalidate_tier_cache();
  * not loaded at all by the feature-only consumers (get_cached_tier_data(
    include_usage=False): @tier_allows_feature, @require_tier, the dispatcher's
    enterprise check) - their allow/deny behaviour is unchanged.

Everything external (pyodbc, the Cloud API) is faked; no DB or HTTP access.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time as _real_time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Keep the module's import-time log handler (and rotate_logs_on_startup) away
# from the live logs/admin_tier_usage_log.txt. Must be set BEFORE the import
# (config.load_dotenv does not override an existing env var).
os.environ["ADMIN_TIER_USAGE_LOG"] = str(
    Path(tempfile.gettempdir()) / "admin_tier_usage_unit_test_log.txt")

try:
    import admin_tier_usage as atu
except Exception as e:  # pragma: no cover - env-dependent
    pytest.skip(f"admin_tier_usage not importable here: {e}", allow_module_level=True)

from flask import Flask  # noqa: E402  (after the import guard on purpose)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

class FakeClock:
    """Stands in for the module-level `time` name: only .time() is needed."""

    def __init__(self, start=1_000_000.0):
        self.now = float(start)

    def time(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(atu, "time", c)
    return c


def _reset_caches():
    atu._tier_cache["data"] = None
    atu._tier_cache["timestamp"] = 0
    atu._usage_cache["data"] = None
    atu._usage_cache["timestamp"] = 0
    atu._usage_cache["last_error"] = None
    # never leave the refresh lock held behind (a failing test could)
    if atu._usage_cache["lock"].locked():
        try:
            atu._usage_cache["lock"].release()
        except RuntimeError:
            pass


@pytest.fixture(autouse=True)
def reset_caches(monkeypatch):
    _reset_caches()
    monkeypatch.setattr(atu, "USAGE_CACHE_TTL", 300)
    yield
    _reset_caches()


@pytest.fixture
def fake_db(monkeypatch):
    """
    Replace pyodbc.connect as used by admin_tier_usage with a fake that serves the
    three usage queries and counts connections. One get_agent_user_env_info()
    refresh = 2 connections (cloud + local).
    """
    state = {"connects": 0, "fail": False,
             "requests": 3848, "env": 2, "agents": 5, "tools": 1, "users": 4}

    class Cursor:
        def __init__(self):
            self._last = ""

        def execute(self, sql, *params):
            if state["fail"]:
                raise RuntimeError("boom: db down")
            self._last = sql

        def fetchone(self):
            s = self._last
            if "PlatformUsageLog" in s:
                return SimpleNamespace(request_count=state["requests"])
            if "AgentEnvironments" in s:
                return SimpleNamespace(env_count=state["env"], agent_count=state["agents"],
                                       tool_count=state["tools"], user_count=state["users"])
            if "total_users" in s:
                return SimpleNamespace(total_users=state["users"], admin_count=1,
                                       developer_count=1, user_count=state["users"] - 2)
            return None

        def close(self):
            pass

    class Conn:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    def connect(*args, **kwargs):
        if state["fail"]:
            raise RuntimeError("boom: db down")
        state["connects"] += 1
        return Conn()

    monkeypatch.setattr(atu.pyodbc, "connect", connect)
    monkeypatch.setattr(atu, "get_cloud_db_connection_string", lambda: "DRIVER=fake;cloud")
    monkeypatch.setattr(atu, "get_db_connection_string", lambda: "DRIVER=fake;local")
    return state


def _expected_usage(state):
    return {"environments": state["env"], "agents": state["agents"],
            "custom_tools": state["tools"], "users": state["users"],
            "requests": state["requests"]}


SUBSCRIPTION = {
    "success": True,
    "tier_features": {
        "documents_enabled": True,
        "workflows_enabled": False,
        "environments_enabled": True,
        "enterprise_features_enabled": False,
        "max_agents": 5,
        "max_users": 10,
        "max_environments": -1,
    },
    "settings": {},
    "subscription": {"current_tier": "professional"},
    "tenant_info": {"name": "unit"},
    "original_tier_features": {},
}


@pytest.fixture
def fake_cloud(monkeypatch):
    """Canned Cloud API data: records the force_refresh flags it was called with."""
    calls = []

    def fake_limits(force_refresh=False):
        calls.append(force_refresh)
        return SUBSCRIPTION

    monkeypatch.setattr(atu, "get_subscription_limits_from_cloud", fake_limits)
    return calls


@pytest.fixture
def app():
    return Flask("admin_tier_usage_unit_test")


# ---------------------------------------------------------------------------
# get_agent_user_env_info(): the TTL cache itself
# ---------------------------------------------------------------------------

class TestUsageCache:
    def test_first_call_queries_then_served_from_cache_within_ttl(self, fake_db, clock):
        us, cu = atu.get_agent_user_env_info()
        assert cu == _expected_usage(fake_db)
        assert us["total"] == 4
        assert us["by_role"] == {"admins": 1, "developers": 1, "users": 2}
        assert fake_db["connects"] == 2

        fake_db["requests"] = 9999  # DB changes, cache must not notice yet
        clock.advance(299)
        us2, cu2 = atu.get_agent_user_env_info()
        assert (us2, cu2) == (us, cu)
        assert fake_db["connects"] == 2, "served from cache: no new connections"

    def test_refreshes_after_ttl(self, fake_db, clock, monkeypatch):
        monkeypatch.setattr(atu, "USAGE_CACHE_TTL", 120)
        atu.get_agent_user_env_info()
        fake_db["requests"] = 4000
        clock.advance(120)  # age == TTL -> expired
        _, cu = atu.get_agent_user_env_info()
        assert cu["requests"] == 4000
        assert fake_db["connects"] == 4

    def test_force_refresh_bypasses_cache(self, fake_db, clock):
        atu.get_agent_user_env_info()
        fake_db["agents"] = 7
        _, cu = atu.get_agent_user_env_info(force_refresh=True)
        assert cu["agents"] == 7
        assert fake_db["connects"] == 4

    def test_invalidate_tier_cache_clears_usage_cache_too(self, fake_db, clock):
        atu.get_agent_user_env_info()
        assert atu.get_usage_cache_status()["has_data"] is True
        atu.invalidate_tier_cache()
        assert atu.get_usage_cache_status()["has_data"] is False
        assert atu._tier_cache["data"] is None
        fake_db["users"] = 11
        _, cu = atu.get_agent_user_env_info()
        assert cu["users"] == 11
        assert fake_db["connects"] == 4

    def test_invalidate_usage_cache_alone(self, fake_db, clock):
        atu.get_agent_user_env_info()
        atu.invalidate_usage_cache()
        atu.get_agent_user_env_info()
        assert fake_db["connects"] == 4

    def test_callers_get_copies_so_mutation_cannot_poison_the_cache(self, fake_db, clock):
        us, cu = atu.get_agent_user_env_info()
        cu["agents"] = 999
        us["by_role"]["admins"] = 999
        us2, cu2 = atu.get_agent_user_env_info()
        assert cu2["agents"] == 5
        assert us2["by_role"]["admins"] == 1
        assert fake_db["connects"] == 2

    def test_failure_with_empty_cache_returns_empty_dicts_and_records_error(self, fake_db, clock):
        fake_db["fail"] = True
        assert atu.get_agent_user_env_info() == ({}, {})
        status = atu.get_usage_cache_status()
        assert status["has_data"] is False
        assert "boom" in (status["last_error"] or "")
        # recovery: next call queries again and clears the error
        fake_db["fail"] = False
        _, cu = atu.get_agent_user_env_info()
        assert cu == _expected_usage(fake_db)
        assert atu.get_usage_cache_status()["last_error"] is None

    def test_failure_with_stale_cache_serves_last_known_values(self, fake_db, clock):
        _, cu_first = atu.get_agent_user_env_info()
        clock.advance(301)  # stale
        fake_db["fail"] = True
        us, cu = atu.get_agent_user_env_info()
        assert cu == cu_first, "stale values beat empty dicts"
        assert us["total"] == 4
        status = atu.get_usage_cache_status()
        assert status["has_data"] is True and "boom" in status["last_error"]

    def test_invalidation_during_a_refresh_discards_that_result(self, fake_db, clock, monkeypatch):
        real_query = atu._query_agent_user_env_info

        def query_then_invalidate():
            result = real_query()
            atu.invalidate_usage_cache()  # happens while the refresh is "in flight"
            return result

        monkeypatch.setattr(atu, "_query_agent_user_env_info", query_then_invalidate)
        _, cu = atu.get_agent_user_env_info()
        assert cu == _expected_usage(fake_db), "the caller still gets the fresh result"
        assert atu._usage_cache["data"] is None, "but the (pre-invalidation) result is not cached"

    def test_concurrent_callers_get_last_known_values_while_one_thread_refreshes(self, fake_db, clock, monkeypatch):
        _, cu_first = atu.get_agent_user_env_info()
        clock.advance(301)  # stale -> next caller refreshes

        started, gate = threading.Event(), threading.Event()
        real_query = atu._query_agent_user_env_info

        def slow_query():
            started.set()
            assert gate.wait(5), "test gate never released"
            return real_query()

        monkeypatch.setattr(atu, "_query_agent_user_env_info", slow_query)
        fake_db["requests"] = 5000
        results = {}

        def refresher():
            results["refresher"] = atu.get_agent_user_env_info()

        t = threading.Thread(target=refresher, daemon=True)
        t.start()
        assert started.wait(5)

        t0 = _real_time.monotonic()
        us, cu = atu.get_agent_user_env_info()  # must NOT block behind the refresher
        assert _real_time.monotonic() - t0 < 1.0
        assert cu == cu_first, "served the last-known values immediately"

        gate.set()
        t.join(5)
        assert results["refresher"][1]["requests"] == 5000
        assert atu._usage_cache["data"][1]["requests"] == 5000
        # only the refresher touched the DB (2 connections) after the first load
        assert fake_db["connects"] == 4

    def test_concurrent_caller_with_empty_cache_waits_for_the_refresh(self, fake_db, clock, monkeypatch):
        started, gate = threading.Event(), threading.Event()
        real_query = atu._query_agent_user_env_info

        def slow_query():
            started.set()
            assert gate.wait(5)
            return real_query()

        monkeypatch.setattr(atu, "_query_agent_user_env_info", slow_query)
        results = {}
        t = threading.Thread(target=lambda: results.__setitem__("a", atu.get_agent_user_env_info()), daemon=True)
        t.start()
        assert started.wait(5)
        threading.Timer(0.2, gate.set).start()
        us, cu = atu.get_agent_user_env_info()  # nothing to serve -> waits for thread A
        t.join(5)
        assert cu == _expected_usage(fake_db)
        assert results["a"][1] == cu
        assert fake_db["connects"] == 2, "the waiter re-used A's result instead of querying again"

    def test_query_closes_connections_even_when_a_statement_fails(self, monkeypatch):
        closed = []

        class Cursor:
            def execute(self, *a):
                raise RuntimeError("statement failed")

            def close(self):
                pass

        class Conn:
            def cursor(self):
                return Cursor()

            def close(self):
                closed.append(True)

        monkeypatch.setattr(atu.pyodbc, "connect", lambda *a, **k: Conn())
        monkeypatch.setattr(atu, "get_cloud_db_connection_string", lambda: "x")
        monkeypatch.setattr(atu, "get_db_connection_string", lambda: "y")
        with pytest.raises(RuntimeError):
            atu._query_agent_user_env_info()
        assert closed == [True]


# ---------------------------------------------------------------------------
# get_usage_cache_status() / config plumbing
# ---------------------------------------------------------------------------

class TestUsageCacheStatusAndConfig:
    def test_status_shape_empty_then_loaded_then_expired(self, fake_db, clock):
        s = atu.get_usage_cache_status()
        assert s == {"has_data": False, "age_seconds": None, "ttl_seconds": 300,
                     "is_expired": True, "next_refresh_in": 0, "last_error": None}
        atu.get_agent_user_env_info()
        s = atu.get_usage_cache_status()
        assert s["has_data"] is True and s["age_seconds"] == 0.0
        assert s["is_expired"] is False and s["next_refresh_in"] == 300
        clock.advance(301)
        s = atu.get_usage_cache_status()
        assert s["is_expired"] is True and s["next_refresh_in"] == 0 and s["age_seconds"] == 301.0

    def test_module_ttl_follows_config(self):
        # USAGE_CACHE_TTL is resolved from config.TIER_USAGE_CACHE_TTL at import
        # (the autouse fixture pins it to 300 for the other tests).
        expected = int(getattr(atu.cfg, "TIER_USAGE_CACHE_TTL", 0) or 300)
        import importlib
        src = Path(atu.__file__).read_text(encoding="utf-8")
        assert "USAGE_CACHE_TTL = int(getattr(cfg, 'TIER_USAGE_CACHE_TTL', 0) or 300)" in src
        assert expected > 0

    def test_config_env_override_and_default(self):
        from tests_v2.unit.test_config_loading import _fresh_config, _restore
        cfg, saved, saved_env = _fresh_config(env={"TIER_USAGE_CACHE_TTL": "45"})
        try:
            assert cfg.TIER_USAGE_CACHE_TTL == 45
        finally:
            _restore(saved, saved_env)
        cfg, saved, saved_env = _fresh_config(env={"TIER_USAGE_CACHE_TTL": None})
        try:
            assert cfg.TIER_USAGE_CACHE_TTL == 300
            assert cfg.TIER_CACHE_TTL == 1800  # untouched
        finally:
            _restore(saved, saved_env)


# ---------------------------------------------------------------------------
# get_cached_tier_data(include_usage=...)
# ---------------------------------------------------------------------------

class TestGetCachedTierDataIncludeUsage:
    def test_include_usage_false_never_queries(self, app, fake_db, fake_cloud, clock):
        with app.app_context():
            td = atu.get_cached_tier_data(include_usage=False)
        assert td["tier_features"]["documents_enabled"] is True
        assert td["current_usage"] == {} and td["user_statistics"] == {}
        assert fake_db["connects"] == 0

    def test_include_usage_false_attaches_last_known_counts_even_when_stale(self, app, fake_db, fake_cloud, clock):
        atu.get_agent_user_env_info()  # populate
        clock.advance(10_000)          # very stale
        with app.app_context():
            td = atu.get_cached_tier_data(include_usage=False)
        assert td["current_usage"] == _expected_usage(fake_db)
        assert fake_db["connects"] == 2, "last-known values, no re-query"

    def test_include_usage_true_loads_once_per_request(self, app, fake_db, fake_cloud, clock):
        with app.app_context():
            td1 = atu.get_cached_tier_data()
            td2 = atu.get_cached_tier_data()
        assert td1 is td2
        assert td1["current_usage"] == _expected_usage(fake_db)
        assert fake_db["connects"] == 2

    def test_feature_only_then_usage_required_upgrades_g_in_place(self, app, fake_db, fake_cloud, clock):
        with app.app_context():
            td = atu.get_cached_tier_data(include_usage=False)
            assert fake_db["connects"] == 0
            td2 = atu.get_cached_tier_data(include_usage=True)
            assert td2 is td
            assert td["current_usage"] == _expected_usage(fake_db)
            assert fake_db["connects"] == 2
            atu.get_cached_tier_data(include_usage=True)  # already loaded for this request
            assert fake_db["connects"] == 2

    def test_usage_is_shared_across_requests_through_the_ttl_cache(self, app, fake_db, fake_cloud, clock):
        with app.app_context():
            atu.get_cached_tier_data()
        with app.app_context():
            td = atu.get_cached_tier_data()
        assert td["current_usage"]["requests"] == 3848
        assert fake_db["connects"] == 2

    def test_force_refresh_refreshes_both_caches(self, app, fake_db, fake_cloud, clock):
        with app.app_context():
            atu.get_cached_tier_data()
        fake_db["requests"] = 4242
        with app.app_context():
            td = atu.get_cached_tier_data(force_refresh=True)
        assert td["current_usage"]["requests"] == 4242
        assert fake_db["connects"] == 4
        assert fake_cloud[-1] is True

    def test_returns_none_when_cloud_data_unavailable(self, app, fake_db, monkeypatch):
        monkeypatch.setattr(atu, "get_subscription_limits_from_cloud", lambda force_refresh=False: None)
        with app.app_context():
            assert atu.get_cached_tier_data(include_usage=False) is None
            assert atu.get_cached_tier_data() is None
        assert fake_db["connects"] == 0


# ---------------------------------------------------------------------------
# Decorators: identical allow/deny behaviour, no usage queries for feature gates
# ---------------------------------------------------------------------------

@pytest.fixture
def gated_app(app):
    @app.route("/docs")
    @atu.tier_allows_feature("documents")
    def docs():
        return "docs ok"

    @app.route("/wf")
    @atu.tier_allows_feature("workflows")
    def wf():
        return "wf ok"

    @app.route("/agents", methods=["POST"])
    @atu.tier_allows_resource("agents")
    def make_agent():
        return "created"

    @app.route("/envs", methods=["POST"])
    @atu.tier_allows_resource("environments")
    def make_env():
        return "created"

    @app.route("/pro")
    @atu.require_tier("professional")
    def pro():
        return "pro ok"

    @app.route("/ent")
    @atu.require_tier("enterprise")
    def ent():
        return "ent ok"

    @app.route("/multi_features")
    @atu.check_usage_limits({"features": ["documents", "environments"]})
    def multi_features():
        return "multi ok"

    @app.route("/multi_resources")
    @atu.check_usage_limits({"features": ["documents"], "resources": ["agents"]})
    def multi_resources():
        return "multi res ok"

    @app.route("/stacked", methods=["POST"])
    @atu.tier_allows_feature("documents")
    @atu.tier_allows_resource("agents")
    def stacked():
        return "stacked ok"

    return app


class TestDecorators:
    def test_tier_allows_feature_allows_without_usage_queries(self, gated_app, fake_db, fake_cloud):
        r = gated_app.test_client().get("/docs")
        assert r.status_code == 200 and r.data == b"docs ok"
        assert fake_db["connects"] == 0, "feature gate must not run the usage-count queries"

    def test_tier_allows_feature_denies_without_usage_queries(self, gated_app, fake_db, fake_cloud):
        r = gated_app.test_client().get("/wf")
        assert r.status_code == 403
        body = r.get_json()
        assert body["feature_required"] == "workflows" and body["upgrade_required"] is True
        assert fake_db["connects"] == 0

    def test_tier_allows_feature_503_when_subscription_unavailable(self, gated_app, fake_db, monkeypatch):
        monkeypatch.setattr(atu, "get_subscription_limits_from_cloud", lambda force_refresh=False: None)
        r = gated_app.test_client().get("/docs")
        assert r.status_code == 503
        assert fake_db["connects"] == 0

    def test_require_tier_uses_no_usage_queries(self, gated_app, fake_db, fake_cloud):
        c = gated_app.test_client()
        assert c.get("/pro").status_code == 200
        r = c.get("/ent")
        assert r.status_code == 403 and r.get_json()["required_tier"] == "enterprise"
        assert fake_db["connects"] == 0

    def test_tier_allows_resource_enforces_limit_from_cached_counts(self, gated_app, fake_db, fake_cloud, clock):
        c = gated_app.test_client()
        r = c.post("/agents")  # 5 agents of max 5 -> at limit
        assert r.status_code == 403
        body = r.get_json()
        assert body["current_usage"] == 5 and body["max_allowed"] == 5
        assert fake_db["connects"] == 2

        fake_db["agents"] = 3  # DB changed, but the cached count (5) is what is enforced within the TTL
        assert c.post("/agents").status_code == 403
        assert fake_db["connects"] == 2

        clock.advance(301)     # cache expired -> fresh count -> allowed
        r = c.post("/agents")
        assert r.status_code == 200 and r.data == b"created"
        assert fake_db["connects"] == 4

    def test_tier_allows_resource_unlimited_passes(self, gated_app, fake_db, fake_cloud):
        assert gated_app.test_client().post("/envs").status_code == 200  # max_environments == -1

    def test_check_usage_limits_features_only_skips_usage(self, gated_app, fake_db, fake_cloud):
        assert gated_app.test_client().get("/multi_features").status_code == 200
        assert fake_db["connects"] == 0

    def test_check_usage_limits_with_resources_loads_usage(self, gated_app, fake_db, fake_cloud):
        r = gated_app.test_client().get("/multi_resources")
        assert r.status_code == 403 and r.get_json()["resource_type"] == "agents"
        assert fake_db["connects"] == 2

    def test_stacked_feature_then_resource_loads_usage_once(self, gated_app, fake_db, fake_cloud):
        fake_db["agents"] = 1
        r = gated_app.test_client().post("/stacked")
        assert r.status_code == 200 and r.data == b"stacked ok"
        assert fake_db["connects"] == 2

    def test_second_request_within_ttl_reuses_counts(self, gated_app, fake_db, fake_cloud):
        c = gated_app.test_client()
        c.post("/agents")
        c.post("/agents")
        assert fake_db["connects"] == 2
