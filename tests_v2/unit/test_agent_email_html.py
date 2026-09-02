"""The Agent — HTML email rendering + transport plumbing (Phase 1 of
docs/the-agent-html-email-views-plan.md).

Two things are locked down here:

  1. `email_render` produces EMAIL-safe HTML. Email is not the web: a <style>
     block or a CSS class silently loses all styling in Gmail, so those are
     invariants, not preferences. Injection and URL-scheme guards are tested
     because the body is model-authored text.

  2. `email_client.send_reply` stays byte-identical when no html_body is passed
     (the feature is additive), and never sends HTML-only when it is.

Runs standalone (python test_agent_email_html.py) or under pytest.
"""
import asyncio
import json
import os
import sys
from html.parser import HTMLParser

import httpx

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

import email_client  # noqa: E402
import email_render as R  # noqa: E402

_RealAsyncClient = httpx.AsyncClient


# ---------------------------------------------------------------------------
# Renderer — structure
# ---------------------------------------------------------------------------

def test_headings_lists_and_paragraphs():
    html = R.render_markdownish("# Title\n\nSome prose.\n\n- one\n- two\n\n1. first")
    assert "Title" in html and "font-weight:700" in html      # heading
    assert "<ul" in html and html.count("<li") == 3            # 2 bullets + 1 ordered
    assert "<ol" in html
    assert "<p" in html and "Some prose." in html


def test_single_newlines_become_br_inside_a_paragraph():
    """In email, what the author typed is what they meant — markdown's
    line-joining surprises people."""
    assert "<br>" in R.render_markdownish("line one\nline two")


def test_pipe_table_becomes_a_table():
    html = R.render_markdownish("| Region | Rev |\n|---|---|\n| North | 12 |\n| South | 9 |")
    assert "<table" in html and "<thead" in html
    assert html.count("<tr") == 3            # header + 2 body rows
    assert "Region" in html and "South" in html


def test_ragged_table_row_is_padded_not_dropped():
    html = R.render_markdownish("| A | B | C |\n|---|---|---|\n| 1 |")
    assert html.count("<tr") == 2
    assert "<td" in html


# ---------------------------------------------------------------------------
# Renderer — safety (the body is model-authored)
# ---------------------------------------------------------------------------

def test_html_in_the_body_is_escaped_not_emitted():
    html = R.render_markdownish("<script>alert(1)</script>")
    assert "<script" not in html
    assert "&lt;script&gt;" in html


def test_dangerous_url_schemes_never_become_links():
    for bad in ("javascript:alert(1)", "data:text/html;base64,x", "file:///etc/passwd"):
        html = R.render_markdownish(f"[click]({bad})")
        assert "href" not in html, bad
        assert "click" in html            # inert: the words survive, the link does not


def test_safe_schemes_do_become_links():
    for good in ("https://a.co", "http://a.co", "mailto:x@a.co"):
        assert f'href="{good}"' in R.render_markdownish(f"[go]({good})")


def test_ampersand_in_url_survives_escaping():
    """Regression: escaping before stashing mismatched offsets ('&' is 1 raw
    char but 5 escaped), which silently truncated the href."""
    html = R.render_markdownish("Query https://a.co/x?a=1&b=2&c=3 now.")
    assert 'href="https://a.co/x?a=1&amp;b=2&amp;c=3"' in html


def test_balanced_parens_in_url_are_kept():
    """Regression: a plain [^)]+ URL pattern cut this short and left a stray ')'."""
    url = "https://en.wikipedia.org/wiki/Foo_(bar)"
    for src in (f"[wiki]({url})", f"see {url} there"):
        html = R.render_markdownish(src)
        assert f'href="{url}"' in html, src


def test_trailing_sentence_punctuation_is_not_part_of_a_bare_url():
    html = R.render_markdownish("Visit https://a.co.")
    assert 'href="https://a.co"' in html
    assert html.rstrip().endswith(".</p>")


# ---------------------------------------------------------------------------
# Renderer — email-client invariants
# ---------------------------------------------------------------------------

def test_no_style_block_and_no_classes():
    """Gmail strips <style> in several contexts; a class-based design would
    lose ALL styling there. Inline styles only."""
    html = R.render_email("# Hi\n\n- a\n\n| A |\n|---|\n| 1 |")
    assert "<style" not in html.lower()
    assert "class=" not in html.lower()
    assert 'style="' in html


