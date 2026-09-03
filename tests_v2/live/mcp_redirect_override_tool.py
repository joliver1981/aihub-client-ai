#!/usr/bin/env python3
"""
Set / clear / show the per-server "Redirect URI override" of an MCP server
through the same admin API the MCP Servers page uses (GET → PUT echo), so a
test can point one server at a different broker host (e.g. the p01 deployment
slot before a swap) and put it back afterwards.

    python tests_v2/live/mcp_redirect_override_tool.py show  [--server-id 30]
    python tests_v2/live/mcp_redirect_override_tool.py set   https://ai-hub-api-p01.azurewebsites.net/api/mcp/oauth/callback
    python tests_v2/live/mcp_redirect_override_tool.py clear
    python tests_v2/live/mcp_redirect_override_tool.py check          # runs /api/mcp/oauth/broker_check

Secrets are never touched: the PUT sends a blank client secret, which the
server keeps on file (keep-on-blank).
"""
import argparse
import json
import os
import re
import sys

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


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
    ap.add_argument("action", choices=["show", "set", "clear", "check"])
    ap.add_argument("value", nargs="?", default="")
    ap.add_argument("--base", default=os.environ.get("MCQ_BASE", "http://localhost:5001"))
    ap.add_argument("--admin-user", default="admin")
    ap.add_argument("--admin-pass", default="admin")
    ap.add_argument("--server-id", type=int, default=30)
    args = ap.parse_args()
    base = args.base.rstrip("/")
    sid = args.server_id

    admin, ok = login(base, args.admin_user, args.admin_pass)
    if not ok:
        print("admin login failed"); sys.exit(2)

    if args.action == "check":
        r = admin.get(f"{base}/api/mcp/oauth/broker_check", params={"server_id": sid}, timeout=60)
        d = r.json()
        print(json.dumps(d, indent=2))
        sys.exit(0 if d.get("ok") else 1)

    s = admin.get(f"{base}/api/mcp/servers/{sid}", timeout=20).json()
    cfg = s.get("oauth_config") or {}
    if args.action == "show":
        print(json.dumps({"server_id": sid, "name": s.get("server_name"), "auth_type": s.get("auth_type"),
                          "oauth_redirect_uri": cfg.get("oauth_redirect_uri"),
                          "has_client_secret": s.get("has_client_secret"),
                          "available_to_users": s.get("available_to_users"),
                          "credential_keys": s.get("credential_keys")}, indent=2))
        return

    override = args.value.strip() if args.action == "set" else ""
    if args.action == "set" and not override:
        print("set needs a URL"); sys.exit(2)
    auth_config = {k: cfg.get(k, "") for k in (
        "oauth_grant_type", "oauth_token_endpoint", "oauth_auth_endpoint",
        "oauth_client_id", "oauth_scope", "oauth_audience")}
    auth_config["oauth_client_secret"] = ""        # blank = keep on file
    auth_config["oauth_redirect_uri"] = override   # blank = cleared
    payload = {
        "server_type": s.get("transport") or "remote",
        "transport": s.get("transport"),
        "server_name": s.get("server_name"),
        "server_url": s.get("server_url"),
        "auth_type": s.get("auth_type"),
        "auth_config": auth_config,
        "category": s.get("category") or "",
        "description": s.get("description") or "",
        "request_timeout": s.get("request_timeout") or 30,
        "max_retries": s.get("max_retries") or 3,
        "verify_ssl": s.get("verify_ssl", True),
    }
    r = admin.put(f"{base}/api/mcp/servers/{sid}", json=payload, timeout=30)
    print("PUT:", r.status_code, r.text[:120])
    after = admin.get(f"{base}/api/mcp/servers/{sid}", timeout=20).json()
    now = (after.get("oauth_config") or {}).get("oauth_redirect_uri")
    print(f"oauth_redirect_uri now: {now!r}; credential_keys: {after.get('credential_keys')}")
    want = override or None
    sys.exit(0 if now == want else 1)


if __name__ == "__main__":
    main()
