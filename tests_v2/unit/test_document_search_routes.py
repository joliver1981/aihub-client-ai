"""Route-level tests for the rebuilt document-search page and its neighbours
(2026-09-03): /document-search, /api/document-search/{categories,fields,
attributes}, /document/view/<id>, /document/delete/<id> and
/api/document-attributes/metadata.

The routes are lifted from app.py (app_route_harness, auth decorators
dropped — each route resolves identity itself via _caller_identity_or_session)
and driven through Flask's test client with a fake DB, a fake search gate, a
stubbed render_template and the REAL document_search_page module over the
fake cursor. The ACL resolver is the real doc_search_v3.acl with a faked DB.
"""
import json
import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, abort, jsonify, request, url_for
from werkzeug.exceptions import HTTPException

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

pytest.importorskip("jwt", reason="PyJWT not installed in this environment")

import shared_auth as sa  # noqa: E402
from doc_search_v3 import acl  # noqa: E402
import document_field_catalog as dfc  # noqa: E402
import document_search_wrapper as dsw  # noqa: E402
from app_route_harness import load_app_symbols  # noqa: E402
from test_document_search_page import Cur  # noqa: E402

pytestmark = pytest.mark.unit

_SECRET = "unit-test-secret-key-please-do-not-ship-0123456789"
GRANTS = {141: ["lease_agreement", "vendor_guide"], 10: [], 125: ["vendor_guide"]}
COUNTS = [("lease_agreement", 185), ("vendor_guide", 6), ("tenant_estoppel_certificate", 8)]
MAPPING = [(1, "leases", "Leases", "", "lease_agreement"), (2, "vendors", "Vendor guides", "", "vendor_guide"),
           (3, "estoppels", "Estoppels", "", "tenant_estoppel_certificate")]
DOC_HIDDEN = ("SKY-ESTOPPEL-S301.docx", "tenant_estoppel_certificate", 3, None, "", "", "", None, "", "")
DOC_VISIBLE = ("DollarGeneral.pdf", "vendor_guide", 12, None, "", "", "", None, "", "")


def _session(uid, role):
    return SimpleNamespace(is_authenticated=True, id=uid, role=role, user_name=f"u{uid}")


class _NoSession:
    is_authenticated = False
    role = 0


class _Gate:
    def __init__(self, busy=False):
        self.busy = busy
        self.entered = 0

    @contextmanager
    def slot(self):
        self.entered += 1
        yield None if self.busy else 1


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CC_JWT_SECRET", _SECRET)
    monkeypatch.setenv("API_KEY", "tenant-key")
    monkeypatch.delenv("DOC_V3_REQUIRE_IDENTITY", raising=False)
    dfc.invalidate(None)
    dfc._table_state.update(exists=None, checked=0.0)

    def _connect():
        class C:
            uid = None

            def execute(self, sql, *params):
                self.uid = params[0] if params else None

            def fetchall(self):
                return [(t,) for t in GRANTS.get(int(self.uid), [])]

        class Conn:
            def close(self):
                pass
        return Conn(), C()
    monkeypatch.setattr(acl, "_connect", _connect)


class World:
    def __init__(self, session_user=None, cur=None, busy=False, unified=None):
        self.cur = cur or Cur(**{"FROM Documents WHERE": COUNTS, "FROM DocumentCategories": MAPPING})
        self.db_calls = 0
        self.rendered = []
        self.purged = []
        self.unified_calls = []
        world = self

        def _get_db_connection():
            world.db_calls += 1
            return SimpleNamespace(cursor=lambda: world.cur, close=lambda: None,
                                   commit=lambda: None, rollback=lambda: None)

        def _render(template, **ctx):
            world.rendered.append((template, ctx))
            return json.dumps({"template": template, **ctx}, default=str)

        def _unified(query, max_results=None, user_id=None, user_role=None, document_types=None):
            world.unified_calls.append({"query": query, "document_types": document_types,
                                        "user_id": user_id, "user_role": user_role})
            return unified or {"ok": True, "passages": [], "answer": None, "text": "", "count": 0, "error": None}

        self.gate = _Gate(busy)
        ns = {"request": request, "jsonify": jsonify, "abort": abort, "os": os, "url_for": url_for,
              "_HTTPException": HTTPException,
              "logger": logging.getLogger("test_document_search_routes"),
              "render_template": _render, "get_db_connection": _get_db_connection,
              "current_user": session_user or _NoSession(), "_SEARCH_GATE": self.gate,
              "purge_document": lambda did: (world.purged.append(did) or ("success", "ok", 200))}
        load_app_symbols(["_InvalidUserAssertion", "_caller_identity", "_caller_identity_or_session",
                          "_DOC_SEARCH_DENIED", "_document_search_identity",
                          "_document_search_scope_from_args", "document_search_page",
                          "api_document_search_categories", "api_document_search_fields",
                          "api_document_search_attributes", "document_view_page",
                          "_document_visible", "delete_document",
                          "api_get_document_attributes_metadata"], ns)
        self.ns = ns
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.add_url_rule("/document-search", "document_search_page", ns["document_search_page"])
        app.add_url_rule("/api/document-search/categories", "cats", ns["api_document_search_categories"])
        app.add_url_rule("/api/document-search/fields", "fields", ns["api_document_search_fields"])
        app.add_url_rule("/api/document-search/attributes", "attrs", ns["api_document_search_attributes"])
        app.add_url_rule("/document/view/<string:document_id>", "document_view_page", ns["document_view_page"])
        app.add_url_rule("/document/delete/<document_id>", "delete_document", ns["delete_document"], methods=["POST"])
        app.add_url_rule("/api/document-attributes/metadata", "meta", ns["api_get_document_attributes_metadata"])
        self.app = app
        self.client = app.test_client()
        import document_search_wrapper as _dsw
        self._patch = pytest.MonkeyPatch()
        self._patch.setattr(_dsw, "document_search_unified", _unified)

    def close(self):
        self._patch.undo()

    def get(self, path, assertion=None, **query):
        headers = {"X-AIHub-User": assertion} if assertion else {}
        return self.client.get(path, headers=headers, query_string=query or None)

    def page(self, **query):
        r = self.get("/document-search", **query)
        return r, (json.loads(r.data) if r.status_code == 200 else None)


