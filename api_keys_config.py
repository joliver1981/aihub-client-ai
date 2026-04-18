"""
API Keys Configuration Module
==============================

Enables "Bring Your Own Key" (BYOK) functionality for AI Hub.
Allows users to use their own OpenAI and Anthropic API keys instead of
the system-provided keys.

Storage:
- API keys: Stored in local secrets (encrypted, machine-bound)
- BYOK enabled toggle: Stored in data/byok_config.json (simple config file)

Usage:
    from api_keys_config import api_keys_bp, register_page_route, init_byok
    app.register_blueprint(api_keys_bp)
"""

import os
import json
import logging
from pathlib import Path
from flask import Blueprint, jsonify, request, render_template
from functools import wraps
from typing import Dict, Optional, Any

from local_secrets import (
    get_secrets_manager,
    get_local_secret,
    set_local_secret,
    has_local_secret
)

logger = logging.getLogger(__name__)

api_keys_bp = Blueprint('api_keys_config', __name__, url_prefix='/api/api-keys')

# =============================================================================
# Constants
# =============================================================================

# Secret names for user-provided API keys (stored in encrypted local secrets)
OPENAI_API_KEY_SECRET = 'USER_OPENAI_API_KEY'
ANTHROPIC_API_KEY_SECRET = 'USER_ANTHROPIC_API_KEY'

# Category for organization in local secrets UI
API_KEYS_CATEGORY = 'llm_api_keys'

# Config file path for BYOK enabled state (not sensitive, just config)
def _get_config_file_path() -> Path:
    """Get path to BYOK config file."""
    data_dir = Path(os.getenv('AIHUB_DATA_DIR', './data'))
    return data_dir / 'byok_config.json'


# =============================================================================
# Authentication Decorator
# =============================================================================

def require_admin(f):
    """Require admin role (role >= 3) for access."""
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            from flask_login import current_user
            if not current_user.is_authenticated:
                return jsonify({'success': False, 'error': 'Authentication required'}), 401
            if current_user.role < 3:
                return jsonify({'success': False, 'error': 'Admin access required'}), 403
        except Exception as e:
            logger.error(f"Auth check failed: {e}")
            return jsonify({'success': False, 'error': 'Authentication check failed'}), 500
        return f(*args, **kwargs)
    return decorated


# =============================================================================
# Configuration Management
# =============================================================================

def _load_config() -> Dict[str, Any]:
    """Load BYOK config from file."""
    config_file = _get_config_file_path()
    
    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error reading BYOK config: {e}")
    
    return {'byok_enabled': False}


