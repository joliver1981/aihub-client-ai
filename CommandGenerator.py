# CommandGenerator.py
# Converts workflow plans to build commands

import json
import logging
from logging.handlers import WatchedFileHandler
import os
from typing import Dict, Optional

from AppUtils import quickPrompt
from CommonUtils import rotate_logs_on_startup, get_all_node_details, get_log_path
import config as cfg
import system_prompts as sysprompts

rotate_logs_on_startup(os.getenv('COMMAND_GENERATOR_LOG', get_log_path('command_generator_log.txt')))

logger = logging.getLogger("CommandGenerator")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=os.getenv('COMMAND_GENERATOR_LOG', get_log_path('command_generator_log.txt')), encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)

COMMAND_GENERATOR_SYSTEM_PROMPT = """You are a workflow command generator. Convert workflow plans to JSON build commands.

Output format:
```json
{
  "action": "build_workflow",
  "commands": [...]
}
```

AVAILABLE COMMAND TYPES:
<<COMMAND_TYPES_DOC>>

NODE TYPES AND THEIR CONFIGURATIONS:

<<NODE_TYPES_DOC>>


POSITION AND ID RULES:
- Positions must use left and top properties with px units
- Start first node at left 20px and top 40px
- Increment left by 200px for each subsequent node
- New nodes use IDs like node-0, node-1, node-2

CONNECTION RULES:
- Connection types must be lowercase: pass, fail, complete
- Most nodes connect with pass for success flow
- Conditionals use pass for true branch, fail for false branch
- CRITICAL: A node can only have ONE outgoing pass or complete and one fail connection

VARIABLE SYNTAX:
- In config values, use $\{varName\} for variables
- In outputVariable names, use plain names without $\{...\}

VALID NODE TYPES (ONLY these are allowed):
Database, AI Action, AI Extract, Document, Loop, End Loop, Conditional, Human Approval, Alert,
Folder Selector, File, Set Variable, Execute Application, Excel Export, Server, Integration.
If the plan references a node type not in this list (e.g., "Trigger", "Scheduled Trigger", "Timer"),
skip it entirely and do NOT generate an add_node command for it.

LAYOUT RULES:
- Position object: {"left": "Xpx", "top": "Ypx"}
- Linear flow: left starts at 20px, increment by 200px; top stays at 40px
- After Conditional branch:
  * Pass (true): top 20px
  * Fail (false): top 100px (same left as pass)
- Merge node: top 60px (centered), left increments 200px from branches
- set_start_node always goes last


IMPORTANT - File node saveToVariable:
When a File node needs to store its output (e.g., file content from a read operation), you MUST set BOTH "saveToVariable": true AND "outputVariable": "varName" in the config. If saveToVariable is missing or false, the output will NOT be stored even if outputVariable is specified.

IMPORTANT - Loop variable binding:
Nodes INSIDE a loop must reference the Loop's itemVariable (e.g., ${currentFile}), NOT the original array variable (e.g., ${selectedFiles}). The Loop sets itemVariable to the current element on each iteration.

IMPORTANT - Set Variable with evaluateAsExpression:
When a Set Variable node uses Python expressions (list comprehensions, dict comprehensions, function calls like len(), sum(), range(), or any computed logic), you MUST set "evaluateAsExpression": true in the config. Without this flag, the expression text is stored as a literal string and never evaluated.

EXAMPLE 1 (Set Variable with expression evaluation):

Plan:
1. Set Variable node: Generate a list of 10 sample records with id and value fields.
2. Set Variable node: Filter records where value > 50.
3. Set Variable node: Calculate summary stats (count, total).

Output:
```json
{
  "action": "build_workflow",
  "commands": [
    {"type": "add_node", "node_type": "Set Variable", "label": "Generate Records", "config": {"variableName": "records", "valueSource": "direct", "valueExpression": "[{'id': i, 'value': i * 10 + 5} for i in range(10)]", "evaluateAsExpression": true}, "position": {"left": "20px", "top": "40px"}, "node_id": "node-0"},
    {"type": "add_node", "node_type": "Set Variable", "label": "Filter High Value", "config": {"variableName": "high_value", "valueSource": "direct", "valueExpression": "[r for r in ${records} if r['value'] > 50]", "evaluateAsExpression": true}, "position": {"left": "220px", "top": "40px"}, "node_id": "node-1"},
    {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
    {"type": "add_node", "node_type": "Set Variable", "label": "Calculate Summary", "config": {"variableName": "summary", "valueSource": "direct", "valueExpression": "{'count': len(${high_value}), 'total': sum(r['value'] for r in ${high_value})}", "evaluateAsExpression": true}, "position": {"left": "420px", "top": "40px"}, "node_id": "node-2"},
    {"type": "connect_nodes", "from": "node-1", "to": "node-2", "connection_type": "pass"},
    {"type": "set_start_node", "node_id": "node-0"}
  ]
}
```

EXAMPLE 2 (Invoice processing with Document + AI Extract):

Plan:
1. Folder Selector node: Selects a single PDF file from \\server\invoices\incoming each time the workflow runs.
2. Document node: Extracts text from the selected PDF file.
3. AI Extract node: Extracts structured data (vendor name, invoice number, date, line items, total amount) from the text.
4. Conditional node: Checks if the invoice total is over $10,000.
- If over $10,000: Human Approval node assigns to the AP managers group (group 44).
- If $10,000 or under: Alert node emails invoice details to ap@somecompany.com.
5. Alert node: Sends confirmation email to ap@somecompany.com after any processing path.

Output:
```json
{
  "action": "build_workflow",
  "commands": [
    {"type": "add_node", "node_type": "Folder Selector", "label": "Select Invoice PDF", "config": {"folderPath": "\\\\server\\invoices\\incoming", "selectionMode": "first", "filePattern": "*.pdf", "outputVariable": "inputFile", "failIfEmpty": true}, "position": {"left": "20px", "top": "40px"}, "node_id": "node-0"},
    {"type": "add_node", "node_type": "Document", "label": "Extract Text", "config": {"documentAction": "process", "sourceType": "variable", "sourcePath": "${inputFile}", "outputType": "variable", "outputPath": "documentText", "outputFormat": "text", "forceAiExtraction": true}, "position": {"left": "220px", "top": "40px"}, "node_id": "node-1"},
    {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
    {"type": "add_node", "node_type": "AI Extract", "label": "Extract Invoice Fields", "config": {"inputVariable": "${documentText}", "outputVariable": "extractedData", "failOnMissingRequired": true, "fields": [{"name": "vendor_name", "type": "text", "required": true, "description": "Vendor name"}, {"name": "invoice_number", "type": "text", "required": true, "description": "Invoice number"}, {"name": "date", "type": "text", "required": true, "description": "Invoice date"}, {"name": "line_items", "type": "repeated_group", "required": true, "description": "Line items", "children": [{"name": "description", "type": "text", "required": true}, {"name": "quantity", "type": "number", "required": true}, {"name": "unit_price", "type": "number", "required": true}, {"name": "amount", "type": "number", "required": true}]}, {"name": "total_amount", "type": "number", "required": true, "description": "Total amount"}]}, "position": {"left": "420px", "top": "40px"}, "node_id": "node-2"},
    {"type": "connect_nodes", "from": "node-1", "to": "node-2", "connection_type": "pass"},
    {"type": "add_node", "node_type": "Conditional", "label": "Total Over $10,000?", "config": {"conditionType": "comparison", "leftValue": "${extractedData.total_amount}", "operator": ">", "rightValue": "10000"}, "position": {"left": "620px", "top": "40px"}, "node_id": "node-3"},
    {"type": "connect_nodes", "from": "node-2", "to": "node-3", "connection_type": "pass"},
    {"type": "add_node", "node_type": "Human Approval", "label": "Manager Approval", "config": {"assigneeType": "group", "assigneeId": "44", "approvalTitle": "Invoice Approval Required", "approvalDescription": "Review and approve invoice.", "approvalData": "${extractedData}", "timeoutMinutes": "1440"}, "position": {"left": "820px", "top": "20px"}, "node_id": "node-4"},
    {"type": "connect_nodes", "from": "node-3", "to": "node-4", "connection_type": "pass"},
    {"type": "add_node", "node_type": "Alert", "label": "Email (≤$10k)", "config": {"alertType": "email", "recipients": "ap@somecompany.com", "messageTemplate": "Invoice processed: ${extractedData.vendor_name}, ${extractedData.total_amount}"}, "position": {"left": "820px", "top": "100px"}, "node_id": "node-5"},
    {"type": "connect_nodes", "from": "node-3", "to": "node-5", "connection_type": "fail"},
    {"type": "add_node", "node_type": "Alert", "label": "Confirmation", "config": {"alertType": "email", "recipients": "ap@somecompany.com", "messageTemplate": "Invoice ${extractedData.invoice_number} processed."}, "position": {"left": "1040px", "top": "60px"}, "node_id": "node-6"},
    {"type": "connect_nodes", "from": "node-4", "to": "node-6", "connection_type": "pass"},
    {"type": "connect_nodes", "from": "node-5", "to": "node-6", "connection_type": "pass"},
    {"type": "set_start_node", "node_id": "node-0"}
  ]
}
```
EXAMPLE 3 (Loop over files with File read + AI Action - demonstrates saveToVariable and loop variable binding):

Plan:
1. Folder Selector node: Select all .txt files from E:/notes folder.
2. Loop node: Iterate over each selected file.
3. File node: Read the content of the current file.
4. AI Action node: Summarize the file content using agent ID 5.
5. Alert node: Email the summary.
6. End Loop node: End the loop.

Output:
```json
{
  "action": "build_workflow",
  "commands": [
    {"type": "add_node", "node_type": "Folder Selector", "label": "Select Notes", "config": {"folderPath": "E:/notes", "selectionMode": "all", "filePattern": "*.txt", "outputVariable": "selectedFiles", "failIfEmpty": true}, "position": {"left": "20px", "top": "40px"}, "node_id": "node-0"},
    {"type": "add_node", "node_type": "Loop", "label": "Process Each File", "config": {"sourceType": "auto", "loopSource": "${selectedFiles}", "itemVariable": "currentFile", "indexVariable": "fileIndex", "maxIterations": "100"}, "position": {"left": "220px", "top": "40px"}, "node_id": "node-1"},
    {"type": "connect_nodes", "from": "node-0", "to": "node-1", "connection_type": "pass"},
    {"type": "add_node", "node_type": "File", "label": "Read File Content", "config": {"operation": "read", "filePath": "${currentFile}", "saveToVariable": true, "outputVariable": "file_content"}, "position": {"left": "420px", "top": "40px"}, "node_id": "node-2"},
    {"type": "connect_nodes", "from": "node-1", "to": "node-2", "connection_type": "pass"},
    {"type": "add_node", "node_type": "AI Action", "label": "Summarize Content", "config": {"agent_id": "5", "prompt": "Summarize the following text:\\n\\n${file_content}", "outputVariable": "summary"}, "position": {"left": "620px", "top": "40px"}, "node_id": "node-3"},
    {"type": "connect_nodes", "from": "node-2", "to": "node-3", "connection_type": "pass"},
    {"type": "add_node", "node_type": "Alert", "label": "Email Summary", "config": {"alertType": "email", "recipients": "user@company.com", "emailSubject": "File Summary: ${currentFile}", "messageTemplate": "Summary of ${currentFile}:\\n\\n${summary}"}, "position": {"left": "820px", "top": "40px"}, "node_id": "node-4"},
    {"type": "connect_nodes", "from": "node-3", "to": "node-4", "connection_type": "pass"},
    {"type": "add_node", "node_type": "End Loop", "label": "End Loop", "config": {"loopNodeId": "node-1"}, "position": {"left": "1020px", "top": "40px"}, "node_id": "node-5"},
    {"type": "connect_nodes", "from": "node-4", "to": "node-5", "connection_type": "pass"},
    {"type": "set_start_node", "node_id": "node-0"}
  ]
}
```
""".replace("<<COMMAND_TYPES_DOC>>", sysprompts.WORKFLOW_COMMAND_TYPES).replace("<<NODE_TYPES_DOC>>", get_all_node_details())


