"""
Builder Agent — Graph Nodes
==============================
Every node logs exactly what it does so you can trace the full pipeline.

STATUS:
  ✅ classify_intent  — Works. Routes messages correctly.
  ✅ converse          — Works. General chat with streaming.
  ✅ analyze_and_plan  — Works. Generates plan + extracts structured steps.
  ✅ execute           — NOW REAL. Uses ActionExecutor to call AI Hub API.
  ✅ handle_rejection  — Works. Clears plan, asks for changes.
"""

import json
import logging
import time
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from builder_config import get_llm, safe_llm_invoke, BUILDER_SYSTEM_PROMPT, INTENT_CLASSIFICATION_PROMPT, STRUCTURED_RESPONSE_FORMAT, AI_HUB_BASE_URL, AI_HUB_API_KEY
from platform_knowledge import get_planning_knowledge, get_capability_list
from context_gatherer import ContextGatherer, SystemContext, validate_and_correct_parameters

logger = logging.getLogger(__name__)


# ─── Domain Detection ────────────────────────────────────────────────────

# Keyword fragments → domains they indicate. Matched case-insensitively
# against the user's message to determine which system resources to fetch.
DOMAIN_KEYWORDS = {
    "workflows": ["workflow", "automat", "trigger", "node", "approval"],
    "schedules": ["schedule", "cron", "recurring", "daily", "weekly", "monthly", "every day", "every week"],
    "connections": ["connection", "database", "data agent", "sql", "query"],
    "integrations": ["integrat", "slack", "teams", "salesforce", "jira", "external service", "cloud storage", "azure blob", "blob storage", "s3 bucket", "stripe", "shopify", "hubspot", "github", "twilio", "sendgrid", "payment", "api key"],
    "mcp": ["mcp", "model context protocol"],
    "knowledge": ["knowledge", "rag", "attach document", "knowledge base"],
    "documents": ["document", "upload", "pdf", "docx", "ingest", "vector"],
    "email": ["email", "provision", "inbox", "mail"],
    "environments": ["environment", "package", "pip install", "venv"],
    "users": ["user", "group", "permission", "role", "account"],
    "jobs": ["job", "scheduled task", "quickjob"],
}


def detect_domains_from_message(message: str) -> list:
    """
    Detect which domains are relevant based on keywords in the user's message.
    Always includes 'tools' and 'agents' as baseline context.
    """
    domains = {"tools", "agents"}  # Always fetch these
    message_lower = message.lower()

    for domain, keywords in DOMAIN_KEYWORDS.items():
        for keyword in keywords:
            if keyword in message_lower:
                domains.add(domain)
                break

    return list(domains)


def _format_resource_registry(state: dict) -> str:
    """
    Format the created_resources registry as a context block for the LLM.
    Returns empty string if no resources have been tracked.
    """
    try:
        resources = state.get("created_resources")
        if not resources:
            return ""

        lines = ["\n\n## PREVIOUSLY CREATED RESOURCES (this conversation)",
                 "Use these IDs when modifying or referencing resources created earlier:\n"]
        for category, items in resources.items():
            if not items:
                continue
            lines.append(f"**{category.title()}:**")
            for item in items:
                parts = []
                for k, v in item.items():
                    parts.append(f"{k}={v}")
                lines.append(f"  - {', '.join(parts)}")
        lines.append("")
        return "\n".join(lines)
    except Exception:
        return ""


# ─── Response Format Guard ─────────────────────────────────────────────────

def _sanitize_llm_response(response) -> None:
    """
    Guard against raw JSON leaking to the user.

    The STRUCTURED_RESPONSE_FORMAT instructs the LLM to return a JSON array
    of content blocks like [{"type": "text", "content": "..."}].  Sometimes
    the LLM returns a single JSON object (e.g. an API-call-like blob) or
    other malformed JSON instead.  This function detects those cases and
    wraps the content in a proper text block so the frontend never shows
    raw JSON to the user.
    """
    if not hasattr(response, "content") or not response.content:
        return

    content = response.content.strip()

    # Quick check: valid structured response starts with '['
    if content.startswith("["):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list) and all(
                isinstance(b, dict) and "type" in b for b in parsed
            ):
                return  # Already valid content blocks
        except (json.JSONDecodeError, TypeError):
            pass

    # Check if it's a raw JSON object (not a content block array)
    if content.startswith("{"):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict) and "type" not in parsed:
                # Raw JSON object leaked — wrap it
                logger.warning(f"  [response_guard] Raw JSON object detected, wrapping in text block: {content[:120]}...")
                escaped = content.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
                response.content = f'[{{"type": "text", "content": "I encountered an issue processing that request. Let me try again — could you tell me more about what you need?"}}]'
                return
        except (json.JSONDecodeError, TypeError):
            pass

    # If content is plain text (not JSON at all), wrap in a text block
    if not content.startswith("["):
        logger.info(f"  [response_guard] Plain text response detected, wrapping in content block")
        escaped = content.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        response.content = f'[{{"type": "text", "content": "{escaped}"}}]'


# ─── Intent Classification ────────────────────────────────────────────────

async def classify_intent(state: dict) -> dict:
    """
    Classify the user's latest message to determine routing.
    Uses the mini model for speed and cost efficiency.
    """
    messages = state.get("messages", [])
    if not messages:
        logger.info("  [classify_intent] No messages, defaulting to 'chat'")
        return {"intent": "chat"}

    latest = messages[-1]
    has_plan = state.get("current_plan") is not None
    plan_status = ""
    if has_plan:
        plan_status = state["current_plan"].get("status", "")

    logger.info(f"  [classify_intent] Input: '{latest.content[:60]}...' | has_plan={has_plan} plan_status={plan_status}")

    prompt = INTENT_CLASSIFICATION_PROMPT.format(
        has_pending_confirmation=has_plan and plan_status == "draft",
        has_active_plan=has_plan,
        is_executing=has_plan and plan_status == "executing",
    )

    try:
        t0 = time.time()
        llm = get_llm(mini=True, streaming=False)  # Internal classification, don't stream

        classify_messages = [
            SystemMessage(content=prompt),
            HumanMessage(content=f"User message: {latest.content}"),
        ]

        response = await safe_llm_invoke(llm, classify_messages)
        intent = response.content.strip().lower().strip('"').strip("'")
        elapsed = time.time() - t0

        valid_intents = {"build", "chat", "query", "confirm_yes", "confirm_no", "provide_context"}
        if intent not in valid_intents:
            logger.warning(f"  [classify_intent] Unknown intent '{intent}', defaulting to 'chat' ({elapsed:.1f}s)")
            intent = "chat"
        else:
            logger.info(f"  [classify_intent] Result: '{intent}' ({elapsed:.1f}s)")

        return {"intent": intent}

    except Exception as e:
        logger.error(f"  [classify_intent] FAILED: {e}")
        return {"intent": "chat"}


# ─── General Conversation ─────────────────────────────────────────────────

async def converse(state: dict) -> dict:
    """Handle general conversation — questions, information, guidance."""
    messages = state.get("messages", [])
    logger.info(f"  [converse] Generating response with {len(messages)} messages in context")

    # Inject permission context so LLM knows what the user can/cannot do
    from permissions import get_permission_context_for_prompt
    user_context = state.get("user_context")
    permission_context = get_permission_context_for_prompt(user_context)
    resource_context = _format_resource_registry(state)
    system_prompt = BUILDER_SYSTEM_PROMPT + "\n\n" + permission_context + resource_context + "\n\n" + STRUCTURED_RESPONSE_FORMAT

    llm = get_llm(mini=False)
    system = SystemMessage(content=system_prompt)
    full_messages = [system] + messages

    t0 = time.time()
    response = await safe_llm_invoke(llm, full_messages)
    elapsed = time.time() - t0
    logger.info(f"  [converse] Done ({elapsed:.1f}s, {len(response.content)} chars)")

    _sanitize_llm_response(response)

    return {"messages": [response]}


# ─── Query and Respond ───────────────────────────────────────────────────

QUERY_SYSTEM_PROMPT_ADDITION = """
The user is asking about existing resources on the platform.
Use the SYSTEM RESOURCES section below to answer accurately with real data.
Present the information clearly — use the names, IDs, and details provided.
If no resources exist in a category, say so honestly.
Do NOT offer to build or create anything unless the user asks for it.
"""


async def query_and_respond(state: dict) -> dict:
    """
    Handle read/list/query requests by fetching real system data first.

    Unlike converse() which is LLM-only, this node:
    1. Detects which domains are relevant from the user's message
    2. Fetches actual system data via ContextGatherer
    3. Includes that data in the system prompt
    4. Lets the LLM answer with real information
    """
    messages = state.get("messages", [])
    logger.info(f"  [query_and_respond] Starting with {len(messages)} messages in context")

    # Extract last user message for domain detection
    last_user_msg = ""
    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "human":
            last_user_msg = msg.content
            break
        elif isinstance(msg, dict) and msg.get("role") == "user":
            last_user_msg = msg.get("content", "")
            break

    # Detect which domains are relevant
    domains_needed = detect_domains_from_message(last_user_msg)
    logger.info(f"  [query_and_respond] Domains detected: {domains_needed}")

    # Fetch dynamic context from the system
    dynamic_context_str = ""
    try:
        from execution import ActionExecutor
        async with ActionExecutor(base_url=AI_HUB_BASE_URL, api_key=AI_HUB_API_KEY) as executor:
            gatherer = ContextGatherer(executor)
            system_context = await gatherer.gather_context(domains_needed=domains_needed)
            dynamic_context_str = system_context.get_full_context_for_prompt()
            logger.info(f"  [query_and_respond] Context gathered: {len(system_context.agents)} agents, {len(system_context.workflows)} workflows")

            # ─── Check if user is asking about a SPECIFIC resource ───
            # If so, fetch detailed information for that resource
            detail_sections = []

            # Check for specific agent mentions
            if "agents" in domains_needed and system_context.agents:
                for agent in system_context.agents:
                    agent_name = agent.get("description", "").lower()
                    agent_id = agent.get("id")
                    # Check if the user message mentions this agent by name or ID
                    id_match = agent_id and str(agent_id) in last_user_msg
                    name_match = agent_name and agent_name in last_user_msg.lower()
                    if agent_id and (name_match or id_match):
                        logger.info(f"  [query_and_respond] Detected query for specific agent '{agent_name}' (ID {agent_id})")
                        agent_detail = await gatherer.fetch_agent_detail(agent_id)
                        if agent_detail:
                            import json
                            detail_sections.append(f"\n\n{'=' * 60}\nDETAILED INFO FOR AGENT '{agent.get('description')}' (ID {agent_id}):\n{'=' * 60}\n{json.dumps(agent_detail, indent=2)}")
                            logger.info(f"  [query_and_respond] Fetched detailed info for agent {agent_id}")

                        # If the message mentions 'email', also fetch email config
                        if "email" in last_user_msg.lower():
                            logger.info(f"  [query_and_respond] Message mentions email, fetching email config for agent {agent_id}")
                            email_config = await gatherer.fetch_agent_email_config(agent_id)
                            if email_config:
                                import json
                                detail_sections.append(f"\n\n{'=' * 60}\nEMAIL CONFIG FOR AGENT '{agent.get('description')}' (ID {agent_id}):\n{'=' * 60}\n{json.dumps(email_config, indent=2)}")
                                logger.info(f"  [query_and_respond] Fetched email config for agent {agent_id}")
                            else:
                                detail_sections.append(f"\n\n{'=' * 60}\nEMAIL CONFIG FOR AGENT '{agent.get('description')}' (ID {agent_id}):\n{'=' * 60}\nEmail not configured for this agent.")
                                logger.info(f"  [query_and_respond] No email config found for agent {agent_id}")

                        break  # Only fetch details for first match

            # Check for specific connection mentions
            if "connections" in domains_needed and system_context.connections:
                for conn in system_context.connections:
                    conn_name = conn.get("name", "").lower()
                    conn_id = conn.get("id")
                    # Check if the user message mentions this connection by name or ID
                    id_match = conn_id and str(conn_id) in last_user_msg
                    name_match = conn_name and conn_name in last_user_msg.lower()
                    if conn_id and (name_match or id_match):
                        logger.info(f"  [query_and_respond] Detected query for specific connection '{conn_name}' (ID {conn_id})")
                        conn_detail = await gatherer.fetch_connection_detail(conn_id)
                        if conn_detail:
                            import json
                            detail_sections.append(f"\n\n{'=' * 60}\nDETAILED INFO FOR CONNECTION '{conn.get('name')}' (ID {conn_id}):\n{'=' * 60}\n{json.dumps(conn_detail, indent=2)}")
                            logger.info(f"  [query_and_respond] Fetched detailed info for connection {conn_id}")
                        break  # Only fetch details for first match

            # Check for specific workflow mentions
            if "workflows" in domains_needed and system_context.workflows:
                for wf in system_context.workflows:
                    wf_name = wf.get("name", "").lower()
                    wf_id = wf.get("id")
                    # Check if the user message mentions this workflow by name or ID
                    id_match = wf_id and str(wf_id) in last_user_msg
                    name_match = wf_name and wf_name in last_user_msg.lower()
                    if wf_id and (name_match or id_match):
                        logger.info(f"  [query_and_respond] Detected query for specific workflow '{wf_name}' (ID {wf_id})")
                        wf_detail = await gatherer.fetch_workflow_detail(wf_id)
                        if wf_detail:
                            import json
                            detail_sections.append(f"\n\n{'=' * 60}\nDETAILED INFO FOR WORKFLOW '{wf.get('name')}' (ID {wf_id}):\n{'=' * 60}\n{json.dumps(wf_detail, indent=2)}")
                            logger.info(f"  [query_and_respond] Fetched detailed info for workflow {wf_id}")

                        # Check if user is asking about execution history/runs/monitor
                        execution_keywords = ["execution", "history", "runs", "run", "results", "monitor", "executed", "recent runs"]
                        if any(kw in last_user_msg.lower() for kw in execution_keywords):
                            logger.info(f"  [query_and_respond] Detected execution history query for workflow {wf_id}")
                            try:
                                exec_result = await executor.execute_step(
                                    domain="workflows",
                                    action="monitor",
                                    parameters={"workflow_id": wf_id, "limit": 20},
                                    description=f"Fetch execution history for workflow {wf_id}",
                                )
                                if exec_result.is_success and exec_result.data:
                                    executions = exec_result.data.get("executions", [])
                                    import json
                                    detail_sections.append(
                                        f"\n\n{'=' * 60}\n"
                                        f"EXECUTION HISTORY FOR WORKFLOW '{wf.get('name')}' (ID {wf_id}):\n"
                                        f"{'=' * 60}\n"
                                        f"Total executions found: {len(executions)}\n"
                                        f"{json.dumps(executions, indent=2) if executions else 'No executions recorded yet.'}"
                                    )
                                    logger.info(f"  [query_and_respond] Fetched {len(executions)} executions for workflow {wf_id}")
                                else:
                                    detail_sections.append(
                                        f"\n\n{'=' * 60}\n"
                                        f"EXECUTION HISTORY FOR WORKFLOW '{wf.get('name')}' (ID {wf_id}):\n"
                                        f"{'=' * 60}\n"
                                        f"Could not retrieve execution history: {exec_result.error or 'Unknown error'}"
                                    )
                            except Exception as exec_err:
                                logger.warning(f"  [query_and_respond] Failed to fetch executions for workflow {wf_id}: {exec_err}")

                        break  # Only fetch details for first match

            # Check for specific integration mentions
            if "integrations" in domains_needed and system_context.integrations:
                for integ in system_context.integrations:
                    integ_name = integ.get("integration_name", "").lower()
                    integ_id = integ.get("integration_id")
                    template_key = integ.get("template_key", "").lower()

                    # Check if the user message mentions this integration by name, template_key, or ID
                    id_match = integ_id and str(integ_id) in last_user_msg
                    name_match = integ_name and integ_name in last_user_msg.lower()
                    template_match = template_key and template_key in last_user_msg.lower()

                    if integ_id and (name_match or template_match or id_match):
                        logger.info(f"  [query_and_respond] Detected query for specific integration '{integ_name}' (ID {integ_id})")

                        # Fetch operations list for this integration
                        operations_data = await gatherer.fetch_integration_operations(integ_id)
                        if operations_data:
                            import json
                            detail_sections.append(
                                f"\n\n{'=' * 60}\n"
                                f"OPERATIONS FOR INTEGRATION '{integ.get('integration_name')}' (ID {integ_id}):\n"
                                f"{'=' * 60}\n"
                                f"{json.dumps(operations_data, indent=2)}"
                            )
                            logger.info(f"  [query_and_respond] Fetched operations for integration {integ_id}")

                        # Check if user wants to execute a specific operation
                        # Common operation keywords and their mappings
                        operation_keywords = {
                            "customers": "get_customers",
                            "customer": "get_customers",
                            "balance": "get_balance",
                            "charges": "get_charges",
                            "charge": "get_charges",
                            "payments": "get_payments",
                            "payment": "get_payments",
                            "invoices": "get_invoices",
                            "invoice": "get_invoices",
                            "subscriptions": "get_subscriptions",
                            "subscription": "get_subscriptions",
                        }

                        # Action verbs that indicate operation execution
                        action_verbs = ["pull", "fetch", "get", "show", "list", "retrieve", "load", "see", "view", "want", "give", "display"]
                        wants_operation = any(verb in last_user_msg.lower() for verb in action_verbs)

                        if wants_operation and operations_data:
                            # Try to detect which operation the user wants
                            detected_operation = None
                            for keyword, op_key in operation_keywords.items():
                                if keyword in last_user_msg.lower():
                                    # Verify this operation exists for this integration
                                    ops_list = operations_data.get("operations", [])
                                    if any(op.get("key") == op_key for op in ops_list):
                                        detected_operation = op_key
                                        logger.info(f"  [query_and_respond] Detected operation request: {op_key}")
                                        break

                            # Execute the operation if detected
                            if detected_operation:
                                logger.info(f"  [query_and_respond] Executing {detected_operation} on integration {integ_id}")
                                operation_result = await gatherer.execute_integration_operation(
                                    integration_id=integ_id,
                                    operation_key=detected_operation
                                )
                                if operation_result:
                                    import json
                                    detail_sections.append(
                                        f"\n\n{'=' * 60}\n"
                                        f"OPERATION RESULT: {detected_operation.upper()} on '{integ.get('integration_name')}'\n"
                                        f"{'=' * 60}\n"
                                        f"{json.dumps(operation_result, indent=2)}"
                                    )
                                    logger.info(f"  [query_and_respond] Executed {detected_operation} on integration {integ_id}")

                        break  # Only fetch details for first match

            # ─── Document search handling ───
            # If user is searching for documents by content/topic (not just listing),
            # execute the search and include results in context
            if "documents" in domains_needed:
                search_keywords = ["search", "find", "look for", "related to", "about", "containing", "matching", "with content"]
                is_search_query = any(kw in last_user_msg.lower() for kw in search_keywords)
                is_just_listing = any(kw in last_user_msg.lower() for kw in ["list all", "show all", "list documents", "show documents"])
                
                if is_search_query and not is_just_listing:
                    logger.info(f"  [query_and_respond] Detected document search query")
                    try:
                        # Extract search terms from the user message
                        # Strip common conversational prefixes to get clean search terms
                        import re
                        search_query = last_user_msg
                        # Remove common prefixes
                        strip_patterns = [
                            r'^(?:search|find|look for|show me|get|retrieve)\s+(?:the\s+)?(?:documents?\s+)?(?:for\s+)?(?:anything\s+)?(?:related\s+to\s+)?(?:about\s+)?(?:containing\s+)?(?:matching\s+)?',
                            r'^(?:what|which)\s+(?:documents?\s+)?(?:do we have\s+)?(?:related\s+to\s+)?(?:about\s+)?',
                        ]
                        cleaned = last_user_msg
                        for pattern in strip_patterns:
                            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE).strip()
                        # Extract quoted terms if present (e.g., "lease" or 'inventory')
                        quoted = re.findall(r'["\']([^"\']+)["\']', last_user_msg)
                        if quoted:
                            search_query = ' '.join(quoted)
                        elif cleaned and len(cleaned) < len(last_user_msg):
                            search_query = cleaned
                        # Remove trailing punctuation / filler
                        search_query = re.sub(r'\s*[-—]\s*show me what.*$', '', search_query, flags=re.IGNORECASE).strip()
                        search_query = search_query.strip('"\'.,!? ')
                        
                        if not search_query:
                            search_query = last_user_msg  # fallback to full message
                        
                        logger.info(f"  [query_and_respond] Document search query extracted: '{search_query}'")
                        
                        # Execute search via the builder document search endpoint
                        search_result = await executor.execute_step(
                            domain="documents",
                            action="search",
                            parameters={"query": search_query, "max_results": 20},
                            description=f"Search documents for: {search_query}",
                        )
                        if search_result.is_success and search_result.data:
                            results = search_result.data.get("results", [])
                            total = search_result.data.get("total_results", len(results))
                            search_type = search_result.data.get("search_type", "unknown")
                            import json
                            detail_sections.append(
                                f"\n\n{'=' * 60}\n"
                                f"DOCUMENT SEARCH RESULTS (query: '{search_query}'):\n"
                                f"{'=' * 60}\n"
                                f"Search type: {search_type}\n"
                                f"Total results: {total}\n"
                                f"{json.dumps(results, indent=2) if results else 'No matching documents found.'}"
                            )
                            logger.info(f"  [query_and_respond] Document search returned {total} results")
                        else:
                            error_msg = search_result.error if search_result else "Unknown error"
                            detail_sections.append(
                                f"\n\nDOCUMENT SEARCH RESULTS:\nSearch failed: {error_msg}"
                            )
                            logger.warning(f"  [query_and_respond] Document search failed: {error_msg}")
                    except Exception as search_err:
                        logger.warning(f"  [query_and_respond] Document search error: {search_err}")

            # Append detail sections to dynamic context
            if detail_sections:
                dynamic_context_str += "".join(detail_sections)

    except Exception as e:
        logger.warning(f"  [query_and_respond] Failed to gather context: {e}")
        dynamic_context_str = "(System context unavailable — could not reach platform APIs)"

    # Build system prompt with real data
    from permissions import get_permission_context_for_prompt
    user_context = state.get("user_context")
    permission_context = get_permission_context_for_prompt(user_context)
    resource_context = _format_resource_registry(state)
    system_prompt = (BUILDER_SYSTEM_PROMPT + QUERY_SYSTEM_PROMPT_ADDITION + "\n\n"
                     + permission_context + "\n\n" + dynamic_context_str + resource_context + "\n\n"
                     + "IMPORTANT: Respond using clean Markdown formatting only. Use Markdown tables, headers, bullet lists, and bold text to present data clearly. Do NOT output JSON content blocks or structured JSON arrays. Present all data in human-readable Markdown.")

    llm = get_llm(mini=False)
    system = SystemMessage(content=system_prompt)
    full_messages = [system] + messages

    t0 = time.time()
    response = await safe_llm_invoke(llm, full_messages)
    elapsed = time.time() - t0
    logger.info(f"  [query_and_respond] Done ({elapsed:.1f}s, {len(response.content)} chars)")

    _sanitize_llm_response(response)

    return {"messages": [response]}


