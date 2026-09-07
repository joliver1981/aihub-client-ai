# AI Hub — Test Environment Setup: regular-user permission fixtures

**Companion to** [`openclaw-tester-brief-3-regular-users.md`](openclaw-tester-brief-3-regular-users.md)
(scenarios **RU-01 … RU-33**). **Written:** 2026-09-05 · **App version:** 2.0 · **Repo:**
`C:\src\aihub-client-ai-dev` (services run from this working tree).

Brief 3 tests The Agent from a **regular user's seat**. Almost every scenario in it is a permission
question — *can this user see that document, that agent, that integration, that other user's file* —
and a permission question is only answerable if the environment contains **something granted and
something withheld**. On a box where everything is shared with everyone, every boundary test passes
vacuously.

**This document builds that environment.** Work through §3 in order, then prove it with §4, then
paste §5 — the **handoff block** — into the top of your test report. Brief 3 refers to accounts,
groups, categories and agents by the names defined here; the two documents are meant to be read side
by side.

**Who does this:** an admin. Two ways:

- **Scripted (recommended)** — `test_human/_scripts/seed_regular_user_fixture.py` does §3.1–§3.5 and
  §3.8 through the platform's own API, idempotently, in about 30 seconds. See §2.4.
- **By hand through the UI** — §3 step by step, if you want to see every screen.

**It has been run against 10.0.0.6 — see §8 for the build record and the filled-in handoff block.**

> **Local test credentials are used throughout and written in plain text in this document.** That is
> deliberate — this is an on-prem throwaway test box (see the `onprem-test-resources` conventions).
> Do not reuse these passwords anywhere that matters, and do not run this procedure against a
> customer install.

---

## 0. What this builds, and why each piece exists

Nothing here is decoration. Each fixture makes exactly one boundary observable:

| Fixture | Makes this testable |
|---|---|
| **Three groups**, one user in none of them | Group-scoped visibility, and fail-closed behaviour for a user with no group at all |
| **Two regular users in the *same* group** | Group-scope sharing must *reach* a peer (a boundary that is wrong when it's too tight, not just when it's too loose) |
| **A regular user in a *different* group** | Cross-group isolation — the classic leak |
| **A Developer (role 2) in the same group** | Proves a refusal is about **role**, not about something being broken. Without this, every "denied" result is ambiguous |
| **Three document categories: one granted to A, one to B, one to nobody** | The category ACL in all three states, from one seat |
| **One document type mapped to no category** | The fail-closed rule (unmapped type = admin-only) |
| **Four agents: shared with A, with B, with nobody, and one disabled** | Agent list scoping and the disabled-state edge |
| **One integration assigned to A, others unassigned** | Integration scoping, which fails *closed* (unassigned = invisible) |
| **A tenant skill, a group skill, a tenant view** | Delete-permission refusals and scope visibility need something that already exists to refuse about |
| **A platform secret** | `list_secret_names` refusing is only meaningful if there is something to list |

---

## 1. Prerequisites

| # | Check | How |
|---|---|---|
| P-1 | All services up | `shortcuts\00_Start-Restart_AIHub_Services_V3.bat` — run **detached**, never piped from an agent shell |
| P-2 | Main app answers | `curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5001/login` → `200` |
| P-3 | The Agent answers | `curl -s http://127.0.0.1:5111/health` |
| P-4 | You can sign in as `admin` / `admin` (role 3) | browser |
| P-5 | Document API `:5011` and Vector API `:5031` are up | needed for §3.5 ingest; ingest silently stalls without them |
| P-6 | SQL Server `10.0.0.6` reachable | needed for verification queries and for brief 3's oracle |

---

## 2. Step 1 — Flags, and the model decision

### 2.1 Open The Agent to all users

Edit `C:\src\aihub-client-ai-dev\.env`:

```
AGENT_ALLOW_ALL_USERS=true
```

Leave `THE_AGENT_ENABLED=true` alone. Restart services (P-1).

> ⚠ **Two traps on this line.**
> (a) The existing line carries an **inline `#` comment** after the value — keep the whitespace
> before the `#`.
> (b) Brief 2 §0 recorded `.env` saying `false` while the running service reported `true` — i.e.
> something else (a machine/user environment variable, or the NSSM service definition) was winning.
> **Trust `/health`, not the file.** If they disagree, find out which is winning before you build
> anything on top of it, and record it — a silent override of `.env` is itself a finding.

**Verify:** `curl -s http://127.0.0.1:5111/health` reports `"allow_all_users": true`.

### 2.2 Record the sub-flags — do not change them

