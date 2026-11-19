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
        alt_usernames: Optional[list] = None,
        match_email: bool = False,
        relink_external: bool = False,
    ) -> Optional[Any]:
        """
        Find an existing user by external identity, or create a new one.

        The last three arguments are for redirect sign-in (SAML) and leave
        directory (LDAP) sign-in exactly as it was when omitted:
            alt_usernames: other AI Hub user names this person may already
                have (for example the full sign-in name next to the short one).
            match_email: also match an existing user by email address.
            relink_external: an existing user found by user name or email is
                linked to this identity whatever it was linked to before
                (local or another directory), instead of local users only.

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
            if relink_external:
                existing_local, matched_by = self._find_linkable_user(
                    [username] + list(alt_usernames or []),
                    email if match_email else None,
                )
                if existing_local is not None:
                    logger.info(
                        f"Linking AI Hub user '{existing_local.username}' "
                        f"(was {existing_local.auth_provider}) to {auth_provider} "
                        f"identity, matched by {matched_by}"
                    )
            else:
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

            # The same user name already belongs to a user linked to a
            # DIFFERENT kind of directory (for example they have since signed
            # in with SAML and were re-linked to it). It is the same person
            # on the same install, so let them in as that user and leave the
            # link alone. Without this the insert below fails on the unique
            # user name and a directory password that was just accepted is
            # reported as a failed sign-in. Two providers of the same kind
            # are deliberately not covered: that stays refused, as before.
            if not relink_external:
                same_name = self._user_class.query.filter_by(username=username).first()
                if (same_name is not None
                        and same_name.auth_provider not in ('local', auth_provider)):
                    same_name.last_sso_login = datetime.utcnow()
                    session.commit()
                    logger.info(
                        f"{auth_provider} sign-in for '{username}' matched the existing "
                        f"{same_name.auth_provider} user of that name; link left unchanged"
                    )
                    return same_name

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

    def _find_linkable_user(self, usernames: list, email: Optional[str]):
        """
        An existing AI Hub user for someone signing in through a redirect
        provider for the first time: by user name first (each candidate in
        order), then by email address. Returns (user, 'user name' | 'email')
        or (None, None).

        An email address shared by more than one AI Hub user matches nobody:
        guessing between accounts would hand one person another's access.
        """
        for candidate in usernames:
            if not candidate:
                continue
            user = self._user_class.query.filter_by(username=candidate).first()
            if user is not None:
                return user, 'user name'

        if email:
            from sqlalchemy import or_
            matches = self._user_class.query.filter(or_(
                self._user_class.email == email,
                self._user_class.external_email == email,
            )).limit(2).all()
            if len(matches) == 1:
                return matches[0], 'email'
            if len(matches) > 1:
                logger.warning(
                    f"More than one AI Hub user has the email address {email}; "
                    f"not matching by email"
                )
        return None, None

    def preview(
        self,
        auth_provider: str,
        external_id: str,
        username: str,
        email: Optional[str] = None,
        groups: Optional[list] = None,
        auto_provision: bool = True,
        default_role: int = 1,
        group_role_mapping: Optional[Dict[str, int]] = None,
        alt_usernames: Optional[list] = None,
        match_email: bool = False,
    ) -> Dict[str, Any]:
        """
        What find_or_create_user would do for this identity, without changing
        anything. For the administrator's test sign-in.

        Returns {'outcome': 'existing' | 'link' | 'create' | 'refuse',
                 'username': str, 'role': int | None, 'reason': str}.
        """
        from sqlalchemy import text

        session = self._db.session
        try:
            api_key = os.getenv('API_KEY')
            if api_key:
                session.execute(
                    text("EXEC tenant.sp_setTenantContext :api_key"),
                    {'api_key': api_key}
                )

            user = self._user_class.query.filter_by(
                auth_provider=auth_provider,
                external_id=external_id
            ).first()
            if user is not None:
                return {'outcome': 'existing', 'username': user.username, 'role': user.role,
                        'reason': 'Already linked to this identity.'}

            user, matched_by = self._find_linkable_user(
                [username] + list(alt_usernames or []),
                email if match_email else None,
            )
            if user is not None:
                return {'outcome': 'link', 'username': user.username, 'role': user.role,
                        'reason': f'Matches an existing AI Hub user by {matched_by} '
                                  f'(currently {user.auth_provider}). It would be linked '
                                  f'to this identity and keep its role and groups.'}

            if not auto_provision:
                return {'outcome': 'refuse', 'username': username, 'role': None,
                        'reason': 'No AI Hub user matches, and creating users on first '
                                  'sign-in is switched off.'}

            role = default_role
            if group_role_mapping:
                role = self._resolve_role(groups or [], group_role_mapping, default_role)
                if role is None:
                    return {'outcome': 'refuse', 'username': username, 'role': None,
                            'reason': 'No AI Hub user matches, and this person is not in '
                                      'any group listed under Group-to-Role Mapping.'}
            return {'outcome': 'create', 'username': username, 'role': role,
                    'reason': 'A new AI Hub user would be created.'}
        finally:
            session.rollback()

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
