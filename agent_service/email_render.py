"""
Deterministic HTML rendering for The Agent's outbound email (Phase 1 of
docs/the-agent-html-email-views-plan.md).

The SERVICE owns presentation, not the model. `draft_email_reply` keeps taking a
plain/markdown-ish body and this module converts it. Rationale: a model asked to
emit raw HTML produces broken markup and inconsistent styling, and leaves no
clean plain-text alternative to fall back on. The model writes prose; the
platform decides how it looks.

Email HTML is not web HTML. The rules enforced here, and why:
  * INLINE STYLES ONLY — no <style> block, no classes. Gmail strips <style> in
    several contexts; a class-based design silently loses all styling there.
  * TABLES FOR LAYOUT — no flex, grid, float or position. Outlook renders with
    the Word engine and ignores modern layout entirely.
  * EXPLICIT background AND color on every visible cell, so the dark-mode
    auto-inversion Gmail/Outlook apply can't invent an unreadable pairing.
  * A PLAIN-TEXT ALTERNATIVE IS ALWAYS SENT ALONGSIDE (the caller passes the
    original markdown-ish body as `body`). Never HTML-only.

On regex: the project directive is never to interpret NATURAL LANGUAGE with
regex/keywords. Nothing here does. This is markup-FORMAT parsing (headings,
lists, pipe tables) plus a fail-closed URL-scheme guard — the two cases the
directive explicitly allows.
"""

import html as _html
import os
import re
from typing import Optional

# ---------------------------------------------------------------------------
# Palette — a LIGHT theme derived from the product's dark UI (index.html :9-13):
# the UI's page ink (#0B0F14) becomes our text, and its accent (#4D9FFF) is
# darkened to #2563C9 (the same value the UI's avatar gradient ends on) so it
# stays readable on white.
# ---------------------------------------------------------------------------
PAGE = "#F4F6F8"
CARD = "#FFFFFF"
INK = "#0B0F14"
MUTED = "#5B6875"
LINE = "#E2E7EC"
ACCENT = "#2563C9"
CODE_BG = "#F1F3F5"

FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,"
        "sans-serif")
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"

DEFAULT_FOOTER = "Sent by The Agent · AI Hub"

_SAFE_SCHEME = re.compile(r"^(?:https?://|mailto:)", re.IGNORECASE)
# The URL half allows ONE level of balanced parens so real links survive —
# e.g. https://en.wikipedia.org/wiki/Foo_(bar) — which a plain [^)]+ would cut
# short, silently emitting a broken href plus a stray ')'.
_MD_LINK = re.compile(r"\[([^\]\n]{1,300})\]\(((?:[^()\s]|\([^()\s]*\)){1,600})\)")
_BOLD = re.compile(r"\*\*([^*\n]{1,400})\*\*")
_CODE = re.compile(r"`([^`\n]{1,400})`")
_BARE_URL = re.compile(r"(https?://[^\s<>\"]{4,600})")
_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")
_BULLET = re.compile(r"^[-*]\s+(.*)$")
_ORDERED = re.compile(r"^\d{1,3}[.)]\s+(.*)$")
_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")

_PLACEHOLDER = "\x00L{}\x00"


def html_enabled() -> bool:
    """Global kill switch. Read at CALL time so flipping it needs no code change
    — only a service restart to pick up a changed .env."""
    return os.getenv("AGENT_EMAIL_HTML", "true").strip().lower() != "false"


def esc(s) -> str:
    return _html.escape(str(s if s is not None else ""))


def _safe_url(url: str) -> Optional[str]:
    """Fail-closed scheme guard: only http(s)/mailto become links. Anything else
    (javascript:, data:, file:) is rendered as inert text, never as an href."""
    url = (url or "").strip()
    return url if _SAFE_SCHEME.match(url) else None


def _anchor(url: str, label: str) -> str:
    return (f'<a href="{esc(url)}" style="color:{ACCENT};'
            f'text-decoration:underline;">{esc(label)}</a>')


