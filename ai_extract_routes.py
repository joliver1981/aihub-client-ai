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

# Workflow skills (2026-09-22): tenant/product SKILL.md files a node may apply
from workflow_skills import list_skills as list_workflow_skill_files, resolve_node_skill
from role_decorators import api_key_or_session_required

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
        # 2026-09-22: optional skill, resolved exactly as the engine does, so
        # "Test extraction" in the designer reflects what the node will apply.
        try:
            skill_text = resolve_node_skill({'skillName': data.get('skill_name', ''),
                                             'skillText': data.get('skill_text', '')})
        except ValueError as e:
            return jsonify({'success': False, 'error': str(e)}), 400
        if skill_text:
            config['skill_instructions'] = skill_text
        
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


@ai_extract_bp.route('/api/workflow/skills', methods=['GET'])
@cross_origin()
@api_key_or_session_required(min_role=2)
def list_workflow_skills():
    """Skills a workflow AI node may pick (2026-09-22): the tenant and product
    SKILL.md files under data/agent/skills — the same files The Agent loads.
    User/group skills are deliberately not offered (a workflow is a shared
    asset; its behaviour must not depend on who runs it).

    Response: {"success": true, "skills": [{scope, name, description, size}, ...]}
    """
    try:
        return jsonify({'success': True, 'skills': list_workflow_skill_files()})
    except Exception as e:
        logger.error(f"list_workflow_skills error: {e}")
        return jsonify({'success': False, 'error': str(e), 'skills': []}), 500


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
