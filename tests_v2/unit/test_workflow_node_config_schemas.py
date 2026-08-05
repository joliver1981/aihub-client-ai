"""Config-key contract tests (2026-07-30 fix for the CC-native guessed-keys
defect family): workflow_node_schemas + its two enforcement chokepoints
(command_center_service.graph.workflow_tools add_node/update_node and
workflow_deterministic_validator.detect_config_key_errors).

The "live shapes" below are byte-for-byte the configs the CC-native agent
actually persisted (wf 1307 Excel, wf 1336 Set Variable) — the regression
guard is that these exact configs can never again save silently."""
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for p in (REPO, os.path.join(REPO, "command_center_service")):
    if p not in sys.path:
        sys.path.insert(0, p)

from workflow_node_schemas import (  # noqa: E402
    validate_node_config, config_errors, config_warnings)
from command_center_service.graph import workflow_tools as wt  # noqa: E402
import workflow_deterministic_validator as wdv  # noqa: E402


# ---------------------------------------------------------------- live shapes

# wf 1336 (REG-headcount-file): CC guessed useExpression/value -> engine read
# neither -> variable resolved to "" -> 0-byte file, every step "Completed".
LIVE_SET_VARIABLE_BAD = {
    "useExpression": True,
    "value": "'\\n'.join([f\"{row['store_id']}\" for row in headcount_rows])",
    "variableName": "headcount_text",
}

# wf 1307 (REG-headcount): CC guessed dataVariable + excelOperation "create"
# -> engine read inputVariable ('' ) + fell back to append -> "No data to write".
LIVE_EXCEL_BAD = {
    "dataVariable": "headcount_rows",
    "excelOperation": "create",
    "excelOutputPath": "C:/temp/aihub_test/reg_headcount.xlsx",
    "outputVariable": "excel_result",
}

# Real Designer-built shapes (workflows/ mirrors) — must stay clean.
GOOD_SET_VARIABLE = {
    "variableName": "x", "valueSource": "direct", "valueExpression": "10",
    "evaluateAsExpression": False, "outputPath": "",
}
GOOD_EXCEL = {
    "inputVariable": "${storeResults}", "excelOperation": "new",
    "excelOutputPath": "${excelOutputPath}", "excelSheetName": "StoreResults",
    "flattenArray": True, "mappingMode": "ai", "outputVariable": "excelExportResult",
}
GOOD_FILE_TRANSFER = {
    "protocol": "sftp", "host": "127.0.0.1", "port": "2222",
    "username": "testuser", "secretName": "SFTP_TEST_PASSWORD",
    "operation": "upload", "localPath": "C:/tmp/x.csv", "remotePath": "outgoing",
    "overwrite": "overwrite", "zeroMatchPolicy": "fail",
    "outputVariable": "xfer", "continueOnError": False,
}


# ------------------------------------------------------------- schema module

def test_live_set_variable_shape_is_rejected():
    errs = config_errors("Set Variable", LIVE_SET_VARIABLE_BAD)
    joined = " ".join(errs)
    assert errs, "the exact wf-1336 config must produce errors"
    assert "valueExpression" in joined          # did-you-mean for 'value'
    assert "evaluateAsExpression" in joined     # did-you-mean for 'useExpression'


def test_live_excel_shape_is_rejected():
    errs = config_errors("Excel Export", LIVE_EXCEL_BAD)
    joined = " ".join(errs)
    assert errs, "the exact wf-1307 config must produce errors"
    assert "inputVariable" in joined            # did-you-mean for 'dataVariable'
    assert "'new'" in joined                    # enum alias create -> new


def test_good_designer_shapes_are_clean():
    for ntype, cfg in (("Set Variable", GOOD_SET_VARIABLE),
                       ("Excel Export", GOOD_EXCEL),
                       ("File Transfer", GOOD_FILE_TRANSFER)):
        assert config_errors(ntype, cfg) == [], f"{ntype} good shape flagged"


def test_set_variable_requires_a_value_source():
    errs = config_errors("Set Variable", {"variableName": "x"})
    assert any("valueExpression" in e for e in errs)


def test_loop_variable_source_requires_loop_source():
    errs = config_errors("Loop", {"sourceType": "variable", "itemVariable": "it"})
    assert any("loopSource" in e for e in errs)
    assert config_errors("Loop", {"sourceType": "auto"}) == []


