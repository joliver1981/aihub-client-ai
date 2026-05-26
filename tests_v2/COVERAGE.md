# tests_v2 Coverage Map

A complete view of which source files are tested where, what's covered, and the known gaps. Built 2026-05-13 against `aihub2.1` (Pydantic 2.12.3, pandas 2.3.3, pytest 9.0.2 + pytest-asyncio 1.3.0).

## Headline numbers

| Layer | Tests |
|---|---|
| `tests_v2/unit/` | 1,204 passed, 1 skipped, 1 xfailed |
| `tests_v2/api/` | 146 passed |
| `tests_v2/security/` | 208 passed, 2 skipped, 2 xfailed |
| `tests_v2/integration/` | 11 passed |
| `tests_v2/migrations/` | 121 passed |
| **tests_v2 total** | **1,690 passed** (3 skipped, 3 xfailed documenting real bugs) |
| Legacy (pre-existing + DCA agent additions) | 2,731 passed |
| **Combined platform total** | **4,421 passing tests** |

Live test plans (markdown + JSON, executed against a running stack): 8 plans, ~95 scenarios.

## Source → tests map

### Compliance Management (entirely new — 302 tests)
| Source | Tests |
|---|---|
| `compliance_engine.py` | `tests_v2/unit/test_compliance_engine.py` (82) |
| `compliance_jobs.py` | `tests_v2/unit/test_compliance_jobs.py` (22) |
| `compliance_comparison.py` | `tests_v2/unit/test_compliance_comparison.py` (28) |
| `compliance_routes.py` | `tests_v2/api/test_compliance_routes.py` (75) + `tests_v2/security/test_compliance_authz.py` (72) |
| End-to-end pipeline | `tests_v2/integration/test_compliance_pipeline.py` (2) |
| Migrations 009–012 | `tests_v2/migrations/test_migration_009_to_012.py` (21) |
| Live | `tests_v2/live/plans/30_COMPLIANCE_HAPPY_PATH.md` (15 scenarios) + `31_COMPLIANCE_SECURITY.md` (16 scenarios) |

### Workflow Execution & Integrations (heavy modifications — 399 tests)
| Source | Tests |
|---|---|
| `workflow_execution.py` | `tests_v2/unit/test_workflow_execution.py` (133) + `tests_v2/api/test_workflow_routes.py` (32) |
| `workflow_validation_config.py` | `tests_v2/unit/test_workflow_validation_config.py` (22) |
| `integration_manager.py` | `tests_v2/unit/test_integration_manager.py` (41) |
| `integration_template_loader.py` | `tests_v2/unit/test_integration_template_loader.py` (42) |
| `integration_routes.py` | `tests_v2/api/test_integration_routes.py` (31) |
| `sharepoint_executor.py` | `tests_v2/unit/test_sharepoint_executor.py` (62) |
| Authorization | `tests_v2/security/test_workflow_authz.py` (19) + `tests_v2/security/test_integration_authz.py` (9) |
| End-to-end | `tests_v2/integration/test_workflow_full_pipeline.py` (8) |
| Live | `33_WORKFLOW_EXECUTION.md` + `module33_workflow_tests.json`, `34_INTEGRATION_FLOW.md` |

### Command Center Service (new ops room + delta — 233 tests)
| Source | Tests |
|---|---|
| `command_center_service/routes/ops.py` | `tests_v2/unit/test_ops_routes.py` (31) |
| `command_center_service/plugins/web_intelligence/geocoder.py` | `tests_v2/unit/test_geocoder.py` (26) |
| `command_center_service/plugins/web_intelligence/handler.py` | `tests_v2/unit/test_web_intelligence_handler.py` (11, 1 xfail) |
| `command_center_service/routes/chat.py` | `tests_v2/unit/test_cc_chat_route.py` (21) |
| `command_center_service/graph/nodes.py` | `tests_v2/unit/test_graph_nodes.py` (49) |
| `command_center_service/graph/tracing.py` | `tests_v2/unit/test_graph_tracing.py` (27) |
| `command_center/orchestration/delegator.py` | `tests_v2/unit/test_delegator.py` (20) |
| `command_center_service/provenance.py` (gaps) | `tests_v2/unit/test_provenance_edge_cases.py` (21) |
| CC chat security | `tests_v2/security/test_cc_chat_security.py` (16, 1 xfail) |
| Ops authz | `tests_v2/security/test_ops_authz.py` (11, 1 xfail) |
| Live | `32_OPS_ROOM_FLOW.md` + `module32_ops_tests.json` |

