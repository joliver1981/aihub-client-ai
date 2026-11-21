"""doc_search_v2: the answer is in the manual, so the agent must be handed it.

Three causes of "the passage exists but the agent did not get it", found on real
vendor manuals (2026-11), each with its own tests here:

  - a semantic hit voted for the wrong page (chunks indexed before the page-label fix
    carry the number of a neighbouring page);
  - the rerank saw only the first characters of a page, so a rule in the lower half of
    a page lost to pages that merely START on the topic;
  - each source page was cut at 2,500 characters without saying what was cut.

Plus the cross-document sweep: a comparison across companies must get each document's
OWN value — the map step used to answer not_found for a document that held the value,
because that one document "could not make the comparison".
"""
import re
import sys
import types
from unittest.mock import patch

import pytest

import config as cfg
from doc_search_v2 import needle
from doc_search_v2 import sweep


@pytest.fixture(autouse=True)
def clean_caches():
    sweep._sweep_cache.clear()
    needle._index_cache.clear()
    yield


def _stub_aki(contents, vector_hits=None, llm_response=None):
    stub = types.ModuleType('agent_knowledge_integration')
    stub._load_agent_knowledge_contents = lambda ids, documents: contents
    stub.search_knowledge_vectors = lambda q, a, user_id=None, top_k=10: (vector_hits or [])
    stub._haiku_call_with_fallback = lambda prompt, system, max_tokens, temp: llm_response
    return stub


def _flat(text):
    return re.sub(r'\s+', ' ', text).strip()


INVOICE_PAGE = ('Carton labels.\nMerchandise Invoice Instructions: Domestic vendors will submit all '
                'merchandise invoices via EDI 810 to the retailer.')
RETURNS_PAGE = ('Return Instructions: damages observed at the point of receipt will be communicated '
                'to the vendor upon inspection of the goods.')
INVOICE_CHUNK = ('[Acme Vendor Guide] [Section: Invoices] Merchandise Invoice Instructions: Domestic '
                 'vendors will submit all merchandise invoices via EDI 810 to the retailer.')


