"""
schedule_tz.py - resolve a user's spoken timezone (or their browser default) to a canonical
zone string for scheduling, and turn that canonical string into a tzinfo the APScheduler engine
can fire on.

Lives at the repo ROOT on purpose: it is imported by BOTH
  * the Command Center scheduling tools (command_center_service/graph/nodes.py) - RESOLUTION, at
    schedule-creation time (to pick the zone + produce a confirmation note), and
  * the job_scheduler engine (job_scheduler.py) - tzinfo CONSTRUCTION, at fire time.

Design contract: NEVER raise and NEVER block scheduling. resolve_timezone() always returns a
usable canonical zone (falling back to the browser zone, then UTC); genuine ambiguity (e.g. "IST"
= India vs Israel vs Ireland) is surfaced as a human-readable `note` for the agent to confirm,
not as an error. This is the "so it does not fail" guarantee.

Canonical forms returned/accepted:
  * a valid IANA name            e.g. "America/New_York", "Asia/Kolkata", "UTC"   (DST-aware)
  * a fixed offset "UTC+HH:MM"   e.g. "UTC+05:30", "UTC-08:00"                     (DST-less, explicit)

We deliberately map bare US/EU abbreviations (EST, PST, BST, ...) to the REGION zone
(America/New_York, ...) rather than the same-named fixed-offset zoneinfo keys, because a user who
says "9am EST" means Eastern wall-clock time year-round (which is EDT in summer), not a frozen
UTC-5. zoneinfo happily resolves "EST" to a fixed -5 with no DST, which would be wrong - so the
abbreviation table is consulted BEFORE any "is this a real IANA key" check.
"""
import re
from datetime import timedelta, timezone as _dt_timezone

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - zoneinfo is stdlib on 3.9+
    ZoneInfo = None


# Common, UNAMBIGUOUS abbreviations / spoken names -> IANA region zone (DST-aware on purpose).
_ABBREV_TO_IANA = {
    # ---- North America ----
    "ET": "America/New_York", "EASTERN": "America/New_York",
    "EST": "America/New_York", "EDT": "America/New_York",
    "CT": "America/Chicago", "CENTRAL": "America/Chicago",
    "CST": "America/Chicago", "CDT": "America/Chicago",
    "MT": "America/Denver", "MOUNTAIN": "America/Denver",
    "MST": "America/Denver", "MDT": "America/Denver",
    "PT": "America/Los_Angeles", "PACIFIC": "America/Los_Angeles",
    "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles",
    "AKST": "America/Anchorage", "AKDT": "America/Anchorage", "ALASKA": "America/Anchorage",
    "HST": "Pacific/Honolulu", "HAST": "Pacific/Honolulu", "HAWAII": "Pacific/Honolulu",
    "AST": "America/Halifax", "ADT": "America/Halifax", "ATLANTIC": "America/Halifax",
    "NST": "America/St_Johns", "NDT": "America/St_Johns",
    # ---- UTC / GMT ----
    "UTC": "UTC", "GMT": "UTC", "ZULU": "UTC", "Z": "UTC", "UT": "UTC",
    # ---- Europe / Africa ----
    "BST": "Europe/London", "UK": "Europe/London", "LONDON": "Europe/London", "BRITISH": "Europe/London",
    "WET": "Europe/Lisbon", "WEST": "Europe/Lisbon",
    "CET": "Europe/Paris", "CEST": "Europe/Paris",
    "EET": "Europe/Athens", "EEST": "Europe/Athens",
    "MSK": "Europe/Moscow", "MOSCOW": "Europe/Moscow",
    "SAST": "Africa/Johannesburg",
    # ---- Middle East / South Asia ----
    "GST": "Asia/Dubai", "DUBAI": "Asia/Dubai",
    "PKT": "Asia/Karachi",
    "BDT": "Asia/Dhaka",
    "NPT": "Asia/Kathmandu",
    "IRST": "Asia/Tehran",
    # ---- East Asia / SE Asia ----
    "JST": "Asia/Tokyo", "TOKYO": "Asia/Tokyo",
    "KST": "Asia/Seoul", "SEOUL": "Asia/Seoul",
    "HKT": "Asia/Hong_Kong",
    "SGT": "Asia/Singapore", "SINGAPORE": "Asia/Singapore",
    "ICT": "Asia/Bangkok",
    "WIB": "Asia/Jakarta",
    "PHT": "Asia/Manila",
    "BEIJING": "Asia/Shanghai", "SHANGHAI": "Asia/Shanghai",
    # ---- Oceania ----
    "AEST": "Australia/Sydney", "AEDT": "Australia/Sydney", "SYDNEY": "Australia/Sydney",
    "ACST": "Australia/Adelaide", "ACDT": "Australia/Adelaide",
    "AWST": "Australia/Perth",
    "NZST": "Pacific/Auckland", "NZDT": "Pacific/Auckland",
}

