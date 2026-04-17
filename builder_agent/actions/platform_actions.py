"""
Builder Agent - Platform Action Mappings
==========================================
Concrete ActionDefinition instances that map every capability in the
domain registry to its actual API route, payload format, and response
structure.

These are derived from the actual AI Hub codebase — the routes in
app.py, blueprint routes, form payloads, and JSON response formats.

NOTE: Legacy routes are mapped but marked. When routes are modernized,
update the mapping here and the rest of the system adjusts automatically.
"""

from .definitions import (
    ActionDefinition,
    ActionSequence,
    FieldSchema,
    FieldType,
    PayloadEncoding,
    ResponseMapping,
    RouteMapping,
    ServiceTarget,
)


def get_platform_actions() -> list:
    """
    Returns all platform action definitions.
    Call after domain registry is populated.
    """
    return [
        # Agents
        *_agent_actions(),
        # Workflows
        *_workflow_actions(),
        # Documents
        *_document_actions(),
        # Tools
        *_tool_actions(),
        # Connections
        *_connection_actions(),
        # Knowledge
        *_knowledge_actions(),
        # Integrations
        *_integration_actions(),
        # Environments
        *_environment_actions(),
        # Email
        *_email_actions(),
        # Jobs
        *_job_actions(),
        # Users
        *_user_actions(),
        # MCP
        *_mcp_actions(),
        # Schedules
        *_schedule_actions(),
    ]


# ═══════════════════════════════════════════════════════════════════════
# AGENTS
# ═══════════════════════════════════════════════════════════════════════

def _agent_actions() -> list:
    return [
        ActionDefinition(
            capability_id="agents.create",
            domain_id="agents",
            description="Create a new general AI agent",
            notes=(
                "Use agent_id=0 for new agents. The route handles both "
                "create and update based on agent_id value."
            ),
            primary_route=RouteMapping(
                method="POST",
                path="/add/agent",
                encoding=PayloadEncoding.JSON,
                description="Create or update a general agent",
                input_fields=[
                    FieldSchema(
                        "agent_id", FieldType.INTEGER, required=True,
                        default=0,
                        description="Set to 0 for new agent, existing ID for update",
                    ),
                    FieldSchema(
                        "agent_description", FieldType.STRING, required=True,
                        description="Agent display name",
                        min_length=1, max_length=200,
                    ),
                    FieldSchema(
                        "agent_objective", FieldType.STRING, required=True,
                        description="Agent objective/system prompt",
                        min_length=1,
                    ),
                    FieldSchema(
                        "agent_enabled", FieldType.BOOLEAN, required=True,
                        default=True,
                        description="Whether the agent is active",
                    ),
                    FieldSchema(
                        "tool_names", FieldType.LIST, required=True,
                        default=[],
                        item_type=FieldType.STRING,
                        description="List of custom tool names to assign",
                    ),
                    FieldSchema(
                        "core_tool_names", FieldType.LIST, required=True,
                        default=[],
                        item_type=FieldType.STRING,
                        description="List of core/built-in tool names to assign",
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "agent_id", "message",
                        description="ID of the created/updated agent",
                        field_type=FieldType.INTEGER,
                    ),
                ],
                success_indicator="status",
                success_status_codes=[200],
            ),
            suggested_prechecks=["agents.list"],
            suggested_followups=["knowledge.attach", "agents.assign_tools"],
        ),

        ActionDefinition(
            capability_id="agents.create_data_agent",
            domain_id="agents",
            description="Create a data agent for natural language database querying",
            primary_route=RouteMapping(
                method="POST",
                path="/add/data_agent",
                encoding=PayloadEncoding.JSON,
                description="Create or update a data agent",
                input_fields=[
                    FieldSchema(
                        "agent_id", FieldType.INTEGER, required=True,
                        default=0,
                        description="Set to 0 for new agent, existing ID for update",
                    ),
                    FieldSchema(
                        "agent_description", FieldType.STRING, required=True,
                        description="Data agent display name",
                    ),
                    FieldSchema(
                        "agent_objective", FieldType.STRING, required=True,
                        description="Agent objective for data querying",
                    ),
                    FieldSchema(
                        "agent_enabled", FieldType.BOOLEAN, required=True,
                        default=True,
                    ),
                    FieldSchema(
                        "connection_id", FieldType.REFERENCE, required=True,
                        reference_domain="connections",
                        description="Database connection for the agent to query",
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "agent_id", "message",
                        description="ID of the created data agent",
                        field_type=FieldType.INTEGER,
                    ),
                ],
                success_indicator="status",
            ),
            suggested_prechecks=["connections.list"],
            suggested_followups=["connections.discover_tables"],
        ),

        ActionDefinition(
            capability_id="agents.update",
            domain_id="agents",
            description="Update an existing agent's configuration",
            notes=(
                "Uses the same /add/agent route with a non-zero agent_id. "
                "The route detects update vs create by the agent_id value."
            ),
            primary_route=RouteMapping(
                method="POST",
                path="/add/agent",
                encoding=PayloadEncoding.JSON,
                description="Update an existing agent",
                input_fields=[
                    FieldSchema(
                        "agent_id", FieldType.REFERENCE, required=True,
                        reference_domain="agents",
                        description="ID of the agent to update",
                    ),
                    FieldSchema(
                        "agent_description", FieldType.STRING, required=True,
                        description="Updated agent name",
                    ),
                    FieldSchema(
                        "agent_objective", FieldType.STRING, required=True,
                        description="Updated agent objective",
                    ),
                    FieldSchema(
                        "agent_enabled", FieldType.BOOLEAN, required=True,
                    ),
                    FieldSchema(
                        "tool_names", FieldType.LIST, required=True,
                        default=[], item_type=FieldType.STRING,
                    ),
                    FieldSchema(
                        "core_tool_names", FieldType.LIST, required=True,
                        default=[], item_type=FieldType.STRING,
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "agent_id", "message",
                        field_type=FieldType.INTEGER,
                    ),
                ],
                success_indicator="status",
            ),
            discovery_capability="agents.list",
        ),

        ActionDefinition(
            capability_id="agents.delete",
            domain_id="agents",
            description="Delete an agent and all its associations",
            is_destructive=True,
            requires_confirmation=True,
            primary_route=RouteMapping(
                method="POST",
                path="/delete/agent",
                encoding=PayloadEncoding.JSON,
                description="Delete an agent",
                input_fields=[
                    FieldSchema(
                        "agent_id", FieldType.REFERENCE, required=True,
                        reference_domain="agents",
                        description="ID of the agent to delete",
                    ),
                ],
                response_mappings=[],
                success_indicator="status",
            ),
            discovery_capability="agents.list",
        ),

        ActionDefinition(
            capability_id="agents.list",
            domain_id="agents",
            description="Get all agents accessible to the current user (includes enabled/disabled status)",
            primary_route=RouteMapping(
                method="GET",
                path="/api/agents/summary",
                encoding=PayloadEncoding.NONE,
                description="List all agents with metadata (id, name, enabled, created_date)",
                input_fields=[],
                response_mappings=[
                    ResponseMapping(
                        "agents", "agents",
                        description="List of agent summary objects",
                        is_list=True,
                    ),
                ],
                success_indicator="status",
                is_idempotent=True,
            ),
        ),

        ActionDefinition(
            capability_id="agents.get",
            domain_id="agents",
            description="Get full details of a specific agent",
            primary_route=RouteMapping(
                method="GET",
                path="/get/agent_info",
                encoding=PayloadEncoding.NONE,
                description="Get all agent details (filter client-side by agent_id)",
                input_fields=[
                    FieldSchema(
                        "agent_id", FieldType.REFERENCE, required=True,
                        reference_domain="agents",
                        description="Agent ID to look up",
                    ),
                ],
                response_mappings=[
                    ResponseMapping("agents", ".", description="List of all agents with details"),
                ],
                is_idempotent=True,
            ),
            discovery_capability="agents.list",
        ),

        ActionDefinition(
            capability_id="agents.list_tools",
            domain_id="agents",
            description="List available tools for an agent",
            primary_route=RouteMapping(
                method="GET",
                path="/api/tools/by-category",
                encoding=PayloadEncoding.NONE,
                description="List all available tools",
                input_fields=[],
                response_mappings=[
                    ResponseMapping("tools", "tools", description="Tool details"),
                ],
                success_indicator="status",
                is_idempotent=True,
            ),
        ),

        ActionDefinition(
            capability_id="agents.assign_tools",
            domain_id="agents",
            description="Update tool assignments for an agent",
            notes=(
                "This uses the same /add/agent route — tool assignment "
                "is part of the agent save operation, not a separate endpoint."
            ),
            primary_route=RouteMapping(
                method="POST",
                path="/add/agent",
                encoding=PayloadEncoding.JSON,
                description="Update agent with new tool assignments",
                input_fields=[
                    FieldSchema(
                        "agent_id", FieldType.REFERENCE, required=True,
                        reference_domain="agents",
                    ),
                    FieldSchema(
                        "agent_description", FieldType.STRING, required=True,
                        description="Agent name (must be re-provided)",
                    ),
                    FieldSchema(
                        "agent_objective", FieldType.STRING, required=True,
                        description="Agent objective (must be re-provided)",
                    ),
                    FieldSchema(
                        "agent_enabled", FieldType.BOOLEAN, required=True,
                    ),
                    FieldSchema(
                        "tool_names", FieldType.LIST, required=True,
                        item_type=FieldType.STRING,
                        description="Custom tool names to assign",
                    ),
                    FieldSchema(
                        "core_tool_names", FieldType.LIST, required=True,
                        item_type=FieldType.STRING,
                        description="Core tool names to assign",
                    ),
                ],
                response_mappings=[
                    ResponseMapping("agent_id", "message", field_type=FieldType.INTEGER),
                ],
                success_indicator="status",
            ),
            discovery_capability="agents.get",
        ),

        ActionDefinition(
            capability_id="agents.export",
            domain_id="agents",
            description="Export an agent as a portable package",
            primary_route=RouteMapping(
                method="GET",
                path="/export/agent/<agent_id>",
                encoding=PayloadEncoding.NONE,
                description="Export agent as downloadable package",
                path_params=["agent_id"],
                input_fields=[
                    FieldSchema(
                        "agent_id", FieldType.REFERENCE, required=True,
                        reference_domain="agents",
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "package", "data",
                        description="Exported agent package data",
                    ),
                ],
                is_idempotent=True,
            ),
            discovery_capability="agents.list",
        ),

        ActionDefinition(
            capability_id="agents.import",
            domain_id="agents",
            description="Import an agent from an exported package",
            primary_route=RouteMapping(
                method="POST",
                path="/import/agent",
                encoding=PayloadEncoding.MULTIPART,
                description="Import agent from package file",
                input_fields=[
                    FieldSchema(
                        "file", FieldType.FILE, required=True,
                        description="Agent package file to import",
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "agent_id", "agent_id",
                        description="ID of the imported agent",
                        field_type=FieldType.INTEGER,
                    ),
                ],
            ),
        ),

        ActionDefinition(
            capability_id="agents.chat",
            domain_id="agents",
            description="Send a message to an agent and receive a response (works with both general and data agents)",
            primary_route=RouteMapping(
                method="POST",
                path="/api/agents/<agent_id>/chat",
                description="Send a chat message to any agent type",
                path_params=["agent_id"],
                input_fields=[
                    FieldSchema(
                        "agent_id", FieldType.REFERENCE, required=True,
                        reference_domain="agents",
                        description="The ID of the agent to chat with",
                    ),
                    FieldSchema(
                        "prompt", FieldType.STRING, required=True,
                        description="The message to send to the agent",
                    ),
                    FieldSchema(
                        "history", FieldType.STRING, required=False,
                        description="Chat history as JSON string (default: empty)",
                        default="[]",
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "response", "response",
                        description="The agent's response text",
                    ),
                ],
                success_indicator="status",
                is_idempotent=False,
            ),
        ),
    ]


