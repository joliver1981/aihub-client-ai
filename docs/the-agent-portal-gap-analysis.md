# The Agent ↔ Command Center: Portal capability gap analysis

**Date:** 2026-08-20 · **Status:** analysis only, no code changed · **Repro:** asked both agents to connect to a portal and download a document — CC did it ("Working in the portal…"); The Agent said it couldn't.

---

## 1. Root cause (one sentence)

The Agent has 52 tools and **zero** that touch portals or browsers, and its system prompt has no PORTALS section — so under its honesty doctrine the model correctly reports it can't; nothing platform-side is missing.

This is the **third occurrence of a known failure class** (email → A6-5 fix; documents → `document_tools.py`, commit bfa8942): *every platform noun needs a first-class tool surface; prompts and skills cannot substitute for a missing tool.* Portals are simply the next noun.

The encouraging part: **all the heavy machinery is body-side and brain-agnostic.** CC's entire "portal feature" is eight thin tool closures in `command_center_service/graph/nodes.py` over seams that already exist as services and stores. Closing the gap = writing the same thin wrappers in The Agent's idiom. This is the harness-on-top thesis working as designed — swapping the brain changes the caller, not the platform.

## 2. How CC does it (verified map)

Two execution modes share one service:

| Piece | Where | Notes |
|---|---|---|
| 8 portal tools | `nodes.py:4270–4834` (closures inside `converse`) | `fetch_from_portal`, `check_portal_download`, `save_portal`, `lookup_portal`, `list_portal_workflows`, `describe_portal_workflow`, `run_portal_workflow`, `schedule_portal_workflow` |
| Gate | `nodes.py:170–176`, registration `:6842–6851` | `BROWSER_USE_ENABLED` + Developer-role (or `BROWSER_USE_ALLOW_ALL_USERS`) |
| Auto-mode (the repro path) | `command_center/tools/portal_fetch.py:127,159` | `POST {bu}/portal/start` → poll `GET {bu}/portal/result/{run_id}`; header `X-AIHub-Internal: API_KEY` |
| Workflow-mode (deterministic replay) | `command_center/tools/portal_workflow_run.py:40,93` | `run_workflow_by_name()` → `POST {bu}/workflow/run`; **same in-proc function** the Workflow Designer Portal node (`workflow_execution.py:4539`), builder UI, scheduler, and the NL workflow builder (`WorkflowAgent.py:830–855`, which emits Portal nodes with a `portalWorkflowSlug` placeholder) all converge on |
| Engine | `browser_use_service/` :5101 (HOST_PORT+100) | `portal_runner.py:536` (browser-use/Playwright, headless forced), `workflow_runner.py:581` (step replay), cobrowse/2FA takeover (`run_registry.AWAITING_HUMAN`, `sign_cobrowse_token`) |
| "Working in the portal…" | `nodes.py:4418` | 10s heartbeat via `graph/progress.py` ProgressQueue → SSE → status pill. **Only auto-mode has this** — CC's `run_portal_workflow` is a silent 600s blocking call (`nodes.py:4699`), no heartbeat |
| Portal registry | `data/portal_registry.json` via `command_center/tools/portal_registry.py` | Per-user, APP_ROOT-aware, atomic writes; holds **key names only** |
| Credentials | `local_secrets` (`data/secrets/secrets.json.enc`) keys `PORTAL_U<uid>_<SLUG>_USERNAME/_PASSWORD/_TOTP` | Resolved **server-side** in browser-use (`browser_use_config.get_secret`); TOTP computed there (pyotp); into the browser only as `sensitive_data` placeholders, never prompt text |
| Saved workflows | `data/portal_workflows.json` via `command_center/tools/portal_workflows.py` | browser_use_service itself imports this store cross-package (`browser_use_service/main.py:461`) — precedent that these stores are import-safe outside Flask |
| File delivery | harvest = snapshot diff of `data/browser_use_downloads/<session_id>/` (`portal_runner.py:746`) → CC `ArtifactManager` chips → `/api/artifacts/<id>/download` | Requires shared filesystem (both true on-box) |
| Scheduling | job type `portal_workflow` (`job_scheduler.py:134,1272`) → `POST /api/portal-workflows/internal/run` (HMAC) | Scheduled delivery = email attachment |

