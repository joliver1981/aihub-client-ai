# AI Hub × General Agents — Strategy Analysis

**Date:** 2026-08-04 · **Status:** Brainstorming input, no code changed
**Method:** 9-agent deep analysis (4 codebase mappers → 3 strategy advocates → scoring judge + completeness critic), ~684k tokens of investigation, 173 tool calls, all claims cited to code.

---

## TL;DR

1. **Your instinct is correct — and it's already half-built.** Command Center is *already* an external brain driving the platform over REST: its workflow/automation/code-flow tools are thin HTTP wrappers with `X-API-Key` auth (`workflow_tools.py:117,581,596,627`). The "layer on top of a coding agent" architecture exists in-house today; the brain just happens to be a hand-rolled one (an 11,243-line `nodes.py`) instead of a frontier harness. Swapping the brain changes the caller, not the platform.
2. **The problem is not capability — it's concepts.** The explore → author code → dry-run → human-gate → promote → schedule → zero-token-replay pipeline **exists end-to-end through CC tools right now**, with an unusually good honesty layer. But a user faces ~22 top-level nouns, **five** ways to "automate a process," **six** credential surfaces, **four** chat front doors, and four scheduling UIs. The capability layer converged; the concept layer never did. That's the "hasn't come together" feeling.
3. **Recommendation: don't build something new.** Clean-sheet was scored and *dominated* — its own advocate conceded the harness-on-top route captures ~70% of the benefit at ~30% of the cost. Instead: **adopt the clean-sheet product story (5 nouns) without the clean-sheet rewrite**, reached through a sequence where nearly every step pays off regardless of the endgame.
4. **The sequence:** lock auth (Phase 0) → delete the duplicates (Phase 1) → build the option-neutral seam: an AI Hub MCP server + one unified code runtime (Phase 2) → shadow Claude-Agent-SDK brain behind a flag, exactly like the CC_AGENT A/B precedent (Phase 3) → flip, and collapse the UI to **Assistant / Playbooks / Approvals** (Phase 4). Decision gates let you stop at "keep the native brain" if SDK licensing/packaging or air-gapped buyers force it — Phases 0–2 pay in full either way.
5. **One blocking liability:** `auth_middleware.py` is finished, tested, and wired into **nothing** (zero references in `app.py`; 29 routes anonymously reachable; `/api/workflow/approvals` leaks 313KB to an anonymous curl). Every strategic option — including doing nothing — needs this fixed first. An external brain on today's REST surface would be an automated exploitation client for the platform's own holes.
6. **The pitch that falls out:** *"AI Hub is what makes a Claude-class agent deployable in your enterprise: credentials it never sees, approvals your people control, schedules that replay for zero tokens, on your servers."* That sentence answers "why not just use Claude Code?" in the same breath it explains the product.

---

## Part 1 — Findings

### F1. The pipeline you described already exists; the back half is the crown jewel

The full loop — agent probes a connection (`get_connection_schema` / `probe_connection_query`) → authors an Automation or Code Flow → **dry-runs with live credentials** → human gate (`checkpoint()` → My Approvals) → **promotes a pinned immutable version** → schedules a real JSS job → deterministic replay at ~zero tokens — is achievable today entirely through CC tools.

The hardening layer is genuinely rare engineering, distilled from ~60 fixed defects:

- Tri-state run outcomes (`success` / `failed` / `unverified` — never success-by-absence-of-exception)
- Save → read-back verification (`workflow_tools.py:593-605`)
- `wait_for_outcome` that never converts a timeout into success
- No-egress evidence ("declared a remote transfer but NO network egress was observed")
- Mutation-claim guards (`nodes.py:112,1540`), the pause-pin for approval-blocked runs
- Append-only automation versions with a promote pin; scheduled runs execute the pinned version only
- The fail-closed internal-exec normalizer (`app.py:1377-1414`) that coerces 200-with-error-body to HTTP 500

Generic harnesses have nothing like this. **This is exactly the discipline an SDK brain needs bolted to its tools — and it lives in the portable tool bodies, not the loop.**

### F2. One broken seam: exploration and production are two incompatible runtimes

`run_python` explores in a sandbox with **no** platform connections, secrets, or `aihub_runtime` SDK (`code_interpreter.py:155-232` seeds only uploads/artifacts). The frozen artifact runs against `aihub.connection()/secret()` (env injection exists only in `automations/runner.py:8-16`). So the agent cannot *promote* what it explored — it re-authors from scratch against an API it never executed, and LLM-authored code first meets live credentials at dry-run.