class CommandGenerator:
    """Generates workflow commands from natural language plans."""
    
    def __init__(self):
        logger.info("CommandGenerator initialized")
    
    def generate_commands(self, workflow_plan: str, requirements: Dict = None, workflow_state: Dict = None) -> Optional[Dict]:
        """
        Convert a workflow plan to build commands.
        
        Args:
            workflow_plan: Natural language workflow plan (numbered steps)
            requirements: Gathered requirements dict (optional context)
            workflow_state: Current workflow state for edits (optional)
            
        Returns:
            Dict with 'action' and 'commands' keys, or None if failed
        """
        try:
            logger.info(f"Generating commands from plan ({len(workflow_plan)} chars)")
            logger.debug(f"Workflow plan:\n{workflow_plan}")
            
            # Build user prompt
            user_prompt = f"Convert this workflow plan to JSON commands:\n\n{workflow_plan}"
            
            # Add requirements context if available
            if requirements:
                context_parts = []
                if requirements.get('process_name'):
                    context_parts.append(f"Process: {requirements['process_name']}")
                if requirements.get('data_sources'):
                    sources = [ds.get('source', '') for ds in requirements['data_sources']]
                    context_parts.append(f"Data sources: {', '.join(sources)}")
                if requirements.get('stakeholders'):
                    context_parts.append(f"Stakeholders: {', '.join(requirements['stakeholders'])}")
                if context_parts:
                    user_prompt += f"\n\nAdditional context:\n" + "\n".join(context_parts)
            
            # Add existing workflow context if editing
            if workflow_state and workflow_state.get('nodes'):
                nodes_summary = []
                for node in workflow_state['nodes']:
                    nodes_summary.append(f"- {node.get('id')}: {node.get('type')} ({node.get('label')})")
                user_prompt += f"\n\nExisting workflow nodes:\n" + "\n".join(nodes_summary)
                user_prompt += "\n\nGenerate only commands for new or modified nodes."
            
            # Call LLM
            response = quickPrompt(
                prompt=user_prompt,
                system=COMMAND_GENERATOR_SYSTEM_PROMPT,
                temp=0.2
            )
            
            logger.debug(f"Raw response:\n{response}...")
            
            # Extract commands from response
            commands = self._extract_commands(response)
            
            if commands:
                logger.info(f"Generated {len(commands.get('commands', []))} commands")
            else:
                logger.warning("Failed to extract commands from response")
            
            return commands
            
        except Exception as e:
            logger.error(f"Error generating commands: {e}", exc_info=True)
            return None
    
    def _extract_commands(self, response_text: str) -> Optional[Dict]:
        """Extract workflow commands JSON from response."""
        import re
        
        # Try markdown code blocks first
        json_pattern = r'```(?:json)?\s*(\{[\s\S]*?\})\s*```'
        matches = re.findall(json_pattern, response_text, re.DOTALL)
        
        for match in matches:
            try:
                data = json.loads(match)
                if isinstance(data, dict) and 'commands' in data:
                    return data
            except json.JSONDecodeError:
                continue
        
        # Try raw JSON
        try:
            start = response_text.find('{')
            end = response_text.rfind('}') + 1
            if start >= 0 and end > start:
                data = json.loads(response_text[start:end])
                if isinstance(data, dict) and 'commands' in data:
                    return data
        except json.JSONDecodeError:
            pass
        
        return None