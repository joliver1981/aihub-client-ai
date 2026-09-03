"""Pack 20 — pass-4 maps + image generation live drive (2026-09-02).

Real streamed /api/chat turns as the admin user (id 13):
  M1  store locations given as PLACE NAMES only -> render_map geocodes them
      (enrichment) and the reply carries an ```aihub-map``` reference whose
      stored spec (GET /api/blocks/<id>) has 3 markers; the reply discloses
      the positions were geocoded / approximate.
  M2  US-state values incl. a non-US region -> the stored choropleth spec has
      the normalized state names AND Ontario carried as unmapped; the reply
      tells the user Ontario was not shaded.
  G1  (only with --image, costs money) "generate an image of …" ->
      generate_image; the reply carries an inline image line + download link
      and the PNG downloads through the API.
The Leaflet rendering itself is verified by screenshot in the in-app browser.

Run:  C:\\Users\\james\\miniconda3\\envs\\aihub-agent\\python.exe pass4_maps_images_live.py [--image]
"""
import json
import os
import re
import sys

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from dotenv import load_dotenv
load_dotenv(os.path.join(APP_ROOT, ".env"))
try:
    import secure_config
    secure_config.load_secure_config()
except Exception:
    pass
import requests
import shared_auth

BASE = f"http://127.0.0.1:{os.getenv('AGENT_SERVICE_PORT') or int(os.getenv('HOST_PORT', '5001')) + 110}"
TOKEN = shared_auth.sign_cc_token({"user_id": 13, "role": 3, "tenant_id": os.getenv("TENANT_ID", ""),
                                   "username": "pass4-live", "name": "Pass4 Live"})
HDR = {"Authorization": f"Bearer {TOKEN}"}
WITH_IMAGE = "--image" in sys.argv
SESSION = None


def turn(msg, timeout=600, fresh=False):
    global SESSION
    if fresh:
        SESSION = None
    r = requests.post(f"{BASE}/api/chat",
                      json={"message": msg, "session_id": SESSION, "timezone": "America/New_York"},
                      headers=HDR, stream=True, timeout=(10, timeout))
    r.raise_for_status()
    tools, texts = [], []
    for raw in r.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data: "):
            continue
        try:
            ev = json.loads(raw[6:])
        except Exception:
            continue
        t = ev.get("type")
        if t == "tool":
            tools.append(ev.get("name", "").replace("mcp__aihub__", ""))
        elif t == "text":
            texts.append(ev.get("text", ""))
        elif t in ("result", "error"):
            SESSION = ev.get("session_id") or SESSION
        if t == "done":
            break
    reply = "\n".join(texts).strip()
    print("\n" + "=" * 78 + f"\nUSER> {msg}\nTOOLS> {tools}\nAGENT> {reply[:1200]}", flush=True)
    return tools, reply


def map_specs(reply):
    """Resolved map specs from the reply — {"ref"} fences through the API."""
    out = []
    for m in re.finditer(r"```aihub-map\s*\n(.*?)\n```", reply, re.S):
        try:
            spec = json.loads(m.group(1))
        except Exception:
            continue
        if isinstance(spec, dict) and spec.get("ref"):
            r = requests.get(f"{BASE}/api/blocks/{spec['ref']}", headers=HDR, timeout=30)
            spec = (r.json() or {}).get("spec") if r.ok else None
        if spec:
            out.append(spec)
    return out


results = []


def check(cid, ok, evidence):
    results.append((cid, bool(ok), str(evidence)[:400]))
    print(f"[{'PASS' if ok else 'FAIL'}] {cid}: {str(evidence)[:300]}", flush=True)


# M1 — markers from place names (geocoded)
tools, reply = turn("Show me a map of these store locations: Newark NJ, Austin TX, and Denver CO.", fresh=True)
mb = map_specs(reply)
mk = (mb[0].get("markers") or []) if mb else []
check("M1 render_map geocoded 3 place names into markers (stored block resolves)",
      "render_map" in tools and len(mk) == 3 and all(-125 < m["lng"] < -66 and 24 < m["lat"] < 50 for m in mk),
      f"tools={tools} markers={[(m.get('label'), m.get('lat'), m.get('lng')) for m in mk]}")
check("M1b reply discloses the positions were geocoded/approximate",
      bool(re.search(r"approximate|geocod|looked up", reply, re.I)), reply[:200])

# M2 — choropleth with an unmappable region
tools, reply = turn("Shade a US map by these sales figures: NJ 120500, TX 98000, CA 75250, and Ontario 5000.",
                    fresh=True)
mb = map_specs(reply)
rg = (mb[0].get("regions") or []) if mb else []
names = sorted(r.get("name") for r in rg)
check("M2 stored choropleth has normalized states and carries Ontario as unmapped",
      "render_map" in tools and names == ["California", "New Jersey", "Ontario", "Texas"]
      and "Ontario" in (mb[0].get("unmapped") or []) if mb else False,
      f"tools={tools} names={names} unmapped={(mb[0].get('unmapped') if mb else None)}")
check("M2b reply tells the user Ontario was not shaded", "ontario" in reply.lower(), reply[:300])

# G1 — image generation (opt-in: real money)
if WITH_IMAGE:
    tools, reply = turn("Generate an image of a red bicycle leaning against a white wall, simple flat "
                        "illustration style.", fresh=True, timeout=900)
    img = re.findall(r"!\[[^\]]*\.png\]\(/api/files/[0-9a-f-]+\)", reply)
    dl = re.findall(r"\[⤓[^\]]*\.png[^\]]*\]\(/api/files/([0-9a-f-]+)\)", reply)
    size = -1
    head = b""
    if dl:
        r = requests.get(f"{BASE}/api/files/{dl[0]}", headers=HDR, timeout=60)
        size = len(r.content) if r.ok else -1
        head = r.content[:4] if r.ok else b""
    check("G1 generate_image -> inline image line + downloadable PNG",
          "generate_image" in tools and img and dl and size > 5000 and head == b"\x89PNG",
          f"tools={tools} img={img[:1]} bytes={size}")
else:
    print("(image generation skipped — pass --image to spend on one real generation)")

print("\n" + "=" * 78)
for cid, ok, ev in results:
    print(f"[{'PASS' if ok else 'FAIL'}] {cid}")
print(f"REPORT {sum(1 for _, ok, _ in results if ok)}/{len(results)} passed")
sys.exit(0 if all(ok for _, ok, _ in results) else 1)
