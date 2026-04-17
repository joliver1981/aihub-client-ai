"""
Command Center — Table Renderer
==================================
Produces structured table blocks from data.
"""

from typing import Any, Dict, List, Optional


def create_table_block(
    headers: List[str],
    rows: List[List[Any]],
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a table content block."""
    block = {
        "type": "table",
        "headers": headers,
        "rows": rows,
    }
    if title:
        block["title"] = title
    return block


def dict_list_to_table(data: List[Dict[str, Any]], title: Optional[str] = None) -> Dict[str, Any]:
    """Convert a list of dicts to a table block."""
    if not data:
        return create_table_block([], [], title)

    headers = list(data[0].keys())
    rows = [[str(row.get(h, "")) for h in headers] for row in data]
    return create_table_block(headers, rows, title)
