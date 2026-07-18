"""
AIHUB-0015 F1 — discovered-table names must not be schema-qualified twice.

Root cause (tester bisection, 2026-07-11/17): connections.discover_tables
returns TABLE_NAME values that are ALREADY schema-qualified ('TS.sales'), and
the retest-#3 grounding code re-prepended TABLE_SCHEMA → 'TS.TS.sales'. The
analyze-grounding guard then substituted the doubled name into
connections.analyze_tables, the analysis failed ('Could not retrieve schema for
table: TS.TS.sales'), the data dictionary stayed empty, and a freshly built
data agent honestly answered 'no tables or columns have been documented'.
Proven by bisection: correct names analyzed 2/2; doubled names reproduced the
build failure exactly.

The fix is qualification-idempotence: an already-dotted name is used as-is.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_NODES = _ROOT / "builder_service" / "graph" / "nodes.py"


def _qualify(_tn, _ts):
    """The exact expression under test (kept in sync by the contract below)."""
    return str(_tn) if ("." in str(_tn)) else f"{_ts}.{_tn}"


class TestQualificationIdempotence:
    @pytest.mark.parametrize("tn,ts,expected", [
        ("TS.sales", "TS", "TS.sales"),            # the live bug: already qualified
        ("TS.product_master", "TS", "TS.product_master"),
        ("sales", "TS", "TS.sales"),               # bare name still gets the schema
        ("orders", "dbo", "dbo.orders"),
        ("dbo.orders", "dbo", "dbo.orders"),
    ])
    def test_never_doubles_the_schema(self, tn, ts, expected):
        q = _qualify(tn, ts)
        assert q == expected
        assert not q.lower().startswith(f"{ts}.{ts}.".lower())

    def test_source_contract_fix_present(self):
        """The fixed expression must stay in the discover_tables tracking block —
        a refactor back to unconditional f"{_ts}.{_tn}" reintroduces TS.TS.sales."""
        src = _NODES.read_text(encoding="utf-8")
        assert '_qual = str(_tn) if ("." in str(_tn)) else f"{_ts}.{_tn}"' in src
        # the buggy unconditional form must not reappear in that block
        import re
        block = re.search(r"connections\.discover_tables.*?discovered_tables_by_conn",
                          src, re.S)
        assert block is not None
        assert '_qual = f"{_ts}.{_tn}"' not in block.group(0)
