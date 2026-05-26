"""
Deterministic field-extraction layer.

Runs on EVERY user message, BEFORE the conversational agent gets a turn.
The extractor uses a small fast LLM to read the user's message against the
current section's schema (and what's already been collected) and returns a
list of {section_id, field_id, value} entries to save. We then call the
same coercion + validation pipeline as `update_field` and persist the
results directly to the session — without depending on the conversational
agent to call any tool.

Why this exists:
    The agent LLM is great at conversation but unreliable at "always call
    update_field with raw words" — it self-rejects, asks the user to
    repeat, or pre-cleans values badly. By extracting values upstream and
    saving them deterministically, the agent's job shrinks to confirming
    what was just saved and asking the next question. It can no longer
    refuse to capture a value because of imperfect transcription.

What it does NOT do:
    - It does NOT replace conversational tools (advance_section, show_recap,
      etc.) — those still flow through the agent.
    - It does NOT touch fields the user clearly isn't talking about.
    - It does NOT "guess" — when the model is unsure, it returns nothing
      and lets the agent ask a clarifying question.

Failure mode:
    Any error (no API key, network, JSON parse error, validation error)
    falls through silently. The agent runs as before. The user might have
    to repeat themselves once, but nothing is corrupted.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from .schema_loader import (
    get_field, get_section, get_section_order, get_lookup_values,
)
from .validation_engine import (
    coerce_value, resolve_select_value, resolve_multi_select_value,
    validate_field,
)
from .state_manager import save_session
from .debug_mode import debug_log

logger = logging.getLogger("DataCollectionAgent.Extractor")

# Loud one-time startup banner so it's obvious in the log whether the new
# extractor module loaded. If you don't see this on server start, the new
# code isn't deployed.
logger.info("=" * 60)
logger.info("FIELD EXTRACTOR MODULE LOADED — pre-agent extraction active")
logger.info("Disable via env var: DCA_DISABLE_EXTRACTION=1")
logger.info("=" * 60)


# ---------------------------------------------------------------------------
# Mini-LLM client (mirrors voice_normalizer pattern — same provider, same auth)
# ---------------------------------------------------------------------------
_client_cache: Dict[str, Any] = {
    'client': None, 'model': None, 'config_signature': None,
}

# Once we discover which call shape the deployment accepts, cache it so
# every subsequent extraction skips the variant retry loop. Resets when
# the client config changes (different deployment, key rotation, etc.).
# Shape: {'token_param': 'max_completion_tokens', 'json_format': True, 'set_temp': False}
_call_shape_cache: Dict[str, Any] = {'shape': None, 'config_signature': None}


def _get_client_and_model():
    try:
        from api_keys_config import get_openai_config
    except Exception as e:
        logger.warning("extractor: api_keys_config unavailable: %s", e)
        return None, None
    try:
        config = get_openai_config(use_mini=True)
    except Exception as e:
        logger.warning("extractor: get_openai_config failed: %s", e)
        return None, None

    sig = (
        config.get('api_type'),
        config.get('model') or config.get('deployment_id'),
        config.get('api_base'), config.get('api_version'),
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
        logger.warning("extractor: client init failed: %s", e)
        return None, None

    _client_cache['client'] = client
    _client_cache['model'] = model
    _client_cache['config_signature'] = sig
    return client, model


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
_SYSTEM = (
    "You extract field values from a user's chat message for a guided "
    "form-filling session. You are the FIRST step in the pipeline — the "
    "values you save are then confirmed by the conversational agent. "
    "Be aggressive about extracting; the validator will catch anything "
    "genuinely wrong. Being too cautious is worse than being wrong, "
    "because the user just has to repeat themselves either way.\n"
    "\n"
    "INPUT YOU GET:\n"
    "  - Currently active section, with its fields\n"
    "  - All other sections (so cross-section answers can be captured)\n"
    "  - Already-collected values\n"
    "  - The RECENT CONVERSATION HISTORY (very important — use it to "
    "    resolve contextual references and corrections)\n"
    "  - The user's current message (often a raw speech-to-text "
    "    transcript with artifacts like spaces in emails, spelled-out "
    "    punctuation, fillers)\n"
    "\n"
    "USE CONVERSATION CONTEXT — this is critical:\n"
    "  The user almost never repeats themselves verbatim. They reference, "
    "  correct, or partially answer. You MUST read the conversation and "
    "  resolve their meaning.\n"
    "\n"
    "  Examples of how to think:\n"
    "    AI: 'What's your target date?'\n"
    "    User: 'June 24th 2025'   -> target_date='2025-06-24'\n"
    "    AI: 'That date is in the past.'\n"
    "    User: 'sorry I meant 2026'  -> target_date='2026-06-24'\n"
    "       (you take the PREVIOUS attempt and replace just the year)\n"
    "\n"
    "    AI: 'Type B is Priority, Type C is Custom. Which one?'\n"
    "    User: 'priority'  -> request_type='type_b'\n"
    "\n"
    "    AI: 'What's your phone?'\n"
    "    User: 'wait, my email is actually jay@example.com'\n"
    "       -> email='jay@example.com'  (correcting earlier email field)\n"
    "\n"
    "    AI: 'How many people?'\n"
    "    User: 'make that 12 instead of 10'  -> count=12\n"
    "\n"
    "    AI: 'When?'\n"
    "    User: 'next Tuesday'  -> resolve to actual ISO date\n"
    "\n"
    "    User: 'the second one'  (after AI showed a list)\n"
    "       -> the option id of the second item\n"
    "\n"
    "  When the user's message is short or refers back ('I meant X', "
    "  'no, Y', 'change it to Z', 'actually...', 'second one', 'that one'), "
    "  look at what the AI just asked, what value was just rejected (and "
    "  why), and merge the correction into the prior value when the user "
    "  clearly only changed PART of it.\n"
    "\n"
    "WHAT YOU OUTPUT:\n"
    "  A JSON object: {\"extractions\": [ {section_id, field_id, value} ]}\n"
    "  - Include ONLY fields the user clearly provided a value for.\n"
    "  - Clean up STT artifacts: spaces in emails ('j oliver 81 at gmail "
    "    dot com' -> 'joliver81@gmail.com'), spelled-out digits in phones, "
    "    fillers, leading 'um/uh/so' words.\n"
    "  - Resolve dates to ISO-8601 (YYYY-MM-DD) using today's date and "
    "    the conversation context. 'next Tuesday' -> compute it.\n"
    "  - For select/lookup fields, return the option's id. The user may "
    "    refer to an option by ANY of its visible attributes — id, label, "
    "    name, code, alias, *_id columns. Match the user's input against "
    "    every attribute on every option, then return the matched option's "
    "    `id`. If the user typed a number that exactly matches one option's "
    "    id (or any *_id column), that is the answer — return that id. "
    "    Labels also work; the validator does fuzzy matching.\n"
    "  - For boolean, return true/false.\n"
    "  - For number, return a JSON number.\n"
    "  - Otherwise return a string.\n"
    "  - Pure conversational filler ('okay', 'sure', 'thanks', 'huh', "
    "    'what?') with no extractable answer -> {\"extractions\": []}.\n"
    "  - The user is allowed to answer multiple fields at once or out of "
    "    section order; capture them all.\n"
    "  - If the user is correcting a value already collected, extract the "
    "    new value — the system overwrites.\n"
    "\n"
    "Return ONLY the JSON object. No markdown, no commentary."
)


def _compact_field(
    field: Dict[str, Any],
    schema: Dict[str, Any],
    collected_data: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Strip a field def down to what the extractor actually needs."""
    out = {
        'id': field.get('id'),
        'label': field.get('label'),
        'type': field.get('type', 'text'),
    }
    if field.get('required'):
        out['required'] = True
    if field.get('prompt_hint'):
        out['hint'] = field['prompt_hint']
    if field.get('description'):
        out['description'] = field['description']
    if field.get('helpful_context'):
        out['helpful_context'] = field['helpful_context']
    # Examples are gold for the extractor on free-text-ish fields like
    # date / phone / email / number, where the schema author has shown
    # what formats are acceptable. Especially valuable for date fields:
    # passing through ["next Tuesday", "in 10 days", "2026-06-24"] tells
    # the model unambiguously that natural-language dates are expected.
    if field.get('examples'):
        out['examples'] = field['examples'][:6]
    if field.get('validation'):
        out['validation'] = field['validation']
    # Options for select/lookup: cap at 25 to keep tokens down
    options = None
    if field.get('options'):
        options = field['options']
    elif field.get('options_ref'):
        options = get_lookup_values(schema, field['options_ref'], collected_data)
    elif field.get('lookup_ref'):
        options = get_lookup_values(schema, field['lookup_ref'], collected_data)
    if options:
        # Use get_option_id so DB-backed views with `topic_id`, `speaker_id`
        # etc. (no plain `id` column) still surface a real id to the
        # extractor LLM. Also pass through ALL identifying columns the user
        # might mention (id, name, label, code, alias, *_id), so the LLM
        # can match the user's input against any of them — not just label.
        from .validation_engine import get_option_id as _get_id
        compact = []
        for o in options[:25]:
            if isinstance(o, dict):
                entry = {
                    'id': _get_id(o),
                    'label': o.get('label') or o.get('name'),
                }
                # Pass description through so the model can answer
                # "what does option B mean?" and disambiguate similar labels.
                if o.get('description'):
                    entry['description'] = o['description']
                # Pass through any column whose key looks like an
                # identifier the user might reference: code, alias,
                # short_name, abbreviation, sku, *_id, etc. The LLM can
                # then match the user's input ("209", "NeuroAxis",
                # "CMX-XR") against ANY visible column.
                for k, v in o.items():
                    if k in ('id', 'value', 'label', 'name', 'description'):
                        continue
                    if v is None:
                        continue
                    if k.endswith('_id') or k in (
                        'code', 'alias', 'short_name', 'abbreviation',
                        'abbr', 'sku', 'symbol', 'key',
                    ):
                        entry[k] = v
                compact.append(entry)
            else:
                compact.append({'id': o, 'label': str(o)})
        out['options'] = compact
        if len(options) > 25:
            out['options_truncated_total'] = len(options)
    return out


