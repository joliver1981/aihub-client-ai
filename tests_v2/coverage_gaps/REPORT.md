# Coverage Gap Report

Static analysis of production surfaces vs. the test corpus.

## Headline numbers

- Total routes discovered: **918**
- Routes with at least one test reference: **462**
- **Untested routes: 456**
- Route coverage: **50.3%**

- Total env-var references: **1066**
- Distinct env vars: **329**
- **Untested env vars: 225**

- Role-decorator usages:
  - `@admin_required`: 30
  - `@api_key_or_session_required`: 199
  - `@developer_required`: 69
  - `@login_required`: 174
  - `@role_required`: 0

## Files with the most untested routes

- `app.py` — 136 untested
- `builder_service/routes/admin.py` — 29 untested
- `local_history_routes.py` — 17 untested
- `command_center_service/routes/memory.py` — 15 untested
- `model_tester/app.py` — 15 untested
- `app_doc_api.py` — 13 untested
- `solution_builder_routes.py` — 12 untested
- `api_keys_config.py` — 11 untested
- `agent_email_routes.py` — 10 untested
- `caution_system.py` — 10 untested
- `document_summarization_wrapper_routes.py` — 9 untested
- `onboarding_routes.py` — 9 untested
- `workflow_builder_routes.py` — 9 untested
- `local_secrets_routes.py` — 8 untested
- `builder_oracle/predictive_forecast/routes/api.py` — 8 untested

## Untested routes (sorted by risk score)

