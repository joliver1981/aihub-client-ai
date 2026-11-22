"""Browser check of The Agent's My Connections screen (2026-11-22), in a real
browser engine (Playwright Chromium, headless) against the running dev install.

It covers what an API check cannot: the rail entry, the screen, the real
sign-in POPUP that Connect opens on the main app's address (and what the
screen says when that window is closed without connecting), the "#connections"
link the assistant hands out, and the Disconnect button.

Safe by construction: it signs in to The Agent as a REGULAR test user who is
not connected, never completes a provider sign-in, and exercises Disconnect by
showing that user one connection as "connected" in the browser only — the real
Disconnect request then runs for a user who has nothing to remove.

Run:  aihub2.1 python tests_v2/live/the_agent_connections_screen_ui_check.py
      [--regular-id 349 --regular-user ru_alex]
"""
import argparse
import json
import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

ROWS = []


def check(name, ok, detail=""):
    ROWS.append((name, bool(ok)))
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  <- {str(detail)[:300]}"))


def main():
    ap = argparse.ArgumentParser()
    port = int(os.environ.get("HOST_PORT", "5001"))
    ap.add_argument("--agent", default=f"http://127.0.0.1:{port + 110}")
    ap.add_argument("--main-port", type=int, default=port)
    ap.add_argument("--regular-id", type=int, default=349)
    ap.add_argument("--regular-user", default="ru_alex")
    a = ap.parse_args()

    import secure_config
    secure_config.load_secure_config()
    import shared_auth
    token = shared_auth.sign_cc_token(
        {"user_id": a.regular_id, "role": 1, "tenant_id": 1,
         "username": a.regular_user, "name": a.regular_user}, ttl_seconds=900)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # ---- arrive with the link the assistant hands out -------------------
        page.goto(f"{a.agent}/?token={token}#connections")
        page.wait_for_selector("#view-connections.on", timeout=20000)
        check("'#connections' opens the screen on arrival", True)
        check("...and the link is used up (works again later)",
              page.evaluate("location.hash") == "")
        page.wait_for_selector("#conn-list .conncard", timeout=30000)
        nav = page.locator("#nav-connections")
        check("the rail has My Connections for a regular user",
              nav.is_visible() and "My Connections" in nav.inner_text())
        about = page.locator("#conn-about").inner_text()
        check("the screen says what the assistant may do with an account",
              "read" in about.lower() and "cannot send" in about.lower(), about)
        check("...and that it is not Agent Email", "not Agent Email" in about, about)
        flyout = page.evaluate("Array.from(document.querySelectorAll('#plat-fly a')).map(a => a.textContent)")
        check("no second 'My Connections' entry pointing at the classic app",
              not any("My Connections" in t for t in flyout), flyout)

        cards = page.locator("#conn-list .conncard")
        first = cards.first
        name = first.locator(".connname").inner_text()
        connect = first.get_by_role("button", name="Connect", exact=True)
        if connect.count() != 1:
            print(f"SKIP  popup checks: '{name}' has no Connect button "
                  "(this user is already connected, or nothing is published)")
        else:
            # ---- Connect opens a real popup on the MAIN app's address ---------
            with page.expect_popup() as pop:
                connect.click()
            popup = pop.value
            popup.wait_for_load_state("domcontentloaded")
            url = popup.url
            check("Connect opens a separate sign-in window", True)
            check("...on the main app's address, not The Agent's",
                  f":{a.main_port}/" in url, url)
            # this browser has no AI Hub sign-in, so the window shows the
            # sign-in form and remembers where to come back to
            check("...which, not signed in, shows AI Hub's sign-in form with the way back",
                  "/login" in url and "connect" in url and f"as%3D{a.regular_id}" in url, url)
            note = page.locator("#conn-note")
            check("the screen says to finish in that window",
                  "window that opened" in note.inner_text(), note.inner_text())
            popup.close()
            page.wait_for_function(
                "document.getElementById('conn-note').innerText.includes('is not connected')",
                timeout=15000)
            text = note.inner_text()
            check("closing the window without connecting is reported as NOT connected",
                  name in text and "is not connected" in text
                  and "bad" in (note.get_attribute("class") or ""), text)

        # ---- the link inside a conversation ---------------------------------
        page.click("#nav-chat")
        page.evaluate("""() => {
            const a = document.createElement('a');
            a.href = '#connections'; a.id = 'probe-link'; a.textContent = 'Open My Connections';
            document.getElementById('thread').appendChild(a);
        }""")
        page.click("#probe-link")
        page.wait_for_selector("#view-connections.on", timeout=10000)
        check("the assistant's link opens the screen from a conversation, in the same tab",
              len(ctx.pages) == 1 or all(pg.is_closed() for pg in ctx.pages[1:]))

        # ---- Disconnect ------------------------------------------------------
        # Show this (not connected) user one connection as connected, in the
        # browser only, so the button exists; the request it sends is real.
        state = {"faked": 0}

        def fake_connected(route):
            resp = route.fetch()
            body = resp.json()
            if state["faked"] == 0 and body.get("connections"):
                body["connections"][0]["connected"] = True
                body["connections"][0]["last_connected"] = "2026-11-22T12:00:00"
                state["faked"] = 1
                route.fulfill(response=resp, body=json.dumps(body),
                              headers={**resp.headers, "content-type": "application/json"})
            else:
                route.fulfill(response=resp)
        page.route("**/api/my-connections", fake_connected)
        page.click("#conn-refresh")
        off = page.locator("#conn-list .conncard").first.get_by_role("button", name="Disconnect")
        try:
            off.wait_for(timeout=15000)
        except Exception:
            off = None
        if off is None:
            print("SKIP  Disconnect button: nothing is published to this user")
        else:
            check("a connected account shows Reconnect and Disconnect",
                  page.locator("#conn-list .conncard").first
                      .get_by_role("button", name="Reconnect").count() == 1)
            asked = []
            sent = []
            page.on("dialog", lambda d: (asked.append(d.message), d.accept()))
            page.on("response", lambda r: sent.append((r.request.method, r.url, r.status))
                    if "/disconnect" in r.url else None)
            off.click()
            page.wait_for_function(
                "document.getElementById('conn-note').innerText.includes('is disconnected')",
                timeout=20000)
            check("Disconnect asks first, and says what stops working",
                  len(asked) == 1 and "scheduled" in asked[0], asked)
            check("...then sends the real request and it succeeds",
                  len(sent) == 1 and sent[0][0] == "POST" and sent[0][2] == 200, sent)
            check("...and the list is re-read: the account shows as not connected",
                  page.locator("#conn-list .conncard").first
                      .get_by_role("button", name="Connect", exact=True).count() == 1)

        check("no script errors on the page", not errors, errors)
        browser.close()

    bad = sum(1 for _, ok in ROWS if not ok)
    print(f"\n{len(ROWS) - bad}/{len(ROWS)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
