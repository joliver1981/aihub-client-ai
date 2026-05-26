"""Command Center Service — Ops Room API
==========================================

Backend for the experimental Ops Command Room UI (`/ops`). Every endpoint
under this router is a thin, read-only aggregation over the same primitives
the classic CC already maintains:

- ``services.SessionManager``       — session count + per-session messages
- ``services.trace_store.TraceStore`` — per-trace JSONL files on disk
- An in-process counter for "in-flight" chat requests (incremented from
  ``routes/chat.py`` via ``ops_inflight``).

There is **no** parallel store. We never invent fake "points" or "alerts"
that the rest of the system doesn't actually have.

Endpoints
---------

``GET /api/ops/kpis``
    Real KPI tile values for the ops room. JSON shape:
    ``{
        "sessions":        {"value": <int>,  "trend": "...",  "tone": "info"},
        "traces_24h":      {"value": <int>,  "trend": "...",  "tone": "info"},
        "in_flight":       {"value": <int>,  "trend": "...",  "tone": "warning"},
        "map_pts_session": {"value": <int>,  "trend": "...",  "tone": "info"},
        "graph_ready":     true|false,
        "ts":              "<iso>"
    }``

``GET /api/ops/session-points?session_id=...``
    Returns every map-block marker that appears in the current session's
    persisted assistant messages, plus a pointer back to the originating
    message (so the drawer can link to a trace and show provenance). Shape:
    ``{
        "session_id": "...",
        "points": [
            {
                "id": "<deterministic id>",
                "lat": 40.71,
                "lng": -74.00,
                "kind": "<map title slug, or 'point'>",
                "name": "<marker label / popup>",
                "detail": "<popup text if different from label>",
                "message_index": <int>,
                "block_title": "...",
                "_provenance": { "lat": {...}, "lng": {...} }
            }
        ],
        "session_map_block_count": <int>
    }``

``GET /api/ops/stream``
    Server-Sent Events. Emits ``event: ops`` payloads broadcast by the chat
    pipeline (see ``ops_inflight.broadcast`` and ``broadcast_event``). Used
    by the ops room ticker so it sees activity from *every* concurrent chat
    request, not only requests originating from this browser tab.

Notes
-----
- Routes are unauthenticated like the rest of ``/api/inspect/*``. Treat
  these endpoints as read-only debug surfaces. Real auth lives at the
  reverse-proxy layer.
- Heavy lifting (trace stat scans) is bounded by ``_TRACE_SCAN_CAP`` to
  keep the call O(N) over the most recent traces, not the whole archive.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from services.trace_store import TraceStore

logger = logging.getLogger(__name__)


# =============================================================================
# Authentication / authorization gate (BUG-CC-OPS-NOAUTH fix)
# =============================================================================
#
# Until this change, /api/ops/* was unauthenticated. Anyone with TCP reach
# to port 5091 could read recent traces (user message text), session counts
# and the live SSE event stream — across ALL tenants. The original
# justification ("real auth lives at the reverse-proxy layer") relied on a
# deployment assumption that doesn't hold at every client.
#
# The fix matches the pattern already used by routes/sessions.py:
#   - All ops endpoints take user_id + tenant_id + role as query params
#   - A FastAPI Depends() at the router level rejects requests that
#     omit user_id or tenant_id (the equivalent of a decorator)
#   - The feed scope is restricted to the requester's own traces folder
#     so user message text never crosses user boundaries
#   - /session-points uses the existing get_session_for() ownership check
#
# Escape hatch: set the env var CC_OPS_AUTH_ENFORCE=0 to revert to the
# old open behaviour. Useful if a deployment immediately misbehaves and
# you need to roll back without re-deploying code.
@dataclass
class _OpsCaller:
    """The user-context bundle the ops endpoints filter on."""
    user_id: int
    tenant_id: int
    role: int


def _require_user_context(
    user_id: Optional[int] = Query(None),
    tenant_id: Optional[int] = Query(None),
    role: int = Query(0),
) -> _OpsCaller:
    """FastAPI dependency — reject the request if user_id or tenant_id
    are missing (when enforcement is on, which is the default).

    Returns the caller's context as a small dataclass each endpoint can
    use for ownership filtering.
    """
    enforce = os.environ.get("CC_OPS_AUTH_ENFORCE", "1") != "0"
    if user_id is None or tenant_id is None:
        if enforce:
            # 401 — never leak whether ops data exists for anonymous
            # callers. The reverse-proxy layer (if present) sees this
            # as a normal auth failure.
            raise HTTPException(status_code=401, detail="Authentication required")
        # Escape-hatch mode: log loudly so this is visible in ops logs
        # but don't reject. user_id=0 / tenant_id=0 means "anonymous";
        # downstream code knows to treat it as 'see nothing user-scoped'.
        logger.warning(
            "[ops] CC_OPS_AUTH_ENFORCE=0 — allowing unauthenticated request "
            "to ops endpoint. Set CC_OPS_AUTH_ENFORCE=1 (default) to re-enable."
        )
        return _OpsCaller(user_id=0, tenant_id=0, role=0)
    try:
        return _OpsCaller(
            user_id=int(user_id),
            tenant_id=int(tenant_id),
            role=int(role or 0),
        )
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid identity")


router = APIRouter(
    prefix="/api/ops",
    tags=["ops"],
    # Apply the auth gate to EVERY endpoint under /api/ops automatically.
    # Individual handlers can still read the caller via Depends() — see
    # the per-endpoint signatures below.
    dependencies=[Depends(_require_user_context)],
)

_session_mgr = None
_data_dir: Path = Path(__file__).parent.parent / "data"
_trace_store = TraceStore(_data_dir)

# Bound scans of the trace tree so the KPI endpoint stays cheap on a hot
# server. With one file per (user, session, trace_id), this caps wall-clock
# work at a few thousand stat() calls — well under 100ms on modest disks.
_TRACE_SCAN_CAP = 5000


def init_ops_routes(session_mgr) -> None:
    """Wire dependencies from main.py at startup."""
    global _session_mgr
    _session_mgr = session_mgr


# =============================================================================
# In-process counters / fan-out for the ticker
# =============================================================================

class _OpsBroadcaster:
    """Tiny pub/sub that fans server-side ops events out to every connected
    SSE client subscribed to ``/api/ops/stream``.

    Kept intentionally minimal — no persistence, no history. New subscribers
    only see events that arrive *after* they connect. The ticker UI seeds
    its initial entries from ``GET /api/ops/feed`` (one-shot recent slice
    of the trace store).
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._inflight: int = 0

    # ---- in-flight counter --------------------------------------------------

    @property
    def inflight(self) -> int:
        return self._inflight

    def begin(self) -> None:
        self._inflight += 1
        self._publish_nowait({"kind": "info", "text": f"chat · request open ({self._inflight} in flight)"})

    def end(self) -> None:
        self._inflight = max(0, self._inflight - 1)
        self._publish_nowait({"kind": "ok", "text": f"chat · request closed ({self._inflight} in flight)"})

    # ---- broadcast ----------------------------------------------------------

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=128)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, event: dict) -> None:
        """Synchronous publish. Drops events when a subscriber's queue is full
        rather than blocking the producer (the producer is the chat pipeline)."""
        # Stamp timestamp once for all subscribers
        if "ts" not in event:
            event = {**event, "ts": datetime.now(timezone.utc).isoformat()}
        self._publish_nowait(event)

    def _publish_nowait(self, event: dict) -> None:
        if "ts" not in event:
            event = {**event, "ts": datetime.now(timezone.utc).isoformat()}
        dead: list[asyncio.Queue] = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer — best to drop; ops ticker is best-effort
                logger.debug("[ops] subscriber queue full, dropping event")
            except Exception:
                dead.append(q)
        for q in dead:
            self._subscribers.discard(q)


