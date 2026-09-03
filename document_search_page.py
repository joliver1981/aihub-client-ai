"""Server side of the /document-search page — rebuilt 2026-09-03 (james: "I
cannot release this page to clients this way").

What changed and why (app.py holds only thin routes over this module so the
logic is unit-testable without the 17k-line app):

  * ACL everywhere. Every list, count, suggestion and search on the page is
    scoped to the caller's v3 category ACL (doc_search_v3.acl) — resolved by
    the route from the session (or a service assertion). Three-state: None =
    unrestricted, [types] = only those, [] = deny-all (the page renders the
    access message and NOTHING is queried).
  * Category tree instead of an all-types list: the sidebar shows the v3
    categories (grants are per category, and they collapse the spelling
    drift of raw types) with their visible types and document counts.
  * Fields only after a type/category is chosen, by type-ahead from the
    per-type field catalog (document_field_catalog) — never the 8.8k-name
    dropdown, never a GROUP BY over DocumentFields at render time.
  * Free-text search runs through document_search_unified — the SAME ACL'd
    path The Agent and Command Center use — with the sidebar's scope as
    document_types (which can only narrow the ACL).
  * Field / attribute filters are exact SQL over DocumentFields /
    DocumentAttributions, AND-ed per document (a document qualifies when
    every criterion matches on one of its pages), scoped to the ACL.

The results contract the template renders:
    {document_id, filename, document_type, page_number, relevance_score,
     snippet (escaped + highlighted), processed_at, reference_number,
     document_date, matching_fields:[{name, value}]}
"""
import html
import logging
import re
import threading
import time
from math import ceil
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

OPERATORS = ("equals", "contains", "starts_with", "ends_with")
MAX_PER_PAGE = 50
SEARCH_PASSAGES = 200         # passages asked from the engine; paginated here
_ATTR_TTL = 60
_attr_cache: Dict[tuple, Tuple[float, list]] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------- request
class SearchRequest:
    def __init__(self, query="", document_type="", category="", field_filters=None,
                 attribute_filters=None, search_mode="fields", min_score=0.5,
                 max_results=10, page=1):
        self.query = (query or "").strip()
        self.document_type = (document_type or "").strip()
        self.category = (category or "").strip()
        self.field_filters = list(field_filters or [])
        self.attribute_filters = list(attribute_filters or [])
        self.search_mode = search_mode if search_mode in ("fields", "attributes", "language") else "fields"
        self.min_score = min_score
        self.max_results = max_results
        self.page = page

    @property
    def has_criteria(self) -> bool:
        return bool(self.query or self.field_filters or self.attribute_filters)