Five more flags decide what a regular user can actually do. Read them off the box and write them
into the handoff block. **Leave them as they are** — the point is to test the shipped posture.

| Flag | Default when absent | Expected on this box | Governs |
|---|---|---|---|
| `AGENT_ALLOW_ALL_USERS` | `false` | **`true`** (you just set it) | entry to The Agent |
| `AGENT_SCHEDULE_ALLOW_ALL_USERS` | **`true`** | absent → `true` | role 1 may schedule agent tasks, View refreshes, View emails |
| `AGENT_BUILD_ALLOW_ALL_USERS` | **`false`** | absent → `false` | role 1 may create/change automations & code flows |
| `BROWSER_USE_ALLOW_ALL_USERS` | `false` | **`true` in `.env`** | role 1 may drive web portals |
| `AGENT_MY_CONNECTIONS_WRITE_TOOLS` | empty | absent → empty = **read-only** | writes through a user's own OAuth accounts |
| turn cap (`turns_per_day`) | `0` = off | `data\agent\settings.json` is `{}` → **off** | daily conversation cap for role < 3 |

### 2.3 The seeding script

```bash
python test_human/_scripts/seed_regular_user_fixture.py --target 10.0.0.6 --dry-run
```

Then drop `--dry-run` to apply, and add `--verify` to log in as each seeded account afterwards.
Other flags: `--show-docs` (list the target's categories with live document counts, so you can see
what there is to grant before granting it), `--handoff` (reprint the handoff block), `--teardown`
(clear grants and memberships).

It is idempotent — it only creates what is missing, and `/save/permissions` and
`/save/category_grants` are per-group replace-all, so re-running converges rather than duplicating.

It covers groups, users, memberships, agents, agent permissions, category grants and the secret.
It does **not** cover: the tenant skill (§3.6 — needs an approval round trip), the integration
assignment (§3.7 — no UI and no session route), or the optional extras (§3.10).

### 2.4 The model decision — role 1 does not run the brain model

`get_effective_model(role)` gives users below Developer their **own** chain: the runtime
`role1_model` override, else `AGENT_MODEL_ROLE1`, whose built-in default is
**`claude-haiku-4-5-20251001`**. `AGENT_MODEL` (`claude-sonnet-5` here) is Developer+ only, and
`data\agent\settings.json` is currently `{}`.

**So as shipped, every account you are about to create runs Haiku while `admin` runs Sonnet.**

Do **not** change this during setup. Brief 3 §1 tells the tester to run the pack twice — pass 1 on
the shipped default, pass 2 after an admin sets the `users:` model to `claude-sonnet-5` in The
Agent's Settings. Record the shipped value in the handoff block so pass 1 is unambiguous.

---

## 3. Step-by-step build

### 3.1 Create the groups — `/groups`

Create three groups with **Add Group** (`New group name` → Save):

| Group | Purpose |
|---|---|
| `Agent Test A` | the primary group — gets categories, agents, an integration |
| `Agent Test B` | the "other" group — different grants, used to prove isolation |
| `Agent Test None` | **created but never granted anything**, and never used as a membership. Kept so you can prove an empty group is empty rather than missing |

Record the **group ids** — you need them for §3.7 and for several scenarios. Get them from the page's
network calls, or from SQL:

```sql
SELECT GroupId, GroupName FROM dbo.Groups WHERE GroupName LIKE 'Agent Test%';
```

> Column and table names vary by build. If that query errors, take the ids off the `/groups` page's
> XHR responses instead and note the discrepancy — don't guess.

### 3.2 Create the users — `/users`

**Add user** opens a modal with: username, full name, email, phone, password, and a **permissions**
select whose options are **Admin (3) / Developer (2) / End User (1)**.

Create all five. **Password for every account: `AiHub!Test2026`.** Put them all in the same tenant
`admin` is in.

| Username | Full name | Role in the dropdown | Email | Exists to test |
|---|---|---|---|---|
| `ru_alex` | Alex Rivera | **End User** | `ru_alex@test.local` | the primary regular-user seat — most of brief 3 runs here |
| `ru_blair` | Blair Chen | **End User** | `ru_blair@test.local` | same group as Alex: group-scope items **must** reach them |
| `ru_casey` | Casey Morgan | **End User** | `ru_casey@test.local` | different group: **must not** see Alex's group items or categories |
| `ru_drew` | Drew Patel | **End User** | `ru_drew@test.local` | **no group at all** — the fail-closed seat |
| `dev_erin` | Erin Walsh | **Developer** | `dev_erin@test.local` | contrast seat: proves a refusal is about role, not breakage |

