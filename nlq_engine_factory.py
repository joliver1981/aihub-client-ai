"""NLQ engine factory — mode resolution, circuit breaker, single construction point.

Part of the NLQ V3 plan (docs/nlq-agentic-engine-plan.md §2). Every NLQ entry
point constructs its engine through create_nlq_engine() instead of building
LLMDataEngine directly, so the legacy/agentic switch lives in exactly one place.

P1 state (current): mode resolution, config plumbing, and the circuit breaker
are live, but the agentic engine does not exist yet — every construction
returns the LEGACY engine. Resolving to 'agentic' logs a warning and falls
through to legacy, so setting NLQ_ENGINE_DEFAULT=agentic today is safe and
visible in logs. P3 swaps AgenticNLQEngine in here and nowhere else.

The legacy construction reproduces the call sites' original code exactly:

    engine = LLMDataEngine(provider=cfg.NLQ_PROVIDER)
    enhanced_qe, enhanced_ae = enhance_engines(engine, nlq_systems)
    engine.query_engine = enhanced_qe
    engine.analytical_engine = enhanced_ae

GeneralAgent's ask_query_agent_a_question historically builds a PLAIN engine
(no enhancement wrappers) — callers preserve that with enhance=False.
"""
import logging
import random
import threading
import time

import config as cfg

logger = logging.getLogger("nlq_engine_factory")
shadow_logger = logging.getLogger("nlq_shadow")

MODE_LEGACY = 'legacy'
MODE_AGENTIC = 'agentic'


def _parse_id_csv(raw):
    """Parse a csv of agent ids into a set of ints, ignoring garbage entries."""
    ids = set()
    for part in str(raw or '').split(','):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            logger.warning(f"[nlq_factory] Ignoring non-integer agent id in NLQ engine config: {part!r}")
    return ids


def resolve_engine_mode(agent_id=None):
    """Resolve which engine serves this agent.

    Precedence: NLQ_LEGACY_AGENT_IDS (deny) > NLQ_AGENTIC_AGENT_IDS (allow)
    > NLQ_ENGINE_DEFAULT > legacy. Unknown/invalid values resolve to legacy —
    misconfiguration must never take the feature down.
    """
    try:
        default = str(getattr(cfg, 'NLQ_ENGINE_DEFAULT', MODE_LEGACY) or MODE_LEGACY).strip().lower()
        if default not in (MODE_LEGACY, MODE_AGENTIC):
            logger.warning(f"[nlq_factory] Unknown NLQ_ENGINE_DEFAULT {default!r}; using legacy")
            default = MODE_LEGACY

        aid = None
        if agent_id is not None:
            try:
                aid = int(agent_id)
            except (TypeError, ValueError):
                aid = None

        if aid is not None:
            if aid in _parse_id_csv(getattr(cfg, 'NLQ_LEGACY_AGENT_IDS', '')):
                return MODE_LEGACY
            if aid in _parse_id_csv(getattr(cfg, 'NLQ_AGENTIC_AGENT_IDS', '')):
                return MODE_AGENTIC
        return default
    except Exception as e:
        logger.error(f"[nlq_factory] Mode resolution failed ({e}); using legacy")
        return MODE_LEGACY


class CircuitBreaker:
    """Process-wide breaker for the agentic engine (thread-safe).

    After `threshold` consecutive failures the breaker opens and agentic
    construction is skipped (legacy serves everything) until `cooldown_s`
    elapses; the cooldown expiry fully resets the breaker. Threshold/cooldown
    read config at call time unless overridden in the constructor (tests).
    """

    def __init__(self, threshold=None, cooldown_s=None):
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._opened_at = None
        self._threshold = threshold
        self._cooldown_s = cooldown_s

    def _get_threshold(self):
        if self._threshold is not None:
            return self._threshold
        return int(getattr(cfg, 'NLQ_AGENTIC_BREAKER_THRESHOLD', 3))

    def _get_cooldown(self):
        if self._cooldown_s is not None:
            return self._cooldown_s
        return int(getattr(cfg, 'NLQ_AGENTIC_BREAKER_COOLDOWN_S', 600))

    def record_failure(self):
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._get_threshold() and self._opened_at is None:
                self._opened_at = time.time()
                logger.error(
                    f"[nlq_factory] Circuit breaker OPEN after {self._consecutive_failures} "
                    f"consecutive agentic failures — legacy serves all NLQ traffic for "
                    f"{self._get_cooldown()}s"
                )

    def record_success(self):
        with self._lock:
            self._consecutive_failures = 0
            self._opened_at = None

    def is_open(self):
        with self._lock:
            if self._opened_at is None:
                return False
            if time.time() - self._opened_at >= self._get_cooldown():
                logger.warning("[nlq_factory] Circuit breaker cooldown elapsed — agentic traffic re-enabled")
                self._opened_at = None
                self._consecutive_failures = 0
                return False
            return True


