"""Guard against the recurring scheduler timezone-display bug class.

The scheduler API returns NAIVE UTC datetimes (isoformat, no 'Z'). Rendering
one with a bare ``new Date(...)`` in the browser reads it as LOCAL time and
shifts the display by the UTC offset (+4h in EDT). This bug has now been fixed
independently in static/js/monitoring.js (commit 76e34de) and in the document
processor templates (2026-08-30) because each surface carried its own drifting
copy of the date helpers.

These tests scan the UI sources so the bug class cannot silently return:

1. No bare ``new Date(<schedule field>)`` parses in templates or static JS —
   server datetimes must go through the shared helpers, which stamp 'Z' first.
2. The canonical helpers live ONLY in static/js/schedule_display.js and
   static/js/monitoring.js — no new per-page copies that can drift.

Pure file scans, no app imports, no external deps.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# The only files allowed to define the canonical date helpers.
# monitoring.js predates the shared file and is live-verified; if it is ever
# folded into schedule_display.js, remove it here.
CANONICAL_HELPER_FILES = {
    Path("static/js/schedule_display.js"),
    Path("static/js/monitoring.js"),
}

# Server-supplied datetime fields that arrive as naive-UTC strings.
NAIVE_UTC_FIELDS = (
    "next_run_time",
    "last_run_time",
    "start_date",
    "end_date",
    "start_time",
    "end_time",
)

BARE_PARSE_RE = re.compile(
    r"new Date\(\s*\w+\.(" + "|".join(NAIVE_UTC_FIELDS) + r")\b"
)

HELPER_DEF_RE = re.compile(
    r"function\s+(normalizeUtcDateString|parseUtcDate|formatDateTime|"
    r"formatDateTimeForInput|getIntervalDescription)\s*\("
)


def _ui_sources():
    files = sorted(REPO_ROOT.glob("templates/**/*.html"))
    files += sorted(REPO_ROOT.glob("static/js/*.js"))
    assert files, "UI source scan found no files — repo layout changed?"
    return files


@pytest.mark.unit
def test_no_bare_new_date_on_schedule_fields():
    """Naive-UTC schedule fields must never be parsed with a bare new Date()."""
    offenders = []
    for path in _ui_sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), start=1):
            match = BARE_PARSE_RE.search(line)
            if match:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{i}: {line.strip()}")
    assert not offenders, (
        "Bare `new Date(<field>)` on a naive-UTC datetime — the browser reads "
        "it as LOCAL time and the display shifts by the UTC offset. Route it "
        "through the shared helpers in static/js/schedule_display.js "
        "(formatDateTime / formatDateTimeForInput / parseUtcDate):\n"
        + "\n".join(offenders)
    )


@pytest.mark.unit
def test_date_helpers_only_defined_in_canonical_files():
    """Per-page copies of the date helpers drift and re-open the bug — ban them."""
    offenders = []
    for path in _ui_sources():
        rel = path.relative_to(REPO_ROOT)
        if rel in CANONICAL_HELPER_FILES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), start=1):
            match = HELPER_DEF_RE.search(line)
            if match:
                offenders.append(f"{rel}:{i}: defines {match.group(1)}()")
    assert not offenders, (
        "Scheduler date helpers may only be defined in "
        f"{sorted(str(p) for p in CANONICAL_HELPER_FILES)} — include "
        "static/js/schedule_display.js instead of copying them:\n"
        + "\n".join(offenders)
    )
