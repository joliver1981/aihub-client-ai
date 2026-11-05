"""BYOK goes direct from every service (2026-11-05).

With BYOK on, the customer's own keys must reach api.openai.com /
api.anthropic.com directly from every process: never through the Cloud API
relay or proxy, and never swapped for our keys. Three pieces in
api_keys_config make that hold:

  1. one switch location that every process resolves the same way. The old
     cwd-relative './data' meant that on installs only the main app ever saw
     BYOK turned on;
  2. handing out the customer's OpenAI key sets BYPASS_PROXY=true for the
     process (the aihub2.1 openai SDK relays every request otherwise, and only
     the main app used to set it), and it stays set until restart;
  3. helpers that move the callers which only knew the proxy (Claude quick
     prompts, document search, document extraction) onto the customer's key.

Runs in aihub2.1 (no anthropic package - the real condition).

Force-add to git (gitignore hides test*.py).
"""
import base64
import json
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import api_keys_config as akc  # noqa: E402

FAKE_OPENAI = 'sk-test-customer-openai'
FAKE_ANTHROPIC = 'sk-ant-test-customer'


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(akc, '_direct_transport_latched', False)
    monkeypatch.delenv('BYPASS_PROXY', raising=False)
    monkeypatch.delenv('AIHUB_DATA_DIR', raising=False)


def _byok_on(monkeypatch, openai=True, anthropic=True):
    keys = {}
    if openai:
        keys[akc.OPENAI_API_KEY_SECRET] = FAKE_OPENAI
    if anthropic:
        keys[akc.ANTHROPIC_API_KEY_SECRET] = FAKE_ANTHROPIC
    monkeypatch.setattr(akc, 'is_byok_enabled', lambda: True)
    monkeypatch.setattr(akc, 'has_local_secret', lambda name: name in keys)
    monkeypatch.setattr(akc, 'get_local_secret', lambda name, default=None: keys.get(name, default))


def _byok_off(monkeypatch):
    monkeypatch.setattr(akc, 'is_byok_enabled', lambda: False)
    monkeypatch.setattr(akc, 'has_local_secret', lambda name: False)


def _write_switch(path, enabled):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'byok_enabled': enabled}))


# --- 1. one switch location ------------------------------------------------

def test_switch_location_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv('AIHUB_DATA_DIR', str(tmp_path / 'explicit'))
    monkeypatch.setenv('APP_ROOT', str(tmp_path / 'root'))
    assert akc._get_config_file_path() == tmp_path / 'explicit' / 'byok_config.json'
    monkeypatch.delenv('AIHUB_DATA_DIR')
    assert akc._get_config_file_path() == tmp_path / 'root' / 'data' / 'byok_config.json'


def test_switch_location_frozen_without_app_root(monkeypatch, tmp_path):
    monkeypatch.delenv('APP_ROOT', raising=False)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'wsgi_agent_api' / 'wsgi_agent_api.exe'))
    assert akc._get_config_file_path() == (tmp_path / 'data' / 'byok_config.json').resolve()


def test_switch_location_does_not_depend_on_the_working_folder(monkeypatch, tmp_path):
    root = tmp_path / 'AIHub'
    service = root / 'document_api_server'
    service.mkdir(parents=True)
    monkeypatch.setenv('APP_ROOT', str(root))
    monkeypatch.chdir(service)
    assert akc._get_config_file_path() == root / 'data' / 'byok_config.json'


def test_toggle_in_the_main_app_reaches_every_other_service(monkeypatch, tmp_path):
    root = tmp_path / 'AIHub'
    main_app, agent_api = root / 'app', root / 'wsgi_agent_api'
    main_app.mkdir(parents=True)
    agent_api.mkdir()
    monkeypatch.setenv('APP_ROOT', str(root))
    monkeypatch.setattr(akc, 'apply_byok_environment', lambda: None)
    monkeypatch.chdir(main_app)                  # NSSM starts each exe in its own folder
    assert akc.set_byok_enabled(True)
    assert json.loads((root / 'data' / 'byok_config.json').read_text()) == {'byok_enabled': True}
    monkeypatch.chdir(agent_api)
    assert akc.is_byok_enabled() is True


def test_anchored_switch_wins_over_the_legacy_one(monkeypatch, tmp_path):
    monkeypatch.setenv('APP_ROOT', str(tmp_path / 'root'))
    monkeypatch.chdir(tmp_path)
    _write_switch(tmp_path / 'root' / 'data' / 'byok_config.json', False)
    _write_switch(tmp_path / 'data' / 'byok_config.json', True)          # old cwd-relative file
    assert akc.is_byok_enabled() is False


