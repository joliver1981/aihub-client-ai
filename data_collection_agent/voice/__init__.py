"""
Voice provider abstraction for the data collection agent.

A single `synthesize(text, voice, format)` entry point dispatches to the
configured provider:

  - openai     — OpenAI TTS (`tts-1` default, fast)
  - azure      — Azure Cognitive Services Neural TTS (REST, no SDK dep)
  - browser    — sentinel that returns no audio; the frontend falls back to
                 SpeechSynthesisUtterance (free, robotic, last resort)

Provider chosen via env var `DCA_TTS_PROVIDER`. Defaults to `openai`
because the platform's existing OpenAI / Azure-OpenAI plumbing already has
credentials available.

Path B (streaming hybrid) calls this synchronously per sentence so the
playback queue can pipeline. Path D (Realtime) bypasses this entirely —
TTS is handled in-stream by OpenAI's Realtime API.
"""

import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


# Available providers — the fallback ordering is intentional. If the
# configured provider can't synthesize (missing key, network error, etc.),
# the caller should fall back to 'browser' which produces no audio bytes
# and signals the frontend to use the platform speech synthesis API.
PROVIDER_OPENAI = 'openai'
PROVIDER_AZURE = 'azure'
PROVIDER_BROWSER = 'browser'  # sentinel — no server-side synthesis

ALLOWED_PROVIDERS = (PROVIDER_OPENAI, PROVIDER_AZURE, PROVIDER_BROWSER)

# OpenAI tts-1 voices
OPENAI_VOICES = ('alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer')
DEFAULT_VOICE = 'nova'


def get_configured_provider() -> str:
    """Return the active TTS provider, defaulting to OpenAI."""
    val = (os.environ.get('DCA_TTS_PROVIDER') or PROVIDER_OPENAI).strip().lower()
    if val not in ALLOWED_PROVIDERS:
        logger.warning(f"Unknown DCA_TTS_PROVIDER={val!r}; defaulting to {PROVIDER_OPENAI}")
        return PROVIDER_OPENAI
    return val


def synthesize(
    text: str,
    voice: Optional[str] = None,
    fmt: str = 'mp3',
    provider: Optional[str] = None,
) -> Tuple[Optional[bytes], str, Optional[str]]:
    """
    Synthesize audio for `text`. Returns:
      (audio_bytes, mime_type, error_message)

    On success: (bytes, 'audio/mpeg' or 'audio/wav', None)
    On browser-fallback signal: (None, 'browser', None) — frontend uses
        SpeechSynthesisUtterance instead.
    On error: (None, '', error_message) — frontend should ALSO fall back
        to SpeechSynthesisUtterance, but log the error.
    """
    if not text or not text.strip():
        return None, '', 'empty text'

    voice = voice or DEFAULT_VOICE
    provider = (provider or get_configured_provider()).lower()

    if provider == PROVIDER_BROWSER:
        return None, 'browser', None

    if provider == PROVIDER_OPENAI:
        try:
            from .openai_tts import synthesize as _openai_synth
            return _openai_synth(text=text, voice=voice, fmt=fmt)
        except Exception as e:
            logger.error(f"OpenAI TTS failed: {e}", exc_info=True)
            return None, '', f'openai tts failed: {e}'

    if provider == PROVIDER_AZURE:
        try:
            from .azure_tts import synthesize as _azure_synth
            return _azure_synth(text=text, voice=voice, fmt=fmt)
        except Exception as e:
            logger.error(f"Azure TTS failed: {e}", exc_info=True)
            return None, '', f'azure tts failed: {e}'

    return None, '', f'unknown provider {provider!r}'
