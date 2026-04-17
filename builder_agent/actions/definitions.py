"""
Builder Agent - Action Definitions (Layer 2)
==============================================
Maps capability IDs from the domain registry to concrete API routes,
input/output schemas, and execution metadata.

This is the abstraction layer between "what to do" (the planner) and
"how to do it" (the actual HTTP calls). The AI works with capability
names like "agents.create" and never needs to know the underlying
route is POST /add/agent with specific form fields.

Key design decisions:
    - FieldSchema uses typed fields with validation rules so the AI
      knows exactly what data it needs to provide.
    - RouteMapping supports both JSON and form-encoded payloads since
      AI Hub uses both patterns.
    - ActionDefinition can have multiple routes (a sequence) for
      capabilities that require multiple API calls.
    - ResponseMapping tells the system which fields to extract from
      API responses and expose as step outputs.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class FieldType(Enum):
    """Data types for action input/output fields."""
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    LIST = "list"
    DICT = "dict"
    FILE = "file"           # File upload
    ENUM = "enum"           # One of a set of choices
    REFERENCE = "reference"  # ID referencing another entity


class PayloadEncoding(Enum):
    """How the request body is encoded."""
    JSON = "json"
    FORM = "form"            # application/x-www-form-urlencoded
    MULTIPART = "multipart"  # multipart/form-data (file uploads)
    QUERY = "query"          # Parameters in URL query string
    NONE = "none"            # No body (GET requests with path params only)


@dataclass
class FieldSchema:
    """
    Schema for a single input or output field.

    The AI reads these to understand what data it needs to collect
    from the user or from previous step outputs.

    Examples:
        FieldSchema("name", FieldType.STRING, required=True,
                    description="Agent display name",
                    min_length=1, max_length=100)

        FieldSchema("is_data_agent", FieldType.BOOLEAN, required=False,
                    default=False, description="Whether this is a data agent")

        FieldSchema("connection_id", FieldType.REFERENCE, required=True,
                    reference_domain="connections",
                    description="Database connection to use")
    """
    name: str
    field_type: FieldType
    required: bool = True
    description: str = ""
    default: Any = None

    # String constraints
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None  # Regex pattern

    # Numeric constraints
    min_value: Optional[float] = None
    max_value: Optional[float] = None

    # Enum constraints
    choices: Optional[List[str]] = None

    # Reference constraints
    reference_domain: Optional[str] = None  # Which domain this ID references

    # List constraints
    item_type: Optional[FieldType] = None  # Type of items in a list

    # Field mapping - the actual key name in the API payload
    # if different from the schema field name
    api_field: Optional[str] = None

    def to_dict(self) -> dict:
        result = {
            "name": self.name,
            "type": self.field_type.value,
            "required": self.required,
            "description": self.description,
        }
        if self.default is not None:
            result["default"] = self.default
        if self.choices:
            result["choices"] = self.choices
        if self.reference_domain:
            result["reference_domain"] = self.reference_domain
        if self.api_field and self.api_field != self.name:
            result["api_field"] = self.api_field
        if self.min_length is not None:
            result["min_length"] = self.min_length
        if self.max_length is not None:
            result["max_length"] = self.max_length
        if self.min_value is not None:
            result["min_value"] = self.min_value
        if self.max_value is not None:
            result["max_value"] = self.max_value
        return result

    @property
    def effective_api_field(self) -> str:
        """The actual field name to use in the API payload."""
        return self.api_field if self.api_field else self.name


@dataclass
class ResponseMapping:
    """
    Describes how to extract useful data from an API response.

    After calling an API route, the system needs to know which parts
    of the response to keep and expose as step outputs.

    Examples:
        # Extract agent_id from JSON response {"id": 42, "name": "..."}
        ResponseMapping(
            output_name="agent_id",
            source_path="id",
            description="ID of the created agent"
        )

        # Extract from nested response {"data": {"document_id": "abc"}}
        ResponseMapping(
            output_name="document_id",
            source_path="data.document_id",
            description="ID of the uploaded document"
        )
    """
    output_name: str                  # Name used in step outputs
    source_path: str                  # Dot-notation path in response JSON
    description: str = ""
    field_type: FieldType = FieldType.STRING
    is_list: bool = False             # Whether this extracts a list of values

    def to_dict(self) -> dict:
        result = {
            "output_name": self.output_name,
            "source_path": self.source_path,
            "type": self.field_type.value,
        }
        if self.description:
            result["description"] = self.description
        if self.is_list:
            result["is_list"] = True
        return result


@dataclass
class RouteMapping:
    """
    Maps a single API call with its method, path, payload encoding,
    input fields, and response extraction.

    This is the atomic unit of execution — one HTTP request.

    Examples:
        RouteMapping(
            method="POST",
            path="/add/agent",
            encoding=PayloadEncoding.FORM,
            description="Create a new general agent",
            input_fields=[
                FieldSchema("description", FieldType.STRING, required=True,
                            description="Agent name/description"),
                FieldSchema("objective", FieldType.STRING, required=True,
                            description="Agent objective/purpose"),
            ],
            response_mappings=[
                ResponseMapping("agent_id", "id", "Created agent ID"),
            ],
        )
    """
    method: str                                   # GET, POST, PUT, DELETE
    path: str                                     # URL path (can contain <param> placeholders)
    encoding: PayloadEncoding = PayloadEncoding.JSON
    description: str = ""

    # Input schema
    input_fields: List[FieldSchema] = field(default_factory=list)

    # Path parameters (extracted from the path template)
    # e.g., path="/api/agents/<agent_id>" → path_params=["agent_id"]
    path_params: List[str] = field(default_factory=list)

    # Response handling
    response_mappings: List[ResponseMapping] = field(default_factory=list)
    success_status_codes: List[int] = field(default_factory=lambda: [200, 201, 202, 204])
    success_indicator: Optional[str] = None  # JSON path to check for success
    # e.g., "success" means check response["success"] == True

    # Execution hints
    requires_auth: bool = True
    is_idempotent: bool = False
    estimated_duration: str = "fast"  # "fast", "medium", "slow"

    def to_dict(self) -> dict:
        result = {
            "method": self.method,
            "path": self.path,
            "encoding": self.encoding.value,
            "description": self.description,
            "input_fields": [f.to_dict() for f in self.input_fields],
            "response_mappings": [r.to_dict() for r in self.response_mappings],
        }
        if self.path_params:
            result["path_params"] = self.path_params
        if self.success_indicator:
            result["success_indicator"] = self.success_indicator
        return result

    def get_field(self, name: str) -> Optional[FieldSchema]:
        """Find an input field by name."""
        for f in self.input_fields:
            if f.name == name:
                return f
        return None

    @property
    def required_fields(self) -> List[FieldSchema]:
        """Get all required input fields."""
        return [f for f in self.input_fields if f.required]

    @property
    def optional_fields(self) -> List[FieldSchema]:
        """Get all optional input fields."""
        return [f for f in self.input_fields if not f.required]

    def build_path(self, params: Dict[str, Any]) -> str:
        """
        Replace path parameter placeholders with actual values.
        e.g., "/api/agents/<agent_id>" + {"agent_id": 42} → "/api/agents/42"
        """
        result = self.path
        for param in self.path_params:
            placeholder = f"<{param}>"
            int_placeholder = f"<int:{param}>"
            str_placeholder = f"<string:{param}>"
            value = str(params.get(param, ""))
            result = result.replace(placeholder, value)
            result = result.replace(int_placeholder, value)
            result = result.replace(str_placeholder, value)
        return result


@dataclass
class ActionSequence:
    """
    Some capabilities require multiple API calls in order.

    For example, "Create an agent with tools" might need:
      1. POST /add/agent → get agent_id
      2. POST /save with tool assignments → configure tools

    Each step in the sequence can reference outputs from previous steps.
    """
    steps: List[RouteMapping] = field(default_factory=list)
    description: str = ""

    # How outputs chain between steps
    # Format: {"step_index.output_name": "next_step_index.input_field"}
    output_chains: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "steps": [s.to_dict() for s in self.steps],
            "description": self.description,
            "output_chains": self.output_chains,
        }


class ServiceTarget:
    """
    Identifies which microservice handles an action.

    This allows the executor to route requests to the correct
    microservice URL based on what the action does.
    """
    MAIN = "main"               # Main app (get_base_url)
    DOCUMENT_API = "document"   # Document processing service
    SCHEDULER_API = "scheduler" # Jobs/scheduling service
    VECTOR_API = "vector"       # Vector search service
    AGENT_API = "agent"         # Agent execution service
    KNOWLEDGE_API = "knowledge" # Knowledge/RAG service
    EXECUTOR_API = "executor"   # Workflow executor service
    MCP_GATEWAY = "mcp"         # MCP gateway service


@dataclass
class ActionDefinition:
    """
    The complete mapping from a capability ID to its execution details.

    This is the main data structure in Layer 2. It connects the planner's
    abstract capabilities to concrete API operations.

    An ActionDefinition can have either:
      - A single `primary_route` for simple CRUD operations
      - A `sequence` for multi-step operations
    (Never both — use sequence if multiple calls are needed.)

    Examples:
        # Simple capability: list agents
        ActionDefinition(
            capability_id="agents.list",
            domain_id="agents",
            description="Get all agents for the current user",
            primary_route=RouteMapping(
                method="GET",
                path="/api/agents/list",
                response_mappings=[
                    ResponseMapping("agents", "agents", is_list=True),
                ],
            ),
        )

        # Complex capability: create agent with tools
        ActionDefinition(
            capability_id="agents.create",
            domain_id="agents",
            sequence=ActionSequence(
                steps=[route1, route2],
                output_chains={"0.agent_id": "1.agent_id"},
            ),
        )
    """
    capability_id: str          # Must match a capability in the domain registry
    domain_id: str              # Domain this action belongs to
    description: str = ""

    # Target microservice for this action (defaults to main app)
    service: str = ServiceTarget.MAIN

    # Simple action (single API call)
    primary_route: Optional[RouteMapping] = None

    # Complex action (multiple API calls)
    sequence: Optional[ActionSequence] = None

    # Pre-conditions: capabilities that should be checked/run first
    # e.g., "agents.create" might want to suggest running "agents.list" first
    # to show the user what already exists
    suggested_prechecks: List[str] = field(default_factory=list)

    # Post-actions: capabilities to suggest after this one completes
    suggested_followups: List[str] = field(default_factory=list)

    # Discovery: how to look up existing entities for this capability
    # e.g., "agents.update" needs an agent_id, so discovery_capability="agents.list"
    discovery_capability: Optional[str] = None

    # Metadata
    is_destructive: bool = False     # True for delete operations
    requires_confirmation: bool = False  # True for destructive/irreversible ops
    required_role: Optional[int] = None  # Minimum user role: 1=User, 2=Developer, 3=Admin
    notes: str = ""                  # Additional context for the AI

    def to_dict(self) -> dict:
        result = {
            "capability_id": self.capability_id,
            "domain_id": self.domain_id,
            "description": self.description,
            "is_destructive": self.is_destructive,
            "requires_confirmation": self.requires_confirmation,
        }
        if self.required_role is not None:
            result["required_role"] = self.required_role
        if self.primary_route:
            result["route"] = self.primary_route.to_dict()
        if self.sequence:
            result["sequence"] = self.sequence.to_dict()
        if self.suggested_prechecks:
            result["suggested_prechecks"] = self.suggested_prechecks
        if self.suggested_followups:
            result["suggested_followups"] = self.suggested_followups
        if self.discovery_capability:
            result["discovery_capability"] = self.discovery_capability
        if self.notes:
            result["notes"] = self.notes
        return result

    @property
    def is_simple(self) -> bool:
        """Whether this is a single-route action."""
        return self.primary_route is not None and self.sequence is None

    @property
    def is_sequence(self) -> bool:
        """Whether this requires multiple API calls."""
        return self.sequence is not None

    @property
    def all_input_fields(self) -> List[FieldSchema]:
        """Get all input fields across all routes."""
        if self.primary_route:
            return list(self.primary_route.input_fields)
        if self.sequence:
            fields = []
            seen = set()
            for step in self.sequence.steps:
                for f in step.input_fields:
                    if f.name not in seen:
                        fields.append(f)
                        seen.add(f.name)
            return fields
        return []

    @property
    def all_response_mappings(self) -> List[ResponseMapping]:
        """Get all response mappings (outputs) across all routes."""
        if self.primary_route:
            return list(self.primary_route.response_mappings)
        if self.sequence:
            mappings = []
            for step in self.sequence.steps:
                mappings.extend(step.response_mappings)
            return mappings
        return []

    def validate(self) -> List[str]:
        """
        Basic self-validation. Returns list of error messages.
        """
        errors = []

        if not self.capability_id:
            errors.append("capability_id is required")
        if not self.domain_id:
            errors.append("domain_id is required")

        if not self.primary_route and not self.sequence:
            errors.append(
                f"Action '{self.capability_id}' must have either "
                f"primary_route or sequence"
            )

        if self.primary_route and self.sequence:
            errors.append(
                f"Action '{self.capability_id}' cannot have both "
                f"primary_route and sequence"
            )

        if self.primary_route:
            errors.extend(self._validate_route(self.primary_route))

        if self.sequence:
            if not self.sequence.steps:
                errors.append(
                    f"Action '{self.capability_id}' sequence has no steps"
                )
            for i, step in enumerate(self.sequence.steps):
                step_errors = self._validate_route(step)
                for err in step_errors:
                    errors.append(f"sequence[{i}]: {err}")

        return errors

    def _validate_route(self, route: RouteMapping) -> List[str]:
        """Validate a single route mapping."""
        errors = []
        if route.method not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
            errors.append(f"Invalid HTTP method: {route.method}")
        if not route.path:
            errors.append("Route path is required")
        if not route.path.startswith("/"):
            errors.append(f"Route path must start with /: {route.path}")

        # Check path params are declared
        import re
        path_placeholders = re.findall(
            r'<(?:int:|string:)?(\w+)>', route.path
        )
        for param in path_placeholders:
            if param not in route.path_params:
                errors.append(
                    f"Path placeholder '{param}' in {route.path} "
                    f"not declared in path_params"
                )

        return errors
