"""
Base Protocol Adapter
======================
Abstract base class for agent communication protocols.

To add a new protocol:
1. Create a new adapter class that inherits from AgentProtocolAdapter
2. Implement send_message() and check_health()
3. Register the adapter in the AdapterRegistry
"""

from abc import ABC, abstractmethod
from typing import AsyncGenerator, List, Dict, Optional, Any
import logging

logger = logging.getLogger(__name__)


class AgentProtocolAdapter(ABC):
    """
    Base class for agent communication protocols.

    Each adapter handles a specific way of communicating with agents:
    - text_chat: Simple text-based chat (like user conversations)
    - structured: JSON-based structured messages (future)
    - function_call: Direct function invocation (future)
    """

    @property
    @abstractmethod
    def protocol_name(self) -> str:
        """Return the name of this protocol (e.g., 'text_chat')."""
        pass

    @abstractmethod
    async def send_message(
        self,
        endpoint: str,
        message: str,
        conversation_history: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        timeout: int = 120,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        Send a message to an agent and stream the response.

        Args:
            endpoint: The agent's endpoint (URL or internal path)
            message: The message to send
            conversation_history: Previous messages in the conversation
            system_prompt: Optional system prompt to include
            timeout: Max seconds to wait for response
            **kwargs: Additional protocol-specific options

        Yields:
            str: Chunks of the agent's response as they arrive
        """
        pass

    @abstractmethod
    async def check_health(self, endpoint: str, timeout: int = 5) -> bool:
        """
        Check if the agent is available and responding.

        Args:
            endpoint: The agent's endpoint
            timeout: Max seconds to wait for health check

        Returns:
            bool: True if agent is healthy, False otherwise
        """
        pass

    async def send_message_sync(
        self,
        endpoint: str,
        message: str,
        conversation_history: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        timeout: int = 120,
        **kwargs
    ) -> str:
        """
        Send a message and wait for the complete response.
        Convenience method that collects all streamed chunks.

        Returns:
            str: The complete response
        """
        chunks = []
        async for chunk in self.send_message(
            endpoint=endpoint,
            message=message,
            conversation_history=conversation_history,
            system_prompt=system_prompt,
            timeout=timeout,
            **kwargs
        ):
            chunks.append(chunk)
        return "".join(chunks)


class AdapterRegistry:
    """
    Registry for protocol adapters.
    Maps protocol names to adapter instances.
    """

    _adapters: Dict[str, AgentProtocolAdapter] = {}

    @classmethod
    def register(cls, adapter: AgentProtocolAdapter):
        """Register an adapter."""
        cls._adapters[adapter.protocol_name] = adapter
        logger.info(f"Registered adapter: {adapter.protocol_name}")

    @classmethod
    def get(cls, protocol_name: str) -> Optional[AgentProtocolAdapter]:
        """Get an adapter by protocol name."""
        return cls._adapters.get(protocol_name)

    @classmethod
    def list_protocols(cls) -> List[str]:
        """List all registered protocol names."""
        return list(cls._adapters.keys())

    @classmethod
    def has_protocol(cls, protocol_name: str) -> bool:
        """Check if a protocol is registered."""
        return protocol_name in cls._adapters
