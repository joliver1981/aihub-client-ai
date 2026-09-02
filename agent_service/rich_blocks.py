"""
Rich output blocks (pass 2 of the CC-gap plan, 2026-09-02).

The chat renders two fenced-block kinds in addition to markdown:

    ```aihub-chart
    {"type": "bar", "title": "…", "labels": [...],
     "series": [{"name": "…", "data": [...]}], "yLabel": "…", "format": "number"}
    ```
    ```aihub-kpi
    {"cards": [{"label": "…", "value": "…", "trend": "…", "direction": "up"}]}
    ```

and an inline image for any ![name](/api/files/<id>) link (the UI fetches it
with the auth header — no token in a URL). This module builds those blocks
SERVER-SIDE from data a tool already holds, so the numbers in a chart never
pass through the model: probe_connection_query(chart=…) and run_python's
produced .png files hand back ready-made text the model pastes verbatim.

Pure functions, no I/O — the unit pack exercises them directly.
"""

import json
import re
from typing import Any, Optional

CHART_TYPES = ("bar", "line", "area", "pie", "doughnut", "hbar")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
MAX_POINTS = 60
MAX_SERIES = 4

_LINK_RE = re.compile(r"\[⤓\s*(?P<name>.+?)\s*\((?P<size>[^)]*)\)\]\((?P<url>/api/files/[0-9a-fA-F-]+)\)")


def fence(kind: str, spec: dict) -> str:
    """The fenced block text for a chart/kpi spec."""
    body = json.dumps(spec, ensure_ascii=False, default=str)
    return f"```aihub-{kind}\n{body}\n```"


def _num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if s.startswith("$"):
        s = s[1:]
    if s.endswith("%"):
        s = s[:-1]
    try:
        return float(s)
    except ValueError:
        return None


def _is_numeric_column(values: list) -> bool:
    seen = False
    for v in values:
        if v is None or v == "":
            continue
        seen = True
        if _num(v) is None:
            return False
    return seen


def chart_from_rows(columns: list, rows: list, chart_type: str = "bar",
                    title: str = "", y_label: str = "", fmt: str = "") -> tuple:
    """Build a chart spec from tabular rows (dicts keyed by column, or lists).

    Labels = the first NON-numeric column (else the first column); series = up
    to MAX_SERIES numeric columns. Returns (fenced_block, note) — the block is
    None with an explanatory note when the shape can't be charted honestly."""
    ct = str(chart_type or "bar").strip().lower()
    if ct not in CHART_TYPES:
        return None, (f"'{chart_type}' is not a chart type; use one of "
                      + ", ".join(CHART_TYPES) + ".")
    cols = [str(c) for c in (columns or [])]
    if not cols or not rows:
        return None, "No rows to chart."
    table = []
    for r in rows:
        if isinstance(r, dict):
            table.append([r.get(c) for c in cols])
        else:
            table.append(list(r))
    truncated = len(table) > MAX_POINTS
    table = table[:MAX_POINTS]
    numeric = [i for i in range(len(cols)) if _is_numeric_column([t[i] for t in table])]
    label_idx = next((i for i in range(len(cols)) if i not in numeric), None)
    if label_idx is None:
        label_idx = 0
        numeric = [i for i in numeric if i != 0]
    if not numeric:
        return None, ("Nothing numeric to chart — the result has no numeric column "
                      "besides the label column.")
    if ct in ("pie", "doughnut"):
        numeric = numeric[:1]
    else:
        numeric = numeric[:MAX_SERIES]
    labels = ["" if t[label_idx] is None else str(t[label_idx]) for t in table]
    series = [{"name": cols[i], "data": [_num(t[i]) for t in table]} for i in numeric]
    spec: dict[str, Any] = {"type": ct, "labels": labels, "series": series}
    if title:
        spec["title"] = str(title)[:120]
    if y_label:
        spec["yLabel"] = str(y_label)[:60]
    elif len(series) == 1:
        spec["yLabel"] = series[0]["name"]
    if fmt:
        spec["format"] = str(fmt)
    note = f"{len(labels)} point(s), series: " + ", ".join(s["name"] for s in series)
    if truncated:
        note += f" — only the first {MAX_POINTS} rows are charted"
    return fence("chart", spec), note


def kpi_block(cards: list) -> str:
    out = []
    for c in cards or []:
        if not isinstance(c, dict) or not c.get("label"):
            continue
        card = {"label": str(c["label"])[:60], "value": str(c.get("value", ""))[:40]}
        if c.get("trend"):
            card["trend"] = str(c["trend"])[:60]
        d = str(c.get("direction") or c.get("trendDirection") or "").lower()
        if d in ("up", "down", "flat"):
            card["direction"] = d
        out.append(card)
    return fence("kpi", {"cards": out})


def image_lines(links: list) -> list:
    """For each staged-download markdown link that points at an image, the
    ![name](/api/files/<id>) line the chat renders inline."""
    out = []
    for link in links or []:
        m = _LINK_RE.search(str(link))
        if not m:
            continue
        name = m.group("name").strip()
        if not name.lower().endswith(IMAGE_EXTS):
            continue
        safe = name.replace("]", ")")
        out.append(f"![{safe}]({m.group('url')})")
    return out
