import config as cfg
import pyodbc
import socket
import os
import re
import unicodedata
import uuid
import app_config
from system_prompts import NODE_DETAIL_REFERENCE
import time


database_server = cfg.DATABASE_SERVER
database_name = cfg.DATABASE_NAME
username = cfg.DATABASE_UID
password = cfg.DATABASE_PWD


def get_app_root():
    """Get the application root directory - always returns an absolute path.
    Uses APP_ROOT env var, falls back to the directory containing this file (for dev)."""
    root = os.getenv('APP_ROOT')
    if root:
        return os.path.abspath(root)
    return os.path.dirname(os.path.abspath(__file__))


def get_app_path(*parts):
    """Join path parts relative to APP_ROOT. Always returns an absolute path."""
    return os.path.join(get_app_root(), *parts)


def get_log_path(filename):
    """Get absolute log file path under APP_ROOT/logs/."""
    return get_app_path('logs', filename)


def get_app_version():
    return app_config.APP_VERSION

def get_agent_version():
    return app_config.AGENT_EXPORT_VERSION

def get_env_version():
    return app_config.ENV_EXPORT_VERSION

def get_db_connection():
    """Create and return a connection to the database"""
    return pyodbc.connect(f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}")

def get_db_connection_string():
    """Create and return a connection to the database"""
    return f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"

def get_cloud_db_connection():
    """Create and return a connection to the database"""
    return pyodbc.connect(f"DRIVER={{SQL Server}};SERVER={cfg.CLOUD_DATABASE_SERVER};DATABASE={cfg.CLOUD_DATABASE_NAME};UID={cfg.CLOUD_DATABASE_UID};PWD={cfg.CLOUD_DATABASE_PWD}")

def get_cloud_db_connection_string():
    """Create and return a connection to the database"""
    return f"DRIVER={{SQL Server}};SERVER={cfg.CLOUD_DATABASE_SERVER};DATABASE={cfg.CLOUD_DATABASE_NAME};UID={cfg.CLOUD_DATABASE_UID};PWD={cfg.CLOUD_DATABASE_PWD}"

def estimate_token_count(text: str) -> int:
    """Simple token estimation"""
    if not text:
        return 0
    return len(text) // cfg.DOC_CHARS_PER_TOKEN

def clean_string_for_logging(text):
    """
    Clean a string to make it safe for logging on Windows console.
    Removes or replaces characters that can't be encoded in cp1252.
    
    Args:
        text (str): Input string that may contain Unicode characters
        
    Returns:
        str: Cleaned string safe for logging
    """
    if not isinstance(text, str):
        text = str(text)
    
    # Method 1: Remove all non-ASCII characters
    # return ''.join(char for char in text if ord(char) < 128)
    
    # Method 2: Replace problematic characters with safe alternatives
    try:
        # Try to encode with cp1252, replace errors
        cleaned = text.encode('cp1252', errors='replace').decode('cp1252')
        return cleaned
    except:
        # Fallback: remove all non-ASCII characters
        return ''.join(char for char in text if ord(char) < 128)


def remove_emojis(text):
    """
    Remove all emoji characters from a string.
    
    Args:
        text (str): Input string
        
    Returns:
        str: String with emojis removed
    """
    if not isinstance(text, str):
        text = str(text)
    
    # Pattern to match emoji characters
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags (iOS)
        "\U00002500-\U00002BEF"  # chinese char
        "\U00002702-\U000027B0"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "\U0001f926-\U0001f937"
        "\U00010000-\U0010ffff"
        "\u2640-\u2642"
        "\u2600-\u2B55"
        "\u200d"
        "\u23cf"
        "\u23e9"
        "\u231a"
        "\ufe0f"  # dingbats
        "\u3030"
        "]+",
        flags=re.UNICODE
    )
    
    return emoji_pattern.sub(r'', text)

def sanitize_for_ascii(text):
    """
    Convert a string to ASCII-safe format by removing accents and special characters.
    
    Args:
        text (str): Input string
        
    Returns:
        str: ASCII-safe string
    """
    if not isinstance(text, str):
        text = str(text)
    
    # Normalize Unicode characters (decompose accented characters)
    normalized = unicodedata.normalize('NFD', text)
    
    # Filter out non-ASCII characters
    ascii_text = ''.join(char for char in normalized if ord(char) < 128)
    
    return ascii_text

def safe_log_string(text, method='replace'):
    """
    Make a string safe for logging with different cleaning methods.
    
    Args:
        text (str): Input string
        method (str): Cleaning method - 'replace', 'remove', 'ascii', 'emoji'
        
    Returns:
        str: Cleaned string
    """
    if not isinstance(text, str):
        text = str(text)
    
    if method == 'replace':
        return clean_string_for_logging(text)
    elif method == 'remove':
        return ''.join(char for char in text if ord(char) < 128)
    elif method == 'ascii':
        return sanitize_for_ascii(text)
    elif method == 'emoji':
        return remove_emojis(text)
    else:
        return clean_string_for_logging(text)


def clean_json_response(response: str) -> str:
    """Remove markdown JSON fencing from AI response."""
    if not response:
        return response
    
    cleaned = response.strip()
    
    # Remove ```json or ``` at start (with optional whitespace)
    cleaned = re.sub(r'^\s*```json\s*', '', cleaned)
    cleaned = re.sub(r'^\s*```\s*', '', cleaned)
    
    # Remove ``` at end (with optional whitespace)
    cleaned = re.sub(r'\s*```\s*$', '', cleaned)
    
    return cleaned.strip()


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    ip = s.getsockname()[0]
    s.close()
    return ip