# ─── Analyze and Plan ─────────────────────────────────────────────────────

PLAN_SYSTEM_PROMPT = """You are the AI Hub Builder Agent — an expert solutions architect.

The user wants to build something on the AI Hub platform. Follow this process:

1. Acknowledge what you understand they want (1-2 sentences max)
2. ANALYZE the full solution: what agents, tools, connections, workflows, and schedules are needed?
3. CHECK the system resources below: what already exists vs. what needs to be created?
4. IDENTIFY what information you need from the user (credentials, server addresses, table names, business rules, etc.)
5. Decide your response mode based on whether you have everything needed:

═══════════════════════════════════════════════════════════════════
CRITICAL — TWO RESPONSE MODES (you MUST pick ONE):
═══════════════════════════════════════════════════════════════════

**MODE A: INFORMATION GATHERING** (use when you're missing required information)
If you need information from the user to execute ANY step — such as database credentials,
server addresses, table/column names, API keys, specific business rules, or configuration
values — you MUST:
- Describe what you'll build (the solution overview) so the user understands the approach
- List the SPECIFIC information you need, as numbered questions
- Do NOT present numbered plan steps or action items
- Do NOT say "Step 1:", "1.", or use any numbered plan format
- End with something like "Once you provide this information, I'll create the full plan"
- The user will respond with the info, and THEN you'll produce the executable plan

Examples of missing information that REQUIRE Mode A:
- Database connection details (server, database name, credentials, auth type)
- API endpoints or API keys for external services
- Specific table names, column names, or data structures
- Business rules that haven't been specified (thresholds, conditions, recipients)
- Integration credentials or configuration values

**MODE B: EXECUTABLE PLAN** (use ONLY when you have ALL required information)
If you have everything needed to execute every step right now:
- Present a clear, numbered plan of EXACTLY what you'll do
- Each step must be executable with concrete parameter values — no placeholders
- For each step, indicate whether it's a direct action or agent delegation
- End with "Shall I go ahead with this plan?" or similar confirmation request

═══════════════════════════════════════════════════════════════════
HOW TO DECIDE: Before writing any numbered step, ask yourself:
"Do I have the ACTUAL values needed to execute this step?"
If the answer is NO for ANY step, use Mode A.
Mode A is ONLY for genuinely missing information — do NOT use it merely to
confirm or summarize before executing. If the user provided all values, use Mode B.
═══════════════════════════════════════════════════════════════════

""" + get_planning_knowledge() + """

EXECUTION OPTIONS:
You can accomplish tasks through TWO types of execution:

1. **Direct API Actions** (domain.action format):
   - Use these for straightforward platform operations
   - Examples: agents.create, workflows.create, knowledge.create

2. **Agent Delegation** (agent:agent_id format):
   - Use these when a specialized agent can better handle the task
   - Agents have expertise in specific domains (workflows, data, reports)
   - Prefer delegation when the task requires multi-step design or complex logic
   - Examples: agent:workflow_agent, agent:data_agent, agent:report_agent

CHOOSING THE BEST PATH:
- For simple CRUD operations → use direct API actions
- For complex design tasks (workflow creation, report building) → delegate to specialized agents
- For editing/modifying existing workflows → delegate to agent:workflow_agent (it handles both create and edit)
- Agents can have multi-turn conversations to gather requirements
- Mix both approaches when appropriate

**CRITICAL — DATA DICTIONARY POPULATION:**
When creating a data agent with a data dictionary, you MUST use direct API actions:
- connections.discover_tables (direct API action — returns table list)
- connections.analyze_tables (direct API action — populates data dictionary)
NEVER delegate table discovery or analysis to agent:data_agent or any other agent.
The data agent cannot populate its own data dictionary (circular dependency).
This is a platform operation that MUST be executed via direct connections API calls.

EDITING EXISTING WORKFLOWS:
When the user wants to edit, modify, update, or change an existing workflow:
- ALWAYS delegate to agent:workflow_agent — the workflow agent can handle both creating new workflows AND editing existing ones
- Include the workflow name in the step description so the agent can find it (e.g., "Edit the 'Invoice Processor' workflow to add a validation step")
- Use words like "edit", "modify", "update", or "change" in the description so the system recognizes edit intent
- The system will automatically discover the existing workflow, load its current state, and pass it to the workflow agent
- If the user's request is ambiguous (could be edit or create), ask for clarification
- **IMPORTANT:** Do NOT use `workflows.update` for modifying workflow nodes (changing SQL queries, alert text, adding/removing nodes, etc.)
  - `workflows.update` requires the COMPLETE workflow JSON definition and is only for raw overwrites
  - For ANY node-level change (SQL, conditions, alerts, variables, etc.), delegate to `agent:workflow_agent`
  - The workflow agent understands node structure and can make surgical edits to individual nodes

DESTRUCTIVE / DANGEROUS OPERATIONS:
- Delete, remove, or destroy operations are DANGEROUS and irreversible.
- If the user asks to delete MULTIPLE resources (e.g., "delete all agents"), you MUST:
  1. Warn them explicitly that this is destructive and irreversible
  2. List the specific resources that will be deleted (names and IDs)
  3. Ask for explicit confirmation BEFORE presenting the plan
- NEVER batch-delete all resources of a type in a single plan — limit destructive plans to at most 3 delete steps.
- For mass-delete requests, suggest a safer alternative (e.g., "disable" instead of "delete") or ask the user to confirm each resource individually.
- Mark each delete step clearly with "⚠️ DELETE:" prefix in the description.

SCOPE DISCIPLINE:
- ONLY include steps that the user explicitly requested OR that are strictly necessary prerequisites for what they requested.
- If the user asks to "connect to a database and query sales data", create the connection and data agent — do NOT add workflows, schedules, monitoring, email alerts, or other extras unless specifically asked.
- When in doubt about whether to add an extra step, leave it out. The user can always ask for more later.
- Prerequisites are OK (e.g., creating a connection before creating a data agent that uses it). Extras are NOT (e.g., adding a daily report workflow when the user only asked for a data agent).

PARTIAL PLANS:
- If you can fulfill PART of a request but not all of it, present a plan for the feasible parts.
- Clearly explain which parts you cannot do and why.
- Still present the plan card for the parts that ARE feasible so the user can approve those.

EMAIL CAPABILITY:
- To give an agent a dedicated email address (send AND receive), use email.provision.
- To just assign simple sending capability, use agents.assign_tools with "send_email_message".
- email.provision creates a full email identity. email.configure adjusts settings like auto-response, inbox tools, workflow triggers.
- Do NOT confuse email.provision with agents.assign_tools — they serve different purposes.

SCHEDULING WORKFLOWS:
- When a user requests scheduling (daily, weekly, monthly, cron, recurring, "every X"), ALWAYS create a SEPARATE schedules.create step.
- Do NOT embed scheduling logic inside a workflow agent delegation step.
- The schedules.create step must come AFTER the workflow creation step and reference the workflow from the previous step.
- Include the cron expression (see CRON EXPRESSION REFERENCE in capabilities).
- Example: "run daily at 8am" → schedules.create with cron_expression="0 8 * * *"

FORMAT YOUR RESPONSE:
- If using Mode A (info gathering): describe the solution approach, then ask numbered questions. NO plan steps.
- If using Mode B (executable plan): present numbered steps with concrete values. Be concise.
- Use exact system values when available (tool names, connection names, etc.)
- Minimize steps — combine operations when the API supports it
- Present reasonable defaults but note the user can change them.

CRITICAL FORMAT RULE FOR MODE B PLANS:
For each numbered step, you MUST include the capability ID in parentheses at the end.
Format: "N. Description (capability_id)"
Examples:
 - "1. Create agent 'My Bot' with objective and enabled status (agents.create)"
 - "2. Create custom tool 'calculator' with inputs and code (custom_tools.create)"
 - "3. Assign tools calculate_discount and web_search to the agent (agents.assign_tools)"
 - "4. Test the agent by sending a chat message (agents.chat)"
 - "5. Create workflow with query and email nodes (agent:workflow_agent)"
This annotation is REQUIRED — the system uses it to parse your plan into executable steps."""


EXTRACT_STEPS_PROMPT = """Extract the plan steps from this AI response into a JSON array.

CRITICAL FIRST CHECK — Is this an information-gathering response?
If the AI response is ASKING the user for information (database credentials, server addresses,
table names, API keys, business rules, etc.) and does NOT present a concrete executable plan
with numbered action steps, return an EMPTY array: []

Signs of an information-gathering response (return []):
- The response asks questions like "What is your database server?", "Can you provide...?"
- The response says "Once you provide this info, I'll create the plan"
- The steps described are aspirational/future ("we will need to...", "I'll then...")
- Parameters would need placeholder values (no real credentials, addresses, or names provided)

Signs of an executable plan (extract steps):
- The response has concrete numbered steps like "1. Create agent named 'X'..."
- Steps have real parameter values, not placeholders or TBD
- The response ends with "Shall I go ahead?" or "Ready to proceed?"

If the response is information-gathering, respond with: []

EXTRACTION HINT: Look for capability IDs in parentheses after step descriptions,
e.g. "Create agent 'X' (agents.create)". If present, split on the first dot to get
domain="agents" and action="create". This is the most reliable extraction signal.

Otherwise, extract the steps:

There are TWO types of steps:

TYPE 1: Direct API Actions
Each step MUST use a valid capability ID from the list below.

""" + get_capability_list() + """

TYPE 2: Agent Delegations
For steps that delegate to a specialized agent, use:
- domain: "agent"
- action: the agent ID (e.g., "workflow_agent", "data_agent", "report_agent")

AVAILABLE AGENTS:
{available_agents}

OUTPUT FORMAT:
Each step should have:
- "description" (brief description of what it does)
- "domain" (the part BEFORE the first dot in the capability ID — e.g., "agents" from "agents.create", "workflows" from "workflows.list", OR "agent" for delegation)
- "action" (the part AFTER the first dot in the capability ID — e.g., "create" from "agents.create", "chat" from "agents.chat", OR agent_id for delegation)
- "parameters" (extracted values as key-value pairs, use empty object {{}} if none)

RULES:
1. For direct actions: Use ONLY capability IDs from the list above — do not invent new ones
2. For agent delegation: Use domain="agent" and action=<agent_id>
3. Extract parameter values mentioned in the plan (names, IDs, settings, etc.)
4. If multiple things are done in one step, keep them combined (don't split)
5. Respond with ONLY a valid JSON array, no other text
6. The "domain" must be a single word — NEVER include a dot. Split the capability ID at the first dot only. Example: "agents.chat" → domain="agents", action="chat"

AI Response to extract from:
{response_text}"""


def _is_auto_executable(steps: list) -> bool:
    """Check if a plan can be auto-executed without user confirmation.

    A plan auto-executes only if NONE of its steps require confirmation.
    Each action's `requires_confirmation` flag controls this behavior.
    Agent delegations always require approval (multi-turn conversations).
    """
    # Disabled: auto-execute creates confusing UX because the planning LLM
    # naturally asks "Shall I go ahead?" but execution starts without waiting
    # for the user's answer. Re-enable when the planning prompt is updated
    # to skip confirmation text for auto-executable plans.
    return False


def _normalize_capability(domain: str, action: str) -> tuple:
    """Fix common LLM extraction errors in domain/action splitting.

    The LLM sometimes produces "agents.agents.chat" instead of "agents.chat"
    by duplicating the domain in the action field or in the domain itself.
    """
    # Case 1: action includes domain prefix (e.g., domain="agents", action="agents.chat")
    if "." in action:
        parts = action.split(".", 1)
        if parts[0] == domain:
            logger.info(f"  [normalize] Fixed action: '{domain}.{action}' → '{domain}.{parts[1]}'")
            action = parts[1]
    # Case 2: domain is doubled (e.g., domain="agents.agents")
    if "." in domain:
        parts = domain.split(".")
        if len(parts) == 2 and parts[0] == parts[1]:
            logger.info(f"  [normalize] Fixed domain: '{domain}' → '{parts[0]}'")
            domain = parts[0]
    return domain, action


def _step_depends_on_failed(step, failed_steps, newly_created_connections, newly_created_agents, newly_created_workflows, newly_created_integrations):
    """
    Check if this step depends on any failed prior step.

    Returns True only if there's an actual dependency (e.g., the step needs
    a connection_id/agent_id/workflow_id/integration_id that would have come from a failed step).

    This allows independent chains to proceed even when one chain fails.
    For example: Python tool creation can proceed even if a connection test failed.
    """
    domain = step.get("domain", "")
    action = step.get("action", "")
    parameters = step.get("parameters", {})

    # Determine what resource types failed to be created
    has_failed_connections = any(
        s.get("domain") == "connections" and s.get("action") == "create"
        for s in failed_steps
    )
    has_failed_agents = any(
        s.get("domain") == "agents" and s.get("action") == "create"
        for s in failed_steps
    )
    has_failed_workflows = any(
        s.get("domain") == "workflows" and s.get("action") in ("create", "save")
        for s in failed_steps
    )
    has_failed_integrations = any(
        s.get("domain") == "integrations" and s.get("action") == "create"
        for s in failed_steps
    )

    # ═══════════════════════════════════════════════════════════════
    # Check if this step depends on CONNECTIONS
    # ═══════════════════════════════════════════════════════════════
    needs_connection = (
        "connection_id" in parameters
        or (domain == "connections" and action in ("test", "update", "delete"))
        or (domain == "agents" and action == "create" and parameters.get("connection_type"))
    )

    if needs_connection and has_failed_connections:
        # Check if this step can find a valid connection
        conn_id = parameters.get("connection_id")
        if not conn_id and not newly_created_connections:
            # No connection specified and none were created successfully
            return True
        if conn_id and isinstance(conn_id, str) and not conn_id.isdigit():
            # Connection referenced by name
            if conn_id not in newly_created_connections:
                # Can't find this connection in successful creations
                return True

    # ═══════════════════════════════════════════════════════════════
    # Check if this step depends on AGENTS
    # ═══════════════════════════════════════════════════════════════
    needs_agent = (
        "agent_id" in parameters
        or (domain == "agents" and action in ("assign_tools", "chat", "get", "update", "delete"))
        or (domain == "email" and action == "provision")
        or (domain == "knowledge" and action == "attach")
    )

    if needs_agent and has_failed_agents:
        agent_id = parameters.get("agent_id")
        if not agent_id and not newly_created_agents:
            return True
        if agent_id and isinstance(agent_id, str) and not agent_id.isdigit():
            if agent_id not in newly_created_agents:
                return True

    # ═══════════════════════════════════════════════════════════════
    # Check if this step depends on WORKFLOWS
    # ═══════════════════════════════════════════════════════════════
    needs_workflow = (
        "workflow_id" in parameters
        or (domain == "workflows" and action in ("execute", "update", "delete"))
        or (domain == "schedules" and action == "create")
    )

    if needs_workflow and has_failed_workflows:
        workflow_id = parameters.get("workflow_id")
        if not workflow_id and not newly_created_workflows:
            return True
        if workflow_id and isinstance(workflow_id, str) and not workflow_id.isdigit():
            if workflow_id not in newly_created_workflows:
                return True

    # ═══════════════════════════════════════════════════════════════
    # Check if this step depends on INTEGRATIONS
    # ═══════════════════════════════════════════════════════════════
    needs_integration = (
        "integration_id" in parameters
        or (domain == "integrations" and action in ("test", "update", "delete", "list_operations", "execute_operation"))
    )

    if needs_integration and has_failed_integrations:
        integration_id = parameters.get("integration_id")
        if not integration_id and not newly_created_integrations:
            return True
        if integration_id and isinstance(integration_id, str) and not integration_id.isdigit():
            if integration_id not in newly_created_integrations:
                return True

    # No blocking dependency found — step can proceed
    return False


