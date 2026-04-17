"""
Base Authentication Provider

Abstract base class that all identity providers must implement.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Tuple


class AuthResult:
    """Result of an authentication attempt."""

    def __init__(self, success: bool, user_attributes: Optional[Dict[str, Any]] = None,
                 error: Optional[str] = None):
        self.success = success
        self.user_attributes = user_attributes or {}
        self.error = error

    def __repr__(self):
        return f"AuthResult(success={self.success}, error={self.error})"


class BaseAuthProvider(ABC):
    """
    Abstract base class for authentication providers.

    Each provider implements authenticate() to validate credentials
    and return user attributes on success.
    """

    @property
    @abstractmethod
    def provider_type(self) -> str:
        """Return the provider type identifier (e.g., 'local', 'ldap', 'azure_ad', 'saml')."""
        pass

    @abstractmethod
    def authenticate(self, username: str, password: str) -> AuthResult:
        """
        Authenticate a user with the given credentials.

        Args:
            username: The username or login identifier
            password: The password or credential

        Returns:
            AuthResult with success=True and user_attributes on success,
            or success=False and error message on failure.

            user_attributes should include:
                - 'username': str (login name)
                - 'name': str (display name)
                - 'email': str (email address, optional)
                - 'external_id': str (unique ID from provider)
                - 'groups': list[str] (group memberships, optional)
        """
        pass

    @abstractmethod
    def test_connection(self) -> Tuple[bool, str]:
        """
        Test connectivity to the identity provider.

        Returns:
            Tuple of (success: bool, message: str)
        """
        pass