def _num(value, default, lo, hi, cast=int):
    try:
        v = cast(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def parse_request(args) -> SearchRequest:
    """From Flask's request.args (a MultiDict — arrays come as name[] lists).
    Format validation only; nothing here interprets natural language."""
    def _list(name):
        try:
            return args.getlist(name)
        except AttributeError:
            v = args.get(name)
            return v if isinstance(v, list) else ([v] if v else [])

    field_filters = []
    names, ops, values = _list("field_name[]"), _list("field_operator[]"), _list("field_value[]")
    for i, path in enumerate(names):
        op = ops[i] if i < len(ops) else "equals"
        val = values[i] if i < len(values) else ""
        if path and str(val).strip():
            leaf = path.split(".")[-1] if "." in path else path
            field_filters.append({
                "field_path": path,
                "display_name": "Any field" if path == "%" else leaf.replace("_", " ").title(),
                "operator": op if op in OPERATORS else "equals",
                "value": str(val).strip(),
            })
    attribute_filters = []
    a_names, a_ops, a_values = _list("attribute_name[]"), _list("attribute_operator[]"), _list("attribute_value[]")
    for i, name in enumerate(a_names):
        op = a_ops[i] if i < len(a_ops) else "equals"
        val = a_values[i] if i < len(a_values) else ""
        if str(name).strip() and str(val).strip():
            attribute_filters.append({"attribute_name": str(name).strip(),
                                      "operator": op if op in OPERATORS else "equals",
                                      "value": str(val).strip()})
    mode = args.get("search_mode") or ("language" if "advanced" in args else "fields")
    return SearchRequest(
        query=args.get("query", ""),
        document_type=args.get("document_type", ""),
        category=args.get("category", ""),
        field_filters=field_filters,
        attribute_filters=attribute_filters,
        search_mode=mode,
        min_score=_num(args.get("min_score", 0.5), 0.5, 0.0, 1.0, float),
        max_results=_num(args.get("max_results", 10), 10, 1, MAX_PER_PAGE),
        page=_num(args.get("page", 1), 1, 1, 100000),
    )


# ---------------------------------------------------------------- scope
def _in(values: Sequence) -> str:
    return ",".join("?" * len(values))


def category_tree(cur, allowed: Optional[List[str]]) -> dict:
    """The sidebar. Only types the caller may see; categories with no visible
    type are omitted; types with no active category mapping appear under
    'Uncategorised' ONLY for unrestricted callers (a restricted user's grants
    are per category, so an allowed type always has one)."""
    if allowed is not None and len(allowed) == 0:
        return {"categories": [], "uncategorised": [], "total_docs": 0, "total_types": 0}
    where = "WHERE is_knowledge_document = 0"
    params: list = []
    if allowed is not None:
        where += f" AND document_type IN ({_in(allowed)})"
        params = list(allowed)
    cur.execute(f"""SELECT document_type, COUNT(*) FROM Documents {where}
                    GROUP BY document_type""", *params)
    counts = {str(r[0]): int(r[1]) for r in cur.fetchall() if r[0]}
    cur.execute("""SELECT c.category_id, c.category_slug, c.category_name, c.description,
                          tc.document_type
                   FROM DocumentCategories c
                   JOIN DocumentTypeCategories tc ON tc.category_id = c.category_id
                   WHERE tc.status = 'active'
                   ORDER BY c.category_name, tc.document_type""")
    cats: Dict[int, dict] = {}
    mapped: set = set()
    for cid, slug, name, desc, dtype in cur.fetchall():
        dtype = str(dtype)
        mapped.add(dtype)
        if dtype not in counts:
            continue                          # no visible document of that type
        c = cats.setdefault(int(cid), {"category_id": int(cid), "slug": slug, "name": name,
                                       "description": desc or "", "doc_count": 0, "types": []})
        c["types"].append({"name": dtype, "count": counts[dtype]})
        c["doc_count"] += counts[dtype]
    uncategorised = []
    if allowed is None:
        uncategorised = [{"name": t, "count": n} for t, n in sorted(counts.items()) if t not in mapped]
    categories = sorted(cats.values(), key=lambda c: (-c["doc_count"], c["name"]))
    return {"categories": categories, "uncategorised": uncategorised,
            "total_docs": sum(counts.values()), "total_types": len(counts)}


def category_types(cur, slug: str) -> List[str]:
    cur.execute("""SELECT tc.document_type FROM DocumentTypeCategories tc
                   JOIN DocumentCategories c ON c.category_id = tc.category_id
                   WHERE c.category_slug = ? AND tc.status = 'active'""", slug)
    return sorted({str(r[0]) for r in cur.fetchall() if r[0]})


def resolve_scope(cur, allowed: Optional[List[str]], req: SearchRequest) -> dict:
    """{"types": None|[...], "label": str, "denied": bool, "message": str}.
    types None = unrestricted and nothing selected. A selected type/category
    with no accessible type is `denied` — an honest refusal, never an empty
    list (which downstream IN-builders would read as NO filter)."""
    deny_msg = ("The selected document type is not accessible to you. An "
                "administrator can grant access on the Groups page.")
    if req.document_type:
        if allowed is not None and req.document_type not in allowed:
            return {"types": [], "label": req.document_type, "denied": True, "message": deny_msg}
        return {"types": [req.document_type], "label": req.document_type, "denied": False, "message": ""}
    if req.category:
        types = category_types(cur, req.category)
        if allowed is not None:
            types = [t for t in types if t in allowed]
        if not types:
            return {"types": [], "label": req.category, "denied": True, "message": deny_msg}
        return {"types": types, "label": req.category, "denied": False, "message": ""}
    return {"types": list(allowed) if allowed is not None else None, "label": "",
            "denied": False, "message": ""}


# ---------------------------------------------------------------- suggestions
def field_suggestions(cur, types: Optional[List[str]], q: str, limit: int = 50) -> dict:
    """Type-ahead over the field catalog. Needs a concrete scope: with none
    (unrestricted caller, nothing selected) the answer is a hint, not 8.8k
    names."""
    import document_field_catalog as dfc
    if not types:
        return {"fields": [], "hint": "Choose a document type or category to see its fields."}
    rows = dfc.suggest(cur, types, q, limit)
    return {"fields": rows, "hint": "" if rows else "No matching field for that type yet."}


def top_fields(cur, types: Optional[List[str]], limit: int = 15) -> List[dict]:
    if not types:
        return []
    import document_field_catalog as dfc
    return dfc.top_fields(cur, types, limit)


def attribute_suggestions(cur, types: Optional[List[str]], q: str = "", limit: int = 50) -> List[dict]:
    """Attribute names (DocumentAttributions, admin-assigned metadata) for the
    scope, with usage counts. Small table; cached briefly per scope."""
    key = (tuple(types) if types is not None else None,)
    now = time.time()
    with _lock:
        hit = _attr_cache.get(key)
        rows = list(hit[1]) if hit and now - hit[0] < _ATTR_TTL else None
    if rows is None:
        where = "WHERE d.is_knowledge_document = 0"
        params: list = []
        if types is not None:
            if not types:
                return []
            where += f" AND d.document_type IN ({_in(types)})"
            params = list(types)
        cur.execute(f"""SELECT da.attribution_type, COUNT(DISTINCT da.document_id), COUNT(*),
                               MIN(da.attribution_value), MAX(da.attribution_value)
                        FROM DocumentAttributions da
                        JOIN Documents d ON d.document_id = da.document_id
                        {where}
                        GROUP BY da.attribution_type
                        ORDER BY COUNT(DISTINCT da.document_id) DESC""", *params)
        rows = []
        for name, docs, uses, v1, v2 in cur.fetchall():
            samples = [s for s in (v1, v2) if s not in (None, "")]
            rows.append({"attribute_name": str(name), "documents_with_attribute": int(docs or 0),
                         "usage_count": int(uses or 0),
                         "sample_values": sorted(set(str(s) for s in samples))})
        with _lock:
            _attr_cache[key] = (now, list(rows))
    ql = (q or "").strip().lower()
    if ql:
        rows = [r for r in rows if ql in r["attribute_name"].lower()]
    return rows[:max(1, min(int(limit or 50), 200))]


# ---------------------------------------------------------------- filters
def _condition(column: str, op: str, value: str) -> Tuple[str, str]:
    if op == "contains":
        return f"{column} LIKE ?", f"%{value}%"
    if op == "starts_with":
        return f"{column} LIKE ?", f"{value}%"
    if op == "ends_with":
        return f"{column} LIKE ?", f"%{value}"
    return f"{column} = ?", value


def _matches(actual: str, op: str, value: str) -> bool:
    a, v = (actual or "").lower(), (value or "").lower()
    if op == "contains":
        return v in a
    if op == "starts_with":
        return a.startswith(v)
    if op == "ends_with":
        return a.endswith(v)
    return a == v


def field_matches(cur, filters: List[dict], types: Optional[List[str]]) -> dict:
    """AND-ed per document: a document qualifies when EVERY criterion matches
    on one of its pages. Returns {"documents": {document_id}, "pages":
    {(document_id, page_number): [{name, value}, ...]}}."""
    if not filters:
        return {"documents": set(), "pages": {}}
    parts, params = [], []
    for f in filters:
        cond, val = _condition("df.field_value", f["operator"], f["value"])
        if f["field_path"] == "%":
            parts.append(f"({cond})")
            params.append(val)
        else:
            parts.append(f"(df.field_path = ? AND {cond})")
            params.extend([f["field_path"], val])
    where = f"({' OR '.join(parts)}) AND d.is_knowledge_document = 0"
    if types is not None:
        if not types:
            return {"documents": set(), "pages": {}}
        where += f" AND d.document_type IN ({_in(types)})"
        params.extend(types)
    cur.execute(f"""SELECT dp.document_id, dp.page_number, df.field_name, df.field_path, df.field_value
                    FROM DocumentFields df
                    JOIN DocumentPages dp ON df.page_id = dp.page_id
                    JOIN Documents d ON dp.document_id = d.document_id
                    WHERE {where}""", *params)
    per_doc: Dict[str, set] = {}
    pages: Dict[tuple, list] = {}
    for doc_id, page_no, fname, fpath, fvalue in cur.fetchall():
        hit_idx = [i for i, f in enumerate(filters)
                   if (f["field_path"] == "%" or f["field_path"] == fpath)
                   and _matches(str(fvalue), f["operator"], f["value"])]
        if not hit_idx:
            continue
        per_doc.setdefault(str(doc_id), set()).update(hit_idx)
        pages.setdefault((str(doc_id), int(page_no or 0)), []).append(
            {"name": str(fname or "").replace("_", " ").title(), "path": fpath, "value": fvalue})
    qualifying = {d for d, hits in per_doc.items() if len(hits) == len(filters)}
    return {"documents": qualifying,
            "pages": {k: v for k, v in pages.items() if k[0] in qualifying}}


def attribute_matches(cur, filters: List[dict], types: Optional[List[str]]) -> set:
    """Document ids matching EVERY attribute criterion (AND per document)."""
    if not filters:
        return set()
    parts, params = [], []
    for f in filters:
        cond, val = _condition("da.attribution_value", f["operator"], f["value"])
        parts.append(f"(da.attribution_type = ? AND {cond})")
        params.extend([f["attribute_name"], val])
    where = f"({' OR '.join(parts)}) AND d.is_knowledge_document = 0"
    if types is not None:
        if not types:
            return set()
        where += f" AND d.document_type IN ({_in(types)})"
        params.extend(types)
    cur.execute(f"""SELECT da.document_id, da.attribution_type, da.attribution_value
                    FROM DocumentAttributions da
                    JOIN Documents d ON d.document_id = da.document_id
                    WHERE {where}""", *params)
    per_doc: Dict[str, set] = {}
    for doc_id, atype, avalue in cur.fetchall():
        for i, f in enumerate(filters):
            if str(atype) == f["attribute_name"] and _matches(str(avalue), f["operator"], f["value"]):
                per_doc.setdefault(str(doc_id), set()).add(i)
    return {d for d, hits in per_doc.items() if len(hits) == len(filters)}


# ---------------------------------------------------------------- rendering
def highlight_snippet(text: str, query: str, max_len: int = 400) -> str:
    """HTML-escape FIRST (page text is untrusted), then mark query terms.
    The template renders this with |safe on purpose."""
    text = " ".join(str(text or "").split())
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "…"
    out = html.escape(text)
    terms = [t for t in re.split(r"\s+", (query or "").strip()) if len(t) >= 3]
    for t in sorted(set(terms), key=len, reverse=True):
        out = re.sub(re.escape(html.escape(t)),
                     lambda m: f'<span class="highlight">{m.group(0)}</span>', out, flags=re.I)
    return out


def iter_pages(current, total, left_edge=1, right_edge=1, left_current=2, right_current=2):
    last = 0
    for num in range(1, total + 1):
        if (num <= left_edge or (current - left_current - 1 < num < current + right_current)
                or num > total - right_edge):
            if last + 1 != num:
                yield None
            yield num
            last = num


def _pagination(page: int, per_page: int, total: int) -> dict:
    pages = ceil(total / per_page) if total else 0
    page = max(1, min(page, pages)) if pages else 1
    return {"page": page, "per_page": per_page, "total": total, "pages": pages,
            "has_prev": page > 1, "has_next": page < pages,
            "prev_num": page - 1, "next_num": page + 1,
            "iter_pages": lambda left_edge=1, right_edge=1, left_current=2, right_current=2:
                iter_pages(page, pages, left_edge, right_edge, left_current, right_current)}


def _page_rows(cur, keys: Iterable[tuple], types: Optional[List[str]]) -> List[dict]:
    """Result rows for exact (document_id, page_number) keys — the field-filter
    only path (no text query). Chunked IN on document ids."""
    keys = list(keys)
    wanted = set(keys)
    doc_ids = sorted({k[0] for k in keys})
    out = []
    for i in range(0, len(doc_ids), 200):
        chunk = doc_ids[i:i + 200]
        where = f"dp.document_id IN ({_in(chunk)}) AND d.is_knowledge_document = 0"
        params = list(chunk)
        if types:
            where += f" AND d.document_type IN ({_in(types)})"
            params += list(types)
        cur.execute(f"""SELECT dp.document_id, dp.page_number, d.filename, d.document_type,
                               SUBSTRING(dp.full_text, 1, 400)
                        FROM DocumentPages dp JOIN Documents d ON d.document_id = dp.document_id
                        WHERE {where}""", *params)
        for doc_id, page_no, filename, dtype, text in cur.fetchall():
            if (str(doc_id), int(page_no or 0)) in wanted:
                out.append({"document_id": str(doc_id), "page_number": int(page_no or 0),
                            "filename": filename, "document_type": dtype,
                            "relevance_score": 1.0, "snippet": text or ""})
    return out


def _first_pages(cur, doc_ids: Iterable[str], types: Optional[List[str]]) -> List[dict]:
    """One row per document (its first page) — the attribute-filter only path."""
    ids = sorted(set(doc_ids))
    out = []
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        where = f"d.document_id IN ({_in(chunk)}) AND d.is_knowledge_document = 0"
        params = list(chunk)
        if types:
            where += f" AND d.document_type IN ({_in(types)})"
            params += list(types)
        cur.execute(f"""SELECT d.document_id, d.filename, d.document_type,
                               (SELECT TOP 1 SUBSTRING(p.full_text, 1, 400) FROM DocumentPages p
                                WHERE p.document_id = d.document_id ORDER BY p.page_number)
                        FROM Documents d WHERE {where}""", *params)
        for doc_id, filename, dtype, text in cur.fetchall():
            out.append({"document_id": str(doc_id), "page_number": 1, "filename": filename,
                        "document_type": dtype, "relevance_score": 1.0, "snippet": text or ""})
    return out


def _enrich(cur, results: List[dict]) -> None:
    ids = sorted({r["document_id"] for r in results if r.get("document_id")})
    meta: Dict[str, tuple] = {}
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        cur.execute(f"""SELECT document_id, processed_at, reference_number, customer_id, vendor_id,
                               document_date
                        FROM Documents WHERE document_id IN ({_in(chunk)})""", *chunk)
        for row in cur.fetchall():
            meta[str(row[0])] = tuple(row[1:])
    for r in results:
        m = meta.get(r.get("document_id"))
        if not m:
            continue
        processed, ref, cust, vend, ddate = m
        r["processed_at"] = processed.strftime("%Y-%m-%d %H:%M") if getattr(processed, "strftime", None) else (processed or "")
        r["reference_number"] = ref or ""
        r["customer_id"] = cust or ""
        r["vendor_id"] = vend or ""
        r["document_date"] = ddate if ddate else ""


def _passage_to_result(p: dict) -> dict:
    try:
        page_no = int(str(p.get("page") or "1").split("-")[0])
    except ValueError:
        page_no = 1
    return {"document_id": p.get("document_id"), "filename": p.get("filename") or "(unknown file)",
            "document_type": p.get("document_type") or "", "page_number": page_no,
            "relevance_score": p.get("relevance") if p.get("relevance") is not None else 0.0,
            "snippet": p.get("text") or ""}


def run_search(cur, req: SearchRequest, scope: dict, identity: tuple,
               unified_search: Callable) -> dict:
    """Execute the request. `unified_search` is document_search_unified (or a
    test double) — the one ACL'd search path shared with The Agent / CC.
    Returns {results, total, pagination, answer, error, scope}."""
    uid, role = identity
    types = scope.get("types")           # None = unrestricted, [..] = concrete scope
    out = {"results": [], "total": 0, "pagination": None, "answer": None, "error": None,
           "scope": scope.get("label", "")}
    if scope.get("denied"):
        out["error"] = scope.get("message")
        return out
    if not req.has_criteria:
        return out

    fm = field_matches(cur, req.field_filters, types) if req.field_filters else None
    am = attribute_matches(cur, req.attribute_filters, types) if req.attribute_filters else None
    if (fm is not None and not fm["documents"]) or (am is not None and not am):
        return out                       # a filter matched nothing: honest empty

    results: List[dict]
    if req.query:
        res = unified_search(req.query, max_results=SEARCH_PASSAGES, user_id=uid, user_role=role,
                             document_types=types) or {}
        if res.get("error"):
            out["error"] = str(res.get("error"))[:300]
        results = [_passage_to_result(p) for p in (res.get("passages") or [])]
        if not results and res.get("answer"):
            out["answer"] = str(res.get("answer"))[:500]
        if not results and res.get("text") and not out["answer"] and res.get("count") == 0 \
                and "not accessible" in str(res.get("text", "")):
            out["error"] = res.get("text")
        results = [r for r in results
                   if r["relevance_score"] is None or r["relevance_score"] >= req.min_score]
        if fm is not None:
            # Document-level: a passage stays when its DOCUMENT satisfies every
            # field criterion (the value may sit on another page than the text).
            results = [r for r in results if r["document_id"] in fm["documents"]]
        if am is not None:
            results = [r for r in results if r["document_id"] in am]
    elif fm is not None:
        keys = sorted(fm["pages"].keys())
        if am is not None:
            keys = [k for k in keys if k[0] in am]
        results = _page_rows(cur, keys, types)
        results.sort(key=lambda r: (r["filename"] or "", r["page_number"]))
    else:
        results = _first_pages(cur, am or set(), types)
        results.sort(key=lambda r: r["filename"] or "")

    for r in results:
        if fm is not None:
            hits = fm["pages"].get((r["document_id"], r["page_number"]))
            if not hits:      # matched elsewhere in the document: show those
                hits = [f for (d, _p), fl in sorted(fm["pages"].items()) if d == r["document_id"] for f in fl]
            r["matching_fields"] = hits
        r["snippet"] = highlight_snippet(r.get("snippet", ""), req.query)

    total = len(results)
    pagination = _pagination(req.page, req.max_results, total)
    start = (pagination["page"] - 1) * req.max_results
    page_results = results[start:start + req.max_results]
    _enrich(cur, page_results)
    out.update(results=page_results, total=total, pagination=pagination if total else None)
    return out