async def analyze_and_plan(state: dict) -> dict:
    """
    Analyze the user's build request and produce a structured plan.

    Step 0: Fetch dynamic context (available tools, agents, etc.)
    Step 0.5: Fetch available specialized agents from registry
    Step 1: Main LLM call — streamed to user, generates the conversational plan
    Step 2: Mini LLM call — extracts structured step data from the response

    The planner can choose between:
    - Direct API actions (domain.action)
    - Agent delegation (agent:agent_id)
    """
    messages = state.get("messages", [])
    logger.info(f"  [analyze_and_plan] Starting with {len(messages)} messages in context")

    # Step 0: Fetch dynamic context from the system
    # Detect which domains are relevant based on the user's message
    system_context = None
    dynamic_context_str = ""
    last_user_msg = ""
    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "human":
            last_user_msg = msg.content
            break
        elif isinstance(msg, dict) and msg.get("role") == "user":
            last_user_msg = msg.get("content", "")
            break

    domains_needed = detect_domains_from_message(last_user_msg)
    try:
        from execution import ActionExecutor
        logger.info(f"  [analyze_and_plan] Fetching dynamic context for domains: {domains_needed}")
        async with ActionExecutor(base_url=AI_HUB_BASE_URL, api_key=AI_HUB_API_KEY) as executor:
            gatherer = ContextGatherer(executor)
            system_context = await gatherer.gather_context(domains_needed=domains_needed)
            dynamic_context_str = system_context.get_full_context_for_prompt()
            logger.info(f"  [analyze_and_plan] Context gathered: {len(system_context.core_tools)} tools, {len(system_context.agents)} agents")
    except Exception as e:
        logger.warning(f"  [analyze_and_plan] Failed to gather context: {e}")
        dynamic_context_str = "(System context unavailable - using static knowledge)"

    # Step 0.5: Fetch available specialized agents from registry
    available_agents_str = ""
    available_agents_list = []
    try:
        import sys
        from pathlib import Path
        BUILDER_AGENT_DIR = Path(__file__).parent.parent.parent / "builder_agent"
        if str(BUILDER_AGENT_DIR) not in sys.path:
            sys.path.insert(0, str(BUILDER_AGENT_DIR))

        from builder_agent.registry.agent_registry import get_enabled_agents

        available_agents_list = get_enabled_agents()
        if available_agents_list:
            agents_context = ["AVAILABLE SPECIALIZED AGENTS:"]
            for agent in available_agents_list:
                agents_context.append(f"  - {agent.id}: {agent.name}")
                agents_context.append(f"    Description: {agent.description}")
                agents_context.append(f"    Specializations: {', '.join(agent.specializations)}")
            available_agents_str = "\n".join(agents_context)
            logger.info(f"  [analyze_and_plan] Found {len(available_agents_list)} specialized agents")
    except Exception as e:
        logger.warning(f"  [analyze_and_plan] Failed to fetch agent registry: {e}")
        available_agents_str = "(No specialized agents available)"

    # Step 1: Generate the plan response with dynamic context + agents
    llm = get_llm(mini=False)

    # Inject permission context so the planner respects user role restrictions
    from permissions import get_permission_context_for_prompt
    user_context = state.get("user_context")
    permission_context = get_permission_context_for_prompt(user_context)

    # Inject learned patterns from resilience memory (if available)
    learning_hints = ""
    try:
        from pathlib import Path as _PlanPath
        from resilience import LearningMemory
        _plan_data_dir = _PlanPath(__file__).parent.parent / "data"
        _plan_memory = LearningMemory(_plan_data_dir)
        learning_hints = _plan_memory.get_planning_hints(domains_needed)
        if learning_hints:
            logger.info(f"  [analyze_and_plan] Injecting {len(learning_hints)} chars of learned patterns")
    except Exception as _le:
        logger.debug(f"  [analyze_and_plan] Learning memory not available: {_le}")

    # Inject dynamic context, permission context, available agents, learning, resource registry, AND structured format
    resource_context = _format_resource_registry(state)
    enhanced_prompt = (PLAN_SYSTEM_PROMPT + "\n\n" + permission_context + "\n\n"
                       + dynamic_context_str + "\n\n" + available_agents_str + "\n\n"
                       + learning_hints + resource_context + "\n\n"
                       + STRUCTURED_RESPONSE_FORMAT)

    # If user confirmed but no plan was generated (step extraction failed),
    # inject a hint to force a structured Mode B plan with explicit capability IDs
    intent = state.get("intent", "")
    has_plan = state.get("current_plan") is not None
    if intent == "confirm_yes" and not has_plan:
        logger.info("  [analyze_and_plan] Re-plan: confirm_yes with no prior plan — injecting structure hint")
        messages = messages + [SystemMessage(content=(
            "IMPORTANT: Your previous response was not parsed into an executable plan. "
            "The user has confirmed they want to proceed. You MUST now generate a Mode B "
            "executable plan with explicit capability IDs for each step. For each step, "
            "clearly state the action in 'domain.action' format (e.g., agents.create, "
            "custom_tools.create, agents.assign_tools, agents.chat). Do NOT describe "
            "execution results — only present the plan steps for approval."
        ))]

    full_messages = [SystemMessage(content=enhanced_prompt)] + messages

    t0 = time.time()
    response = await safe_llm_invoke(llm, full_messages)
    _sanitize_llm_response(response)
    response_text = response.content
    elapsed = time.time() - t0
    logger.info(f"  [analyze_and_plan] Plan response generated ({elapsed:.1f}s, {len(response_text)} chars)")

    # Step 2: Extract structured steps via mini model
    # Format available agents for the extraction prompt
    agents_for_extract = "(No specialized agents available)"
    if available_agents_list:
        agents_for_extract = "\n".join([
            f"  - {a.id}: {a.name} ({', '.join(a.specializations[:3])})"
            for a in available_agents_list
        ])

    steps = []
    try:
        logger.info(f"  [analyze_and_plan] Extracting structured steps via full model...")
        t0 = time.time()
        mini_llm = get_llm(mini=False, streaming=False)  # Full model for reliable step extraction
        extract_response = await safe_llm_invoke(mini_llm, [
            SystemMessage(content="You extract structured data from text. Respond only with valid JSON."),
            HumanMessage(content=EXTRACT_STEPS_PROMPT.format(
                response_text=response_text,
                available_agents=agents_for_extract
            )),
        ])

        raw = extract_response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]

        parsed = json.loads(raw)
        elapsed = time.time() - t0

        if isinstance(parsed, list):
            steps = []
            for i, s in enumerate(parsed):
                domain = s.get("domain", "")
                action = s.get("action", "")
                # Normalize to fix LLM extraction errors like "agents.agents.chat"
                domain, action = _normalize_capability(domain, action)
                steps.append({
                    "id": f"step_{i+1}",
                    "order": i + 1,
                    "description": s.get("description", ""),
                    "domain": domain,
                    "action": action,
                    "parameters": s.get("parameters", {}),
                    "status": "pending",
                    "result": None,
                })
            logger.info(f"  [analyze_and_plan] Extracted {len(steps)} steps ({elapsed:.1f}s):")
            for step in steps:
                params_str = f" params={step['parameters']}" if step['parameters'] else ""
                logger.info(f"    → [{step['domain']}/{step['action']}] {step['description']}{params_str}")
        else:
            logger.warning(f"  [analyze_and_plan] Parsed result is not a list: {type(parsed)}")

    except json.JSONDecodeError as e:
        logger.warning(f"  [analyze_and_plan] JSON parse failed: {e}")
        logger.warning(f"  [analyze_and_plan] Raw extraction output: {extract_response.content[:200]}")
    except Exception as e:
        logger.warning(f"  [analyze_and_plan] Step extraction failed: {e}")

    # ── Information-gathering mode: no steps extracted ──
    # When the LLM response is asking the user for information (Mode A),
    # the extractor returns an empty array. In this case, return the
    # response as a conversational message WITHOUT a plan card.
    # The user's reply will be classified as "provide_context" and routed
    # back to analyze_and_plan, where the LLM will have the info it needs.
    if not steps:
        logger.info("  [analyze_and_plan] No executable steps extracted — information-gathering mode")
        logger.info("  [analyze_and_plan] Returning conversational response (no plan card)")
        result = {
            "messages": [response],
            "current_plan": None,  # No plan card — user needs to provide info first
        }
        if system_context:
            result["system_context"] = system_context
        return result

    # ── Post-planning permission validation ──
    # Check each step against the user's permissions and mark any that
    # require a higher role. This surfaces permission issues at plan time
    # (before the user approves) rather than failing at execution time.
    from permissions import get_user_role, can_access_capability, get_role_name, DOMAIN_ROLE_REQUIREMENTS
    plan_user_role = get_user_role(user_context)
    for step in steps:
        step_domain = step.get("domain", "")
        if step_domain == "agent":
            continue  # Agent delegations don't have domain-level restrictions
        domain_min_role = DOMAIN_ROLE_REQUIREMENTS.get(step_domain)
        if domain_min_role is not None and not can_access_capability(plan_user_role, domain_min_role):
            required_name = get_role_name(domain_min_role)
            user_role_name = get_role_name(plan_user_role)
            step["status"] = "permission_denied"
            step["permission_note"] = (
                f"Requires {required_name} role (your role: {user_role_name}). "
                f"Contact an administrator for access."
            )
            logger.info(f"  [analyze_and_plan] Step {step.get('id')}: permission_denied — "
                        f"{step_domain}.{step.get('action')} requires {required_name}")

    # ── Enrich steps with action metadata (destructive flag) ──
    try:
        from execution import get_action_registry
        action_registry = get_action_registry()
        has_destructive = False
        for step in steps:
            cap_id = f"{step['domain']}.{step['action']}"
            action_def = action_registry.get_action(cap_id)
            if action_def and action_def.is_destructive:
                step["is_destructive"] = True
                has_destructive = True
            else:
                step["is_destructive"] = False
    except Exception:
        has_destructive = False

    # Build the plan object
    goal = ""
    for msg in reversed(messages):
        if hasattr(msg, "content") and isinstance(msg.content, str):
            goal = msg.content
            break

    plan = {
        "plan_id": f"plan_{state.get('session_id', 'unknown')}",
        "goal": goal,
        "steps": steps,
        "context_needed": [],
        "notes": None,
        "status": "draft",
        "has_destructive_steps": has_destructive,
    }

    # Auto-execute single non-destructive steps (skip approval for simple operations)
    if _is_auto_executable(steps):
        plan["status"] = "confirmed"
        logger.info(f"  [analyze_and_plan] Plan auto-confirmed (single non-destructive step: {steps[0].get('domain')}.{steps[0].get('action')})")
    else:
        logger.info(f"  [analyze_and_plan] Plan created: {len(steps)} steps, status=draft")

    # Store system context for use during execution (for validation)
    result = {
        "messages": [response],
        "current_plan": plan,
    }

    # Pass context to execution phase if we gathered it
    if system_context:
        result["system_context"] = system_context

    return result


# ─── Parameter Enrichment ─────────────────────────────────────────────────

EXTRACT_PARAMS_PROMPT = """Extract parameter values for an API call from the given description.

Capability: {capability_id}
Description: {description}

Required fields — YOU MUST provide a value for EACH of these:
{required_fields}

Optional fields — include only if explicitly mentioned:
{optional_fields}

Already extracted (do not change these):
{current_params}

CRITICAL RULES:
1. Use ONLY the EXACT field names listed above (e.g., "agent_objective" NOT "agent_name")
2. For "agent_objective", provide a system prompt like "You are a helpful assistant that..."
3. For "agent_description", use the agent's display name
4. Return ONLY valid JSON — no explanation, no markdown
5. Include ALL required fields even if you need to generate sensible defaults
6. For FILE fields (like "file"), prefer the File ID (e.g., "abc123def456") if available — look for "File ID: `xxx`" in the context. If a full filesystem path is provided (e.g., "C:\\path\\to\\file.txt"), use the COMPLETE path exactly as given — do NOT shorten it to just the filename.

Example for agents.create:
{{"agent_description": "Customer Support Bot", "agent_objective": "You are a helpful customer support assistant. Help users with their questions and issues.", "agent_enabled": true}}

Return the JSON object:"""


async def _enrich_parameters(action_def, current_params: dict, description: str) -> dict:
    """
    Use AI to extract missing parameters from the step description.

    This fills in required fields that weren't explicitly extracted during
    the planning phase by analyzing the natural language description.
    """
    # Get required and optional fields from the action definition
    required_fields = []
    optional_fields = []

    if action_def.is_simple:
        for f in action_def.primary_route.input_fields:
            field_info = f"{f.name} ({f.field_type.value})"
            if f.description:
                field_info += f" - {f.description}"
            if f.choices:
                field_info += f" [choices: {', '.join(f.choices)}]"
            if f.default is not None:
                field_info += f" [default: {f.default}]"

            if f.required and f.default is None:
                required_fields.append(field_info)
            else:
                optional_fields.append(field_info)

    # If we have all required fields already, skip AI extraction
    if action_def.is_simple:
        required_names = {f.name for f in action_def.primary_route.input_fields
                        if f.required and f.default is None}
        if required_names.issubset(current_params.keys()):
            return current_params

    # Use mini model to extract parameters (non-streaming to avoid token leakage)
    try:
        llm = get_llm(mini=True, streaming=False)
        prompt = EXTRACT_PARAMS_PROMPT.format(
            capability_id=action_def.capability_id,
            description=description,
            required_fields="\n".join(f"  - {f}" for f in required_fields) or "  (none)",
            optional_fields="\n".join(f"  - {f}" for f in optional_fields) or "  (none)",
            current_params=json.dumps(current_params, indent=2),
        )

        logger.info(f"  [execute] Parameter extraction prompt:\n{prompt[:500]}...")

        response = await safe_llm_invoke(llm, [
            SystemMessage(content="You extract structured data from text. Return only valid JSON."),
            HumanMessage(content=prompt),
        ])

        raw = response.content.strip()
        logger.info(f"  [execute] LLM extraction response: {raw[:300]}")
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]

        extracted = json.loads(raw)
        if isinstance(extracted, dict):
            # Validate and fix field names - LLM sometimes uses wrong names
            if action_def.is_simple:
                valid_fields = {f.name for f in action_def.primary_route.input_fields}

                # Common field name mistakes the LLM makes.
                # Only apply corrections when the original key is NOT already a
                # valid field — otherwise we'd wrongly remap fields like "name"
                # (valid for connections.create) to "agent_description".
                field_corrections = {
                    "agent_name": "agent_description",  # LLM often confuses these
                    "name": "agent_description",
                    "objective": "agent_objective",
                    "system_prompt": "agent_objective",
                    "enabled": "agent_enabled",
                    "tools": "tool_names",
                }

                # Apply corrections and filter to valid fields only
                corrected = {}
                for key, value in extracted.items():
                    if key in valid_fields:
                        # Key is already valid — use it as-is, no correction
                        corrected[key] = value
                    else:
                        # Try correction
                        corrected_key = field_corrections.get(key, key)
                        if corrected_key in valid_fields:
                            logger.info(f"  [execute] Corrected field name: {key} → {corrected_key}")
                            corrected[corrected_key] = value
                        else:
                            logger.warning(f"  [execute] Ignoring invalid field: {key}")

                extracted = corrected

            # Merge with current params (current takes precedence)
            result = {**extracted, **current_params}
            logger.info(f"  [execute] Enriched parameters: {result}")
            return result

    except Exception as e:
        logger.warning(f"  [execute] Parameter extraction failed: {e}")

    return current_params


# ─── Reference Parameter Resolution ──────────────────────────────────────

async def _resolve_reference_parameters(action_def, parameters: dict) -> dict:
    """
    Resolve REFERENCE-type parameters from names to numeric IDs.

    The LLM often extracts reference fields as human-readable names (e.g.,
    "Invoice Processor" for workflow_id) instead of numeric database IDs.
    This function detects non-numeric reference values and resolves them
    to their correct IDs by querying the appropriate API.

    Currently supports:
    - reference_domain="workflows" → resolves via GET /get/workflows
    - reference_domain="agents" → resolves via GET /api/agents/list

    Args:
        action_def: The action definition with field schemas
        parameters: The current parameters dict

    Returns:
        Updated parameters with resolved reference IDs
    """
    if not action_def or not action_def.is_simple:
        return parameters

    resolved = dict(parameters)

    for field_def in action_def.primary_route.input_fields:
        # Only process REFERENCE fields
        if field_def.field_type.value != "reference":
            continue

        field_name = field_def.name
        value = resolved.get(field_name)

        if value is None:
            continue

        # Check if the value is already a numeric ID
        if isinstance(value, int):
            continue
        if isinstance(value, str) and value.strip().isdigit():
            # Convert string digit to int
            resolved[field_name] = int(value.strip())
            logger.info(f"  [execute] Converted {field_name} from string '{value}' to int {resolved[field_name]}")
            continue

        # Value is a non-numeric string — likely a name that needs resolution
        ref_domain = field_def.reference_domain

        if ref_domain == "workflows":
            resolved_id = await _resolve_workflow_name_to_id(str(value))
            if resolved_id is not None:
                logger.info(f"  [execute] Resolved {field_name}: '{value}' → ID {resolved_id}")
                resolved[field_name] = resolved_id
            else:
                logger.warning(f"  [execute] Could not resolve workflow name '{value}' to an ID")
                # Keep the original value — the API call will likely fail,
                # but the error message will be more useful than silently dropping it

        elif ref_domain == "agents":
            resolved_id = await _resolve_agent_name_to_id(str(value))
            if resolved_id is not None:
                logger.info(f"  [execute] Resolved {field_name}: '{value}' → ID {resolved_id}")
                resolved[field_name] = resolved_id
            else:
                logger.warning(f"  [execute] Could not resolve agent name '{value}' to an ID")

        elif ref_domain == "connections":
            resolved_id = await _resolve_connection_name_to_id(str(value))
            if resolved_id is not None:
                logger.info(f"  [execute] Resolved {field_name}: '{value}' → ID {resolved_id}")
                resolved[field_name] = resolved_id
            else:
                logger.warning(f"  [execute] Could not resolve connection name '{value}' to an ID")

        else:
            logger.debug(f"  [execute] Skipping reference resolution for {field_name} (domain={ref_domain}, not yet supported)")

    return resolved


async def _resolve_workflow_name_to_id(workflow_name: str) -> int | None:
    """
    Resolve a workflow name to its numeric database ID.

    Calls GET /get/workflows and searches for a matching name.

    Args:
        workflow_name: The workflow name to resolve

    Returns:
        The numeric workflow ID, or None if not found
    """
    try:
        from agent_communication.adapters.workflow_builder import WorkflowBuilderAdapter

        adapter = WorkflowBuilderAdapter()
        try:
            match = await adapter.find_workflow_by_name(workflow_name)
            if match:
                workflow_id = match.get("id")
                logger.info(f"  [execute] Resolved workflow '{workflow_name}' → ID {workflow_id}")
                return int(workflow_id)
            else:
                logger.warning(f"  [execute] No workflow found matching name '{workflow_name}'")
                return None
        finally:
            await adapter.close()

    except Exception as e:
        logger.error(f"  [execute] Error resolving workflow name to ID: {e}")
        return None


async def _resolve_agent_name_to_id(agent_name: str) -> int | None:
    """
    Resolve an agent name to its numeric database ID.

    Calls GET /api/agents/list and searches for a matching name.
    Supports exact match (case-insensitive) and fuzzy matching (≥0.7 threshold).

    Args:
        agent_name: The agent name to resolve (e.g., "Gen Agent 012")

    Returns:
        The numeric agent ID, or None if not found
    """
    try:
        import httpx
        async with httpx.AsyncClient(
            base_url=AI_HUB_BASE_URL,
            headers={"X-API-Key": AI_HUB_API_KEY},
            timeout=10.0,
        ) as client:
            response = await client.get("/api/agents/list")
            if response.status_code == 200:
                data = response.json()
                agents = data.get("agents", [])
                name_lower = agent_name.lower().strip()

                # Exact match first
                for agent in agents:
                    if agent.get("agent_name", "").lower() == name_lower:
                        agent_id = int(agent["agent_id"])
                        logger.info(f"  [execute] Resolved agent '{agent_name}' → ID {agent_id}")
                        return agent_id

                # Fuzzy match
                from difflib import SequenceMatcher
                best_id = None
                best_score = 0.0
                for agent in agents:
                    desc = agent.get("agent_name", "").lower()
                    score = SequenceMatcher(None, name_lower, desc).ratio()
                    if score > best_score and score >= 0.7:
                        best_score = score
                        best_id = int(agent["agent_id"])

                if best_id is not None:
                    logger.info(f"  [execute] Fuzzy matched agent '{agent_name}' → ID {best_id} (score: {best_score:.2f})")
                    return best_id

                logger.warning(f"  [execute] No agent found matching name '{agent_name}'")
            else:
                logger.warning(f"  [execute] Agent list API returned {response.status_code}")

    except Exception as e:
        logger.error(f"  [execute] Error resolving agent name to ID: {e}")
    return None


async def _resolve_connection_name_to_id(connection_name: str) -> int | None:
    """
    Resolve a connection name to its numeric database ID.

    Calls GET /api/connections and searches for a matching name.
    Supports exact match (case-insensitive) and fuzzy matching (≥0.7 threshold).

    Args:
        connection_name: The connection name to resolve (e.g., "AIRDB2_SQL_Connection")

    Returns:
        The numeric connection ID, or None if not found
    """
    try:
        import httpx
        async with httpx.AsyncClient(
            base_url=AI_HUB_BASE_URL,
            headers={"X-API-Key": AI_HUB_API_KEY},
            timeout=10.0,
        ) as client:
            response = await client.get("/api/connections")
            if response.status_code == 200:
                data = response.json()
                # Response format: {"status": "success", "connections": [...]}
                # or could be a plain list
                if isinstance(data, list):
                    connections = data
                elif isinstance(data, dict):
                    connections = data.get("connections", data.get("response", []))
                    if not isinstance(connections, list):
                        connections = []
                else:
                    connections = []
                name_lower = connection_name.lower().strip()

                # Exact match first
                for conn in connections:
                    conn_name = conn.get("connection_name", "") or conn.get("name", "")
                    if conn_name.lower() == name_lower:
                        conn_id = int(conn["id"])
                        logger.info(f"  [execute] Resolved connection '{connection_name}' → ID {conn_id}")
                        return conn_id

                # Fuzzy match
                from difflib import SequenceMatcher
                best_id = None
                best_score = 0.0
                for conn in connections:
                    conn_name = conn.get("connection_name", "") or conn.get("name", "")
                    desc = conn_name.lower()
                    score = SequenceMatcher(None, name_lower, desc).ratio()
                    if score > best_score and score >= 0.7:
                        best_score = score
                        best_id = int(conn["id"])

                if best_id is not None:
                    logger.info(f"  [execute] Fuzzy matched connection '{connection_name}' → ID {best_id} (score: {best_score:.2f})")
                    return best_id

                logger.warning(f"  [execute] No connection found matching name '{connection_name}'")
            else:
                logger.warning(f"  [execute] Connection list API returned {response.status_code}")

    except Exception as e:
        logger.error(f"  [execute] Error resolving connection name to ID: {e}")
    return None


# ─── Execution ────────────────────────────────────────────────────────────

