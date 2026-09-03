"""Per-type catalog of the fields extracted from documents — what the
document-search page's field type-ahead reads (james 2026-09-03, rebuild items
3 and 4).

WHY: DocumentFields holds every LLM-extracted field of every page (132,230
rows / 8,791 distinct names on the 397-document dev store; 5,276 of those
names occur in exactly ONE document — the extractor names fields per document,
so they drift). The legacy page GROUP BY'd that whole table on every render and
put all 8.8k names in one <select>. This catalog answers "which fields does
type X carry, and how common is each" from a small indexed table, is kept
current incrementally at ingest (LLMDocumentEngine._store_in_sql_db calls
record_document), and can be rebuilt wholesale (run_document_field_catalog_
backfill.py, or the page's admin "Rebuild" action).

CONTRACT
  suggest(cur, document_types, q, limit) -> [{path, name, display_name,
      doc_count, in_schema}], schema-declared fields first (schemas/<type>*.yml
      is the curated vocabulary), then by how many documents carry the field.
      Names seen in fewer than MIN_DOCS documents are dropped unless `q`
      matches them exactly — that is the singleton noise.
  FALLBACK: table missing (migration 021 not applied and the app login cannot
      CREATE TABLE) or no rows yet for the requested types -> the same answer
      is computed live from DocumentFields for THOSE types only and cached —
      bounded, never the whole store — with a log line pointing at the
      backfill.
  Every read is cached briefly in-process (CACHE_TTL); record_document and
      rebuild invalidate the affected types.
"""
import logging
import os
import re
import threading
import time
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

TABLE = "DocumentFieldCatalog"
SCHEMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schemas")
MIN_DOCS = int(os.getenv("DOC_FIELD_CATALOG_MIN_DOCS", "2"))
CACHE_TTL = int(os.getenv("DOC_FIELD_CATALOG_CACHE_TTL", "60"))
FALLBACK_TTL = int(os.getenv("DOC_FIELD_CATALOG_FALLBACK_TTL", "300"))
CHUNK = 400            # IN-list size; stays far under SQL Server's 2100 params

# One statement per execute — CREATE INDEX on a table created earlier in the
# same batch is not reliably resolved, and older servers reject a 1100-byte
# (type + NVARCHAR(500)) index key, hence the persisted SHA1 of the path.
DDL = [
    f"""IF OBJECT_ID('dbo.{TABLE}', 'U') IS NULL
CREATE TABLE dbo.{TABLE} (
    catalog_id      INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    TenantId        INT NULL,
    document_type   VARCHAR(100)  NOT NULL,
    field_name      NVARCHAR(255) NOT NULL,
    field_path      NVARCHAR(500) NOT NULL,
    field_path_hash AS CONVERT(BINARY(20), HASHBYTES('SHA1', field_path)) PERSISTED,
    doc_count       INT NOT NULL CONSTRAINT DF_{TABLE}_doc_count DEFAULT 0,
    row_count       INT NOT NULL CONSTRAINT DF_{TABLE}_row_count DEFAULT 0,
    first_seen      DATETIME NOT NULL CONSTRAINT DF_{TABLE}_first_seen DEFAULT GETUTCDATE(),
    last_seen       DATETIME NOT NULL CONSTRAINT DF_{TABLE}_last_seen DEFAULT GETUTCDATE()
)""",
    f"""IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_{TABLE}_type_path')
CREATE UNIQUE INDEX UX_{TABLE}_type_path ON dbo.{TABLE} (document_type, field_path_hash)""",
    f"""IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_{TABLE}_type_count')
CREATE INDEX IX_{TABLE}_type_count ON dbo.{TABLE} (document_type, doc_count DESC)""",
]

_lock = threading.Lock()
_cache: Dict[tuple, Tuple[float, list]] = {}       # suggest results
_rows_cache: Dict[tuple, Tuple[float, list, str]] = {}   # per-types raw rows (+ source)
_schema_cache: Dict[str, Tuple[tuple, set]] = {}
_table_state = {"exists": None, "checked": 0.0}
_fallback_logged: set = set()


