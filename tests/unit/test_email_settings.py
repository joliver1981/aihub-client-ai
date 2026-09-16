"""
Unit tests for email_settings.py — the .env fallback of get_email_config().

Regression guard for a v2.0 defect (commit c9eb5e0, 2026-08-25): the module
did ``import app_config as cfg`` — a 12-line constants module (APP_VERSION,
APP_NAME, ...) that has NO EMAIL_PROVIDER / SMTP_* / API_AZURE_EMAIL_*
attributes — instead of ``import config as cfg`` (which loads .env and defines
every one of those keys). With no data/email_settings.json the ``source: 'env'``
branch therefore returned provider 'azure' and all-blank SMTP/Azure fields no
matter what .env said. AppUtils.send_email_smtp and EmailUtils.send_email_smtp
resolve host/port/user/password from get_email_config() at call time, so an
install that configured SMTP only via .env got smtplib.SMTP('') -> failure ->
silent fallback to the vendor cloud relay.

These tests patch attributes on the *config* module (never app_config) and
point AIHUB_DATA_DIR at a temp dir, so neither the real data/email_settings.json
nor the local secrets store is touched.
"""

import importlib
import json
import types

import pytest

import app_config
import email_settings

pytestmark = pytest.mark.unit


# Values that stand in for a .env — deliberately unlike config.py's own
# defaults (smtp.office365.com / 587 / provider azure) so a default leaking
# through is distinguishable from the patched value.
ENV_VALUES = {
    'EMAIL_PROVIDER': 'smtp',
    'SMTP_HOST': 'smtp.example.test',
    'SMTP_PORT': 2525,
    'SMTP_USER': 'svc@example.test',
    'SMTP_PASSWORD': 'env-secret',
    'SMTP_USE_TLS': True,
    'SMTP_FROM': 'svc@example.test',
    'API_AZURE_EMAIL_CONN_STR': 'endpoint=https://env.example.test/;accesskey=env-key',
    'API_AZURE_EMAIL_SENDER': 'noreply@env.example.test',
}


@pytest.fixture
def fresh_email_settings():
    """Re-bind ``email_settings.cfg`` to the ``config`` module that is current
    NOW. Several sibling test modules swap a MagicMock into sys.modules['config']
    at collection time (test_app_utils.py, test_common_utils.py, ...), so in a
    full-suite run the binding made when this file was collected can be stale.
    With the buggy ``import app_config`` a reload still binds app_config, so
    the assertions below keep their teeth."""
    importlib.reload(email_settings)
    return email_settings


@pytest.fixture
def env_config(fresh_email_settings, monkeypatch, tmp_path):
    """Patch the email keys on the ``config`` module (NOT app_config) and
    redirect the data dir to an empty temp dir, i.e. no UI config file."""
    monkeypatch.setenv('AIHUB_DATA_DIR', str(tmp_path))
    for key, value in ENV_VALUES.items():
        monkeypatch.setattr(_config_module(), key, value, raising=False)
    return tmp_path


def _config_module():
    """The ``config`` module as it is in sys.modules right now — the real one,
    or the MagicMock a sibling test module swapped in. import_module (rather
    than a module-level ``import config``) also loads it when nothing else has,
    which is exactly the situation the buggy ``import app_config`` produced."""
    return importlib.import_module('config')


@pytest.fixture
def no_local_secrets(fresh_email_settings, monkeypatch):
    """Keep the tests away from the machine-bound secrets store."""
    monkeypatch.setattr(fresh_email_settings, 'has_local_secret', lambda name, *a, **k: False)
    monkeypatch.setattr(fresh_email_settings, 'get_local_secret', lambda name, *a, **k: '')


# ---------------------------------------------------------------- .env fallback

def test_module_binds_cfg_to_config_module_not_app_config(fresh_email_settings):
    """The regression itself: cfg must be the config module that loads .env."""
    assert fresh_email_settings.cfg is not app_config, (
        "email_settings must `import config as cfg`; app_config.py carries no "
        "EMAIL_PROVIDER / SMTP_* / API_AZURE_EMAIL_* keys, so every .env "
        "fallback value would silently resolve to ''"
    )
    assert fresh_email_settings.cfg is _config_module()


