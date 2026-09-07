"""POST /unfile/type_category — the one route that REMOVES document access.

Migration 016's fail-closed rule: a document_type with NO DocumentTypeCategories
row is readable by admins only. /save/type_category only files or re-files a
type and /merge/document_category only moves types, so until this route there
was no way to reach that state through the product (an admin could not undo a
mis-filing, and the fail-closed branch was untestable — RU-06(c)).

THE CONTRACT
  * admin (role 3) session  -> tenant context set FIRST, then one parametrised
                               DELETE, commit, {'status':'success','unfiled':N}
  * role 1 / role 2 session -> 403 from the REAL role gate, no DB round trip
                               (this removes access, so Developers may not reach it)
  * no session, no key      -> 401
  * missing / blank type    -> 400, no DB round trip
  * type with no row        -> success with unfiled 0 (idempotent), not an error
  * re-filing afterwards    -> /save/type_category takes its INSERT branch

Route source is lifted from app.py (app_route_harness — decorators dropped),
then wrapped in the real role_decorators.api_key_or_session_required(min_role=3)
with a faked flask-login current_user, so the gate under test is the one that
ships. The DB is a small stateful fake keyed by document_type.
"""
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify, request

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

pytest.importorskip("flask_login", reason="flask_login not installed in this environment")

import role_decorators  # noqa: E402
from app_route_harness import load_app_symbols  # noqa: E402

pytestmark = pytest.mark.unit


class _Cursor:
    """Records every execute(); keeps a {document_type: category_id} table so
    DELETE / UPDATE / INSERT report a real rowcount."""

    def __init__(self, rows):
        self.rows = dict(rows)
        self.calls = []
        self.rowcount = -1

    def execute(self, sql, *params):
        sql = " ".join(sql.split())
        self.calls.append((sql, list(params)))
        if sql.startswith("DELETE FROM DocumentTypeCategories"):
            self.rowcount = 1 if self.rows.pop(params[0], None) is not None else 0
        elif sql.startswith("UPDATE DocumentTypeCategories"):
            category_id, _who, document_type = params
            if document_type in self.rows:
                self.rows[document_type] = category_id
                self.rowcount = 1
            else:
                self.rowcount = 0
        elif sql.startswith("INSERT INTO DocumentTypeCategories"):
            document_type, category_id, _who = params
            self.rows[document_type] = category_id
            self.rowcount = 1
        else:
            self.rowcount = -1


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class _NoSession:
    is_authenticated = False


def _session(role, name="admin"):
    return SimpleNamespace(is_authenticated=True, id=1, role=role, user_name=name)


class _Harness:
    def __init__(self, monkeypatch, rows=(("lease_amendment", 7),), user=None):
        self.cursor = _Cursor(rows)
        self.conn = _Conn(self.cursor)
        self.db_calls = 0

        def _get_db_connection():
            self.db_calls += 1
            return self.conn

        user = user or _NoSession()
        # The real gate reads flask_login's current_user from ITS module globals.
        monkeypatch.setattr(role_decorators, "current_user", user)
        ns = {"request": request, "jsonify": jsonify, "os": os,
              "logger": logging.getLogger("test_unfile_type_category"),
              "get_db_connection": _get_db_connection,
              "current_user": user}
        load_app_symbols(["unfile_type_category", "save_type_category"], ns)
        gate = role_decorators.api_key_or_session_required(min_role=3)
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.add_url_rule("/unfile/type_category", "unfile_type_category",
                         gate(ns["unfile_type_category"]), methods=["POST"])
        app.add_url_rule("/save/type_category", "save_type_category",
                         gate(ns["save_type_category"]), methods=["POST"])
        self.client = app.test_client()

    def unfile(self, body):
        return self.client.post("/unfile/type_category", json=body)

    def save(self, body):
        return self.client.post("/save/type_category", json=body)

    def sql(self, needle):
        return [(s, p) for s, p in self.cursor.calls if needle in s]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("API_KEY", "tenant-key")


