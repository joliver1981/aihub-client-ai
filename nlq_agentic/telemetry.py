"""Telemetry for the agentic NLQ engine (plan §4).

Records the per-request tool trace and emits a [DATA_EXPLORER_TIMING]-parity
log line so existing timing habits keep working, plus a JSONL trace line for the
side-by-side eval diffing in P5. Every method is defensive — telemetry must
never break a request.
"""
import json
import logging
import os
import time

logger = logging.getLogger("nlq_agentic.telemetry")

try:
    from CommonUtils import get_log_path
except Exception:  # pragma: no cover - CommonUtils always present in-app
    def get_log_path(filename):
        return filename


class RequestTrace:
    """Accumulates the trace for one get_answer call."""

    def __init__(self, agent_id, question, mode="agentic"):
        self.agent_id = agent_id
        self.question = question
        self.mode = mode
        self.model = None
        self.tool_calls = []          # [{name, ms, ok, error, args_digest}]
        self.iterations = 0
        self.fallback_used = False
        self.fallback_reason = None
        self.final_answer_type = None
        self.error = None
        self._t0 = time.time()

    def record_tool(self, name, ms, ok, args_digest="", error=None):
        self.tool_calls.append({
            "name": name,
            "ms": round(ms, 1),
            "ok": bool(ok),
            "args_digest": (args_digest or "")[:200],
            "error": (str(error)[:200] if error else None),
        })

    def total_ms(self):
        return (time.time() - self._t0) * 1000.0

    def _breakdown(self):
        agg = {}
        for tc in self.tool_calls:
            key = tc["name"]
            agg[key] = agg.get(key, 0.0) + tc["ms"]
        return " | ".join(f"{k}={v/1000.0:.2f}s" for k, v in sorted(agg.items(), key=lambda kv: -kv[1]))

    def emit(self):
        """Emit the timing summary and a JSONL trace line. Never raises."""
        total = self.total_ms()
        try:
            logger.warning(
                f"[DATA_EXPLORER_TIMING] engine=agentic total={total/1000.0:.2f}s "
                f"iterations={self.iterations} tools={len(self.tool_calls)} "
                f"fallback={self.fallback_used} :: {self._breakdown()}"
            )
        except Exception:
            pass
        try:
            record = {
                "agent_id": self.agent_id,
                "question": (self.question or "")[:500],
                "mode": self.mode,
                "model": self.model,
                "iterations": self.iterations,
                "tool_calls": self.tool_calls,
                "fallback_used": self.fallback_used,
                "fallback_reason": self.fallback_reason,
                "final_answer_type": self.final_answer_type,
                "error": self.error,
                "total_ms": round(total, 1),
            }
            path = os.getenv("NLQ_AGENTIC_TRACE_LOG", get_log_path("nlq_agentic_trace.jsonl"))
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        except Exception as e:  # pragma: no cover - disk/serialise best-effort
            logger.debug(f"[nlq_agentic.telemetry] trace write skipped: {e}")
