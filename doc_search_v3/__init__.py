"""Document search v3 — the counting lane over GENERALLY AVAILABLE documents.

Purely additive package (mirrors doc_search_v2's posture): the legacy engine is
never edited and remains the fallback for every error. v3 adds the two things the
legacy engine structurally cannot do:

  * a real DENOMINATOR — the document list is resolved from SQL, filtered by the
    user's category grants, BEFORE any LLM runs, so "how many of N…" has an N;
  * one VERDICT PER DOCUMENT with a deterministic Python roll-up — the count is
    arithmetic over an array, never an LLM counting passages.

Scope: is_knowledge_document = 0 only. Private agent knowledge keeps its existing
brute-force path untouched (owner decision, 2026-08-12).
"""
