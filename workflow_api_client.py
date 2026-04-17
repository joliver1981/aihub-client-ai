"""
Workflow API Client
Client for communicating with the Workflow Executor Service
"""

import os
import logging
import requests
from typing import Optional, Dict
import time
from CommonUtils import get_executor_api_base_url


logger = logging.getLogger(__name__)


class WorkflowServiceError(Exception):
    """Exception raised when workflow service communication fails"""
    def __init__(self, message: str, status_code: int = None):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


class WorkflowAPIClient:
    """Client for communicating with the Workflow Executor Service"""
    
    def __init__(self, base_url: str = get_executor_api_base_url(), timeout: int = 60):
        self.base_url = base_url or get_executor_api_base_url()
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })
    
    def _request(self, method: str, endpoint: str, data: dict = None, retries: int = 2) -> dict:
        """Make HTTP request with retry logic"""
        url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        
        for attempt in range(retries + 1):
            try:
                response = self._session.request(
                    method=method,
                    url=url,
                    json=data,
                    timeout=self.timeout
                )
                
                result = response.json() if response.content else {}
                
                if response.status_code >= 400:
                    raise WorkflowServiceError(
                        result.get('message', f"HTTP {response.status_code}"),
                        response.status_code
                    )
                
                return result
                
            except requests.exceptions.ConnectionError as e:
                if attempt < retries:
                    time.sleep(1)
                    continue
                raise WorkflowServiceError(f"Connection failed: {e}", 503)
                
            except requests.exceptions.Timeout:
                raise WorkflowServiceError("Request timed out", 504)
                
            except WorkflowServiceError:
                raise
                
            except Exception as e:
                raise WorkflowServiceError(f"Unexpected error: {e}", 500)
        
        raise WorkflowServiceError("Max retries exceeded", 503)
    
    def is_available(self) -> bool:
        """Check if service is available"""
        try:
            result = self._request('GET', '/health', retries=0)
            return result.get('status') == 'healthy'
        except:
            return False
    
    def start_workflow(self, workflow_id: int, initiator: str = 'api', workflow_data: dict = None) -> dict:
        """Start a workflow execution"""
        data = {'workflow_id': workflow_id, 'initiator': initiator}
        if workflow_data:
            data['workflow_data'] = workflow_data
        return self._request('POST', '/api/workflow/run', data)
    
    def pause_workflow(self, execution_id: str) -> dict:
        """Pause a workflow"""
        return self._request('POST', f'/api/workflow/executions/{execution_id}/pause')
    
    def resume_workflow(self, execution_id: str) -> dict:
        """Resume a workflow"""
        return self._request('POST', f'/api/workflow/executions/{execution_id}/resume')
    
    def cancel_workflow(self, execution_id: str) -> dict:
        """Cancel a workflow"""
        return self._request('POST', f'/api/workflow/executions/{execution_id}/cancel')
    
    def get_status(self, execution_id: str) -> dict:
        """Get workflow status"""
        return self._request('GET', f'/api/workflow/executions/{execution_id}/status')
    
    def get_active(self) -> dict:
        """Get active workflows"""
        return self._request('GET', '/api/workflow/executions/active')
    
    def log_event(self, execution_id: str, message: str, level: str = 'info', 
                  node_id: str = None, details: dict = None) -> dict:
        """Log workflow event"""
        data = {'execution_id': execution_id, 'message': message, 'level': level}
        if node_id:
            data['node_id'] = node_id
        if details:
            data['details'] = details
        return self._request('POST', '/api/workflow/log', data)


def get_workflow_executor_url() -> str:
    """Get workflow executor URL from environment"""
    return get_executor_api_base_url()
