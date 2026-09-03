"""Unit pack for pass 4 (2026-09-02): maps (agent_service/map_tools.py) and
image generation (agent_service/image_tools.py).

Network is never touched: the geocoder and the OpenAI call are monkeypatched.
The state GeoJSON is the real vendored file. Runs standalone or under pytest;
self-skips without the SDK.
"""
import asyncio
import base64
import json
import os
import re
import sys
import tempfile
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import map_tools as M                      # noqa: E402
    import image_tools as I                    # noqa: E402
    from platform_tools import CURRENT_USER    # noqa: E402
    HAVE_SDK = True
except ImportError as e:
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(
            reason=f"needs the aihub-agent env (claude_agent_sdk): {_IMPORT_ERR}")
    except ImportError:
        pass

PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
INDEX = os.path.join(APP_ROOT, "agent_service", "static", "index.html")


def _run(coro):
    return asyncio.run(coro)


def _txt(res):
    return res["content"][0]["text"]


def _block(text, uid=7):
    """The map spec behind a reply: a {"ref"} fence resolves through the store."""
    import rich_blocks
    m = re.search(r"```aihub-map\n(.*?)\n```", text, re.S)
    assert m, text
    spec = json.loads(m.group(1))
    if "ref" in spec:
        hit = rich_blocks.get_block(uid, spec["ref"])
        assert hit and hit["kind"] == "map", spec
        return hit["spec"]
    return spec


# ---------------------------------------------------------------------------
# Maps — enrichment helpers
# ---------------------------------------------------------------------------

def test_normalize_state_names_codes_and_aliases():
    assert M.normalize_state("NJ") == "New Jersey"
    assert M.normalize_state("nj") == "New Jersey"
    assert M.normalize_state("new jersey") == "New Jersey"
    assert M.normalize_state("New York State") == "New York"
    assert M.normalize_state("Washington DC") == "District of Columbia"
    assert M.normalize_state("US-CA") == "California"
    assert M.normalize_state("Texas, USA") == "Texas"
    assert M.normalize_state("Puerto Rico") == "Puerto Rico"
    assert M.normalize_state("Ontario") is None
    assert M.normalize_state("") is None
    assert len(M.state_names()) >= 50


def test_state_centroid_is_inside_the_state_box():
    lat, lng = M.state_centroid("New Jersey")
    assert 38.5 < lat < 41.5 and -76 < lng < -73
    lat, lng = M.state_centroid("Texas")
    assert 28 < lat < 34 and -102 < lng < -96
    assert M.state_centroid("Atlantis") is None


def test_coerce_value_and_auto_view():
    assert M.coerce_value("$1.2M") == 1200000.0
    assert M.coerce_value("12%") == 12.0
    assert M.coerce_value("1,234") == 1234.0
    assert M.coerce_value(7) == 7.0
    assert M.coerce_value("abc") is None and M.coerce_value(None) is None and M.coerce_value(True) is None
    center, zoom = M.auto_view([40.7, 30.3], [-74.0, -97.7], False)
    assert center == [35.5, -85.85] and zoom == 4
    assert M.auto_view([], [], True) == ([39.8, -98.5], 4)
    assert M.auto_view([40.7], [-74.0], False)[1] == 11


def test_build_regions_normalizes_and_reports_unmapped():
    regions, unmapped, no_value = M.build_regions([
        {"name": "NJ", "value": "120,500", "label": "NJ: $120.5K"},
        {"name": "california", "value": 75000},
        {"state": "Ontario", "value": 5},
        {"name": "Texas"},
        {"name": ""},
    ])
    assert [r["name"] for r in regions] == ["New Jersey", "California", "Texas", "Ontario"]
    assert regions[0]["value"] == 120500.0 and regions[0]["label"] == "NJ: $120.5K"
    assert regions[1]["label"] == "California: 75000"
    assert regions[3] == {"name": "Ontario", "value": 5.0, "label": "Ontario: 5"}   # kept for the renderer's note
    assert unmapped == ["Ontario"] and no_value == ["Texas"]


# ---------------------------------------------------------------------------
# render_map tool (geocoder mocked)
# ---------------------------------------------------------------------------

def test_block_store_is_per_user_and_validates_ids():
    import rich_blocks
    bid = rich_blocks.store_block(7, "map", {"title": "t", "markers": []})
    assert re.match(r"^[0-9a-f]{16}$", bid)
    assert rich_blocks.get_block(7, bid) == {"kind": "map", "spec": {"title": "t", "markers": []}}
    assert rich_blocks.get_block(8, bid) is None                      # another user's block
    assert rich_blocks.get_block(7, "../etc") is None and rich_blocks.get_block(7, "") is None
    f = rich_blocks.ref_fence(7, "chart", {"type": "bar"})
    assert f.startswith("```aihub-chart\n{\"ref\": \"") and f.endswith("\"}\n```")


