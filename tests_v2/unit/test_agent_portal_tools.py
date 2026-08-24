"""The Agent portal tools (P1, docs/the-agent-portal-gap-analysis.md) — unit tests.

No live services and no LLM: the CC client cores (start_portal_fetch /
get_portal_result / cobrowse_link / run_workflow_by_name) and the stores
(portal_registry / local_secrets) are monkeypatched on their REAL module
objects — the tool bodies import them lazily per call, so setattr is picked
up. File staging is exercised for real against a temp file under APP_ROOT.

Runs standalone (C:\\Users\\james\\miniconda3\\envs\\aihub-agent\\python.exe
test_agent_portal_tools.py) or under pytest in an env with claude_agent_sdk;
in an env WITHOUT the SDK (main-app pytest sweep) every test self-skips.
"""
import asyncio
import os
import shutil
import sys
import uuid

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import brain                     # noqa: E402
    import file_tools                # noqa: E402
    import portal_tools              # noqa: E402
    import portal_watch              # noqa: E402
    from platform_tools import CURRENT_USER  # noqa: E402
    from command_center.tools import portal_fetch as cc_pf        # noqa: E402
    from command_center.tools import portal_registry as cc_reg    # noqa: E402
    from command_center.tools import portal_workflow_run as cc_wfr  # noqa: E402
    from command_center.tools import portal_workflows as cc_wf    # noqa: E402
    import local_secrets             # noqa: E402
    import workitem_store            # noqa: E402
    HAVE_SDK = True
except ImportError as e:             # main-env pytest sweep: no claude_agent_sdk
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(
            reason=f"needs the aihub-agent env (claude_agent_sdk): {_IMPORT_ERR}")
    except ImportError:
        pass

TEST_UID = 987654  # never a real user on this box


