"""
OpenAI Text-to-Speech provider.

Uses the same direct-OpenAI client pattern as `whisper_routes.py` (the
platform's existing STT route): instantiate a `openai.OpenAI` with
`cfg.OPENAI_API_KEY`, call `client.audio.speech.create`, return the bytes.

Voice and model choices:
  - `tts-1`     — fast, lower latency. Default for streaming chunks.
  - `tts-1-hd`  — higher fidelity, slightly slower. Good for longer reads.

Voices: alloy, echo, fable, onyx, nova, shimmer. Default 'nova'
(natural, clear, voice-mode-friendly).

Format defaults to mp3 — universally supported by HTML <audio>.
"""

import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Lazy-init module-level client (matches whisper_routes.py)
_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client

    # Try cfg.OPENAI_API_KEY first (platform's existing config)
    api_key = None
    try:
        import config as cfg
        api_key = getattr(cfg, 'OPENAI_API_KEY', None)
    except Exception:
        pass
    # Fall back to env vars
    if not api_key:
        api_key = os.environ.get('OPENAI_API_KEY')

    if not api_key:
        raise RuntimeError(
            "No OpenAI API key configured. Set cfg.OPENAI_API_KEY or "
            "OPENAI_API_KEY env var."
        )

    from openai import OpenAI
    _client = OpenAI(api_key=api_key)
    return _client


# Map common short format names to OpenAI's accepted values + their MIME types
_FORMAT_MAP = {
    'mp3':  ('mp3',  'audio/mpeg'),
    'wav':  ('wav',  'audio/wav'),
    'opus': ('opus', 'audio/ogg'),
    'aac':  ('aac',  'audio/aac'),
    'flac': ('flac', 'audio/flac'),
}


def synthesize(
    text: str,
    voice: str = 'nova',
    fmt: str = 'mp3',
    model: Optional[str] = None,
) -> Tuple[Optional[bytes], str, Optional[str]]:
    """
    Synthesize speech with OpenAI TTS.

    Returns:
      (audio_bytes, mime_type, None) on success
      (None, '', error_message) on failure
    """
    if not text or not text.strip():
        return None, '', 'empty text'

    api_fmt, mime = _FORMAT_MAP.get(fmt.lower(), ('mp3', 'audio/mpeg'))

    try:
        client = _get_client()
    except Exception as e:
        return None, '', str(e)

    model = model or os.environ.get('DCA_TTS_MODEL', 'tts-1')

    try:
        # streaming response — read full bytes (small enough that we don't
        # need to chunk per-network-packet here; the frontend chunks per
        # sentence at the LLM-streaming layer)
        with client.audio.speech.with_streaming_response.create(
            model=model,
            voice=voice,
            input=text,
            response_format=api_fmt,
        ) as response:
            audio_bytes = b''.join(response.iter_bytes())
        if not audio_bytes:
            return None, '', 'no audio bytes returned'
        return audio_bytes, mime, None
    except Exception as e:
        logger.error(f"OpenAI TTS request failed: {e}", exc_info=True)
        return None, '', f'openai tts call failed: {e}'
