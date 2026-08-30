"""
Pack 20 UI-3 — the Schedules rail view (2026-08-30, james).

Loads the REAL page from the running agent service (:5111) in headless
Chromium with every /api/* call STUBBED (no real token anywhere), opens the
Schedules view, and proves:
  * the page script loads without a page-level JS error,
  * schedule rows render with type/paused/mine chips + cadence + next run,
  * "Active only" hides paused jobs by default and unchecking reveals them,
  * the filter chips carry the right counts and filter the list,
  * clicking a row opens the detail pane: facts, Run now / Pause / Delete
    actions, and the run-history rows with status + result message,
  * Run now surfaces the queued note (ok-note),
  * the ＋ New schedule form posts the right body for a one-shot agent task
    (kind, name, prompt, run_in_minutes, browser_timezone) and shows the note.

Needs Playwright + Chromium (on the dev box: conda env aihub2.1). Run:
  C:/Users/james/miniconda3/envs/aihub2.1/python.exe test_human/20_The_Agent/ui_smoke_schedules.py
Exit 0 = all PASS; the last line is "N/N PASS".
"""
import json
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.getenv("AGENT_UI_BASE") or (
    f"http://127.0.0.1:{os.getenv('AGENT_SERVICE_PORT') or int(os.getenv('HOST_PORT', '5001')) + 110}")

SCHEDULES = {"schedules": [
    {"id": 7, "name": "Agent: morning brief", "type": "agent_session",
     "description": "summarize overnight orders", "gist": "summarize overnight orders",
     "is_active": True, "has_active_schedule": True, "created_by": "admin",
     "created_at": "2026-08-30T12:00:00", "owner_user_id": 13, "mine": True,
     "timezone": "America/New_York", "cadence": "cron 0 8 * * 1-5 · America/New_York",
     "next_run_time": "2026-08-31T12:00:00", "last_run_time": "2026-08-30T12:00:05",
     "bound": None, "schedule_count": 1},
    {"id": 8, "name": "Automation: expense triage", "type": "automation",
     "description": "", "gist": "automation abc-123", "is_active": True,
     "has_active_schedule": True, "created_by": "admin",
     "created_at": "2026-08-30T12:00:00", "owner_user_id": 13, "mine": True,
     "timezone": "", "cadence": "every 2 hours", "next_run_time": "2026-08-31T02:00:00",
     "last_run_time": None, "bound": None, "schedule_count": 1},
    {"id": 9, "name": "Old paused job", "type": "workflow", "description": "",
     "gist": "", "is_active": False, "has_active_schedule": False,
     "created_by": "system", "created_at": "2026-08-30T12:00:00",
     "owner_user_id": None, "mine": False, "timezone": "",
     "cadence": "cron 0 6 * * 1", "next_run_time": None,
     "last_run_time": "2026-08-24T06:00:00", "bound": None, "schedule_count": 1},
], "errors": [], "can_see_all": True}

HISTORY = {"job": SCHEDULES["schedules"][0], "history": [
    {"id": 501, "status": "completed", "start_time": "2026-08-30T12:00:05",
     "end_time": "2026-08-30T12:00:47",
     "result_message": "Agent session outcome=success session=abc work_item=12",
     "error_details": ""},
    {"id": 500, "status": "failed", "start_time": "2026-08-29T12:00:05",
     "end_time": "2026-08-29T12:00:12", "result_message": "boom", "error_details": ""},
]}

PLAYBOOKS = {"playbooks": [
    {"kind": "automation", "id": "abc-123", "name": "Expense Triage",
     "description": "", "version": 3, "pinned": 2},
    {"kind": "automation", "id": "def-456", "name": "Unpromoted Thing",
     "description": "", "version": 1, "pinned": None},
    {"kind": "portal_workflow", "id": "vendor-dl", "name": "Vendor Download",
     "description": "goal"},
], "errors": []}

