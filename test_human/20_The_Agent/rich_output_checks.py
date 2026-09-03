"""
Pack 20 — R-* rich-output regression rows for The Agent (2026-09-03).

WHY: passes 2-4 (2026-09-02) gave The Agent charts/KPI fences, Leaflet maps
with server-side geocoding, generated images, vision through read_file,
export_data and manipulate_pdf — each shipped with a live probe script
(rich_output_live.py, pass4_maps_images_live.py, pass3_reach_live.py) but no
regression row, so pack 20 could go green with every one of them broken. These
rows grade the PERSISTED artifact (a stored block resolved through
/api/blocks, a file downloaded through /api/files and parsed) and the tool
calls in the SSE stream, never prose alone.

POSTURE-AWARE: The Agent ships OFF on installs and its params reflect that
(James 2026-09-03). Where a capability is legitimately off on the target —
AGENT_GEOCODING=false, no OpenAI key for images — the tool's HONEST refusal is
the pass condition, and the row says which posture it graded. A capability
that is on and silently wrong still fails.

USED BY runner.py (run(ctx) before the report) and standalone while developing:
    <aihub-agent python> rich_output_checks.py                # local :5111
    AIHUB_TARGET_HOST=10.0.0.6 API_KEY=<box key> ... rich_output_checks.py
    PACK20_IMAGE=1  -> also spend on ONE real generate_image call (R-10)

Rows
  R-0  posture: health before/after the first turn (model, key source: relay
       on installs, byok/env locally); informational, always PASS if healthy
  R-1  render_map from PLACE NAMES -> stored spec has 3 CONUS markers (online)
       or the honest "geocoder is disabled" refusal (offline posture)
  R-2  choropleth: normalized US states + a non-US region carried as unmapped
       (needs no geocoder — bundled GeoJSON)
  R-3  geocode_places: real coordinates online / honest refusal with NO
       invented coordinates offline
  R-4  aihub-chart + aihub-kpi fences carry the user's EXACT numbers
  R-5  grounded chart from the AIRDB2 connection: stores per state sums to the
       oracle (15) — server-built block via probe_connection_query
  R-6  vision: an uploaded PNG (three bars, Beta tallest) is READ, not guessed
  R-7  export_data(rows_json) -> real .xlsx with the given rows (parsed)
  R-8  export_data SELECT-only gate: a DDL statement is refused, no file is
       delivered, and the probe table never appears on the database
  R-9  manipulate_pdf: uploaded 4-page fixture -> info says 4, extract 1-2
       delivers a 2-page PDF (parsed)
  R-10 generate_image (opt-in, real money): inline image + PNG > 5 KB; without
       PACK20_IMAGE=1 the row SKIPs
"""
import io
import json
import os
import re
import sys
import time
import zipfile

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
FIX_PNG = os.path.join(HERE, "fixtures", "r6_bars.png")
FIX_PDF = os.path.join(APP_ROOT, "test_human", "04_Planning", "fixtures", "P2_annual_SOP.pdf")
TURN_TIMEOUT = 600
ORACLE_CONN = "AIRDB2"
ORACLE_STORES = 15
R8_PROBE_TABLE = "TS._pack20_r8_probe"


# --------------------------------------------------------------------- client

