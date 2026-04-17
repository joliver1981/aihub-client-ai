"""
WorkflowBuilder Protocol Adapter
==================================
Adapter for communicating with the WorkflowAgent via its HTTP API.

The WorkflowAgent is a specialized conversational agent that helps users
design and build workflows. It has its own HTTP routes and session management.

Key Features:
- Session-based conversations (auto-created on first message)
- Phase-based flow: discovery → requirements → planning → building → refinement
- Returns workflow_commands when ready to build
- Supports validation and error correction

API Endpoints (all relative to base endpoint):
- POST /guide       - Send messages, get responses (main endpoint)
- POST /validate    - Validate a workflow state
- GET /history      - Get conversation history
- GET /status       - Get session status
- POST /check-mode  - Check if should start in refine mode
- POST /clear       - Clear/destroy a session

This adapter handles the translation between the generic AgentProtocolAdapter
interface and the WorkflowAgent's specific HTTP API.
"""

import asyncio
import json
import logging
import os
from typing import AsyncGenerator, List, Dict, Optional, Any
from dataclasses import dataclass, field
import httpx

from .base import AgentProtocolAdapter, AdapterRegistry

logger = logging.getLogger(__name__)


def _get_base_url() -> str:
    """Get the AI Hub base URL from environment or config."""
    # Try to import from builder_config first
    try:
        from builder_config import AI_HUB_BASE_URL
        return AI_HUB_BASE_URL
    except ImportError:
        pass

    # Fall back to environment variable
    return os.environ.get("AI_HUB_BASE_URL", "http://localhost:5000")


def _get_api_key() -> str:
    """Get the internal API key for service-to-service auth."""
    try:
        from builder_config import AI_HUB_API_KEY
        return AI_HUB_API_KEY
    except ImportError:
        pass

    return os.environ.get("AI_HUB_API_KEY", "")


@dataclass
class WorkflowAgentResponse:
    """Parsed response from the WorkflowAgent."""
    status: str
    response: str
    phase: str
    requirements: Dict[str, Any]
    workflow_plan: Optional[str] = None
    workflow_commands: Optional[Dict[str, Any]] = None
    session_id: str = ""
    is_refine_mode: bool = False
    has_workflow: bool = False
    error: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowAgentResponse":
        """Create from API response dict."""
        return cls(
            status=data.get("status", "unknown"),
            response=data.get("response", ""),
            phase=data.get("phase", ""),
            requirements=data.get("requirements", {}),
            workflow_plan=data.get("workflow_plan"),
            workflow_commands=data.get("workflow_commands"),
            session_id=data.get("session_id", ""),
            is_refine_mode=data.get("is_refine_mode", False),
            has_workflow=data.get("has_workflow", False),
            error=data.get("error"),
        )


