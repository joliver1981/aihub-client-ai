"""JOURNEY-7: Sign out from The Agent.

The Agent is its own service on its own port, so it is not the same site as
the main app: it holds the signed-in person as a token in the browser, and the
main app holds the session (and the Remember Me cookie). Sign out in The
Agent's rail has to end both.

Flow (local sign-in, Remember Me ticked, two tabs open on The Agent):
  - Sign in -> The Agent opens, the rail shows who is signed in + Sign out
  - Click Sign out -> the sign-in form, with the way back into The Agent
  - The main app session is over, Remember Me included (/dashboard -> /login)
  - The Agent's browser storage holds no token; the second tab is signed out
  - Back, a direct visit and a reload all stop on "You're signed out"
  - Sign in again from that screen -> back in The Agent as normal

Catches "the button only navigates away and the token keeps working",
"Remember Me signs the user straight back in" and "Back returns to a working
page".
"""
from __future__ import annotations

import pytest
import requests

from .conftest import MAIN_BASE, TEST_PASS, TEST_USER

SIGNED_OUT = "You're signed out"


def _sign_in(page, remember: bool = False) -> None:
    """Fill the sign-in form the page is on and submit it."""
    page.locator('input[name="username"]').first.fill(TEST_USER)
    page.locator('input[name="password"]').first.fill(TEST_PASS)
    if remember:
        page.locator('input[name="remember"]').first.check()
    page.locator('button[type="submit"], input[type="submit"]').first.click()


def _wait_for_sign_in_form(page) -> None:
    try:
        page.wait_for_selector('input[name="password"]', timeout=15000)
    except Exception:
        raise AssertionError(f"expected the sign-in form, but the page is {page.url}") from None
    assert "/login" in page.url, page.url


def _origin(url: str) -> str:
    return "/".join(url.split("/")[:3])


def _wait_for_agent(page) -> str:
    """Wait until The Agent has opened for a signed-in user; return its address."""
    page.wait_for_selector("#signout", timeout=20000)
    return _origin(page.url)


def _token(page):
    return page.evaluate("localStorage.getItem('agent_token')")


@pytest.mark.slow
@pytest.mark.journey
def test_sign_out_from_the_agent(fresh_page, services_ready):
    page = fresh_page
    ctx = page.context

    # Step 1: sign in (Remember Me on) and land in The Agent.
    page.goto(f"{MAIN_BASE}/the-agent", timeout=15000)
    if "/login" not in page.url:
        pytest.skip(f"/the-agent did not ask for sign-in (landed on {page.url})")
    _sign_in(page, remember=True)
    try:
        agent = _wait_for_agent(page)
    except Exception:
        pytest.skip(f"The Agent did not open after sign-in (landed on {page.url}); "
                    "is it enabled and running?")
    assert agent != _origin(MAIN_BASE), "The Agent should be on its own address"
    assert "signed in as" in page.locator("#who").inner_text()
    assert page.locator("#signout").is_visible()
    old_token = _token(page)
    assert old_token, "The Agent holds no token after sign-in"
    remember_cookies = [c["name"] for c in ctx.cookies() if "remember" in c["name"].lower()]
    assert remember_cookies, "Remember Me was ticked but no remember cookie was set"

    # A second tab on The Agent, signed in through the same browser storage.
    tab2 = ctx.new_page()
    tab2.goto(agent + "/", timeout=15000)
    tab2.wait_for_selector("#signout", timeout=20000)

    # Step 2: sign out -> the sign-in form, with the way back into The Agent.
    page.locator("#signout").click()
    _wait_for_sign_in_form(page)
    assert page.url.startswith(MAIN_BASE), page.url
    assert "the-agent" in page.url, f"sign-in form lost the way back: {page.url}"
    assert page.locator('input[name="password"]').first.is_visible()

    # Step 3: the main app session is over, Remember Me included.
    left = [c["name"] for c in ctx.cookies() if "remember" in c["name"].lower()]
    assert not left, f"Remember Me cookie survived sign-out: {left}"
    probe = ctx.new_page()
    probe.goto(f"{MAIN_BASE}/dashboard", timeout=15000)
    assert "/login" in probe.url, f"/dashboard still opens after sign-out: {probe.url}"
    probe.close()

    # Step 4: the other tab is signed out without anyone touching it.
    tab2.wait_for_selector(f"text={SIGNED_OUT}", timeout=15000)
    assert tab2.locator("#signout").count() == 0
    assert _token(tab2) is None
    tab2.close()

    # Step 5: Back does not return to a working page.
    page.go_back()
    page.wait_for_selector(f"text={SIGNED_OUT}", timeout=15000)
    assert _origin(page.url) == agent
    assert _token(page) is None
    assert page.locator("#signout").count() == 0
    assert page.locator("#input").is_disabled()
    assert all(page.locator(".navlink").evaluate_all("bs => bs.map(b => b.disabled)"))
    # the service refuses a request that carries no token
    assert requests.get(f"{agent}/api/me", timeout=10).status_code == 401

    # Step 6: nor does opening The Agent directly, or reloading it.
    page.goto(agent + "/", timeout=15000)
    page.wait_for_selector(f"text={SIGNED_OUT}", timeout=15000)
    page.reload()
    page.wait_for_selector(f"text={SIGNED_OUT}", timeout=15000)
    assert _token(page) is None

    # Step 7: sign in again from that screen -> back in The Agent as normal.
    # (in this same tab: links in The Agent open a new tab unless told otherwise)
    page.locator(".gate a").click()
    _wait_for_sign_in_form(page)
    assert len(ctx.pages) == 1, "Sign in opened another tab and left this one behind"
    _sign_in(page)
    assert _wait_for_agent(page) == agent
    assert "signed in as" in page.locator("#who").inner_text()
    assert not page.locator("#input").is_disabled()
    assert _token(page) and _token(page) != old_token
    assert page.evaluate("localStorage.getItem('agent_signed_out')") is None


