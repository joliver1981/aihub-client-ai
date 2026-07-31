# 15 — Platform Regression Matrix (the all-areas pre-build gate)

**One command, whole platform.** Pack 14 proves every workflow node still executes; this pack does
the same for **every other subsystem** — auth, all 33 pages, agents (CRUD + live chat + artifact
creation), knowledge ingest, connections (CRUD + live query), NL→SQL data chat, documents API,
automations (full lifecycle **+ the output-verification honesty probe**), code flows, portal
workflows (persist + dup-409 guard), approvals, scheduler, secrets, users/groups, MCP, Command
Center and Builder services. Each check drives the same HTTP surface the UI calls, with a
deterministic oracle and cleanup (`REGP-*` namespace). It also **executes pack 14 and merges its
rows**, so the report covers engine + platform together.

```bash
cd C:\src\aihub-client-ai-dev\test_human\15_Platform_Regression
C:\Users\james\miniconda3\envs\aihub2.1\python.exe runner.py
```

Flags: `--only substr` · `--skip-wf14` (skip the engine leg) · `--skip-llm` (skip the 3 checks that
make live LLM calls: agent chat math, artifact creation, NL→SQL probe).

**Reading the report** (`REPORT_LATEST.md` + `results_history/`): same semantics as pack 14 —
🔴 REGRESSIONS = was PASS in the previous run, now broken (exit 2; the pre-build stop signal);
⚠️ XFAIL = known bug tripwire (flips 🟡 XPASS when fixed); ⏭ SKIP always carries its reason,
including the deliberately-not-automated rows (email pipeline, integrations internal API,
compliance/solutions/environments deep paths — deep NLQ and doc-QA competency live in packs 12/13).

**Pre-build ritual:** run this (it includes pack 14), then the pack-11 UI legs for anything the
release touched. CLEAN or explained = build; REGRESSIONS = stop.
