# Command Center Reliability: Eliminating "Silent Success"

**Status:** Design / plan — *no code changes yet*
**Date:** 2026-07-07
**Author:** Analysis + plan (Claude), verified against current source
**Goal context:** Make the Command Center (CC) agent the primary interface for all users, with the UI reserved for developers who want to navigate screens, research issues, or tweak things directly. Today the UI is the safety net that catches build/config failures. Promoting CC to primary removes that net exactly where the system is weakest, so the net must move *into the agent pipeline* first.

---

## 1. Executive summary

A prior audit surfaced a dominant failure class on the CC → Builder/Workflow path: **the system reports success for work that failed** ("silent success"). Five criticals were re-verified against current code — **all five reproduce** (F2 with a file-path correction).

The root cause is singular and structural: **every layer of the build pipeline is *fail-open*** — it treats "no unhandled exception" as success. The lie is manufactured **independently at four layers**, which is why a verifier bolted onto one spot cannot fix it.

The remediation is a **fail-closed contract** enforced by layered verification, anchored on one principle:

> **Never report success from the response. Report success only from a fresh read of the world — and only for exactly what the user asked.**

**Decision (recorded):** Build the **deterministic** layers first (honest HTTP status + read-back verification + validation gating), which kill all five criticals with no LLM in the trust path. Add the **LLM adversary/critic** as a later phase for semantic/intent gaps only. *Deterministic-first, adversary-later.*

---

## 2. Verified findings

| ID | Verdict | Layer | Current location (corrected) | One-line |
|----|---------|-------|------------------------------|----------|
| **F1** delegator | ✅ CONFIRMED | Delegation/SSE | `command_center/orchestration/delegator.py:248-253` (hard-coded `completed`); error-event append `:220-223`; only failure path is the `except` at `:255-257` | `delegate_to_builder` never checks `resp.status_code`; a 4xx/5xx or an in-stream `event: error` is reported as `status:'completed'`. |
| **F2** compiler | ⚠️ CONFIRMED w/ correction | Compiler gating | **repo-root** `workflow_compiler.py:900` (`success=True` unconditional), validate `:831`, fix loop `:844-877`, ungated save `:880` | Validation runs but gates nothing; invalid workflows are saved and announced "ready to use." **The live compiler is the repo-root file, not `builder_service/execution/workflow_compiler.py`.** |
| **F3** tools.create | ✅ CONFIRMED | Handler 200-on-error + executor | action `builder_agent/actions/platform_actions.py:858` (no `success_indicator`); handler `app.py:2874-2875` (`jsonify(status="error")` → HTTP 200); check `executor.py:474` | Failed custom-tool save returns 200 + error body; executor has no indicator to catch it → scored SUCCESS. |
| **F4** mcp.test_server | ✅ CONFIRMED | Handler 200-on-error + schema drop | action `platform_actions.py:2344`; handler guard `app.py:17159-17160` (200 + `{status:'failed'}`); param drop `executor.py:347-357` | Required `type:'remote'` isn't in the schema so it's dropped; handler short-circuits at 200; no `success_indicator` → PASSED. **Even a genuine connection failure returns 200** (`app.py:17196`). |
| **F5** email.configure | ✅ CONFIRMED | Schema-capability gap | desc `platform_actions.py:1776` advertises "workflow triggers"; `input_fields:1782-1823` omit `workflow_trigger_enabled/workflow_id/workflow_filter_rules`; endpoint `agent_email_routes.py:494-549` accepts them | Planner emits the trigger params; executor silently drops them; the *cosmetic* fields save → legitimate 200 → success. **An honest HTTP code can never catch this — only read-back can.** |

Full evidence in **Appendix A**.

---

## 3. Root-cause analysis — one bug, four layers

Each layer defaults to success and only flips to failure on an unhandled exception:

### 3.1 HTTP handler layer — 200-on-error
~24 main-app/route handlers signal failure **in the JSON body only** (`{status:"error"}`, `{status:"failed"}`, `success:false`) while HTTP status defaults to 200. Any caller checking `res.ok`/`status_code` sees success. The codebase is **inconsistent** — many sibling handlers do it correctly — which is what makes this a trap rather than a known convention. Full list in **Appendix B**; correct in-repo templates in **Appendix D**.

