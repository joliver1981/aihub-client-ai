# engine_enhancements.py - Enhancements for the LLMQueryEngine and LLMAnalyticalEngine classes

import logging
import functools
import json
import numpy as np
from datetime import datetime
import config as cfg
from response_filter import ResponseFilter

class EnhancedLLMQueryEngine:
    """
    Wrapper/enhancer class for the LLMQueryEngine with improved error handling, 
    feedback processing, and caution level support.
    """
    
    def __init__(self, original_engine, error_handler, caution_manager):
        """
        Initialize the enhanced engine.
        
        Args:
            original_engine: Original LLMQueryEngine instance
            error_handler: ErrorHandler instance
            caution_manager: CautionManager instance
        """
        self.original_engine = original_engine
        self.error_handler = error_handler
        self.caution_manager = caution_manager
        self.logger = logging.getLogger(__name__)
        
        # Wrap original methods with enhanced versions
        self._wrap_methods()
    
    def _wrap_methods(self):
        """Wrap key methods of the original engine with enhanced versions."""
        # Store originals
        self._original_is_more_info_required = self.original_engine._is_more_info_required
        self._original_is_data_query_required = self.original_engine._is_data_query_required
        self._original_initialize_data = self.original_engine._initialize_data
        self._original_get_query_from_question = self.original_engine._get_query_from_question
        
        # Replace with enhanced versions
        self.original_engine._is_more_info_required = self._enhanced_is_more_info_required
        self.original_engine._is_data_query_required = self._enhanced_is_data_query_required
        self.original_engine._initialize_data = self._enhanced_initialize_data
        self.original_engine._get_query_from_question = self._enhanced_get_query_from_question
    
    def _enhanced_is_more_info_required(self):
        """
        Enhanced version of _is_more_info_required that incorporates caution level.
        
        Higher caution levels will lower the threshold for asking for more information.
        """
        try:
            # Get the original result
            IS_REQUIRED, request_for_more_information, confidence = self._original_is_more_info_required()
            
            # Get the caution level settings
            caution_level = self.error_handler.caution_level
            clarification_threshold = self.error_handler.get_clarification_threshold()
            
            # Adjust based on caution level
            if not IS_REQUIRED and float(confidence) < clarification_threshold * 100:
                self.logger.info(f"Caution level {caution_level} triggered asking for more information (confidence: {confidence})")
                IS_REQUIRED = True
                if not request_for_more_information or request_for_more_information == '':
                    request_for_more_information = (
                        "I'd like to be more certain before answering your question. "
                        "Could you please provide more details or context?"
                    )
            
            return IS_REQUIRED, request_for_more_information, confidence
            
        except Exception as e:
            self.logger.error(f"Error in enhanced _is_more_info_required: {e}")
            return self._original_is_more_info_required()
    
    def _enhanced_is_data_query_required(self):
        """
        Enhanced version of _is_data_query_required that incorporates caution level.
        
        Higher caution levels will encourage data querying in more cases.
        """
        try:
            # Get the original result
            IS_REQUIRED, explanation, confidence = self._original_is_data_query_required()
            
            # Get the caution level settings
            caution_level = self.error_handler.caution_level
            confidence_threshold = self.error_handler.get_confidence_threshold()
            
            # Calculate adjusted threshold based on caution level
            adjusted_threshold = 1.0 - confidence_threshold  # Invert since this is a different type of confidence
            
            # Adjust based on caution level
            if not IS_REQUIRED and float(confidence) < adjusted_threshold * 100:
                self.logger.info(f"Caution level {caution_level} triggered data query (confidence: {confidence})")
                IS_REQUIRED = True
                explanation = "Based on caution settings, a new data query is required to ensure accuracy."
            
            return IS_REQUIRED, explanation, confidence
            
        except Exception as e:
            self.logger.error(f"Error in enhanced _is_data_query_required: {e}")
            return self._original_is_data_query_required()
    
    def _enhanced_initialize_data(self, input_question, is_first_question=True):
        """
        Enhanced version of _initialize_data with improved error handling and logging.
        """
        try:
            # Record start time for performance monitoring
            start_time = datetime.now()
            
            # Get the result
            result = self._original_initialize_data(input_question, is_first_question)
            
            # Calculate execution time
            execution_time = (datetime.now() - start_time).total_seconds()
            
            # Log result with performance info
            self.logger.info(f"Data initialization completed in {execution_time:.2f} seconds. Success: {result}")
            
            # Store metrics
            self.original_engine.environment.last_query_execution_time = execution_time
            
            return result
            
        except Exception as e:
            self.logger.error(f"Error in _initialize_data: {e}")
            # Record the error message
            self.original_engine.environment.last_query_fail_message = str(e)
            # Record that the operation failed
            self.original_engine.environment.was_last_query_successful = False
            return False
    
    def _enhanced_get_query_from_question(self, input_question, is_first_question=False):
        """
        Enhanced version of _get_query_from_question with improved error handling and context tracking.
        """
        try:
            # Get caution level settings
            caution_level = self.error_handler.caution_level
            max_assumptions = self.caution_manager.get_caution_level(caution_level)['max_assumption_count']
            allow_extrapolation = self.caution_manager.get_caution_level(caution_level)['allow_extrapolation']
            
            # Construct enhanced prompt with caution guidance
            caution_guidance = ""
            if max_assumptions == 0:
                caution_guidance = "\nYou should make NO ASSUMPTIONS about the data or user intent. If anything is unclear, don't generate a query."
            elif max_assumptions <= 2:
                caution_guidance = f"\nYou may make up to {max_assumptions} reasonable assumptions if necessary, but be conservative."
            
            if not allow_extrapolation:
                caution_guidance += "\nDo not extrapolate beyond the data that is explicitly available in the database schema."
            
            # Track any assumptions made
            assumptions_made = []
            
            # Call original method with enhanced input
            # Note: This would require modifying the original method to accept additional parameters
            # or finding another way to pass this guidance to the SQL query generation
            result = self._original_get_query_from_question(input_question, is_first_question)
            
            # If successful, record any assumptions that were made
            if result and hasattr(self.original_engine.environment, 'assumptions_made'):
                self.original_engine.environment.assumptions_made = assumptions_made
            
            return result
            
        except Exception as e:
            self.logger.error(f"Error in _get_query_from_question: {e}")
            return None
        

