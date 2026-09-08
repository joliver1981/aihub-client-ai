# Handoff — close the three document-ACL bypasses (G1/G2/G3) before The Agent goes all-users

**Status:** RESEARCH COMPLETE, NOTHING BUILT. No code was changed producing this document.
**Date:** 2026-09-03
**Repo:** `C:\src\aihub-client-ai-dev` (branch `main` — do **NOT** create a branch; commit to `main`)
**Owner:** James
**Background:** [`the-agent-document-access-all-users.md`](the-agent-document-access-all-users.md) — read §1 and §4 of that first; this document only covers the fixes.
**Scope for the executing agent:** G1, then G2, then G3, in that order. Each is independently shippable. **Read §2 before writing a single line — the obvious implementation of all three is fail-open and will silently grant everything.**

---

## 1. The finding in one paragraph

Document access for The Agent is governed by the **v3 category ACL** (migration 016:
`DocumentCategories` / `DocumentTypeCategories` / `DocumentCategoryGroups`), resolved per user
via group membership by `doc_search_v3.acl.accessible_document_types(user_id, user_role)`.
That ACL is correctly enforced on exactly **one** endpoint —
`/api/internal/document-search-unified` ([`app.py:6241`](../app.py)) — which The Agent's
`search_documents` tool calls. Three neighbouring endpoints that The Agent also reaches
enforce **nothing**: the structured-records query returns rows from every document type
(G1), the document listing enumerates every filename in the store (G2), and agent-to-agent
delegation lets any user talk to any agent id and inherit that agent's document and knowledge
access (G3). All three are reachable today by a role-1 user through ordinary tool calls. The
fixes are small and largely mechanical — the danger is entirely in §2.

---

## 2. ⚠ Read this first — the fail-open trap, and why it applies to all three

`accessible_document_types()` returns a **three-state** value
([`doc_search_v3/acl.py:36`](../doc_search_v3/acl.py)):

| Return | Meaning |
|---|---|
| `None` | UNRESTRICTED — admin (role ≥ 3), or no identity presented |
| `["type", …]` | exactly these document types |
| `[]` | **DENY ALL** — and it is returned on *any* error (DB down, migration 016 missing, resolve failure). Fail-closed by design. |

Both downstream filters treat an empty list as **no filter**:

* [`DocUtils.py:651`](../DocUtils.py) — `if allowed_document_types:` … `[]` is falsy → no SQL filter
* [`document_records_query.py:222`](../document_records_query.py) — same shape, same trap
* [`document_records_query.py:88`](../document_records_query.py) `_coverage()` — same

So **passing `[]` straight through grants everything.** Every caller must gate:

```python
from doc_search_v3 import acl
allowed = acl.accessible_document_types(uid, role)
if acl.deny_all(allowed):
    return <empty / denied result>          # MUST NOT reach the query layer
```

`tests/unit/test_v3_acl.py` locks this with the first two tests in the file. If those two ever
fail together, a user with zero grants sees every document. **Do not "simplify" the deny_all
check away**, and do not change `_build_doc_type_filter` to fix the trap at the engine — the
legacy callers depend on the current behaviour and that is a much larger blast radius than
this handoff.

### The three-state contract must be preserved end to end

`None` (unrestricted) and `[]` (deny-all) are **not interchangeable** and neither is
"falsy". Any code you write that does `if allowed:` is wrong. Use
`if allowed is not None:` to decide whether to filter, and `acl.deny_all(allowed)` to decide
whether to stop.

### Identity is optional, forgery is not

The canonical assertion-handling block is [`app.py:6260-6279`](../app.py). Copy its semantics
exactly:

* header **absent** → `uid = None` → unrestricted (this is deliberate: schedulers,
  automations and the email dispatcher have no user and must keep working)
* header **present and valid** → use the claims
* header **present and invalid/expired/wrong-audience** → **hard 403**, never "treat as
  missing". A forged assertion that degrades to unrestricted is worse than no ACL at all.