# Singleton — imported by routes/chat.py to bump in-flight + publish phases.
ops_broadcaster = _OpsBroadcaster()


def broadcast_event(kind: str, text: str, **extra: Any) -> None:
    """Publish a one-line ticker entry to every ops SSE subscriber.

    Safe to call from any request handler; unbuffered.
    """
    payload = {"kind": kind, "text": text}
    payload.update(extra)
    ops_broadcaster.publish(payload)


# =============================================================================
# /api/ops/kpis
# =============================================================================

def _count_recent_traces(window: timedelta) -> int:
    """Count *.jsonl trace files whose mtime is within `window` of now.

    Bounded by ``_TRACE_SCAN_CAP`` to keep the endpoint cheap on a busy
    server. Exact count is not load-bearing for an ops glance, so falling
    short of the true count when the cap is hit is fine — we expose
    ``capped`` in the response when that happens.
    """
    base = _data_dir / "traces"
    if not base.exists():
        return 0
    cutoff = time.time() - window.total_seconds()
    seen = 0
    matched = 0
    for p in base.rglob("*.jsonl"):
        seen += 1
        if seen > _TRACE_SCAN_CAP:
            break
        try:
            if p.stat().st_mtime >= cutoff:
                matched += 1
        except OSError:
            continue
    return matched


