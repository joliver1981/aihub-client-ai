"""
Anthropic Streaming Helper

This module provides helper functions to handle Anthropic API calls that require streaming
but return responses in a format compatible with non-streaming code.

Newer Anthropic models (Claude 3.5+, Claude 4) require streaming for certain API calls.
This wrapper handles the streaming internally and returns a complete response object.
"""

import anthropic
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class StreamedMessageResponse:
    """
    A response object that mimics the structure of anthropic.types.Message
    but is built from streamed content.
    
    This allows existing code that expects non-streaming responses to work
    without modification.
    """
    id: str
    type: str
    role: str
    content: List[Dict[str, Any]]
    model: str
    stop_reason: Optional[str]
    stop_sequence: Optional[str]
    usage: Dict[str, int]
    
    @property
    def text(self) -> str:
        """Extract text content from the response"""
        text_parts = []
        for block in self.content:
            if isinstance(block, dict) and block.get('type') == 'text':
                text_parts.append(block.get('text', ''))
            elif hasattr(block, 'type') and block.type == 'text':
                text_parts.append(block.text)
        return ''.join(text_parts)


class ContentBlock:
    """Mimics anthropic.types.ContentBlock for compatibility"""
    def __init__(self, block_type: str, text: str = ""):
        self.type = block_type
        self.text = text


class StreamedResponse:
    """
    Wrapper class that collects streamed response and provides
    an interface compatible with non-streaming Message responses.
    """
    
    def __init__(self):
        self.id: str = ""
        self.type: str = "message"
        self.role: str = "assistant"
        self.model: str = ""
        self.stop_reason: Optional[str] = None
        self.stop_sequence: Optional[str] = None
        self.content: List[ContentBlock] = []
        self.usage: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        
        # Internal state for building content
        self._current_text: str = ""
        self._content_blocks: List[Dict[str, Any]] = []
    
    def add_text(self, text: str):
        """Add text to the current content block"""
        self._current_text += text
    
    def finalize(self):
        """Finalize the response after streaming is complete"""
        if self._current_text:
            self.content = [ContentBlock("text", self._current_text)]
    
    def model_dump(self) -> Dict[str, Any]:
        """Return response as dictionary (compatible with Message.model_dump())"""
        return {
            "id": self.id,
            "type": self.type,
            "role": self.role,
            "model": self.model,
            "stop_reason": self.stop_reason,
            "stop_sequence": self.stop_sequence,
            "content": [{"type": "text", "text": self._current_text}],
            "usage": self.usage
        }


def create_message_with_streaming(
    client: anthropic.Anthropic,
    model: str,
    max_tokens: int,
    messages: List[Dict[str, Any]],
    system: Optional[str] = None,
    temperature: float = 0,
    **kwargs
) -> StreamedResponse:
    """
    Create a message using streaming, but return a complete response object.
    
    This function handles the streaming internally and collects all content
    into a response object that is compatible with non-streaming code.
    
    Args:
        client: Initialized Anthropic client
        model: Model name (e.g., 'claude-3-7-sonnet-20250219')
        max_tokens: Maximum tokens in response
        messages: List of message dictionaries
        system: Optional system prompt
        temperature: Temperature for generation (default 0)
        **kwargs: Additional arguments passed to messages.create()
    
    Returns:
        StreamedResponse object with collected content
        
    Example:
        response = create_message_with_streaming(
            client=anthropic_client,
            model="claude-3-7-sonnet-20250219",
            max_tokens=4096,
            system="You are a helpful assistant.",
            messages=[{"role": "user", "content": "Hello!"}]
        )
        print(response.content[0].text)
    """
    response = StreamedResponse()
    
    try:
        # Build the API call parameters
        api_params = {
            "model": model,
            "max_tokens": int(max_tokens),
            "messages": messages,
            "temperature": temperature,
            "stream": True
        }
        
        # Add system prompt if provided
        if system:
            api_params["system"] = system
        
        # Add any additional kwargs
        api_params.update(kwargs)
        
        # Make the streaming API call
        with client.messages.stream(**{k: v for k, v in api_params.items() if k != 'stream'}) as stream:
            # Process streamed events
            for event in stream:
                # Handle different event types
                if hasattr(event, 'type'):
                    if event.type == 'message_start':
                        if hasattr(event, 'message'):
                            response.id = getattr(event.message, 'id', '')
                            response.model = getattr(event.message, 'model', model)
                            response.role = getattr(event.message, 'role', 'assistant')
                            if hasattr(event.message, 'usage'):
                                response.usage['input_tokens'] = getattr(event.message.usage, 'input_tokens', 0)
                    
                    elif event.type == 'content_block_delta':
                        if hasattr(event, 'delta') and hasattr(event.delta, 'text'):
                            response.add_text(event.delta.text)
                    
                    elif event.type == 'message_delta':
                        if hasattr(event, 'delta'):
                            response.stop_reason = getattr(event.delta, 'stop_reason', None)
                            response.stop_sequence = getattr(event.delta, 'stop_sequence', None)
                        if hasattr(event, 'usage'):
                            response.usage['output_tokens'] = getattr(event.usage, 'output_tokens', 0)
        
        # Finalize the response
        response.finalize()
        
        logger.debug(f"Streaming complete. Tokens: {response.usage}")
        
        return response
        
    except Exception as e:
        logger.error(f"Error in streaming API call: {str(e)}")
        raise