def test_no_modern_layout_primitives():
    """Outlook renders with the Word engine and ignores these entirely."""
    html = R.render_email("# Hi\n\n| A | B |\n|---|---|\n| 1 | 2 |").lower()
    for banned in ("display:flex", "display:grid", "float:", "position:absolute"):
        assert banned not in html, banned


def test_output_is_well_formed():
    void = {"br", "hr", "meta", "img", "input"}
    stack, errors = [], []

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag not in void:
                stack.append(tag)

        def handle_endtag(self, tag):
            if not stack or stack[-1] != tag:
                errors.append((tag, list(stack[-3:])))
            else:
                stack.pop()

    P().feed(R.render_email("# T\n\n- a\n- b\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\ntail"))
    assert not errors and not stack, (errors, stack)


def test_degenerate_bodies_never_raise():
    for body in ("", "   ", "#", "|", "|a|b|", "- ", "**", "`", "\x00", "---"):
        R.render_email(body)


def test_kill_switch(monkeypatch=None):
    old = os.environ.get("AGENT_EMAIL_HTML")
    try:
        os.environ["AGENT_EMAIL_HTML"] = "false"
        assert R.html_enabled() is False
        os.environ["AGENT_EMAIL_HTML"] = "true"
        assert R.html_enabled() is True
        del os.environ["AGENT_EMAIL_HTML"]
        assert R.html_enabled() is True          # default ON
    finally:
        if old is None:
            os.environ.pop("AGENT_EMAIL_HTML", None)
        else:
            os.environ["AGENT_EMAIL_HTML"] = old


# ---------------------------------------------------------------------------
# Transport plumbing
# ---------------------------------------------------------------------------

def _capture_send(**kwargs):
    """Run send_reply against a mock transport and return the posted payload."""
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content.decode()))
        return httpx.Response(200, json={"success": True, "message_id": "m1"})

    orig = email_client.httpx.AsyncClient
    email_client.httpx.AsyncClient = (
        lambda **kw: _RealAsyncClient(transport=httpx.MockTransport(handler)))
    try:
        result = asyncio.run(email_client.send_reply(
            ["a@b.co"], "Subj", "plain body", "agent@x.co", "Agent", **kwargs))
    finally:
        email_client.httpx.AsyncClient = orig
    return seen, result


def test_no_html_body_means_an_unchanged_payload():
    """The feature is ADDITIVE: without html_body the request must look exactly
    as it did before it existed."""
    payload, result = _capture_send()
    assert "html_body" not in payload
    assert payload["body"] == "plain body"
    assert payload["provider"] == "mailgun"
    assert result.get("success") is True


def test_html_body_is_sent_alongside_the_plain_text():
    """Never HTML-only — the markdown-ish text is always the alternative."""
    payload, _ = _capture_send(html_body="<html><body>hi</body></html>")
    assert payload["html_body"] == "<html><body>hi</body></html>"
    assert payload["body"] == "plain body"


def test_empty_html_body_is_omitted_rather_than_sent_blank():
    payload, _ = _capture_send(html_body="")
    assert "html_body" not in payload


# ---------------------------------------------------------------------------
# View dashboards (Phase 2) — run_view() output -> email
# ---------------------------------------------------------------------------

def _view(*tiles, name="Ops Board", description="How we're doing"):
    return {"name": name, "description": description, "scope": "user",
            "version": 3, "tiles": list(tiles)}


def test_stat_tile_renders_the_value_large():
    html, text = R.render_view(_view(
        {"index": 0, "title": "Open orders", "viz": "stat", "type": "sql",
         "columns": ["open_orders"], "rows": [[1240]]}))
    assert "1,240" in html                      # thousands separator
    assert "font-size:30px" in html
    assert "Open orders" in html and "1,240" in text


