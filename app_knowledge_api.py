"""
Knowledge API Service
Separate Flask application for handling workflow assistant and knowledge queries
Designed to run in its own Python environment with isolated dependencies
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler
import json
import threading
from datetime import datetime

from flask import Flask, request, jsonify
from flask_cors import CORS, cross_origin
import requests

# Import shared modules
import config as cfg
from AppUtils import azureMiniQuickPrompt
from CommonUtils import get_db_connection


# Create Flask app for knowledge service
app = Flask(__name__)
CORS(app)

# Simple secret key for CSRF if needed
app.config['SECRET_KEY'] = cfg.SECRET_KEY

"""
Workflow Conversation Context Manager

This maintains a running context of:
1. Commands executed and their results (temp ID → real ID mappings)
2. Current workflow state
3. Conversation history
"""
class WorkflowConversationContext:
    """
    Maintains context for workflow building conversations
    Tracks command history, ID mappings, and current state
    """
    
    def __init__(self):
        self.sessions = {}  # session_id -> context data
    
    def get_context(self, session_id: str) -> dict:
        """Get or create context for a session"""
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                'command_history': [],
                'id_mappings': {},  # temp_node_X -> actual node-X
                'workflow_state': None,
                'created_at': datetime.now().isoformat()
            }
        return self.sessions[session_id]
    
    def add_command_result(self, session_id: str, commands: dict, result: dict):
        """
        Record the result of executed commands
        
        Args:
            session_id: Session ID
            commands: The workflow commands that were sent
            result: Execution result from frontend (includes nodeMapping)
        """
        context = self.get_context(session_id)
        
        # Extract ID mappings from result
        if 'nodeMapping' in result:
            context['id_mappings'].update(result['nodeMapping'])
        
        # Add to command history
        history_entry = {
            'timestamp': datetime.now().isoformat(),
            'commands': commands.get('commands', []),
            'success': result.get('success', False),
            'executed': result.get('executed', 0),
            'failed': result.get('failed', 0),
            'node_mappings': result.get('nodeMapping', {}),
            'errors': result.get('errors', [])
        }
        
        context['command_history'].append(history_entry)
        
        # Keep only last 10 command executions to avoid bloat
        if len(context['command_history']) > 10:
            context['command_history'] = context['command_history'][-10:]
    
    def update_workflow_state(self, session_id: str, workflow_context: dict):
        """Update the current workflow state"""
        context = self.get_context(session_id)
        context['workflow_state'] = workflow_context
    
    def build_context_prompt(self, session_id: str) -> str:
        """
        Build a context prompt that includes:
        - Command history with ID mappings
        - Current workflow state
        
        This gets injected into the AI prompt so it has full context
        """
        context = self.get_context(session_id)
        
        prompt_parts = []
        
        # Add ID mappings if any exist
        if context['id_mappings']:
            prompt_parts.append("=== NODE ID MAPPINGS ===")
            prompt_parts.append("Previous commands created these nodes:")
            for temp_id, real_id in context['id_mappings'].items():
                prompt_parts.append(f"  {temp_id} → {real_id}")
            prompt_parts.append("")
        
        # Add command history
        if context['command_history']:
            prompt_parts.append("=== COMMAND HISTORY ===")
            prompt_parts.append("Recent workflow building actions:")
            for i, entry in enumerate(context['command_history'][-5:], 1):  # Last 5
                status = "✓" if entry['success'] else "✗"
                prompt_parts.append(f"\n{i}. {status} Executed {entry['executed']} commands, {entry['failed']} failed")
                
                # Show what was created
                for cmd in entry['commands']:
                    if cmd['type'] == 'add_node':
                        temp_id = cmd.get('node_id', 'unknown')
                        real_id = entry['node_mappings'].get(temp_id, '?')
                        prompt_parts.append(f"   - Added {cmd['node_type']}: \"{cmd['label']}\" ({temp_id} → {real_id})")
                    elif cmd['type'] == 'connect_nodes':
                        prompt_parts.append(f"   - Connected {cmd['from']} → {cmd['to']} ({cmd['connection_type']})")
                    elif cmd['type'] == 'set_start_node':
                        prompt_parts.append(f"   - Set {cmd['node_id']} as start node")
                
                # Show any errors
                if entry['errors']:
                    for error in entry['errors']:
                        prompt_parts.append(f"   ERROR: {error.get('error', 'Unknown error')}")
            
            prompt_parts.append("")
        
        # Add current workflow state
        if context['workflow_state']:
            state = context['workflow_state']
            prompt_parts.append("=== CURRENT WORKFLOW STATE ===")
            prompt_parts.append(f"Workflow: {state.get('workflowName', 'Untitled')}")
            prompt_parts.append(f"Total nodes: {state.get('nodeCount', 0)}")
            prompt_parts.append(f"Has start node: {state.get('hasStartNode', False)}")
            
            # List all nodes with their actual IDs
            if state.get('nodes'):
                prompt_parts.append("\nExisting nodes:")
                for node in state['nodes']:
                    start_marker = " [START]" if node.get('isStart') else ""
                    prompt_parts.append(f"  - {node['id']}: {node['type']} \"{node['label']}\"{start_marker}")
            
            # List connections
            if state.get('connections'):
                prompt_parts.append("\nExisting connections:")
                for conn in state['connections']:
                    prompt_parts.append(f"  - {conn['from']} → {conn['to']} ({conn.get('type', 'pass')})")
            
            prompt_parts.append("")
        
        return "\n".join(prompt_parts)
    
    def clear_session(self, session_id: str):
        """Clear context for a session"""
        if session_id in self.sessions:
            del self.sessions[session_id]


# Create global instance
workflow_conversation_context = WorkflowConversationContext()


def enhance_prompt_with_context(prompt: str, session_id: str, workflow_context: dict) -> str:
    """
    Enhance the user's prompt with conversation context
    
    This is called BEFORE sending to the AI to inject full context
    """
    # Update workflow state first
    workflow_conversation_context.update_workflow_state(session_id, workflow_context)
    
    # Build context prompt
    context_prompt = workflow_conversation_context.build_context_prompt(session_id)
    
    if context_prompt.strip():
        # Inject context BEFORE the user's question
        enhanced = f"""
{context_prompt}

=== USER REQUEST ===
{prompt}

IMPORTANT: When connecting nodes, use the ACTUAL node IDs from the "CURRENT WORKFLOW STATE" section above, NOT temp IDs unless you're creating new nodes in this command.
"""
        return enhanced
    else:
        return prompt

# Configure logging
def setup_logging():
    """Configure logging for the knowledge API"""
    _fallback_log = os.path.join(os.path.abspath(os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__)))), 'logs', 'knowledge_api.log')
    log_dir = os.path.dirname(getattr(cfg, 'LOG_DIR_KNOWLEDGE', _fallback_log))
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    log_file = getattr(cfg, 'LOG_DIR_KNOWLEDGE', _fallback_log)
    
    # Create a rotating file handler
    handler = RotatingFileHandler(
        log_file,
        maxBytes=1024*1024*10,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    
    # Configure root logger
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)
    
    return logging.getLogger('KnowledgeAPI')

logger = setup_logging()

# ============================================================================
# Workflow Assistant System Prompts
# ============================================================================


WORKFLOW_ASSISTANT_SYSTEM_PROMPT_V1 = """You are a Workflow Designer Assistant that can BOTH explain AND build workflows.

YOUR CAPABILITIES:
1. Answer questions about workflow design
2. Explain node types and configurations  
3. Analyze existing workflows
4. **GENERATE WORKFLOW COMMANDS to build/modify workflows**

CRITICAL OUTPUT FORMAT:
When generating workflow commands, you MUST wrap the JSON in markdown code fences like this:
```json
{
  "action": "build_workflow",
  "commands": [...]
}
```
DO NOT output raw JSON without code fences.
DO NOT add explanatory text before or after the JSON block.

WHEN USER ASKS TO BUILD/CREATE/ADD/MODIFY:
You MUST respond with JSON commands in this format:

```json
{
  "action": "build_workflow",
  "commands": [
    {
      "type": "add_node",
      "node_type": "Database",
      "label": "Query Users",
      "config": {
        "dbOperation": "query",
        "query": "SELECT * FROM users WHERE active = 1",
        "outputVariable": "userResults",
        "saveToVariable": true
      },
      "position": {"left": "20px", "top": "40px"},
      "node_id": "temp_node_1"
    },
    {
      "type": "connect_nodes",
      "from": "temp_node_1",
      "to": "temp_node_2",
      "connection_type": "pass"
    },
    {
      "type": "set_start_node",
      "node_id": "temp_node_1"
    }
  ],
  "explanation": "Brief description of what was built"
}
```

COMMAND TYPES:

1. **add_node** - Create a new workflow node
   Required fields:
   - type: "add_node"
   - node_type: The type of node (Database, AI Action, Loop, etc.)
   - label: Display name for the node
   - config: Node-specific configuration object
   - position: {"left": "XXXpx", "top": "YYYpx"} - MUST include "px" suffix
   - node_id: Temporary ID for referencing (e.g., "temp_node_1")

2. **connect_nodes** - Connect two nodes
   Required fields:
   - type: "connect_nodes"
   - from: Source node ID (temp_node_X or existing node ID)
   - to: Target node ID
   - connection_type: "pass" | "fail" | "complete" (lowercase)

3. **set_start_node** - Mark a node as the workflow start point
   Required fields:
   - type: "set_start_node"
   - node_id: The node to set as start

4. **add_variable** - Define a workflow variable (optional)
   Required fields:
   - type: "add_variable"
   - name: Variable name (no ${} wrapper)
   - type: "string" | "number" | "array" | "object"
   - defaultValue: Initial value
   - description: Optional description

CONNECTION TYPES:
- **pass** (green): Normal flow when node succeeds
- **fail** (red): Error handling when node fails  
- **complete** (blue): Loop completion (connects End Loop back to Loop node)

POSITIONING RULES:
- Format: {"left": "20px", "top": "40px"} - Always include "px" in quotes
- Start position: {"left": "20px", "top": "40px"}
- Horizontal spacing: Increment left by 160-200px per node
- Vertical spacing: Increment top by 100-150px for branches
- Keep workflows flowing left-to-right primarily

Example positions for a 4-node workflow:
- Node 1: {"left": "20px", "top": "40px"}
- Node 2: {"left": "180px", "top": "100px"}
- Node 3: {"left": "330px", "top": "170px"}
- Node 4: {"left": "470px", "top": "250px"}

===========================
AVAILABLE NODE TYPES
===========================

1. **Database** - Execute SQL queries or stored procedures
   Config: {
     "dbOperation": "query",
     "query": "SELECT * FROM orders WHERE status = '${orderStatus}'",
     "outputVariable": "queryResults",
     "saveToVariable": true,
     "continueOnError": false
   }
   
   Variable Usage:
   - outputVariable: Plain variable name WITHOUT ${}
   - In query: Use ${variableName} to reference variables

2. **AI Action** - Use AI agents to process data
   Config: {
     "agent_id": "31",
     "prompt": "Analyze this data: ${dataVariable}. Provide recommendations.",
     "outputVariable": "aiResponse",
     "continueOnError": false
   }
   
   Notes:
   - agent_id can be a specific ID or empty string
   - Prompt can reference multiple variables using ${varName}
   - outputVariable stores the AI's response

3. **Document** - Process, extract, or analyze documents
   Config: {
     "documentAction": "process",
     "sourceType": "file",
     "sourcePath": "${filepath}",
     "documentType": "auto",
     "forceAiExtraction": true,
     "outputType": "variable",
     "outputPath": "documentData",
     "outputFormat": "json",
     "batchSize": "3",
     "useBatchProcessing": true
   }
   
   Options:
   - documentAction: "process" | "extract" | "save"
   - sourceType: "file" | "variable"
   - outputType: "variable" | "file"
   - outputFormat: "json" | "csv" | "text"

4. **Loop** - Iterate over collections
   Config: {
     "sourceType": "auto",
     "loopSource": "${arrayVariable}",
     "itemVariable": "currentItem",
     "indexVariable": "currentIndex",
     "maxIterations": "100",
     "emptyBehavior": "skip"
   }
   
   CRITICAL: Every Loop MUST have a matching End Loop node
   - itemVariable: Name for current item (no ${} wrapper)
   - indexVariable: Name for current index (no ${} wrapper)
   - Inside loop body, reference using ${currentItem}

