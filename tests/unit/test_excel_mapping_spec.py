"""
Unit tests for the Excel mapping-spec path (excel_utils.py, 2026-08-05).

The contract under test: a semantic Database/extract -> Excel export makes at
most ONE model call per distinct source schema (the mini-model spec call),
never one per row. The model's spec is schema-constrained and then
deterministically re-validated; transforms are fixed Python functions that
fail closed to the raw value.

TRIPWIRE: TestPerExportCallCount.test_50_row_semantic_export_makes_one_llm_call
is the regression guard for the class of bug where per-row model calls sneak
back into the export path (originally caught by pack 14 tier 3 throughput).
"""

import json
from datetime import date, datetime
from unittest.mock import patch

import pytest

import excel_utils


SCHEMA = {
    "type": "table",
    "columns": [
        {"name": "Customer Name", "expected_type": "string"},
        {"name": "Invoice Date", "expected_type": "date"},
        {"name": "Amount", "expected_type": "currency"},
    ],
    "data_start_row": 2,
}

# Field names deliberately DIFFERENT from the columns so neither the identity
# fast path nor a trivial rename can satisfy the mapping.
RECORD = {"company": "Acme Corp", "invoice_dt": "03/15/2026", "amt": "$1,234.56"}

SPEC_RESPONSE = json.dumps({
    "mappings": [
        {"source": "company", "target": "Customer Name", "transform": "none"},
        {"source": "invoice_dt", "target": "Invoice Date", "transform": "date_iso"},
        {"source": "amt", "target": "Amount", "transform": "number"},
    ]
})


@pytest.fixture(autouse=True)
def clean_state():
    """Every test starts with an empty spec cache and zeroed call counters."""
    excel_utils.clear_mapping_spec_cache()
    excel_utils.reset_llm_call_stats()
    yield
    excel_utils.clear_mapping_spec_cache()
    excel_utils.reset_llm_call_stats()


# =============================================================================
# Transforms are deterministic and fail closed
# =============================================================================

class TestTransforms:
    def test_number_currency_string(self):
        assert excel_utils._transform_number("$1,234.56") == 1234.56

    def test_number_accounting_negative(self):
        assert excel_utils._transform_number("(500)") == -500

    def test_number_passthrough_numeric(self):
        assert excel_utils._transform_number(42) == 42
        assert excel_utils._transform_number(3.14) == 3.14

    def test_number_rejects_garbage(self):
        with pytest.raises(ValueError):
            excel_utils._transform_number("abc")
        with pytest.raises(ValueError):
            excel_utils._transform_number(True)

    def test_date_iso_from_common_formats(self):
        assert excel_utils._transform_date_iso("2026-03-15") == "2026-03-15"
        assert excel_utils._transform_date_iso("03/15/2026") == "2026-03-15"
        assert excel_utils._transform_date_iso("March 15, 2026") == "2026-03-15"

    def test_date_iso_from_objects(self):
        assert excel_utils._transform_date_iso(date(2026, 3, 15)) == "2026-03-15"
        assert excel_utils._transform_date_iso(datetime(2026, 3, 15)) == "2026-03-15"
        assert excel_utils._transform_date_iso(
            datetime(2026, 3, 15, 14, 30, 5)) == "2026-03-15 14:30:05"

    def test_date_iso_ambiguity_is_month_first(self):
        # Pinned policy: numeric dates resolve month-first (US convention).
        assert excel_utils._transform_date_iso("03/04/2026") == "2026-03-04"

    def test_date_iso_rejects_garbage(self):
        with pytest.raises(Exception):
            excel_utils._transform_date_iso("not a date")

    def test_text_and_none(self):
        assert excel_utils._transform_text(42) == "42"
        assert excel_utils._transform_text(None) is None
        assert excel_utils._transform_none({"raw": 1}) == {"raw": 1}


class TestTransformNameNormalization:
    """The model cannot pick the vocabulary spelling; we fold format variants
    and refuse everything else."""

    @pytest.mark.parametrize("variant", [
        "date_iso", "Date_ISO", "date-iso", "DATE-ISO", "date iso", " date_iso "])
    def test_variants_fold_to_canonical(self, variant):
        assert excel_utils._normalize_transform_name(variant) == "date_iso"

    @pytest.mark.parametrize("bad", ["iso_date", "isodate", "date", "", None, 42])
    def test_unknown_names_rejected(self, bad):
        assert excel_utils._normalize_transform_name(bad) is None


# =============================================================================
# Spec validation: the model's output is never trusted
# =============================================================================

