"""
Pack 20 UI-2 — Playbooks rail surfaces portal workflows (2026-08-23, james).

Loads the REAL page from the running agent service (:5111) in headless
Chromium with the /api/* calls it needs STUBBED (no real token anywhere),
stubs /api/playbooks with one row of every kind INCLUDING a portal_workflow,
opens the Playbooks view, and proves:
  * the page script loads without a page-level JS error,
  * the portal workflow renders with its own "portal workflow" chip
    (t-edit_and_return — NOT the regular workflow chip: separate subsystem),
  * the "Portal workflows" filter chip exists with the right count and
    filtering shows exactly the portal workflow row,
  * clicking the row opens the Portal Workflows page deep link
    (:<main_port>/portal-workflows?load=<slug>) in a NEW tab and the
    Playbooks tab stays exactly where it was,
  * regular workflows still deep-link into the designer (unchanged).

Needs Playwright + Chromium (on the dev box: conda env aihub2.1). Run:
  C:/Users/james/miniconda3/envs/aihub2.1/python.exe test_human/20_The_Agent/ui_smoke_playbooks.py
Exit 0 = all PASS; the last line is "N/N PASS".
"""
import json
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.getenv("AGENT_UI_BASE") or (
    f"http://127.0.0.1:{os.getenv('AGENT_SERVICE_PORT') or int(os.getenv('HOST_PORT', '5001')) + 110}")
SLUG = "vendor-invoice-download-2fa"
PLAYBOOKS = {"playbooks": [
    {"kind": "workflow", "id": 41, "name": "Monthly Rollup", "description": "sum the invoices"},
    {"kind": "code_flow", "id": 42, "name": "CSV Cruncher", "description": ""},
    {"kind": "automation", "id": 7, "name": "Expense Triage", "description": "", "version": 3},
    {"kind": "portal_workflow", "id": SLUG, "name": "Vendor Invoice Download - 2FA",
     "description": "Log in and download the latest invoice"},
], "errors": []}
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)))
    print(("PASS" if ok else "FAIL"), name, detail)


def main():
    try:  # emoji in evidence must not kill the run on a cp1252 console
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
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
        ctx.route("**/api/playbooks", j(PLAYBOOKS))
        # The deep-link targets live on the MAIN app (:5001, not running here) — stub them.
        ctx.route("**/portal-workflows*", html("PW"))
        ctx.route("**/workflow_tool*", html("WFT"))

        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.click("#nav-playbooks")
        page.wait_for_selector("#pb-list .qitem", timeout=20000)
        check("page script loads without a JS error", not errors, "; ".join(errors)[:300])

        rows = page.evaluate("""() => Array.from(document.querySelectorAll('#pb-list .qitem')).map(b => ({
            chip: b.querySelector('.tchip')?.textContent || '',
            chipCls: b.querySelector('.tchip')?.className || '',
            title: b.querySelector('.qtitle')?.textContent || '',
            meta: b.querySelector('.qmeta')?.textContent || '' }))""")
        pw = next((r for r in rows if r["title"] == "Vendor Invoice Download - 2FA"), {})
        check("portal workflow row renders in the rail", bool(pw), json.dumps(rows)[:300])
        check("row carries its OWN 'portal workflow' chip (not 'workflow')",
              pw.get("chip") == "portal workflow" and "t-edit_and_return" in pw.get("chipCls", ""),
              json.dumps(pw))
        check("row meta shows the slug id", pw.get("meta") == f"id {SLUG}", pw.get("meta"))

        chips = page.evaluate("""() => Array.from(document.querySelectorAll('#pb-filters .chip')).map(c => ({
            label: c.childNodes[0]?.textContent?.trim() || '',
            n: c.querySelector('.n')?.textContent || '' }))""")
        f = next((c for c in chips if c["label"] == "Portal workflows"), {})
        check("'Portal workflows' filter chip exists with count 1",
              f.get("n") == "1", json.dumps(chips)[:300])

        page.click("#pb-filters .chip:has-text('Portal workflows')")
        page.wait_for_selector("#pb-list .qitem")
        only = page.evaluate("""() => Array.from(document.querySelectorAll('#pb-list .qitem .qtitle'))
                                        .map(n => n.textContent)""")
        check("filter shows exactly the portal workflow",
              only == ["Vendor Invoice Download - 2FA"], json.dumps(only))

        before = page.url
        with ctx.expect_page(timeout=10000) as np_info:
            page.click("#pb-list .qitem")
        newp = np_info.value
        newp.wait_for_load_state()
        expect_url = f"http://127.0.0.1:5001/portal-workflows?load={SLUG}"
        check("click opens /portal-workflows?load=<slug> in a NEW tab",
              newp.url == expect_url, newp.url)
        check("Playbooks tab did not navigate", page.url == before, page.url)
        newp.close()

        # Regular workflows must still deep-link into the designer (unchanged).
        # (":has-text('Workflows')" would also match "Portal workflows" — pick
        # the chip by its EXACT label; a JS click is fine here, no popup opens.)
        page.evaluate("""() => Array.from(document.querySelectorAll('#pb-filters .chip'))
            .find(c => (c.childNodes[0]?.textContent || '').trim() === 'Workflows').click()""")
        page.wait_for_selector("#pb-list .qitem")
        with ctx.expect_page(timeout=10000) as np2:
            page.click("#pb-list .qitem")
        p2 = np2.value
        p2.wait_for_load_state()
        check("regular workflow still opens the designer deep link",
              p2.url == "http://127.0.0.1:5001/workflow_tool?load_workflow_id=41", p2.url)
        p2.close()
        check("still no page-level JS error", not errors, "; ".join(errors)[:300])
        b.close()

    fails = [r for r in results if not r[1]]
    print(f"{len(results) - len(fails)}/{len(results)} PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