# ═══════════════════════════════════════════════════════════════════════
# WORKFLOWS
# ═══════════════════════════════════════════════════════════════════════

def _workflow_actions() -> list:
    return [
        ActionDefinition(
            capability_id="workflows.create",
            domain_id="workflows",
            description="Create and save a new workflow",
            primary_route=RouteMapping(
                method="POST",
                path="/save/workflow",
                encoding=PayloadEncoding.JSON,
                description="Save a workflow definition",
                input_fields=[
                    FieldSchema(
                        "filename", FieldType.STRING, required=True,
                        description="Workflow filename (e.g., 'my_workflow.json')",
                        pattern=r"^[\w\-]+\.json$",
                    ),
                    FieldSchema(
                        "workflow", FieldType.DICT, required=True,
                        description=(
                            "Workflow definition with 'nodes' and 'connections' "
                            "arrays"
                        ),
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "file_path", "file_path",
                        description="Path where workflow was saved",
                    ),
                    ResponseMapping(
                        "database_version", "database_version",
                        description="Version number in database",
                    ),
                ],
                success_indicator="status",
            ),
        ),

        ActionDefinition(
            capability_id="workflows.update",
            domain_id="workflows",
            description="Update an existing workflow definition",
            notes="Same route as create — overwrites the existing file/record. "
                  "WARNING: This requires the COMPLETE workflow JSON definition. "
                  "For modifying individual nodes (changing SQL queries, alert text, "
                  "conditions, etc.), delegate to agent:workflow_agent instead — "
                  "the workflow agent can load and surgically edit existing workflows.",
            primary_route=RouteMapping(
                method="POST",
                path="/save/workflow",
                encoding=PayloadEncoding.JSON,
                description="Save updated workflow definition",
                input_fields=[
                    FieldSchema(
                        "filename", FieldType.STRING, required=True,
                        description="Existing workflow filename",
                    ),
                    FieldSchema(
                        "workflow", FieldType.DICT, required=True,
                        description="Updated workflow definition",
                    ),
                ],
                response_mappings=[
                    ResponseMapping("file_path", "file_path"),
                    ResponseMapping("database_version", "database_version"),
                ],
                success_indicator="status",
            ),
            discovery_capability="workflows.list",
        ),

        ActionDefinition(
            capability_id="workflows.delete",
            domain_id="workflows",
            description="Delete a workflow",
            is_destructive=True,
            requires_confirmation=True,
            primary_route=RouteMapping(
                method="DELETE",
                path="/delete/workflow/<workflow_id>",
                encoding=PayloadEncoding.NONE,
                description="Delete a workflow by ID",
                path_params=["workflow_id"],
                input_fields=[
                    FieldSchema(
                        "workflow_id", FieldType.REFERENCE, required=True,
                        reference_domain="workflows",
                    ),
                ],
                response_mappings=[],
                success_indicator="status",
            ),
            discovery_capability="workflows.list",
        ),

        ActionDefinition(
            capability_id="workflows.list",
            domain_id="workflows",
            description="Get all workflows",
            primary_route=RouteMapping(
                method="GET",
                path="/api/workflows/list",
                encoding=PayloadEncoding.NONE,
                description="List all workflows",
                input_fields=[],
                response_mappings=[
                    ResponseMapping(
                        "workflows", "workflows",
                        description="List of workflow summary objects (id, workflow_name, category)",
                        is_list=True,
                    ),
                ],
                is_idempotent=True,
            ),
        ),

        ActionDefinition(
            capability_id="workflows.get",
            domain_id="workflows",
            description="Get a specific workflow's full definition",
            primary_route=RouteMapping(
                method="GET",
                path="/get/workflow/<workflow_id>",
                encoding=PayloadEncoding.NONE,
                description="Get workflow details",
                path_params=["workflow_id"],
                input_fields=[
                    FieldSchema(
                        "workflow_id", FieldType.REFERENCE, required=True,
                        reference_domain="workflows",
                    ),
                ],
                response_mappings=[
                    ResponseMapping("workflow", "data", description="Workflow definition"),
                ],
                is_idempotent=True,
            ),
            discovery_capability="workflows.list",
        ),

        ActionDefinition(
            capability_id="workflows.execute",
            domain_id="workflows",
            description="Run a workflow",
            primary_route=RouteMapping(
                method="POST",
                path="/api/workflow/run",
                encoding=PayloadEncoding.JSON,
                description="Execute a workflow",
                input_fields=[
                    FieldSchema(
                        "workflow_id", FieldType.REFERENCE, required=True,
                        reference_domain="workflows",
                    ),
                    FieldSchema(
                        "input_data", FieldType.DICT, required=False,
                        default={},
                        description="Input variables for the workflow",
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "execution_id", "execution_id",
                        description="ID of the workflow execution",
                    ),
                ],
            ),
            discovery_capability="workflows.get",
        ),

        ActionDefinition(
            capability_id="workflows.monitor",
            domain_id="workflows",
            description="Get execution history and status for a specific workflow or all workflows",
            notes="Pass workflow_id to filter executions for a specific workflow. "
                  "Without workflow_id, returns all recent executions across all workflows.",
            primary_route=RouteMapping(
                method="GET",
                path="/api/workflow/executions",
                encoding=PayloadEncoding.NONE,
                description="List workflow executions with optional filters",
                input_fields=[
                    FieldSchema(
                        "workflow_id", FieldType.REFERENCE, required=False,
                        reference_domain="workflows",
                        description="Filter executions by workflow ID (resolve workflow name first)",
                    ),
                    FieldSchema(
                        "status", FieldType.STRING, required=False,
                        description="Filter by execution status (e.g., 'completed', 'failed', 'running')",
                    ),
                    FieldSchema(
                        "limit", FieldType.INTEGER, required=False,
                        description="Maximum number of executions to return (default: 50)",
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "executions", "executions",
                        description="List of execution records",
                        is_list=True,
                    ),
                ],
                is_idempotent=True,
            ),
            discovery_capability="workflows.list",
        ),

        ActionDefinition(
            capability_id="workflows.rename",
            domain_id="workflows",
            description="Rename a workflow",
            primary_route=RouteMapping(
                method="PUT",
                path="/api/workflows/<workflow_id>/rename",
                encoding=PayloadEncoding.JSON,
                description="Rename a workflow",
                path_params=["workflow_id"],
                input_fields=[
                    FieldSchema(
                        "workflow_id", FieldType.REFERENCE, required=True,
                        reference_domain="workflows",
                    ),
                    FieldSchema(
                        "name", FieldType.STRING, required=True,
                        description="New workflow name",
                    ),
                ],
                response_mappings=[],
                success_indicator="status",
            ),
            discovery_capability="workflows.list",
        ),
    ]


# ═══════════════════════════════════════════════════════════════════════
# DOCUMENTS
# ═══════════════════════════════════════════════════════════════════════