def test_env_fallback_returns_dotenv_values_when_no_ui_file(env_config):
    assert not (env_config / 'email_settings.json').exists()

    conf = email_settings.get_email_config()

    assert conf['source'] == 'env'
    assert conf['provider'] == 'smtp'
    assert conf['smtp_host'] == 'smtp.example.test'
    assert conf['smtp_port'] == 2525
    assert conf['smtp_user'] == 'svc@example.test'
    assert conf['smtp_password'] == 'env-secret'
    assert conf['smtp_use_tls'] is True
    assert conf['smtp_from'] == 'svc@example.test'
    assert conf['azure_conn_str'] == ENV_VALUES['API_AZURE_EMAIL_CONN_STR']
    assert conf['azure_sender'] == ENV_VALUES['API_AZURE_EMAIL_SENDER']


def test_env_fallback_azure_provider_is_case_normalised(env_config, monkeypatch):
    monkeypatch.setattr(_config_module(), 'EMAIL_PROVIDER', 'Azure', raising=False)

    conf = email_settings.get_email_config()

    assert conf['source'] == 'env'
    assert conf['provider'] == 'azure'
    assert conf['azure_conn_str'] == ENV_VALUES['API_AZURE_EMAIL_CONN_STR']
    assert conf['azure_sender'] == ENV_VALUES['API_AZURE_EMAIL_SENDER']


# ---------------------------------------------------------------- UI file wins

def test_ui_file_wins_over_env_all_or_nothing(env_config, monkeypatch):
    """With a provider set in the UI file the UI values are authoritative:
    a blank SMTP user there MEANS anonymous relay, it does not fall back to
    the .env user; secrets come from the local store, never from .env."""
    (env_config / 'email_settings.json').write_text(json.dumps({
        'provider': 'smtp',
        'smtp': {'host': 'relay.ui.test', 'port': 25, 'user': '',
                 'from': 'alerts@ui.test', 'use_tls': False},
        'azure': {'sender': ''},
    }), encoding='utf-8')
    monkeypatch.setattr(email_settings, 'has_local_secret',
                        lambda name, *a, **k: name == email_settings.SMTP_PASSWORD_SECRET)
    monkeypatch.setattr(email_settings, 'get_local_secret',
                        lambda name, *a, **k: 'ui-store-secret')

    conf = email_settings.get_email_config()

    assert conf['source'] == 'ui'
    assert conf['provider'] == 'smtp'
    assert conf['smtp_host'] == 'relay.ui.test'
    assert conf['smtp_port'] == 25
    assert conf['smtp_from'] == 'alerts@ui.test'
    assert conf['smtp_use_tls'] is False
    assert conf['smtp_password'] == 'ui-store-secret'
    # all-or-nothing: no per-field fallback to the .env user / Azure values
    assert conf['smtp_user'] == ''
    assert conf['azure_conn_str'] == ''
    assert conf['azure_sender'] == ''


# ---------------------------------------------------------------- /status route

@pytest.fixture
def admin_client(fresh_email_settings, monkeypatch):
    """Test client for the blueprint with an admin (role 3) current_user."""
    from flask import Flask
    import flask_login

    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(fresh_email_settings.email_settings_bp)
    # require_admin does `from flask_login import current_user` at call time
    monkeypatch.setattr(flask_login, 'current_user',
                        types.SimpleNamespace(is_authenticated=True, role=3))
    return app.test_client()


def test_status_env_defaults_reflect_dotenv_values(env_config, no_local_secrets, admin_client):
    resp = admin_client.get('/api/email-settings/status')

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['success'] is True
    assert body['source'] == 'env'
    assert body['provider'] == 'smtp'

    env_defaults = body['env_defaults']
    assert env_defaults['provider'] == 'smtp'
    assert env_defaults['smtp_host'] == 'smtp.example.test'
    assert env_defaults['smtp_port'] == 2525
    assert env_defaults['smtp_user'] == 'svc@example.test'
    assert env_defaults['smtp_from'] == 'svc@example.test'
    assert env_defaults['smtp_use_tls'] is True
    assert env_defaults['azure_sender'] == 'noreply@env.example.test'
    assert env_defaults['smtp_password_set'] is True
    assert env_defaults['azure_conn_str_set'] is True
    # secrets are reported as set / not-set only, never echoed
    assert 'smtp_password' not in env_defaults
    assert 'azure_conn_str' not in env_defaults
