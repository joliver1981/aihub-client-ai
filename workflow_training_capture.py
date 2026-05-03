# workflow_training_capture.py
# 
# SIMPLIFIED: Captures training data directly from WorkflowAgent's chat history
# when a workflow is saved. No session tracking needed.

import json
import logging
from logging.handlers import WatchedFileHandler
import os
from datetime import datetime
from typing import Dict, List, Optional

from CommonUtils import rotate_logs_on_startup, get_log_path

rotate_logs_on_startup(os.getenv('WORKFLOW_TRAINING_LOG', get_log_path('workflow_training_log.txt')))

logger = logging.getLogger("WorkflowTrainingCapture")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=os.getenv('WORKFLOW_TRAINING_LOG', get_log_path('workflow_training_log.txt')), encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)


# =============================================================================
# CONFIGURATION
# =============================================================================

def is_capture_enabled() -> bool:
    """Check if training capture is enabled via config"""
    return os.getenv('WORKFLOW_TRAINING_CAPTURE_ENABLED', 'false').lower() == 'true'

def get_storage_path() -> str:
    """Get the storage path for training data"""
    return os.getenv('WORKFLOW_TRAINING_CAPTURE_PATH', './training_data/workflows')

# Phases we want in training data (excludes DISCOVERY and REQUIREMENTS)
TRAINING_PHASES = {'planning', 'building', 'refinement'}


# =============================================================================
# MAIN CAPTURE FUNCTION
# =============================================================================

def capture_from_agent(agent, workflow_type: str = None, success: bool = True) -> bool:
    """
    Capture training data directly from a WorkflowAgent instance.
    
    Call this when the user saves a workflow. It extracts the relevant
    conversation turns and saves them in OpenAI fine-tuning format.
    
    Args:
        agent: The WorkflowAgent instance with chat_history
        workflow_type: Optional classification (e.g., "document_processing")
        success: Whether the workflow was successfully completed
    
    Returns:
        True if data was captured, False otherwise
    """
    if not is_capture_enabled():
        logger.debug("Training capture is disabled")
        return False
    
    try:
        logger.info(f"Capturing training data from agent session {agent.session_id}")
        
        # Get the conversation from the agent
        chat_history = getattr(agent, 'chat_history', [])
        conversation_context = getattr(agent, 'conversation_context', [])
        requirements = getattr(agent, 'requirements', None)
        current_phase = getattr(agent, 'phase', None)
        current_json_commands = getattr(agent, 'current_json_commands', None)
        
        if not chat_history and not conversation_context:
            logger.warning("No conversation history to capture")
            return False
        
        # Extract training examples from the conversation
        examples = _extract_training_examples(
            chat_history=chat_history,
            conversation_context=conversation_context,
            requirements=requirements.to_dict() if requirements else None,
            workflow_commands=current_json_commands
        )
        
        if not examples:
            logger.warning("No training examples extracted (no commands found)")
            return False
        
        # Ensure storage directory exists
        storage_path = get_storage_path()
        os.makedirs(storage_path, exist_ok=True)
        
        # Save raw conversation for reference
        raw_file = os.path.join(storage_path, "conversations.jsonl")
        raw_data = {
            "session_id": agent.session_id,
            "timestamp": datetime.utcnow().isoformat(),
            "workflow_type": workflow_type,
            "success": success,
            "phase": current_phase.value if current_phase else None,
            "requirements": requirements.to_dict() if requirements else None,
            "conversation": conversation_context,
            "example_count": len(examples)
        }
        with open(raw_file, 'a') as f:
            f.write(json.dumps(raw_data) + "\n")
        
        # Save in OpenAI training format (only if successful)
        if success:
            training_file = os.path.join(storage_path, "training_ready.jsonl")
            with open(training_file, 'a') as f:
                for example in examples:
                    f.write(json.dumps(example) + "\n")
            logger.info(f"Saved {len(examples)} training examples from session {agent.session_id}")
        else:
            logger.info(f"Skipped training examples (success=False) for session {agent.session_id}")
        
        return True
        
    except Exception as e:
        logger.error(f"Error capturing training data: {e}", exc_info=True)
        return False
    
    
