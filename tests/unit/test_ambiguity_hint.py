"""_competing_documents_hint — the ambiguity signal, and its wrapper plumbing.

The signal must be discriminating or it is noise: it fires only when several
documents OF THE SAME TYPE match EVERY entity the user named, within a count
band. docs/search-ambiguity-signal-handoff.md; the control case (a store id
that appears in a lease, a roof warranty and a fire inspection) must stay
silent.
"""
import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

for _name in ('anthropic', 'PyPDF2', 'fitz', 'openai'):
    if _name not in sys.modules:
        try:
            __import__(_name)
        except ImportError:
            sys.modules[_name] = MagicMock()

import DocUtils  # noqa: E402
from DocUtils import _competing_documents_hint  # noqa: E402


def _page(doc_id, filename, doc_type, text):
    """Vector-chunk shape: real ids under metadata, chunk id top-level."""
    return {"document_id": f"chunk-{doc_id}-{len(text)}",
            "metadata": {"document_id": doc_id, "filename": filename,
                         "document_type": doc_type, "full_text": text}}


SUMMIT = [
    _page("b1", "SKY-LEASE-S300-SummitCenter.pdf", "lease_agreement",
          "Property: Summit Center, Boston, MA. Term: 15 years."),
    _page("b2", "SKY-LEASE-S350-SummitCenter.pdf", "lease_agreement",
          "Property: Summit Center, Boston, MA. Term: 15 years."),
    _page("b3", "SKY-LEASE-S400-SummitCenter.pdf", "lease_agreement",
          "Property: Summit Center, Boston, MA. Term: 5 years."),
    _page("c1", "SKY-LEASE-S325-SummitCenter.pdf", "lease_agreement",
          "Property: Summit Center, Chicago, IL. Term: 10 years."),
    _page("c2", "SKY-LEASE-S375-SummitCenter.pdf", "lease_agreement",
          "Property: Summit Center, Chicago, IL. Term: 7 years."),
    _page("c3", "SKY-LEASE-S425-SummitCenter.pdf", "lease_agreement",
          "Property: Summit Center, Chicago, IL. Term: 10 years."),
]


def strategy(entities):
    return {"search_approach": "semantic", "question_entities": entities}


@pytest.mark.unit
class TestCompetingDocumentsHint:
    def test_joint_entities_select_only_the_boston_leases(self):
        hint = _competing_documents_hint(SUMMIT, strategy(["Summit Center", "Boston"]))
        assert hint and hint.startswith("AMBIGUITY NOTE:")
        assert "3 distinct lease_agreement" in hint
        for f in ("S300", "S350", "S400"):
            assert f in hint
        for f in ("S325", "S375", "S425"):
            assert f not in hint

    def test_single_entity_fires_on_all_six(self):
        hint = _competing_documents_hint(SUMMIT, strategy(["Summit Center"]))
        assert hint and "6 distinct lease_agreement" in hint

    def test_hint_is_single_line(self):
        hint = _competing_documents_hint(SUMMIT, strategy(["Summit Center"]))
        assert "\n" not in hint

    def test_same_entity_across_document_types_is_context_not_alternatives(self):
        results = [
            _page("l1", "SKY-LEASE-S317-AshcroftLanding.pdf", "lease_agreement",
                  "Store S317. Base rent $30,000."),
            _page("w1", "SKY-ROOFWTY-02-S317.pdf", "roof_warranty",
                  "Roof warranty for store S317."),
            _page("f1", "SKY-FIREINSP-02-S317.pdf", "fire_inspection",
                  "Fire inspection, store S317."),
        ]
        assert _competing_documents_hint(results, strategy(["S317"])) is None

    def test_single_matching_document_is_silent(self):
        assert _competing_documents_hint(
            SUMMIT, strategy(["Summit Center", "Boston", "S400"])) is None

    def test_no_entities_is_silent(self):
        assert _competing_documents_hint(SUMMIT, strategy([])) is None
        assert _competing_documents_hint(SUMMIT, {"search_approach": "semantic"}) is None
        assert _competing_documents_hint(SUMMIT, strategy("Summit Center")) is None

    def test_breadth_above_cap_is_silent(self):
        many = [_page(f"d{i}", f"LEASE-{i}.pdf", "lease_agreement",
                      "Tenant: SKYLINE STORES.") for i in range(9)]
        with patch.object(DocUtils.cfg, "DOC_AMBIGUITY_HINT_MAX_DOCS", 8, create=True):
            assert _competing_documents_hint(many, strategy(["SKYLINE STORES"])) is None

    def test_chunk_ids_do_not_inflate_the_document_count(self):
        # two chunks of the SAME document must count as one document
        results = [
            _page("d1", "SKY-LEASE-S300-SummitCenter.pdf", "lease_agreement",
                  "Summit Center page one."),
            _page("d1", "SKY-LEASE-S300-SummitCenter.pdf", "lease_agreement",
                  "Summit Center page two, different chunk."),
        ]
        assert _competing_documents_hint(results, strategy(["Summit Center"])) is None

    def test_disabled_flag_is_silent(self):
        with patch.object(DocUtils.cfg, "DOC_AMBIGUITY_HINT_ENABLED", False, create=True):
            assert _competing_documents_hint(SUMMIT, strategy(["Summit Center"])) is None

    def test_garbage_results_never_raise(self):
        assert _competing_documents_hint(
            ["oops", 3, None], strategy(["Summit Center"])) is None


