import requests, re, json
BASE = "http://localhost:5001"
s = requests.Session()
r = s.get(f"{BASE}/login")
m = re.search(r'csrf_token.*?value="([^"]+)"', r.text)
csrf = m.group(1)
r = s.post(f"{BASE}/login", data={'csrf_token': csrf, 'username': 'admin', 'password': 'admin', 'submit': 'Login'})

# List existing
r = s.get(f'{BASE}/automations/api/list')
autos = r.json().get("automations", [])
test_auto = None
for a in autos:
    if a.get("name", "").startswith("test-0027"):
        test_auto = a
        break

if not test_auto:
    r = s.post(f'{BASE}/automations/api/create', json={'name': 'test-0027-debug', 'description': 'debug', 'provision_environment': False})
    test_auto = r.json().get("automation", {})

auto_id = test_auto["automation_id"]
print(f"Using automation: {auto_id}")

# Try saving code with manifest
code = 'import csv, os\nout_dir = os.environ.get("AIHUB_RUN_DIR", "/tmp")\nout_path = os.path.join(out_dir, "report.csv")\nwith open(out_path, "w", newline="") as f:\n    w = csv.writer(f)\n    w.writerow(["id", "total"])\n    w.writerow([1, 100])\nprint(f"Wrote {out_path}")\n'

manifest = {"outputs": [{"type": "file", "path": "report.csv", "min_rows": 2}]}
r = s.put(f'{BASE}/automations/api/{auto_id}/code', json={"code": code, "manifest": manifest})
print(f"Save with manifest: {r.status_code}")
print(r.text[:500])

# Try saving code without manifest
r = s.put(f'{BASE}/automations/api/{auto_id}/code', json={"code": code})
print(f"\nSave without manifest: {r.status_code}")
print(r.text[:500])

# Try saving just manifest
r = s.put(f'{BASE}/automations/api/{auto_id}/code', json={"code": code, "manifest": {"outputs": [{"type": "file", "path": "report.csv", "min_rows": 2}]}})
print(f"\nSave with explicit manifest: {r.status_code}")
print(r.text[:500])