def test_bar_tile_uses_nested_tables_not_divs():
    """Outlook's Word engine is unreliable with div widths."""
    html, _ = R.render_view(_view(
        {"index": 0, "title": "By region", "viz": "bar", "type": "sql",
         "columns": ["region", "revenue"],
         "rows": [["North", 100], ["South", 50]]}))
    assert "bgcolor=" in html and 'width="100%"' in html
    assert 'width="50%"' in html                # South is half of North
    # The bar itself must be a coloured TABLE CELL, never a sized <div>.
    assert 'bgcolor="#4D9FFF"' in html
    assert "<div style=\"width:" not in html


def test_bar_tile_scales_to_the_largest_absolute_value():
    html, _ = R.render_view(_view(
        {"index": 0, "title": "T", "viz": "bar", "type": "sql",
         "columns": ["k", "v"], "rows": [["a", 10], ["b", 40]]}))
    assert 'width="25%"' in html and 'width="100%"' in html


def test_line_and_ticker_degrade_to_a_table():
    """No portable email form exists for either — show the data instead."""
    for viz in ("line", "ticker"):
        html, _ = R.render_view(_view(
            {"index": 0, "title": "T", "viz": viz, "type": "sql",
             "columns": ["day", "n"], "rows": [["Mon", 1], ["Tue", 2]]}))
        assert "<table" in html and "Mon" in html and "Tue" in html, viz


def test_failed_tile_shows_the_error_and_labels_stale_cache():
    """run_view serves the last good cache on failure; an email that dropped
    the label would present stale numbers as current."""
    html, text = R.render_view(_view(
        {"index": 0, "title": "Revenue", "viz": "table", "type": "sql",
         "error": "SQL error: invalid object name 'sales'",
         "cache": {"columns": ["region", "rev"], "rows": [["North", 12]],
                   "cached_at": "2026-08-12T09:30:00+00:00"}}))
    assert "invalid object name" in html
    assert "as of 2026-08-12 09:30" in html
    assert "last good result" in html
    assert "invalid object name" in text and "last good result" in text


def test_failed_tile_with_no_cache_shows_only_the_error():
    html, _ = R.render_view(_view(
        {"index": 0, "title": "Revenue", "viz": "table", "type": "sql",
         "error": "connection refused"}))
    assert "connection refused" in html
    assert "last good result" not in html


def test_rows_are_capped_for_email_and_the_truncation_is_stated():
    """Gmail clips over ~102KB; a silently truncated dashboard is worse than an
    explicit count."""
    rows = [[f"r{i}", i] for i in range(40)]
    html, text = R.render_view(_view(
        {"index": 0, "title": "Big", "viz": "table", "type": "sql",
         "columns": ["k", "v"], "rows": rows, "row_count": 40}))
    assert "showing 15 of 40 rows" in html
    assert "showing 15 of 40 rows" in text
    assert "r14" in html and "r15" not in html


def test_numbers_are_grouped_consistently_and_never_rounded():
    """Found on real data: grouping only whole numbers put '8,693' next to
    '16259.35' in one column, which reads as a rendering bug."""
    html, _ = R.render_view(_view(
        {"index": 0, "title": "By vendor", "viz": "table", "type": "sql",
         "columns": ["vendor", "due"],
         "rows": [["A", 8693.0], ["B", 16259.35], ["C", 1378.8]]}))
    assert "8,693" in html
    assert "16,259.35" in html          # grouped AND full precision kept
    assert "1,378.8" in html
    assert "16259.35" not in html


def test_non_numeric_values_pass_through_untouched():
    html, _ = R.render_view(_view(
        {"index": 0, "title": "T", "viz": "table", "type": "sql",
         "columns": ["a", "b"], "rows": [["INV-2026-001", "n/a"]]}))
    assert "INV-2026-001" in html and "n/a" in html


ODBC = ("SQL error: Database error: ('08001', '[08001] [Microsoft][ODBC Driver 17 "
        "for SQL Server]TCP Provider: The wait operation timed out.\r\n (258) "
        "(SQLDriverConnect); [08001] [Microsoft][ODBC Driver 17 for SQL Server]"
        "Login timeout expired (0); [08001] [Microsoft][ODBC Driver 17 for SQL "
        "Server]Invalid connection string attribute (0); [08001] [Microsoft]"
        "[ODBC Driver 17 for SQL Server]A network-related or instance-specific "
        "error has occurred while establishing a connection to SQL Server. "
        "Server is not found or not accessible. (258)')")


