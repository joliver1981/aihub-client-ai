# The Agent — HTML email + embedded View dashboards

**Status:** plan, not built. Written 2026-08-13.
**Scope owner:** James.
**Related:** [docs/the-agent-plan.md](the-agent-plan.md), [docs/views-v2-spec.md](views-v2-spec.md)

---

## 1. Goal

Two things The Agent cannot do today:

1. Send a **nicely formatted HTML email** (today it sends plain text only).
2. **Embed a saved View's dashboard** in the body of that email — including on a
   schedule: *"email me this dashboard every weekday at 9am."*

The scheduled case is the one with the most leverage: the refresh path is already
deterministic and LLM-free, so a daily dashboard email costs zero AI tokens per send.

## 2. Decisions taken (James, 2026-08-13)

| Decision | Choice | Consequence |
|---|---|---|
| Chart rendering | **HTML tables + table-based CSS bars** | Renders everywhere. No SVG, no PNG, no `data:` URIs. |
| Cloud repo | **No `aihub-api` changes** | `cid:` inline images are off the table (see §3.3). All work lands in this repo. |
| Scope policy | **No guard** — if a user can see a View, they can email it | Nothing to build; noted here so it isn't re-litigated. |
| Phase 3 (PNG charts) | **Deferred** | Line charts degrade to tables until/unless it's revisited. |

## 3. Current state

### 3.1 What The Agent does today — plain text, end to end

| Fact | Evidence |
|---|---|
| The outbound tool declares `body` as *"Plain-text draft body"* | [work_tools.py:309](../agent_service/work_tools.py) |
| The transport sends `{to, subject, body, provider, from_address, from_name}` — no `html_body`, no attachments | [email_client.py:134](../agent_service/email_client.py) |
| Auto-send ON → sends immediately; OFF → `edit_and_return` work item, human edits in a plain `<textarea>` | [work_tools.py:352](../agent_service/work_tools.py), [index.html:1826](../agent_service/static/index.html) |
| The approval path sends the approved text through the same plain-text call | [main.py:370](../agent_service/main.py) |

Consequence: HTML markup placed in `body` today arrives as **literal tags**, because
the Mailgun leg maps `body → data["text"]`.

### 3.2 The transport already supports HTML — The Agent is the only caller not using it

