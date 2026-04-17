"""
Protocol Adapters
==================
Adapters for different agent communication protocols.

Currently supported:
- text_chat: Simple text-based chat, similar to how users chat with agents
- workflow_builder: Specialized adapter for the WorkflowAgent's HTTP API
"""

from .base import AgentProtocolAdapter
from .text_chat import TextChatAdapter
from .workflow_builder import WorkflowBuilderAdapter, parse_workflow_metadata, extract_workflow_commands

__all__ = [
    "AgentProtocolAdapter",
    "TextChatAdapter",
    "WorkflowBuilderAdapter",
    "parse_workflow_metadata",
    "extract_workflow_commands",
]
