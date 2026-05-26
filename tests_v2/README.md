# tests_v2 — Comprehensive Test Suite

A deep-coverage test suite that complements the existing `tests/`, `builder_agent/tests/`, `builder_data/tests/`, `builder_mcp/tests/`, and `builder_service/tests/` suites. Targets the new/heavily-modified surfaces that have minimal coverage in the legacy suites.

## Layout

```
tests_v2/
├── unit/              Pure unit tests, all external deps mocked
├── api/               Flask test-client / FastAPI TestClient (no live server)
├── integration/       Multi-module behaviour (DB/HTTP still mocked)
├── security/          Authz, isolation, injection, secret handling
├── migrations/        SQL migration safety + idempotency
├── live/              Tests that require running services (manual / scheduled)
├── fixtures/          Sample artifacts (PDFs, schemas, payloads)
└── conftest.py        Shared fixtures and mocks
```

## Environment

Run with the `aihub2.1` conda env (Pydantic 2, pandas 2.3, pytest-asyncio installed):

```
C:\Users\james\miniconda3\envs\aihub2.1\python.exe -m pytest tests_v2/unit -v --tb=short
```

## Running

```
# All non-live tests
pytest tests_v2/unit tests_v2/api tests_v2/integration tests_v2/security tests_v2/migrations -v

# By area
pytest tests_v2/unit/test_compliance_engine.py -v
pytest tests_v2/security -v

# Migrations against a scratch DB (set TEST_DB_CONN)
pytest tests_v2/migrations -v
```

## What's covered

See [COVERAGE.md](COVERAGE.md) for the full mapping of source files to test files and the rationale behind each suite.
