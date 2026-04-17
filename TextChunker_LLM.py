"""
LLM-Powered TextChunker - Drop-in Replacement
==============================================

This module provides an enhanced TextChunker that uses an LLM (via azureMiniQuickPrompt)
to intelligently identify chunk boundaries instead of brittle regex patterns.

Usage:
    # In config.py:
    VECTOR_USE_SMART_CHUNKING = True  # Enable LLM chunking
    VECTOR_USE_SMART_CHUNKING = False # Use standard chunking
    
    # The TextChunker interface remains exactly the same:
    chunker = TextChunker(chunk_size=1000, chunk_overlap=100)
    chunks = chunker.chunk_text(text, metadata={'doc_id': '123'})

Integration:
    Replace your existing TextChunker class in LLMDocumentVectorEngine.py with this one,
    or copy the _llm_* methods into your existing class.
"""

import re
import json
import hashlib
import logging
from typing import List, Dict, Any, Optional

# These will be imported from your project
try:
    import config as cfg
    VECTOR_CHUNK_SIZE = getattr(cfg, 'VECTOR_CHUNK_SIZE', 1000)
    VECTOR_CHUNK_OVERLAP = getattr(cfg, 'VECTOR_CHUNK_OVERLAP', 100)
    VECTOR_SPLITTER_TYPE = getattr(cfg, 'VECTOR_SPLITTER_TYPE', 'recursive_character')
    # Max characters to send to LLM in a single call (default 128k chars ≈ 32k tokens)
    VECTOR_SMART_CHUNKING_MAX_CHARS = getattr(cfg, 'VECTOR_SMART_CHUNKING_MAX_CHARS', 128000)
except ImportError:
    VECTOR_CHUNK_SIZE = 1000
    VECTOR_CHUNK_OVERLAP = 100
    VECTOR_SPLITTER_TYPE = 'recursive_character'
    VECTOR_SMART_CHUNKING_MAX_CHARS = 128000


# =============================================================================
# LLM CHUNKING PROMPTS
# =============================================================================

LLM_CHUNKING_SYSTEM_PROMPT = """You are a document chunking assistant. Your job is to split documents into logical chunks for semantic search retrieval.

You must return ONLY valid JSON - no explanation, no markdown fencing, just the JSON array."""

LLM_CHUNKING_USER_PROMPT = """Split this document into logical chunks for semantic search retrieval.

RULES:
1. NEVER split a table - keep all rows together with headers
2. NEVER split mid-sentence
3. Keep related paragraphs together when they discuss the same topic
4. Each chunk should be self-contained and understandable in isolation. If content belongs to a named section or entity (e.g., a department, product, location), include that identifier so the chunk makes sense without surrounding context.
5. Target size: {chunk_size} characters per chunk (flexible - prioritize logical boundaries over exact size)
6. If the document is short, it's fine to return just 1-2 chunks

Return ONLY a JSON array with this exact format:
[
  {{"text": "first chunk content here", "type": "paragraph"}},
  {{"text": "second chunk content here", "type": "table"}}
]

Valid types: header, paragraph, table, list, mixed

DOCUMENT TO CHUNK:
{document}"""


# =============================================================================
# TEXT CHUNKER CLASS
# =============================================================================

