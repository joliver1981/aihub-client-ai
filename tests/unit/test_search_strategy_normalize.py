"""DocUtils._normalize_search_strategy — shape guards at the strategy chokepoint.

json.loads succeeding does not mean the strategy LLM returned the expected
object. Seen live (2026-08-15, ACL-clamped CC searches): nested sections came
back as strings, and every consumer's .get() chain raised
"'str' object has no attribute 'get'" — an HTTP 500 instead of a search.
"""
import sys
import types
from unittest.mock import MagicMock

import pytest

# DocUtils drags in heavy deps at import; stub what this box's test env lacks.
for _name in ('anthropic', 'PyPDF2', 'fitz', 'openai'):
    if _name not in sys.modules:
        try:
            __import__(_name)
        except ImportError:
            sys.modules[_name] = MagicMock()

from DocUtils import _normalize_search_strategy  # noqa: E402

Q = "what does the vendor guide say about compliance?"


def norm(strategy):
    attempts = []
    out = _normalize_search_strategy(strategy, Q, attempts)
    return out, attempts


@pytest.mark.unit
class TestNormalizeSearchStrategy:
    def test_proper_object_passes_through_untouched(self):
        s = {"search_approach": "hybrid",
             "semantic_search": {"search_terms": ["a", "b"]},
             "field_search": {"field_filters": [{"field_name": "x"}]}}
        out, attempts = norm(dict(s))
        assert out == s and attempts == []

    def test_bare_string_strategy_falls_back_to_semantic(self):
        # json.loads('"semantic"') == 'semantic' — valid JSON, wrong shape
        out, attempts = norm("semantic")
        assert out["search_approach"] == "semantic"
        assert out["semantic_search"]["search_terms"] == [Q]
        assert attempts

    def test_list_strategy_falls_back(self):
        out, _ = norm(["semantic", "field"])
        assert isinstance(out, dict) and out["search_approach"] == "semantic"

    def test_semantic_search_as_string_becomes_terms(self):
        # The live crash: {"semantic_search": "vendor compliance"}
        out, _ = norm({"search_approach": "semantic",
                       "semantic_search": "vendor compliance"})
        assert out["semantic_search"] == {"search_terms": ["vendor compliance"]}
        # the consumer's exact chain must now be safe:
        assert out.get("semantic_search", {}).get("search_terms") == \
            ["vendor compliance"]

    def test_search_terms_as_string_becomes_list(self):
        # A bare-string terms value would be iterated CHAR BY CHAR downstream.
        out, _ = norm({"search_approach": "semantic",
                       "semantic_search": {"search_terms": "vendor compliance"}})
        assert out["semantic_search"]["search_terms"] == ["vendor compliance"]

    def test_field_search_as_string_dropped(self):
        out, _ = norm({"search_approach": "field", "field_search": "invoice_id"})
        assert out["field_search"] == {}
        assert out.get("field_search", {}).get("field_filters", []) == []

    def test_field_filters_as_dict_dropped(self):
        out, _ = norm({"search_approach": "field",
                       "field_search": {"field_filters": {"field_name": "x"}}})
        assert out["field_search"]["field_filters"] == []

    def test_field_filters_mixed_entries_keep_only_dicts(self):
        # filter_item.get(...) runs on every entry — a str entry crashed live.
        out, _ = norm({"search_approach": "field",
                       "field_search": {"field_filters":
                                        [{"field_name": "x"}, "bogus", 3]}})
        assert out["field_search"]["field_filters"] == [{"field_name": "x"}]

    def test_absent_sections_stay_absent(self):
        # semantic-only strategies legitimately omit field_search (and vice
        # versa) — the guard must not invent sections.
        out, attempts = norm({"search_approach": "semantic",
                              "semantic_search": {"search_terms": ["a"]}})
        assert "field_search" not in out and attempts == []


