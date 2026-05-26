"""
SharePoint Online / OneDrive Executor
=====================================

Custom executor for Microsoft Graph API integrations. Supports two modes
based on the template_key:
  - 'sharepoint_online' → uses /sites/... and /drives/{drive_id}/... endpoints
  - 'onedrive'          → uses /me/drive/... endpoints (personal or business)

Handles both JSON API responses and binary file downloads, plus bridging
downloaded files into the document processing / knowledge base pipeline.

Follows the same interface as CloudStorageExecutor so it can be used as a
drop-in replacement when execution_type == 'sharepoint'.
"""

import os
import json
import time
import uuid
import logging
import base64
import requests
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta

from CommonUtils import get_db_connection
from local_secrets import get_local_secret, set_local_secret
from integration_manager import get_integration_secret_name
import config as cfg

logger = logging.getLogger('sharepoint_executor')

GRAPH_BASE_URL = 'https://graph.microsoft.com/v1.0'
MAX_DOWNLOAD_SIZE_MB = cfg.DOC_MAX_UPLOAD_SIZE_MB


class SharePointExecutor:
    """
    Executes SharePoint operations via Microsoft Graph API.

    Same interface as OperationExecutor / CloudStorageExecutor so it can be
    used as a drop-in replacement when the template has
    execution_type == 'sharepoint'.
    """

    def __init__(self, integration: Dict, template: Dict):
        self.integration = integration
        self.template = template
        self._last_token_error = None

    def execute(
        self,
        operation_key: str,
        parameters: Dict = None,
        context: Dict = None
    ) -> Dict[str, Any]:
        parameters = parameters or {}
        context = context or {}

        if isinstance(parameters, str):
            try:
                parameters = json.loads(parameters) if parameters else {}
            except json.JSONDecodeError:
                parameters = {}
        if not isinstance(parameters, dict):
            parameters = {}

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

        handler_map = {
            'health_check': self._health_check,
            'list_sites': self._list_sites,
            'lookup_site_by_url': self._lookup_site_by_url,
            'list_drives': self._list_drives,
            'list_items': self._list_items,
            'list_my_files': self._list_my_files,
            'get_item_metadata': self._get_item_metadata,
            'download_file': self._download_file,
            'search_files': self._search_files,
            'download_to_knowledge': self._download_to_knowledge,
            # Path-based + write operations (workflow-friendly)
            'list_folder_by_path': self._list_folder_by_path,
            'download_file_by_path': self._download_file_by_path,
            'download_folder': self._download_folder,
            'upload_file': self._upload_file,
            'upload_content': self._upload_content,
            'delete_file': self._delete_file,
            'create_folder': self._create_folder,
            'move_file': self._move_file,
            'copy_file': self._copy_file,
            'rename_file': self._rename_file,
            'import_folder_to_knowledge': self._import_folder_to_knowledge,
        }

        handler = handler_map.get(operation_key)
        if not handler:
            return {
                'success': False,
                'error': f"No handler for SharePoint operation '{operation_key}'",
                'data': None
            }

        start_time = time.time()
        try:
            self._ensure_token_fresh()
            if operation_key == 'download_to_knowledge':
                result = handler(parameters, context)
            else:
                result = handler(parameters)
            response_time_ms = int((time.time() - start_time) * 1000)
            result['response_time_ms'] = response_time_ms

            self._log_execution(operation_key, parameters, result, context)
            return result

        except Exception as e:
            response_time_ms = int((time.time() - start_time) * 1000)
            error_result = {
                'success': False,
                'error': f"SharePoint operation failed: {str(e)}",
                'data': None,
                'response_time_ms': response_time_ms
            }
            self._log_execution(operation_key, parameters, error_result, context)
            return error_result

    # =========================================================================
    # Token Management
    # =========================================================================

    def _get_access_token(self) -> str:
        integration_id = self.integration.get('integration_id')
        return get_local_secret(
            get_integration_secret_name(integration_id, 'access_token'), ''
        )

    def _force_refresh_token(self) -> bool:
        """Invalidate the cached access token and fetch a fresh one
        immediately. Use after the Azure app's permissions have changed —
        the existing token's scope/roles claim was set at issue time and
        won't reflect the new permissions until the token is reissued.

        Returns True on success, False otherwise.
        """
        integration_id = self.integration.get('integration_id')
        # Clear the cached token so _ensure_token_fresh definitely refreshes
        try:
            from local_secrets import set_local_secret
            set_local_secret(
                get_integration_secret_name(integration_id, 'access_token'),
                '',
                f"Cleared for forced refresh on integration {integration_id}"
            )
        except Exception as e:
            logger.warning(f"_force_refresh_token: could not clear cached token: {e}")

        # Mark expired so the OAuth handler treats it as needing refresh
        self.integration['oauth_token_expires_at'] = datetime.utcnow() - timedelta(minutes=1)
        self._update_token_expiration(self.integration['oauth_token_expires_at'])

        # Trigger refresh
        try:
            self._ensure_token_fresh()
            new_token = self._get_access_token()
            ok = bool(new_token)
            if ok:
                logger.info(
                    f"_force_refresh_token: fresh token acquired for "
                    f"integration {integration_id}"
                )
            else:
                logger.warning(
                    f"_force_refresh_token: no token returned for "
                    f"integration {integration_id}"
                )
            return ok
        except Exception as e:
            logger.error(f"_force_refresh_token failed: {e}")
            return False

    def _ensure_token_fresh(self):
        """Refresh the OAuth2 token if expired or about to expire.

        Delegates to the framework's OAuth2AuthHandler which handles both
        refresh_token and client_credentials grant types, and substitutes
        instance_config variables (like {tenant_id}) in the token URL.
        """
        expires_at = self.integration.get('oauth_token_expires_at')
        needs_refresh = True

        if expires_at:
            if isinstance(expires_at, str):
                try:
                    expires_at = datetime.fromisoformat(
                        expires_at.replace('Z', '+00:00')
                    )
                except ValueError:
                    expires_at = None

            if expires_at:
                buffer = timedelta(minutes=5)
                needs_refresh = datetime.utcnow() >= (expires_at - buffer)

        if not needs_refresh:
            return

        try:
            from integration_manager import OAuth2AuthHandler
            result = OAuth2AuthHandler().refresh_credentials(self.integration)
            if not result:
                # Framework didn't return a token. For app-only mode, do a direct
                # token request so we can surface the actual Azure error message.
                if self._is_app_only():
                    self._direct_token_fetch_for_diagnostics()
                else:
                    logger.warning(
                        f"Token refresh returned no result for integration "
                        f"{self.integration.get('integration_id')}"
                    )
                return

            new_expires_at = result.get('expires_at')
            if not new_expires_at:
                expires_in = result.get('expires_in', 3600)
                new_expires_at = datetime.utcnow() + timedelta(seconds=int(expires_in))

            self.integration['oauth_token_expires_at'] = new_expires_at
            self._update_token_expiration(new_expires_at)
            logger.info(
                f"OAuth token refreshed for integration "
                f"{self.integration.get('integration_id')}"
            )
        except Exception as e:
            logger.error(f"Error refreshing OAuth token: {e}")
            self._last_token_error = str(e)

    def _direct_token_fetch_for_diagnostics(self):
        """When the framework's refresh fails for client_credentials, repeat the
        request directly so we can capture the actual response body and surface
        a precise error message (e.g. AADSTS70011, AADSTS7000215, etc.)."""
        integration_id = self.integration.get('integration_id')
        auth_config = self.template.get('auth_config', {}) or {}
        token_url = auth_config.get('token_url', '')

        try:
            instance_config = json.loads(self.integration.get('instance_config') or '{}')
            for key, value in instance_config.items():
                token_url = token_url.replace('{' + key + '}', str(value))
        except (ValueError, TypeError):
            pass

        client_id = get_local_secret(
            get_integration_secret_name(integration_id, 'client_id'), ''
        )
        client_secret = get_local_secret(
            get_integration_secret_name(integration_id, 'client_secret'), ''
        )
        scopes = auth_config.get('scopes') or ['https://graph.microsoft.com/.default']

        if not (token_url and client_id and client_secret):
            self._last_token_error = "Missing tenant ID, client ID, or client secret."
            return

        try:
            resp = requests.post(token_url, data={
                'grant_type': 'client_credentials',
                'client_id': client_id,
                'client_secret': client_secret,
                'scope': ' '.join(scopes),
            }, timeout=30)

            if resp.status_code == 200:
                # Race: framework call failed but direct call worked. Persist token.
                token_data = resp.json()
                if token_data.get('access_token'):
                    set_local_secret(
                        get_integration_secret_name(integration_id, 'access_token'),
                        token_data['access_token'],
                        f"OAuth access token for integration {integration_id}"
                    )
                    expires_in = int(token_data.get('expires_in', 3600))
                    new_exp = datetime.utcnow() + timedelta(seconds=expires_in)
                    self.integration['oauth_token_expires_at'] = new_exp
                    self._update_token_expiration(new_exp)
                return

            # Capture the Azure error response
            try:
                err_body = resp.json()
                err_code = err_body.get('error', '')
                err_desc = err_body.get('error_description', '') or err_body.get('error', '')
                self._last_token_error = (
                    f"Azure AD rejected the token request ({resp.status_code} {err_code}): "
                    f"{err_desc[:500]}"
                )
            except ValueError:
                self._last_token_error = (
                    f"Azure AD returned HTTP {resp.status_code}: {resp.text[:500]}"
                )
            logger.error(f"Direct token fetch failed: {self._last_token_error}")

        except requests.exceptions.RequestException as e:
            self._last_token_error = f"Network error contacting Azure AD: {e}"
            logger.error(self._last_token_error)

    def _update_token_expiration(self, expires_at):
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            cursor.execute("""
                UPDATE UserIntegrations
                SET oauth_token_expires_at = ?, updated_at = GETUTCDATE()
                WHERE integration_id = ?
            """, (expires_at, self.integration.get('integration_id')))
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            logger.error(f"Error updating token expiration: {e}")

    # =========================================================================
    # Mode helpers
    # =========================================================================

    def _is_onedrive(self) -> bool:
        """True when the integration is OneDrive (uses /me/drive/...).
        False for SharePoint Online (uses /sites/.../drives/...)."""
        return self.template.get('template_key') == 'onedrive'

    def _is_app_only(self) -> bool:
        """True when the integration uses OAuth2 client_credentials
        (service account / app-only). In that mode there is no user, so
        endpoints like /me will fail and we use /sites for health checks."""
        auth_config = self.template.get('auth_config', {}) or {}
        return auth_config.get('grant_type') == 'client_credentials'

    def _get_token_roles(self) -> list:
        """Decode the JWT access token (without validation) and return the
        'roles' claim. Useful for detecting Sites.Selected vs Sites.Read.All
        without making an extra API call."""
        token = self._get_access_token()
        if not token:
            return []
        try:
            # JWT has 3 parts: header.payload.signature — we need the payload
            parts = token.split('.')
            if len(parts) < 2:
                return []
            # Add padding for base64
            payload_b64 = parts[1]
            payload_b64 += '=' * (4 - len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            return payload.get('roles', [])
        except Exception as e:
            logger.debug(f"Could not decode JWT roles: {e}")
            return []

    # =========================================================================
    # Graph API Helpers
    # =========================================================================

    def _make_graph_request(
        self,
        endpoint: str,
        params: Dict = None,
        stream: bool = False
    ) -> requests.Response:
        url = f"{GRAPH_BASE_URL}{endpoint}"
        headers = {
            'Authorization': f"Bearer {self._get_access_token()}",
            'Accept': 'application/json',
        }
        return requests.get(
            url, headers=headers, params=params,
            stream=stream, timeout=60, allow_redirects=True
        )

    @staticmethod
    def _format_item(item: Dict) -> Dict:
        """Normalise a Graph driveItem into a simpler dict for the UI."""
        is_folder = 'folder' in item
        result = {
            'id': item.get('id'),
            'name': item.get('name'),
            'type': 'folder' if is_folder else 'file',
            'size': item.get('size', 0),
            'lastModifiedDateTime': item.get('lastModifiedDateTime'),
            'webUrl': item.get('webUrl'),
        }

        # Expose the drive-relative path of THIS item so workflow nodes that
        # need a folder_path string can reference it (e.g. uploading back to
        # the same folder a file was iterated from).
        #
        # Graph returns parentReference.path as "/drives/{driveId}/root:/A/B".
        # We strip the "/drives/.../root:" prefix to get just "A/B", then
        # join with the item name to get the full drive-relative path.
        parent_ref = item.get('parentReference', {}) or {}
        raw_parent_path = parent_ref.get('path') or ''
        if ':' in raw_parent_path:
            # take everything after the last ":" => "/A/B" then strip leading "/"
            parent_relative = raw_parent_path.split(':', 1)[1].lstrip('/')
        else:
            parent_relative = raw_parent_path.lstrip('/')
        item_name = item.get('name') or ''
        if parent_relative and item_name:
            item_path = f"{parent_relative}/{item_name}"
        else:
            item_path = item_name or parent_relative
        result['parentPath'] = parent_relative   # path of the containing folder
        result['path'] = item_path               # drive-relative path of this item

        if is_folder:
            result['childCount'] = item.get('folder', {}).get('childCount', 0)
        else:
            result['mimeType'] = item.get('file', {}).get('mimeType', '')
        return result

    @staticmethod
    def _apply_type_filter(items: List[Dict], type_filter: Optional[str]) -> List[Dict]:
        """Filter formatted items by type.

        Customer folder structures are messy — folders often contain stray
        files mixed in with subfolders, or subfolders mixed in with files.
        This helper lets list_* operations return only the kind of item the
        workflow actually wants to iterate over.

        Args:
            items: List of items already passed through _format_item().
            type_filter: One of "all", "folders", "files", or empty/None.
                         Anything other than "folders"/"files" is treated
                         as no filter (returns items unchanged).

        Returns:
            Filtered list of items.
        """
        if not type_filter:
            return items
        normalized = type_filter.strip().lower()
        if normalized in ('folder', 'folders'):
            return [i for i in items if i.get('type') == 'folder']
        if normalized in ('file', 'files'):
            return [i for i in items if i.get('type') == 'file']
        # "all" or anything unrecognized — return everything
        return items

    # =========================================================================
    # Operation Handlers
    # =========================================================================

    def _health_check(self, params: Dict) -> Dict:
        """
        Verify the integration end-to-end:
          1. Token works at all (call /me)
          2. SharePoint scope is granted (call /sites?$top=1)
        Returns clear, actionable error messages for the common failure modes:
          - No token / expired token / not connected
          - Admin consent missing for Sites.Read.All
          - Tenant has no SharePoint sites accessible to this user
        """
        integration_id = self.integration.get('integration_id')

        if not self._get_access_token():
            if self._is_app_only():
                msg = "Could not acquire a service token."
                if self._last_token_error:
                    msg += f" {self._last_token_error}"
                else:
                    msg += (
                        " Common causes: "
                        "(1) tenant ID is wrong or doesn't match the Azure app's directory; "
                        "(2) client secret is invalid or expired (regenerate in Azure -> Certificates & secrets); "
                        "(3) the Azure app does not have APPLICATION permissions for "
                        "Sites.Read.All / Files.Read.All, or admin consent has not been granted."
                    )
                return {
                    'success': False,
                    'error': msg,
                    'data': None,
                }
            return {
                'success': False,
                'error': (
                    "Not connected. No access token found for this integration. "
                    "Click 'Connect' in the Integrations Gallery to sign in with your Microsoft account."
                ),
                'data': None,
            }

        # App-only mode: no user, skip /me. Just verify SharePoint scope.
        if self._is_app_only():
            # Use /sites/root which always works (search index has latency).
            root_resp = self._make_graph_request('/sites/root')
            if root_resp.status_code == 200:
                root = root_resp.json()
                # Optionally also try search for additional indexed sites
                searched = 0
                search_resp = self._make_graph_request('/sites', params={'search': '*'})
                if search_resp.status_code == 200:
                    searched = len(search_resp.json().get('value', []))

                instance_config = {}
                try:
                    instance_config = json.loads(self.integration.get('instance_config') or '{}')
                except (ValueError, TypeError):
                    pass
                return {
                    'success': True,
                    'data': {
                        'message': "Connected to SharePoint as a service account.",
                        'auth_mode': 'app-only',
                        'tenant_id': instance_config.get('tenant_id'),
                        'tenant_root_site': root.get('webUrl'),
                        'sharepoint_accessible': True,
                        'sites_in_search_index': searched,
                        'integration_id': integration_id,
                        'note': (
                            "If a recently-created site isn't yet visible to search, "
                            "use 'Lookup Site by URL' to access it directly. "
                            "SharePoint search indexing typically catches up in 5-15 minutes."
                        ),
                    },
                    'error': None,
                    'raw_response': json.dumps({'root': root.get('webUrl'), 'searched': searched})[:5000],
                }
            # /sites/root failed — disambiguate auth vs permission scoping.
            #
            # 401 means Microsoft rejected the token itself (truly broken auth).
            #
            # 403 means the token IS valid, but doesn't have permission for
            # this specific resource. Under the Sites.Selected permission
            # model that's the expected state for /sites/root when the
            # tenant root site hasn't been explicitly granted to this app —
            # operations against authorized sites will still work. We treat
            # this as a partial success so is_connected stays True and the
            # integration remains usable.
            if root_resp.status_code == 401:
                return {
                    'success': False,
                    'error': (
                        "Microsoft rejected the service token (401 Unauthorized). "
                        "Disconnect and reconnect the integration, or POST to "
                        "/api/integrations/<id>/refresh-token to mint a fresh token. "
                        "If that doesn't help, verify the tenant ID, client ID and "
                        "client secret stored on the integration match the Azure app."
                    ),
                    'data': {'sharepoint_accessible': False, 'auth_mode': 'app-only'},
                    'raw_response': root_resp.text[:2000],
                }

            if root_resp.status_code == 403:
                # Try to disambiguate further: is /sites/root just not authorized
                # (Sites.Selected mode — fine), or does the app lack any SharePoint
                # permission at all (configuration error — needs admin attention)?
                #
                # If /sites?search=* also returns 403, the token has no SharePoint
                # roles at all — that's a real configuration problem. If search
                # returns 200 (even with zero results), the token has Sites.Read.All
                # but search index hasn't caught up, OR it's Sites.Selected and we
                # can talk to Graph but root isn't granted.
                search_resp = self._make_graph_request('/sites', params={'search': '*', '$top': 1})
                instance_config = {}
                try:
                    instance_config = json.loads(self.integration.get('instance_config') or '{}')
                except (ValueError, TypeError):
                    pass

                if search_resp.status_code == 403:
                    # Both calls returned 403. Check the JWT roles claim —
                    # if Sites.Selected is present, this is the expected state
                    # for a per-site scoped app (root + search aren't granted).
                    token_roles = self._get_token_roles()
                    if 'Sites.Selected' in token_roles:
                        return {
                            'success': True,
                            'data': {
                                'message': (
                                    "Connected (Sites.Selected permission model). "
                                    "This app uses per-site scoping — only sites "
                                    "explicitly granted to it are accessible. "
                                    "Use 'Look up by URL' to access a granted site."
                                ),
                                'auth_mode': 'app-only',
                                'permission_model': 'sites_selected',
                                'token_roles': token_roles,
                                'tenant_id': instance_config.get('tenant_id'),
                                'tenant_root_site': None,
                                'sharepoint_accessible': True,
                                'integration_id': integration_id,
                                'note': (
                                    "Tenant-wide site listing and search are not "
                                    "available with Sites.Selected. Use 'Look up by "
                                    "URL' with the exact SharePoint site URL to "
                                    "browse an authorized site."
                                ),
                            },
                            'error': None,
                        }

                    # No Sites.Selected in the token — real permission problem.
                    return {
                        'success': False,
                        'error': (
                            "Service token works, but SharePoint access is denied. "
                            "Check that EITHER Sites.Read.All / Files.Read.All "
                            "Application permissions are added AND admin-consented, "
                            "OR (Sites.Selected model) at least one site has been "
                            "explicitly granted to this app via the Graph API "
                            "(POST /sites/{site-id}/permissions). "
                            "If you recently changed permissions, POST to "
                            "/api/integrations/<id>/refresh-token to invalidate "
                            "the cached service token."
                        ),
                        'data': {
                            'sharepoint_accessible': False,
                            'auth_mode': 'app-only',
                            'sites_root_status': 403,
                            'sites_search_status': search_resp.status_code,
                            'token_roles': token_roles,
                        },
                        'raw_response': root_resp.text[:2000],
                    }

                # /sites/root forbidden but search works → almost certainly
                # Sites.Selected. Mark as connected so operations against
                # authorized sites are unblocked.
                searched = 0
                try:
                    searched = len(search_resp.json().get('value', []))
                except Exception:
                    pass
                return {
                    'success': True,
                    'data': {
                        'message': (
                            "Connected (Sites.Selected permission model). The "
                            "tenant root isn't authorized to this app, which is "
                            "the expected state for least-privilege scoping. "
                            "Operations against sites explicitly granted to this "
                            "app will work."
                        ),
                        'auth_mode': 'app-only',
                        'permission_model': 'sites_selected_likely',
                        'tenant_id': instance_config.get('tenant_id'),
                        'tenant_root_site': None,
                        'sharepoint_accessible': True,
                        'sites_in_search_index': searched,
                        'integration_id': integration_id,
                        'note': (
                            "Use 'Look up by URL' to access an authorized site. "
                            "If you intended for AI Hub to have tenant-wide access, "
                            "make sure the Sites.Read.All Application permission is "
                            "granted in Azure AND admin-consented."
                        ),
                    },
                    'error': None,
                    'raw_response': json.dumps({
                        'sites_root_status': 403,
                        'sites_search_status': search_resp.status_code,
                        'sites_visible_in_search': searched,
                    })[:5000],
                }

            return self._friendly_error(root_resp, '/sites/root')

        # Step 1: validate token via /me (works with any delegated scope)
        me_resp = self._make_graph_request('/me')
        if me_resp.status_code == 401:
            return {
                'success': False,
                'error': (
                    "Microsoft rejected the access token (401 Unauthorized). "
                    "The token may be expired or revoked. Disconnect and reconnect this integration."
                ),
                'data': None,
                'raw_response': me_resp.text[:2000],
            }
        if me_resp.status_code != 200:
            return self._friendly_error(me_resp, '/me')

        try:
            me = me_resp.json()
        except ValueError:
            return {
                'success': False,
                'error': "Microsoft Graph returned an unexpected response. Try again or reconnect.",
                'data': None,
                'raw_response': me_resp.text[:2000],
            }

        user_info = {
            'displayName': me.get('displayName'),
            'email': me.get('mail') or me.get('userPrincipalName'),
            'id': me.get('id'),
        }

        # Step 2: validate scope (varies by mode)
        if self._is_onedrive():
            drive_resp = self._make_graph_request('/me/drive')
            if drive_resp.status_code == 200:
                drive = drive_resp.json()
                quota = drive.get('quota', {}) or {}
                return {
                    'success': True,
                    'data': {
                        'message': "Connected to OneDrive as {0}.".format(
                            user_info.get('displayName') or user_info.get('email') or 'Microsoft user'
                        ),
                        'user': user_info,
                        'drive_type': drive.get('driveType'),
                        'quota_total_bytes': quota.get('total'),
                        'quota_used_bytes': quota.get('used'),
                        'integration_id': integration_id,
                    },
                    'error': None,
                    'raw_response': json.dumps({'user': user_info, 'driveType': drive.get('driveType')})[:5000],
                }

            if drive_resp.status_code == 403:
                return {
                    'success': False,
                    'error': (
                        "Signed in as {0}, but OneDrive access is denied (403). "
                        "Make sure the Azure app has the 'Files.Read' delegated permission."
                    ).format(user_info.get('displayName') or 'user'),
                    'data': {'user': user_info, 'onedrive_accessible': False},
                    'raw_response': drive_resp.text[:2000],
                }

            return self._friendly_error(drive_resp, '/me/drive')

        # SharePoint mode
        sites_resp = self._make_graph_request('/sites', params={'search': '*', '$top': 1})

        if sites_resp.status_code == 200:
            sites_count = len(sites_resp.json().get('value', []))
            return {
                'success': True,
                'data': {
                    'message': "Connected to SharePoint as {0}.".format(
                        user_info.get('displayName') or user_info.get('email') or 'Microsoft user'
                    ),
                    'user': user_info,
                    'sharepoint_accessible': True,
                    'sites_visible': sites_count,
                    'integration_id': integration_id,
                },
                'error': None,
                'raw_response': json.dumps(user_info)[:5000],
            }

        if sites_resp.status_code == 403:
            return {
                'success': False,
                'error': (
                    "Signed in as {0}, but SharePoint access is denied (403). "
                    "This usually means the 'Sites.Read.All' permission needs admin consent. "
                    "Have an Azure AD admin go to Azure Portal -> App Registrations -> "
                    "your app -> API Permissions, and click 'Grant admin consent'."
                ).format(user_info.get('displayName') or 'user'),
                'data': {'user': user_info, 'sharepoint_accessible': False},
                'raw_response': sites_resp.text[:2000],
            }

        return self._friendly_error(sites_resp, '/sites')

    def _friendly_error(self, resp: requests.Response, endpoint: str) -> Dict:
        """Convert Graph API error responses to actionable messages."""
        code = ''
        msg = ''
        try:
            body = resp.json()
            err = body.get('error', {})
            if isinstance(err, dict):
                code = err.get('code', '')
                msg = err.get('message', '')
            else:
                msg = str(err)
        except Exception:
            msg = resp.text[:300]

        if 'AADSTS65001' in msg:
            friendly = (
                "Admin consent has not been granted for this app's permissions. "
                "Have an Azure AD admin grant consent in Azure Portal -> "
                "App Registrations -> your app -> API Permissions."
            )
        elif 'AADSTS50020' in msg or 'AADSTS50034' in msg:
            friendly = (
                "The signed-in account is not valid for this tenant. "
                "Sign out and reconnect with an account that belongs to the correct tenant."
            )
        elif resp.status_code == 401:
            friendly = (
                "Microsoft rejected the access token. Disconnect and reconnect this integration."
            )
        elif resp.status_code == 403:
            # Include Graph's own message — it's usually more specific than
            # our friendly text (e.g. "accessDenied: tokenMissingRoles").
            # Common causes for app-only:
            #   1. Stale cached token from before permissions were granted
            #      (we auto-retry once with a fresh token for write ops)
            #   2. Required Application permission missing or not consented
            #   3. Tenant policy restricting app access to specific sites
            graph_msg = msg or 'no error message returned'
            friendly = (
                "Access denied for {0}. Microsoft Graph says: \"{1}\". "
                "If you recently added permissions to the Azure app, the "
                "cached service token may not have the new roles yet — "
                "POST /api/integrations/<id>/refresh-token to force a "
                "fresh token. Otherwise, verify the Application permission "
                "is added AND admin consent is granted."
            ).format(endpoint, graph_msg[:300])
        elif resp.status_code == 404:
            friendly = "Resource not found at {0}. The request may be malformed.".format(endpoint)
        elif resp.status_code == 429:
            friendly = "Microsoft Graph is rate-limiting requests. Wait a moment and try again."
        elif resp.status_code >= 500:
            friendly = "Microsoft Graph is currently having issues. Try again in a few minutes."
        else:
            friendly = "{0}: {1}".format(code, msg) if code else (msg or "Unknown error")

        return {
            'success': False,
            'error': "Graph API {0}: {1}".format(resp.status_code, friendly),
            'data': None,
            'raw_response': resp.text[:5000],
        }

    @staticmethod
    def _format_site(s: Dict) -> Dict:
        return {
            'id': s.get('id'),
            'name': s.get('displayName') or s.get('name'),
            'description': s.get('description', ''),
            'webUrl': s.get('webUrl'),
            'hostname': (s.get('siteCollection') or {}).get('hostname', ''),
        }

    def _list_sites(self, params: Dict) -> Dict:
        """List SharePoint sites by combining three sources:
          1. /sites/root          — tenant root (always works, no indexing delay)
          2. /sites?search={q}    — search-indexed sites (notoriously incomplete)
          3. /beta/sites/getAllSites — comprehensive tenant-wide listing
                                       (app-only with Sites.Read.All)
        Results are merged and deduplicated by site ID."""
        query = params.get('query', '*').strip() or '*'

        sites_by_id = {}
        sources_used = []

        # Source 1: tenant root (no indexing delay)
        root_resp = self._make_graph_request('/sites/root')
        if root_resp.status_code == 200:
            root = root_resp.json()
            sid = root.get('id')
            if sid:
                sites_by_id[sid] = self._format_site(root)
                sources_used.append('root')

        # Source 2: /sites?search (incomplete but fast)
        search_resp = self._make_graph_request('/sites', params={'search': query})
        last_search_status = search_resp.status_code
        if search_resp.status_code == 200:
            for s in search_resp.json().get('value', []):
                sid = s.get('id')
                if sid and sid not in sites_by_id:
                    sites_by_id[sid] = self._format_site(s)
            sources_used.append('search')

        # Source 3: comprehensive tenant listing via beta endpoint
        # (most reliable for finding sites that aren't in the search index)
        try:
            beta_url = 'https://graph.microsoft.com/beta/sites/getAllSites'
            beta_headers = {
                'Authorization': f"Bearer {self._get_access_token()}",
                'Accept': 'application/json',
            }
            beta_resp = requests.get(
                beta_url, headers=beta_headers, timeout=30, allow_redirects=True
            )
            if beta_resp.status_code == 200:
                for s in beta_resp.json().get('value', []):
                    sid = s.get('id')
                    if sid and sid not in sites_by_id:
                        sites_by_id[sid] = self._format_site(s)
                sources_used.append('getAllSites')
            else:
                logger.info(
                    f"getAllSites returned {beta_resp.status_code} "
                    f"(non-fatal): {beta_resp.text[:200]}"
                )
        except Exception as e:
            logger.warning(f"getAllSites call failed (non-fatal): {e}")

        sites = list(sites_by_id.values())

        if not sites and last_search_status != 200:
            # Check if this is Sites.Selected — tenant-wide listing won't work
            token_roles = self._get_token_roles()
            if 'Sites.Selected' in token_roles:
                return {
                    'success': True,
                    'data': {
                        'sites': [],
                        'count': 0,
                        'permission_model': 'sites_selected',
                        'note': (
                            "This app uses Sites.Selected (per-site) permissions, "
                            "so tenant-wide site listing is not available. "
                            "Use 'Look up by URL' with the exact SharePoint site "
                            "URL to access a site that has been granted to this app."
                        ),
                    },
                    'error': None,
                }
            return self._error_from_response(search_resp)

        return {
            'success': True,
            'data': {
                'sites': sites,
                'count': len(sites),
                'sources_used': sources_used,
                'note': (
                    "If a site is still missing, use 'Lookup Site by URL' to "
                    "access it directly. Some sites (especially recently-created "
                    "team sites) may not be visible via search APIs."
                ),
            },
            'error': None,
            'raw_response': json.dumps({'count': len(sites), 'sources': sources_used})[:5000],
        }

    def _lookup_site_by_url(self, params: Dict) -> Dict:
        """Resolve a SharePoint site directly from its URL.
        Bypasses the search index — works for sites that haven't been
        indexed yet. Accepts URLs like:
          https://contoso.sharepoint.com/sites/MarketingTeam
          https://contoso.sharepoint.com (root site)
        """
        from urllib.parse import urlparse
        url = (params.get('url') or '').strip()
        if not url:
            return {'success': False, 'error': 'url is required', 'data': None}

        if not url.startswith('http'):
            url = 'https://' + url

        try:
            parsed = urlparse(url)
        except Exception as e:
            return {'success': False, 'error': f'Invalid URL: {e}', 'data': None}

        hostname = parsed.hostname or ''
        path = parsed.path or ''
        if not hostname:
            return {'success': False, 'error': 'Could not parse hostname from URL', 'data': None}

        # Build /sites/{hostname}:{path} or /sites/{hostname} for root
        if path and path != '/':
            endpoint = f"/sites/{hostname}:{path.rstrip('/')}"
        else:
            endpoint = f"/sites/{hostname}"

        resp = self._make_graph_request(endpoint)
        if resp.status_code != 200:
            return self._error_from_response(resp)

        site = resp.json()
        return {
            'success': True,
            'data': {'site': self._format_site(site)},
            'error': None,
            'raw_response': json.dumps(site)[:5000],
        }

    def _list_drives(self, params: Dict) -> Dict:
        site_id = params.get('site_id', '').strip()
        if not site_id:
            return {'success': False, 'error': 'site_id is required', 'data': None}

        resp = self._make_graph_request(f'/sites/{site_id}/drives')
        if resp.status_code != 200:
            return self._error_from_response(resp)

        data = resp.json()
        drives = [
            {
                'id': d.get('id'),
                'name': d.get('name'),
                'description': d.get('description', ''),
                'driveType': d.get('driveType'),
                'webUrl': d.get('webUrl'),
                'quota': d.get('quota', {}),
            }
            for d in data.get('value', [])
        ]
        return {
            'success': True,
            'data': {'drives': drives, 'count': len(drives)},
            'error': None,
            'raw_response': json.dumps(data)[:5000],
        }

    def _list_items(self, params: Dict) -> Dict:
        drive_id = params.get('drive_id', '').strip()
        if not drive_id:
            return {'success': False, 'error': 'drive_id is required', 'data': None}

        item_id = params.get('item_id', '').strip()
        top = min(int(params.get('top', 200)), 999)
        type_filter = params.get('type_filter', '')

        if item_id:
            endpoint = f'/drives/{drive_id}/items/{item_id}/children'
        else:
            endpoint = f'/drives/{drive_id}/root/children'

        resp = self._make_graph_request(endpoint, params={'$top': top})
        if resp.status_code != 200:
            return self._error_from_response(resp)

        data = resp.json()
        items = [self._format_item(i) for i in data.get('value', [])]
        items = self._apply_type_filter(items, type_filter)
        next_link = data.get('@odata.nextLink')

        return {
            'success': True,
            'data': {
                'items': items,
                'count': len(items),
                'hasMore': next_link is not None,
                'nextLink': next_link,
                'typeFilter': type_filter or 'all',
            },
            'error': None,
            'raw_response': json.dumps(data)[:5000],
        }

    def _list_my_files(self, params: Dict) -> Dict:
        """OneDrive-only: list items at the root or inside a folder of /me/drive."""
        item_id = params.get('item_id', '').strip()
        top = min(int(params.get('top', 200)), 999)
        type_filter = params.get('type_filter', '')

        if item_id:
            endpoint = f'/me/drive/items/{item_id}/children'
        else:
            endpoint = '/me/drive/root/children'

        resp = self._make_graph_request(endpoint, params={'$top': top})
        if resp.status_code != 200:
            return self._error_from_response(resp)

        data = resp.json()
        items = [self._format_item(i) for i in data.get('value', [])]
        items = self._apply_type_filter(items, type_filter)
        next_link = data.get('@odata.nextLink')

        return {
            'success': True,
            'data': {
                'items': items,
                'count': len(items),
                'hasMore': next_link is not None,
                'nextLink': next_link,
                'typeFilter': type_filter or 'all',
            },
            'error': None,
            'raw_response': json.dumps(data)[:5000],
        }

    def _item_endpoint(self, params: Dict) -> Optional[str]:
        """Build the right /items/{item_id} endpoint for the current mode.
        Returns None if required parameters are missing."""
        item_id = params.get('item_id', '').strip()
        if not item_id:
            return None
        if self._is_onedrive():
            return f'/me/drive/items/{item_id}'
        drive_id = params.get('drive_id', '').strip()
        if not drive_id:
            return None
        return f'/drives/{drive_id}/items/{item_id}'

    def _get_item_metadata(self, params: Dict) -> Dict:
        endpoint = self._item_endpoint(params)
        if not endpoint:
            return {
                'success': False,
                'error': 'item_id is required (and drive_id for SharePoint)',
                'data': None,
            }

        resp = self._make_graph_request(endpoint)
        if resp.status_code != 200:
            return self._error_from_response(resp)

        item = resp.json()
        return {
            'success': True,
            'data': self._format_item(item),
            'error': None,
            'raw_response': json.dumps(item)[:5000],
        }

    def _download_file(self, params: Dict) -> Dict:
        endpoint = self._item_endpoint(params)
        if not endpoint:
            return {
                'success': False,
                'error': 'item_id is required (and drive_id for SharePoint)',
                'data': None,
            }

        meta_resp = self._make_graph_request(endpoint)
        if meta_resp.status_code != 200:
            return self._error_from_response(meta_resp)

        meta = meta_resp.json()
        if 'folder' in meta:
            return {'success': False, 'error': 'Cannot download a folder', 'data': None}

        filename = meta.get('name', 'download')
        file_size = meta.get('size', 0)
        max_bytes = MAX_DOWNLOAD_SIZE_MB * 1024 * 1024
        if file_size > max_bytes:
            return {
                'success': False,
                'error': f"File too large ({file_size / (1024*1024):.1f} MB). "
                         f"Max allowed is {MAX_DOWNLOAD_SIZE_MB} MB.",
                'data': None,
            }

        target_dir = self._resolve_dest_dir(params.get('dest_folder', ''))
        conflict = params.get('conflict_behavior', 'replace')
        dest_path = self._resolve_conflict_path(target_dir, filename, conflict)
        if dest_path is None:
            return {
                'success': False,
                'error': (
                    f"File '{filename}' already exists in {target_dir} and "
                    f"conflict_behavior='fail'."
                ),
                'data': None,
            }

        content_resp = self._make_graph_request(endpoint + '/content', stream=True)
        if content_resp.status_code not in (200, 302):
            return self._error_from_response(content_resp)

        with open(dest_path, 'wb') as f:
            for chunk in content_resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        actual_size = os.path.getsize(dest_path)
        saved_filename = os.path.basename(dest_path)
        logger.info(
            f"Downloaded {('OneDrive' if self._is_onedrive() else 'SharePoint')} "
            f"file '{filename}' ({actual_size} bytes) → {dest_path}"
        )

        result_data = {
            'filename': filename,
            'saved_as': saved_filename,
            'local_path': dest_path,
            'dest_dir': target_dir,
            'size': actual_size,
            'item_id': params.get('item_id', '').strip(),
        }
        if not self._is_onedrive():
            result_data['drive_id'] = params.get('drive_id', '').strip()

        return {
            'success': True,
            'data': result_data,
            'error': None,
            'raw_response': json.dumps({'filename': filename, 'size': actual_size})[:5000],
        }

    # =========================================================================
    # Path-based & write operations (workflow-friendly)
    # =========================================================================

    @staticmethod
    def _encode_path(path: str) -> str:
        """URL-encode a SharePoint relative path while preserving slashes."""
        from urllib.parse import quote
        return quote(path.strip().strip('/'), safe='/')

    @staticmethod
    def _normalize_modified_after(value: str) -> Optional[str]:
        """Normalize a user-supplied date/datetime into an ISO8601 UTC
        string that Graph's OData $filter accepts.

        Accepts:
          - YYYY-MM-DD (treated as start-of-day UTC)
          - YYYY-MM-DDTHH:MM:SS (Z appended if missing)
          - Already-ISO strings (passed through)
          - Empty / None (returns None)
        """
        v = (value or '').strip()
        if not v:
            return None
        # Date-only -> start of day UTC
        if len(v) == 10 and v[4] == '-' and v[7] == '-':
            return v + 'T00:00:00Z'
        # Strip trailing 'Z' / timezone if odd, then re-add
        if v.endswith('Z'):
            return v
        # Naive datetime — assume UTC
        return v + ('Z' if 'T' in v else 'T00:00:00Z')

    def _list_folder_by_path(self, params: Dict) -> Dict:
        """List files/folders inside a folder identified by path (no item_id
        needed). Workflow-friendly: drive_id is static, folder_path is a
        readable string the workflow author types in.

        Optional filters:
          file_pattern    — glob filter (e.g. '*.pdf', 'Q3_*.xlsx')
          modified_after  — ISO date/datetime; only files modified after this
                            time are returned. Use this for "give me new
                            files since the last run" workflows.
        """
        drive_id = (params.get('drive_id') or '').strip()
        folder_path = (params.get('folder_path') or '').strip().strip('/')
        if not drive_id:
            return {'success': False, 'error': 'drive_id is required', 'data': None}

        if folder_path:
            endpoint = f"/drives/{drive_id}/root:/{self._encode_path(folder_path)}:/children"
        else:
            endpoint = f"/drives/{drive_id}/root/children"

        top = min(int(params.get('top', 200)), 999)
        query: Dict[str, Any] = {'$top': top}

        # Server-side filter by lastModifiedDateTime when modified_after is provided
        modified_after = self._normalize_modified_after(params.get('modified_after'))
        if modified_after:
            query['$filter'] = f"lastModifiedDateTime gt {modified_after}"
            # Ordering by modified date makes pagination predictable for "newest" semantics
            query['$orderby'] = 'lastModifiedDateTime desc'

        resp = self._make_graph_request(endpoint, params=query)
        if resp.status_code != 200:
            return self._error_from_response(resp)

        data = resp.json()
        items = [self._format_item(i) for i in data.get('value', [])]

        pattern = (params.get('file_pattern') or '').strip()
        if pattern:
            import fnmatch
            items = [
                i for i in items
                if i.get('type') == 'folder'
                or fnmatch.fnmatch((i.get('name') or '').lower(), pattern.lower())
            ]

        # Apply type filter AFTER pattern matching. If the user asks for
        # "files only" or "folders only", drop the other kind regardless of
        # what file_pattern preserved.
        type_filter = params.get('type_filter', '')
        items = self._apply_type_filter(items, type_filter)

        return {
            'success': True,
            'data': {
                'items': items,
                'count': len(items),
                'folder_path': folder_path,
                'modified_after': modified_after,
                'file_pattern': pattern or None,
                'typeFilter': type_filter or 'all',
                'hasMore': data.get('@odata.nextLink') is not None,
            },
            'error': None,
            'raw_response': json.dumps({'count': len(items)})[:5000],
        }

    @staticmethod
    def _sanitize_subfolder(folder: str) -> str:
        """Sanitize a user-supplied subfolder so it can be safely appended
        to the uploads root. Strips leading/trailing separators, blocks
        '..' traversal, and replaces filesystem-unsafe characters with
        underscores. Returns '' if the input is empty or fully sanitized
        away."""
        if not folder:
            return ''
        folder = folder.replace('\\', '/').strip().strip('/')
        parts = []
        for raw in folder.split('/'):
            part = raw.strip()
            if not part or part in ('.', '..'):
                continue
            safe = ''.join(c if (c.isalnum() or c in '-_. ') else '_' for c in part)
            safe = safe.strip()
            if safe:
                parts.append(safe)
        return '/'.join(parts)

    def _resolve_dest_dir(self, dest_folder: str) -> str:
        """Resolve the final absolute directory where a downloaded file
        should land.

        Rules:
          - Empty / None         → <APP_ROOT>/uploads/   (legacy default)
          - Absolute path        → use as-is (C:\\temp\\out, /var/data, etc.)
          - Relative path        → rooted under <APP_ROOT>/uploads/ with
                                   '..' traversal stripped and unsafe
                                   characters in each component replaced
                                   by underscores

        Absolute paths are trusted because workflow nodes are configured by
        admins/developers, not end-users — if you point a node at a system
        directory, that's on you. The directory is created automatically.
        """
        upload_root = os.path.join(os.getenv('APP_ROOT', '.'), cfg.APP_UPLOADS_FOLDER)
        folder = (dest_folder or '').strip()
        if not folder:
            target_dir = upload_root
        else:
            # Normalize separators so os.path.isabs handles either flavor on
            # Windows ('C:/temp' vs 'C:\\temp' should both be recognized).
            normalized = folder.replace('/', os.sep) if os.sep != '/' else folder
            if os.path.isabs(normalized):
                target_dir = os.path.normpath(normalized)
            else:
                sanitized = self._sanitize_subfolder(folder)
                target_dir = os.path.normpath(
                    os.path.join(upload_root, sanitized) if sanitized else upload_root
                )

        try:
            os.makedirs(target_dir, exist_ok=True)
        except OSError as e:
            logger.error(
                f"_resolve_dest_dir: could not create '{target_dir}': {e} — "
                f"falling back to upload root"
            )
            target_dir = upload_root
            os.makedirs(target_dir, exist_ok=True)
        return target_dir

    @staticmethod
    def _resolve_conflict_path(target_dir: str, filename: str,
                                conflict_behavior: str) -> Optional[str]:
        """Decide the final on-disk path for a file based on conflict policy.

        Returns None if conflict_behavior == 'fail' and a file already exists
        (caller treats None as an error). Otherwise returns the path to write to.
        """
        dest_path = os.path.join(target_dir, filename)
        if not os.path.exists(dest_path):
            return dest_path

        behavior = (conflict_behavior or 'replace').strip().lower()
        if behavior == 'replace':
            return dest_path
        if behavior == 'fail':
            return None
        # 'rename' — pick the first available name<n>.ext
        stem, ext = os.path.splitext(filename)
        for i in range(1, 1000):
            candidate = os.path.join(target_dir, f"{stem} ({i}){ext}")
            if not os.path.exists(candidate):
                return candidate
        # Pathological fallback — UUID suffix
        return os.path.join(target_dir, f"{stem}_{uuid.uuid4().hex[:8]}{ext}")

    def _download_item_to_disk(self, drive_id: str, content_endpoint: str,
                                meta: Dict, prefix: str = 'sp',
                                dest_folder: str = '',
                                conflict_behavior: str = 'replace') -> Dict:
        """Shared download logic — given a /content endpoint and pre-fetched
        metadata, stream to a directory and return local_path info.

        Preserves the original filename. When a file with the same name
        already exists at the destination, the conflict_behavior parameter
        decides what happens:
          - 'replace' (default) — overwrite the existing file
          - 'rename'            — write to 'name (1).ext', 'name (2).ext', ...
          - 'fail'              — return an error without writing

        prefix is no longer used to prepend to filenames; it's kept on the
        signature only so existing callers don't have to change.
        """
        filename = meta.get('name', 'download')
        file_size = meta.get('size', 0)
        max_bytes = MAX_DOWNLOAD_SIZE_MB * 1024 * 1024
        if file_size > max_bytes:
            return {
                'success': False,
                'error': (
                    f"File too large ({file_size / (1024*1024):.1f} MB). "
                    f"Max allowed is {MAX_DOWNLOAD_SIZE_MB} MB."
                ),
                'data': None,
            }

        target_dir = self._resolve_dest_dir(dest_folder)
        dest_path = self._resolve_conflict_path(target_dir, filename, conflict_behavior)
        if dest_path is None:
            return {
                'success': False,
                'error': (
                    f"File '{filename}' already exists in {target_dir} and "
                    f"conflict_behavior='fail'."
                ),
                'data': None,
            }

        content_resp = self._make_graph_request(content_endpoint, stream=True)
        if content_resp.status_code not in (200, 302):
            return self._error_from_response(content_resp)

        with open(dest_path, 'wb') as f:
            for chunk in content_resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        actual_size = os.path.getsize(dest_path)
        saved_filename = os.path.basename(dest_path)
        logger.info(
            f"Downloaded SharePoint file '{filename}' "
            f"({actual_size} bytes) -> {dest_path}"
        )
        return {
            'success': True,
            'data': {
                'filename': filename,
                'saved_as': saved_filename,
                'local_path': dest_path,
                'dest_dir': target_dir,
                'size': actual_size,
                'item_id': meta.get('id'),
                'drive_id': drive_id,
                'webUrl': meta.get('webUrl'),
            },
            'error': None,
            'raw_response': json.dumps({'filename': filename, 'size': actual_size})[:5000],
        }

    def _download_file_by_path(self, params: Dict) -> Dict:
        """Download a single file given its path within a drive.
        Workflow-friendly: works when the filename comes from a previous
        node's output (e.g. {selected_filename})."""
        drive_id = (params.get('drive_id') or '').strip()
        file_path = (params.get('file_path') or '').strip().strip('/')
        if not drive_id or not file_path:
            return {
                'success': False,
                'error': 'drive_id and file_path are required',
                'data': None,
            }

        encoded = self._encode_path(file_path)
        meta_endpoint = f"/drives/{drive_id}/root:/{encoded}"
        meta_resp = self._make_graph_request(meta_endpoint)
        if meta_resp.status_code != 200:
            return self._error_from_response(meta_resp)

        meta = meta_resp.json()
        if 'folder' in meta:
            return {
                'success': False,
                'error': f"Path '{file_path}' is a folder, not a file",
                'data': None,
            }

        content_endpoint = f"/drives/{drive_id}/root:/{encoded}:/content"
        return self._download_item_to_disk(
            drive_id, content_endpoint, meta,
            dest_folder=params.get('dest_folder', ''),
            conflict_behavior=params.get('conflict_behavior', 'replace'),
        )

    def _download_folder(self, params: Dict) -> Dict:
        """Download every (matching) file from a SharePoint folder. Returns
        an array of local_path entries. Workflow use case: 'process every
        new PDF in /Inbox since yesterday'."""
        drive_id = (params.get('drive_id') or '').strip()
        folder_path = (params.get('folder_path') or '').strip()
        file_pattern = (params.get('file_pattern') or '').strip()
        modified_after = params.get('modified_after')
        max_files = min(int(params.get('max_files', 50)), 500)
        dest_folder = params.get('dest_folder', '') or ''
        conflict_behavior = params.get('conflict_behavior', 'replace')

        if not drive_id:
            return {'success': False, 'error': 'drive_id is required', 'data': None}

        list_result = self._list_folder_by_path({
            'drive_id': drive_id,
            'folder_path': folder_path,
            'file_pattern': file_pattern,
            'modified_after': modified_after,
            'top': max_files * 2,  # over-fetch so we can filter folders
        })
        if not list_result.get('success'):
            return list_result

        items = list_result['data']['items']
        files = [i for i in items if i.get('type') == 'file'][:max_files]

        # Resolve the destination once so every file in the batch lands in
        # the same directory (and we can report it back to the caller).
        target_dir = self._resolve_dest_dir(dest_folder)

        downloaded = []
        errors = []
        for item in files:
            dl = self._download_file({
                'drive_id': drive_id,
                'item_id': item.get('id'),
                'dest_folder': dest_folder,
                'conflict_behavior': conflict_behavior,
            })
            if dl.get('success'):
                downloaded.append(dl['data'])
            else:
                errors.append({'name': item.get('name'), 'error': dl.get('error')})

        return {
            'success': len(errors) == 0,
            'data': {
                'downloaded': downloaded,
                'count': len(downloaded),
                'errors': errors,
                'folder_path': folder_path,
                'file_pattern': file_pattern or None,
                'modified_after': list_result['data'].get('modified_after'),
                'dest_dir': target_dir,
            },
            'error': '; '.join(e.get('error', '') for e in errors) if errors else None,
            'raw_response': json.dumps({'count': len(downloaded), 'errors': len(errors)})[:5000],
        }

    def _upload_file(self, params: Dict) -> Dict:
        """Upload a local file to a SharePoint folder. Picks the right Graph
        upload method automatically based on size (simple PUT for <4MB,
        upload session for larger files).

        Destination can be specified two ways:
          - parent_item_id (preferred for workflows iterating by id): the
            SharePoint item_id of the parent folder.
          - folder_path: relative path under the drive root, e.g.
            "Shared Documents/Inbox/Walmart/DI".

        If both are provided, parent_item_id wins (most precise).

        conflict_behavior:
          - 'rename'  (default): append (1), (2), ... to avoid collision
          - 'replace': overwrite the existing file in place
          - 'fail':    return an error if a file with that name exists
        """
        drive_id = (params.get('drive_id') or '').strip()
        folder_path = (params.get('folder_path') or '').strip().strip('/')
        parent_item_id = (params.get('parent_item_id') or '').strip()
        source_file_path = (params.get('source_file_path') or '').strip()
        filename = (params.get('filename') or '').strip()
        conflict = (params.get('conflict_behavior') or 'rename').strip().lower()

        if conflict not in ('rename', 'replace', 'fail'):
            conflict = 'rename'

        if not drive_id or not source_file_path:
            return {
                'success': False,
                'error': 'drive_id and source_file_path are required',
                'data': None,
            }

        if not os.path.isfile(source_file_path):
            return {
                'success': False,
                'error': f"Local file not found: {source_file_path}",
                'data': None,
            }

        if not filename:
            filename = os.path.basename(source_file_path)

        file_size = os.path.getsize(source_file_path)

        # Build the target URL segment. Graph supports either:
        #   /drives/{drive_id}/items/{parent_id}:/{filename}     (id-based)
        #   /drives/{drive_id}/root:/{folder_path}/{filename}    (path-based)
        # Then append :/content or :/createUploadSession.
        encoded_filename = self._encode_path(filename)
        if parent_item_id:
            target_segment = f"items/{parent_item_id}:/{encoded_filename}"
        elif folder_path:
            target_segment = f"root:/{self._encode_path(folder_path)}/{encoded_filename}"
        else:
            target_segment = f"root:/{encoded_filename}"

        # Simple PUT for small files (<4MB is Graph's limit for this endpoint)
        SIMPLE_LIMIT = 4 * 1024 * 1024
        if file_size <= SIMPLE_LIMIT:
            return self._upload_simple(
                drive_id, target_segment, source_file_path, conflict, file_size
            )
        return self._upload_session(
            drive_id, target_segment, source_file_path, conflict, file_size
        )

    def _upload_simple(
        self, drive_id: str, target_segment: str,
        source_file_path: str, conflict: str, file_size: int,
    ) -> Dict:
        """Simple PUT upload for files <= 4MB.

        target_segment is the URL piece between '/drives/{drive_id}/' and
        ':/content', e.g. 'root:/Folder/file.xlsx' (path-based) or
        'items/{parent_id}:/file.xlsx' (id-based). _upload_file builds it.

        Retries once with a fresh token on 403, because cached service tokens
        don't reflect Azure permission changes until the token is reissued —
        a brand-new write permission on the Azure app won't take effect until
        the next token mint.
        """
        url = (
            f"{GRAPH_BASE_URL}/drives/{drive_id}/{target_segment}:/content"
            f"?@microsoft.graph.conflictBehavior={conflict}"
        )
        with open(source_file_path, 'rb') as f:
            content = f.read()

        def attempt():
            return requests.put(
                url,
                headers={
                    'Authorization': f"Bearer {self._get_access_token()}",
                    'Content-Type': 'application/octet-stream',
                },
                data=content,
                timeout=120,
            )

        resp = attempt()
        if resp.status_code == 403 and self._is_app_only():
            logger.info(
                "Upload returned 403; forcing token refresh and retrying once "
                "in case Azure app permissions were recently changed"
            )
            if self._force_refresh_token():
                resp = attempt()

        if resp.status_code not in (200, 201):
            return self._error_from_response(resp)

        item = resp.json()
        logger.info(
            f"Uploaded {file_size} bytes to SharePoint: {item.get('webUrl')} "
            f"(item_id={item.get('id')}, conflict={conflict})"
        )
        return {
            'success': True,
            'data': self._format_upload_response(item, conflict, file_size),
            'error': None,
            'raw_response': json.dumps({
                'item_id': item.get('id'), 'name': item.get('name')
            })[:5000],
        }

    def _upload_session(
        self, drive_id: str, target_segment: str,
        source_file_path: str, conflict: str, file_size: int,
    ) -> Dict:
        """Chunked upload session for files > 4MB. Up to 60MB chunks; we use
        10MB which is well within limits and reasonable for network hiccups.

        target_segment is the URL piece between '/drives/{drive_id}/' and
        ':/createUploadSession'. See _upload_simple for format details.
        """
        # Step 1: create the upload session.
        # Retry once on 403 in case Azure permissions were recently changed
        # and the cached service token doesn't include the write roles yet.
        session_url = (
            f"{GRAPH_BASE_URL}/drives/{drive_id}/{target_segment}"
            f":/createUploadSession"
        )
        body = {'item': {'@microsoft.graph.conflictBehavior': conflict}}

        def open_session():
            return requests.post(
                session_url,
                headers={
                    'Authorization': f"Bearer {self._get_access_token()}",
                    'Content-Type': 'application/json',
                },
                json=body,
                timeout=60,
            )

        sess_resp = open_session()
        if sess_resp.status_code == 403 and self._is_app_only():
            logger.info(
                "createUploadSession returned 403; forcing token refresh "
                "and retrying once"
            )
            if self._force_refresh_token():
                sess_resp = open_session()

        if sess_resp.status_code not in (200, 201):
            return self._error_from_response(sess_resp)

        upload_url = sess_resp.json().get('uploadUrl')
        if not upload_url:
            return {
                'success': False,
                'error': 'No uploadUrl returned from createUploadSession',
                'data': None,
            }

        # Step 2: PUT chunks of 10MB to the uploadUrl (which is a pre-signed
        # URL — no auth header needed; Graph rejects Authorization here)
        CHUNK = 10 * 1024 * 1024
        final_resp = None
        with open(source_file_path, 'rb') as f:
            offset = 0
            while offset < file_size:
                chunk = f.read(CHUNK)
                end = offset + len(chunk) - 1
                chunk_resp = requests.put(
                    upload_url,
                    headers={
                        'Content-Length': str(len(chunk)),
                        'Content-Range': f"bytes {offset}-{end}/{file_size}",
                    },
                    data=chunk,
                    timeout=600,
                )
                if chunk_resp.status_code not in (200, 201, 202):
                    return self._error_from_response(chunk_resp)
                offset += len(chunk)
                final_resp = chunk_resp

        if final_resp is None or final_resp.status_code not in (200, 201):
            return {
                'success': False,
                'error': 'Upload session completed but final response was not 201/200',
                'data': None,
            }

        item = final_resp.json()
        logger.info(
            f"Uploaded {file_size} bytes (chunked) to SharePoint: "
            f"{item.get('webUrl')} (conflict={conflict})"
        )
        return {
            'success': True,
            'data': self._format_upload_response(item, conflict, file_size),
            'error': None,
            'raw_response': json.dumps({
                'item_id': item.get('id'), 'name': item.get('name')
            })[:5000],
        }

    @staticmethod
    def _format_upload_response(item: Dict, conflict: str, size: int) -> Dict:
        return {
            'item_id': item.get('id'),
            'name': item.get('name'),
            'size': item.get('size', size),
            'webUrl': item.get('webUrl'),
            'created': item.get('createdDateTime'),
            'modified': item.get('lastModifiedDateTime'),
            'conflict_behavior': conflict,
            'parentReference': item.get('parentReference', {}),
        }

    def _upload_content(self, params: Dict) -> Dict:
        """Upload a string or JSON value as a file. For workflows that
        produce text/JSON output and want to drop it into SharePoint."""
        drive_id = (params.get('drive_id') or '').strip()
        folder_path = (params.get('folder_path') or '').strip().strip('/')
        filename = (params.get('filename') or '').strip()
        content = params.get('content', '')
        conflict = (params.get('conflict_behavior') or 'rename').strip().lower()

        if not drive_id or not filename:
            return {
                'success': False,
                'error': 'drive_id and filename are required',
                'data': None,
            }

        if not isinstance(content, str):
            try:
                content = json.dumps(content, indent=2, default=str)
            except (TypeError, ValueError):
                content = str(content)

        # Write to a temp file in /uploads and reuse the file upload path
        import tempfile
        upload_dir = os.path.join(os.getenv('APP_ROOT', '.'), cfg.APP_UPLOADS_FOLDER)
        os.makedirs(upload_dir, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='_' + filename, dir=upload_dir,
            delete=False, encoding='utf-8',
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            return self._upload_file({
                'drive_id': drive_id,
                'folder_path': folder_path,
                'filename': filename,
                'source_file_path': tmp_path,
                'conflict_behavior': conflict,
            })
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _delete_file(self, params: Dict) -> Dict:
        """Delete a file from SharePoint, either by item_id or by file_path.
        Workflow use case: clean up source files after processing."""
        drive_id = (params.get('drive_id') or '').strip()
        item_id = (params.get('item_id') or '').strip()
        file_path = (params.get('file_path') or '').strip().strip('/')

        if not drive_id or (not item_id and not file_path):
            return {
                'success': False,
                'error': 'drive_id plus either item_id or file_path are required',
                'data': None,
            }

        if item_id:
            endpoint = f"/drives/{drive_id}/items/{item_id}"
        else:
            endpoint = f"/drives/{drive_id}/root:/{self._encode_path(file_path)}"

        url = f"{GRAPH_BASE_URL}{endpoint}"

        def attempt():
            return requests.delete(
                url,
                headers={'Authorization': f"Bearer {self._get_access_token()}"},
                timeout=60,
            )

        resp = attempt()
        if resp.status_code == 403 and self._is_app_only():
            logger.info(
                "Delete returned 403; forcing token refresh and retrying once"
            )
            if self._force_refresh_token():
                resp = attempt()

        if resp.status_code not in (200, 204):
            return self._error_from_response(resp)

        logger.info(
            f"Deleted SharePoint item (drive={drive_id}, "
            f"item={item_id or file_path})"
        )
        return {
            'success': True,
            'data': {
                'deleted': True,
                'drive_id': drive_id,
                'item_id': item_id or None,
                'file_path': file_path or None,
            },
            'error': None,
            'raw_response': '',
        }

    # =========================================================================
    # Folder / file management operations
    # =========================================================================

    def _create_folder(self, params: Dict) -> Dict:
        """Create a folder (and any missing parents) inside a SharePoint drive.

        Graph API: POST /drives/{drive_id}/root:/{parent_path}:/children
        with body {"name": "FolderName", "folder": {}, "@microsoft.graph.conflictBehavior": "..."}
        """
        drive_id = (params.get('drive_id') or '').strip()
        folder_path = (params.get('folder_path') or '').strip().strip('/')

        if not drive_id or not folder_path:
            return {
                'success': False,
                'error': 'drive_id and folder_path are required',
                'data': None,
            }

        conflict = params.get('conflict_behavior', 'fail')
        if conflict not in ('fail', 'rename', 'replace'):
            conflict = 'fail'

        # Walk the path segments and create each level.  Graph's simple
        # POST only creates a single level, so for "A/B/C" we need to
        # create "A", then "A/B", then "A/B/C".
        segments = [s for s in folder_path.split('/') if s]
        created = []
        current_parent = ''

        for seg in segments:
            if current_parent:
                parent_endpoint = f"/drives/{drive_id}/root:/{self._encode_path(current_parent)}:/children"
            else:
                parent_endpoint = f"/drives/{drive_id}/root/children"

            url = f"{GRAPH_BASE_URL}{parent_endpoint}"
            body = {
                'name': seg,
                'folder': {},
                '@microsoft.graph.conflictBehavior': conflict,
            }

            def attempt():
                return requests.post(
                    url,
                    headers={
                        'Authorization': f"Bearer {self._get_access_token()}",
                        'Content-Type': 'application/json',
                    },
                    json=body,
                    timeout=60,
                )

            resp = attempt()

            # 409 Conflict with 'fail' means folder already exists — that's OK
            # when creating intermediate parents.
            if resp.status_code == 409:
                current_parent = f"{current_parent}/{seg}" if current_parent else seg
                continue

            if resp.status_code == 403 and self._is_app_only():
                if self._force_refresh_token():
                    resp = attempt()

            if resp.status_code not in (200, 201):
                return self._error_from_response(resp)

            item = resp.json()
            created.append({
                'id': item.get('id'),
                'name': item.get('name'),
                'webUrl': item.get('webUrl'),
            })
            current_parent = f"{current_parent}/{seg}" if current_parent else seg

        logger.info(f"Created folder path '{folder_path}' in drive {drive_id}")
        return {
            'success': True,
            'data': {
                'folder_path': folder_path,
                'created_count': len(created),
                'folders': created,
                'drive_id': drive_id,
            },
            'error': None,
        }

    def _move_file(self, params: Dict) -> Dict:
        """Move a file to a different folder within the same drive.

        Graph API: PATCH /drives/{drive_id}/items/{item_id}
        with body {"parentReference": {"path": "/drives/{drive_id}/root:/{dest_path}"}}

        Supports identification by item_id or file_path (resolves to item_id first).
        """
        drive_id = (params.get('drive_id') or '').strip()
        item_id = (params.get('item_id') or '').strip()
        file_path = (params.get('file_path') or '').strip().strip('/')
        dest_folder = (params.get('dest_folder_path') or '').strip().strip('/')

        if not drive_id or (not item_id and not file_path) or not dest_folder:
            return {
                'success': False,
                'error': 'drive_id, dest_folder_path, and either item_id or file_path are required',
                'data': None,
            }

        # Resolve file_path to item_id if needed
        if not item_id:
            item_id = self._resolve_item_id(drive_id, file_path)
            if not item_id:
                return {
                    'success': False,
                    'error': f"File not found at path: {file_path}",
                    'data': None,
                }

        url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}"
        body = {
            'parentReference': {
                'path': f"/drives/{drive_id}/root:/{self._encode_path(dest_folder)}",
            }
        }

        # Optionally rename during the move
        new_name = (params.get('new_name') or '').strip()
        if new_name:
            body['name'] = new_name

        def attempt():
            return requests.patch(
                url,
                headers={
                    'Authorization': f"Bearer {self._get_access_token()}",
                    'Content-Type': 'application/json',
                },
                json=body,
                timeout=60,
            )

        resp = attempt()
        if resp.status_code == 403 and self._is_app_only():
            if self._force_refresh_token():
                resp = attempt()

        if resp.status_code != 200:
            return self._error_from_response(resp)

        item = resp.json()
        logger.info(
            f"Moved item {item_id} to /{dest_folder} in drive {drive_id}"
        )
        return {
            'success': True,
            'data': {
                'id': item.get('id'),
                'name': item.get('name'),
                'webUrl': item.get('webUrl'),
                'dest_folder_path': dest_folder,
                'drive_id': drive_id,
            },
            'error': None,
            'raw_response': json.dumps(item)[:5000],
        }

    def _copy_file(self, params: Dict) -> Dict:
        """Copy a file to a different folder within the same drive.

        Graph API: POST /drives/{drive_id}/items/{item_id}/copy
        with body {"parentReference": {"driveId": "...", "path": "..."}, "name": "..."}

        The copy is asynchronous — Graph returns a 202 Accepted with a
        monitor URL.  We poll briefly (up to 30s) for completion.
        """
        drive_id = (params.get('drive_id') or '').strip()
        item_id = (params.get('item_id') or '').strip()
        file_path = (params.get('file_path') or '').strip().strip('/')
        dest_folder = (params.get('dest_folder_path') or '').strip().strip('/')

        if not drive_id or (not item_id and not file_path) or not dest_folder:
            return {
                'success': False,
                'error': 'drive_id, dest_folder_path, and either item_id or file_path are required',
                'data': None,
            }

        # Resolve file_path to item_id if needed
        if not item_id:
            item_id = self._resolve_item_id(drive_id, file_path)
            if not item_id:
                return {
                    'success': False,
                    'error': f"File not found at path: {file_path}",
                    'data': None,
                }

        url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}/copy"
        body = {
            'parentReference': {
                'driveId': drive_id,
                'path': f"/root:/{self._encode_path(dest_folder)}",
            }
        }

        new_name = (params.get('new_name') or '').strip()
        if new_name:
            body['name'] = new_name

        def attempt():
            return requests.post(
                url,
                headers={
                    'Authorization': f"Bearer {self._get_access_token()}",
                    'Content-Type': 'application/json',
                },
                json=body,
                timeout=60,
            )

        resp = attempt()
        if resp.status_code == 403 and self._is_app_only():
            if self._force_refresh_token():
                resp = attempt()

        # Graph returns 202 Accepted with a Location header for async monitor
        if resp.status_code == 202:
            monitor_url = resp.headers.get('Location', '')
            # Brief poll for completion (up to 30s)
            copy_result = self._poll_copy_status(monitor_url)
            return {
                'success': True,
                'data': {
                    'copy_status': copy_result.get('status', 'accepted'),
                    'dest_folder_path': dest_folder,
                    'drive_id': drive_id,
                    'source_item_id': item_id,
                    'new_item_id': copy_result.get('resourceId'),
                },
                'error': None,
            }

        if resp.status_code not in (200, 201):
            return self._error_from_response(resp)

        item = resp.json()
        logger.info(
            f"Copied item {item_id} to /{dest_folder} in drive {drive_id}"
        )
        return {
            'success': True,
            'data': {
                'id': item.get('id'),
                'name': item.get('name'),
                'webUrl': item.get('webUrl'),
                'dest_folder_path': dest_folder,
                'drive_id': drive_id,
            },
            'error': None,
            'raw_response': json.dumps(item)[:5000],
        }

    def _poll_copy_status(self, monitor_url: str, max_wait: int = 30) -> Dict:
        """Poll a Graph async copy monitor URL until done or timeout."""
        if not monitor_url:
            return {'status': 'accepted'}

        deadline = time.time() + max_wait
        while time.time() < deadline:
            try:
                resp = requests.get(monitor_url, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    status = data.get('status', '')
                    if status == 'completed':
                        return data
                    if status in ('failed', 'deleteFailed'):
                        return data
                time.sleep(2)
            except Exception:
                time.sleep(2)
        return {'status': 'timeout'}

    def _rename_file(self, params: Dict) -> Dict:
        """Rename a file or folder in place.

        Graph API: PATCH /drives/{drive_id}/items/{item_id}
        with body {"name": "new_name.ext"}
        """
        drive_id = (params.get('drive_id') or '').strip()
        item_id = (params.get('item_id') or '').strip()
        file_path = (params.get('file_path') or '').strip().strip('/')
        new_name = (params.get('new_name') or '').strip()

        if not drive_id or (not item_id and not file_path) or not new_name:
            return {
                'success': False,
                'error': 'drive_id, new_name, and either item_id or file_path are required',
                'data': None,
            }

        # Resolve file_path to item_id if needed
        if not item_id:
            item_id = self._resolve_item_id(drive_id, file_path)
            if not item_id:
                return {
                    'success': False,
                    'error': f"File not found at path: {file_path}",
                    'data': None,
                }

        url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}"
        body = {'name': new_name}

        def attempt():
            return requests.patch(
                url,
                headers={
                    'Authorization': f"Bearer {self._get_access_token()}",
                    'Content-Type': 'application/json',
                },
                json=body,
                timeout=60,
            )

        resp = attempt()
        if resp.status_code == 403 and self._is_app_only():
            if self._force_refresh_token():
                resp = attempt()

        if resp.status_code != 200:
            return self._error_from_response(resp)

        item = resp.json()
        logger.info(
            f"Renamed item {item_id} to '{new_name}' in drive {drive_id}"
        )
        return {
            'success': True,
            'data': {
                'id': item.get('id'),
                'name': item.get('name'),
                'webUrl': item.get('webUrl'),
                'drive_id': drive_id,
            },
            'error': None,
            'raw_response': json.dumps(item)[:5000],
        }

    def _resolve_item_id(self, drive_id: str, file_path: str) -> Optional[str]:
        """Resolve a relative file path to its Graph item ID.
        Returns None if the path doesn't exist."""
        encoded = self._encode_path(file_path)
        resp = self._make_graph_request(f"/drives/{drive_id}/root:/{encoded}")
        if resp.status_code == 200:
            return resp.json().get('id')
        return None

    # =========================================================================
    # Existing search operation
    # =========================================================================

    def _search_files(self, params: Dict) -> Dict:
        query = params.get('query', '').strip()
        if not query:
            return {'success': False, 'error': 'query is required', 'data': None}

        if self._is_onedrive():
            endpoint = f"/me/drive/root/search(q='{query}')"
        else:
            drive_id = params.get('drive_id', '').strip()
            if not drive_id:
                return {'success': False, 'error': 'drive_id is required for SharePoint search', 'data': None}
            endpoint = f"/drives/{drive_id}/root/search(q='{query}')"

        resp = self._make_graph_request(endpoint)
        if resp.status_code != 200:
            return self._error_from_response(resp)

        data = resp.json()
        items = [self._format_item(i) for i in data.get('value', [])]
        return {
            'success': True,
            'data': {'items': items, 'count': len(items)},
            'error': None,
            'raw_response': json.dumps(data)[:5000],
        }

    def _download_to_knowledge(self, params: Dict, context: Dict) -> Dict:
        """Fire-and-forget: spawn a background thread that downloads the file
        and runs it through the document processing pipeline, then return
        immediately with a job_id. Large documents (100+ pages) can take
        an hour or more — running synchronously would tie up the browser
        AJAX request and block the user.

        The background thread writes its own row to IntegrationExecutionLog
        when it completes, so users can see the result in the integration's
        execution log."""
        # Validate up-front so we fail fast on obvious mistakes (still synchronous)
        agent_id = params.get('agent_id')
        if not agent_id:
            return {'success': False, 'error': 'agent_id is required', 'data': None}
        if not (params.get('item_id') or '').strip():
            return {'success': False, 'error': 'item_id is required', 'data': None}

        import copy
        import threading
        import uuid as _uuid

        job_id = _uuid.uuid4().hex
        params_copy = copy.deepcopy(params)
        context_copy = copy.deepcopy(context)
        context_copy['_async_job_id'] = job_id
        integration_snapshot = copy.deepcopy(self.integration)
        template_ref = self.template  # template is read-only, no need to copy

        def worker():
            try:
                logger.info(
                    f"Background SharePoint import job {job_id} starting "
                    f"(integration={integration_snapshot.get('integration_id')}, "
                    f"item={params_copy.get('item_id')}, agent={params_copy.get('agent_id')})"
                )
                worker_executor = SharePointExecutor(integration_snapshot, template_ref)
                worker_executor._ensure_token_fresh()
                result = worker_executor._download_to_knowledge_sync(
                    params_copy, context_copy
                )
                # Tag the result so the log entry is identifiable as the
                # async completion (rather than the initial enqueue)
                result.setdefault('data', {}) and None
                if isinstance(result.get('data'), dict):
                    result['data']['job_id'] = job_id
                    result['data']['_async_completion'] = True
                worker_executor._log_execution(
                    'download_to_knowledge', params_copy, result, context_copy
                )
                logger.info(
                    f"Background SharePoint import job {job_id} finished: "
                    f"success={result.get('success')}, error={result.get('error')}"
                )
            except Exception as e:
                logger.error(
                    f"Background SharePoint import job {job_id} crashed: {e}",
                    exc_info=True,
                )
                # Still log the failure so the user sees it in the execution log
                try:
                    SharePointExecutor(integration_snapshot, template_ref)._log_execution(
                        'download_to_knowledge',
                        params_copy,
                        {
                            'success': False,
                            'error': f"Background import crashed: {e}",
                            'data': {'job_id': job_id, '_async_completion': True},
                            'response_time_ms': 0,
                            'raw_response': '',
                        },
                        context_copy,
                    )
                except Exception:
                    pass

        t = threading.Thread(
            target=worker, daemon=True, name=f"sp-import-{job_id[:8]}"
        )
        t.start()

        return {
            'success': True,
            'data': {
                'job_id': job_id,
                'status': 'queued',
                'message': (
                    "Import started in the background. Large documents "
                    "(100+ pages) can take up to two hours. The import will "
                    "continue running even if you close this window. "
                    "Check the integration's execution log for the result."
                ),
                'item_id': params.get('item_id'),
                'agent_id': agent_id,
            },
            'error': None,
            'raw_response': json.dumps({'job_id': job_id, 'status': 'queued'})[:5000],
        }

    def _import_folder_to_knowledge(self, params: Dict, context: Dict) -> Dict:
        """Fire-and-forget bulk version of download_to_knowledge.
        Lists matching files in a folder (with optional pattern and
        modified_after filters) and imports each one into the agent's
        knowledge base in a single background thread.

        Returns immediately with the matched count and a job_id. Per-file
        results are written to the integration execution log.
        """
        agent_id = params.get('agent_id')
        if not agent_id:
            return {'success': False, 'error': 'agent_id is required', 'data': None}

        drive_id = (params.get('drive_id') or '').strip()
        folder_path = (params.get('folder_path') or '').strip()
        if not drive_id or not folder_path:
            return {
                'success': False,
                'error': 'drive_id and folder_path are required',
                'data': None,
            }

        # List up-front (sync) so the user sees the matched count immediately
        listing = self._list_folder_by_path({
            'drive_id': drive_id,
            'folder_path': folder_path,
            'file_pattern': params.get('file_pattern', ''),
            'modified_after': params.get('modified_after'),
            'top': min(int(params.get('max_files', 50)), 500) * 2,
        })
        if not listing.get('success'):
            return listing

        max_files = min(int(params.get('max_files', 50)), 500)
        files = [
            i for i in listing['data']['items']
            if i.get('type') == 'file'
        ][:max_files]

        if not files:
            return {
                'success': True,
                'data': {
                    'matched': 0,
                    'message': "No files matched the filters; nothing queued.",
                    'folder_path': folder_path,
                    'file_pattern': params.get('file_pattern'),
                    'modified_after': listing['data'].get('modified_after'),
                },
                'error': None,
                'raw_response': json.dumps({'matched': 0})[:5000],
            }

        import copy
        import threading
        import uuid as _uuid

        job_id = _uuid.uuid4().hex
        context_copy = copy.deepcopy(context or {})
        context_copy['_async_bulk_job_id'] = job_id
        integration_snapshot = copy.deepcopy(self.integration)
        template_ref = self.template

        description_prefix = (params.get('description') or '').strip() or \
            f"Imported from SharePoint folder: {folder_path}"

        file_jobs = [
            {
                'drive_id': drive_id,
                'item_id': f.get('id'),
                'agent_id': int(agent_id),
                'description': description_prefix,
                '_filename': f.get('name'),
            }
            for f in files
        ]

        def worker():
            ok = 0
            fail = 0
            try:
                worker_executor = SharePointExecutor(integration_snapshot, template_ref)
                worker_executor._ensure_token_fresh()
                logger.info(
                    f"Bulk SharePoint import job {job_id} starting "
                    f"({len(file_jobs)} files from {folder_path}, agent={agent_id})"
                )
                for fj in file_jobs:
                    try:
                        result = worker_executor._download_to_knowledge_sync(
                            fj, context_copy
                        )
                        if isinstance(result.get('data'), dict):
                            result['data']['job_id'] = job_id
                            result['data']['_async_completion'] = True
                            result['data']['_bulk'] = True
                            result['data']['source_filename'] = fj.get('_filename')
                        # Log each file's result individually so users see
                        # per-file outcomes in the activity log
                        worker_executor._log_execution(
                            'download_to_knowledge', fj, result, context_copy
                        )
                        if result.get('success'):
                            ok += 1
                        else:
                            fail += 1
                    except Exception as e:
                        fail += 1
                        logger.error(
                            f"Bulk import file '{fj.get('_filename')}' "
                            f"in job {job_id} crashed: {e}"
                        )
                logger.info(
                    f"Bulk SharePoint import job {job_id} finished: "
                    f"{ok} succeeded, {fail} failed"
                )
                # Final summary log entry
                worker_executor._log_execution(
                    'import_folder_to_knowledge',
                    {'folder_path': folder_path, 'agent_id': agent_id},
                    {
                        'success': fail == 0,
                        'data': {
                            'job_id': job_id,
                            '_async_completion': True,
                            'message': (
                                f"Bulk import finished: {ok}/{len(file_jobs)} succeeded"
                                + (f", {fail} failed" if fail else "")
                            ),
                            'succeeded': ok,
                            'failed': fail,
                            'total': len(file_jobs),
                            'folder_path': folder_path,
                        },
                        'error': None if fail == 0 else f"{fail} file(s) failed",
                        'response_time_ms': 0,
                        'raw_response': '',
                    },
                    context_copy,
                )
            except Exception as e:
                logger.error(f"Bulk SharePoint import job {job_id} crashed: {e}",
                             exc_info=True)

        threading.Thread(
            target=worker, daemon=True, name=f"sp-bulk-import-{job_id[:8]}"
        ).start()

        return {
            'success': True,
            'data': {
                'job_id': job_id,
                'status': 'queued',
                'matched': len(files),
                'folder_path': folder_path,
                'file_pattern': params.get('file_pattern'),
                'modified_after': listing['data'].get('modified_after'),
                'agent_id': int(agent_id),
                'message': (
                    f"Queued {len(files)} file(s) for background import to "
                    f"the knowledge base. Watch the Recent Activity panel "
                    f"to see each file's status as it completes."
                ),
            },
            'error': None,
            'raw_response': json.dumps({
                'job_id': job_id, 'matched': len(files)
            })[:5000],
        }

    def _download_to_knowledge_sync(self, params: Dict, context: Dict) -> Dict:
        """Synchronous import — used by the background worker. Downloads the
        file from SharePoint/OneDrive and feeds it into the document
        processing pipeline (PDF/DOCX extraction + vector indexing)."""
        agent_id = params.get('agent_id')
        if not agent_id:
            return {'success': False, 'error': 'agent_id is required', 'data': None}

        download_result = self._download_file(params)
        if not download_result.get('success'):
            return download_result

        local_path = download_result['data']['local_path']
        filename = download_result['data']['filename']

        ext = os.path.splitext(filename)[1].lstrip('.').lower()
        if ext not in cfg.DOC_ALLOWED_EXTENSIONS:
            try:
                os.remove(local_path)
            except OSError:
                pass
            return {
                'success': False,
                'error': f"Unsupported file type '.{ext}'. "
                         f"Supported: {', '.join(sorted(cfg.DOC_ALLOWED_EXTENSIONS))}",
                'data': None,
            }

        description = params.get('description', '') or f"Imported from SharePoint: {filename}"

        try:
            from app import process_document_as_knowledge
            result = process_document_as_knowledge(
                file_path=local_path,
                agent_id=int(agent_id),
                description=description,
                user_id=context.get('user_id'),
            )

            if result and result.get('status') == 'success':
                return {
                    'success': True,
                    'data': {
                        'filename': filename,
                        'document_id': result.get('document_id'),
                        'knowledge_id': result.get('knowledge_id'),
                        'document_type': result.get('document_type'),
                        'page_count': result.get('page_count', 0),
                        'message': result.get('message', 'Document imported to knowledge base'),
                    },
                    'error': None,
                    'raw_response': json.dumps(result)[:5000],
                }
            else:
                return {
                    'success': False,
                    'error': (result or {}).get('message', 'Document processing failed'),
                    'data': None,
                }
        except Exception as e:
            logger.error(f"Error processing SharePoint document as knowledge: {e}")
            return {
                'success': False,
                'error': f"Knowledge pipeline error: {str(e)}",
                'data': None,
            }

    # =========================================================================
    # Helpers
    # =========================================================================

    def _error_from_response(self, resp: requests.Response) -> Dict:
        return self._friendly_error(resp, 'Graph API')

    def _log_execution(
        self,
        operation_key: str,
        parameters: Dict,
        result: Dict,
        context: Dict
    ):
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

            safe_params = dict(parameters)
            for sensitive_key in ('access_token', 'refresh_token', 'client_secret'):
                safe_params.pop(sensitive_key, None)

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
                'GET',
                f"graph-api://sharepoint/{operation_key}",
                '{}',
                json.dumps(safe_params)[:5000],
                200 if result.get('success') else 500,
                '{}',
                result.get('raw_response', '')[:5000],
                result.get('response_time_ms'),
                result.get('success'),
                result.get('error'),
            ))

            # Usage stats — non-fatal if the SP isn't deployed
            try:
                cursor.execute("EXEC sp_UpdateIntegrationUsage ?, ?", (
                    self.integration.get('integration_id'),
                    1 if result.get('success') else 0,
                ))
            except Exception as stats_err:
                logger.debug(
                    f"sp_UpdateIntegrationUsage not available, skipping stats: {stats_err}"
                )

            conn.commit()
            cursor.close()
            conn.close()

        except Exception as e:
            logger.error(f"Error logging SharePoint execution: {e}")