### Data Collection Agent gap fill (269 new tests on top of the other agent's 448)
| Source | Tests |
|---|---|
| `data_collection_agent/admin_routes.py` | `test_dca_admin_routes.py` (13) |
| `data_collection_agent/routes.py` | `test_dca_user_routes.py` (30) |
| `data_collection_agent/https_config.py` | `test_dca_https_config.py` (23) |
| `data_collection_agent/debug_mode.py` | `test_dca_debug_mode.py` (26) |
| `data_collection_agent/custom_tool_loader.py` | `test_dca_custom_tool_loader.py` (12) |
| `data_collection_agent/field_extractor.py` | `test_dca_field_extractor.py` (22) |
| `data_collection_agent/voice_normalizer.py` | `test_dca_voice_normalizer.py` (16) |
| `data_collection_agent/voice_settings.py` | `test_dca_voice_settings.py` (32) |
| `data_collection_agent/voice/*.py` | `test_dca_voice_tts.py` (31, covers OpenAI + Azure TTS) |
| `data_collection_agent/builder/builder_agent.py` | `test_dca_builder_agent.py` (8) |
| `data_collection_agent/builder/builder_routes.py` | `test_dca_builder_routes.py` (22) |
| `data_collection_agent/agent.py` | `test_dca_agent.py` (18) |
| `run_dca.py` + `openapi.yaml` | `test_dca_run_smoke.py` (6) |
| Session lifecycle | `tests_v2/integration/test_dca_session_lifecycle.py` (1) |
| Route security | `tests_v2/security/test_dca_route_security.py` (11) |
| Live | `35_DCA_FULL_FLOW.md` + `module35_dca_tests.json` |

### Solutions, Agent Knowledge, Migrations (204 tests)
| Source | Tests |
|---|---|
| `solution_routes.py` | `tests_v2/unit/test_solution_routes.py` (25) + `tests_v2/api/test_solutions_api.py` (8) |
| `agent_knowledge_routes.py` | `tests_v2/unit/test_agent_knowledge_routes.py` (22) |
| `agent_knowledge_integration.py` | `tests_v2/unit/test_agent_knowledge_integration.py` (26) |
| Solution security | `tests_v2/security/test_solution_security.py` (10) |
| Knowledge security | `tests_v2/security/test_agent_knowledge_security.py` (13) |
| Migrations static safety | `tests_v2/migrations/test_migration_safety.py` (88) |
| Migration order | `tests_v2/migrations/test_migration_order.py` (4) |
| Migrations 009–012 dry run | `tests_v2/migrations/test_compliance_migrations_dryrun.py` (8) |
| Live | `37_SOLUTIONS_GALLERY.md`, `38_AGENT_KNOWLEDGE.md` |

### Core utilities modifications (286 tests)
| Source | Tests |
|---|---|
| `universal_assistant.py` | `test_universal_assistant_new.py` (36) |
| `DataUtils.py` | `test_data_utils_new.py` (30) |
| `DocUtils.py` | `test_doc_utils_new.py` (38) |
| `SmartContentRenderer.py` | `test_smart_content_renderer.py` (55) + `tests_v2/security/test_renderer_xss.py` (51) |
| `TextChunker_LLM.py` | `test_text_chunker_llm.py` (28) |
| `GeneralAgent.py` (new gaps) | `test_general_agent_new.py` (12, 1 skipped) |
| `system_prompts.py` | `test_system_prompts.py` (11) |
| `config.py` | `test_config_loading.py` (18) |
| `claudeQuickPrompt.py` | `test_claude_quick_prompt.py` (8) |
| Sample docs | `tests_v2/fixtures/docs/{sample_long.txt, sample_unicode.txt, malformed.html}` |

## Bugs discovered (NOT fixed — recorded for follow-up)

Tests document these as xfail or with `# BUG:` comments so the suite stays green while the bug is on the record.

### Compliance
- **BUG-COMPLIANCE-PATHTRAVERSAL** — `compliance_routes.upload_document` does not run `werkzeug.utils.secure_filename()` before joining the filename onto the upload dir. A `../../../...` filename escapes `${APP_ROOT}/data/compliance_uploads/`.
- **BUG-COMPLIANCE-NULLNAME** — `compliance_routes.create_retailer` calls `data.get("name", "").strip()`. If the JSON body has `"name": null` explicitly, `.get()` returns `None` → `AttributeError` → 500.
- **BUG-COMPLIANCE-NOLENGTHVAL** — No max-length validation at the API layer; 1 MB names crash at the DB layer.

