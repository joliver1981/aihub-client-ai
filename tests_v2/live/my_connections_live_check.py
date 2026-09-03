#!/usr/bin/env python3
"""
My Connections Phase 1 — live check against a RUNNING main app (T2/T3/T4/T6).

Drives the same HTTP surface the UI uses, as an ADMIN and then as a freshly
created ROLE-1 user (the audience the feature exists for — defect §1.2 shipped
because every test had been from an admin seat).

    C:\\Users\\james\\miniconda3\\envs\\aihub2.1\\python.exe tests_v2/live/my_connections_live_check.py
        [--base http://localhost:5001] [--admin-user admin] [--admin-pass admin]
        [--server-id 30] [--keep-user] [--require-broker]

What it proves (each row PASS/FAIL/SKIP with evidence):
  admin   redirect_uri endpoint → broker URI + return address + tenant id
  admin   server record → has_client_secret / available_to_users / column presence
  admin   broker_check → cloud verifies a signed state (needs the deployed broker)
  role-1  My Connections listing shows the server (published, or column absent)
  role-1  Connect (authorize) is NOT 403: 302 to the provider carrying the BROKER
          redirect_uri and a signed state whose return address is this origin —
          or, without a client secret, the on-prem pre-flight page (409)
  role-1  callback: garbage state → 400; foreign-origin signed state → 302 bounce;
          same-origin state with no pending flow → 400 mismatch (T6 on-prem)
  role-1  the on-prem verify endpoint answers ok for a state signed with API_KEY
Cleanup: the temporary user is deleted unless --keep-user.
"""
import argparse
import json
import os
import re
import sys
from urllib.parse import parse_qs, urlsplit

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, REPO)
from dotenv import load_dotenv                                   # noqa: E402
load_dotenv(os.path.join(REPO, ".env"))
from builder_mcp.agent_integration.oauth_state import (         # noqa: E402
    sign_state, verify_state_with_key, StateError, CALLBACK_PATH_SUFFIX,
)

TMP_USER, TMP_PASS = "mcq-role1-probe", "McqTemp!2026"
try:  # Windows consoles default to cp1252; the report uses arrows
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

rows = []


def record(name, status, evidence=""):
    rows.append((name, status, evidence))
    print(f"[{status}] {name}" + (f" — {evidence}" if evidence else ""), flush=True)


