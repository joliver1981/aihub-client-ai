"""
Local Secrets Manager for AI Hub
=================================

Stores API keys and credentials locally on the user's machine.
Secrets NEVER leave the local environment.

Security Model:
- Secrets stored in encrypted JSON file
- Encryption key derived from machine ID + app salt
- Secrets never transmitted to cloud
- User has full control over their credentials

Usage:
    from local_secrets import get_local_secret, set_local_secret
    
    # In custom tools
    api_key = get_local_secret('OPENWEATHERMAP_API_KEY')
    
    # To store a secret
    set_local_secret('OPENWEATHERMAP_API_KEY', 'your-key-here', 'Weather API key')
"""

import os
import json
import base64
import hashlib
import uuid
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

# Cryptography imports
try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    print("Warning: cryptography package not installed. Secrets will use base64 encoding only.")

logger = logging.getLogger(__name__)


class LocalSecretsManager:
    """
    Manages locally-stored secrets with encryption.
    
    Security Model:
    - Secrets stored in encrypted JSON file on local machine
    - Encryption key derived from machine ID + app salt
    - Secrets NEVER transmitted to cloud
    - User can view/export their own secrets
    
    File Structure:
        /data/secrets/
            secrets.json.enc    # Encrypted secrets
            .machine_id         # Machine-specific encryption key seed
    """
    
    # App-specific salt for key derivation (not secret, just unique to app)
    APP_SALT = b'aihub_local_secrets_v1_2025'
    
    def __init__(self, data_dir: str = None):
        """
        Initialize the secrets manager.
        
        Args:
            data_dir: Base data directory. Defaults to ./data or AIHUB_DATA_DIR env var
        """
        # Use APP_ROOT env var for PyInstaller compatibility (set by installer)
        default_base = os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__)))
        self.data_dir = Path(data_dir or os.path.join(default_base, 'data'))
        self.secrets_dir = self.data_dir / 'secrets'
        self.secrets_file = self.secrets_dir / 'secrets.json.enc'
        self.machine_id_file = self.secrets_dir / '.machine_id'
        
        # Ensure directory exists with restricted permissions
        self._ensure_secure_directory()
        
        # Initialize encryption
        self._fernet = self._get_fernet() if CRYPTO_AVAILABLE else None
        
        # Cache for performance (secrets don't change often)
        self._cache = None
        self._cache_time = None
        self._cache_ttl = 5  # seconds
    
    def _ensure_secure_directory(self):
        """Create secrets directory with appropriate permissions."""
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        
        # On Unix, restrict directory permissions
        if os.name != 'nt':
            try:
                os.chmod(self.secrets_dir, 0o700)
            except Exception as e:
                logger.warning(f"Could not set directory permissions: {e}")
    
    def _get_machine_id(self) -> str:
        """
        Get or create a machine-specific ID for encryption.
        
        This ID is unique to the machine and used as part of the encryption key.
        It's stored locally and never transmitted anywhere.
        """
        if self.machine_id_file.exists():
            return self.machine_id_file.read_text().strip()
        
        # Generate new machine ID combining multiple sources for uniqueness
        unique_parts = [
            str(uuid.uuid4()),  # Random UUID
            str(uuid.getnode()),  # MAC address based
            os.name,  # OS type
        ]
        machine_id = hashlib.sha256('|'.join(unique_parts).encode()).hexdigest()[:32]
        
        # Save it
        self.machine_id_file.write_text(machine_id)
        
        # Make it hidden on Windows
        if os.name == 'nt':
            try:
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(
                    str(self.machine_id_file), 0x02  # FILE_ATTRIBUTE_HIDDEN
                )
            except Exception:
                pass
        
        return machine_id
    
    def _get_fernet(self) -> 'Fernet':
        """Create Fernet instance with machine-derived key."""
        if not CRYPTO_AVAILABLE:
            return None
            
        machine_id = self._get_machine_id()
        
        # Derive encryption key from machine ID using PBKDF2
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self.APP_SALT,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(machine_id.encode()))
        return Fernet(key)
    
    def _encrypt(self, data: str) -> bytes:
        """Encrypt data."""
        if self._fernet:
            return self._fernet.encrypt(data.encode())
        else:
            # Fallback to base64 (not secure, but functional)
            return base64.b64encode(data.encode())
    
    def _decrypt(self, data: bytes) -> str:
        """Decrypt data."""
        if self._fernet:
            try:
                return self._fernet.decrypt(data).decode()
            except InvalidToken:
                logger.error("Failed to decrypt secrets - invalid key or corrupted file")
                raise ValueError("Cannot decrypt secrets file. It may be corrupted or from a different machine.")
        else:
            return base64.b64decode(data).decode()
    
    def _load_secrets(self, use_cache: bool = True) -> dict:
        """Load and decrypt secrets from file."""
        # Check cache
        if use_cache and self._cache is not None:
            if self._cache_time and (datetime.now() - self._cache_time).seconds < self._cache_ttl:
                return self._cache
        
        if not self.secrets_file.exists():
            return {}
        
        try:
            encrypted_data = self.secrets_file.read_bytes()
            decrypted_data = self._decrypt(encrypted_data)
            secrets = json.loads(decrypted_data)
            
            # Update cache
            self._cache = secrets
            self._cache_time = datetime.now()
            
            return secrets
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in secrets file: {e}")
            return {}
        except Exception as e:
            logger.error(f"Error loading secrets: {e}")
            raise
    
    def _save_secrets(self, secrets: dict):
        """Encrypt and save secrets to file."""
        try:
            json_data = json.dumps(secrets, indent=2, sort_keys=True)
            encrypted_data = self._encrypt(json_data)
            
            # Write atomically (write to temp, then rename)
            temp_file = self.secrets_file.with_suffix('.tmp')
            temp_file.write_bytes(encrypted_data)
            temp_file.replace(self.secrets_file)
            
            # Restrict file permissions on Unix
            if os.name != 'nt':
                try:
                    os.chmod(self.secrets_file, 0o600)
                except Exception:
                    pass
            
            # Invalidate cache
            self._cache = secrets
            self._cache_time = datetime.now()
            
        except Exception as e:
            logger.error(f"Error saving secrets: {e}")
            raise
    
    # =========================================================================
    # Public API
    # =========================================================================
    
    def get(self, name: str, default: str = None) -> Optional[str]:
        """
        Get a secret by name.
        
        Args:
            name: Secret name (e.g., 'OPENWEATHERMAP_API_KEY')
            default: Default value if not found
            
        Returns:
            Secret value or default
        """
        secrets = self._load_secrets()
        secret_data = secrets.get(name.upper())
        
        if secret_data is None:
            return default
        
        return secret_data.get('value', default)
    
    def set(self, name: str, value: str, description: str = None, category: str = None):
        """
        Store a secret locally.
        
        Args:
            name: Secret name (will be uppercased)
            value: Secret value
            description: Optional description for documentation
            category: Optional category (e.g., 'api_keys', 'credentials')
        """
        name = name.upper().strip()
        
        if not name:
            raise ValueError("Secret name cannot be empty")
        
        if not name.replace('_', '').isalnum():
            raise ValueError("Secret name must be alphanumeric with underscores only")
        
        secrets = self._load_secrets(use_cache=False)
        
        # Preserve creation date if updating
        existing = secrets.get(name, {})
        
        secrets[name] = {
            'value': value,
            'description': description or existing.get('description', ''),
            'category': category or existing.get('category', 'api_keys'),
            'created': existing.get('created', self._now()),
            'updated': self._now()
        }
        
        self._save_secrets(secrets)
        logger.info(f"Secret '{name}' saved to local storage")
    
    def delete(self, name: str) -> bool:
        """
        Delete a secret.
        
        Args:
            name: Secret name to delete
            
        Returns:
            True if deleted, False if not found
        """
        name = name.upper().strip()
        secrets = self._load_secrets(use_cache=False)
        
        if name in secrets:
            del secrets[name]
            self._save_secrets(secrets)
            logger.info(f"Secret '{name}' deleted from local storage")
            return True
        
        return False
    
    def list(self, category: str = None, include_value: bool = False) -> List[Dict[str, Any]]:
        """
        List all secrets (metadata only by default).
        
        Args:
            category: Optional filter by category
            include_value: If True, include the actual values (use with caution)
            
        Returns:
            List of dicts with name, description, category, updated, has_value
        """
        secrets = self._load_secrets()
        result = []
        
        for name, data in secrets.items():
            if category and data.get('category') != category:
                continue
            
            item = {
                'name': name,
                'description': data.get('description', ''),
                'category': data.get('category', 'api_keys'),
                'created': data.get('created', ''),
                'updated': data.get('updated', ''),
                'has_value': bool(data.get('value'))
            }
            
            if include_value:
                item['value'] = data.get('value', '')
            
            result.append(item)
        
        return sorted(result, key=lambda x: x['name'])
    
    def exists(self, name: str) -> bool:
        """Check if a secret exists and has a value."""
        name = name.upper().strip()
        secrets = self._load_secrets()
        return name in secrets and bool(secrets[name].get('value'))
    
    def get_categories(self) -> List[str]:
        """Get list of all categories in use."""
        secrets = self._load_secrets()
        categories = set()
        for data in secrets.values():
            categories.add(data.get('category', 'api_keys'))
        return sorted(categories)
    
    def export_template(self) -> dict:
        """
        Export a template showing required secrets (names only, no values).
        Useful for sharing setup requirements.
        """
        secrets = self._load_secrets()
        return {
            name: {
                'description': data.get('description', ''),
                'category': data.get('category', 'api_keys'),
                'value': ''  # Empty - user fills in
            }
            for name, data in secrets.items()
        }
    
    def import_secrets(self, secrets_dict: dict, overwrite: bool = False):
        """
        Import secrets from a dict.
        
        Args:
            secrets_dict: Dict of {name: {value, description, category}}
            overwrite: If True, overwrite existing secrets
        """
        current = self._load_secrets(use_cache=False)
        
        for name, data in secrets_dict.items():
            name = name.upper().strip()
            
            if name in current and not overwrite:
                continue
            
            if isinstance(data, str):
                # Simple format: {name: value}
                self.set(name, data)
            elif isinstance(data, dict):
                # Full format: {name: {value, description, category}}
                self.set(
                    name,
                    data.get('value', ''),
                    data.get('description'),
                    data.get('category')
                )
    
    def get_for_environment(self, names: List[str] = None) -> Dict[str, str]:
        """
        Get secrets as a dict suitable for environment variables.
        
        Args:
            names: Optional list of secret names. If None, returns all.
            
        Returns:
            Dict of {name: value}
        """
        secrets = self._load_secrets()
        
        if names:
            return {
                name.upper(): secrets.get(name.upper(), {}).get('value', '')
                for name in names
                if secrets.get(name.upper(), {}).get('value')
            }
        else:
            return {
                name: data.get('value', '')
                for name, data in secrets.items()
                if data.get('value')
            }
    
    def get_storage_info(self) -> dict:
        """Get information about the secrets storage for display."""
        return {
            'location': str(self.secrets_file.absolute()),
            'directory': str(self.secrets_dir.absolute()),
            'encrypted': CRYPTO_AVAILABLE,
            'encryption_method': 'Fernet (AES-128-CBC)' if CRYPTO_AVAILABLE else 'Base64 only',
            'exists': self.secrets_file.exists(),
            'count': len(self._load_secrets()),
            'cloud_sync': False,
            'machine_bound': True
        }
    
    def _now(self) -> str:
        """Current timestamp as ISO string."""
        return datetime.now().isoformat()


