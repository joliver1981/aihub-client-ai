"""
Pack 20 helper — a SIMULATED HUMAN for the portal take-over page (PT-13).

Drives the REAL co-browse page of the Browser Use service in headless Chromium
exactly like a person would: waits for "Needs you", types the verification
code (the page relays keystrokes to the live browser over its WebSocket),
presses Enter to submit the portal's 2FA form, then clicks "Hand back /
Resume". Prints one JSON line: {"ok": bool, "status": str, "steps": [...]}.

Usage (Playwright lives in conda env aihub2.1 on the dev box):
  python cobrowse_human.py "<cobrowse page url with ?run=&token=>" "123456"
"""
import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(url: str, code: str) -> int:
    steps = []
    ok = False
    status = ""
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page()
        page.goto(url, wait_until="domcontentloaded")
        steps.append("opened")
        # wait until the page learned the run is awaiting a person (status pill)
        deadline = time.time() + 60
        pill = ""
        while time.time() < deadline:
            pill = page.evaluate("() => document.getElementById('statuspill')?.textContent || ''")
            if "Needs you" in pill or "You have control" in pill:
                break
            time.sleep(1)
        steps.append(f"pill={pill!r}")
        if not ("Needs you" in pill or "You have control" in pill):
            print(json.dumps({"ok": False, "status": pill, "steps": steps}))
            b.close()
            return 1
        # a person would wait a moment for the live frame, then type the code
        time.sleep(2)
        page.keyboard.type(code, delay=80)
        steps.append("typed code")
        time.sleep(0.5)
        page.keyboard.press("Enter")
        steps.append("pressed Enter")
        time.sleep(3)                      # let the portal process the form
        page.click("#resume")
        steps.append("clicked Hand back")
        time.sleep(2)
        status = page.evaluate("() => document.getElementById('statuspill')?.textContent || ''")
        ok = True
        b.close()
    print(json.dumps({"ok": ok, "status": status, "steps": steps}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "123456"))
