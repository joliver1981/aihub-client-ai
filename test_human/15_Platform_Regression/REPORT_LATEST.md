# Platform Regression Report — 20260804_092729 (local dev)

- Build: `ff6e695` | Base: `http://localhost:5001` | Baseline: `results_20260803_160811.json`

## Verdict: **CLEAN** — 50 PASS / 9 SKIP / 5 XFAIL / 1 XPASS

## Full matrix (by area)

| area | check | status | evidence |
|---|---|---|---|
| Services | svc_ports | ✅ PASS | host=localhost; required down=none; not-listening (may run in-process on an install): none — INFORMATIONAL, capability is asserted by the functional checks |
| Auth | auth_bad_password | ✅ PASS | landed=http://localhost:5001/login, rejection-flash=True |
| Auth | auth_anonymous_gate | ✅ PASS | landed=http://localhost:5001/login?next=%2Fusers http=200 |
| Pages | pages_render | ✅ PASS | 33/33 ok |
| Agents | agent_crud | ✅ PASS | id=897, listed=True, deleted=True |
| Agents | agent_chat_math | ✅ PASS | http=200, contains-75=True, tail=with just the number.", "role": "user"}, {"content": "75", "role": "assistant"}], "response": "75", "status": "success"} |
| Agents | agent_artifact_csv | ✅ PASS | http=200, file=yes, content='a,b\n1,2' |
| Knowledge/Docs | knowledge_ingest_delete | ✅ PASS | agent=898, ingest=success, chars=2449, type=policy_document, deleted=True |
| Data/NLQ | nlq_data_chat | ✅ PASS | http=200, contains-15=True, tail=total.", "metadata": {}, "type": "text"}], "type": "rich_content"}, "rich_content_enabled": true, "special_message": ""} |
| Knowledge/Docs | documents_api | ✅ PASS | http=200, documents=20 |
| Automations | automation_lifecycle | ✅ PASS | v=1, dry-run=success, promoted=1, deleted-http=200 |
| Automations | automation_verify_honesty | ✅ PASS | liar-run status=failed (must NOT be success), exit0-but-caught=True |
| Code Flows | codeflows_registry | ✅ PASS | http=200, flows=6 (count informational — fresh installs legitimately have 0) |
| Portal WF | portal_wf_persist | ✅ PASS | save=200, slug=regp_portal_temp, listed=True, dup=409 (want 409), del=200 |
| Approvals | approvals_api | ✅ PASS | http=200, pending=231 |
| Scheduler | scheduler_jobs | ✅ PASS | backend=200 {'backend': 'APScheduler', 'status': 'success', 'use_apsched, jobs-http=200, jobs=4 |
| Secrets | secrets_list | ✅ PASS | secrets=151, has-test-secret=True |
| Users/Groups | users_groups | ✅ PASS | users=15, admin-role=3 |
| MCP | mcp_servers_api | ✅ PASS | http=200, servers=4, gw-port=True |
| Command Center | cc_service | ✅ PASS | cc-http=200, token-http=200, token=True |
| Builder | builder_service | ✅ PASS | http=200 |
| Users/Groups | users_role1_authz | ✅ PASS | login=True, blocked={'users_page': True, 'save_workflow': True, 'automations_create': True}, user-deleted=True |
| Users/Groups | user_file_isolation | ⏭ SKIP | SKIP: owner download not available to probe against (admin http=404) |
| Security | sec_approvals_get_unauth | ⚠️ XFAIL | anonymous GET -> http=200 (must be 302/401/403) |
| Security | sec_approvals_decide_unauth | ⚠️ XFAIL | anonymous POST -> http=500 (must be 302/401/403; 404 means the request REACHED business logic unauthenticated) |
| Security | sec_role1_can_create_agents | ⚠️ XFAIL | role-1 POST /add/agent -> http=200; agent actually created=True (must be blocked, nothing created) |
| Connections | conn_create_and_list | ✅ PASS | id=454, server=10.0.0.6, db=ERPDB, user=ai_user |
| Connections | conn_test_endpoint_good | ✅ PASS | http=200, body={"message": "Connection successful", "status": "success"} |
| Connections | conn_test_endpoint_bad_creds | ✅ PASS | http=200, claimed-success=False (must be False), body={"message": "ODBC Error: ('28000', \"[28000] [Microsoft][ODBC Driver 17 for SQL Server][SQL Server]Login failed for user 'ai_user'. (18456)  |
| Connections | conn_execute_scalar | ✅ PASS | http=200, body={"response": "{\n  \"status\": \"success\",\n  \"columns\": [\n    \"answer\"\n  ],\n  \"rows\": [\n    [\n      \"42\"\n    ]\n  ]\n}", "st |
| Connections | conn_execute_real_table | ✅ PASS | invoices=57 (want >=17, original oracle 17); status=success, columns=['n'] |
| Connections | conn_edit_preserves_password | ✅ PASS | query-before=True, update-http=200, query-after=True, rename-persisted=True |
| Connections | conn_edit_changes_field | ✅ PASS | parameters='Connect Timeout=25;', still-queries=True |
| Connections | conn_password_masked_in_list | ✅ PASS | plaintext-password-in-list=False (must be False); password field='••••••••' |
| Connections | conn_delete_removes | ✅ PASS | removed-from-list=True, post-delete query http=404 refuses=True |
| Connections | conn_unreachable_server_honest | ✅ PASS | http=200, honest-error=True, falsely-returned-data=False |
| Connections | comp_conn_unicode | ✅ PASS | decoded={"status": "success", "columns": ["u"], "rows": [["café-中文-ñ"]]} |
| Connections | comp_conn_nulls | ✅ PASS | returned={"response": "{\n  \"status\": \"success\",\n  \"columns\": [\n    \"a\",\n    \"b\"\n  ],\n  \"rows\": [\n    [\n      \"None\",\n      \"x\"\n    ]\n  ]\n}",  |
| Connections | comp_conn_leading_zeros | ✅ PASS | expect '007' preserved; returned={"response": "{\n  \"status\": \"success\",\n  \"columns\": [\n    \"code\"\n  ],\n  \"rows\": [\n    [\n      \"007\"\n    ]\n  ]\n}", "sta |
| Connections | comp_conn_decimal_precision | ✅ PASS | expect 1234.5678; returned={"response": "{\n  \"status\": \"success\",\n  \"columns\": [\n    \"d\"\n  ],\n  \"rows\": [\n    [\n      \"1234.5678\"\n    ]\n  ]\n}", " |
| Connections | comp_conn_datetime | ✅ PASS | returned={"response": "{\n  \"status\": \"success\",\n  \"columns\": [\n    \"d\"\n  ],\n  \"rows\": [\n    [\n      \"2026-03-04 05:06:07\"\n    ]\n  ]\n}", "status": " |
| Connections | comp_conn_empty_result | ✅ PASS | http=200, looks-like-error=False, body={"response": "{\n  \"status\": \"success\",\n  \"columns\": [\n    \"invoice_id\",\n    \"invoice_date\",\n    \"due_date\",\n    \"customer |
| Connections | comp_conn_malformed_sql | ✅ PASS | http=200, names-the-real-cause=True, body={"response": "{\n  \"status\": \"error\",\n  \"error\": \"('42S02', \\\"[42S02] [Microsoft][ODBC Driver 17 for SQL Server][SQL Server]Invalid object name 'dbo.this_table_ |
| Connections | comp_conn_large_result | ✅ PASS | http=200, payload~154016 chars, truncation-wording-present=False |
| Connections | comp_conn_non_select_write | ⚠️ XFAIL | http=200, write-refused=False (False = the endpoint EXECUTES writes), body={"response": "{\n  \"status\": \"success\",\n  \"columns\": \"\",\n  \"rows\": \"\"\n}", "status": "success"} |
| Connections | comp_conn_concurrent | ✅ PASS | 5 parallel queries correct=5/5 |
| Pages | comp_pages_no_error_leakage | ✅ PASS | pages=33, rendering an error=0 |
| Agents | comp_agent_admits_unknown | ✅ PASS | admits-it-does-not-know=True; reply='## 🔎 Result\n\nI don’t have access to your organization’s internal project registry or naming system, so I can’t identify what proje' |
| Knowledge/Docs | comp_knowledge_retrievable_after_ingest | ✅ PASS | agent=901, kid=1717, marker=ZEPHYR092729, retrieved-the-ingested-fact=True |
| Knowledge/Docs | comp_knowledge_deleted_not_retrievable | 🟡 XPASS | deleted kid=1717; deleted content STILL retrievable=False (must be False) |
| Automations | comp_automation_exception_is_failure | ✅ PASS | raising script -> status='failed' (must NOT be success) |
| Automations | comp_automation_partial_output_caught | ✅ PASS | 1 row written, manifest requires 5 -> status='failed' (must NOT be success) |
| Portal WF | comp_portal_step_roundtrip_fidelity | ✅ PASS | saved 6 steps, read back 6; types-match=True; special-chars-intact=True; unicode-url-intact=True |
| Secrets | comp_secret_lifecycle_and_masking | ✅ PASS | created+listed=True; PLAINTEXT LEAKED IN=none; delete-http=200; removed=True |
| Users/Groups | comp_password_change_invalidates_old | ✅ PASS | old-password-worked-before=True; new-works=True; OLD STILL WORKS=False (must be False) |
| MCP | comp_mcp_tools_enumerate | ⚠️ XFAIL | enabled=4; healthy=['30(EveriAI Graph — St):4', '29(Microsoft Learn (T):3']; empty=none; UNREACHABLE=['1(AI Hub Test MCP Se):http500', '5(Test MCP Server):http500'] |
| Data/NLQ | comp_nlq_admits_unanswerable | ✅ PASS | fabricated-London-foot-traffic=False; reply='I couldn’t find a London store in the current location data. The available stores are all in U.S. cities such as New York, Los Ang' |
| Workflow engine | wf14 | ⏭ SKIP | --skip-wf14 |
| Email | email_inbound | ⏭ SKIP | not automated: inbound email pipeline needs a mail fixture/sink (owner decision: no email automation) |
| Integrations | integrations_api | ⏭ SKIP | not automated: API is internal-token only; the page render is covered under Pages |
| Compliance | compliance_pipeline | ⏭ SKIP | not automated: needs a retailer document set; page render covered under Pages |
| Solutions | solutions_install | ⏭ SKIP | not automated: installing a bundle mutates shared tenant assets; gallery page covered under Pages |
| Environments | environments_provision | ⏭ SKIP | not automated: conda env provisioning is minutes-slow; page render covered under Pages |
| Data/NLQ | data_explorer_battery | ⏭ SKIP | not automated: deep NLQ competency lives in pack 12 (battery.py); one live probe runs here |
| Documents | document_qa_battery | ⏭ SKIP | not automated: deep doc-QA competency lives in pack 13; ingest+API-list run here |
