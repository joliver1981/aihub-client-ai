# nlq_enhancements.py - Integration module for all enhancements

import logging
import json
from flask import Flask, request, jsonify, render_template
from role_decorators import developer_required
from error_handling_system import ErrorHandler
from feedback_system import FeedbackManager, setup_feedback_routes
from caution_system import CautionManager, setup_caution_routes


def initialize_enhancements(app, logger=None):
    """
    Initialize all NLQ enhancement systems and setup routes.
    
    Args:
        app (Flask): The Flask application
        logger (Logger, optional): Logger object for recording events
        
    Returns:
        dict: Dictionary containing all initialized systems
    """
    if logger is None:
        logger = logging.getLogger(__name__)
    
    # Initialize error handler
    error_handler = ErrorHandler(logger)
    
    # Initialize feedback manager
    feedback_manager = FeedbackManager(logger)
    #feedback_manager.create_feedback_tables()
    
    # Initialize caution manager
    caution_manager = CautionManager(logger)
    
    # Setup routes
    setup_feedback_routes(app, feedback_manager)
    setup_caution_routes(app, caution_manager)
    
    # Add admin route for caution settings
    @app.route('/admin/caution-settings', methods=['GET'])
    def caution_settings():
        return render_template('caution_settings.html')
    
    # Add admin route for feedback analysis
    @app.route('/admin/feedback-analysis', methods=['GET'])
    @developer_required()
    def feedback_analysis():
        return render_template('feedback_analysis.html')
    
    # Return all systems for use in other parts of the application
    return {
        'error_handler': error_handler,
        'feedback_manager': feedback_manager,
        'caution_manager': caution_manager
    }

# Integration with data_assistants.html handler
def enhance_chat_data_route(app, systems):
    """
    Enhance the /chat/data route with error handling, feedback, and caution features.
    
    Args:
        app (Flask): The Flask application
        systems (dict): Dictionary of enhancement systems
    """
    error_handler = systems['error_handler']
    caution_manager = systems['caution_manager']
    
    # Store the original route function
    original_chat_data = app.view_functions.get('chat_data')
    
    if not original_chat_data:
        logging.error("Could not find the 'chat_data' route function")
        return
    
    # Define the enhanced route function
    def enhanced_chat_data():
        try:
            # Get request data
            data = request.get_json()
            agent_id = data.get('agent_id')
            question = data.get('question')
            history = data.get('history', [])
            
            # Get user ID
            user_id = None
            if hasattr(request, 'user') and hasattr(request.user, 'id'):
                user_id = request.user.id
                
            # Get caution level for this user
            caution_level = caution_manager.get_user_caution_level(user_id) if user_id else 'medium'
            caution_settings = caution_manager.get_caution_level(caution_level)
            
            # Set the caution level in the error handler
            error_handler.set_caution_level(caution_level)
            
            try:
                # Add caution level parameters to data
                data['caution_level'] = caution_level
                data['confidence_threshold'] = caution_settings['confidence_threshold']
                data['clarification_threshold'] = caution_settings['clarification_threshold']
                data['max_assumption_count'] = caution_settings['max_assumption_count']
                data['allow_extrapolation'] = caution_settings['allow_extrapolation']
                
                # Call the original function
                result = original_chat_data()
                
                # If the result is a response object, convert it to JSON for processing
                if hasattr(result, 'get_json'):
                    result_data = result.get_json()
                else:
                    result_data = result
                
                # Check confidence if available
                if isinstance(result_data, dict) and 'confidence' in result_data:
                    confidence = result_data['confidence']
                    result_data = error_handler.handle_low_confidence(confidence, result_data)
                
                # Add question ID for feedback
                result_data['question_id'] = hash(f"{agent_id}:{question}:{len(history)}")
                
                # Add confidence level (if not present)
                if 'confidence' not in result_data:
                    # Fake confidence of 1.0 - in production you would have a real confidence score
                    result_data['confidence'] = 1.0
                
                return jsonify(result_data)
                
            except Exception as e:
                # Handle application errors
                error_response = error_handler.handle_error(
                    e, 
                    category='application_error',
                    context=f"Agent: {agent_id}, Question: {question}"
                )
                return jsonify(error_response), 500
                
        except Exception as e:
            # Handle request processing errors
            error_response = error_handler.handle_error(
                e, 
                category='request_error',
                context="Processing request data"
            )
            return jsonify(error_response), 400
    
    # Replace the original route function
    app.view_functions['chat_data'] = enhanced_chat_data

