"""
Command Center — File Renderer
=================================
Generic file output utilities (CSV, JSON, etc.)
"""

import csv
import io
import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def render_to_csv(headers: List[str], rows: List[List[Any]]) -> bytes:
    """Render tabular data to CSV bytes."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def render_to_json(data: Any, indent: int = 2) -> bytes:
    """Render data to formatted JSON bytes."""
    return json.dumps(data, indent=indent, default=str).encode("utf-8")


def render_blocks_to_csv(blocks: List[Dict[str, Any]]) -> bytes:
    """Extract all table blocks and render to CSV."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    for block in blocks:
        if block.get("type") != "table":
            continue
        title = block.get("title", "")
        if title:
            writer.writerow([f"--- {title} ---"])
        headers = block.get("headers", [])
        rows = block.get("rows", [])
        writer.writerow(headers)
        writer.writerows(rows)
        writer.writerow([])  # blank separator

    return buffer.getvalue().encode("utf-8")