def _save_config(config: Dict[str, Any]) -> bool:
    """Save BYOK config to file."""
    config_file = _get_config_file_path()
    
    try:
        # Ensure directory exists
        config_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(config_file, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except IOError as e:
        logger.error(f"Error saving BYOK config: {e}")
        return False


def is_byok_enabled() -> bool:
    """Check if BYOK is enabled."""
    config = _load_config()
    return config.get('byok_enabled', False)


def set_byok_enabled(enabled: bool) -> bool:
    """Set BYOK enabled state."""
    config = _load_config()
    config['byok_enabled'] = enabled
    success = _save_config(config)
    
    if success:
        # Apply environment changes
        apply_byok_environment()
    
    return success


def get_byok_status() -> Dict[str, Any]:
    """
    Get the current BYOK status.
    
    Returns:
        Dict with status:
        {
            'byok_enabled': bool,
            'openai_configured': bool,
            'anthropic_configured': bool,
            'openai_key_preview': str,
            'anthropic_key_preview': str
        }
    """
    result = {
        'byok_enabled': is_byok_enabled(),
        'openai_configured': False,
        'anthropic_configured': False,
        'openai_key_preview': '',
        'anthropic_key_preview': ''
    }
    
    # Check OpenAI
    if has_local_secret(OPENAI_API_KEY_SECRET):
        result['openai_configured'] = True
        key = get_local_secret(OPENAI_API_KEY_SECRET, '')
        if key:
            if len(key) > 12:
                result['openai_key_preview'] = f"{key[:7]}...{key[-4:]}"
            else:
                result['openai_key_preview'] = '••••••••'
    
    # Check Anthropic
    if has_local_secret(ANTHROPIC_API_KEY_SECRET):
        result['anthropic_configured'] = True
        key = get_local_secret(ANTHROPIC_API_KEY_SECRET, '')
        if key:
            if len(key) > 16:
                result['anthropic_key_preview'] = f"{key[:10]}...{key[-4:]}"
            else:
                result['anthropic_key_preview'] = '••••••••'
    
    return result


def apply_byok_environment():
    """
    Apply BYOK configuration to environment variables.
    
    Sets BYPASS_PROXY=true if BYOK is enabled AND user has configured their own OpenAI key.
    """
    status = get_byok_status()
    
    if status['byok_enabled'] and status['openai_configured']:
        os.environ['BYPASS_PROXY'] = 'true'
        logger.info("BYOK: Enabled with user OpenAI key - setting BYPASS_PROXY=true")
    else:
        os.environ['BYPASS_PROXY'] = 'false'
        if not status['byok_enabled']:
            logger.info("BYOK: Disabled - setting BYPASS_PROXY=false")
        else:
            logger.info("BYOK: Enabled but no OpenAI key configured - setting BYPASS_PROXY=false")


def get_active_openai_key() -> Optional[str]:
    """
    Get the active OpenAI API key.
    
    Returns user's key if BYOK is enabled and key is configured, None otherwise.
    """
    if is_byok_enabled() and has_local_secret(OPENAI_API_KEY_SECRET):
        return get_local_secret(OPENAI_API_KEY_SECRET)
    return None


def _is_reasoning_model(model_name: str) -> bool:
    """Check if a model supports reasoning/thinking parameters."""
    if not model_name:
        return False
    name = model_name.lower()
    reasoning_prefixes = ['gpt-5', 'o1', 'o3', 'o4']
    return any(name.startswith(prefix) or f'/{prefix}' in name for prefix in reasoning_prefixes)


def get_openai_config(use_alternate_api: bool = False, use_mini: bool = False) -> Dict[str, Any]:
    """
    Get the complete OpenAI configuration based on current settings.

    Priority:
    1. BYOK enabled + user key configured → Direct OpenAI with user's key
    2. cfg.USE_OPENAI_API=True → Direct OpenAI with system key
    3. Default → Azure OpenAI (proxy)

    Args:
        use_alternate_api: If True and using Azure, use alternate Azure deployment
        use_mini: If True, use the mini/smaller model variant

    Returns:
        Dict with all config needed for OpenAI calls:
        {
            'api_type': 'open_ai' or 'azure',
            'api_key': str,
            'api_base': str or None,
            'api_version': str or None,
            'deployment_id': str (for Azure) or None,
            'model': str (for direct OpenAI) or None,
            'source': 'byok' | 'system_openai' | 'azure' | 'azure_alternate',
            'reasoning_effort': str or None  # e.g. 'low', 'medium', 'high'
        }
    """
    import config as cfg

    # Determine which model name to use for direct OpenAI
    if use_mini:
        default_model = getattr(cfg, 'OPENAI_DEPLOYMENT_NAME_MINI', 'gpt-5.4-mini')
    else:
        default_model = getattr(cfg, 'OPENAI_DEPLOYMENT_NAME', 'gpt-5.2')

    # Priority 1: BYOK with user's key
    if is_byok_enabled() and has_local_secret(OPENAI_API_KEY_SECRET):
        user_key = get_local_secret(OPENAI_API_KEY_SECRET)
        config = {
            'api_type': 'open_ai',
            'api_key': user_key,
            'api_base': getattr(cfg, 'OPENAI_API_BASE_URL', 'https://api.openai.com/v1'),
            'api_version': None,
            'deployment_id': None,
            'model': default_model,
            'source': 'byok'
        }
    elif getattr(cfg, 'USE_OPENAI_API', False):
        # Priority 2: System configured for direct OpenAI
        config = {
            'api_type': 'open_ai',
            'api_key': cfg.OPENAI_API_KEY,
            'api_base': getattr(cfg, 'OPENAI_API_BASE_URL', 'https://api.openai.com/v1'),
            'api_version': None,
            'deployment_id': None,
            'model': default_model,
            'source': 'system_openai'
        }
    elif use_alternate_api:
        # Priority 3a: Alternate Azure deployment
        if use_mini:
            deployment = getattr(cfg, 'AZURE_OPENAI_DEPLOYMENT_NAME_ALTERNATE_MINI',
                                 cfg.AZURE_OPENAI_DEPLOYMENT_NAME_ALTERNATE)
        else:
            deployment = cfg.AZURE_OPENAI_DEPLOYMENT_NAME_ALTERNATE

        config = {
            'api_type': 'azure',
            'api_key': cfg.AZURE_OPENAI_API_KEY_ALTERNATE,
            'api_base': cfg.AZURE_OPENAI_BASE_URL_ALTERNATE,
            'api_version': cfg.AZURE_OPENAI_API_VERSION,
            'deployment_id': deployment,
            'model': None,
            'source': 'azure_alternate'
        }
    else:
        # Priority 3b: Primary Azure deployment
        if use_mini:
            deployment = getattr(cfg, 'AZURE_OPENAI_DEPLOYMENT_NAME_MINI',
                                 cfg.AZURE_OPENAI_DEPLOYMENT_NAME)
        else:
            deployment = cfg.AZURE_OPENAI_DEPLOYMENT_NAME

        config = {
            'api_type': 'azure',
            'api_key': cfg.AZURE_OPENAI_API_KEY,
            'api_base': cfg.AZURE_OPENAI_BASE_URL,
            'api_version': cfg.AZURE_OPENAI_API_VERSION,
            'deployment_id': deployment,
            'model': None,
            'source': 'azure'
        }

    # Add reasoning effort for models that support it
    effective_model = config.get('model') or config.get('deployment_id') or ''
    if _is_reasoning_model(effective_model):
        if use_mini:
            config['reasoning_effort'] = getattr(cfg, 'MINI_MODEL_REASONING_EFFORT', 'low')
        else:
            config['reasoning_effort'] = getattr(cfg, 'OPENAI_REASONING_EFFORT', 'low')
    else:
        config['reasoning_effort'] = None

    return config


def is_using_byok_openai() -> bool:
    """
    Check if currently using BYOK for OpenAI.
    
    Useful for conditional logic or logging.
    """
    return is_byok_enabled() and has_local_secret(OPENAI_API_KEY_SECRET)


def get_active_anthropic_key() -> Optional[str]:
    """
    Get the active Anthropic API key.
    
    Returns user's key if BYOK is enabled and key is configured, None otherwise.
    """
    if is_byok_enabled() and has_local_secret(ANTHROPIC_API_KEY_SECRET):
        return get_local_secret(ANTHROPIC_API_KEY_SECRET)
    return None


def get_anthropic_config() -> Dict[str, Any]:
    """
    Get the Anthropic API configuration based on current settings.
    
    Priority:
    1. BYOK enabled + user key → Direct Anthropic with user's key
    2. cfg.AI_HUB_BYPASS_DOCUMENT_PROXY=True → Direct Anthropic with system key
    3. Default → Use proxy
    
    Returns:
        Dict with configuration:
        {
            'use_direct_api': bool,
            'api_key': str or None,
            'model': str,
            'max_tokens': int,
            'source': 'byok' | 'system_direct' | 'proxy'
        }
    """
    import config as cfg
    
    # Get default model and max_tokens from config
    default_model = getattr(cfg, 'ANTHROPIC_MODEL', 'claude-3-7-sonnet-20250219')
    default_max_tokens = int(getattr(cfg, 'ANTHROPIC_MAX_TOKENS', 4096))
    
    # Priority 1: BYOK with user's key
    if is_byok_enabled() and has_local_secret(ANTHROPIC_API_KEY_SECRET):
        user_key = get_local_secret(ANTHROPIC_API_KEY_SECRET)
        return {
            'use_direct_api': True,
            'api_key': user_key,
            'model': default_model,
            'max_tokens': default_max_tokens,
            'source': 'byok'
        }
    
    # Priority 2: System configured for direct Anthropic
    if getattr(cfg, 'AI_HUB_BYPASS_DOCUMENT_PROXY', False):
        return {
            'use_direct_api': True,
            'api_key': getattr(cfg, 'ANTHROPIC_API_KEY', None),
            'model': default_model,
            'max_tokens': default_max_tokens,
            'source': 'system_direct'
        }
    
    # Priority 3: Use proxy (default)
    return {
        'use_direct_api': False,
        'api_key': None,
        'model': default_model,
        'max_tokens': default_max_tokens,
        'source': 'proxy'
    }


def create_anthropic_client():
    """
    Create the appropriate Anthropic client based on BYOK/config settings.
    
    Returns:
        tuple: (anthropic_client or None, config dict)
        
    Usage:
        client, config = create_anthropic_client()
        if config['use_direct_api']:
            response = client.messages.create(...)
        else:
            # Use AnthropicProxyClient instead
    """
    config = get_anthropic_config()
    
    if config['use_direct_api'] and config['api_key']:
        import anthropic
        client = anthropic.Anthropic(api_key=config['api_key'])
        return client, config
    
    return None, config


def create_pandasai_llm(use_alternate_api=True):
    """Create a PandasAI LLM using centralized BYOK-aware config.

    Returns a pandasai-openai LLM instance configured via get_openai_config(),
    respecting BYOK priority (user key > system OpenAI > Azure).
    """
    from pandasai_openai import OpenAI as PandasAIOpenAI
    from pandasai_openai import AzureOpenAI as PandasAIAzureOpenAI
    import openai as openai_sdk
    import config as cfg

    config = get_openai_config(use_alternate_api=use_alternate_api)

    temperature = float(cfg.LLM_TEMPERATURE)
    if config.get('reasoning_effort'):
        temperature = 1.0

    if config['api_type'] == 'open_ai':
        model = config['model']
        # pandasai_openai validates against a hardcoded supported model list
        # that may not include newer models (e.g. gpt-5.2)
        if model not in PandasAIOpenAI._supported_chat_models:
            PandasAIOpenAI._supported_chat_models.append(model)
        return PandasAIOpenAI(
            api_token=config['api_key'],
            model=model,
            temperature=temperature,
            seed=int(cfg.LLM_SEED),
        )
    else:
        # PandasAI's AzureOpenAI constructs the SDK client with both
        # azure_deployment and base_url, which breaks URL routing through
        # the AI Hub proxy.  This inline subclass builds a clean SDK client
        # that uses azure_endpoint only (no base_url, no azure_deployment).
        class _CleanAzureOpenAI(PandasAIAzureOpenAI):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self._clean_client = openai_sdk.AzureOpenAI(
                    api_key=self.api_token,
                    api_version=self.api_version,
                    azure_endpoint=self.azure_endpoint,
                ).chat.completions

            def chat_completion(self, value, memory):
                messages = memory.to_openai_messages() if memory else []
                messages.append({"role": "user", "content": value})
                kwargs = {"messages": messages, "model": self.deployment_name}
                if _is_reasoning_model(self.deployment_name):
                    kwargs["temperature"] = 1.0
                    kwargs["reasoning_effort"] = getattr(cfg, 'OPENAI_REASONING_EFFORT', 'low')
                else:
                    kwargs["temperature"] = self.temperature
                response = self._clean_client.create(**kwargs)
                return response.choices[0].message.content

        return _CleanAzureOpenAI(
            api_token=config['api_key'],
            azure_endpoint=config['api_base'],
            api_version=config['api_version'],
            deployment_name=config['deployment_id'],
            temperature=temperature,
            seed=int(cfg.LLM_SEED),
        )


def is_using_byok_anthropic() -> bool:
    """
    Check if currently using BYOK for Anthropic.
    
    Useful for conditional logic or logging.
    """
    return is_byok_enabled() and has_local_secret(ANTHROPIC_API_KEY_SECRET)


# =============================================================================
# API Key Testing
# =============================================================================

def test_openai_key(api_key: str) -> Dict[str, Any]:
    """Test an OpenAI API key by making a simple API call."""
    try:
        import requests
        
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }
        
        response = requests.get(
            'https://api.openai.com/v1/models',
            headers=headers,
            timeout=15
        )
        
        if response.status_code == 200:
            data = response.json()
            model_count = len(data.get('data', []))
            return {
                'valid': True,
                'message': f'API key is valid. Access to {model_count} models.',
                'details': {'model_count': model_count}
            }
        elif response.status_code == 401:
            return {
                'valid': False,
                'message': 'Invalid API key - authentication failed',
                'details': {'status_code': 401}
            }
        elif response.status_code == 429:
            return {
                'valid': True,
                'message': 'API key is valid but rate limited. Try again later.',
                'details': {'status_code': 429}
            }
        else:
            error_msg = response.json().get('error', {}).get('message', 'Unknown error')
            return {
                'valid': False,
                'message': f'API returned error: {error_msg}',
                'details': {'status_code': response.status_code}
            }
            
    except requests.exceptions.Timeout:
        return {'valid': None, 'message': 'Request timed out - could not verify key', 'details': {'error': 'timeout'}}
    except requests.exceptions.ConnectionError:
        return {'valid': None, 'message': 'Connection failed - check network connectivity', 'details': {'error': 'connection_error'}}
    except Exception as e:
        return {'valid': None, 'message': f'Verification failed: {str(e)}', 'details': {'error': str(e)}}


