"""
Validation engine for the data collection agent.

Two layers of validation:

1. Per-field validation — runs every time a field is updated. Checks the value
   against the field's declared type and validation rules from the schema.

2. Cross-field/section validation — runs when finishing a section or before
   recap. Checks conditional requirements, section completeness, and any
   business rules expressed in the schema.

This is intentionally schema-driven: every validation rule is declared in the
form schema, not hard-coded. New validation rules are added by extending
RULE_HANDLERS, not by changing per-field call sites.
"""

import re
import logging
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple

from .schema_loader import get_field, get_section, get_lookup_values

logger = logging.getLogger(__name__)


# ---------- Lookup / select value resolution ----------

def get_option_id(opt):
    """Pull the canonical id from an option dict. Tries `id` first,
    then `value`, then any `<something>_id` column (so SQL views can
    keep descriptive primary-key column names like `speaker_id` and
    still work with the lookup machinery without aliasing). Returns
    the raw value (not stringified) or None."""
    if not isinstance(opt, dict):
        return opt
    if 'id' in opt and opt['id'] is not None:
        return opt['id']
    if 'value' in opt and opt['value'] is not None:
        return opt['value']
    for k, v in opt.items():
        if k.endswith('_id') and v is not None:
            return v
    return None


def _option_id_label(opt):
    """Normalize one option to (id, label) regardless of whether it's a dict or scalar.
    Uses get_option_id(), which falls back through id → value → *_id
    so SQL views with descriptive PK column names (speaker_id, venue_id,
    etc.) work without aliasing."""
    if isinstance(opt, dict):
        opt_id = str(get_option_id(opt) or '')
        opt_label = str(opt.get('label', '') or opt.get('name', '') or opt_id)
    else:
        opt_id = str(opt)
        opt_label = str(opt)
    return opt_id, opt_label


