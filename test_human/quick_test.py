import requests, re, json, time
BASE = "http://localhost:5001"
s = requests.Session()
r = s.get(f"{BASE}/login")
m = re.search(r'csrf_token.*?value="([^"]+)"', r.text)
csrf = m.group(1)
r = s.post(f"{BASE}/login", data={'csrf_token': csrf, 'username': 'admin', 'password': 'admin', 'submit': 'Login'})
print(f'Login: {r.status_code}, cookies: {len(s.cookies)}')

# List existing
r = s.get(f'{BASE}/automations/api/list')
data = r.json()
print(f'Existing automations: {json.dumps(data, indent=2)[:500]}')

# Create with unique name
r = s.post(f'{BASE}/automations/api/create', json={'name': 'test-0027-core-2', 'description': 'E2E test', 'provision_environment': False})
print(f'Create: {r.status_code}')
print(r.text[:500])
