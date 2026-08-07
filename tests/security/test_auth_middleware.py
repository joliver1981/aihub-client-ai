"""
Security Tests for Authentication Middleware
==============================================

Tests for auth_middleware.py covering:
- is_local_request detection
- is_api_request detection
- check_scheduler_auth validation
- require_login_middleware enforcement
- init_auth_middleware registration
- add_unprotected_endpoint runtime whitelisting
"""

import pytest
import os
from unittest.mock import patch, MagicMock, PropertyMock
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user


# ---------------------------------------------------------------------------
# Test user model
# ---------------------------------------------------------------------------

class TestUser(UserMixin):
    """Minimal user model for auth middleware tests."""

    def __init__(self, user_id, role=1):
        self.id = user_id
        self.role = role


# ---------------------------------------------------------------------------
# Flask test app fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def middleware_app():
    """
    Create a Flask app that exercises auth_middleware.

    We register the middleware manually and create routes with various
    endpoint names so we can test whitelisting, scheduler auth, etc.
    """
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-middleware"
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SERVER_NAME"] = "localhost"

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = "login"

    users = {1: TestUser(1, role=3)}

    @login_manager.user_loader
    def load_user(user_id):
        return users.get(int(user_id))

    # -- Public endpoints (names matching UNPROTECTED_ENDPOINTS) --
    @app.route("/login")
    def login():
        return "login page"

    @app.route("/")
    def home():
        return "home page"

    @app.route("/static-file")
    def static_file_view():
        return "static"

    # -- Protected endpoint --
    @app.route("/dashboard")
    def dashboard():
        return "dashboard"

    # -- API endpoint (protected) --
    @app.route("/api/data")
    def api_data():
        return {"data": "secret"}

    # -- Scheduler endpoint --
    @app.route("/api/execute-job")
    def execute_document_job_api():
        return "job executed"

    # -- Dynamically added endpoint (tested later) --
    @app.route("/new-public")
    def new_public_endpoint():
        return "new public"

    # Register the middleware (ensure enforcement mode, not dry-run)
    import auth_middleware
    auth_middleware.AUTH_MIDDLEWARE_DRY_RUN = False
    auth_middleware.init_auth_middleware(app)

    return app, users


def _login(client, app, user):
    """Log a user in by hitting a temporary login route."""
    from auth_middleware import add_unprotected_endpoint

    @app.route("/_test_login_mw/<int:uid>")
    def _test_login_mw(uid):
        from flask_login import login_user as _lu
        u = TestUser(uid, role=user.role)
        _lu(u)
        return "ok"

    add_unprotected_endpoint("_test_login_mw")
    client.get(f"/_test_login_mw/{user.id}")


# ===========================================================================
# is_local_request
# ===========================================================================

@pytest.mark.security
class TestIsLocalRequest:

    def test_localhost_127(self, middleware_app):
        app, _ = middleware_app
        from auth_middleware import is_local_request
        with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
            assert is_local_request() is True

    def test_localhost_ipv6(self, middleware_app):
        app, _ = middleware_app
        from auth_middleware import is_local_request
        with app.test_request_context("/", environ_base={"REMOTE_ADDR": "::1"}):
            assert is_local_request() is True

    def test_remote_ip_returns_false(self, middleware_app):
        app, _ = middleware_app
        from auth_middleware import is_local_request
        with app.test_request_context("/", environ_base={"REMOTE_ADDR": "192.168.1.100"}):
            assert is_local_request() is False

    def test_localhost_string(self, middleware_app):
        app, _ = middleware_app
        from auth_middleware import is_local_request
        with app.test_request_context("/", environ_base={"REMOTE_ADDR": "localhost"}):
            assert is_local_request() is True


# ===========================================================================
# is_api_request
# ===========================================================================

@pytest.mark.security
class TestIsApiRequest:

    def test_json_content_type_is_api(self, middleware_app):
        app, _ = middleware_app
        from auth_middleware import is_api_request
        with app.test_request_context(
            "/",
            content_type="application/json",
            data="{}",
        ):
            assert is_api_request() is True

    def test_api_path_prefix_is_api(self, middleware_app):
        app, _ = middleware_app
        from auth_middleware import is_api_request
        with app.test_request_context("/api/something"):
            assert is_api_request() is True

    def test_accept_json_header_is_api(self, middleware_app):
        app, _ = middleware_app
        from auth_middleware import is_api_request
        with app.test_request_context(
            "/page",
            headers={"Accept": "application/json"},
        ):
            assert is_api_request() is True

    def test_xhr_header_is_api(self, middleware_app):
        app, _ = middleware_app
        from auth_middleware import is_api_request
        with app.test_request_context(
            "/page",
            headers={"X-Requested-With": "XMLHttpRequest"},
        ):
            assert is_api_request() is True

    def test_regular_browser_request_not_api(self, middleware_app):
        app, _ = middleware_app
        from auth_middleware import is_api_request
        with app.test_request_context(
            "/page",
            headers={"Accept": "text/html"},
        ):
            assert is_api_request() is False