def test_anthropic_key(api_key: str) -> Dict[str, Any]:
    """Test an Anthropic API key by making a simple API call."""
    try:
        import requests
        
        headers = {
            'x-api-key': api_key,
            'Content-Type': 'application/json',
            'anthropic-version': '2023-06-01'
        }
        
        payload = {
            'model': 'claude-3-haiku-20240307',
            'max_tokens': 10,
            'messages': [{'role': 'user', 'content': 'Say "ok"'}]
        }
        
        response = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            return {'valid': True, 'message': 'API key is valid and working.', 'details': {'status_code': 200}}
        elif response.status_code == 401:
            return {'valid': False, 'message': 'Invalid API key - authentication failed', 'details': {'status_code': 401}}
        elif response.status_code == 403:
            return {'valid': False, 'message': 'API key does not have permission for this operation', 'details': {'status_code': 403}}
        elif response.status_code == 429:
            return {'valid': True, 'message': 'API key is valid but rate limited. Try again later.', 'details': {'status_code': 429}}
        elif response.status_code == 529:
            return {'valid': True, 'message': 'API key is valid but Anthropic API is overloaded.', 'details': {'status_code': 529}}
        else:
            error_data = response.json() if response.content else {}
            error_msg = error_data.get('error', {}).get('message', 'Unknown error')
            return {'valid': False, 'message': f'API returned error: {error_msg}', 'details': {'status_code': response.status_code}}
            
    except requests.exceptions.Timeout:
        return {'valid': None, 'message': 'Request timed out - could not verify key', 'details': {'error': 'timeout'}}
    except requests.exceptions.ConnectionError:
        return {'valid': None, 'message': 'Connection failed - check network connectivity', 'details': {'error': 'connection_error'}}
    except Exception as e:
        return {'valid': None, 'message': f'Verification failed: {str(e)}', 'details': {'error': str(e)}}