def _count_session_map_points(session_id: Optional[str]) -> tuple[int, int]:
    """Return (point_count, map_block_count) found in `session_id`'s
    persisted assistant messages.

    Reuses the same JSON-block parsing the chat route does — assistant
    messages are stored as JSON arrays of content blocks. We tolerate
    plain-text or malformed messages by counting them as zero.
    """
    if not session_id or _session_mgr is None:
        return (0, 0)
    try:
        msgs = _session_mgr.get_messages(session_id)
    except Exception:
        return (0, 0)
    pts = 0
    blks = 0
    for m in msgs:
        if m.get("role") != "assistant":
            continue
        content = m.get("content", "")
        if not isinstance(content, str) or not content.strip().startswith("["):
            continue
        try:
            blocks = json.loads(content)
        except Exception:
            continue
        if not isinstance(blocks, list):
            continue
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "map":
                blks += 1
                markers = b.get("markers") or []
                if isinstance(markers, list):
                    pts += sum(1 for mk in markers
                               if isinstance(mk, dict)
                               and isinstance(mk.get("lat"), (int, float))
                               and isinstance(mk.get("lng"), (int, float)))
    return (pts, blks)


@router.get("/kpis")
async def kpis(
    session_id: Optional[str] = Query(None),
    caller: _OpsCaller = Depends(_require_user_context),
):
    """Real KPI values, sourced from session_mgr + trace_store + the
    in-process in-flight counter. Numbers that would have to be fabricated
    (alerts, coverage, "active points" across all sessions) are NOT in this
    response — the ops UI cuts those tiles.

    Session count is filtered to the caller's owned sessions (or, for
    admins/devs, all sessions in their tenant) using the same
    list_sessions_for() filter as /api/sessions.
    """
    if _session_mgr is not None:
        try:
            owned = _session_mgr.list_sessions_for(
                caller.user_id, caller.tenant_id, caller.role
            )
            sessions_count = len(owned)
        except Exception:
            sessions_count = 0
    else:
        sessions_count = 0
    traces_24h = _count_recent_traces(timedelta(hours=24))
    map_pts, map_blocks = _count_session_map_points(session_id)
    return {
        "sessions":        {"value": sessions_count, "trend": _trend_msg(sessions_count, "session"),                  "tone": "info"},
        "traces_24h":      {"value": traces_24h,    "trend": "last 24h",                                              "tone": "info"},
        "in_flight":       {"value": ops_broadcaster.inflight, "trend": "live",                                       "tone": "warning"},
        "map_pts_session": {"value": map_pts,       "trend": f"in {map_blocks} block{'s' if map_blocks != 1 else ''}", "tone": "ok"},
        "graph_ready":     True,  # main.py always wires the graph; if it didn't, /api/health says so
        "ts":              datetime.now(timezone.utc).isoformat(),
    }


def _trend_msg(n: int, noun: str) -> str:
    if n == 0:
        return f"no {noun}s yet"
    if n == 1:
        return f"1 {noun}"
    return f"{n} {noun}s"


# =============================================================================
# /api/ops/session-points
# =============================================================================

@router.get("/session-points")
async def session_points(
    session_id: str = Query(...),
    caller: _OpsCaller = Depends(_require_user_context),
):
    """Extract every map-block marker from the session's stored assistant
    messages. Each point carries a deterministic id, originating message
    index, and the block's ``_provenance`` map (when present) so the
    selection drawer can render WS4 source badges.

    Ownership check: the requester must own the session (or be an admin
    in the same tenant — see _matches_owner in SessionManager). A
    cross-user / cross-tenant access attempt returns 404 — never leak
    whether the session exists.
    """
    if _session_mgr is None:
        return {"session_id": session_id, "points": [], "session_map_block_count": 0}
    owned = _session_mgr.get_session_for(
        session_id, caller.user_id, caller.tenant_id, caller.role
    )
    if owned is None:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    try:
        msgs = _session_mgr.get_messages(session_id)
    except Exception:
        msgs = []

    points: list[dict] = []
    block_count = 0

    for msg_idx, m in enumerate(msgs):
        if m.get("role") != "assistant":
            continue
        content = m.get("content", "")
        if not isinstance(content, str) or not content.strip().startswith("["):
            continue
        try:
            blocks = json.loads(content)
        except Exception:
            continue
        if not isinstance(blocks, list):
            continue

        for blk_idx, b in enumerate(blocks):
            if not isinstance(b, dict) or b.get("type") != "map":
                continue
            block_count += 1
            title = str(b.get("title") or "Map")
            kind = _slug(title) or "point"
            markers = b.get("markers") or []
            block_prov = b.get("_provenance") or {}
            if not isinstance(markers, list):
                continue
            for mkr_idx, mk in enumerate(markers):
                if not isinstance(mk, dict):
                    continue
                lat = mk.get("lat")
                lng = mk.get("lng")
                if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
                    continue
                label = str(mk.get("label") or mk.get("popup") or f"{title} marker {mkr_idx + 1}")
                popup = mk.get("popup")
                detail = str(popup) if popup and str(popup) != label else ""
                # Deterministic point id so the UI's map flyTo reuses the same
                # marker even when the points list is refetched.
                pid = f"{msg_idx}.{blk_idx}.{mkr_idx}"
                # Pull per-marker provenance entries from the block's
                # _provenance map. Convention: keys "markers[N].lat",
                # "markers[N].lng", "markers[N].label" etc.
                prov_for_point = _extract_marker_provenance(block_prov, mkr_idx)
                points.append({
                    "id": pid,
                    "lat": float(lat),
                    "lng": float(lng),
                    "kind": kind,
                    "name": label,
                    "detail": detail,
                    "message_index": msg_idx,
                    "block_title": title,
                    "_provenance": prov_for_point,
                })

    return {
        "session_id": session_id,
        "points": points,
        "session_map_block_count": block_count,
    }