| Method | Path | Function | File:Line | Risk |
| --- | --- | --- | --- | --- |
| POST | `/api/admin/actions` | `create_action` | `builder_service/routes/admin.py:451` | 11 |
| PUT | `/api/admin/actions/{capability_id:path}` | `update_action` | `builder_service/routes/admin.py:483` | 11 |
| DELETE | `/api/admin/actions/{capability_id:path}` | `delete_action` | `builder_service/routes/admin.py:515` | 11 |
| POST | `/api/admin/actions/{capability_id:path}/fields` | `add_input_field` | `builder_service/routes/admin.py:539` | 11 |
| PUT | `/api/admin/actions/{capability_id:path}/fields/{field_name}` | `update_input_field` | `builder_service/routes/admin.py:561` | 11 |
| DELETE | `/api/admin/actions/{capability_id:path}/fields/{field_name}` | `delete_input_field` | `builder_service/routes/admin.py:583` | 11 |
| POST | `/api/admin/actions/{capability_id:path}/mappings` | `add_response_mapping` | `builder_service/routes/admin.py:607` | 11 |
| PUT | `/api/admin/actions/{capability_id:path}/mappings/{output_name}` | `update_response_mapping` | `builder_service/routes/admin.py:629` | 11 |
| DELETE | `/api/admin/actions/{capability_id:path}/mappings/{output_name}` | `delete_response_mapping` | `builder_service/routes/admin.py:651` | 11 |
| POST | `/api/admin/agents` | `create_agent` | `builder_service/routes/admin.py:821` | 11 |
| PUT | `/api/admin/agents/{agent_id}` | `update_agent` | `builder_service/routes/admin.py:850` | 11 |
| DELETE | `/api/admin/agents/{agent_id}` | `delete_agent` | `builder_service/routes/admin.py:878` | 11 |
| POST | `/api/admin/agents/{agent_id}/test` | `test_agent_connection` | `builder_service/routes/admin.py:778` | 11 |
| POST | `/api/admin/config` | `save_config_values` | `builder_service/routes/admin.py:183` | 11 |
| POST | `/api/admin/domains` | `create_domain` | `builder_service/routes/admin.py:259` | 11 |
| PUT | `/api/admin/domains/{domain_id}` | `update_domain` | `builder_service/routes/admin.py:285` | 11 |
| DELETE | `/api/admin/domains/{domain_id}` | `delete_domain` | `builder_service/routes/admin.py:312` | 11 |
| POST | `/api/admin/domains/{domain_id}/capabilities` | `create_capability` | `builder_service/routes/admin.py:336` | 11 |
| PUT | `/api/admin/domains/{domain_id}/capabilities/{capability_id}` | `update_capability` | `builder_service/routes/admin.py:363` | 11 |
| DELETE | `/api/admin/domains/{domain_id}/capabilities/{capability_id}` | `delete_capability` | `builder_service/routes/admin.py:385` | 11 |
| POST | `/api/scheduler/execute_document_job/{job_id}` | `execute_document_job_api` | `app.py:6451` | 9 |
| POST | `/chat/data/reset` | `reset_data_chat` | `app.py:11699` | 9 |
| POST | `/delete/collection` | `delete_collection` | `app.py:3329` | 9 |
| DELETE | `/delete/column` | `delete_column` | `app.py:5199` | 9 |
| POST | `/delete/quickjob` | `delete_quick_job` | `app.py:3973` | 9 |
| DELETE | `/delete/table` | `delete_table` | `app.py:4759` | 9 |
| DELETE | `/delete/table_columns` | `delete_table_columns` | `app.py:5227` | 9 |
| DELETE | `/delete/workflow/category/{category_id}` | `delete_workflow_category` | `app.py:11654` | 9 |
| POST | `/document/delete/{document_id}` | `delete_document` | `app.py:12349` | 9 |
| POST | `/workflow/file/delete` | `delete_file` | `app.py:8985` | 9 |
| POST | `/api/pipelines/{pipeline_id}/execute` | `execute_pipeline` | `builder_data/routes/pipelines.py:73` | 9 |
| POST | `/api/run` | `api_run` | `model_tester/app.py:337` | 9 |
| POST | `/preferences/admin/synchronize` | `admin_synchronize_preferences` | `preferences_routes.py:391` | 9 |
| POST | `/data_explorer/reset` | `data_explorer_reset` | `routes/data_explorer.py:305` | 9 |
| POST | `/api/agent/communication/test` | `test_agent_communication` | `agent_communication_routes.py:187` | 7 |
| POST | `/api/agent/workflows` | `create_agent_workflow` | `agent_communication_routes.py:148` | 7 |
| POST | `/api/agent-email/mark-read/{agent_id}` | `mark_agent_emails_read` | `agent_email_routes.py:759` | 7 |
| POST | `/api/agent-email/reply/{agent_id}` | `send_reply` | `agent_email_routes.py:798` | 7 |
| POST | `/api/agent-email/send-test/{agent_id}` | `send_test_email` | `agent_email_routes.py:842` | 7 |
| POST | `/api/workflow/ai-extract/preview-schema` | `preview_schema` | `ai_extract_routes.py:178` | 7 |
| POST | `/api/workflow/ai-extract/test` | `test_extraction` | `ai_extract_routes.py:40` | 7 |
| POST | `/api/workflow/ai-extract/validate-field-name` | `validate_field_name_api` | `ai_extract_routes.py:140` | 7 |
| POST | `/api/api-keys/anthropic` | `save_anthropic_key` | `api_keys_config.py:672` | 7 |
| DELETE | `/api/api-keys/anthropic` | `delete_anthropic_key` | `api_keys_config.py:731` | 7 |
| POST | `/api/api-keys/anthropic/test` | `test_anthropic` | `api_keys_config.py:775` | 7 |
| POST | `/api/api-keys/model-overrides` | `save_model_overrides` | `api_keys_config.py:816` | 7 |
| DELETE | `/api/api-keys/model-overrides` | `delete_model_overrides` | `api_keys_config.py:850` | 7 |
| POST | `/api/api-keys/openai` | `save_openai_key` | `api_keys_config.py:635` | 7 |
| DELETE | `/api/api-keys/openai` | `delete_openai_key` | `api_keys_config.py:707` | 7 |
| POST | `/api/api-keys/openai/test` | `test_openai` | `api_keys_config.py:754` | 7 |
| POST | `/api/api-keys/toggle` | `toggle_byok` | `api_keys_config.py:611` | 7 |
| POST | `/api/ai/analyze-tables-batch-legacy` | `ai_analyze_tables_batch_api_legacy` | `app.py:15396` | 7 |
| DELETE | `/api/ai/cleanup/{task_id}` | `cleanup_ai_analysis_task` | `app.py:15378` | 7 |
| POST | `/api/cc-generate-token` | `cc_generate_token` | `app.py:1855` | 7 |
| POST | `/api/column/update` | `update_column` | `app.py:14980` | 7 |
| DELETE | `/api/column/{column_id}` | `delete_column_new` | `app.py:15729` | 7 |
| POST | `/api/conversations/create` | `create_conversation_endpoint` | `app.py:4584` | 7 |
| POST | `/api/document/preflight` | `document_preflight` | `app.py:11269` | 7 |
| PUT | `/api/documents/bulk-update` | `api_bulk_update_documents` | `app.py:12608` | 7 |
| POST | `/api/internal/document-search` | `internal_document_search` | `app.py:5090` | 7 |
| POST | `/api/mcp/servers_v1` | `create_mcp_server_v1` | `app.py:15970` | 7 |
| POST | `/api/mcp/test_v1` | `test_mcp_server_v1` | `app.py:15921` | 7 |
| POST | `/api/search-documents-by-attributes` | `api_search_documents_by_attributes` | `app.py:8500` | 7 |
| POST | `/api/search-documents-hybrid` | `api_search_documents_hybrid` | `app.py:8636` | 7 |
| POST | `/api/send_email` | `api_send_email` | `app.py:12228` | 7 |
| POST | `/api/settings/db-logging` | `set_db_logging_setting` | `app.py:17000` | 7 |
| POST | `/api/table/update` | `update_table` | `app.py:14818` | 7 |
| DELETE | `/api/table/{table_id}` | `delete_table_new` | `app.py:15710` | 7 |
| POST | `/api/tool/dependencies` | `get_tool_dependencies` | `app.py:1988` | 7 |
| POST | `/api/validate-builder-token` | `validate_builder_token` | `app.py:1704` | 7 |
| POST | `/api/validate-cc-token` | `validate_cc_token` | `app.py:1817` | 7 |
| POST | `/api/workflow/approvals/bulk` | `process_bulk_approvals` | `app.py:14656` | 7 |
| POST | `/api/workflow/assistant` | `workflow_assistant_proxy` | `app.py:15754` | 7 |
| DELETE | `/api/workflow/assistant/history` | `clear_conversation_history_proxy` | `app.py:15861` | 7 |
| POST | `/api/workflow/assistant/result` | `workflow_assistant_result_proxy` | `app.py:15809` | 7 |
| POST | `/api/workflow/resolve-ids` | `resolve_workflow_ids_proxy` | `app.py:15783` | 7 |
| POST | `/api/workflow/validate` | `validate_workflow_proxy` | `app.py:15885` | 7 |
| POST | `/import/agent/execute` | `import_agent_execute` | `app.py:13738` | 7 |
| POST | `/api/email-dispatcher/poll-now` | `trigger_email_poll` | `app_executor_service.py:515` | 7 |
| POST | `/api/email-dispatcher/start` | `start_email_dispatcher` | `app_executor_service.py:434` | 7 |
| POST | `/api/email-dispatcher/stop` | `stop_email_dispatcher` | `app_executor_service.py:483` | 7 |
| POST | `/api/workflow/log` | `log_workflow_event` | `app_executor_service.py:384` | 7 |
| POST | `/api/workflow/assistant` | `workflow_assistant` | `app_knowledge_api.py:2820` | 7 |
| DELETE | `/api/workflow/assistant/history` | `clear_conversation_history` | `app_knowledge_api.py:3011` | 7 |
| POST | `/api/workflow/assistant/result` | `workflow_assistant_result` | `app_knowledge_api.py:2919` | 7 |
| POST | `/api/workflow/assistant_legacy` | `workflow_assistant_legacy` | `app_knowledge_api.py:2705` | 7 |
| POST | `/api/workflow/resolve-ids` | `resolve_workflow_ids` | `app_knowledge_api.py:3150` | 7 |
| POST | `/api/workflow/validate` | `validate_workflow` | `app_knowledge_api.py:3050` | 7 |
| POST | `/api/cloud/containers` | `list_containers` | `builder_cloud/gateway/app_cloud_gateway.py:187` | 7 |
| POST | `/api/cloud/download` | `download_object` | `builder_cloud/gateway/app_cloud_gateway.py:232` | 7 |
| POST | `/api/cloud/metadata` | `get_object_metadata` | `builder_cloud/gateway/app_cloud_gateway.py:264` | 7 |
| POST | `/api/cloud/objects` | `list_objects` | `builder_cloud/gateway/app_cloud_gateway.py:198` | 7 |
| POST | `/api/cloud/sas-url` | `generate_sas_url` | `builder_cloud/gateway/app_cloud_gateway.py:275` | 7 |
| POST | `/api/cloud/test` | `test_connection` | `builder_cloud/gateway/app_cloud_gateway.py:177` | 7 |
| POST | `/api/cloud/upload` | `upload_object` | `builder_cloud/gateway/app_cloud_gateway.py:209` | 7 |
| POST | `/api/pipelines/` | `create_pipeline` | `builder_data/routes/pipelines.py:39` | 7 |
| DELETE | `/api/pipelines/{pipeline_id}` | `delete_pipeline` | `builder_data/routes/pipelines.py:172` | 7 |
| POST | `/api/pipelines/{pipeline_id}/preview` | `preview_pipeline` | `builder_data/routes/pipelines.py:145` | 7 |
| POST | `/api/quality/compare` | `compare_data` | `builder_data/routes/quality.py:100` | 7 |
| POST | `/api/quality/deduplicate` | `deduplicate_data` | `builder_data/routes/quality.py:164` | 7 |
| POST | `/api/quality/profile` | `profile_data` | `builder_data/routes/quality.py:192` | 7 |
| POST | `/api/quality/scrub` | `scrub_data` | `builder_data/routes/quality.py:128` | 7 |
| POST | `/api/quality/validate` | `validate_data` | `builder_data/routes/quality.py:219` | 7 |
| POST | `/api/mcp/connect` | `connect_server` | `builder_mcp/gateway/app_mcp_gateway.py:157` | 7 |
| POST | `/api/mcp/disconnect` | `disconnect_server` | `builder_mcp/gateway/app_mcp_gateway.py:182` | 7 |
| POST | `/api/internal/mcp/graph` | `graph_mcp` | `builder_mcp/routes/mcp_internal_routes.py:69` | 7 |
| POST | `/api/my-connections/{server_id}/disconnect` | `disconnect_my_connection` | `builder_mcp/routes/my_connections_routes.py:102` | 7 |
| POST | `/api/analyze` | `analyze` | `builder_oracle/predictive_forecast/routes/api.py:168` | 7 |
| DELETE | `/api/models/{model_name}` | `models_delete` | `builder_oracle/predictive_forecast/routes/api.py:160` | 7 |
| POST | `/api/train` | `train` | `builder_oracle/predictive_forecast/routes/api.py:78` | 7 |
| POST | `/api/builder/documents/search` | `builder_search_documents` | `builder_service/routes/builder_document_routes.py:45` | 7 |
| POST | `/api/sessions/with-user` | `create_session_with_user` | `builder_service/routes/chat.py:451` | 7 |
| POST | `/api/caution/level` | `save_caution_level` | `caution_system.py:403` | 7 |
| POST | `/api/caution/level` | `save_caution_level_disabled` | `caution_system.py:359` | 7 |
| POST | `/api/caution/user` | `set_user_caution_level` | `caution_system.py:445` | 7 |
| POST | `/api/caution/user` | `set_user_caution_level_disabled` | `caution_system.py:374` | 7 |
| POST | `/api/auth/refresh-token` | `refresh_token` | `command_center_service/routes/auth.py:41` | 7 |
| DELETE | `/api/memory/insights` | `delete_all_insights_endpoint` | `command_center_service/routes/memory.py:126` | 7 |
| PUT | `/api/memory/preferences` | `update_preferences` | `command_center_service/routes/memory.py:53` | 7 |
| DELETE | `/api/memory/preferences` | `delete_all_preferences` | `command_center_service/routes/memory.py:60` | 7 |
| DELETE | `/api/memory/preferences/{key:path}` | `delete_preference` | `command_center_service/routes/memory.py:67` | 7 |
| DELETE | `/api/memory/routes` | `delete_all_routes_endpoint` | `command_center_service/routes/memory.py:85` | 7 |
| DELETE | `/api/memory/routes/canonical` | `delete_route_by_canonical` | `command_center_service/routes/memory.py:93` | 7 |
| DELETE | `/api/memory/routes/{route_id}` | `delete_route_endpoint` | `command_center_service/routes/memory.py:101` | 7 |
| DELETE | `/api/memory/suggestions` | `delete_all_suggestions` | `command_center_service/routes/memory.py:27` | 7 |
| DELETE | `/api/memory/suggestions/{route_id}` | `delete_suggestion` | `command_center_service/routes/memory.py:35` | 7 |
| POST | `/api/plugins/{plugin_id}/disable` | `disable_plugin` | `command_center_service/routes/plugins.py:45` | 7 |
| POST | `/api/plugins/{plugin_id}/enable` | `enable_plugin` | `command_center_service/routes/plugins.py:37` | 7 |
| POST | `/api/sessions/clear` | `delete_all_sessions` | `command_center_service/routes/sessions.py:122` | 7 |
| POST | `/api/tools/generated/{tool_name}/disable` | `disable_tool` | `command_center_service/routes/tools.py:33` | 7 |
| POST | `/api/solutions/connections/install` | `install_connection` | `connection_install_routes.py:127` | 7 |
| POST | `/api/tool/analyze_package` | `analyze_tool_package` | `custom_tool_import_routes.py:25` | 7 |
| POST | `/api/tool/export_multiple` | `export_multiple_tools` | `custom_tool_import_routes.py:360` | 7 |
| POST | `/api/tool/import` | `import_custom_tools` | `custom_tool_import_routes.py:154` | 7 |
| POST | `/api/data-collection/message-stream` | `send_message_stream` | `data_collection_agent/routes.py:673` | 7 |
| POST | `/api/pages/{page_id}/summarize` | `summarize_single_page` | `document_summarization_routes.py:222` | 7 |
| POST | `/api/summaries/batch-regenerate` | `batch_regenerate_summaries` | `document_summarization_routes.py:637` | 7 |
| DELETE | `/api/summaries/{summary_id}` | `delete_summary` | `document_summarization_routes.py:575` | 7 |
| POST | `/api/documents/batch-summarize` | `batch_summarize_documents` | `document_summarization_wrapper_routes.py:535` | 7 |
| POST | `/api/pages/{page_id}/summarize` | `proxy_summarize_single_page` | `document_summarization_wrapper_routes.py:179` | 7 |
| POST | `/api/summaries/batch-regenerate` | `proxy_batch_regenerate_summaries` | `document_summarization_wrapper_routes.py:259` | 7 |
| POST | `/api/summaries/config/length-limits` | `proxy_update_summary_length_limits` | `document_summarization_wrapper_routes.py:308` | 7 |
| DELETE | `/api/summaries/{summary_id}` | `proxy_delete_summary` | `document_summarization_wrapper_routes.py:245` | 7 |
| POST | `/api/email-processing/dispatcher/start` | `start_dispatcher` | `email_processing_routes.py:445` | 7 |
| POST | `/api/email-processing/dispatcher/stop` | `stop_dispatcher` | `email_processing_routes.py:478` | 7 |
| POST | `/api/email-processing/retry/{record_id}` | `retry_processing` | `email_processing_routes.py:354` | 7 |
| POST | `/api/assignments/bulk` | `bulk_assign` | `environment_assignment_api_routes.py:228` | 7 |
| POST | `/api/feedback/submit` | `submit_feedback` | `feedback_routes.py:133` | 7 |
| POST | `/api/updater/download` | `api_download_update` | `github_updater.py:762` | 7 |
| POST | `/api/updater/install` | `api_install_update` | `github_updater.py:826` | 7 |
| POST | `/api/updater/open-folder` | `api_open_installer_folder` | `github_updater.py:889` | 7 |
| POST | `/api/solutions/integrations/install` | `install_integration` | `integration_install_routes.py:114` | 7 |
| POST | `/api/integrations/oauth/setup-app-only/{template_key}` | `setup_app_only_integration` | `integration_routes.py:1214` | 7 |
| POST | `/api/integrations/oauth/start/{template_key}` | `start_oauth` | `integration_routes.py:1324` | 7 |
| POST | `/api/integrations/secrets/check` | `check_secrets` | `integration_routes.py:1541` | 7 |
| POST | `/api/integrations/templates/import` | `import_template` | `integration_routes.py:470` | 7 |
| POST | `/api/solutions/knowledge/import` | `import_knowledge` | `knowledge_install_routes.py:49` | 7 |
| POST | `/api/history/agent/{agent_id}/clear` | `clear_agent_history` | `local_history_routes.py:633` | 7 |
| POST | `/api/history/cleanup-empty` | `cleanup_empty_conversations` | `local_history_routes.py:602` | 7 |
| POST | `/api/history/clear` | `clear_history` | `local_history_routes.py:546` | 7 |
| POST | `/api/history/conversations` | `create_conversation` | `local_history_routes.py:185` | 7 |
| PUT | `/api/history/conversations/{conversation_id}` | `update_conversation` | `local_history_routes.py:224` | 7 |
| DELETE | `/api/history/conversations/{conversation_id}` | `delete_conversation` | `local_history_routes.py:263` | 7 |
| POST | `/api/history/conversations/{conversation_id}/messages` | `add_message` | `local_history_routes.py:290` | 7 |
| POST | `/api/history/export` | `export_history` | `local_history_routes.py:517` | 7 |
| POST | `/api/history/prune` | `prune_history` | `local_history_routes.py:571` | 7 |
| PUT | `/api/history/queries/{query_id}` | `update_query` | `local_history_routes.py:392` | 7 |
| DELETE | `/api/history/queries/{query_id}` | `delete_query` | `local_history_routes.py:418` | 7 |
| POST | `/api/local-secrets/export` | `export_template` | `local_secrets_routes.py:391` | 7 |
| POST | `/api/local-secrets/import` | `import_secrets` | `local_secrets_routes.py:423` | 7 |
| POST | `/api/local-secrets/test/{name}` | `test_secret` | `local_secrets_routes.py:514` | 7 |
| DELETE | `/api/local-secrets/{name}` | `delete_secret` | `local_secrets_routes.py:253` | 7 |
| POST | `/api/local-secrets/{name}/verify` | `verify_secret` | `local_secrets_routes.py:289` | 7 |
| POST | `/api/evals` | `api_create_eval` | `model_tester/app.py:301` | 7 |
| PUT | `/api/evals/{eval_id}` | `api_update_eval` | `model_tester/app.py:308` | 7 |
| DELETE | `/api/evals/{eval_id}` | `api_delete_eval` | `model_tester/app.py:316` | 7 |
| POST | `/api/judge` | `api_judge` | `model_tester/app.py:410` | 7 |
| DELETE | `/api/results/{rid}` | `api_delete_result` | `model_tester/app.py:476` | 7 |
| PUT | `/api/settings` | `api_put_settings` | `model_tester/app.py:264` | 7 |
| POST | `/api/onboarding/checklist/data-assistant` | `update_data_assistant_checklist` | `onboarding_routes.py:250` | 7 |
| POST | `/api/onboarding/checklist/data-assistant/activate` | `activate_data_assistant_checklist` | `onboarding_routes.py:325` | 7 |
| POST | `/api/onboarding/checklist/data-assistant/step/{step_name}` | `complete_checklist_step` | `onboarding_routes.py:283` | 7 |
| POST | `/api/onboarding/complete` | `complete` | `onboarding_routes.py:98` | 7 |
| POST | `/api/onboarding/progress` | `update_progress` | `onboarding_routes.py:69` | 7 |
| POST | `/api/onboarding/skip` | `skip` | `onboarding_routes.py:124` | 7 |
| POST | `/api/onboarding/tour/record` | `record_tour` | `onboarding_routes.py:163` | 7 |
| POST | `/preferences/api/update` | `update_user_preference_api` | `preferences_routes.py:267` | 7 |
| POST | `/api/solutions/build` | `build_bundle` | `solution_builder_routes.py:750` | 7 |
| POST | `/api/solutions/build/publish` | `build_and_publish` | `solution_builder_routes.py:776` | 7 |
| POST | `/api/solutions/drafts` | `create_draft` | `solution_builder_routes.py:213` | 7 |
| PUT | `/api/solutions/drafts/{draft_id}` | `update_draft` | `solution_builder_routes.py:233` | 7 |
| DELETE | `/api/solutions/drafts/{draft_id}` | `delete_draft` | `solution_builder_routes.py:252` | 7 |
| POST | `/api/solutions/test_install` | `test_install` | `solution_builder_routes.py:808` | 7 |
| POST | `/api/solutions/validate` | `validate_manifest` | `solution_builder_routes.py:637` | 7 |
| POST | `/settings/api/telemetry/consent` | `update_consent` | `telemetry.py:906` | 7 |
| POST | `/api/transcribe` | `transcribe_audio` | `whisper_routes.py:53` | 7 |
| POST | `/api/workflow/builder/check-mode` | `check_builder_mode` | `workflow_builder_routes.py:370` | 7 |
| POST | `/api/workflow/builder/clear` | `workflow_builder_clear` | `workflow_builder_routes.py:339` | 7 |
| POST | `/api/workflow/builder/compile` | `compile_workflow_endpoint` | `workflow_builder_routes.py:215` | 7 |
| POST | `/api/workflow/builder/finalize-capture` | `finalize_training_capture_endpoint` | `workflow_builder_routes.py:471` | 7 |
| POST | `/api/workflow/builder/guide` | `workflow_builder_guide` | `workflow_builder_routes.py:46` | 7 |
| POST | `/api/workflow/builder/validate` | `validate_workflow_state` | `workflow_builder_routes.py:151` | 7 |
| POST | `/api/solutions/workflows/import` | `import_workflow` | `workflow_export_routes.py:112` | 7 |
| GET | `/admin/tier/api/cache-status` | `get_cache_status` | `admin_tier_usage.py:775` | 6 |
| GET | `/admin/tier/api/stats` | `get_tier_stats` | `admin_tier_usage.py:796` | 6 |
| GET | `/admin/tier/api/subscription-info` | `get_subscription_info_from_cloud` | `admin_tier_usage.py:646` | 6 |
| GET | `/admin/tier/api/users/list` | `get_users_list` | `admin_tier_usage.py:957` | 6 |
| GET | `/api/admin/actions` | `list_actions` | `builder_service/routes/admin.py:411` | 6 |
| GET | `/api/admin/actions/{capability_id:path}` | `get_action` | `builder_service/routes/admin.py:433` | 6 |
| GET | `/api/admin/agents` | `list_agents` | `builder_service/routes/admin.py:710` | 6 |
| GET | `/api/admin/agents/{agent_id}` | `get_agent` | `builder_service/routes/admin.py:743` | 6 |
| GET | `/api/admin/config/{file_key}/{var_name}` | `get_config_value` | `builder_service/routes/admin.py:159` | 6 |
| GET | `/api/admin/domains` | `list_domains` | `builder_service/routes/admin.py:217` | 6 |
| GET | `/api/admin/domains/{domain_id}` | `get_domain` | `builder_service/routes/admin.py:241` | 6 |
| GET | `/api/admin/field-corrections` | `get_field_corrections` | `builder_service/routes/admin.py:675` | 6 |
| GET | `/api/admin/health` | `admin_health` | `builder_service/routes/admin.py:908` | 6 |
| POST | `/add/collection` | `add_update_collection` | `app.py:3448` | 5 |
| POST | `/add/column` | `add_column` | `app.py:5131` | 5 |
| POST | `/add/connection-legacy` | `add_update_connection_legacy` | `app.py:3494` | 5 |
| POST | `/add/job` | `add_job` | `app.py:4012` | 5 |
| POST | `/add/quickjob` | `add_quick_job` | `app.py:3912` | 5 |
| POST | `/add/table` | `add_table` | `app.py:4724` | 5 |
| POST | `/add/workflow/category` | `add_workflow_category` | `app.py:11567` | 5 |
| POST | `/chat/general/text` | `chat_general_text` | `app.py:4631` | 5 |
| POST | `/document/reprocess-vectors/all` | `proxy_reprocess_all_vectors` | `app.py:12743` | 5 |
| POST | `/document_processor/job/save` | `document_processor_save_job` | `app.py:6257` | 5 |
| POST | `/export_package` | `export_package` | `app.py:2284` | 5 |
| POST | `/folder/info` | `get_folder_info_route` | `app.py:9140` | 5 |
| POST | `/folder/list_files` | `list_folder_files_route` | `app.py:9046` | 5 |
| POST | `/get/log` | `get_log` | `app.py:2839` | 5 |
| POST | `/get/quickjoblog` | `get_quickjob_log` | `app.py:2858` | 5 |
| POST | `/get_user_agents/{user_id}` | `get_user_agents` | `app.py:5255` | 5 |
| POST | `/import/agent/analyze` | `analyze_agent_package` | `app.py:13556` | 5 |
| POST | `/notification/email/{email_to}/{subject}` | `email_notification` | `app.py:5543` | 5 |
| POST | `/notification/email/{email_to}/{subject}/{message}` | `email_notification` | `app.py:5542` | 5 |
| POST | `/save_package` | `save_package` | `app.py:2207` | 5 |
| POST | `/schedule/quickjob` | `schedule_quickjob` | `app.py:3069` | 5 |
| POST | `/schedule/quickjob_legacy` | `schedule_quickjob_legacy` | `app.py:3010` | 5 |
| POST | `/start_test` | `start_test` | `app.py:5451` | 5 |
| POST | `/stop_test` | `stop_test` | `app.py:5467` | 5 |
| POST | `/test/connection` | `test_connection` | `app.py:11798` | 5 |
| PUT | `/update/workflow/category/{category_id}` | `update_workflow_category_name` | `app.py:11613` | 5 |
| PUT | `/update/workflows/{workflow_id}/category` | `update_workflow_category` | `app.py:5625` | 5 |
| POST | `/workflow/file/append` | `append_file` | `app.py:8891` | 5 |
| POST | `/workflow/file/check` | `check_file` | `app.py:8941` | 5 |
| POST | `/workflow/file/read` | `read_file` | `app.py:8772` | 5 |
| POST | `/workflow/file/write` | `write_file` | `app.py:8833` | 5 |
| POST | `/agents/reload` | `reload_agents` | `app_agent_api.py:670` | 5 |
| POST | `/chat/session` | `chat_with_session` | `app_agent_api.py:575` | 5 |
| POST | `/document/analyze` | `analyze_document_route` | `app_doc_api.py:836` | 5 |
| POST | `/document/extract` | `extract_document_fields_route` | `app_doc_api.py:686` | 5 |
| POST | `/document/extract_text` | `extract_document_text_route` | `app_doc_api.py:766` | 5 |
| POST | `/document/process` | `process_document_route` | `app_doc_api.py:383` | 5 |
| POST | `/document/process_directory` | `process_directory_route` | `app_doc_api.py:449` | 5 |
| POST | `/document/reprocess-vectors/all` | `reprocess_all_vectors_route` | `app_doc_api.py:1177` | 5 |
| POST | `/document/save` | `save_document_route` | `app_doc_api.py:630` | 5 |
| POST | `/document/search` | `search_documents_route_by_ids` | `app_doc_api.py:489` | 5 |
| POST | `/save/workflow/variables/{workflow_id}` | `save_workflow_variables` | `app_doc_api.py:980` | 5 |
| POST | `/documents/batch` | `add_documents_batch` | `app_vector_api.py:138` | 5 |
| POST | `/search_for_ai` | `search_documents_for_ai` | `app_vector_api.py:335` | 5 |
| POST | `/chat/with-environment` | `chat_with_environment` | `environment_assignment_api_routes.py:359` | 5 |
| POST | `/data_explorer/dashboard/save` | `save_dashboard` | `routes/data_explorer.py:410` | 5 |
| DELETE | `/data_explorer/dashboard/{dashboard_id}` | `delete_dashboard` | `routes/data_explorer.py:497` | 5 |
| POST | `/data_explorer/dashboard/{dashboard_id}/rename` | `rename_dashboard` | `routes/data_explorer.py:514` | 5 |
| POST | `/data_explorer/refresh` | `data_explorer_refresh_query` | `routes/data_explorer.py:329` | 5 |
| GET | `/admin/refresh-features` | `refresh_features` | `app.py:14304` | 4 |
| GET | `/delete/collection` | `delete_collection` | `app.py:3329` | 4 |
| GET | `/delete/quickjob` | `delete_quick_job` | `app.py:3973` | 4 |
| GET | `/api/agent/communications/history` | `get_agent_communications` | `agent_communication_routes.py:13` | 2 |
| GET | `/api/agent/workflows` | `get_agent_workflows` | `agent_communication_routes.py:91` | 2 |
| GET | `/api/agent-email/attachment/{attachment_id}` | `download_agent_email_attachment` | `agent_email_routes.py:1068` | 2 |
| GET | `/api/agent-email/attachment/{attachment_id}` | `download_attachment` | `agent_email_routes.py:1113` | 2 |
| GET | `/api/agent-email/attachment/{attachment_id}/extract` | `extract_attachment_text` | `agent_email_routes.py:1211` | 2 |
| GET | `/api/agent-email/attachment/{attachment_id}/info` | `get_attachment_info` | `agent_email_routes.py:1168` | 2 |
| GET | `/api/agent-email/inbox/{agent_id}` | `get_agent_inbox` | `agent_email_routes.py:618` | 2 |
| GET | `/api/agent-email/message/{agent_id}/{message_key}` | `get_agent_email_message` | `agent_email_routes.py:721` | 2 |
| GET | `/api/agents/email/list` | `list_all_agent_emails` | `agent_email_routes.py:1035` | 2 |
| GET | `/api/api-keys/model-overrides` | `get_model_overrides` | `api_keys_config.py:803` | 2 |
| GET | `/api/api-keys/status` | `get_status` | `api_keys_config.py:599` | 2 |
| GET | `/api/available-icons` | `get_available_icons` | `app.py:14368` | 2 |
| GET | `/api/builder-auto-token` | `builder_auto_token` | `app.py:1742` | 2 |
| GET | `/api/columns/{table_id}` | `get_columns_api` | `app.py:14952` | 2 |
| GET | `/api/connection-types` | `get_connection_types` | `app.py:14361` | 2 |
| GET | `/api/connection/stats/{connection_id}` | `get_connection_stats` | `app.py:14786` | 2 |
| GET | `/api/current-user` | `get_current_user_w_role` | `app.py:14636` | 2 |
| GET | `/api/debug/blueprints` | `list_blueprints` | `app.py:14323` | 2 |
| GET | `/api/document-attributes/metadata` | `api_get_document_attributes_metadata` | `app.py:8734` | 2 |
| GET | `/api/document-types` | `api_get_document_types` | `app.py:12507` | 2 |
| GET | `/api/get_agent_doc_type_restrictions/{agent_id}` | `get_agent_doc_type_restrictions` | `app.py:2667` | 2 |
| GET | `/api/internal/connection-schema/{connection_id}` | `internal_get_schema` | `app.py:4957` | 2 |
| GET | `/api/internal/connection-string/{connection_id}` | `internal_get_connection_string` | `app.py:4897` | 2 |
| GET | `/api/internal/connection-tables/{connection_id}` | `internal_get_tables` | `app.py:4931` | 2 |
| GET | `/api/internal/connections` | `internal_list_connections` | `app.py:4865` | 2 |
| GET | `/api/mcp/servers_v1` | `get_mcp_servers_v1` | `app.py:15935` | 2 |
| GET | `/api/quickjob/scheduler/backend` | `get_quickjob_scheduler_backend` | `app.py:3282` | 2 |
| GET | `/api/settings/db-logging` | `get_db_logging_setting` | `app.py:16979` | 2 |
| GET | `/api/system/config` | `get_system_config` | `app.py:12219` | 2 |
| GET | `/api/tables/{connection_id}` | `get_tables_api` | `app.py:14760` | 2 |
| GET | `/api/tool/dependency-groups` | `get_dependency_groups` | `app.py:2041` | 2 |
| GET | `/api/tool/diagnostic` | `tool_diagnostic` | `app.py:2687` | 2 |
| GET | `/api/workflow/analytics` | `get_workflow_analytics` | `app.py:10251` | 2 |
| GET | `/api/workflow/assistant/history` | `get_conversation_history_proxy` | `app.py:15837` | 2 |
| GET | `/api/workflow/logs` | `get_all_workflow_logs` | `app.py:10660` | 2 |
| GET | `/api/workflow/steps/{step_execution_id}` | `get_step_execution_details` | `app.py:10183` | 2 |
| GET | `/api/workflow/user-approvals` | `get_user_approvals` | `app.py:14468` | 2 |
| GET | `/api/email-dispatcher/status` | `get_email_dispatcher_status` | `app_executor_service.py:414` | 2 |
| GET | `/api/workflow/executions/active` | `get_active_executions` | `app_executor_service.py:356` | 2 |
| GET | `/api/workflow/assistant/history` | `get_conversation_history` | `app_knowledge_api.py:2971` | 2 |
| GET | `/api/cloud-storage/health` | `cloud_gateway_health` | `builder_cloud/routes/cloud_routes.py:17` | 2 |
| GET | `/api/pipelines/` | `list_pipelines` | `builder_data/routes/pipelines.py:52` | 2 |
| GET | `/api/pipelines/{pipeline_id}` | `get_pipeline` | `builder_data/routes/pipelines.py:61` | 2 |
| GET | `/api/pipelines/{pipeline_id}/results` | `get_pipeline_results` | `builder_data/routes/pipelines.py:160` | 2 |
| GET | `/api/mcp/connections` | `list_connections` | `builder_mcp/gateway/app_mcp_gateway.py:240` | 2 |
| GET | `/api/mcp/gateway/health` | `gateway_health` | `builder_mcp/routes/mcp_routes.py:939` | 2 |
| GET | `/api/mcp/oauth/authorize/{server_id}` | `oauth_authorize` | `builder_mcp/routes/mcp_routes.py:824` | 2 |
| GET | `/api/mcp/oauth/callback` | `oauth_callback` | `builder_mcp/routes/mcp_routes.py:882` | 2 |
| GET | `/api/mcp/oauth/redirect_uri` | `oauth_redirect_uri` | `builder_mcp/routes/mcp_routes.py:816` | 2 |
| GET | `/api/my-connections/servers` | `list_my_connections` | `builder_mcp/routes/my_connections_routes.py:31` | 2 |
| GET | `/api/algorithms` | `algorithms` | `builder_oracle/predictive_forecast/routes/api.py:41` | 2 |
| GET | `/api/analyze/status/{job_id}` | `analyze_status` | `builder_oracle/predictive_forecast/routes/api.py:179` | 2 |
| GET | `/api/model-files/{filename}` | `model_files` | `builder_oracle/predictive_forecast/routes/api.py:185` | 2 |
| GET | `/api/models` | `models_list` | `builder_oracle/predictive_forecast/routes/api.py:147` | 2 |
| GET | `/api/models/{model_name}` | `models_get` | `builder_oracle/predictive_forecast/routes/api.py:152` | 2 |
| GET | `/api/builder/documents/types` | `builder_list_document_types` | `builder_service/routes/builder_document_routes.py:169` | 2 |
| GET | `/api/auth/config` | `auth_config` | `builder_service/routes/chat.py:330` | 2 |
| GET | `/api/caution/level` | `get_caution_level` | `caution_system.py:392` | 2 |
| GET | `/api/caution/level` | `get_caution_level_disabled` | `caution_system.py:354` | 2 |
| GET | `/api/caution/levels` | `get_caution_levels` | `caution_system.py:382` | 2 |
| GET | `/api/caution/levels` | `get_caution_levels_disabled` | `caution_system.py:349` | 2 |
| GET | `/api/caution/user` | `get_user_caution_level` | `caution_system.py:424` | 2 |
| GET | `/api/caution/user` | `get_user_caution_level_disabled` | `caution_system.py:364` | 2 |
| GET | `/api/inspect/traces` | `list_traces` | `command_center_service/routes/inspect.py:21` | 2 |
| GET | `/api/inspect/traces/{trace_id}` | `get_trace` | `command_center_service/routes/inspect.py:26` | 2 |
| GET | `/api/inspect/traces/{trace_id}/summary` | `get_trace_summary` | `command_center_service/routes/inspect.py:32` | 2 |
| GET | `/api/memory/all` | `get_all_memories_endpoint` | `command_center_service/routes/memory.py:136` | 2 |
| GET | `/api/memory/insights` | `get_insights` | `command_center_service/routes/memory.py:118` | 2 |
| GET | `/api/memory/preferences` | `get_preferences` | `command_center_service/routes/memory.py:47` | 2 |
| GET | `/api/memory/routes` | `get_routes` | `command_center_service/routes/memory.py:78` | 2 |
| GET | `/api/memory/routes/stats` | `get_route_stats_endpoint` | `command_center_service/routes/memory.py:109` | 2 |
| GET | `/api/memory/suggestions` | `get_suggestions` | `command_center_service/routes/memory.py:19` | 2 |
| GET | `/api/tools/audit` | `audit_log` | `command_center_service/routes/tools.py:40` | 2 |
| GET | `/api/tools/generated` | `list_tools` | `command_center_service/routes/tools.py:18` | 2 |
| GET | `/api/tools/generated/{tool_name}` | `get_tool` | `command_center_service/routes/tools.py:24` | 2 |
| GET | `/api/solutions/connections/export/{connection_id}` | `export_connection` | `connection_install_routes.py:47` | 2 |
| GET | `/api/tool/export/{tool_name}` | `export_single_tool` | `custom_tool_import_routes.py:325` | 2 |
| GET | `/api/pages/{page_id}/summaries` | `get_page_summaries` | `document_summarization_routes.py:506` | 2 |
| GET | `/api/pages/{page_id}/summaries` | `proxy_get_page_summaries` | `document_summarization_wrapper_routes.py:230` | 2 |
| GET | `/api/summaries/config/length-limits` | `proxy_get_summary_length_limits` | `document_summarization_wrapper_routes.py:294` | 2 |
| GET | `/api/summaries/stats/length-analysis` | `proxy_get_summary_length_analysis` | `document_summarization_wrapper_routes.py:359` | 2 |
| GET | `/api/summarization/health` | `summarization_health_check` | `document_summarization_wrapper_routes.py:727` | 2 |
| GET | `/api/email-processing/dispatcher/status` | `get_dispatcher_status` | `email_processing_routes.py:404` | 2 |
| GET | `/api/email-processing/history` | `get_processing_history` | `email_processing_routes.py:67` | 2 |
| GET | `/api/email-processing/record/{record_id}` | `get_processing_record` | `email_processing_routes.py:312` | 2 |
| GET | `/api/email-processing/stats` | `get_processing_stats` | `email_processing_routes.py:186` | 2 |
| GET | `/api/assignments/agents/list` | `list_agents_api` | `environment_assignment_api_routes.py:26` | 2 |
| GET | `/api/assignments/list` | `list_assignments` | `environment_assignment_api_routes.py:66` | 2 |
| GET | `/api/assignments/summary` | `assignment_summary` | `environment_assignment_api_routes.py:284` | 2 |
| GET | `/api/feedback/list` | `list_feedback` | `feedback_routes.py:181` | 2 |
| GET | `/api/feedback/my-feedback` | `get_my_feedback` | `feedback_routes.py:285` | 2 |
| GET | `/api/feedback/stats` | `get_feedback_stats` | `feedback_routes.py:267` | 2 |
| GET | `/api/feedback/by-agent` | `get_feedback_by_agent` | `feedback_system.py:548` | 2 |
| GET | `/api/feedback/detail` | `get_feedback_detail` | `feedback_system.py:414` | 2 |
| GET | `/api/feedback/problems` | `get_problematic_questions` | `feedback_system.py:291` | 2 |
| GET | `/api/feedback/trends` | `get_feedback_trends` | `feedback_system.py:599` | 2 |
| GET | `/api/updater/check` | `api_check_update` | `github_updater.py:743` | 2 |
| GET | `/api/updater/download/progress` | `api_download_progress` | `github_updater.py:813` | 2 |
| GET | `/api/updater/install/status` | `api_install_status` | `github_updater.py:868` | 2 |
| GET | `/api/updater/version` | `api_get_version` | `github_updater.py:932` | 2 |
| GET | `/api/setup/status` | `setup_status` | `initial_setup_routes.py:230` | 2 |
| GET | `/api/solutions/integrations/export/{name}` | `export_integration` | `integration_install_routes.py:88` | 2 |
| GET | `/api/solutions/integrations/list` | `list_integrations` | `integration_install_routes.py:61` | 2 |
| GET | `/api/history/conversations` | `list_conversations` | `local_history_routes.py:125` | 2 |
| GET | `/api/history/conversations/{conversation_id}` | `get_conversation` | `local_history_routes.py:160` | 2 |
| GET | `/api/history/dashboard` | `dashboard_data` | `local_history_routes.py:440` | 2 |
| GET | `/api/history/queries` | `list_queries` | `local_history_routes.py:343` | 2 |
| GET | `/api/history/settings` | `get_settings` | `local_history_routes.py:484` | 2 |
| GET | `/api/history/storage` | `storage_info` | `local_history_routes.py:499` | 2 |
| GET | `/api/local-secrets/categories` | `get_categories` | `local_secrets_routes.py:358` | 2 |
| GET | `/api/local-secrets/info` | `get_storage_info` | `local_secrets_routes.py:324` | 2 |
| GET | `/api/local-secrets/{name}` | `get_secret_info` | `local_secrets_routes.py:207` | 2 |
| GET | `/api/evals` | `api_list_evals` | `model_tester/app.py:286` | 2 |
| GET | `/api/evals/{eval_id}` | `api_get_eval` | `model_tester/app.py:291` | 2 |
| GET | `/api/results` | `api_list_results` | `model_tester/app.py:463` | 2 |
| GET | `/api/results/{rid}` | `api_get_result` | `model_tester/app.py:468` | 2 |
| GET | `/api/settings` | `api_get_settings` | `model_tester/app.py:251` | 2 |
| GET | `/api/system_prompts` | `api_list_system_prompts` | `model_tester/app.py:321` | 2 |
| GET | `/api/system_prompts/{name}` | `api_get_system_prompt` | `model_tester/app.py:329` | 2 |
| GET | `/api/onboarding/checklist/data-assistant` | `get_data_assistant_checklist` | `onboarding_routes.py:219` | 2 |
| GET | `/api/onboarding/tour/check/{tour_name}` | `check_tour` | `onboarding_routes.py:195` | 2 |
| GET | `/preferences/api/get` | `get_user_preferences_api` | `preferences_routes.py:226` | 2 |
| GET | `/preferences/api/get/{preference_key}` | `get_single_preference_api` | `preferences_routes.py:247` | 2 |
| GET | `/api/scheduler/types/{job_type}/schedules` | `get_all_schedules_by_type` | `scheduler_routes.py:516` | 2 |
| GET | `/api/solutions/author/assets` | `list_available_assets` | `solution_builder_routes.py:273` | 2 |
| GET | `/api/solutions/author/published` | `list_published` | `solution_builder_routes.py:933` | 2 |
| GET | `/api/solutions/drafts` | `list_drafts` | `solution_builder_routes.py:178` | 2 |
| GET | `/api/solutions/drafts/{draft_id}` | `get_draft` | `solution_builder_routes.py:200` | 2 |
| GET | `/settings/api/telemetry/consent` | `get_consent` | `telemetry.py:896` | 2 |
| GET | `/settings/api/telemetry/health` | `local_health` | `telemetry.py:926` | 2 |
| GET | `/api/workflow/builder/history` | `get_builder_conversation_history` | `workflow_builder_routes.py:428` | 2 |
| GET | `/api/workflow/builder/status` | `workflow_builder_status` | `workflow_builder_routes.py:304` | 2 |
| GET | `/api/workflow/builder/training-stats` | `get_training_statistics` | `workflow_builder_routes.py:603` | 2 |
| GET | `/api/solutions/workflows/export/{name}` | `export_workflow` | `workflow_export_routes.py:72` | 2 |
| GET | `/api/solutions/workflows/list` | `list_workflows` | `workflow_export_routes.py:92` | 2 |
| GET | `/` | `home` | `app.py:1499` | 0 |
| GET | `/` | `preferences_page` | `app.py:12107` | 0 |
| GET | `/add/collection` | `add_update_collection` | `app.py:3448` | 0 |
| GET | `/add/column` | `add_column` | `app.py:5131` | 0 |
| GET | `/add/connection-legacy` | `add_update_connection_legacy` | `app.py:3494` | 0 |
| GET | `/add/job` | `add_job` | `app.py:4012` | 0 |
| GET | `/add/quickjob` | `add_quick_job` | `app.py:3912` | 0 |
| GET | `/add/table` | `add_table` | `app.py:4724` | 0 |
| GET | `/chat/data/explain` | `chat_data_explain` | `app.py:5283` | 0 |
| GET | `/chat/data/status` | `chat_data_status` | `app.py:11716` | 0 |
| GET | `/chat/email` | `chat_email` | `app.py:4660` | 0 |
| GET | `/debug/endpoints` | `list_endpoints` | `app.py:16966` | 0 |
| GET | `/directories` | `list_directories` | `app.py:6603` | 0 |
| GET | `/document-search` | `document_search_page` | `app.py:7027` | 0 |
| GET | `/document-search-legacy` | `document_search_page_legacy` | `app.py:6650` | 0 |
| GET | `/document/config` | `get_document_config` | `app.py:7952` | 0 |
| GET | `/export_results` | `export_results` | `app.py:5397` | 0 |
| GET | `/get/agent_knowledge_user/{agent_id}` | `get_agent_knowledge_user_route` | `app.py:11260` | 0 |
| GET | `/get/collection/{collection_id}` | `get_collection` | `app.py:2902` | 0 |
| GET | `/get/collections` | `get_collections` | `app.py:2911` | 0 |
| GET | `/get/columns` | `get_column` | `app.py:5170` | 0 |
| GET | `/get/data_agents` | `get_data_agents` | `app.py:2515` | 0 |
| GET | `/get/job/{job_id}` | `get_job` | `app.py:2893` | 0 |
| GET | `/get/jobs` | `get_jobs` | `app.py:2830` | 0 |
| GET | `/get/log` | `get_log` | `app.py:2839` | 0 |
| GET | `/get/odbc_drivers` | `get_odbc_drivers` | `app.py:11910` | 0 |
| GET | `/get/quickjoblog` | `get_quickjob_log` | `app.py:2858` | 0 |
| GET | `/get/schedules/{job_id}` | `get_schedules` | `app.py:2987` | 0 |
| GET | `/get/tables/{connection_id}` | `get_table` | `app.py:4787` | 0 |
| GET | `/get/user_agents/{user_id}` | `get_agents_by_user` | `app.py:2549` | 0 |
| GET | `/get/user_data_agents` | `get_user_data_agents` | `app.py:2532` | 0 |
| GET | `/get_packages` | `get_packages` | `app.py:2181` | 0 |
| GET | `/get_results` | `get_results` | `app.py:5474` | 0 |
| GET | `/get_user_agents/{user_id}` | `get_user_agents` | `app.py:5255` | 0 |
| GET | `/load_package/{package_name}` | `load_package` | `app.py:2188` | 0 |
| GET | `/notification/email/{email_to}/{subject}` | `email_notification` | `app.py:5543` | 0 |
| GET | `/notification/email/{email_to}/{subject}/{message}` | `email_notification` | `app.py:5542` | 0 |
| GET | `/schedule/quickjob_legacy` | `schedule_quickjob_legacy` | `app.py:3010` | 0 |
| GET | `/test-crash` | `test_crash` | `app.py:16953` | 0 |
| GET | `/document/get/{document_id}` | `get_document_route` | `app_doc_api.py:596` | 0 |
| GET | `/document/health` | `health_check` | `app_doc_api.py:1095` | 0 |
| GET | `/document/types` | `get_document_types_route` | `app_doc_api.py:1073` | 0 |
| GET | `/get/workflow/variables/{workflow_id}` | `get_workflow_variables` | `app_doc_api.py:1029` | 0 |
| GET | `/` | `index` | `builder_data/main.py:116` | 0 |
| GET | `/` | `index` | `builder_oracle/predictive_forecast/app.py:49` | 0 |
| GET | `/` | `index` | `builder_service/main.py:111` | 0 |
| GET | `/` | `index` | `command_center_service/main.py:208` | 0 |
| GET | `/` | `index` | `model_tester/app.py:246` | 0 |
| GET | `/data_explorer/dashboard/list` | `list_dashboards` | `routes/data_explorer.py:444` | 0 |
| GET | `/data_explorer/dashboard/{dashboard_id}` | `load_dashboard` | `routes/data_explorer.py:472` | 0 |
| GET | `/` | `home` | `run_dca.py:117` | 0 |
| GET | `/solutions/author/edit/{draft_id}` | `author_edit_page` | `solution_builder_routes.py:138` | 0 |
| GET | `/settings/telemetry` | `telemetry_settings` | `telemetry.py:890` | 0 |

