"""The Agent's document tools describe the CALLER's scope, never "the store"
(RU-06b / finding F-2, james 2026-09-07, "option B" — scope-honest).

/api/documents computes its totals inside the caller's category ACL, so a
regular user holding 2 of 18 categories was told "the store contains 11
documents" — a per-user slice labelled as the platform's state, in a way the
user cannot detect. The number was right; the label was wrong. Now:

  * list_documents says "you can see N", never "store holds N";
  * a RESTRICTED caller's result ends with the document types their access
    covers, as DATA (`stats.accessible_document_types`, which the server emits
    only when part of the store is hidden from them) — the coverage-ledger
    pattern, not a guard over the model's reply;
  * an unrestricted caller (no such key in the payload) gets no added line;
  * the deny-all message is byte-identical to before;
  * get_document's not-found text describes the caller's view.

Existence-hiding stays: nothing here confirms or denies that a document
exists outside the caller's access.

Runs standalone (aihub-agent python test_agent_document_scope_honesty.py) or
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

DOC = {"document_id": "d1", "filename": "DCT13_S003_a4_amendment.pdf",
       "document_type": "lease_amendment", "page_count": 3,
       "processed_at": "2026-07-25T16:17:26"}

# The deny-all wording (2026-09-03, sharpened 29f69c3) — pinned byte for byte.
DENIED_TEXT = ("You do not have access to any document categories — this is an "
               "access restriction, not an empty store. An administrator can "
               "grant access on the Groups page.")


def _listing(docs, total_documents, accessible=None, access=None, message=None):
    """A /api/documents payload as the server shapes it. `accessible` present
    == the server decided this caller is restricted with part of the store
    hidden from them; absent == unrestricted (or nothing hidden)."""
    stats = {"total_documents": total_documents, "total_pages": 0,
             "document_types": 1 if docs else 0, "last_updated": None}
    if accessible is not None:
        stats["accessible_document_types"] = accessible
    payload = {"documents": list(docs),
               "pagination": {"page": 1, "per_page": 25, "total_count": len(docs),
                              "total_pages": 1 if docs else 0,
                              "has_prev": False, "has_next": False},
               "stats": stats}
    if access:
        payload["access"] = access
    if message:
        payload["message"] = message
    return payload


def _run(handler, args, payload, status=200):
    async def fake_get(path, params=None):
        return payload, status
    with mock.patch.object(document_tools, "_get", fake_get):
        res = asyncio.run(handler(args))
    return res, " ".join(c.get("text", "") for c in res.get("content", []))


def _run_list(payload, args=None, status=200):
    return _run(document_tools.list_documents.handler, args or {}, payload, status)


def _run_get(payload, status=200):
    return _run(document_tools.get_document.handler, {"document_id": "d9"},
                payload, status)


# ------------------------------------------------------------ item 1: labels
def test_empty_listing_names_the_callers_view_not_the_store():
    _res, text = _run_list(_listing([], 11))
    assert text == "No match among the documents you can search. (You can see 11 document(s).)"
    assert "store" not in text.lower()


def test_listing_header_names_the_callers_view_not_the_store():
    _res, text = _run_list(_listing([DOC], 11))
    assert text.startswith("1 of 1 matching document(s) (you can see 11):")
    assert "DCT13_S003_a4_amendment.pdf" in text
    assert "store" not in text.lower()


def test_get_document_not_found_describes_the_callers_view():
    # Hidden == missing server-side (404 either way), so the tool can only
    # honestly describe the caller's view — never "the store".
    res, text = _run_get({"error": "Document not found"}, status=404)
    assert res.get("is_error") is True
    assert text == ("No document with id d9 is among the documents you can see. "
                    "Use list_documents to see valid ids.")
    assert "store" not in text.lower()


# ------------------------------------------- item 2: scope as data, restricted
def test_restricted_empty_listing_carries_the_access_scope():
    _res, text = _run_list(_listing([], 11, accessible=["commercial_lease_agreement",
                                                         "lease_amendment"]))
    assert text == ("No match among the documents you can search. (You can see 11 document(s).)"
                    "\n(Your access covers: commercial_lease_agreement, lease_amendment.)")


def test_restricted_populated_listing_ends_with_the_access_scope():
    _res, text = _run_list(_listing([DOC], 11, accessible=["lease_amendment"]))
    assert text.startswith("1 of 1 matching document(s) (you can see 11):")
    assert "DCT13_S003_a4_amendment.pdf" in text
    assert text.endswith("\n(Your access covers: lease_amendment.)")


def test_restricted_scope_with_no_visible_types_is_stated_honestly():
    # Restricted, and every granted type currently holds no documents.
    _res, text = _run_list(_listing([], 0, accessible=[]))
    assert text.endswith("\n(Your access covers none of the document types that "
                         "currently hold documents.)")


def test_scope_line_caps_the_names():
    names = [f"type_{i:02d}" for i in range(25)]
    line = document_tools.access_scope_line({"accessible_document_types": names})
    assert line.startswith("\n(Your access covers: type_00, type_01, ")
    assert "type_19" in line and "type_20" not in line
    assert line.endswith(", +5 more.)")


def test_scope_line_is_built_from_the_servers_list_alone():
    # Nothing is inferred from rows or counts — the boundary is the server's
    # ACL-filtered list or nothing.
    assert document_tools.access_scope_line({}) == ""
    assert document_tools.access_scope_line(None) == ""
    assert document_tools.access_scope_line({"accessible_document_types": "x"}) == ""
    assert document_tools.access_scope_line({"total_documents": 11}) == ""


# ------------------------------------- unrestricted callers: no change at all
def test_unrestricted_listings_have_no_scope_line():
    for payload in (_listing([], 400), _listing([DOC], 400)):
        _res, text = _run_list(payload)
        assert "access" not in text.lower()
        assert "(Your" not in text


# --------------------------------------------- deny-all: byte-identical
def test_denied_listing_is_byte_identical_to_before():
    res, text = _run_list(_listing([], 0, access="denied", message=DENIED_TEXT))
    assert text == DENIED_TEXT
    assert not res.get("is_error")


def test_denied_marker_without_a_message_is_byte_identical_to_before():
    _res, text = _run_list(_listing([], 0, access="denied"))
    assert text == DENIED_TEXT


def test_denied_listing_ignores_a_scope_list_if_one_ever_arrived():
    # The server never sends both, but the denial must win regardless.
    _res, text = _run_list(_listing([], 0, accessible=[], access="denied",
                                    message=DENIED_TEXT))
    assert text == DENIED_TEXT


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
