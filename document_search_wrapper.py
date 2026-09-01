"""Consistent-shape wrapper over the document repository search (James, 2026-08-11).

`DocUtils.document_search_super_enhanced_debug` is an LLM-planned search whose
return TYPE varies by the approach its planner chooses:
  - field / hybrid  -> a JSON-string of {results:[…], query_analysis:{…}, …}
  - semantic        -> a pre-formatted "[Source N: file - Page p] (type)
                       (Relevance: 0.42)\\n<text>\\n Document URL: …" text blob
  - (error)         -> a JSON-string {"error": "…"}
Consumers that expect one shape silently break on the other (this is exactly
what made The Agent's search_documents report 0 hits on semantic-path queries).

This module is ADDITIVE and does not touch the legacy function or its existing
/api/internal/document-search endpoint. It calls the same enhanced engine and
NORMALIZES the result into a single stable schema so The Agent and Command
Center can share one contract:

    {
      "ok": bool,
      "engine": "repository_super_search",
      "query": str,
      "approach": str|None,            # semantic|field|hybrid|… when known
      "passages": [                    # ALWAYS a list
        {"filename","page","document_id","document_type","text","relevance","fields"}
      ],
      "answer": str|None,              # engine's synthesized text, when it returns one
      "text": str,                     # always-present readable rendering (LLM ctx / passthrough)
      "count": int,
      "query_analysis": dict,
      "error": str|None,
    }

Legacy callers keep using document_search_super_enhanced_debug directly.
"""
import json
import re

import config as cfg
from CommonUtils import get_db_connection_string

# Repeated "[Source N: <file> - Page <p>] (<type>) (Relevance: r)\n<text>" blocks
# (the semantic-approach rendering from DocUtils.format_search_results_for_ai).
_SOURCE_BLOCK_RE = re.compile(
    r"\[Source\s+\d+:\s*(?P<file>.+?)\s*-\s*Page\s*(?P<page>[^\]]*?)\]\s*"
    r"(?:\((?P<type>[^)]*)\)\s*)?\(Relevance:\s*(?P<rel>[\d.]+)\)[ \t]*\n?"
    r"(?P<text>.*?)(?=\n\[Source\s+\d+:|\Z)",
    re.S,
)


def _passages_from_source_blocks(blob: str) -> list:
    out = []
    for m in _SOURCE_BLOCK_RE.finditer(blob or ""):
        body = m.group("text") or ""
        did = None
        um = re.search(r"Document URL:\s*(\S+)", body)
        if um:
            body = body[:um.start()].rstrip()
            dm = re.search(r"/document/view/([^?\s]+)", um.group(1))
            if dm:
                did = dm.group(1)
        out.append({
            "filename": (m.group("file") or "").strip(),
            "page": (m.group("page") or "").strip(),
            "document_id": did,
            "document_type": (m.group("type") or "").strip() or None,
            "text": body.strip(),
            "relevance": _to_float(m.group("rel")),
            "fields": {},
        })
    return out


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pick(row: dict, *keys, default=None):
    low = {str(k).lower(): v for k, v in row.items()}
    for k in keys:
        v = low.get(k)
        if v not in (None, ""):
            return v
    return default


def _passages_from_dict(payload: dict) -> list:
    out = []
    for row in (payload.get("results") or []):
        if not isinstance(row, dict):
            continue
        out.append({
            "filename": _pick(row, "filename", "file_name", "document_name",
                              "source", default="(unknown file)"),
            "page": _pick(row, "page_number", "page", "page_no"),
            "document_id": _pick(row, "document_id", "doc_id"),
            "document_type": _pick(row, "document_type", "doc_type"),
            "text": _pick(row, "snippet", "matched_text", "highlight",
                          "full_text", "page_text", "text", "content",
                          "chunk", default=""),
            "relevance": _to_float(_pick(row, "relevance_score", "relevance",
                                         "score")),
            "fields": row.get("relevant_fields") if isinstance(
                row.get("relevant_fields"), dict) else {},
        })
    return out


