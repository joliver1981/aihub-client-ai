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
