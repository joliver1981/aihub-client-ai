# Handoff — The Agent cannot use My Connections (personal per-user MCP accounts)

**Status:** **BUILT AND LIVE-VERIFIED 2026-09-03 — see §12 (build record).** §0–§11 are the analysis as written before the build; where the build deviated from §6, §12 says so and why. The §7 interim wording was superseded by the "two mailboxes" framing that ships with the bridge.
**Date:** 2026-09-02 (re-verified 2026-09-03; built 2026-09-03)
**Repo:** `C:\src\aihub-client-ai-dev` (branch `main` — do NOT create a branch; commit to `main`)
**Owner:** James
**Scope for the executing agent:** build the bridge described in §6, or the interim honesty fix in §7, or both. Read §4 and §5 before writing any code — the obvious implementation is unsafe and will not work.

> **✅ UNBLOCKED 2026-09-03.** This handoff originally opened blocked: My Connections OAuth could not complete on any client install, because the redirect URI was derived from the browser's Host header and no provider accepts a non-HTTPS redirect outside loopback. That prerequisite **shipped** — commit `25571e8` "My Connections: Phase 1 live" (broker in production, client secret on file, redirect URI registered in Entra, migration 020 applied; `my_connections_live_check.py --require-broker --toggle` 21/21, `mcp_servers_ui_check.py` 13/13). See [`my-connections-oauth-broker-handoff.md`](my-connections-oauth-broker-handoff.md). **The bridge in §6 is now buildable.** One caveat below in §0.4: no human has completed a Connect yet, so there is nothing live to test against until someone does.

---

## 0. Re-verification — 2026-09-03 (research pass, no code changed)

Re-checked against the live tree because the §6 bridge became buildable. **Everything in §1–§5 still holds.** Four deltas worth knowing before you start.

### 0.1 Still unbuilt — confirmed by exhaustive search, not inference

| Check | Result |
|---|---|
| `agent_service/connection_tools.py` | does not exist |
| `/api/internal/my-connections*` (§6.1 seam) | **zero hits tree-wide**, including `dist/`, `build/`, `node_modules/` |
| `AGENT_MY_CONNECTIONS` kill switch | not present in `brain.py` |
| `builder_mcp/routes/` | still only `mcp_internal_routes.py`, `mcp_routes.py`, `my_connections_routes.py` — no new blueprint |
| `my_connections\|MCPUserTokens\|personal_connection` in `agent_service/*.py` | only `agent_builder_tools.py` — The Agent *setting `allow_personal_connections` on General Agents it builds*. It can grant the capability to others and still cannot use it itself. |
| "My Connections" anywhere under `agent_service/` | exactly two references, both cosmetic: the rail link (`static/index.html:1474`) and the nav-skill description (`product_skills/aihub-platform-navigation/SKILL.md:49`). **No Python file mentions it.** |
| §7 interim honesty fix | **not applied.** `work_tools.py:1136` still instructs "without capability disclaimers"; `brain.py:533` still opens *"YES — you can receive email AND open it"*. The confident-wrong-inbox answer is still the live behaviour. |

**Tool count is now 95, not the 68 in §2.1** (passes 2–4 added `web_tools`, `export_tools`, `map_tools`, `image_tools`, `agent_builder_tools`). None is connection-aware, so §2.1's conclusion is unchanged — only its arithmetic is stale.

### 0.2 ⚠ NEW — the `ask_agent` indirect route is CLOSED. Do not treat it as a back door.

Worth stating explicitly because it looks like a free bridge: The Agent could in principle reach a user's mail by delegating via `ask_agent` to a GeneralAgent that has the personal connection. **It cannot**, for two independent reasons:

1. **No identity on the wire.** `ask_agent` ([`platform_tools.py:313`](../agent_service/platform_tools.py)) POSTs `/api/agents/<id>/chat` with `_headers()` = **`X-API-Key` only**. That route ([`app.py:2652`](../app.py)) resolves a caller from a payload `user_id` **or** an `X-AIHub-User` assertion — The Agent sends neither.
2. **Wrong agent instance even with identity.** The route uses `active_agents[agent_id]` — the **shared** instance built as `GeneralAgent(agent_id)` with `user_id=None`. Personal MCP tools bind at `__init__` (`GeneralAgent.py:3238` → the tool bind at `:3268`); `run(user_id=…)` only sets `self.user_id` for *tracking*, only inside the `use_smart_render` branch, and this route passes `use_smart_render=False`. The `if user_id:` block in `get_mcp_tools_for_agent` never runs.

It fails **closed** — no cross-user bleed on this path — but it yields zero capability. The personalized path is `get_agent_for_user(agent_id, user_id)` ([`app.py:656`](../app.py)), whose **only** call site is `app.py:5410` (`/chat/general`, `app.py:5194`). Do not "fix" this by pointing `ask_agent` at the shared instance with a user id bolted on; that would recreate Blocker B on a second path.

### 0.3 ⚠ NEW — §6.1 can be made safer and cheaper than originally designed

§6.1 proposes internal routes taking `user_id` as a **required, trusted parameter**. Since this doc was written, The Agent grew a proven signed-identity mechanism: [`document_tools.py:161`](../agent_service/document_tools.py) mints `X-AIHub-User` via `shared_auth.sign_user_assertion(uid, tenant_id, role)` for the v3 category ACL — and `api_agent_chat` already **verifies** that same assertion (`shared_auth.verify_token(…, AUD_INTERNAL)` → `claim_user_id`).