def _inline(raw: str) -> str:
    """Links are stashed as placeholders on the RAW text, the remaining prose is
    then escaped, and the (already-escaped) anchors are restored last.

    Order matters. Escaping first and slicing afterwards mismatches offsets the
    moment a URL contains '&' (one raw char, five escaped), which silently
    corrupts the href. Stashing first also stops the bare-URL pass from reaching
    inside an anchor the markdown-link pass just built.
    """
    text = str(raw if raw is not None else "").replace("\x00", "")
    links: list = []

    def _stash(m):
        label, url = m.group(1), m.group(2)
        safe = _safe_url(url)
        if not safe:
            return label            # inert: keep the words, never emit the href
        links.append(_anchor(safe, label))
        return _PLACEHOLDER.format(len(links) - 1)

    text = _MD_LINK.sub(_stash, text)

    def _auto(m):
        url = m.group(1)
        # Trailing sentence punctuation belongs to the prose, not the URL.
        trailing = ""
        while url:
            last = url[-1]
            # A ')' is only punctuation when the URL has no '(' to match it.
            if last in ".,;:!?" or (last == ")" and "(" not in url):
                trailing = last + trailing
                url = url[:-1]
            else:
                break
        safe = _safe_url(url)
        if not safe:
            return m.group(0)
        links.append(_anchor(safe, safe))
        return _PLACEHOLDER.format(len(links) - 1) + trailing

    text = _BARE_URL.sub(_auto, text)
    text = esc(text)
    text = _BOLD.sub(r'<strong style="font-weight:600;">\1</strong>', text)
    text = _CODE.sub(
        f'<code style="font-family:{MONO};font-size:13px;background:{CODE_BG};'
        f'padding:1px 5px;border-radius:4px;">\\1</code>', text)

    for i, anchor in enumerate(links):
        text = text.replace(_PLACEHOLDER.format(i), anchor)
    return text


def _split_row(line: str) -> list:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _table(header: list, rows: list) -> str:
    """A pipe table -> a bordered HTML table. Used by Phase 1 for tables the
    model writes; Phase 2's View renderer will reuse the same cell styling."""
    th = "".join(
        f'<th align="left" style="padding:8px 10px;border-bottom:2px solid {LINE};'
        f'background:{PAGE};color:{MUTED};font-size:12px;font-weight:600;'
        f'text-transform:uppercase;letter-spacing:.03em;">{_inline(c)}</th>'
        for c in header)
    body = []
    for r in rows:
        cells = "".join(
            f'<td style="padding:8px 10px;border-bottom:1px solid {LINE};'
            f'background:{CARD};color:{INK};font-size:14px;vertical-align:top;">'
            f'{_inline(c)}</td>'
            for c in (r + [""] * (len(header) - len(r)))[:len(header)])
        body.append(f"<tr>{cells}</tr>")
    return (f'<table role="presentation" width="100%" cellpadding="0" '
            f'cellspacing="0" border="0" style="width:100%;border-collapse:collapse;'
            f'margin:14px 0;">'
            f"<thead><tr>{th}</tr></thead><tbody>{''.join(body)}</tbody></table>")


def render_markdownish(body: str) -> str:
    """Block-level conversion of the model's prose. Deliberately small: headings,
    bullet/numbered lists, pipe tables, rules, paragraphs. Single newlines inside
    a paragraph become <br> — in email, what the author typed is what they meant,
    and markdown's line-joining surprises people."""
    out: list = []
    para: list = []
    items: list = []
    list_tag = ""

    def flush_para():
        if para:
            out.append(
                f'<p style="margin:0 0 14px;color:{INK};font-size:15px;'
                f'line-height:1.55;">{"<br>".join(_inline(p) for p in para)}</p>')
            para.clear()

    def flush_list():
        nonlocal list_tag
        if items:
            lis = "".join(
                f'<li style="margin:0 0 6px;color:{INK};font-size:15px;'
                f'line-height:1.55;">{_inline(i)}</li>' for i in items)
            out.append(f'<{list_tag} style="margin:0 0 14px;padding-left:22px;">'
                       f'{lis}</{list_tag}>')
            items.clear()
        list_tag = ""

    def flush_all():
        flush_para()
        flush_list()

    lines = (body or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]

        # Pipe table: a header row followed by a |---|---| separator.
        if ("|" in line and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1])
                and len(_split_row(line)) > 1):
            flush_all()
            header = _split_row(line)
            i += 2
            rows = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            out.append(_table(header, rows))
            continue

        if not line.strip():
            flush_all()
            i += 1
            continue

        if _RULE.match(line):
            flush_all()
            out.append(f'<hr style="border:none;border-top:1px solid {LINE};'
                       f'margin:20px 0;">')
            i += 1
            continue

        m = _HEADING.match(line)
        if m:
            flush_all()
            level = len(m.group(1))
            size = {1: 20, 2: 17, 3: 15}[level]
            top = 0 if not out else 22
            out.append(
                f'<div style="margin:{top}px 0 10px;color:{INK};'
                f'font-size:{size}px;font-weight:700;line-height:1.3;">'
                f'{_inline(m.group(2))}</div>')
            i += 1
            continue

        m = _BULLET.match(line)
        if m:
            flush_para()
            if list_tag and list_tag != "ul":
                flush_list()
            list_tag = "ul"
            items.append(m.group(1))
            i += 1
            continue

        m = _ORDERED.match(line)
        if m:
            flush_para()
            if list_tag and list_tag != "ol":
                flush_list()
            list_tag = "ol"
            items.append(m.group(1))
            i += 1
            continue

        flush_list()
        para.append(line.strip())
        i += 1

    flush_all()
    return "".join(out)