def _document_actions() -> list:
    """Document actions - all routed to Document API service."""
    return [
        ActionDefinition(
            capability_id="documents.list",
            domain_id="documents",
            description="Get all documents with filtering and search",
            service=ServiceTarget.MAIN,
            primary_route=RouteMapping(
                method="GET",
                path="/api/documents",
                encoding=PayloadEncoding.QUERY,
                description="List documents with optional filters",
                input_fields=[
                    FieldSchema(
                        "search", FieldType.STRING, required=False,
                        description="Search term to filter documents",
                    ),
                    FieldSchema(
                        "type", FieldType.STRING, required=False,
                        description="Filter by document type",
                    ),
                    FieldSchema(
                        "agent_id", FieldType.REFERENCE, required=False,
                        reference_domain="agents",
                        description="Filter by associated agent",
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "documents", "documents",
                        description="List of document objects",
                        is_list=True,
                    ),
                ],
                is_idempotent=True,
            ),
        ),

        ActionDefinition(
            capability_id="documents.update",
            domain_id="documents",
            description="Update document metadata",
            service=ServiceTarget.MAIN,
            primary_route=RouteMapping(
                method="PUT",
                path="/api/documents/<document_id>",
                encoding=PayloadEncoding.JSON,
                description="Update a document's metadata",
                path_params=["document_id"],
                input_fields=[
                    FieldSchema(
                        "document_id", FieldType.REFERENCE, required=True,
                        reference_domain="documents",
                    ),
                    FieldSchema(
                        "description", FieldType.STRING, required=False,
                        description="Updated document description",
                    ),
                ],
                response_mappings=[],
                success_indicator="status",
            ),
            discovery_capability="documents.list",
        ),

        ActionDefinition(
            capability_id="documents.delete",
            domain_id="documents",
            description="Delete a document and its vector embeddings",
            service=ServiceTarget.MAIN,
            is_destructive=True,
            requires_confirmation=True,
            primary_route=RouteMapping(
                method="DELETE",
                path="/api/documents/<document_id>",
                encoding=PayloadEncoding.NONE,
                description="Delete a document",
                path_params=["document_id"],
                input_fields=[
                    FieldSchema(
                        "document_id", FieldType.REFERENCE, required=True,
                        reference_domain="documents",
                    ),
                ],
                response_mappings=[],
                success_indicator="status",
            ),
            discovery_capability="documents.list",
        ),

        ActionDefinition(
            capability_id="documents.search",
            domain_id="documents",
            description="Search documents by content, filename, or document type. Returns matching documents with metadata.",
            service=ServiceTarget.MAIN,
            primary_route=RouteMapping(
                method="POST",
                path="/api/builder/documents/search",
                encoding=PayloadEncoding.JSON,
                description="Search documents by content similarity or keyword",
                input_fields=[
                    FieldSchema(
                        "query", FieldType.STRING, required=True,
                        description="Search query text (keywords, topic, or question)",
                    ),
                    FieldSchema(
                        "document_type", FieldType.STRING, required=False,
                        description="Optional: filter by document type (e.g. 'resume', 'lease_agreement')",
                    ),
                    FieldSchema(
                        "max_results", FieldType.INTEGER, required=False,
                        description="Maximum number of results to return (default: 20)",
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "results", "results",
                        description="Matching documents",
                        is_list=True,
                    ),
                    ResponseMapping(
                        "total_results", "total_results",
                        description="Total number of matching documents",
                    ),
                ],
                is_idempotent=True,
            ),
        ),

        ActionDefinition(
            capability_id="documents.reprocess",
            domain_id="documents",
            description="Reprocess document vector embeddings",
            service=ServiceTarget.MAIN,
            primary_route=RouteMapping(
                method="POST",
                path="/document/reprocess-vectors",
                encoding=PayloadEncoding.JSON,
                description="Reprocess vectors for a document",
                input_fields=[
                    FieldSchema(
                        "document_id", FieldType.REFERENCE, required=True,
                        reference_domain="documents",
                    ),
                ],
                response_mappings=[],
                success_indicator="status",
                estimated_duration="slow",
            ),
            discovery_capability="documents.list",
        ),
    ]


# ═══════════════════════════════════════════════════════════════════════
# TOOLS
# ═══════════════════════════════════════════════════════════════════════

def _tool_actions() -> list:
    return [
        ActionDefinition(
            capability_id="tools.create",
            domain_id="tools",
            description="Create a new custom Python tool",
            primary_route=RouteMapping(
                method="POST",
                path="/save",
                encoding=PayloadEncoding.JSON,
                description="Save a custom tool",
                input_fields=[
                    FieldSchema(
                        "name", FieldType.STRING, required=True,
                        description="Tool name (used as identifier)",
                    ),
                    FieldSchema(
                        "description", FieldType.STRING, required=True,
                        description="What the tool does",
                    ),
                    FieldSchema(
                        "params", FieldType.LIST, required=True,
                        default=[], item_type=FieldType.STRING,
                        description="Parameter names as a list of strings, e.g. ['temperature', 'unit']",
                    ),
                    FieldSchema(
                        "paramTypes", FieldType.LIST, required=True,
                        default=[], item_type=FieldType.STRING,
                        description="Parameter types as a list of strings matching params order, e.g. ['float', 'str']",
                    ),
                    FieldSchema(
                        "paramOptional", FieldType.LIST, required=False,
                        default=[], item_type=FieldType.BOOLEAN,
                        description="Whether each parameter is optional (list of booleans matching params order)",
                    ),
                    FieldSchema(
                        "paramDefault", FieldType.LIST, required=False,
                        default=[], item_type=FieldType.STRING,
                        description="Default values for optional parameters (list matching params order, use null for required params)",
                    ),
                    FieldSchema(
                        "modules", FieldType.LIST, required=False,
                        default=[], item_type=FieldType.STRING,
                        description="Python modules to import, e.g. ['import math', 'from datetime import datetime']",
                    ),
                    FieldSchema(
                        "code", FieldType.STRING, required=True,
                        description="Python function BODY only (not a full def statement). The system wraps this in a function automatically. Example: 'return (temperature * 9/5) + 32'",
                    ),
                    FieldSchema(
                        "output", FieldType.STRING, required=True,
                        description="Return type of the tool, e.g. 'float', 'str', 'dict', 'list'",
                    ),
                ],
                response_mappings=[],
                success_status_codes=[200],
            ),
        ),

        ActionDefinition(
            capability_id="tools.list",
            domain_id="tools",
            description="Get all available tools organized by category",
            primary_route=RouteMapping(
                method="GET",
                path="/api/tools/by-category",
                encoding=PayloadEncoding.NONE,
                description="List tools by category",
                input_fields=[],
                response_mappings=[
                    ResponseMapping(
                        "categories", "categories",
                        description="Tools organized by category",
                        is_list=True,
                    ),
                ],
                is_idempotent=True,
            ),
        ),

        ActionDefinition(
            capability_id="tools.delete",
            domain_id="tools",
            description="Delete a custom tool",
            is_destructive=True,
            requires_confirmation=True,
            primary_route=RouteMapping(
                method="DELETE",
                path="/delete_package/<package_name>",
                encoding=PayloadEncoding.NONE,
                description="Delete a custom tool package",
                path_params=["package_name"],
                input_fields=[
                    FieldSchema(
                        "package_name", FieldType.STRING, required=True,
                        description="Tool package name to delete",
                    ),
                ],
                response_mappings=[],
            ),
            discovery_capability="tools.list",
        ),
    ]


# ═══════════════════════════════════════════════════════════════════════
# CONNECTIONS
# ═══════════════════════════════════════════════════════════════════════

