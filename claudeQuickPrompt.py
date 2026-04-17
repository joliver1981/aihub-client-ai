"""
claudeQuickPrompt - Drop-in replacement for azureMiniQuickPrompt / azureQuickPrompt
====================================================================================

Uses the Anthropic Claude API instead of Azure OpenAI, avoiding the AppUtils.py
dependency chain (which requires pandas, pyodbc, win32com, etc.).

Function signatures mirror the Azure versions exactly:
    claudeQuickPrompt(prompt, system="You are an assistant.", temp=0.0) -> str

Integration:
    # Anywhere you currently do:
    from AppUtils import azureMiniQuickPrompt
    response = azureMiniQuickPrompt(prompt, system=system)
    
    # Replace with:
    from claudeQuickPrompt import claudeQuickPrompt
    response = claudeQuickPrompt(prompt, system=system)

Dependencies:
    - anthropic (pip install anthropic)
    - config.py (ANTHROPIC_MODEL, ANTHROPIC_MAX_TOKENS)
    - api_keys_config.py (get_anthropic_config)
    - CommonUtils.py (AnthropicProxyClient) — only needed for proxy mode
"""

import logging
import os

logger = logging.getLogger("claudeQuickPrompt")

# ── Config defaults (overridden by config.py if available) ──────────────
_ANTHROPIC_MODEL = None
_ANTHROPIC_MAX_TOKENS = 4096
_ANTHROPIC_CONFIG = None
_CLIENT = None       # Direct anthropic.Anthropic client
_PROXY_CLIENT = None # AnthropicProxyClient for proxy mode
_INITIALIZED = False


def _ensure_initialized():
    """Lazy initialization — runs once on first call."""
    global _ANTHROPIC_MODEL, _ANTHROPIC_MAX_TOKENS, _ANTHROPIC_CONFIG
    global _CLIENT, _PROXY_CLIENT, _INITIALIZED

    if _INITIALIZED:
        return

    # 1. Load config values
    try:
        import config as cfg
        _ANTHROPIC_MODEL = getattr(cfg, 'ANTHROPIC_MODEL', 'claude-sonnet-4-20250514')
        _ANTHROPIC_MAX_TOKENS = int(getattr(cfg, 'ANTHROPIC_MAX_TOKENS', 4096))
    except ImportError:
        logger.warning("config.py not found, using defaults")
        _ANTHROPIC_MODEL = os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4-20250514')
        _ANTHROPIC_MAX_TOKENS = int(os.getenv('ANTHROPIC_MAX_TOKENS', '4096'))

    # 2. Get Anthropic API configuration (handles BYOK + proxy logic)
    try:
        from api_keys_config import get_anthropic_config
        _ANTHROPIC_CONFIG = get_anthropic_config()
    except ImportError:
        logger.warning("api_keys_config not found, falling back to env vars")
        api_key = os.getenv('ANTHROPIC_API_KEY', '')
        _ANTHROPIC_CONFIG = {
            'use_direct_api': bool(api_key),
            'api_key': api_key,
            'source': 'env_var'
        }

    # 3. Initialize the appropriate client
    if _ANTHROPIC_CONFIG.get('use_direct_api'):
        try:
            import anthropic
            _CLIENT = anthropic.Anthropic(api_key=_ANTHROPIC_CONFIG['api_key'])
            logger.info(f"Claude direct client initialized (source: {_ANTHROPIC_CONFIG.get('source', 'unknown')})")
        except ImportError:
            logger.error("anthropic package not installed — pip install anthropic")
            raise
    else:
        try:
            from CommonUtils import AnthropicProxyClient
            _PROXY_CLIENT = AnthropicProxyClient()
            logger.info("Claude proxy client initialized")
        except ImportError:
            logger.error("AnthropicProxyClient not available and direct API not configured")
            raise ImportError(
                "Cannot initialize Claude client. Either set ANTHROPIC_API_KEY for direct API "
                "or ensure CommonUtils.AnthropicProxyClient is available for proxy mode."
            )

    _INITIALIZED = True


def claudeQuickPrompt(prompt, system="You are an assistant.", temp=0.0):
    """
    Drop-in replacement for azureMiniQuickPrompt / azureQuickPrompt.
    
    Mirrors the exact same signature and return type:
        Input:  prompt (str), system (str), temp (float)
        Output: str — the AI response with markdown fences stripped
    
    Supports both direct Anthropic API and proxy mode, matching
    the project's existing BYOK / proxy architecture.
    
    Args:
        prompt: The user prompt to send
        system: System message (default: "You are an assistant.")
        temp:   Sampling temperature 0-1 (default: 0.0)
        
    Returns:
        str: The AI response text, with ```json/```sql/``` fences stripped
    """
    _ensure_initialized()

    try:
        messages = [{"role": "user", "content": prompt}]

        if _CLIENT:
            # ── Direct API mode ─────────────────────────────────────
            response = _CLIENT.messages.create(
                model=_ANTHROPIC_MODEL,
                max_tokens=_ANTHROPIC_MAX_TOKENS,
                system=system,
                messages=messages,
                temperature=temp
            )
            response_text = response.content[0].text

        elif _PROXY_CLIENT:
            # ── Proxy mode ──────────────────────────────────────────
            response = _PROXY_CLIENT.messages_create(
                model=_ANTHROPIC_MODEL,
                max_tokens=_ANTHROPIC_MAX_TOKENS,
                system=system,
                messages=messages,
                temperature=temp
            )
            # Proxy returns dict, not anthropic response object
            if isinstance(response, dict):
                if 'error' in response:
                    raise RuntimeError(f"Proxy error: {response['error']}")
                response_text = response['content'][0]['text']
            else:
                response_text = response.content[0].text
        else:
            raise RuntimeError("No Claude client available (neither direct nor proxy)")

        # Strip markdown fences — matches azureQuickPrompt behavior
        response_text = str(response_text)
        response_text = response_text.replace('```json', '').replace('```sql', '').replace('python```', '').replace('```', '')

        return response_text

    except Exception as e:
        logger.error(f"claudeQuickPrompt error: {e}")
        raise


# ── Aliases for maximum compatibility ───────────────────────────────────
# Use whichever name makes sense at the call site
claudeMiniQuickPrompt = claudeQuickPrompt
claudeQuickPromptMini = claudeQuickPrompt
