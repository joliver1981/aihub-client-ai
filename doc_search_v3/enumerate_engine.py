"""ENUMERATE — the counting engine. "How many of N documents…?" with a real N.

The flow the owner approved ("$3 once, then $0"):

    question -> allowed types (ACL) -> SQL document list  = THE DENOMINATOR
             -> is there a schema FIELD that answers this? (facts-first)
                  -> docs that already carry the field: answered from SQL, free
                  -> docs that don't: ONE verdict call each, in parallel,
                     and the verdict is WRITTEN BACK as that field
             -> deterministic roll-up (a value histogram + label Counter in
                Python — never an LLM counting passages)
             -> a ledger accounting for every document in the denominator

Honesty rules:
  * the ledger names what was NOT read (capped / failed / out of time) — a count
    without its denominator is a guess with good posture;
  * the deadline actually bounds the run (submit-loop-only deadlines are the
    documented sweep.py defect — this uses cancel_futures);
  * every enumerated verdict carries an evidence quote + page, and write-back
    populates DocumentFields.confidence (NULL everywhere else in the store).
"""

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import yaml

from doc_search_v3 import acl

_SCHEMA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'schemas')

_VERDICT_SYSTEM = (
    "You answer ONE question about ONE document. Respond with STRICT JSON only, "
    'no prose, no fences: {"label": "yes|yes_qualified|no|not_addressed", '
    '"value": "<the document\'s answer in a few words>", '
    '"evidence_quote": "<short verbatim quote>", "page": <int or null>, '
    '"confidence": <0-100>}. '
    "Rules: label 'yes' only when the document clearly satisfies the condition in "
    "full; 'yes_qualified' when it satisfies it partially, conditionally, or up to "
    "a threshold (state the qualification in value); 'no' when it clearly does "
    "not; 'not_addressed' when the document does not speak to the question at "
    "all. Never use outside knowledge; never guess."
)


def _connect():
    import pyodbc
    from CommonUtils import get_db_connection_string
    conn = pyodbc.connect(get_db_connection_string())
    cur = conn.cursor()
    cur.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
    return conn, cur


def _llm(prompt: str, system: str, max_tokens: int = 800) -> str:
    """One model call over the platform proxy (same transport as the extractors)."""
    import config as cfg
    from CommonUtils import AnthropicProxyClient
    client = AnthropicProxyClient()
    resp = client.messages_create(
        messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        model=cfg.ANTHROPIC_MODEL, max_tokens=max_tokens, system=system)
    content = resp.get('content') if isinstance(resp, dict) else None
    if not content:
        raise RuntimeError(f"LLM call failed: {str(resp)[:200]}")
    for block in content:
        if isinstance(block, dict) and block.get('type') == 'text' and block.get('text'):
            return block['text']
    raise RuntimeError("LLM returned no text block")


def _parse_json(raw: str) -> dict:
    m = re.search(r'\{[\s\S]*\}', raw or '')
    return json.loads(m.group(0)) if m else {}


def _schema_fields_for(types: List[str]) -> Dict[str, Dict[str, str]]:
    """{document_type: {field_path: description}} from the learned schemas."""
    out = {}
    try:
        for fn in os.listdir(_SCHEMA_DIR):
            if not fn.endswith(('.yml', '.yaml')):
                continue
            try:
                s = yaml.safe_load(open(os.path.join(_SCHEMA_DIR, fn),
                                        encoding='utf-8')) or {}
            except Exception:
                continue
            dt = s.get('document_type')
            if dt in types and isinstance(s.get('fields'), dict):
                out[dt] = {k: (v or {}).get('description') or k
                           for k, v in s['fields'].items()}
    except OSError:
        pass
    return out


def _pick_types(question: str, available: List[str]) -> List[str]:
    """Which document types is this question about? Mini-LLM, never keywords."""
    if len(available) == 1:
        return list(available)
    raw = _llm(
        f"Question: {question}\n\nDocument types available:\n"
        + "\n".join(f"- {t}" for t in sorted(available))
        + "\n\nReturn STRICT JSON: {\"types\": [\"<type>\", ...]} — the type(s) "
          "this question is asking about. Include every type that plausibly "
          "matches (lease_agreement AND lease_amendment for a lease question). "
          "Empty list if none match.",
        system="You map questions to document types. STRICT JSON only.",
        max_tokens=300)
    picked = (_parse_json(raw).get('types') or [])
    return [t for t in picked if t in available]


