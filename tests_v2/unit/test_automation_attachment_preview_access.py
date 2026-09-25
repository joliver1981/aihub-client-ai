"""Automation approval attachments: PREVIEW + per-row ACCESS (james 2026-09-24).

Two changes to the main-app attachment routes in automations/api.py
  gate   : /automations/api/runs/<run_id>/checkpoints/<checkpoint_id>/attachments/<name>
  review : /automations/api/approvals/<request_id>/attachments/<name>

1. PREVIEW — `?inline=1` shows the file in the browser tab (built-in PDF
   viewer, image, text, audio/video) instead of downloading it. Only types a
   browser renders natively are served inline, with a Content-Type fixed by
   the server (_PREVIEW_TYPES); text goes out as text/plain + nosniff; HTML /
   SVG / XML and Office files are never inline (they download as before).
2. ACCESS — the routes used to admit any Developer/Admin (automations_gate)
   with no per-item check, and turned regular users away even on their own
   items. Now: whoever can SEE the approval row, any role
   (approval_store.row_visible_to) — routed user / group members / Developer+
   for an unrouted row. Role never widens a user- or group-routed row, admins
   included. A gate with no bridged row: Developer+ or the run's requester.

Pinned here: the row_visible_to matrix, both routes end to end through a real
Flask app + flask_login (seats chosen by a header), the inline/download
responses, and the My Work link contract (Preview offered for exactly the
server's previewable types).

Run:
  C:\\Users\\james\\miniconda3\\envs\\aihub2.1\\python.exe -m pytest tests_v2/unit/test_automation_attachment_preview_access.py -v
"""
import json
import os
import re
import sys

import pytest

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from automations import approval_store as S  # noqa: E402

pytestmark = pytest.mark.unit

INDEX = os.path.join(APP_ROOT, "agent_service", "static", "index.html")

ALEX, CASEY, ERIN, ADMIN = 101, 102, 201, 1
ROLE = {ALEX: 1, CASEY: 1, ERIN: 2, ADMIN: 3}
AP_TEAM = 7
MEMBERS = {ALEX: [AP_TEAM], CASEY: [], ERIN: [], ADMIN: []}


# ---------------------------------------------------------------------------
# row_visible_to — the rule itself
# ---------------------------------------------------------------------------

def _row(at, aid):
    return {"request_id": "r", "assigned_to_type": at, "assigned_to_id": aid}


class TestRowVisibleTo:

    def test_user_routed_row_is_that_user_only(self):
        row = _row("user", ALEX)
        assert S.row_visible_to(row, ALEX, 1, [])
        for uid in (CASEY, ERIN, ADMIN):
            assert not S.row_visible_to(row, uid, ROLE[uid], [AP_TEAM]), uid

    def test_group_routed_row_is_members_only_any_role(self):
        row = _row("group", AP_TEAM)
        assert S.row_visible_to(row, ALEX, 1, [AP_TEAM])
        assert S.row_visible_to(row, CASEY, 1, [str(AP_TEAM)])     # text ids too
        for uid in (ERIN, ADMIN):
            assert not S.row_visible_to(row, uid, ROLE[uid], []), uid

    def test_unrouted_row_is_developer_plus(self):
        for at in (None, "", "unassigned", "Unassigned"):
            row = _row(at, None)
            assert not S.row_visible_to(row, ALEX, 1, [AP_TEAM]), at
            assert S.row_visible_to(row, ERIN, 2, []), at
            assert S.row_visible_to(row, ADMIN, 3, []), at

    def test_legacy_typeless_row_with_an_id(self):
        # add_row now always writes 'user' when an id is set; an older row
        # may not — its user and (as My Work's pool reading did) Developer+
        row = _row(None, ALEX)
        assert S.row_visible_to(row, ALEX, 1, [])
        assert S.row_visible_to(row, ERIN, 2, [])
        assert not S.row_visible_to(row, CASEY, 1, [])

    def test_bad_input_fails_closed(self):
        assert not S.row_visible_to(None, ALEX, 3, [])
        assert not S.row_visible_to({}, ALEX, 1, [])
        assert not S.row_visible_to(_row("user", ALEX), "abc", 3, [])
        assert not S.row_visible_to(_row("user", "abc"), ALEX, 3, [])
        assert not S.row_visible_to(_row("weird-type", ALEX), ALEX, 3, [])
        assert not S.row_visible_to(_row("group", AP_TEAM), ALEX, 1, None)
        assert not S.row_visible_to(_row(None, None), ERIN, "junk", [])


