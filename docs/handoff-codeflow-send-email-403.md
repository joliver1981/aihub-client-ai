# Handoff — `aihub.send_email()` silently never sends from a Code Flow step

**Status:** OPEN — root-caused, not fixed
**Filed:** 2026-09-05
**Component:** `automations/runner.py` · `automations/api.py` · `code_exec/doctrine.py` ·
`automations/sdk/aihub_runtime/__init__.py`
**Severity:** **High.** "Pull X, build a workbook, email it to me" is one of the most common things
a customer will ask a code flow to do. It reports success and delivers nothing, every time.

---

## 1. In one paragraph

A Code Flow step runs **without a live `AutomationRuns` row** — deliberately. The
`/automations/api/runtime/notify_email` endpoint that backs `aihub.send_email()` **requires** one,
so it returns **403** for every code-flow step. The SDK then returns `False` instead of raising
(also deliberate), so the step exits 0, and the walk summary the model reads surfaces **only
stderr, only for failed steps** — so the `Email sent: False` the script printed on stdout never
reaches the agent or the user. Three individually-defensible decisions compose into a silent
no-op, and the doctrine we hand the model advertises the verb as available.

---

## 2. The four pieces

**1 — Code-flow steps have no run row, on purpose.** `automations/runner.py:965`, `run_code_step`:

> *"Execute an INLINE Code Flow step — LLM-authored Python that lives in a workflow node, not a
> promoted Automation asset — through the shared executor. **No AutomationRuns row** (the workflow
> engine tracks the step); credentials are delivered as env vars (v0), **since there is no live run
> row backing the token/resolve endpoint for this ephemeral execution.**"*

**2 — The email endpoint requires exactly that row.** `automations/api.py:1298`:

```python
run = _get_runner().get_run(claims.get("run_id", ""))
if (not run or run.get("automation_id") != claims.get("automation_id")
        or run.get("status") not in LIVE_STATUSES):
    return jsonify({"error": "run token does not match a live run"}), 403
```

The step's identity is synthesised as `codestep-{run_id}` (`runner.py:979`) and no run is ever
registered, so this branch is unreachable-in-the-good-sense: it *always* 403s.

**3 — The SDK swallows it, by design.** `automations/sdk/aihub_runtime/__init__.py:469`:

> *"Returns True if the platform accepted the send, else False — delivery failure is REPORTED,
> never fatal, because a batch that produced a good CSV must not be lost to a mail outage
> (BRD 7.3 treats email delivery failure as a reportable exception)."*

This is the right call in isolation. It only becomes a defect because of piece 4.

**4 — Nothing ever reports it.** `_summarize_walk` emits `stderr_tail`, and only for non-success
steps. A step that printed `Email sent: False` to **stdout** and exited 0 renders as:

```
✓ step 3 — email-workbook (exit 0): success
```

The "reportable exception" BRD 7.3 asks for is generated correctly and then dropped on the floor.

**Plus:** `code_exec/doctrine.py:25` tells the model the verb exists —
`aihub.send_email(...)` is listed in `SDK_CLAUSE`, which is injected into code-flow authoring. The
agent is being pointed at a tool that cannot work in that surface.

---

## 3. Observed, twice

Competency pack 25 scenario TA-22 ("…and email the workbook to me"), on two independent runs a day
apart, produced byte-identical logs:

```
[aihub] email could not be sent (continuing): HTTP Error 403: FORBIDDEN
[aihub] Email send result: False
===== result =====
exit_code: 0  outcome: success
```

Run 1: `_codeflow_runs\sede0ced8\sf50a7274_1\run.log` and `…\s23a4abc4\…`
Run 2: `_codeflow_runs\s331078a4\s16a47abe_1\run.log` and `…\s6a8ff306\…`

On both runs the agent then told the user the workbook had been emailed and to check their inbox.
That reads as an agent honesty failure and was originally filed as one — it is not. The agent was
handed `✓ success` and reported it. The bad data came from the platform.

**Not verified:** that the same call succeeds from a *promoted automation* run. The code strongly
implies it (an automation run does create the row `notify_email` looks up), but nobody has executed
that counter-test. Do it first — it decides whether the fix is scoped to code flows or wider.

---

## 4. What to change

In rough priority:

1. **Make code-flow steps satisfy the token contract** — register a live run row (or a code-flow
   equivalent) so `notify_email` and any sibling run-token endpoint resolve. This is the actual
   fix; everything below is containment. Check which *other* runtime endpoints share the
   `get_run(...) → LIVE_STATUSES` guard, because they are all equally dead from a code flow:
   `runtime/review_item` is named in the same docstring, so at minimum `aihub.checkpoint()` is
   suspect from a code-flow step too. **Test checkpoint before assuming email is the only casualty.**
2. **Surface step stdout in the walk summary**, or at minimum surface a stdout tail on *success*
   too. Right now the platform's own honest signal is invisible. This is what turns any future
   silent failure into a visible one, so it has value well beyond this bug.
3. **Don't advertise a verb that cannot work in the surface.** Either fix (1), or strip
   `aihub.send_email(...)` from `SDK_CLAUSE` when the doctrine is being injected for a code-flow
   step, so the model routes to `draft_email_reply` / a promoted automation instead.
4. **Consider making `send_email` raise on 4xx while continuing to swallow 5xx/timeouts.** A 403 is
   a configuration error, not the mail outage BRD 7.3 was written for; conflating them is what hides
   this class of bug.