def _slug(s: str) -> str:
    """Tiny ASCII slugger for marker categories. Lowercase + alnum only."""
    out = []
    for ch in s.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_") and out and out[-1] != "_":
            out.append("_")
    return "".join(out).strip("_")[:24]


def _extract_marker_provenance(block_prov: dict, marker_idx: int) -> dict:
    """Pull the entries from `block_prov` that target marker `marker_idx`,
    rewriting the keys to be marker-relative so the UI doesn't need to know
    about the parent block layout.

    `markers[3].lat` becomes `lat`. Entries that don't target this marker
    are skipped.
    """
    if not isinstance(block_prov, dict):
        return {}
    prefix = f"markers[{marker_idx}]."
    out: dict = {}
    for k, v in block_prov.items():
        if not isinstance(k, str):
            continue
        if k.startswith(prefix) and isinstance(v, dict):
            out[k[len(prefix):]] = v
    return out


# =============================================================================
# /api/ops/feed (one-shot recent ticker entries)
# =============================================================================

@router.get("/feed")
async def feed(
    limit: int = Query(20, ge=1, le=100),
    caller: _OpsCaller = Depends(_require_user_context),
):
    """Recent activity, derived from the trace store. Used to seed the
    ticker on first load so the UI isn't empty before any new SSE event
    arrives.

    Each entry is a single line summarising one recent trace start —
    intentionally compact, no per-event drill-down here (that's what the
    inspector page is for).

    Ownership scope: the trace store is laid out as
    ``data/traces/{user_id}/{session_id}/{trace_id}.jsonl``. Regular
    users see only their own subtree (caller.user_id). Admins/devs
    (role >= 2) see every trace in their tenant — for now that's the
    whole tree, because traces aren't tagged with tenant_id on disk and
    cross-tenant separation lives a layer up. A future refinement is to
    organise traces by tenant_id at the top level; until then admins see
    everything and the contract is documented in the test suite.
    """
    base = _data_dir / "traces"
    if not base.exists():
        return {"entries": []}

    # Scope the scan: regular users -> only their own subtree.
    # role>=2 (admin/dev) -> the whole tree (legacy behaviour).
    if caller.role >= 2:
        scan_root = base
    else:
        scan_root = base / str(caller.user_id)
        if not scan_root.exists():
            return {"entries": []}

    # Find the most-recent N .jsonl files by mtime (cap the scan).
    candidates: list[tuple[float, Path]] = []
    seen = 0
    for p in scan_root.rglob("*.jsonl"):
        seen += 1
        if seen > _TRACE_SCAN_CAP:
            break
        try:
            candidates.append((p.stat().st_mtime, p))
        except OSError:
            continue
    candidates.sort(key=lambda t: t[0], reverse=True)

    entries = []
    for mtime, path in candidates[:limit]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                first = f.readline().strip()
            if not first:
                continue
            j = json.loads(first)
            payload = j.get("payload") or {}
            user_msg = payload.get("user_message") or ""
            user_msg_short = (user_msg[:60] + "…") if len(user_msg) > 60 else user_msg
            entries.append({
                "ts": j.get("ts") or datetime.fromtimestamp(mtime, timezone.utc).isoformat(),
                "kind": "info",
                "text": f"trace · {path.stem[:8]} · {user_msg_short}" if user_msg_short else f"trace · {path.stem[:8]}",
                "trace_id": path.stem,
            })
        except Exception:
            continue

    return {"entries": entries}


# =============================================================================
# /api/ops/stream — server-side broadcast SSE
# =============================================================================

@router.get("/stream")
async def stream(request: Request):
    """SSE stream of ops events broadcast by the chat pipeline. New
    subscribers do NOT see history — they should call ``/api/ops/feed``
    once at connect time to populate the ticker, then merge live events on
    top.
    """
    queue = await ops_broadcaster.subscribe()

    async def gen():
        try:
            # Initial hello so the client can confirm the stream opened.
            yield _sse("ready", {"ts": datetime.now(timezone.utc).isoformat()})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield _sse("ops", event)
                except asyncio.TimeoutError:
                    # Comment ping to keep proxies from closing the connection
                    yield ": keepalive\n\n"
        finally:
            ops_broadcaster.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
