"""
Action Executor
================
Executes plan steps by making HTTP calls to the AI Hub microservices.

Takes a step (domain, action, parameters) and:
1. Looks up the ActionDefinition from the registry
2. Determines which microservice handles the action
3. Builds the HTTP request (method, path, payload)
4. Calls the appropriate AI Hub microservice
5. Returns structured results

Supports routing to multiple microservices:
- Main app (agents, tools, connections, etc.)
- Document API (document processing)
- Scheduler API (jobs)
- Executor API (workflow execution)
- etc.
"""

import logging
import httpx
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from enum import Enum

from .registry_loader import get_action_registry, is_initialized
from builder_config import get_service_url, ServiceTarget

logger = logging.getLogger(__name__)


# ─── File Upload Helpers ─────────────────────────────────────────────────

def _resolve_file_path(file_ref: str) -> Optional[Path]:
    """
    Resolve a file reference to a filesystem Path.

    Accepts (tried in order):
    1. A file_id from the upload store (e.g., "abc123def456")
    2. A filename from the upload store (e.g., "document.pdf") — fallback for
       when the LLM uses the human-readable name instead of the file_id
    3. A direct filesystem path (e.g., "C:\\...\\uploads\\abc123_report.pdf")

    Returns Path or None.
    """
    # 1. Try upload store by file_id
    try:
        from routes.upload import get_file_path
        path = get_file_path(file_ref)
        if path and path.exists():
            return path
    except Exception:
        # ImportError if module not available, RuntimeError if FastAPI deps missing, etc.
        pass

    # 2. Try upload store by filename (fallback for when LLM uses filename instead of file_id)
    try:
        from routes.upload import get_file_path_by_filename
        path = get_file_path_by_filename(file_ref)
        if path and path.exists():
            logger.info(f"  [executor] Resolved file by filename fallback: {file_ref} -> {path}")
            return path
    except Exception:
        pass

    # 3. Try as direct filesystem path
    direct = Path(file_ref)
    if direct.exists() and direct.is_file():
        return direct

    # 4. Fallback: search common project directories for the bare filename
    #    (handles cases where the LLM shortened a full path to just a filename)
    try:
        bare_name = Path(file_ref).name  # e.g., "company_policy.txt"
        if bare_name and bare_name != file_ref:
            # Already tried as full path above and it failed — skip re-check
            pass
        else:
            bare_name = file_ref  # file_ref IS just a filename

        # Search common locations relative to the app root
        import os
        app_root = os.getenv('APP_ROOT', str(Path(__file__).resolve().parent.parent.parent))
        search_dirs = [
            Path(app_root) / "test_data",
            Path(app_root) / "uploads",
            Path(app_root) / "data",
            Path(app_root) / "documents",
            Path(app_root),
        ]
        for search_dir in search_dirs:
            candidate = search_dir / bare_name
            if candidate.exists() and candidate.is_file():
                logger.info(f"  [executor] Resolved file by directory search: {file_ref} -> {candidate}")
                return candidate
    except Exception as e:
        logger.warning(f"  [executor] File search fallback failed: {e}")

    return None


def _guess_content_type(filename: str) -> str:
    """Guess MIME type from filename extension."""
    ext = Path(filename).suffix.lower()
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".txt": "text/plain",
        ".csv": "text/csv",
        ".json": "application/json",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }.get(ext, "application/octet-stream")


class ExecutionStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    PENDING = "pending"


@dataclass
class ExecutionResult:
    """Result of executing a single action."""
    status: ExecutionStatus
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    http_status: Optional[int] = None
    service_url: Optional[str] = None  # Which microservice handled this

    @property
    def is_success(self) -> bool:
        return self.status == ExecutionStatus.SUCCESS

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "message": self.message,
            "data": self.data,
            "error": self.error,
            "http_status": self.http_status,
            "service_url": self.service_url,
        }