def test_driver_errors_are_trimmed_but_keep_the_diagnosis():
    """Measured on real data: 4 tiles of raw ODBC spew rendered ~50KB, half of
    Gmail's clip budget."""
    html, text = R.render_view(_view(
        {"index": 0, "title": "T", "viz": "stat", "type": "sql", "error": ODBC}))
    assert "wait operation timed out" in html      # the diagnosis survives
    assert "…" in html
    assert "\r\n" not in html and "\r\n" not in text
    assert len(html) < 2000


def test_wide_tables_are_capped_by_column_and_the_count_is_stated():
    """Found on a real View: a 24-column tile rendered 43KB of a 48KB email.
    Inline styles repeat per cell, so width costs far more than height — and 24
    columns is unreadable in a 600px email regardless."""
    cols = [f"c{i}" for i in range(24)]
    html, text = R.render_view(_view(
        {"index": 0, "title": "Wide", "viz": "table", "type": "sql",
         "columns": cols, "rows": [[i for i in range(24)]]}))
    assert "first 8 of 24 columns" in html
    assert "first 8 of 24 columns" in text
    assert "c7" in html and "c8" not in html
    assert len(html) < 4000


def test_a_worst_case_view_stays_well_under_the_gmail_clip_threshold():
    """8 tiles (MAX_TILES) x 50 rows (TILE_ROW_CAP) x 24 columns, all failing
    loudly — the shape that measured ~48KB for FOUR tiles before the caps."""
    tiles = [{"index": i, "title": f"Tile {i}", "viz": "table", "type": "sql",
              "error": ODBC,
              "cache": {"columns": [f"column_name_{c}" for c in range(24)],
                        "rows": [[f"value {j}-{c}" for c in range(24)]
                                 for j in range(50)],
                        "cached_at": "2026-08-12T09:30:00+00:00"}}
             for i in range(8)]
    view_html, text = R.render_view(_view(*tiles))
    doc = R.render_email_with_view("# Daily board\n\nHere it is.", view_html)
    assert len(doc.encode()) < 90_000, len(doc.encode())
    assert len(text.encode()) < 30_000, len(text.encode())
    # The HTML budget bites here, and dropped tiles are REPORTED — never
    # silently missing. (The text alternative is ~5x smaller and stays within
    # its own budget at this size, so it carries every tile.)
    assert "not shown" in view_html and "Open the View in AI Hub" in view_html


def test_the_first_tile_always_renders_and_drops_are_reported():
    """An empty dashboard helps nobody, so tile 0 renders whatever its size.
    Budget is lowered here rather than faked with a giant fixture."""
    tile = {"index": 0, "title": "Huge", "viz": "table", "type": "sql",
            "columns": ["a", "b"], "rows": [["value-one", "value-two"]]}
    original = R.EMAIL_HTML_BUDGET
    try:
        R.EMAIL_HTML_BUDGET = 100          # smaller than a single tile
        html, _ = R.render_view(_view(tile, tile, tile))
        assert "Huge" in html and "value-one" in html
        assert "2 more tiles not shown" in html
    finally:
        R.EMAIL_HTML_BUDGET = original


def test_automation_tile_names_its_automation():
    html, _ = R.render_view(_view(
        {"index": 0, "title": "Nightly", "viz": "table", "type": "automation",
         "automation": "daily-rollup", "columns": ["a"], "rows": [[1]]}))
    assert "daily-rollup" in html


def test_ragged_and_null_rows_do_not_break_rendering():
    html, _ = R.render_view(_view(
        {"index": 0, "title": "T", "viz": "table", "type": "sql",
         "columns": ["a", "b", "c"], "rows": [["x"], ["y", None, 3], []]}))
    assert "<table" in html


def test_view_html_keeps_the_email_invariants():
    html, _ = R.render_view(_view(
        {"index": 0, "title": "A", "viz": "stat", "type": "sql",
         "columns": ["n"], "rows": [[1]]},
        {"index": 1, "title": "B", "viz": "bar", "type": "sql",
         "columns": ["k", "v"], "rows": [["a", 1]]}))
    assert "<style" not in html.lower() and "class=" not in html.lower()
    for banned in ("display:flex", "display:grid", "float:", "position:absolute"):
        assert banned not in html.lower(), banned


