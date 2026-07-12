"""Deterministic, dictionary-driven display formatting (plan §4/§B, P4).

Replaces V2's LLM-driven formatting check (an extra model call that decided which
columns to format) with a straight lookup: read value_format/units from the data
dictionary (llm_Columns) and format matching result columns in code. Conservative
by design — only unambiguous currency and percentage formats are applied, and
always to a COPY so the stored dataset (and any chart built from it) keeps its
raw numeric values.
"""
import logging

logger = logging.getLogger("nlq_agentic.formatting")

_CURRENCY_FORMATS = {"currency", "money", "dollar", "dollars", "usd"}
_PERCENT_FORMATS = {"percent", "percentage", "pct", "%"}


def load_column_formats(connection_id):
    """Return {lower_column_name: {'format':.., 'units':..}} for the connection.

    Best-effort: returns {} on any error so formatting can never break a request.
    """
    try:
        from DataUtils import query_app_database
        rows = query_app_database(
            """
            SELECT c.column_name, c.value_format, c.units
            FROM llm_Columns c
            INNER JOIN llm_Tables t ON c.table_id = t.id
            WHERE t.connection_id = ?
              AND c.value_format IS NOT NULL
              AND c.value_format <> ''
            """,
            (connection_id,),
        )
    except Exception as e:
        logger.debug(f"[formatting] column format load skipped: {e}")
        return {}

    formats = {}
    for r in rows or []:
        name = (r.get("column_name") or "").strip().lower()
        if not name:
            continue
        # First non-empty definition wins; column names can repeat across tables.
        formats.setdefault(name, {
            "format": (r.get("value_format") or "").strip().lower(),
            "units": (r.get("units") or "").strip(),
        })
    return formats


def _fmt_currency(value, units):
    try:
        num = float(value)
    except (TypeError, ValueError):
        return value
    symbol = "$" if units.upper() in ("", "USD", "$") else ""
    body = f"{num:,.2f}"
    return f"{symbol}{body}" if symbol else f"{body} {units}".strip()


def _fmt_percent(value):
    try:
        num = float(value)
    except (TypeError, ValueError):
        return value
    return f"{num:.1f}%"


def format_dataframe_for_display(df, connection_id):
    """Return a display COPY of df with dictionary-formatted columns.

    Numeric columns whose dictionary value_format is a currency/percentage are
    rendered as formatted strings. Unknown formats and non-matching columns are
    left untouched. Returns df unchanged on any problem.
    """
    if df is None or getattr(df, "empty", True):
        return df
    try:
        import pandas as pd
        formats = load_column_formats(connection_id)
        if not formats:
            return df

        out = df.copy()
        for col in out.columns:
            spec = formats.get(str(col).strip().lower())
            if not spec:
                continue
            fmt = spec["format"]
            if not pd.api.types.is_numeric_dtype(out[col]):
                continue
            if fmt in _CURRENCY_FORMATS:
                units = spec["units"]
                out[col] = out[col].map(lambda v: _fmt_currency(v, units))
            elif fmt in _PERCENT_FORMATS:
                out[col] = out[col].map(_fmt_percent)
        return out
    except Exception as e:
        logger.debug(f"[formatting] display formatting skipped: {e}")
        return df
