"""
Builder Agent - AI Hub Platform Domains
=========================================
Concrete domain definitions for the AI Hub platform.
These are derived from the actual codebase routes, database schema,
and feature areas. This is the single source of truth for what
the builder agent knows about the platform.

Each domain maps to a real section of the platform with actual
API routes and database tables behind it.
"""

from .domains import DomainDefinition, CapabilityDefinition, EntityDefinition


def get_platform_domains() -> list:
    """
    Returns all platform domain definitions.
    Call this at startup to populate the registry.
    """
    return [
        _agents_domain(),
        _workflows_domain(),
        _documents_domain(),
        _tools_domain(),
        _connections_domain(),
        _knowledge_domain(),
        _integrations_domain(),
        _environments_domain(),
        _email_domain(),
        _jobs_domain(),
        _users_domain(),
        _mcp_domain(),
        _schedules_domain(),
    ]


# ─── Agent Domain ─────────────────────────────────────────────────────────

def _agents_domain() -> DomainDefinition:
    return DomainDefinition(
        id="agents",
        name="AI Agents",
        description=(
            "Create and manage AI agents with custom personas, tools, "
            "knowledge bases, and data connections. Agents are the core "
            "building block - users interact with them for chat, data analysis, "
            "and task automation."
        ),
        version="1.0",
        key_concepts=[
            "agent", "assistant", "chatbot", "persona", "system_prompt",
            "ai_agent", "data_agent", "chat", "conversation",
        ],
        context_notes=(
            "There are two types of agents: general agents (is_data_agent=0) "
            "for conversational AI with tools, and data agents (is_data_agent=1) "
            "for natural language database querying. Both share the same Agents "
            "table. Agent creation requires a tier context. Tools are assigned "
            "via AgentTools join table. Agents use RLS via TenantId."
        ),
        depends_on=["tools", "connections"],
        entities=[
            EntityDefinition(
                name="Agent",
                description="An AI agent with configured behavior and capabilities",
                key_fields=["id", "description", "objective", "enabled",
                            "is_data_agent", "TenantId"],
                relationships={
                    "tools": "tools.AgentTool",
                    "connections": "connections.AgentConnection",
                    "knowledge": "knowledge.AgentKnowledge",
                    "groups": "users.AgentGroup",
                }
            ),
            EntityDefinition(
                name="AgentTool",
                description="Association between agent and tools it can use",
                key_fields=["agent_id", "tool_name", "custom_tool", "enabled"],
            ),
            EntityDefinition(
                name="AgentConnection",
                description="Database connections available to data agents",
                key_fields=["agent_id", "connection_id"],
            ),
        ],
        capabilities=[
            CapabilityDefinition(
                id="agents.create",
                name="Create Agent",
                description="Create a new AI agent with name, objective, and tool assignments",
                category="create",
                required_context=["tier_id"],
                tags=["agent", "create", "new", "build"],
            ),
            CapabilityDefinition(
                id="agents.create_data_agent",
                name="Create Data Agent",
                description="Create a data agent for natural language database querying",
                category="create",
                required_context=["tier_id", "connection_id"],
                requires_domains=["connections"],
                tags=["data", "agent", "nlq", "query", "database"],
            ),
            CapabilityDefinition(
                id="agents.update",
                name="Update Agent",
                description="Modify an agent's name, objective, tools, or enabled status",
                category="update",
                required_context=["agent_id"],
                tags=["agent", "modify", "edit", "update"],
            ),
            CapabilityDefinition(
                id="agents.delete",
                name="Delete Agent",
                description="Remove an agent and its tool/connection associations",
                category="delete",
                required_context=["agent_id"],
                tags=["agent", "remove", "delete"],
            ),
            CapabilityDefinition(
                id="agents.list",
                name="List Agents",
                description="Get all agents accessible to the current user",
                category="read",
                tags=["agent", "list", "view", "browse"],
            ),
            CapabilityDefinition(
                id="agents.get",
                name="Get Agent Details",
                description="Get full configuration of a specific agent",
                category="read",
                required_context=["agent_id"],
                tags=["agent", "details", "info"],
            ),
            # agents.add_tools removed — phantom route, use agents.assign_tools instead
            CapabilityDefinition(
                id="agents.assign_tools",
                name="Assign Tools to Agent",
                description="Add or remove tools from an agent's toolkit",
                category="configure",
                required_context=["agent_id"],
                requires_domains=["tools"],
                tags=["agent", "tools", "assign", "configure"],
            ),
            CapabilityDefinition(
                id="agents.export",
                name="Export Agent",
                description="Export an agent as a portable package including tools and knowledge",
                category="execute",
                required_context=["agent_id"],
                tags=["agent", "export", "package", "portable"],
            ),
            CapabilityDefinition(
                id="agents.import",
                name="Import Agent",
                description="Import an agent from an exported package",
                category="execute",
                tags=["agent", "import", "package"],
            ),
            CapabilityDefinition(
                id="agents.list_tools",
                name="List Agent Tools",
                description="Get list of tools available for agent assignment",
                category="read",
                tags=["agent", "tools", "list", "available"],
            ),
            CapabilityDefinition(
                id="agents.chat",
                name="Chat with Agent",
                description="Send a message to an agent and receive a response",
                category="execute",
                required_context=["agent_id"],
                tags=["agent", "chat", "talk", "ask", "message", "conversation", "test"],
            ),
        ],
    )