def get_base_url():
    """Return full base URL
    
    Returns:
        Base URL
    """
    # Get the protocol from environment or default to http
    protocol = os.getenv('PROTOCOL', 'http')
    
    # Get host from environment
    host = os.getenv('HOST', 'localhost')
    if host == "0.0.0.0":
        host = get_local_ip()
    
    # Calculate document API port by adding 10 to the current port
    try:
        current_port = int(os.getenv('HOST_PORT', '5001'))
        document_port = current_port
    except ValueError:
        # Fallback to default port if HOST_PORT is not a valid integer
        document_port = 5001
    
    # Construct the base URL
    base_url = f"{protocol}://{host}:{document_port}"
    
    # Return
    return base_url

def get_document_api_base_url():
    """Return full base URL
    
    Returns:
        Base URL
    """
    # Get the protocol from environment or default to http
    protocol = os.getenv('PROTOCOL', 'http')
    
    # Get host from environment
    host = os.getenv('HOST', 'localhost')
    if host == "0.0.0.0":
        host = get_local_ip()
    
    # Calculate document API port by adding 10 to the current port
    try:
        current_port = int(os.getenv('HOST_PORT', '5001'))
        document_port = current_port + 10
    except ValueError:
        # Fallback to default port if HOST_PORT is not a valid integer
        document_port = 5011
    
    # Construct the base URL
    base_url = f"{protocol}://{host}:{document_port}"
    
    # Return
    return base_url

def get_scheduler_api_base_url():
    """Return full base URL for scheduler API.

    NOTE: Scheduler routes are registered in the main app (not a separate service),
    so this returns the same base URL as get_base_url().

    Returns:
        Base URL (same as main app)
    """
    # Scheduler is part of the main app, not a separate microservice
    return get_base_url()

def get_vector_api_base_url():
    """Return full base URL
    
    Returns:
        Base URL
    """
    # Get the protocol from environment or default to http
    protocol = os.getenv('PROTOCOL', 'http')
    
    # Get host from environment
    host = os.getenv('HOST', 'localhost')
    if host == "0.0.0.0":
        host = get_local_ip()
    
    # Calculate document API port by adding 10 to the current port
    try:
        current_port = int(os.getenv('HOST_PORT', '5001'))
        document_port = current_port + 30
    except ValueError:
        # Fallback to default port if HOST_PORT is not a valid integer
        document_port = 5031
    
    # Construct the base URL
    base_url = f"{protocol}://{host}:{document_port}"
    
    # Return
    return base_url

def get_agent_api_base_url():
    """Return full base URL
    
    Returns:
        Base URL
    """
    # Get the protocol from environment or default to http
    protocol = os.getenv('PROTOCOL', 'http')
    
    # Get host from environment
    host = os.getenv('HOST', 'localhost')
    if host == "0.0.0.0":
        host = get_local_ip()
    
    # Calculate API port by adding 40 to the current port
    try:
        current_port = int(os.getenv('HOST_PORT', '5001'))
        api_port = current_port + 40
    except ValueError:
        # Fallback to default port if HOST_PORT is not a valid integer
        api_port = 5041
    
    # Construct the base URL
    base_url = f"{protocol}://{host}:{api_port}"
    
    # Return
    return base_url

def get_knowledge_api_base_url():
    """Return full base URL
    
    Returns:
        Base URL
    """
    # Get the protocol from environment or default to http
    protocol = os.getenv('PROTOCOL', 'http')
    
    # Get host from environment
    host = os.getenv('HOST', 'localhost')
    if host == "0.0.0.0":
        host = get_local_ip()
    
    # Calculate API port by adding 40 to the current port
    try:
        current_port = int(os.getenv('HOST_PORT', '5001'))
        api_port = current_port + 50
    except ValueError:
        # Fallback to default port if HOST_PORT is not a valid integer
        api_port = 5051
    
    # Construct the base URL
    base_url = f"{protocol}://{host}:{api_port}"
    
    # Return
    return base_url

def get_executor_api_base_url():
    """Return full base URL
    
    Returns:
        Base URL
    """
    # Get the protocol from environment or default to http
    protocol = os.getenv('PROTOCOL', 'http')
    
    # Get host from environment
    host = os.getenv('HOST', 'localhost')
    if host == "0.0.0.0":
        host = get_local_ip()
    
    # Calculate API port by adding 40 to the current port
    try:
        current_port = int(os.getenv('HOST_PORT', '5001'))
        api_port = current_port + 60
    except ValueError:
        # Fallback to default port if HOST_PORT is not a valid integer
        api_port = 5061
    
    # Construct the base URL
    base_url = f"{protocol}://{host}:{api_port}"
    
    # Return
    return base_url

def get_mcp_gateway_api_base_url():
    """Return full base URL for MCP Gateway service

    Returns:
        Base URL (e.g. http://localhost:5071)
    """
    # Get the protocol from environment or default to http
    protocol = os.getenv('PROTOCOL', 'http')

    # Get host from environment
    host = os.getenv('HOST', 'localhost')
    if host == "0.0.0.0":
        host = get_local_ip()

    # Calculate API port by adding 70 to the base port
    try:
        current_port = int(os.getenv('HOST_PORT', '5001'))
        api_port = current_port + 70
    except ValueError:
        # Fallback to default port if HOST_PORT is not a valid integer
        api_port = 5071

    # Construct the base URL
    base_url = f"{protocol}://{host}:{api_port}"

    # Return
    return base_url

