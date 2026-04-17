# smart_change_detector.py
"""
Smart Change Detector - Evaluates whether changes between old and new values 
are semantically meaningful.

Used to filter out "noise" updates where wording changed but meaning didn't,
reducing unnecessary updates when re-processing documents.
"""

import json
import logging
from logging.handlers import WatchedFileHandler
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass
import os
from CommonUtils import rotate_logs_on_startup, get_log_path
from AppUtils import azureMiniQuickPrompt


# Configure logging
def setup_logging():
    """Configure logging for the workflow execution"""
    logger = logging.getLogger("SmartChangeDetector")
    log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
    log_level = getattr(logging, log_level_name, logging.DEBUG)
    logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler = WatchedFileHandler(filename=os.getenv('AI_CHG_MATCHER_LOG', get_log_path('ai_chg_matcher_log.txt')), encoding='utf-8')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger

rotate_logs_on_startup(os.getenv('AI_CHG_MATCHER_LOG', get_log_path('ai_chg_matcher_log.txt')))

logger = setup_logging()

# Try to import the config for batch size, with fallback default
try:
    from config import SMART_CHANGE_BATCH_SIZE
except ImportError:
    SMART_CHANGE_BATCH_SIZE = 25

try:
    from config import SMART_CHANGE_INCLUDE_CONTEXT
except ImportError:
    SMART_CHANGE_INCLUDE_CONTEXT = False  # Default to OFF - simpler comparisons


@dataclass
class ChangeCandidate:
    """Represents a potential change to be evaluated."""
    row_key: str
    row_context: Dict[str, Any]  # e.g., customer, topic, etc.
    field: str
    old_value: Any
    new_value: Any
    
    def to_comparison_dict(self) -> Dict:
        """Convert to dict for AI prompt."""
        return {
            'row_key': self.row_key,
            'context': self.row_context,
            'field': self.field,
            'old': str(self.old_value) if self.old_value else '',
            'new': str(self.new_value) if self.new_value else ''
        }


@dataclass
class ChangeEvaluation:
    """Result of evaluating a change candidate."""
    row_key: str
    should_update: bool
    reason: Optional[str] = None