# ─── Workflow Domain ──────────────────────────────────────────────────────

def _workflows_domain() -> DomainDefinition:
    return DomainDefinition(
        id="workflows",
        name="Workflow Automation",
        description=(
            "Visual automation flows with nodes, conditions, triggers, "
            "and human-in-the-loop approvals. Workflows connect agents, "
            "integrations, and data processing into automated pipelines."
        ),
        version="1.0",
        key_concepts=[
            "workflow", "automation", "flow", "node", "trigger", "schedule",
            "approval", "pipeline", "process", "step", "connection",
        ],
        context_notes=(
            "Workflows are stored as JSON definitions in AgentWorkflows table. "
            "Each workflow has nodes (visual blocks), connections between nodes, "
            "and execution state tracked in AgentWorkflowExecutions. "
            "Workflow execution happens in workflow_execution.py. "
            "The visual builder is workflow.js (client-side). "
            "Approval nodes pause execution and create ApprovalRequests."
        ),
        depends_on=["agents", "integrations"],
        entities=[
            EntityDefinition(
                name="Workflow",
                description="An automation flow with nodes and connections",
                key_fields=["id", "workflow_id", "name", "description",
                            "workflow_definition", "created_by"],
            ),
            EntityDefinition(
                name="WorkflowExecution",
                description="A single run of a workflow with status tracking",
                key_fields=["id", "execution_id", "workflow_id", "status",
                            "current_step", "total_steps"],
            ),
            EntityDefinition(
                name="ApprovalRequest",
                description="Human approval gate within a workflow execution",
                key_fields=["request_id", "step_execution_id", "status",
                            "assigned_to"],
            ),
            EntityDefinition(
                name="WorkflowCategory",
                description="Organizational category for grouping workflows",
                key_fields=["id", "name", "description"],
            ),
        ],
        capabilities=[
            CapabilityDefinition(
                id="workflows.create",
                name="Create Workflow",
                description="Create a new workflow with nodes and connections",
                category="create",
                tags=["workflow", "create", "new", "build", "automation"],
            ),
            CapabilityDefinition(
                id="workflows.update",
                name="Update Workflow",
                description="Modify a workflow's definition, nodes, or connections",
                category="update",
                required_context=["workflow_id"],
                tags=["workflow", "modify", "edit"],
            ),
            CapabilityDefinition(
                id="workflows.delete",
                name="Delete Workflow",
                description="Remove a workflow",
                category="delete",
                required_context=["workflow_id"],
                tags=["workflow", "remove", "delete"],
            ),
            CapabilityDefinition(
                id="workflows.list",
                name="List Workflows",
                description="Get all workflows accessible to current user",
                category="read",
                tags=["workflow", "list", "view"],
            ),
            CapabilityDefinition(
                id="workflows.get",
                name="Get Workflow Details",
                description="Get full workflow definition including nodes and connections",
                category="read",
                required_context=["workflow_id"],
                tags=["workflow", "details"],
            ),
            CapabilityDefinition(
                id="workflows.execute",
                name="Execute Workflow",
                description="Start a workflow execution run",
                category="execute",
                required_context=["workflow_id"],
                tags=["workflow", "run", "execute", "start"],
            ),
            CapabilityDefinition(
                id="workflows.manage_execution",
                name="Manage Execution",
                description="Pause, resume, or cancel a running workflow",
                category="execute",
                required_context=["execution_id"],
                tags=["workflow", "pause", "resume", "cancel"],
            ),
            CapabilityDefinition(
                id="workflows.manage_approvals",
                name="Manage Approvals",
                description="View and respond to workflow approval requests",
                category="execute",
                tags=["workflow", "approval", "approve", "reject"],
            ),
            CapabilityDefinition(
                id="workflows.manage_categories",
                name="Manage Categories",
                description="Create, update, or delete workflow categories",
                category="configure",
                tags=["workflow", "category", "organize"],
            ),
            CapabilityDefinition(
                id="workflows.analytics",
                name="Workflow Analytics",
                description="View execution statistics, logs, and performance data",
                category="query",
                tags=["workflow", "analytics", "stats", "logs", "monitoring"],
            ),
            CapabilityDefinition(
                id="workflows.monitor",
                name="Monitor Executions",
                description="View execution history and status of workflow runs",
                category="read",
                tags=["workflow", "monitor", "execution", "history", "status"],
            ),
            CapabilityDefinition(
                id="workflows.rename",
                name="Rename Workflow",
                description="Change a workflow's display name",
                category="update",
                required_context=["workflow_id"],
                tags=["workflow", "rename", "name"],
            ),
        ],
    )


# ─── Documents Domain ─────────────────────────────────────────────────────

