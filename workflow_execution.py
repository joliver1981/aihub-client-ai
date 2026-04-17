# workflow_execution.py

import uuid
import json
import time
import logging
from logging.handlers import WatchedFileHandler
import datetime
from typing import Dict, Any, Optional, List, Union, Tuple
import pyodbc
import threading
import queue
import os
import glob
import random
import requests
from DataUtils import get_database_connection_string, execute_sql_query
from CommonUtils import get_document_api_base_url, rotate_logs_on_startup, get_log_path
# Import necessary alert utilities
try:
    from AppUtils import send_email, send_email_wrapper, sms_text_message_alert, aihub_phone_call_alert
except ImportError:
    raise ImportError("AppUtils module not found. Make sure it's in the Python path.")

from config import WORKFLOW_DOC_DETECT_TYPE_DEFAULT, WORKFLOW_DOC_EXTRACT_FIELDS_DEFAULT, WORKFLOW_DOC_DO_NOT_SAVE_DEFAULT

import subprocess
import tempfile
import shlex
import csv
import re
from pathlib import Path
import ast
import decimal
import math
from integration_workflow_node import execute_integration_node
from config import MAX_LOG_CHARS


# Configure logging
def setup_logging():
    """Configure logging for the workflow execution"""
    logger = logging.getLogger("WorkflowExecution")
    log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
    log_level = getattr(logging, log_level_name, logging.DEBUG)
    logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler = WatchedFileHandler(filename=os.getenv('WORKFLOW_EXECUTION_LOG', get_log_path('workflow_execution_log.txt')), encoding='utf-8')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger

rotate_logs_on_startup(os.getenv('WORKFLOW_EXECUTION_LOG', get_log_path('workflow_execution_log.txt')))

logger = setup_logging()

try:
    from excel_utils import write_extraction_to_excel
    from excel_update_utils import (
        ExcelUpdateConfig,
        update_excel_with_changes,
        read_excel_to_dict,
        build_row_key
    )
except ImportError as e:
    logger.warning(f"excel_update_utils not found - UPDATE operation will not be available: {e}")
    update_excel_with_changes = None
    read_excel_to_dict = None
    build_row_key = None

from ai_key_matcher import AIKeyMatcher  # Required for AI key matching feature

from smart_change_detector import (
    SmartChangeDetector,
    ChangeCandidate,
    build_change_candidates,
    filter_updates_by_evaluation
)

def to_truncated_str(obj, max_chars=MAX_LOG_CHARS, suffix="... [TRUNCATED]"):
    """
    Convert any object to string and truncate if too long.
    """
    try:
        s = str(obj)
    except Exception:
        s = "<unstringifiable object>"

    if len(s) > max_chars:
        return s[:max_chars] + suffix
    return s


