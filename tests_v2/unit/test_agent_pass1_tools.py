"""Unit pack for The Agent's pass-1 gap tools (2026-09-02):
web search (web_tools.py), the code-flow editors + delete (authoring_tools.py),
ask_agent + get_my_contact_info (platform_tools.py), list_mcp_servers
(integration_tools.py) and send_email with the role split (work_tools.py).

All HTTP / DB / store seams are monkeypatched; nothing touches the network.
Runs standalone (aihub-agent python test_agent_pass1_tools.py) or under
pytest; self-skips in envs without claude_agent_sdk.
"""
import asyncio
import os
import sys
import tempfile
import types
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import web_tools as W                      # noqa: E402
    import authoring_tools as A                # noqa: E402
    import platform_tools as P                 # noqa: E402
    import integration_tools as I              # noqa: E402
    import work_tools as WK                    # noqa: E402
    from platform_tools import CURRENT_USER    # noqa: E402
    HAVE_SDK = True
except ImportError as e:
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(
            reason=f"needs the aihub-agent env (claude_agent_sdk): {_IMPORT_ERR}")
    except ImportError:
        pass


def _run(coro):
    return asyncio.run(coro)


def _txt(res):
    return res["content"][0]["text"]


def _as(role, uid=7):
    return CURRENT_USER.set({"user_id": uid, "role": role, "username": f"u{uid}",
                             "name": f"User {uid}"})


# ---------------------------------------------------------------------------
# Web search
# ---------------------------------------------------------------------------

DDG_HTML = """<html><body><table>
<tr><td><a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=abc" class='result-link'>First &amp; best</a></td></tr>
<tr><td class='result-snippet'>Snippet one   with   spaces</td></tr>
<tr><td><a rel="nofollow" href="https://example.org/b" class='result-link'>Second</a></td></tr>
<tr><td class='result-snippet'>Snippet two</td></tr>
<tr><td><a href="https://example.net/c" class="result-link">Third no snippet</a></td></tr>
</table></body></html>"""


def test_tavily_key_resolution_plain_then_encrypted():
    with mock.patch.dict(os.environ, {"TAVILY_API_KEY": "plain", "TAVILY_API_KEY_ENCRYPTED": "enc"}):
        assert W.tavily_key() == "plain"
    fake_encrypt = types.SimpleNamespace(decrypt_value=lambda v, k: "decrypted-" + v,
                                         ENCRYPTION_KEY="k")
    with mock.patch.dict(os.environ, {"TAVILY_API_KEY": "", "TAVILY_API_KEY_ENCRYPTED": "enc"}), \
         mock.patch.dict(sys.modules, {"encrypt": fake_encrypt}):
        assert W.tavily_key() == "decrypted-enc"
    with mock.patch.dict(os.environ, {"TAVILY_API_KEY": "", "TAVILY_API_KEY_ENCRYPTED": ""}):
        assert W.tavily_key() == ""


def test_ddg_lite_parser_unwraps_links_and_keeps_snippetless_results():
    rows = W.parse_ddg_lite(DDG_HTML, 10)
    assert [r["title"] for r in rows] == ["First & best", "Second", "Third no snippet"]
    assert rows[0]["link"] == "https://example.com/a"          # uddg unwrapped
    assert rows[0]["snippet"] == "Snippet one with spaces"
    assert rows[1]["link"] == "https://example.org/b"
    assert rows[2]["snippet"] == ""
    assert len(W.parse_ddg_lite(DDG_HTML, 2)) == 2


def test_tavily_parse_and_format():
    answer, results = W.parse_tavily(
        {"answer": "42", "results": [{"title": "T", "url": "https://x", "content": "c" * 500}]}, 5)
    out = W.format_results("q", "tavily", answer, results)
    assert "Summary" in out and "42" in out and "https://x" in out and "…" in out


