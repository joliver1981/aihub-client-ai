# Test Cheat Sheet

## Quick Commands

```bash
# Activate environment first
conda activate aihub2

# ── Run everything safe (no E2E) ──────────────────────────────
pytest tests/unit/ tests/security/ builder_agent/tests/ builder_mcp/tests/ -v --tb=short

# ── By suite ──────────────────────────────────────────────────
pytest tests/unit/ -v --tb=short                # Unit tests (239 tests)
pytest tests/security/ -v --tb=short            # Security tests (113 tests)
pytest builder_agent/tests/ -v --tb=short       # Builder agent tests (285 tests)
pytest builder_mcp/tests/ -v --tb=short         # MCP tests (105 tests)

# ── By marker ─────────────────────────────────────────────────
pytest -m unit -v --tb=short                    # Only @pytest.mark.unit
pytest -m security -v --tb=short                # Only @pytest.mark.security
pytest -m crud -v --tb=short                    # Only @pytest.mark.crud

# ── Single file ───────────────────────────────────────────────
pytest tests/unit/test_common_utils.py -v --tb=short
pytest tests/unit/test_agent_crud.py -v --tb=short
pytest tests/unit/test_agent_chat.py -v --tb=short

# ── Single test class or function ─────────────────────────────
pytest tests/unit/test_agent_crud.py::TestGetAgentConfig -v --tb=short
pytest tests/unit/test_agent_crud.py::TestGetAgentConfig::test_returns_agent_with_tools -v

# ── Stop on first failure ─────────────────────────────────────
pytest tests/unit/ -v --tb=short -x

# ── Coverage for a module ─────────────────────────────────────
pytest tests/unit/test_common_utils.py --cov=CommonUtils --cov-report=term-missing -v

# ── Keyword search (run tests matching a pattern) ─────────────
pytest tests/unit/ -k "error" -v --tb=short     # All tests with "error" in the name
pytest tests/unit/ -k "chat" -v --tb=short      # All tests with "chat" in the name
```

> **Never run E2E tests** (`tests/e2e/`) — they require a live app server, database, and Playwright browser.

---

## Test Suites at a Glance

### tests/unit/ — 11 files, ~239 tests
Core application logic, all mocked, no external dependencies.

| File | Tests | What it covers |
|------|-------|----------------|
| `test_agent_chat.py` | 25 | GeneralAgent chat history, error detection, tool introspection, run flow |
| `test_agent_crud.py` | 14 | Agent create/read/update/delete via DataUtils.py |
| `test_common_utils.py` | 36 | Utility functions: logging, DB helpers, URL builders |
| `test_config.py` | 18 | Configuration loading, env var handling |
| `test_connection_secrets.py` | 47 | Credential storage, masking, secret references |
| `test_email_utils.py` | 15 | Email sending, SMTP config, template rendering |
| `test_encrypt.py` | 21 | Encryption/decryption utilities |
| `test_feature_flags.py` | 13 | Feature flag parsing and defaults (**has pre-existing SyntaxError in Python 3.11**) |
| `test_nlq_enhancements.py` | 8 | Natural language query processing |
| `test_notification_client.py` | 18 | Notification service client |
| `test_telemetry.py` | 24 | Telemetry collection and reporting |

### Data Collection Agent (DCA) tests — 8 unit + 1 security file, 448 tests

Schema-driven conversational data collection. All external services (DB,
SMTP, Cloud API, notification engine) are mocked. The JWT-dependent
subset auto-skips when PyJWT isn't installed in the env.

| File | Tests | What it covers |
|------|-------|----------------|
| `tests/unit/test_dca_validation_engine.py` | 107 | Field coercion, every rule handler, validate_field orchestrator, conditional visibility, section completeness |
| `tests/unit/test_dca_schema_loader.py` | 36 | Load/save/delete schemas, inline + file + database lookup resolution, section/field navigation, id sanitization |
| `tests/unit/test_dca_state_manager.py` | 34 | Session CRUD, field updates, status transitions, chat history, concurrent saves, session-id sanitization |
| `tests/unit/test_dca_db_lookup.py` | 76 | SQL identifier whitelist (injection firewall), filter operators (eq/ne/gt/lt/contains/in/isnull), template interpolation, query orchestration with pyodbc mocked |
| `tests/unit/test_dca_auth_token.py` | 0 (skipped — PyJWT) | JWT encode/decode round-trip, expired, bad signature, wrong audience, claim extraction |
| `tests/unit/test_dca_branding.py` | 43 | Resolution hierarchy (defaults < app < schema < jwt), env-var overrides, CSS sanitization, javascript:/data: URL rejection |
| `tests/unit/test_dca_actions.py` | 60 | Action registry, pipeline execution, continue_on_error semantics, template substitution, every action's validate_config |
| `tests/unit/test_dca_schema_validator.py` | 33 | Top-level required, id format, branding rules, section/field validation, conditional refs, action handler delegation |
| `tests/security/test_dca_identity_and_isolation.py` | 59 (+5 skipped — PyJWT) | Identity resolution priority, session ownership enforcement, JWT-never-admin guarantee, SQL identifier whitelist, JWT rejection paths, path traversal defense |

