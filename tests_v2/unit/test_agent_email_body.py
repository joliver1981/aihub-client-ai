"""Agent Email (A6) full_body envelope parsing — regression for James's
2026-08-09 live repro: every inbound email read as '(empty body)'.

The cloud message proxy returns {"success": true, "message": {body_text,
body_plain, stripped_text, ...}} (the shape email_receive_client unwraps
with result.get('message')). The original A6 code looked for a nonexistent
'content' key and Mailgun hyphen field names, so full_body always returned
None — and poll rows carry NO body at all, so nothing could recover it.

Runs standalone (python test_agent_email_body.py) or under pytest.
"""
import asyncio
import os
import sys

import httpx

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

import email_client  # noqa: E402


_RealAsyncClient = httpx.AsyncClient   # patching email_client.httpx patches
                                       # THIS module's httpx too (same object)


def _stub_client(payload, status=200):
    def handler(request):
        return httpx.Response(status, json=payload)
    return lambda **kw: _RealAsyncClient(transport=httpx.MockTransport(handler))


def _body_with(monkeypatched_payload, status=200):
    orig = email_client.httpx.AsyncClient
    email_client.httpx.AsyncClient = _stub_client(monkeypatched_payload, status)
    try:
        return asyncio.run(email_client.full_body("some-message-key"))
    finally:
        email_client.httpx.AsyncClient = orig


def test_real_proxy_envelope_message_key():
    """The LIVE shape (verified against event 48, 2026-08-09)."""
    body = _body_with({"success": True, "message": {
        "body_text": "Great, now can you tell me how many invoices are in ERPDB?\r\n\r\nOn Sun...",
        "body_plain": "Great, now...", "stripped_text": "Great, now...",
        "body_html": "<div>...</div>"}})
    assert body and body.startswith("Great, now can you tell me how many "
                                    "invoices are in ERPDB?")


def test_body_text_preferred_over_stripped():
    body = _body_with({"success": True, "message": {
        "body_text": "full thread", "stripped_text": "stripped"}})
    assert body == "full thread"


def test_stripped_fallback_when_no_body_text():
    body = _body_with({"success": True, "message": {
        "stripped_text": "just the new text"}})
    assert body == "just the new text"


def test_legacy_content_shape_still_parses():
    body = _body_with({"content": {"body_text": "older shape"}})
    assert body == "older shape"


def test_empty_message_returns_none_not_crash():
    assert _body_with({"success": True, "message": {}}) is None
    assert _body_with({"success": True, "message": "weird"}) is None
    assert _body_with({"success": False}, status=404) is None


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"[PASS] {name}")
            except AssertionError as e:
                fails += 1
                print(f"[FAIL] {name}: {e}")
    print(f"{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
