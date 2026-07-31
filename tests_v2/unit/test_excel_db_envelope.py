"""Database->Excel envelope fix (2026-07-31): unpack_database_envelope must
recognize the Database node's {'columns': [...], 'rows': [[...]]} grid and zip
it into the list-of-dicts row model Excel Export consumes — and must leave
every other shape untouched. The live regression guard is pack 14's
``database_to_excel`` check (real engine run, pandas oracle)."""
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from workflow_execution import unpack_database_envelope  # noqa: E402


ENVELOPE = {"columns": ["store_id", "store_name", "headcount"],
            "rows": [["1", "T&C Manhattan", "8"], ["2", "T&C Brooklyn", "8"]]}


def test_envelope_unpacks_to_records():
    out, was_env, empty = unpack_database_envelope(ENVELOPE)
    assert was_env and not empty
    assert out == [
        {"store_id": "1", "store_name": "T&C Manhattan", "headcount": "8"},
        {"store_id": "2", "store_name": "T&C Brooklyn", "headcount": "8"},
    ]


def test_values_pass_through_uncoerced():
    env = {"columns": ["zip"], "rows": [["08540"]]}
    out, _, _ = unpack_database_envelope(env)
    assert out == [{"zip": "08540"}], "leading zeros must survive (no numeric coercion)"


def test_empty_result_set_flagged():
    out, was_env, empty = unpack_database_envelope({"columns": ["a"], "rows": []})
    assert was_env and empty and out == []


def test_non_envelope_dict_untouched():
    plain = {"store": "Manhattan", "units": 1000}
    out, was_env, empty = unpack_database_envelope(plain)
    assert out is plain and not was_env and not empty


def test_dict_with_non_list_rows_untouched():
    # 'columns'/'rows' keys but rows aren't row-lists -> NOT the envelope
    tricky = {"columns": ["a"], "rows": "not-a-grid"}
    out, was_env, _ = unpack_database_envelope(tricky)
    assert out is tricky and not was_env
    tricky2 = {"columns": ["a"], "rows": [{"a": 1}]}
    out2, was_env2, _ = unpack_database_envelope(tricky2)
    assert out2 is tricky2 and not was_env2


def test_lists_and_scalars_untouched():
    for v in ([{"a": 1}], ["x", "y"], "text", 7, None):
        out, was_env, empty = unpack_database_envelope(v)
        assert out is v or out == v
        assert not was_env and not empty


def test_ragged_rows_zip_short():
    # zip() semantics: extra values beyond the column list are dropped, missing
    # values simply absent — no crash on ragged data.
    env = {"columns": ["a", "b"], "rows": [["1"], ["1", "2", "3"]]}
    out, was_env, _ = unpack_database_envelope(env)
    assert was_env
    assert out == [{"a": "1"}, {"a": "1", "b": "2"}]
