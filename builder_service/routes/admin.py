"""
Admin Routes for Builder Configuration
========================================
API endpoints for reading and writing builder agent configuration.
Used by the admin UI to manage prompts, registries, and settings.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from registry_manager import domain_manager, action_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Base paths
BUILDER_SERVICE_DIR = Path(__file__).parent.parent
BUILDER_AGENT_DIR = BUILDER_SERVICE_DIR.parent / "builder_agent"


# ─── Request/Response Models ───────────────────────────────────────────────

class ConfigValue(BaseModel):
    file: str
    variable: str
    value: str


class SaveConfigRequest(BaseModel):
    configs: List[ConfigValue]


class CapabilityCreate(BaseModel):
    id: str
    name: str
    description: str
    category: str = "create"
    required_context: List[str] = []
    requires_domains: List[str] = []
    tags: List[str] = []


class DomainCreate(BaseModel):
    id: str
    name: str
    description: str
    version: str = "1.0"
    key_concepts: List[str] = []
    context_notes: str = ""
    depends_on: List[str] = []
    enabled: bool = True


class DomainUpdate(BaseModel):
    name: str
    description: str
    version: str = "1.0"
    key_concepts: List[str] = []
    context_notes: str = ""
    depends_on: List[str] = []
    enabled: bool = True


class InputFieldCreate(BaseModel):
    name: str
    type: str = "STRING"
    required: bool = False
    default: Any = None
    description: str = ""


class ResponseMappingCreate(BaseModel):
    output_name: str
    source_path: str
    description: str = ""


class RouteCreate(BaseModel):
    method: str = "GET"
    path: str
    encoding: str = "JSON"
    description: str = ""


class ActionCreate(BaseModel):
    capability_id: str
    domain_id: str
    description: str
    notes: str = ""
    route: RouteCreate
    input_fields: List[InputFieldCreate] = []
    response_mappings: List[ResponseMappingCreate] = []


class AgentCreate(BaseModel):
    id: str
    name: str
    description: str
    specializations: List[str] = []
    protocol: str = "text_chat"
    endpoint: str
    timeout: int = 120
    system_prompt: Optional[str] = None
    enabled: bool = True
    metadata: Dict[str, Any] = {}


# ─── File Paths ─────────────────────────────────────────────────────────────

CONFIG_FILES = {
    "builder_config": BUILDER_SERVICE_DIR / "builder_config.py",
    "platform_knowledge": BUILDER_SERVICE_DIR / "platform_knowledge.py",
    "nodes": BUILDER_SERVICE_DIR / "graph" / "nodes.py",
    "context_gatherer": BUILDER_SERVICE_DIR / "context_gatherer.py",
}


# ─── Helper Functions ───────────────────────────────────────────────────────

def extract_multiline_string(content: str, var_name: str) -> Optional[str]:
    """Extract a triple-quoted string variable from Python source."""
    pattern = rf'{var_name}\s*=\s*"""(.*?)"""'
    match = re.search(pattern, content, re.DOTALL)
    if match:
        return match.group(1).strip()

    pattern = rf"{var_name}\s*=\s*'''(.*?)'''"
    match = re.search(pattern, content, re.DOTALL)
    if match:
        return match.group(1).strip()

    return None


def update_multiline_string(content: str, var_name: str, new_value: str) -> str:
    """Update a triple-quoted string variable in Python source."""
    escaped_value = new_value.replace('"""', '\\"\\"\\"')

    pattern = rf'({var_name}\s*=\s*""").*?(""")'
    replacement = rf'\g<1>{escaped_value}\g<2>'

    new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)

    if new_content == content:
        pattern = rf"({var_name}\s*=\s*''').*?(''')"
        new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)

    return new_content


# ─── Config Endpoints ───────────────────────────────────────────────────────

@router.get("/config/{file_key}/{var_name}")
async def get_config_value(file_key: str, var_name: str):
    """Get a specific config variable value."""
    if file_key not in CONFIG_FILES:
        raise HTTPException(status_code=404, detail=f"Unknown config file: {file_key}")

    file_path = CONFIG_FILES[file_key]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Config file not found: {file_path}")

    try:
        content = file_path.read_text(encoding="utf-8")
        value = extract_multiline_string(content, var_name)

        if value is None:
            raise HTTPException(status_code=404, detail=f"Variable not found: {var_name}")

        return {"file": file_key, "variable": var_name, "value": value}

    except Exception as e:
        logger.error(f"Error reading config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/config")
