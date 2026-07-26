"""NEEDLE — hybrid retrieval for pin-point questions over agent knowledge.

Two channels, always both (fixes the legacy either/or):
  - dense:   the existing gated vector search (search_knowledge_vectors — inactive-doc
             gate, SHARED handling, user isolation all inherited)
  - lexical: page-level BM25 computed in pure Python over the agent's active pages —
             exact-term needles (addresses, parties, dollar figures, section numbers)
             are embeddings' weakest case and BM25's strongest. No DuckDB/FTS dependency:
             agent knowledge is small (<= ~1,000 pages) and client boxes cannot download
             extensions, so a 40-line BM25 beats an install-time risk.

Fusion is reciprocal-rank (RRF, k=60) at PAGE granularity — a dense chunk hit votes for
its parent page. A Haiku rerank re-orders the fused shortlist and fails OPEN to RRF order.
The result is a citation-ready evidence block: verbatim page text with [filename p.N]
source markers plus a retrieval ledger, so every quote in the agent's answer is
deterministically verifiable against a named page.
"""
import json
import logging
import math
import re
import threading
import time
from collections import Counter

import config as cfg

_RRF_K = 60
_CACHE_TTL_S = 300
_index_lock = threading.Lock()
_index_cache = {}

_RERANK_SYSTEM = (
    "You rank sources for how directly they answer a question. Respond with STRICT JSON "
    'only: [{"id": <int>, "score": <0-100>}] for EVERY source given, no prose, no fences.'
)


def _tokenize(text: str):
    return re.findall(r"[a-z0-9$%]+", (text or '').lower())