def test_legacy_switch_is_still_read_and_never_rewritten(monkeypatch, tmp_path):
    root = tmp_path / 'root'
    monkeypatch.setenv('APP_ROOT', str(root))
    monkeypatch.chdir(tmp_path)
    _write_switch(tmp_path / 'data' / 'byok_config.json', True)
    assert akc.is_byok_enabled() is True
    assert not (root / 'data' / 'byok_config.json').exists()


def test_installed_main_apps_old_switch_is_seen_by_other_services(monkeypatch, tmp_path):
    root = tmp_path / 'AIHub'
    _write_switch(root / 'app' / 'data' / 'byok_config.json', True)     # pre-fix main app, cwd {app}\app
    service = root / 'wsgi_executor_service'
    service.mkdir(parents=True)
    monkeypatch.setenv('APP_ROOT', str(root))
    monkeypatch.chdir(service)
    assert akc.is_byok_enabled() is True


def test_no_switch_anywhere_means_off(monkeypatch, tmp_path):
    monkeypatch.setenv('APP_ROOT', str(tmp_path / 'root'))
    monkeypatch.chdir(tmp_path)
    assert akc.is_byok_enabled() is False


# --- 2. the customer's OpenAI key never rides the relay ---------------------

def test_handing_out_the_customer_openai_key_goes_direct(monkeypatch):
    monkeypatch.setenv('BYPASS_PROXY', 'false')        # what .env sets for every service
    _byok_on(monkeypatch)
    conf = akc.get_openai_config(use_alternate_api=True)
    assert conf['source'] == 'byok' and conf['api_key'] == FAKE_OPENAI
    assert os.environ['BYPASS_PROXY'] == 'true'


def test_get_active_openai_key_goes_direct(monkeypatch):
    monkeypatch.setenv('BYPASS_PROXY', 'false')
    _byok_on(monkeypatch)
    assert akc.get_active_openai_key() == FAKE_OPENAI
    assert os.environ['BYPASS_PROXY'] == 'true'


def test_other_services_go_direct_from_startup(monkeypatch):
    monkeypatch.setenv('BYPASS_PROXY', 'false')
    _byok_on(monkeypatch)
    akc.init_byok_transport()
    assert os.environ['BYPASS_PROXY'] == 'true'


def test_byok_off_leaves_the_transport_alone(monkeypatch):
    monkeypatch.setenv('BYPASS_PROXY', 'false')
    _byok_off(monkeypatch)
    akc.init_byok_transport()
    assert akc.get_active_openai_key() is None
    assert os.environ['BYPASS_PROXY'] == 'false'


def test_anthropic_only_byok_does_not_touch_the_openai_transport(monkeypatch):
    monkeypatch.setenv('BYPASS_PROXY', 'false')
    _byok_on(monkeypatch, openai=False)
    akc.init_byok_transport()
    assert os.environ['BYPASS_PROXY'] == 'false'


def test_turning_byok_off_keeps_the_process_direct_until_restart(monkeypatch):
    _byok_on(monkeypatch)
    akc.apply_byok_environment()
    assert os.environ['BYPASS_PROXY'] == 'true'
    _byok_off(monkeypatch)
    akc.apply_byok_environment()
    assert os.environ['BYPASS_PROXY'] == 'true'


def test_main_app_starting_with_byok_off_still_relays(monkeypatch):
    monkeypatch.setenv('BYPASS_PROXY', 'true')
    _byok_off(monkeypatch)
    akc.apply_byok_environment()
    assert os.environ['BYPASS_PROXY'] == 'false'       # init_byok() behaves as before


# --- 3. proxy-only Claude callers move to the customer's key ----------------

def test_byok_off_keeps_the_proxy_path(monkeypatch):
    _byok_off(monkeypatch)
    assert akc.byok_anthropic_messages(model='m', max_tokens=5, messages=[]) is None
    assert akc.byok_document_client() is None


def test_system_key_direct_mode_is_not_byok(monkeypatch):
    monkeypatch.setattr(akc, 'get_anthropic_config',
                        lambda: {'use_direct_api': True, 'api_key': 'ours', 'source': 'system_direct'})
    assert akc.byok_anthropic_messages(model='m', max_tokens=5, messages=[]) is None
    assert akc.byok_document_client() is None


