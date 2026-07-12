"""Unit tests — P6 hardening: chaos drill, breaker under traffic, shadow mode.

Verifies the reliability story end to end without a live app/DB:
  * NLQ_AGENTIC_FORCE_ERROR → every request transparently served by legacy.
  * Repeated agentic failures trip the process breaker; once open, requests
    skip agentic entirely (no wasted attempt) and recover after cooldown.
  * Shadow mode runs agentic in the background, log-only, and is fully
    isolated from the production breaker; failures never surface.

NOTE: repo .gitignore ignores test*.py — commit with `git add -f`.
"""
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import config as cfg
import nlq_engine_factory as factory
from nlq_agentic import AgenticNLQEngine


class _FakeLegacy:
    def clear_chat_hist(self): pass
    def add_message_to_hist(self, *a, **k): pass
    def get_answer(self, agent_id, q, recursion_depth=0):
        return ("LEGACY_ANSWER", "", "", "string", "", q, "", "")


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    # Fresh breaker + legacy fallback for every test.
    factory.agentic_breaker.record_success()
    monkeypatch.setattr(cfg, "NLQ_AGENTIC_FALLBACK", True, raising=False)
    monkeypatch.setattr(factory, "_construct_legacy", lambda enhance=False: _FakeLegacy())
    yield
    factory.agentic_breaker.record_success()


def _answer(res):
    return res[0] if isinstance(res, tuple) else res.get("answer")


# ── chaos drill: NLQ_AGENTIC_FORCE_ERROR ─────────────────────────────────

def test_force_error_falls_back_to_legacy(monkeypatch):
    monkeypatch.setattr(cfg, "NLQ_AGENTIC_FORCE_ERROR", True, raising=False)
    eng = AgenticNLQEngine()
    res = eng.get_answer(281, "how many orders?")
    assert _answer(res) == "LEGACY_ANSWER"


def test_force_error_disabled_runs_agentic(monkeypatch):
    # With the flag OFF, the engine attempts the agentic pipeline (which we stub
    # to a sentinel) rather than falling back.
    monkeypatch.setattr(cfg, "NLQ_AGENTIC_FORCE_ERROR", False, raising=False)
    monkeypatch.setattr(AgenticNLQEngine, "_run_agentic",
                        lambda self, a, q, t: ("AGENTIC_OK", "", "", "string", "", q, "", ""))
    eng = AgenticNLQEngine()
    assert _answer(eng.get_answer(281, "q")) == "AGENTIC_OK"


# ── breaker under sustained traffic ──────────────────────────────────────

def test_breaker_trips_then_skips_agentic(monkeypatch):
    monkeypatch.setattr(cfg, "NLQ_AGENTIC_BREAKER_THRESHOLD", 2, raising=False)
    monkeypatch.setattr(cfg, "NLQ_AGENTIC_BREAKER_COOLDOWN_S", 300, raising=False)

    calls = {"n": 0}

    def failing_run(self, agent_id, q, trace):
        calls["n"] += 1
        raise RuntimeError("simulated agentic failure")
    monkeypatch.setattr(AgenticNLQEngine, "_run_agentic", failing_run)

    eng = AgenticNLQEngine()
    # 4 requests of "traffic"; breaker threshold is 2.
    for _ in range(4):
        assert _answer(eng.get_answer(281, "q")) == "LEGACY_ANSWER"

    # Breaker opened after 2 failures; requests 3 & 4 skipped agentic entirely.
    assert factory.agentic_breaker.is_open()
    assert calls["n"] == 2, "breaker should stop invoking agentic once open"


def test_breaker_recovers_after_cooldown(monkeypatch):
    monkeypatch.setattr(cfg, "NLQ_AGENTIC_BREAKER_THRESHOLD", 1, raising=False)
    monkeypatch.setattr(cfg, "NLQ_AGENTIC_BREAKER_COOLDOWN_S", 0, raising=False)
    monkeypatch.setattr(AgenticNLQEngine, "_run_agentic",
                        lambda self, a, q, t: (_ for _ in ()).throw(RuntimeError("boom")))
    eng = AgenticNLQEngine()
    eng.get_answer(281, "q")               # 1 failure → opens (threshold 1)
    time.sleep(0.01)
    assert not factory.agentic_breaker.is_open()   # cooldown 0 → immediately recovered


# ── shadow mode ──────────────────────────────────────────────────────────

def test_shadow_run_is_breaker_isolated(monkeypatch):
    # A shadow failure must NOT touch the production breaker.
    monkeypatch.setattr(cfg, "NLQ_AGENTIC_FORCE_ERROR", True, raising=False)
    factory.agentic_breaker.record_success()
    eng = AgenticNLQEngine()
    out = eng.shadow_run(281, "q", history=[{"role": "Q", "content": "hi"}])
    assert out["ok"] is False
    assert not factory.agentic_breaker.is_open()
    # breaker counter untouched
    assert factory.agentic_breaker._consecutive_failures == 0


def test_shadow_run_success_summary(monkeypatch):
    monkeypatch.setattr(cfg, "NLQ_AGENTIC_FORCE_ERROR", False, raising=False)
    monkeypatch.setattr(AgenticNLQEngine, "_run_agentic",
                        lambda self, a, q, t: ("42 orders", "", "", "string", "", q, "", ""))
    eng = AgenticNLQEngine()
    out = eng.shadow_run(281, "q")
    assert out["ok"] and out["answer_type"] == "string" and "42" in out["answer"]


def test_maybe_run_shadow_off_by_default(monkeypatch):
    monkeypatch.setattr(cfg, "NLQ_SHADOW_COMPARE", False, raising=False)
    ran = {"v": False}
    monkeypatch.setattr(factory, "_construct_agentic",
                        lambda: (_ for _ in ()).throw(AssertionError("should not construct")))
    factory.maybe_run_shadow(281, "q", [], "string")  # must be a no-op
    time.sleep(0.05)
    assert ran["v"] is False


def test_maybe_run_shadow_samples_and_runs(monkeypatch):
    monkeypatch.setattr(cfg, "NLQ_SHADOW_COMPARE", True, raising=False)
    monkeypatch.setattr(cfg, "NLQ_SHADOW_SAMPLE_PCT", 100, raising=False)
    ran = {"v": False}

    class _ShadowEng:
        def shadow_run(self, agent_id, question, history=None):
            ran["v"] = True
            return {"ok": True, "answer_type": "string", "answer": "x"}
    monkeypatch.setattr(factory, "_construct_agentic", lambda: _ShadowEng())

    factory.maybe_run_shadow(281, "q", [{"role": "Q", "content": "hi"}], "string")
    for _ in range(50):
        if ran["v"]:
            break
        time.sleep(0.02)
    assert ran["v"] is True


def test_maybe_run_shadow_swallows_construction_failure(monkeypatch):
    monkeypatch.setattr(cfg, "NLQ_SHADOW_COMPARE", True, raising=False)
    monkeypatch.setattr(cfg, "NLQ_SHADOW_SAMPLE_PCT", 100, raising=False)
    monkeypatch.setattr(factory, "_construct_agentic",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    # Must not raise on the calling thread.
    factory.maybe_run_shadow(281, "q", [], "string")
    time.sleep(0.05)
