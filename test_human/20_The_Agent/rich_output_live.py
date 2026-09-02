"""Pack 20 — rich output + skill scoping live drive (2026-09-02, pass 2).

Real streamed /api/chat turns:
  C1  user-supplied numbers -> the reply carries an ```aihub-chart``` block AND
      an ```aihub-kpi``` block with EXACTLY the user's numbers; the Skill tool
      is NOT invoked for the CLI's bundled "dataviz" skill (skills are scoped
      to the mounted set).
  C2  run_python plot -> the reply carries an inline image line
      ![…png](/api/files/<id>) plus the download link.
  C3  a platform question still loads a MOUNTED product skill (Skill tool
      invoked with a skill name from the workspace) — scoping did not break skills.
The browser-side rendering (Chart.js canvas, KPI cards, blob-fetched images)
is verified by screenshot in the in-app browser; this script pins the wire
contract the renderer consumes.

Run:  C:\\Users\\james\\miniconda3\\envs\\aihub-agent\\python.exe rich_output_live.py
"""
import json
import os
import re
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

BASE = f"http://127.0.0.1:{os.getenv('AGENT_SERVICE_PORT') or int(os.getenv('HOST_PORT', '5001')) + 110}"
TOKEN = shared_auth.sign_cc_token({"user_id": 13, "role": 3, "tenant_id": os.getenv("TENANT_ID", ""),
                                   "username": "rich-live", "name": "Rich Live"})
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
            tools.append((ev.get("name", "").replace("mcp__aihub__", ""), ev.get("input") or {}))
        elif t == "text":
            texts.append(ev.get("text", ""))
        elif t in ("result", "error"):
            SESSION = ev.get("session_id") or SESSION
        if t == "done":
            break
    reply = "\n".join(texts).strip()
    print("\n" + "=" * 78 + f"\nUSER> {msg}\nTOOLS> {[(n, (i.get('skill') if n == 'Skill' else '')) for n, i in tools]}"
          f"\nAGENT> {reply[:1200]}", flush=True)
    return tools, reply


def blocks(reply, kind):
    out = []
    for m in re.finditer(r"```aihub-" + kind + r"\s*\n(.*?)\n```", reply, re.S):
        try:
            out.append(json.loads(m.group(1)))
        except Exception:
            out.append(None)
    return out


def skills_invoked(tools):
    return [i.get("skill") for n, i in tools if n == "Skill"]


results = []


def check(cid, ok, evidence):
    results.append((cid, bool(ok), str(evidence)[:400]))
    print(f"[{'PASS' if ok else 'FAIL'}] {cid}: {str(evidence)[:300]}", flush=True)


# C1 — chart + KPI from the user's own numbers, no foreign skill
tools, reply = turn("Here are last quarter's sales by region: East 120500, West 98000, North 75250, "
                    "South 66100. Show me a bar chart of these numbers and KPI cards for the total and "
                    "the top region.", fresh=True)
ch = blocks(reply, "chart")
kp = blocks(reply, "kpi")
spec = ch[0] if ch and ch[0] else {}
series = (spec.get("series") or [{}])[0].get("data") if spec else None
ok_chart = bool(spec) and set(spec.get("labels") or []) == {"East", "West", "North", "South"} \
    and sorted(float(x) for x in (series or [])) == [66100.0, 75250.0, 98000.0, 120500.0]
ok_kpi = bool(kp and kp[0] and kp[0].get("cards"))
check("C1a aihub-chart block with the user's exact numbers", ok_chart, f"spec={spec}")
check("C1b aihub-kpi block present", ok_kpi, f"kpi={kp}")
check("C1c bundled 'dataviz' skill NOT invoked", "dataviz" not in skills_invoked(tools),
      f"skills={skills_invoked(tools)}")

# C2 — run_python image inline
tools, reply = turn("Use run_python to plot y = x squared for x from 1 to 10 as a line chart, save it "
                    "as a png, and show it to me.", fresh=True, timeout=900)
img = re.findall(r"!\[[^\]]*\.png\]\(/api/files/[0-9a-f-]+\)", reply)
dl = re.findall(r"\[⤓[^\]]*\.png[^\]]*\]\(/api/files/[0-9a-f-]+\)", reply)
check("C2 run_python image line + download link in the reply",
      any(n == "run_python" for n, _ in tools) and img and dl, f"img={img} dl={dl}")

# C3 — a mounted product skill still loads
tools, reply = turn("Before I ask you to do it: what are the rules for sharing an integration with a "
                    "group of regular users in AI Hub? Load the relevant skill first.", fresh=True)
sk = skills_invoked(tools)
check("C3 mounted product skill still loads (aihub-*)", any(str(s).startswith("aihub-") for s in sk),
      f"skills={sk}")

print("\n" + "=" * 78)
for cid, ok, ev in results:
    print(f"[{'PASS' if ok else 'FAIL'}] {cid}")
print(f"REPORT {sum(1 for _, ok, _ in results if ok)}/{len(results)} passed")
sys.exit(0 if all(ok for _, ok, _ in results) else 1)
