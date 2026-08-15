"""Shared read path for DocumentRecords — the rows extracted from repeating document
content (a manual's requirements, an invoice's line items).

ONE implementation, three surfaces: The Agent and Command Center reach it through
POST /api/internal/document-records; GeneralAgent calls it in-process. The honesty
rules live HERE so every surface inherits them:

  * Every answer carries a COVERAGE frame built from the __manifest rows, because
    "4 guides require X" silently means "4 of the ones we extracted" without it.
    A document with no manifest was NEVER extracted — that is not the same as a
    document that states no such requirement.
  * A truncated extraction (manifest status 'partial') is surfaced: matches from it
    are a floor, not a census.
  * No matches / no record sets is NOT a dead end: the response says so and directs
    the agent to fall back to search_documents (james, 2026-08-15: fallback with a
    warning, never a hard stop).

Modes (one function, so surfaces register ONE tool):
  no filters            -> LIST: which record sets exist, their size and coverage
  record_set / search / topic / document_type -> QUERY: matching rows + coverage
"""

import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

import yaml

_SCHEMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'schemas')
_EXCERPT_DISPLAY_CHARS = 240
_IDENT_RE = re.compile(r'^[a-z0-9_]{1,64}$')   # format guard, not NL interpretation

_map_lock = threading.Lock()
_map_cache: Dict[str, Any] = {'at': 0.0, 'value': {}}
_MAP_TTL_S = 60


def _connect():
    import pyodbc
    from CommonUtils import get_db_connection_string
    conn = pyodbc.connect(get_db_connection_string())
    cur = conn.cursor()
    cur.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
    return conn, cur


def get_types_with_records() -> Dict[str, str]:
    """{document_type: record_set_name} for every schema declaring a record set.

    Read from the schema files (the source of truth for what a type EXTRACTS),
    cached briefly — this also serves the search-result hint, which runs on every
    search call and must stay cheap.
    """
    now = time.time()
    with _map_lock:
        if now - _map_cache['at'] < _MAP_TTL_S:
            return dict(_map_cache['value'])
    mapping: Dict[str, str] = {}
    try:
        for fn in os.listdir(_SCHEMA_DIR):
            if not fn.endswith(('.yml', '.yaml')):
                continue
            try:
                with open(os.path.join(_SCHEMA_DIR, fn), 'r', encoding='utf-8') as f:
                    schema = yaml.safe_load(f) or {}
                doc_type = schema.get('document_type')
                records = schema.get('records') or {}
                if doc_type and isinstance(records, dict) and records:
                    mapping[str(doc_type)] = next(iter(records))
            except Exception:
                continue
    except OSError:
        pass
    with _map_lock:
        _map_cache['at'] = now
        _map_cache['value'] = dict(mapping)
    return mapping


def _coverage(cur, record_set: str,
              allowed_document_types: Optional[List[str]]) -> Dict[str, Any]:
    """The denominator: of the documents whose type declares this record set, how
    many were actually extracted, and how many of those runs were partial."""
    types = sorted(t for t, s in get_types_with_records().items() if s == record_set)
    if allowed_document_types:
        types = [t for t in types if t in allowed_document_types]
    out = {'record_set': record_set, 'document_types': types,
           'docs_total': 0, 'docs_extracted': 0, 'docs_partial': 0}
    if not types:
        return out
    ph = ','.join('?' * len(types))
    cur.execute(f"""SELECT COUNT(*) FROM Documents
                    WHERE is_knowledge_document = 0 AND document_type IN ({ph})""",
                *types)
    out['docs_total'] = cur.fetchone()[0]
    cur.execute("""SELECT r.row_json FROM DocumentRecords r
                   JOIN Documents d ON d.document_id = r.document_id
                   WHERE r.record_set = '__manifest'
                     AND JSON_VALUE(r.row_json, '$.record_set') = ?""", record_set)
    for (rj,) in cur.fetchall():
        try:
            m = json.loads(rj)
        except Exception:
            continue
        out['docs_extracted'] += 1
        if m.get('status') == 'partial':
            out['docs_partial'] += 1
    return out


def _coverage_line(cov: Dict[str, Any]) -> str:
    missing = max(0, cov['docs_total'] - cov['docs_extracted'])
    line = (f"COVERAGE: {cov['docs_extracted']} of {cov['docs_total']} "
            f"{'/'.join(cov['document_types']) or '(unknown type)'} document(s) "
            f"have '{cov['record_set']}' records extracted")
    notes = []
    if missing:
        notes.append(f"{missing} NOT extracted — absent from these results, "
                     f"not absent from the documents")
    if cov['docs_partial']:
        notes.append(f"{cov['docs_partial']} extraction(s) partial — matches are "
                     f"a floor, not a census")
    return line + ((" (" + "; ".join(notes) + ")") if notes else "") + "."