def test_legacy_excel_update_op_tolerated():
    # Customer Onboarding workflows carry excelOperation='update'; the
    # template-path rule governs them — the enum must not fight it.
    cfg = dict(GOOD_EXCEL, excelOperation="update",
               excelTemplatePath="C:/t/template.xlsx")
    assert config_errors("Excel Export", cfg) == []


def test_unknown_key_is_warning_not_error():
    cfg = dict(GOOD_SET_VARIABLE, someLegacyJunkKey="x")
    assert config_errors("Set Variable", cfg) == []
    assert any("someLegacyJunkKey" in w for w in config_warnings("Set Variable", cfg))


def test_unschema_node_types_fail_open():
    assert validate_node_config("Compliance Process", {"anything": 1}) == []
    assert validate_node_config("Brand New Future Node", {"x": 1}) == []


# ------------------------------------------------- CC chokepoint (add/update)

def test_add_node_rejects_live_set_variable_shape():
    definition = {"nodes": [], "connections": []}
    res = wt.add_node(definition, "Set Variable", "fmt", LIVE_SET_VARIABLE_BAD)
    assert res["ok"] is False
    assert "valueExpression" in res["error"]
    assert definition["nodes"] == [], "nothing may persist on rejection"


def test_add_node_rejects_live_excel_shape():
    definition = {"nodes": [], "connections": []}
    res = wt.add_node(definition, "Excel Export", "export", LIVE_EXCEL_BAD)
    assert res["ok"] is False
    assert "inputVariable" in res["error"]
    assert definition["nodes"] == []


def test_add_node_allows_draft_configs():
    # Draft-first authoring is legitimate (AIHUB-0054): an EMPTY config is
    # accepted at add time — the save-time validator drafts it with reasons.
    # Only wrong-key / invalid-enum configs (silent-misread class) hard-reject.
    definition = {"nodes": [], "connections": []}
    for ntype in ("Database", "Alert", "Set Variable", "Portal"):
        res = wt.add_node(definition, ntype, ntype.lower(), {},
                          user_context={"user_id": 13})
        assert res["ok"] is True, f"{ntype} empty draft rejected: {res.get('error')}"
    assert len(definition["nodes"]) == 4


def test_add_node_accepts_good_shapes():
    definition = {"nodes": [], "connections": []}
    for ntype, label, cfg in (("Set Variable", "sv", GOOD_SET_VARIABLE),
                              ("Excel Export", "xl", GOOD_EXCEL),
                              ("File Transfer", "ft", GOOD_FILE_TRANSFER)):
        res = wt.add_node(definition, ntype, label, cfg)
        assert res["ok"] is True, f"{ntype}: {res.get('error')}"
    assert len(definition["nodes"]) == 3


def test_update_node_rejects_bad_merge_without_mutating():
    definition = {"nodes": [], "connections": []}
    res = wt.add_node(definition, "Set Variable", "sv", GOOD_SET_VARIABLE)
    nid = res["node_id"]
    before = dict(definition["nodes"][0]["config"])
    res2 = wt.update_node(definition, nid, config_patch={"value": "oops"})
    assert res2["ok"] is False
    assert definition["nodes"][0]["config"] == before, "rejected patch must not mutate"


def test_update_node_accepts_good_patch():
    definition = {"nodes": [], "connections": []}
    res = wt.add_node(definition, "Set Variable", "sv", GOOD_SET_VARIABLE)
    res2 = wt.update_node(definition, res["node_id"],
                          config_patch={"valueExpression": "20"})
    assert res2["ok"] is True
    assert definition["nodes"][0]["config"]["valueExpression"] == "20"


# ------------------------------------------------------- validator chokepoint

def _state(nodes):
    return {"nodes": nodes, "connections": []}


def test_validator_errors_on_live_shapes():
    state = _state([
        {"id": "n1", "type": "Set Variable", "label": "fmt",
         "config": LIVE_SET_VARIABLE_BAD},
        {"id": "n2", "type": "Excel Export", "label": "xl",
         "config": LIVE_EXCEL_BAD},
    ])
    issues = wdv.detect_config_key_errors(state)
    errors = [i for i in issues if i.severity == wdv.ERROR]
    assert {i.node_id for i in errors} == {"n1", "n2"}
    assert all(i.code == "NODE_CONFIG_KEY_CONTRACT" for i in issues)


