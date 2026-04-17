"""
Context Gatherer
=================
Fetches dynamic system values before planning so the LLM has accurate,
up-to-date information about available tools, connections, agents, etc.

This solves the "brittle hardcoding" problem by:
1. Querying the system for actual values
2. Providing those values to the LLM in the planning prompt
3. Validating/correcting LLM outputs against known values
"""

import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


@dataclass
class SystemContext:
    """
    Holds dynamic system values fetched before planning.
    """
    # Tools
    core_tools: List[Dict[str, str]] = field(default_factory=list)  # [{"name": "email_tool", "display_name": "Email"}]
    custom_tools: List[Dict[str, str]] = field(default_factory=list)

    # Agents (for referencing existing agents)
    agents: List[Dict[str, Any]] = field(default_factory=list)  # [{"id": 1, "name": "My Agent"}]

    # Connections (for data agents)
    connections: List[Dict[str, Any]] = field(default_factory=list)

    # Knowledge bases
    knowledge_bases: List[Dict[str, Any]] = field(default_factory=list)

    # Workflows
    workflows: List[Dict[str, Any]] = field(default_factory=list)

    # Schedules
    schedules: List[Dict[str, Any]] = field(default_factory=list)

    # Integrations
    integrations: List[Dict[str, Any]] = field(default_factory=list)

    # MCP Servers
    mcp_servers: List[Dict[str, Any]] = field(default_factory=list)

    # Documents
    documents: List[Dict[str, Any]] = field(default_factory=list)

    # Users
    users: List[Dict[str, Any]] = field(default_factory=list)

    # Lookup maps for validation (built from the lists above)
    _tool_name_map: Dict[str, str] = field(default_factory=dict)  # display_name -> system_name
    _connection_name_map: Dict[str, int] = field(default_factory=dict)  # name -> id
    _workflow_name_map: Dict[str, int] = field(default_factory=dict)  # name -> id
    _agent_name_map: Dict[str, int] = field(default_factory=dict)  # name -> id

    def build_lookup_maps(self):
        """Build reverse lookup maps for validation."""
        self._tool_name_map = {}
        self._connection_name_map = {}
        self._workflow_name_map = {}
        self._agent_name_map = {}

        for tool in self.core_tools + self.custom_tools:
            system_name = tool.get("name", "")
            display_name = tool.get("display_name", "")

            # Map various forms to the system name
            self._tool_name_map[system_name.lower()] = system_name
            self._tool_name_map[display_name.lower()] = system_name

            # Also map common variations
            # "Email Tool" -> "email_tool", "email" -> "email_tool"
            simple_name = display_name.lower().replace(" tool", "").replace(" ", "_")
            self._tool_name_map[simple_name] = system_name

        for conn in self.connections:
            name = conn.get("name", "")
            conn_id = conn.get("id")
            if name and conn_id is not None:
                self._connection_name_map[name.lower()] = conn_id

        for wf in self.workflows:
            name = wf.get("name", "")
            wf_id = wf.get("id")
            if name and wf_id is not None:
                self._workflow_name_map[name.lower()] = wf_id

        for agent in self.agents:
            name = agent.get("description", "")
            agent_id = agent.get("id")
            if name and agent_id is not None:
                self._agent_name_map[name.lower()] = agent_id

    def resolve_tool_name(self, user_input: str) -> Optional[str]:
        """
        Resolve a user-provided tool name to the actual system name.
        Returns None if no match found.
        """
        if not user_input:
            return None

        normalized = user_input.lower().strip()

        # Direct match
        if normalized in self._tool_name_map:
            return self._tool_name_map[normalized]

        # Fuzzy match - find best match above threshold
        best_match = None
        best_score = 0.0
        threshold = 0.7

        for key, system_name in self._tool_name_map.items():
            score = SequenceMatcher(None, normalized, key).ratio()
            if score > best_score and score >= threshold:
                best_score = score
                best_match = system_name

        if best_match:
            logger.info(f"  [context] Fuzzy matched '{user_input}' → '{best_match}' (score: {best_score:.2f})")

        return best_match

    def resolve_connection_name(self, user_input: str) -> Optional[int]:
        """
        Resolve a user-provided connection name to its ID.
        Returns None if no match found.
        """
        if not user_input:
            return None

        normalized = user_input.lower().strip()

        # Direct match
        if normalized in self._connection_name_map:
            return self._connection_name_map[normalized]

        # Try as integer ID
        try:
            conn_id = int(user_input)
            if any(c.get("id") == conn_id for c in self.connections):
                return conn_id
        except (ValueError, TypeError):
            pass

        # Fuzzy match
        best_match_id = None
        best_score = 0.0
        threshold = 0.7

        for key, conn_id in self._connection_name_map.items():
            score = SequenceMatcher(None, normalized, key).ratio()
            if score > best_score and score >= threshold:
                best_score = score
                best_match_id = conn_id

        if best_match_id is not None:
            logger.info(f"  [context] Fuzzy matched connection '{user_input}' → ID {best_match_id} (score: {best_score:.2f})")

        return best_match_id

    def resolve_workflow_name(self, user_input: str) -> Optional[int]:
        """
        Resolve a user-provided workflow name to its ID.
        Returns None if no match found.
        """
        if not user_input:
            return None

        normalized = user_input.lower().strip()

        # Direct match
        if normalized in self._workflow_name_map:
            return self._workflow_name_map[normalized]

        # Try as integer ID
        try:
            wf_id = int(user_input)
            if any(w.get("id") == wf_id for w in self.workflows):
                return wf_id
        except (ValueError, TypeError):
            pass

        # Fuzzy match
        best_match_id = None
        best_score = 0.0
        threshold = 0.7

        for key, wf_id in self._workflow_name_map.items():
            score = SequenceMatcher(None, normalized, key).ratio()
            if score > best_score and score >= threshold:
                best_score = score
                best_match_id = wf_id

        if best_match_id is not None:
            logger.info(f"  [context] Fuzzy matched workflow '{user_input}' → ID {best_match_id} (score: {best_score:.2f})")

        return best_match_id

    def resolve_agent_name(self, user_input: str) -> Optional[int]:
        """
        Resolve a user-provided agent name to its ID.
        Returns None if no match found.
        """
        if not user_input:
            return None

        normalized = user_input.lower().strip()

        # Direct match
        if normalized in self._agent_name_map:
            return self._agent_name_map[normalized]

        # Try as integer ID
        try:
            agent_id = int(user_input)
            if any(a.get("id") == agent_id for a in self.agents):
                return agent_id
        except (ValueError, TypeError):
            pass

        # Fuzzy match
        best_match_id = None
        best_score = 0.0
        threshold = 0.7

        for key, aid in self._agent_name_map.items():
            score = SequenceMatcher(None, normalized, key).ratio()
            if score > best_score and score >= threshold:
                best_score = score
                best_match_id = aid

        if best_match_id is not None:
            logger.info(f"  [context] Fuzzy matched agent '{user_input}' -> ID {best_match_id} (score: {best_score:.2f})")

        return best_match_id

    def get_tools_for_prompt(self) -> str:
        """Format tools for inclusion in the planning prompt."""
        if not self.core_tools and not self.custom_tools:
            return "No tools available (system lookup failed)"

        lines = []

        if self.core_tools:
            lines.append("CORE TOOLS (built-in):")
            for tool in self.core_tools:
                name = tool.get("name", "")
                display = tool.get("display_name", name)
                desc = tool.get("description", "")
                lines.append(f'  - "{name}" ({display}): {desc}')

        if self.custom_tools:
            lines.append("\nCUSTOM TOOLS (user-created):")
            for tool in self.custom_tools:
                name = tool.get("name", "")
                display = tool.get("display_name", name)
                lines.append(f'  - "{name}" ({display})')

        lines.append("\nIMPORTANT: Use the exact tool name in quotes (e.g., \"email_tool\" not \"Email\")")

        return "\n".join(lines)

    def get_agents_for_prompt(self) -> str:
        """Format existing agents for inclusion in the planning prompt."""
        if not self.agents:
            return "No existing agents found"

        enabled_count = sum(1 for a in self.agents if a.get("enabled", True))
        disabled_count = len(self.agents) - enabled_count
        lines = [f"EXISTING AGENTS ({len(self.agents)} total — {enabled_count} enabled, {disabled_count} disabled):"]
        for agent in self.agents:
            agent_id = agent.get("id", "")
            name = agent.get("description", agent.get("name", "Unknown"))
            enabled = agent.get("enabled", True)
            status = "enabled" if enabled else "DISABLED"
            lines.append(f'  - ID {agent_id}: "{name}" [{status}]')

        return "\n".join(lines)

    def get_connections_for_prompt(self) -> str:
        """Format connections for inclusion in the planning prompt."""
        if not self.connections:
            return "No database connections found"

        lines = ["DATABASE CONNECTIONS:"]
        for conn in self.connections:
            conn_id = conn.get("id", "")
            name = conn.get("name", "Unknown")
            db_type = conn.get("type", "")
            lines.append(f'  - ID {conn_id}: "{name}" ({db_type})')

        return "\n".join(lines)

    def get_workflows_for_prompt(self) -> str:
        """Format workflows for inclusion in the planning prompt."""
        if not self.workflows:
            return ""

        lines = ["EXISTING WORKFLOWS:"]
        for wf in self.workflows:
            wf_id = wf.get("id", "")
            name = wf.get("name", "Unknown")
            category = wf.get("category", "")
            suffix = f" [{category}]" if category else ""
            lines.append(f'  - ID {wf_id}: "{name}"{suffix}')

        return "\n".join(lines)

    def get_schedules_for_prompt(self) -> str:
        """Format schedules for inclusion in the planning prompt."""
        if not self.schedules:
            return ""

        lines = ["WORKFLOW SCHEDULES:"]
        for schedule in self.schedules:
            schedule_id = schedule.get("id", "")
            scheduled_job_id = schedule.get("scheduled_job_id", "")
            workflow_id = schedule.get("workflow_id", "")
            workflow_name = schedule.get("workflow_name", "Unknown")
            schedule_type = schedule.get("type", "")
            cron = schedule.get("cron_expression", "")
            is_active = schedule.get("is_active", False)
            next_run = schedule.get("next_run_time", "")
            status = "ACTIVE" if is_active else "inactive"

            schedule_info = f'  - ScheduleId {schedule_id} (ScheduledJobId {scheduled_job_id}): Workflow "{workflow_name}" (WorkflowId {workflow_id})'
            if schedule_type:
                schedule_info += f" | Type: {schedule_type}"
            if cron:
                schedule_info += f" | Cron: {cron}"
            if next_run:
                schedule_info += f" | Next run: {next_run}"
            schedule_info += f" | Status: {status}"
            lines.append(schedule_info)

        return "\n".join(lines)

    def get_integrations_for_prompt(self) -> str:
        """Format integrations for inclusion in the planning prompt."""
        if not self.integrations:
            return ""

        lines = ["CONFIGURED INTEGRATIONS:"]
        for integ in self.integrations:
            integ_id = integ.get("integration_id", integ.get("id", ""))
            name = integ.get("integration_name", integ.get("name", "Unknown"))
            template = integ.get("template_key", "")
            connected = integ.get("is_connected", False)
            lines.append(f'  - ID {integ_id}: "{name}" ({template}) connected={connected}')

        return "\n".join(lines)

    def get_mcp_servers_for_prompt(self) -> str:
        """Format MCP servers for inclusion in the planning prompt."""
        if not self.mcp_servers:
            return ""

        lines = ["MCP SERVERS:"]
        for server in self.mcp_servers:
            server_id = server.get("id", "")
            name = server.get("name", "Unknown")
            lines.append(f'  - ID {server_id}: "{name}"')

        return "\n".join(lines)

    def get_documents_for_prompt(self) -> str:
        """Format documents for inclusion in the planning prompt."""
        if not self.documents:
            return ""

        lines = ["DOCUMENTS:"]
        for doc in self.documents:
            doc_id = doc.get("id", "")
            name = doc.get("name", "Unknown")
            doc_type = doc.get("type", "")
            uploaded = doc.get("uploaded", "")
            entry = f'  - ID {doc_id}: "{name}"'
            if doc_type:
                entry += f" [{doc_type}]"
            if uploaded:
                entry += f" (uploaded: {uploaded})"
            lines.append(entry)

        return "\n".join(lines)

    def get_users_for_prompt(self) -> str:
        """Format users for inclusion in the planning prompt."""
        if not self.users:
            return ""

        lines = ["USERS:"]
        for user in self.users:
            user_id = user.get("id", "")
            username = user.get("username", "Unknown")
            name = user.get("name", "")
            role = user.get("role", "")
            display = f'"{username}"'
            if name:
                display += f" ({name})"
            if role:
                display += f" [{role}]"
            lines.append(f"  - ID {user_id}: {display}")

        return "\n".join(lines)

    def get_full_context_for_prompt(self) -> str:
        """Get all context formatted for inclusion in the planning prompt."""
        sections = [
            "=" * 60,
            "AVAILABLE SYSTEM RESOURCES (use exact names/IDs from this list)",
            "=" * 60,
            "",
            self.get_tools_for_prompt(),
            "",
            self.get_agents_for_prompt(),
            "",
            self.get_connections_for_prompt(),
        ]

        # Only include sections that have data
        for getter in [
            self.get_workflows_for_prompt,
            self.get_schedules_for_prompt,
            self.get_integrations_for_prompt,
            self.get_mcp_servers_for_prompt,
            self.get_documents_for_prompt,
            self.get_users_for_prompt,
        ]:
            section = getter()
            if section:
                sections.append("")
                sections.append(section)

        sections.append("")
        sections.append("=" * 60)
        return "\n".join(sections)


