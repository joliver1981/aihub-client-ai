"""
LDAP/Active Directory Authentication Provider

Uses the ldap3 library (pure Python) for LDAP bind authentication
against Active Directory or other LDAP-compliant directory servers.
"""

import logging
import json
from typing import Tuple, Optional, Dict, Any, List

try:
    import ldap3
    from ldap3 import Server, Connection, ALL, SUBTREE, Tls
    from ldap3.core.exceptions import LDAPException, LDAPBindError, LDAPSocketOpenError
    LDAP3_AVAILABLE = True
except ImportError:
    LDAP3_AVAILABLE = False

from auth.base_provider import BaseAuthProvider, AuthResult

logger = logging.getLogger(__name__)


class LdapAuthProvider(BaseAuthProvider):
    """
    Authenticates users via LDAP bind against Active Directory or other LDAP servers.

    Config JSON structure (stored in IdentityProviderConfig.config_json):
    {
        "server": "dc01.company.com",
        "port": 389,
        "use_ssl": false,
        "base_dn": "DC=company,DC=com",
        "bind_template": "{username}@company.com",
        "user_search_filter": "(sAMAccountName={username})",
        "user_search_base": "DC=company,DC=com",
        "attributes": ["displayName", "mail", "memberOf", "sAMAccountName"],
        "connect_timeout": 10,
        "receive_timeout": 10
    }
    """

    def __init__(self, config: Dict[str, Any], group_role_mapping: Optional[Dict[str, int]] = None):
        """
        Args:
            config: Provider configuration dict (from config_json)
            group_role_mapping: Dict mapping AD group names to AI Hub role integers
                                e.g., {"Domain Admins": 3, "Developers": 2}
        """
        if not LDAP3_AVAILABLE:
            raise ImportError(
                "ldap3 library is not installed. Install it with: pip install ldap3"
            )

        self._config = config
        self._group_role_mapping = group_role_mapping or {}

    @property
    def provider_type(self) -> str:
        return 'ldap'

    def _get_server(self) -> 'ldap3.Server':
        """Create an ldap3 Server object from configuration."""
        host = self._config['server']
        port = self._config.get('port', 636 if self._config.get('use_ssl') else 389)
        use_ssl = self._config.get('use_ssl', False)
        # BUG-AUTH-001 mitigation (option D): default 10s was too tight when
        # DNS resolution + TCP + TLS handshake + RootDSE fetch all happen on
        # the very first request after Flask startup. 30s gives the cold
        # path enough headroom; subsequent requests resolve in milliseconds.
        # Operators can still override via the LDAP provider config.
        connect_timeout = self._config.get('connect_timeout', 30)

        tls = None
        if use_ssl:
            import ssl
            tls = Tls(validate=ssl.CERT_NONE)  # Allow self-signed certs in enterprise environments

        return Server(
            host,
            port=port,
            use_ssl=use_ssl,
            tls=tls,
            get_info=ALL,
            connect_timeout=connect_timeout
        )

    def _format_bind_dn(self, username: str) -> str:
        """Format the bind DN using the configured template."""
        template = self._config.get('bind_template', '{username}')
        return template.replace('{username}', username)

    def _search_user_attributes(self, conn: 'ldap3.Connection', username: str) -> Optional[Dict[str, Any]]:
        """
        Search for user attributes after successful bind.

        Returns dict with display_name, email, groups, and sAMAccountName.
        """
        search_base = self._config.get('user_search_base', self._config.get('base_dn', ''))
        search_filter = self._config.get('user_search_filter', '(sAMAccountName={username})')
        search_filter = search_filter.replace('{username}', username)
        attributes = self._config.get('attributes', ['displayName', 'mail', 'memberOf', 'sAMAccountName'])

        try:
            conn.search(
                search_base=search_base,
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=attributes
            )

            if not conn.entries:
                logger.warning(f"LDAP user search returned no results for: {username}")
                return None

            entry = conn.entries[0]
            attrs = {}

            # Extract attributes safely
            if hasattr(entry, 'displayName') and entry.displayName.value:
                attrs['display_name'] = str(entry.displayName.value)
            else:
                attrs['display_name'] = username

            if hasattr(entry, 'mail') and entry.mail.value:
                attrs['email'] = str(entry.mail.value)
            else:
                attrs['email'] = None

            if hasattr(entry, 'sAMAccountName') and entry.sAMAccountName.value:
                attrs['sam_account_name'] = str(entry.sAMAccountName.value)
            else:
                attrs['sam_account_name'] = username

            # Extract group memberships
            groups = []
            if hasattr(entry, 'memberOf') and entry.memberOf.values:
                for group_dn in entry.memberOf.values:
                    # Extract CN from full DN: "CN=GroupName,OU=Groups,DC=company,DC=com"
                    group_name = self._extract_cn(str(group_dn))
                    if group_name:
                        groups.append(group_name)
            attrs['groups'] = groups

            return attrs

        except Exception as e:
            logger.warning(f"LDAP user attribute search failed: {str(e)}")
            # Bind succeeded but search failed — return minimal attributes
            return {
                'display_name': username,
                'email': None,
                'sam_account_name': username,
                'groups': []
            }

    @staticmethod
    def _extract_cn(dn: str) -> Optional[str]:
        """Extract the CN (Common Name) from a Distinguished Name."""
        for part in dn.split(','):
            part = part.strip()
            if part.upper().startswith('CN='):
                return part[3:]
        return None

    def _resolve_role(self, groups: List[str], default_role: int = 1) -> int:
        """
        Map AD group memberships to an AI Hub role.

        Returns the highest role matched, or default_role if no mapping matches.
        """
        if not self._group_role_mapping or not groups:
            return default_role

        matched_role = default_role
        for group_name, role in self._group_role_mapping.items():
            if group_name in groups:
                matched_role = max(matched_role, role)

        return matched_role

    def authenticate(self, username: str, password: str) -> AuthResult:
        """
        Authenticate by performing an LDAP bind with the user's credentials.
        On success, search for user attributes (name, email, groups).
        """
        if not username or not password:
            return AuthResult(success=False, error='Username and password are required')

        bind_dn = self._format_bind_dn(username)
        receive_timeout = self._config.get('receive_timeout', 10)

        # BUG-AUTH-001 mitigation (option C): a single retry on
        # LDAPSocketOpenError. The first LDAP login after Flask startup
        # occasionally fails on the cold DNS/TCP/TLS handshake; retries
        # succeed reliably. We retry only on socket-open errors (NOT bind
        # errors) so that bad-password attempts are rejected fast and we
        # don't accidentally hammer AD with repeated bad credentials and
        # trigger an account lockout.
        max_attempts = 2
        attempt = 0
        last_socket_error: Optional[Exception] = None

        while attempt < max_attempts:
            attempt += 1
            # New Server object per attempt so the retry doesn't inherit a
            # half-initialised state from the failed first call. Cheap
            # operation — Server() doesn't touch the network until
            # Connection() opens.
            server = self._get_server()

            try:
                import time
                _t0 = time.monotonic()
                conn = Connection(
                    server,
                    user=bind_dn,
                    password=password,
                    auto_bind=True,
                    receive_timeout=receive_timeout,
                    read_only=True
                )
                # Diagnostic logging — captures cold-vs-warm timing so we
                # can confirm/refute the "cold first call" hypothesis the
                # next time the flake is observed in production.
                logger.info(
                    f"LDAP bind successful for user: {username} "
                    f"(attempt {attempt}/{max_attempts}, "
                    f"open+bind took {time.monotonic() - _t0:.2f}s)"
                )

                # Search for user attributes
                attrs = self._search_user_attributes(conn, username)
                if attrs is None:
                    attrs = {
                        'display_name': username,
                        'email': None,
                        'sam_account_name': username,
                        'groups': []
                    }

                conn.unbind()

                return AuthResult(
                    success=True,
                    user_attributes={
                        'username': attrs['sam_account_name'],
                        'name': attrs['display_name'],
                        'email': attrs.get('email'),
                        'external_id': attrs['sam_account_name'],
                        'groups': attrs.get('groups', []),
                    }
                )

            except LDAPBindError as e:
                # Bad credentials — never retry, never lock the account.
                logger.info(
                    f"LDAP bind failed for user {username} "
                    f"(attempt {attempt}): {str(e)}"
                )
                return AuthResult(success=False, error='Invalid credentials')

            except LDAPSocketOpenError as e:
                # Cold connect / TLS / DNS error. Log with attempt number
                # so the loop is visible in production logs, then either
                # retry or give up depending on attempt count.
                logger.warning(
                    f"LDAP socket-open failed (attempt {attempt}/{max_attempts}) "
                    f"for user {username}: {type(e).__name__}: {str(e)}"
                )
                last_socket_error = e
                if attempt < max_attempts:
                    continue  # retry — fresh Server + fresh Connection
                logger.error(
                    f"LDAP connection failed after {max_attempts} attempts: {str(e)}"
                )
                return AuthResult(
                    success=False,
                    error=f'Cannot connect to LDAP server: {str(e)}'
                )

            except LDAPException as e:
                logger.error(
                    f"LDAP error for user {username} "
                    f"(attempt {attempt}): {type(e).__name__}: {str(e)}"
                )
                return AuthResult(success=False, error=f'LDAP error: {str(e)}')

            except Exception as e:
                logger.error(
                    f"Unexpected error during LDAP auth for {username} "
                    f"(attempt {attempt}): {type(e).__name__}: {str(e)}"
                )
                return AuthResult(
                    success=False,
                    error=f'Authentication error: {str(e)}'
                )

        # Defensive — both attempts should have either returned or set
        # last_socket_error; never reachable in practice.
        return AuthResult(
            success=False,
            error=f'LDAP authentication exhausted retries: {last_socket_error}'
        )

    def test_connection(self) -> Tuple[bool, str]:
        """
        Test LDAP server connectivity by attempting an anonymous or simple bind.
        Does not authenticate any specific user.
        """
        try:
            server = self._get_server()
            conn = Connection(server, auto_bind=False, receive_timeout=5)
            conn.open()

            if conn.bound or server.info:
                conn.unbind()
                server_info = f"{self._config['server']}:{self._config.get('port', 389)}"
                return (True, f'Successfully connected to LDAP server at {server_info}')

            conn.unbind()
            return (True, 'Connection opened successfully')

        except LDAPSocketOpenError as e:
            return (False, f'Cannot connect to LDAP server: {str(e)}')

        except Exception as e:
            return (False, f'Connection test failed: {str(e)}')
