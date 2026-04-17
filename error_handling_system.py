import config as cfg


class ErrorHandler:
    """
    Centralized error handling system for NLQ application.
    Manages error categories, user-friendly messages, and logging.
    """
    
    # Error categories with default messages
    ERROR_CATEGORIES = cfg.DATA_AGENT_ERROR_CATEGORIES
    
    # Caution level settings - determines behavior
    CAUTION_LEVELS = cfg.DATA_AGENT_CAUTION_LEVELS
    
    def __init__(self, logger, caution_level='medium'):
        """
        Initialize the error handler with logger and caution level.
        
        Args:
            logger: Logger object for recording errors
            caution_level (str): One of 'low', 'medium', 'high', 'very_high'
        """
        self.logger = logger
        self.is_enabled = cfg.ENABLE_CAUTION_SYSTEM
        self.set_caution_level(caution_level)
        
    def set_caution_level(self, level):
        """Set the caution level for the application."""
        if level not in self.CAUTION_LEVELS:
            self.logger.warning(f"Invalid caution level: {level}. Defaulting to 'medium'.")
            level = 'medium'
            
        self.caution_level = level
        self.confidence_threshold = self.CAUTION_LEVELS[level]['confidence_threshold']
        self.ask_for_clarification_threshold = self.CAUTION_LEVELS[level]['ask_for_clarification_threshold']
        self.logger.info(f"Caution level set to: {level}")
    
    def get_confidence_threshold(self):
        """Get the current confidence threshold based on caution level."""
        return self.confidence_threshold
    
    def get_clarification_threshold(self):
        """Get the current ask-for-clarification threshold based on caution level."""
        return self.ask_for_clarification_threshold
        
    def handle_error(self, error, category='unknown', context=None):
        """
        Log the error and return a user-friendly message.
        
        Args:
            error: The exception or error message
            category: Error category for selecting appropriate user message
            context: Additional context for logging
            
        Returns:
            dict: Error response with user-friendly message
        """
        # Log the error with context
        if context:
            self.logger.error(f"{category} error: {error}. Context: {context}")
        else:
            self.logger.error(f"{category} error: {error}")
            
        # Get user-friendly message
        user_message = self.ERROR_CATEGORIES.get(category, self.ERROR_CATEGORIES['unknown'])
        
        # Return error response
        return {
            'status': 'error',
            'error_category': category,
            'user_message': user_message,
            'show_feedback': True,  # Always encourage feedback on errors
            'using_caution_system': self.is_enabled
        }
    
    def handle_low_confidence(self, confidence_score, response=None):
        """
        Handle cases where the AI's confidence is low.
        
        Args:
            confidence_score: Numerical confidence score (0-1)
            response: Original response from AI
            
        Returns:
            dict: Modified response or error response
        """
        if confidence_score < self.confidence_threshold:
            self.logger.warning(f"Low confidence score: {confidence_score}")
            
            # If confidence is extremely low, return an error
            if confidence_score < (self.confidence_threshold / 2):
                return self.handle_error(
                    f"Confidence score too low: {confidence_score}", 
                    'ai_confidence',
                    context=f"Original response: {response[:100]}..."
                )
            
            # Otherwise, add a disclaimer to the response
            if response:
                disclaimer = ("\n\nNote: I'm not entirely confident in this answer. "
                             "Please verify this information and provide feedback if it's incorrect.")
                
                if isinstance(response, dict):
                    if 'answer' in response:
                        response['answer'] += disclaimer
                    response['low_confidence'] = True
                    response['confidence_score'] = confidence_score
                elif isinstance(response, str):
                    response += disclaimer
                    response = {
                        'status': 'success',
                        'answer': response,
                        'low_confidence': True,
                        'confidence_score': confidence_score
                    }
            
            return response
        
        # If confidence is sufficient, return the original response
        return response
    
    def should_ask_for_clarification(self, clarity_score):
        """
        Determine if the system should ask for clarification based on current caution level.
        
        Args:
            clarity_score: Score indicating how clear the user's question is (0-1)
            
        Returns:
            bool: True if clarification should be requested
        """
        return clarity_score < self.ask_for_clarification_threshold