def test_search_web_falls_back_to_duckduckgo_and_reports_failure_honestly():
    async def tavily_boom(q, n, key):
        raise RuntimeError("401 Unauthorized")

    async def ddg_ok(q, n):
        return [{"title": "DDG hit", "link": "https://d", "snippet": "s"}]

    async def ddg_boom(q, n):
        raise RuntimeError("connect timeout")

    with mock.patch.object(W, "tavily_key", lambda: "k"), \
         mock.patch.object(W, "_search_tavily", tavily_boom), \
         mock.patch.object(W, "_search_ddg", ddg_ok):
        res = _run(W.search_web.handler({"query": "weather newark"}))
        assert not res.get("is_error")
        assert "duckduckgo" in _txt(res) and "DDG hit" in _txt(res)
        assert "Tavily unavailable" in _txt(res)
    with mock.patch.object(W, "tavily_key", lambda: ""), \
         mock.patch.object(W, "_search_ddg", ddg_boom):
        res = _run(W.search_web.handler({"query": "x"}))
        assert res.get("is_error")
        assert "FAILED" in _txt(res) and "no Tavily key" in _txt(res)
        assert "do not invent" in _txt(res)
    res = _run(W.search_web.handler({"query": "   "}))
    assert res.get("is_error")


def test_search_web_uses_tavily_when_it_answers():
    async def tavily_ok(q, n, key):
        assert n == 3
        return "ans", [{"title": "T", "link": "https://t", "snippet": "s"}]

    with mock.patch.object(W, "tavily_key", lambda: "k"), \
         mock.patch.object(W, "default_engine", lambda: "tavily"), \
         mock.patch.object(W, "_search_tavily", tavily_ok):
        res = _run(W.search_web.handler({"query": "q", "num_results": 3}))
        assert not res.get("is_error") and "(tavily)" in _txt(res) and "ans" in _txt(res)


# ---------------------------------------------------------------------------
# Code-flow editors
# ---------------------------------------------------------------------------

def _cf_state():
    return {"name": "Flow", "workflow_id": 9,
            "nodes": [{"id": "s1", "label": "one", "isStart": True},
                      {"id": "s2", "label": "two"}],
            "connections": [{"source": "s1", "target": "s2", "type": "pass"}]}


def test_code_flow_editors_post_the_right_actions_and_read_back():
    tok = _as(2)
    calls = []
    state = {"cf": _cf_state()}

    async def fake_manage(action, payload, timeout=900.0):
        calls.append((action, payload))
        if action == "get":
            return ({"code_flow": state["cf"]}, 200) if state["cf"] else ({"error": "code flow not found"}, 404)
        if action == "unwire":
            state["cf"]["connections"] = []
            return {"ok": True}, 200
        if action == "remove_step":
            state["cf"]["nodes"] = [n for n in state["cf"]["nodes"] if n["id"] != payload["step_id"]]
            return {"ok": True}, 200
        if action == "update_step_code":
            return {"ok": True}, 200
        if action == "delete":
            state["cf"] = None
            return {"deleted": payload["name"]}, 200
        raise AssertionError(action)

    try:
        with mock.patch.object(A, "_manage_cf", fake_manage):
            res = _run(A.unwire_steps.handler({"name": "Flow", "from_step": "s1", "to_step": "s2"}))
            assert not res.get("is_error"), _txt(res)
            assert calls[0] == ("unwire", {"name": "Flow", "from_step": "s1", "to_step": "s2"})
            assert "verified by read-back" in _txt(res)
            res = _run(A.update_step_code.handler({"name": "Flow", "step_id": "s2", "code": "print(1)"}))
            assert not res.get("is_error") and ("update_step_code", {"name": "Flow", "step_id": "s2", "code": "print(1)"}) in calls
            res = _run(A.update_step_code.handler({"name": "Flow", "step_id": "s2", "code": "  "}))
            assert res.get("is_error") and "empty" in _txt(res)
            res = _run(A.remove_code_step.handler({"name": "Flow", "step_id": "s2"}))
            assert not res.get("is_error") and "1 step(s) remain" in _txt(res)
            n = len(calls)
            first = _run(A.delete_code_flow.handler({"name": "Flow"}))
            assert "CONFIRMATION REQUIRED" in _txt(first) and calls[n][0] == "get"
            assert not any(c[0] == "delete" for c in calls)
            second = _run(A.delete_code_flow.handler({"name": "Flow", "confirmed": True}))
            assert any(c[0] == "delete" for c in calls) and "Deleted code flow 'Flow'" in _txt(second)
    finally:
        CURRENT_USER.reset(tok)


