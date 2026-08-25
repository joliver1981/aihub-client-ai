# Handoff spec — The Agent for ALL users (launch package)

**Owner:** `agent_service` (:5111) + main app (3 small edits)
**Type:** guarded feature rollout
**Status: FULLY SHIPPED 2026-08-24.** Items 3–8 in commit dac9ce6 (unit suite
`tests_v2/unit/test_agent_allusers_gates.py` 16/16; pack-20 U-1..U-7; live
replay 6/6; UI smokes 20/20 + 10/10). Full gate ran 83/84 — the lone FAIL was
the runner's cp1252 subprocess decode eating the UI smoke's UTF-8 output
(smoke itself 20/20 directly; decode pinned to utf-8 and replayed PASS in
44ecb08), so the gate was green in substance. Items 1–2 in commit 44ecb08 +
`AGENT_ALLOW_ALL_USERS=true` in the machine-local .env; both services
restarted. Live role-1 E2E 3/3: /api/me 200 with haiku surfaced, live turn
grounded + honestly refused a nonexistent connection, anonymous /the-agent →
302 login.

## Vision (james, verbatim intent)

The Agent interface is the future of the platform **for all users. Period.** It
should help users with anything and everything within their business
environment. Guardrails live in identity/ACL at the platform seams and My Work
approvals — not in feature removal.

## Locked decisions (2026-08-24)

| # | Decision |
|---|---|
| D1 | Regular users (role 1) CAN schedule their own agent tasks on day 1 — split scheduling out of the build gate |
| D2/D6 | Per-user daily **turn cap built as an admin setting, DEFAULT OFF** (0 = unlimited). Not a launch blocker; exists so production clients can turn it on. No other spend guardrail. |
| D3 | Role-1 users function the same as devs/admins on day 1 **including the home-route auto-redirect** into The Agent. No bake period. `?classic=1` sticky escape hatch already works for everyone. |
| D4 | **Per-role model**: role<2 users run a separate model, admin-settable via The Agent's existing admin UI (M-1 pattern), **default `claude-haiku-4-5-20251001`**. Role≥2 keeps `AGENT_MODEL`/settings override (sonnet-5 today). |
| D5 | Phase 2 = running playbooks *shared to them* (group-based share model, like integrations). Phase 3 = portals for users (group-gated). Neither in this package. |
| — | Authoring (automations/code flows/workflows) and portal tools stay Developer+ — unchanged gates. |

**Known caveat accepted with D4:** haiku failed pack A0-6 (fabrication probe —
invented "170+ others" connections). Mitigation: the new role-1 pack section
re-runs the fabrication probe against the role-1 model; admins can flip role-1
to sonnet in the UI if quality complaints surface. Do not re-litigate the
default — james's call.

## Work items

### 1. Main app — open the three hard-coded gates
- [`app.py:1949`](../app.py) `/the-agent` route: `@developer_required()` →
  `@login_required` (keep the `THE_AGENT_ENABLED` check + sticky-classic pop).
- [`templates/base.html:1159`](../templates/base.html) nav link: drop
  `current_user.role >= 2` (keep `is_authenticated and FLAG_THE_AGENT`).
- [`app.py:1619`](../app.py) home redirect (`THE_AGENT_MODE`): drop the
  `role >= 2` condition.
- Needs app 5001 restart. Installer/env defaults elsewhere unchanged.

### 2. Service — `AGENT_ALLOW_ALL_USERS=true`
`.env` on this box (machine-local, not committed) + targeted 5111 restart.
`/health` `allow_all_users` flips to true. The 403 branch
([`main.py:102`](../agent_service/main.py)) stays as the code-level gate for
installs that keep the flag off.

### 3. Filesystem tools scoped for role<2 (was blocker B2)
`read_file` + `list_server_files` (`document_tools.py`) for role<2:
- ALLOW: `/api/files/<id>` refs (via `resolve_api_files_ref` — their own
  staged/delivered files: portal downloads, email attachments) and paths under
  their own `data/agent/users/<uid>/` tree.
- REFUSE (honest, named): any other host path. Today these tools are host-wide
  minus only `data\secrets` + `C:\Windows` — fine for Dev+ (box access anyway),
  not for everyone. Role≥2 behavior unchanged.

