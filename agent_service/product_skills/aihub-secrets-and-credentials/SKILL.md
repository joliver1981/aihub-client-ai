---
name: aihub-secrets-and-credentials
description: Use whenever a user mentions, pastes, or is about to need an API
  key, password, token, or any credential — how AI Hub stores secrets and how
  automations reference them safely.
---

# Secrets and credentials in AI Hub

Platform secrets live in the **encrypted Local Secrets store** on this
install. Automations never contain credential values — they declare secret
**names** in their manifest, and the server injects the values at run time.
Hard-coded credentials are rejected at save time.

## When a user hands you a credential in chat

1. Check `list_secret_names` first — it may already exist under an agreed name.
2. Store it immediately with `store_platform_secret` using an
   UPPER_SNAKE_CASE name that says what it is for (e.g. `SENDGRID_API_KEY`,
   `DAYFORCE_SFTP_PASSWORD`). Pick the category: api_keys, credentials,
   database, or other.
3. From that moment refer to it **only by name** — in conversation, in
   manifests, everywhere.
4. NEVER echo the value back, in full or in part; never write it into
   automation code, a skill, a work item summary, or a View.

If the user prefers not to paste it in chat at all, that's the better hygiene:
agree on the name, point them to **Settings → Local Secrets** in the classic
app (`/local-secrets`), and continue once `list_secret_names` shows it.

## Using a stored secret in an automation

Declare the name in the manifest (`save_automation_code` `manifest_json`),
then read it in code through the runtime — the value never appears in source:

    import aihub_runtime as aihub
    key = aihub.secret("SENDGRID_API_KEY")

Note: manifest secret names are UPPERCASED by the platform — always use
UPPER_SNAKE_CASE from the start so the declared name and the stored name
match exactly.

## Gotchas

- A secret "not found" at run time usually means a name mismatch (case or
  underscore) between the manifest and the store — compare against
  `list_secret_names` output character by character.
- Rotating a credential = store the new value under the SAME name
  (`store_platform_secret` updates in place); every automation referencing it
  picks up the new value on its next run, no code change needed.
