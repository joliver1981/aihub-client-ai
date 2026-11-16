"""Builder, Command Center and workflow-designer Document calls behind an HTTPS
reverse proxy (2026-11-16).

THE CONTRACT
  * published_addresses.published_address(NAME) returns the setting only while
    USE_REVERSE_PROXY is on AND the value starts with http:// or https://.
    Anything else is "" (malformed values log a warning) and the caller keeps
    its existing behaviour.
  * /builder and /command-center redirect to BUILDER_PUBLIC_BASE_URL /
    CC_PUBLIC_BASE_URL when usable; otherwise the redirect is exactly the
    link they have always built.
  * /document/config gains "designerBase" only while USE_REVERSE_PROXY is on;
    its other keys never change.
  * /designer/document/<op> relays exactly five Document API calls, unchanged,
    and answers 404 for anything else or when USE_REVERSE_PROXY is off.
  * The Builder and Command Center hand the browser APP_PUBLIC_BASE_URL for
    links back to AI Hub under the same rule.
  * The designer script uses designerBase when present and the port swap
    otherwise (run in node when node is available).

Route source is lifted straight out of app.py (app_route_harness, decorators
dropped: the sign-in wrappers have their own tests). Force-add to git
(gitignore hides test*.py).
"""
import datetime as dt
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest
from flask import Flask, Response, abort, flash, jsonify, redirect, request, url_for

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import published_addresses as pa  # noqa: E402
from app_route_harness import load_app_symbols  # noqa: E402

pytestmark = pytest.mark.unit

_SETTINGS = ("USE_REVERSE_PROXY", "APP_PUBLIC_BASE_URL", "BUILDER_PUBLIC_BASE_URL",
             "CC_PUBLIC_BASE_URL", "BUILDER_SERVICE_PORT", "BUILDER_SERVICE_HOST",
             "CC_SERVICE_PORT", "CC_SERVICE_HOST", "PROTOCOL", "HOST", "HOST_PORT")

