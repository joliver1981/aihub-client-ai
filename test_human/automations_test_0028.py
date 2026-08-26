"""
AIHUB-0028 - Automations via Command Center conversational build flow.
Tests CC chat tool-selection: create automation, save code, dry-run gate,
promote, run, schedule, honest reporting, role gating.
"""
import requests, json, time, os, re

CC_URL = "http://localhost:5091"
MAIN_URL = "http://localhost:5001"
API_KEY = os.getenv("API_KEY", "DB27D555-03A8-446E-9C23-8DAAA95EAD21")
results = []

def log(msg):
    print(msg, flush=True)

def record(name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    results.append({"test": name, "passed": passed, "detail": detail})
    log(f"  [{status}] {name}" + (f" - {detail}" if detail else ""))

def get_cc_token():
    """Get a CC JWT token via the refresh-token endpoint."""
    r = requests.post(f"{CC_URL}/api/auth/refresh-token", json={"user_id": 13}, timeout=10)
    if r.status_code == 200:
        data = r.json()
        if data.get("token"):
            return data["token"]
    return None

def cc_chat(message, user_context=None, session_id=None, token=None):
    """Send a chat message to CC and collect the SSE response."""
    if user_context is None:
        user_context = {"user_id": 13, "role": 2, "tenant_id": 1, "username": "admin", "name": "Admin"}
    body = {"message": message, "user_context": user_context}
    if session_id:
        body["session_id"] = session_id
    if token:
        headers = {"Authorization": f"Bearer {token}"}
    else:
        headers = {}
    r = requests.post(f"{CC_URL}/api/chat", json=body, stream=True, timeout=120, headers=headers)
    full_response = ""
    current_event = ""
    for line in r.iter_lines(decode_unicode=True):
        if not line:
            current_event = ""
            continue
        if line.startswith("event: "):
            current_event = line[7:]
        elif line.startswith("data: "):
            data = line[6:]
            try:
                chunk = json.loads(data)
                if current_event == "response":
                    blocks = chunk.get("blocks", [])
                    for b in blocks:
                        if b.get("type") == "text":
                            full_response += b.get("content", "")
                elif current_event == "status":
                    phase = chunk.get("phase", "")
                    msg = chunk.get("message", "")
                    full_response += f"[STATUS: {phase} - {msg}] "
                elif current_event == "error":
                    full_response += f"[ERROR: {chunk.get('message', '')}]"
                elif current_event == "done":
                    break
            except json.JSONDecodeError:
                pass
    return full_response

def get_session_id(token=None):
    """Create a CC session and return its ID."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.post(f"{CC_URL}/api/sessions", params={"user_id": 13, "role": 2, "tenant_id": 1, "username": "admin", "name": "Admin"}, timeout=10, headers=headers)
    if r.status_code == 200:
        return r.json().get("session_id")
    return None

def run_tests():
    log("=" * 60)
    log("AIHUB-0028 - CC Conversational Build Flow Test")
    log("=" * 60)

    # Check CC is running
    try:
        r = requests.get(f"{CC_URL}/api/health", timeout=5)
        if r.status_code != 200:
            log(f"CC health check failed: {r.status_code}")
            return
        log("CC is healthy")
    except Exception as e:
        log(f"CC is not reachable: {e}")
        return

    # Get CC token
    token = get_cc_token()
    if not token:
        log("Could not get CC token")
        return
    log("Got CC token")

    # Create a session
    session_id = get_session_id(token)
    if not session_id:
        log("Could not create CC session")
        return
    log(f"Session: {session_id}")

    # 1. Ask CC to create an automation
    log("\n--- Test 1: CC creates an automation ---")
    resp = cc_chat("Please create a new automation called 'cc-test-0028' with the description 'CC build flow test'. Just create it, don't save code yet.", session_id=session_id, token=token)
    has_create = "creat" in resp.lower() or "automation" in resp.lower()
    record("CC creates automation", has_create, f"response_snippet={resp[:200]}")

    # 2. Ask CC to save code for the automation
    log("\n--- Test 2: CC saves automation code ---")
    code_request = """Please save the following code for the 'cc-test-0028' automation:

```python
import csv, os
out_path = os.path.join(os.getcwd(), "report.csv")
with open(out_path, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["id", "total"])
    w.writerow([1, 100])
    w.writerow([2, 200])
print(f"Wrote {out_path}")
```

And use this manifest:
```json
{"name": "cc-test-0028", "outputs": [{"kind": "file", "path": "report.csv", "min_rows": 2}]}
```
"""
    resp = cc_chat(code_request, session_id=session_id, token=token)
    has_save = "saved" in resp.lower() or "version" in resp.lower()
    record("CC saves code", has_save, f"response_snippet={resp[:200]}")

    # 3. Ask CC to dry-run
    log("\n--- Test 3: CC dry-runs with honest reporting ---")
    resp = cc_chat("Please dry-run the 'cc-test-0028' automation and show me the verified result.", session_id=session_id, token=token)
    has_dryrun = "dry" in resp.lower() or "success" in resp.lower() or "failed" in resp.lower()
    has_verify = "verif" in resp.lower() or "report" in resp.lower() or "file" in resp.lower()
    record("CC dry-run with verify report", has_dryrun and has_verify, f"response_snippet={resp[:300]}")

    # 4. Ask CC to promote
    log("\n--- Test 4: CC promotes automation ---")
    resp = cc_chat("The dry-run looks good. Please promote the automation.", session_id=session_id, token=token)
    has_promote = "promot" in resp.lower() or "pinned" in resp.lower() or "live" in resp.lower()
    record("CC promotes automation", has_promote, f"response_snippet={resp[:200]}")

    # 5. Ask CC to run it
    log("\n--- Test 5: CC runs the automation ---")
    resp = cc_chat("Please run the 'cc-test-0028' automation now.", session_id=session_id, token=token)
    has_run = "run" in resp.lower() or "success" in resp.lower() or "complete" in resp.lower()
    record("CC runs automation", has_run, f"response_snippet={resp[:200]}")

    # 6. Deliberately ask CC to save code with a hard-coded password
    log("\n--- Test 6: CC rejects hard-coded password ---")
    bad_code = """Please save this code for the 'cc-test-0028' automation:

```python
import csv, os
PWD = "my_secret_password_123"
out_path = os.path.join(os.getcwd(), "report.csv")
with open(out_path, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow([1, 100])
```
"""
    resp = cc_chat(bad_code, session_id=session_id, token=token)
    has_rejection = "reject" in resp.lower() or "error" in resp.lower() or "credential" in resp.lower() or "password" in resp.lower() or "secret" in resp.lower() or "denied" in resp.lower()
    record("CC reports password rejection honestly", has_rejection, f"response_snippet={resp[:300]}")

    # 7. Ask CC to list runs
    log("\n--- Test 7: CC lists automation runs ---")
    resp = cc_chat("Please show me the run history for the 'cc-test-0028' automation.", session_id=session_id, token=token)
    has_runs = "run" in resp.lower() and ("success" in resp.lower() or "status" in resp.lower() or "history" in resp.lower())
    record("CC lists automation runs", has_runs, f"response_snippet={resp[:200]}")

    # 8. Ask CC to schedule it
    log("\n--- Test 8: CC schedules automation ---")
    resp = cc_chat("Please schedule the 'cc-test-0028' automation to run daily at 2 AM.", session_id=session_id, token=token)
    has_schedule = "schedul" in resp.lower() or "job" in resp.lower() or "cron" in resp.lower()
    record("CC schedules automation", has_schedule, f"response_snippet={resp[:200]}")

    # 9. Role-1 user: automation tools should refuse
    log("\n--- Test 9: Role-1 user refused ---")
    role1_ctx = {"user_id": 1, "role": 1, "tenant_id": 1, "username": "viewer", "name": "Viewer"}
    session2 = get_session_id(token)
    resp = cc_chat("Please list all automations for me.", user_context=role1_ctx, session_id=session2, token=token)
    has_refuse = "developer" in resp.lower() or "role" in resp.lower() or "permission" in resp.lower() or "denied" in resp.lower() or "access" in resp.lower() or "unable" in resp.lower()
    record("Role-1 refused automation tools", has_refuse, f"response_snippet={resp[:300]}")

    # 10. Forged direct call to internal/manage with role=1
    log("\n--- Test 10: Forged role-1 internal/manage -> 403 ---")
    r = requests.post(f"{MAIN_URL}/automations/api/internal/manage",
                      headers={"X-API-Key": API_KEY},
                      json={"action": "list",
                            "user_context": {"user_id": 1, "role": 1, "username": "viewer"},
                            "payload": {}})
    record("Forged role-1 internal/manage -> 403", r.status_code == 403, f"status={r.status_code}")

    # Summary
    log("\n" + "=" * 60)
    passed = sum(1 for r in results if r["passed"])
    failed = sum(1 for r in results if not r["passed"])
    log(f"RESULTS: {passed} passed, {failed} failed, {len(results)} total")
    log("=" * 60)
    if failed:
        log("\nFAILED TESTS:")
        for r in results:
            if not r["passed"]:
                log(f"  X {r['test']} - {r['detail']}")

    with open("aihub_0028_results.json", "w") as f:
        json.dump({"results": results, "passed": passed, "failed": failed}, f, indent=2)

if __name__ == "__main__":
    run_tests()