## Tested routes (sample, with first 5 referencing files)

- **DELETE** `/admin/identity/providers/{provider_id}` (delete_provider) — refs: test_identity_routes_and_ldap_mock.py
- **DELETE** `/api/agent-email/config/{agent_id}` (delete_agent_email_config) — refs: builder_agent_e2e_tests_15_16.md
- **DELETE** `/api/artifacts/{artifact_id}` (delete_artifact) — refs: 04_CC_CONVERSE_TOOLS.md, 17_XUSER_ISOLATION.md, ROUND3_BUG_REPORT.md, run_round3_batch2.py, mod04_CT-9_events.json
- **DELETE** `/api/compliance/retailers/{retailer_id}` (delete_retailer) — refs: test_compliance_routes.py, conftest.py, entities.py, test_compliance_set_lifecycle.py, test_compliance_pipeline.py
- **DELETE** `/api/compliance/schemas/{schema_id}` (delete_schema) — refs: test_compliance_routes.py, conftest.py, entities.py, 30_COMPLIANCE_HAPPY_PATH.md, 31_COMPLIANCE_SECURITY.md
- **DELETE** `/api/compliance/sets/{set_id}` (delete_document_set) — refs: test_compliance_routes.py, test_compliance_set_lifecycle.py, test_compliance_pipeline.py, conftest.py, test_journey_04_compliance_officer.py
- **DELETE** `/api/compliance/versions/{version_id}` (delete_version) — refs: test_compliance_routes.py, test_compliance_pipeline.py, 30_COMPLIANCE_HAPPY_PATH.md, 31_COMPLIANCE_SECURITY.md, module30_tests.json
- **DELETE** `/api/data-collection/builder/session/{session_id}` (builder_close_session) — refs: test_dca_builder_routes.py
- **DELETE** `/api/data-collection/builder/{config_id}` (builder_delete) — refs: module35_dca_tests.json, 35_DCA_FULL_FLOW.md, test_dca_route_security.py, test_dca_builder_routes.py
- **DELETE** `/api/data-collection/session/{session_id}` (abandon_session) — refs: test_dca_session_lifecycle.py, module35_dca_tests.json, 35_DCA_FULL_FLOW.md, test_dca_route_security.py, test_dca_user_routes.py
- **DELETE** `/api/data-collection/session/{session_id}/debug` (clear_debug_events) — refs: test_dca_session_lifecycle.py, module35_dca_tests.json, 35_DCA_FULL_FLOW.md, test_dca_route_security.py, test_dca_user_routes.py
- **DELETE** `/api/documents/bulk-delete` (api_bulk_delete_documents) — refs: TEST_SUITE_INVENTORY.md
- **DELETE** `/api/documents/{document_id}` (api_delete_document) — refs: TEST_SUITE_INVENTORY.md, test_actions.py, builder_agent_capability_tests.md
- **DELETE** `/api/integrations/templates/custom/{template_key}` (delete_custom_template) — refs: test_integration_routes.py
- **DELETE** `/api/integrations/{integration_id}` (delete_integration) — refs: test_route_auth.py, TEST_SUITE_INVENTORY.md, test_integration_routes.py, conftest.py, entities.py
- **DELETE** `/api/mcp/servers/{server_id}` (delete_mcp_server) — refs: test_route_auth.py, conftest.py, entities.py, test_full_feature_tour.py, test_real_user_create_mcp_server_no_reload.py
- **DELETE** `/api/mcp/servers/{server_id}` (delete_server) — refs: test_route_auth.py, conftest.py, entities.py, test_full_feature_tour.py, test_real_user_create_mcp_server_no_reload.py
- **DELETE** `/api/scheduler/jobs/{job_id}` (delete_job) — refs: TEST_SUITE_INVENTORY.md, test_scheduler_lifecycle.py, test_full_feature_tour.py, actions.py
- **DELETE** `/api/scheduler/jobs/{job_id}/schedules/{schedule_id}` (delete_job_schedule) — refs: TEST_SUITE_INVENTORY.md, test_scheduler_lifecycle.py, test_full_feature_tour.py, actions.py
- **DELETE** `/api/scheduler/jobs/{job_id}/types/{job_type}/schedules/{schedule_id}` (delete_job_schedule_by_type) — refs: TEST_SUITE_INVENTORY.md, test_scheduler_lifecycle.py, test_full_feature_tour.py, actions.py
- **DELETE** `/api/sessions/{session_id}` (delete_session) — refs: test_real_user_multi_session_switching.py, test_real_user_pdf_upload_chat_export.py, 32_OPS_ROOM_FLOW.md, module32_ops_tests.json, test_ops_authz.py
- **DELETE** `/api/sessions/{session_id}` (delete_session) — refs: test_real_user_multi_session_switching.py, test_real_user_pdf_upload_chat_export.py, 32_OPS_ROOM_FLOW.md, module32_ops_tests.json, test_ops_authz.py
- **DELETE** `/api/sessions/{session_id}` (delete_session) — refs: test_real_user_multi_session_switching.py, test_real_user_pdf_upload_chat_export.py, 32_OPS_ROOM_FLOW.md, module32_ops_tests.json, test_ops_authz.py
- **DELETE** `/api/uploads/{file_id}` (delete_upload) — refs: test_real_user_pdf_upload_chat_export.py
- **DELETE** `/api/uploads/{file_id}` (delete_upload) — refs: test_real_user_pdf_upload_chat_export.py
- **DELETE** `/delete/workflow/{workflow_id}` (del_workflow) — refs: test_route_auth.py, test_workflow_routes.py, test_competency_workflow_execution.py, conftest.py, test_workflow_lifecycle.py
- **DELETE** `/delete_package/{package_name}` (delete_package) — refs: test_route_auth.py, builder_agent_capability_tests.md
- **DELETE** `/documents/{doc_id}` (delete_document) — refs: TEST_SUITE_INVENTORY.md, test_actions.py, builder_agent_capability_tests.md
- **DELETE** `/history` (clear_history) — refs: tour_coverage.md, tour_report.json, tour_report.md, pages.py, test_universal_assistant_new.py
- **DELETE** `/sessions/{session_id}` (delete_session) — refs: test_dca_session_lifecycle.py, test_real_user_multi_session_switching.py, test_real_user_pdf_upload_chat_export.py, module35_dca_tests.json, 32_OPS_ROOM_FLOW.md
- **GET** `/add/connection` (add_update_connection) — refs: test_rbac.py, entities.py, test_real_user_bad_connection_recovery.py
- **GET** `/add/group` (add_update_group) — refs: test_route_auth.py
- **GET** `/add/user` (add_update_user) — refs: test_rbac.py, test_route_auth.py, entities.py, test_real_user_role_downgrade_mid_session.py
- **GET** `/admin` (admin) — refs: settings_page.py, test_dca_identity_and_isolation.py, test_role_decorators.py, test_identity_routes_and_ldap_mock.py, COVERAGE.md
- **GET** `/admin/api-keys` (api_keys_config_page) — refs: settings_page.py, tour_coverage.md, tour_report.json, tour_report.md, pages.py
- **GET** `/admin/caution-settings` (caution_settings) — refs: tour_report.json, tour_report.md, pages.py
- **GET** `/admin/feedback-analysis` (feedback_analysis) — refs: tour_report.json, tour_report.md, pages.py
- **GET** `/admin/identity/providers` (get_providers) — refs: test_identity_routes_and_ldap_mock.py
- **GET** `/admin/identity/settings` (identity_settings_page) — refs: test_identity_routes_and_ldap_mock.py
- **GET** `/admin/summarization-dashboard` (summarization_admin_dashboard) — refs: TEST_SUITE_INVENTORY.md, tour_coverage.md, tour_report.json, tour_report.md, pages.py

