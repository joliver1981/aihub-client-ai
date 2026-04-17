"""
Agent Communication Models
===========================
Data models for agent-to-agent communication.
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field
import uuid


class ConversationStatus(str, Enum):
    """Status of an agent conversation."""
    ACTIVE = "active"                      # Conversation in progress
    WAITING_FOR_USER = "waiting_for_user"  # Agent asked question Builder can't answer
    COMPLETED = "completed"                # Task finished successfully
    FAILED = "failed"                      # Task failed
    TIMEOUT = "timeout"                    # Agent didn't respond in time


class MessageRole(str, Enum):
    """Role of a message sender."""
    BUILDER = "builder"    # The Builder Agent
    AGENT = "agent"        # The specialized agent
    SYSTEM = "system"      # System messages (errors, status updates)
    USER = "user"          # Escalated user input


class AgentMessage(BaseModel):
    """A single message in an agent conversation."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: MessageRole
    content: str
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        use_enum_values = True


class AgentConversation(BaseModel):
    """
    Represents an ongoing or completed conversation with an agent.

    The Builder maintains this state to track:
    - What we asked the agent to do
    - The back-and-forth messages
    - Current status (active, waiting for user, completed, etc.)
    - Final result when done
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str                          # Which agent we're talking to
    agent_name: str                        # Display name for UI
    status: ConversationStatus = ConversationStatus.ACTIVE
    messages: List[AgentMessage] = Field(default_factory=list)
    task_summary: str                      # Brief description of what we asked
    result: Optional[str] = None           # Final output when completed
    error: Optional[str] = None            # Error message if failed
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

    # For tracking user escalation
    pending_question: Optional[str] = None  # Question waiting for user input

    class Config:
        use_enum_values = True

    def add_message(self, role: MessageRole, content: str, **metadata) -> AgentMessage:
        """Add a message to the conversation."""
        message = AgentMessage(
            role=role,
            content=content,
            metadata=metadata
        )
        self.messages.append(message)
        self.updated_at = datetime.utcnow().isoformat()
        return message

    def add_builder_message(self, content: str) -> AgentMessage:
        """Add a message from the Builder."""
        return self.add_message(MessageRole.BUILDER, content)

    def add_agent_message(self, content: str) -> AgentMessage:
        """Add a message from the specialized agent."""
        return self.add_message(MessageRole.AGENT, content)

    def add_system_message(self, content: str) -> AgentMessage:
        """Add a system message."""
        return self.add_message(MessageRole.SYSTEM, content)

    def add_user_message(self, content: str) -> AgentMessage:
        """Add a user message (escalated input)."""
        return self.add_message(MessageRole.USER, content)

    def mark_waiting_for_user(self, question: str):
        """Mark the conversation as waiting for user input."""
        self.status = ConversationStatus.WAITING_FOR_USER
        self.pending_question = question
        self.updated_at = datetime.utcnow().isoformat()

    def mark_completed(self, result: str):
        """Mark the conversation as completed."""
        self.status = ConversationStatus.COMPLETED
        self.result = result
        self.pending_question = None
        self.updated_at = datetime.utcnow().isoformat()

    def mark_failed(self, error: str):
        """Mark the conversation as failed."""
        self.status = ConversationStatus.FAILED
        self.error = error
        self.pending_question = None
        self.updated_at = datetime.utcnow().isoformat()

    def resume_after_user_input(self):
        """Resume the conversation after receiving user input."""
        if self.status == ConversationStatus.WAITING_FOR_USER:
            self.status = ConversationStatus.ACTIVE
            self.pending_question = None
            self.updated_at = datetime.utcnow().isoformat()

    def get_history_for_agent(self) -> List[Dict[str, str]]:
        """
        Get conversation history in a format suitable for sending to an agent.
        Returns list of {"role": "...", "content": "..."} dicts.
        """
        history = []
        for msg in self.messages:
            # Map roles for the receiving agent's perspective
            if msg.role == MessageRole.BUILDER:
                role = "user"  # Builder is the "user" from agent's perspective
            elif msg.role == MessageRole.AGENT:
                role = "assistant"  # Agent is the "assistant"
            elif msg.role == MessageRole.USER:
                role = "user"  # Escalated user input treated as user
            else:
                role = "system"

            history.append({
                "role": role,
                "content": msg.content
            })

        return history

    def to_ui_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for UI display."""
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "status": self.status,
            "task_summary": self.task_summary,
            "messages": [
                {
                    "id": msg.id,
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp,
                }
                for msg in self.messages
            ],
            "result": self.result,
            "error": self.error,
            "pending_question": self.pending_question,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class DelegationDecision(BaseModel):
    """
    Result of deciding whether to delegate a task to an agent.
    """
    should_delegate: bool
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    reason: str
    confidence: float = 0.0  # 0.0 to 1.0

    @classmethod
    def direct_execution(cls, reason: str = "Task can be executed directly") -> "DelegationDecision":
        """Create a decision for direct execution (no delegation)."""
        return cls(should_delegate=False, reason=reason)

    @classmethod
    def delegate_to(cls, agent_id: str, agent_name: str, reason: str, confidence: float = 0.8) -> "DelegationDecision":
        """Create a decision to delegate to a specific agent."""
        return cls(
            should_delegate=True,
            agent_id=agent_id,
            agent_name=agent_name,
            reason=reason,
            confidence=confidence
        )
