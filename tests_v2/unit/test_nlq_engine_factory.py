"""Unit tests — nlq_engine_factory (NLQ V3 plan P1, docs/nlq-agentic-engine-plan.md).

Covers mode-resolution precedence, csv parsing robustness, the circuit
breaker, and legacy construction fidelity (plain vs enhanced-with-injected-
deps). No Flask app import anywhere — construction tests build the real
LLMDataEngine exactly the way the entry points do.

NOTE: repo .gitignore ignores test*.py — this file must be committed with
`git add -f`.
"""
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import config as cfg
import nlq_engine_factory as factory


# ── csv parsing ──────────────────────────────────────────────────────────

def test_parse_id_csv_robust():
    assert factory._parse_id_csv('1, 2,junk, ,3') == {1, 2, 3}
    assert factory._parse_id_csv('') == set()
    assert factory._parse_id_csv(None) == set()
    assert factory._parse_id_csv('  42  ') == {42}


# ── mode resolution ──────────────────────────────────────────────────────

def test_default_is_legacy(monkeypatch):
    monkeypatch.setattr(cfg, 'NLQ_ENGINE_DEFAULT', 'legacy', raising=False)
    monkeypatch.setattr(cfg, 'NLQ_AGENTIC_AGENT_IDS', '', raising=False)
    monkeypatch.setattr(cfg, 'NLQ_LEGACY_AGENT_IDS', '', raising=False)
    assert factory.resolve_engine_mode(281) == factory.MODE_LEGACY
    assert factory.resolve_engine_mode(None) == factory.MODE_LEGACY


def test_global_agentic_default(monkeypatch):
    monkeypatch.setattr(cfg, 'NLQ_ENGINE_DEFAULT', 'agentic', raising=False)
    monkeypatch.setattr(cfg, 'NLQ_AGENTIC_AGENT_IDS', '', raising=False)
    monkeypatch.setattr(cfg, 'NLQ_LEGACY_AGENT_IDS', '', raising=False)
    assert factory.resolve_engine_mode(281) == factory.MODE_AGENTIC
    assert factory.resolve_engine_mode(None) == factory.MODE_AGENTIC


def test_allowlist_overrides_legacy_default(monkeypatch):
    monkeypatch.setattr(cfg, 'NLQ_ENGINE_DEFAULT', 'legacy', raising=False)
    monkeypatch.setattr(cfg, 'NLQ_AGENTIC_AGENT_IDS', '7, 281', raising=False)
    monkeypatch.setattr(cfg, 'NLQ_LEGACY_AGENT_IDS', '', raising=False)
    assert factory.resolve_engine_mode(281) == factory.MODE_AGENTIC
    assert factory.resolve_engine_mode(9) == factory.MODE_LEGACY


def test_denylist_beats_allowlist_and_default(monkeypatch):
    monkeypatch.setattr(cfg, 'NLQ_ENGINE_DEFAULT', 'agentic', raising=False)
    monkeypatch.setattr(cfg, 'NLQ_AGENTIC_AGENT_IDS', '5', raising=False)
    monkeypatch.setattr(cfg, 'NLQ_LEGACY_AGENT_IDS', '5, 6', raising=False)
    assert factory.resolve_engine_mode(5) == factory.MODE_LEGACY
    assert factory.resolve_engine_mode(6) == factory.MODE_LEGACY
    assert factory.resolve_engine_mode(7) == factory.MODE_AGENTIC


def test_unknown_default_falls_back_to_legacy(monkeypatch):
    monkeypatch.setattr(cfg, 'NLQ_ENGINE_DEFAULT', 'banana', raising=False)
    monkeypatch.setattr(cfg, 'NLQ_AGENTIC_AGENT_IDS', '', raising=False)
    monkeypatch.setattr(cfg, 'NLQ_LEGACY_AGENT_IDS', '', raising=False)
    assert factory.resolve_engine_mode(1) == factory.MODE_LEGACY


def test_agent_id_type_handling(monkeypatch):
    monkeypatch.setattr(cfg, 'NLQ_ENGINE_DEFAULT', 'legacy', raising=False)
    monkeypatch.setattr(cfg, 'NLQ_AGENTIC_AGENT_IDS', '5', raising=False)
    monkeypatch.setattr(cfg, 'NLQ_LEGACY_AGENT_IDS', '', raising=False)
    # Numeric strings resolve like ints; garbage falls back to the default.
    assert factory.resolve_engine_mode('5') == factory.MODE_AGENTIC
    assert factory.resolve_engine_mode('abc') == factory.MODE_LEGACY


