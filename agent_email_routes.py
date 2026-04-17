# agent_email_routes.py
"""
Agent Email Functionality - Complete Routes
Routes for provisioning, configuring, and managing agent email addresses
Includes inbox viewing, configuration UI, and inbound email handling

Cloud API Integration:
- GET  /get_id/<api_key>              - Get numeric TenantId
- GET  /api/email/tenant-id           - Get tenant email info (domain, format)
- GET  /api/email/poll                - Poll for incoming emails
- POST /api/email/acknowledge         - Mark emails as delivered
- GET  /api/email/message/<key>       - Get full message content
- GET  /api/email/attachment?url=     - Get attachment content
- POST /api/notifications/email       - Send email via Cloud API
"""

from flask import Blueprint, request, jsonify, render_template, current_app, g
from flask_login import login_required, current_user
from role_decorators import api_key_or_session_required


def _get_current_user_id():
    """Get current user ID, with fallback for API key auth.
    
    When authenticated via API key (e.g., from the Builder agent),
    current_user is AnonymousUserMixin which has no .id attribute.
    Falls back to admin user (ID 1) for internal API key calls.
    """
    if hasattr(current_user, 'id') and current_user.id is not None:
        return current_user.id
    # API key auth - use admin as the system user
    return 1
import logging
from logging.handlers import WatchedFileHandler
import json
import os
import requests
from CommonUtils import get_db_connection, rotate_logs_on_startup
from config import MAX_ATTACHMENT_CHARS


rotate_logs_on_startup(os.getenv('AGENT_EMAIL_API_LOG', './logs/agent_email_api_log.txt'))

# Configure logging
logger = logging.getLogger("AgentEmailAPI")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=os.getenv('AGENT_EMAIL_API_LOG', './logs/agent_email_api_log.txt'))
handler.setFormatter(formatter)
logger.addHandler(handler)

# Create blueprint
agent_email_bp = Blueprint('agent_email', __name__)

# Cache for tenant ID (avoid repeated API calls)
_tenant_id_cache = {'id': None, 'timestamp': 0}
_TENANT_CACHE_TTL = 300  # 5 minutes

# ============================================================================
# Cloud API Client Functions
# ============================================================================

def get_cloud_api_url():
    """Get the Cloud API base URL."""
    return os.environ.get('AI_HUB_API_URL', '').rstrip('/')


def get_api_key():
    """Get the API/License key."""
    return os.environ.get('API_KEY', '')


def get_cloud_api_timeout():
    """Get timeout for Cloud API requests."""
    try:
        import config as cfg
        return int(getattr(cfg, 'CLOUD_API_REQUESTS_TIMEOUT', 30))
    except:
        return 30


def call_cloud_api(endpoint, method='GET', data=None, params=None):
    """
    Make a request to the Cloud API.
    
    Args:
        endpoint: API endpoint (e.g., '/api/email/poll')
        method: HTTP method
        data: JSON body for POST requests
        params: Query parameters
        
    Returns:
        Response JSON or None on error
    """
    api_url = get_cloud_api_url()
    api_key = get_api_key()
    
    if not api_url:
        logger.error("AI_HUB_API_URL not configured")
        return None
    
    if not api_key:
        logger.error("API_KEY not configured")
        return None
    
    url = f"{api_url}{endpoint}"
    headers = {
        'X-API-Key': api_key,
        'X-License-Key': api_key,  # Some endpoints use this
        'Content-Type': 'application/json'
    }
    
    # Also include api_key as query param for compatibility
    if params is None:
        params = {}
    params['api_key'] = api_key
    
    timeout = get_cloud_api_timeout()
    
    try:
        if method.upper() == 'GET':
            response = requests.get(url, headers=headers, params=params, timeout=timeout)
        elif method.upper() == 'POST':
            response = requests.post(url, headers=headers, params=params, json=data, timeout=timeout)
        else:
            logger.error(f"Unsupported HTTP method: {method}")
            return None
        
        if response.status_code == 200:
            return response.json()
        else:
            logger.warning(f"Cloud API returned {response.status_code}: {response.text[:200]}")
            return {'success': False, 'error': f"HTTP {response.status_code}", 'status_code': response.status_code}
            
    except requests.exceptions.Timeout:
        logger.error(f"Cloud API timeout: {url}")
        return {'success': False, 'error': 'Request timeout'}
    except requests.exceptions.RequestException as e:
        logger.error(f"Cloud API error: {e}")
        return {'success': False, 'error': str(e)}


