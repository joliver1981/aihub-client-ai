# ai_extract_executor.py
# AI Extract Node Executor - Handles structured AI-based field extraction

import json
import re
import logging
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger("AIExtract")


class AIExtractExecutor:
    """
    Executes AI Extract nodes for structured data extraction from text content.
    
    Supports:
    - Field Extraction: Extract specific named fields with types
    - Nested groups: Objects within objects
    - Repeated groups: Arrays of objects (e.g., line items)
    - Type validation: text, number, boolean, list
    - Required field validation
    """
    
    # Supported field types
    FIELD_TYPES = {
        'text': 'string or null',
        'number': 'number or null',
        'boolean': 'true/false or null',
        'list': 'array of values',
        'group': 'object',
        'repeated_group': 'array of objects'
    }
    
    def __init__(self, ai_call_function):
        """
        Initialize the executor with an AI calling function.
        
        Args:
            ai_call_function: Function that takes (prompt, system_message) and returns AI response string
        """
        self.ai_call = ai_call_function
    
    def execute(self, config: Dict, input_content: str) -> Dict:
        """
        Execute an AI extraction based on configuration.
        
        Args:
            config: Node configuration containing:
                - extraction_type: Type of extraction (field_extraction, etc.)
                - fields: List of field definitions
                - special_instructions: Optional additional instructions
                - fail_on_missing_required: Whether to fail if required fields missing
                - output_variable: Name of output variable
            input_content: The text content to extract from
            
        Returns:
            Dict with:
                - success: bool
                - data: Extracted data (if successful)
                - validation: Validation results
                - error: Error message (if failed)
        """
        extraction_type = config.get('extraction_type', 'field_extraction')
        
        if extraction_type == 'field_extraction':
            return self._execute_field_extraction(config, input_content)
        else:
            return {
                'success': False,
                'error': f"Unsupported extraction type: {extraction_type}",
                'data': None
            }
    
    def _execute_field_extraction(self, config: Dict, input_content: str) -> Dict:
        """Execute field extraction."""
        fields = config.get('fields', [])
        special_instructions = config.get('special_instructions', '')
        fail_on_missing = config.get('fail_on_missing_required', False)
        formatting_instructions = config.get('formatting_instructions', '')  # NEW
        
        if not fields:
            return {
                'success': False,
                'error': 'No fields defined for extraction',
                'data': None
            }
        
        # Build the extraction prompt
        prompt = self._build_field_extraction_prompt(
            fields, special_instructions, input_content, formatting_instructions)  # MODIFIED
        system_message = self._get_system_message(formatting_instructions)  # MODIFIED
        
        try:
            # Call the AI
            ai_response = self.ai_call(prompt, system_message)
            
            # Parse the JSON response
            parsed_response = self._parse_json_response(ai_response)
            
            if parsed_response is None:
                return {
                    'success': False,
                    'error': 'Failed to parse AI response as JSON',
                    'raw_response': ai_response,
                    'data': None
                }
            
            # NEW: Separate cell_formatting from extracted data
            cell_formatting = None
            if 'cell_formatting' in parsed_response:
                cell_formatting = parsed_response.pop('cell_formatting')
                logger.debug(f"AI returned cell_formatting for {len(cell_formatting)} field(s)")
            
            extracted_data = parsed_response
            
            # Validate the extraction
            validation = self._validate_extraction(extracted_data, fields)
            
            # Check if we should fail on missing required fields
            if fail_on_missing and not validation['all_required_found']:
                return {
                    'success': False,
                    'error': f"Required fields not found: {', '.join(validation['missing_required'])}",
                    'data': extracted_data,
                    'cell_formatting': cell_formatting,  # NEW: Include even on failure
                    'validation': validation
                }
            
            result = {
                'success': True,
                'data': extracted_data,
                'validation': validation
            }
            
            # NEW: Include cell_formatting if present
            if cell_formatting:
                result['cell_formatting'] = cell_formatting
            
            return result
            
        except Exception as e:
            logger.error(f"AI Extract execution error: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'data': None
            }


    def _get_system_message(self, formatting_instructions: str = None) -> str:
        """Get the system message for extraction."""
        base_message = """You are a precise data extraction assistant. Your task is to extract specific fields from provided content and return valid JSON.

    CRITICAL RULES:
    1. Return ONLY valid JSON - no markdown, no explanations, no code blocks
    2. Use exactly the field names specified (they are case-sensitive)
    3. For fields that cannot be found or determined, use null
    4. For number fields, return numeric values only (no currency symbols, commas, or text)
    5. For repeated_group fields with no items found, return an empty array []
    6. Never invent or assume data - only extract what is explicitly present"""

        if formatting_instructions and formatting_instructions.strip():
            base_message += """
    7. When formatting is requested, include a "cell_formatting" object in your response
    8. Only include fields in cell_formatting that match the formatting criteria"""

        return base_message
    
    def _build_field_extraction_prompt(self, fields: List[Dict], special_instructions: str, 
                                   input_content: str, formatting_instructions: str = None) -> str:
        """Build the extraction prompt from field definitions."""
        
        # Build field descriptions
        fields_description = self._build_fields_description(fields)
        
        # Build expected JSON schema
        json_schema = self._build_json_schema(fields)
        
        prompt = f"""Extract the following fields from the provided content.

    FIELDS TO EXTRACT:
    {fields_description}

    EXPECTED OUTPUT STRUCTURE:
    {json.dumps(json_schema, indent=2)}

    """
        
        if special_instructions:
            prompt += f"""SPECIAL INSTRUCTIONS:
    {special_instructions}

    """

        # NEW: Add formatting instructions if provided
        if formatting_instructions and formatting_instructions.strip():
            prompt += f"""CELL FORMATTING REQUEST:
    Based on the extracted data, suggest cell formatting for Excel output.

    User's formatting instructions: "{formatting_instructions}"

    For each field that should be formatted based on these instructions, add an entry to "cell_formatting".

    Formatting properties you can use (all optional):
    - "fill": Background color as hex (e.g., "#FFCDD2" for light red, "#C8E6C9" for light green, "#FFF59D" for yellow)
    - "font_color": Text color as hex (e.g., "#B71C1C" for dark red)
    - "bold": true/false
    - "reason": Brief explanation of why this cell should be formatted

    Common colors:
    - Yellow: "#FFF59D" (light) or "#FFEB3B" (bright)
    - Red: "#FFCDD2" (light) or "#F44336" (bright)
    - Green: "#C8E6C9" (light) or "#4CAF50" (bright)
    - Blue: "#BBDEFB" (light) or "#2196F3" (bright)
    - Orange: "#FFE0B2" (light) or "#FF9800" (bright)

    Include "cell_formatting" as a top-level key in your JSON response, alongside the extracted fields.

    Example response with formatting:
    {{
    "customer_name": "Acme Corp",
    "amount": 15000,
    "status": "pending",
    "cell_formatting": {{
        "amount": {{"fill": "#FFF59D", "reason": "Highlighted as requested"}},
        "status": {{"fill": "#FFCDD2", "bold": true, "reason": "Pending status flagged"}}
    }}
    }}

    If no fields need formatting, include an empty cell_formatting object: "cell_formatting": {{}}

    """
        
        prompt += f"""CONTENT TO EXTRACT FROM:
    ---
    {input_content}
    ---

    Return ONLY the JSON object with extracted values"""
        
        if formatting_instructions and formatting_instructions.strip():
            prompt += " and cell_formatting"
        
        prompt += ". No other text."
        
        return prompt
    
    def _build_fields_description(self, fields: List[Dict], indent: int = 0) -> str:
        """Build human-readable field descriptions."""
        lines = []
        prefix = "  " * indent
        
        for field in fields:
            name = field.get('name', 'unnamed')
            field_type = field.get('type', 'text')
            required = field.get('required', False)
            description = field.get('description', '')
            
            req_text = "REQUIRED" if required else "optional"
            
            if field_type in ('group', 'repeated_group'):
                type_label = "object" if field_type == 'group' else "array of objects"
                lines.append(f"{prefix}- {name} ({type_label}, {req_text})")
                if description:
                    lines.append(f"{prefix}  Description: {description}")
                
                children = field.get('children', [])
                if children:
                    lines.append(f"{prefix}  Contains:")
                    lines.append(self._build_fields_description(children, indent + 2))
            else:
                type_hint = self.FIELD_TYPES.get(field_type, 'text')
                lines.append(f"{prefix}- {name} ({field_type}, {req_text})")
                if description:
                    lines.append(f"{prefix}  Description: {description}")
        
        return "\n".join(lines)
    
    def _build_json_schema(self, fields: List[Dict]) -> Dict:
        """Build example JSON schema showing expected structure."""
        schema = {}
        
        for field in fields:
            name = field.get('name', 'unnamed')
            field_type = field.get('type', 'text')
            
            if field_type == 'text':
                schema[name] = "string or null"
            elif field_type == 'number':
                schema[name] = "number or null"
            elif field_type == 'boolean':
                schema[name] = "true/false or null"
            elif field_type == 'list':
                schema[name] = ["value1", "value2", "..."]
            elif field_type == 'group':
                children = field.get('children', [])
                schema[name] = self._build_json_schema(children) if children else {}
            elif field_type == 'repeated_group':
                children = field.get('children', [])
                child_schema = self._build_json_schema(children) if children else {}
                schema[name] = [child_schema]
        
        return schema
    
    def _build_output_preview(self, fields: List[Dict]) -> Dict:
        """Build a preview of the output structure with placeholder values."""
        preview = {}
        
        for field in fields:
            name = field.get('name', 'unnamed')
            field_type = field.get('type', 'text')
            
            if field_type == 'text':
                preview[name] = "text"
            elif field_type == 'number':
                preview[name] = 0
            elif field_type == 'boolean':
                preview[name] = False
            elif field_type == 'list':
                preview[name] = []
            elif field_type == 'group':
                children = field.get('children', [])
                preview[name] = self._build_output_preview(children) if children else {}
            elif field_type == 'repeated_group':
                children = field.get('children', [])
                child_preview = self._build_output_preview(children) if children else {}
                preview[name] = [child_preview]
        
        return preview
    
    def _parse_json_response(self, response: str) -> Optional[Dict]:
        """Parse JSON from AI response, handling various formats."""
        if not response:
            return None
        
        # Clean up the response
        cleaned = response.strip()
        
        # Remove markdown code blocks if present
        cleaned = re.sub(r'^```json\s*', '', cleaned)
        cleaned = re.sub(r'^```\s*', '', cleaned)
        cleaned = re.sub(r'\s*```$', '', cleaned)
        cleaned = cleaned.strip()
        
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # Try to find JSON object in the response
            json_match = re.search(r'\{[\s\S]*\}', cleaned)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass
            
            logger.warning(f"Failed to parse JSON response: {cleaned[:200]}...")
            return None
    
    def _validate_extraction(self, data: Dict, fields: List[Dict], path: str = "") -> Dict:
        """
        Validate extracted data against field definitions.
        
        Returns:
            Dict with:
                - all_required_found: bool
                - missing_required: list of field paths
                - type_errors: list of type mismatch descriptions
        """
        missing_required = []
        type_errors = []
        
        def check_fields(data_obj: Any, field_defs: List[Dict], current_path: str):
            for field in field_defs:
                name = field.get('name', '')
                field_type = field.get('type', 'text')
                required = field.get('required', False)
                
                field_path = f"{current_path}.{name}" if current_path else name
                
                # Get the value
                value = data_obj.get(name) if isinstance(data_obj, dict) else None
                
                # Check required
                if required and (value is None or value == "" or value == []):
                    missing_required.append(field_path)
                
                # Check type if value exists
                if value is not None:
                    type_error = self._check_type(value, field_type, field_path)
                    if type_error:
                        type_errors.append(type_error)
                
                # Recurse into nested structures
                if field_type == 'group' and field.get('children'):
                    if isinstance(value, dict):
                        check_fields(value, field['children'], field_path)
                    elif required:
                        missing_required.append(field_path)
                        
                elif field_type == 'repeated_group' and field.get('children'):
                    if isinstance(value, list):
                        for i, item in enumerate(value):
                            check_fields(item, field['children'], f"{field_path}[{i}]")
        
        if data:
            check_fields(data, fields, path)
        
        return {
            'all_required_found': len(missing_required) == 0,
            'missing_required': missing_required,
            'type_errors': type_errors
        }
    
    def _check_type(self, value: Any, expected_type: str, field_path: str) -> Optional[str]:
        """Check if a value matches the expected type."""
        if value is None:
            return None  # None is acceptable for any type
        
        if expected_type == 'text':
            if not isinstance(value, str):
                return f"{field_path}: expected text, got {type(value).__name__}"
        elif expected_type == 'number':
            if not isinstance(value, (int, float)):
                return f"{field_path}: expected number, got {type(value).__name__}"
        elif expected_type == 'boolean':
            if not isinstance(value, bool):
                return f"{field_path}: expected boolean, got {type(value).__name__}"
        elif expected_type == 'list':
            if not isinstance(value, list):
                return f"{field_path}: expected list, got {type(value).__name__}"
        elif expected_type == 'group':
            if not isinstance(value, dict):
                return f"{field_path}: expected object, got {type(value).__name__}"
        elif expected_type == 'repeated_group':
            if not isinstance(value, list):
                return f"{field_path}: expected array, got {type(value).__name__}"
        
        return None


