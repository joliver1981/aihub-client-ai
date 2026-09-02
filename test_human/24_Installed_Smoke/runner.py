"""
Installed-Application Smoke — pack 24.

WHY THIS PACK EXISTS: pack 15 --host <box> already covers most of an installed
build (auth, pages, agent CRUD + chat, knowledge ingest AND retrieval, documents
API, automations, portal workflows, approvals, scheduler, secrets, users/groups,
MCP, connections + the connection competency tier). Three things it does NOT
cover on a remote target, and they are exactly the "did anything obvious break"
surfaces:

  * Command Center  - pack 15 only proves the service answers /health and mints
                      a token. It never takes a real conversational turn.
  * The Agent       - not probed at all by pack 15.
  * NL->SQL         - pack 15's check is pinned to oracle agent 281 (AIRDB2) and
                      SKIPs when that agent is absent, which it is on a fresh
                      install. A total NL->SQL outage therefore reads as "skipped".

This pack fills those three gaps and nothing else. Run it AFTER pack 15 --host.

Design rules borrowed from packs 15/16/17:
  - every check returns (ok, evidence); None = SKIP with a reason, never a
    silent pass.
  - an unreachable dependency SKIPs with the reason named. It never FAILs, so a
    rig problem is never reported as a product defect.
  - NL->SQL is graded on "did it produce a real result", not on a fixed oracle
    value, so it works against whatever data agent the box actually has. The
    regression signature we care about is the friendly fallback string.

Run (aihub2.1 env):
    python runner.py --host 10.0.0.6
    python runner.py --host 10.0.0.6 --only cc_
"""
import argparse
import json
import os
import sys
import time
import datetime
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

# CC and The Agent both authenticate with a token signed from API_KEY, so the
# key has to be loaded before anything is minted (pack 20 does the same). This
# only works when the installed box shares this tree's API_KEY; when it does
# not, the token is rejected and the affected checks SKIP with that reason
# rather than reporting a product failure.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(REPO, ".env"))
except Exception:
    pass
try:
    import secure_config
    secure_config.load_secure_config()
except Exception:
    pass


def mint_token():
    import shared_auth
    return shared_auth.sign_cc_token({
        "user_id": 1, "role": 3, "tenant_id": os.getenv("TENANT_ID", ""),
        "username": "pack24-smoke", "name": "Pack 24 Installed Smoke",
    })

FALLBACK_MARKERS = (
    "encountered an issue processing your request",
    "I'm having trouble processing that request",
)

CHECKS = []


def check(cid, area, title):
    def deco(fn):
        CHECKS.append({"id": cid, "area": area, "title": title, "fn": fn})
        return fn
    return deco


def log(msg):
    print(f"[smoke] {msg}", flush=True)


def hidden_fields(html):
    """Pull hidden inputs (CSRF etc.) out of a login form."""
    import re
    out = {}
    for m in re.finditer(r'<input[^>]+type=["\']hidden["\'][^>]*>', html, re.I):
        tag = m.group(0)
        n = re.search(r'name=["\']([^"\']+)["\']', tag)
        v = re.search(r'value=["\']([^"\']*)["\']', tag)
        if n:
            out[n.group(1)] = v.group(1) if v else ""
    return out


class Api:
    def __init__(self, base_url, username, password):
        self.base = base_url.rstrip("/")
        self.s = requests.Session()
        r = self.s.get(f"{self.base}/login", timeout=20)
        data = {"username": username, "password": password, "submit": "Login"}
        data.update(hidden_fields(r.text))
        r = self.s.post(f"{self.base}/login", data=data,
                        allow_redirects=True, timeout=30)
        if "/login" in r.url:
            raise RuntimeError(f"admin login failed (landed on {r.url})")

    def get(self, path, **kw):
        return self.s.get(f"{self.base}{path}", timeout=kw.pop("timeout", 90), **kw)

    def post(self, path, payload=None, **kw):
        return self.s.post(f"{self.base}{path}", json=payload,
                           timeout=kw.pop("timeout", 120), **kw)

    @staticmethod
    def jbody(r):
        try:
            body = r.json()
        except Exception:
            return None
        if isinstance(body, str):
            try:
                return json.loads(body)
            except Exception:
                return body
        return body


# ------------------------------------------------------------------ checks

@check("svc_health", "Services", "app / CC / agent-service / builder all answer")
def c_health(ctx):
    results = {}
    for name, url in (("app", f"{ctx['base']}/login"),
                      ("command_center", f"{ctx['cc']}/health"),
                      ("agent_service", f"{ctx['agent']}/health"),
                      ("builder", f"{ctx['builder']}/")):
        try:
            r = requests.get(url, timeout=15)
            results[name] = r.status_code
        except Exception as e:
            results[name] = f"ERR {type(e).__name__}"
    ok = all(v == 200 for v in results.values())
    return ok, f"{results}"


