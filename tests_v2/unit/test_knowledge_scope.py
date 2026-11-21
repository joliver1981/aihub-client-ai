"""Unit tests: attributes on knowledge documents and the attribute-scoped knowledge search.

knowledge_scope.py + the two places agent_knowledge_integration.py honours a scope
(the vector search and the knowledge tool). Everything external is replaced: the
database, the vector store, the small model, the search engines.

What must hold:
- attributes are optional, cleaned, never silently shortened, and stored next to
  what the document's metadata already holds;
- a document the Compliance pipeline filed under a retailer has that customer even
  when nothing is stored on it;
- the resolver only ever returns values that exist — whatever the model says;
- a customer-scoped search covers that customer's documents plus the ones with no
  customer attached, never another customer's;
- any failure ends in the search running over everything, as before.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import agent_knowledge_integration as aki
import knowledge_scope as ks


def _doc(doc_id, filename, attributes=None, extra_meta=None):
    meta = dict(extra_meta or {})
    if attributes is not None:
        meta["attributes"] = attributes
    return {"document_id": doc_id, "filename": filename, "knowledge_id": 100 + len(doc_id),
            "document_metadata": json.dumps(meta) if meta else None}


DOCS = [
    _doc("a1", "scan_0042.pdf", {"customer": "Acme Retail"}),
    _doc("a2", "Routing guide FINAL.pdf", {"customer": "Acme"}),          # same customer, other spelling
    _doc("g1", "DI manual.pdf", {"customer": "Globex Stores", "region": "East"}),
    _doc("g2", "DI manual (1).pdf", {"customer": "Globex Stores", "region": "West"}),
    _doc("u1", "notes.pdf"),                                              # nothing attached
]


# ---------------------------------------------------------------------------
# Cleaning and validating attributes
# ---------------------------------------------------------------------------

def test_names_are_normalised():
    assert ks.normalize_name(" Customer Name ") == "customer_name"
    assert ks.normalize_name("Region/Zone") == "region_zone"
    assert ks.normalize_name(None) == ""


@pytest.mark.parametrize("given", [
    {"Customer": " Acme  Retail ", "Region": "East", "empty": " ", "": "x"},
    '{"Customer": "Acme Retail", "Region": "East"}',
    "Customer=Acme Retail; Region=East",
    "customer: Acme Retail\nregion: East\nthis line has no pair",
])
def test_clean_attributes_accepts_the_ways_people_write_them(given):
    assert ks.clean_attributes(given) == {"customer": "Acme Retail", "region": "East"}


def test_clean_attributes_edge_cases():
    assert ks.clean_attributes(None) == {}
    assert ks.clean_attributes("") == {}
    assert ks.clean_attributes(["customer", "Acme"]) == {}
    assert ks.clean_attributes({"tags": ["a", " b ", ""]}) == {"tags": "a, b"}
    assert ks.clean_attributes('{"not": "closed"') == {}            # broken JSON, no pairs


def test_validate_refuses_instead_of_shortening():
    assert ks.validate_attributes({"customer": "Acme"}) == {"customer": "Acme"}
    with pytest.raises(ValueError, match="at most"):
        ks.validate_attributes({"customer": "x" * (ks._MAX_VALUE + 1)})
    with pytest.raises(ValueError, match="attribute name"):
        ks.validate_attributes({"n" * (ks._MAX_NAME + 1): "v"})
    with pytest.raises(ValueError, match="attribute name"):
        ks.validate_attributes("n" * (ks._MAX_NAME + 1) + "=v")
    with pytest.raises(ValueError, match="must be one object"):
        ks.validate_attributes('{"customer": "Acme"')              # broken JSON is refused, not half-read
    with pytest.raises(ValueError, match="must be one object"):
        ks.validate_attributes('{"a": 1} trailing')


def test_attributes_from_metadata_tolerates_anything():
    assert ks.attributes_from_metadata(None) == {}
    assert ks.attributes_from_metadata("not json") == {}
    assert ks.attributes_from_metadata("[1, 2]") == {}
    assert ks.attributes_from_metadata('{"knowledge_summary": "s"}') == {}
    assert ks.attributes_from_metadata('{"attributes": {"Customer": "Acme"}}') == {"customer": "Acme"}
    assert ks.attributes_from_metadata({"attributes": "customer=Acme"}) == {"customer": "Acme"}


# ---------------------------------------------------------------------------
# Storing attributes (database replaced)
# ---------------------------------------------------------------------------

class _FakeDb:
    def __init__(self, rows):
        self.rows = dict(rows)          # document_id -> metadata text
        self.commits = 0
        self.closed = 0
        self._last = None

    def execute(self, sql, *args):
        if sql.lstrip().upper().startswith("SELECT"):
            doc_id = args[0]
            self._last = (self.rows[doc_id],) if doc_id in self.rows else None
        elif sql.lstrip().upper().startswith("UPDATE"):
            self.rows[args[1]] = args[0]

    def fetchone(self):
        return self._last

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed += 1


def _with_db(rows):
    db = _FakeDb(rows)
    return db, patch.object(ks, "_db", lambda: (db, db))


def test_set_attributes_merges_and_keeps_the_rest_of_the_metadata():
    db, p = _with_db({"d1": json.dumps({"knowledge_summary": "about cartons",
                                        "attributes": {"customer": "Acme", "region": "East"}})})
    with p:
        out = ks.set_document_attributes("d1", {"Region": "West", "department": "Toys"})
    assert out == {"customer": "Acme", "region": "West", "department": "Toys"}
    stored = json.loads(db.rows["d1"])
    assert stored["knowledge_summary"] == "about cartons" and stored["attributes"] == out
    assert db.commits == 1 and db.closed == 1


def test_set_attributes_empty_value_removes_and_replace_replaces():
    db, p = _with_db({"d1": json.dumps({"attributes": {"customer": "Acme", "region": "East"}})})
    with p:
        assert ks.set_document_attributes("d1", {"region": ""}) == {"customer": "Acme"}
        assert ks.set_document_attributes("d1", "department=Toys", replace=True) == {"department": "Toys"}
        assert ks.set_document_attributes("d1", "", replace=True) == {}
    assert "attributes" not in json.loads(db.rows["d1"])


def test_set_attributes_on_a_document_without_metadata():
    db, p = _with_db({"d1": None, "d2": "   "})
    with p:
        assert ks.set_document_attributes("d1", {"customer": "Acme"}) == {"customer": "Acme"}
        assert ks.set_document_attributes("d2", {"customer": "Acme"}) == {"customer": "Acme"}
    assert json.loads(db.rows["d1"]) == {"attributes": {"customer": "Acme"}}


def test_set_attributes_never_overwrites_metadata_it_cannot_read():
    db, p = _with_db({"d1": "this is not json", "d2": "[1, 2, 3]"})
    with p:
        assert ks.set_document_attributes("d1", {"customer": "Acme"}) is None
        assert ks.set_document_attributes("d2", {"customer": "Acme"}) is None
        assert ks.set_document_attributes("missing", {"customer": "Acme"}) is None
    assert db.rows["d1"] == "this is not json" and db.rows["d2"] == "[1, 2, 3]" and db.commits == 0


def test_set_attributes_validates_before_touching_the_database():
    db, p = _with_db({"d1": None})
    with p, pytest.raises(ValueError):
        ks.set_document_attributes("d1", {"customer": "x" * 1000})
    assert db.rows["d1"] is None and db.commits == 0


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def test_catalog_uses_the_customer_on_record_only_when_none_is_stored():
    catalog = ks.build_catalog(DOCS, {"u1": "Initech Supply", "a1": "Somebody Else"})
    assert catalog["u1"] == {"customer": "Initech Supply"}          # filled from the compliance record
    assert catalog["a1"] == {"customer": "Acme Retail"}             # what is stored wins
    assert catalog["g1"] == {"customer": "Globex Stores", "region": "East"}


def test_known_values_and_what_can_steer_a_search():
    catalog = ks.build_catalog(DOCS)
    assert ks.known_values(catalog, "customer") == ["Globex Stores", "Acme", "Acme Retail"]
    assert ks.scopable(catalog) == {"customer": ["Globex Stores", "Acme", "Acme Retail"]}
    # one customer only: nothing to narrow
    assert ks.scopable(ks.build_catalog([DOCS[2], DOCS[3]])) == {}


def test_only_configured_attributes_steer():
    catalog = ks.build_catalog(DOCS)
    with patch.object(ks.cfg, "KNOWLEDGE_SCOPE_ATTRIBUTES", "Region, customer", create=True):
        assert ks.scope_attribute_names() == ["region", "customer"]
        assert list(ks.scopable(catalog)) == ["region", "customer"]
    with patch.object(ks.cfg, "KNOWLEDGE_SCOPE_ATTRIBUTES", "", create=True):
        assert ks.scopable(catalog) == {}


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

VALUES = {"customer": ["Globex Stores", "Acme", "Acme Retail"]}


def test_resolver_prompt_lists_the_known_values_and_both_wordings():
    prompt = ks.build_resolver_prompt(VALUES, "acme carton weight", "what's the max carton weight for acmee?", "(none)")
    for v in VALUES["customer"]:
        assert f"  - {v}" in prompt
    assert "what's the max carton weight for acmee?" in prompt and "acme carton weight" in prompt
    assert "Never guess" in prompt and "misspelling" in prompt


def test_reply_keeps_only_values_that_exist():
    raw = json.dumps({"customer": [
        {"label": "Acme", "values": ["acme retail", "ACME", "Acme Corporation"]},       # one invented
        {"label": "Initech", "values": ["Initech Supply"]},                            # all invented
        {"label": "Globex", "values": ["Globex Stores", "Acme"]},                      # Acme already taken
    ], "reason": "x"})
    out = ks.parse_resolver_reply(raw, VALUES)
    assert out == {"customer": [{"label": "Acme", "values": ["Acme Retail", "Acme"]},
                                {"label": "Globex", "values": ["Globex Stores"]}]}


@pytest.mark.parametrize("raw", ["", "I think it is Acme.", "{not json", "[]", '{"customer": []}',
                                 '{"customer": "Acme"}', '{"other": [{"values": ["Acme"]}]}'])
def test_reply_that_names_nothing_usable_means_no_scope(raw):
    assert ks.parse_resolver_reply(raw, VALUES) in ({}, {"customer": []})
    assert not ks.parse_resolver_reply(raw, VALUES).get("customer")


def test_reply_in_a_code_fence_and_as_plain_strings():
    raw = '```json\n{"customer": ["globex stores"], "reason": "named"}\n```'
    assert ks.parse_resolver_reply(raw, VALUES) == {"customer": [{"label": "globex stores", "values": ["Globex Stores"]}]}


def test_resolve_calls_the_model_once_per_question_and_caches():
    calls = []

    def llm(prompt, system):
        calls.append(prompt)
        return '{"customer": [{"label": "Acme", "values": ["Acme", "Acme Retail"]}]}'

    ks._cache.clear()
    with patch.object(aki, "_format_chat_history_for_detector", lambda h: "(none)"):
        one = ks.resolve(VALUES, "acme pallets", "pallet rules for acme?", None, llm_call=llm, cache_key="77")
        two = ks.resolve(VALUES, "acme pallets", "pallet rules for acme?", None, llm_call=llm, cache_key="77")
        other = ks.resolve(VALUES, "globex pallets", "and globex?", None, llm_call=llm, cache_key="77")
    assert one == two == other and len(calls) == 2
    ks._cache.clear()


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

def _ids(group, key="documents"):
    return [d["document_id"] for d in group[key]]


def test_one_group_per_customer_with_its_spellings_and_the_untagged():
    catalog = ks.build_catalog(DOCS)
    sel = {"customer": [{"label": "Acme", "values": ["Acme", "Acme Retail"]},
                        {"label": "Globex", "values": ["Globex Stores"]}]}
    acme, globex = ks.plan_groups(DOCS, catalog, sel)
    assert _ids(acme) == ["a1", "a2", "u1"] and _ids(acme, "unattributed") == ["u1"]
    assert _ids(globex) == ["g1", "g2", "u1"] and _ids(globex, "unattributed") == ["u1"]
    assert acme["label"] == "Acme" and acme["values"] == ["Acme", "Acme Retail"]


def test_a_second_steering_attribute_narrows_every_group():
    catalog = ks.build_catalog(DOCS)
    sel = {"customer": [{"label": "Globex", "values": ["Globex Stores"]}],
           "region": [{"label": "East", "values": ["East"]}]}
    with patch.object(ks.cfg, "KNOWLEDGE_SCOPE_ATTRIBUTES", "customer,region", create=True):
        (g,) = ks.plan_groups(DOCS, catalog, sel)
    assert _ids(g) == ["g1", "u1"] and g["narrowed_by"] == {"region": ["East"]}


def test_nothing_selected_means_no_groups():
    assert ks.plan_groups(DOCS, ks.build_catalog(DOCS), {}) == []


# ---------------------------------------------------------------------------
# decide(): the whole decision, and every way it stands down
# ---------------------------------------------------------------------------

def _decide(docs=DOCS, reply='{"customer": [{"label": "Acme", "values": ["Acme", "Acme Retail"]}]}', derived=None, **kw):
    ks._cache.clear()
    with patch.object(ks, "compliance_customers", lambda ids: derived or {}), \
         patch.object(aki, "_format_chat_history_for_detector", lambda h: "(none)"):
        return ks.decide(77, docs, "carton weight", "carton weight for acme", None,
                         llm_call=(lambda p, s: reply() if callable(reply) else reply), **kw)


def test_decide_scopes_to_the_customer_named():
    d = _decide()
    assert [g["label"] for g in d["groups"]] == ["Acme"] and not d["unresolved"]
    assert _ids(d["groups"][0]) == ["a1", "a2", "u1"]
    assert d["values"] == {"customer": ["Globex Stores", "Acme", "Acme Retail"]}


def test_decide_with_no_customer_named_searches_everything_and_says_so():
    d = _decide(reply='{"customer": [], "reason": "no customer named"}')
    assert d["groups"] == [] and d["unresolved"] and d["values"]["customer"]


def test_decide_uses_customers_on_record_for_untagged_documents():
    docs = [_doc("x1", "a.pdf"), _doc("x2", "b.pdf"), _doc("x3", "c.pdf")]
    d = _decide(docs, reply='{"customer": [{"label": "Acme", "values": ["Acme Retail"]}]}',
                derived={"x1": "Acme Retail", "x2": "Globex Stores"})
    assert _ids(d["groups"][0]) == ["x1", "x3"] and _ids(d["groups"][0], "unattributed") == ["x3"]


def test_decide_stands_down_when_there_is_nothing_to_scope_by():
    assert _decide([_doc("x1", "a.pdf"), _doc("x2", "b.pdf")]) is None            # no attributes anywhere
    assert _decide([DOCS[0]]) is None                                             # a single document
    assert _decide([]) is None
    one_customer = _decide([DOCS[2], DOCS[3]])
    assert one_customer["groups"] == [] and one_customer["values"] == {}           # attributes, one customer


def test_decide_is_switched_off_by_the_setting():
    with patch.object(ks.cfg, "KNOWLEDGE_SCOPE_ENABLED", False, create=True):
        assert _decide() is None


def test_decide_never_raises():
    def boom():
        raise RuntimeError("model unavailable")
    assert _decide(reply=boom) is None
    with patch.object(ks, "build_catalog", side_effect=RuntimeError("bad data")):
        assert ks.decide(77, DOCS, "q") is None


def test_decide_with_no_reply_from_the_model_searches_everything():
    d = _decide(reply=None)
    assert d["groups"] == [] and d["unresolved"]


# ---------------------------------------------------------------------------
# What the agent is told
# ---------------------------------------------------------------------------

def test_scoped_result_says_what_was_searched_and_what_was_not():
    d = _decide()
    g = d["groups"][0]
    text = "SOURCE [scan_0042.pdf p.9]:\nMaximum carton weight 40 lb\nSOURCE [notes.pdf p.1]:\nmisc"
    block = ks.format_group(g, text, d["catalog"])
    assert block.startswith("===== CUSTOMER: Acme =====")
    assert 'Searched ONLY the 2 document(s) attached to this customer (stored as "Acme", "Acme Retail")' in block
    assert "and 1 document(s) with no customer attached" in block
    assert "No customer is attached to: notes.pdf" in block and "NOT confirmed to be Acme's" in block
    out = ks.format_scoped(d, [block])
    assert out.startswith("[Knowledge search scoped by customer: Acme]")
    assert "NOT searched — documents of other customers: Globex Stores" in out
    assert "do not fill it from another block" in out


def test_empty_block_is_stated_not_hidden():
    d = _decide()
    assert "(nothing found in this customer's documents)" in ks.format_group(d["groups"][0], "", d["catalog"])


def test_unscoped_result_names_the_customers_and_whose_each_source_is():
    d = _decide(reply='{"customer": []}')
    out = ks.format_unscoped(d, "SOURCE [DI manual.pdf p.7]:\n50 LBS\nSOURCE [notes.pdf p.1]:\nmisc", DOCS)
    assert out.startswith("[No customer could be identified in the question, so ALL documents were searched.")
    assert "Globex Stores, Acme, Acme Retail" in out
    assert "- DI manual.pdf -> customer: Globex Stores; region: East" in out
    assert "- notes.pdf -> no attributes attached" in out
    assert "scan_0042.pdf" not in out                      # not in the result, not in the list


# ---------------------------------------------------------------------------
# agent_knowledge_integration honours the scope
# ---------------------------------------------------------------------------

def _vector_engine():
    engine = MagicMock()
    engine.search.return_value = []
    return engine


def _filter_ids(engine):
    filters = engine.search.call_args.kwargs["filters"]
    clauses = [c for c in filters["$and"] if "document_id" in c]
    assert len(clauses) <= 1, clauses                       # one document filter, not two
    return clauses[0]["document_id"]["$in"] if clauses else None


def test_vector_search_is_limited_to_the_scope_and_to_active_documents():
    engine = _vector_engine()
    with patch.object(aki, "_get_knowledge_vector_engine", return_value=engine), \
         patch.object(aki, "_get_active_knowledge_document_ids", return_value={"a1", "a2", "g1"}):
        token = aki._SCOPE_DOCUMENT_IDS.set(frozenset({"a1", "a2", "gone"}))
        try:
            aki.search_knowledge_vectors("carton weight", 77, user_id="5")
        finally:
            aki._SCOPE_DOCUMENT_IDS.reset(token)
        assert _filter_ids(engine) == ["a1", "a2"]
        aki.search_knowledge_vectors("carton weight", 77, user_id="5")          # no scope: as before
        assert _filter_ids(engine) == ["a1", "a2", "g1"]


def test_vector_search_returns_nothing_when_the_scope_has_no_active_document():
    engine = _vector_engine()
    with patch.object(aki, "_get_knowledge_vector_engine", return_value=engine), \
         patch.object(aki, "_get_active_knowledge_document_ids", return_value={"g1"}):
        token = aki._SCOPE_DOCUMENT_IDS.set(frozenset({"a1"}))
        try:
            assert aki.search_knowledge_vectors("q", 77, user_id="5") == []
        finally:
            aki._SCOPE_DOCUMENT_IDS.reset(token)
    engine.search.assert_not_called()


def test_vector_search_scope_works_when_the_active_gate_is_unavailable():
    engine = _vector_engine()
    with patch.object(aki, "_get_knowledge_vector_engine", return_value=engine), \
         patch.object(aki, "_get_active_knowledge_document_ids", return_value=None):
        token = aki._SCOPE_DOCUMENT_IDS.set(frozenset({"a1"}))
        try:
            aki.search_knowledge_vectors("q", 77, user_id="5")
        finally:
            aki._SCOPE_DOCUMENT_IDS.reset(token)
    assert _filter_ids(engine) == ["a1"]


def _run_tool(decision, searches, decide_raises=False):
    """Call the knowledge tool with the scope decision and the engine replaced."""
    seen = []

    def fake_search(self, query, documents, history, latest):
        seen.append((sorted(d["document_id"] for d in documents), aki._SCOPE_DOCUMENT_IDS.get()))
        return searches(documents)

    def fake_decide(*a, **k):
        if decide_raises:
            raise RuntimeError("scope module broken")
        return decision

    with patch.object(aki, "get_agent_knowledge_documents", return_value=list(DOCS)), \
         patch.object(aki.KnowledgeTool, "_search_documents", fake_search), \
         patch.object(ks, "decide", fake_decide):
        tool = aki.KnowledgeTool(77, user_id="5").get_knowledge_tool()
        out = tool.invoke({"query": "carton weight for acme and globex"})
    return out, seen


def test_tool_searches_each_customer_separately_inside_its_scope():
    catalog = ks.build_catalog(DOCS)
    sel = {"customer": [{"label": "Acme", "values": ["Acme", "Acme Retail"]},
                        {"label": "Globex", "values": ["Globex Stores"]}]}
    decision = {"groups": ks.plan_groups(DOCS, catalog, sel), "catalog": catalog,
                "values": ks.scopable(catalog), "unresolved": False}
    out, seen = _run_tool(decision, lambda docs: "found in " + ",".join(d["filename"] for d in docs))
    assert seen == [(["a1", "a2", "u1"], frozenset({"a1", "a2", "u1"})),
                    (["g1", "g2", "u1"], frozenset({"g1", "g2", "u1"}))]
    assert aki._SCOPE_DOCUMENT_IDS.get() is None                     # reset after the search
    assert out.index("===== CUSTOMER: Acme =====") < out.index("===== CUSTOMER: Globex =====")
    assert "[Knowledge search scoped by customer: Acme, Globex]" in out


def test_tool_resets_the_scope_even_when_a_search_fails():
    catalog = ks.build_catalog(DOCS)
    sel = {"customer": [{"label": "Acme", "values": ["Acme"]}]}
    decision = {"groups": ks.plan_groups(DOCS, catalog, sel), "catalog": catalog,
                "values": ks.scopable(catalog), "unresolved": False}

    def boom(docs):
        raise RuntimeError("engine down")

    out, _seen = _run_tool(decision, boom)
    assert out.startswith("Error accessing knowledge documents") and aki._SCOPE_DOCUMENT_IDS.get() is None


def test_tool_without_a_scope_runs_one_search_over_everything():
    out, seen = _run_tool(None, lambda docs: "plain result")
    assert seen == [(["a1", "a2", "g1", "g2", "u1"], None)] and out == "plain result"


def test_tool_falls_back_to_everything_when_scoping_breaks():
    out, seen = _run_tool(None, lambda docs: "plain result", decide_raises=True)
    assert seen == [(["a1", "a2", "g1", "g2", "u1"], None)] and out == "plain result"


def test_tool_with_customers_on_file_but_none_named_searches_all_and_labels_sources():
    catalog = ks.build_catalog(DOCS)
    decision = {"groups": [], "catalog": catalog, "values": ks.scopable(catalog), "unresolved": True}
    out, seen = _run_tool(decision, lambda docs: "SOURCE [DI manual.pdf p.7]:\n50 LBS")
    assert seen == [(["a1", "a2", "g1", "g2", "u1"], None)]
    assert "ALL documents were searched" in out and "- DI manual.pdf -> customer: Globex Stores" in out