**Prefer the signed assertion over a trusted `user_id` parameter.** Same plumbing, already proven in this service, and it removes the "any service-key holder can name any user" property the parameter design has. Note the guard in that helper: the contextvar default is the service principal (`user_id=0`) and must **not** mint an assertion — copy that check.

### 0.4 Nothing live to test against yet

`MCPUserTokens` still holds only user 13's rows from **2026-05-20** — no human has completed a Connect since the broker went live. Those tokens are ~105 days old and an AAD delegated refresh token typically dies at 90 days of inactivity. §8 step 1 (baseline the legacy path) therefore **will fail** until someone re-authorizes at `/my-connections`. That is the first thing to do, and it needs a human at the Microsoft sign-in.

### 0.5 Unchanged and still the hard part

**Blocker B** (§4) is untouched: [`server_manager.py:73`](../builder_mcp/gateway/server_manager.py) still keys `self._connections[server_id]` with the per-user bearer baked into the shared connection. This is a live cross-user token-bleed hazard for GeneralAgent under concurrent multi-user load **today**, and any bridge inherits it. §8 step 4 remains the test that matters most.

---

## 1. The finding in one paragraph

AI Hub has a **My Connections** feature (`/my-connections`) where each user authorizes their *own* Microsoft 365 / Google / etc. account via per-user OAuth (`authorization_code` grant), so agents can act **as that user** — read their real mailbox, send mail as them, check their calendar. The legacy **GeneralAgent** picks these up automatically ("Flow B"). **The Agent** (the next-gen service on `:5111`, `agent_service/`) has **no tools for it at all** — not a partial gap, a total one. Worse, The Agent's own UI links to the My Connections page, and its `list_my_email` tool reads a *different* mailbox (the A6 agent mailbox), so "check my email" currently returns a confident answer about the wrong inbox with no disclaimer.

---

## 2. Evidence (all verified live on this box, 2026-09-02)

### 2.1 The Agent's full tool inventory — 68 tools, none connection-aware

> **Count stale as of 2026-09-03: it is now 95 tools** (passes 2–4 added web/export/map/image/builder modules). The table below is the 2026-09-02 snapshot. The conclusion is unchanged — none of the 95 is connection-aware. See §0.1.

Assembled at [`agent_service/brain.py:132`](../agent_service/brain.py) via `create_sdk_mcp_server(name="aihub", ...)`:

| Module | Count | Tools |
|---|---|---|
| `platform_tools.py` | 8 | `list_data_connections`, `get_connection_schema`, `probe_connection_query`, `ask_data_agent`, `list_playbooks`, `list_recent_runs`, `list_secret_names`, `store_platform_secret` |
| `authoring_tools.py` | 18 | automations + code flows lifecycle |
| `work_tools.py` | 8 | `raise_work_item`, `list_my_work`, `schedule_agent_task`, `save_skill`, `list_skills`, `draft_email_reply`, `setup_agent_email`, `get_agent_email_status` |
| `views_tools.py` | 7 | saved views + refresh/email schedules |
| `integration_tools.py` | 4 | `list_integrations`, `get_integration_operations`, `execute_integration_operation`, `assign_integration_groups` |
| `document_tools.py` | 7 | files/search/import/read |
| `portal_tools.py` | 9 | RPA portals + portal workflows |
| `email_tools.py` | 5 | `list_my_email`, `read_email`, `list_email_attachments`, `read_attachment`, `save_attachment` |
| `file_tools.py` | 1 | `offer_file_download` |
| `code_tools.py` | 1 | `run_python` |

`grep -rn "mcp\|MCPUserTokens\|personal_connection" agent_service/*.py` returns **only** hits for the Claude Agent SDK's own in-process tool server (`create_sdk_mcp_server`, `mcp__aihub__*` prefix stripping). Nothing touches My Connections.

### 2.2 Only one consumer of personal connections exists in the entire repo

`get_mcp_tools_for_agent(agent_id, user_id)` — the Flow B loader at [`builder_mcp/agent_integration/mcp_agent_tools.py:70`](../builder_mcp/agent_integration/mcp_agent_tools.py) that joins `MCPUserTokens` for the calling user — is called from exactly one place:

- [`GeneralAgent.py:3238`](../GeneralAgent.py)

Full-tree grep (excluding `node_modules/`, `dist/`, `build/`, `__pycache__/`) confirms: **not** from `agent_service/`, **not** from `command_center/`. The only other `MCPUserTokens` read outside `builder_mcp/` is the per-user agent cache-invalidation signature at [`app.py:642`](../app.py) (`_get_user_mcp_signature`).

### 2.3 There is not even an opt-in switch for The Agent

The per-agent opt-in `allow_personal_connections` is a **column on the `Agents` table** ([`DataUtils.py:763`](../DataUtils.py), `:1018`, `:1143`; written from [`app.py:2944`](../app.py), default `True`). The Agent is a service, not a row in `Agents`, so it has no analogue. See §6.4 for the recommended replacement.

---

## 3. Live state on this dev box (verify before retesting)