### 3.2 Executor layer — two sub-gaps
The executor's success logic (`builder_service/execution/executor.py:474-479`):
```python
is_success = response.status_code in route.success_status_codes
if route.success_indicator and isinstance(response_data, dict):
    indicator_value = response_data.get(route.success_indicator)
    if indicator_value in (False, "error", "fail", "failed"):
        is_success = False
```
- **Gap 2a — no indicator.** `success_indicator="status"` *does* a value-check, so the **43 actions that declare it are largely protected** against the `{status:"error"}` 200-body trap. The real hole is the **7 mutating actions with *no* `success_indicator`** → HTTP-status-only, zero body inspection:
  `tools.create` (:858), `tools.delete` (:936), `connections.delete` (:1099), `integrations.delete` (:1460), `integrations.test` (:1483), `mcp.delete_server` (:2321), `mcp.test_server` (:2344).
- **Gap 2b — narrow sentinel set.** The value-check only catches `{False,"error","fail","failed"}`. A handler returning `{status:"failure"}` (note: not `"failed"` — e.g. `app.py:5711`) or a different key (`success:false` when the indicator is `status`) slips through.
- **Gap 2c — silent param drop.** `executor.py:335,347-357` builds the request body only from params whose key is in the route's `input_fields`; there is **no `else` branch, no warning, no log** (`:409` logs the already-filtered payload). This is the mechanism behind F5 and every schema-capability gap.

### 3.3 Compiler layer — validation that gates nothing
`workflow_compiler.py` computes `is_valid` at `:831` and after each fix at `:868`, threads it into `result['validation']`, but `:900` sets `result['success']=True` **unconditionally** after save; `:880` saves regardless of validity. Every downstream consumer keys off `status=='success'` and ignores the carried `is_valid`. There are **four** identical "Workflow Created/Updated Successfully … ready to use" branches (`builder_service/graph/nodes.py:3976/3982/3986/3992` and duplicate `:4237/4243/4249/4255`).

### 3.4 Delegation → message layer — status laundering + ungated narrator
- `delegate_to_builder` returns `status:'completed'` for **any** stream that doesn't throw (`delegator.py:248-253`); an `event: error` frame is appended to the text but the status stays `completed` (`:220-223`). The real per-step outcome survives only inside `result['plan']`.
- On the CC side, `build_status` and `created_resources` are read straight from that self-reported plan (`command_center_service/graph/nodes.py:6079-6086`), and the **user-facing message is written by an LLM "distiller"** (`:6006-6062`) that is **not gated** on either — it can emit a success message even when the plan failed or nothing was created (`AIMessage` at `:6106`).
- The sibling delegators (`delegate_to_agent:103`, `delegate_to_mcp_tool:282`, `execute_workflow:314`) share the pattern: HTTP 200 → `status:'completed'` with no result validation.

**Net:** a failure at 3.1/3.2 is scored SUCCESS by the executor, laundered to `completed` at 3.4, and rewritten into a plausible success sentence by the distiller. Four independent lies; one user-facing "✅ done."

---

## 4. Architecture — fail-closed contract + layered enforcement

### 4.1 The spine: a tri-state `StepOutcome`
Replace the implicit success/failure boolean with an explicit tri-state that **defaults to *not success***:

| Outcome | Meaning | User-facing framing |
|---|---|---|
| `VERIFIED_SUCCESS` | Ran **and** a fresh read-back confirmed the intended state | "✅ Created X" |
| `FAILED` | Reported failure, or read-back disproved it | "❌ Couldn't create X: <reason>" |
| `UNVERIFIED` | Reported success but we couldn't confirm (no read path / timeout) | "⚠️ Submitted X but couldn't confirm — check <where>" |

The messaging layer may emit success framing **only** for `VERIFIED_SUCCESS`. This single inversion is what "solves it entirely" rather than per-site — every layer must produce and propagate this, and the terminal gate refuses to render optimism without evidence.

### 4.2 Four enforcement mechanisms (leverage order)

**A. Honest HTTP status.** Fix the ~24 handlers to return 4xx/5xx on failure → retroactively makes `status_code in success_codes` correct *for every action at once*. Add a single `after_request` normalizer that coerces any body with `status in ('error','failed')` / `success is False` to ≥400 if a handler forgets — a chokepoint so future drift can't reintroduce the class. **Highest leverage, lowest risk, in-repo templates exist.**

