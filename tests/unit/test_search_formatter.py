"""Tests for format_search_results_for_ai — the page-as-payload repair.

The principle: vector chunks (512 chars) exist to FIND the right page; the AI should
then be shown the PAGE. The previous formatter preferred metadata['matched_chunk'] over
the page text, which made DOC_INCLUDE_FULL_PAGE_IN_CHUNK_RESULTS a silent no-op — the
flag changed what was fetched but never what the AI saw.

These lock down:
  * the full page wins over the chunk, whichever of the two transport shapes arrives
  * several chunk hits on one page render the page ONCE
  * every cut is announced (per-page cap, global budget) — no silent drops
  * the [Source N: ...] header contract that document_search_wrapper parses survives
"""
import sys
import types
from unittest.mock import patch

import pytest

# DocUtils imports heavy optional deps in some environments; stub what's missing so the
# module imports under the pytest env (same approach as test_doc_search_v2.py).
for _name, _attrs in (('anthropic', ('Anthropic',)),
                      ('PyPDF2', ('PdfReader', 'PdfWriter'))):
    if _name not in sys.modules:
        try:
            __import__(_name)
        except ImportError:
            _stub = types.ModuleType(_name)

            class _S:
                def __init__(self, *a, **k):
                    pass

            for _a in _attrs:
                setattr(_stub, _a, _S)
            sys.modules[_name] = _stub

import DocUtils  # noqa: E402
from DocUtils import format_search_results_for_ai  # noqa: E402
from document_search_wrapper import _passages_from_source_blocks  # noqa: E402

PAGE = ("Section 9. MAINTENANCE AND REPAIRS. Tenant shall, at Tenant's sole cost, "
        "maintain the heating, ventilation and air conditioning (HVAC) systems, "
        "provided that any single repair exceeding $1,500 shall be Landlord's "
        "responsibility. " + "Lorem ipsum dolor sit amet. " * 60)

CHUNK = ("Tenant shall, at Tenant's sole cost, maintain the heating, ventilation "
         "and air conditioning (HVAC) systems")


def _hit(doc='doc-1', page=9, chunk=CHUNK, full=PAGE, rel=0.82, chunk_id=None,
         flag_on=True):
    """One chunk-level search result in either transport shape.

    flag_on=True  → engine already substituted the page as the body (flag live).
    flag_on=False → body is the chunk; the page rides only in metadata.full_text
                    (the at-rest shape from index time — pre-restart services).
    """
    meta = {'document_id': doc, 'page_number': page, 'filename': f'{doc}_lease.pdf',
            'document_type': 'lease_agreement', 'full_text': full}
    if flag_on:
        meta['matched_chunk'] = chunk
        body = full
    else:
        body = chunk
    return {'document_id': chunk_id or f'{doc}_chunk_0', 'relevance_score': rel,
            'text': body, 'metadata': meta}


@pytest.fixture(autouse=True)
def _fast_base_url():
    with patch.object(DocUtils, 'get_base_url', return_value='http://x:5001'):
        yield


@pytest.mark.unit
class TestPageIsThePayload:
    def test_flag_on_shape_shows_the_full_page(self):
        out = format_search_results_for_ai([_hit(flag_on=True)])
        assert "Landlord's responsibility" in out, \
            "the page's substance must reach the AI, not just the matched fragment"
        assert len(out) > len(CHUNK) * 3

    def test_flag_off_shape_STILL_shows_the_full_page(self):
        """metadata.full_text is stored on every chunk at index time, so full pages
        work even against a vector service that predates the flag flip."""
        out = format_search_results_for_ai([_hit(flag_on=False)])
        assert "Landlord's responsibility" in out

    def test_the_regression_that_made_the_flag_a_noop(self):
        """flag ON and OFF shapes must not produce a chunk-only payload."""
        on = format_search_results_for_ai([_hit(flag_on=True)])
        off = format_search_results_for_ai([_hit(flag_on=False)])
        for out in (on, off):
            assert PAGE[:400].split('Lorem')[0].strip()[:80] in out.replace('\n', ' ')

    def test_matched_line_appears_only_when_chunk_absent_from_shown_text(self):
        # Normal case: chunk is verbatim inside the page → no Matched line needed.
        out = format_search_results_for_ai([_hit()])
        assert 'Matched:' not in out
        # Cut case: page truncated so hard the chunk fell outside what is shown.
        tail_chunk = 'zzz unique tail sentence that lives at the very end'
        big = PAGE + 'x' * 30000 + tail_chunk
        with patch.object(DocUtils.cfg, 'DOC_SEARCH_MAX_CHARS_PER_SOURCE', 2000,
                          create=True):
            out = format_search_results_for_ai([_hit(full=big, chunk=tail_chunk)])
        assert 'Matched: "zzz unique tail sentence' in out