# Shared process-wide breaker instance (used by the agentic engine from P3 on).
agentic_breaker = CircuitBreaker()


def _get_enhancement_deps():
    """Late imports to avoid circulars (same pattern as routes/data_explorer.py)."""
    from engine_enhancements import enhance_engines
    import app as main_app
    return enhance_engines, main_app.nlq_systems


def _construct_legacy(provider=None, enhance=True, deps=None):
    from LLMDataEngineV2 import LLMDataEngine
    engine = LLMDataEngine(provider=provider or cfg.NLQ_PROVIDER)
    if enhance:
        enhance_engines, nlq_systems = deps if deps is not None else _get_enhancement_deps()
        enhanced_qe, enhanced_ae = enhance_engines(engine, nlq_systems)
        engine.query_engine = enhanced_qe
        engine.analytical_engine = enhanced_ae
    return engine


def _construct_agentic():
    from nlq_agentic import AgenticNLQEngine
    return AgenticNLQEngine(provider="openai")


def maybe_run_shadow(agent_id, question, history, legacy_answer_type=None):
    """Shadow mode: on a sampled fraction of LEGACY-served requests, ALSO run the
    agentic engine in a background daemon thread and log a comparison. Log-only —
    it never touches the user's response, never affects the circuit breaker
    (uses AgenticNLQEngine.shadow_run, which is breaker-isolated), and swallows
    every error. Gated by NLQ_SHADOW_COMPARE + NLQ_SHADOW_SAMPLE_PCT; both off by
    default, so this is a no-op in normal operation. Doubles LLM/DB cost on
    sampled requests — keep the sample small.
    """
    try:
        if not getattr(cfg, "NLQ_SHADOW_COMPARE", False):
            return
        pct = int(getattr(cfg, "NLQ_SHADOW_SAMPLE_PCT", 10))
        if pct <= 0 or random.random() * 100.0 >= pct:
            return

        def _run():
            try:
                engine = _construct_agentic()
                summary = engine.shadow_run(agent_id, question, history=history)
                shadow_logger.warning(
                    "[NLQ_SHADOW] agent=%s q=%r legacy_type=%s agentic=%s",
                    agent_id, (question or "")[:80], legacy_answer_type, summary
                )
            except Exception as e:  # pragma: no cover - best-effort
                shadow_logger.warning(f"[NLQ_SHADOW] shadow run failed: {e}")

        threading.Thread(target=_run, name="nlq-shadow", daemon=True).start()
    except Exception as e:  # pragma: no cover - never let shadow break a request
        logger.debug(f"[nlq_factory] maybe_run_shadow skipped: {e}")


def create_nlq_engine(agent_id=None, provider=None, enhance=True, purpose='', deps=None):
    """Single construction point for NLQ engines.

    Args:
        agent_id: agent the engine will serve, when known at construction time
                  (per-agent allow/deny lists only apply when this is passed).
        provider: LLM provider for the legacy engine; defaults to cfg.NLQ_PROVIDER.
        enhance: apply engine_enhancements wrappers (GeneralAgent passes False).
                 Ignored for the agentic engine (it has no sub-engines to wrap).
        purpose: short caller tag for logs.
        deps: optional (enhance_engines, nlq_systems) tuple — app.py passes its
              module-level pair; when omitted they are late-imported.
    """
    mode = resolve_engine_mode(agent_id)
    if mode == MODE_AGENTIC:
        if agentic_breaker.is_open():
            logger.warning(
                f"[nlq_factory] mode=agentic but breaker is OPEN — using legacy "
                f"(agent={agent_id}, purpose={purpose})"
            )
        else:
            try:
                engine = _construct_agentic()
                logger.info(f"[nlq_factory] mode=agentic (agent={agent_id}, purpose={purpose})")
                return engine
            except Exception as e:
                # Construction must never take the feature down — fall back to legacy.
                logger.error(
                    f"[nlq_factory] agentic construction failed ({e}); using legacy "
                    f"(agent={agent_id}, purpose={purpose})"
                )
    else:
        logger.info(f"[nlq_factory] mode=legacy (agent={agent_id}, purpose={purpose})")
    return _construct_legacy(provider=provider, enhance=enhance, deps=deps)