def render_shell(content_html: str, title: str = "",
                 footer: Optional[str] = DEFAULT_FOOTER) -> str:
    """Wrap rendered content in the 600px email shell. Full document on purpose —
    unlike a web page fragment, an email body IS a document."""
    foot = ""
    if footer:
        foot = (f'<div style="padding:14px 8px 0;color:{MUTED};font-size:12px;'
                f'line-height:1.5;text-align:center;">{esc(footer)}</div>')
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{esc(title)}</title></head>"
        f'<body style="margin:0;padding:0;background:{PAGE};'
        f'-webkit-text-size-adjust:100%;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="background:{PAGE};width:100%;">'
        f'<tr><td align="center" style="padding:24px 12px;">'
        f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        f'border="0" style="width:600px;max-width:100%;background:{CARD};'
        f'border:1px solid {LINE};border-radius:12px;">'
        f'<tr><td style="padding:28px 32px;font-family:{FONT};font-size:15px;'
        f'line-height:1.55;color:{INK};">{content_html}</td></tr></table>'
        f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        f'border="0" style="width:600px;max-width:100%;font-family:{FONT};">'
        f"<tr><td>{foot}</td></tr></table>"
        "</td></tr></table></body></html>")


def render_email(body: str, title: str = "",
                 footer: Optional[str] = DEFAULT_FOOTER) -> str:
    """markdown-ish prose -> a complete, email-client-safe HTML document."""
    return render_shell(render_markdownish(body), title=title, footer=footer)


# ---------------------------------------------------------------------------
# View dashboards (Phase 2)
#
# Input is run_view()'s output verbatim — the same LLM-free contract the Views
# screen renders. Two deliberate departures from the on-screen version:
#
#   * TILES STACK. A View's grid layout (per-tile w/h spans) does not survive
#     email clients; one full-width tile per row is the honest port.
#   * ROWS ARE CAPPED HARDER (15 vs the store's 50). Gmail clips a message over
#     ~102KB and eight 50-row tiles can reach it — a clipped dashboard hides
#     data without saying so, which is worse than an explicit "showing 15 of N".
#
# Staleness and per-tile errors are rendered, never hidden: run_view serves the
# last good cache when a refresh fails, so an email that dropped that label
# would present stale numbers as current.
# ---------------------------------------------------------------------------

