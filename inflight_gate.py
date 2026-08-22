"""In-flight admission gate: fail FAST with 503 + Retry-After instead of queueing.

WHY THIS EXISTS (measured 2026-08-21, see docs/doc-api-concurrency-and-fast-busy.md):
the document stack does not serialize requests in-process — waitress had 16
threads and two concurrent /document/process calls ran their LLM phases side by
side. What stalls is the shared Azure SQL tier (S1) during the SQL store, and
while it stalls every slow request holds a waitress thread. Once the thread pool
is full, NEW callers are parked in waitress's accept queue with no response at
all until a thread frees — they only learn the service was busy when their own
client read timeout fires minutes later (The Agent's import tool gave up at 300 s
while the server went on to store the document). A gate that admits at most N
concurrent heavy requests and answers the (N+1)th immediately with 503 +
Retry-After turns that silent multi-minute hang into an honest, instant "busy".

Design rules:
  * NON-BLOCKING: try_enter never waits. A full gate answers at once.
  * Cap < server threads: the 503 itself needs a free thread to be served, so
    callers size the limit below the waitress thread count (helpers below).
  * Advisory only: it protects the pool; it never changes what an admitted
    request does (extraction semantics untouched).
  * Observable: snapshot() feeds /health so operators and clients can SEE the
    in-flight count, the peak, and how many callers were turned away.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Optional


class InflightGate:
    """Thread-safe bounded slot counter with adaptive Retry-After."""

    def __init__(self, name: str, limit: int, retry_after_default: int = 30,
                 retry_after_min: int = 10, retry_after_max: int = 300,
                 duration_window: int = 20):
        self.name = name
        self.limit = max(1, int(limit))
        self.retry_after_default = int(retry_after_default)
        self.retry_after_min = int(retry_after_min)
        self.retry_after_max = int(retry_after_max)
        self._lock = threading.Lock()
        self._in_flight = 0
        self._peak = 0
        self._admitted_total = 0
        self._rejected_total = 0
        self._last_rejected_at: Optional[float] = None
        self._recent_durations: deque = deque(maxlen=max(1, int(duration_window)))
        self._started: dict = {}          # token -> perf_counter at admit
        self._next_token = 0

    # ------------------------------------------------------------------ core
    def try_enter(self) -> Optional[int]:
        """Admit the caller if a slot is free. Returns a token, or None (busy)."""
        with self._lock:
            if self._in_flight >= self.limit:
                self._rejected_total += 1
                self._last_rejected_at = time.time()
                return None
            self._in_flight += 1
            self._admitted_total += 1
            if self._in_flight > self._peak:
                self._peak = self._in_flight
            self._next_token += 1
            token = self._next_token
            self._started[token] = time.perf_counter()
            return token

    def leave(self, token: Optional[int]) -> Optional[float]:
        """Release a slot. Returns the held duration in seconds (None if unknown)."""
        if token is None:
            return None
        with self._lock:
            started = self._started.pop(token, None)
            if started is None:
                return None        # double release / unknown token: ignore
            self._in_flight = max(0, self._in_flight - 1)
            held = time.perf_counter() - started
            self._recent_durations.append(held)
            return held

    @contextmanager
    def slot(self):
        """`with gate.slot() as token:` — token is None when the gate is full."""
        token = self.try_enter()
        try:
            yield token
        finally:
            self.leave(token)

    # ------------------------------------------------------------ reporting
    def retry_after_seconds(self) -> int:
        """Adaptive hint: half a typical recent hold time, clamped. Default when
        nothing has completed yet. Half, because a slot frees when ANY of the
        admitted requests finishes, not when the newest one does."""
        with self._lock:
            samples = list(self._recent_durations)
        if not samples:
            est = self.retry_after_default
        else:
            samples.sort()
            median = samples[len(samples) // 2]
            est = int(round(median / 2.0))
        return max(self.retry_after_min, min(self.retry_after_max, est))

    def snapshot(self) -> dict:
        with self._lock:
            samples = list(self._recent_durations)
            snap = {
                "name": self.name,
                "in_flight": self._in_flight,
                "limit": self.limit,
                "busy": self._in_flight >= self.limit,
                "peak_in_flight": self._peak,
                "admitted_total": self._admitted_total,
                "rejected_total": self._rejected_total,
                "last_rejected_at": self._last_rejected_at,
            }
        if samples:
            s = sorted(samples)
            snap["recent_duration_s"] = {
                "count": len(s),
                "median": round(s[len(s) // 2], 2),
                "max": round(s[-1], 2),
            }
        snap["retry_after_s"] = self.retry_after_seconds()
        return snap

    def busy_payload(self, what: str = "request") -> dict:
        """JSON body for the 503. Worded so an LLM tool can relay it verbatim."""
        snap = self.snapshot()
        ra = snap["retry_after_s"]
        return {
            "status": "busy",
            "error": "busy",
            "message": (f"The document stack is busy: {snap['in_flight']} of "
                        f"{snap['limit']} {self.name} slots are in use. This is a "
                        f"queue, not an outage — retry this {what} in about {ra} "
                        f"seconds. Nothing was processed for this call."),
            "in_flight": snap["in_flight"],
            "max_in_flight": snap["limit"],
            "retry_after": ra,
        }


# ----------------------------------------------------------------- helpers
def limit_from_env(env_var: str, threads_env: str = "SERVER_THREADS",
                   threads_default: int = 10, headroom: int = 2,
                   floor: int = 1) -> int:
    """Resolve a gate limit: explicit `env_var` wins; otherwise server threads
    minus `headroom` (the threads kept free so the 503 — and health checks —
    can still be served while the heavy slots are all taken)."""
    raw = os.getenv(env_var)
    if raw not in (None, ""):
        try:
            return max(floor, int(raw))
        except ValueError:
            pass
    try:
        threads = int(os.getenv(threads_env, threads_default))
    except ValueError:
        threads = threads_default
    return max(floor, threads - headroom)


def flask_busy_response(gate: InflightGate, what: str = "request"):
    """(body, 503, headers) tuple for Flask views. Imported lazily so this module
    stays importable without Flask (unit tests, scripts)."""
    from flask import jsonify
    payload = gate.busy_payload(what)
    resp = jsonify(payload)
    resp.status_code = 503
    resp.headers["Retry-After"] = str(payload["retry_after"])
    resp.headers["X-Inflight"] = f"{payload['in_flight']}/{payload['max_in_flight']}"
    return resp


def gated(gate: InflightGate, what: str = "request", logger=None):
    """Decorator for Flask views: admit or answer 503 immediately.

    Apply INNERMOST (below auth decorators) so unauthenticated callers are
    rejected by auth without consuming a slot."""
    from functools import wraps

    def deco(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            token = gate.try_enter()
            if token is None:
                if logger is not None:
                    snap = gate.snapshot()
                    logger.warning(f"[inflight] {gate.name}: REJECTED (503) — "
                                   f"{snap['in_flight']}/{snap['limit']} in flight, "
                                   f"rejected_total={snap['rejected_total']}")
                return flask_busy_response(gate, what)
            try:
                return view(*args, **kwargs)
            finally:
                held = gate.leave(token)
                if logger is not None and held is not None:
                    logger.debug(f"[inflight] {gate.name}: released after {held:.1f}s "
                                 f"(now {gate.snapshot()['in_flight']}/{gate.limit})")
        return wrapper
    return deco
