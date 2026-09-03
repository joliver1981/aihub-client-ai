# Document Search page — rebuild (2026-09-03)

**Status:** BUILT, unit-tested, live-verified on the dev box. Commits listed at the end.
**Why:** james, 2026-09-03: "I cannot release this page to clients this way." The legacy
`/document-search` page was open to every signed-in user, applied no document permissions,
listed every document type on the left and every distinct extracted field name (8,791 on the
397-document dev store, 5,276 of them occurring in exactly one document) in one dropdown, and
ran three `GROUP BY` queries over `DocumentFields` (132k rows) on every render.

## What the page does now

| Area | Before | Now |
|---|---|---|
| Access control | none | every list, count, suggestion, search, view and delete is scoped to the caller's **v3 category ACL** (`doc_search_v3.acl`, per user via groups). Admins unrestricted; Developers and users restricted; a user with no grants sees a lock message and nothing is queried. |
| Sidebar | all raw types | the **category tree** (`DocumentCategories` → active types) with document counts, a client-side type filter, "search all" per category. Restricted users never see a type they cannot read; "Uncategorised" only for admins. |
| Field filters | one `<select>` of all fields | **type-ahead** (`/api/document-search/fields`) from the per-type **field catalog**: schema-declared fields first, then by document count; names seen in fewer than 2 documents dropped unless typed exactly; at most 50 per query. Only after a type or category is chosen. |
| Common fields | global top 10 | top 15 for the selected scope, click-to-filter. |
| Free-text search | `LLMDocumentSearch` vector query, unscoped | `document_search_unified(..., document_types=scope)` — the same ACL'd engine The Agent and Command Center use; the scope can only narrow the ACL. |
| Field / attribute criteria | OR across criteria, page level | **AND per document** (every criterion must match somewhere in the document); results are the matching pages, or the text passages of qualifying documents. |
| Attribute tab | metadata call was Developer-only (403 for users) | `/api/document-search/attributes` + `/api/document-attributes/metadata` open to any signed-in user, ACL-scoped. |
| Snippets | raw page text with `|safe` | HTML-escaped before highlighting. |
| `/document/view/<id>` | **no login at all** | `login_required` + ACL: a hidden or missing document is a 404 (no id-oracle). `abort()` now propagates (was swallowed into a 200 error page). |
| `/document/delete/<id>` | any signed-in user could purge any document | Developer+ and only documents the caller can see. |
| `/document-search-legacy` | duplicate 380-line route, no tier check | removed. |
| Concurrency | none | text searches take a slot on the shared search gate; when busy the page says so instead of queueing. |

Search results, pagination (in memory over up to 200 engine passages), the delete modal and
the theme toggle keep their previous look; `static/js/document_search_context.js` still finds
the elements it reads.

## The field catalog

`document_field_catalog.py` + `migrations/021_document_field_catalog.sql`.

- `DocumentFieldCatalog(document_type, field_name, field_path, doc_count, row_count, first_seen, last_seen)`,
  unique on `(document_type, SHA1(field_path))` (the hash keeps the key under older servers'
  900-byte limit), plus a `(document_type, doc_count DESC)` index.
- **Kept current at ingest:** `LLMDocumentEngine._store_in_sql_db` calls
  `record_document()` after the document's own commit — exact recounts for the paths that
  document carries (chunked `IN` lists, `UPDATE` then `INSERT`), idempotent on re-ingest, and
  any catalog failure is logged, never fails an ingest.
- **Reads are cached** in-process (60 s) and invalidated per type by writes.
- **Fallback:** if the table is missing (migration not applied and the app login cannot
  `CREATE TABLE` — true on the dev Azure SQL database) or empty for a type, the same answer is
  computed live from `DocumentFields` **for those types only**, cached 5 minutes, with one
  warning per process pointing at the backfill. The page works either way; suggestions are
  just slower on very large stores until the table exists.

### Operating it on an install

1. Apply `migrations/021_document_field_catalog.sql` with a login that has `CREATE TABLE`
   (the application login does not; same situation as migrations 016 and 020).
2. Run `python run_document_field_catalog_backfill.py` (or press **Rebuild field catalog** on
   the search page as an admin — `POST /api/document-search/catalog/rebuild`). Ingest keeps
   it current afterwards.
3. `GET /api/document-search/catalog/stats` (admin) reports rows / types / last update; the
   admin card on the page shows the same.

## Endpoints

| Route | Auth | Purpose |
|---|---|---|
| `GET /document-search` | login + documents tier | the page |
| `GET /api/document-search/categories` | session or API key + assertion | sidebar tree, ACL-scoped |
| `GET /api/document-search/fields?document_type=\|category=&q=&limit=` | same | field type-ahead; without a scope answers a hint |
| `GET /api/document-search/attributes?…` | same | attribute-name type-ahead |
| `GET /api/document-attributes/metadata` | same (was Developer-only) | attribute metadata, ACL-scoped |
| `POST /api/document-search/catalog/rebuild`, `GET …/stats` | admin | catalog maintenance |

Identity for all of these comes from `_caller_identity_or_session()`: a service assertion
(`X-AIHub-User`, forged → 403) first, else the browser session, else unrestricted for
identity-less API-key callers — the same helper the Documents Manager endpoints use (decision
D1).

## Code map

- `document_search_page.py` — request parsing, scope resolution, category tree, suggestions,
  field/attribute matching, search orchestration, pagination, highlighting. Pure functions over
  a cursor; `tests_v2/unit/test_document_search_page.py` (21).
- `app.py` — thin routes (`_document_search_identity`, `document_search_page`,
  `api_document_search_*`), the view/delete/metadata changes;
  `tests_v2/unit/test_document_search_routes.py` (13, routes lifted from app.py by
  `tests_v2/unit/app_route_harness.py`).
- `document_search_wrapper.py` — `document_types` scope; `tests_v2/unit/test_document_search_wrapper_types.py` (7).
- `document_field_catalog.py` — `tests_v2/unit/test_document_field_catalog.py` (16).
- `templates/document_search.html` — rewritten; vanilla JS type-ahead, category filter,
  admin rebuild.

## Known limits / follow-ups

- Free-text search latency is the platform engine's (LLM-planned); the page says larger scopes
  take longer. The legacy vector-only query was faster but unscoped and inconsistent with The
  Agent's answers.
- Results are paginated over the first 200 engine passages, not a true server-side page over
  the whole corpus.
- Field type-ahead requires a type or category. Cross-type field search was the 8.8k-name
  dropdown; a curated cross-type vocabulary would be the next step if clients ask for it.
- The Documents Manager (`/document-manager`) keeps its own filter dropdown; it is ACL-scoped
  since D1 but was not redesigned here.
