"""Field-selection popularity sort must work on the shape production passes.

document_search_super_enhanced_debug builds {'field_name', 'document_count'}
dicts (simplified_fields) for ai_select_relevant_fields, but the sorts keyed
on usage_count only — a silent no-op for that shape. Inert while the universe
cap and DOC_TOP_N_FIELDS_INCLUDED_IN_RESULTS are both 500, live the moment
those numbers diverge.
"""
import sys
from unittest.mock import MagicMock

import pytest

# DocUtils drags in heavy deps at import; stub what this box's test env lacks.
for _name in ('anthropic', 'PyPDF2', 'fitz', 'openai'):
    if _name not in sys.modules:
        try:
            __import__(_name)
        except ImportError:
            sys.modules[_name] = MagicMock()

import DocUtils as docutils  # noqa: E402


@pytest.mark.unit
class TestFieldSelectionSortShape:
    def test_document_count_shape_sorts_fill_order(self):
        available = [{'field_name': f'f{i}', 'document_count': i} for i in range(10)]
        out = docutils.get_fallback_field_selection(available, "find x", 3)
        assert out['selected_fields'] == ['f9', 'f8', 'f7']

    def test_usage_count_shape_still_sorts(self):
        available = [{'field_name': f'f{i}', 'usage_count': 10 - i} for i in range(10)]
        out = docutils.get_fallback_field_selection(available, "find x", 3)
        assert out['selected_fields'] == ['f0', 'f1', 'f2']
