"""
Unit tests for the Microsoft 365 (Graph API, OAuth2) provider in email_settings.py.

Covers the resolution rules (.env fallback carries the GRAPH_MAIL_* keys; the
UI JSON wins and reads the client secret from the local store; an absent
fallback_smtp key means ON; the SMTP block is the fallback relay and inherits
the Microsoft 365 sender as From), the /save validation for provider 'graph',
what /status reports (flags, never secrets) and the /test route's chain:
Microsoft 365 first, the SMTP relay when that fallback is on, both outcomes
reported.

Fixtures come from test_email_settings.py (same temp data dir, same admin
client, same "patch config not app_config" discipline).
"""

import json
from unittest.mock import MagicMock

import pytest

import email_settings
from test_email_settings import (  # noqa: F401  (pytest fixtures, resolved by name)
    ENV_VALUES,
    _config_module,
    admin_client,
    env_config,
    fresh_email_settings,
    no_local_secrets,
)

pytestmark = pytest.mark.unit

GRAPH_ENV = {
    'EMAIL_PROVIDER': 'graph',
    'GRAPH_MAIL_TENANT_ID': 'tenant-env',
    'GRAPH_MAIL_CLIENT_ID': 'client-env',
    'GRAPH_MAIL_CLIENT_SECRET': 'secret-env',
    'GRAPH_MAIL_SENDER': 'alerts@env.test',
    'EMAIL_GRAPH_FALLBACK_SMTP': False,
    'SMTP_FROM': '',
}


def _write_graph_ui(path, **graph_over):
    graph = {'tenant_id': 'tenant-ui', 'client_id': 'client-ui', 'sender': 'alerts@ui.test'}
    graph.update(graph_over)
    (path / 'email_settings.json').write_text(json.dumps({
        'provider': 'graph',
        'smtp': {'host': 'relay.ui.test', 'port': 25, 'user': '', 'from': '', 'use_tls': False},
        'azure': {'sender': ''},
        'graph': graph,
    }), encoding='utf-8')


@pytest.fixture
def graph_secret_in_store(monkeypatch):
    monkeypatch.setattr(email_settings, 'has_local_secret',
                        lambda name, *a, **k: name == email_settings.GRAPH_CLIENT_SECRET_SECRET)
    monkeypatch.setattr(email_settings, 'get_local_secret', lambda name, *a, **k: 'ui-graph-secret')


# ---------------------------------------------------------------- resolution

def test_env_fallback_carries_graph_keys(env_config, monkeypatch):
    for key, value in GRAPH_ENV.items():
        monkeypatch.setattr(_config_module(), key, value, raising=False)

    conf = email_settings.get_email_config()

    assert conf['source'] == 'env'
    assert conf['provider'] == 'graph'
    assert conf['graph_tenant_id'] == 'tenant-env'
    assert conf['graph_client_id'] == 'client-env'
    assert conf['graph_client_secret'] == 'secret-env'
    assert conf['graph_sender'] == 'alerts@env.test'
    assert conf['graph_fallback_smtp'] is False
    # blank SMTP_FROM in graph mode: the relay fallback sends as the same mailbox
    assert conf['smtp_from'] == 'alerts@env.test'


def test_env_fallback_smtp_from_is_untouched_outside_graph_mode(env_config):
    conf = email_settings.get_email_config()
    assert conf['provider'] == 'smtp'
    assert conf['smtp_from'] == ENV_VALUES['SMTP_FROM']
    assert conf['graph_fallback_smtp'] is True     # the default, from the .env stand-in


def test_ui_graph_config_resolves_with_secret_and_default_fallback(env_config, graph_secret_in_store):
    _write_graph_ui(env_config)   # no fallback_smtp key at all -> default ON

    conf = email_settings.get_email_config()

    assert conf['source'] == 'ui'
    assert conf['provider'] == 'graph'
    assert conf['graph_tenant_id'] == 'tenant-ui'
    assert conf['graph_client_id'] == 'client-ui'
    assert conf['graph_client_secret'] == 'ui-graph-secret'
    assert conf['graph_sender'] == 'alerts@ui.test'
    assert conf['graph_fallback_smtp'] is True
    assert conf['smtp_host'] == 'relay.ui.test'
    assert conf['smtp_from'] == 'alerts@ui.test'   # the relay sends as the M365 mailbox
    assert conf['smtp_password'] == ''             # only the graph secret is in the store


def test_ui_graph_fallback_can_be_switched_off(env_config, no_local_secrets):
    _write_graph_ui(env_config, fallback_smtp=False)
    assert email_settings.get_email_config()['graph_fallback_smtp'] is False


