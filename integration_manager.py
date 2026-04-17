# integration_manager.py
"""
Universal Integration Manager
==============================

Manages integration templates, user integrations, and API execution.
Works with local secrets for credential storage.

Key Features:
- Load and manage pre-built integration templates (from individual files)
- Create and manage user integration instances  
- Execute API operations with automatic auth handling
- OAuth2 token management and refresh
- Webhook handling
- Comprehensive logging

Template Loading:
    Templates are loaded from individual JSON files in the integrations folder:
    /integrations/
        /builtin/           # Shipped templates (read-only)
            quickbooks_online.json
            shopify.json
            ...
        /custom/            # User-created via files (optional)
            my_custom_api.json
    
    User-created templates via UI are stored in the database.

Usage:
    from integration_manager import IntegrationManager
    
    manager = IntegrationManager()
    
    # List available templates
    templates = manager.get_available_templates()
    
    # Create an integration
    integration_id = manager.create_integration(
        template_key='quickbooks_online',
        integration_name='My QuickBooks',
        credentials={'access_token': 'xxx'},
        instance_config={'realmId': '123456'}
    )
    
    # Execute an operation
    result = manager.execute_operation(
        integration_id=integration_id,
        operation_key='get_invoices',
        parameters={'status': 'Unpaid'}
    )
"""

import json
import logging
import os
import re
import hashlib
import hmac
import time
import uuid
import requests
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from urllib.parse import quote, urlencode, urljoin
import pyodbc

# Local imports
from local_secrets import (
    get_secrets_manager, 
    get_local_secret, 
    set_local_secret,
    has_local_secret
)
from CommonUtils import get_db_connection

# Import the template loader
from integration_template_loader import (
    get_template_loader,
    load_templates as load_templates_from_files,
    get_template as get_template_from_loader,
    get_categories as get_categories_from_loader,
    reload_templates
)

from logging.handlers import WatchedFileHandler

from CommonUtils import rotate_logs_on_startup, get_log_path


# =============================================================================
# LOGGING SETUP
# =============================================================================

def setup_logging():
    """Configure logging for notification client"""
    logger = logging.getLogger("IntegrationManager")
    log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
    log_level = getattr(logging, log_level_name, logging.DEBUG)
    logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    log_file = os.getenv('INTEGRATIONS_MGR_LOG', get_log_path('integrations_manager_log.txt'))
    handler = WatchedFileHandler(filename=log_file, encoding='utf-8')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger

# Initialize logging
_log_file = os.getenv('INTEGRATIONS_MGR_LOG', get_log_path('integrations_manager_log.txt'))
rotate_logs_on_startup(_log_file)
logger = setup_logging()


# =============================================================================
# Constants
# =============================================================================

SECRET_PREFIX = 'INT_'  # Prefix for integration secrets


# =============================================================================
# Secret Name Helpers
# =============================================================================

def get_integration_secret_name(integration_id: int, secret_type: str) -> str:
    """Generate secret name for an integration credential."""
    return f"{SECRET_PREFIX}{integration_id}_{secret_type.upper()}"


def create_secret_reference(secret_name: str) -> str:
    """Create a reference string to store in database."""
    return f"{{{{LOCAL_SECRET:{secret_name}}}}}"


def resolve_secret_reference(reference: str) -> str:
    """Resolve a secret reference to its actual value."""
    if not reference:
        return ''
    
    # Check if it's a reference
    match = re.match(r'\{\{LOCAL_SECRET:([A-Za-z0-9_]+)\}\}', reference)
    if match:
        secret_name = match.group(1)
        return get_local_secret(secret_name, '')
    
    # Not a reference, return as-is
    return reference


# =============================================================================
# Template Manager
# =============================================================================

class TemplateManager:
    """
    Manages integration templates (built-in and custom).
    
    Templates are loaded from:
    1. Individual JSON files in /integrations/builtin/ (shipped templates)
    2. Individual JSON files in /integrations/custom/ (user file templates)
    3. Database IntegrationTemplates table (user UI-created templates)
    
    The file-based approach allows:
    - Easy maintenance of individual templates
    - Developers can work on templates independently  
    - New templates added by simply dropping a file
    - Version control friendly (clear git diffs)
    """
    
    @classmethod
    def load_templates(cls, force_reload: bool = False) -> Dict[str, Any]:
        """
        Load templates from files and database.
        
        Args:
            force_reload: Force reload from disk, bypassing cache
            
        Returns:
            Dict mapping template_key to template definition
        """
        if force_reload:
            return reload_templates()
        return load_templates_from_files()
    
    @classmethod
    def get_all_templates(cls) -> List[Dict]:
        """Get all available templates as a list."""
        templates_dict = cls.load_templates()
        return list(templates_dict.values())
    
    @classmethod
    def get_template(cls, template_key: str) -> Optional[Dict]:
        """Get a specific template by key."""
        return get_template_from_loader(template_key)
    
    @classmethod
    def get_categories(cls) -> List[Dict]:
        """Get all template categories with counts."""
        return get_categories_from_loader()
    
    @classmethod
    def get_templates_by_category(cls, category: str) -> List[Dict]:
        """Get all templates in a specific category."""
        return get_template_loader().get_templates_by_category(category)
    
    @classmethod
    def save_custom_template(cls, template: Dict, save_to_file: bool = False) -> str:
        """
        Save a custom template.
        
        Args:
            template: Template definition
            save_to_file: If True, save as file; if False, save to database
            
        Returns:
            The template_key
        """
        return get_template_loader().save_custom_template(template, save_to_file)
    
    @classmethod
    def delete_custom_template(cls, template_key: str) -> bool:
        """Delete a custom template (cannot delete builtin)."""
        return get_template_loader().delete_custom_template(template_key)
    
    @classmethod  
    def reload(cls) -> Dict[str, Any]:
        """Force reload all templates from disk."""
        return reload_templates()
    
    @classmethod
    def get_storage_info(cls) -> Dict:
        """Get information about template storage."""
        return get_template_loader().get_storage_info()


# =============================================================================
# Auth Handlers
# =============================================================================

class AuthHandler(ABC):
    """Base class for authentication handlers."""
    
    @abstractmethod
    def apply_auth(self, request_kwargs: Dict, credentials: Dict, auth_config: Dict) -> Dict:
        """Apply authentication to request kwargs."""
        pass
    
    @abstractmethod
    def needs_refresh(self, integration: Dict) -> bool:
        """Check if credentials need refresh."""
        return False
    
    @abstractmethod
    def refresh_credentials(self, integration: Dict) -> Optional[Dict]:
        """Refresh credentials if supported."""
        return None


class ApiKeyAuthHandler(AuthHandler):
    """Handler for API key authentication."""
    
    def apply_auth(self, request_kwargs: Dict, credentials: Dict, auth_config: Dict) -> Dict:
        api_key = credentials.get('api_key', '')
        location = auth_config.get('api_key_location', 'header')

        if location == 'header':
            header_name = auth_config.get('api_key_header', 'X-API-Key')
            prefix = auth_config.get('api_key_prefix', '')

            if 'headers' not in request_kwargs:
                request_kwargs['headers'] = {}
            request_kwargs['headers'][header_name] = f"{prefix}{api_key}"

            # Log masked key info for debugging auth issues
            masked = (api_key[:4] + '***' + api_key[-4:]) if len(api_key) > 10 else ('***' if api_key else '(empty)')
            logger.debug(f"API Key auth applied: header='{header_name}', "
                         f"key_preview={masked}, key_length={len(api_key)}")

        elif location == 'query':
            param_name = auth_config.get('api_key_param', 'api_key')
            if 'params' not in request_kwargs:
                request_kwargs['params'] = {}
            request_kwargs['params'][param_name] = api_key

        return request_kwargs
    
    def needs_refresh(self, integration: Dict) -> bool:
        return False
    
    def refresh_credentials(self, integration: Dict) -> Optional[Dict]:
        return None


