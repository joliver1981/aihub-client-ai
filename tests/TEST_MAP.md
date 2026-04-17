# Source File → Test File Map

Quick lookup for Claude Code to find the right test for any source file change.

## Has Unit Tests

| Source File | Test File |
|---|---|
| `CommonUtils.py` | `tests/unit/test_common_utils.py` |
| `config.py` | `tests/unit/test_config.py` |
| `connection_secrets.py` | `tests/unit/test_connection_secrets.py` |
| `EmailUtils.py` | `tests/unit/test_email_utils.py` |
| `encrypt.py` | `tests/unit/test_encrypt.py` |
| `feature_flags.py` | `tests/unit/test_feature_flags.py` |
| `nlq_enhancements.py` | `tests/unit/test_nlq_enhancements.py` |
| `notification_client.py` | `tests/unit/test_notification_client.py` |
| `telemetry.py` | `tests/unit/test_telemetry.py` |
| `DataUtils.py` | `tests/unit/test_agent_crud.py` |
| `GeneralAgent.py` | `tests/unit/test_agent_chat.py` |
| `error_handling_system.py` | `tests/unit/test_error_handling_system.py` |
| `request_tracking.py` | `tests/unit/test_request_tracking.py` |
| `response_filter.py` | `tests/unit/test_response_filter.py` |
| `tool_dependency_manager.py` | `tests/unit/test_tool_dependency_manager.py` |
| `caution_system.py` | `tests/unit/test_caution_system.py` |
| `smart_change_detector.py` | `tests/unit/test_smart_change_detector.py` |
| `workflow_command_validator.py` | `tests/unit/test_workflow_command_validator.py` |
| `feedback_system.py` | `tests/unit/test_feedback_system.py` |
| `auth/providers/` | `tests/unit/test_auth_providers.py` |
| `agent_email_tools.py` | `tests/unit/test_agent_email_tools.py` |
| `agent_excel_tools.py` | `tests/unit/test_agent_excel_tools.py` |
| `agent_communication_tool.py` | `tests/unit/test_agent_communication_tool.py` |
| `integration_agent_tools.py` | `tests/unit/test_integration_agent_tools.py` |
| `workflow_trigger_tools.py` | `tests/unit/test_workflow_trigger_tools.py` |
| `secure_config.py` | `tests/unit/test_secure_config.py` |
| `attachment_text_extractor.py` | `tests/unit/test_attachment_text_extractor.py` |
| `excel_utils.py` | `tests/unit/test_excel_utils.py` |
| `excel_update_utils.py` | `tests/unit/test_excel_update_utils.py` |
| `integration_manager.py` | `tests/unit/test_integration_manager.py` |
| `email_agent_dispatcher.py` | `tests/unit/test_email_agent_dispatcher.py` |
| `AppUtils.py` | `tests/unit/test_app_utils.py` |
| `workflow_execution.py` | `tests/unit/test_workflow_execution.py` |
| `SmartContentRenderer.py` | `tests/unit/test_smart_content_renderer.py` |
| `LLMDataEngineV2.py` | `tests/unit/test_llm_data_engine.py` |
| `LLMDataEnvironment.py` | `tests/unit/test_llm_data_environment.py` |
| `LLMQueryEngine.py` | `tests/unit/test_llm_query_engine.py` |
| `LLMAnalyticalEngine.py` | `tests/unit/test_llm_analytical_engine.py` |
| `fast_pdf_extractor.py` | `tests/unit/test_fast_pdf_extractor.py` |
| `DataFrameFileManager.py` | `tests/unit/test_dataframe_file_manager.py` |
| `command_center/memory/route_memory.py` | `tests/unit/test_route_memory.py` |
| `command_center_service/graph/nodes.py` (reroute extraction helper) | `tests/unit/test_reroute_extraction.py` |
| `command_center_service/graph/nodes.py` (capability_router, export_intent_detector mini-LLMs) | `tests/unit/test_capability_router_and_export_intent.py` |

## Has Security Tests

| Source File | Test File |
|---|---|
| `api_keys_config.py` | `tests/security/test_api_key_management.py` |
| `auth_middleware.py` | `tests/security/test_auth_middleware.py` |
| `encrypt.py` | `tests/security/test_encryption.py` |
| `role_decorators.py` | `tests/security/test_role_decorators.py` |
| `app.py` (route auth) | `tests/security/test_route_auth.py` |
| `scheduler_routes.py` (route auth) | `tests/security/test_route_auth.py` |
| `integration_routes.py` (route auth) | `tests/security/test_route_auth.py` |
| `builder_service/execution/executor.py` (multipart) | `builder_service/tests/test_executor_multipart.py` |
| `builder_service/graph/nodes.py` (reference resolution) | `builder_service/tests/test_reference_resolution.py` |

## Has Builder Agent Tests

| Source File | Test File |
|---|---|
| `builder_agent/actions/` | `builder_agent/tests/test_actions.py` |
| `builder_agent/registry/` | `builder_agent/tests/test_registry.py` |
| `builder_agent/planner/` | `builder_agent/tests/test_planner.py` |
| `builder_agent/resolver/` | `builder_agent/tests/test_resolver.py` |
| `builder_agent/validation/` | `builder_agent/tests/test_validation.py` |