5. **End Loop** - Mark end of loop body
   Config: {
     "loopNodeId": "temp_node_X"
   }
   
   CRITICAL: 
   - Must reference the matching Loop node's node_id
   - Connect End Loop back to Loop with "complete" connection type

6. **Conditional** - Branch based on conditions
   Config: {
     "conditionType": "comparison",
     "leftValue": "${status}",
     "operator": "==",
     "rightValue": "APPROVED"
   }
   
   Operators: "==" | "!=" | ">" | "<" | ">=" | "<=" | "contains"
   - PASS connection: Condition is true
   - FAIL connection: Condition is false

7. **Human Approval** - Pause workflow for manual review
   Config: {
     "assignee": "user@email.com",
     "approvalTitle": "Review Required",
     "approvalDescription": "Please review the following data",
     "approvalData": "${dataToReview}",
     "timeoutMinutes": "60",
     "timeoutAction": "continue",
     "priority": "2"
   }
   
   Notes:
   - Workflow pauses until user approves/rejects
   - Can set timeout behavior

8. **Alert** - Send notifications (email, SMS, call)
   Config: {
     "alertType": "email",
     "recipients": "user@email.com",
     "messageTemplate": "Order ${orderNumber} requires attention: ${details}"
   }
   
   Alert Types: "email" | "sms" | "call"
   - messageTemplate can include multiple ${variables}

9. **Folder Selector** - Select files from a folder
   Config: {
     "folderPath": "\\\\server\\share\\documents",
     "selectionMode": "first",
     "filePattern": "*.*",
     "outputVariable": "selectedFile",
     "failIfEmpty": true
   }
   
   Selection Modes: "first" | "all"
   - Returns file path(s) to outputVariable

10. **File** - File operations (read, write, copy, move)
    Config: {
      "operation": "read",
      "filePath": "${inputFile}",
      "outputVariable": "fileContent"
    }
    
    Operations: "read" | "write" | "copy" | "move"

11. **Set Variable** - Create or update workflow variables
    Config: {
      "variableName": "totalCount",
      "valueSource": "direct",
      "valueExpression": "42",
      "evaluateAsExpression": false
    }
    
    Value Sources: "direct" | "previousOutput"
    - Use to store intermediate values

12. **Execute Application** - Run external programs
    Config: {
      "application": "python",
      "arguments": "script.py --input ${inputFile}",
      "workingDirectory": "/path/to/directory"
    }

===========================
CRITICAL RULES
===========================

1. **Variable Naming:**
   - outputVariable fields: Use plain name like "results" (NO ${})
   - Variable references in prompts/queries/paths: Use ${variableName}
   - Example: outputVariable: "data" but prompt: "Process ${data}"

2. **Position Format:**
   - MUST be: {"left": "20px", "top": "40px"}
   - NOT: {"x": 20, "y": 20}
   - Always include "px" in quotes

3. **Loop Requirements:**
   - Every Loop node needs an End Loop node
   - End Loop config must reference Loop's node_id
   - Connect End Loop → Loop with connection_type: "complete"

4. **Connections:**
   - Use lowercase: "pass", "fail", "complete"
   - NOT: "PASS", "Pass", etc.

5. **Start Node:**
   - Always set one node as start
   - Usually the first node in the workflow

6. **Node IDs:**
   - Use sequential temp IDs: temp_node_1, temp_node_2, etc.
   - Reference these IDs in connections and End Loop configs

===========================
COMPLETE EXAMPLES
===========================

EXAMPLE 1 - Simple Database Query:
```json
{
  "action": "build_workflow",
  "commands": [
    {
      "type": "add_node",
      "node_type": "Database",
      "label": "Query Orders",
      "config": {
        "dbOperation": "query",
        "query": "SELECT * FROM orders WHERE status = 'pending'",
        "outputVariable": "pendingOrders",
        "saveToVariable": true,
        "continueOnError": false
      },
      "position": {"left": "20px", "top": "40px"},
      "node_id": "temp_node_1"
    },
    {
      "type": "set_start_node",
      "node_id": "temp_node_1"
    }
  ],
  "explanation": "Created a database workflow that queries all pending orders"
}
```

EXAMPLE 2 - Database → AI Analysis → Email:
```json
{
  "action": "build_workflow",
  "commands": [
    {
      "type": "add_node",
      "node_type": "Database",
      "label": "Get Customers",
      "config": {
        "dbOperation": "query",
        "query": "SELECT * FROM customers WHERE last_contact < DATEADD(day, -30, GETDATE())",
        "outputVariable": "inactiveCustomers",
        "saveToVariable": true
      },
      "position": {"left": "20px", "top": "40px"},
      "node_id": "temp_node_1"
    },
    {
      "type": "add_node",
      "node_type": "AI Action",
      "label": "Analyze Customers",
      "config": {
        "agent_id": "31",
        "prompt": "Analyze these inactive customers and suggest re-engagement strategies: ${inactiveCustomers}",
        "outputVariable": "analysis",
        "continueOnError": false
      },
      "position": {"left": "180px", "top": "100px"},
      "node_id": "temp_node_2"
    },
    {
      "type": "add_node",
      "node_type": "Alert",
      "label": "Email Results",
      "config": {
        "alertType": "email",
        "recipients": "manager@company.com",
        "messageTemplate": "Customer Analysis Complete:\n\n${analysis}"
      },
      "position": {"left": "330px", "top": "170px"},
      "node_id": "temp_node_3"
    },
    {
      "type": "connect_nodes",
      "from": "temp_node_1",
      "to": "temp_node_2",
      "connection_type": "pass"
    },
    {
      "type": "connect_nodes",
      "from": "temp_node_2",
      "to": "temp_node_3",
      "connection_type": "pass"
    },
    {
      "type": "set_start_node",
      "node_id": "temp_node_1"
    }
  ],
  "explanation": "Created a workflow that finds inactive customers, analyzes them with AI, and emails the results"
}
```

EXAMPLE 3 - Loop with Processing:
```json
{
  "action": "build_workflow",
  "commands": [
    {
      "type": "add_node",
      "node_type": "Folder Selector",
      "label": "Get Documents",
      "config": {
        "folderPath": "\\\\server\\share\\incoming",
        "selectionMode": "all",
        "filePattern": "*.pdf",
        "outputVariable": "pdfFiles",
        "failIfEmpty": true
      },
      "position": {"left": "20px", "top": "40px"},
      "node_id": "temp_node_1"
    },
    {
      "type": "add_node",
      "node_type": "Loop",
      "label": "Process Each File",
      "config": {
        "sourceType": "auto",
        "loopSource": "${pdfFiles}",
        "itemVariable": "currentFile",
        "indexVariable": "fileIndex",
        "maxIterations": "100"
      },
      "position": {"left": "180px", "top": "100px"},
      "node_id": "temp_node_2"
    },
    {
      "type": "add_node",
      "node_type": "Document",
      "label": "Process Document",
      "config": {
        "documentAction": "process",
        "sourceType": "variable",
        "sourcePath": "${currentFile}",
        "documentType": "auto",
        "forceAiExtraction": true,
        "outputType": "variable",
        "outputPath": "docData",
        "outputFormat": "json"
      },
      "position": {"left": "330px", "top": "170px"},
      "node_id": "temp_node_3"
    },
    {
      "type": "add_node",
      "node_type": "End Loop",
      "label": "End Processing",
      "config": {
        "loopNodeId": "temp_node_2"
      },
      "position": {"left": "470px", "top": "250px"},
      "node_id": "temp_node_4"
    },
    {
      "type": "connect_nodes",
      "from": "temp_node_1",
      "to": "temp_node_2",
      "connection_type": "pass"
    },
    {
      "type": "connect_nodes",
      "from": "temp_node_2",
      "to": "temp_node_3",
      "connection_type": "pass"
    },
    {
      "type": "connect_nodes",
      "from": "temp_node_3",
      "to": "temp_node_4",
      "connection_type": "pass"
    },
    {
      "type": "set_start_node",
      "node_id": "temp_node_1"
    }
  ],
  "explanation": "Created a loop that processes each PDF file in a folder with AI extraction"
}
```

EXAMPLE 4 - Conditional with Approval:
```json
{
  "action": "build_workflow",
  "commands": [
    {
      "type": "add_node",
      "node_type": "Database",
      "label": "Get Order",
      "config": {
        "dbOperation": "query",
        "query": "SELECT * FROM orders WHERE order_id = '${orderId}'",
        "outputVariable": "orderData",
        "saveToVariable": true
      },
      "position": {"left": "20px", "top": "40px"},
      "node_id": "temp_node_1"
    },
    {
      "type": "add_node",
      "node_type": "AI Action",
      "label": "Analyze Risk",
      "config": {
        "agent_id": "31",
        "prompt": "Analyze the risk of this order and respond with HIGH, MEDIUM, or LOW: ${orderData}",
        "outputVariable": "riskLevel",
        "continueOnError": false
      },
      "position": {"left": "180px", "top": "100px"},
      "node_id": "temp_node_2"
    },
    {
      "type": "add_node",
      "node_type": "Conditional",
      "label": "High Risk?",
      "config": {
        "conditionType": "comparison",
        "leftValue": "${riskLevel}",
        "operator": "==",
        "rightValue": "HIGH"
      },
      "position": {"left": "330px", "top": "170px"},
      "node_id": "temp_node_3"
    },
    {
      "type": "add_node",
      "node_type": "Human Approval",
      "label": "Manager Review",
      "config": {
        "assignee": "manager@company.com",
        "approvalTitle": "High Risk Order",
        "approvalDescription": "This order has been flagged as high risk. Please review.",
        "approvalData": "${orderData}",
        "timeoutMinutes": "60",
        "timeoutAction": "continue"
      },
      "position": {"left": "470px", "top": "250px"},
      "node_id": "temp_node_4"
    },
    {
      "type": "add_node",
      "node_type": "Alert",
      "label": "Auto-Approve Low Risk",
      "config": {
        "alertType": "email",
        "recipients": "orders@company.com",
        "messageTemplate": "Order ${orderId} auto-approved (low risk)"
      },
      "position": {"left": "470px", "top": "80px"},
      "node_id": "temp_node_5"
    },
    {
      "type": "connect_nodes",
      "from": "temp_node_1",
      "to": "temp_node_2",
      "connection_type": "pass"
    },
    {
      "type": "connect_nodes",
      "from": "temp_node_2",
      "to": "temp_node_3",
      "connection_type": "pass"
    },
    {
      "type": "connect_nodes",
      "from": "temp_node_3",
      "to": "temp_node_4",
      "connection_type": "pass"
    },
    {
      "type": "connect_nodes",
      "from": "temp_node_3",
      "to": "temp_node_5",
      "connection_type": "fail"
    },
    {
      "type": "set_start_node",
      "node_id": "temp_node_1"
    }
  ],
  "explanation": "Created a workflow that assesses order risk and requires manager approval for high-risk orders"
}
```

===========================
WHEN TO GENERATE JSON vs EXPLAIN
===========================

Generate JSON commands when user says:
- "Create a workflow..."
- "Build a workflow..."
- "Add a [node type]..."
- "Make a workflow that..."
- "Connect [node] to [node]"
- "Set up a workflow for..."

Explain in words when user asks:
- "How do I...?"
- "What is a [node type]...?"
- "Can you explain...?"
- "Why would I use...?"
- "Show me an example of..." (show config in markdown, not JSON commands)

===========================
IMPORTANT NOTES
===========================

1. **Always wrap JSON in code blocks:**
   ```json
   {
     "action": "build_workflow",
     ...
   }
   ```

2. **Complete configurations:**
   Don't leave config objects empty or incomplete

3. **Realistic spacing:**
   Use appropriate left/top increments based on workflow complexity

4. **Error handling:**
   Consider adding fail connections for critical nodes

5. **Start node:**
   Always set exactly one node as the start

6. **Sequential IDs:**
   Use temp_node_1, temp_node_2, etc. in order

7. **Connection validation:**
   Ensure all "to" nodes exist before creating connections

CURRENT WORKFLOW STATE:
The user's current workflow state will be provided with each question.
Use this to give specific, contextual advice and to build upon their existing workflow.
"""

WORKFLOW_ASSISTANT_SYSTEM_PROMPT = """You are a Workflow Designer Assistant that can explain workflows AND generate workflow commands.

CRITICAL OUTPUT FORMAT:
When generating workflow commands, you MUST wrap the JSON in markdown code fences:

```json
{
  "action": "build_workflow",
  "commands": [...]
}
```

DO NOT output raw JSON without code fences.
DO NOT output multiple versions of the JSON.
DO NOT add explanatory text before or after the JSON block.