def _render_text(passages: list, answer: str | None) -> str:
    if answer and not passages:
        return answer.strip()
    lines = []
    for p in passages:
        loc = f" p.{p['page']}" if p.get("page") not in (None, "") else ""
        did = f" [id {p['document_id']}]" if p.get("document_id") else ""
        head = f"[{p.get('filename') or '(unknown file)'}{loc}]{did}"
        fields = p.get("fields") or {}
        if fields:
            kv = ", ".join(f"{k}={v}" for k, v in list(fields.items())[:8]
                           if v not in (None, ""))
            if kv:
                head += f"\n  fields: {kv}"
        body = " ".join(str(p.get("text") or "").split())
        lines.append(head + (f"\n  {body}" if body else ""))
    return "\n\n".join(lines)


def normalize_search_result(raw, query: str) -> dict:
    """Turn document_search_super_enhanced_debug's variable return into the
    stable schema. `raw` is the engine's return (a str; occasionally already a
    dict if a caller pre-parsed)."""
    payload = raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            payload = raw  # semantic [Source …] blob — not JSON

    approach = None
    query_analysis = {}
    error = None
    answer = None

    if isinstance(payload, dict):
        passages = _passages_from_dict(payload)
        query_analysis = payload.get("query_analysis") or {}
        approach = query_analysis.get("search_approach")
        error = payload.get("error")
    elif isinstance(payload, str) and payload.strip():
        passages = _passages_from_source_blocks(payload)
        approach = "semantic"
        if not passages:
            # Non-empty but unrecognized text (e.g. "No relevant documents
            # found.") — surface it as the answer rather than dropping it.
            answer = payload.strip()
    else:
        passages = []

    text = _render_text(passages, answer)
    return {
        "ok": error is None,
        "engine": "repository_super_search",
        "query": query,
        "approach": approach,
        "passages": passages,
        "answer": answer,
        "text": text,
        "count": len(passages),
        "query_analysis": query_analysis,
        "error": error,
    }


def _empty_result(question: str, engine: str, text: str, error=None) -> dict:
    return {"ok": error is None, "engine": engine, "query": question,
            "approach": None, "passages": [], "answer": text or None,
            "text": text, "count": 0, "query_analysis": {}, "error": error}


def _question_shape(question: str) -> str:
    """COUNT (which/how-many across documents) vs LOOKUP. Mini-LLM per the
    platform directive — never keywords. Any failure -> LOOKUP (legacy path)."""
    try:
        from doc_search_v3.enumerate_engine import _llm, _parse_json
        raw = _llm(
            f"Question: {question}\n\n"
            'Return STRICT JSON: {"shape": "COUNT"} when the answer is a count '
            'or list ACROSS MANY documents ("how many leases...", "which guides '
            'require...", "list every document that..."), or {"shape": "LOOKUP"} '
            "when it asks what specific documents say (facts, passages, "
            "summaries, single-document questions).",
            system="You classify question shapes. STRICT JSON only.",
            max_tokens=60)
        shape = str(_parse_json(raw).get("shape") or "").upper()
        return shape if shape in ("COUNT", "LOOKUP") else "LOOKUP"
    except Exception:
        return "LOOKUP"


