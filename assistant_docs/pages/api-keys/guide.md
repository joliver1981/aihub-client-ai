# API Keys Configuration — BYOK (`/admin/api-keys`)

Configure **Bring-Your-Own-Key** (BYOK) credentials for OpenAI and Anthropic. When enabled, AI Hub uses your configured keys for AI calls instead of the platform's shared keys — typically used to remove rate/usage limits, route spend to your own billing, or comply with data-residency requirements.

> **Admin-only page.** Requires role 3 (admin). Users with lower roles get a 403.

> **Where the page lives:** The URL is `/admin/api-keys`. The page name extracted from this URL is `api-keys` (with a hyphen). This guide lives at `assistant_docs/pages/api-keys/guide.md` accordingly.

---

## Page Layout

### Header
- Title: "API Keys Configuration"
- Subtitle: "Use your own OpenAI and Anthropic API keys"
- **Status badge** (top-right) — shows the overall BYOK status (loading / enabled / disabled / partially configured).

### Info banner
A short explanation of BYOK and an important assurance:

> Your keys are stored **locally on this machine using encrypted storage** and are **never transmitted to external servers**.

This is the LocalSecrets / encrypted credential store on the AI Hub host, not a cloud secret manager.

### Left column — Key Configuration

#### Master Toggle: Enable BYOK
- A single switch that turns BYOK on or off globally for the tenant.
- When OFF, AI Hub uses the platform/system keys regardless of what's configured below.
- When ON, AI Hub uses your configured keys (and only the providers you've configured — partial configuration is allowed).

#### OpenAI API Key card
- Status badge: "Not Configured" / "Configured" / etc.
- Input for the OpenAI API key (typically starts with `sk-`).
- Validation / save buttons.
- Help text on where to obtain the key (OpenAI Platform → API Keys).

#### Anthropic API Key card
Same structure as the OpenAI card, for Claude API keys.

### Right column
Typically shows current usage, model defaults, or BYOK guidance. Exact contents may vary by deployment.

## Common Tasks

### "Enable BYOK and use my own OpenAI key"
1. Paste the key into the OpenAI API Key card; save.
2. Toggle **Enable BYOK** to ON at the top.
3. Verify the status badge updates to indicate BYOK is in effect.

### "Switch off BYOK temporarily"
Toggle **Enable BYOK** to OFF. Your stored keys remain (encrypted) but are not used.

### "Replace an existing key"
Open the relevant provider card, paste the new value, save. The old key is overwritten in the encrypted store.

### "I'm not an admin and I need to set this up"
You can't — this page requires role 3. Ask your AI Hub administrator.

## Security Notes

- Keys are stored in AI Hub's **encrypted local secrets store** on the server host (not in the database, not in `.env`).
- Keys are never echoed back in API responses — the page shows whether a key is configured but not the value.
- Disabling BYOK doesn't delete the stored keys; to remove a key, clear and save the field explicitly.

## Related Pages

- **`/connections`** — for database / API credentials used by tools (separate from BYOK model keys).
- **`/local_secrets`** — the broader local secrets store for service-account credentials.

## What This Page Doesn't Do

- It doesn't manage **per-user** keys — BYOK is tenant-wide.
- It doesn't manage **MCP server credentials** — those are on `/mcp_servers`.
- It doesn't show **token usage** or billing — separate admin surfaces.
- It doesn't manage keys for any provider other than OpenAI and Anthropic.
