# Custom Apps in The Agent — plan

**Status:** plan approved with changes on 2026-10-03, using approach B. No code has been written. Before any code: commit and push all open changes (§12, step 0).
**Date:** 2026-10-03, revised the same day with James's changes · **Asked for by:** James (2026-10-02) · **Written by:** Claude, from four read-only surveys of this tree and of `C:\src\aihub-apps`.
Line numbers are as of 2026-10-03. Where lines are likely to move, function names are given too.

---

## 1. Summary

**What it is.** A Developer or Admin describes a screen to The Agent, and The Agent builds it. They preview it, promote it, and give access to users or groups. Those users open it from a **Custom Apps** menu in The Agent. Apps are stored content: they are created in production, and no app needs a platform code change, rebuild or reinstall.

**The design (approach B, agreed).**
- Each app is **one HTML page plus a manifest**. The manifest lists exactly what data the app reads and which actions it can trigger.
- The page runs in a **sandboxed frame**. It cannot read The Agent's sign-in token or cookies, and it is blocked from the network (§6.6 lists the edge cases).
- Every request goes through **the bridge**: one server entry point that checks the viewer has been given the app, then runs only what the *published* version declared.
- Any logic beyond reading data runs as a promoted **Automation**.

**Built additively, behind one setting.**
- New code goes in new files. Existing files only get small registration hooks, which are skipped when the feature is off (§4.1).
- One setting, `AGENT_APPS_ENABLED`, turns the whole feature on or off, and it is **on by default**.
- Turning it off hides the Custom Apps and App Studio menus and switches the feature off. Everything else in The Agent and the platform behaves exactly as before.

**What already exists.**
- **Views** is very close to this: sharing, promotion, an "Edit with AI" chat per view, SQL and automation-backed data, and a server cache.
- Automations already provide the backend.
- My Work already handles people's decisions.

**What's new.**
- An app store with real versions and access grants.
- In The Agent: the sandboxed frame, a small host page that wraps it, and the bridge.
- A parameterised, read-only SQL route in the main app.
- App Studio.
- A few new tools for The Agent and a product skill.
- In phase 2, a small SDK call that lets a long-running automation publish live progress for apps to show (§6.17).

---

## 2. Decisions

| # | Topic | Outcome |
|---|---|---|
| D1 | Approach (§5) | **B**: sandboxed page + bridge, with logic in Automations. Decided by James, 2026-10-03. |
| D2 | Whose access app data runs under (§6.7) | **The publisher's** (whoever promoted the live version), like a stored procedure. The viewer's identity is passed in for "only mine" filters. The viewer's own items always run as the viewer. Accepted with the plan. |
| D3 | "All users" grants (§6.10) | A warning that lists the app's data sources, plus typed confirmation. No extra admin approval. Accepted with the plan. |
| D4 | Who manages apps in Studio | Every Developer and Admin sees and manages every app in the tenant, with all changes logged. Accepted with the plan. |
| D5 | Storage | The Agent's SQLite, in its own file `data/agent/apps.db`. Accepted with the plan. |
| D6 | Classic mode | Not in the first version. Apps live in The Agent only, and the main app's menu stays untouched, in keeping with additive-only. A link guarded by the setting can be added later if classic-mode users need it. Changed 2026-10-03. |
| D7 | — | Dropped (2026-10-03). This is a general feature, separate from Dayforce. |
| D8 | — | Moved out: not part of this feature. Explained in §11 as a separate, later question about Views. |
| D9 | Names | "Custom Apps" for the menu and "App Studio" for the builder. Accepted with the plan. |
| D10 | — | Not a decision: app building uses the same AI model as The Agent's other chats (§6.14). |
| D11 | Who may run an app's actions | Anyone given the app, unless the action restricts it further by role or group. Accepted with the plan. |
| D12 | Tier | All tiers, for now. Decided by James, 2026-10-03. |

"Accepted with the plan" means James approved the plan as a whole on 2026-10-03 without changing that recommendation.

---

## 3. Requirements traceability

| # | Requirement | Where |
|---|---|---|
| 1 | Create custom screens/mini-apps on the fly, in production | §6, §6.9 |
| 2 | Built via chat with an agent that knows the SDK and architecture, probably The Agent | §6.14 |
| 3 | A separate Developer/Admin interface to create, test and promote, then grant users/groups; All users supported with a warning | §6.9–6.10, §6.13 |
| 4 | Built on The Agent's interface | §6.13 |
| 5 | Developer role and above | §6.13–6.14 (checked on the server) |
| 6 | Easier than forcing a process into the platform, while keeping the platform's benefits | §5, §6.3 (Automations, My Work and connections stay the foundation) |
| 7 | More flexibility in production than Automations, the Automation node and The Agent give today | §5 |
| 8 | Deployed to an isolated area; a Developer or Admin grants access; a "Custom Apps" menu with sub-items | §6 (intro), §6.6, §6.10, §6.13 |
| 9 | Borrow from `aihub-apps` where useful | §9 |
| 10 | Thoughts, suggestions, risks, limitations, approaches | §5, §7, §8, §10 |
| 11 | Additive only; nothing that exists changes behaviour | §4.1 |
| 12 | One on/off setting, on by default; off hides the menu options | §4.1, §6.15 |
| 13 | Commit and push all open changes before starting to code | §4.1, §12 (step 0) |
| 14 | All tiers, for now | §2 (D12) |

Requirements 1–10 are from 2026-10-02; 11–14 are from 2026-10-03.

---

## 4. Goals and non-goals

**Goals**
- A Developer can go from a one-paragraph request to a working, shared screen in one conversation.
- Once the feature has shipped, a new app needs no platform code change, build or reinstall.
- Viewers only see what the app's published version declares. App code can never act with the viewer's sign-in.
- Every publish, grant and action is logged. Any app can be rolled back or switched off immediately.
- Viewing an app does not use AI.
- One setting turns the whole feature on or off.
- Existing features behave exactly as before, whether this feature is on or off.

**Non-goals**
- A general app platform: no custom server code inside apps, and no processes or ports per app.
- Outside or anonymous users, or public pages.
- Replacing Views, My Work, Workflows or Automations.
- Changing how any existing feature behaves: Views, My Work, Automations, connections, or the main app's pages and menu.
- A marketplace or a pipeline for deploying across installs. (Solutions Author export comes later, §6.16.)
- Pages that load anything from the internet, such as CDNs or outside APIs.

### 4.1 Build rules: additive only

1. **Clean baseline first.**
   - Before any code, commit and push all open changes in the working tree: everything, not only this plan.
   - Do the usual checks first: fetch, review what will go out, and scan for secrets (the repo is public).
   - Another session may be partway through a change in the same tree. On 2026-10-03 another agent was reworking Solutions Author, with `static/css/solutions.css` and `templates/solutions_author_list.html` open. In that case, let it finish and commit, or check with James, before committing its files.
   - That gives a pushed point to return to (§12, step 0).