def _pick_field(question: str, fields_by_type: Dict[str, Dict[str, str]]) -> Optional[str]:
    """Does an existing schema field answer this question? Return its path or None."""
    listing = []
    for dt, fields in fields_by_type.items():
        for path, desc in fields.items():
            listing.append(f"- {path}  ({desc})")
    if not listing:
        return None
    raw = _llm(
        f"Question: {question}\n\nExtracted fields available on these documents:\n"
        + "\n".join(sorted(set(listing))[:150])
        + "\n\nReturn STRICT JSON: {\"field_path\": \"<exact path>\" } if ONE of "
          "these fields directly answers the question per document, or "
          "{\"field_path\": null} if none does. Do not stretch — a field must "
          "hold the answer itself, not merely be related.",
        system="You match questions to extracted document fields. STRICT JSON only.",
        max_tokens=200)
    fp = _parse_json(raw).get('field_path')
    if fp and any(fp in fields for fields in fields_by_type.values()):
        return fp
    return None


def _frame_predicate(question: str) -> dict:
    """Reframe the COUNT question once into (a) a crisp yes/no predicate asked
    of ONE document and (b) a value guide naming the short canonical answers.

    Without this, each per-doc call interprets the collective question its own
    way and the value histogram fragments into near-duplicate phrasings
    ('Tenant responsible', 'tenant', 'Tenant must maintain HVAC', ...) that
    GROUP BY can never reunite — seen on the first live Q4 run. One cheap call
    here buys comparable buckets everywhere. Fail-open: on any failure the raw
    question is used, which is exactly yesterday's behavior."""
    try:
        raw = _llm(
            f'The counting question over MANY documents is: "{question}"\n'
            'Return STRICT JSON: {"predicate": "<yes/no question asked of ONE '
            'document>", "value_guide": "<instruction naming the SHORT '
            'canonical values, e.g. one of: tenant / landlord / split (state '
            'threshold) / not addressed>"}. The predicate must be answerable '
            'from a single document with yes/no; the value_guide must force '
            'one-or-two-word canonical values so answers from different '
            'documents land in the same buckets.',
            system="You design per-document predicates for counting over "
                   "document sets. STRICT JSON only.",
            max_tokens=300)
        out = _parse_json(raw)
        if (isinstance(out, dict) and str(out.get('predicate') or '').strip()
                and str(out.get('value_guide') or '').strip()):
            return {'predicate': str(out['predicate']).strip()[:400],
                    'value_guide': str(out['value_guide']).strip()[:300]}
    except Exception as e:
        logging.info(f"v3 predicate framing skipped: {e}")
    return {'predicate': question, 'value_guide': ''}


def _verdict_one(question: str, doc_id: str, filename: str, cur_factory,
                 value_guide: str = '') -> dict:
    """One document, one verdict. Never raises."""
    v = dict(document_id=doc_id, filename=filename, status='enumerated',
             label=None, value=None, quote=None, page=None, confidence=None)
    try:
        conn, cur = cur_factory()
        cur.execute("""SELECT page_number, full_text FROM DocumentPages
                       WHERE document_id = ? ORDER BY page_number""", doc_id)
        pages = cur.fetchall()
        conn.close()
        if not pages:
            v.update(status='failed', value='no page text stored')
            return v
        text = "\n\n".join(f"[Page {pn}]\n{tx}" for pn, tx in pages if tx)[:480000]
        guide = (f"\nValue guide (use these canonical values): {value_guide}"
                 if value_guide else "")
        raw = _llm(f"Question: {question}{guide}\n\nDocument: {filename}\n"
                   f"--- DOCUMENT TEXT ---\n{text}\n--- END ---",
                   system=_VERDICT_SYSTEM, max_tokens=500)
        data = _parse_json(raw)
        label = str(data.get('label') or '').strip().lower()
        if label not in ('yes', 'yes_qualified', 'no', 'not_addressed'):
            v.update(status='failed', value=f'bad label: {label[:40]}')
            return v
        try:
            page = int(data.get('page')) if data.get('page') is not None else None
        except (TypeError, ValueError):
            page = None
        v.update(label=label, value=str(data.get('value') or '')[:300],
                 quote=str(data.get('evidence_quote') or '')[:500], page=page,
                 confidence=data.get('confidence'))
    except Exception as e:
        v.update(status='failed', value=str(e)[:200])
    return v


def _write_back(cur, conn, doc_id: str, field_path: str, verdict: dict):
    """Persist an enumerated verdict as a DocumentFields row so the NEXT ask is
    SQL. Keyed to the evidencing page (fields are page-scoped); replaces any
    previous v3 write for the same path."""
    page = verdict.get('page') or 1
    page_id = f"{doc_id}_p{page}"
    cur.execute("SELECT 1 FROM DocumentPages WHERE page_id = ?", page_id)
    if not cur.fetchone():
        page_id = f"{doc_id}_p1"
        cur.execute("SELECT 1 FROM DocumentPages WHERE page_id = ?", page_id)
        if not cur.fetchone():
            return
    field_name = field_path.split('.')[-1]
    cur.execute("""DELETE f FROM DocumentFields f
                   JOIN DocumentPages p ON f.page_id = p.page_id
                   WHERE p.document_id = ? AND f.field_path = ?""",
                doc_id, field_path)
    cur.execute("""INSERT INTO DocumentFields
                       (page_id, field_name, field_value, field_path, confidence)
                   VALUES (?, ?, ?, ?, ?)""",
                page_id, field_name[:255],
                verdict.get('value') or verdict.get('label'),
                field_path[:500],
                (float(verdict['confidence']) / 100.0
                 if isinstance(verdict.get('confidence'), (int, float)) else None))
    conn.commit()