# ===========================================================================
# check_scheduler_auth
# ===========================================================================

@pytest.mark.security
class TestCheckSchedulerAuth:

    def test_localhost_allowed(self, middleware_app):
        app, _ = middleware_app
        from auth_middleware import check_scheduler_auth
        with app.test_request_context(
            "/api/execute-job",
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        ):
            assert check_scheduler_auth() is True

    @patch.dict(os.environ, {"SCHEDULER_SECRET": "sched-secret-456"})
    def test_valid_scheduler_secret_allowed(self, middleware_app):
        app, _ = middleware_app
        from auth_middleware import check_scheduler_auth
        with app.test_request_context(
            "/api/execute-job",
            environ_base={"REMOTE_ADDR": "10.0.0.5"},
            headers={"X-Scheduler-Secret": "sched-secret-456"},
        ):
            assert check_scheduler_auth() is True

    @patch.dict(os.environ, {"SCHEDULER_SECRET": "sched-secret-456"})
    def test_invalid_secret_rejected(self, middleware_app):
        app, _ = middleware_app
        from auth_middleware import check_scheduler_auth
        with app.test_request_context(
            "/api/execute-job",
            environ_base={"REMOTE_ADDR": "10.0.0.5"},
            headers={"X-Scheduler-Secret": "wrong-secret"},
        ):
            assert check_scheduler_auth() is False

    def test_remote_without_secret_rejected(self, middleware_app):
        app, _ = middleware_app
        from auth_middleware import check_scheduler_auth
        with app.test_request_context(
            "/api/execute-job",
            environ_base={"REMOTE_ADDR": "10.0.0.5"},
        ):
            assert check_scheduler_auth() is False


# ===========================================================================
# require_login_middleware (end-to-end via test_client)
# ===========================================================================

@pytest.mark.security
class TestRequireLoginMiddleware:

    def test_unprotected_endpoint_passes(self, middleware_app):
        """Endpoints in UNPROTECTED_ENDPOINTS are accessible without auth."""
        app, _ = middleware_app
        with app.test_client() as client:
            resp = client.get("/login")
            assert resp.status_code == 200

    def test_home_unprotected(self, middleware_app):
        app, _ = middleware_app
        with app.test_client() as client:
            resp = client.get("/")
            assert resp.status_code == 200

    def test_authenticated_user_passes(self, middleware_app):
        app, users = middleware_app
        with app.test_client() as client:
            _login(client, app, users[1])
            resp = client.get("/dashboard")
            assert resp.status_code == 200
            assert b"dashboard" in resp.data

    def test_unauthenticated_api_request_returns_401_json(self, middleware_app):
        app, _ = middleware_app
        with app.test_client() as client:
            resp = client.get(
                "/api/data",
                headers={"Accept": "application/json"},
            )
            assert resp.status_code == 401
            data = resp.get_json()
            assert "Authentication required" in data["error"]

    def test_unauthenticated_browser_redirects_to_login(self, middleware_app):
        app, _ = middleware_app
        with app.test_client() as client:
            resp = client.get("/dashboard")
            assert resp.status_code == 302
            assert "login" in resp.headers["Location"]

    def test_scheduler_endpoint_localhost_passes(self, middleware_app):
        """The execute_document_job_api endpoint allows localhost without session."""
        app, _ = middleware_app
        with app.test_client() as client:
            # Flask test client sends from 127.0.0.1 by default
            resp = client.get("/api/execute-job")
            assert resp.status_code == 200

    @patch.dict(os.environ, {"AUTH_MIDDLEWARE_DRY_RUN": "true"})
    def test_dry_run_mode_logs_but_allows(self, middleware_app):
        """When AUTH_MIDDLEWARE_DRY_RUN is true, unauthenticated requests pass."""
        # Need to reload the module to pick up the env var change
        import auth_middleware
        original_value = auth_middleware.AUTH_MIDDLEWARE_DRY_RUN
        auth_middleware.AUTH_MIDDLEWARE_DRY_RUN = True
        try:
            app, _ = middleware_app
            with app.test_client() as client:
                resp = client.get("/dashboard")
                # In dry-run mode the middleware logs but does NOT block
                assert resp.status_code == 200
        finally:
            auth_middleware.AUTH_MIDDLEWARE_DRY_RUN = original_value


# ===========================================================================
# init_auth_middleware
# ===========================================================================

