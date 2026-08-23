"""
Pack 20 UI-1 — The Agent UI smoke: links open in a NEW tab (2026-08-23, james).

Loads the REAL page from the running agent service (:5111) in headless
Chromium with the handful of /api/* calls it needs STUBBED (no real token is
used anywhere), replays a conversation carrying a portal take-over link exactly
as the model relays it, and proves:
  * the page script loads without a page-level JS error (the 5727188
    SyntaxError class the API-only gate cannot see),
  * every rendered markdown link carries target=_blank + rel=noopener
    (take-over link, platform paths, bare https URLs),
  * in-page #anchors, mailto:, and /api/files/ handoffs are left alone,
  * clicking the take-over link opens a NEW tab and the conversation tab
    stays exactly where it was,
  * the document-level safety net retargets an anchor from ANY other render
    path, but never hash anchors or programmatic download anchors.

Needs Playwright + Chromium (on the dev box: conda env aihub2.1). Run:
  C:/Users/james/miniconda3/envs/aihub2.1/python.exe test_human/20_The_Agent/ui_smoke_links.py
Exit 0 = all PASS; the last line is "N/N PASS".
"""
import json
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.getenv("AGENT_UI_BASE") or (
    f"http://127.0.0.1:{os.getenv('AGENT_SERVICE_PORT') or int(os.getenv('HOST_PORT', '5001')) + 110}")
SID = "ui-smoke-00000000-0000-0000-0000-000000000001"
COBROWSE = "http://10.0.0.7:5001/portal-workflows/cobrowse/5f36f870dbf64010ab01bdc510a7cf12"
TURNS = {"turns": [
    {"role": "user", "text": "Go to the portal and download the master price list"},
    {"role": "agent", "tools": ["portal_fetch"], "text":
        "The portal needs a 2FA/verification step that I can't complete myself. Please:\n\n"
        "1. Open this link: **" + COBROWSE + "**\n"
        "2. Finish the verification step there\n3. Click **Hand back**\n\n"
        "Also: [Run Monitor](/runs) and https://example.com/docs and "
        "[top](#chat=abc) and [mail](mailto:x@y.z) and "
        "[⤓ price-list.xlsx (12 KB)](/api/files/abc123)\n"},
]}
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)))
    print(("PASS" if ok else "FAIL"), name, detail)


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context()
        # The UI reads its token from localStorage; any string lets boot() run
        # because /api/me is stubbed below (nothing real is ever presented).
        ctx.add_init_script("try{localStorage.setItem('agent_token','ui-smoke-not-a-token')}catch(e){}")
        j = lambda body: (lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(body)))
        html = lambda title: (lambda r: r.fulfill(status=200, content_type="text/html", body=f"<title>{title}</title>{title}"))
        ctx.route("**/api/**", j({}))                       # catch-all first (last registered wins)
        ctx.route("**/api/work/list", j({"items": []}))
        ctx.route("**/api/me", j({"user": {"username": "ui-smoke", "name": "UI Smoke", "role": 3},
                                  "main_port": 5001, "app_version": "smoke"}))
        ctx.route("**/api/chat/history/" + SID, j(TURNS))
        ctx.route("**/portal-workflows/cobrowse/**", html("TAKEOVER"))
        ctx.route("**/runs", html("RUNS"))
        ctx.route("https://example.com/**", html("EX"))
        ctx.route(BASE + "/raw-page", html("RAW"))

        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE + "/#chat=" + SID, wait_until="domcontentloaded")
        page.wait_for_selector('a[href*="cobrowse"]', timeout=20000)
        check("page script loads without a JS error", not errors, "; ".join(errors)[:300])

        attrs = page.evaluate("""() => Array.from(document.querySelectorAll('#thread a[href]')).map(a => ({
            href: a.getAttribute('href'), target: a.getAttribute('target') || '',
            rel: a.getAttribute('rel') || '', cls: a.className }))""")
        by = {a["href"]: a for a in attrs}
        g = lambda h, k: by.get(h, {}).get(k, "")
        check("take-over link target=_blank", g(COBROWSE, "target") == "_blank", json.dumps(by.get(COBROWSE)))
        check("take-over link rel noopener", "noopener" in g(COBROWSE, "rel"))
        check("platform path link (/runs) _blank", g("/runs", "target") == "_blank")
        check("bare https URL _blank", g("https://example.com/docs", "target") == "_blank")
        check("#hash link stays in-page", g("#chat=abc", "target") == "")
        check("mailto link left alone", g("mailto:x@y.z", "target") == "")
        check("/api/files handoff untouched (filelink, no target)",
              g("/api/files/abc123", "target") == "" and "filelink" in g("/api/files/abc123", "cls"))

        before = page.url
        with ctx.expect_page(timeout=10000) as np_info:
            page.click('a[href*="cobrowse"]')
        newp = np_info.value
        newp.wait_for_load_state()
        check("clicking the take-over link opens a NEW tab", newp.url == COBROWSE, newp.url)
        check("conversation tab did not navigate", page.url == before, page.url)
        check("conversation DOM intact after the click",
              page.evaluate("() => !!document.querySelector('a[href*=\"cobrowse\"]')"))
        newp.close()

        # Safety net: an anchor from ANY other render path (no target of its own)
        page.evaluate("""() => { const a = document.createElement('a'); a.id='rawlink';
                                 a.href='/raw-page'; a.textContent='raw'; document.body.appendChild(a); }""")
        with ctx.expect_page(timeout=10000) as np2:
            page.click("#rawlink")
        p2 = np2.value
        p2.wait_for_load_state()
        check("safety net: untargeted anchor opens a NEW tab", p2.url == BASE + "/raw-page", p2.url)
        check("safety net: conversation tab unchanged", page.url == before, page.url)
        p2.close()
        t = page.evaluate("""() => { const a = document.createElement('a'); a.href='#chat=zzz';
                                     a.textContent='h'; document.body.appendChild(a); a.click();
                                     return a.getAttribute('target') || ''; }""")
        check("safety net: hash anchor not retargeted", t == "", t)
        t2 = page.evaluate("""() => { const a = document.createElement('a'); a.href='blob:x';
                                      a.download='f.csv'; document.body.appendChild(a);
                                      try { a.click(); } catch (e) {} return a.getAttribute('target') || ''; }""")
        check("safety net: download anchor not retargeted", t2 == "", t2)
        check("still no page-level JS error", not errors, "; ".join(errors)[:300])
        b.close()

    fails = [r for r in results if not r[1]]
    print(f"{len(results) - len(fails)}/{len(results)} PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
