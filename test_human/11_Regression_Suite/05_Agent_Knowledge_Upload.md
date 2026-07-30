# 05 — Agent Knowledge Upload  (requested item #4)

**Goal:** upload a document into an agent's **persistent knowledge base**, confirm it **indexes**
(chunks into the vector store), and confirm the agent **retrieves** from it in chat (RAG). Plus the
delete path actually purges it. This is the persistent KB flow — distinct from the ad-hoc attach in
§04.

**Where:** Sidebar → **Build → Agent Builders → Agent Builder** (`/custom_agent_enhanced`).

**Fixture:** `fixtures/vendor_payment_terms.docx` (10-vendor reference). Ground truth in
`_ANSWER_KEY.md`.

---

## A. Upload + index

**REG-05-A1 — Create agent + upload.**
Create a fresh agent named **`REG-Knowledge`** (so you don't pollute other agents). In its
**Knowledge / Documents** section, upload `fixtures/vendor_payment_terms.docx`. Save.
- ✅ Upload accepted; the file appears in the agent's knowledge list.

**REG-05-A2 — Indexing completes.**
Wait for indexing to finish (usually < 1 min for this small file — watch for a **chunk count** or
"indexed/ready" status, not "processing").
- ✅ The document reaches an **indexed/ready** state with a non-zero chunk count. A file stuck in
  "processing" or showing 0 chunks = indexing regression.

---

## B. Retrieval in chat (RAG)

Open **Agent Chat** (`/chat`) and select **`REG-Knowledge`**. Ask (verbatim):

**REG-05-B1 —** `Which vendor has the longest payment terms?`
- ✅ **Acme Textiles** at **Net 90**.

**REG-05-B2 —** `Which vendor offers the highest early-pay discount?`
- ✅ **Cascade Down** at **3.5% / 10**.

**REG-05-B3 —** `Which vendor is single-source, and what do they supply?`
- ✅ **Pacific Zipper Co.** — **zippers & sliders**.

**REG-05-B4 —** `Which vendors invoice in non-USD currencies?`
- ✅ **Alpenwerk GmbH (EUR)** and **Mountain Films Ltd. (GBP)**.

**REG-05-B5 — Honesty (not in the doc).**
`What is Acme Textiles' bank account number?`
- ✅ The agent says that isn't in its knowledge. ❌ if it fabricates an account number.

---

## C. Delete purges knowledge (regression guard)

*(Known past bug class: a deleted knowledge file's vectors weren't purged, so the agent kept
"remembering" it. This check guards that.)*

**REG-05-C1 —** In the Agent Builder, **delete** `vendor_payment_terms.docx` from `REG-Knowledge`'s
knowledge and save. Back in chat (start a **new conversation** with the same agent), ask B1 again:
`Which vendor has the longest payment terms?`
- ✅ The agent **no longer answers from the removed document** — it says it has no such knowledge /
  can't find it. ❌ if it still confidently returns "Acme Textiles / Net 90" (stale vector orphan).

---

## Scorecard

| Check | ✅/⚠️/❌ | Value seen |
|---|---|---|
| A1 upload accepted | | |
| A2 indexed, chunks > 0 | | |
| B1 Acme Textiles / Net 90 | | |
| B2 Cascade Down / 3.5%/10 | | |
| B3 Pacific Zipper / zippers | | |
| B4 Alpenwerk EUR + Mountain Films GBP | | |
| B5 no fabricated bank number | | |
| C1 delete purges (no stale answer) | | |

**Pass:** A1–A2 + ≥3 of B1–B4 ✅, B5 ✅, C1 ✅. Indexing stuck (A2) or stale-after-delete (C1) are
real regressions worth blocking on.
