"""
MCP Tool Converter
Converts MCP tool definitions (JSON from gateway) into LangChain StructuredTool objects.
Runs in the main application environment — no MCP SDK dependencies.
"""
import logging
from typing import Dict, Any, Optional, List, Type
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

logger = logging.getLogger(__name__)

# Map JSON Schema types to Python types
JSON_SCHEMA_TYPE_MAP = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


class MCPToolConverter:
    """Converts MCP tool definitions to LangChain-compatible StructuredTool objects"""

    def __init__(self, gateway_client, server_id: int, server_name: str):
        """
        Args:
            gateway_client: MCPGatewayClient instance for making tool calls
            server_id: The MCP server ID (from database)
            server_name: Human-readable server name (used for tool name prefixing)
        """
        self.gateway = gateway_client
        self.server_id = server_id
        # Clean server name for use in tool names (alphanumeric + underscore only)
        self.server_name = self._sanitize_name(server_name)

    @staticmethod
    def _sanitize_name(name: str) -> str:
        """Sanitize a name for use as a tool/function identifier"""
        # Replace common separators with underscore
        sanitized = name.replace('-', '_').replace(' ', '_').replace('.', '_')
        # Remove any non-alphanumeric/underscore characters
        sanitized = ''.join(c for c in sanitized if c.isalnum() or c == '_')
        # Remove leading underscores/digits
        sanitized = sanitized.lstrip('_0123456789')
        return sanitized.lower() or 'mcp_server'

    def convert_tool(self, mcp_tool: dict) -> Optional[StructuredTool]:
        """
        Convert a single MCP tool definition to a LangChain StructuredTool.

        Returns None if mcp_tool is None or conversion fails.

        MCP tool format:
        {
            "name": "read_file",
            "description": "Read contents of a file",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"}
                },
                "required": ["path"]
            }
        }
        """
        if not mcp_tool:
            return None

        try:
            original_name = mcp_tool.get("name", "unknown")
            tool_name = f"{self.server_name}_{self._sanitize_name(original_name)}"
            description = mcp_tool.get("description", f"MCP tool: {original_name}")
            input_schema = mcp_tool.get("inputSchema", {})

            # Build pydantic model for the input schema
            pydantic_model = self._build_pydantic_model(tool_name, input_schema)

            # Create closure to capture server_id, tool_name, gateway
            _gateway = self.gateway
            _server_id = self.server_id
            _original_name = original_name

            def _call_mcp_tool(**kwargs) -> str:
                """Execute MCP tool via gateway"""
                try:
                    result = _gateway.call_tool(
                        server_id=_server_id,
                        tool_name=_original_name,
                        arguments=kwargs
                    )
                    if result.get("status") == "success":
                        return result.get("result", "")
                    else:
                        error = result.get("error", "Unknown error")
                        return f"Error executing {_original_name}: {error}"
                except Exception as e:
                    logger.error(f"Error calling MCP tool {_original_name}: {e}")
                    return f"Error executing {_original_name}: {str(e)}"

            # Create the StructuredTool
            structured_tool = StructuredTool(
                name=tool_name,
                description=description,
                func=_call_mcp_tool,
                args_schema=pydantic_model,
            )

            logger.debug(f"Converted MCP tool: {original_name} -> {tool_name}")
            return structured_tool

        except Exception as e:
            logger.error(f"Failed to convert MCP tool '{mcp_tool.get('name', '?')}': {e}")
            return None

    def convert_all_tools(self, mcp_tools: list) -> list:
        """Convert all MCP tools from a server to LangChain tools"""
        langchain_tools = []
        for mcp_tool in mcp_tools:
            tool = self.convert_tool(mcp_tool)
            if tool is not None:
                langchain_tools.append(tool)
        logger.info(f"Converted {len(langchain_tools)}/{len(mcp_tools)} tools from server {self.server_name}")
        return langchain_tools

    def _build_pydantic_model(self, name: str, input_schema: dict) -> Type[BaseModel]:
        """
        Dynamically create a Pydantic model from MCP inputSchema (JSON Schema).

        Handles:
        - string, number, integer, boolean -> str, float, int, bool
        - array -> List
        - object -> Dict[str, Any]
        - required vs optional fields
        - field descriptions (critical for LLM function calling)
        """
        properties = input_schema.get("properties", {})
        required_fields = set(input_schema.get("required", []))

        field_definitions = {}

        for field_name, field_schema in properties.items():
            python_type = self._json_schema_to_python_type(field_schema)
            field_description = field_schema.get("description", "")
            is_required = field_name in required_fields

            if is_required:
                field_definitions[field_name] = (
                    python_type,
                    Field(description=field_description)
                )
            else:
                field_definitions[field_name] = (
                    Optional[python_type],
                    Field(default=None, description=field_description)
                )

        # If no properties, create a model with no fields
        if not field_definitions:
            field_definitions['placeholder'] = (
                Optional[str],
                Field(default=None, description="No parameters required")
            )

        # Create a valid model name
        model_name = f"MCPInput_{self._sanitize_name(name)}"

        try:
            model = create_model(model_name, **field_definitions)
            return model
        except Exception as e:
            logger.warning(f"Failed to create pydantic model for {name}: {e}, using fallback")
            # Fallback: create a simple model with a single string field
            return create_model(
                model_name,
                input=(Optional[str], Field(default=None, description="Tool input as JSON string"))
            )

    def _json_schema_to_python_type(self, schema: dict) -> type:
        """Convert a JSON Schema type definition to a Python type"""
        schema_type = schema.get("type", "string")

        if schema_type == "array":
            # For arrays, we use List (items type is not enforced at pydantic level)
            return list
        elif schema_type == "object":
            return dict
        else:
            return JSON_SCHEMA_TYPE_MAP.get(schema_type, str)