def capture_bad_plan_to_commands(workflow_plan: str, commands: Dict, session_id: str = None,
                                  validation_errors: Optional[list] = None,
                                  fix_messages: Optional[list] = None) -> bool:
    """
    Capture rejected/bad workflow generations to a SEPARATE JSONL file
    (`bad_plan_to_commands.jsonl`) so they can be mined later for the kinds
    of issues a deterministic validator should catch.

    Bad outputs are workflows the agent generated that triggered the
    validation auto-fix loop. They never make it into the main training
    file (the strict capture filter rejects dirty conversations), but they
    are exactly the cases a downstream validator needs to recognise.

    Stored entry shape:
        {
            "session_id": "...",
            "captured_at": "...",
            "workflow_plan": "<final agreed plan>",
            "commands": {"action": "build_workflow", "commands": [...]},
            "validation_errors": [...],     // if available
            "fix_messages": [...]           // any auto-fix messages the
                                            // frontend sent to the agent
        }
    """
    if not workflow_plan or not commands:
        logger.warning("capture_bad_plan_to_commands: missing workflow_plan or commands")
        return False
    try:
        storage_path = get_storage_path()
        os.makedirs(storage_path, exist_ok=True)
        record = {
            "session_id": session_id,
            "captured_at": datetime.utcnow().isoformat(),
            "workflow_plan": workflow_plan,
            "commands": commands,
            "validation_errors": validation_errors or [],
            "fix_messages": fix_messages or [],
        }
        bad_file = os.path.join(storage_path, "bad_plan_to_commands.jsonl")
        with open(bad_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        logger.info(
            f"Captured bad plan-to-commands example "
            f"({len(commands.get('commands', []))} commands, "
            f"{len(validation_errors or [])} validation errors) "
            f"for session {session_id}"
        )
        return True
    except Exception as e:
        logger.error(f"Error capturing bad plan-to-commands: {e}", exc_info=True)
        return False


def capture_plan_to_commands(workflow_plan: str, commands: Dict, session_id: str = None) -> bool:
    """
    Capture training data in clean plan → commands format for fine-tuning.
    
    Args:
        workflow_plan: Natural language workflow plan
        commands: Generated commands dict with 'action' and 'commands'
        session_id: Optional session identifier
    
    Returns:
        True if captured, False otherwise
    """
    if not is_capture_enabled():
        return False
    
    if not workflow_plan or not commands:
        logger.warning("Missing workflow_plan or commands")
        return False
    
    try:
        storage_path = get_storage_path()
        os.makedirs(storage_path, exist_ok=True)
        
        # Use the same system prompt as CommandGenerator for training consistency
        from CommandGenerator import COMMAND_GENERATOR_SYSTEM_PROMPT
        system_prompt = COMMAND_GENERATOR_SYSTEM_PROMPT
        
        commands_json = json.dumps(commands, indent=2)
        
        training_example = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": workflow_plan},
                {"role": "assistant", "content": f"```json\n{commands_json}\n```"}
            ]
        }
        
        training_file = os.path.join(storage_path, "plan_to_commands.jsonl")
        with open(training_file, 'a') as f:
            f.write(json.dumps(training_example) + "\n")
        
        logger.info(f"Captured plan-to-commands training data ({len(commands.get('commands', []))} commands)")
        return True
        
    except Exception as e:
        logger.error(f"Error capturing training data: {e}", exc_info=True)
        return False


