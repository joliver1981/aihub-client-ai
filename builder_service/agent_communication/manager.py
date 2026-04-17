"""
Agent Communication Manager
=============================
Orchestrates conversations between the Builder Agent and other agents.

Responsibilities:
- Start and manage agent conversations
- Route messages through the appropriate protocol adapter
- Track conversation state and status
- Handle user escalation when agents need input
- Collect results when conversations complete
"""

import logging
import sys
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional, Tuple, Callable, Any
from datetime import datetime

from .models import (
    AgentConversation,
    AgentMessage,
    ConversationStatus,
    MessageRole,
    DelegationDecision,
)
from .adapters.base import AdapterRegistry

# Add builder_agent to path for imports
BUILDER_AGENT_DIR = Path(__file__).parent.parent.parent / "builder_agent"
if str(BUILDER_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(BUILDER_AGENT_DIR))

logger = logging.getLogger(__name__)


class AgentCommunicationManager:
    """
    Manages all agent-to-agent communication.

    Usage:
        manager = AgentCommunicationManager()

        # Start a conversation
        conversation = await manager.start_conversation(
            agent_id="workflow_agent",
            initial_message="Create a workflow that...",
            context={"user_request": "...", "available_tools": [...]}
        )

        # Send messages and stream responses
        async for chunk in manager.send_message(conversation.id, "Some follow-up"):
            print(chunk, end="")

        # Check if user input is needed
        if conversation.status == ConversationStatus.WAITING_FOR_USER:
            user_response = input(conversation.pending_question)
            await manager.provide_user_input(conversation.id, user_response)
    """

    def __init__(self):
        self.active_conversations: Dict[str, AgentConversation] = {}
        self._on_conversation_update: Optional[Callable[[AgentConversation], None]] = None

    def set_update_callback(self, callback: Callable[[AgentConversation], Any]):
        """Set a callback to be called when conversations update."""
        self._on_conversation_update = callback

    def _notify_update(self, conversation: AgentConversation):
        """Notify listeners of a conversation update."""
        if self._on_conversation_update:
            try:
                self._on_conversation_update(conversation)
            except Exception as e:
                logger.error(f"Error in conversation update callback: {e}")

    def _get_agent(self, agent_id: str):
        """Get an agent definition by ID."""
        from builder_agent.registry.agent_registry import get_agent
        return get_agent(agent_id)

    def _get_adapter(self, protocol: str):
        """Get the protocol adapter for a given protocol."""
        adapter = AdapterRegistry.get(protocol)
        if not adapter:
            raise ValueError(f"Unknown protocol: {protocol}. Available: {AdapterRegistry.list_protocols()}")
        return adapter

    async def start_conversation(
        self,
        agent_id: str,
        initial_message: str,
        context: Optional[Dict] = None,
        task_summary: Optional[str] = None,
    ) -> AgentConversation:
        """
        Start a new conversation with an agent.

        Args:
            agent_id: The ID of the agent to talk to
            initial_message: The first message to send
            context: Optional context to include (user request, available resources, etc.)
            task_summary: Brief description of what we're asking (for UI display)

        Returns:
            AgentConversation: The new conversation object
        """
        # Get agent definition
        agent = self._get_agent(agent_id)
        if not agent:
            raise ValueError(f"Agent not found: {agent_id}")

        if not agent.enabled:
            raise ValueError(f"Agent is disabled: {agent_id}")

        # Create conversation
        conversation = AgentConversation(
            agent_id=agent_id,
            agent_name=agent.name,
            task_summary=task_summary or initial_message[:100],
            status=ConversationStatus.ACTIVE,
        )

        # Store the conversation
        # Note: We do NOT add the initial message here — the caller will
        # call send_message() which adds it. Adding here would cause duplicates
        # in the conversation history since both BUILDER and USER roles map
        # to "user" when sent to the agent via get_history_for_agent().
        self.active_conversations[conversation.id] = conversation

        logger.info(f"Started conversation {conversation.id} with {agent.name}")
        self._notify_update(conversation)

        return conversation

    def _format_context(self, context: Dict) -> str:
        """Format context dict into a readable string for the agent."""
        parts = []

        if "user_request" in context:
            parts.append(f"User's original request: {context['user_request']}")

        if "available_tools" in context:
            tools = context["available_tools"]
            if tools:
                parts.append(f"Available tools: {', '.join(tools)}")

        if "available_connections" in context:
            connections = context["available_connections"]
            if connections:
                parts.append(f"Available connections: {', '.join(connections)}")

        if "current_step" in context:
            parts.append(f"Current task: {context['current_step']}")

        # Add any other context items
        for key, value in context.items():
            if key not in ["user_request", "available_tools", "available_connections", "current_step"]:
                parts.append(f"{key}: {value}")

        return "\n".join(parts)

    async def send_message(
        self,
        conversation_id: str,
        message: str,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """
        Send a message in an existing conversation and stream the response.

        Args:
            conversation_id: The conversation ID
            message: The message to send
            **kwargs: Additional keyword arguments passed to the adapter's send_message.
                      For the WorkflowBuilderAdapter, this can include:
                      - workflow_state: Current workflow state for edit/refinement mode

        Yields:
            str: Chunks of the agent's response
        """
        conversation = self.active_conversations.get(conversation_id)
        if not conversation:
            raise ValueError(f"Conversation not found: {conversation_id}")

        if conversation.status not in [ConversationStatus.ACTIVE, ConversationStatus.WAITING_FOR_USER]:
            raise ValueError(f"Conversation is {conversation.status}, cannot send messages")

        # Get agent and adapter
        agent = self._get_agent(conversation.agent_id)
        if not agent:
            raise ValueError(f"Agent not found: {conversation.agent_id}")

        adapter = self._get_adapter(agent.protocol)

        # Add the outgoing message
        conversation.add_builder_message(message)
        conversation.status = ConversationStatus.ACTIVE
        self._notify_update(conversation)

        # Send and collect response
        response_chunks = []
        try:
            async for chunk in adapter.send_message(
                endpoint=agent.endpoint,
                message=message,
                conversation_history=conversation.get_history_for_agent(),
                system_prompt=agent.system_prompt,
                timeout=agent.timeout,
                # Pass conversation_id for adapters that need session tracking
                conversation_id=conversation_id,
                # Pass any additional kwargs (e.g., workflow_state for edit mode)
                **kwargs,
            ):
                response_chunks.append(chunk)
                yield chunk

            # Add the complete response to conversation
            full_response = "".join(response_chunks)
            conversation.add_agent_message(full_response)

            # Check if agent is asking a question or confirming completion
            self._analyze_agent_response(conversation, full_response)

        except TimeoutError as e:
            conversation.status = ConversationStatus.TIMEOUT
            conversation.error = str(e)
            logger.error(f"Conversation {conversation_id} timed out")

        except Exception as e:
            conversation.mark_failed(str(e))
            logger.error(f"Error in conversation {conversation_id}: {e}")
            raise

        finally:
            self._notify_update(conversation)

    def _analyze_agent_response(self, conversation: AgentConversation, response: str):
        """
        Analyze the agent's response to determine next state.

        Looks for:
        - Questions that need user input
        - Completion indicators
        - Error indicators
        """
        response_lower = response.lower()

        # Check for completion indicators
        completion_phrases = [
            "successfully created",
            "has been created",
            "completed successfully",
            "task complete",
            "done!",
            "finished!",
            "all done",
            "workflow is ready",
            "here's the result",
            "i've created",
            "i have created",
        ]

        for phrase in completion_phrases:
            if phrase in response_lower:
                conversation.mark_completed(response)
                logger.info(f"Conversation {conversation.id} completed")
                return

        # Check for question patterns that might need user input
        # These are questions the Builder might not be able to answer
        question_patterns = [
            "which",
            "what would you like",
            "do you want",
            "should i",
            "would you prefer",
            "can you specify",
            "please provide",
            "please specify",
            "i need to know",
            "could you tell me",
        ]

        # Only treat as needing user input if ends with question mark
        # and contains a question pattern
        if response.strip().endswith("?"):
            for pattern in question_patterns:
                if pattern in response_lower:
                    # This might need user input - the Builder will decide
                    # whether it can answer from context or needs to escalate
                    logger.debug(f"Agent asked question: {response[-200:]}")
                    return

        # Otherwise, conversation continues as active
        # The Builder can send another message or the agent might be waiting

    async def provide_user_input(
        self,
        conversation_id: str,
        user_response: str,
    ) -> AsyncGenerator[str, None]:
        """
        Provide user input for a conversation that was waiting.

        Args:
            conversation_id: The conversation ID
            user_response: The user's response

        Yields:
            str: Chunks of the agent's next response
        """
        conversation = self.active_conversations.get(conversation_id)
        if not conversation:
            raise ValueError(f"Conversation not found: {conversation_id}")

        # Forward to the agent — send_message() handles adding the message
        # to conversation history and setting status to ACTIVE, so we don't
        # duplicate those operations here.
        async for chunk in self.send_message(conversation_id, user_response):
            yield chunk

    def mark_waiting_for_user(self, conversation_id: str, question: str):
        """
        Mark a conversation as waiting for user input.

        Called by the Builder when it can't answer an agent's question.
        """
        conversation = self.active_conversations.get(conversation_id)
        if conversation:
            conversation.mark_waiting_for_user(question)
            self._notify_update(conversation)

    def get_conversation(self, conversation_id: str) -> Optional[AgentConversation]:
        """Get a conversation by ID."""
        return self.active_conversations.get(conversation_id)

    def get_active_conversations(self) -> List[AgentConversation]:
        """Get all active conversations."""
        return [
            conv for conv in self.active_conversations.values()
            if conv.status == ConversationStatus.ACTIVE
        ]

    def get_conversations_waiting_for_user(self) -> List[AgentConversation]:
        """Get conversations waiting for user input."""
        return [
            conv for conv in self.active_conversations.values()
            if conv.status == ConversationStatus.WAITING_FOR_USER
        ]

    def cleanup_completed_conversations(self, max_age_seconds: int = 3600):
        """Remove old completed/failed conversations."""
        now = datetime.utcnow()
        to_remove = []

        for conv_id, conv in self.active_conversations.items():
            if conv.status in [ConversationStatus.COMPLETED, ConversationStatus.FAILED, ConversationStatus.TIMEOUT]:
                updated = datetime.fromisoformat(conv.updated_at)
                age = (now - updated).total_seconds()
                if age > max_age_seconds:
                    to_remove.append(conv_id)

        for conv_id in to_remove:
            del self.active_conversations[conv_id]
            logger.debug(f"Cleaned up old conversation: {conv_id}")


# ═══════════════════════════════════════════════════════════════════════════
# DELEGATION HELPER
# ═══════════════════════════════════════════════════════════════════════════

def should_delegate_to_agent(
    capability_id: str,
    task_description: str,
) -> DelegationDecision:
    """
    Decide whether a task should be delegated to an agent.

    Args:
        capability_id: The capability being executed (e.g., "workflow.create_workflow")
        task_description: Description of what we're trying to do

    Returns:
        DelegationDecision: Whether to delegate and to which agent
    """
    from builder_agent.registry.agent_registry import get_agent_for_capability

    # Check if there's an agent that specializes in this capability
    agent = get_agent_for_capability(capability_id)

    if agent:
        return DelegationDecision.delegate_to(
            agent_id=agent.id,
            agent_name=agent.name,
            reason=f"Task matches {agent.name}'s specializations",
            confidence=0.8
        )

    # No matching agent - execute directly
    return DelegationDecision.direct_execution(
        reason="No specialized agent available for this task"
    )


# Singleton instance
_manager: Optional[AgentCommunicationManager] = None


def get_communication_manager() -> AgentCommunicationManager:
    """Get the singleton communication manager instance."""
    global _manager
    if _manager is None:
        _manager = AgentCommunicationManager()
    return _manager