def get_numeric_tenant_id():
    """
    Get the numeric TenantId from Cloud API.
    Uses caching to avoid repeated API calls.
    """
    import time
    
    # Check cache
    if _tenant_id_cache['id'] and (time.time() - _tenant_id_cache['timestamp']) < _TENANT_CACHE_TTL:
        return _tenant_id_cache['id']
    
    api_url = get_cloud_api_url()
    api_key = get_api_key()
    
    if not api_url or not api_key:
        logger.warning("Cloud API not configured, cannot get TenantId")
        return 0
    
    try:
        # Call /get_id/<api_key> endpoint
        url = f"{api_url}/get_id/{api_key}"
        response = requests.get(url, timeout=get_cloud_api_timeout())
        
        if response.status_code == 200:
            data = response.json()
            tenant_id = data.get('response', 0)
            
            # Update cache
            _tenant_id_cache['id'] = tenant_id
            _tenant_id_cache['timestamp'] = time.time()
            
            logger.info(f"Got TenantId from Cloud API: {tenant_id}")
            return tenant_id
        else:
            logger.warning(f"Failed to get TenantId: HTTP {response.status_code}")
            return 0
            
    except Exception as e:
        logger.error(f"Error getting TenantId from Cloud API: {e}")
        return 0


def get_tenant_email_info():
    """
    Get tenant email configuration from Cloud API.
    Returns tenant_id, domain, and email format.
    """
    # First try the dedicated endpoint
    result = call_cloud_api('/api/email/tenant-id')
    
    if result and result.get('success'):
        return {
            'tenant_id': result.get('tenant_id', 0),
            'domain': result.get('domain', ''),
            'email_suffix': f".{result.get('tenant_id')}@{result.get('domain')}" if result.get('tenant_id') else None,
            'email_format': result.get('email_format', ''),
            'configured': True
        }
    
    # Fallback: get tenant ID directly and use config for domain
    tenant_id = get_numeric_tenant_id()
    
    try:
        import config as cfg
        domain = getattr(cfg, 'MAILGUN_DOMAIN', 'mail.aihub.com')
    except:
        domain = 'mail.aihub.com'
    
    return {
        'tenant_id': tenant_id,
        'domain': domain,
        'email_suffix': f".{tenant_id}@{domain}" if tenant_id else None,
        'email_format': f"{{prefix}}.{tenant_id}@{domain}" if tenant_id else None,
        'configured': tenant_id > 0
    }


# ============================================================================
# Database Helper Functions
# ============================================================================

