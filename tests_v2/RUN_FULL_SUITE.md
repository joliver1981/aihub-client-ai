# AI Hub — Full Test Suite Run Guide (for AI agents)

You have been pointed at this document because the human wants you to **run the complete AI Hub test suite end-to-end and report back**. This guide is self-contained. Follow it top-to-bottom. Do not improvise paths or guess commands — every command you need is in here.

> **Heads up — this is a long run.** End-to-end, all suites take roughly **3–4 hours of wall-clock time**. The browser/journey suites alone are ~30–45 min. Start the slow ones in the background and work on the quick ones in parallel where the guide tells you to.

---

## 1. Pre-flight checks (do not skip)

Before any test runs, verify the environment. Each item below has the **exact** check command and the **exact** expected output. If any check fails, fix it before continuing — most test failures downstream are actually environment problems, not product bugs.

### 1.1 Python environment

```powershell
$PY = "$env:USERPROFILE\miniconda3\envs\aihub2.1\python.exe"
& $PY --version
```
Expected: `Python 3.11.x`. If you get a "not found" error, the conda env name is wrong or conda isn't at the expected path — ask the user.

**Important:** The older `aihub2` env will **fail silently** on Pydantic-2 modules. Never use it. Always use `aihub2.1`.

### 1.2 Services that must be running

| Service | Port | Required by |
|---|---|---|
| Main Flask app | 5001 | All HTTP-based suites |
| Document API | 5011 | Knowledge upload paths |
| Vector API | 5031 | Knowledge search / competency suites |
| Knowledge service | 5051 | Knowledge endpoints |
| Executor | 5061 | Workflow execution suite |
| MCP gateway | 5071 | MCP-related tests (optional) |
| Command Center | 5091 | Browser journey suite (`tests_v2/journeys/`) |

Quick reachability sweep:
```powershell
foreach ($port in 5001, 5011, 5031, 5051, 5061, 5071, 5091) {
  try {
    $r = Invoke-WebRequest -Uri "http://localhost:$port/health" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
    Write-Host "  :$port → $($r.StatusCode)"
  } catch {
    Write-Host "  :$port → DOWN ($($_.Exception.Message))"
  }
}
```

If any of 5001 / 5011 / 5031 are down, stop and ask the user to start the stack. Browser tests will be skipped automatically if 5091 is down — that's fine if you're not running journey tests.

### 1.3 Credentials

```powershell
$env:API_KEY = "DB27D555-03A8-446E-9C23-8DAAA95EAD21"   # local dev API key
# Test user: admin / admin (default local)
# LDAP test user: einstein / password (via ldap.forumsys.com)
```

Most tests pick these up from the running app's environment. You don't usually need to export them yourself, but if a test fails with 401 / 403 errors, set `$env:API_KEY` and retry.

### 1.4 Playwright browser dependency (only if running journeys)

```powershell
& $PY -m playwright install chromium
```
One-time. Cached after first install.

### 1.5 Working directory

Run everything from the repo root:
```powershell
cd C:\src\aihub-client-ai-dev
```
All commands below assume this directory.

---

## 2. The test suite at a glance

The test suite lives under `tests_v2/` (the modern catalogue) plus legacy `tests/` and `builder_*/tests/` which are not in scope for this run. Within `tests_v2/`:

| # | Suite | Type | Wall time | Browser? | What it covers |
|---|---|---|---|:---:|---|
| 1 | `tests_v2/unit/` | Unit (mocked) | ~1:30 | No | 1,200+ pure unit tests — compliance, jobs, CC chat, geocoder, DCA, SharePoint, smart renderer, text chunker |
| 2 | `tests_v2/api/` | Flask test client | ~30s | No | 146 route tests — compliance, workflows, integrations, solutions APIs |
| 3 | `tests_v2/security/` | HTTP smoke | ~1 min | No | Auth, RBAC, tenant isolation, secret-store assertions |
| 4 | `tests_v2/integration/` | HTTP integration | ~3 min | No | Cross-service end-to-end flows |
| 5 | `tests_v2/data_lifecycle/` | HTTP integration | ~3 min | No | CRUD lifecycle tests, scheduler edge cases |
| 6 | `tests_v2/auth_e2e/` | HTTP + LDAP | ~2 min | No | Real LDAP login flows, role mapping |
| 7 | `tests_v2/workflow/` | HTTP integration | ~3 min | No | Workflow nodes, expressions, loops, errors |
| 8 | `tests_v2/competency/` | HTTP + agent | ~30–80 min | No | **The "does the AI actually get it right?" suite.** Per format, scores agent Q&A against ground truth: Excel, Word, PDF, Data Assistant (legacy + v2), Workflow, Large Invoices (Method A + B). |
| 9 | `tests_v2/ui/` | Playwright | ~5 min | **Yes** | Small UI-only checks |
| 10 | `tests_v2/journeys/` | Playwright | ~30–45 min | **Yes** | 44 files — admin onboarding, "test like a human" exploratory journeys, chaos tests, no-reload UI regression, multi-step user flows |

