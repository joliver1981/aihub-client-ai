# Views v2 — automation-backed tiles + scoped views

Status: SPEC for review (James, 2026-08-07). Extends the A5 Views MVP
(commit e17ea8d). Doctrine unchanged: purely additive, existing behavior
untouched, everything degradable back to v1 semantics.

## 1. Goals

1. **Automation-backed tiles** — a tile can render the output of a pinned
   automation, which unlocks every data source the platform can already reach
   deterministically: web scrapes (browser-use), external APIs (with secrets
   from the Local Secrets store), Python transforms. One dashboard can mix a
   SQL stat, a scraped table, and an API-fed ticker — with zero new
   execution or egress paths, because the automation lifecycle already
   governs all of it.
2. **Scoped views** — user / group / tenant, mirroring the skills model
   exactly, including its promotion governance:
   - **user** — private; save directly.
   - **group** — save directly IF the saver belongs to the group
     (membership verified server-side); no approval step.
   - **tenant** — ALWAYS goes through an admin approval that lands in the
     admin's **My Work** queue; the view is not visible tenant-wide until a
     role≥3 user approves it there. No bypass — same as skills, an admin's
     own request also files the item (self-approvable, but the audit trail
     exists).

## 2. Non-goals (v2)

- Raw `api`/`http` tile type (frozen GET + extraction). Deliberately
  excluded: it would create a new ungoverned egress path. The automation
  tile covers the use case through governed code. Revisit only if the
  automation flavor proves too heavy in practice.
- Charts/ticker visualizations and per-tile auto-refresh intervals —
  **v2.1** (client-only, cheap, no schema impact: `viz` values `ticker`,
  `line`, `bar`; optional `refresh_seconds` per tile).
- Per-view deep links (`#view=name` hash) — v2.1, trivial.
- Editing tiles from the UI — authoring stays conversational (save_view
  replaces; version bumps), same as v1.

## 3. Scoping model

### 3.1 Namespaces

A view's identity becomes `(scope, namespace, name)`:

| scope  | namespace          | visible to                       | who saves directly       | who deletes/updates |
|--------|--------------------|-----------------------------------|--------------------------|---------------------|
| user   | owner user id      | owner only                        | owner                    | owner               |
| group  | group id           | members of that group             | any member (verified)    | any member          |
| tenant | (single)           | every Agent user                  | NOBODY — approval only   | admin (role≥3)      |

Same name may exist in several scopes. Resolution order when a bare name is
ambiguous (chat, `/api/views/run`): **user > group > tenant** — identical to
skills precedence. The API accepts explicit `{name, scope, group_id}` to
bypass resolution; the UI always passes the explicit triple.

### 3.2 Promotion flow (mirrors save_skill exactly)

- `save_view(scope="user")` → direct write (default).
- `save_view(scope="group", group_id=G)` → server verifies membership via
  the live UserGroups read-through (`readthrough.user_group_ids`); reject
  with an honest error if not a member. The agent must ask the user which
  group and confirm before sharing — prompt guidance, same as skills.
- `save_view(scope="tenant")` → does NOT write. Creates an `approve_deny`
  work item with payload `kind="view_promotion"` carrying the full view
  definition (name, description, tiles, requested_by). Tool reply states
  plainly: *requested, not published*.
- **Publish hook**: `POST /api/work/respond` — alongside the existing
  `skill_promotion` branch, a `view_promotion` branch: decision `approved`
  + responder role≥3 → write the view into tenant scope; role<3 → 403.
  Denied → item closes, nothing written. Lifecycle events land in the
  existing work_item_events log either way.
- Promotion **copies** the definition into the target scope; the source
  view stays where it was (skills parity).

### 3.3 Storage & migration

`views` table (service-owned SQLite, single writer) gains:

    scope       TEXT NOT NULL DEFAULT 'tenant'   -- user | group | tenant
    group_id    INTEGER                          -- scope=group only
    tile_cache  TEXT                             -- last successful run (JSON)
    cached_at   TEXT

SQLite can't drop the existing table-level `UNIQUE(name)`, so `init()`
performs a one-time rebuild migration when the old shape is detected
(create-copy-drop-rename inside one transaction), then creates:

    CREATE UNIQUE INDEX views_ns ON views(
      scope, ifnull(group_id,0),
      CASE WHEN scope='user' THEN ifnull(owner_user,0) ELSE 0 END,
      name);

**Grandfathering:** existing v1 rows (install-wide today) migrate to
`scope='tenant'` — visibility is unchanged for them, no retroactive
approval. Logged at migration time.

## 4. Automation-backed tiles

### 4.1 Tile schema

    {"type": "automation",            // default remains "sql" when absent
     "title": "AR aging (scraped)",
     "automation": "<name or id>",    // resolved + validated at save time
     "inputs": {"region": "east"},    // optional, frozen into the tile
     "viz": "table" | "stat" | "auto"}