# =============================================================================
# Global Instance
# =============================================================================

_secrets_manager: Optional[LocalSecretsManager] = None


def get_secrets_manager(data_dir: str = None) -> LocalSecretsManager:
    """Get or create the global secrets manager instance."""
    global _secrets_manager
    if _secrets_manager is None:
        _secrets_manager = LocalSecretsManager(data_dir)
    return _secrets_manager


def reset_secrets_manager():
    """Reset the global instance (useful for testing)."""
    global _secrets_manager
    _secrets_manager = None


# =============================================================================
# Convenience Functions for Tool Code
# =============================================================================

def get_local_secret(name: str, default: str = '') -> str:
    """
    Get a locally-stored secret.
    
    This function is available to custom tools. Secrets are stored
    locally on your machine and never transmitted to the cloud.
    
    Args:
        name: The secret name (e.g., 'OPENWEATHERMAP_API_KEY')
        default: Default value if secret not found
        
    Returns:
        The secret value, or default if not found
        
    Example:
        api_key = get_local_secret('OPENWEATHERMAP_API_KEY')
    """
    return get_secrets_manager().get(name, default)


def set_local_secret(name: str, value: str, description: str = None, category: str = None):
    """
    Store a secret locally.
    
    Args:
        name: Secret name (e.g., 'OPENWEATHERMAP_API_KEY')
        value: The secret value
        description: Optional description
        category: Optional category for organization
    """
    get_secrets_manager().set(name, value, description, category)