# ---------------------------------------------------------------- schema
def _schema_signature() -> tuple:
    try:
        return tuple(sorted((f, os.path.getmtime(os.path.join(SCHEMA_DIR, f)))
                            for f in os.listdir(SCHEMA_DIR)
                            if f.endswith((".yml", ".yaml"))))
    except OSError:
        return ()


def schema_fields(document_type: str) -> set:
    """Field paths the curated schema (schemas/*.yml with this document_type)
    declares — the vocabulary an admin reviewed, ranked first in suggestions."""
    sig = _schema_signature()
    with _lock:
        hit = _schema_cache.get(document_type)
        if hit and hit[0] == sig:
            return set(hit[1])
    found: set = set()
    try:
        import yaml
        for fn, _mt in sig:
            try:
                with open(os.path.join(SCHEMA_DIR, fn), "r", encoding="utf-8") as fh:
                    schema = yaml.safe_load(fh) or {}
            except Exception:
                continue
            if str(schema.get("document_type") or "") != str(document_type):
                continue
            fields = schema.get("fields") or {}
            if isinstance(fields, dict):
                found.update(str(k) for k in fields.keys())
    except Exception as e:                       # yaml missing, unreadable dir
        log.debug(f"schema_fields({document_type}): {e}")
    with _lock:
        _schema_cache[document_type] = (sig, set(found))
    return found


# ---------------------------------------------------------------- naming
_INDEX_RE = re.compile(r"\[\d+\]")


def display_name(path: str, name: str = "") -> str:
    """'financial_terms.base_rent_amount' -> 'Financial Terms › Base Rent Amount'."""
    path = _INDEX_RE.sub("", str(path or name or ""))
    parts = [p for p in path.split(".") if p]
    leaf = (parts[-1] if parts else str(name or "")).replace("_", " ").strip().title()
    groups = [p.replace("_", " ").strip().title() for p in parts[:-1]]
    return " › ".join(groups + [leaf]) if groups else leaf


# ---------------------------------------------------------------- table
def table_exists(cur, ttl: float = 60.0) -> bool:
    now = time.time()
    with _lock:
        if _table_state["exists"] is not None and now - _table_state["checked"] < ttl:
            return bool(_table_state["exists"])
    try:
        cur.execute(f"SELECT OBJECT_ID('dbo.{TABLE}', 'U')")
        row = cur.fetchone()
        exists = bool(row and row[0])
    except Exception as e:
        log.debug(f"table_exists: {e}")
        exists = False
    with _lock:
        _table_state.update(exists=exists, checked=now)
    return exists


def ensure_table(cur) -> bool:
    """Create the table + indexes if missing (idempotent). False when the login
    cannot (installs: apply migrations/021_document_field_catalog.sql by hand)."""
    try:
        for stmt in DDL:
            cur.execute(stmt)
    except Exception as e:
        log.warning(f"{TABLE}: could not create ({e}); apply migration 021 with a "
                    f"login that has CREATE TABLE")
    with _lock:
        _table_state.update(exists=None)
    return table_exists(cur, ttl=0)


# ---------------------------------------------------------------- reads
def _in(values: Sequence) -> str:
    return ",".join("?" * len(values))


def _observed(cur, document_types: Optional[Sequence[str]] = None,
              field_paths: Optional[Sequence[str]] = None) -> List[tuple]:
    """(document_type, field_name, field_path, doc_count, row_count) straight
    from DocumentFields — the source of truth, bounded by the filters given."""
    where = ["d.is_knowledge_document = 0"]
    params: list = []
    if document_types:
        where.append(f"d.document_type IN ({_in(document_types)})")
        params += list(document_types)
    if field_paths:
        where.append(f"COALESCE(f.field_path, f.field_name) IN ({_in(field_paths)})")
        params += list(field_paths)
    cur.execute(
        f"""SELECT d.document_type, f.field_name, COALESCE(f.field_path, f.field_name),
                   COUNT(DISTINCT d.document_id), COUNT(*)
            FROM DocumentFields f
            JOIN DocumentPages p ON p.page_id = f.page_id
            JOIN Documents d ON d.document_id = p.document_id
            WHERE {' AND '.join(where)}
            GROUP BY d.document_type, f.field_name, COALESCE(f.field_path, f.field_name)""",
        *params)
    return [tuple(r) for r in cur.fetchall()]