Missing seams: a "lift this session into an automation draft" tool; an exploration mode with read-only secret-ref-resolved connections under the *same* SDK; a run-history → auto-repair loop. This is the single highest-leverage *build* item on the board — it converts "agents solve things with code" from substantially-true to seamless, and it's needed under every strategy.

### F3. The concept layer is where the product "hasn't come together"

- **~22 top-level nouns** (Agent + four sub-species, Automation, Workflow, Code Flow, Portal Workflow, Job, Schedule, Approval, Connection, My Connection, Integration, MCP Server, Local Secret, Data Dictionary, Document, Knowledge, Environment, Custom Tool, Solution, Builder, System Prompt…)
- **"Automate a process" has five paths**, each with its own builder, monitor, scheduler and approval semantics. The scheduler's six job types (`job_scheduler.py:99-106`) are literally a census of the redundant primitives.
- **Six credential surfaces**; "where do I put my SFTP password" has no single answer.
- **Four chat front doors**; "Job" means three unrelated things; four scheduling UIs over one APScheduler service.
- **Code Flows — the newest, most on-thesis primitive — has zero human-facing UI** (API-only, consumed by CC). The platform's best idea is invisible.
- Dead surfaces confirmed: `/assistants`, `/data_chat` (flag default-off), a `/jobs` POST handler that saves nothing, orphan pages, and `workflow_api.py` — a Flask app whose every route body is `pass`.
- CC is simultaneously flagged "Experimental" in the nav and positioned as the primary entry point.

### F4. Moat / commodity / liability

**Moats (keep, invest — all body-side):**
- Secret-ref credential plane: `{{LOCAL_SECRET:name}}` resolved only at runtime (`DataUtils.py:2246-2262`); the agent sees refs, never values. The masked-password saga proves the failure modes are subtle — hard-won.
- Approvals/HITL bridge: `checkpoint()`/`review_item()` → My Approvals, group routing, orphan reaper — the one governance UI for non-technical humans that no harness vendor ships.
- Six-type scheduler as a real NSSM service with execution rows.
- Execution history + the honesty layer (F1).
- 14-service on-prem packaging (Inno/NSSM/PyInstaller) + Solutions Author round-trip export — no coding-agent harness has an answer to on-prem enterprise distribution.

**Commodity (a harness does it as well or better):** CC's hand-rolled loop, mini-LLM classifiers (capability router, answer-quality gate, export-intent), `run_python`, delegation-as-subagents, CC-side scheduling surface, memory, WebSearch/PDF/Excel tool wrappers, the 4-LLM build chain (already superseded by CC's own native workflow tools), chat UI, vector RAG, NLQ.

**Liability:** authz (below). Also: token-thrift is currently an *architecture*, not a *fact* — the Excel export still calls an LLM per row (which 100%-fails on the `temperature` kwarg, then falls back; ~25 min per 1000 rows of pure waste).

### F5. What the harness world ships free (mid-2026)

Subagents, skills, hooks, MCP client+server, sandboxed code exec, worktrees, persistent memory, scheduled cloud agents/routines, computer use, Chrome control, structured output, prompt caching. Scheduling, browser automation, code execution, orchestration and memory are **commoditized**; hand-building them is no longer differentiation — it's a maintenance bill this team pays alone (the `temperature`-kwarg breakage is the standing example). Closest competitor to AI Hub's actual position is **Microsoft Copilot Studio** (computer-use GA with Key Vault credentials, Purview audit, configurable human review) — but cloud-tethered. The on-prem governed-agent slot is open today.

### F6. AuthZ is the single blocking gap — for every option

