# Handoff spec — Connection access control for The Agent (regular users)

**Owner:** main app (`app.py`, `connection_acl.py`) + `agent_service` (:5111)
**Type:** security hardening, purely additive, ship-with-kill-switch
**Status: BUILT + LIVE-VERIFIED on the dev tree, 2026-09-22 (Option A).** James's decisions
the same day: **Option A**, floor role ≥ 2, 403 + honest text, **no write-path work** ("that is the
client's job to block with the logon they provide") — so §5 items 4 and 5 were NOT built; the
sandbox token simply follows the platform-filtered index (proved live below). Command Center (§5
item 7) and the classic agent routes (§9) are NOT in this build.

**What shipped:** `connection_acl.py` (resolver, floor `CONNECTION_ACL_UNRESTRICTED_ROLE`=2);
`app.py` `_caller_connection_scope` / `_connection_access_refusal` on `/get/connections` and the
three `/api/discover/*` routes, kill switch `CONNECTION_ACL_ENFORCE` (default true; `false` = old
behaviour + dry-run log line); `app_onedir.spec` hiddenimport; agent side
`platform_tools.restricted_caller()` + role-keyed wording, `match_connection(restricted=)` (no
numeric-id pass-through on an empty index for a scoped caller), `ConnectionAccessDenied` relay in
schema/probe/search/export/view tiles; one ACCESS SCOPE bullet in the system prompt.
**Tests:** `tests_v2/unit/test_connection_acl_resolver.py` (6) + `test_connection_acl_routes.py`
(14) — pytest, main env; `test_agent_connection_acl_wording.py` (8) — aihub-agent env standalone;
existing `test_agent_scope_honesty` 16/16, `test_agent_pass3_tools` 10/10,
`test_agent_platform_headers` 6/6, `test_agent_brain_tool_lists` 8/8 unchanged
(`test_agent_allusers_gates` 15/16 — the `test_model_pick_by_role_and_overrides` failure is the
persisted `data/agent/settings.json` model override, pre-existing and unrelated).
**Live (dev tree, main :5001 + a `:5112` all-users Agent instance, fixture = agent 876 shared with
group 59 then removed):** platform probes with the service key + signed assertions — baseline
`ru_alex`/`ru_casey`/`ru_drew` → `[]` and every discover call 403 `access: denied`; after the
fixture `ru_alex` → `[20]`, query/tables/schema on 20 → 200, on 168 → 403; `ru_drew` → 403;
`dev_erin`/`admin`/no assertion → all six; forged → 403. Run-token scope: `ru_alex` → `['ERPDB']`
(PHARMA "not declared in this run's scope"), `ru_drew` → `[]`, `dev_erin` → all six. Real turns
(haiku, role 1): `ru_alex` listed ERPDB only with the scoped wording and answered the AIRDB question
as "not shared with your account … ask your administrator"; `ru_drew` relayed the deny-all wording;
`dev_erin` listed all six. Transcripts: session scratchpad `probe_turns.json`.
**Not yet on 10.0.0.6** — needs the next build/reinstall.

---

## 1. The finding

Since v2.1 / v2.2 the installer forces `AGENT_ALLOW_ALL_USERS=true`
(`AIHub_Setup_Script_v7_OneDir_Dev.iss` ~1338-1358), so role-1 (End User) seats
reach The Agent. The Agent's data tools call the main app with the platform
**service key** (`X-API-Key`). Every route decorated
`@api_key_or_session_required(...)` treats a valid key as a trusted internal
caller and **skips the role check entirely** (`role_decorators.py:560-580`).
The `min_role=2` on those routes only ever applied to browser sessions.

James's test result ("YES, The Agent can list connections and query any of them
as a regular user") is exactly that. The full set of doors a role-1 user has
today:

| Door | Tool → main-app route | Role 1 gets today | Classic mode gave role 1 |
|---|---|---|---|
| List | `list_data_connections` → `GET /get/connections` (`app.py:3351`, min_role=2 for sessions) | every tenant connection | nothing (Developer+ page) |
| Schema | `get_connection_schema`, `search_tables` → `/api/discover/tables|schema/<id>` (`app.py:17284`, `17446`) | any connection | nothing |
| Read rows | `probe_connection_query` → `/api/discover/query/<id>` (`app.py:17372`; sql_gate, ~50-row cap) | any connection | only through a Data Assistant shared with their group |
| Bulk read | `export_data(connection, sql)` → sandbox `aihub.query` (tool-side SELECT-only gate; up to 100k rows) | any connection | nothing |
| **Read + WRITE** | `run_python` → sandbox `aihub.query(...)`; the run token's allowlist is **every connection name** (`agent_service/code_tools.py:54-65`, `151-160`); `aihub_runtime.query` **commits** non-SELECT (`automations/sdk/aihub_runtime/__init__.py:297-340`) | **any connection, any statement** | nothing |
| Dashboards | `save_view` SQL tiles → `/api/discover/query` (`views_tools.py:71-90`) | any connection | Data Explorer is agent-scoped |
| Delegation | `ask_agent` → `/api/agents/<id>/chat` | **correctly gated** (doc-acl G3: role 1 = group-shared agents only) | same |

**The row nobody has exercised is `run_python`.** The regular-user pack
(RU-28, 2026-09-07) proved the SELECT-only gates on export/probe and the build
gate on the automation lane and concluded "role 1 has no write path". But in
transcript RU-28c The Agent itself offered, verbatim: *"Run it once via
run_python — I execute the UPDATE through the Python interpreter with the aihub
SDK"*, and the tester chose the other path. In code: `run_python` has no role
gate, is on by default (`AGENT_RUN_PYTHON_TOOL=true`), and its token covers
every connection. **Not live-verified in this session** (no write was attempted,
deliberately); the code path is unambiguous.

**Root cause in one sentence:** the per-user identity already travels on every
Agent → platform call as a signed `X-AIHub-User` assertion
(`platform_tools._headers()` / `_user_assertion_header()`, lines ~120-150,
which RAISES rather than sending identity-less), but the connection and
discovery routes never read it. Only the document, agent-chat and email-approval
routes do (doc-acl G1-G3, F-7 part 2).

For the record, the tester brief recorded this as an accepted posture — brief 3
§RU-05 *"Data connections are visible to everyone (confirm the posture)"*, with
the instruction to "file a posture note regardless … James should see it stated
next to the all-users decision". This document is that decision point.

---

## 2. The access model the platform already has

Classic mode: a regular user reaches data **only** through Data Assistants
(data agents) shared with their groups.

```
user -> UserGroups -> AgentGroups -> Agents(is_data_agent=1, enabled=1)
     -> AgentConnections -> Connections
```

Implemented in `DataUtils.select_user_agents_and_connections`,
`fetch_user_agents`, `accessible_agent_ids` (role ≥ 3 unrestricted, else
group-filtered). Admins grant access on the **Groups** page
(`POST /save/permissions`, admin only). Developers (role 2) see every connection
(`/get/connections` is min_role=2) and can build a Data Assistant on any of
them — so in practice Developers already have full connection reach, and the
2026-09-03 "can list it, can ask it" rule made that explicit for agent chat
(`_agent_visibility_filter(unrestricted_from_role=2)`).

**Dev tenant snapshot (read-only query, 2026-09-22):** 6 connections. Five have
an enabled data agent shared with group 5 *Analysts*: AIRDB ← 228, AIRDB2 ← 281,
EDW (SQL Server) ← 139, EDWDB (Postgres) ← 14, ERPDB ← 876. Agent 283 (AIRDB2)
is shared with nobody. **PHARMA (168) has no data agent at all** — under Option
A it is invisible to every regular user, which is exactly the classic posture.
Groups: 1 General Group, 2 Admins, 3 Developers, 4 End Users, 5 Analysts (+ the
RU test groups 6/14/17).

---

## 3. Precedents to reuse (all shipped, all tested)

- `app._caller_identity()` (`app.py:6364`): optional assertion → `(uid, role)`.
  Header absent = unrestricted (scheduler / dispatcher / CC keep working);
  present + valid = that identity; present + invalid = `_InvalidUserAssertion`
  → hard **403**, never "treat as missing".
- `_agent_visibility_filter(strict, unrestricted_from_role)` (`app.py:2592`):
  the role-floor parameter pattern.
- `DataUtils.accessible_agent_ids` (`:1791`) and `doc_search_v3/acl.py`: the
  three-state contract `None` = unrestricted / `[ids]` = allow list / `[]` =
  deny-all, **fail closed on any error**. ⚠ Never write `if allowed:` — an
  empty list is falsy and becomes "no filter" (the trap acl.py exists to guard).
- `platform_tools._headers()` already sends the assertion on EVERY Agent call
  and raises on signing failure. Nothing to add on the wire.
- F-2 wording lesson (RU pack): a restricted view must be described as an
  **access restriction**, never as "does not exist / store is empty". The
  deny-all document wording that tested well: *"this is a permissions setting,
  not that documents don't exist … an admin grants it on the Groups page"*.
- `tests_v2/unit/app_route_harness.py` lifts `app.py` routes out by AST — the
  established way to unit-test these routes, including the real
  `api_key_or_session_required` wrapper.

---

## 4. Options

### Option A — Derive connection access from shared Data Assistants  ← RECOMMENDED (Phase 1)

`allowed_connections(user) = connections of enabled data agents shared with
the user's groups`. Zero schema, zero new UI, the classic model verbatim. The
rule becomes one sentence for admins and for the model: **"The Agent can touch
exactly the databases your Data Assistants can touch."**

- Risk: **LOW.** Additive. Only assertion-bearing calls for role < floor change
  behaviour; Developers, admins, browser sessions and identity-less service
  callers are untouched. The only failure mode is a regular user seeing *fewer*
  connections, never more.
- Residual: a shared Data Assistant grants raw `SELECT` on the **whole**
  connection through probe/export, not just its dictionary tables. Not a new
  class of exposure — the data agent itself already runs LLM-authored SQL over
  the whole connection — but worth saying once. And the **write path must be
  closed separately** (§5 item 4); otherwise a shared read grant is a write
  grant via `run_python`.

### Option B — Explicit per-connection group grants (Phase 2, optional)

New table `ConnectionGroups (id, TenantId, connection_id, group_id, create_date,
created_by)` mirroring `DocumentCategoryGroups` (migration 016), an admin UI on
the Connections page ("Share with groups") or the Groups page, resolver =
union(A, B). Lets an admin grant a connection without first building a Data
Assistant, and is the natural home for a later per-connection / per-group
`writes_allowed` flag (`docs/handoff-the-agent-sql-tool.md` §4).

- Risk: **MEDIUM.** A migration with no runner (016/021 precedent — hand-run);
  the resolver MUST treat a missing table as "no explicit grants, fall back to
  A", never deny-all; new UI to maintain; more to explain. Do it only if James
  wants connection access decoupled from Data Assistants.

### Option C — Role floor: raw-SQL tools become Developer+ (fallback / emergency design)

Per-role `allowed_tools` in `brain._make_options` (the list already varies by
`tool_scope`): role 1 loses `probe_connection_query`, `get_connection_schema`,
`search_tables`, `export_data(sql)`, the `run_python` SDK token and SQL view
tiles; keeps `ask_agent` and everything else. Smallest possible change,
agent-side only.

- Risk: LOW technically, but it is **feature removal** — against the locked
  all-users vision ("Guardrails live in identity/ACL at the platform seams and
  My Work approvals — not in feature removal") — and it leaves the platform
  routes open to any service-key caller (Command Center). Keep the design in
  the drawer as an emergency lever, not as the fix.

### Option D — Rely on `AGENT_ALLOW_ALL_USERS=false`

The documented instant retreat to Developer+ (in the .iss comments). Available
**today** on any install with no build. It also gives up the whole all-users
rollout.

**Levers available today with no code**, for James to weigh as interim posture:
`AGENT_ALLOW_ALL_USERS=false` per install (Developer+ only);
`AGENT_RUN_PYTHON_SDK=false` on the agent service removes the sandbox's platform
token for **everyone** (closes the chat-lane write path platform-wide, but also
Developers' legitimate `aihub.query` in `run_python`; automations unaffected).

---

## 5. Recommended package (Phase 1) — work items

1. **Resolver** `DataUtils.accessible_connection_ids(user_id, user_role)` —
   sibling of `accessible_agent_ids`, same three-state contract. role ≥ floor →
   `None`; else `SELECT DISTINCT ac.connection_id FROM AgentConnections ac JOIN
   Agents a ON a.id = ac.agent_id AND a.is_data_agent = 1 AND a.enabled = 1 JOIN
   AgentGroups ag ON ag.agent_id = a.id JOIN UserGroups ug ON ug.group_id =
   ag.group_id WHERE ug.user_id = ?`; `sp_setTenantContext` first; any error →
   `[]`. The floor is ONE constant, `CONNECTION_ACL_UNRESTRICTED_ROLE`, default
   **2** (matches `/get/connections`' own session gate and the agent-chat rule).

2. **Platform routes read the assertion — this is the boundary.**
   - `GET /get/connections` (`app.py:3351`): `_caller_identity()`; role < floor
     → filter the DataFrame to the allowed ids (`[]` → empty list). Absent
     assertion → unchanged. Forged → 403.
   - `/api/discover/tables|schema|query/<id>` (`app.py:17284/17446/17372`):
     same; `id ∉ allowed` → **403**
     `{"success": false, "access": "denied", "error": "Connection <id> is not
     shared with your account — an admin shares a Data Assistant that uses it
     with one of your groups (Groups page)."}` — a terminal, honest answer the
     model relays (F-2 lesson; the `fallback:false` idea from G1).
     Alternative: 404 "hidden == missing" (doc-ACL style) — decision §8.3.
   - Kill switch `CONNECTION_ACL_ENFORCE` (main-app env, default **true**;
     `false` = legacy tenant-wide behaviour with a log-only line
     `[conn-acl] would have filtered <n> connections for user <id>`). Restart
     5001 only.

3. **Agent side — UX honesty, not the boundary** (`agent_service/platform_tools.py`).
   - `list_data_connections`: role < floor and non-empty → append *"This list is
     scoped to your access; other connections may exist that you cannot see."*
     Empty and role < floor → *"No data connections are shared with you — this is
     an access setting, not an empty platform; an admin shares a Data Assistant
     with one of your groups on the Groups page."* Static, role-keyed wording —
     no regex, no LLM judgment.
   - ⚠ `match_connection` fail-open (~`:232-236`): when the index is EMPTY a
     numeric id passes straight through ("index unavailable" heuristic). For
     role < floor an empty index means deny-all → refuse. Keep the pass-through
     for role ≥ floor. (The platform 403 already covers it; this is the belt.)
   - `search_tables`, the per-turn coverage ledger and `coverage_footer()`
     inherit the filtered index automatically — "Queried this turn: all N
     connections" now counts only the user's connections, which is correct.
   - Relay the platform's `access: denied` text verbatim from probe / schema /
     export errors.

4. **NOT BUILT — James's decision 2026-09-22.** Writes on a connection a regular user CAN see
   remain possible through `run_python` + `aihub.query`; blocking them is the client's job via
   the database login they provide. Kept below for the record.
   **Chat-lane run-token scoping — closes the write path** (`code_tools.execute_python`).
   Make the token's `connections` an explicit parameter per caller instead of
   the global `_connection_names()`:
   - `export_data` → exactly the one connection it resolved (it is SELECT-gated
     by `sql_is_select_only`).
   - `run_python` → `[]` for role < floor (**no platform databases inside the
     sandbox for regular users** — they keep probe/export/schema through the
     platform's gated routes; uploads, pandas, charts, files unchanged) and the
     already-filtered index for role ≥ floor (unchanged for Developers).
   - `manipulate_pdf` → `[]`.
   Existing kill switch `AGENT_RUN_PYTHON_SDK` still applies.

5. **NOT BUILT (same decision; the token's allowlist already follows the filtered index, proved
   live).** **Belt-and-braces at `POST /automations/api/runtime/resolve`**
   (`automations/api.py:2125`): add a `role` claim to `sign_code_run_token`
   (`shared_auth.py:196`, additive — old tokens verify as before). For
   `code_run`-flavour tokens with `role < floor`, re-check `name` against
   `accessible_connection_ids` **by name** before resolving. Automation-flavour
   tokens untouched (Developer+ lane; F-8 is a separate decision, §7).

6. **Views** — no change needed. `_run_sql_tile` goes through
   `/api/discover/query` with the *refreshing* user's assertion; a shared view
   over a connection the viewer can't access renders a tile error carrying the
   denial text (honest). Scheduled refresh / HTML email runs as the schedule
   creator — unchanged.

7. **Command Center — same hole through a second door.** CC's
   `graph/workflow_tools._headers()` (`:115`) sends only the service key and CC
   calls `/get/connections` (`:206`) and the discover routes (`nodes.py:6499,
   6583, 6638`). This matters wherever `CC_ALLOW_ALL_USERS=true` — which it IS
   in this dev tree's `.env` (line 238); the installer does not force it. Once
   the platform routes honour the assertion, CC only needs to mint
   `X-AIHub-User` from its graph user context in `_get/_post` (`nodes.py:2497`
   already does exactly that for documents). Separate small change; decision
   §8.6 on bundling.

8. **Tests.**
   - Unit (main env, route harness): resolver three states; the four routes ×
     {absent, valid role-1 with grants, valid role-1 with `[]`, valid role-2,
     forged} → {unchanged, filtered, empty/403, unchanged, 403}; kill switch
     off → unchanged + log line.
   - Unit (aihub-agent env, standalone): `match_connection` empty-index rule by
     role; `execute_python` token scope per lane (export = one name, run_python
     role-1 = none, role-2 = all); wording branches of `list_data_connections`;
     `test_agent_platform_headers` still 6/6.
   - Unit: `runtime_resolve` role re-check for `code_run` tokens; automation
     tokens unaffected.
   - Live on the dev tree (both services restarted, `AGENT_ALLOW_ALL_USERS=true`
     on the `:5112` retest instance so `:5111` is left alone). Needs one
     fixture: share data agent 876 (ERPDB) with *Agent Test A* (Alpha/Bravo are
     general agents). Then: `ru_drew` (no group) → 0 connections, probe id 20 →
     denial text, `run_python` `aihub.query("ERPDB", …)` → *"not declared in
     this run's scope"*; `ru_alex` (A) → ERPDB only, probe + export work,
     `aihub.query` in `run_python` refused; `ru_casey` (B) → 0 (or B's grant);
     `dev_erin` (role 2) → all 6; admin → all 6; forged assertion → 403; no
     assertion (CC/scheduler posture) → unchanged 6; a shared view tile as
     `ru_casey` → denial tile; AIRDB/ERPDB byte-identical afterwards.
   - Gates: pack 20 T-* smoke, pack 15 `--skip-wf14`, the unit sweep. The RU
     pack's RU-05 / RU-05d expectations **flip** — update
     `docs/openclaw-tester-brief-3-regular-users.md` §RU-05 and
     `docs/openclaw-tester-setup-regular-users.md` §CONNECTIONS.

9. **Docs / release note.** One line in `RELEASE_NOTES_v2.3` and an admin
   sentence: *"To give regular users data in The Agent, share a Data Assistant
   that uses that connection with their group."*

**Rollout:** dev tree → RU seats live → build → 10.0.0.6.
**Rollback:** `CONNECTION_ACL_ENFORCE=false` + restart 5001; the agent side
degrades gracefully (index unfiltered again). `AGENT_RUN_PYTHON_SDK` and
`AGENT_ALLOW_ALL_USERS` remain the coarser levers.

---

## 6. What users will notice

- Regular users on installs where **no** Data Assistant is shared with them see
  **no** connections in The Agent — the same as classic mode. The wording tells
  them why and who fixes it.
- Regular users **with** a shared Data Assistant keep schema / probe / export
  on that connection. `run_python` can no longer reach platform databases for
  them; everything else in `run_python` is unchanged.
- Developers and admins: no change at all. Command Center: no change until
  item 7.
- The coverage line ("Queried this turn: …") counts the user's connections
  only.

---

## 7. Out of scope — separate decisions, referenced not reopened

- **F-8** — the Developer write lane: `aihub_runtime.query` commits, and the
  automation lane accepts DDL. Per-connection `writes_allowed` and the
  two-step-confirm SQL tool are designed in `docs/handoff-the-agent-sql-tool.md`
  §4. This spec closes the write lane for role 1 only.
- `/data_explorer/internal/query` (CC, API key only, no user check) — the
  delegation-authz gap recorded in `cc-security-hardening`.
- The unwired `auth_middleware` (~349 unguarded routes) — P0 legacy track.
- Read-only database logins per connection (customer-side hardening) — a
  guidance note, not a platform change.

---

## 8. Decisions needed from James

1. **Floor:** role ≥ 2 unrestricted (recommended — classic parity, and the
   "can list it, can ask it" rule) or role ≥ 3 (doc-ACL style; would also
   restrict Developers).
2. **Grant source:** Option A only now (recommended), or A + explicit
   `ConnectionGroups` (Option B) as Phase 2.
3. **Denied connection response:** 403 + honest text (recommended) or 404
   "hidden equals missing".
4. **Role-1 `run_python`:** no platform connections in its token (recommended)
   or the user's allowed set (leaves a write path on shared connections).
5. **Interim posture on installs** until the build ships: leave as is, or flip
   `AGENT_ALLOW_ALL_USERS=false`.
6. **Command Center:** bundle item 7 in the same build, or later.
7. **Classic agent routes (§9):** bundle the session-side agent gating into
   Phase 1 (same seam, same risk class) or track it separately.

---

## 9. Follow-up check (James, 2026-09-22): are GENERAL agents gated for regular users?

**Verified in code — The Agent surface IS gated for role 1** (and was live-proved
on 2026-09-03: `tbrady` role 1 → agent 2 = 403, `developer` role 2 → 200):

- `list_agents` (`agent_service/agent_builder_tools.py:509-551`): role < 2 →
  `visible_to()` = the agent is shared with one of the user's groups; footer
  *"(Showing only agents shared with your groups.)"*.
- `get_agent_config` (`:572-584`): role < 2 and not visible → refused.
- `ask_agent` → `POST /api/agents/<id>/chat` (`app.py:2711`) carrying the
  assertion → `_agent_visibility_filter(strict=True, unrestricted_from_role=2)`
  → role 1 gets 403 *"You do not have access to that agent."* unless shared;
  forged assertion → 403.
- `/api/agents/list` (`:2873`) and `/api/agents/summary` (`:2607`): filtered for
  assertion callers (that is how CC's landscape is scoped).

**NOT gated — the classic browser-session routes behind those pages.** The
visibility filter reads the assertion ONLY; a flask-login session presents none,
so it returns `None` = no filter. The pages a role-1 user *sees* are filtered
(`/assistants` → `fetch_user_agents`, `/data_assistants` →
`select_user_agents_and_connections`), but the API routes those pages call are
not — the same "authz by UI" pattern as the connections finding:

| Route | Guard | A role-1 browser session gets |
|---|---|---|
| `GET /get/agents` (`app.py:2576`) | `@login_required` | `select_all_agents_and_tools()` — EVERY general agent (description, objective, tools), no user filter |
| `GET /api/agents/list` (`:2873`), `GET /api/agents/summary` (`:2607`) | `@api_key_or_session_required()` | unfiltered (assertion-only filter) |
| `POST /api/agents/<id>/chat` (`:2711`) | `@api_key_or_session_required()` | can **chat with any** general or data agent by id |
| `POST /chat/general`, `/chat/general_system` (`:5316`, `:5171`) | `@api_key_or_session_required()` | any `agent_id`; no access check anywhere in the body |
| `POST /data_explorer/chat` (`routes/data_explorer.py:162`) | `@login_required` | any data `agent_id` → that agent's **connection is queried** — the connection hole again, classic side |

Pack 18's healthy row "user A cannot read user B's agent" (`b2`) proved only
`GET /get/agent/<id>` between two *Developers*; none of the routes above.
**Status: code-verified only** — the main app was down (`:5001` refused) during
this session, so no live probe was run.

**Task — live verification (no code):**
1. Log in as `ru_alex` (role 1; group *Agent Test A* = id 59 → agents 1035
   Alpha, 1038 Disabled) with the RU seat password from
   `docs/openclaw-tester-setup-regular-users.md`.
2. `GET /get/agents`, `/api/agents/list`, `/api/agents/summary` → count the
   agents returned against the 2 shared. Expected today: everything (gap).
3. `POST /api/agents/1036/chat` (Test Agent Bravo, group B only) and
   `POST /chat/general` with `agent_id` 386 (Retail Operations Manager, shared
   with nobody). Expected today: 200 + an answer (gap); wanted: 403.
4. `POST /data_explorer/chat` with `agent_id` 876 (AR Collections, ERPDB).
   Expected today: an answer from ERPDB (gap).
5. Control through The Agent as `ru_alex`: "list the agents" → 2 shown;
   "ask agent 1036 …" → refused. Confirms the gated surface.
6. Repeat 2-4 as `dev_erin` (role 2) — must stay unchanged after any fix.

**Fix shape (same seam, same low-risk class as §5):** resolve identity with
`_caller_identity_or_session()` (`app.py:6394`, decision D1 — already what the
document routes do) instead of assertion-only inside `_agent_visibility_filter`,
so the session user gets the same filter; listing routes filter rows, chat
routes answer 403 (`/api/agents/<id>/chat`, `/chat/general*`,
`/data_explorer/chat`); `/get/agents` applies `accessible_agent_ids` for
role < 2. Floors stay as each route has them today for assertions (chat = 2,
listing = 3) so Developers see no change. Decision §8.7.
