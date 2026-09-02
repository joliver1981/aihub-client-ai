"""Pack 20 — standing preferences + "email My View to <name>" live drive (2026-09-02).

Real streamed /api/chat turns as user 1 (admin):
  V1  "email <a saved View> to Dana Reyes" -> send_email resolves the NAME
      against the user directory, embeds the View, reports SENT.
  R1  a fresh conversation states two standing preferences -> remember_preference.
  R2  a NEW conversation (no shared transcript) answers from the injected
      preferences block with NO tool call.
  R3  "forget the temperature one" -> forget_preference; the other survives.
Cleans up the throwaway preferences at the end.

Run:  C:\\Users\\james\\miniconda3\\envs\\aihub-agent\\python.exe prefs_and_view_email_live.py [recipient name]
"""
import json
import os
import sys

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from dotenv import load_dotenv
load_dotenv(os.path.join(APP_ROOT, ".env"))
try:
    import secure_config
    secure_config.load_secure_config()
except Exception:
    pass
import requests
import shared_auth
import agent_config
import readthrough
import views_store
import preferences

BASE = f"http://127.0.0.1:{os.getenv('AGENT_SERVICE_PORT') or int(os.getenv('HOST_PORT', '5001')) + 110}"
UID = 1
TOKEN = shared_auth.sign_cc_token({"user_id": UID, "role": 3, "tenant_id": os.getenv("TENANT_ID", ""),
                                   "username": "prefs-live", "name": "Prefs Live"})
RECIPIENT = sys.argv[1] if len(sys.argv) > 1 else "Dana Reyes"
SESSION = None


def turn(msg, timeout=600, fresh=False):
    global SESSION
    if fresh:
        SESSION = None
    r = requests.post(f"{BASE}/api/chat",
                      json={"message": msg, "session_id": SESSION, "timezone": "America/New_York"},
                      headers={"Authorization": f"Bearer {TOKEN}"}, stream=True, timeout=(10, timeout))
    r.raise_for_status()
    tools, texts = [], []
    for raw in r.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data: "):
            continue
        try:
            ev = json.loads(raw[6:])
        except Exception:
            continue
        t = ev.get("type")
        if t == "tool":
            tools.append(ev.get("name", "").replace("mcp__aihub__", ""))
        elif t == "text":
            texts.append(ev.get("text", ""))
        elif t in ("result", "error"):
            SESSION = ev.get("session_id") or SESSION
        if t == "done":
            break
    reply = "\n".join(texts).strip()
    print("\n" + "=" * 78 + f"\nUSER> {msg}\nTOOLS> {tools}\nAGENT> {reply[:1400]}", flush=True)
    return tools, reply


results = []


def check(cid, ok, evidence):
    results.append((cid, bool(ok), str(evidence)[:400]))
    print(f"[{'PASS' if ok else 'FAIL'}] {cid}: {str(evidence)[:300]}", flush=True)


# --- V1: a View visible to user 1
views_store.init()
views = views_store.list_views(UID, readthrough.user_group_ids(UID))
print("views visible to user 1:", [v.get("name") for v in views][:8], flush=True)
if views:
    vname = views[0]["name"]
    tools, reply = turn(f"Email my view '{vname}' to {RECIPIENT} with a one-line note that this is a live test.",
                        fresh=True, timeout=900)
    ok = ("send_email" in tools and "sent" in reply.lower() and "approval" not in reply.lower()
          and RECIPIENT.split()[0].lower() in reply.lower())
    check("V1 email a View to a NAME: send_email, resolved, SENT", ok, f"tools={tools} reply={reply[:220]}")
else:
    check("V1 email a View to a NAME", False, "user 1 has no visible Views on this box")

# --- R1/R2/R3: preferences (clean slate first)
preferences.forget(UID, "", clear_all=True)
tools, reply = turn("From now on, call me Jim, and always give temperatures in Celsius.", fresh=True)
saved = preferences.get(UID)
check("R1 remember_preference saved both", "remember_preference" in tools and len(saved) >= 1
      and any("jim" in p.lower() for p in saved) and any("celsius" in p.lower() for p in saved),
      f"tools={tools} saved={saved}")
tools, reply = turn("Quick check: what do you call me, and what unit do you use for temperatures?", fresh=True)
check("R2 NEW conversation answers from the injected block, no tools",
      not tools and "jim" in reply.lower() and "celsius" in reply.lower(),
      f"tools={tools} reply={reply[:200]}")
tools, reply = turn("Forget the temperature preference.", fresh=True)
saved = preferences.get(UID)
check("R3 forget_preference removed only the temperature one",
      "forget_preference" in tools and any("jim" in p.lower() for p in saved)
      and not any("celsius" in p.lower() for p in saved), f"tools={tools} saved={saved}")

preferences.forget(UID, "", clear_all=True)
print("\n" + "=" * 78)
for cid, ok, ev in results:
    print(f"[{'PASS' if ok else 'FAIL'}] {cid}")
print(f"REPORT {sum(1 for _, ok, _ in results if ok)}/{len(results)} passed")
sys.exit(0 if all(ok for _, ok, _ in results) else 1)
