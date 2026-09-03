"""
The Agent — maps (pass 4 of the CC-gap plan, 2026-09-02).

render_map builds a ready-made ```aihub-map``` block the chat renders with
Leaflet (vendored, offline-safe; tiles come from OpenStreetMap when the box
is online, the drawing works without them). Two layers, combinable:

  markers  — points: {lat, lng, label, popup}. A point WITHOUT coordinates
             ({place: "Newark, NJ"} or a bare "New Jersey") is ENRICHED here:
             a US state name/code resolves to the state's centroid from the
             bundled GeoJSON (offline), anything else is geocoded through
             OpenStreetMap's Nominatim (online; honest failure when it is
             not reachable). Enriched points are flagged in the result so the
             model can say "approximate".
  regions  — a US-state choropleth: {name, value, label}. Names are
             normalized against the bundled GeoJSON — full names, USPS codes
             ("NJ"), "Washington DC" variants — and anything that is not a
             US state is reported back as unmapped instead of dropped.

geocode_places is the same geocoder as a standalone lookup ("what are the
coordinates of …") — 1 request/second, cached, capped, User-Agent set, per
Nominatim's usage policy. AGENT_GEOCODING=false turns it off (offline
installs) and the tools say so honestly.

The block keys match Command Center's map block (center, zoom, markers,
regions, unmapped, title) so the model's CC-era habits carry over.
"""

import asyncio
import json
import os
import re
import time
from typing import Any, Optional

import httpx

from claude_agent_sdk import tool

from agent_config import APP_ROOT, logger
from platform_tools import CURRENT_USER, _text
import rich_blocks

GEOJSON_PATH = os.path.join(APP_ROOT, "agent_service", "static", "data", "us-states.geojson")
GEOCODE_URL = os.getenv("AGENT_GEOCODER_URL", "https://nominatim.openstreetmap.org/search")
GEOCODING_ON = os.getenv("AGENT_GEOCODING", "true").lower() == "true"
GEOCODE_UA = os.getenv("AGENT_GEOCODER_USER_AGENT", "AIHub-TheAgent/2.0 (maps)")
MAX_MARKERS = 200
MAX_GEOCODE = 25
MAX_REGIONS = 60

# USPS codes -> GeoJSON names (the bundled file carries the 50 states, DC and
# Puerto Rico). Aliases cover the spellings people actually type.
STATE_CODES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
    "PR": "Puerto Rico",
}
STATE_ALIASES = {
    "washington dc": "District of Columbia", "washington d.c.": "District of Columbia",
    "d.c.": "District of Columbia", "district of columbia": "District of Columbia",
    "washington, dc": "District of Columbia", "washington state": "Washington",
    "new york state": "New York",
}

_geo_cache: dict = {}          # name(lower) -> feature
_geo_loaded = False
_geocode_cache: dict = {}      # query(lower) -> (lat, lng, display) | None
_last_geocode_at = 0.0


# ---------------------------------------------------------------------------
# GeoJSON (states) — names + centroids, offline
# ---------------------------------------------------------------------------

