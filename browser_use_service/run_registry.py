"""
run_registry.py - process-global registry of IN-FLIGHT portal runs so concurrent control/stream
endpoints (the co-browse live view, the Run Monitor, takeover/resume) can attach to a run that is
otherwise a single long-lived async request.

The run itself (run_portal_fetch / run_workflow) registers a RunState on entry and unregisters in
its finally. FastAPI/uvicorn serves the WS + control endpoints concurrently at the run's await
points, so an operator can watch and (Phase B) drive the same live CDP session.

Pause model (one release path for both reasons a run blocks):
  * a `human` workflow step  -> request_human(run, reason)  (status "awaiting_human")
  * an operator takeover      -> pause_for_takeover(run)     (status "taken_over")
Both then `await await_release(run, timeout)`; the operator's handback/resume calls release(run).
"""
import asyncio
import time

# status values
RUNNING = "running"
AWAITING_HUMAN = "awaiting_human"
TAKEN_OVER = "taken_over"
DONE = "done"
ERROR = "error"


class RunState:
    def __init__(self, run_id, user_id=None, portal=None, kind="workflow"):
        self.run_id = run_id
        self.user_id = str(user_id) if user_id is not None else None
        self.portal = portal
        self.kind = kind                 # "workflow" | "auto"
        self.session = None              # browser-use BrowserSession (set once started)
        self.cdp = None                  # cached CDPSession for screencast
        self.status = RUNNING
        self.reason = None               # why a human is needed (shown in the co-browse banner)
        self.agent_note = None           # last agent thought, for context
        self.paused = asyncio.Event()    # set => the run loop blocks at the next boundary
        self.resume_evt = asyncio.Event()  # set => release the block
        self.viewers = set()             # attached co-browse WebSockets
        self.screencasting = False
        self.cdp = None
        self.last_frame = None           # {data, metadata} - paint instantly on attach
        self._pending_ack_sid = None     # screencast: latest unacked frame (rate-paced ack)
        self._ack_running = False
        self.recorder = None             # Recorder (Phase C) - records operator takeover actions
        self.controller = None           # the ONE connection id currently allowed to drive (input)
        self.pause_index = None          # base_steps index where the run paused (draft splice point)
        self.base_steps = []             # the run's originating steps (for save-as-workflow merge)
        self.start_url = None
        self.goal = None
        self.portal_slug = None
        self.started_at = time.time()
        self.updated_at = self.started_at
        self.paused_seconds = 0.0        # total wall time blocked on a human (excluded from timeout)
        self._pause_started_at = None    # set while CURRENTLY blocked on a human

    def touch(self):
        self.updated_at = time.time()

    def active_seconds(self):
        """Wall time the run has been ACTIVELY working — i.e. EXCLUDING any time blocked waiting
        for a human to take over. The agent's work-timeout is measured against this, so a long
        2FA/CAPTCHA handoff never cancels the run mid-takeover."""
        paused = self.paused_seconds
        if self._pause_started_at is not None:
            paused += time.time() - self._pause_started_at
        return time.time() - self.started_at - paused

    def to_dict(self):
        return {
            "run_id": self.run_id,
            "portal": self.portal,
            "kind": self.kind,
            "status": self.status,
            "reason": self.reason,
            "agent_note": self.agent_note,
            "needs_human": self.status == AWAITING_HUMAN,
            "owner_id": self.user_id,
            "viewers": len(self.viewers),
            "started_at": round(self.started_at, 1),
            "elapsed_seconds": round(time.time() - self.started_at, 1),
        }


RUNS = {}            # run_id -> RunState for every IN-FLIGHT run

# Finished-run results kept briefly so a poll arriving after the run's request returned can still
# fetch the outcome. Capped: oldest-by-ts evicted once past _RESULTS_CAP.
RESULTS = {}
_RESULTS_CAP = 100


def store_result(run_id, result):
    RESULTS[run_id] = {"result": result, "ts": time.time()}
    if len(RESULTS) > _RESULTS_CAP:
        for k, _ in sorted(RESULTS.items(), key=lambda kv: kv[1]["ts"])[:len(RESULTS) - _RESULTS_CAP]:
            RESULTS.pop(k, None)


def get_result(run_id):
    r = RESULTS.get(run_id)
    return r["result"] if r else None


def register(run):
    RUNS[run.run_id] = run
    return run


def get(run_id):
    return RUNS.get(run_id)


def unregister(run_id):
    RUNS.pop(run_id, None)


def can_access(run, user_id, role=0):
    """Owner or Developer+ (role>=2) may view/take over. FAIL CLOSED otherwise — including
    ownerless runs (only Developer+ may touch a run with no owner)."""
    if run is None:
        return False
    try:
        if role and int(role) >= 2:
            return True
    except (TypeError, ValueError):
        pass
    if run.user_id is None:
        return False
    return str(user_id) == str(run.user_id)


def list_runs(user_id=None, role=0):
    out = [r.to_dict() for r in RUNS.values() if can_access(r, user_id, role)]
    return sorted(out, key=lambda x: -x["started_at"])


def _ensure_recorder(run, sensitive, reason=None):
    """Lazily attach a Recorder once a person starts driving (Phase C). Lazy import avoids an
    import cycle (recorder imports nothing from here)."""
    if run.recorder is not None:
        return
    try:
        from recorder import Recorder
        run.recorder = Recorder(sensitive_context=sensitive,
                                reason=reason or "Enter the value for this field (e.g. a 2FA code)")
    except Exception:
        run.recorder = None


def request_human(run, reason=None):
    """A `human` step asks for a person. Status -> awaiting_human; the step then awaits release."""
    run.status = AWAITING_HUMAN
    run.reason = reason
    run.resume_evt.clear()
    _ensure_recorder(run, sensitive=True, reason=reason)
    run.touch()


def pause_for_takeover(run):
    """An operator requested control. We set `paused` but DO NOT flip status to TAKEN_OVER here —
    the run loop flips it only when it actually suspends at the next block boundary, so operator
    input stays gated until the agent has truly yielded (no human-vs-agent race on the live page).
    A `human` step is already suspended, so it's already input-safe."""
    run.paused.set()
    run.resume_evt.clear()
    _ensure_recorder(run, sensitive=False)
    run.touch()


async def await_release(run, timeout=None):
    """Block until release(run) is called (or timeout). Returns True if released, False on timeout.
    Accounts the blocked span into run.paused_seconds (re-entrancy safe) so the agent's work-timeout
    excludes time spent waiting for a human."""
    _outer = run._pause_started_at is None
    if _outer:
        run._pause_started_at = time.time()
    try:
        await asyncio.wait_for(run.resume_evt.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False
    finally:
        if _outer and run._pause_started_at is not None:
            run.paused_seconds += time.time() - run._pause_started_at
            run._pause_started_at = None


def release(run):
    """Handback / resume: unblock the run loop and return it to RUNNING. Drops the controller so
    a later takeover can re-claim exclusive control."""
    run.paused.clear()
    run.resume_evt.set()
    run.controller = None
    if run.status in (AWAITING_HUMAN, TAKEN_OVER):
        run.status = RUNNING
    run.touch()
