"""The Agent document tools — fast-busy (HTTP 503 + Retry-After) handling
(agent_service/document_tools.py, 2026-08-21).

When the doc API's /document/process gate or the main app's document-search
gate is full, the server now answers 503 with a busy payload. The tools must
relay that as an honest "busy, retry in ~N s" — never as a broken import or a
generic HTTP failure that sends the model into retry storms.

Needs the agent env (claude_agent_sdk); self-skips elsewhere. Standalone:
    C:\\Users\\james\\miniconda3\\envs\\aihub-agent\\python.exe tests_v2/unit/test_document_tools_busy.py
"""
import asyncio
import os
import sys
import unittest

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import document_tools as dt                       # noqa: E402
    from platform_tools import CURRENT_USER           # noqa: E402
    HAVE_SDK = True
except Exception as e:                                # pragma: no cover
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(reason=f"needs the aihub-agent env: {_IMPORT_ERR}")
    except ImportError:
        pass


def _run(tool_obj, args):
    """Tools are claude_agent_sdk SdkMcpTool objects: call their handler."""
    handler = getattr(tool_obj, "handler", tool_obj)
    return asyncio.run(handler(args))


def _text_of(result):
    """Tool results are {'content': [{'type':'text','text':...}], ...}."""
    if isinstance(result, dict):
        blocks = result.get("content") or []
        return " ".join(b.get("text", "") for b in blocks if isinstance(b, dict))
    return str(result)


@unittest.skipUnless(HAVE_SDK, "document_tools not importable in this env")
class TestBusyText(unittest.TestCase):
    def test_uses_server_message_and_retry_after(self):
        s = dt._busy_text({"message": "The document stack is busy: 14 of 14 slots.",
                           "retry_after": 45})
        self.assertIn("14 of 14 slots", s)
        self.assertIn("45 seconds", s)
        self.assertIn("rather than retrying in a loop", s)

    def test_header_retry_after_when_payload_lacks_it(self):
        s = dt._busy_text({"message": "busy"}, retry_after="30")
        self.assertIn("30 seconds", s)

    def test_falls_back_to_generic_busy_message(self):
        self.assertEqual(dt._busy_text({}), dt._BUSY_MSG)
        self.assertEqual(dt._busy_text("not json"), dt._BUSY_MSG)


@unittest.skipUnless(HAVE_SDK, "document_tools not importable in this env")
class TestSearchAndRecords503(unittest.TestCase):
    def setUp(self):
        self._saved = dt._post_main
        self._token = CURRENT_USER.set({"user_id": 987654, "username": "busy-test",
                                        "role": 2, "name": "Busy Test"})

    def tearDown(self):
        dt._post_main = self._saved
        CURRENT_USER.reset(self._token)

    def test_search_documents_relays_503_as_busy(self):
        async def fake_post(path, body, internal=False, read_timeout=120.0):
            return ({"status": "busy", "message": "The document stack is busy: 12 of 12 "
                     "document-search slots are in use.", "retry_after": 20}, 503)
        dt._post_main = fake_post
        out = _run(dt.search_documents, {"query": "find the lease"})
        text = _text_of(out)
        self.assertIn("12 of 12", text)
        self.assertIn("20 seconds", text)
        self.assertNotIn("HTTP 503", text)
        self.assertTrue(out.get("is_error", out.get("isError", True)))

    def test_query_document_records_relays_503_as_busy(self):
        async def fake_post(path, body, internal=False, read_timeout=120.0):
            return ({"message": "busy", "retry_after": 15}, 503)
        dt._post_main = fake_post
        out = _run(dt.query_document_records, {"record_set": "line_items"})
        text = _text_of(out)
        self.assertIn("15 seconds", text)
        self.assertNotIn("Record query failed (HTTP 503)", text)

    def test_other_errors_unchanged(self):
        async def fake_post(path, body, internal=False, read_timeout=120.0):
            return ({"message": "nope"}, 500)
        dt._post_main = fake_post
        text = _text_of(_run(dt.search_documents, {"query": "x"}))
        self.assertIn("HTTP 500", text)


class _FakeResp:
    def __init__(self, status, payload, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class _FakeClient:
    """Stands in for httpx.AsyncClient in import_documents."""
    script = []          # list of _FakeResp in call order
    calls = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, data=None, **kw):
        _FakeClient.calls.append((url, data))
        return _FakeClient.script.pop(0)


@unittest.skipUnless(HAVE_SDK, "document_tools not importable in this env")
class TestImport503(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="busy-import-")
        self.f1 = os.path.join(self.tmp, "a.txt")
        self.f2 = os.path.join(self.tmp, "b.txt")
        for f in (self.f1, self.f2):
            with open(f, "w") as fh:
                fh.write("probe")
        self._saved_client = dt.httpx.AsyncClient
        self._saved_existing = dt._existing_paths_for
        dt.httpx.AsyncClient = _FakeClient
        self._token = CURRENT_USER.set({"user_id": 987654, "username": "busy-test",
                                        "role": 2, "name": "Busy Test"})

        async def none_existing(basename):
            return set()
        dt._existing_paths_for = none_existing
        _FakeClient.calls = []

    def tearDown(self):
        dt.httpx.AsyncClient = self._saved_client
        dt._existing_paths_for = self._saved_existing
        CURRENT_USER.reset(self._token)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_503_reported_as_busy_not_failed_http(self):
        _FakeClient.script = [
            _FakeResp(503, {"status": "busy", "message": "The document stack is busy: 14 of 14 "
                                                          "document/process slots are in use.",
                            "retry_after": 25}, headers={"Retry-After": "25"}),
            _FakeResp(200, {"status": "success", "document_id": "doc-2", "page_count": 1}),
        ]
        out = _run(dt.import_documents, {"path": self.tmp})
        text = _text_of(out)
        self.assertIn("BUSY (not imported)", text)
        self.assertIn("14 of 14", text)
        self.assertIn("25 seconds", text)
        self.assertIn("Imported 1 of 2", text)
        self.assertNotIn("HTTP 503", text)
        self.assertEqual(len(_FakeClient.calls), 2)       # no retry storm: one POST per file


if __name__ == "__main__":
    unittest.main(verbosity=2)
