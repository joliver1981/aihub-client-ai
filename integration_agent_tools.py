# integration_agent_tools.py
"""
Integration Tools for AI Agents
================================

Provides tools that allow AI agents to interact with connected integrations.
These tools are automatically registered with agents that have integration
access enabled.

Usage:
    When an agent has integration tools enabled, it can:
    - List available integrations
    - Execute operations on integrations
    - Get data from connected systems

Example agent usage:
    "Get all unpaid invoices from QuickBooks"
    "Create a new customer in Shopify"
    "Send a message to the #sales channel in Slack"
"""

import json
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


def get_integration_tools() -> List[Dict]:
    """
    Get the tool definitions for integration capabilities.
    
    Returns a list of tool definitions in the format expected by
    the AI agent framework.
    """
    return [
        {
            "name": "list_integrations",
            "description": "List all available integrations that the user has connected. Use this to see what external systems are available before trying to access them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Optional category filter (e.g., 'Accounting', 'E-Commerce', 'CRM')"
                    }
                },
                "required": []
            }
        },
        {
            "name": "get_integration_operations",
            "description": "Get the available operations for a specific integration. Call this before using an integration to understand what actions are available.",
            "parameters": {
                "type": "object",
                "properties": {
                    "integration_id": {
                        "type": "integer",
                        "description": "The ID of the integration to get operations for"
                    },
                    "integration_name": {
                        "type": "string",
                        "description": "Alternatively, the name of the integration (e.g., 'My QuickBooks')"
                    }
                },
                "required": []
            }
        },
        {
            "name": "execute_integration",
            "description": "Execute an operation on a connected integration. For example: get invoices from QuickBooks, create orders in Shopify, send messages to Slack.",
            "parameters": {
                "type": "object",
                "properties": {
                    "integration_id": {
                        "type": "integer",
                        "description": "The ID of the integration to use"
                    },
                    "integration_name": {
                        "type": "string",
                        "description": "Alternatively, the name of the integration"
                    },
                    "operation": {
                        "type": "string",
                        "description": "The operation key to execute (e.g., 'get_invoices', 'create_customer')"
                    },
                    "parameters": {
                        "type": "object",
                        "description": "Parameters for the operation (varies by operation)"
                    }
                },
                "required": ["operation"]
            }
        }
    ]


def execute_integration_tool(
    tool_name: str,
    tool_input: Dict,
    agent_id: int = None,
    user_id: int = None
) -> Dict[str, Any]:
    """
    Execute an integration tool call from an AI agent.
    
    Args:
        tool_name: Name of the tool being called
        tool_input: Tool input parameters
        agent_id: ID of the agent making the call
        user_id: ID of the user the agent is acting for
        
    Returns:
        Tool execution result
    """
    from integration_manager import get_integration_manager
    
    manager = get_integration_manager()
    
    try:
        if tool_name == "list_integrations":
            return _handle_list_integrations(manager, tool_input, user_id)
        
        elif tool_name == "get_integration_operations":
            return _handle_get_operations(manager, tool_input, user_id)
        
        elif tool_name == "execute_integration":
            return _handle_execute_integration(manager, tool_input, agent_id, user_id)
        
        else:
            return {
                "success": False,
                "error": f"Unknown tool: {tool_name}"
            }
            
    except Exception as e:
        logger.error(f"Error executing integration tool {tool_name}: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def _handle_list_integrations(
    manager,
    tool_input: Dict,
    user_id: int
) -> Dict[str, Any]:
    """Handle list_integrations tool call."""
    category = tool_input.get('category')
    
    integrations = manager.list_integrations(user_id=user_id)
    
    # Filter by category if specified
    if category:
        integrations = [
            i for i in integrations 
            if i.get('platform_category', '').lower() == category.lower()
        ]
    
    # Format for agent consumption
    formatted = []
    for integ in integrations:
        formatted.append({
            "id": integ['integration_id'],
            "name": integ['integration_name'],
            "platform": integ['platform_name'],
            "category": integ.get('platform_category'),
            "connected": integ.get('is_connected', False),
            "last_used": integ.get('last_used_at')
        })
    
    return {
        "success": True,
        "integrations": formatted,
        "count": len(formatted),
        "message": f"Found {len(formatted)} connected integration(s)" + (f" in category '{category}'" if category else "")
    }