class EnhancedLLMAnalyticalEngine:
    """
    Wrapper/enhancer class for the LLMAnalyticalEngine with improved error handling, 
    result validation, and confidence scoring.
    """
    def __init__(self, original_engine, error_handler):
        """
        Initialize the enhanced engine.
        
        Args:
            original_engine: Original LLMAnalyticalEngine instance
            error_handler: ErrorHandler instance
        """
        self.original_engine = original_engine
        self.error_handler = error_handler
        self.logger = logging.getLogger(__name__)
        
        # Wrap original methods with enhanced versions
        self._wrap_methods()
    
    def _wrap_methods(self):
        """Wrap key methods of the original engine with enhanced versions."""
        # Store originals
        self._original_get_answer = self.original_engine.get_answer
        self._original_is_analytical_query_required_v2 = self.original_engine._is_analytical_query_required_v2
        
        # Replace with enhanced versions
        self.original_engine.get_answer = self._enhanced_get_answer
        self.original_engine._is_analytical_query_required_v2 = self._enhanced_is_analytical_query_required_v2
    
    def _enhanced_get_answer(self, input_question, is_follow_up=False):
        """
        Enhanced version of get_answer with result validation and confidence scoring.
        """
        try:
            # Call original method
            answer, explain, clarify, answer_type, special_message, revised_question = self._original_get_answer(input_question, is_follow_up)
            
            # Validate the results based on answer_type
            if answer_type == 'dataframe':
                # Calculate a confidence score based on the dataframe size and completeness
                confidence_score = self._calculate_df_confidence(answer)
            elif answer_type == 'chart':
                # Charts generally have high confidence if they were generated successfully
                confidence_score = 0.85
            elif answer_type == 'string':
                # Check if the answer contains uncertainty markers
                confidence_score = self._calculate_text_confidence(answer)
            elif answer_type == 'error':
                # Error responses have zero confidence
                confidence_score = 0.0
            else:
                # Default confidence
                confidence_score = 0.7
            
            # Store confidence score
            self.original_engine.environment.last_answer_confidence = confidence_score
            
            print(45 * 'CAUTION ')
            # Add confidence disclaimer if needed
            if confidence_score < self.error_handler.confidence_threshold and answer_type == 'string':
                answer = self._add_confidence_disclaimer(answer, confidence_score)
            elif confidence_score < self.error_handler.confidence_threshold and answer_type != 'string':
                special_message = self._add_confidence_disclaimer(answer, confidence_score)
                
            return answer, explain, clarify, answer_type, special_message, revised_question
            
        except Exception as e:
            self.logger.error(f"Error in get_answer: {e}")
            error_message = f"An error occurred while processing your request: {str(e)}"
            return error_message, "", "", "error", "", ""
    
    def _enhanced_is_analytical_query_required_v2(self):
        """
        Enhanced version of _is_analytical_query_required_v2 that incorporates caution level.
        """
        try:
            # Get original result
            IS_REQUIRED, confidence, explanation = self._original_is_analytical_query_required_v2()
            
            # Get caution level settings
            caution_level = self.error_handler.caution_level
            confidence_threshold = self.error_handler.get_confidence_threshold()
            
            # Adjust decision based on caution level
            # For high caution levels, we want to perform more analytical processing
            if not IS_REQUIRED and caution_level in ['high', 'very_high']:
                # For high caution, lower the threshold for performing analytics
                if float(confidence) < 80:  # Arbitrary threshold
                    IS_REQUIRED = True
                    explanation = "Based on caution settings, analytical processing is required for verification."
            
            return IS_REQUIRED, confidence, explanation
            
        except Exception as e:
            self.logger.error(f"Error in _is_analytical_query_required_v2: {e}")
            return self._original_is_analytical_query_required_v2()
    
    def _calculate_df_confidence(self, df):
        """
        Calculate confidence score for dataframe results.
        
        Args:
            df: Pandas DataFrame
            
        Returns:
            float: Confidence score between 0 and 1
        """
        try:
            # Basic checks
            if df is None or len(df) == 0:
                return 0.3  # Empty dataframe has low confidence
            
            # Check for missing values
            missing_pct = df.isnull().mean().mean()
            
            # Check data types (numeric data often more reliable)
            numeric_cols = df.select_dtypes(include=np.number).columns
            numeric_pct = len(numeric_cols) / len(df.columns) if len(df.columns) > 0 else 0
            
            # Consider size - very small or very large datasets may be less reliable
            size_factor = 1.0
            if len(df) < 5:
                size_factor = 0.8  # Small datasets may be less reliable
            elif len(df) > 1000:
                size_factor = 0.9  # Very large datasets may be overwhelming
            
            # Calculate final score (adjust weights as needed)
            confidence = (
                0.7 +                    # Base confidence
                (1 - missing_pct) * 0.2 + # Less missing data = higher confidence
                numeric_pct * 0.1        # More numeric cols = higher confidence
            ) * size_factor
            
            # Ensure it's between 0 and 1
            return max(0.0, min(1.0, confidence))
            
        except Exception as e:
            self.logger.error(f"Error calculating dataframe confidence: {e}")
            return 0.7  # Default confidence
    
    def _calculate_text_confidence(self, text):
        """
        Calculate confidence score for text answers by looking for uncertainty markers.
        
        Args:
            text: Text answer string
            
        Returns:
            float: Confidence score between 0 and 1
        """
        try:
            # List of uncertainty markers
            uncertainty_markers = [
                "I'm not sure", "uncertain", "might be", "could be", "possibly",
                "I think", "probably", "may", "uncertain", "unclear", "ambiguous",
                "doubt", "not confident", "not certain", "hard to tell", "can't tell"
            ]
            
            # Count markers
            marker_count = sum(1 for marker in uncertainty_markers if marker.lower() in text.lower())
            
            # Calculate confidence based on markers
            if marker_count == 0:
                return 0.9  # High confidence if no uncertainty markers
            elif marker_count <= 2:
                return 0.7  # Medium confidence with few markers
            elif marker_count <= 5:
                return 0.5  # Lower confidence with several markers
            else:
                return 0.3  # Very low confidence with many markers
            
        except Exception as e:
            self.logger.error(f"Error calculating text confidence: {e}")
            return 0.7  # Default confidence
    
    def _add_confidence_disclaimer(self, answer, confidence_score):
        """
        Add a confidence disclaimer to low-confidence answers.
        
        Args:
            answer: Original answer text
            confidence_score: Calculated confidence score
            
        Returns:
            str: Answer with disclaimer
        """
        # Different disclaimers based on confidence level
        if confidence_score < 0.3:
            disclaimer = ("\n\nNOTE: I have very low confidence in this answer. "
                         "The information provided may be incorrect or incomplete. "
                         "Please verify from other sources before making decisions based on this.")
        elif confidence_score < 0.5:
            disclaimer = ("\n\nNOTE: I have low confidence in this answer. "
                         "Some aspects may be incorrect or incomplete. "
                         "Please consider verifying important details.")
        else:
            disclaimer = ("\n\nNOTE: I'm not entirely confident in all aspects of this answer. "
                         "Please use this information with appropriate caution.")
        
        return answer + disclaimer


def enhance_engines(data_engine, systems):
    """
    Enhance the query and analytical engines with the enhancement systems.
    
    Args:
        data_engine: LLMDataEngine instance
        systems: Dictionary of enhancement systems
        
    Returns:
        tuple: Enhanced query engine and analytical engine
    """
    error_handler = systems['error_handler']
    caution_manager = systems['caution_manager']
    
    # Check if caution system is enabled
    if cfg.ENABLE_CAUTION_SYSTEM:
        # Enhance query engine with caution system
        enhanced_query_engine = EnhancedLLMQueryEngine(
            data_engine.query_engine,
            error_handler,
            caution_manager
        )
        
        # Enhance analytical engine with caution system
        enhanced_analytical_engine = EnhancedLLMAnalyticalEngine(
            data_engine.analytical_engine,
            error_handler
        )
    else:
        # Return unenhanced engines if caution system is disabled
        enhanced_query_engine = data_engine.query_engine
        enhanced_analytical_engine = data_engine.analytical_engine
    
    return enhanced_query_engine, enhanced_analytical_engine