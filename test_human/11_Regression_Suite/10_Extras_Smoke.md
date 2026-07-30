# 10 — Extras Smoke  ("anything else you can think of")

Quick regression checks on the remaining basic features so a release doesn't ship with one of them
broken. Each is short — do the ones your release touched, or all of them. Log in as `admin` unless a
step says otherwise.

---

## A. Database Connections CRUD

**Where:** `/connections`.

**REG-10-A1 — Create + test.** Add a throwaway connection `REG-Conn-Temp` → Server `10.0.0.6`,
Database `ERPDB`, user `ai_user`, password `Bradynov11`. Click **Test**.
- ✅ Test succeeds (green). The connection saves and appears in the list.

**REG-10-A2 — Delete.** Delete `REG-Conn-Temp`.
- ✅ It's removed from the list and stays gone after reload.

## B. Data Assistant chat (NL→SQL)

**Where:** `/data_assistants` (the classic data-assistant chat — distinct from Data Explorer §03).
Pick the AIRDB assistant (`REG-Data-AIRDB`).

**REG-10-B1 —** Ask: `How many employees are there in total?`
- ✅ **80** (matches AIRDB). A wrong number, or SQL that errors out to the user, = ❌.

**REG-10-B2 —** Ask: `How many stores are there?`
- ✅ **10**.

**REG-10-B3 — Honesty.** Ask: `What is each store manager's home phone number?`
- ✅ Says that data isn't available (no such column). ❌ if it fabricates phone numbers.

## C. Scheduling (Agent Jobs / scheduler)

**Where:** `/jobs`.

**REG-10-C1 — Create a job.** Create a simple agent job (any agent, any small prompt). Save it.
- ✅ The job saves and appears in the jobs list.

**REG-10-C2 — Schedule it.** Add a schedule (e.g. daily) to that job.
- ✅ A schedule is recorded (next-run time shown). *(The `AIHubJobScheduler` service must be running —
  §00.1.)* Deactivate/delete the schedule afterward.

## D. Approvals

**Where:** `/approvals` (My Approvals).

**REG-10-D1 —** The page loads and renders the queue (empty is fine).
- ✅ If you ran §07 with a checkpoint/review step, a **pending item** appears here with the run's
  files/message, and you can **Proceed/Approve** or **Abort/Reject** it. Otherwise just confirm the
  page renders cleanly.

## E. Create an agent end-to-end (Agent Builder)

**Where:** `/custom_agent_enhanced`.

**REG-10-E1 —** Create a minimal general agent `REG-Agent-Temp` with a short system prompt, save it,
then confirm it appears in the **Agent Chat** picker and answers one message.
- ✅ Agent creates, is selectable, and responds. (Covers the build→use loop beyond §05's knowledge
  focus.) Delete it afterward if you like.

## F. Auth + role gating

**REG-10-F1 — Logout/login.** Log out, then log back in as `admin`.
- ✅ Logout returns to `/login`; re-login lands on the dashboard.

**REG-10-F2 — Role gate.** Log in as **`test`** (role 1).
- ✅ The sidebar shows the reduced **Work** set only — **no Build / Admin sections**, no Command
  Center / Solutions (Developer-gated). Confirms role gating didn't regress. Log back in as `admin`.

## G. MCP Servers & Integrations (render + basic op)

**REG-10-G1 —** `/mcp_servers` loads and lists MCP servers (or a clean empty state); the add/config
control opens.
- ✅ Renders and the add dialog opens without a JS error.

**REG-10-G2 —** `/integrations` loads and shows the integrations catalog.
- ✅ Renders; opening one integration's detail/config works.

---

## Scorecard

| Check | ✅/⚠️/❌/N-A | Evidence |
|---|---|---|
| A1 connection create + test | | |
| A2 connection delete | | |
| B1 employees = 80 (NL→SQL) | | |
| B2 stores = 10 | | |
| B3 no fabricated phone numbers | | |
| C1 job saves | | |
| C2 schedule recorded | | |
| D1 approvals renders (+ pending item if §07) | | |
| E1 agent create → chat works | | |
| F1 logout/login | | |
| F2 role-1 reduced nav | | |
| G1 MCP servers page | | |
| G2 Integrations page | | |

**Pass:** ≥ 90% ✅. B3 fabrication or F2 role-gate failure (a role-1 user seeing Build/Admin) are
release-blocking.
