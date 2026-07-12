"""Row-safety caps for legacy SQL reads (OOM guard).

The legacy NLQ engine and the GeneralAgent raw-SQL tool materialize entire
result sets in memory (pd.read_sql_query / cursor.fetchall) with no LIMIT ever
injected, so a "list everything" question can pull an unbounded table into RAM.
These helpers put a hard, env-tunable ceiling on how many rows a single read
may materialize. The agentic NLQ engine does not use this module — it injects
its own cap via sql_gate (NLQ_AGENTIC_SQL_ROW_CAP).

Deliberately dependency-light (pandas + config only) so it stays unit-testable
without the AppUtils import-time DB connections.
"""
import logging

import pandas as pd

logger = logging.getLogger("sql_row_cap")

_DEFAULT_CAP = 1_000_000
_CHUNK_ROWS = 50_000


def _configured_cap():
    try:
        import config as _cfg
        return int(getattr(_cfg, 'SQL_QUERY_ROW_SAFETY_CAP', _DEFAULT_CAP))
    except Exception:
        return _DEFAULT_CAP


def read_sql_query_row_capped(query, conn, cap=None):
    """pd.read_sql_query with a hard row ceiling.

    Reads in chunks and stops once `cap` rows are materialized. Returns
    (DataFrame, capped) where capped is True when the result was cut at the
    ceiling. cap=None uses config.SQL_QUERY_ROW_SAFETY_CAP; cap<=0 disables.
    """
    if cap is None:
        cap = _configured_cap()
    if cap <= 0:
        return pd.read_sql_query(query, conn), False

    frames = []
    total = 0
    capped = False
    it = iter(pd.read_sql_query(query, conn, chunksize=min(cap, _CHUNK_ROWS)))
    for chunk in it:
        remaining = cap - total
        if len(chunk) > remaining:
            frames.append(chunk.iloc[:remaining])
            total = cap
            capped = True
            break
        frames.append(chunk)
        total += len(chunk)
        if total >= cap:
            # Exactly at the ceiling — only capped if the source had more rows.
            try:
                capped = len(next(it)) > 0
            except StopIteration:
                pass
            break

    if not frames:
        # Zero-row result: the chunked iterator yields nothing, losing column
        # names. Re-run plain (cheap: the result set is empty) so callers keep
        # the empty-DataFrame-with-columns shape they always got.
        return pd.read_sql_query(query, conn), False

    df = frames[0] if len(frames) == 1 else pd.concat(frames, ignore_index=True)
    if capped:
        logger.warning(f"[SQL_ROW_CAP] Result truncated at {cap} rows "
                       f"(SQL_QUERY_ROW_SAFETY_CAP): {str(query)[:150]}")
        try:
            df.attrs['row_cap_applied'] = cap
        except Exception:
            pass
    return df, capped


def fetch_rows_capped(cursor, cap=None, batch_size=_CHUNK_ROWS):
    """cursor.fetchall() with a hard row ceiling.

    Returns (rows, capped). rows is a list of DBAPI row objects, at most `cap`
    long. cap=None uses config.SQL_QUERY_ROW_SAFETY_CAP; cap<=0 disables.
    """
    if cap is None:
        cap = _configured_cap()
    if cap <= 0:
        return cursor.fetchall(), False

    rows = []
    capped = False
    while True:
        batch = cursor.fetchmany(batch_size)
        if not batch:
            break
        remaining = cap - len(rows)
        if len(batch) >= remaining:
            rows.extend(batch[:remaining])
            # Only truly capped if the source had more to give.
            capped = len(batch) > remaining or bool(cursor.fetchmany(1))
            break
        rows.extend(batch)

    if capped:
        logger.warning(f"[SQL_ROW_CAP] Fetch truncated at {cap} rows "
                       f"(SQL_QUERY_ROW_SAFETY_CAP)")
    return rows, capped
