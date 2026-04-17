# workflow_compiler.py
# Server-side workflow compilation pipeline
# Converts workflow plans directly to saved workflows without requiring a browser canvas.
#
# Pipeline: plan → commands → materialize → validate → fix → save
#
# Supports both CREATE and EDIT modes:
#   CREATE: plan → commands → materialize → validate → fix → save (new workflow)
#   EDIT:   load existing → plan → edit commands → apply on top → validate → fix → save (update)
#
# Used by BuilderAgent to programmatically create/edit workflows via:
#   POST /api/workflow/builder/compile

import json
import logging
import os
from logging.handlers import WatchedFileHandler
from typing import Dict, List, Optional, Tuple

from AppUtils import get_db_connection
from CommandGenerator import CommandGenerator
from workflow_command_validator import validate_workflow
from CommonUtils import rotate_logs_on_startup, get_log_path

rotate_logs_on_startup(os.getenv('WORKFLOW_COMPILER_LOG', get_log_path('workflow_compiler_log.txt')))

logger = logging.getLogger("WorkflowCompiler")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=os.getenv('WORKFLOW_COMPILER_LOG', get_log_path('workflow_compiler_log.txt')))
handler.setFormatter(formatter)
logger.addHandler(handler)


# =========================================================================
# WORKFLOW LOADER: Fetch existing workflow from database
# =========================================================================

def load_workflow_from_database(
    workflow_id: int = None,
    workflow_name: str = None
) -> Tuple[bool, Optional[Dict], str]:
    """
    Load an existing workflow from the database by ID or name.
    
    Args:
        workflow_id: Numeric workflow ID (preferred, exact match)
        workflow_name: Workflow name (fallback, exact then fuzzy match)
        
    Returns:
        Tuple of (success, workflow_dict, error_message)
        workflow_dict has shape: {
            "id": int,
            "name": str,
            "nodes": [...],
            "connections": [...],
            "variables": {}
        }
    """
    if not workflow_id and not workflow_name:
        return False, None, "Either workflow_id or workflow_name is required"
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # ----- Load workflow row -----
        if workflow_id:
            cursor.execute("""
                SELECT id, workflow_name, workflow_data 
                FROM Workflows 
                WHERE id = ?
            """, (workflow_id,))
        else:
            # Exact match first
            cursor.execute("""
                SELECT id, workflow_name, workflow_data 
                FROM Workflows 
                WHERE workflow_name = ?
            """, (workflow_name,))
        
        row = cursor.fetchone()
        
        # Fuzzy match fallback (name only)
        if not row and workflow_name and not workflow_id:
            cursor.execute("""
                SELECT id, workflow_name, workflow_data 
                FROM Workflows 
                WHERE workflow_name LIKE ?
                ORDER BY LEN(workflow_name)
            """, (f"%{workflow_name}%",))
            row = cursor.fetchone()
        
        if not row:
            conn.close()
            identifier = f"ID {workflow_id}" if workflow_id else f"name '{workflow_name}'"
            return False, None, f"Workflow not found: {identifier}"
        
        wf_id, wf_name, wf_data_raw = row[0], row[1], row[2]
        
        # Parse workflow_data JSON
        if isinstance(wf_data_raw, str):
            try:
                workflow_data = json.loads(wf_data_raw)
            except json.JSONDecodeError:
                conn.close()
                return False, None, f"Invalid JSON in workflow_data for workflow {wf_id}"
        elif isinstance(wf_data_raw, dict):
            workflow_data = wf_data_raw
        else:
            conn.close()
            return False, None, f"Unexpected workflow_data type: {type(wf_data_raw)}"
        
        # Validate basic structure
        if not isinstance(workflow_data, dict) or 'nodes' not in workflow_data:
            conn.close()
            return False, None, f"Workflow {wf_id} has invalid structure (missing 'nodes' key)"
        
        # ----- Load variables -----
        variables = {}
        try:
            cursor.execute("""
                SELECT variable_name, variable_type, default_value, description 
                FROM Workflow_Variables 
                WHERE workflow_id = ?
            """, (wf_id,))
            for var_row in cursor.fetchall():
                var_name = var_row[0]
                variables[var_name] = {
                    "type": var_row[1] or "string",
                    "defaultValue": var_row[2] or "",
                    "description": var_row[3] or ""
                }
        except Exception as var_err:
            logger.warning(f"Failed to load variables for workflow {wf_id}: {var_err}")
        
        # Merge variables into workflow_data if not already present
        if variables and not workflow_data.get('variables'):
            workflow_data['variables'] = variables
        
        conn.close()
        
        # Build result with metadata
        result = {
            "id": wf_id,
            "name": wf_name,
            "nodes": workflow_data.get("nodes", []),
            "connections": workflow_data.get("connections", []),
            "variables": workflow_data.get("variables", {})
        }
        
        logger.info(
            f"Loaded workflow '{wf_name}' (ID: {wf_id}): "
            f"{len(result['nodes'])} nodes, {len(result['connections'])} connections"
        )
        
        return True, result, ""
        
    except Exception as e:
        logger.error(f"Error loading workflow: {e}", exc_info=True)
        try:
            conn.close()
        except:
            pass
        return False, None, f"Database error loading workflow: {str(e)}"


