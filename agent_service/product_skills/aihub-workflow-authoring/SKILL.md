---
name: aihub-workflow-authoring
description: Use when building, changing, running or debugging a VISUAL
  workflow (the Workflow Designer's nodes and arrows) — the build, save, run,
  read-the-report, fix loop; the folder-of-files pattern; and the gotchas.
---

# Building visual workflows

Visual workflows are the boxes-and-arrows processes of the Workflow Designer.
Whatever you save, the user can open and edit by hand. Code flows (Python
step graphs) and recorded portal workflows (browser replays) are different
things with their own tools.

## The loop

1. `get_workflow_node_reference` with no arguments (overview, rules, every
   node's EXACT config keys), then again with `node_types` = the types you
   will use (their full settings). Use only the listed keys: the engine
   ignores any other key, so an invented key silently does nothing.
2. Design it. Exactly one node has `isStart: true`. Put settings (folders,
   workbook paths, recipients) in workflow `variables` and use them as
   `${name}`, so the user can repoint them without touching nodes.
3. `save_workflow`. Fix EVERY error and warning it returns, then save again
   with `replace_existing=true`. A workflow saved with errors is a draft.
4. Before the first real run, say what a run does (moves files, writes the
   workbook, sends email or approvals) and get a go-ahead, unless the user
   already asked you to run or test it.
5. `run_workflow` returns the run report. Read it. `variables_json` points a
   run at test folders without changing the saved workflow.
6. ATTENTION flags are silent failures: 0 files found, a loop with 0 items,
   every item down the failure branch, a variable that does not exist. Fix,
   save, run again. "Completed" is not proof; a report showing the expected
   outcome (files found and processed, rows written, files moved where you
   meant) is.

## Definition shape

    {"nodes": [{"id": "n1", "type": "Folder Selector", "label": "Find invoices",
                "isStart": true, "config": {...}}],
     "connections": [{"source": "n1", "target": "n2", "type": "pass"}],
     "variables": {"inputFolder": {"type": "string", "defaultValue": "C:\\Data\\in"}}}

Connection types: `pass` (the node succeeded), `fail` (the node failed),
`complete` (always). Positions are added for you if you leave them out.

## The folder-of-files pattern (a proven build)

    n1 Folder Selector  folderPath ${inputFolder}, filePattern "*.pdf|*.xlsx",
                        selectionMode "all", outputVariable "invoiceFiles"
    n2 Loop             sourceType "variable", loopSource "invoiceFiles",
                        itemVariable "SourceFile", emptyBehavior "skip"
    n3 AI Extract       inputSource "auto", inputVariable "${SourceFile}",
                        outputDestination "variable", outputVariable "invoice",
                        fields [{name "Lines", type "repeated_group",
                                 children [{name, type "text", description, required}]}]
    n4 Excel Export     excelOperation "append", excelOutputPath ${outputWorkbook},
                        excelTemplatePath ${outputWorkbook}, excelSheetName "Lines",
                        inputVariable "${invoice.Lines}", flattenArray true,
                        carryForwardFields "SourceFile", mappingMode "ai"
    n5 File             operation "move", filePath ${SourceFile},
                        destinationPath ${processedFolder}
    n6 File             same, destinationPath ${failedFolder}, continueOnError true
    n7 End Loop         loopNodeId "n2"

    n1→n2→n3→n4→n5→n7 pass;  n3→n6 fail;  n4→n6 fail;  n6→n7 pass

Every branch inside the loop ends at the End Loop, failure branches too, and
End Loop's `loopNodeId` is the Loop's id.

## Gotchas

- Folder Selector: `selectionMode "all"` returns a LIST (feed it to a Loop);
  every other mode returns ONE file. Separate several patterns with `|`, `,`
  or `;`; an empty pattern means all files.
- Route a node's failure with a `fail` connection. Do not add a Conditional
  to check whether the previous node worked. `continueOnError: true` makes a
  failed node continue down its PASS path, so its `fail` connection is never
  used: set it only where carrying on is what you want (AI Extract and Excel
  Export then record `{status: "failed", error}` in their outputVariable).
- AI Extract reads PDFs natively, and .docx/.xlsx/.csv/.txt as text. It
  cannot read old .xls or .doc files: convert them first in an AUTOMATION
  (build, dry-run, promote; then call it from an Automation node by
  `automationName` with an `inputs` map). Automations are also the place for
  any logic no node does.
- `${var.field}` reads into objects. Expression Conditionals understand JSON
  `null`, `true` and `false` in substituted values.
- Excel Export reopens the workbook for every row: fine for hundreds of rows
  a run, slow for many thousands. For a large backfill, write to a database
  table (Database node) instead.
- A run is real. Files a test run moved to processed or failed stay there;
  say so, so the user can put test files back before a re-run.
- To CHANGE a workflow: `get_workflow` first (the user may have edited it in
  the Designer), change only what was asked, and save with
  `replace_existing=true` (same name, same id). Saving under a new name makes
  a separate copy.
- Scheduling: you cannot schedule a visual workflow. The user sets a
  schedule on the Workflow Monitor page (Platform menu → Workflow Monitor).

## Saying what you did

Report what the tools returned: the saved name and id, the validation result,
and for a run its status plus what the report shows (files, rows, flags).
Never call a workflow working on the strength of a save alone.
