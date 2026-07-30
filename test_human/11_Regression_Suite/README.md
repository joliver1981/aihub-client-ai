# 11 — Pre-Release Regression Suite (human / agent, UI-driven)

**Purpose.** A fast, repeatable pass over the **basic features** of AI Hub, run **through the real
UI like a human**, so you can confirm *nothing regressed* before cutting a release. Every check has an
exact action, an exact prompt, and an **exact expected value** grounded in the live test databases or
the bundled fixtures — no guessing whether an answer is "close enough."

This is a **smoke/regression** pass, not a competency deep-dive. For the deep behavioral/honesty
tests see packs `08_Automations_Studio`, `09_Code_Flows`, `10_Native_CC_Workflows`.

> **Also run before a release:** `../14_Workflow_Node_Matrix/` — the **workflow node regression
> matrix** (every engine node type + common pairings executed with exact oracles, baseline-diffed
> per run). It catches "a node quietly stopped working" — the class of breakage this UI pass can't
> see. One command: `runner.py`; report lands in `REPORT_LATEST.md`.

---

## Who runs this

- **A human tester** clicking through the browser, or
- **An AI agent** driving the browser tools (the in-app Browser pane `mcp__Claude_Browser__*`, or
  Claude-in-Chrome `mcp__claude-in-chrome__*`). Every step is written so an agent can execute and
  self-grade it: navigate to a URL, type an exact prompt, read the page, compare to the expected
  value in this pack.

> **Golden rule (same as packs 09/10):** drive the **actual UI**. Do not call REST endpoints in place
> of clicking, and never mark a check ✅ from what the reply *claims* — confirm the real artifact
> (the file on disk, the row on the page, the value in the DB). A confident wrong number is a FAIL.

---

## What's covered (maps to the release checklist)

| File | Feature under test | Requested item |
|------|--------------------|----------------|
| [00_Setup_and_Prerequisites.md](00_Setup_and_Prerequisites.md) | Services up, logins, connection, data assistant, SFTP, secret, fixtures | — |
| [01_All_Pages_Open.md](01_All_Pages_Open.md) | **Every page loads** (nav walk, no 500s) | #8 |
| [02_General_Agent_Chat.md](02_General_Agent_Chat.md) | **General agent chat** | #1 |
| [03_Data_Explorer_Chat.md](03_Data_Explorer_Chat.md) | **Data Explorer chat** (NL→data) | #2 |
| [04_Document_Processing.md](04_Document_Processing.md) | **Document processing** (PDF extraction/Q&A) | #3 |
| [05_Agent_Knowledge_Upload.md](05_Agent_Knowledge_Upload.md) | **Agent knowledge upload** + retrieval | #4 |
| [06_Workflow_Execution.md](06_Workflow_Execution.md) | **Workflow execution** (build + run) | #5 |
| [07_Command_Center_Automation.md](07_Command_Center_Automation.md) | **Create an Automation in Command Center** | #6 |
| [08_Artifacts_CC_and_Agents.md](08_Artifacts_CC_and_Agents.md) | **Artifacts** from Command Center **and** general agents | #7 |
| [09_Portal_Workflows.md](09_Portal_Workflows.md) | **Portal Workflows** | #9 |
| [10_Extras_Smoke.md](10_Extras_Smoke.md) | Connections CRUD, Data Assistant (NL→SQL) chat, scheduling, agent builder, approvals, MCP/Integrations | "anything else" |
| [_ANSWER_KEY.md](_ANSWER_KEY.md) | Consolidated ground truth (live-DB facts + fixture facts) | — |
| [TEST_RUN_TEMPLATE.md](TEST_RUN_TEMPLATE.md) | Blank run report + master scorecard — **copy this per run** | — |

---

## Run order & timing

Run **00 first** (prereqs), then **01** as a gate (if pages don't open, stop and fix). After that the
sections are independent — run all, or just the ones a release touched.

| Section | Approx time |
|---|---|
| 00 Setup | 10 min (once) |
| 01 All pages open | 10 min |
| 02 General agent chat | 8 min |
| 03 Data Explorer | 8 min |
| 04 Document processing | 8 min |
| 05 Agent knowledge upload | 12 min (indexing wait) |
| 06 Workflow execution | 12 min |
| 07 Command Center automation | 15 min |
| 08 Artifacts | 10 min |
| 09 Portal Workflows | 12 min |
| 10 Extras smoke | 15 min |
| **Total** | **~1.5–2 hours** |

---

## Environment (this install)

| Thing | Value |
|---|---|
| Main app | `http://localhost:5001` |
| Command Center | `http://localhost:5091` (opens in a new tab from the sidebar) |
| Login (Developer/admin) | `admin` / `admin` |
| Login (plain User, role 1) | `test` |
| Test SQL Server | `10.0.0.6` — `AIRDB` (retail) / `ERPDB` (finance), login `ai_user` |
| SFTP test server | `127.0.0.1:2222`, `testuser` / `testpass` |
| Fixtures | [`fixtures/`](fixtures/) in this folder (self-contained) |

> These are **local throwaway test creds** — fine to type into the UI. See
> `00_Setup_and_Prerequisites.md` for the full checklist.

---

## Scoring

Copy `TEST_RUN_TEMPLATE.md` to `TEST_RUN_<YYYY-MM-DD>.md` and fill it in as you go.

- Score each check **✅ / ⚠️ / ❌** with a one-line evidence note (the value you saw, a file path, a
  screenshot name).
- **Release-blocking (any one = do not ship):** a page 500s (01), a chat/data/doc answer is
  **confidently wrong** vs the answer key, a build/run reports **success while the real artifact is
  missing or wrong** (honesty), or a whole section can't complete.
- **Pass:** ≥ 90% of checks ✅, zero release-blockers.
- File anything you find as an ai-colab board task (`add-task-to-ai-colab`), noting the section and
  check id (e.g. `REG-06-B2`).

Check ids are `REG-<section>-<step>`, e.g. `REG-02-A1`.

## Note for the agent driver

- Prefer the **in-app Browser pane** (`mcp__Claude_Browser__*`); it's already available and DOM-aware.
- Pages that open **in a new tab** from the sidebar (Command Center, Data Explorer, Workflow Designer,
  Builder) — just `navigate` straight to their URL.
- To confirm file artifacts (Excel/CSV written to `C:\temp\...`, files on the SFTP server), use the
  **Bash/PowerShell** tools — that's the "look at the real thing" step, not a UI click.
- Re-run any live-DB oracle with the `connect_test_sql()` helper in `_ANSWER_KEY.md` if you want to
  confirm a value hasn't drifted (the sales table grows daily; structural facts don't).
