import requests, re, json
BASE = "http://localhost:5001"
s = requests.Session()
r = s.get(f"{BASE}/login")
m = re.search(r'csrf_token.*?value="([^"]+)"', r.text)
csrf = m.group(1)
r = s.post(f"{BASE}/login", data={'csrf_token': csrf, 'username': 'admin', 'password': 'admin', 'submit': 'Login'})

# List automations
r = s.get(f'{BASE}/automations/api/list')
autos = r.json().get("automations", [])
for a in autos:
    if a.get("name") == "test-0031-checkpoint":
        auto_id = a["automation_id"]
        # Get runs
        r = s.get(f'{BASE}/automations/api/{auto_id}/runs')
        runs = r.json().get("runs", [])
        if runs:
            run_id = runs[0]["run_id"]
            print(f"Run: {run_id}, status: {runs[0]['status']}")
            # Get log
            r = s.get(f'{BASE}/automations/api/runs/{run_id}/log')
            log = r.json().get("log", "")
            print(f"\nLog:\n{log[:2000]}")
        break