...and 422 more (omitted for brevity).

## Untested env vars

### `AppUtils.py`

- `AI_HUB_PROMPT_MINI`
- `APP_UTILS_LOG`

### `CommandGenerator.py`

- `COMMAND_GENERATOR_LOG`

### `CommonUtils.py`

- `AI_HUB_DOCUMENT_API_URL`
- `AI_HUB_DOCUMENT_PROCESS_ROUTE`
- `AI_HUB_PROXY_ANTHROPIC_MESSAGES`
- `AI_HUB_PROXY_ANTHROPIC_STREAM`

### `DataFrameFileManager.py`

- `DATAFRAME_FILE_MANAGER`

### `GeneralAgent.py`

- `GENERAL_AGENT_LOG`

### `LLMAnalyticalEngine.py`

- `LLM_ANALYTICAL_ENGINE_LOG`

### `LLMDataEngineV2.py`

- `LLM_DATA_ENGINE_LOG`

### `LLMDocumentEngine.py`

- `LLM_DOCUMENT_ENGINE`

### `LLMDocumentSearchEngine.py`

- `LLM_DOCUMENT_SEARCH_ENGINE`

### `RichContentManager.py`

- `RICH_CONTENT_MANAGER_LOG`

### `SmartContentRenderer.py`

- `SMART_CONTENT_RENDER_LOG`