# =============================================================================
# API Routes
# =============================================================================

@api_keys_bp.route('/status', methods=['GET'])
@require_admin
def get_status():
    """Get current BYOK status."""
    try:
        status = get_byok_status()
        return jsonify({'success': True, 'status': status})
    except Exception as e:
        logger.error(f"Error getting BYOK status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@api_keys_bp.route('/toggle', methods=['POST'])
@require_admin
def toggle_byok():
    """Enable or disable BYOK."""
    try:
        data = request.get_json() or {}
        enabled = bool(data.get('enabled', False))
        
        if set_byok_enabled(enabled):
            status = get_byok_status()
            action = 'enabled' if enabled else 'disabled'
            return jsonify({
                'success': True,
                'message': f'BYOK {action}. {"Your API keys will now be used." if enabled else "System keys will now be used."}',
                'status': status
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to save configuration'}), 500
            
    except Exception as e:
        logger.error(f"Error toggling BYOK: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@api_keys_bp.route('/openai', methods=['POST'])
@require_admin
def save_openai_key():
    """Save OpenAI API key."""
    try:
        data = request.get_json()
        
        if not data or not data.get('api_key'):
            return jsonify({'success': False, 'error': 'API key is required'}), 400
        
        api_key = data['api_key'].strip()
        
        if not api_key.startswith('sk-'):
            return jsonify({'success': False, 'error': 'Invalid OpenAI API key format (should start with sk-)'}), 400
        
        set_local_secret(
            OPENAI_API_KEY_SECRET,
            api_key,
            description='Your OpenAI API key for AI Agents',
            category=API_KEYS_CATEGORY
        )
        
        # Apply environment changes if BYOK is enabled
        apply_byok_environment()
        status = get_byok_status()
        
        return jsonify({
            'success': True,
            'message': 'OpenAI API key saved.',
            'status': status
        })
        
    except Exception as e:
        logger.error(f"Error saving OpenAI key: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@api_keys_bp.route('/anthropic', methods=['POST'])
@require_admin
def save_anthropic_key():
    """Save Anthropic API key."""
    try:
        data = request.get_json()
        
        if not data or not data.get('api_key'):
            return jsonify({'success': False, 'error': 'API key is required'}), 400
        
        api_key = data['api_key'].strip()
        
        if not api_key.startswith('sk-ant-'):
            return jsonify({'success': False, 'error': 'Invalid Anthropic API key format (should start with sk-ant-)'}), 400
        
        set_local_secret(
            ANTHROPIC_API_KEY_SECRET,
            api_key,
            description='Your Anthropic API key for Document Processing',
            category=API_KEYS_CATEGORY
        )
        
        status = get_byok_status()
        
        return jsonify({
            'success': True,
            'message': 'Anthropic API key saved.',
            'status': status
        })
        
    except Exception as e:
        logger.error(f"Error saving Anthropic key: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@api_keys_bp.route('/openai', methods=['DELETE'])
@require_admin
def delete_openai_key():
    """Delete stored OpenAI API key."""
    try:
        manager = get_secrets_manager()
        
        if manager.exists(OPENAI_API_KEY_SECRET):
            manager.delete(OPENAI_API_KEY_SECRET)
        
        apply_byok_environment()
        status = get_byok_status()
        
        return jsonify({
            'success': True,
            'message': 'OpenAI API key deleted.',
            'status': status
        })
        
    except Exception as e:
        logger.error(f"Error deleting OpenAI key: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@api_keys_bp.route('/anthropic', methods=['DELETE'])
@require_admin
def delete_anthropic_key():
    """Delete stored Anthropic API key."""
    try:
        manager = get_secrets_manager()
        
        if manager.exists(ANTHROPIC_API_KEY_SECRET):
            manager.delete(ANTHROPIC_API_KEY_SECRET)
        
        status = get_byok_status()
        
        return jsonify({
            'success': True,
            'message': 'Anthropic API key deleted.',
            'status': status
        })
        
    except Exception as e:
        logger.error(f"Error deleting Anthropic key: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@api_keys_bp.route('/openai/test', methods=['POST'])
@require_admin
def test_openai():
    """Test OpenAI API key."""
    try:
        data = request.get_json() or {}
        
        api_key = data.get('api_key', '').strip()
        if not api_key:
            api_key = get_local_secret(OPENAI_API_KEY_SECRET)
            if not api_key:
                return jsonify({'success': False, 'error': 'No API key provided or configured'}), 400
        
        result = test_openai_key(api_key)
        return jsonify({'success': True, **result})
        
    except Exception as e:
        logger.error(f"Error testing OpenAI key: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@api_keys_bp.route('/anthropic/test', methods=['POST'])
@require_admin
def test_anthropic():
    """Test Anthropic API key."""
    try:
        data = request.get_json() or {}
        
        api_key = data.get('api_key', '').strip()
        if not api_key:
            api_key = get_local_secret(ANTHROPIC_API_KEY_SECRET)
            if not api_key:
                return jsonify({'success': False, 'error': 'No API key provided or configured'}), 400
        
        result = test_anthropic_key(api_key)
        return jsonify({'success': True, **result})

    except Exception as e:
        logger.error(f"Error testing Anthropic key: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# Model Overrides (admin-only UI for overriding default LLM model names)
# =============================================================================
# Works independently of BYOK. See model_overrides.py for the storage format
# and the key -> env-var mapping. Changes require a service restart to take
# effect (same as BYOK).

@api_keys_bp.route('/model-overrides', methods=['GET'])
@require_admin
def get_model_overrides():
    """Return current overrides + dropdown lists + effective values + BYOK state."""
    try:
        from model_overrides import get_override_status
        status = get_override_status()
        return jsonify({'success': True, **status})
    except Exception as e:
        logger.error(f"Error getting model overrides: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@api_keys_bp.route('/model-overrides', methods=['POST'])
@require_admin
def save_model_overrides():
    """Save model overrides. Body is a partial dict of {key: value}.
    Only keys in model_overrides.ALLOWED_KEYS are accepted. Empty-string
    values clear that specific override.
    """
    try:
        from model_overrides import save_overrides, get_override_status, ALLOWED_KEYS

        data = request.get_json() or {}
        if not isinstance(data, dict):
            return jsonify({'success': False, 'error': 'Body must be a JSON object'}), 400

        # Silently drop any non-allowed keys before validation; the UI may send
        # extra keys we don't care about.
        filtered = {k: v for k, v in data.items() if k in ALLOWED_KEYS}
        if not filtered:
            return jsonify({'success': False, 'error': 'No valid override keys in request'}), 400

        save_overrides(filtered)
        status = get_override_status()
        return jsonify({
            'success': True,
            'message': 'Model overrides saved. Restart services for changes to take effect.',
            **status,
        })
    except ValueError as ve:
        return jsonify({'success': False, 'error': str(ve)}), 400
    except Exception as e:
        logger.error(f"Error saving model overrides: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@api_keys_bp.route('/model-overrides', methods=['DELETE'])
@require_admin
def delete_model_overrides():
    """Clear all model overrides (delete the overrides file)."""
    try:
        from model_overrides import clear_overrides, get_override_status
        clear_overrides()
        status = get_override_status()
        return jsonify({
            'success': True,
            'message': 'All model overrides cleared. Restart services for changes to take effect.',
            **status,
        })
    except Exception as e:
        logger.error(f"Error clearing model overrides: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# Initialization
# =============================================================================

def init_byok():
    """
    Initialize BYOK configuration on application startup.
    
    Call this from app.py during initialization to apply saved BYOK settings.
    """
    try:
        apply_byok_environment()
        status = get_byok_status()
        
        if status['byok_enabled']:
            providers = []
            if status['openai_configured']:
                providers.append('OpenAI')
            if status['anthropic_configured']:
                providers.append('Anthropic')
            
            if providers:
                logger.info(f"BYOK: Active for {', '.join(providers)}")
            else:
                logger.info("BYOK: Enabled but no keys configured")
        else:
            logger.info("BYOK: Disabled - using system keys")
            
    except Exception as e:
        logger.error(f"Error initializing BYOK: {e}")


# =============================================================================
# Page Route
# =============================================================================

def register_page_route(app):
    """
    Register the API Keys configuration page route.
    
    Call this from app.py:
        from api_keys_config import register_page_route
        register_page_route(app)
    """
    from flask_login import login_required, current_user
    
    @app.route('/admin/api-keys')
    @login_required
    def api_keys_config_page():
        if current_user.role < 3:
            from flask import abort
            abort(403)
        return render_template('api_keys_config.html')