2. **New code goes in new files.** For example:
   - The Agent:
     - `apps_store.py`;
     - `apps_compose.py` (builds the page for the frame);
     - `apps_bridge.py` (the endpoints);
     - `apps_tools.py`;
     - `static/apps.js` and `static/studio.js`;
     - the host page for the frame (§6.6);
     - `product_skills/aihub-custom-apps/`.
   - Main app: a new blueprint file for the parameterised SQL route.
3. **Existing files only get small registration hooks, each skipped when the setting is off:**
   - `agent_service/main.py`: set up the store and mount the new routes.
   - `agent_service/brain.py`: register the tools and add the prompt section.
   - `agent_service/static/index.html`:
     - hidden menu entries and empty screen sections;
     - entries in both the `views` and `navs` tables (`show()` breaks if one is missing);
     - a script include.
   - `app.py`: register the new blueprint.
   - Phase 2 only: the automations runtime gets a new endpoint and SDK function (§6.17). Nothing that already exists there changes.
4. **Nothing that exists changes behaviour.**
   - No changes to existing tables, routes, responses or headers. That includes The Agent page's own headers: the frame's extra protection lives in the new host page (§6.6).
   - No changes to how Views, My Work, Automations or connections work.
   - No start-up code that rewrites existing data.
5. **One setting:** `AGENT_APPS_ENABLED`, on by default (§6.15).
   - When off: the menus are hidden, the new endpoints return 404, and the tools and prompt section are not loaded.
   - Stored apps are kept, and switching the setting back on restores them.
6. **Prove nothing broke.**
   - Run pack 20 and the UI smoke tests before starting, to record the baseline.
   - Run them again after each phase, with the setting both on and off. The results must match the baseline.
   - Commit each working step promptly, separately from unrelated changes.

---

## 5. Approaches considered

| | A. Views v3 (fixed components) | **B. Sandboxed page + bridge** | C. Full-stack mini-apps (`aihub-apps` style) |
|---|---|---|---|
| Flexibility | Low: only what the components can do | High for screens; logic runs as Automations | Highest |
| Safety | Highest | High, as long as the bridge is the only way in or out | Lowest: AI-written server code running with server rights |
| Effect on client machines | None | None, because apps are stored content | A running process and port per app. `aihub-apps` names leftover processes and ports on Windows as its biggest real-world problem |
| How reliably the AI builds it | High (a JSON spec) | High: plain HTML and JavaScript is what models write best | Medium: builds fail, and it allows up to 8 self-repair rounds |
| Size of build | Small | Medium | Large |

James chose **B** on 2026-10-03.
- **A** is close to "do it within the platform's framework", which this feature exists to get around.
- **C** puts a second platform on every client machine, and its server code would run without a sandbox.
- **B** puts the flexibility where it is cheap and safe (the screen). Data, writes and code stay behind parts of the platform that are already controlled.

---

## 6. Design

**What "isolated" means here**
1. Apps have their own storage (`apps.db`) and never touch platform code.
2. Each app runs in a sandboxed frame: no cookies, no storage, no sign-in token, no network.
3. An app can only reach data and actions through the bridge, and only the ones its published version declares.
4. A broken app can't break The Agent, and any app can be switched off instantly.
5. Turning the setting off hides the feature completely and leaves everything else exactly as it was.

### 6.1 Vocabulary

| Term | Meaning |
|---|---|
| App | A named screen: a page, any small files it carries, and a manifest, with an owner, a status and access grants |
| Draft | The app's working copy. Only Developers and Admins can open it |
| Version | A promoted copy of a draft that never changes (v1, v2, …). Viewers always see the published version |
| Source | A named, declared data read (`sql`, `automation_status`, …) |
| Action | A named, declared thing the page may trigger. In v1 this means running a promoted Automation |
| Grant | Access to an app for a user, a group, or all users |
| Publisher | The person who promoted the published version. Declared sources run with their access (D2) |
| Bridge | The one server entry point every app request goes through, plus the SDK in the page that talks to it |
| Host page | A small page of our own, on The Agent's origin, that wraps the app frame and runs the bridge (§6.6) |
| App Studio | The Developer/Admin screen for building, testing, promoting and sharing apps |

### 6.2 What an app is

- `index.html`: the page. Plain HTML, CSS and JavaScript, with no build step.
- Optional small files: JavaScript, CSS or images the app carries. They are inlined into the page when it is served.
- `manifest.json`: what the page may read and do. The example below is illustrative; its names are made up.

```json
{
  "sdk": 1,
  "name": "Overdue invoices",
  "description": "Open invoices past their due date, with a button to send reminders.",
  "libs": ["chart"],
  "sources": {
    "overdue":  { "kind": "sql", "connection": "ERPDB",
                  "sql": "SELECT invoice_id, customer, due_date, amount FROM dbo.Invoices WHERE status = 'Open' AND due_date < ?",
                  "args": ["as_of"], "params": { "as_of": "date" }, "page_size": 200, "ttl": 60 },
    "last_run": { "kind": "automation_status", "automation": "send-dunning-reminders", "ttl": 15 },
    "my_items": { "kind": "my_work", "automation": "send-dunning-reminders" }
  },
  "actions": {
    "send_reminders": { "kind": "run_automation", "automation": "send-dunning-reminders",
                        "inputs": { "customer": "string" }, "who": { "min_role": 2 },
                        "confirm": "Send reminder emails to this customer now?" }
  }
}
```

While the app runs, the page never names a connection, a table or an automation. It only uses the source and action names from its manifest.

### 6.3 Source kinds

| Kind | Returns | Runs as | Default reuse window | Phase |
|---|---|---|---|---|
| `sql` | `{columns, rows, row_count, truncated}`, with typed values | Publisher | 30 s | 1 |
| `automation_status` | The latest run (status, start/finish times, version, trigger), runs skipped since it started, and the last finished run | Publisher | 10 s | 1 |
| `review_items` | For one automation (current run or a recent window): counts by status, the oldest pending item, counts per group. Row fields only if declared | Publisher | 15 s | 1 |
| `my_work` | The viewer's own open items for an automation or kind (count, titles, how long they've waited) | Viewer | Not shared | 1 |
| `automation_output` | The last JSON line printed by a promoted automation (the same convention Views uses) | Publisher | 300 s | 1 |
| `automation_published` | The latest progress an automation published with `aihub.publish_status` (§6.17) | Publisher | 5 s | 2 |
| `app_state` | The app's own small records, such as notes or checklists | Viewer or shared | — | 2 |

Notes:
- **`sql`** must be a single `SELECT` with `?` placeholders.
  - `args` maps the placeholders, in order, to declared params or to viewer values the server fills in (`@viewer_user_id`, `@viewer_username`).
  - Values are always passed as parameters, never pasted into the SQL.
- **`automation_output`** starts a real run on every refresh, which means a run row and a run folder each time. Automations also run one at a time.
  - Use it for data that changes over minutes or hours, not for live status.
  - The data automation must be a separate small automation, never a long-running job itself. Otherwise its runs are simply skipped.