async def save_config_values(request: SaveConfigRequest):
    """Save multiple config values at once."""
    results = []

    for config in request.configs:
        if config.file not in CONFIG_FILES:
            results.append({"file": config.file, "variable": config.variable, "status": "error", "message": "Unknown file"})
            continue

        file_path = CONFIG_FILES[config.file]

        try:
            content = file_path.read_text(encoding="utf-8")
            new_content = update_multiline_string(content, config.variable, config.value)

            if new_content == content:
                results.append({"file": config.file, "variable": config.variable, "status": "error", "message": "Variable not found or unchanged"})
            else:
                file_path.write_text(new_content, encoding="utf-8")
                results.append({"file": config.file, "variable": config.variable, "status": "success"})
                logger.info(f"Updated {config.file}.{config.variable}")

        except Exception as e:
            logger.error(f"Error saving config {config.file}.{config.variable}: {e}")
            results.append({"file": config.file, "variable": config.variable, "status": "error", "message": str(e)})

    return {"results": results}


# ═══════════════════════════════════════════════════════════════════════════
# DOMAIN REGISTRY ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/domains")
async def list_domains():
    """List all domains from the registry."""
    try:
        domains = domain_manager.get_all_domains()
        result = []

        for domain in domains:
            result.append({
                "id": domain["id"],
                "name": domain["name"],
                "description": domain["description"],
                "context_notes": domain.get("context_notes", ""),
                "depends_on": domain.get("depends_on", []),
                "capability_count": len(domain.get("capabilities", [])),
            })

        return {"domains": result}

    except Exception as e:
        logger.error(f"Error loading domains: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/domains/{domain_id}")