class ActionExecutor:
    """
    Executes plan steps against the AI Hub microservices.

    Routes requests to the appropriate microservice based on the action's
    service field. Maintains a pool of HTTP clients, one per service.

    Usage:
        async with ActionExecutor(api_key="...") as executor:
            result = await executor.execute_step(
                domain="agents",
                action="create",
                parameters={"agent_description": "My Bot", ...}
            )
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        base_url: Optional[str] = None,  # Legacy: fallback for main service
    ):
        self.api_key = api_key
        self.timeout = timeout
        self._legacy_base_url = base_url  # For backwards compatibility
        self._clients: Dict[str, httpx.AsyncClient] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    def _get_service_base_url(self, service: str) -> str:
        """Get the base URL for a microservice."""
        # Try using the service URL getter
        try:
            url = get_service_url(service)
            if url:
                return url.rstrip("/")
        except Exception as e:
            logger.warning(f"  [executor] Failed to get URL for service '{service}': {e}")

        # Fallback to legacy base_url if provided
        if self._legacy_base_url:
            return self._legacy_base_url.rstrip("/")

        # Ultimate fallback
        return "http://localhost:5000"

    async def _get_client(self, service: str) -> httpx.AsyncClient:
        """Get or create an HTTP client for a specific service."""
        if service not in self._clients:
            base_url = self._get_service_base_url(service)
            headers = {}
            if self.api_key:
                # Use both headers for compatibility:
                # - X-Internal-API-Key: Checked first by internal_api_key_required()
                # - X-API-Key: Standard API key header for other endpoints
                headers["X-Internal-API-Key"] = self.api_key
                headers["X-API-Key"] = self.api_key
            self._clients[service] = httpx.AsyncClient(
                base_url=base_url,
                headers=headers,
                timeout=self.timeout,
            )
            logger.info(f"  [executor] Created client for service '{service}' → {base_url}")
        return self._clients[service]

    async def close(self):
        """Close all HTTP clients."""
        for service, client in self._clients.items():
            await client.aclose()
            logger.debug(f"  [executor] Closed client for service '{service}'")
        self._clients.clear()

    # Legacy property for backwards compatibility
    @property
    def base_url(self) -> str:
        return self._get_service_base_url(ServiceTarget.MAIN)
    
    async def execute_step(
        self,
        domain: str,
        action: str,
        parameters: Optional[Dict[str, Any]] = None,
        description: str = "",
    ) -> ExecutionResult:
        """
        Execute a single plan step.

        Args:
            domain: Platform domain (e.g., "agents", "workflows")
            action: Action within the domain (e.g., "create", "update")
            parameters: Input parameters for the action
            description: Human-readable step description (for logging)

        Returns:
            ExecutionResult with status, data, and any errors
        """
        capability_id = f"{domain}.{action}"
        parameters = parameters or {}

        logger.info(f"  [executor] Executing: {capability_id}")
        logger.info(f"  [executor] Description: {description}")

        # Check if registries are loaded
        if not is_initialized():
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message="Action registry not initialized",
                error="Registries not loaded. Call load_registries() at startup.",
            )

        # Look up the action definition
        action_registry = get_action_registry()
        action_def = action_registry.get_action(capability_id)

        if not action_def:
            logger.warning(f"  [executor] No action mapping for: {capability_id}")
            return ExecutionResult(
                status=ExecutionStatus.SKIPPED,
                message=f"No action mapping found for {capability_id}",
                error=f"Capability '{capability_id}' has no action definition",
            )

        # Determine which microservice handles this action
        service = getattr(action_def, 'service', ServiceTarget.MAIN)
        service_url = self._get_service_base_url(service)
        logger.info(f"  [executor] Service: {service} → {service_url}")

        # Get the route mapping
        if action_def.is_simple:
            route = action_def.primary_route
        elif action_def.is_sequence:
            # For sequences, execute each step
            return await self._execute_sequence(action_def, parameters, description)
        else:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message=f"Invalid action definition for {capability_id}",
                error="Action has neither primary_route nor sequence",
            )

        # Build and execute the HTTP request
        return await self._execute_route(route, parameters, capability_id, service)
    
    async def _execute_route(
        self,
        route,  # RouteMapping
        parameters: Dict[str, Any],
        capability_id: str,
        service: str = ServiceTarget.MAIN,
    ) -> ExecutionResult:
        """Execute a single route mapping against the specified microservice."""
        # Get the client for this service
        client = await self._get_client(service)
        service_url = self._get_service_base_url(service)

        method = route.method.upper()
        path = route.path

        # Substitute path parameters
        if route.path_params:
            for param in route.path_params:
                placeholder = f"<{param}>"
                int_placeholder = f"<int:{param}>"
                str_placeholder = f"<string:{param}>"
                value = str(parameters.get(param, ""))
                path = path.replace(placeholder, value)
                path = path.replace(int_placeholder, value)
                path = path.replace(str_placeholder, value)

        logger.info(f"  [executor] {method} {service_url}{path}")
        
        # Build request body based on encoding
        body = None
        form_data = None
        query_params = None
        file_fields = {}    # Only populated for multipart encoding
        mp_form_fields = {} # Non-file form fields for multipart

        # Filter parameters to only include defined input fields
        field_names = {f.name for f in route.input_fields}
        api_field_map = {f.name: f.effective_api_field for f in route.input_fields}

        # Build alias map for FILE type fields (e.g., "file_id" → "file")
        # LLMs often use file_id instead of the schema field name
        file_aliases = {}
        for f in route.input_fields:
            if hasattr(f, 'field_type') and f.field_type.value == "file":
                file_aliases[f"{f.name}_id"] = f.name

        # Build filtered payload with correct API field names
        filtered_params = {}
        for name, value in parameters.items():
            # Check direct field name match first
            if name in field_names:
                api_name = api_field_map.get(name, name)
                filtered_params[api_name] = value
            # Check file field aliases (e.g., file_id → file)
            elif name in file_aliases:
                canonical_name = file_aliases[name]
                api_name = api_field_map.get(canonical_name, canonical_name)
                filtered_params[api_name] = value
                logger.info(f"  [executor] Mapped alias '{name}' -> '{canonical_name}' (FILE field)")

        # Apply defaults for missing required fields
        for f in route.input_fields:
            api_name = f.effective_api_field
            if api_name not in filtered_params and f.default is not None:
                filtered_params[api_name] = f.default

        # Special case: if creating an agent and missing agent_objective, provide a default
        if capability_id == "agents.create" and "agent_objective" not in filtered_params:
            agent_name = filtered_params.get("agent_description", "the agent")
            filtered_params["agent_objective"] = f"You are a helpful AI assistant named {agent_name}. Help users with their requests."
            logger.info(f"  [executor] Added default agent_objective for {agent_name}")

        encoding = route.encoding.value if hasattr(route.encoding, 'value') else str(route.encoding)

        if encoding == "json":
            body = filtered_params
        elif encoding == "form":
            form_data = filtered_params
        elif encoding == "query":
            query_params = filtered_params
        elif encoding == "multipart":
            # Build multipart form data with file uploads
            # Identify which fields are FILE type from the route definition
            file_field_names = set()
            for f in route.input_fields:
                if hasattr(f, 'field_type') and f.field_type.value == "file":
                    api_name = f.effective_api_field
                    file_field_names.add(api_name)

            for key, value in filtered_params.items():
                if key in file_field_names and value:
                    # Resolve file_id or path to actual file
                    resolved = _resolve_file_path(str(value))
                    if resolved and resolved.exists():
                        file_fields[key] = resolved
                    else:
                        return ExecutionResult(
                            status=ExecutionStatus.FAILED,
                            message=f"File not found for field '{key}': {value}",
                            error=f"Could not resolve file reference '{value}' to a file on disk. "
                                  f"Make sure a file was uploaded before this action.",
                            service_url=service_url,
                        )
                else:
                    mp_form_fields[key] = str(value) if value is not None else ""

            logger.info(f"  [executor] Multipart upload: files={list(file_fields.keys())}, "
                        f"form={list(mp_form_fields.keys())}")
        # "none" encoding: no body needed (e.g., GET with path params)

        logger.info(f"  [executor] Payload: {filtered_params}")

        try:
            if method == "GET":
                response = await client.get(path, params=query_params or filtered_params)
            elif method == "POST":
                if file_fields:
                    # Multipart file upload
                    files_dict = {}
                    for field_name, fpath in file_fields.items():
                        content_type = _guess_content_type(fpath.name)
                        files_dict[field_name] = (fpath.name, fpath.read_bytes(), content_type)
                    response = await client.post(path, files=files_dict, data=mp_form_fields)
                elif form_data:
                    response = await client.post(path, data=form_data)
                else:
                    # Use empty dict instead of None to ensure Content-Type: application/json is set
                    response = await client.post(path, json=body if body is not None else {})
            elif method == "PUT":
                response = await client.put(path, json=body)
            elif method == "DELETE":
                response = await client.delete(path, params=query_params)
            elif method == "PATCH":
                response = await client.patch(path, json=body)
            else:
                return ExecutionResult(
                    status=ExecutionStatus.FAILED,
                    message=f"Unsupported HTTP method: {method}",
                    error=f"Method {method} not implemented",
                    service_url=service_url,
                )

            logger.info(f"  [executor] Response: {response.status_code}")

            # ── Auth/redirect diagnostics ──────────────────────────────────
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location", "?")
                logger.warning(
                    f"  [executor] ⚠ Redirect detected for {capability_id}: "
                    f"{response.status_code} → {location}  "
                    f"(This usually means the endpoint requires session auth "
                    f"via @login_required instead of @api_key_or_session_required. "
                    f"Check the route decorator in app.py.)"
                )
            elif response.status_code == 401:
                logger.warning(
                    f"  [executor] ⚠ Auth rejected (401) for {capability_id}: "
                    f"API key may be invalid or missing. "
                    f"Key present: {bool(self.api_key)}, "
                    f"Key prefix: {self.api_key[:8] + '...' if self.api_key else 'None'}"
                )
            elif response.status_code == 404:
                logger.warning(
                    f"  [executor] ⚠ Route not found (404) for {capability_id}: "
                    f"{method} {service_url}{path}  "
                    f"(Check that the Flask route exists and accepts {method} method)"
                )

            # Parse response
            try:
                response_data = response.json()
            except:
                response_data = {"raw": response.text}

            # Check success
            is_success = response.status_code in route.success_status_codes

            if route.success_indicator and isinstance(response_data, dict):
                indicator_value = response_data.get(route.success_indicator)
                if indicator_value in (False, "error", "fail", "failed"):
                    is_success = False

            # Domain-specific success checks for operations that return 200 but contain failure info
            if is_success and isinstance(response_data, dict):
                # connections.test: Check for status: "error"
                if capability_id == "connections.test":
                    status_field = response_data.get("status")
                    if status_field == "error":
                        is_success = False
                        logger.info(f"  [executor] Connection test returned HTTP 200 but status='error'")

                # connections.discover_tables: Check for success: False or error indicators
                elif capability_id == "connections.discover_tables":
                    success_field = response_data.get("success")
                    if success_field is False:
                        is_success = False
                        logger.info(f"  [executor] Table discovery returned HTTP 200 but success=False")
                    # Also check for empty tables — may indicate connection or DB failure
                    elif success_field is True:
                        tables = response_data.get("tables", [])
                        if not tables:
                            # Empty tables list likely means the connection failed or DB has no tables
                            # Mark as failed so the sidebar shows the correct status
                            is_success = False
                            message = response_data.get("message", "")
                            logger.info(f"  [executor] Table discovery returned empty tables: {message}")

            # Extract mapped response fields
            extracted = {}
            if is_success and route.response_mappings:
                for mapping in route.response_mappings:
                    value = self._extract_path(response_data, mapping.source_path)
                    if value is not None:
                        extracted[mapping.output_name] = value

            if is_success:
                logger.info(f"  [executor] ✓ Success: {capability_id}")
                return ExecutionResult(
                    status=ExecutionStatus.SUCCESS,
                    message=f"Successfully executed {capability_id}",
                    data=extracted or response_data,
                    http_status=response.status_code,
                    service_url=service_url,
                )
            else:
                error_msg = response_data.get("message") or response_data.get("error") or str(response_data)
                logger.warning(f"  [executor] ✗ Failed: {error_msg}")
                return ExecutionResult(
                    status=ExecutionStatus.FAILED,
                    message=f"Failed to execute {capability_id}",
                    data=response_data,
                    error=error_msg,
                    http_status=response.status_code,
                    service_url=service_url,
                )

        except httpx.ConnectError as e:
            logger.error(f"  [executor] Connection failed: {e}")
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message=f"Could not connect to service at {service_url}",
                error=str(e),
                service_url=service_url,
            )
        except httpx.TimeoutException as e:
            logger.error(f"  [executor] Timeout: {e}")
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message="Request timed out",
                error=str(e),
                service_url=service_url,
            )
        except Exception as e:
            logger.error(f"  [executor] Error: {e}", exc_info=True)
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message=f"Execution error: {str(e)}",
                error=str(e),
                service_url=service_url,
            )
    
    async def _execute_sequence(
        self,
        action_def,  # ActionDefinition
        parameters: Dict[str, Any],
        description: str,
    ) -> ExecutionResult:
        """Execute a multi-step action sequence."""
        sequence = action_def.sequence
        results = []
        step_outputs = {}  # Accumulated outputs from previous steps

        # Get the service for this action
        service = getattr(action_def, 'service', ServiceTarget.MAIN)
        service_url = self._get_service_base_url(service)

        logger.info(f"  [executor] Executing sequence: {len(sequence.steps)} steps")
        logger.info(f"  [executor] Service: {service} → {service_url}")

        for i, step in enumerate(sequence.steps):
            # Merge parameters with outputs from previous steps
            step_params = {**step_outputs, **parameters}

            result = await self._execute_route(
                step,
                step_params,
                f"{action_def.capability_id}[{i}]",
                service,
            )
            results.append(result)

            if not result.is_success:
                logger.warning(f"  [executor] Sequence failed at step {i}")
                return ExecutionResult(
                    status=ExecutionStatus.FAILED,
                    message=f"Sequence failed at step {i+1}: {step.description}",
                    data={"step_results": [r.to_dict() for r in results]},
                    error=result.error,
                    service_url=service_url,
                )

            # Collect outputs for chaining
            step_outputs.update(result.data)

        logger.info(f"  [executor] ✓ Sequence complete")
        return ExecutionResult(
            status=ExecutionStatus.SUCCESS,
            message=f"Successfully executed {action_def.capability_id}",
            data=step_outputs,
            service_url=service_url,
        )
    
    def _extract_path(self, data: dict, path: str) -> Any:
        """Extract a value from nested dict using dot notation."""
        parts = path.split(".")
        current = data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current
