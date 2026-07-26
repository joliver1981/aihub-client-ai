"""SWEEP — exhaustive whole-document map-reduce over agent knowledge, with a coverage ledger.

The v2 answer to cross-corpus questions ("for each lease, who handles HVAC?"). Key
differences from legacy FANOUT: NO similarity gate (nothing is skipped for being a poor
embedding match), NO top-k truncation (the whole document is read), structured per-document
output (the reduce step is deterministic), and a coverage ledger stating exactly what was
read, skipped, or low-confidence — the no-silent-drops rule.

Routing inside v2: the existing mini-LLM router classifies the query; NEEDLE-shaped
questions return None to defer to the proven legacy needle path (v2 currently upgrades the
exhaustive class only). FANOUT/AGGREGATE run the sweep.

Cost control: the sweep estimates map-step cost up front and, above
DOC_SWEEP_COST_CONFIRM_USD, returns a confirmation request instead of running.
"""
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import config as cfg

# Haiku 4.5 pricing (per MTok) for the up-front estimate — deliberately simple.
_IN_PER_MTOK = 1.00
_OUT_PER_MTOK = 5.00
_EST_OUT_TOKENS_PER_DOC = 300
_MAX_MAP_CHARS = 300_000  # per map call; longer docs are chunked, never truncated

import threading

_CACHE_TTL_S = 600
_cache_lock = threading.Lock()
_sweep_cache = {}


def _cache_get(key):
    with _cache_lock:
        entry = _sweep_cache.get(key)
        if entry and (time.time() - entry[0]) < _CACHE_TTL_S:
            return entry[1]
        _sweep_cache.pop(key, None)
        return None


def _cache_put(key, value):
    with _cache_lock:
        if len(_sweep_cache) > 50:
            _sweep_cache.clear()
        _sweep_cache[key] = (time.time(), value)


_MAP_SYSTEM = (
    "You extract answers from ONE document. Respond with STRICT JSON only, no prose, "
    'no markdown fences: {"answer": "<concise answer from THIS document>", '
    '"evidence_quote": "<short verbatim quote>", "page": <int or null>, '
    '"confidence": <0-100>, "not_found": <true|false>}. '
    "If the document does not address the question, set not_found=true and answer to an "
    "empty string. Never use knowledge from outside the document."
)


def _estimate_cost_usd(total_chars: int, doc_count: int) -> float:
    in_tokens = total_chars / 4.0
    out_tokens = doc_count * _EST_OUT_TOKENS_PER_DOC
    return (in_tokens * _IN_PER_MTOK + out_tokens * _OUT_PER_MTOK) / 1_000_000.0


def _parse_map_json(raw: str):
    """Best-effort strict-JSON parse; returns (dict|None, parse_ok)."""
    if not raw:
        return None, False
    text = raw.strip()
    m = re.search(r'\{.*\}', text, re.S)
    if m:
        text = m.group(0)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data, True
    except Exception:
        pass
    return None, False


def _doc_text_chunks(pages: dict):
    """Join a doc's pages (with page markers) and split into <=_MAX_MAP_CHARS chunks."""
    parts = []
    for page_num in sorted(pages, key=lambda p: int(p) if str(p).isdigit() else 0):
        parts.append(f"[Page {page_num}]\n{pages[page_num]}")
    full = "\n\n".join(parts)
    if len(full) <= _MAX_MAP_CHARS:
        return [full]
    return [full[i:i + _MAX_MAP_CHARS] for i in range(0, len(full), _MAX_MAP_CHARS)]


