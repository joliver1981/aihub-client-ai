"""
Connection Secrets Integration
==============================

Integrates local secrets storage with database connections.
Passwords are stored locally, only references are stored in cloud database.

How it works:
- When saving a connection, password is stored in local secrets
- Cloud database stores a reference like: {{LOCAL_SECRET:CONN_PWD_123}}
- When connection is used, reference is replaced with actual password
- User experience is unchanged - they enter password in the same form

Security benefits:
- Passwords never stored in cloud database
- Passwords never transmitted over network
- Only the local machine can access the actual credentials
"""

import re
import logging
from typing import Optional, Tuple, Dict, Any

from local_secrets import get_secrets_manager, get_local_secret, set_local_secret

logger = logging.getLogger(__name__)

# Pattern for password references stored in database
SECRET_REF_PATTERN = r'\{\{LOCAL_SECRET:([A-Za-z0-9_]+)\}\}'

# Prefix for connection password secrets
CONN_SECRET_PREFIX = 'CONN_PWD_'


def get_connection_secret_name(connection_id: int) -> str:
    """
    Generate the secret name for a connection's password.
    
    Args:
        connection_id: The database connection ID
        
    Returns:
        Secret name like 'CONN_PWD_123'
    """
    return f"{CONN_SECRET_PREFIX}{connection_id}"


def is_secret_reference(value: str) -> bool:
    """
    Check if a value is a local secret reference.
    
    Args:
        value: The value to check
        
    Returns:
        True if it's a reference like {{LOCAL_SECRET:NAME}}
    """
    if not value:
        return False
    return bool(re.match(SECRET_REF_PATTERN, value.strip()))


def create_secret_reference(secret_name: str) -> str:
    """
    Create a secret reference string to store in database.
    
    Args:
        secret_name: The name of the local secret
        
    Returns:
        Reference string like '{{LOCAL_SECRET:CONN_PWD_123}}'
    """
    return f"{{{{LOCAL_SECRET:{secret_name}}}}}"


def extract_secret_name(reference: str) -> Optional[str]:
    """
    Extract the secret name from a reference string.
    
    Args:
        reference: String like '{{LOCAL_SECRET:CONN_PWD_123}}'
        
    Returns:
        Secret name like 'CONN_PWD_123' or None if not a reference
    """
    match = re.match(SECRET_REF_PATTERN, reference.strip() if reference else '')
    return match.group(1) if match else None


# =============================================================================
# Connection Save/Load Integration
# =============================================================================

def store_connection_password(connection_id: int, password: str, connection_name: str = None) -> str:
    """
    Store a connection password in local secrets and return the reference.
    
    Call this when saving a connection BEFORE storing in database.
    
    Args:
        connection_id: The connection ID (use 0 for new connections, update after insert)
        password: The actual password to store
        connection_name: Optional name for description
        
    Returns:
        Reference string to store in database instead of password
    """
    if not password:
        return ''
    
    # Don't re-store if it's already a reference
    if is_secret_reference(password):
        return password
    
    secret_name = get_connection_secret_name(connection_id)
    description = f"Password for connection: {connection_name}" if connection_name else f"Connection {connection_id} password"
    
    # Store in local secrets
    manager = get_secrets_manager()
    manager.set(secret_name, password, description, 'connection_passwords')
    
    logger.info(f"Stored password for connection {connection_id} in local secrets")
    
    # Return the reference to store in database
    return create_secret_reference(secret_name)


