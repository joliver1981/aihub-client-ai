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
    if a.get("name", "").startswith("test-0030"):
        print(f"{a['name']}: pinned={a.get('pinned_version')}, current={a.get('current_version')}")

# Try install with name_suffix
build_body = {
    "name": "test-0030-solution",
    "version": "1.0.0",
    "description": "Test solution with automation",
    "selections": {"automation_ids": [a["automation_id"] for a in autos if a.get("name") == "test-0030-export"]},
}
install_body = {**build_body, "name_suffix": "_installed"}
r = s.post(f"{BASE}/api/solutions/test_install", json=install_body, timeout=60)
print(f"\nInstall status: {r.status_code}")
print(f"Install body: {r.text[:1000]}")

# List again
r = s.get(f'{BASE}/automations/api/list')
autos2 = r.json().get("automations", [])
for a in autos2:
    if a.get("name", "").startswith("test-0030"):
        print(f"{a['name']}: pinned={a.get('pinned_version')}, current={a.get('current_version')}")