MALFORMED = ["builder.example.com", "//builder.example.com", "ftp://builder.example.com",
             "javascript:alert(1)", "localhost:8100", "htp://builder.example.com"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _SETTINGS:
        monkeypatch.delenv(name, raising=False)


class _User:
    is_authenticated = True
    id = 7
    role = 2
    TenantId = 1
    username = "dev"
    name = "Dev User"


# ─── published_address ──────────────────────────────────────────────────

class TestPublishedAddress:
    def test_unset_is_empty(self, monkeypatch):
        monkeypatch.setenv("USE_REVERSE_PROXY", "true")
        assert pa.published_address("BUILDER_PUBLIC_BASE_URL") == ""

    def test_set_but_proxy_switch_off_is_empty(self, monkeypatch):
        monkeypatch.setenv("BUILDER_PUBLIC_BASE_URL", "https://builder.example.com")
        assert pa.published_address("BUILDER_PUBLIC_BASE_URL") == ""
        monkeypatch.setenv("USE_REVERSE_PROXY", "false")
        assert pa.published_address("BUILDER_PUBLIC_BASE_URL") == ""

    @pytest.mark.parametrize("switch", ["true", "TRUE", " True ", "1", "yes"])
    def test_proxy_switch_spellings(self, monkeypatch, switch):
        monkeypatch.setenv("USE_REVERSE_PROXY", switch)
        monkeypatch.setenv("CC_PUBLIC_BASE_URL", "https://cc.example.com")
        assert pa.behind_proxy() is True
        assert pa.published_address("CC_PUBLIC_BASE_URL") == "https://cc.example.com"

    @pytest.mark.parametrize("switch", ["", "0", "no", "off", "enabled"])
    def test_other_switch_values_are_off(self, monkeypatch, switch):
        monkeypatch.setenv("USE_REVERSE_PROXY", switch)
        monkeypatch.setenv("CC_PUBLIC_BASE_URL", "https://cc.example.com")
        assert pa.behind_proxy() is False
        assert pa.published_address("CC_PUBLIC_BASE_URL") == ""

    @pytest.mark.parametrize("value,expected", [
        ("https://builder.example.com", "https://builder.example.com"),
        ("https://builder.example.com/", "https://builder.example.com"),
        ("  https://aihub.example.com:8444/  ", "https://aihub.example.com:8444"),
        ("HTTPS://Builder.Example.com", "HTTPS://Builder.Example.com"),
        ("http://builder.internal:8100", "http://builder.internal:8100"),
    ])
    def test_usable_values(self, monkeypatch, value, expected):
        monkeypatch.setenv("USE_REVERSE_PROXY", "true")
        monkeypatch.setenv("BUILDER_PUBLIC_BASE_URL", value)
        assert pa.published_address("BUILDER_PUBLIC_BASE_URL") == expected

    @pytest.mark.parametrize("value", MALFORMED)
    def test_malformed_is_ignored_and_logged(self, monkeypatch, caplog, value):
        monkeypatch.setenv("USE_REVERSE_PROXY", "true")
        monkeypatch.setenv("BUILDER_PUBLIC_BASE_URL", value)
        with caplog.at_level(logging.WARNING, logger="published_addresses"):
            assert pa.published_address("BUILDER_PUBLIC_BASE_URL") == ""
        assert any("BUILDER_PUBLIC_BASE_URL ignored" in r.getMessage() for r in caplog.records)

    def test_blank_value_is_silent(self, monkeypatch, caplog):
        monkeypatch.setenv("USE_REVERSE_PROXY", "true")
        monkeypatch.setenv("BUILDER_PUBLIC_BASE_URL", "   ")
        with caplog.at_level(logging.WARNING, logger="published_addresses"):
            assert pa.published_address("BUILDER_PUBLIC_BASE_URL") == ""
        assert not caplog.records


# ─── /builder and /command-center ───────────────────────────────────────

@pytest.fixture()
def redirect_client():
    ns = {
        "os": os, "dt": dt, "secrets": secrets, "request": request,
        "redirect": redirect, "flash": flash, "url_for": url_for,
        "current_user": _User(), "builder_tokens": {}, "cc_tokens": {},
        "published_address": pa.published_address,
        "cfg": types.SimpleNamespace(CC_ALLOW_ALL_USERS=False),
        "_mint_cc_token": lambda ctx: "cc-token",
        "logger": logging.getLogger("t"),
    }
    load_app_symbols(["cleanup_expired_builder_tokens", "builder_redirect",
                      "cleanup_expired_cc_tokens", "command_center_redirect"], ns)
    app = Flask(__name__)
    app.add_url_rule("/builder", "builder", ns["builder_redirect"])
    app.add_url_rule("/command-center", "cc", ns["command_center_redirect"])
    client = app.test_client()
    client.ns = ns
    return client


def _location(client, path):
    resp = client.get(path, base_url="http://hub.local:5001")
    assert resp.status_code == 302
    return resp.headers["Location"]


class TestBuilderRedirect:
    def test_unset_builds_the_direct_link(self, redirect_client):
        loc = _location(redirect_client, "/builder")
        token = next(iter(redirect_client.ns["builder_tokens"]))
        assert loc == f"http://hub.local:8100?token={token}"

    def test_published_address_is_used(self, redirect_client, monkeypatch):
        monkeypatch.setenv("USE_REVERSE_PROXY", "true")
        monkeypatch.setenv("BUILDER_PUBLIC_BASE_URL", "https://builder.example.com/")
        loc = _location(redirect_client, "/builder")
        token = next(iter(redirect_client.ns["builder_tokens"]))
        assert loc == f"https://builder.example.com/?token={token}"

    def test_set_without_the_proxy_switch_changes_nothing(self, redirect_client, monkeypatch):
        monkeypatch.setenv("BUILDER_PUBLIC_BASE_URL", "https://builder.example.com")
        loc = _location(redirect_client, "/builder")
        assert loc.startswith("http://hub.local:8100?token=")

    @pytest.mark.parametrize("value", MALFORMED)
    def test_malformed_falls_back_to_the_direct_link(self, redirect_client, monkeypatch, value):
        monkeypatch.setenv("USE_REVERSE_PROXY", "true")
        monkeypatch.setenv("BUILDER_PUBLIC_BASE_URL", value)
        loc = _location(redirect_client, "/builder")
        assert loc.startswith("http://hub.local:8100?token=")

    def test_cc_setting_does_not_move_the_builder(self, redirect_client, monkeypatch):
        monkeypatch.setenv("USE_REVERSE_PROXY", "true")
        monkeypatch.setenv("CC_PUBLIC_BASE_URL", "https://cc.example.com")
        assert _location(redirect_client, "/builder").startswith("http://hub.local:8100?token=")


class TestCommandCenterRedirect:
    def test_unset_builds_the_direct_link(self, redirect_client):
        assert _location(redirect_client, "/command-center") == "http://hub.local:5091?token=cc-token"

    def test_published_address_is_used(self, redirect_client, monkeypatch):
        monkeypatch.setenv("USE_REVERSE_PROXY", "true")
        monkeypatch.setenv("CC_PUBLIC_BASE_URL", "https://aihub.example.com:8445")
        assert _location(redirect_client, "/command-center") == \
            "https://aihub.example.com:8445/?token=cc-token"

    def test_set_without_the_proxy_switch_changes_nothing(self, redirect_client, monkeypatch):
        monkeypatch.setenv("CC_PUBLIC_BASE_URL", "https://cc.example.com")
        assert _location(redirect_client, "/command-center") == "http://hub.local:5091?token=cc-token"

    @pytest.mark.parametrize("value", MALFORMED)
    def test_malformed_falls_back_to_the_direct_link(self, redirect_client, monkeypatch, value):
        monkeypatch.setenv("USE_REVERSE_PROXY", "true")
        monkeypatch.setenv("CC_PUBLIC_BASE_URL", value)
        assert _location(redirect_client, "/command-center") == "http://hub.local:5091?token=cc-token"


# ─── /document/config and the relay ─────────────────────────────────────

class _Upstream:
    def __init__(self, content=b'{"status": "success"}', status_code=200, headers=None):
        self.content = content
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "application/json"}