def retrieve_connection_password(password_field: str) -> str:
    """
    Retrieve the actual password from a reference or return as-is.
    
    BACKWARD COMPATIBLE: Works with both:
    - New style: {{LOCAL_SECRET:CONN_PWD_123}} -> resolves from local secrets
    - Legacy style: actual plain text password -> returns as-is
    
    Call this when you need to USE the connection (test, query, etc.)
    
    Args:
        password_field: Either the actual password or a reference
        
    Returns:
        The actual password (resolved if reference, or as-is if legacy)
    """
    if not password_field:
        return ''
    
    # Check if it's a reference (new style)
    secret_name = extract_secret_name(password_field)
    if secret_name:
        # It's a reference - retrieve from local secrets
        password = get_local_secret(secret_name, '')
        if not password:
            logger.warning(f"Password secret '{secret_name}' not found in local secrets")
        return password
    
    # Not a reference - it's a legacy plain text password
    # Return as-is for backward compatibility
    return password_field


def update_connection_secret_id(old_id: int, new_id: int) -> bool:
    """
    Update the secret name when a new connection gets its ID after insert.
    
    Call this after inserting a new connection when you get the actual ID.
    
    Args:
        old_id: The temporary ID (usually 0)
        new_id: The actual database ID
        
    Returns:
        True if updated successfully
    """
    old_secret_name = get_connection_secret_name(old_id)
    new_secret_name = get_connection_secret_name(new_id)
    
    manager = get_secrets_manager()
    
    # Get the old secret
    password = manager.get(old_secret_name)
    if not password:
        return False
    
    # Get metadata
    secrets = manager.list()
    old_secret = next((s for s in secrets if s['name'] == old_secret_name), None)
    description = old_secret.get('description', '') if old_secret else ''
    
    # Create new secret with correct ID
    manager.set(new_secret_name, password, description.replace(f"connection {old_id}", f"connection {new_id}"), 'connection_passwords')
    
    # Delete old secret
    manager.delete(old_secret_name)
    
    logger.info(f"Updated connection secret from {old_secret_name} to {new_secret_name}")
    return True


def delete_connection_secret(connection_id: int) -> bool:
    """
    Delete the password secret when a connection is deleted.
    
    Args:
        connection_id: The connection ID being deleted
        
    Returns:
        True if deleted successfully
    """
    secret_name = get_connection_secret_name(connection_id)
    manager = get_secrets_manager()
    
    if manager.delete(secret_name):
        logger.info(f"Deleted password secret for connection {connection_id}")
        return True
    return False


# =============================================================================
# Connection String Processing
# =============================================================================

def resolve_connection_string_secrets(connection_string: str) -> str:
    """
    Replace any secret references in a connection string with actual values.
    
    This handles cases where passwords are embedded in connection strings.
    
    Args:
        connection_string: The connection string possibly containing references
        
    Returns:
        Connection string with actual values
    """
    if not connection_string:
        return connection_string
    
    def replace_reference(match):
        secret_name = match.group(1)
        value = get_local_secret(secret_name, '')
        if not value:
            logger.warning(f"Secret '{secret_name}' not found when resolving connection string")
        return value
    
    return re.sub(SECRET_REF_PATTERN, replace_reference, connection_string)


def mask_connection_password(connection_data: dict) -> dict:
    """
    Mask the password field for safe display/transmission.
    
    BACKWARD COMPATIBLE: Handles both:
    - New style: {{LOCAL_SECRET:CONN_PWD_123}} -> checks if secret exists
    - Legacy style: plain text password -> masks it
    
    Args:
        connection_data: Dict containing connection fields
        
    Returns:
        Copy of dict with password masked and metadata added
    """
    result = connection_data.copy()
    
    if 'password' in result and result['password']:
        password = result['password']
        
        if is_secret_reference(password):
            # NEW STYLE: It's a reference to local secrets
            secret_name = extract_secret_name(password)
            manager = get_secrets_manager()
            if manager.exists(secret_name):
                result['password'] = '••••••••'  # Masked indicator
                result['_password_local'] = True   # Flag: stored in local secrets
                result['_password_type'] = 'local_secret'
            else:
                # Reference exists but secret is missing (maybe different machine)
                result['password'] = ''
                result['_password_local'] = False
                result['_password_type'] = 'missing_secret'
                result['_password_warning'] = 'Password not found in local secrets. You may need to re-enter it.'
        else:
            # LEGACY: Actual plain text password in database
            result['password'] = '••••••••'  # Still mask it for display
            result['_password_local'] = False  # Flag: NOT in local secrets (legacy)
            result['_password_type'] = 'legacy_database'
    else:
        result['_password_local'] = False
        result['_password_type'] = 'none'
    
    return result


