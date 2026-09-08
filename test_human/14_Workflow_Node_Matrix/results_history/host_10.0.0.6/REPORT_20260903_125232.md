# Workflow Node Regression Report — 20260903_125232

- Build: `6b36d57` | Base: `http://10.0.0.6:5001` | Tier <= 2 | Baseline: `results_20260902_205806.json`
- Outputs: `C:\temp\aihub_test\nodereg\20260903_125232`

## Verdict: **CLEAN** — 2 PASS / 36 SKIP

## Full matrix

| check | tier | status | evidence |
|---|---|---|---|
| setvar_file_write | 1 | ⏭ SKIP | remote mode: engine-box disk not reachable via //10.0.0.6/c$ |
| file_write_append | 1 | ⏭ SKIP | remote mode: engine-box disk not reachable via //10.0.0.6/c$ |
| file_check_delete | 1 | ⏭ SKIP | remote mode: engine-box disk not reachable via //10.0.0.6/c$ |
| conditional_true | 1 | ⏭ SKIP | remote mode: engine-box disk not reachable via //10.0.0.6/c$ |
| conditional_false | 1 | ⏭ SKIP | remote mode: engine-box disk not reachable via //10.0.0.6/c$ |
| loop_list_append | 1 | ⏭ SKIP | remote mode: engine-box disk not reachable via //10.0.0.6/c$ |
| setvar_expression_eval | 1 | ⏭ SKIP | remote mode: engine-box disk not reachable via //10.0.0.6/c$ |
| setvar_expression_failure_honesty | 1 | ⏭ SKIP | remote mode: engine-box disk not reachable via //10.0.0.6/c$ |
| database_select_vars | 2 | ✅ PASS | status=completed; dbrows type=dict; rows=10 (oracle 10) |
| database_fail_edge | 2 | ⏭ SKIP | remote mode: engine-box disk not reachable via //10.0.0.6/c$ |
| setvar_to_excel | 2 | ⏭ SKIP | remote mode: engine-box disk not reachable via //10.0.0.6/c$ |
| database_to_excel | 2 | ⏭ SKIP | remote mode: engine-box disk not reachable via //10.0.0.6/c$ |
| human_approval_approve | 2 | ⏭ SKIP | remote mode: engine-box disk not reachable via //10.0.0.6/c$ |
| human_approval_reject | 2 | ⏭ SKIP | remote mode: engine-box disk not reachable via //10.0.0.6/c$ |
| folder_selector_count | 2 | ⏭ SKIP | remote mode: engine-box disk not reachable via //10.0.0.6/c$ |
| portal_node_run | 2 | ✅ PASS | status=completed; portal-status=ok; files=0 |
| file_transfer_sftp_upload | 2 | ⏭ SKIP | env missing: ['sftp'] — SFTP rig not reachable at 10.0.0.7:2222 (the engine box dials back here) — start it with Start_SFTP_Server_LAN.bat and allow TCP 2222/2121 inbound |
| comp_midchain_failure_honesty | 3 | ⏭ SKIP | tier 3 > --tier 2 |
| comp_real_error_text_propagates | 3 | ⏭ SKIP | tier 3 > --tier 2 |
| comp_variable_survives_long_chain | 3 | ⏭ SKIP | tier 3 > --tier 2 |
| comp_loop_zero_items | 3 | ⏭ SKIP | tier 3 > --tier 2 |
| comp_loop_single_item | 3 | ⏭ SKIP | tier 3 > --tier 2 |
| comp_conditional_boundary | 3 | ⏭ SKIP | tier 3 > --tier 2 |
| comp_type_fidelity_db_to_excel | 3 | ⏭ SKIP | tier 3 > --tier 2 |
| comp_unicode_through_chain | 3 | ⏭ SKIP | tier 3 > --tier 2 |
| comp_large_result_no_truncation | 3 | ⏭ SKIP | tier 3 > --tier 2 |
| comp_excel_export_throughput | 3 | ⏭ SKIP | tier 3 > --tier 2 |
| alert_email | 0 | ⏭ SKIP | not yet automated: excluded by owner decision (james 2026-07-30) — do NOT automate (sends real email) |
| ai_extract | 0 | ⏭ SKIP | not yet automated: excluded by owner decision (james 2026-07-30) — do NOT automate (live LLM cost) |
| ai_action | 0 | ⏭ SKIP | not yet automated: excluded by owner decision (james 2026-07-30) — do NOT automate (live LLM cost) |
| document_node | 0 | ⏭ SKIP | not yet automated: not automated (needs a document-pipeline fixture) |
| excel_update | 0 | ⏭ SKIP | not yet automated: not automated (needs a template .xlsx fixture) |
| execute_application | 0 | ⏭ SKIP | not yet automated: not automated (needs a harmless fixture app to run) |
| integration_node | 0 | ⏭ SKIP | not yet automated: not automated (needs a configured integration instance) |
| compliance_process | 0 | ⏭ SKIP | not yet automated: not automated (needs a retailer document set) |
| compliance_excel_export | 0 | ⏭ SKIP | not yet automated: not automated (needs compliance fixtures) |
| automation_node | 0 | ⏭ SKIP | not yet automated: not automated (needs a promoted automation) |
| code_step | 0 | ⏭ SKIP | not yet automated: not automated (needs a saved code flow) |