async def execute(state: dict) -> dict:
    """
    Execute the confirmed plan by calling the AI Hub API or delegating to agents.

    For each step:
    - If domain="agent": Delegate to the specified agent
    - Otherwise: Execute via direct API call

    This treats agents as first-class execution options alongside direct API actions.
    """
    messages = state.get("messages", [])
    plan = state.get("current_plan")
    system_context = state.get("system_context")  # May be None if context gathering failed
    agent_conversations = state.get("agent_conversations", {})

    logger.info("  [execute] ════════════════════════════════════════")
    logger.info("  [execute] EXECUTION STARTING (self-healing enabled)")
    logger.info(f"  [execute] AI Hub target: {AI_HUB_BASE_URL}")
    if system_context:
        logger.info(f"  [execute] System context available: {len(system_context.core_tools)} tools")
    else:
        logger.info("  [execute] System context: not available (no validation)")
    logger.info("  [execute] ════════════════════════════════════════")

    # Initialize resilience components
    from pathlib import Path as _Path
    _data_dir = _Path(__file__).parent.parent / "data"
    try:
        from resilience import OutcomeTracker, FailureAnalyzer, SelfCorrectionEngine, LearningMemory, ExecutionOutcome
        outcome_tracker = OutcomeTracker(_data_dir)
        failure_analyzer = FailureAnalyzer()
        correction_engine = SelfCorrectionEngine()
        learning_memory = LearningMemory(_data_dir)
        _resilience_available = True
        logger.info("  [execute] Resilience layer initialized")
    except Exception as _e:
        logger.warning(f"  [execute] Resilience layer not available: {_e}")
        outcome_tracker = None
        failure_analyzer = None
        correction_engine = None
        learning_memory = None
        _resilience_available = False

    correction_history = []

    if not plan or not plan.get("steps"):
        logger.warning("  [execute] No plan or no steps to execute")
        llm = get_llm(mini=False)
        response = await safe_llm_invoke(llm, [
            SystemMessage(content=BUILDER_SYSTEM_PROMPT),
            *messages,
            HumanMessage(content="There's no plan to execute. What would you like to build?"),
        ])
        return {"messages": [response], "current_plan": None}

    # Import executor (deferred to avoid circular imports)
    from execution import ActionExecutor, get_action_registry
    from execution.registry_loader import is_initialized

    execution_results = []
    updated_steps = []
    new_agent_conversations = {**agent_conversations}
    pending_agent_question = None
    current_agent_conversation_id = None
    workflow_edit_context = state.get("workflow_edit_context")

    # Track connections created during execution for cross-step resolution
    # Maps connection_name → connection_id for connections created in this execution
    newly_created_connections = {}

    # Track agents created during execution for cross-step resolution
    # Maps agent_name → agent_id for agents created in this execution
    newly_created_agents = {}

    # Track workflows created during execution for cross-step resolution
    # Maps workflow_name → workflow_id for workflows created/saved in this execution
    newly_created_workflows = {}

    # Track integrations created during execution for cross-step resolution
    # Maps integration_name → integration_id for integrations created in this execution
    newly_created_integrations = {}

    # Permission context for hard checks during execution
    from permissions import get_user_role, can_access_capability, get_role_name, DOMAIN_ROLE_REQUIREMENTS
    user_context = state.get("user_context")
    user_role = get_user_role(user_context)

    async with ActionExecutor(base_url=AI_HUB_BASE_URL, api_key=AI_HUB_API_KEY) as executor:
        for step in plan["steps"]:
            # ═══════════════════════════════════════════════════════════════
            # RESUMPTION GUARD — skip already-processed steps
            # ═══════════════════════════════════════════════════════════════
            # When execute() is called after an agent conversation completes,
            # steps that were already processed have a terminal status.
            existing_status = step.get("status", "")
            if existing_status in ("completed", "failed", "delegated", "awaiting_input", "skipped", "permission_denied"):
                updated_steps.append(step)
                # Restore conversation tracking from prior delegation steps
                if existing_status in ("delegated", "awaiting_input"):
                    conv_state = step.get("result", {}).get("conversation_state")
                    if conv_state:
                        new_agent_conversations[conv_state["id"]] = conv_state
                        current_agent_conversation_id = conv_state["id"]
                continue

            domain = step.get("domain", "")
            action = step.get("action", "")
            description = step.get("description", "")

            # Normalize to fix LLM extraction errors like "agents.agents.chat"
            domain, action = _normalize_capability(domain, action)

            # ═══════════════════════════════════════════════════════════════
            # DEPENDENCY CHECK — skip if prior steps failed
            # ═══════════════════════════════════════════════════════════════
            # Steps in a plan are sequential. If an earlier step failed, later
            # steps that depend on its outputs (workflow_id, connection_id, etc.)
            # will also fail or produce invalid results.  Skip them cleanly.
            prior_failed = [
                s for s in updated_steps
                if s.get("status") == "failed" and s.get("order", 0) < step.get("order", 999)
            ]
            if prior_failed:
                # Check if THIS step actually depends on any failed step
                if _step_depends_on_failed(step, prior_failed, newly_created_connections,
                                          newly_created_agents, newly_created_workflows,
                                          newly_created_integrations):
                    failed_desc = "; ".join(
                        f"Step {s.get('order')}: {s.get('domain','')}.{s.get('action','')}"
                        for s in prior_failed
                    )
                    logger.warning(f"  [execute] Step {step.get('order')}: SKIPPING — depends on failed prior step(s): {failed_desc}")
                    updated_steps.append({
                        **step,
                        "status": "skipped",
                        "result": {
                            "status": "skipped",
                            "message": f"Skipped because a prior step failed: {failed_desc}",
                        },
                    })
                    continue
                else:
                    # Step is independent of failed steps — allow it to proceed
                    logger.info(f"  [execute] Step {step.get('order')}: PROCEEDING — independent of failed steps")

            # ═══════════════════════════════════════════════════════════════
            # PERMISSION CHECK (defense-in-depth)
            # ═══════════════════════════════════════════════════════════════
            if domain != "agent":
                # Check domain-level permission first
                domain_min_role = DOMAIN_ROLE_REQUIREMENTS.get(domain)
                if domain_min_role is not None and not can_access_capability(user_role, domain_min_role):
                    required_name = get_role_name(domain_min_role)
                    user_role_name = get_role_name(user_role)
                    logger.warning(f"  [execute] Step {step.get('order')}: PERMISSION DENIED — "
                                   f"{domain}.{action} requires {required_name}, user has {user_role_name}")
                    updated_steps.append({
                        **step,
                        "status": "failed",
                        "result": {
                            "status": "failed",
                            "message": f"Permission denied: {domain}.{action} requires {required_name} role "
                                       f"(your role: {user_role_name}). Contact an administrator for access.",
                            "error": f"Insufficient permissions — {required_name} role required.",
                        },
                    })
                    continue

            # ═══════════════════════════════════════════════════════════════
            # AGENT DELEGATION STEP
            # ═══════════════════════════════════════════════════════════════
            if domain == "agent":
                agent_id = action
                logger.info(f"  [execute] Step {step['order']}: DELEGATING to agent:{agent_id}")
                logger.info(f"  [execute]   → {description}")

                MAX_DELEGATION_ATTEMPTS = 2
                delegation_result = None

                for attempt in range(1, MAX_DELEGATION_ATTEMPTS + 1):
                    try:
                        # On retry, simplify the task description AND fall back
                        # to conversational mode so the agent can ask questions
                        # instead of trying to build everything in one shot.
                        task_desc = description
                        # For workflow_agent, enrich the task with the user's full
                        # goal so the agent gets all details (SQL queries, emails, etc.)
                        if agent_id == "workflow_agent" and plan and plan.get("goal"):
                            user_goal = plan["goal"]
                            if len(user_goal) > len(description):
                                task_desc = f"{description}\n\nFull user requirements:\n{user_goal}"
                        skip_builder_delegation = False
                        if attempt > 1:
                            import re
                            task_desc = re.sub(r'[*_#`\[\]]', '', description)[:500]
                            task_desc = f"Please create a simple version: {task_desc}"
                            skip_builder_delegation = True
                            logger.info(f"  [execute]   🔄 Retry attempt {attempt} (conversational mode)")

                        # Inject the user's original goal into parameters so the
                        # auto-reply mechanism can forward full context to the agent
                        step_params = step.get("parameters", {})
                        if plan and plan.get("goal"):
                            step_params = {**step_params, "_user_goal": plan["goal"]}

                        # Execute agent delegation
                        delegation_result = await _execute_agent_delegation(
                            agent_id=agent_id,
                            task_description=task_desc,
                            parameters=step_params,
                            session_id=state.get("session_id"),
                            workflow_edit_context=workflow_edit_context,
                            skip_builder_delegation=skip_builder_delegation,
                        )

                        if delegation_result["success"]:
                            break  # Success — exit retry loop

                        # Only retry on content filter errors
                        if not delegation_result.get("is_content_filter", False):
                            break  # Non-retryable failure
                        if attempt < MAX_DELEGATION_ATTEMPTS:
                            logger.warning(f"  [execute]   ⚠ Content filter hit, will retry with simplified description")

                    except Exception as e:
                        logger.error(f"  [execute]   ✗ Agent delegation error (attempt {attempt}): {e}")
                        delegation_result = {"success": False, "error": str(e)}
                        if attempt < MAX_DELEGATION_ATTEMPTS and "timeout" in str(e).lower():
                            logger.info(f"  [execute]   🔄 Timeout — will retry")
                            continue
                        break  # Non-retryable exception

                # Process the final delegation result
                if delegation_result and delegation_result.get("success"):
                    # Determine step status based on whether agent needs more input
                    needs_input = delegation_result.get("needs_user_input", False)
                    conv_status = delegation_result.get("conversation_state", {}).get("status", "")
                    if needs_input:
                        step_status = "awaiting_input"
                    elif conv_status == "completed":
                        step_status = "completed"
                    else:
                        step_status = "delegated"

                    logger.info(f"  [execute]   ✓ Agent delegation started (needs_input={needs_input})")
                    updated_step = {
                        **step,
                        "status": step_status,
                        "result": delegation_result,
                    }

                    # Track the agent conversation
                    if delegation_result.get("conversation_state"):
                        conv_state = delegation_result["conversation_state"]
                        new_agent_conversations[conv_state["id"]] = conv_state
                        current_agent_conversation_id = conv_state["id"]

                        # Check if agent needs user input - use the direct flag or conversation state
                        if needs_input or conv_state.get("pending_question"):
                            pending_agent_question = conv_state.get("pending_question") or delegation_result.get("agent_response")
                            logger.info(f"  [execute]   📝 Agent is asking: {pending_agent_question[:100]}...")

                    # Capture workflow edit context if this is a workflow edit
                    if delegation_result.get("workflow_edit_context"):
                        workflow_edit_context = delegation_result["workflow_edit_context"]
                        logger.info(f"  [execute]   📝 Workflow edit context: id={workflow_edit_context.get('workflow_id')}, name='{workflow_edit_context.get('workflow_name')}'")

                    # ─── Auto-save workflow if commands were generated ─────────
                    # When the WorkflowAgent returns workflow_commands, compile them
                    # into a workflow structure and save to the database. This makes
                    # the workflow available for subsequent steps (e.g., schedules.create)
                    # that reference it by name or ID.
                    wf_cmds = delegation_result.get("workflow_commands")
                    if wf_cmds and isinstance(wf_cmds, dict) and wf_cmds.get("commands"):
                        try:
                            from execution.workflow_compiler import compile_workflow_commands

                            # Extract workflow name from step params or description
                            wf_name = step.get("parameters", {}).get("workflow_name", "")
                            if not wf_name:
                                # Try to extract from description
                                desc_lower = description.lower()
                                for marker in ["named '", 'named "', "called '", 'called "']:
                                    idx = desc_lower.find(marker)
                                    if idx >= 0:
                                        start = idx + len(marker)
                                        end = description.find(marker[-1], start)
                                        if end > start:
                                            wf_name = description[start:end]
                                            break
                                if not wf_name:
                                    wf_name = "Builder Agent Workflow"

                            save_payload = compile_workflow_commands(
                                commands=wf_cmds["commands"],
                                workflow_name=wf_name,
                            )
                            logger.info(f"  [execute]   💾 Saving workflow '{wf_name}' ({len(wf_cmds['commands'])} commands compiled)")

                            save_result = await executor.execute_step(
                                domain="workflows",
                                action="create",
                                parameters=save_payload,
                                description=f"Save compiled workflow '{wf_name}'",
                            )

                            if save_result.is_success:
                                # Extract the workflow ID from the save response
                                saved_id = None
                                if save_result.data:
                                    if isinstance(save_result.data, dict):
                                        saved_id = save_result.data.get("workflow_id") or save_result.data.get("database_version")
                                    elif isinstance(save_result.data, str):
                                        import json as _json
                                        try:
                                            _parsed = _json.loads(save_result.data)
                                            saved_id = _parsed.get("workflow_id") or _parsed.get("database_version")
                                        except (ValueError, _json.JSONDecodeError):
                                            pass

                                logger.info(f"  [execute]   ✓ Workflow saved successfully (id={saved_id})")
                                # Store in result so subsequent steps can reference it
                                delegation_result["saved_workflow_name"] = wf_name
                                delegation_result["saved_workflow_id"] = saved_id
                                updated_step["result"] = delegation_result

                                # Track the workflow for cross-step resolution
                                if saved_id and wf_name:
                                    newly_created_workflows[wf_name] = saved_id
                                    logger.info(f"  [execute]   📋 Tracked new workflow '{wf_name}' → ID {saved_id}")
                            else:
                                logger.warning(f"  [execute]   ⚠ Workflow save failed: {save_result.error}")
                                # Don't fail the step — the delegation itself succeeded
                                delegation_result["workflow_save_error"] = save_result.error

                        except Exception as e:
                            logger.error(f"  [execute]   ✗ Workflow compile/save error: {e}", exc_info=True)
                            delegation_result["workflow_save_error"] = str(e)

                else:
                    error_msg = delegation_result.get("error", "Unknown error") if delegation_result else "No response"
                    logger.warning(f"  [execute]   ✗ Agent delegation failed after {attempt} attempt(s): {error_msg}")
                    updated_step = {
                        **step,
                        "status": "failed",
                        "result": delegation_result or {"success": False, "error": error_msg},
                    }

                updated_steps.append(updated_step)

                # PAUSE: if delegation needs user input, stop here and leave remaining steps pending.
                # They will be executed when the agent conversation completes (via handle_agent_response → execute resumption).
                if delegation_result and delegation_result.get("success") and delegation_result.get("needs_user_input", False):
                    current_idx = plan["steps"].index(step)
                    remaining_count = len(plan["steps"]) - current_idx - 1
                    for remaining in plan["steps"][current_idx + 1:]:
                        updated_steps.append(remaining)  # Keep original status (no status = pending)
                    logger.info(f"  [execute] ⏸ Paused execution — agent needs user input. {remaining_count} step(s) pending.")
                    break

                continue

            # ═══════════════════════════════════════════════════════════════
            # DIRECT API ACTION STEP
            # ═══════════════════════════════════════════════════════════════
            logger.info(f"  [execute] Step {step['order']}: {domain}.{action}")
            logger.info(f"  [execute]   → {description}")

            # Pre-check: capabilities that require a file upload
            FILE_REQUIRED_CAPABILITIES = {"knowledge.attach", "agents.import"}
            capability_key = f"{domain}.{action}"
            if capability_key in FILE_REQUIRED_CAPABILITIES:
                # Check if user attached files in ANY message in the conversation
                # (the file is uploaded with the first message, but execution happens
                # after the user approves in a subsequent message)
                messages = state.get("messages", [])
                has_attached_files = False
                for m in messages:
                    msg_content = ""
                    if hasattr(m, "type") and m.type == "human":
                        msg_content = m.content if isinstance(m.content, str) else ""
                    elif isinstance(m, dict) and m.get("role") == "user":
                        msg_content = m.get("content", "")
                    if "**Attached Files:**" in msg_content:
                        has_attached_files = True
                        break

                # Also check if step parameters contain a filesystem path
                # (allows users to provide file paths directly in their request)
                if not has_attached_files:
                    from pathlib import Path
                    step_params = step.get("parameters", {})
                    # Check common file parameter keys and ALL parameter values
                    for param_key, param_val in step_params.items():
                        if isinstance(param_val, str) and param_val.strip():
                            try:
                                file_path = Path(param_val.strip())
                                if file_path.exists() and file_path.is_file():
                                    has_attached_files = True
                                    logger.info(f"  [execute]   ✓ Found filesystem path in parameters['{param_key}']: {param_val}")
                                    break
                            except (OSError, ValueError):
                                pass
                
                # Also check if the user's messages contain a filesystem path
                # (the LLM may not have extracted it into parameters)
                if not has_attached_files:
                    import re
                    from pathlib import Path
                    for m in messages:
                        msg_content = ""
                        if hasattr(m, "type") and m.type == "human":
                            msg_content = m.content if isinstance(m.content, str) else ""
                        elif isinstance(m, dict) and m.get("role") == "user":
                            msg_content = m.get("content", "")
                        # Look for Windows or Unix file paths
                        path_matches = re.findall(r'[A-Za-z]:\\[^\s"\'<>|]+\.\w+|/(?:[\w.-]+/)+[\w.-]+\.\w+', msg_content)
                        for path_str in path_matches:
                            try:
                                fp = Path(path_str.strip())
                                if fp.exists() and fp.is_file():
                                    has_attached_files = True
                                    # Also inject the file path into step parameters
                                    if "file" not in step.get("parameters", {}):
                                        step.setdefault("parameters", {})["file"] = str(fp)
                                    logger.info(f"  [execute]   ✓ Found filesystem path in user message: {fp}")
                                    break
                            except (OSError, ValueError):
                                pass
                        if has_attached_files:
                            break

                # Also check if step parameters contain a file_id from the upload store
                if not has_attached_files:
                    try:
                        from routes.upload import get_file_path, get_file_path_by_filename
                        step_params = step.get("parameters", {})
                        for param_key in ("file", "file_id"):
                            param_val = step_params.get(param_key, "")
                            if isinstance(param_val, str) and param_val.strip():
                                # Try resolving as file_id
                                resolved = get_file_path(param_val.strip())
                                if resolved and resolved.exists():
                                    has_attached_files = True
                                    logger.info(f"  [execute]   ✓ Found file in upload store via '{param_key}': {param_val}")
                                    break
                                # Try resolving as filename
                                resolved = get_file_path_by_filename(param_val.strip())
                                if resolved and resolved.exists():
                                    has_attached_files = True
                                    logger.info(f"  [execute]   ✓ Found file in upload store by filename '{param_key}': {param_val}")
                                    break
                    except Exception as e:
                        logger.debug(f"  [execute]   Upload store check failed: {e}")

                if not has_attached_files:
                    logger.warning(f"  [execute]   ⚠ {capability_key} requires a file upload but none found")
                    updated_step = {
                        **step,
                        "status": "failed",
                        "result": {
                            "status": "failed",
                            "message": f"{capability_key} requires a file upload. Please upload a document and try again.",
                            "error": "No file attached — this action requires a file upload.",
                        },
                    }
                    updated_steps.append(updated_step)
                    continue

            # Check if registry is available
            if not is_initialized():
                logger.warning("  [execute]   ⚠ Registry not loaded, skipping execution")
                updated_step = {
                    **step,
                    "status": "skipped",
                    "result": {"message": "Action registry not available"},
                }
            else:
                # Get parameters from the step (extracted during planning)
                parameters = step.get("parameters", {})

                # ── Placeholder detection: catch obviously incomplete parameters ──
                # If the plan step has parameters with placeholder values (TBD, your_,
                # <placeholder>, etc.), the user hasn't provided the actual values yet.
                # Skip these steps with a clear message instead of sending bad API calls.
                import re as _re
                _placeholder_patterns = [
                    "tbd", "to be determined", "placeholder", "your_", "user_",
                    "example.com", "xxx", "...", "unknown", "n/a",
                    "fill in", "replace with", "enter your", "provide your",
                ]
                # Angle-bracket placeholders like <your_value>, <placeholder>, etc.
                # Must look like a placeholder tag, not just any use of < or > in code.
                _placeholder_angle_re = _re.compile(r"<[a-z_\s]+(value|here|name|host|port|url|key|token|id|email|password|address|server|database)s?>", _re.IGNORECASE)
                # Fields that legitimately contain code — skip placeholder detection
                _code_fields = {"code", "python_code", "query", "sql", "sql_query", "expression"}
                _placeholder_fields = []
                for _pk, _pv in parameters.items():
                    if isinstance(_pv, str):
                        # Skip code fields — they can contain >, <, ..., etc. legitimately
                        if _pk.lower() in _code_fields:
                            continue
                        _pv_lower = _pv.strip().lower()
                        if any(pat in _pv_lower for pat in _placeholder_patterns):
                            _placeholder_fields.append(f"{_pk}={_pv!r}")
                        elif _placeholder_angle_re.search(_pv):
                            _placeholder_fields.append(f"{_pk}={_pv!r}")
                if _placeholder_fields:
                    logger.warning(f"  [execute]   ⚠ Step has placeholder parameters: {', '.join(_placeholder_fields)}")
                    updated_step = {
                        **step,
                        "status": "failed",
                        "result": {
                            "status": "failed",
                            "message": (
                                f"This step has placeholder values that need real data: "
                                f"{', '.join(_placeholder_fields)}. "
                                f"Please provide the actual values and try again."
                            ),
                            "error": "Placeholder parameters detected — real values required.",
                        },
                    }
                    updated_steps.append(updated_step)
                    continue

                # Enrich parameters using AI if we have a description but missing fields
                capability_id = f"{domain}.{action}"
                action_registry = get_action_registry()
                action_def = action_registry.get_action(capability_id)

                if action_def and description:
                    # Inject cross-step context so the LLM knows about
                    # resources created by earlier steps (e.g., connection IDs, agent IDs)
                    enrichment_description = description
                    if newly_created_connections:
                        _cc = ", ".join(
                            f"'{n}' (ID: {i})" for n, i in newly_created_connections.items()
                        )
                        enrichment_description += f"\n\nContext from previous steps — connections created in this execution: {_cc}"
                    if newly_created_agents:
                        _aa = ", ".join(
                            f"'{n}' (ID: {i})" for n, i in newly_created_agents.items()
                        )
                        enrichment_description += f"\n\nContext from previous steps — agents created in this execution: {_aa}"
                    if newly_created_workflows:
                        _ww = ", ".join(
                            f"'{n}' (ID: {i})" for n, i in newly_created_workflows.items()
                        )
                        enrichment_description += f"\n\nContext from previous steps — workflows created in this execution: {_ww}"
                    if newly_created_integrations:
                        _ii = ", ".join(
                            f"'{n}' (ID: {i})" for n, i in newly_created_integrations.items()
                        )
                        enrichment_description += f"\n\nContext from previous steps — integrations created in this execution: {_ii}"
                    parameters = await _enrich_parameters(
                        action_def, parameters, enrichment_description
                    )

                # Validate and correct parameters against system context
                if system_context:
                    parameters = validate_and_correct_parameters(
                        parameters, system_context, capability_id
                    )

                # ── Cross-step resolution: inject newly created connection IDs ──
                # If a previous step created a connection and this step references
                # that connection by name, replace the name with the numeric ID.
                #
                # ALSO: scan previous step results directly as a fallback.
                # This handles cases where the tracking dict wasn't populated
                # (e.g., result.data structure was unexpected).
                if not newly_created_connections:
                    for prev_step in updated_steps:
                        if (prev_step.get("domain") == "connections"
                                and prev_step.get("action") == "create"
                                and prev_step.get("status") == "completed"):
                            prev_result = prev_step.get("result", {})
                            prev_data = prev_result.get("data", {})
                            prev_id = None
                            if isinstance(prev_data, dict):
                                if "response" in prev_data:
                                    try:
                                        prev_id = int(prev_data["response"])
                                    except (ValueError, TypeError):
                                        pass
                                elif "connection_id" in prev_data:
                                    try:
                                        prev_id = int(prev_data["connection_id"])
                                    except (ValueError, TypeError):
                                        pass
                            if prev_id:
                                prev_params = prev_step.get("parameters_used") or prev_step.get("parameters", {})
                                prev_name = prev_params.get("name") or prev_params.get("connection_name") or f"connection_{prev_id}"
                                newly_created_connections[prev_name] = prev_id
                                logger.info(f"  [execute]   🔗 Recovered connection from previous step result: '{prev_name}' → ID {prev_id}")
                                # Also update system_context
                                if system_context and hasattr(system_context, "connections"):
                                    if system_context.connections is None:
                                        system_context.connections = []
                                    system_context.connections.append({
                                        "id": prev_id,
                                        "connection_id": prev_id,
                                        "name": prev_name,
                                        "connection_name": prev_name,
                                    })

                conn_id = parameters.get("connection_id")
                if conn_id and isinstance(conn_id, str) and not conn_id.isdigit():
                    logger.info(f"  [execute]   🔍 Cross-step resolution: conn_id='{conn_id}', "
                               f"newly_created_connections={newly_created_connections}")

                    # Check if this connection name was just created (exact match)
                    if conn_id in newly_created_connections:
                        resolved_id = newly_created_connections[conn_id]
                        logger.info(f"  [execute]   🔗 Resolved newly created connection '{conn_id}' → ID {resolved_id} (exact match)")
                        parameters["connection_id"] = resolved_id
                    else:
                        # Fuzzy match: the plan may use a slightly different name
                        # than the one actually passed to connections.create
                        _conn_lower = conn_id.lower().strip()
                        _resolved = False
                        for _name, _id in newly_created_connections.items():
                            if _name.lower().strip() == _conn_lower:
                                logger.info(f"  [execute]   🔗 Resolved newly created connection '{conn_id}' → ID {_id} (case-insensitive match)")
                                parameters["connection_id"] = _id
                                _resolved = True
                                break

                        # Fallback: if there's exactly ONE newly created connection and we
                        # couldn't resolve by name, just use that connection ID
                        # (the LLM often uses different naming between steps)
                        if not _resolved and len(newly_created_connections) == 1:
                            _only_id = list(newly_created_connections.values())[0]
                            _only_name = list(newly_created_connections.keys())[0]
                            logger.info(f"  [execute]   🔗 Resolved newly created connection '{conn_id}' → ID {_only_id} "
                                       f"(fallback: only one connection '{_only_name}' was created)")
                            parameters["connection_id"] = _only_id

                # Handle missing connection_id: if connection_id is not provided at all,
                # check if we created exactly one connection in this execution and inject it
                elif not conn_id and newly_created_connections:
                    # Actions that commonly need connection_id but might not have it in parameters
                    connection_required_actions = {
                        "connections.test",
                        "connections.update",
                        "connections.delete",
                        "agents.create_data_agent",
                        "tools.create",
                    }
                    if capability_id in connection_required_actions and len(newly_created_connections) == 1:
                        _only_id = list(newly_created_connections.values())[0]
                        _only_name = list(newly_created_connections.keys())[0]
                        logger.info(f"  [execute]   🔗 Injected missing connection_id → ID {_only_id} "
                                   f"(from newly created connection '{_only_name}')")
                        parameters["connection_id"] = _only_id

                # ── Cross-step resolution: inject newly created agent IDs ──
                # If a previous step created an agent and this step references
                # that agent by name, replace the name with the numeric ID.
                #
                # ALSO: scan previous step results directly as a fallback.
                # This handles cases where the tracking dict wasn't populated
                # (e.g., result.data structure was unexpected).
                if not newly_created_agents:
                    for prev_step in updated_steps:
                        if (prev_step.get("domain") == "agents"
                                and prev_step.get("action") in ("create", "create_data_agent")
                                and prev_step.get("status") == "completed"):
                            prev_result = prev_step.get("result", {})
                            prev_data = prev_result.get("data", {})
                            prev_id = None
                            if isinstance(prev_data, dict):
                                if "response" in prev_data:
                                    try:
                                        prev_id = int(prev_data["response"])
                                    except (ValueError, TypeError):
                                        pass
                                elif "agent_id" in prev_data:
                                    try:
                                        prev_id = int(prev_data["agent_id"])
                                    except (ValueError, TypeError):
                                        pass
                            if prev_id:
                                prev_params = prev_step.get("parameters_used") or prev_step.get("parameters", {})
                                prev_name = prev_params.get("agent_description") or prev_params.get("name") or prev_params.get("agent_name") or f"agent_{prev_id}"
                                newly_created_agents[prev_name] = prev_id
                                logger.info(f"  [execute]   🤖 Recovered agent from previous step result: '{prev_name}' → ID {prev_id}")
                                # Also update system_context
                                if system_context and hasattr(system_context, "agents"):
                                    if system_context.agents is None:
                                        system_context.agents = []
                                    system_context.agents.append({
                                        "id": prev_id,
                                        "agent_id": prev_id,
                                        "name": prev_name,
                                        "description": prev_name,
                                        "agent_description": prev_name,
                                    })

                agent_id = parameters.get("agent_id")
                if agent_id and isinstance(agent_id, str) and not agent_id.isdigit():
                    logger.info(f"  [execute]   🔍 Cross-step resolution: agent_id='{agent_id}', "
                               f"newly_created_agents={newly_created_agents}")

                    # Check if this agent name was just created (exact match)
                    if agent_id in newly_created_agents:
                        resolved_id = newly_created_agents[agent_id]
                        logger.info(f"  [execute]   🤖 Resolved newly created agent '{agent_id}' → ID {resolved_id} (exact match)")
                        parameters["agent_id"] = resolved_id
                    else:
                        # Fuzzy match: the plan may use a slightly different name
                        # than the one actually passed to agents.create
                        _agent_lower = agent_id.lower().strip()
                        _resolved = False
                        for _name, _id in newly_created_agents.items():
                            if _name.lower().strip() == _agent_lower:
                                logger.info(f"  [execute]   🤖 Resolved newly created agent '{agent_id}' → ID {_id} (case-insensitive match)")
                                parameters["agent_id"] = _id
                                _resolved = True
                                break

                        # Fallback: if there's exactly ONE newly created agent and we
                        # couldn't resolve by name, just use that agent ID
                        # (the LLM often uses different naming between steps)
                        if not _resolved and len(newly_created_agents) == 1:
                            _only_id = list(newly_created_agents.values())[0]
                            _only_name = list(newly_created_agents.keys())[0]
                            logger.info(f"  [execute]   🤖 Resolved newly created agent '{agent_id}' → ID {_only_id} "
                                       f"(fallback: only one agent '{_only_name}' was created)")
                            parameters["agent_id"] = _only_id

                # Handle missing agent_id: if agent_id is not provided at all,
                # check if we created exactly one agent in this execution and inject it
                elif not agent_id and newly_created_agents:
                    # Actions that commonly need agent_id but might not have it in parameters
                    agent_required_actions = {
                        "agents.assign_tools",
                        "agents.chat",
                        "agents.update",
                        "agents.delete",
                        "agents.get",
                    }
                    if capability_id in agent_required_actions and len(newly_created_agents) == 1:
                        _only_id = list(newly_created_agents.values())[0]
                        _only_name = list(newly_created_agents.keys())[0]
                        logger.info(f"  [execute]   🤖 Injected missing agent_id → ID {_only_id} "
                                   f"(from newly created agent '{_only_name}')")
                        parameters["agent_id"] = _only_id

                # ── Cross-step resolution: inject newly created integration IDs ──
                # If a previous step created an integration and this step references
                # that integration by name, replace the name with the numeric ID.
                #
                # ALSO: scan previous step results directly as a fallback.
                # This handles cases where the tracking dict wasn't populated
                # (e.g., result.data structure was unexpected).
                if not newly_created_integrations:
                    for prev_step in updated_steps:
                        if (prev_step.get("domain") == "integrations"
                                and prev_step.get("action") == "create"
                                and prev_step.get("status") == "completed"):
                            prev_result = prev_step.get("result", {})
                            prev_data = prev_result.get("data", {})
                            prev_id = None
                            if isinstance(prev_data, dict):
                                if "integration_id" in prev_data:
                                    try:
                                        prev_id = int(prev_data["integration_id"])
                                    except (ValueError, TypeError):
                                        pass
                                elif "response" in prev_data:
                                    try:
                                        prev_id = int(prev_data["response"])
                                    except (ValueError, TypeError):
                                        pass
                            if prev_id:
                                prev_params = prev_step.get("parameters_used") or prev_step.get("parameters", {})
                                prev_name = prev_params.get("integration_name") or prev_params.get("name") or f"integration_{prev_id}"
                                newly_created_integrations[prev_name] = prev_id
                                logger.info(f"  [execute]   🔌 Recovered integration from previous step result: '{prev_name}' → ID {prev_id}")
                                # Also update system_context
                                if system_context and hasattr(system_context, "integrations"):
                                    if system_context.integrations is None:
                                        system_context.integrations = []
                                    system_context.integrations.append({
                                        "id": prev_id,
                                        "integration_id": prev_id,
                                        "name": prev_name,
                                        "integration_name": prev_name,
                                    })

                integration_id = parameters.get("integration_id")
                if integration_id and isinstance(integration_id, str) and not integration_id.isdigit():
                    logger.info(f"  [execute]   🔍 Cross-step resolution: integration_id='{integration_id}', "
                               f"newly_created_integrations={newly_created_integrations}")

                    # Check if this integration name was just created (exact match)
                    if integration_id in newly_created_integrations:
                        resolved_id = newly_created_integrations[integration_id]
                        logger.info(f"  [execute]   🔌 Resolved newly created integration '{integration_id}' → ID {resolved_id} (exact match)")
                        parameters["integration_id"] = resolved_id
                    else:
                        # Fuzzy match: the plan may use a slightly different name
                        # than the one actually passed to integrations.create
                        _integ_lower = integration_id.lower().strip()
                        _resolved = False
                        for _name, _id in newly_created_integrations.items():
                            if _name.lower().strip() == _integ_lower:
                                logger.info(f"  [execute]   🔌 Resolved newly created integration '{integration_id}' → ID {_id} (case-insensitive match)")
                                parameters["integration_id"] = _id
                                _resolved = True
                                break

                        # Fallback: if there's exactly ONE newly created integration and we
                        # couldn't resolve by name, just use that integration ID
                        # (the LLM often uses different naming between steps)
                        if not _resolved and len(newly_created_integrations) == 1:
                            _only_id = list(newly_created_integrations.values())[0]
                            _only_name = list(newly_created_integrations.keys())[0]
                            logger.info(f"  [execute]   🔌 Resolved newly created integration '{integration_id}' → ID {_only_id} "
                                       f"(fallback: only one integration '{_only_name}' was created)")
                            parameters["integration_id"] = _only_id

                # Handle missing integration_id: if integration_id is not provided at all,
                # check if we created exactly one integration in this execution and inject it
                elif not integration_id and newly_created_integrations:
                    # Actions that commonly need integration_id but might not have it in parameters
                    integration_required_actions = {
                        "integrations.test",
                        "integrations.update",
                        "integrations.delete",
                        "integrations.list_operations",
                        "integrations.execute_operation",
                    }
                    if capability_id in integration_required_actions and len(newly_created_integrations) == 1:
                        _only_id = list(newly_created_integrations.values())[0]
                        _only_name = list(newly_created_integrations.keys())[0]
                        logger.info(f"  [execute]   🔌 Injected missing integration_id → ID {_only_id} "
                                   f"(from newly created integration '{_only_name}')")
                        parameters["integration_id"] = _only_id

                # ── Cross-step resolution: inject newly created workflow IDs ──
                # If a previous step created/saved a workflow and this step references
                # that workflow by name, replace the name with the numeric ID.
                #
                # ALSO: scan previous step results directly as a fallback.
                # This handles cases where the tracking dict wasn't populated
                # (e.g., delegation results with saved_workflow_id).
                if not newly_created_workflows:
                    for prev_step in updated_steps:
                        # Check for workflow creation via API
                        if (prev_step.get("domain") == "workflows"
                                and prev_step.get("action") == "create"
                                and prev_step.get("status") == "completed"):
                            prev_result = prev_step.get("result", {})
                            prev_data = prev_result.get("data", {})
                            prev_id = None
                            if isinstance(prev_data, dict):
                                if "workflow_id" in prev_data:
                                    try:
                                        prev_id = int(prev_data["workflow_id"])
                                    except (ValueError, TypeError):
                                        pass
                                elif "response" in prev_data:
                                    try:
                                        prev_id = int(prev_data["response"])
                                    except (ValueError, TypeError):
                                        pass
                                elif "database_version" in prev_data:
                                    try:
                                        prev_id = int(prev_data["database_version"])
                                    except (ValueError, TypeError):
                                        pass
                            if prev_id:
                                prev_params = prev_step.get("parameters_used") or prev_step.get("parameters", {})
                                prev_name = prev_params.get("workflow_name") or prev_params.get("name") or f"workflow_{prev_id}"
                                newly_created_workflows[prev_name] = prev_id
                                logger.info(f"  [execute]   📋 Recovered workflow from previous step result: '{prev_name}' → ID {prev_id}")

                        # Check for workflow saved via delegation (agent:workflow_agent)
                        elif (prev_step.get("domain") == "agent"
                                and prev_step.get("status") == "completed"):
                            prev_result = prev_step.get("result", {})
                            saved_wf_id = prev_result.get("saved_workflow_id")
                            saved_wf_name = prev_result.get("saved_workflow_name")
                            if saved_wf_id and saved_wf_name:
                                try:
                                    prev_id = int(saved_wf_id)
                                    newly_created_workflows[saved_wf_name] = prev_id
                                    logger.info(f"  [execute]   📋 Recovered workflow from delegation result: '{saved_wf_name}' → ID {prev_id}")
                                except (ValueError, TypeError):
                                    pass

                workflow_id = parameters.get("workflow_id")
                if workflow_id and isinstance(workflow_id, str) and not workflow_id.isdigit():
                    logger.info(f"  [execute]   🔍 Cross-step resolution: workflow_id='{workflow_id}', "
                               f"newly_created_workflows={newly_created_workflows}")

                    # Check if this workflow name was just created (exact match)
                    if workflow_id in newly_created_workflows:
                        resolved_id = newly_created_workflows[workflow_id]
                        logger.info(f"  [execute]   📋 Resolved newly created workflow '{workflow_id}' → ID {resolved_id} (exact match)")
                        parameters["workflow_id"] = resolved_id
                    else:
                        # Fuzzy match: the plan may use a slightly different name
                        _wf_lower = workflow_id.lower().strip()
                        _resolved = False
                        for _name, _id in newly_created_workflows.items():
                            if _name.lower().strip() == _wf_lower:
                                logger.info(f"  [execute]   📋 Resolved newly created workflow '{workflow_id}' → ID {_id} (case-insensitive match)")
                                parameters["workflow_id"] = _id
                                _resolved = True
                                break

                        # Fallback: if there's exactly ONE newly created workflow and we
                        # couldn't resolve by name, just use that workflow ID
                        if not _resolved and len(newly_created_workflows) == 1:
                            _only_id = list(newly_created_workflows.values())[0]
                            _only_name = list(newly_created_workflows.keys())[0]
                            logger.info(f"  [execute]   📋 Resolved newly created workflow '{workflow_id}' → ID {_only_id} "
                                       f"(fallback: only one workflow '{_only_name}' was created)")
                            parameters["workflow_id"] = _only_id

                # Handle missing workflow_id: if workflow_id is not provided at all,
                # check if we created exactly one workflow in this execution and inject it
                elif not workflow_id and newly_created_workflows:
                    # Actions that commonly need workflow_id but might not have it in parameters
                    workflow_required_actions = {
                        "schedules.create",
                        "schedules.update",
                        "schedules.delete",
                        "schedules.get",
                        "workflows.update",
                        "workflows.delete",
                        "workflows.execute",
                    }
                    if capability_id in workflow_required_actions and len(newly_created_workflows) == 1:
                        _only_id = list(newly_created_workflows.values())[0]
                        _only_name = list(newly_created_workflows.keys())[0]
                        logger.info(f"  [execute]   📋 Injected missing workflow_id → ID {_only_id} "
                                   f"(from newly created workflow '{_only_name}')")
                        parameters["workflow_id"] = _only_id

                # Resolve reference parameters (e.g., workflow/agent/connection name → numeric ID)
                # This MUST run BEFORE validation so names are resolved to IDs first.
                if action_def:
                    parameters = await _resolve_reference_parameters(action_def, parameters)

                # Pre-check: validate connection_id for data agent creation
                # Runs AFTER all resolution attempts so we're checking the final resolved value.
                if capability_id == "agents.create_data_agent" and system_context:
                    conn_id = parameters.get("connection_id")
                    if conn_id and hasattr(system_context, "connections"):
                        available = {str(c.get("id", "")) for c in (system_context.connections or [])}
                        available |= {c.get("name", "") for c in (system_context.connections or [])}
                        # Also include newly created connections from this execution
                        available |= {str(v) for v in newly_created_connections.values()}
                        available |= set(newly_created_connections.keys())
                        if str(conn_id) not in available:
                            conn_names = [f"{c.get('name', '?')} (ID: {c.get('id', '?')})" for c in (system_context.connections or [])]
                            logger.warning(f"  [execute]   ⚠ Connection '{conn_id}' not found")
                            updated_step = {
                                **step,
                                "status": "failed",
                                "result": {
                                    "status": "failed",
                                    "message": f"Connection '{conn_id}' not found. Available connections: {', '.join(conn_names) if conn_names else 'none'}.",
                                    "error": f"Invalid connection_id: {conn_id}",
                                },
                            }
                            updated_steps.append(updated_step)
                            continue

                # ── Normalize file parameter names ──────────────────────
                # The LLM often extracts the parameter as "file_id" but the
                # action definition expects "file" (FieldType.FILE).  Map it.
                if "file_id" in parameters and "file" not in parameters:
                    if action_def:
                        file_fields = {f.name for f in action_def.primary_route.input_fields
                                       if hasattr(f, 'field_type') and f.field_type.value == "file"}
                        if "file" in file_fields:
                            parameters["file"] = parameters.pop("file_id")
                            logger.info(f"  [execute]   📎 Mapped file_id → file: {parameters['file']}")

                logger.info(f"  [execute]   Parameters: {parameters}")

                # Execute the step
                t_step = time.time()
                result = await executor.execute_step(
                    domain=domain,
                    action=action,
                    parameters=parameters,
                    description=description,
                )
                step_duration_ms = int((time.time() - t_step) * 1000)

                # ═══════════════════════════════════════════════════════════
                # SELF-HEALING: Correction loop for failed steps
                # ═══════════════════════════════════════════════════════════
                correction_applied = None
                if not result.is_success and _resilience_available:
                    logger.info(f"  [execute]   ⚡ Self-healing: analyzing failure...")

                    # Record the initial failure
                    outcome_tracker.record(ExecutionOutcome(
                        session_id=state.get("session_id", ""),
                        plan_id=plan.get("plan_id", ""),
                        step_id=step.get("id", ""),
                        capability_id=capability_id,
                        domain=domain,
                        action=action,
                        parameters=parameters,
                        status="failed",
                        http_status=getattr(result, "http_status", None),
                        error=result.error,
                        duration_ms=step_duration_ms,
                        user_goal=plan.get("goal", ""),
                    ))

                    # Analyze the failure
                    _http_status = getattr(result, "http_status", None)
                    analysis = failure_analyzer.analyze(
                        http_status=_http_status,
                        error_message=result.error or "",
                        response_data=None,
                        capability_id=capability_id,
                        parameters=parameters,
                    )
                    logger.info(f"  [execute]   ⚡ Failure category: {analysis.category.value}, "
                                f"auto_fixable={analysis.auto_fixable}, confidence={analysis.confidence}")

                    # Attempt correction if feasible
                    correction_attempts = 0
                    max_corrections = 2
                    while analysis.auto_fixable and correction_attempts < max_corrections:
                        correction_attempts += 1
                        strategy_name = analysis.suggested_strategies[0] if analysis.suggested_strategies else None
                        if not strategy_name:
                            break

                        logger.info(f"  [execute]   🔧 Correction attempt {correction_attempts}: {strategy_name}")
                        correction_result = await correction_engine.attempt_correction(
                            step=step,
                            failure_analysis=analysis,
                            system_context=system_context,
                            attempt_number=correction_attempts,
                        )

                        correction_history.append({
                            "step_id": step.get("id", ""),
                            "strategy": strategy_name,
                            "success": correction_result.success,
                            "message": correction_result.message,
                        })

                        if correction_result.success and correction_result.new_parameters:
                            # Correction produced new parameters — re-execute the step
                            corrected_params = correction_result.new_parameters
                            logger.info(f"  [execute]   🔧 Re-executing with corrected params: {corrected_params}")
                            result = await executor.execute_step(
                                domain=domain,
                                action=action,
                                parameters=corrected_params,
                                description=description,
                            )
                            if result.is_success:
                                correction_applied = strategy_name
                                logger.info(f"  [execute]   ✓ Self-healed via {strategy_name}")

                                # Record the successful correction
                                outcome_tracker.record(ExecutionOutcome(
                                    session_id=state.get("session_id", ""),
                                    plan_id=plan.get("plan_id", ""),
                                    step_id=step.get("id", ""),
                                    capability_id=capability_id,
                                    domain=domain,
                                    action=action,
                                    parameters=corrected_params,
                                    status="corrected",
                                    correction_applied=strategy_name,
                                    correction_result="success",
                                    duration_ms=step_duration_ms,
                                    user_goal=plan.get("goal", ""),
                                ))

                                # Record workaround for future reference
                                learning_memory.record_workaround(
                                    capability_id=capability_id,
                                    original_error=analysis.root_cause,
                                    workaround=f"Auto-corrected via {strategy_name}",
                                )
                                break
                            else:
                                logger.warning(f"  [execute]   ✗ Re-execution still failed: {result.error}")
                        elif correction_result.user_question:
                            # Correction needs user input — can't auto-fix further
                            logger.info(f"  [execute]   💬 Correction needs user input: {correction_result.user_question[:100]}")
                            break
                        else:
                            logger.warning(f"  [execute]   ✗ Correction failed: {correction_result.message}")

                        # Re-analyze for next attempt
                        if correction_attempts < max_corrections and not correction_applied:
                            analysis = failure_analyzer.analyze(
                                http_status=_http_status,
                                error_message=correction_result.message or result.error or "",
                                response_data=None,
                                capability_id=capability_id,
                                parameters=correction_result.new_parameters or parameters,
                            )

                # Record outcome for successful steps (skip if already recorded by correction loop)
                if result.is_success and _resilience_available and not correction_applied:
                    outcome_tracker.record(ExecutionOutcome(
                        session_id=state.get("session_id", ""),
                        plan_id=plan.get("plan_id", ""),
                        step_id=step.get("id", ""),
                        capability_id=capability_id,
                        domain=domain,
                        action=action,
                        parameters=parameters,
                        status="success",
                        duration_ms=step_duration_ms,
                        user_goal=plan.get("goal", ""),
                    ))
                    learning_memory.record_success(
                        capability_id=capability_id,
                        user_intent=description,
                        approach=f"Successful {capability_id}",
                        parameters_used=parameters,
                    )

                # Record persistent failures to learning memory so future plans
                # can avoid the same pitfalls
                if not result.is_success and _resilience_available and not correction_applied:
                    try:
                        _fail_category = "unknown"
                        _fail_root_cause = result.error or "Unknown error"
                        # analysis is set during the self-healing block above
                        if analysis is not None:
                            _fail_category = analysis.category.value if hasattr(analysis.category, 'value') else str(analysis.category)
                            _fail_root_cause = analysis.root_cause or _fail_root_cause
                        learning_memory.record_failure(
                            capability_id=capability_id,
                            error_category=_fail_category,
                            root_cause=_fail_root_cause,
                        )
                    except (NameError, Exception):
                        pass  # analysis may not be defined if resilience init failed

                execution_results.append(result)
                updated_step = {
                    **step,
                    "status": "completed" if result.is_success else "failed",
                    "result": result.to_dict(),
                    "parameters_used": parameters,  # Store actual parameters used (for cross-step resolution)
                }

                # Add self-healing metadata if correction was applied
                if correction_applied:
                    updated_step["self_healed"] = True
                    updated_step["correction_strategy"] = correction_applied

                if result.is_success:
                    if correction_applied:
                        logger.info(f"  [execute]   ✓ Success (🔧 self-healed via {correction_applied})")
                    else:
                        logger.info(f"  [execute]   ✓ Success")

                    # ── Track newly created connections for cross-step resolution ──
                    if capability_id == "connections.create":
                        try:
                            # Extract connection ID from result
                            # Response format: {"status": "success", "response": "85"}
                            new_conn_id = None
                            if result.data and "response" in result.data:
                                new_conn_id = int(result.data["response"])
                            elif result.message and result.message.isdigit():
                                new_conn_id = int(result.message)

                            logger.info(f"  [execute]   🔍 Connection tracking: extracted new_conn_id={new_conn_id}, "
                                       f"parameters keys={list(parameters.keys())}")

                            if new_conn_id:
                                # Get connection name from parameters
                                conn_name = parameters.get("name") or parameters.get("connection_name")
                                logger.info(f"  [execute]   🔍 Connection tracking: conn_name='{conn_name}' "
                                           f"(from parameters['name']='{parameters.get('name')}' or "
                                           f"parameters['connection_name']='{parameters.get('connection_name')}')")
                                if conn_name:
                                    newly_created_connections[conn_name] = new_conn_id
                                    logger.info(f"  [execute]   🔗 Tracked new connection '{conn_name}' → ID {new_conn_id}")

                                    # Update system_context.connections so subsequent validation passes
                                    if system_context and hasattr(system_context, "connections"):
                                        new_conn = {
                                            "id": new_conn_id,
                                            "connection_id": new_conn_id,
                                            "name": conn_name,
                                            "connection_name": conn_name,
                                            # Include other fields from parameters for completeness
                                            **{k: v for k, v in parameters.items() if k not in ["connection_id", "name"]}
                                        }
                                        if system_context.connections is None:
                                            system_context.connections = []
                                        system_context.connections.append(new_conn)
                                        logger.info(f"  [execute]   🔗 Added connection to system_context (total: {len(system_context.connections)})")
                                else:
                                    logger.warning(f"  [execute]   ⚠️  Could not track connection ID {new_conn_id} - no 'name' or 'connection_name' in parameters")
                        except (ValueError, KeyError, AttributeError) as e:
                            logger.warning(f"  [execute]   Could not track new connection: {e}")

                    # ── Track newly created agents for cross-step resolution ──
                    if capability_id == "agents.create" or capability_id == "agents.create_data_agent":
                        try:
                            # Extract agent ID from result
                            # Response format varies:
                            # - agents.create: {"status": "success", "data": {"response": "360"}}
                            # - or: {"status": "success", "data": {"agent_id": 360}}
                            new_agent_id = None
                            if result.data:
                                if "response" in result.data:
                                    try:
                                        new_agent_id = int(result.data["response"])
                                    except (ValueError, TypeError):
                                        pass
                                elif "agent_id" in result.data:
                                    try:
                                        new_agent_id = int(result.data["agent_id"])
                                    except (ValueError, TypeError):
                                        pass

                            logger.info(f"  [execute]   🔍 Agent tracking: extracted new_agent_id={new_agent_id}, "
                                       f"parameters keys={list(parameters.keys())}")

                            if new_agent_id:
                                # Get agent name from parameters
                                # Try both agent_description (common) and name
                                agent_name = parameters.get("agent_description") or parameters.get("name") or parameters.get("agent_name")
                                logger.info(f"  [execute]   🔍 Agent tracking: agent_name='{agent_name}' "
                                           f"(from parameters['agent_description']='{parameters.get('agent_description')}' or "
                                           f"parameters['name']='{parameters.get('name')}' or "
                                           f"parameters['agent_name']='{parameters.get('agent_name')}')")
                                if agent_name:
                                    newly_created_agents[agent_name] = new_agent_id
                                    logger.info(f"  [execute]   🤖 Tracked new agent '{agent_name}' → ID {new_agent_id}")

                                    # Update system_context.agents so subsequent validation and resolution work
                                    if system_context and hasattr(system_context, "agents"):
                                        new_agent = {
                                            "id": new_agent_id,
                                            "agent_id": new_agent_id,
                                            "name": agent_name,
                                            "description": agent_name,
                                            "agent_description": agent_name,
                                            # Include other fields from parameters for completeness
                                            **{k: v for k, v in parameters.items() if k not in ["agent_id", "id"]}
                                        }
                                        if system_context.agents is None:
                                            system_context.agents = []
                                        system_context.agents.append(new_agent)
                                        logger.info(f"  [execute]   🤖 Added agent to system_context (total: {len(system_context.agents)})")
                                else:
                                    logger.warning(f"  [execute]   ⚠️  Could not track agent ID {new_agent_id} - no 'agent_description', 'name', or 'agent_name' in parameters")
                        except (ValueError, KeyError, AttributeError) as e:
                            logger.warning(f"  [execute]   Could not track new agent: {e}")

                    # ── Track newly created workflows for cross-step resolution ──
                    if capability_id == "workflows.create":
                        try:
                            # Extract workflow ID from result
                            # Response format: {"status": "success", "data": {"workflow_id": 123}}
                            # or: {"status": "success", "data": {"response": "123"}}
                            new_workflow_id = None
                            if result.data:
                                if "workflow_id" in result.data:
                                    try:
                                        new_workflow_id = int(result.data["workflow_id"])
                                    except (ValueError, TypeError):
                                        pass
                                elif "response" in result.data:
                                    try:
                                        new_workflow_id = int(result.data["response"])
                                    except (ValueError, TypeError):
                                        pass
                                # Also check for database_version (alternate field name)
                                elif "database_version" in result.data:
                                    try:
                                        new_workflow_id = int(result.data["database_version"])
                                    except (ValueError, TypeError):
                                        pass

                            logger.info(f"  [execute]   🔍 Workflow tracking: extracted new_workflow_id={new_workflow_id}, "
                                       f"parameters keys={list(parameters.keys())}")

                            if new_workflow_id:
                                # Get workflow name from parameters
                                workflow_name = parameters.get("workflow_name") or parameters.get("name")
                                logger.info(f"  [execute]   🔍 Workflow tracking: workflow_name='{workflow_name}' "
                                           f"(from parameters['workflow_name']='{parameters.get('workflow_name')}' or "
                                           f"parameters['name']='{parameters.get('name')}')")
                                if workflow_name:
                                    newly_created_workflows[workflow_name] = new_workflow_id
                                    logger.info(f"  [execute]   📋 Tracked new workflow '{workflow_name}' → ID {new_workflow_id}")

                                    # Update system_context if available
                                    if system_context and hasattr(system_context, "workflows"):
                                        new_workflow = {
                                            "id": new_workflow_id,
                                            "workflow_id": new_workflow_id,
                                            "name": workflow_name,
                                            "workflow_name": workflow_name,
                                        }
                                        if system_context.workflows is None:
                                            system_context.workflows = []
                                        system_context.workflows.append(new_workflow)
                                        logger.info(f"  [execute]   📋 Added workflow to system_context (total: {len(system_context.workflows)})")
                                else:
                                    logger.warning(f"  [execute]   ⚠️  Could not track workflow ID {new_workflow_id} - no 'workflow_name' or 'name' in parameters")
                        except (ValueError, KeyError, AttributeError) as e:
                            logger.warning(f"  [execute]   Could not track new workflow: {e}")

                    # ── Track newly created integrations for cross-step resolution ──
                    if capability_id == "integrations.create":
                        try:
                            # Extract integration ID from result
                            # Response format:
                            # - integrations.create: {"status": "success", "data": {"integration_id": 123}}
                            # or: {"status": "success", "data": {"response": "123"}}
                            new_integration_id = None
                            if result.data:
                                if "integration_id" in result.data:
                                    try:
                                        new_integration_id = int(result.data["integration_id"])
                                    except (ValueError, TypeError):
                                        pass
                                elif "response" in result.data:
                                    try:
                                        new_integration_id = int(result.data["response"])
                                    except (ValueError, TypeError):
                                        pass
                            elif result.message and result.message.isdigit():
                                new_integration_id = int(result.message)

                            logger.info(f"  [execute]   🔍 Integration tracking: extracted new_integration_id={new_integration_id}, "
                                       f"parameters keys={list(parameters.keys())}")

                            if new_integration_id:
                                # Get integration name from parameters
                                integration_name = parameters.get("integration_name") or parameters.get("name")
                                logger.info(f"  [execute]   🔍 Integration tracking: integration_name='{integration_name}' "
                                           f"(from parameters['integration_name']='{parameters.get('integration_name')}' or "
                                           f"parameters['name']='{parameters.get('name')}')")
                                if integration_name:
                                    newly_created_integrations[integration_name] = new_integration_id
                                    logger.info(f"  [execute]   🔌 Tracked new integration '{integration_name}' → ID {new_integration_id}")

                                    # Update system_context.integrations so subsequent validation passes
                                    if system_context and hasattr(system_context, "integrations"):
                                        new_integration = {
                                            "id": new_integration_id,
                                            "integration_id": new_integration_id,
                                            "name": integration_name,
                                            "integration_name": integration_name,
                                            # Include other fields from parameters for completeness
                                            **{k: v for k, v in parameters.items() if k not in ["integration_id", "name"]}
                                        }
                                        if system_context.integrations is None:
                                            system_context.integrations = []
                                        system_context.integrations.append(new_integration)
                                        logger.info(f"  [execute]   🔌 Added integration to system_context (total: {len(system_context.integrations)})")
                                else:
                                    logger.warning(f"  [execute]   ⚠️  Could not track integration ID {new_integration_id} - no 'integration_name' or 'name' in parameters")
                        except (ValueError, KeyError, AttributeError) as e:
                            logger.warning(f"  [execute]   Could not track new integration: {e}")
                else:
                    logger.warning(f"  [execute]   ✗ Failed: {result.error}")

            updated_steps.append(updated_step)

    # Update plan with results
    completed_count = sum(1 for s in updated_steps if s["status"] == "completed")
    delegated_count = sum(1 for s in updated_steps if s["status"] == "delegated")
    awaiting_count = sum(1 for s in updated_steps if s["status"] == "awaiting_input")
    failed_count = sum(1 for s in updated_steps if s["status"] == "failed")
    skipped_count = sum(1 for s in updated_steps if s["status"] == "skipped")

    # Determine final status
    # - "completed" if all steps are completed
    # - "awaiting_agent_input" if any steps are waiting for user to respond to agent
    # - "delegated" if any steps were delegated (agent conversations started, no questions pending)
    # - "partial" if some completed, some failed
    # - "skipped" if all skipped
    # - "failed" if all failed
    if awaiting_count > 0:
        final_status = "awaiting_agent_input"
    elif completed_count + delegated_count == len(updated_steps):
        final_status = "completed" if delegated_count == 0 else "delegated"
    elif completed_count + delegated_count > 0:
        final_status = "partial"
    elif skipped_count > 0:
        final_status = "skipped"
    else:
        final_status = "failed"

    updated_plan = {
        **plan,
        "status": final_status,
        "steps": updated_steps,
    }

    self_healed_count = sum(1 for s in updated_steps if s.get("self_healed"))
    logger.info(f"  [execute] ════════════════════════════════════════")
    logger.info(f"  [execute] EXECUTION COMPLETE: {completed_count} completed, {delegated_count} delegated, {awaiting_count} awaiting input, {failed_count} failed, {self_healed_count} self-healed")
    logger.info(f"  [execute] Final status: {final_status}")
    if pending_agent_question:
        logger.info(f"  [execute] Pending agent question: {pending_agent_question[:100]}...")
    logger.info(f"  [execute] ════════════════════════════════════════")

    # Generate response summarizing what happened
    llm = get_llm(mini=False)

    results_summary = []
    for step in updated_steps:
        step_status = step.get("status", "")
        if step_status == "completed" and step.get("self_healed"):
            status_icon = "🔧"  # Wrench for self-healed steps
        elif step_status == "completed":
            status_icon = "✅"
        elif step_status == "delegated":
            status_icon = "🤖"
        elif step_status == "awaiting_input":
            status_icon = "💬"  # Speech bubble for awaiting user input
        elif step_status == "failed":
            status_icon = "❌"
        elif step_status == "permission_denied":
            status_icon = "🔒"
        elif step_status in ("", "pending") or step_status is None:
            status_icon = "⏳"  # Pending — not yet executed
        else:
            status_icon = "⚠️"

        result_msg = ""
        if step_status == "permission_denied":
            result_msg = f" — {step.get('permission_note', 'Insufficient permissions')}"
        elif step_status in ("", "pending") or step_status is None:
            result_msg = " — waiting (will run after agent conversation completes)"
        elif step.get("result"):
            if step["result"].get("error"):
                result_msg = f" — {step['result']['error']}"
            elif step["result"].get("workflow_commands"):
                # WorkflowAgent returned build commands
                cmd_count = len(step["result"]["workflow_commands"].get("commands", []))
                phase = step["result"].get("workflow_phase", "building")
                result_msg = f" — Workflow commands generated ({cmd_count} commands, phase: {phase})"
            elif step["result"].get("needs_user_input"):
                # Agent is asking questions - show a truncated version
                agent_resp = step["result"].get("agent_response", "")[:150]
                result_msg = f" — Agent needs more info"
            elif step["result"].get("agent_response"):
                # Agent delegation response (may be asking questions or confirming)
                agent_resp = step["result"].get("agent_response", "")[:100]
                result_msg = f" — Agent: {agent_resp}..."
            elif step["result"].get("data"):
                data = step["result"]["data"]
                if isinstance(data, dict) and "response" in data:
                    # agents.chat or similar — show actual response content
                    response_text = str(data["response"])[:500]
                    result_msg = f" — Agent responded: {response_text}"
                elif isinstance(data, dict):
                    # Other data — show compact key-value summary (skip noise fields)
                    data_summary = ", ".join(
                        f"{k}={repr(v)[:80]}" for k, v in data.items()
                        if k not in ("raw", "chat_history")
                    )
                    result_msg = f" — {step['result'].get('message', 'Done')}" + (f" ({data_summary})" if data_summary else "")
                else:
                    result_msg = f" — {step['result'].get('message', 'Done')}"
        results_summary.append(f"{status_icon} **Step {step['order']}:** {step['description']}{result_msg}")

    results_text = "\n".join(results_summary)

    if final_status == "completed":
        summary_prompt = f"""The plan executed successfully. Summarize what was accomplished:

{results_text}

Be brief and positive. Mention any IDs or resources created. Suggest logical next steps."""
    elif final_status == "awaiting_agent_input":
        # Agent is asking questions - display the question prominently
        summary_prompt = f"""A specialized agent needs more information to proceed.

{results_text}

The agent is asking:

---
{pending_agent_question}
---

Present this question to the user clearly. They should respond to the agent's question to continue.
DO NOT add your own questions - just relay the agent's question and wait for the user's response."""
    elif final_status == "delegated":
        summary_prompt = f"""The plan included agent delegations. Summarize:

{results_text}

Explain that specialized agents have taken over certain tasks and may ask follow-up questions.
If there are pending questions from agents, mention them."""
    elif final_status == "skipped":
        summary_prompt = f"""The plan could NOT be executed because the action registry is not loaded. This is a server configuration issue.

{results_text}

Tell the user: "I wasn't able to execute the plan because the Builder service's action registry failed to load. This is a server-side configuration issue that needs to be fixed before I can create resources on the platform."

Do NOT say the actions were successful. Be clear that nothing was actually created."""
    else:
        summary_prompt = f"""The plan had some issues. Briefly summarize:

{results_text}

In 2-3 sentences: say what succeeded, what failed, and one concrete next step the user can take.
Keep it short and actionable — no detailed failure analysis."""

    response = await safe_llm_invoke(llm, [
        SystemMessage(content=BUILDER_SYSTEM_PROMPT + "\n\n" + STRUCTURED_RESPONSE_FORMAT),
        HumanMessage(content=summary_prompt),
    ])

    # Build result with agent conversation state if any delegations occurred
    result = {
        "messages": [response],
        "current_plan": updated_plan,
    }

    if new_agent_conversations != agent_conversations:
        result["agent_conversations"] = new_agent_conversations

    if current_agent_conversation_id:
        result["current_agent_conversation_id"] = current_agent_conversation_id

    if pending_agent_question:
        result["pending_agent_question"] = pending_agent_question

    if workflow_edit_context:
        result["workflow_edit_context"] = workflow_edit_context

    if correction_history:
        result["correction_history"] = correction_history

    # ─── Resource Registry: track created resource IDs across turns ──────
    # Extract IDs from execution results and merge with prior resources
    try:
        prior_resources = state.get("created_resources") or {}
        new_resources = dict(prior_resources)  # shallow copy

        # Resource ID keys we look for in step result data, mapped to registry category
        _RESOURCE_KEYS = {
            "connection_id": "connections",
            "agent_id": "agents",
            "tool_id": "tools",
            "tool_name": "tools",
            "knowledge_id": "knowledge",
            "workflow_id": "workflows",
            "saved_workflow_id": "workflows",
            "saved_workflow_name": "workflows",
            "schedule_id": "schedules",
            "email_address": "email",
        }

        for step in updated_plan.steps:
            step_result = step.get("result") if isinstance(step, dict) else getattr(step, "result", None)
            if not step_result:
                continue
            data = step_result.get("data", {}) if isinstance(step_result, dict) else {}
            if not isinstance(data, dict):
                continue

            for key, category in _RESOURCE_KEYS.items():
                if key in data and data[key]:
                    if category not in new_resources:
                        new_resources[category] = []
                    # Avoid duplicates
                    value = data[key]
                    entry = {"id": value} if key.endswith("_id") else {"name": value}
                    # Merge name info if available
                    if key == "connection_id" and "connection_name" in data:
                        entry["name"] = data["connection_name"]
                    elif key == "agent_id" and "agent_description" in data:
                        entry["name"] = data["agent_description"]
                    elif key == "schedule_id" and "schedule_cron" in data:
                        entry["cron"] = data["schedule_cron"]

                    # Don't add if this exact entry already exists
                    if entry not in new_resources[category]:
                        new_resources[category].append(entry)

        if new_resources:
            result["created_resources"] = new_resources
            logger.info(f"  [execute] Resource registry updated: {new_resources}")
    except Exception as e:
        logger.warning(f"  [execute] Failed to update resource registry: {e}")

    return result


