"""Fixtures for UI clickability / reachability tests.

Self-contained — does NOT import tests/conftest.py (which pulls in the full
e2e plugin baggage). Just the minimum to log in once and reuse a session.
"""
import os
import pytest
import requests
from playwright.sync_api import sync_playwright, Browser, Page

MAIN_BASE = os.environ.get("AI_HUB_BASE_URL", "http://localhost:5001")
CC_BASE = os.environ.get("CC_BASE_URL", "http://localhost:5091")
TEST_USER = os.environ.get("UI_TEST_USER", "admin")
TEST_PASS = os.environ.get("UI_TEST_PASS", "admin")


def _services_up() -> tuple[bool, str]:
    """Return (True, '') if both services respond, else (False, reason)."""
    for url, name in [(f"{MAIN_BASE}/login", "main app"),
                       (f"{CC_BASE}/api/health", "command center")]:
        try:
            r = requests.get(url, timeout=3, allow_redirects=False)
            if r.status_code >= 500:
                return False, f"{name} returned {r.status_code}"
        except requests.exceptions.RequestException as e:
            return False, f"{name} unreachable: {e}"
    return True, ""


@pytest.fixture(scope="session")
def services_ready():
    """Hard-skip the whole suite if the stack isn't up."""
    ok, reason = _services_up()
    if not ok:
        pytest.skip(f"UI tests require live services: {reason}")
    return True


def _login(page: Page) -> bool:
    """Log into the main app. Returns True on success."""
    try:
        page.goto(f"{MAIN_BASE}/login", timeout=15000)
        page.wait_for_load_state("networkidle", timeout=10000)
        if "/login" not in page.url:
            return True  # already authed
        username_sel = 'input[name="username"], input[name="email"], #username, #email'
        password_sel = 'input[name="password"], input[type="password"], #password'
        submit_sel = 'button[type="submit"], input[type="submit"], .btn-login'
        page.locator(username_sel).first.fill(TEST_USER)
        page.locator(password_sel).first.fill(TEST_PASS)
        page.locator(submit_sel).first.click()
        page.wait_for_url(lambda url: "/login" not in url, timeout=10000)
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"login failed: {e}")
        return False


@pytest.fixture(scope="session")
def _playwright_instance():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(_playwright_instance, services_ready):
    """One headless chromium for the whole session."""
    browser = _playwright_instance.chromium.launch(headless=True)
    yield browser
    browser.close()


@pytest.fixture(scope="session")
def auth_state(browser: Browser, tmp_path_factory):
    """Log in once, save storage state, reuse across all tests."""
    state_path = str(tmp_path_factory.mktemp("ui_auth") / "storage.json")
    ctx = browser.new_context(ignore_https_errors=True)
    page = ctx.new_page()
    ok = _login(page)
    if not ok:
        ctx.close()
        pytest.skip("Cannot log into main app — UI clickability tests need admin/admin")
    ctx.storage_state(path=state_path)
    ctx.close()
    return state_path
