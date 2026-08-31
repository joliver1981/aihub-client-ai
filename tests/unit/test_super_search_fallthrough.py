"""document_search_super_enhanced_debug — the zero-chunk fall-through.

The semantic branch's early return used to fire even when the vector engine
returned ZERO chunks, making the hybrid field half (4b/6) and the whole
Step-5 fallback sequence unreachable whenever the vector engine was up.
Seen live 2026-08-31: "No relevant documents found" for a question with 185
matching documents. These tests pin the repaired control flow:

  - zero chunks + hybrid   -> the field half runs and its results come back
  - zero chunks + semantic -> the fallback sequence runs
  - non-empty chunks       -> the fast early return is untouched
"""
import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# DocUtils drags in heavy deps at import; stub what this box's test env lacks.
for _name in ('anthropic', 'PyPDF2', 'fitz', 'openai'):
    if _name not in sys.modules:
        try:
            __import__(_name)
        except ImportError:
            sys.modules[_name] = MagicMock()

import DocUtils  # noqa: E402

Q = "what length is the term of Summit Center, Boston lease?"

UNIVERSE = json.dumps({
    "field_metadata": [], "custom_attribute_metadata": [],
    "document_types": [], "document_counts": [],
})

FIELD_HIT = {"document_id": "d1", "page_number": 1,
             "filename": "SKY-LEASE-S350-SummitCenter.pdf",
             "snippet": "1.3 Term: 15 years."}


def _fake_vector_module(chunks):
    """A vector_engine_client whose search_for_ai always returns `chunks`."""
    mod = types.ModuleType("vector_engine_client")

    class VectorEngineClient:
        def search_for_ai(self, term, filters=None):
            return {"results": list(chunks)}

    mod.VectorEngineClient = VectorEngineClient
    return mod


def _run(strategy, chunks, doc_search_json):
    """Run the full function with every external dependency stubbed."""
    calls = []

    def fake_document_search(**kwargs):
        calls.append(kwargs)
        return doc_search_json

    with patch.object(DocUtils, "get_document_types",
                      lambda allowed_document_types=None:
                      json.dumps({"document_types": ["lease_agreement"]})), \
         patch.object(DocUtils, "azureMiniQuickPrompt",
                      lambda **kw: '["lease_agreement"]'), \
         patch.object(DocUtils, "get_document_universe",
                      lambda *a, **kw: UNIVERSE), \
         patch.object(DocUtils, "azureQuickPrompt",
                      lambda **kw: json.dumps(strategy)), \
         patch.object(DocUtils, "document_search", fake_document_search), \
         patch.object(DocUtils.cfg, "DOC_USE_AI_SELECTED_FIELDS", False), \
         patch.object(DocUtils.cfg, "DOC_USE_LLM_RERANK", False, create=True), \
         patch.dict(sys.modules,
                    {"vector_engine_client": _fake_vector_module(chunks)}):
        out = DocUtils.document_search_super_enhanced_debug(
            "conn-string", user_question=Q, max_results=50,
            check_completeness=False)
    return out, calls


@pytest.mark.unit
class TestZeroChunkFallthrough:
    def test_hybrid_zero_chunks_runs_field_half(self):
        strategy = {
            "search_approach": "hybrid",
            "reasoning": "r", "confidence": "high",
            "semantic_search": {"search_terms": ["Summit Center Boston lease term"]},
            "field_search": {"field_filters": [
                {"field_name": "city", "operator": "equals", "value": "Boston"}]},
        }
        out, calls = _run(strategy, chunks=[],
                          doc_search_json=json.dumps({"results": [dict(FIELD_HIT)]}))
        body = json.loads(out)
        ids = [r.get("document_id") for r in body["results"]]
        assert "d1" in ids, "field half never ran on zero-chunk semantic"
        # the field branch, not a fallback, must have produced it
        assert any(kwargs.get("field_filters") for kwargs in calls)
        assert body["results"][0]["search_method"].startswith("field_")

    def test_semantic_zero_chunks_runs_fallbacks(self):
        strategy = {
            "search_approach": "semantic",
            "reasoning": "r", "confidence": "high",
            "semantic_search": {"search_terms": ["lease term"]},
        }
        out, calls = _run(strategy, chunks=[],
                          doc_search_json=json.dumps({"results": [dict(FIELD_HIT)]}))
        body = json.loads(out)
        assert body["results"], "fallback sequence never ran on zero-chunk semantic"
        assert body["results"][0]["search_method"].startswith("fallback_")
        assert calls, "no fallback document_search call was made"

    def test_zero_chunks_and_empty_fallbacks_reports_honest_error(self):
        strategy = {
            "search_approach": "semantic",
            "reasoning": "r", "confidence": "high",
            "semantic_search": {"search_terms": ["lease term"]},
        }
        out, _ = _run(strategy, chunks=[],
                      doc_search_json=json.dumps({"results": []}))
        body = json.loads(out)
        assert body["results"] == []
        assert body["error"], "exhausted search must set the error message"

    def test_nonempty_chunks_keep_the_fast_early_return(self):
        strategy = {
            "search_approach": "hybrid",
            "reasoning": "r", "confidence": "high",
            "semantic_search": {"search_terms": ["Summit Center Boston lease term"]},
            "field_search": {"field_filters": [
                {"field_name": "city", "operator": "equals", "value": "Boston"}]},
        }
        # deduplicate_search_results keys on a TOP-LEVEL document_id and
        # silently drops entries without one — mirror the real chunk shape.
        chunk = {"document_id": "d1", "page_number": 1, "relevance_score": 0.9,
                 "filename": "SKY-LEASE-S350-SummitCenter.pdf",
                 "text": "Term: 15 years"}
        with patch.object(DocUtils, "format_search_results_for_ai",
                          lambda results: "FORMATTED"):
            out, calls = _run(strategy, chunks=[chunk],
                              doc_search_json=json.dumps({"results": []}))
        assert out == "FORMATTED"
        assert calls == [], "happy path must not touch SQL document_search"
