import json
import logging
from logging.handlers import WatchedFileHandler
import os
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

from langchain_openai import AzureChatOpenAI, ChatOpenAI
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage, HumanMessage
from langchain_classic.agents.format_scratchpad import format_to_tool_messages
from langchain_classic.agents.output_parsers import ToolsAgentOutputParser
from langchain_classic.agents import AgentExecutor

import requests

from AppUtils import get_db_connection, azureQuickPrompt
import config as cfg
from CommonUtils import rotate_logs_on_startup, get_all_node_details, get_node_details, get_log_path, get_base_url
from role_decorators import get_internal_api_key
import system_prompts as sysprompts

# Import CommandGenerator for two-stage architecture
if getattr(cfg, 'USE_TWO_STAGE_ARCHITECTURE', False):
    try:
        from CommandGenerator import CommandGenerator
    except ImportError:
        CommandGenerator = None
else:
    CommandGenerator = None

rotate_logs_on_startup(os.getenv('WORKFLOW_AGENT_LOG', get_log_path('workflow_agent_log.txt')))

# Configure logging
logger = logging.getLogger("WorkflowAgent")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=os.getenv('WORKFLOW_AGENT_LOG', get_log_path('workflow_agent_log.txt')), encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)


class BuilderPhase(Enum):
    """Phases of the workflow building process"""
    DISCOVERY = "discovery"
    REQUIREMENTS = "requirements"
    PLANNING = "planning"
    BUILDING = "building"
    REFINEMENT = "refinement"

@dataclass
class WorkflowRequirements:
    """Structure to hold gathered requirements"""
    process_name: Optional[str] = None
    process_description: Optional[str] = None
    trigger_type: Optional[str] = None
    trigger_details: Dict = field(default_factory=dict)
    data_sources: List[Dict] = field(default_factory=list)
    decision_points: List[Dict] = field(default_factory=list)
    stakeholders: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    systems_involved: List[str] = field(default_factory=list)
    approval_levels: List[Dict] = field(default_factory=list)
    
    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            'process_name': self.process_name,
            'process_description': self.process_description,
            'trigger_type': self.trigger_type,
            'trigger_details': self.trigger_details,
            'data_sources': self.data_sources,
            'decision_points': self.decision_points,
            'stakeholders': self.stakeholders,
            'outputs': self.outputs,
            'constraints': self.constraints,
            'systems_involved': self.systems_involved,
            'approval_levels': self.approval_levels
        }

