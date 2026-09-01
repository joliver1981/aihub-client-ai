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


# =============================================================================
# 3. BROWSER USE — a LIVE-RELOAD key (the service re-reads the file per run)
# =============================================================================

class TestBrowserUseLiveReloadKey:
    """'browser_use_model' is consumed by browser_use_service, which runs in its
    own env and re-reads data/model_overrides.json on every run. So: it must be
    an accepted key with a dropdown, it must NOT be exported into this process's
    env, it must never gate the restart banner, and its effective value must be
    the file (override) or the .env/platform fallback chain.
    """
    AZURE_CFG = dict(
        USE_OPENAI_API=False,
        OPENAI_DEPLOYMENT_NAME='gpt-direct',
        AZURE_OPENAI_DEPLOYMENT_NAME='gpt-azure',
        AZURE_OPENAI_DEPLOYMENT_NAME_MINI='gpt-azure-mini',
        AZURE_OPENAI_DEPLOYMENT_NAME_EMBEDDING='embed',
        OPENAI_VISION_MODEL='vision',
        ANTHROPIC_ADVANCED='claude-a', ANTHROPIC_MODEL='claude-m', ANTHROPIC_MINI='claude-mini',
    )

    def test_key_is_allowed_and_maps_to_service_env_var(self):
        assert 'browser_use_model' in model_overrides.ALLOWED_KEYS
        assert model_overrides.KEY_TO_ENV_VARS['browser_use_model'] == ['BROWSER_USE_LLM_MODEL']
        assert 'browser_use_model' in model_overrides.LIVE_RELOAD_KEYS

    def test_dropdown_exists_for_key(self):
        from supported_models import DROPDOWNS
        assert DROPDOWNS['browser_use_model'], 'browser_use_model needs a non-empty dropdown list'

    def test_save_accepts_key(self, overrides_file):
        merged = model_overrides.save_overrides({'browser_use_model': ' gpt-5.6-terra '})
        assert merged['browser_use_model'] == 'gpt-5.6-terra'
        assert json.loads(overrides_file.read_text())['browser_use_model'] == 'gpt-5.6-terra'

    def test_apply_does_not_export_live_key_into_env(self, overrides_file, monkeypatch):
        import os
        monkeypatch.setenv('BROWSER_USE_LLM_MODEL', 'from-dot-env')
        overrides_file.write_text(json.dumps({'browser_use_model': 'gpt-ui-pick'}))

        applied = model_overrides.apply_overrides_to_env()

        assert 'BROWSER_USE_LLM_MODEL' not in applied
        assert os.environ['BROWSER_USE_LLM_MODEL'] == 'from-dot-env'

    def test_status_effective_is_override_when_set(self, overrides_file, monkeypatch):
        monkeypatch.setitem(sys.modules, 'config', _stub_config(**self.AZURE_CFG))
        monkeypatch.setenv('BROWSER_USE_LLM_MODEL', 'from-dot-env')
        overrides_file.write_text(json.dumps({'browser_use_model': 'gpt-ui-pick'}))

        status = model_overrides.get_override_status()

        assert status['effective_values']['browser_use_model'] == 'gpt-ui-pick'
        assert status['any_override_active'] is True

    def test_status_default_follows_dot_env_pin_then_platform_primary(
            self, overrides_file, monkeypatch):
        monkeypatch.setitem(sys.modules, 'config', _stub_config(**self.AZURE_CFG))
        overrides_file.write_text(json.dumps({'browser_use_model': ''}))
        # _read_config_defaults lazily imports command_center_service/cc_config,
        # whose module body calls load_dotenv() — and under the stubbed `config`
        # that import fails, so it is never cached and would re-populate
        # BROWSER_USE_LLM_MODEL from the repo .env on EVERY call. Stub it out.
        monkeypatch.setitem(sys.modules, 'cc_config', types.ModuleType('cc_config'))

        monkeypatch.setenv('BROWSER_USE_LLM_MODEL', 'from-dot-env')
        monkeypatch.setenv('AZURE_OPENAI_DEPLOYMENT_NAME', 'gpt-azure-live')
        status = model_overrides.get_override_status()
        assert status['defaults']['browser_use_model'] == 'from-dot-env'
        assert status['effective_values']['browser_use_model'] == 'from-dot-env'

        # No dedicated pin → the platform's primary OpenAI deployment.
        monkeypatch.delenv('BROWSER_USE_LLM_MODEL', raising=False)
        status = model_overrides.get_override_status()
        assert status['defaults']['browser_use_model'] == 'gpt-azure-live'

        # Nothing anywhere → the service's built-in literal.
        monkeypatch.delenv('AZURE_OPENAI_DEPLOYMENT_NAME', raising=False)
        monkeypatch.setitem(sys.modules, 'config',
                            _stub_config(**dict(self.AZURE_CFG, AZURE_OPENAI_DEPLOYMENT_NAME='')))
        status = model_overrides.get_override_status()
        assert status['defaults']['browser_use_model'] == model_overrides.BROWSER_USE_BUILTIN_DEFAULT

    def test_live_key_never_gates_restart_required(self, overrides_file, monkeypatch):
        monkeypatch.setitem(sys.modules, 'config', _stub_config(**self.AZURE_CFG))
        # Make every non-live role consistent so they don't trip the banner.
        for key, env_vars in model_overrides.KEY_TO_ENV_VARS.items():
            for v in env_vars:
                monkeypatch.delenv(v, raising=False)
        for key in ('openai_primary', 'openai_mini', 'openai_vision', 'openai_embedding',
                    'openai_image', 'anthropic_primary', 'anthropic_mini'):
            default = {'openai_primary': 'gpt-5.6-terra', 'openai_mini': 'gpt-5.6-luna',
                       'openai_vision': 'gpt-4o', 'openai_embedding': 'text-embedding-3-small',
                       'openai_image': 'gpt-image-2', 'anthropic_primary': 'claude-opus-4-8',
                       'anthropic_mini': 'claude-sonnet-5'}[key]
            for v in model_overrides.KEY_TO_ENV_VARS[key]:
                monkeypatch.setenv(v, default)

        # Override set, env differs (it ALWAYS differs — the service never goes via env).
        monkeypatch.setenv('BROWSER_USE_LLM_MODEL', 'from-dot-env')
        overrides_file.write_text(json.dumps({'browser_use_model': 'gpt-ui-pick'}))
        assert model_overrides.get_override_status()['restart_required'] is False

        # Override cleared, .env pin differs from the built-in literal — still no banner.
        overrides_file.write_text(json.dumps({'browser_use_model': ''}))
        assert model_overrides.get_override_status()['restart_required'] is False


