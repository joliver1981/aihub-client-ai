# workflow_command_validator.py
# Workflow command validation with a deterministic pre-pass + LLM fallback.
#
# Default flow (config.WORKFLOW_VALIDATOR_DETERMINISTIC_ENABLED=True):
#   1. Run workflow_deterministic_validator.run() on the workflow state.
#      It emits structured Issues and, where it can, fix_commands.
#   2. If the deterministic pre-pass found nothing OR fixed everything (no
#      unfixable errors) AND WORKFLOW_VALIDATOR_ALWAYS_RUN_LLM is False,
#      return the result without calling the LLM. This is the fast path.
#   3. Otherwise call the LLM validator as a fallback, merge its output, and
#      return the combined result.
#   4. Any exception in the deterministic path falls through to the LLM.
#
# Disable the deterministic pre-pass entirely by setting
# config.WORKFLOW_VALIDATOR_DETERMINISTIC_ENABLED=False - the original LLM-only
# behavior is preserved verbatim in _validate_workflow_with_llm().

import os
import json
import logging
from logging.handlers import WatchedFileHandler
from typing import Dict, Optional, Tuple, List

from AppUtils import azureQuickPrompt, azureMiniQuickPrompt
import system_prompts as sp
from CommonUtils import rotate_logs_on_startup, get_all_node_details, get_log_path
import config as cfg
import workflow_deterministic_validator as det_val


rotate_logs_on_startup(os.getenv('WORKFLOW_VALIDATION_LOG', get_log_path('workflow_validation_log.txt')))

# Configure logging
logger = logging.getLogger("WorkflowValidation")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=os.getenv('WORKFLOW_VALIDATION_LOG', get_log_path('workflow_validation_log.txt')), encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)


def check_missing_save_to_variable(workflow_state: Dict) -> List[str]:
    """
    Check for File/Database nodes that have outputVariable but are missing saveToVariable.
    This catches a common AI Builder omission that prevents variables from being stored.

    Returns list of warning messages.
    """
    import re
    warnings = []
    for node in workflow_state.get('nodes', []):
        config = node.get('config', {})
        node_type = node.get('type', '')
        node_id = node.get('id', '?')
        label = node.get('label', node_id)

        if node_type in ('File', 'Database'):
            output_var = config.get('outputVariable', '')
            save_to_var = config.get('saveToVariable')

            if output_var and not save_to_var:
                warnings.append(
                    f"MISSING CONFIG: {node_id} ({label}) [{node_type}] has outputVariable "
                    f"'{output_var}' but saveToVariable is not set to true. "
                    f"The output will not be stored. Add saveToVariable: true to fix.")
                logger.info(f"Missing saveToVariable found: {node_id} ({label})")

    return warnings


def check_variable_references(workflow_state: Dict) -> List[str]:
    """
    Check that all ${varName} references in node configs can potentially be resolved
    by variables defined by other nodes (outputVariable, variableName, itemVariable).

    Returns list of warning messages for unresolvable references.
    """
    import re
    warnings = []
    defined_vars = set()

    # Built-in variables that are always available
    defined_vars.update(['_previousStepOutput', '_loopStats', 'currentIndex', 'currentItem'])

    # Collect all variables that nodes define
    for node in workflow_state.get('nodes', []):
        config = node.get('config', {})
        node_type = node.get('type', '')

        # outputVariable (File, Database, AI Action, AI Extract, Folder Selector, etc.)
        if config.get('outputVariable'):
            var_name = config['outputVariable'].replace('${', '').replace('}', '').strip()
            if var_name:
                defined_vars.add(var_name)

        # variableName (Set Variable)
        if config.get('variableName'):
            var_name = config['variableName'].replace('${', '').replace('}', '').strip()
            if var_name:
                defined_vars.add(var_name)

        # Loop node: itemVariable and indexVariable
        if node_type == 'Loop':
            if config.get('itemVariable'):
                item_var = config['itemVariable'].replace('${', '').replace('}', '').strip()
                if item_var:
                    defined_vars.add(item_var)
            if config.get('indexVariable'):
                idx_var = config['indexVariable'].replace('${', '').replace('}', '').strip()
                if idx_var:
                    defined_vars.add(idx_var)

        # Document node: outputPath can define a variable
        if node_type == 'Document' and config.get('outputPath'):
            var_name = config['outputPath'].replace('${', '').replace('}', '').strip()
            if var_name:
                defined_vars.add(var_name)

    logger.debug(f"Variable reference check: Defined variables: {defined_vars}")

    # Check all ${varName} references in configs
    for node in workflow_state.get('nodes', []):
        config = node.get('config', {})
        node_id = node.get('id', '?')
        label = node.get('label', node_id)

        for key, value in config.items():
            if isinstance(value, str) and '${' in value:
                # Extract all variable references (just the root variable name, not nested paths)
                refs = re.findall(r'\$\{([^}.]+)', value)
                for ref in refs:
                    ref_clean = ref.strip()
                    if ref_clean and ref_clean not in defined_vars:
                        warnings.append(
                            f"UNRESOLVED VARIABLE: {node_id} ({label}) references "
                            f"${{{ref_clean}}} in config field '{key}' but no node defines "
                            f"this variable. Check spelling or add a node that creates it.")
                        logger.info(f"Unresolved variable found: ${{{ref_clean}}} in {node_id}.{key}")

    return warnings