---

## 5. Related, found in the same runs

- **Orphaned schedules.** Deleting a code flow does not remove a schedule created against it by
  `schedule_agent_task` (as opposed to `schedule_code_flow`). Jobs **#649** and **#650** both
  survived deletion of their target flow and stayed active with a weekday 7:30am cron — each would
  have fired into a void until its two-week bound expired. Both deleted by hand. The
  `delete_code_flow` confirmation text claims "deleting removes the flow and its schedules", which
  is true only for the code-flow schedule type.
- **No `delete_skill` tool.** The Agent can create skills but not remove them, and says so honestly
  when asked. `/api/skills/delete` exists on the service; the tool surface just doesn't expose it.
- See also `docs/handoff-the-agent-sql-tool.md` (low priority) — the `dry_run_automation` rename
  proposed there is the same class of naming problem as this one.

---

## 6. Resolution — 2026-09-05

**Status: FIXED** (validated: all four pieces reproduced exactly as written; the FK on
`AutomationRuns.automation_id → Automations` makes "register a real run row" impossible for
an ephemeral step, so the fix registers the step *in its token* instead).

1. **Token contract satisfied for code-flow steps.** `run_code_step` mints the run token with
   `{kind: "codestep", workdir, name, user_id}` (`shared_auth.sign_automation_run_token(extra=…)`).
   One new chokepoint in `automations/api.py` — `_live_run_from_token` /
   `_live_run_from_claims` — replaces the six inline `get_run(...) → LIVE_STATUSES` guards.
   A promoted run proves liveness by its DB row as before; a code step proves it by a fresh
   `<workdir>/_heartbeat` (touched every 2s by the supervising runner, dead within 30s of the
   step ending). `notify_email`, `ai`, `review_items_status` and `resolve` now accept both
   flavors. `checkpoint` and `review_item` refuse the step flavor **with a message that says
   why** (they need a supervised run to pause/resume against; the SDK already auto-approves /
   skips them for steps, which stays as documented).
2. **Step stdout is in the walk summary** for every step, success included — both
   `agent_service/authoring_tools._summarize_walk` and CC `codeflow_tools.summarize_walk`.
3. **Doctrine is honest per surface.** Code-flow authoring keeps advertising `send_email` (it
   works now) and says `checkpoint` auto-approves in a step. The chat-lane `SDK_CLAUSE`
   (GA + CC run_python), GeneralAgent's tool docstring and The Agent's brain now say that
   `send_email / checkpoint / review_item / llm / ai_extract` are NOT available from
   run_python — those endpoints only ever accepted automation tokens; the chat token has a
   different audience. `aihub.help()` prints the same note in each context.
4. **`send_email` raises on 4xx / no token / chat lane**, and still returns False on 5xx,
   transport errors and a platform-reported delivery failure (BRD 7.3 preserved). The
   sibling verbs get a plain "not available from run_python" error instead of "HTTP 403".

`requested_by` is threaded from the dry-run routes (session + internal manage) into each
step's token so the email/approval rows carry who asked (None from the workflow engine,
which has no user on the execution).

Tests: `tests_v2/unit/test_codestep_runtime_token.py` — includes a real-subprocess
end-to-end against the Flask blueprint (step → SDK → notify_email → 200 sent) and the exact
regression (runtime 403 → the step now FAILS instead of reporting success).

§5 items (orphaned `schedule_agent_task` schedules on flow delete, no `delete_skill` tool)
remain open — they are separate defects.

## 7. §5 follow-ups — resolved 2026-09-05

- **Orphaned schedules.** Root cause was wider than filed: the code-flow delete removed only the
  Workflows row. `schedule_code_flow` jobs survived too (the scheduler's target reaper removes
  those eventually — `workflow`-type jobs whose TargetId no longer exists); `schedule_agent_task`
  jobs have `TargetId 0` and a free-text prompt, so nothing could ever reap them (#649/#650).
  Now:
  - `schedule_agent_task` takes an optional **`code_flow`** (the flow's exact name), stored as a
    job parameter — a structured link; a flow that does not exist is refused. The Schedules
    screen's create path accepts the same field.
  - `CodeFlowManager.delete_code_flow_with_schedules` sweeps, in one call: `workflow`-type jobs
    targeting the flow's workflow id, `agent_session` jobs linked via `code_flow`, and — only
    when the caller names them — `agent_session` jobs whose **prompt mentions the flow by name**.
    Mentions are surfaced as candidates, never guessed at: the agent's `delete_code_flow`
    two-step lists them (job id, name, prompt gist) and removes them on confirm unless the user
    keeps one via `keep_job_ids`. The REST delete and the CC/agent manage `delete` both use the
    sweep; the manage `get` returns `schedules` so the preview is honest. A DB failure while
    listing aborts the delete (fail closed).
- **`delete_skill` tool** added to The Agent (`work_tools.py`): same scopes and permissions as
  `/api/skills/delete` (user = own, group = member, tenant/product = admin), two-step confirm,
  read-back verification; ambiguous names across scopes ask for `scope`. Registered in
  `MUTATING_TOOLS`; the skills doctrine says to use it and that re-saving overwrites.

Tests: `tests_v2/unit/test_code_flows_manager.py` (sweep + real-SQL seams) and
`tests_v2/unit/test_agent_delete_skill_and_orphan_schedules.py` (agent env, 7/7).