@pytest.fixture()
def doc_client(monkeypatch):
    import requests

    calls = []
    state = {"reply": _Upstream(), "raise": None}

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        if state["raise"] is not None:
            raise state["raise"]
        return state["reply"]

    monkeypatch.setattr(requests, "request", fake_request)
    common = types.ModuleType("CommonUtils")
    common.get_document_api_base_url = lambda: "http://127.0.0.1:5011"
    monkeypatch.setitem(sys.modules, "CommonUtils", common)

    ns = {
        "os": os, "request": request, "jsonify": jsonify, "abort": abort,
        "Response": Response, "behind_proxy": pa.behind_proxy,
        "get_local_ip": lambda: "10.1.2.3",
        "cfg": types.SimpleNamespace(DOC_PROCESSING_TIMEOUT_MINUTES=120),
        "logger": logging.getLogger("t"),
    }
    load_app_symbols(["get_document_config", "_DESIGNER_DOCUMENT_CALLS",
                      "designer_document_relay"], ns)
    app = Flask(__name__)
    app.add_url_rule("/document/config", "config", ns["get_document_config"])
    app.add_url_rule("/designer/document/<operation>", "relay",
                     ns["designer_document_relay"], methods=["GET", "POST"])
    client = app.test_client()
    client.calls = calls
    client.state = state
    return client


class TestDocumentConfig:
    def test_direct_install_reply_is_unchanged(self, doc_client, monkeypatch):
        monkeypatch.setenv("HOST", "0.0.0.0")
        monkeypatch.setenv("HOST_PORT", "5001")
        assert doc_client.get("/document/config").get_json() == {
            "port": 5011, "baseUrl": "http://10.1.2.3:5011",
            "protocol": "http", "host": "10.1.2.3"}

    def test_behind_proxy_adds_only_the_designer_base(self, doc_client, monkeypatch):
        monkeypatch.setenv("HOST", "0.0.0.0")
        monkeypatch.setenv("HOST_PORT", "5001")
        monkeypatch.setenv("USE_REVERSE_PROXY", "true")
        assert doc_client.get("/document/config").get_json() == {
            "port": 5011, "baseUrl": "http://10.1.2.3:5011",
            "protocol": "http", "host": "10.1.2.3", "designerBase": "/designer"}


