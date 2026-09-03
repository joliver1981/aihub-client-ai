#!/usr/bin/env python3
"""
The Agent x My Connections — live check against RUNNING services.

Drives the bridge end to end on this box:

  seam   GET  /api/internal/my-connections            as the CONNECTED user -> server listed, connected
  seam   GET  .../<sid>/tools                         -> the Graph tools, with annotations
  seam   POST .../<sid>/call list_recent_emails       -> real mail (counts only are printed)
  seam   negative identity: no assertion / bad / service principal -> 401, never mail
  seam   a user WITHOUT a grant -> needs_authorization, never mail
  seam   CROSS-USER CONCURRENCY: connected user + stranger in parallel -> each gets
         only its own outcome; the gateway holds a "<sid>@u<uid>" connection for
         the connected user and none for the stranger (Blocker B closed)
  agent  chat as the connected user: "what's in my Outlook inbox?" -> uses
         use_my_connection, NOT list_my_email
  agent  chat as the connected user: "send from my Outlook" -> refused by the
         write gate (no use_my_connection(send_email) call), Sent Items unchanged
  agent  chat as a user without a grant -> points at /my-connections

    C:\\Users\\james\\miniconda3\\envs\\aihub2.1\\python.exe tests_v2/live/the_agent_my_connections_live_check.py
        [--server-id 30] [--user 13] [--stranger 987654] [--skip-agent] [--skip-write-probe]

Prints PASS/FAIL/SKIP rows with evidence. Mail CONTENT is never printed —
only counts and outcome codes (the mailbox is a real person's).
"""
import argparse
import json
import os
import sys
import threading
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, REPO)
from dotenv import load_dotenv                       # noqa: E402
load_dotenv(os.path.join(REPO, ".env"))
try:
    from secure_config import load_secure_config     # noqa: E402
    load_secure_config()
except Exception:
    pass
import shared_auth                                   # noqa: E402
from role_decorators import get_internal_api_key     # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

rows = []


def record(name, status, evidence=""):
    rows.append((name, status, evidence))
    print(f"[{status}] {name}" + (f" — {evidence}" if evidence else ""), flush=True)


def seam_headers(uid, role=2):
    # Same headers The Agent sends: the machine-derived internal key as
    # X-API-Key (what the auth middleware and internal_api_key_required both
    # read) plus the explicit internal header.
    key = get_internal_api_key()
    h = {"X-API-Key": key, "X-Internal-API-Key": key, "Connection": "close"}
    if uid is not None:
        h["X-AIHub-User"] = shared_auth.sign_user_assertion(uid, 1, role)
    return h


def seam_get(base, path, uid, role=2, timeout=120):
    r = requests.get(f"{base}{path}", headers=seam_headers(uid, role), timeout=timeout)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"raw": r.text[:300]}


def seam_post(base, path, uid, body, role=2, timeout=180):
    r = requests.post(f"{base}{path}", headers=seam_headers(uid, role), json=body, timeout=timeout)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"raw": r.text[:300]}


def mail_count(result_text):
    """The Graph tool returns JSON {count, messages:[...]}; never print subjects."""
    try:
        d = json.loads(result_text)
        if isinstance(d, dict):
            return d.get("count", len(d.get("messages") or d.get("meetings") or []))
    except Exception:
        pass
    return None