def _handle_get_operations(
    manager,
    tool_input: Dict,
    user_id: int
) -> Dict[str, Any]:
    """Handle get_integration_operations tool call."""
    integration_id = tool_input.get('integration_id')
    integration_name = tool_input.get('integration_name')
    
    # Find integration by ID or name
    if not integration_id and integration_name:
        integrations = manager.list_integrations(user_id=user_id)
        matching = [
            i for i in integrations 
            if i['integration_name'].lower() == integration_name.lower()
        ]
        if matching:
            integration_id = matching[0]['integration_id']
        else:
            return {
                "success": False,
                "error": f"Integration '{integration_name}' not found"
            }
    
    if not integration_id:
        return {
            "success": False,
            "error": "Please provide integration_id or integration_name"
        }
    
    operations = manager.get_operations(integration_id)
    
    # Format for agent consumption
    formatted = []
    for op in operations:
        params = []
        for p in op.get('parameters', []):
            params.append({
                "name": p['name'],
                "type": p.get('type', 'text'),
                "required": p.get('required', False),
                "description": p.get('description', p.get('label', ''))
            })
        
        formatted.append({
            "key": op['key'],
            "name": op['name'],
            "description": op.get('description', ''),
            "category": op.get('category', 'read'),  # read/write
            "parameters": params
        })
    
    return {
        "success": True,
        "operations": formatted,
        "count": len(formatted)
    }


def _handle_execute_integration(
    manager,
    tool_input: Dict,
    agent_id: int,
    user_id: int
) -> Dict[str, Any]:
    """Handle execute_integration tool call."""
    integration_id = tool_input.get('integration_id')
    integration_name = tool_input.get('integration_name')
    operation = tool_input.get('operation')
    parameters = tool_input.get('parameters', {})
    
    if not operation:
        return {
            "success": False,
            "error": "Operation is required"
        }
    
    # Find integration by ID or name
    if not integration_id and integration_name:
        integrations = manager.list_integrations(user_id=user_id)
        matching = [
            i for i in integrations 
            if i['integration_name'].lower() == integration_name.lower()
            or i['platform_name'].lower() == integration_name.lower()
        ]
        if matching:
            integration_id = matching[0]['integration_id']
        else:
            # Try partial match
            partial = [
                i for i in integrations
                if integration_name.lower() in i['integration_name'].lower()
                or integration_name.lower() in i['platform_name'].lower()
            ]
            if partial:
                integration_id = partial[0]['integration_id']
            else:
                return {
                    "success": False,
                    "error": f"Integration '{integration_name}' not found. Use list_integrations to see available integrations."
                }
    
    if not integration_id:
        return {
            "success": False,
            "error": "Please provide integration_id or integration_name"
        }
    
    # Execute the operation
    result = manager.execute_operation(
        integration_id=integration_id,
        operation_key=operation,
        parameters=parameters,
        context={
            'agent_id': agent_id,
            'user_id': user_id
        }
    )
    
    # Format response for agent
    if result.get('success'):
        return {
            "success": True,
            "data": result.get('data'),
            "response_time_ms": result.get('response_time_ms'),
            "message": f"Successfully executed '{operation}'"
        }
    else:
        return {
            "success": False,
            "error": result.get('error', 'Operation failed'),
            "message": f"Failed to execute '{operation}': {result.get('error')}"
        }


# =============================================================================
# Tool Registration Helper
# =============================================================================

