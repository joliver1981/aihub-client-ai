"""
email_receive_client.py - Client library for receiving emails via Cloud API

This module is used by the on-prem application to:
- Get TenantId from Cloud API (used as email suffix)
- Poll Cloud API for new emails
- Fetch full message content
- Acknowledge processed emails

Email format: {prefix}.{TenantId}@{domain}
Example: sales.42@mail.yourdomain.com -> TenantId = 42

Usage:
    from email_receive_client import EmailReceiveClient
    
    client = EmailReceiveClient()
    
    # Get your tenant ID (used as email suffix)
    tenant_id = client.get_tenant_id()
    
    # Poll for new emails
    emails = client.poll_for_emails()
    
    # Process each email
    for email in emails:
        content = client.get_message_content(email['message_key'])
        # ... process email ...
        client.acknowledge_emails([email['event_id']])

Environment Variables:
    AI_HUB_API_URL - Cloud API URL (e.g., https://api.aihub.example.com)
    API_KEY - License key for authentication
"""

import os
import logging
import requests
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class EmailReceiveClient:
    """Client for receiving emails via Cloud API polling."""
    
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        """Singleton pattern for shared client instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, api_url: str = None, api_key: str = None):
        """
        Initialize the email receive client.
        
        Args:
            api_url: Cloud API URL (defaults to AI_HUB_API_URL env var)
            api_key: License key (defaults to API_KEY env var)
        """
        if self._initialized:
            return
            
        self.api_url = (api_url or os.environ.get('AI_HUB_API_URL', '')).rstrip('/')
        self.api_key = api_key or os.environ.get('API_KEY', '')
        self.timeout = 30
        
        # Cache for tenant info
        self._tenant_id = None
        self._email_domain = None
        self._initialized = True
    
    def _get_headers(self) -> Dict[str, str]:
        """Get request headers with authentication."""
        return {
            'X-API-Key': self.api_key,
            'Content-Type': 'application/json'
        }
    
    def _make_request(self, method: str, endpoint: str, 
                      json_data: Dict = None, params: Dict = None) -> Dict:
        """
        Make an authenticated request to the Cloud API.
        """
        if not self.api_url:
            logger.warning("AI_HUB_API_URL not configured")
            return {'success': False, 'error': 'AI_HUB_API_URL not configured'}
        
        if not self.api_key:
            logger.warning("API_KEY not configured")
            return {'success': False, 'error': 'API_KEY not configured'}
        
        url = f"{self.api_url}{endpoint}"
        
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self._get_headers(),
                json=json_data,
                params=params,
                timeout=self.timeout
            )
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error: {e.response.status_code} - {e.response.text}")
            try:
                return e.response.json()
            except:
                return {'success': False, 'error': f"HTTP {e.response.status_code}"}
        except requests.exceptions.Timeout:
            logger.error("Request timeout")
            return {'success': False, 'error': 'Request timeout'}
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error: {e}")
            return {'success': False, 'error': 'Connection error - Cloud API may be unavailable'}
        except Exception as e:
            logger.error(f"Request error: {e}")
            return {'success': False, 'error': str(e)}
    
    # =========================================================================
    # Tenant ID (Email Suffix)
    # =========================================================================
    
    def get_tenant_id(self) -> Optional[int]:
        """
        Get the TenantId for this API key.
        
        The TenantId is used as the email suffix:
        Email format: {prefix}.{TenantId}@{domain}
        
        Caches the result since it doesn't change.
        
        Returns:
            The TenantId (integer) or None on failure
        """
        if self._tenant_id:
            return self._tenant_id
        
        result = self._make_request('GET', '/api/email/tenant-id')
        
        if result.get('success'):
            self._tenant_id = result.get('tenant_id')
            self._email_domain = result.get('domain', '')
            return self._tenant_id
        else:
            logger.error(f"Failed to get tenant ID: {result.get('error')}")
            return None
    
    def get_email_domain(self) -> str:
        """
        Get the email domain.
        
        Returns:
            The email domain (e.g., "mail.yourdomain.com")
        """
        if self._email_domain:
            return self._email_domain
        
        # Fetch tenant ID also gets domain
        self.get_tenant_id()
        return self._email_domain or ''
    
    def build_email_address(self, prefix: str) -> str:
        """
        Build a full email address from a prefix.
        
        Args:
            prefix: The email prefix (e.g., "sales")
            
        Returns:
            Full email address (e.g., "sales.42@mail.domain.com")
        """
        tenant_id = self.get_tenant_id()
        domain = self.get_email_domain()
        
        if not tenant_id or not domain:
            return ''
        
        return f"{prefix}.{tenant_id}@{domain}"
    
    def get_email_suffix(self) -> str:
        """
        Get the full email suffix including domain.
        
        Returns:
            Suffix string (e.g., ".42@mail.domain.com")
        """
        tenant_id = self.get_tenant_id()
        domain = self.get_email_domain()
        
        if not tenant_id or not domain:
            return ''
        
        return f".{tenant_id}@{domain}"
    
    # =========================================================================
    # Polling Methods
    # =========================================================================
    
    def poll_for_emails(self, limit: int = 50, include_counts: bool = False) -> List[Dict]:
        """
        Poll for new/pending emails.
        
        Args:
            limit: Maximum number of emails to return (max 100)
            include_counts: Include total email counts in response
            
        Returns:
            List of email event dictionaries
        """
        params = {
            'limit': min(limit, 100),
            'include_counts': 'true' if include_counts else 'false'
        }
        
        result = self._make_request('GET', '/api/email/poll', params=params)
        
        if result.get('success'):
            return result.get('emails', [])
        else:
            logger.error(f"Error polling for emails: {result.get('error')}")
            return []
    
    def poll_with_metadata(self, limit: int = 50) -> Dict:
        """
        Poll for emails with additional metadata.
        
        Args:
            limit: Maximum number of emails to return
            
        Returns:
            Full response including emails, counts, and has_more flag
        """
        params = {
            'limit': min(limit, 100),
            'include_counts': 'true'
        }
        
        result = self._make_request('GET', '/api/email/poll', params=params)
        
        if result.get('success'):
            return {
                'emails': result.get('emails', []),
                'count': result.get('count', 0),
                'has_more': result.get('has_more', False),
                'pending': result.get('counts', {}).get('pending', 0),
                'total': result.get('counts', {}).get('total', 0)
            }
        else:
            logger.error(f"Error polling for emails: {result.get('error')}")
            return {
                'emails': [],
                'count': 0,
                'has_more': False,
                'pending': 0,
                'total': 0,
                'error': result.get('error')
            }
    
    def get_email_counts(self) -> Dict[str, int]:
        """
        Get email counts without fetching emails.
        
        Returns:
            Dict with 'pending' and 'total' counts
        """
        result = self._make_request('GET', '/api/email/counts')
        
        if result.get('success'):
            return {
                'pending': result.get('pending', 0),
                'total': result.get('total', 0)
            }
        else:
            return {'pending': 0, 'total': 0}
    
    def acknowledge_emails(self, event_ids: List[int]) -> int:
        """
        Acknowledge that emails have been processed.
        
        Call this after successfully processing emails so they
        won't be returned in future poll requests.
        
        Args:
            event_ids: List of event IDs to acknowledge
            
        Returns:
            Number of emails acknowledged
        """
        if not event_ids:
            return 0
        
        result = self._make_request('POST', '/api/email/acknowledge', 
                                     json_data={'event_ids': event_ids})
        
        if result.get('success'):
            return result.get('acknowledged', 0)
        else:
            logger.error(f"Error acknowledging emails: {result.get('error')}")
            return 0
    
    # =========================================================================
    # Message Content Methods
    # =========================================================================
    
    def get_message_content(self, message_key: str, 
                            storage_url: str = None) -> Optional[Dict]:
        """
        Get full message content.
        
        Args:
            message_key: The message key from the poll response
            storage_url: Optional storage URL for direct fetch
            
        Returns:
            Message content dict or None on failure
        """
        params = {}
        if storage_url:
            params['storage_url'] = storage_url
        
        result = self._make_request('GET', f'/api/email/message/{message_key}', 
                                     params=params)
        
        if result.get('success'):
            return result.get('message')
        else:
            logger.error(f"Error fetching message: {result.get('error')}")
            return None
    
    def get_attachment(self, attachment_url: str) -> Optional[bytes]:
        """
        Get attachment binary content.
        
        Args:
            attachment_url: The attachment URL from message content
            
        Returns:
            Binary attachment content or None on failure
        """
        if not self.api_url or not self.api_key:
            return None
        
        try:
            response = requests.get(
                f"{self.api_url}/api/email/attachment",
                headers={'X-API-Key': self.api_key},
                params={'url': attachment_url},
                timeout=60
            )
            
            response.raise_for_status()
            return response.content
            
        except Exception as e:
            logger.error(f"Error fetching attachment: {e}")
            return None
    
    # =========================================================================
    # Health Check
    # =========================================================================
    
    def health_check(self) -> Dict:
        """Check if the email service is healthy."""
        result = self._make_request('GET', '/api/email/health')
        return result
    
    def is_configured(self) -> bool:
        """Check if the client is properly configured."""
        return bool(self.api_url and self.api_key)


# =============================================================================
# Email Polling Service (for background processing)
# =============================================================================

class EmailPollingService:
    """
    Service for continuous email polling with callback processing.
    
    Usage:
        def process_email(email, content):
            print(f"Received: {email['subject']}")
            return True  # Return True to acknowledge
        
        service = EmailPollingService(callback=process_email)
        service.poll_once()  # Or run in a loop
    """
    
    def __init__(self, callback, api_url: str = None, api_key: str = None,
                 auto_acknowledge: bool = True):
        """
        Initialize the polling service.
        
        Args:
            callback: Function to call for each email (email, content) -> bool
            api_url: Cloud API URL
            api_key: License key
            auto_acknowledge: If True, automatically acknowledge processed emails
        """
        self.client = EmailReceiveClient(api_url, api_key)
        self.callback = callback
        self.auto_acknowledge = auto_acknowledge
        self.last_poll = None
        self.emails_processed = 0
    
    def poll_once(self, limit: int = 50, fetch_content: bool = True) -> int:
        """
        Poll for emails once and process them.
        
        Args:
            limit: Maximum emails to fetch
            fetch_content: If True, fetch full content before calling callback
            
        Returns:
            Number of emails successfully processed
        """
        self.last_poll = datetime.now()
        
        emails = self.client.poll_for_emails(limit=limit)
        
        if not emails:
            return 0
        
        processed_ids = []
        
        for email in emails:
            try:
                content = None
                if fetch_content:
                    content = self.client.get_message_content(
                        email.get('message_key', ''),
                        email.get('storage_url')
                    )
                
                success = self.callback(email, content)
                
                if success and self.auto_acknowledge:
                    processed_ids.append(email['event_id'])
                
            except Exception as e:
                logger.error(f"Error processing email {email.get('event_id')}: {e}")
        
        if processed_ids:
            self.client.acknowledge_emails(processed_ids)
        
        self.emails_processed += len(processed_ids)
        return len(processed_ids)
    
    def get_stats(self) -> Dict:
        """Get polling statistics."""
        return {
            'last_poll': self.last_poll.isoformat() if self.last_poll else None,
            'emails_processed': self.emails_processed,
            'pending': self.client.get_email_counts().get('pending', 0)
        }


# =============================================================================
# Convenience Functions (Module-level)
# =============================================================================

_default_client = None


def get_client() -> EmailReceiveClient:
    """Get the default email receive client (singleton)."""
    global _default_client
    if _default_client is None:
        _default_client = EmailReceiveClient()
    return _default_client


def get_tenant_id() -> Optional[int]:
    """Get tenant ID using default client."""
    return get_client().get_tenant_id()


def get_email_suffix() -> str:
    """Get email suffix using default client."""
    return get_client().get_email_suffix()


def poll_for_emails(limit: int = 50) -> List[Dict]:
    """Poll for new emails using default client."""
    return get_client().poll_for_emails(limit)


def get_message_content(message_key: str, storage_url: str = None) -> Optional[Dict]:
    """Get message content using default client."""
    return get_client().get_message_content(message_key, storage_url)


def acknowledge_emails(event_ids: List[int]) -> int:
    """Acknowledge emails using default client."""
    return get_client().acknowledge_emails(event_ids)


def build_email_address(prefix: str) -> str:
    """Build email address using default client."""
    return get_client().build_email_address(prefix)
