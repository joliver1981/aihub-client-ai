"""
Azure Cognitive Services Neural TTS provider.

REST-based — does NOT use `azure-cognitiveservices-speech` SDK so we don't
have to add a heavy dependency. The REST API takes SSML, returns audio
bytes. Auth is via subscription key in `Ocp-Apim-Subscription-Key` header.

Configure via env:
  - AZURE_SPEECH_KEY     — required; subscription key
  - AZURE_SPEECH_REGION  — required; e.g. 'eastus'
  - DCA_AZURE_VOICE      — optional; default 'en-US-AriaNeural'

Activate by setting `DCA_TTS_PROVIDER=azure`.
"""

import html
import logging
import os
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)


# Default Azure neural voice (warm, voice-mode-friendly)
DEFAULT_AZURE_VOICE = 'en-US-AriaNeural'


# Map our short fmt names to Azure's X-Microsoft-OutputFormat values
_FORMAT_MAP = {
    'mp3':  ('audio-24khz-96kbitrate-mono-mp3', 'audio/mpeg'),
    'wav':  ('riff-24khz-16bit-mono-pcm',       'audio/wav'),
    'opus': ('audio-24khz-bitrate-vbr-opus',    'audio/ogg'),
}


def synthesize(
    text: str,
    voice: Optional[str] = None,
    fmt: str = 'mp3',
) -> Tuple[Optional[bytes], str, Optional[str]]:
    key = os.environ.get('AZURE_SPEECH_KEY')
    region = os.environ.get('AZURE_SPEECH_REGION')
    if not key or not region:
        return None, '', (
            'Azure TTS not configured. Set AZURE_SPEECH_KEY and '
            'AZURE_SPEECH_REGION env vars, or pick a different '
            'DCA_TTS_PROVIDER.'
        )

    voice = voice or os.environ.get('DCA_AZURE_VOICE') or DEFAULT_AZURE_VOICE
    azure_fmt, mime = _FORMAT_MAP.get(fmt.lower(), _FORMAT_MAP['mp3'])

    # Build minimal SSML — escape user text since we're embedding in XML
    ssml = (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xml:lang="en-US">'
        f'<voice name="{html.escape(voice, quote=True)}">'
        f'{html.escape(text)}'
        f'</voice></speak>'
    )

    url = f'https://{region}.tts.speech.microsoft.com/cognitiveservices/v1'
    headers = {
        'Ocp-Apim-Subscription-Key': key,
        'Content-Type': 'application/ssml+xml',
        'X-Microsoft-OutputFormat': azure_fmt,
        'User-Agent': 'data_collection_agent',
    }

    try:
        response = requests.post(url, headers=headers, data=ssml.encode('utf-8'), timeout=20)
    except requests.exceptions.RequestException as e:
        return None, '', f'azure tts network error: {e}'

    if response.status_code != 200:
        return None, '', (
            f'azure tts returned HTTP {response.status_code}: '
            f'{response.text[:200]}'
        )

    audio = response.content
    if not audio:
        return None, '', 'azure tts returned empty body'
    return audio, mime, None
