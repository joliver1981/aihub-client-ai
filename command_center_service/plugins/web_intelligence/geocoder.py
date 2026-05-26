"""Web Intelligence — Geocoder
================================

Real geocoder implementation backing the `geocode_address` tool exposed by
:class:`WebIntelligencePlugin`. Replaces the previous placeholder that
returned ``{lat: 0.0, lng: 0.0}`` regardless of input — that "value" is
indistinguishable from a real point on the equator/prime-meridian and was
the cause of false markers off the coast of West Africa on the Ops Room
dominant map.

Design
------
* **Adapter pattern.** Backends implement the :class:`GeocoderBackend`
  protocol (``geocode(query) -> GeocodeResult | None``). The active backend
  is picked by the ``CC_GEOCODER`` env var (default ``"nominatim"``).
* **Default backend** — :class:`NominatimBackend` hits the public OSM
  Nominatim endpoint with a polite ``User-Agent`` and a module-level
  ≤1 req/sec rate limiter (matches Nominatim's published usage policy).
* **Optional paid backend** — :class:`MapboxBackend` is a stub so the hook
  is wired in but only the contract is exercised. Drop in a real
  implementation when an API key is provisioned.
* **Cache.** Module-level LRU keyed on the *normalized* query string,
  capped at 10,000 entries with a 7-day TTL. Addresses don't move so this
  is conservative.
* **Failure mode.** On any error (timeout, HTTP error, empty result, bad
  JSON) the geocoder returns ``None`` and emits a WARN log line. It never
  falls back to ``(0.0, 0.0)`` — that's the bug we're fixing.

Provenance
----------
:class:`GeocodeResult` carries enough fields to construct a
``ProvenanceEntry`` (see ``command_center_service/provenance.py``) without
the caller having to know which backend ran. WS3's
``_enrich_geographic`` stamps the resulting entry onto the map block's
``_provenance`` sibling map at the right field path.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol, runtime_checkable

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------

@dataclass
class GeocodeResult:
    """Structured geocoding result.

    Confidence is mapped from Nominatim's ``importance`` field (a number in
    ``[0, 1]`` reflecting how prominent the place is in OSM). For backends
    that don't supply a confidence-equivalent score we leave the field at
    ``None`` so consumers can distinguish "we didn't measure" from "we
    measured and got 0.5".
    """

    lat: float
    lng: float
    display_name: str
    confidence: Optional[float]  # 0..1, may be None
    source: str  # "nominatim" | "mapbox" | ...
    query: str
    fetched_at: str  # ISO 8601, UTC
    # Optional raw evidence for the audit trail
    raw: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Backend protocol + implementations
# ---------------------------------------------------------------------------

@runtime_checkable
class GeocoderBackend(Protocol):
    """Adapter contract for geocoder backends."""

    name: str

    def geocode(self, query: str) -> GeocodeResult | None:  # pragma: no cover - protocol
        ...


# ---------------------------------------------------------------------------
# Rate limiter — module level, thread-safe.
# ---------------------------------------------------------------------------

class _RateLimiter:
    """Simple ≥1-call-per-N-second gate. Thread-safe; sleeps the caller."""

    def __init__(self, min_interval_seconds: float):
        self.min_interval = float(min_interval_seconds)
        self._lock = threading.Lock()
        self._last_call_at = 0.0

    def acquire(self) -> None:
        """Block (sleep) until the next call slot is allowed.

        We sleep the caller rather than raising so a burst of geocode calls
        spreads out naturally. Use a short min_interval for tight loops.
        """
        with self._lock:
            now = time.monotonic()
            wait = (self._last_call_at + self.min_interval) - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._last_call_at = now

    def reset(self) -> None:
        """For tests — allow the next call immediately."""
        with self._lock:
            self._last_call_at = 0.0


# Nominatim policy: max 1 request per second from a given source.
_NOMINATIM_RATE_LIMITER = _RateLimiter(min_interval_seconds=1.0)


# ---------------------------------------------------------------------------
# LRU cache — keyed on normalized query.
# ---------------------------------------------------------------------------

_CACHE_MAX_ENTRIES = 10_000
_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days


class _LRUCache:
    """Small LRU+TTL cache. Thread-safe."""

    def __init__(self, max_entries: int, ttl_seconds: int):
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._store: "OrderedDict[str, tuple[float, GeocodeResult]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> GeocodeResult | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            stored_at, value = entry
            if (time.time() - stored_at) > self.ttl_seconds:
                # Stale — drop.
                self._store.pop(key, None)
                return None
            # Mark as recently used.
            self._store.move_to_end(key)
            return value

    def put(self, key: str, value: GeocodeResult) -> None:
        with self._lock:
            self._store[key] = (time.time(), value)
            self._store.move_to_end(key)
            while len(self._store) > self.max_entries:
                self._store.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:  # pragma: no cover - trivial
        with self._lock:
            return len(self._store)


_GEOCODE_CACHE = _LRUCache(_CACHE_MAX_ENTRIES, _CACHE_TTL_SECONDS)


def _normalize_query(query: str) -> str:
    """Normalize for cache hits — lowercase, collapse whitespace."""
    if not query:
        return ""
    return " ".join(query.lower().split())


# ---------------------------------------------------------------------------
# Nominatim backend
# ---------------------------------------------------------------------------

_NOMINATIM_ENDPOINT = "https://nominatim.openstreetmap.org/search"
_NOMINATIM_USER_AGENT = "aihub-client-ai-dev (admin contact)"
_NOMINATIM_TIMEOUT_S = 5.0


class NominatimBackend:
    """OpenStreetMap Nominatim geocoder.

    Respects Nominatim's usage policy:
      * Identifying ``User-Agent`` (Nominatim returns 403 without one).
      * ≤1 request per second module-level rate-limit.
      * ``email`` parameter is omitted; if you operate at scale, the policy
        recommends adding an ``email=`` query param and standing up a local
        Nominatim instance instead of using the public endpoint.
    """

    name = "nominatim"

    def __init__(
        self,
        endpoint: str = _NOMINATIM_ENDPOINT,
        user_agent: str = _NOMINATIM_USER_AGENT,
        timeout_seconds: float = _NOMINATIM_TIMEOUT_S,
        rate_limiter: _RateLimiter | None = None,
        http_client_factory=None,
    ):
        self.endpoint = endpoint
        self.user_agent = user_agent
        self.timeout = timeout_seconds
        # Injectable for tests.
        self._rate_limiter = rate_limiter or _NOMINATIM_RATE_LIMITER
        self._http_client_factory = http_client_factory or (
            lambda: httpx.Client(timeout=self.timeout, headers={"User-Agent": self.user_agent})
        )

    def geocode(self, query: str) -> GeocodeResult | None:
        q = (query or "").strip()
        if not q:
            return None

        self._rate_limiter.acquire()

        params = {"q": q, "format": "json", "limit": "1"}
        try:
            with self._http_client_factory() as client:
                resp = client.get(self.endpoint, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException:
            logger.warning("[geocoder/nominatim] timeout for query=%r", q)
            return None
        except httpx.HTTPError as e:
            logger.warning("[geocoder/nominatim] HTTP error for query=%r: %s", q, e)
            return None
        except ValueError as e:
            # json decode error
            logger.warning("[geocoder/nominatim] JSON decode error for query=%r: %s", q, e)
            return None
        except Exception as e:  # belt-and-suspenders; never return (0,0)
            logger.warning("[geocoder/nominatim] unexpected error for query=%r: %s", q, e)
            return None

        if not isinstance(data, list) or not data:
            logger.warning("[geocoder/nominatim] empty result for query=%r", q)
            return None

        top = data[0]
        try:
            lat = float(top["lat"])
            lng = float(top["lon"])
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("[geocoder/nominatim] missing/bad lat-lon in result for query=%r: %s", q, e)
            return None

        # Confidence ≈ Nominatim's "importance" (0..1). Clamp defensively.
        importance = top.get("importance")
        confidence: Optional[float]
        if isinstance(importance, (int, float)):
            confidence = max(0.0, min(1.0, float(importance)))
        else:
            confidence = None

        return GeocodeResult(
            lat=lat,
            lng=lng,
            display_name=str(top.get("display_name") or q),
            confidence=confidence,
            source=self.name,
            query=q,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            raw={
                "place_id": top.get("place_id"),
                "osm_type": top.get("osm_type"),
                "osm_id": top.get("osm_id"),
                "type": top.get("type"),
                "class": top.get("class"),
            },
        )


class MapboxBackend:
    """Mapbox geocoder — stub.

    Wired up so the adapter dispatch table is complete. To enable:
      1. Provision a Mapbox access token (``MAPBOX_TOKEN`` env var).
      2. Replace the body of :meth:`geocode` with a real call to
         ``https://api.mapbox.com/geocoding/v5/mapbox.places/<query>.json``.
      3. Map Mapbox's ``relevance`` (0..1) directly onto ``confidence``.
    """

    name = "mapbox"

    def __init__(self):
        # TODO(geocoder/mapbox): implement real Mapbox call once an API key
        # is provisioned. For now the backend is unconfigured and we always
        # return None so a caller that selects mapbox falls through cleanly.
        self._configured = False

    def geocode(self, query: str) -> GeocodeResult | None:  # pragma: no cover - stub
        if not self._configured:
            logger.warning("[geocoder/mapbox] backend selected but not configured — returning None")
            return None
        # Real implementation goes here.
        return None


# ---------------------------------------------------------------------------
# Dispatch + public API
# ---------------------------------------------------------------------------

_BACKEND_REGISTRY: dict[str, type] = {
    "nominatim": NominatimBackend,
    "mapbox": MapboxBackend,
}

_DEFAULT_BACKEND_NAME = "nominatim"


def _resolve_backend_name() -> str:
    name = (os.environ.get("CC_GEOCODER") or _DEFAULT_BACKEND_NAME).strip().lower()
    if name not in _BACKEND_REGISTRY:
        logger.warning(
            "[geocoder] unknown CC_GEOCODER=%r — falling back to %r",
            name, _DEFAULT_BACKEND_NAME,
        )
        name = _DEFAULT_BACKEND_NAME
    return name


_backend_singleton: GeocoderBackend | None = None
_backend_lock = threading.Lock()


def get_backend() -> GeocoderBackend:
    """Return the active geocoder backend (singleton, env-selected)."""
    global _backend_singleton
    with _backend_lock:
        if _backend_singleton is None:
            cls = _BACKEND_REGISTRY[_resolve_backend_name()]
            _backend_singleton = cls()
        return _backend_singleton


def set_backend(backend: GeocoderBackend | None) -> None:
    """Override the active backend — primarily for tests."""
    global _backend_singleton
    with _backend_lock:
        _backend_singleton = backend


def geocode(query: str) -> GeocodeResult | None:
    """Top-level entry point. Cached + dispatched to the active backend.

    Returns ``None`` on any failure. Never returns a sentinel ``(0, 0)``.
    """
    if not query or not query.strip():
        return None

    key = _normalize_query(query)
    cached = _GEOCODE_CACHE.get(key)
    if cached is not None:
        logger.debug("[geocoder] cache hit for query=%r", query)
        return cached

    backend = get_backend()
    result = backend.geocode(query)
    if result is not None:
        _GEOCODE_CACHE.put(key, result)
    return result


def clear_cache() -> None:
    """Test helper — wipe the LRU cache."""
    _GEOCODE_CACHE.clear()
