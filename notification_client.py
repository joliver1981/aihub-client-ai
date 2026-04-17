"""
notification_client.py - AI Hub Notification Client

This module provides notification functions for the AI Hub application.
It routes all notifications through the Cloud API for tier-based limits and usage tracking.

ARCHITECTURE:
- This client looks up agent email addresses from the LOCAL database
- It sends all required info (from_address, from_name, provider) to the Cloud API
- The Cloud API does NOT read from application tables - it just sends via Azure/Mailgun

INSTALLATION:
1. Copy this file to your AI Hub application root
2. Set environment variables:
   - AI_HUB_API_URL: Base URL of Cloud API (e.g., https://api.yourdomain.com)
   - API_KEY: Your license key for authentication
   
USAGE:
Replace direct Azure SDK calls with these functions:
    from notification_client import send_email_notification, sms_text_message_alert

These are drop-in replacements for the existing AppUtils functions.
"""

import os
import requests
import base64
from typing import List, Dict, Any, Optional
import logging
from logging.handlers import WatchedFileHandler

from CommonUtils import get_db_connection, rotate_logs_on_startup, get_log_path


# =============================================================================
# LOGGING SETUP
# =============================================================================

def setup_logging():
    """Configure logging for notification client"""
    logger = logging.getLogger("NotificationClient")
    log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
    log_level = getattr(logging, log_level_name, logging.DEBUG)
    logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    log_file = os.getenv('NOTIFICATION_CLIENT_LOG', get_log_path('notification_client_log.txt'))
    handler = WatchedFileHandler(filename=log_file, encoding='utf-8')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger

# Initialize logging
_log_file = os.getenv('NOTIFICATION_CLIENT_LOG', get_log_path('notification_client_log.txt'))
rotate_logs_on_startup(_log_file)
logger = setup_logging()


# =============================================================================
# CONFIGURATION
# =============================================================================

def get_api_url() -> str:
    """Get the Cloud API base URL"""
    return os.environ.get('AI_HUB_API_URL', 'https://api.aihub.everiai.ai')


def get_api_key() -> str:
    """Get the API/license key for authentication"""
    return os.environ.get('API_KEY', '')


# =============================================================================
# LOCAL DATABASE LOOKUP (Agent Email Addresses)
# =============================================================================

