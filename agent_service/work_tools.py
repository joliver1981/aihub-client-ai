"""
The Agent's My Work tools — raise and inspect work items (A2).

raise_work_item is the seam that lets the assistant route work to humans: a
question it needs answered, something to review, a draft to edit, an FYI. In
interactive chat the human is right there — so the tool is mostly for things
addressed to OTHER people or for work that should outlive the conversation;
headless runs (A3) will lean on it heavily.

schedule_agent_task (A3) schedules headless runs: recurring, BOUNDED-recurring
("every 10 minutes for the next hour" — engine-native end_date + max_runs, one
job the engine stops itself) and one-shot delayed; cron timezones are engine-
native (the per-schedule `timezone` parameter, DST-aware). Since 2026-08-22 it
also captures the chat session it was asked from so each firing can append its
result to that conversation (main.py /api/run resume, AGENT_DEFER_TO_CHAT).
"""

import json
import os
from typing import Any

from platform_tools import CURRENT_USER, _text
from claude_agent_sdk import tool
import workitem_store


@tool(
    "raise_work_item",
    "Put a work item in someone's My Work queue. Use when something needs a "
    "human decision, review, input, or awareness that should be tracked — "
    "especially for someone OTHER than the current user, or work that must "
    "outlive this conversation. Verbs: approve_deny, review, provide_input, "
    "edit_and_return, acknowledge, do_offline. Leave addressed_user_id at 0 "
    "for a shared item anyone can claim. Never fabricate payload evidence — "
    "only include facts from this conversation's tool results.",
    {
        "type": "object",
        "properties": {
            "verb": {"type": "string",
                     "enum": ["approve_deny", "review", "provide_input",
                              "edit_and_return", "acknowledge", "do_offline"]},
            "title": {"type": "string", "description": "Short imperative title"},
            "summary": {"type": "string",
                        "description": "Everything needed to act, inline"},
            "addressed_user_id": {"type": "integer",
                                  "description": "0 = shared (anyone claims)"},
            "priority": {"type": "integer", "description": "0 normal, 1 high"},
            "payload_json": {"type": "string",
                             "description": "Optional JSON evidence payload"},
        },
        "required": ["verb", "title", "summary"],
        "additionalProperties": False,
    },
)
async def raise_work_item(args: dict[str, Any]) -> dict[str, Any]:
    user = CURRENT_USER.get()
    payload = {}
    if args.get("payload_json"):
        try:
            payload = json.loads(args["payload_json"])
        except Exception:
            return _text("payload_json is not valid JSON", is_error=True)
    # Fail-closed: reserved payload kinds are minted ONLY by their sanctioned
    # tools. A generic work item must never be able to impersonate one and
    # turn a human's approval into a publish or an email send.
    if isinstance(payload, dict) and payload.get("kind") in (
            "skill_promotion", "view_promotion", "agent_email_reply"):
        return _text("payload.kind '" + str(payload["kind"]) + "' is reserved "
                     "— use save_skill / save_view / draft_email_reply "
                     "instead.", is_error=True)
    addressed = int(args.get("addressed_user_id") or 0) or None
    try:
        item = workitem_store.create_item(
            str(args["verb"]), str(args["title"]).strip(),
            summary=str(args.get("summary") or ""),
            payload=payload,
            addressed_user=addressed,
            from_kind="agent_session",
            from_ref=str(user.get("username") or ""),
            priority=int(args.get("priority") or 0),
            created_by=str(user.get("username") or "agent"),
        )
    except ValueError as e:
        return _text(str(e), is_error=True)
    who = f"user {addressed}" if addressed else "the shared queue (anyone can claim)"
    return _text(f"Work item created: '{item['title']}' "
                 f"(id {item['work_item_id']}, {item['verb']}) — addressed to {who}. "
                 "It is now visible in My Work.")


@tool(
    "list_my_work",
    "List the open items in the current user's My Work queue (their personal "
    "items plus unclaimed shared items).",
    {},
)
async def list_my_work(args: dict[str, Any]) -> dict[str, Any]:
    user = CURRENT_USER.get()
    items = workitem_store.list_items(int(user.get("user_id") or 0))
    if not items:
        return _text("The My Work queue is empty — nothing is waiting on you.")
    lines = []
    for it in items[:30]:
        who = "you" if it.get("addressed_user") else (
            f"claimed by you" if it.get("claimed_by") else "unclaimed · shared")
        lines.append(f"- [{it['verb']}] {it['title']} (id {it['work_item_id'][:8]}…, "
                     f"{it['status']}, {who}, raised {it['created_at']})")
    return _text(f"Open work items ({len(items)}):\n" + "\n".join(lines))


# --- timezone support (2026-08-20; engine-native 2026-08-22; user-zone default 2026-08-22) ---
# The scheduler engine fires CRON triggers in the per-schedule `timezone` job
# parameter (job_scheduler._create_trigger -> schedule_tz.to_tzinfo, DST-aware,
# shipped 2b15fd3). So this tool stores the cron AS THE USER WROTE IT plus the
# canonical zone, and the engine does the (DST-correct) math at fire time.
# ⚠ It must NOT also pre-convert the hour field to UTC: the engine re-applies
# the zone and the job fires double-shifted (live repro 2026-08-22: job 453,
# "0 7 * * 1-5" Eastern stored as "0 11 * * 1-5" + timezone America/New_York ->
# engine next run 15:00 UTC = 11am Eastern, four hours late).
#
# WHICH zone when the user names none (james 2026-08-22): their BROWSER zone —
# main.py stamps user["browser_timezone"] from the UI's Intl zone, exactly the
# way Command Center does — then AGENT_DEFAULT_TZ, then the server's zone
# (Windows zone mapped to IANA so DST is right; a fixed offset as last resort).
# Every time we state back to the user is rendered in that same zone.

_TZ_ALIASES = {
    "eastern": "America/New_York", "et": "America/New_York",
    "est": "America/New_York", "edt": "America/New_York",
    "central": "America/Chicago", "ct": "America/Chicago",
    "cst": "America/Chicago", "cdt": "America/Chicago",
    "mountain": "America/Denver", "mt": "America/Denver",
    "mst": "America/Denver", "mdt": "America/Denver",
    "pacific": "America/Los_Angeles", "pt": "America/Los_Angeles",
    "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles",
    "utc": "UTC", "gmt": "UTC", "z": "UTC",
}