@pytest.mark.unit
class TestEntityGuard:
    """question_entities must survive into at least one search term.

    The 2026-08-31 pack-23 failure: 'term of Summit Center, Boston lease?'
    was rewritten to generic terms ('lease term', ...) — the property and
    the city both discarded — and the engine answered from a Chicago lease.
    The guard's repair is appending the raw question as one more term.
    """

    QE = "what length is the term of Summit Center, Boston lease?"

    def _norm(self, strategy):
        attempts = []
        out = _normalize_search_strategy(strategy, self.QE, attempts)
        return out, attempts

    def test_stripped_entities_append_raw_question(self):
        out, attempts = self._norm({
            "search_approach": "semantic",
            "question_entities": ["Summit Center", "Boston"],
            "semantic_search": {"search_terms": ["lease term", "initial term"]}})
        assert out["semantic_search"]["search_terms"] == \
            ["lease term", "initial term", self.QE]
        assert any("entity-guard" in a for a in attempts)

    def test_entities_present_leaves_terms_alone(self):
        terms = ["Summit Center Boston lease term", "initial term"]
        out, attempts = self._norm({
            "search_approach": "semantic",
            "question_entities": ["Summit Center", "Boston"],
            "semantic_search": {"search_terms": list(terms)}})
        assert out["semantic_search"]["search_terms"] == terms
        assert attempts == []

    def test_punctuation_and_case_do_not_defeat_the_match(self):
        # 'Summit Center, Boston' (comma) must count as present in a term
        # that spells it 'summit center boston'.
        out, attempts = self._norm({
            "search_approach": "semantic",
            "question_entities": ["Summit Center, Boston"],
            "semantic_search": {"search_terms": ["summit center boston lease"]}})
        assert out["semantic_search"]["search_terms"] == \
            ["summit center boston lease"]
        assert attempts == []

    def test_no_entities_key_leaves_strategy_untouched(self):
        out, attempts = self._norm({
            "search_approach": "semantic",
            "semantic_search": {"search_terms": ["lease term"]}})
        assert out["semantic_search"]["search_terms"] == ["lease term"]
        assert attempts == []

    def test_entities_wrong_shape_fail_open(self):
        out, _ = self._norm({
            "search_approach": "semantic",
            "question_entities": "Summit Center",  # not a list
            "semantic_search": {"search_terms": ["lease term"]}})
        assert out["semantic_search"]["search_terms"] == ["lease term"]

    def test_question_already_a_term_never_duplicated(self):
        # 'Boston, MA' is not verbatim from the question, so it reads as
        # missing — but the question itself is already being searched, and
        # appending it again would only duplicate the vector query.
        out, _ = self._norm({
            "search_approach": "semantic",
            "question_entities": ["Summit Center", "Boston, MA"],
            "semantic_search": {"search_terms": [self.QE]}})
        assert out["semantic_search"]["search_terms"] == [self.QE]

    def test_empty_terms_left_for_consumer_fallback(self):
        # The consumer substitutes [user_question] when terms are empty;
        # the guard must not pre-empt that path.
        out, _ = self._norm({
            "search_approach": "semantic",
            "question_entities": ["Summit Center"],
            "semantic_search": {"search_terms": []}})
        assert out["semantic_search"]["search_terms"] == []

    def test_field_only_strategy_without_terms_untouched(self):
        s = {"search_approach": "field",
             "question_entities": ["Summit Center"],
             "field_search": {"field_filters": [{"field_name": "city"}]}}
        out, attempts = self._norm(dict(s))
        assert "semantic_search" not in out
        assert attempts == []


@pytest.mark.unit
class TestRankShapeGuard:
    """rank_search_results must survive wrong-shape entries from any branch."""

    def _rank(self, results):
        from DocUtils import rank_search_results
        return rank_search_results(results, "q")

    def test_exploded_error_string_chars_dropped(self):
        # extend("error text") puts 1-char strings in the list — the live 500.
        good = {"document_id": "d1", "page_number": 1, "search_method": "field"}
        out = self._rank(list("error!") + [good])
        assert out == [good]

    def test_mixed_garbage_dropped_dicts_kept(self):
        g1 = {"document_id": "d1", "page_number": 1}
        g2 = {"document_id": "d2", "page_number": 2}
        out = self._rank([g1, None, ["nested"], "msg", 3.14, g2])
        assert {r["document_id"] for r in out} == {"d1", "d2"}

    def test_all_garbage_returns_empty_not_crash(self):
        assert self._rank(["a", "b", None]) == []

    def test_clean_input_unchanged(self):
        g1 = {"document_id": "d1", "page_number": 1}
        assert self._rank([g1]) == [g1]