# Validation helper for field names
def validate_field_name(name: str) -> Tuple[bool, str]:
    """
    Validate that a field name is valid for dot notation access.
    
    Valid names:
    - Start with letter or underscore
    - Contain only letters, numbers, underscores
    - No spaces, hyphens, dots, or special characters
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not name:
        return False, "Field name cannot be empty"
    
    pattern = r'^[a-zA-Z_][a-zA-Z0-9_]*$'
    if not re.match(pattern, name):
        return False, f"Invalid field name '{name}'. Use only letters, numbers, and underscores. Must start with a letter or underscore."
    
    # Check for reserved words
    reserved = ['null', 'true', 'false', 'undefined', 'NaN', 'Infinity']
    if name.lower() in [r.lower() for r in reserved]:
        return False, f"'{name}' is a reserved word and cannot be used as a field name"
    
    return True, ""


def normalize_field_name(name: str) -> str:
    """
    Convert a string to a valid field name.
    
    Examples:
    - "Invoice Number" -> "invoice_number"
    - "Total-Amount" -> "total_amount"
    - "123field" -> "_123field"
    """
    if not name:
        return "unnamed_field"
    
    # Replace spaces and hyphens with underscores
    normalized = re.sub(r'[\s\-]+', '_', name)
    
    # Remove any other invalid characters
    normalized = re.sub(r'[^a-zA-Z0-9_]', '', normalized)
    
    # Ensure it starts with a letter or underscore
    if normalized and normalized[0].isdigit():
        normalized = '_' + normalized
    
    # Convert to lowercase (snake_case convention)
    normalized = normalized.lower()
    
    return normalized if normalized else "unnamed_field"


def build_output_preview_json(fields: List[Dict]) -> str:
    """
    Build a JSON string preview of the expected output structure.
    
    Args:
        fields: List of field definitions
        
    Returns:
        JSON string with placeholder values
    """
    def build_preview(field_list: List[Dict]) -> Dict:
        result = {}
        for field in field_list:
            name = field.get('name', 'unnamed')
            field_type = field.get('type', 'text')
            
            if field_type == 'text':
                result[name] = "text"
            elif field_type == 'number':
                result[name] = 0
            elif field_type == 'boolean':
                result[name] = False
            elif field_type == 'list':
                result[name] = []
            elif field_type == 'group':
                children = field.get('children', [])
                result[name] = build_preview(children) if children else {}
            elif field_type == 'repeated_group':
                children = field.get('children', [])
                child_preview = build_preview(children) if children else {}
                result[name] = [child_preview]
        
        return result
    
    preview = build_preview(fields)
    return json.dumps(preview, indent=2)