class TestDesignerDocumentRelay:
    @pytest.mark.parametrize("method,op", [
        ("GET", "types"), ("POST", "save"), ("POST", "process"),
        ("POST", "extract"), ("POST", "analyze")])
    def test_off_without_the_proxy_switch(self, doc_client, method, op):
        resp = doc_client.open(f"/designer/document/{op}", method=method)
        assert resp.status_code == 404
        assert doc_client.calls == []

    @pytest.mark.parametrize("method,op", [
        ("POST", "backfill_records"), ("POST", "search"), ("POST", "process_directory"),
        ("GET", "health"), ("POST", "extract_text"), ("GET", "save"), ("POST", "types"),
        ("GET", "process")])
    def test_nothing_else_is_relayed(self, doc_client, monkeypatch, method, op):
        monkeypatch.setenv("USE_REVERSE_PROXY", "true")
        resp = doc_client.open(f"/designer/document/{op}", method=method)
        assert resp.status_code == 404
        assert doc_client.calls == []

    def test_types_is_fetched_and_returned_as_is(self, doc_client, monkeypatch):
        monkeypatch.setenv("USE_REVERSE_PROXY", "true")
        doc_client.state["reply"] = _Upstream(b'{"document_types": ["invoice"]}')
        resp = doc_client.get("/designer/document/types?format=json")
        assert resp.status_code == 200
        assert resp.get_json() == {"document_types": ["invoice"]}
        call = doc_client.calls[0]
        assert call["method"] == "GET"
        assert call["url"] == "http://127.0.0.1:5011/document/types"
        assert call["params"] == {"format": ["json"]}
        assert call["data"] is None
        assert call["timeout"] == 120 * 60

    def test_json_body_travels_unchanged(self, doc_client, monkeypatch):
        monkeypatch.setenv("USE_REVERSE_PROXY", "true")
        body = json.dumps({"content": "text", "outputPath": "C:/out/a.txt"}).encode()
        resp = doc_client.post("/designer/document/save", data=body,
                               content_type="application/json")
        assert resp.status_code == 200
        call = doc_client.calls[0]
        assert call["url"] == "http://127.0.0.1:5011/document/save"
        assert call["data"] == body
        assert call["headers"] == {"Content-Type": "application/json"}

    def test_multipart_body_and_boundary_travel_unchanged(self, doc_client, monkeypatch):
        monkeypatch.setenv("USE_REVERSE_PROXY", "true")
        boundary = "----designerBoundary123"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"filePath\"\r\n\r\n"
                f"C:/docs/a.pdf\r\n--{boundary}\r\nContent-Disposition: form-data; "
                f"name=\"file\"; filename=\"a.bin\"\r\nContent-Type: application/octet-stream"
                f"\r\n\r\n").encode() + bytes(range(256)) + f"\r\n--{boundary}--\r\n".encode()
        ctype = f"multipart/form-data; boundary={boundary}"
        resp = doc_client.post("/designer/document/process", data=body, content_type=ctype)
        assert resp.status_code == 200
        call = doc_client.calls[0]
        assert call["method"] == "POST"
        assert call["data"] == body
        assert call["headers"] == {"Content-Type": ctype}

    def test_error_status_body_and_retry_after_come_back(self, doc_client, monkeypatch):
        monkeypatch.setenv("USE_REVERSE_PROXY", "true")
        doc_client.state["reply"] = _Upstream(
            b'{"status": "busy", "message": "try again"}', 503,
            {"Content-Type": "application/json", "Retry-After": "30"})
        resp = doc_client.post("/designer/document/process", data=b"x",
                               content_type="multipart/form-data; boundary=b")
        assert resp.status_code == 503
        assert resp.get_json() == {"status": "busy", "message": "try again"}
        assert resp.headers["Retry-After"] == "30"

    def test_document_service_down_is_a_readable_503(self, doc_client, monkeypatch):
        import requests
        monkeypatch.setenv("USE_REVERSE_PROXY", "true")
        doc_client.state["raise"] = requests.exceptions.ConnectionError("refused")
        resp = doc_client.get("/designer/document/types")
        assert resp.status_code == 503
        assert resp.get_json()["status"] == "error"
        assert "connect" in resp.get_json()["message"].lower()

    def test_timeout_is_a_readable_504(self, doc_client, monkeypatch):
        import requests
        monkeypatch.setenv("USE_REVERSE_PROXY", "true")
        doc_client.state["raise"] = requests.exceptions.Timeout("slow")
        resp = doc_client.post("/designer/document/analyze", data=b"x",
                               content_type="multipart/form-data; boundary=b")
        assert resp.status_code == 504
        assert resp.get_json()["status"] == "error"