def _build_user_prompt(message: str, schema: Dict[str, Any], session) -> str:
    current_section_id = session.current_section_id
    current_section = get_section(schema, current_section_id) if current_section_id else None

    parts = []

    # Today's date in ISO so the model can resolve "next Tuesday", "in
    # 10 days", etc., and so it can apply min_days_ahead-style validation
    # context to its output even though we still validate server-side.
    from datetime import date as _date
    parts.append(f"TODAY'S DATE: {_date.today().isoformat()}")
    parts.append("")

    # Filter conditionally-hidden fields out so the extractor doesn't
    # try to populate fields that don't currently apply (e.g. a
    # follow-up phone field when the user said they don't want a
    # follow-up). Late import to avoid a circular dep at module load.
    from .validation_engine import is_field_visible as _vis

    def _visible_fields(sec):
        return [
            f for f in (sec.get('fields') or [])
            if _vis(schema, sec.get('id'), f.get('id') or '',
                    session.collected_data or {})
        ]

    if current_section:
        parts.append(f"CURRENT SECTION: {current_section.get('id')} — {current_section.get('title')}")
        parts.append("FIELDS IN CURRENT SECTION (currently applicable only):")
        parts.append(json.dumps(
            [_compact_field(f, schema, session.collected_data) for f in _visible_fields(current_section)],
            indent=2,
        ))

    # Other sections (compact)
    other_sections = []
    for sid in get_section_order(schema):
        if sid == current_section_id:
            continue
        sec = get_section(schema, sid)
        if not sec:
            continue
        other_sections.append({
            'id': sec.get('id'),
            'title': sec.get('title'),
            'fields': [_compact_field(f, schema, session.collected_data) for f in _visible_fields(sec)],
        })
    if other_sections:
        parts.append("OTHER SECTIONS (cross-section answers allowed if user clearly responds out of order):")
        parts.append(json.dumps(other_sections, indent=2))

    parts.append("ALREADY-COLLECTED VALUES (overwrite when the user is correcting):")
    parts.append(json.dumps(session.collected_data or {}, indent=2, default=str))

    # Recent conversation history — THE most important context for
    # short / corrective / contextual user messages. Without this the
    # extractor can't resolve "I meant 2026" -> 2026-06-24, "second one"
    # -> the matching option, "no, make it 12" -> count=12, etc.
    chat_history = getattr(session, 'chat_history', None) or []
    if chat_history:
        # Keep the last 10 turns, lightly truncated
        recent = chat_history[-10:]
        parts.append("")
        parts.append("RECENT CONVERSATION (oldest first, last entry is what came BEFORE the user's current message):")
        history_lines = []
        for turn in recent:
            role = turn.get('role', '?').upper()
            content = (turn.get('content') or '').strip()
            if not content:
                continue
            # Strip the [SYSTEM NOTE — auto-extracted ...] suffix we
            # inject on the user side; it'll just confuse the model.
            split_at = content.find('[SYSTEM NOTE')
            if split_at > 0:
                content = content[:split_at].strip()
            if len(content) > 600:
                content = content[:600] + '…'
            history_lines.append(f"  {role}: {content}")
        parts.append("\n".join(history_lines))

    parts.append("")
    parts.append(f'USER MESSAGE (the one to extract from): "{message}"')
    parts.append("")
    parts.append('Return ONLY: {"extractions": [...]}')
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------
def _call_extractor(message: str, schema: Dict[str, Any], session,
                    timeout_seconds: float = None) -> Optional[List[Dict[str, Any]]]:
    # Hard wall-clock limit. Tunable via env var so admins can tighten or
    # loosen for their deployment/proxy latency.
    if timeout_seconds is None:
        try:
            timeout_seconds = float(os.getenv('DCA_EXTRACTOR_TIMEOUT_SECONDS', '6.0'))
        except ValueError:
            timeout_seconds = 6.0
    logger.info("extractor: _call_extractor entry, timeout=%.1fs message=%r",
                timeout_seconds, message)
    client, model = _get_client_and_model()
    if client is None or model is None:
        logger.warning("extractor: no LLM client/model available — extraction disabled this turn")
        debug_log(session, 'error', {
            'where': 'field_extractor._get_client_and_model',
            'error': 'returned None — check api_keys_config.get_openai_config(use_mini=True)',
        })
        return None

    debug_log(session, 'extract_step', {
        'step': 'client_ready',
        'model': model,
        'api_type': 'azure' if 'AzureOpenAI' in type(client).__name__ else 'openai',
    })

    user_prompt = _build_user_prompt(message, schema, session)
    logger.info("extractor: prompt built, %d chars", len(user_prompt))
    debug_log(session, 'extract_step', {
        'step': 'prompt_built',
        'prompt_chars': len(user_prompt),
        'system_prompt_preview': _SYSTEM[:300],
        'user_prompt_preview': user_prompt[:1500],
    })

    def _call(use_json_format: bool, set_temp: bool, token_param: str):
        kwargs = {
            'model': model,
            'messages': [
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": user_prompt},
            ],
            'timeout': timeout_seconds,
        }
        # Newer reasoning models (gpt-5.x, o1, o3-mini, etc.) reject
        # `max_tokens` and require `max_completion_tokens` instead. We try
        # the new param first and fall back to the legacy one. See:
        # https://platform.openai.com/docs/api-reference/chat
        kwargs[token_param] = 600
        if set_temp:
            kwargs['temperature'] = 0.0
        if use_json_format:
            kwargs['response_format'] = {"type": "json_object"}
        return client.chat.completions.create(**kwargs)

    # Once we know which call shape the deployment accepts (resolved on
    # the first successful call), reuse it. Skip the full variant retry
    # loop on every subsequent call — that's what was making first-fail
    # turns take 30+ seconds and hanging the chat at "Thinking…".
    cfg = _client_cache.get('config_signature')
    cached_shape = (
        _call_shape_cache.get('shape')
        if _call_shape_cache.get('config_signature') == cfg
        else None
    )
    full_variants = [
        # New-style reasoning models (gpt-5.x, o-series): no temp +
        # completion-tokens. These deployments are now most common.
        ('max_completion_tokens', True,  False),
        ('max_completion_tokens', False, False),
        # Legacy models still accept max_tokens.
        ('max_tokens',            True,  True),
        ('max_tokens',            True,  False),
        ('max_tokens',            False, False),
    ]
    if cached_shape:
        # Try the known-good shape only. Saves 4 round-trips on every call.
        variants = [(cached_shape['token_param'], cached_shape['json_format'], cached_shape['set_temp'])]
        logger.info("extractor: using cached call shape: %s", cached_shape)
    else:
        variants = full_variants

    resp = None
    last_err = None
    attempts = []
    for idx, (token_param, use_json_format, set_temp) in enumerate(variants):
        try:
            logger.info("extractor: LLM attempt %d (token_param=%s, json_format=%s, set_temp=%s)",
                        idx + 1, token_param, use_json_format, set_temp)
            resp = _call(use_json_format, set_temp, token_param)
            attempts.append({
                'attempt': idx + 1, 'token_param': token_param,
                'json_format': use_json_format, 'set_temp': set_temp,
                'success': True,
            })
            # Cache the working shape so the next extraction skips the
            # full variant retry loop. This is what makes the chat snappy
            # — first turn pays the discovery cost, every other turn is
            # one round-trip.
            _call_shape_cache['shape'] = {
                'token_param': token_param,
                'json_format': use_json_format,
                'set_temp': set_temp,
            }
            _call_shape_cache['config_signature'] = _client_cache.get('config_signature')
            break
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            attempts.append({
                'attempt': idx + 1, 'token_param': token_param,
                'json_format': use_json_format, 'set_temp': set_temp,
                'success': False, 'error': str(e),
            })
            logger.warning("extractor: attempt %d failed: %s", idx + 1, e)
            if 'unsupported' in msg or 'not supported' in msg or 'invalid' in msg or '400' in msg:
                continue
            break

    debug_log(session, 'extract_step', {
        'step': 'llm_attempts',
        'attempts': attempts,
    })

    if resp is None:
        # If we were using a cached shape and it failed, fall through to
        # the full variant matrix once before giving up. The deployment
        # may have changed.
        if cached_shape:
            logger.warning("extractor: cached shape failed; clearing and retrying with full variant matrix")
            _call_shape_cache['shape'] = None
            _call_shape_cache['config_signature'] = None
            for idx, (token_param, use_json_format, set_temp) in enumerate(full_variants):
                if cached_shape and (token_param, use_json_format, set_temp) == (
                    cached_shape['token_param'], cached_shape['json_format'], cached_shape['set_temp']):
                    continue  # already tried
                try:
                    resp = _call(use_json_format, set_temp, token_param)
                    attempts.append({
                        'attempt': len(attempts) + 1, 'token_param': token_param,
                        'json_format': use_json_format, 'set_temp': set_temp,
                        'success': True, 'fallback': True,
                    })
                    _call_shape_cache['shape'] = {
                        'token_param': token_param,
                        'json_format': use_json_format,
                        'set_temp': set_temp,
                    }
                    _call_shape_cache['config_signature'] = _client_cache.get('config_signature')
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    attempts.append({
                        'attempt': len(attempts) + 1, 'token_param': token_param,
                        'json_format': use_json_format, 'set_temp': set_temp,
                        'success': False, 'error': str(e), 'fallback': True,
                    })

    if resp is None:
        logger.warning("extractor: LLM call failed: %s", last_err)
        debug_log(session, 'error', {
            'where': 'field_extractor.LLM call',
            'error': str(last_err) if last_err else 'unknown',
            'attempts': attempts,
        })
        return None

    content = ''
    try:
        content = (resp.choices[0].message.content or "").strip()
        logger.info("extractor: raw LLM response = %r", content[:500])
        debug_log(session, 'extract_step', {
            'step': 'llm_raw_response',
            'content': content,
        })
        if content.startswith('```'):
            content = content.strip('`')
            if content.lower().startswith('json'):
                content = content[4:].strip()
        data = json.loads(content)
        extractions = data.get('extractions') or []
        if not isinstance(extractions, list):
            logger.warning("extractor: extractions is not a list: %r", extractions)
            debug_log(session, 'error', {
                'where': 'field_extractor.parse',
                'error': 'extractions key is not a list',
                'data': data,
            })
            return None
        logger.info("extractor: parsed %d extractions: %s", len(extractions), extractions)
        return extractions
    except Exception as e:
        logger.warning("extractor: bad JSON: %s — content=%r", e, content)
        debug_log(session, 'error', {
            'where': 'field_extractor.JSON parse',
            'error': str(e),
            'raw_content': content[:1000],
        })
        return None