def _connection_actions() -> list:
    return [
        ActionDefinition(
            capability_id="connections.create",
            domain_id="connections",
            description="Create a new database connection",
            required_role=2,
            primary_route=RouteMapping(
                method="POST",
                path="/api/connections",
                encoding=PayloadEncoding.JSON,
                description="Create a database connection",
                input_fields=[
                    FieldSchema(
                        "name", FieldType.STRING, required=True,
                        description="Connection display name",
                    ),
                    FieldSchema(
                        "connection_type", FieldType.ENUM, required=True,
                        choices=[
                            "sql_server", "mysql", "postgresql",
                            "sqlite", "oracle", "odbc",
                        ],
                        description="Database type",
                    ),
                    FieldSchema(
                        "server", FieldType.STRING, required=True,
                        description="Database server hostname",
                    ),
                    FieldSchema(
                        "database", FieldType.STRING, required=True,
                        description="Database name",
                    ),
                    FieldSchema(
                        "username", FieldType.STRING, required=False,
                        description="Database username",
                    ),
                    FieldSchema(
                        "password", FieldType.STRING, required=False,
                        description="Database password",
                    ),
                    FieldSchema(
                        "port", FieldType.INTEGER, required=False,
                        default=1433,
                        description="Database port (default 1433 for SQL Server)",
                    ),
                    FieldSchema(
                        "use_trusted", FieldType.BOOLEAN, required=False,
                        default=False,
                        description="Use Windows trusted authentication",
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "connection_id", "connection.id",
                        field_type=FieldType.INTEGER,
                    ),
                ],
                success_indicator="status",
            ),
        ),

        ActionDefinition(
            capability_id="connections.list",
            domain_id="connections",
            description="Get all database connections",
            required_role=2,
            primary_route=RouteMapping(
                method="GET",
                path="/api/connections",
                encoding=PayloadEncoding.NONE,
                description="List all connections",
                input_fields=[],
                response_mappings=[
                    ResponseMapping(
                        "connections", "connections",
                        is_list=True,
                    ),
                ],
                is_idempotent=True,
            ),
        ),

        ActionDefinition(
            capability_id="connections.get",
            domain_id="connections",
            description="Get details of a specific connection",
            required_role=2,
            primary_route=RouteMapping(
                method="GET",
                path="/api/connections/<connection_id>",
                encoding=PayloadEncoding.NONE,
                path_params=["connection_id"],
                input_fields=[
                    FieldSchema(
                        "connection_id", FieldType.REFERENCE, required=True,
                        reference_domain="connections",
                    ),
                ],
                response_mappings=[
                    ResponseMapping("connection", "connection"),
                ],
                is_idempotent=True,
            ),
            discovery_capability="connections.list",
        ),

        ActionDefinition(
            capability_id="connections.update",
            domain_id="connections",
            description="Update a database connection",
            required_role=2,
            primary_route=RouteMapping(
                method="PUT",
                path="/api/connections/<connection_id>",
                encoding=PayloadEncoding.JSON,
                path_params=["connection_id"],
                input_fields=[
                    FieldSchema(
                        "connection_id", FieldType.REFERENCE, required=True,
                        reference_domain="connections",
                    ),
                    FieldSchema("name", FieldType.STRING, required=False),
                    FieldSchema("server", FieldType.STRING, required=False),
                    FieldSchema("database", FieldType.STRING, required=False),
                    FieldSchema("username", FieldType.STRING, required=False),
                    FieldSchema("password", FieldType.STRING, required=False),
                ],
                response_mappings=[],
                success_indicator="status",
            ),
            discovery_capability="connections.list",
        ),

        ActionDefinition(
            capability_id="connections.delete",
            domain_id="connections",
            description="Delete a database connection",
            is_destructive=True,
            requires_confirmation=True,
            required_role=2,
            primary_route=RouteMapping(
                method="DELETE",
                path="/api/connections/<connection_id>",
                encoding=PayloadEncoding.NONE,
                path_params=["connection_id"],
                input_fields=[
                    FieldSchema(
                        "connection_id", FieldType.REFERENCE, required=True,
                        reference_domain="connections",
                    ),
                ],
                response_mappings=[],
            ),
            discovery_capability="connections.list",
        ),

        ActionDefinition(
            capability_id="connections.test",
            domain_id="connections",
            description="Test a database connection",
            required_role=2,
            primary_route=RouteMapping(
                method="POST",
                path="/api/connections/<connection_id>/test",
                encoding=PayloadEncoding.NONE,
                path_params=["connection_id"],
                input_fields=[
                    FieldSchema(
                        "connection_id", FieldType.REFERENCE, required=True,
                        reference_domain="connections",
                    ),
                ],
                response_mappings=[
                    ResponseMapping("connected", "connected", field_type=FieldType.BOOLEAN),
                ],
                success_indicator="status",
                is_idempotent=True,
            ),
            discovery_capability="connections.list",
        ),

        ActionDefinition(
            capability_id="connections.query",
            domain_id="connections",
            description="Execute a SQL query against a connection",
            required_role=2,
            primary_route=RouteMapping(
                method="POST",
                path="/api/connections/<connection_id>/execute",
                encoding=PayloadEncoding.JSON,
                path_params=["connection_id"],
                input_fields=[
                    FieldSchema(
                        "connection_id", FieldType.REFERENCE, required=True,
                        reference_domain="connections",
                    ),
                    FieldSchema(
                        "query", FieldType.STRING, required=True,
                        description="SQL query to execute",
                    ),
                ],
                response_mappings=[
                    ResponseMapping("rows", "response.rows", is_list=True),
                    ResponseMapping("columns", "response.columns", is_list=True),
                ],
                success_indicator="status",
            ),
            discovery_capability="connections.list",
        ),

        # ── Data Dictionary ──────────────────────────────────────────
        ActionDefinition(
            capability_id="connections.discover_tables",
            domain_id="connections",
            description="Auto-discover tables from a database connection",
            required_role=2,
            primary_route=RouteMapping(
                method="GET",
                path="/api/discover/tables/<connection_id>",
                encoding=PayloadEncoding.NONE,
                path_params=["connection_id"],
                description="Discover all tables from the connected database",
                is_idempotent=True,
                input_fields=[
                    FieldSchema(
                        "connection_id", FieldType.REFERENCE, required=True,
                        reference_domain="connections",
                        description="Connection to discover tables from",
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "tables", "tables",
                        description="List of discovered tables with names, schemas, and documentation status",
                        field_type=FieldType.LIST,
                        is_list=True,
                    ),
                ],
                success_indicator="success",
            ),
            suggested_prechecks=["connections.test"],
            suggested_followups=["connections.analyze_tables"],
            notes=(
                "Returns tables with TABLE_NAME, TABLE_SCHEMA, TABLE_TYPE, "
                "column_count, and is_documented flag. Present the table list "
                "to the user and let them choose which tables to analyze."
            ),
        ),

        ActionDefinition(
            capability_id="connections.analyze_tables",
            domain_id="connections",
            description="AI-analyze selected tables to populate the data dictionary",
            required_role=2,
            primary_route=RouteMapping(
                method="POST",
                path="/api/ai/analyze-tables-batch",
                encoding=PayloadEncoding.JSON,
                description="Start background AI analysis of selected tables",
                input_fields=[
                    FieldSchema(
                        "connection_id", FieldType.REFERENCE, required=True,
                        reference_domain="connections",
                        description="Connection the tables belong to",
                    ),
                    FieldSchema(
                        "table_names", FieldType.LIST, required=True,
                        description="List of table names to analyze (e.g. ['dbo.Orders', 'dbo.Products'])",
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "task_id", "task_id",
                        description="Background task ID for tracking progress",
                        field_type=FieldType.STRING,
                    ),
                ],
                success_indicator="success",
            ),
            notes=(
                "Analysis runs in the background. Returns a task_id immediately. "
                "Each table takes 10-30 seconds to analyze with AI. The data "
                "dictionary will be populated automatically when analysis completes. "
                "Do NOT wait for completion — inform the user that analysis is "
                "running in the background and the data agent will be ready shortly."
            ),
        ),

        ActionDefinition(
            capability_id="connections.check_analysis_progress",
            domain_id="connections",
            description="Check progress of a background data dictionary analysis",
            required_role=2,
            primary_route=RouteMapping(
                method="GET",
                path="/api/ai/progress/<task_id>",
                encoding=PayloadEncoding.NONE,
                path_params=["task_id"],
                is_idempotent=True,
                description="Get progress of background table analysis",
                input_fields=[
                    FieldSchema(
                        "task_id", FieldType.STRING, required=True,
                        description="Task ID returned from analyze_tables",
                    ),
                ],
                response_mappings=[
                    ResponseMapping("current", "current", field_type=FieldType.INTEGER),
                    ResponseMapping("total", "total", field_type=FieldType.INTEGER),
                    ResponseMapping("status", "status", field_type=FieldType.STRING),
                    ResponseMapping("results", "results", field_type=FieldType.LIST, is_list=True),
                    ResponseMapping("errors", "errors", field_type=FieldType.LIST, is_list=True),
                ],
                success_indicator="success",
            ),
            notes=(
                "Poll this to check analysis progress. Status values: "
                "processing, completed, failed. Only use if the user asks "
                "about progress — do not poll automatically."
            ),
        ),
    ]


# ═══════════════════════════════════════════════════════════════════════
# KNOWLEDGE
# ═══════════════════════════════════════════════════════════════════════

def _knowledge_actions() -> list:
    return [
        ActionDefinition(
            capability_id="knowledge.attach",
            domain_id="knowledge",
            description="Upload and attach a document as agent knowledge",
            primary_route=RouteMapping(
                method="POST",
                path="/add/agent_knowledge",
                encoding=PayloadEncoding.MULTIPART,
                description="Upload file and attach as knowledge to an agent",
                input_fields=[
                    FieldSchema(
                        "agent_id", FieldType.REFERENCE, required=True,
                        reference_domain="agents",
                        description="Agent to attach knowledge to",
                    ),
                    FieldSchema(
                        "file", FieldType.FILE, required=True,
                        description="Document file to attach. Accepts: a File ID from a prior upload, OR a full filesystem path (e.g., 'C:\\\\path\\\\to\\\\document.txt'). If the user provides a filesystem path, use it directly.",
                    ),
                    FieldSchema(
                        "description", FieldType.STRING, required=False,
                        default="",
                        description="Description of the knowledge document",
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "knowledge_id", "knowledge_id",
                        description="ID of the created knowledge entry",
                        field_type=FieldType.INTEGER,
                    ),
                ],
                success_indicator="status",
                estimated_duration="slow",
            ),
            suggested_prechecks=["agents.list"],
        ),

        ActionDefinition(
            capability_id="knowledge.list",
            domain_id="knowledge",
            description="Get all knowledge documents for an agent",
            primary_route=RouteMapping(
                method="GET",
                path="/get/agent_knowledge/<agent_id>",
                encoding=PayloadEncoding.NONE,
                path_params=["agent_id"],
                input_fields=[
                    FieldSchema(
                        "agent_id", FieldType.REFERENCE, required=True,
                        reference_domain="agents",
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "knowledge_items", "data",
                        is_list=True,
                    ),
                ],
                is_idempotent=True,
            ),
            discovery_capability="agents.list",
        ),

        ActionDefinition(
            capability_id="knowledge.detach",
            domain_id="knowledge",
            description="Remove a knowledge document from an agent",
            is_destructive=True,
            requires_confirmation=True,
            primary_route=RouteMapping(
                method="POST",
                path="/delete/agent_knowledge/<knowledge_id>",
                encoding=PayloadEncoding.NONE,
                path_params=["knowledge_id"],
                input_fields=[
                    FieldSchema(
                        "knowledge_id", FieldType.REFERENCE, required=True,
                        reference_domain="knowledge",
                        description="Knowledge entry ID to remove",
                    ),
                ],
                response_mappings=[],
                success_indicator="status",
            ),
            discovery_capability="knowledge.list",
        ),
    ]


# ═══════════════════════════════════════════════════════════════════════
# INTEGRATIONS
# ═══════════════════════════════════════════════════════════════════════