def _documents_domain() -> DomainDefinition:
    return DomainDefinition(
        id="documents",
        name="Document Processing",
        description=(
            "Upload, process, search, and manage documents. Includes "
            "AI-powered text extraction, vector embeddings for RAG, "
            "and document scheduling for automated processing."
        ),
        version="1.0",
        key_concepts=[
            "document", "pdf", "file", "upload", "extract", "search",
            "vector", "embedding", "rag", "ocr", "text",
        ],
        context_notes=(
            "Documents are stored in the Documents table with document_id (GUID). "
            "Text extraction uses fast_pdf_extractor (PyMuPDF) with fallback to "
            "AI extraction. Vector embeddings stored in ChromaDB. "
            "Document processing happens in LLMDocumentEngine and "
            "LLMDocumentVectorEngine. Reprocessing can update vectors for "
            "existing documents."
        ),
        depends_on=[],
        entities=[
            EntityDefinition(
                name="Document",
                description="A processed document with extracted text and metadata",
                key_fields=["document_id", "filename", "file_type",
                            "status", "TenantId"],
            ),
        ],
        capabilities=[
            CapabilityDefinition(
                id="documents.upload",
                name="Upload Document",
                description="Upload and process a new document",
                category="create",
                tags=["document", "upload", "file", "process"],
            ),
            CapabilityDefinition(
                id="documents.list",
                name="List Documents",
                description="Get all documents with filtering and pagination",
                category="read",
                tags=["document", "list", "browse"],
            ),
            CapabilityDefinition(
                id="documents.get",
                name="Get Document",
                description="Get document details and extracted content",
                category="read",
                required_context=["document_id"],
                tags=["document", "view", "details"],
            ),
            CapabilityDefinition(
                id="documents.search",
                name="Search Documents",
                description="Semantic search across document content using vector similarity",
                category="query",
                tags=["document", "search", "find", "query", "rag"],
            ),
            CapabilityDefinition(
                id="documents.extract",
                name="Extract from Document",
                description="AI-powered extraction of specific fields or data from documents",
                category="execute",
                required_context=["document_id"],
                tags=["document", "extract", "ai", "fields", "data"],
            ),
            CapabilityDefinition(
                id="documents.update",
                name="Update Document",
                description="Update document metadata and description",
                category="update",
                required_context=["document_id"],
                tags=["document", "update", "edit", "metadata"],
            ),
            CapabilityDefinition(
                id="documents.delete",
                name="Delete Document",
                description="Remove a document and its vectors",
                category="delete",
                required_context=["document_id"],
                tags=["document", "remove", "delete"],
            ),
            CapabilityDefinition(
                id="documents.reprocess",
                name="Reprocess Vectors",
                description="Regenerate vector embeddings for documents",
                category="execute",
                tags=["document", "vector", "reprocess", "rebuild"],
            ),
        ],
    )


# ─── Tools Domain ─────────────────────────────────────────────────────────

def _tools_domain() -> DomainDefinition:
    return DomainDefinition(
        id="tools",
        name="Custom Tools",
        description=(
            "Python-based tools that agents can use. Includes custom user "
            "tools with code, parameters, and dependency management, as well "
            "as core platform tools."
        ),
        version="1.0",
        key_concepts=[
            "tool", "function", "code", "python", "custom", "parameter",
            "dependency", "package",
        ],
        context_notes=(
            "Tools are stored as Python code. Custom tools have user-defined "
            "parameters and code that runs in agent execution context. "
            "Core tools are built-in (file operations, web search, etc.). "
            "Tool dependencies are managed by tool_dependency_manager.py. "
            "Tools are saved/loaded as 'packages' via the custom_tool page."
        ),
        depends_on=[],
        entities=[
            EntityDefinition(
                name="Tool",
                description="A custom or core tool available for agent use",
                key_fields=["tool_name", "custom_tool", "enabled"],
            ),
        ],
        capabilities=[
            CapabilityDefinition(
                id="tools.create",
                name="Create Custom Tool",
                description="Create a new Python tool with parameters and code",
                category="create",
                tags=["tool", "create", "custom", "python", "code"],
            ),
            CapabilityDefinition(
                id="tools.update",
                name="Update Tool",
                description="Modify a tool's code, parameters, or configuration",
                category="update",
                required_context=["tool_name"],
                tags=["tool", "edit", "modify"],
            ),
            CapabilityDefinition(
                id="tools.delete",
                name="Delete Tool",
                description="Remove a custom tool",
                category="delete",
                required_context=["tool_name"],
                tags=["tool", "remove", "delete"],
            ),
            CapabilityDefinition(
                id="tools.list",
                name="List Tools",
                description="Get all available tools by category",
                category="read",
                tags=["tool", "list", "browse", "available"],
            ),
            CapabilityDefinition(
                id="tools.manage_dependencies",
                name="Manage Dependencies",
                description="Configure tool dependencies and package requirements",
                category="configure",
                tags=["tool", "dependency", "package", "requirements"],
            ),
        ],
    )


# ─── Connections Domain ───────────────────────────────────────────────────