## Has MCP Tests

| Source File | Test File |
|---|---|
| `builder_mcp/tool_converter.py` | `builder_mcp/tests/test_tool_converter.py` |
| `builder_mcp/gateway_client.py` | `builder_mcp/tests/test_gateway_client.py` |
| `builder_mcp/` (routes) | `builder_mcp/tests/test_integration.py` |
| `builder_mcp/` (agent) | `builder_mcp/tests/test_agent_integration.py` |
| `builder_mcp/` (protocol) | `builder_mcp/tests/test_protocol.py` |
| `builder_mcp/gateway/` | `builder_mcp/gateway/tests/test_gateway.py` |

## Has Builder Data Tests

| Source File | Test File |
|---|---|
| `builder_data/quality/validator.py` | `builder_data/tests/test_validator.py` |
| `builder_data/quality/cleanser.py` | `builder_data/tests/test_cleanser.py` |
| `builder_data/quality/comparator.py` | `builder_data/tests/test_comparator.py` |
| `builder_data/quality/standardizer.py` | `builder_data/tests/test_standardizer.py` |
| `builder_data/quality/deduplicator.py` | `builder_data/tests/test_deduplicator.py` |
| `builder_data/quality/report.py` | `builder_data/tests/test_report.py` |
| `builder_data/pipeline/models.py` | `builder_data/tests/test_models.py` |
| `builder_data/pipeline/steps/transform.py` | `builder_data/tests/test_transform_step.py` |
| `builder_data/pipeline/steps/filter.py` | `builder_data/tests/test_filter_step.py` |
| `builder_data/pipeline/steps/compare.py` | `builder_data/tests/test_compare_step.py` |
| `builder_data/pipeline/steps/scrub.py` | `builder_data/tests/test_scrub_step.py` |
| `builder_data/pipeline/steps/source.py` | `builder_data/tests/test_source_step.py` |
| `builder_data/pipeline/steps/destination.py` | `builder_data/tests/test_destination_step.py` |
| `builder_data/pipeline/engine.py` | `builder_data/tests/test_pipeline_engine.py` |

## Has E2E Tests (DO NOT run from Claude Code)

| Source File Area | Test File |
|---|---|
| Agent builder UI | `tests/e2e/test_agent_builder.py` |
| Agent CRUD UI | `tests/e2e/test_agent_crud_flow.py` |
| Assistants UI | `tests/e2e/test_assistants.py` |
| Chat interaction | `tests/e2e/test_chat_interaction.py` |
| Connections CRUD | `tests/e2e/test_connections_crud.py` |
| Core functionality | `tests/e2e/test_core_functionality.py` |
| Custom tools UI | `tests/e2e/test_custom_tools.py` |
| Data assistants | `tests/e2e/test_data_assistants.py` |
| Environment E2E | `tests/e2e/test_environment_e2e_flow.py` |
| Environments | `tests/e2e/test_environments.py` |
| Jobs | `tests/e2e/test_jobs.py` |
| Jobs CRUD | `tests/e2e/test_jobs_crud.py` |
| Navigation | `tests/e2e/test_navigation.py` |
| RBAC | `tests/e2e/test_rbac.py` |
| Settings pages | `tests/e2e/test_settings_pages.py` |
| Smoke tests | `tests/e2e/test_smoke.py` |
| Universal assistant | `tests/e2e/test_universal_assistant.py` |
| User management | `tests/e2e/test_user_management.py` |
| Workflow assistant | `tests/e2e/test_workflow_assistant.py` |
| Workflow builder UI | `tests/e2e/test_workflow_builder_ui.py` |
| Workflow integration | `tests/e2e/test_workflow_integration.py` |
| Workflow nodes | `tests/e2e/test_workflow_nodes.py` |
| Workflows | `tests/e2e/test_workflows.py` |

## Needs Tests — Priority Order

### Tier 1: Pure logic, easy to test

| Source File | Proposed Test File |
|---|---|
| `workflow_validation_config.py` | `tests/unit/test_workflow_validation_config.py` — *may be dead code, verify before testing* |

### Tier 2: High value, moderate mocking

| Source File | Proposed Test File |
|---|---|
| `agent_knowledge_integration.py` | `tests/unit/test_agent_knowledge_integration.py` — *heavily DB-dependent, low ROI for unit tests* |

### Tier 3: Complex mocking, high impact

*All Tier 3 modules now have tests — see "Has Unit Tests" above.*

### Not testable via unit tests (skip)

These require a running application server, database, or browser:
- `app.py`, `app_agent_api.py`, `app_doc_api.py`, `app_executor_service.py` (Flask apps)
- `wsgi*.py`, `run*.py` (entry points)
- `*_routes.py` files (route handlers — best tested via E2E)
- `copy_onnxruntime.py`, `download_vendor_assets.py`, `prepare_python_bundle.py` (build scripts)
