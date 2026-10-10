"""Server-file routes are locked down (2026-10-10).

/workflow/file/{read,write,append,check,delete} and /folder/{list_files,info}
in app.py act on any path the caller names. They had no role check: any
signed-in user — and anyone at all with AUTH_MIDDLEWARE_DRY_RUN=true — could
read, write or delete any file on the server. Now each needs a Developer
session or the internal API key, and server_path_guard refuses the secret
store, the system folder, .env files and deletes that would take them along.
"""
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import server_path_guard as g  # noqa: E402


@pytest.fixture
def box(tmp_path, monkeypatch):
    """An install under tmp: APP_ROOT/data/secrets and a fake system folder."""
    app_root = tmp_path / "app"
    (app_root / "data" / "secrets").mkdir(parents=True)
    (app_root / "data" / "secrets" / "secrets.json.enc").write_text("x")
    system = tmp_path / "Windows"
    (system / "System32").mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("APP_ROOT", str(app_root))
    monkeypatch.setenv("SystemRoot", str(system))
    monkeypatch.setattr(g, "_norm", g._norm)       # (no caching to reset)
    return app_root, system, work


def test_ordinary_paths_are_allowed(box):
    app_root, _system, work = box
    assert g.refusal(str(work / "input" / "a.pdf")) is None
    assert g.refusal(str(app_root / "logs" / "x.txt")) is None
    assert g.refusal(str(work), for_delete=True) is None


def test_secret_store_and_system_folder_are_refused(box):
    app_root, system, work = box
    assert g.refusal(str(app_root / "data" / "secrets" / "secrets.json.enc"))
    assert g.refusal(str(app_root / "DATA" / "Secrets"))                 # any case
    assert g.refusal(str(work / ".." / "app" / "data" / "secrets" / "x"))  # climbing out
    assert g.refusal(str(system / "System32" / "drivers" / "etc" / "hosts"))


def test_env_files_are_refused(box):
    app_root, _system, work = box
    for name in (".env", ".ENV", ".env.backup"):
        assert "credentials" in g.refusal(str(app_root / name)), name
    assert g.refusal(str(work / "my.env.txt")) is None


def test_deleting_a_folder_that_contains_protected_files_is_refused(box):
    app_root, system, _work = box
    assert g.refusal(str(app_root / "data")) is None                      # reading/listing is fine
    assert "Deleting this" in g.refusal(str(app_root / "data"), for_delete=True)
    assert "Deleting this" in g.refusal(str(app_root), for_delete=True)
    assert g.refusal(str(system.parent), for_delete=True)


def test_empty_path_is_refused(box):
    assert g.refusal("") and g.refusal("   ") and g.refusal(None)


# ------------------------------------------------------------ the routes

APP = (ROOT / "app.py").read_text(encoding="utf-8", errors="replace")
ROUTES = ["/workflow/file/read", "/workflow/file/write", "/workflow/file/append",
          "/workflow/file/check", "/workflow/file/delete", "/folder/list_files", "/folder/info"]


def _block(route):
    start = APP.index(f"@app.route('{route}'")
    end = APP.find("@app.route(", start + 10)
    return APP[start:end if end > 0 else len(APP)]


@pytest.mark.parametrize("route", ROUTES)
def test_each_route_needs_a_developer_and_checks_the_path(route):
    blk = _block(route)
    head = blk[:blk.index("\ndef ")]
    assert "@api_key_or_session_required(min_role=2)" in head, route
    assert "_server_path_refused(" in blk, route


def test_delete_also_refuses_ancestors_of_protected_folders():
    assert "_server_path_refused(file_path, for_delete=True)" in _block("/workflow/file/delete")


def test_listing_never_returns_protected_files():
    assert "not _server_path_refusal(f)" in _block("/folder/list_files")


def test_folder_info_no_longer_calls_fromtimestamp_on_the_module():
    blk = _block("/folder/info")
    code = "\n".join(line.split("#", 1)[0] for line in blk.splitlines())   # comments aside
    assert not re.search(r"(?<![\w.])datetime\.fromtimestamp", code)
    assert "_dt_mod.datetime.fromtimestamp" in blk
