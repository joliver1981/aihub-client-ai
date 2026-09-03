"""Unit tests for the signed OAuth state (builder_mcp/agent_integration/oauth_state.py).

The state carries the on-prem return address through the provider and the
cloud broker; an unverified return address would be an open redirect. These
tests pin the wire format with a known-answer vector (the cloud repo pins the
SAME vector — the two modules are copies) and exercise every refusal path the
broker relies on (T6 in the handoff).
"""
from __future__ import annotations

import json

import pytest

from builder_mcp.agent_integration import oauth_state as s

pytestmark = pytest.mark.unit

KEY = "TEST-API-KEY-0000"
RET = "http://10.0.0.7:5001/api/mcp/oauth/callback"
NONCE = "nonce-0123456789abcdefghij"
NOW = 1_800_000_000

# Known-answer vector — must be byte-identical in aihub-api/tests/test_mcp_oauth_broker.py
KAT_STATE = (
    "eyJlIjoxODAwMDAwNjAwLCJuIjoibm9uY2UtMDEyMzQ1Njc4OWFiY2RlZmdoaWoiLCJyIjoiaHR0cDovLzEwLjAuMC43"
    "OjUwMDEvYXBpL21jcC9vYXV0aC9jYWxsYmFjayIsInQiOjQyLCJ2IjoxfQ"
    ".-suSlh4v16c3JIjxf7EzPQfDcyCcLNJbBQOA7Ls9Ty0"
)


def _lookup(tenant_id):
    return KEY if tenant_id == 42 else None


def test_known_answer_vector_is_stable():
    state, nonce = s.sign_state(KEY, 42, RET, nonce=NONCE, ttl_seconds=600, now=NOW)
    assert nonce == NONCE
    assert state == KAT_STATE


def test_round_trip_returns_trusted_payload():
    state, nonce = s.sign_state(KEY, 42, RET, now=NOW)
    payload = s.verify_state(state, _lookup, now=NOW + 100)
    assert payload == {"v": 1, "t": 42, "r": RET, "n": nonce, "e": NOW + 600}


def test_payload_is_compact_sorted_json_without_padding():
    body = KAT_STATE.split(".")[0]
    assert "=" not in KAT_STATE
    decoded = json.loads(s._b64u_decode(body))
    assert list(decoded.keys()) == ["e", "n", "r", "t", "v"]


@pytest.mark.parametrize("bad, reason", [
    ("", "malformed"),
    ("no-dot-here", "malformed"),
    (".sig", "malformed"),
    ("body.", "malformed"),
    ("x" * 5000, "malformed"),
    ("!!!.sig", "bad_payload"),                       # not base64url
    (s._b64u_encode(b"[1,2]") + ".sig", "bad_payload"),  # JSON but not an object
    (s._b64u_encode(b"{\"t\":1}") + ".sig", "missing_field"),
])
def test_structural_refusals(bad, reason):
    with pytest.raises(s.StateError) as ei:
        s.verify_state(bad, _lookup, now=NOW)
    assert ei.value.reason == reason


def _forge(payload: dict, key: str = KEY) -> str:
    body = s._b64u_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return f"{body}.{s._signature(key, body)}"


def _base_payload(**over):
    p = {"v": 1, "t": 42, "r": RET, "n": NONCE, "e": NOW + 600}
    p.update(over)
    return p


def test_tampered_signature_is_refused():
    state, _ = s.sign_state(KEY, 42, RET, now=NOW)
    body, sig = state.split(".")
    flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
    with pytest.raises(s.StateError) as ei:
        s.verify_state(f"{body}.{flipped}", _lookup, now=NOW)
    assert ei.value.reason == "bad_signature"


def test_signed_with_another_tenants_key_is_refused():
    state = _forge(_base_payload(), key="SOME-OTHER-KEY")
    with pytest.raises(s.StateError) as ei:
        s.verify_state(state, _lookup, now=NOW)
    assert ei.value.reason == "bad_signature"


def test_unknown_tenant_is_refused():
    state = _forge(_base_payload(t=7))
    with pytest.raises(s.StateError) as ei:
        s.verify_state(state, _lookup, now=NOW)
    assert ei.value.reason == "unknown_tenant"


