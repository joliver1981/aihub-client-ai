"""Tests for doc_search_v3.acl — the category-based document allow list.

THE FIRST TEST IS THE POINT: the legacy engine's _build_doc_type_filter treats an
empty allow list as NO FILTER, so deny-all handed to it silently becomes
allow-all. Every caller must gate on deny_all() before the engine. If the first
two tests ever fail together, a user with zero grants sees every document.
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

from doc_search_v3 import acl  # noqa: E402


def _db_available():
    try:
        conn, _ = acl._connect()
        conn.close()
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_available(), reason="live DB unavailable")


@pytest.mark.unit
class TestTheFailOpenTrap:
    def test_legacy_filter_treats_empty_list_as_no_filter(self):
        """Documents the trap this module guards. If the legacy engine ever fixes
        this, this test failing is GOOD NEWS — delete it and simplify callers."""
        from DocUtils import _build_doc_type_filter
        with_and, bare = _build_doc_type_filter(None, [])
        assert with_and == '' and bare == '', \
            "[] produced no SQL filter — deny-all IS allow-all at the engine"

    def test_deny_all_catches_what_the_engine_would_miss(self):
        assert acl.deny_all([]) is True
        assert acl.deny_all(None) is False          # unrestricted
        assert acl.deny_all(['lease_agreement']) is False


@pytest.mark.unit
class TestContract:
    def test_admin_is_unrestricted_without_touching_the_db(self):
        with patch.object(acl, '_connect', side_effect=AssertionError('must not connect')):
            assert acl.accessible_document_types(12, user_role=3) is None

    def test_no_identity_defaults_to_todays_posture(self, monkeypatch):
        monkeypatch.delenv('DOC_V3_REQUIRE_IDENTITY', raising=False)
        with patch.object(acl, '_connect', side_effect=AssertionError('must not connect')):
            assert acl.accessible_document_types(None) is None
            assert acl.accessible_document_types('') is None
            assert acl.accessible_document_types(0) is None

    def test_no_identity_fails_closed_when_enforcement_is_on(self, monkeypatch):
        monkeypatch.setenv('DOC_V3_REQUIRE_IDENTITY', 'true')
        assert acl.accessible_document_types(None) == []

    def test_db_failure_is_deny_all_never_unrestricted(self):
        with patch.object(acl, '_connect', side_effect=RuntimeError('db down')):
            out = acl.accessible_document_types(12, user_role=1)
        assert out == [], "an error must never widen access"

    def test_query_failure_is_deny_all(self):
        class BadCur:
            def execute(self, *a):
                raise RuntimeError('boom')

        class Conn:
            def close(self):
                pass

        with patch.object(acl, '_connect', return_value=(Conn(), BadCur())):
            assert acl.accessible_document_types(12, user_role=1) == []


@pytest.mark.unit
@needs_db
class TestLiveGrants:
    def test_seeded_user_resolves_types(self):
        """Post-seed, james (user 12, in groups) resolves the full type list —
        the migration's no-op-by-construction promise."""
        out = acl.accessible_document_types(12, user_role=2)
        assert isinstance(out, list) and len(out) >= 18
        assert 'lease_agreement' in out

    def test_user_in_no_group_fails_closed(self):
        conn, cur = acl._connect()
        cur.execute("""SELECT TOP 1 id FROM [dbo].[User]
                       WHERE id NOT IN (SELECT user_id FROM UserGroups)""")
        row = cur.fetchone()
        conn.close()
        if not row:
            pytest.skip("every user is in a group on this box")
        out = acl.accessible_document_types(row[0], user_role=1)
        assert out == [], "no groups -> no grants -> deny-all"


@pytest.mark.unit
class TestFramePredicate:
    """_frame_predicate must always yield a usable predicate — fail-open to
    the raw question, never raise into the enumerate run."""

    def test_good_framing_used(self, monkeypatch):
        from doc_search_v3 import enumerate_engine as ee
        monkeypatch.setattr(ee, '_llm', lambda *a, **k:
                            '{"predicate": "Does this lease assign HVAC to '
                            'the tenant?", "value_guide": "one of: tenant / '
                            'landlord / split"}')
        out = ee._frame_predicate("How many leases make HVAC the tenant's job?")
        assert out['predicate'].startswith('Does this lease')
        assert 'tenant / landlord' in out['value_guide']

    def test_bad_json_falls_back_to_question(self, monkeypatch):
        from doc_search_v3 import enumerate_engine as ee
        monkeypatch.setattr(ee, '_llm', lambda *a, **k: 'not json at all')
        q = "How many leases make HVAC the tenant's job?"
        assert ee._frame_predicate(q) == {'predicate': q, 'value_guide': ''}

    def test_llm_exception_falls_back(self, monkeypatch):
        from doc_search_v3 import enumerate_engine as ee
        def boom(*a, **k):
            raise RuntimeError("llm down")
        monkeypatch.setattr(ee, '_llm', boom)
        q = "How many?"
        assert ee._frame_predicate(q) == {'predicate': q, 'value_guide': ''}

    def test_missing_keys_fall_back(self, monkeypatch):
        from doc_search_v3 import enumerate_engine as ee
        monkeypatch.setattr(ee, '_llm', lambda *a, **k: '{"predicate": ""}')
        q = "How many?"
        assert ee._frame_predicate(q) == {'predicate': q, 'value_guide': ''}
