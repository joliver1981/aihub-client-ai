"""Clean up stale Stripe integrations, keep only the most recent working one."""
import requests

s = requests.Session()
s.post('http://localhost:5001/login', data={'username': 'admin', 'password': 'admin'}, allow_redirects=False)
# Get CSRF/session cookie
s.get('http://localhost:5001/dashboard')

# Generate the same internal API key the builder service uses
import sys, os, hashlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'builder_service'))
from builder_config import AI_HUB_API_KEY
print(f"Using API key: {AI_HUB_API_KEY[:20]}...")
headers = {'X-API-Key': AI_HUB_API_KEY}

r = s.get('http://localhost:5001/api/integrations', headers=headers)
print(f"List integrations: {r.status_code}")
data = r.json()
integrations = data.get('integrations', [])
print(f"Total integrations: {len(integrations)}")

# Find all Stripe integrations
stripe_integrations = []
for i in integrations:
    iid = i.get('integration_id', i.get('id'))
    name = i.get('integration_name', i.get('name', ''))
    tpl = i.get('template_key', '')
    connected = i.get('is_connected', False)
    print(f"  ID:{iid} Name:'{name}' Template:{tpl} Connected:{connected}")
    if 'stripe' in str(tpl).lower() or 'stripe' in str(name).lower():
        stripe_integrations.append({'id': iid, 'name': name, 'connected': connected})

print(f"\nStripe integrations: {len(stripe_integrations)}")

if len(stripe_integrations) <= 1:
    print("Only 0-1 Stripe integrations found, nothing to clean up.")
else:
    # Keep the last one (highest ID = most recent), delete the rest
    stripe_integrations.sort(key=lambda x: x['id'])
    keep = stripe_integrations[-1]
    to_delete = stripe_integrations[:-1]
    
    print(f"Keeping: ID {keep['id']} '{keep['name']}' (connected={keep['connected']})")
    print(f"Deleting: {len(to_delete)} stale integrations")
    
    for integ in to_delete:
        iid = integ['id']
        print(f"  Deleting ID {iid} '{integ['name']}'...", end=' ')
        r = s.delete(f'http://localhost:5001/api/integrations/{iid}', headers=headers)
        print(f"Status: {r.status_code} - {r.text[:100]}")

# Also clean up non-Stripe integrations that are disconnected/test
non_stripe = [i for i in integrations if 'stripe' not in str(i.get('template_key', '')).lower() and 'stripe' not in str(i.get('integration_name', i.get('name', ''))).lower()]
print(f"\nNon-Stripe integrations: {len(non_stripe)}")
for i in non_stripe:
    iid = i.get('integration_id', i.get('id'))
    name = i.get('integration_name', i.get('name', ''))
    print(f"  ID:{iid} Name:'{name}'")

print("\nDone!")
