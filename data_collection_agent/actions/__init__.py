"""
Completion actions pipeline.

After a user confirms the recap, the route calls execute_pipeline() with the
list of actions defined in the form schema's `completion.actions`. Each action
has a `type` that resolves to a registered ActionHandler subclass.

To add a new action type:
  1. Create data_collection_agent/actions/your_action.py with a subclass of ActionHandler.
  2. Import and register it in `register_builtin_actions()` below.

The registry is a process-global dict, populated once per process at blueprint
initialization (see data_collection_agent/__init__.py).
"""

import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Type

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Result and pipeline types
# ----------------------------------------------------------------------

@dataclass
class ActionResult:
    """Outcome of a single action execution."""
    action_type: str
    label: str
    success: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class PipelineResult:
    """Outcome of executing the full pipeline of completion actions."""
    results: List[ActionResult] = field(default_factory=list)
    all_success: bool = False
    stopped_early: bool = False

    def to_dict(self) -> Dict:
        return {
            'results': [r.to_dict() for r in self.results],
            'all_success': self.all_success,
            'stopped_early': self.stopped_early,
        }


# ----------------------------------------------------------------------
# Base class
# ----------------------------------------------------------------------

class ActionHandler(ABC):
    """
    Subclass this for each new action type and register it via ActionRegistry.

    Handlers should be stateless — each `execute` call creates whatever it needs.
    Long-running actions (workflow polling, etc.) should respect their own
    timeouts and return a partial-success ActionResult rather than blocking.
    """

    action_type: str = ''  # Override in subclasses

    @abstractmethod
    def execute(self, collected_data: Dict, session, config: Dict,
                schema: Dict) -> ActionResult:
        """
        Run the action.

        Args:
            collected_data: session.collected_data — full data, organized by section
            session: the CollectionSession (for ids, user_id, timestamps)
            config: this action's config dict from schema['completion']['actions'][i]
            schema: the full schema (for lookup data, field metadata)

        Returns:
            ActionResult describing the outcome.
        """
        ...

    def validate_config(self, config: Dict) -> List[str]:
        """
        Optional: validate this action's config at schema-save time.
        Return a list of error strings (empty if valid).
        Default implementation returns no errors.
        """
        return []


# ----------------------------------------------------------------------
# Registry + pipeline executor
# ----------------------------------------------------------------------

class ActionRegistry:
    """Process-global registry of action handlers, keyed by action type string."""
    _handlers: Dict[str, Type[ActionHandler]] = {}

    @classmethod
    def register(cls, action_type: str, handler_class: Type[ActionHandler]):
        if action_type in cls._handlers:
            logger.warning(f"Re-registering action handler for type '{action_type}'")
        cls._handlers[action_type] = handler_class
        logger.info(f"Registered action handler: {action_type} -> {handler_class.__name__}")

    @classmethod
    def get(cls, action_type: str) -> Optional[Type[ActionHandler]]:
        return cls._handlers.get(action_type)

    @classmethod
    def list_types(cls) -> List[str]:
        return list(cls._handlers.keys())

    @classmethod
    def execute_pipeline(cls, actions: List[Dict], collected_data: Dict,
                         session, schema: Dict) -> PipelineResult:
        """
        Execute the pipeline of completion actions in order.

        Each action's `continue_on_error` flag controls whether a failure stops
        the pipeline. By default, failure stops the pipeline.
        """
        out = PipelineResult()
        for cfg in (actions or []):
            atype = cfg.get('type')
            label = cfg.get('label') or atype or 'action'
            handler_cls = cls.get(atype)
            if handler_cls is None:
                out.results.append(ActionResult(
                    action_type=atype or '?',
                    label=label,
                    success=False,
                    message=f"Unknown action type: {atype}",
                ))
                if not cfg.get('continue_on_error', False):
                    out.stopped_early = True
                    break
                continue

            handler = handler_cls()
            t_start = time.time()
            try:
                result = handler.execute(collected_data, session, cfg, schema)
                # Ensure the handler set the right action_type/label, fallback if not
                if not result.action_type:
                    result.action_type = atype
                if not result.label:
                    result.label = label
            except Exception as e:
                logger.error(f"Action {atype} ({label}) raised: {e}", exc_info=True)
                result = ActionResult(
                    action_type=atype,
                    label=label,
                    success=False,
                    message=f"Exception: {e}",
                )
            result.duration_ms = int((time.time() - t_start) * 1000)
            out.results.append(result)

            if not result.success and not cfg.get('continue_on_error', False):
                out.stopped_early = True
                break

        out.all_success = bool(out.results) and all(r.success for r in out.results)
        return out


