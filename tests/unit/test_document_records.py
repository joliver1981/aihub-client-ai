"""Tests for RECORD extraction — a document's repeating content as rows.

A document has two kinds of extractable content:
  FIELDS  — one value per document (title, tenant, effective date)
  RECORDS — many of a thing, each with attributes that belong together
            (a manual's requirements, an invoice's line items)

Records were previously dropped on the floor: a 108-page vendor guide's ~112
requirements got smeared across a flat field namespace as
``fob_points[0].rates.OPO.rate_20ft`` and then deliberately discarded by the
consolidation rule "DROP anything with an array index".

The invariants these lock down:
  * rows ACCUMULATE across page groups (fields use first-value-wins; records must not)
  * a document type with no repeating unit declares no records and is unaffected
  * output truncation is DETECTED, because a short row count that looks complete is
    the failure mode most likely to produce a confidently wrong answer
"""
import os
import sys
import types
from unittest.mock import patch

import pytest

for _name, _attrs in (('anthropic', ('Anthropic',)),
                      ('PyPDF2', ('PdfReader', 'PdfWriter'))):
    if _name not in sys.modules:
        try:
            __import__(_name)
        except ImportError:
            _stub = types.ModuleType(_name)

            class _Stub:
                def __init__(self, *a, **k):
                    pass

            for _attr in _attrs:
                setattr(_stub, _attr, _Stub)
            sys.modules[_name] = _stub

from LLMDocumentEngine import LLMDocumentProcessor  # noqa: E402


GUIDE_SCHEMA = {
    'document_type': 'vendor_guide',
    'fields': {'document_metadata.title': {'description': 'Title'}},
    'records': {
        'requirements': {
            'grain': 'one row per stated obligation the vendor must meet',
            'expected_rows': 112,
            'columns': {
                'topic': 'Area the requirement belongs to',
                'requirement': 'Short name',
                'value': 'What is required',
                'source_pages': 'Page(s)',
                'excerpt': 'Verbatim sentence',
            },
            'vocabulary': {'topic': ['Compliance', 'Labeling', 'Packaging', 'Other']},
        }
    },
}

LEASE_SCHEMA = {
    'document_type': 'lease_agreement',
    'fields': {'parties.lessee.name': {'description': 'Tenant'}},
}


def _pages(n, chars=800, start=1):
    return [{'page_number': i, 'text': 'x' * chars} for i in range(start, start + n)]


def _reply(rows, stop_reason='end_turn'):
    import json as _json
    return {'content': [{'type': 'text', 'text': _json.dumps({'rows': rows})}],
            'stop_reason': stop_reason}


@pytest.fixture
def engine():
    with patch.object(LLMDocumentProcessor, '_load_schemas', return_value={}):
        e = LLMDocumentProcessor(sql_connection_string=None)
    e._anthropic_config = {'use_direct_api': False}
    e.anthropic_proxy_client = None
    return e


@pytest.mark.unit
class TestRecordSpecValidation:
    def test_valid_spec_is_accepted_and_provenance_is_forced(self, engine):
        out = engine._clean_record_spec({'requirements': {
            'grain': 'one row per obligation',
            'expected_rows': 112,
            'columns': {'topic': 'area', 'requirement': 'name'},
        }})
        cols = out['requirements']['columns']
        assert 'source_pages' in cols and 'excerpt' in cols, \
            "a row nobody can trace to a page is not auditable"

    def test_spec_without_columns_is_rejected(self, engine):
        assert engine._clean_record_spec({'requirements': {'grain': 'x'}}) == {}

    def test_empty_or_malformed_degrades_to_no_records(self, engine):
        for bad in (None, {}, {'x': 'not a dict'}, [], 'nonsense'):
            assert engine._clean_record_spec(bad) == {}

    def test_at_most_one_record_set(self, engine):
        out = engine._clean_record_spec({
            'requirements': {'columns': {'a': 'x'}},
            'contacts': {'columns': {'b': 'y'}},
        })
        assert len(out) == 1

    def test_vocabulary_is_kept_only_for_real_columns(self, engine):
        out = engine._clean_record_spec({'requirements': {
            'columns': {'topic': 'area'},
            'vocabulary': {'topic': ['Compliance', 'Labeling'],
                           'ghost': ['nope']},
        }})
        vocab = out['requirements']['vocabulary']
        assert vocab == {'topic': ['Compliance', 'Labeling']}

    def test_set_name_is_sanitised(self, engine):
        out = engine._clean_record_spec({'Line Items!': {'columns': {'a': 'x'}}})
        assert list(out) == ['line_items']