WHEN USER ASKS TO BUILD/CREATE/ADD/MODIFY, respond with JSON:

```json
{
  "action": "build_workflow",
  "commands": [
    {
      "type": "add_node",
      "node_type": "Database",
      "label": "Query Users",
      "config": {
        "dbOperation": "query",
        "query": "SELECT * FROM users",
        "outputVariable": "results",
        "saveToVariable": true
      },
      "position": {"left": "20px", "top": "40px"},
      "node_id": "temp_node_1"
    },
    {
      "type": "connect_nodes",
      "from": "temp_node_1",
      "to": "temp_node_2",
      "connection_type": "pass"
    },
    {
      "type": "set_start_node",
      "node_id": "temp_node_1"
    },
    {
      "type": "update_node_config",
      "node_id": "node-0",
      "config": {
        "query": "SELECT * FROM updated_table"
      }
    }
  ],
  "explanation": "What was built"
}
```

COMMAND TYPES:
- add_node: Create new nodes
- connect_nodes: Connect two nodes
- set_start_node: Mark a node as workflow start
- update_node_config: Update existing node's configuration (DO NOT create new node)
- add_variable: Define workflow variables

NODE TYPES & CONFIGS:

**Database**: {"dbOperation": "query", "query": "SELECT * FROM table WHERE x='${var}'", "outputVariable": "results", "saveToVariable": true}

**AI Action**: {"agent_id": "31", "prompt": "Analyze: ${data}", "outputVariable": "response", "continueOnError": false}

**Document**: {"documentAction": "process", "sourceType": "file|variable", "sourcePath": "${file}", "outputType": "variable", "outputPath": "docData", "outputFormat": "json", "forceAiExtraction": true}

**Loop**: {"sourceType": "auto", "loopSource": "${array}", "itemVariable": "item", "indexVariable": "index", "maxIterations": "100"}

**End Loop**: {"loopNodeId": "temp_node_X"} - MUST match Loop node_id, connect back with "complete"

**Conditional**: {"conditionType": "comparison", "leftValue": "${var}", "operator": "==", "rightValue": "value"}

**Human Approval**: {"assignee": "email@company.com", "approvalTitle": "Review", "approvalDescription": "Details", "approvalData": "${data}", "timeoutMinutes": "60"}

**Alert**: {"alertType": "email", "recipients": "email@company.com", "messageTemplate": "Message: ${var}"}

**Folder Selector**: {"folderPath": "\\\\server\\path", "selectionMode": "first|all", "filePattern": "*.*", "outputVariable": "files", "failIfEmpty": true}

**File**: {"operation": "read|write|copy|move", "filePath": "${path}", "outputVariable": "content"}

**Set Variable**: {"variableName": "var", "valueSource": "direct", "valueExpression": "value"}

CRITICAL RULES:
1. Position format: {"left": "20px", "top": "40px"} - MUST include "px"
2. Spacing: Start at left: "20px", increment by 160-200px horizontally
3. outputVariable: Plain name WITHOUT ${} - e.g., "results" not "${results}"
4. Variable references IN configs: Use ${varName} - e.g., query: "WHERE id='${orderId}'"
5. Connection types: Lowercase "pass", "fail", "complete"
6. Loop + End Loop: Every Loop needs End Loop with matching loopNodeId
7. Always set one start node
8. Use temp_node_1, temp_node_2, etc. sequentially for NEW nodes
9. Use actual node IDs (node-0, node-1) for EXISTING nodes from workflow state

UPDATING EXISTING NODES:
When user asks to "change", "update", "modify", or "edit" an existing node:
- Use update_node_config command with actual node ID
- DO NOT create a new node
- Only include config fields that need to change

Example - "Change the database query":
```json
{
  "action": "build_workflow",
  "commands": [
    {
      "type": "update_node_config",
      "node_id": "node-1",
      "config": {
        "query": "SELECT * FROM new_table"
      }
    }
  ],
  "explanation": "Updated database query"
}
```

REFERENCING EXISTING NODES:
When connecting to existing nodes:
1. Check workflow_context for existing node IDs
2. Use ACTUAL node IDs (node-0, node-1), NOT temp_node_X
3. Only use temp_node_X for NEW nodes created in THIS command

Example - Connect to existing node:
```json
{
  "commands": [
    {
      "type": "add_node",
      "node_id": "temp_node_1",
      ...
    },
    {
      "type": "connect_nodes",
      "from": "node-0",        // Existing node
      "to": "temp_node_1",     // New node
      "connection_type": "pass"
    }
  ]
}
```

EXAMPLES:

Simple query:
```json
{
  "action": "build_workflow",
  "commands": [
    {"type": "add_node", "node_type": "Database", "label": "Get Orders", "config": {"dbOperation": "query", "query": "SELECT * FROM orders", "outputVariable": "orders", "saveToVariable": true}, "position": {"left": "20px", "top": "40px"}, "node_id": "temp_node_1"},
    {"type": "set_start_node", "node_id": "temp_node_1"}
  ],
  "explanation": "Query orders workflow"
}
```

Update existing node:
```json
{
  "action": "build_workflow",
  "commands": [
    {"type": "update_node_config", "node_id": "node-0", "config": {"prompt": "New prompt text"}}
  ],
  "explanation": "Updated AI Action prompt"
}
```

WHEN TO USE:
Generate JSON for: "create", "build", "add", "make", "connect", "change", "update", "modify", "edit"
Explain in words for: "how do I", "what is", "explain", "why"

Always wrap JSON in ```json code blocks.
"""

WORKFLOW_DOCUMENTATION = """
# Workflow Designer Comprehensive Guide

## Table of Contents
1. [Overview](#overview)
2. [Core Concepts](#core-concepts)
3. [Workflow Variables](#workflow-variables)
4. [Connection Types](#connection-types)
5. [Node Types Reference](#node-types-reference)
6. [Common Patterns and Best Practices](#common-patterns-and-best-practices)
7. [Troubleshooting](#troubleshooting)

---

## Overview

The Workflow Designer is a visual automation tool that allows users to create complex business processes by connecting different types of nodes. Each node performs a specific action, and data flows between nodes through workflow variables and connections.

### Key Architecture Principles

- **Backend Execution**: All workflow logic executes server-side in Python
- **Visual Configuration**: Frontend provides a drag-and-drop interface for designing workflows
- **Data Flow**: Data passes between nodes via a variables dictionary, not direct return values
- **Connection-Based Routing**: Workflow flow is controlled by connection types (PASS, FAIL, COMPLETE)

---

## Core Concepts

### Workflow Structure

A workflow consists of:
- **Nodes**: Individual action units (Database queries, File operations, Conditionals, etc.)
- **Connections**: Links between nodes that define execution flow
- **Variables**: Data storage mechanism for passing information between nodes
- **Start Node**: The designated entry point (marked with a star icon)

### Execution Flow

1. Workflow execution begins at the Start Node
2. Each node executes and stores results in `_previousStepOutput`
3. Connections determine the next node based on success/failure
4. Execution continues until no more connections exist or workflow is terminated

### Data Flow Model

```
Node A executes → Result stored in variables['_previousStepOutput']
              → Node B reads from _previousStepOutput
              → Node B executes → Updates _previousStepOutput
              → Node C reads new _previousStepOutput value
```

**Critical Rule**: Each node's output overwrites `_previousStepOutput`. To preserve data from earlier nodes, store it in named variables.

---

## Workflow Variables

### Variable Types

#### 1. System Variables
- `_previousStepOutput`: Automatically updated with each node's execution result
- Cannot be manually set or defined by users
- Always contains the most recent node's output data

#### 2. User-Defined Variables
- Created in the Variables Manager (Variables button in toolbar)
- Persist throughout workflow execution
- Can store any data type: strings, numbers, objects, arrays

### Variable Syntax

Variables use the `${variableName}` syntax in node configurations:

```
Example: ${userEmail}
         ${invoice_number}
         ${_previousStepOutput}
```

### Defining Variables

1. Click the "Variables" button in the toolbar
2. Click "Add Variable"
3. Enter variable name (alphanumeric, no spaces)
4. Set data type: String, Number, Boolean, Object, or Array
5. Set default value (optional)
6. Click "Save Variable"

### Using Variables in Nodes

Variables can be referenced in most node configuration fields:

**Direct Reference:**
```
File Path: ${outputFolder}/report.pdf
Email Body: Hello ${userName}, your order ${orderId} is ready.
SQL Query: SELECT * FROM orders WHERE user_id = ${userId}
```

**Nested Object Access:**
```
${_previousStepOutput.data.results[0].value}
${userProfile.contact.email}
```

### Setting Variable Values

Use the **Set Variable** node to assign values to variables during workflow execution:

1. Add a Set Variable node
2. Select the target variable from dropdown
3. Choose value source:
   - **Direct Value**: Type a literal value or expression
   - **Previous Output**: Extract from `_previousStepOutput` using a path
4. Optionally enable "Evaluate as Expression" for Python eval() calculations

**Example - Set from previous output:**
```
Variable: processedCount
Value Source: Previous Output
Output Path: data.items.length
```

**Example - Set from expression:**
```
Variable: totalPrice
Value Source: Direct Value
Value: ${quantity} * ${unitPrice}
Evaluate as Expression: ✓
```

### Output Variables

Many nodes support saving their output to a named variable:
- **Database Node**: Check "Save Output to Variable" and specify variable name
- **File Node**: Check "Save to Variable" to store file content
- **Folder Selector Node**: Saves selected file path(s) to specified variable
- **AI Action Node**: Can save AI response to a variable
- **AI Extract Node**: Saves extracted structured data to a variable
- **Excel Export Node**: Saves file path and row count to a variable
- **Integration Node**: Can save operation result to a variable

This prevents output from being overwritten by subsequent nodes.

---

## Connection Types

Connections control workflow routing based on node execution results. Right-click a connection to change its type.

### PASS (Green)
- **Purpose**: Path taken when node executes successfully
- **Color**: Green
- **Usage**: Default connection type for normal flow
- **Example**: Database query succeeds → Follow PASS path

### FAIL (Red)
- **Purpose**: Path taken when node encounters an error
- **Color**: Red
- **Usage**: Error handling, alternative flows
- **Example**: File not found → Follow FAIL path to error notification

### COMPLETE (Blue)
- **Purpose**: Always follows this path regardless of success/failure
- **Color**: Blue
- **Usage**: Cleanup operations, final steps, Human Approval completion
- **Example**: After Human Approval (approved or rejected) → Follow COMPLETE path

### Connection Rules

1. **Multiple Connections**: A node can have multiple outgoing connections of different types
2. **Priority**: If both PASS and COMPLETE exist, execution follows the appropriate one
3. **Loop Nodes**: Loop nodes use PASS for loop body and COMPLETE for post-loop
4. **Conditional Nodes**: Evaluation result determines PASS (true) or FAIL (false) path
5. **Human Approval**: Use PASS (approved), FAIL (rejected), and COMPLETE (either outcome)

---

## Node Types Reference

### Database Node

**Purpose**: Execute database operations including queries, stored procedures, and CRUD operations.

**Configuration:**

| Field | Description | Example |
|-------|-------------|---------|
| Database Operation (`dbOperation`) | Type of operation | query, procedure, select, insert, update, delete |
| Database Connection (`connection`) | Saved connection ID | Use connection ID from available connections |
| SQL Query (`query`) | SQL statement to execute | `SELECT * FROM users WHERE id = ${userId}` |
| Stored Procedure (`procedure`) | Procedure name (for procedure operation) | `GetCustomerOrders` |
| Parameters (`parameters`) | JSON array of procedure parameters | `[${customerId}, "active"]` |
| Table Name (`tableName`) | Table for select/insert/update/delete | `orders` |
| Columns (`columns`) | Columns for select (default `*`) | `id, name, email` |
| Where Clause (`whereClause`) | WHERE condition for select/update/delete | `status = 'active'` |
| Save Output to Variable (`saveToVariable`) | Store results in named variable | ✓ |
| Output Variable (`outputVariable`) | Variable name for results | `queryResults` |
| Continue on Error (`continueOnError`) | Keep going if query fails | ✓ |

**Supported Operations:**
- **query**: Execute raw SQL query (SELECT, or any SQL statement)
- **procedure**: Call stored procedure with JSON parameters
- **select**: Build SELECT query from columns, table, and WHERE clause
- **insert**: Insert data into table from direct JSON, variable, or previous step
- **update**: Update table rows matching WHERE clause
- **delete**: Delete rows from table matching WHERE clause

**Variable Usage:**
```sql
-- Using variables in queries
SELECT * FROM orders
WHERE customer_id = ${customerId}
  AND status = '${orderStatus}'

