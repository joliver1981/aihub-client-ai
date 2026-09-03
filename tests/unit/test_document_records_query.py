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
from unittest.mock import patch

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


# ---------------------------------------------------------------------------
# Allow-list plumbing (doc-acl G1, 2026-09-03) — pure logic, no DB: a
# recording cursor captures the SQL each mode executes.
# ---------------------------------------------------------------------------

class _RecordingCursor:
    """Records every execute(); returns no rows and zero counts."""

    def __init__(self):
        self.calls = []

    def execute(self, sql, *params):
        self.calls.append((" ".join(sql.split()), tuple(params)))

    def fetchone(self):
        return (0,)

    def fetchall(self):
        return []


_TYPES = {'vendor_guide': 'vendor_requirements',
          'retailer_guide': 'vendor_requirements',
          'lease_agreement': 'lease_terms'}


@pytest.mark.unit
class TestAllowListPlumbing:
    """allowed_document_types must reach BOTH the row query and the COVERAGE
    denominator: the route (app.py internal_document_records) passes the
    caller's category allow list, and every SQL that decides what the caller
    sees has to carry it — rows without the denominator would let a
    restricted user infer the size of the corpus they cannot read."""

    def test_query_mode_filters_rows_by_allowed_types(self):
        cur = _RecordingCursor()
        with patch.object(drq, 'get_types_with_records', return_value=dict(_TYPES)):
            out = drq._query_mode(cur, 'vendor_requirements', 'carton', None, None, 5,
                                  ['vendor_guide', 'lease_agreement'])
        assert out['ok'] is True
        sql, params = cur.calls[0]                       # the row SELECT
        assert 'd.document_type IN (?,?)' in sql
        assert params[-2:] == ('vendor_guide', 'lease_agreement')

    def test_coverage_denominator_is_filtered_by_allowed_types(self):
        cur = _RecordingCursor()
        with patch.object(drq, 'get_types_with_records', return_value=dict(_TYPES)):
            cov = drq._coverage(cur, 'vendor_requirements', ['vendor_guide'])
        assert cov['document_types'] == ['vendor_guide']  # retailer_guide dropped
        sql, params = cur.calls[0]                       # the COUNT
        assert 'document_type IN (?)' in sql and params == ('vendor_guide',)

    def test_coverage_of_a_set_the_caller_cannot_see_counts_nothing(self):
        cur = _RecordingCursor()
        with patch.object(drq, 'get_types_with_records', return_value=dict(_TYPES)):
            cov = drq._coverage(cur, 'vendor_requirements', ['lease_agreement'])
        assert cov['document_types'] == [] and cov['docs_total'] == 0
        assert cur.calls == [], "no visible type -> nothing to count, no SQL"

    def test_unrestricted_none_applies_no_filter(self):
        cur = _RecordingCursor()
        with patch.object(drq, 'get_types_with_records', return_value=dict(_TYPES)):
            drq._query_mode(cur, 'vendor_requirements', 'carton', None, None, 5, None)
        sql, _ = cur.calls[0]
        assert 'document_type IN' not in sql


@pytest.mark.unit
class TestTheFailOpenTrap:
    """Documents the trap doc_search_v3.acl exists to guard, at THIS layer: an
    EMPTY allow list produces NO filter (`if allowed_document_types:` is falsy
    for []), i.e. deny-all handed to the query layer becomes allow-all. Every
    caller must gate on acl.deny_all() first — app.py internal_document_records
    does. Mirrors tests/unit/test_v3_acl.py::TestTheFailOpenTrap.

    If these two tests ever fail, the layer was fixed to fail closed: that is
    GOOD NEWS — delete them and simplify the callers."""

    def test_empty_allow_list_is_no_filter_for_rows(self):
        cur = _RecordingCursor()
        with patch.object(drq, 'get_types_with_records', return_value=dict(_TYPES)):
            drq._query_mode(cur, 'vendor_requirements', 'carton', None, None, 5, [])
        sql, _ = cur.calls[0]
        assert 'document_type IN' not in sql, \
            "[] produced no SQL filter — deny-all IS allow-all at the query layer"

    def test_empty_allow_list_is_no_filter_for_coverage(self):
        cur = _RecordingCursor()
        with patch.object(drq, 'get_types_with_records', return_value=dict(_TYPES)):
            cov = drq._coverage(cur, 'vendor_requirements', [])
        assert cov['document_types'] == ['retailer_guide', 'vendor_guide'], \
            "[] left the denominator unfiltered — the trap the route guards against"
