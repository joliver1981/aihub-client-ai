"""
Registry Manager
==================
Manages reading and writing to the domain and action registry Python files.
Provides CRUD operations for domains, capabilities, actions, and input fields.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# File paths
BUILDER_SERVICE_DIR = Path(__file__).parent
BUILDER_AGENT_DIR = BUILDER_SERVICE_DIR.parent / "builder_agent"
DOMAINS_FILE = BUILDER_AGENT_DIR / "registry" / "platform_domains.py"
ACTIONS_FILE = BUILDER_AGENT_DIR / "actions" / "platform_actions.py"


# ═══════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════

def capability_to_dict(cap) -> Dict[str, Any]:
    """Convert a CapabilityDefinition to a dictionary."""
    return {
        "id": cap.id,
        "name": cap.name,
        "description": cap.description,
        "category": cap.category,
        "required_context": list(cap.required_context) if cap.required_context else [],
        "requires_domains": list(cap.requires_domains) if cap.requires_domains else [],
        "tags": list(cap.tags) if cap.tags else [],
    }


def domain_to_dict(domain) -> Dict[str, Any]:
    """Convert a DomainDefinition to a dictionary."""
    return {
        "id": domain.id,
        "name": domain.name,
        "description": domain.description,
        "version": domain.version,
        "key_concepts": list(domain.key_concepts) if domain.key_concepts else [],
        "context_notes": domain.context_notes or "",
        "depends_on": list(domain.depends_on) if domain.depends_on else [],
        "capabilities": [capability_to_dict(cap) for cap in domain.capabilities],
        "enabled": getattr(domain, 'enabled', True),  # Default to True for backwards compatibility
    }


def action_to_dict(action) -> Dict[str, Any]:
    """Convert an ActionDefinition to a dictionary."""
    route_info = None
    input_fields = []
    response_mappings = []

    if action.primary_route:
        route = action.primary_route
        route_info = {
            "method": route.method,
            "path": route.path,
            "encoding": route.encoding.value if hasattr(route.encoding, 'value') else str(route.encoding),
            "description": route.description or "",
        }

        for field in route.input_fields:
            input_fields.append({
                "name": field.name,
                "type": field.field_type.value if hasattr(field.field_type, 'value') else str(field.field_type),
                "required": field.required,
                "default": field.default,
                "description": field.description or "",
            })

        for mapping in route.response_mappings:
            response_mappings.append({
                "output_name": mapping.output_name,
                "source_path": mapping.source_path,
                "description": mapping.description or "",
            })

    return {
        "capability_id": action.capability_id,
        "domain_id": action.domain_id,
        "description": action.description,
        "notes": action.notes or "",
        "route": route_info,
        "input_fields": input_fields,
        "response_mappings": response_mappings,
        "suggested_prechecks": list(action.suggested_prechecks) if action.suggested_prechecks else [],
        "suggested_followups": list(action.suggested_followups) if action.suggested_followups else [],
    }


# ═══════════════════════════════════════════════════════════════════════════
# CODE GENERATION
# ═══════════════════════════════════════════════════════════════════════════

def generate_capability_code(cap: Dict[str, Any], indent: int = 12) -> str:
    """Generate Python code for a CapabilityDefinition."""
    ind = " " * indent
    lines = [
        f'{ind}CapabilityDefinition(',
        f'{ind}    id="{cap["id"]}",',
        f'{ind}    name="{cap["name"]}",',
        f'{ind}    description="{cap["description"]}",',
        f'{ind}    category="{cap["category"]}",',
    ]

    if cap.get("required_context"):
        lines.append(f'{ind}    required_context={cap["required_context"]},')

    if cap.get("requires_domains"):
        lines.append(f'{ind}    requires_domains={cap["requires_domains"]},')

    if cap.get("tags"):
        lines.append(f'{ind}    tags={cap["tags"]},')

    lines.append(f'{ind}),')
    return '\n'.join(lines)


def generate_domain_function_code(domain: Dict[str, Any]) -> str:
    """Generate Python code for a domain function."""
    caps_code = []
    for cap in domain.get("capabilities", []):
        caps_code.append(generate_capability_code(cap))

    capabilities_str = '\n'.join(caps_code) if caps_code else ""

    key_concepts_str = json.dumps(domain.get("key_concepts", []))
    depends_on_str = json.dumps(domain.get("depends_on", []))

    enabled_str = str(domain.get("enabled", True))

    code = f'''
def _{domain["id"]}_domain() -> DomainDefinition:
    return DomainDefinition(
        id="{domain["id"]}",
        name="{domain["name"]}",
        description=(
            "{domain["description"]}"
        ),
        version="{domain.get("version", "1.0")}",
        key_concepts={key_concepts_str},
        context_notes=(
            "{domain.get("context_notes", "")}"
        ),
        depends_on={depends_on_str},
        entities=[],
        capabilities=[
{capabilities_str}
        ],
        enabled={enabled_str},
    )
'''
    return code


def _find_action_block(content: str, capability_id: str):
    """Find an ActionDefinition block by capability_id using parenthesis counting.

    Returns (start, end) offsets of the full block including trailing comma,
    or None if not found. Handles nested parentheses correctly.
    """
    marker = f'ActionDefinition(\n            capability_id="{capability_id}"'
    idx = content.find(marker)
    if idx == -1:
        # Try with flexible whitespace
        pattern = rf'ActionDefinition\(\s*capability_id="{re.escape(capability_id)}"'
        match = re.search(pattern, content)
        if not match:
            return None
        idx = match.start()

    # Walk backwards to include leading whitespace
    start = idx
    while start > 0 and content[start - 1] in (' ', '\t'):
        start -= 1

    # Count parentheses from the opening '(' of ActionDefinition(
    paren_start = content.index('(', idx)
    depth = 0
    i = paren_start
    while i < len(content):
        ch = content[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                # Found the matching close paren
                end = i + 1
                # Include trailing comma if present
                if end < len(content) and content[end] == ',':
                    end += 1
                return (start, end)
        elif ch == '"':
            # Skip string literals
            i += 1
            while i < len(content) and content[i] != '"':
                if content[i] == '\\':
                    i += 1
                i += 1
        elif ch == "'":
            # Skip single-quoted strings
            i += 1
            while i < len(content) and content[i] != "'":
                if content[i] == '\\':
                    i += 1
                i += 1
        i += 1

    return None


def generate_input_field_code(field: Dict[str, Any], indent: int = 20) -> str:
    """Generate Python code for a FieldSchema."""
    ind = " " * indent
    field_type = field.get("type", "STRING").upper()

    lines = [
        f'{ind}FieldSchema(',
        f'{ind}    "{field["name"]}", FieldType.{field_type}, required={field.get("required", False)},',
    ]

    if field.get("default") is not None:
        default_val = field["default"]
        if isinstance(default_val, str):
            lines.append(f'{ind}    default="{default_val}",')
        elif isinstance(default_val, bool):
            lines.append(f'{ind}    default={str(default_val)},')
        elif isinstance(default_val, (list, dict)):
            lines.append(f'{ind}    default={json.dumps(default_val)},')
        else:
            lines.append(f'{ind}    default={default_val},')

    if field.get("description"):
        desc = field["description"].replace('"', '\\"')
        lines.append(f'{ind}    description="{desc}",')

    lines.append(f'{ind}),')
    return '\n'.join(lines)


def generate_response_mapping_code(mapping: Dict[str, Any], indent: int = 20) -> str:
    """Generate Python code for a ResponseMapping."""
    ind = " " * indent
    lines = [
        f'{ind}ResponseMapping(',
        f'{ind}    "{mapping["output_name"]}", "{mapping["source_path"]}",',
    ]

    if mapping.get("description"):
        desc = mapping["description"].replace('"', '\\"')
        lines.append(f'{ind}    description="{desc}",')

    lines.append(f'{ind}),')
    return '\n'.join(lines)


def generate_action_code(action: Dict[str, Any], indent: int = 8) -> str:
    """Generate Python code for an ActionDefinition."""
    ind = " " * indent

    # Generate input fields
    input_fields_code = []
    for field in action.get("input_fields", []):
        input_fields_code.append(generate_input_field_code(field))
    input_fields_str = '\n'.join(input_fields_code) if input_fields_code else ""

    # Generate response mappings
    response_mappings_code = []
    for mapping in action.get("response_mappings", []):
        response_mappings_code.append(generate_response_mapping_code(mapping))
    response_mappings_str = '\n'.join(response_mappings_code) if response_mappings_code else ""

    route = action.get("route", {})
    method = route.get("method", "GET")
    path = route.get("path", "/")
    encoding = route.get("encoding", "json").upper()
    route_desc = route.get("description", "").replace('"', '\\"')
    action_desc = action.get("description", "").replace('"', '\\"')
    action_notes = action.get("notes", "").replace('"', '\\"')

    code = f'''{ind}ActionDefinition(
{ind}    capability_id="{action["capability_id"]}",
{ind}    domain_id="{action["domain_id"]}",
{ind}    description="{action_desc}",
{ind}    notes="{action_notes}",
{ind}    primary_route=RouteMapping(
{ind}        method="{method}",
{ind}        path="{path}",
{ind}        encoding=PayloadEncoding.{encoding},
{ind}        description="{route_desc}",
{ind}        input_fields=[
{input_fields_str}
{ind}        ],
{ind}        response_mappings=[
{response_mappings_str}
{ind}        ],
{ind}    ),
{ind}),'''

    return code


# ═══════════════════════════════════════════════════════════════════════════
# DOMAIN REGISTRY OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════

class DomainRegistryManager:
    """Manages CRUD operations for the domain registry."""

    def __init__(self):
        self.file_path = DOMAINS_FILE

    def _read_file(self) -> str:
        return self.file_path.read_text(encoding="utf-8")

    def _write_file(self, content: str):
        self.file_path.write_text(content, encoding="utf-8")
        logger.info(f"Updated {self.file_path}")

    def _reload_domains(self):
        """Force reload of the domains module."""
        import sys
        module_name = "builder_agent.registry.platform_domains"
        if module_name in sys.modules:
            del sys.modules[module_name]

    def get_all_domains(self) -> List[Dict[str, Any]]:
        """Get all domains as dictionaries."""
        import sys
        if str(BUILDER_AGENT_DIR.parent) not in sys.path:
            sys.path.insert(0, str(BUILDER_AGENT_DIR.parent))

        self._reload_domains()
        from builder_agent.registry.platform_domains import get_platform_domains

        domains = get_platform_domains()
        return [domain_to_dict(d) for d in domains]

    def get_domain(self, domain_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific domain by ID."""
        domains = self.get_all_domains()
        for domain in domains:
            if domain["id"] == domain_id:
                return domain
        return None

    def add_capability(self, domain_id: str, capability: Dict[str, Any]) -> bool:
        """Add a capability to a domain."""
        content = self._read_file()

        # Find the domain function and its capabilities list
        pattern = rf'(def _{domain_id}_domain\(\).*?capabilities=\[)(.*?)(\s*\],\s*\))'
        match = re.search(pattern, content, re.DOTALL)

        if not match:
            logger.error(f"Domain function not found: _{domain_id}_domain")
            return False

        # Generate new capability code
        new_cap_code = generate_capability_code(capability)

        # Insert before the closing bracket
        existing_caps = match.group(2).rstrip()
        if existing_caps.strip():
            new_caps = existing_caps + '\n' + new_cap_code
        else:
            new_caps = '\n' + new_cap_code

        new_content = content[:match.start(2)] + new_caps + content[match.end(2):]
        self._write_file(new_content)
        return True

    def update_capability(self, domain_id: str, capability_id: str, capability: Dict[str, Any]) -> bool:
        """Update a capability in a domain."""
        content = self._read_file()

        # Find and replace the capability
        # Pattern to match a CapabilityDefinition with the given id
        pattern = rf'(\s*CapabilityDefinition\(\s*id="{capability_id}".*?\),)'
        match = re.search(pattern, content, re.DOTALL)

        if not match:
            logger.error(f"Capability not found: {capability_id}")
            return False

        # Generate new capability code
        new_cap_code = generate_capability_code(capability)

        new_content = content[:match.start(1)] + '\n' + new_cap_code + content[match.end(1):]
        self._write_file(new_content)
        return True

    def delete_capability(self, domain_id: str, capability_id: str) -> bool:
        """Delete a capability from a domain."""
        content = self._read_file()

        # Find and remove the capability
        pattern = rf'\s*CapabilityDefinition\(\s*id="{capability_id}".*?\),'
        match = re.search(pattern, content, re.DOTALL)

        if not match:
            logger.error(f"Capability not found: {capability_id}")
            return False

        new_content = content[:match.start()] + content[match.end():]
        self._write_file(new_content)
        return True

    def add_domain(self, domain: Dict[str, Any]) -> bool:
        """Add a new domain to the registry."""
        content = self._read_file()

        # Generate domain function code
        domain_func_code = generate_domain_function_code(domain)

        # Find where to insert (before get_platform_domains or at end of file)
        # Insert before the last domain function or before get_platform_domains
        insert_pattern = r'(def get_platform_domains\(\))'
        match = re.search(insert_pattern, content)

        if match:
            insert_pos = match.start()
            new_content = content[:insert_pos] + domain_func_code + '\n\n' + content[insert_pos:]
        else:
            new_content = content + domain_func_code

        # Also add to get_platform_domains return list
        return_pattern = r'(return\s*\[)(.*?)(\s*\])'
        return_match = re.search(return_pattern, new_content, re.DOTALL)

        if return_match:
            existing_list = return_match.group(2).rstrip()
            if existing_list.strip():
                new_list = existing_list.rstrip(',') + f',\n        _{domain["id"]}_domain(),'
            else:
                new_list = f'\n        _{domain["id"]}_domain(),'

            new_content = new_content[:return_match.start(2)] + new_list + new_content[return_match.end(2):]

        self._write_file(new_content)
        return True

    def update_domain(self, domain_id: str, domain: Dict[str, Any]) -> bool:
        """Update an existing domain in the registry."""
        content = self._read_file()

        # Generate new domain function code
        new_func_code = generate_domain_function_code(domain)

        # Find and replace the existing domain function
        pattern = rf'(def _{domain_id}_domain\(\).*?(?=\ndef |$))'
        match = re.search(pattern, content, re.DOTALL)

        if not match:
            return False

        # Replace the old function with the new one
        new_content = content[:match.start()] + new_func_code.strip() + '\n\n' + content[match.end():]

        self._write_file(new_content)
        return True

    def delete_domain(self, domain_id: str) -> bool:
        """Delete a domain from the registry."""
        content = self._read_file()

        # Remove the domain function
        pattern = rf'\n*def _{domain_id}_domain\(\).*?(?=\ndef |$)'
        content = re.sub(pattern, '', content, flags=re.DOTALL)

        # Remove from get_platform_domains return list
        pattern = rf',?\s*_{domain_id}_domain\(\),?'
        content = re.sub(pattern, '', content)

        self._write_file(content)
        return True