### `WorkflowAgent.py`

- `WORKFLOW_AGENT_LOG`

### `admin_tier_usage.py`

- `ADMIN_TIER_USAGE_LOG`

### `agent_api_client.py`

- `AGENT_API_CLIENT_LOG`
- `AGENT_API_URL`

### `agent_email_routes.py`

- `AGENT_EMAIL_API_LOG`

### `agent_email_tools.py`

- `AGENT_EMAIL_TOOLS_LOG`

### `agent_environment_executor.py`

- `AGENT_API_LOG`
- `ENVIRONMENT_EXECUTOR_LOG`

### `agent_excel_tools.py`

- `EXCEL_TOOLS_LOG`

### `ai_key_matcher.py`

- `AI_KEY_MATCHER_LOG`

### `app.py`

- `BUILDER_SERVICE_HOST`
- `BUILDER_SERVICE_PORT`
- `CC_SERVICE_HOST`
- `CC_SERVICE_PORT`
- `MCP_ENCRYPTION_KEY`
- `MPLBACKEND`
- `REVERSE_PROXY_HOPS`
- `USE_REVERSE_PROXY`
- `VECTOR_API_KEY`

### `app_config_b2c.py`

- `CLIENT_ID`
- `EDITPROFILE_USER_FLOW`
- `RESETPASSWORD_USER_FLOW`
- `SIGNUPSIGNIN_USER_FLOW`
- `TENANT_NAME`