def _extract_training_examples(
    chat_history: List,
    conversation_context: List[Dict],
    requirements: Optional[Dict],
    workflow_commands: Optional[Dict] = None
) -> List[Dict]:
    """
    Extract training examples from conversation.
    
    Creates properly formatted multi-turn conversations for OpenAI fine-tuning.
    Generates ONE training example per conversation that includes ALL turns
    up to and including the LAST command-generating turn.
    
    OpenAI format requires alternating user/assistant messages:
    {"messages": [
        {"role": "system", "content": "..."},
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."},
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."}  <- ends with assistant containing commands
    ]}
    """
    examples = []
    
    system_prompt = _get_system_prompt()
    
    # Add requirements context to system prompt if available
    if requirements:
        requirements_context = f"\n\nCurrent workflow requirements:\n{json.dumps(requirements, indent=2)}"
        full_system_prompt = system_prompt + requirements_context
    else:
        full_system_prompt = system_prompt
    
    # Find the LAST assistant turn that contains workflow commands
    # This gives us the most complete conversation
    last_command_idx = -1
    for i, turn in enumerate(conversation_context):
        if turn.get('role') == 'assistant':
            content = turn.get('content', '')
            if _extract_commands_from_response(content):
                last_command_idx = i
    
    if last_command_idx < 0 and not workflow_commands:
        # No command-generating turns found
        return examples
    
    # Build a single multi-turn training example with all turns up to the last command
    messages = [{"role": "system", "content": full_system_prompt}]
    
    # Add all turns (user and assistant) up to and including the last command turn
    for j in range(0, last_command_idx + 1):
        turn = conversation_context[j]
        role = turn.get('role')
        msg_content = turn.get('content', '')
        
        if not msg_content:
            continue
        
        if role == 'user':
            messages.append({"role": "user", "content": msg_content})
        elif role == 'assistant':
            # Check if this turn has commands
            if workflow_commands:
                commands = workflow_commands
            else:
                commands = _extract_commands_from_response(msg_content)
            if commands:
                # For command-generating turns, extract just the clean JSON
                assistant_content = f"```json\n{json.dumps(commands, indent=2)}\n```"
                messages.append({"role": "assistant", "content": assistant_content})
            else:
                # Non-command turns: include full response but truncate if very long
                if len(msg_content) > 1500:
                    msg_content = msg_content[:1500] + "..."
                messages.append({"role": "assistant", "content": msg_content})
    
    # Validate we have proper structure
    # Should be: system, user, assistant, user, assistant, ... ending with assistant
    if len(messages) < 3:  # Need at least system + user + assistant
        return examples
    
    # Check that it ends with assistant
    if messages[-1].get('role') != 'assistant':
        return examples
    
    # Return single example with the complete conversation
    examples.append({"messages": messages})
    
    return examples


def _extract_commands_from_response(response: str) -> Optional[Dict]:
    """Extract workflow commands JSON from an assistant response."""
    import re
    
    # Look for JSON in markdown code blocks
    json_pattern = r'```(?:json)?\s*(\{[\s\S]*?\})\s*```'
    matches = re.findall(json_pattern, response, re.DOTALL)
    
    for match in matches:
        try:
            data = json.loads(match)
            # Check if it looks like workflow commands
            if isinstance(data, dict) and 'commands' in data:
                return data
            if isinstance(data, dict) and data.get('action') == 'build_workflow':
                return data
        except json.JSONDecodeError:
            continue
    
    return None


def _get_system_prompt() -> str:
    """Get the system prompt for training examples."""
    return """You are a workflow automation assistant that generates JSON workflow commands.

When given workflow requirements or modification requests, output commands in ```json blocks.

Command types: add_node, connect_nodes, set_start_node, update_node_config, delete_node, delete_connection

Node types: Database, AI Action, Document, Loop, End Loop, Conditional, Human Approval, Alert, Folder Selector, File, Set Variable, Execute Application

Variables: Use ${varName} in config values, plain names in outputVariable fields.
Connections: Use pass (success), fail (error), or complete types. Each node can have only one outgoing connection per type."""


# =============================================================================
# STATISTICS
# =============================================================================

def get_statistics() -> Dict:
    """Get capture statistics.

    The plan_to_commands_count is surfaced whether or not capture is currently
    enabled — users want visibility into accumulated training data regardless
    of whether new exports are being accepted right now.
    """
    stats = {
        "capture_enabled": is_capture_enabled(),
    }

    try:
        storage_path = get_storage_path()
        stats["storage_path"] = storage_path

        raw_file = os.path.join(storage_path, "conversations.jsonl")
        if os.path.exists(raw_file):
            with open(raw_file, 'r', encoding='utf-8') as f:
                stats["captured_conversations"] = sum(1 for _ in f)

        training_file = os.path.join(storage_path, "training_ready.jsonl")
        if os.path.exists(training_file):
            with open(training_file, 'r', encoding='utf-8') as f:
                stats["training_examples"] = sum(1 for _ in f)

        plan_file = os.path.join(storage_path, "plan_to_commands.jsonl")
        if os.path.exists(plan_file):
            with open(plan_file, 'r', encoding='utf-8') as f:
                stats["plan_to_commands_count"] = sum(1 for _ in f)
            stats["plan_to_commands_bytes"] = os.path.getsize(plan_file)
        else:
            stats["plan_to_commands_count"] = 0

    except Exception as e:
        stats["error"] = str(e)

    return stats
