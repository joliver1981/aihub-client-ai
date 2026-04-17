"""
Agent API Client
Client module for communicating with the separated Agent API service
This can be used by the main application to interact with agents
"""

import os
import requests
import json
import logging
from logging.handlers import WatchedFileHandler
from typing import Dict, List, Optional, Any
from urllib.parse import urljoin
import time
from CommonUtils import get_agent_api_base_url, rotate_logs_on_startup, get_log_path

rotate_logs_on_startup(os.getenv('AGENT_API_CLIENT_LOG', get_log_path('agent_api_client_log.txt')))

# Configure logging
logger = logging.getLogger("AgentClientAPI")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=os.getenv('AGENT_API_CLIENT_LOG', get_log_path('agent_api_client_log.txt')), encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)


class AgentAPIClient:
    """Client for interacting with the Agent API service"""
    
    def __init__(self, base_url: str = None, timeout: int = 60, max_retries: int = 3):
        """
        Initialize the Agent API client
        
        Args:
            base_url: Base URL of the Agent API service
            timeout: Request timeout in seconds
            max_retries: Maximum number of retry attempts
        """
        self.base_url = base_url or os.getenv('AGENT_API_URL', get_agent_api_base_url())
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        
        # Set up retry logic
        from requests.adapters import HTTPAdapter
        from requests.packages.urllib3.util.retry import Retry
        
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
    
    def _make_request_do_not_use(self, method: str, endpoint: str, **kwargs) -> Dict:
        """
        Make a request to the Agent API
        
        Args:
            method: HTTP method
            endpoint: API endpoint
            **kwargs: Additional request parameters
        
        Returns:
            Response data as dictionary
        
        Raises:
            Exception: If request fails after retries
        """
        url = urljoin(self.base_url, endpoint)
        
        # Set default timeout if not provided
        if 'timeout' not in kwargs:
            kwargs['timeout'] = self.timeout
        
        try:
            response = self.session.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Request to {url} failed: {str(e)}")
            raise Exception(f"Agent API request failed: {str(e)}")
        
    def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict:
        """
        Make a request to the Agent API
        """
        url = urljoin(self.base_url, endpoint)
        
        # Set default timeout if not provided
        if 'timeout' not in kwargs:
            kwargs['timeout'] = self.timeout
        
        # Force connection close
        if 'headers' not in kwargs:
            kwargs['headers'] = {}

        kwargs['headers']['Connection'] = 'close'
        
        try:
            # Don't use session - create fresh connection each time
            response = requests.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            logger.error(f"Request to {url} timed out after {self.timeout} seconds")
            raise Exception(f"Agent API request timed out")
        except requests.exceptions.RequestException as e:
            logger.error(f"Agent API Request to {url} failed: {str(e)}")
            raise Exception(f"Agent API request failed: {str(e)}")
        except Exception as e:
            logger.error(f"Request to {url} failed: {str(e)}")
    
    def health_check(self) -> bool:
        """
        Check if the Agent API service is healthy
        
        Returns:
            True if service is healthy, False otherwise
        """
        try:
            response = self._make_request('GET', '/health')
            return response.get('status') == 'healthy'
        except:
            return False
    
    def list_agents(self) -> List[Dict]:
        """
        Get list of all available agents
        
        Returns:
            List of agent information dictionaries
        """
        response = self._make_request('GET', '/agents')
        if response.get('status') == 'success':
            return response.get('agents', [])
        raise Exception(response.get('message', 'Failed to list agents'))
    
    def get_agent_info(self, agent_id: int) -> Dict:
        """
        Get detailed information about a specific agent
        
        Args:
            agent_id: ID of the agent
        
        Returns:
            Agent information dictionary
        """
        response = self._make_request('GET', f'/agents/{agent_id}')
        if response.get('status') == 'success':
            return response.get('agent', {})
        raise Exception(response.get('message', 'Failed to get agent info'))
    
    def load_agent(self, agent_id: int) -> bool:
        """
        Load an agent into memory
        
        Args:
            agent_id: ID of the agent to load
        
        Returns:
            True if successful
        """
        response = self._make_request('POST', f'/agents/{agent_id}/load')
        if response.get('status') == 'success':
            return True
        raise Exception(response.get('message', 'Failed to load agent'))
    
    def unload_agent(self, agent_id: int) -> bool:
        """
        Unload an agent from memory
        
        Args:
            agent_id: ID of the agent to unload
        
        Returns:
            True if successful
        """
        response = self._make_request('POST', f'/agents/{agent_id}/unload')
        if response.get('status') == 'success':
            return True
        raise Exception(response.get('message', 'Failed to unload agent'))
    
    def chat(self, agent_id: int, prompt: str, chat_history: List[Dict] = None, use_smart_render: bool = False, user_id: int = None) -> Dict:
        """
        Send a chat message to an agent (stateless)
        
        Args:
            agent_id: ID of the agent
            prompt: User message/prompt
            chat_history: Previous chat history
        
        Returns:
            Dictionary with response and updated chat history
        """
        if use_smart_render:
            use_smart_render = 'true'
        else:
            use_smart_render = 'false'
            
        data = {
            'agent_id': agent_id,
            'prompt': prompt,
            'chat_history': chat_history or [],
            'use_smart_render': use_smart_render,
            'user_id': user_id
        }
        
        logger.info("AgentAPIClient - Making chat request via POST...")
        response = self._make_request('POST', '/chat', json=data)
        
        if response.get('status') == 'success':
            return {
                'response': response.get('response'),
                'chat_history': response.get('chat_history', [])
            }
        
        # Return error response in expected format
        return {
            'response': response.get('response', 'An error occurred'),
            'chat_history': response.get('chat_history', chat_history or [])
        }
    
    def create_session(self, agent_id: int, user_id: Optional[str] = None) -> str:
        """
        Create a new chat session
        
        Args:
            agent_id: ID of the agent
            user_id: Optional user identifier
        
        Returns:
            Session ID
        """
        data = {
            'agent_id': agent_id,
            'user_id': user_id
        }
        
        response = self._make_request('POST', '/sessions', json=data)
        
        if response.get('status') == 'success':
            return response.get('session_id')
        raise Exception(response.get('message', 'Failed to create session'))
    
    def delete_session(self, session_id: str) -> bool:
        """
        Delete a chat session
        
        Args:
            session_id: ID of the session to delete
        
        Returns:
            True if successful
        """
        response = self._make_request('DELETE', f'/sessions/{session_id}')
        return response.get('status') == 'success'
    
    def chat_with_session(self, session_id: str, prompt: str) -> str:
        """
        Send a chat message using a session
        
        Args:
            session_id: ID of the chat session
            prompt: User message/prompt
        
        Returns:
            Agent response
        """
        data = {
            'session_id': session_id,
            'prompt': prompt
        }
        
        response = self._make_request('POST', '/chat/session', json=data)
        
        if response.get('status') == 'success':
            return response.get('response')
        raise Exception(response.get('message', 'Chat failed'))
    
    def execute_agent_task(self, agent_id: int, message: str, context: Dict = None) -> Dict:
        """
        Execute a specific task with an agent (for inter-agent communication)
        
        Args:
            agent_id: ID of the agent
            message: Task message
            context: Optional context dictionary
        
        Returns:
            Execution result dictionary
        """
        data = {
            'agent_id': agent_id,
            'message': message,
            'context': context or {}
        }
        
        response = self._make_request('POST', f'/agents/{agent_id}/execute', json=data)
        return response
    
    def reload_agents(self) -> Dict:
        """
        Reload all agents (useful after configuration changes)
        
        Returns:
            Dictionary with loaded and failed agents
        """
        response = self._make_request('POST', '/agents/reload')
        if response.get('status') == 'success':
            return {
                'loaded': response.get('loaded', []),
                'failed': response.get('failed', [])
            }
        raise Exception(response.get('message', 'Failed to reload agents'))
    
    def cleanup_idle(self, idle_minutes: int = 30) -> bool:
        """
        Cleanup idle agents and sessions
        
        Args:
            idle_minutes: Minutes of inactivity before cleanup
        
        Returns:
            True if successful
        """
        data = {'idle_minutes': idle_minutes}
        response = self._make_request('POST', '/cleanup', json=data)
        return response.get('status') == 'success'


