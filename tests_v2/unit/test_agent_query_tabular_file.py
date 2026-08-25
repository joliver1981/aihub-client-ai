"""query_tabular_file (2026-08-25) — The Agent's structured tabular lane.

Closes the wrong-math-on-CSV gap for The Agent: instead of in-context
arithmetic over file text (unreliable past a few hundred rows), the tool posts
to the main app's /api/internal/tabular/query, which runs pandas on the full
file (agent_excel_tools.run_tabular_query). Tests cover registration,
read-only marking, the shared authz chokepoint (path resolution + protected
dirs), the tabular-extension gate, and request/response plumbing with the
HTTP layer mocked.

Standalone (aihub-agent python test_agent_query_tabular_file.py) or pytest;
self-skips without claude_agent_sdk.
"""
import asyncio
import os
import sys
import uuid

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import document_tools as dt
    from platform_tools import CURRENT_USER
    HAVE_SDK = True
except ImportError as e:
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(reason=f"needs aihub-agent env: {_IMPORT_ERR}")
    except ImportError:
        pass

TEST_UID = 987656
_TOOL = None


def _tool():
    global _TOOL
    if _TOOL is None:
        _TOOL = {t.name: t for t in dt.DOCUMENT_TOOLS}["query_tabular_file"]
    return _TOOL


def _run(args):
    return asyncio.run(_tool().handler(args))


def _txt(res):
    return res["content"][0]["text"]


def _as_user():
    CURRENT_USER.set({"user_id": TEST_UID, "role": 3, "username": "tabq-unit"})


def _tmp(name, data="a,b\n1,2\n3,4\n"):
    d = os.path.join(APP_ROOT, "temp")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{uuid.uuid4().hex[:8]}_{name}")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(data)
    return p


class patched:
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


def _fake_post(reply, capture):
    async def fake(path, body, internal=False, read_timeout=120.0):
        capture["path"] = path
        capture["body"] = body
        capture["internal"] = internal
        return reply, 200
    return fake


def test_registered_and_read_only():
    import brain
    assert "query_tabular_file" in {t.name for t in dt.DOCUMENT_TOOLS}
    assert "query_tabular_file" in brain._READ_TOOL_NAMES
    assert "query_tabular_file" not in brain.MUTATING_TOOLS


def test_summary_posts_internal_and_relays_text():
    _as_user()
    p = _tmp("inv.csv")
    cap = {}
    reply = {"status": "success",
             "result": {"ok": True, "text": "Total rows across all sheets: 1047"}}
    with patched(dt, _post_main=_fake_post(reply, cap)):
        out = _run({"path": p})
    assert not out.get("is_error")
    assert "1047" in _txt(out)
    assert cap["path"] == "/api/internal/tabular/query"
    assert cap["internal"] is True
    assert cap["body"]["operation"] == "summary"
    assert cap["body"]["path"] == p


def test_aggregate_params_forwarded():
    _as_user()
    p = _tmp("inv.csv")
    cap = {}
    reply = {"status": "success", "result": {"ok": True, "text": "sum=52"}}
    with patched(dt, _post_main=_fake_post(reply, cap)):
        out = _run({"path": p, "operation": "aggregate",
                    "group_by": "Banner",
                    "aggregations": '{"Pieces": "sum"}',
                    "filter_condition": "Pieces > 0"})
    assert "sum=52" in _txt(out)
    assert cap["body"]["group_by"] == "Banner"
    assert cap["body"]["aggregations"] == '{"Pieces": "sum"}'
    assert cap["body"]["filter_condition"] == "Pieces > 0"


def test_non_tabular_extension_refused_before_http():
    _as_user()
    p = _tmp("doc.pdf")
    called = {}
    with patched(dt, _post_main=_fake_post({}, called)):
        out = _run({"path": p})
    assert out.get("is_error")
    assert "not a tabular data file" in _txt(out)
    assert "path" not in called  # never reached HTTP


def test_missing_file_refused_by_resolver():
    _as_user()
    out = _run({"path": os.path.join(APP_ROOT, "temp", "nope-does-not-exist.csv")})
    assert out.get("is_error")


def test_backend_error_relayed_honestly():
    _as_user()
    p = _tmp("inv.csv")
    cap = {}
    reply = {"status": "success",
             "result": {"ok": False, "error": "Column 'Zzz' not found"}}
    with patched(dt, _post_main=_fake_post(reply, cap)):
        out = _run({"path": p, "operation": "aggregate",
                    "aggregations": '{"Zzz": "sum"}'})
    assert out.get("is_error")
    assert "Zzz" in _txt(out)


if __name__ == "__main__":
    if not HAVE_SDK:
        print(f"SKIP: {_IMPORT_ERR}")
        sys.exit(0)
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if fails else 0)
