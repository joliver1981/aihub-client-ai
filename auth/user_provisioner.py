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
                # Existing user — update last login time and attributes.
                # Use tz-naive UTC datetime; the legacy {SQL Server} ODBC
                # driver mishandles tz-aware datetimes on parameter binds.
                user.last_sso_login = datetime.utcnow()
                if email:
                    user.external_email = email
                if name and name != user.name:
                    user.name = name

                # Update role based on current group membership (if mapping configured).
                # If the user is no longer in any mapped group, leave their role
                # unchanged — don't silently downgrade an existing user. Admins
                # who want to revoke access should disable the local account.
                if group_role_mapping and groups:
                    new_role = self._resolve_role(groups, group_role_mapping, user.role)
                    if new_role is not None and new_role != user.role:
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
                if email:
                    existing_local.external_email = email
                # tz-naive datetime — see comment on the first branch above
                existing_local.last_sso_login = datetime.utcnow()

                if group_role_mapping and groups:
                    new_role = self._resolve_role(groups, group_role_mapping, existing_local.role)
                    if new_role is not None:
                        existing_local.role = new_role

                session.commit()
                logger.info(f"Linked local user '{username}' to {auth_provider} identity")
                return existing_local

            # User not found — auto-provision if enabled
            if not auto_provision:
                logger.info(f"Auto-provision disabled, rejecting unknown {auth_provider} user: {username}")
                return None

            # Determine role from group mapping.
            # If a group mapping is configured, require the user to be a
            # member of at least one mapped group — otherwise refuse to
            # auto-provision. "You configured a mapping, so unmapped users
            # shouldn't get in" is the least-surprising behavior.
            role = default_role
            if group_role_mapping:
                resolved = self._resolve_role(groups or [], group_role_mapping, default_role)
                if resolved is None:
                    logger.info(
                        f"Rejecting auto-provision for {username}: "
                        f"not a member of any mapped group "
                        f"(user groups: {groups or []})"
                    )
                    return None
                role = resolved

            # Create new user
            # Generate a random unusable password (external auth users don't use local passwords)
            placeholder_password = ''
            if self._bcrypt:
                import secrets
                placeholder_password = self._bcrypt.generate_password_hash(
                    secrets.token_hex(32)
                ).decode('utf-8')

            # Bypass SQLAlchemy ORM for the INSERT. Use the existing
            # Add_User() raw-SQL path that the rest of the app relies on.
            #
            # Why: the legacy {SQL Server} ODBC driver throws HY104
            # ("Invalid precision value (0) (SQLBindParameter)") when
            # pyodbc binds NULL parameters to NVARCHAR columns (and also
            # has known issues with tz-aware datetime params). SQLAlchemy
            # always uses bound parameters via pyodbc, so it can't avoid
            # these failures with this driver.
            #
            # Add_User() avoids parameter binding entirely — values are
            # escaped via format_string_for_insert() and interpolated into
            # the SQL string before execution. No SQLBindParameter call,
            # no HY104. Same legacy driver, same database, just a
            # different code path that's been working in production.
            #
            # After insert, we requery via ORM so flask_login still has
            # a normal model instance (and so last_sso_login can be set
            # through the ORM where datetime handling is fine for
            # already-existing rows).
            from DataUtils import Add_User
            new_user_id, ok = Add_User(
                user_id=0,
                user_name=username,
                role=role,
                email=email or '',
                phone='',
                name=name,
                password=placeholder_password,
                auth_provider=auth_provider,
                external_id=external_id,
            )
            if not ok or not new_user_id:
                logger.error(
                    f"Add_User() returned ok={ok}, new_user_id={new_user_id} "
                    f"for {auth_provider} user '{username}'"
                )
                return None

            # Add_User() reads the new id out of a pandas DataFrame, which
            # yields numpy.int64. pyodbc rejects numpy types as bind params
            # with HY105 "Invalid parameter type". Coerce to a native int
            # before using it anywhere parameter binding will happen.
            try:
                new_user_id = int(new_user_id)
            except (TypeError, ValueError):
                logger.error(
                    f"Could not coerce new_user_id={new_user_id!r} "
                    f"(type={type(new_user_id).__name__}) to int"
                )
                return None

            # Re-query through SQLAlchemy so flask_login gets a managed
            # User instance back. last_sso_login is set on the managed
            # instance and committed via the normal session — this UPDATE
            # path doesn't have the HY104 issue because the bound row
            # already exists (only the datetime column is being touched,
            # and SQLAlchemy strips tz info for db.DateTime columns).
            new_user = self._user_class.query.filter_by(id=new_user_id).first()
            if new_user is None:
                logger.error(
                    f"Add_User() reported success but re-query returned no row "
                    f"for id={new_user_id}, username='{username}'"
                )
                return None

            # Stamp last_sso_login with a tz-naive datetime to keep the
            # legacy driver happy on the UPDATE binding.
            new_user.last_sso_login = datetime.utcnow()
            if email:
                new_user.external_email = email
            session.commit()

            logger.info(f"Auto-provisioned new {auth_provider} user: {username} (role={role})")
            return new_user

        except Exception as e:
            logger.error(f"User provisioning error for {username}: {str(e)}")
            session.rollback()
            return None

    @staticmethod
    def _resolve_role(groups: list, group_role_mapping: Dict[str, int], default_role: int):
        """
        Map group memberships to the highest matching AI Hub role.

        Returns the matched role (int) if the user is in at least one mapped
        group, otherwise None. Comparison is case-insensitive and ignores
        leading/trailing whitespace — AD group names are case-insensitive,
        but Python string equality is not.
        """
        if not group_role_mapping or not groups:
            return None
        normalized_user_groups = {str(g).strip().casefold() for g in groups if g}
        matched_role = None
        for group_name, role in group_role_mapping.items():
            if str(group_name).strip().casefold() in normalized_user_groups:
                if matched_role is None or role > matched_role:
                    matched_role = role
        if matched_role is None:
            return None
        return max(matched_role, default_role)