def _map_one_document(llm_call, query: str, identifier: str, pages: dict):
    """One whole-document extraction. Returns a finding dict; never raises."""
    finding = dict(identifier=identifier, answer='', quote='', page=None,
                   confidence=None, not_found=False, parse_fallback=False, error=None)
    try:
        chunks = _doc_text_chunks(pages)
        answers, quotes, confs, any_found, first_page = [], [], [], False, None
        for chunk in chunks:
            prompt = (
                f"Question: {query}\n\n"
                f"Document: {identifier}\n"
                f"--- DOCUMENT TEXT ---\n{chunk}\n--- END DOCUMENT TEXT ---"
            )
            raw = llm_call(prompt, system=_MAP_SYSTEM, max_tokens=500, temp=0.0)
            data, ok = _parse_map_json(raw or '')
            if not ok:
                # No silent drops: keep the raw text as the answer, flagged.
                finding['parse_fallback'] = True
                if raw:
                    answers.append(str(raw).strip()[:600])
                continue
            if not data.get('not_found'):
                any_found = True
                if data.get('answer'):
                    answers.append(str(data['answer']).strip())
                if data.get('evidence_quote'):
                    quotes.append(str(data['evidence_quote']).strip())
                if first_page is None and data.get('page') is not None:
                    first_page = data.get('page')
            if isinstance(data.get('confidence'), (int, float)):
                confs.append(float(data['confidence']))
        finding['answer'] = ' / '.join(a for a in answers if a)
        finding['quote'] = quotes[0] if quotes else ''
        finding['page'] = first_page
        finding['confidence'] = min(confs) if confs else None
        finding['not_found'] = not any_found and not finding['parse_fallback']
    except Exception as e:
        finding['error'] = str(e)[:200]
    return finding


