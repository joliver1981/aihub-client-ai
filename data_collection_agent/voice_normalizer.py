"""
LLM-backed voice transcript normalizer.

When a user dictates a value through the browser's STT, the raw transcript
often contains artifacts that can't be cleanly parsed:

  "celebrate 81 at gmail dot com"  ->  user meant "celebrate81@gmail.com"
  "five five five one two three four"  ->  "5551234"
  "next Tuesday"  ->  "2026-05-12"
  "the priority one"  ->  matches a "priority" select option
  "about twenty five bucks"  ->  25

Rather than enumerate field-type-specific regex rules (which we tried — they
were brittle and broke on every new phrasing), we delegate to a small fast
LLM. It gets the field's schema (label, type, hint, options) plus the raw
transcript, and returns the cleaned value the user clearly intended.

Cost / latency: gpt-4o-mini class, ~100 input + ~30 output tokens per call,
roughly $0.0001 and ~300ms. Cheap enough to call on every voice-mode
update_field invocation; fast enough to not be felt in conversation flow.

Failure mode: any error (no API key, network, malformed response) returns
the raw value unchanged. The downstream validator will then either accept
it as-is or surface an error, which is no worse than the pre-LLM behavior.
"""

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger("DataCollectionAgent.VoiceNormalizer")


# ---------------------------------------------------------------------------
# OpenAI client cache — built lazily on first use, reused thereafter.
# We use the raw OpenAI SDK rather than going through LangChain so the
# call has minimal overhead. The auth/provider config still comes from the
# platform's `api_keys_config.get_openai_config(use_mini=True)`.
# ---------------------------------------------------------------------------
_client_cache: Dict[str, Any] = {
    'client': None,
    'model': None,
    'config_signature': None,  # detect config change between calls
}


def _get_client_and_model():
    """Return (client, model_name) for the mini model. Builds + caches on
    first call. Returns (None, None) if config or import fails — callers
    must treat that as 'no normalization available, pass through'."""
    try:
        from api_keys_config import get_openai_config
    except Exception as e:
        logger.warning("voice_normalizer: api_keys_config unavailable: %s", e)
        return None, None

    try:
        config = get_openai_config(use_mini=True)
    except Exception as e:
        logger.warning("voice_normalizer: get_openai_config failed: %s", e)
        return None, None

    sig = (
        config.get('api_type'),
        config.get('model') or config.get('deployment_id'),
        config.get('api_base'),
        config.get('api_version'),
    )
    if _client_cache['client'] is not None and _client_cache['config_signature'] == sig:
        return _client_cache['client'], _client_cache['model']

    try:
        if config.get('api_type') == 'open_ai':
            from openai import OpenAI
            client = OpenAI(api_key=config['api_key'])
            model = config['model']
        else:
            from openai import AzureOpenAI
            client = AzureOpenAI(
                api_key=config['api_key'],
                api_version=config['api_version'],
                azure_endpoint=config['api_base'],
            )
            model = config['deployment_id']
    except Exception as e:
        logger.warning("voice_normalizer: client init failed: %s", e)
        return None, None

    _client_cache['client'] = client
    _client_cache['model'] = model
    _client_cache['config_signature'] = sig
    return client, model


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------
_SYSTEM = (
    "You are a voice transcript cleaner for a form-filling assistant. The "
    "user dictated a value into a single form field. The browser's "
    "speech-to-text is imperfect — it inserts spaces, splits or merges "
    "words, spells out punctuation as words, swaps homophones, and adds "
    "fillers ('um', 'like'). Your job is to recover the value the user "
    "clearly intended, given the field's type and label.\n"
    "\n"
    "Output rules — these are strict:\n"
    "1. Output ONLY a JSON object: {\"value\": <cleaned>, \"confidence\": "
    "\"high\"|\"medium\"|\"low\"}.\n"
    "2. The cleaned `value` is a string unless the field type is number "
    "(then a JSON number) or boolean (then true/false).\n"
    "3. If the transcript is genuinely unparseable, return the original "
    "transcript verbatim with confidence \"low\".\n"
    "4. Never invent characters that have no plausible source in the "
    "input. If you're guessing past the data, that's confidence \"low\".\n"
    "5. For emails: collapse spaces in the local part, replace spoken "
    "'at'/'dot' with '@'/'.', lowercase.\n"
    "6. For phones: keep digits in order, drop fillers, convert spelled-out "
    "digits ('five' -> '5'). Preserve country/area code grouping if clear.\n"
    "7. For numbers: return the numeric value the user named.\n"
    "8. For dates: prefer ISO-8601 (YYYY-MM-DD) when the date is clear; "
    "leave natural language if ambiguous.\n"
    "9. For select fields: if a list of options is given, pick the option "
    "whose label or id best matches the transcript and return that "
    "option's id. If nothing reasonably matches, return the transcript.\n"
    "10. For free text: trim, collapse whitespace, drop obvious fillers, "
    "preserve the user's wording otherwise.\n"
    "\n"
    "Do not add commentary, explanations, or markdown. Just the JSON."
)