@check("nlq_real_answer", "Data/NLQ", "NL->SQL returns a real result, not the friendly fallback")
def c_nlq(ctx):
    """Graded on substance, not on a fixed oracle: a data agent must produce an
    answer that is NOT the DATA_AGENT_FALLBACK_RESPONSE. That fallback string is
    the exact signature of the gpt-5.6-terra 'tools + reasoning_effort' 400 that
    silently killed NL->SQL for a week - the engine 400s, swallows it, and serves
    a friendly apology as a normal answer."""
    api = ctx["api"]
    body = api.jbody(api.get("/get/data_agents")) or []
    rows = body.get("data") if isinstance(body, dict) else body
    if isinstance(rows, str):
        rows = json.loads(rows)
    ids = [str(a.get("id") or a.get("agent_id")) for a in (rows or [])
           if isinstance(a, dict)]
    if not ids:
        return None, "SKIP: no data agents exist on this target"

    api.get("/data_assistants", timeout=45)   # seed the chat session
    tried = []
    for aid in ids[:4]:
        t0 = time.time()
        r = api.post("/chat/data",
                     {"agent_id": aid, "question": "How many rows are in the largest table?",
                      "history": [], "format_table_as_json": False,
                      "caution_level": "medium"}, timeout=180)
        el = time.time() - t0
        text = r.text or ""
        fell_back = any(m in text for m in FALLBACK_MARKERS)
        no_schema = "no documented schema" in text
        tried.append(f"agent {aid}: http={r.status_code} {el:.1f}s "
                     f"fallback={fell_back} no_schema={no_schema}")
        if r.status_code == 200 and not fell_back and not no_schema:
            return True, (f"agent {aid} answered in {el:.1f}s without the fallback; "
                          f"payload={len(text)}b | tried: {'; '.join(tried)}")
    # Every agent either fell back or has no schema. Only the FALLBACK case is a
    # product failure; an unconfigured agent is a fixture problem, not a defect.
    if all("no_schema=True" in t for t in tried):
        return None, f"SKIP: no data agent on this box has a documented schema | {'; '.join(tried)}"
    return False, f"every data agent returned the fallback | {'; '.join(tried)}"


@check("cc_chat_turn", "Command Center", "CC takes a real conversational turn")
def c_cc_chat(ctx):
    try:
        token = mint_token()
    except Exception as e:
        return None, f"SKIP: could not mint a CC token ({type(e).__name__}: {e})"

    try:
        r = requests.post(f"{ctx['cc']}/api/chat",
                          json={"message": "What is 1875 divided by 25? "
                                           "Reply with just the number."},
                          headers={"Authorization": f"Bearer {token}"},
                          timeout=180)
    except Exception as e:
        return None, f"SKIP: CC unreachable ({type(e).__name__}: {e})"

    if r.status_code == 401:
        # The CC JWT secret is derived from the box's API_KEY. A token minted
        # from THIS machine's config cannot validate against a box with a
        # different key - that is a rig mismatch, not a CC defect.
        return None, ("SKIP: CC rejected a locally-minted token (401) — the "
                      "installed box uses a different API_KEY, so CC chat "
                      "cannot be driven from here without its key")
    text = r.text or ""
    ok = r.status_code == 200 and "75" in text and not any(
        m in text for m in FALLBACK_MARKERS)
    return ok, f"http={r.status_code}, contains-75={'75' in text}, tail={text[-160:]}"


@check("agent_service_turn", "The Agent", "agent service answers a real turn")
def c_agent_turn(ctx):
    base = ctx["agent"]
    try:
        h = requests.get(f"{base}/health", timeout=15)
        hb = h.json() if h.status_code == 200 else {}
    except Exception as e:
        return None, f"SKIP: agent service unreachable ({type(e).__name__}: {e})"
    if h.status_code != 200:
        return False, f"/health http={h.status_code}"

    try:
        token = mint_token()
    except Exception as e:
        return None, f"SKIP: could not mint an agent token ({type(e).__name__}: {e})"

    try:
        r = requests.post(f"{base}/api/chat",
                          json={"message": "What is 1875 divided by 25? "
                                           "Reply with just the number."},
                          headers={"Authorization": f"Bearer {token}"},
                          stream=True, timeout=(15, 180))
    except Exception as e:
        return None, f"SKIP: /api/chat unreachable ({type(e).__name__}: {e})"

    if r.status_code in (401, 403):
        # Token is signed from THIS tree's API_KEY; a box with a different key
        # rejects it. Rig mismatch, not an agent-service defect.
        return None, (f"SKIP: installed box rejected a locally-minted token "
                      f"(http={r.status_code}) — it uses a different API_KEY")
    if r.status_code != 200:
        return False, f"/api/chat http={r.status_code}"

    body = []
    try:
        for chunk in r.iter_content(chunk_size=None, decode_unicode=True):
            if chunk:
                body.append(chunk if isinstance(chunk, str)
                            else chunk.decode("utf-8", "replace"))
            if sum(len(b) for b in body) > 60000:
                break
    except Exception:
        pass
    text = "".join(body)
    ok = "75" in text and not any(m in text for m in FALLBACK_MARKERS)
    return ok, (f"health model={hb.get('model')}, stream={len(text)}b, "
                f"contains-75={'75' in text}, tail={text[-160:]}")