- **Sources about the viewer's own data** (`my_work`) always run as the viewer, and their results are never shared between viewers.

### 6.4 Action kinds

**`run_automation` (phase 1)**
- Only the promoted version can run.
- Only declared inputs are accepted, and their types are checked. `fixed_inputs` are set in the manifest, not by the page.
- `who` can restrict the action further, by role or group.
- `confirm` is text the host shows before running.
- It returns `{run_id}`. The page then polls with `aihub.run(run_id)`, which only answers for runs this app started for this viewer.
- The run is recorded with the viewer as the person who ran it.

**Later:** raise a My Work item to a declared user or group (non-reserved kinds only); send email through a My Work approval; write to `app_state`.

### 6.5 The SDK inside the page

The host page injects this into every app:

| Call | What it does |
|---|---|
| `aihub.ready` | A promise that resolves once the bridge is connected |
| `aihub.data(source, params?)` | Reads a declared source |
| `aihub.action(name, inputs?)` | Runs a declared action (the host shows `confirm` first) |
| `aihub.run(run_id)` | Status of a run this app started |
| `aihub.me()` | `{name, role_label, timezone}`, for display only, with no ids |
| `aihub.open(target)` | Navigation done by The Agent: `"my-work"`, `{work_item}`, `{app}`, `{view}` |
| `aihub.openLink(url)` | The host opens an http(s) link in a new tab |
| `aihub.download(name, data, type)` | The host builds the file and downloads it |
| `aihub.toast(text)` | A notification shown by the host |
| `aihub.every(seconds, fn)` | A refresh loop that pauses while the app is hidden |
| `aihub.ui.*` | Kit helpers: table (sort, filter, CSV), chart (Chart.js), KPI, badge, progress bar, times in the viewer's time zone |

- **Errors are captured automatically** (`window.onerror`, unhandled promise rejections, `console.error`) and shown in Studio's Activity tab.
- **A design kit is injected too:** CSS that matches The Agent, in light and dark. Apps look native without the model writing much CSS.

### 6.6 How a page runs: host page, sandbox and bridge

```
The Agent page (unchanged: no new headers)
  └─ host page (new; same origin as The Agent; runs the bridge; carries its own content policy)
        └─ app frame (sandboxed srcdoc: opaque origin, no cookies, no storage, no token, strict policy)
              └─ private MessageChannel port ─▶ host page
                    └─ POST /api/apps/<slug>/data/<source>      (Bearer = the viewer)
                          server: viewer granted? → published manifest (never the page's word)
                                  → type-check params → fill viewer values → shared cache / one execution at a time
                                  → run the declared source with the right identity → typed rows
```

**Why a separate host page.**
- It keeps The Agent page itself unchanged: no new headers or policies.
- The host page is served by a new route, so it can send its own headers.
- It shows up in two places: the app screen, and Studio's preview.

**The frame**
- `sandbox="allow-scripts allow-forms allow-modals"`. Never `allow-same-origin`, `allow-top-navigation*` or `allow-popups*`.
  - Downloads go through the host (`aihub.download`), so there is no `allow-downloads`.
  - `allow-forms` is needed because without it, browsers drop the form's submit event. Ordinary AI-written forms would then silently do nothing. The content policy below still blocks any real submission.
- **How the page is served:**
  - The server composes the page (`GET /api/apps/<slug>/render`).
  - The host page fetches it with the viewer's token and loads it as `srcdoc`, so no token appears in any URL.
- **Content-Security-Policy:** the composer writes a CSP `<meta>` tag as the first element of `<head>`, before any app content:
  ```
  default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:;
  font-src data:; media-src data: blob:; connect-src 'none'; form-action 'none'; base-uri 'none';
  frame-src 'none'; worker-src 'none'
  ```
  - The app's own files and the bundled libraries listed in `libs` are inlined.
  - There is no `'unsafe-eval'`, so libraries that need `eval` or `new Function` won't run.
- **Blocking self-navigation:** an app could send data out by navigating its own frame to an outside address, and the frame's own policy can't stop that.
  - The host page's own `frame-src` rule should block it.
  - The prototype must confirm two things: that `srcdoc` frames still load under that rule, and that each target browser blocks self-navigation.
  - Fallback, if a browser doesn't block it: the host notices the frame navigating away, removes it and logs a security event. That is detection, not prevention.
- **Remaining channels:** some browsers have channels these policies don't cover, mainly WebRTC and DNS prefetch hints.
  - They carry little data and need deliberately hostile code.
  - The static checks (§6.14) and the review of changes at promote flag them.
  - The prototype records what each target browser actually does.

**The bridge, in the host page**
- The host page is on The Agent's origin, so it can read the sign-in token. The app frame cannot.
- When the frame loads, the host creates a `MessageChannel` and hands the frame one port. The host listens only on its own port, so nothing else can talk to the bridge.
- There is a fixed set of operations; anything else is ignored.
- The host adds the app name and the mode (`published` or `draft`) itself. The page cannot ask for draft mode.
- The host never passes tokens, API keys, server paths or platform URLs into the frame.
- Talking to The Agent page:
  - The Agent page only tells the host page which app to show.
  - The host page asks The Agent page to navigate, for example to My Work.

**The bridge, on the server.** Every call goes through these checks in order:
1. The viewer is signed in.
2. The app exists and is enabled.
3. The mode is allowed (draft is Developer and above).
4. The viewer has been given the app (published mode).
5. The source or action is declared in that version.
6. Parameters are type-checked; unknown parameters are refused.
7. Viewer values are filled in.
8. The cache is checked.
9. The source runs under the right identity.
10. Usage counters are updated.

### 6.7 Whose access

**Agreed (D2): declared sources run with the publisher's access.**
- **Why not the viewer's own access:** it would give away more than the app shows.
  - A regular user can only reach a connection when a Data Assistant on it is shared with their group (`connection_acl.accessible_connection_ids`).
  - That sharing opens the whole connection to them through The Agent's data tools.
  - With the publisher's access, giving a group the app gives them exactly the declared results and nothing more.
- **"Only mine" screens** still work: the server fills the viewer's identity into queries (`@viewer_user_id`, `@viewer_username`).
- **The viewer's own items and decisions** (`my_work`, deciding an item) always run as the viewer.
- **Who the publisher is:** the person who promoted the live version.
  - The bridge re-checks them on every call (cached briefly).
  - If their account is inactive or below Developer, sources stop working and say "this app needs to be re-published". Studio flags the app.
- **This follows an existing pattern:** The Agent already signs identity assertions for a stored person; Views' scheduled refresh runs as the view's creator.

### 6.8 Caching, load and freshness

- **One query serves everyone.**
  - Results are cached per app, version, source and parameters (including viewer values) for the source's `ttl`.
  - Identical requests that arrive together share one execution.
  - With the publisher's access, results are the same for every viewer unless viewer values are used. Example: 50 people watching a screen that refreshes every 15 s cost about four queries a minute per source.