BLOB = (
    "[Source 1: SKY-LEASE-S350-SummitCenter - Page 1] (lease_agreement) (Relevance: 0.90)\n"
    "Term: 15 years.\n"
    " Document URL: http://x/document/view/d1?page=1\n"
)
NOTE = ("AMBIGUITY NOTE: 3 distinct lease_agreement documents in these results "
        "all match 'Summit Center' + 'Boston' (a, b, c). The question may assume "
        "there is only one such document.")


def _fake_pkg_doc_search_v3():
    pkg = types.ModuleType("doc_search_v3")
    acl = types.ModuleType("doc_search_v3.acl")
    acl.accessible_document_types = lambda user_id, user_role: None
    acl.deny_all = lambda allowed: False
    pkg.acl = acl
    return {"doc_search_v3": pkg, "doc_search_v3.acl": acl}


def _fake_records():
    m = types.ModuleType("document_records_query")
    m.get_types_with_records = lambda: {}
    return m


def _unified(raw):
    import document_search_wrapper as dsw
    with patch.object(dsw, "_question_shape", lambda q: "LOOKUP"), \
         patch.object(DocUtils, "document_search_super_enhanced_debug",
                      lambda *a, **kw: raw), \
         patch.dict(sys.modules, {**_fake_pkg_doc_search_v3(),
                                  "document_records_query": _fake_records()}):
        return dsw.document_search_unified("q", conn_string="cs")


@pytest.mark.unit
class TestWrapperPlumbing:
    def test_blob_note_is_lifted_out_of_the_last_passage_and_reattached(self):
        result = _unified(BLOB + "\n\n" + NOTE)
        assert result["ambiguity_hint"] == NOTE
        assert result["text"].rstrip().endswith(NOTE)
        assert all("AMBIGUITY NOTE" not in p["text"] for p in result["passages"])
        assert result["count"] == 1

    def test_blob_without_note_sets_nothing(self):
        result = _unified(BLOB)
        assert "ambiguity_hint" not in result
        assert "AMBIGUITY NOTE" not in result["text"]

    def test_json_lane_note_carries_through(self):
        raw = json.dumps({"results": [], "query_analysis": {}, "error": None,
                          "ambiguity_hint": NOTE})
        result = _unified(raw)
        assert result["ambiguity_hint"] == NOTE
