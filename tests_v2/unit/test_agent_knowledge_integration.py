"""Unit tests for agent_knowledge_integration.py.

We test the deterministic helpers (formatters, page-marker round-trip,
query normalisation) directly, and the higher-level functions
(``index_knowledge_document``, ``search_knowledge_vectors``,
``get_all_knowledge_summaries``) with all I/O mocked — DB, vector engine,
and LLM proxy.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

import agent_knowledge_integration as aki


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_normalize_search_query_dedupes_and_caps():
    q = "lease lease HVAC requirements requirements"
    out = aki._normalize_search_query(q)
    # 'lease' appears once, 'requirements' once. Order preserved.
    assert out.lower().count("lease") == 1
    assert out.lower().count("requirements") == 1


def test_normalize_search_query_respects_max_chars():
    q = "word " * 1000
    out = aki._normalize_search_query(q, max_chars=50)
    assert len(out) <= 50


def test_build_document_with_page_markers_drops_empty_pages():
    pages = [
        (10, 1, "alpha", "a.pdf", "lease"),
        (11, 2, "   ", "a.pdf", "lease"),  # blank — must be skipped
        (12, 3, "beta", "a.pdf", "lease"),
    ]
    text, page_map = aki._build_document_with_page_markers(pages)
    assert "PAGE 1" in text
    assert "PAGE 2" not in text       # skipped
    assert "PAGE 3" in text
    assert page_map == {1: 10, 3: 12}


def test_derive_page_from_chunk_extracts_first_marker():
    chunk = "===== PAGE 5 =====\n\nbody text"
    page, cleaned = aki._derive_page_from_chunk(chunk, running_page=3)
    assert page == 5
    assert "PAGE 5" not in cleaned
    assert "body text" in cleaned


def test_derive_page_from_chunk_keeps_running_page_when_no_marker():
    page, cleaned = aki._derive_page_from_chunk("just body", running_page=7)
    assert page == 7
    assert cleaned == "just body"


def test_build_embed_text_prepends_section_header_when_present():
    meta = {"document_identifier": "Acme Lease 2026",
            "section_breadcrumb": "Article 7 > HVAC"}
    out = aki._build_embed_text("the body", meta)
    assert "[Acme Lease 2026]" in out
    assert "[Section: Article 7 > HVAC]" in out
    assert "the body" in out


def test_build_embed_text_returns_bare_text_without_section_meta():
    assert aki._build_embed_text("plain body", {}) == "plain body"


def test_format_chunk_for_ai_legacy_path():
    out = aki._format_chunk_for_ai({}, "page body", "file.pdf", 4)
    assert "file.pdf" in out
    assert "(page 4)" in out
    assert "page body" in out


def test_format_chunk_for_ai_with_section_header():
    meta = {"document_identifier": "Doc X",
            "section_breadcrumb": "Sec 1 > Sub",
            "section_summary": "About foo"}
    out = aki._format_chunk_for_ai(meta, "body", "ignored.pdf", 9)
    assert "Doc X" in out
    assert "Sec 1 > Sub" in out
    assert "About foo" in out
    assert "page 9" in out


def test_format_knowledge_response_serves_whole_pages_within_budget():
    # Admit-or-deny policy (2026-08-25): a page is NEVER sliced mid-text.
    # A 60K page (the old per-page cap would have cut it at 50K) comes back
    # whole; the budget only stops BETWEEN pages, with omissions enumerated.
    big = "x" * 60_000
    content = {"d1": {"filename": "big.pdf", "document_type": "lease",
                       "pages": {1: big}}}
    out = aki._format_knowledge_response(content, apply_caps=True)
    assert out.count("x") == 60_000
    assert "truncated" not in out.lower()

    # Over the total budget: pages past the boundary are omitted whole and
    # named, with the escape-hatch tools called out.
    huge = "y" * 250_000
    content2 = {"d1": {"filename": "big.pdf", "document_type": "lease",
                        "pages": {1: huge, 2: huge}}}
    out2 = aki._format_knowledge_response(content2, apply_caps=True)
    assert "NOT INCLUDED" in out2
    assert "big.pdf page 2" in out2
    assert "get_document_pages" in out2


def test_format_knowledge_response_no_caps_returns_full():
    big = "x" * 60_000
    content = {"d1": {"filename": "big.pdf", "document_type": "lease",
                       "pages": {1: big}}}
    out = aki._format_knowledge_response(content, apply_caps=False)
    assert out.count("x") == 60_000


def test_sample_document_text_returns_full_when_under_budget():
    pages = ["short page"]
    out = aki._sample_document_text(pages, total_chars=1000, n_points=3)
    assert "short page" in out


def test_sample_document_text_stratified_when_over_budget():
    # Build text long enough that stratification kicks in.
    pages = ["A" * 5000, "B" * 5000, "C" * 5000]
    out = aki._sample_document_text(pages, total_chars=900, n_points=3)
    assert len(out) <= 900
    # Should contain content from across the doc — at least an A and either
    # B or C section.
    assert "A" in out and ("B" in out or "C" in out)


def test_split_text_into_chunks_fallback_when_chunker_fails(monkeypatch):
    """Force TextChunker to error → fallback paragraph splitter kicks in."""
    class _BoomChunker:
        def __init__(self, *a, **kw): pass
        def chunk_text(self, *a, **kw): raise RuntimeError("boom")

    monkeypatch.setattr("TextChunker_LLM.TextChunker", _BoomChunker)
    text = "para one\n\npara two\n\npara three"
    chunks = aki._split_text_into_chunks(text, chunk_size=100)
    assert chunks  # non-empty
    joined = "\n".join(chunks)
    assert "para one" in joined and "para two" in joined and "para three" in joined


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

def test_index_knowledge_document_no_vector_engine_returns_false(monkeypatch):
    monkeypatch.setattr(aki, "_get_knowledge_vector_engine", lambda: None)
    assert aki.index_knowledge_document("d1", agent_id=1) is False


def test_index_knowledge_document_no_pages_returns_false(monkeypatch):
    monkeypatch.setenv("API_KEY", "k")
    monkeypatch.setattr(aki, "_get_knowledge_vector_engine", lambda: MagicMock())
    cur = MagicMock()
    cur.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cur
    monkeypatch.setattr(aki, "get_db_connection", lambda: conn)
    assert aki.index_knowledge_document("d-empty", agent_id=1) is False


def test_index_knowledge_document_calls_vector_engine(monkeypatch):
    monkeypatch.setenv("API_KEY", "k")
    vec = MagicMock()
    monkeypatch.setattr(aki, "_get_knowledge_vector_engine", lambda: vec)

    cur = MagicMock()
    cur.fetchall.return_value = [
        (100, 1, "page one text", "lease.pdf", "lease_agreement"),
        (101, 2, "page two text", "lease.pdf", "lease_agreement"),
    ]
    conn = MagicMock()
    conn.cursor.return_value = cur
    monkeypatch.setattr(aki, "get_db_connection", lambda: conn)
    # Skip summary generation (relies on Anthropic).
    monkeypatch.setattr(aki, "generate_knowledge_summary", lambda *a, **kw: "")

    # Stub the chunker to return deterministic chunks.
    class _StubChunker:
        def __init__(self, *a, **kw): pass
        def chunk_text(self, text, metadata):
            return [
                {"text": "===== PAGE 1 =====\npage one text", "metadata": metadata},
                {"text": "===== PAGE 2 =====\npage two text", "metadata": metadata},
            ]

    monkeypatch.setattr(aki, "TextChunker", _StubChunker)

    ok = aki.index_knowledge_document("doc-7", agent_id=42, user_id="u-1")
    assert ok is True
    assert vec.index.called

    docs = vec.index.call_args.kwargs["documents"]
    metas = vec.index.call_args.kwargs["metadatas"]
    ids = vec.index.call_args.kwargs["ids"]
    assert len(docs) == 2 == len(metas) == len(ids)
    # Metadata must contain the user/agent isolation keys.
    for m in metas:
        assert m["agent_id"] == "42"
        assert m["user_id"] == "u-1"
        assert m["document_id"] == "doc-7"
    # Chunk ids must be unique and prefixed.
    assert all(i.startswith("kb_doc-7_c") for i in ids)
    assert len(set(ids)) == len(ids)


def test_index_knowledge_document_uses_shared_when_no_user(monkeypatch):
    """When user_id is None, vectors are tagged 'SHARED' for whole-agent access."""
    monkeypatch.setenv("API_KEY", "k")
    vec = MagicMock()
    monkeypatch.setattr(aki, "_get_knowledge_vector_engine", lambda: vec)

    cur = MagicMock()
    cur.fetchall.return_value = [(1, 1, "text", "f.pdf", "x")]
    conn = MagicMock()
    conn.cursor.return_value = cur
    monkeypatch.setattr(aki, "get_db_connection", lambda: conn)
    monkeypatch.setattr(aki, "generate_knowledge_summary", lambda *a, **kw: "")

    class _StubChunker:
        def __init__(self, *a, **kw): pass
        def chunk_text(self, text, metadata):
            return [{"text": "===== PAGE 1 =====\ntext", "metadata": metadata}]

    monkeypatch.setattr(aki, "TextChunker", _StubChunker)
    aki.index_knowledge_document("doc-x", agent_id=5, user_id=None)
    assert vec.index.called
    metas = vec.index.call_args.kwargs["metadatas"]
    assert metas[0]["user_id"] == "SHARED"


# ---------------------------------------------------------------------------
# Searching
# ---------------------------------------------------------------------------

def test_search_knowledge_vectors_refuses_without_user_id(monkeypatch, caplog):
    monkeypatch.setattr(aki, "_get_knowledge_vector_engine", lambda: MagicMock())
    with caplog.at_level(logging.WARNING):
        out = aki.search_knowledge_vectors("anything", agent_id=1, user_id=None)
    assert out == []
    # Ensure we logged the refusal (data-leakage guard).
    assert any("without user_id" in r.message for r in caplog.records)


def test_search_knowledge_vectors_builds_isolation_filter(monkeypatch):
    vec = MagicMock()
    vec.search.return_value = [
        {"text": "hit", "metadata": {"agent_id": "5"}, "score": 0.1}
    ]
    monkeypatch.setattr(aki, "_get_knowledge_vector_engine", lambda: vec)

    out = aki.search_knowledge_vectors("query", agent_id=5, user_id="u-9",
                                        top_k=3)
    assert out == [{"text": "hit", "metadata": {"agent_id": "5"}, "score": 0.1}]
    filters = vec.search.call_args.kwargs["filters"]
    # MUST require agent_id == 5 AND (user == u-9 OR SHARED).
    assert "$and" in filters
    parts = filters["$and"]
    assert {"agent_id": "5"} in parts
    or_clause = next((p for p in parts if "$or" in p), None)
    assert or_clause is not None
    ors = or_clause["$or"]
    assert {"user_id": "u-9"} in ors
    assert {"user_id": "SHARED"} in ors


def test_search_knowledge_vectors_returns_empty_on_engine_failure(monkeypatch):
    monkeypatch.setattr(aki, "_get_knowledge_vector_engine", lambda: None)
    out = aki.search_knowledge_vectors("q", agent_id=1, user_id="u")
    assert out == []


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------

def test_get_all_knowledge_summaries_parses_metadata_json(monkeypatch):
    monkeypatch.setenv("API_KEY", "k")
    cur = MagicMock()
    cur.fetchall.return_value = [
        ("doc-1", "a.pdf", "lease", '{"knowledge_summary": "lease about HVAC"}'),
        ("doc-2", "b.pdf", "vendor", None),
        ("doc-3", "c.pdf", "other", "not-valid-json"),
    ]
    conn = MagicMock()
    conn.cursor.return_value = cur
    monkeypatch.setattr(aki, "get_db_connection", lambda: conn)

    out = aki.get_all_knowledge_summaries(agent_id=1, user_id="u-1")
    assert len(out) == 3
    assert out[0]["summary"] == "lease about HVAC"
    assert out[1]["summary"] == ""
    assert out[2]["summary"] == ""


def test_get_all_knowledge_summaries_swallows_db_error(monkeypatch):
    def _boom():
        raise RuntimeError("boom")
    monkeypatch.setattr(aki, "get_db_connection", _boom)
    assert aki.get_all_knowledge_summaries(agent_id=1) == []


# ---------------------------------------------------------------------------
# remove_knowledge_document_vectors — queues a delete
# ---------------------------------------------------------------------------

def test_remove_knowledge_document_vectors_enqueues(monkeypatch):
    calls = []
    monkeypatch.setattr(aki, "queue_knowledge_vector_delete",
                        lambda doc_id: calls.append(doc_id))
    aki.remove_knowledge_document_vectors("doc-99")
    assert calls == ["doc-99"]


# ---------------------------------------------------------------------------
# Router — defaults to NEEDLE on LLM failure
# ---------------------------------------------------------------------------

def test_route_knowledge_query_falls_back_to_needle_on_error(monkeypatch):
    # Force both Anthropic-client and proxy paths to fail.
    monkeypatch.setattr("api_keys_config.create_anthropic_client",
                        lambda: (_ for _ in ()).throw(RuntimeError("no key")))
    out = aki.route_knowledge_query("anything", doc_count=3, total_chars=1234)
    assert out == "NEEDLE"


def test_route_knowledge_query_normalizes_proxy_response(monkeypatch):
    """When proxy returns 'fan out' we still classify as FANOUT."""
    monkeypatch.setattr("api_keys_config.create_anthropic_client",
                        lambda: (None, {}))
    fake_proxy = MagicMock()
    fake_proxy.messages_create.return_value = {
        "content": [{"text": "Fan-out"}]
    }
    monkeypatch.setattr("CommonUtils.AnthropicProxyClient", lambda: fake_proxy)
    out = aki.route_knowledge_query("compare X across", doc_count=5,
                                     total_chars=10_000)
    assert out == "FANOUT"
