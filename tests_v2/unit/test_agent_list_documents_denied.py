"""list_documents renders a category-ACL denial honestly (james 2026-09-03).

/api/documents answers a user with zero category grants an EMPTY listing with
HTTP 200 (doc-acl G2) plus an additive `access: "denied"` marker. Before this
the tool rendered that as "No documents in the store match that. (Store
total: 0)" and the model told the user the store was empty — an access
boundary read as an empty store. Now the tool relays the same denial text the
search / records tools use, and an ordinary empty or filtered listing is
unchanged.

Runs standalone (aihub-agent python test_agent_list_documents_denied.py) or
under pytest; self-skips without claude_agent_sdk. Force-add to git.
"""
import asyncio
import os
import sys
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import document_tools                      # noqa: E402
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

DENIED = {"documents": [], "pagination": {"page": 1, "per_page": 25, "total_count": 0,
                                          "total_pages": 0, "has_prev": False, "has_next": False},
          "stats": {"total_documents": 0, "total_pages": 0, "document_types": 0,
                    "last_updated": None},
          "access": "denied",
          "message": "You do not have access to any document categories — this is an "
                     "access restriction, not an empty store. An administrator can "
                     "grant access on the Groups page."}
EMPTY = {k: v for k, v in DENIED.items() if k not in ("access", "message")}
ONE = {"documents": [{"document_id": "d1", "filename": "a.pdf", "document_type": "vendor_guide",
                      "page_count": 3, "processed_at": "2026-09-01T10:00:00"}],
       "pagination": {"page": 1, "per_page": 25, "total_count": 1, "total_pages": 1,
                      "has_prev": False, "has_next": False},
       "stats": {"total_documents": 1, "total_pages": 3, "document_types": 1,
                 "last_updated": "2026-09-01T10:00:00"}}


def _run(payload, status=200):
    async def fake_get(path, params=None):
        assert path == "/api/documents"
        return payload, status
    with mock.patch.object(document_tools, "_get", fake_get):
        res = asyncio.run(document_tools.list_documents.handler({}))
    return res, " ".join(c.get("text", "") for c in res.get("content", []))


def test_denied_listing_relays_the_access_message_not_an_empty_store():
    res, text = _run(DENIED)
    assert "do not have access to any document categories" in text
    assert "not an empty store" in text, "the wording must rule out the 'empty store' reading"
    assert "Groups page" in text
    assert "store total" not in text.lower() and "No documents" not in text
    assert not res.get("is_error"), "a denial is information for the user, not a tool failure"


def test_denied_marker_without_a_message_uses_the_same_wording():
    payload = {k: v for k, v in DENIED.items() if k != "message"}
    _res, text = _run(payload)
    assert "not an empty store" in text and "Groups page" in text


def test_plain_empty_listing_is_unchanged():
    _res, text = _run(EMPTY)
    assert "No documents in the store match that" in text
    assert "access" not in text.lower()


def test_filtered_listing_is_unchanged():
    _res, text = _run(ONE)
    assert "1 of 1 matching document(s) (store holds 1)" in text
    assert "a.pdf" in text and "vendor_guide" in text


def test_http_error_still_reads_as_an_error():
    res, text = _run({"error": "boom"}, status=500)
    assert res.get("is_error") is True and "HTTP 500" in text


def _main():
    fails = 0
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            fails += 1
            print(f"FAIL {name}: {e!r}")
    print(f"{len(tests) - fails}/{len(tests)} PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    if not HAVE_SDK:
        print(f"SKIP: needs aihub-agent env ({_IMPORT_ERR})")
        sys.exit(0)
    sys.exit(_main())