- **`ttl` is a freshness setting, not a cap.**
  - Each source chooses its own.
  - Studio shows what it costs ("this source runs about N times a day"), so the developer can choose sensibly. This matters most for `automation_output`.
- **Large results:**
  - `sql` sources declare a `page_size` and accept an offset. Results say `truncated: true` when there is more.
  - The only hard limit is the platform's existing row ceiling (`SQL_QUERY_ROW_SAFETY_CAP`, `config.py:510`). Hitting it is reported, never silent.

### 6.9 Lifecycle

States: `draft` → `published` → `disabled` / `archived` → `deleted` (soft delete; history is kept).

- **Saving** only changes the draft. Viewers keep seeing the published version, so a developer can work on a live app safely.
- **Promote** creates the next version, which never changes, and records the publisher and a note. It requires:
  1. The manifest checks out at that moment: connections resolve, automations exist and are promoted, and each SQL source passes the read-only check and test-runs.
  2. A clean preview report for this exact draft (matched by a hash of its content). This is the same rule as "dry-run before promote" for Automations.
  3. Explicit confirmation.
- **New data in a new version:** if the new version reads data the old one didn't, the promote screen lists the new sources. For an All-users app it shows the warning again.
- **Rollback** promotes an old version again, under a new version number, so history stays in one line and can be audited.
- **Disable** takes effect on the next request. Viewers see "temporarily unavailable".
- **Dependencies:**
  - Studio flags apps whose automation or connection was renamed or deleted.
  - The automatic cleanup of one-off automations should also skip automations used by app sources. It already skips those pinned to View tiles.
  - That is a small change to existing cleanup code, so it gets the same care as the hooks in §4.1, and the cleanup must still work exactly as before when the setting is off.

### 6.10 Sharing

- **Who and what:** grants go to users, groups or all users, and belong to the app, not to a version.
- **Who can change them:** any Developer or Admin (D4). The pickers show names only, never email or phone.
- **New apps start private.** (`aihub-apps` has a bug where an app with no access rows is open to everyone; avoid it.)
- **All users** shows this warning and then needs typed confirmation (D3):
  > Every signed-in AI Hub user — including End Users — will be able to open this app and see what it shows. This app reads: • ERPDB query "overdue" • run status of automation "send-dunning-reminders". Sharing with a group is usually better. Type ALL USERS to confirm.
- **Revoking** takes effect on the viewer's next request.
- **What each person sees:**
  - In the menu, viewers see published apps given to them directly, through a group, or to all users.
  - Developers also see the apps they own in the menu.
  - Studio shows every app.

### 6.11 Storage: `data/agent/apps.db`

| Table | Key columns |
|---|---|
| `apps` | app_id, slug (unique), name, description, icon, owner_user, tenant_id, status, published_version, created / updated / last-opened / archived / deleted times |
| `app_drafts` | app_id, html, assets_json, manifest_json, content_hash, updated_by / at, the last preview report with its hash and time |
| `app_versions` | (app_id, version), html, assets_json, manifest_json, content_hash, published_by, published_at, note, origin (promote / rollback / import) |
| `app_grants` | (app_id, subject_type user / group / all, subject_id), granted_by, granted_at |
| `app_events` | seq, app_id, at, actor, kind (create, save, preview, promote, rollback, grant, revoke, status, action_run, error), detail_json |
| `app_usage` | (app_id, day), opens, data_calls, errors |
| `app_state` (phase 2) | app_id, collection, doc_id, scope (shared / viewer), data_json, updated_by / at |

- It is a new file, so existing databases are not touched.
- The tables are created at startup, the same way the Views table is (`views_store.init()`, `agent_service/main.py:43`), and only when the setting is on.
- Make schema changes inside explicit transactions. `sqlite3.executescript()` commits on its own, which was learned the hard way in Views v2.
- `scripts/build_agent_service.ps1` already leaves `*.db` files out of builds (lines 32-36), so each install keeps its own apps.
- Add `apps.db` to the backup guidance, next to `mywork.db`.

### 6.12 Server endpoints

**The Agent (`agent_service`).** All of these are new. They are mounted only when the setting is on; otherwise they return 404. All use the Bearer token unless noted.