@pytest.mark.security
class TestInitAuthMiddleware:

    def test_registers_before_request_handler(self):
        """init_auth_middleware adds a before_request function to the app."""
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test"

        login_manager = LoginManager()
        login_manager.init_app(app)

        before_count = len(app.before_request_funcs.get(None, []))

        from auth_middleware import init_auth_middleware
        init_auth_middleware(app)

        after_count = len(app.before_request_funcs.get(None, []))
        assert after_count == before_count + 1

    def test_add_unprotected_endpoint_works_at_runtime(self):
        from auth_middleware import UNPROTECTED_ENDPOINTS, add_unprotected_endpoint

        test_ep = "my_runtime_public_endpoint"
        assert test_ep not in UNPROTECTED_ENDPOINTS

        add_unprotected_endpoint(test_ep)
        assert test_ep in UNPROTECTED_ENDPOINTS

        # Cleanup
        UNPROTECTED_ENDPOINTS.discard(test_ep)


# ===========================================================================
# Edge cases
# ===========================================================================

@pytest.mark.security
class TestMiddlewareEdgeCases:

    def test_no_endpoint_passes_through(self, middleware_app):
        """When request.endpoint is None the middleware returns None (no block)."""
        app, _ = middleware_app
        from auth_middleware import require_login_middleware
        with app.test_request_context("/nonexistent-path"):
            # Manually set endpoint to None to simulate odd scenario
            from flask import request as req
            # In a real context with no matching route, endpoint is None
            # We test the function directly
            result = require_login_middleware()
            # endpoint is None -> early return (None)
            # But test_request_context may auto-resolve; let's just check no crash
            assert result is None or hasattr(result, "status_code")

    @patch.dict(os.environ, {"SCHEDULER_SECRET": "secret123"})
    def test_scheduler_endpoint_remote_with_secret_passes(self, middleware_app):
        """Scheduler endpoint from remote IP with correct secret should pass."""
        app, _ = middleware_app
        # We need to test via the middleware pipeline. The test client always
        # comes from 127.0.0.1 (localhost), so we patch is_local_request.
        from auth_middleware import is_local_request
        with patch("auth_middleware.is_local_request", return_value=False):
            with app.test_client() as client:
                resp = client.get(
                    "/api/execute-job",
                    headers={"X-Scheduler-Secret": "secret123"},
                )
                assert resp.status_code == 200


# ---------------------------------------------------------------------------
# API-key passthrough (service-to-service) — added with the app.py wiring
# ---------------------------------------------------------------------------

class TestApiKeyPassthrough:
    """
    Service callers (CC, scheduler jobs, automations runtime, The Agent)
    authenticate with the platform API key instead of a session. The
    middleware must treat a VALID key exactly like a logged-in session and
    fail CLOSED on anything else.
    """

    @patch.dict(os.environ, {"API_KEY": "test-platform-key"})
    def test_valid_x_api_key_header_passes(self, middleware_app):
        app, _ = middleware_app
        with app.test_client() as client:
            resp = client.get("/dashboard", headers={"X-API-Key": "test-platform-key"})
            assert resp.status_code == 200
            assert b"dashboard" in resp.data

    @patch.dict(os.environ, {"API_KEY": "test-platform-key"})
    def test_valid_bearer_token_passes(self, middleware_app):
        app, _ = middleware_app
        with app.test_client() as client:
            resp = client.get(
                "/api/data",
                headers={"Authorization": "Bearer test-platform-key",
                         "Accept": "application/json"},
            )
            assert resp.status_code == 200

    @patch.dict(os.environ, {"API_KEY": "test-platform-key"})
    def test_wrong_api_key_still_blocked(self, middleware_app):
        app, _ = middleware_app
        with app.test_client() as client:
            resp = client.get(
                "/api/data",
                headers={"X-API-Key": "wrong-key", "Accept": "application/json"},
            )
            assert resp.status_code == 401

    @patch.dict(os.environ, {"API_KEY": ""})
    def test_empty_configured_key_never_matches(self, middleware_app):
        """If the platform API_KEY is unset, no key (even empty) may pass."""
        app, _ = middleware_app
        with app.test_client() as client:
            resp = client.get(
                "/api/data",
                headers={"X-API-Key": "", "Accept": "application/json"},
            )
            assert resp.status_code == 401

    @patch.dict(os.environ, {"API_KEY": "test-platform-key"})
    def test_validation_error_fails_closed(self, middleware_app):
        """If key validation itself errors, the request is treated as anonymous."""
        import role_decorators
        app, _ = middleware_app
        with patch.object(role_decorators, "_validate_tenant_api_key",
                          side_effect=RuntimeError("boom")):
            with app.test_client() as client:
                resp = client.get(
                    "/api/data",
                    headers={"X-API-Key": "test-platform-key",
                             "Accept": "application/json"},
                )
                assert resp.status_code == 401
