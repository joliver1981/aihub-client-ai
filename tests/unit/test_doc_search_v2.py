"""Tests for the doc_search_v2 factory (selection + breaker), SWEEP, and NEEDLE engines."""
import sys
import types
from unittest.mock import patch

import pytest

import config as cfg
from doc_search_v2 import factory
from doc_search_v2 import needle
from doc_search_v2 import sweep


@pytest.fixture(autouse=True)
def clean_factory(monkeypatch):
    factory.reset_breaker()
    sweep._sweep_cache.clear()
    needle._index_cache.clear()
    monkeypatch.setattr(cfg, 'DOC_SEARCH_ENGINE_DEFAULT', 'legacy', raising=False)
    monkeypatch.setattr(cfg, 'DOC_SEARCH_V2_AGENT_IDS', '', raising=False)
    monkeypatch.setattr(cfg, 'DOC_SEARCH_LEGACY_AGENT_IDS', '', raising=False)
    monkeypatch.setattr(cfg, 'DOC_SEARCH_V2_BREAKER_THRESHOLD', 3, raising=False)
    monkeypatch.setattr(cfg, 'DOC_SEARCH_V2_BREAKER_COOLDOWN_S', 600, raising=False)
    monkeypatch.setattr(cfg, 'DOC_SEARCH_V2_FORCE_ERROR', False, raising=False)
    yield
    factory.reset_breaker()


