"""/api/documents, /api/document-types, /api/documents/<id> — caller identity +
v3 category ACL (doc-acl G2, 2026-09-03; docs/handoff-doc-acl-g1-g3.md §4).

THE CONTRACT (assertion-only: the browser session path stays unfiltered —
decision D1 in the handoff):
  * X-AIHub-User ABSENT  -> today's SQL, unfiltered
  * PRESENT and valid    -> `AND d.document_type IN (...)` on the ROW query,
                            the COUNT (paging) and the STATS query alike — a
                            filtered row set with an unfiltered total would
                            hand the model a contradiction it cannot explain
  * PRESENT, zero grants -> HTTP 200 with the same documents/pagination/stats
                            shape zeroed (NOT 403: the agent's list_documents
                            would render that as an outage), and no DB round
                            trip at all
  * PRESENT and invalid  -> 403
  * /api/documents/<id>  -> a document whose type the caller cannot see is
                            404 exactly like a missing one (no id-oracle)

Route source is lifted from app.py (app_route_harness); the ACL resolver is
the real doc_search_v3.acl over a faked DB connect; the routes' own DB is a
recording fake.
"""
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest
from flask import Flask, jsonify, request

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

pytest.importorskip("jwt", reason="PyJWT not installed in this environment")

import shared_auth as sa  # noqa: E402
from doc_search_v3 import acl  # noqa: E402
from app_route_harness import load_app_symbols  # noqa: E402

pytestmark = pytest.mark.unit

_SECRET = "unit-test-secret-key-please-do-not-ship-0123456789"
GRANTS = {141: ["vendor_guide", "lease_agreement"], 10: []}

_ROW_VISIBLE = ("doc-1", "vendor guide.pdf", "vendor_guide", 12, "REF-1", None, None,
                None, datetime(2026, 9, 1, 10, 0), r"C:\in\vendor guide.pdf", None)
_ROW_HIDDEN = ("doc-2", "Project Falcon - termination terms.pdf", "settlement_agreement",
               3, None, None, None, None, datetime(2026, 9, 2, 10, 0),
               r"C:\in\falcon.pdf", None)


class _Cursor:
    """Records every execute(); answers each query by its shape."""

    def __init__(self, rows, single):
        self.calls = []
        self._rows = rows
        self._single = single
        self._last = ""

    def execute(self, sql, params=None):
        self._last = " ".join(sql.split())
        self.calls.append((self._last, list(params) if isinstance(params, (list, tuple))
                           else ([params] if params is not None else [])))

    def fetchone(self):
        if "AS cnt" in self._last:                       # paging COUNT
            return (len(self._rows),)
        if "total_documents" in self._last:               # STATS
            return (len(self._rows), 42, 2, datetime(2026, 9, 2, 10, 0))
        return self._single                               # single-document GET

    def fetchall(self):
        if "GROUP BY document_type" in self._last:        # /api/document-types
            return [("vendor_guide", 5), ("settlement_agreement", 1)]
        return list(self._rows)


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CC_JWT_SECRET", _SECRET)
    monkeypatch.setenv("API_KEY", "tenant-key")
    monkeypatch.delenv("DOC_V3_REQUIRE_IDENTITY", raising=False)


@pytest.fixture
def fake_grants(monkeypatch):
    def _connect():
        class Cur:
            uid = None

            def execute(self, sql, *params):
                self.uid = params[0] if params else None

            def fetchall(self):
                return [(t,) for t in GRANTS.get(int(self.uid), [])]

        class Conn:
            def close(self):
                pass

        return Conn(), Cur()
    monkeypatch.setattr(acl, "_connect", _connect)