def get_cloud_storage_api_base_url():
    """Return full base URL for Cloud Storage Gateway service

    Returns:
        Base URL (e.g. http://localhost:5081)
    """
    protocol = os.getenv('PROTOCOL', 'http')
    host = os.getenv('HOST', 'localhost')
    if host == "0.0.0.0":
        host = get_local_ip()

    # Calculate API port by adding 80 to the base port
    try:
        current_port = int(os.getenv('HOST_PORT', '5001'))
        api_port = current_port + 80
    except ValueError:
        api_port = 5081

    return f"{protocol}://{host}:{api_port}"

def get_command_center_api_base_url():
    """Return full base URL for Command Center service

    Returns:
        Base URL (e.g. http://localhost:5091)
    """
    protocol = os.getenv('PROTOCOL', 'http')
    host = os.getenv('HOST', 'localhost')
    if host == "0.0.0.0":
        host = get_local_ip()

    # Calculate API port by adding 90 to the base port
    try:
        current_port = int(os.getenv('HOST_PORT', '5001'))
        api_port = current_port + 90
    except ValueError:
        api_port = 5091

    return f"{protocol}://{host}:{api_port}"

def normalize_boolean(value):
    """
    Normalize various representations to Python boolean values (True/False).
    
    Args:
        value: A Python boolean, string ('true'/'false'), or other value
        
    Returns:
        bool: True or False
    """
    # Handle string values explicitly
    if isinstance(value, str):
        return value.lower() in ('true', 'yes', 'y', '1', 'on')
    
    # For everything else, use Python's truthiness
    return bool(value)


def generate_connection_string(database_type, server, port, database_name, user_name, password, parameters='', odbc_driver=None):
    """
    Generate ODBC connection string based on database type and parameters
    """
    conn_str = ""

    # Normalize database type for case-insensitive comparison
    db_type_lower = (database_type or '').strip().lower()

    # Parse parameters string to dictionary
    param_dict = {}
    if parameters and parameters.strip():
        for param in parameters.split(';'):
            if param and '=' in param:
                key, value = param.split('=', 1)
                param_dict[key.strip()] = value.strip()

    # Get default driver if none provided
    if not odbc_driver:
        if db_type_lower in ('sql server', 'sqlserver'):
            odbc_driver = 'ODBC Driver 17 for SQL Server'
        elif db_type_lower == 'oracle':
            odbc_driver = 'Oracle in OraClient12Home1'
        elif db_type_lower in ('postgres', 'postgresql'):
            odbc_driver = 'PostgreSQL UNICODE'
        elif db_type_lower == 'snowflake':
            odbc_driver = 'SnowflakeDSIIDriver'
        else:
            odbc_driver = 'SQL Server'

    # Generate connection string based on database type
    if db_type_lower in ('sql server', 'sqlserver'):
        # Default port to 1433 for SQL Server if not specified or zero
        if not port or port == 0:
            port = 1433
        # Handle SQL Server instances correctly - don't use port if server contains a backslash
        if '\\' in server:
            # When there's an instance name, don't use the port
            conn_str = f"DRIVER={{{odbc_driver}}};Server={server};Database={database_name};Uid={user_name};Pwd={password};"
        else:
            # When no instance is specified, include the port
            conn_str = f"DRIVER={{{odbc_driver}}};Server={server},{port};Database={database_name};Uid={user_name};Pwd={password};"

        for key, value in param_dict.items():
            conn_str += f"{key}={value};"

    elif db_type_lower == 'oracle':
        conn_str = f"DRIVER={{{odbc_driver}}};DBQ={server}:{port}/{database_name};Uid={user_name};Pwd={password};"
        for key, value in param_dict.items():
            conn_str += f"{key}={value};"

    elif db_type_lower in ('postgres', 'postgresql'):
        conn_str = f"DRIVER={{{odbc_driver}}};Server={server};Port={port};Database={database_name};Uid={user_name};Pwd={password};"
        for key, value in param_dict.items():
            conn_str += f"{key}={value};"

    elif db_type_lower == 'snowflake':
        conn_str = f"DRIVER={{{odbc_driver}}};Server={server};Port={port};Database={database_name};Uid={user_name};Pwd={password};"
        for key, value in param_dict.items():
            conn_str += f"{key}={value};"

    else:
        conn_str = f"DRIVER={{{odbc_driver}}};Server={server};Port={port};Database={database_name};Uid={user_name};Pwd={password};"
        for key, value in param_dict.items():
            conn_str += f"{key}={value};"
    
    return conn_str


# anthropic_client.py
import requests
import json
import logging
import base64
import time
import random
from typing import Dict, List, Union, Optional, Any, BinaryIO

