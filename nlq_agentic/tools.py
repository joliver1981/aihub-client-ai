"""Tool schemas + handlers for the agentic NLQ loop (plan §4).

P3 tools: get_table_details, run_sql, respond. (create_chart and ask_user land
in P4.) run_sql routes every query through sql_gate (the P2 security boundary);
SQL errors are returned to the model as tool output so it self-repairs — there
is no separate correction-LLM chain like the legacy engine has.

Shared read-only utilities (DataUtils, AppUtils, sql_gate) are late-imported
inside handlers to keep package import light and circular-safe, matching the
factory's pattern.
"""
import json
import logging
import time

logger = logging.getLogger("nlq_agentic.tools")

TERMINAL_TOOLS = {"respond", "ask_user"}

# Char budget for a get_table_details reply so a wide schema can't blow the
# context window; the model can call it again for more tables.
_SCHEMA_CHAR_BUDGET = 14000
# Rows previewed back to the model from a run_sql result.
_PREVIEW_ROWS = 20


def build_tool_schemas(strict=True):
    """OpenAI function-tool schemas. strict=True adds additionalProperties:false
    and lists every property as required (P0 verified strict mode is accepted)."""

    def fn(name, description, properties, required):
        f = {"name": name, "description": description,
             "parameters": {"type": "object", "properties": properties, "required": required}}
        if strict:
            f["strict"] = True
            f["parameters"]["additionalProperties"] = False
        return {"type": "function", "function": f}

    return [
        fn(
            "get_table_details",
            "Get the full schema for specific tables: columns with data types, semantic types, "
            "example values, synonyms, business rules, required filters, and any calculated metrics "
            "(virtual columns with SQL formulas). Call this before writing SQL against a table.",
            {"tables": {"type": "array", "items": {"type": "string"},
                        "description": "Exact table names to fetch details for."}},
            ["tables"],
        ),
        fn(
            "run_sql",
            "Execute a single read-only SQL SELECT against the connected database and return a "
            "preview of the results. Only SELECT is permitted; anything else is rejected. If the "
            "query errors, read the message and correct it.",
            {"query": {"type": "string", "description": "One SQL SELECT statement for the target database dialect."}},
            ["query"],
        ),
        fn(
            "respond",
            "Provide the final answer to the user and end the turn. Use answer_kind='table' to show "
            "the rows of a dataset you queried (give its dataset_ref); use 'text' for a written answer, "
            "a scalar value, or to ask the user for clarification.",
            {
                "answer_kind": {"type": "string", "enum": ["text", "table"],
                                "description": "'table' to display a dataset's rows, otherwise 'text'."},
                "text": {"type": "string",
                         "description": "The written answer in plain business language. For a table answer, a short caption."},
                "dataset_ref": {"type": ["string", "null"],
                                "description": "For answer_kind='table', the dataset_ref (e.g. 'dataset_1') to display. Null for text."},
            },
            ["answer_kind", "text", "dataset_ref"],
        ),
    ]


class ToolContext:
    """Everything the handlers need, without reaching into the engine."""

    def __init__(self, state, row_cap):
        self.state = state
        self.row_cap = row_cap


def _handle_get_table_details(ctx, args):
    from DataUtils import (
        get_enhanced_full_schema_with_column_details_as_yaml,
        get_column_descriptions_with_table_descriptions_as_yaml,
    )
    tables = args.get("tables") or []
    if isinstance(tables, str):
        tables = [tables]
    tables = [str(t).strip() for t in tables if str(t).strip()]
    if not tables:
        return "No tables specified. Pass the exact table names you need details for."

    try:
        yaml = get_enhanced_full_schema_with_column_details_as_yaml(tables, ctx.state.connection_id)
    except Exception as e:
        logger.warning(f"[tools] enhanced schema failed ({e}); trying basic")
        yaml = ""
    if not yaml:
        try:
            yaml = get_column_descriptions_with_table_descriptions_as_yaml(tables, ctx.state.connection_id)
        except Exception as e:
            return f"Could not load schema for {tables}: {e}"
    if not yaml:
        return (f"No documented schema found for: {', '.join(tables)}. "
                f"Check the table names against the catalog in the system prompt.")

    if len(yaml) > _SCHEMA_CHAR_BUDGET:
        yaml = yaml[:_SCHEMA_CHAR_BUDGET] + "\n... [truncated — request fewer tables for full detail]"
    return yaml


def _handle_run_sql(ctx, args):
    from AppUtils import execute_sql_query_v2
    from sql_gate import gate_sql

    query = (args.get("query") or "").strip()
    if not query:
        return "No query provided."

    gate = gate_sql(query, database_type=ctx.state.database_type, row_cap=ctx.row_cap)
    if not gate.ok:
        return (f"QUERY REJECTED: {gate.reason}. Only a single read-only SELECT is permitted — "
                f"no INSERT/UPDATE/DELETE/DDL/EXEC and no multiple statements. Revise and try again.")

    df, error = execute_sql_query_v2(gate.sql, ctx.state.connection_string)
    if df is None:
        ctx.state.was_last_query_successful = False
        return (f"SQL ERROR: {error}\nThe query did not execute. Use get_table_details to confirm "
                f"column and table names, then correct the SQL and retry.")

    ref = ctx.state.add_dataset(df, gate.sql)
    ctx.state.current_query = gate.sql
    ctx.state.was_last_query_successful = True
    ctx.state.last_query_row_count = int(len(df))

    dtypes = ", ".join(f"{c}:{str(df[c].dtype)}" for c in df.columns[:40])
    preview = df.head(_PREVIEW_ROWS).to_string(index=False)
    note = ""
    if gate.cap_applied:
        note = f"\n(Note: a row cap of {ctx.row_cap} was applied to bound the result size.)"
    truncated = ""
    if len(df) > _PREVIEW_ROWS:
        truncated = f"\n(Showing first {_PREVIEW_ROWS} of {len(df)} rows.)"
    return (f"OK — stored as {ref}: {len(df)} row(s), {len(df.columns)} column(s).\n"
            f"Columns: {dtypes}\n\n{preview}{truncated}{note}\n\n"
            f"If this answers the question, call respond (answer_kind='table', dataset_ref='{ref}' "
            f"to show the rows, or 'text' to state a single value).")


HANDLERS = {
    "get_table_details": _handle_get_table_details,
    "run_sql": _handle_run_sql,
}


def execute_tool(ctx, name, args, trace=None):
    """Dispatch a non-terminal tool call; return (result_text). Records timing."""
    handler = HANDLERS.get(name)
    t0 = time.time()
    if handler is None:
        if trace is not None:
            trace.record_tool(name, (time.time() - t0) * 1000.0, ok=False, error="unknown tool")
        return f"Unknown tool '{name}'."
    try:
        result = handler(ctx, args)
        ok, err = True, None
    except Exception as e:
        logger.error(f"[tools] handler {name} raised: {e}")
        result = f"Tool '{name}' failed: {e}"
        ok, err = False, e
    if trace is not None:
        digest = json.dumps(args, default=str)[:200]
        trace.record_tool(name, (time.time() - t0) * 1000.0, ok=ok, args_digest=digest, error=err)
    return result
