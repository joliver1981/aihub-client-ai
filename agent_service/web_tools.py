"""
The Agent's web tools — internet search (gap G3, 2026-09-02).

Mirrors the MAIN APP's WebSearch.py (the General Agent's web_search core
tool), not Command Center's Tavily-only copy: Tavily when a key is configured
— TAVILY_API_KEY plain, else TAVILY_API_KEY_ENCRYPTED decrypted exactly the way
config.py does (encrypt.decrypt_value) — and the keyless DuckDuckGo Lite engine
as the fallback, so search works on installs with no key at all. httpx +
stdlib only (BeautifulSoup is not in the aihub-agent env). Never raises into
the agent loop; a failed search is reported as a failure, never as "no
internet access".
"""

import os
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from claude_agent_sdk import tool

from agent_config import logger
from platform_tools import _text

TAVILY_URL = "https://api.tavily.com/search"
DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
_UA = "Mozilla/5.0 (compatible; AIHub-TheAgent/1.0)"
MAX_RESULTS = 10
_SNIPPET_CAP = 400


def tavily_key() -> str:
    """Plain env first (parity with CC), else the platform's encrypted form."""
    key = (os.getenv("TAVILY_API_KEY") or "").strip()
    if key:
        return key
    enc = (os.getenv("TAVILY_API_KEY_ENCRYPTED") or "").strip()
    if not enc:
        return ""
    try:
        from encrypt import decrypt_value, ENCRYPTION_KEY
        return (decrypt_value(enc, ENCRYPTION_KEY) or "").strip()
    except Exception as e:
        logger.warning(f"TAVILY_API_KEY_ENCRYPTED could not be decrypted: {e}")
        return ""


def default_engine() -> str:
    return (os.getenv("DEFAULT_INTERNET_SEARCH") or "tavily").strip().lower()


def parse_tavily(data: dict, n: int) -> tuple:
    """(answer, [{title, link, snippet}]) from Tavily's response shape."""
    data = data or {}
    answer = str(data.get("answer") or "").strip()
    results = []
    for r in (data.get("results") or [])[:n]:
        results.append({"title": str(r.get("title") or "").strip(),
                        "link": str(r.get("url") or "").strip(),
                        "snippet": str(r.get("content") or "").strip()})
    return answer, results


class _DDGLiteParser(HTMLParser):
    """Collects (title, href, snippet) from DuckDuckGo Lite's result table:
    <a class="result-link" href=...>title</a> ... <td class="result-snippet">.
    A link without a following snippet is still a result."""

    def __init__(self):
        super().__init__()
        self.results = []
        self._in_link = False
        self._in_snippet = False
        self._cur = None

    def _flush(self):
        if self._cur is not None:
            self.results.append(self._cur)
            self._cur = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "a" and "result-link" in cls:
            self._flush()
            self._cur = {"title": "", "link": a.get("href") or "", "snippet": ""}
            self._in_link = True
        elif tag in ("td", "div") and "result-snippet" in cls and self._cur is not None:
            self._in_snippet = True

    def handle_endtag(self, tag):
        if tag == "a" and self._in_link:
            self._in_link = False
        elif tag in ("td", "div") and self._in_snippet:
            self._in_snippet = False
            self._flush()

    def handle_data(self, data):
        if self._cur is None:
            return
        if self._in_link:
            self._cur["title"] += data
        elif self._in_snippet:
            self._cur["snippet"] += data

    def close(self):
        super().close()
        self._flush()


def clean_link(href: str) -> str:
    """DDG wraps targets as //duckduckgo.com/l/?uddg=<url>&rut=...; unwrap."""
    h = (href or "").strip()
    if h.startswith("//"):
        h = "https:" + h
    try:
        u = urlparse(h)
        if "duckduckgo.com" in (u.netloc or "") and u.path.startswith("/l/"):
            target = parse_qs(u.query).get("uddg", [""])[0]
            if target:
                return unquote(target)
    except Exception:
        pass
    return h


def parse_ddg_lite(html_text: str, n: int) -> list:
    p = _DDGLiteParser()
    p.feed(html_text or "")
    p.close()
    out = []
    for r in p.results:
        title = " ".join(unescape(r["title"]).split())
        link = clean_link(r["link"])
        snippet = " ".join(unescape(r["snippet"]).split())
        if title or link:
            out.append({"title": title, "link": link, "snippet": snippet})
        if len(out) >= n:
            break
    return out


def format_results(query: str, engine: str, answer: str, results: list) -> str:
    lines = [f"Web search ({engine}) for: {query}"]
    if answer:
        lines.append(f"Summary (from the search provider): {answer}")
    if results:
        lines.append("Sources:")
        for i, r in enumerate(results, 1):
            snip = r.get("snippet") or ""
            if len(snip) > _SNIPPET_CAP:
                snip = snip[:_SNIPPET_CAP] + "…"
            lines.append(f"{i}. {r.get('title') or 'Untitled'} — {r.get('link') or ''}"
                         + (f"\n   {snip}" if snip else ""))
    else:
        lines.append("No results.")
    lines.append("(Cite the sources you rely on; verify the summary against them "
                 "when it matters.)")
    return "\n".join(lines)


async def _search_tavily(query: str, n: int, key: str) -> tuple:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=20.0)) as client:
        r = await client.post(
            TAVILY_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"query": query, "include_answer": "basic", "max_results": n})
        r.raise_for_status()
        return parse_tavily(r.json(), n)


async def _search_ddg(query: str, n: int) -> list:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=20.0),
                                 follow_redirects=True) as client:
        r = await client.get(DDG_LITE_URL, params={"q": query},
                             headers={"User-Agent": _UA})
        r.raise_for_status()
        return parse_ddg_lite(r.text, n)


@tool(
    "search_web",
    "Search the INTERNET for current information — news, weather, prices, "
    "releases, documentation, company facts, anything time-sensitive or outside "
    "your training data. YES, you have web access: use this instead of guessing "
    "at live facts or saying you can't browse. Returns the provider's short "
    "summary (when available) plus titled sources with links — cite them in "
    "your reply. Be specific in the query. num_results 1-10 (default 5).",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for"},
            "num_results": {"type": "integer", "description": "1-10, default 5"},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)
async def search_web(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return _text("Give me something to search for.", is_error=True)
    try:
        n = max(1, min(MAX_RESULTS, int(args.get("num_results") or 5)))
    except (TypeError, ValueError):
        n = 5
    key = tavily_key()
    engine = default_engine()
    errors = []
    if key and engine != "duckduckgo":
        try:
            answer, results = await _search_tavily(query, n, key)
            if results or answer:
                return _text(format_results(query, "tavily", answer, results))
            errors.append("tavily returned no results")
        except Exception as e:
            logger.warning(f"tavily search failed: {e}")
            errors.append(f"tavily: {type(e).__name__}: {e}")
    try:
        results = await _search_ddg(query, n)
        if results:
            note = ("" if not errors
                    else f"\n(Tavily unavailable — {errors[0]}; used DuckDuckGo.)")
            return _text(format_results(query, "duckduckgo", "", results) + note)
        errors.append("duckduckgo returned no results")
    except Exception as e:
        logger.warning(f"duckduckgo search failed: {e}")
        errors.append(f"duckduckgo: {type(e).__name__}: {e}")
    if not key:
        errors.append("no Tavily key is configured (TAVILY_API_KEY / "
                      "TAVILY_API_KEY_ENCRYPTED)")
    return _text("Web search FAILED — " + "; ".join(errors)
                 + ". Tell the user the search failed; do not invent results.",
                 is_error=True)


WEB_TOOLS = [search_web]
