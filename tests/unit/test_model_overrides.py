"""
Unit tests for model_overrides.py — the admin-UI model override layer.

Pins the two contracts that let gpt-5.2 traffic hide behind a gpt-5.4 display
(diagnosed 2026-08-05):

1. APPLY: an override set in the admin UI must reach the env vars of EVERY
   path that can serve the request — OpenAI-direct AND the Azure deployment
   (primary + alternate) — so whatever branch get_openai_config() picks, the
   admin-set model is the one used.

2. DISPLAY: the "current value" reported to the admin UI must come from the
   branch that actually serves platform traffic (Azure unless USE_OPENAI_API),
   never from the other branch's always-populated default.
"""

import json
import sys
import types

import pytest

import model_overrides


# =============================================================================
# Helpers
# =============================================================================

def _stub_config(**attrs):
    """A minimal stand-in for the config module."""
    mod = types.ModuleType('config')
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture
def overrides_file(tmp_path, monkeypatch):
    """Point OVERRIDES_PATH at a temp file and return its Path."""
    path = tmp_path / 'model_overrides.json'
    monkeypatch.setattr(model_overrides, 'OVERRIDES_PATH', path)
    return path


# =============================================================================
# 1. APPLY — set it means use it
# =============================================================================

class TestApplyMapping:
    def test_openai_primary_maps_to_direct_and_both_azure_deployments(self):
        env_vars = model_overrides.KEY_TO_ENV_VARS['openai_primary']
        assert 'OPENAI_MODEL' in env_vars
        assert 'AZURE_OPENAI_DEPLOYMENT_NAME' in env_vars
        assert 'AZURE_OPENAI_DEPLOYMENT_NAME_ALTERNATE' in env_vars

    def test_openai_mini_maps_to_direct_and_both_azure_deployments(self):
        env_vars = model_overrides.KEY_TO_ENV_VARS['openai_mini']
        assert 'OPENAI_MODEL_MINI' in env_vars
        assert 'AZURE_OPENAI_DEPLOYMENT_NAME_MINI' in env_vars
        assert 'AZURE_OPENAI_DEPLOYMENT_NAME_ALTERNATE_MINI' in env_vars

    def test_apply_sets_every_mapped_env_var(self, overrides_file, monkeypatch):
        for var in model_overrides.KEY_TO_ENV_VARS['openai_primary']:
            monkeypatch.delenv(var, raising=False)
        overrides_file.write_text(json.dumps({'openai_primary': 'gpt-test-x'}))

        applied = model_overrides.apply_overrides_to_env()

        for var in model_overrides.KEY_TO_ENV_VARS['openai_primary']:
            assert applied[var] == 'gpt-test-x'
            import os
            assert os.environ[var] == 'gpt-test-x'

    def test_apply_wins_over_preexisting_env(self, overrides_file, monkeypatch):
        monkeypatch.setenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'from-machine-env')
        overrides_file.write_text(json.dumps({'openai_primary': 'gpt-test-y'}))

        model_overrides.apply_overrides_to_env()

        import os
        assert os.environ['AZURE_OPENAI_DEPLOYMENT_NAME'] == 'gpt-test-y'

    def test_blank_override_leaves_env_alone(self, overrides_file, monkeypatch):
        monkeypatch.setenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'from-machine-env')
        overrides_file.write_text(json.dumps({'openai_primary': ''}))

        applied = model_overrides.apply_overrides_to_env()

        assert applied == {}
        import os
        assert os.environ['AZURE_OPENAI_DEPLOYMENT_NAME'] == 'from-machine-env'


class TestResolutionUsesOverriddenDeployment:
    """The override must survive all the way through get_openai_config()."""

    def _resolve(self, monkeypatch, use_alternate_api):
        import api_keys_config
        cfg = _stub_config(
            USE_OPENAI_API=False,
            OPENAI_DEPLOYMENT_NAME='gpt-direct-unused',
            OPENAI_DEPLOYMENT_NAME_MINI='gpt-direct-mini-unused',
            AZURE_OPENAI_DEPLOYMENT_NAME='admin-set-model',
            AZURE_OPENAI_DEPLOYMENT_NAME_ALTERNATE='admin-set-model',
            AZURE_OPENAI_DEPLOYMENT_NAME_MINI='admin-set-mini',
            AZURE_OPENAI_DEPLOYMENT_NAME_ALTERNATE_MINI='admin-set-mini',
            AZURE_OPENAI_API_KEY='k', AZURE_OPENAI_API_KEY_ALTERNATE='k',
            AZURE_OPENAI_BASE_URL='https://x', AZURE_OPENAI_BASE_URL_ALTERNATE='https://x',
            AZURE_OPENAI_API_VERSION='2024-12-01-preview',
        )
        monkeypatch.setitem(sys.modules, 'config', cfg)
        monkeypatch.setattr(api_keys_config, 'is_byok_enabled', lambda: False)
        return api_keys_config.get_openai_config(use_alternate_api=use_alternate_api)

    def test_primary_azure_path_uses_set_deployment(self, monkeypatch):
        config = self._resolve(monkeypatch, use_alternate_api=False)
        assert config['api_type'] == 'azure'
        assert config['deployment_id'] == 'admin-set-model'

    def test_alternate_azure_path_uses_set_deployment(self, monkeypatch):
        # GeneralAgent and the data engines all pass use_alternate_api=True;
        # the admin-set model must govern that path too.
        config = self._resolve(monkeypatch, use_alternate_api=True)
        assert config['api_type'] == 'azure'
        assert config['deployment_id'] == 'admin-set-model'