# ----------------------------------------------------------------------
# Template substitution engine
# ----------------------------------------------------------------------

# Regex for {{...}} placeholders, allowing dot paths and prefixes like __secret:
_TEMPLATE_RE = re.compile(r'\{\{\s*([^{}]+?)\s*\}\}')


def render_template(template: Any, collected_data: Dict, session, schema: Dict) -> Any:
    """
    Recursively substitute {{...}} placeholders in strings, dicts, and lists.

    Supported placeholders:
      {{field_id}}                  - any collected field's value (searches all sections)
      {{section_id.field_id}}       - explicit section.field
      {{lookup_field.property}}     - dot path into a lookup-resolved object
      {{__all_data__}}              - full collected_data dict as JSON
      {{__summary__}}               - human-readable plain-text summary
      {{__secret:KEY__}}            - environment / local secret lookup
      {{__session_id__}}            - the session's UUID
      {{__user_id__}}               - the user's id
      {{__timestamp__}}             - submission timestamp (UTC ISO)
      {{__config_id__}}             - the schema's id

    Non-string templates (dict, list) are walked recursively. None and primitives
    pass through.
    """
    if isinstance(template, str):
        return _render_string(template, collected_data, session, schema)
    if isinstance(template, dict):
        return {k: render_template(v, collected_data, session, schema) for k, v in template.items()}
    if isinstance(template, list):
        return [render_template(v, collected_data, session, schema) for v in template]
    return template


def _render_string(s: str, collected_data: Dict, session, schema: Dict) -> str:
    def replace(match):
        token = match.group(1).strip()
        return _resolve_token(token, collected_data, session, schema)
    return _TEMPLATE_RE.sub(replace, s)


def _resolve_token(token: str, collected_data: Dict, session, schema: Dict) -> str:
    # Special tokens
    if token == '__all_data__':
        return json.dumps(collected_data, default=str, indent=2)
    if token == '__summary__':
        return _build_summary_text(collected_data, schema)
    if token == '__session_id__':
        return getattr(session, 'session_id', '')
    if token == '__user_id__':
        return getattr(session, 'user_id', '')
    if token == '__timestamp__':
        return datetime.utcnow().isoformat()
    if token == '__config_id__':
        return getattr(session, 'config_id', '')

    if token.startswith('__secret:') and token.endswith('__'):
        key = token[len('__secret:'):-len('__')]
        return _lookup_secret(key)

    # Dot-path lookup
    parts = token.split('.')
    if len(parts) == 1:
        # Bare field id — search all sections
        for sec_data in collected_data.values():
            if isinstance(sec_data, dict) and parts[0] in sec_data:
                value = sec_data[parts[0]]
                # If the value resolves to a lookup object, return its id (default)
                return _stringify(value)
        return ''

    # parts[0] could be a section id, OR a field id whose value is an object we can index into
    first = parts[0]
    rest = parts[1:]

    # Case A: section.field[.subfield...]
    if first in collected_data and isinstance(collected_data[first], dict):
        sub = collected_data[first]
        if rest and rest[0] in sub:
            value = sub[rest[0]]
            for p in rest[1:]:
                value = _index_value(value, p)
                if value is None:
                    return ''
            return _stringify(value)

    # Case B: field.subfield - field's value is expected to be a lookup id; expand to lookup row
    for sec_data in collected_data.values():
        if not isinstance(sec_data, dict):
            continue
        if first in sec_data:
            field_value = sec_data[first]
            # Try to find the field's lookup_ref to expand
            field_def = _find_field_def(schema, first)
            if field_def and field_def.get('lookup_ref'):
                # `schema_loader` lives at the package root, not inside
                # `actions/` — use a parent-relative import.
                from ..schema_loader import get_lookup_values
                items = get_lookup_values(schema, field_def['lookup_ref'])
                for item in items:
                    if isinstance(item, dict) and str(item.get('id')) == str(field_value):
                        value = item
                        for p in rest:
                            value = _index_value(value, p)
                            if value is None:
                                return ''
                        return _stringify(value)
            # Fall back to the raw value (with rest ignored)
            return _stringify(field_value)

    return ''


