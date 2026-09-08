# Restricting document access for The Agent across all users — research

**Date:** 2026-09-03 · **Status:** research only, no code changes · **Scope:** how document-type
access control works for The Agent (`agent_service`, :5111) when every user — not just devs
and admins — is allowed in.

---

## TL;DR

Yes, this works today with no code changes, but **not** through the mechanism you remember.
There are **two different document-type ACLs** in the platform and only one of them applies
to The Agent:

| | Per-**agent** allow list | Per-**user** category ACL |
|---|---|---|
| Table(s) | `AgentDocumentTypes` (migration 008) | `DocumentCategories` / `DocumentTypeCategories` / `DocumentCategoryGroups` (migration 016) |
| Granted to | one Agents row | a **Group**, which users belong to |
| Admin UI | Agent Builder → Document Type Restrictions | Groups page (grants) + `/document_categories` (categories) |
| Governs The Agent? | **No** — The Agent is not an `Agents` row | **Yes** |
| Governs Command Center? | no | yes (`nodes.py` sends the same identity header) |
| Governs classic General Agents? | yes | no |

The old "build an agent in Agent Builder and grant it document types" model is
**per-agent**. The Agent is a single shared service, so that model does not apply to it.
What applies is the **v3 category ACL**, which is per-user via group membership — which is
the right shape for an all-users rollout anyway.

**Why nothing looked restricted in your testing:** `doc_search_v3/acl.py:45` short-circuits
to unrestricted for **role ≥ 3 (admin)** without consulting the tables at all. Every admin
account sees everything by design. Role 2 (Developer) *is* restricted, so a dev account is a
valid test subject — role 3 is not.

---

## 1. How it works end to end

```
browser login
  └─ CC JWT (aud="command-center") → agent_service/main.py:88 _verify_request()
       → {user_id, role, tenant_id, username}
  └─ brain.py:684  CURRENT_USER.set(user_ctx)      ← contextvar, per turn
       └─ document_tools.py:146 _headers()
            mints X-AIHub-User = HS256 assertion (aud="aihub-internal", 5 min)
            ONLY when user_id not in (None, "", 0)
  └─ POST /api/internal/document-search-unified          (app.py:6241)
       ├─ assertion PRESENT + valid  → uid, role
       ├─ assertion PRESENT + forged → hard 403 (never treated as "missing")
       └─ assertion ABSENT           → uid=None → unrestricted (legacy posture)
  └─ document_search_wrapper.document_search_unified(user_id, user_role)
       └─ doc_search_v3.acl.accessible_document_types(uid, role)
            role >= 3            → None   = UNRESTRICTED (tables not consulted)
            uid falsy            → None   unless DOC_V3_REQUIRE_IDENTITY=true
            otherwise            → SELECT DISTINCT tc.document_type
                                     FROM DocumentTypeCategories tc
                                     JOIN DocumentCategoryGroups cg ON cg.category_id = tc.category_id
                                     JOIN UserGroups             ug ON ug.group_id    = cg.group_id
                                    WHERE ug.user_id = ? AND tc.status = 'active'
            any DB/resolve error → []     = DENY ALL (fails closed)
       └─ acl.deny_all(allowed) is checked BEFORE the legacy engine
```

### The fail-open trap this design exists to guard

`DocUtils._build_doc_type_filter` (DocUtils.py:626) treats an **empty** allow list as
"no filter" — `if allowed_document_types:` is falsy for `[]` — so handing `[]` straight to
the legacy engine would grant **everything**. Callers must therefore call `acl.deny_all()`
and stop first. `tests/unit/test_v3_acl.py` locks this with the first two tests in the file;
if those two ever fail together, a user with zero grants sees every document.

### Category layer, not raw types

`document_type` is free text assigned by an LLM at ingest, so it drifts —
`lease_agreement` / `commercial_lease_agreement` / `retail_lease_agreement` are one business
concept spelled three ways. Grants are on a **category**, so "Leases" is granted once and
every present and future spelling that maps into it inherits it. The safety property:
**a document_type with no category row is granted to nobody**, so a hallucinated new type is
invisible to non-admins rather than silently visible to everyone. The classifier can never
widen access, only fail to widen it.