# Genuinely AMBIGUOUS abbreviations that are NOT in the table above. We resolve to the most
# likely zone but attach a note so the agent can confirm the region with the user.
_AMBIGUOUS = {
    "IST": ("Asia/Kolkata", ["Asia/Kolkata (India)", "Asia/Jerusalem (Israel)", "Europe/Dublin (Ireland)"]),
    "AMT": ("America/Manaus", ["America/Manaus (Amazon)", "Asia/Yerevan (Armenia)"]),
    "WST": ("Pacific/Apia", ["Pacific/Apia (Samoa)", "Australia/Perth (Western Australia)"]),
    "ECT": ("America/Guayaquil", ["America/Guayaquil (Ecuador)", "Europe/Paris (Central Europe)"]),
}

_OFFSET_RE = re.compile(r'^\s*(?:UTC|GMT)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?\s*$', re.IGNORECASE)
# Allow a trailing signed-digit tail so 'Etc/GMT-5' / 'Etc/GMT+8' extract whole (not truncated
# to 'Etc/GMT' = UTC+0). The full token is still validated via _is_valid_iana before use.
_SLASH_RE = re.compile(r'[A-Za-z]+(?:/[A-Za-z_]+)+(?:[+-]\d{1,2})?')
_WORD_RE = re.compile(r'[A-Za-z]{1,12}')


def _is_valid_iana(name):
    if not name or ZoneInfo is None:
        return False
    try:
        ZoneInfo(name)
        return True
    except Exception:
        return False


def _parse_offset(text):
    """'UTC+5:30' / 'GMT-8' / '+0530' / '-08:00' -> canonical 'UTC+05:30', else None.
    Requires an explicit +/- sign so plain numbers ('9am') never parse as an offset."""
    if not text:
        return None
    m = _OFFSET_RE.match(text)
    if not m:
        return None
    sign, hh, mm = m.group(1), int(m.group(2)), int(m.group(3) or 0)
    if hh > 14 or mm > 59:
        return None
    return f"UTC{sign}{hh:02d}:{mm:02d}"


def _extract_token(spoken):
    """Pull a single zone-ish token from a phrase like '9am EST' / 'run at 9 IST' / 'Asia/Tokyo'.
    Returns the token (original case) or ''."""
    s = (spoken or "").strip()
    if not s:
        return ""
    slash = _SLASH_RE.search(s)
    if slash:
        return slash.group(0)
    known = set(_ABBREV_TO_IANA) | set(_AMBIGUOUS)
    last = ""
    for w in _WORD_RE.findall(s):
        if w.upper() in known or (w.isupper() and 2 <= len(w) <= 5):
            last = w
    return last