# ---------------------------------------------------------------------------
# Apply + persist
# ---------------------------------------------------------------------------
def _apply_extraction(extraction: Dict[str, Any], session, schema) -> Dict[str, Any]:
    """
    Coerce + validate + save one extraction. Returns a record describing
    what happened — used for debug events and so the agent can be told.
    """
    section_id = extraction.get('section_id')
    field_id = extraction.get('field_id')
    raw_value = extraction.get('value')

    record = {
        'section_id': section_id,
        'field_id': field_id,
        'raw_value': raw_value,
        'applied': False,
        'final_value': None,
        'error': None,
    }

    if not section_id or not field_id:
        record['error'] = 'missing section_id or field_id'
        return record

    field = get_field(schema, section_id, field_id)
    if not field:
        record['error'] = f'unknown field {section_id}.{field_id}'
        return record

    # Refuse to populate a field whose conditional.show_when currently
    # evaluates false — applying it would put data into a field that
    # doesn't apply to this submission.
    from .validation_engine import is_field_visible as _vis
    if not _vis(schema, section_id, field_id, session.collected_data or {}):
        record['error'] = f'field {section_id}.{field_id} is currently hidden by its show_when condition'
        return record

    field_type = field.get('type', 'text')
    value = raw_value

    # Resolve select/lookup labels to ids using fuzzy matching
    if field_type in ('select', 'lookup') and isinstance(value, str):
        resolved_id, match_err = resolve_select_value(value, schema, field, session.collected_data if session else None)
        if match_err:
            record['error'] = f'select-resolve: {match_err}'
            return record
        if resolved_id is not None:
            value = resolved_id
    elif field_type == 'multi_select':
        resolved_list, match_err = resolve_multi_select_value(value, schema, field, session.collected_data if session else None)
        if match_err:
            record['error'] = f'multi_select-resolve: {match_err}'
            return record
        if resolved_list is not None:
            value = resolved_list

    coerced, coerce_err = coerce_value(value, field_type)
    if coerce_err:
        record['error'] = f'coerce: {coerce_err}'
        return record

    # Validate against the current full data set (with this value tentatively applied)
    tentative = {**(session.collected_data or {})}
    tentative.setdefault(section_id, {})
    tentative[section_id] = {**tentative[section_id], field_id: coerced}

    errors = validate_field(schema, section_id, field_id, coerced, tentative)
    if errors:
        record['error'] = 'validate: ' + ' '.join(errors)
        return record

    # Commit
    session.set_field_value(section_id, field_id, coerced)
    record['applied'] = True
    record['final_value'] = coerced
    return record