---

## 2. Admin surfaces (already built, already in the nav)

| Screen | Route | What it does |
|---|---|---|
| **Groups** → Document Categories panel | `/get/category_grants/<gid>`, `/save/category_grants` | Per-group Access + Manage checkboxes over every category. Save is DELETE+INSERT; an empty list is a legitimate "this group sees no documents". |
| **Document Categories** | `/document_categories` (app.py:4331) | Every type→category mapping with live doc counts, **pending** AI filings awaiting review, **unmapped stray types** (admin-only until filed), create category, merge categories. |

Both are `min_role=3` (admin). `can_manage=1` additionally makes the group the category's
**steward** — its members get the My Work items for new type assignments and may recategorise.

---

## 3. Live state on this box (read-only verification, 2026-09-03)

Tables all present. `DocumentCategories` = **95**, `DocumentTypeCategories` = **124**,
`DocumentCategoryGroups` = **1602**. 18 groups. Documents: 397 general + 1,579 knowledge.
40 distinct general document types currently in the store.

Running the **real resolver** for every user:

```
  uid  user             role  resolver result
    1  jsmith              1  118 type(s) allowed
    9  mjones              1  118 type(s) allowed
   10  tbrady              1  []    -> DENY ALL (no group grants)
   11  bpitt               1  118 type(s) allowed
   54  cservice            1  []    -> DENY ALL (no group grants)
  125  test                1  118 type(s) allowed
  249  user                1  118 type(s) allowed
  285  dreyes              1  118 type(s) allowed
    8  jmiller             2  118 type(s) allowed
  141  developer           2  118 type(s) allowed
   12  james               3  None  -> UNRESTRICTED (admin)
   13  admin               3  None  -> UNRESTRICTED (admin)
   24  jamie               3  None  -> UNRESTRICTED (admin)
  142  ad2                 3  None  -> UNRESTRICTED (admin)
```

Notable:

* **2 role-1 users already resolve to DENY ALL** (`tbrady`, `cservice`) because they are in no
  group. Migration 016 grants categories to *groups*, so a user in no group gets nothing.
  This is correct fail-closed behaviour, but it will read as "document search is broken" the
  moment those accounts start using The Agent.
* Mapping provenance: 89 `migration`, 34 `ai`, 1 `human`. All `active` — no pending backlog.
* **6 categories have no group grant** → admin-only: `account_statement`,
  `litigation_hold_notice`, `purchase_order`, `support_ticket_log`,
  `tenant_estoppel_certificate`, `warranty_certificate`. These are newer AI-filed types that
  post-date the migration seed, so they never got the blanket grant.
* **1 unmapped type** → admin-only: `internal_audit_report` (1 doc).
* Only one agent uses the *old* per-agent restriction at all: agent 1007 "Doc Agent 1001",
  1 type.

---

## 4. Gaps

Ordered by how much they matter for an all-users rollout.

### G1 — `query_document_records` bypasses the ACL entirely  ⚠ highest impact

`/api/internal/document-records` (app.py:6301) never reads `X-AIHub-User` and never passes
`allowed_document_types`, even though `document_records_query.query_document_records()`
already accepts that parameter (document_records_query.py:134) and threads it through both
list and query modes. The Agent's `query_document_records` tool therefore returns extracted
record rows from **every** document type.

This matters more than it sounds: record rows *are* document contents — a compliance guide's
requirements, an invoice's line items — and the tool description actively pushes the model
toward it for any "which / how many / list every" question. So the ACL holds for
`search_documents` and is silently absent one tool over. The fix is symmetric with the
search route and small.

### G2 — `list_documents` / `get_document` are unscoped

