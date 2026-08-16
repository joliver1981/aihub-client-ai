"""Tests for schema-driven field extraction (LLMDocumentProcessor._extract_with_schema).

These cover the repair of a silent data-loss bug: the previous implementation
extracted a field only when the schema supplied a regex ``pattern``. No schema in
the wild carries one, so it returned {} for every field — and because
``_save_ai_schema`` writes a pattern-less schema after the first successful AI
extraction and is then preferred over the AI path, each document type extracted
correctly exactly once and silently extracted nothing afterwards.

The invariant these lock down: a schema must never make extraction WORSE than
having no schema at all.
"""
import os
import sys
import types
from unittest.mock import patch

import pytest

# Document ingestion runs under the `aihubant` conda env; the test env (`aihub2.1`)
# has pytest but not `anthropic`/`PyPDF2`. Stub them so this module is importable
# wherever the suite runs — same approach as tests/unit/test_doc_search_v2.py.
for _name, _attrs in (('anthropic', ('Anthropic',)),
                      ('PyPDF2', ('PdfReader', 'PdfWriter'))):
    if _name not in sys.modules:
        try:
            __import__(_name)
        except ImportError:
            _stub = types.ModuleType(_name)

            class _Stub:  # noqa: D401 - inert placeholder
                def __init__(self, *a, **k):
                    pass

            for _attr in _attrs:
                setattr(_stub, _attr, _Stub)
            sys.modules[_name] = _stub

from LLMDocumentEngine import LLMDocumentProcessor  # noqa: E402


LEASE_SCHEMA = {
    'document_type': 'lease_agreement',
    'fields': {
        'execution_date': {'description': 'Auto-detected field: execution_date'},
        'parties.lessee.name': {'description': 'Auto-detected field: parties.lessee.name'},
        'parties.lessor.name': {'description': 'Auto-detected field: parties.lessor.name'},
        'premises.size': {'description': 'Auto-detected field: premises.size'},
    },
}

# Shape of the compliance schemas: a document_type (so it loads) but no `fields`.
CATEGORY_SCHEMA = {
    'document_type': 'retailer_compliance',
    'categories': {'shipping': {'description': 'Freight and delivery'}},
}


@pytest.fixture
def engine():
    """A processor with no SQL connection — we only exercise extraction."""
    with patch.object(LLMDocumentProcessor, '_load_schemas', return_value={}):
        return LLMDocumentProcessor(sql_connection_string=None)


@pytest.mark.unit
class TestDottedFieldAssignment:
    def test_flat_name(self, engine):
        target = {}
        engine._assign_dotted_field(target, 'execution_date', '2021-04-01')
        assert target == {'execution_date': '2021-04-01'}

    def test_nested_name_builds_dicts(self, engine):
        target = {}
        engine._assign_dotted_field(target, 'parties.lessee.name', 'Skyline Stores')
        assert target == {'parties': {'lessee': {'name': 'Skyline Stores'}}}

    def test_siblings_share_parents(self, engine):
        target = {}
        engine._assign_dotted_field(target, 'parties.lessee.name', 'Skyline')
        engine._assign_dotted_field(target, 'parties.lessee.phone', '555-0100')
        engine._assign_dotted_field(target, 'parties.lessor.name', 'Hartford Trust')
        assert target == {
            'parties': {
                'lessee': {'name': 'Skyline', 'phone': '555-0100'},
                'lessor': {'name': 'Hartford Trust'},
            }
        }

    def test_scalar_parent_is_replaced_not_crashed(self, engine):
        """A malformed schema mixing 'a' and 'a.b' must not raise."""
        target = {'parties': 'not-a-dict'}
        engine._assign_dotted_field(target, 'parties.lessee.name', 'ACME')
        assert target['parties']['lessee']['name'] == 'ACME'