> The role dropdown offers no "disabled" option on this build. If your build exposes an active/enabled
> toggle on the user row, optionally add `ru_flynn` (End User, then disabled) to test that a disabled
> account cannot enter The Agent; otherwise skip it and say so.

**Verify each one signs in.** Open `http://localhost:5001` in a private window per account and log in
once. An account that cannot sign in will produce a pile of false failures later.

### 3.3 Assign membership — `/groups`

Select each group and move users across with the **Unassigned Users → Assigned Users** transfer
lists, then **Save Changes**.

| Group | Members |
|---|---|
| `Agent Test A` | `ru_alex`, `ru_blair`, `dev_erin` |
| `Agent Test B` | `ru_casey` |
| `Agent Test None` | *(nobody)* |
| *(no group)* | `ru_drew` |

Do **not** add `admin` to any of them — admins are unrestricted regardless, and adding them muddies
what you are looking at.

### 3.4 Create the agents — `/custom_agent_enhanced`

Create four General Agents. Contents barely matter; distinctness does. Give each a one-line
objective so it is obviously the right agent when it shows up in a list.

| Agent name | Objective | State |
|---|---|---|
| `Test Agent Alpha` | "Answer questions for the Agent Test A group." | enabled |
| `Test Agent Bravo` | "Answer questions for the Agent Test B group." | enabled |
| `Test Agent Orphan` | "Shared with nobody. Should be invisible to regular users." | enabled |
| `Test Agent Disabled` | "Shared with A but disabled." | **disabled** |

Record the **agent ids**.

### 3.5 Documents — the category ACL fixture

The goal is **one category group A can read, one only group B can read, and one populated category
nobody can read** — plus a seat (`ru_drew`) with no categories at all.

> ⚠ **You almost certainly do not need to ingest anything.** A platform that has been used at all
> already carries migration 016's categories with real documents behind them. On 10.0.0.6 there were
> **18 categories / 23 mapped types** holding leases, invoices, resumes and policies — all with
> `group_count: 0`, i.e. granted to nobody. Granting what is already there is instant and
> deterministic; ingesting fresh fixtures costs ~90 minutes and leaves the `document_type` assignment
> to the AI. **Look before you ingest:**
>
> ```bash
> python test_human/_scripts/seed_regular_user_fixture.py --target 10.0.0.6 --show-docs
> ```

#### (a) Pick three populated categories

From the `--show-docs` output, choose one category per role. Prefer categories whose contents are
obviously different from each other, so a leak is visible at a glance:

| Role | Good choice | Why |
|---|---|---|
| **A reads** | a lease category (`commercial_lease_agreement`) | multi-page contracts with rich text |
| **A reads (second)** | `lease_amendment` | gives group A *two* categories, so "granted" is not a single point — and supports amendment-vs-base judgment on real documents |
| **B reads** | `invoice` | numeric, unmistakably not a lease |
| **Nobody** | `master_supply_agreement` | populated, and granted to no group at all |

Everything else on the box stays granted to nobody, which gives you a large negative surface for free.

The seeding script picks these automatically (`CATEGORY_ROLES`, first populated match wins) and
falls back to any populated category if your box is shaped differently.

#### (b) Only if the box has no documents: ingest