-- Stored procedure with parameters
EXEC GetCustomerOrders @CustomerId = ${customerId}
```

**Output Structure:**
```json
{
  "success": true,
  "data": [
    {"column1": "value1", "column2": "value2"},
    {"column1": "value3", "column2": "value4"}
  ]
}
```

**Best Practices:**
- Always use parameterized queries to prevent SQL injection
- Save complex query results to variables for reuse
- Use FAIL connections to handle query errors
- Test queries in database client before adding to workflow

---

### File Node

**Purpose**: Perform file system operations including read, write, append, check existence, delete, copy, and move files.

**Configuration:**

| Field | Description | Example |
|-------|-------------|---------|
| File Operation (`operation`) | Type of operation | read, write, append, check, delete, copy, move |
| File Path (`filePath`) | Path to the file | `/data/reports/${reportDate}.txt` |
| Destination Path (`destinationPath`) | Target path for copy/move | `/archive/${fileName}` |
| Content Source (`contentSource`) | Where content comes from (write/append) | direct, variable, previous |
| Content (`content`) | Text to write (if direct) | `Report generated: ${timestamp}` |
| Content Variable (`contentVariable`) | Variable with content (if variable) | `${reportContent}` |
| Save to Variable (`saveToVariable`) | Store file content in variable | ✓ |
| Output Variable (`outputVariable`) | Variable name for results | `fileContent` |
| Continue on Error (`continueOnError`) | Keep going if operation fails | ✓ |

**Operations:**

**read** - Reads file content as text string, can save to variable
**write** - Creates new file or overwrites existing with content from direct input, variable, or previous step
**append** - Adds content to end of existing file, creates file if it doesn't exist
**check** - Verifies if file exists, returns boolean in output
**delete** - Removes file from filesystem (cannot undo)
**copy** - Duplicates file to destination path
**move** - Relocates file to destination path, removes from original location

**Output Structure:**
```json
{
  "success": true,
  "data": {
    "operation": "read",
    "filePath": "/data/file.txt",
    "content": "file contents here",
    "size": 1024
  }
}
```

---

### Folder Selector Node

**Purpose**: Select files from a directory based on different selection strategies.

**Configuration:**

| Field | Description | Example |
|-------|-------------|---------|
| Folder Path (`folderPath`) | Directory to search | `/uploads/${customerId}/invoices` |
| Selection Mode (`selectionMode`) | How to select files | all, pattern, first, latest, largest, smallest, random |
| File Pattern (`filePattern`) | Pattern to match files | `*.pdf` or `invoice_*.xlsx` |
| Output Variable (`outputVariable`) | Variable to store result | `selectedFiles` |
| Fail if Empty (`failIfEmpty`) | Fail workflow if no files found | ✓ |

**Selection Modes:**

- **all**: Returns all matching files as an array (use with Loop node to process each)
- **pattern**: Match files using wildcard pattern (supports pipe or comma-delimited multiple patterns)
- **first**: Select first file alphabetically
- **latest**: Select most recently modified file
- **largest**: Select largest file by size
- **smallest**: Select smallest file by size
- **random**: Select a random file

**Output Structure:**
```json
{
  "success": true,
  "data": {
    "folderPath": "/uploads/2024/01",
    "filesFound": true,
    "selectedFile": "/uploads/2024/01/invoice_001.pdf",
    "allFiles": ["/uploads/2024/01/invoice_001.pdf", "/uploads/2024/01/invoice_002.pdf"]
  }
}
```

**Best Practices:**
- Always set Output Variable to preserve selected file path
- Use FAIL path to handle "no files found" scenario (when failIfEmpty is true)
- Use "all" selection mode with Loop node to process multiple files
- Supports network paths (use double backslash for UNC paths)

---

### Document Node

**Purpose**: Process documents (PDF, DOCX) to extract raw text content or analyze with AI.

**Configuration:**

| Field | Description | Example |
|-------|-------------|---------|
| Document Action (`documentAction`) | Type of operation | process, extract, analyze, save |
| Source Type (`sourceType`) | Where document comes from | file, variable, previous |
| Source Path (`sourcePath`) | Path or variable name | `/docs/${documentId}.pdf` or `${docPath}` |
| Output Type (`outputType`) | Where to store result | variable, file, return |
| Output Path (`outputPath`) | Variable name or file path | `extractedText` |
| Output Format (`outputFormat`) | Format for output | json, csv, text |
| Force AI Extraction (`forceAiExtraction`) | Use AI for extraction | ✓ |

**Operations:**

- **process**: Process document and extract text content
- **extract**: Extract structured fields from document
- **analyze**: Analyze document content with AI
- **save**: Create/save a document

**Note**: For extracting structured data with predefined fields from documents, consider using the **AI Extract** node instead. AI Extract can process PDF/DOCX files directly and extract named fields in a single step, which is more efficient than Document → AI Action for structured extraction.

**Output Structure:**
```json
{
  "success": true,
  "data": {
    "document_text": "extracted document text...",
    "pageCount": 5,
    "documentType": "pdf"
  }
}
```

---

### AI Action Node

**Purpose**: Send prompts to AI agents for flexible text generation, analysis, summarization, and other AI-powered tasks.

**Configuration:**

| Field | Description | Example |
|-------|-------------|---------|
| Agent (`agent_id`) | AI agent to use (select from available agents) | Use agent ID from available agents |
| Prompt (`prompt`) | Instructions for AI | `Summarize this document: ${documentText}` |
| Output Variable (`outputVariable`) | Variable to store AI response | `aiSummary` |
| Continue on Error (`continueOnError`) | Keep going if AI call fails | ✓ |

**Prompt Design:**

Use variables to create dynamic prompts. The special placeholder `{prev_output}` injects the previous step's output:
```
Analyze the following resume and extract:
- Name
- Email
- Years of experience
- Key skills

Resume text:
${resumeText}

Return results as JSON.
```

**Output Structure:**
```json
{
  "success": true,
  "data": {
    "response": "AI generated text response...",
    "chatHistory": [...]
  }
}
```

**Best Practices:**
- Be specific in prompts for consistent results
- Always save output to variable if you need it later
- Handle potential FAIL path for API errors
- For structured data extraction with named fields, consider AI Extract instead

---

### AI Extract Node

**Purpose**: Extract structured data from text OR documents using AI with predefined field schemas. Can process PDF/DOCX files directly without needing a Document node first.

**CRITICAL**: The `fields` array is ALWAYS REQUIRED - it defines what data to extract.

**Configuration:**

| Field | Description | Example |
|-------|-------------|---------|
| Input Source (`inputSource`) | Source type | auto (recommended), text, document |
| Input Variable (`inputVariable`) | Variable with text or file path | `${documentPath}` or `${rawText}` |
| Output Variable (`outputVariable`) | Variable for extracted data | `extractedData` |
| Special Instructions (`specialInstructions`) | AI guidance text | "Return numbers without currency symbols" |
| Fail on Missing Required (`failOnMissingRequired`) | Fail if required fields missing | ✓ |
| Fields (`fields`) | Array of field definitions | See below |

**Field Definitions:**

Each field in the `fields` array contains:
- `name`: Field name (letters, numbers, underscores only, must start with letter or underscore)
- `type`: One of `text`, `number`, `boolean`, `list`, `group`, `repeated_group`
- `required`: Boolean
- `description`: Description to guide extraction
- `children`: Array of child field definitions (only for `group` and `repeated_group` types)

**Output Destinations:**

| Destination (`outputDestination`) | Description |
|-----------------------------------|-------------|
| `variable` (default) | Store extracted data as structured object in variable |
| `excel_new` | Create new Excel file with extracted data |
| `excel_template` | Copy template and populate with extracted data |
| `excel_append` | Add new row to existing Excel file |

**Excel Output Configuration (when outputDestination is not "variable"):**

| Field | Description |
|-------|-------------|
| `excelOutputPath` | Path for output file (supports variables like `/output/${customer}_data.xlsx`) |
| `excelTemplatePath` | Template file path (required for excel_template and excel_append) |
| `excelSheetName` | Optional target sheet name |
| `mappingMode` | `ai` (auto-mapping) or `manual` |
| `aiMappingInstructions` | Instructions for AI column mapping |
| `fieldMapping` | Manual mapping object: `{"field_name": "Excel Column"}` |

**Optional Metadata Fields:**
- `includeConfidence`: Add confidence scores per field
- `includeAssumptions`: Add AI reasoning per field
- `includeSources`: Add source page numbers per field

**Output Structure:**
```json
{
  "success": true,
  "data": {
    "extraction": {
      "customer_name": "Acme Corp",
      "invoice_total": 1500.00,
      "line_items": [
        {"description": "Widget A", "quantity": 10, "price": 100.00}
      ]
    },
    "mode": "document",
    "excel": {
      "file_path": "/output/data.xlsx",
      "rows_written": 1,
      "sheet_name": "Sheet1"
    }
  }
}
```

**Output Access:** Use dollar-brace with dot notation: `${extractedData.customer_name}` or `${extractedData.line_items[0].price}`

**Key Advantages over AI Action:**
- Predictable, named fields for downstream logic (conditionals, data mapping)
- Can process PDF/DOCX files directly (pass file path as inputVariable)
- More efficient than Document → AI Action for structured extraction
- Built-in Excel output support

---

### Excel Export Node

**Purpose**: Write variable data directly to Excel files. A standalone export node separate from AI Extract's Excel output, offering more control over data mapping and update operations.

**Configuration:**

| Field | Description | Example |
|-------|-------------|---------|
| Input Variable (`inputVariable`) | Variable containing data to export | `${extractedData}` |
| Flatten Array (`flattenArray`) | Each array item becomes a separate row | ✓ |
| Carry Forward Fields (`carryForwardFields`) | Fields to include from parent context in each row | `record_id, customer_name` |
| Excel Operation (`excelOperation`) | How to write to Excel | new, template, append, update |
| Excel Output Path (`excelOutputPath`) | Path for output file | `/output/${customer}_data.xlsx` |
| Excel Template Path (`excelTemplatePath`) | Source template file | `/templates/report.xlsx` |
| Sheet Name (`excelSheetName`) | Target sheet name | `Data` |
| Mapping Mode (`mappingMode`) | How to map fields to columns | ai, manual |
| AI Mapping Instructions (`aiMappingInstructions`) | Guide AI mapping | "Map 'topic' to 'Category' column" |
| Field Mapping (`fieldMapping`) | Manual field-to-column mapping | `{"vendor_name": "VENDOR", "total": "AMOUNT"}` |

**Excel Operations:**

- **new**: Create a new Excel file with data
- **template**: Create new file from template and populate
- **append**: Add rows to existing file (most common, this is the default)
- **update**: Intelligently update existing rows by key columns, add new rows, optionally track deleted rows

**UPDATE Operation Configuration (when excelOperation is "update"):**

| Field | Description |
|-------|-------------|
| `keyColumns` | Comma-separated column names that uniquely identify rows (required) |
| `useAIKeyMatching` | Enable AI-assisted semantic key matching |
| `aiKeyMatchingInstructions` | Guide AI key matching behavior |
| `useSmartChangeDetection` | Only update rows when meaning has actually changed |
| `smartChangeStrictness` | `strict` (preserves nuance like must vs should) or `lenient` (facts only) |
| `highlightChanges` | Highlight changed cells with color |
| `trackDeletedRows` | Mark rows not in new data as deleted |
| `addChangeTimestamp` | Add/update timestamp column on changed rows |
| `timestampColumn` | Name of the timestamp column (default: "Last Updated") |
| `changeLogSheet` | Optional sheet name for change history log |

**Output Structure:**
```json
{
  "success": true,
  "data": {
    "file_path": "/output/report.xlsx",
    "rows_written": 15,
    "sheet_name": "Sheet1",
    "rows_updated": 3,
    "rows_added": 2,
    "rows_deleted": 1,
    "rows_skipped_semantic": 5,
    "cells_changed": 8
  }
}
```

**Common Patterns:**
- **Loop + AI Extract + Excel Export**: Process multiple files, extract data, append each to Excel
- **Database + Excel Export**: Query database, export results to Excel report
- **Document Re-processing + UPDATE**: Re-extract from documents and intelligently update existing Excel data (use AI Key Matching for semantic variations, Smart Change Detection to avoid noise updates)
- **Compliance Tracking + UPDATE**: Track vendor requirements over time with change history (use strict mode where "must" vs "should" matters)

**Key Advantages over AI Extract Excel Output:**
- Can export any variable data, not just extraction results
- Supports carry-forward fields from parent context
- Supports intelligent UPDATE operations with AI-assisted matching
- More explicit control over column mapping

---

### Set Variable Node

**Purpose**: Set or calculate workflow variables during execution.

**Configuration:**

| Field | Description | Example |
|-------|-------------|---------|
| Variable Name (`variableName`) | Variable to set | `processedCount` |
| Value Source (`valueSource`) | Where to get value | direct, output |
| Value/Expression (`valueExpression`) | Literal value or expression | `${itemCount} * 2` |
| Output Path (`outputPath`) | Path in previous output (when source is "output") | `data.results.length` |
| Evaluate as Expression (`evaluateAsExpression`) | Evaluate using Python eval() | ✓ |

**IMPORTANT**: Expression evaluation uses **Python eval()**, NOT JavaScript. All workflow variables are available in the eval context, along with the `math`, `json`, and `re` modules.

**Expression Limitations**: eval() only supports expressions, NOT statements. You cannot use: `def`, `for`, `if/else` blocks, `import`, `class`, or assignments. For complex logic, use single-line expressions like dict/list comprehensions, ternary operators, or built-in functions like `next()`, `filter()`, `map()`.

**Use Cases:**

**1. Set Literal Value:**
```
Variable Name: status
Value Source: Direct Value
Value: "processing"
```

**2. Set from Previous Output:**
```
Variable Name: recordCount
Value Source: Previous Output
Output Path: data.rowCount
```

**3. Calculate Value (Python expression):**
```
Variable Name: totalPrice
Value Source: Direct Value
Value: ${quantity} * ${unitPrice}
Evaluate as Expression: ✓
```

**4. Extract Nested Data:**
```
Variable Name: customerEmail
Value Source: Previous Output
Output Path: data.customer.contact.email
```

**Output Structure:**
```json
{
  "success": true,
  "data": {
    "variableName": "totalPrice",
    "value": 250.00,
    "evaluated": true
  }
}
```

**Best Practices:**
- Use descriptive variable names
- Set variables before nodes that need them
- Extract important data from complex outputs immediately
- Use expressions for calculations instead of separate nodes

---

### Conditional Node

**Purpose**: Branch workflow execution based on conditions (if/then/else logic).

**Configuration:**

| Field | Description | Example |
|-------|-------------|---------|
| Condition Type (`conditionType`) | Type of check | comparison, expression, contains, exists, empty |
| Left Value (`leftValue`) | First operand (for comparison) | `${orderTotal}` |
| Operator (`operator`) | Comparison operator | ==, !=, >, <, >=, <= |
| Right Value (`rightValue`) | Second operand (for comparison) | `1000` |
| Expression (`expression`) | Expression to evaluate (for expression type) | `${price} > 100 and ${qty} > 5` |
| Contains Text (`containsText`) | Text to search in (for contains) | `${email}` |
| Search Text (`searchText`) | Substring to find (for contains) | `@company.com` |
| Exists Variable (`existsVariable`) | Variable to check (for exists) | `${customerId}` |
| Empty Variable (`emptyVariable`) | Variable to check (for empty) | `${searchResults}` |

**Condition Types:**

**comparison** - Compare two values using operators (==, !=, >, >=, <, <=). Values are auto-evaluated (tries int/float/bool/JSON parse).

**expression** - Evaluate a Python-like expression that returns true/false.

**contains** - Check if text contains a substring (case-sensitive).

**exists** - Check if a variable is defined and not None.

**empty** - Check if a variable is empty, null, or undefined. Works with strings, arrays, objects.

**Connection Flow:**
- **TRUE (PASS connection)**: Condition evaluates to true
- **FALSE (FAIL connection)**: Condition evaluates to false
- On error, defaults to FAIL path

**Examples:**

```
Check order value:
  Condition Type: comparison
  Left Value: ${orderTotal}
  Operator: >
  Right Value: 1000

  → PASS: orderTotal > 1000 (high-value order)
  → FAIL: orderTotal <= 1000 (regular order)