`/api/documents` GET (app.py:14516) is `@api_key_or_session_required(min_role=2)` with no
category filter. The Agent authenticates with the tenant API key, so the role check never
even reaches the calling user. A role-1 user can enumerate every filename, document type,
reference number and processed date in the store — including types they cannot search.

Contents don't leak, but filenames usually are the sensitive part
("Project Falcon — termination terms.pdf").

### G3 — `ask_agent` launders both ACLs

`/api/agents/<int:agent_id>/chat` (app.py:2655) is `@api_key_or_session_required()` — no
`min_role`, and no `_agent_visibility_filter()` (app.py:2557), which the *listing* endpoints
do apply. `platform_tools._headers()` (platform_tools.py:34) sends only `X-API-Key`, no user
assertion.

Net effect: a role-1 user can ask **any agent id**, including agents not shared with their
groups, and that agent answers using **its own** per-agent document allow list and **its own**
knowledge documents. That is agent-visibility ACL and document-category ACL bypassed in one
call. `list_agents` is visibility-filtered, so this needs a guessed/known id — but ids are
small integers and the agent will happily try them.

### G4 — Knowledge documents are outside the category ACL by design

1,579 of 1,976 documents are `is_knowledge_document = 1`. Migration 016 explicitly excludes
them ("private agent knowledge … keeps its existing per-agent / per-user isolation"), and
`enumerate_engine` / `/api/documents` both filter to `is_knowledge_document = 0`. That is a
deliberate, defensible boundary — but the practical route *into* that corpus is G3.

### G5 — Assertion minting fails open

`document_tools._headers()` wraps `import shared_auth` + `sign_user_assertion` in a bare
`except: pass` with the comment "identity is an enhancement; a doc call must never fail over
it". If signing ever fails — PyJWT missing in the service env, `API_KEY` absent so
`get_jwt_secret()` returns None — the header is silently dropped and the endpoint treats the
call as identity-less, i.e. **unrestricted**.

On this dev tree that never fires (the same secret already verifies the login token). On an
installed box where the service's env is seeded differently, it is exactly the failure you
would not notice: search keeps working, just for everything. `DOC_V3_REQUIRE_IDENTITY=true`
closes it globally, but it also denies every legitimately identity-less internal caller
(scheduler, automations, email dispatcher), so it cannot simply be flipped on.

### G6 — Migration 016 is manual, and its absence is a silent outage

No migration runner ships with the installer; 004, 008 and 016 were each run by hand
(`run_cc_migration.py`, `run_agent_doc_types_migration.py` — there is no 016 equivalent).
On an install where 016 has not been run, `acl._connect()` / the SELECT raises, the resolver
logs and returns `[]`, and **every non-admin gets DENY ALL** on document search. Fail-closed
is the right default, but it presents as "documents stopped working for regular users" with
the reason only in the log.

The seed block also needs a resolvable `TenantId` (session context, else the first
`Documents.TenantId`), and it prints `SEED SKIPPED` rather than failing if it cannot find one
— worth checking the output rather than assuming.

### G7 — New document types become admin-only silently

A type with no `DocumentTypeCategories` row is invisible to non-admins. `category_assignment.py`
auto-files new types and posts My Work items to the category's stewards, but a low-confidence
filing lands `status='pending'`, and only `status='active'` is consulted. Either way the
regular user just gets fewer results, with no explanation in the answer. This box already
shows 1 unmapped type and 6 ungranted categories accumulated since the migration.

### G8 — SQL surfaces are a side door

`export_data` (SELECT-only gated, uncapped to `AGENT_EXPORT_MAX_ROWS`) and
`probe_connection_query` (server-side read-only gate, ~50 rows) run against **connections**.
If any tenant connection points at the AI Hub application database, a user can
`SELECT … FROM Documents` / `DocumentPages` directly and skip the whole ACL. Worth auditing
the connection list before opening the doors.

### G9 — Nothing tests the seam end to end

