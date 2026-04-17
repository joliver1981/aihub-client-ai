# ai_key_matcher.py
"""
AI-Assisted Key Matching for Excel Update Operations

This module provides AI-powered canonical key generation to handle
cases where key fields have slight variations that should match:
- "Carton construction materials and specifications"
- "Carton construction materials"  
- "Carton construction material and specifications"

All three should be treated as the same record.

Usage:
    matcher = AIKeyMatcher(llm_client)
    canonical_keys = matcher.generate_canonical_keys(
        records=[...],
        key_columns=['customer', 'program_type', 'topic', 'requirement'],
        instructions="Focus on the core requirement concept, ignore minor wording differences"
    )
"""

import json
import logging
from logging.handlers import WatchedFileHandler
import hashlib
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
import os
from CommonUtils import rotate_logs_on_startup, get_log_path
from AppUtils import azureMiniQuickPrompt


# Configure logging
def setup_logging():
    """Configure logging for the workflow execution"""
    logger = logging.getLogger("AIKeyMatcher")
    log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
    log_level = getattr(logging, log_level_name, logging.DEBUG)
    logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler = WatchedFileHandler(filename=os.getenv('AI_KEY_MATCHER_LOG', get_log_path('ai_key_matcher_log.txt')), encoding='utf-8')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger

rotate_logs_on_startup(os.getenv('AI_KEY_MATCHER_LOG', get_log_path('ai_key_matcher_log.txt')))

logger = setup_logging()


@dataclass
class KeyMatchResult:
    """Result of AI key matching"""
    original_key: str
    canonical_key: str
    confidence: float
    reasoning: str = ""


