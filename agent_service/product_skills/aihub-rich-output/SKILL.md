---
name: aihub-rich-output
description: Use when an answer would be clearer as a chart, KPI cards, or an
  inline picture — the exact fenced-block syntax The Agent's chat renders
  (aihub-chart, aihub-kpi), inline /api/files images, and the honesty rules
  for where the numbers come from.
---

# Rich output in the chat

The chat renders two fenced blocks and inline images besides markdown.

## Chart

    ```aihub-chart
    {"type": "bar", "title": "Revenue by region",
     "labels": ["East", "West", "North"],
     "series": [{"name": "Revenue", "data": [120500, 98000, 75250]},
     "yLabel": "USD", "format": "currency"}
    ```

- `type`: bar | line | area | pie | doughnut | hbar (horizontal bars).
- `series`: one or more; a pie/doughnut uses the first only.
- `format`: number (default) | currency | percent — axis and tooltips.
- Optional `xLabel`, `stacked: true`.
- Keep it under ~60 points; use a table for the detail rows.

## KPI cards

    ```aihub-kpi
    {"cards": [
      {"label": "Open orders", "value": "1,204", "trend": "+5% vs last week", "direction": "up"},
      {"label": "Late shipments", "value": 37, "trend": "-12%", "direction": "down"}]}
    ```

Two to six headline numbers. `direction`: up | down | flat (colors the trend).

## Inline images

An image link `![chart.png](/api/files/<id>)` renders inline. `run_python`
returns those lines for every image it produced — paste them verbatim,
keeping the download links. Never paste a server path or a data: URL.

## Where the numbers come from (non-negotiable)

- A chart's numbers come from a tool result in THIS conversation or from the
  user's own message. Never chart remembered, estimated, or rounded figures.
- Data from a probe query: call `probe_connection_query` with `chart=…` and
  `chart_title` and paste the returned block VERBATIM — the numbers never
  pass through you. Shape the SQL for the chart: label column first, one row
  per point, `ORDER BY`, under ~60 rows.
- Data from `ask_agent`, a document, or a file the user gave you: you may
  write the block yourself from the values the tool returned — copy them
  exactly, and say where they came from in the text.
- If a block cannot be rendered the chat shows the JSON with a note, so a
  malformed block is visible, never silent — keep the JSON valid (double
  quotes, no trailing commas, one object per fence).
