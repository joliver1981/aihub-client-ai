"""
Automated Stripe Integration Test Runner
Sends all 9 test turns via the builder service API directly.
"""
import requests
import json
import time
import sys

BASE_URL = "http://localhost:8100"
MAIN_URL = "http://localhost:5001"

# Get auth token
s = requests.Session()
r = s.post(f"{MAIN_URL}/login", data={"username": "admin", "password": "admin"}, allow_redirects=False)
print(f"Login: {r.status_code}")

# Get builder token from redirect
r = s.get(f"{MAIN_URL}/builder", allow_redirects=False)
if r.status_code in (301, 302):
    loc = r.headers.get("Location", "")
    token = loc.split("token=")[-1] if "token=" in loc else ""
    print(f"Token: {token[:30]}...")
else:
    print(f"Builder redirect failed: {r.status_code}")
    sys.exit(1)

# Builder session
bs = requests.Session()
bs.headers["Authorization"] = f"Bearer {token}"

def send_message(message, turn_name, wait_secs=30):
    """Send a message to the builder and wait for response."""
    print(f"\n{'='*70}")
    print(f"TURN: {turn_name}")
    print(f"{'='*70}")
    print(f"SENDING: {message[:100]}...")
    
    try:
        r = bs.post(f"{BASE_URL}/api/chat", json={"message": message}, timeout=120)
        print(f"Status: {r.status_code}")
        
        if r.status_code == 200:
            data = r.json()
            response = data.get("response", data.get("message", str(data)))
            print(f"RESPONSE ({len(response)} chars):")
            print(response[:2000])
            if len(response) > 2000:
                print(f"... [{len(response)-2000} more chars]")
            return data
        else:
            print(f"ERROR: {r.text[:500]}")
            return None
    except Exception as e:
        print(f"EXCEPTION: {e}")
        return None

def send_message_stream(message, turn_name):
    """Send a message via streaming endpoint."""
    print(f"\n{'='*70}")
    print(f"TURN: {turn_name}")  
    print(f"{'='*70}")
    print(f"SENDING: {message[:100]}...")
    
    try:
        r = bs.post(f"{BASE_URL}/api/chat/stream", json={"message": message}, stream=True, timeout=180)
        print(f"Status: {r.status_code}")
        
        full_response = ""
        for line in r.iter_lines(decode_unicode=True):
            if line:
                if line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                        if data.get("type") == "content":
                            full_response += data.get("content", "")
                        elif data.get("type") == "done":
                            break
                        elif data.get("type") == "plan":
                            plan = data.get("plan", {})
                            steps = plan.get("steps", [])
                            print(f"\n  PLAN ({len(steps)} steps):")
                            for step in steps:
                                print(f"    Step {step.get('order')}: {step.get('domain')}.{step.get('action')} - {step.get('description','')[:80]}")
                        elif data.get("type") == "step_result":
                            sr = data.get("result", {})
                            print(f"    STEP RESULT: {sr.get('status')} - {sr.get('message','')[:100]}")
                            if sr.get("data"):
                                print(f"      DATA: {json.dumps(sr['data'])[:300]}")
                    except json.JSONDecodeError:
                        pass
        
        print(f"\nRESPONSE ({len(full_response)} chars):")
        print(full_response[:2000])
        if len(full_response) > 2000:
            print(f"... [{len(full_response)-2000} more chars]")
        return full_response
    except Exception as e:
        print(f"EXCEPTION: {e}")
        return None

# Check what API endpoints exist
print("Checking builder API endpoints...")
for endpoint in ["/api/chat", "/api/chat/stream", "/api/health", "/health"]:
    try:
        r = bs.get(f"{BASE_URL}{endpoint}", timeout=5)
        print(f"  GET {endpoint}: {r.status_code}")
    except:
        try:
            r = bs.post(f"{BASE_URL}{endpoint}", json={"message": "test"}, timeout=5)
            print(f"  POST {endpoint}: {r.status_code}")
        except Exception as e:
            print(f"  {endpoint}: {e}")

# Try to find the right endpoint
print("\n\nLooking for chat endpoint...")
r = bs.options(f"{BASE_URL}/api/chat", timeout=5)
print(f"OPTIONS /api/chat: {r.status_code} {r.headers.get('Allow','')}")

# Check routes
try:
    r = bs.get(f"{BASE_URL}/api/routes", timeout=5)
    print(f"Routes: {r.status_code}")
    if r.status_code == 200:
        print(r.text[:500])
except:
    pass

# Look at the builder service routes file to find the correct endpoint
print("\n\nDone with endpoint discovery. Check routes/chat.py for the correct endpoint.")
