# Live Test Plan 38 — Agent Knowledge

End-to-end exercise of agent knowledge: upload a document → attach to an
agent → query through the agent → confirm retrieval works.

## Pre-conditions

- AI Hub main app running on `HOST_PORT` (default 5001).
- Document API service running on `HOST_PORT + 10`.
- Vector API service running on `HOST_PORT + 30` (knowledge endpoints:
  `/knowledge/index`, `/knowledge/search`, `/knowledge/delete`).
- Anthropic credentials configured (BYOK or proxy).
- A custom agent exists. Note its `agent_id`.
- A small test document on disk — ideally a 3-10 page PDF so chunking
  and indexing complete in seconds, not minutes.

## Steps

### 1. Upload a knowledge document

- [ ] Navigate to `/agent_knowledge/<agent_id>`.
- [ ] Click "Add Knowledge".
- [ ] Choose your test document and supply a short description.
- [ ] `POST /add/agent_knowledge` should return 200 with
      `{status: "success", knowledge_id: <n>, document_id: "...",
        document_type: "...", page_count: <n>}`.
- [ ] The document appears in the listing on
      `GET /get/agent_knowledge/<agent_id>`.

### 2. Confirm indexing kicked off

- [ ] Tail the main-app log (or `logs/skr_trace.txt` if
      `KNOWLEDGE_ENABLE_TRACE=True`).
- [ ] Within a few seconds you should see
      `Queued knowledge indexing for doc <document_id>` and shortly
      after `Indexed N chunks for knowledge document <id>`.
- [ ] If you see "Knowledge vector engine not available", the Vector
      API on port `HOST_PORT + 30` isn't reachable — fix that first.

### 3. Verify vector store has chunks

- [ ] Direct call:
      `POST http://127.0.0.1:<HOST_PORT+30>/knowledge/search` with
      body `{"query": "<topic from your doc>", "filters":
      {"$and":[{"agent_id":"<agent_id>"},{"$or":[{"user_id":"<your
      user_id>"},{"user_id":"SHARED"}]}]}, "limit": 5}`.
- [ ] Expect non-empty `results.documents[0]` with the relevant chunks.

### 4. Query through the agent

- [ ] Open a chat session for `<agent_id>` (UI: `/chat?agent=<id>` or
      similar).
- [ ] Ask a question whose answer is in the uploaded doc — e.g. if
      the doc is a lease, "What's the renewal term?"
- [ ] The agent should reply citing the document. In the response,
      look for a "Knowledge Reference Info" block (or equivalent
      based on your agent prompt) listing
      `filename → page N`.

### 5. Update the description

- [ ] Edit a knowledge item's description.
- [ ] `POST /update/agent_knowledge/<knowledge_id>` returns 200.
- [ ] The new description renders on the page.

### 6. Delete the knowledge item

- [ ] Click delete.
- [ ] `POST /delete/agent_knowledge/<knowledge_id>` returns 200.
- [ ] The item is soft-deleted (gone from the listing).
- [ ] Within a few seconds, log shows
      `Knowledge delete: N chunks removed for <document_id>`.
- [ ] Re-running the search in step 3 returns 0 results for that
      document_id.

### 7. User isolation

- [ ] Log in as a different user with access to the SAME agent.
- [ ] Upload a different document under the same agent.
- [ ] Switch back to user 1. Their listing should still show only
      THEIR documents — not user 2's.
- [ ] Run a chat query in agent — the response should ground on
      user 1's docs only (vector search filters by user_id OR
      'SHARED').

### 8. Excel knowledge (if your agent supports it)

- [ ] Upload an `.xlsx` file.
- [ ] Confirm the file is persisted under
      `<APP_ROOT>/<EXCEL_KNOWLEDGE_FILES_DIR>/<document_id>/<filename>`.
- [ ] On delete, the persisted directory is removed.

### 9. Failure modes to verify

- [ ] Upload with **no file**: `POST /add/agent_knowledge` → 400
      "No file part".
- [ ] Upload a corrupt PDF: should return `status: "error"` from the
      document API.
- [ ] Stop the Vector API. Upload completes (document saved to
      database) but indexing logs a non-fatal warning; agent query
      still works against the page-text fallback.

## Expected Defects to Watch For

- Document upload returns success but no chunks ever appear in the
  vector store → vector API down or chunker raised.
- Agent answers ignore the document → either index didn't complete,
  or the query routed to the wrong shape (check
  `logs/skr_trace.txt`).
- One user's upload visible to another → user_id filter regression.
  CRITICAL — file an immediate issue.