# ─── Links back to AI Hub from the Builder and the Command Center ───────

@pytest.mark.parametrize("config_file", [
    "command_center_service/cc_config.py", "builder_service/builder_config.py"])
class TestLinksBackToMainApp:
    @staticmethod
    def _fn(config_file):
        ns = {}
        load_app_symbols(["get_main_app_public_url"], ns, path=str(_ROOT / config_file))
        return ns["get_main_app_public_url"]

    def test_direct_install_is_empty(self, config_file, monkeypatch):
        monkeypatch.setenv("APP_PUBLIC_BASE_URL", "https://aihub.example.com")
        assert self._fn(config_file)() == ""

    def test_behind_proxy_uses_the_app_address(self, config_file, monkeypatch):
        monkeypatch.setenv("USE_REVERSE_PROXY", "true")
        monkeypatch.setenv("APP_PUBLIC_BASE_URL", "https://aihub.example.com/")
        assert self._fn(config_file)() == "https://aihub.example.com"

    def test_behind_proxy_unset_is_empty(self, config_file, monkeypatch):
        monkeypatch.setenv("USE_REVERSE_PROXY", "true")
        assert self._fn(config_file)() == ""

    def test_malformed_is_empty(self, config_file, monkeypatch):
        monkeypatch.setenv("USE_REVERSE_PROXY", "true")
        monkeypatch.setenv("APP_PUBLIC_BASE_URL", "aihub.example.com")
        assert self._fn(config_file)() == ""


def test_services_fall_back_to_the_address_they_always_used():
    """Each caller must keep its old value when the published one is empty."""
    studio = (_ROOT / "command_center_service/routes/studio.py").read_text(encoding="utf-8")
    chat = (_ROOT / "builder_service/routes/chat.py").read_text(encoding="utf-8")
    assert "get_main_app_public_url() or get_base_url()" in studio
    assert "get_main_app_public_url() or get_base_url()" in chat


# ─── The designer script ────────────────────────────────────────────────

def _designer_url_functions():
    src = (_ROOT / "static/js/workflow.js").read_text(encoding="utf-8")
    start = src.index("let documentDesignerBase = '';")
    end = src.index("async function getDocumentURL(")
    end = src.index("\n}", end) + 2
    return src[start:end]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("config,expected", [
    # direct install: this page's address with the Document API's port
    ({"port": 5011, "baseUrl": "http://10.1.2.3:5011", "protocol": "http", "host": "10.1.2.3"},
     "http://hub.local:5011/document/types"),
    # behind a proxy: the relay path on this app
    ({"port": 5011, "baseUrl": "http://10.1.2.3:5011", "protocol": "http", "host": "10.1.2.3",
      "designerBase": "/designer"},
     "/designer/document/types"),
    # an unexpected value is not trusted
    ({"port": 5011, "designerBase": 17}, "http://hub.local:5011/document/types"),
])
def test_designer_script_picks_the_address(config, expected):
    script = (
        "const window = {location: {origin: 'http://hub.local:5001'}};\n"
        "console.log = () => {};\n"
        f"const fetch = async () => ({{ok: true, json: async () => ({json.dumps(config)})}});\n"
        + _designer_url_functions() +
        "\ngetDocumentURL('/document/types').then(u => process.stdout.write(u));\n"
    )
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout == expected


def test_designer_script_has_one_address_builder():
    """Every Document step call goes through getDocumentURL, so the proxy
    switch cannot miss one (the process call used to build its own)."""
    src = (_ROOT / "static/js/workflow.js").read_text(encoding="utf-8")
    assert len(re.findall(r"url\.port\s*=", src)) == 1
    for route in ("types", "save", "process", "extract", "analyze"):
        assert f"getDocumentURL('/document/{route}')" in src