# =========================================================================
# STEP 1: Generate commands from plan (uses existing CommandGenerator)
# =========================================================================

def generate_commands_from_plan(
    workflow_plan: str,
    requirements: Dict = None,
    workflow_state: Dict = None
) -> Tuple[bool, Optional[Dict], str]:
    """
    Convert a workflow plan to build commands using CommandGenerator.
    
    Args:
        workflow_plan: The numbered plan text (from <workflow_plan> tags)
        requirements: Optional requirements context dict
        workflow_state: Optional existing workflow state (for modifications)
        
    Returns:
        Tuple of (success, commands_dict, error_message)
        commands_dict has shape: {"action": "build_workflow", "commands": [...]}
    """
    try:
        generator = CommandGenerator()
        result = generator.generate_commands(
            workflow_plan=workflow_plan,
            requirements=requirements,
            workflow_state=workflow_state
        )
        
        if not result or 'commands' not in result:
            return False, None, "CommandGenerator failed to produce commands from plan"
        
        if len(result.get('commands', [])) == 0:
            return False, None, "CommandGenerator produced zero commands"
        
        logger.info(f"Generated {len(result['commands'])} commands from plan")
        return True, result, ""
        
    except Exception as e:
        logger.error(f"Error generating commands: {e}", exc_info=True)
        return False, None, f"Command generation error: {str(e)}"


# =========================================================================
# STEP 2: Resolve IDs (agent names → DB IDs, connection names → DB IDs)
# =========================================================================

def _resolve_agent_id(agent_id_or_name: str) -> str:
    """Resolve agent name to numeric database ID."""
    if str(agent_id_or_name).isdigit():
        return str(agent_id_or_name)
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Exact match first
        cursor.execute("""
            SELECT id FROM Agents 
            WHERE description = ? AND enabled = 1
        """, (agent_id_or_name,))
        result = cursor.fetchone()
        
        # Fuzzy match fallback
        if not result:
            cursor.execute("""
                SELECT id FROM Agents 
                WHERE description LIKE ? AND enabled = 1
                ORDER BY LEN(description)
            """, (f"%{agent_id_or_name}%",))
            result = cursor.fetchone()
        
        conn.close()
        if result:
            logger.info(f"Resolved agent '{agent_id_or_name}' -> ID {result[0]}")
            return str(result[0])
            
    except Exception as e:
        logger.error(f"Error resolving agent ID for '{agent_id_or_name}': {e}")
    
    logger.warning(f"Could not resolve agent '{agent_id_or_name}', defaulting to '1'")
    return "1"


def _resolve_connection_id(conn_id_or_name: str) -> str:
    """Resolve connection name to numeric database ID."""
    if str(conn_id_or_name).isdigit():
        return str(conn_id_or_name)
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Exact match first
        cursor.execute("""
            SELECT id FROM Connections 
            WHERE connection_name = ?
        """, (conn_id_or_name,))
        result = cursor.fetchone()
        
        # Fuzzy match fallback
        if not result:
            cursor.execute("""
                SELECT id FROM Connections 
                WHERE connection_name LIKE ?
                ORDER BY LEN(connection_name)
            """, (f"%{conn_id_or_name}%",))
            result = cursor.fetchone()
        
        conn.close()
        if result:
            logger.info(f"Resolved connection '{conn_id_or_name}' -> ID {result[0]}")
            return str(result[0])
            
    except Exception as e:
        logger.error(f"Error resolving connection ID for '{conn_id_or_name}': {e}")
    
    logger.warning(f"Could not resolve connection '{conn_id_or_name}', defaulting to '1'")
    return "1"