def test_expired_state_is_refused():
    state, _ = s.sign_state(KEY, 42, RET, ttl_seconds=600, now=NOW)
    with pytest.raises(s.StateError) as ei:
        s.verify_state(state, _lookup, now=NOW + 601)
    assert ei.value.reason == "expired"
    assert s.verify_state(state, _lookup, now=NOW + 600)["t"] == 42  # boundary inclusive


def test_return_url_rewritten_after_signing_is_refused():
    state, _ = s.sign_state(KEY, 42, RET, now=NOW)
    body, sig = state.split(".")
    payload = json.loads(s._b64u_decode(body))
    payload["r"] = "http://attacker.example/api/mcp/oauth/callback"
    evil_body = s._b64u_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    with pytest.raises(s.StateError) as ei:
        s.verify_state(f"{evil_body}.{sig}", _lookup, now=NOW)
    assert ei.value.reason == "bad_signature"


@pytest.mark.parametrize("url", [
    "http://user@10.0.0.7:5001/api/mcp/oauth/callback",        # userinfo
    "http://10.0.0.7:5001/api/mcp/oauth/callback?x=1",         # query smuggling
    "http://10.0.0.7:5001/api/mcp/oauth/callback#frag",
    "javascript:alert(1)//api/mcp/oauth/callback",
    "ftp://10.0.0.7/api/mcp/oauth/callback",
    "http:///api/mcp/oauth/callback",                          # no host
    "http://10.0.0.7:5001/somewhere/else",                     # foreign path
    "http://10.0.0.7:5001/api/mcp/oauth/callback\r\nX: y",     # control chars
    "http://10.0.0.7:5001\\@evil/api/mcp/oauth/callback",      # backslash trick
    "http://10.0.0.7:99999/api/mcp/oauth/callback",            # bad port
    "",
])
def test_bad_return_urls_are_refused_even_when_correctly_signed(url):
    # Signed with the RIGHT key: the URL shape check must still refuse.
    state = _forge(_base_payload(r=url))
    with pytest.raises(s.StateError) as ei:
        s.verify_state(state, _lookup, now=NOW)
    assert ei.value.reason == "bad_return_url"


def test_sign_refuses_a_return_url_the_broker_would_reject():
    with pytest.raises(s.StateError):
        s.sign_state(KEY, 42, "http://10.0.0.7:5001/not/the/callback", now=NOW)
    with pytest.raises(ValueError):
        s.sign_state("", 42, RET, now=NOW)


def test_https_return_url_and_script_root_prefix_are_accepted():
    for url in ("https://hub.example.com/api/mcp/oauth/callback",
                "http://10.0.0.7:5001/aihub/api/mcp/oauth/callback"):
        state, _ = s.sign_state(KEY, 42, url, now=NOW)
        assert s.verify_state(state, _lookup, now=NOW)["r"] == url


def test_verify_with_key_ignores_tenant_id():
    state, _ = s.sign_state(KEY, 999, RET, now=NOW)
    assert s.verify_state_with_key(state, KEY, now=NOW)["t"] == 999
    with pytest.raises(s.StateError):
        s.verify_state_with_key(state, "wrong", now=NOW)


def test_parse_state_does_not_verify():
    state = _forge(_base_payload(), key="not-the-key")
    payload, body, sig = s.parse_state(state)
    assert payload["t"] == 42            # usable for a rate-limit bucket / log line only


@pytest.mark.parametrize("url, origin", [
    ("HTTP://LocalHost:80/x", "http://localhost"),
    ("https://a.b:443/x", "https://a.b"),
    ("http://10.0.0.7:5001/api/mcp/oauth/callback", "http://10.0.0.7:5001"),
    ("http://[::1]:5001/x", "http://[::1]:5001"),
    ("http://localhost/", "http://localhost"),
])
def test_origin_of(url, origin):
    assert s.origin_of(url) == origin


def test_append_query_encodes_and_chains():
    assert s.append_query("http://h/api/mcp/oauth/callback", {"code": "c d&", "state": "a.b"}) == \
        "http://h/api/mcp/oauth/callback?code=c+d%26&state=a.b"
    assert s.append_query("http://h/p?x=1", {"code": "c"}) == "http://h/p?x=1&code=c"