def _integration_actions() -> list:
    return [
        ActionDefinition(
            capability_id="integrations.list",
            domain_id="integrations",
            description="Get all configured integrations",
            required_role=2,
            primary_route=RouteMapping(
                method="GET",
                path="/api/integrations",
                encoding=PayloadEncoding.NONE,
                input_fields=[],
                response_mappings=[
                    ResponseMapping("integrations", "integrations", is_list=True),
                ],
                is_idempotent=True,
            ),
        ),

        ActionDefinition(
            capability_id="integrations.create",
            domain_id="integrations",
            description="Create a new integration from a template",
            required_role=2,
            primary_route=RouteMapping(
                method="POST",
                path="/api/integrations",
                encoding=PayloadEncoding.JSON,
                input_fields=[
                    FieldSchema(
                        "template_key", FieldType.STRING, required=True,
                        description="Integration template identifier (e.g., 'stripe', 'hubspot', 'slack')",
                    ),
                    FieldSchema(
                        "integration_name", FieldType.STRING, required=True,
                        description="Display name for this integration",
                    ),
                    FieldSchema(
                        "credentials", FieldType.DICT, required=True,
                        description="Authentication credentials (e.g., {'api_key': '...'} for bearer/api_key auth)",
                    ),
                    FieldSchema(
                        "instance_config", FieldType.DICT, required=False,
                        description="Optional instance-specific configuration (e.g., {'realmId': '123'} for QuickBooks)",
                    ),
                    FieldSchema(
                        "description", FieldType.STRING, required=False,
                        description="Optional description of this integration instance",
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "integration_id", "integration_id",
                        field_type=FieldType.INTEGER,
                    ),
                ],
                success_indicator="status",
            ),
            suggested_prechecks=["integrations.list_templates"],
            suggested_followups=["integrations.test"],
            notes=(
                "Use integrations.list_templates first to find the correct template_key. "
                "Common templates: 'stripe' (bearer auth, api_key), 'hubspot' (bearer auth, api_key), "
                "'slack' (bearer auth, bot_token), 'shopify' (bearer auth, access_token), "
                "'quickbooks_online' (OAuth2), 'netsuite' (OAuth1 TBA), 'azure_blob_storage' "
                "(cloud_storage, connection_string). Credentials dict keys vary by template — "
                "check the template's auth_config for the expected field names."
            ),
        ),

        ActionDefinition(
            capability_id="integrations.delete",
            domain_id="integrations",
            description="Delete an integration",
            is_destructive=True,
            requires_confirmation=True,
            required_role=2,
            primary_route=RouteMapping(
                method="DELETE",
                path="/api/integrations/<integration_id>",
                encoding=PayloadEncoding.NONE,
                path_params=["integration_id"],
                input_fields=[
                    FieldSchema(
                        "integration_id", FieldType.REFERENCE, required=True,
                        reference_domain="integrations",
                    ),
                ],
                response_mappings=[],
            ),
            discovery_capability="integrations.list",
        ),

        ActionDefinition(
            capability_id="integrations.test",
            domain_id="integrations",
            description="Test an integration connection",
            required_role=2,
            primary_route=RouteMapping(
                method="POST",
                path="/api/integrations/<integration_id>/test",
                encoding=PayloadEncoding.NONE,
                path_params=["integration_id"],
                input_fields=[
                    FieldSchema(
                        "integration_id", FieldType.REFERENCE, required=True,
                        reference_domain="integrations",
                    ),
                ],
                response_mappings=[
                    ResponseMapping("connected", "connected", field_type=FieldType.BOOLEAN),
                ],
                is_idempotent=True,
            ),
            discovery_capability="integrations.list",
        ),

        ActionDefinition(
            capability_id="integrations.list_templates",
            domain_id="integrations",
            description="Get available integration templates",
            required_role=2,
            primary_route=RouteMapping(
                method="GET",
                path="/api/integrations/templates",
                encoding=PayloadEncoding.NONE,
                input_fields=[],
                response_mappings=[
                    ResponseMapping("templates", "templates", is_list=True),
                ],
                is_idempotent=True,
            ),
        ),

        ActionDefinition(
            capability_id="integrations.update",
            domain_id="integrations",
            description="Update an existing integration's name, description, config, or credentials",
            required_role=2,
            primary_route=RouteMapping(
                method="PUT",
                path="/api/integrations/<integration_id>",
                encoding=PayloadEncoding.JSON,
                path_params=["integration_id"],
                input_fields=[
                    FieldSchema(
                        "integration_id", FieldType.REFERENCE, required=True,
                        reference_domain="integrations",
                        description="ID of the integration to update",
                    ),
                    FieldSchema(
                        "integration_name", FieldType.STRING, required=False,
                        description="New display name for the integration",
                    ),
                    FieldSchema(
                        "description", FieldType.STRING, required=False,
                        description="Updated description",
                    ),
                    FieldSchema(
                        "instance_config", FieldType.DICT, required=False,
                        description="Updated instance-specific configuration",
                    ),
                    FieldSchema(
                        "credentials", FieldType.DICT, required=False,
                        description="Updated credentials (stored securely)",
                    ),
                ],
                response_mappings=[
                    ResponseMapping("message", "message"),
                ],
                success_indicator="status",
            ),
            discovery_capability="integrations.list",
        ),

        ActionDefinition(
            capability_id="integrations.list_operations",
            domain_id="integrations",
            description="Get available operations for a configured integration",
            required_role=2,
            primary_route=RouteMapping(
                method="GET",
                path="/api/integrations/<integration_id>/operations",
                encoding=PayloadEncoding.NONE,
                path_params=["integration_id"],
                input_fields=[
                    FieldSchema(
                        "integration_id", FieldType.REFERENCE, required=True,
                        reference_domain="integrations",
                        description="ID of the integration to get operations for",
                    ),
                ],
                response_mappings=[
                    ResponseMapping("operations", "operations", is_list=True),
                ],
                is_idempotent=True,
            ),
            discovery_capability="integrations.list",
        ),

        ActionDefinition(
            capability_id="integrations.execute_operation",
            domain_id="integrations",
            description="Execute an operation on a configured integration (e.g., fetch data, create record)",
            required_role=2,
            primary_route=RouteMapping(
                method="POST",
                path="/api/integrations/<integration_id>/execute",
                encoding=PayloadEncoding.JSON,
                path_params=["integration_id"],
                input_fields=[
                    FieldSchema(
                        "integration_id", FieldType.REFERENCE, required=True,
                        reference_domain="integrations",
                        description="ID of the integration to execute against",
                    ),
                    FieldSchema(
                        "operation", FieldType.STRING, required=True,
                        description="Operation key to execute (e.g., 'get_customers', 'run_suiteql')",
                    ),
                    FieldSchema(
                        "parameters", FieldType.DICT, required=False,
                        description="Operation-specific parameters",
                    ),
                ],
                response_mappings=[
                    ResponseMapping("data", "data"),
                    ResponseMapping("response_time_ms", "response_time_ms",
                                    field_type=FieldType.INTEGER),
                ],
                success_indicator="status",
            ),
            suggested_prechecks=["integrations.list_operations"],
            discovery_capability="integrations.list",
        ),
    ]


# ═══════════════════════════════════════════════════════════════════════
# ENVIRONMENTS
# ═══════════════════════════════════════════════════════════════════════

def _environment_actions() -> list:
    return [
        ActionDefinition(
            capability_id="environments.get",
            domain_id="environments",
            description="Get an agent's environment configuration",
            required_role=2,
            primary_route=RouteMapping(
                method="GET",
                path="/api/agents/<agent_id>/environment",
                encoding=PayloadEncoding.NONE,
                path_params=["agent_id"],
                input_fields=[
                    FieldSchema(
                        "agent_id", FieldType.REFERENCE, required=True,
                        reference_domain="agents",
                    ),
                ],
                response_mappings=[
                    ResponseMapping("environment", "environment"),
                ],
                is_idempotent=True,
            ),
            discovery_capability="agents.list",
        ),

        ActionDefinition(
            capability_id="environments.assign",
            domain_id="environments",
            description="Assign an environment to an agent",
            required_role=2,
            primary_route=RouteMapping(
                method="POST",
                path="/api/agents/<agent_id>/environment",
                encoding=PayloadEncoding.JSON,
                path_params=["agent_id"],
                input_fields=[
                    FieldSchema(
                        "agent_id", FieldType.REFERENCE, required=True,
                        reference_domain="agents",
                    ),
                    FieldSchema(
                        "environment_name", FieldType.STRING, required=True,
                        description="Name of the environment to assign",
                    ),
                    FieldSchema(
                        "packages", FieldType.LIST, required=False,
                        default=[], item_type=FieldType.STRING,
                        description="Python packages to install",
                    ),
                ],
                response_mappings=[],
                success_indicator="status",
            ),
            discovery_capability="agents.list",
        ),

        ActionDefinition(
            capability_id="environments.status",
            domain_id="environments",
            description="Get status of all environments",
            required_role=2,
            primary_route=RouteMapping(
                method="GET",
                path="/api/environments/status",
                encoding=PayloadEncoding.NONE,
                input_fields=[],
                response_mappings=[
                    ResponseMapping("environments", "environments", is_list=True),
                ],
                is_idempotent=True,
            ),
        ),
    ]


# ═══════════════════════════════════════════════════════════════════════
# EMAIL
# ═══════════════════════════════════════════════════════════════════════

