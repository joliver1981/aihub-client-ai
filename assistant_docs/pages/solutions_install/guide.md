# Install a Solution

You are helping a user on the **install wizard** for a solution from the Solutions Gallery. The wizard walks through four steps: Preview, Credentials, Install, Try it now.

---

## What this page does

The user clicked a tile in the gallery. This page is the path from a packaged `.zip` to real, installed content in the current tenant. Every asset class in the bundle is dispatched to the same import path a user would use manually (agent import, workflow import, etc.) — the installer never bypasses the platform's normal ingestion rules.

If they bail out, nothing is created. If install fails partway through, whatever succeeded so far sticks; the result summary lists what made it and what errored.

---

## Step 1 — Preview

Shows:

- **Manifest header** — name, version, vertical, author.
- **Description.**
- **"What gets installed"** — a strip of chips summarising asset counts (*Agents: 2*, *Workflows: 1*, *Data: schema + 3 seeds, 2 samples*, etc.). An empty strip means the bundle declared nothing — that's a validation warning, but install can still proceed.
- **Conflict preview** — loaded via the `/analyze` endpoint when the page opens. Either:
  - Green "No name conflicts with existing items", OR
  - Yellow list of names that already exist in the tenant, grouped by asset class.
- **README** — rendered markdown from `README.md` inside the bundle.

Nothing is committed at this step.

### If the conflict preview shows items
The warning means *if you pick `overwrite` on step 3, those existing items get replaced; if you pick `rename`, both will coexist; if you pick `skip`, the incoming copies are left out.* Suggest `rename` unless the user explicitly wants to replace something.

---

## Step 2 — Credentials

One input per placeholder the solution declared. Common shapes:

- `DB_SERVER`, `DB_USER`, `DB_PASSWORD` — database connection scaffolds
- API keys for integrations (e.g. `STRIPE_API_KEY`)
- Custom placeholders the author defined

Each input shows the human label and optional help text. If the manifest supplied a `sample_value`, it appears as the placeholder and the user can paste it for demo installs.

### What happens to sensitive values

Any placeholder that lands in the `password`, `token`, or `api_key` fields of an installed connection is *not* stored in plaintext. At install time:

1. A new Local Secret key is generated (pattern: `SOL_<solution_id>_<connection_name>_<field>`).
2. The user's value is written into Local Secrets under that key.
3. The Connections table row stores the reference `{{LOCAL_SECRET:<key>}}`, which is resolved at runtime by `DataUtils.get_db_conn_str`.

No plaintext credential is ever written to the Connections table.

### If a placeholder is blank
If it's marked `required: true` in the manifest, the installer will error for that asset class but continue with the rest. The result summary lists which ones failed.

---

## Step 3 — Install

Two knobs:

- **Conflict mode** — `rename` (default), `skip`, or `overwrite`. Applies per asset class.
- **Name suffix** — optional string appended to every created name. Useful for demo/sandbox installs ("install into my tenant tagged `_demo`").

Pressing **Install** kicks off the dispatcher. Each asset class runs against its existing import route via the Flask test client, carrying the user's session cookie — so the install respects the same tenant scoping and role checks as a manual import.

### What the progress bar actually represents
The bar is indeterminate — the installer processes asset classes sequentially. There's no reliable per-asset fraction, so the bar pulses until the server returns.

---

## Step 4 — Try it now

Shows:

- An install summary (count + per-asset list with status).
- Any errors or warnings in a yellow panel.
- One button per `post_install` action from the manifest:
  - `run_workflow` → opens `/workflows?run=<name>`
  - `chat_with_agent` → opens `/agents?chat=<name>`
  - `open_page` → navigates to the given URL
- A **Back to gallery** link.

Post-install buttons are the author's pick — they're the one-minute "here's what to do now" path.

---

## Common user questions

**"Can I undo an install?"**
Not in one click. Each asset class has its own delete path — workflows from `/workflows`, agents from the agent list, connections from Connections, etc. If the user used a `name_suffix`, the created items are easy to filter and clean up.

**"Why did some things install and others fail?"**
The installer fails soft: one asset class erroring doesn't abort the rest. Check the result summary — errors are listed per-asset. Common causes: missing required credential, bundle references an agent_id that isn't in the tenant, or a downstream route rejected the payload.

**"The credential I entered is wrong — do I reinstall?"**
For connections: faster to fix the Local Secret directly (Settings → Local Secrets) since the connection row already points at the key. For integrations: edit the integration's credentials on the Integrations page. Reinstalling is only needed if an asset itself is broken.

**"What if the bundle wants a database I don't have?"**
The seed loader runs in sandbox mode when no target connection is supplied — SQL is parsed but not executed. Other asset classes don't need a database. So a partial install is possible if the user skips the DB-dependent parts.

---

## Data you should prioritise

If the custom context includes `current_step`, `manifest`, or `conflicts`, use those over the auto-extracted DOM context — they're authoritative for what's actually been fetched from the server.
