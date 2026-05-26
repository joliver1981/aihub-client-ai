# Module 30: Compliance Management — Happy Path
**Purpose:** End-to-end smoke test of the new Compliance Management subsystem against a running AI Hub instance. Exercises the full retailer → set → upload → extract → compare lifecycle on real infrastructure (SQL Server, document API, Anthropic).
**Time estimate:** 45–60 minutes
**Prerequisites:**
  - AI Hub running on `http://10.0.0.7:5001` (or wherever `HOST_PORT` points)
  - Logged in as a developer-role user (role >= 2)
  - Migrations 009–012 have been applied
  - A real PDF compliance document available locally (sample under `tests_v2/fixtures/compliance/sample_compliance.pdf` works, but a multi-page retailer policy PDF gives more interesting extraction results)
  - `tests_v2/fixtures/compliance/sample_schema.json` available to upload

---

## CHP-1: Open compliance management page
**Action:** Navigate to `/compliance` in browser.
**Expected:** Page loads, retailer list is empty (or shows existing retailers if tenant has data), no console errors. Footer/breadcrumbs visible.
**Pass criteria:** :green_circle: page renders without 5xx and without JS error overlays.

---

## CHP-2: Create a new retailer
**Action:** Click "New Retailer", enter `Module30 Test Retailer` and notes `Live happy-path test`. Save.
**Expected:** 201 from `POST /api/compliance/retailers`. New retailer appears in the list. `retailer_id` captured for later steps.
**Pass criteria:** :green_circle: retailer visible in list after page refresh, ID > 0.

---

## CHP-3: Create a document set on the new retailer
**Action:** Open the retailer detail page, click "New Document Set", category `Shipping`, description `Shipping & logistics policies`. Save.
**Expected:** 201 from `POST /api/compliance/retailers/{retailer_id}/sets`. Set visible under the retailer.
**Pass criteria:** :green_circle: set listed; `set_id` recorded.

---

## CHP-4: Attach the default compliance schema to the set
**Action:** On the set, choose the "Default Retailer Compliance" schema (auto-seeded from `schemas/retailer_compliance.yaml`).
**Expected:** PUT `/api/compliance/sets/{set_id}` returns 200; the set's `extraction_schema_id` is set in the response.
**Pass criteria:** :green_circle: schema linked; verify via GET `/api/compliance/sets/{set_id}`.

---

## CHP-5: Upload a compliance PDF (async, default path)
**Action:** Click "Upload Document", select `sample_compliance.pdf`, submit.
**Expected:** 202 from `POST /api/compliance/sets/{set_id}/upload`. Response includes `job_id` and `status: "queued"`. A "ghost entry" appears in the versions list with status "queued" / "running".
**Pass criteria:** :green_circle: job_id returned; ghost entry visible immediately.

---

## CHP-6: Poll job status until completion
**Action:** Poll `GET /api/compliance/sets/{set_id}/jobs` every 5 seconds for up to 5 minutes.
**Expected:** Job transitions queued → running → done. Final job entry shows `version_id`, `version_number=1`, and a success message.
**Pass criteria:** :green_circle: job reaches `done` (or `duplicate` if the file was previously uploaded). If extraction took > 5 min, escalate to YELLOW with notes.

---

## CHP-7: Fetch requirements for the new version
**Action:** Click into the version, view requirements list. Also hit `GET /api/compliance/versions/{version_id}/requirements`.
**Expected:** A non-empty list of requirements, each with category/subcategory/text/source_page.
**Pass criteria:** :green_circle: at least 1 requirement extracted. If 0 are extracted, check the job's `extraction_diagnostics` field — that's a meaningful failure mode, not just an empty doc.

---

