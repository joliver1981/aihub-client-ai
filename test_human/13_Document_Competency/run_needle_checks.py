"""NEEDLE checks against the scale agent: per-store fact questions graded on exact
ground-truth values (deposit / expiry). Verifies the v2 hybrid needle end-to-end."""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', '..')))

import requests
import config as cfg  # noqa: F401

BASE = os.getenv('DCT13_BASE_URL', 'http://127.0.0.1:5001')
API_KEY = os.getenv('AIHUB_API_KEY') or os.getenv('API_KEY') or 'DB27D555-03A8-446E-9C23-8DAAA95EAD21'

truth = json.load(open(os.path.join(HERE, 'scale_ground_truth.json'), encoding='utf-8'))
st = json.load(open(os.path.join(HERE, 'scale_state.json'), encoding='utf-8'))
agent_id = st['agent_id']

# Deterministic sample: first two landlord, first tenant, first split stores by id.
def first_of(cls, n=1):
    return sorted(s for s, t in truth.items() if t['hvac'] == cls)[:n]

samples = first_of('landlord', 2) + first_of('tenant', 2) + first_of('split', 2)

checks = []
for s in samples:
    t = truth[s]
    year = re.search(r'(20\d\d)', t['expiry']).group(1)
    checks.append((f'{s}-expiry', f"When does the lease for store {s} expire?", [year]))
    dep = t['deposit']
    if dep.startswith('None'):
        expect = ['none', 'no security deposit', 'not require', 'creditworthiness']
    else:
        amount = re.search(r'\$([\d,]+)', dep).group(1)
        expect = [amount]
    checks.append((f'{s}-deposit', f"What is the security deposit for store {s}?", expect))

passed = failed = 0
for cid, q, expect in checks:
    r = requests.post(f"{BASE}/api/agents/{agent_id}/chat",
                      headers={"X-API-Key": API_KEY, "Authorization": f"Bearer {API_KEY}",
                               "Content-Type": "application/json"},
                      json={'prompt': q, 'history': '[]'}, timeout=300)
    ans = str(r.json().get('response') or r.json().get('answer') or '') if r.status_code == 200 else f'[{r.status_code}]'
    low = ans.lower()
    hit = [e for e in expect if e.lower() in low]
    cited = bool(re.search(r'SCALE13_S\d{3}|\bp\.?\s?\d\b|page \d', ans, re.I))
    verdict = 'PASS' if hit else 'FAIL'
    passed += verdict == 'PASS'
    failed += verdict == 'FAIL'
    print(f"  {cid:16} {verdict}  matched={hit or expect}  cited_source={cited}  answer[:110]={ans[:110].replace(chr(10), ' ')!r}")

print(f"\nNEEDLE checks: {passed} PASS / {failed} FAIL of {len(checks)}")
sys.exit(1 if failed else 0)