# Windows zone ids (time.tzname[0]) -> IANA. The server fallback must be
# DST-aware; Windows has no IANA name of its own and tzlocal is not installed
# in this env. Common client-install zones; anything else -> fixed offset.
_WINDOWS_TZ_TO_IANA = {
    "eastern standard time": "America/New_York",
    "central standard time": "America/Chicago",
    "mountain standard time": "America/Denver",
    "us mountain standard time": "America/Phoenix",
    "pacific standard time": "America/Los_Angeles",
    "alaskan standard time": "America/Anchorage",
    "hawaiian standard time": "Pacific/Honolulu",
    "atlantic standard time": "America/Halifax",
    "newfoundland standard time": "America/St_Johns",
    "utc": "UTC", "coordinated universal time": "UTC",
    "gmt standard time": "Europe/London",
    "w. europe standard time": "Europe/Berlin",
    "romance standard time": "Europe/Paris",
    "central europe standard time": "Europe/Budapest",
    "central european standard time": "Europe/Warsaw",
    "e. europe standard time": "Europe/Chisinau",
    "fle standard time": "Europe/Helsinki",
    "gtb standard time": "Europe/Athens",
    "russian standard time": "Europe/Moscow",
    "india standard time": "Asia/Kolkata",
    "arabian standard time": "Asia/Dubai",
    "israel standard time": "Asia/Jerusalem",
    "south africa standard time": "Africa/Johannesburg",
    "china standard time": "Asia/Shanghai",
    "tokyo standard time": "Asia/Tokyo",
    "korea standard time": "Asia/Seoul",
    "singapore standard time": "Asia/Singapore",
    "aus eastern standard time": "Australia/Sydney",
    "aus central standard time": "Australia/Adelaide",
    "w. australia standard time": "Australia/Perth",
    "new zealand standard time": "Pacific/Auckland",
    "sa pacific standard time": "America/Bogota",
    "e. south america standard time": "America/Sao_Paulo",
    "argentina standard time": "America/Argentina/Buenos_Aires",
    "central standard time (mexico)": "America/Mexico_City",
}