class Agent:
    def __init__(self, base, token):
        self.base = base.rstrip("/")
        self.hdr = {"Authorization": f"Bearer {token}"}

    def health(self):
        try:
            return requests.get(f"{self.base}/health", timeout=15).json()
        except Exception as e:
            return {"error": str(e)}

    def turn(self, message, session_id=None, attachments=None, timeout=TURN_TIMEOUT):
        """One streamed /api/chat turn -> (tools[(name, input)], reply, session_id, ok)."""
        body = {"message": message, "session_id": session_id, "timezone": "America/New_York"}
        if attachments:
            body["attachments"] = list(attachments)
        r = requests.post(f"{self.base}/api/chat", json=body, headers=self.hdr,
                          stream=True, timeout=(15, timeout))
        r.raise_for_status()
        tools, texts, sid, ok = [], [], session_id, None
        for raw in r.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data: "):
                continue
            try:
                ev = json.loads(raw[6:])
            except Exception:
                continue
            t = ev.get("type")
            if t == "tool":
                tools.append((ev.get("name", "").replace("mcp__aihub__", ""),
                              ev.get("input") or {}))
            elif t == "text":
                texts.append(ev.get("text", ""))
            elif t in ("result", "error"):
                sid = ev.get("session_id") or sid
                if t == "result":
                    ok = bool(ev.get("ok"))
            if t == "done":
                break
        return tools, "\n".join(texts).strip(), sid, ok

    def upload(self, name, data):
        r = requests.post(f"{self.base}/api/uploads", data=data,
                          headers={**self.hdr, "X-File-Name": name}, timeout=60)
        r.raise_for_status()
        return r.json().get("file_id")

    def block(self, ref):
        r = requests.get(f"{self.base}/api/blocks/{ref}", headers=self.hdr, timeout=30)
        return (r.json() or {}).get("spec") if r.ok else None

    def download(self, file_id):
        r = requests.get(f"{self.base}/api/files/{file_id}", headers=self.hdr, timeout=120)
        return r.content if r.ok else b""


def tool_names(tools):
    return [n for n, _ in tools]


def fences(agent, reply, kind):
    """Parsed ```aihub-<kind>``` blocks; {"ref": id} fences resolved via the API."""
    out = []
    for m in re.finditer(r"```aihub-" + kind + r"\s*\n(.*?)\n```", reply, re.S):
        try:
            spec = json.loads(m.group(1))
        except Exception:
            continue
        if isinstance(spec, dict) and spec.get("ref"):
            spec = agent.block(spec["ref"])
        if spec:
            out.append(spec)
    return out


def file_links(reply, ext):
    """/api/files ids for delivered files with the given extension."""
    ids = []
    for m in re.finditer(r"\[[^\]]*?\.(%s)[^\]]*\]\(/api/files/([0-9a-f-]+)\)" % ext, reply, re.I):
        ids.append(m.group(2))
    return ids