Save-time validation (read-back honesty, extends v1's connection check):
the automation must exist AND have a **pinned version** — a tile may never
run draft code. Unpinned → refuse with "promote it first".

### 4.2 Output contract

The automation's **last stdout line** must be a single JSON value:

    {"columns": ["sym","px"], "rows": [["ACME", 41.2], ...]}   // explicit
    [{"sym":"ACME","px":41.2}, ...]                             // list of dicts (normalized)
    {"value": 41.2, "label": "ACME"}                            // single stat

`run_view` parses it out of the run result's `stdout_tail` and normalizes to
column-ordered arrays (same renderer as SQL tiles). Row cap **50** — parity
with the probe seam; tiles are pulse numbers and top-N lists, not exports.
No parseable JSON on the last line → per-tile error naming the contract.
A "building a view tile" recipe is added to the `aihub-playbook-lifecycle`
product skill (print-based contract — zero engine/SDK changes, additive).

### 4.3 Refresh mechanics & honesty rules

Refresh runs the **pinned version** through the existing manage `run`
seam with the refreshing user's context (audit-true: the run row shows who
refreshed). Per-tile honesty branches, all surfaced in the tile, never
blended:

- **failed / error** → tile error with exit code + stderr tail.
- **client timeout / still executing** (`timed_out`, `inline_wait_elapsed`,
  per-tile budget `VIEW_TILE_TIMEOUT`, default 120s) → tile shows "still
  running (run_id …)" and keeps displaying the **cached** last result,
  labeled with its `cached_at` timestamp. Never presented as fresh.
- **paused at a checkpoint** (`waiting_on_checkpoint`) → a dashboard
  refresh cannot wait on humans: the service immediately settles the
  checkpoint **abort** via the existing decide seam (comment: "view
  refresh cannot wait on approvals") so no orphan approval debris lands in
  My Work, and the tile errors with "this automation checkpoints — remove
  the checkpoint or don't use it in a View."

SQL tiles keep v1 behavior. Tiles refresh concurrently (asyncio gather)
so one slow automation doesn't serialize the dashboard; results paint from
`tile_cache` instantly on open, then update as runs land.

Two limits made explicit during review (2026-08-07):
- The run seam returns only the final ~2000 chars of stdout, so the tile's
  JSON line must stay under ~1900 chars; the contract error and the product
  skill both say so.
- Automation RUNS are Developer+ platform-wide, so a role<2 viewer (the
  AGENT_ALLOW_ALL_USERS direction) gets an honest per-tile "requires a
  Developer role" error plus the cached last result. Plain-user dashboards
  get live automation data via v2.1 scheduled refresh + cache, not by
  bypassing the platform's run gating.

### 4.4 Cost note

An automation tile costs one automation run per refresh (still zero LLM
unless the automation itself calls `aihub.llm`). The UI shows per-tile
duration; v2.1's `refresh_seconds` gives ticker-style tiles their own
cadence instead of hammering the whole board.

## 5. API & tool changes

- `save_view` — gains `scope` (default `user` — **changed from v1's
  implicit install-wide**; the grandfathered rows keep tenant visibility)
  and `group_id`; tiles accept the new `type`/`automation`/`inputs` keys.
- `list_saved_views` / `GET /api/views` — returns only what the caller can
  see (own + their groups + tenant), each row carrying scope + group_id.
- `POST /api/views/run` — accepts `{name, scope, group_id}`; enforces
  visibility server-side (a non-member running a group view by guessed
  name → 403, not a result).
- `delete_view` — namespace-aware; permission table in §3.1; two-step
  confirm kept.
- `POST /api/work/respond` — `view_promotion` publish branch (role≥3).
- Mutation-claim guard: `save_view`/`delete_view` already registered; no
  change.

## 6. UI changes

- Views list: scope chips (same palette as Skills: user/group/tenant),
  grouped sort (mine → groups → tenant); save flow stays conversational.
- Tile header shows source badge (`SQL` / `automation ▷ name`) and, for
  cached paints, "as of <cached_at>" until fresh data lands.
- Detail header: refresh-all plus per-tile refresh on hover.

## 7. Test plan (pack 20 additions)

- **V2-1** scope isolation: user A's private view invisible to user B's
  list AND 403 on direct run (deterministic, two tokens).
- **V2-2** group gate: non-member `save_view(scope=group)` rejected;
  member succeeds; member B sees it.
- **V2-3** tenant promotion: request → work item in My Work → role<3
  approval 403s → role≥3 approve publishes → visible to a plain user;
  denial publishes nothing.
- **V2-4** automation tile e2e: build+promote a tiny automation printing a
  JSON table, save a view on it, run → rows render; run row exists with
  the refreshing user (ground truth via manage `runs`).
- **V2-5** checkpoint honesty: tile automation that checkpoints → tile
  error + checkpoint auto-aborted (no pending approval left behind).
- **V2-6** contract violation: automation printing no JSON → per-tile
  error naming the contract; other tiles unaffected.
- **V2-7** migration: v1-shape DB migrates; legacy rows readable as
  tenant scope.

## 8. Build estimate & order

~2 days: (1) store migration + scoped CRUD + endpoints (half day),
(2) automation tiles + honesty branches + concurrent refresh + cache
(half day), (3) promotion hook + UI + product-skill recipe (half day),
(4) pack V2-1..7 + live gate + restart ritual (half day).