async def get_domain(domain_id: str):
    """Get detailed info for a specific domain."""
    try:
        domain = domain_manager.get_domain(domain_id)

        if not domain:
            raise HTTPException(status_code=404, detail=f"Domain not found: {domain_id}")

        return domain

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading domain {domain_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/domains")
async def create_domain(domain: DomainCreate):
    """Create a new domain."""
    try:
        # Check if domain already exists
        existing = domain_manager.get_domain(domain.id)
        if existing:
            raise HTTPException(status_code=400, detail=f"Domain already exists: {domain.id}")

        domain_dict = domain.model_dump()
        domain_dict["capabilities"] = []

        success = domain_manager.add_domain(domain_dict)

        if success:
            return {"status": "success", "domain_id": domain.id}
        else:
            raise HTTPException(status_code=500, detail="Failed to create domain")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating domain: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/domains/{domain_id}")
async def update_domain(domain_id: str, domain: DomainUpdate):
    """Update an existing domain."""
    try:
        existing = domain_manager.get_domain(domain_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Domain not found: {domain_id}")

        # Build update dict, preserving capabilities
        domain_dict = domain.model_dump()
        domain_dict["id"] = domain_id
        domain_dict["capabilities"] = existing.get("capabilities", [])

        success = domain_manager.update_domain(domain_id, domain_dict)

        if success:
            return {"status": "success", "domain_id": domain_id}
        else:
            raise HTTPException(status_code=500, detail="Failed to update domain")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating domain: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/domains/{domain_id}")
async def delete_domain(domain_id: str):
    """Delete a domain."""
    try:
        existing = domain_manager.get_domain(domain_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Domain not found: {domain_id}")

        success = domain_manager.delete_domain(domain_id)

        if success:
            return {"status": "success", "domain_id": domain_id}
        else:
            raise HTTPException(status_code=500, detail="Failed to delete domain")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting domain: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Capability Endpoints ──────────────────────────────────────────────────

@router.post("/domains/{domain_id}/capabilities")
async def create_capability(domain_id: str, capability: CapabilityCreate):
    """Add a capability to a domain."""
    try:
        existing = domain_manager.get_domain(domain_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Domain not found: {domain_id}")

        # Check if capability ID already exists
        for cap in existing.get("capabilities", []):
            if cap["id"] == capability.id:
                raise HTTPException(status_code=400, detail=f"Capability already exists: {capability.id}")

        success = domain_manager.add_capability(domain_id, capability.model_dump())

        if success:
            return {"status": "success", "capability_id": capability.id}
        else:
            raise HTTPException(status_code=500, detail="Failed to create capability")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating capability: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/domains/{domain_id}/capabilities/{capability_id}")
async def update_capability(domain_id: str, capability_id: str, capability: CapabilityCreate):
    """Update a capability in a domain."""
    try:
        existing = domain_manager.get_domain(domain_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Domain not found: {domain_id}")

        success = domain_manager.update_capability(domain_id, capability_id, capability.model_dump())

        if success:
            return {"status": "success", "capability_id": capability.id}
        else:
            raise HTTPException(status_code=500, detail="Failed to update capability")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating capability: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/domains/{domain_id}/capabilities/{capability_id}")
async def delete_capability(domain_id: str, capability_id: str):
    """Delete a capability from a domain."""
    try:
        existing = domain_manager.get_domain(domain_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Domain not found: {domain_id}")

        success = domain_manager.delete_capability(domain_id, capability_id)

        if success:
            return {"status": "success", "capability_id": capability_id}
        else:
            raise HTTPException(status_code=500, detail="Failed to delete capability")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting capability: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════
# ACTION REGISTRY ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/actions")
async def list_actions():
    """List all actions from the registry."""
    try:
        actions = action_manager.get_all_actions()
        result = []

        for action in actions:
            result.append({
                "capability_id": action["capability_id"],
                "domain_id": action["domain_id"],
                "description": action["description"],
                "has_route": action.get("route") is not None,
            })

        return {"actions": result}

    except Exception as e:
        logger.error(f"Error loading actions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/actions/{capability_id:path}")
async def get_action(capability_id: str):
    """Get detailed info for a specific action."""
    try:
        action = action_manager.get_action(capability_id)

        if not action:
            raise HTTPException(status_code=404, detail=f"Action not found: {capability_id}")

        return action

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading action {capability_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/actions")
async def create_action(action: ActionCreate):
    """Create a new action."""
    try:
        existing = action_manager.get_action(action.capability_id)
        if existing:
            raise HTTPException(status_code=400, detail=f"Action already exists: {action.capability_id}")

        action_dict = {
            "capability_id": action.capability_id,
            "domain_id": action.domain_id,
            "description": action.description,
            "notes": action.notes,
            "route": action.route.model_dump(),
            "input_fields": [f.model_dump() for f in action.input_fields],
            "response_mappings": [m.model_dump() for m in action.response_mappings],
        }

        success = action_manager.add_action(action_dict)

        if success:
            return {"status": "success", "capability_id": action.capability_id}
        else:
            raise HTTPException(status_code=500, detail="Failed to create action")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating action: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/actions/{capability_id:path}")
async def update_action(capability_id: str, action: ActionCreate):
    """Update an action."""
    try:
        existing = action_manager.get_action(capability_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Action not found: {capability_id}")

        action_dict = {
            "capability_id": action.capability_id,
            "domain_id": action.domain_id,
            "description": action.description,
            "notes": action.notes,
            "route": action.route.model_dump(),
            "input_fields": [f.model_dump() for f in action.input_fields],
            "response_mappings": [m.model_dump() for m in action.response_mappings],
        }

        success = action_manager.update_action(capability_id, action_dict)

        if success:
            return {"status": "success", "capability_id": action.capability_id}
        else:
            raise HTTPException(status_code=500, detail="Failed to update action")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating action: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/actions/{capability_id:path}")
async def delete_action(capability_id: str):
    """Delete an action."""
    try:
        existing = action_manager.get_action(capability_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Action not found: {capability_id}")

        success = action_manager.delete_action(capability_id)

        if success:
            return {"status": "success", "capability_id": capability_id}
        else:
            raise HTTPException(status_code=500, detail="Failed to delete action")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting action: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Input Field Endpoints ─────────────────────────────────────────────────

@router.post("/actions/{capability_id:path}/fields")
async def add_input_field(capability_id: str, field: InputFieldCreate):
    """Add an input field to an action."""
    try:
        existing = action_manager.get_action(capability_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Action not found: {capability_id}")

        success = action_manager.add_input_field(capability_id, field.model_dump())

        if success:
            return {"status": "success", "field_name": field.name}
        else:
            raise HTTPException(status_code=500, detail="Failed to add input field")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding input field: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/actions/{capability_id:path}/fields/{field_name}")
async def update_input_field(capability_id: str, field_name: str, field: InputFieldCreate):
    """Update an input field in an action."""
    try:
        existing = action_manager.get_action(capability_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Action not found: {capability_id}")

        success = action_manager.update_input_field(capability_id, field_name, field.model_dump())

        if success:
            return {"status": "success", "field_name": field.name}
        else:
            raise HTTPException(status_code=500, detail="Failed to update input field")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating input field: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/actions/{capability_id:path}/fields/{field_name}")
async def delete_input_field(capability_id: str, field_name: str):
    """Delete an input field from an action."""
    try:
        existing = action_manager.get_action(capability_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Action not found: {capability_id}")

        success = action_manager.delete_input_field(capability_id, field_name)

        if success:
            return {"status": "success", "field_name": field_name}
        else:
            raise HTTPException(status_code=500, detail="Failed to delete input field")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting input field: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Response Mapping Endpoints ─────────────────────────────────────────────

@router.post("/actions/{capability_id:path}/mappings")
async def add_response_mapping(capability_id: str, mapping: ResponseMappingCreate):
    """Add a response mapping to an action."""
    try:
        existing = action_manager.get_action(capability_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Action not found: {capability_id}")

        success = action_manager.add_response_mapping(capability_id, mapping.model_dump())

        if success:
            return {"status": "success", "output_name": mapping.output_name}
        else:
            raise HTTPException(status_code=500, detail="Failed to add response mapping")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding response mapping: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/actions/{capability_id:path}/mappings/{output_name}")
async def update_response_mapping(capability_id: str, output_name: str, mapping: ResponseMappingCreate):
    """Update a response mapping in an action."""
    try:
        existing = action_manager.get_action(capability_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Action not found: {capability_id}")

        success = action_manager.update_response_mapping(capability_id, output_name, mapping.model_dump())

        if success:
            return {"status": "success", "output_name": mapping.output_name}
        else:
            raise HTTPException(status_code=500, detail="Failed to update response mapping")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating response mapping: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/actions/{capability_id:path}/mappings/{output_name}")
async def delete_response_mapping(capability_id: str, output_name: str):
    """Delete a response mapping from an action."""
    try:
        existing = action_manager.get_action(capability_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Action not found: {capability_id}")

        success = action_manager.delete_response_mapping(capability_id, output_name)

        if success:
            return {"status": "success", "output_name": output_name}
        else:
            raise HTTPException(status_code=500, detail="Failed to delete response mapping")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting response mapping: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Field Corrections Endpoint ─────────────────────────────────────────────

@router.get("/field-corrections")
async def get_field_corrections():
    """Get the field name corrections dictionary."""
    try:
        nodes_file = CONFIG_FILES["nodes"]
        content = nodes_file.read_text(encoding="utf-8")

        pattern = r'field_corrections\s*=\s*\{([^}]+)\}'
        match = re.search(pattern, content)

        if match:
            dict_content = match.group(1)
            corrections = []

            for line in dict_content.split('\n'):
                item_match = re.search(r'"([^"]+)"\s*:\s*"([^"]+)"', line)
                if item_match:
                    corrections.append({
                        "wrong": item_match.group(1),
                        "correct": item_match.group(2),
                    })

            return {"corrections": corrections}

        return {"corrections": []}

    except Exception as e:
        logger.error(f"Error loading field corrections: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════
# AGENT REGISTRY ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/agents")
async def list_agents():
    """List all registered agents."""
    try:
        import sys
        if str(BUILDER_AGENT_DIR) not in sys.path:
            sys.path.insert(0, str(BUILDER_AGENT_DIR))

        from builder_agent.registry.agent_registry import get_all_agents

        agents = get_all_agents()
        return {
            "agents": [
                {
                    "id": agent.id,
                    "name": agent.name,
                    "description": agent.description,
                    "specializations": agent.specializations,
                    "protocol": agent.protocol,
                    "endpoint": agent.endpoint,
                    "timeout": agent.timeout,
                    "enabled": agent.enabled,
                    "metadata": agent.metadata,
                }
                for agent in agents
            ]
        }

    except Exception as e:
        logger.error(f"Error loading agents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str):
    """Get detailed info for a specific agent."""
    try:
        import sys
        if str(BUILDER_AGENT_DIR) not in sys.path:
            sys.path.insert(0, str(BUILDER_AGENT_DIR))

        from builder_agent.registry.agent_registry import get_agent

        agent = get_agent(agent_id)

        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

        return {
            "id": agent.id,
            "name": agent.name,
            "description": agent.description,
            "specializations": agent.specializations,
            "protocol": agent.protocol,
            "endpoint": agent.endpoint,
            "timeout": agent.timeout,
            "enabled": agent.enabled,
            "system_prompt": agent.system_prompt,
            "metadata": agent.metadata,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading agent {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agents/{agent_id}/test")
async def test_agent_connection(agent_id: str):
    """Test if an agent is reachable."""
    try:
        import sys
        if str(BUILDER_AGENT_DIR) not in sys.path:
            sys.path.insert(0, str(BUILDER_AGENT_DIR))

        from builder_agent.registry.agent_registry import get_agent
        from agent_communication.adapters.base import AdapterRegistry

        agent = get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

        adapter = AdapterRegistry.get(agent.protocol)
        if not adapter:
            return {
                "status": "error",
                "message": f"No adapter for protocol: {agent.protocol}",
                "healthy": False,
            }

        is_healthy = await adapter.check_health(agent.endpoint, timeout=5)

        return {
            "status": "ok" if is_healthy else "unreachable",
            "agent_id": agent_id,
            "endpoint": agent.endpoint,
            "healthy": is_healthy,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error testing agent {agent_id}: {e}")
        return {
            "status": "error",
            "message": str(e),
            "healthy": False,
        }


@router.post("/agents")
async def create_agent(agent: AgentCreate):
    """Create a new agent in the registry."""
    try:
        import sys
        if str(BUILDER_AGENT_DIR) not in sys.path:
            sys.path.insert(0, str(BUILDER_AGENT_DIR))

        from builder_agent.registry.agent_registry import get_agent, add_agent

        # Check if agent already exists
        existing = get_agent(agent.id)
        if existing:
            raise HTTPException(status_code=400, detail=f"Agent already exists: {agent.id}")

        success = add_agent(agent.model_dump())

        if success:
            return {"status": "success", "agent_id": agent.id}
        else:
            raise HTTPException(status_code=500, detail="Failed to create agent")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating agent: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/agents/{agent_id}")
async def update_agent(agent_id: str, agent: AgentCreate):
    """Update an existing agent in the registry."""
    try:
        import sys
        if str(BUILDER_AGENT_DIR) not in sys.path:
            sys.path.insert(0, str(BUILDER_AGENT_DIR))

        from builder_agent.registry.agent_registry import get_agent, update_agent as registry_update_agent

        existing = get_agent(agent_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

        success = registry_update_agent(agent_id, agent.model_dump())

        if success:
            return {"status": "success", "agent_id": agent_id}
        else:
            raise HTTPException(status_code=500, detail="Failed to update agent")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating agent: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str):
    """Delete an agent from the registry."""
    try:
        import sys
        if str(BUILDER_AGENT_DIR) not in sys.path:
            sys.path.insert(0, str(BUILDER_AGENT_DIR))

        from builder_agent.registry.agent_registry import get_agent, delete_agent as registry_delete_agent

        existing = get_agent(agent_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

        success = registry_delete_agent(agent_id)

        if success:
            return {"status": "success", "agent_id": agent_id}
        else:
            raise HTTPException(status_code=500, detail="Failed to delete agent")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting agent: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Health Check ───────────────────────────────────────────────────────────

@router.get("/health")
async def admin_health():
    """Check if admin routes are accessible and config files exist."""
    status = {}

    for key, path in CONFIG_FILES.items():
        status[key] = path.exists()

    return {
        "status": "ok",
        "files": status,
        "builder_agent_dir": str(BUILDER_AGENT_DIR),
        "builder_agent_exists": BUILDER_AGENT_DIR.exists(),
    }