def _connections_domain() -> DomainDefinition:
    return DomainDefinition(
        id="connections",
        name="Database Connections",
        description=(
            "Manage database connections used by data agents and workflows. "
            "Supports multiple database types through ODBC drivers and "
            "includes data dictionary management."
        ),
        version="1.0",
        key_concepts=[
            "connection", "database", "sql", "odbc", "table", "column",
            "data_dictionary", "schema",
        ],
        context_notes=(
            "Connections use ODBC drivers (primarily CData) for database access. "
            "The data dictionary (tables, columns, descriptions) is critical for "
            "data agents to understand the schema. Tables and columns can be "
            "auto-discovered and AI-analyzed for descriptions. Connection "
            "credentials use the local secrets system."
        ),
        depends_on=[],
        entities=[
            EntityDefinition(
                name="Connection",
                description="A database connection configuration",
                key_fields=["id", "name", "connection_type", "connection_string"],
            ),
            EntityDefinition(
                name="Table",
                description="A database table registered in the data dictionary",
                key_fields=["id", "connection_id", "table_name", "description"],
            ),
            EntityDefinition(
                name="Column",
                description="A table column with metadata for AI understanding",
                key_fields=["id", "table_id", "column_name", "description",
                            "data_type"],
            ),
        ],
        capabilities=[
            CapabilityDefinition(
                id="connections.create",
                name="Create Connection",
                description="Create a new database connection",
                category="create",
                required_role=2,
                tags=["connection", "database", "create", "new"],
            ),
            CapabilityDefinition(
                id="connections.test",
                name="Test Connection",
                description="Test connectivity to a database",
                category="execute",
                required_context=["connection_id"],
                required_role=2,
                tags=["connection", "test", "verify"],
            ),
            CapabilityDefinition(
                id="connections.list",
                name="List Connections",
                description="Get all configured database connections",
                category="read",
                required_role=2,
                tags=["connection", "list", "browse"],
            ),
            CapabilityDefinition(
                id="connections.discover_tables",
                name="Discover Tables",
                description="Auto-discover tables from a database connection",
                category="execute",
                required_context=["connection_id"],
                required_role=2,
                tags=["connection", "discover", "tables", "schema"],
            ),
            CapabilityDefinition(
                id="connections.manage_dictionary",
                name="Manage Data Dictionary",
                description="Add, update, or AI-analyze tables and columns",
                category="configure",
                required_context=["connection_id"],
                required_role=2,
                tags=["dictionary", "table", "column", "describe"],
            ),
            CapabilityDefinition(
                id="connections.analyze_tables",
                name="Analyze Tables with AI",
                description="Run AI analysis on selected tables to populate the data dictionary with descriptions, types, and relationships",
                category="execute",
                required_context=["connection_id", "table_names"],
                required_role=2,
                tags=["dictionary", "analyze", "ai", "metadata", "describe"],
            ),
            CapabilityDefinition(
                id="connections.check_analysis_progress",
                name="Check Analysis Progress",
                description="Check the progress of a background data dictionary analysis task",
                category="read",
                required_context=["task_id"],
                required_role=2,
                tags=["dictionary", "progress", "status"],
            ),
            # connections.execute_query removed — duplicate of connections.query
            CapabilityDefinition(
                id="connections.get",
                name="Get Connection Details",
                description="Get full details of a specific database connection",
                category="read",
                required_context=["connection_id"],
                required_role=2,
                tags=["connection", "details", "info"],
            ),
            CapabilityDefinition(
                id="connections.update",
                name="Update Connection",
                description="Modify a database connection's settings",
                category="update",
                required_context=["connection_id"],
                required_role=2,
                tags=["connection", "edit", "modify", "update"],
            ),
            CapabilityDefinition(
                id="connections.delete",
                name="Delete Connection",
                description="Remove a database connection",
                category="delete",
                required_context=["connection_id"],
                required_role=2,
                tags=["connection", "remove", "delete"],
            ),
            CapabilityDefinition(
                id="connections.query",
                name="Query Connection",
                description="Execute an ad-hoc SQL query and return results",
                category="query",
                required_context=["connection_id"],
                required_role=2,
                tags=["connection", "query", "sql", "data"],
            ),
        ],
    )


# ─── Knowledge Domain ─────────────────────────────────────────────────────

def _knowledge_domain() -> DomainDefinition:
    return DomainDefinition(
        id="knowledge",
        name="Agent Knowledge",
        description=(
            "Attach document collections to agents as knowledge bases. "
            "Enables RAG (retrieval-augmented generation) so agents can "
            "reference specific documents when answering questions."
        ),
        version="1.0",
        key_concepts=[
            "knowledge", "knowledge_base", "rag", "context", "reference",
        ],
        context_notes=(
            "Knowledge links agents to documents via AgentKnowledge table. "
            "When an agent has knowledge attached, the system searches "
            "document vectors for relevant context before generating responses. "
            "Managed through agent_knowledge_routes.py and "
            "agent_knowledge_integration.py."
        ),
        depends_on=["agents", "documents"],
        entities=[
            EntityDefinition(
                name="AgentKnowledge",
                description="Association between an agent and a document for RAG",
                key_fields=["knowledge_id", "agent_id", "document_id",
                            "description", "is_active"],
                relationships={
                    "agent_id": "agents.Agent",
                    "document_id": "documents.Document",
                }
            ),
        ],
        capabilities=[
            CapabilityDefinition(
                id="knowledge.attach",
                name="Attach Knowledge",
                description="Link a document to an agent as a knowledge source",
                category="create",
                required_context=["agent_id", "document_id"],
                requires_domains=["agents", "documents"],
                tags=["knowledge", "attach", "add", "rag", "document"],
            ),
            CapabilityDefinition(
                id="knowledge.detach",
                name="Detach Knowledge",
                description="Remove a document from an agent's knowledge base",
                category="delete",
                required_context=["knowledge_id"],
                tags=["knowledge", "remove", "detach"],
            ),
            CapabilityDefinition(
                id="knowledge.list",
                name="List Agent Knowledge",
                description="Get all knowledge documents attached to an agent",
                category="read",
                required_context=["agent_id"],
                tags=["knowledge", "list", "view"],
            ),
        ],
    )