class ContextGatherer:
    """
    Gathers dynamic context from the AI Hub API before planning.
    """

    def __init__(self, executor):
        """
        Args:
            executor: ActionExecutor instance for making API calls
        """
        self.executor = executor

    async def gather_context(self, domains_needed: List[str] = None) -> SystemContext:
        """
        Fetch context from the system based on what domains are likely needed.

        Args:
            domains_needed: List of domains to fetch context for.
                          If None, fetches tools (most common need).
        """
        context = SystemContext()

        if domains_needed is None:
            domains_needed = ["tools"]  # Default to tools as most common

        # Fetch tools if needed
        if "tools" in domains_needed or "agents" in domains_needed:
            await self._fetch_tools(context)

        # Fetch existing agents if needed
        if "agents" in domains_needed:
            await self._fetch_agents(context)

        # Fetch connections if data agents might be involved
        if "connections" in domains_needed:
            await self._fetch_connections(context)

        # Fetch workflows if workflow operations are involved
        if "workflows" in domains_needed or "schedules" in domains_needed:
            await self._fetch_workflows(context)

        # Fetch schedules if schedule operations are involved
        if "schedules" in domains_needed:
            await self._fetch_schedules(context)

        # Fetch integrations if integration operations are involved
        if "integrations" in domains_needed:
            await self._fetch_integrations(context)

        # Fetch MCP servers if MCP operations are involved
        if "mcp" in domains_needed:
            await self._fetch_mcp_servers(context)

        # Fetch documents if document operations are involved
        if "documents" in domains_needed:
            await self._fetch_documents(context)

        # Fetch users if user/permission operations are involved
        if "users" in domains_needed:
            await self._fetch_users(context)

        # Build lookup maps for validation
        context.build_lookup_maps()

        return context

    async def _fetch_tools(self, context: SystemContext):
        """Fetch available tools from the system."""
        try:
            result = await self.executor.execute_step(
                domain="agents",
                action="list_tools",
                parameters={},
                description="Fetch available tools"
            )

            if result.is_success and result.data:
                categories = result.data.get("categories", {})

                for category_name, category_data in categories.items():
                    tools = category_data.get("tools", [])
                    for tool in tools:
                        tool_info = {
                            "name": tool.get("name", ""),
                            "display_name": tool.get("display_name", ""),
                            "description": tool.get("description", ""),
                            "category": category_name,
                        }

                        # Categorize as core or custom based on category
                        if category_name.lower() in ["core", "built-in", "system"]:
                            context.core_tools.append(tool_info)
                        else:
                            # For now, treat all as core since the API groups by category
                            context.core_tools.append(tool_info)

                logger.info(f"  [context] Fetched {len(context.core_tools)} core tools, {len(context.custom_tools)} custom tools")
            else:
                logger.warning(f"  [context] Failed to fetch tools: {result.error}")

        except Exception as e:
            logger.error(f"  [context] Error fetching tools: {e}")

    async def _fetch_agents(self, context: SystemContext):
        """Fetch existing agents from the system."""
        try:
            result = await self.executor.execute_step(
                domain="agents",
                action="list",
                parameters={},
                description="Fetch existing agents"
            )

            if result.is_success and result.data:
                agents = result.data.get("agents", [])
                for agent in agents:
                    context.agents.append({
                        "id": agent.get("agent_id"),
                        "description": agent.get("agent_name", ""),
                        "enabled": agent.get("enabled", True),
                        "created_date": agent.get("created_date"),
                    })

                logger.info(f"  [context] Fetched {len(context.agents)} existing agents")
            else:
                logger.warning(f"  [context] Failed to fetch agents: {result.error}")

        except Exception as e:
            logger.error(f"  [context] Error fetching agents: {e}")

    async def _fetch_connections(self, context: SystemContext):
        """Fetch database connections from the system."""
        try:
            result = await self.executor.execute_step(
                domain="connections",
                action="list",
                parameters={},
                description="Fetch database connections"
            )

            if result.is_success and result.data:
                raw = result.data

                # The /api/connections GET route calls get_connections() which
                # returns jsonify(dataframe_to_json(df)).  dataframe_to_json
                # produces a JSON *string*, then jsonify wraps it again —
                # double-encoding.  The executor's response.json() gives back
                # a Python string that needs a second json.loads().
                # Additionally, the response may come back as:
                #   - a dict with a "connections" key (from response_mappings)
                #   - a raw string (double-encoded)
                #   - a list of connection dicts directly
                import json

                # Unwrap response_mapping wrapper if present
                if isinstance(raw, dict):
                    raw = raw.get("connections", raw.get("data", raw))

                # Handle double-encoded JSON strings
                max_parses = 3
                while isinstance(raw, str) and max_parses > 0:
                    try:
                        raw = json.loads(raw)
                        max_parses -= 1
                    except (json.JSONDecodeError, ValueError):
                        logger.warning("  [context] Could not parse connections string as JSON")
                        raw = []
                        break

                # Normalize: could be a list or a dict with a data key
                if isinstance(raw, list):
                    connections = raw
                elif isinstance(raw, dict):
                    connections = raw.get("connections", raw.get("data", []))
                else:
                    connections = []

                for conn in connections:
                    if isinstance(conn, str):
                        try:
                            conn = json.loads(conn)
                        except (json.JSONDecodeError, ValueError):
                            continue
                    if not isinstance(conn, dict):
                        continue
                    # Support both legacy field names (connection_id,
                    # connection_name, database_type) and normalised names
                    # (id, name, type).
                    context.connections.append({
                        "id": conn.get("id") or conn.get("connection_id"),
                        "name": conn.get("name", "") or conn.get("connection_name", ""),
                        "type": conn.get("type", "") or conn.get("database_type", ""),
                    })

                logger.info(f"  [context] Fetched {len(context.connections)} connections")
            else:
                logger.warning(f"  [context] Failed to fetch connections: {result.error}")

        except Exception as e:
            logger.error(f"  [context] Error fetching connections: {e}")

    async def _fetch_workflows(self, context: SystemContext):
        """Fetch existing workflows from the system."""
        try:
            result = await self.executor.execute_step(
                domain="workflows",
                action="list",
                parameters={},
                description="Fetch existing workflows"
            )

            if result.is_success and result.data:
                raw = result.data

                # The /get/workflows route uses jsonify(dataframe_to_json(df))
                # which double-encodes: dataframe_to_json returns a JSON string,
                # jsonify wraps it as a JSON string literal.  So response.json()
                # gives back a Python string that needs a second parse.
                if isinstance(raw, str):
                    import json
                    try:
                        raw = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        logger.warning("  [context] Could not parse workflows string as JSON")
                        raw = []

                # Normalize: could be a list directly or a dict with a data key
                if isinstance(raw, list):
                    workflows = raw
                elif isinstance(raw, dict):
                    workflows = raw.get("data", raw.get("workflows", []))
                else:
                    workflows = []

                for wf in workflows:
                    if not isinstance(wf, dict):
                        continue
                    context.workflows.append({
                        "id": wf.get("id", wf.get("workflow_id")),
                        "name": wf.get("workflow_name", wf.get("name", wf.get("filename", "Unknown"))),
                        "category": wf.get("category", ""),
                    })

                logger.info(f"  [context] Fetched {len(context.workflows)} workflows")
            else:
                logger.warning(f"  [context] Failed to fetch workflows: {result.error}")

        except Exception as e:
            logger.error(f"  [context] Error fetching workflows: {e}")

    async def _fetch_schedules(self, context: SystemContext):
        """Fetch workflow schedules from the system."""
        try:
            result = await self.executor.execute_step(
                domain="schedules",
                action="list",
                parameters={},
                description="Fetch workflow schedules"
            )

            if result.is_success and result.data:
                raw = result.data

                # The /api/scheduler/types/workflow/schedules route returns
                # {data: [...schedules...]}.  The response_mapping maps "schedules" → "data".
                # Handle both the raw response and the mapped response.
                import json

                # Unwrap response_mapping wrapper if present
                if isinstance(raw, dict):
                    raw = raw.get("schedules", raw.get("data", raw))

                # Handle double-encoded JSON strings (same pattern as workflows/connections)
                max_parses = 3
                while isinstance(raw, str) and max_parses > 0:
                    try:
                        raw = json.loads(raw)
                        max_parses -= 1
                    except (json.JSONDecodeError, ValueError):
                        logger.warning("  [context] Could not parse schedules string as JSON")
                        raw = []
                        break

                # Normalize: could be a list directly or a dict with a data key
                if isinstance(raw, list):
                    schedules = raw
                elif isinstance(raw, dict):
                    schedules = raw.get("schedules", raw.get("data", []))
                else:
                    schedules = []

                for schedule in schedules:
                    if isinstance(schedule, str):
                        try:
                            schedule = json.loads(schedule)
                        except (json.JSONDecodeError, ValueError):
                            continue
                    if not isinstance(schedule, dict):
                        continue
                    context.schedules.append({
                        "id": schedule.get("id"),
                        "scheduled_job_id": schedule.get("scheduled_job_id"),
                        "workflow_id": schedule.get("workflow_id"),
                        "workflow_name": schedule.get("workflow_name", ""),
                        "type": schedule.get("type", ""),
                        "cron_expression": schedule.get("cron_expression", ""),
                        "is_active": schedule.get("is_active", False),
                        "next_run_time": schedule.get("next_run_time", ""),
                        "created_at": schedule.get("created_at", ""),
                    })

                logger.info(f"  [context] Fetched {len(context.schedules)} workflow schedules")
            else:
                logger.warning(f"  [context] Failed to fetch schedules: {result.error}")

        except Exception as e:
            logger.error(f"  [context] Error fetching schedules: {e}")

    async def _fetch_integrations(self, context: SystemContext):
        """Fetch configured integrations from the system."""
        try:
            result = await self.executor.execute_step(
                domain="integrations",
                action="list",
                parameters={},
                description="Fetch configured integrations"
            )

            if result.is_success and result.data:
                integrations = result.data.get("integrations", [])
                for integ in integrations:
                    context.integrations.append({
                        "integration_id": integ.get("integration_id", integ.get("id")),
                        "integration_name": integ.get("integration_name", integ.get("name", "")),
                        "template_key": integ.get("template_key", ""),
                        "is_connected": integ.get("is_connected", False),
                        "auth_type": integ.get("auth_type", ""),
                        "platform_name": integ.get("platform_name", ""),
                    })

                logger.info(f"  [context] Fetched {len(context.integrations)} integrations")
            else:
                logger.warning(f"  [context] Failed to fetch integrations: {result.error}")

        except Exception as e:
            logger.error(f"  [context] Error fetching integrations: {e}")

    async def _fetch_mcp_servers(self, context: SystemContext):
        """Fetch MCP servers from the system."""
        try:
            result = await self.executor.execute_step(
                domain="mcp",
                action="list_servers",
                parameters={},
                description="Fetch MCP servers"
            )

            if result.is_success and result.data:
                servers = result.data if isinstance(result.data, list) else result.data.get("servers", [])
                for server in servers:
                    context.mcp_servers.append({
                        "id": server.get("id"),
                        "name": server.get("name", ""),
                    })

                logger.info(f"  [context] Fetched {len(context.mcp_servers)} MCP servers")
            else:
                logger.warning(f"  [context] Failed to fetch MCP servers: {result.error}")

        except Exception as e:
            logger.error(f"  [context] Error fetching MCP servers: {e}")

    async def _fetch_users(self, context: SystemContext):
        """Fetch users from the system."""
        try:
            result = await self.executor.execute_step(
                domain="users",
                action="list",
                parameters={},
                description="Fetch users"
            )

            if result.is_success and result.data:
                raw = result.data

                # The /get/users route uses jsonify(dataframe_to_json(df))
                # which double-encodes: dataframe_to_json returns a JSON string,
                # jsonify wraps it as a JSON string literal. So response.json()
                # gives back a Python string that needs a second parse.
                # Keep parsing until we get a non-string result.
                import json
                max_parses = 3
                while isinstance(raw, str) and max_parses > 0:
                    try:
                        raw = json.loads(raw)
                        max_parses -= 1
                    except (json.JSONDecodeError, ValueError):
                        logger.warning("  [context] Could not parse users string as JSON")
                        raw = []
                        break

                # Normalize: could be a list directly or a dict with a data key
                if isinstance(raw, list):
                    users = raw
                elif isinstance(raw, dict):
                    users = raw.get("data", raw.get("users", []))
                else:
                    users = []

                for user in users:
                    # Skip non-dict entries (edge case: malformed data or
                    # double-encoded JSON where entries are still strings)
                    if isinstance(user, str):
                        try:
                            user = json.loads(user)
                        except (json.JSONDecodeError, ValueError):
                            continue
                    if not isinstance(user, dict):
                        continue
                    try:
                        context.users.append({
                            "id": user.get("id"),
                            "username": user.get("username", ""),
                            "name": user.get("name", ""),
                            "role": user.get("role", ""),
                        })
                    except AttributeError:
                        # user somehow isn't a dict despite the check
                        continue

                logger.info(f"  [context] Fetched {len(context.users)} users")
            else:
                logger.warning(f"  [context] Failed to fetch users: {result.error}")

        except Exception as e:
            logger.error(f"  [context] Error fetching users: {e}")

    async def _fetch_documents(self, context: SystemContext):
        """Fetch documents from the system."""
        try:
            result = await self.executor.execute_step(
                domain="documents",
                action="list",
                parameters={},
                description="Fetch documents"
            )

            if result.is_success and result.data:
                docs = result.data.get("documents", [])
                if isinstance(docs, list):
                    for doc in docs:
                        if isinstance(doc, dict):
                            context.documents.append({
                                "id": doc.get("id") or doc.get("document_id"),
                                "name": doc.get("name") or doc.get("filename", ""),
                                "type": doc.get("type") or doc.get("file_type", ""),
                                "uploaded": doc.get("uploaded") or doc.get("upload_date", ""),
                            })

                logger.info(f"  [context] Fetched {len(context.documents)} documents")
            else:
                logger.warning(f"  [context] Failed to fetch documents: {result.error}")

        except Exception as e:
            logger.error(f"  [context] Error fetching documents: {e}")

    # ─── Detail Fetch Methods ────────────────────────────────────────────

    async def fetch_agent_detail(self, agent_id: int) -> Optional[Dict[str, Any]]:
        """
        Fetch detailed information about a specific agent.

        The /get/agent_info endpoint returns ALL agents with id, description,
        and objective. We filter client-side for the specific agent_id.

        Args:
            agent_id: The agent ID to fetch details for

        Returns:
            Dict with agent details, or None if fetch failed
        """
        try:
            result = await self.executor.execute_step(
                domain="agents",
                action="get",
                parameters={"agent_id": agent_id},
                description=f"Fetch agent details for ID {agent_id}"
            )

            if result.is_success and result.data:
                # The /get/agent_info endpoint returns a list of all agents.
                # We need to find the specific agent by ID.
                agents_data = result.data
                if isinstance(agents_data, dict):
                    agents_data = agents_data.get("agents", agents_data.get("data", agents_data))

                # If it's a list, filter for our agent_id
                if isinstance(agents_data, list):
                    for agent in agents_data:
                        if agent.get("id") == agent_id:
                            logger.info(f"  [context] Found agent {agent_id} in detail response")
                            return agent
                    logger.warning(f"  [context] Agent {agent_id} not found in response list")
                    return None
                elif isinstance(agents_data, dict):
                    # Single agent response
                    return agents_data
                else:
                    logger.warning(f"  [context] Unexpected response type: {type(agents_data)}")
                    return None
            else:
                logger.warning(f"  [context] Failed to fetch agent {agent_id}: {result.error}")
                return None

        except Exception as e:
            logger.error(f"  [context] Error fetching agent {agent_id}: {e}")
            return None

    async def fetch_connection_detail(self, connection_id: int) -> Optional[Dict[str, Any]]:
        """
        Fetch detailed information about a specific connection.

        Args:
            connection_id: The connection ID to fetch details for

        Returns:
            Dict with connection details, or None if fetch failed
        """
        try:
            result = await self.executor.execute_step(
                domain="connections",
                action="get",
                parameters={"connection_id": connection_id},
                description=f"Fetch connection details for ID {connection_id}"
            )

            if result.is_success and result.data:
                return result.data.get("connection", result.data)
            else:
                logger.warning(f"  [context] Failed to fetch connection {connection_id}: {result.error}")
                return None

        except Exception as e:
            logger.error(f"  [context] Error fetching connection {connection_id}: {e}")
            return None

    async def fetch_workflow_detail(self, workflow_id: int) -> Optional[Dict[str, Any]]:
        """
        Fetch detailed information about a specific workflow.

        Args:
            workflow_id: The workflow ID to fetch details for

        Returns:
            Dict with workflow details, or None if fetch failed
        """
        try:
            result = await self.executor.execute_step(
                domain="workflows",
                action="get",
                parameters={"workflow_id": workflow_id},
                description=f"Fetch workflow details for ID {workflow_id}"
            )

            if result.is_success and result.data:
                return result.data.get("workflow", result.data)
            else:
                logger.warning(f"  [context] Failed to fetch workflow {workflow_id}: {result.error}")
                return None

        except Exception as e:
            logger.error(f"  [context] Error fetching workflow {workflow_id}: {e}")
            return None

    async def fetch_agent_email_config(self, agent_id: int) -> Optional[Dict[str, Any]]:
        """
        Fetch email configuration for a specific agent.

        Args:
            agent_id: The agent ID to fetch email config for

        Returns:
            Dict with email config details, or None if fetch failed or not configured
        """
        try:
            result = await self.executor.execute_step(
                domain="email",
                action="get",
                parameters={"agent_id": agent_id},
                description=f"Fetch email config for agent {agent_id}"
            )

            if result.is_success and result.data:
                # The response_mapping maps "config" → "config"
                config = result.data.get("config", result.data)
                return config
            else:
                logger.info(f"  [context] No email config found for agent {agent_id}: {result.error}")
                return None

        except Exception as e:
            logger.error(f"  [context] Error fetching email config for agent {agent_id}: {e}")
            return None

    async def fetch_integration_operations(self, integration_id: int) -> Optional[Dict[str, Any]]:
        """
        Fetch the list of operations available for a specific integration.

        Args:
            integration_id: The integration ID to fetch operations for

        Returns:
            Dict with operations list, or None if fetch failed
        """
        try:
            result = await self.executor.execute_step(
                domain="integrations",
                action="list_operations",
                parameters={"integration_id": integration_id},
                description=f"Fetch operations for integration {integration_id}"
            )

            if result.is_success and result.data:
                return result.data
            else:
                logger.warning(f"  [context] Failed to fetch operations for integration {integration_id}: {result.error}")
                return None

        except Exception as e:
            logger.error(f"  [context] Error fetching operations for integration {integration_id}: {e}")
            return None

    async def execute_integration_operation(
        self, integration_id: int, operation_key: str, parameters: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Execute a specific operation on an integration.

        Args:
            integration_id: The integration ID to execute on
            operation_key: The operation key (e.g., 'get_customers', 'get_balance')
            parameters: Optional parameters for the operation

        Returns:
            Dict with operation results, or None if execution failed
        """
        try:
            exec_params = {"integration_id": integration_id, "operation": operation_key}
            if parameters:
                exec_params.update(parameters)

            result = await self.executor.execute_step(
                domain="integrations",
                action="execute_operation",
                parameters=exec_params,
                description=f"Execute {operation_key} on integration {integration_id}"
            )

            if result.is_success and result.data:
                return result.data
            else:
                logger.warning(f"  [context] Failed to execute {operation_key} on integration {integration_id}: {result.error}")
                return {"error": result.error or "Unknown error"}

        except Exception as e:
            logger.error(f"  [context] Error executing {operation_key} on integration {integration_id}: {e}")
            return {"error": str(e)}


def validate_and_correct_parameters(
    parameters: Dict[str, Any],
    context: SystemContext,
    capability_id: str
) -> Dict[str, Any]:
    """
    Validate and correct parameters against known system values.

    Args:
        parameters: The parameters extracted by the LLM
        context: The system context with valid values
        capability_id: The capability being executed (e.g., "agents.create")

    Returns:
        Corrected parameters
    """
    corrected = dict(parameters)

    # Correct tool names
    if capability_id in ["agents.create", "agents.assign_tools"]:
        # Handle core_tool_names
        if "core_tool_names" in corrected:
            corrected_tools = []
            for tool_name in corrected["core_tool_names"]:
                resolved = context.resolve_tool_name(tool_name)
                if resolved:
                    corrected_tools.append(resolved)
                    if resolved != tool_name:
                        logger.info(f"  [validate] Corrected tool name: '{tool_name}' → '{resolved}'")
                else:
                    logger.warning(f"  [validate] Unknown tool '{tool_name}' - keeping as-is")
                    corrected_tools.append(tool_name)
            corrected["core_tool_names"] = corrected_tools

        # Handle tool_names (custom tools)
        if "tool_names" in corrected:
            corrected_tools = []
            for tool_name in corrected["tool_names"]:
                resolved = context.resolve_tool_name(tool_name)
                if resolved:
                    corrected_tools.append(resolved)
                else:
                    corrected_tools.append(tool_name)
            corrected["tool_names"] = corrected_tools

        # ── Move misplaced tools between categories ──
        # The LLM doesn't know which tools are core vs custom, so it may put
        # core tools in tool_names or vice versa.  Fix that here.
        core_tool_system_names = {t["name"] for t in context.core_tools}
        custom_tool_system_names = {t["name"] for t in context.custom_tools}

        # Ensure both keys exist for the re-categorization pass
        if "core_tool_names" not in corrected:
            corrected["core_tool_names"] = []
        if "tool_names" not in corrected:
            corrected["tool_names"] = []

        # Move core tools that ended up in tool_names → core_tool_names
        still_custom = []
        for name in corrected["tool_names"]:
            if name in core_tool_system_names:
                logger.info(f"  [validate] Moving '{name}' from tool_names → core_tool_names (it's a core tool)")
                if name not in corrected["core_tool_names"]:
                    corrected["core_tool_names"].append(name)
            else:
                still_custom.append(name)
        corrected["tool_names"] = still_custom

        # Move custom tools that ended up in core_tool_names → tool_names
        still_core = []
        for name in corrected["core_tool_names"]:
            if name in custom_tool_system_names:
                logger.info(f"  [validate] Moving '{name}' from core_tool_names → tool_names (it's a custom tool)")
                if name not in corrected["tool_names"]:
                    corrected["tool_names"].append(name)
            else:
                still_core.append(name)
        corrected["core_tool_names"] = still_core

        # Remove empty lists to keep payloads clean
        if not corrected["tool_names"]:
            del corrected["tool_names"]
        if not corrected["core_tool_names"]:
            del corrected["core_tool_names"]

    # Correct connection references
    if "connection_id" in corrected and context.connections:
        conn_val = corrected["connection_id"]
        if isinstance(conn_val, str) and not conn_val.isdigit():
            resolved_id = context.resolve_connection_name(conn_val)
            if resolved_id is not None:
                logger.info(f"  [validate] Resolved connection '{conn_val}' → ID {resolved_id}")
                corrected["connection_id"] = resolved_id

    # Correct workflow references
    if "workflow_id" in corrected and context.workflows:
        wf_val = corrected["workflow_id"]
        if isinstance(wf_val, str) and not wf_val.isdigit():
            resolved_id = context.resolve_workflow_name(wf_val)
            if resolved_id is not None:
                logger.info(f"  [validate] Resolved workflow '{wf_val}' → ID {resolved_id}")
                corrected["workflow_id"] = resolved_id

    # Correct agent references
    if "agent_id" in corrected:
        agent_val = corrected["agent_id"]
        logger.info(f"  [validate] agent_id='{agent_val}' (type={type(agent_val).__name__}), "
                     f"agents available: {len(context.agents)}")
        if context.agents and isinstance(agent_val, str) and not agent_val.isdigit():
            resolved_id = context.resolve_agent_name(agent_val)
            if resolved_id is not None:
                logger.info(f"  [validate] Resolved agent '{agent_val}' -> ID {resolved_id}")
                corrected["agent_id"] = resolved_id
            else:
                logger.warning(f"  [validate] Could not resolve agent name '{agent_val}' — "
                               f"known agents: {list(context._agent_name_map.keys())[:5]}")

    # ── Auto-resolve job_id (workflow_id) for schedule operations ──
    # The schedules.update and schedules.delete routes expect job_id to be
    # the WORKFLOW ID (TargetId), not the ScheduledJobId. The LLM often
    # omits job_id or confuses the IDs. If schedule_id is present, look up
    # the correct workflow_id from the system context.
    if capability_id in ("schedules.update", "schedules.delete"):
        schedule_id = corrected.get("schedule_id")
        job_id = corrected.get("job_id")
        if schedule_id and hasattr(context, "schedules") and context.schedules:
            for sched in context.schedules:
                sid = sched.get("id")
                wf_id = sched.get("workflow_id")
                if sid is not None and str(sid) == str(schedule_id) and wf_id is not None:
                    if job_id is None or str(job_id) != str(wf_id):
                        # job_id was missing or wrong — set to workflow_id
                        logger.info(f"  [validate] Resolved job_id for schedule {schedule_id}: "
                                    f"workflow_id={wf_id} (was: {job_id})")
                        corrected["job_id"] = int(wf_id)
                    break

    return corrected
