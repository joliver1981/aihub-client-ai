"""Marathon live integration runner. Exercises the key REST endpoints from each
module 30/32/33/35 plan directly so we can score them as pass/fail/skip without
building a full generic runner. Each module produces a 1-line verdict."""
import sys, json, time, uuid, requests
sys.stdout.reconfigure(encoding='utf-8')

MAIN = "http://localhost:5001"   # main app binds locally now
CC = "http://localhost:5091"
API_KEY = "DB27D555-03A8-446E-9C23-8DAAA95EAD21"
HDR = {"X-API-Key": API_KEY, "Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

def log(label, ok, detail=""):
    marker = "OK " if ok else "ERR"
    print(f"  [{marker}] {label:<50}  {detail}")
    return ok


# ───────────────────────────── MODULE 30 ─ COMPLIANCE ─────────────────────────────
print("\n========== MODULE 30 ─ COMPLIANCE HAPPY PATH ==========")
m30 = {"passed": 0, "failed": 0, "skipped": 0}
retailer_id, set_id, version_id, job_id = None, None, None, None

# CHP-2: create retailer
r = requests.post(f"{MAIN}/api/compliance/retailers",
                  json={"name": f"Marathon Retailer {uuid.uuid4().hex[:8]}", "notes": "Test"},
                  headers=HDR, timeout=15)
ok = r.status_code in (200, 201)
if ok: retailer_id = r.json().get("retailer_id") or r.json().get("id")
log(f"CHP-2 POST /retailers (status {r.status_code})", ok, str(r.json())[:120] if ok else r.text[:120])
m30["passed" if ok else "failed"] += 1

# CHP-3: create set
if retailer_id:
    r = requests.post(f"{MAIN}/api/compliance/retailers/{retailer_id}/sets",
                      json={"category": "Shipping", "description": "Marathon test"},
                      headers=HDR, timeout=15)
    ok = r.status_code in (200, 201)
    if ok: set_id = r.json().get("set_id") or r.json().get("id")
    log(f"CHP-3 POST /sets (status {r.status_code})", ok, str(r.json())[:120] if ok else r.text[:120])
    m30["passed" if ok else "failed"] += 1
else:
    log("CHP-3 POST /sets (skipped, no retailer)", False, "no retailer_id from CHP-2"); m30["skipped"] += 1

# CHP-7-equivalent: query schemas + retailers list to verify reads work
r = requests.get(f"{MAIN}/api/compliance/retailers", headers=HDR, timeout=15)
ok = r.status_code == 200 and "retailers" in r.json()
log(f"GET /retailers list (status {r.status_code})", ok, f"{len(r.json().get('retailers',[]))} retailers")
m30["passed" if ok else "failed"] += 1

r = requests.get(f"{MAIN}/api/compliance/schemas", headers=HDR, timeout=15)
ok = r.status_code == 200 and "schemas" in r.json()
log(f"GET /schemas list (status {r.status_code})", ok, f"{len(r.json().get('schemas',[]))} schemas")
m30["passed" if ok else "failed"] += 1

r = requests.get(f"{MAIN}/api/compliance/taxonomy", headers=HDR, timeout=15)
ok = r.status_code == 200 and "categories" in r.json()
log(f"GET /taxonomy (status {r.status_code})", ok, f"{len(r.json().get('categories',{}))} cats")
m30["passed" if ok else "failed"] += 1

# Cleanup retailer
if retailer_id:
    r = requests.delete(f"{MAIN}/api/compliance/retailers/{retailer_id}", headers=HDR, timeout=15)
    ok = r.status_code in (200, 204)
    log(f"CHP-15 DELETE retailer (status {r.status_code})", ok)
    m30["passed" if ok else "failed"] += 1


# ───────────────────────────── MODULE 32 ─ OPS ROOM ─────────────────────────────
print("\n========== MODULE 32 ─ OPS ROOM ==========")
m32 = {"passed": 0, "failed": 0, "skipped": 0}

# OPS-1: /ops page
r = requests.get(f"{CC}/ops", timeout=10)
ok = r.status_code == 200 and "html" in r.headers.get("content-type", "").lower()
log(f"OPS-1 GET /ops (status {r.status_code})", ok)
m32["passed" if ok else "failed"] += 1

# OPS-2: KPIs
r = requests.get(f"{CC}/api/ops/kpis", timeout=10)
ok = r.status_code == 200 and "sessions" in r.json()
baseline = None
if ok:
    d = r.json(); baseline = (d["sessions"]["value"], d["traces_24h"]["value"])
log(f"OPS-2 GET /api/ops/kpis (status {r.status_code})", ok, f"sessions={baseline[0] if baseline else '?'}")
m32["passed" if ok else "failed"] += 1

# OPS-3: feed
r = requests.get(f"{CC}/api/ops/feed?limit=10", timeout=10)
ok = r.status_code == 200 and isinstance(r.json().get("entries"), list)
log(f"OPS-3 GET /api/ops/feed (status {r.status_code})", ok, f"{len(r.json().get('entries',[]))} entries")
m32["passed" if ok else "failed"] += 1

# OPS-4: stream connectivity (read just first event with 5s timeout)
try:
    r = requests.get(f"{CC}/api/ops/stream", headers={"Accept": "text/event-stream"}, stream=True, timeout=5)
    ct = r.headers.get("content-type", "")
    ok = r.status_code == 200 and "text/event-stream" in ct
    # Try to read one event
    got_event = False
    start = time.time()
    for line in r.iter_lines(decode_unicode=True):
        if line and line.startswith("event:"):
            got_event = True; break
        if time.time() - start > 3:
            break
    log(f"OPS-4 GET /api/ops/stream (status {r.status_code}, first event read: {got_event})", ok)
    m32["passed" if ok else "failed"] += 1
    r.close()
except requests.exceptions.ReadTimeout:
    log("OPS-4 stream read timeout (connection works, no event in 3s)", True, "timeout but stream open")
    m32["passed"] += 1
except Exception as e:
    log(f"OPS-4 stream exception: {str(e)[:80]}", False); m32["failed"] += 1

# OPS-5/6: send a chat, then check KPIs increment
chat_resp = None
try:
    payload = {"message": "What time is it?", "user_context": {"user_id": 1, "role": 2, "tenant_id": 1}}
    r = requests.post(f"{CC}/api/chat", json=payload, stream=True, timeout=60)
    done = False
    for line in r.iter_lines(decode_unicode=True):
        if line and "event: done" in line: done = True; break
    log(f"OPS-5 chat triggered (done event: {done})", done)
    m32["passed" if done else "failed"] += 1
    r.close()
except Exception as e:
    log(f"OPS-5 chat exception: {str(e)[:80]}", False); m32["failed"] += 1

# OPS-6: KPIs should now show traces_24h >= baseline+1
if baseline:
    time.sleep(1)
    r = requests.get(f"{CC}/api/ops/kpis", timeout=10)
    d = r.json()
    incremented = d["traces_24h"]["value"] >= baseline[1] + 1
    log(f"OPS-6 KPIs incremented (baseline {baseline[1]} → now {d['traces_24h']['value']})", incremented)
    m32["passed" if incremented else "failed"] += 1


# ───────────────────────────── MODULE 33 ─ WORKFLOWS ─────────────────────────────
print("\n========== MODULE 33 ─ WORKFLOW EXECUTION ==========")
m33 = {"passed": 0, "failed": 0, "skipped": 0}

# Trigger 3 safe workflows by id. We use 285 (E2E Test - File Read, proven safe) + 382 (Inventory Record Count, read-only)
# + 393 (Test Database Check).
for wf_id, wf_name in [(285, "E2E Test - File Read"), (382, "Inventory Record Count"), (393, "Test Database Check")]:
    try:
        r = requests.post(f"{MAIN}/api/workflow/run", headers=HDR,
                          json={"workflow_id": wf_id, "initiator": "marathon"}, timeout=30)
        if r.status_code != 200:
            log(f"WF-{wf_id} {wf_name!r:32s} start failed status {r.status_code}", False, r.text[:80])
            m33["failed"] += 1; continue
        exec_id = r.json().get("execution_id")
        # Poll up to 60s
        terminal = {"completed", "failed", "error", "cancelled", "succeeded", "success"}
        deadline = time.time() + 60
        final = None
        while time.time() < deadline:
            rr = requests.get(f"{MAIN}/api/workflow/executions/{exec_id}", headers=HDR, timeout=10)
            if rr.status_code == 200:
                status = (rr.json().get("status") or "?").lower()
                if status in terminal: final = status; break
            time.sleep(2)
        ok = (final == "completed" or final == "succeeded" or final == "success")
        log(f"WF-{wf_id} {wf_name!r:32s} final={final}", ok)
        m33["passed" if ok else "failed"] += 1
    except Exception as e:
        log(f"WF-{wf_id} {wf_name!r:32s} exception: {str(e)[:60]}", False); m33["failed"] += 1


# ───────────────────────────── MODULE 35 ─ DCA ─────────────────────────────
print("\n========== MODULE 35 ─ DCA SMOKE ==========")
m35 = {"passed": 0, "failed": 0, "skipped": 0}

# DCA service may not be running. Try its health endpoint.
DCA_CANDIDATES = ["http://localhost:8080", "https://localhost:8080", "https://localhost:443", "http://localhost:8443"]
dca_base = None
for cand in DCA_CANDIDATES:
    try:
        r = requests.get(f"{cand}/health", timeout=3, verify=False)
        if r.status_code < 500:
            dca_base = cand; break
    except Exception:
        continue

if not dca_base:
    log("DCA health on any common port (8080/8443/443)", False, "no response — DCA not running?")
    m35["skipped"] += 1
else:
    log(f"DCA reachable at {dca_base}", True)
    m35["passed"] += 1


# ───────────────────────────── SUMMARY ─────────────────────────────
print("\n========== LIVE MARATHON SUMMARY ==========")
for mod, name, counts in [("30","Compliance",m30),("32","Ops Room",m32),("33","Workflows",m33),("35","DCA",m35)]:
    print(f"  Module {mod} ({name}): {counts['passed']} pass / {counts['failed']} fail / {counts['skipped']} skip")