def test_render_map_places_points_enriches_and_reports():
    calls = []
    tok = CURRENT_USER.set({"user_id": 7, "role": 2, "username": "dev"})

    async def fake_geocode(place):
        calls.append(place)
        if "newark" in place.lower():
            return {"lat": 40.7357, "lng": -74.1724, "display": "Newark, Essex County, New Jersey, USA"}
        return None

    with mock.patch.object(M, "geocode", fake_geocode):
        res = _run(M.render_map.handler({
            "title": "Stores",
            "markers_json": json.dumps([
                {"lat": 30.27, "lng": -97.74, "label": "Austin", "popup": "2 stores"},
                {"place": "Newark, NJ", "label": "Newark"},
                "Texas",
                {"place": "Nowhere Special 12345"},
            ]),
            "regions_json": json.dumps([{"name": "NJ", "value": 120500}, {"name": "Ontario", "value": 1}]),
        }))
        assert not res.get("is_error"), _txt(res)
        out = _txt(res)
        spec = _block(out)
        assert len(spec["markers"]) == 3                      # Austin, Newark (geocoded), Texas (centroid)
        assert spec["markers"][1]["lat"] == 40.7357 and spec["markers"][1]["label"] == "Newark"
        assert 28 < spec["markers"][2]["lat"] < 34 and spec["markers"][2]["label"] == "Texas"
        assert spec["regions"][0]["name"] == "New Jersey" and spec["unmapped"] == ["Ontario"]
        assert [r["name"] for r in spec["regions"]] == ["New Jersey", "Ontario"]
        assert spec["title"] == "Stores" and len(spec["center"]) == 2 and spec["zoom"] >= 1
        assert "approximate" in out and "Newark, NJ ->" in out and "Texas -> centre of Texas" in out
        assert "NOT placed" in out and "Nowhere Special 12345" in out
        assert "Not US states" in out and "Ontario" in out
        assert calls == ["Newark, NJ", "Nowhere Special 12345"]  # states never hit the geocoder
        assert '{"ref": "' in out                                   # a reference, not the data
    CURRENT_USER.reset(tok)


def test_render_map_honest_when_nothing_can_be_placed_and_when_geocoder_is_down():
    async def down(place):
        raise RuntimeError("no network")

    with mock.patch.object(M, "geocode", down):
        res = _run(M.render_map.handler({"title": "x", "markers_json": json.dumps([{"place": "Paris"}])}))
        assert res.get("is_error") and "could not be reached" in _txt(res)
        res = _run(M.render_map.handler({"title": "x", "markers_json": json.dumps([{"place": "Paris"}, "NJ"])}))
        assert not res.get("is_error")
        assert "NOT placed (tell the user): Paris" in _txt(res) and "geocoder could not be reached" in _txt(res)
    res = _run(M.render_map.handler({"title": "x"}))
    assert res.get("is_error") and "nothing to map" in _txt(res)
    res = _run(M.render_map.handler({"title": "x", "markers_json": "not json"}))
    assert res.get("is_error") and "valid JSON" in _txt(res)
    with mock.patch.object(M, "GEOCODING_ON", False):
        res = _run(M.render_map.handler({"title": "x", "markers_json": json.dumps([{"place": "Paris"}])}))
        assert res.get("is_error") and "disabled" in _txt(res)


def test_geocode_places_tool():
    async def fake_geocode(place):
        return {"lat": 48.85, "lng": 2.35, "display": "Paris, France"} if place == "Paris" else None

    with mock.patch.object(M, "geocode", fake_geocode):
        res = _run(M.geocode_places.handler({"places": ["Paris", "Nowhere 99", "NJ"]}))
        out = _txt(res)
        assert "Paris: (48.85, 2.35) — Paris, France" in out
        assert "Nowhere 99: NOT FOUND" in out
        assert "NJ: centre of New Jersey" in out and "[offline" in out
    res = _run(M.geocode_places.handler({"places": []}))
    assert res.get("is_error")


# ---------------------------------------------------------------------------
# Image generation
# ---------------------------------------------------------------------------

def test_image_families_sizes_and_request_shape():
    assert I.model_family("gpt-image-2") == "gpt-image" and I.model_family("dall-e-3") == "dall-e-3"
    assert I.normalize_size("gpt-image-2", "portrait") == "1024x1536"
    assert I.normalize_size("gpt-image-2", "1792x1024") == "1536x1024"        # closest orientation
    assert I.normalize_size("dall-e-3", "landscape") == "1792x1024"
    assert I.normalize_size("dall-e-2", "landscape") == "1024x1024"
    assert I.normalize_size("dall-e-3", "garbage") == "1024x1024"
    b = I.build_request("gpt-image-2", "a cat", "square")
    assert b == {"model": "gpt-image-2", "prompt": "a cat", "n": 1, "size": "1024x1024"}
    b = I.build_request("dall-e-3", "a cat", "wide")
    assert b["response_format"] == "b64_json" and b["size"] == "1792x1024"