class SmartChangeDetector:
    """
    Evaluates whether changes between old and new values are semantically meaningful.
    
    Supports two modes:
    - Strict: Preserves nuance (must vs should, all vs most) - for compliance/legal
    - Lenient: Focuses on facts only (numbers, dates, key requirements) - for general docs
    """
    
    def __init__(
        self, 
        llm_callable: Callable[[str], str] = None, 
        batch_size: int = None,
        include_context: bool = None
    ):
        """
        Initialize the detector.
        
        Args:
            llm_callable: Function that takes a prompt and returns LLM response
            batch_size: Number of changes to evaluate per AI call (default from config)
            include_context: Whether to include row context in prompts (default from config)
        """
        self.llm_callable = llm_callable
        self.batch_size = batch_size or SMART_CHANGE_BATCH_SIZE
        self.include_context = include_context if include_context is not None else SMART_CHANGE_INCLUDE_CONTEXT
    
    def evaluate_changes(
        self,
        changes: List[ChangeCandidate],
        strictness: str = 'strict',
        instructions: str = None
    ) -> Dict[str, ChangeEvaluation]:
        """
        Evaluate a list of potential changes to determine which are semantically meaningful.
        
        Args:
            changes: List of ChangeCandidate objects to evaluate
            strictness: 'strict' or 'lenient' mode
            instructions: Optional additional instructions for the AI
            
        Returns:
            Dict mapping row_key -> ChangeEvaluation with should_update decision
        """
        if not changes:
            return {}
        
        logger.info(f"Evaluating {len(changes)} potential changes with {strictness} mode")
        
        all_evaluations = {}
        
        # Process in batches
        for i in range(0, len(changes), self.batch_size):
            batch = changes[i:i + self.batch_size]
            batch_num = (i // self.batch_size) + 1
            total_batches = (len(changes) + self.batch_size - 1) // self.batch_size
            
            logger.debug(f"Processing batch {batch_num}/{total_batches} ({len(batch)} changes)")
            
            try:
                batch_results = self._evaluate_batch(batch, strictness, instructions)
                all_evaluations.update(batch_results)
            except Exception as e:
                logger.error(f"Error evaluating batch {batch_num}: {e}")
                # On error, default to updating (safe fallback)
                for change in batch:
                    all_evaluations[change.row_key] = ChangeEvaluation(
                        row_key=change.row_key,
                        should_update=True,
                        reason="Evaluation failed - defaulting to update"
                    )
        
        # Log summary
        updates_needed = sum(1 for e in all_evaluations.values() if e.should_update)
        skipped = len(all_evaluations) - updates_needed
        logger.info(f"Smart change detection complete: {updates_needed} material changes, {skipped} semantically equivalent (skipped)")
        
        return all_evaluations
    
    def _evaluate_batch(
        self,
        changes: List[ChangeCandidate],
        strictness: str,
        instructions: str = None
    ) -> Dict[str, ChangeEvaluation]:
        """Evaluate a batch of changes with a single AI call."""
        
        prompt = self._build_evaluation_prompt(changes, strictness, instructions)
        
        logger.debug(f"Smart Change Detector prompt:\n{prompt}")
        
        response = self._call_llm(prompt)
        
        logger.info(f"Smart Change Detector raw response:\n{response}")
        
        return self._parse_evaluation_response(response, changes)
    
    def _build_evaluation_prompt(
        self,
        changes: List[ChangeCandidate],
        strictness: str,
        instructions: str = None
    ) -> str:
        """Build the prompt for evaluating changes."""
        
        # Build the changes list for the prompt
        changes_text = []
        for idx, change in enumerate(changes, 1):
            # Conditionally include row context based on setting
            if self.include_context and change.row_context:
                ctx = change.row_context
                ctx_str = " | ".join(f"{k}: {v}" for k, v in ctx.items() if v)
                context_line = f"\n  Context: {ctx_str}"
            else:
                context_line = ""
            
            changes_text.append(f"""Change {idx}:
  Row Key: {change.row_key}{context_line}
  Field: {change.field}
  OLD: "{change.old_value or ''}"
  NEW: "{change.new_value or ''}"
""")
        
        changes_block = "\n".join(changes_text)
        
        # Get strictness-specific instructions
        strictness_instructions = self._get_strictness_instructions(strictness)
        
        # Custom instructions
        custom_instructions = ""
        if instructions:
            custom_instructions = f"\n## Additional Instructions\n{instructions}\n"
        
        prompt = f"""You are a semantic change evaluator. Determine whether each change represents a MATERIAL (meaningful) change or is SEMANTICALLY EQUIVALENT (same meaning, different wording).

## Evaluation Mode: {strictness.upper()}

{strictness_instructions}

## Changes to Evaluate

{changes_block}

{custom_instructions}

## Output Format
Return a JSON object with the evaluation for each change number:
```json
{{
  "1": {{"update": true, "reason": "Brief reason"}},
  "2": {{"update": false, "reason": "Brief reason"}},
  ...
}}
```

Where:
- "update": true = MATERIAL change, should update the row
- "update": false = SEMANTICALLY EQUIVALENT, skip the update

Only return the JSON object, no additional text."""

        return prompt
    
    def _get_strictness_instructions(self, strictness: str) -> str:
        """Get mode-specific instructions."""
        
        if strictness == 'strict':
            return """## STRICT Mode Guidelines
In STRICT mode, preserve nuance and treat subtle differences as meaningful.

MARK AS MATERIAL CHANGE (update: true):
- Obligation level changes: "must" vs "should", "shall" vs "may", "required" vs "recommended"
- Scope changes: "all" vs "most" vs "some", "always" vs "usually"
- Certainty changes: "will" vs "may", "guaranteed" vs "expected"
- Any numbers, dates, amounts, or thresholds that differ
- Added or removed conditions, exceptions, or qualifiers
- Changes in who is responsible or affected

MARK AS SEMANTICALLY EQUIVALENT (update: false):
- Pure synonym substitutions with identical meaning: "utilize" vs "use"
- Word order changes with same meaning
- Passive vs active voice: "must be submitted" vs "you must submit"
- Punctuation or formatting differences
- Filler words added/removed: "please note that" vs direct statement
- "shall" vs "will" (legal equivalents in contracts)
- "no less than X" vs "minimum of X" vs "at least X"

When in doubt in STRICT mode, mark as MATERIAL CHANGE."""

        else:  # lenient
            return """## LENIENT Mode Guidelines
In LENIENT mode, focus only on factual/substantive differences. Ignore tone and phrasing.

MARK AS MATERIAL CHANGE (update: true):
- Any numbers, dates, amounts, percentages, or thresholds that differ
- Quantitative scope changes: "5 items" vs "3 items"
- Added or removed specific requirements, conditions, or entities
- Different deadlines, time periods, or frequencies
- Changes in specific names, products, or references

MARK AS SEMANTICALLY EQUIVALENT (update: false):
- Obligation level differences: "must" vs "should" vs "shall" (ignore these)
- Qualitative scope differences: "all" vs "most" (unless quantified)
- Rephrasing that conveys the same requirement
- Word order, sentence structure changes
- Passive vs active voice
- Adding/removing introductory phrases
- Synonym substitutions
- Formatting, punctuation, capitalization

When in doubt in LENIENT mode, mark as SEMANTICALLY EQUIVALENT."""
    
    def _call_llm(self, prompt: str) -> str:
        """Call the LLM with the prompt."""
        if self.llm_callable:
            return self.llm_callable(prompt)
        
        # Try to import and use the default LLM function
        try:
            response = azureMiniQuickPrompt(
                prompt=prompt, 
                system="You are a semantic change evaluator. Return only valid JSON."
            )
            return response
        except ImportError:
            raise RuntimeError("No LLM client available for smart change detection")
    
    def _parse_evaluation_response(
        self,
        response: str,
        changes: List[ChangeCandidate]
    ) -> Dict[str, ChangeEvaluation]:
        """Parse the AI response into evaluation results."""
        
        try:
            # Extract JSON from response
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
            
            evaluations = {}
            for idx, change in enumerate(changes, 1):
                idx_str = str(idx)
                if idx_str in result:
                    eval_data = result[idx_str]
                    should_update = eval_data.get('update', True)
                    reason = eval_data.get('reason', '')
                    
                    evaluations[change.row_key] = ChangeEvaluation(
                        row_key=change.row_key,
                        should_update=should_update,
                        reason=reason
                    )
                else:
                    # Not in response - default to update
                    evaluations[change.row_key] = ChangeEvaluation(
                        row_key=change.row_key,
                        should_update=True,
                        reason="Not evaluated - defaulting to update"
                    )
            
            return evaluations
            
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse evaluation response as JSON: {e}")
            # On parse failure, default to updating all
            return {
                change.row_key: ChangeEvaluation(
                    row_key=change.row_key,
                    should_update=True,
                    reason="Parse failed - defaulting to update"
                )
                for change in changes
            }


# Default columns to exclude from smart change detection
# These are system-managed metadata columns, not document content
DEFAULT_EXCLUDED_COLUMNS = [
    'Last Updated',
    'last_updated',
    'LastUpdated',
    'last updated',
    'timestamp',
    'Timestamp',
    'created_at',
    'updated_at',
    'modified_at',
    'Created At',
    'Updated At',
    'Modified At',
    '_row_id',
    '_id',
]


def build_change_candidates(
    input_data: List[Dict],
    existing_rows: Dict[str, Dict],
    key_columns: List[str],
    value_columns: List[str] = None,
    excluded_columns: List[str] = None
) -> List[ChangeCandidate]:
    """
    Build a list of ChangeCandidate objects by comparing input data to existing rows.
    
    Args:
        input_data: Incoming data rows
        existing_rows: Dict of existing rows keyed by composite key (lowercase)
        key_columns: Columns that form the composite key
        value_columns: Columns to compare for changes (None = all non-key, non-excluded columns)
        excluded_columns: Additional columns to exclude from comparison (added to defaults)
        
    Returns:
        List of ChangeCandidate objects for rows that have differences
    """
    candidates = []
    
    # Build the full exclusion list
    exclude_set = set(col.lower() for col in DEFAULT_EXCLUDED_COLUMNS)
    if excluded_columns:
        exclude_set.update(col.lower() for col in excluded_columns)
    
    # Also exclude key columns from value comparison
    key_columns_lower = set(col.lower() for col in key_columns)
    
    for row in input_data:
        # Build the composite key (lowercase for lookup)
        key_parts = [str(row.get(col, '')).strip() for col in key_columns]
        row_key = '|'.join(key_parts)
        lookup_key = row_key.lower()
        
        # Check if this row exists
        if lookup_key not in existing_rows:
            continue  # New row, not a candidate for change detection
        
        existing_row = existing_rows[lookup_key]
        
        # Build row context from key columns
        row_context = {col: row.get(col, '') for col in key_columns}
        
        # Determine which columns to compare
        if value_columns:
            cols_to_check = value_columns
        else:
            # All columns except key columns and excluded columns
            all_cols = set(row.keys()) | set(existing_row.keys())
            cols_to_check = [
                c for c in all_cols 
                if c.lower() not in key_columns_lower 
                and c.lower() not in exclude_set
            ]
        
        # Check each column for differences
        for col in cols_to_check:
            old_val = existing_row.get(col)
            new_val = row.get(col)
            
            # Normalize for comparison
            old_str = str(old_val).strip() if old_val is not None else ''
            new_str = str(new_val).strip() if new_val is not None else ''
            
            # Skip if identical
            if old_str == new_str:
                continue
            
            # Skip if both empty/None
            if not old_str and not new_str:
                continue
            
            # Found a difference
            candidates.append(ChangeCandidate(
                row_key=row_key,
                row_context=row_context,
                field=col,
                old_value=old_val,
                new_value=new_val
            ))
    
    return candidates


def filter_updates_by_evaluation(
    input_data: List[Dict],
    evaluations: Dict[str, ChangeEvaluation],
    key_columns: List[str]
) -> tuple[List[Dict], int]:
    """
    Filter input data based on change evaluations.
    
    Args:
        input_data: Original input data rows
        evaluations: Dict of row_key -> ChangeEvaluation
        key_columns: Columns that form the composite key
        
    Returns:
        Tuple of (filtered_data, skipped_count)
    """
    filtered = []
    skipped = 0
    
    for row in input_data:
        # Build the row key
        key_parts = [str(row.get(col, '')).strip() for col in key_columns]
        row_key = '|'.join(key_parts)
        
        # Check if we have an evaluation for this row
        evaluation = evaluations.get(row_key)
        
        if evaluation and not evaluation.should_update:
            # Skip this row - semantically equivalent
            skipped += 1
            continue
        
        # Include this row
        filtered.append(row)
    
    return filtered, skipped