def get_tenant_context():
    """Get database connection with tenant context set. Returns (conn, cursor)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    api_key = get_api_key()
    cursor.execute("EXEC tenant.sp_setTenantContext ?", (api_key,))
    return conn, cursor


# ============================================================================
# UI Routes - Pages
# ============================================================================

@agent_email_bp.route('/agent-email/config/<int:agent_id>')
@api_key_or_session_required(min_role=2)
def agent_email_config_page(agent_id):
    """Render the email configuration page for an agent."""
    return render_template('agent_email_config.html', agent_id=agent_id)


@agent_email_bp.route('/agent-email/inbox/<int:agent_id>')
@api_key_or_session_required(min_role=2)
def agent_email_inbox_page(agent_id):
    """Render the inbox page for an agent."""
    return render_template('agent_inbox.html', agent_id=agent_id)


# ============================================================================
# API Routes - Configuration
# ============================================================================

@agent_email_bp.route('/api/agent-email/config/<int:agent_id>', methods=['GET'])
@api_key_or_session_required()
def get_agent_email_config(agent_id):
    """Get email configuration for an agent."""
    try:
        conn, cursor = get_tenant_context()
        
        # Get agent name
        cursor.execute("SELECT description AS name FROM Agents WHERE id = ?", (agent_id,))
        agent_row = cursor.fetchone()
        agent_name = agent_row[0] if agent_row else f"Agent {agent_id}"
        
        # Check if table has new columns
        has_new_columns = True
        row = None
        
        try:
            cursor.execute("""
                SELECT 
                    mapping_id, agent_id, email_address, email_prefix, from_name, is_active,
                    inbound_enabled, auto_respond_enabled, auto_respond_instructions,
                    auto_respond_style, require_approval, workflow_trigger_enabled,
                    workflow_id, workflow_filter_rules, inbox_tools_enabled,
                    last_processed_timestamp, max_auto_responses_per_day, cooldown_minutes,
                    auto_responses_today, notify_on_receive, notify_on_auto_reply,
                    notification_email
                FROM AgentEmailAddresses
                WHERE agent_id = ?
            """, (agent_id,))
            row = cursor.fetchone()
        except Exception as col_err:
            logger.warning(f"New columns not available: {col_err}")
            has_new_columns = False
            try:
                cursor.execute("""
                    SELECT mapping_id, agent_id, email_address, from_name, is_active
                    FROM AgentEmailAddresses
                    WHERE agent_id = ?
                """, (agent_id,))
                row = cursor.fetchone()
            except:
                row = None
        
        # Get available workflows
        workflows = []
        try:
            cursor.execute("""
                SELECT id, workflow_name AS name FROM Workflows 
                WHERE is_active = 1 
                ORDER BY workflow_name
            """)
            workflows = [{'id': r[0], 'name': r[1]} for r in cursor.fetchall()]
        except:
            pass
        
        cursor.close()
        conn.close()
        
        # Get tenant email info from Cloud API
        tenant_info = get_tenant_email_info()
        tenant_id = tenant_info.get('tenant_id', 0)
        domain = tenant_info.get('domain', '')
        
        if not row:
            return jsonify({
                'success': True,
                'configured': False,
                'agent_id': agent_id,
                'agent_name': agent_name,
                'tenant_id': tenant_id,
                'domain': domain,
                'email_suffix': tenant_info.get('email_suffix'),
                'cloud_api_configured': tenant_info.get('configured', False),
                'workflows': workflows,
                'config': None
            })
        
        # Build config object
        if has_new_columns:
            config = {
                'mapping_id': row[0],
                'agent_id': row[1],
                'email_address': row[2],
                'email_prefix': row[3] or '',
                'from_name': row[4] or '',
                'is_active': bool(row[5]) if row[5] is not None else True,
                'inbound_enabled': bool(row[6]) if row[6] is not None else False,
                'auto_respond_enabled': bool(row[7]) if row[7] is not None else False,
                'auto_respond_instructions': row[8] or '',
                'auto_respond_style': row[9] or 'professional',
                'require_approval': bool(row[10]) if row[10] is not None else True,
                'workflow_trigger_enabled': bool(row[11]) if row[11] is not None else False,
                'workflow_id': row[12],
                'workflow_filter_rules': json.loads(row[13]) if row[13] else [],
                'inbox_tools_enabled': bool(row[14]) if row[14] is not None else False,
                'last_processed_timestamp': row[15].isoformat() if row[15] else None,
                'max_auto_responses_per_day': row[16] or 50,
                'cooldown_minutes': row[17] or 15,
                'auto_responses_today': row[18] or 0,
                'notify_on_receive': bool(row[19]) if row[19] is not None else True,
                'notify_on_auto_reply': bool(row[20]) if row[20] is not None else True,
                'notification_email': row[21] or '' if len(row) > 21 else ''
            }
        else:
            email_addr = row[2] or ''
            prefix = email_addr.split('.')[0] if '.' in email_addr else ''
            config = {
                'mapping_id': row[0],
                'agent_id': row[1],
                'email_address': email_addr,
                'email_prefix': prefix,
                'from_name': row[3] or '',
                'is_active': bool(row[4]) if row[4] is not None else True,
                'inbound_enabled': False,
                'auto_respond_enabled': False,
                'auto_respond_instructions': '',
                'auto_respond_style': 'professional',
                'require_approval': True,
                'workflow_trigger_enabled': False,
                'workflow_id': None,
                'workflow_filter_rules': [],
                'inbox_tools_enabled': False,
                'last_processed_timestamp': None,
                'max_auto_responses_per_day': 50,
                'cooldown_minutes': 15,
                'auto_responses_today': 0,
                'notify_on_receive': True,
                'notify_on_auto_reply': True,
                'notification_email': ''
            }
        
        return jsonify({
            'success': True,
            'configured': True,
            'agent_id': agent_id,
            'agent_name': agent_name,
            'tenant_id': tenant_id,
            'domain': domain,
            'email_suffix': tenant_info.get('email_suffix'),
            'cloud_api_configured': tenant_info.get('configured', False),
            'workflows': workflows,
            'config': config
        })
        
    except Exception as e:
        logger.error(f"Error getting agent email config: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@agent_email_bp.route('/api/agent-email/config/<int:agent_id>', methods=['POST'])
@api_key_or_session_required()
def save_agent_email_config(agent_id):
    """Save or update email configuration for an agent."""
    try:
        data = request.get_json()
        conn, cursor = get_tenant_context()
        
        # Get tenant info from Cloud API
        tenant_info = get_tenant_email_info()
        tenant_id = tenant_info.get('tenant_id', 0)
        domain = tenant_info.get('domain', '')
        
        # Build email address
        email_prefix = data.get('email_prefix', '').lower().strip()
        email_prefix = ''.join(c for c in email_prefix if c.isalnum() or c == '-')
        
        email_address = f"{email_prefix}.{tenant_id}@{domain}" if email_prefix and tenant_id else None
        
        # Serialize filter rules
        filter_rules = data.get('workflow_filter_rules')
        filter_rules_json = json.dumps(filter_rules) if filter_rules else None
        
        # Check if config exists for this agent
        cursor.execute("SELECT mapping_id, email_address, email_prefix, from_name FROM AgentEmailAddresses WHERE agent_id = ?", (agent_id,))
        existing = cursor.fetchone()
        
        # Check if email_prefix is already used by a different agent
        if email_prefix and not existing:
            cursor.execute("SELECT agent_id FROM AgentEmailAddresses WHERE email_prefix = ?", (email_prefix,))
            prefix_conflict = cursor.fetchone()
            if prefix_conflict:
                return jsonify({'status': 'error', 'message': f'Email prefix "{email_prefix}" is already in use by agent #{prefix_conflict[0]}. Please choose a different prefix.'}), 409
        elif email_prefix and existing and existing[2] != email_prefix:
            # Updating to a new prefix — check it's not taken by another agent
            cursor.execute("SELECT agent_id FROM AgentEmailAddresses WHERE email_prefix = ? AND agent_id != ?", (email_prefix, agent_id))
            prefix_conflict = cursor.fetchone()
            if prefix_conflict:
                return jsonify({'status': 'error', 'message': f'Email prefix "{email_prefix}" is already in use by agent #{prefix_conflict[0]}. Please choose a different prefix.'}), 409
        
        # Preserve existing email_address/prefix/from_name when not provided in update
        if existing and not email_address:
            email_address = existing[1]
            email_prefix = existing[2] or email_prefix
        
        # Preserve existing from_name if not explicitly provided or sent as empty
        from_name = data.get('from_name')
        if not from_name:  # None, empty string, or missing
            from_name = existing[3] if existing else ''
        
        try:
            if existing:
                cursor.execute("""
                    UPDATE AgentEmailAddresses SET
                        email_address = ?,
                        email_prefix = ?,
                        from_name = ?,
                        is_active = ?,
                        inbound_enabled = ?,
                        auto_respond_enabled = ?,
                        auto_respond_instructions = ?,
                        auto_respond_style = ?,
                        require_approval = ?,
                        workflow_trigger_enabled = ?,
                        workflow_id = ?,
                        workflow_filter_rules = ?,
                        inbox_tools_enabled = ?,
                        max_auto_responses_per_day = ?,
                        cooldown_minutes = ?,
                        notify_on_receive = ?,
                        notify_on_auto_reply = ?,
                        notification_email = ?
                    WHERE agent_id = ?
                """, (
                    email_address,
                    email_prefix,
                    from_name,
                    data.get('is_active', True),
                    data.get('inbound_enabled', False),
                    data.get('auto_respond_enabled', False),
                    data.get('auto_respond_instructions'),
                    data.get('auto_respond_style', 'professional'),
                    data.get('require_approval', True),
                    data.get('workflow_trigger_enabled', False),
                    data.get('workflow_id'),
                    filter_rules_json,
                    data.get('inbox_tools_enabled', False),
                    data.get('max_auto_responses_per_day', 50),
                    data.get('cooldown_minutes', 15),
                    data.get('notify_on_receive', True),
                    data.get('notify_on_auto_reply', True),
                    data.get('notification_email'),
                    agent_id
                ))
                mapping_id = existing[0]
            else:
                cursor.execute("""
                    INSERT INTO AgentEmailAddresses (
                        agent_id, email_address, email_prefix, from_name, is_active,
                        inbound_enabled, auto_respond_enabled, auto_respond_instructions,
                        auto_respond_style, require_approval, workflow_trigger_enabled,
                        workflow_id, workflow_filter_rules, inbox_tools_enabled,
                        max_auto_responses_per_day, cooldown_minutes,
                        notify_on_receive, notify_on_auto_reply, notification_email, created_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    agent_id,
                    email_address,
                    email_prefix,
                    from_name,
                    data.get('is_active', True),
                    data.get('inbound_enabled', False),
                    data.get('auto_respond_enabled', False),
                    data.get('auto_respond_instructions'),
                    data.get('auto_respond_style', 'professional'),
                    data.get('require_approval', True),
                    data.get('workflow_trigger_enabled', False),
                    data.get('workflow_id'),
                    filter_rules_json,
                    data.get('inbox_tools_enabled', False),
                    data.get('max_auto_responses_per_day', 50),
                    data.get('cooldown_minutes', 15),
                    data.get('notify_on_receive', True),
                    data.get('notify_on_auto_reply', True),
                    data.get('notification_email'),
                    _get_current_user_id()
                ))
                cursor.execute("SELECT SCOPE_IDENTITY()")
                row = cursor.fetchone()
                mapping_id = row[0] if row else None
        except Exception as col_err:
            # Fallback to basic columns
            logger.warning(f"Using basic columns: {col_err}")
            if existing:
                cursor.execute("""
                    UPDATE AgentEmailAddresses SET
                        email_address = ?, from_name = ?, is_active = ?
                    WHERE agent_id = ?
                """, (email_address, from_name, data.get('is_active', True), agent_id))
                mapping_id = existing[0]
            else:
                cursor.execute("""
                    INSERT INTO AgentEmailAddresses (agent_id, email_address, from_name, is_active, created_by)
                    VALUES (?, ?, ?, ?, ?)
                """, (agent_id, email_address, from_name, data.get('is_active', True), _get_current_user_id()))
                cursor.execute("SELECT SCOPE_IDENTITY()")
                row = cursor.fetchone()
                mapping_id = row[0] if row else None
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'mapping_id': mapping_id,
            'email_address': email_address
        })
        
    except Exception as e:
        logger.error(f"Error saving agent email config: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@agent_email_bp.route('/api/agent-email/config/<int:agent_id>', methods=['DELETE'])
@api_key_or_session_required(min_role=2)
def delete_agent_email_config(agent_id):
    """Delete email configuration for an agent."""
    try:
        conn, cursor = get_tenant_context()
        cursor.execute("DELETE FROM AgentEmailAddresses WHERE agent_id = ?", (agent_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({'success': True, 'deleted': deleted})
        
    except Exception as e:
        logger.error(f"Error deleting agent email config: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# API Routes - Inbox (via Cloud API)
# ============================================================================

@agent_email_bp.route('/api/agent-email/inbox/<int:agent_id>', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_agent_inbox(agent_id):
    """
    Get inbox messages for an agent.
    Polls Cloud API and filters to this agent's email address.
    """
    try:
        conn, cursor = get_tenant_context()
        
        # Get agent's email config
        try:
            cursor.execute("""
                SELECT email_address, from_name, last_processed_timestamp, inbound_enabled
                FROM AgentEmailAddresses
                WHERE agent_id = ? AND is_active = 1
            """, (agent_id,))
            row = cursor.fetchone()
        except:
            cursor.execute("""
                SELECT email_address, from_name
                FROM AgentEmailAddresses
                WHERE agent_id = ? AND is_active = 1
            """, (agent_id,))
            row = cursor.fetchone()
            if row:
                row = (row[0], row[1], None, True)
        
        cursor.close()
        conn.close()
        
        if not row:
            return jsonify({
                'success': True,
                'email_address': None,
                'emails': [],
                'total_count': 0,
                'new_count': 0,
                'message': 'Agent does not have email configured'
            })
        
        email_address = row[0]
        from_name = row[1]
        last_processed = row[2] if len(row) > 2 else None
        
        # Poll Cloud API for emails
        limit = min(int(request.args.get('limit', 50)), 100)
        
        result = call_cloud_api('/api/email/poll', params={'limit': limit, 'include_counts': 'true'})
        
        if not result or not result.get('success'):
            error_msg = result.get('error', 'Could not reach Cloud API') if result else 'Cloud API not configured'
            return jsonify({
                'success': True,
                'email_address': email_address,
                'from_name': from_name,
                'emails': [],
                'total_count': 0,
                'new_count': 0,
                'message': error_msg,
                'storage_note': 'Emails are retained for 3 days'
            })
        
        all_emails = result.get('emails', [])
        
        # Filter to this agent's emails (by recipient address)
        agent_emails = [
            e for e in all_emails 
            if e.get('recipient_email', '').lower() == email_address.lower()
               or e.get('recipient', '').lower() == email_address.lower()
        ]
        
        # Mark new vs processed based on last_processed_timestamp
        from datetime import datetime
        for email in agent_emails:
            email['is_new'] = True
            if last_processed:
                try:
                    received = email.get('received_at', '') or email.get('timestamp', '')
                    if received:
                        email_time = datetime.fromisoformat(received.replace('Z', '+00:00'))
                        if hasattr(last_processed, 'replace'):
                            email['is_new'] = email_time > last_processed
                except:
                    pass
        
        new_count = sum(1 for e in agent_emails if e.get('is_new'))
        
        return jsonify({
            'success': True,
            'email_address': email_address,
            'from_name': from_name,
            'emails': agent_emails,
            'total_count': len(agent_emails),
            'new_count': new_count,
            'storage_note': 'Emails are retained for 3 days'
        })
        
    except Exception as e:
        logger.error(f"Error getting agent inbox: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@agent_email_bp.route('/api/agent-email/message/<int:agent_id>/<message_key>', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_agent_email_message(agent_id, message_key):
    """Get full content of a specific email via Cloud API."""
    try:
        storage_url = request.args.get('storage_url')
        event_id = request.args.get('event_id')
        
        params = {}
        if storage_url:
            params['storage_url'] = storage_url
        if event_id:
            params['event_id'] = event_id
        
        result = call_cloud_api(f'/api/email/message/{message_key}', params=params)
        
        if not result:
            return jsonify({
                'success': False,
                'error': 'Cloud API not configured'
            }), 400
        
        if not result.get('success'):
            return jsonify({
                'success': False,
                'error': result.get('error', 'Failed to fetch message')
            }), result.get('status_code', 500)
        
        return jsonify({
            'success': True,
            'message': result.get('message')
        })
        
    except Exception as e:
        logger.error(f"Error getting email message: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@agent_email_bp.route('/api/agent-email/mark-read/<int:agent_id>', methods=['POST'])
@api_key_or_session_required(min_role=2)
def mark_agent_emails_read(agent_id):
    """Mark emails as read and acknowledge them in Cloud API."""
    try:
        data = request.get_json() or {}
        event_ids = data.get('event_ids', [])
        
        # Update last_processed_timestamp in local DB
        conn, cursor = get_tenant_context()
        try:
            cursor.execute("""
                UPDATE AgentEmailAddresses
                SET last_processed_timestamp = GETDATE()
                WHERE agent_id = ?
            """, (agent_id,))
            conn.commit()
        except:
            pass
        cursor.close()
        conn.close()
        
        # Acknowledge in Cloud API
        acknowledged = 0
        if event_ids:
            result = call_cloud_api('/api/email/acknowledge', method='POST', data={'event_ids': event_ids})
            if result and result.get('success'):
                acknowledged = result.get('acknowledged', 0)
        
        return jsonify({
            'success': True,
            'acknowledged': acknowledged
        })
        
    except Exception as e:
        logger.error(f"Error marking emails read: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@agent_email_bp.route('/api/agent-email/reply/<int:agent_id>', methods=['POST'])
@api_key_or_session_required(min_role=2)
def send_reply(agent_id):
    """Send a reply email via Cloud API."""
    try:
        data = request.get_json()
        
        conn, cursor = get_tenant_context()
        cursor.execute("""
            SELECT email_address, from_name FROM AgentEmailAddresses
            WHERE agent_id = ? AND is_active = 1
        """, (agent_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not row:
            return jsonify({'success': False, 'error': 'Agent email not configured'}), 400
        
        from_address = row[0]
        from_name = row[1] or 'AI Hub Agent'
        
        # Send via Cloud API notifications endpoint
        email_data = {
            'to': [data.get('to')],
            'subject': data.get('subject', ''),
            'body': data.get('body', ''),
            'from_address': from_address,
            'from_name': from_name,
            'agent_id': agent_id
        }
        
        result = call_cloud_api('/api/notifications/email', method='POST', data=email_data)
        
        if not result:
            return jsonify({'success': False, 'error': 'Cloud API not configured'}), 500
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error sending reply: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@agent_email_bp.route('/api/agent-email/send-test/<int:agent_id>', methods=['POST'])
@api_key_or_session_required(min_role=2)
def send_test_email(agent_id):
    """Send a test email via Cloud API to verify configuration."""
    try:
        data = request.get_json() or {}
        test_recipient = data.get('to')
        
        if not test_recipient:
            return jsonify({'success': False, 'error': 'Recipient email required'}), 400
        
        conn, cursor = get_tenant_context()
        cursor.execute("""
            SELECT email_address, from_name, email_prefix FROM AgentEmailAddresses
            WHERE agent_id = ? AND is_active = 1
        """, (agent_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not row:
            return jsonify({'success': False, 'error': 'Agent email not configured'}), 400
        
        from_address = row[0]
        from_name = row[1] or 'AI Hub Agent'
        email_prefix = row[2]
        
        # Reconstruct full email address if domain is missing (DB may have partial address)
        if from_address and '@' in from_address and not from_address.split('@')[1]:
            tenant_info = get_tenant_email_info()
            domain = tenant_info.get('domain', '')
            tenant_id = tenant_info.get('tenant_id', 0)
            if email_prefix and tenant_id and domain:
                from_address = f"{email_prefix}.{tenant_id}@{domain}"
                logger.info(f"Reconstructed email address: {from_address}")
        
        from datetime import datetime
        
        # Send via Cloud API
        email_data = {
            'to': [test_recipient],
            'subject': 'Test Email from AI Hub Agent',
            'body': f"""This is a test email from your AI Hub agent.

Agent Email: {from_address}
Display Name: {from_name}
Sent: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

If you received this email, your agent email configuration is working correctly!

---
AI Hub""",
            'from_address': from_address,
            'from_name': from_name,
            'agent_id': agent_id
        }
        
        result = call_cloud_api('/api/notifications/email', method='POST', data=email_data)
        
        if not result:
            return jsonify({'success': False, 'error': 'Cloud API not configured'}), 500
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error sending test email: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# Original Routes (backward compatibility)
# ============================================================================

@agent_email_bp.route('/api/agents/<int:agent_id>/email/provision', methods=['POST'])
@api_key_or_session_required()
def provision_agent_email(agent_id):
    """Provision an email address for an agent.
    
    Also accepts optional configuration flags (inbound_enabled, auto_respond_enabled,
    auto_respond_style, inbox_tools_enabled) so callers can provision + configure
    in a single call.
    """
    try:
        data = request.json or {}
        from_name = data.get('from_name', 'AI Agent')
        
        # Get tenant info from Cloud API
        tenant_info = get_tenant_email_info()
        tenant_id = tenant_info.get('tenant_id', 0)
        domain = tenant_info.get('domain', 'mail.aihub.com')
        
        email_prefix = f"agent{agent_id}"
        email_address = f"{email_prefix}.{tenant_id}@{domain}"
        
        # Extract optional configuration flags (defaults match DB defaults)
        inbound_enabled = data.get('inbound_enabled', False)
        auto_respond_enabled = data.get('auto_respond_enabled', False)
        auto_respond_style = data.get('auto_respond_style', 'professional')
        inbox_tools_enabled = data.get('inbox_tools_enabled', False)
        auto_respond_instructions = data.get('auto_respond_instructions')
        
        conn, cursor = get_tenant_context()
        
        cursor.execute("""
            SELECT mapping_id, email_address FROM AgentEmailAddresses
            WHERE agent_id = ? AND is_active = 1
        """, (agent_id,))
        existing = cursor.fetchone()
        
        if existing:
            cursor.close()
            conn.close()
            return jsonify({
                'status': 'error',
                'message': 'Agent already has an email address',
                'email_address': existing[1]
            }), 400
        
        try:
            cursor.execute("""
                INSERT INTO AgentEmailAddresses (
                    agent_id, email_address, email_prefix, from_name,
                    inbound_enabled, auto_respond_enabled, auto_respond_style,
                    auto_respond_instructions, inbox_tools_enabled, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                agent_id, email_address, email_prefix, from_name,
                inbound_enabled, auto_respond_enabled, auto_respond_style,
                auto_respond_instructions, inbox_tools_enabled,
                _get_current_user_id()
            ))
        except Exception as col_err:
            # Fallback if new columns don't exist yet
            logger.warning(f"Extended provision columns not available, using basic insert: {col_err}")
            cursor.execute("""
                INSERT INTO AgentEmailAddresses (agent_id, email_address, email_prefix, from_name, created_by)
                VALUES (?, ?, ?, ?, ?)
            """, (agent_id, email_address, email_prefix, from_name, _get_current_user_id()))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'email_address': email_address,
            'from_name': from_name,
            'inbound_enabled': inbound_enabled,
            'auto_respond_enabled': auto_respond_enabled,
            'auto_respond_style': auto_respond_style,
            'inbox_tools_enabled': inbox_tools_enabled
        })
    
    except Exception as e:
        logger.error(f"Error provisioning email: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@agent_email_bp.route('/api/agents/<int:agent_id>/email', methods=['GET'])
@api_key_or_session_required()
def get_agent_email(agent_id):
    """Get agent's email address."""
    try:
        conn, cursor = get_tenant_context()
        cursor.execute("""
            SELECT mapping_id, email_address, from_name, is_active, created_at
            FROM AgentEmailAddresses
            WHERE agent_id = ? AND is_active = 1
        """, (agent_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not row:
            return jsonify({'status': 'success', 'has_email': False})
        
        return jsonify({
            'status': 'success',
            'has_email': True,
            'email': {
                'mapping_id': row[0],
                'email_address': row[1],
                'from_name': row[2],
                'is_active': bool(row[3]),
                'created_at': row[4].isoformat() if row[4] else None
            }
        })
    
    except Exception as e:
        logger.error(f"Error getting email: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@agent_email_bp.route('/api/agents/email/list', methods=['GET'])
@api_key_or_session_required()
def list_all_agent_emails():
    """List all agent email addresses."""
    try:
        conn, cursor = get_tenant_context()
        cursor.execute("""
            SELECT agent_id, email_address, from_name, is_active, created_at
            FROM AgentEmailAddresses
            WHERE is_active = 1
            ORDER BY agent_id
        """)
        
        emails = []
        for row in cursor.fetchall():
            emails.append({
                'agent_id': row[0],
                'email_address': row[1],
                'from_name': row[2],
                'is_active': bool(row[3]),
                'created_at': row[4].isoformat() if row[4] else None
            })
        
        cursor.close()
        conn.close()
        
        return jsonify({'status': 'success', 'emails': emails, 'count': len(emails)})
    
    except Exception as e:
        logger.error(f"Error listing emails: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@agent_email_bp.route('/api/agent-email/attachment/<int:attachment_id>', methods=['GET'])
@api_key_or_session_required(min_role=2)
def download_agent_email_attachment(attachment_id):
    """Download an attachment via Cloud API."""
    try:
        # Call Cloud API to get attachment
        api_url = get_cloud_api_url()
        api_key = get_api_key()
        
        if not api_url or not api_key:
            return jsonify({'success': False, 'error': 'Cloud API not configured'}), 400
        
        url = f"{api_url}/api/email/attachment/{attachment_id}"
        headers = {
            'X-API-Key': api_key
        }

        logger.info(f"Fetching attachment from: {url}")
        
        response = requests.get(url, headers=headers, params={'api_key': api_key}, timeout=60)

        logger.info(f"Cloud API response: {response.status_code} - {response.text[:200]}")
        
        if response.status_code != 200:
            return jsonify({'success': False, 'error': 'Attachment not found'}), 404
        
        # Forward the response with same headers
        from flask import Response
        return Response(
            response.content,
            mimetype=response.headers.get('Content-Type', 'application/octet-stream'),
            headers={
                'Content-Disposition': response.headers.get('Content-Disposition', 'attachment')
            }
        )
        
    except Exception as e:
        logger.error(f"Error downloading attachment: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# Attachment Routes
# ============================================================================

@agent_email_bp.route('/api/agent-email/attachment/<int:attachment_id>', methods=['GET'])
@api_key_or_session_required(min_role=2)
def download_attachment(attachment_id):
    """
    Download an email attachment by ID.
    
    Returns the raw file bytes with appropriate content headers.
    Used by both the UI for downloads and AI tools for text extraction.
    """
    from flask import Response
    
    try:
        conn, cursor = get_tenant_context()
        
        # Get attachment info from database
        cursor.execute("""
            SELECT 
                filename,
                content_type,
                size,
                content
            FROM InboundEmailAttachments
            WHERE attachment_id = ?
        """, (attachment_id,))
        
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not row:
            return jsonify({'success': False, 'error': 'Attachment not found'}), 404
        
        filename = row[0]
        content_type = row[1] or 'application/octet-stream'
        size = row[2]
        content_bytes = row[3]
        
        # If we have content stored locally, return it
        if content_bytes:
            return Response(
                content_bytes,
                mimetype=content_type,
                headers={
                    'Content-Disposition': f'attachment; filename="{filename}"',
                    'Content-Length': str(len(content_bytes))
                }
            )
        
        return jsonify({'success': False, 'error': 'Attachment content not available'}), 404
        
    except Exception as e:
        logger.error(f"Error downloading attachment: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@agent_email_bp.route('/api/agent-email/attachment/<int:attachment_id>/info', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_attachment_info(attachment_id):
    """Get attachment metadata without downloading the file."""
    try:
        conn, cursor = get_tenant_context()
        
        cursor.execute("""
            SELECT 
                attachment_id,
                event_id,
                filename,
                content_type,
                size,
                content IS NOT NULL as has_content
            FROM InboundEmailAttachments
            WHERE attachment_id = ?
        """, (attachment_id,))
        
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not row:
            return jsonify({'success': False, 'error': 'Attachment not found'}), 404
        
        return jsonify({
            'success': True,
            'attachment': {
                'attachment_id': row[0],
                'event_id': row[1],
                'filename': row[2],
                'content_type': row[3],
                'size': row[4],
                'available': bool(row[5])
            }
        })
        
    except Exception as e:
        logger.error(f"Error getting attachment info: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@agent_email_bp.route('/api/agent-email/attachment/<int:attachment_id>/extract', methods=['GET'])
@api_key_or_session_required(min_role=2)  
def extract_attachment_text(attachment_id):
    """
    Extract text content from an attachment.
    
    Query params:
        max_chars: Maximum characters to return (default: 50000)
        allow_ocr: Whether to use OCR for scanned PDFs (default: true)
    
    Returns JSON with extracted text.
    """
    try:
        max_chars = int(request.args.get('max_chars', MAX_ATTACHMENT_CHARS))
        allow_ocr = request.args.get('allow_ocr', 'true').lower() == 'true'
        
        conn, cursor = get_tenant_context()
        
        # Get attachment info and content
        cursor.execute("""
            SELECT 
                filename,
                content_type,
                size,
                content
            FROM InboundEmailAttachments
            WHERE attachment_id = ?
        """, (attachment_id,))
        
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not row:
            return jsonify({'success': False, 'error': 'Attachment not found'}), 404
        
        filename = row[0]
        content_type = row[1]
        size = row[2]
        file_bytes = row[3]
        
        if not file_bytes:
            return jsonify({
                'success': False, 
                'error': 'Attachment content not available'
            }), 404
        
        # Extract text
        try:
            from attachment_text_extractor import extract_text_from_attachment
            
            result = extract_text_from_attachment(
                file_bytes=file_bytes,
                filename=filename,
                content_type=content_type,
                max_chars=max_chars,
                allow_ocr_fallback=allow_ocr
            )
            
            return jsonify({
                'success': result['success'],
                'filename': filename,
                'content_type': content_type,
                'size': size,
                'text': result.get('text', ''),
                'truncated': result.get('truncated', False),
                'original_length': result.get('original_length', 0),
                'file_type': result.get('file_type'),
                'extraction_method': result.get('extraction_method'),
                'error': result.get('error')
            })
            
        except ImportError as e:
            return jsonify({
                'success': False,
                'error': f'Text extraction module not available: {e}'
            }), 500
        
    except Exception as e:
        logger.error(f"Error extracting attachment text: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500
    
