#!/usr/bin/env python3
"""
Headless-browser check of the MCP Servers admin page OAuth block (WI-4 / WI-5)
against a RUNNING main app — the front-end half the HTTP checks cannot see.

    C:\\Users\\james\\miniconda3\\envs\\aihub2.1\\python.exe tests_v2/live/mcp_servers_ui_check.py
        [--base http://localhost:5001] [--admin-user admin] [--admin-pass admin] [--server-id 30]

Logs in over HTTP (same recipe as pack 15), hands the session cookie to
Playwright, opens /mcp_servers, opens the Edit modal for the server and reads:
the redirect-URI hint (value + source line), the client-secret status line,
the "Available to users" switch (+ migration note), the override field, the
list badge, and the result of the "Test broker" button. Read-only: it never
clicks Save. Fails on any page/console error.
"""
import argparse
import os
import re
import sys
from urllib.parse import urlsplit

import requests
from playwright.sync_api import sync_playwright

try:  # Windows consoles default to cp1252; the report uses arrows
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

rows = []


def record(name, status, evidence=""):
    rows.append((name, status, evidence))
    print(f"[{status}] {name}" + (f" — {evidence}" if evidence else ""), flush=True)


def hidden_fields(html):
    out = dict(re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', html))
    out.update(dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"', html)))
    return out


def login(base, username, password):
    sess = requests.Session()
    r = sess.get(f"{base}/login", timeout=20)
    data = {"username": username, "password": password, "submit": "Login"}
    data.update(hidden_fields(r.text))
    r = sess.post(f"{base}/login", data=data, allow_redirects=False, timeout=30)
    ok = r.status_code in (301, 302, 303) and "/login" not in (r.headers.get("Location") or "")
    return sess, ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("MCQ_BASE", "http://localhost:5001"))
    ap.add_argument("--admin-user", default="admin")
    ap.add_argument("--admin-pass", default="admin")
    ap.add_argument("--server-id", type=int, default=30)
    args = ap.parse_args()
    base = args.base.rstrip("/")
    sid = args.server_id
    host = urlsplit(base).hostname

    sess, ok = login(base, args.admin_user, args.admin_pass)
    record("admin login", "PASS" if ok else "FAIL")
    if not ok:
        return finish()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
        ctx.add_cookies([{"name": c.name, "value": c.value, "domain": host, "path": "/"} for c in sess.cookies])
        page = ctx.new_page()
        problems = []
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        page.on("console", lambda m: problems.append(f"console.error: {m.text}") if m.type == "error" else None)

        page.goto(f"{base}/mcp_servers", wait_until="domcontentloaded")
        page.wait_for_selector("#serversTableBody tr", timeout=30000)
        row = page.query_selector(f'#serversTableBody tr:has(button[onclick="editServer({sid})"])')
        row_html = row.inner_html() if row else ""
        record("list: server row present", "PASS" if row else "FAIL")
        record("list: 'Users' badge reflects effective visibility (column absent → visible)",
               "PASS" if "Published on My Connections" in row_html else "FAIL")

        page.click(f'button[onclick="editServer({sid})"]')
        page.wait_for_selector("#serverModal.show", timeout=15000)
        page.wait_for_function(
            "() => (document.getElementById('oauthRedirectUri').textContent || '').includes('/api/mcp/oauth/callback')",
            timeout=15000)
        uri = page.text_content("#oauthRedirectUri").strip()
        source = page.text_content("#oauthRedirectSource").strip()
        record("modal: redirect URI hint shows the broker URI",
               "PASS" if uri.endswith("/api/mcp/oauth/callback") and uri.startswith("https://") else "FAIL", uri)
        record("modal: source line names the broker + return address + tenant",
               "PASS" if "broker" in source and "sends users back to" in source and "tenant" in source else "FAIL", source)
        record("modal: 'Web' platform instruction visible",
               "PASS" if "Web" in page.text_content("#oauthRedirectHint") else "FAIL")

        secret_status = page.text_content("#oauthSecretStatus").strip()
        record("modal: client-secret status line rendered", "PASS" if secret_status else "FAIL", secret_status)

        vis_group = page.eval_on_selector("#oauthAvailableGroup", "el => getComputedStyle(el).display")
        checked = page.is_checked("#oauthAvailableToUsers")
        note = page.eval_on_selector("#oauthAvailableMigrationNote", "el => getComputedStyle(el).display")
        record("modal: 'Available to users' switch shown for authorization_code",
               "PASS" if vis_group != "none" else "FAIL", f"display={vis_group} checked={checked}")
        record("modal: migration-020 note shown while the column is absent",
               "PASS" if note != "none" else "FAIL", f"display={note}")
        override_group = page.eval_on_selector("#oauthRedirectOverrideGroup", "el => getComputedStyle(el).display")
        record("modal: redirect override field shown", "PASS" if override_group != "none" else "FAIL")

        # grant-type toggle hides the per-user controls
        page.select_option("#oauthGrantType", "client_credentials")
        hidden = page.eval_on_selector("#oauthAvailableGroup", "el => getComputedStyle(el).display")
        hint_hidden = page.eval_on_selector("#oauthRedirectHint", "el => getComputedStyle(el).display")
        record("modal: switching to client_credentials hides switch + hint",
               "PASS" if hidden == "none" and hint_hidden == "none" else "FAIL")
        page.select_option("#oauthGrantType", "authorization_code")
        page.wait_for_function(
            "() => getComputedStyle(document.getElementById('oauthAvailableGroup')).display !== 'none'", timeout=5000)

        page.click("#oauthBrokerCheckBtn")
        page.wait_for_function(
            "() => /Broker (verified|check)/.test(document.getElementById('oauthBrokerCheckResult').textContent)",
            timeout=30000)
        result = page.text_content("#oauthBrokerCheckResult").strip()
        record("modal: 'Test broker' reports a result (verified, or a named failure until the cloud is deployed)",
               "PASS" if ("Broker verified" in result or "Broker check failed" in result) else "FAIL", result[:140])

        page.click('#serverModal button[data-dismiss="modal"]', timeout=5000)
        record("no page/console errors", "PASS" if not problems else "FAIL", "; ".join(problems)[:300])
        browser.close()
    return finish()


def finish():
    failed = [n for n, s, _ in rows if s == "FAIL"]
    print(f"\n{sum(1 for _, s, _ in rows if s == 'PASS')} PASS / {len(failed)} FAIL")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
