"""
Local Authentication Provider

Wraps the existing bcrypt password authentication into the provider interface.
This is the default provider and is always available as a fallback.
"""

import logging
from typing import Tuple

from auth.base_provider import BaseAuthProvider, AuthResult

logger = logging.getLogger(__name__)


class LocalAuthProvider(BaseAuthProvider):
    """
    Authenticates users against the local database using bcrypt password hashing.
    This wraps the existing authentication logic from app.py.
    """

    def __init__(self, bcrypt_instance, user_class):
        """
        Args:
            bcrypt_instance: Flask-Bcrypt instance for password verification
            user_class: The User ORM class with get_by_username() method
        """
        self._bcrypt = bcrypt_instance
        self._user_class = user_class

    @property
    def provider_type(self) -> str:
        return 'local'

    def authenticate(self, username: str, password: str) -> AuthResult:
        """
        Authenticate against local bcrypt password hash.

        Returns AuthResult with user_attributes containing the local User object
        reference under the 'local_user' key for direct use by the login route.
        """
        try:
            user = self._user_class.get_by_username(username)
            if user is None:
                return AuthResult(success=False, error='User not found')

            if not user.password:
                return AuthResult(success=False, error='No password set for user')

            if not self._bcrypt.check_password_hash(user.password, password):
                return AuthResult(success=False, error='Invalid password')

            return AuthResult(
                success=True,
                user_attributes={
                    'username': user.username,
                    'name': user.name,
                    'email': user.email,
                    'external_id': None,
                    'local_user': user,  # Pass the actual ORM object
                }
            )
        except Exception as e:
            logger.error(f"Local auth error: {str(e)}")
            return AuthResult(success=False, error=f'Authentication error: {str(e)}')

    def test_connection(self) -> Tuple[bool, str]:
        """Local auth is always available."""
        return (True, 'Local authentication is available')