class AIKeyMatcher:
    """
    Generates canonical keys using AI to handle semantic similarity in key fields.
    
    The AI analyzes records and generates stable, normalized keys that will match
    even when the original key values have slight variations.
    """
    
    def __init__(self, llm_callable=None):
        """
        Initialize the AI Key Matcher.
        
        Args:
            llm_callable: A callable that takes a prompt and returns AI response.
                         If None, will attempt to use the default LLM client.
        """
        self.llm_callable = llm_callable
    
    def generate_canonical_keys(
        self,
        records: List[Dict],
        key_columns: List[str],
        existing_keys: List[str] = None,
        instructions: str = None
    ) -> Dict[str, str]:
        """
        Generate canonical keys for a list of records.
        
        Args:
            records: List of dictionaries containing the data
            key_columns: Column names that form the composite key
            existing_keys: Optional list of existing keys to match against
            instructions: Optional custom instructions for key generation
            
        Returns:
            Dictionary mapping original composite key -> canonical key
        """
        if not records:
            return {}
        
        # Extract the key values from records
        record_keys = []
        for record in records:
            key_parts = []
            for col in key_columns:
                value = record.get(col, '')
                if value is None:
                    value = ''
                key_parts.append(str(value).strip())
            original_key = '|'.join(key_parts)
            record_keys.append({
                'original_key': original_key,
                'parts': {col: record.get(col, '') for col in key_columns}
            })
        
        # Build the prompt for AI
        prompt = self._build_canonicalization_prompt(
            record_keys, 
            key_columns, 
            existing_keys,
            instructions
        )
        
        # Call the AI
        try:
            logger.debug(f"AI Prompt: {prompt}")
            response = self._call_llm(prompt)
            logger.debug(f"AI Response: {response}")
            canonical_map = self._parse_response(response, record_keys)
            logger.debug(f"Parsed Response (cononical map): {canonical_map}")
            return canonical_map
        except Exception as e:
            logger.error(f"AI key matching failed: {e}")
            # Fallback: return original keys normalized
            return {rk['original_key']: self._normalize_key(rk['original_key']) 
                    for rk in record_keys}
    
    def match_incoming_to_existing(
        self,
        incoming_records: List[Dict],
        existing_records: List[Dict],
        key_columns: List[str],
        instructions: str = None
    ) -> Dict[str, str]:
        """
        Match incoming records to existing records using AI.
        
        Returns a mapping of incoming_key -> existing_key (or None if new)
        """
        if not incoming_records or not existing_records:
            return {}
        
        # Helper for case-insensitive column lookup
        def get_column_value(record: Dict, col: str) -> str:
            """Get column value with case-insensitive column name lookup."""
            if col in record:
                return str(record[col]).strip() if record[col] else ''
            # Try case-insensitive match
            record_lower = {k.lower(): v for k, v in record.items()}
            if col.lower() in record_lower:
                val = record_lower[col.lower()]
                return str(val).strip() if val else ''
            return ''
        
        # Extract keys from both sets
        incoming_keys = []
        for record in incoming_records:
            key_parts = [get_column_value(record, col) for col in key_columns]
            incoming_keys.append('|'.join(key_parts))
        
        existing_keys = []
        for record in existing_records:
            key_parts = [get_column_value(record, col) for col in key_columns]
            existing_keys.append('|'.join(key_parts))
        
        # Build matching prompt
        prompt = self._build_matching_prompt(
            incoming_keys,
            existing_keys,
            key_columns,
            instructions
        )
        
        try:
            logger.debug(f"AI Key Matcher prompt:\n{prompt}")
            response = self._call_llm(prompt)
            logger.info(f"AI Key Matcher raw response:\n{response}")
            match_map = self._parse_matching_response(response, incoming_keys, existing_keys)
            logger.info(f"AI Key Matcher parsed matches: {match_map}")
            return match_map
        except Exception as e:
            logger.error(f"AI key matching failed: {e}")
            # Fallback to normalized exact matching
            normalized_existing = {self._normalize_key(k): k for k in existing_keys}
            return {ik: normalized_existing.get(self._normalize_key(ik)) 
                    for ik in incoming_keys}
    
    def _build_canonicalization_prompt(
        self,
        record_keys: List[Dict],
        key_columns: List[str],
        existing_keys: List[str] = None,
        instructions: str = None
    ) -> str:
        """Build the prompt for canonical key generation."""
        
        prompt = """You are a data matching expert. Your task is to generate canonical (normalized, stable) keys for records to enable accurate matching even when values have minor variations.

## Key Columns
The composite key is formed from these columns: {columns}

## Records to Process
{records}

{existing_section}

## Instructions
Generate a canonical key for each record that:
1. Captures the core semantic meaning
2. Is consistent across minor variations (typos, word order, singular/plural, etc.)
3. Uses lowercase, removes extra whitespace
4. Keeps essential identifying information
5. Is deterministic - same concept always produces same canonical key

{custom_instructions}

## Output Format
Return a JSON object mapping each original key to its canonical key:
```json
{{
  "original_key_1": "canonical_key_1",
  "original_key_2": "canonical_key_2"
}}
```

Only return the JSON object, no additional text."""

        # Format records for display
        records_text = "\n".join([
            f"- Original: `{rk['original_key']}`\n  Parts: {json.dumps(rk['parts'])}"
            for rk in record_keys[:50]  # Limit to 50 records
        ])
        
        # Add existing keys section if provided
        existing_section = ""
        if existing_keys:
            existing_section = f"""
## Existing Keys (for reference - try to match to these when semantically equivalent)
{chr(10).join(['- ' + k for k in existing_keys[:100]])}
"""
        
        custom_instructions = ""
        if instructions:
            custom_instructions = f"\nAdditional instructions: {instructions}"
        
        return prompt.format(
            columns=", ".join(key_columns),
            records=records_text,
            existing_section=existing_section,
            custom_instructions=custom_instructions
        )
    
    def _build_matching_prompt(
        self,
        incoming_keys: List[str],
        existing_keys: List[str],
        key_columns: List[str],
        instructions: str = None
    ) -> str:
        """Build prompt for matching incoming to existing keys."""
        
        prompt = """You are a data matching expert. Match incoming records to existing records based on semantic similarity of their keys.

## Key Columns
Keys are composite values from: {columns}

## Existing Records (in the Excel file)
{existing}

## Incoming Records (to be matched)
{incoming}

## Instructions
Match ONLY when the incoming record represents THE SAME ITEM with minor text variations:
- Typos or misspellings
- Word order differences
- Singular vs plural
- Abbreviations vs full words

DO NOT match records that are:
- Different items within the same category
- Related but distinct concepts
- Different specifications, values, or parameters

Ask yourself: "If I kept only one of these records, would I lose information?" 
If YES → they are DIFFERENT records → return null
If NO → they are the SAME record → return the match

When uncertain, return null. A duplicate row is better than overwritten data.

{custom_instructions}

## Output Format
Return a JSON object mapping each incoming key to its matching existing key (or null):
```json
{{
  "incoming_key_1": "matching_existing_key_or_null",
  "incoming_key_2": null
}}
```

Only return the JSON object, no additional text."""

        custom_instructions = ""
        if instructions:
            custom_instructions = f"\nAdditional instructions: {instructions}"
        
        return prompt.format(
            columns=", ".join(key_columns),
            existing="\n".join([f"- `{k}`" for k in existing_keys[:100]]),
            incoming="\n".join([f"- `{k}`" for k in incoming_keys[:50]]),
            custom_instructions=custom_instructions
        )
    
    def _call_llm(self, prompt: str) -> str:
        """Call the LLM with the prompt."""
        if self.llm_callable:
            return self.llm_callable(prompt)
        
        try:
            response = azureMiniQuickPrompt(prompt=prompt, system="You are a data matching assistant. Return only valid JSON.")
            return response
        except ImportError:
            raise RuntimeError("No LLM client available for AI key matching")
    
    def _parse_response(self, response: str, record_keys: List[Dict]) -> Dict[str, str]:
        """Parse the AI response into a key mapping."""
        # Extract JSON from response
        try:
            # Try to find JSON in the response
            json_match = response
            if "```json" in response:
                start = response.find("```json") + 7
                end = response.find("```", start)
                json_match = response[start:end].strip()
            elif "```" in response:
                start = response.find("```") + 3
                end = response.find("```", start)
                json_match = response[start:end].strip()
            
            result = json.loads(json_match)
            
            # Validate result has entries for our keys
            validated = {}
            for rk in record_keys:
                original = rk['original_key']
                if original in result:
                    validated[original] = result[original]
                else:
                    # Fallback to normalized
                    validated[original] = self._normalize_key(original)
            
            return validated
            
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse AI response as JSON: {e}")
            # Fallback to simple normalization
            return {rk['original_key']: self._normalize_key(rk['original_key']) 
                    for rk in record_keys}
    
    def _parse_matching_response(
        self, 
        response: str, 
        incoming_keys: List[str],
        existing_keys: List[str]
    ) -> Dict[str, str]:
        """Parse the matching response."""
        try:
            json_match = response
            if "```json" in response:
                start = response.find("```json") + 7
                end = response.find("```", start)
                json_match = response[start:end].strip()
            elif "```" in response:
                start = response.find("```") + 3
                end = response.find("```", start)
                json_match = response[start:end].strip()
            
            result = json.loads(json_match)
            
            # Validate matches exist in existing keys
            existing_set = set(existing_keys)
            validated = {}
            for ik in incoming_keys:
                match = result.get(ik)
                if match and match in existing_set:
                    validated[ik] = match
                else:
                    validated[ik] = None
            
            return validated
            
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse matching response: {e}")
            return {ik: None for ik in incoming_keys}
    
    def _normalize_key(self, key: str) -> str:
        """Simple normalization fallback when AI is unavailable."""
        if not key:
            return ""
        
        # Lowercase
        normalized = key.lower()
        
        # Remove extra whitespace
        normalized = ' '.join(normalized.split())
        
        # Remove common variations
        replacements = [
            ('materials', 'material'),
            ('specifications', 'specification'),
            ('requirements', 'requirement'),
            (' and ', ' '),
            (' & ', ' '),
            ('  ', ' '),
        ]
        for old, new in replacements:
            normalized = normalized.replace(old, new)
        
        return normalized.strip()