```
MCPServers
  1   AI Hub Test MCP Server                              remote / none    / enabled
  5   Test MCP Server                                     remote / none    / enabled
  29  Microsoft Learn (Test)                              remote / none    / enabled
  30  EveriAI Graph — Step 1: OAuth Credentials (TEST)    remote / oauth2  / enabled
      url    http://127.0.0.1:5001/api/internal/mcp/graph   <- in-APP MCP endpoint
      grant  authorization_code
      scope  User.Read Mail.Read Mail.Send Calendars.Read offline_access

MCPUserTokens
  server 30, user 13 — 3 rows, last updated 2026-05-20 23:07

AgentMCPServers
  (agent 232 -> server 54)   ORPHAN: server 54 does not exist in MCPServers
  (agent 518 -> server 29)

MCP Gateway  :5071  healthy, 0 active connections
The Agent    :5111  healthy, app_root C:\src\aihub-client-ai-dev  (runs from THIS tree)
```

**Server 30 is the Graph connection James referred to.** Its four tools are defined at [`builder_mcp/servers/graph_tools.py:123`](../builder_mcp/servers/graph_tools.py):
`get_my_profile`, `list_recent_emails`, `send_email`, `list_upcoming_meetings`.

It is hosted **in-process by the main app** — [`builder_mcp/routes/mcp_internal_routes.py`](../builder_mcp/routes/mcp_internal_routes.py) speaks MCP streamable-http (JSON-RPC 2.0 over a single POST), loopback-restricted, and passes the `Authorization: Bearer` header straight through to Microsoft Graph. The gateway populates that header via `mcp_agent_tools._get_auth_headers` → `oauth_manager.get_access_token`.

> ### ⚠ The stored token is 104 days stale
> `oauth_expires_at = 1779322919` — expired **~9,032,299 seconds (≈104.5 days) ago**. A refresh token IS present and [`oauth_manager.get_access_token:323`](../builder_mcp/agent_integration/oauth_manager.py) auto-refreshes, but an AAD delegated refresh token typically dies after 90 days of inactivity. **Expect user 13 to need a re-authorize at `/my-connections` before any live test.**
>
> **Prove the legacy path works FIRST** (a GeneralAgent with `allow_personal_connections=1`, chatting as user 13, calling `list_recent_emails`) so you are testing your new bridge and not a dead OAuth grant.

---

## 4. ⚠ Do NOT wire The Agent to the existing `/mcp/servers/<id>/tools/call` route

It looks like a free bridge — [`builder_mcp/routes/mcp_routes.py:579`](../builder_mcp/routes/mcp_routes.py) (`GET /servers/<id>/tools`) and `:621` (`POST /servers/<id>/tools/call`) both carry `@api_key_or_session_required(min_role=2)`, so a service-key caller reaches them. **It is unsafe and it will not work.** Two hard blockers:

**Blocker A — no user identity, so no token.**
`get_server_tools` calls `_build_connection_config(server_type, server_url, auth_type, connection_config, server_id)` at roughly `mcp_routes.py:607` — **without `user_id`**. `_get_auth_headers(..., user_id=None)` therefore asks `oauth_manager.get_access_token(server_id, None)`, which for an `authorization_code` grant **raises**:

> `get_access_token requires user_id for authorization_code servers (personal/delegated tokens).`

These routes physically cannot mint user 13's Graph token.

**Blocker B — the gateway caches connections per `server_id` only, so bearers leak across users.**
[`builder_mcp/gateway/server_manager.py:73`](../builder_mcp/gateway/server_manager.py) stores `self._connections[server_id]`, with no user dimension. The per-user `Authorization` header is baked into that shared connection at connect time. `call_server_tool` does not reconnect — it calls `gateway.call_tool(server_id, ...)` against whatever connection already exists. **User B's tool call would execute against user A's Graph token.**

GeneralAgent only escapes this because `get_mcp_tools_for_agent` calls `connect_server(server_id, config)` on every per-user agent build, and `ServerManager.connect` tears down the existing connection for that `server_id` first (`server_manager.py:53-54`). That is a race, not a fix — two users hitting server 30 concurrently is a live cross-user token-bleed hazard **that any new bridge will inherit unless it is closed.**

---

## 5. Architectural constraints the implementation must respect

1. **`agent_service/` must not import `CommonUtils` or flask.** Stated contract — see the [`agent_service/email_tools.py`](../agent_service/email_tools.py) module docstring: *"this service must not import CommonUtils/flask — the HTTP client is the whole contract."* Credential decryption needs the `MCP_ENCRYPTION_KEY` / `encrypt.ENCRYPTION_KEY` and DB access that live in the main app.
2. **There IS an escape hatch, but it is the wrong one here.** [`agent_service/readthrough.py`](../agent_service/readthrough.py) proves the service *can* open its own read-only pyodbc connection (own driver selection + `DATABASE_*` env + `EXEC tenant.sp_setTenantContext`) when no HTTP seam exists. **Do not use it for this.** Tokens are encrypted with `DECRYPTBYPASSPHRASE` and need refresh-on-expiry with a per-`(server, user)` lock — that logic belongs in `oauth_manager`, in the main app. Go over HTTP.
3. **Tool shape mismatch.** [`builder_mcp/client/tool_converter.py`](../builder_mcp/client/tool_converter.py) emits **LangChain** tools for GeneralAgent. The Agent uses `claude_agent_sdk`'s `@tool` decorator. No direct reuse — you are writing new tool bodies either way.
4. **Identity is already plumbed.** `CURRENT_USER` is a `contextvars.ContextVar` set per turn in `main.py` before the loop runs ([`agent_service/platform_tools.py:26`](../agent_service/platform_tools.py)); `user_id` comes from the JWT `sub` claim ([`agent_service/main.py:107`](../agent_service/main.py)). Tools read identity from there and **never** from anything the model wrote.