def register_integration_tools_for_agent(agent_config: Dict) -> List[Dict]:
    """
    Get integration tools configured for a specific agent.
    
    This is called when building the tool list for an agent.
    Only returns tools if the agent has integration access enabled.
    
    Args:
        agent_config: Agent configuration dict
        
    Returns:
        List of tool definitions to add to agent
    """
    # Check if agent has integration tools enabled
    if not agent_config.get('integration_tools_enabled', False):
        return []
    
    # Check if there are any connected integrations for this tenant
    from integration_manager import get_integration_manager
    manager = get_integration_manager()
    
    integrations = manager.list_integrations()
    if not integrations:
        logger.debug("No integrations available, not registering integration tools")
        return []
    
    # Return the integration tool definitions
    return get_integration_tools()


def get_integration_tools_system_prompt_addition(agent_config: Dict) -> str:
    """
    Get additional system prompt text for agents with integration tools.
    
    This adds context about available integrations to help the agent
    use them effectively.
    """
    if not agent_config.get('integration_tools_enabled', False):
        return ""
    
    from integration_manager import get_integration_manager
    manager = get_integration_manager()
    
    integrations = manager.list_integrations()
    if not integrations:
        return ""
    
    # Build a summary of available integrations
    integration_summary = []
    for integ in integrations:
        integration_summary.append(
            f"- {integ['integration_name']} ({integ['platform_name']}): "
            f"{'Connected' if integ.get('is_connected') else 'Disconnected'}"
        )
    
    return f"""

## Available Integrations

You have access to the following connected integrations. Use the integration tools to interact with them:

{chr(10).join(integration_summary)}

When the user asks about data from external systems (invoices, orders, customers, etc.), 
use the appropriate integration to fetch real data. Always:
1. First use list_integrations to confirm what's available
2. Use get_integration_operations to understand what actions you can take
3. Use execute_integration to perform the actual operation

Be specific when reporting data and mention the source integration.
"""


# =============================================================================
# LangChain Tool Wrapper (if using LangChain)
# =============================================================================

def create_langchain_integration_tools():
    """
    Create LangChain-compatible tools for integrations.
    
    Returns tools that can be added to a LangChain agent.
    """
    try:
        from langchain_core.tools import Tool, StructuredTool
        from pydantic import BaseModel, Field
        from typing import Optional
        
        # Define input schemas
        class ListIntegrationsInput(BaseModel):
            category: Optional[str] = Field(None, description="Optional category filter")
        
        class GetOperationsInput(BaseModel):
            integration_id: Optional[int] = Field(None, description="Integration ID")
            integration_name: Optional[str] = Field(None, description="Integration name")
        
        class ExecuteIntegrationInput(BaseModel):
            integration_id: Optional[int] = Field(None, description="Integration ID")
            integration_name: Optional[str] = Field(None, description="Integration name")
            operation: str = Field(..., description="Operation key to execute")
            parameters: Optional[dict] = Field(default_factory=dict, description="Operation parameters")
        
        # Create the tools
        list_tool = StructuredTool.from_function(
            func=lambda category=None: execute_integration_tool(
                "list_integrations", {"category": category}
            ),
            name="list_integrations",
            description="List all connected integrations",
            args_schema=ListIntegrationsInput
        )
        
        get_ops_tool = StructuredTool.from_function(
            func=lambda integration_id=None, integration_name=None: execute_integration_tool(
                "get_integration_operations",
                {"integration_id": integration_id, "integration_name": integration_name}
            ),
            name="get_integration_operations",
            description="Get available operations for an integration",
            args_schema=GetOperationsInput
        )
        
        execute_tool = StructuredTool.from_function(
            func=lambda operation, integration_id=None, integration_name=None, parameters=None: execute_integration_tool(
                "execute_integration",
                {
                    "integration_id": integration_id,
                    "integration_name": integration_name,
                    "operation": operation,
                    "parameters": parameters or {}
                }
            ),
            name="execute_integration",
            description="Execute an operation on a connected integration",
            args_schema=ExecuteIntegrationInput
        )
        
        return [list_tool, get_ops_tool, execute_tool]
        
    except ImportError:
        logger.warning("LangChain not available, cannot create LangChain tools")
        return []
