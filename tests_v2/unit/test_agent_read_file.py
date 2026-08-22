"""read_file (2026-08-22) — one tool that plainly reads ANY common file type.

Text/code/config read LOCALLY (instant, zero LLM, nothing stored, whole file);
documents (PDF/Word/Excel/images) go through the engine's extract-WITHOUT-store
path. Tests cover both lanes, the resolvers (path / api-files link / attachment
id), the denylist, whole-file (no truncation), the honest oversize refusal, the
binary refusal, and the busy-503 relay. Doc-engine HTTP is mocked.

Standalone (aihub-agent python test_agent_read_file.py) or pytest; self-skips
without claude_agent_sdk.
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
    import file_tools
    from platform_tools import CURRENT_USER
    import httpx
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

TEST_UID = 987655
_READ = None


def _tool():
    global _READ
    if _READ is None:
        _READ = {t.name: t for t in dt.DOCUMENT_TOOLS}["read_file"]
    return _READ


def _run(args):
    return asyncio.run(_tool().handler(args))


def _txt(res):
    return res["content"][0]["text"]


def _as_user():
    CURRENT_USER.set({"user_id": TEST_UID, "role": 3, "username": "read-unit"})


def _tmp(name, data):
    d = os.path.join(APP_ROOT, "temp")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{uuid.uuid4().hex[:8]}_{name}")
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(p, mode, **({} if isinstance(data, bytes) else {"encoding": "utf-8"})) as fh:
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


def test_registered_and_read_only():
    import brain
    assert "read_file" in {t.name for t in dt.DOCUMENT_TOOLS}
    assert "read_file" in brain._READ_TOOL_NAMES      # side-thread readable
    assert "read_file" not in brain.MUTATING_TOOLS    # do_not_store => not a write


def test_reads_text_file_whole_no_truncation():
    _as_user()
    big = "line of csv data,{}\n".format("x" * 50)
    content = "col\n" + big * 5000          # ~ 300 KB, well over the old 20 KB
    p = _tmp("big.csv", content)
    try:
        res = _run({"path": p})
        t = _txt(res)
        assert not res.get("is_error")
        assert "Truncated" not in t and "first" not in t.lower()
        assert t.count("line of csv data") == 5000     # every row present
    finally:
        os.remove(p)


def test_reads_json_and_code_locally():
    _as_user()
    for name, body in (("cfg.json", '{"balance": 12345.67}'),
                       ("script.py", "print('hi')\n")):
        p = _tmp(name, body)
        try:
            res = _run({"path": p})
            assert not res.get("is_error") and body.split("\n")[0] in _txt(res)
        finally:
            os.remove(p)


def test_binary_unknown_ext_refused_as_text():
    _as_user()
    p = _tmp("blob.dat", b"\x00\x01\x02BINARY\xff\xfe" + b"\x00" * 50)
    try:
        res = _run({"path": p})
        assert res.get("is_error") and "binary" in _txt(res).lower()
    finally:
        os.remove(p)


def test_denylist_blocks_secret_dir():
    _as_user()
    secret = os.path.join(APP_ROOT, "data", "secrets", "secrets.json.enc")
    os.makedirs(os.path.dirname(secret), exist_ok=True)
    made = not os.path.exists(secret)
    if made:
        open(secret, "w").write("x")
    try:
        res = _run({"path": secret})
        assert res.get("is_error") and "protected" in _txt(res).lower()
    finally:
        if made:
            os.remove(secret)


def test_oversize_refused_not_truncated():
    _as_user()
    p = _tmp("huge.txt", "abcd\n")
    try:
        with patched(dt, _READ_MAX_MB=0):        # force the backstop
            res = _run({"path": p})
        t = _txt(res)
        assert res.get("is_error") and "too large" in t and "import_documents" in t
        assert "abcd" not in t                    # never a partial file
    finally:
        os.remove(p)


def test_resolves_api_files_link():
    _as_user()
    src = _tmp("delivered.txt", "delivered contents here")
    try:
        ok, link, _staged = file_tools.stage_offer(TEST_UID, src)
        assert ok
        import re
        fid = re.search(r"/api/files/([a-f0-9-]+)", link).group(1)
        res = _run({"path": f"/api/files/{fid}"})
        assert not res.get("is_error") and "delivered contents here" in _txt(res)
        # bare id resolves too
        res2 = _run({"path": fid})
        assert "delivered contents here" in _txt(res2)
    finally:
        os.remove(src)
        import shutil
        shutil.rmtree(os.path.join(APP_ROOT, "data", "agent", "users", str(TEST_UID)),
                      ignore_errors=True)


def test_missing_file_is_honest():
    _as_user()
    res = _run({"path": os.path.join(APP_ROOT, "temp", "nope_9x8y7z.txt")})
    assert res.get("is_error") and "No such file" in _txt(res)


def _mock_httpx(status, payload=None, text="", timeout=False):
    _Real = httpx.AsyncClient

    def handler(request):
        if timeout:
            raise httpx.ReadTimeout("read timed out")
        assert request.url.path == "/document/process"
        # read_file must ask for extract-without-store, no LLM passes
        body = request.content.decode() if request.content else ""
        assert "do_not_store=true" in body or "do_not_store=True" in body
        return httpx.Response(status, json=payload) if payload is not None \
            else httpx.Response(status, text=text)

    def factory(**kw):
        return _Real(transport=httpx.MockTransport(handler))
    return factory


def test_pdf_uses_extract_without_store():
    _as_user()
    p = _tmp("statement.pdf", b"%PDF-1.4 fake")
    try:
        with patched(httpx, AsyncClient=_mock_httpx(
                200, {"status": "success",
                      "document_text": "Ending balance: $9,432.10"})):
            res = _run({"path": p})
        t = _txt(res)
        assert not res.get("is_error")
        assert "$9,432.10" in t and "not stored" in t and "native" in t
    finally:
        os.remove(p)


def test_doc_busy_503_is_honest():
    _as_user()
    p = _tmp("statement.pdf", b"%PDF-1.4 fake")
    try:
        with patched(httpx, AsyncClient=_mock_httpx(
                503, {"message": "Document stack busy", "retry_after": 30})):
            res = _run({"path": p})
        assert res.get("is_error") and "busy" in _txt(res).lower()
    finally:
        os.remove(p)


def test_doc_empty_text_suggests_ocr():
    _as_user()
    p = _tmp("scan.pdf", b"%PDF-1.4 fake")
    try:
        with patched(httpx, AsyncClient=_mock_httpx(
                200, {"status": "success", "document_text": ""})):
            res = _run({"path": p})
        assert res.get("is_error") and "ocr=true" in _txt(res)
    finally:
        os.remove(p)


def test_allowlist_aligned_to_engine():
    # bug fixes: json/md/xml/html now importable; bmp/tiff dropped (engine rejects)
    assert {"json", "md", "xml", "html", "htm"} <= dt._ALLOWED_EXTS
    assert not ({"bmp", "tiff", "tif"} & dt._ALLOWED_EXTS)
    assert "webp" in dt._ALLOWED_EXTS


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
