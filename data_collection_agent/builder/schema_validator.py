"""
Schema validator for the data collection agent.

Run by the builder wizard at every Save. Surfaces specific, actionable errors
so the wizard UI can highlight issues. Distinct from `validation_engine.py`
(which validates user data at runtime).

Returns a structured result:
  {
    'valid': bool,
    'errors': [ {path, code, message}, ... ],
    'warnings': [ {path, code, message}, ... ]
  }
"""

import re
from typing import Any, Dict, List, Optional, Set

# Action handlers know how to validate their own configs — pull from registry
try:
    from ..actions import ActionRegistry
except ImportError:
    ActionRegistry = None


VALID_FIELD_TYPES = {
    'text', 'textarea', 'number', 'date', 'boolean',
    'select', 'multi_select', 'lookup',
    'email', 'phone', 'file',
}

VALID_OPERATORS = {'==', '!=', '>', '<', '>=', '<=', 'in', 'not_in'}

ID_RE = re.compile(r'^[a-z][a-z0-9_]*$', re.IGNORECASE)
TEMPLATE_RE = re.compile(r'\{\{\s*([^{}]+?)\s*\}\}')

# Special tokens that always resolve, even when no field with that name exists
SPECIAL_TEMPLATE_TOKENS = {
    '__all_data__', '__summary__', '__session_id__',
    '__user_id__', '__timestamp__', '__config_id__',
}