# ─── Agent Response Type Detection ─────────────────────────────────────────
# Uses LLM to accurately detect whether an agent's response is asking questions
# or providing a definitive result. This is more reliable than hardcoded phrase
# matching since agents can phrase questions in many different ways.

AGENT_RESPONSE_TYPE_PROMPT = """Analyze this response from a specialized agent and determine its type.

Agent's response:
---
{response_text}
---

Additional context:
- Has structured output (like workflow commands): {has_structured_output}
- Workflow phase (if applicable): {workflow_phase}

Classify this response as ONE of:
- "asking_questions" - Agent is asking the user for more information, clarification, or preferences
- "definitive_result" - Agent has completed the task and is presenting the final result
- "providing_update" - Agent is giving a status update but conversation may continue

Consider:
- Questions may be phrased as "What would you like...?", "Could you...?", "Which...?", etc.
- Questions may also be indirect: "I need to know...", "Please provide...", "Let me know..."
- A definitive result includes completed work, created resources, or final summaries
- If unsure, prefer "asking_questions" to ensure user gets a chance to respond

Respond with ONLY one of: asking_questions, definitive_result, providing_update"""


async def _detect_agent_response_type(
    response_text: str,
    has_structured_output: bool = False,
    workflow_phase: str | None = None,
) -> str:
    """
    Use LLM to detect whether an agent's response is asking questions or providing results.

    This replaces hardcoded phrase matching with LLM reasoning for better accuracy.
    The mini model is used for speed and cost efficiency.

    Args:
        response_text: The agent's response text
        has_structured_output: Whether the agent produced structured output (commands, etc.)
        workflow_phase: The current workflow phase if applicable

    Returns:
        One of: "asking_questions", "definitive_result", "providing_update"
    """
    # Quick check: if there's structured output, it's likely a definitive result
    if has_structured_output:
        return "definitive_result"

    # Empty or very short responses are likely updates
    if not response_text or len(response_text.strip()) < 20:
        return "providing_update"

    try:
        t0 = time.time()
        llm = get_llm(mini=True, streaming=False)

        prompt = AGENT_RESPONSE_TYPE_PROMPT.format(
            response_text=response_text[:2000],  # Limit for efficiency
            has_structured_output=has_structured_output,
            workflow_phase=workflow_phase or "N/A",
        )

        result = await safe_llm_invoke(llm, [
            SystemMessage(content="You analyze agent responses to classify their intent. Respond with only the classification."),
            HumanMessage(content=prompt),
        ])

        response_type = result.content.strip().lower().replace('"', '').replace("'", "")
        elapsed = time.time() - t0

        valid_types = {"asking_questions", "definitive_result", "providing_update"}
        if response_type not in valid_types:
            logger.warning(f"  [_detect_agent_response_type] Unknown type '{response_type}', defaulting to 'asking_questions' ({elapsed:.1f}s)")
            response_type = "asking_questions"
        else:
            logger.info(f"  [_detect_agent_response_type] Detected: '{response_type}' ({elapsed:.1f}s)")

        return response_type

    except Exception as e:
        logger.error(f"  [_detect_agent_response_type] LLM detection failed: {e}")
        # Default to asking_questions to ensure user gets a chance to respond
        return "asking_questions"