### `app_doc_api.py`

- `DOC_API_LOG`

### `app_doc_job_q.py`

- `SCHEMA_DIR`
- `VECTOR_DB_PATH`

### `app_executor_service.py`

- `APP_WORKFLOW_EXECUTOR_LOG`

### `app_jss_main.py`

- `JOB_SCHEDULER_LOG`

### `app_vector_api.py`

- `DOC_VECTOR_API_LOG`
- `KNOWLEDGE_VECTOR_DB_PATH`
- `VECTOR_COLLECTION_NAME`

### `attachment_text_extractor.py`

- `EMAIL_ATTACHMENT_EXTRACTOR`

### `auth_middleware.py`

- `LOG_DIR_AUTH`

### `builder_cloud/client/cloud_storage_client.py`

- `CLOUD_GATEWAY_URL`

### `builder_cloud/gateway/cloud_gateway_config.py`

- `CLOUD_DEFAULT_SAS_EXPIRY`
- `CLOUD_GATEWAY_LOG`
- `CLOUD_GATEWAY_PORT`
- `CLOUD_MAX_DOWNLOAD_MB`
- `CLOUD_MAX_UPLOAD_MB`
- `CLOUD_TIMEOUT`

### `builder_data/builder_data_config.py`

- `CORS_ORIGINS`
- `DATA_MAX_ROWS_PIPELINE`
- `DATA_MAX_ROWS_PREVIEW`
- `DATA_PIPELINE_TIMEOUT`
- `DATA_SERVICE_DEBUG`
- `DATA_SERVICE_HOST`
- `DATA_TEMP_DIR`
- `LANGCHAIN_TRACING_V2`
- `LANGSMITH_TRACING`

