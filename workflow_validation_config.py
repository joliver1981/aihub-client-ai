"""
Workflow Validation Configuration
Contains all prompts and settings for the AI-powered workflow validation system
"""
# TODO: DELETE NOT USED ANYMORE
# Main validation settings
WORKFLOW_VALIDATION_CONFIG = {
    'enabled': True,  # Master switch to enable/disable validation
    'use_ai': True,   # Use AI for validation (if False, only basic checks)
    'max_issues_to_report': 20,  # Maximum number of issues to report
    'auto_fix_threshold': 0.8,  # Confidence threshold for auto-fix suggestions
    
    # Validation prompts for AI
    'prompts': {
        # System prompt that sets the AI's role
        'system_validation': """You are an expert workflow validation specialist. Your role is to analyze workflows for correctness, completeness, and alignment with requirements.
                
You must evaluate:
1. Structural integrity (nodes properly connected, start/end nodes defined)
2. Logical flow (conditions, loops, branches make sense)
3. Data flow (variables properly set and used)
4. Requirements alignment (workflow matches stated requirements)
5. Best practices (efficiency, error handling, maintainability)

Provide detailed, actionable feedback with specific fixes. Always return valid JSON.""",

        # Structural analysis prompt
        'structure_analysis': """Analyze this workflow structure and identify issues:

WORKFLOW DATA:
{workflow_json}

EXPECTED REQUIREMENTS:
{requirements}

Evaluate:
1. Missing or disconnected nodes
2. Invalid connections (wrong types: should be pass, fail, or complete)
3. Orphaned nodes (not connected to flow)
4. Missing start or end nodes
5. Loop structures (proper Start Loop/End Loop pairing with matching IDs)
6. Conditional branches (all paths handled, both pass and fail connections)
7. Variable dependencies (variables used before being set)

For each issue found, provide:
- Issue description
- Severity (critical/error/warning/info)
- Specific node ID if applicable
- Suggested fix with exact commands
- Tools needed to fix (get_available_database_connections, get_available_ai_agents, etc.)

Return as JSON:
{{
  "structural_issues": [
    {{
      "severity": "critical|error|warning|info",
      "node_id": "node_123",
      "issue": "description",
      "suggestion": "how to fix",
      "tools_needed": ["tool1", "tool2"],
      "fix_command": {{
        "type": "add_node|connect_nodes|update_node_config",
        "parameters": {{}}
      }}
    }}
  ],
  "structure_valid": true/false,
  "confidence": 0.0-1.0
}}""",

        # Business logic validation prompt
        'logic_validation': """Validate the business logic of this workflow:

WORKFLOW DATA:
{workflow_json}

REQUIREMENTS:
{requirements}

PROCESS DESCRIPTION:
{process_description}

Check for:
1. Logic errors in conditions:
   - Impossible conditions (e.g., x > 10 AND x < 5)
   - Missing else paths (fail connections from conditionals)
   - Redundant conditions
2. Loop issues:
   - Infinite loops (no exit condition)
   - Missing End Loop nodes
   - Incorrect loop variable usage
3. Variable problems:
   - Usage before initialization
   - Type mismatches
   - Missing required variables
4. Operator issues:
   - Wrong operator for data type
   - Invalid comparisons
5. Error handling:
   - Missing error paths for database operations
   - No fallback for AI actions
   - Missing timeout handling for approvals
6. Approval flows:
   - Missing approvers
   - Incorrect approval chains
   - No timeout configuration
7. Data transformation:
   - Missing data mappings
   - Incorrect format conversions

For each issue, specify:
- Logic problem description
- Impact on workflow execution
- Corrective action needed
- Whether AI tools can help gather missing info

Return as JSON:
{{
  "logic_issues": [
    {{
      "severity": "critical|error|warning|info", 
      "category": "condition|loop|variable|approval|data|error_handling",
      "node_id": "node_id",
      "issue": "description",
      "impact": "what will happen if not fixed",
      "fix": "specific corrective action",
      "missing_info": ["what info is needed"],
      "suggested_tools": ["get_available_database_connections", "get_available_ai_agents"]
    }}
  ],
  "logic_sound": true/false,
  "confidence": 0.0-1.0
}}""",

        # Requirements matching prompt
        'requirements_matching': """Verify this workflow meets the stated requirements:

WORKFLOW IMPLEMENTATION:
{workflow_json}

ORIGINAL REQUIREMENTS:
{requirements}

USER'S PROCESS DESCRIPTION:
{process_description}

AGREED WORKFLOW PLAN:
{workflow_plan}

Validate that the workflow includes:
1. All required data sources:
   - Databases mentioned
   - Files/folders referenced
   - APIs or external systems
2. All decision points:
   - Business rules implemented
   - Conditional logic for each decision
   - Proper branching
3. All stakeholders/approvers:
   - Human approval nodes for each approver
   - Correct assignees configured
   - Timeout settings
4. All outputs:
   - Reports generated
   - Notifications sent
   - Data saved
5. All systems integrated:
   - Each system has appropriate node
   - Correct connection/agent IDs
6. Constraints respected:
   - Time limits
   - Data volume limits
   - Security requirements
7. Trigger conditions:
   - Correct trigger type
   - Proper trigger configuration

For each requirement, indicate:
- Whether it's met (true/false)
- What's missing if not met
- Nodes that should be added/modified
- Specific configuration needed

Return as JSON:
{{
  "requirements_analysis": {{
    "data_sources_met": true/false,
    "missing_data_sources": ["list of missing sources"],
    "decision_points_met": true/false,
    "missing_decisions": ["list of missing decision points"],
    "approvals_met": true/false,
    "missing_approvals": ["list of missing approvers"],
    "outputs_met": true/false,
    "missing_outputs": ["list of missing outputs"],
    "systems_integrated": true/false,
    "missing_systems": ["list of missing system integrations"],
    "trigger_configured": true/false,
    "trigger_issues": ["list of trigger problems"]
  }},
  "gaps": [
    {{
      "requirement": "specific requirement not met",
      "severity": "critical|error|warning",
      "suggested_node": {{
        "type": "node_type",
        "label": "suggested label",
        "config": {{
          "field": "value"
        }},
        "connections": ["connect to node_id with type pass"]
      }}
    }}
  ],
  "requirements_met": true/false,
  "confidence": 0.0-1.0
}}""",

        # Fix generation prompt
        'fix_suggestion': """Generate specific fixes for these workflow issues:

CURRENT WORKFLOW:
{workflow_json}

ISSUES FOUND:
{issues}

REQUIREMENTS:
{requirements}

For each issue, provide:
1. Step-by-step fix instructions
2. Exact workflow commands to execute
3. Which AI tools to use for missing information
4. Alternative approaches if main fix isn't possible

Generate fixes that:
- Make minimal changes to existing workflow
- Preserve all correct logic
- Use proper command syntax
- Reference actual node IDs from the workflow
- Use available tools appropriately

Available tools for gathering information:
- get_available_database_connections: Find database connection IDs
- get_available_ai_agents: Find AI agent IDs
- validate_email: Verify email addresses for approvals
- check_variable_exists: Verify if variable is defined

Available command types:
- add_node: Create new node with full configuration
- update_node_config: Modify existing node settings
- connect_nodes: Create connection between nodes
- set_start_node: Mark node as workflow start
- add_variable: Define workflow variable
- remove_node: Delete unnecessary node
- remove_connection: Remove incorrect connection

Return as JSON:
{{
  "fixes": [
    {{
      "issue_id": "reference to issue",
      "fix_steps": [
        "Step 1: Use tool X to get Y",
        "Step 2: Add node with configuration",
        "Step 3: Connect to existing flow"
      ],
      "commands": [
        {{
          "type": "add_node",
          "parameters": {{
            "node_type": "Database",
            "label": "Query Customer Data",
            "config": {{
              "dbConnection": "use get_available_database_connections",
              "query": "SELECT * FROM customers WHERE status = 'active'"
            }},
            "position": {{"left": "300px", "top": "200px"}},
            "node_id": "db_query_node"
          }}
        }},
        {{
          "type": "connect_nodes",
          "parameters": {{
            "from": "existing_node_id",
            "to": "db_query_node",
            "connection_type": "pass"
          }}
        }}
      ],
      "tools_to_use": [
        {{
          "tool": "get_available_database_connections",
          "purpose": "Find the correct database connection ID for customer database"
        }}
      ],
      "alternative_approach": "If database is not available, use AI Action node to simulate data"
    }}
  ],
  "fix_order": ["issue_id1", "issue_id2"],
  "estimated_complexity": "low|medium|high",
  "can_auto_fix": true/false
}}""",

        # Performance optimization prompt (optional)
        'performance_optimization': """Analyze workflow for performance improvements:

WORKFLOW:
{workflow_json}

Suggest optimizations for:
1. Parallel processing opportunities
2. Redundant operations that can be combined
3. Inefficient loops that can be optimized
4. Database query optimizations
5. Caching opportunities
6. Batch processing instead of individual items

Return optimization suggestions as JSON."""
    },
    
    # Validation rules for specific node types
    'node_validation_rules': {
        'Database': {
            'required_fields': ['dbConnection', 'query', 'dbOperation'],
            'optional_fields': ['outputVariable', 'saveToVariable'],
            'field_types': {
                'dbConnection': 'string',
                'query': 'string',
                'dbOperation': ['query', 'execute'],
                'saveToVariable': 'boolean'
            }
        },
        'AI Action': {
            'required_fields': ['agent_id', 'prompt'],
            'optional_fields': ['outputVariable', 'continueOnError'],
            'field_types': {
                'agent_id': 'string',
                'prompt': 'string',
                'continueOnError': 'boolean'
            }
        },
        'Loop': {
            'required_fields': ['loopSource', 'itemVariable'],
            'optional_fields': ['indexVariable', 'maxIterations'],
            'field_types': {
                'loopSource': 'string',
                'itemVariable': 'string',
                'indexVariable': 'string',
                'maxIterations': 'string'
            }
        },
        'End Loop': {
            'required_fields': ['loopNodeId'],
            'optional_fields': [],
            'field_types': {
                'loopNodeId': 'string'
            }
        },
        'Conditional': {
            'required_fields': ['conditionType', 'leftValue', 'operator'],
            'optional_fields': ['rightValue'],
            'field_types': {
                'conditionType': ['comparison', 'expression'],
                'leftValue': 'string',
                'operator': ['==', '!=', '>', '<', '>=', '<=', 'contains'],
                'rightValue': 'string'
            }
        },
        'Human Approval': {
            'required_fields': ['assignee', 'approvalTitle'],
            'optional_fields': ['approvalDescription', 'approvalData', 'timeoutMinutes'],
            'field_types': {
                'assignee': 'string',
                'approvalTitle': 'string',
                'approvalDescription': 'string',
                'timeoutMinutes': 'string'
            }
        },
        'Alert': {
            'required_fields': ['alertType', 'recipients'],
            'optional_fields': ['emailSubject', 'messageTemplate', 'attachmentPath'],
            'field_types': {
                'alertType': ['email', 'text', 'call'],
                'recipients': 'string',
                'emailSubject': 'string',
                'messageTemplate': 'string',
                'attachmentPath': 'string'
            }
        },
        'Document': {
            'required_fields': ['documentAction', 'sourceType'],
            'optional_fields': ['sourcePath', 'outputType', 'outputPath', 'outputFormat'],
            'field_types': {
                'documentAction': ['process', 'generate'],
                'sourceType': ['file', 'variable'],
                'outputType': ['variable', 'file'],
                'outputFormat': ['json', 'text', 'structured']
            }
        },
        'Set Variable': {
            'required_fields': ['variableName', 'valueSource'],
            'optional_fields': ['valueExpression'],
            'field_types': {
                'variableName': 'string',
                'valueSource': ['direct', 'expression'],
                'valueExpression': 'string'
            }
        },
        'Folder Selector': {
            'required_fields': ['folderPath'],
            'optional_fields': ['filePattern', 'recursive', 'outputVariable'],
            'field_types': {
                'folderPath': 'string',
                'filePattern': 'string',
                'recursive': 'boolean'
            }
        },
        'File Operation': {
            'required_fields': ['operation', 'sourcePath'],
            'optional_fields': ['targetPath', 'outputVariable', 'outputFormat'],
            'field_types': {
                'operation': ['read', 'write', 'append', 'delete', 'copy', 'move', 'check'],
                'sourcePath': 'string',
                'targetPath': 'string',
                'outputFormat': ['text', 'json', 'base64']
            }
        }
    },
    
    # Severity thresholds
    'severity_scores': {
        'critical': 25,  # Points deducted for critical issues
        'error': 15,     # Points deducted for errors
        'warning': 5,    # Points deducted for warnings
        'info': 1        # Points deducted for info/suggestions
    },
    
    # Auto-fix settings
    'auto_fix': {
        'enabled': True,
        'require_confirmation': True,
        'max_fixes_per_run': 10,
        'fix_priority': ['critical', 'error', 'warning', 'info']
    }
}

# Export for use in other modules
def get_validation_config():
    """Get the current validation configuration"""
    return WORKFLOW_VALIDATION_CONFIG

def update_validation_config(updates: dict):
    """Update validation configuration with new settings"""
    global WORKFLOW_VALIDATION_CONFIG
    
    def deep_update(original, updates):
        """Recursively update nested dictionaries"""
        for key, value in updates.items():
            if isinstance(value, dict) and key in original:
                deep_update(original[key], value)
            else:
                original[key] = value
    
    deep_update(WORKFLOW_VALIDATION_CONFIG, updates)
    return WORKFLOW_VALIDATION_CONFIG

def get_validation_prompt(prompt_name: str):
    """Get a specific validation prompt by name"""
    return WORKFLOW_VALIDATION_CONFIG.get('prompts', {}).get(prompt_name, '')

def set_validation_prompt(prompt_name: str, prompt_text: str):
    """Update a specific validation prompt"""
    if 'prompts' not in WORKFLOW_VALIDATION_CONFIG:
        WORKFLOW_VALIDATION_CONFIG['prompts'] = {}
    WORKFLOW_VALIDATION_CONFIG['prompts'][prompt_name] = prompt_text
    return True