**Do NOT run all of `tests_v2/` in one pytest invocation.** Some test files have identical filenames across categories (e.g. `conftest.py`, helpers); pytest's collection collides on those and you get spurious failures. Run each top-level directory separately, or the specific test file you want.

---

## 3. The exact run sequence

Run the suites in this order. The order is chosen so:
- Quick suites run first (you get a signal fast)
- The browser suites run last (they're slow and don't gate the HTTP-only ones)
- Long competency runs go in the background while you work the quick suites

### 3.1 Quick foundational suites (parallel-safe, total ~6 min)

Open **three** PowerShell windows and run these three in parallel — they don't share state:

**Window A — unit tests:**
```powershell
$PY = "$env:USERPROFILE\miniconda3\envs\aihub2.1\python.exe"
& $PY -m pytest tests_v2\unit\ -v --tb=short 2>&1 | Tee-Object -FilePath "$env:TEMP\run_unit.log"
```

**Window B — API + security:**
```powershell
$PY = "$env:USERPROFILE\miniconda3\envs\aihub2.1\python.exe"
& $PY -m pytest tests_v2\api\ tests_v2\security\ -v --tb=short 2>&1 | Tee-Object -FilePath "$env:TEMP\run_api_security.log"
```

**Window C — integration + data_lifecycle + workflow:**
```powershell
$PY = "$env:USERPROFILE\miniconda3\envs\aihub2.1\python.exe"
& $PY -m pytest tests_v2\integration\ tests_v2\data_lifecycle\ tests_v2\workflow\ -v --tb=short 2>&1 | Tee-Object -FilePath "$env:TEMP\run_integration_workflow.log"
```

When all three return, capture the pass/fail count from each.

### 3.2 Auth (LDAP-dependent, ~2 min)

```powershell
& $PY -m pytest tests_v2\auth_e2e\ -v --tb=short 2>&1 | Tee-Object -FilePath "$env:TEMP\run_auth.log"
```
**If you see `ConnectionError` to `ldap.forumsys.com`:** the public LDAP test server is intermittently down. That's an environment issue, not a product bug — note it in the report and move on.

### 3.3 Competency suites (run sequentially, ~80 min total)

