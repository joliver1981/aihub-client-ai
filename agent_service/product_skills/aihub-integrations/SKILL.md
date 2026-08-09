---
name: aihub-integrations
description: Use when a request involves an external system — SharePoint,
  OneDrive, Shopify, Stripe, Azure Blob, or any connected API — how the
  Integrations feature works, the SharePoint file recipe, and group access.
---

# Integrations — connected external systems

AI Hub's Integrations feature connects external platforms (SharePoint,
OneDrive, Shopify, Stripe, Azure Blob, custom REST APIs). The PLATFORM owns
every credential and token lifecycle — instances are configured on the
Integrations page, never through chat. If a user offers integration
credentials in chat, point them to the Integrations page (see the secrets
skill for the general rule).

## Procedure

1. `list_integrations` — see what's connected AND available to this user.
   Never assume; installs differ.
2. `get_integration_operations(id)` — exact operation keys and parameters.
3. `execute_integration_operation(id, operation, parameters_json)` — runs
   server-side under the platform's auth. Results come back truncated at
   ~2500 chars — report only what you can see, with honest counts.
4. `delete_*` operations require the user's explicit confirmation
   (confirmed=true on the second call).

## SharePoint file recipe (the common ask)

Typical flow to fetch a file when given a site/library/file description:
`lookup_site_by_url` (or `list_sites`) → `list_drives` (document libraries)
→ `search_files` or `list_folder_by_path` → `download_file` /
`download_file_by_path`. A download lands on the SERVER — the user is in a
web browser, so ALWAYS follow it with `offer_file_download` (pass the saved
path from the operation result) and give them the returned chat link; never
quote a server path as the deliverable. For pulling CONTENT INTO THE
PLATFORM, prefer
`download_to_knowledge` (single file) or `import_folder_to_knowledge`
(whole folder) — they land documents straight in the knowledge system where
document search can use them. Newly created SharePoint sites can take
5–15 minutes to appear in `list_sites` (search index) — `lookup_site_by_url`
bypasses the index.

## Who can use what (group access)

- Developers/admins: every integration, always (legacy behavior).
- Regular users: ONLY integrations assigned to at least one of their
  groups. Unassigned integrations are invisible to them — assignment is the
  deliberate opt-in that makes an integration available to regular users.
- Admins assign via `assign_integration_groups(id, group_ids)` — always ask
  WHICH groups first; pass the full list (it replaces); empty list reverts
  to developers/admins only.
- If a regular user asks for an external system and sees no integrations,
  say so honestly and suggest an admin assign one — never work around the
  gate with raw credentials.