def resolve_command_ids(commands: List[Dict]) -> List[Dict]:
    """
    Post-process commands to resolve human-readable names to database IDs.
    Modifies commands in-place and returns them.
    
    Resolves:
        - config.agent_id: agent name → Agents table ID
        - config.dbConnection: connection name → Connections table ID
    """
    for command in commands:
        if command.get("type") in ("add_node", "update_node_config"):
            config = command.get("config", {})
            
            if "agent_id" in config and not str(config["agent_id"]).isdigit():
                config["agent_id"] = _resolve_agent_id(config["agent_id"])
                
            if "dbConnection" in config and not str(config["dbConnection"]).isdigit():
                config["dbConnection"] = _resolve_connection_id(config["dbConnection"])
    
    return commands


# =========================================================================
# STEP 3: Materialize commands into workflow_data
# =========================================================================

def materialize_commands(commands_json: Dict, base_workflow: Dict = None) -> Dict:
    """
    Convert workflow build commands into a saveable workflow_data structure.
    This is the server-side equivalent of what the frontend canvas + jsPlumb does.
    
    Takes the output of CommandGenerator and produces the exact data shape
    that /save/workflow and save_workflow_to_database() expect.
    
    Args:
        commands_json: {"action": "build_workflow", "commands": [...]}
        base_workflow: Optional existing workflow data to build on top of (edit mode).
                       If provided, commands are applied incrementally over this base.
        
    Returns:
        {
            "nodes": [...],
            "connections": [...],
            "variables": {}
        }
    """
    # Initialize from base workflow if editing, otherwise start empty
    if base_workflow:
        nodes = {n["id"]: dict(n) for n in base_workflow.get("nodes", [])}
        connections = [dict(c) for c in base_workflow.get("connections", [])]
        variables = dict(base_workflow.get("variables", {}))
        start_node_id = None
        for n in base_workflow.get("nodes", []):
            if n.get("isStart"):
                start_node_id = n["id"]
                break
        logger.info(f"Edit mode: starting from base with {len(nodes)} nodes, {len(connections)} connections")
    else:
        nodes = {}
        connections = []
        variables = {}
        start_node_id = None
    
    commands = commands_json.get("commands", [])
    logger.info(f"Materializing {len(commands)} commands" + (" (edit mode)" if base_workflow else " (create mode)"))
    
    for i, cmd in enumerate(commands):
        cmd_type = cmd.get("type")
        
        if cmd_type == "add_node":
            node_id = cmd.get("node_id", f"node-{i}")
            position = cmd.get("position", {"left": "20px", "top": "40px"})
            
            if node_id in nodes and base_workflow:
                logger.debug(f"  Replacing existing node: {node_id}")
            
            nodes[node_id] = {
                "id": node_id,
                "type": cmd.get("node_type", ""),
                "label": cmd.get("label", cmd.get("node_type", "")),
                "position": position,
                "config": cmd.get("config", {}),
                "isStart": False
            }
            logger.debug(f"  Added node: {node_id} ({cmd.get('node_type')})")
        
        elif cmd_type == "connect_nodes":
            from_id = cmd.get("from", "")
            to_id = cmd.get("to", "")
            conn_type = cmd.get("connection_type", "pass")
            
            # Avoid duplicate connections
            already_exists = any(
                c.get("source", c.get("from")) == from_id
                and c.get("target", c.get("to")) == to_id
                and c.get("type") == conn_type
                for c in connections
            )
            if not already_exists:
                connections.append({
                    "source": from_id,
                    "target": to_id,
                    "type": conn_type,
                    "sourceAnchor": "Right",
                    "targetAnchor": "Left"
                })
                logger.debug(f"  Connected: {from_id} -> {to_id} ({conn_type})")
            else:
                logger.debug(f"  Skipped duplicate connection: {from_id} -> {to_id} ({conn_type})")
        
        elif cmd_type == "set_start_node":
            start_node_id = cmd.get("node_id")
            # Clear previous start flag
            for n in nodes.values():
                n["isStart"] = False
            logger.debug(f"  Start node: {start_node_id}")
        
        elif cmd_type == "add_variable":
            var_name = cmd.get("name", "")
            variables[var_name] = {
                "type": cmd.get("data_type", "string"),
                "defaultValue": cmd.get("default_value", ""),
                "description": cmd.get("description", "")
            }
            logger.debug(f"  Added variable: {var_name}")
        
        elif cmd_type == "update_node_config":
            node_id = cmd.get("node_id", "")
            if node_id in nodes:
                existing_config = nodes[node_id].get("config", {})
                existing_config.update(cmd.get("config", {}))
                nodes[node_id]["config"] = existing_config
                # Also update label if provided
                if cmd.get("label"):
                    nodes[node_id]["label"] = cmd["label"]
                logger.debug(f"  Updated config: {node_id}")
            else:
                logger.warning(f"  update_node_config: node {node_id} not found")
        
        elif cmd_type == "delete_node":
            node_id = cmd.get("node_id", "")
            if node_id in nodes:
                del nodes[node_id]
                # Remove connections involving this node
                connections = [
                    c for c in connections
                    if c.get("source", c.get("from")) != node_id
                    and c.get("target", c.get("to")) != node_id
                ]
                if start_node_id == node_id:
                    start_node_id = None
                logger.debug(f"  Deleted node: {node_id}")
        
        elif cmd_type == "delete_connection":
            from_id = cmd.get("from", "")
            to_id = cmd.get("to", "")
            del_type = cmd.get("connection_type")
            connections = [
                c for c in connections
                if not (
                    c.get("source", c.get("from")) == from_id
                    and c.get("target", c.get("to")) == to_id
                    and (del_type is None or c.get("type") == del_type)
                )
            ]
            logger.debug(f"  Deleted connection: {from_id} -> {to_id}")
        
        elif cmd_type == "delete_variable":
            var_name = cmd.get("name", "")
            if var_name in variables:
                del variables[var_name]
                logger.debug(f"  Deleted variable: {var_name}")
        
        else:
            logger.warning(f"  Unknown command type: {cmd_type}")
    
    # Mark start node
    if start_node_id and start_node_id in nodes:
        nodes[start_node_id]["isStart"] = True
    elif not any(n.get("isStart") for n in nodes.values()) and nodes:
        # Fallback: mark first node as start if nothing is marked
        first_id = next(iter(nodes))
        nodes[first_id]["isStart"] = True
        logger.warning(f"No start node specified, defaulting to {first_id}")
    
    workflow_data = {
        "nodes": list(nodes.values()),
        "connections": connections,
        "variables": variables
    }
    
    logger.info(
        f"Materialized: {len(workflow_data['nodes'])} nodes, "
        f"{len(workflow_data['connections'])} connections, "
        f"{len(workflow_data['variables'])} variables"
    )
    
    return workflow_data


