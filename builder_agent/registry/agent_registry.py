"""
Agent Registry
===============
Defines available AI agents that the Builder can communicate with
to accomplish specialized tasks.

Each agent has:
- id: Unique identifier
- name: Display name
- description: What the agent specializes in
- specializations: Keywords for matching tasks to agents
- protocol: Which communication adapter to use
- endpoint: How to reach the agent (URL or internal path)
- timeout: Max seconds to wait for a response
- system_prompt: Optional context to provide when starting conversations
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class AgentDefinition:
    """Defines an AI agent that can be delegated to."""
    id: str
    name: str
    description: str
    specializations: List[str]
    protocol: str  # "text_chat", "structured", etc.
    endpoint: str  # URL or internal identifier
    timeout: int = 120  # seconds
    system_prompt: Optional[str] = None
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
# AGENT DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════

def _workflow_agent() -> AgentDefinition:
    """
    Workflow Agent - specializes in creating and managing workflows.

    Uses the workflow_builder protocol which communicates via the WorkflowAgent's
    HTTP API. The endpoint should point to the base path (e.g., /api/workflow/builder).

    The WorkflowAgent is conversational and follows a phase-based flow:
    - Discovery: Understands what the user wants
    - Requirements: Gathers specifics (data sources, stakeholders, etc.)
    - Planning: Creates a numbered workflow plan
    - Building: Generates workflow_commands (JSON)
    - Refinement: Modifies existing workflows

    The adapter handles session management and command extraction.
    """
    return AgentDefinition(
        id="workflow_agent",
        name="Workflow Agent",
        description=(
            "Specializes in creating, modifying, and orchestrating workflows. "
            "Uses a conversational approach to gather requirements before building. "
            "Can design automation flows with triggers, conditions, and actions."
        ),
        specializations=[
            "workflow", "automation", "trigger", "orchestration",
            "flow", "process", "pipeline", "scheduling",
            "automate", "sequence", "step", "task"
        ],
        protocol="workflow_builder",  # Uses the specialized WorkflowBuilder adapter
        endpoint="/api/workflow/builder",  # Base path for WorkflowAgent HTTP API
        timeout=180,  # Workflow building can take longer
        system_prompt=None,  # WorkflowAgent manages its own prompts internally
        metadata={
            "version": "2.0",
            "category": "automation",
            "phases": ["discovery", "requirements", "planning", "building", "refinement"],
            "supports_commands": True,
            "supports_validation": True,
        }
    )


def _data_agent() -> AgentDefinition:
    """Data Agent - specializes in database connections and data operations."""
    return AgentDefinition(
        id="data_agent",
        name="Data Agent",
        description=(
            "Specializes in database connections, SQL queries, and data transformations. "
            "Can help design data pipelines and query optimization."
        ),
        specializations=[
            "database", "sql", "query", "data", "connection",
            "etl", "transformation", "schema", "table"
        ],
        protocol="text_chat",
        endpoint="/api/agents/data/chat",
        timeout=90,
        system_prompt=(
            "You are a Data Agent that helps with database operations and data management. "
            "You can design SQL queries, suggest schema improvements, and help with data pipelines."
        ),
        metadata={
            "version": "1.0",
            "category": "data",
        }
    )


def _report_agent() -> AgentDefinition:
    """Report Agent - specializes in creating reports and dashboards."""
    return AgentDefinition(
        id="report_agent",
        name="Report Agent",
        description=(
            "Specializes in creating reports, dashboards, and data visualizations. "
            "Can help design charts, metrics, and reporting layouts."
        ),
        specializations=[
            "report", "dashboard", "chart", "visualization",
            "metrics", "analytics", "kpi", "graph"
        ],
        protocol="text_chat",
        endpoint="/api/agents/report/chat",
        timeout=90,
        system_prompt=(
            "You are a Report Agent that helps create reports and dashboards. "
            "You understand data visualization best practices and can suggest effective chart types."
        ),
        metadata={
            "version": "1.0",
            "category": "reporting",
        }
    )


def _data_pipeline_agent() -> AgentDefinition:
    """
    Data Pipeline Agent - specializes in data transformation, quality,
    comparison, and cross-system data movement.

    Uses its own micro-service (builder_data) running on HOST_PORT + 70.
    Communicates via the text_chat protocol with its /api/chat SSE endpoint.
    """
    return AgentDefinition(
        id="data_pipeline_agent",
        name="Data Pipeline Agent",
        description=(
            "Specializes in data pipeline creation, data quality analysis, "
            "comparison, deduplication, cleansing, and cross-system data movement. "
            "Can design and execute ETL/ELT pipelines using existing connections."
        ),
        specializations=[
            "data pipeline", "etl", "data quality", "compare", "comparison",
            "deduplicate", "dedup", "cleanse", "clean", "scrub", "standardize",
            "transform", "column mapping", "type conversion", "data movement",
            "sync", "migration", "merge", "data transfer", "data compare",
            "data scrub", "data cleanse", "data profile", "data validate"
        ],
        protocol="text_chat",
        endpoint="/api/chat",
        timeout=300,  # Pipelines can take longer
        system_prompt=None,  # Manages its own prompts internally
        metadata={
            "version": "1.0",
            "category": "data",
            "port_offset": 70,
            "service": "builder_data",
        }
    )


# ═══════════════════════════════════════════════════════════════════════════
# REGISTRY ACCESS
# ═══════════════════════════════════════════════════════════════════════════

def get_all_agents() -> List[AgentDefinition]:
    """Get all registered agents (built-in + custom)."""
    # Start with built-in agents
    built_in = {
        "workflow_agent": _workflow_agent(),
        "data_agent": _data_agent(),
        "report_agent": _report_agent(),
        "data_pipeline_agent": _data_pipeline_agent(),
    }

    # Override with custom agents (custom agents take precedence)
    all_agents = {**built_in, **_custom_agents}

    return list(all_agents.values())


def get_agent(agent_id: str) -> Optional[AgentDefinition]:
    """Get a specific agent by ID."""
    # Check custom agents first (they override built-ins)
    if agent_id in _custom_agents:
        return _custom_agents[agent_id]

    # Check built-in agents
    for agent in get_all_agents():
        if agent.id == agent_id:
            return agent
    return None


def get_enabled_agents() -> List[AgentDefinition]:
    """Get only enabled agents."""
    return [agent for agent in get_all_agents() if agent.enabled]


def find_agents_by_specialization(keywords: List[str]) -> List[AgentDefinition]:
    """Find agents that match the given keywords."""
    keywords_lower = [k.lower() for k in keywords]
    matching = []

    for agent in get_enabled_agents():
        agent_specs = [s.lower() for s in agent.specializations]
        # Check if any keyword matches any specialization
        if any(kw in spec or spec in kw for kw in keywords_lower for spec in agent_specs):
            matching.append(agent)

    return matching


def get_agent_for_capability(capability_id: str) -> Optional[AgentDefinition]:
    """
    Determine if a capability should be delegated to an agent.
    Returns the agent if delegation is recommended, None otherwise.

    This is a simple keyword-based matching. Can be enhanced with
    more sophisticated logic or ML-based matching.
    """
    # Extract keywords from capability ID (e.g., "workflow.create_workflow" -> ["workflow", "create"])
    keywords = capability_id.replace(".", "_").split("_")

    matching_agents = find_agents_by_specialization(keywords)

    # Return the first matching agent (could be enhanced to rank by relevance)
    return matching_agents[0] if matching_agents else None


def get_best_agent_match(user_message: str) -> Tuple[Optional[AgentDefinition], int]:
    """
    Find the best matching agent for a user's message and return the score.

    This is the core matching logic used by both get_agent_for_request
    (with threshold) and fallback matching (any score >= 1).

    Args:
        user_message: The user's request text

    Returns:
        Tuple of (best_matching_agent, score) or (None, 0) if no match
    """
    if not user_message:
        return None, 0

    # Normalize the message for matching
    message_lower = user_message.lower()

    # Score each agent based on keyword matches
    best_agent = None
    best_score = 0

    for agent in get_enabled_agents():
        score = 0
        for spec in agent.specializations:
            spec_lower = spec.lower()
            # Exact word match gets higher score
            if f" {spec_lower} " in f" {message_lower} ":
                score += 2
            # Partial match gets lower score
            elif spec_lower in message_lower:
                score += 1

        if score > best_score:
            best_score = score
            best_agent = agent

    return best_agent, best_score


def get_agent_for_request(user_message: str) -> Optional[AgentDefinition]:
    """
    Analyze a user's message and determine if it should be delegated to an agent.

    This performs keyword matching against the user's request to find
    a specialized agent that can handle the task.

    Args:
        user_message: The user's request text

    Returns:
        AgentDefinition if a matching agent is found (score >= 2), None otherwise
    """
    agent, score = get_best_agent_match(user_message)

    # Only return an agent if we have a meaningful match (at least 2 points)
    if agent and score >= 2:
        return agent

    return None


# ═══════════════════════════════════════════════════════════════════════════
# MUTABLE AGENT STORAGE (for admin UI)
# ═══════════════════════════════════════════════════════════════════════════

# Custom agents added via the admin UI (in-memory storage)
_custom_agents: Dict[str, AgentDefinition] = {}


def add_agent(agent_data: Dict[str, Any]) -> bool:
    """
    Add a new agent to the registry.

    Note: This adds to in-memory storage. For persistence, consider
    saving to a JSON file or database.
    """
    try:
        agent = AgentDefinition(
            id=agent_data["id"],
            name=agent_data["name"],
            description=agent_data.get("description", ""),
            specializations=agent_data.get("specializations", []),
            protocol=agent_data.get("protocol", "text_chat"),
            endpoint=agent_data["endpoint"],
            timeout=agent_data.get("timeout", 120),
            system_prompt=agent_data.get("system_prompt"),
            enabled=agent_data.get("enabled", True),
            metadata=agent_data.get("metadata", {}),
        )
        _custom_agents[agent.id] = agent
        return True
    except Exception as e:
        print(f"Error adding agent: {e}")
        return False


def update_agent(agent_id: str, agent_data: Dict[str, Any]) -> bool:
    """Update an existing agent in the registry."""
    try:
        # Check if it's a custom agent
        if agent_id in _custom_agents:
            agent = AgentDefinition(
                id=agent_id,
                name=agent_data["name"],
                description=agent_data.get("description", ""),
                specializations=agent_data.get("specializations", []),
                protocol=agent_data.get("protocol", "text_chat"),
                endpoint=agent_data["endpoint"],
                timeout=agent_data.get("timeout", 120),
                system_prompt=agent_data.get("system_prompt"),
                enabled=agent_data.get("enabled", True),
                metadata=agent_data.get("metadata", {}),
            )
            _custom_agents[agent_id] = agent
            return True

        # For built-in agents, we can't modify them directly
        # (would need to override in custom agents)
        # For now, create a custom override
        agent = AgentDefinition(
            id=agent_id,
            name=agent_data["name"],
            description=agent_data.get("description", ""),
            specializations=agent_data.get("specializations", []),
            protocol=agent_data.get("protocol", "text_chat"),
            endpoint=agent_data["endpoint"],
            timeout=agent_data.get("timeout", 120),
            system_prompt=agent_data.get("system_prompt"),
            enabled=agent_data.get("enabled", True),
            metadata=agent_data.get("metadata", {}),
        )
        _custom_agents[agent_id] = agent
        return True

    except Exception as e:
        print(f"Error updating agent: {e}")
        return False


def delete_agent(agent_id: str) -> bool:
    """Delete an agent from the registry."""
    try:
        if agent_id in _custom_agents:
            del _custom_agents[agent_id]
            return True
        # Can't delete built-in agents, but we can disable them via update
        return False
    except Exception as e:
        print(f"Error deleting agent: {e}")
        return False
