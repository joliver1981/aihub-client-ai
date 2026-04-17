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