def check_duplicate_connections(workflow_state: Dict) -> List[str]:
    """
    Check for nodes with multiple outgoing connections of the same type.
    Note: 'pass' and 'complete' are considered the same type.
    
    Returns list of error messages for duplicate connections.
    """
    connections = workflow_state.get('connections', [])
    nodes = {n['id']: n for n in workflow_state.get('nodes', [])}
    
    logger.debug(f"Duplicate connection check: Analyzing {len(connections)} connections")
    
    # Track outgoing connections by node and normalized type
    # Key: (from_node, normalized_type) -> list of to_nodes
    outgoing = {}
    
    for conn in connections:
        from_id = conn.get('from')
        to_id = conn.get('to')
        conn_type = conn.get('type', 'pass')
        
        # Normalize: treat 'pass' and 'complete' as the same type
        normalized_type = 'pass_or_complete' if conn_type in ('pass', 'complete') else conn_type
        
        key = (from_id, normalized_type)
        if key not in outgoing:
            outgoing[key] = []
        outgoing[key].append({'to': to_id, 'original_type': conn_type})
    
    logger.debug(f"Duplicate connection check: Outgoing connections by (node, type): {outgoing}")
    
    # Find duplicates
    errors = []
    for (from_id, normalized_type), targets in outgoing.items():
        if len(targets) > 1:
            node_label = nodes.get(from_id, {}).get('label', from_id)
            target_info = [f"{t['to']} ({t['original_type']})" for t in targets]
            
            if normalized_type == 'pass_or_complete':
                error_msg = f"DUPLICATE CONNECTION: {from_id} ({node_label}) has multiple pass/complete connections to: {', '.join(target_info)}"
            else:
                error_msg = f"DUPLICATE CONNECTION: {from_id} ({node_label}) has multiple {normalized_type} connections to: {', '.join(target_info)}"
            
            errors.append(error_msg)
            logger.debug(f"Duplicate connection check: Found - {error_msg}")
    
    if not errors:
        logger.debug("Duplicate connection check: No duplicates found")
    
    return errors