Check if file uploaded:
  Condition Type: exists
  Exists Variable: ${uploadedFile}

  → PASS: Variable exists
  → FAIL: Variable not defined

Check if results empty:
  Condition Type: empty
  Empty Variable: ${searchResults}

  → PASS: Variable is empty/null
  → FAIL: Variable has content
```

**Best Practices:**
- Always have both PASS and FAIL paths
- Use clear, descriptive conditions
- Test edge cases (empty values, null, undefined)
- Use expression type for complex multi-condition checks

---

### Loop Node

**Purpose**: Iterate over arrays or collections, executing a series of nodes for each item.

**Configuration:**

| Field | Description | Example |
|-------|-------------|---------|
| Source Type (`sourceType`) | How to get the array | auto, variable, path, folderFiles, split |
| Loop Source (`loopSource`) | Variable or path containing array | `${queryResults}` |
| Split Delimiter (`splitDelimiter`) | Delimiter for split mode | `,` |
| Item Variable (`itemVariable`) | Name for current item | `currentItem` |
| Index Variable (`indexVariable`) | Name for loop index | `currentIndex` |
| Max Iterations (`maxIterations`) | Safety limit | `100` |
| Empty Behavior (`emptyBehavior`) | What to do if array is empty | skip, fail, default |

**Source Types:**

- **auto**: Auto-detect array from previous output (recommended)
- **variable**: Use explicit variable reference
- **path**: Navigate nested path (e.g., `${extractedNotes.Notes}`)
- **folderFiles**: Extract from Folder Selector's allFiles output
- **split**: Split a string into array by delimiter

**Loop Structure:**

```
Loop Node (Start)
    ↓ PASS (loop body)
[Nodes to execute per item]
    ↓
End Loop Node
    ↓ COMPLETE (after all iterations)
Next Node
```

**How It Works:**

1. Loop Node extracts array from source
2. For each item:
   - Sets `${itemVariable}` to current item
   - Sets `${indexVariable}` to current index (0-based)
   - Executes loop body (PASS connection path)
3. After all iterations:
   - Follows COMPLETE connection
   - Results available in `_previousStepOutput`

**Output Structure (after loop completion):**
```json
{
  "success": true,
  "data": {
    "loopResults": [
      {"success": true, "data": {...}},
      {"success": true, "data": {...}}
    ],
    "itemsProcessed": 10,
    "totalIterations": 10
  }
}
```

**Best Practices:**
- Always set Max Iterations to prevent infinite loops
- Use descriptive item/index variable names
- Use End Loop node to mark loop body end
- Handle errors in loop body with FAIL paths
- Use PASS connection for loop body, COMPLETE for after-loop path

---

### End Loop Node

**Purpose**: Marks the end of a loop body and returns control to the Loop node.

**Configuration:**

| Field | Description | Example |
|-------|-------------|---------|
| Loop Node ID (`loopNodeId`) | Associated Loop node | Auto-detected |
| Completion Message (`completionMessage`) | Optional message | "Loop completed successfully" |

**Important Notes:**

- **Automatic Detection**: Usually auto-detects which Loop node it belongs to
- **Loop Body Exit**: Place this at the end of nodes to execute per iteration
- **Results Collection**: Automatically collects results from each iteration

**Placement:**

```
CORRECT:
Loop → Node A → Node B → End Loop
                              ↓ COMPLETE
                           Node C

INCORRECT:
Loop → Node A → End Loop → Node B
(Node B would execute per iteration AND after loop)
```

---

### Human Approval Node

**Purpose**: Pause workflow execution pending manual approval by a user or group.

**Configuration:**

| Field | Description | Example |
|-------|-------------|---------|
| Approval Title (`approvalTitle`) | Short description | "Approve Invoice Payment" |
| Approval Description (`approvalDescription`) | Detailed instructions | "Review invoice #${invoiceNumber} for $${amount}" |
| Assignee Type (`assigneeType`) | Who approves | user, group, unassigned |
| Assignee ID (`assigneeId`) | Specific user or group ID | Select from dropdown |
| Priority (`priority`) | Priority level | 0 (Normal), 1 (High), 2 (Urgent) |
| Due Hours (`dueHours`) | Hours until timeout | `24` |
| Timeout Action (`timeoutAction`) | What happens on timeout | continue (auto-approve), fail |
| Approval Data (`approvalData`) | Data to show to approver | `${orderDetails}` |

**Approval Flow:**

1. Node creates approval request in database
2. Workflow execution pauses at this node
3. Assigned user/group receives notification
4. Approver reviews and approves/rejects via UI
5. Workflow resumes with appropriate path:
   - **PASS**: Approved
   - **FAIL**: Rejected
   - **COMPLETE**: Either outcome (use for cleanup)

**Timeout Behavior:**

If no action taken within timeout period:
- **continue (Auto-Approve)**: Follows PASS path
- **fail**: Follows FAIL path and fails the workflow

**Output Structure:**
```json
{
  "success": true,
  "data": {
    "status": "Approved",
    "comments": "Looks good, approved.",
    "responded_by": "john.doe"
  }
}
```

**Best Practices:**
- Provide clear, specific approval descriptions
- Include relevant data in description using variables
- Set reasonable timeout periods
- Always define both PASS and FAIL paths
- Use COMPLETE path for logging or notification regardless of outcome
- Assign to groups for redundancy (any member can approve)

---

### Alert Node

**Purpose**: Send notifications to users via email, text message, or phone call.

**Configuration:**

| Field | Description | Example |
|-------|-------------|---------|
| Alert Type (`alertType`) | Notification method | email, text, call |
| Recipients (`recipients`) | Who to notify | `${managerEmail}, alerts@company.com` |
| Message (`messageTemplate`) | Notification content with variables | `Order ${orderId} completed for ${customerName}` |
| Continue on Error (`continueOnError`) | Keep going if alert fails | ✓ |

**Alert Types:**

- **email**: Send email to one or more comma-separated addresses. Automatically detects HTML content if message contains `<html` tag. Sets message body only; does NOT support file attachments.
- **text**: Send SMS text message to a single phone number.
- **call**: Make a phone call with a spoken message to a single phone number.

**Output Structure:**
```json
{
  "success": true,
  "data": {
    "alert_type": "email",
    "recipients": ["user@company.com", "alerts@company.com"],
    "success": true,
    "message": "Email sent successfully"
  }
}
```

**Best Practices:**
- Enable "Continue on Error" for non-critical alerts
- Use variables to personalize messages
- Consolidate notifications to avoid spam
- Include relevant context and data in messages

---

### Execute Application Node

**Purpose**: Run external executables, scripts, or system commands.

**Configuration:**

| Field | Description | Example |
|-------|-------------|---------|
| Command Type (`commandType`) | Type of execution | executable, script, command |
| Executable Path (`executablePath`) | Path to executable or script | `C:\\scripts\\process.py` |
| Arguments (`arguments`) | Command line arguments | `--input ${inputFile} --output ${outputFile}` |
| Working Directory (`workingDirectory`) | Where to run | `C:\\data\\processing` |
| Environment Variables (`environmentVars`) | Newline-separated KEY=VALUE pairs | `API_URL=https://...` |
| Timeout (`timeout`) | Max execution time in seconds (default 300) | `600` |
| Capture Output (`captureOutput`) | Capture stdout/stderr | ✓ |
| Success Codes (`successCodes`) | Comma-separated exit codes for success | `0, 1` |
| Fail on Error (`failOnError`) | Fail if exit code not in success codes | ✓ |
| Input Data Handling (`inputDataHandling`) | How to pass previous output | none, stdin, file, args |
| Output Parsing (`outputParsing`) | How to parse stdout | text, json, csv, regex |
| Output Regex (`outputRegex`) | Regex pattern (if parsing is regex) | `Result: (.+)` |
| Output Variable (`outputVariable`) | Variable for output | `commandResult` |
| Continue on Error (`continueOnError`) | Keep going if execution fails | ✓ |

**Output Structure:**
```json
{
  "success": true,
  "data": {
    "exit_code": 0,
    "output": "parsed output data",
    "stdout": "raw stdout text",
    "stderr": "",
    "command": "process.py"
  }
}
```

**Security Notes:**
- Executable paths are validated before execution
- Use absolute paths for security
- Set appropriate timeouts
- Never execute user-provided commands directly

---

### Integration Node

**Purpose**: Execute operations on connected external integrations (QuickBooks, Shopify, Stripe, and other third-party systems).