## CHP-8: Export requirements as Excel
**Action:** Click "Export to Excel" on the version, or hit `GET /api/compliance/versions/{version_id}/export/excel`.
**Expected:** A `.xlsx` file downloads with one row per requirement and a "Requirements" sheet header (Category, Subcategory, Requirement, Value, Severity, Source Page, Confidence).
**Pass criteria:** :green_circle: file opens in Excel without errors and contains the requirements.

---

## CHP-9: Upload a SECOND version of the same set
**Action:** Upload a different compliance PDF (or a modified version of the same doc with `--metadata-changed` so the hash differs). Confirm version_number=2 appears.
**Expected:** 202 → done. Version 2 is `is_current=1`, version 1 is `is_current=0`.
**Pass criteria:** :green_circle: two versions listed; the newer is marked current.

---

## CHP-10: Compare version 1 vs version 2
**Action:** Click "Compare to previous version" or hit `POST /api/compliance/compare/versions` with `version_a_id`/`version_b_id`.
**Expected:** 200 with a `result` payload containing `summary` (per-category) and `details` (per-requirement). Categories show added/removed/modified counts.
**Pass criteria:** :green_circle: at least one detail row marked `is_meaningful=true`. If both versions extract identical content, summary should reflect that with 0 meaningful changes.

---

## CHP-11: Export comparison as Excel
**Action:** Hit `GET /api/compliance/comparisons/{comparison_id}/export/excel`.
**Expected:** A formatted .xlsx with rows colored by change type (green=added, red=removed, yellow=modified-meaningful).
**Pass criteria:** :green_circle: file opens, rows colored, "Meaningful" column shows Yes/No correctly.

---

## CHP-12: Compare retailers (cross-retailer)
**Action:** Create a second test retailer with its own set and a similar uploaded doc, then hit `POST /api/compliance/compare/retailers`.
**Expected:** 200 with a side-by-side comparison of the current requirements for both retailers, scoped to the selected category if provided.
**Pass criteria:** :green_circle: details list includes rows from both retailers' current versions.

---

## CHP-13: Create a custom schema and link it
**Action:** Upload `sample_schema.json` via the schemas UI (or POST `/api/compliance/schemas`). Create a new set on the test retailer that uses this schema. Upload a doc and confirm extraction uses the custom fields.
**Expected:** Extraction yields fields like `notes.topic`, `notes.requirement` (from the schema's `repeated_group`).
**Pass criteria:** :green_circle: at least one requirement's `subcategory` matches a key from the custom schema.

---

## CHP-14: Delete the test version
**Action:** Click "Delete" on version 2. Confirm.
**Expected:** 200 from `DELETE /api/compliance/versions/{version_id}`. Version 1 is auto-promoted to `is_current=1`. Agent knowledge cleanup runs in the background (verify via knowledge UI that the doc no longer appears).
**Pass criteria:** :green_circle: only version 1 remains, marked current.

---

## CHP-15: Delete the test retailer (full cleanup)
**Action:** Click "Delete Retailer" on the test retailer.
**Expected:** 200 from `DELETE /api/compliance/retailers/{retailer_id}`. Cascading deletes remove all sets, versions, requirements, and shared knowledge entries.
**Pass criteria:** :green_circle: retailer gone from list; no orphan rows remain. Verify by hitting `POST /api/compliance/admin/cleanup-orphaned-knowledge` and confirming `orphans_removed == 0`.

---

## Summary

| Test | Score | Notes |
|------|-------|-------|
| CHP-1 | ⬜ | |
| CHP-2 | ⬜ | |
| CHP-3 | ⬜ | |
| CHP-4 | ⬜ | |
| CHP-5 | ⬜ | |
| CHP-6 | ⬜ | |
| CHP-7 | ⬜ | |
| CHP-8 | ⬜ | |
| CHP-9 | ⬜ | |
| CHP-10 | ⬜ | |
| CHP-11 | ⬜ | |
| CHP-12 | ⬜ | |
| CHP-13 | ⬜ | |
| CHP-14 | ⬜ | |
| CHP-15 | ⬜ | |