def test_resolve_openai_key_order():
    d = tempfile.mkdtemp()
    with mock.patch.dict(os.environ, {"AIHUB_DATA_DIR": d, "OPENAI_API_KEY": "", "OPENAI_API_KEY_ENCRYPTED": ""}):
        assert I.resolve_openai_key() == ("", "none")
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env"}):
            assert I.resolve_openai_key() == ("sk-env", "env")
        open(os.path.join(d, "byok_config.json"), "w").write('{"byok_enabled": true}')
        fake_ls = mock.MagicMock()
        fake_ls.get_local_secret = lambda name: "sk-byok" if name == "USER_OPENAI_API_KEY" else ""
        with mock.patch.dict(sys.modules, {"local_secrets": fake_ls}), \
             mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env"}):
            assert I.resolve_openai_key() == ("sk-byok", "byok")          # BYOK beats env


def test_generate_image_success_and_failures():
    tok = CURRENT_USER.set({"user_id": 7, "role": 2, "username": "dev"})
    staged = []

    def fake_stage(uid, path, name=None):
        staged.append((uid, os.path.basename(path), name))
        return True, f"[⤓ {name} (1.0 KB)](/api/files/0f1e2d3c-1111-2222-3333-444455556666)", path

    async def ok_call(key, body, timeout=180.0):
        assert key == "sk-test" and body["model"] == I.DEFAULT_MODEL and body["n"] == 1
        return 200, {"data": [{"b64_json": base64.b64encode(PNG_1x1).decode(), "revised_prompt": "a red bicycle"}]}

    async def bad_call(key, body, timeout=180.0):
        return 400, {"error": {"message": "Your request was rejected by the safety system."}}

    fake_ft = mock.MagicMock()
    fake_ft.stage_offer = fake_stage
    try:
        with mock.patch.object(I, "resolve_openai_key", lambda: ("sk-test", "env")), \
             mock.patch.dict(sys.modules, {"file_tools": fake_ft}):
            with mock.patch.object(I, "call_openai", ok_call):
                res = _run(I.generate_image.handler({"prompt": "a red bicycle on white", "size": "square"}))
                assert not res.get("is_error"), _txt(res)
                out = _txt(res)
                assert "![a_red_bicycle_on_white.png](/api/files/" in out and "[⤓ a_red_bicycle_on_white.png" in out
                assert "revised prompt: a red bicycle" in out
                assert staged and staged[0][2] == "a_red_bicycle_on_white.png"
            with mock.patch.object(I, "call_openai", bad_call):
                res = _run(I.generate_image.handler({"prompt": "something"}))
                assert res.get("is_error") and "could not generate that image" in _txt(res)
                assert "safety system" in _txt(res)
        with mock.patch.object(I, "resolve_openai_key", lambda: ("", "none")):
            res = _run(I.generate_image.handler({"prompt": "x y z"}))
            assert res.get("is_error") and "no OpenAI API key" in _txt(res)
        with mock.patch.dict(os.environ, {"CC_IMAGE_GENERATION_ENABLED": "false"}):
            res = _run(I.generate_image.handler({"prompt": "x y z"}))
            assert res.get("is_error") and "turned off" in _txt(res)
    finally:
        CURRENT_USER.reset(tok)


# ---------------------------------------------------------------------------
# Frontend contract
# ---------------------------------------------------------------------------

def test_frontend_has_leaflet_and_the_map_renderer():
    html = open(INDEX, encoding="utf-8").read()
    for needle in ('href="/static/vendor/leaflet/leaflet.css"', 'src="/static/vendor/leaflet/leaflet.js"',
                   "function mountMap", "aihub-map", "/static/data/us-states.geojson", "L.geoJSON", "d._lmap"):
        assert needle in html, needle
    vendor = os.path.join(APP_ROOT, "agent_service", "static", "vendor", "leaflet")
    assert os.path.getsize(os.path.join(vendor, "leaflet.js")) > 100_000
    assert os.path.isfile(os.path.join(vendor, "images", "marker-icon.png"))
    assert os.path.getsize(M.GEOJSON_PATH) > 50_000


if __name__ == "__main__":
    if not HAVE_SDK:
        print(f"SKIP-ALL: {_IMPORT_ERR}")
        sys.exit(0)
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in fns:
        try:
            f()
            print(f"PASS  {n}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {n}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {n}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
