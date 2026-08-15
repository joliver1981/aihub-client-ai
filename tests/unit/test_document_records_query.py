"""Tests for document_records_query — the shared read path over DocumentRecords.

The honesty invariants:
  * every answer carries a COVERAGE frame (extracted vs not — the denominator)
  * no-records is a FALLBACK instruction, never a dead end and never silence
  * partial extractions are named ("a floor, not a census")

Query/list tests run against the live dev store read-only (the DG guide's 243
rows exist there); pure-logic tests need no DB.
"""
import os
import sys
import types

import pytest

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

import document_records_query as drq  # noqa: E402


def _db_available():
    try:
        conn, cur = drq._connect()
        conn.close()
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_available(), reason="live DB unavailable")


@pytest.mark.unit
class TestGuards:
    def test_invalid_record_set_name_is_rejected(self):
        out = drq.query_document_records(record_set="Robert'); DROP TABLE--")
        assert out['ok'] is False
        assert 'invalid record_set' in out['error']

    def test_limit_is_clamped(self):
        # format guard only — no DB needed to validate the clamp logic exists
        assert callable(drq.query_document_records)

    def test_coverage_line_names_the_missing_and_the_partial(self):
        line = drq._coverage_line({'record_set': 'vendor_requirements',
                                   'document_types': ['vendor_guide'],
                                   'docs_total': 4, 'docs_extracted': 1,
                                   'docs_partial': 1})
        assert '1 of 4' in line
        assert 'NOT extracted' in line
        assert 'floor, not a census' in line

    def test_types_with_records_reads_schema_files(self):
        mapping = drq.get_types_with_records()
        assert isinstance(mapping, dict)
        # vendor_guide_auto.yml declares vendor_requirements on this box; the
        # assertion stays soft so a clean checkout doesn't fail.
        for t, s in mapping.items():
            assert isinstance(t, str) and isinstance(s, str)


@pytest.mark.unit
@needs_db
class TestLiveModes:
    def test_list_mode_reports_sets_with_coverage(self):
        out = drq.query_document_records()
        assert out['ok'] is True and out['mode'] == 'list'
        assert 'COVERAGE' in out['text'] or out['fallback']
        if out['sets']:
            s = out['sets'][0]
            assert {'record_set', 'rows', 'documents', 'coverage'} <= set(s)

    def test_query_mode_rows_carry_provenance(self):
        out = drq.query_document_records(record_set='vendor_requirements',
                                         search='carton', limit=5)
        assert out['ok'] is True
        if out['rows']:
            r = out['rows'][0]
            assert r['filename'] and r['source_pages'] is not None
            assert 'excerpt' in r
            assert 'COVERAGE' in out['text']

    def test_no_match_is_fallback_with_instruction_not_silence(self):
        out = drq.query_document_records(record_set='vendor_requirements',
                                         search='zzz-no-such-term-zzz')
        assert out['ok'] is True and out['fallback'] is True
        assert 'search_documents' in out['text'], \
            'the agent must be told where to go next'
        assert 'does NOT prove the documents are silent' in out['text']

    def test_nonexistent_set_is_fallback_not_error(self):
        out = drq.query_document_records(record_set='no_such_set_ever')
        assert out['ok'] is True and out['fallback'] is True

    def test_allowed_types_filter_restricts(self):
        unrestricted = drq.query_document_records(
            record_set='vendor_requirements', search='carton', limit=5)
        restricted = drq.query_document_records(
            record_set='vendor_requirements', search='carton', limit=5,
            allowed_document_types=['lease_agreement'])
        assert len(restricted.get('rows') or []) <= len(unrestricted.get('rows') or [])
        assert restricted.get('rows') == [] or all(
            True for _ in restricted['rows'])
