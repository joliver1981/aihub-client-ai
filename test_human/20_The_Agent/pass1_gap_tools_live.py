"""Pack 20 — pass-1 gap tools live drive (2026-09-02).

Real streamed /api/chat turns exercising the pass-1 tools: search_web,
list_mcp_servers, get_my_contact_info, list_agents -> ask_agent delegation,
the code-flow editors (add / wire / unwire / update_step_code /
remove_code_step / two-step delete_code_flow) and send_email. Ground truth for
the code flow is read straight from the code-flows manage seam; the email goes
to the address given on the command line (default: none -> step skipped).

Run:  C:\\Users\\james\\miniconda3\\envs\\aihub-agent\\python.exe pass1_gap_tools_live.py [email-to]
Baseline: see REPORT line at the end (2026-09-02, haiku-4-5 admin override).
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

BASE = f"http://127.0.0.1:{os.getenv('AGENT_SERVICE_PORT') or int(os.getenv('HOST_PORT', '5001')) + 110}"
MAIN = agent_config.get_base_url()
TOKEN = shared_auth.sign_cc_token({"user_id": 1, "role": 3, "tenant_id": os.getenv("TENANT_ID", ""),
                                   "username": "pass1-live", "name": "Pass1 Live"})
EMAIL_TO = sys.argv[1] if len(sys.argv) > 1 else ""
FLOW = "Pass1 Edit Test"
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
    print("\n" + "=" * 78 + f"\nUSER> {msg}\nTOOLS> {tools}\nAGENT> {reply[:1600]}", flush=True)
    return tools, reply


def cf_get(name):
    r = requests.post(f"{MAIN}/codeflows/api/internal/manage",
                      json={"action": "get", "user_context": {"user_id": 1, "role": 3, "username": "pass1-live"},
                            "payload": {"name": name}},
                      headers={"X-API-Key": agent_config.AI_HUB_API_KEY}, timeout=60)
    return (r.json() if r.ok else None), r.status_code


results = []


def check(cid, ok, evidence):
    results.append((cid, bool(ok), str(evidence)[:400]))
    print(f"[{'PASS' if ok else 'FAIL'}] {cid}: {str(evidence)[:300]}", flush=True)


NO_INTERNET = ("don't have internet", "do not have internet", "can't browse", "cannot browse",
               "no internet access", "can't search the web", "cannot search the web")

# P1 web search
tools, reply = turn("What is the current weather in Newark, New Jersey right now? Use the web.", fresh=True)
check("P1 search_web used, no capability disclaimer",
      "search_web" in tools and not any(k in reply.lower() for k in NO_INTERNET),
      f"tools={tools} reply={reply[:160]}")

# P2 MCP servers
tools, reply = turn("What MCP servers are set up on this platform?")
check("P2 list_mcp_servers used", "list_mcp_servers" in tools, f"tools={tools}")

# P3 contact info
tools, reply = turn("What email address do you have on file for me?")
check("P3 get_my_contact_info used and address relayed",
      "get_my_contact_info" in tools and "@" in reply, f"tools={tools} reply={reply[:160]}")

# P4 agent discovery + delegation
tools, reply = turn("Which of our agents queries the ERPDB database? Ask it how many vendors it has and relay the answer.",
                    timeout=900)
check("P4 list_agents then ask_agent", "list_agents" in tools and "ask_agent" in tools,
      f"tools={tools} reply={reply[:200]}")

# P5 code-flow editing (fresh conversation so the model plans from scratch)
cf, st = cf_get(FLOW)
if st == 200:
    requests.post(f"{MAIN}/codeflows/api/internal/manage",
                  json={"action": "delete", "user_context": {"user_id": 1, "role": 3, "username": "pass1-live"},
                        "payload": {"name": FLOW}},
                  headers={"X-API-Key": agent_config.AI_HUB_API_KEY}, timeout=60)
tools, reply = turn(
    f"Create a code flow named '{FLOW}' with two steps: 'one' that prints hello and 'two' that prints world, "
    "wired one -> two on pass. Do not dry-run it.", fresh=True, timeout=900)
cf, st = cf_get(FLOW)
nodes = (cf or {}).get("code_flow", {}).get("nodes", []) if cf else []
check("P5a create + 2 steps + wire", st == 200 and len(nodes) == 2 and "wire_steps" in tools,
      f"tools={tools} nodes={[n.get('label') for n in nodes]}")
tools, reply = turn(
    "Now insert a step named 'middle' between one and two that prints middle (wire one -> middle -> two and remove "
    "the old direct edge), and change step two's code to print WORLD in capitals. Then show me the flow.",
    timeout=900)
cf, st = cf_get(FLOW)
flow = (cf or {}).get("code_flow", {}) if cf else {}
nodes = flow.get("nodes", [])
edges = [(e.get("source"), e.get("target")) for e in flow.get("connections", [])]
ids = {n.get("label"): n.get("id") for n in nodes}
direct = (ids.get("one"), ids.get("two")) in edges
check("P5b insert-between: unwire_steps + update_step_code used, old edge gone",
      "unwire_steps" in tools and "update_step_code" in tools and len(nodes) == 3 and not direct,
      f"tools={tools} nodes={list(ids)} edges={edges}")
tools, reply = turn("Remove the 'middle' step again.", timeout=600)
cf, st = cf_get(FLOW)
nodes = (cf or {}).get("code_flow", {}).get("nodes", []) if cf else []
check("P5c remove_code_step", "remove_code_step" in tools and len(nodes) == 2,
      f"tools={tools} nodes={[n.get('label') for n in nodes]}")
tools, reply = turn(f"Delete the code flow '{FLOW}'.")
cf, st = cf_get(FLOW)
check("P5d delete asks for confirmation first", "delete_code_flow" in tools and st == 200,
      f"tools={tools} still_exists={st == 200}")
tools, reply = turn("Yes, delete it.")
cf, st = cf_get(FLOW)
check("P5e delete after confirmation", "delete_code_flow" in tools and st == 404,
      f"tools={tools} status={st}")

# P6 send_email
if EMAIL_TO:
    tools, reply = turn(
        f"Send an email to {EMAIL_TO} with the subject 'The Agent pass-1 live test' saying this is a live test of "
        "the new send_email tool from The Agent, with a two-item bullet list of what pass 1 added.", fresh=True)
    check("P6 send_email used and reported SENT",
          "send_email" in tools and "sent" in reply.lower() and "approval" not in reply.lower(),
          f"tools={tools} reply={reply[:200]}")
else:
    check("P6 send_email", False, "no recipient given on the command line")

print("\n" + "=" * 78)
for cid, ok, ev in results:
    print(f"[{'PASS' if ok else 'FAIL'}] {cid}")
print(f"REPORT {sum(1 for _, ok, _ in results if ok)}/{len(results)} passed")
sys.exit(0 if all(ok for _, ok, _ in results) else 1)