# ---------------------------------------------------------------------------
# The routes, end to end
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self):
        self._rows = []

    def execute(self, sql, *params):
        assert "UserGroups" in sql
        self._rows = [(g,) for g in MEMBERS.get(int(params[0]), [])]

    def fetchall(self):
        return self._rows


class _FakeConn:
    def cursor(self):
        return _FakeCursor()

    def close(self):
        pass


class _FakeManager:
    def __init__(self, base_path):
        self.base_path = base_path

    def _db_conn(self):
        return _FakeConn()


class _FakeRunner:
    def __init__(self, runs):
        self.runs = runs

    def get_run(self, run_id):
        return self.runs.get(run_id)


@pytest.fixture
def env(tmp_path, monkeypatch):
    from flask import Flask
    from flask_login import LoginManager, UserMixin
    import automations.api as api_mod
    from automations import checkpoints as cp

    base = tmp_path / "tenant"
    base.mkdir()
    workdir = tmp_path / "run1"
    workdir.mkdir()
    files = {"scan.pdf": b"%PDF-1.4 fake", "list.csv": b"a,b\n1,2\n",
             "page.html": b"<script>alert(1)</script>", "pic.svg": b"<svg onload=alert(1)>",
             "memo.docx": b"PK\x03\x04docx", "clip.mp4": b"\x00\x00\x00\x18ftypmp42"}
    for n, b in files.items():
        (workdir / n).write_bytes(b)
    atts = [{"name": n, "relpath": n, "size": len(b)} for n, b in files.items()]

    def review_row(at, aid):
        row = S.add_row(str(base), "Automation exception", "d", assigned_to_id=aid,
                        assigned_to_type=at, approval_data=json.dumps(
                            {"source": "automation", "kind": "review", "run_id": "run-1",
                             "attachments": atts}))
        return row["request_id"]

    gate_row = S.add_row(str(base), "Automation checkpoint", "d", assigned_to_id=ALEX,
                         approval_data=json.dumps({"run_id": "run-1"}))
    gate = cp.create_checkpoint(str(workdir), "check these",
                                attachments=atts,
                                approval_request_id=gate_row["request_id"])
    rowless = cp.create_checkpoint(str(workdir), "no queue row", attachments=atts)

    runs = {"run-1": {"run_id": "run-1", "log_path": str(workdir / "run.log"),
                      "requested_by": CASEY}}
    monkeypatch.setattr(api_mod, "_manager", _FakeManager(str(base)))
    monkeypatch.setattr(api_mod, "_tables_ensured", True)
    monkeypatch.setattr(api_mod, "_runner", _FakeRunner(runs))
    monkeypatch.setattr(api_mod.cfg, "AUTOMATIONS_ENABLED", True, raising=False)

    class Seat(UserMixin):
        def __init__(self, uid):
            self.id, self.role = uid, ROLE[uid]

    app = Flask(__name__)
    app.secret_key = "test"
    lm = LoginManager(app)

    @lm.request_loader
    def _load(req):
        uid = req.headers.get("X-Seat")
        return Seat(int(uid)) if uid else None

    app.register_blueprint(api_mod.automations_bp)
    client = app.test_client()

    def get(url, seat=None):
        return client.get(url, headers={"X-Seat": str(seat)} if seat else {})

    return {"get": get, "review_row": review_row, "gate": gate["checkpoint_id"],
            "rowless": rowless["checkpoint_id"], "api": api_mod}


def _review_url(rid, name, inline=False):
    return f"/automations/api/approvals/{rid}/attachments/{name}" + ("?inline=1" if inline else "")


def _gate_url(cid, name, inline=False):
    return (f"/automations/api/runs/run-1/checkpoints/{cid}/attachments/{name}"
            + ("?inline=1" if inline else ""))