# ─── Agent Delegation Helper ─────────────────────────────────────────────

async def _execute_agent_delegation(
    agent_id: str,
    task_description: str,
    parameters: dict,
    session_id: str | None,
    workflow_edit_context: dict | None = None,
    skip_builder_delegation: bool = False,
) -> dict:
    """
    Execute an agent delegation step.

    This starts a conversation with the specified agent and sends the task.
    Returns the agent's initial response and conversation state.

    For the WorkflowAgent, the response may include workflow_commands which
    are extracted and returned separately for the canvas to execute.

    For workflow edits, this function will:
    1. Detect if the user wants to edit an existing workflow
    2. Discover the workflow by name via GET /get/workflows
    3. Load the full workflow state via GET /get/workflow/<id>
    4. Pass the workflow_state to the WorkflowAgent's /guide endpoint
       to trigger REFINEMENT mode

    IMPORTANT: If the agent responds with questions (no workflow_commands),
    we mark the conversation as needing user input and set pending_question
    so the Builder can relay the question back to the user.
    """
    try:
        from agent_communication.manager import get_communication_manager
        from agent_communication.adapters import parse_workflow_metadata

        manager = get_communication_manager()

        # ═══════════════════════════════════════════════════════════════
        # WORKFLOW EDIT DETECTION (WorkflowAgent only)
        # ═══════════════════════════════════════════════════════════════
        workflow_state_for_agent = None
        edit_workflow_id = None
        edit_workflow_name = None

        if agent_id == "workflow_agent":
            # Check if we already have edit context from a previous detection
            if workflow_edit_context:
                edit_workflow_id = workflow_edit_context.get("workflow_id")
                edit_workflow_name = workflow_edit_context.get("workflow_name")
                workflow_state_for_agent = workflow_edit_context.get("workflow_state")
                logger.info(f"  [_execute_agent_delegation] Using pre-loaded edit context: workflow_id={edit_workflow_id}, name='{edit_workflow_name}'")
            else:
                # Try to detect edit intent from the task description
                edit_context = await _detect_and_load_workflow_for_edit(task_description, parameters)
                if edit_context:
                    edit_workflow_id = edit_context["workflow_id"]
                    edit_workflow_name = edit_context["workflow_name"]
                    workflow_state_for_agent = edit_context["workflow_state"]
                    logger.info(f"  [_execute_agent_delegation] Detected edit intent: workflow_id={edit_workflow_id}, name='{edit_workflow_name}'")

        # Start a conversation with the agent
        conversation = await manager.start_conversation(
            agent_id=agent_id,
            initial_message=task_description,
            context={
                "task_description": task_description,
                "parameters": parameters,
                "session_id": session_id,
            },
            task_summary=task_description[:100],
        )

        logger.info(f"  [_execute_agent_delegation] Started conversation {conversation.id} with {conversation.agent_name}")

        # For workflow edits, we need to pass workflow_state via the adapter's kwargs
        # The WorkflowBuilderAdapter will include it in the /guide request payload
        send_kwargs = {}
        if agent_id == "workflow_agent" and not skip_builder_delegation:
            send_kwargs["is_builder_delegation"] = True
            logger.info(f"  [_execute_agent_delegation] Builder delegation mode enabled for workflow_agent")
        elif agent_id == "workflow_agent" and skip_builder_delegation:
            logger.info(f"  [_execute_agent_delegation] Builder delegation skipped — using conversational mode")
        if workflow_state_for_agent:
            send_kwargs["workflow_state"] = workflow_state_for_agent
            logger.info(f"  [_execute_agent_delegation] Passing workflow_state to agent (nodes={len(workflow_state_for_agent.get('nodes', []))})")

        # Collect the agent's response
        response_chunks = []
        async for chunk in manager.send_message(conversation.id, task_description, **send_kwargs):
            response_chunks.append(chunk)

        full_response = "".join(response_chunks)
        logger.info(f"  [_execute_agent_delegation] Agent responded: {len(full_response)} chars")

        # Detect known agent fallback/error responses (content filter, LLM errors)
        _AGENT_FALLBACK_MARKERS = [
            "having trouble processing that request",
            "content filtering or message complexity",
            "encountered an issue processing your request",
        ]
        if any(marker in full_response.lower() for marker in _AGENT_FALLBACK_MARKERS):
            logger.warning(f"  [_execute_agent_delegation] Detected fallback/error response from agent")
            return {
                "success": False,
                "agent_id": agent_id,
                "error": f"Agent returned an error: {full_response[:200]}",
                "is_content_filter": True,
            }

        # Check for workflow metadata (WorkflowAgent responses)
        response_text, workflow_metadata = parse_workflow_metadata(full_response)
        workflow_commands = None
        workflow_phase = None

        if workflow_metadata and workflow_metadata.get("__workflow_metadata__"):
            workflow_commands = workflow_metadata.get("workflow_commands")
            workflow_phase = workflow_metadata.get("phase")
            if workflow_commands:
                cmd_count = len(workflow_commands.get("commands", []))
                logger.info(f"  [_execute_agent_delegation] Extracted {cmd_count} workflow commands (phase: {workflow_phase})")

        # ─── Workflow delegation completion check ────────────────────────
        # When the WorkflowAgent returns a plan (phase=planning) but no build
        # commands, we need to send a follow-up message to trigger the actual
        # command generation.  The WorkflowAgent's _auto_update_phase() is
        # skipped for builder delegations, so it won't auto-transition from
        # PLANNING → BUILDING.  We send explicit "build it" language that
        # matches the phase-transition trigger words in _auto_update_phase().
        logger.info(f"  [_execute_agent_delegation] Build follow-up check: agent_id={agent_id}, "
                     f"workflow_phase={workflow_phase}, workflow_commands={workflow_commands is not None}, "
                     f"workflow_metadata={workflow_metadata is not None}, "
                     f"has_plan={workflow_metadata.get('workflow_plan') is not None if workflow_metadata else 'N/A'}")
        if (
            agent_id == "workflow_agent"
            and workflow_phase == "planning"
            and not workflow_commands
            and workflow_metadata
            and workflow_metadata.get("workflow_plan")
        ):
            logger.info("  [_execute_agent_delegation] WorkflowAgent returned plan only (phase=planning, no commands). "
                         "Sending follow-up message to trigger build.")
            build_message = (
                "The workflow plan looks good. Now please build this workflow — "
                "call generate_workflow_commands to create the build commands for the plan you just created."
            )
            build_chunks = []
            async for chunk in manager.send_message(conversation.id, build_message, **send_kwargs):
                build_chunks.append(chunk)

            build_response = "".join(build_chunks)
            logger.info(f"  [_execute_agent_delegation] Build follow-up response: {len(build_response)} chars")

            # Re-parse the build response for workflow metadata
            response_text_build, build_metadata = parse_workflow_metadata(build_response)
            if build_metadata and build_metadata.get("__workflow_metadata__"):
                build_commands = build_metadata.get("workflow_commands")
                build_phase = build_metadata.get("phase")
                if build_commands:
                    workflow_commands = build_commands
                    workflow_phase = build_phase
                    cmd_count = len(workflow_commands.get("commands", []))
                    logger.info(f"  [_execute_agent_delegation] Build follow-up produced {cmd_count} commands (phase: {build_phase})")
                    # Append the build response to the main response for the user
                    response_text = response_text + "\n\n" + response_text_build
                else:
                    logger.warning("  [_execute_agent_delegation] Build follow-up still produced no commands")
            else:
                logger.warning("  [_execute_agent_delegation] Build follow-up had no workflow metadata")

        # Get updated conversation state
        updated_conversation = manager.get_conversation(conversation.id)

        # Determine if the agent is asking for more information
        # If no workflow_commands and response looks like a question, mark as waiting for user
        pending_question = updated_conversation.pending_question
        conversation_status = updated_conversation.status.value

        # Use LLM to detect if the agent is asking questions or providing a result
        # This is more accurate than hardcoded phrase matching
        has_structured_output = workflow_commands is not None
        response_type = await _detect_agent_response_type(
            response_text=response_text,
            has_structured_output=has_structured_output,
            workflow_phase=workflow_phase,
        )

        is_asking_questions = response_type == "asking_questions"
        is_update = response_type == "providing_update"

        logger.info(f"  [_execute_agent_delegation] Response type detection: type={response_type}, workflow_commands={has_structured_output}, workflow_phase={workflow_phase}")

        if is_asking_questions and not pending_question:
            # Agent is asking questions - set the response as the pending question
            pending_question = response_text
            conversation_status = "waiting_for_user"
            logger.info(f"  [_execute_agent_delegation] Agent is asking questions, marking conversation as waiting_for_user")
            # Update the conversation status in the manager
            manager.mark_waiting_for_user(conversation.id, response_text)
        elif is_update and not pending_question:
            # Agent is providing an update — conversation is still active but the
            # agent didn't explicitly ask a question. Keep it active so the router
            # knows to forward the next user message to the agent.
            conversation_status = "active"
            logger.info(f"  [_execute_agent_delegation] Agent providing update, conversation remains active")
        elif response_type == "definitive_result" or has_structured_output:
            # Guard: for workflow_agent in planning phase with no plan/commands,
            # the agent is still gathering requirements — not providing a result.
            is_wf_still_gathering = (
                agent_id == "workflow_agent"
                and workflow_phase == "planning"
                and not workflow_commands
                and (not workflow_metadata or not workflow_metadata.get("workflow_plan"))
            )
            if is_wf_still_gathering:
                # The workflow agent is asking for details.  Rather than waiting
                # for the user (who already provided everything in the builder
                # plan), auto-reply with the full task description + parameters
                # so the agent has all the context it needs.
                logger.info(f"  [_execute_agent_delegation] WorkflowAgent asking for details — auto-replying with full context")
                # Include the user's original goal (from the plan) which contains
                # ALL the details they provided — not just the summarized step description
                user_goal = parameters.get("_user_goal", "") or task_description
                auto_reply = (
                    f"Here are all the details you need:\n\n{user_goal}\n\n"
                    f"Step instructions: {task_description}\n\n"
                    f"Parameters: {json.dumps(parameters, indent=2) if parameters else 'None'}\n\n"
                    f"Please generate the workflow plan now using ALL of the information above."
                )
                retry_chunks = []
                async for chunk in manager.send_message(conversation.id, auto_reply, **send_kwargs):
                    retry_chunks.append(chunk)
                retry_response = "".join(retry_chunks)
                logger.info(f"  [_execute_agent_delegation] Auto-reply response: {len(retry_response)} chars")
                
                # Re-parse for workflow metadata
                response_text_retry, retry_metadata = parse_workflow_metadata(retry_response)
                if retry_metadata and retry_metadata.get("__workflow_metadata__"):
                    retry_commands = retry_metadata.get("workflow_commands")
                    retry_phase = retry_metadata.get("phase")
                    retry_plan = retry_metadata.get("workflow_plan")
                    if retry_commands:
                        workflow_commands = retry_commands
                        workflow_phase = retry_phase
                        has_structured_output = True
                        response_text = response_text + "\n\n" + response_text_retry
                        logger.info(f"  [_execute_agent_delegation] Auto-reply produced commands!")
                    elif retry_plan:
                        # Got a plan — now trigger build
                        workflow_metadata = retry_metadata
                        workflow_phase = retry_phase
                        response_text = response_text_retry
                        logger.info(f"  [_execute_agent_delegation] Auto-reply produced plan — will trigger build")
                        # Fall through to the build follow-up check below
                    else:
                        # Still no plan — mark as waiting for user
                        pending_question = response_text_retry
                        conversation_status = "waiting_for_user"
                        logger.info(f"  [_execute_agent_delegation] Auto-reply still no plan — waiting for user")
                        manager.mark_waiting_for_user(conversation.id, response_text_retry)
                else:
                    pending_question = response_text
                    conversation_status = "waiting_for_user"
                    logger.info(f"  [_execute_agent_delegation] Auto-reply had no metadata — waiting for user")
                    manager.mark_waiting_for_user(conversation.id, response_text)
                
                # Re-check for build follow-up if we got a plan
                if (workflow_phase == "planning" and not workflow_commands 
                    and workflow_metadata and workflow_metadata.get("workflow_plan")):
                    logger.info("  [_execute_agent_delegation] Auto-reply produced plan — sending build follow-up")
                    build_message = (
                        "The workflow plan looks good. Now please build this workflow — "
                        "call generate_workflow_commands to create the build commands for the plan you just created."
                    )
                    build_chunks = []
                    async for chunk in manager.send_message(conversation.id, build_message, **send_kwargs):
                        build_chunks.append(chunk)
                    build_response = "".join(build_chunks)
                    logger.info(f"  [_execute_agent_delegation] Build follow-up after auto-reply: {len(build_response)} chars")
                    
                    response_text_build, build_metadata = parse_workflow_metadata(build_response)
                    if build_metadata and build_metadata.get("__workflow_metadata__"):
                        build_commands = build_metadata.get("workflow_commands")
                        if build_commands:
                            workflow_commands = build_commands
                            workflow_phase = build_metadata.get("phase")
                            has_structured_output = True
                            response_text = response_text + "\n\n" + response_text_build
                            cmd_count = len(workflow_commands.get("commands", []))
                            logger.info(f"  [_execute_agent_delegation] Build after auto-reply produced {cmd_count} commands")
                            # Mark as completed now
                            conversation_status = "completed"
                            updated_conversation = manager.get_conversation(conversation.id)
                            updated_conversation.mark_completed(response_text)
                            logger.info(f"  [_execute_agent_delegation] Workflow built successfully via auto-reply path")
            else:
                # Agent has provided a definitive result (e.g., workflow commands generated,
                # task completed). Mark the conversation as completed.
                conversation_status = "completed"
                updated_conversation.mark_completed(response_text)
                logger.info(f"  [_execute_agent_delegation] Agent provided definitive result, marking conversation as completed")

        # ─── Compile workflow if we have a plan ─────────────────────────
        # The compile step creates/updates the workflow in the platform.
        # This must happen in _execute_agent_delegation too (not just in
        # handle_agent_response) because the workflow agent may return a
        # complete plan+commands on the very first message.
        compile_result = None
        if (workflow_metadata and workflow_metadata.get("workflow_plan")
                and agent_id == "workflow_agent"):
            compile_result_data = await _handle_workflow_agent_metadata(
                agent_metadata=workflow_metadata,
                response_text=response_text,
                current_conv_id=conversation.id,
                logger=logger,
                workflow_edit_context=(
                    {"workflow_id": edit_workflow_id, "workflow_name": edit_workflow_name}
                    if edit_workflow_id else None
                ),
            )
            if compile_result_data:
                response_text = compile_result_data.get("response_text", response_text)
                compile_result = compile_result_data.get("compile_result")
                if compile_result and compile_result.get("status") == "success":
                    has_structured_output = True
                    conversation_status = "completed"
                    updated_conversation = manager.get_conversation(conversation.id)
                    updated_conversation.mark_completed(response_text)
                    logger.info(f"  [_execute_agent_delegation] Workflow compiled successfully in initial delegation")

        # The agent needs user input if it's asking questions OR providing an update
        # (both mean the conversation is still ongoing and the user should respond)
        conversation_needs_input = is_asking_questions or is_update

        # Convert messages to simple dicts for JSON serialization
        messages_for_state = [
            {"role": msg.role, "content": msg.content}
            for msg in updated_conversation.messages
        ]

        conv_state = {
            "id": updated_conversation.id,
            "agent_id": updated_conversation.agent_id,
            "agent_name": updated_conversation.agent_name,
            "status": conversation_status,
            "task_summary": updated_conversation.task_summary,
            "pending_question": pending_question,
            "result": updated_conversation.result,
            "message_count": len(updated_conversation.messages),
            "messages": messages_for_state,
        }

        logger.info(f"  [_execute_agent_delegation] Conv state: status={conversation_status}, messages={len(messages_for_state)}")

        result = {
            "success": True,
            "agent_id": agent_id,
            "agent_name": updated_conversation.agent_name,
            "conversation_id": conversation.id,
            "agent_response": response_text,  # Clean response without metadata
            "conversation_state": conv_state,
            "needs_user_input": conversation_needs_input,  # True if agent needs more info
        }

        # Add workflow-specific data if present
        if workflow_commands:
            result["workflow_commands"] = workflow_commands
            result["workflow_phase"] = workflow_phase
        if compile_result:
            result["compile_result"] = compile_result

        # Add workflow edit context if this is an edit operation
        if edit_workflow_id is not None:
            result["workflow_edit_context"] = {
                "workflow_id": edit_workflow_id,
                "workflow_name": edit_workflow_name,
            }

        return result

    except Exception as e:
        logger.error(f"  [_execute_agent_delegation] Error: {e}", exc_info=True)
        return {
            "success": False,
            "agent_id": agent_id,
            "error": str(e),
        }