class AnthropicProxyClient:
    """Client for using the Anthropic API through a proxy"""
    
    def __init__(self, proxy_base_url: str = os.getenv('AI_HUB_DOCUMENT_API_URL')):
        """
        Initialize the Anthropic proxy client.
        
        Args:
            proxy_base_url: Base URL for the proxy API
        """
        self.proxy_base_url = proxy_base_url
        self.document_proces_route = os.getenv('AI_HUB_DOCUMENT_PROCESS_ROUTE')
        self.logger = logging.getLogger(__name__)

        # Retry configuration - simple and conservative
        self.max_retries = cfg.DOC_ANTHROPIC_PROXY_MAX_RETRIES                  # Number of attempts
        self.base_delay = cfg.DOC_ANTHROPIC_PROXY_RETRY_DELAY_BASE              # Initial delay in seconds
        self.max_delay = cfg.DOC_ANTHROPIC_PROXY_RETRY_DELAY_MAX                # Maximum delay in seconds
        self.retry_status_codes = cfg.DOC_ANTHROPIC_PROXY_RETRY_STATUS_CODES    # Added 529 for overloaded

        self.api_key = os.getenv("API_KEY")
        self.user_request_id = None
        self.module_name = None


    def _set_tracking_params(self, module_name, request_id=None):
        self.module_name = module_name
        if not request_id:
            request_id = str(uuid.uuid4())
        self.user_request_id = request_id
        print(f'Anthropic Proxy Client: Set request id {self.user_request_id} for module {self.module_name}')

    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers for Cloud API requests"""
        return {'X-API-Key': self.api_key or os.getenv("API_KEY", '')}

    def _get_tracking_params(self) -> Dict[str, str]:
        """Get tracking parameters from Flask context if available"""
        params = {}
        try:
            params['user_request_id'] = self.user_request_id
            params['module_name'] = self.module_name

            print(f'Anthropic Proxy Client: Got request id {self.user_request_id} for module {self.module_name}')
        except Exception as e:
            print(f"Error getting tracking params: {str(e)}")
        return params

    def _should_retry(self, status_code: int, attempt: int) -> bool:
        """Check if we should retry based on status code and attempt number"""
        return status_code in self.retry_status_codes and attempt < self.max_retries
    
    def _get_retry_delay(self, attempt: int) -> float:
        """Calculate delay with exponential backoff and jitter"""
        delay = min(self.base_delay * (2 ** attempt), self.max_delay)
        # Add random jitter (±25%)
        jitter = delay * 0.25 * (2 * random.random() - 1)
        return max(0.1, delay + jitter)
    
    def _make_request_with_retry(self, request_func, *args, **kwargs):
        """Execute a request function with retry logic"""
        last_response = None
        
        for attempt in range(self.max_retries + 1):
            try:
                response = request_func(*args, **kwargs)
                
                # If successful, return immediately
                if response.status_code == 200:
                    return response
                
                # Store the response for potential retry logic
                last_response = response
                
                # Check if we should retry
                if self._should_retry(response.status_code, attempt):
                    delay = self._get_retry_delay(attempt)
                    self.logger.warning(
                        f"Request failed with status {response.status_code} "
                        f"(attempt {attempt + 1}/{self.max_retries + 1}). "
                        f"Retrying in {delay:.2f} seconds..."
                    )
                    time.sleep(delay)
                    continue
                else:
                    # No retry, break out of loop
                    break
                    
            except Exception as e:
                # For connection errors, retry if we have attempts left
                if attempt < self.max_retries:
                    delay = self._get_retry_delay(attempt)
                    self.logger.warning(
                        f"Request failed with exception: {str(e)} "
                        f"(attempt {attempt + 1}/{self.max_retries + 1}). "
                        f"Retrying in {delay:.2f} seconds..."
                    )
                    time.sleep(delay)
                    continue
                else:
                    # No more retries, re-raise the exception
                    raise
        
        # Return the last response if we didn't succeed
        return last_response
    
    
    def messages_create(
        self,
        model: str = cfg.ANTHROPIC_MODEL,
        max_tokens: int = int(cfg.ANTHROPIC_MAX_TOKENS),
        messages: List[Dict[str, Any]] = None,
        system: Optional[str] = None,
        temperature: float = 0.0,
        stream: bool = False
    ) -> Dict[str, Any]:
        """
        Send a message to Claude through the proxy.
        
        Args:
            model: Claude model to use
            max_tokens: Maximum number of tokens to generate
            messages: List of message objects in Claude format
            system: Optional system message
            temperature: Temperature for sampling (0-1)
            stream: Whether to stream the response
            
        Returns:
            Claude API response
        """
        try:
            payload = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": messages or []
            }
            
            if system:
                payload["system"] = system
                
            # Choose endpoint based on streaming
            endpoint = f"{self.proxy_base_url}{os.getenv('AI_HUB_PROXY_ANTHROPIC_MESSAGES')}"
            if stream:
                endpoint = f"{self.proxy_base_url}{os.getenv('AI_HUB_PROXY_ANTHROPIC_STREAM')}"

            # Add tracking parameters as query params
            tracking_params = self._get_tracking_params()

            # Define the request function for retry logic
            auth_headers = {**self._get_auth_headers(), "Content-Type": "application/json"}
            def make_request():
                return requests.post(
                    endpoint,
                    json=payload,
                    headers=auth_headers,
                    params=tracking_params,  # Add tracking params
                    timeout=cfg.DOC_API_REQUESTS_TIMEOUT
                )
            
            # Make the request with retry logic
            response = self._make_request_with_retry(make_request)

            # Handle response
            if response.status_code != 200:
                self.logger.error(f"Error from Anthropic proxy: {response.status_code} - {response.text}")
                return {
                    "error": f"Proxy returned status code {response.status_code}",
                    "details": response.text
                }
                
            if stream:
                return response  # Return the streaming response object
            else:
                return response.json()
                
        except Exception as e:
            self.logger.error(f"Error using Anthropic proxy: {str(e)}")
            return {"error": str(e)}
    
    def messages_with_document(
        self,
        file_path: str = "",
        file_bytes: bytes = None,
        user_text: str = "",
        model: str = cfg.ANTHROPIC_MODEL,
        max_tokens: int = int(cfg.ANTHROPIC_MAX_TOKENS),
        system: Optional[str] = None,
        temperature: float = 0.0
    ) -> Dict[str, Any]:
        """
        Send a message with a document to Claude through the proxy.
        
        Args:
            file_path: Path to document file
            user_text: Text prompt to include with document
            model: Claude model to use
            max_tokens: Maximum number of tokens to generate
            system: Optional system message
            temperature: Temperature for sampling (0-1)
            
        Returns:
            Claude API response
        """
        try:
            # Setup form data
            form_data = {
                "model": model,
                "max_tokens": str(max_tokens),
                "temperature": str(temperature),
                "text": user_text
            }
            
            if system:
                form_data["system"] = system
                
            # Setup file upload
            if file_path:
                files = {
                    'file': open(file_path, 'rb')
                }
            elif file_bytes:
                files = {
                    'file': file_bytes
                }
            else:
                return {
                    "error": f"No file path for bytes specified.",
                    "details": "Missing file input"
                }
            
            # Add tracking parameters as query params
            tracking_params = self._get_tracking_params()
                
            # Define the request function for retry logic
            auth_headers = self._get_auth_headers()
            def make_request():
                # Reopen file for each retry attempt if using file_path
                current_files = files
                if file_path:
                    current_files = {'file': open(file_path, 'rb')}

                try:
                    return requests.post(
                        f"{self.proxy_base_url}{self.document_proces_route}",
                        data=form_data,
                        files=current_files,
                        headers=auth_headers,
                        params=tracking_params  # Add tracking params
                    )
                finally:
                    # Close file if we opened it
                    if file_path and 'file' in current_files:
                        current_files['file'].close()
            
            # Make the request with retry logic
            response = self._make_request_with_retry(make_request)
            
            # Handle response
            if response.status_code != 200:
                self.logger.error(f"Error from Anthropic document proxy: {response.status_code} - {response.text}")
                return {
                    "error": f"Proxy returned status code {response.status_code}",
                    "details": response.text
                }
                
            return response.json()
                
        except Exception as e:
            self.logger.error(f"Error using Anthropic document proxy: {str(e)}")
            return {"error": str(e)}
        
    def messages_with_document_stream(
        self,
        file_path: str = "",
        file_bytes: bytes = None,
        user_text: str = "",
        model: str = cfg.ANTHROPIC_MODEL,
        max_tokens: int = int(cfg.ANTHROPIC_MAX_TOKENS),
        system: Optional[str] = None,
        temperature: float = 0.0
    ) -> Dict[str, Any]:
        """
        Send a message with a document to Claude through the proxy with streaming.
        Accumulates streamed chunks and returns the complete response.
        
        Returns:
            Dict with 'content', 'stop_reason', and 'usage' keys
        """
        try:
            # Setup form data - same as non-streaming but with stream=true
            form_data = {
                "model": model,
                "max_tokens": str(max_tokens),
                "temperature": str(temperature),
                "text": user_text,
                "stream": "true"  # Request streaming
            }
            
            if system:
                form_data["system"] = system
            
            # Prepare file
            files = None
            if file_path:
                files = {'file': open(file_path, 'rb')}
            elif file_bytes:
                files = {'file': ('document.pdf', file_bytes, 'application/pdf')}
            
            if not files:
                return {"error": "No file provided", "details": "Missing file input"}
            
            tracking_params = self._get_tracking_params()
            
            self.logger.info(f"Starting streaming document request: {file_path}")
            
            try:
                response = requests.post(
                    f"{self.proxy_base_url}{self.document_proces_route}",
                    data=form_data,
                    files=files,
                    headers=self._get_auth_headers(),
                    params=tracking_params,
                    stream=True,  # Enable response streaming
                    timeout=900   # 15 minute timeout for long operations
                )
            finally:
                if file_path and files and 'file' in files:
                    files['file'].close()
            
            if response.status_code != 200:
                self.logger.error(f"Error from streaming proxy: {response.status_code} - {response.text}")
                return {
                    "error": f"Proxy returned status code {response.status_code}",
                    "details": response.text
                }
            
            # Accumulate streamed content
            accumulated_text = []
            stop_reason = None
            usage = {}
            
            for line in response.iter_lines():
                if not line:
                    continue
                
                line = line.decode('utf-8')
                
                # Handle SSE format: "data: {...}"
                if line.startswith('data: '):
                    data_str = line[6:]  # Remove "data: " prefix
                    
                    if data_str == '[DONE]':
                        break
                    
                    try:
                        event = json.loads(data_str)
                        event_type = event.get('type')
                        
                        if event_type == 'content_block_delta':
                            delta = event.get('delta', {})
                            if delta.get('type') == 'text_delta':
                                text_chunk = delta.get('text', '')
                                accumulated_text.append(text_chunk)
                        
                        elif event_type == 'message_delta':
                            stop_reason = event.get('delta', {}).get('stop_reason')
                            usage = event.get('usage', usage)
                        
                        elif event_type == 'message_stop':
                            break
                        
                        elif event_type == 'error':
                            error_msg = event.get('error', 'Unknown streaming error')
                            self.logger.error(f"Stream error event: {error_msg}")
                            return {"error": error_msg, "details": "Error during streaming"}
                        
                    except json.JSONDecodeError:
                        self.logger.warning(f"Failed to parse SSE event: {line}")
                        continue
            
            # Build response in standard format
            full_text = ''.join(accumulated_text)
            
            self.logger.info(f"Streaming complete. Received {len(full_text)} characters. Stop reason: {stop_reason}")
            
            return {
                "content": [{"type": "text", "text": full_text}],
                "stop_reason": stop_reason,
                "usage": usage
            }
            
        except requests.exceptions.Timeout:
            self.logger.error("Request timed out during streaming")
            return {"error": "Request timed out", "details": "The streaming request exceeded the timeout limit"}
        except Exception as e:
            self.logger.error(f"Error in streaming document request: {str(e)}")
            return {"error": str(e)}
    
    def messages_with_document_data(
        self,
        file_data: BinaryIO,
        file_type: str,
        user_text: str = "",
        model: str = cfg.ANTHROPIC_MODEL,
        max_tokens: int = int(cfg.ANTHROPIC_MAX_TOKENS),
        system: Optional[str] = None,
        temperature: float = 0.0
    ) -> Dict[str, Any]:
        """
        Send a message with document data to Claude through the proxy.
        
        Args:
            file_data: File data as bytes or file-like object
            file_type: MIME type of the file (e.g., 'application/pdf')
            user_text: Text prompt to include with document
            model: Claude model to use
            max_tokens: Maximum number of tokens to generate
            system: Optional system message
            temperature: Temperature for sampling (0-1)
            
        Returns:
            Claude API response
        """
        try:
            # Setup form data
            form_data = {
                "model": model,
                "max_tokens": str(max_tokens),
                "temperature": str(temperature),
                "text": user_text
            }
            
            if system:
                form_data["system"] = system
                
            # Setup file upload
            files = {
                'file': ('document', file_data, file_type)
            }

            # Add tracking parameters as query params
            tracking_params = self._get_tracking_params()
                
            # Define the request function for retry logic
            auth_headers = self._get_auth_headers()
            def make_request():
                return requests.post(
                    f"{self.proxy_base_url}{self.document_proces_route}",
                    data=form_data,
                    files=files,
                    headers=auth_headers,
                    params=tracking_params  # Add tracking params
                )
            
            # Make the request with retry logic
            response = self._make_request_with_retry(make_request)
            
            # Handle response
            if response.status_code != 200:
                self.logger.error(f"Error from Anthropic document proxy: {response.status_code} - {response.text}")
                return {
                    "error": f"Proxy returned status code {response.status_code}",
                    "details": response.text
                }
                
            return response.json()
                
        except Exception as e:
            self.logger.error(f"Error using Anthropic document proxy: {str(e)}")
            return {"error": str(e)}
        

def build_filter_conditions(filters, table_alias, field_column, value_column, logic='OR'):
    """
    Build WHERE clause conditions and parameters for filtering.
    
    Args:
        filters: List of filter dictionaries with structure:
            {
                'field_name' or 'attribute_name': str,
                'operator': str,
                'value': str,
                'value2': str (optional, for between operations)
            }
        table_alias: SQL table alias (e.g., 'df' for DocumentFields, 'da' for DocumentAttributions)
        field_column: Column name for the field/attribute name (e.g., 'field_name', 'attribution_type')  
        value_column: Column name for the field/attribute value (e.g., 'field_value', 'attribution_value')
        logic: 'OR' or 'AND' - how to combine multiple filter conditions
        
    Returns:
        tuple: (where_clause_string, parameters_list) or (None, []) if no valid filters
    """
    if not filters:
        return None, []
        
    conditions = []
    params = []
    
    for filter_item in filters:
        # Handle both field filters and attribute filters
        field_name = filter_item.get('field_name') or filter_item.get('attribute_name', '')
        operator = filter_item.get('operator', 'equals')
        value = filter_item.get('value', '')
        value2 = filter_item.get('value2', '')
        
        # Skip if missing required fields
        if not field_name:
            continue
        
        # Handle operators that don't require a value
        if operator in ['is_null', 'is_not_null', 'exists', 'not_exists']:
            pass  # These don't need a value
        elif operator == 'between' and (not value or not value2):
            continue  # Between requires both values
        elif not value and operator not in ['is_null', 'is_not_null', 'exists', 'not_exists']:
            continue  # All other operators require at least one value

        # Determine cast type based on field name (for numeric/date operations)
        TRY_CAST_VALUE = 'FLOAT'
        if hasattr(cfg, 'DOC_DATE_FIELD_KEYWORDS') and any(keyword in str(field_name).lower() for keyword in cfg.DOC_DATE_FIELD_KEYWORDS):
            TRY_CAST_VALUE = 'DATE'

        # Build SQL condition based on operator
        try:
            if operator == 'equals':
                conditions.append(f"({table_alias}.{field_column} LIKE ? AND {table_alias}.{value_column} = ?)")
                params.extend([f'%{field_name}%', value])
                
            elif operator == 'not_equals':
                conditions.append(f"({table_alias}.{field_column} LIKE ? AND ({table_alias}.{value_column} != ? OR {table_alias}.{value_column} IS NULL))")
                params.extend([f'%{field_name}%', value])
                
            elif operator == 'contains':
                conditions.append(f"({table_alias}.{field_column} LIKE ? AND LOWER({table_alias}.{value_column}) LIKE LOWER(?))")
                params.extend([f'%{field_name}%', f'%{value}%'])
                
            elif operator == 'not_contains':
                conditions.append(f"({table_alias}.{field_column} LIKE ? AND (LOWER({table_alias}.{value_column}) NOT LIKE LOWER(?) OR {table_alias}.{value_column} IS NULL))")
                params.extend([f'%{field_name}%', f'%{value}%'])
                
            elif operator == 'starts_with':
                conditions.append(f"({table_alias}.{field_column} LIKE ? AND LOWER({table_alias}.{value_column}) LIKE LOWER(?))")
                params.extend([f'%{field_name}%', f'{value}%'])
                
            elif operator == 'ends_with':
                conditions.append(f"({table_alias}.{field_column} LIKE ? AND LOWER({table_alias}.{value_column}) LIKE LOWER(?))")
                params.extend([f'%{field_name}%', f'%{value}'])
                
            # Numeric/Date comparison operators
            elif operator == 'greater_than':
                conditions.append(f"({table_alias}.{field_column} LIKE ? AND TRY_CAST({table_alias}.{value_column} AS {TRY_CAST_VALUE}) > TRY_CAST(? AS {TRY_CAST_VALUE}) AND TRY_CAST({table_alias}.{value_column} AS {TRY_CAST_VALUE}) IS NOT NULL)")
                params.extend([f'%{field_name}%', value])
                
            elif operator == 'greater_than_equal':
                conditions.append(f"({table_alias}.{field_column} LIKE ? AND TRY_CAST({table_alias}.{value_column} AS {TRY_CAST_VALUE}) >= TRY_CAST(? AS {TRY_CAST_VALUE}) AND TRY_CAST({table_alias}.{value_column} AS {TRY_CAST_VALUE}) IS NOT NULL)")
                params.extend([f'%{field_name}%', value])
                
            elif operator == 'less_than':
                conditions.append(f"({table_alias}.{field_column} LIKE ? AND TRY_CAST({table_alias}.{value_column} AS {TRY_CAST_VALUE}) < TRY_CAST(? AS {TRY_CAST_VALUE}) AND TRY_CAST({table_alias}.{value_column} AS {TRY_CAST_VALUE}) IS NOT NULL)")
                params.extend([f'%{field_name}%', value])
                
            elif operator == 'less_than_equal':
                conditions.append(f"({table_alias}.{field_column} LIKE ? AND TRY_CAST({table_alias}.{value_column} AS {TRY_CAST_VALUE}) <= TRY_CAST(? AS {TRY_CAST_VALUE}) AND TRY_CAST({table_alias}.{value_column} AS {TRY_CAST_VALUE}) IS NOT NULL)")
                params.extend([f'%{field_name}%', value])
                
            elif operator == 'between':
                conditions.append(f"({table_alias}.{field_column} LIKE ? AND TRY_CAST({table_alias}.{value_column} AS {TRY_CAST_VALUE}) BETWEEN TRY_CAST(? AS {TRY_CAST_VALUE}) AND TRY_CAST(? AS {TRY_CAST_VALUE}) AND TRY_CAST({table_alias}.{value_column} AS {TRY_CAST_VALUE}) IS NOT NULL)")
                params.extend([f'%{field_name}%', value, value2])
                
            elif operator == 'in':
                values_list = [v.strip() for v in value.split(',') if v.strip()]
                if values_list:
                    placeholders = ', '.join(['?' for _ in values_list])
                    conditions.append(f"({table_alias}.{field_column} LIKE ? AND {table_alias}.{value_column} IN ({placeholders}))")
                    params.extend([f'%{field_name}%'] + values_list)
                    
            elif operator == 'not_in':
                values_list = [v.strip() for v in value.split(',') if v.strip()]
                if values_list:
                    placeholders = ', '.join(['?' for _ in values_list])
                    conditions.append(f"({table_alias}.{field_column} LIKE ? AND ({table_alias}.{value_column} NOT IN ({placeholders}) OR {table_alias}.{value_column} IS NULL))")
                    params.extend([f'%{field_name}%'] + values_list)
                    
            elif operator == 'is_null':
                conditions.append(f"({table_alias}.{field_column} LIKE ? AND ({table_alias}.{value_column} IS NULL OR {table_alias}.{value_column} = ''))")
                params.extend([f'%{field_name}%'])
                
            elif operator == 'is_not_null':
                conditions.append(f"({table_alias}.{field_column} LIKE ? AND {table_alias}.{value_column} IS NOT NULL AND {table_alias}.{value_column} != '')")
                params.extend([f'%{field_name}%'])
                
            elif operator == 'regex':
                conditions.append(f"({table_alias}.{field_column} LIKE ? AND {table_alias}.{value_column} LIKE ?)")
                params.extend([f'%{field_name}%', value])
                
            elif operator == 'length_equals':
                conditions.append(f"({table_alias}.{field_column} LIKE ? AND LEN({table_alias}.{value_column}) = TRY_CAST(? AS INT) AND TRY_CAST(? AS INT) IS NOT NULL)")
                params.extend([f'%{field_name}%', value, value])
                
            elif operator == 'length_greater':
                conditions.append(f"({table_alias}.{field_column} LIKE ? AND LEN({table_alias}.{value_column}) > TRY_CAST(? AS INT) AND TRY_CAST(? AS INT) IS NOT NULL)")
                params.extend([f'%{field_name}%', value, value])
                
            elif operator == 'length_less':
                conditions.append(f"({table_alias}.{field_column} LIKE ? AND LEN({table_alias}.{value_column}) < TRY_CAST(? AS INT) AND TRY_CAST(? AS INT) IS NOT NULL)")
                params.extend([f'%{field_name}%', value, value])
                
            elif operator == 'exists':
                conditions.append(f"({table_alias}.{field_column} LIKE ?)")
                params.extend([f'%{field_name}%'])
                
            elif operator == 'not_exists':
                # Mark for special handling - caller needs to handle this differently
                conditions.append(f"__NOT_EXISTS__{field_name}")
                params.extend([f'%{field_name}%'])
                
            else:
                print(f"Warning: Unknown operator '{operator}' for field '{field_name}', skipping...")
                continue
                
        except Exception as operator_error:
            print(f"Error processing operator '{operator}' for field '{field_name}': {str(operator_error)}")
            continue
    
    if not conditions:
        return None, []
    
    # Join conditions with specified logic
    where_clause = f" ({f' {logic} '.join(conditions)})"
    
    return where_clause, params


def rotate_logs_on_startup(log_file):
    """
    Check and rotate log files at application startup
    to avoid permission issues during runtime
    """
    try:
        import datetime
        
        # Get rotation settings
        max_bytes = getattr(cfg, 'LOG_MAX_BYTES', 1 * 1024 * 1024)  # 1 MB default
        backup_count = getattr(cfg, 'LOG_BACKUP_COUNT', 5)          # 5 backups default
        
        # Create log directory if it doesn't exist
        log_dir = os.path.dirname(log_file)
        if not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        
        # Check if current log file is too large
        if os.path.exists(log_file) and os.path.getsize(log_file) > max_bytes:
            print(f"Rotating log file {log_file} at startup")
            
            # Delete the oldest log file if it exists
            oldest_log = f"{log_file}.{backup_count}"
            if os.path.exists(oldest_log):
                os.remove(oldest_log)
            
            # Shift all existing log files up by one
            for i in range(backup_count-1, 0, -1):
                src = f"{log_file}.{i}"
                dst = f"{log_file}.{i+1}"
                if os.path.exists(src):
                    # Ensure destination file doesn't exist
                    if os.path.exists(dst):
                        os.remove(dst)
                    os.rename(src, dst)
            
            # Rename the current log file
            if os.path.exists(log_file):
                # Ensure destination file doesn't exist
                if os.path.exists(f"{log_file}.1"):
                    os.remove(f"{log_file}.1")
                os.rename(log_file, f"{log_file}.1")
            
            # Create an empty log file
            try:
                with open(log_file, 'w') as f:
                    f.write(f"Log file rotated at {datetime.datetime.now().isoformat()}\n")
            except:
                with open(log_file, 'w') as f:
                    f.write(f"Log file rotated at {datetime.now().isoformat()}\n")
                
            print(f"Log rotation complete")
    except Exception as e:
        print(f"Error rotating logs: {str(e)}")



def get_all_node_details() -> str:
    """
    Returns complete documentation for all workflow node types as a single string.
    Used for system prompts and comprehensive reference.
    
    Returns:
        Complete node configuration documentation.
    """
    return "\n\n".join(NODE_DETAIL_REFERENCE.values())


def get_node_details(node_types: str) -> str:
    """
    Get detailed configuration information for specific workflow node types.
    Use this when you need to understand the exact config fields and options for planning.
    
    Args:
        node_types: Single node type or string with comma-separated list of node types.
                   Examples: "AI Extract" or "Database, Loop, Conditional"
                   Use "all" to get all node types.
    
    Returns:
        Detailed configuration documentation for the requested node types.
    """
    # Handle "all" request
    if node_types.strip().lower() == "all":
        return get_all_node_details()
    
    # Parse the input
    requested = [n.strip() for n in node_types.split(",")]
    
    results = []
    not_found = []
    
    for node_type in requested:
        # Try exact match first
        if node_type in NODE_DETAIL_REFERENCE:
            results.append(NODE_DETAIL_REFERENCE[node_type])
        else:
            # Try case-insensitive match
            matched = False
            for key in NODE_DETAIL_REFERENCE:
                if key.lower() == node_type.lower():
                    results.append(NODE_DETAIL_REFERENCE[key])
                    matched = True
                    break
            if not matched:
                not_found.append(node_type)
    
    output = "\n\n".join(results)
    
    if not_found:
        output += f"\n\nNOTE: The following node types were not found: {', '.join(not_found)}"
        output += f"\nAvailable node types: {', '.join(NODE_DETAIL_REFERENCE.keys())}"
    
    if not results:
        output = f"No matching node types found. Available types: {', '.join(NODE_DETAIL_REFERENCE.keys())}"
    
    return output


# =============================================================================
# Runtime Tracking
# =============================================================================
class Timer:
    """Simple timer for measuring execution time."""
    
    def __init__(self):
        self.start_time = None
        self.end_time = None
    
    def start(self):
        self.start_time = time.time()
        return self
    
    def stop(self):
        self.end_time = time.time()
        return self
    
    @property
    def elapsed_ms(self) -> float:
        """Elapsed time in milliseconds."""
        if self.start_time is None:
            return 0
        end = self.end_time or time.time()
        return (end - self.start_time) * 1000
    
    @property
    def elapsed_seconds(self) -> float:
        """Elapsed time in seconds."""
        return self.elapsed_ms / 1000
    