"""Document search v2 — side-by-side re-core (docs/document-search-recore-analysis.md).

Purely additive package. The legacy engines (agent_knowledge_integration, DocUtils,
LLMDocumentVectorEngine) are never imported-from, never edited, and remain the permanent
fallback. Engine selection + circuit breaker live in factory.py; the SWEEP engine
(whole-document map-reduce with a coverage ledger) lives in sweep.py.
"""
