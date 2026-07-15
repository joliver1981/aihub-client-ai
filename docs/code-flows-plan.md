# Code Flows — Plan

**Status:** PLAN ONLY (2026-07-14). Nothing built. Design-only; react before we commit.
**Builds on:** [on-the-fly-automations-plan.md](on-the-fly-automations-plan.md) (Automations: environments, SDK, runner, verification, events, checkpoints, the workflow "Automation" node, the scheduler job type — all shipped).
**Chosen approach (James, 2026-07-14):** *reuse the workflow execution engine with a code-only node palette.* Do NOT build a second workflow engine.

---

## 1. Thesis

LLMs build automated processes more reliably by **writing code** than by assembling a fixed-vocabulary visual graph. A **Code Flow** is the agent-facing way to build a complex, multi-step automated process **entirely in code** — while reusing the workflow execution engine, the canvas (as a read-only debug viewer), the scheduler, and the whole Automations substrate. The human **Workflow Designer stays for end users**; Code Flows are what the AGENTS use under the hood when asked to automate something complex, instead of struggling to build with human node-types they don't map to well.

**The one-line reframe:** an Automation is *one script*. A Code Flow is *orchestrated code steps with a control graph* — the multi-step evolution of an Automation, executed by the engine we already own.

## 2. The core design (code-only node palette)

A **Code Flow = a workflow (in the existing engine) whose nodes are all "code steps."** Each code step is LLM-authored Python that runs in the Automations runner/environment. The engine's existing **pass / fail / complete edges** provide the control flow — sequence, on-failure branches, alert steps. The **visual viewer is the existing canvas**, but opening a node shows **Python code**, not a config form. It schedules through the existing scheduler.

```
Code Flow "nightly-vendor-recon"  (a workflow the engine runs)
  ┌──────────────┐   pass   ┌──────────────┐   pass   ┌──────────────┐
  │ step: pull    │ ───────▶ │ step: recon   │ ───────▶ │ step: upload  │
  │ (code)        │          │ (code)        │          │ (code)        │
  └──────┬───────┘          └──────┬───────┘          └──────────────┘
         │ fail                     │ fail
         ▼                          ▼
  ┌──────────────┐          ┌──────────────┐
  │ step: alert   │          │ step: alert   │   ← failure-branch code steps
  │ (code)        │          │ (code)        │      (aihub.notify / Alert node)
  └──────────────┘          └──────────────┘
```

The dev sees a **visual flow**; clicking any node opens **its Python code** (read-only) with the live run overlay. That is the "graph legibility without graph-authoring" compromise: the LLM writes code, the human reads a graph.

## 3. What we REUSE (the foundation is already ~70% built)

| Capability | Already built | Code Flows use it for |
|---|---|---|
| Workflow execution engine (`workflow_execution.py`) — node dispatch, **pass/fail/complete edges**, variables, loops, conditionals, Human Approval | ✅ | The orchestration graph. Fail edges = on-failure branches; complete edges (workflow-complete-conn work) = "always run this next". |
| **Automation node** (`_execute_automation_node`, P4) — runs a pinned automation, inputs from variables, outputs to variables, honest tri-state → pass/fail routing | ✅ | The v0 "code step" — literally works today. |
| Automation runner + per-flow-step environments + pip libraries | ✅ | Each code step runs real Python with any library. |
| `aihub_runtime` SDK (connection/secret/input/checkpoint/log) | ✅ | Steps reference existing connections/secrets; the base for the expanded SDK. |
| Output verification + honest tri-state + events sidecar (`events.jsonl`) | ✅ | Per-step verified outcomes; the events feed powers the live per-node viewer. |
| Checkpoints + abort + Mission Control | ✅ | Human-in-the-loop mid-flow; live supervision. |
| Workflow **canvas** | ✅ | The read-only Code-Flow viewer (render the graph; node → code). |
| Scheduler (workflow + automation job types) | ✅ | Schedule the whole Code Flow; it's a workflow. |
| Solutions Author packaging | ✅ | Ship a Code Flow to another tenant. |

## 4. What's NEW (the actual build)