def resolve_select_value(
    value: Any, schema: Dict, field: Dict,
    collected_data: Optional[Dict] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Map a free-form input string to a defined option's id for a select / lookup
    / multi_select field.

    Tries (in order, all case-insensitive):
      1. exact match on id
      2. exact match on label
      3. exact match on the leading token of the label (handles "Type B" when
         label is "Type B — Priority")
      4. unique substring match on label or id
      5. single-letter shortcut (e.g. "B" -> "Type B" when no other options
         start with "B")

    Returns:
        (resolved_id, error_message)
        - resolved_id, None: a clean match was found
        - None, error_message: ambiguous — multiple plausible matches
        - None, None: no match found (caller should fall back to normal validation)
    """
    if value is None or value == '':
        return None, None

    # Discover the option universe
    options = []
    if field.get('options'):
        options = field['options']
    elif field.get('options_ref'):
        options = get_lookup_values(schema, field['options_ref'], collected_data)
    elif field.get('lookup_ref'):
        options = get_lookup_values(schema, field['lookup_ref'], collected_data)
    if not options:
        return None, None

    needle = str(value).strip().lower()
    if not needle:
        return None, None

    pairs = [_option_id_label(o) for o in options]

    # 1. exact id
    for opt_id, _ in pairs:
        if opt_id.lower() == needle:
            return opt_id, None

    # 2. exact label
    for opt_id, opt_label in pairs:
        if opt_label.lower() == needle:
            return opt_id, None

    # 3. leading token of label, e.g. "Type B" matching "Type B — Priority"
    for opt_id, opt_label in pairs:
        head = re.split(r'[—–\-\(:]', opt_label, maxsplit=1)[0].strip().lower()
        if head and head == needle:
            return opt_id, None

    # 4. unique substring (in label or id)
    substr_matches = []
    for opt_id, opt_label in pairs:
        if needle in opt_label.lower() or needle in opt_id.lower():
            substr_matches.append((opt_id, opt_label))
    if len(substr_matches) == 1:
        return substr_matches[0][0], None

    # 5. single-letter / abbreviation: e.g. "B" should hit "Type B" if no
    #    other label starts the same letter at the relevant position
    if len(needle) <= 3:
        # Match where the trailing distinguishing token starts with `needle`.
        # For labels like "Type A — Standard", "Type B — Priority", we look
        # at the second word.
        token_matches = []
        for opt_id, opt_label in pairs:
            tokens = [t for t in re.split(r'\s+', opt_label) if t]
            if len(tokens) >= 2 and tokens[1].lower().startswith(needle):
                token_matches.append((opt_id, opt_label))
        if len(token_matches) == 1:
            return token_matches[0][0], None
        # Fallback: first non-trivial token (handles single-token labels like "Active")
        first_tok_matches = []
        for opt_id, opt_label in pairs:
            tokens = [t for t in re.split(r'\s+', opt_label) if t]
            if tokens and tokens[0].lower().startswith(needle):
                first_tok_matches.append((opt_id, opt_label))
        if len(first_tok_matches) == 1:
            return first_tok_matches[0][0], None

    if len(substr_matches) > 1:
        labels = ', '.join(f"'{lbl}'" for _, lbl in substr_matches)
        return None, (
            f"'{value}' could mean any of: {labels}. "
            "Please be more specific."
        )

    return None, None


def resolve_multi_select_value(
    value: Any, schema: Dict, field: Dict,
    collected_data: Optional[Dict] = None,
) -> Tuple[Optional[List[str]], Optional[str]]:
    """
    Like resolve_select_value but for multi_select. Accepts a list, a
    comma/semicolon-separated string, or a single value, and returns a list of
    resolved option ids.
    """
    if value is None or value == '':
        return None, None

    if isinstance(value, list):
        items = [str(v) for v in value]
    else:
        items = [piece.strip() for piece in re.split(r'[,;]', str(value)) if piece.strip()]

    resolved: List[str] = []
    errors: List[str] = []
    for item in items:
        rid, err = resolve_select_value(item, schema, field, collected_data)
        if err:
            errors.append(err)
        elif rid is not None:
            resolved.append(rid)
        else:
            # No match — preserve the raw value for normal validation to flag
            resolved.append(item)
    if errors:
        return None, ' '.join(errors)
    return resolved, None


# ---------- Voice-input normalization ----------
#
# Speech-to-text adds whitespace, punctuation noise, disfluency artifacts,
# and homophone substitutions that text inputs don't have. We used to handle
# this with field-type-specific regex rules ("at"→"@", strip whitespace in
# emails, spelled-out digits in phones, etc.) — that approach was brittle
# and fragile, and it could only ever cover patterns we'd thought of in
# advance.
#
# The current implementation lives in `voice_normalizer.py` and delegates to
# a small, fast LLM (gpt-4o-mini class) that takes the raw transcript +
# field schema and returns the cleaned value. It generalizes naturally to
# any field type without us enumerating rules.
#
# This module no longer owns voice normalization. See `voice_normalizer.py`.

# ---------- Type coercion ----------

def coerce_value(value: Any, field_type: str) -> Tuple[Any, Optional[str]]:
    """
    Coerce an incoming value (which is often a string from chat) to the
    declared field type. Returns (coerced_value, error_message).
    """
    if value is None or value == '':
        return None, None

    try:
        if field_type in ('text', 'textarea', 'email', 'phone'):
            return str(value).strip(), None

        if field_type == 'number':
            if isinstance(value, (int, float)):
                return value, None
            s = str(value).strip().replace(',', '')
            if '.' in s:
                return float(s), None
            return int(s), None

        if field_type == 'date':
            if isinstance(value, date):
                return value.isoformat(), None
            s = str(value).strip()
            # Try common formats; final stored form is ISO YYYY-MM-DD
            for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m-%d-%Y', '%d/%m/%Y',
                        '%B %d, %Y', '%b %d, %Y', '%B %d %Y', '%b %d %Y'):
                try:
                    return datetime.strptime(s, fmt).date().isoformat(), None
                except ValueError:
                    continue
            return None, f"Could not parse '{value}' as a date. Try YYYY-MM-DD."

        if field_type == 'boolean':
            if isinstance(value, bool):
                return value, None
            s = str(value).strip().lower()
            if s in ('true', 'yes', 'y', '1', 'on'):
                return True, None
            if s in ('false', 'no', 'n', '0', 'off'):
                return False, None
            return None, f"Could not interpret '{value}' as yes/no."

        if field_type == 'select':
            return str(value).strip(), None

        if field_type == 'multi_select':
            if isinstance(value, list):
                return [str(v).strip() for v in value], None
            # Allow comma-separated strings from chat
            return [v.strip() for v in str(value).split(',') if v.strip()], None

        if field_type == 'lookup':
            # Stored as the lookup item's id (a string)
            return str(value).strip(), None

        # Unknown type — store as-is
        return value, None
    except (TypeError, ValueError) as e:
        return None, f"Invalid value for {field_type}: {e}"


# ---------- Rule handlers ----------

def _rule_required(value, _params, _field, _all_data) -> Optional[str]:
    if value is None or value == '' or value == []:
        return "This field is required."
    return None


def _rule_future_date(value, params, _field, _all_data) -> Optional[str]:
    if not value:
        return None
    try:
        d = datetime.strptime(value, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return f"Could not interpret '{value}' as a date."
    today = date.today()
    if d <= today:
        return "This date must be in the future."
    min_days = (params or {}).get('min_days_ahead', 0)
    if min_days and (d - today).days < min_days:
        return f"This date must be at least {min_days} days from today."
    return None


def _rule_min_max(value, params, field, _all_data) -> Optional[str]:
    if value is None:
        return None
    try:
        n = float(value)
    except (ValueError, TypeError):
        return None  # type errors caught elsewhere
    p = params or field.get('validation', {}) or {}
    mn = p.get('min')
    mx = p.get('max')
    if mn is not None and n < mn:
        return f"Value must be at least {mn}."
    if mx is not None and n > mx:
        return f"Value must be at most {mx}."
    return None


def _rule_min_max_length(value, params, field, _all_data) -> Optional[str]:
    if value is None or value == '':
        return None
    p = params or field.get('validation', {}) or {}
    s = str(value)
    mn = p.get('min_length')
    mx = p.get('max_length')
    if mn is not None and len(s) < mn:
        return f"Must be at least {mn} characters."
    if mx is not None and len(s) > mx:
        return f"Must be no more than {mx} characters."
    return None


def _rule_pattern(value, params, field, _all_data) -> Optional[str]:
    if value is None or value == '':
        return None
    p = params or field.get('validation', {}) or {}
    pattern = p.get('pattern')
    if not pattern:
        return None
    try:
        if not re.match(pattern, str(value)):
            return p.get('pattern_message') or f"Does not match required format."
    except re.error as e:
        logger.warning(f"Invalid regex in field {field.get('id')}: {e}")
        return None
    return None


def _rule_email(value, _params, _field, _all_data) -> Optional[str]:
    if not value:
        return None
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', str(value)):
        return "Please provide a valid email address."
    return None


def _rule_phone(value, _params, _field, _all_data) -> Optional[str]:
    if not value:
        return None
    digits = re.sub(r'\D', '', str(value))
    if len(digits) < 7 or len(digits) > 15:
        return "Please provide a valid phone number."
    return None


def _rule_in_list(value, params, field, _all_data) -> Optional[str]:
    if value is None or value == '':
        return None
    options = (params or {}).get('options') or field.get('options') or []
    # Build the set of accepted forms. For DB-backed lookups the row dict
    # has no `id` key — it has e.g. `product_id`, `speaker_id` — so we
    # resolve the canonical id via get_option_id (id → value → *_id).
    # We also accept the label and any *_id column as an alternate form,
    # all case-insensitive, so an upstream resolver that returned a
    # label rather than an id still validates cleanly.
    valid_ids: List[str] = []
    accepted_lower: set = set()
    for o in options:
        if isinstance(o, dict):
            opt_id = get_option_id(o)
            if opt_id is not None:
                valid_ids.append(str(opt_id))
                accepted_lower.add(str(opt_id).lower())
            label = o.get('label') or o.get('name')
            if label is not None:
                accepted_lower.add(str(label).lower())
            # Any other *_id column (rare, but be permissive)
            for k, v in o.items():
                if k.endswith('_id') and v is not None:
                    accepted_lower.add(str(v).lower())
        else:
            valid_ids.append(str(o))
            accepted_lower.add(str(o).lower())
    if str(value).lower() not in accepted_lower:
        # Show the canonical ids (or label fallbacks) in the error so the
        # message is meaningful.
        display = valid_ids if valid_ids else sorted(accepted_lower)
        return f"Must be one of: {', '.join(display)}."
    return None


# Registry of rule handlers — extend this to add new validation rules
RULE_HANDLERS = {
    'required': _rule_required,
    'future_date': _rule_future_date,
    'min_max': _rule_min_max,
    'min_max_length': _rule_min_max_length,
    'pattern': _rule_pattern,
    'email': _rule_email,
    'phone': _rule_phone,
    'in_list': _rule_in_list,
}


# ---------- Public API ----------

def validate_field(schema: Dict, section_id: str, field_id: str, value: Any,
                   all_data: Optional[Dict[str, Dict]] = None) -> List[str]:
    """
    Validate a single field value. Returns a list of error messages (empty if valid).

    `all_data` is the full session.collected_data dict, used for cross-field rules
    like conditional requirements. Pass it when available.
    """
    field = get_field(schema, section_id, field_id)
    if not field:
        return [f"Unknown field: {section_id}.{field_id}"]

    errors: List[str] = []
    field_type = field.get('type', 'text')
    is_required = bool(field.get('required'))

    # Conditional visibility — if not visible, skip required/format checks
    if not _is_field_visible(field, all_data or {}):
        return []

    # Required check
    if is_required:
        err = _rule_required(value, None, field, all_data)
        if err:
            errors.append(err)
            return errors  # No point in further checks if missing

    # If empty and not required, skip remaining checks
    if value is None or value == '' or value == []:
        return errors

    # Type-implied validators (email and phone fields auto-apply their format)
    if field_type == 'email':
        err = _rule_email(value, None, field, all_data)
        if err:
            errors.append(err)

    if field_type == 'phone':
        err = _rule_phone(value, None, field, all_data)
        if err:
            errors.append(err)

    # Apply validation block
    validation = field.get('validation') or {}
    rule = validation.get('rule')
    if rule and rule in RULE_HANDLERS:
        err = RULE_HANDLERS[rule](value, validation, field, all_data)
        if err:
            errors.append(err)

    # Numeric min/max (declared inline, no rule keyword needed)
    if field_type == 'number':
        err = _rule_min_max(value, validation, field, all_data)
        if err:
            errors.append(err)

    # String length (declared inline)
    if field_type in ('text', 'textarea'):
        err = _rule_min_max_length(value, validation, field, all_data)
        if err:
            errors.append(err)

    # Pattern (declared inline if present)
    if validation.get('pattern'):
        err = _rule_pattern(value, validation, field, all_data)
        if err:
            errors.append(err)

    # in_list for select / lookup — verify value matches an option
    if field_type == 'select':
        # Inline options
        if field.get('options'):
            err = _rule_in_list(value, {'options': field['options']}, field, all_data)
            if err:
                errors.append(err)
        # Lookup-backed options
        elif field.get('options_ref'):
            options = get_lookup_values(schema, field['options_ref'], all_data)
            err = _rule_in_list(value, {'options': options}, field, all_data)
            if err:
                errors.append(err)

    if field_type == 'lookup' and field.get('lookup_ref'):
        options = get_lookup_values(schema, field['lookup_ref'], all_data)
        err = _rule_in_list(value, {'options': options}, field, all_data)
        if err:
            errors.append(err)

    return errors


def validate_section(schema: Dict, section_id: str,
                     all_data: Dict[str, Dict]) -> Dict[str, List[str]]:
    """
    Validate every field in a section against the current data.

    Returns: {field_id: [error_messages, ...]} — only includes fields with errors.
    """
    section = get_section(schema, section_id)
    if not section:
        return {}

    out: Dict[str, List[str]] = {}
    section_data = all_data.get(section_id, {}) or {}

    for fld in section.get('fields', []):
        fid = fld.get('id')
        if not fid:
            continue
        value = section_data.get(fid)
        errs = validate_field(schema, section_id, fid, value, all_data)
        if errs:
            out[fid] = errs

    return out


def validate_all(schema: Dict, all_data: Dict[str, Dict]) -> Dict[str, Dict[str, List[str]]]:
    """
    Validate every field in every section. Returns nested dict:
    {section_id: {field_id: [error_messages, ...]}, ...}
    """
    out: Dict[str, Dict[str, List[str]]] = {}
    for section in schema.get('sections', []):
        sid = section.get('id')
        if not sid:
            continue
        section_errors = validate_section(schema, sid, all_data)
        if section_errors:
            out[sid] = section_errors
    return out


def is_section_complete(schema: Dict, section_id: str,
                        all_data: Dict[str, Dict]) -> bool:
    """
    A section is complete when all its visible required fields have non-empty
    values AND no field has validation errors.
    """
    section = get_section(schema, section_id)
    if not section:
        return False
    section_data = all_data.get(section_id, {}) or {}
    for fld in section.get('fields', []):
        fid = fld.get('id')
        if not fid:
            continue
        if not _is_field_visible(fld, all_data):
            continue
        value = section_data.get(fid)
        if fld.get('required') and (value is None or value == '' or value == []):
            return False
        errs = validate_field(schema, section_id, fid, value, all_data)
        if errs:
            return False
    return True


def get_missing_required_fields(schema: Dict, section_id: str,
                                all_data: Dict[str, Dict]) -> List[Dict]:
    """
    Return the list of required fields in a section that are still empty.
    Each entry is the field dict from the schema (so callers can read label, prompt_hint, etc.).
    """
    section = get_section(schema, section_id)
    if not section:
        return []
    section_data = all_data.get(section_id, {}) or {}
    out = []
    for fld in section.get('fields', []):
        if not fld.get('required'):
            continue
        if not _is_field_visible(fld, all_data):
            continue
        value = section_data.get(fld.get('id'))
        if value is None or value == '' or value == []:
            out.append(fld)
    return out


# ---------- Conditional visibility ----------

def is_field_visible(schema: Dict, section_id: str, field_id: str,
                     all_data: Dict[str, Dict]) -> bool:
    """Public helper: evaluate a field's `conditional.show_when` rule
    against the collected data and return whether the field should
    currently be presented to the user.

    Used by the agent prompt builder, field extractor, recap, and
    frontend progress panel so they all agree on which fields apply
    given the current answers.
    """
    from .schema_loader import get_field as _get_field
    field = _get_field(schema, section_id, field_id)
    if not field:
        return False
    return _is_field_visible(field, all_data or {})


def visible_fields_by_section(schema: Dict, all_data: Dict[str, Dict]) -> Dict[str, list]:
    """Return {section_id: [field_id, ...]} of currently-visible fields.
    Sections include only field ids whose conditionals evaluate true
    given `all_data`. Convenient for shipping to the frontend so it
    can render the progress panel correctly without re-implementing
    conditional logic in JS."""
    from .schema_loader import get_section_order as _order
    out = {}
    for sid in _order(schema):
        section = next(
            (s for s in (schema.get('sections') or []) if s.get('id') == sid),
            None,
        )
        if not section:
            continue
        ids = []
        for fld in section.get('fields', []):
            fid = fld.get('id')
            if not fid:
                continue
            if _is_field_visible(fld, all_data or {}):
                ids.append(fid)
        out[sid] = ids
    return out


def _is_field_visible(field: Dict, all_data: Dict[str, Dict]) -> bool:
    """
    Evaluate a field's `conditional.show_when` rule against the collected data.
    If no condition is set, the field is visible by default.

    Schema:
        "conditional": {
            "show_when": {
                "field": "some_field_id",     # bare field_id searches all sections
                "section": "optional_section_id",  # optional, scopes the lookup
                "operator": "==" | "!=" | ">" | "<" | ">=" | "<=" | "in" | "not_in",
                "value": <any>
            }
        }
    """
    cond = field.get('conditional') or {}
    show_when = cond.get('show_when')
    if not show_when:
        return True

    target_field_id = show_when.get('field')
    target_section_id = show_when.get('section')
    operator = show_when.get('operator', '==')
    expected = show_when.get('value')

    actual = _find_field_value(all_data, target_field_id, target_section_id)

    try:
        if operator == '==':
            return actual == expected
        if operator == '!=':
            return actual != expected
        if operator == '>':
            return actual is not None and actual > expected
        if operator == '<':
            return actual is not None and actual < expected
        if operator == '>=':
            return actual is not None and actual >= expected
        if operator == '<=':
            return actual is not None and actual <= expected
        if operator == 'in':
            return actual in (expected or [])
        if operator == 'not_in':
            return actual not in (expected or [])
    except TypeError:
        return True  # If comparison fails, default to visible (safer)
    return True


def _find_field_value(all_data: Dict[str, Dict], field_id: str,
                      section_id: Optional[str] = None) -> Any:
    """Look up a field's value, optionally within a specific section."""
    if section_id:
        return (all_data.get(section_id) or {}).get(field_id)
    # Search all sections; first match wins
    for sec_data in all_data.values():
        if isinstance(sec_data, dict) and field_id in sec_data:
            return sec_data[field_id]
    return None