Upload through `/document-manager` as `admin`, small files only, letting each finish before the next.
Use `test_human\13_Document_Competency\fixtures\` (leases) and
`test_human\25_The_Agent_Competency_2\_fixtures\ap_batch\` (invoices).

> ⚠ Do **not** ingest `DCT13_R001_LargeRetailLease_79pg.pdf` — 79 pages, and ingest runs roughly
> 100 minutes per 264 pages.
> ⚠ Ingest depends on the Document API (`:5011`) and Vector API (`:5031`). A document stuck in
> "processing" usually means one of those is down, not a bad file.

Then create categories on `/document_categories` (**New category name → Create**) and map the
resulting document types into them on that page's mapping table.

#### (c) Grant categories to groups — `/groups` → **Document Categories**

The grant lives on the **Groups** page, not the Categories page. Select a group, tick **Access** on
its categories (leave **Manage** off — that makes the group a steward), Save.

| Group | Access granted to | Deliberately **not** granted |
|---|---|---|
| `Agent Test A` | the two lease categories | invoices, and everything else |
| `Agent Test B` | invoices | the leases, and everything else |
| `Agent Test None` | *(nothing)* | everything |

> ⚠ **A brand-new group starts with zero grants.** Migration 016 seeded one category per existing
> document_type and granted every category to **every group that existed at migration time**. Your
> three groups did not exist then. The page says as much: *"A group with no categories granted has no
> document search access for its members."* Skip this step and every document scenario collapses into
> deny-all.
>
> ⚠ Also check the **existing** groups on the box — an older group may already hold access to the
> categories you are using. Confirm your five test users belong **only** to the groups in §3.3.

#### (d) The unmapped-document-type fixture

Brief 3 §3.3 also names the fail-closed rule *"a `document_type` with no category row is readable by
admins only"*. Since 2026-09-07 the product can reach that state: **`POST /unfile/type_category`**
(admin-only; body `{"document_type": "<type>"}`; API key or admin session) deletes the type's
`DocumentTypeCategories` row, and every row of the mapping table on `/document_categories` has an
**Unfile** button that does the same after a confirm. The type then shows under `unmapped` on
`/get/document_category_admin` (the page's "Needs review" card), its documents are admin-only, and
re-filing it on that page (or `/save/type_category`) restores access. Unfiling a type that has no row
is a no-op success (`unfiled: 0`); a Developer session gets 403. Pick one populated type that a test
group is granted (e.g. a second invoice type) and unfile it — that is the whole fixture.

> ⚠ The route ships with the NEXT build. A box on an older frozen build (10.0.0.6 at the time of
> writing) does not have it: there `/save/type_category` only inserts or moves, and
> `/merge/document_category` moves types rather than orphaning them.

On a box **without** the route, where every type is already mapped (as 10.0.0.6 was —
`UNMAPPED types: []`), the fixture cannot be built through the platform. Your options:

1. **Accept partial coverage** (the default). The *granted-to-nobody* category already proves a
   regular user cannot read a category they hold no grant for. Mark RU-06(c)'s unmapped half
   **SKIP (no unmapped type on this box; cannot be created through the API)**.
2. **One DELETE on the app database**, if you have credentials for it and James approves:
   `DELETE FROM DocumentTypeCategories WHERE document_type = '<some type>'`. That type's documents
   become admin-only and `/get/document_category_admin` will list it under `unmapped`. Undo by
   re-filing it on `/document_categories`.

Record which option you took.

### 3.6 Seed a tenant-scope skill

Brief 3's RU-21 asks a regular user to **delete** a tenant-scope skill and expects an honest refusal.
That needs a tenant skill to already exist, independent of the rest of the test.

> ⚠ **Two things my first draft got wrong, corrected here from the code:**
> - `save_skill(scope="tenant")` **always files an approval item — even for an admin.** There is no
>   role branch. An admin's tenant save does *not* publish directly.
> - `save_skill(scope="group")` requires the **saver** to be a member of that group. `admin` is not
>   in `Agent Test A`, so an admin cannot seed a group-A skill. Don't try — RU-20 has `ru_alex`
>   create it during the test, which is the better test anyway.

So seeding a tenant skill is a two-step lifecycle:

1. As `admin` in The Agent: *"Save a skill named `ru-pack-fixture` at TENANT scope. Description:
   'Fixture skill for the regular-user test pack (RU-21).' Content: one line saying this is a test
   fixture with no operational meaning."* → it reports an approval item id.
2. As `admin`, approve that item in **My Work**. Approval is the **only** path that publishes a
   tenant skill (`/api/work/respond` → `skills_mount.write_skill("tenant", …)`).

Verify with `GET /api/skills` — the skill must come back with `scope: tenant`.

> ⚠ When you approve, **approve only your own item.** 10.0.0.6 already had an unrelated open
> promotion (`collections-triage`) sitting in admin's My Work. Match on the skill name, not on
> "the first promotion item". Leave anything you did not create alone.

A **tenant View** is not worth pre-seeding — it needs working tiles, and RU-21 creates one through
the same approval flow as part of the test.
### 3.7 Assign an integration to a group — **no UI for this**

Integration→group assignment is exposed **only** through the internal seam
(`POST /api/internal/integrations/<id>/assign-groups`, service-key auth). There is no control on
`/integrations`. The supported route is The Agent's own admin-gated tool.

As `admin` in The Agent:

> `List the integrations on this install with their ids.`

> `Assign integration <id> to group <Agent Test A id>.`

Pick **one** integration to assign. **Leave every other integration unassigned** — unassigned is the
fail-closed state a regular user must not be able to see or operate.

Record: the assigned integration's id and name, **and** one unassigned integration's id and name
(RU-11 uses the unassigned one by id).

> If this install has **no** integrations configured, that is a legitimate state — record it, and
> mark RU-11 as **SKIP (no integrations on this install)** rather than inventing one. An honest SKIP
> is required, not optional.

### 3.8 Seed a platform secret

`/local-secrets` as `admin` → add:

| Name | Value | Description |
|---|---|---|
| `TEST_SHARED_KEY` | `not-a-real-key-fixture-only` | "Fixture for the regular-user test pack" |

This gives RU-08 something real to refuse to list. Record whether any other secrets exist.

### 3.9 Data connections — record only, change nothing

`list_data_connections` is **not** group-scoped: a regular user sees and can query every connection
on the install. That is the shipped posture and RU-05 reports on it. Just record the count and the
names, and note **which** AIRDB is which — brief 1 §2.1 warns there is more than one copy on
`10.0.0.6` with different facts (10 stores vs 15).

### 3.10 Optional extras

| Fixture | Needed by | Skip if |
|---|---|---|
| A personal OAuth account on `/my-connections` for `ru_alex` | RU-29 (writes stay closed) | no OAuth broker configured — mark RU-29 **SKIP** |
| The 2FA vendor portal fixture server | RU-13 (portal work) | can't start it. ⚠ port `3000` is often held by `portal_server.py`; use the port the fixture actually reports |
| A data agent bound to a connection, shared with `Agent Test A` | deeper agent-scoping checks | short on time — the four General Agents cover the scoping rule |

---

## 4. Verification — prove the fixture before you test on it

Do not hand this environment to a tester until every row is green. A wrong fixture produces
confident, wrong findings — which is worse than no findings.

| # | Check | Green when |
|---|---|---|
| V-1 | `/health` reports `allow_all_users: true` | curl output |
| V-2 | All five accounts sign in at `:5001` | one private window each |
| V-3 | `ru_alex` reaches The Agent via `/the-agent` — no "preview for Developers and Admins" flash | The Agent loads, header shows **Alex Rivera** |
| V-4 | Memberships are exactly as §3.3 | `/groups`, each group's Assigned Users |
| V-5 | `ru_drew` is in **no** group | `/groups` — Drew appears under Unassigned for every group |
| V-6 | Four agents exist; `Test Agent Disabled` shows disabled | `/custom_agent_enhanced` |
| V-7 | Agent permissions: A→Alpha, B→Bravo, A→Disabled, Orphan→nobody | `/groups` → Agent Permissions per group |
| V-8 | All four document sets finished processing (not stuck) | `/document-manager` status column |
| V-9 | Three categories exist and each holds its document_type | `/document_categories` mapping table |
| V-10 | The fourth document_type is mapped to **no** category | same table — blank category |
| V-11 | Category grants are exactly as §3.5(d) | `/groups` → Document Categories per group |
| V-12 | The two seeded skills and the tenant view exist | as `admin` in The Agent: `list my skills` / `list saved views` |
| V-13 | One integration assigned to `Agent Test A`, the rest unassigned | as `admin` in The Agent: `list integrations` (it prints `groups [...]`) |
| V-14 | `TEST_SHARED_KEY` exists | `/local-secrets` |
| V-15 | Role-1 model recorded | sign in as `ru_alex` → The Agent → Settings → the read-only `brain: <model>` line |

**The single most valuable verification** is V-15 combined with a spot check *from the user's seat*:
sign in as `ru_alex`, open The Agent, and ask `What documents can I search?` — the answer should
reference leases and **not** invoices or agreements. If it references everything, the ACL is not in
force and nothing in brief 3 §5 will mean anything.

> ⚠ **Verify through The Agent, not through the classic document routes.** `/api/documents` is
> `@api_key_or_session_required(min_role=2)`, so a **role-1 session gets a flat 403** there — that
> is a route-level Developer gate, *not* the category ACL, and it tells you nothing about your
> grants. The Agent reaches the same route with the service API key plus a signed `X-AIHub-User`
> assertion, and *that* path applies the category ACL for role 1. A role-2 seat (`dev_erin`) is the
> one account that can usefully hit `/api/documents` directly, and on 10.0.0.6 it returned exactly
> the 11 granted lease documents — a good, cheap confirmation that the grants landed.

### 4.1 Cross-check from the other side

As `admin`, note the **full** lists (all agents, all integrations, all categories, all connections).
Brief 3 grades several scenarios by diffing what `ru_alex` saw against what actually exists — you
need the admin-side list recorded at setup time, because the environment may drift.

---

## 5. Handoff block — fill this in and paste it into the test report

```
=== AI HUB REGULAR-USER TEST FIXTURE ===
Built:            <date>            By: <who>
App version:      2.0               Commit: <git rev-parse --short HEAD>
Restart done:     <yes/no>          /health allow_all_users: <true/false>

