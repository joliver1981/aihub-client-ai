"""/api/internal/document-records — caller identity + v3 category ACL
(doc-acl G1, 2026-09-03; docs/handoff-doc-acl-g1-g3.md §3).

THE CONTRACT — the route must behave exactly like document-search-unified:
  * X-AIHub-User ABSENT  -> unrestricted (allowed_document_types=None); the
                            identity-less internal callers (scheduler,
                            automations, email dispatcher) keep working
  * PRESENT and valid    -> query_document_records gets that user's allow list
  * PRESENT, zero grants -> query_document_records is NEVER called (its
                            `if allowed_document_types:` treats [] as NO filter
                            — fail-open); a TERMINAL `mode: "denied"` result
                            with fallback=False comes back instead
  * PRESENT and invalid  -> 403 (forged / wrong audience / expired / no
                            secret), never "treat as missing"

The route source is lifted straight out of app.py (app_route_harness — no
app.py import); the ACL resolver is the REAL doc_search_v3.acl with only its
DB connect faked, so the three-state contract is exercised end to end.
"""
import logging
import os
import sys
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
import document_records_query as drq  # noqa: E402
from doc_search_v3 import acl  # noqa: E402
from app_route_harness import load_app_symbols  # noqa: E402

pytestmark = pytest.mark.unit

_SECRET = "unit-test-secret-key-please-do-not-ship-0123456789"
# The shapes seen live on 2026-09-03: `developer` (141, role 2) resolves to a
# type list; `tbrady` (10, role 1) is in no group -> [] = deny-all.
GRANTS = {141: ["vendor_guide", "lease_agreement"], 10: []}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CC_JWT_SECRET", _SECRET)
    monkeypatch.delenv("DOC_V3_REQUIRE_IDENTITY", raising=False)


@pytest.fixture
def fake_grants(monkeypatch):
    """The REAL resolver over a faked DB: grant rows come from GRANTS by uid."""
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


@pytest.fixture
def records_calls(monkeypatch):
    """Replace the query layer with a recorder; returns the list of kwargs."""
    calls = []

    def _fake_query(**kw):
        calls.append(kw)
        return {"ok": True, "mode": "query", "rows": [], "fallback": False,
                "coverage": [], "text": "RECORDS"}
    monkeypatch.setattr(drq, "query_document_records", _fake_query)
    return calls


@pytest.fixture
def client():
    ns = {"request": request, "jsonify": jsonify,
          "logger": logging.getLogger("test_internal_records_acl")}
    load_app_symbols(["_InvalidUserAssertion", "_caller_identity",
                      "internal_document_records"], ns)
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.add_url_rule("/api/internal/document-records", "internal_document_records",
                     ns["internal_document_records"], methods=["POST"])
    return app.test_client()


def _assertion(uid, role, **kw):
    return sa.sign_user_assertion(uid, "t1", role, **kw)


def _post(client, body=None, assertion=None):
    headers = {"X-AIHub-User": assertion} if assertion else {}
    return client.post("/api/internal/document-records", json=body or {},
                       headers=headers)


# ---------------------------------------------------------------- absent
def test_absent_header_is_unrestricted(client, records_calls, fake_grants):
    r = _post(client, {"record_set": "vendor_requirements", "search": "carton"})
    assert r.status_code == 200
    assert len(records_calls) == 1
    call = records_calls[0]
    assert call["allowed_document_types"] is None
    assert call["record_set"] == "vendor_requirements" and call["search"] == "carton"


# ---------------------------------------------------------------- granted
def test_granted_user_is_filtered_to_their_types(client, records_calls, fake_grants):
    r = _post(client, {"record_set": "vendor_requirements"}, _assertion(141, 2))
    assert r.status_code == 200
    assert records_calls[0]["allowed_document_types"] == ["vendor_guide", "lease_agreement"]


def test_admin_is_unrestricted_without_touching_the_tables(client, records_calls,
                                                           monkeypatch):
    monkeypatch.setattr(acl, "_connect",
                        lambda: (_ for _ in ()).throw(AssertionError("must not connect")))
    r = _post(client, {}, _assertion(12, 3))
    assert r.status_code == 200
    assert records_calls[0]["allowed_document_types"] is None


# ---------------------------------------------------------------- deny-all
def test_zero_grants_never_reaches_the_query_layer(client, records_calls, fake_grants):
    r = _post(client, {"record_set": "vendor_requirements"}, _assertion(10, 1))
    assert r.status_code == 200
    res = r.get_json()["result"]
    assert records_calls == [], \
        "[] must never be passed on — the query layer treats it as NO filter"
    assert res["ok"] is True and res["mode"] == "denied"
    assert res["rows"] == [] and res["coverage"] == []
    assert res["fallback"] is False, \
        "a denied result is terminal; fallback:true would send the agent to search_documents for a second refusal"
    assert "do not have access to any document categories" in res["text"]
    assert "Groups page" in res["text"]


def test_resolver_db_failure_is_deny_all_not_unrestricted(client, records_calls,
                                                          monkeypatch):
    monkeypatch.setattr(acl, "_connect",
                        lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    r = _post(client, {"record_set": "vendor_requirements"}, _assertion(141, 2))
    assert r.status_code == 200
    assert r.get_json()["result"]["mode"] == "denied"
    assert records_calls == []


# ---------------------------------------------------------------- forged
@pytest.mark.parametrize("make_token", [
    pytest.param(lambda: "garbage.token.value", id="garbage"),
    pytest.param(lambda: sa.sign_cc_token({"user_id": 141, "role": 2, "tenant_id": "t1"}),
                 id="wrong-audience-cc-session"),
    pytest.param(lambda: _assertion(141, 2, ttl_seconds=-60), id="expired"),
    pytest.param(lambda: _assertion(141, 2, secret="some-other-secret-0123456789abcdef"),
                 id="bad-signature"),
])
def test_invalid_assertion_is_403_not_missing(client, records_calls, fake_grants,
                                              make_token):
    r = _post(client, {"record_set": "vendor_requirements"}, make_token())
    assert r.status_code == 403
    assert r.get_json()["message"] == "invalid user assertion"
    assert records_calls == [], "a forged assertion must not degrade to unrestricted"


def test_assertion_without_a_signing_secret_is_403(client, records_calls, monkeypatch):
    token = _assertion(141, 2)                 # minted while the secret was set
    monkeypatch.delenv("CC_JWT_SECRET", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("AI_HUB_API_KEY", raising=False)
    r = _post(client, {"record_set": "vendor_requirements"}, token)
    assert r.status_code == 403
    assert records_calls == []


# ---------------------------------------------------------------- helper
def test_caller_identity_three_states(fake_grants):
    ns = {"request": request, "jsonify": jsonify, "logger": logging.getLogger("t")}
    load_app_symbols(["_InvalidUserAssertion", "_caller_identity"], ns)
    app = Flask(__name__)
    with app.test_request_context("/x"):
        assert ns["_caller_identity"]() == (None, None)
    with app.test_request_context("/x", headers={"X-AIHub-User": _assertion(141, 2)}):
        assert ns["_caller_identity"]() == ("141", 2)      # sub is a STRING by design
    with app.test_request_context("/x", headers={"X-AIHub-User": "nope"}):
        with pytest.raises(ns["_InvalidUserAssertion"]):
            ns["_caller_identity"]()