# ─── Integrations Domain ─────────────────────────────────────────────────

def _integrations_domain() -> DomainDefinition:
    return DomainDefinition(
        id="integrations",
        name="External Integrations",
        description=(
            "Connect to external services like Salesforce, Slack, email "
            "providers etc. using templates and OAuth. Also supports cloud "
            "storage providers (Azure Blob Storage, etc.) for file upload, "
            "download, and management. Integrations can be used by agents "
            "as tools and in workflow nodes."
        ),
        version="1.1",
        key_concepts=[
            "integration", "api", "oauth", "salesforce", "slack",
            "external", "service", "connector", "template",
            "cloud storage", "azure blob", "blob storage", "file upload",
            "file download", "container", "s3",
        ],
        context_notes=(
            "Integrations use templates (JSON) defining available operations, "
            "auth methods (API key, OAuth2, OAuth1 TBA, basic auth, cloud_storage), and "
            "configuration. Templates are loaded from "
            "integration_template_loader.py. Each integration instance stores "
            "its credentials (encrypted) and can be tested. Integration "
            "operations can be executed standalone or as workflow nodes.\n\n"
            "Cloud storage integrations (auth_type='cloud_storage') use a "
            "dedicated Cloud Storage Gateway service with native SDKs. "
            "Azure Blob Storage requires a connection_string credential. "
            "Operations include: list_containers, list_objects, upload_object, "
            "download_object, delete_object, get_object_metadata, "
            "generate_sas_url. Files under 50MB are supported; text files "
            "transfer as plain text, binary files as base64."
        ),
        depends_on=[],
        entities=[
            EntityDefinition(
                name="Integration",
                description="A configured connection to an external service",
                key_fields=["id", "template_key", "name", "config", "status"],
            ),
            EntityDefinition(
                name="IntegrationTemplate",
                description="Template defining an integration type's capabilities",
                key_fields=["template_key", "name", "auth_type", "operations"],
            ),
        ],
        capabilities=[
            CapabilityDefinition(
                id="integrations.create",
                name="Create Integration",
                description="Set up a new integration from a template",
                category="create",
                required_context=["template_key"],
                required_role=2,
                tags=["integration", "create", "connect", "setup"],
            ),
            CapabilityDefinition(
                id="integrations.list",
                name="List Integrations",
                description="Get all configured integrations",
                category="read",
                required_role=2,
                tags=["integration", "list", "browse"],
            ),
            CapabilityDefinition(
                id="integrations.list_templates",
                name="List Templates",
                description="Get available integration templates",
                category="read",
                required_role=2,
                tags=["integration", "template", "available"],
            ),
            CapabilityDefinition(
                id="integrations.test",
                name="Test Integration",
                description="Test an integration's connectivity",
                category="execute",
                required_context=["integration_id"],
                required_role=2,
                tags=["integration", "test", "verify"],
            ),
            CapabilityDefinition(
                id="integrations.execute_operation",
                name="Execute Operation",
                description="Run an operation on a configured integration",
                category="execute",
                required_context=["integration_id", "operation_name"],
                required_role=2,
                tags=["integration", "execute", "operation", "run"],
            ),
            CapabilityDefinition(
                id="integrations.update",
                name="Update Integration",
                description="Update an integration's name, description, config, or credentials",
                category="update",
                required_context=["integration_id"],
                required_role=2,
                tags=["integration", "update", "modify", "edit"],
            ),
            CapabilityDefinition(
                id="integrations.list_operations",
                name="List Operations",
                description="Get available operations for a configured integration",
                category="read",
                required_context=["integration_id"],
                required_role=2,
                tags=["integration", "operations", "available", "list"],
            ),
            CapabilityDefinition(
                id="integrations.delete",
                name="Delete Integration",
                description="Remove a configured integration",
                category="delete",
                required_context=["integration_id"],
                required_role=2,
                tags=["integration", "remove", "delete"],
            ),
        ],
    )


# ─── Environments Domain ─────────────────────────────────────────────────

def _environments_domain() -> DomainDefinition:
    return DomainDefinition(
        id="environments",
        name="Agent Environments",
        description=(
            "Isolated Python environments for agents with custom packages. "
            "Allows agents to have their own dependencies without affecting "
            "the main application."
        ),
        version="1.0",
        key_concepts=[
            "environment", "python", "packages", "isolation", "virtualenv",
            "sandbox", "dependencies",
        ],
        context_notes=(
            "Environments are managed via environment_manager.py and "
            "environment_api.py. Each environment is a separate Python "
            "virtualenv. Agents can be assigned to environments. "
            "Templates provide preset package configurations. "
            "The sandbox allows testing packages before assigning."
        ),
        depends_on=["agents"],
        entities=[
            EntityDefinition(
                name="Environment",
                description="An isolated Python environment with custom packages",
                key_fields=["env_id", "name", "packages"],
            ),
        ],
        capabilities=[
            CapabilityDefinition(
                id="environments.create",
                name="Create Environment",
                description="Create a new isolated Python environment",
                category="create",
                tier_requirement="professional",
                required_role=2,
                tags=["environment", "create", "new", "python"],
            ),
            CapabilityDefinition(
                id="environments.install_package",
                name="Install Package",
                description="Install a Python package into an environment",
                category="configure",
                required_context=["env_id"],
                required_role=2,
                tags=["environment", "package", "install", "pip"],
            ),
            CapabilityDefinition(
                id="environments.assign_agent",
                name="Assign Agent to Environment",
                description="Link an agent to use a specific environment",
                category="configure",
                required_context=["env_id", "agent_id"],
                requires_domains=["agents"],
                required_role=2,
                tags=["environment", "agent", "assign"],
            ),
            CapabilityDefinition(
                id="environments.list",
                name="List Environments",
                description="Get all configured environments",
                category="read",
                required_role=2,
                tags=["environment", "list", "browse"],
            ),
            CapabilityDefinition(
                id="environments.get",
                name="Get Agent Environment",
                description="Get the environment configuration for a specific agent",
                category="read",
                required_context=["agent_id"],
                requires_domains=["agents"],
                required_role=2,
                tags=["environment", "agent", "get", "details"],
            ),
            CapabilityDefinition(
                id="environments.assign",
                name="Assign Environment",
                description="Assign an environment with packages to an agent",
                category="configure",
                required_context=["agent_id"],
                requires_domains=["agents"],
                required_role=2,
                tags=["environment", "assign", "agent", "packages"],
            ),
            CapabilityDefinition(
                id="environments.status",
                name="Environment Status",
                description="Get status of all environments",
                category="read",
                required_role=2,
                tags=["environment", "status", "health"],
            ),
        ],
    )