- The cloud route accepts `html_body` and base64 `attachments`
  ([notification_routes.py:101](file:///C:/src/aihub-api/project/api/notification_routes.py)),
  honored on both the Azure leg
  ([notification_utils.py:311](file:///C:/src/aihub-api/project/api/notification_utils.py))
  and the Mailgun leg ([:385](file:///C:/src/aihub-api/project/api/notification_utils.py)).
- It is a **proven live path in this platform**: scheduled portal workflows already
  send styled HTML with an attachment and an `APP_PUBLIC_BASE_URL` deep link
  ([portal_workflows_routes.py:199](../routes/portal_workflows_routes.py)).

So Phase 1 is wiring, not invention.

### 3.3 Why `cid:` inline images are excluded

The Mailgun leg always posts files as `("attachment", …)`, never `("inline", …)`, and
the Azure leg builds attachments with no `contentId`. Embedded images therefore require
an `aihub-api` change — which auto-deploys to Azure on push. Out of scope by decision.

### 3.4 Views are already shaped for this

`run_view()` in [views_tools.py](../agent_service/views_tools.py) is a **pure server-side
Python function**, LLM-free, returning exactly the render contract we need:

```
{name, description, scope, version, updated_at,
 tiles: [{index, title, viz, layout, columns, rows, row_count,
          error?, cache?{columns, rows, cached_at}}]}
```

- ≤ 8 tiles (`MAX_TILES`), ≤ 50 rows per tile (`TILE_ROW_CAP`).
- `viz` ∈ `auto | stat | table | ticker | line | bar`.
- Failed tiles carry an honest `error` **plus** their last-good `cache` with `cached_at`.
- Already reachable headlessly: `/api/views/run` (user JWT) and
  `/api/views/refresh-cache` (service key + stored principal, [main.py:650](../agent_service/main.py)).

**The gap:** rendering exists only in the browser — charts are inline SVG built with
`createElementNS` ([index.html:907](../agent_service/static/index.html)), tiles are DOM
built in JS ([:971](../agent_service/static/index.html)). A server-side Python renderer
is a port of ~100 lines of JS over a contract that is already clean.

---

## 4. Design

### 4.1 New module: `agent_service/email_render.py`

Pure functions, no I/O, no LLM, no new dependencies — trivially unit-testable.

```
render_shell(title, blocks) -> str          # 600px table shell, inline styles
render_view(view_result)    -> (html, text) # the whole dashboard
render_tile(tile)           -> str          # dispatch on viz
render_markdownish(body)    -> str          # headings/bold/lists/links/paras
```

Per-`viz` behavior:

| `viz` | Email rendering |
|---|---|
| `stat` | Big number + label cell |
| `table` / `auto` | Bordered table, header row, zebra rows |
| `bar` | **Nested-table bars**: label cell + a track cell containing a `bgcolor` cell sized `width="N%"` |
| `line` | **Degrades to a table** (with the min–max summary line the UI shows) |
| `ticker` | **Degrades to a table** (CSS animation cannot travel) |

Every tile also emits its honesty line — `as of <cached_at>`, `(stale — last good result)`,
or the per-tile error text. This is not decoration: `run_view` deliberately serves stale
cache on failure, so an email that hides that ships confidently wrong numbers.

### 4.2 Email-HTML rules (these are the constraints that actually bite)

- **Inline styles only** — no CSS classes, no `<style>` block.
- **Tables for layout.** No flex, no grid, no float, no `position`.
- **Bars are nested tables with `bgcolor`**, not `div` widths — Outlook's Word engine is
  unreliable with the latter.
- **Max width 600px**, explicit `bgcolor` on every cell so dark-mode auto-inversion in
  Gmail/Outlook can't produce unreadable pairings.
- **Watch total size.** Gmail clips messages over ~102KB. Eight tiles × 50 rows can
  approach that. → **Email-specific row cap of 15 rows/tile**, rendered as
  *"showing 15 of 47 rows"* with a deep link back to the Views screen via
  `APP_PUBLIC_BASE_URL` (same pattern as [portal_workflows_routes.py:218](../routes/portal_workflows_routes.py)).
- Plain-text alternative is **always** generated alongside — never send HTML-only.

### 4.3 Who authors the HTML

**The service, not the model.** `draft_email_reply` keeps taking a plain/markdown-ish
`body`; a new `rich: bool` parameter makes the service convert it deterministically via
`render_markdownish`. Rationale: a model emitting raw HTML produces broken markup and
inconsistent styling, and there is no clean text fallback to derive. The model writes
prose; the platform owns presentation.

---

## 5. Phases

### Phase 1 — HTML transport (~½ day)

| File | Change |
|---|---|
| [email_client.py](../agent_service/email_client.py) | `send_reply(..., html_body=None)`; include `html_body` in the payload only when set |
| [work_tools.py](../agent_service/work_tools.py) | `draft_email_reply` gains `rich: bool`; renders `body` → HTML; passes both |
| [work_tools.py](../agent_service/work_tools.py) | Auto-send branch passes `html_body` through |
| [main.py](../agent_service/main.py) | Approval payload carries `html_body`; `work_respond` re-renders the approved text at send time |
| `email_render.py` | New — shell + markdown-ish converter |

**Approval semantics:** the human edits *prose* in the textarea; the service re-renders
that prose to HTML at send. What they approve is what sends, in both formats.

### Phase 2 — Embed a View (1–2 days)

Extend the **existing** `draft_email_reply` with an optional
`include_view: {name, scope?, group_id?}` rather than adding a second send tool. Reason:
auto-send, the approval queue, and the `outbound_enabled` kill switch are one chokepoint
today — a parallel tool would duplicate and eventually diverge from all three.

Flow: tool resolves the view via `views_store.get()` (visibility enforced there) →
calls `run_view()` **server-side** → `render_view()` → appends to both HTML and text bodies.

**The model never sees the tile data.** Numbers are deterministic and cannot be
paraphrased, rounded, or hallucinated into the email.

**Freshness:** when the send is queued for approval, the dashboard re-renders at
*approval* time, not draft time. The email then states its own as-of timestamps, so
this is honest either way — but it must be stated in the tool's result text so The Agent
doesn't tell the user "I've drafted an email with today's numbers" about a send that may
happen tomorrow.

### Phase 3 — PNG charts via `cid:` — **DEFERRED** (no `aihub-api` changes)

Recorded so the numbering matches the discussion. Revisit only if table-based bars prove
insufficient. A matplotlib renderer already exists at
[nlq_agentic/charts.py:22](../nlq_agentic/charts.py) if it's ever picked up — note it
lives in the **main app env**; `aihub-agent` has no matplotlib or pandas.

### Phase 4 — "Email me this dashboard daily at 9am" (~½–1 day)

The machinery is already built; this is a near-clone of the `view_refresh` job family.

| File | Change |
|---|---|
| [main.py](../agent_service/main.py) | New `POST /api/views/email` — service-key auth + stored principal, cloned from `/api/views/refresh-cache`: `run_view()` → `render_view()` → send |
| [job_scheduler.py:113](../job_scheduler.py) | Register `view_email` in `job_types`, behind the same `AGENT_SESSION_JOBS_ENABLED` flag |
| [job_scheduler.py:1310](../job_scheduler.py) | Executor cloned from `_execute_view_refresh_job`, pointed at the new endpoint |
| [views_tools.py:443](../agent_service/views_tools.py) | New `schedule_view_email` tool — `schedule_view_refresh` plus recipients, subject, and **timezone** |

**Timezone is a required part of this phase, not a nicety.** `schedule_view_refresh`
passes no timezone at all, so a cron `0 9 * * *` created through it fires at the
scheduler's default (UTC) — "9am" would be wrong for every user. The mechanism already
exists and is used by Command Center: resolve the user's spoken zone with
[schedule_tz.py](../schedule_tz.py) `resolve_timezone()` at schedule-creation time and
pass the canonical zone as **`parameters.timezone`**; the engine reads it at
[job_scheduler.py:318](../job_scheduler.py) and builds a DST-aware `CronTrigger`
([:512](../job_scheduler.py)). Mirror
[schedule_logic.py:162](../command_center_service/scheduling/schedule_logic.py).

Also carry over from `schedule_view_refresh`: `target_id` must be the **string** `"0"`
(the route's presence check treats int `0` as missing), and job creation must be verified
by read-back before reporting success.

**From-address and kill switch:** send as the scheduling user's agent email address
(`email_store.get_address(uid)`), honoring `outbound_enabled`. If they have no active
address, **fail at schedule time** with a clear message — never create a job that will
fail silently at 9am every day.

**Failure visibility:** the executor writes the send result into the execution record's
`result_message`, exactly as the refresh executor does today, so a broken daily email is
visible in scheduler history rather than merely absent from an inbox.

---

## 6. Testing

- **`tests_v2/unit/`** — golden-output tests for `email_render.py`: each `viz`, the
  degradation paths (line/ticker → table), the stale-cache label, the per-tile error
  label, the row cap, and total rendered size. Plus `html_body` plumbing through
  `send_reply` and the approval payload round-trip.
- **`test_human/20_The_Agent/runner.py`** — live checks modeled on the existing JSS
  livefire checks (`V21-3`, `V23-3`): schedule a `view_email` job on a short interval,
  assert a real send result lands in the execution record.
- **Real-client visual pass** — send to `joliver81@gmail.com`, eyeball in Gmail (web +
  mobile) and Outlook desktop, in both light and dark mode. Table-based bars are exactly
  the thing that looks fine in a unit test and wrong in Outlook.

## 7. Packaging

No installer change needed: `agent_service\*` is wildcard-copied with `recursesubdirs`
([AIHub_Setup_Script_v5_OneDir_Dev.iss:79](../AIHub_Setup_Script_v5_OneDir_Dev.iss)) and
staged by `scripts/build_agent_service.ps1`. New modules are picked up automatically —
unlike `browser_use_service`, which needs explicit loose-module entries.

Restart required after Phase 4: `job_scheduler.py` changes need the JSS restarted, and
agent_service changes need The Agent service restarted.

## 8. Open risks

| Risk | Mitigation |
|---|---|
| Gmail clips >102KB | Email row cap (§4.2) + size assertion in unit tests |
| Dark-mode auto-inversion | Explicit `bgcolor` on every cell; visual pass in both modes |
| Outlook mangles bars | Nested tables, not divs; visual pass |
| Approval UX confusion (prose edit vs rendered dashboard) | Tool result text states the re-render-at-send behavior explicitly |
| Scheduled sends fail unseen | Send result recorded in the execution record (§ Phase 4) |
| Tenant email quota | Cloud enforces per-tenant limits and returns `blocked_by_limit`; surface it verbatim, never as success |

## 9. Effort

| Phase | Estimate |
|---|---|
| 1 — HTML transport | ~½ day |
| 2 — Embed a View | 1–2 days |
| 4 — Scheduled dashboard email | ~½–1 day |
| **Total** | **~2½–3½ days** |
