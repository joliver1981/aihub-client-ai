# ai_extract_routes.py
# API routes for AI Extract node functionality

import json
import logging
from flask import Blueprint, request, jsonify
from flask_cors import cross_origin

# Import the executor
from ai_extract_executor import (
    AIExtractExecutor, 
    validate_field_name, 
    normalize_field_name,
    build_output_preview_json
)

# Import the AI utility function
from AppUtils import azureQuickPrompt

logger = logging.getLogger("AIExtractRoutes")

# Create blueprint
ai_extract_bp = Blueprint('ai_extract', __name__)


def ai_call_wrapper(prompt: str, system_message: str) -> str:
    """
    Wrapper function to call the AI using existing infrastructure.
    
    Args:
        prompt: The user prompt
        system_message: The system message
        
    Returns:
        AI response string
    """
    return azureQuickPrompt(prompt, system=system_message, temp=0.0)


@ai_extract_bp.route('/api/workflow/ai-extract/test', methods=['POST'])
@cross_origin()
def test_extraction():
    """
    Test an AI extraction configuration against sample content.
    
    Request body:
    {
        "extraction_type": "field_extraction",
        "fields": [...],
        "special_instructions": "...",
        "test_content": "..."
    }
    
    Response:
    {
        "success": true/false,
        "result": {...},  // Extracted data
        "validation": {
            "all_required_found": true/false,
            "missing_required": [...],
            "type_errors": [...]
        },
        "error": "..." // If failed
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': 'No request data provided'
            }), 400
        
        # Get configuration
        extraction_type = data.get('extraction_type', 'field_extraction')
        fields = data.get('fields', [])
        special_instructions = data.get('special_instructions', '')
        test_content = data.get('test_content', '')
        fail_on_missing = data.get('fail_on_missing_required', False)
        
        if not fields:
            return jsonify({
                'success': False,
                'error': 'No fields defined for extraction'
            }), 400
        
        if not test_content:
            return jsonify({
                'success': False,
                'error': 'No test content provided'
            }), 400
        
        # Validate field names
        for field in fields:
            is_valid, error_msg = validate_field_name(field.get('name', ''))
            if not is_valid:
                return jsonify({
                    'success': False,
                    'error': f"Invalid field configuration: {error_msg}"
                }), 400
            
            # Validate children if present
            for child in field.get('children', []):
                is_valid, error_msg = validate_field_name(child.get('name', ''))
                if not is_valid:
                    return jsonify({
                        'success': False,
                        'error': f"Invalid child field configuration: {error_msg}"
                    }), 400
        
        # Create config
        config = {
            'extraction_type': extraction_type,
            'fields': fields,
            'special_instructions': special_instructions,
            'fail_on_missing_required': fail_on_missing
        }
        
        # Execute extraction
        executor = AIExtractExecutor(ai_call_wrapper)
        result = executor.execute(config, test_content)
        
        return jsonify({
            'success': result.get('success', False),
            'result': result.get('data'),
            'validation': result.get('validation', {}),
            'error': result.get('error'),
            'raw_response': result.get('raw_response')  # For debugging
        })
        
    except Exception as e:
        logger.error(f"Test extraction error: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@ai_extract_bp.route('/api/workflow/ai-extract/validate-field-name', methods=['POST'])
@cross_origin()
def validate_field_name_api():
    """
    Validate a field name for use in extraction.
    
    Request body:
    {
        "name": "field_name"
    }
    
    Response:
    {
        "valid": true/false,
        "error": "...",  // If invalid
        "normalized": "..."  // Suggested normalized name
    }
    """
    try:
        data = request.get_json()
        name = data.get('name', '')
        
        is_valid, error_msg = validate_field_name(name)
        normalized = normalize_field_name(name)
        
        return jsonify({
            'valid': is_valid,
            'error': error_msg if not is_valid else None,
            'normalized': normalized
        })
        
    except Exception as e:
        return jsonify({
            'valid': False,
            'error': str(e)
        }), 500


@ai_extract_bp.route('/api/workflow/ai-extract/preview-schema', methods=['POST'])
@cross_origin()
def preview_schema():
    """
    Generate a preview of the expected output JSON structure.
    
    Request body:
    {
        "fields": [...]
    }
    
    Response:
    {
        "success": true/false,
        "preview": {...}
    }
    """
    try:
        data = request.get_json()
        fields = data.get('fields', [])
        
        if not fields:
            return jsonify({
                'success': False,
                'error': 'No fields provided'
            }), 400
        
        preview_json = build_output_preview_json(fields)
        
        return jsonify({
            'success': True,
            'preview': json.loads(preview_json)
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# Function to register blueprint with app
def register_ai_extract_routes(app):
    """Register AI Extract routes with the Flask app."""
    app.register_blueprint(ai_extract_bp)
    logger.info("AI Extract routes registered")