def get_agent_email_from_local_db(agent_id: int) -> Optional[Dict[str, str]]:
    """
    Look up agent-specific email address from the LOCAL application database.
    
    This queries the AgentEmailAddresses table in the local database,
    NOT the Cloud API database.
    
    Args:
        agent_id: The agent's ID
        
    Returns:
        Dict with 'email_address' and 'from_name', or None if not configured
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context for row-level security
        cursor.execute("EXEC tenant.sp_setTenantContext ?", (os.getenv('API_KEY'),))
        
        cursor.execute("""
            SELECT email_address, from_name 
            FROM AgentEmailAddresses 
            WHERE agent_id = ? AND is_active = 1
        """, (agent_id,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if result:
            return {
                'email_address': result[0],
                'from_name': result[1] or 'AI Agent'
            }
        return None
        
    except Exception as e:
        logger.warning(f"Could not look up agent email (agent_id={agent_id}): {e}")
        return None


# =============================================================================
# API CLIENT CLASS
# =============================================================================

class NotificationClient:
    """Client for Cloud API notification endpoints"""
    
    def __init__(self, api_url: str = None, api_key: str = None):
        self.api_url = api_url or get_api_url()
        self.api_key = api_key or get_api_key()
    
    def _make_request(
        self, 
        method: str, 
        endpoint: str, 
        json_data: Dict = None,
        params: Dict = None
    ) -> Dict[str, Any]:
        """Make an authenticated request to the Cloud API"""
        url = f"{self.api_url}{endpoint}"

        # Authenticate via header (not query string)
        headers = {
            'X-API-Key': self.api_key,
            'Content-Type': 'application/json'
        }

        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, params=params, timeout=30)
            elif method == 'POST':
                response = requests.post(url, headers=headers, json=json_data, params=params, timeout=60)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, params=params, timeout=30)
            else:
                return {'success': False, 'error': f'Unknown method: {method}'}
            
            # Parse response
            result = response.json()
            
            # Log if blocked by limit
            if result.get('blocked_by_limit'):
                logger.warning(f"Notification blocked by limit: {result.get('message')}")
            
            return result
            
        except requests.exceptions.Timeout:
            logger.error(f"Request timeout: {endpoint}")
            return {'success': False, 'error': 'Request timeout'}
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            return {'success': False, 'error': str(e)}
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return {'success': False, 'error': str(e)}
    
    # =========================================================================
    # EMAIL
    # =========================================================================
    
    def send_email(
        self,
        to: List[str],
        subject: str,
        body: str,
        html_body: str = None,
        from_address: str = None,
        from_name: str = None,
        provider: str = 'azure',
        agent_id: int = None,
        agent_name: str = None,
        workflow_id: int = None,
        execution_id: str = None,
        attachments: List[Dict] = None
    ) -> Dict[str, Any]:
        """
        Send an email via Cloud API.
        
        Args:
            to: List of recipient email addresses
            subject: Email subject
            body: Plain text body
            html_body: Optional HTML body
            from_address: Sender email (None = use system default)
            from_name: Sender display name
            provider: 'azure' or 'mailgun'
            agent_id: For logging
            agent_name: For logging
            workflow_id: For logging
            execution_id: For logging
            attachments: List of dicts with filename, content (bytes), content_type
            
        Returns:
            Dict with success, message, usage info
        """
        data = {
            'to': to,
            'subject': subject,
            'body': body,
            'provider': provider
        }
        
        if html_body:
            data['html_body'] = html_body
        if from_address:
            data['from_address'] = from_address
        if from_name:
            data['from_name'] = from_name
        if agent_id:
            data['agent_id'] = agent_id
        if agent_name:
            data['agent_name'] = agent_name
        if workflow_id:
            data['workflow_id'] = workflow_id
        if execution_id:
            data['execution_id'] = execution_id
        
        # Encode attachments as base64
        if attachments:
            data['attachments'] = []
            for att in attachments:
                content = att.get('content', b'')
                if isinstance(content, bytes):
                    content = base64.b64encode(content).decode('utf-8')
                data['attachments'].append({
                    'filename': att.get('filename', 'attachment'),
                    'content': content,
                    'content_type': att.get('content_type', 'application/octet-stream')
                })
        
        return self._make_request('POST', '/api/notifications/email', json_data=data)
    
    def check_email_limit(self) -> Dict[str, Any]:
        """Check email limits without sending"""
        return self._make_request('GET', '/api/notifications/email/check')
    
    # =========================================================================
    # SMS
    # =========================================================================
    
    def send_sms(
        self,
        to: str,
        message: str,
        agent_id: int = None,
        agent_name: str = None,
        workflow_id: int = None,
        execution_id: str = None
    ) -> Dict[str, Any]:
        """Send an SMS via Cloud API"""
        data = {
            'to': to,
            'message': message
        }
        
        if agent_id:
            data['agent_id'] = agent_id
        if agent_name:
            data['agent_name'] = agent_name
        if workflow_id:
            data['workflow_id'] = workflow_id
        if execution_id:
            data['execution_id'] = execution_id
        
        return self._make_request('POST', '/api/notifications/sms', json_data=data)
    
    def check_sms_limit(self) -> Dict[str, Any]:
        """Check SMS limits without sending"""
        return self._make_request('GET', '/api/notifications/sms/check')
    
    # =========================================================================
    # PHONE
    # =========================================================================
    
    def send_phone_call(
        self,
        to: str,
        message: str,
        voice_index: int = 0,
        agent_id: int = None,
        agent_name: str = None,
        workflow_id: int = None,
        execution_id: str = None
    ) -> Dict[str, Any]:
        """Initiate a phone call via Cloud API"""
        data = {
            'to': to,
            'message': message,
            'voice_index': voice_index
        }
        
        if agent_id:
            data['agent_id'] = agent_id
        if agent_name:
            data['agent_name'] = agent_name
        if workflow_id:
            data['workflow_id'] = workflow_id
        if execution_id:
            data['execution_id'] = execution_id
        
        return self._make_request('POST', '/api/notifications/phone', json_data=data)
    
    def check_phone_limit(self) -> Dict[str, Any]:
        """Check phone call limits without calling"""
        return self._make_request('GET', '/api/notifications/phone/check')
    
    # =========================================================================
    # USAGE
    # =========================================================================
    
    def get_usage(self) -> Dict[str, Any]:
        """Get notification usage statistics"""
        return self._make_request('GET', '/api/notifications/usage')
    
    def get_config(self) -> Dict[str, Any]:
        """Get notification configuration (default email, limits)"""
        return self._make_request('GET', '/api/notifications/config')


# =============================================================================
# DROP-IN REPLACEMENT FUNCTIONS
# =============================================================================
# These match the signatures of the existing functions in AppUtils.py
# so they can be imported as replacements.

# Global client instance
_client = None

def _get_client() -> NotificationClient:
    """Get or create the global client instance"""
    global _client
    if _client is None:
        _client = NotificationClient()
    return _client


def send_email_notification(
    to: List[str],
    subject: str,
    body: str,
    html_body: str = None,
    agent_id: int = None,
    agent_name: str = None,
    attachments: List[Dict] = None
) -> Dict[str, Any]:
    """
    Send an email notification. Drop-in replacement for AppUtils.send_email_notification.
    
    Looks up agent email from LOCAL database if agent_id is provided.
    Falls back to system default email if no agent email configured.
    """
    from_address = None
    from_name = None
    provider = 'azure'  # Default to Azure (system email)
    
    # If agent_id provided, look up agent-specific email from LOCAL database
    if agent_id:
        agent_email = get_agent_email_from_local_db(agent_id)
        if agent_email:
            from_address = agent_email['email_address']
            from_name = agent_email['from_name']
            provider = 'mailgun'  # Agent emails use Mailgun
    
    return _get_client().send_email(
        to=to,
        subject=subject,
        body=body,
        html_body=html_body,
        from_address=from_address,
        from_name=from_name,
        provider=provider,
        agent_id=agent_id,
        agent_name=agent_name,
        attachments=attachments
    )


def send_email(
    to_email: str,
    subject: str,
    email_text: str,
    agent_id: int = None
) -> Dict[str, Any]:
    """
    Simple email sending function. Drop-in replacement for AppUtils.send_email.
    """
    return send_email_notification(
        to=[to_email],
        subject=subject,
        body=email_text,
        agent_id=agent_id
    )


def sms_text_message_alert(
    to: str,
    message: str,
    agent_id: int = None,
    agent_name: str = None
) -> Dict[str, Any]:
    """
    Send an SMS alert. Drop-in replacement for AppUtils.sms_text_message_alert.
    """
    return _get_client().send_sms(
        to=to,
        message=message,
        agent_id=agent_id,
        agent_name=agent_name
    )


def aihub_phone_call_alert(
    to: str,
    message: str,
    voice_index: int = 0,
    agent_id: int = None,
    agent_name: str = None
) -> Dict[str, Any]:
    """
    Make a phone call alert. Drop-in replacement for AppUtils.aihub_phone_call_alert.
    """
    return _get_client().send_phone_call(
        to=to,
        message=message,
        voice_index=voice_index,
        agent_id=agent_id,
        agent_name=agent_name
    )


# =============================================================================
# WORKFLOW-AWARE FUNCTIONS
# =============================================================================
# These include workflow_id and execution_id for better tracking in logs.

def send_workflow_email(
    to: List[str],
    subject: str,
    body: str,
    html_body: str = None,
    agent_id: int = None,
    agent_name: str = None,
    workflow_id: int = None,
    execution_id: str = None,
    attachments: List[Dict] = None
) -> Dict[str, Any]:
    """
    Send email from a workflow node. Includes workflow context for tracking.
    """
    from_address = None
    from_name = None
    provider = 'azure'
    
    if agent_id:
        agent_email = get_agent_email_from_local_db(agent_id)
        if agent_email:
            from_address = agent_email['email_address']
            from_name = agent_email['from_name']
            provider = 'mailgun'
    
    return _get_client().send_email(
        to=to,
        subject=subject,
        body=body,
        html_body=html_body,
        from_address=from_address,
        from_name=from_name,
        provider=provider,
        agent_id=agent_id,
        agent_name=agent_name,
        workflow_id=workflow_id,
        execution_id=execution_id,
        attachments=attachments
    )


def send_workflow_sms(
    to: str,
    message: str,
    agent_id: int = None,
    agent_name: str = None,
    workflow_id: int = None,
    execution_id: str = None
) -> Dict[str, Any]:
    """
    Send SMS from a workflow node. Includes workflow context for tracking.
    """
    return _get_client().send_sms(
        to=to,
        message=message,
        agent_id=agent_id,
        agent_name=agent_name,
        workflow_id=workflow_id,
        execution_id=execution_id
    )


def send_workflow_phone_call(
    to: str,
    message: str,
    voice_index: int = 0,
    agent_id: int = None,
    agent_name: str = None,
    workflow_id: int = None,
    execution_id: str = None
) -> Dict[str, Any]:
    """
    Make phone call from a workflow node. Includes workflow context for tracking.
    """
    return _get_client().send_phone_call(
        to=to,
        message=message,
        voice_index=voice_index,
        agent_id=agent_id,
        agent_name=agent_name,
        workflow_id=workflow_id,
        execution_id=execution_id
    )


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def get_notification_usage() -> Dict[str, Any]:
    """Get current notification usage statistics"""
    return _get_client().get_usage()


def check_email_limit() -> Dict[str, Any]:
    """Check if email can be sent (limit check)"""
    return _get_client().check_email_limit()


def check_sms_limit() -> Dict[str, Any]:
    """Check if SMS can be sent (limit check)"""
    return _get_client().check_sms_limit()


def check_phone_limit() -> Dict[str, Any]:
    """Check if phone call can be made (limit check)"""
    return _get_client().check_phone_limit()