class TextChunker:
    """
    Enhanced text chunker with optional LLM-powered intelligent chunking.
    
    When VECTOR_USE_SMART_CHUNKING is True, uses an LLM to identify logical
    chunk boundaries. When False, uses standard LangChain-based splitting.
    
    This is a drop-in replacement for the existing TextChunker.
    """
    
    def __init__(self, 
                 chunk_size: int = VECTOR_CHUNK_SIZE, 
                 chunk_overlap: int = VECTOR_CHUNK_OVERLAP,
                 splitter_type: str = VECTOR_SPLITTER_TYPE,
                 use_smart_chunking: bool = None):
        """
        Initialize the text chunker.
        
        Args:
            chunk_size: Target size for each chunk (in characters)
            chunk_overlap: Number of characters to overlap between chunks
            splitter_type: Type of splitter for standard mode
            use_smart_chunking: Override config setting (None = use config)
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.splitter_type = splitter_type
        self.logger = logging.getLogger(__name__)
        
        # Determine if smart (LLM) chunking is enabled
        if use_smart_chunking is not None:
            self._use_smart = use_smart_chunking
        else:
            try:
                self._use_smart = getattr(cfg, 'VECTOR_USE_SMART_CHUNKING', False)
            except:
                self._use_smart = False
        
        # Also enable smart chunking if splitter_type is 'smart' or 'llm'
        if splitter_type in ('smart', 'llm'):
            self._use_smart = True
        
        # Initialize standard splitter (used as fallback or when smart=False)
        self.splitter = self._get_splitter()
        
        # LLM function - lazy loaded
        self._llm_func = None
    
    def _get_splitter(self):
        """Get the standard LangChain text splitter"""
        try:
            if self.splitter_type == "recursive_character":
                from langchain_text_splitters import RecursiveCharacterTextSplitter
                return RecursiveCharacterTextSplitter(
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap,
                    length_function=len,
                    separators=["\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " ", ""]
                )
            
            elif self.splitter_type == "token":
                from langchain_text_splitters import TokenTextSplitter
                return TokenTextSplitter(
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap
                )
            
            elif self.splitter_type == "markdown":
                from langchain_text_splitters import MarkdownHeaderTextSplitter
                return MarkdownHeaderTextSplitter(
                    headers_to_split_on=[
                        ("#", "Header 1"),
                        ("##", "Header 2"),
                        ("###", "Header 3"),
                    ]
                )
            
            elif self.splitter_type == "html":
                from langchain_text_splitters import HTMLHeaderTextSplitter
                return HTMLHeaderTextSplitter(
                    headers_to_split_on=[
                        ("h1", "Header 1"),
                        ("h2", "Header 2"),
                        ("h3", "Header 3"),
                    ]
                )
            
            elif self.splitter_type == "python":
                from langchain_text_splitters import PythonCodeTextSplitter
                return PythonCodeTextSplitter(
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap
                )
            
            else:
                from langchain_text_splitters import RecursiveCharacterTextSplitter
                return RecursiveCharacterTextSplitter(
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap
                )
                
        except Exception as e:
            self.logger.warning(f"LangChain splitters not available: {e}")
            return None
    
    def _get_llm_func(self):
        """
        Lazy load the LLM function.
        
        Attempts import strategies in order:
        1. claudeQuickPrompt (Anthropic API — no pandas/AppUtils dependency)
        2. Direct import from AppUtils (works if pandas is installed)
        3. Standalone OpenAI function using openai + api_keys_config (last resort)
        """
        if self._llm_func is not None:
            return self._llm_func
        
        # Strategy 1: Claude API (preferred for doc processing — zero heavy dependencies)
        try:
            from claudeQuickPrompt import claudeQuickPrompt
            self._llm_func = claudeQuickPrompt
            self.logger.info("LLM function loaded: claudeQuickPrompt (Anthropic API)")
            return self._llm_func
        except ImportError as e:
            self.logger.warning(f"claudeQuickPrompt not available ({e}), trying AppUtils")
        
        # Strategy 2: Try importing from AppUtils directly (requires pandas)
        try:
            from AppUtils import azureMiniQuickPrompt
            self._llm_func = azureMiniQuickPrompt
            self.logger.info("LLM function loaded: azureMiniQuickPrompt (AppUtils)")
            return self._llm_func
        except ImportError as e:
            self.logger.warning(f"Could not import from AppUtils ({e}), building standalone LLM function")
        
        # Strategy 3: Build standalone OpenAI function (avoids AppUtils and its pandas dependency)
        try:
            from openai import OpenAI as _OpenAI, AzureOpenAI as _AzureOpenAI
            from api_keys_config import get_openai_config

            def _standalone_mini_prompt(prompt, system="You are an assistant.", temp=0.0):
                """Standalone LLM call that bypasses AppUtils (no pandas dependency)"""
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt}
                ]

                config = get_openai_config(use_alternate_api=False, use_mini=True)

                if config['api_type'] == 'open_ai':
                    client = _OpenAI(api_key=config['api_key'])
                    model = config['model']
                else:
                    client = _AzureOpenAI(
                        api_key=config['api_key'],
                        api_version=config['api_version'],
                        azure_endpoint=config['api_base']
                    )
                    model = config['deployment_id']

                kwargs = {"messages": messages, "model": model}
                if config.get('reasoning_effort'):
                    kwargs["reasoning"] = {"effort": config['reasoning_effort']}
                    kwargs["temperature"] = 1.0
                else:
                    kwargs["temperature"] = temp

                chat_completion = client.chat.completions.create(**kwargs)
                response = str(chat_completion.choices[0].message.content)
                response = response.replace('```json', '').replace('```sql', '').replace('python```', '').replace('```', '')
                return response
            
            self._llm_func = _standalone_mini_prompt
            self.logger.info("LLM function built standalone (openai + api_keys_config)")
            return self._llm_func
            
        except ImportError as e:
            self.logger.error(f"Could not build standalone LLM function: {e}")
            raise ImportError(
                f"Cannot initialize LLM chunking. All strategies failed: "
                f"claudeQuickPrompt not found, AppUtils import failed (likely missing pandas), "
                f"and standalone fallback also failed: {e}. "
                f"Install anthropic package or ensure openai + api_keys_config are available."
            )
    
    def chunk_text(self, text: str, metadata: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Split text into chunks with metadata preservation.
        
        This is the main entry point - same interface as the original TextChunker.
        
        Args:
            text: Text to chunk
            metadata: Base metadata to include with each chunk
            
        Returns:
            List of chunk dictionaries with 'text' and 'metadata' keys
        """
        if not text or not text.strip():
            return []
        
        text = text.strip()
        
        # If text is small enough, return as single chunk
        if len(text) <= self.chunk_size:
            return self._create_single_chunk(text, metadata)
        
        # Use LLM chunking or standard chunking
        if self._use_smart:
            try:
                return self._llm_chunk(text, metadata)
            except Exception as e:
                self.logger.error(f"LLM chunking failed: {e}. Falling back to standard chunking.")
                print(f"LLM chunking failed: {e}. Falling back to standard chunking.")
                return self._standard_chunk(text, metadata)
        else:
            return self._standard_chunk(text, metadata)
    
    # =========================================================================
    # LLM-POWERED CHUNKING
    # =========================================================================
    
    def _llm_chunk(self, text: str, metadata: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Use LLM to intelligently chunk the document.
        
        Args:
            text: Text to chunk
            metadata: Base metadata to include with each chunk
            
        Returns:
            List of chunk dictionaries
        """
        llm_func = self._get_llm_func()
        
        # For very long documents, use windowed processing
        # Default 128k chars handles virtually all single-page documents
        max_input_size = VECTOR_SMART_CHUNKING_MAX_CHARS
        
        if len(text) > max_input_size:
            self.logger.info(f"Document exceeds max size ({len(text)} > {max_input_size}), using windowed chunking")
            return self._llm_chunk_windowed(text, metadata, max_input_size)
        
        # Single-pass chunking for normal-sized documents
        return self._llm_chunk_single(text, metadata, llm_func)
    
    def _llm_chunk_single(self, text: str, metadata: Dict[str, Any], 
                          llm_func) -> List[Dict[str, Any]]:
        """Process a document in a single LLM call."""
        
        # Build the prompt
        prompt = LLM_CHUNKING_USER_PROMPT.format(
            chunk_size=self.chunk_size,
            document=text
        )
        
        # Call the LLM
        self.logger.info(f"Calling LLM for chunking ({len(text)} chars)")
        response = llm_func(prompt, system=LLM_CHUNKING_SYSTEM_PROMPT, temp=0.0)
        
        # Parse the response
        chunks_data = self._parse_llm_response(response)
        
        if not chunks_data:
            self.logger.warning("LLM returned no chunks, falling back to standard")
            return self._standard_chunk(text, metadata)
        
        # Validate chunks contain actual text from the document
        chunks_data = self._validate_chunks(chunks_data, text)
        
        if not chunks_data:
            self.logger.warning("No valid chunks after validation, falling back to standard")
            return self._standard_chunk(text, metadata)
        
        # Convert to standard chunk format
        return self._format_llm_chunks(chunks_data, metadata)
    
    def _llm_chunk_windowed(self, text: str, metadata: Dict[str, Any], 
                            window_size: int) -> List[Dict[str, Any]]:
        """
        Process a large document using overlapping windows.
        
        For documents larger than the LLM context window, we:
        1. Split into overlapping segments
        2. Ask LLM to identify boundaries in each segment
        3. Merge boundaries and create final chunks
        """
        llm_func = self._get_llm_func()
        
        # Calculate window parameters
        overlap = min(500, window_size // 4)  # 25% overlap or 500 chars
        step = window_size - overlap
        
        all_boundaries = [0]  # Start of document is always a boundary
        
        # Process each window
        position = 0
        while position < len(text):
            window_end = min(position + window_size, len(text))
            window_text = text[position:window_end]
            
            # Ask LLM to identify split points in this window
            boundaries = self._get_boundaries_from_llm(window_text, llm_func)
            
            # Adjust boundaries to absolute positions and add to list
            for b in boundaries:
                absolute_pos = position + b
                if absolute_pos > 0 and absolute_pos < len(text):
                    all_boundaries.append(absolute_pos)
            
            position += step
            
            if window_end >= len(text):
                break
        
        all_boundaries.append(len(text))  # End of document
        
        # Deduplicate and sort boundaries
        all_boundaries = sorted(set(all_boundaries))
        
        # Merge boundaries that are too close together
        merged_boundaries = self._merge_close_boundaries(all_boundaries, min_gap=100)
        
        # Create chunks from boundaries
        chunks_data = []
        for i in range(len(merged_boundaries) - 1):
            start = merged_boundaries[i]
            end = merged_boundaries[i + 1]
            chunk_text = text[start:end].strip()
            if chunk_text:
                chunks_data.append({
                    'text': chunk_text,
                    'type': 'mixed'  # Can't determine type in windowed mode
                })
        
        return self._format_llm_chunks(chunks_data, metadata)
    
    def _get_boundaries_from_llm(self, window_text: str, llm_func) -> List[int]:
        """Ask LLM to identify chunk boundaries within a text window."""
        
        prompt = f"""Analyze this text and identify the character positions where it should be split into chunks for search retrieval.

Rules:
1. Never split inside a table
2. Never split mid-sentence
3. Split at logical topic/section changes
4. Target chunk size: ~{self.chunk_size} characters

Return ONLY a JSON array of character positions (integers) where splits should occur.
Example: [450, 920, 1350]

If no splits are needed, return: []

TEXT:
{window_text}"""
        
        response = llm_func(prompt, system="Return only a JSON array of integers.", temp=0.0)
        
        try:
            boundaries = self._parse_json_response(response)
            if isinstance(boundaries, list):
                return [b for b in boundaries if isinstance(b, int)]
        except:
            pass
        
        return []
    
    def _merge_close_boundaries(self, boundaries: List[int], min_gap: int) -> List[int]:
        """Merge boundaries that are too close together."""
        if len(boundaries) <= 2:
            return boundaries
        
        merged = [boundaries[0]]
        for b in boundaries[1:]:
            if b - merged[-1] >= min_gap:
                merged.append(b)
            # else: skip this boundary, too close to previous
        
        # Ensure we include the end
        if boundaries[-1] not in merged:
            merged.append(boundaries[-1])
        
        return merged
    
    def _parse_llm_response(self, response: str) -> List[Dict[str, Any]]:
        """
        Parse LLM response into chunk data, handling various response formats.
        
        Args:
            response: Raw LLM response string
            
        Returns:
            List of chunk dictionaries with 'text' and 'type' keys
        """
        if not response:
            return []
        
        parsed = self._parse_json_response(response)
        
        if not parsed:
            return []
        
        # Validate structure
        if not isinstance(parsed, list):
            self.logger.warning(f"LLM response is not a list: {type(parsed)}")
            return []
        
        # Validate each chunk has required fields
        valid_chunks = []
        for item in parsed:
            if isinstance(item, dict) and 'text' in item:
                chunk = {
                    'text': str(item['text']).strip(),
                    'type': str(item.get('type', 'mixed'))
                }
                if chunk['text']:  # Only add non-empty chunks
                    valid_chunks.append(chunk)
        
        return valid_chunks
    
    def _parse_json_response(self, response: str) -> Any:
        """
        Robustly parse JSON from LLM response.
        
        Handles:
        - Clean JSON
        - JSON with ```json fencing
        - JSON with ``` fencing
        - JSON with leading/trailing whitespace
        - JSON with explanation text before/after
        """
        if not response:
            return None
        
        original = response
        
        # Step 1: Remove markdown code fencing
        # Handle ```json ... ``` or ``` ... ```
        fenced_pattern = r'```(?:json)?\s*([\s\S]*?)\s*```'
        match = re.search(fenced_pattern, response, re.IGNORECASE)
        if match:
            response = match.group(1)
        
        # Step 2: Strip whitespace
        response = response.strip()
        
        # Step 3: Try direct parse
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass
        
        # Step 4: Try to find JSON array in the response
        # Look for [...] pattern
        array_match = re.search(r'\[[\s\S]*\]', response)
        if array_match:
            try:
                return json.loads(array_match.group())
            except json.JSONDecodeError:
                pass
        
        # Step 5: Try to find JSON object in the response
        # Look for {...} pattern
        obj_match = re.search(r'\{[\s\S]*\}', response)
        if obj_match:
            try:
                return json.loads(obj_match.group())
            except json.JSONDecodeError:
                pass
        
        # Step 6: Log failure for debugging
        self.logger.warning(f"Could not parse JSON from response: {original[:200]}...")
        
        return None
    
    def _validate_chunks(self, chunks_data: List[Dict], original_text: str) -> List[Dict]:
        """
        Validate that chunks contain actual content from the document.
        Currently bypassed — returns all chunks as-is.
        """
        # TODO: implement a reliable validation strategy
        return chunks_data
    
    def _format_llm_chunks(self, chunks_data: List[Dict], 
                           metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Convert LLM chunk data to standard chunk format."""
        
        chunk_objects = []
        total_chunks = len(chunks_data)
        
        for i, chunk_data in enumerate(chunks_data):
            chunk_text = chunk_data['text']
            chunk_type = chunk_data.get('type', 'mixed')
            
            chunk_metadata = (metadata or {}).copy()
            chunk_metadata.update({
                'chunk_index': i,
                'total_chunks': total_chunks,
                'chunk_size': len(chunk_text),
                'is_complete_text': (total_chunks == 1),
                'chunk_hash': hashlib.md5(chunk_text.encode()).hexdigest()[:8],
                'splitter_type': 'llm',
                'chunk_type': chunk_type,
                'contains_table': (chunk_type == 'table'),
                'contains_list': (chunk_type == 'list'),
            })
            
            # Add navigation metadata
            if i > 0:
                chunk_metadata['has_previous_chunk'] = True
            if i < total_chunks - 1:
                chunk_metadata['has_next_chunk'] = True
            
            chunk_objects.append({
                'text': chunk_text,
                'metadata': chunk_metadata
            })
        
        return chunk_objects
    
    # =========================================================================
    # STANDARD CHUNKING (Original behavior)
    # =========================================================================
    
    def _standard_chunk(self, text: str, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Standard chunking using LangChain or fallback."""
        
        if self.splitter:
            try:
                chunks = self.splitter.split_text(text)
            except Exception as e:
                self.logger.warning(f"LangChain splitter failed: {e}")
                chunks = self._fallback_split(text)
        else:
            chunks = self._fallback_split(text)
        
        return self._format_standard_chunks(chunks, metadata)
    
    def _format_standard_chunks(self, chunks: List[str], 
                                metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Format standard chunks with metadata."""
        
        chunk_objects = []
        total_chunks = len(chunks)
        
        for i, chunk_text in enumerate(chunks):
            chunk_text = chunk_text.strip()
            if not chunk_text:
                continue
            
            chunk_metadata = (metadata or {}).copy()
            chunk_metadata.update({
                'chunk_index': i,
                'total_chunks': total_chunks,
                'chunk_size': len(chunk_text),
                'is_complete_text': False,
                'chunk_hash': hashlib.md5(chunk_text.encode()).hexdigest()[:8],
                'splitter_type': self.splitter_type,
                'chunk_type': 'mixed',
                'contains_table': False,
                'contains_list': False,
            })
            
            if i > 0:
                chunk_metadata['has_previous_chunk'] = True
            if i < total_chunks - 1:
                chunk_metadata['has_next_chunk'] = True
            
            chunk_objects.append({
                'text': chunk_text,
                'metadata': chunk_metadata
            })
        
        # Update total_chunks in case we filtered empty chunks
        for chunk in chunk_objects:
            chunk['metadata']['total_chunks'] = len(chunk_objects)
        
        return chunk_objects
    
    def _create_single_chunk(self, text: str, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Create a single chunk when text fits within chunk_size."""
        
        chunk_metadata = (metadata or {}).copy()
        chunk_metadata.update({
            'chunk_index': 0,
            'total_chunks': 1,
            'chunk_size': len(text),
            'is_complete_text': True,
            'chunk_hash': hashlib.md5(text.encode()).hexdigest()[:8],
            'splitter_type': 'llm' if self._use_smart else self.splitter_type,
            'chunk_type': 'mixed',
            'contains_table': False,
            'contains_list': False,
        })
        
        return [{'text': text, 'metadata': chunk_metadata}]
    
    def _fallback_split(self, text: str) -> List[str]:
        """Simple fallback splitting when LangChain is not available."""
        
        separators = ['\n\n', '\n', '. ', ' ']
        
        def split_recursive(text, seps):
            if not seps or len(text) <= self.chunk_size:
                result = []
                for i in range(0, len(text), self.chunk_size - self.chunk_overlap):
                    chunk = text[i:i + self.chunk_size]
                    if chunk.strip():
                        result.append(chunk)
                return result
            
            sep = seps[0]
            parts = text.split(sep)
            result = []
            
            current = ""
            for part in parts:
                if len(current + part + sep) <= self.chunk_size:
                    current += part + sep
                else:
                    if current:
                        result.append(current.rstrip(sep))
                    if len(part) > self.chunk_size:
                        result.extend(split_recursive(part, seps[1:]))
                    else:
                        current = part + sep
            
            if current:
                result.append(current.rstrip(sep))
            
            return result
        
        return split_recursive(text, separators)


# =============================================================================
# CONVENIENCE FUNCTION
# =============================================================================

def create_chunker(use_smart: bool = None, **kwargs) -> TextChunker:
    """
    Factory function to create a TextChunker.
    
    Args:
        use_smart: Enable LLM chunking (None = use config)
        **kwargs: Additional arguments passed to TextChunker
        
    Returns:
        Configured TextChunker instance
    """
    return TextChunker(use_smart_chunking=use_smart, **kwargs)
