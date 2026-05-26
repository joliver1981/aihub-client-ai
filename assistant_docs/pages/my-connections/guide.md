# My Connections (`/my-connections`)

The **personal accounts** page. This is where each user connects their own Microsoft 365, Google Workspace, or similar accounts so AI agents can act **on their behalf** — sending email as them, reading their calendar, browsing their files, etc.

> **Don't confuse this with two other pages:**
> - **`/connections`** — shared/tenant-wide credentials and connections (databases, API keys, service accounts). Configured by admins/builders, used by tools across all users.
> - **`/mcp_servers`** — admin page for registering and configuring MCP servers themselves. Service-account integrations (no per-user sign-in) live here too.
>
> **This page (`/my-connections`)** is per-user. Each user signs in with their own account, and their tokens are scoped to them.

---

## What This Page Shows

Each card represents an MCP server that supports **per-user OAuth sign-in** (technically: `auth_type='oauth2'` with `grant_type='authorization_code'`). Service-account integrations don't appear here — they're tenant-wide and live on the admin MCP Servers page.

A card displays:
- **Service name and icon** (e.g. Microsoft 365, Google Workspace)
- **Category** (if set)
- **Description** of what the service does
- **Scope pills** — the permissions the integration is asking for (e.g. read mail, send mail, read calendar)
- **Status badge** — "Connected" (green) or "Not connected" (gray)
- **Last connected timestamp** — when the user most recently authorized

If no per-user OAuth servers are configured for the tenant, the page shows an **empty state** with the message "No personal connections available" and a note to ask the administrator to configure one.

---

## Actions

### Connect
For services the user hasn't connected yet. Clicking **Connect** opens an OAuth popup window where the user signs in to the external service (Microsoft, Google, etc.) and approves the requested scopes. When the popup closes, the page refreshes and the card shows as Connected.

**Popup blocker:** If the OAuth window doesn't appear, the browser blocked it. Allow popups for this site, then click Connect again.

### Re-authorize
For services already connected. Use when:
- Scopes have changed and need re-consent.
- The refresh token has expired.
- The user wants to sign in with a different account.

This is the same OAuth flow as Connect.

### Disconnect
Revokes the user's tokens for the service. The user's agents can no longer act on their behalf for that service until they Connect again. A confirmation dialog appears before disconnection — there's no undo, but the user can simply Connect again at any time.

---

## How Per-User Connections Are Used

Once a user is connected to, say, Microsoft 365:
- Any agent that has tools backed by that MCP server (e.g. "send_email", "list_calendar_events") can use **the current user's** tokens when that user is the one chatting.
- The token never moves between users — agent calls run in the context of whoever is signed in.
- If a user without a connection asks the agent to do the same action, the tool call will fail with an auth error (or the agent will be told it can't perform that action).

This is why per-user connections matter for personalization: an "Email me a summary" workflow needs the *user's* mailbox, not a shared one.

---

## Common Questions

### "Why don't I see [Microsoft 365 / Google / etc.] here?"
The administrator hasn't configured an MCP server for it yet (or the configured one is a service-account integration, which doesn't appear on this page). Ask the admin to set up an OAuth 2.0 / Authorization Code MCP server for the service.

### "I connected, but the agent says it can't access my mail."
Three things to check:
1. The connection is still active — return to this page and confirm the card shows "Connected."
2. The scopes match what the agent needs. If they don't, click **Re-authorize** to refresh consent.
3. The agent actually has the matching MCP tool wired in. That's configured on the agent's builder page (`/custom_agent_enhanced`), not here.

### "What happens if I disconnect?"
- Your tokens are revoked for that service.
- Agents can no longer act as you on that service.
- Your connection is removed only — the MCP server itself stays available, and other users' connections are unaffected.
- You can reconnect any time.

### "Is this the same as the Integrations page?"
No. **`/integrations`** is for connecting SaaS platforms (Shopify, QuickBooks, Stripe, etc.) into the **tenant's** workflows — those credentials are typically shared. **`/my-connections`** is your *personal* identity into services like Microsoft 365 and Google Workspace, scoped only to you.

---

## What This Page Doesn't Do

- It doesn't let you change scopes — those are defined on the MCP server config (admin-side).
- It doesn't let you add new services — administrators register MCP servers on `/mcp_servers`.
- It doesn't show shared/tenant connections — those are on `/connections`.
- It doesn't show service-account integrations — those are tenant-wide and live on `/mcp_servers`.