---

## 6. Recommended implementation

Mirror the **integrations** pattern exactly — [`agent_service/integration_tools.py`](../agent_service/integration_tools.py) is the proven template for "platform feature, credentials stay server-side, thin HTTP wrappers, service-key auth, honest truncation".

### 6.1 Main app — new user-scoped internal seam

Add alongside the existing internal integration routes ([`app.py:6066`](../app.py) list / `:6109` operations / `:6126` assign-groups / `:6163` execute). Suggested home: a new blueprint in `builder_mcp/routes/` registered near `my_connections_bp` at [`app.py:13994`](../app.py).

```
GET  /api/internal/my-connections?user_id=<uid>
     -> [{server_id, name, description, category, icon, connected: bool,
          last_connected, scope}]
        Same filter as my_connections_routes.list_my_connections:
        auth_type='oauth2' AND enabled=1 AND oauth_grant_type='authorization_code'.
        MUST also return servers the user has NOT authorized, flagged
        connected:false, so the agent can say "go connect it" instead of
        silently having no capability.

GET  /api/internal/my-connections/<server_id>/tools?user_id=<uid>
     -> {server_id, name, tools:[{name, description, inputSchema}]}

POST /api/internal/my-connections/<server_id>/call
     body {user_id, tool_name, arguments}
     -> {status, result} | {status:"error", message}
```

Auth: service key (`X-API-Key`), same decorator family as the internal integration routes. `user_id` is a **required** parameter on every endpoint — never inferred, never defaulted.

**Both** the tools and call endpoints must:
- pass `user_id` through: `_build_connection_config(..., user_id=uid)` → `_get_auth_headers(..., user_id=uid)` → `oauth_manager.get_access_token(server_id, uid)`;
- **reconnect immediately before the call** with that user's config (closes Blocker B for the single-request case), **or** — better — add a user dimension to the gateway's connection key so `_connections[(server_id, user_id)]` is the unit. The second option also fixes the pre-existing GeneralAgent race and is the more honest fix; it touches `builder_mcp/gateway/server_manager.py` and every `connect_server`/`call_tool`/`list_tools`/`disconnect` caller, so scope it deliberately.

Return the "user has not authorized this yet" case as **structured, non-exceptional data** (`connected:false`, or a `needs_authorization` error code), not a 500 — `get_access_token` raises `RuntimeError("No refresh token for user_id=… — the user must complete the OAuth authorization flow (My Connections).")` and that must surface to the model as readable text.

### 6.2 The Agent — new `agent_service/connection_tools.py`

Three tools, bodies modeled on `integration_tools.py`:

| Tool | Notes |
|---|---|
| `list_my_connections` | Both pools: authorized (usable now) and available-but-not-yet-authorized (tell the user to visit `/my-connections`). Description must state plainly that these are the user's **personal** accounts, distinct from `list_data_connections` (databases) and `list_integrations` (tenant-wide). |
| `get_connection_tools` | List the MCP tools on one connected server. "Check before executing — never guess a tool name or its parameters," same wording discipline as `get_integration_operations`. **Must hide write tools that the §6.4 gate denies** — the model should not see a capability it cannot use. |
| `use_my_connection` | Execute one tool. Truncate previews at `MAX_RESULT_CHARS = 2500` like `integration_tools.py` and report counts honestly. **Must refuse denied write tools** — same shared guard as above. |

**Both** tools call one shared `write_allowed(tool_name) -> bool` helper backed by `AGENT_MY_CONNECTIONS_WRITE_TOOLS` (see §6.4 — this is decided, not optional). Empty config = read-only personal connections, which is the default posture.

Register in [`brain.py:132`](../agent_service/brain.py) behind a kill switch, following the established additive/reversible doctrine (`AGENT_DOCUMENT_TOOLS`, `AGENT_PORTAL_TOOLS`, `AGENT_EMAIL_TOOLS`, `AGENT_RUN_PYTHON_TOOL`):

```python
_MY_CONNECTIONS_ON = os.getenv("AGENT_MY_CONNECTIONS", "true").lower() == "true"
...
+ (CONNECTION_TOOLS if _MY_CONNECTIONS_ON else [])
```

Also update in `brain.py`:
- `_READ_TOOL_NAMES` (`brain.py:189`) — add `list_my_connections` and `get_connection_tools` so read-only work-item side threads can see them. **Leave `use_my_connection` out** — a side thread must not send mail as the user.
- `MUTATING_TOOLS` (frozenset, `brain.py` ~145) — add `use_my_connection`, so the mutation-claim guard covers "I sent that email".
- `SYSTEM_PROMPT` "WHAT YOU CAN DO" block (`brain.py` ~208) — one line for personal connections.

> **⚠ agent_service gotcha (bitten before):** never put a helper function between an `@tool()` decorator and the function it decorates. `create_sdk_mcp_server` fails at import with `'function' object has no attribute 'name'`. See the note at [`agent_service/work_tools.py:877`](../agent_service/work_tools.py).

### 6.3 Restart to pick up changes

The Agent service does not hot-reload. Restart it with `agent_service/start_agent_service_dev.bat` (conda env `aihub-agent`, port 5111). Main-app route changes need the main app restarted (env `aihubant`). **Never pipe the V3 restart launcher from an agent shell.**