@pytest.mark.unit
class TestRecordExtraction:
    def test_rows_accumulate_across_page_groups(self, engine):
        """The core difference from fields. A manual states DIFFERENT requirements on
        different pages; first-value-wins would throw away all but the first group."""
        pages = _pages(40, chars=3000)
        replies = [
            _reply([{'topic': 'Labeling', 'requirement': 'carton marking'}]),
            _reply([{'topic': 'Packaging', 'requirement': 'inner pack'},
                    {'topic': 'Compliance', 'requirement': 'EDI 856'}]),
        ]
        with patch.object(engine, '_group_pages_for_extraction', return_value=[
                {'text': '[Page 1] a', 'page_numbers': list(range(1, 21))},
                {'text': '[Page 21] b', 'page_numbers': list(range(21, 41))}]), \
             patch('LLMDocumentEngine.AnthropicProxyClient') as mock_client:
            mock_client.return_value.messages_create.side_effect = replies
            out = engine._extract_records(pages, 'vendor_guide', GUIDE_SCHEMA)

        rows = out['requirements']['rows']
        assert len(rows) == 3, "rows from every group must be kept"
        assert {r['requirement'] for r in rows} == {
            'carton marking', 'inner pack', 'EDI 856'}

    def test_a_type_with_no_record_set_extracts_nothing(self, engine):
        assert engine._extract_records(_pages(5), 'lease_agreement', LEASE_SCHEMA) == {}

    def test_columns_not_in_the_spec_are_dropped(self, engine):
        with patch.object(engine, '_group_pages_for_extraction', return_value=[
                {'text': '[Page 1] a', 'page_numbers': [1]}]), \
             patch('LLMDocumentEngine.AnthropicProxyClient') as mock_client:
            mock_client.return_value.messages_create.return_value = _reply(
                [{'topic': 'Labeling', 'requirement': 'x', 'invented': 'nope'}])
            out = engine._extract_records(_pages(1), 'vendor_guide', GUIDE_SCHEMA)
        assert 'invented' not in out['requirements']['rows'][0]

    def test_empty_rows_are_discarded(self, engine):
        with patch.object(engine, '_group_pages_for_extraction', return_value=[
                {'text': '[Page 1] a', 'page_numbers': [1]}]), \
             patch('LLMDocumentEngine.AnthropicProxyClient') as mock_client:
            mock_client.return_value.messages_create.return_value = _reply(
                [{'topic': None, 'requirement': ''}, {'topic': 'Labeling'}])
            out = engine._extract_records(_pages(1), 'vendor_guide', GUIDE_SCHEMA)
        assert len(out['requirements']['rows']) == 1

    def test_the_vocabulary_reaches_the_prompt(self, engine):
        """Without a controlled vocabulary, one guide says topic='Shipping' and another
        says 'Logistics', and counting across them silently under-reports."""
        with patch.object(engine, '_group_pages_for_extraction', return_value=[
                {'text': '[Page 1] a', 'page_numbers': [1]}]), \
             patch('LLMDocumentEngine.AnthropicProxyClient') as mock_client:
            mock_client.return_value.messages_create.return_value = _reply([])
            engine._extract_records(_pages(1), 'vendor_guide', GUIDE_SCHEMA)
            sent = mock_client.return_value.messages_create.call_args
        text = sent.kwargs['messages'][0]['content'][0]['text']
        assert 'MUST be exactly one of: Compliance, Labeling, Packaging, Other' in text


@pytest.mark.unit
class TestTruncationIsDetected:
    """Output truncation is the silent killer: every page was READ, so page coverage
    reports 100%, but the rows are cut off and the count looks complete."""

    def test_max_tokens_stop_marks_the_run_incomplete(self, engine):
        with patch.object(engine, '_group_pages_for_extraction', return_value=[
                {'text': '[Page 1] a', 'page_numbers': [1]}]), \
             patch('LLMDocumentEngine.AnthropicProxyClient') as mock_client:
            mock_client.return_value.messages_create.return_value = _reply(
                [{'topic': 'Labeling', 'requirement': 'x'}], stop_reason='max_tokens')
            out = engine._extract_records(_pages(1), 'vendor_guide', GUIDE_SCHEMA)
        assert out['requirements']['truncated_groups'] == 1

    def test_a_failed_group_is_counted_not_silently_skipped(self, engine):
        with patch.object(engine, '_group_pages_for_extraction', return_value=[
                {'text': '[Page 1] a', 'page_numbers': [1]},
                {'text': '[Page 2] b', 'page_numbers': [2]}]), \
             patch('LLMDocumentEngine.AnthropicProxyClient') as mock_client:
            mock_client.return_value.messages_create.side_effect = [
                RuntimeError('proxy down'),
                _reply([{'topic': 'Labeling', 'requirement': 'x'}]),
            ]
            out = engine._extract_records(_pages(2), 'vendor_guide', GUIDE_SCHEMA)
        assert out['requirements']['truncated_groups'] == 1
        assert len(out['requirements']['rows']) == 1

    def test_unparseable_json_is_counted(self, engine):
        with patch.object(engine, '_group_pages_for_extraction', return_value=[
                {'text': '[Page 1] a', 'page_numbers': [1]}]), \
             patch('LLMDocumentEngine.AnthropicProxyClient') as mock_client:
            mock_client.return_value.messages_create.return_value = {
                'content': [{'type': 'text', 'text': 'not json at all'}],
                'stop_reason': 'end_turn'}
            out = engine._extract_records(_pages(1), 'vendor_guide', GUIDE_SCHEMA)
        assert out['requirements']['truncated_groups'] == 1
        assert out['requirements']['rows'] == []