```python
uid, role = None, None
assertion = request.headers.get("X-AIHub-User")
if assertion:
    try:
        import shared_auth
        # verify_token returns (claims, error) — NOT a bare dict.
        claims, verr = shared_auth.verify_token(assertion, shared_auth.AUD_INTERNAL)
        if verr or not claims:
            return jsonify({"status": "error", "message": "invalid user assertion"}), 403
        uid = claims.get("sub") or claims.get("user_id")
        role = claims.get("role")
    except Exception:
        return jsonify({"status": "error", "message": "invalid user assertion"}), 403
```

Prefer `shared_auth.claim_user_id(claims)` over `claims.get("sub")` where you need an int —
`sub` is minted as a **string** ([`shared_auth.py:129`](../shared_auth.py)).

### Never trust a user id from a request body

[`app.py:2721`](../app.py) currently reads `data.get('user_id')` from the POST body and only
falls back to the assertion. That is fine for its present purpose (artifact staging) but it is
**not an authorization input**. In G3 you must authorize from the **assertion only**.

---

## 3. G1 — `/api/internal/document-records` bypasses the ACL entirely

**Impact: highest.** Record rows *are* document contents — a compliance guide's requirements,
an invoice's line items. The tool description in
[`agent_service/document_tools.py:800`](../agent_service/document_tools.py) actively steers
the model to this tool for every "which / how many / list every" question, so a restricted
user's most census-shaped questions are answered from the *unrestricted* corpus.

### Evidence

[`app.py:6297`](../app.py) — the route reads no identity header and passes no allow list:

```python
@app.route("/api/internal/document-records", methods=['POST'])
@cross_origin()
@internal_api_key_required()
@_inflight_gated(_SEARCH_GATE, what="document records query", logger=logger)
def internal_document_records():
    try:
        data = request.get_json() or {}
        from document_records_query import query_document_records
        result = query_document_records(
            record_set=data.get("record_set"),
            search=data.get("search"),
            topic=data.get("topic"),
            document_type=data.get("document_type"),
            limit=data.get("limit") or 50,
        )
```

Meanwhile the callee already accepts and threads the parameter
([`document_records_query.py:129`](../document_records_query.py)):

```python
def query_document_records(record_set=None, search=None, topic=None,
                           document_type=None, limit=50,
                           allowed_document_types: Optional[List[str]] = None):
```

…through `_list_mode` (`:166`), `_query_mode` (`:206`, filters `d.document_type IN (…)` at
`:222`) and `_coverage` (`:83`, filters the denominator at `:88`). **The plumbing is done.
Only the route is missing.**

The Agent already sends the assertion on this call — `_post_main(..., internal=True)` uses
[`document_tools.py:146`](../agent_service/document_tools.py) `_headers()`, which mints
`X-AIHub-User` whenever there is a real user. So no agent-side change is needed for G1.

### The change

In `internal_document_records()`:

1. Insert the §2 assertion block (verbatim semantics — 403 on forged).
2. Resolve and gate:
   ```python
   from doc_search_v3 import acl
   allowed = acl.accessible_document_types(uid, role)
   if acl.deny_all(allowed):
       return jsonify({"status": "success", "result": {
           "ok": True, "mode": "denied", "rows": [], "fallback": False,
           "coverage": [],
           "text": ("You do not have access to any document categories. "
                    "An administrator can grant access on the Groups page."),
       }})
   ```
   Match the wording used by `document_search_wrapper.py:229` and
   `enumerate_engine.py:264` — the model has learned to relay it.
3. Pass `allowed_document_types=allowed` into `query_document_records(...)`.

**Do not** set `fallback: true` on the denied result. `fallback: true` tells the model to
retry via `search_documents` ([`document_tools.py:835`](../agent_service/document_tools.py)),
which will also (correctly) deny — producing two refusals and an unclear answer. A denied
result is terminal.

### Tests

Extend [`tests/unit/test_document_records_query.py`](../tests/unit/test_document_records_query.py):

* `allowed_document_types=['a']` restricts `_query_mode`'s SQL params and `_coverage`'s
  denominator (assert on the executed SQL, as the existing tests do).
* `allowed_document_types=[]` at the **query layer** produces no filter — i.e. document the
  trap, mirroring `tests/unit/test_v3_acl.py::TestTheFailOpenTrap`. This test failing later
  is good news, not bad.

