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
for a in autos:
    if a.get("name", "").startswith("test-0027"):
        auto_id = a["automation_id"]
        print(f"Using: {a['name']} ({auto_id})")
        
        # Try SFTP manifest
        sftp_manifest = {
            "name": "test-0027-sftp",
            "outputs": [
                {"kind": "file", "path": "report.csv", "min_rows": 1},
                {"kind": "sftp_upload", "remote_path": "/incoming/automation_report.csv",
                 "verify": {"remote_listing": "/incoming/automation_report.csv"}},
            ],
        }
        code = 'print("hello")'
        r = s.put(f'{BASE}/automations/api/{auto_id}/code', json={"code": code, "manifest": sftp_manifest})
        print(f"SFTP manifest save: {r.status_code}")
        print(r.text[:300])
        break