@pytest.mark.unit
class TestFocusedPreview:
    def test_short_page_is_shown_whole(self):
        assert needle._focused_preview('Security  Deposit:\n$68,000', ['deposit'], {'deposit': 1.0}) \
            == 'Security Deposit: $68,000'

    def test_preview_shows_the_matching_passage_not_only_the_page_start(self):
        # The page opens with carton markings; the invoice rule is at the bottom.
        page = ('WORLD CARTON MARKINGS ' + 'label pantone department ' * 60
                + 'Merchandise Invoice Instructions: import vendors submit invoices via EDI 810.')
        idf = {'invoices': 2.0, 'import': 1.5, 'edi': 2.0, 'carton': 0.2}
        preview = needle._focused_preview(page, ['import', 'invoices', 'edi'], idf)
        assert preview.startswith('WORLD CARTON MARKINGS')      # still says what the page is
        assert 'EDI 810' in preview                             # and shows why it matches
        assert len(preview) <= needle._PREVIEW_LEAD + needle._PREVIEW_WINDOW + 5

    def test_no_query_term_on_the_page_falls_back_to_the_page_start(self):
        page = 'alpha ' * 400
        assert needle._focused_preview(page, ['zzz'], {'zzz': 3.0}) == \
            _flat(page)[:needle._PREVIEW_LEAD + needle._PREVIEW_WINDOW]

    def test_rerank_is_shown_the_matching_passage(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_NEEDLE_RERANK', True, raising=False)
        page = ('WORLD CARTON MARKINGS ' + 'label pantone department ' * 60
                + 'Import vendors submit merchandise invoices via EDI 810.')
        pages = [dict(doc_id='d1', filename='m.pdf', page_number=12, text=page),
                 dict(doc_id='d1', filename='m.pdf', page_number=13, text='Return instructions.')]
        seen = {}

        def llm(prompt, system, max_tokens, temp):
            seen['prompt'] = prompt
            return '[{"id": 0, "score": 90}, {"id": 1, "score": 10}]'

        idf = {'invoices': 2.0, 'import': 1.5, 'edi': 2.0}
        order = needle._rerank(llm, 'how do import vendors submit invoices edi', pages, [0, 1], idf=idf)
        assert order == [0, 1]
        assert 'EDI 810' in seen['prompt']


@pytest.mark.unit
class TestPageHoldingChunk:
    PAGES = [dict(doc_id='d1', filename='m.pdf', page_number=11, text=INVOICE_PAGE),
             dict(doc_id='d1', filename='m.pdf', page_number=12, text=RETURNS_PAGE)]
    DOC_PAGES = {'11': 0, '12': 1}

    def test_vote_moves_to_the_page_that_holds_the_text(self):
        # Stored with page 12 (the old indexing bug); the text is on page 11.
        assert needle._page_holding_chunk(INVOICE_CHUNK, 12, self.DOC_PAGES, self.PAGES, {}) == '11'

    def test_correct_label_is_kept(self):
        assert needle._page_holding_chunk(INVOICE_CHUNK, 11, self.DOC_PAGES, self.PAGES, {}) == '11'

    def test_text_found_nowhere_keeps_the_label(self):
        chunk = 'A passage the chunking model rewrote so that it matches no page text at all, anywhere.'
        assert needle._page_holding_chunk(chunk, 12, self.DOC_PAGES, self.PAGES, {}) == '12'

    def test_very_short_chunk_keeps_the_label(self):
        assert needle._page_holding_chunk('EDI 810', 12, self.DOC_PAGES, self.PAGES, {}) == '12'


@pytest.mark.unit
class TestNeedleEvidence:
    def test_dense_vote_on_a_neighbouring_page_is_moved(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_NEEDLE_RERANK', False, raising=False)
        contents = {'d1': {'filename': 'm.pdf', 'pages': {
            11: INVOICE_PAGE, 12: RETURNS_PAGE, 13: 'Unrelated page about pallets and stretch wrap.'}}}
        hits = [{'text': INVOICE_CHUNK, 'metadata': {'document_id': 'd1', 'page_number': 12}}]
        stub = _stub_aki(contents, vector_hits=hits)
        with patch.dict(sys.modules, {'agent_knowledge_integration': stub}):
            out = needle.knowledge_needle_v2('zzz', 1, user_id='u', documents=[{'document_id': 'd1'}])
        # The question shares no word with any page, so the only vote is the semantic
        # one — and it must land on page 11, where the text is.
        assert out is not None
        assert 'SOURCE [m.pdf p.11]' in out
        assert 'SOURCE [m.pdf p.12]' not in out

    def test_long_page_is_given_whole_not_cut_at_2500(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_NEEDLE_RERANK', False, raising=False)
        monkeypatch.setattr(cfg, 'KNOWLEDGE_PARENT_PAGE_CHAR_CAP', 12000, raising=False)
        page = 'Testing process intro. ' * 150 + 'FCC Test Reports are valid for 5 years after test date.'
        assert len(page) > 2500
        stub = _stub_aki({'d1': {'filename': 'm.pdf', 'pages': {36: page}}})
        with patch.dict(sys.modules, {'agent_knowledge_integration': stub}):
            out = needle.knowledge_needle_v2('FCC test reports valid', 1, user_id='u',
                                             documents=[{'document_id': 'd1'}])
        assert 'valid for 5 years after test date' in out
        assert 'page continues' not in out and 'IN PART' not in out

    def test_a_page_over_the_configured_limit_says_it_is_shown_in_part(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_NEEDLE_RERANK', False, raising=False)
        monkeypatch.setattr(cfg, 'KNOWLEDGE_PARENT_PAGE_CHAR_CAP', 500, raising=False)
        page = 'deposit clause wording ' * 100
        stub = _stub_aki({'d1': {'filename': 'm.pdf', 'pages': {3: page}}})
        with patch.dict(sys.modules, {'agent_knowledge_integration': stub}):
            out = needle.knowledge_needle_v2('deposit clause', 1, user_id='u',
                                             documents=[{'document_id': 'd1'}])
        assert 'shown IN PART' in out and f'of {len(page.strip()):,} chars' in out
        assert 'get_document_page_by_number' in out

    def test_pages_left_out_for_the_total_limit_are_named(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_NEEDLE_RERANK', False, raising=False)
        monkeypatch.setattr(cfg, 'KNOWLEDGE_PARENT_PAGE_CHAR_CAP', 12000, raising=False)
        monkeypatch.setattr(cfg, 'KNOWLEDGE_PARENT_TOTAL_CHAR_CAP', 1500, raising=False)
        pages = {n: f'deposit clause page {n} ' + 'wording ' * 120 for n in (1, 2, 3)}
        stub = _stub_aki({'d1': {'filename': 'm.pdf', 'pages': pages}})
        with patch.dict(sys.modules, {'agent_knowledge_integration': stub}):
            out = needle.knowledge_needle_v2('deposit clause', 1, user_id='u',
                                             documents=[{'document_id': 'd1'}])
        assert out.count('SOURCE [m.pdf p.') == 1        # the first page always goes in
        assert 'further matching page(s) left out' in out
        assert out.count('m.pdf p.') == 3                # the other two are named, not dropped


@pytest.mark.unit
class TestSweepCrossDocumentQuestion:
    QUESTION = 'Compare the maximum carton weight of Acme, Globex and Initech.'

    def test_question_comes_after_the_document(self):
        seen = {}

        def llm(prompt, system, max_tokens, temp):
            seen['prompt'], seen['system'] = prompt, system
            return ('{"answer":"50 lbs","evidence_quote":"WEIGHT 2 LBS. 50LBS.","page":7,'
                    '"confidence":95,"not_found":false}')

        finding = sweep._map_one_document(llm, self.QUESTION, 'Initech Vendor Guide.pdf',
                                          {7: 'MAXIMUM CARTON SIZE WEIGHT 2 LBS. 50LBS.'})
        assert finding['answer'] == '50 lbs' and finding['not_found'] is False
        prompt = seen['prompt']
        assert prompt.index('--- END DOCUMENT TEXT ---') < prompt.index(self.QUESTION)
        assert 'not_found is false' in prompt
        assert 'SEVERAL documents' in seen['system']

    def test_a_document_silent_on_the_topic_is_still_not_found(self):
        def llm(prompt, system, max_tokens, temp):
            return '{"answer":"","evidence_quote":"","page":null,"confidence":0,"not_found":true}'

        finding = sweep._map_one_document(llm, self.QUESTION, 'Acme Legal Terms.pdf',
                                          {1: 'Governing law and indemnification.'})
        assert finding['not_found'] is True and finding['answer'] == ''
