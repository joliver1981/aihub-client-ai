"""LLM provider abstraction for model_tester.

Supported providers:
- openai     : direct OpenAI API
- azure      : Azure OpenAI deployment
- anthropic  : Claude messages API (HTTP, no SDK dependency)
- lmstudio   : local LMStudio (OpenAI-compatible HTTP)

Each provider exposes the same chat() interface.
"""
import os
import json
import time
import requests


def get_default_api_key(provider: str) -> str:
    """Pull the API key the main aihub app uses for the given provider.

    Falls back to environment variables if the secure store isn't reachable.
    Resolution order:
      1. main app's secure_config (if importable)
      2. environment variable (OPENAI_API_KEY / AZURE_OPENAI_API_KEY / ANTHROPIC_API_KEY)
    """
    try:
        # Try secure_config from the parent app
        import sys
        sys.path.insert(0, r'C:\src\aihub-client-ai-dev')
        from secure_config import load_secure_config  # noqa
        load_secure_config()
    except Exception:
        pass

    keys = {
        'openai':    os.getenv('OPENAI_API_KEY', ''),
        'azure':     os.getenv('AZURE_OPENAI_API_KEY', '') or os.getenv('OPENAI_API_KEY', ''),
        'anthropic': os.getenv('ANTHROPIC_API_KEY', ''),
        'lmstudio':  'lm-studio',  # LMStudio doesn't enforce auth; dummy value
    }
    return keys.get(provider, '')


def get_default_endpoint(provider: str) -> str:
    """Pull default endpoint for providers that need one (Azure)."""
    if provider == 'azure':
        return os.getenv('AZURE_OPENAI_ENDPOINT', '') or os.getenv('OPENAI_API_BASE', '')
    if provider == 'lmstudio':
        return 'http://localhost:1234/v1'
    return ''


def chat(model_config: dict, system_prompt: str, user_prompt: str,
         temperature: float = 0.2, max_tokens: int = 8192) -> dict:
    """Call the configured model. Returns {ok, content, error, usage, elapsed_ms}.

    model_config example:
      {provider: 'azure', deployment: 'gpt-4.1-mini',
       endpoint: 'https://...', api_version: '2024-08-01-preview',
       api_key_override: null}
    """
    provider = model_config.get('provider', '').lower()
    api_key = model_config.get('api_key_override') or get_default_api_key(provider)
    if not api_key:
        return {'ok': False, 'error': f'No API key available for provider {provider}'}

    t0 = time.time()
    try:
        if provider == 'openai':
            content, usage = _chat_openai(model_config, api_key, system_prompt, user_prompt, temperature, max_tokens)
        elif provider == 'azure':
            content, usage = _chat_azure(model_config, api_key, system_prompt, user_prompt, temperature, max_tokens)
        elif provider == 'anthropic':
            content, usage = _chat_anthropic(model_config, api_key, system_prompt, user_prompt, temperature, max_tokens)
        elif provider == 'lmstudio':
            content, usage = _chat_lmstudio(model_config, api_key, system_prompt, user_prompt, temperature, max_tokens)
        else:
            return {'ok': False, 'error': f'Unknown provider: {provider}'}
        return {
            'ok': True,
            'content': content,
            'usage': usage,
            'elapsed_ms': int((time.time() - t0) * 1000),
        }
    except Exception as e:
        return {
            'ok': False,
            'error': f'{type(e).__name__}: {e}',
            'elapsed_ms': int((time.time() - t0) * 1000),
        }


def _create_chat_with_token_param_fallback(client, model, messages, temp, max_tokens):
    """Try max_completion_tokens first (required by newer models like gpt-5,
    o1, o3); fall back to max_tokens for older models that don't support it.
    Also retry without temperature if the model rejects non-default temps."""
    try:
        return client.chat.completions.create(
            model=model, messages=messages,
            temperature=temp, max_completion_tokens=max_tokens,
        )
    except Exception as e:
        msg = str(e).lower()
        # Older models reject max_completion_tokens
        if 'max_completion_tokens' in msg and ('unsupported' in msg or 'unknown' in msg or 'not support' in msg):
            try:
                return client.chat.completions.create(
                    model=model, messages=messages,
                    temperature=temp, max_tokens=max_tokens,
                )
            except Exception as e2:
                msg2 = str(e2).lower()
                if 'temperature' in msg2 and ('unsupported' in msg2 or 'not support' in msg2):
                    return client.chat.completions.create(
                        model=model, messages=messages, max_tokens=max_tokens,
                    )
                raise
        # Some reasoning models reject non-default temperature
        if 'temperature' in msg and ('unsupported' in msg or 'not support' in msg):
            return client.chat.completions.create(
                model=model, messages=messages, max_completion_tokens=max_tokens,
            )
        raise