def test_deep_link_only_when_a_public_base_url_exists():
    tile = {"index": 0, "title": "A", "viz": "stat", "type": "sql",
            "columns": ["n"], "rows": [[1]]}
    html, text = R.render_view(_view(tile), base_url="https://hub.example.com")
    assert 'href="https://hub.example.com/the-agent"' in html
    assert "https://hub.example.com/the-agent" in text
    html2, _ = R.render_view(_view(tile), base_url="")
    assert "the-agent" not in html2


def test_prose_and_dashboard_compose_into_one_document():
    view_html, _ = R.render_view(_view(
        {"index": 0, "title": "Open", "viz": "stat", "type": "sql",
         "columns": ["n"], "rows": [[7]]}))
    doc = R.render_email_with_view("# Morning\n\nHere's where we stand.",
                                   view_html, title="Morning")
    assert doc.startswith("<!DOCTYPE html>") and doc.rstrip().endswith("</html>")
    assert "Morning" in doc and "Here&#x27;s where we stand." in doc
    assert "Open" in doc and ">7<" in doc
    assert doc.count("<!DOCTYPE") == 1          # one document, not nested


def test_empty_view_renders_without_raising():
    html, text = R.render_view({"name": "Empty", "tiles": []})
    assert "Empty" in html and "Empty" in text


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def test_refresh_summary_tells_the_model_what_actually_happened():
    """Regression from the first live run: every tile had failed and was serving
    4-day-old cache, and the agent's covering note called them "current figures"
    — it cannot see tile data, so the tool has to say so."""
    from work_tools import _refresh_summary

    ok = {"columns": ["a"], "rows": [[1]]}
    stale = {"error": "boom", "cache": {"columns": ["a"], "rows": [[1]],
                                        "cached_at": "2026-08-09T18:53:00+00:00"}}
    dead = {"error": "boom"}

    assert _refresh_summary([ok, ok]) == "all 2 tiles refreshed live"

    s = _refresh_summary([stale, stale, stale, stale])
    assert "0 of 4 tiles refreshed live" in s
    assert "4 could NOT refresh" in s

    mixed = _refresh_summary([ok, stale, dead])
    assert "1 of 3 tiles refreshed live" in mixed
    assert "1 could NOT refresh" in mixed and "1 failed with no data" in mixed


def test_every_registered_tool_is_actually_decorated():
    """Regression, 2026-08-13: a plain helper added BETWEEN a @tool block and
    its intended function silently stole the decoration, leaving the real tool
    undecorated. py_compile passed; the service died at import with
    "'function' object has no attribute 'name'". A one-line guard beats a dead
    service, and it covers every tool module at once.
    """
    import brain  # builds the MCP server from all the *_TOOLS lists

    from authoring_tools import AUTHORING_TOOLS
    from document_tools import DOCUMENT_TOOLS
    from file_tools import FILE_TOOLS
    from integration_tools import INTEGRATION_TOOLS
    from platform_tools import AIHUB_TOOLS
    from views_tools import VIEWS_TOOLS
    from work_tools import WORK_TOOLS
    from agent_builder_tools import AGENT_BUILDER_TOOLS

    registries = {"AIHUB": AIHUB_TOOLS, "AUTHORING": AUTHORING_TOOLS,
                  "WORK": WORK_TOOLS, "VIEWS": VIEWS_TOOLS,
                  "INTEGRATION": INTEGRATION_TOOLS, "FILE": FILE_TOOLS,
                  "DOCUMENT": DOCUMENT_TOOLS, "AGENT_BUILDER": AGENT_BUILDER_TOOLS}
    for label, tools in registries.items():
        for t in tools:
            assert hasattr(t, "name"), (
                f"{label}_TOOLS contains an UNDECORATED function: "
                f"{getattr(t, '__name__', t)!r} — a @tool block above it is "
                f"probably decorating the wrong function")

    names = [t.name for t in WORK_TOOLS]
    assert "draft_email_reply" in names
    assert "render_view_for_email" not in names      # a helper, not a tool


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                passed += 1
                print(f"PASS {name}")
            except Exception as e:
                failed += 1
                print(f"FAIL {name}: {e}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