class patched:
    """Set attrs on an object for the duration of a block, then restore."""

    def __init__(self, obj, **attrs):
        self.obj, self.attrs, self.saved = obj, attrs, {}

    def __enter__(self):
        for k, v in self.attrs.items():
            self.saved[k] = getattr(self.obj, k)
            setattr(self.obj, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            setattr(self.obj, k, v)
        return False


def _run(tool_obj, args):
    return asyncio.run(tool_obj.handler(args))


def _txt(res):
    return res["content"][0]["text"]


def _as_user(role=3):
    CURRENT_USER.set({"user_id": TEST_UID, "role": role,
                      "username": "portal-unit", "tenant_id": ""})


def _tool(name):
    return {getattr(t, "name", ""): t for t in portal_tools.PORTAL_TOOLS}[name]


_SAVED_ENTRY = {"slug": "acme", "name": "Acme", "url": "http://portal.local/login",
                "username_secret": "U_K", "password_secret": "P_K",
                "totp_secret": None, "allowed_domains": []}


def _cleanup_user_files():
    d = os.path.join(APP_ROOT, "data", "agent", "users", str(TEST_UID))
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- registration

def test_registration_and_lists():
    names = [getattr(t, "name", "") for t in portal_tools.PORTAL_TOOLS]
    assert names == ["lookup_portal", "save_portal", "portal_fetch",
                     "check_portal_run", "list_portal_workflows",
                     "describe_portal_workflow", "run_portal_workflow",
                     "schedule_portal_workflow", "cancel_portal_workflow_schedule"]
    for n in ("portal_fetch", "save_portal", "run_portal_workflow",
              "schedule_portal_workflow", "cancel_portal_workflow_schedule"):
        assert n in brain.MUTATING_TOOLS, n
    for n in ("lookup_portal", "list_portal_workflows", "describe_portal_workflow"):
        assert n in brain._READ_TOOL_NAMES, n
    assert brain.SENSITIVE_TOOL_FIELDS["save_portal"] == ("password", "totp")
    assert brain.SENSITIVE_TOOL_FIELDS["portal_fetch"] == ("password", "totp")
    assert "WEB PORTALS" in brain.SYSTEM_PROMPT
    assert "lookup_portal FIRST" in brain.SYSTEM_PROMPT


def test_role_gate_denies_below_dev():
    _as_user(role=1)
    with patched(portal_tools, _ALLOW_ALL=False):
        res = _run(_tool("lookup_portal"), {})
    assert res.get("is_error") and "Developer role" in _txt(res)


# ------------------------------------------------------------------- lookups

def test_lookup_lists_saved_portals():
    _as_user()
    with patched(cc_reg,
                 list_portals=lambda uid: [{"name": "Acme", "slug": "acme",
                                            "url": "http://portal.local/login"}]):
        res = _run(_tool("lookup_portal"), {})
    t = _txt(res)
    assert "Acme -> http://portal.local/login" in t and not res.get("is_error")


def test_lookup_miss_offers_adhoc():
    _as_user()
    with patched(cc_reg, lookup_portal=lambda uid, n: None,
                 list_portals=lambda uid: []):
        res = _run(_tool("lookup_portal"), {"name": "nope"})
    assert "No saved portal matches" in _txt(res)
    assert "ad-hoc" in _txt(res)


# --------------------------------------------------------------- portal_fetch

def test_fetch_saved_portal_stages_download():
    _as_user()
    tmp_dir = os.path.join(APP_ROOT, "temp")
    os.makedirs(tmp_dir, exist_ok=True)
    src = os.path.join(tmp_dir, f"portal_unit_{uuid.uuid4().hex[:8]}.txt")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("invoice bytes")
    calls = {}

    def fake_start(portal_name, start_url, task, session_id, user_context,
                   overrides, inline, upload_files=None):
        calls["start"] = {"url": start_url, "overrides": overrides,
                          "inline": inline, "uploads": upload_files}
        return {"run_id": "r-happy"}

    try:
        with patched(cc_pf, start_portal_fetch=fake_start,
                     get_portal_result=lambda rid, t=15: {
                         "done": True, "status": "ok", "files": [src],
                         "final_result": "downloaded the latest invoice"}), \
             patched(portal_tools, WAIT_SECONDS=10):
            with patched(cc_reg, lookup_portal=lambda uid, n: dict(_SAVED_ENTRY)):
                res = _run(_tool("portal_fetch"),
                           {"portal_name": "acme", "task": "download the invoice"})
        t = _txt(res)
        assert "/api/files/" in t, t                      # staged link, not a path
        assert "VERBATIM" in t and not res.get("is_error")
        assert src not in t                                # never leak server path? (link only)
        # saved mode: credentials went as KEY NAMES, no inline values
        assert calls["start"]["overrides"]["password_secret"] == "P_K"
        assert calls["start"]["inline"] is None
        assert calls["start"]["url"] == "http://portal.local/login"
    finally:
        if os.path.isfile(src):
            os.remove(src)
        _cleanup_user_files()


def test_fetch_needs_human_returns_takeover_link_immediately():
    _as_user()
    with patched(cc_pf,
                 start_portal_fetch=lambda *a, **k: {"run_id": "r-2fa"},
                 get_portal_result=lambda rid, t=15: {
                     "done": False, "needs_human": True, "reason": "a 2FA code"},
                 cobrowse_link=lambda rid: f"http://main/portal-workflows/cobrowse/{rid}"), \
         patched(portal_tools, WAIT_SECONDS=10), patched(portal_watch, ENABLED=False):
        with patched(cc_reg, lookup_portal=lambda uid, n: dict(_SAVED_ENTRY)):
            res = _run(_tool("portal_fetch"),
                       {"portal_name": "acme", "task": "download"})
    t = _txt(res)
    assert "PAUSED" in t
    assert "http://main/portal-workflows/cobrowse/r-2fa" in t
    assert "run_id: r-2fa" in t and "check_portal_run" in t
    assert "do NOT claim" in t or "Do NOT claim" in t
    # (the watch-ON default — result auto-delivered to the conversation instead
    #  of "say when you're done" — is covered by test_agent_portal_watch.py)


def test_fetch_budget_exhausted_is_honest():
    _as_user()
    with patched(cc_pf,
                 start_portal_fetch=lambda *a, **k: {"run_id": "r-slow"},
                 get_portal_result=lambda rid, t=15: {"done": False}), \
         patched(portal_tools, WAIT_SECONDS=0), patched(portal_watch, ENABLED=False):
        with patched(cc_reg, lookup_portal=lambda uid, n: dict(_SAVED_ENTRY)):
            res = _run(_tool("portal_fetch"),
                       {"portal_name": "acme", "task": "download"})
    t = _txt(res)
    assert "NOT finished" in t and "run_id: r-slow" in t
    assert "check_portal_run" in t
    assert "Do NOT tell the user the file is downloading" in t


def test_fetch_without_url_asks_honestly():
    _as_user()
    with patched(cc_reg, lookup_portal=lambda uid, n: None):
        res = _run(_tool("portal_fetch"), {"portal_name": "mystery", "task": "dl"})
    assert res.get("is_error") and "don't have a login URL" in _txt(res)


def test_fetch_gone_run_gives_up_after_strikes():
    _as_user()
    with patched(cc_pf,
                 start_portal_fetch=lambda *a, **k: {"run_id": "r-gone"},
                 get_portal_result=lambda rid, t=15: {"done": False,
                                                      "error": "service returned 404"}), \
         patched(portal_tools, WAIT_SECONDS=60):
        with patched(cc_reg, lookup_portal=lambda uid, n: dict(_SAVED_ENTRY)):
            res = _run(_tool("portal_fetch"),
                       {"portal_name": "acme", "task": "download"})
    assert res.get("is_error") and "no longer active" in _txt(res)


# ------------------------------------------------------------------- uploads

def test_upload_refusals():
    ap, reason = portal_tools._upload_refusal(os.path.join(APP_ROOT, ".env"))
    assert ap is None and "credential store" in reason
    ap, reason = portal_tools._upload_refusal(
        os.path.join(APP_ROOT, "data", "secrets", "secrets.json.enc"))
    assert ap is None and "credential store" in reason
    ap, reason = portal_tools._upload_refusal(
        os.path.join(APP_ROOT, "no_such_file_9x8y7z.bin"))
    assert ap is None and "couldn't find" in reason
    _as_user()
    res = _run(_tool("portal_fetch"), {"portal_name": "acme", "task": "up",
                                       "upload_file": os.path.join(APP_ROOT, ".env")})
    assert res.get("is_error") and "Upload refused" in _txt(res)


# ----------------------------------------------------------------- _finish_run

def test_finish_run_honest_texts():
    ok_nofile = portal_tools._finish_run({"status": "ok", "files": []}, TEST_UID)
    assert "NO file was captured" in _txt(ok_nofile)
    assert "do NOT claim" in _txt(ok_nofile)

    failed = portal_tools._finish_run({"status": "error", "error": "login failed"},
                                      TEST_UID)
    assert failed.get("is_error") and "login failed" in _txt(failed)

    up_ok = portal_tools._finish_run({"status": "ok", "files": [],
                                      "is_upload": True,
                                      "final_result": "uploaded fine"}, TEST_UID)
    assert "Upload completed" in _txt(up_ok) and not up_ok.get("is_error")

    up_bad = portal_tools._finish_run({"status": "error", "is_upload": True,
                                       "error": "input not found"}, TEST_UID)
    assert up_bad.get("is_error") and "did NOT complete" in _txt(up_bad)


# ------------------------------------------- screen reading (James 2026-08-22)
# Read-only portal tasks: the browser agent's on-screen reading arrives as
# final_result and must be relayed — framed as a reading, never as a document.

def test_finish_run_relays_page_reading_when_no_files():
    res = portal_tools._finish_run(
        {"status": "ok", "files": [],
         "final_result": "The current balance shown on the account page is $12,345.67"},
        TEST_UID)
    t = _txt(res)
    assert "$12,345.67" in t
    assert "READING" in t and "NOT a downloaded document" in t
    assert not res.get("is_error")
    assert "/api/files/" not in t                       # never invents a download


def test_finish_run_no_files_no_reading_keeps_honest_text():
    res = portal_tools._finish_run({"status": "ok", "files": [], "final_result": ""},
                                   TEST_UID)
    assert "NO file was captured" in _txt(res)


def test_finish_run_download_branch_unchanged_by_reading():
    tmp_dir = os.path.join(APP_ROOT, "temp")
    os.makedirs(tmp_dir, exist_ok=True)
    src = os.path.join(tmp_dir, f"dl_{uuid.uuid4().hex[:8]}.txt")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("statement")
    try:
        res = portal_tools._finish_run(
            {"status": "ok", "files": [src], "final_result": "downloaded it"}, TEST_UID)
        t = _txt(res)
        assert "/api/files/" in t and "Browser agent's note" in t
        assert "READING" not in t
    finally:
        os.remove(src)
        _cleanup_user_files()


# ------------------------------------------- P2: schedule / cancel / takeover / uploads

def _sched_router(calls, jobs_list, job_detail, post_id=777):
    """MockTransport handler for the scheduler REST the tools drive."""
    import json as _json
    import httpx as _hx
    deleted = set()

    def handler(request):
        p = request.url.path
        calls.append((request.method, p))
        if request.method == "GET" and p == "/api/scheduler/jobs":
            return _hx.Response(200, json=jobs_list)
        if request.method == "GET" and p.startswith("/api/scheduler/jobs/"):
            jid = int(p.rsplit("/", 1)[1])
            if jid in deleted:
                return _hx.Response(404, json={"error": "not found"})
            return _hx.Response(200, json=job_detail.get(
                jid, {"schedules": [{"is_active": True}], "parameters": {}}))
        if request.method == "POST" and p == "/api/scheduler/jobs":
            calls.append(("BODY", _json.loads(request.content)))
            return _hx.Response(200, json={"id": post_id})
        if request.method == "DELETE" and p.startswith("/api/scheduler/jobs/"):
            deleted.add(int(p.rsplit("/", 1)[1]))
            return _hx.Response(200, json={})
        return _hx.Response(404, json={})
    return handler


def _client_factory(handler):
    import httpx as _hx
    _Real = _hx.AsyncClient

    def factory(**kw):
        return _Real(transport=_hx.MockTransport(handler))
    return _hx, factory


_WF = {"slug": "acme_dl", "name": "Acme DL", "steps": [{"type": "goto", "url": "http://x"}]}
_EXISTING = {5: {"parameters": {"workflow_slug": {"value": "acme_dl"},
                                "user_id": {"value": str(TEST_UID)}},
                 "schedules": [{"is_active": True, "next_run_time": "2026-09-01T06:00:00"}]}}


def test_schedule_portal_workflow_creates_verified_job():
    _as_user(role=3)
    calls = []
    _hx, factory = _client_factory(_sched_router(calls, [], {}))
    with patched(cc_wf, get_workflow=lambda uid, n: dict(_WF)), \
         patched(_hx, AsyncClient=factory):
        res = _run(_tool("schedule_portal_workflow"),
                   {"name": "acme dl", "every_days": 1})
    t = _txt(res)
    assert not res.get("is_error"), t
    assert "job #777" in t and "verified active" in t
    body = next(c[1] for c in calls if c[0] == "BODY")
    assert body["type"] == "portal_workflow"
    assert body["parameters"]["workflow_slug"]["value"] == "acme_dl"
    assert body["parameters"]["user_id"]["value"] == str(TEST_UID)
    assert body["schedule"]["type"] == "interval" and body["schedule"]["start_date"]


def test_schedule_portal_workflow_replaces_existing():
    _as_user(role=3)
    calls = []
    _hx, factory = _client_factory(_sched_router(
        calls, [{"id": 5, "type": "portal_workflow"}], dict(_EXISTING)))
    with patched(cc_wf, get_workflow=lambda uid, n: dict(_WF)), \
         patched(_hx, AsyncClient=factory):
        res = _run(_tool("schedule_portal_workflow"),
                   {"name": "acme dl", "cron_expression": "0 6 * * 1",
                    "timezone": "UTC"})
    t = _txt(res)
    assert ("DELETE", "/api/scheduler/jobs/5") in calls
    assert "Replaced previous job(s) #5" in t and "job #777" in t


def test_cancel_portal_schedule_two_step_verified():
    _as_user(role=3)
    calls = []
    _hx, factory = _client_factory(_sched_router(
        calls, [{"id": 5, "type": "portal_workflow"}], dict(_EXISTING)))
    with patched(cc_wf, get_workflow=lambda uid, n: dict(_WF)), \
         patched(_hx, AsyncClient=factory):
        first = _run(_tool("cancel_portal_workflow_schedule"), {"name": "acme dl"})
        assert "confirm" in _txt(first).lower() and \
            ("DELETE", "/api/scheduler/jobs/5") not in calls
        second = _run(_tool("cancel_portal_workflow_schedule"),
                      {"name": "acme dl", "confirmed": True})
    assert "removed job(s) #5" in _txt(second) and "verified gone" in _txt(second)


def test_cancel_when_nothing_scheduled_is_plain():
    _as_user(role=3)
    _hx, factory = _client_factory(_sched_router([], [], {}))
    with patched(cc_wf, get_workflow=lambda uid, n: dict(_WF)), \
         patched(_hx, AsyncClient=factory):
        res = _run(_tool("cancel_portal_workflow_schedule"),
                   {"name": "acme dl", "confirmed": True})
    assert "No schedule exists" in _txt(res)


def test_headless_takeover_raises_work_item():
    CURRENT_USER.set({"user_id": TEST_UID, "role": 3, "username": "portal-unit",
                      "tenant_id": "", "mode": "headless"})
    made = {}

    def fake_create(verb, title, **kw):
        made.update({"verb": verb, "title": title, **kw})
        return {"work_item_id": 42, "title": title, "verb": verb}

    with patched(workitem_store, create_item=fake_create), \
         patched(cc_pf,
                 start_portal_fetch=lambda *a, **k: {"run_id": "r-h"},
                 get_portal_result=lambda rid, t=15: {"done": False, "needs_human": True,
                                                      "reason": "a 2FA code"},
                 cobrowse_link=lambda rid: f"http://main/portal-workflows/cobrowse/{rid}"), \
         patched(portal_tools, WAIT_SECONDS=10):
        with patched(cc_reg, lookup_portal=lambda uid, n: dict(_SAVED_ENTRY)):
            res = _run(_tool("portal_fetch"), {"portal_name": "acme", "task": "dl"})
    t = _txt(res)
    assert "My Work item (#42)" in t
    assert made["verb"] == "do_offline" and made["payload"]["kind"] == "portal_takeover"
    assert made["addressed_user"] == TEST_UID and "cobrowse/r-h" in made["summary"]


def test_interactive_takeover_raises_nothing():
    _as_user(role=3)  # no mode key = interactive chat
    made = []
    with patched(workitem_store, create_item=lambda *a, **k: made.append(1) or {"work_item_id": 1}), \
         patched(cc_pf,
                 start_portal_fetch=lambda *a, **k: {"run_id": "r-i"},
                 get_portal_result=lambda rid, t=15: {"done": False, "needs_human": True},
                 cobrowse_link=lambda rid: "http://main/cobrowse/r-i"), \
         patched(portal_tools, WAIT_SECONDS=10):
        with patched(cc_reg, lookup_portal=lambda uid, n: dict(_SAVED_ENTRY)):
            res = _run(_tool("portal_fetch"), {"portal_name": "acme", "task": "dl"})
    assert not made and "PAUSED" in _txt(res)


def test_chat_upload_helpers_roundtrip_and_cap():
    try:
        fid, path, size = file_tools.save_upload(TEST_UID, "Q3 report (final).csv", b"a,b\n1,2\n")
        assert size == 8 and os.path.isfile(path)
        assert os.sep + str(TEST_UID) + os.sep + "uploads" in path
        hit = file_tools.resolve_upload(TEST_UID, fid)
        assert hit and hit[0] == path and hit[1].endswith(".csv")
        assert file_tools.resolve_upload(424299, fid) is None       # owner-scoped
        assert any(u["file_id"] == fid for u in file_tools.list_uploads(TEST_UID))
        block = file_tools.attachments_prompt_block(TEST_UID, [fid, "bogus"])
        assert path in block and "never show" in block
        assert file_tools.attachments_prompt_block(TEST_UID, ["bogus"]) == ""
        with patched(file_tools, UPLOAD_MAX_MB=0):
            try:
                file_tools.save_upload(TEST_UID, "big.bin", b"x")
                assert False, "cap not enforced"
            except ValueError as e:
                assert "upload cap" in str(e)
    finally:
        _cleanup_user_files()


# ------------------------------------------------------------ check_portal_run

def test_check_requires_run_id():
    _as_user()
    res = _run(_tool("check_portal_run"), {})
    assert res.get("is_error") and "No run_id" in _txt(res)


# --------------------------------------------------------- run_portal_workflow

def test_run_workflow_missing_names_hint():
    _as_user()
    with patched(cc_wfr, run_workflow_by_name=lambda *a, **k: {
            "status": "error", "error": "no saved workflow named 'x'",
            "files": [], "blocks": [], "file_count": 0, "final_result": None,
            "steps": []}):
        res = _run(_tool("run_portal_workflow"), {"name": "x"})
    assert res.get("is_error") and "list_portal_workflows" in _txt(res)


def test_run_workflow_upload_flag_passes_inputs():
    _as_user()
    tmp_dir = os.path.join(APP_ROOT, "temp")
    os.makedirs(tmp_dir, exist_ok=True)
    src = os.path.join(tmp_dir, f"portal_up_{uuid.uuid4().hex[:8]}.csv")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("a,b\n1,2\n")
    seen = {}

    def fake_run(name, session_id, user_context, timeout, inputs=None, **kw):
        seen["inputs"] = inputs
        return {"status": "ok", "is_upload": True, "files": [],
                "final_result": "uploaded", "error": None}

    try:
        with patched(cc_wfr, run_workflow_by_name=fake_run):
            res = _run(_tool("run_portal_workflow"),
                       {"name": "acme upload", "upload_file": src})
        assert seen["inputs"] == {"files": [os.path.abspath(src)]}
        assert "Upload completed" in _txt(res)
    finally:
        if os.path.isfile(src):
            os.remove(src)


# --------------------------------------------- delivered-file follow-ups
# (James's Alpaca repro 2026-08-21: after delivering a download link, the
# model's only handle was /api/files/<id> — import_documents errored on it
# and the model hunted the filesystem.)

def test_stage_offer_returns_staged_path():
    tmp_dir = os.path.join(APP_ROOT, "temp")
    os.makedirs(tmp_dir, exist_ok=True)
    src = os.path.join(tmp_dir, f"stage_{uuid.uuid4().hex[:8]}.txt")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("x")
    try:
        ok, link, path = file_tools.stage_offer(TEST_UID, src)
        assert ok and "/api/files/" in link
        assert path and os.path.isfile(path)
        assert os.sep + str(TEST_UID) + os.sep in path  # per-user dir
    finally:
        os.remove(src)
        _cleanup_user_files()


def test_resolve_api_files_ref_owner_scoped():
    tmp_dir = os.path.join(APP_ROOT, "temp")
    os.makedirs(tmp_dir, exist_ok=True)
    src = os.path.join(tmp_dir, f"ref_{uuid.uuid4().hex[:8]}.pdf")
    with open(src, "wb") as fh:
        fh.write(b"%PDF-1.4 fake")
    try:
        ok, link, _p = file_tools.stage_offer(TEST_UID, src)
        assert ok
        import re
        fid = re.search(r"/api/files/([a-f0-9-]+)", link).group(1)
        # link form and bare-id form both resolve for the owner
        for ref in (f"/api/files/{fid}", fid, f"see [file](/api/files/{fid})"):
            path, name = file_tools.resolve_api_files_ref(ref, TEST_UID)
            assert path and os.path.isfile(path) and name.endswith(".pdf"), ref
        # another user's id resolves to nothing (fail closed)
        assert file_tools.resolve_api_files_ref(f"/api/files/{fid}", 424299) == (None, None)
        # garbage never resolves
        assert file_tools.resolve_api_files_ref("no ref here", TEST_UID) == (None, None)
        assert file_tools.resolve_api_files_ref("/api/files/not-a-uuid", TEST_UID) == (None, None)
    finally:
        os.remove(src)
        _cleanup_user_files()


def test_finish_run_gives_model_server_copies_line():
    tmp_dir = os.path.join(APP_ROOT, "temp")
    os.makedirs(tmp_dir, exist_ok=True)
    src = os.path.join(tmp_dir, f"copies_{uuid.uuid4().hex[:8]}.txt")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("statement")
    try:
        res = portal_tools._finish_run({"status": "ok", "files": [src]}, TEST_UID)
        t = _txt(res)
        assert "/api/files/" in t
        assert "Server copies" in t and "NEVER show a raw path" in t
        assert os.path.join("users", str(TEST_UID), "downloads") in t
    finally:
        os.remove(src)
        _cleanup_user_files()


def test_upload_refusal_accepts_api_files_ref():
    _as_user()
    tmp_dir = os.path.join(APP_ROOT, "temp")
    os.makedirs(tmp_dir, exist_ok=True)
    src = os.path.join(tmp_dir, f"up_{uuid.uuid4().hex[:8]}.csv")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("a\n")
    try:
        ok, link, staged = file_tools.stage_offer(TEST_UID, src)
        assert ok
        import re
        fid = re.search(r"/api/files/([a-f0-9-]+)", link).group(1)
        path, err = portal_tools._upload_refusal(f"/api/files/{fid}")
        assert err is None and path == staged
    finally:
        os.remove(src)
        _cleanup_user_files()


def test_import_documents_resolves_api_files_ref():
    import document_tools
    import httpx as _httpx
    _Real = _httpx.AsyncClient
    _as_user()
    tmp_dir = os.path.join(APP_ROOT, "temp")
    os.makedirs(tmp_dir, exist_ok=True)
    src = os.path.join(tmp_dir, f"imp_{uuid.uuid4().hex[:8]}.txt")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("Ending balance: $12,345.67")
    try:
        ok, link, staged = file_tools.stage_offer(TEST_UID, src)
        assert ok
        posted = {}

        def handler(request):
            posted["url"] = str(request.url)
            return _httpx.Response(200, json={"status": "success",
                                              "document_id": 42,
                                              "page_count": 1})

        def factory(**kw):
            return _Real(transport=_httpx.MockTransport(handler))

        with patched(_httpx, AsyncClient=factory):
            # force=true skips the dedupe GET, so only /document/process fires
            res = _run({t.name: t for t in
                        document_tools.DOCUMENT_TOOLS}["import_documents"],
                       {"path": link, "force": True})
        t = _txt(res)
        assert not res.get("is_error"), t
        assert "Imported 1" in t and "document/process" in posted["url"]
        # unknown / other-owner ref fails honestly, no filesystem hunt hint
        bogus = "/api/files/00000000-0000-0000-0000-000000000000"
        res2 = _run({t2.name: t2 for t2 in
                     document_tools.DOCUMENT_TOOLS}["import_documents"],
                    {"path": bogus})
        assert res2.get("is_error") and "doesn't match any download" in _txt(res2)
    finally:
        os.remove(src)
        _cleanup_user_files()


# ------------------------------------------- doc-stack busy honesty (2026-08-21)
# Under concurrent doc work the stack queues past client timeouts; a raw
# ReadTimeout traceback read like an outage and caused retry storms. The tools
# must say "busy queue, retry once in a minute" instead.

def _timeout_client_factory():
    import httpx as _hx
    _Real = _hx.AsyncClient

    def handler(request):
        raise _hx.ReadTimeout("read timed out")

    def factory(**kw):
        return _Real(transport=_hx.MockTransport(handler))
    return _hx, factory


def test_search_busy_timeout_is_honest():
    import document_tools
    _hx, factory = _timeout_client_factory()
    _as_user()
    with patched(_hx, AsyncClient=factory):
        res = _run({t.name: t for t in document_tools.DOCUMENT_TOOLS}
                   ["search_documents"], {"query": "anything"})
    t = _txt(res)
    assert res.get("is_error") and "BUSY" in t and "not an outage" in t
    assert "Traceback" not in t


def test_records_busy_timeout_is_honest():
    import document_tools
    _hx, factory = _timeout_client_factory()
    _as_user()
    with patched(_hx, AsyncClient=factory):
        res = _run({t.name: t for t in document_tools.DOCUMENT_TOOLS}
                   ["query_document_records"], {"record_set": "x"})
    t = _txt(res)
    assert res.get("is_error") and "BUSY" in t


def test_import_timeout_gives_verify_guidance():
    import document_tools
    _hx, factory = _timeout_client_factory()
    _as_user()
    tmp_dir = os.path.join(APP_ROOT, "temp")
    os.makedirs(tmp_dir, exist_ok=True)
    src = os.path.join(tmp_dir, f"busy_{uuid.uuid4().hex[:8]}.txt")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write("x")
    try:
        with patched(_hx, AsyncClient=factory):
            res = _run({t.name: t for t in document_tools.DOCUMENT_TOOLS}
                       ["import_documents"], {"path": src, "force": True})
        t = _txt(res)
        assert "may still complete" in t and "idempotent" in t
    finally:
        os.remove(src)


# ----------------------------------------------------------------- save_portal

def test_save_portal_readback_verified_and_no_echo():
    _as_user()
    with patched(cc_reg,
                 save_portal=lambda uid, n, u, us, pw, totp, doms: {
                     "slug": "acme", "name": "Acme", "url": "http://portal.local"},
                 lookup_portal=lambda uid, n: dict(_SAVED_ENTRY)), \
         patched(local_secrets, get_local_secret=lambda k, *a, **kw: "DECRYPTS"):
        res = _run(_tool("save_portal"),
                   {"name": "Acme", "url": "http://portal.local",
                    "username": "ap@corp.com", "password": "hunter2-secret",
                    "totp": "JBSWY3DP"})
    t = _txt(res)
    assert "read-back verified" in t and not res.get("is_error")
    assert "hunter2-secret" not in t and "JBSWY3DP" not in t and "DECRYPTS" not in t


def test_save_portal_readback_failure_is_loud():
    _as_user()
    with patched(cc_reg,
                 save_portal=lambda uid, n, u, us, pw, totp, doms: {
                     "slug": "acme", "name": "Acme", "url": "http://portal.local"},
                 lookup_portal=lambda uid, n: dict(_SAVED_ENTRY)), \
         patched(local_secrets, get_local_secret=lambda k, *a, **kw: None):
        res = _run(_tool("save_portal"),
                   {"name": "Acme", "url": "http://portal.local",
                    "username": "u", "password": "p"})
    assert res.get("is_error") and "did NOT verify" in _txt(res)


# -------------------------------------------------------------------- auto-naming (2026-08-23)

def test_clean_name_sanitizes():
    assert portal_tools._clean_name('"Master Price List"') == "Master Price List"
    assert portal_tools._clean_name("Download Invoice.\n\nblah") == "Download Invoice"
    assert portal_tools._clean_name("`Vendor Portal Export`;") == "Vendor Portal Export"
    assert portal_tools._clean_name("one two three four five six seven eight") \
        == "one two three four five six"          # 6-word cap
    assert portal_tools._clean_name("   ") == ""
    assert portal_tools._clean_name("###") == ""   # not sluggable


class _FakeResp:
    def __init__(self, status, text_out=""):
        self.status_code = status
        self._t = text_out

    def json(self):
        return {"content": [{"type": "text", "text": self._t}]}


def test_suggest_name_uses_haiku_when_available():
    import httpx
    captured = {}

    def fake_post(url, **kw):
        captured["url"] = url
        captured["model"] = kw.get("json", {}).get("model")
        return _FakeResp(200, "Master Price List Download")
    with patched(portal_tools, NAMING_ENABLED=True), \
         patched(os, environ={**os.environ, "ANTHROPIC_API_KEY": "k",
                              "ANTHROPIC_BASE_URL": "http://relay.local/api/agent-llm"}), \
         patched(httpx, post=fake_post):
        name = portal_tools._suggest_workflow_name(
            "download the master price list", "http://portal.local/login",
            [{"type": "goto"}, {"type": "login"}])
    assert name == "Master Price List Download"
    assert captured["url"].endswith("/v1/messages")
    assert captured["model"] == portal_tools.NAMING_MODEL


def test_suggest_name_falls_back_on_non_200_or_disabled():
    import httpx
    with patched(portal_tools, NAMING_ENABLED=True), \
         patched(os, environ={**os.environ, "ANTHROPIC_API_KEY": "k"}), \
         patched(httpx, post=lambda url, **kw: _FakeResp(404, "")):
        assert portal_tools._suggest_workflow_name("do a thing", "http://x/login", []) == ""
    # disabled -> no call at all
    with patched(portal_tools, NAMING_ENABLED=False):
        assert portal_tools._suggest_workflow_name("do a thing", "http://x/login", []) == ""


def test_autosave_prefers_llm_name_and_marks_auto_record():
    from command_center.tools import portal_workflows as wf
    seen = {}

    def fake_save(uid, name, steps, portal_slug=None, start_url=None, goal=None, **kw):
        seen.update(name=name, auto_record=kw.get("auto_record"), goal=goal)
        return {"slug": wf.slug(name), "name": name, "step_count": len(steps)}
    res = {"draft_workflow": {"name": "Recorded workflow", "goal": "download the master price list",
                              "start_url": "http://portal.local/login",
                              "steps": [{"type": "goto"}, {"type": "login"}]}}
    with patched(portal_tools, _suggest_workflow_name=lambda *a, **k: "Master Price List"), \
         patched(wf, save_workflow=fake_save):
        note = portal_tools._autosave_draft(res, TEST_UID)
    assert seen["name"] == "Master Price List" and seen["auto_record"] is True
    assert "Master Price List" in note


def test_autosave_falls_back_to_store_goal_naming_when_llm_blank():
    from command_center.tools import portal_workflows as wf
    seen = {}

    def fake_save(uid, name, steps, portal_slug=None, start_url=None, goal=None, **kw):
        # the store would derive from goal; here we just confirm the generic
        # placeholder was passed through (not a hallucinated name) with the goal
        seen.update(name=name, goal=goal, auto_record=kw.get("auto_record"))
        return {"slug": "download_the_master_price_list",
                "name": wf.derive_name_from_goal(goal), "step_count": len(steps)}
    res = {"draft_workflow": {"name": "Recorded workflow", "goal": "download the master price list",
                              "start_url": "http://portal.local/login",
                              "steps": [{"type": "goto"}]}}
    with patched(portal_tools, _suggest_workflow_name=lambda *a, **k: ""), \
         patched(wf, save_workflow=fake_save):
        note = portal_tools._autosave_draft(res, TEST_UID)
    assert seen["name"] == "Recorded workflow" and seen["auto_record"] is True
    assert "Download the master price list" in note   # store-derived name surfaced


# -------------------------------------------------------------------- runner

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
    _cleanup_user_files()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