def test_ui_smtp_from_wins_over_the_graph_sender_when_given(env_config, no_local_secrets):
    _write_graph_ui(env_config)
    data = json.loads((env_config / 'email_settings.json').read_text(encoding='utf-8'))
    data['smtp']['from'] = 'relay-alerts@ui.test'
    (env_config / 'email_settings.json').write_text(json.dumps(data), encoding='utf-8')
    assert email_settings.get_email_config()['smtp_from'] == 'relay-alerts@ui.test'


# ---------------------------------------------------------------- /save

def test_save_graph_requires_tenant_client_sender_and_secret(env_config, no_local_secrets, admin_client):
    base = {'provider': 'graph', 'smtp': {}, 'azure': {},
            'graph': {'tenant_id': 't', 'client_id': 'c', 'sender': 'a@x.test', 'client_secret': 's'}}
    for missing in ('tenant_id', 'client_id', 'sender'):
        payload = json.loads(json.dumps(base))
        payload['graph'][missing] = ''
        resp = admin_client.post('/api/email-settings/save', json=payload)
        assert resp.status_code == 400, missing
        assert 'Microsoft 365' in resp.get_json()['error']

    payload = json.loads(json.dumps(base))
    payload['graph']['client_secret'] = ''
    resp = admin_client.post('/api/email-settings/save', json=payload)
    assert resp.status_code == 400
    assert 'client secret' in resp.get_json()['error']

    payload = json.loads(json.dumps(base))
    payload['graph']['sender'] = 'not-an-address'
    assert admin_client.post('/api/email-settings/save', json=payload).status_code == 400

    assert not (env_config / 'email_settings.json').exists()


def test_save_graph_writes_json_and_keeps_the_secret_in_the_store_only(env_config, no_local_secrets,
                                                                        admin_client, monkeypatch):
    stored = {}
    monkeypatch.setattr(email_settings, 'set_local_secret',
                        lambda name, value, **k: stored.__setitem__(name, value))

    resp = admin_client.post('/api/email-settings/save', json={
        'provider': 'graph',
        'smtp': {'host': 'relay.ui.test', 'port': '25', 'user': '', 'from': '', 'use_tls': False, 'password': ''},
        'azure': {'sender': '', 'connection_string': ''},
        'graph': {'tenant_id': ' tenant-ui ', 'client_id': 'client-ui', 'sender': 'alerts@ui.test',
                  'client_secret': 'graph-secret-9f3', 'fallback_smtp': False},
    })

    assert resp.status_code == 200, resp.get_json()
    saved = json.loads((env_config / 'email_settings.json').read_text(encoding='utf-8'))
    assert saved['provider'] == 'graph'
    assert saved['graph'] == {'tenant_id': 'tenant-ui', 'client_id': 'client-ui',
                              'sender': 'alerts@ui.test', 'fallback_smtp': False}
    assert saved['smtp']['host'] == 'relay.ui.test'
    assert 'graph-secret-9f3' not in json.dumps(saved)    # never in the JSON
    assert 'client_secret' not in json.dumps(saved)
    assert stored == {email_settings.GRAPH_CLIENT_SECRET_SECRET: 'graph-secret-9f3'}


def test_save_graph_keeps_a_stored_secret_when_the_field_comes_back_blank(env_config, graph_secret_in_store,
                                                                         admin_client, monkeypatch):
    writes = []
    monkeypatch.setattr(email_settings, 'set_local_secret', lambda *a, **k: writes.append(a))
    resp = admin_client.post('/api/email-settings/save', json={
        'provider': 'graph', 'smtp': {}, 'azure': {},
        'graph': {'tenant_id': 't', 'client_id': 'c', 'sender': 'a@x.test', 'client_secret': ''}})
    assert resp.status_code == 200, resp.get_json()
    assert writes == []


def test_save_graph_accepts_a_blank_smtp_block(env_config, no_local_secrets, admin_client, monkeypatch):
    monkeypatch.setattr(email_settings, 'set_local_secret', lambda *a, **k: None)
    resp = admin_client.post('/api/email-settings/save', json={
        'provider': 'graph', 'smtp': {'host': '', 'port': ''}, 'azure': {},
        'graph': {'tenant_id': 't', 'client_id': 'c', 'sender': 'a@x.test', 'client_secret': 's'}})
    assert resp.status_code == 200, resp.get_json()


def test_save_graph_still_validates_a_filled_in_relay_port(env_config, no_local_secrets, admin_client, monkeypatch):
    monkeypatch.setattr(email_settings, 'set_local_secret', lambda *a, **k: None)
    resp = admin_client.post('/api/email-settings/save', json={
        'provider': 'graph', 'smtp': {'host': 'relay.ui.test', 'port': '99999'}, 'azure': {},
        'graph': {'tenant_id': 't', 'client_id': 'c', 'sender': 'a@x.test', 'client_secret': 's'}})
    assert resp.status_code == 400
    assert 'port' in resp.get_json()['error'].lower()