results = []
created_bodies = []


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
        ctx.add_init_script("try{localStorage.setItem('agent_token','ui-smoke-not-a-token')}catch(e){}")
        j = lambda body, status=200: (lambda r: r.fulfill(
            status=status, content_type="application/json", body=json.dumps(body)))
        ctx.route("**/api/**", j({}))                       # catch-all first (last registered wins)
        ctx.route("**/api/work/list", j({"items": []}))
        ctx.route("**/api/me", j({"user": {"username": "ui-smoke", "name": "UI Smoke", "role": 3},
                                  "main_port": 5001, "app_version": "smoke"}))
        ctx.route("**/api/playbooks", j(PLAYBOOKS))

        def schedules_root(route):
            req = route.request
            if req.method == "POST":
                created_bodies.append(json.loads(req.post_data or "{}"))
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"ok": True, "job_id": 99,
                                               "note": "Scheduled agent task 'probe' (job #99)."}))
            else:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(SCHEDULES))
        ctx.route("**/api/schedules", schedules_root)
        ctx.route("**/api/schedules/7/history*", j(HISTORY))
        ctx.route("**/api/schedules/7/run", j({"ok": True, "note": "Queued — the engine fires it within about a minute."}))
        ctx.route("**/api/schedules/7/active", j({"ok": True, "is_active": False}))

        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.click("#nav-schedules")
        page.wait_for_selector("#sch-list .qitem", timeout=20000)
        check("page script loads without a JS error", not errors, "; ".join(errors)[:300])

        rows = page.evaluate("""() => Array.from(document.querySelectorAll('#sch-list .qitem')).map(b => ({
            chips: Array.from(b.querySelectorAll('.tchip')).map(c => c.textContent),
            title: b.querySelector('.qtitle')?.textContent || '',
            sub: b.querySelector('.qfrom')?.textContent || '',
            meta: b.querySelector('.qmeta')?.textContent || '' }))""")
        check("active-only default hides the paused job",
              len(rows) == 2 and all(r["title"] != "Old paused job" for r in rows),
              json.dumps(rows)[:300])
        brief = next((r for r in rows if r["title"] == "Agent: morning brief"), {})
        check("agent task row: type+mine chips, cadence, next run",
              brief.get("chips") == ["agent task", "mine"]
              and "cron 0 8 * * 1-5" in brief.get("sub", "")
              and brief.get("meta", "").startswith("next "), json.dumps(brief))

        page.uncheck("#sch-activeonly")
        page.wait_for_function(
            "document.querySelectorAll('#sch-list .qitem').length === 3")
        paused = page.evaluate("""() => Array.from(document.querySelectorAll('#sch-list .qitem'))
            .map(b => b.querySelector('.qtitle')?.textContent).includes('Old paused job')""")
        check("unchecking Active only reveals the paused job", paused)
        pchips = page.evaluate("""() => {
            const row = Array.from(document.querySelectorAll('#sch-list .qitem'))
              .find(b => b.querySelector('.qtitle')?.textContent === 'Old paused job');
            return Array.from(row.querySelectorAll('.tchip')).map(c => c.textContent); }""")
        check("paused row carries a 'paused' chip", "paused" in pchips, json.dumps(pchips))

        chips = page.evaluate("""() => Array.from(document.querySelectorAll('#sch-filters .chip')).map(c => ({
            label: c.childNodes[0]?.textContent?.trim() || '',
            n: c.querySelector('.n')?.textContent || '' }))""")
        want = {"All": "3", "Agent tasks": "1", "Automations": "1", "Workflows": "1"}
        got = {c["label"]: c["n"] for c in chips}
        check("filter chips carry the right counts",
              all(got.get(k) == v for k, v in want.items()), json.dumps(got))
        page.click("#sch-filters .chip:has-text('Agent tasks')")
        only = page.evaluate("""() => Array.from(document.querySelectorAll('#sch-list .qitem .qtitle'))
                                        .map(n => n.textContent)""")
        check("filtering shows exactly the agent task",
              only == ["Agent: morning brief"], json.dumps(only))

        page.click("#sch-list .qitem")
        page.wait_for_selector("#sch-detail .actions", timeout=10000)
        detail = page.evaluate("""() => ({
            crumb: document.querySelector('#sch-detail .crumb')?.textContent || '',
            title: document.querySelector('#sch-detail .dtitle')?.textContent || '',
            facts: document.querySelector('#sch-detail .kv')?.textContent || '',
            buttons: Array.from(document.querySelectorAll('#sch-detail .actions .btn')).map(b => b.textContent),
            hist: Array.from(document.querySelectorAll('#sch-detail .dsection > div:not(.dlabel)')).length })""")
        check("detail pane: crumb + title + facts",
              "job #7" in detail["crumb"] and detail["title"] == "Agent: morning brief"
              and "Cadence" in detail["facts"] and "America/New_York" in detail["facts"],
              json.dumps(detail)[:300])
        check("detail actions: Run now / Pause / Delete",
              detail["buttons"] == ["▶ Run now", "⏸ Pause", "✕ Delete"],
              json.dumps(detail["buttons"]))
        hist = page.evaluate("""() => Array.from(
            document.querySelectorAll('#sch-detail .dsection > div'))
            .map(d => d.textContent).filter(t => t.includes('outcome=') || t === 'boom' || t.includes('boom'))""")
        check("run history rows render with the result message",
              any("outcome=success" in h for h in hist) and any("boom" in h for h in hist),
              json.dumps(hist)[:300])

        page.click("#sch-detail .actions .btn.primary")  # Run now
        page.wait_for_selector("#sch-detail .actions .ok-note", timeout=10000)
        note = page.evaluate("() => document.querySelector('#sch-detail .actions .ok-note')?.textContent || ''")
        check("Run now surfaces the queued note", "Queued" in note, note)

        # ---- create form: one-shot agent task
        page.click("#sch-new")
        page.wait_for_selector("#schc-save", timeout=5000)
        page.fill("#schc-name", "probe")
        page.fill("#schc-prompt", "say DONE")
        page.select_option("#schc-mode", "once_in")
        page.fill("#schc-in", "5")
        page.click("#schc-save")
        page.wait_for_function(
            "document.getElementById('schc-note').textContent.includes('job #99')",
            timeout=10000)
        body = created_bodies[-1] if created_bodies else {}
        check("create posts the right body",
              body.get("kind") == "agent_task" and body.get("name") == "probe"
              and body.get("prompt") == "say DONE" and body.get("run_in_minutes") == 5
              and "browser_timezone" in body, json.dumps(body))

        check("still no page-level JS error", not errors, "; ".join(errors)[:300])
        b.close()

    fails = [r for r in results if not r[1]]
    print(f"{len(results) - len(fails)}/{len(results)} PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
