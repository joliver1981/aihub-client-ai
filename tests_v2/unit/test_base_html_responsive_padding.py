"""Static guard for AIHUB-0002 — committed + CI-runnable, no live app needed.

`templates/base.html`'s `.new-content-area` must keep >= 15px horizontal padding
so it absorbs the `-15px` side gutters of a bare Bootstrap `.row` placed directly
in the content area. If that padding is ever re-zeroed (it had regressed to the
invalid `padding: -1px -1px`, which clamps to 0), every bare-`.row` page overflows
the viewport by ~15px again. The behavioral journey test
(tests_v2/journeys/test_real_user_content_no_horizontal_overflow.py) verifies the
rendered result against a live app, but it is gitignored by the repo's `test*.py`
convention; this parse-only check is tracked so the regression is caught in CI.
"""
from __future__ import annotations

import re
from pathlib import Path

BASE_HTML = Path(__file__).resolve().parents[2] / "templates" / "base.html"
MIN_HORIZONTAL_PADDING_PX = 15


def _px(value: str):
    value = value.strip()
    if value == "0":  # unitless zero is valid CSS and means 0px
        return 0.0
    m = re.match(r"^(-?[0-9.]+)px$", value)
    return float(m.group(1)) if m else None


def _horizontal_padding(block: str):
    """Return (left, right) padding in px parsed from a CSS rule body, honoring
    the `padding` shorthand plus any `padding-left`/`padding-right` longhand."""
    left = right = None
    shorthand = re.search(r"(?<![-\w])padding\s*:\s*([^;}]+)", block)
    if shorthand:
        parts = shorthand.group(1).split()
        vals = [_px(p) for p in parts]
        if len(parts) == 1:           # all sides
            left = right = vals[0]
        elif len(parts) in (2, 3):    # v h  |  t h b  -> 2nd value is horizontal
            left = right = vals[1]
        elif len(parts) >= 4:         # t r b l
            right, left = vals[1], vals[3]
    pl = re.search(r"padding-left\s*:\s*([^;}]+)", block)
    pr = re.search(r"padding-right\s*:\s*([^;}]+)", block)
    if pl:
        left = _px(pl.group(1))
    if pr:
        right = _px(pr.group(1))
    return left, right


def test_new_content_area_keeps_horizontal_padding():
    css = BASE_HTML.read_text(encoding="utf-8")
    # The base `.new-content-area { ... }` rule (not the `> .tier-banner` break-out).
    m = re.search(r"\.new-content-area\s*\{([^}]*)\}", css)
    assert m, ".new-content-area rule not found in templates/base.html"
    # Strip CSS comments so the parser doesn't read the `padding: -1px -1px`
    # quoted inside the explanatory comment instead of the real declaration.
    block = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.DOTALL)
    left, right = _horizontal_padding(block)
    assert left is not None and right is not None, (
        f"could not parse .new-content-area padding from base.html rule:\n{block!r}"
    )
    assert left >= MIN_HORIZONTAL_PADDING_PX and right >= MIN_HORIZONTAL_PADDING_PX, (
        f".new-content-area horizontal padding is {left}/{right}px but must be "
        f">= {MIN_HORIZONTAL_PADDING_PX}px to absorb a bare Bootstrap .row's -15px "
        f"gutters (AIHUB-0002). Re-zeroing it reintroduces the ~15px page overflow."
    )
