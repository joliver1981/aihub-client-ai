"""
AIHUB-0027 - Automations core lifecycle end-to-end test.
Covers: create, save code+manifest, dry-run, promote, run, history,
secret scan, honest outcomes (success/failed/unverified), pinned-version,
dashboard, role gating.
"""
import requests, re, json, time, os

BASE = "http://localhost:5001"
API_KEY = os.getenv("API_KEY", "DB27D555-03A8-446E-9C23-8DAAA95EAD21")
results = []

def log(msg):
    print(msg, flush=True)

def record(name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    results.append({"test": name, "passed": passed, "detail": detail})
    log(f"  [{status}] {name}" + (f" - {detail}" if detail else ""))

def make_session(username="admin", password="admin"):
    s = requests.Session()
    r = s.get(f"{BASE}/login")
    m = re.search(r'csrf_token.*?value="([^"]+)"', r.text)
    csrf = m.group(1) if m else ''
    r = s.post(f"{BASE}/login", data={'csrf_token': csrf, 'username': username, 'password': password, 'submit': 'Login'})
    return s

def jpost(s, path, **kw):
    r = s.post(f"{BASE}{path}", **kw)
    try: return r.status_code, r.json()
    except: return r.status_code, {"_raw": r.text[:500]}

def jget(s, path, **kw):
    r = s.get(f"{BASE}{path}", **kw)
    try: return r.status_code, r.json()
    except: return r.status_code, {"_raw": r.text[:500]}

def jput(s, path, **kw):
    r = s.put(f"{BASE}{path}", **kw)
    try: return r.status_code, r.json()
    except: return r.status_code, {"_raw": r.text[:500]}

def jdel(s, path, **kw):
    r = s.delete(f"{BASE}{path}", **kw)
    try: return r.status_code, r.json()
    except: return r.status_code, {"_raw": r.text[:500]}

CODE_SUCCESS = '''import csv, os
out_path = os.path.join(os.getcwd(), "report.csv")
with open(out_path, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["id", "total"])
    w.writerow([1, 100])
    w.writerow([2, 200])
    w.writerow([3, 300])
print(f"Wrote {out_path}")
'''

CODE_LIAR = '''print("I claim to write a CSV but I do not.")
'''

CODE_SECRET = '''PWD = "super_secret_password_123"
print("bad code")
'''

MANIFEST = {"name": "test-0027-core", "outputs": [{"kind": "file", "path": "report.csv", "min_rows": 2}]}

def run_tests():
    log("=" * 60)
    log("AIHUB-0027 - Automations Core Lifecycle E2E Test")
    log("=" * 60)

    s = make_session()
    assert len(s.cookies) > 0, "Login failed"

    # Clean up prior test automations
    _, body = jget(s, "/automations/api/list")
    for a in body.get("automations", []):
        if a.get("name", "").startswith("test-0027"):
            jdel(s, f"/automations/api/{a['automation_id']}")
            log(f"  Cleaned up {a['name']}")

    # 1. Create
    log("\n--- Test 1: Create automation ---")
    code, body = jpost(s, "/automations/api/create",
                       json={"name": "test-0027-core", "description": "E2E test", "provision_environment": False})
    auto_id = body.get("automation", {}).get("automation_id")
    record("Create automation (201)", code == 201, f"status={code}, id={auto_id}")
    if not auto_id:
        log(f"FATAL: {body}")
        return

    # 2. Save code + manifest
    log("\n--- Test 2: Save code v1 ---")
    code, body = jput(s, f"/automations/api/{auto_id}/code", json={"code": CODE_SUCCESS, "manifest": MANIFEST})
    v1 = body.get("version")
    record("Save code v1", code == 200, f"version={v1}")

    # 3. Dry-run (success)
    log("\n--- Test 3: Dry-run v1 ---")
    code, body = jpost(s, f"/automations/api/{auto_id}/run", json={"dry_run": True, "wait": True})
    record("Dry-run success", body.get("status") == "success",
           f"status={body.get('status')}, verify={json.dumps(body.get('verify_report', []))[:200]}")

    # 4. Promote
    log("\n--- Test 4: Promote v1 ---")
    code, body = jpost(s, f"/automations/api/{auto_id}/promote", json={})
    record("Promote v1", code == 200, f"pinned={body.get('pinned_version')}")

    # 5. Run promoted v1
    log("\n--- Test 5: Run promoted v1 ---")
    code, body = jpost(s, f"/automations/api/{auto_id}/run", json={"wait": True})
    record("Run v1 success", body.get("status") == "success", f"status={body.get('status')}")

    # 6. Run history
    log("\n--- Test 6: Run history ---")
    code, body = jget(s, f"/automations/api/{auto_id}/runs")
    runs = body.get("runs", [])
    record("Run history has entries", len(runs) >= 1, f"runs={len(runs)}")

    # 7. Get run log
    log("\n--- Test 7: Get run log ---")
    if runs:
        rid = runs[0].get("run_id")
        code, body = jget(s, f"/automations/api/runs/{rid}/log")
        record("Run log retrieved", code == 200 and bool(body.get("log")), f"log_len={len(body.get('log', ''))}")

    # 8. Secret scan rejection
    log("\n--- Test 8: Secret scan ---")
    code, body = jput(s, f"/automations/api/{auto_id}/code", json={"code": CODE_SECRET, "manifest": MANIFEST})
    record("Secret scan rejects password literal", code == 400, f"error={body.get('error', '')[:100]}")

    # 9. Honest 'failed' outcome (liar script)
    log("\n--- Test 9: Liar script -> failed ---")
    code, body = jput(s, f"/automations/api/{auto_id}/code", json={"code": CODE_LIAR, "manifest": MANIFEST})
    liar_v = body.get("version")
    code, body = jpost(s, f"/automations/api/{auto_id}/run", json={"dry_run": True, "wait": True})
    record("Liar script -> failed", body.get("status") == "failed", f"status={body.get('status')}")

    # 10. SFTP upload (without remote verify - just file output verify)
    log("\n--- Test 10: SFTP upload -> success ---")
    SFTP_CODE = '''import csv, os, paramiko
out_path = os.path.join(os.getcwd(), "report.csv")
with open(out_path, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["id", "total"])
    w.writerow([1, 100])
transport = paramiko.Transport(("127.0.0.1", 2222))
transport.connect(username="testuser", password="testpass")
sftp = paramiko.SFTPClient.from_transport(transport)
sftp.put(out_path, "/incoming/automation_report.csv")
sftp.close()
transport.close()
print("Uploaded to SFTP")
'''
    SFTP_MAN = {"name": "test-0027-sftp", "outputs": [{"kind": "file", "path": "report.csv", "min_rows": 1}]}
    code, body = jpost(s, "/automations/api/create",
                       json={"name": "test-0027-sftp", "description": "SFTP test", "provision_environment": False})
    sftp_id = body.get("automation", {}).get("automation_id")
    if sftp_id:
        jput(s, f"/automations/api/{sftp_id}/code", json={"code": SFTP_CODE, "manifest": SFTP_MAN})
        code, body = jpost(s, f"/automations/api/{sftp_id}/run", json={"dry_run": True, "wait": True})
        record("SFTP upload + file verify -> success", body.get("status") == "success",
               f"status={body.get('status')}")
    else:
        record("SFTP upload -> success", False, "could not create automation")

    # 11. Missing SFTP upload -> failed
    log("\n--- Test 11: Missing SFTP upload -> failed ---")
    BAD_CODE = '''import csv, os
out_path = os.path.join(os.getcwd(), "report.csv")
with open(out_path, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow([1, 100])
# Claim SFTP but don't do it
'''
    BAD_MAN = {"name": "test-0027-sftp-bad",
               "outputs": [{"kind": "file", "path": "report.csv", "min_rows": 1},
                           {"kind": "sftp_upload", "remote_dir": "/incoming"}]}
    code, body = jpost(s, "/automations/api/create",
                       json={"name": "test-0027-sftp-bad", "description": "bad SFTP", "provision_environment": False})
    bad_id = body.get("automation", {}).get("automation_id")
    if bad_id:
        jput(s, f"/automations/api/{bad_id}/code", json={"code": BAD_CODE, "manifest": BAD_MAN})
        code, body = jpost(s, f"/automations/api/{bad_id}/run", json={"dry_run": True, "wait": True})
        # sftp_upload with no verify -> unverified (no way to check), file -> success
        # Overall should be unverified (sftp output not checked but present as unverified)
        record("Missing SFTP -> unverified or failed", body.get("status") in ("unverified", "failed"),
               f"status={body.get('status')}")
    else:
        record("Missing SFTP -> failed", False, "could not create automation")

    # 12. Pinned-version discipline
    log("\n--- Test 12: Pinned-version discipline ---")
    V2_CODE = CODE_SUCCESS.replace("100", "999")
    code, body = jput(s, f"/automations/api/{auto_id}/code", json={"code": V2_CODE, "manifest": MANIFEST})
    v2 = body.get("version")
    log(f"  Saved v{v2} without promoting (pinned is still v{v1})")

    # Normal run -> should use v1 (pinned)
    jpost(s, f"/automations/api/{auto_id}/run", json={"wait": True})
    _, body = jget(s, f"/automations/api/{auto_id}/runs")
    last_run = body.get("runs", [{}])[0] if body.get("runs") else {}
    record("Normal run uses pinned v1", last_run.get("version") == v1, f"version={last_run.get('version')}")

    # Dry-run -> should use v2 (latest)
    jpost(s, f"/automations/api/{auto_id}/run", json={"dry_run": True, "wait": True})
    _, body = jget(s, f"/automations/api/{auto_id}/runs")
    last_run = body.get("runs", [{}])[0] if body.get("runs") else {}
    record("Dry-run uses latest v2", last_run.get("version") == v2, f"version={last_run.get('version')}")

    # 13. Dashboard
    log("\n--- Test 13: Dashboard ---")
    r = s.get(f"{BASE}/automations/")
    record("Dashboard returns 200", r.status_code == 200, f"status={r.status_code}")
    record("Dashboard has content", "automations" in r.text.lower(), "")

    # 14. Skip-if-running
    log("\n--- Test 14: Skip-if-running ---")
    # Start a long-running async run, then immediately start another
    # Use a sleep-based code to ensure overlap
    SLEEP_CODE = '''import time, os
time.sleep(5)
out_path = os.path.join(os.getcwd(), "report.csv")
with open(out_path, "w") as f:
    f.write("data\\n")
'''
    code, body = jput(s, f"/automations/api/{auto_id}/code", json={"code": SLEEP_CODE, "manifest": MANIFEST})
    v3 = body.get("version")
    # Promote v3 so it can be run
    jpost(s, f"/automations/api/{auto_id}/promote", json={})
    # Start async run (no wait)
    code, body = jpost(s, f"/automations/api/{auto_id}/run", json={})
    first_run = body.get("run_id")
    log(f"  Started async run: {first_run}")
    # Immediately start another
    time.sleep(0.5)
    code, body = jpost(s, f"/automations/api/{auto_id}/run", json={"wait": True})
    # Wait for first run to finish
    time.sleep(6)
    _, body = jget(s, f"/automations/api/{auto_id}/runs")
    all_runs = body.get("runs", [])
    has_skipped = any(r.get("status") == "skipped" for r in all_runs)
    record("Skip-if-running produces 'skipped'", has_skipped,
           f"runs={len(all_runs)}, last3={[r.get('status') for r in all_runs[-3:]]}")

    # 15. Role-1 gating
    log("\n--- Test 15: Role-1 gating ---")
    r = requests.post(f"{BASE}/automations/api/internal/manage",
                      headers={"X-API-Key": API_KEY},
                      json={"action": "list", "user_context": {"user_id": 1, "role": 1, "username": "viewer"}, "payload": {}})
    record("Role-1 -> 403", r.status_code == 403, f"status={r.status_code}")

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

    with open("aihub_0027_results.json", "w") as f:
        json.dump({"results": results, "passed": passed, "failed": failed}, f, indent=2)
    log("\nResults written to aihub_0027_results.json")

if __name__ == "__main__":
    run_tests()