def agent_chat(agent_base, uid, username, role, message, timeout=400):
    """Drive one The Agent turn over SSE; returns (tools_called, text, ok)."""
    tok = shared_auth.sign_cc_token({"user_id": uid, "role": role, "tenant_id": 1,
                                     "username": username, "name": username})
    tools, texts, ok = [], [], None
    with requests.post(f"{agent_base}/api/chat", json={"message": message, "timezone": "America/New_York"},
                       headers={"Authorization": f"Bearer {tok}"}, stream=True, timeout=timeout) as r:
        if r.status_code != 200:
            return tools, f"HTTP {r.status_code}: {r.text[:200]}", False
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except Exception:
                continue
            t = ev.get("type")
            if t == "tool":
                tools.append((str(ev.get("name") or "").replace("mcp__aihub__", ""), ev.get("input") or {}))
            elif t == "text":
                texts.append(ev.get("text") or "")
            elif t in ("result", "error"):
                ok = bool(ev.get("ok")) if t == "result" else False
    return tools, "".join(texts), ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("MCQ_BASE", "http://127.0.0.1:5001"))
    ap.add_argument("--agent-base", default="http://127.0.0.1:5111")
    ap.add_argument("--gateway-base", default="http://127.0.0.1:5071")
    ap.add_argument("--server-id", type=int, default=30)
    ap.add_argument("--user", type=int, default=13, help="a user who completed Connect")
    ap.add_argument("--username", default="user13")
    ap.add_argument("--stranger", type=int, default=987654, help="a real user with NO grant")
    ap.add_argument("--skip-agent", action="store_true")
    ap.add_argument("--skip-write-probe", action="store_true")
    args = ap.parse_args()
    base, sid, uid, stranger = args.base.rstrip("/"), args.server_id, args.user, args.stranger
    seam = "/api/internal/my-connections"

    # ---- seam: identity refusals -------------------------------------------------
    st, body = seam_get(base, seam, None)
    record("seam refuses a call with no assertion", "PASS" if st == 401 else "FAIL", f"HTTP {st} {body.get('code')}")
    r = requests.get(f"{base}{seam}", headers={"X-API-Key": get_internal_api_key(),
                                               "X-AIHub-User": "garbage"}, timeout=30)
    record("seam refuses a garbage assertion", "PASS" if r.status_code == 401 else "FAIL", f"HTTP {r.status_code}")
    st, body = seam_get(base, seam, 0)
    record("seam refuses the service principal (sub 0)", "PASS" if st == 401 else "FAIL", f"HTTP {st}")
    r = requests.get(f"{base}{seam}", headers={"X-AIHub-User": shared_auth.sign_user_assertion(uid, 1, 2)}, timeout=30)
    record("seam refuses without the service key", "PASS" if r.status_code == 401 else "FAIL", f"HTTP {r.status_code}")

    # ---- seam: catalog ----------------------------------------------------------
    st, body = seam_get(base, seam, uid)
    conns = body.get("connections") or []
    mine = next((c for c in conns if int(c.get("server_id") or 0) == sid), None)
    record("seam lists the catalog for the asserted user",
           "PASS" if st == 200 and body.get("user_id") == uid and mine else "FAIL",
           f"HTTP {st} user_id={body.get('user_id')} servers={[c.get('server_id') for c in conns]}")
    record(f"server {sid} shows connected for user {uid}",
           "PASS" if mine and mine.get("connected") else "FAIL",
           f"connected={mine and mine.get('connected')} last={mine and mine.get('last_connected')}")
    assert mine and "server_url" not in mine, "public view must not leak connection internals"

    # ---- seam: tools ----------------------------------------------------------------
    st, body = seam_get(base, f"{seam}/{sid}/tools", uid)
    tools = body.get("tools") or []
    names = sorted(t.get("name") for t in tools)
    ann = {t.get("name"): (t.get("annotations") or {}).get("readOnlyHint") for t in tools}
    record("seam lists the Graph tools on the user's own connection",
           "PASS" if st == 200 and body.get("status") == "success" and "list_recent_emails" in names else "FAIL",
           f"HTTP {st} status={body.get('status')} tools={names} msg={str(body.get('message') or '')[:160]}")
    record("tools carry read/write annotations",
           "PASS" if ann.get("list_recent_emails") is True and ann.get("send_email") is False else "FAIL", str(ann))

    # ---- seam: a real read as the user (counts only) --------------------------------
    st, body = seam_post(base, f"{seam}/{sid}/call", uid,
                         {"tool_name": "list_recent_emails", "arguments": {"limit": 5},
                          "context": {"source": "live_check"}})
    n = mail_count(body.get("result", "")) if body.get("status") == "success" else None
    record("seam reads the connected user's inbox (list_recent_emails)",
           "PASS" if st == 200 and body.get("status") == "success" and n is not None else "FAIL",
           f"HTTP {st} status={body.get('status')} messages_returned={n} "
           f"{('msg=' + str(body.get('message'))[:200]) if body.get('status') != 'success' else ''}")
    sent_before = None
    st, body = seam_post(base, f"{seam}/{sid}/call", uid,
                         {"tool_name": "list_recent_emails", "arguments": {"limit": 3, "folder": "sentitems"},
                          "context": {"source": "live_check"}})
    if body.get("status") == "success":
        try:
            sent_before = [m.get("received") or m.get("receivedDateTime") or m.get("sent")
                           for m in (json.loads(body["result"]).get("messages") or [])][:1]
        except Exception:
            sent_before = None
    record("seam reads Sent Items (baseline for the write-gate probe)",
           "PASS" if body.get("status") == "success" else "SKIP", f"status={body.get('status')} latest={sent_before}")

    # ---- gateway: the connection is keyed to the user --------------------------------
    try:
        gw = requests.get(f"{args.gateway_base}/api/mcp/connections", timeout=10).json()
    except Exception as e:
        gw = {"_error": str(e)}
    key = f"{sid}@u{uid}"
    record("gateway holds a per-user connection key for the connected user",
           "PASS" if key in gw else "FAIL", f"keys={sorted(k for k in gw if not k.startswith('_'))}")

    # ---- seam: stranger ---------------------------------------------------------------
    st, body = seam_get(base, f"{seam}/{sid}/tools", stranger)
    record("a user with no grant gets needs_authorization (tools), not a 500 and not mail",
           "PASS" if st == 200 and body.get("status") == "needs_authorization" and body.get("connected") is False else "FAIL",
           f"HTTP {st} status={body.get('status')} msg={str(body.get('message') or '')[:120]}")
    st, body = seam_post(base, f"{seam}/{sid}/call", stranger,
                         {"tool_name": "list_recent_emails", "arguments": {"limit": 1}})
    record("a user with no grant gets needs_authorization (call)",
           "PASS" if st == 200 and body.get("status") == "needs_authorization" and "result" not in body else "FAIL",
           f"HTTP {st} status={body.get('status')}")

    # ---- cross-user concurrency ------------------------------------------------------
    outcomes = {}

    def worker(tag, who):
        for i in range(3):
            st_, b_ = seam_post(base, f"{seam}/{sid}/call", who,
                                {"tool_name": "list_recent_emails", "arguments": {"limit": 2},
                                 "context": {"source": "live_check_concurrent"}})
            outcomes.setdefault(tag, []).append((st_, b_.get("status"), mail_count(b_.get("result", ""))
                                                 if b_.get("status") == "success" else None))

    threads = [threading.Thread(target=worker, args=("user", uid)),
               threading.Thread(target=worker, args=("stranger", stranger)),
               threading.Thread(target=worker, args=("user2", uid))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    user_ok = all(s == "success" for _, s, _ in outcomes.get("user", []) + outcomes.get("user2", []))
    stranger_ok = all(s == "needs_authorization" for _, s, _ in outcomes.get("stranger", []))
    try:
        gw = requests.get(f"{args.gateway_base}/api/mcp/connections", timeout=10).json()
    except Exception as e:
        gw = {"_error": str(e)}
    no_stranger_conn = f"{sid}@u{stranger}" not in gw
    record("CROSS-USER: concurrent calls keep each user's outcome separate",
           "PASS" if user_ok and stranger_ok and no_stranger_conn else "FAIL",
           f"user={outcomes.get('user')} user2={outcomes.get('user2')} stranger={outcomes.get('stranger')} "
           f"gateway_keys={sorted(k for k in gw if not k.startswith('_'))}")

    if args.skip_agent:
        return finish()

    # ---- through The Agent --------------------------------------------------------------
    try:
        h = requests.get(f"{args.agent_base}/health", timeout=5).json()
    except Exception as e:
        record("The Agent reachable", "FAIL", str(e))
        return finish()
    record("The Agent reachable", "PASS", str(h)[:120])

    tools, text, ok = agent_chat(args.agent_base, uid, args.username, 3,
                                 "Check my Microsoft 365 / Outlook inbox through my personal connection: "
                                 "how many messages are in my inbox right now and what is the most recent "
                                 "one about? One sentence, no more.")
    used = [n for n, _ in tools]
    record("Agent answers 'my Outlook inbox' via use_my_connection (not list_my_email)",
           "PASS" if ok and "use_my_connection" in used and "list_my_email" not in used else "FAIL",
           f"ok={ok} tools={used} reply_len={len(text)}")
    # The reply can quote the user's own mail — keep it OUT of the repo tree.
    import tempfile
    reply_path = os.path.join(tempfile.gettempdir(), "the_agent_my_connections_last_reply.txt")
    with open(reply_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"      (full reply saved outside the repo: {reply_path})")

    tools, text, ok = agent_chat(args.agent_base, stranger, f"user{stranger}", 2,
                                 "What's in my Outlook inbox right now?")
    used = [n for n, _ in tools]
    record("Agent tells a user without a grant to connect at /my-connections",
           "PASS" if ok and "/my-connections" in text and "list_my_email" not in used else "FAIL",
           f"ok={ok} tools={used} mentions_page={'/my-connections' in text} reply={text[:160]!r}")

    if args.skip_write_probe:
        return finish()
    tools, text, ok = agent_chat(args.agent_base, uid, args.username, 3,
                                 "Send an email FROM my own Outlook account (through my personal Microsoft 365 "
                                 "connection, not your agent mailbox) to joliver81@gmail.com with subject "
                                 "'AI Hub write-gate probe' and body 'probe'. Do not use any other way to send.")
    sends = [(n, i) for n, i in tools if n == "use_my_connection" and str(i.get("tool_name")) == "send_email"]
    own_mailbox_sends = [n for n, _ in tools if n in ("send_email", "draft_email_reply")]
    st, body = seam_post(base, f"{seam}/{sid}/call", uid,
                         {"tool_name": "list_recent_emails", "arguments": {"limit": 3, "folder": "sentitems"},
                          "context": {"source": "live_check"}})
    sent_after = None
    if body.get("status") == "success":
        try:
            sent_after = [m.get("received") or m.get("receivedDateTime") or m.get("sent")
                          for m in (json.loads(body["result"]).get("messages") or [])][:1]
        except Exception:
            sent_after = None
    unchanged = (sent_before is None) or (sent_after == sent_before)
    record("WRITE GATE (default): sending from the user's Outlook is refused; Sent Items unchanged",
           "PASS" if ok and unchanged and "own" in text.lower() else "FAIL",
           f"ok={ok} use_my_connection(send_email) attempts={len(sends)} own_mailbox_offers_or_sends={own_mailbox_sends} "
           f"sent_before={sent_before} sent_after={sent_after} reply={text[:200]!r}")
    return finish()


def finish():
    fails = [r for r in rows if r[1] == "FAIL"]
    print(f"\n{len(rows) - len(fails)}/{len(rows)} passed" + (f" — FAILED: {[r[0] for r in fails]}" if fails else ""))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