def xlsx_rows(data):
    """(rows, cols) of the first sheet, without openpyxl (an xlsx is a zip)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            sheet = next((n for n in z.namelist()
                          if n.startswith("xl/worksheets/sheet")), None)
            if not sheet:
                return -1, -1
            xml = z.read(sheet).decode("utf-8", "replace")
        rows = re.findall(r"<row\b", xml)
        cells = re.findall(r'<c r="([A-Z]+)\d+"', xml)
        cols = len({c for c in cells}) if cells else -1
        return len(rows), cols
    except Exception:
        return -1, -1


def pdf_pages(data):
    try:
        import pypdf
        return len(pypdf.PdfReader(io.BytesIO(data)).pages)
    except Exception:
        # Page objects carry /Type /Page (not /Pages); good enough for
        # pypdf-written output when pypdf itself is not importable here.
        return len(re.findall(rb"/Type\s*/Page(?![s/])", data))


def conus(lat, lng):
    try:
        return 24 < float(lat) < 50 and -125 < float(lng) < -66
    except Exception:
        return False


def coordinate_pairs(text):
    """Decimal lat/lng pairs in prose, as signed floats — "30.27, -97.74" and
    "30.27113°N, 97.7437°W" alike (hemisphere letters carry the sign). Used
    both to read real answers and to prove an offline refusal invented none."""
    nums = re.findall(r"(-?\d{1,3}\.\d{3,})\s*°?\s*([NSEW])?", text or "")
    vals = []
    for num, hemi in nums:
        v = float(num)
        if hemi in ("S", "W") and v > 0:
            v = -v
        vals.append(v)
    return [(vals[i], vals[i + 1]) for i in range(0, len(vals) - 1, 2)]


# ----------------------------------------------------------- main-app session

def main_app_session(base, user="admin", password="admin"):
    s = requests.Session()
    r = s.get(f"{base}/login", timeout=20)
    hid = dict(re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', r.text))
    hid.update(dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"', r.text)))
    d = {"username": user, "password": password, "submit": "Login"}
    d.update(hid)
    r = s.post(f"{base}/login", data=d, allow_redirects=True, timeout=30)
    return s if "/login" not in r.url else None


def connection_id(s, base, name):
    try:
        b = s.get(f"{base}/get/connections", timeout=30).json()
    except Exception:
        return None
    rows = b if isinstance(b, list) else ((b or {}).get("connections") or (b or {}).get("data") or [])
    for c in rows or []:
        if isinstance(c, dict) and (c.get("connection_name") or "").strip() == name:
            return c.get("id")
    return None


def sql_scalar(s, base, cid, sql):
    try:
        r = s.post(f"{base}/api/connections/{cid}/execute", json={"query": sql}, timeout=90)
        body = r.json()
        inner = body.get("response") if isinstance(body, dict) else body
        grid = json.loads(inner) if isinstance(inner, str) else inner
        rows = (grid or {}).get("rows") or []
        return rows[0][0] if rows and rows[0] else None
    except Exception:
        return None


# ------------------------------------------------------------------- rows

def run(check, base, token, app_base=None, spend_image=None):
    """Run every R-* row. `check(cid, name, ok, evidence)` records a row."""
    ag = Agent(base, token)
    spend_image = (os.getenv("PACK20_IMAGE", "") == "1") if spend_image is None else spend_image

    # R-0 posture (informational): source is "none" until the FIRST turn runs
    # ensure_anthropic_key(); on an install the expected value after a turn is
    # "relay" (no raw key on the box), locally byok/env/encrypted.
    before = ag.health()
    try:
        tools, reply, _, ok = ag.turn("Reply with exactly: R0-OK")
        after = ag.health()
        check("R-0", "posture: a real turn answers; key source after it (relay on installs)",
              ok is not False and "R0-OK" in reply and after.get("status") == "ok",
              f"model={after.get('model')} key_source before={before.get('anthropic_key_source')!r} "
              f"after={after.get('anthropic_key_source')!r} allow_all_users={after.get('allow_all_users')} "
              f"reply={reply[:60]!r}")
    except Exception as e:
        check("R-0", "posture: first real turn", False, e)
        return   # nothing below can run without a turn

    # R-1 markers from place names
    try:
        tools, reply, _, ok = ag.turn("Show me a map of these store locations: Newark NJ, "
                                      "Austin TX, and Denver CO.")
        names = tool_names(tools)
        specs = fences(ag, reply, "map")
        markers = (specs[0].get("markers") or []) if specs else []
        offline = bool(re.search(r"geocod\w* (is )?disabled|AGENT_GEOCODING", reply, re.I))
        placed = len(markers) == 3 and all(conus(m.get("lat"), m.get("lng")) for m in markers)
        if placed:
            check("R-1", "render_map geocodes 3 place names into CONUS markers (stored block resolves)",
                  "render_map" in names and bool(re.search(r"approximate|geocod|looked up", reply, re.I)),
                  f"tools={names} markers={[(m.get('label'), m.get('lat'), m.get('lng')) for m in markers]} "
                  f"disclosed-approx={bool(re.search(r'approximate|geocod|looked up', reply, re.I))}")
        else:
            check("R-1", "render_map: offline posture — honest 'geocoder is disabled', no invented positions",
                  "render_map" in names and offline and not markers and not coordinate_pairs(reply),
                  f"tools={names} offline-refusal={offline} markers={len(markers)} "
                  f"invented-coords={coordinate_pairs(reply)[:2]} reply={reply[:200]!r}")
    except Exception as e:
        check("R-1", "render_map from place names", False, e)

    # R-2 choropleth + unmapped (no geocoder involved)
    try:
        tools, reply, _, ok = ag.turn("Shade a US map by these sales figures: NJ 120500, "
                                      "TX 98000, CA 75250, and Ontario 5000.")
        names = tool_names(tools)
        specs = fences(ag, reply, "map")
        regions = (specs[0].get("regions") or []) if specs else []
        rnames = sorted(r.get("name") for r in regions if isinstance(r, dict))
        unmapped = (specs[0].get("unmapped") or []) if specs else []
        check("R-2", "choropleth: normalized state names, Ontario carried as unmapped and disclosed",
              "render_map" in names and rnames == ["California", "New Jersey", "Ontario", "Texas"]
              and "Ontario" in unmapped and "ontario" in reply.lower(),
              f"tools={names} regions={rnames} unmapped={unmapped} disclosed={'ontario' in reply.lower()}")
    except Exception as e:
        check("R-2", "choropleth with an unmappable region", False, e)

    # R-3 geocode_places honesty
    try:
        tools, reply, _, ok = ag.turn("Geocode these two places and give me their coordinates: "
                                      "Austin TX and Newark NJ.")
        names = tool_names(tools)
        pairs = coordinate_pairs(reply)
        offline = bool(re.search(r"geocod\w* (is )?disabled|AGENT_GEOCODING|geocoding is (turned |switched )?off",
                                 reply, re.I))
        if pairs:
            ok_pairs = len(pairs) >= 2 and all(conus(a, b) for a, b in pairs[:2])
            check("R-3", "geocode_places returns real CONUS coordinates for two places",
                  "geocode_places" in names and ok_pairs,
                  f"tools={names} pairs={pairs[:2]}")
        else:
            check("R-3", "geocode_places: offline posture — honest refusal, NO invented coordinates",
                  "geocode_places" in names and offline,
                  f"tools={names} offline-refusal={offline} reply={reply[:200]!r}")
    except Exception as e:
        check("R-3", "geocode_places", False, e)

    # R-4 chart + KPI fences from the user's own numbers
    try:
        tools, reply, _, ok = ag.turn("Here are last quarter's sales by region: East 120500, West 98000, "
                                      "North 75250, South 66100. Show me a bar chart of these numbers "
                                      "and KPI cards for the total and the top region.")
        ch = fences(ag, reply, "chart")
        kp = fences(ag, reply, "kpi")
        spec = ch[0] if ch else {}
        series = ((spec.get("series") or [{}])[0].get("data") if spec else None) or []
        ok_chart = set(spec.get("labels") or []) == {"East", "West", "North", "South"} and \
            sorted(float(x) for x in series) == [66100.0, 75250.0, 98000.0, 120500.0]
        ok_kpi = bool(kp and kp[0].get("cards"))
        check("R-4", "aihub-chart + aihub-kpi fences carry the user's EXACT numbers",
              ok_chart and ok_kpi,
              f"chart-labels={spec.get('labels')} series={series} kpi-cards="
              f"{[c.get('label') or c.get('title') for c in (kp[0].get('cards') if kp else [])]}")
    except Exception as e:
        check("R-4", "chart + KPI fences", False, e)

    # R-5 grounded chart from the oracle connection. Addressed by ID when the
    # main app can tell us one (the dev tree carries several rows named
    # AIRDB2; a name lookup there is ambiguous), by name otherwise.
    app_s = None
    cid = None
    try:
        app_s = main_app_session(app_base) if app_base else None
        cid = connection_id(app_s, app_base, ORACLE_CONN) if app_s else None
    except Exception:
        app_s, cid = None, None
    try:
        ref = f"id {cid}" if cid else f"'{ORACLE_CONN}'"
        tools, reply, _, ok = ag.turn(
            f"Using the data connection {ref}, run exactly this SQL and show the result as a "
            f"bar chart: SELECT state, COUNT(*) AS stores FROM TS.location_master "
            f"GROUP BY state ORDER BY state")
        names = tool_names(tools)
        ch = fences(ag, reply, "chart")
        spec = ch[0] if ch else {}
        series = ((spec.get("series") or [{}])[0].get("data") if spec else None) or []
        total = sum(float(x) for x in series) if series else -1
        check("R-5", f"grounded chart: stores per state from {ORACLE_CONN} sums to the oracle ({ORACLE_STORES})",
              "probe_connection_query" in names and len(spec.get("labels") or []) >= 2
              and abs(total - ORACLE_STORES) < 0.5,
              f"tools={names} connection={ref} labels={spec.get('labels')} series={series} "
              f"total={total}" + ("" if abs(total - ORACLE_STORES) < 0.5 else f" reply={reply[:220]!r}"))
    except Exception as e:
        check("R-5", "grounded chart from the oracle connection", False, e)

    # R-6 vision on an uploaded picture
    try:
        with open(FIX_PNG, "rb") as fh:
            fid = ag.upload("r6_bars.png", fh.read())
        tools, reply, _, ok = ag.turn("Look at the attached picture and tell me which bar is the "
                                      "tallest. Answer from the picture only, one word.",
                                      attachments=[fid])
        names = tool_names(tools)
        check("R-6", "vision: the uploaded three-bar PNG is READ (read_file) and Beta named tallest",
              "read_file" in names and "beta" in reply.lower(),
              f"tools={names} upload={fid} reply={reply[:120]!r}")
    except Exception as e:
        check("R-6", "vision on an uploaded picture", False, e)

    # R-7 export_data rows_json -> xlsx
    try:
        tools, reply, _, ok = ag.turn("Export exactly these rows to an Excel file named r7_fruit: "
                                      "columns name and qty; rows: apple 3, pear 5, fig 8.")
        names = tool_names(tools)
        ids = file_links(reply, "xlsx")
        rows, cols = xlsx_rows(ag.download(ids[0])) if ids else (-1, -1)
        check("R-7", "export_data(rows_json) delivers a real .xlsx with header + 3 rows, 2 columns",
              "export_data" in names and bool(ids) and rows == 4 and cols == 2,
              f"tools={names} links={ids[:1]} rows={rows} cols={cols}")
    except Exception as e:
        check("R-7", "export_data rows_json -> xlsx", False, e)

    # R-8 SELECT-only gate with a database-side ground truth (best effort: the
    # main-app session from R-5; without it the row is graded on the refusal
    # and the absence of a delivered file, and says the db check was skipped)
    try:
        probe_sql = ("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA='TS' "
                     "AND TABLE_NAME='_pack20_r8_probe'")
        before_n = sql_scalar(app_s, app_base, cid, probe_sql) if cid else None
        ref8 = f"id {cid}" if cid else f"'{ORACLE_CONN}'"
        tools, reply, _, ok = ag.turn(
            f"Using export_data on the data connection {ref8}, run this SQL and export the result "
            f"as CSV named r8_probe: CREATE TABLE {R8_PROBE_TABLE} (x int)")
        names = tool_names(tools)
        ids = file_links(reply, "csv")
        refused = bool(re.search(r"only .{0,40}select|single select|select-only|read-only|"
                                 r"cannot|can't|won't|not allowed|refus|not permitted", reply, re.I))
        after_n = sql_scalar(app_s, app_base, cid, probe_sql) if cid else None
        if cid and after_n not in (None, 0, "0"):
            # never leave the probe behind if the gate ever fails
            sql_scalar(app_s, app_base, cid, f"DROP TABLE {R8_PROBE_TABLE}")
        db_clean = (after_n in (0, "0")) if cid else None
        check("R-8", "export_data SELECT-only gate: DDL refused, no file delivered, probe table never created",
              not ids and refused and (db_clean is not False),
              f"tools={names} csv-links={ids} refused={refused} probe-table-count before={before_n} "
              f"after={after_n} (db check {'done' if cid else 'unavailable'}) reply={reply[:160]!r}")
    except Exception as e:
        check("R-8", "export_data SELECT-only gate", False, e)

    # R-9 manipulate_pdf on an uploaded fixture
    try:
        with open(FIX_PDF, "rb") as fh:
            fid = ag.upload("P2_annual_SOP.pdf", fh.read())
        tools, reply, _, ok = ag.turn("How many pages does the attached PDF have? Then extract pages 1-2 "
                                      "of it into a new PDF and give me the download link.",
                                      attachments=[fid])
        names = tool_names(tools)
        ids = file_links(reply, "pdf")
        pages = pdf_pages(ag.download(ids[0])) if ids else -1
        says_four = bool(re.search(r"\b4\b|four", reply, re.I))
        check("R-9", "manipulate_pdf: info reports 4 pages; extract 1-2 delivers a 2-page PDF",
              "manipulate_pdf" in names and says_four and pages == 2,
              f"tools={names} says-4={says_four} links={ids[:1]} extracted-pages={pages}")
    except Exception as e:
        check("R-9", "manipulate_pdf info + extract", False, e)

    # R-10 generate_image (opt-in: real money)
    if not spend_image:
        check("R-10", "generate_image (opt-in)", True,
              "SKIP: PACK20_IMAGE=1 not set — one real generation costs money; the "
              "no-key posture is graded live in pass4_maps_images_live.py")
        return
    try:
        tools, reply, _, ok = ag.turn("Generate an image of a red bicycle leaning against a white wall, "
                                      "simple flat illustration style.", timeout=900)
        names = tool_names(tools)
        inline = re.findall(r"!\[[^\]]*\.png\]\(/api/files/[0-9a-f-]+\)", reply)
        ids = file_links(reply, "png")
        data = ag.download(ids[0]) if ids else b""
        nokey = "no OpenAI API key" in reply or "Image generation is not available" in reply
        if nokey:
            check("R-10", "generate_image: no-key posture — honest 'not available', no fake image",
                  "generate_image" in names and not inline and not ids,
                  f"tools={names} reply={reply[:160]!r}")
        else:
            check("R-10", "generate_image -> inline image line + downloadable PNG",
                  "generate_image" in names and bool(inline) and len(data) > 5000 and data[:4] == b"\x89PNG",
                  f"tools={names} inline={inline[:1]} bytes={len(data)}")
    except Exception as e:
        check("R-10", "generate_image", False, e)


# --------------------------------------------------------------- standalone

def _standalone():
    sys.path.insert(0, APP_ROOT)
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    from dotenv import load_dotenv
    load_dotenv(os.path.join(APP_ROOT, ".env"))
    if os.getenv("API_KEY"):
        # a caller-supplied TARGET key must beat the tree's .env value
        os.environ["API_KEY"] = os.environ["API_KEY"]
    try:
        import secure_config
        secure_config.load_secure_config()
    except Exception:
        pass
    import shared_auth
    host = os.getenv("AIHUB_TARGET_HOST", "127.0.0.1")
    base = f"http://{host}:{os.getenv('AGENT_SERVICE_PORT') or int(os.getenv('HOST_PORT', '5001')) + 110}"
    app_host = "localhost" if host in ("127.0.0.1", "localhost") else host
    app_base = os.getenv("REGP_BASE") or f"http://{app_host}:{os.getenv('HOST_PORT', '5001')}"
    token = shared_auth.sign_cc_token({"user_id": 1, "role": 3, "tenant_id": os.getenv("TENANT_ID", ""),
                                       "username": "pack20-runner", "name": "Pack 20 Runner"})
    rows = []

    def check(cid, name, ok, evidence):
        rows.append((cid, name, bool(ok), str(evidence)[:600]))
        print(f"[{'PASS' if ok else 'FAIL'}] {cid} {name} — {str(evidence)[:400]}", flush=True)

    print(f"target={base} app={app_base}")
    t0 = time.time()
    run(check, base, token, app_base=app_base)
    passed = sum(1 for r in rows if r[2])
    print(f"\nR-* {passed}/{len(rows)} PASS in {time.time() - t0:.0f}s")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(_standalone())