def test_code_flow_editors_flag_unverified_and_gate_role():
    tok = _as(2)

    async def lying_manage(action, payload, timeout=900.0):
        if action == "get":
            return {"code_flow": _cf_state()}, 200        # edge still there
        return {"ok": True}, 200

    try:
        with mock.patch.object(A, "_manage_cf", lying_manage):
            res = _run(A.unwire_steps.handler({"name": "Flow", "from_step": "s1", "to_step": "s2"}))
            assert res.get("is_error") and "UNVERIFIED" in _txt(res)
            res = _run(A.remove_code_step.handler({"name": "Flow", "step_id": "s2"}))
            assert res.get("is_error") and "UNVERIFIED" in _txt(res)
    finally:
        CURRENT_USER.reset(tok)
    tok = _as(1)
    try:
        with mock.patch.dict(os.environ, {"AGENT_BUILD_ALLOW_ALL_USERS": "false"}):
            for h, args in ((A.unwire_steps.handler, {"name": "F", "from_step": "a", "to_step": "b"}),
                            (A.remove_code_step.handler, {"name": "F", "step_id": "a"}),
                            (A.update_step_code.handler, {"name": "F", "step_id": "a", "code": "x"}),
                            (A.delete_code_flow.handler, {"name": "F", "confirmed": True})):
                res = _run(h(args))
                assert res.get("is_error") and "Developer" in _txt(res)
    finally:
        CURRENT_USER.reset(tok)


# ---------------------------------------------------------------------------
# ask_agent / contact info / MCP list
# ---------------------------------------------------------------------------

def test_ask_agent_replaces_ask_data_agent_and_attributes_the_answer():
    names = {t.name for t in P.AIHUB_TOOLS}
    assert "ask_agent" in names and "ask_data_agent" not in names
    assert "get_my_contact_info" in names

    async def fake_post(path, body, timeout=None):
        assert path == "/api/agents/968/chat" and body["prompt"] == "hi"
        return {"response": "hello from 968"}, 200

    with mock.patch.object(P, "_post", fake_post):
        res = _run(P.ask_agent.handler({"agent_id": 968, "question": "hi"}))
        assert "Agent 968 answered" in _txt(res) and "hello from 968" in _txt(res)


def test_get_my_contact_info_reads_only_the_signed_in_user():
    tok = _as(2, uid=42)
    seen = {}

    class _Cur:
        def execute(self, sql, uid):
            seen["uid"] = uid
        def fetchone(self):
            return ("Ann Example", "ann@example.com", "", "ann")

    class _Conn:
        def cursor(self):
            return _Cur()
        def close(self):
            pass

    fake_rt = types.SimpleNamespace(_db=lambda: _Conn())
    try:
        with mock.patch.dict(sys.modules, {"readthrough": fake_rt}):
            res = _run(P.get_my_contact_info.handler({}))
        assert seen["uid"] == 42
        assert "ann@example.com" in _txt(res) and "(none on file)" in _txt(res)
    finally:
        CURRENT_USER.reset(tok)