class TestSpecValidation:
    FIELDS = ["company", "invoice_dt", "amt"]
    COLS = ["Customer Name", "Invoice Date", "Amount"]

    def test_valid_spec_passes_through(self):
        mappings, warnings = excel_utils._validate_mapping_spec(
            json.loads(SPEC_RESPONSE), self.FIELDS, self.COLS)
        assert len(mappings) == 3
        assert warnings == []

    def test_unknown_transform_degrades_to_none_with_warning(self):
        raw = {"mappings": [
            {"source": "amt", "target": "Amount", "transform": "iso date"}]}
        mappings, warnings = excel_utils._validate_mapping_spec(raw, self.FIELDS, self.COLS)
        assert mappings == [{"source": "amt", "target": "Amount", "transform": "none"}]
        assert any("unknown transform" in w for w in warnings)

    def test_hallucinated_source_and_target_dropped(self):
        raw = {"mappings": [
            {"source": "no_such_field", "target": "Amount", "transform": "none"},
            {"source": "amt", "target": "No Such Column", "transform": "none"},
            {"source": "amt", "target": "Amount", "transform": "number"}]}
        mappings, warnings = excel_utils._validate_mapping_spec(raw, self.FIELDS, self.COLS)
        assert mappings == [{"source": "amt", "target": "Amount", "transform": "number"}]
        assert len(warnings) == 2

    def test_duplicate_target_keeps_first(self):
        raw = {"mappings": [
            {"source": "company", "target": "Customer Name", "transform": "none"},
            {"source": "amt", "target": "Customer Name", "transform": "none"}]}
        mappings, warnings = excel_utils._validate_mapping_spec(raw, self.FIELDS, self.COLS)
        assert len(mappings) == 1
        assert mappings[0]["source"] == "company"
        assert any("duplicate target" in w for w in warnings)

    def test_case_insensitive_rescue_uses_actual_names(self):
        raw = {"mappings": [
            {"source": "COMPANY", "target": "customer name", "transform": "none"}]}
        mappings, _ = excel_utils._validate_mapping_spec(raw, self.FIELDS, self.COLS)
        assert mappings == [{"source": "company", "target": "Customer Name", "transform": "none"}]

    def test_garbage_shapes_yield_no_mappings(self):
        for raw in ({}, {"mappings": "nope"}, {"mappings": [42]}, []):
            mappings, warnings = excel_utils._validate_mapping_spec(raw, self.FIELDS, self.COLS)
            assert mappings == []
            assert warnings

    def test_strict_schema_enums_are_the_actual_names(self):
        rf = excel_utils._build_mapping_spec_response_format(self.FIELDS, self.COLS)
        props = rf["json_schema"]["schema"]["properties"]["mappings"]["items"]["properties"]
        assert props["source"]["enum"] == self.FIELDS
        assert props["target"]["enum"] == self.COLS
        assert props["transform"]["enum"] == list(excel_utils._MAPPING_TRANSFORMS)
        assert rf["json_schema"]["strict"] is True


# =============================================================================
# End-to-end through map_data_to_schema (model mocked)
# =============================================================================

class TestMapViaSpec:
    def test_semantic_mapping_applies_spec_and_transforms(self):
        with patch.object(excel_utils, "azureMiniQuickPrompt",
                          return_value=SPEC_RESPONSE) as mock_mini:
            result = excel_utils.map_data_to_schema(RECORD, SCHEMA)
        assert mock_mini.call_count == 1
        assert result["rows"] == [{
            "Customer Name": "Acme Corp",
            "Invoice Date": "2026-03-15",
            "Amount": 1234.56,
        }]

    def test_missing_source_field_yields_null(self):
        record = {"company": "Acme Corp"}  # no invoice_dt / amt
        response = json.dumps({"mappings": [
            {"source": "company", "target": "Customer Name", "transform": "none"}]})
        with patch.object(excel_utils, "azureMiniQuickPrompt", return_value=response):
            result = excel_utils.map_data_to_schema(record, SCHEMA)
        assert result["rows"] == [{"Customer Name": "Acme Corp"}]

    def test_failed_transform_writes_raw_value_with_warning(self):
        record = {"company": "Acme", "invoice_dt": "no date here", "amt": "$5"}
        with patch.object(excel_utils, "azureMiniQuickPrompt", return_value=SPEC_RESPONSE):
            result = excel_utils.map_data_to_schema(record, SCHEMA)
        row = result["rows"][0]
        assert row["Invoice Date"] == "no date here"      # raw, not dropped
        assert row["Amount"] == 5
        assert any("written unchanged" in w for w in result.get("warnings", []))

    def test_identity_fast_path_still_wins_no_model_call(self):
        record = {"Customer Name": "Acme", "Invoice Date": "2026-01-01", "Amount": 5}
        with patch.object(excel_utils, "azureMiniQuickPrompt") as mock_mini, \
             patch.object(excel_utils, "quickPrompt") as mock_primary:
            result = excel_utils.map_data_to_schema(record, SCHEMA)
        assert mock_mini.call_count == 0
        assert mock_primary.call_count == 0
        assert result["rows"][0]["Customer Name"] == "Acme"

    def test_unstructured_input_falls_back_to_legacy_mapper(self):
        legacy = json.dumps({"rows": [{"Customer Name": "Acme"}], "warnings": []})
        with patch.object(excel_utils, "azureMiniQuickPrompt") as mock_mini, \
             patch.object(excel_utils, "quickPrompt", return_value=legacy) as mock_primary:
            result = excel_utils.map_data_to_schema(
                "Acme Corp bought widgets on March 3rd", SCHEMA)
        assert mock_mini.call_count == 0                  # spec path skipped
        assert mock_primary.call_count == 1               # legacy handles free text
        assert result["rows"] == [{"Customer Name": "Acme"}]

    def test_spec_generation_failure_falls_back_and_caches_the_failure(self):
        legacy = json.dumps({"rows": [{"Customer Name": "Acme"}], "warnings": []})
        with patch.object(excel_utils, "azureMiniQuickPrompt",
                          side_effect=RuntimeError("model down")) as mock_mini, \
             patch.object(excel_utils, "quickPrompt", return_value=legacy):
            first = excel_utils.map_data_to_schema(RECORD, SCHEMA)
            second = excel_utils.map_data_to_schema(RECORD, SCHEMA)
        # Two response-format attempts on the FIRST call only; the failure is
        # cached so row 2 doesn't re-pay the spec call.
        assert mock_mini.call_count == 2
        assert first["rows"] and second["rows"]

    def test_field_definition_context_shape_unwraps_values(self):
        """write_extraction_to_excel passes {field: {'value':..., 'description':...}}
        when field definitions exist (doc-extraction path); the applier must
        write the inner value, as the legacy AI mapper did."""
        record = {
            "company": {"value": "Acme Corp", "description": "vendor legal name"},
            "invoice_dt": {"value": "03/15/2026", "description": "invoice date"},
            "amt": {"value": "$1,234.56", "description": "grand total"},
        }
        with patch.object(excel_utils, "azureMiniQuickPrompt", return_value=SPEC_RESPONSE):
            result = excel_utils.map_data_to_schema(record, SCHEMA)
        assert result["rows"] == [{
            "Customer Name": "Acme Corp",
            "Invoice Date": "2026-03-15",
            "Amount": 1234.56,
        }]

    def test_env_flag_disables_spec_path(self, monkeypatch):
        monkeypatch.setenv("EXCEL_AI_MAPPING_SPEC", "false")
        legacy = json.dumps({"rows": [{"Customer Name": "Acme"}], "warnings": []})
        with patch.object(excel_utils, "azureMiniQuickPrompt") as mock_mini, \
             patch.object(excel_utils, "quickPrompt", return_value=legacy) as mock_primary:
            excel_utils.map_data_to_schema(RECORD, SCHEMA)
        assert mock_mini.call_count == 0
        assert mock_primary.call_count == 1