def has_local_secret(name: str) -> bool:
    """
    Check if a local secret exists.
    
    Args:
        name: The secret name to check
        
    Returns:
        True if the secret exists and has a value
    """
    return get_secrets_manager().exists(name)


def list_local_secrets() -> List[Dict[str, Any]]:
    """
    List all local secrets (names and metadata only, not values).
    
    Returns:
        List of secret metadata
    """
    return get_secrets_manager().list()


# =============================================================================
# Tool Execution Integration
# =============================================================================

class SecretsEnvironmentContext:
    """
    Context manager that injects secrets as environment variables.
    
    Usage:
        with SecretsEnvironmentContext(['OPENWEATHERMAP_API_KEY', 'SENDGRID_API_KEY']):
            # Secrets available as os.environ['SECRET_NAME']
            run_tool()
        # Environment restored to original state
    """
    
    def __init__(self, secret_names: List[str] = None, inject_all: bool = False):
        """
        Args:
            secret_names: List of secret names to inject
            inject_all: If True, inject all secrets (use with caution)
        """
        self.secret_names = secret_names
        self.inject_all = inject_all
        self._original_env = {}
        self._injected_keys = []
    
    def __enter__(self):
        manager = get_secrets_manager()
        
        if self.inject_all:
            secrets = manager.get_for_environment()
        elif self.secret_names:
            secrets = manager.get_for_environment(self.secret_names)
        else:
            secrets = {}
        
        # Save original values and inject secrets
        for name, value in secrets.items():
            self._original_env[name] = os.environ.get(name)
            os.environ[name] = value
            self._injected_keys.append(name)
        
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        # Restore original environment
        for name in self._injected_keys:
            original = self._original_env.get(name)
            if original is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original
        
        return False  # Don't suppress exceptions


def inject_secrets_to_environment(secret_names: List[str] = None) -> Dict[str, str]:
    """
    Inject secrets into os.environ.
    
    Args:
        secret_names: List of secret names. If None, injects all.
        
    Returns:
        Dict of original values for restoration
    """
    manager = get_secrets_manager()
    secrets = manager.get_for_environment(secret_names)
    
    original = {}
    for name, value in secrets.items():
        original[name] = os.environ.get(name)
        os.environ[name] = value
    
    return original


def restore_environment(original: Dict[str, str]):
    """Restore environment variables to their original values."""
    for name, value in original.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