# Modify LLMDataEngine to handle caution levels and error reporting
def enhance_llm_data_engine(data_engine, systems):
    """
    Enhance LLMDataEngine with caution level handling and error reporting.
    
    Args:
        data_engine: The LLMDataEngine instance
        systems (dict): Dictionary of enhancement systems
    """
    error_handler = systems['error_handler']
    
    # Store the original get_answer method
    original_get_answer = data_engine.get_answer
    
    # Define enhanced get_answer method
    def enhanced_get_answer(agent_id, input_question, caution_level=None):
        try:
            # Set caution level if provided
            if caution_level:
                error_handler.set_caution_level(caution_level)
            
            # Extract caution settings
            confidence_threshold = error_handler.get_confidence_threshold()
            clarification_threshold = error_handler.get_clarification_threshold()
            
            # Store original thresholds to restore them later
            original_confidence_threshold = None
            if hasattr(data_engine.query_engine, '_is_data_query_required'):
                # This is a very specific implementation detail that would need to be adjusted
                # based on your actual code structure
                if hasattr(data_engine.environment, 'confidence_threshold'):
                    original_confidence_threshold = data_engine.environment.confidence_threshold
                    data_engine.environment.confidence_threshold = confidence_threshold
            
            # Call original method
            results = original_get_answer(agent_id, input_question)
            
            # Restore original thresholds
            if original_confidence_threshold is not None:
                data_engine.environment.confidence_threshold = original_confidence_threshold
            
            # Extract results - handle both dict and tuple return formats
            if isinstance(results, dict):
                # Rich content format (when ENABLE_RICH_CONTENT_RENDERING is True)
                answer = results.get('answer', '')
                explain = results.get('explain', '')
                clarify = results.get('clarify', '')
                answer_type = results.get('answer_type', 'string')
                special_message = results.get('special_message', '')
                revised_question = results.get('revised_question', '')
                return_query = results.get('query', '')
            else:
                # Original tuple format (8 values)
                answer, explain, clarify, answer_type, special_message, input_question, revised_question, return_query = results
            
            # Add confidence information
            if hasattr(data_engine.environment, 'confidence_score'):
                confidence_score = data_engine.environment.confidence_score
            else:
                # Default confidence if not available
                confidence_score = 0.8  # Reasonable default
            
            # Handle low confidence
            if confidence_score < confidence_threshold:
                if confidence_score < (confidence_threshold / 2):
                    # Very low confidence - replace answer with disclaimer
                    answer = "I'm not confident enough to provide an answer. Please try rephrasing your question or providing more context."
                    answer_type = 'string'
                else:
                    # Add disclaimer to answer
                    if answer_type == 'string' and isinstance(answer, str):
                        answer += "\n\nNote: I'm not entirely confident in this answer. Please verify this information."
            
            # Return results with confidence score, preserving original format
            if isinstance(results, dict):
                # Preserve dict format with updated answer and added confidence
                results['answer'] = answer
                results['answer_type'] = answer_type
                results['explain'] = explain
                results['clarify'] = clarify
                results['special_message'] = special_message
                results['confidence_score'] = confidence_score
                return results
            else:
                return (answer, explain, clarify, answer_type, special_message,
                        input_question, revised_question, return_query, confidence_score)
            
        except Exception as e:
            # Handle errors
            error_info = error_handler.handle_error(
                e, 
                category='query_execution_error',
                context=f"Agent: {agent_id}, Question: {input_question}"
            )
            
            # Return error as answer
            return (error_info['user_message'], "", "", "string", "", 
                    input_question, "", "", 0.0)
    
    # Replace the original method
    data_engine.get_answer = enhanced_get_answer

# Modify the explain function to include feedback
def enhance_explain_route(app, systems):
    """
    Enhance the /chat/data/explain route with error handling.
    
    Args:
        app (Flask): The Flask application
        systems (dict): Dictionary of enhancement systems
    """
    error_handler = systems['error_handler']
    
    # Store the original route function
    original_explain = app.view_functions.get('explain_data')
    
    if not original_explain:
        logging.error("Could not find the 'explain_data' route function")
        return
    
    # Define the enhanced route function
    def enhanced_explain():
        try:
            # Call the original function
            result = original_explain()
            return result
        except Exception as e:
            # Handle application errors
            error_response = error_handler.handle_error(
                e, 
                category='explain_error',
                context="Generating explanation"
            )
            return jsonify(error_response), 500
    
    # Replace the original route function
    app.view_functions['explain_data'] = enhanced_explain