Known CC wart worth NOT copying: cross-turn run tracking is a module-global in-process dict (`portal_fetch._LAST_AUTO_RUN`, `portal_fetch.py:85`) — lost on CC restart. Also `POST /portal/fetch` (sync) is a dead path with no production callers; the live pair is `/portal/start` + `/portal/result`.

## 3. What The Agent has to build on (verified)

- **Extension seam is proven and small:** new module → export list → two lines in `brain.py` (import + concat at `brain.py:46–50`), kill-switch template at `brain.py:42–44` (`AGENT_DOCUMENT_TOOLS` pattern).
- **`sys.path` already includes APP_ROOT** (`agent_config.py:41–42`) → `command_center.tools.portal_registry / portal_workflows / portal_workflow_run` and `local_secrets` are directly importable (the installer already ships `local_secrets` with the agent service).
- **Long-job honesty pattern exists:** `dry_run_automation` → `check_automation_run` poll doctrine (`authoring_tools.py:10–17,350`), and `schedule_agent_task`'s read-back verification (`work_tools.py:356–363`).
- **File last-mile exists:** `offer_file_download` staging (`file_tools.py`) + Bearer-auth `GET /api/files/<id>`; harvested portal files land under APP_ROOT (`data/browser_use_downloads/`) so the under-root staging rule passes. The Agent's UI already renders `/api/files/` links as auth-fetch download buttons.
- **Secrets redaction exists:** `SENSITIVE_TOOL_FIELDS` (`brain.py:74`) hides values from tool chips.
- **Cobrowse/2FA is reusable as-is:** takeover link is a main-app route (`/portal-workflows/cobrowse/<run_id>`, ownership-checked, `sign_cobrowse_token`) — The Agent only needs to print the URL; the user's main-app session authenticates it.
- **Progress UX today:** tool chips pulse amber with a live elapsed timer; events fire only at tool dispatch/return — no intra-tool channel (yet).

## 4. Proposed plan (additive, kill-switched)

### P1 — `agent_service/portal_tools.py` (closes the repro; ~a day incl. tests)

Seven tools, template = `integration_tools.py` + `document_tools.py`:

| Tool | Mode | Seam | Class |
|---|---|---|---|
| `lookup_portal(name="")` | read | `portal_registry` import | read |
| `save_portal(name,url,username,password,totp,allowed_domains)` | write | `local_secrets.set_local_secret` + `portal_registry.save_portal` | MUTATING + redacted fields |
| `portal_fetch(portal_or_url, task, [creds], upload_file="")` | auto | `POST {bu}/portal/start`, poll `result` **inside the tool** up to ~120s; return **early** with cobrowse link on `awaiting_human`; past deadline return `run_id` + "still running — check_portal_run" | MUTATING + redacted creds |
| `check_portal_run(run_id)` | auto | one poll; on success **stage files + return ready `[⤓ name](/api/files/<id>)` links in the tool body** (deterministic — don't rely on the model to chain `offer_file_download`) | read |
| `list_portal_workflows()` / `describe_portal_workflow(name)` | read | `portal_workflows` import | read |
| `run_portal_workflow(name, upload_file="")` | replay | import `portal_workflow_run.run_workflow_by_name` (the four-consumer convergence seam; gives credential-key resolution + `record_run` bookkeeping for free), run in thread, stage + offer files | MUTATING |

Deliberate improvements over CC while porting:
- **Explicit `run_id` threading** (survives restarts via the disk-persisted session transcript) instead of CC's in-process `_LAST_AUTO_RUN` global.
- **Bounded poll + honest handoff** instead of a 10-minute silent block.

Registration checklist (the four hand-maintained lists in `brain.py` — the real drift hazard):
1. `MUTATING_TOOLS` += `portal_fetch`, `save_portal`, `run_portal_workflow`
2. `_READ_TOOL_NAMES` += `lookup_portal`, `list_portal_workflows`, `describe_portal_workflow`
3. `SENSITIVE_TOOL_FIELDS` += `save_portal.{password,totp}`, `portal_fetch.{password,totp}`
4. `SYSTEM_PROMPT` PORTALS section — **positively framed** ("YES — you can sign into vendor/customer web portals to download and upload documents…"), lookup-first doctrine, delivery rule (always end with `/api/files/` offer links, never server paths), takeover etiquette (relay link → wait → `check_portal_run`), offer-to-save after a successful ad-hoc run (CC parity). *Twice-learned lesson: lead with capability; boundaries only-when-asked.*

Gating: `AGENT_PORTAL_TOOLS` (new agent-local kill switch, default true) **and** the platform's existing `BROWSER_USE_ENABLED`; per-user Developer-role check inside tool bodies honoring `BROWSER_USE_ALLOW_ALL_USERS` — so one platform flag governs the capability everywhere.

Companions: product skill `aihub-portals/SKILL.md` (saved-portal flow, first-time ad-hoc + save, 2FA takeover, upload mode, auto-vs-replay decision, scheduling pointer) + routing line in `aihub-platform-navigation`. *Lesson from the save_view ticker miss: tool descriptions outrank skills — enumerate all modes in the descriptions themselves.*

### P2 — parity polish (1–2 days, after P1 proves out)

- **True "Working in the portal…" streaming:** per-session progress queue in agent_service (CC's `graph/progress.py` is the reference), drained by `main.py`'s SSE generator alongside SDK messages; new `tool_progress` event → chip sub-status. Benefits *all* long tools (dry-runs, imports), not just portals.
- `schedule_portal_workflow` tool (scheduler REST + read-back; mind the portal_workflow `target_id` INT coercion quirk).
- **Headless takeover → My Work:** scheduled agent run hitting `AWAITING_HUMAN` raises a work item with the cobrowse link instead of timing out (better than CC's email-only path).
- Scope note: The Agent has no user→agent chat upload yet, so upload-mode runs are limited to server-visible files for now — separate, pre-existing gap.

### P3 — pack 20 additions

- **P-1 capability honesty:** "can you connect to web portals and download documents?" — graded on `lookup_portal` use and NOT leading with no (A6-5 email pattern).
- **P-2 live E2E:** saved demo portal (`meridian_vendor_portal` → localhost:3000 demo panel) — real turn "connect to the Meridian vendor portal and download the latest invoice" → assert portal tool called, file staged, `/api/files/<id>` serves bytes, cross-user 404, no server path in reply.
- **P-3 replay ground truth:** `run_portal_workflow` flips `last_run_status`/`success_count` in the store.
- **P-4 redaction:** `save_portal` chip shows no secret values.

## 5. Risks / open items

| Risk | Disposition |
|---|---|
| `@tool()` adjacency + export-list-by-function-name quirks | Known; checklist above |
| `X-AIHub-Internal` token: confirm agent service env resolves the same `API_KEY` browser-use enforces (`BROWSER_USE_AUTH_ENFORCE`) | Build-time verify |
| `command_center/tools/portal_fetch.py` top-level imports clean for cross-package use (CC artifact manager is lazily imported inside `_register_artifacts` only — don't touch that path) | Build-time verify; fallback = 60-line agent-local client for `/portal/start|result` |
| Both CC and The Agent writing `portal_registry.json` (atomic whole-file, last-writer-wins) | Accepted — same exposure browser_use↔CC already has on `portal_workflows.json` |
| Ad-hoc creds pasted in chat ride the SDK transcript | Same accepted caveat as `store_platform_secret`; chips redacted; loopback only |
| Main-app `POST /api/portal-workflows/internal/run` may email attachments as part of the route | Avoided by using `run_workflow_by_name` import instead |
| Pre-existing drift found in passing: `_READ_TOOL_NAMES` is missing `list_code_flows` + `list_skills` (side-threads silently lack them) | Separate 2-line fix, flagged as its own task |
| Workflow **node-type** registries | Untouched by this plan — P1 adds agent tools, not a node type, so the Portal node's four-registry sync fan-out (engine dispatch `workflow_execution.py:595`, schema `workflow_node_schemas.py:184`, `VALID_WORKFLOW_NODE_TYPES` `system_prompts.py:2857`, designer UI `static/js/portal_node.js:23`) stays as-is |

## 6. Decisions for James

1. **v1 scope:** both modes (auto fetch + workflow replay)? *Recommend yes — both are thin; auto-mode is what the repro used.*
2. **Ad-hoc inline credentials in chat** (CC parity) or saved-portals-only? *Recommend parity — it's the on-ramp to `save_portal` governance.*
3. **Progress side channel** in P1 or defer to P2? *Recommend defer — bounded-poll returns + chip timers are honest and adequate; the side channel is a one-time harness feature worth doing for all tools together.*
4. **Flag default:** `AGENT_PORTAL_TOOLS=true` (inert anyway unless `BROWSER_USE_ENABLED`)? *Recommend true, matching document tools.*