**DCA live/CC chat test plan:** `e2e_app_tests/production_readiness_round2/24_DCA_COLLECTION_FLOW.md` + `module24_tests.json` — 16 scenarios covering greeting, value resolution (id/label/fuzzy), conditional fields, custom-tool research, back-and-edit, recap, submission. Run via `cc_api_batch.py`.

### tests/security/ — 4 files, ~113 tests
Authentication, authorization, encryption, and access control.

| File | Tests | What it covers |
|------|-------|----------------|
| `test_api_key_management.py` | 26 | API key creation, validation, rotation |
| `test_auth_middleware.py` | 24 | Request auth middleware (**22 pre-existing errors — endpoint collision**) |
| `test_encryption.py` | 15 | Data encryption at rest |
| `test_role_decorators.py` | 48 | @admin_required, @developer_required, @role_required, API key decorators |

### builder_agent/tests/ — 5 files, ~285 tests
Builder agent planning, actions, validation, and dependency resolution.

| File | Tests | What it covers |
|------|-------|----------------|
| `test_actions.py` | 74 | Builder agent action execution |
| `test_planner.py` | 36 | Build plan generation |
| `test_registry.py` | 50 | Action/tool registry |
| `test_resolver.py` | 72 | Dependency resolution |
| `test_validation.py` | 53 | Input/output validation |

### builder_mcp/tests/ — 5 files, ~105 tests
MCP gateway client, tool conversion, and agent integration.

| File | Tests | What it covers |
|------|-------|----------------|
| `test_agent_integration.py` | 19 | get_mcp_tools_for_agent, system prompt additions, auth headers |
| `test_gateway_client.py` | 22 | MCPGatewayClient HTTP calls, health check, server management |
| `test_integration.py` | 7 | End-to-end MCP flow (mocked) |
| `test_protocol.py` | 20 | MCP protocol message handling |
| `test_tool_converter.py` | 37 | JSON Schema → Pydantic → LangChain StructuredTool conversion |

### tests/e2e/ — 23 files, ~466 tests ⚠️ DO NOT RUN
Playwright browser tests requiring live application. Run only in CI or manually with a running server.

---

## Pytest Markers

| Marker | Description | Example usage |
|--------|-------------|---------------|
| `unit` | Pure unit tests, no external deps | `pytest -m unit` |
| `security` | Security-focused tests | `pytest -m security` |
| `crud` | Create/read/update/delete lifecycle | `pytest -m crud` |
| `integration` | Tests requiring service interaction | `pytest -m integration` |
| `workflow` | Workflow builder/execution | `pytest -m workflow` |
| `smoke` | Quick sanity checks | `pytest -m smoke` |
| `slow` | Long-running tests | `pytest -m "not slow"` (exclude) |
| `e2e` | End-to-end browser tests | **Never run locally** |
| `ui` | UI functional tests | **Never run locally** |
| `auth` | Tests requiring authentication | `pytest -m auth` |
| `rbac` | Role-based access control | `pytest -m rbac` |

---

## Known Issues

| Issue | File | Status |
|-------|------|--------|
| SyntaxError in Python 3.11 (nested f-string quotes) | `test_feature_flags.py` | Exclude with `--ignore=tests/unit/test_feature_flags.py` |
| 22 errors from Flask endpoint collision | `test_auth_middleware.py` | Pre-existing, does not affect other tests |

---

## Useful Patterns

```bash
# Run tests for files you changed (pre-commit check)
git diff --name-only | grep '\.py$'
# Then look up each file in tests/TEST_MAP.md and run the corresponding test

# Run with verbose failure output
pytest tests/unit/test_agent_crud.py -v --tb=long

# Run and show print() output
pytest tests/unit/test_agent_crud.py -v -s

# Run tests in parallel (requires pytest-xdist: pip install pytest-xdist)
pytest tests/unit/ -n auto -v --tb=short

# List all tests without running them
pytest tests/unit/ --collect-only
```
