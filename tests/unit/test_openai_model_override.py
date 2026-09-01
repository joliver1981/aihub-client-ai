"""Per-call model override on the OpenAI path.

get_openai_config(model_override=...) swaps ONLY the model / deployment name the
slot resolved (transport untouched, reasoning_effort re-derived for the override);
azureQuickPrompt(model=...) plumbs it through. Both default to the old behaviour
so the ~50 existing azureQuickPrompt callers are unaffected.
"""
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

import api_keys_config


def _stub_config(**attrs):
    mod = types.ModuleType('config')
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


AZURE = dict(
    USE_OPENAI_API=False,
    OPENAI_DEPLOYMENT_NAME='gpt-direct', OPENAI_DEPLOYMENT_NAME_MINI='gpt-direct-mini',
    AZURE_OPENAI_DEPLOYMENT_NAME='gpt-5.6-terra', AZURE_OPENAI_DEPLOYMENT_NAME_ALTERNATE='gpt-5.6-terra',
    AZURE_OPENAI_DEPLOYMENT_NAME_MINI='gpt-5.6-luna', AZURE_OPENAI_DEPLOYMENT_NAME_ALTERNATE_MINI='gpt-5.6-luna',
    AZURE_OPENAI_API_KEY='k', AZURE_OPENAI_API_KEY_ALTERNATE='k',
    AZURE_OPENAI_BASE_URL='https://x', AZURE_OPENAI_BASE_URL_ALTERNATE='https://x',
    AZURE_OPENAI_API_VERSION='2024-12-01-preview',
    OPENAI_API_KEY='sk', OPENAI_API_BASE_URL='https://api.openai.com/v1',
    OPENAI_REASONING_EFFORT='medium', MINI_MODEL_REASONING_EFFORT='medium',
)


@pytest.fixture
def azure_cfg(monkeypatch):
    monkeypatch.setitem(sys.modules, 'config', _stub_config(**AZURE))
    monkeypatch.setattr(api_keys_config, 'is_byok_enabled', lambda: False)


@pytest.mark.unit
class TestGetOpenAIConfigModelOverride:
    def test_default_is_unchanged(self, azure_cfg):
        c = api_keys_config.get_openai_config()
        assert c['deployment_id'] == 'gpt-5.6-terra'
        assert 'model_override' not in c
        assert c['reasoning_effort'] == 'medium'

    def test_azure_override_swaps_only_the_deployment(self, azure_cfg):
        base = api_keys_config.get_openai_config()
        c = api_keys_config.get_openai_config(model_override='gpt-5.6-luna')
        assert c['deployment_id'] == 'gpt-5.6-luna'
        assert c['model_override'] == 'gpt-5.6-luna'
        for key in ('api_type', 'api_key', 'api_base', 'api_version', 'source', 'model'):
            assert c[key] == base[key], key

    def test_alternate_azure_override(self, azure_cfg):
        c = api_keys_config.get_openai_config(use_alternate_api=True, model_override='gpt-5.6-luna')
        assert c['source'] == 'azure_alternate'
        assert c['deployment_id'] == 'gpt-5.6-luna'

    def test_direct_openai_override_swaps_the_model(self, monkeypatch):
        monkeypatch.setitem(sys.modules, 'config', _stub_config(**dict(AZURE, USE_OPENAI_API=True)))
        monkeypatch.setattr(api_keys_config, 'is_byok_enabled', lambda: False)
        c = api_keys_config.get_openai_config(model_override='gpt-4o-mini')
        assert c['api_type'] == 'open_ai'
        assert c['model'] == 'gpt-4o-mini'
        assert c['deployment_id'] is None

    def test_reasoning_effort_follows_the_override(self, azure_cfg):
        assert api_keys_config.get_openai_config(model_override='gpt-4o-mini')['reasoning_effort'] is None
        assert api_keys_config.get_openai_config(model_override='gpt-5.6-luna')['reasoning_effort'] == 'medium'

    def test_blank_override_is_no_override(self, azure_cfg):
        for blank in ('', None):
            c = api_keys_config.get_openai_config(model_override=blank)
            assert c['deployment_id'] == 'gpt-5.6-terra'
            assert 'model_override' not in c


@pytest.mark.unit
class TestAzureQuickPromptModelParam:
    def _call(self, monkeypatch, **kw):
        import AppUtils
        seen = {}

        def fake_config(use_alternate_api=False, use_mini=False, model_override=None):
            seen['model_override'] = model_override
            return {'api_type': 'azure', 'api_key': 'k', 'api_base': 'https://x',
                    'api_version': 'v', 'deployment_id': model_override or 'gpt-5.6-terra',
                    'model': None, 'source': 'azure', 'reasoning_effort': 'medium'}

        client = MagicMock()
        client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content='```json\n{"ok": 1}\n```'))])
        monkeypatch.setattr(AppUtils, 'get_openai_config', fake_config)
        monkeypatch.setattr(AppUtils, '_create_openai_client', lambda config: client)
        out = AppUtils.azureQuickPrompt('p', system='s', **kw)
        return out, seen, client.chat.completions.create.call_args.kwargs

    def test_default_call_passes_no_override(self, monkeypatch):
        out, seen, create_kwargs = self._call(monkeypatch)
        assert seen['model_override'] is None
        assert create_kwargs['model'] == 'gpt-5.6-terra'
        assert out.strip() == '{"ok": 1}'

    def test_model_param_reaches_the_api_call(self, monkeypatch):
        _, seen, create_kwargs = self._call(monkeypatch, model='gpt-5.6-luna')
        assert seen['model_override'] == 'gpt-5.6-luna'
        assert create_kwargs['model'] == 'gpt-5.6-luna'
        assert create_kwargs['reasoning_effort'] == 'medium'
        assert create_kwargs['temperature'] == 1.0

    def test_anthropic_provider_honours_model_too(self, monkeypatch):
        import AppUtils
        seen = {}
        monkeypatch.setattr(AppUtils, '_anthropic_quick_prompt',
                            lambda prompt, system, temp, model: seen.setdefault('model', model) or 'x')
        AppUtils.azureQuickPrompt('p', provider='anthropic', model='claude-haiku-4-5')
        assert seen['model'] == 'claude-haiku-4-5'
