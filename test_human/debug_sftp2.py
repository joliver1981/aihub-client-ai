import requests, re, json
BASE = "http://localhost:5001"
s = requests.Session()
r = s.get(f"{BASE}/login")
m = re.search(r'csrf_token.*?value="([^"]+)"', r.text)
csrf = m.group(1)
r = s.post(f"{BASE}/login", data={'csrf_token': csrf, 'username': 'admin', 'password': 'admin', 'submit': 'Login'})

r = s.get(f'{BASE}/automations/api/list')
autos = r.json().get("automations", [])
for a in autos:
    if a.get("name") == "test-0027-sftp":
        auto_id = a["automation_id"]
        r = s.post(f'{BASE}/automations/api/{auto_id}/run', json={"dry_run": True, "wait": True})
        print(f"Status: {r.status_code}")
        print(json.dumps(r.json(), indent=2))
        # Check log
        runs = s.get(f'{BASE}/automations/api/{auto_id}/runs').json().get("runs", [])
        if runs:
            log = s.get(f'{BASE}/automations/api/runs/{runs[0]["run_id"]}/log').json()
            print(f"\nLog:\n{log.get('log', '')[:2000]}")
        break
