"""
Platform Knowledge Layer
=========================
Operational guidance for the Builder Agent on how to effectively
use the AI Hub platform. This bridges the gap between knowing
what capabilities exist and knowing how to combine them correctly.

This knowledge is injected into the planning prompts so the LLM
understands platform-specific workflows and constraints.
"""

# ═══════════════════════════════════════════════════════════════════════
# CORE CONCEPTS
# ═══════════════════════════════════════════════════════════════════════

PLATFORM_OVERVIEW = """
AI Hub is a platform for building AI agents, workflows, and integrations.

CORE ENTITIES:
- **Agents**: AI assistants with personas, tools, and knowledge bases
  - General Agents: Conversational AI with tool access
  - Data Agents: Natural language database querying
- **Tools**: Capabilities agents can use (email, web search, custom APIs)
- **Knowledge Bases**: Document collections for RAG-powered responses
- **Workflows**: Automated multi-step processes with triggers and approvals
- **Connections**: Database connections for data agents
- **Integrations**: External service connections (Slack, Teams, etc.)
- **Jobs**: Scheduled tasks and background processing
- **MCP Servers**: Model Context Protocol servers for extended capabilities
"""

# ═══════════════════════════════════════════════════════════════════════
# DYNAMIC CONTEXT
# ═══════════════════════════════════════════════════════════════════════

DYNAMIC_CONTEXT_GUIDANCE = """
USING SYSTEM VALUES:
The system will provide you with available resources (tools, agents, connections, etc.)
when relevant to the user's request. When provided:
- Use the EXACT names/IDs from the system list
- System names may differ from display names (e.g., "send_email" vs "Email Tool")
- If you need a resource that isn't listed, ask the user for clarification
- COMPARE what exists vs. what your plan needs — identify gaps
- If a required resource is NOT in the system list, plan to CREATE it first
"""

# ═══════════════════════════════════════════════════════════════════════
# VALID CAPABILITY IDS
# ═══════════════════════════════════════════════════════════════════════