@pytest.fixture
def world():
    holder = []

    def make(**kw):
        w = World(**kw)
        holder.append(w)
        return w
    yield make
    for w in reversed(holder):      # undo patches newest-first, or an older undo re-installs a newer stub
        w.close()


def _assertion(uid, role):
    return sa.sign_user_assertion(uid, "t1", role)


# ================================================================ the page
def test_page_deny_all_session_renders_the_lock_and_touches_no_db(world):
    w = world(session_user=_session(10, 1))
    r, ctx = w.page()
    assert r.status_code == 200 and ctx["denied"] is True
    assert "access restriction" in ctx["denied_message"] and w.db_calls == 0


def test_page_developer_tree_is_scoped_and_hidden_type_is_refused(world):
    w = world(session_user=_session(141, 2))
    r, ctx = w.page()
    sql, params = w.cur.sql("FROM Documents WHERE")[0]
    assert "document_type IN (?,?)" in sql and set(params) == {"lease_agreement", "vendor_guide"}
    assert ctx["denied"] is False and ctx["can_delete"] is True and ctx["is_admin"] is False
    r, ctx = w.page(document_type="tenant_estoppel_certificate", query="x", search_mode="language")
    assert "not accessible" in ctx["error_message"] and ctx["search_results"] == []
    assert w.unified_calls == [], "a refused scope never reaches the engine"


def test_page_developer_search_delegates_with_the_scope(world):
    passages = [{"filename": "a.pdf", "page": "1", "document_id": "d1", "document_type": "lease_agreement",
                 "text": "rent", "relevance": 0.9}]
    w = world(session_user=_session(141, 2),
              unified={"ok": True, "passages": passages, "answer": None, "text": "", "count": 1, "error": None})
    r, ctx = w.page(document_type="lease_agreement", query="rent", search_mode="language", min_score="0")
    assert w.unified_calls == [{"query": "rent", "document_types": ["lease_agreement"], "user_id": 141, "user_role": 2}]
    assert ctx["total_results"] == 1 and ctx["search_results"][0]["document_id"] == "d1"
    assert w.gate.entered == 1 and ctx["scope_label"] == "lease agreement"


def test_page_busy_gate_is_an_honest_message(world):
    w = world(session_user=_session(141, 2), busy=True)
    r, ctx = w.page(query="rent", search_mode="language")
    assert "busy" in ctx["error_message"] and w.unified_calls == []


def test_page_admin_sees_everything_and_the_catalog_card(world, monkeypatch):
    monkeypatch.setattr(dfc, "stats", lambda cur: {"table": False, "rows": 0, "types": 0, "last_seen": None})
    w = world(session_user=_session(12, 3))
    r, ctx = w.page()
    assert "document_type IN" not in w.cur.sql("FROM Documents WHERE")[0][0]
    assert ctx["is_admin"] is True and ctx["catalog_stats"]["table"] is False
    assert ctx["tree"]["uncategorised"] == [] and len(ctx["tree"]["categories"]) == 3


def test_page_forged_assertion_is_403(world):
    w = world(session_user=_session(12, 3))
    assert w.get("/document-search", "garbage").status_code == 403


# ================================================================ JSON endpoints
def test_fields_endpoint_scope_rules(world, monkeypatch):
    monkeypatch.setattr(dfc, "suggest", lambda cur, types, q, limit: [
        {"path": "parties.tenant", "name": "tenant", "display_name": "Parties › Tenant", "doc_count": 180, "in_schema": True}]
        if types == ["lease_agreement"] else [])
    w = world(session_user=_session(141, 2))
    body = w.get("/api/document-search/fields", q="ten").get_json()
    assert body["fields"] == [] and "Choose a document type" in body["hint"]
    body = w.get("/api/document-search/fields", document_type="tenant_estoppel_certificate", q="a").get_json()
    assert body["access"] == "denied" and body["fields"] == []
    body = w.get("/api/document-search/fields", document_type="lease_agreement", q="ten").get_json()
    assert body["fields"][0]["path"] == "parties.tenant"
    w10 = world(session_user=_session(10, 1))
    assert w10.get("/api/document-search/fields", document_type="lease_agreement").get_json()["access"] == "denied"