**Configuration:**

| Field | Description | Example |
|-------|-------------|---------|
| Integration (`integration_id`) | Connected integration to use | Select from available integrations |
| Operation (`operation`) | Operation to execute | `get_invoices`, `create_order`, etc. |
| Parameters (`parameters`) | Operation-specific parameters (dynamic form) | Varies by operation |
| Output Variable (`outputVariable`) | Variable to store result | `integrationResult` |
| Continue on Error (`continueOnError`) | Keep going if operation fails | ✓ |

**How It Works:**

1. Select a connected integration from the dropdown (integrations are configured separately in the Integrations management area)
2. Choose an operation available for that integration (operations are loaded dynamically based on the selected integration)
3. Fill in operation-specific parameters (the form adapts based on the selected operation)
4. Optionally specify an output variable to store the result

**Parameter Variable Support:**
Parameters support `${variableName}` syntax for dynamic values. Variable references are resolved at execution time, including nested paths like `${customer.id}`.

**Output Structure:**
```json
{
  "success": true,
  "data": {
    "invoices": [...],
    "total_count": 25
  },
  "response_time_ms": 342,
  "status_code": 200
}
```

**Webhook Triggers:**
Integrations can also trigger workflows via webhooks. When an external system (e.g., Shopify order created, Stripe payment received) sends a webhook, it can automatically start a workflow with the webhook payload mapped to workflow variables.

**Best Practices:**
- Use Continue on Error for non-critical integration calls
- Save results to variables for downstream processing
- Check for API rate limits on high-volume operations
- Use Conditional nodes after Integration nodes to handle different response states

---

## Common Patterns and Best Practices

### Pattern 1: File Processing Pipeline

```
1. Folder Selector → Select newest file
   Output Variable: ${inputFile}

2. File Node (Read) → Load file content
   File Path: ${inputFile}
   Output Variable: ${fileContent}

3. AI Action → Process content
   Prompt: "Process this data: ${fileContent}"
   Output Variable: ${processedData}

4. File Node (Write) → Save results
   File Path: /output/result_${timestamp}.txt
   Content Source: Variable (${processedData})

5. File Node (Move) → Archive original
   Source: ${inputFile}
   Destination: /archive/${inputFile}
```

### Pattern 2: Database Query with Error Handling

```
1. Database Node → Query data
   Query: SELECT * FROM orders WHERE date = ${processDate}
   Save Output: ✓ → ${orders}
   ↓ PASS
2. Loop Node → Process each order
   Loop Variable: ${currentOrder}
   ↓ PASS (loop body)
3. AI Action → Analyze order
   ↓ FAIL (from Database Node)
4. Alert Node → Notify error
   Message: "Database query failed: ${_previousStepOutput.error}"
```

### Pattern 3: Conditional Routing

```
1. Database Node → Get order total
   Output Variable: ${orderTotal}

2. Conditional Node → Check if high value
   Condition Type: comparison
   Left Value: ${orderTotal}
   Operator: >
   Right Value: 10000
   ↓ PASS (high value)
3. Human Approval → Require approval
   Title: "Approve high-value order ${orderId}"
   Assignee Type: group
   ↓ FAIL (regular order)
4. Alert Node → Standard processing
   Message: "Processing order ${orderId}"
```

### Pattern 4: Multi-File Document Extraction to Excel

```
1. Folder Selector → Select all PDF files
   Selection Mode: all
   File Pattern: *.pdf
   Output Variable: ${pdfFiles}

2. Loop Node → For each PDF
   Source Type: folderFiles
   Item Variable: ${currentFile}
   ↓ PASS
3. AI Extract → Extract structured data
   Input Source: auto
   Input Variable: ${currentFile}
   Fields: [vendor_name, invoice_total, due_date, ...]
   Output Variable: extractedData

4. Excel Export → Append to report
   Input Variable: ${extractedData}
   Excel Operation: append
   Excel Output Path: /output/extraction_report.xlsx
   Mapping Mode: ai

5. End Loop Node
   ↓ COMPLETE
6. Alert Node → "Processed ${_previousStepOutput.data.itemsProcessed} documents"
```

### Pattern 5: Integration-Triggered Workflow

```
[Webhook Trigger: Shopify new order]
   ↓
1. Set Variable → Extract order details
   Variable: ${orderTotal}
   Value Source: output
   Output Path: webhook_payload.total_price

2. Conditional → High-value check
   Left Value: ${orderTotal}
   Operator: >
   Right Value: 500
   ↓ PASS
3. Integration Node → Create QuickBooks invoice
   Integration: QuickBooks
   Operation: create_invoice
   ↓ FAIL
4. Alert → Notify sales team
   Type: email
   Message: "New order received: $${orderTotal}"
```

### Best Practices Summary

**Variables:**
- Define all variables upfront in Variables Manager
- Use descriptive, consistent naming (camelCase or snake_case)
- Save important outputs to named variables immediately
- Don't rely solely on `_previousStepOutput` for data you need later

**Node Configuration:**
- Test nodes individually before connecting
- Use clear, descriptive node labels
- Fill in all required fields
- Validate variable references (use variable selector button)

**Error Handling:**
- Always add FAIL paths for critical nodes
- Use Alert nodes to notify of errors
- Enable "Continue on Error" for non-critical operations
- Log errors to files or database for debugging

**Connections:**
- Use appropriate connection types (PASS/FAIL/COMPLETE)
- Test all paths in your workflow
- Avoid creating disconnected node groups
- Ensure all paths lead to completion or proper termination

**Performance:**
- Limit loop iterations with Max Iterations
- Set reasonable timeouts on long-running nodes
- Use database queries efficiently (WHERE clauses, indexes)
- Clean up temporary files and variables

**Testing:**
- Test with sample data before production
- Verify variable substitution works correctly
- Check error paths trigger appropriately
- Validate outputs at each step

**Security:**
- Never expose sensitive credentials in variables
- Validate user inputs before use
- Use parameterized database queries
- Limit file system access to necessary paths
- Review Execute Application commands carefully

---

## Troubleshooting

### Common Issues and Solutions

#### Issue: Variable not found or undefined

**Symptoms:**
- Error: "Variable 'variableName' not found"
- Empty values in node outputs

**Solutions:**
1. Verify variable is defined in Variables Manager
2. Check spelling and syntax: `${variableName}` not `$variableName`
3. Ensure variable is set before the node that uses it
4. Check if previous node actually set the variable

#### Issue: Workflow stops unexpectedly

**Symptoms:**
- Execution halts mid-workflow
- No error message in logs

**Solutions:**
1. Check if node is missing required connections
2. Verify all paths (PASS/FAIL) are defined
3. Check for infinite loops (no End Loop node)
4. Review execution logs for silent failures

#### Issue: Loop not working correctly

**Symptoms:**
- Loop executes once or not at all
- Wrong items being processed

**Solutions:**
1. Verify array path in Loop Source configuration
2. Check if source data is actually an array (not a string representation of an array)
3. Ensure End Loop node is properly placed and connected
4. Set Max Iterations to reasonable value
5. Use PASS connection for loop body, COMPLETE for after-loop
6. Try Source Type "auto" if other modes aren't finding the array

#### Issue: Previous step output is empty

**Symptoms:**
- `_previousStepOutput` is {} or null
- Cannot access expected data

**Solutions:**
1. Check if previous node executed successfully
2. Verify previous node has PASS connection to current node
3. Save output to named variable instead of relying on _previousStepOutput
4. Check node logs for execution errors

#### Issue: Database connection fails

**Symptoms:**
- "Connection timeout"
- "Authentication failed"

**Solutions:**
1. Verify database connection configured correctly
2. Check network connectivity to database
3. Verify credentials are correct
4. Ensure database server is running
5. Check firewall rules

#### Issue: File operations fail

**Symptoms:**
- "File not found"
- "Permission denied"

**Solutions:**
1. Verify file paths are absolute and correct
2. Check file permissions
3. Ensure directory exists for write operations
4. Verify variable substitution in paths works
5. Check if workflow has necessary filesystem permissions

#### Issue: Human Approval not triggering

**Symptoms:**
- Workflow doesn't pause
- Approval not created

**Solutions:**
1. Verify assignee user/group exists
2. Check database connection
3. Ensure approval node has proper connections (PASS/FAIL/COMPLETE)
4. Verify timeout settings are reasonable

#### Issue: AI Action or AI Extract node fails

**Symptoms:**
- "API timeout"
- "Invalid response"
- AI Extract returning empty fields

**Solutions:**
1. Check AI agent is properly configured
2. Verify API credentials are valid
3. Reduce prompt/document size if too large
4. Increase timeout setting
5. Check API rate limits
6. For AI Extract: verify field definitions are clear and descriptive

#### Issue: Variable substitution not working

**Symptoms:**
- Literal `${variableName}` appears in output
- Variables not replaced with values

**Solutions:**
1. Ensure correct syntax: `${variableName}` with curly braces
2. Verify variable is defined before use
3. Check variable name matches exactly (case-sensitive)
4. Use variable selector button to avoid typos

#### Issue: Excel Export writes wrong data

**Symptoms:**
- Wrong columns in output
- Missing rows or data

**Solutions:**
1. Check that inputVariable contains the expected data structure (dict or array of dicts)
2. For array data, enable flattenArray to write each item as a separate row
3. Verify field mapping matches actual field names in the data
4. Use AI mapping mode for automatic column matching
5. Check that template file exists and is accessible (for template/append operations)

### Debugging Tips

**1. Enable Debug Mode:**
- Turn on detailed logging
- Check execution logs after each node
- Review variable values at each step

**2. Test in Isolation:**
- Test individual nodes before connecting
- Use static test data initially
- Validate each node's output structure

**3. Use Set Variable Nodes:**
- Add diagnostic Set Variable nodes
- Log intermediate values
- Track workflow state at key points

**4. Check Connection Types:**
- Right-click connections to view type
- Verify PASS/FAIL routing is correct
- Ensure all outcomes have paths

**5. Review Execution Logs:**
- Check workflow execution history
- Look for error messages
- Verify execution order is correct

**6. Validate Data Structures:**
- Use browser console to inspect _previousStepOutput
- Verify array/object structures match expectations
- Check for null/undefined values

---

## Appendix: Quick Reference

### Variable Syntax
```
Reference: ${variableName}
Nested: ${object.property.subProperty}
Array: ${array[0].property}
System: ${_previousStepOutput}
```

### Connection Types Quick Guide
| Type | Color | Use Case |
|------|-------|----------|
| PASS | Green | Success path |
| FAIL | Red | Error/failure path |
| COMPLETE | Blue | Always follows regardless of outcome |

### Node Types Quick Guide
| Node Type | Category | Purpose |
|-----------|----------|---------|
| Database | Data Sources | Execute SQL queries and stored procedures |
| File | Data Sources | Read, write, copy, move, delete files |
| Folder Selector | Data Sources | Select files from directories |
| AI Action | AI/Intelligence | Flexible AI text generation and analysis |
| AI Extract | AI/Intelligence | Structured data extraction with field schemas |
| Document | AI/Intelligence | Process documents to extract raw text |
| Conditional | Flow Control | Branch workflow based on conditions |
| Loop | Flow Control | Iterate over arrays/collections |
| End Loop | Flow Control | Mark end of loop body |
| Human Approval | Flow Control | Pause for manual approval |
| Alert | Communication | Send email, text, or phone notifications |
| Set Variable | Transformation | Set or calculate workflow variables |
| Execute Application | Transformation | Run external executables/scripts |
| Excel Export | Transformation | Write data to Excel files |
| Integration | External | Execute operations on connected integrations |

### Common Node Sequences
```
Data Retrieval: Database → Set Variable → [Use Data]

File Processing: Folder Selector → File Read → Process → File Write

Approval Flow: Condition → Human Approval → [PASS/FAIL paths]

Iteration: Database → Loop → [Process Item] → End Loop → Summary

Document Extraction: Folder Selector → Loop → AI Extract → Excel Export → End Loop

Integration: Integration Node → Conditional → [Handle Response]

Error Handling: [Any Node] ─FAIL→ Alert → [Recovery or End]
```

### Variable Best Practices Checklist
- [ ] Defined in Variables Manager
- [ ] Descriptive name (no spaces)
- [ ] Correct data type set
- [ ] Default value provided (if applicable)
- [ ] Referenced with ${} syntax
- [ ] Set before use in workflow
- [ ] Saved from outputs if needed later

