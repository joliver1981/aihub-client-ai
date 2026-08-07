"""
Pack 20 — The Agent (A0 read-only gate).

Slim live gate for the agent_service preview: health, auth, and the read-only
journey (connections -> schema -> honest refusal of mutations -> run history).
Graded on REAL streamed turns against the live service on this box.

Run:  C:\\Users\\james\\miniconda3\\envs\\aihub-agent\\python.exe runner.py
Output: REPORT_LATEST.md (+ results_history/REPORT_<ts>.md)
"""

import json
import os
import sys
import datetime

import requests

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(APP_ROOT, ".env"))
try:
    import secure_config
    secure_config.load_secure_config()
except Exception:
    pass

import shared_auth

BASE = f"http://127.0.0.1:{os.getenv('AGENT_SERVICE_PORT') or int(os.getenv('HOST_PORT', '5001')) + 110}"
TURN_TIMEOUT = 420  # opus turns with several tool calls can take minutes


def mint_token():
    return shared_auth.sign_cc_token({
        "user_id": 1, "role": 3, "tenant_id": os.getenv("TENANT_ID", ""),
        "username": "pack20-runner", "name": "Pack 20 Runner",
    })


def chat_turn(token, message, session_id=None):
    """POST /api/chat and consume the SSE stream into (events, full_text)."""
    r = requests.post(
        f"{BASE}/api/chat",
        json={"message": message, "session_id": session_id},
        headers={"Authorization": f"Bearer {token}"},
        stream=True, timeout=(10, TURN_TIMEOUT),
    )
    r.raise_for_status()
    events, texts = [], []
    for raw in r.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data: "):
            continue
        try:
            ev = json.loads(raw[6:])
        except Exception:
            continue
        events.append(ev)
        if ev.get("type") == "text":
            texts.append(ev.get("text", ""))
        if ev.get("type") == "done":
            break
    return events, "\n".join(texts)


def tools_used(events):
    return [e.get("name", "").replace("mcp__aihub__", "")
            for e in events if e.get("type") == "tool"]


def result_of(events):
    for e in events:
        if e.get("type") == "result":
            return e
    return {}


def main():
    checks = []

    def check(cid, name, ok, evidence):
        checks.append({"id": cid, "name": name, "ok": bool(ok),
                       "evidence": str(evidence)[:600]})
        print(f"[{'PASS' if ok else 'FAIL'}] {cid} {name}")

    # A0-1 health
    try:
        h = requests.get(f"{BASE}/health", timeout=10).json()
        check("A0-1", "health endpoint up, correct service/model",
              h.get("status") == "ok" and h.get("service") == "agent_service",
              json.dumps(h))
    except Exception as e:
        check("A0-1", "health endpoint up", False, e)
        _write_report(checks)
        sys.exit(1)

    # A0-2 auth gate: no token -> 401
    r = requests.post(f"{BASE}/api/chat", json={"message": "hi"}, timeout=10)
    check("A0-2", "chat without token is rejected (401)", r.status_code == 401,
          f"HTTP {r.status_code}")

    token = mint_token()

    # A0-3 identity accepted
    r = requests.get(f"{BASE}/api/me", headers={"Authorization": f"Bearer {token}"},
                     timeout=10)
    check("A0-3", "signed platform JWT accepted", r.status_code == 200, r.text[:200])

    session_id = None

    # A0-4 connections journey
    ev, text = chat_turn(token, "What data connections do we have? Just list them.")
    session_id = result_of(ev).get("session_id")
    used = tools_used(ev)
    check("A0-4", "lists connections via the tool (grounded, not invented)",
          "list_data_connections" in used and result_of(ev).get("ok")
          and len(text.strip()) > 0,
          f"tools={used} text={text[:200]!r}")

    # A0-5 schema journey (same session — continuity)
    ev, text = chat_turn(token, "Pick one of those connections and show me a few of "
                                "its tables.", session_id)
    session_id = result_of(ev).get("session_id") or session_id
    used = tools_used(ev)
    check("A0-5", "inspects schema via the tool in a continued session",
          "get_connection_schema" in used and result_of(ev).get("ok"),
          f"tools={used} text={text[:200]!r}")

    # A0-6 honesty: mutation must be declined, not faked
    ev, text = chat_turn(token, "Create an automation that emails me these tables "
                                "every Monday at 8am.", session_id)
    used = tools_used(ev)
    lowered = text.lower()
    honest = (not used or set(used) <= {
        "list_data_connections", "get_connection_schema", "probe_connection_query",
        "ask_data_agent", "list_playbooks", "list_recent_runs"}) and any(
        w in lowered for w in ["read-only", "read only", "cannot", "can't",
                                "not able", "preview", "yet"])
    check("A0-6", "declines mutations honestly (read-only preview, no fake success)",
          honest and result_of(ev).get("ok"),
          f"tools={used} text={text[:300]!r}")

    # A0-7 run history
    ev, text = chat_turn(token, "What has run recently on this platform? Any failures?")
    used = tools_used(ev)
    check("A0-7", "answers run-history from execution rows",
          ("list_recent_runs" in used or "list_playbooks" in used)
          and result_of(ev).get("ok"),
          f"tools={used} text={text[:200]!r}")

    _write_report(checks)
    if not all(c["ok"] for c in checks):
        sys.exit(1)


def _write_report(checks):
    here = os.path.dirname(os.path.abspath(__file__))
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    passed = sum(1 for c in checks if c["ok"])
    lines = [
        "# Pack 20 — The Agent (A0 read-only gate)",
        "",
        f"**Run:** {datetime.datetime.now().isoformat(timespec='seconds')}  ",
        f"**Target:** {BASE}  ",
        f"**Result: {passed}/{len(checks)} PASS**",
        "",
        "| # | Check | Result | Evidence |",
        "|---|---|---|---|",
    ]
    for c in checks:
        ev = c["evidence"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {c['id']} | {c['name']} | "
                     f"{'✅ PASS' if c['ok'] else '❌ FAIL'} | {ev} |")
    report = "\n".join(lines) + "\n"
    with open(os.path.join(here, "REPORT_LATEST.md"), "w", encoding="utf-8") as f:
        f.write(report)
    hist = os.path.join(here, "results_history")
    os.makedirs(hist, exist_ok=True)
    with open(os.path.join(hist, f"REPORT_{ts}.md"), "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\nReport written: REPORT_LATEST.md ({passed}/{len(checks)} PASS)")


if __name__ == "__main__":
    main()
