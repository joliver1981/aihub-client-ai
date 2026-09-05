"""Ad-hoc PT-13 (hand-back -> conversation bridge) PLUS the new take-over
BUTTON block, run LIVE against the dev Agent service (:5111).

Same journey as pack 20 PT-13 (runner.py), with the Vantage 2FA portal on
:3001 because :3000 is held by another test portal on this box today.
Reuses the runner's helpers (mint_token / chat_turn / result_of).
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = r"C:\src\aihub-client-ai-dev\test_human\20_The_Agent"
sys.path.insert(0, HERE)
os.chdir(HERE)
import runner  # noqa: E402  (module level = env + helpers only; main() is guarded)
import requests  # noqa: E402
import shared_auth  # noqa: E402
from runner import BASE, mint_token, chat_turn, result_of, tools_used  # noqa: E402

PORTAL = os.getenv("VANTAGE_URL", "http://localhost:3001")
UI_PY = r"C:\Users\james\miniconda3\envs\aihub2.1\python.exe"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)))
    print(("PASS" if ok else "FAIL"), name, detail, flush=True)


def main():
    token = mint_token()
    hdr = {"Authorization": f"Bearer {token}"}
    try:
        up = requests.get(f"{PORTAL}/login-2fa", timeout=5).status_code == 200
    except Exception:
        up = False
    check("precondition: Vantage 2FA portal up", up, PORTAL)
    if not up:
        return 1

    t0 = time.time()
    ev, txt = chat_turn(
        token,
        f"Go to the portal at {PORTAL}/login-2fa, log in with username pack20 and "
        "password pack20, then open the Download Center and download the master "
        "price list (price-list.xlsx). Stop right after that one download.",
        timeout=900)
    sid = result_of(ev).get("session_id")
    m = re.search(r"cobrowse/([0-9a-f]{32})", txt)
    run_id = m.group(1) if m else ""
    print(f"turn {time.time() - t0:.0f}s tools={tools_used(ev)} sid={sid} run={run_id}", flush=True)
    print("---- agent text ----\n" + txt[:3000] + "\n----", flush=True)
    check("PAUSED + take-over link relayed", bool(run_id) and "cobrowse/" in txt, run_id)

    # ---- the new button block
    fences = re.findall(r"```aihub-action\s*\n(\{.*?\})\s*\n```", txt, re.S)
    check("model pasted the aihub-action reference fence once", len(fences) == 1, f"fences={len(fences)}")
    if fences:
        try:
            ref = json.loads(fences[0]).get("ref", "")
        except Exception:
            ref = ""
        check("fence is a reference (no inline URL)", bool(ref) and '"url"' not in fences[0], fences[0][:120])
        r = requests.get(f"{BASE}/api/blocks/{ref}", headers=hdr, timeout=30)
        spec = r.json().get("spec") if r.status_code == 200 else None
        check("GET /api/blocks/<ref> resolves to the open_url action for THIS run",
              r.status_code == 200 and bool(spec) and spec.get("action") == "open_url"
              and str(spec.get("url", "")).endswith(f"/portal-workflows/cobrowse/{run_id}")
              and spec.get("label") == "Take over the browser", f"{r.status_code} {spec}")

    # ---- PT-13 proper (unchanged journey)
    def watch():
        w = requests.get(f"{BASE}/api/portal/watches", headers=hdr, timeout=60).json()
        return next((x for x in w.get("watches", []) if x["run_id"] == run_id), {})

    w0 = watch() if run_id else {}
    armed = (w0.get("status") == "active" and w0.get("phase") == "paused"
             and w0.get("session_id") == sid)
    check("watch armed on this conversation (paused)", armed, json.dumps(w0)[:200])
    human = {}
    if armed:
        from CommonUtils import get_browser_use_api_base_url as bu
        cb_tok = shared_auth.sign_cobrowse_token(run_id, 1, 3)
        cb_url = f"{bu()}/cobrowse?run={run_id}&token={cb_tok}"
        pr = subprocess.run([UI_PY, os.path.join(HERE, "cobrowse_human.py"), cb_url, "123456"],
                            capture_output=True, text=True, timeout=240,
                            encoding="utf-8", errors="replace")
        try:
            human = json.loads((pr.stdout or "").strip().splitlines()[-1])
        except Exception:
            human = {"ok": False, "raw": (pr.stdout or pr.stderr or "")[-300:]}
    check("simulated human typed the code and handed back", human.get("ok") is True,
          json.dumps(human)[:300])

    final, deadline = {}, time.time() + 480
    while time.time() < deadline:
        final = watch()
        busy = (requests.get(f"{BASE}/api/chat/version", params={"session_id": sid},
                             headers=hdr, timeout=60).json() if sid else {})
        if final.get("status") in ("done", "gone", "expired", "disarmed") and not busy.get("inflight"):
            break
        time.sleep(4)
    ver = (requests.get(f"{BASE}/api/chat/version", params={"session_id": sid},
                        headers=hdr, timeout=60).json() if sid else {})
    turns = (requests.get(f"{BASE}/api/chat/history/{sid}", headers=hdr,
                          timeout=60).json().get("turns", []) if sid else [])
    upd = next((i for i, t in enumerate(turns) if t.get("kind") == "portal_update"), None)
    after = (" ".join(t.get("text", "") for t in turns[(upd or 0) + 1:] if t.get("role") == "agent")
             if upd is not None else "")
    lm = re.search(r"/api/files/([a-f0-9-]+)", after)
    link = lm.group(0) if lm else ""
    dl = requests.get(f"{BASE}{link}", headers=hdr, timeout=90) if link else None
    items = requests.get(f"{BASE}/api/work/list", headers=hdr, timeout=60).json().get("items", [])
    fyi = [i for i in items if (i.get("payload") or {}).get("kind") == "portal_run_update"
           and i["payload"].get("run_id") == run_id]
    check("hand-back bridge: watch done + handback_at + [PORTAL RUN UPDATE] turn + "
          "/api/files link served + version bump + My Work FYI",
          final.get("status") == "done" and bool(final.get("handback_at")) and upd is not None
          and bool(link) and dl is not None and dl.status_code == 200 and len(dl.content) > 0
          and int(ver.get("version") or 0) >= 1 and not ver.get("inflight")
          and len(fyi) >= 1 and fyi[0]["payload"].get("chat_session_id") == sid,
          f"watch={final.get('status')}/{final.get('phase')} handback={bool(final.get('handback_at'))} "
          f"outcome={final.get('outcome')!r} update_turn={upd} link={bool(link)} "
          f"bytes={len(getattr(dl, 'content', b''))} version={ver.get('version')} fyi={len(fyi)}")
    hist_txt = " ".join(t.get("text", "") for t in turns if t.get("role") == "agent")
    check("history replay carries the fence (button re-renders on reload)", "```aihub-action" in hist_txt)
    for it in fyi:                       # tidy: acknowledge the probe's FYI
        try:
            requests.post(f"{BASE}/api/work/respond", headers=hdr, timeout=30,
                          json={"id": it["id"], "response": {"decision": "acknowledged"}})
        except Exception:
            pass
    fails = [r for r in results if not r[1]]
    print(f"{len(results) - len(fails)}/{len(results)} PASS", flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