### `builder_mcp/client/mcp_gateway_client.py`

- `MCP_GATEWAY_URL`

### `builder_mcp/gateway/app_mcp_gateway.py`

- `MCP_GATEWAY_HOST`
- `MCP_GATEWAY_PORT`

### `builder_mcp/gateway/mcp_gateway_config.py`

- `MCP_CONNECT_TIMEOUT`
- `MCP_GATEWAY_LOG`
- `MCP_MAX_RETRIES`
- `MCP_TOOL_CACHE_TTL`
- `MCP_TOOL_CALL_TIMEOUT`

### `builder_mcp/servers/graph_stdio_server.py`

- `GRAPH_MCP_LOG_LEVEL`

### `builder_oracle/predictive_forecast/app.py`

- `FC_SECRET_KEY`

### `builder_oracle/predictive_forecast/config/settings.py`

- `FC_CONFIDENCE_LEVEL`
- `FC_CUMULATIVE_IMPORTANCE`
- `FC_DEBUG`
- `FC_DNN_BATCH_SIZE`
- `FC_DNN_DROPOUT`
- `FC_DNN_EPOCHS`
- `FC_DNN_LAYER_1`
- `FC_DNN_LAYER_2`
- `FC_DNN_NUM_FOLDS`
- `FC_DNN_PATIENCE`
- `FC_DNN_USE_KFOLD`
- `FC_FEATURE_IMPORTANCE_ITER`
- `FC_HOST`
- `FC_MAX_UPLOAD_MB`
- `FC_MC_DROPOUT_ITER`
- `FC_PORT`
- `FC_THREADS`

### `builder_service/builder_config.py`

- `BUILDER_DEBUG`
- `BUILDER_HOST`
- `BUILDER_PORT`

### `command_center_service/cc_config.py`

- `CC_ANSWER_QUALITY_GATE`
- `CC_AQG_CONFIDENCE_THRESHOLD`
- `CC_AQG_GEO_CONFIDENCE`
- `CC_AQG_KNOWLEDGE_CONFIDENCE`
- `CC_CAPABILITY_ROUTER`
- `CC_DEBUG`
- `CC_DELEGATION_TIMEOUT`
- `CC_DOCUMENT_SEARCH_ENABLED`
- `CC_HOST`
- `CC_IMAGE_GENERATION_ENABLED`
- `CC_INTENT_HEURISTICS`
- `CC_MINI_ACTIVE_DELEGATION_ROUTING`
- `CC_MINI_AGENT_PICKER`
- `CC_MINI_AGENT_SELECTION_PARSER`
- `CC_MINI_ALTERNATIVE_AGENT_FINDER`
- `CC_MINI_ANSWER_QUALITY_GATE`
- `CC_MINI_BUILDER_AFFIRMATIVE_DETECTOR`
- `CC_MINI_BUILDER_DISTILLER`
- `CC_MINI_CAPABILITY_ROUTER`
- `CC_MINI_DELEGATION_RESULT_CLASSIFIER`
- `CC_MINI_EXPORT_INTENT_DETECTOR`
- `CC_MINI_INTENT_CLASSIFICATION`
- `CC_MINI_RESPONSE_SANITIZER`
- `CC_MINI_TASK_DECOMPOSITION`
- `CC_MINI_TOOL_EMAIL_EXTRACTOR`
- `CC_MINI_TOOL_EXPORT_STRUCTURER`
- `CC_MINI_TOOL_MAP_STRUCTURER`
- `CC_PORT`
- `CC_PRINCIPLED_ROUTING`
- `CC_ROUTE_MEMORY`
- `CC_SESSION_INSIGHTS`
- `CC_SESSION_INSIGHT_MIN_TURNS`
- `CC_USE_MINI_LLM`

### `command_center_service/main.py`

- `CC_LOG_BACKUP_COUNT`
- `CC_LOG_MAX_BYTES`

### `config.py`