### 4. Secrets seam gated for role<2 (was blocker B3)
`store_platform_secret` / `list_secret_names`: role<2 → honest refusal for now
(portals are Phase 3; revisit with per-user scoping then).
**VERIFIED 2026-08-24:** the Local Secrets store is **TENANT-GLOBAL** —
`app.py /workflow/secrets/list` calls `list_local_secrets()` and
`/workflow/secrets/store` calls `get_secrets_manager().set(...)`, neither takes
any user identity. Every user reads the same name list and writes the same
shared store, so a role-1 write could clobber a credential the tenant's
automations reference. Developer+ gate is therefore correct, not just cautious.

### 5. Scheduling split (D1)
`schedule_agent_task` ([`work_tools.py:637`](../agent_service/work_tools.py)) +
`schedule_view_email` / `schedule_view_refresh` (`views_tools.py` — the two
`AGENT_BUILD_ALLOW_ALL_USERS` checks): move to a new flag
`AGENT_SCHEDULE_ALLOW_ALL_USERS`, **default true**. Portal workflow scheduling
(`portal_tools.py:897`) stays on the build gate. Bounded recurrence + tz
contract already work for any principal; headless runs already execute as the
stored principal, so role-1 scheduled turns hit the same tool gates.

### 6. Per-role model (D4)
- `data/agent/settings.json` gains `role1_model` (runtime override, same file
  as the existing `model` override; empty → default `claude-haiku-4-5-20251001`,
  constant in `agent_config.py`, env-overridable `AGENT_MODEL_ROLE1`).
- `brain.run_turn` model pick: role≥2 → existing chain (settings.model →
  AGENT_MODEL); role<2 → settings.role1_model → AGENT_MODEL_ROLE1 default.
  Applies to chat, side-threads, headless `/api/run`, email-triggered turns —
  everywhere the principal's role is in the envelope.
- Admin UI: second row next to the existing admin-only model override
  (clickable brain line / `/api/settings/model` pattern, role≥3). `/health`
  reports both (`model`, `model_role1`).

### 7. Optional per-user daily turn cap (D2/D6) — DEFAULT OFF
- Mechanism (answers james's "how would you cap and track"): one table in the
  service's own `mywork.db` — `agent_usage(user_id, day, turns)` — incremented
  once per brain turn (chat, `/api/run`, email poller). No cloud, no relay.
- Setting `turns_per_day` in `settings.json` (admin UI row, role≥3);
  `0`/absent = OFF = no cap check (the counter still increments — free
  usage telemetry either way).
- When ON and exceeded: honest "daily limit reached — resets at midnight
  (<tz>)" refusal BEFORE any LLM call. Applies to role<2 only? NO — applies to
  every non-admin (role<3) when on, simplest honest semantics; admins exempt.
- Day boundary = server-local date; document in the setting label.

### 8. Pack-20 role-1 persona section (was blocker B4)
New checks with `_tok(uid, role=1)` (fresh throwaway uid):
- ALLOWED: data Q&A over the identity whitelist, document search (ACL),
  views save/run, schedule_agent_task one-shot (deleted after), email status.
- REFUSED (honest): automation authoring, portal tools, arbitrary-path
  read_file/list_server_files, store_platform_secret.
- MODEL: role-1 turn runs the role1 model (assert via transcript/model line).
- FABRICATION probe (A0-6 style) against the role-1 model — the known haiku
  weak spot; grade with the hardened marker list.
- Cap check: temp-set turns_per_day=2, third turn refused, then restore OFF.
- Unit: gate tests for items 3/4/5/6/7 (force-add; gitignore hides test*.py).

## Explicitly OUT of scope
- Any other spend guardrail/metering, relay migration 001.
- Shared-playbook runs (Phase 2), portals for role 1 (Phase 3).
- Builder/portal gate changes. THE_AGENT_NAV_LENS stays off.

## Rollout / rollback
- Order: items 3–8 (service, gate-neutral) → pack green → items 1–2 (open the
  doors) → live E2E as a real role-1 user → flip nothing else.
- Restarts: 5111 targeted (kill PID → `start_agent_service_dev.bat` → /health);
  app 5001 via V3 bat (redirect to file, never pipe).
- Rollback = `AGENT_ALLOW_ALL_USERS=false` (+ the nav/redirect edits are inert
  for role-1 once the service 403s them; full revert = 3 tiny diffs).

## Effort
~2–3 days total: items 3/4/5 ≈ 1d, item 6 ≈ ½d, item 7 ≈ ½d, item 8 ≈ 1d,
items 1/2 ≈ ½d incl. E2E.