Add a route-level test (new file, e.g. `tests_v2/unit/test_internal_records_acl.py`):

* absent header → `query_document_records` called with `allowed_document_types=None`
* valid assertion for a granted user → called with that user's list
* valid assertion for a user with zero grants → **`query_document_records` never called**,
  denied text returned
* forged / wrong-audience / expired assertion → **403**

---

## 4. G2 — `/api/documents` and `/api/document-types` enumerate the whole store

**Impact: metadata disclosure.** Contents don't leak, but filenames usually *are* the
sensitive part ("Project Falcon — termination terms.pdf"), and `/api/document-types` hands
back the complete taxonomy plus per-type counts.

### Evidence

[`app.py:14514`](../app.py) `api_get_documents` is `@api_key_or_session_required(min_role=2)`
and filters only on `d.is_knowledge_document = 0`. The Agent authenticates with the **tenant
API key**, so the `min_role=2` check never reaches the calling user — a role-1 user's
`list_documents` / `get_document` calls sail through. Same story at
[`app.py:14648`](../app.py) `api_get_document_types`.

Agent-side callers ([`agent_service/document_tools.py`](../agent_service/document_tools.py)):

| Line | Caller | Purpose |
|---|---|---|
| `:334` | `_existing_paths_for` | import idempotency probe — see the trap below |
| `:739` | `list_documents` tool | user-facing listing |
| `:780` | `get_document` tool | single-document metadata |
| `agent_builder_tools.py:361` | `/api/document-types` | populates the **per-agent** allow-list picker (Developer+, admin surface) |

### The change

In `api_get_documents()`:

1. §2 assertion block.
2. Resolve `allowed`; on `deny_all` return the **empty-but-well-formed** payload — the same
   `documents` / `pagination` / `stats` shape with zeroes. Do **not** 403: the agent's
   `list_documents` renders `stats.total_documents` and a 403 turns into "Could not list
   documents (HTTP 403)", which reads as an outage rather than an access boundary.
3. When `allowed is not None`, append `AND d.document_type IN (…)` to the row query **and to
   the statistics query at `app.py:14607`**. If you filter only the rows, `total_count` and
   `stats.total_documents` will contradict the rows and the model will report a discrepancy it
   cannot explain.
4. Apply the same treatment to `api_get_document_types()` (`app.py:14648`).

Also give `/api/documents/<id>` GET ([`app.py:9946`](../app.py)) the same check, or
`get_document` remains an id-oracle for a document the listing hid. Ids are opaque, so this is
lower risk than G1 — but it is two lines and closes the pair.

### ⚠ Trap — the import idempotency probe

`_existing_paths_for` ([`document_tools.py:331`](../agent_service/document_tools.py)) calls
`/api/documents` to decide whether a file was already imported. Once the route is filtered, a
user re-importing a file whose existing copy sits in a category they cannot see will get a
**duplicate row** — the exact mess migration-era idempotency was added to prevent.

Narrow in practice: role < 2 can only import from their own tree or delivered `/api/files`
refs ([`document_tools.py:409`](../agent_service/document_tools.py)), so this mostly affects
Developer (role 2) users, who *are* subject to the ACL. **Recommended:** filter anyway and
accept the edge case; note it in the code comment so the next reader does not "fix" it by
dropping identity from `_headers()`. **Do not** add an identity-suppressing bypass to
`_headers()` — that reintroduces a global fail-open for the sake of one probe.

### ⚠ Decision required — does the browser UI get filtered too? (see §8-D1)

### Tests

New `tests_v2/unit/test_api_documents_acl.py`:

* absent header → unfiltered SQL (today's behaviour preserved)
* granted user → `IN (…)` present on **both** the row query and the stats query
* zero-grant user → empty payload with a well-formed `pagination`/`stats` block, HTTP 200
* forged assertion → 403

---

## 5. G3 — `ask_agent` launders both the agent-visibility and document ACLs

**Impact: two ACLs bypassed in one call, and it is the only route into the 1,579 knowledge
documents.**

### Evidence

[`app.py:2652`](../app.py):

```python
@app.route('/api/agents/<int:agent_id>/chat', methods=['POST'])
@cross_origin()
@api_key_or_session_required()          # no min_role, no visibility filter
def api_agent_chat(agent_id):
```

The platform already has the resolver — [`app.py:2557`](../app.py)
`_agent_visibility_filter()` (→ `DataUtils.accessible_agent_ids`, the same
`None` / `[ids]` / `[]` three-state contract) — and applies it to
`/api/agents/summary` and `/api/agents/list`. It is **not** applied here.

On the caller side, [`agent_service/platform_tools.py:34`](../agent_service/platform_tools.py):

```python
def _headers():
    return {"X-API-Key": AI_HUB_API_KEY, "Connection": "close"}
```

No `X-AIHub-User`. `ask_agent` (`:313`) posts through `_post` (`:56`) with those headers, so
the answering agent runs with **its own** per-agent `AgentDocumentTypes` allow list and **its
own** `AgentKnowledge` documents, with no reference to who asked.

`list_agents` *is* visibility-filtered, so exploiting this needs a guessed id — but agent ids
are small integers and the model will try them when a user asks it to.

### The change — two sides, both required

**Main app** (`api_agent_chat`, `app.py:2655`), immediately after the payload validation:

```python
allowed_ids = _agent_visibility_filter()
if allowed_ids is not None and int(agent_id) not in allowed_ids:
    return jsonify({'status': 'error',
                    'response': 'You do not have access to that agent.'}), 403
```

Note the three-state handling: `None` = admin / no assertion = unfiltered (preserves every
existing session and service caller); `[]` = deny-all and the `not in` check refuses, which is
correct.

**Agent service** ([`platform_tools.py`](../agent_service/platform_tools.py)): teach `_headers()`
to attach the assertion. Mirror
[`document_tools.py:146-166`](../agent_service/document_tools.py) — **including its guard that
`user_id` of `0` is the service principal and must NOT mint an assertion** — but **do not
copy its `except: pass`.** That swallow is itself a finding (G5 in the background doc): on a
box where signing fails it silently degrades to unrestricted. For this new call site, log and
refuse. `tests_v2/unit/test_cc_doc_identity.py` is the reference for the contract you want
("must RAISE — not silently degrade to unrestricted — when signing fails").

`_headers()` is shared by every `platform_tools` call, so adding identity to it changes the
headers on `list_data_connections`, `probe_connection_query`, `ask_data_agent` and the rest.
Those endpoints ignore an unknown header today, so this is additive — **but re-run pack 20's
tool smoke** (`test_human/20_The_Agent/tool_smoke_checks.py`) to confirm, and mention it in the
commit message.

### ⚠ Trap — do not authorize from the body

`api_agent_chat` reads `data.get('user_id')` at [`app.py:2721`](../app.py) and only falls back
to the assertion. Leave that path alone (it feeds artifact staging), but **authorize strictly
from `_agent_visibility_filter()`**, which reads the assertion and nothing else. If you
authorize from the body's `user_id`, any caller holding the API key can name any user.

### Out of scope for G3

Making the answering agent honour the **caller's** document categories (rather than its own
per-agent allow list) is a genuinely bigger design question — the two ACLs have different
shapes and the classic General Agent has no user identity in its tool context. **Do not
attempt it here.** G3's goal is only: you may delegate to agents you can already see. If
after G3 a user can still reach categories they lack through an agent that *is* shared with
them, that is a known, accepted, and separately-tracked residue — say so in the commit
message rather than quietly widening scope.

### Tests

New `tests_v2/unit/test_agent_chat_visibility.py`:

