# Handoff — Make My Connections deployable (cloud OAuth redirect broker)

**Status:** PHASE 1 BUILT (2026-09-02, both repos, committed locally — see §0a). Still needed from James: WI-0 client secret, a DDL login for migration 020, and approval to push `aihub-api` (auto-deploys the broker). Phase 2 not started.
**Spans two repos:** `C:\src\aihub-client-ai-dev` (on-prem AI Hub) and `C:\src\aihub-api` (cloud API → `https://ai-hub-api.azurewebsites.net`).
**Branching:** do NOT create branches. Commit to `main` in each repo, promptly.
**⚠ `aihub-api` pushes auto-deploy to Azure.** Do not push there until the endpoint is tested locally.

---

## 0a. Implementation status — 2026-09-02 (Phase 1 built)

Everything in Phase 1 (WI-1…WI-6, §11) is implemented and verified as far as this box allows without the two
inputs only James can supply (the Azure client secret; a DDL-capable SQL login). Phase 2 was not started.

### What was built

| WI | Where | Notes |
|---|---|---|
| WI-1 broker | `aihub-api/project/api/mcp_oauth_broker.py`, `mcp_oauth_state.py`, registered in `project/__init__.py` | `GET /api/mcp/oauth/callback`, `POST /api/mcp/oauth/verify` (admin self-test), `GET /api/mcp/oauth/health`. Stateless; per-tenant and per-IP limits via the existing `TenantRateLimiter`; plain escaped error pages; never logs code/state. **Committed locally, NOT pushed** — pushing deploys to Azure. |
| WI-2 pinning | `builder_mcp/routes/mcp_routes.py`, `builder_mcp/agent_integration/oauth_state.py` | `_oauth_registered_redirect_uri()` resolves server override → `OAUTH_REDIRECT_BASE_URL` → `AI_HUB_API_URL` → hard default; `_oauth_redirect_uri()` demoted to the return address; the token exchange repeats the registered URI; signed state; session keyed by nonce with pending entries pruned to 3; pre-flight refusal (409) when no client secret (`OAUTH_REQUIRE_CLIENT_SECRET`, default true). |
| WI-3 role gate | same | `oauth_authorize` is now `@api_key_or_session_required()`; role ≥ 2 enforced inside the `client_credentials` branch only. Live-verified as a role-1 user. |
| WI-4 publish switch | `migrations/020_mcp_servers_available_to_users.sql`, `builder_mcp/agent_integration/mcp_server_visibility.py`, listing + authorize enforcement, admin CRUD | Column missing → visible everywhere (the live state here: the app login has no ALTER). The edit form shows a "migration 020 not applied" note until it is. |
| WI-5 admin UI | `templates/mcp_servers.html` | Redirect URI shown as soon as the grant is per-user; Copy; the Web-platform line; a source line (broker / env / override, return address, tenant id); **Test broker** (`GET /api/mcp/oauth/broker_check` → cloud `verify`); client-secret on-file status; per-server "Redirect URI override" field (stored as `oauth_redirect_uri`); a "Users" badge in the list. |
| WI-6 | `agent_service/email_client.py`, `notification_client.py` | Fallback host → `https://ai-hub-api.azurewebsites.net`, trailing slash stripped. |
| §11 | directory | Microsoft 365 description rewritten around the new flow; the phantom "Microsoft Graph (OAuth)" row removed. |

### Found while building (not in the design)