@pytest.mark.unit
class TestSchemaDrivenExtraction:
    def test_fields_without_patterns_are_extracted(self, engine):
        """The core repair: description-only fields must reach the LLM."""
        with patch.object(engine, '_extract_fields_with_llm', return_value={
            'execution_date': 'April 1, 2021',
            'parties.lessee.name': 'Skyline Stores',
            'parties.lessor.name': 'Hartford Commercial Real Estate Trust',
            'premises.size': '21,000 square feet',
        }) as mock_llm:
            out = engine._extract_with_schema('lease text', LEASE_SCHEMA, 'lease_agreement')

        assert out['execution_date'] == 'April 1, 2021'
        assert out['parties']['lessee']['name'] == 'Skyline Stores'
        assert out['premises']['size'] == '21,000 square feet'

        # The model is handed the schema's field names as the target list.
        sent_fields = mock_llm.call_args[0][1]
        assert set(sent_fields) == set(LEASE_SCHEMA['fields'])

    def test_nulls_are_not_recorded(self, engine):
        """A field the page does not state must not be written as an empty value."""
        with patch.object(engine, '_extract_fields_with_llm', return_value={
            'execution_date': 'April 1, 2021',
            'parties.lessee.name': None,
            'parties.lessor.name': '',
            'premises.size': None,
        }):
            out = engine._extract_with_schema('lease text', LEASE_SCHEMA, 'lease_agreement')

        assert out == {'execution_date': 'April 1, 2021'}
        assert 'parties' not in out

    def test_missing_key_in_llm_response_is_tolerated(self, engine):
        with patch.object(engine, '_extract_fields_with_llm', return_value={
            'execution_date': 'April 1, 2021',
        }):
            out = engine._extract_with_schema('lease text', LEASE_SCHEMA, 'lease_agreement')
        assert out == {'execution_date': 'April 1, 2021'}


@pytest.mark.unit
class TestNeverWorseThanNoSchema:
    """A schema must never silently suppress extraction. This is the bug class."""

    def test_schema_without_fields_falls_back_to_ai(self, engine):
        with patch.object(engine, '_extract_with_ai',
                          return_value={'requirement': 'GMA pallets'}) as mock_ai:
            out = engine._extract_with_schema('text', CATEGORY_SCHEMA, 'retailer_compliance')

        mock_ai.assert_called_once()
        assert out == {'requirement': 'GMA pallets'}

    def test_empty_fields_dict_falls_back_to_ai(self, engine):
        with patch.object(engine, '_extract_with_ai', return_value={'a': 1}) as mock_ai:
            out = engine._extract_with_schema('text', {'fields': {}}, 'anything')
        mock_ai.assert_called_once()
        assert out == {'a': 1}

    def test_llm_failure_falls_back_to_ai_rather_than_returning_empty(self, engine):
        with patch.object(engine, '_extract_fields_with_llm',
                          side_effect=RuntimeError('proxy down')), \
             patch.object(engine, '_extract_with_ai',
                          return_value={'recovered': True}) as mock_ai:
            out = engine._extract_with_schema('lease text', LEASE_SCHEMA, 'lease_agreement')

        mock_ai.assert_called_once()
        assert out == {'recovered': True}

    def test_regression_pattern_less_schema_is_not_silently_empty(self, engine):
        """The exact shape of every auto-generated schema on disk.

        Before the fix this assertion failed: the call returned {}.
        """
        with patch.object(engine, '_extract_fields_with_llm',
                          return_value={'premises.size': '21,000 square feet'}):
            out = engine._extract_with_schema('lease text', LEASE_SCHEMA, 'lease_agreement')
        assert out, "a pattern-less schema must not produce an empty extraction"


def _pages(n, chars_per_page=1000, start=1):
    return [{'page_number': i, 'text': 'x' * chars_per_page}
            for i in range(start, start + n)]