EMAIL_ROW_CAP = 15
# Columns need a cap MORE than rows do. Measured on a real saved View: one
# 24-column tile rendered 43KB of a 48KB email — inline styles repeat per cell,
# so width costs far more than height. Twenty-four columns is also unreadable in
# a 600px email. Both problems have the same fix, and the count is stated.
EMAIL_COL_CAP = 8
# Per-dimension caps can always be beaten by a COMBINATION: 8 tiles x 15 rows x
# 8 columns still measured 104KB, past Gmail's ~102KB clip point. So the whole
# fragment gets a hard byte budget as well, and tiles that don't fit are
# reported rather than dropped — a clipped email hides data without saying so.
EMAIL_HTML_BUDGET = 70_000
EMAIL_TEXT_BUDGET = 24_000
# Driver errors are enormous — a single dead SQL connection produces ~700 chars
# of ODBC spew, and a 4-tile View repeating it in both formats measured ~50KB,
# half of Gmail's ~102KB clip budget. On screen the full text is fine (the View
# is right there); in email the first line carries the diagnosis and the deep
# link carries the rest.
ERROR_CHARS = 180
BAR_FILL = "#4D9FFF"
BAR_TRACK = "#EDF1F5"
ERR_INK = "#B42318"
ERR_BG = "#FEF3F2"
ERR_LINE = "#FDA29B"


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt(v) -> str:
    """Thousands separators for every number, integer or not — grouping only
    some of a column ("8,693" next to "16259.35") reads as a rendering bug. We
    do NOT round: inventing or dropping precision in a dashboard is a lie."""
    if v is None:
        return ""
    n = _num(v)
    if n is None:
        return str(v)
    return f"{int(n):,}" if float(n).is_integer() else f"{n:,}"


def _first_numeric_col(columns, rows) -> int:
    """Mirrors the UI's firstNumericCol (index.html): column 0 is the label,
    the first column with numbers is the value."""
    for c in range(1, len(columns)):
        if any(_num(r[c]) is not None for r in rows if len(r) > c):
            return c
    return 1 if len(columns) > 1 else 0


def _cell(content: str, *, head: bool = False, align: str = "left") -> str:
    """Deliberately terse. Colour and font-size INHERIT from the table element
    (they do in every major client), so only the properties that don't inherit —
    padding and borders — are repeated per cell. With hundreds of cells in a
    wide table, the saving is most of the message size."""
    if head:
        return (f'<th align="{align}" style="padding:7px 10px;'
                f'border-bottom:2px solid {LINE};">{content}</th>')
    return (f'<td align="{align}" style="padding:7px 10px;'
            f'border-bottom:1px solid {LINE};">{content}</td>')


def _short_error(text) -> str:
    """One tidy line: collapse the driver's newlines and keep the head, which is
    where the diagnosis lives ('wait operation timed out', 'invalid object name')."""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= ERROR_CHARS else flat[:ERROR_CHARS].rstrip() + "…"


def _note(text: str) -> str:
    return (f'<div style="margin:8px 0 0;color:{MUTED};font-size:11px;'
            f'line-height:1.4;">{esc(text)}</div>')


def _tile_stat(columns, rows) -> str:
    value = rows[0][0] if rows and rows[0] else ""
    label = columns[0] if columns else ""
    return (f'<div style="font-size:30px;font-weight:700;color:{INK};'
            f'line-height:1.15;">{esc(_fmt(value))}</div>'
            f'<div style="margin-top:2px;color:{MUTED};font-size:12px;">'
            f'{esc(label)}</div>')


def _tile_bar(columns, rows) -> str:
    """Bars as NESTED TABLES with bgcolor — Outlook's Word engine is unreliable
    with div widths, so a coloured cell sized in % is the portable bar."""
    yc = _first_numeric_col(columns, rows)
    pts = [(str(r[0]) if r else "", _num(r[yc]) if len(r) > yc else None)
           for r in rows]
    pts = [(x, y) for x, y in pts if y is not None]
    if not pts:
        return _note("no numeric data to chart")
    peak = max((abs(y) for _, y in pts), default=0) or 1
    out = []
    for label, value in pts[:EMAIL_ROW_CAP]:
        pct = max(1, min(100, int(round(abs(value) / peak * 100))))
        fill = (f'<table role="presentation" width="100%" cellpadding="0" '
                f'cellspacing="0" border="0" style="width:100%;'
                f'border-collapse:collapse;"><tr>'
                f'<td width="{pct}%" bgcolor="{BAR_FILL}" '
                f'style="width:{pct}%;background:{BAR_FILL};height:14px;'
                f'font-size:0;line-height:14px;border-radius:3px;">&nbsp;</td>'
                f'<td style="font-size:0;line-height:14px;">&nbsp;</td>'
                f"</tr></table>")
        out.append(
            f"<tr>"
            f'<td width="34%" style="padding:5px 8px 5px 0;color:{INK};'
            f'font-size:13px;vertical-align:middle;">{esc(label)}</td>'
            f'<td bgcolor="{BAR_TRACK}" style="background:{BAR_TRACK};'
            f'border-radius:3px;vertical-align:middle;">{fill}</td>'
            f'<td align="right" style="padding:5px 0 5px 10px;color:{INK};'
            f'font-size:13px;font-weight:600;white-space:nowrap;'
            f'vertical-align:middle;">{esc(_fmt(value))}</td>'
            f"</tr>")
    body = (f'<table role="presentation" width="100%" cellpadding="0" '
            f'cellspacing="0" border="0" style="width:100%;'
            f'border-collapse:collapse;">{"".join(out)}</table>')
    if len(pts) > EMAIL_ROW_CAP:
        body += _note(f"showing {EMAIL_ROW_CAP} of {len(pts)}")
    return body