# ------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="10.0.0.6",
                    help="installed application host (default 10.0.0.6)")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="admin")
    ap.add_argument("--only", default=None)
    ap.add_argument("--api-key-file", default=None,
                    help="path to a file whose first non-empty line is the "
                         "INSTALLED box's API_KEY. CC and The Agent sign their "
                         "bearer tokens from it, so without it those two checks "
                         "SKIP. Pass a file rather than the value so the key "
                         "never lands in shell history or a transcript.")
    args = ap.parse_args()

    if args.api_key_file:
        try:
            with open(args.api_key_file, "r", encoding="utf-8") as fh:
                key = next((ln.strip() for ln in fh if ln.strip()), "")
            if not key:
                log(f"WARNING: {args.api_key_file} is empty — CC/Agent will SKIP")
            else:
                # Override whatever this tree loaded so tokens are signed for the
                # TARGET box. Both names are read by shared_auth's secret chain.
                os.environ["API_KEY"] = key
                os.environ.pop("CC_JWT_SECRET", None)
                import importlib
                import shared_auth
                importlib.reload(shared_auth)
                log(f"signing tokens with the key from {args.api_key_file}")
        except OSError as e:
            log(f"WARNING: could not read {args.api_key_file} ({e}) — CC/Agent will SKIP")

    host = args.host
    base = f"http://{host}:5001"
    ctx = {
        "base": base,
        "cc": f"http://{host}:5091",
        "agent": f"http://{host}:5111",
        "builder": f"http://{host}:8100",
    }
    log(f"target={host} (installed application)")

    try:
        ctx["api"] = Api(base, args.user, args.password)
    except Exception as e:
        log(f"FATAL: cannot log in to {base}: {e}")
        return 3

    selected = [c for c in CHECKS if not args.only or args.only in c["id"]]
    results = []
    for c in selected:
        t0 = time.time()
        try:
            ok, evidence = c["fn"](ctx)
        except Exception as e:
            ok, evidence = False, f"{type(e).__name__}: {e}"
        el = time.time() - t0
        status = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
        results.append({"id": c["id"], "area": c["area"], "status": status,
                        "evidence": str(evidence)[:400]})
        suffix = "" if status == "SKIP" else f" ({el:.1f}s)"
        log(f"{status:6s} {c['id']}{suffix} — {str(evidence)[:190]}")

    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    verdict = ("CLEAN" if not counts.get("FAIL") else "FAILURES")

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    hist = os.path.join(HERE, "results_history", f"host_{host}")
    os.makedirs(hist, exist_ok=True)
    lines = [f"# Installed Smoke — {stamp} (INSTALLED {host})", "",
             f"- Base: `{base}`", "",
             f"## Verdict: **{verdict}** — "
             + " / ".join(f"{v} {k}" for k, v in sorted(counts.items())), "",
             "| area | check | status | evidence |", "|---|---|---|---|"]
    icon = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "SKIP": "⏭ SKIP"}
    for r in results:
        ev = r["evidence"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {r['area']} | {r['id']} | {icon[r['status']]} | {ev} |")
    report = "\n".join(lines) + "\n"

    with open(os.path.join(hist, f"results_{stamp}.json"), "w", encoding="utf-8") as fh:
        json.dump({"stamp": stamp, "host": host, "results": results}, fh, indent=2)
    for p in (os.path.join(hist, f"REPORT_{stamp}.md"),
              os.path.join(HERE, "REPORT_LATEST.md")):
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(report)

    print("\n" + "=" * 72)
    print(report)
    print(f"Report: {os.path.join(HERE, 'REPORT_LATEST.md')}")
    return 0 if verdict == "CLEAN" else 1


if __name__ == "__main__":
    sys.exit(main())