class BearerAuthHandler(AuthHandler):
    """Handler for Bearer token authentication."""
    
    def apply_auth(self, request_kwargs: Dict, credentials: Dict, auth_config: Dict) -> Dict:
        # Check auth_config for custom token field name (e.g., Stripe uses 'api_key')
        token_field = auth_config.get('token_field', '')
        if token_field and credentials.get(token_field):
            token = credentials[token_field]
        else:
            token = credentials.get('access_token', '') or credentials.get('bearer_token', '')

        if 'headers' not in request_kwargs:
            request_kwargs['headers'] = {}

        # Support custom token prefix (default: "Bearer ")
        token_prefix = auth_config.get('token_prefix', 'Bearer ')

        # Build the header value
        if token_prefix:
            header_value = f"{token_prefix}{token}"
        else:
            header_value = token

        request_kwargs['headers']['Authorization'] = header_value

        return request_kwargs
    
    def needs_refresh(self, integration: Dict) -> bool:
        return False
    
    def refresh_credentials(self, integration: Dict) -> Optional[Dict]:
        return None


class BasicAuthHandler(AuthHandler):
    """Handler for Basic authentication."""
    
    def apply_auth(self, request_kwargs: Dict, credentials: Dict, auth_config: Dict) -> Dict:
        username = credentials.get('username', '')
        password = credentials.get('password', '')
        
        request_kwargs['auth'] = (username, password)
        return request_kwargs
    
    def needs_refresh(self, integration: Dict) -> bool:
        return False
    
    def refresh_credentials(self, integration: Dict) -> Optional[Dict]:
        return None


class OAuth2AuthHandler(AuthHandler):
    """
    Handler for OAuth 2.0 authentication with token refresh.
    
    Supports:
    - Standard refresh_token grant type
    - client_credentials grant type (e.g., Walmart Marketplace API)
    - Custom token header names (e.g., WM_SEC.ACCESS_TOKEN instead of Authorization)
    - Custom token prefixes (e.g., no prefix, or custom prefix instead of "Bearer ")
    
    Auth config options:
        grant_type: 'refresh_token' (default) or 'client_credentials'
        token_url: URL for token endpoint
        token_header: Header name for access token (default: 'Authorization')
        token_prefix: Prefix for token value (default: 'Bearer ')
        token_auth_method: How to send client credentials for token request
                          'client_secret_basic' (default) = HTTP Basic Auth
                          'client_secret_post' = In request body
    """
    
    def apply_auth(self, request_kwargs: Dict, credentials: Dict, auth_config: Dict) -> Dict:
        """Apply OAuth2 authentication to request headers."""
        access_token = credentials.get('access_token', '')
        
        if 'headers' not in request_kwargs:
            request_kwargs['headers'] = {}
        
        # Support custom token header name (default: Authorization)
        token_header = auth_config.get('token_header', 'Authorization')
        
        # Support custom token prefix (default: "Bearer ")
        # Use empty string for APIs that don't want a prefix
        token_prefix = auth_config.get('token_prefix', 'Bearer ')
        
        # Build the header value
        if token_prefix:
            header_value = f"{token_prefix}{access_token}"
        else:
            header_value = access_token
        
        request_kwargs['headers'][token_header] = header_value
        
        return request_kwargs
    
    def needs_refresh(self, integration: Dict) -> bool:
        """Check if token is expired or about to expire."""
        expires_at = integration.get('oauth_token_expires_at')
        if not expires_at:
            # For client_credentials without stored expiration, assume needs refresh
            # to ensure we always have a valid token
            return True
        
        if isinstance(expires_at, str):
            try:
                expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            except ValueError:
                # If we can't parse the date, assume expired
                return True
        
        # Refresh if within 5 minutes of expiration
        buffer = timedelta(minutes=5)
        return datetime.utcnow() >= (expires_at - buffer)
    
    def refresh_credentials(self, integration: Dict) -> Optional[Dict]:
        """
        Refresh OAuth2 access token.
        
        Supports both refresh_token and client_credentials grant types.
        """
        template = TemplateManager.get_template(integration.get('template_key'))
        if not template:
            return None
        
        auth_config = template.get('auth_config', {})
        token_url = auth_config.get('token_url')
        grant_type = auth_config.get('grant_type', 'refresh_token')
        
        if not token_url:
            logger.error("No token URL configured for OAuth2 refresh")
            return None
        
        # Get credentials from local secrets
        integration_id = integration.get('integration_id')
        client_id = get_local_secret(
            get_integration_secret_name(integration_id, 'client_id')
        )
        client_secret = get_local_secret(
            get_integration_secret_name(integration_id, 'client_secret')
        )
        
        if not client_id or not client_secret:
            logger.error("Missing client_id or client_secret for OAuth2")
            return None
        
        try:
            if grant_type == 'client_credentials':
                # Client Credentials flow (e.g., Walmart)
                result = self._token_request_client_credentials(
                    token_url, client_id, client_secret, auth_config
                )
            else:
                # Standard Refresh Token flow
                refresh_token = get_local_secret(
                    get_integration_secret_name(integration_id, 'refresh_token')
                )
                if not refresh_token:
                    logger.error("No refresh token available")
                    return None
                
                result = self._token_request_refresh_token(
                    token_url, client_id, client_secret, refresh_token, auth_config
                )
            
            if result:
                # Store the new access token
                set_local_secret(
                    get_integration_secret_name(integration_id, 'access_token'),
                    result['access_token'],
                    f"OAuth access token for integration {integration_id}"
                )
                
                # Store new refresh token if provided (refresh_token flow only)
                if result.get('refresh_token'):
                    set_local_secret(
                        get_integration_secret_name(integration_id, 'refresh_token'),
                        result['refresh_token'],
                        f"OAuth refresh token for integration {integration_id}"
                    )
                
                return result
            
            return None
                
        except Exception as e:
            logger.error(f"Error refreshing token: {e}")
            return None
    
    def _token_request_client_credentials(
        self, 
        token_url: str, 
        client_id: str, 
        client_secret: str,
        auth_config: Dict
    ) -> Optional[Dict]:
        """
        Perform client_credentials token request.
        
        Used by APIs like Walmart that don't require user authorization.
        """
        token_auth_method = auth_config.get('token_auth_method', 'client_secret_basic')
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json'
        }
        
        # Add any custom headers required for token endpoint
        token_headers = auth_config.get('token_headers', {})
        headers.update(token_headers)

        # Resolve dynamic placeholders in token headers (e.g., {{uuid}})
        for header_name in list(headers.keys()):
            header_value = headers[header_name]
            if isinstance(header_value, str) and '{{' in header_value:
                if '{{uuid}}' in header_value:
                    headers[header_name] = header_value.replace('{{uuid}}', str(uuid.uuid4()))

        data = {
            'grant_type': 'client_credentials'
        }
        
        # Add any additional token request parameters
        token_params = auth_config.get('token_params', {})
        data.update(token_params)
        
        # Determine how to send client credentials
        auth = None
        if token_auth_method == 'client_secret_basic':
            # Send as HTTP Basic Auth header
            auth = (client_id, client_secret)
        else:
            # Send in request body (client_secret_post)
            data['client_id'] = client_id
            data['client_secret'] = client_secret
        
        response = requests.post(
            token_url, 
            data=data, 
            headers=headers,
            auth=auth,
            timeout=30
        )
        
        if response.status_code == 200:
            token_data = response.json()
            expires_in = token_data.get('expires_in', 900)  # Default 15 min for client_credentials
            
            return {
                'access_token': token_data.get('access_token'),
                'refresh_token': None,  # client_credentials doesn't use refresh tokens
                'expires_in': expires_in,
                'expires_at': datetime.utcnow() + timedelta(seconds=expires_in)
            }
        else:
            logger.error(f"Client credentials token request failed: {response.status_code} - {response.text}")
            return None
    
    def _token_request_refresh_token(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        auth_config: Dict
    ) -> Optional[Dict]:
        """Perform refresh_token token request."""
        token_auth_method = auth_config.get('token_auth_method', 'client_secret_post')
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json'
        }
        
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token
        }
        
        auth = None
        if token_auth_method == 'client_secret_basic':
            auth = (client_id, client_secret)
        else:
            data['client_id'] = client_id
            data['client_secret'] = client_secret
        
        response = requests.post(
            token_url,
            data=data,
            headers=headers,
            auth=auth,
            timeout=30
        )
        
        if response.status_code == 200:
            token_data = response.json()
            expires_in = token_data.get('expires_in', 3600)
            
            return {
                'access_token': token_data.get('access_token'),
                'refresh_token': token_data.get('refresh_token', refresh_token),
                'expires_in': expires_in,
                'expires_at': datetime.utcnow() + timedelta(seconds=expires_in)
            }
        else:
            logger.error(f"Token refresh failed: {response.status_code} - {response.text}")
            return None


