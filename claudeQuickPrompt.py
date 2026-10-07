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
    # Fallback aligned with config.py ANTHROPIC_MINI default ('claude-sonnet-5').
    # In normal operation config.ANTHROPIC_MODEL is set via .env and the
    # 'claude-sonnet-5' literal below is never reached.
    try:
        import config as cfg
        _ANTHROPIC_MODEL = (
            getattr(cfg, 'ANTHROPIC_MODEL', None)
            or getattr(cfg, 'ANTHROPIC_MINI', 'claude-sonnet-5')
        )
        _ANTHROPIC_MAX_TOKENS = int(getattr(cfg, 'ANTHROPIC_MAX_TOKENS', 4096))
    except ImportError:
        logger.warning("config.py not found, using defaults")
        _ANTHROPIC_MODEL = os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-5')
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
            # By design this process has no anthropic package (main app, vector
            # API, ...): run direct-mode calls on the Documents API instead of
            # failing — api_keys_config.ByokRelayClient returns the Messages JSON.
            try:
                from api_keys_config import ByokRelayClient, byok_route_via_doc_api_enabled
                relay_ok = byok_route_via_doc_api_enabled()
            except ImportError:
                relay_ok = False
            if not relay_ok:
                logger.error("anthropic package not installed in this process and the "
                             "Documents API route is off (BYOK_ROUTE_VIA_DOC_API=false)")
                raise
            _CLIENT = ByokRelayClient()
            logger.info(f"Claude direct mode (source: {_ANTHROPIC_CONFIG.get('source', 'unknown')}) "
                        f"routed via the Documents API (no anthropic package in this process)")
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


def _sampling_kwargs(model, temp):
    """{'temperature': t} when the Claude model accepts it, else {} — newer
    models (Opus 4.7+, Opus/Sonnet/Haiku 5.x, Fable 5) reject the param with a 400.
    Delegates to config.anthropic_sampling_kwargs; keeps a local fallback so
    this module still works without config.py (matching the module's design)."""
    try:
        from config import anthropic_sampling_kwargs
        return anthropic_sampling_kwargs(model, temp)
    except ImportError:
        m = (model or '').lower()
        no_sampling = ('opus-4-7', 'opus-4-8', 'opus-5', 'sonnet-5', 'haiku-5', 'fable-5', 'mythos-5', 'mythos-preview')
        if temp is None or any(marker in m for marker in no_sampling):
            return {}
        return {'temperature': temp}


def _text_of(response):
    """The reply text, wherever it sits — Claude 5.x models put a thinking block
    first, so content[0] has no text. Same contract as
    config.anthropic_response_text, kept local (no config import) so this
    module stays standalone, like _sampling_kwargs' marker fallback above."""
    content = response.get('content') if isinstance(response, dict) else getattr(response, 'content', None)
    if not content:
        raise ValueError(f"LLM returned no content: {str(response)[:300]}")

    def _field(block, name):
        return block.get(name) if isinstance(block, dict) else getattr(block, name, None)

    def _text(block):
        value = _field(block, 'text')
        return value if isinstance(value, str) and value else None

    parts = [_text(b) for b in content if _field(b, 'type') == 'text' and _text(b)]
    if not parts:
        parts = [_text(b) for b in content if _text(b)]   # untyped shapes / test doubles
    if not parts:
        raise ValueError("LLM returned no text block")
    return ''.join(parts)


def claudeQuickPrompt(prompt, system="You are an assistant.", temp=0.0, model=None):
    """
    Drop-in replacement for azureMiniQuickPrompt / azureQuickPrompt.

    Mirrors the exact same signature and return type:
        Input:  prompt (str), system (str), temp (float), model (str|None)
        Output: str — the AI response with markdown fences stripped

    Supports both direct Anthropic API and proxy mode, matching
    the project's existing BYOK / proxy architecture.

    Args:
        prompt: The user prompt to send
        system: System message (default: "You are an assistant.")
        temp:   Sampling temperature 0-1 (default: 0.0)
        model:  Optional model override. If None, uses cfg.ANTHROPIC_MODEL.
                Use this to dispatch cheap calls (e.g. fan-out extraction,
                rerank scoring) to cfg.ANTHROPIC_MINI without changing the
                default for the rest of the system.

    Returns:
        str: The AI response text, with ```json/```sql/``` fences stripped
    """
    _ensure_initialized()

    selected_model = model or _ANTHROPIC_MODEL

    try:
        messages = [{"role": "user", "content": prompt}]

        if _CLIENT:
            # ── Direct API mode ─────────────────────────────────────
            response = _CLIENT.messages.create(
                model=selected_model,
                max_tokens=_ANTHROPIC_MAX_TOKENS,
                system=system,
                messages=messages,
                **_sampling_kwargs(selected_model, temp)
            )
            response_text = _text_of(response)

        elif _PROXY_CLIENT:
            # ── Proxy mode ──────────────────────────────────────────
            # (proxy client gates temperature internally on the model)
            response = _PROXY_CLIENT.messages_create(
                model=selected_model,
                max_tokens=_ANTHROPIC_MAX_TOKENS,
                system=system,
                messages=messages,
                temperature=temp
            )
            # Proxy returns dict, not anthropic response object
            if isinstance(response, dict):
                if 'error' in response:
                    raise RuntimeError(f"Proxy error: {response['error']}")
                response_text = _text_of(response)
            else:
                response_text = _text_of(response)
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