- `ANTHROPIC_API_THROTTLE_DELAY`
- `AZURE_OPENAI_API_KEY_MINI`
- `AZURE_OPENAI_API_VERSION_MINI`
- `AZURE_OPENAI_DEPLOYMENT_NAME_ALTERNATE_MINI`
- `AZURE_OPENAI_DEPLOYMENT_NAME_EMBEDDING`
- `COMMAND_GENERATOR_DEPLOYMENT`
- `COMMAND_GENERATOR_MODEL`
- `DATABASE_DRIVER`
- `DOC_MAX_UPLOAD_SIZE_MB`
- `DOC_PROCESSING_TIMEOUT_MINUTES`
- `DOC_RERANK_FETCH_N`
- `DOC_RERANK_KEEP_THRESHOLD`
- `DOC_RERANK_MAX_KEEP`
- `DOC_USE_LLM_RERANK`
- `EMAIL_FALLBACK_ENABLED`
- `KNOWLEDGE_FANOUT_MAX_DOCS`
- `KNOWLEDGE_FANOUT_PARALLEL`
- `KNOWLEDGE_FANOUT_PER_DOC_TOP_K`
- `KNOWLEDGE_HAIKU_MODEL`
- `KNOWLEDGE_PARENT_PAGE_CHAR_CAP`
- `KNOWLEDGE_PARENT_TOTAL_CHAR_CAP`
- `KNOWLEDGE_ROUTER_MODEL`
- `KNOWLEDGE_SUMMARY_MAX_CHARS`
- `KNOWLEDGE_SUMMARY_SAMPLE_POINTS`
- `KNOWLEDGE_SUMMARY_SAMPLE_TOTAL_CHARS`
- `KNOWLEDGE_SUMMARY_SAMPLING`
- `LLM_CHARACTER_LIMIT`
- `LLM_PANDAS_AGENT_DESCRIPTION`
- `LLM_PANDAS_CODE_REGEN_PROMPT`
- `LLM_PANDAS_CODE_REGEN_SYSTEM`
- `LLM_ROW_LIMIT`
- `MAX_CONCURRENT_WORKFLOWS`
- `NLQ_PROVIDER`
- `OPENAI_REASONING_EFFORT`
- `PHONE_DISPLAY_NAME`
- `PHONE_SOURCE`
- `SOLUTIONS_CACHE_DIR`
- `SOLUTIONS_CATALOG_URL`
- `SOLUTIONS_DRAFTS_DIR`
- `USE_LLM_CHART_GENERATION`
- `USE_MODERN_CHAT_UI`
- `USE_TWO_STAGE_ARCHITECTURE`
- `VECTOR_EMBEDDING_TOKEN_ENCODING`
- `WINRM_DOMAIN`
- `WORKFLOW_TRAINING_CAPTURE_ENABLED`

### `data_collection_agent/actions/workflow_action.py`

- `INTERNAL_API_KEY`

### `data_collection_agent/agent.py`

- `DCA_AGENT_LOG`
- `DCA_AGENT_MAX_EXECUTION_SECONDS`
- `DCA_AGENT_MAX_ITERATIONS`

### `data_collection_agent/builder/builder_agent.py`

- `DCA_BUILDER_LOG`

### `data_collection_agent/builder/builder_routes.py`

- `DCA_BUILDER_ROUTES_LOG`

### `data_collection_agent/field_extractor.py`

- `DCA_EXTRACTOR_TIMEOUT_SECONDS`

### `data_collection_agent/routes.py`

- `DCA_ROUTES_LOG`

### `email_agent_dispatcher.py`

- `EMAIL_DISPATCHER_LOG`

### `encrypt_config.py`

- `ENCRYPTION_SALT`
- `ENCRYPTION_SECRET`

### `excel_update_utils.py`

- `EXCEL_UPDATE_LOG`

### `excel_utils.py`

- `EXCEL_UTILS_LOG`

### `fast_pdf_extractor.py`

- `FAST_PDF_EXTRACTOR_LOG`

### `github_updater.py`

- `AIHUB_GITHUB_REPO`
- `AIHUB_VERSION`
- `GITHUB_UPDATER_LOG`
- `ProgramData`

### `initial_setup.py`

- `DEFAULT_ADMIN_USERNAME`

### `integration_manager.py`

- `INTEGRATIONS_MGR_LOG`

### `integration_routes.py`

- `INTEGRATIONS_LOG`

### `integration_template_loader.py`

- `AIHUB_INTEGRATIONS_DIR`

### `job_scheduler.py`

- `JOB_SCHEDULER_SERVICE_LOG`

### `model_tester/app.py`

- `MODEL_TESTER_PORT`

### `model_tester/llm_clients.py`

- `AZURE_OPENAI_ENDPOINT`
- `OPENAI_API_BASE`

### `run_app.py`

- `HOST_DEBUG`
- `HOST_IP`
- `SERVER_THREADS`

### `run_dca.py`

- `DCA_HOST`
- `DCA_PORT`
- `DCA_REQUIRE_AUTH`

### `smart_change_detector.py`

- `AI_CHG_MATCHER_LOG`

### `telemetry.py`

- `APP_ENVIRONMENT`
- `TELEMETRY_LOG`

### `training/llm.py`

- `TRAINING_LLM_BACKEND`

### `whisper_routes.py`

- `WHISPER_LOG`

### `workflow_builder_routes.py`

- `WORKFLOW_BUILDER_ROUTES_LOG`

### `workflow_command_validator.py`

- `WORKFLOW_VALIDATION_LOG`

### `workflow_compiler.py`

- `WORKFLOW_COMPILER_LOG`

### `workflow_execution.py`

- `WORKFLOW_EXECUTION_LOG`

### `workflow_training_capture.py`

- `WORKFLOW_TRAINING_CAPTURE_PATH`
- `WORKFLOW_TRAINING_LOG`

### `wsgi.py`

- `SERVER_CONNECTION_LIMIT`
- `WAITRESS_CHANNEL_TIMEOUT`

### `wsgi_executor_service.py`

- `EXECUTOR_SERVICE_THREADS`
- `WSGI_EXECUTOR_SERVICE_LOG`

### `wsgi_knowledge_api.py`

- `KNOWLEDGE_API_LOG`
- `KNOWLEDGE_SERVER_THREADS`

### `wsgi_vector_api.py`

- `VECTOR_SERVER_THREADS`


## Role decorators in use

### `@admin_required` — 30 use(s)
- `admin_tier_usage.py:107` `index`
- `admin_tier_usage.py:647` `get_subscription_info_from_cloud`
- `admin_tier_usage.py:776` `get_cache_status`
- `admin_tier_usage.py:786` `invalidate_cache`
- `admin_tier_usage.py:797` `get_tier_stats`
- `admin_tier_usage.py:958` `get_users_list`
- `admin_tier_usage.py:1010` `get_feature_flags`
- `admin_tier_usage.py:1023` `update_feature_flags`
- `app.py:1472` `users`
- `app.py:1494` `groups`
- `app.py:2977` `get_user`
- `app.py:16980` `get_db_logging_setting`
- `app.py:17001` `set_db_logging_setting`
- `auth_identity_routes.py:26` `identity_settings_page`
- `auth_identity_routes.py:33` `get_providers`
- `auth_identity_routes.py:74` `save_provider`
- `auth_identity_routes.py:148` `delete_provider`
- `auth_identity_routes.py:172` `test_provider_connection`
- `auth_identity_routes.py:207` `test_provider_connection_adhoc`
- `feedback_routes.py:183` `list_feedback`
- ... and 10 more.

### `@api_key_or_session_required` — 199 use(s)
- `agent_email_routes.py:265` `agent_email_config_page`
- `agent_email_routes.py:272` `agent_email_inbox_page`
- `agent_email_routes.py:283` `get_agent_email_config`
- `agent_email_routes.py:432` `save_agent_email_config`
- `agent_email_routes.py:596` `delete_agent_email_config`
- `agent_email_routes.py:619` `get_agent_inbox`
- `agent_email_routes.py:722` `get_agent_email_message`
- `agent_email_routes.py:760` `mark_agent_emails_read`
- `agent_email_routes.py:799` `send_reply`
- `agent_email_routes.py:843` `send_test_email`
- `agent_email_routes.py:916` `provision_agent_email`
- `agent_email_routes.py:1001` `get_agent_email`
- `agent_email_routes.py:1036` `list_all_agent_emails`
- `agent_email_routes.py:1069` `download_agent_email_attachment`
- `agent_email_routes.py:1114` `download_attachment`
- `agent_email_routes.py:1169` `get_attachment_info`
- `agent_email_routes.py:1212` `extract_attachment_text`
- `app.py:1857` `cc_generate_token`
- `app.py:1989` `get_tool_dependencies`
- `app.py:2042` `get_dependency_groups`
- ... and 179 more.

### `@developer_required` — 69 use(s)
- `app.py:1466` `custom_tool`
- `app.py:1478` `connections`
- `app.py:1484` `system_logs`
- `app.py:1489` `data_dictionary`
- `app.py:1636` `custom_data_agent`
- `app.py:1659` `builder_redirect`
- `app.py:1787` `command_center_redirect`
- `app.py:1972` `custom_agent_enhanced`
- `app.py:2208` `save_package`
- `app.py:4726` `add_table`
- `app.py:4761` `delete_table`
- `app.py:4789` `get_table`
- `app.py:4806` `execute_query`
- `app.py:4833` `execute_query_result`
- `app.py:5133` `add_column`
- `app.py:5201` `delete_column`
- `app.py:5229` `delete_table_columns`
- `app.py:5438` `llm_unit_test`
- `app.py:5444` `workflow_tool`
- `app.py:5618` `get_workflow_categories`
- ... and 49 more.

### `@login_required` — 174 use(s)
- `admin_tier_usage.py:95` `decorated_function`
- `agent_communication_routes.py:14` `get_agent_communications`
- `agent_communication_routes.py:92` `get_agent_workflows`
- `agent_communication_routes.py:149` `create_agent_workflow`
- `agent_communication_routes.py:188` `test_agent_communication`
- `agent_knowledge_routes.py:375` `get_agent_knowledge_route`
- `agent_knowledge_routes.py:384` `add_agent_knowledge_route`
- `agent_knowledge_routes.py:501` `update_agent_knowledge_route`
- `agent_knowledge_routes.py:531` `delete_agent_knowledge_route`
- `agent_knowledge_routes.py:557` `agent_knowledge_page`
- `api_keys_config.py:915` `api_keys_config_page`
- `app.py:1525` `dashboard`
- `app.py:1550` `jobs`
- `app.py:1562` `chat_modern`
- `app.py:1569` `data_chat_modern`
- `app.py:1588` `assistants`
- `app.py:1594` `data_assistants`
- `app.py:1627` `submit`
- `app.py:1744` `builder_auto_token`
- `app.py:1905` `cc_auto_token`
- ... and 154 more.