def generate_canonical_key_for_update(
    incoming_data: List[Dict],
    existing_data: List[Dict],
    key_columns: List[str],
    instructions: str = None,
    llm_callable = None
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Convenience function for Excel update operations.
    
    Generates canonical keys for both incoming and existing data,
    enabling matching even when key values have minor variations.
    
    Args:
        incoming_data: New data to be merged
        existing_data: Current data in Excel
        key_columns: Columns forming the composite key
        instructions: Optional AI instructions
        llm_callable: Optional custom LLM callable
        
    Returns:
        Tuple of (incoming_canonical_map, existing_canonical_map)
        Each maps original_key -> canonical_key
    """
    matcher = AIKeyMatcher(llm_callable)
    
    # Combine all records to ensure consistent canonicalization
    all_records = incoming_data + existing_data
    
    canonical_map = matcher.generate_canonical_keys(
        records=all_records,
        key_columns=key_columns,
        instructions=instructions
    )
    
    # Split back into incoming and existing
    incoming_map = {}
    for record in incoming_data:
        key_parts = [str(record.get(col, '')).strip() for col in key_columns]
        original_key = '|'.join(key_parts)
        incoming_map[original_key] = canonical_map.get(original_key, original_key)
    
    existing_map = {}
    for record in existing_data:
        key_parts = [str(record.get(col, '')).strip() for col in key_columns]
        original_key = '|'.join(key_parts)
        existing_map[original_key] = canonical_map.get(original_key, original_key)
    
    return incoming_map, existing_map