### Workflow Testing Checklist
- [ ] All required variables defined
- [ ] Start node marked
- [ ] All nodes have required fields filled
- [ ] All connections have correct types
- [ ] PASS and FAIL paths defined where needed
- [ ] Error handling in place
- [ ] Tested with sample data
- [ ] Execution logs reviewed
- [ ] Performance acceptable
- [ ] Security considerations addressed

---

## End of Guide

This guide covers all major aspects of the Workflow Designer. For specific technical questions or issues not covered here, refer to:
- System logs and error messages
- Database schema documentation
- API documentation for integrations
- Node-specific configuration examples

Remember: The best way to learn is by building workflows. Start simple, test frequently, and gradually add complexity.
"""

# ============================================================================
# Conversation Memory (Session-based)
# ============================================================================

class ConversationMemory:
    """Manages conversation history for workflow assistant"""
    
    def __init__(self):
        self.conversations = {}
        self.lock = threading.Lock()
    
    def get_history(self, session_id: str) -> list:
        """Get conversation history for a session"""
        with self.lock:
            if session_id not in self.conversations:
                self.conversations[session_id] = []
            return self.conversations[session_id].copy()
    
    def add_message(self, session_id: str, role: str, content: str):
        """Add a message to conversation history"""
        with self.lock:
            if session_id not in self.conversations:
                self.conversations[session_id] = []
            
            self.conversations[session_id].append({
                'role': role,
                'content': content,
                'timestamp': datetime.now().isoformat()
            })
            
            # Keep only last 20 messages to prevent memory bloat
            if len(self.conversations[session_id]) > 20:
                self.conversations[session_id] = self.conversations[session_id][-20:]
    
    def clear_history(self, session_id: str):
        """Clear conversation history for a session"""
        with self.lock:
            if session_id in self.conversations:
                del self.conversations[session_id]
    
    def get_context_summary(self, session_id: str, max_messages: int = 5) -> str:
        """Get a summary of recent conversation for context"""
        history = self.get_history(session_id)
        if not history:
            return ""
        
        recent = history[-max_messages:]
        summary = []
        for msg in recent:
            role = msg['role'].upper()
            content = msg['content'][:100]  # Truncate long messages
            summary.append(f"{role}: {content}")
        
        return "\n".join(summary)

# Global conversation memory
conversation_memory = ConversationMemory()

# ============================================================================
# Helper Functions
# ============================================================================
# Create global instance after imports
workflow_conversation_context = WorkflowConversationContext()


def build_workflow_context_prompt(workflow_context: dict) -> str:
    """Build a formatted prompt with workflow context"""
    if not workflow_context or 'error' in workflow_context:
        return "No workflow context available."
    
    context_parts = []
    
    # Workflow name
    workflow_name = workflow_context.get('workflowName', 'Untitled')
    context_parts.append(f"Workflow Name: {workflow_name}")
    
    # Node count and start node status
    node_count = workflow_context.get('nodeCount', 0)
    has_start = workflow_context.get('hasStartNode', False)
    context_parts.append(f"Nodes: {node_count} (Start node: {'✓' if has_start else '✗'})")
    
    # Nodes summary
    nodes = workflow_context.get('nodes', [])
    if nodes:
        context_parts.append("\nNodes in workflow:")
        for i, node in enumerate(nodes, 1):
            node_type = node.get('type', 'Unknown')
            node_label = node.get('label', node_type)
            is_start = '⭐ START' if node.get('isStart') else ''
            context_parts.append(f"  {i}. {node_label} ({node_type}) {is_start}")
            
            # Show configuration if node is simple
            config = node.get('config', {})
            if config and len(str(config)) < 200:  # Only show small configs
                context_parts.append(f"     Config: {json.dumps(config, indent=8)}")
    
    # Connections
    connections = workflow_context.get('connections', [])
    if connections:
        context_parts.append(f"\nConnections: {len(connections)}")
        for conn in connections[:5]:  # Show first 5 connections
            from_id = conn.get('from', 'unknown')
            to_id = conn.get('to', 'unknown')
            conn_type = conn.get('type', 'standard')
            context_parts.append(f"  {from_id} → {to_id} ({conn_type})")
        
        if len(connections) > 5:
            context_parts.append(f"  ... and {len(connections) - 5} more connections")
    
    # Variables
    variables = workflow_context.get('variables', {})
    if variables:
        context_parts.append(f"\nWorkflow Variables: {len(variables)}")
        for var_name, var_def in list(variables.items())[:5]:
            var_type = var_def.get('type', 'string')
            default_val = var_def.get('defaultValue', '')
            context_parts.append(f"  - {var_name} ({var_type}): {default_val}")
        
        if len(variables) > 5:
            context_parts.append(f"  ... and {len(variables) - 5} more variables")
    
    # Selected node
    selected_node = workflow_context.get('selectedNode')
    if selected_node:
        context_parts.append(f"\nCurrently selected: {selected_node.get('type', 'Unknown')} node")
    
    return "\n".join(context_parts)

def call_ai_api(prompt: str, system_prompt: str = None) -> dict:
    """
    Call the AI API using existing infrastructure
    
    Args:
        prompt: User prompt
        system_prompt: System prompt (optional)
    
    Returns:
        dict with 'response' and optional 'error'
    """
    try:
        logger.debug(f'Knowledge API (call_ai_api) System:\n {system_prompt or WORKFLOW_ASSISTANT_SYSTEM_PROMPT}')
        logger.debug(f'Knowledge API (call_ai_api) Prompt:\n {prompt}')
        # Use the existing azureMiniQuickPrompt function
        response = azureMiniQuickPrompt(
            prompt=prompt,
            system=system_prompt or WORKFLOW_ASSISTANT_SYSTEM_PROMPT
        )
        logger.debug(f'Knowledge API (call_ai_api) Response:\n {response}')
        return {
            'response': response,
            'error': None
        }
    
    except Exception as e:
        logger.error(f"Error calling AI API: {str(e)}")
        return {
            'response': None,
            'error': str(e)
        }
    

def extract_workflow_commands(response_text):
    """
    Extract workflow commands from assistant response
    
    Returns:
        tuple: (has_commands: bool, commands: dict or None)
    """
    import re
    import json as json_lib
    
    logger.info(f"Extracting commands from response (length: {len(response_text)})")
    
    try:
        # Method 1: Look for JSON in markdown code blocks
        json_pattern = r'```(?:json)?\s*(\{[\s\S]*?\})\s*```'
        matches = re.findall(json_pattern, response_text, re.DOTALL)
        
        if matches:
            logger.info(f"Found {len(matches)} code blocks")
            for i, match in enumerate(matches):
                try:
                    commands = json_lib.loads(match)
                    if isinstance(commands, dict) and 'commands' in commands:
                        logger.info(f"✓ Extracted workflow commands from code block {i}")
                        return True, commands
                except Exception as e:
                    logger.debug(f"Code block {i} failed: {e}")
                    continue
                
        # The response text IS the JSON directly (no code blocks)
        # Try to parse it directly first
        try:
            commands = json_lib.loads(response_text.strip())
            if isinstance(commands, dict) and 'commands' in commands and 'action' in commands:
                logger.info(f"✓ Extracted workflow commands directly from response")
                return True, commands
        except json_lib.JSONDecodeError:
            logger.debug("Response is not direct JSON, looking for code blocks")
        
        # Method 2: Look for raw JSON without code blocks
        json_start = response_text.find('{"action":')
        if json_start != -1:
            # Find matching closing brace
            brace_count = 0
            json_end = json_start
            for i in range(json_start, len(response_text)):
                if response_text[i] == '{':
                    brace_count += 1
                elif response_text[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        json_end = i + 1
                        break
            
            if json_end > json_start:
                try:
                    json_str = response_text[json_start:json_end]
                    commands = json_lib.loads(json_str)
                    if isinstance(commands, dict) and 'commands' in commands:
                        logger.info(f"✓ Extracted workflow commands from raw JSON")
                        return True, commands
                except Exception as e:
                    logger.debug(f"Raw JSON parse failed: {e}")
        
        logger.info("✗ No workflow commands found")
        return False, None
        
    except Exception as e:
        logger.error(f"Error extracting workflow commands: {e}", exc_info=True)
        return False, None
    
# ============================================================================
# API Routes
# ============================================================================

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'knowledge-api',
        'timestamp': datetime.now().isoformat()
    })


@app.route('/api/workflow/assistant_legacy', methods=['POST'])
@cross_origin()
def workflow_assistant_legacy():
    """
    Workflow assistant endpoint - Can answer questions AND generate workflow commands
    
    Request body:
    {
        "question": "User's question or command",
        "workflow_context": {...},  // Current workflow state
        "session_id": "session-id",  // Optional
        "include_history": true/false  // Optional
    }
    
    Response:
    {
        "status": "success",
        "response": "Assistant's text response",
        "session_id": "session-id",
        "has_commands": true/false,  // NEW: indicates if workflow commands are present
        "workflow_commands": {...}   // NEW: workflow commands if has_commands is true
    }
    """
    try:
        data = request.get_json()
        
        if not data or 'question' not in data:
            return jsonify({
                'status': 'error',
                'error': 'Question is required'
            }), 400
        
        question = data.get('question')
        workflow_context = data.get('workflow_context', {})
        #session_id = data.get('session_id') or generate_session_id()
        session_id = data.get('session_id') or str(datetime.now().timestamp())
        include_history = data.get('include_history', False)
        
        # Build workflow context prompt
        context_prompt = build_workflow_context_prompt(workflow_context)
        
        # Build full prompt
        full_prompt = f"""CURRENT WORKFLOW STATE:
{context_prompt}

USER QUESTION: {question}

Please provide a helpful, specific answer based on the workflow state above."""
        
        # Add conversation history if requested
        if include_history:
            history_summary = conversation_memory.get_context_summary(session_id)
            if history_summary:
                full_prompt = f"""RECENT CONVERSATION:
{history_summary}

{full_prompt}"""
        
        # Build system prompt with documentation
        enhanced_system_prompt = f"""{WORKFLOW_ASSISTANT_SYSTEM_PROMPT}

WORKFLOW DOCUMENTATION:
{WORKFLOW_DOCUMENTATION}"""
        
        # Log the request
        logger.info(f"Workflow assistant request - Session: {session_id}, Question: {question[:100]}")
        
        # Call AI API
        result = call_ai_api(full_prompt, enhanced_system_prompt)
        
        if result['error']:
            logger.error(f"AI API error: {result['error']}")
            return jsonify({
                'status': 'error',
                'error': result['error'],
                'session_id': session_id
            }), 500
        
        response_text = result['response']

        logger.debug(f"Response Text:\n {response_text}")
        
        # Extract workflow commands if present
        has_commands, workflow_commands = extract_workflow_commands(response_text)
        
        # Store in conversation memory
        conversation_memory.add_message(session_id, 'user', question)
        conversation_memory.add_message(session_id, 'assistant', response_text)
        
        # Build response
        response_data = {
            'status': 'success',
            'response': response_text,
            'session_id': session_id,
            'has_commands': has_commands
        }

        logger.debug(f"Final Response Data:\n {response_data}")
        
        # Add workflow commands if present
        if has_commands and workflow_commands:
            response_data['workflow_commands'] = workflow_commands
            # Extract just the explanation for the response text
            response_data['response'] = workflow_commands.get('explanation', response_text)
        
        return jsonify(response_data)
    
    except Exception as e:
        logger.error(f"Workflow assistant error: {str(e)}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500
    

@app.route('/api/workflow/assistant', methods=['POST'])
@cross_origin()
def workflow_assistant():
    """
    Workflow assistant endpoint with conversation context tracking
    """
    try:
        data = request.get_json()
        
        if not data or 'question' not in data:
            return jsonify({
                'status': 'error',
                'error': 'Question is required'
            }), 400
        
        question = data.get('question')
        workflow_context = data.get('workflow_context', {})
        session_id = data.get('session_id') or str(datetime.now().timestamp())
        include_history = data.get('include_history', False)
        
        # ===== ENHANCE PROMPT WITH CONVERSATION CONTEXT =====
        enhanced_question = enhance_prompt_with_context(question, session_id, workflow_context)
        # ===== END ENHANCEMENT =====
        
        # Build workflow context prompt (existing code)
        context_prompt = build_workflow_context_prompt(workflow_context)
        
        # Build full prompt
        full_prompt = f"""CURRENT WORKFLOW STATE:
{context_prompt}

{enhanced_question}

Please provide a helpful, specific answer based on the workflow state above."""
        
        # Add conversation history if requested
        if include_history:
            history_summary = conversation_memory.get_context_summary(session_id)
            if history_summary:
                full_prompt = f"""RECENT CONVERSATION:
{history_summary}

