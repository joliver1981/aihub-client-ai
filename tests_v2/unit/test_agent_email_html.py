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