@pytest.mark.unit
class TestPageCoverageUnderChunking:
    """No page may be dropped, duplicated, reordered or truncated when a large
    document is split for extraction. This is separate from the PDF text-extraction
    batching, which has already completed by the time these chunks are built."""

    def test_small_document_is_one_chunk(self, engine):
        chunks = engine._group_pages_for_extraction(_pages(10), max_chars=300000)
        assert len(chunks) == 1
        assert chunks[0]['page_numbers'] == list(range(1, 11))

    def test_every_page_appears_exactly_once(self, engine):
        pages = _pages(300, chars_per_page=3000)          # a 300-page compliance doc
        chunks = engine._group_pages_for_extraction(pages, max_chars=100000)
        covered = [n for c in chunks for n in c['page_numbers']]
        assert len(chunks) > 1, "this document should require multiple chunks"
        assert covered == list(range(1, 301)), "pages must be complete, ordered, unduplicated"
        assert len(set(covered)) == 300

    def test_no_chunk_exceeds_budget_except_a_single_oversized_page(self, engine):
        pages = _pages(50, chars_per_page=4000)
        budget = 20000
        for chunk in engine._group_pages_for_extraction(pages, max_chars=budget):
            if len(chunk['page_numbers']) > 1:
                assert len(chunk['text']) <= budget

    def test_oversized_single_page_is_kept_whole_not_truncated(self, engine):
        """A page larger than the whole budget must still be extracted intact."""
        pages = [
            {'page_number': 1, 'text': 'a' * 500},
            {'page_number': 2, 'text': 'b' * 250000},   # bigger than the budget
            {'page_number': 3, 'text': 'c' * 500},
        ]
        chunks = engine_chunks = engine._group_pages_for_extraction(pages, max_chars=50000)
        covered = [n for c in engine_chunks for n in c['page_numbers']]
        assert covered == [1, 2, 3]
        big = next(c for c in chunks if 2 in c['page_numbers'])
        assert 'b' * 250000 in big['text'], "oversized page must not be truncated"

    def test_page_markers_are_present_for_attribution(self, engine):
        chunks = engine._group_pages_for_extraction(_pages(3), max_chars=300000)
        text = chunks[0]['text']
        assert '[Page 1]' in text and '[Page 2]' in text and '[Page 3]' in text

    def test_exactly_one_marker_per_page(self, engine):
        """The extractors already prefix '[Page N]'; we must not add a second."""
        import re as _re
        pages = [
            {'page_number': 1, 'text': '[Page 1]\nalready prefixed'},   # fast extractor shape
            {'page_number': 2, 'text': 'no prefix here'},               # blank-page / flag-off shape
        ]
        text = engine._group_pages_for_extraction(pages, max_chars=300000)[0]['text']
        assert len(_re.findall(r'\[Page 1\]', text)) == 1
        assert len(_re.findall(r'\[Page 2\]', text)) == 1
        assert len(_re.findall(r'\[Page \d+\]', text)) == 2, "one marker per page, no duplicates"

    def test_empty_pages_are_still_counted(self, engine):
        pages = [{'page_number': 1, 'text': ''},
                 {'page_number': 2, 'text': 'real content'},
                 {'page_number': 3, 'text': None}]
        chunks = engine._group_pages_for_extraction(pages, max_chars=300000)
        covered = [n for c in chunks for n in c['page_numbers']]
        assert covered == [1, 2, 3], "a blank page is still a page and must be accounted for"


@pytest.mark.unit
class TestDocumentLevelExtraction:
    def test_one_value_per_field_attached_to_its_cited_page(self, engine):
        pages = _pages(6)
        with patch.object(engine, '_extract_fields_with_llm', return_value={
            'parties.lessee.name': {'value': 'Skyline Stores', 'page': 2},
            'premises.size': {'value': '21,000 square feet', 'page': 4},
            'execution_date': {'value': None, 'page': None},
            'parties.lessor.name': {'value': 'Hartford Trust', 'page': 2},
        }):
            by_page, _unlisted, _rep = engine._extract_document_level_fields(pages, 'lease_agreement', LEASE_SCHEMA)

        assert by_page[2]['parties']['lessee']['name'] == 'Skyline Stores'
        assert by_page[2]['parties']['lessor']['name'] == 'Hartford Trust'
        assert by_page[4]['premises']['size'] == '21,000 square feet'
        # A null field is recorded nowhere at all.
        assert not any('execution_date' in v for v in by_page.values())

    def test_first_value_wins_across_chunks(self, engine):
        """The measured failure: different pages reporting different values for one
        document-level fact. Later chunks must not overwrite an earlier answer."""
        pages = _pages(40, chars_per_page=3000)
        responses = [
            {'parties.lessor.name': {'value': 'Atlanta Retail Holdings LLC', 'page': 1}},
            {'parties.lessor.name': {'value': 'LLC', 'page': 30}},
        ]
        with patch.object(engine, '_extract_fields_with_llm', side_effect=responses):
            by_page, _unlisted, _rep = engine._extract_document_level_fields(
                pages, 'lease_agreement', LEASE_SCHEMA)

        names = [v['parties']['lessor']['name'] for v in by_page.values()
                 if 'parties' in v]
        assert names == ['Atlanta Retail Holdings LLC']

    def test_bare_values_without_page_hint_are_accepted(self, engine):
        pages = _pages(4)
        with patch.object(engine, '_extract_fields_with_llm',
                          return_value={'premises.size': '14,000 SF'}):
            by_page, _unlisted, _rep = engine._extract_document_level_fields(
                pages, 'lease_agreement', LEASE_SCHEMA)
        assert by_page[1]['premises']['size'] == '14,000 SF'

    def test_bogus_page_hint_falls_back_to_a_page_in_the_chunk(self, engine):
        pages = _pages(3)
        with patch.object(engine, '_extract_fields_with_llm',
                          return_value={'premises.size': {'value': '9,000 SF', 'page': 99}}):
            by_page, _unlisted, _rep = engine._extract_document_level_fields(
                pages, 'lease_agreement', LEASE_SCHEMA)
        assert set(by_page) <= {1, 2, 3}, "a hallucinated page number must not create a page"

    def test_schema_without_fields_yields_nothing_to_do(self, engine):
        assert engine._extract_document_level_fields(_pages(3), 'x', CATEGORY_SCHEMA) == ({}, {}, None)