{full_prompt}"""
        
        # Build system prompt with documentation
        enhanced_system_prompt = f"""{WORKFLOW_ASSISTANT_SYSTEM_PROMPT}

WORKFLOW DOCUMENTATION:
{WORKFLOW_DOCUMENTATION}"""
        
        # Log the request
        logger.info(f"Workflow assistant request - Session: {session_id}, Question: {question[:100]}")
        
        # Call AI API
        result = call_ai_api(full_prompt, enhanced_system_prompt)
        
        if result['error']:
            logger.error(f"AI API error: {result['error']}")
            return jsonify({
                'status': 'error',
                'error': result['error'],
                'session_id': session_id
            }), 500
        
        response_text = result['response']
        
        # Extract workflow commands
        has_commands, workflow_commands = extract_workflow_commands(response_text)
        
        # Store in conversation memory
        conversation_memory.add_message(session_id, 'user', question)
        conversation_memory.add_message(session_id, 'assistant', response_text)
        
        # Build response
        response_data = {
            'status': 'success',
            'response': response_text,
            'session_id': session_id,
            'has_commands': has_commands
        }
        
        # Add workflow commands if present
        if has_commands and workflow_commands:
            response_data['workflow_commands'] = workflow_commands
            response_data['response'] = workflow_commands.get('explanation', response_text)
            logger.info(f"✓ Returning {len(workflow_commands.get('commands', []))} workflow commands")
        
        return jsonify(response_data)
    
    except Exception as e:
        logger.error(f"Workflow assistant error: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500



# ===== RECEIVE EXECUTION RESULTS =====
@app.route('/api/workflow/assistant/result', methods=['POST'])
@cross_origin()
def workflow_assistant_result():
    """
    Receive execution results from frontend to update context
    
    Request body:
    {
        "session_id": "session-id",
        "commands": {...},  // The commands that were executed
        "result": {         // Execution result from frontend
            "success": true,
            "executed": 2,
            "failed": 0,
            "nodeMapping": {
                "temp_node_1": "node-0",
                "temp_node_2": "node-1"
            },
            "errors": []
        }
    }
    """
    try:
        data = request.get_json()
        
        session_id = data.get('session_id')
        commands = data.get('commands')
        result = data.get('result')
        
        if not session_id or not commands or not result:
            return jsonify({
                'status': 'error',
                'error': 'Missing required fields'
            }), 400
        
        # Update conversation context with execution results
        workflow_conversation_context.add_command_result(session_id, commands, result)
        
        logger.info(f"Updated context for session {session_id}: {result.get('executed')} executed, {result.get('failed')} failed")
        
        return jsonify({
            'status': 'success',
            'message': 'Context updated'
        })
    
    except Exception as e:
        logger.error(f"Error updating context: {str(e)}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500

@app.route('/api/workflow/assistant/history', methods=['GET'])
@cross_origin()
def get_conversation_history():
    """
    Get conversation history for a session
    
    Query params:
        session_id: Session ID
    
    Response:
    {
        "status": "success",
        "history": [...],
        "session_id": "session-id"
    }
    """
    try:
        session_id = request.args.get('session_id')
        
        if not session_id:
            return jsonify({
                'status': 'error',
                'error': 'Session ID is required'
            }), 400
        
        history = conversation_memory.get_history(session_id)
        
        return jsonify({
            'status': 'success',
            'history': history,
            'session_id': session_id
        })
    
    except Exception as e:
        logger.error(f"Error getting conversation history: {str(e)}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500

@app.route('/api/workflow/assistant/history', methods=['DELETE'])
@cross_origin()
def clear_conversation_history():
    """
    Clear conversation history for a session
    
    Query params:
        session_id: Session ID
    
    Response:
    {
        "status": "success",
        "message": "Conversation history cleared"
    }
    """
    try:
        session_id = request.args.get('session_id')
        
        if not session_id:
            return jsonify({
                'status': 'error',
                'error': 'Session ID is required'
            }), 400
        
        conversation_memory.clear_history(session_id)
        
        return jsonify({
            'status': 'success',
            'message': 'Conversation history cleared',
            'session_id': session_id
        })
    
    except Exception as e:
        logger.error(f"Error clearing conversation history: {str(e)}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500

@app.route('/api/workflow/validate', methods=['POST'])
@cross_origin()
def validate_workflow():
    """
    Validate a workflow configuration
    
    Request body:
    {
        "workflow_context": {...}  // Workflow state to validate
    }
    
    Response:
    {
        "status": "success",
        "valid": true/false,
        "issues": [...],  // List of validation issues
        "suggestions": [...]  // List of improvement suggestions
    }
    """
    try:
        data = request.get_json()
        workflow_context = data.get('workflow_context', {})
        
        issues = []
        suggestions = []
        
        # Check for start node
        if not workflow_context.get('hasStartNode'):
            issues.append({
                'severity': 'error',
                'message': 'No start node defined',
                'fix': 'Right-click any node and select "Set as Start"'
            })
        
        # Check for nodes
        nodes = workflow_context.get('nodes', [])
        if len(nodes) == 0:
            issues.append({
                'severity': 'error',
                'message': 'Workflow has no nodes',
                'fix': 'Drag node types from the toolbar onto the canvas'
            })
        
        # Check for connections
        connections = workflow_context.get('connections', [])
        if len(nodes) > 1 and len(connections) == 0:
            issues.append({
                'severity': 'warning',
                'message': 'Nodes are not connected',
                'fix': 'Connect nodes by dragging from right endpoint to left endpoint'
            })
        
        # Check for disconnected nodes
        connected_nodes = set()
        for conn in connections:
            connected_nodes.add(conn.get('from'))
            connected_nodes.add(conn.get('to'))
        
        for node in nodes:
            if node.get('id') not in connected_nodes and len(nodes) > 1:
                issues.append({
                    'severity': 'warning',
                    'message': f"Node '{node.get('label', 'Unknown')}' is not connected",
                    'fix': 'Connect this node or remove it if not needed'
                })
        
        # Check for Loop/End Loop pairing
        loop_count = sum(1 for node in nodes if node.get('type') == 'Loop')
        end_loop_count = sum(1 for node in nodes if node.get('type') == 'End Loop')
        
        if loop_count != end_loop_count:
            issues.append({
                'severity': 'error',
                'message': f"Loop/End Loop mismatch: {loop_count} Loop nodes, {end_loop_count} End Loop nodes",
                'fix': 'Every Loop node must have a matching End Loop node'
            })
        
        # Suggestions
        if len(nodes) > 10:
            suggestions.append('Consider breaking this workflow into smaller, more manageable workflows')
        
        if workflow_context.get('debugMode') == False:
            suggestions.append('Enable debug mode to test your workflow step-by-step')
        
        return jsonify({
            'status': 'success',
            'valid': len([i for i in issues if i['severity'] == 'error']) == 0,
            'issues': issues,
            'suggestions': suggestions
        })
    
    except Exception as e:
        logger.error(f"Error validating workflow: {str(e)}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500



@app.route('/api/workflow/resolve-ids', methods=['POST'])
@cross_origin()
def resolve_workflow_ids():
    """
    Use AI to resolve agent names and connection names to IDs
    
    Request:
        {
            "commands": {...}  // Workflow commands with natural language references
        }
    
    Response:
        {
            "status": "success",
            "commands": {...},  // Commands with resolved IDs
            "resolutions": {...}  // What was changed
        }
    """
    try:
        print('Called resolve-ids...')
        logger.info('Called resolve-ids...')

        data = request.get_json()
        commands = data.get('commands', {})
        
        logger.debug(f"Commands: {commands}")
        # Get reference data for agents and connections
        agents_list = get_agents_list()
        connections_list = get_connections_list()
        
        logger.debug(agents_list)
        logger.debug(connections_list)

        # Convert commands to JSON string for AI processing
        commands_json = json.dumps(commands, indent=2)
        
        # Build AI prompt
        prompt = f"""You are resolving agent names and database connection names to their numeric IDs.

AVAILABLE AGENTS:
{agents_list}

AVAILABLE DATABASE CONNECTIONS:
{connections_list}

WORKFLOW COMMANDS WITH NAMES:
{commands_json}

TASK:
1. Find any "agent_id" fields that contain text names (not numeric IDs)
2. Find any "connection" fields that contain text names (not numeric IDs)
3. Match the names to the closest available agent or connection from the lists above
4. Replace the names with the numeric IDs
5. If a name doesn't match anything, use "" for agent_id or the first connection ID for connection field

IMPORTANT:
- Only output the complete JSON with replaced IDs
- Keep ALL other fields exactly the same
- Match names intelligently (handle typos, case differences, partial matches)
- If unsure, pick the most likely match

Output the complete workflow commands JSON with resolved IDs:"""

        system_prompt = "You are a data resolution assistant. You match human-readable names to numeric IDs accurately."
        
        logger.info("Sending commands to AI for ID resolution")
        
        # Call AI to resolve IDs
        ai_response = azureMiniQuickPrompt(prompt, system_prompt)
        
        if not ai_response or ai_response.startswith("Error:"):
            raise Exception(f"AI resolution failed: {ai_response}")
        
        # Parse AI response - extract JSON
        resolved_commands = extract_json_from_response(ai_response)
        
        logger.debug(f'Resolved Commands: {resolved_commands}')

        if not resolved_commands:
            raise Exception("Failed to parse AI response as JSON")
        
        # Compare original vs resolved to show what changed
        resolutions = find_resolutions(commands, resolved_commands)
        
        logger.info(f"AI resolved {len(resolutions)} ID mappings")
        
        return jsonify({
            'status': 'success',
            'commands': resolved_commands,
            'resolutions': resolutions
        })
    
    except Exception as e:
        logger.error(f"Error resolving IDs: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


def get_agents_list():
    """Get formatted list of agents with IDs and names"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
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
            agents.append(f"Agent ID: {row.agent_id} | Name: {row.agent_name} | Purpose: {row.agent_objective or 'N/A'}")
        
        cursor.close()
        conn.close()
        
        return "\n".join(agents) if agents else "No agents available"
    
    except Exception as e:
        logger.error(f"Error fetching agents: {str(e)}")
        return "Error fetching agents"


def get_connections_list():
    """Get formatted list of database connections with IDs and names"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
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
            connections.append(f"Connection ID: {row.id} | Name: {row.connection_name} | Database: {row.database_name}")
        
        cursor.close()
        conn.close()
        
        return "\n".join(connections) if connections else "No connections available"
    
    except Exception as e:
        logger.error(f"Error fetching connections: {str(e)}")
        return "Error fetching connections"


def extract_json_from_response(response_text):
    """Extract JSON from AI response (may have markdown code blocks)"""
    import re
    
    # Try to extract from code blocks first
    json_pattern = r'```(?:json)?\s*(\{[\s\S]*?\})\s*```'
    matches = re.findall(json_pattern, response_text, re.DOTALL)
    
    if matches:
        try:
            return json.loads(matches[0])
        except:
            pass
    
    # Try to parse entire response as JSON
    try:
        return json.loads(response_text.strip())
    except:
        pass
    
    # Try to find JSON object in response
    json_start = response_text.find('{')
    json_end = response_text.rfind('}')
    
    if json_start != -1 and json_end != -1 and json_end > json_start:
        try:
            return json.loads(response_text[json_start:json_end + 1])
        except:
            pass
    
    return None


def find_resolutions(original, resolved):
    """
    Compare original and resolved commands to find what was changed
    Returns dict of changes: {"agent_id in node-0": "General Agent → 31"}
    """
    resolutions = {}
    
    def compare_objects(orig, res, path=""):
        if isinstance(orig, dict) and isinstance(res, dict):
            for key in orig.keys():
                new_path = f"{path}.{key}" if path else key
                
                if key in res:
                    orig_val = orig[key]
                    res_val = res[key]
                    
                    # Check if value changed
                    if orig_val != res_val:
                        # Only track agent_id and connection changes
                        if key in ['agent_id', 'connection']:
                            resolutions[new_path] = f"{orig_val} → {res_val}"
                        else:
                            # Recurse for nested objects
                            compare_objects(orig_val, res_val, new_path)
        
        elif isinstance(orig, list) and isinstance(res, list):
            for i, (orig_item, res_item) in enumerate(zip(orig, res)):
                compare_objects(orig_item, res_item, f"{path}[{i}]")
    
    compare_objects(original, resolved)
    return resolutions