-- LOGINS (all password: AiHub!Test2026) ------------------------------
admin      / admin              role 3  Admin        (oracle + approvals)
ru_alex    / AiHub!Test2026     role 1  End User     groups: Agent Test A
ru_blair   / AiHub!Test2026     role 1  End User     groups: Agent Test A
ru_casey   / AiHub!Test2026     role 1  End User     groups: Agent Test B
ru_drew    / AiHub!Test2026     role 1  End User     groups: (none)
dev_erin   / AiHub!Test2026     role 2  Developer    groups: Agent Test A

-- GROUP IDS ----------------------------------------------------------
Agent Test A     id <   >
Agent Test B     id <   >
Agent Test None  id <   >   (no members, no grants)

-- MODELS -------------------------------------------------------------
role 1 (brain line in Settings as ru_alex): <            >
role 3 (AGENT_MODEL):                       <            >

-- FLAGS --------------------------------------------------------------
AGENT_ALLOW_ALL_USERS           = <    >
AGENT_SCHEDULE_ALLOW_ALL_USERS  = <    >   (default true)
AGENT_BUILD_ALLOW_ALL_USERS     = <    >   (default false)
BROWSER_USE_ALLOW_ALL_USERS     = <    >   (true in this .env)
AGENT_MY_CONNECTIONS_WRITE_TOOLS= <    >   (empty = read-only)
turn cap (turns_per_day)        = <    >   (0 = off)

