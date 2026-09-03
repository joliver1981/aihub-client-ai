"""document_search_page — server side of the rebuilt /document-search page
(2026-09-03). Pure logic over a recording fake cursor and a fake unified
search; no database, no LLM.

Invariants under test:
  * request parsing is format validation only (arrays, clamps, modes);
  * scope resolution never widens the ACL: a selected type outside the grants
    is DENIED (never an empty list — downstream IN-builders read [] as no
    filter), a category is intersected with the grants;
  * the category tree shows only visible types and hides 'Uncategorised' from
    restricted callers;
  * field / attribute criteria are AND-ed per document and scoped by type;
  * the free-text search is delegated to the unified (ACL'd) search with the
    scope as document_types, intersected with the filter matches, min-score
    filtered, paginated;
  * snippets are HTML-escaped before highlighting.
"""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import document_search_page as dsp  # noqa: E402

pytestmark = pytest.mark.unit


class Args(dict):
    def getlist(self, k):
        v = self.get(k)
        return v if isinstance(v, list) else ([v] if v is not None else [])


class Cur:
    """Recorder answering by SQL shape."""

    def __init__(self, **answers):
        self.calls = []
        self.answers = answers
        self._last = ""

    def execute(self, sql, *params):
        self._last = " ".join(sql.split())
        self.calls.append((self._last, params))

    def fetchall(self):
        for needle, rows in self.answers.items():
            if needle in self._last:
                return list(rows)
        return []

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None

    def sql(self, needle):
        return [c for c in self.calls if needle in c[0]]


# ---------------------------------------------------------------- parsing
def test_parse_request_arrays_modes_and_clamps():
    req = dsp.parse_request(Args({"query": " rent ", "document_type": "lease_agreement",
                                  "field_name[]": ["financial_terms.base_rent_amount", "%", "x"],
                                  "field_operator[]": ["contains", "bogus", "equals"],
                                  "field_value[]": ["5,000", "abc", ""],
                                  "attribute_name[]": ["Region"], "attribute_operator[]": ["equals"],
                                  "attribute_value[]": ["West"],
                                  "search_mode": "attributes", "min_score": "7", "max_results": "999",
                                  "page": "-3"}))
    assert req.query == "rent" and req.document_type == "lease_agreement"
    assert [f["field_path"] for f in req.field_filters] == ["financial_terms.base_rent_amount", "%"]
    assert req.field_filters[0]["display_name"] == "Base Rent Amount" and req.field_filters[1]["display_name"] == "Any field"
    assert req.field_filters[1]["operator"] == "equals", "unknown operator falls back to equals"
    assert req.attribute_filters == [{"attribute_name": "Region", "operator": "equals", "value": "West"}]
    assert req.search_mode == "attributes"
    assert req.min_score == 1.0 and req.max_results == dsp.MAX_PER_PAGE and req.page == 1


def test_parse_request_language_mode_from_legacy_advanced_flag():
    req = dsp.parse_request(Args({"query": "x", "advanced": "1"}))
    assert req.search_mode == "language" and req.has_criteria
    assert not dsp.parse_request(Args({})).has_criteria


# ---------------------------------------------------------------- scope
def test_resolve_scope_type_outside_grants_is_denied_not_empty_list():
    cur = Cur()
    scope = dsp.resolve_scope(cur, ["lease_agreement"], dsp.SearchRequest(document_type="settlement_agreement"))
    assert scope["denied"] is True and scope["types"] == [] and "Groups page" in scope["message"]
    ok = dsp.resolve_scope(cur, ["lease_agreement"], dsp.SearchRequest(document_type="lease_agreement"))
    assert ok == {"types": ["lease_agreement"], "label": "lease_agreement", "denied": False, "message": ""}


def test_resolve_scope_category_is_intersected_with_grants():
    cur = Cur(**{"category_slug = ?": [("lease_agreement",), ("lease_amendment",), ("estoppel",)]})
    scope = dsp.resolve_scope(cur, ["lease_agreement", "estoppel"], dsp.SearchRequest(category="leases"))
    assert scope["types"] == ["estoppel", "lease_agreement"] and not scope["denied"]
    none = dsp.resolve_scope(cur, ["vendor_guide"], dsp.SearchRequest(category="leases"))
    assert none["denied"] is True


