"""
cron_dow.py - translate a crontab DAY-OF-WEEK field from standard cron
numbering (0=Sunday) into day NAMES, which mean the same days everywhere.

Lives at the repo ROOT on purpose (same pattern as schedule_tz.py): it is
imported by BOTH
  * the scheduler engine (job_scheduler.py) - the CONSUMER-side root fix.
    APScheduler's CronTrigger day_of_week numbering is 0=MONDAY, and
    from_crontab does NOT remap standard crontab's 0=SUNDAY input despite its
    name (verified on APScheduler 3.11.0: '0 9 * * 1-5' fires Tue-Sat).
    Nine live schedules were observed a day late, including weekday-named
    automations executing on a Saturday (2026-08-15). Normalizing to names
    at the trigger callsite fixes every stored row without editing data.
  * The Agent's scheduling tools (agent_service/views_tools.py) - the
    PRODUCER-side normalization, so newly stored expressions are readable
    and convention-proof at rest.

Only plain numeric day-of-week fields are translated ('1-5', '1,3', '0', '7').
'*', step syntax ('*/2', '1-5/2') and fields already using names pass through
unchanged: APScheduler's weekday-NAME expression does not support steps, so
translating those could turn a parseable field into a parse error. The
contract is that this function NEVER makes a previously parseable expression
unparseable, and never raises.
"""

# Standard crontab order: index 0 = Sunday (7 is Sunday too).
_CRON_DAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


def normalize_cron_dow(expr):
    """'0 9 * * 1-5' -> '0 9 * * mon-fri' (standard cron: 0 and 7 = Sunday).

    Anything that is not a plain 5-field expression with a purely numeric
    day-of-week field is returned untouched. Never raises.
    """
    try:
        parts = str(expr or "").split()
        if len(parts) != 5:
            return expr
        dow = parts[4]
        if dow == "*" or "/" in dow or any(c.isalpha() for c in dow):
            return expr
        if not all(c.isdigit() or c in ",-" for c in dow):
            return expr

        def _name(token):
            n = int(token)
            return _CRON_DAY_NAMES[0 if n == 7 else n] if 0 <= n <= 7 else token

        out = []
        for chunk in dow.split(","):
            if "-" in chunk:
                lo, _, hi = chunk.partition("-")
                if lo.isdigit() and hi.isdigit():
                    out.append(f"{_name(lo)}-{_name(hi)}")
                else:
                    out.append(chunk)
            elif chunk.isdigit():
                out.append(_name(chunk))
            else:
                out.append(chunk)
        parts[4] = ",".join(out)
        return " ".join(parts)
    except Exception:
        return expr