# ═══════════════════════════════════════════════════════════════════════════
# ACTION REGISTRY OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════

class ActionRegistryManager:
    """Manages CRUD operations for the action registry."""

    def __init__(self):
        self.file_path = ACTIONS_FILE

    def _read_file(self) -> str:
        return self.file_path.read_text(encoding="utf-8")

    def _write_file(self, content: str):
        self.file_path.write_text(content, encoding="utf-8")
        logger.info(f"Updated {self.file_path}")

    def _reload_actions(self):
        """Force reload of the actions module."""
        import sys
        module_name = "builder_agent.actions.platform_actions"
        if module_name in sys.modules:
            del sys.modules[module_name]

    def get_all_actions(self) -> List[Dict[str, Any]]:
        """Get all actions as dictionaries."""
        import sys
        if str(BUILDER_AGENT_DIR.parent) not in sys.path:
            sys.path.insert(0, str(BUILDER_AGENT_DIR.parent))

        self._reload_actions()
        from builder_agent.actions.platform_actions import get_platform_actions

        actions = get_platform_actions()
        return [action_to_dict(a) for a in actions]

    def get_action(self, capability_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific action by capability ID."""
        actions = self.get_all_actions()
        for action in actions:
            if action["capability_id"] == capability_id:
                return action
        return None

    def add_action(self, action: Dict[str, Any]) -> bool:
        """Add a new action to the registry."""
        content = self._read_file()

        # Find the domain's action function
        domain_id = action["domain_id"]
        pattern = rf'(def _{domain_id}_actions\(\).*?return\s*\[)(.*?)(\s*\])'
        match = re.search(pattern, content, re.DOTALL)

        if not match:
            logger.error(f"Action function not found: _{domain_id}_actions")
            return False

        # Generate new action code
        new_action_code = generate_action_code(action)

        # Insert before the closing bracket
        existing_actions = match.group(2).rstrip()
        if existing_actions.strip():
            new_actions = existing_actions + '\n\n' + new_action_code
        else:
            new_actions = '\n' + new_action_code

        new_content = content[:match.start(2)] + new_actions + content[match.end(2):]
        self._write_file(new_content)
        return True

    def update_action(self, capability_id: str, action: Dict[str, Any]) -> bool:
        """Update an action in the registry."""
        content = self._read_file()

        block = _find_action_block(content, capability_id)
        if not block:
            logger.error(f"Action not found: {capability_id}")
            return False

        start, end = block
        new_action_code = generate_action_code(action)

        new_content = content[:start] + new_action_code + content[end:]
        self._write_file(new_content)
        return True

    def delete_action(self, capability_id: str) -> bool:
        """Delete an action from the registry."""
        content = self._read_file()

        block = _find_action_block(content, capability_id)
        if not block:
            logger.error(f"Action not found: {capability_id}")
            return False

        start, end = block
        # Also remove trailing newlines to keep formatting clean
        while end < len(content) and content[end] in ('\n', '\r'):
            end += 1

        new_content = content[:start] + content[end:]
        self._write_file(new_content)
        return True

    def add_input_field(self, capability_id: str, field: Dict[str, Any]) -> bool:
        """Add an input field to an action."""
        content = self._read_file()

        # Find the action and its input_fields list
        pattern = rf'(ActionDefinition\(\s*capability_id="{capability_id}".*?input_fields=\[)(.*?)(\s*\],)'
        match = re.search(pattern, content, re.DOTALL)

        if not match:
            logger.error(f"Action not found: {capability_id}")
            return False

        # Generate new field code
        new_field_code = generate_input_field_code(field)

        # Insert before the closing bracket
        existing_fields = match.group(2).rstrip()
        if existing_fields.strip():
            new_fields = existing_fields + '\n' + new_field_code
        else:
            new_fields = '\n' + new_field_code

        new_content = content[:match.start(2)] + new_fields + content[match.end(2):]
        self._write_file(new_content)
        return True

    def delete_input_field(self, capability_id: str, field_name: str) -> bool:
        """Delete an input field from an action."""
        content = self._read_file()

        # Find and remove the field
        pattern = rf'\s*FieldSchema\(\s*"{field_name}".*?\),'
        match = re.search(pattern, content, re.DOTALL)

        if not match:
            logger.error(f"Field not found: {field_name}")
            return False

        new_content = content[:match.start()] + content[match.end():]
        self._write_file(new_content)
        return True

    def update_input_field(self, capability_id: str, field_name: str, field: Dict[str, Any]) -> bool:
        """Update an input field in an action."""
        content = self._read_file()

        # Find and replace the field
        pattern = rf'(\s*)FieldSchema\(\s*"{field_name}".*?\),'
        match = re.search(pattern, content, re.DOTALL)

        if not match:
            logger.error(f"Field not found: {field_name}")
            return False

        # Generate new field code with same indentation
        new_field_code = generate_input_field_code(field)

        new_content = content[:match.start()] + '\n' + new_field_code + content[match.end():]
        self._write_file(new_content)
        return True

    def add_response_mapping(self, capability_id: str, mapping: Dict[str, Any]) -> bool:
        """Add a response mapping to an action."""
        content = self._read_file()

        # Find the action and its response_mappings list
        pattern = rf'(ActionDefinition\(\s*capability_id="{capability_id}".*?response_mappings=\[)(.*?)(\s*\],)'
        match = re.search(pattern, content, re.DOTALL)

        if not match:
            logger.error(f"Action not found: {capability_id}")
            return False

        # Generate new mapping code
        new_mapping_code = generate_response_mapping_code(mapping)

        # Insert before the closing bracket
        existing_mappings = match.group(2).rstrip()
        if existing_mappings.strip():
            new_mappings = existing_mappings + '\n' + new_mapping_code
        else:
            new_mappings = '\n' + new_mapping_code

        new_content = content[:match.start(2)] + new_mappings + content[match.end(2):]
        self._write_file(new_content)
        return True

    def delete_response_mapping(self, capability_id: str, output_name: str) -> bool:
        """Delete a response mapping from an action."""
        content = self._read_file()

        # Find and remove the mapping
        pattern = rf'\s*ResponseMapping\(\s*"{output_name}".*?\),'
        match = re.search(pattern, content, re.DOTALL)

        if not match:
            logger.error(f"Mapping not found: {output_name}")
            return False

        new_content = content[:match.start()] + content[match.end():]
        self._write_file(new_content)
        return True

    def update_response_mapping(self, capability_id: str, output_name: str, mapping: Dict[str, Any]) -> bool:
        """Update a response mapping in an action."""
        content = self._read_file()

        # Find and replace the mapping
        pattern = rf'(\s*)ResponseMapping\(\s*"{output_name}".*?\),'
        match = re.search(pattern, content, re.DOTALL)

        if not match:
            logger.error(f"Mapping not found: {output_name}")
            return False

        # Generate new mapping code with same indentation
        new_mapping_code = generate_response_mapping_code(mapping)

        new_content = content[:match.start()] + '\n' + new_mapping_code + content[match.end():]
        self._write_file(new_content)
        return True


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON INSTANCES
# ═══════════════════════════════════════════════════════════════════════════

domain_manager = DomainRegistryManager()
action_manager = ActionRegistryManager()
