"""workflow_run_report.build_run_report — the run digest an authoring AI reads
after a test run (2026-10-09). Shapes mirror the real WorkflowExecutions /
StepExecutions / ExecutionLogs rows."""
import json

from workflow_run_report import build_run_report

EXEC = {"execution_id": "E1", "workflow_name": "Invoices", "status": "Completed",
        "started_at": "2026-10-09T10:00:00", "completed_at": "2026-10-09T10:00:42"}


def step(sid, node_id, name, ntype, status="Completed", out=None, err=None, t="2026-10-09T10:00:01"):
    return {"step_execution_id": sid, "node_id": node_id, "node_name": name, "node_type": ntype,
            "status": status, "output_data": json.dumps(out) if out is not None else None,
            "error_message": err, "started_at": t}


def test_zero_files_and_zero_items_are_flagged():
    steps = [step("s1", "n1", "Find invoices", "Folder Selector", out={"filesFound": False, "selectedFile": None}),
             step("s2", "n2", "Each invoice", "Loop", out={"_loopStats": {"totalItems": 0, "processedItems": 0}})]
    r = build_run_report(EXEC, steps, [])
    assert any("found 0 files" in f for f in r["flags"])
    assert any("processed 0 items" in f for f in r["flags"])
    assert r["duration_s"] == 42.0 and "ATTENTION" in r["text"]


def test_condition_always_same_branch_and_failure_folder():
    steps = [step("s0", "n0", "Each invoice", "Loop", out={"_loopStats": {"totalItems": 3, "processedItems": 3}})]
    for i in range(3):
        steps.append(step(f"c{i}", "n1", "Excel ok?", "Conditional", out={"conditionResult": False}))
        steps.append(step(f"m{i}", "n2", "Move to failed", "File",
                          out={"operation": "move", "destinationPath": f"C:\\in\\failed\\f{i}.pdf"}))
    r = build_run_report(EXEC, steps, [])
    assert any("FALSE on all 3 runs" in f for f in r["flags"])
    assert any("All 3 loop item(s) ended up in a failure folder" in f for f in r["flags"])


def test_warnings_attach_to_their_step_and_unresolved_is_flagged():
    steps = [step("s1", "n1", "Excel ok?", "Conditional", out={"conditionResult": False})]
    logs = [{"step_execution_id": "S1", "log_level": "warning",
             "message": "Conditional leftValue: ${r.status} could not be resolved"},
            {"step_execution_id": None, "log_level": "error", "message": "something at run level"},
            {"step_execution_id": "s1", "log_level": "info", "message": "ignored"}]
    r = build_run_report(EXEC, steps, logs)
    node = r["nodes"][0]
    assert any("could not be resolved" in w for w in node["warnings"])
    assert r["warnings"] == {"error: something at run level": 1}
    assert any("referenced a variable that does not exist" in f for f in r["flags"])


def test_counts_rows_extractions_errors():
    steps = [step("a", "n1", "Extract", "AI Extract", out={"extraction": {"Lines": [1, 2, 3]}}),
             step("b", "n1", "Extract", "AI Extract", status="Failed", err="timed out"),
             step("c", "n2", "Append", "Excel Export", out={"rows_written": 3}),
             step("d", "n3", "Prep", "Automation", out={"status": "success"})]
    r = build_run_report(EXEC, steps, [])
    by = {n["name"]: n for n in r["nodes"]}
    assert by["Extract"]["extracted"] == {"Lines": 3} and by["Extract"]["errors"] == {"timed out": 1}
    assert by["Append"]["rows_written"] == 3 and by["Prep"]["automation"] == {"success": 1}
    assert "ERROR x1: timed out" in r["text"]
    assert not any("wrote 0 rows" in f for f in r["flags"])


def test_workflow_variables_are_listed_and_empty_ones_flagged():
    # Live 2026-10-09: the AI Builder saved variables under the wrong key, so
    # they ran empty; the automation said "inputs are required" and nothing in
    # the report named the variables, so the builder "fixed" the wrong thing.
    steps = [step("s1", "n1", "Prepare", "Automation", status="Failed",
                  err="exit code 1 — stderr: ERROR: both input_folder and output_workbook inputs are required")]
    r = build_run_report(EXEC, steps, [], variables={"inputFolder": "", "outputWorkbook": None,
                                                     "failedFolder": "C:\\in\\failed"})
    assert any("inputFolder, outputWorkbook had no value" in f for f in r["flags"])
    assert "WORKFLOW VARIABLES" in r["text"] and 'failedFolder = "C:\\\\in\\\\failed"' in r["text"]
    assert r["variables"]["failedFolder"] == "C:\\in\\failed"
    # Without variables the report is unchanged.
    assert "WORKFLOW VARIABLES" not in build_run_report(EXEC, steps, [])["text"]


def test_automation_summary_line_is_shown():
    # Live 2026-10-09: The Agent had to list folders to learn what the
    # preparation automation set aside; its last printed line says so.
    summary = '{"duplicates_moved": 6, "not_read": 2, "converted": 6}'
    steps = [step("s1", "n1", "Prepare", "Automation",
                  out={"status": "success", "stdout_tail": "MOVED a.pdf -> duplicates/a.pdf\n" + summary + "\n"}),
             step("s2", "n2", "Tidy", "Code Step", out={"status": "success", "stdout_tail": "x" * 2500})]
    r = build_run_report(EXEC, steps, [])
    assert f"printed (last line): {summary}" in r["text"]
    by = {n["name"]: n for n in r["nodes"]}
    assert by["Tidy"]["automation"] == {"success": 1}
    assert next(iter(by["Tidy"]["printed"])).startswith("…")   # a tail that began mid-line says so


def test_clean_run_has_no_flags():
    steps = [step("s1", "n1", "Find", "Folder Selector", out={"allFiles": ["a", "b"]}),
             step("s2", "n2", "Loop", "Loop", out={"_loopStats": {"totalItems": 2, "processedItems": 2}}),
             step("s3", "n3", "Move", "File", out={"operation": "move", "destinationPath": "C:\\done\\a"}),
             step("s4", "n3", "Move", "File", out={"operation": "move", "destinationPath": "C:\\done\\b"})]
    assert build_run_report(EXEC, steps, [])["flags"] == []
