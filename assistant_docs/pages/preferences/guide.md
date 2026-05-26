# User Preferences (`/preferences/`)

The personal-settings page. Each logged-in user can configure their own preferences here — these are per-user, not tenant-wide. Preferences are saved automatically when changed; there's no Save button.

> **What this page is not:**
> - Not the admin settings page — tenant-level configuration is elsewhere.
> - Not where you connect personal OAuth accounts — that's `/my-connections`.
> - Not where you configure API keys for BYOK — that's `/admin/api-keys` (admin-only).

---

## Page Layout

- **Header** — page title "User Preferences" and a **Reset to Defaults** button (top right).
- **Info banner** — "Preferences are saved automatically when changed." Auto-save means a single setting flip persists immediately.
- **Category cards** — preferences are grouped into cards by category. Categories load dynamically from the preference definitions on the server, so the exact set varies by deployment and feature flags.

Each preference within a category shows its label, current value (checkbox / dropdown / text input depending on type), and usually a short description of what it controls.

## Behavior

- **Auto-save** — any change to a preference is persisted as soon as you make it. No confirmation dialog, no Save button.
- **Feedback notifications** — successful saves and any errors appear as toast notifications in the top-right.
- **Reset to Defaults** — wipes the user's overrides and restores every preference in every category to its default value. There's no per-category reset.

## Common Tasks

### "Change a single setting"
Find the category card, flip the toggle / pick the option / type the value. It saves immediately. If you don't see a confirmation, look top-right for the toast — that's where success/error feedback lands.

### "Reset everything"
Click **Reset to Defaults** at the top of the page. This affects only the current user's preferences.

### "I don't see the preference I'm looking for"
- Some preferences only appear for users with certain roles or when certain features are enabled for the tenant.
- Tenant-level / admin settings are not on this page — those are in admin areas the regular user can't see.
- Personal account connections (Microsoft 365, Google, etc.) are on `/my-connections`, not here.

## What This Page Doesn't Do

- It doesn't manage **tenant-wide** settings — only personal preferences.
- It doesn't manage **OAuth connections to external services** — that's `/my-connections`.
- It doesn't manage **API keys** — that's `/admin/api-keys` and requires admin role.
- It doesn't show **usage / billing** information.