# ─── Email Domain ─────────────────────────────────────────────────────────

def _email_domain() -> DomainDefinition:
    return DomainDefinition(
        id="email",
        name="Agent Email",
        description=(
            "Provision email addresses for agents and configure email "
            "processing. Agents can receive, process, and respond to emails "
            "autonomously via Mailgun integration."
        ),
        version="1.0",
        key_concepts=[
            "email", "mailgun", "inbox", "send", "receive", "provision",
        ],
        context_notes=(
            "Email is provisioned per-agent via agent_email_routes.py. "
            "Incoming emails are routed by email_agent_dispatcher.py. "
            "Processing history tracked in email_processing_routes.py. "
            "Agents need the email tool enabled to process emails."
        ),
        depends_on=["agents"],
        entities=[
            EntityDefinition(
                name="AgentEmail",
                description="Email configuration for an agent",
                key_fields=["agent_id", "email_address", "enabled"],
            ),
        ],
        capabilities=[
            CapabilityDefinition(
                id="email.provision",
                name="Provision Email",
                description="Create an email address for an agent",
                category="create",
                required_context=["agent_id"],
                requires_domains=["agents"],
                tags=["email", "provision", "create", "mailgun"],
            ),
            CapabilityDefinition(
                id="email.configure",
                name="Configure Email",
                description="Update email processing settings for an agent",
                category="configure",
                required_context=["agent_id"],
                tags=["email", "configure", "settings"],
            ),
            CapabilityDefinition(
                id="email.list",
                name="List Email Agents",
                description="Get all agents with email provisioned",
                category="read",
                tags=["email", "list", "agents"],
            ),
            CapabilityDefinition(
                id="email.get",
                name="Get Email Config",
                description="Get email configuration for a specific agent",
                category="read",
                required_context=["agent_id"],
                requires_domains=["agents"],
                tags=["email", "get", "config", "details"],
            ),
            CapabilityDefinition(
                id="email.deprovision",
                name="Deprovision Email",
                description="Remove email address from an agent",
                category="delete",
                required_context=["agent_id"],
                requires_domains=["agents"],
                tags=["email", "remove", "deprovision", "delete"],
            ),
        ],
    )


# ─── Jobs Domain ──────────────────────────────────────────────────────────

def _jobs_domain() -> DomainDefinition:
    return DomainDefinition(
        id="jobs",
        name="Jobs & Scheduling",
        description=(
            "Scheduled and on-demand data processing jobs. Quick jobs "
            "for ad-hoc tasks and scheduled jobs for recurring operations."
        ),
        version="1.0",
        key_concepts=[
            "job", "schedule", "quickjob", "cron", "recurring", "task",
        ],
        context_notes=(
            "Jobs are managed in job_scheduler.py. Quick jobs are lighter "
            "ad-hoc tasks. Scheduled jobs use cron-like scheduling. "
            "Job execution logs are tracked and viewable."
        ),
        depends_on=["connections"],
        entities=[
            EntityDefinition(
                name="Job",
                description="A scheduled or ad-hoc data processing job",
                key_fields=["job_id", "name", "schedule", "status"],
            ),
        ],
        capabilities=[
            CapabilityDefinition(
                id="jobs.create",
                name="Create Job",
                description="Create a new scheduled or quick job",
                category="create",
                required_role=2,
                tags=["job", "schedule", "create", "new"],
            ),
            CapabilityDefinition(
                id="jobs.list",
                name="List Jobs",
                description="Get all jobs and their status",
                category="read",
                required_role=2,
                tags=["job", "list", "browse", "status"],
            ),
            CapabilityDefinition(
                id="jobs.execute",
                name="Execute Job",
                description="Manually trigger a job execution",
                category="execute",
                required_context=["job_id"],
                required_role=2,
                tags=["job", "run", "execute", "trigger"],
            ),
            CapabilityDefinition(
                id="jobs.delete",
                name="Delete Job",
                description="Remove a job",
                category="delete",
                required_context=["job_id"],
                required_role=2,
                tags=["job", "remove", "delete"],
            ),
            CapabilityDefinition(
                id="jobs.schedule",
                name="Schedule Job",
                description="Schedule a job for recurring execution",
                category="configure",
                required_context=["job_id"],
                required_role=2,
                tags=["job", "schedule", "recurring", "cron"],
            ),
        ],
    )


