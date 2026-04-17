# AI Hub — Claude Code Instructions

## Testing Protocol

After modifying any `.py` file:
1. Check `tests/TEST_MAP.md` for the corresponding test file
2. If a test file exists, run it: `python -m pytest <test_file> -v --tb=short -x`
3. If the test fails, analyze the failure and fix the test or the code
4. If no test file exists and the change touches testable logic (not route handlers, templates, or wsgi entry points), offer to create a unit test

**Never run E2E tests** (`tests/e2e/`) — they require a live application server, database, and Playwright browser.

### Test Commands

```bash
# Unit tests
python -m pytest tests/unit/ -m unit -v --tb=short -x

# Security tests
python -m pytest tests/security/ -v --tb=short -x

# Builder agent tests
python -m pytest builder_agent/tests/ -v --tb=short -x

# MCP tests
python -m pytest builder_mcp/tests/ -v --tb=short -x

# All safe tests (no E2E)
python -m pytest tests/unit/ tests/security/ builder_agent/tests/ builder_mcp/tests/ -v --tb=short

# Coverage for a specific module
python -m pytest tests/unit/test_X.py --cov=X --cov-report=term-missing -v --tb=short
```

### Pre-Commit Test Check

When asked to run tests before a commit:
1. Run `git diff --name-only` to find modified `.py` files
2. Look up each file in `tests/TEST_MAP.md`
3. Run all corresponding test files
4. Report pass/fail summary

### Test Conventions

When writing new unit tests, follow these patterns (see `tests/unit/test_common_utils.py` as the style reference):

- **Module-level mocks before import**: Mock heavy dependencies (`config`, `app_config`, `system_prompts`, `pyodbc`) via `sys.modules.setdefault()` before importing the module under test
- **Class organization**: Group related tests in `@pytest.mark.unit` decorated classes named `TestClassName`
- **Fixtures**: Use shared fixtures from `tests/unit/conftest.py` (`mock_cursor`, `mock_db_connection`, `mock_db`, `mock_llm_response`, `flask_app`, `clean_env`, etc.)
- **Error handling**: Tool callables should never raise — they return error strings for the agent to see. Test this pattern.
- **No live dependencies**: All unit tests must work offline without database, LLM API keys, or running services
- **File placement**: `tests/unit/test_<module_name>.py`

### Test Priority for New Coverage

See the "Needs Tests" section in `tests/TEST_MAP.md` for prioritized list of untested modules.
