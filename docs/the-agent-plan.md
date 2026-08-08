# The Agent — Implementation Plan

**Status:** REVIEWED — open questions settled (§8) · awaiting James's "go" · 2026-08-06
**Origin:** docs/platform-strategy-analysis-2026-08.md (Strategy B: harness-on-top) + six brainstorm rounds
**Doctrine (binding):** Purely additive. Zero changes to existing app behavior. Everything behind switches. Kill switch back to legacy at every layer.

---

## 1. The product shape

Three surfaces, one sentence each:

- **Assistant** — you → system. Talk to ask, analyze, and build. One front door.
- **My Work** — system → you. Every piece of human work in one queue: approvals, questions, drafts to edit, FYIs.
- **Playbooks** — the standing machinery. Frozen, versioned, scheduled artifacts that replay deterministically at ~zero tokens.

Pitch line: *"Users talk. Approvers decide in one queue. Builders inspect. Admins govern. Everything else is machinery."*

**Surface fates under Agent Mode** (mode = presentation lens; role = permission; nothing deleted, ever):

| Fate | Surfaces |
|---|---|
| Absorbed into Assistant | Agent Chat, Data Explorer chat, CC chat (classic CC stays behind flag as fallback brain) |
| Detail views via deep link (Dev+) | Workflow Designer (canvas of a playbook), Automations Studio (code view), Mission Control / Workflow Monitor (run history), Portal recorder (capture tool) |
| First-class management (Admin/Library) | Document Manager, Connections, Data Dictionary, Secrets, Users/Groups, **Skills (new)**, System Prompts, one Schedules monitor |
| Quietly fades (hidden by mode, never deleted) | NL Builder :8100, separate Agent/Data-Assistant builders (→ config pages), the four scheduling UIs (→ Schedule tab on Playbook), /jobs page |

Principle: **talk to create, click to manage.** The agent and the management screens are two clients of the same REST endpoints — one substrate, one audit trail.

---

## 2. Architecture (all new components)

**One new NSSM service: `agent_service` ("The Agent")** — next free port (~5111), built via the `aihub-new-service` pattern, PyInstaller onedir, packaged in the v5+ installer when ready. **Logging: rotating log files in the root `logs/` folder like every other service** (e.g. `logs/agent_service_log.txt`).