class _BM25:
    """Minimal Okapi BM25 over a small page corpus."""

    def __init__(self, token_lists, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.doc_tfs = [Counter(toks) for toks in token_lists]
        self.doc_lens = [len(toks) for toks in token_lists]
        self.avg_len = (sum(self.doc_lens) / len(self.doc_lens)) if self.doc_lens else 0.0
        df = Counter()
        for tf in self.doc_tfs:
            df.update(tf.keys())
        n = len(token_lists)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def scores(self, query_tokens):
        out = []
        for tf, dl in zip(self.doc_tfs, self.doc_lens):
            s = 0.0
            for t in query_tokens:
                if t not in tf:
                    continue
                idf = self.idf.get(t, 0.0)
                freq = tf[t]
                denom = freq + self.k1 * (1 - self.b + self.b * dl / (self.avg_len or 1))
                s += idf * freq * (self.k1 + 1) / denom
            out.append(s)
        return out


def _build_page_index(contents):
    """Flatten contents -> parallel lists of page records + BM25 index."""
    pages = []
    for doc_id, content in contents.items():
        fname = content.get('filename') or f'Document {doc_id}'
        for page_num, text in sorted(content.get('pages', {}).items(),
                                     key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
            if text and text.strip():
                pages.append(dict(doc_id=str(doc_id), filename=fname,
                                  page_number=page_num, text=text))
    bm25 = _BM25([_tokenize(p['text']) for p in pages])
    return pages, bm25


def _cached_index(fingerprint, contents):
    with _index_lock:
        entry = _index_cache.get(fingerprint)
        if entry and (time.time() - entry[0]) < _CACHE_TTL_S:
            return entry[1]
    built = _build_page_index(contents)
    with _index_lock:
        if len(_index_cache) > 20:
            _index_cache.clear()
        _index_cache[fingerprint] = (time.time(), built)
    return built


def _rrf_fuse(pages, lex_ranked_idx, dense_ranked_keys):
    """RRF at page granularity. dense_ranked_keys: ordered (doc_id, page_number) votes."""
    key_to_idx = {(p['doc_id'], str(p['page_number'])): i for i, p in enumerate(pages)}
    fused = Counter()
    for rank, idx in enumerate(lex_ranked_idx):
        fused[idx] += 1.0 / (_RRF_K + rank + 1)
    seen = set()
    rank = 0
    for key in dense_ranked_keys:
        if key in seen:
            continue
        seen.add(key)
        idx = key_to_idx.get(key)
        if idx is not None:
            fused[idx] += 1.0 / (_RRF_K + rank + 1)
            rank += 1
    return [idx for idx, _ in fused.most_common()]


def _rerank(llm_call, query, pages, candidate_idx):
    """Haiku rerank of the fused shortlist; fails OPEN to the given order."""
    if not getattr(cfg, 'DOC_NEEDLE_RERANK', True) or len(candidate_idx) <= 1:
        return candidate_idx
    try:
        listing = []
        for i, idx in enumerate(candidate_idx):
            p = pages[idx]
            preview = re.sub(r'\s+', ' ', p['text'])[:400]
            listing.append(f"[id {i}] {p['filename']} p.{p['page_number']}: {preview}")
        prompt = f"Question: {query}\n\nSources:\n" + "\n".join(listing)
        raw = llm_call(prompt, system=_RERANK_SYSTEM, max_tokens=400, temp=0.0)
        m = re.search(r'\[.*\]', raw or '', re.S)
        scored = json.loads(m.group(0)) if m else []
        order = sorted((s for s in scored if isinstance(s, dict) and 'id' in s),
                       key=lambda s: -float(s.get('score', 0)))
        reranked = [candidate_idx[int(s['id'])] for s in order
                    if 0 <= int(s['id']) < len(candidate_idx)]
        missing = [idx for idx in candidate_idx if idx not in reranked]
        return reranked + missing
    except Exception as e:
        logging.info(f"needle rerank failed open ({e}) — keeping RRF order")
        return candidate_idx


def knowledge_needle_v2(query, agent_id, user_id=None, documents=None):
    """Hybrid needle retrieval. Returns a citation-ready evidence string, or None to
    defer to the legacy needle path. Raises on internal failure (caller falls back)."""
    if not documents:
        return None

    import agent_knowledge_integration as aki

    doc_ids = [d['document_id'] for d in documents]
    contents = aki._load_agent_knowledge_contents(doc_ids, documents)
    if not contents:
        return None

    fingerprint = '|'.join(sorted(str(k) for k in contents.keys()))
    pages, bm25 = _cached_index(fingerprint, contents)
    if not pages:
        return None

    top_pages = int(getattr(cfg, 'DOC_NEEDLE_TOP_PAGES', 8))
    shortlist_n = max(top_pages + 4, int(top_pages * 1.5))

    # Lexical channel
    q_tokens = _tokenize(query)
    lex_scores = bm25.scores(q_tokens)
    lex_ranked = [i for i in sorted(range(len(pages)), key=lambda i: -lex_scores[i])
                  if lex_scores[i] > 0][:shortlist_n]

    # Dense channel (existing gated vector search; chunk hits vote for their page)
    dense_keys = []
    try:
        hits = aki.search_knowledge_vectors(query, int(agent_id), user_id=user_id,
                                            top_k=shortlist_n * 2)
        for h in hits:
            meta = h.get('metadata') or {}
            if meta.get('document_id') is not None:
                dense_keys.append((str(meta.get('document_id')), str(meta.get('page_number'))))
    except Exception as e:
        logging.warning(f"needle dense channel failed ({e}) — lexical-only")

    fused = _rrf_fuse(pages, lex_ranked, dense_keys)[:shortlist_n]
    if not fused:
        return None  # nothing matched either channel — let legacy try its own fallbacks

    def llm_call(prompt, system, max_tokens, temp):
        return aki._haiku_call_with_fallback(prompt, system=system, max_tokens=max_tokens, temp=temp)

    final_idx = _rerank(llm_call, query, pages, fused)[:top_pages]

    lines = ["[Knowledge needle — hybrid retrieval (lexical + semantic), citation-ready]", ""]
    for idx in final_idx:
        p = pages[idx]
        text = p['text'].strip()
        if len(text) > 2500:
            text = text[:2500] + ' …[page continues]'
        lines.append(f"SOURCE [{p['filename']} p.{p['page_number']}]:")
        lines.append(text)
        lines.append("")
    lines.append(
        f"RETRIEVAL: {len(pages)} pages in scope · lexical matches {len(lex_ranked)} · "
        f"semantic votes {len(dense_keys)} · showing top {len(final_idx)} after fusion"
        + (" + rerank" if getattr(cfg, 'DOC_NEEDLE_RERANK', True) else "")
    )
    lines.append(
        "Answer from these sources only. Cite each fact as [filename p.N] and quote key "
        "language verbatim. If the sources do not contain the answer, say so plainly."
    )
    return "\n".join(lines)