These exercise live AI behavior. Run them **one at a time** (the indexer and vector store don't like parallel uploads):

```powershell
& $PY -m pytest tests_v2\competency\test_competency_agent_knowledge_excel.py -v -s --tb=short    # ~5 min
& $PY -m pytest tests_v2\competency\test_competency_agent_knowledge_word.py -v -s --tb=short     # ~5 min
& $PY -m pytest tests_v2\competency\test_competency_agent_knowledge_pdf.py -v -s --tb=short      # ~5 min
& $PY -m pytest tests_v2\competency\test_competency_data_assistant_nl_to_sql.py -v -s --tb=short # ~5 min
& $PY -m pytest tests_v2\competency\test_competency_data_explorer_v2_nl_to_sql.py -v -s --tb=short  # ~5 min
& $PY -m pytest tests_v2\competency\test_competency_workflow_execution.py -v -s --tb=short       # ~5 min
& $PY -m pytest tests_v2\competency\test_competency_large_invoice_reports.py -v -s --tb=short    # ~25-30 min (slow: 8 fixtures, ~60 questions)
& $PY -m pytest tests_v2\competency\test_competency_large_invoice_reports_isolated.py -v -s --tb=short  # ~45-50 min (slowest: 1 fresh agent per fixture)
```

**Competency-suite specifics you must know:**

- Every competency suite writes a markdown report to `tests_v2/artifacts/competency/<suite>_competency_report.md`. Read it after each run — that's where per-question detail lives.
- Each suite has a configured **score floor**. Pytest will mark the run FAILED if the overall score drops below floor — but the report still gets written. Look at the report regardless of pytest's pass/fail to understand WHY a question failed.
- The two large-invoice suites use a hybrid grading approach: regex fast-path for cheap cases, **mini-LLM grader fallback** for cases where the agent's answer is semantically correct but didn't pattern-match the test regex (e.g., agent says "23.77%" but regex wanted "23.7%/23.8%"). Questions graded via the LLM are marked **✅🤖** in the output stream. This is intentional and reliable — the LLM grader is deterministic at temp=0.
- The "Method A" multi-doc suite vs "Method B" isolated suite is intentional: Method A stress-tests cross-document disambiguation; Method B isolates per-fixture extraction quality. If Method A scores much lower than Method B, that's a known multi-doc retrieval issue (see `large_invoice_reports_comparison.md`).

### 3.4 UI smoke (~5 min, browser required)

```powershell
& $PY -m pytest tests_v2\ui\ -v --tb=short 2>&1 | Tee-Object -FilePath "$env:TEMP\run_ui.log"
```
Skipped automatically if port 5091 (Command Center) is unreachable.

### 3.5 Browser journeys (~30–45 min, browser required)

This is the BIG one. Real Playwright + headless Chromium drives the UI through admin onboarding, "test like a human" exploratory journeys, chaos scenarios, no-reload UI regressions, and full feature tour.

```powershell
& $PY -m pytest tests_v2\journeys\ -v --tb=short 2>&1 | Tee-Object -FilePath "$env:TEMP\run_journeys.log"
```

**Journey-suite specifics you must know:**

- The whole suite skips if either the main app (5001) or Command Center (5091) is unreachable. The `conftest.py` does the check.
- Default test user: `admin` / `admin`. Override with `$env:UI_TEST_USER` / `$env:UI_TEST_PASSWORD` if the dev install uses different creds.
- Failures dump Playwright traces + screenshots to `tests_v2/artifacts/journeys/`. **Always check these on a failed journey** — the Playwright trace tells you what actually happened on screen, far more useful than the assertion error.
- Some tests cover known-skip behavior (e.g., two-tabs-same-session is intentionally not implemented; the test asserts that and skips). Skips with a clear reason are fine — don't treat them as failures.

---

## 4. Where everything lands

After all suites finish, results are scattered across these locations:

| Type | Location |
|---|---|
| Live console output | `$env:TEMP\run_*.log` (one per suite — see `Tee-Object` paths above) |
| Competency markdown reports | `tests_v2\artifacts\competency\*_competency_report.md` |
| Journey screenshots / Playwright traces | `tests_v2\artifacts\journeys\` |
| Coverage gap report (auto-regenerated) | `tests_v2\coverage_gaps\REPORT.md` |
| Auto comparison docs (large-invoice suite only) | `tests_v2\artifacts\competency\large_invoice_reports_comparison.md` |

**The single most important reference doc** when interpreting results: `tests_v2\TEST_SUITE_INVENTORY.md`. It catalogues every suite, every known bug, and every known-skip with rationale. **If you see a test fail and you're not sure if it's a new bug or a known issue, check the inventory's bug ledger first.**

---

## 5. Interpreting results

### 5.1 Pass / fail criteria

- **Pytest exit code 0** = all tests passed (or were correctly skipped with a known reason).
- **Pytest exit code 1** = at least one test failed. Doesn't mean the whole run is invalid — check WHICH tests failed.
- **Competency floor breach** = overall score below the suite's floor (typically 50%). The auto-report is still written; read it to understand which questions failed.
- **Journey screenshot dump** = a browser test failed and saved evidence under `tests_v2\artifacts\journeys\`. Always look at the screenshot before declaring a bug.

### 5.2 Known categories of "failure that isn't really a failure"

You **will** see some of these. Don't panic:

| Pattern | What it means | What to do |
|---|---|---|
| `ConnectionError` to `ldap.forumsys.com` | Public LDAP test server intermittently down | Note + move on; not a product bug |
| `BUG-WORKFLOW-DB-UNKNOWN-ERROR` failure | Known intermittent SQL connection-pool issue | Re-run the single test; if it passes, document as intermittent |
| `BUG-DATA-ASSISTANT-AGG-500` on legacy `/chat/data` | Known bug, has open ticket | Note as known-bug, not new |
| Competency suite "❌" with response time <5s and agent text says "Which document do you mean?" | Cross-doc disambiguation in multi-doc agent (BUG-NEEDLE-WRONG-DOC-AFTER-CLARIFY) | Known issue; documented in inventory |
| `pytest.skip("AGENT_KNOWLEDGE_QA_LIVE not set")` and similar env-gated skips | Test requires a manually-enabled env var to run live calls | Skip is correct; this is by-design opt-in |
| Two-tabs-same-session journey skipped | Cross-tab sync is intentionally not implemented (product decision) | Skip is correct |

### 5.3 Categories of "this IS a new failure"

- Anything in `tests_v2/security/` failing — security suites should always pass
- Anything in `tests_v2/api/` failing — Flask routes either work or they don't
- A competency suite scoring **below baseline** documented in `large_invoice_reports_comparison.md` (or per-suite summary docs)
- Any journey test that previously passed and now fails with a screenshot showing a clearly broken UI state

**When in doubt:** capture the full failure (assertion, stack trace, screenshot if any), search the bug ledger in `TEST_SUITE_INVENTORY.md` for a matching `BUG-*` ID, and if not found, treat as a new finding.

---

## 6. Final report — exactly what to send back to the human

When all suites have finished, write **one consolidated report** in this format and send it as your final message. Don't omit sections; if a section had nothing to report, write "none" explicitly.

```
# Full Test Suite Run — <date>

## Headline
- Total runtime: <X hours Y min>
- Overall: <N> passed, <M> failed, <K> skipped across all suites
- New failures (not in bug ledger): <count>
- Known-issue failures: <count>

## Per-suite results

| Suite | Tests | Pass | Fail | Skip | Wall time | Notes |
|---|---:|---:|---:|---:|---:|---|
| unit | ... | ... | ... | ... | ... | ... |
| api | ... | ... | ... | ... | ... | ... |
| security | ... | ... | ... | ... | ... | ... |
| ...one row per suite... | | | | | | |

## Competency scores

| Suite | Overall | vs. baseline | Notes |
|---|---:|---:|---|
| excel | X% | +/− Y pp | ... |
| word | X% | ... | ... |
| pdf | X% | ... | ... |
| ...one row per competency suite... | | | |

## New failures (not in bug ledger)

For each: file:line, what failed, the actual assertion or screenshot path, your best guess at root cause.

## Known-issue failures
For each: BUG-* ID it matches, brief one-line confirmation it's the same issue.

## Environment notes
Anything unusual: services that were slow to start, LDAP timeouts, etc.

## Recommendation
One short paragraph: is the build shippable as-is, does it need fixes first, etc.
```

---

## 7. Common gotchas you'll hit

These have all bitten previous runs. Internalize them:

1. **The conda env is `aihub2.1`, NOT `aihub2`.** The older env's Pydantic 1 will break half the modules silently. If imports fail mysteriously, check `$PY` points at `aihub2.1`.
2. **Never run all of `tests_v2/` in one pytest invocation.** Filename collisions across subdirectories cause spurious test-collection failures. Run per-directory.
3. **Flask code changes need a restart.** Tests hit the running server over HTTP — they don't reload code. If you make a product code change mid-run, the test still sees the OLD code until Flask restarts. If a test is checking new behavior and failing, ask the user "did you restart Flask?".
4. **Indexer is asynchronous.** After uploading a knowledge document, the chunk-indexing happens on a background worker thread. The competency suites already wait 240s by default for the indexer — don't try to "speed up" by reducing this; you'll get false-negative test failures with no chunks in the vector store.
5. **Browser tests need port 5091.** The journey suite's conftest skips the entire suite if Command Center is down. That's fine if you're not running journeys; not fine if you are.
6. **Some competency questions intentionally use deictic phrasing** ("the grand total on this invoice"). When multiple similar fixtures coexist, the agent will sometimes ask "which one?". The test runner's `ask_with_followup` helper automatically replies with the disambiguation hint and re-scores. Output marked **💬** = follow-up was sent. This is by-design and the test framework already handles it; you don't need to do anything special.
7. **LLM-graded questions show as ✅🤖** in the competency output stream. That means the regex fast-path missed but the LLM grader confirmed the answer is semantically correct. These are valid passes. Don't try to "fix" the regex unless you see a pattern of LLM-grader rescues that indicate a genuine regex bug.
8. **Tests in `tests_v2/live/` are NOT in the standard run.** They're manually-curated probes for live debugging. Skip them unless the user asks specifically.

---

## 8. If something goes badly wrong

- Test process hangs > 30 min on a single test: kill the process, log which test was running, move on. Don't restart Flask unless the hang is in an HTTP call that's not returning.
- Vector API returns 500 on every knowledge upload: check `logs/skr_trace.txt` and the main app's `logs/app_log.txt` for `FAILED to index` lines. Common cause: oversized chunks > 8192 tokens hitting the embedding limit. Phase 0 of the pipeline now logs these loudly so they're easy to spot.
- ChromaDB collection corruption: run `scripts\reindex_knowledge.py --dry-run` to see how many docs are affected; if any, ask user before running the actual reindex (it queues 200+ documents which takes 30–60 min).

---

## 9. TL;DR for impatient agents

If you only read one section, read this:

1. `cd C:\src\aihub-client-ai-dev`
2. `$PY = "$env:USERPROFILE\miniconda3\envs\aihub2.1\python.exe"`
3. Verify ports 5001 / 5011 / 5031 are up.
4. Run section 3.1 → 3.5 commands top-to-bottom.
5. Open every `tests_v2\artifacts\competency\*.md` after the competency suites.
6. Write the report from section 6 and send it.

That's the job. Now go.