def _catalog_rows(cur, types: Sequence[str]) -> Tuple[List[tuple], str]:
    """[(field_name, field_path, doc_count_total)] for the types, from the
    table when it has them, else computed live for those types (fallback)."""
    key = tuple(types)
    now = time.time()
    with _lock:
        hit = _rows_cache.get(key)
        if hit and now - hit[0] < (CACHE_TTL if hit[2] == "catalog" else FALLBACK_TTL):
            return list(hit[1]), hit[2]
    rows: List[tuple] = []
    source = "catalog"
    if table_exists(cur):
        cur.execute(
            f"""SELECT field_name, field_path, SUM(doc_count)
                FROM dbo.{TABLE} WHERE document_type IN ({_in(types)})
                GROUP BY field_name, field_path""", *types)
        rows = [(r[0], r[1], int(r[2] or 0)) for r in cur.fetchall()]
    if not rows:
        source = "fallback"
        agg: Dict[Tuple[str, str], int] = {}
        for _t, fname, fpath, dcount, _rc in _observed(cur, types):
            agg[(fname, fpath)] = agg.get((fname, fpath), 0) + int(dcount or 0)
        rows = [(n, p, c) for (n, p), c in agg.items()]
        if rows:
            missing = [t for t in types if t not in _fallback_logged]
            if missing:
                _fallback_logged.update(missing)
                log.warning(f"{TABLE} has no rows for {missing}: answering from "
                            f"DocumentFields live (cached {FALLBACK_TTL}s). Run "
                            f"run_document_field_catalog_backfill.py to build the catalog.")
    with _lock:
        _rows_cache[key] = (now, list(rows), source)
    return rows, source


def suggest(cur, document_types: Iterable[str], q: str = "", limit: int = 50,
            min_docs: Optional[int] = None) -> List[dict]:
    """Type-ahead over the fields of the given types. See the module contract."""
    types = sorted({str(t).strip() for t in (document_types or []) if str(t or "").strip()})
    if not types:
        return []
    q = (q or "").strip().lower()
    limit = max(1, min(int(limit or 50), 200))
    min_docs = MIN_DOCS if min_docs is None else int(min_docs)
    key = (tuple(types), q, limit, min_docs)
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return [dict(r) for r in hit[1]]

    rows, _source = _catalog_rows(cur, types)
    declared: set = set()
    for t in types:
        declared |= schema_fields(t)

    out = []
    for fname, fpath, total in rows:
        fpath = str(fpath or fname or "")
        fname = str(fname or fpath.split(".")[-1])
        disp = display_name(fpath, fname)
        exact = q and (q == fpath.lower() or q == fname.lower())
        if q and not exact and q not in fpath.lower() and q not in fname.lower() \
                and q not in disp.lower():
            continue
        if total < min_docs and not exact:
            continue          # singleton noise — one document's private field name
        out.append({"path": fpath, "name": fname, "display_name": disp,
                    "doc_count": int(total), "in_schema": fpath in declared})
    out.sort(key=lambda r: (0 if r["in_schema"] else 1, -r["doc_count"], r["path"]))
    out = out[:limit]
    with _lock:
        _cache[key] = (now, [dict(r) for r in out])
    return out


def top_fields(cur, document_types: Iterable[str], limit: int = 15) -> List[dict]:
    return suggest(cur, document_types, "", limit)


def stats(cur) -> dict:
    if not table_exists(cur):
        return {"table": False, "rows": 0, "types": 0, "last_seen": None}
    cur.execute(f"SELECT COUNT(*), COUNT(DISTINCT document_type), MAX(last_seen) FROM dbo.{TABLE}")
    r = cur.fetchone()
    return {"table": True, "rows": int(r[0] or 0), "types": int(r[1] or 0),
            "last_seen": r[2].isoformat() if r and r[2] else None}


# ---------------------------------------------------------------- writes
def invalidate(document_type: Optional[str] = None) -> None:
    with _lock:
        if document_type is None:
            _cache.clear()
            _rows_cache.clear()
            return
        for k in [k for k in _cache if document_type in k[0]]:
            _cache.pop(k, None)
        for k in [k for k in _rows_cache if document_type in k]:
            _rows_cache.pop(k, None)
        _fallback_logged.discard(document_type)