# ================================================================== admin path
def test_admin_unfiles_a_mapped_type(monkeypatch):
    h = _Harness(monkeypatch, user=_session(3))
    r = h.unfile({"document_type": "lease_amendment"})
    assert r.status_code == 200
    assert r.get_json() == {"status": "success", "unfiled": 1}
    # tenant context is set BEFORE the DELETE, like every sibling route
    assert h.cursor.calls[0] == ("EXEC tenant.sp_setTenantContext ?", ["tenant-key"])
    deletes = h.sql("DELETE FROM DocumentTypeCategories")
    assert deletes == [("DELETE FROM DocumentTypeCategories WHERE document_type = ?",
                        ["lease_amendment"])]
    assert h.conn.commits == 1 and h.conn.closed
    assert "lease_amendment" not in h.cursor.rows, "the row must be gone (admin-only now)"


def test_document_type_is_trimmed_before_the_delete(monkeypatch):
    h = _Harness(monkeypatch, user=_session(3))
    r = h.unfile({"document_type": "  lease_amendment  "})
    assert r.status_code == 200 and r.get_json()["unfiled"] == 1
    assert h.sql("DELETE")[0][1] == ["lease_amendment"]


def test_unfile_logs_the_consequence_not_just_the_action(monkeypatch, caplog):
    h = _Harness(monkeypatch, user=_session(3, name="jo"))
    with caplog.at_level(logging.INFO, logger="test_unfile_type_category"):
        h.unfile({"document_type": "lease_amendment"})
    line = [m for m in caplog.messages if "[category-review]" in m]
    assert len(line) == 1
    assert "UNFILED" in line[0] and "admin-only" in line[0] and "by jo" in line[0]


# ============================================================ idempotent no-op
def test_unfiling_a_type_with_no_row_is_a_no_op_success(monkeypatch):
    h = _Harness(monkeypatch, rows=(), user=_session(3))
    r = h.unfile({"document_type": "never_filed"})
    assert r.status_code == 200
    assert r.get_json() == {"status": "success", "unfiled": 0}
    assert h.sql("DELETE")[0][1] == ["never_filed"]


def test_unfiling_twice_reports_zero_the_second_time(monkeypatch):
    h = _Harness(monkeypatch, user=_session(3))
    assert h.unfile({"document_type": "lease_amendment"}).get_json()["unfiled"] == 1
    assert h.unfile({"document_type": "lease_amendment"}).get_json()["unfiled"] == 0


# ================================================================= validation
@pytest.mark.parametrize("body", [
    pytest.param({}, id="absent"),
    pytest.param({"document_type": ""}, id="empty"),
    pytest.param({"document_type": "   "}, id="blank"),
    pytest.param({"document_type": None}, id="null"),
])
def test_missing_document_type_is_400_without_touching_the_db(monkeypatch, body):
    h = _Harness(monkeypatch, user=_session(3))
    r = h.unfile(body)
    assert r.status_code == 400
    assert r.get_json() == {"status": "error", "message": "document_type required"}
    assert h.db_calls == 0
    assert "lease_amendment" in h.cursor.rows


# ================================================================== role gate
@pytest.mark.parametrize("role", [1, 2], ids=["user", "developer"])
def test_non_admin_session_is_refused_by_the_real_gate(monkeypatch, role):
    h = _Harness(monkeypatch, user=_session(role, name="dev_erin"))
    r = h.unfile({"document_type": "lease_amendment"})
    assert r.status_code == 403
    assert r.get_json() == {"error": "Admin access required", "required_role": 3}
    assert h.db_calls == 0
    assert "lease_amendment" in h.cursor.rows, "a refused call must not remove access"


def test_anonymous_caller_is_401(monkeypatch):
    h = _Harness(monkeypatch)
    r = h.unfile({"document_type": "lease_amendment"})
    assert r.status_code == 401
    assert h.db_calls == 0


# =========================================================== re-filing works
def test_refiling_after_unfile_takes_the_insert_branch(monkeypatch):
    """Additive constraint: after unfiling, /save/type_category must still be
    able to file the type — its UPDATE sees rowcount 0 and INSERTs."""
    h = _Harness(monkeypatch, user=_session(3))
    assert h.unfile({"document_type": "lease_amendment"}).get_json()["unfiled"] == 1
    r = h.save({"document_type": "lease_amendment", "category_id": 86})
    assert r.status_code == 200 and r.get_json() == {"status": "success"}
    assert len(h.sql("UPDATE DocumentTypeCategories")) == 1
    inserts = h.sql("INSERT INTO DocumentTypeCategories")
    assert len(inserts) == 1 and inserts[0][1] == ["lease_amendment", 86, "admin"]
    assert h.cursor.rows == {"lease_amendment": 86}