class AgentAPIAdapter:
    """
    Adapter class to make the API client compatible with existing code
    This mimics the interface of the original GeneralAgent class
    """
    
    def __init__(self, agent_id: int, client: AgentAPIClient = None):
        """
        Initialize the adapter for a specific agent
        
        Args:
            agent_id: ID of the agent
            client: AgentAPIClient instance (creates new if not provided)
        """
        self.agent_id = agent_id
        self.client = client or AgentAPIClient()
        self.chat_history = []
        
        # Load agent info
        try:
            info = self.client.get_agent_info(agent_id)
            self.AGENT_NAME = info.get('name', f'Agent {agent_id}')
        except:
            self.AGENT_NAME = f'Agent {agent_id}'
    
    def initialize_chat_history(self, chat_hist: List[Dict]):
        """Initialize chat history (compatible with existing code)"""
        self.chat_history = chat_hist
    
    def get_chat_history(self) -> List[Dict]:
        """Get current chat history"""
        return self.chat_history
    
    def clear_chat_history(self):
        """Clear chat history"""
        self.chat_history = []
    
    def run(self, input_prompt: str, use_smart_render=False, user_id=None) -> str:
        """
        Run the agent with a prompt (compatible with existing code)
        
        Args:
            input_prompt: User message/prompt
        
        Returns:
            Agent response
        """
        logger.info(f"AGENT API CLIENT: Request to run agent {self.agent_id} user id {user_id}")

        result = self.client.chat(
            self.agent_id,
            input_prompt,
            self.chat_history,
            use_smart_render=use_smart_render,
            user_id=user_id
        )
        
        # Update chat history
        self.chat_history = result.get('chat_history', [])
        
        return result.get('response', '')
    
    def handle_agent_request(self, message: str, context: Dict = None) -> Dict:
        """
        Handle inter-agent request (compatible with existing code)
        
        Args:
            message: Request message
            context: Optional context
        
        Returns:
            Response dictionary
        """
        return self.client.execute_agent_task(
            self.agent_id,
            message,
            context
        )
    
    def cleanup(self):
        """Cleanup (no-op for API client)"""
        pass


def create_agent_client(use_api: bool = True, api_url: str = None) -> Any:
    """
    Factory function to create either an API client or local agent
    
    Args:
        use_api: If True, use API client; if False, use local GeneralAgent
        api_url: Optional API URL (uses default if not provided)
    
    Returns:
        Either AgentAPIClient or GeneralAgent class
    """
    if use_api:
        return AgentAPIClient(api_url)
    else:
        # Import and return the local GeneralAgent class
        from GeneralAgent import GeneralAgent
        return GeneralAgent

