"""Engine selection + circuit breaker for the document-search re-core.

Mirrors the proven NLQ engine-factory pattern:
  - selection precedence: denylist -> allowlist -> global default -> legacy
  - every error path resolves to 'legacy'
  - a process-wide circuit breaker pins traffic to legacy after
    DOC_SEARCH_V2_BREAKER_THRESHOLD consecutive v2 failures, for
    DOC_SEARCH_V2_BREAKER_COOLDOWN_S seconds

Rollback ladder: automatic breaker -> per-agent denylist -> global default.
"""
import logging
import threading
import time

import config as cfg

_lock = threading.Lock()
_state = {'consecutive_failures': 0, 'open_until': 0.0}


def _id_set(csv_value) -> set:
    return {s.strip() for s in str(csv_value or '').split(',') if s.strip()}


def breaker_open() -> bool:
    with _lock:
        return time.time() < _state['open_until']


def record_v2_failure() -> None:
    """Count a v2 failure; open the breaker at the threshold."""
    try:
        threshold = int(getattr(cfg, 'DOC_SEARCH_V2_BREAKER_THRESHOLD', 3))
        cooldown = int(getattr(cfg, 'DOC_SEARCH_V2_BREAKER_COOLDOWN_S', 600))
        with _lock:
            _state['consecutive_failures'] += 1
            if _state['consecutive_failures'] >= threshold:
                _state['open_until'] = time.time() + cooldown
                logging.warning(
                    f"doc_search_v2 circuit breaker OPEN for {cooldown}s "
                    f"({_state['consecutive_failures']} consecutive failures) — all traffic on legacy"
                )
    except Exception:
        pass


def record_v2_success() -> None:
    with _lock:
        _state['consecutive_failures'] = 0


def reset_breaker() -> None:
    """Test/ops helper."""
    with _lock:
        _state['consecutive_failures'] = 0
        _state['open_until'] = 0.0


def resolve_engine(agent_id) -> str:
    """Return 'v2' or 'legacy' for this agent. Never raises; unknown -> legacy."""
    try:
        aid = str(agent_id)
        if aid in _id_set(getattr(cfg, 'DOC_SEARCH_LEGACY_AGENT_IDS', '')):
            return 'legacy'
        wants_v2 = (
            aid in _id_set(getattr(cfg, 'DOC_SEARCH_V2_AGENT_IDS', ''))
            or str(getattr(cfg, 'DOC_SEARCH_ENGINE_DEFAULT', 'legacy')).strip().lower() == 'v2'
        )
        if wants_v2 and not breaker_open():
            return 'v2'
        return 'legacy'
    except Exception as e:
        logging.warning(f"doc_search_v2 engine resolution failed ({e}) — using legacy")
        return 'legacy'