def extract_and_save_fields(message: str, session, schema) -> List[Dict[str, Any]]:
    """
    Public entry point. Runs the extractor on `message` against `schema`
    and saves any successful extractions to `session`.

    Returns a list of records describing what happened (for debug + so the
    agent can see what was just saved). Always safe to call — failures
    fall through with an empty list, the agent runs as if there was no
    extraction step at all.

    Wall-clock-bounded: the entire extractor flow is wrapped in a single
    try/except so nothing — proxy hang, malformed schema, anything —
    can stall the chat at "Thinking…".
    """
    if not message or not message.strip():
        return []
    if os.getenv('DCA_DISABLE_EXTRACTION', '').strip().lower() in ('1', 'true', 'yes', 'on'):
        return []

    debug_log(session, 'extract_call', {
        'message': message,
        'current_section': session.current_section_id,
    })

    import time as _t
    started_at = _t.time()
    try:
        extractions = _call_extractor(message, schema, session)
    except Exception as e:
        logger.error("extractor: unexpected exception in _call_extractor: %s", e, exc_info=True)
        debug_log(session, 'error', {
            'where': 'field_extractor.extract_and_save_fields',
            'error': str(e),
            'elapsed_s': round(_t.time() - started_at, 2),
        })
        debug_log(session, 'extract_result', {
            'status': 'exception',
            'error': str(e),
            'records': [],
        })
        return []
    elapsed = round(_t.time() - started_at, 2)
    logger.info("extractor: LLM phase complete in %.2fs", elapsed)
    if extractions is None:
        debug_log(session, 'extract_result', {
            'status': 'llm-unavailable-or-error',
            'elapsed_s': elapsed,
            'records': [],
        })
        return []

    records = []
    any_applied = False
    for ext in extractions:
        rec = _apply_extraction(ext, session, schema)
        records.append(rec)
        if rec.get('applied'):
            any_applied = True

    if any_applied:
        try:
            save_session(session)
        except Exception as e:
            logger.warning("extractor: save_session failed: %s", e)
            debug_log(session, 'error', {
                'where': 'field_extractor.save_session',
                'error': str(e),
            })

    debug_log(session, 'extract_result', {
        'count_returned': len(extractions),
        'count_applied': sum(1 for r in records if r.get('applied')),
        'records': records,
    })
    logger.info(
        "extractor: returned=%d applied=%d records=%s",
        len(extractions),
        sum(1 for r in records if r.get('applied')),
        records,
    )
    return records


# NOTE: The previous `format_extraction_summary_for_agent` helper, which
# produced a "[SYSTEM NOTE — auto-extracted ...]" string injected into the
# agent's input message, has been removed. The extractor now saves
# silently to session.collected_data, and the agent sees the resulting
# state through its DATA COLLECTED SO FAR system-prompt block on the
# very same turn (the agent is constructed AFTER extraction runs). One
# source of truth, no synthetic notes.