@pytest.mark.unit
class TestSchemaGeneratedFromWholeDocument:
    """A schema must describe the DOCUMENT, not whichever page happened to be first.

    Measured on a 108-page vendor guide: the page-1-derived schema captured the title,
    the confidentiality marking and five marketing slogans — and then bounded extraction
    for every later document of that type.
    """

    def test_merge_collects_keys_from_every_page(self, engine):
        shapes = [
            {'document': {'title': 'Vendor Guide'}},                 # cover page
            {'shipping': {'pallet': {'type': 'GMA'}}},               # page 40
            {'shipping': {'pallet': {'max_height_in': 60}},          # page 41
             'chargebacks': {'late_fee': '$500'}},
        ]
        merged = engine._merge_field_shapes(shapes)
        assert merged['document']['title'] == 'Vendor Guide'
        assert merged['shipping']['pallet']['type'] == 'GMA'
        assert merged['shipping']['pallet']['max_height_in'] == 60
        assert merged['chargebacks']['late_fee'] == '$500'

    def test_merge_tolerates_empty_and_non_dict_pages(self, engine):
        merged = engine._merge_field_shapes([{}, None, {'a': 1}, 'nonsense', {'b': {'c': 2}}])
        assert merged == {'a': 1, 'b': {'c': 2}}

    def test_merge_does_not_let_a_later_blank_erase_an_earlier_value(self, engine):
        merged = engine._merge_field_shapes([{'a': 'real'}, {'a': ''}])
        assert merged['a'] == 'real'

    def test_generated_schema_covers_the_whole_document(self, engine, tmp_path):
        engine.schema_dir = str(tmp_path)
        engine.schemas = {}
        merged = engine._merge_field_shapes([
            {'document': {'title': 'Direct Import Vendor Guide'}},
            {'shipping': {'pallet': {'type': 'GMA'}}},
            {'chargebacks': {'late_fee': '$500'}},
        ])
        with patch.object(engine, '_consolidate_schema_fields', return_value={
            'fields': {
                'document.title': 'Title of the guide',
                'shipping.pallet.type': 'Required pallet type',
                'chargebacks.late_fee': 'Late fee',
            },
            'records': {},
        }) as mock_con:
            engine._save_ai_schema('vendor_guide', merged)

        # Consolidation sees every page's paths, not just page one's.
        observed = set(mock_con.call_args[0][1])
        assert {'document.title', 'shipping.pallet.type', 'chargebacks.late_fee'} <= observed

        written = list(tmp_path.glob('vendor_guide_auto.yml'))
        assert written, "a schema should have been written"
        import yaml as _yaml
        saved = _yaml.safe_load(written[0].read_text())
        assert set(saved['fields']) == {'document.title', 'shipping.pallet.type',
                                        'chargebacks.late_fee'}


@pytest.mark.unit
class TestSchemaConsolidation:
    """The raw merge is a page-by-page transcript, not a schema. Writing it verbatim
    produced 1,808 'fields' from one 108-page manual — 313 section headings, 331 array
    positions, five spellings of 'last updated' and an extraction_error key."""

    def test_consolidated_schema_is_written_not_the_raw_paths(self, engine, tmp_path):
        engine.schema_dir = str(tmp_path)
        engine.schemas = {}
        noisy = {f'sections.s{i}.title': f'section {i}' for i in range(200)}
        noisy.update({'retailer': 'Dollar General', 'effective_date': '2025-11-03'})

        with patch.object(engine, '_consolidate_schema_fields', return_value={
            'fields': {'retailer': 'Retailer the guide belongs to',
                       'effective_date': 'Date the guide takes effect'},
            'records': {},
        }):
            engine._save_ai_schema('vendor_guide', noisy)

        import yaml as _yaml
        saved = _yaml.safe_load((tmp_path / 'vendor_guide_auto.yml').read_text())
        assert set(saved['fields']) == {'retailer', 'effective_date'}

    def test_no_schema_is_written_when_consolidation_fails(self, engine, tmp_path):
        """A junk schema would silently bound every later document of this type;
        writing nothing leaves free-form extraction in place, which is honest."""
        engine.schema_dir = str(tmp_path)
        engine.schemas = {}
        with patch.object(engine, '_consolidate_schema_fields',
                          side_effect=RuntimeError('model unavailable')):
            engine._save_ai_schema('vendor_guide', {'a': {'b': 1}})
        assert not list(tmp_path.glob('*.yml')), "no schema may be written on failure"