### 6.4 Authorization decisions — settle these before coding

- **Opt-in model (READ).** `Agents.allow_personal_connections` has no analogue for a service. Per James's standing *denylist-over-allowlist* directive, the right default is: **The Agent uses whatever the calling user has personally authorized**, with a service-level denylist (`AGENT_MY_CONNECTIONS_DENY=<comma-separated server_ids>`) that starts empty and grows by observation. Do not build an allowlist. **This applies to read capability only** — writes are a deliberate default-closed carve-out, below.

### ⚠ DECIDED (James, 2026-09-02) — writes through a personal connection are OFF by default

**No sending email as the user unless a config setting explicitly turns it on.** This is settled; do not redesign it. The rationale, so you can enforce it in the right spirit:

- **The Agent already has its own mailbox.** A6 mail is identifiable as the agent. Graph `send_email` puts the message in the *user's* Sent Items with only their name on it — indistinguishable from them typing it, and unrecallable. It buys nothing the agent's own address doesn't already do.
- **The capability arrives whether you want it or not.** Server 30's scope already includes `Mail.Send`, so `send_email` appears in the tool list the moment the bridge exists. Excluding it must be deliberate.
- **Read and write are different risk classes.** Reading is inspectable and recoverable; sending is irreversible and outward-facing. They must not ride on the same switch.

**Implement it as:**

1. **A denied-by-default list, not a boolean.** `AGENT_MY_CONNECTIONS_WRITE_TOOLS` — empty by default; a comma-separated list of connection tool names permitted to mutate (e.g. `send_email`). The same gate then covers every future write tool on any connection — calendar write, file upload, Teams post — with no new code.
2. **Enforced at the chokepoint, both paths.** Filter denied tools out of `get_connection_tools`' output **and** refuse them in `use_my_connection`. One shared guard function both call — not two that can drift apart. (This is the repeated lesson from the pack-09 fix round: guard the chokepoint every caller converges on.)
3. **Steer, don't just block.** When a write is denied, the tool returns readable text telling the model to offer the agent's own mailbox instead ("I can send this from my own address"), not a bare refusal. Silent or cryptic failure here is the outcome to avoid.
4. **Still a mutation when enabled.** Any tool permitted through the gate goes in `MUTATING_TOOLS` so the mutation-claim guard covers "I sent that email," and stays out of `_READ_TOOL_NAMES` so read-only side threads can never reach it.
5. **Fail-closed matching only.** Compare tool names against the configured list exactly. Do not pattern-match to *guess* whether something is a write — an unrecognized tool is denied, not inferred.

> **Note on the tension:** this is default-**closed**, which cuts against the standing denylist-over-allowlist directive, and that is intentional. Default-open is the right rule for capability discovery; it stops paying for irreversible actions taken in a human's name. Writes are the narrow carve-out — reads stay default-open per the usual rule.
- **Headless / scheduled runs.** A scheduled agent task or email-triggered turn runs as the user who created the trigger. Decide explicitly whether personal connections are live in headless mode or interactive-only. Interactive-only is the safer v1 — "The Agent sent mail as me at 3am from a schedule I forgot about" is the failure you do not want.
- **Audit.** `MCPToolConverter` captures `user_id` + `agent_id` for the audit log on the GeneralAgent path. The Agent keeps its own per-user audit; make sure the bridge writes an equivalent record — who, which server, which tool, what result.

---

## 7. Interim honesty fix (small, independent, worth doing regardless)

The silent wrong answer is the part that will bite in a demo, and it can be fixed without the bridge.

**The collision:** The Agent's `list_my_email` / `read_email` read the **A6 agent mailbox** (cloud relay — see the [`agent_service/email_client.py`](../agent_service/email_client.py) docstring), *not* Outlook. And `get_agent_email_status` is explicitly instructed to answer *"did you get any email?"* **"without capability disclaimers."** So "check my email" is answered confidently from the wrong inbox.

**The setup that makes it worse:** The Agent's own next-gen UI links to the page it cannot use — [`agent_service/static/index.html:1117`](../agent_service/static/index.html) puts `["My Connections", "/my-connections"]` in the Platform rail. A user connects Microsoft 365 from inside The Agent, returns, and finds nothing changed.

**And the docs promise it:** [`assistant_docs/pages/my-connections/guide.md:3`](../assistant_docs/pages/my-connections/guide.md) opens with *"so AI agents can act on their behalf — sending email as them, reading their calendar…"*. Line 69 correctly scopes the wiring to `/custom_agent_enhanced` (the legacy builder), so the doc is technically accurate, but line 54 promises a failure mode The Agent cannot produce: *"the tool call will fail with an auth error (or the agent will be told it can't perform that action)"* — there is no tool to fail.

**Fix:** a few lines of prompt/description text stating that the agent-email tools cover the **AI Hub agent mailbox only**, and that Outlook / Microsoft 365 lives under `/my-connections`, which The Agent cannot currently reach. Touch:
- `list_my_email` / `get_agent_email_status` tool descriptions in [`agent_service/email_tools.py`](../agent_service/email_tools.py) and [`agent_service/work_tools.py`](../agent_service/work_tools.py);
- the My Connections entry in [`agent_service/product_skills/aihub-platform-navigation/SKILL.md:49`](../agent_service/product_skills/aihub-platform-navigation/SKILL.md).