# ─── Users Domain ─────────────────────────────────────────────────────────

def _users_domain() -> DomainDefinition:
    return DomainDefinition(
        id="users",
        name="Users & Groups",
        description=(
            "Manage platform users and permission groups. Controls access "
            "to agents and features through group-based permissions."
        ),
        version="1.0",
        key_concepts=[
            "user", "group", "permission", "access", "role", "admin",
        ],
        context_notes=(
            "Users authenticate via Flask-Login. Groups control agent access. "
            "Role-based access is enforced by role_decorators.py "
            "(admin_required, developer_required). RLS via TenantId "
            "controls data isolation."
        ),
        depends_on=[],
        entities=[
            EntityDefinition(
                name="User",
                description="A platform user account",
                key_fields=["id", "username", "role", "TenantId"],
            ),
            EntityDefinition(
                name="Group",
                description="A permission group for agent access control",
                key_fields=["group_id", "name"],
            ),
        ],
        capabilities=[
            CapabilityDefinition(
                id="users.create",
                name="Create User",
                description="Add a new platform user",
                category="create",
                required_role=3,
                tags=["user", "create", "add"],
            ),
            CapabilityDefinition(
                id="users.list",
                name="List Users",
                description="Get all platform users",
                category="read",
                required_role=3,
                tags=["user", "list", "browse"],
            ),
            CapabilityDefinition(
                id="users.list_groups",
                name="List Groups",
                description="Get all permission groups",
                category="read",
                required_role=3,
                tags=["group", "list", "browse", "permission"],
            ),
            CapabilityDefinition(
                id="users.delete_group",
                name="Delete Group",
                description="Remove a permission group",
                category="delete",
                required_context=["group_id"],
                required_role=3,
                tags=["group", "delete", "remove"],
            ),
            CapabilityDefinition(
                id="users.get_group_permissions",
                name="Get Group Permissions",
                description="Get which agents are assigned to a group",
                category="read",
                required_context=["group_id"],
                required_role=3,
                tags=["group", "permission", "agents", "assigned"],
            ),
            CapabilityDefinition(
                id="users.get_group_members",
                name="Get Group Members",
                description="Get assigned and unassigned users for a group",
                category="read",
                required_context=["group_id"],
                required_role=3,
                tags=["group", "members", "users", "assigned"],
            ),
            CapabilityDefinition(
                id="users.manage_group_members",
                name="Manage Group Members",
                description="Assign users and agent access permissions to a group",
                category="configure",
                required_context=["group_id"],
                required_role=3,
                tags=["group", "assign", "users", "agents", "permission", "manage"],
            ),
            CapabilityDefinition(
                id="users.delete",
                name="Delete User",
                description="Remove a platform user account",
                category="delete",
                required_context=["user_id"],
                required_role=3,
                tags=["user", "remove", "delete"],
            ),
            CapabilityDefinition(
                id="users.create_group",
                name="Create Group",
                description="Create a new permission group",
                category="create",
                required_role=3,
                tags=["group", "create", "permission", "new"],
            ),
        ],
    )


# ─── MCP Domain ───────────────────────────────────────────────────────────

def _mcp_domain() -> DomainDefinition:
    return DomainDefinition(
        id="mcp",
        name="MCP Servers",
        description=(
            "Model Context Protocol servers that extend agent capabilities "
            "with external tool connections. Agents can connect to MCP servers "
            "to access third-party tools and services."
        ),
        version="1.0",
        key_concepts=[
            "mcp", "model_context_protocol", "server", "external_tools",
        ],
        context_notes=(
            "MCP servers are registered and can be assigned to agents. "
            "The server directory provides pre-configured server templates. "
            "Server tools are discovered dynamically. MCP routes are in "
            "app.py (both v1 legacy and current endpoints)."
        ),
        depends_on=["agents"],
        entities=[
            EntityDefinition(
                name="MCPServer",
                description="A registered MCP server providing external tools",
                key_fields=["id", "name", "url", "status"],
            ),
        ],
        capabilities=[
            CapabilityDefinition(
                id="mcp.register_server",
                name="Register MCP Server",
                description="Add a new MCP server connection",
                category="create",
                required_role=2,
                tags=["mcp", "server", "register", "add"],
            ),
            CapabilityDefinition(
                id="mcp.list_servers",
                name="List MCP Servers",
                description="Get all registered MCP servers",
                category="read",
                required_role=2,
                tags=["mcp", "server", "list"],
            ),
            CapabilityDefinition(
                id="mcp.assign_to_agent",
                name="Assign Server to Agent",
                description="Link an MCP server to an agent",
                category="configure",
                required_context=["server_id", "agent_id"],
                requires_domains=["agents"],
                required_role=2,
                tags=["mcp", "agent", "assign"],
            ),
            CapabilityDefinition(
                id="mcp.discover_tools",
                name="Discover Server Tools",
                description="List tools available from an MCP server",
                category="read",
                required_context=["server_id"],
                required_role=2,
                tags=["mcp", "tools", "discover"],
            ),
            CapabilityDefinition(
                id="mcp.browse_directory",
                name="Browse Server Directory",
                description="Browse available MCP server templates",
                category="read",
                required_role=2,
                tags=["mcp", "directory", "browse", "templates"],
            ),
            CapabilityDefinition(
                id="mcp.create_server",
                name="Create MCP Server",
                description="Register a new MCP server with connection details",
                category="create",
                required_role=2,
                tags=["mcp", "server", "create", "register", "new"],
            ),
            CapabilityDefinition(
                id="mcp.delete_server",
                name="Delete MCP Server",
                description="Remove a registered MCP server",
                category="delete",
                required_context=["server_id"],
                required_role=2,
                tags=["mcp", "server", "delete", "remove"],
            ),
            CapabilityDefinition(
                id="mcp.test_server",
                name="Test MCP Server",
                description="Test connectivity to an MCP server",
                category="execute",
                required_role=2,
                tags=["mcp", "server", "test", "connectivity"],
            ),
            CapabilityDefinition(
                id="mcp.get_tools",
                name="Get Server Tools",
                description="Get available tools from a specific MCP server",
                category="read",
                required_context=["server_id"],
                required_role=2,
                tags=["mcp", "tools", "server", "list"],
            ),
        ],
    )