def _server_offset_label():
    import datetime as _dt
    off = _dt.datetime.now().astimezone().utcoffset() or _dt.timedelta()
    mins = int(off.total_seconds() // 60)
    return f"UTC{'+' if mins >= 0 else '-'}{abs(mins) // 60:02d}:{abs(mins) % 60:02d}"


def server_zone_label():
    """The zone used when neither the user's browser nor an explicit name says:
    AGENT_DEFAULT_TZ (if it is a usable zone) > the server's Windows zone
    mapped to an IANA name (DST-aware) > the server's CURRENT fixed offset
    'UTC±HH:MM' (last resort — DST-frozen until the job is re-saved)."""
    env = os.getenv("AGENT_DEFAULT_TZ", "").strip()
    if env:
        c = _zone_canonical(env)
        if c:
            return c
    import time as _t
    name = (_t.tzname[0] if getattr(_t, "tzname", None) else "").strip().lower()
    iana = _WINDOWS_TZ_TO_IANA.get(name)
    if iana:
        return iana
    return _server_offset_label()


def _resolve_tz_offset_minutes(tz_label):
    """Return (offset_minutes_east_of_utc, canonical_label) for a timezone.

    Accepts IANA names, common US aliases, the platform's abbreviation table
    (schedule_tz: BST, CET, AEST, ...), 'UTC', '+HH:MM'/'-HH:MM'. Empty ->
    server_zone_label(). Raises ValueError on anything unrecognized or
    ambiguous (fail closed — never guess a timezone); the canonical label is
    always one the engine's schedule_tz.to_tzinfo() accepts.
    """
    import re
    import datetime as _dt
    from zoneinfo import ZoneInfo

    label = str(tz_label or "").strip()
    if not label:
        label = server_zone_label()
    m = re.fullmatch(r"(?:UTC)?([+-])(\d{1,2})(?::?(\d{2}))?", label, re.I)
    if m:
        mins = int(m.group(2)) * 60 + int(m.group(3) or 0)
        mins = -mins if m.group(1) == "-" else mins
        return mins, f"UTC{m.group(1)}{abs(mins) // 60:02d}:{abs(mins) % 60:02d}"
    name = _TZ_ALIASES.get(label.lower())
    if not name:
        # the platform's (international) abbreviation table — shared with CC
        try:
            import schedule_tz as _stz
            key = label.upper().replace(" ", "")
            if key in getattr(_stz, "_AMBIGUOUS", {}):
                _iana, alts = _stz._AMBIGUOUS[key]
                raise ValueError(
                    f"'{tz_label}' is ambiguous — say which you mean: "
                    + ", ".join(alts))
            name = getattr(_stz, "_ABBREV_TO_IANA", {}).get(key)
        except ImportError:
            name = None
    name = name or label
    try:
        off = _dt.datetime.now(ZoneInfo(name)).utcoffset() or _dt.timedelta()
    except Exception:
        raise ValueError(
            f"unknown timezone '{tz_label}' — use an IANA name like "
            "America/New_York, an alias like Eastern/Central/Mountain/Pacific, "
            "'UTC', or an offset like -05:00")
    return int(off.total_seconds() // 60), name


def _zone_canonical(label):
    """Canonical engine label for a zone string, or None if it is not usable."""
    try:
        return _resolve_tz_offset_minutes(label)[1]
    except ValueError:
        return None


def _engine_tz_label(label, offset_minutes=0):
    """Kept for callers that still pass the pre-2026-08-22 'server-local (…)'
    label; canonical labels pass through unchanged."""
    if str(label).startswith("server-local"):
        sign = "+" if offset_minutes >= 0 else "-"
        return f"UTC{sign}{abs(offset_minutes) // 60:02d}:{abs(offset_minutes) % 60:02d}"
    return str(label)


def default_zone_label(user):
    """(zone, source) to assume when the user names no zone: their BROWSER zone
    (main.py stamps user['browser_timezone'] from the UI) > AGENT_DEFAULT_TZ >
    the server's zone. `source` is one of 'browser' | 'AGENT_DEFAULT_TZ' |
    'server' so the confirmation can say honestly which was assumed."""
    bz = str((user or {}).get("browser_timezone") or "").strip()
    if bz:
        c = _zone_canonical(bz)
        if c:
            return c, "browser"
    env = os.getenv("AGENT_DEFAULT_TZ", "").strip()
    if env and _zone_canonical(env):
        return _zone_canonical(env), "AGENT_DEFAULT_TZ"
    return server_zone_label(), "server"


_ZONE_SOURCE_TEXT = {
    "explicit": "the zone you named",
    "browser": "your browser's timezone, since you named none",
    "AGENT_DEFAULT_TZ": "this install's default timezone (AGENT_DEFAULT_TZ), since "
                        "you named none and your browser's zone is not known here",
    "server": "the server's timezone, since you named none and your browser's "
              "zone is not known here",
}


def _zone_tzinfo(zone_label):
    """tzinfo for a canonical label: IANA name, 'UTC', or 'UTC±HH:MM'."""
    import re
    import datetime as _dt
    from zoneinfo import ZoneInfo
    z = str(zone_label or "UTC")
    m = re.fullmatch(r"UTC([+-])(\d{2}):(\d{2})", z)
    if m:
        delta = _dt.timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
        return _dt.timezone(delta if m.group(1) == "+" else -delta)
    try:
        return ZoneInfo(z)
    except Exception:
        return _dt.timezone.utc


def fmt_local(dt_utc, zone_label, fmt="%Y-%m-%d %H:%M"):
    """Render a naive-UTC (or aware) datetime as wall-clock text in `zone_label`
    with the zone's abbreviation ('2026-08-22 21:45 EDT', '… UTC-04:00')."""
    import re
    import datetime as _dt
    if dt_utc is None:
        return ""
    aware = dt_utc if dt_utc.tzinfo else dt_utc.replace(tzinfo=_dt.timezone.utc)
    local = aware.astimezone(_zone_tzinfo(zone_label))
    z = str(zone_label or "UTC")
    if re.fullmatch(r"UTC[+-]\d{2}:\d{2}", z) or z == "UTC":
        abbr = z
    else:
        abbr = local.tzname() or z
    return f"{local.strftime(fmt)} {abbr}"


def local_to_utc(naive_local, zone_label):
    """Naive wall-clock datetime in `zone_label` -> naive UTC datetime."""
    import datetime as _dt
    return (naive_local.replace(tzinfo=_zone_tzinfo(zone_label))
            .astimezone(_dt.timezone.utc).replace(tzinfo=None))


def now_line(zone_label):
    """One line prepended to every turn (main.py): the current wall-clock time
    in the user's zone + the zone itself, so the model can do time arithmetic
    and state times in the user's terms. chat_history.replay strips it."""
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    return (f"[Context: now {fmt_local(now, zone_label, '%A %Y-%m-%d %H:%M')} "
            f"({zone_label}) — the user's timezone. Times the user says are in "
            "this zone unless they name another; state every time back to them "
            "in it.]")


# --- bounded recurrence (2026-08-22) -----------------------------------------
# "Every 10 minutes for the next hour" is ONE job the ENGINE stops on its own:
#   * interval_minutes        -> IntervalTrigger(minutes=N) (engine-native)
#   * for_minutes             -> ScheduleDefinitions.EndDate -> trigger end_date
#   * occurrences             -> ScheduleDefinitions.MaxRuns (engine deactivates
#                                the row once CurrentRuns reaches it)
# Interval bounds set BOTH (end_date is the hard time stop; max_runs gives the
# exact count + a clean deactivation) — never a fan-out of one-shot jobs.

_BOUND_SLACK_SECONDS = 30   # end_date lands just past the last planned fire

_RUN_AT_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M",
                   "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %I:%M %p", "%Y-%m-%d %I%p")


def _build_schedule(args, now=None, default_tz=None, default_src="server"):
    """Turn the tool's cadence + bound arguments into the scheduler's `schedule`
    dict plus the facts the success text reports. Raises ValueError with an
    honest, user-facing reason — it never guesses a cadence or a zone.

    default_tz/default_src: the zone to assume when `timezone` is not given
    (main.py stamps the browser zone; see default_zone_label).

    Returns a dict: schedule, kind ('one_shot'|'cron'|'interval'), params
    (extra provenance job parameters), interval_seconds, first_run_at (UTC),
    end_at (UTC), max_runs, expected_runs, tz_label (cron zone), local_cron,
    display_tz (zone for stating times), zone_src, note."""
    import datetime as _dt

    now = now or _dt.datetime.utcnow()

    def _fmt(d):
        return d.strftime("%Y-%m-%d %H:%M:%S")

    def _int(key):
        v = args.get(key)
        if v in (None, "", False):
            return None
        try:
            v = int(v)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a whole number of minutes/runs")
        if v == 0:
            return None          # absent (mirrors the tool's truthiness rule)
        if v < 1:
            raise ValueError(f"{key} must be at least 1")
        return v

    run_in = _int("run_in_minutes")
    run_at = str(args.get("run_at") or "").strip()
    every_minutes, every_hours, every_days = (_int("every_minutes"),
                                              _int("every_hours"), _int("every_days"))
    for_minutes, occurrences = _int("for_minutes"), _int("occurrences")
    cron = str(args.get("cron_expression") or "").strip()

    # the zone every time is interpreted and stated in
    explicit_tz = str(args.get("timezone") or "").strip()
    if explicit_tz:
        _tz_mins, zone = _resolve_tz_offset_minutes(explicit_tz)
        zone_src = "explicit"
    elif default_tz:
        zone, zone_src = default_tz, (default_src or "server")
    else:
        zone, zone_src = server_zone_label(), "server"

    out = {"schedule": None, "kind": None, "params": {}, "interval_seconds": None,
           "first_run_at": None, "end_at": None, "max_runs": None,
           "expected_runs": None, "tz_label": None, "local_cron": None,
           "display_tz": zone, "zone_src": zone_src, "run_at_local": None,
           "note": ""}

    if run_in and run_at:
        raise ValueError("give run_in_minutes (relative) OR run_at (an absolute "
                         "local date-time), not both")
    if run_in or run_at:
        if for_minutes or occurrences:
            raise ValueError(
                "a one-shot fires exactly once, so for_minutes/occurrences do "
                "not apply — for a bounded repeat use every_minutes (or "
                "every_hours/every_days) together with for_minutes or occurrences")
        if run_in:
            fire_at = now + _dt.timedelta(minutes=run_in)
        else:
            local = None
            for f in _RUN_AT_FORMATS:
                try:
                    local = _dt.datetime.strptime(run_at, f)
                    break
                except ValueError:
                    continue
            if local is None:
                raise ValueError(f"run_at '{run_at}' is not a date-time I can read "
                                 "— use 'YYYY-MM-DD HH:MM' (24h) in the user's zone")
            fire_at = local_to_utc(local, zone)
            if fire_at < now + _dt.timedelta(seconds=30):
                raise ValueError(
                    f"run_at {local.strftime('%Y-%m-%d %H:%M')} {zone} is already "
                    f"in the past (now is {fmt_local(now, zone)}) — give a future time")
            out["run_at_local"] = (local, zone)
        out.update(kind="one_shot", first_run_at=fire_at, expected_runs=1,
                   schedule={"type": "date", "start_date": _fmt(fire_at)})
        return out

    if cron:
        if len(cron.split()) != 5:
            raise ValueError(f"cron '{cron}' must have 5 fields "
                             "(minute hour day-of-month month day-of-week)")
        out.update(kind="cron", tz_label=zone, local_cron=cron,
                   schedule={"type": "cron", "cron_expression": cron},
                   params={"timezone": zone, "local_cron": cron})
        why = _ZONE_SOURCE_TEXT.get(zone_src, _ZONE_SOURCE_TEXT["server"])
        if zone.startswith("UTC") and zone != "UTC":
            out["note"] = (f" Cron `{cron}` fires at fixed offset {zone} ({why}); "
                           "the offset is frozen now, so a DST change shifts firing "
                           "by an hour until re-saved — name a zone like "
                           "America/New_York to avoid that.")
        else:
            out["note"] = (f" Cron `{cron}` fires in {zone} ({why}) — the engine "
                           "applies that zone at fire time (DST-aware).")
    elif every_minutes or every_hours or every_days:
        # Interval schedules need an anchored start or the engine's re-create
        # loop pushes the next fire forever (CC lesson).
        sched = {"type": "interval", "start_date": _fmt(now)}
        secs = 0
        if every_minutes:
            sched["interval_minutes"] = every_minutes
            secs += every_minutes * 60
        if every_hours:
            sched["interval_hours"] = every_hours
            secs += every_hours * 3600
        if every_days:
            sched["interval_days"] = every_days
            secs += every_days * 86400
        out.update(kind="interval", schedule=sched, interval_seconds=secs,
                   first_run_at=now + _dt.timedelta(seconds=secs))
    else:
        raise ValueError("Provide cron_expression, every_minutes/every_hours/"
                         "every_days, run_in_minutes, or run_at for a one-shot.")

    if for_minutes or occurrences:
        end_at, max_runs = None, None
        if for_minutes:
            end_at = now + _dt.timedelta(minutes=for_minutes)
            if out["kind"] == "interval":
                count = (for_minutes * 60) // out["interval_seconds"]
                if count < 1:
                    raise ValueError(
                        f"for_minutes={for_minutes} is shorter than the interval "
                        f"({out['interval_seconds'] // 60} min) — nothing would "
                        "ever fire; make the window at least one interval long")
                max_runs = count
        if occurrences:
            max_runs = occurrences if max_runs is None else min(max_runs, occurrences)
            if out["kind"] == "interval":
                by_count = now + _dt.timedelta(
                    seconds=occurrences * out["interval_seconds"])
                end_at = by_count if end_at is None else min(end_at, by_count)
        if end_at is not None:
            out["schedule"]["end_date"] = _fmt(
                end_at + _dt.timedelta(seconds=_BOUND_SLACK_SECONDS))
            out["end_at"] = end_at
        if max_runs is not None:
            out["schedule"]["max_runs"] = max_runs
            out["max_runs"] = max_runs
        out["expected_runs"] = max_runs   # None: cron + for_minutes (uncounted)
    return out


def _bound_was_recorded(plan, readback_schedules):
    """Read-back honesty for bounds: the ACTIVE schedule row must carry the
    end_date / max_runs the plan asked for. If the engine dropped them the job
    would run forever — the caller deletes it and reports NOT scheduled."""
    if not (plan.get("end_at") or plan.get("max_runs")):
        return True
    rows = [s for s in (readback_schedules or []) if s.get("is_active")]
    if not rows:
        return False
    row = rows[0]
    ok_end = bool(row.get("end_date")) if plan.get("end_at") else True
    ok_max = (row.get("max_runs") is not None) if plan.get("max_runs") else True
    return ok_end and ok_max


def _cadence_text(plan):
    """Human-readable cadence for the success text."""
    if plan["kind"] == "cron":
        return f"cron `{plan['local_cron']}` in {plan['tz_label']}"
    secs = plan["interval_seconds"] or 0
    if secs % 86400 == 0:
        n = secs // 86400
        return f"every {n} day{'s' if n != 1 else ''}"
    if secs % 3600 == 0:
        n = secs // 3600
        return f"every {n} hour{'s' if n != 1 else ''}"
    n = max(secs // 60, 1)
    return f"every {n} minute{'s' if n != 1 else ''}"


def _bound_text(plan, now):
    """'first run …' / 'stops by …' / 'stops after N runs' phrasing in the
    user's zone, honest about approximation (the engine polls ~every minute;
    the last fire can land a little after)."""
    zone = plan.get("display_tz") or "UTC"
    parts = []
    if plan["kind"] == "interval" and plan["first_run_at"]:
        mins = max(int((plan["first_run_at"] - now).total_seconds() // 60), 1)
        parts.append(f"first run ~{mins} min from now "
                     f"(≈{fmt_local(plan['first_run_at'], zone, '%H:%M')})")
    if plan["end_at"] is not None:
        span = int((plan["end_at"] - now).total_seconds() // 60)
        parts.append(f"stops by ≈{fmt_local(plan['end_at'], zone, '%H:%M')} "
                     f"(~{span} min from now)")
    elif plan["max_runs"]:
        parts.append(f"stops after {plan['max_runs']} run(s)")
    if plan["end_at"] is not None and plan["expected_runs"]:
        parts.append(f"about {plan['expected_runs']} run(s) in total")
    return ", ".join(parts)


@tool(
    "schedule_agent_task",
    "Schedule a HEADLESS agent task — recurring, BOUNDED-recurring, or one-shot: "
    "at each firing an agent session runs the given prompt AS the current user "
    "and reports its result into their My Work queue (and, when scheduled from "
    "a chat, appends it to THAT conversation). Cadence shapes: "
    "(1) RECURRING forever — cron_expression (+timezone) OR every_minutes/"
    "every_hours/every_days. (2) BOUNDED REPEAT — the SAME interval params "
    "PLUS a bound: for_minutes (stop after this many minutes) or occurrences "
    "(stop after this many runs): 'every 10 minutes for the next hour' = "
    "every_minutes=10, for_minutes=60; 'every 5 minutes, 12 times' = "
    "every_minutes=5, occurrences=12. This is ONE job the engine stops on its "
    "own — never schedule an unbounded job for a bounded ask and never fan out "
    "one-shots. (3) ONE-SHOT — relative: run_in_minutes ('in 2 minutes', 'in an "
    "hour'); absolute: run_at = 'YYYY-MM-DD HH:MM' in the user's zone ('at 3pm', "
    "'tomorrow 9am' — compute the date/time from the [Context: now …] line). "
    "TIMEZONE: times the user says are in THEIR zone — the [Context] line's zone "
    "(their browser) — unless they name another; pass `timezone` only when they "
    "name one. The engine applies the zone at fire time (DST-aware). Always "
    "state times back in the user's zone (the tool's text already does). The "
    "engine polls about every minute, so timing is minute-granular. For purely "
    "mechanical repetition prefer an automation (zero tokens per run). Report "
    "ONLY the ids and the cadence/bound/zone facts this returns.",
    {
        "type": "object",
        "properties": {
            "task_prompt": {"type": "string",
                            "description": "The full instruction the headless "
                                           "session will run each time"},
            "name": {"type": "string", "description": "Short job name"},
            "cron_expression": {"type": "string",
                                "description": "5-field cron in the LOCAL "
                                               "timezone given by `timezone` "
                                               "(default: the user's zone)"},
            "timezone": {"type": "string",
                         "description": "Only when the user NAMES a zone: IANA "
                                        "(America/New_York), alias (Eastern), "
                                        "'UTC', or '-05:00'. Omit to use the "
                                        "user's own zone (browser)."},
            "every_minutes": {"type": "integer",
                              "description": "Interval in minutes (min 1). "
                                             "Combine with for_minutes or "
                                             "occurrences for a bounded repeat."},
            "every_hours": {"type": "integer"},
            "every_days": {"type": "integer"},
            "for_minutes": {"type": "integer",
                            "description": "BOUND: stop firing this many minutes "
                                           "from now ('for the next hour' = 60). "
                                           "Must be at least one interval long."},
            "occurrences": {"type": "integer",
                            "description": "BOUND: stop after this many runs "
                                           "('12 times' = 12)."},
            "run_in_minutes": {"type": "integer",
                               "description": "One-shot, RELATIVE: fire once this "
                                              "many minutes from now (min 1)."},
            "run_at": {"type": "string",
                       "description": "One-shot, ABSOLUTE: 'YYYY-MM-DD HH:MM' "
                                      "(24h) wall-clock time in the user's zone "
                                      "(or `timezone`). Must be in the future."},
        },
        "required": ["task_prompt", "name"],
        "additionalProperties": False,
    },
)
async def schedule_agent_task(args: dict[str, Any]) -> dict[str, Any]:
    import datetime as _dt
    import httpx
    from platform_tools import _headers
    from agent_config import get_base_url, defer_to_chat_enabled

    user = CURRENT_USER.get()
    if int(user.get("role") or 0) < 2 and os.getenv(
            "AGENT_BUILD_ALLOW_ALL_USERS", "false").lower() != "true":
        return _text("Scheduling agent tasks requires a Developer role.",
                     is_error=True)
    now = _dt.datetime.utcnow()
    dz, dsrc = default_zone_label(user)
    try:
        plan = _build_schedule(args, now=now, default_tz=dz, default_src=dsrc)
    except ValueError as e:
        return _text(f"Nothing was scheduled: {e}", is_error=True)
    schedule = plan["schedule"]
    one_shot = plan["kind"] == "one_shot"
    zone = plan["display_tz"]

    # Deferred-results-to-chat (Level 1): remember the conversation this was
    # asked from so each firing can append its result there. A headless run
    # only carries a chat id when it is itself a resumed chat (chaining).
    chat_sid = (user.get("chat_session_id")
                or (user.get("session_id") if user.get("mode") != "headless" else None))
    chat_sid = str(chat_sid or "").strip() or None

    body = {
        "name": f"Agent: {str(args['name']).strip()[:80]}",
        "type": "agent_session",
        # string "0": the route's presence check treats int 0 as missing
        "target_id": "0",
        "description": str(args["task_prompt"])[:400],
        "created_by": str(user.get("username") or "agent"),
        "is_active": True,
        "parameters": {
            "prompt": {"value": str(args["task_prompt"]), "type": "string"},
            "user_id": {"value": str(int(user.get("user_id") or 0)), "type": "string"},
            "role": {"value": str(int(user.get("role") or 2)), "type": "string"},
            "username": {"value": str(user.get("username") or ""), "type": "string"},
            # the zone the user thinks in: headless runs state times in it and
            # default any chained schedule to it
            "user_timezone": {"value": zone, "type": "string"},
        },
        "schedule": schedule,
    }
    for k, v in (plan.get("params") or {}).items():
        # provenance: what the user meant (cron + zone), and the engine reads
        # `timezone` to fire the cron in that zone
        body["parameters"][k] = {"value": str(v), "type": "string"}
    if chat_sid:
        body["parameters"]["session_id"] = {"value": chat_sid, "type": "string"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{get_base_url()}/api/scheduler/jobs",
                                  json=body, headers=_headers())
            data = r.json() if r.status_code < 500 else {}
            if r.status_code >= 400 or not data.get("id"):
                return _text(f"Nothing was scheduled (HTTP {r.status_code}: "
                             f"{data.get('error', r.text[:200])}). Do NOT tell "
                             "the user it was scheduled.", is_error=True)
            job_id = data["id"]
            # Read-back: the job + an active schedule row must really exist.
            rb = await client.get(f"{get_base_url()}/api/scheduler/jobs/{job_id}",
                                  headers=_headers())
            rbd = rb.json() if rb.status_code < 400 else {}
            active = any(s.get("is_active") for s in (rbd.get("schedules") or []))
            if not active:
                return _text(f"Job #{job_id} was created but NO active schedule "
                             "row exists — report this as NOT scheduled.",
                             is_error=True)
            if not _bound_was_recorded(plan, rbd.get("schedules")):
                # Fail closed: a bounded ask must never leave an unbounded job.
                try:
                    await client.delete(f"{get_base_url()}/api/scheduler/jobs/{job_id}",
                                        headers=_headers())
                except Exception:
                    pass
                return _text(f"Job #{job_id} was created but the engine did NOT "
                             "record the requested bound (end_date/max_runs) — "
                             "it was removed so nothing runs forever. Report "
                             "this as NOT scheduled.", is_error=True)
    except Exception as e:
        return _text(f"Scheduling failed: {e}", is_error=True)

    deliver = ("lands its result as an FYI in their My Work"
               + (" and appends it to this conversation (open it from history "
                  "if you have moved on)" if chat_sid and defer_to_chat_enabled()
                  else ""))
    if one_shot:
        if plan.get("run_at_local"):
            local, z = plan["run_at_local"]
            when = (f"at {fmt_local(plan['first_run_at'], z)} ({z}; "
                    f"≈{max(int((plan['first_run_at'] - now).total_seconds() // 60), 1)} "
                    "minute(s) from now)")
        else:
            when = (f"in about {max(int(args.get('run_in_minutes') or 1), 1)} "
                    f"minute(s) (≈{fmt_local(plan['first_run_at'], zone, '%H:%M')})")
        return _text(f"One-shot task '{body['name']}' scheduled (job #{job_id}, "
                     f"verified active by read-back). It fires ONCE {when} — the "
                     "engine polls ~every minute — runs as "
                     f"{user.get('username')}, and {deliver}.")
    bound = _bound_text(plan, now)
    if plan["end_at"] is not None or plan["max_runs"]:
        return _text(f"Bounded headless agent task '{body['name']}' scheduled "
                     f"(job #{job_id}, verified active by read-back — the bound "
                     f"is recorded on the schedule): {_cadence_text(plan)}, "
                     f"{bound}; the engine stops it on its own. Each firing runs "
                     f"as {user.get('username')} and {deliver}.")
    return _text(f"Scheduled headless agent task '{body['name']}' (job #{job_id}, "
                 f"verified active by read-back): {_cadence_text(plan)}"
                 + (f", {bound}" if bound else "")
                 + f". Each firing runs as {user.get('username')} and {deliver}. "
                 "The engine picks it up on its next poll." + plan["note"])


@tool(
    "save_skill",
    "Save procedural knowledge as a skill so future sessions start from "
    "know-how instead of rediscovery. Use AFTER solving something non-obvious: "
    "a process, a data model's quirks, a client convention. Scopes: 'user' "
    "(private, default), 'group' (share with one of the user's groups — ask "
    "the user to confirm first, and pass their group_id), 'tenant' (everyone — "
    "this only FILES A REQUEST; an admin must approve it in My Work). Write "
    "the description as a trigger ('use when...'). Record procedure and "
    "gotchas, but tell future sessions to verify current facts with discovery "
    "tools — never freeze schema or values as truth.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "kebab-case, e.g. month-end-close"},
            "description": {"type": "string", "description": "'Use when …' trigger line"},
            "content": {"type": "string", "description": "The skill body (markdown)"},
            "scope": {"type": "string", "enum": ["user", "group", "tenant"]},
            "group_id": {"type": "integer", "description": "Required for scope=group"},
        },
        "required": ["name", "description", "content"],
        "additionalProperties": False,
    },
)
async def save_skill(args: dict[str, Any]) -> dict[str, Any]:
    import skills_mount
    user = CURRENT_USER.get()
    uid = int(user.get("user_id") or 0)
    scope = str(args.get("scope") or "user")
    name = str(args["name"]).strip().lower()
    if not skills_mount.valid_name(name):
        return _text("Skill name must be kebab-case (a-z, 0-9, '-').", is_error=True)

    if scope == "tenant":
        item = workitem_store.create_item(
            "approve_deny", f"Promote skill '{name}' to tenant",
            summary=(f"Requested by {user.get('username')}. Description: "
                     f"{args['description']}\n\n--- SKILL.md ---\n"
                     + str(args["content"])[:1500]),
            payload={"kind": "skill_promotion", "name": name,
                     "description": str(args["description"]),
                     "content": str(args["content"]),
                     "requested_by": user.get("username")},
            from_kind="agent_session", from_ref=str(user.get("username") or ""),
            created_by=str(user.get("username") or "agent"), priority=0)
        return _text(f"Tenant promotion requested — approval item "
                     f"{item['work_item_id']} is now in My Work (admin approval "
                     "required; the skill is NOT shared yet).")

    if scope == "group":
        gid = int(args.get("group_id") or 0)
        if not gid:
            return _text("scope=group needs group_id (ask the user which of "
                         "their groups).", is_error=True)
        import readthrough
        if gid not in readthrough.user_group_ids(uid):
            return _text(f"User {uid} is not a member of group {gid} — not saved.",
                         is_error=True)
        path = skills_mount.write_skill("group", name, args["description"],
                                        args["content"], group_id=gid)
        return _text(f"Skill '{name}' saved to group {gid} ({path}). Members' "
                     "future sessions will load it when relevant.")

    path = skills_mount.write_skill("user", name, args["description"],
                                    args["content"], user_id=uid)
    return _text(f"Skill '{name}' saved to your private scope ({path}). Your "
                 "future sessions will load it when relevant.")


@tool(
    "list_skills",
    "List the skills the current user's sessions load: product + tenant + "
    "their groups + private.",
    {},
)
async def list_skills_tool(args: dict[str, Any]) -> dict[str, Any]:
    import skills_mount
    import readthrough
    user = CURRENT_USER.get()
    uid = int(user.get("user_id") or 0)
    skills = skills_mount.list_skills(uid, readthrough.user_group_ids(uid))
    if not skills:
        return _text("No skills exist yet in any scope.")
    lines = []
    for s in skills:
        scope = s["scope"] + (f" {s['group_id']}" if s.get("group_id") else "")
        lines.append(f"- [{scope}] {s['name']} — {s['description'][:100]}")
    return _text(f"Skills ({len(skills)}):\n" + "\n".join(lines))


def _refresh_summary(tiles: list) -> str:
    """How the refresh ACTUALLY went, in one line.

    The model never sees tile data, which is what stops it inventing numbers —
    but it also means it cannot tell a live dashboard from four stale tiles. On
    the first live run it wrote "the tiles carry the current figures" about a
    board where every tile had failed and was serving 4-day-old cache. The
    rendered email was honest; the covering note was not. So the tool reports
    freshness explicitly and the model is told to pass it on.
    """
    fresh = sum(1 for t in tiles if not t.get("error"))
    stale = sum(1 for t in tiles if t.get("error") and (t.get("cache") or {}).get("rows") is not None)
    dead = len(tiles) - fresh - stale
    if fresh == len(tiles):
        return f"all {fresh} tiles refreshed live"
    bits = [f"{fresh} of {len(tiles)} tiles refreshed live"]
    if stale:
        bits.append(f"{stale} could NOT refresh and show older cached values")
    if dead:
        bits.append(f"{dead} failed with no data at all")
    # Carry a real reason, not just a count: "0 of 6 refreshed" in a scheduler
    # execution record is a mystery a week later, and this is the only place
    # that still has the tile errors in hand.
    why = next((str(t.get("error")) for t in tiles if t.get("error")), "")
    if why:
        bits.append(f"first error: {' '.join(why.split())[:200]}")
    return "; ".join(bits)


async def render_view_for_email(view_name: str, scope: str, group_id: int,
                                principal: dict) -> tuple:
    """Resolve + refresh a View server-side and render it for email.
    Returns (html, text, error, refresh_summary).

    NOT a tool — a plain helper, and it must stay ABOVE the @tool block below:
    a bare function sitting between a decorator and its intended target silently
    steals the decoration, leaving the real tool undecorated and crashing
    create_sdk_mcp_server at import ('function' object has no attribute 'name').

    The MODEL never sees the tile data: numbers travel from the governed
    refresh path straight into the message, so they cannot be paraphrased,
    rounded, or invented. Visibility is enforced by views_store.get() — a
    guessed name resolves to nothing, not to data.

    Shared with the approval path in main.py, which re-runs this at SEND time
    so an approved email carries current numbers rather than draft-time ones.
    """
    import readthrough
    import views_store
    import email_render
    from views_tools import run_view

    uid = int(principal.get("user_id") or 0)
    view = views_store.get(str(view_name).strip(), uid,
                           readthrough.user_group_ids(uid), str(scope or ""),
                           int(group_id or 0))
    if not view:
        return "", "", (f"No saved View named '{view_name}' is visible to this "
                        "user — nothing was drafted or sent."), ""
    # Automation tiles run through the governed seam AS this principal.
    CURRENT_USER.set(principal)
    result = await run_view(view)
    html, text = email_render.render_view(
        result, base_url=os.getenv("APP_PUBLIC_BASE_URL", ""))
    return html, text, None, _refresh_summary(result.get("tiles") or [])


@tool(
    "draft_email_reply",
    "Send/draft an outbound email FROM the current user's personal agent "
    "address, honoring their address settings: with auto-send OFF (default) "
    "it files an EDITABLE approval into My Work and NOTHING sends until they "
    "approve; with auto-send ON it sends immediately via the platform's "
    "governed transport and reports so. Outbound can be disabled entirely on "
    "the Email screen. Report exactly what happened — never claim SENT unless "
    "the result says so. Requires an active agent email address. "
    "FORMATTING: write `body` as plain text with light markdown — '# ' / '## ' "
    "headings, '- ' bullets, '1. ' numbered lists, **bold**, `code`, "
    "[links](https://…), and | pipe | tables |. The service renders that to "
    "styled HTML and sends the text you wrote as the plain-text alternative, "
    "so write it to read well BOTH ways. Do NOT write raw HTML. "
    "EMBED A DASHBOARD: pass view_name to append a saved View's live tiles to "
    "the email. The View is refreshed and rendered BY THE SERVICE at send time "
    "— you never see or retype the numbers, so never restate them in `body`; "
    "write the covering note and let the tiles carry the data. A refresh can "
    "PARTIALLY FAIL, in which case those tiles show older cached values (the "
    "email labels them). The tool result tells you exactly how many refreshed — "
    "never call the figures 'current' or 'live' unless it says all tiles did.",
    {
        "type": "object",
        "properties": {
            "to": {"type": "array", "items": {"type": "string"},
                   "description": "Recipient addresses"},
            "subject": {"type": "string"},
            "view_name": {"type": "string",
                          "description": "Optional: a saved View to refresh and "
                                         "embed as a dashboard in the email"},
            "view_scope": {"type": "string",
                           "enum": ["user", "group", "tenant"],
                           "description": "Only when View names collide across scopes"},
            "view_group_id": {"type": "integer"},
            "body": {"type": "string",
                     "description": "Draft body: plain text with light markdown "
                                    "(see FORMATTING above). Never raw HTML."},
            "rich": {"type": "boolean",
                     "description": "Default true — send a formatted HTML version "
                                    "alongside the plain text. Set false only for "
                                    "a deliberately plain-text-only message."},
            "context": {"type": "string",
                        "description": "Optional: what this replies to (shown "
                                       "to the approver)"},
        },
        "required": ["to", "subject", "body"],
        "additionalProperties": False,
    },
)
async def draft_email_reply(args: dict[str, Any]) -> dict[str, Any]:
    import email_store
    user = CURRENT_USER.get()
    uid = int(user.get("user_id") or 0)
    addr = email_store.get_address(uid)
    if not addr or not addr.get("is_active"):
        return _text("This user has no active agent email address — nothing "
                     "was drafted. They can create one on the Email screen.",
                     is_error=True)
    if not addr.get("outbound_enabled", 1):
        return _text("Outbound email is DISABLED for this address (Email "
                     "screen setting) — nothing was drafted or sent.",
                     is_error=True)
    to = [str(a).strip() for a in (args.get("to") or []) if str(a).strip()]
    if not to:
        return _text("At least one recipient is required.", is_error=True)
    subject = str(args["subject"]).strip()[:300]
    body = str(args["body"])
    # HTML is opt-OUT per message and kill-switchable install-wide
    # (AGENT_EMAIL_HTML=false). The markdown-ish body is ALWAYS sent as the
    # plain-text alternative, so a client that can't render HTML loses nothing.
    import email_render
    rich = bool(args.get("rich", True)) and email_render.html_enabled()

    # Optional embedded dashboard (Phase 2). Rendered NOW for the auto-send
    # path; the approval path stores the reference and re-renders at send.
    view_html = view_text = ""
    view_ref = None
    if str(args.get("view_name") or "").strip():
        principal = {"user_id": uid, "role": int(user.get("role") or 2),
                     "username": str(user.get("username") or ""),
                     "name": str(user.get("name") or "")}
        view_html, view_text, view_err, view_status = await render_view_for_email(
            str(args["view_name"]), str(args.get("view_scope") or ""),
            int(args.get("view_group_id") or 0), principal)
        if view_err:
            return _text(view_err, is_error=True)
        view_ref = {"name": str(args["view_name"]).strip(),
                    "scope": str(args.get("view_scope") or ""),
                    "group_id": int(args.get("view_group_id") or 0),
                    # Stored principal, same idea as a view_refresh JSS job: the
                    # approval may be actioned by an admin who cannot see the
                    # drafter's private View, so the re-run uses THIS envelope.
                    "as_user": principal}

    plain_body = body + (("\n\n" + view_text) if view_text else "")

    if addr.get("auto_send"):
        # AUTO-SEND (James 2026-08-09, opt-in per address): send now through
        # the same cloud transport the approval path uses; leave a closed
        # FYI audit item. A failed send falls back to the approval queue —
        # never silently dropped.
        import email_client
        result = await email_client.send_reply(
            to, subject, plain_body, addr["email_address"],
            f"{addr.get('prefix', 'agent')} via The Agent",
            html_body=email_render.render_email_with_view(
                body, view_html, title=subject) if rich else None)
        if result.get("success"):
            workitem_store.create_item(
                "acknowledge", f"✉ Auto-sent: {subject or '(no subject)'}",
                summary=(f"To: {', '.join(to)}\nFrom: {addr['email_address']}\n"
                         f"(auto-send is ON for this address)"
                         + (f"\nEmbedded View: {view_ref['name']}" if view_ref else "")
                         + f"\n\n{body[:1500]}"),
                payload={"kind": "agent_email_autosent", "to": to,
                         "subject": subject, "from_user": uid},
                addressed_user=uid, from_kind="agent_email",
                from_ref=addr["email_address"],
                created_by=str(user.get("username") or "agent"))
            return _text(f"Email SENT to {', '.join(to)} from "
                         f"{addr['email_address']} (auto-send is enabled for "
                         "this address; an FYI audit item was added to "
                         "My Work)."
                         + (f" The View '{view_ref['name']}' was embedded as a "
                            f"dashboard: {view_status}. You did not see its "
                            "numbers, so do not describe them — and if any tile "
                            "did not refresh, say that plainly rather than "
                            "calling the figures current."
                            if view_ref else ""))
        logger_note = str(result.get("error", result))[:200]
        # fall through to the approval path so the message isn't lost
        fallback_note = (f"Auto-send FAILED ({logger_note}) — filed for "
                         "manual approval instead. ")
    else:
        fallback_note = ""

    item = workitem_store.create_item(
        "edit_and_return", f"Send: {subject or '(no subject)'}",
        summary=(f"To: {', '.join(to)}\nFrom: {addr['email_address']}\n"
                 + (f"Context: {str(args.get('context'))[:400]}\n" if args.get('context') else "")
                 + "\nEdit the body if needed — what you approve is what sends."
                 + ("\nFormatting (headings, lists, tables) is applied to the "
                    "text you approve; the plain text is sent alongside it."
                    if rich else "")
                 + (f"\nThe View '{view_ref['name']}' is embedded below your "
                    "text and is REFRESHED when you approve, so the email "
                    "carries current numbers — not these."
                    if view_ref else "")),
        payload={"kind": "agent_email_reply", "to": to, "subject": subject,
                 "body": body, "from_address": addr["email_address"],
                 "from_user": uid, "rich": rich, "view": view_ref,
                 "context": str(args.get("context") or "")[:500]},
        addressed_user=uid,
        from_kind="agent_email", from_ref=addr["email_address"],
        created_by=str(user.get("username") or "agent"))
    return _text(f"{fallback_note}Draft filed for approval (work item "
                 f"{item['work_item_id']}). It is in My Work now; NOTHING has "
                 "been sent — the user approves (and may edit) the body first."
                 + (f" The View '{view_ref['name']}' will be refreshed and "
                    "embedded when they approve."
                    if view_ref else ""))


@tool(
    "setup_agent_email",
    "Create (or re-activate) the current user's personal agent email address "
    "— WITH THEIR PERMISSION. TWO-STEP: first call WITHOUT confirmed to get "
    "the proposed address; present it, tell them they can pick a different "
    "prefix, and only after they explicitly agree call again with "
    "confirmed=true (and their chosen prefix if they gave one). The address "
    "becomes <prefix>-agent.<tenant>@<domain>; sending stays approval-gated "
    "by default and all options live on the Email screen.",
    {
        "type": "object",
        "properties": {
            "prefix": {"type": "string",
                       "description": "Optional prefix; defaults to their "
                                      "username (sanitized)"},
            "confirmed": {"type": "boolean"},
        },
        "required": [],
        "additionalProperties": False,
    },
)
async def setup_agent_email(args: dict[str, Any]) -> dict[str, Any]:
    import email_store
    import email_client
    user = CURRENT_USER.get()
    uid = int(user.get("user_id") or 0)
    existing = email_store.get_address(uid)
    if existing and existing.get("is_active") and not args.get("prefix"):
        return _text(f"Already set up: {existing['email_address']} is ACTIVE — "
                     "nothing to create. Settings live on the Email screen.")
    info = await email_client.tenant_info()
    if not info:
        return _text("The cloud email service is unreachable, so the address "
                     "suffix can't be resolved — nothing was created. Try "
                     "again shortly.", is_error=True)
    prefix = email_store.sanitize_prefix(
        args.get("prefix")
        or (existing or {}).get("prefix")
        or email_store.sanitize_prefix(user.get("username"))
        or str(uid))
    if not prefix:
        return _text("That prefix has no email-safe characters (a-z, 0-9, "
                     "hyphen) — pick another.", is_error=True)
    address = email_client.compose_address(prefix, info["tenant_id"],
                                           info["domain"])
    if not args.get("confirmed"):
        return _text(f"PROPOSAL (nothing created yet): their agent address "
                     f"would be {address}. Ask the user to confirm — and tell "
                     "them they can choose a different prefix (letters, "
                     "numbers, hyphens). Only call again with confirmed=true "
                     "after they explicitly agree.")
    try:
        row = email_store.upsert_address(
            uid, prefix, address, str(user.get("username") or ""),
            int(user.get("role") or 2), True)
    except ValueError as e:
        return _text(f"Not created: {e} — suggest a different prefix.",
                     is_error=True)
    return _text(f"Done — {row['email_address']} is ACTIVE. Mail sent there "
                 "reaches me as a session run as them, results land in "
                 "My Work, and any replies I draft wait for their approval "
                 "(auto-send and other options are on the Email screen).")


@tool(
    "get_agent_email_status",
    "The QUICK PULSE on the current user's agent email: their address (or "
    "that none exists yet), settings, poller state, and the last few inbound "
    "rows (sender/subject/outcome). Questions like 'did you get any email?' "
    "are answered from THIS — call it and report the activity directly, "
    "without capability disclaimers. For anything deeper — paging or "
    "searching past mail, opening a message's body, reading or saving "
    "attachments — use list_my_email / read_email / list_email_attachments "
    "/ read_attachment / save_attachment.",
    {},
)
async def get_agent_email_status(args: dict[str, Any]) -> dict[str, Any]:
    import email_store
    import email_poller
    import email_client
    user = CURRENT_USER.get()
    uid = int(user.get("user_id") or 0)
    row = email_store.get_address(uid)
    info = await email_client.tenant_info()
    suffix = (f"-agent.{info['tenant_id']}@{info['domain']}" if info
              else "(cloud email service unreachable)")
    lines = []
    if row:
        state = "ENABLED" if row.get("is_active") else "DISABLED"
        lines.append(f"Address: {row['email_address']} ({state})")
        lines.append(
            "Settings: outbound "
            + ("ON" if row.get("outbound_enabled", 1) else "OFF")
            + ", auto-send " + ("ON (replies send immediately)"
                                if row.get("auto_send")
                                else "OFF (replies wait for approval)")
            + (", notify-on-receive → " + row["notification_email"]
               if row.get("notify_on_receive") and row.get("notification_email")
               else "")
            + (f", cooldown {row['cooldown_minutes']}m"
               if row.get("cooldown_minutes") is not None else "")
            + ("; standing reply instructions are set"
               if str(row.get("reply_instructions") or "").strip() else ""))
        recent = email_store.recent(row["email_address"], 5)
        if recent:
            lines.append(f"Recent inbound activity ({len(recent)} shown):")
            for e in recent:
                lines.append(f"  - {e['processed_at'][:16]} [{e['outcome']}] "
                             f"from {e.get('sender') or '?'}: "
                             f"{(e.get('subject') or '(no subject)')[:60]}")
        else:
            lines.append("No inbound mail processed yet.")
        from agent_config import email_tools_enabled
        if email_tools_enabled():
            lines.append("Deeper reads: list_my_email pages/searches the "
                         "whole log (each row's event_id opens with "
                         "read_email); read_attachment / save_attachment "
                         "handle attachments.")
    else:
        default = email_store.sanitize_prefix(user.get("username")) or str(uid)
        lines.append("No agent email address set up yet for this user. OFFER "
                     "TO SET IT UP: with their permission you can create it "
                     f"yourself via setup_agent_email — suggest '{default}"
                     f"{suffix}' and tell them they may pick a different "
                     "prefix. (The Email screen is the manual alternative.)")
    lines.append(f"Inbound poller: {'RUNNING (every ' + str(email_poller.POLL_SECONDS) + 's)' if email_poller.enabled() else 'OFF (AGENT_EMAIL_ENABLED=false — an admin must enable it)'}")
    lines.append("How it works: mail sent to the address becomes a headless "
                 "agent session run as this user; results land in My Work, "
                 "and replies the agent drafts always wait for their approval.")
    return _text("\n".join(lines))


WORK_TOOLS = [raise_work_item, list_my_work, schedule_agent_task,
              save_skill, list_skills_tool, draft_email_reply,
              get_agent_email_status, setup_agent_email]
