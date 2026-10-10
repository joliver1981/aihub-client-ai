"""Excel Export "append" creates the workbook on first use (2026-10-10).

A workflow that adds every run's rows to a register in its Results folder
used to fail its first run ("Cannot append - file does not exist"), and with
workflow file drops nobody can put a starting workbook there. Now the first
append creates it: from a copy of a separate template when there is one,
otherwise from the rows, with a manual field mapping naming the columns so
the next append lines up with them.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

openpyxl = pytest.importorskip("openpyxl")
import excel_utils  # noqa: E402


@pytest.fixture(autouse=True)
def no_ai(monkeypatch):
    # sheet-type detection may ask an AI for unclear sheets; these are plain tables
    monkeypatch.setattr(excel_utils, "detect_template_type", lambda ws: "table")


def _result(**values):
    return {"fields": {k: {"value": v} for k, v in values.items()}}


def _rows(path, sheet=None):
    wb = openpyxl.load_workbook(path)
    ws = wb[sheet] if sheet else wb.active
    rows = [[c for c in r] for r in ws.iter_rows(values_only=True)]
    wb.close()
    return [r for r in rows if any(v is not None for v in r)]


MAPPING = {"InvoiceNumber": "Invoice #", "Vendor": "Vendor", "Amount": "Amount"}


def test_first_append_creates_the_register_and_the_next_one_lines_up(tmp_path):
    out = tmp_path / "outbox" / "Invoice_Register.xlsx"
    first = excel_utils.write_extraction_to_excel(
        _result(InvoiceNumber="INV-1", Vendor="Acme", Amount=10.5), str(out),
        template_path=str(out), operation="append", field_mapping=MAPPING, sheet_name="Lines")
    assert first.get("success"), first
    second = excel_utils.write_extraction_to_excel(
        _result(InvoiceNumber="INV-2", Vendor="Globex", Amount=7), str(out),
        template_path=str(out), operation="append", field_mapping=MAPPING, sheet_name="Lines")
    assert second.get("success"), second
    rows = _rows(out, "Lines")
    assert list(rows[0][:3]) == ["Invoice #", "Vendor", "Amount"]
    assert [list(r[:3]) for r in rows[1:]] == [["INV-1", "Acme", 10.5], ["INV-2", "Globex", 7]]


def test_first_append_with_a_separate_template_starts_from_a_copy(tmp_path):
    template = tmp_path / "Register_Template.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Lines"
    ws.append(["Invoice #", "Vendor", "Amount", "Approved By"])
    wb.save(template)
    out = tmp_path / "outbox" / "Invoice_Register.xlsx"
    res = excel_utils.write_extraction_to_excel(
        _result(InvoiceNumber="INV-9", Vendor="Initech", Amount=3), str(out),
        template_path=str(template), operation="append", field_mapping=MAPPING, sheet_name="Lines")
    assert res.get("success"), res
    rows = _rows(out, "Lines")
    assert list(rows[0]) == ["Invoice #", "Vendor", "Amount", "Approved By"]   # the template's header
    assert list(rows[1][:3]) == ["INV-9", "Initech", 3]
    assert _rows(template, "Lines") == [["Invoice #", "Vendor", "Amount", "Approved By"]]  # template untouched


def test_new_is_unchanged(tmp_path):
    out = tmp_path / "new.xlsx"
    res = excel_utils.write_extraction_to_excel(
        _result(InvoiceNumber="INV-3", Vendor="Hooli", Amount=1), str(out),
        operation="new", field_mapping=MAPPING)
    assert res.get("success"), res
    assert list(_rows(out)[0][:3]) == ["InvoiceNumber", "Vendor", "Amount"]   # "new" still names columns by field