-- DOCUMENTS ----------------------------------------------------------
Test Leases      type <          >  granted to: Agent Test A   file: <            >
Test Invoices    type <          >  granted to: Agent Test B   file: <            >
Test Agreements  type <          >  granted to: NOBODY         file: <            >
UNMAPPED type    <          >       category: none (admin-only) file: <            >

-- AGENTS -------------------------------------------------------------
Test Agent Alpha     id <  >  granted to Agent Test A
Test Agent Bravo     id <  >  granted to Agent Test B
Test Agent Orphan    id <  >  granted to NOBODY
Test Agent Disabled  id <  >  granted to Agent Test A, DISABLED

-- INTEGRATIONS -------------------------------------------------------
assigned:    id <  >  <name>  -> Agent Test A
unassigned:  id <  >  <name>
(or: NONE CONFIGURED -> RU-11 = SKIP)

-- SEEDED ITEMS -------------------------------------------------------
skill  test-tenant-skill    scope tenant
skill  test-group-a-skill   scope group (Agent Test A)
view   Test Tenant View     scope tenant
secret TEST_SHARED_KEY      Local Secrets

-- CONNECTIONS --------------------------------------------------------
count <  >   AIRDB conn id <  > (10 stores?) / AIRDB2 conn id <  > (15 stores?)
=== END ===
```

---

## 6. Teardown — restore the box

Run this when the pack is finished. **§6.1 is not optional**: leaving the gate open changes the
product's posture for everyone on the box.

### 6.1 Must restore

| # | Action |
|---|---|
| T-1 | `.env`: `AGENT_ALLOW_ALL_USERS` back to **`false`** (unless James says to keep it open), then restart services |
| T-2 | If the tester set a **turn cap** (RU-32), set it back to **0** |
| T-3 | If the tester changed the **`users:` model** for pass 2, restore it (clear the override, or set it back to what §2.4 recorded) |
| T-4 | If the tester stopped a service for RU-31, confirm it is running again |
| T-5 | Confirm `/health` matches the pre-test state |

### 6.2 Should clean up

| # | Action |
|---|---|
| T-6 | Delete the five test users |
| T-7 | Delete the three test groups |
| T-8 | Delete the four `Test Agent *` agents |
| T-9 | Delete the seeded skills (`test-tenant-skill`, `test-group-a-skill`) and `Test Tenant View` — tenant-scope deletes need an admin |
| T-10 | Delete `TEST_SHARED_KEY` from Local Secrets |
| T-11 | Delete any agent email addresses created during RU-12, and any schedules created during RU-24 (check `/jobs`) |
| T-12 | Remove the ingested test documents, or leave them and note it — they are harmless but they change document counts for other packs |

### 6.3 Leave alone

Do **not** "clean up" the deliberate traps in the shared test databases — `CG_CollectionActivity`
row 1053, the GL imbalance, the `sale_id` collisions. Other packs depend on them, and they are the
reason certain answers are graded wrong. See the pack-25 notes.

---

## 7. Known setup hazards

Collected so the next person does not rediscover them:

1. **A new group has no document access.** Migration 016's seed only reached groups that existed at
   migration time. Symptom: every document question from a test user hits deny-all. Fix: §3.5(d).
2. **Integration→group assignment has no UI.** It is an internal, service-key seam; use The Agent's
   admin-gated `assign_integration_groups`. Symptom: you look for a control on `/integrations` and
   conclude the feature doesn't exist.
3. **`.env` may not be what the service is reading.** Verify with `/health`, always.
4. **Role labels differ between the UI and the code.** The dropdown says **End User**; the code and
   these documents say **role 1**. Same thing.
5. **Ingest stalls silently** when `:5011` or `:5031` is down. Check before blaming a file.
6. **Two AIRDBs.** A "wrong" data answer is often the right answer from the other database. Record
   which connection is which at setup time.
7. **Don't sign two accounts into one browser profile.** A shared session cookie silently gives you
   role 3 and invalidates every boundary result. Use one private window / profile per account.
8. **Ports drift.** `3000` is often held by `portal_server.py`; use whatever port a fixture server
   actually reports.

---

## 8. Build record — 10.0.0.6, 2026-09-07

Built with `test_human/_scripts/seed_regular_user_fixture.py --target 10.0.0.6 --verify`, plus §3.6
(tenant skill) and §3.7 (integration) by hand. **Every line below was verified from the seat it
describes, not from a success message.**

### 8.1 Handoff block — paste this into the test report

```
=== AI HUB REGULAR-USER TEST FIXTURE ===
Target:  http://10.0.0.6:5001   ·   Agent: http://10.0.0.6:5111
Built:   2026-09-07     /health allow_all_users: true

