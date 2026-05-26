"""Fixtures for multi-step user-journey tests.

These tests drive a real browser (Playwright + headless Chromium) through
end-to-end UX flows against a LIVE stack at:
  - Main Flask app: http://localhost:5001  (admin/admin)
  - Command Center service: http://localhost:5091

The goal is to catch "every individual page works but the actual user flow
breaks" — modal won't close after submit, list doesn't refresh after create,
session lost on navigation, etc.

Skips the whole suite if either service is unreachable. Login pattern is
copied inline (not imported) from tests_v2/ui/conftest.py and
tests_v2/auth_e2e/conftest.py to keep this suite self-contained.

Run:
    C:\\Users\\james\\miniconda3\\envs\\aihub2.1\\python.exe \
        -m pytest tests_v2/journeys/ -v --tb=short
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
import requests
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

# ---------------------------------------------------------------------------
# Make project root importable so we can pull DB config if a journey needs it.
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

MAIN_BASE = os.environ.get("AI_HUB_BASE_URL", "http://localhost:5001")
CC_BASE = os.environ.get("CC_BASE_URL", "http://localhost:5091")
TEST_USER = os.environ.get("UI_TEST_USER", "admin")
TEST_PASS = os.environ.get("UI_TEST_PASS", "admin")

# Test artifacts created during a journey use this prefix so cleanup can find
# them. NEVER touch records without this prefix.
ARTIFACT_PREFIX = "JOURNEY_TEST_"


# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------
def pytest_configure(config):
    config.addinivalue_line(
        "markers", "slow: long-running journey test (30s-120s)"
    )
    config.addinivalue_line(
        "markers", "journey: multi-step user-journey end-to-end test"
    )


# ---------------------------------------------------------------------------
# Service readiness
# ---------------------------------------------------------------------------
def _services_up() -> tuple[bool, str]:
    """Return (True, '') if both services respond, else (False, reason)."""
    for url, name in [
        (f"{MAIN_BASE}/login", "main app (5001)"),
        (f"{CC_BASE}/api/health", "command center (5091)"),
    ]:
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
        pytest.skip(f"Journey tests require live services: {reason}")
    return True


# ---------------------------------------------------------------------------
# Login (copied from tests_v2/ui/conftest.py — keep this suite self-contained)
# ---------------------------------------------------------------------------
def _login(page: Page) -> bool:
    """Log into the main app via the HTML login form. Returns True on success."""
    try:
        page.goto(f"{MAIN_BASE}/login", timeout=15000)
        page.wait_for_load_state("networkidle", timeout=10000)
        if "/login" not in page.url:
            return True  # already authed (session cookie reused)
        username_sel = 'input[name="username"], input[name="email"], #username, #email'
        password_sel = 'input[name="password"], input[type="password"], #password'
        submit_sel = 'button[type="submit"], input[type="submit"], .btn-login'
        page.locator(username_sel).first.fill(TEST_USER)
        page.locator(password_sel).first.fill(TEST_PASS)
        page.locator(submit_sel).first.click()
        page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass  # networkidle is best-effort — pages that poll never go idle
        return True
    except Exception as e:
        print(f"[journeys] login failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Playwright lifecycle
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def _playwright_instance():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(_playwright_instance, services_ready) -> Browser:
    """One headless chromium for the whole session."""
    b = _playwright_instance.chromium.launch(headless=True)
    yield b
    b.close()


@pytest.fixture(scope="session")
def auth_state(browser: Browser, tmp_path_factory) -> str:
    """Log in once, save storage state, reuse across all journey tests."""
    state_path = str(tmp_path_factory.mktemp("journey_auth") / "storage.json")
    ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1366, "height": 768})
    page = ctx.new_page()
    ok = _login(page)
    if not ok:
        ctx.close()
        pytest.skip(f"Cannot log into {MAIN_BASE} as {TEST_USER}/{TEST_PASS}")
    ctx.storage_state(path=state_path)
    ctx.close()
    return state_path


@pytest.fixture
def authed_page(browser: Browser, auth_state: str):
    """Fresh browser context with the stored auth, fresh page, 1366x768.

    Each test gets a clean context so cookies/local-storage from earlier tests
    can't bleed in.
    """
    ctx = browser.new_context(
        storage_state=auth_state,
        ignore_https_errors=True,
        viewport={"width": 1366, "height": 768},
    )
    page = ctx.new_page()
    page.set_default_timeout(15000)
    yield page
    try:
        ctx.close()
    except Exception:
        pass


@pytest.fixture
def fresh_page(browser: Browser):
    """An UNAUTHENTICATED browser context. Used by JOURNEY-1 and JOURNEY-6."""
    ctx = browser.new_context(
        ignore_https_errors=True,
        viewport={"width": 1366, "height": 768},
    )
    page = ctx.new_page()
    page.set_default_timeout(15000)
    yield page
    try:
        ctx.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# API helpers — used to short-circuit setup (e.g. create a workflow via API
# then verify the UI reflects it) and to clean up.
#
# Each helper pulls the session cookies out of the Playwright auth state so
# we authenticate as the same admin user.
# ---------------------------------------------------------------------------
def _session_from_auth_state(auth_state_path: str) -> requests.Session:
    """Build a requests.Session from the Playwright storage_state JSON.

    Falls back to form-login if the stored cookies don't authenticate (e.g.
    state expired between writes).
    """
    import json

    s = requests.Session()
    s.headers.update({"User-Agent": "aihub-journeys/1.0"})
    try:
        with open(auth_state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for c in data.get("cookies", []):
            s.cookies.set(
                c["name"],
                c["value"],
                domain=c.get("domain"),
                path=c.get("path", "/"),
            )
    except Exception:
        pass
    # Quick sanity check; if we're not authed, do a form login to refresh.
    probe = s.get(f"{MAIN_BASE}/dashboard", timeout=10, allow_redirects=False)
    if probe.status_code in (301, 302) and "/login" in (probe.headers.get("Location", "")):
        _form_login(s)
    return s


def _form_login(session: requests.Session) -> bool:
    """Fallback form-login (mirrors auth_e2e/conftest.py do_login)."""
    import re

    get = session.get(f"{MAIN_BASE}/login", timeout=10)
    m = re.search(
        r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']',
        get.text,
    )
    csrf = m.group(1) if m else None
    payload = {"username": TEST_USER, "password": TEST_PASS, "submit": "Login"}
    if csrf:
        payload["csrf_token"] = csrf
    r = session.post(
        f"{MAIN_BASE}/login", data=payload, allow_redirects=False, timeout=15
    )
    return r.status_code in (301, 302)


@pytest.fixture
def http_session(auth_state: str) -> requests.Session:
    """An authenticated requests.Session against the main app."""
    s = _session_from_auth_state(auth_state)
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Artifact cleanup — best-effort. Runs at module scope so journeys that crash
# mid-flow don't leak rows.
# ---------------------------------------------------------------------------
def _delete_journey_agents(session: requests.Session) -> int:
    """Delete any agents whose name starts with ARTIFACT_PREFIX. Returns count."""
    n = 0
    try:
        r = session.get(f"{MAIN_BASE}/api/agents/summary", timeout=10)
        if r.status_code != 200:
            return 0
        agents = (r.json() or {}).get("agents", []) or []
    except Exception:
        return 0
    for a in agents:
        name = (a.get("agent_name") or "")
        if name.startswith(ARTIFACT_PREFIX):
            try:
                dr = session.post(
                    f"{MAIN_BASE}/delete/agent",
                    json={"agent_id": a.get("agent_id")},
                    timeout=15,
                )
                if dr.status_code == 200:
                    n += 1
            except Exception:
                pass
    return n


def _delete_journey_workflows(session: requests.Session) -> int:
    """Delete any workflows whose name starts with ARTIFACT_PREFIX."""
    n = 0
    try:
        r = session.get(f"{MAIN_BASE}/api/workflows/list", timeout=10)
        if r.status_code != 200:
            return 0
        workflows = (r.json() or {}).get("workflows", []) or []
    except Exception:
        return 0
    for w in workflows:
        name = (w.get("workflow_name") or "")
        if name.startswith(ARTIFACT_PREFIX):
            try:
                wid = w.get("id")
                if wid is None:
                    continue
                dr = session.delete(
                    f"{MAIN_BASE}/delete/workflow/{int(wid)}", timeout=15
                )
                if dr.status_code == 200:
                    n += 1
            except Exception:
                pass
    return n


def _delete_journey_retailers(session: requests.Session) -> int:
    """Delete any compliance retailers whose name starts with ARTIFACT_PREFIX."""
    n = 0
    try:
        r = session.get(f"{MAIN_BASE}/api/compliance/retailers", timeout=10)
        if r.status_code != 200:
            return 0
        retailers = (r.json() or {}).get("retailers", []) or []
    except Exception:
        return 0
    for rec in retailers:
        name = (rec.get("name") or "")
        if not name.startswith(ARTIFACT_PREFIX):
            continue
        rid = rec.get("id") or rec.get("retailer_id")
        if rid is None:
            continue
        try:
            # Delete sets first (FK constraint protection)
            try:
                sr = session.get(
                    f"{MAIN_BASE}/api/compliance/retailers/{int(rid)}/sets",
                    timeout=10,
                )
                if sr.status_code == 200:
                    for s_ in (sr.json() or {}).get("sets", []) or []:
                        sid = s_.get("id") or s_.get("set_id")
                        if sid is not None:
                            session.delete(
                                f"{MAIN_BASE}/api/compliance/sets/{int(sid)}",
                                timeout=10,
                            )
            except Exception:
                pass
            dr = session.delete(
                f"{MAIN_BASE}/api/compliance/retailers/{int(rid)}", timeout=15
            )
            if dr.status_code == 200:
                n += 1
        except Exception:
            pass
    return n


@pytest.fixture(scope="module")
def cleanup_artifacts(auth_state):
    """Module-scoped cleanup: nukes anything prefixed JOURNEY_TEST_ at teardown.

    Yields a dict the tests can populate, but the real safety net is the
    prefix scan — tests that crash before reporting still get cleaned up.
    """
    created = {"agents": [], "workflows": [], "retailers": []}
    yield created
    # Final sweep — always runs.
    try:
        s = _session_from_auth_state(auth_state)
        a = _delete_journey_agents(s)
        w = _delete_journey_workflows(s)
        r = _delete_journey_retailers(s)
        if a or w or r:
            print(f"\n[journeys cleanup] deleted agents={a} workflows={w} retailers={r}")
        s.close()
    except Exception as e:
        print(f"[journeys cleanup] best-effort sweep failed: {e}")


# ---------------------------------------------------------------------------
# Convenience: timing helper to make slow waits less mystery-meat
# ---------------------------------------------------------------------------
def wait_until(predicate, timeout: float = 30.0, interval: float = 0.5,
               desc: str = "condition") -> bool:
    """Poll predicate() until True or timeout. Returns whether it became True."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval)
    print(f"[wait_until] timed out after {timeout}s waiting for: {desc}")
    return False