1. **The "code step" as a first-class node.** Start by **reusing the Automation node** (a step = a referenced, promoted automation — zero new engine work). Then add a lighter **inline Code Step** node (code embedded in the node, no separate promoted asset) for ergonomics, so a 3-step flow isn't 3 separate automation assets.
2. **The Code-Flow authoring layer.** The agent PLANS the steps, WRITES each step's code, declares the graph (sequence + on-failure edges), and **compiles it to a code-node workflow** the engine runs. This is a code-first sibling of `WorkflowAgent` — it emits code nodes, not human node-types. Likely a set of CC tools (`create_code_flow`, `add_step`, `wire`, `dry_run_flow`, `promote_flow`) mirroring the Automations tools.
3. **Inter-step data passing.** Steps must share data (step 1 writes a file, step 2 reads it). Design: a **shared flow workdir** all steps in a run see, plus workflow variables for small values and artifacts for big ones. SDK: `aihub.flow_input()`, `aihub.step_output(name, value)`, `aihub.read('file')`. Do NOT push large frames through workflow variables.
4. **Expanded SDK + capability model (the crown jewel).** Grow the SDK into the platform's programmable surface: `aihub.query_data()`, `aihub.ask_agent(name, question)`, `aihub.notify(channel, msg)`, `aihub.artifact()`, plus flow context. **Security:** reuse the run-token + manifest allowlist — the flow DECLARES what it may touch (connections, secrets, agents, notify channels, whether it may create resources); the token scopes it; nothing is ambient. Get this right before the SDK gets powerful.
5. **The per-node debug viewer.** Reuse the canvas to render the flow graph; a node-detail panel shows the step's **code** + its **live log/verify** (the Studio panel machinery, scoped per node, fed by the events sidecar). Read-only for humans.

## 5. Key design decisions to lock

- **Referenced vs inline steps.** v0 = referenced (Automation nodes, reuses everything, but asset-heavy: N automations per flow). v1 = inline Code Step node (one flow asset, steps are code blobs in it). Recommend shipping referenced first to prove the model on the real engine, then inline for ergonomics.
- **Inter-step data = shared workdir + artifacts, not fat variables.** The engine variables are for small values/handles.
- **Alerts/failure = the engine's fail edges** routing to code steps that call `aihub.notify()` (or reuse the existing **Alert node**). No new control-flow engine.
- **Routing boundary (avoid re-creating this-week's ambiguity):** Automations and Code Flows are ONE family ("write code to do a process"); the agent picks single-script (Automation) vs multi-step (Code Flow) *within* the family. The build-shape decision stays binary at the top: **process/orchestration → automation-family; object/interactive (agent, connection, knowledge, MCP) → the object Builder.** The human Workflow Designer is a separate, end-user surface.
- **One canvas or two?** The Code-Flow viewer can be the same canvas component in a code-only/read-only mode, or a dedicated viewer. Lean: same canvas, "code mode" flag — reuse.

## 6. Phasing

| Phase | Deliverable | Reuses / new |
|---|---|---|
| **P0** | Code Flow = a workflow of **Automation nodes** with fail edges, authored via new CC tools (create_code_flow / add referenced step / wire / dry-run / promote). Runs + schedules on the existing engine. | ~all reuse; new = the authoring tools + compile-to-workflow |
| **P1** | Expanded SDK (`query_data` / `ask_agent` / `notify` / `artifact` / flow context) + capability model (declare-and-scope via the run token). | extend SDK + token allowlist |
| **P2** | Inline **Code Step** node (code in the node, shared flow workdir for inter-step data) — one flow asset, not N automations. | new node type + runner seam |
| **P3** | Per-node **debug viewer** — canvas graph, node → code + live log/verify (Studio panel machinery per node). | reuse canvas + events sidecar |
| **P4** | Dedicated code-flow authoring agent (code-first WorkflowAgent variant) if the CC-tool flow isn't enough for big flows; loop/branch/retry ergonomics. | new authoring agent |

## 7. Honest risks / guardrails

- **Don't ship two competing builders.** Code Flows subsume the AGENT's use of the *workflow* builder, not the whole Builder (agents/connections/knowledge stay Builder objects), and not the human designer. Keep the top-level routing binary.
- **Governance of arbitrary code.** A node graph's data access is legible; code's isn't — recovered by (a) the manifest/token allowlist, (b) the per-node code viewer, (c) the egress logging already built. Name it as a requirement.
- **Security surface of a powerful SDK.** `ask_agent` / `create_resource` from inside code is highly privileged — the declare-and-scope capability model is mandatory, not optional.
- **Asset sprawl (referenced steps).** N automations per flow is heavy; the inline Code Step (P2) is the fix — but P0 referenced-first proves the engine reuse cheaply.
- **Don't reinvent a distributed workflow engine.** Retries/idempotency/partial-failure are deep; lean on the engine we have and keep the orchestration model simple (sequence + fail/complete edges + alert steps).

## 8. Open questions for James

1. **Naming/surfacing:** is "Code Flows" a distinct thing users see, or purely the agent's under-the-hood representation (with the human designer being the only "workflow" users see)?
2. **How much SDK power in v1** — read-only (connections/secrets/query/notify) first, and defer create-agents/create-resources until the capability model is battle-tested?
3. **P0 referenced-steps is asset-heavy** — accept that to prove the engine reuse, or jump straight to the inline Code Step node?
4. **Viewer**: extend the existing canvas node-detail in a read-only "code mode," or a purpose-built code-flow viewer?