`tests/unit/test_v3_acl.py` covers the resolver well, including the fail-open trap. But
nothing in `tests_v2/` or `test_human/20_The_Agent/` asserts that a **role-1 user actually
receives restricted results through The Agent**. The pack-20 all-users section lists
"document search (ACL)" as an allowed capability but does not verify the restriction. Every
link in the chain above is currently unproven at the seam.

---

## 5. Suggested plan

### Before flipping all-users on

1. **Run migration 016 on every target install** and check the printed counts (it prints
   `SEED SKIPPED` rather than failing when it cannot resolve a TenantId). Without it, every
   non-admin gets deny-all.
2. **Sweep for users in no group.** They get zero documents. Two such role-1 accounts exist
   here already. Either add them to a group or accept the (silent) lockout knowingly.
3. **Do the category work.** This is the actual effort and the ACL is only as good as it.
   Merge the ~95 seeded 1:1 categories down to real business concepts (Leases, Invoices, HR,
   Compliance, …) on `/document_categories`. The merge tool unions grants and moves types,
   so it is safe to iterate.
4. **Set grants per group on the Groups page**, then remove the blanket grants migration 016
   seeded. Order matters — narrow after you have the categories you want, not before.
5. **Test as a role-1 user, and as a role-2 user.** Role 3 short-circuits to unrestricted and
   will never show you a restriction. Role 2 *is* restricted, so `developer` / `jmiller` are
   valid subjects without creating a new account.
6. **Clear the backlog**: file `internal_audit_report`, and decide whether the 6 ungranted
   categories should be admin-only or granted.

### Close before broad rollout — small, symmetric changes

* **G1** — pass identity + allow list through `/api/internal/document-records`. The parameter
  already exists; this is copying the ~15 lines of assertion handling from
  `internal_document_search_unified` and threading `allowed` in. Highest value per line.
* **G2** — filter `/api/documents` by the same allow list when an assertion is present, so
  `list_documents` stops enumerating everything.
* **G3** — apply `_agent_visibility_filter()` to `/api/agents/<id>/chat`, and have `ask_agent`
  forward the user assertion.

### Consider

* **G5** — rather than `DOC_V3_REQUIRE_IDENTITY` globally, make an assertion-minting failure
  *loud* in the agent service (log + refuse the document call) while leaving genuinely
  identity-less internal callers on today's posture. The blanket flag is too coarse.
* **G7** — surface unmapped types and ungranted categories as a banner or a small report on
  `/document_categories`, so drift is visible rather than a slow silent narrowing.
* **G9** — add a role-1 document-ACL row to pack 20: grant a fixture user one category, assert
  the search answer cites only that category's documents and honestly refuses the rest.

---

## 6. Reference — files and lines

| What | Where |
|---|---|
| Resolver (the contract) | `doc_search_v3/acl.py:36` `accessible_document_types` |
| Deny-all guard | `doc_search_v3/acl.py:78` `deny_all` |
| Fail-open trap it guards | `DocUtils.py:626` `_build_doc_type_filter` |
| Search facade | `document_search_wrapper.py:222` |
| COUNT-shape engine | `doc_search_v3/enumerate_engine.py:261` |
| Enforcing endpoint | `app.py:6241` `internal_document_search_unified` |
| **Non**-enforcing endpoints | `app.py:6301` records · `app.py:14516` `/api/documents` · `app.py:2655` agent chat |
| Assertion minting | `agent_service/document_tools.py:146` `_headers` |
| Identity in | `agent_service/main.py:88` `_verify_request` · `agent_service/brain.py:684` |
| Group grants UI | `app.py:4258` / `app.py:4292`, `templates/groups.html:701` |
| Category admin UI | `app.py:4331` `/document_categories`, `templates/document_categories.html` |
| Auto-categorisation | `doc_search_v3/category_assignment.py` |
| Schema | `migrations/016_document_categories_and_group_access.sql` |
| Old per-agent ACL | `migrations/008_agent_document_type_restrictions.sql`, `DataUtils.py:1185` |
| Tests | `tests/unit/test_v3_acl.py` (resolver only — no seam test) |
