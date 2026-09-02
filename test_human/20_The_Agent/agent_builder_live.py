"""Pack 20 — Agent Builder live drive (2026-09-02).

Drives the General-Agent builder tools through The Agent's /api/chat with REAL
streamed turns: create by name only, add tools, read config, restrict/clear
document types, upload + remove a knowledge document (two-step), rename +
disable, share with a group (admin), and the two-step delete. Every step is
checked against the platform DB through the service's own read-through, and
the agent is deleted at the end (group-share rows are cleaned up too).

Run:  C:\\Users\\james\\miniconda3\\envs\\aihub-agent\\python.exe agent_builder_live.py
      [agent name] [knowledge file path]
Baseline: 13/13 on 2026-09-02 (haiku-4-5 as the admin-override model).
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
import readthrough
import agent_config

BASE = f"http://127.0.0.1:{os.getenv('AGENT_SERVICE_PORT') or int(os.getenv('HOST_PORT', '5001')) + 110}"
MAIN = agent_config.get_base_url()
TOKEN = shared_auth.sign_cc_token({"user_id": 1, "role": 3, "tenant_id": os.getenv("TENANT_ID", ""),
                                   "username": "live-builder", "name": "Live Builder"})
NAME = sys.argv[1] if len(sys.argv) > 1 else "Gen Agent 1005"
KNOWLEDGE_FILE = (sys.argv[2] if len(sys.argv) > 2 else
                  os.path.join(APP_ROOT, "test_human", "04_Planning", "fixtures", "P2_annual_SOP.pdf"))
SESSION = None


def turn(msg, timeout=600):
    global SESSION
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
    print("\n" + "=" * 78 + f"\nUSER> {msg}\nTOOLS> {tools}\nAGENT> {reply[:1800]}", flush=True)
    return tools, reply


def db(fn):
    conn = readthrough._db()
    try:
        cur = conn.cursor()
        return fn(cur)
    finally:
        conn.close()


def agent_rows(name):
    def fn(cur):
        cur.execute("SELECT id, description, enabled, ISNULL(is_data_agent,0) FROM Agents WHERE description = ?", name)
        return [tuple(r) for r in cur.fetchall()]
    return db(fn)


def agent_tools(aid):
    def fn(cur):
        cur.execute("SELECT tool_name, custom_tool FROM AgentTools WHERE agent_id = ? ORDER BY tool_name", aid)
        return [(r[0], bool(r[1])) for r in cur.fetchall()]
    return db(fn)


def agent_doc_types(aid):
    def fn(cur):
        cur.execute("SELECT document_type FROM AgentDocumentTypes WHERE agent_id = ?", aid)
        return sorted(r[0] for r in cur.fetchall())
    return db(fn)


def agent_groups(aid):
    def fn(cur):
        cur.execute("SELECT group_id FROM AgentGroups WHERE agent_id = ?", aid)
        return sorted(r[0] for r in cur.fetchall())
    return db(fn)


def agent_knowledge(aid):
    def fn(cur):
        cur.execute("SELECT knowledge_id, document_id, is_active FROM AgentKnowledge WHERE agent_id = ?", aid)
        return [tuple(r) for r in cur.fetchall()]
    return db(fn)


def doc_types():
    r = requests.get(f"{MAIN}/api/document-types",
                     headers={"X-API-Key": agent_config.AI_HUB_API_KEY}, timeout=30)
    return [t["name"] for t in r.json()] if r.ok else []


results = []


def check(cid, ok, evidence):
    results.append((cid, bool(ok), str(evidence)[:400]))
    print(f"[{'PASS' if ok else 'FAIL'}] {cid}: {str(evidence)[:300]}", flush=True)


print("pre-existing rows for", NAME, ":", agent_rows(NAME), flush=True)

# 1. create by name only (the exact ask from James's transcript)
tools, reply = turn(f'create a new agent named "{NAME}"')
rows = agent_rows(NAME)
check("B1 create", "create_general_agent" in tools and len(rows) == 1, f"tools={tools} rows={rows}")
if not rows:
    print("no agent row — stopping")
    sys.exit(1)
AID = rows[0][0]
check("B1b default objective disclosed", "objective" in reply.lower(), reply[:200])
check("B1c mandatory tools auto-added",
      any(t == "get_the_current_date_and_time" for t, _ in agent_tools(AID)), agent_tools(AID))

# 2. tools
tools, reply = turn(f"give {NAME} the web search tool and the database query tool")
tl = agent_tools(AID)
check("B2 set_agent_tools",
      "set_agent_tools" in tools and ("web_search", False) in tl and ("query_database", False) in tl,
      f"tools={tools} db={tl}")

# 3. config read
tools, reply = turn(f"show me how {NAME} is configured")
check("B3 get_agent_config", "get_agent_config" in tools and "web_search" in reply, f"tools={tools}")

# 4. document types
types = doc_types()
print("document types available:", types[:12], flush=True)
if types:
    dt = types[0]
    tools, reply = turn(f"restrict {NAME}'s document access to the '{dt}' document type only")
    check("B4 set_agent_document_types",
          "set_agent_document_types" in tools and agent_doc_types(AID) == [dt],
          f"tools={tools} db={agent_doc_types(AID)}")
    tools, reply = turn(f"remove the document type restriction from {NAME} so it can see all documents")
    check("B4b clear restriction", agent_doc_types(AID) == [], f"tools={tools} db={agent_doc_types(AID)}")
else:
    check("B4 set_agent_document_types", False, "no document types on this install")

# 5. knowledge
if KNOWLEDGE_FILE and os.path.isfile(KNOWLEDGE_FILE):
    tools, reply = turn(f"add the file {KNOWLEDGE_FILE} as knowledge for {NAME}", timeout=1500)
    kn = agent_knowledge(AID)
    check("B5 add_agent_knowledge", "add_agent_knowledge" in tools and any(k[2] for k in kn),
          f"tools={tools} db={kn}")
    if kn:
        tools, reply = turn(f"remove that knowledge document from {NAME}")
        tools2, reply2 = turn("yes, remove it")
        kn2 = agent_knowledge(AID)
        check("B5b delete_agent_knowledge (two-step)",
              "delete_agent_knowledge" in tools + tools2 and not any(k[2] for k in kn2),
              f"first={tools} second={tools2} db={kn2}")
else:
    check("B5 add_agent_knowledge", False, "no knowledge file given")

# 6. rename + disable
tools, reply = turn(f"rename {NAME} to '{NAME} (renamed)' and disable it")
rows = agent_rows(f"{NAME} (renamed)")
check("B6 update_general_agent", "update_general_agent" in tools and rows and rows[0][2] is False,
      f"tools={tools} rows={rows}")
NAME2 = f"{NAME} (renamed)" if rows else NAME

# 7. share with a group (admin)
tools, reply = turn(f"share {NAME2} with the Analysts group")
gr = agent_groups(AID)
check("B7 assign_agent_groups", "assign_agent_groups" in tools and 5 in gr, f"tools={tools} db={gr}")

# 8. delete: two-step
tools, reply = turn(f"delete the agent {NAME2}")
check("B8a delete asks for confirmation", "delete_general_agent" in tools and agent_rows(NAME2),
      f"tools={tools} reply={reply[:200]}")
tools, reply = turn("yes, delete it")
check("B8b delete after confirmation", "delete_general_agent" in tools and not agent_rows(NAME2),
      f"tools={tools} rows={agent_rows(NAME2)}")


# cleanup any strays (group share rows are left by the page's delete too)
def cleanup(cur):
    cur.execute("DELETE FROM AgentGroups WHERE agent_id = ?", AID)
    cur.execute("DELETE FROM AgentDocumentTypes WHERE agent_id = ?", AID)
    cur.connection.commit()
    return True


try:
    db(cleanup)
except Exception as e:
    print("cleanup:", e)

print("\n" + "=" * 78)
for cid, ok, ev in results:
    print(f"[{'PASS' if ok else 'FAIL'}] {cid}")
print(f"{sum(1 for _, ok, _ in results if ok)}/{len(results)} passed")
sys.exit(0 if all(ok for _, ok, _ in results) else 1)