Verified again this session: `init_auth_middleware` exists (`auth_middleware.py:218`), has passing tests, and is referenced by **nothing** in `app.py`. `AUTH_MIDDLEWARE_DRY_RUN=true` at Machine scope. 29 routes anonymously reachable; anonymous POSTs persist (HTTP 201); `/api/scheduler/jobs` and `/api/workflow/approvals` leak 32KB/313KB anonymously. Identity itself works (login, sessions, CC's signed JWT); blanket *enforcement* does not. Until wired, the governance pitch is falsifiable in one curl.

---

## Part 2 — The three options, scored

| | A — Converge in place | B — Harness on top | C — Clean sheet |
|---|---|---|---|
| **Thesis** | CC (own runtime) becomes the only front door; everything else becomes tools/viewers | Claude-Agent-SDK brain over AI Hub as the deterministic body, via an MCP seam | New 5-noun product ("Playbooks"), cherry-picking ~6 services; AI Hub to maintenance |
| **Judge score** | **7/10** | **8/10** | **4/10** |
| **Best at** | Time-to-value (9), migration risk (8) | Token economics (9), fidelity to goal (9), defensibility (7), maintenance (8) | Endpoint comprehensibility (9) |
| **Worst at** | Maintenance 3/10 — own a 2025 harness forever | Time-to-value (6) — packaging + parity gating are honest weeks-to-months | Time-to-value 2/10, migration risk 2/10 — two products, one team |
| **Fatal flaw** | The moats don't vote for it: every defensible asset is body-side and survives under any option; the bet is out-maintaining Anthropic's harness team | True air-gap unservable; vendor concentration; dies if Phase 0 slips | Dominated: B captures ~70% of benefit at ~30% of cost; extraction risk turns "clean sheet" into "second front-end on the same mess" |

**Why B wins:** it's the only option that answers everything at once — the brain rides the frontier for free while the body monetizes the genuine moats, and the defensibility question inverts from *"why is your agent better than Claude Code?"* (unanswerable) to *"AI Hub is what makes a Claude-class agent enterprise-deployable"* (compelling). And it's the completion of a migration the platform already half-performed on itself: the "delegate for knowledge, native tools for mechanism" rule (63% of defects were on the delegated build path) *is* the Option B thesis, proven in-house.

**Why not A as the terminal state:** A's own advocate conceded it — A is the right next six months *regardless of endgame*, but as a destination it means permanently re-cloning every harness advance by hand, alone, with 11k lines of `nodes.py` as the standing bill.

**Why not C:** its five-noun product story is the best articulation of the target — adopt it as the design north star and marketing narrative. Its codebase strategy (extraction + transpiler + two-track org) is the classic rewrite trap, and Map 4's finding that CC already fronts the body over REST removes its core premise.

---

## Part 3 — Recommended sequence (B through A's on-ramp)

**Phase 0 — Lock the doors** *(1–2 weeks; blocking; do before anything else)*
Wire `init_auth_middleware(app)` into `app.py`; triage the 29 anonymous routes, the anonymous-POST hole, and the approvals leak; flip `AUTH_MIDDLEWARE_DRY_RUN` off staging→prod; begin extending the CC signed-JWT per-user pattern (`shared_auth.py`) to main-app routes so future brain calls carry *user* identity, not the tenant god-key. Caveat from the critic: "one function call" is the wiring, not the rollout — budget a regression pass for pages/integrations that currently depend on unguarded access.

**Phase 1 — Delete the duplicates** *(2–4 weeks; pure subtraction; option-neutral)*
Retire `builder_service` + `WorkflowAgent` + the prompt side of `workflow_compiler` (keep its deterministic validator); delete dead surfaces (`/assistants`, `/data_chat`, `workflow_api.py` stub, no-op `/jobs` POST, orphan pages); unify `VALID_WORKFLOW_NODE_TYPES` + node schemas into **one machine-readable contract module** (kills the hand-synced duplicates that breed silent divergence); kill the per-row Excel AI call; add prompt caching to CC's stable doctrine blocks as the interim token win.

**Phase 2 — Build the option-neutral seam** *(4–8 weeks; overlaps Phase 1; the low-regret core)*
(a) **AI Hub MCP server**: clone the in-repo `mcp_internal_routes.py` JSON-RPC pattern; port ~25–30 honesty-hardened tool bodies from `workflow_tools`/`automation_tools`/`codeflow_tools` near-verbatim — the honesty layer travels *with* the tools. Auth = API key + signed per-user header.
(b) **Close the runtime break (F2)**: seed the code-exec sandbox with `aihub_runtime` + a read-only secret-ref credential broker; add `promote_session_to_automation`.
Everything in this phase improves CC *today* and is the landing pad for the SDK brain tomorrow. Design constraint added by this synthesis: the seam needs an explicit **prompt-injection threat model** (inbound email, webhooks, portal content, and uploaded docs all reach an LLM holding credential-touching tools — none of the advocates addressed this).

**Phase 3 — Shadow brain** *(6–12 weeks)*
`aihub-brain` as a 15th NSSM service (Python Claude Agent SDK under PyInstaller onedir, per the `aihub-new-service` pattern); CC chat proxies behind `CC_BRAIN=sdk`, default OFF — the exact CC_AGENT A/B precedent that shipped the native agent. Platform knowledge becomes SKILL.md packages (System Prompts admin evolves into skill-package management). Hard parity gate: packs 15/16/19 green against **both** brains, with special scrutiny on the silent-success guards ported as hooks. Add an `agent_session` scheduler job type — the on-prem answer to cloud routines; point webhook/inbound-email triggers at the brain.

**Phase 4 — Flip and collapse** *(on parity green)*
`CC_BRAIN=sdk` default on; retire the `nodes.py` loop (tool bodies + hooks survive), mini-LLM classifiers, `run_python`. Ship the noun collapse: the Work nav becomes **Assistant / My Playbooks / My Approvals** — Playbooks as a thin union list over automations/workflows/code flows/portal workflows (type field, no schema rewrite); Mission Control, Workflow Monitor, and Studio re-badged as deep-linked **viewers**, never start pages; one chat surface (Ops Room becomes THE UI or dies). Adopt C's five-noun story as onboarding and marketing.

**Decision gates:**
- After Phase 2: if SDK licensing/redistribution or onedir packaging proves unworkable, or the pipeline fills with genuinely air-gapped buyers → stop at **A-terminal** (keep the native brain, consciously accept the harness line-item). Phases 0–2 still pay in full.
- After Phase 3: if parity on honesty behaviors can't be reached → hold the flag OFF; the gap list becomes the SDK-hooks backlog. Ship no regressions.
- Throughout: **no new user-facing nouns; no new surface without retiring one.** The deletion discipline *is* the product strategy.

---

## Part 4 — The no-regret list (do these regardless of strategy)

Every one of these is independently justified even if the strategic decision is deferred indefinitely:

1. Wire authz + flip dry-run off (Phase 0) — live clients are exposed **today**
2. Retire the builder chain (already superseded by CC native tools, shipped as default)
3. Delete the confirmed-dead surfaces
4. Unify the engine contracts into one machine-readable module
5. Close the exploration↔production runtime break
6. Prompt caching on CC doctrine blocks; kill the per-row Excel AI call
7. Extend per-user signed identity to main-app routes

---

## Part 5 — What this analysis cannot tell you (and cheap ways to close the gaps)

The completeness critic's most consequential findings — this analysis is code archaeology; the following are absent from *all* the reasoning above:

- **No commercial ground truth**: install count, buyer persona, win/loss/churn, whether AI Hub is a multi-client product or a services vehicle with two engagements. This can flip the whole calculus.
- **No usage telemetry**: nobody knows which of the ~30 surfaces real users touch; every retire/collapse call is being made blind.
- **No pricing/COGS arithmetic**: not one dollar figure for token spend per tenant, SDK cost per user-month, or seat price the market bears. "Token thrift" is argued without numbers.
- **Prompt injection**: every option puts an LLM with credential-touching tools behind untrusted input (email, webhooks, portals, uploads). No advocate mentioned it. Needs a threat model before the seam ships.
- **SDK licensing**: nobody verified the Claude Agent SDK can be embedded, PyInstaller-frozen, and resold in an on-prem commercial product, or who owns the API billing relationship.
- **The wrong competitor set**: the non-technical buyer of "automate my process" is also evaluating Power Automate, n8n, Zapier, UiPath — not just coding harnesses.
- **Chat-first UX is an article of faith**: no evidence an AP clerk prefers conversing with an Assistant over forms; the playbook-conversion behavior (users freezing work instead of re-asking) is assumed, not observed.

**The cheap decisive experiment (recommend doing this first, ~2 days):** point a stock Claude Code / Agent SDK session at the existing REST seams via a quick-and-dirty MCP wrapper and attempt the AP-clerk journey end-to-end ("email me invoice totals every Monday, flag >$50k for approval"). Map 4's load-bearing claim — that an external brain captures ~90% of platform capability through existing seams — gets validated or killed by measurement instead of argument, before a single strategic dollar is committed.

---

## Part 6 — The product story (adopted from Option C, independent of codebase strategy)

**Five nouns:** Assistant · Playbook · Approval · Schedule · Connection

**One sentence for users:** *"You talk to the Assistant; it does things now, or turns them into Playbooks that run on schedules, ask people when they must, and cost nothing to re-run."*

**One sentence for buyers:** *"AI Hub is what makes a Claude-class agent deployable in your enterprise — credentials it never sees, approvals your people control, schedules that replay deterministically, on your servers."*

---

*Full agent outputs (4 maps, 3 advocate cases, judge, critic) preserved in the session workflow journal. Analysis conducted read-only; no code was modified.*
