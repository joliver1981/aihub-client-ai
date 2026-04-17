import logging
import json
import config as cfg
from AppUtils import azureQuickPrompt, azureMiniQuickPrompt
import system_prompts as sysprompts

class ResponseFilter:
    """
    Simple filter that uses LLM reasoning to detect and rewrite responses 
    containing technical details.
    """
    
    def __init__(self, logger=None):
        """Initialize the response filter."""
        self.logger = logger or logging.getLogger(__name__)
        # Get configuration - default to False if not present
        self.enabled = getattr(cfg, 'ENABLE_RESPONSE_FILTER', True)
    
    def filter_response(self, response, user_question):
        """
        Check if a response contains technical details and rewrite if needed.
        
        Args:
            response (str or dict): The original response
            user_question (str): The original user question
            
        Returns:
            Same type as response: The filtered response
        """
        if not self.enabled:
            return response
        print('CAUTION SYSTEM ACTIVE!')
        print('EVALUATING RESPONSE:', str(response))
        # Handle different response types
        if isinstance(response, dict):
            if 'answer' in response and isinstance(response['answer'], str):
                response['answer'] = self._filter_text(response['answer'], user_question)
            return response
        elif isinstance(response, str):
            return self._filter_text(response, user_question)
        else:
            # For non-text responses (like DataFrames), return as is
            return response
    
    def _filter_text(self, text, user_question):
        """
        Filter a text response using LLM reasoning.
        
        Args:
            text (str): The text to filter
            user_question (str): The original user question
            
        Returns:
            str: The filtered text
        """
        # Skip empty responses
        if not text or text.strip() == "":
            return text
            
        # Skip very short responses (unlikely to have technical details)
        if len(text) < 30:
            return text
            
        try:
            # System prompt for detecting and rewriting technical responses
            system = sysprompts.SYS_PROMPT_RESPONSE_FILTER_SYSTEM
            
            # Create prompt with the response and user question for context
            prompt = sysprompts.SYS_PROMPT_RESPONSE_FILTER_PROMPT.replace('{user_question}', str(user_question)).replace('{text}', str(text))
            
            # Use the LLM to check and rewrite if needed
            #print('USE_MINI_MODELS_WHEN_POSSIBLE:', cfg.USE_MINI_MODELS_WHEN_POSSIBLE)
            if cfg.USE_MINI_MODELS_WHEN_POSSIBLE:
                print('Checking with mini model...', cfg.MINI_MODEL_REASONING_EFFORT)
                result = azureMiniQuickPrompt(prompt=prompt, system=system)
            else:
                print('Checking with normal model...')
                result = azureQuickPrompt(prompt=prompt, system=system, use_alternate_api=True)
            
            # If the result is exactly the same as the input, no rewriting was needed
            if result.strip() == text.strip():
                print('No changes needed, returning original response...')
                return text
            
            # Otherwise, return the rewritten response
            self.logger.info("Technical details detected and rewritten in response")
            print("Technical details detected and rewritten in response")
            result = '*' + str(result)  # TODO Remove this line adding a '*' before going live
            return result
            
        except Exception as e:
            # On any error, return the original text and log the issue
            self.logger.error(f"Error filtering response: {str(e)}")
            return text