def _upsert(cur, rows: Iterable[tuple]) -> int:
    """rows: (document_type, field_name, field_path, doc_count, row_count).
    UPDATE by (type, path); INSERT when new; the unique index settles a race."""
    n = 0
    for dtype, fname, fpath, dcount, rcount in rows:
        cur.execute(
            f"""UPDATE dbo.{TABLE} SET doc_count = ?, row_count = ?, field_name = ?,
                       last_seen = GETUTCDATE()
                WHERE document_type = ? AND field_path = ?""",
            int(dcount or 0), int(rcount or 0), str(fname or "")[:255], str(dtype), str(fpath)[:500])
        if not getattr(cur, "rowcount", 0):
            try:
                cur.execute(
                    f"""INSERT INTO dbo.{TABLE} (document_type, field_name, field_path,
                                                 doc_count, row_count)
                        VALUES (?, ?, ?, ?, ?)""",
                    str(dtype), str(fname or "")[:255], str(fpath)[:500],
                    int(dcount or 0), int(rcount or 0))
            except Exception:
                # lost a race with a concurrent ingest of the same type: the row
                # exists now — the UPDATE below wins either way
                cur.execute(
                    f"""UPDATE dbo.{TABLE} SET doc_count = ?, row_count = ?, last_seen = GETUTCDATE()
                        WHERE document_type = ? AND field_path = ?""",
                    int(dcount or 0), int(rcount or 0), str(dtype), str(fpath)[:500])
        n += 1
    return n


def record_document(cur, document_id: str, document_type: Optional[str] = None) -> int:
    """Fold ONE just-stored document into the catalog: exact doc/row counts
    recomputed for the (type, path) pairs it carries — idempotent, so a
    re-ingest of the same document never double-counts. Returns rows touched;
    0 (and a log line) when the table is not there. Caller commits."""
    if not document_type:
        cur.execute("SELECT document_type FROM Documents WHERE document_id = ?", document_id)
        r = cur.fetchone()
        document_type = r[0] if r else None
    if not document_type:
        return 0
    if not table_exists(cur) and not ensure_table(cur):
        log.info(f"{TABLE} absent — {document_id} not cataloged")
        return 0
    cur.execute(
        """SELECT DISTINCT COALESCE(f.field_path, f.field_name)
           FROM DocumentFields f JOIN DocumentPages p ON p.page_id = f.page_id
           WHERE p.document_id = ?""", document_id)
    paths = [str(r[0]) for r in cur.fetchall() if r and r[0]]
    touched = 0
    for i in range(0, len(paths), CHUNK):
        chunk = paths[i:i + CHUNK]
        touched += _upsert(cur, _observed(cur, [document_type], chunk))
    invalidate(str(document_type))
    return touched


def rebuild(cur, document_types: Optional[Iterable[str]] = None) -> Dict[str, int]:
    """Recompute the catalog for the given types (or ALL types) from
    DocumentFields. Returns {document_type: rows}. Caller commits."""
    types = sorted({str(t).strip() for t in (document_types or []) if str(t or "").strip()})
    if not ensure_table(cur):
        raise RuntimeError(f"{TABLE} is missing and could not be created — apply "
                           f"migrations/021_document_field_catalog.sql")
    rows = _observed(cur, types or None)
    if types:
        cur.execute(f"DELETE FROM dbo.{TABLE} WHERE document_type IN ({_in(types)})", *types)
    else:
        cur.execute(f"DELETE FROM dbo.{TABLE}")
    counts: Dict[str, int] = {}
    batch = [(str(t), str(n or "")[:255], str(p)[:500], int(c or 0), int(rc or 0))
             for t, n, p, c, rc in rows]
    for i in range(0, len(batch), 500):
        cur.executemany(
            f"""INSERT INTO dbo.{TABLE} (document_type, field_name, field_path, doc_count, row_count)
                VALUES (?, ?, ?, ?, ?)""", batch[i:i + 500])
    for t, *_ in batch:
        counts[t] = counts.get(t, 0) + 1
    invalidate(None)
    return counts