class TestReviewRouteAccess:

    def test_user_routed_row(self, env):
        rid = env["review_row"]("user", ALEX)
        assert env["get"](_review_url(rid, "scan.pdf"), ALEX).status_code == 200
        for uid in (CASEY, ERIN, ADMIN):
            r = env["get"](_review_url(rid, "scan.pdf"), uid)
            assert r.status_code == 403, uid
            assert b"%PDF" not in r.data

    def test_group_routed_row(self, env):
        rid = env["review_row"]("group", AP_TEAM)
        assert env["get"](_review_url(rid, "scan.pdf"), ALEX).status_code == 200   # role-1 member
        for uid in (CASEY, ERIN, ADMIN):
            assert env["get"](_review_url(rid, "scan.pdf"), uid).status_code == 403, uid

    def test_unrouted_row_is_developer_plus(self, env):
        rid = env["review_row"](None, None)
        assert env["get"](_review_url(rid, "scan.pdf"), ALEX).status_code == 403
        assert env["get"](_review_url(rid, "scan.pdf"), ERIN).status_code == 200
        assert env["get"](_review_url(rid, "scan.pdf"), ADMIN).status_code == 200

    def test_signed_out_is_refused(self, env):
        rid = env["review_row"]("user", ALEX)
        assert env["get"](_review_url(rid, "scan.pdf")).status_code == 401

    def test_feature_flag_off_is_refused(self, env, monkeypatch):
        rid = env["review_row"]("user", ALEX)
        monkeypatch.setattr(env["api"].cfg, "AUTOMATIONS_ENABLED", False)
        assert env["get"](_review_url(rid, "scan.pdf"), ALEX).status_code == 403

    def test_denied_viewer_learns_nothing_about_names(self, env):
        rid = env["review_row"]("user", ALEX)
        # the same 403 for a declared and an undeclared name
        assert env["get"](_review_url(rid, "scan.pdf"), CASEY).status_code == 403
        assert env["get"](_review_url(rid, "nope.pdf"), CASEY).status_code == 403
        assert env["get"](_review_url(rid, "nope.pdf"), ALEX).status_code == 404


class TestGateRouteAccess:

    def test_gate_follows_its_queue_row(self, env):
        assert env["get"](_gate_url(env["gate"], "scan.pdf"), ALEX).status_code == 200
        for uid in (CASEY, ERIN, ADMIN):     # CASEY is the run's requester — the row wins
            assert env["get"](_gate_url(env["gate"], "scan.pdf"), uid).status_code == 403, uid

    def test_gate_without_a_row_is_developer_plus_or_the_requester(self, env):
        assert env["get"](_gate_url(env["rowless"], "scan.pdf"), CASEY).status_code == 200
        assert env["get"](_gate_url(env["rowless"], "scan.pdf"), ERIN).status_code == 200
        assert env["get"](_gate_url(env["rowless"], "scan.pdf"), ALEX).status_code == 403

    def test_a_bridged_row_that_cannot_be_read_denies_everyone(self, env):
        # review finding 2026-09-24: a gate that WAS routed must not fall
        # back to "Developer+" when its row file is missing or corrupt
        from automations import checkpoints as cp
        api = env["api"]
        workdir = os.path.dirname(api._get_runner().get_run("run-1")["log_path"])
        gone = cp.create_checkpoint(workdir, "row vanished",
                                    attachments=[{"name": "scan.pdf", "relpath": "scan.pdf"}],
                                    approval_request_id="no-such-row")
        for uid in (ALEX, CASEY, ERIN, ADMIN):
            assert env["get"](_gate_url(gone["checkpoint_id"], "scan.pdf"), uid).status_code == 403, uid