### Workflow / Integration
- **BUG-WORKFLOW-VAR-ARRAYIDX** — `workflow_execution._replace_variable_references` does not handle bare `${items[0]}`; only `${obj.items[0]}` works.
- **BUG-SHAREPOINT-VARNAME** — `sharepoint_executor._health_check` (~line 461) references `sites_resp` before assignment in an unreachable-but-not-unreachable branch (NameError if exercised).
- **BUG-INTEGRATION-PARTIAL-COMMIT** — `integration_manager.create_integration` does not roll back DB row when secret-store write fails afterwards — credential refs may point at non-existent secrets.

### Command Center
- **BUG-CC-OPS-NOAUTH** — `/api/ops/*` has no authn/authz at the application layer (the docstring says "real auth lives at the reverse-proxy layer"). Anyone reaching port 5091 can enumerate session counts, trace IDs, session-points, and the SSE stream.
- **BUG-CC-OPS-NO-TENANT-FILTER** — `/api/ops/feed` walks the whole `data/traces/` tree, exposing any user's recent traces.
- **BUG-CC-SESSION-ID-FORGERY** — `routes/chat.py` does not verify ownership of client-supplied `session_id`; relies on a side-effect in `attach_user_context_if_missing`. An attacker who guesses a fresh unstamped session ID can claim it.
- **BUG-CC-TITLE-SANITIZER** — Auto-title sanitizer strips `<script>...</script>` but leaves the leading `">` artifact. Not exploitable (output is escaped) but violates contract.
- **BUG-WEBINTEL-EXCEPTION-SAFETY** — `geocoder.geocode` doesn't wrap backend calls; a custom raising backend bubbles into the chat pipeline.

### Core utilities (GeneralAgent / DataUtils)
- **BUG-GA-CUSTOMTOOL-UNBOUND** — `GeneralAgent.load_custom_tool` raises `UnboundLocalError` when the tool folder doesn't exist (`config`/`code` referenced unbound).
- **BUG-GA-NOPANDAS** — `GeneralAgent.dataframe_to_csv` calls `pd.DataFrame` but pandas is never imported into GeneralAgent — always raises `NameError`.
- **BUG-DATAUTILS-FINALLY** — `DataUtils.query_app_database` `finally` references `cursor` even when `conn.cursor()` raised, masking the original error.

## Known coverage gaps (intentional)

- **LLM-driven paths** (Anthropic / OpenAI calls inside workflow AI nodes, builder agent, `_enrich_knowledge`, knowledge summarization) are tested only with mocked LLMs. Real LLM behavior is covered by the live plans.
- **OAuth2 token refresh** flows in `integration_manager` require real Azure AD endpoints; only the request-boundary mocks are unit-tested.
- **SubprocessExecution paths** in `SmartContentRenderer.execute_python_code` / `process_blocks_with_execution` are out of scope for unit tests.
- **Real DB tenant context** (`tenant.sp_setTenantContext` row-level security) — contract-tested (the call is observed in mocks), but actual RLS enforcement requires SQL Server (covered by live plans).
- **Routes that live in `app.py`** — `app.py` imports the full AI Hub at module level (matplotlib, GeneralAgent, telemetry, SQLAlchemy ORM). API tests use mini Flask apps that match the route contract; direct `app.py` tests would require heavy module-level mocks.
- **Full async LangGraph nodes** (`classify_intent`, `converse`, `build`) are tested only at the helper level — end-to-end node tests belong in live plans.

## Running

Each sub-suite must be run separately (pytest collection collides on duplicate filenames across suites). Same constraint as the legacy `tests/` directory.

```
PY=C:\Users\james\miniconda3\envs\aihub2.1\python.exe

$PY -m pytest tests_v2/unit -v
$PY -m pytest tests_v2/api -v
$PY -m pytest tests_v2/security -v
$PY -m pytest tests_v2/integration -v
$PY -m pytest tests_v2/migrations -v
```

Live plans require running services (Main 5001, CC 5091, Agent 5041, Builder 8100). Run them via `cc_api_batch.py` from `e2e_app_tests/production_readiness_round2/` using the per-plan `moduleNN_*.json`.