* no assertion → unfiltered, agent reachable (preserves today's service callers)
* assertion for a user whose groups include the agent → reachable
* assertion for a user whose groups exclude it → **403**
* assertion for a user with no groups (`accessible_agent_ids` → `[]`) → **403**
* body `user_id` naming an authorized user + assertion naming an unauthorized one → **403**
  (authorization comes from the assertion)

Plus, in `tests_v2/unit/`: `platform_tools._headers()` mints a verifiable assertion for a real
user, mints nothing for `user_id` 0 / absent, and **raises rather than degrading** when
signing fails.

---

## 6. Order of work and effort

| # | Item | Files | Rough size |
|---|---|---|---|
| 1 | **G1** — records route | `app.py` (1 route), tests | ~25 lines + tests |
| 2 | **G2** — documents listing | `app.py` (3 routes), tests | ~50 lines + tests |
| 3 | **G3** — agent chat + agent-side headers | `app.py` (1 route), `agent_service/platform_tools.py`, tests | ~35 lines + tests |

Ship them as **three separate commits** — they have different blast radii and G2 carries a
UI-visible decision (§8-D1). G1 first: highest impact, smallest change, zero UI surface.

Restart requirements after each: main app (`app.py`) needs the 5001 restart; G3 also needs the
5111 agent-service restart. Use the deterministic V3 restart script — and per the standing
note, **never pipe it from an agent shell**.

---

## 7. Verification — live, on this box

Live state at the time of writing (`2026-09-03`): tables present, 95 categories, 124
type→category mappings, 1602 grants, 40 distinct general document types, 397 general + 1,579
knowledge documents.

**Test subjects — do not use an admin.** `accessible_document_types` short-circuits to
unrestricted for role ≥ 3 without consulting the tables, so an admin account can never
demonstrate a restriction. Verified resolver output on this box:

| uid | user | role | resolver |
|---|---|---|---|
| 1 | `jsmith` | 1 | 118 types |
| 10 | `tbrady` | 1 | `[]` — **deny all** (in no group) |
| 54 | `cservice` | 1 | `[]` — **deny all** (in no group) |
| 141 | `developer` | 2 | 118 types |
| 12 | `james` | 3 | `None` — unrestricted |

`tbrady` and `cservice` are ready-made deny-all subjects. `developer` / `jmiller` (role 2) are
ready-made restricted-but-not-empty subjects. To build a *narrowly* granted subject, create a
group with one category granted on the Groups page and put a fresh user in it.

**Per fix:**

* **G1** — as `developer`, ask The Agent a census question ("how many documents state X",
  "which guides require Y"). Confirm the `COVERAGE:` line's denominator counts only granted
  types. As `tbrady`, confirm the denial text, and confirm the model does **not** then retry
  `search_documents`.
* **G2** — as `developer`, `list_documents` must show only granted types and its
  "store holds N" figure must agree with the rows. As `tbrady`, an honest empty result, not an
  HTTP error.
* **G3** — as `developer`, `ask_agent` against an agent id **not** in their groups must refuse.
  Confirm the same id still works for `james`. Confirm the classic agent UI and any CC
  delegation still work (those callers present no assertion → unfiltered).

**Regression:** run pack 15 (platform regression gate) before building, and pack 20 after —
G3 touches shared `platform_tools._headers()`.

**Read-only state probes** used to produce the table above are in the scratchpad
(`chk_acl_state.py`, `chk_resolver.py`); re-create them rather than hunting for them — they
are ~30 lines each: connect via `CommonUtils.get_db_connection_string()`, `EXEC
tenant.sp_setTenantContext ?, os.getenv('API_KEY')`, then call
`acl.accessible_document_types(uid, role)` per user.

---

## 8. Decisions James must make

**D1 — Does G2 filter the browser UI too?** As specified above, G2 only filters when an
`X-AIHub-User` assertion is present, so the classic Documents page (session auth, `min_role=2`)
stays unfiltered — a Developer browsing the UI still sees every filename while the *same
person* asking The Agent does not. That inconsistency is defensible (the UI is a Developer
admin surface) but it is a real seam.

*Recommendation:* ship G2 assertion-only first, then decide separately whether the session
path should resolve identity from `current_user`. Doing both in one commit conflates a
security fix with a UI behaviour change.

**D2 — Migration 016 on target installs.** No runner ships it (004 and 008 were each run by
hand). If 016 is absent, the resolver's `except` returns `[]` and **every non-admin gets
deny-all** on documents. After G1/G2 that failure spreads to records and listings too, so the
blast radius of a missed migration grows with this work. Worth deciding now whether a
`run_doc_categories_migration.py` sibling ships alongside.

**D3 — Residual knowledge-document exposure after G3.** G3 stops delegation to *unshared*
agents. It does not stop a user reaching document categories they lack via an agent that **is**
shared with them and holds broader access. Accept and track, or scope a follow-on.

---

## 9. Reference — files and lines

| What | Where |
|---|---|
| Resolver (three-state contract) | [`doc_search_v3/acl.py:36`](../doc_search_v3/acl.py) `accessible_document_types` |
| Deny-all guard | [`doc_search_v3/acl.py:78`](../doc_search_v3/acl.py) `deny_all` |
| The fail-open trap it guards | [`DocUtils.py:626`](../DocUtils.py) `_build_doc_type_filter` |
| **Canonical** assertion block to copy | [`app.py:6260-6279`](../app.py) |
| Assertion minting (with the uid-0 guard) | [`agent_service/document_tools.py:146`](../agent_service/document_tools.py) `_headers` |
| Assertion minting contract test | [`tests_v2/unit/test_cc_doc_identity.py`](../tests_v2/unit/test_cc_doc_identity.py) |
| **G1** route to fix | [`app.py:6297`](../app.py) `internal_document_records` |
| G1 callee (already plumbed) | [`document_records_query.py:129`](../document_records_query.py) · `_list_mode:166` · `_query_mode:206` · `_coverage:83` (filter at `:88`) |
| **G2** routes to fix | [`app.py:14514`](../app.py) `api_get_documents` · [`app.py:14648`](../app.py) `api_get_document_types` · [`app.py:9946`](../app.py) single-document GET |
| G2 stats query needing the same filter | [`app.py:14607`](../app.py) |
| G2 idempotency probe (the trap) | [`agent_service/document_tools.py:331`](../agent_service/document_tools.py) `_existing_paths_for` |
| **G3** route to fix | [`app.py:2652`](../app.py) `api_agent_chat` |
| G3 resolver to reuse | [`app.py:2557`](../app.py) `_agent_visibility_filter` → `DataUtils.accessible_agent_ids:1791` |
| G3 caller headers to fix | [`agent_service/platform_tools.py:34`](../agent_service/platform_tools.py) `_headers` · `ask_agent:313` |
| G3 body-`user_id` path to leave alone | [`app.py:2721`](../app.py) |
| JWT helpers | [`shared_auth.py:124`](../shared_auth.py) `sign_user_assertion` · `:262` `claim_user_id` · `AUD_INTERNAL` |
| Existing ACL tests | [`tests/unit/test_v3_acl.py`](../tests/unit/test_v3_acl.py) · [`tests/unit/test_document_records_query.py`](../tests/unit/test_document_records_query.py) |
| Admin UI (grants) | [`app.py:4261`](../app.py) / [`app.py:4296`](../app.py), `templates/groups.html:701` |
| Admin UI (categories) | [`app.py:4333`](../app.py) `/document_categories` |
| Schema | [`migrations/016_document_categories_and_group_access.sql`](../migrations/016_document_categories_and_group_access.sql) |

---

## 10. Do-not list

1. **Do not** pass `[]` to `query_document_records`, `document_search_super_enhanced_debug`, or
   any `IN (…)` builder. Gate on `acl.deny_all()` first. `[]` means deny-all everywhere except
   in the SQL builders, where it means allow-all.
2. **Do not** write `if allowed:` — `None` and `[]` are both falsy and mean opposite things.
   Use `if allowed is not None:` and `acl.deny_all(allowed)`.
3. **Do not** treat an invalid assertion as a missing one. Forged → 403.
4. **Do not** authorize from a request-body `user_id`.
5. **Do not** copy `document_tools._headers()`'s `except: pass` into new code. Log and refuse.
6. **Do not** add an identity-suppressing bypass to `_headers()` to keep the import
   idempotency probe unfiltered — that is a global fail-open for one edge case.
7. **Do not** "fix" `_build_doc_type_filter`'s empty-list behaviour. Legacy callers depend on
   it; the blast radius is far larger than this handoff.
8. **Do not** test with an admin account and conclude the ACL works. Role ≥ 3 never consults
   the tables.
9. **Do not** create a branch. Commit to `main`, promptly, one commit per fix.
10. **Do not** widen G3 into "make delegated agents honour the caller's categories". That is a
    separate design problem (§5, Out of scope).