Inside it:
1. **Brain** — Claude Agent SDK (Python): the loop, context management, subagents, skills, hooks, resumable sessions. We host; no Node on client boxes.
2. **Tools** — in-process MCP tools: plain Python functions ported from CC's thin HTTP wrappers (`workflow_tools` / `automation_tools` / `codeflow_tools` bodies), calling the EXISTING main-app REST with `X-API-Key`. The honesty layer (save→read-back, tri-state outcomes, no-egress evidence) lives in the tool bodies and ports with them. Tool functions + JSON schemas live in a plain module with a thin per-harness adapter → brain-swappable (OpenAI Agents SDK, etc.) later.
3. **Skills** — SKILL.md packages, four scopes (see §4).
4. **Hooks** — CC's guards ported as SDK lifecycle hooks: mutation-claim gate (block replies claiming unverified actions), session-ledger stamping.
5. **Identity** — the **session envelope** is the first-class object: `{principal, mode, profile}` constructed by every entry path (chat / scheduler / webhook / email), consumed by tool scoping, mounts, persona, and audit. Interactive = the logged-in user (JWT from platform session). Headless = the user who created the trigger. System principal = admin-created maintenance only. **Privacy is enforced in tools + mounts, never the model.**
6. **Audit** — The Agent keeps its own per-user audit log (who asked, which tools ran, what was created), since main-app rows are tenant-scoped today.
7. **UI** — its own next-gen front end (concept approved: https://claude.ai/code/artifact/dba02f37-6e38-42a3-a100-7980b6c92bf4): Assistant chat, My Work, Playbooks, Admin › Skills. Sleek/smooth is a hard requirement.

**The only touches to existing code (both flag-gated, default off):**
- ~10 lines: nav entry + token redirect for "The Agent" (same pattern as CC's Experimental entry).
- ~20 lines: `agent_session` job type in the scheduler (new type only; existing six untouched).

**Model access:** PoC = direct Anthropic key on the dev box. Production path = **Anthropic-compatible passthrough route on aihub-api** (SDK honors `ANTHROPIC_BASE_URL`) so clients keep installing with OUR key and all metering/billing stays ours. BYOK becomes optional, not required. Brain-down ≠ platform-down: playbooks/schedules/approvals/classic all keep working; the Agent UI shows an honest "assistant unavailable" state.

**Kill switches:** stop the service · nav flag off · Agent Mode off (per user/install). Classic CC keeps running untouched throughout — it IS the fallback.

---

## 3. My Work (generalizing today's My Approvals)

An evolution of the existing checkpoint/ApprovalRequests machinery — widen the item schema, don't rebuild. "Approval" becomes one verb of many.

**Work item anatomy:** addressed-to (user or group) · from (agent session / playbook run / user / system) · verb (approve-deny, review, provide-input form, edit-and-return, acknowledge/FYI, do-offline-then-done) · payload (text, artifacts, diffs, forms — everything needed to act inline) · what-it-blocks (the paused run; response resumes it) · lifecycle (open → claimed → responded → closed, due dates, escalation).

**Locked requirements:**
- **Agent Email Approvals carry over** (today's approvals.html tabEmail) as the email-send item type — **drafts stay inline-editable**: what you approve is what sends, edits included; "reset to agent's draft" available.
- **Group claiming is explicit** (viewing ≠ claiming) **with release/un-claim**; claimed items hide from the group's queues until released; actions disabled until claimed.
- **Per-item agent side-thread**: ask the raising agent a question on the item, get an answer inline, then decide.
- FYIs are digested (one overnight summary, not nine drips). Items are actionable inline — never links out to hunt context.
- Skill promotion to tenant = a work item in admins' queues (see §4).

**Flow dashboard: PINNED for later** (leadership view of users/groups, bottlenecks, response times). **Day-1 requirement that makes it a pure add later: work items record lifecycle events** — created/claimed/released/responded/closed with timestamp + actor + blocked-run pointer.

---

## 4. Skills & memory (procedural memory + personalization)

**Four scopes, one directory tree under `data/agent/`** (alongside the platform's other data): `data/agent/skills/product/` (shipped, read-only, updated per release) · `data/agent/skills/tenant/` (learned here; admin-managed; **exportable via Solutions Author**) · `data/agent/groups/<id>/skills/` (a user's session mounts the union of their groups) · `data/agent/users/<id>/` (private skills + preference **memory**, auto-loaded in their own sessions only).

**Promotion policy (locked):** user → group = inline user confirmation only. Anything → tenant = **always admin approval**, routed through My Work.

**Rules:** agent-authored skills default to the author's private scope; promotion includes a scrub pass. Skills carry procedure + gotchas but lean on discovery tools for current facts ("probe the schema; don't trust remembered column names"). Reference checks (connection/playbook/skill citations verified periodically) surface drift in the Skills admin screen. Users can view/edit/delete their own memory ("what do you know about me?"). **Admins get counts + disable/purge (offboarding/compliance), never contents** of user scope.

Skills admin screen concept approved (same artifact, Admin › Skills): scope filters, origin chips (AGENT/HUMAN/SHIPPED), SKILL.md preview, reference check, usage incl. "sessions corrected it after loading," version history with provenance, Export to solution bundle.

---

## 5. Views — deterministic dashboards (Phase 2, designed-for on day 1)

Data Explorer's save-a-dashboard capability must not be lost: users compose dashboards from query results/visuals, reopen them later, and **refresh deterministically — no rebuilding via chat**.

**Design:** a **View** is a sibling artifact type to playbooks — same pin-and-version semantics. At save time the recipe is frozen: the exact SQL (distilled from the NLQ/agent exchange), connection refs (secret-refs, never values), and a layout spec (tiles/visuals). Refresh = re-run pinned SQL, re-render layout — zero LLM tokens, fully deterministic. Editing via chat produces a new version; the pin moves on promote, exactly like playbooks. Views appear in the artifact plane alongside playbooks, are schedulable (refresh on schedule), and shareable per user/group scope.

**The general primitive** (why this is called out up front): *pin the recipe, not the output.* The same machinery serves any repeatable deliverable — reports, extracts, dashboards. Existing Data Explorer dashboards remain untouched in classic mode throughout.

---

## 6. Build phases (all additive; each independently stoppable)

**A0 — Skeleton + spike (~2 days).** `agent_service` with SDK brain (Claude, `claude-opus-5` default) + 5 read-only tools (list connections, get schema, probe query, NLQ, list playbooks/runs) + minimal chat UI; logs to root `logs/`. Direct Anthropic key. This IS the decisive experiment: measure whether a frontier harness over the existing REST seams is actually good, before deeper investment. Exit: the AP-clerk journey's read-only half works end-to-end, graded by the new slim pack 20.

**A1 — Authoring tools.** create/dry-run/promote/schedule for automations + code flows via existing governed endpoints; checkpoint/approval flow reaches today's My Approvals unchanged. Exit: "email me invoice totals every Monday, flag >$50k" builds, gates, schedules — through conversation.

**A2 — My Work + next-gen UI.** The approved concept made real: queue + detail, seven item types, side-threads, editable email sends, claim/release. New tables for the widened item schema **with lifecycle events from day 1**; existing ApprovalRequests read-through so today's approvals appear in the new queue untouched.

**A3 — Agent Mode + skills + scheduling.** Per-user/install mode switch (nav lens); skills scopes + promotion flows + Skills admin screen; `agent_session` job type (flag-gated) for headless runs as the schedule creator; webhook/email triggers.

**A4 — Parity gate + hardening.** Packs 15/16/19 green with special scrutiny on silent-success guards ported as hooks. Only after green does Agent Mode default on for anyone.

**A5 — Views (Phase 2).** Deterministic dashboards per §5.

**A6 — Agent Email (James, A4 feedback #3).** Every user can create a personal
inbound address for The Agent: `<prefix>-agent.<client_id>@mail.everiai.ai`
via Mailgun. `<client_id>` is fixed per install (platform config; shown in a
readonly box on the create-address page); the user may change their prefix
(defaults to their user id). Mail arriving there starts/continues a headless
agent session as that user, honoring the same tool gates; attachments ride the
existing agent-email attachment pipeline (reuse `agent_email` infra: inbound
webhook, threading, cooldown, approvals). Outbound replies stay behind the
My Work editable-draft gate — nothing sends unapproved.

**Phase-2 backlog — product-skill "phone home" (feedback #4).** Installs
report product-skill gaps (anonymized: which asks found no skill, which skills
misfired) to a central endpoint; curated fixes ship back as product-skill
updates — behavior fixes without code releases, hive-mind style. Needs an
opt-in flag, an egress allowlist, and a review pipeline before anything ships.

**Legacy track (independent, James's timeline, never blocks A-track):**
- `BUILDER_HOST=127.0.0.1` (one-line config; closes the 0.0.0.0 unauthenticated builder exposure).
- auth_middleware: teach it X-API-Key (it currently only understands session cookies — naive enforcement would break CC/JSS/automations callers), wire in DRY_RUN (log-only), observe weeks, triage the log, then staged enforcement with env-var rollback. Per-route decorators on the 29 anonymous routes as the narrow alternative.
- Later/never: per-user signed identity on main-app routes; aihub-api Anthropic passthrough (before first client ships The Agent); builder-chain retirement.

---

## 7. Explicitly deferred or dropped (so nothing is silently lost)

| Item | Status |
|---|---|
| Flow dashboard (leadership/bottleneck view) | Pinned; lifecycle events land day 1 so it's pure dashboarding later |
| Views / deterministic dashboards | Phase 2 (A5), designed-for now |
| aihub-api Anthropic-compatible relay + metering | Required before first client deployment; not for PoC |
| BYOK productization / business-model changes | Later; relay path preserves current model |
| Per-user identity enforcement on main-app routes | Legacy track; The Agent keeps its own audit log meanwhile |
| Prompt-injection threat-model workstream | Dropped per James (on-prem; approvals gates are the compensating control) |
| Prompt caching on legacy CC / builder-chain deletion / dead-surface deletion | Dropped or demoted to "hidden by mode" |
| Excel per-row AI fix | Already fixed (9cb18f1) |
| Group skill conflicts | Precedence: user > group > tenant > product |

---

## 8. Settled at review (James, 2026-08-06)

1. **Product name: "The Agent"** — nav label and product name. Internal service `agent_service`, port ~5111.
2. **Brain: Claude.** The Claude Agent SDK is the harness; Claude is the model it drives. Default `claude-opus-5`, overridable via `AGENT_MODEL` env var. PoC uses a direct Anthropic key on the dev box; production routes through the aihub-api relay (§2).
3. **Skills/memory tree lives under `data/agent/`** — with the platform's other data, so it rides existing backup/packaging conventions.
4. **My Work is Agent-Mode-only for now.** Classic mode (including today's My Approvals page) stays exactly as-is.
5. **Test gate: a slim NEW pack** — `test_human/20_The_Agent/` — keeping the theme of separation; reuses harness ideas from pack 16 without touching it.

Plus one standing requirement: **all Agent service logs write to the root `logs/` folder** like every other service.