VALID_CAPABILITIES = """
VALID CAPABILITY IDS (use domain.action format):

AGENTS:
- agents.create — Create a new general agent
- agents.create_data_agent — Create a data agent for database querying
- agents.update — Update an agent's objective, description, or settings (does NOT change tool assignments — use agents.assign_tools for that)
- agents.delete — Remove an agent
- agents.list — List all agents
- agents.get — Retrieve full agent details including current tool assignments for a specific agent
- agents.assign_tools — Update tool assignments for an agent (uses the agent save endpoint)
- agents.list_tools — Get available tools organized by category
- agents.export — Export agent as portable package
- agents.import — Import agent from package file
- agents.chat — Send a message to an agent and get a response

KNOWLEDGE:
- knowledge.attach — Upload and attach a document as agent knowledge
- knowledge.list — List knowledge documents for an agent
- knowledge.detach — Remove a knowledge document from an agent

TOOLS:
- tools.create — Create a custom Python tool
- tools.delete — Remove a custom tool
- tools.list — List all tools by category

WORKFLOWS:
- workflows.create — Create and save a new workflow
- workflows.update — Update an existing workflow definition (for node-level changes like SQL edits, delegate to workflow_agent instead)
- workflows.delete — Remove a workflow
- workflows.list — List all workflows
- workflows.get — Get a workflow's full definition
- workflows.execute — Run a workflow
- workflows.monitor — Get execution history and status (supports filtering by workflow_id)
- workflows.rename — Rename a workflow

CONNECTIONS:
- connections.create — Create a database connection
- connections.update — Modify a connection
- connections.delete — Remove a connection
- connections.test — Test a connection
- connections.list — List all connections
- connections.get — Get details of a specific connection
- connections.query — Execute a SQL query against a connection
- connections.discover_tables — Auto-discover tables from a database (returns table list for user to select from)
- connections.analyze_tables — AI-analyze selected tables to populate the data dictionary (runs in background)
- connections.check_analysis_progress — Check progress of background analysis (only if user asks)

INTEGRATIONS:
- integrations.create — Set up an integration from a template (REST APIs, OAuth services, or cloud storage)
- integrations.delete — Remove an integration
- integrations.test — Test an integration connection
- integrations.list — List configured integrations
- integrations.list_templates — Get available integration templates
- integrations.list_operations — Get available operations for a configured integration
- integrations.execute_operation — Run an operation on a configured integration
Note: Cloud storage integrations (Azure Blob Storage) use the 'cloud_storage' auth type with a connection string. Operations include list_containers, list/upload/download/delete files, and generate shareable URLs.
AGENT INTEGRATION TOOLS: Agents can interact with integrations directly via 3 built-in tools:
- list_integrations — agent tool to list available integrations
- get_integration_operations — agent tool to discover operations on an integration
- execute_integration — agent tool to run an operation and get results
To give an agent integration access, assign these tool names via agents.assign_tools or agents.create.
These tools work alongside any other tools the agent has (web_search, email, custom tools, etc.).

DOCUMENTS:
- documents.list — List documents with filtering and search
- documents.update — Update document metadata
- documents.delete — Delete a document and its vector embeddings
- documents.search — Search documents using vector similarity
- documents.reprocess — Reprocess document vector embeddings

EMAIL:
- email.provision — Provision an email address for an agent
- email.configure — Update email configuration for an agent
- email.get — Get email configuration for an agent
- email.deprovision — Remove email from an agent

ENVIRONMENTS:
- environments.get — Get an agent's environment configuration
- environments.assign — Assign an environment to an agent
- environments.status — Get status of all environments

USERS (Admin only):
- users.list — List all users
- users.create — Create a new user account
- users.delete — Remove a user account
- users.create_group — Create a permission group
- users.list_groups — List all permission groups
- users.delete_group — Delete a permission group
- users.get_group_permissions — Get which agents are assigned to a group
- users.get_group_members — Get assigned and unassigned users for a group
- users.manage_group_members — Assign users and agent access permissions to a group

JOBS:
- jobs.create — Create a data processing job
- jobs.delete — Remove a job
- jobs.list — List all jobs
- jobs.schedule — Schedule a job for recurring execution

MCP (Model Context Protocol):
- mcp.list_servers — List all MCP servers
- mcp.create_server — Register a new MCP server
- mcp.delete_server — Remove an MCP server
- mcp.test_server — Test connectivity to an MCP server
- mcp.get_tools — Get available tools from an MCP server
- mcp.assign_to_agent — Assign MCP server tools to an agent
- mcp.browse_directory — Browse the MCP server directory

SCHEDULES (Workflow Scheduling):
- schedules.create — Schedule a workflow to run on a recurring basis (uses cron expressions)
- schedules.list — List all workflow schedules
- schedules.get — Get details of a specific schedule
- schedules.update — Modify a schedule's timing or settings
- schedules.delete — Remove a workflow schedule
- schedules.run_now — Trigger an immediate one-off execution of a workflow

CRON EXPRESSION REFERENCE (for schedules.create):
Format: "Minute Hour Day Month DayOfWeek"
- "0 8 * * *" → Daily at 8:00 AM
- "0 8 * * 1-5" → Weekdays at 8:00 AM
- "0 9 * * 1" → Every Monday at 9:00 AM
- "0 0 1 * *" → First of every month at midnight
- "*/15 * * * *" → Every 15 minutes
Note: Times are in UTC unless timezone_offset is provided.

IMPORTANT: Only use capability IDs from this list. Do not invent new ones.
"""

# ═══════════════════════════════════════════════════════════════════════
# PLANNING RULES
# ═══════════════════════════════════════════════════════════════════════

PLANNING_RULES = """
PLANNING GUIDELINES:

1. **Minimize Steps**: Combine operations when the API supports it.
   If parameters can be included in a create/update call, do it in one step.

2. **Use Valid Capabilities**: Only use capability IDs from the VALID CAPABILITY IDS list.
   The execution engine cannot handle invented capabilities.

3. **Use Exact System Values**: When the system provides available resources
   (tools, connections, agents, etc.), use the exact names/IDs provided.

4. **Chain Dependencies**: When steps depend on each other, subsequent steps
   can reference IDs from previous steps (e.g., agent_id from agents.create).

5. **Ask for Clarification**: If the user request is ambiguous or missing
   required information, ask before planning.

6. **File Usage**: `knowledge.attach` and `agents.import` accept:
   - A File ID from a prior upload
   - A full filesystem path provided by the user (e.g., "C:\data\file.txt")
   If the user provides a filesystem path, use it directly as the `file` value.
   Only note a file upload prerequisite if the user has not provided a path OR File ID
   (e.g., "Requires: user must upload a document or provide a file path").

7. **Check Prerequisites Before Planning**: Before creating any resource, verify its
   dependencies exist. For example, a data agent needs a connection — check if one exists,
   and if not, plan to create it first. A workflow with email needs an agent with email
   provisioned. Always plan dependency creation BEFORE the resource that needs it.

8. **Think End-to-End**: Don't just create individual resources — think about the full
   solution. If the user wants "inventory monitoring," that means: connection + data agent +
   workflow + schedule + notifications. Plan the COMPLETE solution, not just one piece.

9. **Be Resourceful**: If a needed capability doesn't exist as a built-in tool, consider
   creating a custom Python tool. If a needed integration isn't available, suggest
   alternatives. Exhaust every possibility before saying something can't be done.
"""

