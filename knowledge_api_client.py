"""
Knowledge API Client
Client module for communicating with the Knowledge API service
Used by the main application to interact with the workflow assistant
"""

import os
import requests
import json
import logging
from typing import Dict, Optional
from urllib.parse import urljoin
from CommonUtils import get_knowledge_api_base_url
from config import KNOWLEDGE_API_TIMEOUT

logger = logging.getLogger(__name__)

class KnowledgeAPIClient:
    """Client for interacting with the Knowledge API service"""
    
    def __init__(self, base_url: str = None, timeout: int = KNOWLEDGE_API_TIMEOUT):
        """
        Initialize the Knowledge API client
        
        Args:
            base_url: Base URL of the Knowledge API service
            timeout: Request timeout in seconds
        """
        self.base_url = get_knowledge_api_base_url()
        self.timeout = timeout
        self.session = requests.Session()
    
    def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict:
        """
        Make a request to the Knowledge API
        
        Args:
            method: HTTP method
            endpoint: API endpoint
            **kwargs: Additional request parameters
        
        Returns:
            Response data as dictionary
        
        Raises:
            Exception: If request fails
        """
        print(f'Making request to {endpoint}...')
        
        url = urljoin(self.base_url, endpoint)
        
        # Set default timeout if not provided
        if 'timeout' not in kwargs:
            kwargs['timeout'] = self.timeout
        
        try:
            response = self.session.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        
        except requests.exceptions.Timeout:
            logger.error(f"Request timeout for {method} {url}")
            raise Exception(f"Request timeout after {self.timeout} seconds")
        
        except requests.exceptions.ConnectionError:
            logger.error(f"Connection error for {method} {url}")
            raise Exception(f"Could not connect to Knowledge API at {self.base_url}")
        
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error for {method} {url}: {e}")
            try:
                error_data = e.response.json()
                raise Exception(error_data.get('error', str(e)))
            except:
                raise Exception(str(e))
        
        except Exception as e:
            logger.error(f"Unexpected error for {method} {url}: {e}")
            raise
    
    def health_check(self) -> Dict:
        """
        Check if the Knowledge API service is healthy
        
        Returns:
            Health status information
        """
        return self._make_request('GET', '/health')
    
    def ask_workflow_assistant(
        self,
        question: str,
        workflow_context: Dict = None,
        session_id: str = None,
        include_history: bool = False
    ) -> Dict:
        """
        Ask the workflow assistant a question
        
        Args:
            question: User's question
            workflow_context: Current workflow state (nodes, connections, etc.)
            session_id: Optional session ID for conversation tracking
            include_history: Whether to include conversation history
        
        Returns:
            dict: {
                'status': 'success' | 'error',
                'response': 'AI response text',
                'session_id': 'session-id',
                'error': 'error message if applicable'
            }
        """
        payload = {
            'question': question,
            'workflow_context': workflow_context or {},
            'include_history': include_history
        }
        
        if session_id:
            payload['session_id'] = session_id
        
        return self._make_request(
            'POST',
            '/api/workflow/assistant',
            json=payload
        )
    
    def get_conversation_history(self, session_id: str) -> Dict:
        """
        Get conversation history for a session
        
        Args:
            session_id: Session ID
        
        Returns:
            dict: {
                'status': 'success',
                'history': [...],
                'session_id': 'session-id'
            }
        """
        return self._make_request(
            'GET',
            f'/api/workflow/assistant/history?session_id={session_id}'
        )
    
    def clear_conversation_history(self, session_id: str) -> Dict:
        """
        Clear conversation history for a session
        
        Args:
            session_id: Session ID
        
        Returns:
            dict: {
                'status': 'success',
                'message': 'Conversation history cleared'
            }
        """
        return self._make_request(
            'DELETE',
            f'/api/workflow/assistant/history?session_id={session_id}'
        )
    
    def validate_workflow(self, workflow_context: Dict) -> Dict:
        """
        Validate a workflow configuration
        
        Args:
            workflow_context: Workflow state to validate
        
        Returns:
            dict: {
                'status': 'success',
                'valid': true/false,
                'issues': [...],
                'suggestions': [...]
            }
        """
        return self._make_request(
            'POST',
            '/api/workflow/validate',
            json={'workflow_context': workflow_context}
        )
        
        
    def resolve_workflow_ids(self, commands):
        """
        Resolve agent names and connection names to IDs
        
        Args:
            commands (dict): Workflow commands with natural language names
            
        Returns:
            dict: Response with resolved IDs
        """
        try:
            response = requests.post(
                f"{self.base_url}/api/workflow/resolve-ids",
                json={'commands': commands},
                timeout=KNOWLEDGE_API_TIMEOUT  # AI resolution may take longer
            )
            response.raise_for_status()
            return response.json()
        
        except requests.exceptions.Timeout:
            logger.error("Knowledge API timeout during ID resolution")
            return {
                'status': 'error',
                'error': 'Request timeout - AI resolution took too long'
            }
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Knowledge API request error during ID resolution: {e}")
            return {
                'status': 'error',
                'error': f'Knowledge API error: {str(e)}'
            }
        
        except Exception as e:
            logger.error(f"Unexpected error in resolve_workflow_ids: {e}")
            return {
                'status': 'error',
                'error': str(e)
            }
        
    def send_execution_result(self, session_id, commands, result):
        """
        Send workflow execution results to update context
        
        Args:
            session_id (str): Session ID
            commands (dict): Executed commands
            result (dict): Execution result
            
        Returns:
            dict: Response from API
        """
        try:
            response = requests.post(
                f"{self.base_url}/api/workflow/assistant/result",
                json={
                    'session_id': session_id,
                    'commands': commands,
                    'result': result
                },
                timeout=KNOWLEDGE_API_TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        
        except requests.exceptions.Timeout:
            logger.error("Knowledge API timeout during execution result")
            return {
                'status': 'error',
                'error': 'Request timeout'
            }
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Knowledge API request error: {e}")
            return {
                'status': 'error',
                'error': f'Knowledge API error: {str(e)}'
            }
        
        except Exception as e:
            logger.error(f"Unexpected error in send_execution_result: {e}")
            return {
                'status': 'error',
                'error': str(e)
            }


    def get_conversation_history(self, session_id):
        """
        Get conversation history for a session
        
        Args:
            session_id (str): Session ID
            
        Returns:
            dict: Response with conversation history
        """
        try:
            response = requests.get(
                f"{self.base_url}/api/workflow/assistant/history",
                params={'session_id': session_id},
                timeout=KNOWLEDGE_API_TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        
        except requests.exceptions.Timeout:
            logger.error("Knowledge API timeout during get history")
            return {
                'status': 'error',
                'error': 'Request timeout'
            }
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Knowledge API request error: {e}")
            return {
                'status': 'error',
                'error': f'Knowledge API error: {str(e)}'
            }
        
        except Exception as e:
            logger.error(f"Unexpected error in get_conversation_history: {e}")
            return {
                'status': 'error',
                'error': str(e)
            }


    def clear_conversation_history(self, session_id):
        """
        Clear conversation history for a session
        
        Args:
            session_id (str): Session ID
            
        Returns:
            dict: Response confirming history cleared
        """
        try:
            response = requests.delete(
                f"{self.base_url}/api/workflow/assistant/history",
                params={'session_id': session_id},
                timeout=KNOWLEDGE_API_TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        
        except requests.exceptions.Timeout:
            logger.error("Knowledge API timeout during clear history")
            return {
                'status': 'error',
                'error': 'Request timeout'
            }
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Knowledge API request error: {e}")
            return {
                'status': 'error',
                'error': f'Knowledge API error: {str(e)}'
            }
        
        except Exception as e:
            logger.error(f"Unexpected error in clear_conversation_history: {e}")
            return {
                'status': 'error',
                'error': str(e)
            }


    def validate_workflow(self, workflow_context):
        """
        Validate a workflow configuration
        
        Args:
            workflow_context (dict): Workflow state to validate
            
        Returns:
            dict: Validation results with issues and suggestions
        """
        try:
            response = requests.post(
                f"{self.base_url}/api/workflow/validate",
                json={'workflow_context': workflow_context},
                timeout=KNOWLEDGE_API_TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        
        except requests.exceptions.Timeout:
            logger.error("Knowledge API timeout during validation")
            return {
                'status': 'error',
                'error': 'Request timeout'
            }
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Knowledge API request error: {e}")
            return {
                'status': 'error',
                'error': f'Knowledge API error: {str(e)}'
            }
        
        except Exception as e:
            logger.error(f"Unexpected error in validate_workflow: {e}")
            return {
                'status': 'error',
                'error': str(e)
            }




# ============================================================================
# Helper Functions for Common Use Cases
# ============================================================================

def get_knowledge_client() -> KnowledgeAPIClient:
    """
    Get a configured Knowledge API client instance
    
    Returns:
        KnowledgeAPIClient instance
    """
    return KnowledgeAPIClient()


def ask_workflow_question(
    question: str,
    workflow_context: Dict = None
) -> str:
    """
    Simple helper to ask a workflow question and get the response text
    
    Args:
        question: User's question
        workflow_context: Current workflow state
    
    Returns:
        Response text from the assistant
    
    Raises:
        Exception: If request fails
    """
    client = get_knowledge_client()
    result = client.ask_workflow_assistant(question, workflow_context)
    
    if result.get('status') == 'error':
        raise Exception(result.get('error', 'Unknown error'))
    
    return result.get('response', '')


# ============================================================================
# Adapter Pattern (Optional - for consistency with AgentAPIClient)
# ============================================================================

class KnowledgeAPIAdapter:
    """
    Adapter to make KnowledgeAPIClient compatible with existing patterns
    Similar to AgentAPIAdapter
    """
    
    def __init__(self, client: KnowledgeAPIClient = None):
        """Initialize with a KnowledgeAPIClient instance"""
        self.client = client or get_knowledge_client()
    
    def ask(
        self,
        question: str,
        context: Dict = None,
        session_id: str = None
    ) -> str:
        """
        Ask a question and return just the response text
        
        Args:
            question: User's question
            context: Workflow context
            session_id: Optional session ID
        
        Returns:
            Response text
        """
        result = self.client.ask_workflow_assistant(
            question=question,
            workflow_context=context,
            session_id=session_id
        )
        
        if result.get('status') == 'error':
            raise Exception(result.get('error', 'Unknown error'))
        
        return result.get('response', '')
    
    def validate(self, workflow_context: Dict) -> Dict:
        """
        Validate a workflow
        
        Args:
            workflow_context: Workflow to validate
        
        Returns:
            Validation result
        """
        return self.client.validate_workflow(workflow_context)