class WorkflowBuilderAdapter(AgentProtocolAdapter):
    """
    Adapter for the WorkflowAgent's HTTP API.

    This adapter:
    - Manages session IDs for multi-turn conversations
    - Sends messages to the /guide endpoint
    - Parses structured responses including workflow_commands
    - Handles validation via /validate endpoint
    - Supports workflow state synchronization

    The WorkflowAgent is conversational - it asks questions to gather
    requirements before generating workflow_commands. The adapter handles
    this naturally by streaming responses back to the caller.
    """

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        # Track session IDs for conversations
        self._conversation_sessions: Dict[str, str] = {}

    @property
    def protocol_name(self) -> str:
        return "workflow_builder"

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client with auth headers."""
        if self._client is None or self._client.is_closed:
            headers = {}
            api_key = _get_api_key()
            if api_key:
                headers["X-Internal-API-Key"] = api_key
                headers["X-API-Key"] = api_key
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(180.0),
                headers=headers,
            )
        return self._client

    def _get_session_id(self, conversation_id: Optional[str] = None) -> str:
        """
        Get or generate a session ID for the WorkflowAgent.

        The WorkflowAgent uses its own session management. We map our
        conversation IDs to WorkflowAgent session IDs.
        """
        if conversation_id and conversation_id in self._conversation_sessions:
            return self._conversation_sessions[conversation_id]

        # Generate a new session ID
        import uuid
        session_id = f"builder-agent-wf-{uuid.uuid4().hex[:12]}"

        if conversation_id:
            self._conversation_sessions[conversation_id] = session_id

        return session_id

    def _resolve_endpoint(self, endpoint: str) -> str:
        """
        Resolve a potentially relative endpoint to a full URL.

        If the endpoint starts with '/', it's treated as relative to AI_HUB_BASE_URL.
        Otherwise, it's used as-is.
        """
        if endpoint.startswith("/"):
            base_url = _get_base_url().rstrip("/")
            return f"{base_url}{endpoint}"
        return endpoint

    async def send_message(
        self,
        endpoint: str,
        message: str,
        conversation_history: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        timeout: int = 120,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        Send a message to the WorkflowAgent and stream the response.

        The WorkflowAgent expects:
        - message: The user's message
        - session_id: Session identifier (auto-managed)
        - workflow_state: Current canvas state (if any)
        - is_validation_fix: Whether this is fixing validation errors

        Args:
            endpoint: Base URL of the WorkflowAgent API. Can be relative (e.g., "/api/workflow/builder")
                      or absolute (e.g., "http://localhost:5000/api/workflow/builder")
            message: The message to send
            conversation_history: Previous messages (used for context but not sent directly)
            system_prompt: Not used - WorkflowAgent manages its own prompts
            timeout: Max seconds to wait
            **kwargs: Additional options:
                - conversation_id: Our conversation ID for session mapping
                - workflow_state: Current workflow canvas state
                - is_validation_fix: Set true when sending validation errors

        Yields:
            str: The agent's response text (followed by metadata as JSON if commands present)
        """
        client = await self._get_client()

        # Extract kwargs
        conversation_id = kwargs.get("conversation_id")
        workflow_state = kwargs.get("workflow_state")
        is_validation_fix = kwargs.get("is_validation_fix", False)
        is_builder_delegation = kwargs.get("is_builder_delegation", False)

        # Get or create session ID
        session_id = self._get_session_id(conversation_id)

        # Resolve relative endpoint to full URL and build /guide endpoint
        base_endpoint = self._resolve_endpoint(endpoint)
        guide_endpoint = base_endpoint.rstrip("/") + "/guide"
        payload = {
            "message": message,
            "session_id": session_id,
            "workflow_state": workflow_state,
            "is_validation_fix": is_validation_fix,
            "is_builder_delegation": is_builder_delegation,
        }

        logger.info(f"Sending message to WorkflowAgent at {guide_endpoint}")
        logger.info(f"  Session: {session_id}, Phase: {kwargs.get('current_phase', 'unknown')}")
        logger.debug(f"  Payload: {json.dumps(payload, indent=2)[:500]}")

        try:
            response = await client.post(
                guide_endpoint,
                json=payload,
                timeout=float(timeout)
            )
            response.raise_for_status()

            # Parse the JSON response
            data = response.json()
            parsed = WorkflowAgentResponse.from_dict(data)

            if parsed.status == "error":
                error_msg = parsed.error or "Unknown error from WorkflowAgent"
                logger.error(f"WorkflowAgent error: {error_msg}")
                yield f"[Error from WorkflowAgent: {error_msg}]"
                return

            # Yield the main response text
            yield parsed.response

            # ALWAYS include metadata so the caller knows the phase
            # This is crucial for detecting when the agent is in discovery/requirements
            # phases and is asking questions
            if parsed.workflow_commands:
                logger.info(f"WorkflowAgent returned commands: {len(parsed.workflow_commands.get('commands', []))} commands")
            if parsed.workflow_plan:
                logger.info(f"WorkflowAgent returned workflow_plan (ready to compile)")

            # Append structured metadata for the caller
            metadata = {
                "__workflow_metadata__": True,
                "phase": parsed.phase,
                "has_workflow": parsed.has_workflow,
                "workflow_commands": parsed.workflow_commands,
                "workflow_plan": parsed.workflow_plan,
                "requirements": parsed.requirements,
                "session_id": parsed.session_id,
            }
            yield f"\n\n---WORKFLOW_METADATA---\n{json.dumps(metadata)}"

            logger.info(f"WorkflowAgent response: phase={parsed.phase}, has_workflow={parsed.has_workflow}, has_plan={parsed.workflow_plan is not None}")

        except httpx.TimeoutException:
            logger.error(f"Timeout communicating with WorkflowAgent at {guide_endpoint}")
            raise TimeoutError(f"WorkflowAgent did not respond within {timeout} seconds")

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error from WorkflowAgent: {e.response.status_code}")
            try:
                error_data = e.response.json()
                error_msg = error_data.get("error", str(e))
            except Exception:
                error_msg = str(e)
            raise ConnectionError(f"WorkflowAgent returned error: {error_msg}")

        except Exception as e:
            logger.error(f"Error communicating with WorkflowAgent: {e}")
            raise

    async def check_health(self, endpoint: str, timeout: int = 5) -> bool:
        """
        Check if the WorkflowAgent is available.

        Tries the /status endpoint with a test session ID.
        """
        client = await self._get_client()

        base_endpoint = self._resolve_endpoint(endpoint)
        status_endpoint = base_endpoint.rstrip("/") + "/status"

        try:
            response = await client.get(
                status_endpoint,
                params={"session_id": "health-check"},
                timeout=float(timeout)
            )
            # Even a 400 (session not found) means the agent is running
            return response.status_code in (200, 400, 404)

        except Exception as e:
            logger.debug(f"WorkflowAgent health check failed: {e}")
            return False

    async def validate_workflow(
        self,
        endpoint: str,
        workflow_state: Dict[str, Any],
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """
        Validate a workflow state via the WorkflowAgent.

        Args:
            endpoint: Base URL of the WorkflowAgent API
            workflow_state: The workflow state to validate
            timeout: Max seconds to wait

        Returns:
            Dict with validation results:
            - is_valid: bool
            - errors: List[str]
            - warnings: List[str]
        """
        client = await self._get_client()

        base_endpoint = self._resolve_endpoint(endpoint)
        validate_endpoint = base_endpoint.rstrip("/") + "/validate"

        try:
            response = await client.post(
                validate_endpoint,
                json={"workflow_state": workflow_state},
                timeout=float(timeout)
            )
            response.raise_for_status()

            data = response.json()
            return {
                "is_valid": data.get("is_valid", False),
                "errors": data.get("errors", []),
                "warnings": data.get("warnings", []),
            }

        except Exception as e:
            logger.error(f"Workflow validation failed: {e}")
            return {
                "is_valid": False,
                "errors": [str(e)],
                "warnings": [],
            }

    async def get_conversation_history(
        self,
        endpoint: str,
        conversation_id: str,
        timeout: int = 10,
    ) -> List[Dict[str, str]]:
        """
        Get conversation history from the WorkflowAgent.

        Args:
            endpoint: Base URL of the WorkflowAgent API
            conversation_id: Our conversation ID
            timeout: Max seconds to wait

        Returns:
            List of message dicts with 'role' and 'content'
        """
        client = await self._get_client()

        session_id = self._get_session_id(conversation_id)
        base_endpoint = self._resolve_endpoint(endpoint)
        history_endpoint = base_endpoint.rstrip("/") + "/history"

        try:
            response = await client.get(
                history_endpoint,
                params={"session_id": session_id},
                timeout=float(timeout)
            )
            response.raise_for_status()

            data = response.json()
            return data.get("history", [])

        except Exception as e:
            logger.error(f"Failed to get conversation history: {e}")
            return []

    async def get_session_status(
        self,
        endpoint: str,
        conversation_id: str,
        timeout: int = 10,
    ) -> Dict[str, Any]:
        """
        Get session status from the WorkflowAgent.

        Args:
            endpoint: Base URL of the WorkflowAgent API
            conversation_id: Our conversation ID
            timeout: Max seconds to wait

        Returns:
            Session status dict including phase, requirements, etc.
        """
        client = await self._get_client()

        session_id = self._get_session_id(conversation_id)
        base_endpoint = self._resolve_endpoint(endpoint)
        status_endpoint = base_endpoint.rstrip("/") + "/status"

        try:
            response = await client.get(
                status_endpoint,
                params={"session_id": session_id},
                timeout=float(timeout)
            )
            response.raise_for_status()

            data = response.json()
            return data.get("summary", {})

        except Exception as e:
            logger.error(f"Failed to get session status: {e}")
            return {}

    async def clear_session(
        self,
        endpoint: str,
        conversation_id: str,
        timeout: int = 10,
    ) -> bool:
        """
        Clear/destroy a WorkflowAgent session.

        Args:
            endpoint: Base URL of the WorkflowAgent API
            conversation_id: Our conversation ID
            timeout: Max seconds to wait

        Returns:
            True if cleared successfully
        """
        client = await self._get_client()

        session_id = self._conversation_sessions.get(conversation_id)
        if not session_id:
            return True  # Nothing to clear

        base_endpoint = self._resolve_endpoint(endpoint)
        clear_endpoint = base_endpoint.rstrip("/") + "/clear"

        try:
            response = await client.post(
                clear_endpoint,
                json={"session_id": session_id},
                timeout=float(timeout)
            )
            response.raise_for_status()

            # Remove from our mapping
            del self._conversation_sessions[conversation_id]
            return True

        except Exception as e:
            logger.error(f"Failed to clear session: {e}")
            return False

    async def send_accelerated_build(
        self,
        endpoint: str,
        workflow_plan: str,
        conversation_id: Optional[str] = None,
        timeout: int = 120,
    ) -> AsyncGenerator[str, None]:
        """
        Send a complete workflow plan for accelerated building.

        This skips the discovery/requirements phases when the BuilderAgent
        already has complete requirements and a plan.

        Args:
            endpoint: Base URL of the WorkflowAgent API
            workflow_plan: The workflow plan in <workflow_plan> format
            conversation_id: Optional conversation ID for session mapping
            timeout: Max seconds to wait

        Yields:
            str: The agent's response (likely with workflow_commands)
        """
        # Format the accelerated build message
        message = f"Build this workflow:\n\n{workflow_plan}\n\nBuild it now."

        async for chunk in self.send_message(
            endpoint=endpoint,
            message=message,
            conversation_history=[],
            timeout=timeout,
            conversation_id=conversation_id,
        ):
            yield chunk

    async def list_workflows(
        self,
        timeout: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        List all saved workflows (lightweight summary).

        Calls GET /api/workflows/list to retrieve workflow summaries
        (id, workflow_name, category, enabled) without full workflow_data.

        Args:
            timeout: Max seconds to wait

        Returns:
            List of workflow dicts, each containing 'id', 'workflow_name', 'category', 'enabled'
        """
        client = await self._get_client()

        base_url = _get_base_url().rstrip("/")
        url = f"{base_url}/api/workflows/list"

        logger.info(f"Listing workflows via {url}")

        try:
            response = await client.get(url, timeout=float(timeout))
            response.raise_for_status()

            data = response.json()
            # /api/workflows/list returns {"workflows": [...]}
            if isinstance(data, dict) and "workflows" in data:
                workflows = data["workflows"]
                logger.info(f"Found {len(workflows)} workflows")
                return workflows
            elif isinstance(data, list):
                # Fallback for raw list response
                logger.info(f"Found {len(data)} workflows")
                return data
            else:
                logger.warning(f"Unexpected response format from /api/workflows/list: {type(data)}")
                return []

        except Exception as e:
            logger.error(f"Failed to list workflows: {e}")
            return []

    async def get_workflow(
        self,
        workflow_id: int,
        timeout: int = 30,
    ) -> Optional[Dict[str, Any]]:
        """
        Get the full workflow state for a specific workflow.

        Calls GET /get/workflow/<workflow_id> to retrieve nodes, connections, and variables.

        Args:
            workflow_id: The ID of the workflow to load
            timeout: Max seconds to wait

        Returns:
            Dict with workflow state (nodes, connections, variables) or None if not found
        """
        client = await self._get_client()

        base_url = _get_base_url().rstrip("/")
        url = f"{base_url}/get/workflow/{workflow_id}"

        logger.info(f"Loading workflow {workflow_id} via {url}")

        try:
            response = await client.get(url, timeout=float(timeout))
            response.raise_for_status()

            data = response.json()
            logger.info(f"Loaded workflow {workflow_id}: {len(data.get('nodes', []))} nodes, {len(data.get('connections', []))} connections")
            return data

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Workflow {workflow_id} not found")
            else:
                logger.error(f"HTTP error loading workflow {workflow_id}: {e.response.status_code}")
            return None

        except Exception as e:
            logger.error(f"Failed to load workflow {workflow_id}: {e}")
            return None

    async def find_workflow_by_name(
        self,
        name: str,
        timeout: int = 30,
    ) -> Optional[Dict[str, Any]]:
        """
        Find a workflow by name (case-insensitive fuzzy match).

        Lists all workflows and finds the best match for the given name.

        Args:
            name: The workflow name to search for
            timeout: Max seconds to wait

        Returns:
            The matching workflow dict (with 'id' and 'workflow_name') or None
        """
        workflows = await self.list_workflows(timeout=timeout)
        if not workflows:
            return None

        name_lower = name.lower().strip()

        # First pass: exact match (case-insensitive)
        for wf in workflows:
            wf_name = wf.get("workflow_name", "")
            if wf_name.lower().strip() == name_lower:
                logger.info(f"Exact match found: '{wf_name}' (ID={wf.get('id')})")
                return wf

        # Second pass: substring match
        for wf in workflows:
            wf_name = wf.get("workflow_name", "")
            if name_lower in wf_name.lower() or wf_name.lower() in name_lower:
                logger.info(f"Substring match found: '{wf_name}' (ID={wf.get('id')})")
                return wf

        logger.info(f"No workflow found matching '{name}'")
        return None

    async def compile_workflow(
        self,
        endpoint: str,
        workflow_plan: str,
        workflow_name: str,
        requirements: Optional[Dict[str, Any]] = None,
        workflow_id: Optional[int] = None,
        save: bool = True,
        max_fix_attempts: int = 2,
        timeout: int = 180,
    ) -> Dict[str, Any]:
        """
        Compile a workflow plan into a saved workflow.

        This is Path B in the BuilderAgent workflow creation flow.
        It takes a workflow_plan (numbered steps) and compiles it directly
        into a saved workflow, skipping the conversational guide.

        When workflow_id is provided, the compile endpoint operates in edit mode:
        it loads the existing workflow, generates delta commands, and materializes
        changes on top of the existing workflow.

        Args:
            endpoint: Base URL of the WorkflowAgent API (e.g., "/api/workflow/builder")
            workflow_plan: The numbered plan text from the WorkflowAgent
            workflow_name: Name for the workflow
            requirements: Optional requirements context for better accuracy
            workflow_id: If provided, triggers edit mode (modifies existing workflow)
            save: Whether to save to DB (False for dry-run validation)
            max_fix_attempts: Max validation → fix → revalidate cycles
            timeout: Max seconds to wait

        Returns:
            Dict with compile result:
            - status: "success" or "error"
            - workflow_id: int (if saved)
            - workflow_name: str
            - workflow_data: dict with nodes/connections/variables
            - validation: dict with is_valid/errors/warnings
            - node_count: int
            - connection_count: int
            - mode: "create" or "edit"
            - error: str (if failed)
        """
        client = await self._get_client()

        base_endpoint = self._resolve_endpoint(endpoint)
        compile_endpoint = base_endpoint.rstrip("/") + "/compile"

        payload = {
            "workflow_plan": workflow_plan,
            "workflow_name": workflow_name,
            "save": save,
            "max_fix_attempts": max_fix_attempts,
        }

        if requirements:
            payload["requirements"] = requirements

        if workflow_id is not None:
            payload["workflow_id"] = workflow_id

        mode = "edit" if workflow_id is not None else "create"
        logger.info(f"Compiling workflow '{workflow_name}' via {compile_endpoint} (mode={mode}, workflow_id={workflow_id})")
        logger.debug(f"  Plan: {workflow_plan[:200]}...")

        try:
            response = await client.post(
                compile_endpoint,
                json=payload,
                timeout=float(timeout)
            )
            response.raise_for_status()

            result = response.json()

            if result.get("status") == "success":
                logger.info(f"Workflow compiled successfully: ID={result.get('workflow_id')}, "
                           f"nodes={result.get('node_count')}, connections={result.get('connection_count')}")
            else:
                logger.warning(f"Workflow compile failed: {result.get('error')}")

            return result

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error from compile endpoint: {e.response.status_code}")
            try:
                error_data = e.response.json()
                return {
                    "status": "error",
                    "error": error_data.get("error", str(e)),
                    "workflow_name": workflow_name,
                    "workflow_data": error_data.get("workflow_data"),
                    "validation": error_data.get("validation"),
                }
            except Exception:
                return {
                    "status": "error",
                    "error": str(e),
                    "workflow_name": workflow_name,
                }

        except Exception as e:
            logger.error(f"Error compiling workflow: {e}")
            return {
                "status": "error",
                "error": str(e),
                "workflow_name": workflow_name,
            }

    async def close(self):
        """Close the HTTP client and clean up."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
        self._conversation_sessions.clear()


# Create and register the adapter
_workflow_builder_adapter = WorkflowBuilderAdapter()
AdapterRegistry.register(_workflow_builder_adapter)


# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def parse_workflow_metadata(response: str) -> tuple[str, Optional[Dict[str, Any]]]:
    """
    Parse a response that may contain workflow metadata.

    The adapter appends workflow metadata as a JSON block after the response.
    This helper separates the response text from the metadata.

    Args:
        response: The full response string

    Returns:
        Tuple of (response_text, metadata_dict or None)
    """
    separator = "\n\n---WORKFLOW_METADATA---\n"

    if separator in response:
        parts = response.split(separator, 1)
        response_text = parts[0]
        try:
            metadata = json.loads(parts[1])
            return response_text, metadata
        except json.JSONDecodeError:
            return response_text, None

    return response, None


def extract_workflow_commands(response: str) -> Optional[Dict[str, Any]]:
    """
    Extract workflow commands from a response if present.

    Args:
        response: The full response string from the adapter

    Returns:
        workflow_commands dict if present, None otherwise
    """
    _, metadata = parse_workflow_metadata(response)

    if metadata and metadata.get("__workflow_metadata__"):
        return metadata.get("workflow_commands")

    return None