class WorkflowExecutionEngine:
    """Engine for executing workflows with pause/resume support and human approvals"""
    
    def __init__(self, connection_string: str):
        """Initialize the workflow execution engine
        
        Args:
            connection_string: SQL Server connection string
        """
        self.connection_string = connection_string
        self._active_executions = {}  # Stores in-memory state of active executions
        self._execution_queues = {}   # Queues for communicating with execution threads
        self._execution_threads = {}  # Background threads for each execution

        # Tracking structures for loop management
        self._completed_loops = {}  # Track completed loops per execution
        self._loop_results = {}  # Store loop results for End Loop nodes
        self._active_loops = {}  # Track active loop states
    
    def get_db_connection(self):
        """Create and return a database connection"""
        return pyodbc.connect(self.connection_string)

    def find_variable(self, var_name: str, data_dict: dict) -> tuple[str | None, any]:
        """
        Find a variable in a dictionary, handling ${var_name} or var_name format
        on both the input variable and dictionary keys.
        
        Args:
            var_name: Variable name with or without ${} wrapping
            data_dict: Dictionary to search in (keys may or may not have ${} wrapping)
        
        Returns:
            Tuple of (found_key: str | None, value: any)
            - found_key: The actual key name as it exists in data_dict, or None if not found
            - value: The value associated with the key, or None if not found
        """
        # Normalize the input variable name (remove ${} if present)
        normalized_var = var_name.strip()
        if normalized_var.startswith('${') and normalized_var.endswith('}'):
            normalized_var = normalized_var[2:-1]
        
        # Try to find the variable in the dictionary with different formats
        # 1. Try exact match first
        if var_name in data_dict:
            return var_name, data_dict[var_name]
        
        # 2. Try normalized name (without ${})
        if normalized_var in data_dict:
            return normalized_var, data_dict[normalized_var]
        
        # 3. Try with ${} wrapping
        wrapped_var = f"${{{normalized_var}}}"
        if wrapped_var in data_dict:
            return wrapped_var, data_dict[wrapped_var]
        
        return None, None
    
    def start_workflow(self, workflow_id: int, workflow_data: Dict, initiator: str = 'system') -> str:
        """Start a new workflow execution
        
        Args:
            workflow_id: ID of the workflow to execute
            workflow_data: Parsed workflow definition (nodes, connections, variables)
            initiator: User or system that initiated the workflow
            
        Returns:
            execution_id: Unique ID for the workflow execution
        """
        # Generate a unique execution ID
        execution_id = str(uuid.uuid4())
        print('Execution thread id:', execution_id)
        # Create database record
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        try:
            # First, get the tenant context
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

            # Get workflow name
            cursor.execute("SELECT workflow_name FROM Workflows WHERE id = ?", workflow_id)
            row = cursor.fetchone()
            if not row:
                print(f"Workflow with ID {workflow_id} not found")
                raise ValueError(f"Workflow with ID {workflow_id} not found")
            
            workflow_name = row[0]
            
            print('Creating execution record...')
            # Create execution record
            cursor.execute("""
                INSERT INTO WorkflowExecutions (
                    execution_id, workflow_id, workflow_name, status, 
                    started_at, initiated_by, execution_data
                ) VALUES (?, ?, ?, 'Running', getutcdate(), ?, ?)
            """, execution_id, workflow_id, workflow_name, initiator, json.dumps({}))
            
            print('Init variables...')
            # Initialize workflow variables
            if 'variables' in workflow_data:
                for var_name, var_data in workflow_data['variables'].items():
                    # Convert value to correct type
                    var_type = var_data.get('type', 'string')
                    default_value = var_data.get('defaultValue', '')
                    
                    # Store variable value in execution_data
                    cursor.execute("""
                        INSERT INTO WorkflowVariables (
                            execution_id, variable_name, variable_type, 
                            variable_value, last_updated
                        ) VALUES (?, ?, ?, ?, getutcdate())
                    """, execution_id, var_name, var_type, json.dumps(default_value))
            
            print('Getting start node...')
            print('workflow_data:', workflow_data)
            # Find the start node
            start_node = None
            for node in workflow_data.get('nodes', []):
                if node.get('isStart'):
                    start_node = node
                    print('Start node found: ', start_node)
                    break
    
            if not start_node:
                print("No start node defined in the workflow")
                raise ValueError("No start node defined in the workflow")
            
            print('Commit executed.')
            # Commit the transaction
            conn.commit()
            
            print('Logging execution...')
            # Add log entry for workflow start
            self.log_execution(execution_id, None, "info", 
                               f"Workflow execution started: {workflow_name}", 
                               {"workflow_id": workflow_id, "initiator": initiator})

            print('Setting in-memory state...')
            # Create in-memory state for this execution
            self._active_executions[execution_id] = {
                'workflow_id': workflow_id,
                'workflow_name': workflow_name,
                'status': 'Running',
                'started_at': datetime.datetime.now().isoformat(),
                'workflow_data': workflow_data,
                'current_node': start_node['id'],
                'variables': {},
                'paused': False,
                'cancelled': False
            }
            
            print('Creating queue...')
            # Create a queue for communicating with the execution thread
            self._execution_queues[execution_id] = queue.Queue()
            
            print('Starting thread...', execution_id)
            # Start a background thread to execute the workflow
            thread = threading.Thread(
                target=self._execute_workflow_thread,
                args=(execution_id, start_node['id'])
            )
            thread.daemon = True
            thread.start()
            self._execution_threads[execution_id] = thread
            
            print('Thread created successfully.')
            return execution_id
            
        except Exception as e:
            conn.rollback()
            print(f"Error starting workflow: {str(e)}")
            logger.error(f"Error starting workflow: {str(e)}")
            raise e
        finally:
            cursor.close()
            conn.close()
    
    def _execute_workflow_thread(self, execution_id: str, start_node_id: str):
        """Background thread that executes a workflow
        
        Args:
            execution_id: Unique ID of the workflow execution
            start_node_id: ID of the node to start from
        """
        try:
            # Get workflow data from in-memory state
            execution_state = self._active_executions[execution_id]
            workflow_data = execution_state['workflow_data']
            
            # Load variables from database
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            # First, get the tenant context
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            cursor.execute("""
                SELECT variable_name, variable_type, variable_value
                FROM WorkflowVariables
                WHERE execution_id = ?
            """, execution_id)
            
            variables = {}
            for row in cursor.fetchall():
                var_name, var_type, var_value = row
                # Convert value based on type
                try:
                    parsed_value = json.loads(var_value)
                    if var_type == 'number':
                        parsed_value = float(parsed_value)
                    variables[var_name] = parsed_value
                except:
                    variables[var_name] = var_value
            
            cursor.close()
            conn.close()
            
            # Store variables in execution state
            execution_state['variables'] = variables
            
            # Start execution from the specified node
            current_node_id = start_node_id
            
            # Execute nodes until workflow completes
            while current_node_id and not execution_state['cancelled']:
                # Check if execution is paused
                if execution_state['paused']:
                    # Wait for resume signal
                    self.log_execution(
                        execution_id, current_node_id, "info", 
                        "Workflow execution paused, waiting for resume signal")
                    
                    # Update node status to paused
                    self._update_step_status(execution_id, current_node_id, 'Paused')
                    
                    # Wait for commands from the queue
                    command = self._execution_queues[execution_id].get()
                    
                    if command == 'resume':
                        execution_state['paused'] = False
                        self.log_execution(
                            execution_id, current_node_id, "info", 
                            "Workflow execution resumed")
                        
                        # Update node status back to running
                        self._update_step_status(execution_id, current_node_id, 'Running')
                    elif command == 'cancel':
                        execution_state['cancelled'] = True
                        self.log_execution(
                            execution_id, current_node_id, "info", 
                            "Workflow execution cancelled while paused")
                        break
                
                # Find the node definition
                node = None
                for n in workflow_data['nodes']:
                    if n['id'] == current_node_id:
                        node = n
                        break
                
                if not node:
                    self.log_execution(
                        execution_id, current_node_id, "error", 
                        f"Node not found: {current_node_id}")
                    break
                
                # Execute the node
                try:
                    print('Executing node...')
                    print('Node:', node)
                    print('Variables:', variables)
                    next_node_id = self._execute_node(execution_id, node, variables)
                    current_node_id = next_node_id
                except Exception as e:
                    self.log_execution(
                        execution_id, node['id'], "error", 
                        f"Error executing node: {str(e)}")
                    
                    # Update workflow status to failed
                    self._update_workflow_status(execution_id, 'Failed')
                    execution_state['status'] = 'Failed'
                    break
            
            # If cancelled, update the status
            if execution_state['cancelled']:
                self._update_workflow_status(execution_id, 'Cancelled')
                execution_state['status'] = 'Cancelled'
            # If completed successfully, update the status
            elif current_node_id is None and not execution_state['paused']:
                self._update_workflow_status(execution_id, 'Completed')
                execution_state['status'] = 'Completed'
                self.log_execution(
                    execution_id, None, "info", 
                    "Workflow execution completed successfully")
            
            # Clean up resources
            if execution_id in self._execution_queues:
                del self._execution_queues[execution_id]
            if execution_id in self._execution_threads:
                del self._execution_threads[execution_id]
                
        except Exception as e:
            logger.error(f"Error in workflow execution thread: {str(e)}")
            # Update workflow status to failed
            self._update_workflow_status(execution_id, 'Failed')
            if execution_id in self._active_executions:
                self._active_executions[execution_id]['status'] = 'Failed'
            
            self.log_execution(
                execution_id, None, "error", 
                f"Unhandled error in workflow execution: {str(e)}")
            
        finally:
            # CRITICAL: Always clean up resources when thread completes
            self._cleanup_execution_resources(execution_id)
            logger.info(f"Workflow thread completed and cleaned up for execution {execution_id}")

    
    def _execute_node(self, execution_id: str, node: Dict, variables: Dict) -> Optional[str]:
        """Execute a single node in the workflow
        
        Args:
            execution_id: Unique ID of the workflow execution
            node: Node definition
            variables: Current workflow variables
                
        Returns:
            next_node_id: ID of the next node to execute, or None if workflow is complete
        """
        node_id = node['id']
        node_type = node['type']
        node_label = node.get('label', node_type)
        node_config = node.get('config', {})
        
        # Create or update step execution record
        step_execution_id = self._create_step_execution(
            execution_id, node_id, node_label, node_type)
        
        # Log the start of node execution
        self.log_execution(
            execution_id, node_id, "info", 
            f"Executing node: {node_label} ({node_type})")
        
        # For Human Approval nodes, handle the approval flow
        if node_type == 'Human Approval':
            return self._execute_human_approval_node(
                execution_id, step_execution_id, node, variables)
        
        # Handle other node types
        try:
            # Execute the node action based on type
            result = None
            
            if node_type == 'Database':
                print('Executing database action...')
                result = self._execute_database_node(execution_id, node, variables)
            elif node_type == 'Folder Selector':
                print('Executing folder selector action...')
                result = self._execute_folder_selector_node(execution_id, node, variables)
            elif node_type == 'Document':
                print('Executing document action...')
                result = self._execute_document_node(execution_id, node, variables)
            elif node_type == 'AI Action':
                print('Executing AI action...')
                result = self._execute_ai_action_node(execution_id, node, variables)
            elif node_type == 'Set Variable':  # Added support for Set Variable node type
                print('Executing Set Variable action...')
                result = self._execute_set_variable_node(execution_id, node, variables)
            elif node_type == 'Alert':  # Added support for Set Variable node type
                print('Executing Alert action...')
                result = self._execute_alert_node(execution_id, node, variables)
            elif node_type == 'Conditional':
                print('Executing conditional action...')
                result = self._execute_conditional_node(execution_id, node, variables)
            elif node_type == 'Loop':
                print('Executing loop action...')
                result = self._execute_loop_node(execution_id, node, variables)
            elif node_type == 'End Loop':
                print('Executing end loop action...')
                result = self._execute_end_loop_node(execution_id, node, variables)
            elif node_type == 'Execute Application':
                print('Executing application node...')
                result = self._execute_application_node(execution_id, node, variables)
            elif node_type == 'File':
                result = self._execute_file_node(execution_id, node, variables)
            elif node_type == 'AI Extract':
                print('Executing AI extract action...')
                result = self._execute_ai_extract_node(execution_id, node, variables)
            elif node_type == 'Excel Export':
                print('Executing Excel Export action...')
                result = self._execute_excel_export_node(execution_id, node, variables)
            elif node_type == 'Integration':
                print('Executing Integration node...')
                result = execute_integration_node(execution_id, node, variables, self.log_execution)
            # elif node_type == 'Server':
            #     result = self._execute_server_node(execution_id, node, variables)
            # And so on...
            else:
                # Default handling for unimplemented node types
                print(f"Node type '{node_type}' not implemented yet")
                self.log_execution(
                    execution_id, node_id, "warning",
                    f"Node type '{node_type}' not implemented yet")
                result = {'success': True, 'data': {}}
            
            # Store the output for potential use by subsequent nodes
            variables['_previousStepOutput'] = result.get('data', {})
            
            # Check if the node execution was successful
            if not result.get('success', False):
                raise ValueError(result.get('error', f"Node execution failed: {node_type}"))
            
            # Update the step execution status to completed
            self._update_step_status(execution_id, node_id, 'Completed', 
                                output_data=result.get('data', {}))
            
            # Special handling for Loop nodes (add after successful execution)
            if node_type == 'Loop':
                # Check if loop has completed
                if execution_id in self._completed_loops and node_id in self._completed_loops[execution_id]:
                    # Find the End Loop node and continue from there
                    end_loop_node_id = self._find_end_loop_node(execution_id, node_id)
                    if end_loop_node_id:
                        self.log_execution(
                            execution_id, node_id, "info",
                            f"Loop completed, continuing from End Loop node")
                        # Clean up the completed flag after using it
                        self._completed_loops[execution_id].discard(node_id)
                        return end_loop_node_id
            
            # Special handling for End Loop nodes
            if node_type == 'End Loop':
                # Clean up completed loops tracking if needed
                loop_node_id = node_config.get('loopNodeId')
                if not loop_node_id and hasattr(self, '_active_loops') and self._active_loops:
                    loop_node_id = list(self._active_loops.keys())[-1] if self._active_loops else None
                
                # If not in active loop, clean up tracking
                if not (hasattr(self, '_active_loops') and loop_node_id in self._active_loops):
                    if execution_id in self._completed_loops and loop_node_id:
                        self._completed_loops[execution_id].discard(loop_node_id)
            
            # Determine the next node based on connections
            workflow_data = self._active_executions[execution_id]['workflow_data']
            connections = workflow_data.get('connections', [])
            
            # Find connections from this node
            next_connections = [
                conn for conn in connections if conn['source'] == node_id
            ]

            # When determining next connections for Loop nodes, modify the logic:
            if node_type == 'Loop' and execution_id in self._completed_loops and node_id in self._completed_loops[execution_id]:
                # Filter out pass connections (loop body) since loop is completed
                original_count = len(next_connections)
                next_connections = [
                    conn for conn in next_connections 
                    if conn.get('type') != 'pass'
                ]
                self.log_execution(
                execution_id, node_id, "debug",
                f"Filtered connections: {original_count} -> {len(next_connections)} (removed pass connections)")
        
            
            if not next_connections:
                # No more nodes to execute
                return None
            
            # For now, just follow the first "pass" connection
            for conn in next_connections:
                conn_type = conn.get('type', 'pass')
                if conn_type == 'pass':
                    return conn['target']
            
            # If no pass connection, return None to end the workflow
            return None
                
        except Exception as e:
            # Update the step execution status to failed
            self._update_step_status(
                execution_id, node_id, 'Failed', 
                error_message=str(e))
            
            # Log the error
            self.log_execution(
                execution_id, node_id, "error", 
                f"Error executing node: {str(e)}")
            
            # Find fail connections
            workflow_data = self._active_executions[execution_id]['workflow_data']
            connections = workflow_data.get('connections', [])
            
            fail_connections = [
                conn for conn in connections 
                if conn['source'] == node_id and conn.get('type') == 'fail'
            ]
            
            if fail_connections:
                return fail_connections[0]['target']
            
            # If no fail connection, re-raise the exception
            raise

    def _has_repeated_group_fields(self, fields: List[Dict]) -> bool:
        """Check if any field in the schema is a repeated_group type.
        
        Args:
            fields: List of field definitions
            
        Returns:
            True if any field is type 'repeated_group'
        """
        for field in fields:
            if field.get('type') == 'repeated_group':
                return True
            # Also check children recursively
            children = field.get('children', [])
            if children and self._has_repeated_group_fields(children):
                return True
        return False

    def _build_schema_fields_for_document_extraction(self, fields: List[Dict]) -> Dict[str, str]:
        """Build schema_fields dict for populate_schema_with_claude.
        
        Properly handles nested structures:
        - group: Described as "an object containing {child fields}"
        - repeated_group: Described as "an ARRAY of objects, each containing {child fields}"
        
        This allows Claude to return proper nested/array values instead of flat dot-notation fields.
        
        Args:
            fields: List of field definitions from the node config
            
        Returns:
            Dict mapping field names to descriptions for Claude
        """
        schema_fields = {}
        
        for field in fields:
            field_name = field.get('name', '')
            field_desc = field.get('description', '')
            field_type = field.get('type', 'text')
            required = field.get('required', False)
            children = field.get('children', [])
            
            if not field_name:
                continue
            
            if field_type == 'repeated_group' and children:
                # Build description that tells Claude to return an array of objects
                child_descriptions = self._build_child_field_descriptions(children)
                
                desc_parts = []
                if field_desc:
                    desc_parts.append(field_desc)
                
                desc_parts.append(
                    f"Return as an ARRAY of objects. Each object in the array should contain these fields: {child_descriptions}. "
                    f"Extract ALL matching items from the document. If no items found, return an empty array []."
                )
                
                if required:
                    desc_parts.append("(REQUIRED - must have at least one item)")
                
                schema_fields[field_name] = " ".join(desc_parts)
                
            elif field_type == 'group' and children:
                # Build description that tells Claude to return a nested object
                child_descriptions = self._build_child_field_descriptions(children)
                
                desc_parts = []
                if field_desc:
                    desc_parts.append(field_desc)
                
                desc_parts.append(
                    f"Return as an object containing these fields: {child_descriptions}."
                )
                
                if required:
                    desc_parts.append("(REQUIRED)")
                
                schema_fields[field_name] = " ".join(desc_parts)
                
            else:
                # Simple field - just add description with type hint
                desc_parts = []
                if field_desc:
                    desc_parts.append(field_desc)
                desc_parts.append(f"(type: {field_type})")
                if required:
                    desc_parts.append("(REQUIRED)")
                
                schema_fields[field_name] = " ".join(desc_parts)
        
        return schema_fields
    
    def _build_child_field_descriptions(self, children: List[Dict], indent: int = 0) -> str:
        """Build a description string for child fields.
        
        Args:
            children: List of child field definitions
            indent: Current nesting level for recursive calls
            
        Returns:
            String describing the child fields
        """
        parts = []
        
        for child in children:
            child_name = child.get('name', '')
            child_desc = child.get('description', '')
            child_type = child.get('type', 'text')
            child_required = child.get('required', False)
            grandchildren = child.get('children', [])
            
            if not child_name:
                continue
            
            if child_type == 'repeated_group' and grandchildren:
                # Nested array
                nested_desc = self._build_child_field_descriptions(grandchildren, indent + 1)
                field_str = f'"{child_name}" (array of objects with: {nested_desc})'
            elif child_type == 'group' and grandchildren:
                # Nested object
                nested_desc = self._build_child_field_descriptions(grandchildren, indent + 1)
                field_str = f'"{child_name}" (object with: {nested_desc})'
            else:
                # Simple field
                field_str = f'"{child_name}"'
                if child_desc:
                    field_str += f' ({child_desc})'
                field_str += f' [{child_type}]'
            
            if child_required:
                field_str += ' REQUIRED'
            
            parts.append(field_str)
        
        return ', '.join(parts)

    def _execute_ai_extract_node(self, execution_id: str, node: Dict, variables: Dict) -> Dict:
        """Execute an AI Extract node in the workflow
        
        Supports two extraction modes:
        - Text extraction: Uses AIExtractExecutor with raw text input (existing behavior)
        - Document extraction: Uses populate_schema_with_claude with document files
        
        Optionally writes output to Excel file.
        
        Args:
            execution_id: Unique ID of the workflow execution
            node: Node definition
            variables: Current workflow variables
            
        Returns:
            result: Result of the node execution
        """
        from ai_extract_executor import AIExtractExecutor
        from AppUtils import azureQuickPrompt, is_file_path
        
        node_id = node['id']
        node_config = node.get('config', {})
        
        self.log_execution(
            execution_id, node_id, "info", 
            "Executing AI Extract node")
        
        # DEBUG: Log the full config
        self.log_execution(
            execution_id, node_id, "debug",
            f"Full node config: {node_config}")
        
        self.log_execution(
            execution_id, node_id, "debug",
            f"outputToExcel value: {node_config.get('outputToExcel')} (type: {type(node_config.get('outputToExcel'))})")
        
        
        try:
            # Get input source mode and input value
            input_source = node_config.get('inputSource', 'auto')  # 'auto', 'text', 'document'
            input_variable = node_config.get('inputVariable', '')
            
            # Resolve variable references
            input_value = self._replace_variable_references(input_variable, variables)
            
            if not input_value:
                raise ValueError("No input provided for extraction")
            
            self.log_execution(
                execution_id, node_id, "debug",
                f"Input source mode: {input_source}, Input value length: {len(input_value) if input_value else 0}")
            
            # Determine extraction mode based on inputSource setting
            use_document_extraction = False
            document_path = None
            
            if input_source == 'document':
                # Forced document mode - input must be a file path
                if not os.path.exists(input_value):
                    raise FileNotFoundError(f"Document not found: {input_value}")
                use_document_extraction = True
                document_path = input_value
                
            elif input_source == 'text':
                # Forced text mode - treat input as raw text
                use_document_extraction = False
                
            else:
                # Auto-detect mode
                if is_file_path(input_value) and os.path.exists(input_value):
                    use_document_extraction = True
                    document_path = input_value
                else:
                    use_document_extraction = False
            
            self.log_execution(
                execution_id, node_id, "info",
                f"Using {'document' if use_document_extraction else 'text'} extraction mode")
            
            # Get field configuration
            fields = node_config.get('fields', [])
            if not fields:
                raise ValueError("No fields defined for extraction")
            
            # Execute extraction based on mode
            if use_document_extraction:
                extracted_data, extraction_result_full = self._execute_document_extraction(
                    execution_id, node_id, document_path, fields, node_config)
            else:
                extracted_data, extraction_result_full = self._execute_text_extraction(
                    execution_id, node_id, input_value, fields, node_config)
            
            # Flatten metadata into extracted_data if include options are enabled
            include_confidence = node_config.get('includeConfidence', False)
            include_assumptions = node_config.get('includeAssumptions', False)
            include_sources = node_config.get('includeSources', False)
            
            if extraction_result_full and (include_confidence or include_assumptions or include_sources):
                fields_result = extraction_result_full.get('fields', {})
                for field_name, field_info in fields_result.items():
                    if include_confidence:
                        confidence = field_info.get('confidence', '')
                        if confidence:
                            extracted_data[f"{field_name}_confidence"] = confidence
                    
                    if include_assumptions:
                        assumptions = field_info.get('assumptions', [])
                        if assumptions:
                            extracted_data[f"{field_name}_assumptions"] = "; ".join(assumptions)
                    
                    if include_sources:
                        sources = field_info.get('sources', [])
                        if sources:
                            source_pages = []
                            for src in sources:
                                pages = src.get('pages', [])
                                source_pages.extend([str(p) for p in pages])
                            if source_pages:
                                extracted_data[f"{field_name}_sources"] = ", ".join(source_pages)
                
                self.log_execution(
                    execution_id, node_id, "info",
                    f"Added flattened metadata to output: confidence={include_confidence}, "
                    f"assumptions={include_assumptions}, sources={include_sources}")
            
            # Store result in output variable
            output_var = node_config.get('outputVariable', 'extractedData')
            output_var = self._extract_variable_name(output_var)
            
            # Update variable in database - store the simple key:value format
            self._update_workflow_variable(
                execution_id, output_var, 'object', extracted_data)
            
            # Update in-memory variables
            variables[output_var] = extracted_data
            
            self.log_execution(
                execution_id, node_id, "info",
                f"Stored extracted data in variable: {output_var}",
                {"fields_extracted": list(extracted_data.keys()) if extracted_data else []})
            
            # Store full result if available (document extraction always has it,
            # text extraction has it when formatting was requested)
            if extraction_result_full:
                full_var = f"{output_var}_full"
                self._update_workflow_variable(
                    execution_id, full_var, 'object', extraction_result_full)
                variables[full_var] = extraction_result_full
                
                if use_document_extraction:
                    self.log_execution(
                        execution_id, node_id, "debug",
                        f"Stored full extraction result (with assumptions/sources) in: {full_var}")
                else:
                    self.log_execution(
                        execution_id, node_id, "debug",
                        f"Stored extraction result with cell_formatting in: {full_var}")
            
            # Handle Excel output if configured
            output_destination = node_config.get('outputDestination', 'variable')
            output_to_excel = output_destination != 'variable'
            excel_result = None

            if output_to_excel:
                # Derive operation from outputDestination
                operation_map = {
                    'excel_new': 'new',
                    'excel_template': 'new_from_template',
                    'excel_append': 'append'
                }
                excel_operation = operation_map.get(output_destination, 'new')
                
                # Add to node_config so _write_extraction_to_excel can use it
                node_config['excelOperation'] = excel_operation
                
                excel_result = self._write_extraction_to_excel(
                    execution_id, node_id, extraction_result_full or extracted_data, 
                    node_config, variables, use_document_extraction)

                # Fail the node if Excel operation failed
                if not excel_result.get('success', False):
                    error_msg = excel_result.get('error', 'Error writing to Excel file')
                    self.log_execution(
                        execution_id, node_id, "error",
                        f"Excel output failed: {error_msg}")
                    
                    return {
                        'success': False,
                        'error': f"Excel output failed: {error_msg}",
                        'data': {'extraction': extracted_data}
                    }
            
            # Build return data
            result_data = {
                'extraction': extracted_data,
                'mode': 'document' if use_document_extraction else 'text'
            }
            
            if excel_result:
                result_data['excel'] = excel_result
            
            return {
                'success': True,
                'data': result_data
            }
            
        except Exception as e:
            error_message = str(e)
            self.log_execution(
                execution_id, node_id, "error",
                f"AI Extract error: {error_message}")
            
            return {
                'success': False,
                'error': error_message,
                'data': {}
            }
        
        
    def _execute_excel_update_node(self,
        execution_id: str,
        node: Dict,
        variables: Dict,
        log_execution_func,
        replace_variable_references_func
    ) -> Dict:
        """
        Execute an Excel Update node - updates existing rows with change tracking.
        
        Features:
        - Standard key matching (exact match on key columns)
        - AI-assisted key matching (optional, for handling semantic variations)
        - Smart Change Detection (optional, skip updates when meaning unchanged)
        - Change tracking with highlighting
        - Partial updates (when track_deleted is disabled)
        """
        node_id = node['id']
        node_config = node.get('config', {})
        
        log_execution_func(execution_id, node_id, "info", "Executing Excel Update node")
        
        try:
            # Check if update utilities are available
            if update_excel_with_changes is None:
                raise RuntimeError("Excel Update utilities not available. Please ensure excel_update_utils.py is installed.")
            
            # Get input data
            input_variable = node_config.get('inputVariable', '')
            input_data = replace_variable_references_func(input_variable, variables)
            
            if input_data is None:
                raise ValueError(f"Input variable '{input_variable}' is empty or not found")
            
            # Parse JSON string if necessary
            if isinstance(input_data, str):
                trimmed = input_data.strip()
                if trimmed and (trimmed.startswith('{') or trimmed.startswith('[')):
                    try:
                        input_data = json.loads(trimmed)
                        log_execution_func(execution_id, node_id, "debug", 
                                        f"Parsed JSON string to {type(input_data).__name__}")
                    except json.JSONDecodeError:
                        pass
            
            # Ensure input_data is a list
            if isinstance(input_data, dict):
                if 'fields' in input_data:
                    flat_data = {}
                    for field_name, field_info in input_data['fields'].items():
                        if isinstance(field_info, dict):
                            flat_data[field_name] = field_info.get('value')
                        else:
                            flat_data[field_name] = field_info
                    input_data = [flat_data]
                else:
                    input_data = [input_data]
            elif not isinstance(input_data, list):
                raise ValueError(f"Input data must be a dict or list, got {type(input_data).__name__}")
            
            log_execution_func(execution_id, node_id, "debug", f"Input data rows: {len(input_data)}")
            
            # Get file path
            excel_file_path = node_config.get('excelOutputPath', '')
            excel_file_path = replace_variable_references_func(excel_file_path, variables)
            
            if not excel_file_path:
                raise ValueError("Output File Path is required for UPDATE operation")
            
            log_execution_func(execution_id, node_id, "info", 
                            f"Updating Excel file: {excel_file_path}")
            
            # Get key columns
            key_columns_str = node_config.get('keyColumns', '')
            if not key_columns_str:
                raise ValueError("Key Column(s) are required for UPDATE operation")
            
            key_columns = [col.strip() for col in key_columns_str.split(',') if col.strip()]
            
            if not key_columns:
                raise ValueError("At least one key column must be specified")
            
            log_execution_func(execution_id, node_id, "debug", f"Key columns: {key_columns}")
            
            # Get sheet name
            sheet_name = node_config.get('excelSheetName', '') or None
            
            # Get field mapping
            field_mapping = node_config.get('fieldMapping')
            if isinstance(field_mapping, str):
                try:
                    field_mapping = json.loads(field_mapping)
                except json.JSONDecodeError:
                    field_mapping = None
            
            # Apply field mapping to input data if provided
            if field_mapping:
                mapped_data = []
                for row in input_data:
                    mapped_row = {}
                    for source_field, target_column in field_mapping.items():
                        if source_field in row:
                            mapped_row[target_column] = row[source_field]
                    for key, value in row.items():
                        if key not in field_mapping:
                            mapped_row[key] = value
                    mapped_data.append(mapped_row)
                input_data = mapped_data
                log_execution_func(execution_id, node_id, "debug", "Applied field mapping to input data")
            
            # ================================================================
            # AI KEY MATCHING (if enabled)
            # ================================================================
            use_ai_key_matching = node_config.get('useAIKeyMatching', False)
            ai_key_instructions = node_config.get('aiKeyMatchingInstructions', '')
            
            if use_ai_key_matching:
                log_execution_func(execution_id, node_id, "info", "AI key matching enabled")
                input_data = self._apply_ai_key_matching(
                    execution_id=execution_id,
                    node_id=node_id,
                    input_data=input_data,
                    excel_file_path=excel_file_path,
                    key_columns=key_columns,
                    sheet_name=sheet_name,
                    instructions=ai_key_instructions,
                    log_execution_func=log_execution_func
                )
            
            # ================================================================
            # SMART CHANGE DETECTION (if enabled)
            # ================================================================
            use_smart_change_detection = node_config.get('useSmartChangeDetection', False)
            smart_change_strictness = node_config.get('smartChangeStrictness', 'strict')
            rows_skipped_semantic = 0
            
            if use_smart_change_detection:
                log_execution_func(execution_id, node_id, "info", 
                                  f"Smart Change Detection enabled ({smart_change_strictness} mode)")
                input_data, rows_skipped_semantic = self._apply_smart_change_detection(
                    execution_id=execution_id,
                    node_id=node_id,
                    input_data=input_data,
                    excel_file_path=excel_file_path,
                    key_columns=key_columns,
                    sheet_name=sheet_name,
                    strictness=smart_change_strictness,
                    log_execution_func=log_execution_func
                )
            
            # ================================================================
            # GET UPDATE OPTIONS
            # ================================================================
            highlight_changes = node_config.get('highlightChanges', True)
            track_deleted = node_config.get('trackDeletedRows', False)
            add_new_records = node_config.get('addNewRecords', True)
            change_log_sheet = node_config.get('changeLogSheet', '') or None
            
            log_execution_func(execution_id, node_id, "debug", 
                            f"Update config: highlight={highlight_changes}, "
                            f"track_deleted={track_deleted}, "
                            f"add_new_records={add_new_records}, "
                            f"change_log={change_log_sheet}")
            
            # ================================================================
            # BUILD CONFIG AND EXECUTE UPDATE
            # ================================================================
            config = ExcelUpdateConfig(
                key_columns=key_columns,
                highlight_changes=highlight_changes,
                change_highlight_color=node_config.get('changeHighlightColor', '#FFFF00'),
                new_row_color=node_config.get('newRowColor', '#90EE90'),
                deleted_row_color=node_config.get('deletedRowColor', '#FFB6C1'),
                track_deleted_rows=track_deleted,
                add_new_records=add_new_records,
                mark_deleted_as=node_config.get('markDeletedAs', 'strikethrough'),
                add_change_timestamp=node_config.get('addChangeTimestamp', True),
                timestamp_column=node_config.get('timestampColumn', 'Last Updated'),
                change_log_sheet=change_log_sheet
            )

            log_execution_func(execution_id, node_id, "info", 
                  f"DIAGNOSTIC: About to update with {len(input_data)} rows")
            
            result = update_excel_with_changes(
                file_path=excel_file_path,
                new_data=input_data,
                config=config,
                sheet_name=sheet_name,
                output_path=excel_file_path
            )
            
            # ================================================================
            # PROCESS RESULT
            # ================================================================
            if result.success:
                log_execution_func(execution_id, node_id, "info",
                                f"Excel update complete: {result.rows_updated} updated, "
                                f"{result.rows_added} added, {result.rows_deleted} deleted, "
                                f"{result.cells_changed} cells changed"
                                + (f", {rows_skipped_semantic} skipped (semantic)" if rows_skipped_semantic > 0 else ""))
                
                return {
                    'success': True,
                    'data': {
                        'file_path': excel_file_path,
                        'rows_updated': result.rows_updated,
                        'rows_added': result.rows_added,
                        'rows_deleted': result.rows_deleted,
                        'rows_skipped_semantic': rows_skipped_semantic,
                        'cells_changed': result.cells_changed,
                        'total_changes': len(result.changes) if result.changes else 0,
                        'has_changes': result.cells_changed > 0 or result.rows_added > 0 or result.rows_deleted > 0,
                        'changes': [c.to_dict() for c in (result.changes[:100] if result.changes else [])]
                    }
                }
            else:
                error_msg = '; '.join(result.errors) if result.errors else 'Unknown error'
                raise RuntimeError(f"Update failed: {error_msg}")
                
        except Exception as e:
            error_msg = f"Excel Update failed: {str(e)}"
            log_execution_func(execution_id, node_id, "error", error_msg)
            logger.error(error_msg, exc_info=True)
            return {
                'success': False,
                'error': error_msg,
                'data': {}
            }

    def _apply_ai_key_matching(
        self,
        execution_id: str,
        node_id: str,
        input_data: List[Dict],
        excel_file_path: str,
        key_columns: List[str],
        sheet_name: str = None,
        instructions: str = None,
        log_execution_func = None
    ) -> List[Dict]:
        """
        Apply AI key matching to transform incoming keys to match existing keys.
        
        When key values have minor variations (typos, word order, singular/plural),
        AI identifies which incoming records match existing records and transforms
        the incoming key values to match exactly, preventing duplicate rows.
        
        Args:
            execution_id: Workflow execution ID
            node_id: Node ID for logging
            input_data: Incoming data rows
            excel_file_path: Path to existing Excel file
            key_columns: Columns that form the composite key
            sheet_name: Target sheet
            instructions: Optional AI instructions for matching
            log_execution_func: Logging function
            
        Returns:
            Modified input_data with key values adjusted to match existing keys
        """
        if not input_data:
            return input_data
        
        # Read existing data from Excel
        if read_excel_to_dict is None:
            raise RuntimeError("read_excel_to_dict not available - ensure excel_update_utils.py is installed")
        
        case_sensitive = False
        
        existing_rows, existing_row_nums, existing_columns, data_start_row = read_excel_to_dict(
            excel_file_path,
            sheet_name=sheet_name,
            key_columns=key_columns,
            case_sensitive=case_sensitive
        )
        
        if not existing_rows:
            log_execution_func(execution_id, node_id, "debug", 
                              "No existing rows in Excel, skipping AI key matching")
            return input_data
        
        # Convert existing rows to list format
        existing_data = list(existing_rows.values())
        existing_keys = list(existing_rows.keys())
        
        log_execution_func(execution_id, node_id, "debug",
                          f"AI key matching: {len(input_data)} incoming vs {len(existing_data)} existing rows")
        
        # Use AI to find matches
        matcher = AIKeyMatcher()
        
        # Get AI to match incoming to existing
        match_result = matcher.match_incoming_to_existing(
            incoming_records=input_data,
            existing_records=existing_data,
            key_columns=key_columns,
            instructions=instructions or self._get_default_key_matching_instructions()
        )
        
        # Apply matches by modifying the key columns in input_data
        modified_data = []
        matches_found = 0
        
        for row in input_data:
            # Case-insensitive column lookup
            row_lower = {k.lower(): v for k, v in row.items()}
            key_parts = []
            for col in key_columns:
                if col in row:
                    value = row[col]
                elif col.lower() in row_lower:
                    value = row_lower[col.lower()]
                else:
                    value = ''
                key_parts.append(str(value).strip() if value else '')
            original_key = '|'.join(key_parts)
            
            # Check if AI found a match
            matched_existing_key = match_result.get(original_key)
            
            if matched_existing_key:
                # Normalize for lookup (existing_rows uses lowercase keys when case_sensitive=False)
                lookup_key = matched_existing_key.lower() if not case_sensitive else matched_existing_key

                if lookup_key in existing_rows:
                    # Found a match - update key columns to match existing row exactly
                    modified_row = row.copy()  # Don't modify original
                    existing_row = existing_rows[lookup_key]
                    
                    for col in key_columns:
                        if col in existing_row and existing_row[col] is not None:
                            modified_row[col] = existing_row[col]
                    
                    matches_found += 1
                    log_execution_func(execution_id, node_id, "debug",
                                    f"AI matched: '{original_key}' -> '{matched_existing_key}'")
                    
                    modified_data.append(modified_row)
                else:
                    # No match found - keep original
                    modified_data.append(row)
            else:
                # No AI match found - this is a NEW row, keep it as-is for insertion
                modified_data.append(row)
        
        log_execution_func(execution_id, node_id, "info",
                          f"AI key matching complete: {matches_found} matches found out of {len(input_data)} rows")
        
        return modified_data
    
    def _get_default_key_matching_instructions(self) -> str:
        """Get default instructions for AI key matching."""
        return """Match incoming records to existing records based on semantic similarity of their composite keys.

Guidelines:
1. Match records that clearly represent the same entity or concept
2. Tolerate minor variations: plural/singular, word order, abbreviations, extra whitespace, punctuation differences
3. Be conservative - only match when confident; if unsure, return null (no match)
4. A false negative (missing a match) is better than a false positive (wrong match)"""

    def _apply_smart_change_detection(
        self,
        execution_id: str,
        node_id: str,
        input_data: List[Dict],
        excel_file_path: str,
        key_columns: List[str],
        sheet_name: str = None,
        strictness: str = 'strict',
        log_execution_func = None
    ) -> tuple:
        """
        Apply Smart Change Detection to filter out updates where meaning hasn't changed.
        
        Args:
            execution_id: Workflow execution ID
            node_id: Node ID for logging
            input_data: Incoming data rows (possibly already key-matched)
            excel_file_path: Path to existing Excel file
            key_columns: Columns that form the composite key
            sheet_name: Target sheet
            strictness: 'strict' or 'lenient' mode
            log_execution_func: Logging function
            
        Returns:
            Tuple of (filtered_input_data, rows_skipped_count)
        """
        log_execution_func(execution_id, node_id, "info", 
                      f"DIAGNOSTIC: Smart change detection received {len(input_data) if input_data else 0} rows")
        
        if not input_data:
            return input_data, 0
        
        # Read existing data from Excel
        if read_excel_to_dict is None:
            raise RuntimeError("read_excel_to_dict not available")
        
        existing_rows, _, _, _ = read_excel_to_dict(
            excel_file_path,
            sheet_name=sheet_name,
            key_columns=key_columns,
            case_sensitive=False
        )
        
        if not existing_rows:
            log_execution_func(execution_id, node_id, "debug", 
                              "No existing rows in Excel, skipping smart change detection")
            return input_data, 0
        
        # Build list of change candidates (rows that have differences)
        change_candidates = build_change_candidates(
            input_data=input_data,
            existing_rows=existing_rows,
            key_columns=key_columns,
            value_columns=None  # Check all non-key columns
        )
        
        if not change_candidates:
            log_execution_func(execution_id, node_id, "debug", 
                              "No changes detected, nothing to evaluate")
            return input_data, 0
        
        log_execution_func(execution_id, node_id, "debug",
                          f"Found {len(change_candidates)} field changes to evaluate")
        
        # Evaluate changes with AI
        detector = SmartChangeDetector()
        evaluations = detector.evaluate_changes(
            changes=change_candidates,
            strictness=strictness,
            instructions=None
        )
        
        # Group evaluations by row key and determine if any field in the row should update
        row_should_update = {}
        for candidate in change_candidates:
            row_key = candidate.row_key
            evaluation = evaluations.get(row_key)
            
            if evaluation and evaluation.should_update:
                row_should_update[row_key] = True
            elif row_key not in row_should_update:
                row_should_update[row_key] = False
        
        # Filter input data
        filtered_data = []
        skipped_count = 0
        
        for row in input_data:
            key_parts = [str(row.get(col, '')).strip() for col in key_columns]
            row_key = '|'.join(key_parts)
            
            # Check if this row has been evaluated
            if row_key in row_should_update:
                if row_should_update[row_key]:
                    filtered_data.append(row)
                else:
                    skipped_count += 1
                    log_execution_func(execution_id, node_id, "debug",
                                      f"Skipping row (semantically equivalent): {row_key}")
            else:
                # Row wasn't in evaluations (new row or no changes detected)
                filtered_data.append(row)
        
        log_execution_func(execution_id, node_id, "info",
                          f"Smart change detection: {len(filtered_data)} rows to process, "
                          f"{skipped_count} skipped (semantically equivalent)")
        
        return filtered_data, skipped_count

    def _execute_excel_export_node(self, execution_id: str, node: Dict, variables: Dict) -> Dict:
        """Execute an Excel Export node - writes variable data to Excel
        
        This node allows writing any variable data to Excel, with support for:
        - Single row export (dict input)
        - Multiple row export (array input with flatten option)
        - Carry-forward fields from parent context
        - Field mapping (AI or manual)
        
        Args:
            execution_id: Unique ID of the workflow execution
            node: Node definition containing configuration
            variables: Current workflow variables
            
        Returns:
            Result dictionary with success status and file path
        """
        
        
        node_id = node['id']
        node_config = node.get('config', {})
        
        self.log_execution(
            execution_id, node_id, "info", 
            "Executing Excel Export node")

        # Check for UPDATE operation
        excel_operation = node_config.get('excelOperation', 'append')
        if excel_operation == 'update':
            return self._execute_excel_update_node(
                execution_id=execution_id,
                node=node,
                variables=variables,
                log_execution_func=self.log_execution,
                replace_variable_references_func=self._replace_variable_references
            )
        
        try:
            # Get input data from variable
            input_variable = node_config.get('inputVariable', '')
            input_data = self._replace_variable_references(input_variable, variables)
            
            if input_data is None:
                raise ValueError(f"Input variable '{input_variable}' is empty or not found")
            
            self.log_execution(
                execution_id, node_id, "debug",
                f"Input data type: {type(input_data).__name__}")
            
            # IMPORTANT: If input_data is a JSON string, parse it back to dict/list
            # This happens because _replace_variable_references converts dicts/lists to JSON strings
            if isinstance(input_data, str):
                trimmed = input_data.strip()
                if trimmed and (trimmed.startswith('{') or trimmed.startswith('[')):
                    try:
                        input_data = json.loads(trimmed)
                        self.log_execution(
                            execution_id, node_id, "debug",
                            f"Parsed JSON string to {type(input_data).__name__}")
                    except json.JSONDecodeError:
                        # Not valid JSON, keep as string
                        self.log_execution(
                            execution_id, node_id, "debug",
                            "Input looks like JSON but failed to parse, keeping as string")
            
            # Get flatten option
            flatten_array = node_config.get('flattenArray', False)
            
            # Get carry-forward fields
            carry_forward_str = node_config.get('carryForwardFields', '')
            carry_forward_fields = [f.strip() for f in carry_forward_str.split(',') if f.strip()]
            
            # Build carry-forward values from variables
            carry_forward_values = {}
            if carry_forward_fields:
                for cf_field in carry_forward_fields:
                    # Try to get from variables using various patterns
                    cf_value = None
                    
                    # Try direct variable reference
                    if cf_field in variables:
                        cf_value = variables[cf_field]
                    else:
                        # Try to get from common parent objects
                        for var_name in ['extractedData', '_previousStepOutput']:
                            parent = variables.get(var_name, {})
                            if isinstance(parent, dict) and cf_field in parent:
                                cf_value = parent[cf_field]
                                break
                    
                    if cf_value is not None:
                        carry_forward_values[cf_field] = cf_value
                        self.log_execution(
                            execution_id, node_id, "debug",
                            f"Carry-forward field '{cf_field}' = {str(cf_value)[:50]}")
            
            # Prepare rows to write
            rows_to_write = []
            
            if flatten_array and isinstance(input_data, list):
                # Multiple rows - each array item becomes a row
                self.log_execution(
                    execution_id, node_id, "info",
                    f"Flattening array with {len(input_data)} items to rows")
                
                for item in input_data:
                    if isinstance(item, dict):
                        # Merge carry-forward values with item
                        row = {**carry_forward_values, **item}
                        rows_to_write.append(row)
                    else:
                        # Simple value - wrap in dict
                        row = {**carry_forward_values, 'value': item}
                        rows_to_write.append(row)
            elif isinstance(input_data, list):
                # Array but not flattening - write as single cell or handle specially
                if len(input_data) > 0 and isinstance(input_data[0], dict):
                    # List of dicts - write each as a row
                    for item in input_data:
                        row = {**carry_forward_values, **item}
                        rows_to_write.append(row)
                else:
                    # List of simple values - join as string
                    row = {**carry_forward_values, 'values': ', '.join(str(v) for v in input_data)}
                    rows_to_write.append(row)
            elif isinstance(input_data, dict):
                # Single dict - one row
                row = {**carry_forward_values, **input_data}
                rows_to_write.append(row)
            else:
                # Simple value - wrap in dict
                row = {**carry_forward_values, 'value': input_data}
                rows_to_write.append(row)
            
            self.log_execution(
                execution_id, node_id, "info",
                f"Prepared {len(rows_to_write)} row(s) for export")
            
            # Get Excel configuration
            excel_output_path = self._replace_variable_references(
                node_config.get('excelOutputPath', ''), variables)
            excel_operation = node_config.get('excelOperation', 'append')
            excel_template_path = self._replace_variable_references(
                node_config.get('excelTemplatePath', ''), variables)
            sheet_name = self._replace_variable_references(
                node_config.get('excelSheetName', ''), variables)
            
            if not sheet_name:
                sheet_name = None
                
            if not excel_output_path:
                raise ValueError("Excel output path is required")
            
            # Map operation names
            operation_map = {
                'new': 'new',
                'template': 'new_from_template',
                'append': 'append'
            }
            operation = operation_map.get(excel_operation, 'append')
            
            # Get field mapping configuration
            field_mapping = node_config.get('fieldMapping', None)
            if field_mapping and isinstance(field_mapping, str):
                try:
                    field_mapping = json.loads(field_mapping)
                except json.JSONDecodeError:
                    field_mapping = None
                    
            ai_mapping_instructions = node_config.get('aiMappingInstructions', '')
            
            self.log_execution(
                execution_id, node_id, "debug",
                f"Excel config - Path: {excel_output_path}, Operation: {operation}, "
                f"Template: {excel_template_path}, Sheet: {sheet_name}")
            
            # Write each row to Excel
            total_rows_written = 0
            last_result = None
            
            for i, row_data in enumerate(rows_to_write):
                # Convert row to extraction_result format
                extraction_result = self._convert_dict_to_extraction_format(row_data)
                
                self.log_execution(
                    execution_id, node_id, "debug",
                    f"Writing row {i+1}/{len(rows_to_write)}: {list(row_data.keys())}")
                
                # Write to Excel
                result = write_extraction_to_excel(
                    extraction_result=extraction_result,
                    output_path=excel_output_path,
                    template_path=excel_template_path if excel_template_path else None,
                    operation=operation,
                    include_assumptions=False,
                    include_sources=False,
                    include_confidence=False,
                    field_mapping=field_mapping,
                    ai_mapping_instructions=ai_mapping_instructions,
                    sheet_name=sheet_name
                )
                
                if result.get('success'):
                    total_rows_written += result.get('rows_written', 1)
                    last_result = result
                    
                    # After first write, always append for subsequent rows
                    if operation == 'new' or operation == 'new_from_template':
                        operation = 'append'
                        excel_template_path = excel_output_path  # Use output file as template for appends
                else:
                    self.log_execution(
                        execution_id, node_id, "warning",
                        f"Failed to write row {i+1}: {result.get('error', 'Unknown error')}")
            
            if last_result and last_result.get('success'):
                self.log_execution(
                    execution_id, node_id, "info",
                    f"Excel export complete: {total_rows_written} row(s) written to {excel_output_path}")
                
                return {
                    'success': True,
                    'data': {
                        'file_path': excel_output_path,
                        'rows_written': total_rows_written,
                        'sheet_name': sheet_name or 'default'
                    }
                }
            else:
                error_msg = last_result.get('error', 'No rows written') if last_result else 'No data to write'
                raise ValueError(error_msg)
                
        except Exception as e:
            error_msg = f"Excel Export failed: {str(e)}"
            self.log_execution(execution_id, node_id, "error", error_msg)
            logger.error(error_msg, exc_info=True)
            return {
                'success': False,
                'error': error_msg,
                'data': {}
            }

    def _convert_dict_to_extraction_format(self, data: Dict) -> Dict:
        """Convert a simple dictionary to the extraction_result format expected by write_extraction_to_excel
        
        Args:
            data: Simple dict like {"field1": "value1", "field2": "value2"}
            
        Returns:
            Extraction result format: {"fields": {"field1": {"value": "value1"}, ...}}
        """
        fields = {}
        for key, value in data.items():
            fields[key] = {
                "value": value,
                "assumptions": [],
                "sources": []
            }
        
        return {
            "fields": fields,
            "global_assumptions": []
        }


    def _execute_text_extraction(self, execution_id: str, node_id: str, 
                            input_content: str, fields: List[Dict], 
                            node_config: Dict) -> Tuple[Dict, Dict]:
        """Execute text-based extraction using AIExtractExecutor.
        
        Args:
            execution_id: Workflow execution ID
            node_id: Node ID
            input_content: Text content to extract from
            fields: Field definitions
            node_config: Node configuration
            
        Returns:
            Tuple of (extracted_data, extraction_result_full)
            - extraction_result_full contains cell_formatting if present
        """
        from ai_extract_executor import AIExtractExecutor
        from AppUtils import azureQuickPrompt
        
        self.log_execution(
            execution_id, node_id, "debug",
            f"Executing text extraction on {len(input_content)} characters")
        
        # Create AI call wrapper
        def ai_call(prompt, system_message):
            return azureQuickPrompt(prompt, system=system_message, temp=0.0)
        
        # Create executor and run extraction
        executor = AIExtractExecutor(ai_call)
        
        # Get formatting instructions from config
        formatting_instructions = node_config.get('formattingInstructions', '')
        if formatting_instructions:
            self.log_execution(
                execution_id, node_id, "debug",
                f"Formatting instructions for text extraction: {formatting_instructions[:100]}...")
        
        config = {
            'extraction_type': node_config.get('extractionType', 'field_extraction'),
            'fields': fields,
            'special_instructions': node_config.get('specialInstructions', ''),
            'fail_on_missing_required': node_config.get('failOnMissingRequired', False),
            'formatting_instructions': formatting_instructions  # NEW: Pass formatting instructions
        }
        
        result = executor.execute(config, input_content)
        
        if not result.get('success'):
            raise ValueError(result.get('error', 'Text extraction failed'))
        
        extracted_data = result.get('data', {})
        
        self.log_execution(
            execution_id, node_id, "info",
            f"Text extraction complete. Fields extracted: {len(extracted_data)}")
        
        # NEW: Build extraction_result_full to include cell_formatting
        # This matches the structure from document extraction
        extraction_result_full = None
        cell_formatting = result.get('cell_formatting')
        
        if cell_formatting:
            self.log_execution(
                execution_id, node_id, "info",
                f"AI suggested formatting for {len(cell_formatting)} field(s)")
            
            # Build a structure similar to document extraction result
            # so that excel_utils can process it the same way
            extraction_result_full = {
                'fields': {k: {'value': v} for k, v in extracted_data.items()},
                'cell_formatting': cell_formatting
            }
        
        return extracted_data, extraction_result_full


    def _execute_document_extraction(self, execution_id: str, node_id: str,
                                    document_path: str, fields: List[Dict],
                                    node_config: Dict) -> Tuple[Dict, Dict]:
        """Execute document-based extraction using populate_schema_with_claude.

        For PDF files, uses the native document extraction pipeline.
        For all other file types (txt, csv, docx, xlsx, png, etc.), extracts
        text first using attachment_text_extractor, then routes through
        text-based extraction.

        Args:
            execution_id: Workflow execution ID
            node_id: Node ID
            document_path: Path to document file
            fields: Field definitions
            node_config: Node configuration

        Returns:
            Tuple of (extracted_data_simple, extraction_result_full)
        """
        from AppUtils import populate_schema_with_claude, populate_schema_with_claude_chunked

        if not os.path.exists(document_path):
            raise FileNotFoundError(f"Document not found: {document_path}")

        # Check file extension — only PDFs go through the native document pipeline.
        # All other supported file types are converted to text first.
        file_ext = os.path.splitext(document_path)[1].lower()
        if file_ext != '.pdf':
            self.log_execution(
                execution_id, node_id, "info",
                f"Non-PDF file detected ({file_ext}). Extracting text before AI extraction.")

            from attachment_text_extractor import extract_text_from_attachment

            with open(document_path, 'rb') as f:
                file_bytes = f.read()

            extraction = extract_text_from_attachment(
                file_bytes=file_bytes,
                filename=os.path.basename(document_path)
            )

            if not extraction.get('success'):
                raise ValueError(
                    f"Failed to extract text from {os.path.basename(document_path)}: "
                    f"{extraction.get('error', 'Unknown error')}")

            extracted_text = extraction.get('text', '')
            if not extracted_text.strip():
                raise ValueError(
                    f"No text content could be extracted from {os.path.basename(document_path)}")

            self.log_execution(
                execution_id, node_id, "info",
                f"Extracted {len(extracted_text)} characters from {file_ext} file. "
                f"Routing to text extraction.")

            return self._execute_text_extraction(
                execution_id, node_id, extracted_text, fields, node_config)

        self.log_execution(
            execution_id, node_id, "info",
            f"Executing document extraction on: {document_path}")
        
        # Convert fields config to schema_fields format
        # populate_schema_with_claude expects: {"field_name": "description", ...}
        schema_fields = self._build_schema_fields_for_document_extraction(fields)
        
        self.log_execution(
            execution_id, node_id, "debug",
            f"Schema fields for extraction: {list(schema_fields.keys())}")
        
        # Add special instructions if configured
        special_instructions = node_config.get('specialInstructions', '')
        if special_instructions:
            # Prepend special instructions context to first field's description
            # (This is a workaround - ideally populate_schema_with_claude would accept extra instructions)
            first_key = list(schema_fields.keys())[0] if schema_fields else None
            if first_key:
                schema_fields[first_key] = f"{schema_fields[first_key]} [Additional context: {special_instructions}]"

        # Get formatting instructions from config
        formatting_instructions = node_config.get('formattingInstructions', '')

        self.log_execution(
                execution_id, node_id, "info",
                f"User formatting instructions: {formatting_instructions}")

        logger.debug(f"=====>>>>> Formatting instructions: {formatting_instructions}")
        logger.debug(f"=====>>>>> Full node configuration: {node_config}")
        
        # Call document extraction
        extraction_result = populate_schema_with_claude_chunked(
            pdf_path=document_path,
            schema_fields=schema_fields,
            module_name=f"workflow_{execution_id}",
            request_id=f"{execution_id}_{node_id}",
            formatting_instructions=formatting_instructions
        )

        logger.debug("******************************************************************************")
        logger.debug("Result from Claude:")
        logger.debug(f"Extraction result: {to_truncated_str(extraction_result)}")
        logger.debug("******************************************************************************")
        
        # Log any global assumptions
        global_assumptions = extraction_result.get('global_assumptions', [])
        if global_assumptions:
            self.log_execution(
                execution_id, node_id, "warning",
                f"Document extraction assumptions: {'; '.join(global_assumptions)}")
            
        # Log cell formatting if present
        cell_formatting = extraction_result.get('cell_formatting', {})
        if cell_formatting:
            formatted_fields = list(cell_formatting.keys())
            self.log_execution(
                execution_id, node_id, "info",
                f"AI suggested formatting for {len(formatted_fields)} field(s): {formatted_fields}")
        
        # Convert to simple key:value format for compatibility
        extracted_data = {}
        fields_result = extraction_result.get('fields', {})
        
        for field_name, field_info in fields_result.items():
            value = field_info.get('value')
            extracted_data[field_name] = value
            
            # Log any field-specific assumptions
            assumptions = field_info.get('assumptions', [])
            if assumptions:
                self.log_execution(
                    execution_id, node_id, "debug",
                    f"Field '{field_name}' assumptions: {'; '.join(assumptions)}")
        
        self.log_execution(
            execution_id, node_id, "info",
            f"Document extraction complete. Fields extracted: {len(extracted_data)}")
        
        return extracted_data, extraction_result


    def _write_extraction_to_excel(self, execution_id: str, node_id: str,
                                    extraction_result: Dict, node_config: Dict,
                                    variables: Dict, is_document_extraction: bool) -> Dict:
        """Write extraction results to Excel file.
        
        Args:
            execution_id: Workflow execution ID
            node_id: Node ID
            extraction_result: Extraction result (full format if document, simple if text)
            node_config: Node configuration
            variables: Workflow variables
            is_document_extraction: Whether this was document extraction
            
        Returns:
            Excel operation result dict
        """
        from excel_utils import write_extraction_to_excel
        
        self.log_execution(
            execution_id, node_id, "info",
            "Writing extraction results to Excel")
        
        # Get Excel configuration
        excel_output_path = self._replace_variable_references(
            node_config.get('excelOutputPath', ''), variables)
        excel_operation = node_config.get('excelOperation', 'new')
        excel_template_path = self._replace_variable_references(
            node_config.get('excelTemplatePath', ''), variables)
        
        # Get sheet name from config
        sheet_name = self._replace_variable_references(
            node_config.get('excelSheetName', ''), variables)

        if not sheet_name:
            sheet_name = None

        include_assumptions = node_config.get('includeAssumptions', False)
        include_sources = node_config.get('includeSources', False)
        include_confidence = node_config.get('includeConfidence', False)
        # Parse fieldMapping - it may be a JSON string from the hidden field
        field_mapping = node_config.get('fieldMapping', None)
        if field_mapping and isinstance(field_mapping, str):
            try:
                field_mapping = json.loads(field_mapping)
            except json.JSONDecodeError:
                field_mapping = None
        ai_mapping_instructions = node_config.get('aiMappingInstructions', '')
        
        if not excel_output_path:
            raise ValueError("Excel output path is required when outputToExcel is enabled")
        
        self.log_execution(
            execution_id, node_id, "debug",
            f"Excel config - Path: {excel_output_path}, Operation: {excel_operation}, "
            f"Template: {excel_template_path}")
        
        # Convert to document format if needed
        # Skip conversion if already in document format (has 'fields' key)
        if not is_document_extraction and 'fields' not in extraction_result:
            extraction_result = self._convert_to_document_format(extraction_result)

        logger.debug(f"Extraction result: {to_truncated_str(extraction_result)}")
        logger.debug(f"Field mapping: {field_mapping}")
        logger.debug(f"Include assumptions: {include_assumptions}")
        logger.debug(f"Include sources: {include_sources}")
        logger.debug(f"Include confidence: {include_confidence}")
        logger.debug(f"Ai mapping instructions: {ai_mapping_instructions}")
        logger.debug(f"Excel template path: {excel_template_path}")
        logger.debug(f"Sheet name: {sheet_name}")

        # Get field definitions for AI mapping context
        field_definitions = node_config.get('fields', [])
        logger.debug(f"Field definitions: {field_definitions}")
        
        # Write to Excel
        excel_result = write_extraction_to_excel(
            extraction_result=extraction_result,
            output_path=excel_output_path,
            template_path=excel_template_path if excel_template_path else None,
            operation=excel_operation,
            include_assumptions=include_assumptions,
            include_sources=include_sources,
            include_confidence=include_confidence,
            field_mapping=field_mapping,
            ai_mapping_instructions=ai_mapping_instructions,
            sheet_name=sheet_name,
            field_definitions=field_definitions
        )

        logger.debug(f"Excel result: {excel_result}")
        
        if excel_result.get('success'):
            self.log_execution(
                execution_id, node_id, "info",
                f"Excel file written successfully: {excel_result.get('file_path')}")
        else:
            self.log_execution(
                execution_id, node_id, "warning",
                f"Excel write issue: {excel_result.get('error', 'Unknown error')}")
        
        return excel_result

    def _convert_to_document_format(self, simple_extraction: Dict) -> Dict:
        """Convert simple key:value extraction to document extraction format.
        
        Text extraction returns: {"field_name": "value", ...}
        Document extraction returns: {"fields": {"field_name": {"value": ..., "assumptions": [], "sources": []}}}
        
        Also preserves cell_formatting if present.
        
        Args:
            simple_extraction: Simple key:value dict (may include cell_formatting)
            
        Returns:
            Document extraction format dict
        """
        # Check if cell_formatting is present and extract it
        cell_formatting = simple_extraction.pop('cell_formatting', None) if isinstance(simple_extraction, dict) else None
        
        fields = {}
        for field_name, value in simple_extraction.items():
            fields[field_name] = {
                "value": value,
                "assumptions": [],
                "sources": []
            }
        
        result = {
            "fields": fields,
            "global_assumptions": ["Extracted from text input (no source document)"]
        }
        
        # Preserve cell_formatting if it was present
        if cell_formatting:
            result["cell_formatting"] = cell_formatting
        
        return result


    def _find_end_loop_node(self, execution_id: str, loop_node_id: str) -> Optional[str]:
        """Find the End Loop node associated with a Loop node
        
        Args:
            execution_id: Unique ID of the workflow execution
            loop_node_id: ID of the Loop node
            
        Returns:
            end_loop_node_id: ID of the End Loop node or None
        """
        workflow_data = self._active_executions[execution_id]['workflow_data']
        nodes = workflow_data.get('nodes', [])
        connections = workflow_data.get('connections', [])
        
        # Use BFS to find End Loop node
        visited = set()
        queue = []
        
        # Start with connections from the loop node
        loop_connections = [c for c in connections if c['source'] == loop_node_id]
        for conn in loop_connections:
            if conn.get('type', 'pass') == 'pass':
                queue.append(conn['target'])
        
        while queue:
            node_id = queue.pop(0)
            if node_id in visited:
                continue
            visited.add(node_id)
            
            # Find the node
            node = next((n for n in nodes if n['id'] == node_id), None)
            if not node:
                continue
            
            # Check if this is an End Loop node
            if node.get('type') == 'End Loop':
                return node_id
            
            # Add connected nodes to queue
            next_connections = [c for c in connections if c['source'] == node_id]
            for conn in next_connections:
                if conn['target'] not in visited:
                    queue.append(conn['target'])
        
        return None
    

    def _execute_human_approval_node(self, execution_id: str, step_execution_id: str, node: Dict, variables: Dict) -> Optional[str]:
        """Execute a Human Approval node with user/group assignment
        Uses PASS/FAIL/COMPLETE connection types
        Properly handles database connections to avoid close() errors
        """
        # IMPORTANT: Create naive datetime representing UTC to avoid pyodbc timezone conversion issues.
        # pyodbc may convert timezone-aware datetimes to local server time before inserting,
        # but SQL Server datetime columns don't store timezone info. By using a naive datetime
        # that represents UTC (matching what SQL Server's getutcdate() returns), we ensure
        # consistent storage and retrieval across different server timezones.
        from datetime import datetime, timedelta, timezone

        def get_utc_now_naive():
            """Get current UTC time as naive datetime (no timezone info) for SQL Server compatibility"""
            return datetime.now(timezone.utc).replace(tzinfo=None)

        node_id = node['id']
        node_config = node.get('config', {})
        
        # Initialize database objects
        conn = None
        cursor = None
        
        try:
            # Extract approval details
            title = node_config.get('approvalTitle', 'Approval Required')
            description = node_config.get('approvalDescription', '')
            
            # Assignment fields
            assignee_type = node_config.get('assigneeType', '')
            assignee_id = node_config.get('assigneeId', None)
            
            # Convert priority to integer
            priority = node_config.get('priority', 0)
            if isinstance(priority, str):
                try:
                    priority = int(priority)
                except:
                    priority = 0
            
            # Handle timeout/due date
            due_date = None

            # Try dueHours first
            due_hours = node_config.get('dueHours')
            if due_hours:
                try:
                    due_hours = float(due_hours) if due_hours else 0
                    if due_hours > 0:
                        due_date = get_utc_now_naive() + timedelta(hours=due_hours)
                except (ValueError, TypeError):
                    self.log_execution(
                        execution_id, node_id, "warning", 
                        f"Invalid dueHours: {due_hours}")
            
            # Fall back to timeoutMinutes
            if not due_date:
                timeout_minutes = node_config.get('timeoutMinutes')
                if timeout_minutes:
                    try:
                        timeout_minutes = float(timeout_minutes) if timeout_minutes else 0
                        if timeout_minutes > 0:
                            due_date = get_utc_now_naive() + timedelta(minutes=timeout_minutes)
                    except (ValueError, TypeError):
                        pass
            
            timeout_action = node_config.get('timeoutAction', 'continue')
            
            # Process variable references
            title = self._replace_variable_references(title, variables)
            description = self._replace_variable_references(description, variables)
            approval_data = node_config.get('approvalData', '{}')
            approval_data = self._replace_variable_references(approval_data, variables)
            
            # Create approval request in database
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            request_id = str(uuid.uuid4())
            
            # Check for new columns
            cursor.execute("""
                SELECT COUNT(*) 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_NAME = 'ApprovalRequests' 
                AND COLUMN_NAME = 'assigned_to_type'
            """)
            
            has_new_columns = cursor.fetchone()[0] > 0
            
            if has_new_columns and assignee_type:
                # New schema with user/group support
                cursor.execute("""
                    INSERT INTO ApprovalRequests (
                        request_id, step_execution_id, title, description,
                        status, requested_at, assigned_to_type, assigned_to_id,
                        approval_data, priority, due_date
                    ) VALUES (?, ?, ?, ?, 'Pending', getutcdate(), ?, ?, ?, ?, ?)
                """, request_id, step_execution_id, title, description, 
                    assignee_type, assignee_id, approval_data, priority, due_date)
            else:
                # Old schema - use assignee field for backward compatibility
                old_assignee = node_config.get('assignee', '')
                cursor.execute("""
                    INSERT INTO ApprovalRequests (
                        request_id, step_execution_id, title, description,
                        status, requested_at, assigned_to, approval_data
                    ) VALUES (?, ?, ?, ?, 'Pending', getutcdate(), ?, ?)
                """, request_id, step_execution_id, title, description, 
                    old_assignee, approval_data)
            
            conn.commit()
            
            self.log_execution(
                execution_id, node_id, "info", 
                f"Created approval request: {title}", 
                {
                    "request_id": request_id,
                    "assignee_type": assignee_type,
                    "assignee_id": assignee_id,
                    "due_date": due_date.isoformat() if due_date else None
                })
            
            # Update step status
            self._update_step_status(execution_id, node_id, 'Paused')
            
            # Pause workflow
            execution_state = self._active_executions.get(execution_id)
            if not execution_state:
                raise ValueError(f"Execution state not found for {execution_id}")
                
            execution_state['paused'] = True
            self._update_workflow_status(execution_id, 'Paused')
            
            # Send notification if method exists
            if hasattr(self, '_send_approval_notification'):
                self._send_approval_notification(request_id, title, assignee_type, assignee_id)
            
            # Wait for approval
            approval_response = None
            has_timed_out = False
            
            while not approval_response and not execution_state.get('cancelled', False):
                # Check timeout - use naive UTC datetime for comparison
                if due_date and get_utc_now_naive() > due_date:
                    has_timed_out = True
                    self.log_execution(
                        execution_id, node_id, "warning", 
                        "Approval request timed out")
                    break
                
                # Check for approval response
                cursor.execute("""
                    SELECT status, comments, responded_by
                    FROM ApprovalRequests
                    WHERE request_id = ? AND status != 'Pending'
                """, request_id)
                
                row = cursor.fetchone()
                if row:
                    status, comments, responded_by = row
                    approval_response = {
                        'status': status,
                        'comments': comments,
                        'responded_by': responded_by
                    }
                    break
                
                # Check for cancel command
                try:
                    import queue
                    wait_time = 5
                    if due_date:
                        time_until_timeout = (due_date - get_utc_now_naive()).total_seconds()
                        if time_until_timeout > 0:
                            wait_time = min(5, time_until_timeout)
                    
                    if execution_id in self._execution_queues:
                        command = self._execution_queues[execution_id].get(timeout=wait_time)
                        if command == 'cancel':
                            execution_state['cancelled'] = True
                            break
                    else:
                        # No queue available, just wait
                        import time
                        time.sleep(wait_time)
                except queue.Empty:
                    pass
                except Exception as e:
                    self.log_execution(
                        execution_id, node_id, "debug", 
                        f"Queue check error: {str(e)}")
                    import time
                    time.sleep(5)
            
            # Handle timeout
            if has_timed_out and not approval_response:
                if timeout_action == 'continue':
                    cursor.execute("""
                        UPDATE ApprovalRequests
                        SET status = 'Timeout-Approved', response_at = getutcdate(),
                            comments = 'Auto-approved due to timeout',
                            responded_by = 'system-timeout'
                        WHERE request_id = ?
                    """, request_id)
                    conn.commit()
                    
                    approval_response = {
                        'status': 'Approved',
                        'comments': 'Auto-approved due to timeout',
                        'responded_by': 'system-timeout'
                    }
                else:
                    cursor.execute("""
                        UPDATE ApprovalRequests
                        SET status = 'Timeout-Rejected', response_at = getutcdate(),
                            comments = 'Auto-rejected due to timeout',
                            responded_by = 'system-timeout'
                        WHERE request_id = ?
                    """, request_id)
                    conn.commit()
                    
                    approval_response = {
                        'status': 'Rejected',
                        'comments': 'Auto-rejected due to timeout',
                        'responded_by': 'system-timeout'
                    }
            
            # Handle cancellation
            if execution_state.get('cancelled', False):
                cursor.execute("""
                    UPDATE ApprovalRequests
                    SET status = 'Cancelled', response_at = getutcdate()
                    WHERE request_id = ?
                """, request_id)
                conn.commit()
                
                self.log_execution(
                    execution_id, node_id, "warning", 
                    "Approval request cancelled")
                return None
            
            # Process approval response
            if approval_response:
                self.log_execution(
                    execution_id, node_id, "info", 
                    f"Approval {approval_response['status']}", 
                    approval_response)
                
                # Store results in variables
                variables[f"{node_id}_approved"] = approval_response['status'] in ['Approved', 'Timeout-Approved']
                variables[f"{node_id}_status"] = approval_response['status']
                variables[f"{node_id}_comments"] = approval_response.get('comments', '')
                variables[f"{node_id}_responded_by"] = approval_response.get('responded_by', '')
                
                # Update step status
                self._update_step_status(execution_id, node_id, 'Completed')
                
                # Resume workflow
                execution_state['paused'] = False
                self._update_workflow_status(execution_id, 'Running')
                
                # CRITICAL: Use PASS/FAIL/COMPLETE connections
                workflow_data = self._active_executions[execution_id]['workflow_data']
                connections = workflow_data.get('connections', [])
                next_connections = [c for c in connections if c['source'] == node_id]
                
                # Determine approval result
                is_approved = approval_response['status'] in ['Approved', 'Timeout-Approved']
                
                # Route based on connection types
                for conn in next_connections:
                    conn_type = conn.get('type', 'pass').lower()
                    
                    if conn_type == 'pass' and is_approved:
                        return conn['target']
                    elif conn_type == 'fail' and not is_approved:
                        return conn['target']
                    elif conn_type == 'complete':
                        return conn['target']
                
                # Default to first connection
                #if next_connections:
                    #return next_connections[0]['target']
                
                return None
                
        except Exception as e:
            self.log_execution(
                execution_id, node_id, "error", 
                f"Error in approval node: {str(e)}")
            
            self._update_step_status(execution_id, node_id, 'Failed')
            
            # Re-raise to trigger fail path
            raise
            
        finally:
            # Properly close database connections
            if cursor:
                try:
                    cursor.close()
                except:
                    pass
            if conn:
                try:
                    conn.close()
                except:
                    pass

    def _send_approval_notification(self, request_id: str, title: str, 
                                    assignee_type: str, assignee_id: int):
        """Send notification for approval request (placeholder for email/slack integration)"""
        # This is a placeholder for notification logic
        # You can integrate with email, Slack, or other notification systems here
        logger.info(f"Notification sent for approval {request_id} to {assignee_type}:{assignee_id}")

    def _get_available_assignees(self):
        """Get list of available users and groups for assignment"""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        # Get tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        assignees = {
            'users': [],
            'groups': []
        }
        
        try:
            # Get users with End User role or higher (roles 1, 2, 3)
            cursor.execute("""
                SELECT id, user_name, name, permissions
                FROM users 
                WHERE permissions IN (1, 2, 3)
                ORDER BY name
            """)
            
            for row in cursor.fetchall():
                role_name = {1: 'End User', 2: 'Developer', 3: 'Admin'}.get(row[3], 'Unknown')
                assignees['users'].append({
                    'id': row[0],
                    'username': row[1],
                    'name': row[2],
                    'role': role_name,
                    'display': f"{row[2]} ({row[1]}) - {role_name}"
                })
            
            # Get all active groups
            cursor.execute("""
                SELECT group_id, group_name, 
                    (SELECT COUNT(*) FROM user_groups ug WHERE ug.group_id = g.group_id) as member_count
                FROM groups g
                ORDER BY group_name
            """)
            
            for row in cursor.fetchall():
                assignees['groups'].append({
                    'id': row[0],
                    'name': row[1],
                    'member_count': row[2],
                    'display': f"{row[1]} ({row[2]} members)"
                })
                
        finally:
            cursor.close()
            conn.close()
        
        return assignees
    
    
    def _create_step_execution(
        self, execution_id: str, node_id: str, node_name: str, node_type: str) -> str:
        """Create a step execution record in the database
        
        Args:
            execution_id: Unique ID of the workflow execution
            node_id: ID of the node
            node_name: Display name of the node
            node_type: Type of the node
            
        Returns:
            step_execution_id: Unique ID of the step execution
        """
        step_execution_id = str(uuid.uuid4())
        
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        try:
            # First, get the tenant context
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

            cursor.execute("""
                INSERT INTO StepExecutions (
                    step_execution_id, execution_id, node_id, node_name, node_type,
                    status, started_at
                ) VALUES (?, ?, ?, ?, ?, 'Running', getutcdate())
            """, step_execution_id, execution_id, node_id, node_name, node_type)
            
            conn.commit()
            return step_execution_id
            
        except Exception as e:
            conn.rollback()
            logger.error(f"Error creating step execution: {str(e)}")
            raise e
        finally:
            cursor.close()
            conn.close()
    
    def _update_step_status(
        self, execution_id: str, node_id: str, status: str, 
        output_data: Dict = None, error_message: str = None):
        """Update the status of a step execution
        
        Args:
            execution_id: Unique ID of the workflow execution
            node_id: ID of the node
            status: New status
            output_data: Optional output data
            error_message: Optional error message
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        try:
            # First, get the tenant context
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            # Find the step execution record
            cursor.execute("""
                SELECT step_execution_id
                FROM StepExecutions
                WHERE execution_id = ? AND node_id = ?
                ORDER BY started_at DESC
            """, execution_id, node_id)
            
            row = cursor.fetchone()
            if not row:
                logger.warning(f"Step execution not found for node {node_id}")
                return
            
            step_execution_id = row[0]
            
            # Update the status
            try:
                if status in ['Completed', 'Failed', 'Approved', 'Rejected', 'Skipped']:
                    cursor.execute("""
                        UPDATE StepExecutions
                        SET status = ?, completed_at = getutcdate(), 
                            output_data = ?, error_message = ?
                        WHERE step_execution_id = ?
                    """, status, 
                        json.dumps(output_data) if output_data else None,
                        error_message,
                        step_execution_id)
                else:
                    cursor.execute("""
                        UPDATE StepExecutions
                        SET status = ?, 
                            output_data = ?, error_message = ?
                        WHERE step_execution_id = ?
                    """, status, 
                        json.dumps(output_data) if output_data else None,
                        error_message,
                        step_execution_id)
            except:
                logger.warning(f"Step execution update failed for node {node_id}, falling back to status only update...")
                cursor.execute("""
                        UPDATE StepExecutions
                        SET status = ?, completed_at = getutcdate(), 
                            output_data = ?, error_message = ?
                        WHERE step_execution_id = ?
                    """, status, 
                        None,
                        error_message,
                        step_execution_id)
            
            conn.commit()
            
        except Exception as e:
            conn.rollback()
            logger.error(f"Error updating step status: {str(e)}")
        finally:
            cursor.close()
            conn.close()
    
    def _update_workflow_status(self, execution_id: str, status: str):
        """Update the status of a workflow execution
        
        Args:
            execution_id: Unique ID of the workflow execution
            status: New status
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        try:
            # First, get the tenant context
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

            if status in ['Completed', 'Failed', 'Cancelled']:
                cursor.execute("""
                    UPDATE WorkflowExecutions
                    SET status = ?, completed_at = getutcdate()
                    WHERE execution_id = ?
                """, status, execution_id)
            else:
                cursor.execute("""
                    UPDATE WorkflowExecutions
                    SET status = ?
                    WHERE execution_id = ?
                """, status, execution_id)
            
            conn.commit()
            
        except Exception as e:
            conn.rollback()
            logger.error(f"Error updating workflow status: {str(e)}")
        finally:
            cursor.close()
            conn.close()
    
    def pause_workflow(self, execution_id: str) -> bool:
        """Pause a running workflow execution
        
        Args:
            execution_id: Unique ID of the workflow execution
            
        Returns:
            success: True if the workflow was paused
        """
        if execution_id not in self._active_executions:
            return False
        
        execution_state = self._active_executions[execution_id]
        if execution_state['status'] != 'Running':
            return False
        
        execution_state['paused'] = True
        self._update_workflow_status(execution_id, 'Paused')
        
        self.log_execution(
            execution_id, None, "info", 
            "Workflow execution paused manually")
        
        return True
    
    def resume_workflow(self, execution_id: str) -> bool:
        """Resume a paused workflow execution
        
        Args:
            execution_id: Unique ID of the workflow execution
            
        Returns:
            success: True if the workflow was resumed
        """
        if execution_id not in self._active_executions:
            return False
        
        execution_state = self._active_executions[execution_id]
        if execution_state['status'] != 'Paused':
            return False
        
        # Send resume command to the execution thread
        self._execution_queues[execution_id].put('resume')
        
        self.log_execution(
            execution_id, None, "info", 
            "Workflow execution resumed manually")
        
        return True
    
    def cancel_workflow(self, execution_id: str) -> bool:
        """Cancel a workflow execution
        
        Args:
            execution_id: Unique ID of the workflow execution
            
        Returns:
            success: True if the workflow was cancelled
        """
        if execution_id not in self._active_executions:
            return False
        
        execution_state = self._active_executions[execution_id]
        if execution_state['status'] in ['Completed', 'Failed', 'Cancelled']:
            return False
        
        # Send cancel command to the execution thread
        self._execution_queues[execution_id].put('cancel')
        
        execution_state['cancelled'] = True
        self._update_workflow_status(execution_id, 'Cancelled')
        
        self.log_execution(
            execution_id, None, "info", 
            "Workflow execution cancelled manually")
        
        return True
    
    def log_execution(
        self, execution_id: str, step_id: str, level: str, 
        message: str, details: Dict = None):
        """Log a workflow execution event
        
        Args:
            execution_id: Unique ID of the workflow execution
            step_id: ID of the step (node) or None
            level: Log level (info, warning, error, debug)
            message: Log message
            details: Additional details as dictionary
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        try:
            # First, get the tenant context
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

            print('Logging step execution...')
            # Find step execution ID if step_id is provided
            step_execution_id = None
            if step_id:
                cursor.execute("""
                    SELECT step_execution_id
                    FROM StepExecutions
                    WHERE execution_id = ? AND node_id = ?
                    ORDER BY started_at DESC
                """, execution_id, step_id)
                
                row = cursor.fetchone()
                if row:
                    step_execution_id = row[0]

            conn.commit()
            print('Adding log entry...', execution_id, step_execution_id, details)
            print('Details:', json.dumps(details) if details else None)
            # Insert log entry
            cursor.execute("""
                INSERT INTO ExecutionLogs (
                    execution_id, step_execution_id, timestamp,
                    log_level, message, details
                ) VALUES (?, ?, getutcdate(), ?, ?, ?)
            """, (execution_id, step_execution_id, level, message,
                 json.dumps(details) if details else None))
            
            print('Commit')
            conn.commit()
            
            print('Logging to application...')
            # Also log to the application logger
            log_method = getattr(logger, level.lower(), logger.info)
            log_method(f"[{execution_id}] {message}")
            
        except Exception as e:
            print(f"Error logging execution event: {str(e)}")
            conn.rollback()
            logger.error(f"Error logging execution event: {str(e)}")
        finally:
            cursor.close()
            conn.close()

    def _get_nested_value_for_variables(self, obj: Any, path: str) -> Any:
        """Get a nested value from an object using dot notation with enhanced handling
        Handles JSON strings, arrays, and complex nested structures with detailed logging
        
        Args:
            obj: The object to navigate (can be dict, list, or JSON string)
            path: Dot-notation path to the desired value (e.g., "data.customer[0].email")
            
        Returns:
            value: The value at the specified path or None if not found
            
        Examples:
            obj = {"data": {"items": [{"name": "test"}]}}
            path = "data.items[0].name"
            returns: "test"
        """
        #import re
        import json
        
        logger.debug(f"_get_nested_value_enhanced called with path: '{path}'")
        logger.debug(f"_get_nested_value_enhanced input type: {type(obj).__name__}")
        
        # Handle empty path - return the object as-is
        if not path or path == '':
            logger.debug("Empty path, returning original object")
            return obj
        
        # Step 1: Parse JSON string if needed
        if isinstance(obj, str):
            logger.debug("Input is string, attempting to parse as JSON")
            try:
                obj = json.loads(obj)
                logger.debug(f"Successfully parsed JSON string, new type: {type(obj).__name__}")
            except (json.JSONDecodeError, ValueError) as e:
                logger.debug(f"String is not valid JSON: {str(e)}, returning None")
                return None
        
        # Step 2: Split path into segments
        # Handle paths like "data.items[0].name" -> ["data", "items[0]", "name"]
        path_segments = path.split('.')
        logger.debug(f"Path split into {len(path_segments)} segments: {path_segments}")
        
        # Step 3: Navigate through each segment
        current_value = obj
        
        for i, segment in enumerate(path_segments):
            logger.debug(f"Processing segment {i+1}/{len(path_segments)}: '{segment}'")
            logger.debug(f"Current value type: {type(current_value).__name__}")
            
            # Check if current value is None
            if current_value is None:
                logger.debug(f"Current value is None at segment '{segment}', cannot continue")
                return None
            
            # Parse JSON string at any level
            if isinstance(current_value, str):
                logger.debug(f"Current value is string at segment '{segment}', attempting JSON parse")
                try:
                    current_value = json.loads(current_value)
                    logger.debug(f"Successfully parsed JSON, new type: {type(current_value).__name__}")
                except (json.JSONDecodeError, ValueError) as e:
                    logger.debug(f"String is not valid JSON: {str(e)}, returning None")
                    return None
            
            # Check if segment contains array index
            # Pattern: property_name[index] or just [index]
            array_match = re.match(r'^([^\[]*)\[(\d+)\]$', segment)
            
            if array_match:
                # Has array index: e.g., "items[0]" or "[0]"
                property_name = array_match.group(1)
                array_index = int(array_match.group(2))
                
                logger.debug(f"Segment has array notation: property='{property_name}', index={array_index}")
                
                # If there's a property name before the bracket
                if property_name:
                    # First access the property
                    if isinstance(current_value, dict):
                        if property_name in current_value:
                            current_value = current_value[property_name]
                            logger.debug(f"Accessed property '{property_name}', type: {type(current_value).__name__}")
                        else:
                            logger.debug(f"Property '{property_name}' not found in dict, keys: {list(current_value.keys())}")
                            return None
                    else:
                        logger.debug(f"Cannot access property '{property_name}' on type {type(current_value).__name__}")
                        return None
                
                # Now access the array index
                if isinstance(current_value, list):
                    if 0 <= array_index < len(current_value):
                        current_value = current_value[array_index]
                        logger.debug(f"Accessed array index [{array_index}], type: {type(current_value).__name__}")
                    else:
                        logger.debug(f"Array index {array_index} out of bounds (length: {len(current_value)})")
                        return None
                else:
                    logger.debug(f"Cannot access array index on type {type(current_value).__name__}")
                    return None
            
            else:
                # No array index, just a simple property access
                logger.debug(f"Simple property access: '{segment}'")
                
                if isinstance(current_value, dict):
                    if segment in current_value:
                        current_value = current_value[segment]
                        logger.debug(f"Accessed property '{segment}', type: {type(current_value).__name__}")
                    else:
                        logger.debug(f"Property '{segment}' not found in dict")
                        logger.debug(f"Available keys: {list(current_value.keys())}")
                        return None
                elif isinstance(current_value, list):
                    logger.debug(f"Current value is list but segment '{segment}' is not array notation")
                    return None
                else:
                    logger.debug(f"Cannot access property '{segment}' on type {type(current_value).__name__}")
                    return None
            
            # Log current state after processing segment
            if current_value is None:
                logger.debug(f"Value became None after processing segment '{segment}'")
            elif isinstance(current_value, (dict, list)):
                logger.debug(f"Current value after '{segment}': {type(current_value).__name__} with {len(current_value)} items")
            else:
                logger.debug(f"Current value after '{segment}': {type(current_value).__name__} = {str(current_value)[:100]}")
        
        # Step 4: Return final value
        logger.debug(f"Successfully navigated path '{path}', final type: {type(current_value).__name__}")
        if isinstance(current_value, str):
            logger.debug(f"Final value (string): {current_value[:200]}")
        elif isinstance(current_value, (int, float, bool)):
            logger.debug(f"Final value: {current_value}")
        elif isinstance(current_value, (dict, list)):
            logger.debug(f"Final value: {type(current_value).__name__} with {len(current_value)} items")
        
        return current_value

    def _replace_variable_references(self, text: str, variables: Dict) -> str:
        """Replace ${variable} references in text with their values
        Supports nested object access with dot notation and array indices
        
        Args:
            text: Text containing variable references
            variables: Dictionary of variables
            
        Returns:
            text: Text with variables replaced
            
        Supported syntax:
            ${varName}                    - Simple variable
            ${varName.property}           - Nested property
            ${varName.nested.deep}        - Deep nesting
            ${arrayVar[0]}                - Array index
            ${obj.array[0].property}      - Mixed notation
            ${_previousStepOutput.data.results[0].value}  - Complex paths
        """
        if not text or not isinstance(text, str):
            return text
        
        #import re
        #import json
        
        # Enhanced pattern to match ${varName.path.to.value[0].field}
        # We'll capture everything inside the braces and parse it ourselves
        pattern = r'\$\{([^}]+)\}'
        
        def replace_var(match):
            """Replace a variable reference with its value, supporting nested paths"""
            full_match = match.group(0)  # e.g., ${var.nested[0].field}
            full_path = match.group(1)   # e.g., var.nested[0].field
            
            try:
                # Split on the first dot to separate variable name from nested path
                # Use split with maxsplit=1 to only split on the FIRST dot
                if '.' in full_path:
                    parts = full_path.split('.', 1)
                    var_name = parts[0]
                    nested_path = parts[1]
                else:
                    # No nested path, just a simple variable
                    var_name = full_path
                    nested_path = None
                
                # Step 1: Get the base variable
                # Try with the find_variable helper to handle ${} wrapped keys
                found_key, value = self.find_variable(var_name, variables)
                
                # If not found with find_variable, try direct lookup
                if value is None:
                    value = variables.get(var_name)
                
                # If still not found, return original text
                if value is None:
                    logger.debug(f"Variable '{var_name}' not found, keeping original: {full_match}")
                    return full_match
                
                # Step 2: If there's a nested path, navigate the structure
                if nested_path:
                    # Use _get_nested_value to safely navigate the structure
                    nested_value = self._get_nested_value_for_variables(value, nested_path)
                    
                    if nested_value is None:
                        # Fallback: try without nested path (return base variable)
                        logger.debug(f"Nested path '{nested_path}' not found in '{var_name}', using base value")
                        # Continue with base value (don't return, process below)
                    else:
                        value = nested_value
                
                # Step 3: Convert value to string for replacement
                if value is None:
                    # If nested navigation returned None, keep original
                    logger.debug(f"Nested value is None, keeping original: {full_match}")
                    return full_match
                    
                if isinstance(value, str):
                    return value
                elif isinstance(value, (dict, list)):
                    return json.dumps(value)
                elif isinstance(value, bool):
                    return str(value).lower()  # true/false instead of True/False
                else:
                    return str(value)
                    
            except Exception as e:
                # Fallback: if anything goes wrong, keep the original text
                logger.warning(f"Error replacing variable '{full_match}': {str(e)}, keeping original")
                return full_match
        
        # Replace ${varName.path} pattern with nested support
        result = re.sub(pattern, replace_var, text)
        
        # Also support legacy $varName pattern (without braces) for backward compatibility
        # This only supports simple variable names, not nested paths
        legacy_pattern = r'\$([a-zA-Z_][a-zA-Z0-9_]*)\b(?!\{)'
        
        def replace_legacy_var(match):
            """Replace legacy $varName syntax (simple names only)"""
            var_name = match.group(1)
            try:
                found_key, value = self.find_variable(var_name, variables)
                if value is None:
                    value = variables.get(var_name)
                if value is None:
                    return match.group(0)
                return str(value)
            except Exception as e:
                logger.warning(f"Error replacing legacy variable '${var_name}': {str(e)}")
                return match.group(0)
        
        result = re.sub(legacy_pattern, replace_legacy_var, result)
        
        return result


    def _extract_variable_name(self, text: str) -> str:
        """Normalize a variable reference to just its name.

        Examples:
        - "${myVar}" -> "myVar"
        - "$myVar" -> "myVar"
        - "myVar" -> "myVar"
        """
        if not isinstance(text, str):
            return text
        #import re
        match = re.match(r'^\$\{([a-zA-Z][a-zA-Z0-9_]*)\}$', text)
        if match:
            return match.group(1)
        match2 = re.match(r'^\$([a-zA-Z][a-zA-Z0-9_]*)$', text)
        if match2:
            return match2.group(1)
        return text

    def _to_dict(value):
        if isinstance(value, dict):
            return value
        elif isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return {"data": value}  # not JSON, just raw string
        elif hasattr(value, "__dict__"):  # custom object
            return vars(value)
        else:
            return {"data": value}
        

    def _check_data_dict(self, result):
        try:
            if 'data' not in result:
                wrapped_dict = {
                    'status': result['status'],
                    'data': {
                        'columns': result['columns'],
                        'rows': result['rows']
                    }
                }
                return wrapped_dict
            return result
        except:
            return result

        
    def _execute_database_node(self, execution_id: str, node: Dict, variables: Dict) -> Dict:
        """Execute a Database node in the workflow
        
        Args:
            execution_id: Unique ID of the workflow execution
            node: Node definition
            variables: Current workflow variables
            
        Returns:
            result: Result of the node execution
        """
        node_id = node['id']
        node_config = node.get('config', {})
        
        # Log the start of database node execution
        self.log_execution(
            execution_id, node_id, "info", 
            f"Executing database node with operation: {node_config.get('dbOperation', 'unknown')}")
        
        try:
            # Get the connection string
            connection_id = node_config.get('connection')
            if not connection_id:
                raise ValueError("Database connection is required")
            
            # Determine the operation type and execute
            db_operation = node_config.get('dbOperation', 'query')
            result = None
            
            if db_operation == 'query':
                # Execute raw SQL query
                print('Executing database operation:', db_operation)
                query = self._replace_variable_references(node_config.get('query', ''), variables)
                if not query:
                    raise ValueError("SQL query is required")
                    
                result = self._execute_database_query(connection_id, query)
                result = self._check_data_dict(result)
                
            elif db_operation == 'procedure':
                # Execute stored procedure
                print('Executing database operation:', db_operation)
                procedure = self._replace_variable_references(node_config.get('procedure', ''), variables)
                if not procedure:
                    raise ValueError("Stored procedure name is required")
                    
                # Parse parameters with variable replacements
                param_json = node_config.get('parameters', '[]')
                param_json = self._replace_variable_references(param_json, variables)
                
                try:
                    parameters = json.loads(param_json)
                    result = self._execute_stored_procedure(connection_id, procedure, parameters)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Error parsing procedure parameters: {str(e)}")
                    
            elif db_operation == 'select':
                # Build and execute SELECT query
                print('Executing database operation:', db_operation)
                columns = self._replace_variable_references(node_config.get('columns', '*'), variables)
                table = self._replace_variable_references(node_config.get('tableName', ''), variables)
                where_clause = self._replace_variable_references(node_config.get('whereClause', ''), variables)
                
                if not table:
                    raise ValueError("Table name is required for SELECT operation")
                    
                query = f"SELECT {columns} FROM {table}"
                if where_clause:
                    query += f" WHERE {where_clause}"
                    
                result = self._execute_database_query(connection_id, query)
                result = self._check_data_dict(result)
                
            elif db_operation == 'insert':
                # Execute INSERT operation
                print('Executing database operation:', db_operation)
                table = self._replace_variable_references(node_config.get('tableName', ''), variables)
                if not table:
                    raise ValueError("Table name is required for INSERT operation")
                    
                # Get data to insert based on data source
                insert_data = self._get_data_from_source(node_config, variables)
                
                # Validate data
                if not insert_data or not isinstance(insert_data, dict):
                    raise ValueError("Invalid data for INSERT operation")
                    
                # Build INSERT query
                columns = list(insert_data.keys())
                if not columns:
                    raise ValueError("No data columns provided for INSERT operation")
                    
                # Format values for SQL
                values = []
                for val in insert_data.values():
                    if val is None:
                        values.append("NULL")
                    elif isinstance(val, bool):
                        values.append("1" if val else "0")
                    elif isinstance(val, str):
                        escaped_val = val.replace("'", "''")
                        values.append(f"'{escaped_val}'")
                    elif isinstance(val, (dict, list)):
                        json_val = json.dumps(val).replace("'", "''")
                        values.append(f"'{json_val}'")
                    elif isinstance(val, (int, float)):
                        values.append(str(val))
                    else:
                        # Handle datetime and other types
                        escaped_val = str(val).replace("'", "''")
                        values.append(f"'{escaped_val}'")
                        
                query = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(values)})"
                result = self._execute_database_query(connection_id, query)
                
            elif db_operation == 'update':
                # Execute UPDATE operation
                print('Executing database operation:', db_operation)
                table = self._replace_variable_references(node_config.get('tableName', ''), variables)
                where_clause = self._replace_variable_references(node_config.get('whereClause', ''), variables)
                
                if not table:
                    raise ValueError("Table name is required for UPDATE operation")
                    
                if not where_clause:
                    raise ValueError("WHERE clause is required for UPDATE operation")
                    
                # Get data to update based on data source
                update_data = self._get_data_from_source(node_config, variables)
                
                # Validate data
                if not update_data or not isinstance(update_data, dict):
                    raise ValueError("Invalid data for UPDATE operation")
                    
                # Build SET clause
                set_clauses = []
                for key, value in update_data.items():
                    if value is None:
                        set_clauses.append(f"{key} = NULL")
                    elif isinstance(value, bool):
                        set_clauses.append(f"{key} = {1 if value else 0}")
                    elif isinstance(value, str):
                        escaped_value = value.replace("'", "''")
                        set_clauses.append(f"{key} = '{escaped_value}'")
                    elif isinstance(value, (dict, list)):
                        json_value = json.dumps(value).replace("'", "''")
                        set_clauses.append(f"{key} = '{json_value}'")
                    elif isinstance(value, (int, float)):
                        set_clauses.append(f"{key} = {value}")
                    else:
                        # Handle datetime and other types
                        escaped_value = str(value).replace("'", "''")
                        set_clauses.append(f"{key} = '{escaped_value}'")
                        
                if not set_clauses:
                    raise ValueError("No data columns provided for UPDATE operation")
                    
                query = f"UPDATE {table} SET {', '.join(set_clauses)} WHERE {where_clause}"
                result = self._execute_database_query(connection_id, query)
                
            elif db_operation == 'delete':
                # Execute DELETE operation
                print('Executing database operation:', db_operation)
                table = self._replace_variable_references(node_config.get('tableName', ''), variables)
                where_clause = self._replace_variable_references(node_config.get('whereClause', ''), variables)
                
                if not table:
                    raise ValueError("Table name is required for DELETE operation")
                    
                if not where_clause:
                    raise ValueError("WHERE clause is required for DELETE operation")
                    
                query = f"DELETE FROM {table} WHERE {where_clause}"
                result = self._execute_database_query(connection_id, query)
                
            else:
                raise ValueError(f"Unknown database operation: {db_operation}")
                
            # Process result
            print('Processing result...')
            self.log_execution(
                        execution_id, node_id, "info",
                        f"Database result: {result}")
            if result and result.get('status') == 'success':
                # Store result in variable if configured
                # Default saveToVariable to true when outputVariable is present (backwards-compatible fix)
                db_has_output_var = bool(node_config.get('outputVariable'))
                db_save_to_var = node_config.get('saveToVariable', True if db_has_output_var else False)
                if db_save_to_var and node_config.get('outputVariable'):
                    var_name = self._extract_variable_name(node_config.get('outputVariable'))
                    var_value = result.get('response', result.get('data', result))

                    self.log_execution(
                        execution_id, node_id, "info",
                        f"var_name: {var_name}")
                    
                    self.log_execution(
                        execution_id, node_id, "info",
                        f"var_value: {var_value}")
                    
                    # Update variable in database
                    self._update_workflow_variable(
                        execution_id, var_name, 'object', var_value)
                        
                    # Update in-memory variables
                    variables[var_name] = var_value

                    self.log_execution(
                        execution_id, node_id, "info",
                        f"variables: {variables}")
                    
                    self.log_execution(
                        execution_id, node_id, "info",
                        f"Stored database result in variable: {var_name}")
                        
                # Return success result
                return {
                    'success': True,
                    'data': {
                        'operation': db_operation,
                        'result': result.get('response', result.get('data', {})),
                        'rowsAffected': result.get('affected_rows', 0)
                    }
                }
            else:
                raise ValueError(result.get('error', 'Unknown database error'))
                
        except Exception as e:
            error_message = str(e)
            self.log_execution(
                execution_id, node_id, "error",
                f"Database operation error: {error_message}")
                
            # Check if we should continue on error
            if node_config.get('continueOnError', False):
                self.log_execution(
                    execution_id, node_id, "warning",
                    "Continuing workflow despite database error")
                    
                # Set output variable to error result
                # Default saveToVariable to true when outputVariable is present (backwards-compatible fix)
                db_err_has_output_var = bool(node_config.get('outputVariable'))
                db_err_save_to_var = node_config.get('saveToVariable', True if db_err_has_output_var else False)
                if db_err_save_to_var and node_config.get('outputVariable'):
                    var_name = self._extract_variable_name(node_config.get('outputVariable'))
                    var_value = {'error': error_message}

                    # Update variable in database
                    self._update_workflow_variable(
                        execution_id, var_name, 'object', var_value)
                        
                    # Update in-memory variables
                    variables[var_name] = var_value
                    
                    self.log_execution(
                        execution_id, node_id, "info",
                        f"Set variable {var_name} to error result")
                        
                return {
                    'success': True,  # Return success so workflow continues
                    'data': {
                        'operation': node_config.get('dbOperation', 'unknown'),
                        'error': error_message,
                        'success': False
                    }
                }
            
            return {
                'success': False,
                'error': error_message,
                'data': {
                    'operation': node_config.get('dbOperation', 'unknown')
                }
            }

    def _get_data_from_source(self, node_config: Dict, variables: Dict) -> Dict:
        """Get data from the specified source in node configuration
        
        Args:
            node_config: Node configuration
            variables: Current workflow variables
            
        Returns:
            data: The data object
        """
        data_source = node_config.get('dataSource', 'direct')
        
        if data_source == 'direct':
            # Get data directly from the configuration
            data_str = self._replace_variable_references(node_config.get('data', '{}'), variables)
            try:
                return json.loads(data_str)
            except json.JSONDecodeError as e:
                raise ValueError(f"Error parsing data JSON: {str(e)}")
                
        elif data_source == 'variable':
            # Get data from a workflow variable
            var_name = node_config.get('dataVariable', '')
            var_name, _ = self.find_variable(var_name, variables)
            if not var_name:
                raise ValueError(f"Variable {var_name} not found")
                
            data = variables[var_name]
            if not isinstance(data, dict):
                raise ValueError(f"Variable {var_name} is not an object")
                
            return data
            
        elif data_source == 'previous':
            # Get data from the previous step's output
            path = node_config.get('dataPath', '')
            # This assumes there's a prev_data parameter available
            # You'll need to modify _execute_node to pass this information
            prev_data = variables.get('_previousStepOutput', {})
            
            data = self._get_nested_value(prev_data, path)
            if data is None:
                raise ValueError(f"Path '{path}' not found in previous step output")
                
            if not isinstance(data, dict):
                raise ValueError(f"Data at path '{path}' is not an object")
                
            return data
            
        else:
            raise ValueError(f"Unknown data source: {data_source}")

    def _execute_database_query(self, connection_id: str, query: str) -> Dict:
        """Execute a SQL query using the specified connection
        
        Args:
            connection_id: Database connection identifier
            query: SQL query to execute
            
        Returns:
            result: Query result
        """
        try:
            print('Executing query:', query, connection_id)
            conn_str,_,_ = get_database_connection_string(connection_id)
            print('Conn Str:', conn_str)
            # Use the execute_sql_query function from DataUtils
            return execute_sql_query(conn_str, query)
        except Exception as e:
            raise ValueError(f"Error executing SQL query: {str(e)}")

    def _execute_stored_procedure(self, connection_id: str, procedure: str, parameters: List) -> Dict:
        """Execute a stored procedure with parameters
        
        Args:
            connection_id: Database connection identifier
            procedure: Name of the stored procedure
            parameters: List of parameter objects
            
        Returns:
            result: Procedure execution result
        """
        # Get the connection string for the specified connection
        conn_str, _, _ = get_database_connection_string(connection_id)
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()
        
        try:
            # Prepare parameter string for the SQL call
            param_placeholders = ', '.join(['?' for _ in parameters])
            param_values = [param['value'] for param in parameters]
            
            # Execute the stored procedure
            sql = f"EXEC {procedure} {param_placeholders}"
            cursor.execute(sql, param_values)
            
            # Helper to convert non-serializable types
            def make_serializable(val):
                if val is None:
                    return None
                elif isinstance(val, (datetime.date, datetime.datetime)):
                    return val.isoformat()
                elif isinstance(val, decimal.Decimal):
                    return float(val)
                elif isinstance(val, bytes):
                    return val.decode('utf-8', errors='replace')
                else:
                    return val
            
            # Fetch all results
            columns = [column[0] for column in cursor.description] if cursor.description else []
            results = []
            
            for row in cursor.fetchall():
                results.append({col: make_serializable(val) for col, val in zip(columns, row)})
                
            # Check for additional result sets
            while cursor.nextset():
                if cursor.description:
                    additional_columns = [column[0] for column in cursor.description]
                    for row in cursor.fetchall():
                        results.append({col: make_serializable(val) for col, val in zip(additional_columns, row)})
            
            return {
                'status': 'success',
                'response': results,
                'affected_rows': cursor.rowcount if cursor.rowcount > 0 else 0
            }
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e)
            }
        finally:
            cursor.close()
            conn.close()

    def _get_nested_value(self, obj: Dict, path: str) -> Any:
        """Get a nested value from an object using dot notation
        
        Args:
            obj: The object to navigate
            path: Dot-notation path to the desired value
            
        Returns:
            value: The value at the specified path or None if not found
        """
        if not path:
            return obj
            
        #import re
        keys = path.split('.')
        result = obj
        
        for key in keys:
            if result is None or not isinstance(result, (dict, list)):
                return None
                
            # Handle array indices in the path (e.g., "data.results[0].value")
            match = re.match(r'^([^\[]+)(?:\[(\d+)\])?$', key)
            if match:
                prop_name, array_index = match.groups()
                
                if prop_name not in result:
                    return None
                    
                result = result[prop_name]
                
                if array_index is not None and isinstance(result, list):
                    array_index = int(array_index)
                    if array_index < len(result):
                        result = result[array_index]
                    else:
                        return None
            else:
                if key not in result:
                    return None
                result = result[key]
        
        return result

    def _update_workflow_variable(self, execution_id: str, variable_name: str, 
                                variable_type: str, variable_value: Any):
        """Update a workflow variable in the database
        
        Args:
            execution_id: Unique ID of the workflow execution
            variable_name: Name of the variable
            variable_type: Type of the variable
            variable_value: Value of the variable
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        try:
            # Remove the ${ and } from the variable name (for output variables)
            variable_name = variable_name.replace("${", "").replace("}", "")

            print('UPDATING VARIABLE:', variable_name)
            
            # First, get the tenant context
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            # Check if the variable exists
            cursor.execute("""
                SELECT COUNT(*)
                FROM WorkflowVariables
                WHERE execution_id = ? AND variable_name = ?
            """, execution_id, variable_name)
            
            count = cursor.fetchone()[0]
            
            # Convert value to JSON string
            value_json = json.dumps(variable_value)

            print('TO VALUE:', value_json)
            
            if count > 0:
                # Update existing variable
                cursor.execute("""
                    UPDATE WorkflowVariables
                    SET variable_type = ?, variable_value = ?, last_updated = getutcdate()
                    WHERE execution_id = ? AND variable_name = ?
                """, variable_type, value_json, execution_id, variable_name)
            else:
                # Insert new variable
                cursor.execute("""
                    INSERT INTO WorkflowVariables (
                        execution_id, variable_name, variable_type, 
                        variable_value, last_updated
                    ) VALUES (?, ?, ?, ?, getutcdate())
                """, execution_id, variable_name, variable_type, value_json)
            
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            conn.close()


    def _execute_folder_selector_node(self, execution_id: str, node: Dict, variables: Dict) -> Dict:
        """Execute a Folder Selector node in the workflow
        
        Args:
            execution_id: Unique ID of the workflow execution
            node: Node definition
            variables: Current workflow variables
            
        Returns:
            result: Result of the node execution
        """
        node_id = node['id']
        node_config = node.get('config', {})
        
        # Log the start of folder selector node execution
        self.log_execution(
            execution_id, node_id, "info", 
            f"Executing folder selector node with mode: {node_config.get('selectionMode', 'first')}")
        
        try:
            # Get the folder path with variable replacements
            folder_path = self._replace_variable_references(
                node_config.get('folderPath', ''), variables)
            
            if not folder_path:
                raise ValueError("Folder path is empty or undefined")
            
            # Get selection mode
            selection_mode = node_config.get('selectionMode', 'first')
            
            # Get and process file pattern
            file_pattern = self._replace_variable_references(
                node_config.get('filePattern', '*.*'), variables)
            
            # List files in the folder
            file_list = self._list_files_in_folder(folder_path, file_pattern, selection_mode)
            
            # Select the appropriate file based on selection_mode
            selected_file = self._select_file(file_list, selection_mode)
            
            # Handle case where no files are found
            if not selected_file:
                if node_config.get('failIfEmpty', True):
                    # Fail if configured to do so
                    raise ValueError(f"No files found in folder: {folder_path}")
                else:
                    # Set output variable to empty string if specified
                    if node_config.get('outputVariable'):
                        output_var = self._extract_variable_name(node_config.get('outputVariable'))
                        # Update variable in database
                        self._update_workflow_variable(
                            execution_id, output_var, 'string', '')
                        # Update in-memory variables
                        variables[output_var] = ''
                        
                        self.log_execution(
                            execution_id, node_id, "info",
                            f"No files found. Set variable {output_var} to empty string")
                    
                    # Return success with empty result
                    return {
                        'success': True,
                        'data': {
                            'folderPath': folder_path,
                            'filesFound': False,
                            'selectedFile': None
                        }
                    }
            
            # Set output variable if specified
            print('--------------------------------')
            print('Setting output variable (before)...')
            print('Node config:', node_config)
            print('Output variable:', node_config.get('outputVariable'))
            print('Selected file:', selected_file)
            print('Variables:', variables)
            
            if node_config.get('outputVariable'):
                output_var = self._extract_variable_name(node_config.get('outputVariable'))
                if selection_mode == 'all':
                    # Update variable in database (json.dumps handles list serialization)
                    self._update_workflow_variable(
                        execution_id, output_var, 'array', file_list)
                    # Keep as list in-memory for direct consumption by loop nodes
                    variables[output_var] = file_list

                    self.log_execution(
                        execution_id, node_id, "info",
                        f"Set variable {output_var} to list with {len(file_list)} items")
                else:
                    # Update variable in database
                    self._update_workflow_variable(
                        execution_id, output_var, 'string', selected_file)
                    # Update in-memory variables
                    variables[output_var] = selected_file
                
                    self.log_execution(
                        execution_id, node_id, "info",
                        f"Set variable {output_var} to: {selected_file}")
                
            print('Output variable set (after)...')
            print('Variables:', variables)
            print('--------------------------------')
            
            # Return success with the selected file
            return {
                'success': True,
                'data': {
                    'folderPath': folder_path,
                    'filesFound': True,
                    'selectedFile': selected_file,
                    'allFiles': file_list
                }
            }
        
        except Exception as e:
            error_message = str(e)
            self.log_execution(
                execution_id, node_id, "error",
                f"Folder selector error: {error_message}")
            
            return {
                'success': False,
                'error': error_message,
                'data': {
                    'folderPath': node_config.get('folderPath', '')
                }
            }

    def _list_files_in_folder(self, folder_path: str, file_pattern: str, selection_mode: str) -> List[str]:
        """List files in the specified folder matching the pattern
        
        Args:
            folder_path: Path to the folder
            file_pattern: File pattern to match (e.g., *.pdf)
            selection_mode: Selection mode (first, latest, pattern, etc.)
            
        Returns:
            file_list: List of file paths matching the criteria
        """
        # Ensure the folder path exists
        if not os.path.exists(folder_path):
            raise ValueError(f"Folder does not exist: {folder_path}")
        
        if not os.path.isdir(folder_path):
            raise ValueError(f"Path is not a directory: {folder_path}")
        
        # Support pipe-delimited or comma-delimited patterns
        if '|' in file_pattern or ',' in file_pattern:
            # Determine delimiter
            delimiter = '|' if '|' in file_pattern else ','
            patterns = [p.strip() for p in file_pattern.split(delimiter)]
            files = []
            for pattern in patterns:
                glob_pattern = os.path.join(folder_path, pattern)
                files.extend(glob.glob(glob_pattern))
            # Remove duplicates while preserving order
            files = list(dict.fromkeys(files))
        else:
            # Single pattern
            glob_pattern = os.path.join(folder_path, file_pattern)
            files = glob.glob(glob_pattern)
        
        # Return only file paths (no directories)
        return [f for f in files if os.path.isfile(f)]

    def _select_file(self, file_list: List[str], selection_mode: str) -> Optional[str]:
        """Select a file from the list based on the selection mode
        
        Args:
            file_list: List of file paths
            selection_mode: Selection mode (first, latest, pattern, etc.)
            
        Returns:
            selected_file: The selected file path or None if no files found
        """
        if not file_list:
            return None
        
        if selection_mode == 'first' or selection_mode == 'all':
            # Return the first file in the list
            return file_list[0]
        
        elif selection_mode == 'latest':
            # Return the most recently modified file
            return max(file_list, key=os.path.getmtime)
        
        elif selection_mode == 'pattern':
            # This is already handled by the glob pattern in _list_files_in_folder
            # Just return the first matching file
            return file_list[0]
        
        elif selection_mode == 'largest':
            # Return the largest file by size
            return max(file_list, key=os.path.getsize)
        
        elif selection_mode == 'smallest':
            # Return the smallest file by size
            return min(file_list, key=os.path.getsize)
        
        elif selection_mode == 'random':
            # Return a random file from the list
            return random.choice(file_list)
        
        # Default to first file
        return file_list[0] if file_list else None
    
    ###############################
    ########## Documents ##########
    ###############################
    def _get_local_ip(self):
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip

    def _get_document_api_url(self, path: str = "") -> str:
        """Get the document API URL endpoint
        
        Args:
            path: Optional path to append to the base URL
            
        Returns:
            url: The complete document API URL
        """
        # Get the protocol, host and port to call the config endpoint
        # protocol = os.getenv('PROTOCOL', 'http')
        # host = os.getenv('HOST', 'localhost')
        # if host == "0.0.0.0":
        #     host = self._get_local_ip()
        # current_port = int(os.getenv('HOST_PORT', '3001')) + 10
        
        # # Get the base URL from the config
        # base_url = f"{protocol}://{host}:{current_port}"
        base_url = get_document_api_base_url()
        
        # Append path if provided
        if path:
            # Make sure path starts with a slash
            if not path.startswith('/'):
                path = f"/{path}"
            return f"{base_url}{path}"
        
        return base_url
    
    def _get_app_api_url(self, path: str = "") -> str:
        """Get the document API URL endpoint
        
        Args:
            path: Optional path to append to the base URL
            
        Returns:
            url: The complete document API URL
        """
        # Get the protocol, host and port to call the config endpoint
        protocol = os.getenv('PROTOCOL', 'http')
        host = os.getenv('HOST', 'localhost')
        if host == "0.0.0.0":
            host = self._get_local_ip()
        current_port = int(os.getenv('HOST_PORT', '3001'))
        
        # Get the base URL from the config
        base_url = f"{protocol}://{host}:{current_port}"
        
        # Append path if provided
        if path:
            # Make sure path starts with a slash
            if not path.startswith('/'):
                path = f"/{path}"
            return f"{base_url}{path}"
        
        return base_url
    
    def _execute_document_node(self, execution_id: str, node: Dict, variables: Dict) -> Dict:
        """Execute a Document node in the workflow
        
        Args:
            execution_id: Unique ID of the workflow execution
            node: Node definition
            variables: Current workflow variables
            
        Returns:
            result: Result of the node execution
        """
        node_id = node['id']
        node_config = node.get('config', {})

        print(f"Node: {node_id}")
        print(f"Node config: {node_config}")
        print(f"Variables: {variables}")
        
        # Log the start of document node execution
        self.log_execution(
            execution_id, node_id, "info", 
            f"Executing document node with action: {node_config.get('documentAction', 'process')}")
        
        try:
            # Get the document action
            document_action = node_config.get('documentAction', 'process')
            
            # Execute the appropriate document action
            if document_action == 'process':
                result = self._process_document(execution_id, node_id, node_config, variables)
            elif document_action == 'extract':
                result = self._extract_document(execution_id, node_id, node_config, variables)
            elif document_action == 'analyze':
                result = self._analyze_document(execution_id, node_id, node_config, variables)
            elif document_action == 'get':
                result = self._get_document(execution_id, node_id, node_config, variables)
            elif document_action == 'save':
                result = self._save_document(execution_id, node_id, node_config, variables)
            else:
                raise ValueError(f"Unknown document action: {document_action}")
            
            print(f'Document Node Execution Result: {result}')
            self.log_execution(execution_id, node_id, "info", f"Document Node Execution Result: {result}")

            # Ensure data is a dict
            if isinstance(result.get("data"), list):
                data = result["data"]
                if data and isinstance(data[0], dict):
                    result["data"] = data[0]
                    self.log_execution(execution_id, node_id, "warning", f"Detected and successfully corrected invalid result format in data key (list instead of dict)")
                else:
                    result["data"] = {}
            elif not isinstance(result.get("data"), dict):
                self.log_execution(execution_id, node_id, "warning", f"Invalid format detected in result data key")
                result["data"] = {}

            # Process result based on output type
            if result.get('success', False):
                output_type = node_config.get('outputType', 'variable')
                output_path = node_config.get('outputPath', '')

                print(86 * '-')
                print(86 * '-')
                print('VARIABLES:')
                print('output_type', output_type)
                print('output_path', output_path)
                print('variables', variables)
                
                # Replace variable references in output path (Only we're saving to a file b/c the file name could be a variable reference)
                # NOTE: We don't want to run this for variables b/c the results are OUTPUT to variables NOT INPUT to variables
                if output_type == 'file':
                    output_path = self._replace_variable_references(output_path, variables)

                print('VARIABLES (AFTER REFERENCE UPDATE):')
                print('output_type', output_type)
                print('output_path', output_path)
                print('variables', variables)
                
                # Handle output based on type
                if output_type == 'variable' and output_path:
                    document_text = result.get('data', {}).get("document_text", "")
                    if not document_text:
                        document_text = result.get('data', {})
                    # Store in workflow variable
                    self._update_workflow_variable(
                        execution_id, output_path, 'object', document_text)
                    
                    # Update in-memory variables
                    output_path = output_path.replace("${", "").replace("}", "")
                    variables[output_path] = document_text
                    
                    self.log_execution(
                        execution_id, node_id, "info",
                        f"Stored document result in variable: {output_path}")
                        
                elif output_type == 'file' and output_path and document_action != 'save':
                    # Save to file
                    if not output_path:
                        self.log_execution(
                            execution_id, node_id, "warning",
                            "Output path is empty after variable substitution")
                        
                        # Add error to result but don't fail the operation
                        result['fileSaved'] = False
                        result['fileError'] = "Output path is empty after variable substitution"
                    else:
                        try:
                            # Prepare content for saving
                            content_to_save = None
                            data = result.get('data', {})
                            
                            if isinstance(data, str):
                                content_to_save = data
                            elif isinstance(data, dict) and 'content' in data:
                                content_to_save = data['content']
                            else:
                                #import json
                                content_to_save = json.dumps(data, indent=2)
                            
                            # Save the content to file
                            self._save_file_content(output_path, content_to_save)
                            
                            # Add save result to the original result
                            result['fileSaved'] = True
                            result['filePath'] = output_path
                            
                            self.log_execution(
                                execution_id, node_id, "info",
                                f"Saved document output to file: {output_path}")
                        except Exception as save_error:
                            # Don't fail the whole operation if just the save fails
                            result['fileSaved'] = False
                            result['fileError'] = str(save_error)
                            
                            self.log_execution(
                                execution_id, node_id, "error",
                                f"Error saving to file: {str(save_error)}")
                            
                print('VARIABLES (AFTER ALL UPDATES):')
                print('output_type', output_type)
                print('output_path', output_path)
                print('variables', variables)
                print("result.get('data')", result.get('data', {}))
                
                print(86 * '-')
                print(86 * '-')
                
                # For "return" output type, the data is already ready to be passed to the next node
                
            return {
                'success': result.get('success', False),
                'data': result.get('data', {}),
                'error': result.get('error', None)
            }
            
        except Exception as e:
            error_message = str(e)
            self.log_execution(
                execution_id, node_id, "error",
                f"Document node error: {error_message}")
            
            return {
                'success': False,
                'error': error_message,
                'data': {
                    'action': node_config.get('documentAction', 'process')
                }
            }

    def _get_document_source(self, execution_id: str, node_id: str, config: Dict, variables: Dict) -> Dict:
        """Get document source based on configuration
        
        Args:
            execution_id: Unique ID of the workflow execution
            node_id: ID of the node
            config: Node configuration
            variables: Current workflow variables
            
        Returns:
            source_info: Information about the document source
        """
        try:
            source_type = config.get('sourceType', 'file')
            
            if source_type == 'file':
                # Get file path (with variable replacement)
                file_path = self._replace_variable_references(config.get('sourcePath', ''), variables)
                
                if not file_path:
                    return {'success': False, 'error': 'File path is required', 'data': None}
                
                return {'success': True, 'type': 'path', 'data': file_path}
                
            elif source_type == 'variable':
                # Get from workflow variable
                var_name = config.get('sourcePath', '')
                var_name, _ = self.find_variable(var_name, variables)
                if not var_name:
                    return {
                        'success': False,
                        'error': f'Variable "{var_name}" not found',
                        'data': None
                    }
                
                var_value = variables[var_name]
                
                # Check if it's a file object or a path string
                if isinstance(var_value, dict) and var_value.get('type') == 'file':
                    return {'success': True, 'type': 'file', 'data': var_value.get('data')}
                elif isinstance(var_value, str):
                    return {'success': True, 'type': 'path', 'data': var_value}
                else:
                    return {
                        'success': False,
                        'error': f'Variable "{var_name}" does not contain a valid file or path',
                        'data': None
                    }
                    
            elif source_type == 'previous':
                # Get from previous step output
                prev_data = variables.get('_previousStepOutput', {})
                
                if 'filePath' in prev_data:
                    return {'success': True, 'type': 'path', 'data': prev_data['filePath']}
                elif 'file' in prev_data:
                    return {'success': True, 'type': 'file', 'data': prev_data['file']}
                else:
                    return {
                        'success': False,
                        'error': 'Previous step output does not contain a valid file or path',
                        'data': None
                    }
                    
            else:
                return {
                    'success': False,
                    'error': f'Unknown source type: {source_type}',
                    'data': None
                }
                
        except Exception as e:
            self.log_execution(
                execution_id, node_id, "error",
                f"Error getting document source: {str(e)}")
                
            return {'success': False, 'error': str(e), 'data': None}

    def _save_file_content(self, file_path: str, content: str) -> bool:
        """Save content to a file
        
        Args:
            file_path: Path to save the file
            content: Content to save
            
        Returns:
            success: True if the file was saved successfully
        """
        import os
        
        try:
            # Create directory if it doesn't exist
            directory = os.path.dirname(file_path)
            if directory and not os.path.exists(directory):
                os.makedirs(directory)
            
            # Write content to file
            with open(file_path, 'w', encoding='utf-8') as file:
                file.write(content)
                
            return True
        except Exception as e:
            print(f"Error saving file: {str(e)}")
            raise

    def _process_document(self, execution_id: str, node_id: str, config: Dict, variables: Dict) -> Dict:
        """Process a document
        
        Args:
            execution_id: Unique ID of the workflow execution
            node_id: ID of the node
            config: Node configuration
            variables: Current workflow variables
            
        Returns:
            result: Result of the document processing
        """
        try:
            # Get document source
            source_result = self._get_document_source(execution_id, node_id, config, variables)
            if not source_result.get('success', False):
                return source_result
            
            # Prepare parameters for document processing
            #import requests
            import os
            
            document_type = config.get('documentType', 'auto')
            force_ai_extraction = True
            use_batch_processing = str(config.get('useBatchProcessing', 'true')).lower() == 'true'
            batch_size = config.get('batchSize', 3)
            page_range = config.get('pageRange', '')
            extract_fields = config.get('extract_fields', WORKFLOW_DOC_EXTRACT_FIELDS_DEFAULT)
            detect_document_type = config.get('detect_document_type', WORKFLOW_DOC_DETECT_TYPE_DEFAULT)

            # Handle do_not_store with documentSharing fallback
            if 'do_not_store' in config:
                do_not_store = str(config.get('do_not_store', str(WORKFLOW_DOC_DO_NOT_SAVE_DEFAULT))).lower() == 'true'
            else:
                document_sharing = config.get('documentSharing', 'private')
                do_not_store = (document_sharing == 'private')
            
            # Prepare form data
            data = {
                'document_type': document_type,
                'force_ai_extraction': 'true' if force_ai_extraction else 'false',
                'use_batch_processing': 'true' if use_batch_processing else 'false',
                'batch_size': str(batch_size),
                'do_not_store': 'true' if do_not_store else 'false',
                'extract_fields': 'true' if extract_fields else 'false',
                'detect_document_type': 'true' if detect_document_type else 'false'
            }

            self.log_execution(
                execution_id, node_id, "debug",
                f"Process document form data: {data}")
            
            if page_range:
                data['page_range'] = page_range
            
            # Handle file or file path
            source_type = source_result.get('type')
            source_data = source_result.get('data')
            
            files = None
            if source_type == 'path':
                data['filePath'] = source_data
            elif source_type == 'file':
                # If the source is a file object, add it to the files parameter
                files = {'file': open(source_data, 'rb')}
            
            # Get document processing API endpoint
            endpoint = self._get_document_api_url("/document/process")
            
            # Call API
            response = requests.post(
                endpoint,
                data=data,
                files=files
            )
            
            if response.status_code != 200:
                error_data = response.json()
                raise ValueError(error_data.get('message', 'Document processing failed'))
            
            result = response.json()

            print(86 * '-')
            # print('RAW JSON RESULT FROM DOCUMENT PROCESSING WORKFLOW')
            # print(result)
            print(86 * '-')

            return {
                'success': result.get('status') == 'success',
                'data': result,
                'error': result.get('message') if result.get('status') == 'error' else None
            }
        
        except Exception as e:
            self.log_execution(
                execution_id, node_id, "error",
                f"Error processing document: {str(e)}")
                
            return {'success': False, 'error': str(e), 'data': {}}

    def _extract_document(self, execution_id: str, node_id: str, config: Dict, variables: Dict) -> Dict:
        """Extract data from a document
        
        Args:
            execution_id: Unique ID of the workflow execution
            node_id: ID of the node
            config: Node configuration
            variables: Current workflow variables
            
        Returns:
            result: Result of the document extraction
        """
        try:
            # Get document source
            source_result = self._get_document_source(execution_id, node_id, config, variables)
            if not source_result.get('success', False):
                return source_result
            
            # Prepare parameters for document extraction
            #import requests
            import os

            self.log_execution(
                execution_id, node_id, "debug",
                f"Extract document config data: {config}")
            
            document_type = config.get('documentType', 'auto')
            force_ai_extraction = True
            use_batch_processing = str(config.get('useBatchProcessing', 'true')).lower() == 'true'
            batch_size = config.get('batchSize', 3)
            page_range = config.get('pageRange', '')

            # Handle do_not_store with documentSharing fallback
            if 'do_not_store' in config:
                do_not_store = str(config.get('do_not_store', str(WORKFLOW_DOC_DO_NOT_SAVE_DEFAULT))).lower() == 'true'
            else:
                document_sharing = config.get('documentSharing', 'private')
                do_not_store = (document_sharing == 'private')
            
            # Prepare form data
            data = {
                'document_type': document_type,
                'force_ai_extraction': 'true' if force_ai_extraction else 'false',
                'use_batch_processing': 'true' if use_batch_processing else 'false',
                'batch_size': str(batch_size),
                'extract_fields': 'true',
                'do_not_store': 'true' if do_not_store else 'false'
            }

            self.log_execution(
                execution_id, node_id, "debug",
                f"Extract document form data: {data}")
            
            if page_range:
                data['page_range'] = page_range
            
            # Handle file or file path
            source_type = source_result.get('type')
            source_data = source_result.get('data')
            
            files = None
            if source_type == 'path':
                data['filePath'] = source_data
            elif source_type == 'file':
                # If the source is a file object, add it to the files parameter
                files = {'file': open(source_data, 'rb')}
            
            # Get document API endpoint
            endpoint = self._get_document_api_url("/document/extract")
            
            # Call API
            response = requests.post(
                endpoint,
                data=data,
                files=files
            )
            
            if response.status_code != 200:
                error_data = response.json()
                raise ValueError(error_data.get('message', 'Document extraction failed'))
            
            result = response.json()

            print(f'Result from API: {result}')
            self.log_execution(
                execution_id, node_id, "info",
                f"Result from API: {result}")
            
            # Return extracted data if successful
            if result.get('status') == 'success':
                return {
                    'success': True,
                    'data': result.get('extracted_data', {}),
                    'error': None
                }
            else:
                return {
                    'success': False,
                    'data': {},
                    'error': result.get('message', 'Document extraction failed')
                }
        
        except Exception as e:
            self.log_execution(
                execution_id, node_id, "error",
                f"Error extracting document: {str(e)}")
                
            return {'success': False, 'error': str(e), 'data': {}}

    def _analyze_document(self, execution_id: str, node_id: str, config: Dict, variables: Dict) -> Dict:
        """Analyze a document with AI
        
        Args:
            execution_id: Unique ID of the workflow execution
            node_id: ID of the node
            config: Node configuration
            variables: Current workflow variables
            
        Returns:
            result: Result of the document analysis
        """
        try:
            print('Starting document analyze...')
            # Get document source
            source_result = self._get_document_source(execution_id, node_id, config, variables)
            if not source_result.get('success', False):
                return source_result
            
            print('Config:', config)
            print('Variables:', variables)
            # Get prompt with variable replacements
            prompt = self._replace_variable_references(config.get('prompt', ''), variables)
            print('prompt:', prompt)
            # Prepare form data
            data = {
                'prompt': prompt
            }
            
            # Handle file or file path
            source_type = source_result.get('type')
            source_data = source_result.get('data')
            
            files = None
            if source_type == 'path':
                data['filePath'] = source_data
            elif source_type == 'file':
                # If the source is a file object, add it to the files parameter
                files = {'file': open(source_data, 'rb')}
            
            # Get document API endpoint
            endpoint = self._get_document_api_url("/document/analyze")
            print('Calling document endpoint:', endpoint)
            # Call API
            response = requests.post(
                endpoint,
                data=data,
                files=files
            )
            print('response:', response)
            if response.status_code != 200:
                error_data = response.json()
                raise ValueError(error_data.get('message', 'Document analysis failed'))
            
            result = response.json()
            print('result:', result)
            return {
                'success': result.get('status') == 'success',
                'data': result,
                'error': result.get('message') if result.get('status') == 'error' else None
            }
        
        except Exception as e:
            self.log_execution(
                execution_id, node_id, "error",
                f"Error analyzing document: {str(e)}")
                
            return {'success': False, 'error': str(e), 'data': {}}

    def _get_document(self, execution_id: str, node_id: str, config: Dict, variables: Dict) -> Dict:
        """Get a document by ID
        
        Args:
            execution_id: Unique ID of the workflow execution
            node_id: ID of the node
            config: Node configuration
            variables: Current workflow variables
            
        Returns:
            result: Result of getting the document
        """
        try:
            # Get document ID with variable replacements
            document_id = self._replace_variable_references(config.get('documentId', ''), variables)
            
            if not document_id:
                return {'success': False, 'error': 'Document ID is required', 'data': {}}
            
            # Get format
            output_format = config.get('outputFormat', 'json').lower()
            if output_format == 'same':
                output_format = 'json'
            
            # Call API
            #import requests
            import os
            
            # Get document API endpoint
            endpoint = self._get_document_api_url(f"/document/get/{document_id}?format={output_format}")
            
            response = requests.get(endpoint)
            
            if response.status_code != 200:
                error_data = response.json()
                raise ValueError(error_data.get('message', 'Failed to retrieve document'))
            
            # Handle different response types
            if output_format == 'csv':
                csv_text = response.text
                return {
                    'success': True,
                    'data': {'content': csv_text, 'format': 'csv'},
                    'error': None
                }
            else:
                result = response.json()
                
                if result.get('status') == 'success':
                    return {
                        'success': True,
                        'data': result.get('data', result),
                        'error': None
                    }
                else:
                    return {
                        'success': False,
                        'data': {},
                        'error': result.get('message', 'Failed to retrieve document')
                    }
        
        except Exception as e:
            self.log_execution(
                execution_id, node_id, "error",
                f"Error getting document: {str(e)}")
                
            return {'success': False, 'error': str(e), 'data': {}}

    def _save_document(self, execution_id: str, node_id: str, config: Dict, variables: Dict) -> Dict:
        """Save a document
        
        Args:
            execution_id: Unique ID of the workflow execution
            node_id: ID of the node
            config: Node configuration
            variables: Current workflow variables
            
        Returns:
            result: Result of saving the document
        """
        try:
            # Get document content
            source_type = config.get('sourceType', 'previous')
            content = None
            
            if source_type == 'previous':
                # Use previous output
                prev_data = variables.get('_previousStepOutput', {})
                
                if isinstance(prev_data, str):
                    content = prev_data
                elif isinstance(prev_data, dict) and ('content' in prev_data or 'text' in prev_data):
                    content = prev_data.get('content', prev_data.get('text', ''))
                else:
                    #import json
                    content = json.dumps(prev_data)
                    
            elif source_type == 'variable':
                # Get from workflow variable
                var_name = config.get('sourcePath', '')
                var_name, _ = self.find_variable(var_name, variables)

                logger.info(f"Attempting to find variable: {var_name}")
                logger.info("*********************************************")
                logger.info(f"Checking in current variables: {variables}")
                
                if not var_name:
                    return {
                        'success': False,
                        'error': f'Variable "{var_name}" not found',
                        'data': {}
                    }
                
                var_value = variables[var_name]
                
                if isinstance(var_value, str):
                    content = var_value
                else:
                    #import json
                    content = json.dumps(var_value)
                    
            else:
                return {
                    'success': False,
                    'error': 'Source type not supported for save operation',
                    'data': {}
                }
            
            # Get output path with variable replacements
            output_path = self._replace_variable_references(config.get('outputPath', ''), variables)

            logging.info(f"Attempting to save to output path: {output_path}")
            
            if not output_path:
                return {'success': False, 'error': 'Output path is required', 'data': {}}
            
            # Call API or directly save file
            #import requests
            import os
            
            try:
                # Use the document API if available
                # Get document API endpoint
                endpoint = self._get_document_api_url("/document/save")
                
                response = requests.post(
                    endpoint,
                    json={
                        'content': content,
                        'outputPath': output_path
                    }
                )
                
                if response.status_code != 200:
                    error_data = response.json()
                    raise ValueError(error_data.get('message', 'Failed to save document'))
                
                result = response.json()
                
                return {
                    'success': result.get('status') == 'success',
                    'data': result,
                    'error': result.get('message') if result.get('status') == 'error' else None
                }
                
            except requests.exceptions.RequestException:
                # If API fails or is not available, try direct file save
                self._save_file_content(output_path, content)
                
                return {
                    'success': True,
                    'data': {
                        'status': 'success',
                        'filePath': output_path,
                        'message': 'Document saved successfully'
                    },
                    'error': None
                }
        
        except Exception as e:
            self.log_execution(
                execution_id, node_id, "error",
                f"Error saving document: {str(e)}")
                
            return {'success': False, 'error': str(e), 'data': {}}
        
    ###############################
    ########## AI Action ##########
    ###############################
    def _execute_ai_action_node(self, execution_id: str, node: Dict, variables: Dict) -> Dict:
        """Execute an AI Action node in the workflow
        
        Args:
            execution_id: Unique ID of the workflow execution
            node: Node definition
            variables: Current workflow variables
            
        Returns:
            result: Result of the node execution
        """
        print('Executing AI action...')
        node_id = node['id']
        node_config = node.get('config', {})
        
        # Log the start of AI action node execution
        self.log_execution(
            execution_id, node_id, "info", 
            "Executing AI action node")
        print('Node:', node)
        print('variables:', variables)
        try:
            # Get the agent ID and prompt from the configuration
            agent_id = node_config.get('agent_id', '')
            prompt = node_config.get('prompt', '')
            
            # Replace variable references in the prompt
            prompt = self._replace_variable_references(prompt, variables)
            print('Prompt:', prompt)
            # Replace the special {prev_output} placeholder
            prev_output = variables.get('_previousStepOutput', {})
            print('prev_output:', prev_output)
            if isinstance(prev_output, dict):
                # Convert dict to string for replacement
                #import json
                prev_output_str = json.dumps(prev_output)
            else:
                # Use the value as is if it's already a string
                prev_output_str = str(prev_output)
                
            prompt = prompt.replace('{prev_output}', prev_output_str)
            
            # Log the processed prompt
            self.log_execution(
                execution_id, node_id, "info", 
                "AI prompt after variable substitution",
                {"prompt": prompt})
            
            # Prepare the request data
            request_data = {
                'agent_id': agent_id,
                'prompt': prompt,
                'hist': '[]'  # Use empty array if no history provided
            }
            print('request data:', request_data)
            # Get the API endpoint (adjust as needed for your environment)
            api_url = f"{self._get_app_api_url('/chat/general_system')}"
            print('Executing request:', api_url)
            response = requests.post(
                api_url,
                headers={
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                    'X-API-Key': os.getenv('API_KEY', '')
                },
                json=request_data
            )
            print('response:', response)
            if response.status_code != 200:
                error_data = response.json() if response.content else {'response': f"HTTP error! status: {response.status_code}"}
                raise ValueError(error_data.get('response', f"HTTP error! status: {response.status_code}"))
            
            result = response.json()
            
            if result.get('status') == 'error':
                raise ValueError(result.get('response', 'Unknown error from AI service'))
            
            # Store result in variable if configured
            if node_config.get('outputVariable'):
                var_name = self._extract_variable_name(node_config.get('outputVariable'))
                var_value = {
                    'response': result.get('response', ''),
                    'chatHistory': result.get('chat_history', [])
                }
                
                # TODO: Need to determine if full chat history is required or just the response (currently just recent response)
                # Update variable in database
                self._update_workflow_variable(
                    execution_id, var_name, 'object', var_value.get('response', ''))
                    
                # Update in-memory variables
                variables[var_name] = var_value.get('response', '')
                
                self.log_execution(
                    execution_id, node_id, "info",
                    f"Stored AI response in variable: {var_name}")
            
            return {
                'success': True,
                'data': {
                    'response': result.get('response', ''),
                    'chatHistory': result.get('chat_history', [])
                }
            }
            
        except Exception as e:
            error_message = str(e)
            self.log_execution(
                execution_id, node_id, "error",
                f"AI action error: {error_message}")
            
            # Check if we should continue on error
            if node_config.get('continueOnError', False):
                self.log_execution(
                    execution_id, node_id, "warning",
                    "Continuing workflow despite AI action error")
                    
                # Set output variable to error result if specified
                if node_config.get('outputVariable'):
                    var_name = self._extract_variable_name(node_config.get('outputVariable'))
                    var_value = {'error': error_message}
                    
                    # Update variable in database
                    self._update_workflow_variable(
                        execution_id, var_name, 'object', var_value)
                        
                    # Update in-memory variables
                    variables[var_name] = var_value
                    
                    return {
                        'success': True,  # Return success so workflow continues
                        'data': {
                            'error': error_message,
                            'success': False
                        }
                    }
            
            return {
                'success': False,
                'error': error_message,
                'data': {}
            }
        
    def _looks_like_python_expression(self, expression: str) -> bool:
        """Check if a valueExpression looks like a Python expression that needs eval().

        This is a safety net for when the AI builder generates complex expressions
        but forgets to set evaluateAsExpression: true. It detects common Python
        patterns that would never be intended as plain string values.

        Args:
            expression: The valueExpression string to check

        Returns:
            True if the expression appears to need Python eval()
        """
        if not expression or not isinstance(expression, str):
            return False

        stripped = expression.strip()

        # List comprehension: [x for x in ...]
        if re.search(r'\[.+\bfor\b\s+\w+\s+\bin\b', stripped):
            return True

        # Dict comprehension: {k: v for k, v in ...}
        if re.search(r'\{.+\bfor\b\s+\w+\s+\bin\b', stripped):
            return True

        # Python function calls commonly used in expressions
        # Match calls like len(...), sum(...), range(...), sorted(...), etc.
        python_funcs = (
            r'\blen\s*\(', r'\bsum\s*\(', r'\brange\s*\(', r'\bsorted\s*\(',
            r'\bfilter\s*\(', r'\bmap\s*\(', r'\bmin\s*\(', r'\bmax\s*\(',
            r'\babs\s*\(', r'\bround\s*\(', r'\benumerate\s*\(', r'\bzip\s*\(',
            r'\bint\s*\(', r'\bfloat\s*\(', r'\bstr\s*\(', r'\bbool\s*\(',
            r'\blist\s*\(', r'\bdict\s*\(', r'\btuple\s*\(', r'\bset\s*\(',
            r'\bnext\s*\(', r'\bany\s*\(', r'\ball\s*\(',
            r'__import__\s*\('
        )
        for pattern in python_funcs:
            if re.search(pattern, stripped):
                return True

        # Expression starts with [ or { and contains Python-like syntax inside
        # e.g., [{'key': value} for ...] or {'key': expr, ...}
        if stripped.startswith('[') and ("for " in stripped or "if " in stripped):
            return True
        if stripped.startswith('{') and ("for " in stripped or ": " in stripped):
            # Distinguish from simple JSON: check for Python syntax markers
            if re.search(r"f['\"]|__import__|\.join\(|\.format\(|\bfor\b.*\bin\b", stripped):
                return True

        # f-string patterns: f"..." or f'...'
        if re.search(r"\bf['\"]", stripped):
            return True

        return False

    def _execute_set_variable_node(self, execution_id: str, node: Dict, variables: Dict) -> Dict:
        """Execute a Set Variable node in the workflow

        Args:
            execution_id: Unique ID of the workflow execution
            node: Node definition
            variables: Current workflow variables
            
        Returns:
            result: Result of the node execution
        """
        node_id = node['id']
        node_config = node.get('config', {})
        
        # Log the start of set variable node execution
        self.log_execution(
            execution_id, node_id, "info", 
            "Executing Set Variable node")
        
        try:
            # Get the variable name
            raw_variable_name = node_config.get('variableName', '')
            variable_name = raw_variable_name.replace('${', '').replace('}', '').strip()
            variable_name = re.sub(r'[^a-zA-Z0-9_]', '', variable_name)
            if not variable_name:
                raise ValueError("No variable name specified")
            
            # Determine value source
            value_source = node_config.get('valueSource', 'direct')
            
            if value_source == 'direct':
                # Get value expression
                value_expression = node_config.get('valueExpression', '')

                # Auto-detect expressions: if evaluateAsExpression is not explicitly set,
                # check if the valueExpression looks like a Python expression that needs eval()
                evaluate_as_expression = node_config.get('evaluateAsExpression', False)
                if not evaluate_as_expression and value_expression:
                    evaluate_as_expression = self._looks_like_python_expression(value_expression)
                    if evaluate_as_expression:
                        self.log_execution(
                            execution_id, node_id, "info",
                            f"Auto-detected Python expression in valueExpression, enabling expression evaluation")

                # Evaluate as expression if configured (or auto-detected)
                if evaluate_as_expression:
                    try:
                        self.log_execution(
                            execution_id, node_id, "debug",
                            f"value_source: {value_source}, evaluateAsExpression: {node_config.get('evaluateAsExpression')}")
                                    
                        #import math
                        #import json
                        #import re
                        
                        # Build eval context with all variables directly accessible
                        # This allows expressions like: firstName + " " + lastName
                        # Include comprehensive set of safe Python built-ins for complex expressions
                        eval_locals = {
                            # Modules
                            'math': math,
                            'json': json,
                            're': re,
                            
                            # Type constructors
                            'str': str,
                            'int': int,
                            'float': float,
                            'bool': bool,
                            'list': list,
                            'dict': dict,
                            'tuple': tuple,
                            'set': set,
                            'frozenset': frozenset,
                            'bytes': bytes,
                            'bytearray': bytearray,
                            
                            # Numeric functions
                            'abs': abs,
                            'round': round,
                            'min': min,
                            'max': max,
                            'sum': sum,
                            'pow': pow,
                            'divmod': divmod,
                            
                            # Sequence/iteration functions
                            'len': len,
                            'range': range,
                            'enumerate': enumerate,
                            'zip': zip,
                            'map': map,
                            'filter': filter,
                            'sorted': sorted,
                            'reversed': reversed,
                            'next': next,
                            'iter': iter,
                            'all': all,
                            'any': any,
                            
                            # String/character functions
                            'ord': ord,
                            'chr': chr,
                            'format': format,
                            'repr': repr,
                            
                            # Object inspection
                            'type': type,
                            'isinstance': isinstance,
                            'hasattr': hasattr,
                            'getattr': getattr,
                            'callable': callable,
                            
                            # Utility
                            'slice': slice,
                            'print': print,  # For debugging
                            
                            # Previous step data shortcuts
                            'prevData': variables.get('_previousStepOutput', {}),
                            '_prevData': variables.get('_previousStepOutput', {}),
                            '_previousStepOutput': variables.get('_previousStepOutput', {})
                        }
                        
                        # Add all workflow variables directly to eval context
                        for var_name, var_value in variables.items():
                            # Clean variable name (remove ${} wrapper if present)
                            clean_name = var_name.strip()
                            if clean_name.startswith('${') and clean_name.endswith('}'):
                                clean_name = clean_name[2:-1]
                            # Only add valid Python identifiers
                            if clean_name.isidentifier():
                                eval_locals[clean_name] = var_value

                        # Auto-parse JSON strings in variables
                        # This handles cases where variables were stored as JSON strings
                        # Scenario 1: The entire variable is a JSON string (e.g., '[{"a":1},{"b":2}]')
                        # Scenario 2: Variable is a list containing JSON string items (e.g., ['{"a":1}', '{"b":2}'])
                        for var_name in list(eval_locals.keys()):
                            var_value = eval_locals[var_name]
                            
                            # Skip built-in functions/modules
                            if callable(var_value) or var_name in ('math', 'json', 're'):
                                continue
                            
                            # Scenario 1: Variable itself is a JSON string (representing list or dict)
                            if isinstance(var_value, str) and var_value.strip():
                                first_char = var_value.strip()[0]
                                # Only try to parse if it looks like JSON (starts with { or [)
                                if first_char in ('{', '['):
                                    try:
                                        parsed = json.loads(var_value)
                                        if isinstance(parsed, (list, dict)):
                                            eval_locals[var_name] = parsed
                                            self.log_execution(
                                                execution_id, node_id, "debug",
                                                f"Auto-parsed JSON string variable '{var_name}' to {type(parsed).__name__}")
                                            var_value = parsed  # Update for next check
                                    except (json.JSONDecodeError, TypeError):
                                        pass
                            
                            # Scenario 2: Variable is a list containing JSON strings
                            if isinstance(var_value, list) and len(var_value) > 0:
                                parsed_list = []
                                any_parsed = False
                                for item in var_value:
                                    if isinstance(item, str) and item.strip():
                                        first_char = item.strip()[0]
                                        if first_char in ('{', '['):
                                            try:
                                                parsed_item = json.loads(item)
                                                parsed_list.append(parsed_item)
                                                any_parsed = True
                                            except (json.JSONDecodeError, TypeError):
                                                parsed_list.append(item)
                                        else:
                                            parsed_list.append(item)
                                    else:
                                        parsed_list.append(item)
                                if any_parsed:
                                    eval_locals[var_name] = parsed_list
                                    self.log_execution(
                                        execution_id, node_id, "debug",
                                        f"Auto-parsed JSON strings in list variable '{var_name}'")
                        
                        # Convert ${varName} syntax to bare varName in the expression
                        # This allows users to write ${firstName} + " " + ${lastName}
                        # which becomes: firstName + " " + lastName
                        processed_expr = re.sub(r'\$\{([^}]+)\}', r'\1', value_expression)

                        # DEBUG: Log what's in eval_locals
                        self.log_execution(
                            execution_id, node_id, "debug",
                            f"eval_locals keys: {list(eval_locals.keys())}")
                        self.log_execution(
                            execution_id, node_id, "debug",
                            f"'next' in eval_locals: {'next' in eval_locals}")
                        self.log_execution(
                            execution_id, node_id, "debug",
                            f"processed_expr: {processed_expr[:200]}")
                        
                        # Try to evaluate the expression
                        # NOTE: We pass eval_locals as globals (not locals) because
                        # comprehensions and generator expressions can only access globals,
                        # not locals, due to Python's scoping rules
                        safe_builtins = {
                            'len': len, 'str': str, 'int': int, 'float': float,
                            'bool': bool, 'list': list, 'dict': dict, 'tuple': tuple,
                            'sum': sum, 'max': max, 'min': min, 'abs': abs,
                            'round': round, 'sorted': sorted, 'reversed': reversed,
                            'any': any, 'all': all, 'enumerate': enumerate,
                            'range': range, 'zip': zip, 'map': map, 'filter': filter,
                            'isinstance': isinstance, 'type': type,
                            'True': True, 'False': False, 'None': None,
                        }
                        eval_locals["__builtins__"] = safe_builtins
                        expr_compiled = compile(processed_expr, '<string>', 'eval')
                        value = eval(expr_compiled, eval_locals)

                        self.log_execution(
                            execution_id, node_id, "info",
                            f"Evaluated expression: {value_expression} -> {value}")
                    except Exception as e:
                        self.log_execution(
                            execution_id, node_id, "warning",
                            f"Expression evaluation error: {str(e)}, using raw string")
                        # Fall back to variable-substituted string value
                        value = self._replace_variable_references(value_expression, variables)
                else:
                    # Not an expression - just do variable substitution
                    # Log available variables for debugging
                    self.log_execution(
                        execution_id, node_id, "debug",
                        f"Variable substitution - Expression: '{value_expression}'")
                    self.log_execution(
                        execution_id, node_id, "debug",
                        f"Available variables: {list(variables.keys())}")
                    
                    value = self._replace_variable_references(value_expression, variables)
                    
                    # Log result of substitution
                    if value != value_expression:
                        self.log_execution(
                            execution_id, node_id, "debug",
                            f"Substitution result: '{value_expression}' → '{str(value)[:200]}'")
                    elif '${' in value_expression:
                        self.log_execution(
                            execution_id, node_id, "warning",
                            f"Variable not substituted - expression still contains ${{}}. Check if variable exists.")
            
            elif value_source == 'output':
                # Get data from previous step using the specified path
                output_path = node_config.get('outputPath', '')
                prev_data = variables.get('_previousStepOutput', {})
                
                # Use the _get_nested_value function to safely access the path
                value = self._get_nested_value(prev_data, output_path)
                
                if value is None:
                    self.log_execution(
                        execution_id, node_id, "warning",
                        f"Path '{output_path}' not found in previous output, using empty string")
                    value = ''
            elif value_source == 'expression':
                # Get value expression
                value_expression = node_config.get('valueExpression', '')

                # Auto-detect expressions: if evaluateAsExpression is not explicitly set,
                # check if the valueExpression looks like a Python expression that needs eval()
                evaluate_as_expression = node_config.get('evaluateAsExpression', False)
                if not evaluate_as_expression and value_expression:
                    evaluate_as_expression = self._looks_like_python_expression(value_expression)
                    if evaluate_as_expression:
                        self.log_execution(
                            execution_id, node_id, "info",
                            f"Auto-detected Python expression in valueExpression, enabling expression evaluation")

                # Evaluate as expression if configured (or auto-detected)
                if evaluate_as_expression:
                    try:
                        self.log_execution(
                            execution_id, node_id, "debug",
                            f"value_source: {value_source}, evaluateAsExpression: {node_config.get('evaluateAsExpression')}")
                        
                        #import math
                        #import json
                        #import re
                        
                        # Build eval context with all variables directly accessible
                        # This allows expressions like: firstName + " " + lastName
                        # Include comprehensive set of safe Python built-ins for complex expressions
                        eval_locals = {
                            # Modules
                            'math': math,
                            'json': json,
                            're': re,
                            
                            # Type constructors
                            'str': str,
                            'int': int,
                            'float': float,
                            'bool': bool,
                            'list': list,
                            'dict': dict,
                            'tuple': tuple,
                            'set': set,
                            'frozenset': frozenset,
                            'bytes': bytes,
                            'bytearray': bytearray,
                            
                            # Numeric functions
                            'abs': abs,
                            'round': round,
                            'min': min,
                            'max': max,
                            'sum': sum,
                            'pow': pow,
                            'divmod': divmod,
                            
                            # Sequence/iteration functions
                            'len': len,
                            'range': range,
                            'enumerate': enumerate,
                            'zip': zip,
                            'map': map,
                            'filter': filter,
                            'sorted': sorted,
                            'reversed': reversed,
                            'next': next,
                            'iter': iter,
                            'all': all,
                            'any': any,
                            
                            # String/character functions
                            'ord': ord,
                            'chr': chr,
                            'format': format,
                            'repr': repr,
                            
                            # Object inspection
                            'type': type,
                            'isinstance': isinstance,
                            'hasattr': hasattr,
                            'getattr': getattr,
                            'callable': callable,
                            
                            # Utility
                            'slice': slice,
                            'print': print,  # For debugging
                            
                            # Previous step data shortcuts
                            'prevData': variables.get('_previousStepOutput', {}),
                            '_prevData': variables.get('_previousStepOutput', {}),
                            '_previousStepOutput': variables.get('_previousStepOutput', {})
                        }
                        
                        # Add all workflow variables directly to eval context
                        for var_name, var_value in variables.items():
                            # Clean variable name (remove ${} wrapper if present)
                            clean_name = var_name.strip()
                            if clean_name.startswith('${') and clean_name.endswith('}'):
                                clean_name = clean_name[2:-1]
                            # Only add valid Python identifiers
                            if clean_name.isidentifier():
                                eval_locals[clean_name] = var_value

                        # Auto-parse JSON strings in variables
                        # This handles cases where variables were stored as JSON strings
                        # Scenario 1: The entire variable is a JSON string (e.g., '[{"a":1},{"b":2}]')
                        # Scenario 2: Variable is a list containing JSON string items (e.g., ['{"a":1}', '{"b":2}'])
                        for var_name in list(eval_locals.keys()):
                            var_value = eval_locals[var_name]
                            
                            # Skip built-in functions/modules
                            if callable(var_value) or var_name in ('math', 'json', 're'):
                                continue
                            
                            # Scenario 1: Variable itself is a JSON string (representing list or dict)
                            if isinstance(var_value, str) and var_value.strip():
                                first_char = var_value.strip()[0]
                                # Only try to parse if it looks like JSON (starts with { or [)
                                if first_char in ('{', '['):
                                    try:
                                        parsed = json.loads(var_value)
                                        if isinstance(parsed, (list, dict)):
                                            eval_locals[var_name] = parsed
                                            self.log_execution(
                                                execution_id, node_id, "debug",
                                                f"Auto-parsed JSON string variable '{var_name}' to {type(parsed).__name__}")
                                            var_value = parsed  # Update for next check
                                    except (json.JSONDecodeError, TypeError):
                                        pass
                            
                            # Scenario 2: Variable is a list containing JSON strings
                            if isinstance(var_value, list) and len(var_value) > 0:
                                parsed_list = []
                                any_parsed = False
                                for item in var_value:
                                    if isinstance(item, str) and item.strip():
                                        first_char = item.strip()[0]
                                        if first_char in ('{', '['):
                                            try:
                                                parsed_item = json.loads(item)
                                                parsed_list.append(parsed_item)
                                                any_parsed = True
                                            except (json.JSONDecodeError, TypeError):
                                                parsed_list.append(item)
                                        else:
                                            parsed_list.append(item)
                                    else:
                                        parsed_list.append(item)
                                if any_parsed:
                                    eval_locals[var_name] = parsed_list
                                    self.log_execution(
                                        execution_id, node_id, "debug",
                                        f"Auto-parsed JSON strings in list variable '{var_name}'")
                        
                        # Convert ${varName} syntax to bare varName in the expression
                        # This allows users to write ${firstName} + " " + ${lastName}
                        # which becomes: firstName + " " + lastName
                        processed_expr = re.sub(r'\$\{([^}]+)\}', r'\1', value_expression)

                        # DEBUG: Log what's in eval_locals
                        self.log_execution(
                            execution_id, node_id, "debug",
                            f"eval_locals keys: {list(eval_locals.keys())}")
                        self.log_execution(
                            execution_id, node_id, "debug",
                            f"'next' in eval_locals: {'next' in eval_locals}")
                        self.log_execution(
                            execution_id, node_id, "debug",
                            f"processed_expr: {processed_expr[:200]}")
                        
                        # Try to evaluate the expression
                        # NOTE: We pass eval_locals as globals (not locals) because
                        # comprehensions and generator expressions can only access globals,
                        # not locals, due to Python's scoping rules
                        safe_builtins = {
                            'len': len, 'str': str, 'int': int, 'float': float,
                            'bool': bool, 'list': list, 'dict': dict, 'tuple': tuple,
                            'sum': sum, 'max': max, 'min': min, 'abs': abs,
                            'round': round, 'sorted': sorted, 'reversed': reversed,
                            'any': any, 'all': all, 'enumerate': enumerate,
                            'range': range, 'zip': zip, 'map': map, 'filter': filter,
                            'isinstance': isinstance, 'type': type,
                            'True': True, 'False': False, 'None': None,
                        }
                        eval_locals["__builtins__"] = safe_builtins
                        expr_compiled = compile(processed_expr, '<string>', 'eval')
                        value = eval(expr_compiled, eval_locals)

                        self.log_execution(
                            execution_id, node_id, "info",
                            f"Evaluated expression: {value_expression} -> {value}")
                    except Exception as e:
                        self.log_execution(
                            execution_id, node_id, "warning",
                            f"Expression evaluation error: {str(e)}, using raw string")
                        # Fall back to variable-substituted string value
                        value = self._replace_variable_references(value_expression, variables)
                else:
                    # Not an expression - just do variable substitution
                    value = self._replace_variable_references(value_expression, variables)
            else:
                raise ValueError(f"Unknown value source: {value_source}")
            
            print('Updating variable:', variable_name, str(value), execution_id)
            # Update the variable
            self._update_workflow_variable(
                execution_id, variable_name, 
                self._determine_variable_type(value), value)
            
            # Update in-memory variables
            variables[variable_name] = value
            print('in-memory value:', variables[variable_name])
            
            self.log_execution(
                execution_id, node_id, "info",
                f"Set variable '{variable_name}' to value", 
                {"value_type": type(value).__name__})
            
            # Return success result
            return {
                'success': True,
                'data': {
                    'variableSet': variable_name,
                    'variableValue': value,
                    'variableType': type(value).__name__
                }
            }
        
        except Exception as e:
            error_message = str(e)
            self.log_execution(
                execution_id, node_id, "error",
                f"Error setting variable: {error_message}")
            
            return {
                'success': False,
                'error': error_message,
                'data': {}
            }

    def _determine_variable_type(self, value) -> str:
        """Determine the type of a variable value
        
        Args:
            value: The value to check
            
        Returns:
            variable_type: Type name as string
        """
        if value is None:
            return 'null'
        elif isinstance(value, bool):
            return 'boolean'
        elif isinstance(value, int) or isinstance(value, float):
            return 'number'
        elif isinstance(value, str):
            return 'string'
        elif isinstance(value, list):
            return 'array'
        elif isinstance(value, dict):
            return 'object'
        else:
            # Convert to string for any other types
            return 'string'
        
    ###############################
    ########## Alert Action ##########
    ###############################
    def _execute_alert_node(self, execution_id: str, node: Dict, variables: Dict) -> Dict:
        """Execute an Alert node in the workflow
        
        Args:
            execution_id: Unique ID of the workflow execution
            node: Node definition
            variables: Current workflow variables
                
        Returns:
            result: Result of the node execution
        """
        node_id = node['id']
        node_config = node.get('config', {})
        
        # Log the start of alert node execution
        self.log_execution(
            execution_id, node_id, "info", 
            f"Executing alert node with type: {node_config.get('alertType', 'email')}")
        
        try:
            # Get alert parameters
            alert_type = node_config.get('alertType', 'email')
            recipients = self._replace_variable_references(node_config.get('recipients', ''), variables)
            message_template = self._replace_variable_references(node_config.get('messageTemplate', ''), variables)

            if not recipients:
                raise ValueError("Recipients are required")

            if not message_template:
                raise ValueError("Message template is required")

            # Get optional email-specific parameters
            email_subject = self._replace_variable_references(node_config.get('emailSubject', ''), variables)
            attachment_path = self._replace_variable_references(node_config.get('attachmentPath', ''), variables)

            # Log the alert details
            self.log_execution(
                execution_id, node_id, "info",
                f"Sending {alert_type} alert to {recipients}",
                {"message_length": len(message_template)})

            # Send the alert based on type
            result = None
            success = False

            if alert_type == 'email':
                # Parse recipients (split by comma)
                recipient_list = [r.strip() for r in recipients.split(',') if r.strip()]

                # Determine if message is HTML
                is_html = '<html' in message_template.lower()

                # Use custom subject or default to workflow name
                default_subject = f"Workflow Alert: {self._active_executions[execution_id]['workflow_name']}"
                subject = email_subject.strip() if email_subject and email_subject.strip() else default_subject

                # Validate attachment path if provided
                resolved_attachment = None
                if attachment_path and attachment_path.strip():
                    resolved_attachment = attachment_path.strip()
                    if not os.path.exists(resolved_attachment):
                        self.log_execution(
                            execution_id, node_id, "warning",
                            f"Attachment file not found: {resolved_attachment}")
                        resolved_attachment = None
                    else:
                        self.log_execution(
                            execution_id, node_id, "info",
                            f"Attaching file: {resolved_attachment}")

                # Send email
                success = send_email(
                    recipients=recipient_list,
                    subject=subject,
                    body=message_template,
                    attachment_path=resolved_attachment,
                    html_content=is_html
                )
                
                result = {
                    "alert_type": "email",
                    "recipients": recipient_list,
                    "success": success,
                    "message": "Email sent successfully" if success else "Failed to send email"
                }
                
            elif alert_type == 'text':  # Mapping slack to text based on template
                # Handle text message
                response = sms_text_message_alert(
                    input_text=message_template,
                    destination=recipients
                )
                
                success = "succeeded" in response.lower()
                
                result = {
                    "alert_type": "text",
                    "recipient": recipients,
                    "success": success,
                    "message": response
                }
                
            elif alert_type == 'call':  # Mapping webhook to call based on template
                # Handle phone call
                response = aihub_phone_call_alert(
                    input_text=message_template,
                    destination=recipients
                )
                
                success = "succeeded" in response.lower()
                
                result = {
                    "alert_type": "call",
                    "recipient": recipients,
                    "success": success,
                    "message": response
                }
                
            else:
                raise ValueError(f"Unsupported alert type: {alert_type}")
            
            # Update output variable if configured
            if success and node_config.get('outputVariable'):
                var_name = self._extract_variable_name(node_config.get('outputVariable'))
                
                # Update variable in database
                self._update_workflow_variable(
                    execution_id, var_name, 'object', result)
                    
                # Update in-memory variables
                variables[var_name] = result
                
                self.log_execution(
                    execution_id, node_id, "info",
                    f"Stored alert result in variable: {var_name}")
            
            # Log the result
            log_level = "info" if success else "error"
            self.log_execution(
                execution_id, node_id, log_level,
                f"Alert {alert_type} result: {result['message']}")
            
            # Check continue on error
            if not success and node_config.get('continueOnError', False):
                self.log_execution(
                    execution_id, node_id, "warning",
                    "Continuing workflow despite alert failure")
                
                return {
                    'success': True,  # Return success so workflow continues
                    'data': result
                }
            
            return {
                'success': success,
                'data': result,
                'error': None if success else result.get('message', "Alert failed")
            }
            
        except Exception as e:
            error_message = str(e)
            self.log_execution(
                execution_id, node_id, "error",
                f"Alert node error: {error_message}")
            
            # Check if we should continue on error
            if node_config.get('continueOnError', False):
                self.log_execution(
                    execution_id, node_id, "warning",
                    "Continuing workflow despite alert error")
                    
                return {
                    'success': True,  # Return success so workflow continues
                    'data': {
                        'alert_type': node_config.get('alertType', 'unknown'),
                        'error': error_message,
                        'success': False
                    }
                }
            
            return {
                'success': False,
                'error': error_message,
                'data': {
                    'alert_type': node_config.get('alertType', 'unknown')
                }
            }

    def _execute_conditional_node(self, execution_id: str, node: Dict, variables: Dict) -> Dict:
        """Execute a Conditional node
        
        Args:
            execution_id: Unique ID of the workflow execution
            node: Node definition containing configuration
            variables: Current workflow variables
            
        Returns:
            Result dictionary with success flag determining the path
        """
        node_id = node['id']
        node_config = node.get('config', {})
        
        try:
            condition_result = False
            
            # Process variable references in the config
            processed_config = {}
            for key, value in node_config.items():
                if isinstance(value, str):
                    processed_config[key] = self._replace_variable_references(value, variables)
                else:
                    processed_config[key] = value
            
            condition_type = processed_config.get('conditionType', 'comparison')
            
            if condition_type == 'comparison':
                left_val = self._evaluate_value(processed_config.get('leftValue', ''))
                right_val = self._evaluate_value(processed_config.get('rightValue', ''))
                operator = processed_config.get('operator', '==')
                
                condition_result = self._evaluate_comparison(left_val, operator, right_val)
                
            elif condition_type == 'expression':
                expression = processed_config.get('expression', '')
                condition_result = self._evaluate_expression(expression, variables)
                
            elif condition_type == 'contains':
                text = str(self._evaluate_value(processed_config.get('containsText', '')) or '')
                search = str(processed_config.get('searchText', '') or '')
                condition_result = search in text
                
            elif condition_type == 'exists':
                var_name = processed_config.get('existsVariable', '')
                condition_result = var_name in variables
                
            elif condition_type == 'empty':
                # Use the raw (unresolved) config value here — we need the variable
                # NAME, not its resolved value. The generic _replace_variable_references
                # pass above would turn "${customerList}" into its contents, breaking
                # the lookup on variables.
                raw_empty_var = str(node_config.get('emptyVariable', '') or '').strip()
                var_name = raw_empty_var.strip('${}').strip()
                if not var_name:
                    # Nothing configured — treat as empty.
                    condition_result = True
                else:
                    value = variables.get(var_name)
                    condition_result = (
                        value is None
                        or value == ''
                        or (isinstance(value, list) and len(value) == 0)
                        or (isinstance(value, dict) and len(value) == 0)
                    )
            
            self.log_execution(
                execution_id, node_id, "info",
                f"Conditional evaluation: {'TRUE' if condition_result else 'FALSE'} (Type: {condition_type})")
            
            return {
                'success': condition_result,  # This determines pass/fail path
                'data': {
                    'conditionResult': condition_result,
                    'conditionType': condition_type
                }
            }
            
        except Exception as e:
            error_message = str(e)
            self.log_execution(
                execution_id, node_id, "error",
                f"Conditional evaluation error: {error_message}")
            
            # On error, default to fail path
            return {
                'success': False,
                'error': error_message,
                'data': {}
            }

    def _evaluate_value(self, value):
        """Evaluate a value that might be a variable reference or literal"""
        if not isinstance(value, str):
            return value
        
        # Try to convert to appropriate type
        value = value.strip()
        
        # Check for number
        try:
            if '.' in value:
                return float(value)
            else:
                return int(value)
        except ValueError:
            pass
        
        # Check for boolean
        if value.lower() == 'true':
            return True
        elif value.lower() == 'false':
            return False
        elif value.lower() == 'null' or value.lower() == 'none':
            return None
        
        # Try JSON parse
        try:
            #import json
            return json.loads(value)
        except:
            pass
        
        # Return as string
        return value

    def _evaluate_comparison(self, left, operator, right):
        """Evaluate a comparison operation"""
        try:
            if operator == '==':
                return left == right
            elif operator == '!=':
                return left != right
            elif operator == '>':
                return left > right
            elif operator == '>=':
                return left >= right
            elif operator == '<':
                return left < right
            elif operator == '<=':
                return left <= right
            else:
                return False
        except Exception:
            # If comparison fails (e.g., incompatible types), return False
            return False

    def _evaluate_expression(self, expression: str, variables: Dict) -> bool:
        """Safely evaluate a Python expression with a whitelisted set of builtins.

        Available functions: len, str, int, float, bool, list, dict, tuple,
        sum, max, min, abs, round, sorted, reversed, any, all, enumerate,
        range, zip, map, filter, isinstance, type.

        Variables are injected into the eval context by name (with ${} stripped),
        so ${varName} in the expression becomes the Python identifier varName.
        This avoids the raw string substitution issue where string values like
        "active" would be injected unquoted and treated as undefined Python names.
        """
        try:
            import re as re_module

            # Convert ${varName} to bare varName for eval context lookup
            # instead of doing raw string substitution which breaks string values
            processed_expr = re_module.sub(r'\$\{([^}]+)\}', r'\1', expression)

            # For now, use a simple eval with limited scope
            safe_builtins = {
                'len': len, 'str': str, 'int': int, 'float': float,
                'bool': bool, 'list': list, 'dict': dict, 'tuple': tuple,
                'sum': sum, 'max': max, 'min': min, 'abs': abs,
                'round': round, 'sorted': sorted, 'reversed': reversed,
                'any': any, 'all': all, 'enumerate': enumerate,
                'range': range, 'zip': zip, 'map': map, 'filter': filter,
                'isinstance': isinstance, 'type': type,
                'True': True, 'False': False, 'None': None,
            }
            safe_dict = {
                '__builtins__': safe_builtins,
            }
            # Add all workflow variables by their clean name (strip ${} if stored that way)
            for var_name, var_value in variables.items():
                clean_name = var_name.strip()
                if clean_name.startswith('${') and clean_name.endswith('}'):
                    clean_name = clean_name[2:-1]
                if clean_name.isidentifier():
                    safe_dict[clean_name] = var_value
            # Also add raw variables dict for backward compatibility
            safe_dict.update(variables)

            self.log_execution(
                None, None, "debug",
                f"Evaluating conditional expression: {processed_expr}")

            result = eval(processed_expr, safe_dict)
            return bool(result)

        except Exception as e:
            logger.error(f"Expression evaluation error for '{expression}': {str(e)}")
            # Fallback: try with raw string substitution for backward compatibility
            try:
                fallback_expr = self._replace_variable_references(expression, variables)
                logger.info(f"Retrying expression with string substitution: {fallback_expr}")
                safe_builtins_fb = {
                    'len': len, 'str': str, 'int': int, 'float': float,
                    'bool': bool, 'list': list, 'dict': dict, 'tuple': tuple,
                    'sum': sum, 'max': max, 'min': min, 'abs': abs,
                    'round': round, 'sorted': sorted, 'reversed': reversed,
                    'any': any, 'all': all, 'enumerate': enumerate,
                    'range': range, 'zip': zip, 'map': map, 'filter': filter,
                    'isinstance': isinstance, 'type': type,
                    'True': True, 'False': False, 'None': None,
                }
                safe_dict_fb = {'__builtins__': safe_builtins_fb}
                safe_dict_fb.update(variables)
                result = eval(fallback_expr, safe_dict_fb)
                return bool(result)
            except Exception as e2:
                logger.error(f"Expression evaluation fallback also failed: {str(e2)}")
                return False
        
    def _execute_loop_node(self, execution_id: str, node: Dict, variables: Dict) -> Dict:
        """Execute a Loop node
        
        Args:
            execution_id: Unique ID of the workflow execution
            node: Node definition containing configuration
            variables: Current workflow variables
            
        Returns:
            Result dictionary with collected results from iterations
        """
        node_id = node.get('id', '')
        node_config = node.get('config', {})

        self.log_execution(
                    execution_id, node_id, "info",
                    f"Initializing tracking structures...")

        # Initialize tracking structures if needed
        if execution_id not in self._completed_loops:
            self._completed_loops[execution_id] = set()
        if execution_id not in self._loop_results:
            self._loop_results[execution_id] = {}
        
        try:
            # Get the source array using same logic as frontend
            loop_source = node_config.get('loopSource', '')
            source_type = node_config.get('sourceType', 'auto')
            items = []
            source_description = ''

            self.log_execution(
                    execution_id, node_id, "info",
                    f"Loop Source: {loop_source} | Source Type: {source_type}")

            logger.info(f"Loop Source: {loop_source} | Source Type: {source_type}")
            
            # Previous step output
            prev_output = variables.get('_previousStepOutput', {})

            self.log_execution(
                    execution_id, node_id, "info",
                    f"Previous Step Output: {prev_output}")

            logger.info(f"Previous Step Output: {prev_output}")
            
            if source_type == 'auto' or not loop_source:
                # Auto-detect array
                items, source_description = self._auto_detect_array(prev_output)
                self.log_execution(
                    execution_id, node_id, "info",
                    f"Auto-detected array from: {source_description}")
                logger.info(f"Auto-detected array from: {source_description}")
                    
            elif source_type == 'folderFiles':
                # Special handling for Folder Selector
                if isinstance(prev_output, dict):
                    items = prev_output.get('allFiles', prev_output.get('data', {}).get('allFiles', []))
                source_description = 'Folder Selector allFiles'
                
            elif source_type == 'split':
                # Split string into array - supports nested paths like ${extractedNotes.Notes}
                string_to_split = ''
                if loop_source.startswith('${') and loop_source.endswith('}'):
                    full_path = loop_source[2:-1]
                    
                    # Split on first dot to separate variable name from nested path
                    if '.' in full_path:
                        parts = full_path.split('.', 1)
                        var_name = parts[0]
                        nested_path = parts[1]
                    else:
                        var_name = full_path
                        nested_path = None
                    
                    # Get the base variable
                    found_key, raw_value = self.find_variable(var_name, variables)
                    
                    # If there's a nested path, navigate into the structure
                    if nested_path and raw_value is not None:
                        raw_value = self._get_nested_value_for_variables(raw_value, nested_path)
                    
                    string_to_split = str(raw_value) if raw_value is not None else ''
                else:
                    string_to_split = str(self._get_nested_value(prev_output, loop_source) or '')
                
                delimiter = node_config.get('splitDelimiter', ',')
                items = [s.strip() for s in string_to_split.split(delimiter) if s.strip()]
                source_description = f'Split string by "{delimiter}"'
                
            elif source_type == 'variable':
                # Variable reference - supports nested paths like ${extractedNotes.Notes}
                full_path = loop_source.replace('${', '').replace('}', '')
                
                # Split on first dot to separate variable name from nested path
                if '.' in full_path:
                    parts = full_path.split('.', 1)
                    var_name = parts[0]
                    nested_path = parts[1]
                else:
                    var_name = full_path
                    nested_path = None
                
                # Get the base variable
                found_key, raw_value = self.find_variable(var_name, variables)
                
                # If there's a nested path, navigate into the structure
                if nested_path and raw_value is not None:
                    self.log_execution(
                        execution_id, node_id, "debug",
                        f"Navigating nested path '{nested_path}' in variable '{var_name}'")
                    raw_value = self._get_nested_value_for_variables(raw_value, nested_path)
                
                # Parse the variable value into a list
                if isinstance(raw_value, str):
                    parsed_list = self._parse_string_to_list(raw_value)
                    if parsed_list is not None:
                        items = parsed_list
                        source_description = f'Variable (parsed): {var_name}'
                    else:
                        items = raw_value
                        source_description = f'Variable: {var_name}'
                elif isinstance(raw_value, list):
                    items = raw_value
                    source_description = f'Variable (list): {var_name}'
                elif isinstance(raw_value, dict):
                    items, nested_desc = self._auto_detect_array(raw_value)
                    source_description = f'Variable {var_name} -> {nested_desc}'
                else:
                    items = raw_value
                    source_description = f'Variable: {var_name}'
                
            elif source_type == 'path':
                # Path reference
                items = self._get_nested_value(prev_output, loop_source) or []
                source_description = f'Path: {loop_source}'
                
            else:
                # Direct source handling
                if loop_source.startswith('${') and loop_source.endswith('}'):
                    var_name = loop_source[2:-1]
                    items = variables.get(var_name, [])
                    source_description = f'Variable: {var_name}'
                elif '.' in loop_source:
                    items = self._get_nested_value(prev_output, loop_source) or []
                    source_description = f'Path: {loop_source}'
                elif loop_source:
                    items = variables.get(loop_source, prev_output.get(loop_source, []))
                    source_description = f'Direct: {loop_source}'
                else:
                    items = prev_output if isinstance(prev_output, list) else []
                    source_description = 'Previous output'
            
            # Ensure we have a list
            if not isinstance(items, list):
                if isinstance(items, str):
                    parsed_list = self._parse_string_to_list(items)
                    if parsed_list is not None:
                        items = parsed_list
                        source_description += ' (parsed from string)'

                if not isinstance(items, list):
                    self.log_execution(
                        execution_id, node_id, "warning",
                        f"Loop source is not an array: {type(items).__name__}, using empty array")
                    items = []
            
            self.log_execution(
                execution_id, node_id, "info",
                f"Starting loop with {len(items)} items from {source_description}")

            logger.info(f"Loop config: {node_config}")
            
            # Get loop configuration
            # Sanitize variable names: strip ${}, whitespace, and ensure valid identifier
            def sanitize_var_name(name, default):
                if not name:
                    return default
                # Remove ${} wrapper if user accidentally included it
                sanitized = name.replace('${', '').replace('}', '').strip()
                # Remove any invalid characters (keep only alphanumeric and underscore)
                #import re
                sanitized = re.sub(r'[^a-zA-Z0-9_]', '', sanitized)
                return sanitized if sanitized else default
            
            item_var = sanitize_var_name(node_config.get('itemVariable', ''), 'currentItem')
            index_var = sanitize_var_name(node_config.get('indexVariable', ''), 'currentIndex')
            array_info_var = node_config.get('arrayInfoVariable', '')
            
            # Log the variable names being used (helps debug when users configure custom names)
            self.log_execution(
                execution_id, node_id, "debug",
                f"Loop variable names - item: '{item_var}', index: '{index_var}'")

            max_iterations_config = node_config.get('maxIterations', 100)
            try:
                max_iterations_value = int(max_iterations_config) if max_iterations_config else 100
            except (TypeError, ValueError):
                self.log_execution(
                    execution_id, node_id, "warning",
                    f"Invalid maxIterations value: {max_iterations_config}, using default value")
                max_iterations_value = 100

            max_iterations = min(len(items), max_iterations_value)

            output_mode = node_config.get('outputMode', 'array')
            
            # Store array info if variable specified
            if array_info_var:
                self._update_workflow_variable(
                    execution_id, array_info_var, 'object', {
                        'items': items,
                        'length': len(items),
                        'source': source_description,
                        'isEmpty': len(items) == 0
                    })
                variables[array_info_var] = {
                    'items': items,
                    'length': len(items),
                    'source': source_description,
                    'isEmpty': len(items) == 0
                }
            
            # Find the loop body node (connected via pass connection)
            workflow_data = self._active_executions[execution_id]['workflow_data']
            connections = workflow_data.get('connections', [])

            #logger.info(f"Workflow Data: {workflow_data}")
            #logger.info(f"connections: {connections}")
            
            loop_body_connections = [
                conn for conn in connections 
                if conn['source'] == node_id and 
                (conn.get('type') == 'pass' or not conn.get('type'))
            ]
            
            if not loop_body_connections or len(items) == 0:
                if not loop_body_connections:
                    self.log_execution(
                        execution_id, node_id, "warning",
                        "No loop body connection found")
                else:
                    self.log_execution(
                        execution_id, node_id, "info",
                        "No items to iterate over")
                        
                return {
                    'success': True,
                    'data': {
                        'message': 'No items to process or no loop body connected',
                        '_loopStats': {
                            'totalItems': len(items),
                            'processedItems': 0,
                            'skippedItems': len(items),
                            'source': source_description
                        }
                    }
                }
            
            loop_body_node_id = loop_body_connections[0]['target']
            
            # Store original variable values
            original_item_var = variables.get(item_var)
            original_index_var = variables.get(index_var)
            original_loop_stats = variables.get('_loopStats')
            
            # Mark loop as active
            if not hasattr(self, '_active_loops'):
                self._active_loops = {}
            self._active_loops[node_id] = {
                'execution_id': execution_id,
                'current_index': 0,
                'total_items': len(items),
                'results': []
            }
            
            results = []
            
            # Execute loop iterations
            for i in range(max_iterations):
                # Check if execution is still active
                if execution_id not in self._active_executions:
                    self.log_execution(
                        execution_id, node_id, "warning",
                        "Workflow execution stopped during loop")
                    break
                
                # Update loop state
                self._active_loops[node_id]['current_index'] = i
                
                # Set loop variables
                variables[item_var] = items[i]
                variables[index_var] = i
                variables['_loopStats'] = {
                    'currentIndex': i,
                    'totalItems': len(items),
                    'processedItems': i + 1,
                    'isLastItem': i == max_iterations - 1
                }
                
                # Update variables in database
                self._update_workflow_variable(
                    execution_id, item_var, 'any', items[i])
                self._update_workflow_variable(
                    execution_id, index_var, 'number', i)
                self._update_workflow_variable(
                    execution_id, '_loopStats', 'object', variables['_loopStats'])
                
                self.log_execution(
                    execution_id, node_id, "info",
                    f"Loop iteration {i + 1}/{max_iterations}")
                
                # Log what variables are being set (helps debug Set Variable issues)
                self.log_execution(
                    execution_id, node_id, "debug",
                    f"Setting loop variable '{item_var}' = {str(items[i])[:200]}")
                self.log_execution(
                    execution_id, node_id, "debug",
                    f"Setting loop variable '{index_var}' = {i}")
                
                # Execute the loop body branch
                loop_result = self._execute_loop_body_branch(
                    execution_id, loop_body_node_id, variables, node_id)
                
                # # Collect results based on output mode
                # if output_mode == 'array':
                #     results.append(loop_result)
                # elif output_mode == 'last':
                #     results = [loop_result]
                # elif output_mode == 'concat':
                #     # Concatenate string results
                #     str_result = str(loop_result) if loop_result else ''
                #     results.append(str_result)
                # elif output_mode == 'merge' and isinstance(loop_result, dict):
                #     # Merge object results
                #     if not results:
                #         results = {}
                #     results.update(loop_result)
                
                # # Store in active loop results
                # self._active_loops[node_id]['results'].append(loop_result)

                # SAFE RESULT COLLECTION - Handle any data type gracefully
                try:
                    if output_mode == 'array':
                        # Most common case - just collect everything
                        results.append(loop_result)
                        
                    elif output_mode == 'last':
                        # Keep only the last result
                        results = [loop_result]
                        
                    elif output_mode == 'concat':
                        # Try to concatenate as strings
                        if loop_result is not None:
                            str_result = str(loop_result)
                            results.append(str_result)
                        
                    elif output_mode == 'merge':
                        # Only merge if it's actually a dict, otherwise skip
                        if isinstance(loop_result, dict):
                            if not isinstance(results, dict):
                                results = {}
                            try:
                                results.update(loop_result)
                            except Exception as merge_err:
                                self.log_execution(
                                    execution_id, node_id, "warning",
                                    f"Could not merge result in iteration {i+1}: {str(merge_err)}")
                        else:
                            # Don't fail - just log and continue
                            self.log_execution(
                                execution_id, node_id, "debug",
                                f"Skipping non-dict result in merge mode (iteration {i+1})")
                            # If results is still a list, keep it as array mode
                            if isinstance(results, list):
                                results.append(loop_result)
                                
                    else:
                        # Default fallback - treat as array
                        results.append(loop_result)
                        
                except Exception as collect_err:
                    # Never fail the loop - just log the issue and continue
                    self.log_execution(
                        execution_id, node_id, "warning",
                        f"Error collecting result in iteration {i+1}: {str(collect_err)}")
                    # Fallback to array collection
                    if not isinstance(results, list):
                        results = []
                    results.append(loop_result)
                
                # Store in active loop results (safe storage)
                if hasattr(self, '_active_loops') and node_id in self._active_loops:
                    self._active_loops[node_id]['results'].append(loop_result)
            
            # Clean up active loop
            if node_id in self._active_loops:
                del self._active_loops[node_id]
            
            # Restore original variable values
            if original_item_var is not None:
                variables[item_var] = original_item_var
            else:
                variables.pop(item_var, None)
                
            if original_index_var is not None:
                variables[index_var] = original_index_var
            else:
                variables.pop(index_var, None)
                
            if original_loop_stats is not None:
                variables['_loopStats'] = original_loop_stats
            else:
                variables.pop('_loopStats', None)
            
            self.log_execution(
                execution_id, node_id, "info",
                f"Loop completed: {max_iterations} iterations processed")
            
            # Prepare output based on mode
            # if output_mode == 'concat':
            #     output_data = {'result': ''.join(results) if isinstance(results, list) else ''}
            # elif output_mode == 'merge':
            #     output_data = results if isinstance(results, dict) else {}
            # elif output_mode == 'array':
            #     output_data = {'results': results}
            # elif output_mode == 'last':
            #     output_data = results[0] if results else {}
            # else:
            #     output_data = {}
            
            # output_data['_loopStats'] = {
            #     'totalItems': len(items),
            #     'processedItems': max_iterations,
            #     'skippedItems': len(items) - max_iterations,
            #     'source': source_description
            # }

            # # Store results for End Loop node (add this before final return)
            # if execution_id not in self._loop_results:
            #     self._loop_results[execution_id] = {}
            # self._loop_results[execution_id][node_id] = output_data 
            
            # # Mark this loop as completed
            # if execution_id not in self._completed_loops:
            #     self._completed_loops[execution_id] = set()
            # self._completed_loops[execution_id].add(node_id)
            
            # return {
            #     'success': True,
            #     'data': output_data
            # }

            # Prepare output based on mode (SAFE VERSION)
            try:
                if output_mode == 'concat':
                    if isinstance(results, list):
                        # Join all string representations
                        output_data = {'result': ''.join(str(r) for r in results if r is not None)}
                    else:
                        output_data = {'result': str(results)}
                        
                elif output_mode == 'merge':
                    if isinstance(results, dict):
                        output_data = results
                    else:
                        # Fallback to array format if merge didn't work
                        output_data = {'results': results if isinstance(results, list) else [results]}
                        
                elif output_mode == 'array':
                    # Standard array output
                    output_data = {'results': results if isinstance(results, list) else [results]}
                    
                elif output_mode == 'last':
                    # Get the last result
                    if isinstance(results, list) and results:
                        output_data = results[-1] if isinstance(results[-1], dict) else {'result': results[-1]}
                    else:
                        output_data = {}
                else:
                    # Default to array format
                    output_data = {'results': results if isinstance(results, list) else [results]}
                    
            except Exception as output_err:
                # Ultimate fallback - just wrap everything safely
                self.log_execution(
                    execution_id, node_id, "warning",
                    f"Error preparing output: {str(output_err)}, using safe defaults")
                output_data = {'results': results if isinstance(results, list) else []}
            
            # Add loop statistics (always safe)
            output_data['_loopStats'] = {
                'totalItems': len(items),
                'processedItems': max_iterations,
                'skippedItems': len(items) - max_iterations,
                'source': source_description
            }

            # Store results for End Loop node
            if execution_id not in self._loop_results:
                self._loop_results[execution_id] = {}
            self._loop_results[execution_id][node_id] = output_data 
            
            # Mark this loop as completed
            if execution_id not in self._completed_loops:
                self._completed_loops[execution_id] = set()
            self._completed_loops[execution_id].add(node_id)
            
            return {
                'success': True,
                'data': output_data
            }
            
        except Exception as e:
            # Clean up on error
            if hasattr(self, '_active_loops') and node_id in self._active_loops:
                del self._active_loops[node_id]

            if execution_id in self._completed_loops:
                self._completed_loops[execution_id].discard(node_id)
                
            error_message = str(e)
            self.log_execution(
                execution_id, node_id, "error",
                f"Loop execution error: {error_message}")
            
            return {
                'success': False,
                'error': error_message,
                'data': {}
            }

    def _execute_loop_body_branch(self, execution_id: str, start_node_id: str, 
                                variables: Dict, loop_node_id: str) -> Dict:
        """Execute the loop body branch until End Loop is encountered
        
        Args:
            execution_id: Unique ID of the workflow execution
            start_node_id: ID of the first node in loop body
            variables: Current workflow variables
            loop_node_id: ID of the Loop node (for End Loop detection)
            
        Returns:
            Result from the loop body execution
        """
        logger.info(f"Executing loop body branch...")
        # Find the node in the workflow
        workflow_data = self._active_executions[execution_id]['workflow_data']
        nodes = workflow_data.get('nodes', [])
        connections = workflow_data.get('connections', [])
        
        visited_nodes = set()
        current_node_id = start_node_id
        last_result = {}
        
        logger.info(f"Current Node ID: {current_node_id}")
        while current_node_id:
            # Prevent infinite loops
            if current_node_id in visited_nodes:
                break
            visited_nodes.add(current_node_id)

            logger.debug(f"visited_nodes: {visited_nodes}")
            
            # Find the node
            node = next((n for n in nodes if n['id'] == current_node_id), None)
            if not node:
                break

            logger.debug(f"Found Node: {node}")
            logger.debug(f"Node Type: {node.get('type', '')}")
            # Check if it's an End Loop node
            if node.get('type') == 'End Loop':
                # Don't execute End Loop during iterations
                logger.info("Reached End Loop node, returning to loop")
                self.log_execution(
                    execution_id, current_node_id, "debug",
                    "Reached End Loop node, returning to loop")
                break
            
            logger.info(f"Executing node...")
            logger.debug(f"Node Execution Parameters")
            logger.debug(f"execution_id: {execution_id}")
            logger.debug(f"node: {to_truncated_str(node)}")
            logger.debug(f"variables: {to_truncated_str(variables)}")

            try:
                # Execute the node
                result = self._execute_node(execution_id, node, variables)

                # logger.info(f'Node Result: {result}')
                # last_result = result.get('data', {})
                # logger.debug(f"Last Result (_previousStepOutput): {last_result}")
                
                # # Update variables with the result
                # variables['_previousStepOutput'] = last_result
                
                # # Find next node based on result
                # next_connections = [
                #     conn for conn in connections 
                #     if conn['source'] == current_node_id
                # ]

                # logger.debug(f"Next Connections: {next_connections}")
                
                # current_node_id = None
                # for conn in next_connections:
                #     logger.debug(f"conn: {conn}")
                #     conn_type = conn.get('type', 'pass')
                    
                #     # Follow appropriate path
                #     if (conn_type == 'pass' and result.get('success', False)) or \
                #     (conn_type == 'fail' and not result.get('success', False)):
                #         current_node_id = conn['target']
                #         logger.info(f"Found next node: {current_node_id}")
                #         break

                # Handle different return types robustly
                if isinstance(result, str):
                    # Normal case: _execute_node returned next node ID
                    next_node_id = result
                    last_result = variables.get('_previousStepOutput', {})
                    current_node_id = next_node_id
                    
                elif isinstance(result, dict):
                    # Alternative case: if _execute_node returned a dict (shouldn't happen but be safe)
                    last_result = result.get('data', {})
                    
                    # Determine next node based on success/failure
                    if result.get('success', False):
                        current_node_id = self._find_next_pass_connection(execution_id, current_node_id)
                    else:
                        current_node_id = self._find_next_fail_connection(execution_id, current_node_id)
                        
                elif result is None:
                    # No next node returned
                    last_result = variables.get('_previousStepOutput', {})
                    current_node_id = None
                    
                else:
                    # Unexpected type
                    self.log_execution(
                        execution_id, loop_node_id, "warning",
                        f"Unexpected return type from _execute_node: {type(result).__name__}")
                    last_result = variables.get('_previousStepOutput', {})
                    current_node_id = None

            except Exception as e:
                print(str(e))
                logger.error(f"Error executing loop body branch - {str(e)}")

                self.log_execution(
                execution_id, loop_node_id, "error",
                f"Error executing node {current_node_id}: {str(e)}")
            
                # On error, try to find a fail connection
                current_node_id = self._find_next_fail_connection(execution_id, current_node_id)
                
                if not current_node_id:
                    # No fail path, stop execution
                    break
        
        return last_result

    def _find_next_connection_by_type(self, execution_id: str, source_node_id: str, 
                                     connection_type: str) -> Optional[str]:
        """
        Find the next node connected from source_node_id with the specified connection type.
        
        Args:
            execution_id: Unique ID of the workflow execution
            source_node_id: ID of the source node
            connection_type: Type of connection to follow ('pass', 'fail', 'complete')
            
        Returns:
            Target node ID if found, None otherwise
        """
        if execution_id not in self._active_executions:
            return None
            
        workflow_data = self._active_executions[execution_id]['workflow_data']
        connections = workflow_data.get('connections', [])
        
        # Find connections from the source node with the specified type
        matching_connections = [
            conn for conn in connections 
            if conn['source'] == source_node_id and conn.get('type', 'pass') == connection_type
        ]
        
        # Return the first matching connection's target
        if matching_connections:
            return matching_connections[0]['target']
        
        return None
    
    def _find_next_pass_connection(self, execution_id: str, source_node_id: str) -> Optional[str]:
        """
        Find the next node connected via a 'pass' connection.
        
        Args:
            execution_id: Unique ID of the workflow execution
            source_node_id: ID of the source node
            
        Returns:
            Target node ID if found, None otherwise
        """
        return self._find_next_connection_by_type(execution_id, source_node_id, 'pass')
    
    def _find_next_fail_connection(self, execution_id: str, source_node_id: str) -> Optional[str]:
        """
        Find the next node connected via a 'fail' connection.
        
        Args:
            execution_id: Unique ID of the workflow execution
            source_node_id: ID of the source node
            
        Returns:
            Target node ID if found, None otherwise
        """
        return self._find_next_connection_by_type(execution_id, source_node_id, 'fail')
    
    def _find_next_complete_connection(self, execution_id: str, source_node_id: str) -> Optional[str]:
        """
        Find the next node connected via a 'complete' connection.
        
        Args:
            execution_id: Unique ID of the workflow execution
            source_node_id: ID of the source node
            
        Returns:
            Target node ID if found, None otherwise
        """
        return self._find_next_connection_by_type(execution_id, source_node_id, 'complete')
    
    def _find_any_next_connection(self, execution_id: str, source_node_id: str, 
                                 priority_order: List[str] = None) -> Optional[str]:
        """
        Find the next node with any connection, following a priority order.
        
        Args:
            execution_id: Unique ID of the workflow execution
            source_node_id: ID of the source node
            priority_order: List of connection types in priority order
                          Default: ['pass', 'complete', 'fail']
            
        Returns:
            Target node ID if found, None otherwise
        """
        if priority_order is None:
            priority_order = ['pass', 'complete', 'fail']
        
        for conn_type in priority_order:
            next_node = self._find_next_connection_by_type(execution_id, source_node_id, conn_type)
            if next_node:
                return next_node
        
        # If no connections found with specified types, return first connection of any type
        if execution_id in self._active_executions:
            workflow_data = self._active_executions[execution_id]['workflow_data']
            connections = workflow_data.get('connections', [])
            
            all_connections = [
                conn for conn in connections 
                if conn['source'] == source_node_id
            ]
            
            if all_connections:
                return all_connections[0]['target']
        
        return None
    
    def _get_all_connections_from_node(self, execution_id: str, source_node_id: str) -> List[Dict]:
        """
        Get all connections from a source node.
        
        Args:
            execution_id: Unique ID of the workflow execution
            source_node_id: ID of the source node
            
        Returns:
            List of connection dictionaries
        """
        if execution_id not in self._active_executions:
            return []
            
        workflow_data = self._active_executions[execution_id]['workflow_data']
        connections = workflow_data.get('connections', [])
        
        return [
            conn for conn in connections 
            if conn['source'] == source_node_id
        ]

    def _parse_string_to_list(self, value):
        """Attempt to parse a string into a list using multiple strategies.

        Tries in order:
        1. json.loads (valid JSON arrays)
        2. ast.literal_eval (Python repr with single quotes, backslashes)
        3. Single-quote to double-quote replacement + json.loads
        4. Comma-separated split (non-bracketed strings only)
        5. Newline-separated split (if no commas)

        Returns:
            A list if parsing succeeded, or None if all strategies failed
        """
        if not isinstance(value, str):
            return None

        trimmed = value.strip()
        if not trimmed:
            return None

        looks_like_list = (trimmed.startswith('[') and trimmed.endswith(']'))
        looks_like_dict = (trimmed.startswith('{') and trimmed.endswith('}'))

        # Strategy 1: json.loads (fastest, handles valid JSON)
        if looks_like_list or looks_like_dict:
            try:
                parsed = json.loads(trimmed)
                if isinstance(parsed, list):
                    return parsed
                elif isinstance(parsed, dict):
                    items, _ = self._auto_detect_array(parsed)
                    return items if items else None
            except (json.JSONDecodeError, TypeError):
                pass

        # Strategy 2: ast.literal_eval (handles Python repr strings)
        if looks_like_list or (trimmed.startswith('(') and trimmed.endswith(')')):
            try:
                parsed = ast.literal_eval(trimmed)
                if isinstance(parsed, (list, tuple)):
                    return list(parsed)
            except (ValueError, SyntaxError, TypeError):
                pass

        # Strategy 3: Single-quote to double-quote replacement
        if looks_like_list:
            try:
                replaced = trimmed.replace("'", '"')
                parsed = json.loads(replaced)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass

        # Strategy 4: Comma-separated values (non-bracketed only)
        if not looks_like_list and not looks_like_dict and ',' in trimmed:
            items = [s.strip() for s in trimmed.split(',') if s.strip()]
            if len(items) > 1:
                return items

        # Strategy 5: Newline-separated values
        if not looks_like_list and not looks_like_dict and '\n' in trimmed and ',' not in trimmed:
            items = [s.strip() for s in trimmed.split('\n') if s.strip()]
            if len(items) > 1:
                return items

        return None

    def _auto_detect_array(self, data):
        """Auto-detect arrays in data

        Args:
            data: The data to search for arrays (can be list, dict, or JSON string)

        Returns:
            Tuple of (array, description)
        """
        # FIRST: If data is a string, try to parse it
        if isinstance(data, str):
            parsed_list = self._parse_string_to_list(data)
            if parsed_list is not None:
                return parsed_list, 'Parsed string to array'
            return [], 'String input (not a parseable array)'
        
        # Check if data itself is an array
        if isinstance(data, list):
            return data, 'Direct array input'
        
        # Check for Folder Selector pattern
        if isinstance(data, dict):
            if 'allFiles' in data and isinstance(data['allFiles'], list):
                return data['allFiles'], 'Folder Selector: allFiles'
            
            # Check for database results
            if 'results' in data and isinstance(data['results'], list):
                return data['results'], 'Database query results'
            
            # Check nested data
            if 'data' in data:
                nested = data['data']
                # If nested data is a JSON string, try to parse it
                if isinstance(nested, str):
                    trimmed_nested = nested.strip()
                    if trimmed_nested and ((trimmed_nested.startswith('[') and trimmed_nested.endswith(']')) or \
                                          (trimmed_nested.startswith('{') and trimmed_nested.endswith('}'))):
                        try:
                            parsed = json.loads(trimmed_nested)
                            if isinstance(parsed, list):
                                return parsed, 'Parsed data.data JSON string'
                        except (json.JSONDecodeError, TypeError):
                            pass
                elif isinstance(nested, list):
                    return nested, 'data property'
                elif isinstance(nested, dict):
                    if 'allFiles' in nested and isinstance(nested['allFiles'], list):
                        return nested['allFiles'], 'Nested Folder Selector: allFiles'
                    if 'results' in nested and isinstance(nested['results'], list):
                        return nested['results'], 'Nested results'
            
            # Check common array property names
            common_props = ['items', 'records', 'rows', 'documents', 'files', 
                        'list', 'array', 'collection']
            for prop in common_props:
                if prop in data and isinstance(data[prop], list):
                    return data[prop], f'Property: {prop}'
        
        return [], 'No array found'

    def _execute_end_loop_node(self, execution_id: str, node: Dict, variables: Dict) -> Dict:
        """Execute an End Loop node - enhance existing implementation"""
        node_id = node['id']
        node_config = node.get('config', {})
        
        # Find the associated loop node
        loop_node_id = node_config.get('loopNodeId')
        
        if not loop_node_id:
            # Auto-detect: find the Loop node that could reach this End Loop
            # Check if _active_loops exists (it should from your existing code)
            if hasattr(self, '_active_loops') and self._active_loops:
                # Get the most recent active loop
                loop_node_id = list(self._active_loops.keys())[-1] if self._active_loops else None
        
        # Check if we're inside a loop iteration
        if hasattr(self, '_active_loops') and loop_node_id in self._active_loops:
            # We're inside a loop - just return the data to continue
            loop_state = self._active_loops[loop_node_id]
            self.log_execution(
                execution_id, node_id, "debug",
                f"End Loop reached for iteration {loop_state['current_index'] + 1}/{loop_state['total_items']}")
            
            # Pass through the previous output
            prev_output = variables.get('_previousStepOutput', {})
            return {
                'success': True,
                'data': prev_output
            }
        
        # We're not in a loop - this means the loop has completed
        # Get the loop results
        loop_results = variables.get('_previousStepOutput', {})
        if execution_id in self._loop_results and loop_node_id in self._loop_results[execution_id]:
            loop_results = self._loop_results[execution_id][loop_node_id]
            # Clean up stored results
            del self._loop_results[execution_id][loop_node_id]
        
        # Log completion message if configured
        if node_config.get('completionMessage'):
            message = self._replace_variable_references(
                node_config['completionMessage'], variables)
            self.log_execution(
                execution_id, node_id, "info",
                f"Loop complete: {message}")
        
        # Continue with the accumulated loop results
        return {
            'success': True,
            'data': loop_results
        }
    

    def _cleanup_execution_resources(self, execution_id: str):
        """
        Clean up all resources associated with a completed/failed/cancelled execution
        
        Args:
            execution_id: The execution to clean up
        """
        try:
            # Remove from active executions
            if execution_id in self._active_executions:
                del self._active_executions[execution_id]
                logger.info(f"Removed execution {execution_id} from active executions")
            
            # Clean up the queue
            if execution_id in self._execution_queues:
                # Clear any remaining items in the queue
                try:
                    while not self._execution_queues[execution_id].empty():
                        self._execution_queues[execution_id].get_nowait()
                except queue.Empty:
                    pass
                del self._execution_queues[execution_id]
                logger.info(f"Cleaned up queue for execution {execution_id}")
            
            # Remove thread reference
            if execution_id in self._execution_threads:
                thread = self._execution_threads[execution_id]
                # Thread should already be finished, but check
                if thread.is_alive():
                    logger.warning(f"Thread for execution {execution_id} is still alive during cleanup")
                del self._execution_threads[execution_id]
                logger.info(f"Removed thread reference for execution {execution_id}")
            
            # Clean up loop tracking structures
            if hasattr(self, '_completed_loops') and execution_id in self._completed_loops:
                del self._completed_loops[execution_id]
            
            if hasattr(self, '_loop_results') and execution_id in self._loop_results:
                del self._loop_results[execution_id]
            
            # Clean up any active loops
            if hasattr(self, '_active_loops'):
                # Remove any loops associated with this execution
                keys_to_remove = [key for key in self._active_loops.keys() 
                                 if key.startswith(f"{execution_id}_")]
                for key in keys_to_remove:
                    del self._active_loops[key]
            
            logger.info(f"Successfully cleaned up all resources for execution {execution_id}")
            
        except Exception as e:
            logger.error(f"Error cleaning up execution {execution_id}: {str(e)}")

    def get_active_executions_count(self) -> int:
        """Get the count of currently active executions"""
        return len(self._active_executions)
    
    def get_active_execution_ids(self) -> list:
        """Get list of active execution IDs"""
        return list(self._active_executions.keys())
    
    def is_execution_active(self, execution_id: str) -> bool:
        """Check if an execution is still active in memory"""
        return execution_id in self._active_executions
    
    def cleanup_stale_executions(self):
        """
        Clean up any stale executions that may be stuck
        This should be called periodically or on startup
        """
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            # Get tenant context
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            # Find executions that are marked as Running/Paused but not in memory
            cursor.execute("""
                SELECT execution_id, status, started_at
                FROM WorkflowExecutions
                WHERE status IN ('Running', 'Paused')
                ORDER BY started_at DESC
            """)
            
            db_executions = cursor.fetchall()
            
            for row in db_executions:
                execution_id = row[0]
                status = row[1]
                started_at = row[2]
                
                # Check if this execution is in memory
                if not self.is_execution_active(execution_id):
                    # This execution is not in memory but marked as running/paused
                    # It's likely stuck from a previous run
                    logger.warning(f"Found stuck execution {execution_id} with status {status}")
                    
                    # Update it to Failed with explanation
                    cursor.execute("""
                        UPDATE WorkflowExecutions
                        SET status = 'Failed', 
                            completed_at = getutcdate(),
                            error_message = 'Execution was interrupted by application restart'
                        WHERE execution_id = ?
                    """, execution_id)
                    
                    # Log the cleanup
                    cursor.execute("""
                        INSERT INTO ExecutionLogs (
                            execution_id, timestamp, log_level, message
                        ) VALUES (?, getutcdate(), 'error', ?)
                    """, execution_id, "Execution marked as failed due to application restart")
                    
                    logger.info(f"Marked stuck execution {execution_id} as Failed")
            
            conn.commit()
            cursor.close()
            conn.close()
            
        except Exception as e:
            logger.error(f"Error cleaning up stale executions: {str(e)}")


    def _execute_application_node(self, execution_id: str, node: Dict, variables: Dict) -> Dict:
        """Execute an external application or script
        
        Args:
            execution_id: Unique ID of the workflow execution
            node: Node definition containing id, type, config, etc.
            variables: Current workflow variables
            
        Returns:
            Dictionary containing execution results with 'success' and 'data' keys
        """
        node_id = node['id']
        node_config = node.get('config', {})
        
        try:
            # Log the start of execution
            self.log_execution(
                execution_id, node_id, 'info',
                f"Starting application execution",
                {'node_config': node_config}
            )
            
            # Extract configuration with variable replacement
            command_type = node_config.get('commandType', 'command')
            executable_path = self._replace_variable_references(
                node_config.get('executablePath', ''), variables)
            arguments = self._replace_variable_references(
                node_config.get('arguments', ''), variables)
            working_directory = self._replace_variable_references(
                node_config.get('workingDirectory', ''), variables) or None
            environment_vars = self._replace_variable_references(
                node_config.get('environmentVars', ''), variables)
            
            # Parse numeric and boolean configs
            timeout = int(node_config.get('timeout', 300))
            capture_output = node_config.get('captureOutput', True)
            success_codes = node_config.get('successCodes', '0')
            fail_on_error = node_config.get('failOnError', True)
            continue_on_error = node_config.get('continueOnError', False)
            input_data_handling = node_config.get('inputDataHandling', 'none')
            output_parsing = node_config.get('outputParsing', 'text')
            output_regex = node_config.get('outputRegex', '.*')
            output_variable = node_config.get('outputVariable', '')
            
            # Security validation
            if not self._validate_app_execution_security(executable_path, command_type):
                raise ValueError(f"Security validation failed for: {executable_path}")
            
            # Build the command
            command = self._build_command(command_type, executable_path, arguments)
            
            # Prepare environment
            env = os.environ.copy()
            if environment_vars:
                for line in environment_vars.strip().split('\n'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        env[key.strip()] = value.strip()
            
            # Handle input data from previous step
            stdin_data = None
            temp_file = None
            
            prev_output = variables.get('_previousStepOutput', {})
            
            if input_data_handling == 'stdin' and prev_output:
                stdin_data = str(prev_output).encode('utf-8')
            elif input_data_handling == 'file' and prev_output:
                temp_file = self._create_temp_input_file(prev_output)
                command.append(temp_file.name)
            elif input_data_handling == 'args' and prev_output:
                if isinstance(prev_output, (list, tuple)):
                    command.extend([str(item) for item in prev_output])
                else:
                    command.append(str(prev_output))
            
            # Log the command being executed (sanitized)
            self.log_execution(
                execution_id, node_id, 'info',
                f"Executing command: {' '.join(command[:2])}...",  # Log only first parts for security
                {'command_type': command_type, 'timeout': timeout}
            )
            
            # Execute the command
            try:
                result = subprocess.run(
                    command,
                    capture_output=capture_output,
                    text=True,
                    timeout=timeout,
                    cwd=working_directory,
                    env=env,
                    stdin=subprocess.PIPE if stdin_data else None,
                    input=stdin_data.decode('utf-8') if stdin_data else None
                )
                
                # Check exit code
                success_codes_list = [int(code.strip()) for code in success_codes.split(',')]
                success = result.returncode in success_codes_list
                
                # Parse output
                output = self._parse_application_output(
                    result.stdout if capture_output else '', 
                    output_parsing, 
                    output_regex
                )
                
                # Log execution result
                self.log_execution(
                    execution_id, node_id,
                    'info' if success else 'error',
                    f"Application execution {'succeeded' if success else 'failed'}",
                    {
                        'exit_code': result.returncode,
                        'success': success,
                        'stdout_length': len(result.stdout) if capture_output else 0,
                        'stderr_length': len(result.stderr) if capture_output else 0
                    }
                )
                
                # Prepare result data
                result_data = {
                    'exit_code': result.returncode,
                    'output': output,
                    'stdout': result.stdout if capture_output else None,
                    'stderr': result.stderr if capture_output else None,
                    'command': executable_path  # Don't expose full command for security
                }
                
                # Save output to variable if configured
                if output_variable:
                    var_name = self._extract_variable_name(output_variable)
                    self._update_workflow_variable(
                        execution_id, var_name, 
                        self._determine_variable_type(output), 
                        output
                    )
                    variables[var_name] = output
                    
                    self.log_execution(
                        execution_id, node_id, "info",
                        f"Stored application output in variable: {var_name}"
                    )
                
                # Handle failure
                if not success:
                    error_msg = f"Application failed with exit code {result.returncode}"
                    if capture_output and result.stderr:
                        error_msg += f": {result.stderr[:500]}"  # Limit error message length
                    
                    if continue_on_error or not fail_on_error:
                        self.log_execution(
                            execution_id, node_id, "warning",
                            "Continuing workflow despite application error"
                        )
                        return {
                            'success': True,  # Allow workflow to continue
                            'data': result_data
                        }
                    else:
                        return {
                            'success': False,
                            'error': error_msg,
                            'data': result_data
                        }
                
                # Return success results
                return {
                    'success': True,
                    'data': result_data
                }
                
            except subprocess.TimeoutExpired:
                self.log_execution(
                    execution_id, node_id, 'error',
                    f"Application execution timed out after {timeout} seconds"
                )
                
                if continue_on_error or not fail_on_error:
                    return {
                        'success': True,  # Allow workflow to continue
                        'data': {
                            'error': f'Timeout after {timeout} seconds',
                            'exit_code': -1
                        }
                    }
                
                return {
                    'success': False,
                    'error': f'Timeout after {timeout} seconds',
                    'data': {'exit_code': -1}
                }
                
            finally:
                # Cleanup temp file if created
                if temp_file:
                    try:
                        os.unlink(temp_file.name)
                    except:
                        pass
                        
        except Exception as e:
            error_message = str(e)
            self.log_execution(
                execution_id, node_id, 'error',
                f"Error executing application: {error_message}",
                {'error_type': type(e).__name__}
            )
            
            if node_config.get('continueOnError', False):
                self.log_execution(
                    execution_id, node_id, "warning",
                    "Continuing workflow despite application error"
                )
                return {
                    'success': True,  # Allow workflow to continue
                    'data': {
                        'error': error_message,
                        'exit_code': -1
                    }
                }
            
            return {
                'success': False,
                'error': error_message,
                'data': {'exit_code': -1}
            }

    def _validate_app_execution_security(self, executable_path: str, command_type: str) -> bool:
        """Validate that the application execution is secure"""
        # Prevent path traversal
        if '..' in executable_path or executable_path.startswith('~'):
            logger.warning(f"Path traversal attempt blocked: {executable_path}")
            return False
        
        # Check against blacklist (customize based on your security requirements)
        blacklisted_commands = [
            'rm', 'del', 'format', 'fdisk', 'dd', 'shutdown', 'reboot',
            'kill', 'pkill', 'killall', 'systemctl', 'service'
        ]
        
        executable_name = os.path.basename(executable_path).lower()
        if any(cmd in executable_name for cmd in blacklisted_commands):
            logger.warning(f"Blacklisted command blocked: {executable_path}")
            return False
        
        # For scripts and executables, verify file exists
        if command_type in ['executable', 'script']:
            if not os.path.exists(executable_path):
                logger.warning(f"Executable not found: {executable_path}")
                return False
            
            if not os.access(executable_path, os.X_OK):
                logger.warning(f"File is not executable: {executable_path}")
                # For scripts, this might be OK as they'll be run with interpreter
                if command_type != 'script':
                    return False
        
        # Optional: Check against whitelist (uncomment and customize)
        # whitelisted_paths = ['/opt/approved_apps/', '/usr/local/bin/']
        # if not any(executable_path.startswith(path) for path in whitelisted_paths):
        #     logger.warning(f"Executable not in whitelist: {executable_path}")
        #     return False
        
        return True

    def _build_command(self, command_type: str, executable_path: str, 
                    arguments: str) -> List[str]:
        """Build the command array based on command type"""
        command = []
        
        if command_type == 'script':
            # Determine interpreter based on file extension
            ext = Path(executable_path).suffix.lower()
            interpreters = {
                '.py': ['python3'],
                '.sh': ['bash'],
                '.bash': ['bash'],
                '.js': ['node'],
                '.rb': ['ruby'],
                '.pl': ['perl'],
                '.ps1': ['pwsh', '-File'],
                '.r': ['Rscript']
            }
            
            if ext in interpreters:
                command.extend(interpreters[ext])
            
            command.append(executable_path)
            
        elif command_type == 'command':
            # Parse as shell command
            command = shlex.split(executable_path)
            
        else:  # executable
            command = [executable_path]
        
        # Add arguments
        if arguments:
            # Handle multi-line arguments (one per line) or space-separated
            if '\n' in arguments:
                args = [arg.strip() for arg in arguments.split('\n') if arg.strip()]
            else:
                args = shlex.split(arguments)
            command.extend(args)
        
        return command

    def _create_temp_input_file(self, input_data: Any) -> tempfile.NamedTemporaryFile:
        """Create a temporary file with input data"""
        temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.tmp')
        
        if isinstance(input_data, (dict, list)):
            json.dump(input_data, temp_file)
        else:
            temp_file.write(str(input_data))
        
        temp_file.close()
        return temp_file

    def _parse_application_output(self, output: str, parsing_type: str, 
                                regex_pattern: str = None) -> Any:
        """Parse the output based on the specified parsing type"""
        if not output:
            return None
            
        try:
            if parsing_type == 'json':
                return json.loads(output)
            elif parsing_type == 'csv':
                reader = csv.DictReader(output.splitlines())
                return list(reader)
            elif parsing_type == 'xml':
                # Basic XML handling - you might want to add xml.etree.ElementTree
                return output  # For now, return raw XML
            elif parsing_type == 'regex' and regex_pattern:
                matches = re.findall(regex_pattern, output, re.MULTILINE)
                return matches if matches else output
            else:  # text
                return output
        except Exception as e:
            logger.warning(f"Failed to parse output as {parsing_type}: {str(e)}")
            return output  # Return raw output if parsing fails
        
    def _execute_file_node(self, execution_id: str, node: Dict, variables: Dict) -> Dict:
        """Execute a File node in the workflow
        
        Args:
            execution_id: Unique ID of the workflow execution
            node: Node definition containing file operation config
            variables: Current workflow variables
            
        Returns:
            result: Dict with 'success', 'data', and optional 'error' keys
        """
        import os
        
        node_id = node['id']
        node_config = node.get('config', {})
        
        # Get the file operation
        operation = node_config.get('operation', 'read')
        
        # Log the start of file node execution
        self.log_execution(
            execution_id, node_id, "info", 
            f"Executing file node with operation: {operation}")
        
        try:
            # Get file path with variable replacement
            file_path = self._replace_variable_references(
                node_config.get('filePath', ''), variables)
            
            if not file_path:
                raise ValueError("File path is required")
            
            print(f'Executing file operation {operation}')
            logger.info(f'Executing file operation {operation}')
            
            # Execute the appropriate file operation
            if operation == 'read':
                result = self._file_read_operation(
                    execution_id, node_id, file_path, node_config, variables)
            elif operation == 'write':
                result = self._file_write_operation(
                    execution_id, node_id, file_path, node_config, variables)
            elif operation == 'append':
                result = self._file_append_operation(
                    execution_id, node_id, file_path, node_config, variables)
            elif operation == 'check':
                result = self._file_check_operation(
                    execution_id, node_id, file_path, node_config, variables)
            elif operation == 'delete':
                result = self._file_delete_operation(
                    execution_id, node_id, file_path, node_config, variables)
            elif operation == 'copy':
                result = self._file_copy_operation(
                    execution_id, node_id, file_path, node_config, variables)
            elif operation == 'move':
                result = self._file_move_operation(
                    execution_id, node_id, file_path, node_config, variables)
            else:
                raise ValueError(f"Unknown file operation: {operation}")
            
            # Handle output variable if configured
            # Default saveToVariable to true when outputVariable is present (backwards-compatible fix)
            has_output_var = bool(node_config.get('outputVariable', ''))
            save_to_var = node_config.get('saveToVariable', True if has_output_var else False)
            if result.get('success', False) and save_to_var:
                output_variable = node_config.get('outputVariable', '')
                if output_variable:
                    output_var = self._extract_variable_name(output_variable)
                    #output_value = result.get('data', {}).get('content') or result.get('data', {}).get('exists', False)

                    # Determine what value to store based on operation type
                    if operation == 'read':
                        # For read: store the file content
                        output_value = result.get('data', {}).get('content', '')
                    elif operation == 'check':
                        # For check: store true/false if file exists
                        output_value = result.get('data', {}).get('exists', False)
                    else:
                        # For write/append/delete/copy/move: store true on success
                        output_value = True
                    
                    # Update variable in database
                    self._update_workflow_variable(
                        execution_id, output_var, 
                        'string' if isinstance(output_value, str) else 'boolean', 
                        output_value)
                    
                    # Update in-memory variables
                    variables[output_var] = output_value
                    
                    self.log_execution(
                        execution_id, node_id, "info",
                        f"Stored file operation result in variable: {output_var}")
            
            return result
            
        except Exception as e:
            error_message = str(e)
            self.log_execution(
                execution_id, node_id, "error",
                f"File operation error: {error_message}")
            
            return {
                'success': False,
                'error': error_message,
                'data': {
                    'operation': operation,
                    'filePath': node_config.get('filePath', '')
                }
            }


    def _file_read_operation(self, execution_id: str, node_id: str, file_path: str, 
                            config: Dict, variables: Dict) -> Dict:
        """Read content from a file
        
        Args:
            execution_id: Workflow execution ID
            node_id: Node ID
            file_path: Path to the file to read
            config: Node configuration
            variables: Workflow variables
            
        Returns:
            Result dict with file content
        """
        import os
        
        try:
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"File not found: {file_path}")
            
            # Read file content
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            file_size = os.path.getsize(file_path)
            
            self.log_execution(
                execution_id, node_id, "info",
                f"Successfully read file: {file_path} ({file_size} bytes)")
            
            return {
                'success': True,
                'data': {
                    'operation': 'read',
                    'filePath': file_path,
                    'content': content,
                    'size': file_size
                }
            }
            
        except Exception as e:
            raise Exception(f"Failed to read file: {str(e)}")


    def _file_write_operation(self, execution_id: str, node_id: str, file_path: str, 
                            config: Dict, variables: Dict) -> Dict:
        """Write content to a file (overwrites existing content)
        
        Args:
            execution_id: Workflow execution ID
            node_id: Node ID
            file_path: Path to the file to write
            config: Node configuration
            variables: Workflow variables
            
        Returns:
            Result dict with operation status
        """
        import os
        
        try:
            # Get content from configured source
            content = self._get_file_content_from_source(
                execution_id, node_id, config, variables)
            
            # Create directory if it doesn't exist
            directory = os.path.dirname(file_path)
            if directory and not os.path.exists(directory):
                os.makedirs(directory)
            
            # Write content to file
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            bytes_written = len(content.encode('utf-8'))
            
            self.log_execution(
                execution_id, node_id, "info",
                f"Successfully wrote to file: {file_path} ({bytes_written} bytes)")
            
            return {
                'success': True,
                'data': {
                    'operation': 'write',
                    'filePath': file_path,
                    'bytesWritten': bytes_written
                }
            }
            
        except Exception as e:
            raise Exception(f"Failed to write file: {str(e)}")


    def _file_append_operation(self, execution_id: str, node_id: str, file_path: str, 
                            config: Dict, variables: Dict) -> Dict:
        """Append content to a file
        
        Args:
            execution_id: Workflow execution ID
            node_id: Node ID
            file_path: Path to the file to append to
            config: Node configuration
            variables: Workflow variables
            
        Returns:
            Result dict with operation status
        """
        import os
        
        try:
            # Get content from configured source
            content = self._get_file_content_from_source(
                execution_id, node_id, config, variables)
            
            # Create directory if it doesn't exist
            directory = os.path.dirname(file_path)
            if directory and not os.path.exists(directory):
                os.makedirs(directory)
            
            # Append content to file
            with open(file_path, 'a', encoding='utf-8') as f:
                f.write(content)
            
            bytes_written = len(content.encode('utf-8'))
            
            self.log_execution(
                execution_id, node_id, "info",
                f"Successfully appended to file: {file_path} ({bytes_written} bytes)")
            
            return {
                'success': True,
                'data': {
                    'operation': 'append',
                    'filePath': file_path,
                    'bytesWritten': bytes_written
                }
            }
            
        except Exception as e:
            raise Exception(f"Failed to append to file: {str(e)}")


    def _file_check_operation(self, execution_id: str, node_id: str, file_path: str, 
                            config: Dict, variables: Dict) -> Dict:
        """Check if a file exists
        
        Args:
            execution_id: Workflow execution ID
            node_id: Node ID
            file_path: Path to the file to check
            config: Node configuration
            variables: Workflow variables
            
        Returns:
            Result dict with existence status
        """
        import os
        
        try:
            exists = os.path.exists(file_path)
            
            self.log_execution(
                execution_id, node_id, "info",
                f"File existence check for {file_path}: {exists}")
            
            return {
                'success': True,
                'data': {
                    'operation': 'check',
                    'filePath': file_path,
                    'exists': exists
                }
            }
            
        except Exception as e:
            raise Exception(f"Failed to check file: {str(e)}")


    def _file_delete_operation(self, execution_id: str, node_id: str, file_path: str, 
                            config: Dict, variables: Dict) -> Dict:
        """Delete a file
        
        Args:
            execution_id: Workflow execution ID
            node_id: Node ID
            file_path: Path to the file to delete
            config: Node configuration
            variables: Workflow variables
            
        Returns:
            Result dict with operation status
        """
        import os
        
        try:
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"File not found: {file_path}")
            
            # Delete the file
            os.remove(file_path)
            
            self.log_execution(
                execution_id, node_id, "info",
                f"Successfully deleted file: {file_path}")
            
            return {
                'success': True,
                'data': {
                    'operation': 'delete',
                    'filePath': file_path,
                    'deleted': True
                }
            }
            
        except Exception as e:
            raise Exception(f"Failed to delete file: {str(e)}")


    def _file_copy_operation(self, execution_id: str, node_id: str, file_path: str, 
                            config: Dict, variables: Dict) -> Dict:
        """Copy a file to a new location
        
        Args:
            execution_id: Workflow execution ID
            node_id: Node ID
            file_path: Path to the source file
            config: Node configuration (should contain 'destinationPath')
            variables: Workflow variables
            
        Returns:
            Result dict with operation status
        """
        import os
        import shutil
        
        try:
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"Source file not found: {file_path}")
            
            # Get destination path with variable replacement
            destination_path = self._replace_variable_references(
                config.get('destinationPath', ''), variables)
            
            if not destination_path:
                raise ValueError("Destination path is required for copy operation")
            
            # Create destination directory if it doesn't exist
            dest_directory = os.path.dirname(destination_path)
            if dest_directory and not os.path.exists(dest_directory):
                os.makedirs(dest_directory)
            
            # Copy the file
            shutil.copy2(file_path, destination_path)
            
            self.log_execution(
                execution_id, node_id, "info",
                f"Successfully copied file from {file_path} to {destination_path}")
            
            return {
                'success': True,
                'data': {
                    'operation': 'copy',
                    'sourcePath': file_path,
                    'destinationPath': destination_path,
                    'copied': True
                }
            }
            
        except Exception as e:
            raise Exception(f"Failed to copy file: {str(e)}")


    def _file_move_operation(self, execution_id: str, node_id: str, file_path: str, 
                            config: Dict, variables: Dict) -> Dict:
        """Move a file to a new location
        
        Args:
            execution_id: Workflow execution ID
            node_id: Node ID
            file_path: Path to the source file
            config: Node configuration (should contain 'destinationPath')
            variables: Workflow variables
            
        Returns:
            Result dict with operation status
        """
        import os
        import shutil
        
        try:
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"Source file not found: {file_path}")
            
            # Get destination path with variable replacement
            destination_path = self._replace_variable_references(
                config.get('destinationPath', ''), variables)
            
            if not destination_path:
                raise ValueError("Destination path is required for move operation")
            
            # Create destination directory if it doesn't exist
            dest_directory = os.path.dirname(destination_path)
            if dest_directory and not os.path.exists(dest_directory):
                os.makedirs(dest_directory)
            
            # Move the file
            shutil.move(file_path, destination_path)
            
            self.log_execution(
                execution_id, node_id, "info",
                f"Successfully moved file from {file_path} to {destination_path}")
            
            return {
                'success': True,
                'data': {
                    'operation': 'move',
                    'sourcePath': file_path,
                    'destinationPath': destination_path,
                    'moved': True
                }
            }
            
        except Exception as e:
            raise Exception(f"Failed to move file: {str(e)}")


    def _get_file_content_from_source(self, execution_id: str, node_id: str, 
                                    config: Dict, variables: Dict) -> str:
        """Get file content from the configured source
        
        Args:
            execution_id: Workflow execution ID
            node_id: Node ID
            config: Node configuration containing content source settings
            variables: Workflow variables
            
        Returns:
            Content as string
        """
        #import json
        
        content_source = config.get('contentSource', 'direct')
        
        if content_source == 'direct':
            # Direct content input
            content = self._replace_variable_references(
                config.get('content', ''), variables)
            
        elif content_source == 'variable':
            # Get from workflow variable
            var_name = config.get('contentVariable', '')
            var_name = self._extract_variable_name(var_name)
            
            if var_name not in variables:
                raise ValueError(f"Variable '{var_name}' not found")
            
            content = variables[var_name]
            
            # Convert objects/arrays to JSON strings
            if not isinstance(content, str):
                content = json.dumps(content, indent=2)
                
        elif content_source == 'previous':
            # Get from previous step output
            prev_data = variables.get('_previousStepOutput', {})
            content_path = config.get('contentPath', '')
            
            if content_path:
                # Navigate the path to get nested value
                content = self._get_nested_value(prev_data, content_path)
            else:
                content = prev_data
            
            if content is None:
                raise ValueError(f"Path '{content_path}' not found in previous step output")
            
            # Convert objects/arrays to JSON strings
            if not isinstance(content, str):
                content = json.dumps(content, indent=2)
        else:
            raise ValueError(f"Unknown content source: {content_source}")
        
        return str(content)


    def _get_nested_value(self, obj: any, path: str) -> any:
        """Get a nested value from an object using dot notation
        
        Args:
            obj: Object to navigate
            path: Dot-separated path (e.g., 'data.results.0.value')
            
        Returns:
            The value at the path, or None if not found
        """
        if not path:
            return obj
        
        #import re
        
        keys = path.split('.')
        result = obj
        
        for key in keys:
            if result is None:
                return None
            
            # Handle array indices in the path (e.g., "results[0]")
            match = re.match(r'^([^\[]+)(?:\[(\d+)\])?$', key)
            if match:
                key_name = match.group(1)
                index = match.group(2)
                
                # Navigate to the key
                if isinstance(result, dict):
                    result = result.get(key_name)
                else:
                    return None
                
                # Navigate to the array index if present
                if index is not None and result is not None:
                    try:
                        result = result[int(index)]
                    except (IndexError, KeyError, TypeError):
                        return None
            else:
                # Simple key access
                if isinstance(result, dict):
                    result = result.get(key)
                else:
                    return None
        
        return result