| Endpoint | Who | Purpose |
|---|---|---|
| `GET /api/apps/config` | Signed in | `{enabled, can_build}`. The menus appear only when this answers |
| `GET /apps/host` | Signed in (the page's own calls carry the token) | The host page for the frame (§6.6) |
| `GET /api/apps` | Signed in | Apps I can open (the menu) |
| `GET /api/apps/{slug}/render?mode=` | Given the app; draft = Developer+ | The composed page for the frame |
| `POST /api/apps/{slug}/data/{source}` | Given the app; draft = Developer+ | Read a source |
| `POST /api/apps/{slug}/action/{name}` | Given the app, plus the action's `who` | Run an action |
| `GET /api/apps/{slug}/runs/{run_id}` | The viewer who started the run | Run status |
| `POST /api/apps/{slug}/telemetry` | Signed in | Errors and preview reports |
| `GET/POST /api/studio/apps…` | Developer+ | List, create, get/put the draft, promote, roll back, versions, diff, grants, status, delete (two-step), activity |
| `POST /api/studio/apps/{slug}/edit-chat` | Developer+, checked at the endpoint | The per-app AI edit chat |
| `GET /api/studio/directory` | Developer+ | Groups and users for the grant picker (ids and names only) |
| `GET/POST /api/apps/internal/export\|import` | Service key | Solutions Author (phase 3) |

**Main app.** All additions; nothing existing changes.

**A parameterised, read-only SQL route for apps.**
- The name is still to settle, e.g. `POST /api/apps-data/sql/<connection_id>`. It lives in a new blueprint file.
- **Why the existing route won't do:** `/api/discover/query` (`app.py:17629`) is The Agent's own probing tool. It:
  - takes raw SQL only, with no parameters;
  - is capped at 50 rows, 25 columns and 200 characters per cell (`config.py:519-521`);
  - turns every value into text.
- **What the new route needs:**
  - A service key **plus a required `X-AIHub-User` identity header**, refusing calls without it. Today a key on its own is fully trusted.
  - The same `sql_gate` check that allows only a single SELECT.
  - Parameters passed separately from the SQL.
  - Typed JSON values.
  - Offset and page size taken from the request, under the platform's row ceiling.
  - The connection access check (`_connection_access_refusal`).
  - A 404 when the setting is off. Both services read the same `.env`.

**Other main-app additions**
- **Run status for `automation_status`:** reuse `internal/manage` (`runs` / `active`) with the publisher's identity, as it is. If that turns out awkward, add a small new read-only internal route rather than changing the existing one.
- **Phase 2:** `aihub.publish_status` (§6.17).
- **User and group names for the grant picker:** if the existing directory operation returns contact details, add a new names-only operation next to it. Don't change the existing one.
- **The main app's menu is not changed** in the first version (D6).

### 6.13 The Agent's interface

**How it works today**
- The left menu is seven fixed buttons (`agent_service/static/index.html:401-416`).
- Screens switch through two lookup tables, `views` and `navs` (lines 1258-1271), and `show()` (line 1272). A screen missing from either table makes `show()` throw an error.
- The `#…` part of the address is read only at startup (`viewFromHash`, line 1677).
- The only grouped links are in the Platform pop-out (`PLATFORM_GROUPS`, lines 1548-1607).
- `index.html` is 3,977 lines of inline CSS and JavaScript.

**What to add.** Everything below is hidden until `GET /api/apps/config` answers. If the setting is off, or the new scripts fail to load, The Agent looks exactly as it does today.
- **Left menu:**
  - a "Custom Apps" entry that expands to the viewer's apps. This is new: the menu has no sub-items today.
  - "App Studio", shown to Developers and above.
- **App screen:**
  - A header drawn by The Agent above the host page: name, version, publisher, last updated, refresh, pop-out, report a problem.
  - The header sits outside the frame, so an app can't fake it.
- **Direct links and full-window mode:**
  - `#app=<slug>` and `#studio=<slug>`.
  - A `hashchange` listener in the new `apps.js`, so these work when the address changes, not just at startup. The existing start-up code is not changed.
  - `?app=<slug>&solo=1` opens one app full-window, for example on a wall screen.
- **App Studio:**
  - a list of apps with status, owner, last opened and health;
  - per app, these tabs:

    | Tab | Contents |
    |---|---|
    | Preview | The live draft, reloading on save. Optionally "preview as" a chosen user, which fills in that user's viewer values (logged) |
    | Chat | The edit chat |
    | Data | Sources and actions, each with a test button |
    | Code | The page and manifest, read-only for now |
    | Versions | List, diff, promote, roll back |
    | Access | Grants and the All-users warning |
    | Activity | Logged changes, errors, usage |
- **Previews while building from the main chat:**
  - The agent replies with a link that opens the draft in Studio's Preview tab (`#studio=<slug>`).
  - A preview shown directly inside the chat message is phase 2. It needs a new case in the chat's shared message renderer, so it waits until the core is proven.
- **Where the code goes:**
  - This interface goes in new files (`static/apps.js`, `static/studio.js`, and the host page). `index.html` only gets the hooks listed in §4.1.
  - Still no build step, and only bundled libraries.

### 6.14 Building apps with The Agent

The Agent is the right builder. It already:
- lists connections and reads real table structures;
- test-runs queries;
- writes, dry-runs and promotes automations;
- raises My Work items;
- learns the platform from product skills.

**Tools.** All are Developer and above, checked inside the tool code. They are loaded only when the setting is on.

| Tool | What it does | Changes anything? |
|---|---|---|
| `list_apps` | Apps, their status and sources | No |
| `get_app` | The draft and published page and manifest, the last preview report, grants | No |
| `save_app_draft` | Creates or updates the draft. Checks the manifest, runs the static checks, and test-runs each source with sample values. Returns a report and a link to the preview | Yes |
| `check_app` | The latest preview report for the current draft (waits briefly for the preview to report in) | No |
| `promote_app` | Two-step. Refuses without a clean preview of the current draft | Yes |
| `share_app` | Adds or removes grants. All users is two-step, with the warning | Yes |
| `set_app_status` / `delete_app` | Disable, enable, archive. Delete is two-step | Yes |

**Registering the tools in `brain.py`.** Do it all in one change, or the lists drift apart. Every addition is skipped when the setting is off:
- the imports and the tool list (`brain.py:25-40`, `169-182`);
- `MUTATING_TOOLS` (189-212) and the nouns the false-claim check looks for (`MUTATION_CLAIM_RE`, 224-231): add "app";
- `_READ_TOOL_NAMES` (241-262);
- a "CUSTOM APPS" section in `SYSTEM_PROMPT`, written in terms of what The Agent can do. Keep the size budget for the prompt file in mind (771-795);
- the tool-list drift test (`tests_v2/unit/test_agent_brain_tool_lists.py`) and the tool smoke list in pack 20 (`test_human/20_The_Agent/tool_smoke_checks.py:134`).

**Known traps from earlier work**
- When a tool's description and a skill disagree, the model follows the tool description. Update both together.
- Never put a helper function between `@tool(...)` and its function. It compiles but breaks at import.
- `py_compile` passing doesn't prove the module imports.

**Which AI model builds apps (was D10).**
- The Agent's chats run on one Claude model, set by `AGENT_MODEL` or the admin's model setting. End Users get a cheaper model by default, but only Developers build apps.
- App building uses that same model; there is no new setting.
- If the screens it builds turn out weak, a later setting could send app building, and only app building, to a stronger and more expensive model.

**Product skill** (`agent_service/product_skills/aihub-custom-apps/SKILL.md`; product skills are mounted only when the setting is on):
1. When to build an app, a View, an Automation or a My Work item.
2. The page and manifest, with examples.
3. The SDK and design kit, with snippets to copy.
4. Recipes:
   - an automation status screen;
   - a filterable table over a query;
   - a form that runs an automation and shows the result;
   - a KPI header with a chart.
5. Rules:
   - Read the real table structure first (`get_connection_schema`, `probe_connection_query`).
   - Declare sources; never fetch.
   - Never fake data. An empty or failing source says so honestly.
   - Show times in the viewer's time zone.
   - No outside URLs and no form posts.
   - Refresh no faster than the source's `ttl`.
   - Show as little personal data as possible.
   - Ask before promoting or sharing.
6. The loop: save → check → fix → promote (after the user confirms) → share (after the user confirms). If the same errors come back twice, stop and ask.

**Edit chat**
- A new endpoint modelled on Views' edit chat (`agent_service/main.py:1003-1052`), which itself is not changed. The first message carries the current draft and the editing rules, and there is one chat session per app.
- Unlike Views, it checks for Developer and above **at the endpoint**. Views' edit chat relies only on the store's permission checks.

**Checks**
- **Static (on the server, when saving):**
  - the manifest's format;
  - that sources resolve and test-run;
  - that the page has no outside URLs (`src`, `href`, CSS `url()`, `import`);
  - no `<form action>`, `<base>` or `<meta http-equiv=refresh>`;
  - password fields, WebRTC and DNS-prefetch hints are flagged;
  - libraries are ones the platform knows.
- **In the browser (phase 1):**
  - Studio's preview renders the draft.
  - It reports errors, failed bridge calls, and a blank screen (nothing visible once the sources have answered), tagged with the draft's content hash.
  - `check_app` returns this report, and promote requires a clean one. If no preview is open, `check_app` replies with the preview link.
- **Without a browser (phase 2):**
  - The server renders the app and takes a screenshot the agent can look at.
  - Playwright is only in the `aihub2.1` development environment today, so this needs packaging, for example using the browser the Browser Use service already ships.

### 6.15 Operations

**The setting: `AGENT_APPS_ENABLED`**
- It lives in `.env`, like The Agent's other feature switches, and is **on by default**.
- Off:
  - Custom Apps and App Studio disappear from The Agent's menu;
  - the building tools, prompt section and skill aren't loaded;
  - every new endpoint returns 404, in The Agent and in the main app;
  - stored apps are kept untouched, and turning it back on restores them.
- Changing it needs a restart of The Agent, and of the main app for the main app's new route, like the other switches.
- **Per-app disable** takes one app offline without touching the setting.

**Logs and health**
- `[apps]` lines go to `logs/agent_service_log.txt` for create, save, promote, grant, action and errors. They never contain row data.
- `/health` is unchanged. Feature status and app counts are available from `GET /api/apps/config` (and Studio).

**Restarts**
- Changes to the static interface files show up on a page reload.
- Python changes need the usual restart of just The Agent (port 5111).
- The main-app route needs the main app (port 5001) restarted.

**Installer**
- No new service is needed.
- The Agent's build copies its whole source folder, so new files ship automatically.
- The main-app route ships with the main app build.

**Access for End Users**
- People must be able to reach The Agent to open apps. On an install where End Users can't use The Agent (`AGENT_ALLOW_ALL_USERS=false`), they can't open apps either. Current installers turn that setting on.
- An "apps-only" mode for End Users is possible later.

**Upgrades**
- Each manifest records `"sdk": 1`, and the bridge keeps old operations working.
- In phase 2, a compatibility check runs every stored app's sources after an upgrade.

### 6.16 Solutions Author (phase 3)

- **Export:** add an `apps` kind. It exports the published version, referring to connections and automations by name only, as workflows already do for automations.
- **Grants are not exported**, because group ids differ between installs.
- **Install:** the app arrives as a draft that must be previewed and promoted on the target. This matches how automations install unpromoted.
- **Connection between the two:** Solutions Author lives in the main app and apps live in The Agent's database, so it uses the export/import endpoints protected by the service key.
- **Effort:** adding a kind touches five places in Solutions Author: the list of kinds, the bundler, the picker, the installer and the wizard. Each needs the same care as the hooks in §4.1.
- **Timing:** another agent was changing Solutions Author on 2026-10-03.
  - Start this phase only after that work has landed.
  - Re-check Solutions Author's structure first; the five places above may have moved.

### 6.17 Live progress from automations (phase 2)

**Why.**
- A run's status says `running` for as long as the run lasts. For a long-running automation, that says nothing about how far along it is.
- Today the only finer-grained signals are free-text files or log lines, and an app should not parse those.
- So: give automations a small, structured way to publish progress.

**SDK:** `aihub.publish_status(data, key="status") -> bool`.
- It never raises; on failure it returns False, like `send_email`.
- A run never fails because of a status update.
- Scripts should guard the call with `hasattr(aihub, "publish_status")`, so they still run on platforms without it.

**Endpoint:** `POST /automations/api/runtime/publish_status` (new).
- It uses the run's own token, like `review_item`.
- It writes `automations/tenant_*/_status/<automation_id>/<key>.json` atomically, with the data, the time (UTC), the run id, the automation id and name, and the version.

**Reading it back:** a new internal route (service key plus a required user identity).
- It returns the data, how old it is, and whether the run that published it is still alive (checked from the run row and its heartbeat file).
- If that run has died, the `automation_published` source reports `stale: true`, so a crash doesn't leave a screen showing "in progress" forever.

**Additive.** Existing automations are unaffected; only scripts that call it publish anything. Mission Control could show it too, later.

---

## 7. Security model

### 7.1 Threats and how they're handled

| Threat | Why it's real here | How it's handled |
|---|---|---|
| App code takes over the viewer's account | The Agent keeps its token in `localStorage` (`index.html:683`). Neither service sends a Content-Security-Policy. Both accept requests from any website (`agent_service/main.py:77-81`, `app.py:387`) | Never run app code on The Agent's own origin. The sandbox has no `allow-same-origin`. The bridge runs in the host page, over a private port |
| App sends data out | A built app could be careless, or follow instructions planted in data | The frame's policy blocks fetch, XHR, WebSockets, images, scripts and forms. The host page's `frame-src` blocks self-navigation. Remaining channels are flagged by static checks and the promote review |
| Viewer tampers with requests | The page runs in their browser | The server runs only the published manifest. Values are typed and passed as parameters; unknown parameters are refused |
| Writes or SQL injection | `aihub.query` commits statements that aren't SELECTs | Single-SELECT check at promote and on every call; parameterised values; writes only through promoted Automations |
| Sharing too much | Publisher's access and All users are the point, so they must be deliberate | Only declared sources. The share screen lists them. Warning plus confirmation. New sources are flagged at promote. Grants are logged |
| Planted instructions in data while building | The test data has one on purpose (ERPDB CollectionActivity #1053) | No outbound channel; review of changes at promote; static checks; a test that builds an app against that record |
| Fake sign-in screens inside the product | Users trust anything inside AI Hub | The header is drawn by The Agent; forms can't post anywhere; password fields are flagged |
| Drafts leaking | Drafts can contain half-finished queries | Draft pages and data are Developer and above only; the page can't choose draft mode |
| Publisher leaves or is demoted | Sources run with their access | Checked on every call; sources stop working; Studio flags it |
| Too much load | Many viewers refreshing; automations run one at a time | Shared cache and one execution at a time; `ttl` per source; live progress from `publish_status` instead of starting runs |

### 7.2 Sandbox tests

These are the exit criteria for the prototype, and then a permanent smoke test. From inside a deliberately hostile test app:
1. `localStorage.getItem("agent_token")` is blocked (SecurityError).
2. `document.cookie` is blocked.
3. Reading `parent.document` or `top.location.href` is blocked.
4. `fetch`, XHR, WebSocket and EventSource to :5111, :5001 and an outside host are all blocked (a policy-violation event fires).
5. `new Image().src = "http://…"` is blocked.
6. `location.href = "https://example.com/?d=1"` is blocked by the host page's `frame-src`.
7. `window.open`, `<a target=_blank>` and `top.location = …` are blocked.
8. A form posting to an outside host is blocked.
9. `parent.postMessage(...)` pretending to be the bridge is ignored.
10. Bridge checks:
    - an undeclared source gets 404;
    - a wrong parameter type gets 400;
    - draft mode as an End User gets 403;
    - a viewer without access gets 404;
    - after access is revoked mid-session, the next call gets 404.
11. WebRTC and DNS prefetch: record each browser's behaviour. These are remaining channels, flagged by the static checks.

### 7.3 Existing holes that apps must not make worse

- `/document/serve` still returns any file path to any signed-in user. It is tracked separately.
- The access-check middleware only logs on this machine (`AUTH_MIDDLEWARE_DRY_RUN=true` in `.env`).
- API keys are accepted in the URL (`role_decorators.py:297`), and a key on its own is fully trusted.

So the bridge never gives an app a key, a direct platform URL or a server path, and the new main-app SQL route refuses calls without a user identity. Fixing these holes is separate work; this feature does not change them.

### 7.4 Personal data

- Apps should show the minimum: counts, phases, numbers of files. Details belong in My Work, which already controls who sees what.
- `review_items` returns counts unless specific fields are declared. Studio warns when the declared fields include titles or descriptions.
- Bridge logs never contain row data.

---

## 8. Limitations to accept

- **No custom server code in apps.** Logic lives in Automations, which take seconds to start and run one at a time. That's fine for buttons, not for rapid back-and-forth.
- **Screens refresh by checking periodically.** Pushed updates are a later phase.
- **Only libraries bundled with the platform.** Nothing loads from the internet, which also suits offline installs.
  - Apps can carry their own JavaScript and CSS files.
  - Libraries that need `eval` won't run.
- **Apps only appear inside The Agent**, and they are down when it is. They need no AI to view, so there is no token cost.
- **Sign-in is required;** there are no outside users.
- **Buttons in a preview do real things.** An automation run from a preview really runs. The preview marks actions as live and asks first.
- **Changing the setting needs a restart of The Agent,** like its other switches.
- **One platform release is needed for the feature.** After that, apps are stored content, but future upgrades must keep the SDK working the same way.

---

## 9. From `aihub-apps`: what to borrow and what to skip

**What `aihub-apps` ("EveriApp") is:** a separate, full app builder.
- The backend is FastAPI with SQLite.
- Generated apps are React, Vite and Tailwind, each with its own SQLite database.
- Each chat turn is one streamed LLM call that writes whole files.
- A checking loop (TypeScript, build, start-up check, optional Playwright) sends structured errors back for up to 8 repair rounds.
- It has draft and published versions with rollback, and permission rows per user and group.

**Borrow**
- **Explicit data bindings:** give the model the real columns of the sources the app is bound to, and say so plainly when nothing is bound.
- **"Guide the user, don't fake it":** no convincing screens over invented data. Their AI once invented a customer's database structure and fake fallback data.
- **Structured errors sent back to the model:** stop when the same errors repeat, or when the problem is configuration rather than code.
- **Versions:** drafts separate from versions that never change; differences between versions; rollback saved as a new version.
- **Access:** per-user and per-group permission rows, with logging and an optional publish approval.
- **AI decisions:** named server-side AI decisions with fallback values (later).
- **Mockups:** show a mockup in chat first, then "make this real".
- **Debugging:** a record of each build turn with the exact prompts.

**Skip**
- A development server, a port and a `node_modules` folder per app, and TypeScript/Vite builds on every turn.
- Python server functions running in child processes.
- The deployment agent, SSH, blue/green deploys and the marketplace.
- Apps that are open by default.
- Its preview frame: it isn't sandboxed, it shares an origin with its API, and the token is injected into the page.

---

## 10. Delivery risks

| Risk | How to reduce it |
|---|---|
| Breaking something that works today | The build rules in §4.1: a pushed baseline before coding, new files, hooks that are skipped when the setting is off, and pack 20 + UI smoke tests matched against the baseline with the setting on and off |
| The quality of screens the model builds varies | Design kit and templates; browser preview checks; the promote gate; screenshots without a browser in phase 2 |
| `index.html` is one large file that every screen depends on | New interface in separate files; `index.html` gets only the hooks in §4.1; the chat loop and message renderer are untouched in phase 1 |
| Apps pile up the way one-off automations did | Owner, last-opened date, archive; health in Studio; warnings before deleting something an app depends on |
| Apps depend on the format of automation output | Declared sources and health checks in Studio; `publish_status` gives a stable format instead of parsing text |
| Upgrades break apps | Each app records its SDK version; a compatibility check after upgrades |
| The feature grows into a general app platform | The non-goals in §4; logic stays in Automations |

---

## 11. Things noticed during the survey (not part of this feature)

These are recorded for later. Nothing here is changed by this project.

**Views shows cached data behind "access denied" (was D8).**
- **What happens today:**
  - A Developer builds a View with a SQL tile on, say, ERPDB, and shares it with a group.
  - An End User in that group opens it. End Users may only query connections that a Data Assistant shared with their group gives them.
  - If ERPDB isn't one of those, their live query is refused, and the tile shows a red "access denied" message.
  - Directly under it, the tile shows the rows from the last time someone allowed to query ERPDB refreshed the View (the Developer, or a scheduled refresh), stamped "as of <time>".
  - So the End User does see the data, just not live, under a message saying they can't (`agent_service/views_tools.py:77-80` and `275-284`; `static/index.html:1889-1896`).
- **Why it matters:** it's the same question as D2 for apps: does sharing something let people see the data it shows?
  - For automation tiles, Views already answers yes on purpose: End Users see the creator's last results.
  - For SQL tiles it happens by accident, since the connection access rules arrived (2026-09-22).
  - Either answer can be right, but a screen shouldn't say "access denied" while showing the data.
- **Options, if you want it addressed later:**
  - **(a)** Treat sharing as permission: run shared SQL tiles with the creator's access, so they are live, and drop the message.
  - **(b)** Treat it as a leak: don't show cached rows to someone refused live access.

**My Work reads review items from every tenant's folder.**
- `agent_service/readthrough.py` searches `automations/tenant_*/_approvals` (lines 281 and 364).
- That is harmless on installs with one tenant.
- The new `review_items` source must use the automation manager's own tenant folder instead.

**Views' edit chat lets anyone who can see the view run full-tool turns.** Permissions are only checked in the store. The apps edit chat checks for Developer and above at the endpoint instead.

**The query route Views' SQL tiles use was built for The Agent's own probing:** 50 rows, 25 columns, 200 characters per cell, every value as text. That is why apps get their own read route (§6.12).

---

## 12. Phases

**Step 0 — before any code**
- Commit and push all open changes in the working tree: everything, not only this plan.
- Do the usual checks first: fetch, review what will go out, scan for secrets (the repo is public).
- If another session is partway through a change (see §4.1), let it finish and commit, or check with James, before committing its files.
- Run pack 20 and the UI smoke tests, and keep the results as the baseline.

**Phase 0 — Prototype** (a day or two)
- **Delivers:**
  - the setting, the host page, the sandboxed frame, the bridge and the page composer;
  - `sql` (through the new parameterised route) and `automation_status` sources;
  - a hand-written sample screen on the dev tenant (e.g. overdue invoices on ERPDB).
- **Done when:**
  - §7.2 tests 1–10 pass in Edge and Chrome (Firefox if available), and test 11 is recorded;
  - an End User test account sees the sample only once given access;
  - cached reads are fast;
  - with the setting off, The Agent looks and behaves exactly as before.

**Phase 1 — First version** (medium: bigger than Views v2, smaller than Automations)
- **Delivers:**
  - the `apps.db` store, versions, grants and logging;
  - App Studio;
  - the menu, the app screen, direct links and full-window mode;
  - sources `sql`, `automation_status`, `review_items`, `my_work` and `automation_output`, plus the `run_automation` action;
  - the SDK, the design kit, and 3–4 templates;
  - The Agent's tools, skill, prompt section and edit chat;
  - static and browser checks, the promote gate, and the All-users warning;
  - tests.
- **Done when, tested live:**
  - A Developer builds a sample screen from a one-paragraph request, for example "overdue invoices with a button to send reminders". The preview is clean, they promote it and give it to a group, and a group member opens it.
  - Someone outside the group gets a 404.
  - Revoking takes effect on the next call.
  - Building against the planted-instruction record produces no outside addresses, or they are flagged.
  - With the setting off, the menus are gone, the new endpoints return 404, and pack 20 and the UI smoke tests match the baseline. With it on, they still match.

**Phase 2** (small to medium)
- **Delivers:**
  - `publish_status` (§6.17): SDK call, endpoint, store and the `automation_published` source;
  - `app_state`;
  - previews shown directly inside the chat;
  - rendering without a browser, with screenshots;
  - scheduled refresh for `automation_output`;
  - the after-upgrade compatibility check.
- **Done when:**
  - a long-running test automation publishes progress and an app shows it live;
  - after that run is killed, the app shows the status as stale.

**Phase 3** (medium)
- **Starts after** the Solutions Author work that was in progress on 2026-10-03 has landed (§6.16).
- **Delivers:**
  - the `apps` kind in Solutions Author;
  - pushed updates;
  - app cards inside My Work items;
  - named AI actions.
- **Done when:**
  - export and import works between two installs;
  - the installed app arrives as a draft.

---

## 13. Test plan

- **Regression:**
  - Run pack 20 and the UI smoke tests before starting (the baseline) and after each phase, with the setting on and off.
  - The results must match the baseline.
- **Unit tests in The Agent's environment.** There is no pytest there, so these are standalone runners:
  - the store: create/read/update/delete, versions, grants, visibility, group lookups that fail closed, and transactions for schema changes;
  - manifest checks;
  - the page composer: the content policy comes first in `<head>`, nothing from the app comes before it, and libraries are inlined;
  - the bridge: grants, modes, parameter types, viewer values, the cache and one-execution-at-a-time, and stopping when the publisher loses access;
  - the tools: role checks and two-step flows;
  - the setting: off means the tools, prompt section and endpoints are absent;
  - the tool-list drift test.
- **Unit tests in the main environment** (pytest, `aihub2.1`):
  - The new SQL route, through `tests_v2/unit/app_route_harness.py`. Cover: identity required, single-SELECT check, parameters, typed output, refusal when the user has no access to the connection, and 404 when the setting is off.
  - `publish_status`, in phase 2.
- **Interface smoke tests** (Playwright in `aihub2.1`, with `/api/*` faked, like UI-1):
  - the menu list, the app screen, the Studio tabs and direct links;
  - with the setting off: no new menu entries and no errors;
  - the hostile-app sandbox test (§7.2).
- **A new `CA-*` section in pack 20:**
  - fixed-script checks: store → render → data → grant → revoke;
  - live chat turns: build by chat; refuse to fake data when a source fails; ask before promote and share.
- **Live end-to-end runs** with the dev machine's test accounts from the connection-access work:
  - `ru_alex` (End User, group 59);
  - `ru_drew` (End User, no group);
  - `dev_erin` (Developer);
  - admin.
- **Grading:** check against what actually happened (store rows, grants, the served HTML, run rows), not the wording of replies.
- **Note:** test files named `test*.py` are ignored by git here, so add them with `git add -f`.

---

## Appendix — code references (as of 2026-10-03)

**The Agent (`agent_service/`)**
- `main.py`:
  - lifespan and store set-up 41-49 (`views_store.init()` at 43);
  - CORS 77-81; static file mount 83-85; `_verify_request` 88-112; `/api/me` 206-222;
  - `/api/work/internal/raise` 365-403 (reserved kinds at 380);
  - promotion approval hook ~665-712 (682);
  - Views edit chat 1003-1052;
  - Views list / run / refresh-cache ~1171-1234.
- `work_tools.py`: reserved payload kinds 73-80.
- `views_store.py`:
  - `_save` 236-309;
  - `request_tenant_promotion` 312-339;
  - `visible_ns` / `list_views` / `get` 342-403;
  - `_can_modify` 406-444.
- `views_tools.py`:
  - SQL tile 70-95;
  - automation tile 98-167 (End User 403 at ~142);
  - `run_view` 196-294;
  - cache merge 267-284.
- `readthrough.py`:
  - `user_group_ids` 153; `all_groups` 170; `group_names` 186;
  - `automation_pending` 274; `automation_decided` 357;
  - tenant search 281, 364.
- `platform_tools.py`: `_headers` / user identity header 122-154.
- `rich_blocks.py`: `fence` 41-44; stored blocks 57-95; `action_fence` 110-127.
- `brain.py`:
  - imports 25-40; on/off switches 115-167; MCP server 169-182;
  - `MUTATING_TOOLS` 189-212; `SENSITIVE_TOOL_FIELDS` 218-222; `MUTATION_CLAIM_RE` 224-231; `_READ_TOOL_NAMES` 241-262;
  - prompt-file budget 771-795.
- `static/index.html` (3,977 lines):
  - left menu 401-416; token 682-690;
  - sandboxed email frame ~1394-1458;
  - `views` / `navs` / `show()` 1258-1291;
  - `PLATFORM_GROUPS` / `buildPlatformLinks` 1548-1607;
  - `viewFromHash` 1677;
  - tile error + cached rows 1889-1896.

**Main app and automations**
- `app.py`:
  - `CORS(app)` 387;
  - `/the-agent` ~1987-2026;
  - `_caller_identity` ~6489;
  - `_caller_connection_scope` / `_connection_access_refusal` ~6563 / ~6599;
  - `/api/discover/query` 17629-17705.
- `config.py`: `SQL_QUERY_ROW_SAFETY_CAP` 510; discover-route caps 519-521.
- `role_decorators.py`: key accepted in the URL 297; `api_key_or_session_required` ~525.
- `connection_acl.py`: `accessible_connection_ids` ~65.
- `DataUtils.py`: `accessible_agent_ids` ~1791.
- `automations/api.py`:
  - `automations_gate` 93; `automations_signed_in` 106;
  - `_viewer_may_see_row` 1491;
  - `runtime/review_item` 1621;
  - `internal/manage` 2444.
- `automations/runner.py`: one run at a time and `skipped` 717-730; pending review rows cancelled when a run ends ~458-482.
- `automations/approval_store.py`: one JSON file per row under `_approvals` (12, 38).
- `scripts/build_agent_service.ps1`: `*.db` files left out of builds, 32-36.

**`aihub-apps` (separate repo)** — see §9. Main references:
- `backend/src/ai/prompts.py` (system prompt);
- `backend/src/ai/verifier.py` (checking loop);
- `backend/src/versions/service.py` (versions);
- `backend/src/apps/models.py` (permissions);
- `app-sdk/src/index.ts` (SDK).
