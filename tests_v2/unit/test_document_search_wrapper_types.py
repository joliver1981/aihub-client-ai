"""document_search_unified(document_types=...) — the explicit type scope the
document-search page passes (2026-09-03).

THE CONTRACT: a scope can only NARROW the caller's category ACL, never widen
it (requested ∩ allowed); a scope with no accessible type is answered like
deny-all — an honest "not accessible" and the engine is never called (an
empty list would mean NO FILTER at the engine, the fail-open trap
doc_search_v3.acl documents); a scoped search always takes the LOOKUP engine.

The legacy engine and the ACL resolver are patched at their module attributes;
nothing touches a database or an LLM.
"""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

pytestmark = pytest.mark.unit

try:
    import DocUtils  # noqa: E402  (heavy; importable in the main-app env)
    import document_search_wrapper as dsw  # noqa: E402
    from doc_search_v3 import acl  # noqa: E402
except Exception as e:  # pragma: no cover - env-dependent
    pytest.skip(f"main-app document stack not importable here: {e}", allow_module_level=True)


@pytest.fixture
def engine(monkeypatch):
    """Record the allow list the legacy engine receives; answer no passages."""
    calls = []

    def _fake(cs, user_question=None, max_results=800, check_completeness=False,
              allowed_document_types=None):
        calls.append(allowed_document_types)
        return '{"results": [], "query_analysis": {"search_approach": "field"}}'
    monkeypatch.setattr(DocUtils, "document_search_super_enhanced_debug", _fake)
    monkeypatch.setattr(dsw, "get_db_connection_string", lambda: "cs")
    monkeypatch.setattr(dsw, "_question_shape", lambda q: "LOOKUP")
    return calls


@pytest.fixture
def grants(monkeypatch):
    state = {"allowed": None}
    monkeypatch.setattr(acl, "accessible_document_types",
                        lambda uid, role=None: state["allowed"])
    return state


def test_no_scope_unrestricted_passes_none(engine, grants):
    grants["allowed"] = None
    out = dsw.document_search_unified("rent", user_id=12, user_role=3)
    assert out["ok"] is True and engine == [None]


def test_scope_with_unrestricted_caller_becomes_the_allow_list(engine, grants):
    grants["allowed"] = None
    dsw.document_search_unified("rent", user_id=12, user_role=3,
                                document_types=["lease_agreement"])
    assert engine == [["lease_agreement"]]


def test_scope_is_intersected_with_the_acl(engine, grants):
    grants["allowed"] = ["lease_agreement", "vendor_guide"]
    dsw.document_search_unified("rent", user_id=141, user_role=2,
                                document_types=["lease_agreement", "settlement_agreement"])
    assert engine == [["lease_agreement"]], "a type outside the grants must be dropped, never added"


def test_scope_outside_the_acl_is_refused_without_calling_the_engine(engine, grants):
    grants["allowed"] = ["lease_agreement"]
    out = dsw.document_search_unified("rent", user_id=141, user_role=2,
                                      document_types=["settlement_agreement"])
    assert engine == [], "[] must never reach the engine (it means NO filter there)"
    assert out["ok"] is True and out["passages"] == [] and out["count"] == 0
    assert "not accessible" in out["text"] and "Groups page" in out["text"]


def test_deny_all_caller_is_refused_even_with_a_scope(engine, grants):
    grants["allowed"] = []
    out = dsw.document_search_unified("rent", user_id=10, user_role=1,
                                      document_types=["lease_agreement"])
    assert engine == []
    assert "do not have access to any document categories" in out["text"]


def test_blank_scope_entries_are_ignored(engine, grants):
    grants["allowed"] = None
    dsw.document_search_unified("rent", user_id=12, user_role=3, document_types=["", None, " "])
    assert engine == [None]


def test_scoped_search_never_uses_the_count_engine(engine, grants, monkeypatch):
    grants["allowed"] = None
    monkeypatch.setattr(dsw, "_question_shape", lambda q: "COUNT")
    import doc_search_v3.enumerate_engine as ee
    monkeypatch.setattr(ee, "enumerate_documents",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    out = dsw.document_search_unified("how many leases mention HVAC", user_id=12, user_role=3,
                                      document_types=["lease_agreement"])
    assert out["ok"] is True and engine == [["lease_agreement"]]
