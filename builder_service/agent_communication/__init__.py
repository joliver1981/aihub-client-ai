"""
Agent Communication Module
===========================
Handles communication between the Builder Agent and other specialized agents.

This module provides:
- Models for agent messages and conversations
- Protocol adapters for different communication methods
- Manager for orchestrating agent conversations
"""

from .models import (
    AgentMessage,
    AgentConversation,
    ConversationStatus,
)
from .manager import AgentCommunicationManager

__all__ = [
    "AgentMessage",
    "AgentConversation",
    "ConversationStatus",
    "AgentCommunicationManager",
]