def knowledge_search_v2(query: str, agent_id, user_id=None, documents=None,
                        chat_history=None, latest_user_input=None):
    """v2 entry for the agent-knowledge search path.

    Returns a formatted answer string, or None to defer to the legacy engine
    (NEEDLE-shaped queries, empty scope). Raises on internal failure — the caller's
    switch records the failure and falls back to legacy.
    """
    if getattr(cfg, 'DOC_SEARCH_V2_FORCE_ERROR', False):
        raise RuntimeError('DOC_SEARCH_V2_FORCE_ERROR chaos drill')

    if not documents:
        return None

    # Lazy imports from the legacy module — read-only reuse of its helpers; the legacy
    # module never imports this package at module level, so there is no cycle.
    import agent_knowledge_integration as aki

    started = time.time()
    deadline = started + int(getattr(cfg, 'DOC_SEARCH_V2_TIMEOUT_S', 180))

    # Deterministic scope (the documents list is already active-only and user-scoped).
    max_docs = int(getattr(cfg, 'DOC_SWEEP_MAX_DOCS', 1000))
    capped = max(0, len(documents) - max_docs)
    scope = documents[:max_docs]

    contents = aki._load_agent_knowledge_contents([d['document_id'] for d in scope], scope)
    if not contents:
        return None

    total_chars = sum(len(t) for c in contents.values() for t in c.get('pages', {}).values())

    # Route on the USER'S question, not the agent's tool paraphrase. Agents decompose
    # portfolio questions into per-document NEEDLE-shaped tool calls ("<doc> HVAC
    # responsibility"), which would make the sweep unreachable exactly when it matters.
    # The latest_user_input snapshot carries the real ask; real doc/char counts inform
    # the router the same way the legacy path does.
    routing_question = (latest_user_input or '').strip() or query
    try:
        route = aki.route_knowledge_query(routing_question, len(contents), total_chars)
    except Exception:
        route = 'FANOUT'
    logging.info(f"doc_search_v2: route={route} for question {routing_question[:90]!r}")
    if route == 'NEEDLE':
        logging.info("doc_search_v2: NEEDLE-shaped question — deferring to legacy needle path")
        return None

    # One sweep per (agent, question, document-set): agents mid-decomposition fire many
    # per-document tool calls for the same user question — serve them all from a single
    # sweep run. The document-id set is part of the key so knowledge adds/deletes
    # invalidate the cache immediately (a deleted document must never be served, even
    # from a minutes-old sweep).
    scope_fingerprint = '|'.join(sorted(str(c) for c in contents.keys()))
    cache_key = (str(agent_id), str(user_id), routing_question.lower(), scope_fingerprint)
    cached = _cache_get(cache_key)
    if cached is not None:
        logging.info("doc_search_v2: serving sweep from cache (same question, same agent)")
        return cached
    est = _estimate_cost_usd(total_chars, len(contents))
    confirm_at = float(getattr(cfg, 'DOC_SWEEP_COST_CONFIRM_USD', 5.00))
    if est > confirm_at:
        return (
            f"[SWEEP COST CONFIRMATION REQUIRED] Answering this exhaustively means reading "
            f"{len(contents)} documents (~{total_chars:,} characters) end to end — estimated "
            f"cost ≈ ${est:.2f}, above the configured ${confirm_at:.2f} threshold "
            f"(DOC_SWEEP_COST_CONFIRM_USD). Nothing was run. Please confirm with the user "
            f"before proceeding; an administrator can raise the threshold to allow this run."
        )

    def llm_call(prompt, system, max_tokens, temp):
        return aki._haiku_call_with_fallback(prompt, system=system, max_tokens=max_tokens, temp=temp)

    findings, skipped_timeout = [], []
    items = []
    for doc_id, content in contents.items():
        ident = content.get('filename') or f'Document {doc_id}'
        items.append((doc_id, ident, content.get('pages', {})))

    parallel = max(1, int(getattr(cfg, 'DOC_SWEEP_PARALLEL', 8)))
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {}
        for doc_id, ident, pages in items:
            if time.time() > deadline:
                skipped_timeout.append(ident)
                continue
            futures[pool.submit(_map_one_document, llm_call, routing_question, ident, pages)] = ident
        for fut in as_completed(futures):
            remaining = deadline - time.time()
            try:
                findings.append(fut.result(timeout=max(5.0, remaining)))
            except Exception as e:
                findings.append(dict(identifier=futures[fut], answer='', quote='', page=None,
                                     confidence=None, not_found=False, parse_fallback=False,
                                     error=str(e)[:200]))

    findings.sort(key=lambda f: f['identifier'])
    answered = [f for f in findings if f['answer'] and not f['error']]
    not_found = [f for f in findings if f['not_found'] and not f['answer']]
    errored = [f for f in findings if f['error']]
    low_conf = [f for f in findings if f['confidence'] is not None and f['confidence'] < 60]
    parse_fb = [f for f in findings if f['parse_fallback']]

    lines = [f"[Knowledge sweep — every document read in full]", ""]
    if answered:
        lines.append("FINDINGS PER DOCUMENT:")
        for f in answered:
            conf = f" (confidence {f['confidence']:.0f})" if f['confidence'] is not None else ''
            page = f", page {f['page']}" if f['page'] else ''
            lines.append(f"- {f['identifier']}{page}{conf}: {f['answer']}")
            if f['quote']:
                lines.append(f"    Evidence: \"{f['quote']}\"")
    if not_found:
        lines.append("")
        lines.append("DOCUMENTS THAT DO NOT ADDRESS THE QUESTION (read in full, nothing found):")
        for f in not_found:
            lines.append(f"- {f['identifier']}")
    if errored:
        lines.append("")
        lines.append("DOCUMENTS THAT COULD NOT BE READ (report these — do not guess their contents):")
        for f in errored:
            lines.append(f"- {f['identifier']}: {f['error']}")

    elapsed = time.time() - started
    ledger = (
        f"COVERAGE LEDGER: {len(scope)} document(s) in scope · "
        f"{len(findings) - len(errored)} read in full · {len(errored)} failed · "
        f"{len(skipped_timeout)} skipped (timeout) · {len(not_found)} silent on the question · "
        f"{len(low_conf)} low-confidence · {len(parse_fb)} parse-fallback"
    )
    if capped:
        ledger += f" · SCOPE CAPPED: {capped} document(s) beyond DOC_SWEEP_MAX_DOCS were NOT read"
    if skipped_timeout:
        ledger += f" · unread: {', '.join(skipped_timeout)}"
    ledger += f" · est cost ${est:.2f} · {elapsed:.0f}s"
    lines += ["", ledger,
              "Answer the user based ONLY on the findings above, and relay the coverage "
              "ledger so they know exactly what was and wasn't read. Every document has "
              "already been read in full — do NOT search per document again."]
    result = "\n".join(lines)
    _cache_put(cache_key, result)
    return result