# ---------------------------------------------------------------- /status

def test_status_reports_graph_fields_and_secret_flags_only(env_config, graph_secret_in_store,
                                                            admin_client, monkeypatch):
    _write_graph_ui(env_config)
    for key, value in GRAPH_ENV.items():
        monkeypatch.setattr(_config_module(), key, value, raising=False)

    body = admin_client.get('/api/email-settings/status').get_json()

    assert body['provider'] == 'graph'
    assert body['ui']['graph'] == {'tenant_id': 'tenant-ui', 'client_id': 'client-ui',
                                   'sender': 'alerts@ui.test', 'fallback_smtp': True}
    assert body['ui']['graph_client_secret_set'] is True
    assert body['env_defaults']['graph_sender'] == 'alerts@env.test'
    assert body['env_defaults']['graph_client_id'] == 'client-env'
    assert body['env_defaults']['graph_client_secret_set'] is True
    assert body['env_defaults']['graph_fallback_smtp'] is False
    assert 'ui-graph-secret' not in json.dumps(body)
    assert 'secret-env' not in json.dumps(body)


# ---------------------------------------------------------------- /test (the chain)

@pytest.fixture
def graph_ui(env_config, graph_secret_in_store):
    _write_graph_ui(env_config)
    return env_config


def test_test_send_graph_success(graph_ui, admin_client, monkeypatch):
    import email_graph
    sent = {}
    monkeypatch.setattr(email_graph, 'send_mail',
                        lambda conf, to, subject, body, *a, **k: sent.update(to=to, sender=conf['graph_sender']))
    smtp = MagicMock()
    monkeypatch.setattr(email_settings, '_test_smtp', smtp)

    resp = admin_client.post('/api/email-settings/test', json={'to': 'me@x.test'})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()['success'] is True
    assert 'via graph' in resp.get_json()['message']
    assert sent == {'to': 'me@x.test', 'sender': 'alerts@ui.test'}
    smtp.assert_not_called()


def test_test_send_graph_failure_uses_the_smtp_fallback_and_reports_both(graph_ui, admin_client, monkeypatch):
    import email_graph

    def boom(*a, **k):
        raise email_graph.GraphMailError(
            'Microsoft 365 token request failed (HTTP 400): invalid_client: AADSTS7000215: Invalid client secret')
    monkeypatch.setattr(email_graph, 'send_mail', boom)
    smtp = MagicMock()
    monkeypatch.setattr(email_settings, '_test_smtp', smtp)

    resp = admin_client.post('/api/email-settings/test', json={'to': 'me@x.test'})

    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body['success'] is True
    assert body['fallback_used'] is True
    assert 'AADSTS7000215' in body['message']
    assert 'relay.ui.test:25' in body['message']
    smtp.assert_called_once()
    assert smtp.call_args[0][1] == 'me@x.test'


def test_test_send_graph_failure_with_fallback_off_is_a_502_with_the_real_error(graph_ui, admin_client, monkeypatch):
    _write_graph_ui(graph_ui, fallback_smtp=False)
    import email_graph

    def boom(*a, **k):
        raise email_graph.GraphMailError(
            'Microsoft 365 sendMail failed (HTTP 403): ErrorAccessDenied: Access is denied.')
    monkeypatch.setattr(email_graph, 'send_mail', boom)
    smtp = MagicMock()
    monkeypatch.setattr(email_settings, '_test_smtp', smtp)

    resp = admin_client.post('/api/email-settings/test', json={'to': 'me@x.test'})

    assert resp.status_code == 502
    assert 'ErrorAccessDenied' in resp.get_json()['error']
    smtp.assert_not_called()


def test_test_send_graph_failure_without_a_relay_host_is_a_502(graph_ui, admin_client, monkeypatch):
    data = json.loads((graph_ui / 'email_settings.json').read_text(encoding='utf-8'))
    data['smtp']['host'] = ''
    (graph_ui / 'email_settings.json').write_text(json.dumps(data), encoding='utf-8')
    import email_graph

    def boom(*a, **k):
        raise email_graph.GraphMailError('Microsoft 365 token request failed (HTTP 400): AADSTS90002: Tenant not found')
    monkeypatch.setattr(email_graph, 'send_mail', boom)
    smtp = MagicMock()
    monkeypatch.setattr(email_settings, '_test_smtp', smtp)

    resp = admin_client.post('/api/email-settings/test', json={'to': 'me@x.test'})

    assert resp.status_code == 502
    assert 'AADSTS90002' in resp.get_json()['error']
    smtp.assert_not_called()
