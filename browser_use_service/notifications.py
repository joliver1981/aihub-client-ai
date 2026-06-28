"""
notifications.py - best-effort "a portal run needs a human" ping.

The isolated service can't send email itself (no SMTP/DB config), so it asks the MAIN APP over
HTTP to email the run's owner. Fire-and-forget on a thread: a notification failure must never
block or fail the run (the Run Monitor still surfaces the pending takeover regardless).
"""
import asyncio
import logging
import os

log = logging.getLogger("browser_use_service")

_TASKS = set()


def _main_app_base():
    host = os.getenv("HOST", "127.0.0.1")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    return f"http://{host}:{os.getenv('HOST_PORT', '5001')}"


def _post(user_id, run_id, portal):
    import requests
    try:
        requests.post(
            f"{_main_app_base()}/api/portal-workflows/internal/notify-takeover",
            json={"user_id": str(user_id), "run_id": run_id, "portal": portal or "portal"},
            headers={"X-AIHub-Internal": os.getenv("API_KEY", "")},
            timeout=8,
        )
    except Exception as e:
        log.debug("notify_takeover post failed: %s", e)


def notify_takeover(run):
    """Schedule a best-effort takeover email to the run owner. No-op if there's no owner or no
    running loop; never raises."""
    if run is None or getattr(run, "user_id", None) is None:
        return

    async def _go():
        try:
            await asyncio.to_thread(_post, run.user_id, run.run_id, run.portal)
        except Exception:
            pass

    try:
        task = asyncio.create_task(_go())
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
    except RuntimeError:
        pass