# =========================================================================
# STEP 4: Convert materialized workflow_data to validation format
# =========================================================================

def _to_validation_format(workflow_data: Dict) -> Dict:
    """
    Convert materialized workflow_data to the format expected by validate_workflow().
    
    The validator expects connections with 'from'/'to' keys.
    The save format uses 'source'/'target' keys.
    """
    validation_state = {
        "nodes": workflow_data.get("nodes", []),
        "connections": []
    }
    
    for conn in workflow_data.get("connections", []):
        validation_state["connections"].append({
            "from": conn.get("source", conn.get("from", "")),
            "to": conn.get("target", conn.get("to", "")),
            "type": conn.get("type", "pass")
        })
    
    return validation_state


# =========================================================================
# STEP 5: Request fixes from CommandGenerator when validation fails
# =========================================================================

def _generate_fix_commands(
    errors: List[str],
    warnings: List[str],
    workflow_data: Dict
) -> Optional[Dict]:
    """
    Generate fix commands for validation issues by sending the errors
    and current workflow state back to CommandGenerator.
    
    Returns commands dict or None if fix generation fails.
    """
    try:
        # Build a fix plan that describes what needs to change
        fix_plan = "Fix the following issues in the existing workflow:\n\n"
        
        if errors:
            fix_plan += "ERRORS (must fix):\n"
            for i, err in enumerate(errors, 1):
                fix_plan += f"  {i}. {err}\n"
            fix_plan += "\n"
        
        if warnings:
            fix_plan += "WARNINGS (should fix):\n"
            for i, warn in enumerate(warnings, 1):
                fix_plan += f"  {i}. {warn}\n"
            fix_plan += "\n"
        
        fix_plan += "Generate ONLY the commands needed to fix these issues. "
        fix_plan += "Do NOT recreate nodes that already exist. "
        fix_plan += "Use update_node_config, connect_nodes, delete_connection, or delete_node as needed."
        
        generator = CommandGenerator()
        result = generator.generate_commands(
            workflow_plan=fix_plan,
            workflow_state={
                "nodes": workflow_data.get("nodes", []),
                "connections": workflow_data.get("connections", [])
            }
        )
        
        if result and result.get("commands"):
            logger.info(f"Generated {len(result['commands'])} fix commands")
            return result
        
        return None
        
    except Exception as e:
        logger.error(f"Error generating fix commands: {e}", exc_info=True)
        return None