def check_connectivity(workflow_state: Dict) -> List[str]:
    """
    Check that all nodes are connected to the main workflow (reachable from start node).
    Returns list of disconnected node IDs that need connections.
    Only returns the "entry point" nodes of disconnected subgraphs, not all nodes in them.
    """
    nodes = {n['id']: n for n in workflow_state.get('nodes', [])}
    connections = workflow_state.get('connections', [])
    
    logger.debug(f"Connectivity check: {len(nodes)} nodes, {len(connections)} connections")
    
    if not nodes:
        logger.debug("Connectivity check: No nodes found, skipping")
        return []
    
    # Find start node
    start_node_id = None
    for node in workflow_state.get('nodes', []):
        if node.get('isStart'):
            start_node_id = node['id']
            break
    
    if not start_node_id:
        logger.debug("Connectivity check: No start node found, skipping")
        return []  # No start node - AI validation will catch this
    
    logger.debug(f"Connectivity check: Start node is {start_node_id}")
    
    # Build adjacency list (from -> [to nodes])
    adj = {node_id: [] for node_id in nodes}
    for conn in connections:
        from_id = conn.get('from')
        to_id = conn.get('to')
        if from_id in adj and to_id:
            adj[from_id].append(to_id)
    
    logger.debug(f"Connectivity check: Adjacency list: {adj}")
    
    # BFS from start node to find all reachable nodes
    reachable = set()
    queue = [start_node_id]
    while queue:
        current = queue.pop(0)
        if current in reachable:
            continue
        reachable.add(current)
        for neighbor in adj.get(current, []):
            if neighbor not in reachable:
                queue.append(neighbor)
    
    logger.debug(f"Connectivity check: Reachable nodes from start: {reachable}")
    
    # Find disconnected nodes
    all_nodes = set(nodes.keys())
    disconnected = all_nodes - reachable
    
    logger.debug(f"Connectivity check: All nodes: {all_nodes}")
    logger.debug(f"Connectivity check: Disconnected nodes: {disconnected}")
    
    if not disconnected:
        logger.debug("Connectivity check: All nodes are connected!")
        return []  # All nodes connected
    
    # Find "entry points" in disconnected subgraphs
    # These are nodes with no incoming connections from OTHER disconnected nodes
    # They are the nodes that need a connection FROM the main workflow
    incoming_from_disconnected = {node_id: set() for node_id in disconnected}
    for conn in connections:
        from_id = conn.get('from')
        to_id = conn.get('to')
        if from_id in disconnected and to_id in disconnected:
            incoming_from_disconnected[to_id].add(from_id)
    
    logger.debug(f"Connectivity check: Incoming edges within disconnected subgraph: {incoming_from_disconnected}")
    
    # Entry points have no incoming edges from other disconnected nodes
    entry_points = [
        node_id for node_id in disconnected 
        if len(incoming_from_disconnected[node_id]) == 0
    ]
    
    logger.debug(f"Connectivity check: Entry points needing connection: {entry_points}")
    
    return entry_points


def _validate_workflow_with_llm(workflow_state: Dict) -> Tuple[bool, Dict]:
    """
    Original LLM-based workflow validator (now a private fallback).

    Calls the validation LLM with the full workflow state, then layers Python
    connectivity / duplicate-connection / save-to-variable / variable-reference
    checks on top. Returns (is_valid, result_dict) with errors and warnings.

    This is the fallback used when the deterministic pre-pass leaves residual
    issues, when WORKFLOW_VALIDATOR_DETERMINISTIC_ENABLED is False, or when
    the deterministic pre-pass raises an exception.
    """
    try:
        logger.debug(f"Validating workflow with {len(workflow_state.get('nodes', []))} nodes")
        
        prompt = f"""WORKFLOW STATE TO VALIDATE:
{json.dumps(workflow_state, indent=2)}

Analyze this workflow and respond with JSON only:
{{
    "is_valid": true/false,
    "errors": ["list of errors found"],
    "warnings": ["list of warnings"]
}}"""
        SYSTEM_PROMPT = sp.WORKFLOW_VALIDATION_SYSTEM.replace("<<workflow_node_types>>", get_all_node_details())

        logger.debug(f"Validating workflow system:\n{SYSTEM_PROMPT}")
        logger.debug(f"Validating workflow:\n{prompt}")
        
        response = azureQuickPrompt(prompt, system=SYSTEM_PROMPT, temp=0.0)
        
        logger.info(f"Validation response: {response}")
        
        # Parse response
        result = json.loads(response)
        
        # Convert is_valid to Python boolean
        is_valid_raw = result.get('is_valid', True)
        if isinstance(is_valid_raw, str):
            is_valid = is_valid_raw.lower() == 'true'
        else:
            is_valid = bool(is_valid_raw)
        result['is_valid'] = is_valid
        
        # Ensure errors and warnings are lists
        errors = result.get('errors', [])
        warnings = result.get('warnings', [])

        # =====================================================================
        # PYTHON CONNECTIVITY CHECK - catches what AI might miss
        # =====================================================================
        disconnected_nodes = check_connectivity(workflow_state)
        
        if disconnected_nodes:
            # Build a lookup for node labels
            node_labels = {n['id']: n.get('label', n['id']) for n in workflow_state.get('nodes', [])}
            
            for node_id in disconnected_nodes:
                label = node_labels.get(node_id, node_id)
                error_msg = f"DISCONNECTED NODE: {node_id} ({label}) is not connected to the main workflow and needs an incoming connection"
                if error_msg not in errors:
                    errors.append(error_msg)
                    is_valid = False
                    logger.info(f"Connectivity check found: {error_msg}")
        # =====================================================================

        # =====================================================================
        # PYTHON DUPLICATE CONNECTION CHECK
        # =====================================================================
        duplicate_errors = check_duplicate_connections(workflow_state)
        
        for error_msg in duplicate_errors:
            if error_msg not in errors:
                errors.append(error_msg)
                is_valid = False
                logger.info(f"Python duplicate check found: {error_msg}")
        # =====================================================================

        # =====================================================================
        # PYTHON SAVE-TO-VARIABLE CHECK
        # =====================================================================
        save_var_warnings = check_missing_save_to_variable(workflow_state)

        for warning_msg in save_var_warnings:
            if warning_msg not in warnings:
                warnings.append(warning_msg)
                logger.info(f"Save-to-variable check found: {warning_msg}")
        # =====================================================================

        # =====================================================================
        # PYTHON VARIABLE REFERENCE CHECK
        # =====================================================================
        var_ref_warnings = check_variable_references(workflow_state)

        for warning_msg in var_ref_warnings:
            if warning_msg not in warnings:
                warnings.append(warning_msg)
                logger.info(f"Variable reference check found: {warning_msg}")
        # =====================================================================

        result['is_valid'] = is_valid
        result['errors'] = errors
        result['warnings'] = warnings
        
        return is_valid, result
        
    except json.JSONDecodeError as e:
        logger.warning(f"AI validation returned non-JSON: {response[:200]}")
        return True, {
            "is_valid": True,
            "errors": [],
            "warnings": [f"Validation response parsing failed: {str(e)}"]
        }
    except Exception as e:
        logger.error(f"Validation failed: {e}")
        return True, {
            "is_valid": True,
            "errors": [],
            "warnings": [f"Validation unavailable: {str(e)}"]
        }


