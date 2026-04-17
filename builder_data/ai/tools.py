"""
Builder Data — Agent Tools
==============================
LangChain tool definitions that the data agent can invoke
during the converse node for interactive data exploration.
"""

import json
import logging
from typing import List, Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# These get set by the graph initialization
_connection_bridge = None
_dataframe_store = None


def init_tools(connection_bridge, dataframe_store=None):
    global _connection_bridge, _dataframe_store
    _connection_bridge = connection_bridge
    _dataframe_store = dataframe_store


@tool
async def list_connections() -> str:
    """List all available database connections configured in AI Hub."""
    if _connection_bridge is None:
        return "Error: Connection bridge not initialized"
    try:
        connections = await _connection_bridge.list_connections()
        if not connections:
            return "No connections available."
        lines = []
        for conn in connections:
            conn_id = conn.get("id", conn.get("connection_id", "?"))
            name = conn.get("name", conn.get("connection_name", "Unknown"))
            conn_type = conn.get("type", conn.get("database_type", ""))
            lines.append(f"- **{name}** (ID: {conn_id}, Type: {conn_type})")
        return "Available connections:\n" + "\n".join(lines)
    except Exception as e:
        return f"Error listing connections: {e}"


@tool
async def get_table_schema(connection_id: int) -> str:
    """Get the table and column schema metadata for a database connection."""
    if _connection_bridge is None:
        return "Error: Connection bridge not initialized"
    try:
        schema = await _connection_bridge.get_schema_metadata(connection_id)
        if not schema:
            return f"No schema metadata available for connection {connection_id}"
        return f"Schema for connection {connection_id}:\n```yaml\n{schema}\n```"
    except Exception as e:
        return f"Error getting schema: {e}"


@tool
async def preview_data(connection_id: int, query: str, max_rows: int = 10) -> str:
    """Execute a SQL query and return the first N rows as a preview table."""
    if _connection_bridge is None:
        return "Error: Connection bridge not initialized"
    try:
        conn_str, conn_id, db_type = await _connection_bridge.get_connection_string(connection_id)

        # Inject row limit
        safe_query = query.strip()
        if safe_query.upper().startswith("SELECT") and "TOP " not in safe_query.upper()[:30]:
            safe_query = safe_query.replace("SELECT ", f"SELECT TOP {max_rows} ", 1)

        df, error = _connection_bridge.execute_query_sync(safe_query, conn_str)
        if error:
            return f"Query error: {error}"

        return f"Query returned {len(df)} rows, {len(df.columns)} columns:\n\n{df.to_markdown(index=False)}"
    except Exception as e:
        return f"Error executing query: {e}"


@tool
async def run_quality_profile(connection_id: int, query: str) -> str:
    """
    Run a data quality profile on a query result.
    Returns column-level statistics including null counts, unique values, and data types.
    """
    if _connection_bridge is None:
        return "Error: Connection bridge not initialized"
    try:
        from quality.comparator import DataComparator
        from quality.report import QualityReport

        conn_str, conn_id, db_type = await _connection_bridge.get_connection_string(connection_id)
        df, error = _connection_bridge.execute_query_sync(query, conn_str)
        if error:
            return f"Query error: {error}"

        comparator = DataComparator()
        profile = comparator.profile(df)

        report_gen = QualityReport()
        report = report_gen.generate(df, profile=profile)

        return report.markdown_summary
    except Exception as e:
        return f"Error profiling data: {e}"


@tool
async def compare_tables(
    conn_a_id: int,
    query_a: str,
    conn_b_id: int,
    query_b: str,
    key_columns: List[str],
) -> str:
    """
    Compare two data sources by executing queries against different connections.
    Returns a summary of matches, mismatches, and rows only in one source.
    """
    if _connection_bridge is None:
        return "Error: Connection bridge not initialized"
    try:
        from quality.comparator import DataComparator

        # Load both sources
        conn_str_a, _, _ = await _connection_bridge.get_connection_string(conn_a_id)
        df_a, err_a = _connection_bridge.execute_query_sync(query_a, conn_str_a)
        if err_a:
            return f"Error loading source A: {err_a}"

        conn_str_b, _, _ = await _connection_bridge.get_connection_string(conn_b_id)
        df_b, err_b = _connection_bridge.execute_query_sync(query_b, conn_str_b)
        if err_b:
            return f"Error loading source B: {err_b}"

        comparator = DataComparator()
        result = comparator.compare(df_a, df_b, key_columns=key_columns)

        summary = result.summary
        lines = [
            f"## Comparison Results",
            f"- Source A rows: {summary['total_a']}",
            f"- Source B rows: {summary['total_b']}",
            f"- Matched: {summary['matched']}",
            f"- Mismatched: {summary['mismatched']}",
            f"- Only in A: {summary['only_in_a']}",
            f"- Only in B: {summary['only_in_b']}",
            f"- Quality Score: {result.quality_score:.1%}",
        ]

        if result.column_stats:
            lines.append(f"\n### Column Match Rates")
            for col, stats in result.column_stats.items():
                lines.append(f"- `{col}`: {stats['match_rate']:.1%} match")

        return "\n".join(lines)
    except Exception as e:
        return f"Error comparing data: {e}"


@tool
async def find_duplicates(
    connection_id: int,
    query: str,
    key_columns: List[str],
    strategy: str = "exact",
) -> str:
    """
    Find duplicate rows in a dataset based on key columns.
    Strategy can be 'exact' or 'fuzzy'.
    """
    if _connection_bridge is None:
        return "Error: Connection bridge not initialized"
    try:
        from quality.deduplicator import Deduplicator, DeduplicationStrategy

        conn_str, _, _ = await _connection_bridge.get_connection_string(connection_id)
        df, error = _connection_bridge.execute_query_sync(query, conn_str)
        if error:
            return f"Query error: {error}"

        deduplicator = Deduplicator()
        result = deduplicator.deduplicate(
            df,
            key_columns=key_columns,
            strategy=DeduplicationStrategy(strategy),
        )

        lines = [
            f"## Deduplication Results",
            f"- Original rows: {result.original_count}",
            f"- After dedup: {result.deduplicated_count}",
            f"- Duplicates found: {result.duplicates_found}",
            f"- Uniqueness score: {result.quality_score:.1%}",
        ]

        if result.duplicate_groups is not None and len(result.duplicate_groups) > 0:
            sample = result.duplicate_groups.head(10)
            lines.append(f"\n### Sample Duplicates (first 10)")
            lines.append(sample.to_markdown(index=False))

        return "\n".join(lines)
    except Exception as e:
        return f"Error finding duplicates: {e}"


def get_data_tools() -> list:
    """Return the list of tools for the data agent."""
    return [
        list_connections,
        get_table_schema,
        preview_data,
        run_quality_profile,
        compare_tables,
        find_duplicates,
    ]