def test_categories_endpoint_deny_all_and_scoped(world):
    w = world(session_user=_session(10, 1))
    body = w.get("/api/document-search/categories").get_json()
    assert body["access"] == "denied" and body["categories"] == [] and w.db_calls == 0
    # the fake answers the COUNT query with what the IN filter would leave
    w = world(session_user=_session(141, 2),
              cur=Cur(**{"FROM Documents WHERE": COUNTS[:2], "FROM DocumentCategories": MAPPING}))
    body = w.get("/api/document-search/categories").get_json()
    assert {c["slug"] for c in body["categories"]} == {"leases", "vendors"}
    sql, params = w.cur.sql("FROM Documents WHERE")[0]
    assert "document_type IN (?,?)" in sql and set(params) == {"lease_agreement", "vendor_guide"}


def test_attributes_endpoint_scoped(world):
    cur = Cur(**{"FROM Documents WHERE": COUNTS, "FROM DocumentCategories": MAPPING,
                 "FROM DocumentAttributions": [("Region", 4, 9, "East", "West")]})
    import document_search_page as dsp
    dsp._attr_cache.clear()
    w = world(session_user=_session(141, 2), cur=cur)
    body = w.get("/api/document-search/attributes", document_type="lease_agreement").get_json()
    assert body["attributes"][0]["attribute_name"] == "Region"
    sql, params = cur.sql("FROM DocumentAttributions")[0]
    assert "document_type IN (?)" in sql and params == ("lease_agreement",)
    body = w.get("/api/document-search/attributes", document_type="tenant_estoppel_certificate").get_json()
    assert body["access"] == "denied"


# ================================================================ view + delete
def _view_cur(doc):
    return Cur(**{"FROM Documents d WHERE d.document_id = ?": [doc],
                  "FROM DocumentPages WHERE document_id = ? AND page_number = ?": [("p1", 1, "text")]})


def test_view_hidden_type_is_404_like_missing_and_deny_all_is_404(world):
    w = world(session_user=_session(141, 2), cur=_view_cur(DOC_HIDDEN))
    assert w.get("/document/view/doc-hidden").status_code == 404
    w = world(session_user=_session(141, 2), cur=_view_cur(None))
    assert w.get("/document/view/doc-missing").status_code == 404
    w = world(session_user=_session(10, 1), cur=_view_cur(DOC_VISIBLE))
    assert w.get("/document/view/doc-visible").status_code == 404 and w.db_calls == 0


def test_view_visible_type_renders(world):
    w = world(session_user=_session(141, 2), cur=_view_cur(DOC_VISIBLE))
    r = w.get("/document/view/doc-visible")
    assert r.status_code == 200 and w.rendered and w.rendered[0][0] == "document_view.html"


def test_delete_requires_developer_and_visibility(world):
    w = world(session_user=_session(125, 1))
    r = w.client.post("/document/delete/doc-1")
    assert r.status_code == 403 and w.purged == []
    cur = Cur(**{"SELECT document_type FROM Documents WHERE document_id": [("tenant_estoppel_certificate",)]})
    w = world(session_user=_session(141, 2), cur=cur)
    r = w.client.post("/document/delete/doc-hidden")
    assert r.status_code == 404 and w.purged == []
    cur = Cur(**{"SELECT document_type FROM Documents WHERE document_id": [("vendor_guide",)]})
    w = world(session_user=_session(141, 2), cur=cur)
    r = w.client.post("/document/delete/doc-visible")
    assert r.status_code == 200 and w.purged == ["doc-visible"]
    w = world(session_user=_session(12, 3))
    r = w.client.post("/document/delete/any")
    assert r.status_code == 200 and w.purged == ["any"] and w.db_calls == 0, "admin: no type lookup needed"


# ================================================================ attribute metadata
def test_attribute_metadata_is_scoped_by_the_acl(world, monkeypatch):
    import DocUtils
    calls = []

    def _meta(document_type=None, return_format="dict"):
        calls.append(document_type)
        return json.dumps({"attribute_metadata": [], "total_unique_attributes": 0})
    monkeypatch.setattr(DocUtils, "get_document_attributes_metadata", _meta)
    w = world(session_user=_session(141, 2))
    assert w.get("/api/document-attributes/metadata").status_code == 200
    assert calls[-1] == ["lease_agreement", "vendor_guide"], "restricted caller -> the allow list"
    w.get("/api/document-attributes/metadata", document_type="lease_agreement")
    assert calls[-1] == "lease_agreement"
    body = w.get("/api/document-attributes/metadata", document_type="tenant_estoppel_certificate").get_json()
    assert body["access"] == "denied" and len(calls) == 2, "a type outside the grants never reaches DocUtils"
    w = world(session_user=_session(12, 3))
    w.get("/api/document-attributes/metadata")
    assert calls[-1] is None, "admin -> unrestricted"
    w = world(session_user=_session(10, 1))
    assert w.get("/api/document-attributes/metadata").get_json()["access"] == "denied"