# ─── Schedules Domain ────────────────────────────────────────────────────

def _schedules_domain() -> DomainDefinition:
    return DomainDefinition(
        id="schedules",
        name="Workflow Scheduling",
        description=(
            "Schedule workflows to run automatically on a recurring basis. "
            "Supports cron expressions for flexible scheduling (daily, weekly, "
            "monthly, custom patterns). Schedules are managed by the JobSchedulerService."
        ),
        version="1.0",
        key_concepts=[
            "schedule", "cron", "recurring", "automation", "timer",
            "daily", "weekly", "monthly", "trigger", "workflow_schedule",
        ],
        context_notes=(
            "Schedules are stored in ScheduledJobs and ScheduleDefinitions tables. "
            "The JobSchedulerService (APScheduler) polls every 60 seconds for due schedules. "
            "Schedule types: 'cron' (recurring via cron expression), 'interval' (recurring at "
            "fixed intervals using interval_seconds/minutes/hours/days/weeks), and 'date' "
            "(one-time execution at a specific datetime). "
            "All schedule times are stored in UTC. The scheduler converts timezone_offset "
            "from local time to UTC on create/update. "
            "The ScheduledJobId in the ScheduledJobs table is different from the workflow ID — "
            "use schedules.list to find the ScheduledJobId for a given workflow."
        ),
        depends_on=["workflows"],
        entities=[
            EntityDefinition(
                name="ScheduledJob",
                description="A scheduled job that can trigger workflow executions",
                key_fields=["ScheduledJobId", "JobName", "JobType", "TargetId",
                            "Description", "CreatedBy", "IsActive"],
            ),
            EntityDefinition(
                name="ScheduleDefinition",
                description="The schedule timing details (cron, interval, or one-time)",
                key_fields=["ScheduleId", "ScheduledJobId", "ScheduleType",
                            "CronExpression", "StartDate", "EndDate",
                            "NextRunTime", "LastRunTime", "MaxRuns", "IsActive"],
            ),
            EntityDefinition(
                name="ScheduleExecutionHistory",
                description="Execution history tracking for scheduled runs",
                key_fields=["ExecutionId", "ScheduledJobId", "ScheduleId",
                            "Status", "StartedAt", "CompletedAt", "ResultMessage"],
            ),
        ],
        capabilities=[
            CapabilityDefinition(
                id="schedules.create",
                name="Create Workflow Schedule",
                description="Schedule a workflow to run on a recurring basis using cron expressions",
                category="create",
                required_context=["workflow_id"],
                requires_domains=["workflows"],
                required_role=2,
                tags=["schedule", "create", "cron", "recurring", "automation", "workflow"],
            ),
            CapabilityDefinition(
                id="schedules.list",
                name="List Workflow Schedules",
                description="Get all workflow schedules with their timing and status",
                category="read",
                required_role=2,
                tags=["schedule", "list", "view", "browse"],
            ),
            CapabilityDefinition(
                id="schedules.get",
                name="Get Schedule Details",
                description="Get details of a specific workflow schedule",
                category="read",
                required_context=["workflow_id", "schedule_id"],
                required_role=2,
                tags=["schedule", "details", "info"],
            ),
            CapabilityDefinition(
                id="schedules.update",
                name="Update Schedule",
                description="Modify a schedule's timing, active status, or run limits",
                category="update",
                required_context=["workflow_id", "schedule_id"],
                required_role=2,
                tags=["schedule", "modify", "edit", "update"],
            ),
            CapabilityDefinition(
                id="schedules.delete",
                name="Delete Schedule",
                description="Remove a workflow schedule",
                category="delete",
                required_context=["workflow_id", "schedule_id"],
                required_role=2,
                tags=["schedule", "remove", "delete"],
            ),
            CapabilityDefinition(
                id="schedules.run_now",
                name="Run Workflow Now",
                description="Trigger an immediate one-off execution of a workflow",
                category="execute",
                required_context=["workflow_id"],
                requires_domains=["workflows"],
                required_role=2,
                tags=["workflow", "run", "execute", "immediate", "trigger"],
            ),
        ],
    )