class TestPreviewResponses:

    def test_default_is_still_a_download(self, env):
        rid = env["review_row"]("user", ALEX)
        r = env["get"](_review_url(rid, "scan.pdf"), ALEX)
        assert r.headers["Content-Disposition"].startswith("attachment")

    @pytest.mark.parametrize("name,ctype", [
        ("scan.pdf", "application/pdf"),
        ("list.csv", "text/plain"),
        ("clip.mp4", "video/mp4"),
    ])
    def test_previewable_types_open_inline(self, env, name, ctype):
        rid = env["review_row"]("user", ALEX)
        for url in (_review_url(rid, name, True), _gate_url(env["gate"], name, True)):
            r = env["get"](url, ALEX)
            assert r.status_code == 200, url
            assert r.headers["Content-Disposition"].startswith("inline"), url
            assert r.headers["Content-Type"].split(";")[0] == ctype, url
            assert r.headers["X-Content-Type-Options"] == "nosniff"
            assert "no-store" in r.headers["Cache-Control"]

    @pytest.mark.parametrize("name", ["page.html", "pic.svg", "memo.docx"])
    def test_script_capable_and_office_types_never_inline(self, env, name):
        rid = env["review_row"]("user", ALEX)
        r = env["get"](_review_url(rid, name, True), ALEX)
        assert r.status_code == 200
        # ?inline=1 is ignored: the unchanged download (attachment disposition
        # makes the browser save it, never render it on the main-app origin)
        assert r.headers["Content-Disposition"].startswith("attachment")
        assert "inline" not in r.headers["Content-Disposition"]

    @pytest.mark.parametrize("data,charset", [
        (b"name,city\nM\xfcller GmbH,Caf\xe9\n", "windows-1252"),   # Excel CSV default
        ("name,city\nMüller GmbH,Café\n".encode("utf-8"), "utf-8"),
        ("﻿name\nMüller\n".encode("utf-8"), "utf-8"),              # UTF-8 BOM
        ("name\nMüller\n".encode("utf-16"), "utf-16"),                  # UTF-16 BOM
        (b"plain ascii\n", "utf-8"),
    ])
    def test_text_preview_declares_the_files_real_charset(self, env, data, charset):
        api = env["api"]
        workdir = os.path.dirname(api._get_runner().get_run("run-1")["log_path"])
        with open(os.path.join(workdir, "list.csv"), "wb") as f:
            f.write(data)
        rid = env["review_row"]("user", ALEX)
        r = env["get"](_review_url(rid, "list.csv", True), ALEX)
        assert r.status_code == 200
        assert r.headers["Content-Type"] == f"text/plain; charset={charset}"
        assert r.data == data                              # bytes untouched

    def test_utf8_char_cut_by_the_sniff_window_is_still_utf8(self, env, tmp_path):
        p = tmp_path / "big.txt"
        p.write_bytes(b"a" * 65535 + "é".encode("utf-8"))  # 2-byte char straddles 64 KB
        assert env["api"]._text_charset(str(p)) == "utf-8"

    def test_inline_does_not_bypass_access(self, env):
        rid = env["review_row"]("user", ALEX)
        assert env["get"](_review_url(rid, "scan.pdf", True), ADMIN).status_code == 403

    def test_preview_map_has_no_script_capable_types(self, env):
        m = env["api"]._PREVIEW_TYPES
        for ext in (".html", ".htm", ".svg", ".xml", ".xhtml", ".js"):
            assert ext not in m, ext
        assert all(not v.startswith(("text/html", "image/svg", "application/xml",
                                     "text/xml", "application/xhtml"))
                   for v in m.values())


# ---------------------------------------------------------------------------
# My Work (The Agent UI) link contract
# ---------------------------------------------------------------------------

def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class TestMyWorkPreviewLink:

    def test_preview_link_is_the_same_route_inline_in_a_new_tab(self):
        html = _read(INDEX)
        assert 'pv.href = platUrl(path + "?inline=1")' in html
        assert 'pv.target = "_blank"; pv.rel = "noopener"' in html
        # Download link unchanged beside it
        assert "link.href = platUrl(path)" in html

    def test_client_and_server_agree_on_previewable_types(self):
        import automations.api as api_mod
        html = _read(INDEX)
        m = re.search(r"const PREVIEWABLE = /\\\.\(([^)]*)\)\$/i;", html)
        assert m, "PREVIEWABLE regex not found"
        client = set()
        for alt in m.group(1).split("|"):
            client |= ({"jpg", "jpeg"} if alt == "jpe?g" else {alt})
        server = {ext.lstrip(".") for ext in api_mod._PREVIEW_TYPES}
        assert client == server