def _load_geo() -> dict:
    global _geo_loaded
    if _geo_loaded:
        return _geo_cache
    _geo_loaded = True
    try:
        with open(GEOJSON_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        for f in data.get("features") or []:
            name = str((f.get("properties") or {}).get("name") or "").strip()
            if name:
                _geo_cache[name.lower()] = f
    except Exception as e:
        logger.warning(f"map_tools: GeoJSON unavailable: {e}")
    return _geo_cache


def state_names() -> list:
    return sorted(f["properties"]["name"] for f in _load_geo().values())


def normalize_state(raw) -> Optional[str]:
    """A US state/territory name as the GeoJSON spells it, or None."""
    s = " ".join(str(raw or "").strip().split())
    if not s:
        return None
    key = s.lower()
    if key in STATE_ALIASES:
        return STATE_ALIASES[key]
    if s.upper() in STATE_CODES and len(s) == 2:
        return STATE_CODES[s.upper()]
    geo = _load_geo()
    if key in geo:
        return geo[key]["properties"]["name"]
    key2 = re.sub(r"\s+state$", "", key)
    if key2 in geo:
        return geo[key2]["properties"]["name"]
    # "US-NJ", "NJ, USA", "New Jersey, United States"
    m = re.match(r"^(?:us-)?([a-z]{2})(?:,\s*(?:usa|us|united states))?$", key)
    if m and m.group(1).upper() in STATE_CODES:
        return STATE_CODES[m.group(1).upper()]
    m = re.match(r"^(.+?),\s*(?:usa|us|united states)$", key)
    if m and m.group(1) in geo:
        return geo[m.group(1)]["properties"]["name"]
    return None


def _walk_coords(geom):
    t = geom.get("type")
    c = geom.get("coordinates") or []
    if t == "Polygon":
        for ring in c:
            for pt in ring:
                yield pt
    elif t == "MultiPolygon":
        for poly in c:
            for ring in poly:
                for pt in ring:
                    yield pt


def state_centroid(name: str) -> Optional[tuple]:
    """(lat, lng) — bounding-box centre of the state's geometry (offline)."""
    f = _load_geo().get(str(name or "").lower())
    if not f:
        return None
    lngs, lats = [], []
    for pt in _walk_coords(f.get("geometry") or {}):
        try:
            lngs.append(float(pt[0]))
            lats.append(float(pt[1]))
        except (TypeError, ValueError, IndexError):
            continue
    if not lats:
        return None
    return (round((min(lats) + max(lats)) / 2, 4), round((min(lngs) + max(lngs)) / 2, 4))


def coerce_value(v) -> Optional[float]:
    """'$1.2M' -> 1200000.0; '12%' -> 12.0; None/'' -> None."""
    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("$", "").replace("%", "")
    mult = 1.0
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*([kKmMbB])?$", s)
    if not m:
        return None
    if m.group(2):
        mult = {"k": 1e3, "m": 1e6, "b": 1e9}[m.group(2).lower()]
    return float(m.group(1)) * mult


def auto_view(lats: list, lngs: list, has_regions: bool) -> tuple:
    """(center, zoom) — Command Center's heuristic."""
    if lats:
        lat_spread = max(lats) - min(lats) if len(lats) > 1 else 0
        lng_spread = max(lngs) - min(lngs) if len(lngs) > 1 else 0
        spread = max(lat_spread, lng_spread)
        zoom = 3 if spread > 40 else 4 if spread > 20 else 5 if spread > 10 else \
            6 if spread > 5 else 7 if spread > 2 else 9 if spread > 0.5 else 11
        return [round(sum(lats) / len(lats), 4), round(sum(lngs) / len(lngs), 4)], zoom
    if has_regions:
        return [39.8, -98.5], 4
    return [20.0, 0.0], 2


# ---------------------------------------------------------------------------
# Geocoding (online, polite)
# ---------------------------------------------------------------------------

async def geocode(place: str) -> Optional[dict]:
    """{lat, lng, display} via Nominatim, cached; None when not found or the
    geocoder is off/unreachable (the caller reports which)."""
    global _last_geocode_at
    q = " ".join(str(place or "").split())
    if not q:
        return None
    key = q.lower()
    if key in _geocode_cache:
        return _geocode_cache[key]
    if not GEOCODING_ON:
        return None
    wait = 1.05 - (time.monotonic() - _last_geocode_at)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_geocode_at = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=20.0)) as client:
            r = await client.get(GEOCODE_URL, params={"q": q, "format": "json", "limit": 1},
                                 headers={"User-Agent": GEOCODE_UA, "Accept-Language": "en"})
            rows = r.json() if r.status_code == 200 else []
    except Exception as e:
        logger.warning(f"geocode failed for {q!r}: {e}")
        raise
    hit = None
    if isinstance(rows, list) and rows:
        try:
            hit = {"lat": round(float(rows[0]["lat"]), 5), "lng": round(float(rows[0]["lon"]), 5),
                   "display": str(rows[0].get("display_name") or q)[:160]}
        except (KeyError, TypeError, ValueError):
            hit = None
    _geocode_cache[key] = hit
    return hit


# ---------------------------------------------------------------------------
# Block building (pure given resolved points)
# ---------------------------------------------------------------------------

def build_regions(raw_regions: list) -> tuple:
    """(regions, unmapped, no_value) — normalized choropleth rows. `regions`
    KEEPS the unmatched rows under their original names (after the matched
    ones): the chat's renderer flags anything it cannot match to a state, so
    the "not shown" note survives even when the model re-types the block and
    drops the separate `unmapped` key (seen live 2026-09-02)."""
    regions, unmapped, no_value = [], [], []
    keep: list = []
    for r in (raw_regions or [])[:MAX_REGIONS]:
        if not isinstance(r, dict):
            continue
        name_in = str(r.get("name") or r.get("state") or r.get("region") or "").strip()
        if not name_in:
            continue
        name = normalize_state(name_in)
        if not name:
            unmapped.append(name_in)
            uval = coerce_value(r.get("value"))
            keep.append({"name": name_in, "value": uval if uval is not None else 0,
                         "label": str(r.get("label") or f"{name_in}: {r.get('value', '')}")[:120]})
            continue
        val = coerce_value(r.get("value"))
        if val is None:
            no_value.append(name)
        label = str(r.get("label") or f"{name}: {r.get('value', '')}")[:120]
        regions.append({"name": name, "value": val if val is not None else 0, "label": label})
    return regions + keep, unmapped, no_value