1. **Every edit of an OAuth server wiped its client secret.** `update_server` ran `DELETE FROM MCPServerCredentials WHERE server_id = ?` and then skipped blank values, so the form's "leave blank to keep existing" was false — a rename, a scope change, or the new publish switch would drop `oauth_client_secret` (and bearer / basic / API-key secrets). Fixed: blank *secret* keys survive the delete; blank non-secret keys still clear. This reframes §3.2 — the secret may have been entered in May and wiped by a later edit. Either way: add it (WI-0) and it now stays.
2. **T2 as written could not pass.** The browser's `localhost` cookie jar is not the `10.0.0.7` one, so a provider return to `localhost` has no session. The on-prem callback now **self-brokers**: reached on an origin other than the signed return address, it 302s there with code+state (signature-bound, so not an open redirect). This also makes the per-server override usable with no cloud at all. Live-verified.
3. **Reflected XSS** in the old callback (`<pre>{error}</pre>` from `?error=`). All OAuth pages now render through one escaped helper; provider rejections are still shown verbatim, escaped.
4. **Cloud helper correction.** `email_receive_routes.py` is never registered; the live lookup is `views.py:6825` on `Tenants.LicenseKey`. The broker keys the other way on the same table (`SELECT LicenseKey FROM Tenants WHERE TenantId = ? AND IsActive = 1`).
5. **Tenant id on-prem** comes from the local `SESSION_CONTEXT(N'TenantId')` after `sp_setTenantContext` (1 here, equal to the cloud's), falling back to `agent_email_routes.get_numeric_tenant_id()`. A wrong id fails closed at the broker; **Test broker** makes that visible before any user clicks Connect.
6. The main app runs under `aihub2.1` (`python wsgi.py`), not `aihubant` (§16 corrected). Restart only the app: stop the owner of :5001 and its `cmd /k` parent, then start `cmd /k "title AIHub-DEV Main App && call C:\Users\james\miniconda3\Scripts\activate.bat aihub2.1 && python wsgi.py"` from the tree.
7. Migration tests: `MCPServers` and `Groups` were added to `EXTERNAL_TABLES` (the 013/016 FK checks already failed for that reason). The 014→016 numbering-gap failure predates this work and was left alone.

### Verification evidence (2026-09-02, this box, app restarted from this tree)

| Check | Result |
|---|---|
| `tests_v2/unit/test_oauth_state.py`, `tests_v2/api/test_mcp_oauth_routes.py`, `tests_v2/api/test_my_connections_visibility.py`, `tests_v2/migrations/` | 258 pass; the only failure is the pre-existing 014→016 gap |
| `aihub-api/tests/test_mcp_oauth_broker.py` — real app object, tenant lookup patched: T6 refusals (no redirect), bounce, verify, rate limits, known-answer vector | 19 pass |
| `tests_v2/live/my_connections_live_check.py` — admin + a temporary role-1 user against :5001 | 14 PASS / 1 SKIP (`broker_check`: cloud returns 404 until deployed) |
| `tests_v2/live/mcp_servers_ui_check.py` — headless Chromium, admin page edit modal | 13 PASS, no page/console errors |
| pack 15 `runner.py --only mcp --skip-wf14 --skip-llm` | CLEAN |
| `tests_v2/unit` + `api` + `security` (3,466 pass) | 62 failures, all pre-existing and in unrelated modules (ops routes, CC chat security, Excel extraction, …); two modules fail to collect for a missing `claude_agent_sdk` in `aihub2.1` |

### What James needs to do, in order

1. **WI-0** — create a client secret on app registration `fd11daaa-…`, paste it into server 30, Save. The status line under the field turns green ("A client secret is on file"). Register `https://ai-hub-api.azurewebsites.net/api/mcp/oauth/callback` under the registration's **Web** platform (the Copy button gives the exact string).
2. **Approve the `aihub-api` push** (one additive commit). After the deploy: `python tests/test_mcp_oauth_broker_live.py` (no credentials → the T6 matrix against Azure), then **Test broker** on the MCP Servers page must say "Broker verified this installation (tenant 1)".
3. **Apply migration 020** with a DDL-capable login (`TenantAppUser` cannot). Until then every enabled OAuth server is visible and the switch is inert — the form says so.
4. Then T3/T5/T7 for real: a role-1 user on a second machine → Connect → Microsoft → back → "✔ Connected" → a GeneralAgent with `allow_personal_connections=1` reads that user's mail.

---

## 0. TL;DR

My Connections — per-user OAuth so an agent can act as *you* (your mailbox, your calendar) — **cannot work for a single real user on a client install today.** Three independent defects, all in §1:

1. The OAuth redirect URI is derived from the browser's `Host` header, so no provider will accept it.
2. The **Connect** button is gated to role ≥ 2, so regular users get a 403.
3. There's no way for an admin to control *when* a server becomes visible to users.

**Phase 1 (this document's priority — Microsoft only):** fix all three. The redirect fix is one new cloud endpoint — `https://ai-hub-api.azurewebsites.net/api/mcp/oauth/callback` — which every customer's IT registers in their own app registration. The cloud 302s the browser back to the on-prem install; credentials and tokens never leave the customer's network; no reverse proxy, no on-prem certificate, no inbound firewall rule.

**Phase 2 (§12–§14):** Google and Slack as personal connections. The OAuth plumbing is shared and needs no further work; each provider needs a small in-app MCP server and a directory row.

**Start with WI-0 (§3.4)** — five minutes, no code, and nothing below is meaningful until it's green.

---

## 1. The three defects

### 1.1 Redirect URI is derived from the request Host

[`builder_mcp/routes/mcp_routes.py:811`](../builder_mcp/routes/mcp_routes.py):

```python
def _oauth_redirect_uri() -> str:
    return url_for('mcp.oauth_callback', _external=True)
```

`_external=True` builds from the incoming `Host` header. Browse at `http://10.0.0.7:5001` and the provider receives `http://10.0.0.7:5001/api/mcp/oauth/callback`. All three call sites route through this function — `:873` authorize, `:916` token exchange, `:821` the admin UI's hint.

Observed: `AADSTS50011: The redirect URI ... does not match the redirect URIs configured for the application`.

**And that URI can never be registered.** Entra accepts `http://` only for loopback (`localhost`, `127.0.0.1`, `[::1]`); everything else must be `https`. Google and Slack have the same rule. Meanwhile client installs are HTTP-only: [`run_app.py:10`](../run_app.py) is `waitress.serve(app, host=host, port=port, threads=threads)` with no `ssl_context`, and `setup_ssl.bat` generates a cert that **nothing references** — not the installer, not `run_app.py`. It is vestigial.

Net effect: only someone at the server console browsing `localhost` can authorize anything.

### 1.2 The Connect button is Developer-gated

[`templates/my_connections.html:135`](../templates/my_connections.html) opens `/api/mcp/oauth/authorize/<server_id>`. That route carries `@api_key_or_session_required(min_role=2)` ([`mcp_routes.py:825`](../builder_mcp/routes/mcp_routes.py)), and per [`role_decorators.py:540`](../role_decorators.py) `min_role` gates **session** auth at role ≥ 2 (1=User, 2=Developer, 3=Admin).

The page is `@login_required` — any role. So a regular user sees the card, clicks Connect, and gets 403.

On the dev box: user 13 is `admin`, **role 3** — every test to date has been from an admin seat, which is why this has never surfaced. Role distribution here is **8 users at role 1**, 2 at role 2, 4 at role 3. Those eight are precisely the audience the feature exists for.

### 1.3 No admin control over visibility

Visibility is implicit — see §2.1. Creating an `oauth2` + `authorization_code` server makes it **instantly visible to every user in the tenant**, including while it is still being configured. There is no staging state, so a half-configured server is live and fails for everyone who clicks it. `MCPServers` has no visibility column (schema confirmed live).

---

## 2. What already works — do not rebuild

The "admin enables it, user connects it" model is built end to end.

**Admin surface** — `/mcp_servers`, OAuth block at [`templates/mcp_servers.html:204-278`](../templates/mcp_servers.html): Auth Type, **Grant Type**, Auth/Token Endpoint, Scope, Client ID, Client Secret (write-only; blank on edit = keep existing), Audience, a read-only **Redirect URI hint** (`oauthRedirectHint`, fed by `GET /api/mcp/oauth/redirect_uri`), an Authorize button, and a per-user counter.

**User surface** — `/my-connections` ([`builder_mcp/routes/my_connections_routes.py`](../builder_mcp/routes/my_connections_routes.py)).

**Token lifecycle** — [`oauth_manager.py`](../builder_mcp/agent_integration/oauth_manager.py): PKCE, encrypted per-user storage in `MCPUserTokens`, auto-refresh under a per-`(server, user)` lock, `client_secret` sent when present.

**Agent consumption** — Flow B at [`mcp_agent_tools.py:70`](../builder_mcp/agent_integration/mcp_agent_tools.py): any agent with `allow_personal_connections=1` picks up the calling user's authorized servers.

### 2.1 How a server reaches My Connections (needed context)

Two gates in `list_my_connections`; there is no "publish" control today:

1. SQL: `WHERE auth_type = 'oauth2' AND enabled = 1`
2. Python: `_load_server_config(sid)['oauth_grant_type'] == 'authorization_code'` — grant type lives in the encrypted credentials table, so it cannot be filtered in SQL

Servers with `auth_type='none'` or `client_credentials` never appear. There is **no per-user or per-group scoping** — every signed-in user sees every qualifying server. WI-4 (§8) adds the missing publish control.

---

## 3. Dev-box state

```
MCPServers
  1   AI Hub Test MCP Server                            remote / none    / enabled
  5   Test MCP Server                                   remote / none    / enabled
  29  Microsoft Learn (Test)                            remote / none    / enabled
  30  EveriAI Graph — Step 1: OAuth Credentials (TEST)  remote / oauth2  / enabled
      url    http://127.0.0.1:5001/api/internal/mcp/graph
      grant  authorization_code
      scope  User.Read Mail.Read Mail.Send Calendars.Read offline_access
      Azure app id  fd11daaa-13d1-4665-a8e2-b50a954521fc

MCPUserTokens   server 30, user 13 — tokens from 2026-05-20, access token EXPIRED ~104 days
MCP Gateway     :5071 healthy      The Agent  :5111 healthy
User            id 13 = 'admin', role 3
```

### 3.1 Why only one card shows

Servers 1, 5, 29 are `auth_type='none'` → filtered at gate 1 (§2.1). Expected, not a bug.

### 3.2 Why the dev box fails with AADSTS7000218

`MCPServerCredentials` for server 30 holds exactly five keys — `oauth_auth_endpoint`, `oauth_client_id`, `oauth_grant_type`, `oauth_scope`, `oauth_token_endpoint`. **There is no `oauth_client_secret` row and there never was one** (the admin UI's blank-means-keep behavior cannot have erased it).

Yet the exchange succeeded on 2026-05-20 — tokens exist. `exchange_authorization_code` sends a secret only `if client_secret:`, so that request went out **without** one and Entra accepted it. A confidential client cannot do that. Conclusion: **app registration `fd11daaa-…` was a public client in May and is now treated as confidential.** Our code did not change — `oauth_manager.py` and `mcp_routes.py` were last touched in `de2c2c1` (2026-05-26), the commit that created the subsystem.

> **Update 2026-09-02 (build):** a second explanation surfaced — `update_server` wiped `oauth_client_secret` on every edit of the server (§0a, item 1). The two are not exclusive; the remedy is the same: add the secret, which now survives edits.

### 3.3 The target model is confidential

Under this design the customer's IT owns the app registration and creates a real client secret, stored encrypted in their own database. So the fix is to **add the secret**, not to revert Azure to a public client. In the portal, the redirect URI belongs under the **Web** platform.

### 3.4 ⚠ WI-0 — do this first (5 minutes, no code)

1. Azure portal → app registration `fd11daaa-…` → Certificates & secrets → New client secret → copy the **Value** (shown once).
2. `/mcp_servers` → edit server 30 → paste into Client Secret → **Save**. Blank saves are a no-op ([`mcp_servers.html:955`](../templates/mcp_servers.html)).
3. Browse `http://localhost:5001/my-connections` — **the whole flow on localhost**; the PKCE verifier lives in a host-scoped session cookie, so switching hosts mid-flow gives "OAuth state mismatch".
4. Connect. Confirm a token is stored and a GeneralAgent with `allow_personal_connections=1` can call `list_recent_emails`.

This proves the app registration, scopes, consent, PKCE, storage and refresh all work, so any later failure is attributable to new code rather than a dead grant.

---

## 4. The design

### 4.1 Flow

```
 1. User clicks Connect on /my-connections (browsing http://10.0.0.7:5001)
 2. On-prem: generate PKCE verifier + nonce; stash {server_id, user_id, verifier}
    in the Flask session; build a SIGNED state carrying the return address
 3. On-prem: 302 the browser to Microsoft with
       redirect_uri = https://ai-hub-api.azurewebsites.net/api/mcp/oauth/callback
 4. User signs in and consents
 5. Microsoft: 302 the browser to the CLOUD with ?code=...&state=...
 6. Cloud broker: verify the signed state, then 302 the browser to
       http://10.0.0.7:5001/api/mcp/oauth/callback?code=...&state=...
 7. On-prem: match state to the session entry, then POST the token exchange with
       redirect_uri = https://ai-hub-api.azurewebsites.net/api/mcp/oauth/callback
       + client_id + client_secret + code_verifier
 8. Tokens stored on-prem in MCPUserTokens. Done.
```

### 4.2 Why this satisfies every constraint

| Constraint | How |
|---|---|
| No reverse proxy, no on-prem TLS | The registered URI is Everi's HTTPS endpoint |
| No inbound firewall rule | **The cloud never contacts the install.** Only the browser does — and it is already on the LAN, having just used the app |
| No new outbound dependency | On-prem makes **zero** calls to the cloud in this flow; the broker is a browser bounce, not a proxy |
| Customer IT owns the registration | Their client_id/secret stay encrypted in their own DB; the cloud never sees them |
| Tokens stay on-prem | The broker sees only a single-use, PKCE-bound code, useless without the verifier |
| One URL for all customers | The same string forever — Everi's endpoint |
| Google and Slack later | Identical HTTPS-redirect requirement, identical fix |

### 4.3 The return address

`_oauth_redirect_uri()` is **not deleted — it is demoted.** It stops being the redirect URI and becomes the *return address* the broker bounces back to. Host-derivation is now exactly right: it guarantees the user lands back on the origin they logged in on, so the session cookie holding the PKCE verifier is still valid.

### 4.4 Signed state (security-critical)

An unvalidated return URL is an **open redirect**. Bind it with the secret both sides already share: the tenant API key. On-prem sends `X-API-Key: os.getenv('API_KEY')` to the cloud today ([`agent_service/email_client.py:31`](../agent_service/email_client.py)); the cloud validates it via `get_tenant_from_api_key` ([`project/api/email_receive_routes.py:50`](file:///C:/src/aihub-api/project/api/email_receive_routes.py)). Same value.

**On-prem builds:**

```
payload = {"t": <tenant_id>, "r": <return_url>, "n": <nonce>, "e": <unix expiry>}
b       = base64url(json(payload))            # unpadded
sig     = base64url(HMAC_SHA256(key=API_KEY, msg=b))
state   = f"{b}.{sig}"
```

**Cloud verifies in this order**, refusing with a plain error page on any failure:

1. Split on the last `.`; reject malformed input.
2. Decode `payload`; require all four fields.
3. Look up the tenant by `t`, fetch its API key. Unknown tenant → refuse.
4. Recompute the HMAC and compare with `hmac.compare_digest`. **Never** `==`.
5. Reject if `e` is past (issue with a 10-minute TTL).
6. Validate `r`: scheme in `{http, https}`, non-empty host, **no** `userinfo@`, no control characters.
7. Only then `302` to `r` with `code` and `state` appended verbatim.

Implementer notes:
- The cloud needs a **tenant_id → api_key** lookup. Today's helper goes the other way; the table has both columns, so it's a one-line query. Confirm before designing around it.
- `state` becomes ~300 chars; all three providers echo it verbatim and tolerate this.
- Use the **nonce**, not the whole blob, as the session key: `session[f'mcp_oauth_state_{nonce}']`.
- The broker is **stateless** — no new table, no pre-registration.

### 4.5 Config

| Name | Where | Default | Purpose |
|---|---|---|---|
| `OAUTH_REDIRECT_BASE_URL` | on-prem `.env` | `AI_HUB_API_URL`, then `https://ai-hub-api.azurewebsites.net` | Base for the registered redirect URI |
| `oauth_redirect_uri` | per-server credential key | unset | Full override for one server (air-gapped, or a customer insisting on their own HTTPS host). Slots into the existing `_load_server_config` dict — no schema change |

Resolution: per-server override → `OAUTH_REDIRECT_BASE_URL` + `/api/mcp/oauth/callback` → hard default.

**⚠ `AI_HUB_API_URL` in `.env` has a trailing slash.** Strip before joining, as `email_client._cloud_base()` does.

---

# PHASE 1 — Microsoft

Everything in this phase is required before one real user can connect anything.

## 5. WI-1 — Cloud broker (`C:\src\aihub-api`)

Add `GET /api/mcp/oauth/callback` to the existing `api_blueprint` ([`project/__init__.py:79`](file:///C:/src/aihub-api/project/__init__.py)), beside `email_receive_routes.py`.

- **No `@require_api_key`.** The provider redirects a browser here; there is no header to carry. Authenticity comes from the signed state (§4.4), which is stronger for this purpose.
- Accept `code`, `state`, `error`, `error_description`.
- On a provider error, render a plain error page — do **not** bounce an error to the return URL.
- On success, verify per §4.4, then `302` to the return URL with `code` and `state` appended.
- Log tenant id, outcome, and rejection reason. **Never log `code`, `state`, or any token.**
- Rate-limit by tenant — [`project/api/rate_limiter.py`](file:///C:/src/aihub-api/project/api/rate_limiter.py) already exists.

**Test locally before pushing.** Pushing auto-deploys to Azure.

## 6. WI-2 — On-prem redirect pinning

In [`builder_mcp/routes/mcp_routes.py`](../builder_mcp/routes/mcp_routes.py):

1. Add `_oauth_registered_redirect_uri(server_id)` implementing §4.5. This is what goes to the provider and into the token exchange.
2. Keep `_oauth_redirect_uri()` as the **return address** (§4.3).
3. `oauth_authorize` (`:824`): build the signed state (§4.4); key the session on the nonce; pass the registered URI to `build_authorize_url`.
4. `oauth_callback` (`:882`): unchanged validation, but pass the **registered** URI to `exchange_authorization_code`. Providers require the token-exchange `redirect_uri` to match the authorize request exactly — **the most likely bug in this work item.**
5. `oauth_redirect_uri` (`:816`): return the **registered** URI so the admin UI shows IT the right string.
6. **Pre-flight check.** `oauth_authorize` already loads the config to read `grant_type`. In the same breath, if the grant is `authorization_code` and no `oauth_client_secret` is stored, return a clear error — *"this server has no client secret configured; an admin must add one on the MCP Servers page"* — instead of bouncing the user to the provider to fail there. This is the exact round trip that cost time on 2026-09-02.

`oauth_manager.py` needs **no changes** in Phase 1 — it already takes `redirect_uri` as a parameter and sends `client_secret` when present.

## 7. WI-3 — Let regular users connect (defect §1.2)

`/api/mcp/oauth/authorize/<server_id>` must be reachable by **any authenticated user** for `authorization_code` servers, while staying admin-only for `client_credentials` (which forces a tenant-wide service-account token fetch — genuinely an admin action).

The route already branches on `grant_type` early, so the cleanest shape is to **lower the decorator to `@login_required`-equivalent and enforce the admin requirement inside the `client_credentials` branch**, rather than splitting into two routes.

Requirements:
- `authorization_code`: any authenticated user may start the flow **for a server published to users** (WI-4). The flow already binds tokens to `current_user.id`, so a user can only ever authorize themselves.
- `client_credentials`: role ≥ 2 as today.
- An unpublished server: refuse for non-admins even by direct URL (see WI-4).
- `oauth_callback` stays undecorated — the provider redirects the browser there and state validation is the control.

**Test as a role-1 user, not as admin.** This defect exists precisely because it is invisible from an admin seat.

## 8. WI-4 — Per-server "Available to users" switch (defect §1.3)

Give the admin explicit control over when a configured server becomes visible on My Connections.

**Schema** — new migration under `migrations/`:

```sql
ALTER TABLE MCPServers
  ADD available_to_users BIT NOT NULL
      CONSTRAINT DF_MCPServers_available_to_users DEFAULT 0;

-- Preserve today's behavior for existing installs: anything currently
-- eligible stays visible. New servers start unpublished.
UPDATE MCPServers SET available_to_users = 1
  WHERE auth_type = 'oauth2' AND enabled = 1;
```

**⚠ Read paths must tolerate a missing column.** A migration has previously been written but not applied here because the deploy login lacked DDL rights. Follow the existing precedent at [`mcp_agent_tools.py:76`](../builder_mcp/agent_integration/mcp_agent_tools.py) — try/except around the read — and **fall back to visible**, so a failed migration preserves today's behavior instead of hiding everyone's working connections.

**Enforce in two places.** Filtering only the listing is not enforcement:
- `list_my_connections` ([`my_connections_routes.py`](../builder_mcp/routes/my_connections_routes.py)) — add to the WHERE clause.
- `oauth_authorize` — a non-admin must not be able to authorize an unpublished server by direct URL. Admins **may**, so they can test before publishing.

**UI** — a toggle in the `/mcp_servers` OAuth block:
- Label: *"Available to users on My Connections"*; helper text: *"Users can connect their own account once this is on."*
- Visible/enabled only when Grant Type is `authorization_code` (it is meaningless otherwise).
- Reflect state in the server list so an admin can see at a glance what is published.

**Not in scope:** per-group scoping. The boolean doesn't foreclose it — the natural extension mirrors integrations' `assigned_group_ids` ([`agent_service/integration_tools.py`](../agent_service/integration_tools.py)) if it's ever wanted.

## 9. WI-5 — Admin UI polish (`templates/mcp_servers.html`)

The Redirect URI hint (`oauthRedirectHint` / `oauthRedirectUri`, ~`:266`) already calls `GET /api/mcp/oauth/redirect_uri`, so WI-2 step 5 fixes its value for free. Improve presentation:

- Label it as the string to hand IT for the app registration, with a copy button.
- One line: register it under the **Web** platform (not "Mobile and desktop") — this is a confidential client.
- Show it as soon as Grant Type is `authorization_code`, not only after save; IT usually needs it before the record exists.

## 10. WI-6 — Cleanup

Two modules fall back to a **hostname that does not exist** when `AI_HUB_API_URL` is unset — a silent misroute on any install with an incomplete `.env`:

- [`agent_service/email_client.py:28`](../agent_service/email_client.py) — `"https://api.aihub.everiai.ai"`
- [`notification_client.py:64`](../notification_client.py) — same string

Point both at `https://ai-hub-api.azurewebsites.net`, or make the fallback fail loudly.

## 11. Directory — Microsoft only in Phase 1

[`mcp_routes.py:728`](../builder_mcp/routes/mcp_routes.py). The **Microsoft 365** entry is real and working: `authorization_code`, correct Entra endpoints, pointing at AI Hub's own in-process Graph MCP server (`_internal_graph_url()`). Phase 1 changes:

- Confirm it end to end after WI-1/WI-2, and update its `description` to mention the redirect URI now comes from the hint box.
- **Remove `Microsoft Graph (OAuth)`.** Its URL `https://graph.microsoft.com/mcp` does not exist, and having two Microsoft OAuth rows invites an admin to pick the broken one.
- Leave `Microsoft Learn` (real URL, `auth_type: none`, never appears in My Connections).
- Leave `Salesforce` / `GitHub` alone — out of scope, invented URLs.

---

# PHASE 2 — Google and Slack

Do not start until Phase 1 is live-verified with a real role-1 user.

## 12. What is shared vs. per-provider

**Already done, no per-provider work:** the redirect broker, PKCE, encrypted per-user token storage, refresh with locking, My Connections UI, the visibility switch, per-user scoping, Flow B agent loading. Google and Slack are blocked by the *same* HTTPS-redirect rule Phase 1 fixes, so they inherit the fix.

**Needed per provider:** a small in-app MCP server plus a directory row. [`builder_mcp/servers/graph_tools.py`](../builder_mcp/servers/graph_tools.py) is the entire pattern — **181 lines, 4 tools** (`get_my_profile`, `list_recent_emails`, `send_email`, `list_upcoming_meetings`), served via [`mcp_internal_routes.py`](../builder_mcp/routes/mcp_internal_routes.py) (loopback-only, streamable-http JSON-RPC, bearer passed through to the provider).

**Do not look for a vendor-hosted MCP endpoint.** None exists for Microsoft, Google or Slack; the current directory's `graph.microsoft.com/mcp` and `slack.com/api/mcp/v1` rows are placeholders with invented URLs.

Both providers need two small additions to shared code — build them as **generic mechanisms, not provider branches**:

- **`oauth_extra_authorize_params`** — a per-server config key merged into `build_authorize_url`'s query string. Both providers need it (below).
- **A token-response normalization hook** — `_store_token_response` requires a top-level `access_token`; Slack does not comply. A per-server response-shape hint or a small provider adapter. **Do not write Slack-specific branches into shared code.**

## 13. Google

- New `builder_mcp/servers/google_tools.py` + a route beside the Graph one.
- v1 tools: `get_my_profile`, `list_recent_emails` (Gmail), `send_email`, `list_upcoming_meetings` (Calendar).
- `oauth_auth_endpoint`: `https://accounts.google.com/o/oauth2/v2/auth`; `oauth_token_endpoint`: `https://oauth2.googleapis.com/token`.
- Scopes: `openid email profile https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/calendar.readonly`.
- **⚠ Google only returns a refresh token when the authorize request carries `access_type=offline`** (plus `prompt=consent` to re-issue one). Without it, connections silently stop working after ~1 hour with no refresh path — the failure looks like a token bug days later. This is what `oauth_extra_authorize_params` is for.
- Customer IT registers the same broker URL as an Authorized redirect URI in their Google Cloud project.

## 14. Slack

The existing directory row is `auth_type: 'bearer'` at a non-existent URL, so it can never appear in My Connections. **Rebuild it, don't confirm it.**

- `auth_type: 'oauth2'`, `oauth_grant_type: 'authorization_code'`.
- `oauth_auth_endpoint`: `https://slack.com/oauth/v2/authorize`; `oauth_token_endpoint`: `https://slack.com/api/oauth.v2.access`.
- **⚠ Non-standard token response.** For user tokens the access token arrives at `authed_user.access_token`, not top level — [`_store_token_response`](../builder_mcp/agent_integration/oauth_manager.py) raises `"OAuth token response missing access_token"` on that shape. Use the normalization hook from §12.
- **⚠ Slack user scopes go in `user_scope`, not `scope`** — the `oauth_extra_authorize_params` mechanism again.
- Good news: Slack tokens do not expire by default, and `_is_token_valid` already returns `True` when no expiry is stored (*"opaque token, no expiry info"*). No refresh path needed unless the customer enables token rotation.
- In-app MCP server with a small v1 surface (`list_channels`, `search_messages`, `post_message` — `post_message` is a write, so the default-closed write gate in the companion handoff applies).

---

## 15. Test plan

| # | Test | Phase | Needs |
|---|---|---|---|
| **T0** | §3.4 — add the client secret, authorize at `localhost`, confirm a GeneralAgent reads real mail | 1 | nothing |
| **T1** | Control: browse by `10.0.0.7`, confirm `AADSTS50011`. Already observed 2026-09-02 | 1 | nothing |
| **T2** | **Cheapest proof of the pinning fix.** Set `OAUTH_REDIRECT_BASE_URL=http://localhost:5001`, browse the app at `http://10.0.0.7:5001`, authorize. Must succeed — the provider sees the localhost URI regardless of the browser's host, and the redirect lands on the server's own loopback. **Validates pinning with no TLS and no cloud work.** | 1 | WI-2 |
| **T3** | **As a role-1 user** (not admin): the full connect flow. This is the test defect §1.2 exists for | 1 | WI-3 |
| **T4** | Visibility switch: off → card hidden AND direct-URL authorize refused for a non-admin, permitted for an admin. On → both work. Column-missing path falls back to visible | 1 | WI-4 |
| **T5** | Broker end to end from a second machine, as a role-1 user on the LAN | 1 | WI-1 + WI-2 |
| **T6** | Broker security: tampered signature; expired `e`; unknown tenant; `r` rewritten to an attacker host; `r` with `userinfo@`; missing params. **Every one must refuse and must not redirect.** Make these permanent tests | 1 | WI-1 |
| **T7** | Two users authorize the same server; each gets their own mailbox. Pairs with the cross-user gateway bleed test in the companion handoff | 1 | WI-1 + WI-2 |
| **T8** | Google: refresh still works after 1h (proves `access_type=offline` landed). Slack: nested token shape stored correctly | 2 | §13, §14 |
| **T9** | Platform regression pack `test_human/15_Platform_Regression` before any build | both | — |

---

## 16. Gotchas

- **Token-exchange `redirect_uri` must equal the authorize `redirect_uri` exactly.** Most likely bug in WI-2.
- **Session cookie is host-scoped.** The return address must be the origin the user started from, or the PKCE verifier is unreachable → "OAuth state mismatch".
- **`SameSite`.** The return from the cloud is a cross-site top-level GET. Flask's default (unset → browser-treated as Lax) permits cookies on that navigation. If anything sets `SESSION_COOKIE_SAMESITE='Strict'`, this breaks — verify.
- **Cookies over HTTP** cannot be `Secure`. Already the case; do not "fix" it.
- **Migrations may not apply** — the deploy login has lacked DDL rights before. Every read of `available_to_users` must tolerate a missing column and fall back to visible.
- **Trailing slash** on `AI_HUB_API_URL`.
- **`aihub-api` pushes auto-deploy to Azure.**
- **Restart to pick up changes:** main app (`aihub2.1`, `python wsgi.py` — see §0a item 6 for the app-only restart), The Agent (`agent_service/start_agent_service_dev.bat`, env `aihub-agent`). Never pipe the V3 restart launcher from an agent shell.
- **Air-gapped installs** cannot reach the broker. The per-server `oauth_redirect_uri` override is their escape hatch and puts TLS back on them. Document; do not solve in v1.

---

## 17. Files

**`C:\src\aihub-api`**

| Path | Action |
|---|---|
| `project/api/` (new module or into `views.py`) | WI-1 broker endpoint |
| `project/api/email_receive_routes.py` | read — `get_tenant_from_api_key` / `require_api_key` pattern |
| `project/api/rate_limiter.py` | read — reuse |
| `project/__init__.py` | read — blueprint registration |

**`C:\src\aihub-client-ai-dev`**

| Path | Action |
|---|---|
| `builder_mcp/routes/mcp_routes.py` | WI-2, WI-3, WI-4 (enforcement), WI-5, §11 |
| `builder_mcp/routes/my_connections_routes.py` | WI-4 (listing filter) |
| `migrations/` | WI-4 (new migration) |
| `templates/mcp_servers.html` | WI-4 (toggle), WI-5 |
| `builder_mcp/agent_integration/oauth_manager.py` | **Phase 2 only** — extra authorize params + token-shape hook. No change in Phase 1 |
| `builder_mcp/servers/graph_tools.py` | read — the per-provider template |
| `builder_mcp/servers/google_tools.py`, `slack_tools.py` | new (Phase 2) |
| `builder_mcp/routes/mcp_internal_routes.py` | Phase 2 — mount new providers |
| `agent_service/email_client.py`, `notification_client.py` | WI-6 |
| `.env` | `OAUTH_REDIRECT_BASE_URL` |

---

## 18. Standing directives

- **No git branches.** Commit to `main` in both repos, promptly. Ask before large pushes. `aihub-api` auto-deploys.
- Services run from **this** tree via manual cmd windows — commit immediately.
- **Additive and reversible.** New behavior behind config that reverts cleanly.
- **Honesty over silent success.** Never raise into an agent loop; empty results are information; surface provider rejections verbatim; verify mutations by read-back.
- **Test as the least-privileged user who is supposed to use the feature.** Defect §1.2 shipped because every test was from an admin seat.
- Another agent may be working in this tree — verify `git diff` is entirely yours and re-check `origin/main..HEAD` before pushing.

## 19. Related

- [`docs/the-agent-my-connections-handoff.md`](the-agent-my-connections-handoff.md) — giving **The Agent** tools to use these connections. **Blocked on Phase 1:** without a usable authorize flow there is nothing for The Agent to bridge to. Its §6.4 write-gate doctrine (writes default-closed) applies to every provider added here, including Slack's `post_message`.