## Node-type coverage map (all 21 engine node types)

| node type | covered by |
|---|---|
| Database | database_select_vars:PASS |
| Folder Selector | ⏭ check exists, SKIPPED this run: folder_selector_count |
| Document | 📋 planned (not automated (needs a document-pipeline fixture)) |
| AI Action | 📋 planned (excluded by owner decision (james 2026-07-30) — do NOT automate (live LLM cost)) |
| Set Variable | ⏭ check exists, SKIPPED this run: setvar_file_write, conditional_true, conditional_false, loop_list_append, setvar_expression_eval, setvar_expression_failure_honesty, setvar_to_excel, human_approval_approve, human_approval_reject |
| Alert | 📋 planned (excluded by owner decision (james 2026-07-30) — do NOT automate (sends real email)) |
| Conditional | ⏭ check exists, SKIPPED this run: conditional_true, conditional_false |
| Loop | ⏭ check exists, SKIPPED this run: loop_list_append |
| End Loop | ⏭ check exists, SKIPPED this run: loop_list_append |
| Execute Application | 📋 planned (not automated (needs a harmless fixture app to run)) |
| File | ⏭ check exists, SKIPPED this run: setvar_file_write, file_write_append, file_check_delete, conditional_true, conditional_false, loop_list_append, setvar_expression_eval, setvar_expression_failure_honesty, database_fail_edge, human_approval_approve, human_approval_reject, file_transfer_sftp_upload |
| AI Extract | 📋 planned (excluded by owner decision (james 2026-07-30) — do NOT automate (live LLM cost)) |
| Excel Export | ⏭ check exists, SKIPPED this run: setvar_to_excel, database_to_excel |
| Integration | 📋 planned (not automated (needs a configured integration instance)) |
| Compliance Process | 📋 planned (not automated (needs a retailer document set)) |
| Compliance Excel Export | 📋 planned (not automated (needs compliance fixtures)) |
| Automation | 📋 planned (not automated (needs a promoted automation)) |
| Code Step | 📋 planned (not automated (needs a saved code flow)) |
| File Transfer | ⏭ check exists, SKIPPED this run: file_transfer_sftp_upload |
| Portal | portal_node_run:PASS |
| Human Approval | ⏭ check exists, SKIPPED this run: human_approval_approve, human_approval_reject |

## Config lint (informational) — scanned ALL 11 persisted workflows

- Excel Export nodes with broken config: **5**
  - wf 2 'Customer Onboarding - AI Guided v4 _w Bulk Update_': Excel Export invalid excelOperation='update'
  - wf 1002 'Customer Onboarding - Horizon Replica _w SP v2_ (Imported) (Imported)': Excel Export invalid excelOperation='update'
  - wf 1003 'Customer Onboarding - Horizon Replica _w SP v2_ _Imported_': Excel Export invalid excelOperation='update'
  - wf 1004 'Customer Onboarding - Horizon Replica _w SP v2_ _Imported__2': Excel Export invalid excelOperation='update'
  - wf 1005 'Customer Onboarding - Horizon Replica _w SP v2_ _Imported_GOOD_': Excel Export invalid excelOperation='update'
- Unknown node types: **0**
- Dead edge types (engine follows only pass/fail/complete): **0**

## XFAIL registry (known bugs the matrix tracks)

- **setvar_expression_failure_honesty** — Engine Fix-3 backlog (found 2026-07-30, wf 1337): when expression evaluation fails (e.g. f-string comprehension over a DB envelope), the engine silently stores the LITERAL source text and the step 'Completes' — dishonest fallback
- **comp_type_fidelity_db_to_excel** — FOUND 2026-08-02: a SQL NULL arrives in the spreadsheet as the literal four-character text 'None' - the user sees the word None in the cell instead of an empty one, and any downstream SUM/filter treats it as data. Related but milder: Excel Export writes EVERY value as a text cell (data_type 's'), so numbers cannot be summed and dates cannot be sorted without the user converting the column first. Values themselves are correct - '007' really is preserved. OWNER DECISION PENDING.