def test_resolve_scope_nothing_selected_follows_the_acl():
    assert dsp.resolve_scope(Cur(), None, dsp.SearchRequest())["types"] is None
    assert dsp.resolve_scope(Cur(), ["a", "b"], dsp.SearchRequest())["types"] == ["a", "b"]


# ---------------------------------------------------------------- tree
COUNTS = [("lease_agreement", 185), ("vendor_guide", 6), ("stray_type", 1)]
MAPPING = [(1, "leases", "Leases", "Lease docs", "lease_agreement"),
           (1, "leases", "Leases", "Lease docs", "lease_amendment"),   # no documents -> omitted
           (2, "vendors", "Vendor guides", "", "vendor_guide"),
           (3, "empty", "Empty category", "", "nothing_here")]


def test_category_tree_unrestricted_shows_uncategorised_and_counts():
    cur = Cur(**{"FROM Documents": COUNTS, "FROM DocumentCategories": MAPPING})
    tree = dsp.category_tree(cur, None)
    assert [c["slug"] for c in tree["categories"]] == ["leases", "vendors"], "empty category omitted"
    assert tree["categories"][0]["doc_count"] == 185 and tree["categories"][0]["types"] == [{"name": "lease_agreement", "count": 185}]
    assert tree["uncategorised"] == [{"name": "stray_type", "count": 1}]
    assert tree["total_docs"] == 192 and tree["total_types"] == 3
    assert "document_type IN" not in cur.sql("FROM Documents")[0][0]


def test_category_tree_restricted_is_scoped_and_hides_uncategorised():
    cur = Cur(**{"FROM Documents": [("lease_agreement", 185)], "FROM DocumentCategories": MAPPING})
    tree = dsp.category_tree(cur, ["lease_agreement"])
    sql, params = cur.sql("FROM Documents")[0]
    assert "document_type IN (?)" in sql and params == ("lease_agreement",)
    assert [c["slug"] for c in tree["categories"]] == ["leases"] and tree["uncategorised"] == []


def test_category_tree_deny_all_queries_nothing():
    cur = Cur()
    assert dsp.category_tree(cur, []) == {"categories": [], "uncategorised": [], "total_docs": 0, "total_types": 0}
    assert cur.calls == []


# ---------------------------------------------------------------- suggestions
def test_field_suggestions_need_a_scope(monkeypatch):
    import document_field_catalog as dfc
    monkeypatch.setattr(dfc, "suggest", lambda cur, types, q, limit: [{"path": "a", "name": "a", "display_name": "A", "doc_count": 3, "in_schema": False}])
    assert dsp.field_suggestions(Cur(), None, "a")["fields"] == []
    assert "Choose a document type" in dsp.field_suggestions(Cur(), None, "a")["hint"]
    assert dsp.field_suggestions(Cur(), ["lease_agreement"], "a")["fields"][0]["path"] == "a"


def test_attribute_suggestions_scope_and_query(monkeypatch):
    dsp._attr_cache.clear()
    cur = Cur(**{"FROM DocumentAttributions": [("Region", 4, 9, "East", "West"), ("Owner", 2, 2, "x", "x")]})
    rows = dsp.attribute_suggestions(cur, ["lease_agreement"], "")
    sql, params = cur.sql("FROM DocumentAttributions")[0]
    assert "document_type IN (?)" in sql and params == ("lease_agreement",)
    assert [r["attribute_name"] for r in rows] == ["Region", "Owner"]
    assert rows[0]["sample_values"] == ["East", "West"] and rows[1]["sample_values"] == ["x"]
    assert [r["attribute_name"] for r in dsp.attribute_suggestions(cur, ["lease_agreement"], "reg")] == ["Region"]
    assert dsp.attribute_suggestions(cur, [], "") == []


# ---------------------------------------------------------------- filters
FIELD_ROWS = [("d1", 1, "tenant", "parties.tenant", "Acme Corp"),
              ("d1", 3, "state", "governing_law.state", "NY"),
              ("d2", 1, "tenant", "parties.tenant", "Acme Corp"),      # matches only one criterion
              ("d3", 2, "state", "governing_law.state", "NY")]


