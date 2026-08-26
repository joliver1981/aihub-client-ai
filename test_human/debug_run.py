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
    if a.get("name") == "test-0027-core":
        auto_id = a["automation_id"]
        # Run and see the full response
        r = s.post(f'{BASE}/automations/api/{auto_id}/run', json={"wait": True})
        print(f"Run response ({r.status_code}):")
        print(json.dumps(r.json(), indent=2))
        
        # Also check runs list
        r = s.get(f'{BASE}/automations/api/{auto_id}/runs')
        print(f"\nRuns list:")
        for run in r.json().get("runs", [])[:3]:
            print(json.dumps(run, indent=2))
        break