@pytest.mark.unit
class TestSchemaShape:
    def test_records_are_written_into_the_schema(self, engine):
        schema = engine._build_schema_dict('vendor_guide', {
            'fields': {'a': 'x'},
            'records': {'requirements': {'grain': 'one row per obligation',
                                         'columns': {'topic': 'area'}}},
        })
        assert 'records' in schema
        assert schema['records']['requirements']['grain'] == 'one row per obligation'

    def test_a_lease_schema_is_unchanged_by_this_feature(self, engine):
        """No records key at all — a lease's schema looks exactly as it did before."""
        schema = engine._build_schema_dict('lease_agreement',
                                           {'fields': {'tenant': 'x'}, 'records': {}})
        assert 'records' not in schema
        assert schema == {'document_type': 'lease_agreement',
                          'fields': {'tenant': {'description': 'x'}}}


@pytest.mark.unit
class TestTruncatedRowsAreSalvaged:
    """An output-cap stop cuts the JSON mid-row; the rows before the cut are complete
    and must be recovered, not discarded (measured: 108 pages in one request -> 0 rows)."""

    def test_complete_rows_before_the_cut_are_recovered(self, engine):
        cut = ('{"rows": [{"topic": "Labeling", "requirement": "carton marking"}, '
               '{"topic": "Packaging", "requirement": "inner pack"}, '
               '{"topic": "Compliance", "requirement": "EDI 856", "val')
        rows = engine._salvage_rows(cut)
        assert len(rows) == 2
        assert rows[1]['requirement'] == 'inner pack'

    def test_no_rows_key_salvages_nothing(self, engine):
        assert engine._salvage_rows('{"data": [1,2,3]') == []

    def test_records_use_a_smaller_input_budget_than_fields(self, engine):
        """Field output is fixed-size; record output grows with input. The group size
        for records must come from DOC_RECORDS_INPUT_TOKENS, not the field budget."""
        import config as cfg
        captured = {}
        orig = engine._group_pages_for_extraction

        def spy(pages, max_chars):
            captured['max_chars'] = max_chars
            return orig(pages, max_chars)

        with patch.object(engine, '_group_pages_for_extraction', side_effect=spy), \
             patch('LLMDocumentEngine.AnthropicProxyClient') as mock_client:
            mock_client.return_value.messages_create.return_value = _reply([])
            engine._extract_records(_pages(3), 'vendor_guide', GUIDE_SCHEMA)
        expected = int(getattr(cfg, 'DOC_RECORDS_INPUT_TOKENS', 12000)
                       * float(getattr(cfg, 'DOC_CHARS_PER_TOKEN', 4) or 4))
        assert captured['max_chars'] == max(10000, expected)


@pytest.mark.unit
class TestPageRefNormalization:
    """The model cites '[Page N]' markers back as 'Page 6'; the reference column
    holds bare numbers so queries and citations can join on it."""

    @pytest.mark.parametrize('raw,expected', [
        ('Page 6', '6'),
        ('[Page 8], [Page 9]', '8,9'),
        ('p. 6-7', '6,7'),
        ('6', '6'),
        ('8,9', '8,9'),
        ('Pages 12 and 14', '12,14'),
        ('', ''),
        (None, ''),
        ('Page 6, Page 6', '6'),          # dedup
    ])
    def test_normalization(self, engine, raw, expected):
        assert engine._normalize_page_ref(raw) == expected

    def test_rows_are_normalized_during_extraction(self, engine):
        with patch.object(engine, '_group_pages_for_extraction', return_value=[
                {'text': '[Page 1] a', 'page_numbers': [1]}]), \
             patch('LLMDocumentEngine.AnthropicProxyClient') as mock_client:
            mock_client.return_value.messages_create.return_value = _reply(
                [{'topic': 'Labeling', 'requirement': 'x',
                  'source_pages': 'Page 6'}])
            out = engine._extract_records(_pages(1), 'vendor_guide', GUIDE_SCHEMA)
        assert out['requirements']['rows'][0]['source_pages'] == '6'