def create_message_with_streaming_simple(
    client: anthropic.Anthropic,
    model: str,
    max_tokens: int,
    messages: List[Dict[str, Any]],
    system: Optional[str] = None,
    temperature: float = 0,
    **kwargs
) -> StreamedResponse:
    """
    Simplified version using the stream helper's built-in text collection.
    
    This is an alternative implementation that may be more reliable
    for certain use cases.
    """
    response = StreamedResponse()
    
    try:
        # Build the API call parameters
        api_params = {
            "model": model,
            "max_tokens": int(max_tokens),
            "messages": messages,
            "temperature": temperature,
        }
        
        if system:
            api_params["system"] = system
        
        api_params.update(kwargs)
        
        # Use the stream manager
        with client.messages.stream(**api_params) as stream:
            # Get the final message after streaming completes
            final_message = stream.get_final_message()
            
            # Copy attributes from final message
            response.id = final_message.id
            response.model = final_message.model
            response.role = final_message.role
            response.stop_reason = final_message.stop_reason
            response.stop_sequence = final_message.stop_sequence
            
            # Extract text content
            for block in final_message.content:
                if hasattr(block, 'text'):
                    response.add_text(block.text)
            
            # Copy usage
            if final_message.usage:
                response.usage['input_tokens'] = final_message.usage.input_tokens
                response.usage['output_tokens'] = final_message.usage.output_tokens
        
        response.finalize()
        return response
        
    except Exception as e:
        logger.error(f"Error in streaming API call: {str(e)}")
        raise


# Convenience function that matches the original API signature
def anthropic_messages_create(
    client: anthropic.Anthropic,
    model: str,
    max_tokens: int,
    messages: List[Dict[str, Any]],
    system: Optional[str] = None,
    temperature: float = 0,
    use_streaming: bool = True,
    **kwargs
) -> Union[StreamedResponse, Any]:
    """
    Drop-in replacement for client.messages.create() that handles streaming automatically.
    
    Args:
        client: Initialized Anthropic client
        model: Model name
        max_tokens: Maximum tokens
        messages: Messages list
        system: Optional system prompt
        temperature: Temperature (default 0)
        use_streaming: If True, use streaming (required for newer models)
        **kwargs: Additional arguments
        
    Returns:
        Response object compatible with non-streaming response
    """
    if use_streaming:
        return create_message_with_streaming_simple(
            client=client,
            model=model,
            max_tokens=max_tokens,
            messages=messages,
            system=system,
            temperature=temperature,
            **kwargs
        )
    else:
        # Fall back to non-streaming (may not work with newer models)
        api_params = {
            "model": model,
            "max_tokens": int(max_tokens),
            "messages": messages,
            "temperature": temperature,
        }
        if system:
            api_params["system"] = system
        api_params.update(kwargs)
        
        return client.messages.create(**api_params)