class OAuth1TBAAuthHandler(AuthHandler):
    """
    Handler for OAuth 1.0a Token-Based Authentication (TBA).

    Used by systems like NetSuite that require each request to be signed
    with HMAC-SHA256 using a combination of consumer and token credentials.

    Required credentials:
        - consumer_key: Application consumer key
        - consumer_secret: Application consumer secret
        - token_id: User token ID
        - token_secret: User token secret

    Auth config options:
        signature_method: HMAC-SHA256 (default) or HMAC-SHA1
        realm: OAuth realm (e.g., NetSuite account ID)
    """

    def apply_auth(self, request_kwargs: Dict, credentials: Dict, auth_config: Dict) -> Dict:
        """Build and apply OAuth 1.0a Authorization header."""
        consumer_key = credentials.get('consumer_key', '')
        consumer_secret = credentials.get('consumer_secret', '')
        token_id = credentials.get('token_id', '')
        token_secret = credentials.get('token_secret', '')

        if not all([consumer_key, consumer_secret, token_id, token_secret]):
            logger.error("OAuth1 TBA: missing one or more required credentials "
                         "(consumer_key, consumer_secret, token_id, token_secret)")
            return request_kwargs

        # OAuth 1.0a parameters
        oauth_nonce = uuid.uuid4().hex
        oauth_timestamp = str(int(time.time()))
        signature_method = auth_config.get('signature_method', 'HMAC-SHA256')
        realm = auth_config.get('realm', '')

        # Determine the HTTP method and URL from request_kwargs
        method = request_kwargs.get('method', 'GET').upper()
        url = request_kwargs.get('url', '')

        # Collect OAuth parameters (excluding realm and signature)
        oauth_params = {
            'oauth_consumer_key': consumer_key,
            'oauth_token': token_id,
            'oauth_nonce': oauth_nonce,
            'oauth_timestamp': oauth_timestamp,
            'oauth_signature_method': signature_method,
            'oauth_version': '1.0',
        }

        # Collect all parameters for signature base string
        # Include query params if present
        all_params = dict(oauth_params)
        if request_kwargs.get('params'):
            all_params.update(request_kwargs['params'])

        # Build the signature base string per OAuth 1.0a spec
        # 1. Percent-encode each key and value
        # 2. Sort by encoded key, then by encoded value
        # 3. Join with &
        encoded_params = []
        for k, v in all_params.items():
            encoded_params.append((quote(str(k), safe=''), quote(str(v), safe='')))
        encoded_params.sort()
        param_string = '&'.join(f"{k}={v}" for k, v in encoded_params)

        # Strip query string and fragment from URL for base string
        base_url = url.split('?')[0].split('#')[0]

        # Signature base string: METHOD&url&params
        base_string = '&'.join([
            quote(method, safe=''),
            quote(base_url, safe=''),
            quote(param_string, safe=''),
        ])

        # Signing key: consumer_secret&token_secret (both percent-encoded)
        signing_key = f"{quote(consumer_secret, safe='')}&{quote(token_secret, safe='')}"

        # Generate signature
        if signature_method == 'HMAC-SHA1':
            hash_func = hashlib.sha1
        else:
            hash_func = hashlib.sha256

        import base64
        hashed = hmac.new(
            signing_key.encode('utf-8'),
            base_string.encode('utf-8'),
            hash_func,
        )
        oauth_signature = base64.b64encode(hashed.digest()).decode('utf-8')

        # Build Authorization header
        auth_header_params = [
            f'realm="{quote(realm, safe="")}"' if realm else None,
            f'oauth_consumer_key="{quote(consumer_key, safe="")}"',
            f'oauth_token="{quote(token_id, safe="")}"',
            f'oauth_nonce="{quote(oauth_nonce, safe="")}"',
            f'oauth_timestamp="{quote(oauth_timestamp, safe="")}"',
            f'oauth_signature_method="{quote(signature_method, safe="")}"',
            f'oauth_version="1.0"',
            f'oauth_signature="{quote(oauth_signature, safe="")}"',
        ]
        auth_header = 'OAuth ' + ', '.join(p for p in auth_header_params if p)

        if 'headers' not in request_kwargs:
            request_kwargs['headers'] = {}
        request_kwargs['headers']['Authorization'] = auth_header

        masked_key = (consumer_key[:4] + '***' + consumer_key[-4:]) if len(consumer_key) > 10 else '***'
        logger.debug(f"OAuth1 TBA auth applied: realm='{realm}', "
                     f"consumer_key_preview={masked_key}, sig_method={signature_method}")

        return request_kwargs

    def needs_refresh(self, integration: Dict) -> bool:
        return False

    def refresh_credentials(self, integration: Dict) -> Optional[Dict]:
        return None


class CloudStorageAuthHandler(AuthHandler):
    """
    Handler for cloud storage authentication.

    Auth is handled by the cloud SDK inside the gateway service, so apply_auth
    is a no-op. Credentials are resolved from local secrets and passed to the
    gateway per-request by CloudStorageExecutor.
    """

    def apply_auth(self, request_kwargs: Dict, credentials: Dict, auth_config: Dict) -> Dict:
        # No-op: cloud storage auth is handled by the SDK in the gateway
        return request_kwargs

    def needs_refresh(self, integration: Dict) -> bool:
        return False

    def refresh_credentials(self, integration: Dict) -> Optional[Dict]:
        return None