def test_list_mcp_servers_formats_and_gates():
    servers = [{"server_id": 1, "server_name": "Filesystem", "server_type": "local",
                "enabled": True, "tool_count": 5, "status": "active", "agent_count": 2},
               {"server_id": 2, "server_name": "Remote X", "server_type": "remote",
                "enabled": False, "tool_count": None, "status": "inactive",
                "server_url": "https://mcp.example", "description": "desc"}]

    async def fake_get(path):
        assert path == "/api/mcp/servers"
        return servers

    tok = _as(2)
    try:
        with mock.patch.object(I, "_get", fake_get):
            res = _run(I.list_mcp_servers.handler({}))
            out = _txt(res)
            assert "MCP servers (2" in out and "Filesystem" in out and "(DISABLED)" in out
            assert "used by 2 agent(s)" in out
            res = _run(I.list_mcp_servers.handler({"server": "remote x"}))
            assert "https://mcp.example" in _txt(res) and "desc" in _txt(res)
            res = _run(I.list_mcp_servers.handler({"server": "nope"}))
            assert res.get("is_error") and "Existing servers" in _txt(res)
    finally:
        CURRENT_USER.reset(tok)
    tok = _as(1)
    try:
        res = _run(I.list_mcp_servers.handler({}))
        assert res.get("is_error") and "Developer" in _txt(res)
    finally:
        CURRENT_USER.reset(tok)


# ---------------------------------------------------------------------------
# send_email — role split, kill switch, attachments, honesty
# ---------------------------------------------------------------------------

def _patch_email(sends, items, addr):
    fake_store = types.SimpleNamespace(get_address=lambda uid: addr)

    async def fake_send(to, subject, body, from_address, from_name, html_body=None,
                        attachments=None):
        sends.append({"to": to, "subject": subject, "from": from_address,
                      "html": bool(html_body), "attachments": attachments})
        return {"success": True}

    fake_client = types.SimpleNamespace(send_reply=fake_send)
    fake_render = types.SimpleNamespace(html_enabled=lambda: True,
                                        render_email_with_view=lambda b, v, title="": "<p>x</p>")

    def create_item(kind, title, summary="", payload=None, **kw):
        items.append({"kind": kind, "title": title, "payload": payload, **kw})
        return {"work_item_id": "wi-1"}

    return [mock.patch.dict(sys.modules, {"email_store": fake_store,
                                          "email_client": fake_client,
                                          "email_render": fake_render}),
            mock.patch.object(WK.workitem_store, "create_item", create_item)]


def _enter(ps):
    for p in ps:
        p.start()


def _exit(ps):
    for p in ps:
        p.stop()


def test_send_email_validates_and_honors_outbound_kill_switch():
    tok = _as(2)
    sends, items = [], []
    ps = _patch_email(sends, items, {"is_active": 1, "outbound_enabled": 0,
                                     "email_address": "u7-agent.1@x.io", "prefix": "u7"})
    _enter(ps)
    try:
        res = _run(WK.send_email.handler({"to": ["not-an-address"], "subject": "s", "body": "b"}))
        assert res.get("is_error") and "Not a valid email" in _txt(res)
        res = _run(WK.send_email.handler({"to": ["a@b.co"], "subject": "", "body": "b"}))
        assert res.get("is_error")
        res = _run(WK.send_email.handler({"to": ["a@b.co"], "subject": "s", "body": "b"}))
        assert res.get("is_error") and "DISABLED" in _txt(res) and not sends
    finally:
        _exit(ps)
        CURRENT_USER.reset(tok)


def test_send_email_developer_sends_now_from_agent_address_or_platform_sender():
    tok = _as(2)
    sends, items = [], []
    ps = _patch_email(sends, items, {"is_active": 1, "outbound_enabled": 1,
                                     "email_address": "u7-agent.1@x.io", "prefix": "u7"})
    _enter(ps)
    try:
        res = _run(WK.send_email.handler({"to": ["a@b.co"], "subject": "Hi", "body": "**b**"}))
        assert not res.get("is_error"), _txt(res)
        assert "SENT" in _txt(res) and sends[0]["from"] == "u7-agent.1@x.io" and sends[0]["html"]
        assert items and items[0]["kind"] == "acknowledge"       # audit FYI
    finally:
        _exit(ps)
    sends, items = [], []
    ps = _patch_email(sends, items, None)                       # no personal address
    _enter(ps)
    try:
        res = _run(WK.send_email.handler({"to": ["a@b.co"], "subject": "Hi", "body": "b"}))
        assert not res.get("is_error") and "platform's default sender" in _txt(res)
        assert sends[0]["from"] == ""
    finally:
        _exit(ps)
        CURRENT_USER.reset(tok)