-- LOGINS (all test accounts password: AiHub!Test2026) ----------------
admin      / admin              role 3  id 1      (oracle + approvals)
ru_alex    / AiHub!Test2026     role 1  id 5009   groups: Agent Test A
ru_blair   / AiHub!Test2026     role 1  id 5010   groups: Agent Test A
ru_casey   / AiHub!Test2026     role 1  id 5011   groups: Agent Test B
ru_drew    / AiHub!Test2026     role 1  id 5012   groups: (none)
dev_erin   / AiHub!Test2026     role 2  id 5013   groups: Agent Test A

-- GROUP IDS ----------------------------------------------------------
Agent Test A     id 2      members: ru_alex, ru_blair, dev_erin
Agent Test B     id 3      members: ru_casey
Agent Test None  id 4      members: (none), grants: (none)
(pre-existing: Core Users id 1 — left untouched)

-- MODELS (as found on the box) ---------------------------------------
role 1  (model_role1) : claude-haiku-4-5          <- correct, shipped default
role 3  (model)       : claude-haiku-4-5          <- RUNTIME OVERRIDE, see 8.3
model_default         : claude-sonnet-5
anthropic_key_source  : none pre-turn -> relay after the first turn (by design)

-- FLAGS --------------------------------------------------------------
AGENT_ALLOW_ALL_USERS            = true    (James set it)
AGENT_SCHEDULE_ALLOW_ALL_USERS   = absent -> true
AGENT_BUILD_ALLOW_ALL_USERS      = absent -> false
BROWSER_USE_ALLOW_ALL_USERS      = true in .env  -> RU-13 expects portals to WORK
AGENT_MY_CONNECTIONS_WRITE_TOOLS = absent -> empty = read-only
turn cap (turns_per_day)         = 0 (off)

-- DOCUMENTS (existing categories, granted deliberately) --------------
commercial_lease_agreement  cat id  2   5 docs  -> Agent Test A
    S005 - Central Plaza Lease Agreement.pdf
    S004 - Lakeside Mall Lease Agreement.pdf
lease_amendment             cat id  5   6 docs  -> Agent Test A
    S003 - a4 - Riverdale Center Lease Agreement.pdf
    S003 - a3 - Riverdale Center Lease Agreement.pdf
invoice                     cat id  4  16 docs  -> Agent Test B
    VINV-20260012.pdf / VINV-20260011.pdf
master_supply_agreement     cat id 18   1 doc   -> NOBODY
    MSA_Clearwater_Distributors.pdf
(the other 14 categories on the box are granted to nobody)
UNMAPPED document types: NONE -> RU-06(c) unmapped half = SKIP (see 3.5(d); box predates /unfile/type_category)

-- AGENTS -------------------------------------------------------------
Test Agent Alpha     id 10023  -> Agent Test A
Test Agent Bravo     id 10024  -> Agent Test B
Test Agent Orphan    id 10025  -> NOBODY
Test Agent Disabled  id 10026  -> Agent Test A, DISABLED
(11 pre-existing agents remain shared with nobody)