# =============================================================================
# TRIPWIRE - the per-export model-call budget
# =============================================================================

class TestPerExportCallCount:
    def test_50_row_semantic_export_makes_one_llm_call(self):
        """Simulates the workflow engine's per-row loop (workflow_execution.py
        calls map_data_to_schema once per row) over 50 semantic-mapping rows.
        Budget: ONE model call for the whole export. If this fails, a per-row
        model call has snuck back into the export path - that is the exact
        regression that cost ~1M tokens per 1,000-row export."""
        rows = [{"company": f"Co {i}", "invoice_dt": "03/15/2026",
                 "amt": f"${i},000.00"} for i in range(50)]
        with patch.object(excel_utils, "azureMiniQuickPrompt",
                          return_value=SPEC_RESPONSE) as mock_mini, \
             patch.object(excel_utils, "quickPrompt") as mock_primary:
            results = [excel_utils.map_data_to_schema(row, SCHEMA) for row in rows]

        assert mock_mini.call_count == 1, (
            f"Expected 1 spec call for 50 rows, got {mock_mini.call_count} - "
            "per-row model calls are back in the export path")
        assert mock_primary.call_count == 0, (
            "Legacy per-row primary-model mapper ran during a structured "
            "semantic export - the spec path regressed")
        assert len(results) == 50
        assert all(r["rows"][0]["Invoice Date"] == "2026-03-15" for r in results)
        assert results[7]["rows"][0]["Amount"] == 7000.0

        stats = excel_utils.get_llm_call_stats()
        assert stats == {"mapping_spec": 1}

    def test_two_distinct_schemas_cost_two_calls(self):
        """Mixed-shape exports pay one spec call per DISTINCT field set, not
        per row."""
        shape_a = [{"company": f"A{i}", "invoice_dt": "01/01/2026", "amt": "$1"}
                   for i in range(10)]
        shape_b = [{"vendor": f"B{i}", "paid_on": "01/02/2026", "total": "$2"}
                   for i in range(10)]
        response_b = json.dumps({"mappings": [
            {"source": "vendor", "target": "Customer Name", "transform": "none"},
            {"source": "paid_on", "target": "Invoice Date", "transform": "date_iso"},
            {"source": "total", "target": "Amount", "transform": "number"}]})
        responses = {"company": SPEC_RESPONSE, "vendor": response_b}

        def fake_mini(prompt, *args, **kwargs):
            return responses["company"] if '"company"' in prompt else responses["vendor"]

        with patch.object(excel_utils, "azureMiniQuickPrompt",
                          side_effect=fake_mini) as mock_mini:
            for row in shape_a + shape_b:
                excel_utils.map_data_to_schema(row, SCHEMA)

        assert mock_mini.call_count == 2
