import requests, json
s = requests.Session()
s.post('http://localhost:5001/login', data={'username':'admin','password':'admin'})
r = s.get('http://localhost:5001/api/integrations')
data = r.json()
print("Top-level keys:", list(data.keys()))
integrations = data.get('integrations', data.get('data', []))
print(f"Count: {len(integrations)}")
if integrations:
    print("First integration keys:", list(integrations[0].keys()))
    print("First integration:", json.dumps(integrations[0], indent=2, default=str)[:500])