class _Harness:
    def __init__(self, rows=(_ROW_VISIBLE,), single=_ROW_VISIBLE):
        self.cursor = _Cursor(list(rows), single)
        self.db_calls = 0

        def _get_db_connection():
            self.db_calls += 1
            return _Conn(self.cursor)

        ns = {"request": request, "jsonify": jsonify, "os": os,
              "logger": logging.getLogger("test_api_documents_acl"),
              "get_db_connection": _get_db_connection}
        load_app_symbols(["_InvalidUserAssertion", "_caller_identity",
                          "api_get_documents", "api_get_document_types",
                          "api_get_document"], ns)
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.add_url_rule("/api/documents", "api_get_documents",
                         ns["api_get_documents"], methods=["GET"])
        app.add_url_rule("/api/document-types", "api_get_document_types",
                         ns["api_get_document_types"], methods=["GET"])
        app.add_url_rule("/api/documents/<string:document_id>", "api_get_document",
                         ns["api_get_document"], methods=["GET"])
        self.client = app.test_client()

    def get(self, path, assertion=None, **query):
        headers = {"X-AIHub-User": assertion} if assertion else {}
        return self.client.get(path, headers=headers, query_string=query or None)

    def sql(self, needle):
        """The recorded (sql, params) pairs containing `needle`."""
        return [(s, p) for s, p in self.cursor.calls if needle in s]


def _assertion(uid, role, **kw):
    return sa.sign_user_assertion(uid, "t1", role, **kw)


# =============================================================== /api/documents
def test_documents_absent_header_is_unfiltered(fake_grants):
    h = _Harness()
    r = h.get("/api/documents", per_page=5)
    assert r.status_code == 200
    body = r.get_json()
    assert [d["document_id"] for d in body["documents"]] == ["doc-1"]
    rows_sql = h.sql("ORDER BY d.processed_at")[0][0]
    stats_sql = h.sql("total_documents")[0][0]
    assert "document_type IN" not in rows_sql
    assert "document_type IN" not in stats_sql
    assert body["pagination"]["total_count"] == 1
    assert body["stats"]["total_documents"] == 1


def test_documents_granted_user_filters_rows_count_and_stats(fake_grants):
    h = _Harness()
    r = h.get("/api/documents", _assertion(141, 2), per_page=5)
    assert r.status_code == 200
    rows_sql, rows_params = h.sql("ORDER BY d.processed_at")[0]
    count_sql, count_params = h.sql("AS cnt")[0]
    stats_sql, stats_params = h.sql("total_documents")[0]
    assert "d.document_type IN (?,?)" in rows_sql
    assert rows_params[:2] == ["vendor_guide", "lease_agreement"]   # then OFFSET/FETCH
    assert "d.document_type IN (?,?)" in count_sql
    assert count_params == ["vendor_guide", "lease_agreement"]
    assert "document_type IN (?,?)" in stats_sql, \
        "stats must carry the same filter or total_documents contradicts the rows"
    assert stats_params == ["vendor_guide", "lease_agreement"]


def test_documents_user_filter_composes_with_the_type_and_search_filters(fake_grants):
    h = _Harness()
    r = h.get("/api/documents", _assertion(141, 2), document_type="vendor_guide",
              search="carton")
    assert r.status_code == 200
    rows_sql, rows_params = h.sql("ORDER BY d.processed_at")[0]
    assert "d.document_type = ?" in rows_sql and "d.filename LIKE ?" in rows_sql
    assert "d.document_type IN (?,?)" in rows_sql
    assert rows_params[:4] == ["vendor_guide", "%carton%", "vendor_guide", "lease_agreement"]


def test_documents_zero_grants_is_an_honest_empty_payload_not_403(fake_grants):
    h = _Harness()
    r = h.get("/api/documents", _assertion(10, 1), page=2, per_page=7)
    assert r.status_code == 200
    body = r.get_json()
    assert body["documents"] == []
    assert body["pagination"] == {"page": 2, "per_page": 7, "total_count": 0,
                                  "total_pages": 0, "has_prev": True, "has_next": False}
    assert body["stats"] == {"total_documents": 0, "total_pages": 0,
                             "document_types": 0, "last_updated": None}
    # Additive marker so the agent's list_documents says "no access", not
    # "the store is empty" (james 2026-09-03).
    assert body["access"] == "denied"
    assert "do not have access to any document categories" in body["message"]
    assert "not an empty store" in body["message"]
    assert "Groups page" in body["message"]
    assert h.db_calls == 0, "deny-all must not touch the DB ([] would mean NO filter there)"


def test_documents_granted_user_payload_has_no_denied_marker(fake_grants):
    h = _Harness()
    body = h.get("/api/documents", _assertion(141, 2)).get_json()
    assert "access" not in body and "message" not in body


