# Platform Regression Report — 20260730_213444

- Build: `cc0427c` | Base: `http://localhost:5001` | Baseline: `none (first run)`

## Verdict: **CLEAN** — 38 PASS / 18 SKIP / 2 XFAIL

## Full matrix (by area)

| area | check | status | evidence |
|---|---|---|---|
| Services | svc_ports | ✅ PASS | down=none of 7 |
| Auth | auth_bad_password | ✅ PASS | landed=http://localhost:5001/login |
| Auth | auth_anonymous_gate | ✅ PASS | landed=http://localhost:5001/login?next=%2Fusers http=200 |
| Pages | pages_render | ✅ PASS | 33/33 ok |
| Agents | agent_crud | ✅ PASS | id=839, listed=True, deleted=True |
| Agents | agent_chat_math | ✅ PASS | http=200, contains-75=True, tail=with just the number.", "role": "user"}, {"content": "75", "role": "assistant"}], "response": "75", "status": "success"} |
| Agents | agent_artifact_csv | ✅ PASS | http=200, file=yes, content='a,b\n1,2' |
| Knowledge/Docs | knowledge_ingest_delete | ✅ PASS | ingest=success, chars=2449, type=vendor_payment_terms_policy, deleted=True |
| Connections | connection_crud_query | ✅ PASS | id=230, query-42=True, deleted=True (del http=200) |
| Data/NLQ | nlq_data_chat | ✅ PASS | http=200, contains-15=True, tail=total.", "metadata": {}, "type": "text"}], "type": "rich_content"}, "rich_content_enabled": true, "special_message": ""} |
| Knowledge/Docs | documents_api | ✅ PASS | http=200, documents=20 |
| Automations | automation_lifecycle | ✅ PASS | v=1, dry-run=success, promoted=1, deleted-http=200 |
| Automations | automation_verify_honesty | ✅ PASS | liar-run status=failed (must NOT be success), exit0-but-caught=True |
| Code Flows | codeflows_registry | ✅ PASS | http=200, flows=6 |
| Portal WF | portal_wf_persist | ✅ PASS | save=200, slug=regp_portal_temp, listed=True, dup=409 (want 409), del=200 |
| Approvals | approvals_api | ✅ PASS | http=200, pending=136 |
| Scheduler | scheduler_jobs | ✅ PASS | backend=200 {'backend': 'APScheduler', 'status': 'success', 'use_apsched, jobs-http=200, jobs=4 |
| Secrets | secrets_list | ✅ PASS | secrets=146, has-test-secret=True |
| Users/Groups | users_groups | ✅ PASS | users=14, admin-role=3 |
| MCP | mcp_servers_api | ✅ PASS | http=200, servers=4, gw-port=True |
| Command Center | cc_service | ✅ PASS | cc-http=200, token-http=200, token=True |
| Builder | builder_service | ✅ PASS | http=200 |
| Workflow engine | wf14:setvar_file_write | ✅ PASS | status=completed; file='value=hello-nodereg' |
| Workflow engine | wf14:file_write_append | ✅ PASS | status=completed; lines=['line1', 'line2'] |
| Workflow engine | wf14:file_check_delete | ✅ PASS | status=completed; deleted=True; steps=File:Completed,File:Completed,File:Completed |
| Workflow engine | wf14:conditional_true | ✅ PASS | status=completed; TRUE-file=True; FALSE-file=False |
| Workflow engine | wf14:conditional_false | ✅ PASS | status=completed; TRUE-file=False; FALSE-file=True |
| Workflow engine | wf14:loop_list_append | ✅ PASS | status=completed; lines=['alpha', 'beta', 'gamma']; continuation-ran=True |
| Workflow engine | wf14:setvar_expression_eval | ✅ PASS | status=completed; file='n=21' (oracle 'n=21') |
| Workflow engine | wf14:setvar_expression_failure_honesty | ⚠️ XFAIL | status=completed; literal-leaked=True; file='\'\'.join([f"{row[\'x\']}" for row in nonexistent_var])' \| log-tail: Executing node: bad calc (Set Variable) ~ Workflow execution started: NODEREG-setvar_e |
| Workflow engine | wf14:database_select_vars | ✅ PASS | status=completed; dbrows type=dict; rows=10 (oracle 10) |
| Workflow engine | wf14:database_fail_edge | ✅ PASS | status=completed; fail-edge-file=True; pass-edge-file=False |
| Workflow engine | wf14:setvar_to_excel | ✅ PASS | status=completed; xlsx rows=[{'store': 'Manhattan', 'units': 1000, 'revenue': 30000}, {'store': 'Brooklyn', 'units': 770, 'revenue': 23100}] |
| Workflow engine | wf14:database_to_excel | ⚠️ XFAIL | status=failed; xlsx-rows=None (oracle 10x headcount=8) \| log-tail: Executing node: headcount (Database) ~ Workflow execution started: NODEREG-database_to_excel |
| Workflow engine | wf14:human_approval_approve | ✅ PASS | status=completed; decided=True (req=B2D53946-F6E8-4738-883C-5A40921A4686, http=200); post-approval file=True |
| Workflow engine | wf14:human_approval_reject | ✅ PASS | status=completed; decided=True; downstream-file=False (must be False) |
| Workflow engine | wf14:folder_selector_count | ✅ PASS | status=completed; files-found=3 (oracle 3) |
| Workflow engine | wf14:portal_node_run | ✅ PASS | status=completed; portal-status=ok; files=0 |
| Workflow engine | wf14:file_transfer_sftp_upload | ✅ PASS | status=completed; remote-file=True (xfer_20260730_213531.txt); content-ok=True |
| Workflow engine | wf14:alert_email | ⏭ SKIP | not yet automated: excluded by owner decision (james 2026-07-30) — do NOT automate (sends real email) |
| Workflow engine | wf14:ai_extract | ⏭ SKIP | not yet automated: excluded by owner decision (james 2026-07-30) — do NOT automate (live LLM cost) |
| Workflow engine | wf14:ai_action | ⏭ SKIP | not yet automated: excluded by owner decision (james 2026-07-30) — do NOT automate (live LLM cost) |
| Workflow engine | wf14:document_node | ⏭ SKIP | not yet automated: not automated (needs a document-pipeline fixture) |
| Workflow engine | wf14:excel_update | ⏭ SKIP | not yet automated: not automated (needs a template .xlsx fixture) |
| Workflow engine | wf14:execute_application | ⏭ SKIP | not yet automated: not automated (needs a harmless fixture app to run) |
| Workflow engine | wf14:integration_node | ⏭ SKIP | not yet automated: not automated (needs a configured integration instance) |
| Workflow engine | wf14:compliance_process | ⏭ SKIP | not yet automated: not automated (needs a retailer document set) |
| Workflow engine | wf14:compliance_excel_export | ⏭ SKIP | not yet automated: not automated (needs compliance fixtures) |
| Workflow engine | wf14:automation_node | ⏭ SKIP | not yet automated: not automated (needs a promoted automation) |
| Workflow engine | wf14:code_step | ⏭ SKIP | not yet automated: not automated (needs a saved code flow) |
| Workflow engine | wf14:baseline | ✅ PASS | pack-14 exit=0 (0=clean, 1=failures, 2=regressions) |
| Email | email_inbound | ⏭ SKIP | not automated: inbound email pipeline needs a mail fixture/sink (owner decision: no email automation) |
| Integrations | integrations_api | ⏭ SKIP | not automated: API is internal-token only; the page render is covered under Pages |
| Compliance | compliance_pipeline | ⏭ SKIP | not automated: needs a retailer document set; page render covered under Pages |
| Solutions | solutions_install | ⏭ SKIP | not automated: installing a bundle mutates shared tenant assets; gallery page covered under Pages |
| Environments | environments_provision | ⏭ SKIP | not automated: conda env provisioning is minutes-slow; page render covered under Pages |
| Data/NLQ | data_explorer_battery | ⏭ SKIP | not automated: deep NLQ competency lives in pack 12 (battery.py); one live probe runs here |
| Documents | document_qa_battery | ⏭ SKIP | not automated: deep doc-QA competency lives in pack 13; ingest+API-list run here |