def resolve_timezone(spoken="", iana_hint="", browser_tz=""):
    """Resolve to (canonical, display, note).

    Priority (each rung only runs if the ones above miss):
      1. explicit numeric offset in `spoken`        ('UTC+5:30' -> fixed offset)
      2. unambiguous abbreviation/name table         ('EST' -> America/New_York, DST-aware)
      3. `spoken` is itself a full IANA name          ('Asia/Tokyo')
      4. LLM-supplied IANA best-guess, VALIDATED      ('IST' + hint 'Asia/Kolkata')
      5. ambiguous-abbreviation default + confirm     ('IST' -> Asia/Kolkata, note alternatives)
      6. browser timezone (the no-explicit-zone default)
      7. UTC (last resort; note that we couldn't recognize what was said)

    Never raises (including on non-string args — they are coerced to '').
    """
    spoken = spoken.strip() if isinstance(spoken, str) else ""
    iana_hint = iana_hint.strip() if isinstance(iana_hint, str) else ""
    browser_tz = browser_tz.strip() if isinstance(browser_tz, str) else ""
    token = _extract_token(spoken)

    # 1. explicit numeric offset (DST-less by definition - the user gave a fixed offset)
    off = _parse_offset(spoken) or _parse_offset(token)
    if off is not None:
        return off, off, ""

    key = token.upper().replace(" ", "")

    # 2. unambiguous abbreviation / spoken name -> DST-aware region zone
    if key in _ABBREV_TO_IANA:
        iana = _ABBREV_TO_IANA[key]
        return iana, iana, ""

    # 3. token is already a full IANA name (must contain a '/', or be UTC) - excludes bare
    #    fixed-offset keys like 'EST' which zoneinfo would otherwise accept with no DST
    if token and ("/" in token) and _is_valid_iana(token):
        return token, token, ""

    # 4. LLM best-guess IANA, validated (handles the international long tail, incl. IST->Asia/...)
    if iana_hint and _is_valid_iana(iana_hint):
        note = f"Interpreted '{token or spoken}' as {iana_hint}." if (token or spoken) else ""
        return iana_hint, iana_hint, note

    # 5. ambiguous abbreviation with no usable hint: pick the most likely, ask to confirm
    if key in _AMBIGUOUS:
        iana, alts = _AMBIGUOUS[key]
        note = f"'{token}' is ambiguous - assuming {iana}. If you meant another, say which: {', '.join(alts)}."
        return iana, iana, note

    # 6. default to the browser's zone when the user named no (recognizable) zone.
    #    Gate it the same way the spoken IANA rung is gated: require a REGION zone (or UTC),
    #    never a bare fixed-offset key. A real browser always sends a region name
    #    ("America/New_York"); rejecting bare abbreviations here means an injected/buggy
    #    "EST" can't sneak in as a frozen UTC-5 (no-DST) zone and defeat the EST trap.
    if browser_tz and ("/" in browser_tz or browser_tz.upper() == "UTC") and _is_valid_iana(browser_tz):
        note = ""
        if spoken and not token:
            note = f"Didn't recognize the timezone '{spoken}'; used your browser timezone ({browser_tz}). Tell me a city/region to change it."
        elif token and key not in _ABBREV_TO_IANA:
            note = f"Didn't recognize '{token}'; used your browser timezone ({browser_tz})."
        return browser_tz, browser_tz, note

    # 7. last resort - never fail
    note = ""
    if spoken:
        note = f"Couldn't determine a timezone from '{spoken}', so I used UTC. Tell me a city/region to set it correctly."
    return "UTC", "UTC", note


def to_tzinfo(canonical):
    """Turn a canonical zone string from resolve_timezone() into a tzinfo for APScheduler.
    Returns a tzinfo, or None if it can't be built (caller should fall back to its default).
    Accepts a valid IANA name OR a fixed 'UTC+HH:MM' offset. Never raises - returns None if a
    tzinfo can't be built (so the caller falls back to its default zone)."""
    name = canonical.strip() if isinstance(canonical, str) else ""
    if not name:
        return None
    m = re.match(r'^UTC([+-])(\d{2}):(\d{2})$', name)
    if m:
        try:
            sign = 1 if m.group(1) == '+' else -1
            delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
            if delta >= timedelta(hours=24):   # datetime.timezone requires strictly within ±24h
                return None
            return _dt_timezone(sign * delta)
        except Exception:
            return None
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            return None
    return None