def _email_actions() -> list:
    """Email provisioning and configuration actions.

    These actions create and manage dedicated email addresses for agents,
    enabling both sending and receiving. For simple send-only capability,
    assign the send_email_message tool via agents.create or agents.assign_tools.
    """
    return [
        ActionDefinition(
            capability_id="email.provision",
            domain_id="email",
            description="Provision a NEW email address for an agent that does NOT yet have one. Only use for first-time setup. To update settings on an existing email, use email.configure instead.",
            primary_route=RouteMapping(
                method="POST",
                path="/api/agents/<agent_id>/email/provision",
                encoding=PayloadEncoding.JSON,
                path_params=["agent_id"],
                input_fields=[
                    FieldSchema(
                        "agent_id", FieldType.REFERENCE, required=True,
                        reference_domain="agents",
                        description="Agent to provision email for",
                    ),
                    FieldSchema(
                        "from_name", FieldType.STRING, required=False,
                        default="AI Agent",
                        description="Display name for outgoing emails",
                    ),
                    FieldSchema(
                        "inbound_enabled", FieldType.BOOLEAN, required=False,
                        default=False,
                        description="Enable receiving inbound emails",
                    ),
                    FieldSchema(
                        "auto_respond_enabled", FieldType.BOOLEAN, required=False,
                        default=False,
                        description="Enable AI auto-response to incoming emails",
                    ),
                    FieldSchema(
                        "auto_respond_style", FieldType.ENUM, required=False,
                        choices=["professional", "friendly", "formal", "default"],
                        default="professional",
                        description="Style/tone for auto-responses",
                    ),
                    FieldSchema(
                        "inbox_tools_enabled", FieldType.BOOLEAN, required=False,
                        default=False,
                        description="Give agent tools to check inbox and reply to emails",
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "email_address", "email_address",
                        description="The provisioned email address",
                        field_type=FieldType.STRING,
                    ),
                ],
                success_indicator="status",
            ),
            suggested_prechecks=["agents.list"],
        ),

        ActionDefinition(
            capability_id="email.configure",
            domain_id="email",
            description="Update email settings for an agent that ALREADY has email provisioned. Use for changing auto-response, inbox tools, workflow triggers, from_name, or active status. If asked to 'configure', 'update', or 'change' email settings, use this action.",
            primary_route=RouteMapping(
                method="POST",
                path="/api/agent-email/config/<agent_id>",
                encoding=PayloadEncoding.JSON,
                path_params=["agent_id"],
                input_fields=[
                    FieldSchema(
                        "agent_id", FieldType.REFERENCE, required=True,
                        reference_domain="agents",
                        description="Agent whose email to configure",
                    ),
                    FieldSchema(
                        "email_prefix", FieldType.STRING, required=False,
                        description="Email prefix (lowercase, alphanumeric and hyphens)",
                    ),
                    FieldSchema(
                        "from_name", FieldType.STRING, required=False,
                        default="",
                        description="Display name for outgoing emails",
                    ),
                    FieldSchema(
                        "is_active", FieldType.BOOLEAN, required=False,
                        default=True,
                        description="Whether email is active",
                    ),
                    FieldSchema(
                        "inbound_enabled", FieldType.BOOLEAN, required=False,
                        default=False,
                        description="Enable receiving inbound emails",
                    ),
                    FieldSchema(
                        "auto_respond_enabled", FieldType.BOOLEAN, required=False,
                        default=False,
                        description="Enable AI auto-response to incoming emails",
                    ),
                    FieldSchema(
                        "auto_respond_style", FieldType.ENUM, required=False,
                        choices=["professional", "friendly", "formal", "default"],
                        default="professional",
                        description="Style of auto-responses",
                    ),
                    FieldSchema(
                        "inbox_tools_enabled", FieldType.BOOLEAN, required=False,
                        default=False,
                        description="Give agent tools to check inbox and reply to emails",
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "email_address", "email_address",
                        description="The configured email address",
                        field_type=FieldType.STRING,
                    ),
                ],
                success_indicator="status",
            ),
            suggested_prechecks=["email.get"],
        ),

        ActionDefinition(
            capability_id="email.get",
            domain_id="email",
            description="Get email configuration for an agent",
            primary_route=RouteMapping(
                method="GET",
                path="/api/agent-email/config/<agent_id>",
                encoding=PayloadEncoding.QUERY,
                path_params=["agent_id"],
                input_fields=[
                    FieldSchema(
                        "agent_id", FieldType.REFERENCE, required=True,
                        reference_domain="agents",
                        description="Agent to get email config for",
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "config", "config",
                        description="Email configuration object",
                        field_type=FieldType.STRING,
                    ),
                ],
                success_indicator="status",
                is_idempotent=True,
            ),
        ),

        ActionDefinition(
            capability_id="email.deprovision",
            domain_id="email",
            description="Remove email configuration from an agent",
            is_destructive=True,
            requires_confirmation=True,
            primary_route=RouteMapping(
                method="DELETE",
                path="/api/agent-email/config/<agent_id>",
                encoding=PayloadEncoding.QUERY,
                path_params=["agent_id"],
                input_fields=[
                    FieldSchema(
                        "agent_id", FieldType.REFERENCE, required=True,
                        reference_domain="agents",
                        description="Agent to remove email from",
                    ),
                ],
                response_mappings=[],
                success_indicator="status",
            ),
        ),
    ]


# ═══════════════════════════════════════════════════════════════════════
# JOBS
# ═══════════════════════════════════════════════════════════════════════

def _job_actions() -> list:
    """Job actions - all routed to Scheduler API service."""
    return [
        ActionDefinition(
            capability_id="jobs.create",
            domain_id="jobs",
            description="Create a new data processing job",
            service=ServiceTarget.SCHEDULER_API,
            required_role=2,
            primary_route=RouteMapping(
                method="POST",
                path="/add/quickjob",
                encoding=PayloadEncoding.JSON,
                description="Create a quick job",
                input_fields=[
                    FieldSchema(
                        "job_name", FieldType.STRING, required=True,
                        description="Job display name",
                    ),
                    FieldSchema(
                        "connection_id", FieldType.REFERENCE, required=True,
                        reference_domain="connections",
                    ),
                    FieldSchema(
                        "query", FieldType.STRING, required=True,
                        description="SQL query for the job",
                    ),
                    FieldSchema(
                        "output_type", FieldType.ENUM, required=True,
                        choices=["csv", "excel", "json"],
                        description="Output file format",
                    ),
                ],
                response_mappings=[
                    ResponseMapping("job_id", "job_id", field_type=FieldType.INTEGER),
                ],
                success_indicator="status",
            ),
            suggested_prechecks=["connections.list"],
        ),

        ActionDefinition(
            capability_id="jobs.list",
            domain_id="jobs",
            description="Get all jobs",
            service=ServiceTarget.SCHEDULER_API,
            required_role=2,
            primary_route=RouteMapping(
                method="GET",
                path="/get/quickjobs",
                encoding=PayloadEncoding.NONE,
                input_fields=[],
                response_mappings=[
                    ResponseMapping("jobs", "data", is_list=True),
                ],
                is_idempotent=True,
            ),
        ),

        ActionDefinition(
            capability_id="jobs.delete",
            domain_id="jobs",
            description="Delete a job",
            service=ServiceTarget.SCHEDULER_API,
            is_destructive=True,
            requires_confirmation=True,
            required_role=2,
            primary_route=RouteMapping(
                method="POST",
                path="/delete/quickjob",
                encoding=PayloadEncoding.JSON,
                input_fields=[
                    FieldSchema(
                        "job_id", FieldType.REFERENCE, required=True,
                        reference_domain="jobs",
                    ),
                ],
                response_mappings=[],
                success_indicator="status",
            ),
            discovery_capability="jobs.list",
        ),

        ActionDefinition(
            capability_id="jobs.schedule",
            domain_id="jobs",
            description="Schedule a job for recurring execution",
            service=ServiceTarget.SCHEDULER_API,
            required_role=2,
            primary_route=RouteMapping(
                method="POST",
                path="/schedule/quickjob",
                encoding=PayloadEncoding.JSON,
                description="Schedule a quick job",
                input_fields=[
                    FieldSchema(
                        "job_id", FieldType.REFERENCE, required=True,
                        reference_domain="jobs",
                    ),
                    FieldSchema(
                        "schedule_type", FieldType.ENUM, required=True,
                        choices=["once", "daily", "weekly", "monthly"],
                    ),
                    FieldSchema(
                        "start_date", FieldType.STRING, required=True,
                        description="Start date (YYYY-MM-DD)",
                    ),
                    FieldSchema(
                        "start_time", FieldType.STRING, required=True,
                        description="Start time (HH:MM)",
                    ),
                ],
                response_mappings=[],
                success_indicator="status",
            ),
            discovery_capability="jobs.list",
        ),
    ]


# ═══════════════════════════════════════════════════════════════════════
# USERS
# ═══════════════════════════════════════════════════════════════════════