# ─── Workflow Edit Detection ─────────────────────────────────────────────

DETECT_EDIT_INTENT_PROMPT = """Analyze this task description and determine if the user wants to EDIT an existing workflow or CREATE a new one.

Task description:
---
{task_description}
---

Additional parameters: {parameters}

Respond with a JSON object:
- "intent": "edit" or "create"
- "workflow_name": the name of the workflow to edit (null if creating new)

Signs of EDIT intent:
- Words like "edit", "modify", "update", "change", "fix", "add to", "remove from", "adjust"
- References to a specific existing workflow by name
- Descriptions of changes to something that already exists

Signs of CREATE intent:
- Words like "create", "build", "new", "make", "set up"
- No reference to an existing workflow
- Describing something from scratch

If the user references a workflow name, extract it as accurately as possible.

Respond with ONLY valid JSON, no other text."""


async def _detect_and_load_workflow_for_edit(
    task_description: str,
    parameters: dict,
) -> dict | None:
    """
    Detect if the user wants to edit an existing workflow and load it.

    This function:
    1. Uses LLM to detect edit intent and extract workflow name
    2. Calls GET /get/workflows to find the workflow
    3. Calls GET /get/workflow/<id> to load the full state
    4. Returns the workflow edit context or None

    Returns:
        Dict with {workflow_id, workflow_name, workflow_state} or None
    """
    # Step 1: Detect edit intent via LLM
    try:
        llm = get_llm(mini=True, streaming=False)
        prompt = DETECT_EDIT_INTENT_PROMPT.format(
            task_description=task_description,
            parameters=json.dumps(parameters) if parameters else "{}",
        )

        response = await safe_llm_invoke(llm, [
            SystemMessage(content="You analyze user intent. Respond with only valid JSON."),
            HumanMessage(content=prompt),
        ])

        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]

        result = json.loads(raw)
        intent = result.get("intent", "create")
        workflow_name = result.get("workflow_name")

        logger.info(f"  [_detect_and_load_workflow_for_edit] Detected intent={intent}, workflow_name={workflow_name}")

        if intent != "edit" or not workflow_name:
            return None

    except Exception as e:
        logger.warning(f"  [_detect_and_load_workflow_for_edit] Intent detection failed: {e}")
        return None

    # Step 2: Find the workflow by name
    try:
        from agent_communication.adapters.workflow_builder import WorkflowBuilderAdapter

        adapter = WorkflowBuilderAdapter()
        try:
            matching_workflow = await adapter.find_workflow_by_name(workflow_name)

            if not matching_workflow:
                logger.info(f"  [_detect_and_load_workflow_for_edit] No workflow found matching '{workflow_name}'")
                return None

            workflow_id = matching_workflow.get("id")
            actual_name = matching_workflow.get("workflow_name", workflow_name)

            # Step 3: Load the full workflow state
            workflow_state = await adapter.get_workflow(workflow_id)

            if not workflow_state:
                logger.warning(f"  [_detect_and_load_workflow_for_edit] Failed to load workflow {workflow_id}")
                return None

            # Add the name to the state (the GET endpoint doesn't include it)
            workflow_state["name"] = actual_name

            logger.info(f"  [_detect_and_load_workflow_for_edit] Loaded workflow '{actual_name}' (ID={workflow_id}): "
                        f"{len(workflow_state.get('nodes', []))} nodes, {len(workflow_state.get('connections', []))} connections")

            return {
                "workflow_id": workflow_id,
                "workflow_name": actual_name,
                "workflow_state": workflow_state,
            }

        finally:
            await adapter.close()

    except Exception as e:
        logger.error(f"  [_detect_and_load_workflow_for_edit] Error discovering/loading workflow: {e}", exc_info=True)
        return None


# ─── Handle Rejection ────────────────────────────────────────────────────

async def handle_rejection(state: dict) -> dict:
    """Handle when the user rejects or wants to modify a plan."""
    messages = state.get("messages", [])
    logger.info("  [handle_rejection] User wants to modify/reject the plan")

    llm = get_llm(mini=False)

    rejection_prompt = f"""{BUILDER_SYSTEM_PROMPT}

The user wants to modify or reject the proposed plan. Respond helpfully:
- If they want changes, acknowledge what they want different and propose a revised plan
- If they want to cancel entirely, acknowledge and ask what they'd like to do instead
- Stay constructive and solution-oriented

{STRUCTURED_RESPONSE_FORMAT}"""

    full_messages = [SystemMessage(content=rejection_prompt)] + messages
    response = await safe_llm_invoke(llm, full_messages)

    logger.info("  [handle_rejection] Plan cleared, new response generated")

    return {
        "messages": [response],
        "current_plan": None,
    }


# ═══════════════════════════════════════════════════════════════════════════
# AGENT COMMUNICATION NODES
# ═══════════════════════════════════════════════════════════════════════════
# Note: Agent delegation now happens inside the execute node via _execute_agent_delegation.
# These nodes handle the agent conversation lifecycle after initial delegation.