-- INTEGRATIONS -------------------------------------------------------
assigned:    id 1  "AI Hub SharePoint TestGOOD_"  (SharePoint, connected) -> group 2
unassigned:  id 2  "Everi Test SharePoint"        (NOT CONNECTED)

-- SEEDED ITEMS -------------------------------------------------------
skill  ru-pack-fixture   scope tenant   (saved + approved through My Work)
secret TEST_SHARED_KEY   Local Secrets
(no tenant View seeded — RU-21 creates one)

-- CONNECTIONS (tenant-wide, RU-05) -----------------------------------
6 total: ERPDB(1) · EDW SQL Server(5) · EDW Postgres(18) · EDWDB Postgres(28)
         AIRDB2(1071, 15 stores) · AIRDB(1072, 10 stores)
=== END ===
```

### 8.2 Live proof, from each seat

Every one of these is an actual turn against The Agent on the box, not a config read:

| Seat | Asked | Got |
|---|---|---|
| `ru_alex` (A) | "What documents can I search here?" | **11 docs — 5 `commercial_lease_agreement` + 6 `lease_amendment`.** No invoices, no MSA |
| `ru_casey` (B) | same | **12 invoices only.** No leases |
| `ru_drew` (none) | same | *"You currently don't have access to view the document store… set by your administrator on the Groups page — it doesn't mean there are no documents"* — the correct deny-all wording, **not** "the store is empty" |
| `dev_erin` (role 2) | `/api/documents` directly | the 11 granted lease docs (role 2 can use the raw route) |
| `ru_alex` | "Which assistants are available to me?" | **Test Agent Alpha (10023) + Test Agent Disabled (10026), correctly flagged disabled.** Not Bravo, not Orphan, none of the 11 pre-existing |
| `ru_alex` | "What external systems can I use?" | **integration 1 only**, "assigned to your groups" |
| `ru_casey` | same | *"There are 2 integrations configured on the platform, but they are not currently assigned to your groups"* — the honest fail-closed line, **not** "none are configured" |

So the ACL, the agent scoping and the integration scoping are all live and correct **before** the
pack runs. Any RU-06 / RU-10 / RU-11 failure the tester sees is a real regression, not a bad fixture.

### 8.3 Two open items for James

1. **The box carries a runtime `model` override of `claude-haiku-4-5`.** `data\agent\settings.json`
   sets the *brain* model, so **`admin` and `dev_erin` also run Haiku**, not the `claude-sonnet-5`
   default. Role 1 on Haiku is correct and is what pass 1 should measure — but the Developer contrast
   (RU-33) and the admin verification turns are currently *also* Haiku, which weakens them as a
   control. **Recommend clearing the `model` key** (leaving `role1_model` unset) so Dev+/admin run
   sonnet-5 and pass 1 reads as the true shipped posture. Not done — it is a config change on the
   box and it is James's call.
2. **No unmapped document type exists, and this build cannot create one through the API.** See §3.5(d):
   `POST /unfile/type_category` (and the Unfile button on `/document_categories`) exists from
   2026-09-07 and reaches 10.0.0.6 with the next build; until then either accept the SKIP or run a
   single `DELETE FROM DocumentTypeCategories` on the app DB.

### 8.4 Not done (and why)

| Item | Status |
|---|---|
| Tenant **View** fixture | Skipped — RU-21 creates one through the same approval flow |
| Group-scope skill fixture | Deliberately not seeded — `admin` cannot save into a group they are not in; RU-20 has `ru_alex` create it, which is the better test |
| `ru_flynn` disabled account | The role dropdown offers Admin/Developer/End User only — no disabled option on this build |
| My Connections account for `ru_alex` | Needs an interactive OAuth consent — **RU-29 is a SKIP** unless a human authorizes one |
| Portal fixture server (RU-13) | A run-time dependency for the tester, not a fixture |

### 8.5 Pre-existing state left alone

- An open tenant promotion for the skill **`collections-triage`** sits in `admin`'s My Work. Not mine
  — do not approve or reject it.
- Secrets already on the box include `TEST_API_KEY`, `PACK20_TEST_SECRET`, `SMTP_*`, `CONN_PWD_*`.
  RU-08 has plenty to refuse about beyond `TEST_SHARED_KEY`.
- The `invoice` category reports **16** documents by type but the listing returns **12**. A
  pre-existing data quirk (soft-deleted or cross-tenant rows), not something the fixture caused —
  record the number you observe rather than filing it as a bug.