@pytest.mark.unit
class TestFactoryResolution:
    def test_default_is_legacy(self):
        assert factory.resolve_engine(42) == 'legacy'

    def test_allowlist_selects_v2(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_SEARCH_V2_AGENT_IDS', '42, 99', raising=False)
        assert factory.resolve_engine(42) == 'v2'
        assert factory.resolve_engine(7) == 'legacy'

    def test_denylist_wins_over_allowlist(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_SEARCH_V2_AGENT_IDS', '42', raising=False)
        monkeypatch.setattr(cfg, 'DOC_SEARCH_LEGACY_AGENT_IDS', '42', raising=False)
        assert factory.resolve_engine(42) == 'legacy'

    def test_global_default_v2(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_SEARCH_ENGINE_DEFAULT', 'v2', raising=False)
        assert factory.resolve_engine(1) == 'v2'

    def test_denylist_wins_over_global_default(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_SEARCH_ENGINE_DEFAULT', 'v2', raising=False)
        monkeypatch.setattr(cfg, 'DOC_SEARCH_LEGACY_AGENT_IDS', '5', raising=False)
        assert factory.resolve_engine(5) == 'legacy'
        assert factory.resolve_engine(6) == 'v2'


@pytest.mark.unit
class TestBreaker:
    def test_breaker_opens_after_threshold_and_pins_legacy(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_SEARCH_V2_AGENT_IDS', '42', raising=False)
        for _ in range(3):
            factory.record_v2_failure()
        assert factory.breaker_open() is True
        assert factory.resolve_engine(42) == 'legacy'

    def test_success_resets_failure_count(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_SEARCH_V2_AGENT_IDS', '42', raising=False)
        factory.record_v2_failure()
        factory.record_v2_failure()
        factory.record_v2_success()
        factory.record_v2_failure()
        assert factory.breaker_open() is False
        assert factory.resolve_engine(42) == 'v2'


@pytest.mark.unit
class TestSweepInternals:
    def test_parse_clean_json(self):
        data, ok = sweep._parse_map_json('{"answer":"landlord","evidence_quote":"q","page":3,"confidence":90,"not_found":false}')
        assert ok and data['answer'] == 'landlord' and data['page'] == 3

    def test_parse_fenced_json(self):
        data, ok = sweep._parse_map_json('```json\n{"answer":"x","not_found":false}\n```')
        assert ok and data['answer'] == 'x'

    def test_parse_garbage_flags_fallback(self):
        data, ok = sweep._parse_map_json('The tenant handles HVAC.')
        assert not ok and data is None

    def test_chunking_never_truncates(self):
        pages = {1: 'a' * 250_000, 2: 'b' * 250_000}
        chunks = sweep._doc_text_chunks(pages)
        assert sum(len(c) for c in chunks) >= 500_000
        assert all(len(c) <= sweep._MAX_MAP_CHARS for c in chunks)

    def test_cost_estimate_scale(self):
        # 15M tokens of input ≈ $15 on Haiku — the plan's anchor number
        est = sweep._estimate_cost_usd(60_000_000, 500)
        assert 14 < est < 18


def _stub_aki(route='FANOUT', docs=None, llm_response=None):
    stub = types.ModuleType('agent_knowledge_integration')
    stub.route_knowledge_query = lambda q, n, c: route
    stub._load_agent_knowledge_contents = lambda ids, documents: docs or {}
    stub._haiku_call_with_fallback = lambda prompt, system, max_tokens, temp: llm_response
    return stub


@pytest.mark.unit
class TestKnowledgeSearchV2:
    DOCS = [{'document_id': 'd1'}, {'document_id': 'd2'}]
    CONTENTS = {
        'd1': {'filename': 'Lease A.pdf', 'pages': {1: 'Landlord shall maintain all HVAC systems.'}},
        'd2': {'filename': 'Lease B.pdf', 'pages': {1: 'This lease is about parking only.'}},
    }

    def test_needle_defers_to_legacy_when_disabled(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_NEEDLE_V2_ENABLED', False, raising=False)
        stub = _stub_aki(route='NEEDLE', docs=self.CONTENTS)
        with patch.dict(sys.modules, {'agent_knowledge_integration': stub}):
            assert sweep.knowledge_search_v2('what is X', 1, documents=self.DOCS) is None

    def test_empty_scope_defers(self):
        assert sweep.knowledge_search_v2('q', 1, documents=[]) is None

    def test_routes_on_user_question_not_tool_paraphrase(self, monkeypatch):
        # Tool query looks per-document; the USER question is portfolio-shaped.
        monkeypatch.setattr(cfg, 'DOC_SWEEP_COST_CONFIRM_USD', 5.0, raising=False)
        seen = {}
        stub = _stub_aki(docs=self.CONTENTS,
                         llm_response='{"answer":"Landlord","evidence_quote":"q","page":1,"confidence":95,"not_found":false}')
        stub.route_knowledge_query = lambda q, n, c: seen.setdefault('q', q) or 'FANOUT'
        with patch.dict(sys.modules, {'agent_knowledge_integration': stub}):
            out = sweep.knowledge_search_v2(
                'DCT13_S005_CentralPlaza HVAC responsibility', 1, documents=self.DOCS,
                latest_user_input='For each lease, who handles HVAC maintenance?')
        assert seen['q'] == 'For each lease, who handles HVAC maintenance?'
        assert 'COVERAGE LEDGER' in out

    def test_sweep_cache_serves_repeat_calls(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_SWEEP_COST_CONFIRM_USD', 5.0, raising=False)
        calls = {'n': 0}

        def counting_llm(prompt, system, max_tokens, temp):
            calls['n'] += 1
            return '{"answer":"Landlord","evidence_quote":"q","page":1,"confidence":95,"not_found":false}'

        stub = _stub_aki(docs=self.CONTENTS)
        stub._haiku_call_with_fallback = counting_llm
        with patch.dict(sys.modules, {'agent_knowledge_integration': stub}):
            first = sweep.knowledge_search_v2('q1', 7, documents=self.DOCS,
                                              latest_user_input='who handles HVAC in each lease?')
            after_first = calls['n']
            second = sweep.knowledge_search_v2('q2 different paraphrase', 7, documents=self.DOCS,
                                               latest_user_input='who handles HVAC in each lease?')
        assert after_first > 0
        assert calls['n'] == after_first  # no new LLM calls — served from cache
        assert first == second

    def test_chaos_flag_raises(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_SEARCH_V2_FORCE_ERROR', True, raising=False)
        with pytest.raises(RuntimeError):
            sweep.knowledge_search_v2('q', 1, documents=self.DOCS)

    def test_sweep_reads_everything_and_ledgers(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_SWEEP_COST_CONFIRM_USD', 5.0, raising=False)
        resp = '{"answer":"Landlord","evidence_quote":"maintain all HVAC","page":1,"confidence":95,"not_found":false}'
        stub = _stub_aki(docs=self.CONTENTS, llm_response=resp)
        with patch.dict(sys.modules, {'agent_knowledge_integration': stub}):
            out = sweep.knowledge_search_v2('who maintains HVAC in each lease', 1, documents=self.DOCS)
        assert 'COVERAGE LEDGER' in out
        assert '2 document(s) in scope' in out
        assert '2 read in full' in out
        assert 'Lease A.pdf' in out and 'Landlord' in out

    def test_not_found_documents_are_listed_not_dropped(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_SWEEP_COST_CONFIRM_USD', 5.0, raising=False)
        resp = '{"answer":"","evidence_quote":"","page":null,"confidence":90,"not_found":true}'
        stub = _stub_aki(docs=self.CONTENTS, llm_response=resp)
        with patch.dict(sys.modules, {'agent_knowledge_integration': stub}):
            out = sweep.knowledge_search_v2('who maintains HVAC', 1, documents=self.DOCS)
        assert 'DO NOT ADDRESS THE QUESTION' in out
        assert 'Lease B.pdf' in out
        assert '2 read in full' in out

    def test_cost_confirmation_gate(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_SWEEP_COST_CONFIRM_USD', 0.000001, raising=False)
        stub = _stub_aki(docs=self.CONTENTS, llm_response='{}')
        with patch.dict(sys.modules, {'agent_knowledge_integration': stub}):
            out = sweep.knowledge_search_v2('who maintains HVAC', 1, documents=self.DOCS)
        assert 'COST CONFIRMATION REQUIRED' in out
        assert 'Nothing was run' in out


def _stub_aki_needle(contents, vector_hits=None, llm_response=None):
    stub = types.ModuleType('agent_knowledge_integration')
    stub._load_agent_knowledge_contents = lambda ids, documents: contents
    stub.search_knowledge_vectors = lambda q, a, user_id=None, top_k=10: (vector_hits or [])
    stub._haiku_call_with_fallback = lambda prompt, system, max_tokens, temp: llm_response
    return stub


@pytest.mark.unit
class TestBM25:
    def test_exact_term_page_ranks_first(self):
        pages = [['alpha', 'beta', 'gamma'], ['deposit', 'security', 'deposit'], ['misc', 'words']]
        bm = needle._BM25(pages)
        scores = bm.scores(['security', 'deposit'])
        assert scores.index(max(scores)) == 1

    def test_no_match_scores_zero(self):
        bm = needle._BM25([['alpha'], ['beta']])
        assert all(s == 0 for s in bm.scores(['zzz']))


@pytest.mark.unit
class TestRRF:
    def test_agreement_beats_single_channel(self):
        pages = [dict(doc_id='d1', filename='a', page_number='1', text='x'),
                 dict(doc_id='d2', filename='b', page_number='1', text='y'),
                 dict(doc_id='d3', filename='c', page_number='1', text='z')]
        # lexical prefers 0 then 1; dense prefers page (d2,1)=idx1 then (d3,1)=idx2
        fused = needle._rrf_fuse(pages, [0, 1], [('d2', '1'), ('d3', '1')])
        assert fused[0] == 1  # both channels voted for idx 1


@pytest.mark.unit
class TestNeedleEngine:
    CONTENTS = {
        'd1': {'filename': 'Lease A.pdf', 'pages': {1: 'Security Deposit: $68,000 (two months).',
                                                    2: 'Boilerplate assignment text.'}},
        'd2': {'filename': 'Lease B.pdf', 'pages': {1: 'HVAC handled by landlord entirely.'}},
    }
    DOCS = [{'document_id': 'd1'}, {'document_id': 'd2'}]

    def test_hybrid_returns_citation_block(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_NEEDLE_RERANK', False, raising=False)
        hits = [{'metadata': {'document_id': 'd1', 'page_number': 1}}]
        stub = _stub_aki_needle(self.CONTENTS, vector_hits=hits)
        with patch.dict(sys.modules, {'agent_knowledge_integration': stub}):
            out = needle.knowledge_needle_v2('what is the security deposit', 1,
                                             user_id='u', documents=self.DOCS)
        assert out is not None
        assert 'SOURCE [Lease A.pdf p.1]' in out
        assert '$68,000' in out
        assert 'RETRIEVAL:' in out and 'Cite each fact' in out
        assert out.index('Lease A.pdf p.1') < out.index('RETRIEVAL:')

    def test_dense_failure_falls_back_to_lexical_only(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_NEEDLE_RERANK', False, raising=False)
        stub = _stub_aki_needle(self.CONTENTS)
        def boom(*a, **k):
            raise RuntimeError('vector api down')
        stub.search_knowledge_vectors = boom
        with patch.dict(sys.modules, {'agent_knowledge_integration': stub}):
            out = needle.knowledge_needle_v2('security deposit amount', 1,
                                             user_id='u', documents=self.DOCS)
        assert out is not None and 'Lease A.pdf' in out

    def test_rerank_failure_keeps_rrf_order(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_NEEDLE_RERANK', True, raising=False)
        stub = _stub_aki_needle(self.CONTENTS, llm_response='not json at all')
        with patch.dict(sys.modules, {'agent_knowledge_integration': stub}):
            out = needle.knowledge_needle_v2('security deposit', 1, user_id='u',
                                             documents=self.DOCS)
        assert out is not None and 'SOURCE [' in out

    def test_no_match_defers(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_NEEDLE_RERANK', False, raising=False)
        stub = _stub_aki_needle(self.CONTENTS)
        with patch.dict(sys.modules, {'agent_knowledge_integration': stub}):
            out = needle.knowledge_needle_v2('zzz qqq nonexistent', 1, user_id='u',
                                             documents=self.DOCS)
        assert out is None

    def test_empty_contents_defers(self):
        stub = _stub_aki_needle({})
        with patch.dict(sys.modules, {'agent_knowledge_integration': stub}):
            assert needle.knowledge_needle_v2('q', 1, documents=self.DOCS) is None

    def test_sweep_branch_kill_switch_defers_needles(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_NEEDLE_V2_ENABLED', False, raising=False)
        contents = TestNeedleEngine.CONTENTS
        stub = _stub_aki_needle(contents)
        stub.route_knowledge_query = lambda q, n, c: 'NEEDLE'
        with patch.dict(sys.modules, {'agent_knowledge_integration': stub}):
            assert sweep.knowledge_search_v2('security deposit', 1, user_id='u',
                                             documents=self.DOCS) is None
