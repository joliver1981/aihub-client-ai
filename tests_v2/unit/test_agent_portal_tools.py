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
    from platform_tools import CURRENT_USER  # noqa: E402
    from command_center.tools import portal_fetch as cc_pf        # noqa: E402
    from command_center.tools import portal_registry as cc_reg    # noqa: E402
    from command_center.tools import portal_workflow_run as cc_wfr  # noqa: E402
    import local_secrets             # noqa: E402
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
                     "describe_portal_workflow", "run_portal_workflow"]
    for n in ("portal_fetch", "save_portal", "run_portal_workflow"):
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
         patched(portal_tools, WAIT_SECONDS=10):
        with patched(cc_reg, lookup_portal=lambda uid, n: dict(_SAVED_ENTRY)):
            res = _run(_tool("portal_fetch"),
                       {"portal_name": "acme", "task": "download"})
    t = _txt(res)
    assert "PAUSED" in t
    assert "http://main/portal-workflows/cobrowse/r-2fa" in t
    assert "run_id: r-2fa" in t and "check_portal_run" in t
    assert "do NOT claim" in t or "Do NOT claim" in t


def test_fetch_budget_exhausted_is_honest():
    _as_user()
    with patched(cc_pf,
                 start_portal_fetch=lambda *a, **k: {"run_id": "r-slow"},
                 get_portal_result=lambda rid, t=15: {"done": False}), \
         patched(portal_tools, WAIT_SECONDS=0):
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
