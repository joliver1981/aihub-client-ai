"""get_column_value_sets — the VALUE half of schema grounding.

Live failure this exists for: a generated dunning automation wrote
`activity_type = 'promise_to_pay'` (the readable phrase from the user's request)
against a column that holds 'ptp'. Zero rows, no error, the promise-to-pay hold
never fired, and a customer who had already promised to pay got a dunning letter.

Schema grounding already stopped invented column NAMES. These tests cover the
value side, and in particular the rule that keeps the cure from becoming the
disease: a column is either enumerated in FULL or not at all. Handing back 25 of
200 values invites the same failure -- the model concludes its value is absent
and invents one anyway.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ai_metadata_generator import get_column_value_sets  # noqa: E402

SQLSERVER_CS = ("DRIVER={ODBC Driver 17 for SQL Server};SERVER=x;DATABASE=d;"
                "UID=u;PWD=p")


def cols(*specs):
    """(name, type, maxlen) -> INFORMATION_SCHEMA-shaped column dicts."""
    return [{'COLUMN_NAME': n, 'DATA_TYPE': t, 'CHARACTER_MAXIMUM_LENGTH': ln}
            for n, t, ln in specs]


class FakeDb:
    """Stands in for execute_sql_query_v2: answers the cardinality probe from
    `data`, then serves DISTINCT values per column."""

    def __init__(self, data, fail_on=None):
        self.data = data                  # {column: [values]}
        self.fail_on = fail_on or (lambda q: False)
        self.queries = []

    def __call__(self, query, _cs):
        self.queries.append(query)
        if self.fail_on(query):
            return None, "boom"
        if 'COUNT(DISTINCT' in query:
            rows = [{'col_name': c, 'n': len(set(v))} for c, v in self.data.items()
                    if f"[{c}]" in query]
            return pd.DataFrame(rows), None
        for c, vals in self.data.items():
            if f"SELECT DISTINCT" in query and f"[{c}]" in query:
                uniq = sorted(set(vals))
                cap = 26
                if ' TOP ' in query:
                    cap = int(query.split(' TOP ')[1].split()[0])
                return pd.DataFrame({c: uniq[:cap]}), None
        return pd.DataFrame(), None


# --------------------------------------------------------------- the real bug
def test_surfaces_the_abbreviated_value_the_model_would_not_guess():
    db = FakeDb({'activity_type': ['ptp', 'ptp', 'note', 'dispute', 'call']})
    out = get_column_value_sets(db, 'dbo.CG_CollectionActivity', SQLSERVER_CS,
                                cols(('activity_type', 'varchar', 20)))
    assert out['activity_type']['values'] == ['call', 'dispute', 'note', 'ptp']
    # The value the model actually invented is provably absent.
    assert 'promise_to_pay' not in out['activity_type']['values']


def test_mixed_conventions_are_all_shown():
    """is_holiday holds Y, N, Yes AND No. Filtering ='Y' silently drops rows,
    so the model has to see all four to have any chance."""
    db = FakeDb({'is_holiday': ['Y', 'N', 'Yes', 'No', 'Y', 'N']})
    out = get_column_value_sets(db, 'TS.calendar_master', SQLSERVER_CS,
                                cols(('is_holiday', 'varchar', 3)))
    assert set(out['is_holiday']['values']) == {'Y', 'N', 'Yes', 'No'}


# ------------------------------------------------- never partially enumerated
def test_over_cap_reports_a_count_and_no_values():
    db = FakeDb({'customer_id': [f'C{i:05d}' for i in range(300)]})
    out = get_column_value_sets(db, 'dbo.Invoices', SQLSERVER_CS,
                                cols(('customer_id', 'varchar', 20)), max_values=25)
    v = out['customer_id']
    assert v['too_many'] is True
    assert v['distinct_count'] == 300
    assert 'values' not in v, "a partial list is worse than none"


def test_exactly_at_the_cap_is_still_enumerated_in_full():
    db = FakeDb({'code': [f'V{i:02d}' for i in range(25)]})
    out = get_column_value_sets(db, 't', SQLSERVER_CS,
                                cols(('code', 'varchar', 10)), max_values=25)
    assert len(out['code']['values']) == 25
    assert out['code']['distinct_count'] == 25


def test_one_over_the_cap_flips_to_count_only():
    db = FakeDb({'code': [f'V{i:02d}' for i in range(26)]})
    out = get_column_value_sets(db, 't', SQLSERVER_CS,
                                cols(('code', 'varchar', 10)), max_values=25)
    assert out['code'].get('too_many') is True and 'values' not in out['code']


def test_every_enumerated_column_is_complete():
    db = FakeDb({'a': ['x', 'y'], 'b': [f'{i}' for i in range(40)], 'c': ['p', 'q', 'r']})
    out = get_column_value_sets(db, 't', SQLSERVER_CS,
                                cols(('a', 'varchar', 10), ('b', 'varchar', 10),
                                     ('c', 'varchar', 10)), max_values=25)
    for name, v in out.items():
        if 'values' in v:
            assert len(v['values']) == v['distinct_count'], f"{name} truncated"


# ------------------------------------------------------------------- budget
def test_budget_defers_whole_columns_rather_than_truncating_them():
    data = {f'c{i}': [f'value-{i}-{j}' for j in range(20)] for i in range(12)}
    out = get_column_value_sets(db := FakeDb(data), 't', SQLSERVER_CS,
                                cols(*[(f'c{i}', 'varchar', 30) for i in range(12)]),
                                payload_budget=300)
    assert any(v.get('deferred') for v in out.values()), "budget never engaged"
    for name, v in out.items():
        assert not (v.get('deferred') and v.get('values')), f"{name} both deferred and listed"
        if 'values' in v:
            assert len(v['values']) == v['distinct_count']
    assert db.queries, "should still have probed"


def test_probe_cap_defers_the_tail_explicitly_not_silently():
    data = {f'c{i}': ['a', 'b'] for i in range(40)}
    out = get_column_value_sets(FakeDb(data), 't', SQLSERVER_CS,
                                cols(*[(f'c{i}', 'varchar', 10) for i in range(40)]),
                                max_probe_columns=10)
    deferred = [n for n, v in out.items() if v.get('deferred')]
    assert len(deferred) == 30, "the un-probed tail must be reported, never dropped"


# ------------------------------------------------------------- single column
def test_only_column_returns_just_that_column():
    db = FakeDb({'activity_type': ['ptp', 'note'], 'status': ['open', 'closed']})
    out = get_column_value_sets(db, 't', SQLSERVER_CS,
                                cols(('activity_type', 'varchar', 20),
                                     ('status', 'varchar', 20)),
                                only_column='activity_type')
    assert set(out) == {'activity_type'}


def test_only_column_is_case_insensitive():
    db = FakeDb({'activity_type': ['ptp']})
    out = get_column_value_sets(db, 't', SQLSERVER_CS,
                                cols(('activity_type', 'varchar', 20)),
                                only_column='ACTIVITY_TYPE')
    assert 'activity_type' in out


def test_only_column_ignores_the_budget():
    """A deliberate single-column lookup is the escape hatch for a wide table;
    applying the table-wide budget to it would defeat the point."""
    db = FakeDb({'code': [f'value-{i}' for i in range(20)]})
    out = get_column_value_sets(db, 't', SQLSERVER_CS, cols(('code', 'varchar', 30)),
                                payload_budget=1, only_column='code')
    assert len(out['code']['values']) == 20


# ------------------------------------------------------ candidate selection
@pytest.mark.parametrize("dtype,maxlen,expected", [
    ('varchar', 20, True),
    ('nvarchar', 50, True),
    ('char', 2, True),
    ('varchar', 4000, False),      # free text, not a code
    ('varchar', -1, False),        # varchar(MAX)
    ('int', None, False),
    ('datetime', None, False),
    ('decimal', None, False),
])
def test_only_short_string_columns_are_probed(dtype, maxlen, expected):
    db = FakeDb({'c': ['a', 'b']})
    out = get_column_value_sets(db, 't', SQLSERVER_CS, cols(('c', dtype, maxlen)))
    assert ('c' in out) is expected


def test_odd_column_names_are_skipped_not_interpolated():
    db = FakeDb({"weird';DROP": ['a']})
    out = get_column_value_sets(db, 't', SQLSERVER_CS,
                                cols(("weird';DROP", 'varchar', 10)))
    assert out == {}
    assert not db.queries, "an unsafe identifier must never reach SQL"


# ------------------------------------------------------------- best effort
def test_probe_failure_returns_empty_and_never_raises():
    db = FakeDb({'c': ['a']}, fail_on=lambda q: 'COUNT(DISTINCT' in q)
    assert get_column_value_sets(db, 't', SQLSERVER_CS, cols(('c', 'varchar', 10))) == {}


def test_value_query_failure_drops_only_that_column():
    db = FakeDb({'good': ['a', 'b'], 'bad': ['x', 'y']},
                fail_on=lambda q: 'SELECT DISTINCT' in q and '[bad]' in q)
    out = get_column_value_sets(db, 't', SQLSERVER_CS,
                                cols(('good', 'varchar', 10), ('bad', 'varchar', 10)))
    assert out['good']['values'] == ['a', 'b']
    assert 'values' not in out.get('bad', {})


def test_no_candidate_columns_costs_nothing():
    db = FakeDb({})
    assert get_column_value_sets(db, 't', SQLSERVER_CS,
                                 cols(('id', 'int', None), ('ts', 'datetime', None))) == {}
    assert not db.queries


def test_long_values_are_clipped_but_the_set_stays_complete():
    db = FakeDb({'c': ['x' * 500, 'y']})
    out = get_column_value_sets(db, 't', SQLSERVER_CS, cols(('c', 'varchar', 100)),
                                max_value_len=20)
    assert out['c']['distinct_count'] == 2 and len(out['c']['values']) == 2
    assert all(len(v) <= 21 for v in out['c']['values'])