def test_byok_claude_call_without_the_package_goes_to_the_documents_api(monkeypatch):
    _byok_on(monkeypatch)
    monkeypatch.setitem(sys.modules, 'anthropic', None)          # aihub2.1: no package
    monkeypatch.delenv('BYOK_ROUTE_VIA_DOC_API', raising=False)
    monkeypatch.setattr(akc, 'internal_service_key', lambda: 'k' * 64)
    sent = {}

    class _Response:
        status_code = 200
        text = ''

        def json(self):
            return {'content': [{'type': 'text', 'text': 'ok'}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.update(url=url, body=json)
        return _Response()

    import requests
    monkeypatch.setattr(requests, 'post', fake_post)
    out = akc.byok_anthropic_messages(model='claude-x', max_tokens=7, system='sys', temperature=0.0,
                                      messages=[{'role': 'user', 'content': 'hi'}])
    assert out == {'content': [{'type': 'text', 'text': 'ok'}]}
    assert sent['url'].endswith('/internal/anthropic/messages')
    assert 'azurewebsites' not in sent['url']
    assert sent['body'] == {'model': 'claude-x', 'max_tokens': 7, 'system': 'sys',
                            'temperature': 0.0, 'messages': [{'role': 'user', 'content': 'hi'}]}


def test_byok_claude_call_with_the_package_uses_the_direct_client(monkeypatch):
    _byok_on(monkeypatch)
    client = object()
    monkeypatch.setattr(akc, 'create_anthropic_client', lambda: (client, {'source': 'byok'}))
    seen = {}

    class _Streamed:
        def model_dump(self):
            return {'content': [{'type': 'text', 'text': 'direct'}]}

    def fake_create(client, **kwargs):
        seen.update(client=client, **kwargs)
        return _Streamed()

    helper = types.ModuleType('anthropic_streaming_helper')
    helper.anthropic_messages_create = fake_create
    monkeypatch.setitem(sys.modules, 'anthropic_streaming_helper', helper)
    out = akc.byok_anthropic_messages(model='claude-x', max_tokens=9, messages=[])
    assert out == {'content': [{'type': 'text', 'text': 'direct'}]}
    assert seen['client'] is client and seen['model'] == 'claude-x' and seen['max_tokens'] == 9


@pytest.mark.parametrize('name,data,kind,media_type', [
    ('invoice.pdf', b'anything', 'document', 'application/pdf'),
    ('', b'%PDF-1.4 body', 'document', 'application/pdf'),
    ('scan.JPG', b'x', 'image', 'image/jpeg'),
    ('', b'\xff\xd8\xff\xe0 body', 'image', 'image/jpeg'),
    ('photo.png', b'x', 'image', 'image/png'),
    ('', b'\x89PNG\r\n\x1a\n body', 'image', 'image/png'),
    ('old.doc', b'\xd0\xcf\x11\xe0 body', 'document', 'application/octet-stream'),
])
def test_document_block_matches_the_cloud_document_route(name, data, kind, media_type):
    block = akc.document_content_block(name, data)
    assert block['type'] == kind
    assert block['source']['type'] == 'base64' and block['source']['media_type'] == media_type
    assert base64.b64decode(block['source']['data']) == data


def test_document_client_sends_the_file_straight_to_anthropic(monkeypatch, tmp_path):
    import config as cfg
    pdf = tmp_path / 'invoice.pdf'
    pdf.write_bytes(b'%PDF-1.4 test')
    seen = {}

    def fake(**kwargs):
        seen.update(kwargs)
        return {'content': [{'type': 'text', 'text': '{}'}]}

    monkeypatch.setattr(akc, 'byok_anthropic_messages', fake)
    out = akc.ByokDocumentClient().messages_with_document_stream(
        file_path=str(pdf), user_text='extract', model='', max_tokens=None,
        system='sys', temperature=0.0)
    assert out == {'content': [{'type': 'text', 'text': '{}'}]}
    content = seen['messages'][0]['content']
    assert content[0]['type'] == 'document'
    assert content[0]['source']['media_type'] == 'application/pdf'
    assert content[1] == {'type': 'text', 'text': 'extract'}
    assert seen['model'] == cfg.ANTHROPIC_MODEL       # the cloud filled an empty model in; now we do
    assert seen['system'] == 'sys'


def test_document_client_reports_failures_like_the_proxy(monkeypatch, tmp_path):
    def boom(**kwargs):
        raise RuntimeError('Anthropic said no')

    monkeypatch.setattr(akc, 'byok_anthropic_messages', boom)
    pdf = tmp_path / 'x.pdf'
    pdf.write_bytes(b'%PDF-')
    out = akc.ByokDocumentClient().messages_with_document(file_path=str(pdf))
    assert 'Anthropic said no' in out['error']