def map_spec(title: str, markers: list, regions: list, unmapped: list,
             center: Optional[list] = None, zoom: Optional[int] = None) -> dict:
    lats = [m["lat"] for m in markers]
    lngs = [m["lng"] for m in markers]
    auto_center, auto_zoom = auto_view(lats, lngs, bool(regions))
    spec: dict[str, Any] = {"title": str(title or "")[:80],
                            "center": center or auto_center, "zoom": int(zoom or auto_zoom)}
    if markers:
        spec["markers"] = markers
    if regions:
        spec["regions"] = regions
    if unmapped:
        spec["unmapped"] = unmapped
    return spec


def map_block(title: str, markers: list, regions: list, unmapped: list,
              center: Optional[list] = None, zoom: Optional[int] = None,
              uid: Optional[int] = None) -> str:
    """The fence for a map: a stored REFERENCE when a user is known (the model
    pastes 3 short lines it cannot corrupt), the inline spec otherwise."""
    spec = map_spec(title, markers, regions, unmapped, center, zoom)
    if uid is not None:
        return rich_blocks.ref_fence(int(uid), "map", spec)
    return rich_blocks.fence("map", spec)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool(
    "render_map",
    "Build an interactive MAP for the chat (Leaflet) and return a block to paste "
    "into your reply VERBATIM. Two layers, combinable: markers_json — points "
    "[{\"lat\":40.7,\"lng\":-74.0,\"label\":\"Newark\",\"popup\":\"Sales $1M\"}]; a "
    "point WITHOUT coordinates is ENRICHED here: give {\"place\":\"Newark, NJ\", "
    "\"label\":…} and it is geocoded, or a US state name/code and its centroid "
    "is used (offline). regions_json — a US-state choropleth "
    "[{\"name\":\"NJ\",\"value\":120500,\"label\":\"NJ: $120.5K\"}] (names, USPS "
    "codes and DC variants all work; non-US names are reported as unmapped, "
    "never silently dropped). Values MUST come from a tool result or the "
    "user's message. The result says exactly what was placed, what was "
    "geocoded (call those approximate) and what could not be mapped.",
    {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "markers_json": {"type": "string", "description": "JSON array of points"},
            "regions_json": {"type": "string", "description": "JSON array of US-state rows"},
            "center_lat": {"type": "number"},
            "center_lng": {"type": "number"},
            "zoom": {"type": "integer", "description": "1-18 (auto when omitted)"},
        },
        "required": ["title"],
        "additionalProperties": False,
    },
)
async def render_map(args: dict[str, Any]) -> dict[str, Any]:
    try:
        raw_markers = json.loads(args["markers_json"]) if args.get("markers_json") else []
        raw_regions = json.loads(args["regions_json"]) if args.get("regions_json") else []
    except Exception as e:
        return _text(f"markers_json / regions_json must be valid JSON ({e}).", is_error=True)
    if isinstance(raw_regions, dict):
        raw_regions = raw_regions.get("regions") or []
    if isinstance(raw_markers, dict):
        raw_markers = raw_markers.get("markers") or []
    if not isinstance(raw_markers, list) or not isinstance(raw_regions, list):
        return _text("markers_json must be a JSON array of points and regions_json a JSON "
                     "array of state rows.", is_error=True)
    if not raw_markers and not raw_regions:
        return _text("Give me markers_json (points) and/or regions_json (US states) — "
                     "nothing to map.", is_error=True)

    markers, geocoded, failed, geocoder_down = [], [], [], False
    geocode_budget = MAX_GEOCODE
    for m in raw_markers[:MAX_MARKERS]:
        if isinstance(m, str):
            m = {"place": m}
        if not isinstance(m, dict):
            continue
        label = str(m.get("label") or m.get("name") or m.get("place") or "")[:80]
        popup = str(m.get("popup") or label)[:300]
        lat, lng = m.get("lat"), m.get("lng", m.get("lon", m.get("long")))
        try:
            if lat not in (None, "") and lng not in (None, ""):
                markers.append({"lat": float(lat), "lng": float(lng), "label": label, "popup": popup})
                continue
        except (TypeError, ValueError):
            pass
        place = str(m.get("place") or m.get("location") or m.get("address")
                    or m.get("city") or label).strip()
        if not place:
            failed.append("(a point with no coordinates and no place)")
            continue
        st = normalize_state(place)
        if st and state_centroid(st):
            la, ln = state_centroid(st)
            markers.append({"lat": la, "lng": ln, "label": label or st,
                            "popup": popup or st})
            geocoded.append(f"{place} -> centre of {st} ({la}, {ln})")
            continue
        if geocode_budget <= 0:
            failed.append(f"{place} (geocoding budget of {MAX_GEOCODE} per call exhausted)")
            continue
        geocode_budget -= 1
        try:
            hit = await geocode(place)
        except Exception:
            geocoder_down = True
            hit = None
        if hit:
            markers.append({"lat": hit["lat"], "lng": hit["lng"], "label": label or place,
                            "popup": popup or hit["display"]})
            geocoded.append(f"{place} -> {hit['display'][:60]} ({hit['lat']}, {hit['lng']})")
        else:
            failed.append(place)

    regions, unmapped, no_value = build_regions(raw_regions)
    if not markers and len(regions) == len(unmapped):
        why = ("the geocoder is disabled on this install (AGENT_GEOCODING=false)"
               if not GEOCODING_ON else
               "the geocoder could not be reached" if geocoder_down else
               "no point had coordinates and no name could be placed")
        return _text("Nothing could be placed on a map: " + why
                     + (f". Unmapped regions: {', '.join(unmapped)}" if unmapped else "")
                     + (f". Unplaced points: {', '.join(failed)}" if failed else "")
                     + ". Give coordinates (lat/lng) for the points, or US state names "
                       "for a choropleth.", is_error=True)

    center = None
    if args.get("center_lat") is not None and args.get("center_lng") is not None:
        center = [float(args["center_lat"]), float(args["center_lng"])]
    zoom = int(args["zoom"]) if args.get("zoom") else None
    uid = int((CURRENT_USER.get() or {}).get("user_id") or 0)
    block = map_block(str(args.get("title") or "Map"), markers, regions, unmapped, center, zoom,
                      uid=uid)

    lines = [f"Map ready — paste the 3-line block below into your reply EXACTLY as it is "
             f"(it is a reference; the map data is stored server-side, so retyping it "
             f"would break the map). {len(markers)} marker(s), "
             f"{len(regions) - len(unmapped)} shaded state(s)."]
    if geocoded:
        lines.append("ENRICHED — these positions were looked up, not given; your reply "
                     "MUST say they are approximate: "
                     + "; ".join(geocoded[:12]) + (" …" if len(geocoded) > 12 else ""))
    if failed:
        lines.append("NOT placed (tell the user): " + ", ".join(failed[:12])
                     + (" …" if len(failed) > 12 else "")
                     + (" — the geocoder is disabled on this install; give lat/lng instead."
                        if not GEOCODING_ON else
                        " — the geocoder could not be reached; give lat/lng instead."
                        if geocoder_down else ""))
    if unmapped:
        lines.append("Not US states, so not shaded (the map notes them too): "
                     + ", ".join(unmapped[:12]))
    if no_value:
        lines.append("No numeric value for: " + ", ".join(no_value[:12])
                     + " — those states show without shading.")
    return _text("\n".join(lines) + "\n" + block)


