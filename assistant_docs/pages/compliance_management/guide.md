# Retailer Compliance (/compliance_management)

The Retailer Compliance page is where teams track the compliance requirements that retailers and partners hand down to them — and how those requirements change over time. It turns a stack of vendor-compliance PDFs and Word docs into a structured, comparable, exportable record.

## Why This Page Exists

When you sell to a major retailer, that retailer typically gives you a compliance manual: routing guides, packaging rules, EDI specs, chargeback schedules, return policies, etc. Those documents update every year (or more often), and missing a change can cost real money in fines. This page:

1. Stores the compliance documents per retailer, organized into document sets and versioned over time.
2. Extracts structured requirements from each document automatically (using extraction workflows and/or compliance agents).
3. Lets you compare two versions to see exactly what changed — and whether the change was meaningful or cosmetic.
4. Exports the structured requirements (and the comparison) to Excel for distribution.

## Page Layout

The page is breadcrumb-driven. You navigate down through three levels and can pop back up at any time using the breadcrumb at the top.

### Level 1 — Retailers
A grid of cards, one per retailer (Amazon, Walmart, Target, etc.). Each card shows the retailer name and some metadata (e.g., number of document sets). Click a card to drill in.

- **Add Retailer** (top right) — register a new retailer.

### Level 2 — Document Sets
Once inside a retailer, you see the document sets that retailer maintains. A *document set* is a category like "Domestic Routing Guide" or "International Packaging Rules." Each set has its own version history and (optionally) its own associated compliance agent and extraction workflow.

- **Add Document Set** — create a new set within this retailer.

### Level 3 — Versions
Inside a document set, you see a timeline of versions. The current version is highlighted. Each version represents an upload of the source document at a specific point in time.

- **Upload new version** — bring in a newer version of the document.
- **Open** a version to view its extracted requirements table.
- **Compare** two versions to see line-by-line changes.

### Level 4 — Requirements (when a version is open)
A table of structured requirements extracted from the document. Columns vary by extraction schema but typically include rule name, category, requirement text, effective date, etc.

- **Export Excel** — download the structured requirements as a spreadsheet.

### Comparison View (when two versions are compared)
- Rows are colored:
  - **Green** = added in the newer version.
  - **Red** = removed in the newer version.
  - **Yellow** = modified.
  - **Faded** = cosmetic-only change (formatting, not substance).
- Summary badges at the top count meaningful vs. cosmetic changes.
- **Export Comparison Excel** — download the comparison.

## Common Tasks

### Add a new retailer
1. Click **Add Retailer** at the top.
2. Enter the retailer name and any notes.
3. Click **Create**.

### Create a document set under a retailer
1. Click a retailer card to drill in.
2. Click **Add Document Set**.
3. Fill in:
   - *Category* — short name (e.g., `domestic`, `international`).
   - *Description* — what this set covers.
   - *Compliance Agent* — optional; an agent whose knowledge base will be indexed with these documents so it can answer questions about them.
   - *Extraction Workflow* — optional; a workflow that overrides schema-based extraction.
4. Save.

### Upload a new version
1. Drill into the document set.
2. Use the upload zone to drop the new PDF/Word file.
3. The job appears at the top with status (Queued → Running → Done). When Done, the new version appears in the version list.
4. The new upload automatically becomes the *current* version. Older versions remain available.

### Compare two versions
1. From the version list, select two versions to compare.
2. The comparison view opens with color-coded changes and a summary.
3. Export to Excel for distribution.

### Track in-flight uploads
Above the version list, the *Jobs* container shows any uploads currently being extracted:
- **Queued** — waiting in line.
- **Running** — extraction in progress.
- **Done** — finished; new version is in the list.
- **Duplicate** — an identical file was already uploaded.
- **Error** — extraction failed; hover for details.

## Troubleshooting

- **Upload appears, then disappears as "Duplicate"** — that file (by content hash) was already uploaded to this document set. Either it's already a version, or it was rejected the first time and re-rejected.
- **Upload errors out** — the extraction workflow or schema couldn't parse this file. Check the document set's extraction configuration. As a fallback, you can change the document set to use a different extraction workflow and retry.
- **Comparison shows everything as "modified"** — usually a side effect of major formatting differences. Filter out *cosmetic* changes using the summary badges.
- **The compliance agent doesn't know about a new version** — the version's documents are indexed into the assigned compliance agent's knowledge base on upload. If you didn't assign an agent on the document set, indexing was skipped; assign one and re-upload.

## What This Page Is NOT

- It is **not** where you build the compliance agent — go to Custom Agents to configure the agent itself; on this page you only attach an existing agent to a document set.
- It is **not** where you author the extraction workflow — go to the Workflow Designer; you select an already-built workflow here.
- It is **not** a general document library — for general document management, use the Document Manager page.