def validate_workflow(workflow_state: Dict) -> Tuple[bool, Dict]:
    """
    Validate a workflow state and identify any issues.

    Tries the deterministic pre-pass first (when enabled), then falls back to
    the LLM validator for issues the deterministic pass cannot resolve.

    Args:
        workflow_state: Current workflow state with nodes and connections

    Returns:
        Tuple of (is_valid, result_dict)
        result_dict contains:
            - is_valid: bool
            - errors: list of error strings
            - warnings: list of warning strings
            - fix_commands: optional dict of {action, commands[]} the frontend
                            can apply directly. Only present when the
                            deterministic pre-pass emitted commands or the LLM
                            returned them. The frontend's command executor
                            already understands this shape.
    """
    deterministic_enabled = getattr(cfg, "WORKFLOW_VALIDATOR_DETERMINISTIC_ENABLED", True)
    fix_warnings = getattr(cfg, "WORKFLOW_VALIDATOR_FIX_WARNINGS", False)
    always_run_llm = getattr(cfg, "WORKFLOW_VALIDATOR_ALWAYS_RUN_LLM", False)

    # Master switch: if the new path is disabled, behave exactly as before.
    if not deterministic_enabled:
        logger.debug("Deterministic validator disabled; using LLM-only path")
        return _validate_workflow_with_llm(workflow_state)

    # Run the deterministic pre-pass. Any exception falls through to LLM.
    try:
        det_result = det_val.run(workflow_state, fix_warnings=fix_warnings)
    except Exception as e:
        logger.exception(f"Deterministic validator raised; falling back to LLM: {e}")
        return _validate_workflow_with_llm(workflow_state)

    warning_messages = [i.message for i in det_result.warnings]
    # Structured warning details so the frontend can decorate the offending
    # nodes in the designer UI (e.g. red ring + tooltip on the source node).
    warning_details = [
        {
            "code": i.code,
            "node_id": i.node_id,
            "message": i.message,
            "extra": i.extra,
        }
        for i in det_result.warnings
    ]

    # Re-validate after applying the deterministic fix commands to a virtual
    # copy of the workflow state. The "errors" we report to the caller reflect
    # the POST-FIX state - so a clean is_valid=true response really means
    # "the workflow is correct after the deterministic fixes are applied,"
    # not just "the deterministic fixers said they could handle it."
    post_fix_error_messages: List[str] = []
    if det_result.fix_commands:
        try:
            post_fix_state = det_val.apply_commands_to_state(
                workflow_state, det_result.fix_commands
            )
            post_fix_run = det_val.run(post_fix_state, fix_warnings=fix_warnings)
            post_fix_error_messages = [i.message for i in post_fix_run.errors]
            if post_fix_error_messages:
                logger.warning(
                    f"Deterministic post-fix re-validation found {len(post_fix_error_messages)} "
                    f"residual error(s) - the fixers did not fully resolve every issue. "
                    f"Codes: {sorted({i.code for i in post_fix_run.errors})}"
                )
            else:
                logger.info(
                    f"Deterministic post-fix re-validation: clean (was "
                    f"{len(det_result.errors)} error(s) before fixes)."
                )
        except Exception as e:
            logger.exception(
                f"apply_commands_to_state raised during re-validation; "
                f"reporting unfixable_errors as residual: {e}"
            )
            post_fix_error_messages = [i.message for i in det_result.unfixable_errors]
    else:
        # No fixes attempted - residual is whatever was unfixable
        post_fix_error_messages = [i.message for i in det_result.unfixable_errors]

    is_valid = len(post_fix_error_messages) == 0

    # Fast path: post-fix state is clean AND user hasn't asked for an LLM
    # second opinion. Skip the LLM entirely.
    if is_valid and not always_run_llm:
        result: Dict = {
            "is_valid": True,
            "errors": [],
            "warnings": warning_messages,
            "warning_details": warning_details,
        }
        if det_result.fix_commands:
            result["fix_commands"] = {
                "action": "build_workflow",
                "commands": det_result.fix_commands,
            }
            logger.info(
                f"Deterministic pre-pass: {len(det_result.errors)} error(s) detected, "
                f"all resolved by {len(det_result.fix_commands)} fix command(s); "
                f"post-fix re-validation clean. LLM skipped."
            )
        else:
            logger.debug("Deterministic pre-pass: clean, LLM skipped.")
        return True, result

    # Slow path: there are residual errors the deterministic path couldn't
    # auto-fix, OR the user wants the LLM to run regardless. Call the LLM
    # validator and merge its findings on top of what we already have.
    if det_result.unfixable_errors:
        logger.info(
            f"Deterministic pre-pass: {len(det_result.unfixable_errors)} residual "
            f"error(s) - falling back to LLM. Codes: "
            f"{sorted({i.code for i in det_result.unfixable_errors})}"
        )
    else:
        logger.debug("Deterministic pre-pass clean; running LLM as configured belt-and-suspenders check.")

    try:
        llm_is_valid, llm_result = _validate_workflow_with_llm(workflow_state)
    except Exception as e:
        # If even the LLM blows up after a deterministic exception... return
        # what we have. Better than 500-ing the request.
        logger.exception(f"LLM fallback also raised; returning deterministic-only result: {e}")
        result = {
            "is_valid": is_valid,
            "errors": post_fix_error_messages,
            "warnings": warning_messages + [f"LLM fallback unavailable: {e}"],
            "warning_details": warning_details,
        }
        if det_result.fix_commands:
            result["fix_commands"] = {
                "action": "build_workflow",
                "commands": det_result.fix_commands,
            }
        return is_valid, result

    # Merge: union of errors / warnings, dedup by exact text
    merged_errors = list(post_fix_error_messages)
    for msg in llm_result.get("errors", []) or []:
        if msg not in merged_errors:
            merged_errors.append(msg)
    merged_warnings = list(warning_messages)
    for msg in llm_result.get("warnings", []) or []:
        if msg not in merged_warnings:
            merged_warnings.append(msg)

    merged_is_valid = bool(llm_is_valid) and is_valid

    merged: Dict = {
        "is_valid": merged_is_valid,
        "errors": merged_errors,
        "warnings": merged_warnings,
        "warning_details": warning_details,
    }

    # Combine fix commands. Deterministic ones come first (they're safer);
    # then any LLM-emitted ones are appended, with naive dedup on the full
    # command dict.
    combined_fix_commands: List[Dict] = []
    seen_cmds = []
    for cmd in det_result.fix_commands or []:
        if cmd not in seen_cmds:
            seen_cmds.append(cmd)
            combined_fix_commands.append(cmd)
    llm_fix_block = llm_result.get("fix_commands") if isinstance(llm_result, dict) else None
    if isinstance(llm_fix_block, dict):
        for cmd in llm_fix_block.get("commands", []) or []:
            if cmd not in seen_cmds:
                seen_cmds.append(cmd)
                combined_fix_commands.append(cmd)
    if combined_fix_commands:
        merged["fix_commands"] = {
            "action": "build_workflow",
            "commands": combined_fix_commands,
        }
        logger.info(
            f"Merged validator output: {len(merged_errors)} error(s), "
            f"{len(combined_fix_commands)} fix command(s) total "
            f"({len(det_result.fix_commands)} deterministic + remainder from LLM)."
        )

    return merged_is_valid, merged