# =============================================================================
# 4. Document search strategy model — a LIVE key like browser_use_model
# =============================================================================

class TestDocSearchStrategyLiveKey:
    """The doc-search strategy step (DocUtils step 3/6) reads its model from
    data/model_overrides.json on every search, so the admin UI can point ONE
    lane at a cheaper model with no restart and no change to the platform model."""

    AZURE_CFG = TestPathAwareDisplay.AZURE_CFG

    def test_registered_as_a_live_key_with_a_dropdown(self):
        assert model_overrides.KEY_TO_ENV_VARS['doc_search_strategy'] == ['DOC_SEARCH_STRATEGY_MODEL']
        assert 'doc_search_strategy' in model_overrides.LIVE_RELOAD_KEYS
        from supported_models import DROPDOWNS
        assert DROPDOWNS['doc_search_strategy'], 'doc_search_strategy needs a non-empty dropdown list'
        assert 'gpt-5.6-luna' in DROPDOWNS['doc_search_strategy']

    def test_apply_never_exports_the_live_key(self, overrides_file, monkeypatch):
        import os
        monkeypatch.delenv('DOC_SEARCH_STRATEGY_MODEL', raising=False)
        overrides_file.write_text(json.dumps({'doc_search_strategy': 'gpt-5.6-luna'}))
        assert model_overrides.apply_overrides_to_env() == {}
        assert 'DOC_SEARCH_STRATEGY_MODEL' not in os.environ

    def test_resolve_live_override_precedence(self, overrides_file):
        overrides_file.write_text(json.dumps({'doc_search_strategy': ' gpt-5.6-luna '}))
        assert model_overrides.resolve_live_override('doc_search_strategy', 'from-env') == 'gpt-5.6-luna'
        overrides_file.write_text(json.dumps({'doc_search_strategy': ''}))
        assert model_overrides.resolve_live_override('doc_search_strategy', 'from-env') == 'from-env'
        overrides_file.unlink()
        assert model_overrides.resolve_live_override('doc_search_strategy', '') == ''
        overrides_file.write_text('not json')
        assert model_overrides.resolve_live_override('doc_search_strategy', 'from-env') == 'from-env'

    def test_default_shown_is_env_pin_else_openai_primary(self, monkeypatch):
        monkeypatch.setitem(sys.modules, 'config', _stub_config(**self.AZURE_CFG))
        monkeypatch.delenv('DOC_SEARCH_STRATEGY_MODEL', raising=False)
        assert model_overrides._read_config_defaults()['doc_search_strategy'] == 'gpt-azure'
        monkeypatch.setenv('DOC_SEARCH_STRATEGY_MODEL', 'gpt-5.6-luna')
        assert model_overrides._read_config_defaults()['doc_search_strategy'] == 'gpt-5.6-luna'

    def test_status_reports_the_file_as_the_live_value(self, overrides_file, monkeypatch):
        monkeypatch.setitem(sys.modules, 'config', _stub_config(**self.AZURE_CFG))
        # Pin every non-live role to its code default so this box's machine-env
        # model pins cannot trip the restart banner (same setup as the browser_use test).
        for key, env_vars in model_overrides.KEY_TO_ENV_VARS.items():
            for v in env_vars:
                monkeypatch.delenv(v, raising=False)
        for key, default in {'openai_primary': 'gpt-5.6-terra', 'openai_mini': 'gpt-5.6-luna',
                             'openai_vision': 'gpt-4o', 'openai_embedding': 'text-embedding-3-small',
                             'openai_image': 'gpt-image-2', 'anthropic_primary': 'claude-opus-4-8',
                             'anthropic_mini': 'claude-sonnet-5'}.items():
            for v in model_overrides.KEY_TO_ENV_VARS[key]:
                monkeypatch.setenv(v, default)
        overrides_file.write_text(json.dumps({'doc_search_strategy': 'gpt-5.6-luna'}))
        status = model_overrides.get_override_status()
        assert status['effective_values']['doc_search_strategy'] == 'gpt-5.6-luna'
        assert status['restart_required'] is False
