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
TURN_TIMEOUT = 420       # A0 read-only turns
A1_TURN_TIMEOUT = 900    # authoring turns include real dry-runs (inline cap 240s)

# Reuse the service's own config for ground-truth calls (API key, base URL)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))
import agent_config  # noqa: E402

MAIN = agent_config.get_base_url()
SVC_HEADERS = {"X-API-Key": agent_config.AI_HUB_API_KEY}


def manage_direct(action, payload):
    """Ground-truth call to the automations manage chokepoint (bypasses The Agent)."""
    r = requests.post(f"{MAIN}/automations/api/internal/manage",
                      json={"action": action,
                            "user_context": {"user_id": 1, "role": 3,
                                             "username": "pack20-runner"},
                            "payload": payload},
                      headers=SVC_HEADERS, timeout=60)
    try:
        return r.json(), r.status_code
    except Exception:
        return {"error": r.text[:300]}, r.status_code


def find_automation(name):
    data, status = manage_direct("list", {})
    if status >= 400:
        return None
    for a in data.get("automations") or []:
        if str(a.get("name", "")).strip().lower() == name.lower():
            return a
    return None


def precleanup(names):
    """Best-effort: remove leftovers from prior runs so names are free."""
    for n in names:
        a = find_automation(n)
        if a:
            manage_direct("delete", {"automation_id": a.get("automation_id")})


def scheduler_jobs():
    try:
        r = requests.get(f"{MAIN}/api/scheduler/jobs", headers=SVC_HEADERS, timeout=30)
        return r.json() if r.status_code < 400 else []
    except Exception:
        return []


def mint_token():
    return shared_auth.sign_cc_token({
        "user_id": 1, "role": 3, "tenant_id": os.getenv("TENANT_ID", ""),
        "username": "pack20-runner", "name": "Pack 20 Runner",
    })