def _tile_table(columns, rows, row_count=None, cap_applied=False) -> str:
    if not columns:
        return _note("no data")
    cols = columns[:EMAIL_COL_CAP]
    shown = rows[:EMAIL_ROW_CAP]
    head = "".join(_cell(esc(c), head=True) for c in cols)
    body = []
    for r in shown:
        padded = (list(r) + [""] * len(columns))[:len(cols)]
        body.append("<tr>" + "".join(
            _cell(esc(_fmt(v)), align="right" if _num(v) is not None else "left")
            for v in padded) + "</tr>")
    html = (f'<table role="presentation" width="100%" cellpadding="0" '
            f'cellspacing="0" border="0" style="width:100%;'
            f'border-collapse:collapse;color:{INK};font-size:13px;">'
            f'<thead style="background:{PAGE};color:{MUTED};font-size:11px;'
            f'text-transform:uppercase;letter-spacing:.03em;">'
            f"<tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>")
    total = row_count if isinstance(row_count, int) else len(rows)
    bits = []
    if len(rows) > EMAIL_ROW_CAP:
        bits.append(f"showing {EMAIL_ROW_CAP} of {total} rows")
    else:
        bits.append(f"{total} row{'s' if total != 1 else ''}")
    if len(columns) > EMAIL_COL_CAP:
        bits.append(f"first {EMAIL_COL_CAP} of {len(columns)} columns")
    if cap_applied:
        bits.append("server row cap applied")
    return html + _note(" · ".join(bits))


def _tile_body(tile, columns, rows) -> str:
    viz = (tile.get("viz") or "auto").lower()
    if viz == "bar" and rows:
        return _tile_bar(columns, rows)
    if viz in ("stat", "auto", "") and len(rows) == 1 and len(columns) == 1:
        return _tile_stat(columns, rows)
    # line and ticker have no portable email form: a chart needs an image and a
    # marquee needs CSS animation. Both degrade to the data itself.
    return _tile_table(columns, rows, tile.get("row_count"),
                       bool(tile.get("cap_applied")))


def render_tile(tile: dict) -> str:
    title = tile.get("title") or "(untitled)"
    if str(tile.get("type") or "") == "automation":
        title += f" · ▷ {tile.get('automation') or 'automation'}"
    parts = [f'<div style="margin:0 0 10px;color:{INK};font-size:15px;'
             f'font-weight:600;line-height:1.3;">{esc(title)}</div>']

    error = tile.get("error")
    cache = tile.get("cache") or {}
    if error:
        parts.append(
            f'<div style="margin:0 0 10px;padding:8px 10px;background:{ERR_BG};'
            f'border:1px solid {ERR_LINE};border-radius:6px;color:{ERR_INK};'
            f'font-size:12px;line-height:1.45;">{esc(_short_error(error))}</div>')
        if cache.get("rows") is not None:
            as_of = str(cache.get("cached_at") or "")[:16].replace("T", " ")
            parts.append(_tile_body(tile, cache.get("columns") or [],
                                    cache.get("rows") or []))
            parts.append(_note(f"as of {as_of or 'earlier'} — last good result"))
    else:
        parts.append(_tile_body(tile, tile.get("columns") or [],
                                tile.get("rows") or []))

    return (f'<table role="presentation" width="100%" cellpadding="0" '
            f'cellspacing="0" border="0" style="width:100%;margin:0 0 16px;'
            f'border-collapse:separate;"><tr>'
            f'<td style="padding:16px 18px;background:{CARD};'
            f'border:1px solid {LINE};border-radius:10px;">'
            f'{"".join(parts)}</td></tr></table>')


