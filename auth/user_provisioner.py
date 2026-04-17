"""
User Provisioner

Finds or creates a local User record from external identity provider attributes.
Used by LDAP, SAML, OIDC providers to map external identities to local users.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class UserProvisioner:
    """
    Maps external identity attributes to local User records.

    On first login: creates a new local User (if auto_provision is enabled).
    On subsequent logins: updates last_sso_login and returns existing User.
    """

    def __init__(self, db, user_class, bcrypt_instance=None):
        """
        Args:
            db: SQLAlchemy database instance
            user_class: The User ORM class
            bcrypt_instance: Flask-Bcrypt instance (for generating placeholder passwords)
        """
        self._db = db
        self._user_class = user_class
        self._bcrypt = bcrypt_instance

    def find_or_create_user(
        self,
        auth_provider: str,
        external_id: str,
        username: str,
        name: str,
        email: Optional[str] = None,
        groups: Optional[list] = None,
        auto_provision: bool = True,
        default_role: int = 1,
        group_role_mapping: Optional[Dict[str, int]] = None,
        tenant_id: Optional[int] = None,
    ) -> Optional[Any]:
        """
        Find an existing user by external identity, or create a new one.

        Args:
            auth_provider: Provider type ('ldap', 'azure_ad', 'saml')
            external_id: Unique ID from the identity provider
            username: Login username
            name: Display name
            email: Email address (optional)
            groups: Group memberships from IdP (optional)
            auto_provision: Whether to create a new user if not found
            default_role: Default role for new users (1=User, 2=Developer, 3=Admin)
            group_role_mapping: Dict mapping group names to role integers
            tenant_id: Tenant ID for multi-tenant isolation

        Returns:
            User object if found/created, None if user not found and auto_provision is False.
        """
        from sqlalchemy import text

        session = self._db.session

        try:
            # Set tenant context for RLS
            api_key = os.getenv('API_KEY')
            if api_key:
                session.execute(
                    text("EXEC tenant.sp_setTenantContext :api_key"),
                    {'api_key': api_key}
                )

            # Try to find existing user by external identity
            user = self._user_class.query.filter_by(
                auth_provider=auth_provider,
                external_id=external_id
            ).first()

            if user:
                # Existing user — update last login time and attributes
                user.last_sso_login = datetime.now(timezone.utc)
                if email:
                    user.external_email = email
                if name and name != user.name:
                    user.name = name

                # Update role based on current group membership (if mapping configured)
                if group_role_mapping and groups:
                    new_role = self._resolve_role(groups, group_role_mapping, user.role)
                    if new_role != user.role:
                        logger.info(f"Updating role for {username}: {user.role} -> {new_role}")
                        user.role = new_role

                session.commit()
                logger.info(f"Existing {auth_provider} user logged in: {username}")
                return user

            # Also check if there's a local user with the same username
            # (handles migration from local to LDAP auth)
            existing_local = self._user_class.query.filter_by(
                username=username,
                auth_provider='local'
            ).first()

            if existing_local:
                # Link the existing local account to the external identity
                existing_local.auth_provider = auth_provider
                existing_local.external_id = external_id
                existing_local.external_email = email
                existing_local.last_sso_login = datetime.now(timezone.utc)

                if group_role_mapping and groups:
                    new_role = self._resolve_role(groups, group_role_mapping, existing_local.role)
                    existing_local.role = new_role

                session.commit()
                logger.info(f"Linked local user '{username}' to {auth_provider} identity")
                return existing_local

            # User not found — auto-provision if enabled
            if not auto_provision:
                logger.info(f"Auto-provision disabled, rejecting unknown {auth_provider} user: {username}")
                return None

            # Determine role from group mapping
            role = default_role
            if group_role_mapping and groups:
                role = self._resolve_role(groups, group_role_mapping, default_role)

            # Create new user
            # Generate a random unusable password (external auth users don't use local passwords)
            placeholder_password = ''
            if self._bcrypt:
                import secrets
                placeholder_password = self._bcrypt.generate_password_hash(
                    secrets.token_hex(32)
                ).decode('utf-8')

            new_user = self._user_class(
                username=username,
                name=name,
                email=email or '',
                phone='',
                role=role,
                password=placeholder_password,
                auth_provider=auth_provider,
                external_id=external_id,
                external_email=email,
                last_sso_login=datetime.now(timezone.utc),
            )

            # Set TenantId if available
            if tenant_id:
                new_user.TenantId = tenant_id

            session.add(new_user)
            session.commit()

            logger.info(f"Auto-provisioned new {auth_provider} user: {username} (role={role})")
            return new_user

        except Exception as e:
            logger.error(f"User provisioning error for {username}: {str(e)}")
            session.rollback()
            return None

    @staticmethod
    def _resolve_role(groups: list, group_role_mapping: Dict[str, int], default_role: int) -> int:
        """Map group memberships to the highest matching AI Hub role."""
        matched_role = default_role
        for group_name, role in group_role_mapping.items():
            if group_name in groups:
                matched_role = max(matched_role, role)
        return matched_role