# =============================================================================
# Batch Operations
# =============================================================================

def process_connection_for_save(connection_data: dict) -> dict:
    """
    Process connection data before saving to database.
    Stores password in local secrets and replaces with reference.
    
    Args:
        connection_data: Dict with connection fields including 'password'
        
    Returns:
        Modified dict with password reference
    """
    result = connection_data.copy()
    connection_id = result.get('connection_id', 0)
    connection_name = result.get('connection_name', '')
    password = result.get('password', '')
    
    # Handle password
    if password and not is_secret_reference(password):
        # It's a real password, store it locally
        result['password'] = store_connection_password(
            connection_id, 
            password, 
            connection_name
        )
    elif password == '••••••••' or password == '':
        # Password unchanged or empty - keep existing reference if updating
        if connection_id and connection_id != 0:
            # For updates, we need to preserve the existing reference
            # The caller should handle this by not including password in the update
            # or by fetching the existing reference
            result['_password_unchanged'] = True
    
    return result


def process_connection_for_use(connection_data: dict) -> dict:
    """
    Process connection data when it needs to be used (test, query).
    Resolves password references to actual values.
    
    Args:
        connection_data: Dict with connection fields
        
    Returns:
        Modified dict with actual password
    """
    result = connection_data.copy()
    
    # Resolve password
    if 'password' in result:
        result['password'] = retrieve_connection_password(result['password'])
    
    # Resolve any secrets in connection string
    if 'connection_string' in result:
        result['connection_string'] = resolve_connection_string_secrets(result['connection_string'])
    
    return result


def process_connections_for_display(connections: list) -> list:
    """
    Process a list of connections for display to user.
    Masks passwords and adds local storage indicators.
    
    Args:
        connections: List of connection dicts
        
    Returns:
        List with masked passwords
    """
    return [mask_connection_password(conn) for conn in connections]


# =============================================================================
# Migration Helper
# =============================================================================

def get_password_storage_type(password_field: str) -> str:
    """
    Determine how a password is stored.
    
    Returns:
        'local_secret' - Stored in local secrets (new style)
        'legacy_database' - Plain text in database (legacy)
        'empty' - No password set
    """
    if not password_field:
        return 'empty'
    
    if is_secret_reference(password_field):
        return 'local_secret'
    
    return 'legacy_database'


def should_migrate_password(password_field: str) -> bool:
    """
    Check if a password should be migrated from legacy to local secrets.
    
    Returns True if password is plain text (legacy style).
    """
    if not password_field:
        return False
    return not is_secret_reference(password_field)


def migrate_existing_password(connection_id: int, current_password: str, connection_name: str = None) -> str:
    """
    Migrate an existing password from database to local secrets.
    
    Use this for one-time migration of existing connections.
    
    Args:
        connection_id: The connection ID
        current_password: The password currently stored in database
        connection_name: Optional connection name for description
        
    Returns:
        The reference to update in database, or empty if no migration needed
    """
    if not current_password:
        return ''
    
    # Already migrated
    if is_secret_reference(current_password):
        return current_password
    
    # Migrate to local secrets
    return store_connection_password(connection_id, current_password, connection_name)


def check_password_availability(connection_id: int) -> dict:
    """
    Check if a connection's password is available in local secrets.
    
    Args:
        connection_id: The connection ID
        
    Returns:
        Dict with status information
    """
    secret_name = get_connection_secret_name(connection_id)
    manager = get_secrets_manager()
    
    return {
        'connection_id': connection_id,
        'secret_name': secret_name,
        'available': manager.exists(secret_name),
        'storage_location': 'local'
    }