def test_documents_admin_is_unfiltered_without_the_tables(monkeypatch):
    monkeypatch.setattr(acl, "_connect",
                        lambda: (_ for _ in ()).throw(AssertionError("must not connect")))
    h = _Harness()
    r = h.get("/api/documents", _assertion(12, 3))
    assert r.status_code == 200
    assert "document_type IN" not in h.sql("ORDER BY d.processed_at")[0][0]


@pytest.mark.parametrize("make_token", [
    pytest.param(lambda: "garbage.token.value", id="garbage"),
    pytest.param(lambda: sa.sign_cc_token({"user_id": 141, "role": 2, "tenant_id": "t1"}),
                 id="wrong-audience"),
    pytest.param(lambda: _assertion(141, 2, ttl_seconds=-60), id="expired"),
])
def test_documents_forged_assertion_is_403(fake_grants, make_token):
    h = _Harness()
    r = h.get("/api/documents", make_token())
    assert r.status_code == 403
    assert h.db_calls == 0


def test_documents_resolver_failure_is_deny_all(monkeypatch):
    monkeypatch.setattr(acl, "_connect",
                        lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    h = _Harness()
    r = h.get("/api/documents", _assertion(141, 2))
    assert r.status_code == 200 and r.get_json()["documents"] == []
    assert h.db_calls == 0


# ========================================================== /api/document-types
def test_types_absent_header_is_unfiltered(fake_grants):
    h = _Harness()
    r = h.get("/api/document-types")
    assert r.status_code == 200
    assert [t["name"] for t in r.get_json()] == ["vendor_guide", "settlement_agreement"]
    assert "document_type IN" not in h.sql("GROUP BY document_type")[0][0]


def test_types_granted_user_is_filtered(fake_grants):
    h = _Harness()
    r = h.get("/api/document-types", _assertion(141, 2))
    assert r.status_code == 200
    sql, params = h.sql("GROUP BY document_type")[0]
    assert "document_type IN (?,?)" in sql
    assert params == ["vendor_guide", "lease_agreement"]


def test_types_zero_grants_is_empty_list_200(fake_grants):
    h = _Harness()
    r = h.get("/api/document-types", _assertion(10, 1))
    assert r.status_code == 200 and r.get_json() == []
    assert h.db_calls == 0


def test_types_forged_assertion_is_403(fake_grants):
    h = _Harness()
    assert h.get("/api/document-types", "garbage").status_code == 403


# ========================================================= /api/documents/<id>
def test_single_absent_header_returns_any_type(fake_grants):
    h = _Harness(single=_ROW_HIDDEN)
    r = h.get("/api/documents/doc-2")
    assert r.status_code == 200
    assert r.get_json()["filename"] == "Project Falcon - termination terms.pdf"


def test_single_granted_user_sees_a_visible_type(fake_grants):
    h = _Harness(single=_ROW_VISIBLE)
    r = h.get("/api/documents/doc-1", _assertion(141, 2))
    assert r.status_code == 200
    assert r.get_json()["document_type"] == "vendor_guide"


def test_single_hidden_type_is_404_like_a_missing_document(fake_grants):
    h = _Harness(single=_ROW_HIDDEN)
    r = h.get("/api/documents/doc-2", _assertion(141, 2))
    assert r.status_code == 404
    body = r.get_json()
    assert "Falcon" not in str(body), "the hidden filename must not leak in the error"
    missing = _Harness(single=None).get("/api/documents/doc-9", _assertion(141, 2))
    assert missing.status_code == 404
    assert missing.get_json() == body, "hidden and missing must be indistinguishable"


def test_single_missing_document_is_404_not_500(fake_grants):
    h = _Harness(single=None)
    r = h.get("/api/documents/doc-9")
    assert r.status_code == 404


def test_single_zero_grants_is_404_without_a_db_round_trip(fake_grants):
    h = _Harness(single=_ROW_VISIBLE)
    r = h.get("/api/documents/doc-1", _assertion(10, 1))
    assert r.status_code == 404
    assert h.db_calls == 0


def test_single_forged_assertion_is_403(fake_grants):
    h = _Harness()
    assert h.get("/api/documents/doc-1", "garbage").status_code == 403