def _build_user_prompt(raw_value: str, field: Dict[str, Any], options: Optional[list]) -> str:
    parts = [
        f"Field label: {field.get('label') or field.get('id')}",
        f"Field type: {field.get('type', 'text')}",
    ]
    if field.get('prompt_hint'):
        parts.append(f"Hint: {field['prompt_hint']}")
    if field.get('description'):
        parts.append(f"Description: {field['description']}")
    if options:
        # Compact option list — use get_option_id so DB-backed views with
        # `topic_id`, `speaker_id` etc. (no plain `id` column) still
        # surface a real id. Capped to keep tokens down.
        from .validation_engine import get_option_id as _get_id
        compact = []
        for o in options[:25]:
            if isinstance(o, dict):
                compact.append(f"  - id={_get_id(o)!r}, label={o.get('label') or o.get('name')!r}")
            else:
                compact.append(f"  - {o!r}")
        parts.append("Options:")
        parts.extend(compact)
        if len(options) > 25:
            parts.append(f"  ... ({len(options) - 25} more)")
    parts.append(f"Raw transcript: {raw_value!r}")
    parts.append("")
    parts.append("Return ONLY the JSON object now:")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def normalize_voice_value(
    raw_value: Any,
    field: Dict[str, Any],
    options: Optional[list] = None,
    timeout_seconds: float = 8.0,
    session: Any = None,
) -> Any:
    """
    Clean up a raw STT transcript for a single form field by asking a fast
    mini LLM to interpret the user's intent.

    Args:
        raw_value: The raw transcript value as it came from the agent or UI.
        field: The field schema dict (id, type, label, prompt_hint, ...).
        options: For select/lookup fields, the resolved option list — passed
            through so the model can pick a matching id directly.
        timeout_seconds: Max time to wait for the mini model.

    Returns:
        The cleaned value. On any failure (no client, timeout, parse error,
        explicit low-confidence pass-through) returns `raw_value` unchanged.
        The downstream validator handles the rest.
    """
    if raw_value is None or not isinstance(raw_value, str):
        return raw_value
    if not raw_value.strip():
        return raw_value

    # Local override for offline / dev / test environments.
    if os.getenv('DCA_VOICE_NORMALIZE', '1') == '0':
        return raw_value

    client, model = _get_client_and_model()
    if client is None or model is None:
        return raw_value

    user_prompt = _build_user_prompt(raw_value, field, options)

    # Variant retry: newer reasoning models (gpt-5.x, o-series) require
    # `max_completion_tokens` and reject both `max_tokens` and
    # `temperature`; legacy models accept the old shape. We try the new
    # one first since most fresh deployments are reasoning models now.
    def _call(use_json_format: bool, set_temp: bool, token_param: str):
        kwargs = {
            'model': model,
            'messages': [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            'timeout': timeout_seconds,
        }
        kwargs[token_param] = 300
        if set_temp:
            kwargs['temperature'] = 0.0
        if use_json_format:
            kwargs['response_format'] = {"type": "json_object"}
        return client.chat.completions.create(**kwargs)

    variants = [
        ('max_completion_tokens', True,  False),
        ('max_completion_tokens', False, False),
        ('max_tokens',            True,  True),
        ('max_tokens',            True,  False),
        ('max_tokens',            False, False),
    ]
    resp = None
    last_error = None
    for token_param, use_json_format, set_temp in variants:
        try:
            resp = _call(use_json_format, set_temp, token_param)
            break
        except Exception as e:
            last_error = e
            msg = str(e).lower()
            # Only retry on "unsupported parameter" / 400-style errors
            if 'unsupported' in msg or 'not supported' in msg or 'invalid' in msg or '400' in msg:
                continue
            break

    if resp is None:
        logger.warning(
            "voice_normalizer: LLM call failed for field %s (%s): %s",
            field.get('id'), field.get('type'), last_error,
        )
        return raw_value

    content = ''
    try:
        content = (resp.choices[0].message.content or "").strip()
        # Strip markdown code fences if the model added them despite instructions
        if content.startswith('```'):
            content = content.strip('`')
            # Remove leading "json" language tag
            if content.lower().startswith('json'):
                content = content[4:].strip()
        data = json.loads(content)
        cleaned = data.get('value', raw_value)
        confidence = (data.get('confidence') or '').lower()
    except Exception as e:
        logger.warning(
            "voice_normalizer: bad JSON for field %s: %s — content=%r",
            field.get('id'), e, content,
        )
        return raw_value

    # Defensive: model may return a dict/list when it shouldn't. Coerce
    # back to string for non-numeric/boolean fields. The validator does
    # the final type coercion anyway.
    field_type = field.get('type', 'text')
    if field_type not in ('number', 'boolean') and not isinstance(cleaned, (str, list)):
        cleaned = str(cleaned)

    logger.info(
        "voice_normalizer: %s (%s)  %r -> %r  [%s]",
        field.get('id'), field_type, raw_value, cleaned, confidence or 'unknown',
    )
    # Debug-mode visibility — late import to avoid a startup cycle
    if session is not None:
        try:
            from .debug_mode import debug_log
            debug_log(session, 'voice_normalize', {
                'field_id': field.get('id'),
                'field_type': field_type,
                'raw': raw_value,
                'cleaned': cleaned,
                'confidence': confidence,
                'model': model,
                'options_count': len(options) if options else 0,
            })
        except Exception:
            pass
    return cleaned