def _chat_openai(cfg, api_key, system, user, temp, max_tokens):
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]
    resp = _create_chat_with_token_param_fallback(client, cfg['model'], messages, temp, max_tokens)
    return resp.choices[0].message.content, _usage_dict(resp.usage)


def _resolve_main_app_azure_defaults():
    """Read-only call into the main app's api_keys_config.get_openai_config().
    Used when the model_tester's Azure model has blank deployment/endpoint —
    so we transparently use whatever the production app is using."""
    try:
        import sys
        if r'C:\src\aihub-client-ai-dev' not in sys.path:
            sys.path.insert(0, r'C:\src\aihub-client-ai-dev')
        from api_keys_config import get_openai_config
        cfg = get_openai_config() or {}
        if cfg.get('api_type') != 'azure':
            return None
        return {
            'deployment': cfg.get('deployment_id') or '',
            'endpoint': cfg.get('api_base') or '',
            'api_version': cfg.get('api_version') or '2024-08-01-preview',
            'api_key': cfg.get('api_key') or '',
        }
    except Exception:
        return None


def _chat_azure(cfg, api_key, system, user, temp, max_tokens):
    from openai import AzureOpenAI
    endpoint = cfg.get('endpoint') or get_default_endpoint('azure')
    deployment = cfg.get('deployment') or ''
    api_version = cfg.get('api_version') or '2024-08-01-preview'

    # Fallback: if the model_tester's azure config has blank deployment/endpoint,
    # pull whatever the main app currently uses. We never write to the main app.
    if not deployment or not endpoint:
        defaults = _resolve_main_app_azure_defaults()
        if defaults:
            deployment = deployment or defaults['deployment']
            endpoint = endpoint or defaults['endpoint']
            if api_version == '2024-08-01-preview':
                api_version = defaults.get('api_version') or api_version
            if not cfg.get('api_key_override'):
                api_key = api_key or defaults['api_key']

    if not endpoint:
        raise RuntimeError('Azure endpoint not configured. Set it in Settings or '
                           'ensure the main app has AZURE_OPENAI_ENDPOINT / OPENAI_API_BASE in env.')
    if not deployment:
        raise RuntimeError('Azure deployment not configured. Set it in Settings or '
                           'ensure the main app has a deployment configured in api_keys_config.')

    client = AzureOpenAI(api_key=api_key, api_version=api_version, azure_endpoint=endpoint)
    messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]
    resp = _create_chat_with_token_param_fallback(client, deployment, messages, temp, max_tokens)
    return resp.choices[0].message.content, _usage_dict(resp.usage)


def _chat_anthropic(cfg, api_key, system, user, temp, max_tokens):
    """Anthropic Messages API via HTTP. Falls back to no-temperature if the
    model deprecates that parameter (newer reasoning-style Claude models)."""
    headers = {
        'x-api-key': api_key,
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json',
    }
    base_payload = {
        'model': cfg['model'],
        'max_tokens': max_tokens,
        'system': system,
        'messages': [{'role': 'user', 'content': user}],
    }
    payload = {**base_payload, 'temperature': temp}
    r = requests.post('https://api.anthropic.com/v1/messages',
                      headers=headers, json=payload, timeout=600)
    if r.status_code == 400 and 'temperature' in r.text.lower() and (
        'deprecated' in r.text.lower() or 'not support' in r.text.lower() or 'unsupported' in r.text.lower()
    ):
        # Retry without temperature
        r = requests.post('https://api.anthropic.com/v1/messages',
                          headers=headers, json=base_payload, timeout=600)
    if r.status_code != 200:
        raise RuntimeError(f'Anthropic API {r.status_code}: {r.text[:500]}')
    data = r.json()
    content_blocks = data.get('content') or []
    text = ''.join(b.get('text', '') for b in content_blocks if b.get('type') == 'text')
    usage = data.get('usage') or {}
    return text, {
        'prompt_tokens': usage.get('input_tokens'),
        'completion_tokens': usage.get('output_tokens'),
        'total_tokens': (usage.get('input_tokens') or 0) + (usage.get('output_tokens') or 0),
    }


def _chat_lmstudio(cfg, api_key, system, user, temp, max_tokens):
    """LMStudio is OpenAI-compatible at a custom base URL."""
    from openai import OpenAI
    base_url = cfg.get('endpoint') or get_default_endpoint('lmstudio')
    client = OpenAI(api_key=api_key, base_url=base_url)
    messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]
    model = cfg.get('model') or 'local-model'
    resp = _create_chat_with_token_param_fallback(client, model, messages, temp, max_tokens)
    return resp.choices[0].message.content, _usage_dict(resp.usage)


def _usage_dict(usage):
    if usage is None:
        return {}
    if hasattr(usage, 'model_dump'):
        return usage.model_dump()
    return {
        'prompt_tokens': getattr(usage, 'prompt_tokens', None),
        'completion_tokens': getattr(usage, 'completion_tokens', None),
        'total_tokens': getattr(usage, 'total_tokens', None),
    }