def test_field_matches_are_anded_per_document_and_scoped():
    cur = Cur(**{"FROM DocumentFields df": FIELD_ROWS})
    filters = [{"field_path": "parties.tenant", "operator": "contains", "value": "acme"},
               {"field_path": "governing_law.state", "operator": "equals", "value": "NY"}]
    m = dsp.field_matches(cur, filters, ["lease_agreement"])
    assert m["documents"] == {"d1"}
    assert set(m["pages"]) == {("d1", 1), ("d1", 3)}
    assert m["pages"][("d1", 1)][0]["value"] == "Acme Corp"
    sql, params = cur.sql("FROM DocumentFields df")[0]
    assert "(df.field_path = ? AND df.field_value LIKE ?)" in sql and "d.document_type IN (?)" in sql
    assert params == ("parties.tenant", "%acme%", "governing_law.state", "NY", "lease_agreement")


def test_field_matches_any_field_and_empty_scope():
    cur = Cur(**{"FROM DocumentFields df": FIELD_ROWS})
    m = dsp.field_matches(cur, [{"field_path": "%", "operator": "equals", "value": "ny"}], None)
    assert m["documents"] == {"d1", "d3"}
    assert cur.sql("FROM DocumentFields df")[0][1] == ("ny",)
    assert dsp.field_matches(Cur(), [{"field_path": "%", "operator": "equals", "value": "x"}], []) == {"documents": set(), "pages": {}}


def test_attribute_matches_anded_per_document():
    cur = Cur(**{"FROM DocumentAttributions da": [("d1", "Region", "West"), ("d1", "Owner", "Bob"),
                                                  ("d2", "Region", "West")]})
    filters = [{"attribute_name": "Region", "operator": "equals", "value": "west"},
               {"attribute_name": "Owner", "operator": "starts_with", "value": "b"}]
    assert dsp.attribute_matches(cur, filters, None) == {"d1"}


# ---------------------------------------------------------------- rendering
def test_highlight_escapes_html_then_marks_terms():
    out = dsp.highlight_snippet("<b>Rent</b> is due; rent escalates 3%", "rent due")
    assert "<b>" not in out and "&lt;b&gt;" in out
    assert out.count('<span class="highlight">') == 3
    assert dsp.highlight_snippet("x" * 500, "", max_len=100).endswith("…")


def test_pagination_shape():
    p = dsp._pagination(2, 10, 35)
    assert p["pages"] == 4 and p["has_prev"] and p["has_next"] and p["page"] == 2
    assert list(p["iter_pages"]()) == [1, 2, 3, 4]
    assert dsp._pagination(9, 10, 35)["page"] == 4, "page clamped to the last page"


# ---------------------------------------------------------------- run_search
def _unified(passages, calls, answer=None, error=None):
    def fn(query, max_results=None, user_id=None, user_role=None, document_types=None):
        calls.append({"query": query, "max_results": max_results, "user_id": user_id,
                      "user_role": user_role, "document_types": document_types})
        return {"ok": error is None, "passages": passages, "answer": answer, "text": "", "count": len(passages), "error": error}
    return fn


PASSAGES = [{"filename": "a.pdf", "page": "1", "document_id": "d1", "document_type": "lease_agreement", "text": "rent is due", "relevance": 0.9},
            {"filename": "b.pdf", "page": "2", "document_id": "d2", "document_type": "lease_agreement", "text": "no rent", "relevance": 0.4},
            {"filename": "c.pdf", "page": "1", "document_id": "d3", "document_type": "vendor_guide", "text": "rent again", "relevance": 0.8}]
META = [("d1", None, "REF-1", None, None, None), ("d3", None, "", None, None, None)]


def test_run_search_delegates_to_the_unified_search_with_the_scope_and_filters_by_score():
    calls = []
    cur = Cur(**{"FROM Documents WHERE document_id IN": META})
    req = dsp.SearchRequest(query="rent", min_score=0.5, max_results=10)
    scope = {"types": ["lease_agreement", "vendor_guide"], "label": "leases", "denied": False, "message": ""}
    out = dsp.run_search(cur, req, scope, ("141", 2), _unified(PASSAGES, calls))
    assert calls == [{"query": "rent", "max_results": dsp.SEARCH_PASSAGES, "user_id": "141", "user_role": 2,
                      "document_types": ["lease_agreement", "vendor_guide"]}]
    assert [r["document_id"] for r in out["results"]] == ["d1", "d3"], "0.4 dropped by min_score"
    assert out["results"][0]["reference_number"] == "REF-1"
    assert '<span class="highlight">rent</span>' in out["results"][0]["snippet"]
    assert out["total"] == 2 and out["pagination"]["pages"] == 1