async def delegate_to_agent(state: dict) -> dict:
    """
    Delegate the current task to a specialized agent and manage the conversation.

    This node:
    1. Starts a conversation with the target agent
    2. Sends the user's request
    3. Streams the agent's response
    4. Handles follow-up questions if the agent needs more info
    """
    messages = state.get("messages", [])
    delegation_target = state.get("delegation_target")
    agent_conversations = state.get("agent_conversations", {})

    if not delegation_target:
        logger.warning("  [delegate_to_agent] No delegation target specified")
        return {"should_delegate": False}

    latest_message = messages[-1].content if hasattr(messages[-1], "content") else str(messages[-1])
    logger.info(f"  [delegate_to_agent] Delegating to {delegation_target}: '{latest_message[:60]}...'")

    try:
        # Import the communication manager
        from agent_communication.manager import get_communication_manager, AgentCommunicationManager

        manager = get_communication_manager()

        # Start a new conversation with the agent
        conversation = await manager.start_conversation(
            agent_id=delegation_target,
            initial_message=latest_message,
            context={
                "user_request": latest_message,
                "session_id": state.get("session_id"),
            },
            task_summary=latest_message[:100],
        )

        logger.info(f"  [delegate_to_agent] Started conversation {conversation.id} with {conversation.agent_name}")

        # Collect the agent's response
        response_chunks = []
        async for chunk in manager.send_message(conversation.id, latest_message):
            response_chunks.append(chunk)

        full_response = "".join(response_chunks)
        logger.info(f"  [delegate_to_agent] Agent responded: {len(full_response)} chars")

        # Update conversation state for graph
        updated_conversation = manager.get_conversation(conversation.id)
        conv_state = {
            "id": updated_conversation.id,
            "agent_id": updated_conversation.agent_id,
            "agent_name": updated_conversation.agent_name,
            "status": updated_conversation.status.value,
            "task_summary": updated_conversation.task_summary,
            "pending_question": updated_conversation.pending_question,
            "result": updated_conversation.result,
            "message_count": len(updated_conversation.messages),
        }

        # Store in agent_conversations dict
        new_agent_conversations = {**agent_conversations, conversation.id: conv_state}

        # Check if agent is waiting for user input
        pending_question = None
        if updated_conversation.status.value == "waiting_for_user":
            pending_question = updated_conversation.pending_question
            logger.info(f"  [delegate_to_agent] Agent waiting for user: {pending_question}")

        # Create AI message with the agent's response
        from langchain_core.messages import AIMessage
        agent_response = AIMessage(content=full_response)

        return {
            "messages": [agent_response],
            "agent_conversations": new_agent_conversations,
            "current_agent_conversation_id": conversation.id,
            "pending_agent_question": pending_question,
            "should_delegate": False,  # Reset for next turn
            "delegation_target": None,
        }

    except Exception as e:
        logger.error(f"  [delegate_to_agent] Delegation failed: {e}", exc_info=True)

        # Generate error response
        llm = get_llm(mini=False)
        error_response = await safe_llm_invoke(llm, [
            SystemMessage(content=BUILDER_SYSTEM_PROMPT),
            HumanMessage(content=f"""I tried to delegate your request to a specialized agent but encountered an error:
{str(e)}

Please try rephrasing your request or I can try to help you directly.

Original request: {latest_message}"""),
        ])

        return {
            "messages": [error_response],
            "should_delegate": False,
            "delegation_target": None,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT-SPECIFIC METADATA HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════
# These handlers process structured metadata from specific agent types.
# Each handler is called only when the appropriate metadata type is detected.
# This keeps agent-specific logic isolated and the main handler generic.


async def _handle_workflow_agent_metadata(
    agent_metadata: dict,
    response_text: str,
    current_conv_id: str,
    logger,
    workflow_edit_context: dict | None = None,
) -> dict | None:
    """
    Handle WorkflowAgent-specific metadata.

    This function processes workflow_plan and workflow_commands from the
    WorkflowAgent's response. If a workflow_plan is present, it calls the
    /compile endpoint to create or edit the workflow.

    When workflow_edit_context is provided (containing workflow_id and workflow_name),
    the compile call will operate in edit mode, applying changes on top of the
    existing workflow rather than creating a new one.

    Args:
        agent_metadata: The parsed metadata from the agent's response
        response_text: The text portion of the agent's response
        current_conv_id: The current conversation ID
        logger: Logger instance
        workflow_edit_context: Optional dict with {workflow_id, workflow_name} for edit mode

    Returns:
        Dict with results, or None if no special handling was needed:
        - response_text: Updated response text (if modified)
        - workflow_commands: Extracted workflow commands (if any)
        - workflow_plan: The workflow plan (if any)
        - workflow_phase: Current phase
        - compile_result: Result from compile (if called)
    """
    workflow_commands = agent_metadata.get("workflow_commands")
    workflow_plan = agent_metadata.get("workflow_plan")
    workflow_phase = agent_metadata.get("phase")
    compile_result = None

    if workflow_commands:
        cmd_count = len(workflow_commands.get("commands", []))
        logger.info(f"  [_handle_workflow_agent_metadata] Extracted {cmd_count} workflow commands (phase: {workflow_phase})")

    # If we have a workflow_plan, compile it to create/edit the workflow.
    # Note: The WorkflowAgent may return BOTH workflow_commands (for the frontend canvas)
    # and workflow_plan (for the compile endpoint). In the builder service context, we
    # always use the /compile endpoint, so we compile whenever a plan is available,
    # regardless of whether commands were also returned.
    if workflow_plan:
        # Determine if this is an edit or create operation
        edit_workflow_id = None
        if workflow_edit_context:
            edit_workflow_id = workflow_edit_context.get("workflow_id")

        mode = "edit" if edit_workflow_id else "create"
        logger.info(f"  [_handle_workflow_agent_metadata] Detected workflow_plan - calling /compile to {mode} workflow"
                     f"{f' (ID={edit_workflow_id})' if edit_workflow_id else ''}")

        from agent_communication.adapters.workflow_builder import WorkflowBuilderAdapter

        adapter = WorkflowBuilderAdapter()

        # Extract workflow name: prefer edit context name, then requirements, then default
        requirements = agent_metadata.get("requirements", {})
        if workflow_edit_context and workflow_edit_context.get("workflow_name"):
            workflow_name = workflow_edit_context["workflow_name"]
        else:
            workflow_name = requirements.get("process_name") or "New Workflow"

        try:
            compile_result = await adapter.compile_workflow(
                endpoint="/api/workflow/builder",
                workflow_plan=workflow_plan,
                workflow_name=workflow_name,
                requirements=requirements,
                workflow_id=edit_workflow_id,
                save=True,
                max_fix_attempts=2,
                timeout=180,
            )

            if compile_result.get("status") == "success":
                result_workflow_id = compile_result.get("workflow_id")
                node_count = compile_result.get("node_count", 0)
                result_mode = compile_result.get("mode", mode)
                logger.info(f"  [_handle_workflow_agent_metadata] Workflow {result_mode}d successfully! ID={result_workflow_id}, nodes={node_count}")

                if result_mode == "edit":
                    response_text = f"""{response_text}

**Workflow Updated Successfully!**
- **Name:** {workflow_name}
- **ID:** {result_workflow_id}
- **Nodes:** {node_count}
- **Connections:** {compile_result.get('connection_count', 0)}

The workflow has been updated and saved."""
                else:
                    response_text = f"""{response_text}

**Workflow Created Successfully!**
- **Name:** {workflow_name}
- **ID:** {result_workflow_id}
- **Nodes:** {node_count}
- **Connections:** {compile_result.get('connection_count', 0)}

The workflow has been saved and is ready to use."""
            else:
                error = compile_result.get("error", "Unknown error")
                action_verb = "update" if edit_workflow_id else "create"
                logger.warning(f"  [_handle_workflow_agent_metadata] Workflow compile failed: {error}")
                response_text = f"""{response_text}

**Note:** I tried to {action_verb} the workflow but encountered an issue: {error}

You may need to refine the requirements or try again."""

            # Clean up the session after compile
            session_id = agent_metadata.get("session_id")
            if session_id:
                await adapter.clear_session("/api/workflow/builder", current_conv_id)

        except Exception as e:
            action_verb = "update" if edit_workflow_id else "create"
            logger.error(f"  [_handle_workflow_agent_metadata] Error calling compile: {e}", exc_info=True)
            response_text = f"""{response_text}

**Note:** I tried to {action_verb} the workflow but encountered an error: {str(e)}"""

        finally:
            await adapter.close()

    # Return results if we did anything
    if workflow_commands or workflow_plan or compile_result:
        return {
            "response_text": response_text,
            "workflow_commands": workflow_commands,
            "workflow_plan": workflow_plan,
            "workflow_phase": workflow_phase,
            "compile_result": compile_result,
        }

    return None


async def handle_agent_response(state: dict) -> dict:
    """
    Handle a user's response to an agent's question.

    When an agent asks for clarification, this node forwards the user's
    response back to the agent and continues the conversation.

    IMPORTANT: This node uses the LLM to format and stream the agent's response
    to the user. This ensures tokens are streamed properly to the frontend.
    """
    messages = state.get("messages", [])
    current_conv_id = state.get("current_agent_conversation_id")
    agent_conversations = state.get("agent_conversations", {})

    if not current_conv_id:
        logger.warning("  [handle_agent_response] No active agent conversation")
        # Use LLM to generate a response explaining there's no active conversation
        llm = get_llm(mini=False)
        response = await safe_llm_invoke(llm, [
            SystemMessage(content=BUILDER_SYSTEM_PROMPT),
            HumanMessage(content="I don't have an active conversation with any specialist agent. What would you like me to help you with?"),
        ])
        return {"messages": [response], "pending_agent_question": None}

    latest_message = messages[-1].content if hasattr(messages[-1], "content") else str(messages[-1])
    logger.info(f"  [handle_agent_response] Forwarding response to conversation {current_conv_id}")

    try:
        from agent_communication.manager import get_communication_manager
        from agent_communication.adapters import parse_workflow_metadata

        manager = get_communication_manager()

        # Get the current conversation to check agent type
        current_conversation = manager.get_conversation(current_conv_id)
        agent_id = current_conversation.agent_id if current_conversation else None

        # Forward the user's response to the agent
        # Note: We send the message as-is - agent-specific enhancements should be
        # handled by the adapter, not here in the generic handler
        response_chunks = []
        async for chunk in manager.provide_user_input(current_conv_id, latest_message):
            response_chunks.append(chunk)

        full_response = "".join(response_chunks)
        logger.info(f"  [handle_agent_response] Agent responded: {len(full_response)} chars")

        # Check for structured metadata in the response
        # This is protocol-agnostic - any adapter can append metadata in this format
        response_text, agent_metadata = parse_workflow_metadata(full_response)

        # Initialize agent-specific result tracking
        agent_result = None  # For any structured result from the agent

        # Handle agent-specific metadata if present
        # The metadata format is generic, but certain fields trigger special handling
        if agent_metadata and agent_metadata.get("__workflow_metadata__"):
            # This is a WorkflowAgent response - handle workflow-specific logic
            workflow_edit_context = state.get("workflow_edit_context")
            agent_result = await _handle_workflow_agent_metadata(
                agent_metadata=agent_metadata,
                response_text=response_text,
                current_conv_id=current_conv_id,
                logger=logger,
                workflow_edit_context=workflow_edit_context,
            )
            if agent_result:
                # Update response_text if the handler modified it
                response_text = agent_result.get("response_text", response_text)

        # Get updated conversation state
        updated_conversation = manager.get_conversation(current_conv_id)

        # Determine if the agent is still asking for more information
        pending_question = updated_conversation.pending_question
        conversation_status = updated_conversation.status.value

        # Check if the agent produced a definitive result (meaning conversation is complete)
        # This is agent-agnostic - we check if there's structured output from the agent.
        #
        # For WorkflowAgent: The conversation is ONLY complete when a workflow has been
        # successfully compiled (compile_result with status=success). Workflow commands
        # alone are intermediate — the <workflow_plan> must be extracted and compiled
        # before the workflow actually exists in the platform.
        has_definitive_result = False
        if agent_result is not None:
            compile_result = agent_result.get("compile_result")
            if compile_result and compile_result.get("status") == "success":
                # Workflow was actually created/updated in the platform
                has_definitive_result = True
            # Add other agent result checks here as needed
            # (e.g., for data_agent, report_agent, etc.)

        # Use LLM to detect if the agent is asking questions or providing a result
        # This is more accurate than hardcoded phrase matching
        workflow_phase = agent_metadata.get("phase") if agent_metadata else None
        response_type = await _detect_agent_response_type(
            response_text=response_text,
            has_structured_output=has_definitive_result,
            workflow_phase=workflow_phase,
        )

        is_asking_questions = response_type == "asking_questions"
        is_definitive = response_type == "definitive_result"
        is_update = response_type == "providing_update"

        logger.info(f"  [handle_agent_response] Response type detection: type={response_type}, has_definitive_result={has_definitive_result}")

        if is_asking_questions and not pending_question:
            # Agent is asking more questions - set the response as the pending question
            pending_question = response_text
            conversation_status = "waiting_for_user"
            logger.info(f"  [handle_agent_response] Agent is asking more questions")
            # Update the conversation status in the manager
            manager.mark_waiting_for_user(current_conv_id, response_text)
        elif is_update and not pending_question:
            # Agent is providing an update — conversation is still active but the agent
            # didn't explicitly ask a question. Keep the conversation active so the router
            # knows to forward the next user message to the agent.
            conversation_status = "active"
            logger.info(f"  [handle_agent_response] Agent providing update, conversation remains active")

        # Check if conversation is complete
        current_conv = current_conv_id  # Default: keep conversation active

        if updated_conversation.status.value == "completed" or has_definitive_result:
            # Conversation is done (status set or we got a definitive result)
            current_conv = None
            pending_question = None
            conversation_status = "completed"
            if has_definitive_result:
                logger.info(f"  [handle_agent_response] Conversation completed (agent produced definitive result)")
            else:
                logger.info(f"  [handle_agent_response] Conversation completed (status=completed)")
        elif is_definitive and not is_asking_questions:
            # LLM detected this as a definitive result — but for WorkflowAgent,
            # a plan-only response (phase="planning" with no compile_result) is NOT
            # definitive. The conversation must continue to generate_workflow_commands.
            is_workflow_planning_only = (
                agent_id == "workflow_agent"
                and workflow_phase == "planning"
                and not has_definitive_result  # no compile_result
            )
            if is_workflow_planning_only:
                # Keep conversation active — the workflow agent still needs to
                # generate commands and compile the workflow
                conversation_status = "active"
                logger.info(f"  [handle_agent_response] WorkflowAgent in planning phase — keeping conversation active (not marking as complete)")
            else:
                # LLM detected this as a definitive result - consider the conversation complete
                current_conv = None
                pending_question = None
                conversation_status = "completed"
                logger.info(f"  [handle_agent_response] Marking conversation as complete (LLM detected definitive result)")

        # Convert messages to simple dicts for JSON serialization
        messages_for_state = [
            {"role": msg.role, "content": msg.content}
            for msg in updated_conversation.messages
        ]

        conv_state = {
            "id": updated_conversation.id,
            "agent_id": updated_conversation.agent_id,
            "agent_name": updated_conversation.agent_name,
            "status": conversation_status,
            "task_summary": updated_conversation.task_summary,
            "pending_question": pending_question,
            "result": updated_conversation.result,
            "message_count": len(updated_conversation.messages),
            "messages": messages_for_state,
        }

        logger.info(f"  [handle_agent_response] Conv state updated: status={conversation_status}, messages={len(messages_for_state)}")

        new_agent_conversations = {**agent_conversations, current_conv_id: conv_state}

        # Get agent name for context
        agent_name = updated_conversation.agent_name or "Specialist Agent"

        # Use LLM to format and stream the response to the user
        # This ensures tokens are properly streamed to the frontend
        llm = get_llm(mini=False)

        if is_asking_questions:
            # Agent is asking more questions - relay as-is without LLM commentary
            format_prompt = f"""Relay this from the {agent_name} to the user.
Output the agent's message below as-is, only lightly formatting for clarity.
Do NOT add commentary, do NOT rephrase, do NOT add your own questions.

{response_text}"""

        elif agent_result and agent_result.get("compile_result"):
            # WorkflowAgent-specific: A workflow was compiled (created or edited)
            compile_result = agent_result["compile_result"]
            compile_mode = compile_result.get("mode", "create")
            is_edit = compile_mode == "edit"
            if compile_result.get("status") == "success":
                workflow_id = compile_result.get("workflow_id")
                workflow_name = compile_result.get("workflow_name", "New Workflow")
                node_count = compile_result.get("node_count", 0)
                connection_count = compile_result.get("connection_count", 0)

                if is_edit:
                    format_prompt = f"""The {agent_name} has modified your workflow and I've saved the changes!

**Workflow Updated Successfully!**
- **Name:** {workflow_name}
- **Workflow ID:** {workflow_id}
- **Nodes:** {node_count}
- **Connections:** {connection_count}

The workflow has been updated and saved. You can find it in the Workflows section.

Let the user know their workflow has been updated and offer to help with anything else."""
                else:
                    format_prompt = f"""The {agent_name} has designed your workflow and I've created it for you!

**Workflow Created Successfully!**
- **Name:** {workflow_name}
- **Workflow ID:** {workflow_id}
- **Nodes:** {node_count}
- **Connections:** {connection_count}

The workflow has been saved to the platform and is ready to use. You can find it in the Workflows section.

Let the user know their workflow is ready and offer to help with anything else."""
            else:
                error = compile_result.get("error", "Unknown error")
                action_verb = "updating" if is_edit else "creating"
                format_prompt = f"""The {agent_name} designed a workflow plan, but there was an issue {action_verb} it:

Error: {error}

Let the user know about the issue and suggest they may need to refine the requirements or try again."""

        elif agent_result and agent_result.get("workflow_commands"):
            # WorkflowAgent-specific: Workflow commands were produced but NOT compiled yet.
            # This means the agent is still working — it needs a workflow_plan to compile.
            workflow_commands = agent_result["workflow_commands"]
            cmd_count = len(workflow_commands.get("commands", []))
            format_prompt = f"""The {agent_name} is making progress and has generated {cmd_count} workflow components.

Agent's response:
---
{response_text}
---

Present this update to the user. The workflow is still being designed — the agent may need
additional input or confirmation before the final workflow can be created.
If the agent is asking questions or requesting confirmation, relay that to the user."""

        elif has_definitive_result:
            # Agent produced some definitive result - present it
            format_prompt = f"""The {agent_name} has completed the task.

Agent's response:
---
{response_text}
---

Summarize what was accomplished and offer to help with anything else."""

        else:
            # General agent response - works for any agent type
            format_prompt = f"""The {agent_name} has responded:

---
{response_text}
---

Present this response to the user in a helpful way."""

        response = await safe_llm_invoke(llm, [
            SystemMessage(content=BUILDER_SYSTEM_PROMPT + "\n\n" + STRUCTURED_RESPONSE_FORMAT),
            HumanMessage(content=format_prompt),
        ])

        result = {
            "messages": [response],
            "agent_conversations": new_agent_conversations,
            "current_agent_conversation_id": current_conv,
            "pending_agent_question": pending_question,
        }

        # Add agent-specific results if present
        # This keeps the result extensible for different agent types
        if agent_result:
            # WorkflowAgent-specific fields
            if agent_result.get("workflow_commands"):
                result["workflow_commands"] = agent_result["workflow_commands"]
                result["workflow_phase"] = agent_result.get("workflow_phase")
            if agent_result.get("compile_result"):
                result["compile_result"] = agent_result["compile_result"]
            # Future: Add other agent-specific result fields here

        # ═══════════════════════════════════════════════════════════════════════
        # UPDATE PLAN STATUS when agent conversation completes
        # ═══════════════════════════════════════════════════════════════════════
        # If the conversation is now completed, we need to update the plan step
        # that was awaiting input to reflect the new status
        logger.info(f"  [handle_agent_response] Checking plan update: conversation_status={conversation_status}, current_conv_id={current_conv_id}")

        if conversation_status == "completed":
            plan = state.get("current_plan")
            logger.info(f"  [handle_agent_response] Plan exists: {plan is not None}, has steps: {bool(plan and plan.get('steps'))}")

            if plan and plan.get("steps"):
                updated_steps = []
                for step in plan["steps"]:
                    # Find the step that was delegated to this agent
                    step_result = step.get("result", {})
                    step_conv_id = step_result.get("conversation_id")
                    step_status = step.get("status")

                    logger.info(f"  [handle_agent_response] Step {step.get('order')}: status={step_status}, conv_id={step_conv_id}, target_conv_id={current_conv_id}, match={step_conv_id == current_conv_id}")

                    if (step_status in ("awaiting_input", "delegated") and step_conv_id == current_conv_id):
                        # Update this step to completed
                        logger.info(f"  [handle_agent_response] ✓ Updating step {step.get('order')} to completed")
                        updated_step = {
                            **step,
                            "status": "completed",
                        }
                        updated_steps.append(updated_step)
                    else:
                        updated_steps.append(step)

                # Recalculate plan status
                completed_count = sum(1 for s in updated_steps if s["status"] == "completed")
                delegated_count = sum(1 for s in updated_steps if s["status"] == "delegated")
                awaiting_count = sum(1 for s in updated_steps if s["status"] == "awaiting_input")
                failed_count = sum(1 for s in updated_steps if s["status"] == "failed")
                pending_count = sum(1 for s in updated_steps if s.get("status") in (None, "", "pending"))

                logger.info(f"  [handle_agent_response] Step counts: completed={completed_count}, delegated={delegated_count}, awaiting={awaiting_count}, failed={failed_count}, pending={pending_count}")

                if awaiting_count > 0:
                    plan_status = "awaiting_agent_input"
                elif pending_count > 0:
                    # Steps still waiting to execute — plan should resume
                    plan_status = "executing"
                elif completed_count + delegated_count == len(updated_steps):
                    plan_status = "completed" if delegated_count == 0 else "delegated"
                elif completed_count + delegated_count > 0:
                    plan_status = "partial"
                elif failed_count > 0:
                    plan_status = "failed"
                else:
                    plan_status = plan.get("status", "completed")

                updated_plan = {
                    **plan,
                    "status": plan_status,
                    "steps": updated_steps,
                }

                logger.info(f"  [handle_agent_response] Updated plan status: {plan_status}")
                result["current_plan"] = updated_plan
        else:
            logger.info(f"  [handle_agent_response] Skipping plan update (conversation_status={conversation_status} != completed)")

        return result

    except Exception as e:
        logger.error(f"  [handle_agent_response] Error: {e}", exc_info=True)
        # Use LLM to generate error response so it streams
        llm = get_llm(mini=False)
        error_response = await safe_llm_invoke(llm, [
            SystemMessage(content=BUILDER_SYSTEM_PROMPT),
            HumanMessage(content=f"I encountered an error while communicating with the specialist agent: {str(e)}. Please try again or let me know how else I can help."),
        ])
        return {"messages": [error_response], "pending_agent_question": None}