> **Skill propagation is automatic.** The nav skill is materialized per-user into `data/agent/users/<uid>/ws/.claude/skills/` (currently users 1, 13, 77, 424250, 424301, 424310, 987654). That is not stale state to clean up — [`agent_service/skills_mount.py:128`](../agent_service/skills_mount.py) `build_user_workspace` does a fresh `copytree` **every turn**, so editing the source `SKILL.md` propagates on the next message with no cache to bust.

Delete this section's wording again once §6 ships.

---

## 8. Verification plan

1. **Baseline the legacy path.** As user 13, chat with a GeneralAgent that has `allow_personal_connections=1`, ask it to list recent emails. If this fails with an OAuth error, re-authorize server 30 at `/my-connections` first. **Do not proceed until this is green** — otherwise you cannot tell a bridge bug from a dead grant.
2. **Seam, directly.** `curl` each new `/api/internal/my-connections*` endpoint with the service key and `user_id=13`. Confirm the tools list returns the four Graph tools and a call returns real mail.
3. **Negative identity test.** Same calls with a `user_id` that has **no** token for server 30. Must return a clean `needs_authorization` / `connected:false`, **not** a 500 and **not** user 13's mail.
4. **Cross-user bleed test (the important one).** Two concurrent calls to server 30 as two different authorized users. Each result must belong to the right mailbox. This is the test that proves Blocker B is closed; write it as a permanent regression test.
5. **Through The Agent.** Ask The Agent, as user 13, "what's in my Outlook inbox?" — it must use `use_my_connection`, not `list_my_email`. Then ask as a user with no connection — it must say so and point at `/my-connections`.
6. **Write gate — default state.** With `AGENT_MY_CONNECTIONS_WRITE_TOOLS` unset (the shipping default), ask The Agent to email someone from your Outlook account. It must (a) not list `send_email` in `get_connection_tools`, (b) refuse it if called directly, and (c) offer to send from its own A6 address instead. Confirm no mail leaves the Graph account — check the test account's Sent Items, not just the agent's reply.
7. **Write gate — enabled state.** Set `AGENT_MY_CONNECTIONS_WRITE_TOOLS=send_email`, restart, repeat. Mail should now send, appear in `MUTATING_TOOLS` telemetry, and remain unreachable from a read-only work-item side thread.
8. **Kill switch.** `AGENT_MY_CONNECTIONS=false` + restart → tools unregister, The Agent honestly reports no such capability, nothing else regresses.
9. **Regression gate.** Run the platform regression pack (`test_human/15_Platform_Regression`) before any build.

---

## 9. Loose ends spotted in passing (not part of this work)

- **Orphan row:** `AgentMCPServers (agent 232 -> server 54)` references a `server_id` that does not exist in `MCPServers`. Harmless today (the join drops it) but it is dead data; worth a cleanup and a FK if the schema allows.
- **Pre-existing gateway race:** Blocker B in §4 already affects GeneralAgent under concurrent multi-user load today. Fixing the gateway connection key (§6.1, second option) fixes both at once.
- **`/api/internal/mcp/graph` auth posture:** loopback-restricted with bearer passthrough, no other authentication. Fine given the gateway is same-machine, but note it if the gateway ever moves off-box.

---

## 10. Files you will touch or read

| Path | Why |
|---|---|
| `agent_service/brain.py` | tool registration, `_READ_TOOL_NAMES`, `MUTATING_TOOLS`, system prompt |
| `agent_service/connection_tools.py` | **new** — the three tools |
| `agent_service/integration_tools.py` | **the template to copy** |
| `agent_service/platform_tools.py` | `CURRENT_USER`, `_get`/`_post`/`_text` helpers |
| `agent_service/email_tools.py`, `work_tools.py` | §7 honesty wording |
| `agent_service/product_skills/aihub-platform-navigation/SKILL.md` | §7 honesty wording |
| `builder_mcp/routes/my_connections_routes.py` | the existing user-facing surface + its server filter |
| `builder_mcp/routes/mcp_routes.py` | existing admin routes (do not reuse — see §4) |
| `builder_mcp/agent_integration/mcp_agent_tools.py` | `_build_connection_config`, `_get_auth_headers`, Flow B reference |
| `builder_mcp/agent_integration/oauth_manager.py` | `get_access_token`, refresh + locking |
| `builder_mcp/gateway/server_manager.py` | connection keying (Blocker B) |
| `builder_mcp/servers/graph_tools.py` | the four Graph tools |
| `app.py` | internal-route neighborhood (~6066–6198), blueprint registration (~13994) |

---

## 11. Standing directives for whoever executes this

- **No git branches.** Commit to `main`, promptly. Ask before large pushes.
- **Services run from THIS tree** via manual cmd windows — commit immediately so work is not lost to a git clean.
- **Additive and reversible.** Every new capability ships behind an env kill switch that reverts to today's behavior.
- **Honesty over silent success.** Tool bodies never raise into the loop; empty results are first-class information; server rejections surface verbatim; mutations are verified by read-back where a read-back exists.
- **Denylist, not allowlist.** Default-open capability gating; deny lists start empty and grow by observation. **One named exception in this work:** writes through a personal connection are default-closed (§6.4). Do not "correct" that back to default-open — it is a deliberate decision, not an oversight.
- Another agent may be working in this same tree — verify `git diff` is 100% yours and re-check `origin/main..HEAD` before pushing.

