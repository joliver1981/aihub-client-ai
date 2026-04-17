"""
Workflow Command Compiler
==========================
Server-side compiler that converts workflow commands (add_node, connect_nodes,
set_start_node) into a workflow data structure that can be saved via POST /save/workflow.

This is the server-side equivalent of the browser's WorkflowCommandExecutor (JS).
The browser executor manipulates the DOM canvas; this compiler produces the JSON
structure that the /save/workflow endpoint expects.

Usage:
    from execution.workflow_compiler import compile_workflow_commands

    workflow_data = compile_workflow_commands(commands, workflow_name="My Workflow")
    # Returns: {"filename": "My Workflow.json", "workflow": {"nodes": [...], "connections": [...], "variables": {}}}
"""

import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Canonical list of valid workflow node types
# (mirrors static/js/workflow.js nodeConfigTemplates and system_prompts.VALID_WORKFLOW_NODE_TYPES)
VALID_NODE_TYPES: Set[str] = {
    "Database", "AI Action", "AI Extract", "Document", "Loop", "End Loop",
    "Conditional", "Human Approval", "Alert", "Folder Selector", "File",
    "Set Variable", "Execute Application", "Excel Export", "Server",
    "Integration",
}


def compile_workflow_commands(
    commands: List[Dict[str, Any]],
    workflow_name: str = "Untitled Workflow",
) -> Dict[str, Any]:
    """
    Compile a list of workflow commands into a saveable workflow structure.

    Args:
        commands: List of command dicts (add_node, connect_nodes, set_start_node)
        workflow_name: Name for the workflow file

    Returns:
        Dict with "filename" and "workflow" keys ready for POST /save/workflow
    """
    nodes: List[Dict[str, Any]] = []
    connections: List[Dict[str, Any]] = []
    variables: Dict[str, Any] = {}
    start_node_id: Optional[str] = None
    node_id_map: Dict[str, str] = {}  # temp_id -> actual_id (identity for now)

    for i, cmd in enumerate(commands):
        cmd_type = cmd.get("type", "")

        if cmd_type == "add_node":
            node = _compile_add_node(cmd)
            if node is None:
                # Invalid node type — skip this command
                continue
            nodes.append(node)
            # Track node ID for reference
            temp_id = cmd.get("node_id", f"node-{i}")
            node_id_map[temp_id] = node["id"]

        elif cmd_type == "connect_nodes":
            connection = _compile_connect_nodes(cmd, node_id_map)
            if connection:
                connections.append(connection)

        elif cmd_type == "set_start_node":
            start_node_id = cmd.get("node_id")

        elif cmd_type == "set_variable":
            var_name = cmd.get("name", "")
            if var_name:
                variables[var_name] = {
                    "type": cmd.get("var_type", "string"),
                    "defaultValue": cmd.get("default_value", ""),
                    "description": cmd.get("description", ""),
                }

        elif cmd_type == "remove_node":
            remove_id = cmd.get("node_id")
            nodes = [n for n in nodes if n["id"] != remove_id]
            connections = [
                c for c in connections
                if c["source"] != remove_id and c["target"] != remove_id
            ]

        elif cmd_type == "update_config":
            target_id = cmd.get("node_id")
            new_config = cmd.get("config", {})
            for node in nodes:
                if node["id"] == target_id:
                    node["config"].update(new_config)
                    break

        else:
            logger.warning(f"  [workflow_compiler] Unknown command type: {cmd_type}")

    # Apply start node
    if start_node_id:
        for node in nodes:
            node["isStart"] = (node["id"] == start_node_id)
    elif nodes:
        # Default: first node is start
        nodes[0]["isStart"] = True

    workflow_data = {
        "nodes": nodes,
        "connections": connections,
        "variables": variables,
    }

    # Build the filename (the save endpoint strips .json to get the name)
    filename = f"{workflow_name}.json"

    logger.info(
        f"  [workflow_compiler] Compiled {len(commands)} commands → "
        f"{len(nodes)} nodes, {len(connections)} connections, "
        f"{len(variables)} variables"
    )

    return {
        "filename": filename,
        "workflow": workflow_data,
    }


def _compile_add_node(cmd: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert an add_node command to a workflow node dict.

    Returns None if the node type is not valid (e.g., hallucinated types like "Trigger").
    """
    node_id = cmd.get("node_id", "node-0")
    node_type = cmd.get("node_type", "Unknown")
    label = cmd.get("label", node_type)
    position = cmd.get("position", {"left": "20px", "top": "40px"})
    config = cmd.get("config", {})

    if node_type not in VALID_NODE_TYPES:
        logger.warning(
            f"  [workflow_compiler] Skipping node '{node_id}' with invalid type "
            f"'{node_type}'. Valid types: {sorted(VALID_NODE_TYPES)}"
        )
        return None

    return {
        "id": node_id,
        "type": node_type,
        "label": label,
        "position": position,
        "config": config,
        "isStart": False,
    }


def _compile_connect_nodes(
    cmd: Dict[str, Any],
    node_id_map: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    """Convert a connect_nodes command to a workflow connection dict."""
    source = cmd.get("from", "")
    target = cmd.get("to", "")
    conn_type = cmd.get("connection_type", "pass")

    # Resolve through node_id_map in case IDs were remapped
    source = node_id_map.get(source, source)
    target = node_id_map.get(target, target)

    if not source or not target:
        logger.warning(f"  [workflow_compiler] Invalid connection: {source} → {target}")
        return None

    return {
        "source": source,
        "target": target,
        "type": conn_type,
        "sourceAnchor": "Right",
        "targetAnchor": "Left",
    }