# =============================================================================
# 2. DISPLAY — show the model the platform is actually on
# =============================================================================

class TestPathAwareDisplay:
    AZURE_CFG = dict(
        USE_OPENAI_API=False,
        OPENAI_DEPLOYMENT_NAME='gpt-direct',
        OPENAI_DEPLOYMENT_NAME_MINI='gpt-direct-mini',
        AZURE_OPENAI_DEPLOYMENT_NAME='gpt-azure',
        AZURE_OPENAI_DEPLOYMENT_NAME_MINI='gpt-azure-mini',
        AZURE_OPENAI_DEPLOYMENT_NAME_EMBEDDING='embed',
        OPENAI_VISION_MODEL='vision',
        ANTHROPIC_ADVANCED='claude-a', ANTHROPIC_MODEL='claude-m', ANTHROPIC_MINI='claude-mini',
    )

    def test_azure_path_shows_azure_deployment(self, monkeypatch):
        monkeypatch.setitem(sys.modules, 'config', _stub_config(**self.AZURE_CFG))
        defaults = model_overrides._read_config_defaults()
        assert defaults['openai_primary'] == 'gpt-azure'
        assert defaults['openai_mini'] == 'gpt-azure-mini'

    def test_direct_path_shows_direct_model(self, monkeypatch):
        cfg = dict(self.AZURE_CFG, USE_OPENAI_API=True)
        monkeypatch.setitem(sys.modules, 'config', _stub_config(**cfg))
        defaults = model_overrides._read_config_defaults()
        assert defaults['openai_primary'] == 'gpt-direct'
        assert defaults['openai_mini'] == 'gpt-direct-mini'

    def test_representative_env_var_prefers_azure_on_azure_path(self, monkeypatch):
        monkeypatch.setitem(sys.modules, 'config', _stub_config(USE_OPENAI_API=False))
        var = model_overrides._representative_env_var(
            'openai_primary', model_overrides.KEY_TO_ENV_VARS['openai_primary'])
        assert var == 'AZURE_OPENAI_DEPLOYMENT_NAME'

    def test_representative_env_var_prefers_direct_on_direct_path(self, monkeypatch):
        monkeypatch.setitem(sys.modules, 'config', _stub_config(USE_OPENAI_API=True))
        var = model_overrides._representative_env_var(
            'openai_primary', model_overrides.KEY_TO_ENV_VARS['openai_primary'])
        assert var == 'OPENAI_MODEL'

    def test_non_chat_roles_keep_first_env_var(self, monkeypatch):
        monkeypatch.setitem(sys.modules, 'config', _stub_config(USE_OPENAI_API=False))
        var = model_overrides._representative_env_var(
            'openai_embedding', model_overrides.KEY_TO_ENV_VARS['openai_embedding'])
        assert var == 'AZURE_OPENAI_DEPLOYMENT_NAME_EMBEDDING'

    def test_status_reports_platform_openai_path(self, overrides_file, monkeypatch):
        monkeypatch.setitem(sys.modules, 'config', _stub_config(**self.AZURE_CFG))
        status = model_overrides.get_override_status()
        assert status['platform_openai_path'] == 'azure'

        cfg = dict(self.AZURE_CFG, USE_OPENAI_API=True)
        monkeypatch.setitem(sys.modules, 'config', _stub_config(**cfg))
        status = model_overrides.get_override_status()
        assert status['platform_openai_path'] == 'openai_direct'

    def test_status_effective_value_reads_azure_var_on_azure_path(
            self, overrides_file, monkeypatch):
        # No override set; OPENAI_MODEL points elsewhere but the platform is on
        # Azure — effective must report the Azure deployment, not OPENAI_MODEL.
        monkeypatch.setitem(sys.modules, 'config', _stub_config(**self.AZURE_CFG))
        monkeypatch.setenv('OPENAI_MODEL', 'gpt-direct-red-herring')
        monkeypatch.setenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-azure-live')
        status = model_overrides.get_override_status()
        assert status['effective_values']['openai_primary'] == 'gpt-azure-live'