def _user_actions() -> list:
    return [
        ActionDefinition(
            capability_id="users.list",
            domain_id="users",
            description="Get all users",
            required_role=3,
            primary_route=RouteMapping(
                method="GET",
                path="/get/users",
                encoding=PayloadEncoding.NONE,
                input_fields=[],
                response_mappings=[
                    ResponseMapping("users", "data", is_list=True),
                ],
                is_idempotent=True,
            ),
        ),

        ActionDefinition(
            capability_id="users.create",
            domain_id="users",
            description="Create a new user account",
            required_role=3,
            primary_route=RouteMapping(
                method="POST",
                path="/add/user",
                encoding=PayloadEncoding.JSON,
                input_fields=[
                    FieldSchema(
                        "username", FieldType.STRING, required=True,
                        description="Login username",
                    ),
                    FieldSchema(
                        "password", FieldType.STRING, required=True,
                        description="User password",
                    ),
                    FieldSchema(
                        "role", FieldType.ENUM, required=True,
                        choices=["admin", "developer", "user"],
                        description="User role",
                    ),
                ],
                response_mappings=[
                    ResponseMapping("user_id", "user_id", field_type=FieldType.INTEGER),
                ],
                success_indicator="status",
            ),
        ),

        ActionDefinition(
            capability_id="users.delete",
            domain_id="users",
            description="Delete a user account",
            is_destructive=True,
            requires_confirmation=True,
            required_role=3,
            primary_route=RouteMapping(
                method="POST",
                path="/delete/user",
                encoding=PayloadEncoding.JSON,
                input_fields=[
                    FieldSchema(
                        "user_id", FieldType.REFERENCE, required=True,
                        reference_domain="users",
                    ),
                ],
                response_mappings=[],
                success_indicator="status",
            ),
            discovery_capability="users.list",
        ),

        ActionDefinition(
            capability_id="users.create_group",
            domain_id="users",
            description="Create a permission group. Set id=0 for new groups.",
            required_role=3,
            primary_route=RouteMapping(
                method="POST",
                path="/add/group",
                encoding=PayloadEncoding.JSON,
                input_fields=[
                    FieldSchema(
                        "id", FieldType.INTEGER, required=True,
                        default=0,
                        description="Group ID (use 0 to create a new group, or existing ID to update)",
                    ),
                    FieldSchema(
                        "group_name", FieldType.STRING, required=True,
                        description="Group name",
                    ),
                ],
                response_mappings=[
                    ResponseMapping("new_id", "response", field_type=FieldType.STRING,
                                    description="The new group ID"),
                ],
                success_indicator="status",
            ),
        ),

        ActionDefinition(
            capability_id="users.list_groups",
            domain_id="users",
            description="Get all permission groups",
            required_role=3,
            primary_route=RouteMapping(
                method="GET",
                path="/get/groups",
                encoding=PayloadEncoding.NONE,
                input_fields=[],
                response_mappings=[
                    ResponseMapping("groups", "data", is_list=True),
                ],
                is_idempotent=True,
            ),
        ),

        ActionDefinition(
            capability_id="users.delete_group",
            domain_id="users",
            description="Delete a permission group",
            is_destructive=True,
            requires_confirmation=True,
            required_role=3,
            primary_route=RouteMapping(
                method="GET",
                path="/delete/group/<group_id>",
                encoding=PayloadEncoding.NONE,
                path_params=["group_id"],
                input_fields=[
                    FieldSchema(
                        "group_id", FieldType.REFERENCE, required=True,
                        reference_domain="users",
                        description="ID of the group to delete",
                    ),
                ],
                response_mappings=[],
                success_indicator="status",
            ),
            discovery_capability="users.list_groups",
        ),

        ActionDefinition(
            capability_id="users.get_group_permissions",
            domain_id="users",
            description="Get agents assigned to a group",
            required_role=3,
            primary_route=RouteMapping(
                method="POST",
                path="/get/group_permissions",
                encoding=PayloadEncoding.JSON,
                input_fields=[
                    FieldSchema(
                        "group_id", FieldType.REFERENCE, required=True,
                        reference_domain="users",
                        description="Group ID to get permissions for",
                    ),
                ],
                response_mappings=[
                    ResponseMapping("assigned_permissions", "assigned_permissions", is_list=True),
                ],
                is_idempotent=True,
            ),
            discovery_capability="users.list_groups",
        ),

        ActionDefinition(
            capability_id="users.get_group_members",
            domain_id="users",
            description="Get assigned and unassigned users for a group",
            required_role=3,
            primary_route=RouteMapping(
                method="GET",
                path="/get/user_groups/<group_id>",
                encoding=PayloadEncoding.NONE,
                path_params=["group_id"],
                input_fields=[
                    FieldSchema(
                        "group_id", FieldType.REFERENCE, required=True,
                        reference_domain="users",
                        description="Group ID to get members for",
                    ),
                ],
                response_mappings=[
                    ResponseMapping("assigned_users", "assigned_users", is_list=True),
                    ResponseMapping("unassigned_users", "unassigned_users", is_list=True),
                ],
                is_idempotent=True,
            ),
            discovery_capability="users.list_groups",
        ),

        ActionDefinition(
            capability_id="users.manage_group_members",
            domain_id="users",
            description="Assign users and agent access permissions to a group",
            requires_confirmation=True,
            required_role=3,
            primary_route=RouteMapping(
                method="POST",
                path="/save/permissions",
                encoding=PayloadEncoding.JSON,
                input_fields=[
                    FieldSchema(
                        "group_id", FieldType.REFERENCE, required=True,
                        reference_domain="users",
                        description="Group ID to manage",
                    ),
                    FieldSchema(
                        "assigned_users", FieldType.LIST, required=True,
                        item_type=FieldType.INTEGER,
                        description="List of user IDs to assign to this group",
                    ),
                    FieldSchema(
                        "permissions", FieldType.LIST, required=True,
                        item_type=FieldType.INTEGER,
                        description="List of agent IDs this group can access",
                    ),
                ],
                response_mappings=[],
                success_indicator="status",
            ),
            discovery_capability="users.list_groups",
            suggested_prechecks=["users.get_group_members", "users.get_group_permissions"],
            notes="The 'permissions' field is a list of agent IDs that members of this group can access. The 'assigned_users' field is a list of user IDs that belong to this group.",
        ),
    ]


# ═══════════════════════════════════════════════════════════════════════
# MCP (Model Context Protocol)
# ═══════════════════════════════════════════════════════════════════════

def _mcp_actions() -> list:
    return [
        ActionDefinition(
            capability_id="mcp.list_servers",
            domain_id="mcp",
            description="Get all MCP servers",
            required_role=2,
            primary_route=RouteMapping(
                method="GET",
                path="/api/mcp/servers",
                encoding=PayloadEncoding.NONE,
                input_fields=[],
                response_mappings=[
                    ResponseMapping("servers", "servers", is_list=True),
                ],
                is_idempotent=True,
            ),
        ),

        ActionDefinition(
            capability_id="mcp.create_server",
            domain_id="mcp",
            description="Register a new MCP server",
            required_role=2,
            primary_route=RouteMapping(
                method="POST",
                path="/api/mcp/servers",
                encoding=PayloadEncoding.JSON,
                input_fields=[
                    FieldSchema(
                        "server_name", FieldType.STRING, required=True,
                        description="Server display name",
                    ),
                    FieldSchema(
                        "server_url", FieldType.STRING, required=True,
                        description="MCP server URL",
                    ),
                    FieldSchema(
                        "transport_type", FieldType.ENUM, required=True,
                        choices=["sse", "streamable_http"],
                        description="Transport protocol",
                    ),
                    FieldSchema(
                        "server_type", FieldType.ENUM, required=False,
                        choices=["local", "remote"],
                        description="Server type — use 'remote' for HTTP-based servers (default: remote)",
                    ),
                    FieldSchema(
                        "description", FieldType.STRING, required=False,
                    ),
                    FieldSchema(
                        "auth_type", FieldType.ENUM, required=False,
                        choices=["none", "api_key", "bearer_token", "oauth2"],
                    ),
                    FieldSchema(
                        "auth_config", FieldType.DICT, required=False,
                        description="Authentication configuration",
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "server_id", "server.id",
                        field_type=FieldType.INTEGER,
                    ),
                ],
                success_indicator="status",
            ),
        ),

        ActionDefinition(
            capability_id="mcp.delete_server",
            domain_id="mcp",
            description="Delete an MCP server",
            is_destructive=True,
            requires_confirmation=True,
            required_role=2,
            primary_route=RouteMapping(
                method="DELETE",
                path="/api/mcp/servers/<server_id>",
                encoding=PayloadEncoding.NONE,
                path_params=["server_id"],
                input_fields=[
                    FieldSchema(
                        "server_id", FieldType.REFERENCE, required=True,
                        reference_domain="mcp",
                    ),
                ],
                response_mappings=[],
            ),
            discovery_capability="mcp.list_servers",
        ),

        ActionDefinition(
            capability_id="mcp.test_server",
            domain_id="mcp",
            description="Test connectivity to an MCP server",
            required_role=2,
            primary_route=RouteMapping(
                method="POST",
                path="/api/mcp/test",
                encoding=PayloadEncoding.JSON,
                input_fields=[
                    FieldSchema(
                        "url", FieldType.STRING, required=True,
                        description="MCP server URL to test",
                    ),
                    FieldSchema(
                        "transport_type", FieldType.ENUM, required=True,
                        choices=["sse", "streamable_http"],
                    ),
                ],
                response_mappings=[
                    ResponseMapping("connected", "success", field_type=FieldType.BOOLEAN),
                    ResponseMapping("tools", "tools", is_list=True),
                ],
                is_idempotent=True,
            ),
        ),

        ActionDefinition(
            capability_id="mcp.get_tools",
            domain_id="mcp",
            description="Get available tools from an MCP server",
            required_role=2,
            primary_route=RouteMapping(
                method="GET",
                path="/api/mcp/servers/<server_id>/tools",
                encoding=PayloadEncoding.NONE,
                path_params=["server_id"],
                input_fields=[
                    FieldSchema(
                        "server_id", FieldType.REFERENCE, required=True,
                        reference_domain="mcp",
                    ),
                ],
                response_mappings=[
                    ResponseMapping("tools", "tools", is_list=True),
                ],
                is_idempotent=True,
            ),
            discovery_capability="mcp.list_servers",
        ),

        ActionDefinition(
            capability_id="mcp.assign_to_agent",
            domain_id="mcp",
            description="Assign MCP server tools to an agent",
            required_role=2,
            primary_route=RouteMapping(
                method="POST",
                path="/api/agents/<agent_id>/mcp-servers_v1",
                encoding=PayloadEncoding.JSON,
                path_params=["agent_id"],
                input_fields=[
                    FieldSchema(
                        "agent_id", FieldType.REFERENCE, required=True,
                        reference_domain="agents",
                    ),
                    FieldSchema(
                        "server_id", FieldType.REFERENCE, required=True,
                        reference_domain="mcp",
                    ),
                    FieldSchema(
                        "tool_names", FieldType.LIST, required=False,
                        item_type=FieldType.STRING,
                        description="Specific tools to enable (empty = all)",
                    ),
                ],
                response_mappings=[],
                success_indicator="status",
            ),
            suggested_prechecks=["agents.list", "mcp.list_servers"],
        ),

        ActionDefinition(
            capability_id="mcp.browse_directory",
            domain_id="mcp",
            description="Browse the MCP server directory",
            required_role=2,
            primary_route=RouteMapping(
                method="GET",
                path="/api/mcp/directory",
                encoding=PayloadEncoding.NONE,
                input_fields=[],
                response_mappings=[
                    ResponseMapping("servers", "servers", is_list=True),
                ],
                is_idempotent=True,
            ),
        ),
    ]


# ═══════════════════════════════════════════════════════════════════════
# SCHEDULES (Workflow Scheduling)
# ═══════════════════════════════════════════════════════════════════════