# =========================================================================
# STEP 6: Save workflow to database and file
# =========================================================================

def save_compiled_workflow(
    workflow_name: str,
    workflow_data: Dict,
    workflow_id: int = None
) -> Tuple[bool, Optional[int], str]:
    """
    Save compiled workflow to database and JSON file.
    Replicates the logic from app.py save_workflow_to_database() + save_to_file().
    
    When workflow_id is provided, updates by ID (ensures correct row on rename).
    Otherwise falls back to MERGE by workflow_name (upsert).
    
    Args:
        workflow_name: Name for the workflow (also used as filename)
        workflow_data: The materialized workflow data {nodes, connections, variables}
        workflow_id: Optional existing workflow ID (for guaranteed update targeting)
        
    Returns:
        Tuple of (success, workflow_id, error_message)
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        workflow_json = json.dumps(workflow_data)
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        if workflow_id:
            # Direct update by ID (edit mode — ID is known)
            cursor.execute("""
                UPDATE Workflows 
                SET workflow_data = ?, 
                    workflow_name = ?,
                    last_modified = getutcdate(), 
                    version = version + 1
                WHERE id = ?
            """, (workflow_json, workflow_name, workflow_id))
            
            if cursor.rowcount == 0:
                # ID not found, fall through to MERGE
                logger.warning(f"Update by ID {workflow_id} matched 0 rows, falling back to MERGE")
                workflow_id = None
        
        if not workflow_id:
            # Upsert by name (create mode, or fallback)
            workflow_sql = """
            MERGE INTO Workflows AS target
            USING (VALUES (?, ?, getutcdate(), ?)) AS source (workflow_name, workflow_data, last_modified, version)
            ON target.workflow_name = source.workflow_name
            WHEN MATCHED THEN
                UPDATE SET 
                    workflow_data = source.workflow_data,
                    last_modified = source.last_modified,
                    version = target.version + 1
            WHEN NOT MATCHED THEN
                INSERT (workflow_name, workflow_data, last_modified, version)
                VALUES (source.workflow_name, source.workflow_data, source.last_modified, source.version);
            """
            cursor.execute(workflow_sql, (workflow_name, workflow_json, 1))
        
        # Get the workflow_id
        cursor.execute("SELECT id workflow_id FROM Workflows WHERE workflow_name = ?;", (workflow_name,))
        row = cursor.fetchone()
        if row:
            workflow_id = row[0]
        
        # Save variables if present
        if workflow_id and workflow_data.get('variables'):
            cursor.execute("DELETE FROM Workflow_Variables WHERE workflow_id = ?", (workflow_id,))
            
            for var_name, var_info in workflow_data['variables'].items():
                var_type = var_info.get('type', 'string')
                default_value = var_info.get('defaultValue', '')
                description = var_info.get('description', '')
                
                if not isinstance(default_value, str):
                    try:
                        default_value = json.dumps(default_value)
                    except:
                        default_value = str(default_value)
                
                cursor.execute("""
                    INSERT INTO Workflow_Variables 
                    (workflow_id, variable_name, variable_type, default_value, description, created_date, last_modified)
                    VALUES (?, ?, ?, ?, ?, getutcdate(), getutcdate())
                """, (workflow_id, var_name, var_type, default_value, description))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Saved workflow to database: {workflow_name} (ID: {workflow_id})")
        
        # ----- Save to file -----
        try:
            workflows_dir = os.path.join(os.path.dirname(__file__), 'workflows')
            os.makedirs(workflows_dir, exist_ok=True)
            
            filename = workflow_name if workflow_name.endswith('.json') else f"{workflow_name}.json"
            file_path = os.path.join(workflows_dir, filename)
            
            with open(file_path, 'w') as f:
                json.dump(workflow_data, f, indent=2)
            
            logger.info(f"Saved workflow to file: {file_path}")
        except Exception as file_err:
            # File save is non-critical, log but don't fail
            logger.warning(f"Failed to save workflow file (non-critical): {file_err}")
        
        return True, workflow_id, ""
        
    except Exception as e:
        logger.error(f"Error saving workflow: {e}", exc_info=True)
        try:
            conn.rollback()
            conn.close()
        except:
            pass
        return False, None, f"Database save error: {str(e)}"


# =========================================================================
# MAIN ORCHESTRATOR: compile_workflow()
# =========================================================================

def compile_workflow(
    workflow_plan: str,
    workflow_name: str,
    requirements: Dict = None,
    save: bool = True,
    max_fix_attempts: int = 2,
    workflow_id: int = None
) -> Dict:
    """
    Full compilation pipeline: plan → commands → materialize → validate → fix → save.
    
    Supports two modes:
        CREATE mode (default): Builds a new workflow from scratch.
        EDIT mode (workflow_id provided): Loads existing workflow, applies edit commands
            on top, validates, and saves the updated version.
    
    Args:
        workflow_plan: The workflow plan text (from <workflow_plan> tags)
        workflow_name: Name for the workflow
        requirements: Optional requirements dict for additional context
        save: Whether to save to database (default True)
        max_fix_attempts: Max validation fix retries (default 2)
        workflow_id: Existing workflow ID to edit (triggers edit mode)
        
    Returns:
        {
            "success": bool,
            "workflow_id": int or None,
            "workflow_name": str,
            "workflow_data": {...} or None,
            "commands": {...} or None,
            "validation": {"is_valid": bool, "errors": [...], "warnings": [...]},
            "fix_attempts": int,
            "mode": "create" or "edit",
            "error": str or None
        }
    """
    is_edit = workflow_id is not None
    mode = "edit" if is_edit else "create"
    
    result = {
        "success": False,
        "workflow_id": workflow_id,
        "workflow_name": workflow_name,
        "workflow_data": None,
        "commands": None,
        "validation": None,
        "fix_attempts": 0,
        "mode": mode,
        "error": None
    }
    
    logger.info(f"=" * 80)
    logger.info(f"COMPILE WORKFLOW [{mode.upper()}]: {workflow_name}")
    if is_edit:
        logger.info(f"Editing workflow ID: {workflow_id}")
    logger.info(f"Plan length: {len(workflow_plan)} chars")
    logger.info(f"=" * 80)
    
    # ----- STEP 0 (edit mode only): Load existing workflow -----
    existing_workflow = None
    
    if is_edit:
        logger.info("STEP 0: Loading existing workflow...")
        
        load_success, existing_workflow, load_error = load_workflow_from_database(
            workflow_id=workflow_id
        )
        
        if not load_success:
            result["error"] = load_error
            logger.error(f"FAILED at Step 0: {load_error}")
            return result
        
        # Use the stored name if caller didn't provide a new one,
        # but allow rename if a different name was passed
        if not workflow_name or workflow_name == str(workflow_id):
            workflow_name = existing_workflow["name"]
            result["workflow_name"] = workflow_name
        
        logger.info(
            f"STEP 0 complete: loaded '{existing_workflow['name']}' "
            f"({len(existing_workflow['nodes'])} nodes, "
            f"{len(existing_workflow['connections'])} connections)"
        )
    
    # ----- STEP 1: Generate commands from plan -----
    logger.info("STEP 1: Generating commands from plan...")
    
    # In edit mode, pass existing workflow as context so CommandGenerator
    # knows about existing nodes and generates only delta commands
    workflow_state_for_gen = existing_workflow if is_edit else None
    
    gen_success, commands_json, gen_error = generate_commands_from_plan(
        workflow_plan=workflow_plan,
        requirements=requirements,
        workflow_state=workflow_state_for_gen
    )
    
    if not gen_success:
        result["error"] = gen_error
        logger.error(f"FAILED at Step 1: {gen_error}")
        return result
    
    result["commands"] = commands_json
    logger.info(f"STEP 1 complete: {len(commands_json.get('commands', []))} commands generated")
    
    # ----- STEP 2: Resolve IDs -----
    logger.info("STEP 2: Resolving IDs...")
    
    commands_json["commands"] = resolve_command_ids(commands_json["commands"])
    logger.info("STEP 2 complete: IDs resolved")
    
    # ----- STEP 3: Materialize commands into workflow_data -----
    logger.info("STEP 3: Materializing commands...")
    
    # In edit mode, pass existing workflow as the base to build on top of.
    # In create mode, base_workflow is None → starts from scratch.
    workflow_data = materialize_commands(commands_json, base_workflow=existing_workflow)
    result["workflow_data"] = workflow_data
    logger.info(f"STEP 3 complete: {len(workflow_data['nodes'])} nodes materialized")
    
    # ----- STEP 4: Validate -----
    logger.info("STEP 4: Validating workflow...")
    
    validation_state = _to_validation_format(workflow_data)
    is_valid, validation_result = validate_workflow(validation_state)
    result["validation"] = validation_result
    
    errors = validation_result.get("errors", [])
    warnings = validation_result.get("warnings", [])
    
    logger.info(
        f"STEP 4 complete: valid={is_valid}, "
        f"errors={len(errors)}, warnings={len(warnings)}"
    )
    
    # ----- STEP 5: Fix loop (if validation failed) -----
    fix_attempt = 0
    while not is_valid and fix_attempt < max_fix_attempts:
        fix_attempt += 1
        result["fix_attempts"] = fix_attempt
        
        logger.info(f"STEP 5: Fix attempt {fix_attempt}/{max_fix_attempts}...")
        logger.info(f"  Errors to fix: {errors}")
        
        fix_commands = _generate_fix_commands(errors, warnings, workflow_data)
        
        if not fix_commands or not fix_commands.get("commands"):
            logger.warning(f"  No fix commands generated, stopping fix loop")
            break
        
        logger.info(f"  Applying {len(fix_commands['commands'])} fix commands...")
        
        # Resolve IDs in fix commands too
        fix_commands["commands"] = resolve_command_ids(fix_commands["commands"])
        
        # Apply fixes on top of current workflow_data
        workflow_data = materialize_commands(fix_commands, base_workflow=workflow_data)
        result["workflow_data"] = workflow_data
        
        # Re-validate
        validation_state = _to_validation_format(workflow_data)
        is_valid, validation_result = validate_workflow(validation_state)
        result["validation"] = validation_result
        
        errors = validation_result.get("errors", [])
        warnings = validation_result.get("warnings", [])
        
        logger.info(
            f"  After fix {fix_attempt}: valid={is_valid}, "
            f"errors={len(errors)}, warnings={len(warnings)}"
        )
    
    # ----- STEP 6: Save -----
    if save:
        logger.info("STEP 6: Saving workflow...")
        
        save_success, saved_id, save_error = save_compiled_workflow(
            workflow_name=workflow_name,
            workflow_data=workflow_data,
            workflow_id=workflow_id if is_edit else None
        )
        
        if not save_success:
            result["error"] = save_error
            logger.error(f"FAILED at Step 6: {save_error}")
            return result
        
        result["workflow_id"] = saved_id
        logger.info(f"STEP 6 complete: saved as ID {saved_id}")
    else:
        logger.info("STEP 6: Skipped (save=False)")
    
    # ----- Final result -----
    result["success"] = True
    
    logger.info(f"=" * 80)
    logger.info(f"COMPILE COMPLETE [{mode.upper()}]: {workflow_name}")
    logger.info(f"  Nodes: {len(workflow_data['nodes'])}")
    logger.info(f"  Connections: {len(workflow_data['connections'])}")
    logger.info(f"  Variables: {len(workflow_data['variables'])}")
    logger.info(f"  Fix attempts: {fix_attempt}")
    logger.info(f"  Valid: {is_valid}")
    logger.info(f"  Workflow ID: {result['workflow_id']}")
    logger.info(f"=" * 80)
    
    return result