@pytest.mark.unit
class TestExpensiveWorkIsNotLostOnFailure:
    """Reading a document free-form costs ONE CALL PER PAGE (108 for a 108-page manual).
    Consolidating those paths into a schema is one cheap call. If the cheap step fails
    and we discard the expensive step, every future document of that type pays the
    per-page cost again — permanently. These lock the recovery path."""

    def test_failed_consolidation_parks_the_observed_paths(self, engine, tmp_path):
        engine.schema_dir = str(tmp_path)
        engine.schemas = {}
        with patch.object(engine, '_consolidate_schema_fields',
                          side_effect=RuntimeError('proxy 500')):
            engine._save_ai_schema('vendor_guide', {'shipping': {'pallet': 'GMA'},
                                                    'retailer': 'DG'})
        parked = tmp_path / '_pending' / 'vendor_guide_observed.json'
        assert parked.exists(), "the expensive read's output must be preserved"
        import json as _json
        data = _json.loads(parked.read_text())
        assert set(data['observed_paths']) == {'shipping.pallet', 'retailer'}

    def test_consolidation_is_retried_before_giving_up(self, engine, tmp_path):
        engine.schema_dir = str(tmp_path)
        engine.schemas = {}
        with patch.object(engine, '_consolidate_schema_fields',
                          side_effect=[RuntimeError('transient'),
                                       {'fields': {'retailer': 'r'}, 'records': {}}]) as m:
            engine._save_ai_schema('vendor_guide', {'retailer': 'DG'})
        assert m.call_count == 2
        assert (tmp_path / 'vendor_guide_auto.yml').exists()

    def test_a_later_document_recovers_the_schema_without_re_reading(self, engine, tmp_path):
        engine.schema_dir = str(tmp_path)
        engine.schemas = {}
        with patch.object(engine, '_consolidate_schema_fields',
                          side_effect=RuntimeError('down')):
            engine._save_ai_schema('vendor_guide', {'retailer': 'DG',
                                                    'shipping': {'pallet': 'GMA'}})

        # Next document of this type: one cheap call, no per-page re-read.
        with patch.object(engine, '_consolidate_schema_fields',
                          return_value={'fields': {'retailer': 'Retailer name'},
                                        'records': {}}) as m:
            ok = engine._try_schema_from_pending('vendor_guide')

        assert ok is True
        assert 'vendor_guide' in engine.schemas
        assert set(m.call_args[0][1]) == {'retailer', 'shipping.pallet'}
        assert not (tmp_path / '_pending' / 'vendor_guide_observed.json').exists()

    def test_successful_consolidation_clears_any_parked_paths(self, engine, tmp_path):
        engine.schema_dir = str(tmp_path)
        engine.schemas = {}
        with patch.object(engine, '_consolidate_schema_fields',
                          side_effect=RuntimeError('down')):
            engine._save_ai_schema('vendor_guide', {'retailer': 'DG'})
        assert (tmp_path / '_pending' / 'vendor_guide_observed.json').exists()

        engine.schemas = {}
        with patch.object(engine, '_consolidate_schema_fields',
                          return_value={'fields': {'retailer': 'Retailer name'},
                                        'records': {}}):
            engine._save_ai_schema('vendor_guide', {'retailer': 'DG'})
        assert not (tmp_path / '_pending' / 'vendor_guide_observed.json').exists()

    def test_recovery_is_a_no_op_when_nothing_is_parked(self, engine, tmp_path):
        engine.schema_dir = str(tmp_path)
        engine.schemas = {}
        assert engine._try_schema_from_pending('never_seen') is False

    def test_excluded_pseudo_types_are_never_recovered(self, engine, tmp_path):
        engine.schema_dir = str(tmp_path)
        engine.schemas = {}
        os.makedirs(tmp_path / '_pending', exist_ok=True)
        (tmp_path / '_pending' / 'unknown_document_observed.json').write_text(
            '{"observed_paths": ["a.b"]}')
        assert engine._try_schema_from_pending('unknown_document') is False

    def test_consolidator_output_is_scrubbed_of_array_indices_and_errors(self, engine):
        with patch.object(engine, '_extract_fields_with_llm'):
            pass
        raw = {'fields': {
            'tenant.name': 'Tenant',
            'fob_points[0].rate': 'a table row',
            'extraction_error': 'diagnostic noise',
        }}
        with patch.object(engine, '_anthropic_config', {'use_direct_api': False}), \
             patch('LLMDocumentEngine.AnthropicProxyClient') as mock_client:
            mock_client.return_value.messages_create.return_value = {
                'content': [{'text': __import__('json').dumps(raw)}]}
            engine.anthropic_proxy_client = None
            out = engine._consolidate_schema_fields('lease_agreement', ['tenant.name'])
        assert out['fields'] == {'tenant.name': 'Tenant'}
        assert out['records'] == {}

    def test_consolidation_raises_when_no_fields_survive(self, engine):
        with patch.object(engine, '_anthropic_config', {'use_direct_api': False}), \
             patch('LLMDocumentEngine.AnthropicProxyClient') as mock_client:
            mock_client.return_value.messages_create.return_value = {
                'content': [{'text': '{"fields": {}}'}]}
            engine.anthropic_proxy_client = None
            with pytest.raises(Exception):
                engine._consolidate_schema_fields('x', ['a.b'])