def _tile_text(tile) -> str:
    """Plain-text alternative — the half every non-HTML client gets."""
    title = tile.get("title") or "(untitled)"
    lines = [f"-- {title} --"]
    error = tile.get("error")
    cache = tile.get("cache") or {}
    if error:
        lines.append(f"   ! {_short_error(error)}")
        columns, rows = cache.get("columns") or [], cache.get("rows") or []
        if rows:
            as_of = str(cache.get("cached_at") or "")[:16].replace("T", " ")
            lines.append(f"   (as of {as_of or 'earlier'} — last good result)")
    else:
        columns, rows = tile.get("columns") or [], tile.get("rows") or []
    if columns:
        cols = columns[:EMAIL_COL_CAP]
        lines.append("   " + " | ".join(str(c) for c in cols))
        for r in rows[:EMAIL_ROW_CAP]:
            lines.append("   " + " | ".join(
                _fmt(v) for v in (list(r) + [""] * len(columns))[:len(cols)]))
        total = tile.get("row_count") if isinstance(tile.get("row_count"), int) \
            else len(rows)
        notes = []
        if len(rows) > EMAIL_ROW_CAP:
            notes.append(f"showing {EMAIL_ROW_CAP} of {total} rows")
        if len(columns) > EMAIL_COL_CAP:
            notes.append(f"first {EMAIL_COL_CAP} of {len(columns)} columns")
        if notes:
            lines.append("   ... " + ", ".join(notes))
    return "\n".join(lines)


def render_view(view: dict, base_url: str = "") -> tuple:
    """run_view() output -> (html_fragment, text_fragment).

    Returns a FRAGMENT, not a document: the caller composes it with the prose
    body so one email carries both, in both formats.
    """
    name = view.get("name") or "View"
    desc = view.get("description") or ""
    head = [f'<div style="margin:22px 0 4px;color:{INK};font-size:18px;'
            f'font-weight:700;line-height:1.3;">{esc(name)}</div>']
    if desc:
        head.append(f'<div style="margin:0 0 14px;color:{MUTED};font-size:13px;'
                    f'line-height:1.45;">{esc(desc)}</div>')
    else:
        head.append('<div style="height:10px;line-height:10px;">&nbsp;</div>')

    tiles = view.get("tiles") or []

    # Budgeted assembly: the first tile always renders (an empty dashboard helps
    # nobody), after which tiles are added only while they fit.
    rendered, dropped, used = [], 0, 0
    for i, t in enumerate(tiles):
        block = render_tile(t)
        if i and used + len(block) > EMAIL_HTML_BUDGET:
            dropped = len(tiles) - i
            break
        rendered.append(block)
        used += len(block)
    html = "".join(head) + "".join(rendered)
    if dropped:
        html += _note(f"{dropped} more tile{'s' if dropped != 1 else ''} not "
                      "shown — the full dashboard would be clipped by some mail "
                      "clients. Open the View in AI Hub for all of it.")

    link = (base_url or "").rstrip("/")
    if link.startswith("http"):
        html += (f'<div style="margin:2px 0 0;font-size:12px;">'
                 f'<a href="{esc(link)}/the-agent" style="color:{ACCENT};'
                 f'text-decoration:underline;">Open this View in AI Hub</a></div>')

    text = [f"== {name} =="]
    if desc:
        text.append(desc)
    text.append("")
    tused, tdropped = 0, 0
    for i, t in enumerate(tiles):
        block = _tile_text(t)
        if i and tused + len(block) > EMAIL_TEXT_BUDGET:
            tdropped = len(tiles) - i
            break
        text.append(block)
        text.append("")
        tused += len(block)
    if tdropped:
        text.append(f"({tdropped} more tile{'s' if tdropped != 1 else ''} not "
                    "shown — open the View in AI Hub for all of it.)")
        text.append("")
    if link.startswith("http"):
        text.append(f"Open this View in AI Hub: {link}/the-agent")
    return html, "\n".join(text)


def render_email_with_view(body: str, view_html: str = "", title: str = "",
                           footer: Optional[str] = DEFAULT_FOOTER) -> str:
    """Prose + an embedded dashboard, as one email-safe document."""
    return render_shell(render_markdownish(body) + (view_html or ""),
                        title=title, footer=footer)