def hidden_fields(html):
    out = dict(re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', html))
    out.update(dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"', html)))
    return out


def login(base, username, password):
    sess = requests.Session()
    r = sess.get(f"{base}/login", timeout=20)
    data = {"username": username, "password": password, "submit": "Login"}
    data.update(hidden_fields(r.text))
    r = sess.post(f"{base}/login", data=data, allow_redirects=False, timeout=30)
    ok = r.status_code in (301, 302, 303) and "/login" not in (r.headers.get("Location") or "")
    return sess, ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("MCQ_BASE", "http://localhost:5001"))
    ap.add_argument("--admin-user", default="admin")
    ap.add_argument("--admin-pass", default="admin")
    ap.add_argument("--server-id", type=int, default=30)
    ap.add_argument("--keep-user", action="store_true")
    ap.add_argument("--require-broker", action="store_true",
                    help="fail (not skip) when the cloud broker check does not pass")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    sid = args.server_id
    api_key = os.getenv("API_KEY", "")

    admin, ok = login(base, args.admin_user, args.admin_pass)
    if not ok:
        record("admin login", "FAIL", "check --admin-user/--admin-pass")
        return finish()
    record("admin login", "PASS")

    # ---- admin: redirect info -------------------------------------------------
    r = admin.get(f"{base}/api/mcp/oauth/redirect_uri", params={"server_id": sid}, timeout=20)
    info = r.json() if r.status_code == 200 else {}
    registered = info.get("redirect_uri", "")
    record("admin: redirect_uri endpoint",
           "PASS" if r.status_code == 200 and registered.endswith(CALLBACK_PATH_SUFFIX)
           and info.get("return_address", "").startswith(base) and info.get("tenant_id") else "FAIL",
           f"{registered} ({info.get('source')}), return={info.get('return_address')}, tenant={info.get('tenant_id')}")

    # ---- admin: server record --------------------------------------------------
    r = admin.get(f"{base}/api/mcp/servers/{sid}", timeout=20)
    srv = r.json() if r.status_code == 200 else {}
    has_secret = bool(srv.get("has_client_secret"))
    published = bool(srv.get("available_to_users", True))
    column_present = bool(srv.get("visibility_column_present"))
    grant = (srv.get("oauth_config") or {}).get("oauth_grant_type")
    record("admin: server record exposes secret/visibility state",
           "PASS" if r.status_code == 200 and "has_client_secret" in srv and "available_to_users" in srv else "FAIL",
           f"grant={grant} has_client_secret={has_secret} available_to_users={published} "
           f"column_present={column_present}")

    # ---- admin: broker self-test ------------------------------------------------
    r = admin.get(f"{base}/api/mcp/oauth/broker_check", params={"server_id": sid}, timeout=40)
    bc = r.json() if r.status_code == 200 else {}
    if bc.get("ok"):
        record("admin: broker_check (cloud verified a signed state)", "PASS",
               f"tenant={bc.get('tenant_id')} broker_tenant={bc.get('broker_tenant_id')} via {bc.get('verify_url')}")
    else:
        record("admin: broker_check", "FAIL" if args.require_broker else "SKIP",
               f"{bc.get('reason')} (http={bc.get('http_status')}, {bc.get('verify_url')})")

    # ---- role-1 user ------------------------------------------------------------
    def probe_user_id():
        body = admin.get(f"{base}/get/users", timeout=20).json()
        if isinstance(body, str):
            body = json.loads(body)
        if isinstance(body, dict):
            body = body.get("data") or body.get("users") or body.get("response") or []
        if isinstance(body, str):
            body = json.loads(body)
        rows = [u for u in (body or []) if isinstance(u, dict)]
        return next((u.get("id") for u in rows if (u.get("user_name") or "") == TMP_USER), None)

    # Reuse a probe user left behind by an interrupted run (re-setting its password), else create.
    uid = probe_user_id()
    r = admin.post(f"{base}/add/user", json={"user_id": uid or 0, "user_name": TMP_USER, "name": "MCQ role-1 probe",
                                            "email": "mcq-probe@example.com", "password": TMP_PASS,
                                            "role": 1, "phone": ""}, timeout=30)
    uid = probe_user_id()
    if not uid:
        record("create role-1 probe user", "FAIL", f"add-user http={r.status_code} {r.text[:120]}")
        return finish()
    record("create role-1 probe user", "PASS", f"id={uid}")

    try:
        user, ok = login(base, TMP_USER, TMP_PASS)
        record("role-1 login", "PASS" if ok else "FAIL")
        if not ok:
            return finish()

        # listing
        r = user.get(f"{base}/api/my-connections/servers", timeout=20)
        listed = [c.get("server_id") for c in (r.json() if r.status_code == 200 else [])]
        expect_listed = published or not column_present
        record("role-1: My Connections listing",
               "PASS" if r.status_code == 200 and ((sid in listed) == expect_listed) else "FAIL",
               f"listed={listed} expected_present={expect_listed}")

        # authorize — the defect §1.2 test
        r = user.get(f"{base}/api/mcp/oauth/authorize/{sid}", allow_redirects=False, timeout=30)
        if r.status_code == 403:
            record("role-1: Connect is not Developer-gated", "FAIL" if expect_listed else "PASS",
                   f"403 — {'still gated!' if expect_listed else 'unpublished server correctly refused'}")
        elif r.status_code == 302:
            loc = r.headers.get("Location", "")
            q = {k: v[0] for k, v in parse_qs(urlsplit(loc).query).items()}
            try:
                payload = verify_state_with_key(q.get("state", ""), api_key)
                state_ok = payload["r"] == base + CALLBACK_PATH_SUFFIX and payload["t"] == info.get("tenant_id")
                state_ev = f"state.r={payload['r']} t={payload['t']}"
            except StateError as e:
                state_ok, state_ev = False, f"state refused: {e.reason}"
            record("role-1: Connect → 302 to provider with BROKER redirect_uri + signed state",
                   "PASS" if has_secret and q.get("redirect_uri") == registered and state_ok
                   and q.get("code_challenge_method") == "S256" else "FAIL",
                   f"host={urlsplit(loc).netloc} redirect_uri={q.get('redirect_uri')} {state_ev}")
        elif r.status_code == 409 and "client secret" in r.text:
            record("role-1: Connect → on-prem pre-flight (no client secret yet)",
                   "PASS" if not has_secret else "FAIL",
                   "409 page names the fix; add the secret on MCP Servers (WI-0) to test the full 302")
        else:
            record("role-1: Connect", "FAIL", f"http={r.status_code} {r.text[:160]!r}")

        # callback refusals / bounce (T6 on-prem)
        r = user.get(f"{base}/api/mcp/oauth/callback", params={"code": "x", "state": "garbage"},
                     allow_redirects=False, timeout=20)
        record("role-1: callback garbage state → 400", "PASS" if r.status_code == 400 and "Location" not in r.headers else "FAIL",
               str(r.status_code))
        here = urlsplit(base)
        other_host = "127.0.0.1" if here.hostname in ("localhost", "::1") else "localhost"
        foreign = f"{here.scheme}://{other_host}:{here.port or 80}{CALLBACK_PATH_SUFFIX}"
        st, _ = sign_state(api_key, info.get("tenant_id") or 1, foreign)
        r = user.get(f"{base}/api/mcp/oauth/callback", params={"code": "x", "state": st},
                     allow_redirects=False, timeout=20)
        loc = r.headers.get("Location", "")
        record("role-1: callback on another origin → 302 bounce to the return address",
               "PASS" if r.status_code == 302 and loc.startswith(foreign + "?") and "state=" in loc else "FAIL",
               f"{r.status_code} {loc[:70]}")
        st, _ = sign_state(api_key, info.get("tenant_id") or 1, base + CALLBACK_PATH_SUFFIX)
        r = user.get(f"{base}/api/mcp/oauth/callback", params={"code": "x", "state": st},
                     allow_redirects=False, timeout=20)
        record("role-1: same-origin state with no pending flow → 400 mismatch",
               "PASS" if r.status_code == 400 and "mismatch" in r.text else "FAIL", str(r.status_code))
        r = user.post(f"{base}/api/mcp/oauth/verify", json={"state": st}, timeout=20)
        record("on-prem verify endpoint answers ok for our own state",
               "PASS" if r.status_code == 200 and r.json().get("ok") else "FAIL", r.text[:80])
        r = user.get(f"{base}/api/mcp/oauth/callback",
                     params={"error": "<script>alert(1)</script>", "error_description": "cancelled"},
                     allow_redirects=False, timeout=20)
        record("callback escapes provider error text",
               "PASS" if r.status_code == 400 and "<script>alert" not in r.text and "&lt;script&gt;" in r.text else "FAIL",
               str(r.status_code))
        r = user.get(f"{base}/api/mcp/oauth/redirect_uri", timeout=20)
        record("role-1 cannot read the admin redirect endpoint", "PASS" if r.status_code in (401, 403) else "FAIL",
               str(r.status_code))
    finally:
        if not args.keep_user:
            d = admin.post(f"{base}/delete/user", json={"user_id": uid}, timeout=30)
            record("cleanup: delete probe user", "PASS" if d.status_code == 200 and d.json().get("status") == "success" else "FAIL",
                   d.text[:80])
    return finish()


def finish():
    failed = [n for n, s, _ in rows if s == "FAIL"]
    print(f"\n{sum(1 for _, s, _ in rows if s == 'PASS')} PASS / {len(failed)} FAIL / "
          f"{sum(1 for _, s, _ in rows if s == 'SKIP')} SKIP")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