def enumerate_documents(question: str, user_id=None, user_role=None,
                        document_type: Optional[str] = None) -> Dict[str, Any]:
    """The public entry point. Returns {ok, text, counts, ledger, verdicts, ...};
    {ok: False, denied: True} when the caller's grants resolve to deny-all."""
    import config as cfg
    started = time.time()
    deadline = started + int(getattr(cfg, 'DOC_V3_ENUM_TIMEOUT_S', 110))
    max_docs = int(getattr(cfg, 'DOC_SWEEP_MAX_DOCS', 1000))
    parallel = int(getattr(cfg, 'DOC_SWEEP_PARALLEL', 8))

    allowed = acl.accessible_document_types(user_id, user_role)
    if acl.deny_all(allowed):
        return {'ok': False, 'denied': True,
                'text': "You do not have access to any document categories. "
                        "An administrator can grant access on the Groups page."}

    conn, cur = _connect()
    try:
        cur.execute("""SELECT DISTINCT document_type FROM Documents
                       WHERE is_knowledge_document = 0""")
        present = [r[0] for r in cur.fetchall() if r[0]]
        if allowed is not None:
            present = [t for t in present if t in allowed]
        if document_type:
            present = [t for t in present if t == document_type]
        if not present:
            return {'ok': True, 'counts': {}, 'verdicts': [],
                    'text': "No accessible documents match this question's scope."}

        types = _pick_types(question, present) or present
        ph = ','.join('?' * len(types))
        cur.execute(f"""SELECT document_id, filename, document_type
                        FROM Documents
                        WHERE is_knowledge_document = 0
                          AND document_type IN ({ph})
                        ORDER BY filename""", *types)
        rows = cur.fetchall()
        docs = [(r[0], r[1]) for r in rows]
        doc_type_of = {r[0]: r[2] for r in rows}
        capped = max(0, len(docs) - max_docs)
        docs = docs[:max_docs]
        denominator = len(docs)
        if denominator == 0:
            return {'ok': True, 'counts': {}, 'verdicts': [],
                    'text': f"No documents of type {', '.join(types)} to count."}

        # ---- facts-first ---------------------------------------------------
        field_path = _pick_field(question, _schema_fields_for(types))
        pre = {}
        if field_path:
            cur.execute(f"""SELECT p.document_id,
                                   MAX(CAST(f.field_value AS NVARCHAR(300)))
                            FROM DocumentFields f
                            JOIN DocumentPages p ON f.page_id = p.page_id
                            JOIN Documents d ON d.document_id = p.document_id
                            WHERE f.field_path = ?
                              AND d.document_type IN ({ph})
                            GROUP BY p.document_id""", field_path, *types)
            pre = {r[0]: r[1] for r in cur.fetchall() if r[1] not in (None, '')}

        verdicts: List[dict] = []
        for doc_id, filename in docs:
            if doc_id in pre:
                verdicts.append(dict(document_id=doc_id, filename=filename,
                                     status='from_fields', label=None,
                                     value=pre[doc_id], quote=None, page=None,
                                     confidence=None))

        todo = [(d, f) for d, f in docs if d not in pre]

        # ---- enumerate the gap, deadline-bounded ---------------------------
        # ONE framing call turns the collective question into a per-document
        # predicate + canonical value buckets; every verdict below (and its
        # write-back) uses it, so stored values stay GROUP-BY-able.
        framing = _frame_predicate(question) if todo else \
            {'predicate': question, 'value_guide': ''}
        not_reached: List[str] = []
        if todo:
            pool = ThreadPoolExecutor(max_workers=parallel)
            futures = {pool.submit(_verdict_one, framing['predicate'], d, f,
                                   _connect, framing['value_guide']): (d, f)
                       for d, f in todo}
            try:
                for fut in as_completed(futures, timeout=max(5.0, deadline - time.time())):
                    verdicts.append(fut.result())
            except Exception:
                pass   # timeout — whatever hasn't completed is accounted below
            finally:
                done_ids = {v['document_id'] for v in verdicts}
                not_reached = [f for d, f in todo if d not in done_ids]
                pool.shutdown(wait=False, cancel_futures=True)

        # write-back so the next ask is SQL
        if field_path:
            for v in verdicts:
                if v['status'] == 'enumerated' and v.get('value'):
                    try:
                        _write_back(cur, conn, v['document_id'], field_path, v)
                    except Exception as e:
                        logging.warning(f"v3 write-back failed for "
                                        f"{v['document_id']}: {e}")

        # ---- deterministic roll-up ----------------------------------------
        from collections import Counter
        value_hist = Counter(str(v['value']).strip() for v in verdicts
                             if v.get('value') and v['status'] in
                             ('from_fields', 'enumerated'))
        label_hist = Counter(v['label'] for v in verdicts if v.get('label'))
        failed = [v for v in verdicts if v['status'] == 'failed']

        # Per-type accounting: "how many LEASES ..." must never be silently
        # inflated by amendments (or any second type) sharing the scope — the
        # breakdown makes each type's contribution visible (design decision,
        # 2026-08-13).
        answered_ids = set()
        by_type: Dict[str, Dict[str, Any]] = {}
        for d, _f in docs:
            t = doc_type_of.get(d) or 'unknown'
            by_type.setdefault(t, {'in_scope': 0, 'from_fields': 0,
                                   'enumerated': 0, 'failed': 0,
                                   'not_reached': 0, 'labels': {}})
            by_type[t]['in_scope'] += 1
        for v in verdicts:
            t = doc_type_of.get(v['document_id']) or 'unknown'
            bt = by_type.get(t)
            if not bt:
                continue
            answered_ids.add(v['document_id'])
            if v['status'] in ('from_fields', 'enumerated'):
                bt[v['status']] += 1
                if v.get('label'):
                    bt['labels'][v['label']] = bt['labels'].get(v['label'], 0) + 1
            elif v['status'] == 'failed':
                bt['failed'] += 1
        for d, _f in docs:
            if d not in answered_ids:
                t = doc_type_of.get(d) or 'unknown'
                by_type[t]['not_reached'] += 1

        lines = [f"COUNT over {denominator} document(s) "
                 f"({', '.join(types)}) — question: {question}"]
        if framing['predicate'] != question:
            lines.append(f"Predicate applied to each document: "
                         f"{framing['predicate']}")
        if field_path:
            lines.append(f"Field consulted: {field_path}")
        if value_hist:
            lines.append("ANSWER BREAKDOWN (by document's answer):")
            for val, n in value_hist.most_common(15):
                lines.append(f"  {n:>4}  {val[:90]}")
        if label_hist:
            lines.append("VERDICTS (enumerated documents):")
            for lab in ('yes', 'yes_qualified', 'no', 'not_addressed'):
                if label_hist.get(lab):
                    lines.append(f"  {label_hist[lab]:>4}  {lab}")
        if len(by_type) > 1:
            lines.append("BY DOCUMENT TYPE (a count of one type must not "
                         "absorb another's documents):")
            for t in sorted(by_type):
                bt = by_type[t]
                labs = " · ".join(f"{n} {lab}" for lab, n in
                                  sorted(bt['labels'].items())) or "no verdicts"
                lines.append(
                    f"  {t}: {bt['in_scope']} in scope — {labs}"
                    f" — {bt['from_fields']} from fields, "
                    f"{bt['enumerated']} read now, {bt['failed']} failed, "
                    f"{bt['not_reached']} not reached")
        ledger = (f"LEDGER: {denominator} in scope · {len(pre)} answered from "
                  f"stored fields · "
                  f"{sum(1 for v in verdicts if v['status'] == 'enumerated')} read "
                  f"now (answers saved for next time) · {len(failed)} failed · "
                  f"{len(not_reached)} not reached before the time limit")
        if capped:
            ledger += f" · SCOPE CAPPED: {capped} more document(s) NOT counted"
        lines.append(ledger)
        if not_reached:
            lines.append("Not reached: " + ", ".join(not_reached[:10])
                         + (" …" if len(not_reached) > 10 else ""))
        if failed:
            lines.append("Failed: " + ", ".join(f"{v['filename']}" for v in failed[:6]))
        lines.append("Relay the LEDGER with the numbers — a count without its "
                     "denominator and coverage is not an answer.")

        return {'ok': True, 'engine': 'doc_search_v3.enumerate',
                'question': question, 'types': types,
                'predicate': framing['predicate'],
                'denominator': denominator, 'field_path': field_path,
                'counts': dict(value_hist), 'labels': dict(label_hist),
                'by_type': by_type,
                'verdicts': verdicts, 'capped': capped,
                'not_reached': not_reached,
                'elapsed_s': round(time.time() - started, 1),
                'text': "\n".join(lines)}
    finally:
        try:
            conn.close()
        except Exception:
            pass