# ═══════════════════════════════════════════════════════════════════════
# DEPENDENCY CHAINS
# ═══════════════════════════════════════════════════════════════════════

DEPENDENCY_CHAINS = """
DEPENDENCY RULES — Create prerequisites BEFORE the resources that need them:

1. **Data Agents → Connections → Data Dictionary**: A data agent REQUIRES an existing
   database connection AND a populated data dictionary to function. The full chain is:
   a. Create/verify the connection (connections.create + connections.test)
   b. Create the data agent linked to the connection (agents.create_data_agent)
   c. Discover available tables (connections.discover_tables) — present the table list to the
      user and ask which tables to analyze for the data dictionary
   d. Analyze selected tables (connections.analyze_tables) — this runs in the background
      (10-30 seconds per table). Do NOT wait for completion. Inform the user that analysis
      is running and the data agent will be fully functional once it finishes.

   **CRITICAL — USE DIRECT API ACTIONS, NOT DELEGATION:**
   - ALWAYS use connections.discover_tables and connections.analyze_tables as direct API actions
   - NEVER delegate table discovery or analysis to agent:data_agent — the data agent cannot
     populate its own data dictionary (circular dependency)
   - NEVER delegate to any other agent for table discovery/analysis — these are platform
     operations that must be executed directly via the connections API

   IMPORTANT: Without steps c and d, the data agent cannot answer questions because it has
   no schema knowledge. Always include table discovery and analysis when creating a data agent.

2. **Tool Assignment → Tools Exist**: Before assigning tools to an agent (agents.assign_tools),
   verify the tools exist. If custom functionality is needed, create a custom tool first (tools.create).

3. **Scheduling → Workflow Exists**: A schedule (schedules.create) needs a workflow to trigger.
   Create the workflow first, then create the schedule referencing it.

4. **Knowledge Base → Agent Exists**: To attach knowledge to an agent (knowledge.create),
   the agent must already exist. Create the agent first.

5. **Email Provisioning → Agent Exists**: To provision email for an agent (email.provision),
   the agent must exist first. Create the agent, then provision email.

6. **Integration Actions → Integration Template**: Some integrations require templates
   or configurations. Check what's available before assuming an integration exists.
   To give an agent direct access to an integration's data, assign these tools to the agent:
   list_integrations, get_integration_operations, execute_integration. The agent can then
   query the integration in real-time during chat (e.g., "get my Stripe balance").

7. **MCP Tool Access → Both Exist**: MCP tools require both an MCP server connection
   AND an agent. Ensure the MCP server is connected and the agent exists.

SOLUTION ARCHITECTURE — Think like an architect:
When designing a solution, follow this framework:
1. ANALYZE: What is the user trying to achieve? What's the end-to-end flow?
2. INVENTORY: What resources already exist in the system? (Check the system context)
3. GAP ANALYSIS: What's missing? What needs to be created?
4. DESIGN: What's the optimal combination of agents, tools, workflows, and connections?
5. GATHER: What information do you need from the user? (credentials, business rules, etc.)

CREATING CUSTOM TOOLS:
When no built-in tool meets a need, create a custom Python tool:
- Use tools.create with a Python function that implements the logic
- The tool becomes assignable to any agent
- Useful for: custom API integrations, data transformations, business logic, calculations

MULTI-RESOURCE SOLUTIONS — Common patterns:
- **Database Monitoring**: Connection → Data Agent → Discover Tables → Analyze Tables → Workflow (query + alert) → Schedule
- **Data Agent Setup**: Connection → Test → Data Agent → Discover Tables → (user selects) → Analyze Tables (background)
- **Email Processing**: Agent → Email Provision → Workflow (trigger on email) → Tools
- **Report Generation**: Connection → Data Agent → Discover Tables → Analyze Tables → Custom Tool (format) → Workflow → Schedule
- **External Service Agent**: Integration (create + test) → Agent + assign integration tools (list_integrations, get_integration_operations, execute_integration) → Chat directly with external data
- **Integration Workflow**: Integration (create + test) → Workflow (integration execute node) → Schedule
"""

# ═══════════════════════════════════════════════════════════════════════
# COMBINED KNOWLEDGE FOR PROMPTS
# ═══════════════════════════════════════════════════════════════════════

def get_planning_knowledge() -> str:
    """
    Returns the combined platform knowledge for injection into planning prompts.
    """
    return f"""
{PLATFORM_OVERVIEW}

{DYNAMIC_CONTEXT_GUIDANCE}

{VALID_CAPABILITIES}

{PLANNING_RULES}

{DEPENDENCY_CHAINS}
"""


def get_capability_list() -> str:
    """
    Returns just the valid capability IDs for injection into extraction prompts.
    """
    return VALID_CAPABILITIES
