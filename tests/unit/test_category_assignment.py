"""Tests for AI-managed category assignment (doc_search_v3.category_assignment).

The safety property: the classifier can never WIDEN access. It files a type into
an existing category (whose grants an admin already set) or creates a NEW
category with ZERO grants (admin-only). Wrong filings are recategorisable; a
failure leaves the type admin-only. And it must never fail an ingest.
"""
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

import config as cfg  # noqa: E402
from doc_search_v3 import category_assignment as ca  # noqa: E402


class FakeCur:
    """Minimal cursor: scripted fetches, records executes."""

    def __init__(self, script):
        self.script = list(script)
        self.executed = []

    def execute(self, sql, *params):
        self.executed.append((' '.join(sql.split()), params))

    def fetchone(self):
        return self.script.pop(0) if self.script else None

    def fetchall(self):
        return self.script.pop(0) if self.script else []


class FakeConn:
    def commit(self):
        pass

    def close(self):
        pass


def _wire(monkeypatch, cur):
    monkeypatch.setattr(ca, '_connect', lambda: (FakeConn(), cur))


@pytest.mark.unit
class TestAssignment:
    def test_already_mapped_is_a_single_cheap_query(self, monkeypatch):
        cur = FakeCur([(1,)])                      # mapping exists
        _wire(monkeypatch, cur)
        with patch.object(ca, '_classify',
                          side_effect=AssertionError('must not classify')):
            assert ca.ensure_category_assignment('lease_agreement') is None
        assert len(cur.executed) == 1

    def test_ai_managed_on_files_as_active(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_CATEGORY_AI_MANAGED', True, raising=False)
        monkeypatch.setattr(cfg, 'DOC_CATEGORY_HOLD_LOW_CONFIDENCE', False,
                            raising=False)
        cur = FakeCur([None,                                     # no mapping
                       [(7, 'leases', 'Leases')],                # categories
                       []])                                      # stewards
        _wire(monkeypatch, cur)
        with patch.object(ca, '_classify', return_value={
                'category_slug': 'leases', 'confidence': 90, 'reason': 'variant'}):
            out = ca.ensure_category_assignment('ground_lease')
        assert out['status'] == 'active' and out['category_id'] == 7
        insert = next(e for e in cur.executed
                      if 'INSERT INTO DocumentTypeCategories' in e[0])
        assert "'ai'" in insert[0] and 'active' in str(insert[1])

    def test_ai_managed_off_files_as_pending(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_CATEGORY_AI_MANAGED', False, raising=False)
        cur = FakeCur([None, [(7, 'leases', 'Leases')], []])
        _wire(monkeypatch, cur)
        with patch.object(ca, '_classify', return_value={
                'category_slug': 'leases', 'confidence': 95, 'reason': 'x'}):
            out = ca.ensure_category_assignment('ground_lease')
        assert out['status'] == 'pending'

    def test_low_confidence_hold_when_enabled(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_CATEGORY_AI_MANAGED', True, raising=False)
        monkeypatch.setattr(cfg, 'DOC_CATEGORY_HOLD_LOW_CONFIDENCE', True,
                            raising=False)
        monkeypatch.setattr(cfg, 'DOC_CATEGORY_CONFIDENCE_THRESHOLD', 70,
                            raising=False)
        cur = FakeCur([None, [(7, 'leases', 'Leases')], []])
        _wire(monkeypatch, cur)
        with patch.object(ca, '_classify', return_value={
                'category_slug': 'leases', 'confidence': 45, 'reason': 'meh'}):
            out = ca.ensure_category_assignment('board_minutes')
        assert out['status'] == 'pending', \
            'the escape hatch: low confidence must hold when enabled'

    def test_unmatched_type_creates_admin_only_category(self, monkeypatch):
        monkeypatch.setattr(cfg, 'DOC_CATEGORY_AI_MANAGED', True, raising=False)
        cur = FakeCur([None, [(7, 'leases', 'Leases')],
                       (42,),                                    # new category id
                       []])                                      # stewards
        _wire(monkeypatch, cur)
        with patch.object(ca, '_classify', return_value={
                'category_slug': None, 'confidence': 80, 'reason': 'novel'}):
            out = ca.ensure_category_assignment('press_release')
        assert out['created_category'] is True and out['category_id'] == 42
        cat_insert = next(e[0] for e in cur.executed
                          if 'INSERT INTO DocumentCategories' in e[0])
        # The new category gets NO DocumentCategoryGroups rows — admin-only.
        assert not any('INSERT INTO DocumentCategoryGroups' in e[0]
                       for e in cur.executed), \
            'the classifier must never write grants'
        assert 'ai_categoriser' in cat_insert

    def test_failure_returns_none_never_raises(self, monkeypatch):
        monkeypatch.setattr(ca, '_connect',
                            lambda: (_ for _ in ()).throw(RuntimeError('db down')))
        assert ca.ensure_category_assignment('anything') is None

    def test_empty_type_is_a_noop(self, monkeypatch):
        with patch.object(ca, '_connect',
                          side_effect=AssertionError('must not connect')):
            assert ca.ensure_category_assignment('') is None
            assert ca.ensure_category_assignment(None) is None