# ── circuit breaker ──────────────────────────────────────────────────────

def test_breaker_opens_after_threshold_and_cools_down():
    br = factory.CircuitBreaker(threshold=2, cooldown_s=0.05)
    assert not br.is_open()
    br.record_failure()
    assert not br.is_open()
    br.record_failure()
    assert br.is_open()
    time.sleep(0.06)
    assert not br.is_open()   # cooldown elapsed -> fully reset
    br.record_failure()
    assert not br.is_open()   # counter restarted after reset


def test_breaker_success_resets_counter():
    br = factory.CircuitBreaker(threshold=2, cooldown_s=60)
    br.record_failure()
    br.record_success()
    br.record_failure()
    assert not br.is_open()


def test_breaker_reads_config_when_not_overridden(monkeypatch):
    monkeypatch.setattr(cfg, 'NLQ_AGENTIC_BREAKER_THRESHOLD', 1, raising=False)
    monkeypatch.setattr(cfg, 'NLQ_AGENTIC_BREAKER_COOLDOWN_S', 600, raising=False)
    br = factory.CircuitBreaker()
    br.record_failure()
    assert br.is_open()


# ── construction fidelity ────────────────────────────────────────────────

def test_construct_plain_legacy_engine(monkeypatch):
    """GeneralAgent parity: enhance=False yields unwrapped sub-engines."""
    # Pin legacy mode — the ambient env may run the agentic pilot
    # (NLQ_ENGINE_DEFAULT=agentic), and this test is about LEGACY construction.
    monkeypatch.setattr(cfg, 'NLQ_ENGINE_DEFAULT', 'legacy', raising=False)
    monkeypatch.setattr(cfg, 'NLQ_AGENTIC_AGENT_IDS', '', raising=False)
    from LLMDataEngineV2 import LLMDataEngine
    from LLMQueryEngine import LLMQueryEngine
    engine = factory.create_nlq_engine(agent_id=1, enhance=False, purpose='unit-test')
    assert isinstance(engine, LLMDataEngine)
    assert type(engine.query_engine) is LLMQueryEngine


def test_construct_enhanced_engine_with_injected_deps(monkeypatch):
    """Route parity: deps injection applies the enhancement return values."""
    # Pin legacy mode (see test_construct_plain_legacy_engine).
    monkeypatch.setattr(cfg, 'NLQ_ENGINE_DEFAULT', 'legacy', raising=False)
    monkeypatch.setattr(cfg, 'NLQ_AGENTIC_AGENT_IDS', '', raising=False)
    sentinel_qe, sentinel_ae = object(), object()

    def fake_enhance(engine, systems):
        assert systems == {'sys': True}
        return sentinel_qe, sentinel_ae

    engine = factory.create_nlq_engine(
        enhance=True, purpose='unit-test', deps=(fake_enhance, {'sys': True})
    )
    assert engine.query_engine is sentinel_qe
    assert engine.analytical_engine is sentinel_ae


def test_agentic_mode_constructs_agentic_engine(monkeypatch):
    """P3: agentic mode now builds the real AgenticNLQEngine (breaker closed)."""
    monkeypatch.setattr(cfg, 'NLQ_ENGINE_DEFAULT', 'agentic', raising=False)
    monkeypatch.setattr(cfg, 'NLQ_AGENTIC_AGENT_IDS', '', raising=False)
    monkeypatch.setattr(cfg, 'NLQ_LEGACY_AGENT_IDS', '', raising=False)
    factory.agentic_breaker.record_success()  # ensure closed
    from nlq_agentic import AgenticNLQEngine
    engine = factory.create_nlq_engine(agent_id=42, purpose='unit-test')
    assert isinstance(engine, AgenticNLQEngine)


def test_agentic_construction_failure_falls_back_to_legacy(monkeypatch):
    """Construction must never take the feature down — a broken agentic ctor -> legacy."""
    monkeypatch.setattr(cfg, 'NLQ_ENGINE_DEFAULT', 'agentic', raising=False)
    monkeypatch.setattr(cfg, 'NLQ_AGENTIC_AGENT_IDS', '', raising=False)
    monkeypatch.setattr(cfg, 'NLQ_LEGACY_AGENT_IDS', '', raising=False)
    factory.agentic_breaker.record_success()

    def boom():
        raise RuntimeError("simulated agentic import failure")
    monkeypatch.setattr(factory, '_construct_agentic', boom)

    from LLMDataEngineV2 import LLMDataEngine
    engine = factory.create_nlq_engine(agent_id=42, enhance=False, purpose='unit-test')
    assert isinstance(engine, LLMDataEngine)
