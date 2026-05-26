# Live Test Plan 37 — Solutions Gallery

End-to-end exercise of the Solutions Gallery feature: browse → preview →
install → verify → optional uninstall.

## Pre-conditions

- AI Hub main app running on `HOST_PORT` (default 5001).
- Logged in as a user with **Developer** role (role >= 2).
- Feature flag `solutions_enabled` is **on**. Verify in
  `feature_flags.py` or via `/api/feature_flags`.
- At least one bundled solution under `SOLUTIONS_BUILTIN_DIR` (default
  `solutions_builtin/`). If none, drop a small `.zip` containing
  `solution.json`, `README.md`, and `preview/icon.png` there before
  starting.

## Steps

### 1. Gallery page renders

- [ ] Navigate to `/solutions`.
- [ ] Expect a tile-grid page. Confirm:
    - title bar reads "Solutions Gallery" (or your branded equivalent)
    - each tile shows the solution name, description, vertical tag,
      and a preview icon
    - tiles for bundled solutions load immediately (no spinner stuck)

### 2. Catalog API

- [ ] In DevTools, watch `GET /api/solutions/catalog` fire on page load.
- [ ] Confirm 200 with a JSON object `{ "solutions": [...] }`.
- [ ] Each entry has `id`, `name`, `version`, `description`, `source`.

### 3. Detail view

- [ ] Click a tile. Expect navigation to `/solutions/install/<id>`.
- [ ] The wizard page shows manifest details: name, version, author,
      description, dependencies (credentials required).
- [ ] DevTools should show `GET /api/solutions/<id>` returning the
      manifest + preview metadata.

### 4. README preview

- [ ] If the bundle includes a README, the wizard renders it.
- [ ] `GET /api/solutions/<id>/readme` returns markdown text.

### 5. Preview asset

- [ ] If the bundle includes `preview/icon.png` or screenshots, they
      load in the wizard.
- [ ] Try directly: `GET /api/solutions/<id>/preview/icon.png` →
      returns the icon bytes with a correct `Content-Type`.
- [ ] **Security check:** try
      `GET /api/solutions/<id>/preview/..%2F..%2Fetc%2Fpasswd`.
      Expect 404 or 4xx — never the contents of any system file.

### 6. Analyze (dry-run)

- [ ] Click "Analyze" (or whatever the wizard button is).
- [ ] Confirm `POST /api/solutions/<id>/analyze` fires.
- [ ] Response shows `{ valid: true, manifest: {...}, conflicts: {...} }`.
- [ ] If there are existing agents/workflows with the same names, they
      should appear under `conflicts`.

### 7. Install

- [ ] Choose conflict mode = `rename`.
- [ ] Fill in any required credentials with throw-away values.
- [ ] Click "Install".
- [ ] Confirm `POST /api/solutions/<id>/install` returns 200 (success)
      or 207 (partial — review the `errors` array).
- [ ] Visit `/agents`, `/workflow`, `/api/integrations` (or the
      relevant tenant-side admin pages) and confirm the newly
      installed resources appear.

### 8. Upload-install of an external bundle

- [ ] On the gallery page click "Upload bundle".
- [ ] Select a `.zip` from your filesystem. Confirm
      `POST /api/solutions/upload_stage` returns a `staging_id`.
- [ ] You should be redirected into the wizard at
      `/solutions/install/staged_<id>`.
- [ ] Run the install. Confirm the same end state as step 7.

### 9. Negative cases

- [ ] Try to install while logged in as a **basic user** (role 1).
      Expect 403 from `/api/solutions/<id>/install`.
- [ ] Upload a file that isn't a zip (e.g. `foo.txt`). Expect either
      400 "upload is not a valid solution bundle" or a soft-fail
      install result with errors.

### 10. Cleanup

- [ ] Note any objects the install created.
- [ ] If your tenant supports it, delete or rename them so a
      subsequent re-install starts from a known state.

## Expected Defects to Watch For

- Stuck spinner on the gallery → catalog API is unreachable or
  feature flag is off.
- 500 on `/api/solutions/<id>/install` with a stack trace in the
  response body → indicates server is leaking internals; bug.
- `..` in the preview URL returning a file → critical path-traversal
  bug, file an immediate issue.