class WorkflowAgent:
    """
    System-level agent for guided workflow creation.
    Does not require database metadata or agent_id.
    """
    
    def __init__(self, session_id: str = None, workflow_state: Dict = None, is_builder_delegation: bool = False):
        """Initialize the workflow agent"""
        self.session_id = session_id or str(datetime.now().timestamp())
        self.phase = BuilderPhase.DISCOVERY
        self.requirements = WorkflowRequirements()
        self.workflow_plan = None
        self.generated_commands = None
        self.conversation_context = []
        self.chat_history = []
        self.tools = []
        self.workflow_state = workflow_state
        self.current_json_commands = None
        self.original_workflow_plan = None   # First plan before any fixes (NOTE: For future use)
        self.accumulated_commands = []       # All commands (initial + fixes) (NOTE: For future use)
        self.is_validation_fix = False       # Flag set by frontend
        self.is_builder_delegation = is_builder_delegation  # Skip Q&A, go straight to building

        # Builder delegation: skip DISCOVERY/REQUIREMENTS, start in PLANNING
        if is_builder_delegation:
            self.phase = BuilderPhase.PLANNING
            logger.info(f"Builder delegation mode — starting in PLANNING phase (skip DISCOVERY/REQUIREMENTS)")

        # Initialize command generator for two-stage architecture
        self.use_two_stage = getattr(cfg, 'USE_TWO_STAGE_ARCHITECTURE', False)
        self.command_generator = None
        if self.use_two_stage and CommandGenerator:
            try:
                self.command_generator = CommandGenerator()
                logger.info("Two-stage architecture enabled - using CommandGenerator")
            except Exception as e:
                logger.warning(f"Failed to initialize CommandGenerator, falling back to single-stage: {e}")
                self.use_two_stage = False

        # Auto-detect if we should start in refinement mode (overrides builder delegation for edits)
        if workflow_state and self._has_existing_workflow(workflow_state):
            self.phase = BuilderPhase.REFINEMENT
            self._extract_requirements_from_workflow(workflow_state)
            logger.info(f"Starting in REFINEMENT mode with {len(workflow_state.get('nodes', []))} existing nodes")
            
            # Store refine greeting
            self._add_initial_greeting(workflow_state)
        else:
            # Store build greeting
            self._add_initial_greeting(None)

        # Initialize LLM
        self._initialize_llm()
        
        # Set up system prompt
        self._set_system_prompt()
        
        # Register tools
        self._register_tools()
        
        # Build agent executor
        self._build_agent_executor()
        
        logger.info(f"Initialized WorkflowAgent for session {self.session_id}")
    
    def _initialize_llm(self):
        """Initialize the OpenAI LLM (supports BYOK, direct OpenAI, and Azure)."""
        from api_keys_config import get_openai_config

        config = get_openai_config(use_alternate_api=False)

        reasoning_effort = config.get('reasoning_effort')
        # Builder delegations use lower temperature for deterministic output
        if self.is_builder_delegation:
            temperature = 0.4
        else:
            temperature = 1.0 if reasoning_effort else 0.7

        if config['api_type'] == 'open_ai':
            self.llm = ChatOpenAI(
                model=config['model'],
                api_key=config['api_key'],
                temperature=temperature,
                max_tokens=16192,
                reasoning_effort=reasoning_effort,
                streaming=False,
            )
        else:
            self.llm = AzureChatOpenAI(
                azure_deployment=config['deployment_id'],
                model=config['deployment_id'],
                api_version=config['api_version'],
                azure_endpoint=config['api_base'],
                api_key=config['api_key'],
                temperature=temperature,
                max_tokens=16192,
                reasoning_effort=reasoning_effort,
                streaming=False,
            )

    def _has_existing_workflow(self, workflow_state: Dict) -> bool:
        """Check if workflow state contains actual workflow nodes"""
        if not workflow_state:
            return False
        
        nodes = workflow_state.get('nodes', [])
        return len(nodes) > 0
    
    def _add_initial_greeting(self, workflow_state: Dict = None):
        """Add the initial greeting to conversation context"""
        if workflow_state and self._has_existing_workflow(workflow_state):
            nodes = workflow_state.get('nodes', [])
            node_types = {}
            for node in nodes:
                node_type = node.get('type', 'unknown')
                node_types[node_type] = node_types.get(node_type, 0) + 1
            
            node_summary = ', '.join([f"{count} {ntype}" for ntype, count in node_types.items()])
            workflow_name = workflow_state.get('name', 'your workflow')
            
            greeting = (
                f"Hi! I can see you have an existing workflow \"{workflow_name}\" "
                f"with {len(nodes)} nodes ({node_summary}).\n\n"
                f"What would you like to change or improve?"
            )
        else:
            greeting = (
                "Hi! I'm here to help you build a workflow that automates your business process. "
                "Let's start by understanding what you'd like to automate.\n\n"
                "What process or task are you looking to streamline?"
            )
        
        self.conversation_context.append({
            "role": "assistant",
            "content": greeting
        })

    def _extract_requirements_from_workflow(self, workflow_state: Dict):
        """Extract requirements from existing workflow structure"""
        if not workflow_state:
            return
        
        nodes = workflow_state.get('nodes', [])
        connections = workflow_state.get('connections', [])
        
        # Infer process name from workflow name if available
        workflow_name = workflow_state.get('name', workflow_state.get('workflow_name'))
        if workflow_name:
            self.requirements.process_name = workflow_name
            self.requirements.process_description = f"Existing workflow: {workflow_name}"
        
        # Analyze node types to understand the workflow
        node_types = [node.get('type') for node in nodes]
        
        # Detect data sources (Database nodes)
        db_nodes = [n for n in nodes if n.get('type') == 'Database']
        if db_nodes:
            self.requirements.data_sources = [
                {'type': 'database', 'description': node.get('label', 'Database operation')}
                for node in db_nodes
            ]
        
        # Detect decision points (Conditional nodes)
        cond_nodes = [n for n in nodes if n.get('type') == 'Conditional']
        if cond_nodes:
            self.requirements.decision_points = [
                {'description': node.get('label', 'Decision point')}
                for node in cond_nodes
            ]
        
        # Detect approval requirements (Human Approval nodes)
        approval_nodes = [n for n in nodes if n.get('type') == 'Human Approval']
        if approval_nodes:
            self.requirements.approval_levels = [
                {'description': node.get('label', 'Approval required')}
                for node in approval_nodes
            ]
        
        # Detect outputs (Alert/File nodes)
        output_nodes = [n for n in nodes if n.get('type') in ['Alert', 'File', 'Document']]
        if output_nodes:
            self.requirements.outputs = [
                node.get('label', 'Output') for node in output_nodes
            ]
        
        # Detect AI usage
        ai_nodes = [n for n in nodes if n.get('type') == 'AI Action']
        if ai_nodes:
            if 'AI Processing' not in self.requirements.systems_involved:
                self.requirements.systems_involved.append('AI Processing')
        
        # Detect loops
        loop_nodes = [n for n in nodes if n.get('type') in ['Loop', 'End Loop', 'Start Loop']]
        if loop_nodes:
            if 'Iteration/Loops' not in self.requirements.systems_involved:
                self.requirements.systems_involved.append('Iteration/Loops')
        
        logger.info(f"Extracted requirements from workflow: {len(nodes)} nodes, {len(connections)} connections")
    

    def _escape_curly(self, text: str) -> str:
        return text.replace("{", "{{").replace("}", "}}")

    
    def _format_workflow_state(self) -> str:
        """Format workflow state for inclusion in prompts with comprehensive node details"""
        if not self.workflow_state:
            return "No existing workflow."
        
        nodes = self.workflow_state.get('nodes', [])
        connections = self.workflow_state.get('connections', [])
        workflow_name = self.workflow_state.get('name', 'Untitled Workflow')  # This value is not in the JSON config
        variables = self.workflow_state.get('variables', {})
        
        output = []
        output.append(f"\n{'='*70}")
        # output.append(f"CURRENT WORKFLOW: {workflow_name}")
        # output.append(f"{'='*70}")
        output.append(f"Total: {len(nodes)} nodes, {len(connections)} connections\n")
        
        # List all nodes with detailed configuration
        output.append("EXISTING NODES:")
        output.append("-" * 70)
        
        for i, node in enumerate(nodes, 1):
            node_id = node.get('id')
            node_type = node.get('type')
            label = node.get('label')
            is_start = node.get('isStart', False)
            config = node.get('config', {})
            
            output.append(f"\n{i}. NODE ID: {node_id}{' [START NODE]' if is_start else ''}")
            output.append(f"   Type: {node_type}")
            output.append(f"   Label: '{label}'")
            
            # Format configuration based on node type
            if config:
                output.append("   Configuration:")
                
                if node_type == 'Database':
                    output.append(f"      • DB Connection: {config.get('dbConnection', 'Not set')}")
                    output.append(f"      • Operation: {config.get('dbOperation', 'Not set')}")
                    if 'query' in config:
                        query = config['query']
                        # Truncate long queries
                        if len(query) > 100:
                            query = query[:97] + "..."
                        output.append(f"      • Query: {query}")
                    # WRAP IN BACKTICKS
                    output.append(f"      • Output Variable: `{config.get('outputVariable', 'None')}`")
                    output.append(f"      • Save to Variable: {config.get('saveToVariable', 'false')}")
                
                elif node_type == 'AI Action':
                    output.append(f"      • Agent ID: {config.get('agent_id', 'Not set')}")
                    if 'prompt' in config:
                        prompt = config['prompt']
                        if len(prompt) > 100:
                            prompt = prompt[:97] + "..."
                        output.append(f"      • Prompt: {prompt}")
                    # WRAP IN BACKTICKS
                    output.append(f"      • Output Variable: `{config.get('outputVariable', 'None')}`")
                    output.append(f"      • Continue on Error: {config.get('continueOnError', 'false')}")
                
                elif node_type == 'AI Extract':
                    output.append(f"      • Input Source: {config.get('inputSource', 'auto')}")
                    output.append(f"      • Input Variable: `{config.get('inputVariable', 'Not set')}`")
                    output.append(f"      • Output Variable: `{config.get('outputVariable', 'Not set')}`")
                    output.append(f"      • Fail on Missing Required: {config.get('failOnMissingRequired', 'false')}")
                    if config.get('specialInstructions'):
                        instructions = config['specialInstructions']
                        if len(instructions) > 80:
                            instructions = instructions[:77] + "..."
                        output.append(f"      • Special Instructions: {instructions}")
                    # Show fields summary
                    fields = config.get('fields', [])
                    if fields:
                        field_names = [f.get('name', '?') for f in fields[:5]]
                        fields_str = ', '.join(field_names)
                        if len(fields) > 5:
                            fields_str += f", ... (+{len(fields) - 5} more)"
                        output.append(f"      • Fields ({len(fields)}): {fields_str}")
                    # Excel output options (if configured)
                    output_dest = config.get('outputDestination', 'variable')
                    if output_dest != 'variable':
                        output.append(f"      • Output Destination: {output_dest}")
                        if config.get('excelOutputPath'):
                            output.append(f"      • Excel Output Path: {config.get('excelOutputPath')}")
                        if config.get('excelTemplatePath'):
                            output.append(f"      • Excel Template Path: {config.get('excelTemplatePath')}")
                        if config.get('excelSheetName'):
                            output.append(f"      • Excel Sheet Name: {config.get('excelSheetName')}")
                        output.append(f"      • Mapping Mode: {config.get('mappingMode', 'ai')}")
                
                elif node_type == 'Excel Export':
                    output.append(f"      • Input Variable: `{config.get('inputVariable', 'Not set')}`")
                    output.append(f"      • Flatten Array: {config.get('flattenArray', 'false')}")
                    if config.get('carryForwardFields'):
                        output.append(f"      • Carry-Forward Fields: {config.get('carryForwardFields')}")
                    if config.get('manualFields'):
                        output.append(f"      • Manual Fields: {config.get('manualFields')}")
                    output.append(f"      • Excel Operation: {config.get('excelOperation', 'append')}")
                    output.append(f"      • Excel Output Path: {config.get('excelOutputPath', 'Not set')}")
                    if config.get('excelTemplatePath'):
                        output.append(f"      • Excel Template Path: {config.get('excelTemplatePath')}")
                    if config.get('excelSheetName'):
                        output.append(f"      • Excel Sheet Name: {config.get('excelSheetName')}")
                    output.append(f"      • Mapping Mode: {config.get('mappingMode', 'ai')}")
                    if config.get('aiMappingInstructions'):
                        instructions = config['aiMappingInstructions']
                        if len(instructions) > 80:
                            instructions = instructions[:77] + "..."
                        output.append(f"      • AI Mapping Instructions: {instructions}")
                    if config.get('fieldMapping'):
                        field_mapping = config['fieldMapping']
                        if isinstance(field_mapping, dict):
                            mapping_preview = ', '.join([f"{k}→{v}" for k, v in list(field_mapping.items())[:3]])
                            if len(field_mapping) > 3:
                                mapping_preview += f", ... (+{len(field_mapping) - 3} more)"
                            output.append(f"      • Field Mapping: {mapping_preview}")
                        else:
                            output.append(f"      • Field Mapping: {field_mapping}")
                    
                    # UPDATE operation specific settings
                    if config.get('excelOperation') == 'update':
                        output.append(f"      • Key Columns: {config.get('keyColumns', 'Not set')}")
                        
                        # AI Key Matching
                        if config.get('useAIKeyMatching'):
                            output.append(f"      • AI Key Matching: Enabled")
                            if config.get('aiKeyMatchingInstructions'):
                                instructions = config['aiKeyMatchingInstructions']
                                if len(instructions) > 80:
                                    instructions = instructions[:77] + "..."
                                output.append(f"        - Instructions: {instructions}")
                        else:
                            output.append(f"      • AI Key Matching: Disabled")
                        
                        # Smart Change Detection
                        if config.get('useSmartChangeDetection'):
                            strictness = config.get('smartChangeStrictness', 'strict')
                            output.append(f"      • Smart Change Detection: Enabled ({strictness} mode)")
                        else:
                            output.append(f"      • Smart Change Detection: Disabled")
                        
                        # Change Tracking options
                        output.append(f"      • Highlight Changes: {config.get('highlightChanges', True)}")
                        output.append(f"      • Track Deleted Rows: {config.get('trackDeletedRows', False)}")
                        if config.get('addChangeTimestamp', True):
                            output.append(f"      • Timestamp Column: {config.get('timestampColumn', 'Last Updated')}")
                        if config.get('changeLogSheet'):
                            output.append(f"      • Change Log Sheet: {config.get('changeLogSheet')}")
                
                elif node_type == 'Document':
                    output.append(f"      • Action: {config.get('documentAction', 'Not set')}")
                    output.append(f"      • Source Type: {config.get('sourceType', 'Not set')}")
                    output.append(f"      • Source Path: `{config.get('sourcePath', 'Not set')}`")
                    output.append(f"      • Output Type: {config.get('outputType', 'Not set')}")
                    # WRAP IN BACKTICKS
                    output.append(f"      • Output Path: `{config.get('outputPath', 'Not set')}`")
                    output.append(f"      • Output Format: {config.get('outputFormat', 'Not set')}")
                    output.append(f"      • Force AI Extraction: {config.get('forceAiExtraction', 'false')}")
                
                elif node_type == 'Loop':
                    output.append(f"      • Source Type: {config.get('sourceType', 'auto')}")
                    output.append(f"      • Loop Source: {config.get('loopSource', 'Not set')}")
                    # WRAP IN BACKTICKS
                    output.append(f"      • Item Variable: `{config.get('itemVariable', 'Not set')}`")
                    output.append(f"      • Index Variable: `{config.get('indexVariable', 'Not set')}`")
                    output.append(f"      • Max Iterations: {config.get('maxIterations', 'Not set')}")
                
                elif node_type == 'End Loop':
                    output.append(f"      • Loop Node ID: {config.get('loopNodeId', 'Not set')}")
                
                elif node_type == 'Conditional':
                    output.append(f"      • Condition Type: {config.get('conditionType', 'comparison')}")
                    output.append(f"      • Left Value: {config.get('leftValue', 'Not set')}")
                    output.append(f"      • Operator: {config.get('operator', 'Not set')}")
                    output.append(f"      • Right Value: {config.get('rightValue', 'Not set')}")
                    # Show the complete condition
                    left = config.get('leftValue', '?')
                    op = config.get('operator', '?')
                    right = config.get('rightValue', '?')
                    output.append(f"      • Condition: {left} {op} {right}")
                
                elif node_type == 'Human Approval':
                    output.append(f"      • Assignee Type: {config.get('assigneeType', 'unassigned')}")
                    output.append(f"      • Assignee ID: {config.get('assigneeId', 'None')}")
                    output.append(f"      • Title: {config.get('approvalTitle', 'Not set')}")
                    if 'approvalDescription' in config:
                        desc = config['approvalDescription']
                        if len(desc) > 80:
                            desc = desc[:77] + "..."
                        output.append(f"      • Description: {desc}")
                    # WRAP IN BACKTICKS
                    output.append(f"      • Approval Data: `{config.get('approvalData', 'None')}`")
                    output.append(f"      • Timeout (minutes): {config.get('timeoutMinutes', 'Not set')}")
                
                elif node_type == 'Alert':
                    output.append(f"      • Alert Type: {config.get('alertType', 'email')}")
                    output.append(f"      • Recipients: {config.get('recipients', 'Not set')}")
                    output.append(f"      • Subject: {config.get('subject', 'Not set')}")
                    if 'messageTemplate' in config:
                        msg = config['messageTemplate']
                        if len(msg) > 80:
                            msg = msg[:77] + "..."
                        output.append(f"      • Message: {msg}")
                
                elif node_type == 'Folder Selector':
                    output.append(f"      • Folder Path: {config.get('folderPath', 'Not set')}")
                    output.append(f"      • Selection Mode: {config.get('selectionMode', 'first')}")
                    output.append(f"      • File Pattern: {config.get('filePattern', '*.*')}")
                    # WRAP IN BACKTICKS
                    output.append(f"      • Output Variable: `{config.get('outputVariable', 'Not set')}`")
                    output.append(f"      • Fail if Empty: {config.get('failIfEmpty', 'false')}")
                
                elif node_type == 'File':
                    output.append(f"      • Operation: {config.get('operation', 'Not set')}")
                    output.append(f"      • File Path: {config.get('filePath', 'Not set')}")
                    # WRAP IN BACKTICKS
                    output.append(f"      • Output Variable: `{config.get('outputVariable', 'None')}`")
                    if 'content' in config:
                        content = config['content']
                        if len(content) > 80:
                            content = content[:77] + "..."
                        output.append(f"      • Content: {content}")
                
                elif node_type == 'Set Variable':
                    # WRAP IN BACKTICKS
                    output.append(f"      • Variable Name: `{config.get('variableName', 'Not set')}`")
                    output.append(f"      • Value Source: {config.get('valueSource', 'direct')}")
                    output.append(f"      • Value Expression: {config.get('valueExpression', 'Not set')}")
                
                elif node_type == 'Execute Application':
                    output.append(f"      • Application Path: {config.get('applicationPath', 'Not set')}")
                    output.append(f"      • Arguments: {config.get('arguments', 'None')}")
                    output.append(f"      • Wait for Completion: {config.get('waitForCompletion', 'true')}")
                    # WRAP IN BACKTICKS
                    output.append(f"      • Output Variable: `{config.get('outputVariable', 'None')}`")
                
                else:
                    # Generic config display for unknown node types
                    for key, value in config.items():
                        if isinstance(value, str) and len(value) > 80:
                            value = value[:77] + "..."
                        output.append(f"      • {key}: {value}")
        
        # List all connections
        output.append(f"\n{'-'*70}")
        output.append("CONNECTIONS:")
        output.append("-" * 70)
        
        logger.debug(f"DEBUGGING OUTPUT OF NODES:\n{nodes}")
        logger.debug(f"DEBUGGING OUTPUT OF CONNECTIONS:\n{connections}")
        #print(f"DEBUGGING OUTPUT OF CONNECTIONS:\n{connections}")
        if connections:
            for i, conn in enumerate(connections, 1):
                
                source = conn.get('from') if conn.get('from') else conn.get('source')
                target = conn.get('to') if conn.get('to') else conn.get('target')
                conn_type = conn.get('type', 'pass')
                
                # Find node labels for better readability
                source_label = next((n.get('label') for n in nodes if n.get('id') == source), source)
                target_label = next((n.get('label') for n in nodes if n.get('id') == target), target)
                
                # Format connection type with visual indicator
                type_indicator = {
                    'pass': '-[PASS]->',
                    'fail': '-[FAIL]->',
                    'complete': '-[COMPLETE]->'
                }.get(conn_type, f'-[{conn_type.upper()}]->')
                
                output.append(f"{i}. {source} ({source_label}) {type_indicator} {target} ({target_label})")
        else:
            output.append("   No connections defined")
        
        output.append(f"\n{'='*70}\n")
        output_str = "\n".join(output)
        output_str = self._escape_curly(output_str)
        #output_str = output_str.replace('${', '${{').replace('}', '}}')
        return output_str

    def _set_system_prompt(self):
        """Set the system prompt based on current phase"""
        
        # Workflow command documentation without ANY JSON syntax to avoid template issues
        workflow_command_docs = """
NODE TYPES AND THEIR CONFIGURATIONS:

{node_types_doc}

REQUIRED ID LOOKUPS:
Your workflow plan will be sent to a Command Generator that CANNOT look up IDs. You MUST look up and include these IDs in your plan:
- Database nodes: Use get_available_database_connections tool → include "connection ID X" in your plan.
  After identifying the connection, use get_database_schema to discover available tables and columns.
  Use this schema information to construct appropriate SQL queries for Database nodes.
  Do NOT ask the user for table names, column names, or SQL queries — discover them from the schema.
- AI Action nodes: Use get_available_ai_agents tool → include "agent ID X" in your plan
- Human Approval nodes: Use get_available_users or get_available_groups tool → include "user ID X" or "group ID X" in your plan
If you do not include these IDs, the workflow cannot be built correctly.
"""
        
        base_prompt = """You are an intelligent Workflow Automation Assistant helping users build workflows through natural conversation.

{workflow_docs}

Your approach adapts based on the current phase of the process. You have deep knowledge of business processes across all industries and can guide users from vague ideas to concrete workflows.

YOUR GOAL:
Your primary output is a detailed WORKFLOW PLAN - a numbered list of steps describing each node, its purpose, and its configuration details. This plan is then sent to a separate Command Generator AI that converts it into executable workflow commands. The Command Generator has NO access to tools and can only work from the information in your plan, so your plan must be complete and include all IDs, paths, field names, and configuration details.

CRITICAL PLANNING PRINCIPLE - PRESERVE USER DETAILS:
When users provide detailed specifications (field definitions, SQL queries, prompts, mappings, 
configuration values, file paths, column names, etc.), include these details verbatim in the 
workflow plan. The plan is handed off to a separate AI for command generation - if details 
are summarized or omitted from the plan, the command generator cannot produce accurate configurations.

DO NOT:
- Summarize lists of fields to just names (descriptions guide extraction behavior)
- Paraphrase SQL queries or prompts (exact wording matters)
- Omit mappings, paths, or configuration values the user specified

DO:
- Include full field definitions with names, types, descriptions, and any mappings
- Preserve exact queries, prompts, and expressions as provided
- Carry forward all paths, column names, and configuration details

The workflow plan is a specification document, not a summary for human reading.

KEY KNOWLEDGE:
- Document node is ALWAYS used for PDF/document text extraction
- AI Action nodes are for analysis, validation, and processing AFTER extraction
- Don't ask about extraction tools - you know Document node does this
- Folder Selector is used when files come from network folders

CRITICAL - VALID NODE TYPES:
You may ONLY use these node types in workflow plans: Database, AI Action, AI Extract, Document,
Loop, End Loop, Conditional, Human Approval, Alert, Folder Selector, File, Set Variable,
Execute Application, Excel Export, Server, Integration.
Do NOT invent node types that are not in this list (e.g., "Trigger", "Scheduled Trigger", "Timer",
"Webhook", "Start", "End", "Delay", "Wait"). These do not exist.
Workflows are triggered externally — either manually, via the platform's schedule system, or via API call.
There is NO trigger node. If the user requests a scheduled trigger, note in your plan that a
schedule should be created separately after the workflow is built.

IMPORTANT CONTEXT:
- Gather requirements naturally through conversation
- Build workflows incrementally as you understand the user's needs
- When ready to build, generate a detailed workflow plan with all required IDs and use the generate_workflow_commands tool
- Always explain what you're doing and why
- Use the tools available to look up connections, agents, users, and groups BEFORE finalizing your plan

Current Phase: {phase}

Phase-Specific Guidance:
{phase_guidance}

Key Principles:
1. Ask smart, contextual questions based on what the user tells you
2. Use your knowledge of business processes to fill in common patterns
3. Don't overwhelm with too many questions at once (3-5 max)
4. Build progressively - you can always refine later
5. When building, use the generate_workflow_commands tool to create the JSON from your final plan
"""
        
        phase_guidance = {
            BuilderPhase.DISCOVERY: """
You're helping the user identify what process they want to automate.
- Understand their current pain points
- Identify the core process type
- Determine high-level goals
- Be encouraging and show the possibilities
- IMPORTANT: Start using update_requirements tool as soon as you learn details:
  * Process name when mentioned
  * Trigger type (manual, file, scheduled, etc.)
  * Any data sources or systems mentioned
""",
            BuilderPhase.REQUIREMENTS: """
You're gathering specific requirements for the workflow.
- Ask about data sources and systems
- Understand decision points and rules
- Identify stakeholders and approvals needed
- Clarify inputs and expected outputs
- CRITICAL: Use update_requirements tool EVERY TIME you learn something new:
  * When user mentions process name → update process_name
  * When user mentions trigger type → update trigger_type
  * When user mentions data source → use add_data_source
  * When user mentions stakeholder/email → use add_stakeholder
  * When user mentions output/result → use add_output
  * When user mentions system → use add_system
- Use the get_available_database_connections and get_available_ai_agents tools to show options
- When users mention data sources or databases, use get_database_schema to discover what tables and columns are available. This lets you construct SQL queries without asking the user for technical database details.
- For PDF/document processing, you already know to use Document nodes for extraction
- Only ask which AI agent they want for analysis, not about extraction tools
""",
BuilderPhase.PLANNING: """
You're planning the workflow structure.
- Map requirements to specific workflow nodes
- Explain the workflow flow you'll create
- Identify any gaps or assumptions
- Be specific about which nodes you'll use
- For Database nodes, verify table and column names against the actual schema using get_database_schema before finalizing the plan

IMPORTANT: When creating the workflow plan, wrap numbered steps in <workflow_plan> tags like this:

<workflow_plan>
1. Folder Selector node: Selects files from the specified folder.
2. Document node: Extracts text from the document.
3. AI Extract node: Extracts structured data fields.
4. Alert node: Sends notification via email.
</workflow_plan>

This format ensures the plan is clearly identified for processing.
""",
BuilderPhase.BUILDING: """
You're now building the workflow.
- Once the user confirms the workflow plan is correct, use the generate_workflow_commands tool
- The tool will generate the JSON commands based on the stored workflow plan
- Include all necessary nodes based on requirements
""",
            BuilderPhase.REFINEMENT: f"""
The initial workflow has been built and EXISTS in the workflow designer. Now refining based on user feedback.

CRITICAL RULES:
- The workflow ALREADY EXISTS with specific node IDs (node-0, node-1, etc.)
- DO NOT recreate the entire workflow - only describe the changes needed
- Reference existing node IDs when describing modifications

REFINEMENT PROCESS:
1. Understand what the user wants to change
2. Create a modification plan describing ONLY the changes needed
3. Call generate_workflow_commands and pass the modification plan as the argument
4. Reference existing node IDs in your plan

MODIFICATION PLAN EXAMPLES:

User: "Change the email recipient to finance@company.com"
Plan: "Update node-4 (Alert node): Change recipients to finance@company.com"

User: "Add a validation step before the approval"
Plan: "1. Add new Conditional node after node-2: Check if amount > 0
       2. Connect node-2 to new node
       3. Connect new node (pass) to node-3
       4. Connect new node (fail) to new Alert node for invalid data"

User: "Remove the approval step"
Plan: "1. Delete node-3 (Human Approval)
       2. Reconnect node-2 directly to node-4"

User: "Change the condition threshold from $10,000 to $5,000"
Plan: "Update node-3 (Conditional): Change rightValue from 10000 to 5000"

IMPORTANT:
- Always check the current workflow state below to find correct node IDs
- Describe changes in plain language - the command generator will create the proper commands
- Be specific about which nodes to modify, add, or remove

Current Workflow State:
{self._format_workflow_state()}
"""
        }

        # Builder delegation: override phase guidance to skip Q&A and go straight to building
        if self.is_builder_delegation:
            phase_guidance[self.phase] = """
You are receiving a delegated task from the Builder Agent. The user's requirements have
already been gathered. DO NOT ask clarifying questions — the task description contains
everything you need.

BUILDER DELEGATION RULES:
1. Treat the incoming message as a COMPLETE specification — go straight to creating a workflow plan
2. Look up required IDs (connections, agents, users) using your tools BEFORE creating the plan
3. For Database nodes, use get_database_schema to discover tables and columns, then construct
   SQL queries based on the actual schema and the user's intent. Do NOT ask the user for SQL
   queries or table names — figure them out from the schema.
4. Create a focused workflow plan and generate commands immediately using generate_workflow_commands
5. If the description is very complex, build the CORE workflow first (3-5 nodes max) and
   note what can be added in a refinement pass
6. Do NOT ask questions like "What database?", "Who should approve?" — use whatever details
   are provided in the description or make reasonable defaults
7. Wrap your workflow plan in <workflow_plan> tags
8. IMPORTANT: After creating the plan, IMMEDIATELY call the generate_workflow_commands tool.
   Do not wait for user confirmation — the Builder Agent has already obtained confirmation.
"""

        # NOTE: This now uses a shortened node types doc since it only plans workflows now and does not generate build commands
        workflow_command_docs = workflow_command_docs.format(command_types_doc=sysprompts.WORKFLOW_COMMAND_TYPES, node_types_doc=sysprompts.WORKFLOW_NODE_TYPES)

        self.SYSTEM = base_prompt.format(
            workflow_docs=workflow_command_docs,
            phase=self.phase.value,
            phase_guidance=phase_guidance.get(self.phase, "")
        )

        #print(f"SYSTEM PROMPT UPDATED TO PHASE: {self.SYSTEM}")

    def _extract_workflow_plan(self, response: str) -> Optional[str]:
        """Extract workflow plan from <workflow_plan> tags in response."""
        import re
        pattern = r'<workflow_plan>([\s\S]*?)</workflow_plan>'
        match = re.search(pattern, response)
        if match:
            plan = match.group(1).strip()
            logger.info(f"Extracted workflow plan ({len(plan)} chars)")
            return plan
        return None

    def _register_tools(self):
        """Register tools for workflow building"""
        
        # NOTE: Deprecated - replaced by get_detailed_node_config
        @tool
        def get_available_node_types() -> str:
            """Get detailed information about all available workflow node types and their configurations"""
            node_types = {
                "Database": {
                    "description": "Execute database queries or updates",
                    "key_config": {
                        "dbConnection": "Connection ID (required)",
                        "dbOperation": "query or execute",
                        "query": "SQL query string",
                        "saveToVariable": "Boolean to save output",
                        "outputVariable": "Variable to store results"
                    },
                    "use_cases": ["Fetch data", "Update records", "Insert data", "Delete records"]
                },
                "AI Action": {
                    "description": "Process data using AI agents",
                    "key_config": {
                        "agent_id": "ID of the AI agent (required)",
                        "prompt": "Prompt to send to the agent",
                        "outputVariable": "Variable to store AI response",
                        "continueOnError": "Whether to continue if AI fails (boolean)"
                    },
                    "use_cases": ["Text analysis", "Data extraction", "Content generation", "Decision support"]
                },
                "AI Extract": {
                    "description": "Extract structured data from text using AI with defined field schemas",
                    "key_config": {
                        "inputVariable": "Variable containing text to extract from, use dollar-brace syntax",
                        "outputVariable": "Name for storing extracted data object without dollar-brace",
                        "failOnMissingRequired": "Boolean true or false",
                        "specialInstructions": "Optional text for AI guidance (e.g., Return numbers without currency symbols)",
                        "fields": [
                            {"name": "field_name", "type": "text|number|boolean|list|group|repeated_group", "required": "boolean true or false", "description": "Description to guide extraction", "children": "Array of child field definitions (only for group and repeated_group types)"}
                        ]
                    },
                    "use_cases": ["Extracting specific data points", "User needs predictable, consistent field names for downstream conditional logic"]
                },
                "Human Approval": {
                    "description": "Request approval from a human",
                    "key_config": {
                        "assigneeType": "user, group, or unassigned",
                        "assigneeId": "Email or user ID of approver",
                        "approvalTitle": "Title of the approval request",
                        "approvalDescription": "Detailed description",
                        "approvalData": "Data to include for review",
                        "timeoutMinutes": "String number like 60"
                    },
                    "use_cases": ["Purchase approvals", "Document review", "Exception handling", "Quality checks"]
                },
                "File": {
                    "description": "Read, write, or manipulate files",
                    "key_config": {
                        "operation": "read, write, append, delete, check, copy, or move",
                        "filePath": "Path to the file",
                        "destinationPath": "Destination path for copy/move operation",
                        "contentSource": "direct, variable, or previous (for write or append operations)",
                        "content": "Content to write for 'write' operation",
                        "contentVariable": "Variable containing content to write",
                        "contentPath": "Path in previous step to write",
                        "saveToVariable": "Boolean - MUST be true to store output (required with outputVariable)",
                        "outputVariable": "Variable to store content (read) or existence check (check)"
                    },
                    "use_cases": ["Read configs", "Write reports", "Move processed files", "Archive data"]
                },
                "Folder Selector": {
                    "description": "Select files from a folder based on patterns",
                    "key_config": {
                        "folderPath": "Path to monitor",
                        "filePattern": "Pattern to match files (e.g., *.pdf)",
                        "selectionMode": "first, all, or pattern",
                        "outputVariable": "Variable to store selected file(s)",
                        "failIfEmpty": "Fail if no files found (boolean)"
                    },
                    "use_cases": ["File processing", "Batch processing", "File triggers", "Document intake"]
                },
                "Loop": {
                    "description": "Iterate over a collection of items",
                    "key_config": {
                        "sourceType": "Usually auto",
                        "loopSource": "Variable containing array/list (use ${varName} syntax)",
                        "itemVariable": "Variable name for current item (plain name, no ${}) - use THIS inside loop body",
                        "indexVariable": "Variable name for current index (plain name, no ${})",
                        "maxIterations": "Maximum iterations allowed"
                    },
                    "important": "Nodes INSIDE the loop must reference ${itemVariable}, NOT the original array variable",
                    "use_cases": ["Process multiple records", "Batch operations", "Send multiple emails", "Generate reports"]
                },
                "End Loop": {
                    "description": "Marks the end of a loop iteration",
                    "key_config": {
                        "loopNodeId": "ID of the corresponding starting Loop node (required)"
                    },
                    "use_cases": ["Marks the end of a loop"]
                },
                "Conditional": {
                    "description": "Make decisions based on conditions",
                    "key_config": {
                        "conditionType": "comparison or expression",
                        "leftValue": "First value to compare",
                        "operator": "==, !=, >, <, >=, <=, contains",
                        "rightValue": "Second value to compare"
                    },
                    "use_cases": ["Route approvals", "Check thresholds", "Validate data", "Error handling"]
                },
                "Alert": {
                    "description": "Send notifications via email or other channels",
                    "key_config": {
                        "alertType": "email, sms, or webhook",
                        "recipients": "Comma-separated list of recipients",
                        "messageTemplate": "Message body with variables"
                    },
                    "use_cases": ["Status updates", "Error notifications", "Completion alerts", "Escalations"]
                },
                "Set Variable": {
                    "description": "Set or manipulate workflow variables",
                    "key_config": {
                        "variableName": "Name of the variable",
                        "valueSource": "direct or expression",
                        "valueExpression": "Value or expression to evaluate",
                        "evaluateAsExpression": "Evaluate expression using Python (boolean true or false)"
                    },
                    "use_cases": ["Store calculations", "Format data", "Set flags", "Initialize values"]
                },
                "Document": {
                    "description": "Process documents with AI extraction",
                    "key_config": {
                        "documentAction": "process or generate",
                        "sourceType": "file or variable",
                        "sourcePath": "Path to document or variable name",
                        "outputFormat": "json, text, or structured",
                        "forceAiExtraction": "Force AI processing"
                    },
                    "use_cases": ["Invoice processing", "Form extraction", "Contract analysis", "Report parsing"]
                },
                "Execute Application": {
                    "description": "Run external applications or scripts",
                    "key_config": {
                        "applicationPath": "Path to executable",
                        "arguments": "Command-line arguments",
                        "waitForCompletion": "Wait for app to finish",
                        "outputVariable": "Variable to store output"
                    },
                    "use_cases": ["Run scripts", "Call external tools", "System integration", "Batch processing"]
                }
            }
            return self._escape_curly(json.dumps(node_types, indent=2))
        
        @tool
        def get_available_database_connections() -> str:
            """Get list of available database connections"""
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                
                # Set tenant context
                cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
                
                cursor.execute("""
                    SELECT 
                        id,
                        connection_name,
                        database_name
                    FROM Connections
                    ORDER BY connection_name
                """)
                
                connections = []
                for row in cursor.fetchall():
                    connections.append({
                        "id": row.id,
                        "name": row.connection_name,
                        "database": row.database_name
                    })
                
                cursor.close()
                conn.close()
                
                if connections:
                    result = "Available Database Connections:\n"
                    for conn_info in connections:
                        result += f"Connection ID: {conn_info['id']} | Name: {conn_info['name']} | Database: {conn_info['database']}\n"
                    return result
                else:
                    return "No database connections available. You can use placeholder ID: 1"

            except Exception as e:
                logger.error(f"Error fetching database connections: {e}")
                return "Error fetching connections. You can use placeholder IDs: 1, 2, 3"

        @tool
        def get_available_integrations() -> str:
            """Get list of available connected external integrations (e.g., Stripe, Shopify,
            QuickBooks, Slack). ALWAYS call this tool before adding an Integration node, to
            look up the correct numeric integration_id. NEVER guess or use the integration
            name as the integration_id - the validator will reject the workflow."""
            try:
                from integration_manager import get_integration_manager
                manager = get_integration_manager()
                integrations = manager.list_integrations()
                if not integrations:
                    return "No integrations are currently connected for this tenant."
                result = "Available Integrations (use the numeric Integration ID as the integration_id config field):\n"
                for it in integrations:
                    connected = "connected" if it.get('is_connected') else "disconnected"
                    result += (
                        f"Integration ID: {it['integration_id']} | "
                        f"Name: {it['integration_name']} | "
                        f"Platform: {it.get('platform_name', '')} | "
                        f"Category: {it.get('platform_category', '')} | "
                        f"Status: {connected}\n"
                    )
                return result
            except Exception as e:
                logger.error(f"Error fetching integrations: {e}")
                return f"Error fetching integrations: {e}"

        @tool
        def get_integration_operations(integration_id: int) -> str:
            """Get available operations for a specific integration. Returns each operation's
            key (snake_case identifier), display name, category (read/write), description, and
            parameters. ALWAYS call this tool after get_available_integrations and before
            configuring an Integration node. The 'operation' config field on the Integration
            node MUST be one of the operation keys returned by this tool (e.g. 'get_customers'),
            NOT the human-readable name."""
            try:
                from integration_manager import get_integration_manager
                manager = get_integration_manager()
                operations = manager.get_operations(integration_id)
                if not operations:
                    return (
                        f"No operations found for integration_id={integration_id}. "
                        f"Verify the ID with get_available_integrations."
                    )
                result = f"Operations for integration_id={integration_id}:\n"
                for op in operations:
                    params_lines = []
                    for p in op.get('parameters', []):
                        req = " (required)" if p.get('required') else ""
                        desc = p.get('description') or p.get('label') or ''
                        params_lines.append(
                            f"    - {p.get('name')} ({p.get('type','text')}){req}: {desc}"
                        )
                    params_summary = ("\n" + "\n".join(params_lines)) if params_lines else " (none)"
                    result += (
                        f"\n* operation key: {op.get('key')}\n"
                        f"  name: {op.get('name')}\n"
                        f"  category: {op.get('category', 'read')}\n"
                        f"  description: {op.get('description','')}\n"
                        f"  parameters:{params_summary}\n"
                    )
                return result
            except Exception as e:
                logger.error(f"Error fetching integration operations: {e}")
                return f"Error fetching operations for integration_id={integration_id}: {e}"

        @tool
        def get_available_ai_agents() -> str:
            """Get list of available AI agents"""
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                
                # Set tenant context
                cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
                
                cursor.execute("""
                    SELECT 
                        id as agent_id,
                        description as agent_name,
                        objective as agent_objective
                    FROM Agents
                    WHERE enabled = 1
                    ORDER BY description
                """)
                
                agents = []
                for row in cursor.fetchall():
                    full_obj = row.agent_objective or "No description available"
                    short_obj = full_obj[:100] + "..." if len(full_obj) > 100 else full_obj
                    agents.append({
                        "id": row.agent_id,
                        "name": row.agent_name,
                        "objective": short_obj
                    })
                
                cursor.close()
                conn.close()
                
                if agents:
                    result = "Available AI Agents:\n"
                    for agent in agents:
                        result += f"Agent ID: {agent['id']} | Name: {agent['name']} | Purpose: {agent['objective']}\n"
                    return result
                else:
                    return "No AI agents available. You can use placeholder IDs: 1, 2, 3"
                    
            except Exception as e:
                logger.error(f"Error fetching AI agents: {e}")
                return "Error fetching agents. You can use placeholder IDs: 1, 2, 3"
            
        @tool
        def get_available_users() -> str:
            """Get list of available users"""
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                
                # Set tenant context
                cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
                
                cursor.execute("""
                    SELECT 
                        id,
                        name,
                        email
                    FROM [User]
                    ORDER BY user_name
                """)
                
                connections = []
                for row in cursor.fetchall():
                    connections.append({
                        "id": row.id,
                        "name": row.name,
                        "email": row.email
                    })
                
                cursor.close()
                conn.close()
                
                if connections:
                    result = "Available Users:\n"
                    for conn_info in connections:
                        result += f"User ID: {conn_info['id']} | Name: {conn_info['name']} | Email: {conn_info['email']}\n"
                    return result
                else:
                    return "No users available. You can use placeholder ID: 1"
                    
            except Exception as e:
                logger.error(f"Error fetching users: {e}")
                return "Error fetching users. You can use placeholder IDs: 1, 2, 3"
            
        @tool
        def get_available_user_groups() -> str:
            """Get list of available user groups"""
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                
                # Set tenant context
                cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
                
                cursor.execute("""
                    SELECT 
                        id,
                        group_name
                    FROM [Groups]
                    ORDER BY group_name
                """)
                
                connections = []
                for row in cursor.fetchall():
                    connections.append({
                        "id": row.id,
                        "group_name": row.group_name
                    })
                
                cursor.close()
                conn.close()
                
                if connections:
                    result = "Available User Groups:\n"
                    for conn_info in connections:
                        result += f"Group ID: {conn_info['id']} | Group Name: {conn_info['group_name']}\n"
                    return result
                else:
                    return "No groups available. You can use placeholder ID: 1"
                    
            except Exception as e:
                logger.error(f"Error fetching groups: {e}")
                return "Error fetching groups. You can use placeholder IDs: 1, 2, 3"

        @tool
        def get_database_tables(connection_id: int) -> str:
            """Get the list of tables available in a specific database connection.
            Use this after get_available_database_connections to see what tables exist
            in a database before constructing queries.

            Args:
                connection_id: The numeric connection ID from get_available_database_connections
            """
            try:
                base_url = get_base_url()
                api_url = f"{base_url}/api/internal/connection-tables/{connection_id}"
                headers = {
                    'Content-Type': 'application/json',
                    'X-Internal-API-Key': get_internal_api_key()
                }

                response = requests.get(api_url, headers=headers, timeout=15)

                if response.status_code != 200:
                    try:
                        error_msg = response.json().get('message', f'HTTP {response.status_code}')
                    except Exception:
                        error_msg = f'HTTP {response.status_code}'
                    return f"Error fetching tables for connection {connection_id}: {error_msg}"

                data = response.json()
                if data.get('status') != 'success':
                    return f"Error: {data.get('message', 'Unknown error')}"

                tables = data.get('tables', [])
                if not tables:
                    return f"No tables found for connection {connection_id}."

                result = f"Tables in connection {connection_id}:\n"
                for t in tables:
                    schema = t.get('schema', 'dbo')
                    name = t.get('table_name', '')
                    result += f"  - {schema}.{name}\n"

                return result

            except requests.exceptions.ConnectionError:
                return f"Error: Could not connect to the main application API. Is the main app running?"
            except Exception as e:
                logger.error(f"Error fetching tables for connection {connection_id}: {e}")
                return f"Error fetching tables for connection {connection_id}: {str(e)}"

        @tool
        def get_database_schema(connection_id: int) -> str:
            """Get the full schema (tables and columns with data types) for a database connection.
            Use this to discover what data is available so you can construct SQL queries.
            Call this after identifying the connection ID from get_available_database_connections.

            Args:
                connection_id: The numeric connection ID from get_available_database_connections
            """
            try:
                base_url = get_base_url()
                api_url = f"{base_url}/api/internal/connection-schema/{connection_id}"
                headers = {
                    'Content-Type': 'application/json',
                    'X-Internal-API-Key': get_internal_api_key()
                }

                response = requests.get(api_url, headers=headers, timeout=30)

                if response.status_code == 404:
                    return f"Connection {connection_id} not found. Use get_available_database_connections to find valid connection IDs."

                if response.status_code != 200:
                    try:
                        error_msg = response.json().get('message', f'HTTP {response.status_code}')
                    except Exception:
                        error_msg = f'HTTP {response.status_code}'
                    return f"Error fetching schema for connection {connection_id}: {error_msg}"

                data = response.json()
                if data.get('status') != 'success':
                    return f"Error: {data.get('message', 'Unknown error')}"

                schema_yaml = data.get('schema_yaml', '')
                table_count = data.get('table_count', 0)

                if not schema_yaml:
                    return f"No schema information available for connection {connection_id}."

                # Truncate if schema is very large to avoid filling LLM context
                max_chars = 6000
                if len(schema_yaml) > max_chars:
                    schema_yaml = schema_yaml[:max_chars] + (
                        f"\n\n... (truncated — showing first {max_chars} chars of {len(schema_yaml)} total. "
                        f"{table_count} tables in database. Use get_database_tables to see full table list, "
                        f"then ask about specific tables.)"
                    )

                result = f"Schema for connection {connection_id} ({table_count} tables):\n\n{schema_yaml}"
                return result

            except requests.exceptions.ConnectionError:
                return f"Error: Could not connect to the main application API. Is the main app running?"
            except Exception as e:
                logger.error(f"Error fetching schema for connection {connection_id}: {e}")
                return f"Error fetching schema for connection {connection_id}: {str(e)}"

        @tool
        def get_detailed_node_config(node_types: str) -> str:
            """
            Get detailed configuration information for specific workflow node types.
            Use this when you need to understand the exact config fields and options for planning or if the user needs specific guidance.

            When multiple nodes could potentially solve the same problem (e.g., AI Action vs AI Extract 
            for data extraction, or Document vs AI Extract for processing files), use this tool to 
            compare their capabilities and select the most appropriate node for the user's specific requirements.
            
            Args:
                node_types: Single node type or string with comma-separated list of node types.
                        Examples: "AI Extract" or "Database, Loop, Conditional"
                        Use "all" to get all node types.
            
            Returns:
                Detailed configuration documentation for the requested node types.
            """
            return get_node_details(node_types)
        
        @tool
        def identify_missing_requirements() -> str:
            """Analyze current requirements and identify what information is still needed"""
            missing = []
            reqs = self.requirements
            
            # Check core requirements
            if not reqs.process_name:
                missing.append("What should we call this workflow?")
            
            if not reqs.trigger_type:
                missing.append("How should the workflow be triggered? (manual, scheduled, file arrival, etc.)")
            
            if not reqs.data_sources:
                missing.append("Where does the data come from?")
            
            if not reqs.outputs:
                missing.append("What should be the final output or result?")
            
            # Check for specific details based on what we know
            if reqs.trigger_type == "file" and not reqs.trigger_details.get("folder_path"):
                missing.append("Which folder should be monitored for files?")
            
            if reqs.approval_levels and not any(a.get("approver") for a in reqs.approval_levels):
                missing.append("Who are the approvers for each approval level?")
            
            return json.dumps({
                "missing_count": len(missing),
                "missing_items": missing,
                "requirements_summary": reqs.to_dict()
            })
        
        @tool
        def update_requirements(
            process_name: Optional[str] = None,
            trigger_type: Optional[str] = None,
            add_data_source: Optional[str] = None,
            add_stakeholder: Optional[str] = None,
            add_output: Optional[str] = None,
            add_system: Optional[str] = None
        ) -> str:
            """Update the workflow requirements based on user input"""
            if process_name:
                self.requirements.process_name = process_name
            
            if trigger_type:
                self.requirements.trigger_type = trigger_type
            
            if add_data_source:
                self.requirements.data_sources.append({"source": add_data_source})
            
            if add_stakeholder:
                self.requirements.stakeholders.append(add_stakeholder)
            
            if add_output:
                self.requirements.outputs.append(add_output)
                
            if add_system:
                self.requirements.systems_involved.append(add_system)
            
            return f"Requirements updated. Current state: {json.dumps(self.requirements.to_dict())}"
        
        @tool  
        def generate_workflow_commands(workflow_plan: str = "") -> str:
            """Generate workflow JSON commands based on the workflow plan and requirements.
            
            Args:
                workflow_plan: The workflow plan describing the nodes and their connections.
                              Pass the plan you created for the user.
            """

            try:
                logger.info(86 * '-')
                logger.info("Generating workflow commands...")
                logger.info(86 * '-')
                if self.phase != BuilderPhase.BUILDING:
                    self.update_phase(BuilderPhase.BUILDING)
                
                commands = None

                # Use provided plan, or fall back to stored plan
                plan = workflow_plan if workflow_plan else None

                # If no plan provided, try stored plan or conversation context
                if not plan:
                    plan = self.workflow_plan

                # Try to get workflow plan from conversation if not already stored
                workflow_plan = plan
                if not workflow_plan and self.conversation_context:
                    # Check recent assistant messages for <workflow_plan> tags
                    for msg in reversed(self.conversation_context):
                        if msg.get('role') == 'assistant':
                            extracted = self._extract_workflow_plan(msg.get('content', ''))
                            if extracted:
                                workflow_plan = extracted
                                self.workflow_plan = extracted  # Store it
                                logger.info("Extracted workflow plan from conversation context")
                                break

                logger.debug(f"Current phase: {self.phase}")
                logger.debug(f"Command generator available: {'Yes' if self.command_generator else 'No'}")
                logger.debug(f"Current workflow plan: {workflow_plan}")
                logger.debug(f"Current requirements: {json.dumps(self.requirements.to_dict(), indent=2)}")
                logger.debug(f"Current workflow state: {json.dumps(self.workflow_state, indent=2)}")
                
                # Use CommandGenerator if available and we have a plan
                if self.command_generator and workflow_plan:
                    logger.info("Using CommandGenerator with stored workflow plan")
                    result = self.command_generator.generate_commands(
                        workflow_plan=workflow_plan,
                        requirements=self.requirements.to_dict(),
                        workflow_state=self.workflow_state
                    )
                    if result and 'commands' in result:
                        commands = result['commands']
                        logger.info(f"CommandGenerator produced {len(commands)} commands")
                        logger.info(json.dumps(commands, indent=2))
                
                # Fallback: prompt the LLM to generate inline
                if commands is None:
                    logger.info("No commands were generated, falling back to inline command generation...")
                    plan_text = workflow_plan or "Based on the gathered requirements"
                    return f"""Please generate the workflow commands based on this plan:

{plan_text}

Requirements context:
{json.dumps(self.requirements.to_dict(), indent=2)}

Output a complete JSON with action: build_workflow and commands array in a ```json code block."""
                
                # Post-process: resolve names to IDs
                for command in commands:
                    if command.get("type") == "add_node":
                        config = command.get("config", {})
                        if "agent_id" in config and not str(config["agent_id"]).isdigit():
                            config["agent_id"] = self._resolve_agent_id(config["agent_id"])
                        if "dbConnection" in config and not str(config["dbConnection"]).isdigit():
                            config["dbConnection"] = self._resolve_connection_id(config["dbConnection"])
                
                self.generated_commands = commands
                
                if len(commands) > 0:
                    self.update_phase(BuilderPhase.REFINEMENT)
                
                workflow_json = {
                    "action": "build_workflow",
                    "commands": commands
                }

                self.current_json_commands = workflow_json.copy()

                # Store original plan only on FIRST generation (not validation fixes)
                if plan and not self.original_workflow_plan and not self.is_validation_fix:
                    self.original_workflow_plan = plan
                
                # Always update current plan
                if plan:
                    self.workflow_plan = plan
                
                # Accumulate all commands (initial + fixes)
                if commands:
                    self.generated_commands = commands
                    if isinstance(commands, list):
                        self.accumulated_commands.extend(commands)
                        logger.info(f"Accumulated commands: {len(self.accumulated_commands)} total")
                
                # Reset validation fix flag after processing
                self.is_validation_fix = False
                
                result = "I've generated your workflow:\n\n```json\n"
                result += json.dumps(workflow_json, indent=2)
                result += "\n```"
                
                return result
                
            except Exception as e:
                logger.error(f"Error generating workflow commands: {e}", exc_info=True)
                return f"Error generating commands: {str(e)}. Please try again."
        
        # Add tools to the list
        self.tools = [
            get_available_database_connections,
            get_database_tables,
            get_database_schema,
            get_available_integrations,
            get_integration_operations,
            get_available_ai_agents,
            identify_missing_requirements,
            update_requirements,
            generate_workflow_commands,
            get_available_user_groups,
            get_available_users,
            get_detailed_node_config
        ]

    def _resolve_agent_id(self, agent_id_or_name):
        """Helper to resolve agent ID from name"""
        if str(agent_id_or_name).isdigit():
            return str(agent_id_or_name)
        
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            cursor.execute("""
                SELECT id FROM Agents 
                WHERE description = ? AND enabled = 1
            """, (agent_id_or_name,))
            result = cursor.fetchone()
            
            if not result:
                cursor.execute("""
                    SELECT id FROM Agents 
                    WHERE description LIKE ? AND enabled = 1
                    ORDER BY LEN(description)
                """, (f"%{agent_id_or_name}%",))
                result = cursor.fetchone()
            
            conn.close()
            if result:
                return str(result[0])
                
        except Exception as e:
            logger.error(f"Error resolving agent ID: {e}")
        
        return "1"  # Default
    
    def _resolve_connection_id(self, conn_id_or_name):
        """Helper to resolve connection ID from name"""
        if str(conn_id_or_name).isdigit():
            return str(conn_id_or_name)
        
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            cursor.execute("""
                SELECT id FROM Connections 
                WHERE connection_name = ?
            """, (conn_id_or_name,))
            result = cursor.fetchone()
            
            if not result:
                cursor.execute("""
                    SELECT id FROM Connections 
                    WHERE connection_name LIKE ?
                    ORDER BY LEN(connection_name)
                """, (f"%{conn_id_or_name}%",))
                result = cursor.fetchone()
            
            conn.close()
            if result:
                return str(result[0])
                
        except Exception as e:
            logger.error(f"Error resolving connection ID: {e}")
        
        return "1"  # Default
    
    def _build_agent_executor(self):
        """Build the agent executor with tools"""
        # Format tools for OpenAI tools API (required by GPT-5.2+)
        tools_formatted = [convert_to_openai_tool(t) for t in self.tools]

        # Create prompt template
        prompt = ChatPromptTemplate.from_messages([
            ("system", self.SYSTEM),
            MessagesPlaceholder(variable_name="chat_history"),
            ("user", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        # Bind tools to LLM (tools API instead of legacy functions API)
        llm_with_tools = self.llm.bind(tools=tools_formatted)

        # Create the agent using the tools API pattern
        agent = (
            {
                "input": lambda x: x["input"],
                "chat_history": lambda x: x.get("chat_history", []),
                "agent_scratchpad": lambda x: format_to_tool_messages(
                    x["intermediate_steps"]
                ),
            }
            | prompt
            | llm_with_tools
            | ToolsAgentOutputParser()
        )
        
        # Create agent executor
        self.agent_executor = AgentExecutor(
            agent=agent,
            tools=self.tools,
            verbose=True,
            handle_parsing_errors=True,
            max_iterations=10
        )
    
    def _preprocess_json(self, json_str: str) -> str:
        """
        Preprocess JSON string to fix common issues from LLM output:
        1. Remove JavaScript-style comments (// and /* */)
        2. Fix improperly escaped backslashes in paths
        3. Remove trailing commas
        """
        import re
        
        # Remove single-line comments (// ...)
        json_str = re.sub(r'//.*$', '', json_str, flags=re.MULTILINE)
        
        # Remove multi-line comments (/* ... */)
        json_str = re.sub(r'/\*[\s\S]*?\*/', '', json_str)
        
        # Fix backslash escaping in Windows paths
        # Pattern: Look for paths like \\server\folder and ensure proper escaping
        # This is tricky because we need to find unescaped backslashes in string values
        
        # Strategy: Find string values that look like paths and fix them
        def fix_path_escapes(match):
            """Fix backslash escaping within a JSON string value"""
            content = match.group(1)
            # Check if this looks like a Windows path (contains backslashes followed by word chars)
            if '\\' in content and not content.startswith('${'):
                # Replace single backslashes with double (but not already doubled)
                # First, normalize any existing escaping
                # \\\\  -> [QUAD]
                # \\    -> [DOUBLE]  
                # Then: [DOUBLE] that's followed by a letter/word -> should be \\\\
                
                # Replace \\\\ with placeholder
                content = content.replace('\\\\\\\\', '\x00QUAD\x00')
                # Replace remaining \\ with placeholder  
                content = content.replace('\\\\', '\x00DOUBLE\x00')
                # Any remaining single \ before a word char needs to be escaped
                content = re.sub(r'\\([a-zA-Z_${}])', r'\\\\\1', content)
                # Restore placeholders
                content = content.replace('\x00QUAD\x00', '\\\\\\\\')
                content = content.replace('\x00DOUBLE\x00', '\\\\')
            return f'"{content}"'
        
        # Apply to all string values (simplistic approach - finds "..." patterns)
        json_str = re.sub(r'"((?:[^"\\]|\\.)*)"', fix_path_escapes, json_str)
        
        # Remove trailing commas before ] or }
        json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
        
        return json_str
    
    def _extract_workflow_commands_with_ai(self, response: str) -> Optional[Dict]:
        """
        Use AI to extract workflow commands from a response when regex/preprocessing fails.
        This is a fallback that only runs when normal extraction doesn't work.
        
        Args:
            response: The full AI response that may contain workflow commands
            
        Returns:
            Dict with 'action' and 'commands' keys, or None if no commands found
        """
        try:
            logger.info("Attempting AI-based workflow command extraction (fallback)")
            
            system_prompt = """You are a JSON extraction assistant. Your task is to extract workflow build commands from text.

The text may contain a JSON object with workflow commands in a format like:
{
  "action": "build_workflow",
  "commands": [...]
}

RULES:
1. If the text contains workflow commands, extract ONLY the JSON object
2. Fix any JSON syntax errors (comments, trailing commas, escape sequences)
3. Ensure all Windows paths have properly escaped backslashes (use \\\\)
4. Return ONLY valid JSON - no explanations, no markdown, no code fences
5. If no workflow commands are present, return exactly: {"no_commands": true}

IMPORTANT: Your response must be ONLY the JSON object, nothing else."""

            user_prompt = f"""Extract the workflow build commands from this text. Return only valid JSON.

TEXT:
{response}

Remember: Return ONLY the JSON object, no other text."""

            # Call the AI to extract commands
            ai_response = azureQuickPrompt(
                prompt=user_prompt,
                system=system_prompt,
                temp=0.0  # Deterministic for consistent extraction
            )
            
            # Clean up the response (azureQuickPrompt already strips code fences)
            ai_response = ai_response.strip()
            
            # Parse the extracted JSON
            extracted = json.loads(ai_response)
            
            # Check if it found commands
            if extracted.get("no_commands"):
                logger.info("AI extraction found no workflow commands in response")
                return None
            
            # Validate structure
            if isinstance(extracted, dict) and 'commands' in extracted:
                # Ensure action is set
                if 'action' not in extracted:
                    extracted['action'] = 'build_workflow'
                    
                logger.info(f"AI extraction successful - found {len(extracted['commands'])} commands")
                return extracted
            else:
                logger.warning("AI extraction returned unexpected structure")
                return None
                
        except json.JSONDecodeError as e:
            logger.error(f"AI extraction returned invalid JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"AI extraction failed: {e}")
            return None
    
    
    def update_phase(self, new_phase: BuilderPhase):
        """Update the current phase and system prompt"""
        self.phase = new_phase
        self._set_system_prompt()
        self._build_agent_executor()
        logger.info(f"Updated phase to {new_phase.value}")
    
    def process_message(self, message: str, workflow_state: Dict = None) -> Tuple[str, Dict]:
        """
        Process a user message and return response with metadata
        
        Returns:
            Tuple of (response_text, metadata_dict)
        """
        try:
            # Update workflow state if provided
            if workflow_state:
                old_state_exists = self._has_existing_workflow(self.workflow_state)
                new_state_exists = self._has_existing_workflow(workflow_state)
                self.workflow_state = workflow_state
                
                # If we just got a workflow and we're still in discovery, jump to refinement
                if new_state_exists and not old_state_exists and self.phase == BuilderPhase.DISCOVERY:
                    logger.info("Workflow detected - switching to REFINEMENT mode")
                    self.update_phase(BuilderPhase.REFINEMENT)
                    self._extract_requirements_from_workflow(workflow_state)
                    # Rebuild the agent with new system prompt
                    self._set_system_prompt()
                    self._build_agent_executor()
                else:
                    # Rebuild the agent with updated system prompt
                    self._set_system_prompt()
                    self._build_agent_executor()

            # Store the message in context
            self.conversation_context.append({"role": "user", "content": message})
            
            # Determine if we should update phase based on conversation
            # Skip auto-phase for builder delegations (already in PLANNING, don't transition)
            if self.phase != BuilderPhase.REFINEMENT and not self.is_builder_delegation:
                self._auto_update_phase()
                
            # Run the agent
            result = self.agent_executor.invoke({
                "input": message,
                "chat_history": self.chat_history
            })
            
            response = result.get("output", "")

            logger.debug(f"Agent Response: {response}")
            
            # Update chat history
            self.chat_history.extend([
                HumanMessage(content=message),
                AIMessage(content=response)
            ])
            
            # Store response in context
            self.conversation_context.append({"role": "assistant", "content": response})
            
            # Extract and store workflow plan if present
            extracted_plan = self._extract_workflow_plan(response)
            if extracted_plan:
                self.workflow_plan = extracted_plan
                logger.info("Stored workflow plan for command generation")
            
            # Use workflow commands from tool call only (not auto-extracted from response)
            workflow_commands = None
            if self.generated_commands:
                workflow_commands = {
                    "action": "build_workflow",
                    "commands": self.generated_commands
                } if isinstance(self.generated_commands, list) else self.generated_commands
                logger.info(f"Using workflow commands from generate_workflow_commands tool")
                self.current_json_commands = workflow_commands.copy()
                # Clear after use
                # self.generated_commands = None
            
            # Build metadata
            metadata = {
                "phase": self.phase.value,
                "requirements": self.requirements.to_dict(),
                "workflow_commands": workflow_commands,
                "workflow_plan": self.workflow_plan,
                "session_id": self.session_id,
                "is_refine_mode": self.phase == BuilderPhase.REFINEMENT,
                "has_workflow": self._has_existing_workflow(self.workflow_state)
            }

            logger.debug(f"Response: {response}, Metadata: {metadata}")
            print(f"Response: {response}, Metadata: {metadata}")
            
            return response, metadata
            
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            err_msg = cfg.WORKFLOW_AGENT_FALLBACK_RESPONSE
            return err_msg, {
                "phase": self.phase.value,
                "error": str(e)
            }
    
    def _auto_update_phase(self):
        """Automatically update phase based on conversation progress"""
        reqs = self.requirements
        
        # Check conversation for key indicators
        recent_messages = self.conversation_context[-5:] if len(self.conversation_context) >= 5 else self.conversation_context
        recent_text = " ".join([m["content"].lower() for m in recent_messages])
        
        # Phase progression logic
        if self.phase == BuilderPhase.DISCOVERY:
            # Move to requirements if we have basic info
            if reqs.process_name or reqs.trigger_type or len(self.conversation_context) > 4:
                self.update_phase(BuilderPhase.REQUIREMENTS)
        
        elif self.phase == BuilderPhase.REQUIREMENTS:
            # Move to planning if we have enough requirements
            if (reqs.data_sources and (reqs.outputs or reqs.stakeholders)) or \
               len(self.conversation_context) > 8 or \
               any(word in recent_text for word in ["ready", "that's all", "everything", "complete"]):
                self.update_phase(BuilderPhase.PLANNING)
        
        elif self.phase == BuilderPhase.PLANNING:
            # Move to building when user confirms or says build
            build_indicators = ["build", "create", "proceed", "yes", "go ahead", "looks good", 
                              "that's right", "correct", "generate", "make it"]
            if any(indicator in recent_text for indicator in build_indicators):
                self.update_phase(BuilderPhase.BUILDING)
        
        elif self.phase == BuilderPhase.BUILDING:
            # Move to refinement after building
            if self.generated_commands or "generated" in recent_text:
                self.update_phase(BuilderPhase.REFINEMENT)
    
    def get_session_summary(self) -> Dict:
        """Get a summary of the current session"""
        return {
            "session_id": self.session_id,
            "phase": self.phase.value,
            "requirements": self.requirements.to_dict(),
            "conversation_length": len(self.conversation_context),
            "has_workflow": self.generated_commands is not None
        }