---

## 12. Build record — 2026-09-03

Both §6 (the bridge) and the honesty wording (§7, reframed) shipped in one commit on `main`. Everything below was verified against the running services on this box after restarting the gateway, the main app and The Agent from this tree.

### 12.1 What was built

| Layer | File | What it does |
|---|---|---|
| Gateway | `builder_mcp/gateway/server_manager.py` | **Connections are keyed `(server_id, user_id)`** (`connection_key("30", 13)` → `"30@u13"`). `connect` / `disconnect` / `list_tools` / `call_tool` / `get_status` take an optional `user_id`; callers that pass none keep the legacy `server_id`-only key, so the admin routes are unchanged. `get_all_connections` reports `server_id` + `user_id` per key; `connect`/`get_status` echo `connection_key`. `tools/list` **annotations** are carried through. |
| Gateway | `builder_mcp/gateway/app_mcp_gateway.py` | `user_id` on `ConnectRequest`, `DisconnectRequest`, `ToolCallRequest` and as a query param on `/status` and `/tools`. Older callers omit it and see no change. |
| Main app | `builder_mcp/client/mcp_gateway_client.py` | `user_id=` kwarg on the five calls (sent only when set — an older gateway ignores it). |
| Main app | `builder_mcp/client/tool_converter.py` | `MCPToolConverter(connection_user_id=…)` routes every GeneralAgent tool call to that user's own connection. |
| Main app | `builder_mcp/agent_integration/mcp_agent_tools.py` | `is_personal_server(server_id, auth_type)` (oauth2 + `authorization_code`). Flow B connects/lists/calls **personal** servers under the user's key and shared servers under the legacy key — **this closes the pre-existing GeneralAgent race (Blocker B) as well.** |
| Main app | `builder_mcp/agent_integration/personal_connections.py` | **New.** The bridge: `catalog_for_user` (the ONE filter the page and the seam share), `ensure_user_connection` (fresh token from `oauth_manager.get_access_token`; reopen when the connection is older than `MY_CONNECTIONS_CONN_MAX_AGE`, default 60 s; one retry after a stale-401), `list_user_tools`, `call_user_tool`, `annotate_known_tools`, `[MCP_AUDIT]` lines on logger `mcp.audit` with `agent_id=the_agent`. **Refuses (`gateway_unscoped`) when the gateway does not echo a per-user `connection_key`** — an old gateway would silently share, so it fails closed. |
| Main app | `builder_mcp/routes/my_connections_internal_routes.py` | **New.** `GET /api/internal/my-connections`, `GET …/<sid>/tools`, `POST …/<sid>/call`. `internal_api_key_required()` **plus** a signed `X-AIHub-User` assertion (aud `aihub-internal`); the user id is never a parameter; sub 0 is refused. Registered in `app.py` right after `my_connections_bp`. |
| Main app | `builder_mcp/routes/my_connections_routes.py` | The page's list now calls the shared catalog; Disconnect also drops the user's live gateway connection. |
| Main app | `builder_mcp/servers/graph_tools.py` | The four Graph tools declare MCP `annotations` (`readOnlyHint` true on the three reads, false on `send_email`). |
| The Agent | `agent_service/connection_tools.py` | **New.** `list_my_connections`, `get_connection_tools`, `use_my_connection` — thin wrappers over the seam, identity from `CURRENT_USER`, assertion minted per call. One shared guard `tool_permission()` feeds both discovery and execution. |
| The Agent | `agent_service/brain.py` | Kill switch `AGENT_MY_CONNECTIONS` (default true); `_READ_TOOL_NAMES` += the two discovery tools; `MUTATING_TOOLS` += `use_my_connection`; server version 0.11.0; new prompt section **PERSONAL CONNECTIONS**; the EMAIL section opens with "two different mailboxes". |
| The Agent | `work_tools.py`, `email_tools.py`, `product_skills/aihub-platform-navigation/SKILL.md`, `product_skills/aihub-integrations/SKILL.md` | The §7 wording, reframed: agent mailbox vs the user's own Outlook, and where each lives. |
| Docs | `assistant_docs/pages/my-connections/guide.md` | Says The Agent uses these connections and that sending from the user's account is admin-enabled. |
| Tests | `builder_mcp/gateway/tests/test_user_scoped_connections.py`, `tests_v2/unit/test_personal_connections_seam.py`, `tests_v2/unit/test_agent_connection_tools.py`, pins in `tests_v2/unit/test_agent_brain_tool_lists.py`, live `tests_v2/live/the_agent_my_connections_live_check.py` | See §12.4. |

### 12.2 Configuration (The Agent service unless noted)

| Setting | Default | Meaning |
|---|---|---|
| `AGENT_MY_CONNECTIONS` | `true` | Kill switch. `false` unregisters the three tools (98 → 95 tools). |
| `AGENT_MY_CONNECTIONS_WRITE_TOOLS` | *(empty)* | Comma-separated **exact** tool names permitted to mutate through a personal connection, e.g. `send_email`. |
| `AGENT_MY_CONNECTIONS_HEADLESS_WRITES` | `false` | Whether an allowed write may also run in a scheduled / email-triggered session. |
| `AGENT_MY_CONNECTIONS_DENY` | *(empty)* | Comma-separated server ids The Agent must not use even when the user authorized them. |
| `MY_CONNECTIONS_CONN_MAX_AGE` (main app) | `60` | Seconds before a user's gateway connection is reopened with a fresh token. |