@tool(
    "geocode_places",
    "Look up coordinates for place names — cities, addresses, landmarks, "
    "countries — through OpenStreetMap's geocoder (also used by render_map "
    "automatically). Returns lat/lng and the matched place so you can confirm "
    "it is the right one. Up to 25 places per call; US state names resolve "
    "offline to their centre. Say clearly when a place could not be found.",
    {
        "type": "object",
        "properties": {"places": {"type": "array", "items": {"type": "string"}}},
        "required": ["places"],
        "additionalProperties": False,
    },
)
async def geocode_places(args: dict[str, Any]) -> dict[str, Any]:
    places = [str(p).strip() for p in (args.get("places") or []) if str(p).strip()]
    if not places:
        return _text("Give me at least one place name.", is_error=True)
    if len(places) > MAX_GEOCODE:
        return _text(f"At most {MAX_GEOCODE} places per call.", is_error=True)
    lines, down = [], False
    for p in places:
        st = normalize_state(p)
        if st and state_centroid(st):
            la, ln = state_centroid(st)
            lines.append(f"- {p}: centre of {st} ({la}, {ln}) [offline, approximate]")
            continue
        if not GEOCODING_ON:
            lines.append(f"- {p}: geocoding is disabled on this install")
            continue
        try:
            hit = await geocode(p)
        except Exception:
            down = True
            hit = None
        if hit:
            lines.append(f"- {p}: ({hit['lat']}, {hit['lng']}) — {hit['display']}")
        else:
            lines.append(f"- {p}: NOT FOUND" + (" (geocoder unreachable)" if down else ""))
    return _text("Geocoding results:\n" + "\n".join(lines))


MAP_TOOLS = [render_map, geocode_places]