@pytest.mark.unit
class TestResponseTextExtraction:
    """Reasoning models can emit a thinking block first, so content[0] may have no
    'text' key at all. Indexing [0] blindly raised KeyError on a live call."""

    def test_thinking_block_before_text(self, engine):
        resp = {'content': [{'type': 'thinking', 'thinking': 'hmm'},
                            {'type': 'text', 'text': '{"fields":{}}'}]}
        assert engine._response_text(resp) == '{"fields":{}}'

    def test_plain_text_first(self, engine):
        assert engine._response_text({'content': [{'type': 'text', 'text': 'hi'}]}) == 'hi'

    def test_direct_sdk_object_shape(self, engine):
        class Block:
            type = 'text'
            text = 'from sdk'

        class Resp:
            content = [Block()]

        assert engine._response_text(Resp()) == 'from sdk'

    def test_proxy_error_shape_raises_clearly(self, engine):
        with pytest.raises(ValueError, match="LLM call failed"):
            engine._response_text({'error': 'Proxy returned status code 500',
                                   'details': 'upstream'})

    def test_no_text_anywhere_raises(self, engine):
        with pytest.raises(ValueError):
            engine._response_text({'content': [{'type': 'thinking', 'thinking': 'x'}]})


@pytest.mark.unit
class TestPseudoTypesGetNoSchema:
    """'unknown_document' is the absence of a type, not a type."""

    @pytest.mark.parametrize('doc_type', ['unknown', 'unknown_document',
                                          'UNKNOWN_DOCUMENT', '  unknown  '])
    def test_excluded_types(self, engine, doc_type):
        assert engine._schema_excluded(doc_type) is True

    @pytest.mark.parametrize('doc_type', ['lease_agreement', 'invoice', 'vendor_guide'])
    def test_real_types_are_not_excluded(self, engine, doc_type):
        assert engine._schema_excluded(doc_type) is False

    def test_no_schema_is_written_for_an_excluded_type(self, engine, tmp_path):
        engine.schema_dir = str(tmp_path)
        engine.schemas = {}
        engine._save_ai_schema('unknown_document', {'some': {'field': 'value'}})
        assert not list(tmp_path.iterdir()), "no schema file may be written for a pseudo-type"

    def test_a_stale_schema_file_for_an_excluded_type_is_ignored_on_load(self, engine, tmp_path):
        """There is already an unknown_document_auto.yml shape in the wild."""
        import yaml as _yaml
        (tmp_path / 'unknown_document_auto.yml').write_text(_yaml.dump({
            'document_type': 'unknown_document',
            'fields': {'whatever': {'description': 'from some unrelated document'}},
        }))
        (tmp_path / 'invoice_auto.yml').write_text(_yaml.dump({
            'document_type': 'invoice',
            'fields': {'total': {'description': 'invoice total'}},
        }))
        engine.schema_dir = str(tmp_path)
        loaded = engine._load_schemas()
        assert 'invoice' in loaded, "real types still load"
        assert 'unknown_document' not in loaded, "a pseudo-type schema must not take effect"
