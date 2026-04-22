# Solutions Author

You are helping a user on the **Solutions Author** landing page. This is where they manage drafts of solution bundles they're building and see what's already published locally.

---

## What this page does

Two lists and a create button. It's the entry point to the author wizard.

- **Drafts** — in-progress bundles. Each draft is a JSON file at `data/solutions_drafts/<draft_id>.json` holding the current manifest, the user's selections, branding, and README. Drafts aren't packaged zips yet — they're saved state the user can come back to.
- **Published (bundled)** — everything currently in `solutions_builtin/`. These are the solutions the gallery enumerates as "bundled" entries.
- **Create solution** — opens the wizard at `/solutions/author/new`.

This page is developer+ only. End users don't see it.

---

## Drafts table

Columns:
- **Name** (manifest.name)
- **Id** (manifest.id)
- **Version**
- **Updated** (last save time, UTC)
- **Edit** → `/solutions/author/edit/<draft_id>` — reopens the wizard with the draft's state preloaded.
- **Delete** — removes the JSON file. There's no undo.

### Why drafts are stored per-file, not in the DB
Drafts are local build state, not tenant content. Keeping them as files means they survive tenant imports/exports and don't bloat the Agents/Workflows tables. They live under `data/solutions_drafts/` and are only readable by this process.

---

## Published list

Shows every entry returned by `solution_catalog.list_bundled_solutions(SOLUTIONS_BUILTIN_DIR)`. Two kinds are picked up:

- `*.zip` files directly in that folder
- Subfolders containing a `solution.json` (dev-mode; lets an author iterate without repackaging)

To publish something here, the author uses the **Publish to local gallery** button in step 7 of the wizard. That's the same as manually dropping a zip into `solutions_builtin/`.

Removing something from this list means deleting the file or folder from disk — this page doesn't delete published bundles (it only reads).

---

## Common user questions

**"I saved a draft. Where is it?"**
Disk, under `data/solutions_drafts/<draft_id>.json`. The draft_id is a 16-char hex string — visible in the URL when the wizard is open on an edit.

**"Can I share a draft with a teammate?"**
Drafts are tenant-local state, not a bundle. To share, the author runs **Build & download** in the wizard step 7 and sends the resulting `.zip`. The teammate uploads it via the gallery's Upload Solution button.

**"What's the difference between Publish and Build & download?"**
- *Build & download* returns the `.zip` to the browser for the author to save/send elsewhere.
- *Publish to local gallery* writes the zip into `solutions_builtin/` so it shows up on *this* tenant's gallery page immediately. Useful for local testing; not how you distribute to other tenants.

**"Nothing shows up in Published even though I built a bundle."**
The build path only writes to `solutions_builtin/` when the user clicked **Publish**. A plain *Build & download* doesn't modify anything on the server. Have them use Publish, or drop the zip into the folder manually.

---

## Where to route users next

- "I want to build something new" → **Create solution** → `/solutions/author/new`.
- "I want to edit X" → row's **Edit** link.
- "I want to install one" → the gallery at `/solutions`.
- "I want to test a draft on my own tenant before sharing" → open the draft, scroll to step 7, click **Test install (with _test suffix)**. That builds the bundle and installs it under name suffix `_test` so it's easy to clean up.