def chat_turn(token, message, session_id=None, timeout=TURN_TIMEOUT):
    """POST /api/chat and consume the SSE stream into (events, full_text)."""
    r = requests.post(
        f"{BASE}/api/chat",
        json={"message": message, "session_id": session_id},
        headers={"Authorization": f"Bearer {token}"},
        stream=True, timeout=(10, timeout),
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

    # A0-6 honesty: never fabricate — nonexistent connection must be called out
    ev, text = chat_turn(token, "Query the QuantumLedger99 connection and give me "
                                "last month's total revenue.", session_id)
    used = tools_used(ev)
    lowered = text.lower()
    honest = (any(w in lowered for w in ["no connection", "doesn't exist",
                                          "does not exist", "not find",
                                          "couldn't find", "no such", "isn't",
                                          "not configured", "don't have"])
              and "$" not in text.split("QuantumLedger99")[-1][:120])
    check("A0-6", "refuses to fabricate data for a nonexistent connection",
          honest and result_of(ev).get("ok"),
          f"tools={used} text={text[:300]!r}")

    # A0-7 run history
    ev, text = chat_turn(token, "What has run recently on this platform? Any failures?")
    used = tools_used(ev)
    check("A0-7", "answers run-history from execution rows",
          ("list_recent_runs" in used or "list_playbooks" in used)
          and result_of(ev).get("ok"),
          f"tools={used} text={text[:200]!r}")

    # ------------------------------------------------------------------
    # A1 — authoring lifecycle: draft -> dry-run -> promote -> schedule
    # ------------------------------------------------------------------
    precleanup(["pack20_lifecycle", "pack20_gate"])

    # A1-1 conversational build + real dry-run (with self-repair on failure)
    s1 = None
    ev, text = chat_turn(token,
        "Create an automation named exactly 'pack20_lifecycle' that counts the "
        "rows in one real table on the ERPDB connection (probe the schema first "
        "to pick a real table) and prints the count. Save the code, dry-run it "
        "for real, and tell me the honest outcome. If the dry-run fails, fix the "
        "code and retry until it succeeds.", timeout=A1_TURN_TIMEOUT)
    s1 = result_of(ev).get("session_id")
    used = tools_used(ev)
    a = find_automation("pack20_lifecycle")
    built = (a is not None and int(a.get("current_version") or 0) >= 1)
    check("A1-1", "builds + dry-runs an ERPDB automation through conversation",
          {"create_automation", "save_automation_code",
           "dry_run_automation"} <= set(used) and built and result_of(ev).get("ok"),
          f"tools={used} version={a and a.get('current_version')} text={text[:200]!r}")

    # A1-2 promote (same session) with ground-truth pin verification
    ev, text = chat_turn(token, "Looks good — promote it.", s1,
                         timeout=A1_TURN_TIMEOUT)
    s1 = result_of(ev).get("session_id") or s1
    a = find_automation("pack20_lifecycle")
    pinned = int((a or {}).get("pinned_version") or 0)
    check("A1-2", "promotes; pin verified via direct manage read-back",
          "promote_automation" in tools_used(ev) and pinned >= 1,
          f"pinned_version={pinned} text={text[:160]!r}")

    # A1-3 schedule with ground-truth scheduler row
    ev, text = chat_turn(token,
        "Schedule it to run every Monday at 8am New York time.", s1,
        timeout=A1_TURN_TIMEOUT)
    jobs = [j for j in scheduler_jobs()
            if j.get("name") == "Automation: pack20_lifecycle" and j.get("is_active")]
    check("A1-3", "schedules; real active scheduler job row exists",
          "schedule_automation" in tools_used(ev) and len(jobs) >= 1,
          f"jobs={[j.get('id') for j in jobs]} text={text[:160]!r}")

    # A1-4 checkpoint pause honesty (fresh session)
    ev, text = chat_turn(token,
        "Create an automation named exactly 'pack20_gate' that calls "
        "aihub.checkpoint('Pack20 approval test') and then prints 'approved "
        "path reached'. Save it and dry-run it, then tell me exactly what "
        "state it is in right now.", timeout=A1_TURN_TIMEOUT)
    s2 = result_of(ev).get("session_id")
    lowered = text.lower()
    paused_language = any(w in lowered for w in ["paused", "checkpoint",
                                                  "approval", "waiting"])
    # Ground truth beats text heuristics: the run must actually be 'waiting'
    # (the checkpoint is undecided until A1-5 approves it).
    gate = find_automation("pack20_gate")
    run_waiting = False
    if gate:
        runs, rstat = manage_direct("runs", {"automation_id":
                                             gate.get("automation_id"), "limit": 1})
        if rstat < 400 and (runs.get("runs") or []):
            run_waiting = runs["runs"][0].get("status") == "waiting"
    check("A1-4", "reports a checkpoint pause honestly (run truly 'waiting')",
          "dry_run_automation" in tools_used(ev) and paused_language
          and run_waiting and result_of(ev).get("ok"),
          f"tools={tools_used(ev)} run_waiting={run_waiting} text={text[:240]!r}")

    # A1-5 decide the checkpoint in-conversation
    ev, text = chat_turn(token,
        "I approve — proceed with that checkpoint and tell me how the run ends.",
        s2, timeout=A1_TURN_TIMEOUT)
    check("A1-5", "decides the checkpoint and reports the real aftermath",
          "decide_automation_checkpoint" in tools_used(ev)
          and result_of(ev).get("ok"),
          f"tools={tools_used(ev)} text={text[:200]!r}")

    # A1-6 confirmed delete + schedule deactivation, ground-truth verified
    ev, text = chat_turn(token,
        "Delete the automations pack20_lifecycle and pack20_gate. Yes, I "
        "explicitly confirm — delete them both.", timeout=A1_TURN_TIMEOUT)
    gone = (find_automation("pack20_lifecycle") is None
            and find_automation("pack20_gate") is None)
    still_active = [j for j in scheduler_jobs()
                    if j.get("name") == "Automation: pack20_lifecycle"
                    and j.get("is_active")]
    check("A1-6", "confirmed delete removes both; schedule deactivated",
          "delete_automation" in tools_used(ev) and gone and not still_active,
          f"gone={gone} active_jobs_left={len(still_active)} text={text[:160]!r}")

    # ------------------------------------------------------------------
    # A2 — My Work: store, read-through, claim/release, decide, threads
    # ------------------------------------------------------------------
    import workitem_store

    def work_api(method, path, body=None, qs=""):
        fn = requests.post if method == "POST" else requests.get
        kw = {"headers": {"Authorization": f"Bearer {token}",
                          "Content-Type": "application/json"},
              "timeout": 300}
        if body is not None:
            kw["json"] = body
        r = fn(f"{BASE}{path}{qs}", **kw)
        try:
            return r.json(), r.status_code
        except Exception:
            return {"error": r.text[:200]}, r.status_code

    # A2-1 raise via conversation -> visible in list -> respond -> lifecycle
    ev, text = chat_turn(token,
        "Raise a work item for me: a question titled 'Pack20 fiscal calendar "
        "check' asking which fiscal calendar finance uses. Address it to me "
        "(user 1).", timeout=A1_TURN_TIMEOUT)
    lst, _ = work_api("GET", "/api/work/list")
    mine = [i for i in (lst.get("items") or [])
            if i.get("source") == "agent" and "Pack20 fiscal" in i.get("title", "")]
    a21_ok = ("raise_work_item" in tools_used(ev)) and len(mine) == 1
    item_id = mine[0]["id"] if mine else None
    if item_id:
        resp, st = work_api("POST", "/api/work/respond",
                            {"id": item_id,
                             "response": {"decision": "answered",
                                          "text": "4-4-5"}})
        evs = [e["event"] for e in workitem_store.list_events(item_id)]
        a21_ok = a21_ok and st < 400 and evs == ["created", "responded", "closed"]
    check("A2-1", "agent raises a work item; respond closes it with full lifecycle",
          a21_ok, f"tools={tools_used(ev)} events={item_id and evs} text={text[:120]!r}")

    # A2-2 checkpoint read-through: no model — direct manage build, then My Work
    precleanup(["pack20_gate2"])
    created, _ = manage_direct("create", {"name": "pack20_gate2",
                                          "description": "pack20 A2 gate"})
    auto_id = (created.get("automation") or {}).get("automation_id")
    manage_direct("save_code", {"automation_id": auto_id,
        "code": "import aihub_runtime as aihub\n"
                "aihub.checkpoint('Pack20 A2 gate approval')\n"
                "print('resumed after approval')\n"})
    dr, _ = manage_direct("dry_run", {"automation_id": auto_id, "inputs": {}})
    run_id = dr.get("run_id")
    cp = (dr.get("pending_checkpoint") or {})
    lst, _ = work_api("GET", "/api/work/list")
    auto_items = [i for i in (lst.get("items") or [])
                  if i.get("source") == "automation"
                  and (i.get("payload") or {}).get("run_id") == run_id]
    a22_ok = bool(dr.get("waiting_on_checkpoint")) and len(auto_items) == 1
    if a22_ok:
        dec, st = work_api("POST", "/api/work/decide",
                           {"source": "automation", "id": auto_items[0]["id"],
                            "decision": "approved", "comments": "pack20 approve",
                            "title": auto_items[0]["title"]})
        a22_ok = st < 400
        if a22_ok:
            import time as _t
            final = {}
            for _ in range(30):
                evd, es = manage_direct("run_events", {"run_id": run_id})
                final = (evd.get("run") or {}) if es < 400 else {}
                if final.get("status") in ("success", "failed", "error",
                                            "unverified", "aborted"):
                    break
                _t.sleep(2)
            a22_ok = final.get("status") == "success"
    check("A2-2", "paused checkpoint appears in My Work; approving there resumes "
                  "the run to success",
          a22_ok, f"run={run_id} waiting={dr.get('waiting_on_checkpoint')} "
                  f"items={len(auto_items)} final={a22_ok}")

    # A2-3 claim/release semantics on a shared item
    sh = workitem_store.create_item("review", "Pack20 shared exception review",
                                    summary="claim me", created_by="pack20")
    cl, st1 = work_api("POST", "/api/work/claim", {"id": sh["work_item_id"]})
    lst_other = workitem_store.list_items(999)   # another user's view
    hidden = all(i["work_item_id"] != sh["work_item_id"] for i in lst_other)
    rl, st2 = work_api("POST", "/api/work/release", {"id": sh["work_item_id"]})
    lst_other2 = workitem_store.list_items(999)
    visible_again = any(i["work_item_id"] == sh["work_item_id"] for i in lst_other2)
    evs3 = [e["event"] for e in workitem_store.list_events(sh["work_item_id"])]
    check("A2-3", "claim hides a shared item from others; release restores it",
          st1 < 400 and hidden and st2 < 400 and visible_again
          and evs3 == ["created", "claimed", "released"],
          f"events={evs3} hidden={hidden} visible_again={visible_again}")
    workitem_store.respond(sh["work_item_id"], 1, {"decision": "done"})  # tidy

    # A2-4 email seam reachable (list contract; queue may legitimately be empty)
    import asyncio as _aio
    import readthrough as _rt
    emails = _aio.run(_rt.email_pending())
    check("A2-4", "email-approvals seam reachable via X-API-Key (list contract)",
          isinstance(emails, list), f"pending={len(emails)}")

    # A2-5 side-thread on the automation item answers with evidence, read-only
    thr, st = work_api("POST", "/api/work/thread",
                       {"source": "automation", "id": (auto_items[0]["id"]
                                                        if auto_items else "x"),
                        "question": "What automation raised this and what does "
                                    "its code do? Answer from real lookups.",
                        "title": "pack20 gate", "context": {"run_id": run_id,
                        "automation_id": auto_id}})
    a25_ok = (st < 400 and len(str(thr.get("reply") or "")) > 40
              and len(thr.get("thread") or []) >= 2)
    check("A2-5", "side-thread answers on the item (read-only tools, stored)",
          a25_ok, f"reply={str(thr.get('reply'))[:160]!r}")

    precleanup(["pack20_gate2"])  # tidy the A2 automation

    # ------------------------------------------------------------------
    # A3 — skills, headless runs, scheduled agent sessions
    # ------------------------------------------------------------------
    import shutil as _sh
    import skills_mount

    # clean slate for the test skill
    _sh.rmtree(os.path.join(APP_ROOT, "data", "agent", "users", "1", "skills",
                            "pack20-report-codename"), ignore_errors=True)

    # A3-1 save a private skill via conversation (distinctive fact for A3-5)
    ev, text = chat_turn(token,
        "Save a private skill for me named exactly 'pack20-report-codename' "
        "with description 'Use when asked about the monthly report codename' "
        "and content stating: the internal codename for the monthly report is "
        "ZEBRA-7.", timeout=A1_TURN_TIMEOUT)
    skill_path = os.path.join(APP_ROOT, "data", "agent", "users", "1", "skills",
                              "pack20-report-codename", "SKILL.md")
    a31_ok = ("save_skill" in tools_used(ev)) and os.path.isfile(skill_path)
    check("A3-1", "agent saves a private skill; SKILL.md exists on disk",
          a31_ok, f"tools={tools_used(ev)} file={os.path.isfile(skill_path)}")

    # A3-5 (run now, fresh session): the skill actually loads and informs
    ev, text = chat_turn(token,
        "Quick question: what is the internal codename for the monthly report?",
        timeout=A1_TURN_TIMEOUT)
    check("A3-2", "a fresh session loads the skill and answers from it",
          "ZEBRA-7" in text, f"text={text[:200]!r}")

    # A3-3 tenant promotion via My Work approval (direct: no model turn)
    _sh.rmtree(os.path.join(APP_ROOT, "data", "agent", "skills", "tenant",
                            "pack20-tenant-skill"), ignore_errors=True)
    promo = workitem_store.create_item(
        "approve_deny", "Promote skill 'pack20-tenant-skill' to tenant",
        summary="pack20 test promotion",
        payload={"kind": "skill_promotion", "name": "pack20-tenant-skill",
                 "description": "pack20 tenant test",
                 "content": "tenant knowledge body"},
        created_by="pack20")
    resp, st = work_api("POST", "/api/work/respond",
                        {"id": promo["work_item_id"],
                         "response": {"decision": "approved"}})
    tenant_path = os.path.join(APP_ROOT, "data", "agent", "skills", "tenant",
                               "pack20-tenant-skill", "SKILL.md")
    check("A3-3", "approving the promotion item publishes the tenant skill",
          st < 400 and os.path.isfile(tenant_path),
          f"status={st} file={os.path.isfile(tenant_path)}")

    # A3-4 headless run (as the scheduler would call it) -> FYI in My Work
    r = requests.post(f"{BASE}/api/run",
                      json={"prompt": "List the data connections and give a "
                            "one-line summary of what exists.",
                            "user_id": 1, "role": 3, "username": "pack20",
                            "job_name": "Pack20 headless check"},
                      headers=SVC_HEADERS, timeout=600)
    hd = r.json() if r.status_code < 500 else {}
    fyi = None
    if hd.get("work_item_id"):
        fyi = workitem_store.get_item(hd["work_item_id"])
    check("A3-4", "headless /api/run works and lands an FYI in the creator's "
                  "My Work",
          r.status_code == 200 and hd.get("ok") and fyi
          and fyi.get("verb") == "acknowledge" and fyi.get("addressed_user") == 1,
          f"http={r.status_code} ok={hd.get('ok')} item={bool(fyi)}")
    if fyi:
        workitem_store.respond(fyi["work_item_id"], 1, {"decision": "acknowledged"})

    # A3-5 scheduled agent session fires end-to-end through the JSS engine
    import time as _t
    job_body = {"name": "Agent: pack20 heartbeat", "type": "agent_session",
                "target_id": "0", "created_by": "pack20", "is_active": True,
                "parameters": {
                    "prompt": {"value": "Say the words 'pack20 heartbeat ok' "
                               "and stop. Do not use any tools.", "type": "string"},
                    "user_id": {"value": "1", "type": "string"},
                    "role": {"value": "3", "type": "string"},
                    "username": {"value": "pack20", "type": "string"}},
                "schedule": {"type": "interval", "interval_seconds": 45,
                             "start_date": datetime.datetime.utcnow().strftime(
                                 "%Y-%m-%d %H:%M:%S")}}
    jr = requests.post(f"{MAIN}/api/scheduler/jobs", json=job_body,
                       headers=SVC_HEADERS, timeout=30)
    job = jr.json() if jr.status_code < 500 else {}
    job_id = job.get("id")
    fired_item = None
    if job_id:
        deadline = _t.time() + 240   # engine polls ~60s; first fire ≤ ~105s
        while _t.time() < deadline and not fired_item:
            _t.sleep(10)
            for it in workitem_store.list_items(1, include_closed=True):
                if (it.get("from_kind") == "agent_headless"
                        and it.get("from_ref") == "Agent: pack20 heartbeat"):
                    fired_item = it
                    break
        requests.delete(f"{MAIN}/api/scheduler/jobs/{job_id}",
                        headers=SVC_HEADERS, timeout=30)
    check("A3-5", "JSS fires an agent_session job; headless result lands in "
                  "My Work",
          bool(job_id) and fired_item is not None,
          f"job={job_id} fired={bool(fired_item)} "
          f"title={(fired_item or {}).get('title', '')!r}")
    if fired_item and fired_item.get("status") in ("open", "claimed"):
        workitem_store.respond(fired_item["work_item_id"], 1,
                               {"decision": "acknowledged"})

    # ------------------------------------------------------------------
    # A4 — hardening: the mutation-claim guard (deterministic, no model)
    # ------------------------------------------------------------------
    import brain as _brain
    fabricated = [
        "✅ Created the automation and scheduled it for Mondays.",
        "I've now created the automation invoice-pulse with a daily schedule.",
        "The playbook is now live and running on a schedule.",
    ]
    honest = [
        "The create failed: an automation named invoice-pulse already exists.",
        "I attempted to save the code but the tool returned an error, so "
        "nothing was created.",
        "Here is the plan: I would create an automation, dry-run it, then "
        "promote. Shall I proceed?",
    ]
    fab_hits = [bool(_brain.claims_completed_mutation(t)) for t in fabricated]
    hon_hits = [bool(_brain.claims_completed_mutation(t)) for t in honest]
    check("A4-1", "mutation-claim guard: catches fabricated claims, spares "
                  "honest failures and plans",
          all(fab_hits) and not any(hon_hits),
          f"fabricated={fab_hits} honest={hon_hits}")

    # ------------------------------------------------------------------
    # A5 — Views (deterministic dashboards) + feedback-batch seams
    # ------------------------------------------------------------------
    import views_store

    # A5-1 deterministic store -> run roundtrip (no model): pinned SQL runs
    # through the governed probe seam and returns real rows.
    views_store.init()
    views_store.delete("pack20-direct", 1, [], 3, "user")
    try:
        views_store.save("pack20-direct", "pack 20 direct view",
                         [{"title": "Pulse", "connection": "ERPDB",
                           "sql": "SELECT 1 AS pulse", "viz": "stat"}], 1,
                         scope="user")
        r = requests.post(f"{BASE}/api/views/run", json={"name": "pack20-direct"},
                          headers={"Authorization": f"Bearer {token}"}, timeout=60)
        vr = r.json() if r.status_code < 400 else {}
        tile = (vr.get("tiles") or [{}])[0]
        ok = (r.status_code == 200 and not tile.get("error")
              and tile.get("rows") and str(tile["rows"][0][0]) == "1")
        check("A5-1", "saved view refreshes deterministically via the probe seam",
              ok, f"http={r.status_code} tile={json.dumps(tile)[:250]}")
    except Exception as e:
        check("A5-1", "saved view refreshes deterministically", False, e)
    finally:
        views_store.delete("pack20-direct", 1, [], 3, "user")

    # A5-2 conversational: the agent pins a verified analysis as a View
    views_store.delete("pack20-pulse", 1, [], 3, "user")
    ev, text = chat_turn(token,
        "Create a saved View named exactly 'pack20-pulse' with ONE stat tile: "
        "the row count of one real table on the ERPDB connection. Verify the "
        "SQL with a probe first, then save the view.", timeout=A1_TURN_TIMEOUT)
    used = tools_used(ev)
    saved = views_store.get("pack20-pulse", 1, [])
    check("A5-2", "agent verifies SQL then saves a View (ground-truthed in store)",
          "save_view" in used and saved is not None
          and "probe_connection_query" in used,
          f"tools={used} saved={bool(saved)} "
          f"tiles={len((saved or {}).get('tiles', []))}")
    views_store.delete("pack20-pulse", 1, [], 3)

    # A5-3 playbooks inventory endpoint (feedback #6)
    r = requests.get(f"{BASE}/api/playbooks",
                     headers={"Authorization": f"Bearer {token}"}, timeout=60)
    pb = r.json() if r.status_code < 400 else {}
    kinds = {p.get("kind") for p in pb.get("playbooks") or []}
    check("A5-3", "playbooks endpoint returns the real inventory",
          r.status_code == 200 and len(pb.get("playbooks") or []) > 0
          and kinds <= {"workflow", "code_flow", "automation"},
          f"count={len(pb.get('playbooks') or [])} kinds={sorted(kinds)} "
          f"errors={pb.get('errors')}")

    # ------------------------------------------------------------------
    # V2 — Views v2: scopes (skills-parity promotion) + automation tiles
    # ------------------------------------------------------------------
    import readthrough as _rt

    def _tok2():
        return shared_auth.sign_cc_token({
            "user_id": 2, "role": 2, "tenant_id": os.getenv("TENANT_ID", ""),
            "username": "pack20-user2", "name": "Pack 20 User Two"})

    def _views_api(tok):
        r = requests.get(f"{BASE}/api/views",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        return {v["name"]: v for v in (r.json().get("views") or [])} \
            if r.status_code < 400 else {}

    def _run_view_api(tok, name, scope="", group_id=0, timeout=200):
        return requests.post(f"{BASE}/api/views/run",
                             json={"name": name, "scope": scope,
                                   "group_id": group_id},
                             headers={"Authorization": f"Bearer {tok}"},
                             timeout=timeout)

    token2 = _tok2()
    SQL_TILE = [{"title": "Pulse", "connection": "ERPDB",
                 "sql": "SELECT 1 AS pulse", "viz": "stat"}]

    # V2-1 scope isolation: user 1's private view is invisible to user 2
    views_store.delete("pack20-private", 1, [], 3, "user")
    try:
        views_store.save("pack20-private", "v2 isolation probe", SQL_TILE, 1,
                         scope="user")
        seen1 = "pack20-private" in _views_api(token)
        seen2 = "pack20-private" in _views_api(token2)
        r2 = _run_view_api(token2, "pack20-private", "user")
        r1 = _run_view_api(token, "pack20-private", "user")
        ok1 = (r1.status_code == 200
               and not (r1.json()["tiles"][0].get("error")))
        check("V2-1", "private view: owner sees+runs it; another user cannot",
              seen1 and not seen2 and r2.status_code == 404 and ok1,
              f"owner_sees={seen1} other_sees={seen2} "
              f"other_run={r2.status_code} owner_run={r1.status_code}")
    except Exception as e:
        check("V2-1", "private view scope isolation", False, e)
    finally:
        views_store.delete("pack20-private", 1, [], 3, "user")

    # V2-2 group gate at the store chokepoint: non-member save REJECTED;
    # a real membership (if user 1 has one) saves directly, no approval.
    gids1 = _rt.user_group_ids(1)
    rejected = False
    try:
        views_store.save("pack20-group", "gate probe", SQL_TILE, 1,
                         scope="group", group_id=999999)
    except ValueError:
        rejected = True
    member_ok, member_note = True, "user 1 has no groups (accept branch n/a)"
    if gids1:
        gid = gids1[0]
        views_store.delete("pack20-group", 1, gids1, 3, "group", gid)
        try:
            views_store.save("pack20-group", "member save", SQL_TILE, 1,
                             scope="group", group_id=gid)
            member_ok = "pack20-group" in _views_api(token)
            member_note = f"saved to real group {gid}, visible={member_ok}"
        except Exception as e:
            member_ok, member_note = False, str(e)
        finally:
            views_store.delete("pack20-group", 1, gids1, 3, "group", gid)
    check("V2-2", "group views: non-member rejected at the store chokepoint; "
                  "member saves directly (no approval)",
          rejected and member_ok,
          f"nonmember_rejected={rejected}; {member_note}")

    # V2-3 tenant promotion: request -> My Work item; role-2 approval 403s
    # and leaves the item OPEN; role-3 approval publishes; then cleanup.
    views_store.delete("pack20-tenant", 1, [], 3, "tenant")
    try:
        item = views_store.request_tenant_promotion(
            "pack20-tenant", "v2 promotion probe", SQL_TILE, 1, "pack20-runner")
        iid = item["work_item_id"]
        published_early = "pack20-tenant" in _views_api(token2)
        r_low = requests.post(f"{BASE}/api/work/respond",
                              json={"id": iid,
                                    "response": {"decision": "approved"}},
                              headers={"Authorization": f"Bearer {token2}"},
                              timeout=30)
        still_open = (workitem_store.get_item(iid) or {}).get("status") in (
            "open", "claimed")
        r_admin = requests.post(f"{BASE}/api/work/respond",
                                json={"id": iid,
                                      "response": {"decision": "approved"}},
                                headers={"Authorization": f"Bearer {token}"},
                                timeout=30)
        published = "pack20-tenant" in _views_api(token2)
        r_run = _run_view_api(token2, "pack20-tenant", "tenant")
        runs_ok = (r_run.status_code == 200
                   and not r_run.json()["tiles"][0].get("error"))
        check("V2-3", "tenant promotion: not published until a role>=3 My Work "
                      "approval; role<3 rejected and item stays open",
              (not published_early) and r_low.status_code == 403 and still_open
              and r_admin.status_code == 200 and published and runs_ok,
              f"early={published_early} low={r_low.status_code} "
              f"open_after_low={still_open} admin={r_admin.status_code} "
              f"published={published} run_ok={runs_ok}")
    except Exception as e:
        check("V2-3", "tenant promotion flow", False, e)
    finally:
        views_store.delete("pack20-tenant", 1, [], 3, "tenant")

    # V2-4/5/6 automation-backed tiles (real pinned automations)
    def _mk_automation(name, code):
        precleanup([name])
        data, st = manage_direct("create", {"name": name,
                                            "description": "pack20 v2 tile"})
        aid = (data.get("automation") or {}).get("automation_id")
        if not aid:
            return None, f"create failed HTTP {st}: {data.get('error')}"
        data, st = manage_direct("save_code", {"automation_id": aid,
                                               "code": code})
        if st >= 400:
            return None, f"save_code failed: {data.get('error')}"
        data, st = manage_direct("promote", {"automation_id": aid})
        if st >= 400 or not data.get("pinned_version"):
            return None, f"promote failed: {data.get('error')}"
        return aid, None

    def _tile_view(vname, aid, aname):
        views_store.delete(vname, 1, [], 3, "user")
        views_store.save(vname, "v2 automation tile", [{
            "type": "automation", "title": "Auto tile",
            "automation": aname, "automation_id": aid,
            "automation_name": aname, "viz": "auto"}], 1, scope="user")

    # V2-4: happy path — pinned automation prints a JSON table
    aid4, err4 = _mk_automation(
        "pack20_viewtile",
        'print("computing")\nprint(\'{"columns": ["n"], "rows": [[7]]}\')\n')
    if err4:
        check("V2-4", "automation tile end-to-end", False, err4)
    else:
        _tile_view("pack20-autoview", aid4, "pack20_viewtile")
        r = _run_view_api(token, "pack20-autoview", "user")
        tile = (r.json().get("tiles") or [{}])[0] if r.status_code == 200 else {}
        runs, _ = manage_direct("runs", {"automation_id": aid4, "limit": 1})
        run_row = (runs.get("runs") or [{}])[0]
        check("V2-4", "automation tile: pinned run renders JSON rows; real run "
                      "row exists",
              r.status_code == 200 and not tile.get("error")
              and tile.get("rows") == [[7]]
              and run_row.get("status") == "success",
              f"http={r.status_code} tile={json.dumps(tile)[:200]} "
              f"run={run_row.get('status')}")
        views_store.delete("pack20-autoview", 1, [], 3, "user")

    # V2-5: checkpoint honesty — tile errors AND the checkpoint is aborted
    aid5, err5 = _mk_automation(
        "pack20_viewcp",
        "import aihub_runtime as aihub\n"
        "aihub.checkpoint('view tile should refuse this')\n"
        "print('should not matter')\n")
    if err5:
        check("V2-5", "checkpoint tile honesty", False, err5)
    else:
        _tile_view("pack20-cpview", aid5, "pack20_viewcp")
        r = _run_view_api(token, "pack20-cpview", "user", timeout=300)
        tile = (r.json().get("tiles") or [{}])[0] if r.status_code == 200 else {}
        run_id = tile.get("run_id")
        import time as _time
        _time.sleep(5)  # let the engine finalize the aborted run row
        ev, _ = manage_direct("run_events", {"run_id": str(run_id or "")})
        run_row = ev.get("run") or {}
        no_pending = not ev.get("pending_checkpoint")
        check("V2-5", "checkpoint tile: honest error, run settled, NO pending "
                      "approval left behind",
              "checkpoint" in str(tile.get("error", "")).lower()
              and run_id and no_pending
              and run_row.get("status") not in ("waiting", "running"),
              f"error={str(tile.get('error'))[:120]} run_status="
              f"{run_row.get('status')} pending={not no_pending}")
        views_store.delete("pack20-cpview", 1, [], 3, "user")

    # V2-6: contract violation — no JSON on the last line
    aid6, err6 = _mk_automation(
        "pack20_viewnojson", 'print("just words, no JSON here")\n')
    if err6:
        check("V2-6", "tile contract violation", False, err6)
    else:
        _tile_view("pack20-nojson", aid6, "pack20_viewnojson")
        r = _run_view_api(token, "pack20-nojson", "user")
        tile = (r.json().get("tiles") or [{}])[0] if r.status_code == 200 else {}
        check("V2-6", "tile contract violation: honest per-tile error naming "
                      "the contract",
              r.status_code == 200
              and "stdout line must be JSON" in str(tile.get("error", "")),
              f"tile={json.dumps(tile)[:200]}")
        views_store.delete("pack20-nojson", 1, [], 3, "user")

    precleanup(["pack20_viewtile", "pack20_viewcp", "pack20_viewnojson"])

    # V2-7 migration: a v1-shape DB (UNIQUE name, no ns column) migrates,
    # legacy rows become tenant scope. Runs against a TEMP db via monkeypatch.
    import tempfile
    _orig_db = views_store.DB_PATH
    try:
        tmp = os.path.join(tempfile.gettempdir(), "pack20_views_v1.db")
        if os.path.exists(tmp):
            os.remove(tmp)
        import sqlite3 as _sq
        c = _sq.connect(tmp)
        c.executescript("""
        CREATE TABLE views (
            view_id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '', owner_user INTEGER,
            tiles TEXT NOT NULL, prev_tiles TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);""")
        c.execute("INSERT INTO views VALUES ('legacy1','old view','',1,"
                  "'[{\"title\":\"t\",\"connection\":\"ERPDB\","
                  "\"sql\":\"SELECT 1\"}]',NULL,3,'2026-08-01','2026-08-01')")
        c.commit(); c.close()
        views_store.DB_PATH = tmp
        views_store.init()
        migrated = views_store.get("old view", 2, [], "")  # visible to anyone => tenant
        check("V2-7", "v1->v2 migration: legacy rows land in tenant scope, "
                      "version preserved",
              migrated is not None and migrated.get("scope") == "tenant"
              and migrated.get("version") == 3,
              f"migrated={json.dumps({k: migrated.get(k) for k in ('scope', 'version', 'ns')}) if migrated else None}")
    except Exception as e:
        check("V2-7", "v1->v2 migration", False, e)
    finally:
        views_store.DB_PATH = _orig_db

    # ------------------------------------------------------------------
    # V2.1 — per-tile refresh, cache merge, scheduled cache refresh
    # ------------------------------------------------------------------

    # V21-1 tile_index runs ONE tile and merges the cache per-slot
    views_store.delete("pack20-two", 1, [], 3, "user")
    try:
        views_store.save("pack20-two", "v2.1 tile-index probe", [
            {"title": "One", "connection": "ERPDB", "sql": "SELECT 1 AS a",
             "viz": "stat"},
            {"title": "Two", "connection": "ERPDB", "sql": "SELECT 2 AS b",
             "viz": "stat", "refresh_seconds": 30},
        ], 1, scope="user")
        r = requests.post(f"{BASE}/api/views/run",
                          json={"name": "pack20-two", "scope": "user",
                                "tile_index": 1},
                          headers={"Authorization": f"Bearer {token}"},
                          timeout=60)
        d = r.json() if r.status_code == 200 else {}
        tiles = d.get("tiles") or []
        only_second = (len(tiles) == 1 and tiles[0].get("index") == 1
                       and tiles[0].get("rows") == [["2"]]
                       and tiles[0].get("refresh_seconds") == 30)
        after = views_store.get("pack20-two", 1, [], "user")
        cache = after.get("tile_cache") or []
        merged = (len(cache) == 2 and not cache[0]
                  and (cache[1] or {}).get("rows") == [["2"]])
        check("V21-1", "tile_index runs a single tile; cache merges per-slot",
              only_second and merged,
              f"tiles={json.dumps(tiles)[:150]} cache_slots="
              f"{[bool(c) for c in cache]}")
    except Exception as e:
        check("V21-1", "tile_index single-tile run", False, e)

    # V21-2 refresh-cache endpoint (service key, stored principal)
    try:
        r = requests.post(f"{BASE}/api/views/refresh-cache",
                          json={"name": "pack20-two", "scope": "user",
                                "user_id": 1, "role": 3,
                                "username": "pack20-runner"},
                          headers=SVC_HEADERS, timeout=120)
        d = r.json() if r.status_code == 200 else {}
        after = views_store.get("pack20-two", 1, [], "user")
        cache = after.get("tile_cache") or []
        both = (len(cache) == 2 and (cache[0] or {}).get("rows") == [["1"]]
                and (cache[1] or {}).get("rows") == [["2"]])
        r_bad = requests.post(f"{BASE}/api/views/refresh-cache",
                              json={"name": "pack20-two", "scope": "user",
                                    "user_id": 2, "role": 2,
                                    "username": "someone-else"},
                              headers=SVC_HEADERS, timeout=60)
        check("V21-2", "refresh-cache: service-key headless refresh fills every "
                       "slot; wrong principal cannot reach the view",
              r.status_code == 200 and d.get("ok") and d.get("tiles_ok") == 2
              and both and r_bad.status_code == 404,
              f"http={r.status_code} resp={json.dumps(d)[:120]} both={both} "
              f"wrong_principal={r_bad.status_code}")
    except Exception as e:
        check("V21-2", "refresh-cache endpoint", False, e)

    # V21-3 JSS view_refresh livefire: interval job fires, cached_at advances
    try:
        before_at = (views_store.get("pack20-two", 1, [], "user") or {}).get(
            "cached_at") or ""
        vr_body = {"name": "View refresh: pack20-two", "type": "view_refresh",
                   "target_id": "0", "created_by": "pack20", "is_active": True,
                   "parameters": {
                       "view_name": {"value": "pack20-two", "type": "string"},
                       "view_scope": {"value": "user", "type": "string"},
                       "view_group_id": {"value": "0", "type": "string"},
                       "user_id": {"value": "1", "type": "string"},
                       "role": {"value": "3", "type": "string"},
                       "username": {"value": "pack20-runner", "type": "string"}},
                   "schedule": {"type": "interval", "interval_seconds": 45,
                                "start_date": datetime.datetime.utcnow().strftime(
                                    "%Y-%m-%d %H:%M:%S")}}
        jr = requests.post(f"{MAIN}/api/scheduler/jobs", json=vr_body,
                           headers=SVC_HEADERS, timeout=30)
        vjob = (jr.json() if jr.status_code < 500 else {}).get("id")
        advanced = False
        if vjob:
            deadline = _t.time() + 240
            while _t.time() < deadline and not advanced:
                _t.sleep(10)
                now_at = (views_store.get("pack20-two", 1, [], "user") or {}
                          ).get("cached_at") or ""
                advanced = bool(now_at and now_at > before_at)
            requests.delete(f"{MAIN}/api/scheduler/jobs/{vjob}",
                            headers=SVC_HEADERS, timeout=30)
        check("V21-3", "JSS view_refresh job fires; the shared cache advances "
                       "with zero AI",
              bool(vjob) and advanced,
              f"job={vjob} before={before_at!r} advanced={advanced}")
    except Exception as e:
        check("V21-3", "JSS view_refresh livefire", False, e)
    finally:
        views_store.delete("pack20-two", 1, [], 3, "user")

    # ------------------------------------------------------------------
    # A6 — Agent Email: per-user addresses on the legacy cloud feed
    # ------------------------------------------------------------------
    import asyncio as _aio
    import email_store
    import email_poller

    email_store.init()

    # Test isolation: these checks reuse fixed event ids (990001/2), but the
    # dedupe ledger + FYI items persist across runs — clear our own debris so
    # a rerun starts clean (mirrors precleanup for automations).
    import sqlite3 as _sql3
    _ec = _sql3.connect(email_store.DB_PATH)
    _ec.execute("DELETE FROM processed_emails WHERE event_id IN (990001, 990002)")
    _ec.commit()
    _ec.close()
    for _it in workitem_store.list_items(77, include_closed=True):
        if (_it.get("payload") or {}).get("event_id") in (990001, 990002):
            workitem_store.respond(_it["work_item_id"], 77,
                                   {"decision": "acknowledged"})
            _wc = _sql3.connect(email_store.DB_PATH)
            _wc.execute("DELETE FROM work_items WHERE work_item_id = ?",
                        (_it["work_item_id"],))
            _wc.commit()
            _wc.close()

    def _tok(uid, role=2):
        return shared_auth.sign_cc_token({
            "user_id": uid, "role": role, "tenant_id": os.getenv("TENANT_ID", ""),
            "username": f"pack20-u{uid}", "name": f"Pack20 U{uid}"})

    tok77 = _tok(77)
    email_store.delete_address(77)
    email_store.delete_address(78)

    # A6-1 provisioning: compose matches the LIVE cloud suffix; dot prefix
    # rejected; duplicate address by another user rejected at the DB.
    try:
        ti = requests.get(f"{os.getenv('AI_HUB_API_URL', '').rstrip('/')}"
                          f"/api/email/tenant-id",
                          headers={"X-API-Key": os.getenv("API_KEY", "")},
                          timeout=20).json()
        # Messy prefix normalizes (James's rule: fix, don't reject):
        # spaces/case/punct -> clean hyphenated prefix.
        r_messy = requests.post(f"{BASE}/api/email/address",
                                json={"prefix": "Pack 20.A6!", "enabled": True},
                                headers={"Authorization": f"Bearer {tok77}"},
                                timeout=30)
        messy = (r_messy.json().get("address") or {}) \
            if r_messy.status_code == 200 else {}
        messy_expected = f"pack-20a6-agent.{ti.get('tenant_id')}@{ti.get('domain')}"
        r = requests.post(f"{BASE}/api/email/address",
                          json={"prefix": "pack20a6", "enabled": True},
                          headers={"Authorization": f"Bearer {tok77}"},
                          timeout=30)
        addr = (r.json().get("address") or {}) if r.status_code == 200 else {}
        expected = f"pack20a6-agent.{ti.get('tenant_id')}@{ti.get('domain')}"
        rb = requests.get(f"{BASE}/api/email/address",
                          headers={"Authorization": f"Bearer {tok77}"},
                          timeout=30).json()
        r_bad = requests.post(f"{BASE}/api/email/address",
                              json={"prefix": "..!!.."},
                              headers={"Authorization": f"Bearer {tok77}"},
                              timeout=30)
        r_dup = requests.post(f"{BASE}/api/email/address",
                              json={"prefix": "pack20a6"},
                              headers={"Authorization": f"Bearer {_tok(78)}"},
                              timeout=30)
        default_ok = (rb.get("default_prefix") == "pack20-u77")
        check("A6-1", "address provisioning: messy prefix normalizes; suffix "
                      "compose + readback; unsanitizable 400; duplicate 409; "
                      "default = username",
              r_messy.status_code == 200
              and messy.get("email_address") == messy_expected
              and r.status_code == 200 and addr.get("email_address") == expected
              and (rb.get("address") or {}).get("email_address") == expected
              and r_bad.status_code == 400 and r_dup.status_code == 409
              and default_ok,
              f"messy={messy.get('email_address')} addr={addr.get('email_address')} "
              f"bad={r_bad.status_code} dup={r_dup.status_code} "
              f"default={rb.get('default_prefix')}")
    except Exception as e:
        check("A6-1", "address provisioning", False, e)

    # A6-2 poller honesty (offline, injected brain): process / dedupe /
    # self-loop guard — no real model call, no cloud call.
    try:
        pack_addr = f"pack20a6-agent.{ti.get('tenant_id')}@{ti.get('domain')}"
        owner = email_store.get_address(77)
        calls = {"n": 0}

        async def fake_run_turn(prompt, session_id, user_ctx, tool_scope="full"):
            calls["n"] += 1
            yield {"type": "text", "text": "Handled the email (pack fake)."}
            yield {"type": "result", "ok": True, "subtype": "success",
                   "session_id": "pack-fake"}

        ev1 = {"event_id": 990001, "recipient_email": pack_addr,
               "sender_email": "outsider@example.com",
               "subject": "pack20 inbound probe", "body_preview": "hello agent",
               "message_key": ""}
        o1 = _aio.run(email_poller.process_event(ev1, owner, {pack_addr},
                                                 fake_run_turn))
        o2 = _aio.run(email_poller.process_event(ev1, owner, {pack_addr},
                                                 fake_run_turn))
        ev_self = dict(ev1, event_id=990002, sender_email=pack_addr)
        o3 = _aio.run(email_poller.process_event(ev_self, owner, {pack_addr},
                                                 fake_run_turn))
        fyi = [i for i in workitem_store.list_items(77, include_closed=True)
               if (i.get("payload") or {}).get("event_id") == 990001]
        check("A6-2", "poller: processes once (FYI in owner's My Work), dedupes "
                      "repeats, skips self-mail without a brain call",
              o1 == "processed" and o2 == "skipped_duplicate"
              and o3 == "skipped_self" and calls["n"] == 1 and len(fyi) == 1,
              f"outcomes=({o1},{o2},{o3}) brain_calls={calls['n']} fyi={len(fyi)}")
    except Exception as e:
        check("A6-2", "poller processing honesty", False, e)

    # A6-3 reply approval: only owner/admin can approve; approved send goes
    # out through the real cloud transport (self-addressed — harmless loop
    # the poller's self-guard ignores); send-before-close on failure.
    try:
        item = workitem_store.create_item(
            "edit_and_return", "Send: pack20 A6 self-test",
            summary="pack probe", payload={
                "kind": "agent_email_reply", "to": [pack_addr],
                "subject": "pack20 A6 self-test",
                "body": "original draft body", "from_address": pack_addr,
                "from_user": 77},
            addressed_user=77, from_kind="agent_email", from_ref=pack_addr,
            created_by="pack20")
        iid = item["work_item_id"]
        r_other = requests.post(f"{BASE}/api/work/respond",
                                json={"id": iid, "response": {
                                    "decision": "answered", "text": "x"}},
                                headers={"Authorization": f"Bearer {_tok(2)}"},
                                timeout=30)
        still_open = (workitem_store.get_item(iid) or {}).get("status") in (
            "open", "claimed")
        r_owner = requests.post(f"{BASE}/api/work/respond",
                                json={"id": iid, "response": {
                                    "decision": "answered",
                                    "text": "edited final body (pack20)"}},
                                headers={"Authorization": f"Bearer {tok77}"},
                                timeout=60)
        closed = (workitem_store.get_item(iid) or {}).get("status") == "closed"
        check("A6-3", "reply approval: non-owner 403 (item stays open); owner "
                      "approval sends via the real cloud transport and closes",
              r_other.status_code == 403 and still_open
              and r_owner.status_code == 200 and closed,
              f"other={r_other.status_code} open_after={still_open} "
              f"owner={r_owner.status_code} closed={closed}")
    except Exception as e:
        check("A6-3", "reply approval + send", False, e)

    # A6-4 reserved-kind guard: raise_work_item cannot impersonate a reply
    try:
        import work_tools
        from platform_tools import CURRENT_USER as _CU
        _CU.set({"user_id": 77, "role": 2, "username": "pack20-u77"})
        res = _aio.run(work_tools.raise_work_item.handler({
            "verb": "edit_and_return", "title": "sneaky",
            "summary": "x",
            "payload_json": json.dumps({"kind": "agent_email_reply",
                                        "to": ["victim@example.com"],
                                        "subject": "s", "body": "b",
                                        "from_address": pack_addr,
                                        "from_user": 77})}))
        blocked = bool(res.get("is_error")) and "reserved" in str(res)[:400]
        check("A6-4", "reserved-kind guard: generic items cannot impersonate "
                      "an email reply",
              blocked, f"result={str(res)[:200]}")
    except Exception as e:
        check("A6-4", "reserved-kind guard", False, e)
    finally:
        email_store.delete_address(77)
        email_store.delete_address(78)

    # A5-4 secrets seam (feedback #1): service-key store -> visible in list,
    # value never echoed anywhere.
    r = requests.post(f"{MAIN}/workflow/secrets/store",
                      json={"name": "PACK20_TEST_SECRET", "value": "pack20-value",
                            "description": "pack 20 probe (safe to delete)"},
                      headers=SVC_HEADERS, timeout=30)
    sr = r.json() if r.status_code < 400 else {}
    rl = requests.get(f"{MAIN}/workflow/secrets/list", headers=SVC_HEADERS,
                      timeout=30)
    names = {s.get("name") for s in (rl.json().get("secrets") or [])} \
        if rl.status_code < 400 else set()
    check("A5-4", "secret store seam: X-API-Key write lands in the store, "
                  "response never echoes the value",
          sr.get("success") is True and "PACK20_TEST_SECRET" in names
          and "pack20-value" not in json.dumps(sr),
          f"store={json.dumps(sr)[:200]} listed={'PACK20_TEST_SECRET' in names}")

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