def test_send_email_regular_user_files_approval_or_asks_for_an_address():
    tok = _as(1)
    sends, items = [], []
    ps = _patch_email(sends, items, {"is_active": 1, "outbound_enabled": 1,
                                     "email_address": "u7-agent.1@x.io", "prefix": "u7"})
    _enter(ps)
    try:
        res = _run(WK.send_email.handler({"to": ["a@b.co"], "subject": "Hi", "body": "b"}))
        assert not res.get("is_error") and "NOTHING has been sent" in _txt(res)
        assert not sends and items[0]["kind"] == "edit_and_return"
        assert items[0]["payload"]["kind"] == "agent_email_reply"
        assert items[0]["payload"]["from_address"] == "u7-agent.1@x.io"
    finally:
        _exit(ps)
    sends, items = [], []
    ps = _patch_email(sends, items, None)
    _enter(ps)
    try:
        res = _run(WK.send_email.handler({"to": ["a@b.co"], "subject": "Hi", "body": "b"}))
        assert res.get("is_error") and "setup_agent_email" in _txt(res) and not sends and not items
    finally:
        _exit(ps)
        CURRENT_USER.reset(tok)


def test_send_email_attachment_is_resolved_and_encoded():
    tok = _as(2)
    sends, items = [], []
    d = tempfile.mkdtemp()
    fpath = os.path.join(d, "report.csv")
    with open(fpath, "wb") as fh:
        fh.write(b"a,b\n1,2\n")
    ps = _patch_email(sends, items, None)
    _enter(ps)
    try:
        res = _run(WK.send_email.handler({"to": ["a@b.co"], "subject": "Hi", "body": "b",
                                          "attach_file": fpath}))
        assert not res.get("is_error"), _txt(res)
        att = sends[0]["attachments"]
        assert att and att[0]["filename"] == "report.csv"
        # Windows maps .csv to the Excel type via the registry; both are fine.
        assert att[0]["content_type"] in ("text/csv", "application/vnd.ms-excel")
        import base64
        assert base64.b64decode(att[0]["content"]) == b"a,b\n1,2\n"
        res = _run(WK.send_email.handler({"to": ["a@b.co"], "subject": "Hi", "body": "b",
                                          "attach_file": os.path.join(d, "missing.pdf")}))
        assert res.get("is_error") and "Attachment not found" in _txt(res) and len(sends) == 1
    finally:
        _exit(ps)
        CURRENT_USER.reset(tok)


def test_send_email_reports_transport_failure_honestly():
    tok = _as(2)
    sends, items = [], []
    ps = _patch_email(sends, items, None)
    _enter(ps)

    async def failing(*a, **kw):
        return {"success": False, "error": "mailgun 500"}

    try:
        with mock.patch.object(sys.modules["email_client"], "send_reply", failing):
            res = _run(WK.send_email.handler({"to": ["a@b.co"], "subject": "Hi", "body": "b"}))
            assert res.get("is_error") and "NOT sent" in _txt(res) and "mailgun 500" in _txt(res)
            assert not items
    finally:
        _exit(ps)
        CURRENT_USER.reset(tok)


def test_build_email_attachments_fails_closed_on_missing_file():
    d = tempfile.mkdtemp()
    fpath = os.path.join(d, "x.txt")
    open(fpath, "wb").write(b"hi")
    out, err = WK.build_email_attachments([{"path": fpath, "filename": "x.txt"}])
    assert err is None and out[0]["content_type"] == "text/plain"
    out, err = WK.build_email_attachments([{"path": os.path.join(d, "gone.txt")}])
    assert out is None and "no longer exists" in err


if __name__ == "__main__":
    if not HAVE_SDK:
        print(f"SKIP-ALL: {_IMPORT_ERR}")
        sys.exit(0)
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in fns:
        try:
            f()
            print(f"PASS  {n}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {n}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {n}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