@pytest.mark.slow
@pytest.mark.journey
def test_classic_logout_signs_the_agent_out_too(fresh_page, services_ready):
    """Logout in the classic app must end The Agent in the same browser.

    The Agent's token is in the browser storage of its own address, which the
    main app cannot reach, so the landing page Logout lands on loads The
    Agent's "signed out" page in a hidden frame.

    Flow: sign in -> The Agent open in one tab -> Logout in the classic app
    from another tab -> the landing page, as always -> the open Agent tab is
    signed out, and a direct visit to The Agent asks for sign-in.
    """
    page = fresh_page
    ctx = page.context

    page.goto(f"{MAIN_BASE}/the-agent", timeout=15000)
    if "/login" not in page.url:
        pytest.skip(f"/the-agent did not ask for sign-in (landed on {page.url})")
    _sign_in(page)
    try:
        agent = _wait_for_agent(page)
    except Exception:
        pytest.skip(f"The Agent did not open after sign-in (landed on {page.url}); "
                    "is it enabled and running?")
    assert _token(page)

    # Logout in the classic app (its Logout menu entry is a link to /logout).
    classic = ctx.new_page()
    classic.goto(f"{MAIN_BASE}/logout", timeout=15000)
    assert classic.url.rstrip("/") == MAIN_BASE.rstrip("/"), (
        f"Logout no longer lands on the landing page: {classic.url}")
    frame = classic.locator('iframe[src$="/static/signed-out.html"]')
    assert frame.count() == 1, "the landing page did not load The Agent's signed-out page"
    assert frame.get_attribute("src").startswith(agent)
    assert not frame.is_visible()

    # The Agent tab that was open is signed out without anyone touching it.
    page.wait_for_selector(f"text={SIGNED_OUT}", timeout=15000)
    assert _token(page) is None
    assert page.locator("#signout").count() == 0

    # A direct visit does not get back in either.
    page.goto(agent + "/", timeout=15000)
    page.wait_for_selector(f"text={SIGNED_OUT}", timeout=15000)
    assert _token(page) is None

    # The frame is there once: a later visit to the landing page has none.
    classic.goto(f"{MAIN_BASE}/", timeout=15000)
    assert classic.locator('iframe[src$="/static/signed-out.html"]').count() == 0
    classic.close()

    # Signing in again opens The Agent as normal.
    page.locator(".gate a").click()
    _wait_for_sign_in_form(page)
    _sign_in(page)
    assert _wait_for_agent(page) == agent
    assert _token(page)