def get_auth_handler(auth_type: str) -> AuthHandler:
    """Get the appropriate auth handler for the given type."""
    handlers = {
        'api_key': ApiKeyAuthHandler(),
        'bearer': BearerAuthHandler(),
        'basic': BasicAuthHandler(),
        'oauth2': OAuth2AuthHandler(),
        'oauth1_tba': OAuth1TBAAuthHandler(),
        'cloud_storage': CloudStorageAuthHandler(),
        'none': ApiKeyAuthHandler()  # No-op handler
    }
    return handlers.get(auth_type, ApiKeyAuthHandler())


# =============================================================================
# Operation Executor
# =============================================================================

class OperationExecutor:
    """Executes integration operations."""
    
    def __init__(self, integration: Dict, template: Dict):
        self.integration = integration
        self.template = template
        self.auth_handler = get_auth_handler(template.get('auth_type', 'none'))
    
    def execute(
        self, 
        operation_key: str, 
        parameters: Dict = None,
        context: Dict = None
    ) -> Dict[str, Any]:
        """
        Execute an operation.
        
        Args:
            operation_key: Key of the operation to execute
            parameters: Operation parameters
            context: Additional context (workflow_execution_id, agent_id, etc.)
            
        Returns:
            Dict with 'success', 'data', 'error', 'raw_response'
        """
        parameters = parameters or {}
        context = context or {}
        
        # Defensive check: parse parameters if they're a JSON string
        if isinstance(parameters, str):
            try:
                parameters = json.loads(parameters) if parameters else {}
            except json.JSONDecodeError:
                parameters = {}
        
        # Ensure parameters is a dict
        if not isinstance(parameters, dict):
            parameters = {}
        
        # Find the operation
        operations = self.template.get('operations', [])
        operation = next(
            (op for op in operations if op.get('key') == operation_key),
            None
        )
        
        if not operation:
            return {
                'success': False,
                'error': f"Operation '{operation_key}' not found",
                'data': None
            }
        
        # Check if auth needs refresh
        if self.auth_handler.needs_refresh(self.integration):
            logger.info("Refreshing OAuth token...")
            refresh_result = self.auth_handler.refresh_credentials(self.integration)
            if refresh_result:
                self._update_token_expiration(refresh_result)
        
        # Build the request
        try:
            request_kwargs = self._build_request(operation, parameters)
        except Exception as e:
            return {
                'success': False,
                'error': f"Error building request: {str(e)}",
                'data': None
            }
        
        # Execute the request
        start_time = time.time()
        try:
            method = operation.get('method', 'GET').upper()
            logger.info(f"Executing {method} {request_kwargs.get('url')} "
                        f"(operation: {operation_key})")
            response = requests.request(
                method=method,
                **request_kwargs,
                timeout=60
            )
            
            response_time_ms = int((time.time() - start_time) * 1000)
            
            # Process response
            result = self._process_response(response, operation)
            result['response_time_ms'] = response_time_ms
            result['status_code'] = response.status_code
            
            # Log execution
            self._log_execution(
                operation_key=operation_key,
                request_kwargs=request_kwargs,
                response=response,
                result=result,
                context=context
            )
            
            return result
            
        except requests.exceptions.Timeout:
            return {
                'success': False,
                'error': 'Request timed out',
                'data': None,
                'response_time_ms': int((time.time() - start_time) * 1000)
            }
        except requests.exceptions.RequestException as e:
            return {
                'success': False,
                'error': f"Request failed: {str(e)}",
                'data': None,
                'response_time_ms': int((time.time() - start_time) * 1000)
            }
    
    def _build_request(self, operation: Dict, parameters: Dict) -> Dict:
        """Build request kwargs for the operation."""
        # Get base URL and resolve instance variables
        instance_config = json.loads(self.integration.get('instance_config') or '{}')

        # Use base_url override from instance_config if present (e.g., Custom REST API)
        base_url = instance_config.pop('_base_url_override', None) or self.template.get('base_url', '')
        
        # Substitute instance variables in base URL
        for key, value in instance_config.items():
            base_url = base_url.replace(f'{{{key}}}', str(value))
        
        # Build endpoint URL
        endpoint = operation.get('endpoint', '')
        
        # Substitute parameters in endpoint
        for key, value in parameters.items():
            endpoint = endpoint.replace(f'{{{key}}}', str(value))
        
        # Also substitute instance config in endpoint
        for key, value in instance_config.items():
            endpoint = endpoint.replace(f'{{{key}}}', str(value))
        
        url = urljoin(base_url.rstrip('/') + '/', endpoint.lstrip('/'))
        
        # Start building request kwargs
        request_kwargs = {
            'url': url,
            'headers': dict(self.template.get('default_headers', {}))
        }

        # Substitute instance_config values and dynamic placeholders in headers
        for header_name in list(request_kwargs['headers'].keys()):
            header_value = request_kwargs['headers'][header_name]
            if isinstance(header_value, str):
                for key, value in instance_config.items():
                    header_value = header_value.replace(f'{{{key}}}', str(value))
                header_value = self._resolve_dynamic_placeholders(header_value)
                request_kwargs['headers'][header_name] = header_value

        # Handle query parameters for GET requests
        method = operation.get('method', 'GET').upper()
        if method == 'GET':
            query_params = {}
            for param_def in operation.get('parameters', []):
                param_name = param_def.get('name')
                if param_name in parameters and parameters[param_name]:
                    query_params[param_name] = parameters[param_name]
            
            # Handle query builder (for APIs like QuickBooks that use SQL-like queries)
            if operation.get('query_builder'):
                query = self._build_query(operation['query_builder'], parameters)
                query_params['query'] = query
            
            if query_params:
                request_kwargs['params'] = query_params
        
        # Handle request body for POST/PUT/PATCH
        elif method in ('POST', 'PUT', 'PATCH'):
            body_template = operation.get('body_template')
            if body_template:
                body = self._build_body(body_template, parameters)
                
                # Check content type for encoding
                content_type = request_kwargs['headers'].get('Content-Type', 'application/json')
                if 'application/x-www-form-urlencoded' in content_type:
                    request_kwargs['data'] = body
                else:
                    request_kwargs['json'] = body
            else:
                # Use parameters directly as body
                request_kwargs['json'] = parameters
        
        # Temporarily store method in request_kwargs for auth handlers that need it
        # (e.g., OAuth1 signature generation requires the HTTP method)
        request_kwargs['method'] = method

        # Apply authentication
        credentials = self._get_credentials()
        auth_config = self.template.get('auth_config', {})

        # Log credential availability (not values) for debugging auth issues
        cred_keys_found = [k for k, v in credentials.items() if v]
        logger.debug(f"Credentials found: {cred_keys_found}, "
                     f"auth_type: {self.template.get('auth_type')}")

        request_kwargs = self.auth_handler.apply_auth(request_kwargs, credentials, auth_config)

        # Remove method from kwargs — requests.request() takes it as a separate arg
        request_kwargs.pop('method', None)

        return request_kwargs
    
    def _build_query(self, query_template: str, parameters: Dict) -> str:
        """Build a query string from template and parameters."""
        query = query_template
        
        for key, value in parameters.items():
            if value is not None and value != '':
                query = query.replace(f'{{{key}}}', str(value))
        
        # Remove unfilled placeholders and their conditions
        # This is a simplified approach - may need enhancement for complex queries
        query = re.sub(r"AND \w+ [<>=!]+ '\{[^}]+\}'", '', query)
        query = re.sub(r"WHERE\s+AND", 'WHERE', query)
        query = re.sub(r"WHERE\s*$", '', query)
        
        return query.strip()
    
    def _build_body(self, body_template: Dict, parameters: Dict) -> Dict:
        """Build request body from template and parameters."""
        def substitute(obj):
            if isinstance(obj, str):
                # Check if entire string is a placeholder
                match = re.match(r'^\{(\w+)\}$', obj)
                if match:
                    param_name = match.group(1)
                    value = parameters.get(param_name)
                    # Try to parse JSON if it's a string that looks like JSON
                    if isinstance(value, str):
                        try:
                            return json.loads(value)
                        except:
                            pass
                    return value
                
                # Substitute placeholders within string
                for key, value in parameters.items():
                    if value is not None:
                        obj = obj.replace(f'{{{key}}}', str(value))
                return obj
            
            elif isinstance(obj, dict):
                result = {}
                for k, v in obj.items():
                    substituted = substitute(v)
                    # Skip None values
                    if substituted is not None and substituted != '':
                        result[k] = substituted
                return result
            
            elif isinstance(obj, list):
                return [substitute(item) for item in obj]
            
            return obj
        
        return substitute(body_template)

    def _resolve_dynamic_placeholders(self, value: str) -> str:
        """Resolve dynamic placeholders like {{uuid}} in header/config values."""
        if '{{uuid}}' in value:
            value = value.replace('{{uuid}}', str(uuid.uuid4()))
        if '{{timestamp}}' in value:
            value = value.replace('{{timestamp}}', datetime.utcnow().isoformat() + 'Z')
        return value

    def _get_credentials(self) -> Dict:
        """Get credentials from local secrets."""
        integration_id = self.integration.get('integration_id')
        credentials = {}
        
        # Common credential types to check
        credential_types = [
            'api_key', 'access_token', 'refresh_token',
            'bearer_token', 'username', 'password',
            'client_id', 'client_secret',
            'consumer_key', 'consumer_secret',
            'token_id', 'token_secret',
        ]
        
        for cred_type in credential_types:
            secret_name = get_integration_secret_name(integration_id, cred_type)
            value = get_local_secret(secret_name, '')
            if value:
                credentials[cred_type] = value
        
        return credentials
    
    def _process_response(self, response: requests.Response, operation: Dict) -> Dict:
        """Process the API response."""
        try:
            # Try to parse as JSON
            data = response.json()

            # Apply output mapping if defined
            output_mapping = operation.get('output_mapping', {})
            if output_mapping:
                mapped_data = {}
                for output_key, json_path in output_mapping.items():
                    mapped_data[output_key] = self._extract_json_path(data, json_path)
                data = mapped_data

            success = 200 <= response.status_code < 300

            # Build descriptive error message including HTTP status
            error_msg = None
            if not success:
                api_error = data.get('error', data.get('message', data.get('errors', '')))
                if isinstance(api_error, dict):
                    api_error = json.dumps(api_error)
                elif isinstance(api_error, list):
                    api_error = '; '.join(str(e) for e in api_error)
                status_text = {
                    400: 'Bad Request', 401: 'Unauthorized', 403: 'Forbidden',
                    404: 'Not Found', 429: 'Rate Limited', 500: 'Server Error'
                }.get(response.status_code, f'HTTP {response.status_code}')
                error_msg = f"{status_text} ({response.status_code})"
                if api_error:
                    error_msg += f": {api_error}"

            return {
                'success': success,
                'data': data,
                'error': error_msg,
                'raw_response': response.text[:5000]  # Truncate for logging
            }

        except json.JSONDecodeError:
            success = 200 <= response.status_code < 300
            return {
                'success': success,
                'data': response.text if success else None,
                'error': None if success else f"HTTP {response.status_code}: {response.text[:500]}",
                'raw_response': response.text[:5000]
            }
    
    def _extract_json_path(self, data: Any, path: str) -> Any:
        """Extract value from data using simplified JSON path (e.g., '$.results.items')."""
        if not path or not path.startswith('$'):
            return data
        
        parts = path[2:].split('.')  # Remove '$.' prefix
        current = data
        
        for part in parts:
            if not part:
                continue
                
            # Handle array index
            match = re.match(r'(\w+)\[(\d+)\]', part)
            if match:
                key, index = match.groups()
                if isinstance(current, dict):
                    current = current.get(key, [])
                if isinstance(current, list) and len(current) > int(index):
                    current = current[int(index)]
                else:
                    return None
            else:
                if isinstance(current, dict):
                    current = current.get(part)
                else:
                    return None
            
            if current is None:
                return None
        
        return current
    
    def _log_execution(
        self, 
        operation_key: str, 
        request_kwargs: Dict, 
        response: requests.Response,
        result: Dict,
        context: Dict
    ):
        """Log the execution to database."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            # Mask sensitive headers
            safe_headers = dict(request_kwargs.get('headers', {}))
            for key in ['Authorization', 'X-API-Key', 'X-Shopify-Access-Token']:
                if key in safe_headers:
                    safe_headers[key] = '***REDACTED***'
            
            cursor.execute("""
                INSERT INTO IntegrationExecutionLog (
                    integration_id, operation_key, workflow_execution_id, agent_id, user_id,
                    request_method, request_url, request_headers, request_body,
                    response_status, response_headers, response_body, response_time_ms,
                    success, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self.integration.get('integration_id'),
                operation_key,
                context.get('workflow_execution_id'),
                context.get('agent_id'),
                context.get('user_id'),
                request_kwargs.get('method', 'GET'),
                request_kwargs.get('url'),
                json.dumps(safe_headers),
                json.dumps(request_kwargs.get('json') or request_kwargs.get('data') or {})[:5000],
                response.status_code,
                json.dumps(dict(response.headers))[:2000],
                result.get('raw_response', '')[:5000],
                result.get('response_time_ms'),
                result.get('success'),
                result.get('error')
            ))
            
            # Update usage stats
            cursor.execute("EXEC sp_UpdateIntegrationUsage ?, ?", (
                self.integration.get('integration_id'),
                1 if result.get('success') else 0
            ))
            
            conn.commit()
            cursor.close()
            conn.close()
            
        except Exception as e:
            logger.error(f"Error logging execution: {e}")
    
    def _update_token_expiration(self, token_data: Dict):
        """Update token expiration in database."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            cursor.execute("""
                UPDATE UserIntegrations
                SET oauth_token_expires_at = ?, updated_at = GETUTCDATE()
                WHERE integration_id = ?
            """, (
                token_data.get('expires_at'),
                self.integration.get('integration_id')
            ))
            
            conn.commit()
            cursor.close()
            conn.close()
            
        except Exception as e:
            logger.error(f"Error updating token expiration: {e}")


# =============================================================================
# Cloud Storage Executor
# =============================================================================

class CloudStorageExecutor:
    """
    Executes cloud storage operations via the Cloud Storage Gateway service.

    Same interface as OperationExecutor so it can be used as a drop-in
    replacement when the template has execution_type == 'cloud_storage'.
    """

    def __init__(self, integration: Dict, template: Dict):
        self.integration = integration
        self.template = template

    def execute(
        self,
        operation_key: str,
        parameters: Dict = None,
        context: Dict = None
    ) -> Dict[str, Any]:
        """
        Execute a cloud storage operation via the gateway.

        Returns:
            Dict with 'success', 'data', 'error', 'response_time_ms'
        """
        parameters = parameters or {}
        context = context or {}

        # Defensive: parse parameters if JSON string
        if isinstance(parameters, str):
            try:
                parameters = json.loads(parameters) if parameters else {}
            except json.JSONDecodeError:
                parameters = {}
        if not isinstance(parameters, dict):
            parameters = {}

        # Verify operation exists in template
        operations = self.template.get('operations', [])
        operation = next(
            (op for op in operations if op.get('key') == operation_key),
            None
        )
        if not operation:
            return {
                'success': False,
                'error': f"Operation '{operation_key}' not found",
                'data': None
            }

        # Resolve credentials from local secrets
        credentials = self._get_credentials()
        provider = self.template.get('cloud_provider', 'azure_blob')

        start_time = time.time()
        try:
            from builder_cloud.client.cloud_storage_client import CloudStorageClient
            client = CloudStorageClient()

            # Map operation_key to the appropriate client method
            method_map = {
                'list_containers': lambda: client.list_containers(provider, credentials),
                'list_objects': lambda: client.list_objects(
                    provider, credentials,
                    parameters.get('container', ''),
                    parameters.get('prefix'),
                    int(parameters.get('max_results', 100))
                ),
                'upload_object': lambda: client.upload_object(
                    provider, credentials,
                    parameters.get('container', ''),
                    parameters.get('object_name', ''),
                    parameters.get('content', ''),
                    parameters.get('content_type', 'application/octet-stream'),
                    parameters.get('encoding', 'text')
                ),
                'download_object': lambda: client.download_object(
                    provider, credentials,
                    parameters.get('container', ''),
                    parameters.get('object_name', '')
                ),
                'delete_object': lambda: client.delete_object(
                    provider, credentials,
                    parameters.get('container', ''),
                    parameters.get('object_name', '')
                ),
                'get_object_metadata': lambda: client.get_object_metadata(
                    provider, credentials,
                    parameters.get('container', ''),
                    parameters.get('object_name', '')
                ),
                'generate_sas_url': lambda: client.generate_sas_url(
                    provider, credentials,
                    parameters.get('container', ''),
                    parameters.get('object_name', ''),
                    int(parameters.get('expiry_hours', 1)) * 3600,
                    parameters.get('permission', 'read')
                ),
            }

            handler = method_map.get(operation_key)
            if not handler:
                return {
                    'success': False,
                    'error': f"No handler for cloud storage operation '{operation_key}'",
                    'data': None
                }

            result = handler()
            response_time_ms = int((time.time() - start_time) * 1000)

            success = result.get('success', False)
            return_data = {
                'success': success,
                'data': result,
                'error': result.get('error'),
                'response_time_ms': response_time_ms,
                'raw_response': json.dumps(result)[:5000]
            }

            # Log execution
            self._log_execution(operation_key, parameters, return_data, context)

            return return_data

        except Exception as e:
            response_time_ms = int((time.time() - start_time) * 1000)
            error_result = {
                'success': False,
                'error': f"Cloud storage operation failed: {str(e)}",
                'data': None,
                'response_time_ms': response_time_ms
            }
            self._log_execution(operation_key, parameters, error_result, context)
            return error_result

    def _get_credentials(self) -> Dict:
        """Get cloud storage credentials from local secrets."""
        integration_id = self.integration.get('integration_id')
        credentials = {}

        # Get credential fields from template auth_config
        auth_config = self.template.get('auth_config', {})
        credential_fields = auth_config.get('credential_fields', [])

        for field_def in credential_fields:
            field_name = field_def.get('field', '')
            secret_name = get_integration_secret_name(integration_id, field_name)
            value = get_local_secret(secret_name, '')
            if value:
                credentials[field_name] = value

        return credentials

    def _log_execution(
        self,
        operation_key: str,
        parameters: Dict,
        result: Dict,
        context: Dict
    ):
        """Log the execution to database."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

            # Don't log file content in parameters
            safe_params = dict(parameters)
            if 'content' in safe_params and len(str(safe_params.get('content', ''))) > 200:
                safe_params['content'] = f"[{len(str(safe_params['content']))} chars]"

            cursor.execute("""
                INSERT INTO IntegrationExecutionLog (
                    integration_id, operation_key, workflow_execution_id, agent_id, user_id,
                    request_method, request_url, request_headers, request_body,
                    response_status, response_headers, response_body, response_time_ms,
                    success, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self.integration.get('integration_id'),
                operation_key,
                context.get('workflow_execution_id'),
                context.get('agent_id'),
                context.get('user_id'),
                'POST',
                f"cloud-storage://{self.template.get('cloud_provider', 'unknown')}/{operation_key}",
                '{}',
                json.dumps(safe_params)[:5000],
                200 if result.get('success') else 500,
                '{}',
                result.get('raw_response', '')[:5000],
                result.get('response_time_ms'),
                result.get('success'),
                result.get('error')
            ))

            # Update usage stats
            cursor.execute("EXEC sp_UpdateIntegrationUsage ?, ?", (
                self.integration.get('integration_id'),
                1 if result.get('success') else 0
            ))

            conn.commit()
            cursor.close()
            conn.close()

        except Exception as e:
            logger.error(f"Error logging cloud storage execution: {e}")


# =============================================================================
# Main Integration Manager
# =============================================================================

class IntegrationManager:
    """Main class for managing integrations."""
    
    def __init__(self):
        self.template_manager = TemplateManager
    
    # =========================================================================
    # Template Operations
    # =========================================================================
    
    def get_available_templates(self, category: str = None) -> List[Dict]:
        """Get available integration templates."""
        if category:
            templates = self.template_manager.get_templates_by_category(category)
        else:
            templates = self.template_manager.get_all_templates()
        
        # Add database templates (custom user templates)
        db_templates = self._get_custom_templates_from_db()
        
        return templates + db_templates
    
    def get_template(self, template_key: str) -> Optional[Dict]:
        """Get a specific template."""
        # Check built-in first
        template = self.template_manager.get_template(template_key)
        if template:
            return template
        
        # Check database for custom templates
        return self._get_custom_template_from_db(template_key)
    
    def get_categories(self) -> List[Dict]:
        """Get template categories."""
        return self.template_manager.get_categories()
    
    def _get_custom_templates_from_db(self) -> List[Dict]:
        """Get custom templates from database."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            cursor.execute("""
                SELECT template_key, platform_name, platform_category, description,
                       logo_url, auth_type, auth_config, base_url, default_headers,
                       operations, is_builtin
                FROM IntegrationTemplates
                WHERE is_active = 1 AND is_builtin = 0
            """)
            
            templates = []
            for row in cursor.fetchall():
                templates.append({
                    'template_key': row[0],
                    'platform_name': row[1],
                    'platform_category': row[2],
                    'description': row[3],
                    'logo_url': row[4],
                    'auth_type': row[5],
                    'auth_config': json.loads(row[6]) if row[6] else {},
                    'base_url': row[7],
                    'default_headers': json.loads(row[8]) if row[8] else {},
                    'operations': json.loads(row[9]) if row[9] else [],
                    'is_builtin': bool(row[10]),
                    'is_custom': True
                })
            
            cursor.close()
            conn.close()
            return templates
            
        except Exception as e:
            logger.error(f"Error getting custom templates: {e}")
            return []
    
    def _get_custom_template_from_db(self, template_key: str) -> Optional[Dict]:
        """Get a specific custom template from database."""
        templates = self._get_custom_templates_from_db()
        return next((t for t in templates if t['template_key'] == template_key), None)
    
    # =========================================================================
    # Integration CRUD
    # =========================================================================
    
    def create_integration(
        self,
        template_key: str,
        integration_name: str,
        credentials: Dict,
        instance_config: Dict = None,
        user_id: int = None,
        description: str = None,
        base_url_override: str = None
    ) -> Tuple[bool, Optional[int], str]:
        """
        Create a new integration instance.

        Args:
            template_key: Template to use
            integration_name: User's name for this integration
            credentials: Dict of credentials (api_key, access_token, etc.)
            instance_config: Instance-specific config (shop_domain, realmId, etc.)
            user_id: Creating user's ID
            description: Optional description
            base_url_override: Override the template's base_url (for Custom REST API etc.)

        Returns:
            Tuple of (success, integration_id, message)
        """
        template = self.get_template(template_key)
        if not template:
            return False, None, f"Template '{template_key}' not found"

        # Store base_url override in instance_config so it's available at execution time
        if base_url_override:
            if instance_config is None:
                instance_config = {}
            instance_config['_base_url_override'] = base_url_override

        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

            # Check if template exists in DB, if not create it
            cursor.execute(
                "SELECT template_id FROM IntegrationTemplates WHERE template_key = ?",
                template_key
            )
            row = cursor.fetchone()

            if row:
                template_id = row[0]
            else:
                # Insert built-in template to DB using OUTPUT clause for reliable ID retrieval
                cursor.execute("""
                    INSERT INTO IntegrationTemplates (
                        template_key, platform_name, platform_category, description,
                        logo_url, auth_type, auth_config, base_url, default_headers,
                        operations, is_builtin, is_active
                    ) 
                    OUTPUT INSERTED.template_id
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1)
                """, (
                    template_key,
                    template.get('platform_name'),
                    template.get('platform_category'),
                    template.get('description'),
                    template.get('logo_url'),
                    template.get('auth_type'),
                    json.dumps(template.get('auth_config', {})),
                    template.get('base_url'),
                    json.dumps(template.get('default_headers', {})),
                    json.dumps(template.get('operations', []))
                ))
                row = cursor.fetchone()
                if row is None:
                    return False, None, "Failed to insert template - check RLS policies and permissions"
                template_id = int(row[0])
            
            # Create the integration record using OUTPUT clause
            cursor.execute("""
                INSERT INTO UserIntegrations (
                    template_id, integration_name, description, instance_config,
                    credentials_reference, is_active, created_by
                ) 
                OUTPUT INSERTED.integration_id
                VALUES (?, ?, ?, ?, ?, 1, ?)
            """, (
                template_id,
                integration_name,
                description,
                json.dumps(instance_config or {}),
                '{}',  # Will update with references after we have the ID
                user_id
            ))
            
            row = cursor.fetchone()
            if row is None:
                return False, None, "Failed to insert integration - check RLS policies and permissions"
            integration_id = int(row[0])
            
            # Store credentials in local secrets
            credentials_references = {}
            for cred_key, cred_value in credentials.items():
                if cred_value:
                    secret_name = get_integration_secret_name(integration_id, cred_key)
                    set_local_secret(
                        secret_name,
                        cred_value,
                        f"{cred_key} for {integration_name}",
                        'integration_credentials'
                    )
                    credentials_references[cred_key] = create_secret_reference(secret_name)
            
            # Update with credential references
            cursor.execute("""
                UPDATE UserIntegrations
                SET credentials_reference = ?, is_connected = 1, last_connected_at = GETUTCDATE()
                WHERE integration_id = ?
            """, (json.dumps(credentials_references), integration_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return True, integration_id, "Integration created successfully"
            
        except Exception as e:
            logger.error(f"Error creating integration: {e}")
            return False, None, str(e)
    
    def get_integration(self, integration_id: int) -> Optional[Dict]:
        """Get an integration by ID."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            cursor.execute("EXEC sp_GetIntegrationForExecution ?", integration_id)
            row = cursor.fetchone()
            
            if not row:
                return None
            
            integration = {
                'integration_id': row[0],
                'integration_name': row[1],
                'instance_config': row[2],
                'credentials_reference': row[3],
                'oauth_token_expires_at': row[4],
                'is_connected': bool(row[5]),
                'template_key': row[6],
                'platform_name': row[7],
                'auth_type': row[8],
                'auth_config': json.loads(row[9]) if row[9] else {},
                'base_url': row[10],
                'default_headers': json.loads(row[11]) if row[11] else {},
                'operations': json.loads(row[12]) if row[12] else []
            }
            
            cursor.close()
            conn.close()
            
            return integration
            
        except Exception as e:
            logger.error(f"Error getting integration: {e}")
            return None
    
    def list_integrations(self, user_id: int = None, include_inactive: bool = False) -> List[Dict]:
        """List all integrations."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            query = """
                SELECT
                    ui.integration_id, ui.integration_name, ui.description,
                    ui.is_connected, ui.last_used_at, ui.total_requests,
                    ui.successful_requests, ui.failed_requests,
                    it.template_key, it.platform_name, it.platform_category,
                    it.logo_url, it.auth_type, ui.instance_config
                FROM UserIntegrations ui
                INNER JOIN IntegrationTemplates it ON ui.template_id = it.template_id
                WHERE ui.is_active = 1
            """
            
            params = []
            if user_id:
                query += " AND ui.created_by = ?"
                params.append(user_id)
            
            if not include_inactive:
                query += " AND it.is_active = 1"
            
            query += " ORDER BY ui.integration_name"
            
            cursor.execute(query, params)
            
            integrations = []
            for row in cursor.fetchall():
                integrations.append({
                    'integration_id': row[0],
                    'integration_name': row[1],
                    'description': row[2],
                    'is_connected': bool(row[3]),
                    'last_used_at': row[4].isoformat() if row[4] else None,
                    'total_requests': row[5],
                    'successful_requests': row[6],
                    'failed_requests': row[7],
                    'template_key': row[8],
                    'platform_name': row[9],
                    'platform_category': row[10],
                    'logo_url': row[11],
                    'auth_type': row[12],
                    'instance_config': row[13]
                })
            
            cursor.close()
            conn.close()
            
            return integrations
            
        except Exception as e:
            logger.error(f"Error listing integrations: {e}")
            return []
    
    def get_integrations_by_template(self, template_key: str) -> List[Dict]:
        """
        Get all integrations that use a specific template.
        
        Useful for checking if a template can be deleted.
        
        Args:
            template_key: The template key to search for
            
        Returns:
            List of integrations using this template
        """
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            cursor.execute("""
                SELECT 
                    ui.integration_id, ui.integration_name, ui.is_connected
                FROM UserIntegrations ui
                INNER JOIN IntegrationTemplates it ON ui.template_id = it.template_id
                WHERE it.template_key = ? AND ui.is_active = 1
            """, template_key)
            
            integrations = []
            for row in cursor.fetchall():
                integrations.append({
                    'integration_id': row[0],
                    'integration_name': row[1],
                    'is_connected': bool(row[2])
                })
            
            cursor.close()
            conn.close()
            
            return integrations
            
        except Exception as e:
            logger.error(f"Error getting integrations by template: {e}")
            return []
    
    def update_integration(
        self,
        integration_id: int,
        integration_name: str = None,
        description: str = None,
        instance_config: Dict = None,
        credentials: Dict = None
    ) -> Tuple[bool, str]:
        """Update an integration."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            updates = []
            params = []
            
            if integration_name:
                updates.append("integration_name = ?")
                params.append(integration_name)
            
            if description is not None:
                updates.append("description = ?")
                params.append(description)
            
            if instance_config is not None:
                # Merge with existing instance_config to preserve fields not being updated
                cursor.execute(
                    "SELECT instance_config FROM UserIntegrations WHERE integration_id = ?",
                    integration_id
                )
                existing_row = cursor.fetchone()
                existing_config = {}
                if existing_row and existing_row[0]:
                    try:
                        existing_config = json.loads(existing_row[0])
                    except (json.JSONDecodeError, TypeError):
                        pass
                existing_config.update(instance_config)
                updates.append("instance_config = ?")
                params.append(json.dumps(existing_config))
            
            if credentials:
                # Update credentials in local secrets
                for cred_key, cred_value in credentials.items():
                    if cred_value:
                        secret_name = get_integration_secret_name(integration_id, cred_key)
                        set_local_secret(
                            secret_name,
                            cred_value,
                            f"{cred_key} for integration {integration_id}",
                            'integration_credentials'
                        )
                # Reset connection status so user can re-test with new credentials
                updates.append("is_connected = 1")
                updates.append("last_error = NULL")

            if updates:
                updates.append("updated_at = GETUTCDATE()")
                params.append(integration_id)
                
                cursor.execute(f"""
                    UPDATE UserIntegrations
                    SET {', '.join(updates)}
                    WHERE integration_id = ?
                """, params)
                
                conn.commit()
            
            cursor.close()
            conn.close()
            
            return True, "Integration updated successfully"
            
        except Exception as e:
            logger.error(f"Error updating integration: {e}")
            return False, str(e)
    
    def delete_integration(self, integration_id: int) -> Tuple[bool, str]:
        """Soft delete an integration."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            cursor.execute("""
                UPDATE UserIntegrations
                SET is_active = 0, updated_at = GETUTCDATE()
                WHERE integration_id = ?
            """, integration_id)
            
            conn.commit()
            cursor.close()
            conn.close()
            
            # Note: We don't delete the local secrets - user might want to restore
            
            return True, "Integration deleted successfully"
            
        except Exception as e:
            logger.error(f"Error deleting integration: {e}")
            return False, str(e)
    
    # =========================================================================
    # Operation Execution
    # =========================================================================
    
    def execute_operation(
        self,
        integration_id: int,
        operation_key: str,
        parameters: Dict = None,
        context: Dict = None
    ) -> Dict[str, Any]:
        """
        Execute an operation on an integration.
        
        Args:
            integration_id: ID of the integration to use
            operation_key: Key of the operation to execute
            parameters: Operation parameters
            context: Additional context (workflow_execution_id, agent_id, etc.)
            
        Returns:
            Dict with 'success', 'data', 'error'
        """
        # Get the integration
        integration = self.get_integration(integration_id)
        if not integration:
            return {
                'success': False,
                'error': f"Integration {integration_id} not found",
                'data': None
            }
        
        if not integration.get('is_connected'):
            logger.warning(f"Integration {integration_id} "
                           f"({integration.get('integration_name')}) is not connected - "
                           f"skipping operation. Use Test Connection to re-verify.")
            return {
                'success': False,
                'error': "Integration is not connected. Please use Test Connection to re-verify.",
                'data': None
            }
        
        # Get the template
        template = self.get_template(integration.get('template_key'))
        if not template:
            return {
                'success': False,
                'error': f"Template not found",
                'data': None
            }
        
        # Create executor and run — route cloud storage to its own executor
        if template.get('execution_type') == 'cloud_storage':
            executor = CloudStorageExecutor(integration, template)
        else:
            executor = OperationExecutor(integration, template)
        return executor.execute(operation_key, parameters, context)
    
    def get_operations(self, integration_id: int) -> List[Dict]:
        """Get available operations for an integration."""
        integration = self.get_integration(integration_id)
        if not integration:
            return []
        
        return integration.get('operations', [])
    
    # =========================================================================
    # Test Connection
    # =========================================================================
    
    def test_connection(self, integration_id: int) -> Dict[str, Any]:
        """Test an integration connection.

        Unlike execute_operation(), this bypasses the is_connected check
        so that a previously-failed integration can be re-tested and
        recover to a connected state.
        """
        integration = self.get_integration(integration_id)
        if not integration:
            return {
                'success': False,
                'error': 'Integration not found'
            }

        # Get the template
        template = self.get_template(integration.get('template_key'))
        if not template:
            return {
                'success': False,
                'error': 'Template not found'
            }

        # Find a read-only operation to test with
        operations = integration.get('operations', [])
        test_operation = next(
            (op for op in operations if op.get('category') == 'read'),
            operations[0] if operations else None
        )

        if not test_operation:
            return {
                'success': False,
                'error': 'No operations available to test'
            }

        # Execute directly, bypassing is_connected check
        logger.info(f"Testing connection for integration {integration_id} "
                     f"({integration.get('integration_name')}) using operation "
                     f"'{test_operation.get('key')}'")

        # Route cloud storage to its own executor
        if template.get('execution_type') == 'cloud_storage':
            executor = CloudStorageExecutor(integration, template)
        else:
            executor = OperationExecutor(integration, template)
        parameters = {'limit': 1} if 'limit' in str(test_operation.get('parameters', [])) else {}
        result = executor.execute(
            test_operation.get('key'),
            parameters,
            {'test_connection': True}
        )

        # Update connection status based on test result
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

            cursor.execute("""
                UPDATE UserIntegrations
                SET is_connected = ?, last_error = ?, updated_at = GETUTCDATE()
                WHERE integration_id = ?
            """, (
                result.get('success'),
                result.get('error') if not result.get('success') else None,
                integration_id
            ))

            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"Connection test for integration {integration_id}: "
                        f"{'SUCCESS' if result.get('success') else 'FAILED'}"
                        f"{' - ' + str(result.get('error')) if result.get('error') else ''}")

        except Exception as e:
            logger.error(f"Error updating connection status: {e}")
        
        return result


# =============================================================================
# Convenience Functions
# =============================================================================

_manager: Optional[IntegrationManager] = None


def get_integration_manager() -> IntegrationManager:
    """Get or create the global integration manager."""
    global _manager
    if _manager is None:
        _manager = IntegrationManager()
    return _manager


def execute_integration_operation(
    integration_id: int,
    operation_key: str,
    parameters: Dict = None,
    **context
) -> Dict[str, Any]:
    """Convenience function for executing an integration operation."""
    return get_integration_manager().execute_operation(
        integration_id, operation_key, parameters, context
    )
