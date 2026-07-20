"""
AIHUB-0060 — response-side secret masking must not break markdown, and secret
NAMES (by-name doctrine references like AUTODEMO_SFTP) stay displayable.

Live evidence (james, three transcripts): the masker rewrote
"secret: `AUTODEMO_SFTP`" to "secret: `***" — consuming the CLOSING backtick —
and the unclosed code span made everything after render flat ("responses read
like dense text"). The masker slice is exec'd from chat.py source so these
tests exercise the REAL patterns without importing the flask route.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_CHAT = Path(__file__).resolve().parents[2] / "command_center_service" / "routes" / "chat.py"


def _mask_resp():
    src = _CHAT.read_text(encoding="utf-8")
    start = src.find("                import re as _rrm")
    end = src.find("                if isinstance(blocks, list):", start)
    assert start > 0 and end > start, "masker slice not found in chat.py"
    body = "\n".join(ln[16:] if len(ln) > 16 else ln
                     for ln in src[start:end].splitlines())
    ns = {}
    exec(body, ns)  # noqa: S102 - our own source under test
    return ns["_mask_resp"]


class TestMaskingKeepsMarkdownBalanced:
    def test_backticked_value_masked_with_closing_backtick_restored(self):
        m = _mask_resp()
        out = m("Connect with secret: `hunter2value` and continue.")
        assert "hunter2value" not in out
        assert "secret: `***`" in out                      # balanced!
        assert out.count("`") % 2 == 0

    def test_password_and_token_masked_balanced(self):
        m = _mask_resp()
        out = m("password: `sup3rS3cret!` then token: `abcDEF123xyz`")
        assert "sup3rS3cret!" not in out and "abcDEF123xyz" not in out
        assert out.count("`") % 2 == 0

    def test_the_live_transcript_shape_no_longer_mangles(self):
        m = _mask_resp()
        out = m("* Uploads the CSV using secret: `AUTODEMO_SFTP`\n* Declared verified outputs")
        assert out.count("`") % 2 == 0
        assert "* Declared verified outputs" in out


class TestSecretNamesStayDisplayable:
    def test_upper_snake_secret_reference_not_masked(self):
        m = _mask_resp()
        for text in ("using secret: `AUTODEMO_SFTP` into /outgoing",
                     "secret: AUTODEMO_SFTP", "Secret = SQL_10_0_0_6_PASSWORD"):
            assert "***" not in m(text), text

    def test_lowercase_or_mixed_secret_values_still_masked(self):
        m = _mask_resp()
        assert "***" in m("secret: `myactualsecretvalue`")
        assert "***" in m("secret: aB3$-not-a-name!x".replace("$", ""))

    def test_password_labels_mask_even_upper_snake(self):
        # the name exemption applies ONLY to the 'secret' label — a password
        # value that happens to be UPPER_SNAKE still masks
        m = _mask_resp()
        assert "***" in m("password: `HUNTER_TWO_X`")

    def test_json_shapes_unchanged(self):
        m = _mask_resp()
        out = m('{"password": "hunter2", "api_key": "abc123xyz"}')
        assert "hunter2" not in out and "abc123xyz" not in out
        assert '"password": "***"' in out
