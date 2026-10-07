"""BYOK Claude calls from processes WITHOUT the anthropic package route via the
Documents API (2026-10-07).

The main app / executor / agent+knowledge APIs (aihub2.1) and the vector API
(aihubvector2) deliberately have no `anthropic` package. In direct mode (BYOK)
create_anthropic_client() and claudeQuickPrompt used to `import anthropic` in
the calling process and fail (knowledge summaries, LLM chunking, re-ranker...
silently degraded). They now hand the call to the Documents API's loopback-only
/internal/anthropic/messages endpoint, authenticated with the machine-bound
internal key. Runs in aihub2.1 (no anthropic package — the real condition).

Force-add to git (gitignore hides test*.py).
"""
import importlib
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import api_keys_config as akc  # noqa: E402

DIRECT = {'use_direct_api': True, 'api_key': 'sk-byok-test', 'model': 'm',
          'max_tokens': 10, 'source': 'byok'}


def test_internal_service_key_matches_role_decorators():
    import role_decorators
    assert akc.internal_service_key() == role_decorators.get_internal_api_key()
    assert len(akc.internal_service_key()) == 64


def test_internal_service_key_is_read_only(monkeypatch):
    empty = tempfile.mkdtemp()
    monkeypatch.setenv('AIHUB_DATA_DIR', empty)
    assert akc.internal_service_key() == ''
    assert not (Path(empty) / 'secrets' / '.machine_id').exists()   # never created


def test_direct_mode_without_package_routes_via_doc_api(monkeypatch):
    monkeypatch.setattr(akc, 'get_anthropic_config', lambda: dict(DIRECT))
    monkeypatch.setitem(sys.modules, 'anthropic', None)      # import anthropic -> ImportError
    monkeypatch.delenv('BYOK_ROUTE_VIA_DOC_API', raising=False)
    client, conf = akc.create_anthropic_client()
    assert isinstance(client, akc.ByokRelayClient)
    assert conf['source'] == 'byok'


def test_kill_switch_restores_the_old_import_error(monkeypatch):
    monkeypatch.setattr(akc, 'get_anthropic_config', lambda: dict(DIRECT))
    monkeypatch.setitem(sys.modules, 'anthropic', None)
    monkeypatch.setenv('BYOK_ROUTE_VIA_DOC_API', 'false')
    with pytest.raises(ImportError):
        akc.create_anthropic_client()


def test_proxy_mode_is_untouched(monkeypatch):
    monkeypatch.setattr(akc, 'get_anthropic_config',
                        lambda: {'use_direct_api': False, 'api_key': None, 'source': 'proxy'})
    client, conf = akc.create_anthropic_client()
    assert client is None and conf['source'] == 'proxy'


def _fake_post(calls, status=200, body=None):
    def post(url, json=None, headers=None, timeout=None):
        calls.append({'url': url, 'json': json, 'headers': headers, 'timeout': timeout})
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = body or {'content': [{'type': 'text', 'text': 'OK'}]}
        resp.text = 'boom'
        return resp
    return post


def test_relay_posts_to_doc_api_with_internal_key(monkeypatch):
    import requests
    calls = []
    monkeypatch.setattr(requests, 'post', _fake_post(calls))
    monkeypatch.setattr(akc, 'internal_service_key', lambda: 'k' * 64)
    out = akc.ByokRelayClient().messages.create(
        model='claude-haiku-5-5', max_tokens=16, system='s',
        messages=[{'role': 'user', 'content': 'hi'}])
    assert out == {'content': [{'type': 'text', 'text': 'OK'}]}
    assert calls[0]['url'].endswith('/internal/anthropic/messages')
    assert calls[0]['url'].startswith('http://127.0.0.1:')
    assert calls[0]['headers'] == {'X-Internal-API-Key': 'k' * 64}
    assert calls[0]['json']['model'] == 'claude-haiku-5-5'
    assert calls[0]['timeout']                      # the configured DOC_API_REQUESTS_TIMEOUT


def test_relay_raises_on_http_error(monkeypatch):
    import requests
    monkeypatch.setattr(requests, 'post', _fake_post([], status=409))
    monkeypatch.setattr(akc, 'internal_service_key', lambda: 'k' * 64)
    with pytest.raises(RuntimeError, match='HTTP 409'):
        akc.ByokRelayClient().messages.create(model='m', max_tokens=1, messages=[])


def test_relay_refuses_stream_and_missing_key(monkeypatch):
    monkeypatch.setattr(akc, 'internal_service_key', lambda: '')
    with pytest.raises(ValueError):
        akc.ByokRelayClient().messages.create(model='m', max_tokens=1, messages=[], stream=True)
    with pytest.raises(RuntimeError, match='internal service key'):
        akc.ByokRelayClient().messages.create(model='m', max_tokens=1, messages=[])


def test_claude_quick_prompt_uses_the_relay_without_the_package(monkeypatch):
    import requests
    calls = []
    body = {'content': [{'type': 'thinking', 'thinking': ''},
                        {'type': 'text', 'text': '```json\n{"a": 1}\n```'}]}
    monkeypatch.setattr(requests, 'post', _fake_post(calls, body=body))
    monkeypatch.setattr(akc, 'internal_service_key', lambda: 'k' * 64)
    monkeypatch.setattr(akc, 'get_anthropic_config', lambda: dict(DIRECT))
    monkeypatch.setitem(sys.modules, 'anthropic', None)
    monkeypatch.delenv('BYOK_ROUTE_VIA_DOC_API', raising=False)
    sys.modules.pop('claudeQuickPrompt', None)
    cqp = importlib.import_module('claudeQuickPrompt')
    try:
        out = cqp.claudeQuickPrompt('q', system='s', temp=0.0, model='claude-haiku-4-5')
        assert out.strip() == '{"a": 1}'
        assert isinstance(cqp._CLIENT, akc.ByokRelayClient)
        assert calls[0]['json']['temperature'] == 0.0           # older model: still sent
    finally:
        sys.modules.pop('claudeQuickPrompt', None)