def validate_schema(schema: Dict, *, existing_ids: Optional[Set[str]] = None,
                    is_new: bool = False) -> Dict:
    """
    Run all validation checks over a schema.

    Args:
        schema: the schema dict
        existing_ids: set of already-saved schema ids (used to detect duplicates
                      when creating a new schema)
        is_new: if True, treat the schema's id as new and ensure it's not in
                existing_ids
    """
    errors: List[Dict] = []
    warnings: List[Dict] = []

    if not isinstance(schema, dict):
        return {
            'valid': False,
            'errors': [{'path': '', 'code': 'NOT_A_DICT', 'message': 'Schema must be a JSON object.'}],
            'warnings': [],
        }

    # ---------- Top-level required fields ----------
    schema_id = schema.get('id')
    if not schema_id:
        errors.append({'path': 'id', 'code': 'REQUIRED', 'message': 'Schema id is required.'})
    elif not ID_RE.match(schema_id):
        errors.append({
            'path': 'id', 'code': 'INVALID_ID',
            'message': "Schema id must start with a letter and contain only letters, digits, and underscores.",
        })
    elif is_new and existing_ids and schema_id in existing_ids:
        errors.append({
            'path': 'id', 'code': 'DUPLICATE_ID',
            'message': f"A schema with id '{schema_id}' already exists.",
        })

    if not schema.get('name'):
        errors.append({'path': 'name', 'code': 'REQUIRED', 'message': 'Schema name is required.'})

    sections = schema.get('sections') or []
    if not sections:
        errors.append({
            'path': 'sections', 'code': 'REQUIRED',
            'message': 'At least one section is required.',
        })

    # ---------- Optional `requires_secure_context` flag ----------
    rsc = schema.get('requires_secure_context')
    if rsc is not None and not isinstance(rsc, bool):
        errors.append({
            'path': 'requires_secure_context',
            'code': 'BAD_TYPE',
            'message': "'requires_secure_context' must be true or false (or omitted).",
        })

    # ---------- Optional branding block ----------
    branding = schema.get('branding')
    if branding is not None:
        if not isinstance(branding, dict):
            errors.append({
                'path': 'branding', 'code': 'BAD_BRANDING',
                'message': "'branding' must be an object (or omitted).",
            })
        else:
            allowed_branding_keys = {
                'display_name', 'logo_url', 'primary_color', 'accent_color',
                'font_family', 'footer_text', 'favicon_url', 'support_url',
            }
            hex_re = re.compile(r'^#[0-9A-Fa-f]{3,8}$')
            for k, v in branding.items():
                if k not in allowed_branding_keys:
                    warnings.append({
                        'path': f'branding.{k}', 'code': 'UNKNOWN_BRANDING_KEY',
                        'message': f"Unknown branding key '{k}' will be ignored. "
                                   f"Allowed: {sorted(allowed_branding_keys)}.",
                    })
                    continue
                if v is None or v == '':
                    continue
                if k in ('primary_color', 'accent_color') and not hex_re.match(str(v)):
                    errors.append({
                        'path': f'branding.{k}', 'code': 'BAD_COLOR',
                        'message': f"'{k}' must be a hex color like '#06b6d4'. Got: {v!r}.",
                    })
                if k in ('logo_url', 'favicon_url', 'support_url'):
                    s = str(v).strip().lower()
                    if s.startswith('javascript:'):
                        errors.append({
                            'path': f'branding.{k}', 'code': 'BAD_URL',
                            'message': f"'{k}' rejects javascript: URLs.",
                        })

    completion = schema.get('completion') or {}
    actions = completion.get('actions') or []
    if not actions:
        warnings.append({
            'path': 'completion.actions', 'code': 'NO_ACTIONS',
            'message': 'No completion actions defined — collected data will not go anywhere.',
        })

    # ---------- Section validation ----------
    section_ids: Set[str] = set()
    field_ids_by_section: Dict[str, Set[str]] = {}
    all_field_ids: Set[str] = set()

    for idx, section in enumerate(sections):
        path_prefix = f'sections[{idx}]'
        sid = section.get('id')
        if not sid:
            errors.append({'path': f'{path_prefix}.id', 'code': 'REQUIRED', 'message': 'Section id is required.'})
            continue
        if not ID_RE.match(sid):
            errors.append({
                'path': f'{path_prefix}.id', 'code': 'INVALID_ID',
                'message': f"Section id '{sid}' must start with a letter and use only letters, digits, underscores.",
            })
        if sid in section_ids:
            errors.append({
                'path': f'{path_prefix}.id', 'code': 'DUPLICATE_SECTION_ID',
                'message': f"Duplicate section id: '{sid}'.",
            })
        section_ids.add(sid)
        field_ids_by_section[sid] = set()

        if not section.get('title'):
            warnings.append({
                'path': f'{path_prefix}.title', 'code': 'NO_TITLE',
                'message': f"Section '{sid}' has no title — the UI will fall back to the id.",
            })

        fields = section.get('fields') or []
        if not fields:
            warnings.append({
                'path': f'{path_prefix}.fields', 'code': 'NO_FIELDS',
                'message': f"Section '{sid}' has no fields.",
            })
        for fidx, field in enumerate(fields):
            fpath = f'{path_prefix}.fields[{fidx}]'
            _validate_field(field, fpath, sid, schema,
                            errors, warnings,
                            section_field_ids=field_ids_by_section[sid],
                            all_field_ids=all_field_ids)

    # ---------- depends_on cross-section check ----------
    for idx, section in enumerate(sections):
        path = f'sections[{idx}].depends_on'
        depends = section.get('depends_on')
        if not depends:
            continue
        target = depends.get('section')
        if target and target not in section_ids:
            errors.append({
                'path': f'{path}.section', 'code': 'BAD_REFERENCE',
                'message': f"depends_on references unknown section: '{target}'.",
            })

    # ---------- Lookup data validation ----------
    lookup_data = schema.get('lookup_data') or {}
    valid_lookup_refs = set(lookup_data.keys())
    for ref, lookup in lookup_data.items():
        path = f'lookup_data.{ref}'
        if not isinstance(lookup, dict):
            errors.append({
                'path': path, 'code': 'BAD_LOOKUP',
                'message': f"Lookup '{ref}' must be an object with 'source' and 'values'/'file'.",
            })
            continue
        source = lookup.get('source', 'inline')
        if source not in ('inline', 'file', 'database'):
            errors.append({
                'path': f'{path}.source', 'code': 'BAD_LOOKUP_SOURCE',
                'message': f"Lookup '{ref}' has invalid source '{source}' "
                           f"(must be 'inline', 'file', or 'database').",
            })
        if source == 'inline' and not lookup.get('values'):
            warnings.append({
                'path': f'{path}.values', 'code': 'EMPTY_LOOKUP',
                'message': f"Inline lookup '{ref}' has no values.",
            })
        if source == 'file' and not lookup.get('file'):
            errors.append({
                'path': f'{path}.file', 'code': 'BAD_LOOKUP_FILE',
                'message': f"File-sourced lookup '{ref}' must specify a 'file' name.",
            })
        if source == 'database':
            # Validate the structural pieces of a DB-backed lookup. Runtime
            # query is checked at execution time; here we just catch
            # obvious config mistakes.
            import re as _re_id
            ident = _re_id.compile(r'^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$')
            if not lookup.get('connection_id'):
                errors.append({
                    'path': f'{path}.connection_id', 'code': 'BAD_LOOKUP_CONN',
                    'message': f"Database lookup '{ref}' must specify a 'connection_id' "
                               f"(picked from the platform's saved connections).",
                })
            view = lookup.get('view') or ''
            if not view:
                errors.append({
                    'path': f'{path}.view', 'code': 'BAD_LOOKUP_VIEW',
                    'message': f"Database lookup '{ref}' must specify a 'view' (table or view name).",
                })
            elif not ident.match(view):
                errors.append({
                    'path': f'{path}.view', 'code': 'UNSAFE_VIEW',
                    'message': f"Database lookup '{ref}' view name '{view}' is not a valid "
                               f"identifier (use schema.name or name only; no inline SQL).",
                })
            cols = lookup.get('select_columns') or []
            if not isinstance(cols, list) or not cols:
                errors.append({
                    'path': f'{path}.select_columns', 'code': 'BAD_LOOKUP_COLUMNS',
                    'message': f"Database lookup '{ref}' must list 'select_columns' "
                               f"(the privacy allowlist; columns not listed are never queried).",
                })
            else:
                col_ident = _re_id.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
                for c in cols:
                    if not isinstance(c, str) or not col_ident.match(c):
                        errors.append({
                            'path': f'{path}.select_columns', 'code': 'UNSAFE_COLUMN',
                            'message': f"Database lookup '{ref}' has unsafe column name {c!r}.",
                        })
                        break
            fb = lookup.get('filter_by')
            if fb is not None and not isinstance(fb, dict):
                errors.append({
                    'path': f'{path}.filter_by', 'code': 'BAD_LOOKUP_FILTER',
                    'message': f"Database lookup '{ref}' filter_by must be an object (key→value).",
                })
            ob = lookup.get('order_by')
            if ob is not None and not isinstance(ob, list):
                errors.append({
                    'path': f'{path}.order_by', 'code': 'BAD_LOOKUP_ORDER',
                    'message': f"Database lookup '{ref}' order_by must be an array of "
                               f"{{column, direction}} objects.",
                })

    # ---------- Custom tools (schema-level allowlist) ----------
    custom_tools = schema.get('custom_tools')
    if custom_tools is not None:
        if not isinstance(custom_tools, list):
            errors.append({
                'path': 'custom_tools', 'code': 'BAD_CUSTOM_TOOLS',
                'message': "schema.custom_tools must be a JSON array of platform tool names.",
            })
        else:
            for tname in custom_tools:
                if not isinstance(tname, str) or not tname.strip():
                    errors.append({
                        'path': 'custom_tools', 'code': 'BAD_CUSTOM_TOOL_NAME',
                        'message': f"custom_tools entry must be a non-empty string, got {tname!r}.",
                    })

    # ---------- Field references to lookups ----------
    for idx, section in enumerate(sections):
        for fidx, field in enumerate(section.get('fields') or []):
            fpath = f'sections[{idx}].fields[{fidx}]'
            ref = field.get('options_ref') or field.get('lookup_ref')
            if ref and ref not in valid_lookup_refs:
                errors.append({
                    'path': f'{fpath}.{"options_ref" if field.get("options_ref") else "lookup_ref"}',
                    'code': 'BAD_LOOKUP_REF',
                    'message': f"Field '{field.get('id')}' references unknown lookup: '{ref}'.",
                })

    # ---------- Conditional show_when references ----------
    for idx, section in enumerate(sections):
        for fidx, field in enumerate(section.get('fields') or []):
            cond = (field.get('conditional') or {}).get('show_when')
            if not cond:
                continue
            cond_path = f'sections[{idx}].fields[{fidx}].conditional.show_when'
            target_field = cond.get('field')
            target_section = cond.get('section')
            operator = cond.get('operator', '==')
            if operator not in VALID_OPERATORS:
                errors.append({
                    'path': f'{cond_path}.operator', 'code': 'BAD_OPERATOR',
                    'message': f"Unknown operator '{operator}'. Use one of: {sorted(VALID_OPERATORS)}.",
                })
            if not target_field:
                errors.append({
                    'path': f'{cond_path}.field', 'code': 'REQUIRED',
                    'message': "show_when must reference a 'field'.",
                })
            else:
                if target_section:
                    if target_section not in field_ids_by_section:
                        errors.append({
                            'path': f'{cond_path}.section', 'code': 'BAD_REFERENCE',
                            'message': f"show_when.section references unknown section '{target_section}'.",
                        })
                    elif target_field not in field_ids_by_section.get(target_section, set()):
                        errors.append({
                            'path': f'{cond_path}.field', 'code': 'BAD_REFERENCE',
                            'message': f"show_when.field '{target_field}' not found in section '{target_section}'.",
                        })
                else:
                    if target_field not in all_field_ids:
                        errors.append({
                            'path': f'{cond_path}.field', 'code': 'BAD_REFERENCE',
                            'message': f"show_when references unknown field '{target_field}'.",
                        })

    # ---------- Completion actions validation ----------
    for idx, action in enumerate(actions):
        apath = f'completion.actions[{idx}]'
        if not isinstance(action, dict):
            errors.append({'path': apath, 'code': 'BAD_ACTION', 'message': 'Action must be an object.'})
            continue
        atype = action.get('type')
        if not atype:
            errors.append({'path': f'{apath}.type', 'code': 'REQUIRED', 'message': 'Action type is required.'})
            continue
        if ActionRegistry:
            handler_cls = ActionRegistry.get(atype)
            if handler_cls is None:
                errors.append({
                    'path': f'{apath}.type', 'code': 'UNKNOWN_ACTION_TYPE',
                    'message': f"Unknown action type '{atype}'. "
                               f"Available: {sorted(ActionRegistry.list_types())}.",
                })
            else:
                # Per-action validation
                try:
                    handler = handler_cls()
                    sub_errors = handler.validate_config(action) or []
                    for msg in sub_errors:
                        errors.append({'path': apath, 'code': 'BAD_ACTION_CONFIG', 'message': msg})
                except Exception as e:
                    errors.append({
                        'path': apath, 'code': 'ACTION_VALIDATE_FAILED',
                        'message': f"Could not validate action: {e}",
                    })

        # Walk all string config values for {{...}} template references that
        # don't resolve to a defined field (or special token).
        bad_tokens = _check_templates(action, all_field_ids)
        for token in bad_tokens:
            warnings.append({
                'path': apath, 'code': 'UNRESOLVED_TEMPLATE',
                'message': f"Action references '{{{{{token}}}}}' which is not a defined field.",
            })

    return {
        'valid': not errors,
        'errors': errors,
        'warnings': warnings,
    }