**B. Deterministic read-back verifier — the load-bearing new component.** After any mutating capability, call the action's **already-declared `discovery_capability`** (its list/get route) and assert the intended entity/field exists in fresh platform state. This *generalizes a pattern already in the repo* — `command_center_service/scheduling/schedule_logic.py::_confirm_created` (write → GET back → assert active). It is the **only** mechanism that catches F5 and the entire "returned 200 but did nothing / wrote defaults / wrote the wrong thing" class. No LLM; one cheap GET; no hallucination.

**C. Artifact validation gating.** For anything with a validator (workflows today), gate `success` and the message on `is_valid`. The verdict is already computed and carried, so this is a gating change, not new plumbing.

**D. Adversary / critic (LLM — *later phase*).** At the message boundary, given *(original request + per-step `StepOutcome`s + B's read-back evidence)*, an LLM asks "does the evidence support the success we're about to claim?" with **veto/downgrade power over the distiller**. Catches *semantic intent* gaps B can't encode (valid, saved workflow that doesn't do what was asked). **Not in the deterministic-first scope; documented here so the contract leaves a clean seam for it.**

### 4.3 Why deterministic-first (and not adversary-only)
- **Deterministic-only** misses intent gaps (asked for X, built valid-but-wrong Y) — accepted as a known residual until Phase D.
- **Adversary-only** puts an LLM in charge of ground truth — the *same class of mistake* that created this problem (trusting a narrator). It can be fooled and adds latency/cost/nondeterminism to the primary path.
- **Layered:** B establishes ground truth cheaply/deterministically as the primary gate; D (later) reasons over B's evidence as a second gate; the distiller is subordinate to both.

---

## 5. Phased plan

Each phase is independently shippable and leaves the system strictly more honest than before. Exact targets are listed so implementation is mechanical when greenlit.

### Phase 0 — Make failure visible (low-risk plumbing)
*Goal: let real failure signal reach the gates; kill the cheapest criticals immediately.*
- **F1:** `delegate_to_builder` (`delegator.py:195-253`) — inspect `resp.status_code` (mirror the sibling delegators that already gate on `== 200`); derive the returned `status` from the embedded `plan['status']` **and** any `event: error` frame, instead of hard-coding `'completed'`.
- **Gap 2c:** add the missing `else:` in `executor.py:347-357` that `logger.warning`s every dropped param (`capability_id` + key). Turns the entire schema-gap class into a visible log line.
- **Gap 2a (partial F3/F4):** add `success_indicator="status"` to the 7 exposed mutating actions in `platform_actions.py`. Fixes F3 and half of F4 in minutes (F4 still needs the schema + handler fix below).

### Phase 1 — Honest HTTP contract (Mechanism A)
*Goal: make the executor's default check correct everywhere.*
- Fix the ~24 handlers (**Appendix B**) to return `(<body>, 4xx/5xx)` on failure, following the in-repo templates (**Appendix D**). Two mechanical shapes: bare-error-dict → add a status code; ternary-status → return `(payload, 200 if ok else 5xx)`.
- Fix the unchecked proxy passthroughs (`app.py:10018/10039/10060` pause/resume/cancel; `:11678` knowledge; MCP `:17198/17263`) to map the upstream body's error to a status code.
- Add the `after_request` failure-body→status-code normalizer as the safety net (exclude the by-design cases in **Appendix C**).

### Phase 2 — Deterministic read-back verifier (Mechanism B — the core)
*Goal: success depends on the world, not the response.*
- Promote `_confirm_created` (`schedule_logic.py:40-64`) into a generic `verify_write(capability_id, params, result) -> StepOutcome`, driven by each action's `discovery_capability` + a small per-capability **expected-field map** (e.g. `email.configure → assert workflow_trigger_enabled == requested`).
- Wire it into the **executor** right after `is_success` is decided (`executor.py:506-522`) — decided §8.1, so coverage is universal (Builder + every caller). Downgrade to `FAILED` when read-back disproves, `UNVERIFIED` when no read path exists or the read-back itself errors/times out. Read-back runs for every write, no cap (§8.4); independent read-backs may run concurrently.
- **Fix the F5 schemas:** add `workflow_trigger_enabled`, `workflow_id`, `workflow_filter_rules` (and the other silently-undeliverable fields at `agent_email_routes.py:508-522`) to `email.configure`/`email.provision` `input_fields` — make the capability *reachable*, not just detected.
- Add a **load-time registry lint** (`definitions.py:_validate_route`) that flags any action whose description advertises a capability keyword with no matching `input_field` — catches F5-class drift for `agents.create/update`, `mcp.create_server`, `connections.create`, etc. (**Appendix E**).

### Phase 3 — Artifact validation gating (Mechanism C — F2)
- `workflow_compiler.py:900` — replace unconditional `result['success']=True` with `result['success']=is_valid` (or a `validation_failed`/`partial` status when saved-but-invalid). This one line flips `status:'success'` at the route and flows to all four message branches.
- **Persistence = save-as-draft (§8.2).** Keep the save at `:880` even when invalid, but tag the persisted row `draft`/`invalid` (not runnable). Don't lose the user's work; do refuse to call it "ready."
- Require `compile_result['validation']['is_valid']` before the "ready to use" copy at `nodes.py:3986/4249` (both duplicated blocks); otherwise emit a draft-saved message that names `validation['errors']`.

### Phase 4 — Fail-closed messaging + `UNVERIFIED` handling (finish the contract; deterministic)
- Introduce the tri-state `StepOutcome` end-to-end.
- Gate the CC distiller at the single chokepoint `nodes.py:6058-6062`/`:6106`: forbid success framing unless the plan is `VERIFIED_SUCCESS` with non-empty verified `created_resources`.
- Replace the four hardcoded "Successfully…" strings and the builder summary-LLM's "be brief and positive" branch (`nodes.py:3019-3024`) so copy is derived from verified outcome, not templated optimism.
- **`UNVERIFIED` confirm-or-continue (§8.3).** Before executing a step that depends on an `UNVERIFIED` upstream step:
  - **Interactive channel present** → pause and ask ("Step X couldn't be confirmed — continue with the remaining steps?"). Reuse the existing confirmation/interrupt plumbing (`_should_auto_confirm` path, `nodes.py:5972-6004`) rather than inventing a new prompt path.
  - **No interactive channel** (scheduled / email-triggered / headless) → proceed best-effort through the downstream steps and carry every `UNVERIFIED` into the final report; never silently upgrade it to success.
  - Requires an explicit `interactive`/`channel` signal on the run context (scheduler and email-trigger entrypoints must set it false). If the signal is missing, default to the interactive/ask branch (fail-safe: prefer asking over blindly continuing).

### Phase 5 — Anti-silent-success regression harness
*Goal: prove it's solved, don't believe it.*
- For each mutating capability, inject each failure mode — handler 500, handler 200-with-error, dropped-param no-op, invalid artifact — and assert the **user-facing message** reports failure or unverified (never success).
- Integrate into the existing `tests_v2` suite; this becomes the standing oracle against regression.

### Phase D (later) — Adversary/critic
- Lightweight LLM at the message boundary over the assembled evidence, veto/downgrade only. Out of current scope; the tri-state contract leaves the seam.

---

## 6. Test & verification strategy

The harness in Phase 5 is the proof. Per mutating capability, a matrix:

| Injected condition | Expected user-facing outcome |
|---|---|
| Handler raises → 500 | `FAILED` with reason |
| Handler returns 200 + `{status:"error"}` | `FAILED` (via A or B) |
| Write succeeds but drops a requested param (F5-shape) | `UNVERIFIED` or `FAILED` (via B) — **never success** |
| Artifact saved but invalid (F2-shape) | `FAILED`/validation errors surfaced |
| Genuine success | `VERIFIED_SUCCESS` |

A capability "passes" only when all rows produce honest framing. The suite must cover the 7 no-indicator actions and the create/configure paths explicitly.

---

## 7. Existing primitives to reuse (don't reinvent)

- **`_confirm_created`** — `command_center_service/scheduling/schedule_logic.py:40-64`; surfaced as `confirmed` at `:140-142,:223-225`. The template for Mechanism B.
- **`discovery_capability`** — every writable action already declares its read path (`definitions.py:372`; e.g. `agents.update.discovery_capability='agents.list'`). The verifier consumes this directly; no new endpoints needed.
- **Read routes already exist** for every writable domain: `agents.list/get`, `workflows.list/get`, `tools.list`, `connections.list/get`, `integrations.list`, `knowledge.list`, `mcp.list_servers/get_tools`, `schedules.list/get`, `email.get`, `environments.get/status`.
- **`suggested_prechecks`/`suggested_followups`** (`definitions.py:365-368`) are serialized to the planner but **never auto-executed** — a candidate execution hook for verification.
- **Correct handler templates** for Phase 1 — **Appendix D**.
- **`OutcomeTracker.record`** (`resilience/outcome_tracker.py:68`) logs outcomes but never re-reads the entity — the place to attach verified-outcome telemetry.

---

## 8. Resolved decisions (2026-07-07)

1. **Verifier placement → EXECUTOR.** Read-back lives in `executor.py:506-522`, right after `is_success` is decided, so it protects the Builder *and* every other caller of the executor (not just CC). One implementation, universal coverage.
2. **Invalid-artifact persistence (F2) → SAVE AS DRAFT + honest message.** Do not discard the user's work. `workflow_compiler.py:880` still saves when invalid, but tags the row as `draft`/`invalid` (not runnable) and the message states it needs fixes and names the validation errors. `success` still flips to not-`VERIFIED_SUCCESS`, so no "ready to use" copy is emitted.
3. **`UNVERIFIED` policy → CONFIRM-OR-CONTINUE.** When a step is `UNVERIFIED` and has dependent downstream steps: **if an interactive channel exists** (live CC conversation), pause and ask the user whether to continue with the remaining steps; **if it does not** (scheduled / email-triggered / headless / background run), proceed best-effort to complete the downstream steps, carrying the `UNVERIFIED` status into the final report. See Phase 4 for the mechanism.
4. **Read-back latency → NO CAP, NO SAMPLING.** Verify every write, every time — correctness over speed is the explicit goal. Independent read-backs in a multi-entity plan *may* run concurrently (that's parallelism, not a limit), but nothing is skipped or throttled.
5. **`after_request` normalizer scope → EXECUTOR-MARKED REQUESTS ONLY (my call).** A *global* body→status coercion is unsafe: the classic UI's `fetch` handlers today rely on some endpoints returning `200` + an error body, and flipping those to 4xx/5xx could trip error paths that currently don't fire. So the normalizer keys off a marker the executor already can set (service-to-service header, e.g. `X-AIHub-Internal-Exec: 1`) and only coerces `{status in ('error','failed'), success is False}` → ≥400 for agent-originated calls. Browser/UI traffic is untouched. The explicit per-handler status-code fixes (Phase 1) remain the primary correctness mechanism; this normalizer is defense-in-depth against future drift on the agent surface only. Exclusions in **Appendix C** still apply.

---

## Appendix A — Full finding evidence
- **F1:** `delegator.py:195` opens the stream with no `status_code`/`raise_for_status` in the 195-253 block; `:214-215` is the only token writer; `:220-223` appends error text without changing status; `:248-253` unconditional `completed`; `:255-257` is the sole failure path. Siblings `delegate_to_agent:118-122`, `delegate_to_mcp_tool:285-286`, `execute_workflow:317-318` all gate on `status_code==200`.
- **F2:** live compiler is **repo-root** `workflow_compiler.py` (912 lines), *not* `builder_service/execution/workflow_compiler.py` (182 lines, unrelated `compile_workflow_commands`). `:831` validate, `:844-877` fix loop (only consumer of `is_valid`), `:880` `if save:` (no validity guard), `:900` `result['success']=True`, `:908` `is_valid` only logged. Route `workflow_builder_routes.py:269/271/275`. Messages `nodes.py:3967→3986-3992` and dup `:4228→4249-4255`.
- **F3:** `platform_actions.py:858-912` tools.create, `success_status_codes=[200]` at `:910`, no `success_indicator`. `app.py:2874-2875` `jsonify(status="error")` (no code → 200). `save_custom_tool` returns False `app.py:877-881`. `executor.py:474` status-only; `:482-504` overrides cover only `connections.test`/`discover_tables`.
- **F4:** `platform_actions.py:2344-2368`, inputs only `url`+`transport_type` (`:2353-2360`), no `type`. `executor.py:349` drops undeclared. `app.py:17159-17160` `if data.get('type')!='remote': return jsonify({'status':'failed'})` → 200. No `success_indicator` → default codes `[200,201,202,204]` (`definitions.py:210`). Also `ResponseMapping("connected","success")` (`:2363`) reads a `success` key the handler never returns (all branches return `status`). **Genuine remote failures also 200** (`app.py:17196`).
- **F5:** `platform_actions.py:1776` desc advertises "workflow triggers"; `input_fields:1782-1823` lack `workflow_trigger_enabled/workflow_id/workflow_filter_rules`; `email.provision:1719-1771` too. Endpoint `agent_email_routes.py:431` (`<int:agent_id>`, repo root) reads/persists them at UPDATE `:494-496,:514-516` / INSERT `:531-532,:547-549`. Executor drop `:335,347-357`.

## Appendix B — HTTP 200-on-error handlers (Phase 1 targets)
`app.py`: `:2875` /save (custom tool; also `result` UnboundLocalError risk in except), `:2190` /custom, `:2255` /save_package, `:2221` /load_package, `:2272` /delete_package, `:3715` /add/connection (+`:3786` /api/connections POST delegates), `:12211`/`:12177`/`:12184` /test/connection, `:17160`+`:17198` /api/mcp/test, `:17226`+`:17263` /api/mcp/servers/<id>/tools, `:16393` /api/agents/<id>/mcp-servers, `:5220` /api/internal/integrations/<id>/execute, `:14580` /api/agents/<id>/environment, `:2632` /save/permissions, `:11678` /add/agent_knowledge (forwards timed-out `{status:error}`), `:4220`/`:4232` /chat/general_system, `:4480` /chat/general, `:4796` /chat/general/text, `:12034` /chat/data/reset, `:5711` /notification/email (`{status:'failure'}`), `:3046` /get/schedules.
`integration_routes.py`: `:968` /<id>/test, `:1065` /<id>/execute, `:1121` /<id>/refresh-token, `:1199` /<id>/sharepoint/browse.
`routes/portal_workflows_routes.py`: `:241`/`:243` /api/portal-workflows/runs.
Secondary (unchecked proxy passthrough): `app.py:10018`/`:10039`/`:10060` workflow pause/resume/cancel.

## Appendix C — Excluded from the 200-on-error fix (by design / false positives)
`solution_builder_routes.py:675/681` (validation-result-as-200 is correct REST); `app.py:15694/15782` and `email_processing_routes.py:416` (`success:True` payloads that merely contain an `errors[]` array); `integration_routes.py:1297` (intentional "saved but token not acquired" warning); `agent_email_routes.py:1270` (partial-success extraction).

## Appendix D — Correct in-repo handler templates (copy the pattern)
`app.py`: `/save/workflow` `:6019/6028/6037` (200/400/500), `/add/agent` `:2708/2712` (500), `/add/data_agent` `:2812/2816` (500), `/delete/agent` `:2833/2836` (500), `/get/agent_info` `:2640` (500), `/api/send_email` `:12579/12595/12599` (400/500), `/update|/delete/agent_knowledge` `:11824/11831`,`:11864/11871` (500), `/api/connections/<id>/execute` `:3797/3801/3807` (400/404/500), workflow local branches `:10023-10029`,`:10264-10313` (`e.status_code or 500`).

## Appendix E — Other schema-capability gaps (Phase 2 lint targets)
- `email.configure`/`email.provision`: missing `workflow_trigger_enabled`, `workflow_id`, `workflow_filter_rules`, `require_approval`, `auto_respond_instructions`, `max_auto_responses_per_day`, `cooldown_minutes`, `notify_on_receive`, `notify_on_auto_reply`, `notification_email` (all read at `agent_email_routes.py:508-522`).
- `agents.create/update/assign_tools`: `model`, `temperature`, MCP servers, environment extras silently dropped.
- `mcp.create_server`: no `command`/`args`/`env` for local/stdio servers (enum offers `local` but no way to launch it).
- `connections.create`: no `driver`/ODBC-string/`schema`/`encrypt`/`trust-cert` fields.
- **General:** every action is exposed — the description is the only contract and it is unchecked against the schema until the Phase 2 lint exists.
