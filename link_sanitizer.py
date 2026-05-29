"""
Strip fabricated file URLs from LLM-generated markdown.

LLMs frequently write markdown links like `[output.pdf](http://host/output.pdf)`
in their prose even when the tool that generated the file already returned a
proper artifact block. Those invented URLs 404. This module unwraps such links,
keeping the text and dropping the broken href.

Real artifact URLs (multi-segment download paths used by this app) are passed
through untouched.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

# A URL path that's just a single filename with a known extension —
# essentially never a legitimate href from an LLM in this app.
_SINGLE_FILENAME_RE = re.compile(r"^/?[^/\s]+\.[A-Za-z0-9]{2,5}$")

# Markers identifying real artifact/download URLs the app actually serves.
_REAL_URL_MARKERS = (
    "/api/chat/artifacts/",
    "/api/chat/files/",
    "/api/artifacts/",
    "/download/",
)


def strip_fabricated_file_links(markdown: str) -> str:
    """Unwrap markdown links whose URL looks like a fabricated single-file
    path. Keeps the link text; drops the broken href. Real artifact links
    pass through unchanged."""
    if not isinstance(markdown, str) or "](" not in markdown:
        return markdown

    def _sub(m: "re.Match[str]") -> str:
        text = m.group(1)
        url = m.group(2).strip()
        if any(marker in url for marker in _REAL_URL_MARKERS):
            return m.group(0)
        try:
            path = urlparse(url).path or url
        except Exception:
            return m.group(0)
        if _SINGLE_FILENAME_RE.match(path):
            return text
        return m.group(0)

    return _LINK_RE.sub(_sub, markdown)