def _validate_field(field: Dict, path: str, section_id: str, schema: Dict,
                    errors: List[Dict], warnings: List[Dict], *,
                    section_field_ids: Set[str], all_field_ids: Set[str]):
    fid = field.get('id')
    if not fid:
        errors.append({'path': f'{path}.id', 'code': 'REQUIRED', 'message': 'Field id is required.'})
        return
    if not ID_RE.match(fid):
        errors.append({
            'path': f'{path}.id', 'code': 'INVALID_ID',
            'message': f"Field id '{fid}' must start with a letter and use only letters, digits, underscores.",
        })
    if fid in section_field_ids:
        errors.append({
            'path': f'{path}.id', 'code': 'DUPLICATE_FIELD_ID',
            'message': f"Field id '{fid}' is duplicated in section '{section_id}'.",
        })
    section_field_ids.add(fid)
    all_field_ids.add(fid)

    if not field.get('label'):
        warnings.append({
            'path': f'{path}.label', 'code': 'NO_LABEL',
            'message': f"Field '{fid}' has no label.",
        })

    ftype = field.get('type')
    if not ftype:
        errors.append({'path': f'{path}.type', 'code': 'REQUIRED', 'message': 'Field type is required.'})
    elif ftype not in VALID_FIELD_TYPES:
        errors.append({
            'path': f'{path}.type', 'code': 'BAD_TYPE',
            'message': f"Unknown field type '{ftype}'. Valid types: {sorted(VALID_FIELD_TYPES)}.",
        })

    # select must have either inline options or options_ref
    if ftype == 'select':
        if not field.get('options') and not field.get('options_ref'):
            errors.append({
                'path': path, 'code': 'SELECT_NO_OPTIONS',
                'message': f"Select field '{fid}' must have 'options' or 'options_ref'.",
            })
    if ftype == 'lookup' and not field.get('lookup_ref'):
        errors.append({
            'path': f'{path}.lookup_ref', 'code': 'REQUIRED',
            'message': f"Lookup field '{fid}' must specify 'lookup_ref'.",
        })


def _check_templates(value: Any, valid_field_ids: Set[str]) -> List[str]:
    """
    Recursively scan a config value for {{...}} placeholders and return any
    that don't resolve to a defined field or special token.
    """
    bad: List[str] = []

    def walk(v):
        if isinstance(v, str):
            for m in TEMPLATE_RE.findall(v):
                token = m.strip()
                if token in SPECIAL_TEMPLATE_TOKENS:
                    continue
                if token.startswith('__secret:') and token.endswith('__'):
                    continue
                # Allow dot-paths — first segment is what we check
                head = token.split('.', 1)[0]
                if head not in valid_field_ids:
                    bad.append(token)
        elif isinstance(v, dict):
            for vv in v.values():
                walk(vv)
        elif isinstance(v, list):
            for vv in v:
                walk(vv)

    walk(value)
    return bad