### 12.3 Deviations from §6 — and why

1. **Identity = the signed assertion (§0.3), not a trusted `user_id` parameter.** The seam ignores any `user_id` in the query or body (pinned by test). A service-key holder cannot name a user.
2. **Blocker B closed at the gateway (the second option in §6.1), not by reconnect-before-call alone.** The key is `(server_id, user_id)`; Flow B uses it for personal servers, so the GeneralAgent race is gone too. The bridge additionally refuses an unscoped gateway.
3. **"Read" is a server declaration, never a guess.** §6.4.5 says do not pattern-match to decide whether a tool writes. So: a tool runs if it is exactly listed in `AGENT_MY_CONNECTIONS_WRITE_TOOLS`, **or** its server declares `annotations.readOnlyHint = true`. Anything undeclared is denied with text that names the setting. Our Graph server declares all four; the seam overlays those declarations even through an older gateway. Foreign servers (Phase 2 Google/Slack) must declare, or their tools are listed exactly by name.
4. **Headless: reads allowed, writes double-gated.** §6.4 left this open and leaned interactive-only. The daily-routine use case ("summarize my inbox each morning") is exactly a scheduled read, so reads are allowed there; an allowed write additionally needs `AGENT_MY_CONNECTIONS_HEADLESS_WRITES=true`. "Sent mail as me at 3 am" stays impossible by default.
5. **`get_connection_tools` hides denied tools from the usable list but adds one steering footer** naming them and what to offer instead (the agent's own mailbox). Hiding alone left the model with no explanation to give the user.
6. **The catalog moved into a shared module** so the page and the seam cannot drift.
7. **The §7 "cannot reach" wording was not applied as written** because the bridge ships with it; the same sites now carry the "two mailboxes" distinction instead.

### 12.4 Evidence

- Unit: gateway 22/22 (18 existing + 4 new, both `aihubmcp` standalone and `aihub2.1` pytest); seam/bridge 15/15; The Agent tools 16/16; brain tool-list drift 8/8 (new pins included).
- Kill switch, offline: `AGENT_MY_CONNECTIONS=false` → 95 tools, none of the three registered; `true` → 98.
- Restarted from this tree (detached, WMI): gateway `18224 → 12992`, main app `15380 → 10908`, The Agent `10544 → 4068`.
- **Live, `tests_v2/live/the_agent_my_connections_live_check.py --username admin`: 18/18.** In order: seam refuses no-assertion / garbage / service-principal / no-service-key (all 401); catalog for user 13 lists server 30 **connected**; the four Graph tools with annotations; **`list_recent_emails` returned 5 real messages** (the access token had expired ~8 h earlier — the refresh path worked; `MCPUserTokens.updated_date` moved to 22:26); Sent Items baseline; gateway holds `30@u13`; a user with no grant gets `needs_authorization` on tools and on call; **cross-user race** (two threads as user 13, one as a stranger, 3 calls each, concurrently): user 13 success ×6 with 2 messages each, stranger `needs_authorization` ×3, gateway keys after = `['30@u13']` only; through The Agent as user 13, "how many messages are in my inbox" ran `list_my_connections → get_connection_tools → use_my_connection` and **not** `list_my_email`; as the stranger it answered with `/my-connections`; the **write-gate probe** ("send from my own Outlook account") made **zero** `use_my_connection(send_email)` attempts, Sent Items unchanged, and the reply offered its own mailbox.
- Platform regression pack 15, run against the restarted services (this tree, with the bridge live): **CLEAN — 51 PASS / 51 SKIP / 4 XFAIL** (the four are the pre-existing authz tripwires), exit 0, no regressions. The MCP row `mcp_servers_api` (admin routes, legacy server_id key) still passes alongside the per-user keys.

### 12.5 §8 verification plan — status

| Step | Status |
|---|---|
| 1 baseline legacy GeneralAgent path | Not run separately. The grant was proven live through the seam, which uses the same `get_access_token` path; user 13 completed a real Connect on 2026-09-03 12:57. |
| 2 seam directly | Done (live 18/18). |
| 3 negative identity | Done (401s + `needs_authorization`; never mail). |
| 4 cross-user bleed | Done at the gateway (unit, 3 users concurrently, distinct transports) and at the seam (live, user + stranger concurrently). A second **real** grant (two humans) is still the only way to see two mailboxes side by side — open as T7. |
| 5 through The Agent | Done (three chats). |
| 6 write gate, default | Done live (no attempt, Sent Items unchanged). |
| 7 write gate, enabled | **Unit-tested only.** Not run live: it would send real mail from James's account and needs a restart with the setting on. |
| 8 kill switch | Verified at import time (offline), not by a service restart. |
| 9 regression gate | Done: pack 15 CLEAN (51/51/4 XFAIL) after the restart. |

### 12.6 Loose ends (new)

- Deleting a server on the admin page disconnects only the legacy gateway connection; per-user connections for that server idle until the gateway restarts (harmless — the catalog 404s before any call).
- The dry-run auth middleware reads `X-API-Key`, not `X-Internal-API-Key`; send the internal key as `X-API-Key` (The Agent does; the live script was fixed to).
- The main app has no `/health` route; `/login` is its liveness probe.
- `AgentMCPServers (232 → 54)` orphan row from §9 is still there.
