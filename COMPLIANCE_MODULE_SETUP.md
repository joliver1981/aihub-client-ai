# Retailer Compliance Module — Setup Guide

## What was built

A self-contained module for managing retailer compliance documents with versioning, semantic comparison (same retailer over time AND cross-retailer), and conversational Q&A through a custom agent.

## Files added

| File | Purpose |
|---|---|
| `migrations/009_retailer_compliance_tables.sql` | 5 new tables: Retailers, RetailerDocumentSets, RetailerDocumentVersions, ExtractedRequirements, ComparisonResults |
| `schemas/retailer_compliance.yaml` | Standardized 6-category, 57-field taxonomy for cross-retailer normalization |
| `compliance_engine.py` | Core pipeline: ingest -> extract -> version -> knowledge index -> Excel |
| `compliance_comparison.py` | Version diff + cross-retailer diff (uses SmartChangeDetector strict mode) |
| `compliance_routes.py` | Flask blueprint with 16 API endpoints + UI page |
| `templates/compliance_management.html` | Full management UI (retailers, sets, versions, upload, comparison, export) |
| `tools/query_compliance_requirements/` | Agent tool — structured query against ExtractedRequirements |
| `tools/compare_compliance_requirements/` | Agent tool — natural-language wrapper around comparison engine |

`app.py` updated to register the compliance blueprint.

## Setup steps

### 1. Run the database migration

Apply `migrations/009_retailer_compliance_tables.sql` against your tenant database. It is idempotent.

### 2. Restart the app

The new blueprint is registered at app startup. After restart:
- UI page: `/compliance`
- API base: `/api/compliance/*`

### 3. Create the Compliance Agent

In Agent Builder, create a new custom agent (name suggestion: "Retailer Compliance Assistant"):

**System prompt suggestion:**
```
You are a retailer compliance assistant. You help users understand and compare 
shipping, packaging, labeling, EDI, quality, and chargeback requirements from 
major retailers (Amazon, Walmart, Dollar General, etc.).

Standard requirement categories: shipping, packaging, labeling, edi_electronic, 
quality_testing, penalties_chargebacks.

When users ask about specific requirements, use query_compliance_requirements 
to look them up. When they ask "what changed" or "how does X compare to Y", use 
compare_compliance_requirements. For free-form questions about document content, 
use search_agent_knowledge (built-in).

Always cite the retailer and source category when answering. If a user asks 
about a retailer that has no documents loaded, tell them so explicitly.
```

**Assign tools:** add these custom tools to the agent:
- `query_compliance_requirements`
- `compare_compliance_requirements`
- `search_agent_knowledge` (built-in — already available to all custom agents with knowledge enabled)

Note the agent's numeric ID — you'll need it for uploads.

### 4. Upload documents

**Via the UI (`/compliance`):**
1. Click "Add Retailer" — create entries for Amazon, Walmart, etc.
2. Click into a retailer, click "Add Document Set" (e.g., "domestic", "international")
3. Click into a document set, click "Upload New Version" — drag/drop the PDF

**Via API (to also push docs into the agent's knowledge base):**
```
POST /api/compliance/sets/{set_id}/upload
Content-Type: multipart/form-data
file: <PDF>
agent_id: <compliance_agent_id>
```

The `agent_id` parameter triggers knowledge indexing. The UI currently does not pass `agent_id`; either extend the UI to include it, or add an "Agent ID" field to the upload modal.

### 5. Test end-to-end

1. **Upload a 100+ page Amazon compliance PDF** -> verify retailer/set/version records, requirements extracted into `ExtractedRequirements`
2. **Upload a second version** -> verify version auto-increment, hash dedup if identical
3. **Compare versions** -> click "Compare Versions" on the version list
4. **Upload a Walmart doc** -> from the retailer list, click "Cross-Retailer Comparison"
5. **Ask the compliance agent:**
   - "What are Amazon's pallet weight limits?" (uses `query_compliance_requirements`)
   - "Compare Amazon and Walmart barcode requirements" (uses `compare_compliance_requirements`)
   - "What does the Amazon doc say about ASN timing?" (uses `search_agent_knowledge`)
6. **Export to Excel** -> requirements list and comparison results both exportable

## Architecture notes

- **No workflow involved.** The compliance engine calls existing platform functions directly (LLMDocumentProcessor, SmartChangeDetector, populate_excel, process_document_as_knowledge).
- **Tenant-isolated.** All tables include TenantId with default from `session_context('TenantId')`. All queries set tenant context via `tenant.sp_setTenantContext`.
- **Agent-scoped knowledge.** Documents indexed for the compliance agent are only accessible through that agent — meets your access-control goal.
- **Cross-retailer comparison works** because all retailers' requirements are normalized into the same 6-category taxonomy at extraction time.

## Future extensions (deferred)

- Folder-watch automation (drop PDFs into `data/compliance_inbox/<retailer>/<category>/` for auto-processing)
- Cross-retailer matrix view (rows=subcategories, columns=retailers, cells=values with strictest highlighting)
- Workflow integration (an "Agent Knowledge" workflow node + a "Compliance Process" workflow node) for users who want to embed compliance processing inside larger onboarding flows
- Chat panel embedded directly in the compliance UI (right-side drawer)