def document_search_unified(question: str, max_results: int | None = None,
                            check_completeness: bool | None = None,
                            conn_string: str | None = None,
                            user_id=None, user_role=None) -> dict:
    """Additive facade: route by question shape, then run the right engine and
    return the stable normalized schema. The Agent and Command Center call this
    (via the internal endpoint) instead of parsing raw engine output.

    v3 (2026-08-15): COUNT-shaped questions go to doc_search_v3.enumerate — a
    real denominator, one verdict per document, deterministic roll-up — and
    LOOKUP questions run the legacy engine, now carrying the caller's category
    allow list. Identity is optional: absent -> today's unrestricted posture.
    Every v3 error falls back to legacy, never to a dead end."""
    question = (question or "").strip()
    if not question:
        return _empty_result(question, "repository_super_search", "",
                             error="question is required")

    allowed = None
    try:
        from doc_search_v3 import acl
        allowed = acl.accessible_document_types(user_id, user_role)
        if acl.deny_all(allowed):
            # MUST stop here: the legacy engine treats an empty allow list as
            # NO FILTER (fail-open) — deny-all may never reach it.
            return _empty_result(
                question, "doc_search_v3.acl",
                "You do not have access to any document categories. An "
                "administrator can grant access on the Groups page.")
    except Exception as e:
        # ACL machinery itself failing must not take search down for the
        # identity-less callers that predate it.
        if user_id not in (None, "", 0):
            return _empty_result(question, "doc_search_v3.acl",
                                 "Access could not be verified — try again.",
                                 error=str(e)[:200])
        allowed = None

    if getattr(cfg, "DOC_SEARCH_V3_ENABLED", True) \
            and _question_shape(question) == "COUNT":
        try:
            from doc_search_v3.enumerate_engine import enumerate_documents
            v3 = enumerate_documents(question, user_id=user_id,
                                     user_role=user_role)
            if v3.get("ok") or v3.get("denied"):
                result = _empty_result(question, "doc_search_v3.enumerate",
                                       v3.get("text") or "")
                result["approach"] = "enumerate"
                result["count"] = v3.get("denominator") or 0
                result["v3"] = {k: v3.get(k) for k in
                                ("denominator", "types", "field_path", "counts",
                                 "labels", "capped", "not_reached", "elapsed_s")}
                return result
        except Exception as e:
            import logging
            logging.error(f"doc_search_v3 enumerate failed ({e}) — "
                          f"falling back to legacy search")

    from DocUtils import document_search_super_enhanced_debug
    cs = conn_string or get_db_connection_string()
    mr = int(max_results) if max_results else int(getattr(cfg, "DOC_SEARCH_LIMIT", 800))
    cc = (check_completeness if check_completeness is not None
          else bool(getattr(cfg, "DOC_CHECK_COMPLETENESS", False)))
    raw = document_search_super_enhanced_debug(
        cs, user_question=question, max_results=mr, check_completeness=cc,
        allowed_document_types=allowed)

    # AMBIGUITY HINT — engine-computed (DocUtils._competing_documents_hint):
    # several same-type documents all matched every entity the user named.
    # The engine appends it inline for the direct GA lane; here it must be
    # lifted OUT of the raw blob before normalization (the last [Source …]
    # block's text runs to end-of-string, so a trailing note would be
    # swallowed into that passage) and re-attached after, exactly like
    # records_hint below. Single-line extraction — format check only.
    ambiguity_hint = None
    try:
        if isinstance(raw, str) and raw.strip():
            if raw.lstrip().startswith("{"):
                try:
                    ambiguity_hint = (json.loads(raw) or {}).get("ambiguity_hint")
                except (TypeError, ValueError):
                    ambiguity_hint = None
            else:
                m = re.search(r"^AMBIGUITY NOTE:.*$", raw, re.M)
                if m:
                    ambiguity_hint = m.group(0).strip()
                    raw = (raw[:m.start()] + raw[m.end():]).rstrip()
    except Exception:
        ambiguity_hint = None   # additive only; never break search

    result = normalize_search_result(raw, question)

    if ambiguity_hint:
        result["ambiguity_hint"] = ambiguity_hint
        if result.get("text"):
            result["text"] = result["text"] + "\n\n" + ambiguity_hint

    # RECORDS HINT — the discovery bridge. When search returns pages from document
    # types that carry structured record sets, tell the model so at exactly the
    # moment it matters: a which/how-many question answered by counting passages is
    # the confident-wrong-number failure, and the records tool is the fix. Appended
    # AFTER normalization so the [Source …] parsing contract is untouched; CC reads
    # result["text"], The Agent renders records_hint explicitly — both see it.
    try:
        from document_records_query import get_types_with_records
        type_map = get_types_with_records()   # cached, cheap
        hit_types = sorted({p.get("document_type") for p in result["passages"]
                            if p.get("document_type")} & set(type_map))
        if hit_types:
            sets = sorted({type_map[t] for t in hit_types})
            hint = (f"NOTE: structured record rows exist for "
                    f"{', '.join(hit_types)} documents (record set(s): "
                    f"{', '.join(sets)}). For 'which documents…' or 'how many…' "
                    f"questions, query the document-records tool instead of "
                    f"counting these passages — passages are a relevance sample, "
                    f"not a census.")
            result["records_hint"] = hint
            if result.get("text"):
                result["text"] = result["text"] + "\n\n" + hint
    except Exception:
        pass   # the hint is an enhancement; search must never fail because of it

    return result
