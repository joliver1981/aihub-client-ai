"""Live check of The Agent's My Connections screen (2026-11-22) against a
running dev install: The Agent on HOST_PORT+110 and the main app on HOST_PORT.

What it drives, end to end and for real:
  - the screen's list for an Admin and for a regular user (each sees only
    their own state; no connection internals; the on/off field on /api/me);
  - the Connect address the screen opens (/my-connections/connect/<id>):
    not signed in -> the sign-in form with the way back; signed in as the
    same user -> on to the provider's sign-in page; signed in as a
    DIFFERENT user -> stopped, nothing started;
  - Disconnect for a user who is NOT connected (a no-op for them) and that it
    leaves another user's real connection alone.

What it never does: complete a sign-in at Microsoft (a person does that), or
disconnect a user who IS connected — the Disconnect step is skipped unless the
regular seat is not connected, so a real authorization cannot be removed.

Run:  aihub2.1 python tests_v2/live/the_agent_connections_screen_live_check.py
      [--admin-id 13 --regular-id 349 --server-id 30]
Tokens are minted the way the main app's /the-agent route mints them.
"""
import argparse
import os
import re
import sys

import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

ROWS = []


def check(name, ok, detail=""):
    ROWS.append((name, bool(ok), str(detail)))
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  <- {str(detail)[:300]}"))


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
    port = int(os.environ.get("HOST_PORT", "5001"))
    ap.add_argument("--main", default=f"http://127.0.0.1:{port}")
    ap.add_argument("--agent", default=f"http://127.0.0.1:{port + 110}")
    ap.add_argument("--admin-user", default="admin")
    ap.add_argument("--admin-pass", default="admin")
    ap.add_argument("--admin-id", type=int, default=13)
    ap.add_argument("--regular-id", type=int, default=349)
    ap.add_argument("--regular-user", default="ru_alex")
    ap.add_argument("--server-id", type=int, default=30)
    a = ap.parse_args()

    import secure_config
    secure_config.load_secure_config()
    import shared_auth

    seats = {
        "admin": {"user_id": a.admin_id, "role": 3, "tenant_id": 1,
                  "username": a.admin_user, "name": a.admin_user},
        "regular": {"user_id": a.regular_id, "role": 1, "tenant_id": 1,
                    "username": a.regular_user, "name": a.regular_user},
    }
    tok = {k: shared_auth.sign_cc_token(v, ttl_seconds=900) for k, v in seats.items()}
    hdr = {k: {"Authorization": "Bearer " + t, "Content-Type": "application/json"}
           for k, t in tok.items()}

    def listing(seat):
        r = requests.get(f"{a.agent}/api/my-connections", headers=hdr[seat], timeout=60)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {}

    # ---- the list, per seat -------------------------------------------------
    state = {}
    for seat, ctx in seats.items():
        r = requests.get(f"{a.agent}/api/me", headers=hdr[seat], timeout=30)
        me = r.json() if r.status_code == 200 else {}
        check(f"{seat}: /api/me says the screen is on",
              r.status_code == 200 and me.get("my_connections") is True,
              f"{r.status_code} {me.get('my_connections')}")
        st, d = listing(seat)
        conns = d.get("connections")
        ok = st == 200 and isinstance(conns, list)
        check(f"{seat}: the list answers", ok, f"{st} {d}")
        if not ok:
            continue
        internals = [k for c in conns for k in c
                     if k in ("server_url", "connection_config", "auth_type", "server_type")]
        check(f"{seat}: no connection internals in the list", not internals, internals)
        check(f"{seat}: the list names the user it is for", d.get("user_id") == ctx["user_id"],
              d.get("user_id"))
        check(f"{seat}: 'can publish' follows the role",
              d.get("can_publish") is (ctx["role"] >= 3), d.get("can_publish"))
        state[seat] = {c["server_id"]: c for c in conns}
        for c in conns:
            print(f"      [{seat}] id {c['server_id']} | {c['name']} | "
                  f"connected={c['connected']} | blocked={c['agent_blocked']}")

    r = requests.get(f"{a.agent}/api/my-connections",
                     headers={"Authorization": "Bearer not-a-token"}, timeout=30)
    check("the list refuses a bad token", r.status_code == 401, r.status_code)
    r = requests.post(f"{a.agent}/api/my-connections/{a.server_id}/disconnect",
                      headers={"Authorization": "Bearer not-a-token"}, timeout=30)
    check("Disconnect refuses a bad token", r.status_code == 401, r.status_code)

    # ---- the Connect address ------------------------------------------------
    hop = f"{a.main}/my-connections/connect/{a.server_id}"
    r = requests.get(f"{hop}?as={a.regular_id}", allow_redirects=False, timeout=30)
    loc = r.headers.get("Location") or ""
    check("Connect, not signed in: sent to the sign-in form with the way back",
          r.status_code == 302 and "/login" in loc and "connect" in loc, f"{r.status_code} {loc}")

    sess, ok = login(a.main, a.admin_user, a.admin_pass)
    check("signed in to the main app as the admin test user", ok)
    if ok:
        r = sess.get(f"{hop}?as={a.regular_id}", allow_redirects=False, timeout=30)
        check("Connect, signed in as a DIFFERENT user: stopped, nothing started",
              r.status_code == 409 and "Nothing was connected" in r.text
              and not r.headers.get("Location"), f"{r.status_code} {r.text[:200]}")
        r = sess.get(f"{hop}?as={a.admin_id}", allow_redirects=False, timeout=30)
        loc = r.headers.get("Location") or ""
        check("Connect, signed in as the same user: on to the authorize route",
              r.status_code == 302 and loc.endswith(f"/api/mcp/oauth/authorize/{a.server_id}"),
              f"{r.status_code} {loc}")
        if a.server_id in state.get("admin", {}):
            # one more step, NOT followed: the provider's own sign-in address
            r = sess.get(f"{a.main}/api/mcp/oauth/authorize/{a.server_id}",
                         allow_redirects=False, timeout=30)
            loc = r.headers.get("Location") or ""
            check("...which hands the browser to the provider's sign-in page",
                  r.status_code == 302 and loc.startswith("https://")
                  and "redirect_uri=" in loc and "state=" in loc,
                  f"{r.status_code} {loc[:120]}")
            print(f"      provider sign-in host: {loc.split('/')[2] if loc.startswith('https://') else '?'}")

    # ---- Disconnect, only where it cannot remove a real authorization -------
    reg = state.get("regular", {}).get(a.server_id)
    adm_before = state.get("admin", {}).get(a.server_id)
    if reg is None:
        print(f"SKIP  Disconnect: server {a.server_id} is not in the regular user's list")
    elif reg.get("connected"):
        print("SKIP  Disconnect: the regular user IS connected — not removing a real authorization")
    else:
        r = requests.post(f"{a.agent}/api/my-connections/{a.server_id}/disconnect",
                          headers=hdr["regular"], timeout=60)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        check("Disconnect as the (not connected) regular user answers success",
              r.status_code == 200 and body.get("status") == "success", f"{r.status_code} {r.text[:200]}")
        st, d = listing("admin")
        adm_after = {c["server_id"]: c for c in d.get("connections") or []}.get(a.server_id)
        if adm_before is not None:
            check("...and the admin's own connection is exactly as it was",
                  adm_after is not None
                  and adm_after.get("connected") == adm_before.get("connected")
                  and adm_after.get("last_connected") == adm_before.get("last_connected"),
                  f"before={adm_before} after={adm_after}")

    bad = sum(1 for _, ok, _ in ROWS if not ok)
    print(f"\n{len(ROWS) - bad}/{len(ROWS)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