@pytest.mark.unit
class TestOnePageRendersOnce:
    def test_three_chunk_hits_one_page(self):
        """Dedup upstream keys on CHUNK id, so same-page hits arrive separately.
        Rendering the same page three times would triple-spend the budget."""
        hits = [_hit(chunk_id=f'doc-1_chunk_{i}', rel=0.8 - i * 0.1,
                     chunk=f'{CHUNK} variant {i}') for i in range(3)]
        out = format_search_results_for_ai(hits)
        assert out.count('[Source ') == 1
        assert '[Source 1: doc-1_lease - Page 9]' in out

    def test_different_pages_render_separately_best_first(self):
        hits = [_hit(page=9, rel=0.6, chunk_id='c1'),
                _hit(page=4, rel=0.9, chunk_id='c2',
                     full='Page four text about the security deposit.')]
        out = format_search_results_for_ai(hits)
        assert out.index('Page 4') < out.index('Page 9'), 'best page first'
        assert out.count('[Source ') == 2


@pytest.mark.unit
class TestEveryCutIsAnnounced:
    def test_oversized_single_page_is_cut_with_a_marker(self):
        big = 'A' * 60000
        out = format_search_results_for_ai([_hit(full=big, chunk='AAAA')])
        assert '[page text truncated at 20,000 chars' in out

    def test_budget_overflow_is_counted_not_silent(self):
        with patch.object(DocUtils.cfg, 'VECTOR_SEARCH_RESULTS_CHAR_LIMIT_FOR_AI', 6000):
            hits = [_hit(doc=f'doc-{i}', chunk_id=f'c{i}', rel=0.9 - i * 0.01,
                         full=f'Page text for document {i}. ' * 100)
                    for i in range(10)]
            out = format_search_results_for_ai(hits)
        assert '[Result set truncated: showing' in out
        assert 'omitted after the 6,000-character limit' in out

    def test_first_page_huge_still_returns_something(self):
        with patch.object(DocUtils.cfg, 'VECTOR_SEARCH_RESULTS_CHAR_LIMIT_FOR_AI', 3000):
            out = format_search_results_for_ai([_hit(full='B' * 50000, chunk='BBB')])
        assert '[Source 1:' in out


@pytest.mark.unit
class TestWrapperContractSurvives:
    """CC and The Agent consume this blob through document_search_wrapper's regex.
    If these fail, both surfaces lose document search."""

    def test_passages_parse_with_full_pages(self):
        hits = [_hit(), _hit(doc='doc-2', page=3, chunk_id='c9', rel=0.7,
                             full='Second lease page about renewal options.')]
        blob = format_search_results_for_ai(hits)
        passages = _passages_from_source_blocks(blob)
        assert len(passages) == 2
        assert passages[0]['document_id'] == 'doc-1'
        assert passages[0]['page'] == '9'
        assert "Landlord's responsibility" in passages[0]['text']
        assert 'Document URL' not in passages[0]['text']

    def test_truncation_notice_does_not_create_a_phantom_passage(self):
        with patch.object(DocUtils.cfg, 'VECTOR_SEARCH_RESULTS_CHAR_LIMIT_FOR_AI', 6000):
            hits = [_hit(doc=f'doc-{i}', chunk_id=f'c{i}',
                         full=f'Page text for document {i}. ' * 100)
                    for i in range(10)]
            blob = format_search_results_for_ai(hits)
        passages = _passages_from_source_blocks(blob)
        assert all(p['filename'].startswith('doc-') for p in passages), \
            'the omission notice must not parse as a source'

    def test_matched_line_stays_inside_its_own_passage(self):
        tail_chunk = 'unique tail sentence for parser test'
        big = PAGE + 'x' * 30000 + tail_chunk
        with patch.object(DocUtils.cfg, 'DOC_SEARCH_MAX_CHARS_PER_SOURCE', 2000,
                          create=True):
            blob = format_search_results_for_ai(
                [_hit(full=big, chunk=tail_chunk),
                 _hit(doc='doc-2', page=2, chunk_id='c2', full='Short page two.')])
        passages = _passages_from_source_blocks(blob)
        assert len(passages) == 2
