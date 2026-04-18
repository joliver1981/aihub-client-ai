"""
image_params.py
---------------
Model-aware parameter builder for OpenAI's images.generate() endpoint.

Different OpenAI image models accept different parameter sets. Using the wrong
parameter for a model causes an API error (e.g. `response_format` is valid on
DALL-E 2/3 but rejected by gpt-image-1).

This module centralizes that logic so call sites just pass
(model, prompt, size) and get back a dict that is safe to splat into
`client.images.generate(**kwargs)`.

Supported families:
    - dall-e-3   (default)
    - dall-e-2
    - gpt-image-*  (matches gpt-image-1, gpt-image-1.5, and any future gpt-image-N)

Anything else is treated as dall-e-3 shape (with a warning-logged for the caller
to see), since that's what this codebase has historically sent.
"""
from __future__ import annotations

from typing import Dict, Tuple


# ─── Size tables ─────────────────────────────────────────────────────────────
# Each family has a set of valid size strings plus a preferred size for each
# orientation (square / portrait / landscape). Users can request any WxH and
# we'll map to the closest valid size for the model.

_SIZE_TABLE: Dict[str, Dict[str, str]] = {
    'dall-e-3': {
        'square':    '1024x1024',
        'portrait':  '1024x1792',
        'landscape': '1792x1024',
    },
    'dall-e-2': {
        # DALL-E 2 only supports 1:1 aspect. Non-square requests collapse to 1024x1024.
        'square':    '1024x1024',
        'portrait':  '1024x1024',
        'landscape': '1024x1024',
    },
    'gpt-image': {
        'square':    '1024x1024',
        'portrait':  '1024x1536',
        'landscape': '1536x1024',
    },
}

_VALID_SIZES: Dict[str, set] = {
    'dall-e-3': {'1024x1024', '1024x1792', '1792x1024'},
    'dall-e-2': {'256x256', '512x512', '1024x1024'},
    # gpt-image-1 also supports 'auto' but we only map to concrete sizes here.
    'gpt-image': {'1024x1024', '1024x1536', '1536x1024', 'auto'},
}


def _model_family(model: str) -> str:
    """Classify a model string into a family key ('dall-e-3', 'dall-e-2', 'gpt-image').
    Unknown models default to 'dall-e-3' shape (conservative)."""
    m = (model or '').strip().lower()
    if m.startswith('gpt-image'):
        return 'gpt-image'
    if m == 'dall-e-2':
        return 'dall-e-2'
    return 'dall-e-3'


def _classify_orientation(size: str) -> str:
    """Return 'portrait' | 'landscape' | 'square' for a 'WxH' size string.
    Malformed input returns 'square'."""
    try:
        w_str, h_str = (size or '').lower().split('x')
        w, h = int(w_str), int(h_str)
    except (ValueError, AttributeError):
        return 'square'
    if h > w:
        return 'portrait'
    if w > h:
        return 'landscape'
    return 'square'


def _normalize_size(model: str, size: str) -> str:
    """Pick a valid size string for the given model.

    If `size` is already valid for this model's family, return it unchanged.
    Otherwise, map to the closest-orientation preferred size.
    """
    family = _model_family(model)
    # If user passed a size that's valid for THIS family, honor it
    if size in _VALID_SIZES.get(family, set()):
        return size
    # Otherwise: classify the requested orientation and pick the family's default
    orient = _classify_orientation(size)
    return _SIZE_TABLE[family].get(orient, _SIZE_TABLE[family]['square'])


def build_image_generate_kwargs(
    model: str,
    prompt: str,
    size: str = '1024x1024',
    quality: str | None = None,
) -> Dict:
    """Build kwargs for openai.OpenAI().images.generate(**kwargs) that are
    compatible with the given model.

    Args:
        model:   The OpenAI image model name (e.g. 'dall-e-3', 'gpt-image-1').
        prompt:  The image description.
        size:    Requested size string 'WxH'. Will be normalized to a valid
                 size for the target model if necessary.
        quality: Optional explicit quality override. If None, a family-default
                 is used. Note that valid quality values differ by family:
                    dall-e-3:   'standard' | 'hd'
                    dall-e-2:   (not accepted — omit)
                    gpt-image:  'low' | 'medium' | 'high' | 'auto'

    Returns:
        A dict you can splat into client.images.generate(**kwargs).
        All variants in this codebase always return base64 in the response,
        so `b64 = response.data[0].b64_json` works uniformly downstream.
    """
    family = _model_family(model)
    kwargs: Dict = {
        'model': model,
        'prompt': prompt,
        'n': 1,
        'size': _normalize_size(model, size),
    }

    if family == 'gpt-image':
        # gpt-image-1 / gpt-image-1.5: always returns base64, NO response_format
        # parameter accepted. Quality values are low/medium/high/auto.
        q = quality if quality in ('low', 'medium', 'high', 'auto') else 'auto'
        kwargs['quality'] = q

    elif family == 'dall-e-2':
        # DALL-E 2: no quality parameter at all, needs response_format for b64.
        kwargs['response_format'] = 'b64_json'

    else:  # dall-e-3 (and unknown-model fallback)
        # DALL-E 3: quality is 'standard' or 'hd'; needs response_format for b64.
        q = quality if quality in ('standard', 'hd') else 'standard'
        kwargs['quality'] = q
        kwargs['response_format'] = 'b64_json'

    return kwargs


__all__ = ['build_image_generate_kwargs']