def test_run_search_text_plus_field_filters_intersects():
    calls = []
    cur = Cur(**{"FROM DocumentFields df": FIELD_ROWS, "FROM Documents WHERE document_id IN": META})
    req = dsp.SearchRequest(query="rent", min_score=0.0,
                            field_filters=[{"field_path": "%", "operator": "equals", "value": "NY"}])
    out = dsp.run_search(cur, req, {"types": None, "label": "", "denied": False, "message": ""},
                         (None, None), _unified(PASSAGES, calls))
    ids = [(r["document_id"], r["page_number"]) for r in out["results"]]
    assert ids == [("d1", 1), ("d3", 1)], "d2 has no NY field -> dropped; d1/d3 qualify as DOCUMENTS"
    assert [f["value"] for f in out["results"][0]["matching_fields"]] == ["NY"], \
        "the match sits on another page of d1 -> shown anyway"


def test_run_search_field_filters_only_reads_the_matching_pages():
    cur = Cur(**{"FROM DocumentFields df": FIELD_ROWS,
                 "FROM DocumentPages dp JOIN Documents d": [("d1", 1, "a.pdf", "lease_agreement", "Acme text"),
                                                            ("d1", 3, "a.pdf", "lease_agreement", "NY text"),
                                                            ("d1", 9, "a.pdf", "lease_agreement", "not a match")],
                 "FROM Documents WHERE document_id IN": META})
    req = dsp.SearchRequest(field_filters=[{"field_path": "parties.tenant", "operator": "contains", "value": "acme"},
                                           {"field_path": "governing_law.state", "operator": "equals", "value": "NY"}])
    out = dsp.run_search(cur, req, {"types": ["lease_agreement"], "label": "lease_agreement", "denied": False, "message": ""},
                         (None, None), _unified([], []))
    assert [(r["document_id"], r["page_number"]) for r in out["results"]] == [("d1", 1), ("d1", 3)]
    assert out["results"][0]["matching_fields"][0]["name"] == "Tenant"
    assert out["results"][0]["relevance_score"] == 1.0


def test_run_search_denied_scope_and_no_criteria_and_empty_filter():
    denied = {"types": [], "label": "x", "denied": True, "message": "nope"}
    out = dsp.run_search(Cur(), dsp.SearchRequest(query="q"), denied, (None, None), _unified([], []))
    assert out["error"] == "nope" and out["results"] == []
    ok = {"types": None, "label": "", "denied": False, "message": ""}
    assert dsp.run_search(Cur(), dsp.SearchRequest(), ok, (None, None), _unified(PASSAGES, []))["results"] == []
    calls = []
    out = dsp.run_search(Cur(), dsp.SearchRequest(query="q", field_filters=[{"field_path": "%", "operator": "equals", "value": "zzz"}]),
                         ok, (None, None), _unified(PASSAGES, calls))
    assert out["results"] == [] and calls == [], "a filter that matched nothing never calls the engine"


def test_run_search_surfaces_engine_answer_and_error():
    calls = []
    ok = {"types": None, "label": "", "denied": False, "message": ""}
    out = dsp.run_search(Cur(), dsp.SearchRequest(query="q"), ok, (None, None),
                         _unified([], calls, answer="No relevant documents found."))
    assert out["answer"] == "No relevant documents found." and out["results"] == []
    out = dsp.run_search(Cur(), dsp.SearchRequest(query="q"), ok, (None, None),
                         _unified([], calls, error="engine down"))
    assert out["error"] == "engine down"


def test_run_search_paginates_in_memory():
    calls = []
    many = [dict(PASSAGES[0], document_id=f"d{i}", page="1") for i in range(23)]
    cur = Cur(**{"FROM Documents WHERE document_id IN": []})
    req = dsp.SearchRequest(query="rent", min_score=0.0, max_results=10, page=3)
    out = dsp.run_search(cur, req, {"types": None, "label": "", "denied": False, "message": ""},
                         (None, None), _unified(many, calls))
    assert out["total"] == 23 and out["pagination"]["pages"] == 3 and out["pagination"]["page"] == 3
    assert [r["document_id"] for r in out["results"]] == ["d20", "d21", "d22"]