def test_validator_clean_on_good_shapes_and_registered():
    state = _state([
        {"id": "n1", "type": "Set Variable", "label": "sv",
         "config": GOOD_SET_VARIABLE},
    ])
    assert [i for i in wdv.detect_config_key_errors(state)
            if i.severity == wdv.ERROR] == []
    assert wdv.detect_config_key_errors in wdv.DETECTORS


# ------------------------------------------------------- Horizon live shapes
# 2026-08-05 regression (wf 1218 "Customer Onboarding - Horizon Replica"):
# the contract tables understated the engine, so a WORKING imported workflow
# lit up with false warnings/errors — File copy/move flagged as invalid
# operations, destinationPath/contentPath/contentVariable as unknown keys,
# Loop defaultArray (Designer-written) as unknown, and the entire Excel
# excelOperation='update' key family as unknown. These configs are
# byte-for-byte from wf 1218 and must stay CLEAN.

LIVE_HORIZON_FILE_COPY = {
    "content": "", "contentPath": "", "contentSource": "direct",
    "contentVariable": "", "continueOnError": False,
    "destinationPath": "C:/temp/x/output/${customerFolder.name}_Template.xlsx",
    "filePath": "C:/temp/x/template/Empty_Template.xlsx",
    "operation": "copy", "outputVariable": "", "saveToVariable": False,
}

LIVE_HORIZON_LOOP = {
    "arrayInfoVariable": "", "defaultArray": "[]", "emptyBehavior": "skip",
    "indexVariable": "fileIdx", "itemVariable": "documentFile",
    "loopSource": "${documentFiles.data.items}", "maxIterations": "100",
    "outputMode": "array", "sourceType": "auto", "splitDelimiter": ",",
}

LIVE_HORIZON_EXCEL_UPDATE = {
    "addChangeTimestamp": True, "addNewRecords": True,
    "aiKeyMatchingInstructions": "match on core requirement",
    "aiMappingInstructions": "", "carryForwardFields": "",
    "changeHighlightColor": "#ffff00", "changeLogSheet": "Change History",
    "deletedRowColor": "#ffb6c1", "excelOperation": "update",
    "excelOutputPath": "${templatePath}", "excelSheetName": "Requirements_Notes",
    "excelTemplatePath": "C:/temp/x/Master Template.xlsx",
    "fieldMapping": None, "flattenArray": False, "highlightChanges": True,
    "inputVariable": "${extractedNotes.Notes}",
    "keyColumns": "customer,program_type,topic,requirement",
    "manualFields": "customer,program_type,requirement",
    "mappingMode": "ai", "markDeletedAs": "strikethrough",
    "newRowColor": "#90ee90", "smartChangeStrictness": "lenient",
    "timestampColumn": "Last Updated", "trackDeletedRows": False,
    "useAIKeyMatching": True, "useSmartChangeDetection": True,
}


def test_horizon_file_copy_and_move_are_clean():
    assert validate_node_config("File", LIVE_HORIZON_FILE_COPY) == []
    move_cfg = dict(LIVE_HORIZON_FILE_COPY, operation="move",
                    sourcePath="C:/temp/x/a.pdf")
    assert validate_node_config("File", move_cfg) == []


def test_horizon_loop_default_array_is_clean():
    assert validate_node_config("Loop", LIVE_HORIZON_LOOP) == []


def test_horizon_excel_update_family_is_clean():
    assert validate_node_config("Excel Export", LIVE_HORIZON_EXCEL_UPDATE) == []


def test_validator_clean_on_horizon_workflow_shapes():
    state = _state([
        {"id": "n1", "type": "File", "label": "Copy New Template",
         "config": LIVE_HORIZON_FILE_COPY},
        {"id": "n2", "type": "Loop", "label": "Start Loop Files",
         "config": LIVE_HORIZON_LOOP},
        {"id": "n3", "type": "Excel Export", "label": "Update Template",
         "config": LIVE_HORIZON_EXCEL_UPDATE},
    ])
    assert wdv.detect_config_key_errors(state) == []


def test_wrong_keys_still_error_after_widening():
    # Widening `known` must not have dulled the contract's real purpose.
    assert config_errors("Excel Export", LIVE_EXCEL_BAD)
    assert config_errors("Set Variable", LIVE_SET_VARIABLE_BAD)
    assert config_errors("File", {"operation": "shred", "filePath": "x"})
