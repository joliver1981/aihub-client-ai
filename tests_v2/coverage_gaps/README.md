# Coverage gap detector

A small static-analysis tool that scans the AI Hub codebase and reports
which production surfaces have **zero references in any test suite**.

It looks at three kinds of surface:

| Surface | Examples |
| --- | --- |
| HTTP routes | `@app.route('/login')`, `@compliance_bp.route('/api/...')`, `@router.get('/sessions')` |
| Environment variables | `os.getenv('API_KEY')`, `os.environ['HOST_PORT']`, `os.environ.get('FOO')` |
| Role decorators | `@admin_required()`, `@developer_required()`, `@login_required`, `@api_key_or_session_required(...)`, `@role_required(...)` |

A route or env var is considered "tested" if its path (normalised — Flask
`<int:id>` rewritten to `{id}`) or name appears as a substring in any file
under `tests/`, `tests_v2/`, `e2e_app_tests/`, or any service's `tests/`
sub-folder. This is intentionally a low bar; the goal is to surface
*completely* untested surfaces, not to certify depth of coverage.

## What it produces

- **`REPORT.md`** — Markdown report, regenerated every time you run the
  pytest suite or the CLI with `--report`.
- **`baseline.json`** — number of untested routes recorded the first time
  you ran the suite. The pytest suite fails if the count climbs above it
  (so a new route can't ship without either a test or a deliberate
  baseline bump).

## Running it

### As tests (the usual way)

```
C:\Users\james\miniconda3\envs\aihub2.1\python.exe -m pytest tests_v2/coverage_gaps/ -v -s
```

The `-s` flag is recommended so the report path and headline numbers print.

### Standalone CLI

```
# Headline numbers to stdout
python tests_v2/coverage_gaps/detector.py

# Full Markdown report to stdout
python tests_v2/coverage_gaps/detector.py --report

# Write report to a file
python tests_v2/coverage_gaps/detector.py --report --output=tests_v2/coverage_gaps/REPORT.md

# Reset the baseline to today's count
python tests_v2/coverage_gaps/detector.py --update-baseline
```

## Where the report lives

`tests_v2/coverage_gaps/REPORT.md` — overwritten on every run.

## Updating the baseline

You'll want to do this after you legitimately accept that a new
untested route exists (e.g. it's a temporary scaffold, a deprecated alias,
or you're prioritising other work).

Either:

- delete `tests_v2/coverage_gaps/baseline.json` and re-run the suite — the
  first test will rewrite it and pass; **or**
- run `python tests_v2/coverage_gaps/detector.py --update-baseline`.

## Limitations

- **Test discovery is string-match-based.** A test that hits `/api`
  technically "covers" every route under `/api/...`. We tolerate this —
  the report flags *completely uncovered* routes, which is the right
  signal for a solo developer choosing where to write the next test.
- **No semantic awareness of `url_for(...)`**. If your tests reach a route
  exclusively via `url_for('view_func')`, we won't notice. (Easy fix: add
  the route string to a comment or docstring in the test.)
- **Blueprint prefixes are only resolved when the `Blueprint(...)` call
  lives in the same file as the routes**. Cross-file `url_prefix` set in
  `register_blueprint(bp, url_prefix='...')` is not currently followed.
  In this codebase that's only `auth_identity_bp` and `integrations_bp`,
  both of which already declare their prefix inline, so the report is
  accurate in practice.
- **FastAPI sub-router prefixes via `include_router(prefix=...)`** aren't
  followed either; in command_center_service no prefixes are used, so we
  match the routes exactly as written.
- **AST parse failures are skipped silently.** If a file is syntactically
  invalid (or uses Python 3.12+ syntax we can't parse), it'll be missed.
