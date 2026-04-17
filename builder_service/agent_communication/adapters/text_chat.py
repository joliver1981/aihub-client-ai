"""
Text Chat Protocol Adapter
===========================
Simple text-based chat protocol, similar to how users chat with agents.

This adapter:
- Sends messages as plain text with conversation history
- Receives streamed text responses via SSE
- Handles the protocol just like a user chatting in the UI

Request format:
POST {endpoint}
{
    "message": "...",
    "history": [
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."},
        ...
    ],
    "system_prompt": "..." (optional)
}

Response format:
SSE stream with "data: {text}" lines, or a JSON response with "response" field.
"""

import asyncio
import logging
from typing import AsyncGenerator, List, Dict, Optional
import httpx

from .base import AgentProtocolAdapter, AdapterRegistry

logger = logging.getLogger(__name__)


class TextChatAdapter(AgentProtocolAdapter):
    """
    Adapter for simple text-based chat communication.

    This is the most straightforward protocol - agents receive messages
    just like they would from a user, and respond in natural language.
    """

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def protocol_name(self) -> str:
        return "text_chat"

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
        return self._client

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
        Send a message and stream the response.

        The endpoint should accept POST requests with JSON body and
        return either:
        - SSE stream (preferred for real-time updates)
        - JSON response with "response" field
        """
        client = await self._get_client()

        # Build request payload
        payload = {
            "message": message,
            "history": conversation_history,
        }

        if system_prompt:
            payload["system_prompt"] = system_prompt

        # Add any extra kwargs to payload
        payload.update(kwargs)

        logger.info(f"Sending message to agent at {endpoint}")
        logger.debug(f"Payload: {payload}")

        try:
            # Try streaming first (SSE)
            async with client.stream(
                "POST",
                endpoint,
                json=payload,
                timeout=float(timeout)
            ) as response:
                response.raise_for_status()

                content_type = response.headers.get("content-type", "")

                if "text/event-stream" in content_type:
                    # Handle SSE stream
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]  # Remove "data: " prefix
                            if data.strip() == "[DONE]":
                                break
                            yield data
                        elif line.startswith("event: error"):
                            # Handle error events
                            next_line = await response.aiter_lines().__anext__()
                            if next_line.startswith("data: "):
                                error_msg = next_line[6:]
                                logger.error(f"Agent error: {error_msg}")
                                raise Exception(f"Agent error: {error_msg}")

                elif "application/json" in content_type:
                    # Handle JSON response (non-streaming)
                    body = await response.aread()
                    import json
                    data = json.loads(body)

                    if "response" in data:
                        yield data["response"]
                    elif "content" in data:
                        yield data["content"]
                    elif "message" in data:
                        # Check if this is an error response with status
                        if "status" in data and data.get("status") == "error":
                            yield f"Error: {data['message']}"
                        else:
                            yield data["message"]
                    elif "status" in data:
                        # Status-only response (e.g., {"status": "success"})
                        status = data["status"]
                        msg = data.get("message", "")
                        if msg:
                            yield f"{status.capitalize()}: {msg}"
                        else:
                            yield status.capitalize()
                    else:
                        # Return the whole response as formatted string
                        import json as json_module
                        yield json_module.dumps(data, indent=2)

                else:
                    # Handle plain text response
                    async for chunk in response.aiter_text():
                        yield chunk

        except httpx.TimeoutException:
            logger.error(f"Timeout communicating with agent at {endpoint}")
            raise TimeoutError(f"Agent at {endpoint} did not respond within {timeout} seconds")

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error from agent: {e.response.status_code}")
            raise ConnectionError(f"Agent returned error: {e.response.status_code}")

        except Exception as e:
            logger.error(f"Error communicating with agent: {e}")
            raise

    async def check_health(self, endpoint: str, timeout: int = 5) -> bool:
        """
        Check if the agent is available.

        Tries to reach a health endpoint or the main endpoint with a simple ping.
        """
        client = await self._get_client()

        # Try common health check patterns
        health_endpoints = [
            endpoint.rstrip("/") + "/health",
            endpoint.rstrip("/").rsplit("/", 1)[0] + "/health",  # Parent path
        ]

        for health_url in health_endpoints:
            try:
                response = await client.get(health_url, timeout=float(timeout))
                if response.status_code == 200:
                    return True
            except Exception:
                continue

        # If no health endpoint, try a simple POST with empty/test message
        try:
            response = await client.post(
                endpoint,
                json={"message": "ping", "history": []},
                timeout=float(timeout)
            )
            return response.status_code in (200, 400)  # 400 might mean "bad request" but agent is up
        except Exception:
            return False

    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


# Register the adapter
_text_chat_adapter = TextChatAdapter()
AdapterRegistry.register(_text_chat_adapter)
