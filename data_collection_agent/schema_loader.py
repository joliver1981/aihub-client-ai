"""
Schema loader for the data collection agent.

Loads form schemas from JSON files in `data_collection_agent/configs/`, resolves
lookup data references (inline or file-based), and provides a clean dict interface
to consumers (the agent, routes, validation engine, action handlers).

Schema validation lives in `builder/schema_validator.py` — this module focuses on
loading and resolving references for runtime use.
"""

import os
import json
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Configs directory — schemas + lookup data files
CONFIGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'configs')


def _ensure_configs_dir():
    """Ensure the configs directory exists."""
    if not os.path.exists(CONFIGS_DIR):
        os.makedirs(CONFIGS_DIR, exist_ok=True)


def get_schema_path(config_id: str) -> str:
    """Get the absolute filesystem path for a schema config."""
    # Sanitize: only allow alphanumeric, underscore, hyphen
    safe_id = ''.join(c for c in config_id if c.isalnum() or c in '_-')
    return os.path.join(CONFIGS_DIR, f"{safe_id}.json")


def load_schema(config_id: str, resolve_lookups: bool = True) -> Optional[Dict]:
    """
    Load a form schema by ID.

    Args:
        config_id: The schema's unique ID (matches the filename without .json).
        resolve_lookups: If True, resolves file-based lookup data into inline values.
                         If False, leaves lookup_data as-is for editing in the wizard.

    Returns:
        The schema dict, or None if not found / invalid.
    """
    _ensure_configs_dir()
    path = get_schema_path(config_id)
    if not os.path.exists(path):
        logger.warning(f"Schema not found: {config_id} at {path}")
        return None

    try:
        with open(path, 'r', encoding='utf-8') as f:
            schema = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in schema {config_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error loading schema {config_id}: {e}")
        return None

    if resolve_lookups:
        schema = _resolve_lookup_data(schema)

    return schema


def save_schema(config_id: str, schema: Dict) -> Tuple[bool, Optional[str]]:
    """
    Save a schema to disk. Returns (success, error_message).

    The schema_validator should be called before this to surface validation errors.
    """
    _ensure_configs_dir()
    path = get_schema_path(config_id)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(schema, f, indent=2, default=str)
        logger.info(f"Saved schema {config_id} to {path}")
        return True, None
    except Exception as e:
        logger.error(f"Error saving schema {config_id}: {e}")
        return False, str(e)


def delete_schema(config_id: str) -> bool:
    """Delete a schema. Returns True if deleted."""
    path = get_schema_path(config_id)
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"Deleted schema {config_id}")
            return True
    except Exception as e:
        logger.error(f"Error deleting schema {config_id}: {e}")
    return False


def list_schemas() -> List[Dict]:
    """List all saved schemas with summary metadata. Skips files starting with `_`."""
    _ensure_configs_dir()
    schemas = []
    try:
        for fn in os.listdir(CONFIGS_DIR):
            if not fn.endswith('.json'):
                continue
            if fn.startswith('_'):
                # Convention: files starting with _ are example/lookup data, not real schemas
                continue
            config_id = fn[:-5]  # strip .json
            schema = load_schema(config_id, resolve_lookups=False)
            if schema:
                schemas.append({
                    'id': schema.get('id', config_id),
                    'name': schema.get('name', config_id),
                    'version': schema.get('version', '1.0'),
                    'description': schema.get('description', ''),
                    'section_count': len(schema.get('sections', [])),
                    'action_count': len(schema.get('completion', {}).get('actions', [])),
                })
    except Exception as e:
        logger.error(f"Error listing schemas: {e}")
    return schemas


def _resolve_lookup_data(schema: Dict) -> Dict:
    """
    Walk through schema.lookup_data and resolve any `source: "file"` entries by
    loading their JSON files into inline `values`.

    Mutates a copy of the schema (does not modify the input).
    """
    if not schema.get('lookup_data'):
        return schema

    # Shallow copy and rebuild lookup_data
    resolved = dict(schema)
    resolved_lookups = {}

    for ref_name, lookup_def in schema['lookup_data'].items():
        if not isinstance(lookup_def, dict):
            resolved_lookups[ref_name] = lookup_def
            continue

        source = lookup_def.get('source', 'inline')
        if source == 'inline':
            resolved_lookups[ref_name] = lookup_def
        elif source == 'file':
            file_name = lookup_def.get('file')
            if not file_name:
                logger.warning(f"Lookup '{ref_name}' has source=file but no 'file' field")
                resolved_lookups[ref_name] = {'source': 'inline', 'values': []}
                continue
            file_path = os.path.join(CONFIGS_DIR, file_name)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    file_data = json.load(f)
                if isinstance(file_data, list):
                    values = file_data
                elif isinstance(file_data, dict) and 'values' in file_data:
                    values = file_data['values']
                else:
                    logger.warning(f"Unexpected lookup file format: {file_path}")
                    values = []
                resolved_lookups[ref_name] = {
                    'source': 'inline',
                    'values': values,
                    '_resolved_from_file': file_name,
                }
            except FileNotFoundError:
                logger.warning(f"Lookup file not found: {file_path}")
                resolved_lookups[ref_name] = {'source': 'inline', 'values': []}
            except Exception as e:
                logger.error(f"Error loading lookup file {file_path}: {e}")
                resolved_lookups[ref_name] = {'source': 'inline', 'values': []}
        else:
            logger.warning(f"Unknown lookup source: {source} for {ref_name}")
            resolved_lookups[ref_name] = lookup_def

    resolved['lookup_data'] = resolved_lookups
    return resolved


def get_lookup_values(schema: Dict, lookup_ref: str) -> List[Dict]:
    """
    Get the resolved values for a lookup reference. Returns empty list if not found.
    Schema must already be resolved (via load_schema(resolve_lookups=True)).
    """
    lookup_data = schema.get('lookup_data', {})
    lookup_def = lookup_data.get(lookup_ref)
    if not lookup_def:
        return []
    if isinstance(lookup_def, dict):
        return lookup_def.get('values', []) or []
    return []


def get_section(schema: Dict, section_id: str) -> Optional[Dict]:
    """Find a section by ID in the schema. Returns None if not found."""
    for section in schema.get('sections', []):
        if section.get('id') == section_id:
            return section
    return None


def get_field(schema: Dict, section_id: str, field_id: str) -> Optional[Dict]:
    """Find a field by section ID + field ID. Returns None if not found."""
    section = get_section(schema, section_id)
    if not section:
        return None
    for field in section.get('fields', []):
        if field.get('id') == field_id:
            return field
    return None


def get_required_field_ids(schema: Dict, section_id: str) -> List[str]:
    """Return the IDs of required fields in a section."""
    section = get_section(schema, section_id)
    if not section:
        return []
    return [f['id'] for f in section.get('fields', []) if f.get('required')]


def get_section_order(schema: Dict) -> List[str]:
    """Return section IDs in their defined order."""
    sections = sorted(
        schema.get('sections', []),
        key=lambda s: s.get('order', 999),
    )
    return [s['id'] for s in sections]


def get_next_section_id(schema: Dict, current_section_id: str) -> Optional[str]:
    """
    Return the ID of the section that follows current_section_id, or None if
    current is the last section.
    """
    order = get_section_order(schema)
    try:
        idx = order.index(current_section_id)
    except ValueError:
        return None
    if idx + 1 >= len(order):
        return None
    return order[idx + 1]