def query_document_records(record_set: Optional[str] = None,
                           search: Optional[str] = None,
                           topic: Optional[str] = None,
                           document_type: Optional[str] = None,
                           limit: int = 50,
                           allowed_document_types: Optional[List[str]] = None
                           ) -> Dict[str, Any]:
    """The one entry point. See module docstring for modes and honesty rules.

    allowed_document_types mirrors the search engine's existing per-agent ACL
    parameter: None/empty = unrestricted (today's posture), a non-empty list
    restricts every mode to those types.
    """
    limit = max(1, min(int(limit or 50), 200))
    record_set = (record_set or '').strip().lower() or None
    if record_set and not _IDENT_RE.match(record_set):
        return {'ok': False, 'error': f"invalid record_set name: {record_set!r}"}

    try:
        conn, cur = _connect()
    except Exception as e:
        return {'ok': False, 'error': f"database unavailable: {e}"}

    try:
        if not any([record_set, search, topic]):
            return _list_mode(cur, document_type, allowed_document_types)
        return _query_mode(cur, record_set, search, topic, document_type,
                           limit, allowed_document_types)
    except Exception as e:
        return {'ok': False, 'error': str(e)[:300]}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _list_mode(cur, document_type, allowed_document_types) -> Dict[str, Any]:
    cur.execute("""SELECT r.record_set, COUNT(*), COUNT(DISTINCT r.document_id)
                   FROM DocumentRecords r
                   WHERE r.record_set <> '__manifest'
                   GROUP BY r.record_set ORDER BY COUNT(*) DESC""")
    sets = []
    for name, rows, docs in cur.fetchall():
        cov = _coverage(cur, name, allowed_document_types)
        if allowed_document_types and not cov['document_types']:
            continue   # set belongs entirely to types this caller may not see
        if document_type and document_type not in cov['document_types']:
            continue
        cur.execute("""SELECT TOP 8 JSON_VALUE(row_json,'$.topic'), COUNT(*)
                       FROM DocumentRecords WHERE record_set = ?
                       GROUP BY JSON_VALUE(row_json,'$.topic')
                       ORDER BY COUNT(*) DESC""", name)
        topics = [t for t, _ in cur.fetchall() if t]
        sets.append({'record_set': name, 'rows': rows, 'documents': docs,
                     'topics': topics, 'coverage': cov})

    if not sets:
        return {'ok': True, 'mode': 'list', 'sets': [], 'fallback': True,
                'text': ("NO RECORD SETS EXIST yet for these documents. Structured "
                         "record extraction has not produced rows here — fall back "
                         "to search_documents and answer from page text, and say "
                         "plainly that the answer comes from reading pages, not "
                         "from a structured table.")}

    lines = ["AVAILABLE RECORD SETS:"]
    for s in sets:
        lines.append(f"- {s['record_set']}: {s['rows']} row(s) across "
                     f"{s['documents']} document(s)"
                     + (f" · topics: {', '.join(s['topics'])}" if s['topics'] else ""))
        lines.append("  " + _coverage_line(s['coverage']))
    lines.append("Query with record_set plus search/topic filters. For questions "
                 "these sets do not cover, fall back to search_documents.")
    return {'ok': True, 'mode': 'list', 'sets': sets, 'fallback': False,
            'text': "\n".join(lines)}


def _query_mode(cur, record_set, search, topic, document_type, limit,
                allowed_document_types) -> Dict[str, Any]:
    where = ["r.record_set <> '__manifest'"]
    params: List[Any] = []
    if record_set:
        where.append("r.record_set = ?")
        params.append(record_set)
    if topic:
        where.append("JSON_VALUE(r.row_json,'$.topic') = ?")
        params.append(topic)
    if search:
        where.append("(r.row_json LIKE ? OR r.excerpt LIKE ?)")
        params += [f'%{search}%', f'%{search}%']
    if document_type:
        where.append("d.document_type = ?")
        params.append(document_type)
    if allowed_document_types:
        ph = ','.join('?' * len(allowed_document_types))
        where.append(f"d.document_type IN ({ph})")
        params += list(allowed_document_types)

    cur.execute(f"""SELECT TOP ({int(limit)}) d.filename, d.document_id,
                           r.record_set, r.row_index, r.row_json,
                           r.source_pages, r.excerpt
                    FROM DocumentRecords r
                    JOIN Documents d ON d.document_id = r.document_id
                    WHERE {' AND '.join(where)}
                    ORDER BY d.filename, r.record_set, r.row_index""", *params)

    rows = []
    sets_seen = set()
    for filename, doc_id, rset, idx, rj, pages, excerpt in cur.fetchall():
        try:
            data = json.loads(rj)
        except Exception:
            data = {}
        data.pop('excerpt', None)   # rendered separately, truncated
        rows.append({'filename': filename, 'document_id': doc_id,
                     'record_set': rset, 'row_index': idx, 'data': data,
                     'source_pages': pages,
                     'excerpt': (excerpt or '')[:_EXCERPT_DISPLAY_CHARS]})
        sets_seen.add(rset)

    coverages = [_coverage(cur, s, allowed_document_types)
                 for s in sorted(sets_seen or ({record_set} if record_set else set()))]

    if not rows:
        cov_txt = "\n".join(_coverage_line(c) for c in coverages if c['document_types'])
        return {'ok': True, 'mode': 'query', 'rows': [], 'fallback': True,
                'coverage': coverages,
                'text': ("NO MATCHING RECORDS." + (f"\n{cov_txt}" if cov_txt else "")
                         + "\nThis means no extracted row matched — it does NOT "
                           "prove the documents are silent. Fall back to "
                           "search_documents for a page-text answer, and if "
                           "coverage above shows unextracted documents, say the "
                           "answer may be incomplete.")}

    lines = [f"RECORDS: {len(rows)} matching row(s)"
             + (f" (showing first {limit})" if len(rows) == limit else "")]
    for r in rows:
        d = r['data']
        head = f"[{r['filename']} p.{r['source_pages'] or '?'}]"
        t = d.get('topic')
        if t:
            head += f" ({t})"
        lines.append(head)
        for k, v in d.items():
            if k in ('topic', 'source_pages', 'source_page') or v in (None, ''):
                continue
            lines.append(f"    {k}: {str(v)[:160]}")
        if r['excerpt']:
            lines.append(f"    \"{r['excerpt']}\"")
    for c in coverages:
        if c['document_types']:
            lines.append(_coverage_line(c))
    return {'ok': True, 'mode': 'query', 'rows': rows, 'fallback': False,
            'coverage': coverages, 'text': "\n".join(lines)}