def _schedule_actions() -> list:
    """
    Schedule actions for recurring workflow execution.

    The scheduler uses cron expressions for flexible timing.
    All times are stored in UTC - the timezone_offset parameter converts
    local times to UTC on create/update.

    Common cron patterns:
    - "0 8 * * *"     → Daily at 8:00 AM
    - "0 8 * * 1-5"   → Weekdays at 8:00 AM
    - "0 9 * * 1"     → Every Monday at 9:00 AM
    - "0 0 1 * *"     → First of every month at midnight
    - "*/15 * * * *"  → Every 15 minutes
    """
    return [
        ActionDefinition(
            capability_id="schedules.create",
            domain_id="schedules",
            description="Create a recurring schedule for a workflow using cron expressions",
            required_role=2,
            notes=(
                "Creates a ScheduledJob + ScheduleDefinition record pair. "
                "The cron_expression uses standard 5-field format: Minute Hour Day Month DayOfWeek. "
                "Times are interpreted as UTC unless timezone_offset is provided. "
                "IMPORTANT: The response includes 'scheduled_job_id' (the ScheduledJob ID) which is "
                "required for update/delete/run_now operations — this is different from the workflow_id."
            ),
            primary_route=RouteMapping(
                method="POST",
                path="/api/scheduler/jobs/<job_id>/types/workflow/schedules",
                encoding=PayloadEncoding.JSON,
                description="Create a workflow schedule",
                path_params=["job_id"],
                input_fields=[
                    FieldSchema(
                        "job_id", FieldType.REFERENCE, required=True,
                        reference_domain="workflows",
                        description="Workflow ID (used as job_id for workflow-type schedules)",
                    ),
                    FieldSchema(
                        "type", FieldType.ENUM, required=True,
                        choices=["cron"],
                        default="cron",
                        description="Schedule type (use 'cron' for recurring schedules)",
                    ),
                    FieldSchema(
                        "cron_expression", FieldType.STRING, required=True,
                        description="Cron expression (e.g., '0 8 * * *' for daily at 8 AM)",
                    ),
                    FieldSchema(
                        "start_date", FieldType.STRING, required=False,
                        description="Schedule start date (ISO format, optional)",
                    ),
                    FieldSchema(
                        "end_date", FieldType.STRING, required=False,
                        description="Schedule end date (ISO format, optional)",
                    ),
                    FieldSchema(
                        "max_runs", FieldType.INTEGER, required=False,
                        description="Maximum number of executions (null = unlimited)",
                    ),
                    FieldSchema(
                        "is_active", FieldType.BOOLEAN, required=True,
                        default=True,
                        description="Whether the schedule is active",
                    ),
                    FieldSchema(
                        "timezone_offset", FieldType.INTEGER, required=False,
                        default=0,
                        description="Timezone offset in minutes from UTC (0 = UTC)",
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "schedule_id", "id",
                        description="ID of the created schedule (ScheduleDefinitions ID)",
                        field_type=FieldType.INTEGER,
                    ),
                    ResponseMapping(
                        "scheduled_job_id", "scheduled_job_id",
                        description="ScheduledJob ID (use this for update/delete/run_now operations)",
                        field_type=FieldType.INTEGER,
                    ),
                ],
                success_indicator="status",
            ),
            suggested_prechecks=["workflows.list"],
        ),

        ActionDefinition(
            capability_id="schedules.list",
            domain_id="schedules",
            description="Get all workflow schedules with timing and status information",
            required_role=2,
            primary_route=RouteMapping(
                method="GET",
                path="/api/scheduler/types/workflow/schedules",
                encoding=PayloadEncoding.NONE,
                description="List all workflow schedules",
                input_fields=[],
                response_mappings=[
                    ResponseMapping(
                        "schedules", "data",
                        description="List of schedule objects with workflow info",
                        is_list=True,
                    ),
                ],
                is_idempotent=True,
            ),
        ),

        ActionDefinition(
            capability_id="schedules.get",
            domain_id="schedules",
            description="Get details of a specific workflow schedule",
            required_role=2,
            primary_route=RouteMapping(
                method="GET",
                path="/api/scheduler/jobs/<job_id>/types/workflow/schedules/<schedule_id>",
                encoding=PayloadEncoding.NONE,
                description="Get schedule details",
                path_params=["job_id", "schedule_id"],
                input_fields=[
                    FieldSchema(
                        "job_id", FieldType.REFERENCE, required=True,
                        reference_domain="workflows",
                        description="Workflow ID (used as job_id for workflow-type schedules)",
                    ),
                    FieldSchema(
                        "schedule_id", FieldType.REFERENCE, required=True,
                        reference_domain="schedules",
                        description="Schedule definition ID",
                    ),
                ],
                response_mappings=[
                    ResponseMapping("schedule", "data", description="Schedule details"),
                ],
                is_idempotent=True,
            ),
            discovery_capability="schedules.list",
        ),

        ActionDefinition(
            capability_id="schedules.update",
            domain_id="schedules",
            description="Update a workflow schedule's timing or settings",
            required_role=2,
            notes=(
                "Use this to modify the cron expression, interval settings, start/end dates, "
                "max_runs, or active status. The schedule type can be changed between cron, "
                "interval, and date. For interval schedules, provide at least one interval field. "
                "For cron schedules, provide cron_expression. For date schedules, provide start_date."
            ),
            primary_route=RouteMapping(
                method="PUT",
                path="/api/scheduler/jobs/<job_id>/types/workflow/schedules/<schedule_id>",
                encoding=PayloadEncoding.JSON,
                description="Update schedule settings",
                path_params=["job_id", "schedule_id"],
                input_fields=[
                    FieldSchema(
                        "job_id", FieldType.REFERENCE, required=True,
                        reference_domain="workflows",
                        description="Workflow ID (used as job_id for workflow-type schedules)",
                    ),
                    FieldSchema(
                        "schedule_id", FieldType.REFERENCE, required=True,
                        reference_domain="schedules",
                        description="Schedule definition ID (the 'id' field from schedules.list)",
                    ),
                    FieldSchema(
                        "type", FieldType.ENUM, required=False,
                        choices=["cron", "interval", "date"],
                        description="Schedule type — only provide if changing the type",
                    ),
                    FieldSchema(
                        "cron_expression", FieldType.STRING, required=False,
                        description="Cron expression for cron-type schedules (e.g., '0 8 * * *')",
                    ),
                    FieldSchema(
                        "interval_seconds", FieldType.INTEGER, required=False,
                        description="Interval in seconds (for interval-type schedules)",
                    ),
                    FieldSchema(
                        "interval_minutes", FieldType.INTEGER, required=False,
                        description="Interval in minutes (for interval-type schedules)",
                    ),
                    FieldSchema(
                        "interval_hours", FieldType.INTEGER, required=False,
                        description="Interval in hours (for interval-type schedules)",
                    ),
                    FieldSchema(
                        "interval_days", FieldType.INTEGER, required=False,
                        description="Interval in days (for interval-type schedules)",
                    ),
                    FieldSchema(
                        "interval_weeks", FieldType.INTEGER, required=False,
                        description="Interval in weeks (for interval-type schedules)",
                    ),
                    FieldSchema(
                        "start_date", FieldType.STRING, required=False,
                        description="Schedule start date (ISO format, e.g., '2025-03-15T14:00:00')",
                    ),
                    FieldSchema(
                        "end_date", FieldType.STRING, required=False,
                        description="Schedule end date (ISO format)",
                    ),
                    FieldSchema(
                        "max_runs", FieldType.INTEGER, required=False,
                        description="Maximum number of executions (null = unlimited)",
                    ),
                    FieldSchema(
                        "is_active", FieldType.BOOLEAN, required=False,
                        description="Enable or disable the schedule",
                    ),
                    FieldSchema(
                        "timezone_offset", FieldType.INTEGER, required=False,
                        default=0,
                        description="Timezone offset in minutes from UTC",
                    ),
                ],
                response_mappings=[],
                success_indicator="status",
            ),
            discovery_capability="schedules.list",
        ),

        ActionDefinition(
            capability_id="schedules.delete",
            domain_id="schedules",
            description="Delete a workflow schedule",
            is_destructive=True,
            requires_confirmation=True,
            required_role=2,
            primary_route=RouteMapping(
                method="DELETE",
                path="/api/scheduler/jobs/<job_id>/types/workflow/schedules/<schedule_id>",
                encoding=PayloadEncoding.NONE,
                description="Delete a schedule",
                path_params=["job_id", "schedule_id"],
                input_fields=[
                    FieldSchema(
                        "job_id", FieldType.REFERENCE, required=True,
                        reference_domain="workflows",
                        description="Workflow ID (used as job_id for workflow-type schedules)",
                    ),
                    FieldSchema(
                        "schedule_id", FieldType.REFERENCE, required=True,
                        reference_domain="schedules",
                        description="Schedule definition ID (the 'id' field from schedules.list)",
                    ),
                ],
                response_mappings=[],
                success_indicator="status",
            ),
            discovery_capability="schedules.list",
        ),

        ActionDefinition(
            capability_id="schedules.run_now",
            domain_id="schedules",
            description="Trigger an immediate one-off execution of a scheduled job",
            required_role=2,
            notes=(
                "This runs a scheduled job immediately, bypassing any schedule timing. "
                "Requires the ScheduledJobId (not the workflow ID). "
                "Use schedules.list first to find the scheduled job ID for a workflow. "
                "Useful for testing or manual triggers."
            ),
            primary_route=RouteMapping(
                method="POST",
                path="/api/scheduler/run/<job_id>",
                encoding=PayloadEncoding.NONE,
                description="Run a scheduled job immediately",
                path_params=["job_id"],
                input_fields=[
                    FieldSchema(
                        "job_id", FieldType.REFERENCE, required=True,
                        reference_domain="schedules",
                        description="ScheduledJobId from the schedules list (not the workflow ID)",
                    ),
                ],
                response_mappings=[
                    ResponseMapping(
                        "execution_id", "execution_id",
                        description="ID of the triggered execution",
                    ),
                ],
                success_indicator="status",
            ),
            suggested_prechecks=["schedules.list"],
        ),
    ]