def _index_value(value, key):
    if isinstance(value, dict):
        return value.get(key)
    return None


def _find_field_def(schema: Dict, field_id: str) -> Optional[Dict]:
    for section in schema.get('sections', []):
        for fld in section.get('fields', []):
            if fld.get('id') == field_id:
                return fld
    return None


def _stringify(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


def _build_summary_text(collected_data: Dict, schema: Dict) -> str:
    """Build a human-readable plain-text summary of the collected data."""
    # `schema_loader` lives at the package root, not inside `actions/`.
    from ..schema_loader import get_section, get_section_order, get_lookup_values
    lines = []
    for sid in get_section_order(schema):
        section = get_section(schema, sid)
        if not section:
            continue
        sec_data = collected_data.get(sid) or {}
        if not sec_data:
            continue
        lines.append(f"{section.get('title', sid)}:")
        for fld in section.get('fields', []):
            fid = fld.get('id')
            if not fid or fid not in sec_data:
                continue
            value = sec_data[fid]
            # Friendlier display for booleans / lookups
            if fld.get('type') == 'boolean':
                display = 'Yes' if value else 'No'
            elif fld.get('type') == 'lookup' and fld.get('lookup_ref'):
                items = get_lookup_values(schema, fld['lookup_ref'])
                display = next(
                    (
                        item.get('label') or item.get('name') or str(value)
                        for item in items
                        if isinstance(item, dict) and str(item.get('id')) == str(value)
                    ),
                    str(value),
                )
            elif isinstance(value, list):
                display = ', '.join(str(v) for v in value)
            else:
                display = str(value)
            lines.append(f"  - {fld.get('label', fid)}: {display}")
        lines.append('')
    return '\n'.join(lines).rstrip()


def _lookup_secret(key: str) -> str:
    """
    Resolve a {{__secret:KEY__}} reference. Tries (in order):
      1. data_collection_agent local secrets store (data/dca_secrets.json)
      2. Environment variable
      3. Empty string (with a warning)
    """
    # 1. Local secrets file
    secrets_path = os.path.join(
        os.getenv('APP_ROOT', os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'data',
        'dca_secrets.json',
    )
    try:
        if os.path.exists(secrets_path):
            with open(secrets_path, 'r', encoding='utf-8') as f:
                secrets = json.load(f) or {}
            if key in secrets:
                return str(secrets[key])
    except Exception as e:
        logger.warning(f"Error reading dca_secrets.json: {e}")
    # 2. Environment variable
    if key in os.environ:
        return os.environ[key]
    logger.warning(f"Secret '{key}' not found in dca_secrets.json or environment")
    return ''


# ----------------------------------------------------------------------
# Built-in registration
# ----------------------------------------------------------------------

_BUILTINS_REGISTERED = False


def register_builtin_actions():
    """Register all built-in action handlers. Idempotent."""
    global _BUILTINS_REGISTERED
    if _BUILTINS_REGISTERED:
        return
    from .email_action import EmailAction
    from .workflow_action import WorkflowAction
    from .api_action import ApiAction
    from .webhook_action import WebhookAction
    from .agent_action import AgentAction
    from .sms_action import SmsAction

    ActionRegistry.register('email', EmailAction)
    ActionRegistry.register('workflow', WorkflowAction)
    ActionRegistry.register('api', ApiAction)
    ActionRegistry.register('webhook', WebhookAction)
    ActionRegistry.register('agent', AgentAction)
    ActionRegistry.register('sms', SmsAction)
    _BUILTINS_REGISTERED = True


# Auto-register on import so the registry is always populated when anything
# imports `data_collection_agent.actions` (e.g. the schema validator).
# The blueprint factory still calls register_builtin_actions() explicitly,
# which is now a no-op the second time.
try:
    register_builtin_actions()
except Exception as _e:  # pragma: no cover
    logger.warning(f"Auto-registration of built-in actions failed: {_e}")
