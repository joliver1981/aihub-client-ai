# agent_email_tools.py
"""
Agent Email Inbox Tools
=======================
Tools that allow AI agents to interact with their email inbox when
the 'Inbox Tools' flag is enabled in the agent's email configuration.

These tools are conditionally loaded into an agent's toolset based on:
1. The agent having an email address configured
2. The 'inbox_tools_enabled' flag being True in AgentEmailAddresses

Tools Provided:
- check_my_inbox: View new emails in the agent's inbox
- read_email: Read the full content of a specific email
- reply_to_email: Send a reply to an email
- send_email: Compose and send a new email
- search_inbox: Search emails by sender, subject, or keywords
- get_inbox_summary: Get a quick summary of inbox status

Integration:
    Import in GeneralAgent.py and add to tools list when email is configured.
    See the integration guide in the docstring below.
"""

from langchain_core.tools import tool
from typing import Optional, List, Dict, Any
import logging
from logging.handlers import WatchedFileHandler
import os
import json
import requests
from datetime import datetime, timedelta
from CommonUtils import rotate_logs_on_startup, get_cloud_db_connection as get_db_connection, get_log_path
from config import MAX_ATTACHMENT_CHARS


# Configure logging
def setup_logging():
    """Configure logging for the agent API"""
    logger = logging.getLogger("AgentEmailTools")
    log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
    log_level = getattr(logging, log_level_name, logging.DEBUG)
    logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler = WatchedFileHandler(filename=os.getenv('AGENT_EMAIL_TOOLS_LOG', get_log_path('agent_email_tools_log.txt')), encoding='utf-8')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger

rotate_logs_on_startup(os.getenv('AGENT_EMAIL_TOOLS_LOG', get_log_path('agent_email_tools_log.txt')))

logger = setup_logging()

# ============================================================================
# Cloud API Client (standalone version for tools module)
# ============================================================================

def _get_cloud_api_url():
    """Get the Cloud API base URL."""
    return os.environ.get('AI_HUB_API_URL', '').rstrip('/')


def _get_api_key():
    """Get the API/License key."""
    return os.environ.get('API_KEY', '')


def _call_cloud_api(endpoint, method='GET', data=None, params=None, timeout=30):
    """
    Make a request to the Cloud API.
    
    Args:
        endpoint: API endpoint (e.g., '/api/email/poll')
        method: HTTP method
        data: JSON body for POST requests
        params: Query parameters
        timeout: Request timeout in seconds
        
    Returns:
        Response JSON or None on error
    """
    api_url = _get_cloud_api_url()
    api_key = _get_api_key()
    
    if not api_url or not api_key:
        return {'success': False, 'error': 'Cloud API not configured'}
    
    url = f"{api_url}{endpoint}"
    headers = {
        'X-API-Key': api_key,
        'X-License-Key': api_key,
        'Content-Type': 'application/json'
    }
    
    try:
        if method.upper() == 'GET':
            response = requests.get(url, headers=headers, params=params, timeout=timeout)
        elif method.upper() == 'POST':
            response = requests.post(url, headers=headers, params=params, json=data, timeout=timeout)
        else:
            return {'success': False, 'error': f'Unsupported method: {method}'}
        
        if response.status_code == 200:
            return response.json()
        else:
            return {'success': False, 'error': f'HTTP {response.status_code}', 'status_code': response.status_code}
            
    except requests.exceptions.Timeout:
        return {'success': False, 'error': 'Request timeout'}
    except requests.exceptions.RequestException as e:
        return {'success': False, 'error': str(e)}


# ============================================================================
# Attachment Helper
# ============================================================================

def _prepare_attachment(file_path: str) -> Optional[Dict[str, str]]:
    """
    Read a file from disk and return an attachment dict for the Cloud API.

    Args:
        file_path: Absolute path to the file to attach.

    Returns:
        Dict with 'filename', 'content' (base64), and 'content_type',
        or None if the file cannot be read.
    """
    import base64
    import mimetypes

    if not file_path or not os.path.isfile(file_path):
        return None

    try:
        filename = os.path.basename(file_path)
        content_type, _ = mimetypes.guess_type(file_path)
        if not content_type:
            content_type = 'application/octet-stream'

        with open(file_path, 'rb') as f:
            file_bytes = f.read()

        return {
            'filename': filename,
            'content': base64.b64encode(file_bytes).decode('utf-8'),
            'content_type': content_type,
        }
    except Exception as e:
        logger.error(f"Failed to prepare attachment from {file_path}: {e}")
        return None


# ============================================================================
# Database Helper (for getting agent's email config)
# ============================================================================

def _get_agent_email_config(agent_id: int) -> Optional[Dict]:
    """Get the email configuration for an agent."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", (os.environ.get('API_KEY', ''),))
        
        cursor.execute("""
            SELECT email_address, from_name, inbound_enabled, inbox_tools_enabled
            FROM AgentEmailAddresses
            WHERE agent_id = ? AND is_active = 1
        """, (agent_id,))
        row = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        if row:
            return {
                'email_address': row[0],
                'from_name': row[1],
                'inbound_enabled': bool(row[2]) if row[2] is not None else False,
                'inbox_tools_enabled': bool(row[3]) if row[3] is not None else False
            }
        return None
    except Exception as e:
        logger.error(f"Error getting agent email config: {e}")
        return None


def _check_inbox_tools_enabled(agent_id: int) -> bool:
    """Check if inbox tools are enabled for the agent."""
    config = _get_agent_email_config(agent_id)
    return config.get('inbox_tools_enabled', False) if config else False


# ============================================================================
# Module-level context for current agent
# We use threading.local to store the current agent context
# ============================================================================

import threading
_email_tool_context = threading.local()


def set_email_tool_context(agent_id: int, email_address: str, from_name: str = None):
    """Set the context for email tools (call this before agent execution)."""
    _email_tool_context.agent_id = agent_id
    _email_tool_context.email_address = email_address
    _email_tool_context.from_name = from_name or 'AI Agent'


def get_email_tool_context() -> Dict:
    """Get the current email tool context."""
    return {
        'agent_id': getattr(_email_tool_context, 'agent_id', None),
        'email_address': getattr(_email_tool_context, 'email_address', None),
        'from_name': getattr(_email_tool_context, 'from_name', 'AI Agent')
    }


def clear_email_tool_context():
    """Clear the email tool context (call after agent execution)."""
    if hasattr(_email_tool_context, 'agent_id'):
        delattr(_email_tool_context, 'agent_id')
    if hasattr(_email_tool_context, 'email_address'):
        delattr(_email_tool_context, 'email_address')
    if hasattr(_email_tool_context, 'from_name'):
        delattr(_email_tool_context, 'from_name')


# ============================================================================
# Email Inbox Tools
# ============================================================================

@tool
def check_my_inbox(limit: int = 10, include_read: bool = False) -> str:
    """
    Check your email inbox for new messages.
    
    Use this tool to see what emails have arrived. Returns a summary of each email
    including sender, subject, and when it was received.
    
    ### Parameters:
    - limit: Maximum number of emails to return (default: 10, max: 50)
    - include_read: If True, include previously read emails (default: False, only new emails)
    
    ### Returns:
    A formatted list of emails in your inbox, or a message if no emails are found.
    
    ### Example Usage:
    - "Check my inbox for new emails"
    - "Show me my last 5 emails"
    - "Are there any new messages?"
    """
    try:
        ctx = get_email_tool_context()
        if not ctx.get('email_address'):
            return "Error: Email not configured for this agent. Please configure email first."
        
        agent_email = ctx['email_address']
        limit = min(max(1, limit), 50)  # Clamp between 1 and 50
        
        # Poll Cloud API for emails
        result = _call_cloud_api('/api/email/poll', params={'limit': 100, 'include_counts': 'true'})
        
        if not result or not result.get('success'):
            error_msg = result.get('error', 'Could not reach email service') if result else 'Email service unavailable'
            return f"Unable to check inbox: {error_msg}"
        
        all_emails = result.get('emails', [])
        
        # Filter to this agent's emails
        my_emails = [
            e for e in all_emails 
            if (e.get('recipient_email', '') or e.get('recipient', '')).lower() == agent_email.lower()
        ]
        
        if not my_emails:
            return f"📭 Your inbox is empty. No emails found for {agent_email}."
        
        # Sort by received time (newest first)
        my_emails.sort(key=lambda x: x.get('received_at', x.get('timestamp', '')), reverse=True)
        
        # Apply limit
        my_emails = my_emails[:limit]
        
        # Format output
        lines = []
        lines.append(f"📬 Found {len(my_emails)} email(s) in your inbox ({agent_email}):\n")
        
        for idx, email in enumerate(my_emails, 1):
            # Cloud API returns sender_email and sender_name
            sender_email = email.get('sender_email', '')
            sender_name = email.get('sender_name', '')
            # Format as "Name <email>" or just email if no name
            if sender_name:
                sender = f"{sender_name} <{sender_email}>"
            else:
                sender = sender_email or 'Unknown'
            
            subject = email.get('subject', '(No subject)')
            received = email.get('received_at', email.get('timestamp', 'Unknown'))
            event_id = email.get('event_id', email.get('id', 'N/A'))
            attachment_count = email.get('attachment_count', 0)
            attachments = email.get('attachments', [])
            
            # Format timestamp
            if received and received != 'Unknown':
                try:
                    dt = datetime.fromisoformat(received.replace('Z', '+00:00'))
                    received = dt.strftime('%b %d, %Y %I:%M %p')
                except:
                    pass
            
            lines.append(f"{'─' * 50}")
            lines.append(f"📧 Email ID: {event_id}")
            lines.append(f"   From: {sender}")
            lines.append(f"   Subject: {subject}")
            lines.append(f"   Received: {received}")
            # Show attachment info
            if attachments:
                # If we have actual attachment details, show filenames and IDs
                lines.append(f"   📎 Attachments ({len(attachments)}):")
                for att in attachments:
                    att_id = att.get('attachment_id', '')
                    att_name = att.get('filename', att.get('name', 'Unknown'))
                    if att_id:
                        lines.append(f"      • ID {att_id}: {att_name}")
                    else:
                        lines.append(f"      • {att_name}")
            elif attachment_count > 0:
                # Fallback: just have count, no details
                lines.append(f"   📎 {attachment_count} attachment(s) - use list_email_attachments for details")
        
        lines.append(f"\n{'─' * 50}")
        lines.append("Use read_email with the Email ID (or subject text) to see the full content.")
        lines.append("Use list_email_attachments with the Email ID (or subject text) to see attachments.")
        
        return '\n'.join(lines)
        
    except Exception as e:
        logger.error(f"Error checking inbox: {e}")
        return f"Error checking inbox: {str(e)}"


def _find_email_by_id_or_subject(identifier: str, emails: list) -> dict:
    """
    Find an email by event_id or by subject text match.
    
    Args:
        identifier: Either an event_id (numeric string) or subject text to search for
        emails: List of email dicts from the Cloud API
        
    Returns:
        The matching email dict, or None if not found
    """
    if not identifier or not emails:
        return None
    
    identifier_str = str(identifier).strip()
    identifier_lower = identifier_str.lower()
    
    # First, try exact ID match
    for email in emails:
        event_id = str(email.get('event_id', ''))
        if event_id == identifier_str:
            return email
    
    # Try matching by 'id' field as well
    for email in emails:
        email_id = str(email.get('id', ''))
        if email_id == identifier_str:
            return email
    
    # If not a pure number, try subject matching
    if not identifier_str.isdigit():
        # Try exact subject match first
        for email in emails:
            subject = (email.get('subject', '') or '').lower()
            if subject == identifier_lower:
                return email
        
        # Try partial subject match (identifier contained in subject)
        for email in emails:
            subject = (email.get('subject', '') or '').lower()
            if identifier_lower in subject:
                return email
        
        # Try partial match (subject contained in identifier)
        for email in emails:
            subject = (email.get('subject', '') or '').lower()
            if subject and subject in identifier_lower:
                return email
    
    return None


@tool
def read_email(email_id: str) -> str:
    """
    Read the full content of a specific email.
    
    Use this tool after check_my_inbox to read the complete content of an email.
    You can provide either the Email ID or the email subject.
    
    ### Parameters:
    - email_id: The Email ID (e.g., "12345") OR the email subject text (e.g., "test with scanned pdf")
    
    ### Returns:
    The full email content including sender, subject, body, and attachment information.
    
    ### Example Usage:
    - "Read email 12345"
    - "Show me the email about test with scanned pdf"
    - "Open the email with subject 'Monthly Report'"
    """
    try:
        ctx = get_email_tool_context()
        if not ctx.get('email_address'):
            return "Error: Email not configured for this agent."
        
        if not email_id:
            return "Error: Please provide an email ID or subject. Use check_my_inbox first to see available emails."
        
        agent_email = ctx['email_address']
        
        # Poll to get emails
        result = _call_cloud_api('/api/email/poll', params={'limit': 100})
        
        if not result or not result.get('success'):
            return "Unable to access email service."
        
        # Filter to this agent's emails
        my_emails = [
            e for e in result.get('emails', [])
            if (e.get('recipient_email', '') or e.get('recipient', '')).lower() == agent_email.lower()
        ]
        
        # Find the email by ID or subject
        target_email = _find_email_by_id_or_subject(email_id, my_emails)
        
        if not target_email:
            return f"Email '{email_id}' not found. Use check_my_inbox to see current emails."
        
        # Get the message key and fetch full content
        message_key = target_email.get('message_key', '')
        storage_url = target_email.get('storage_url', '')
        
        if message_key:
            params = {'storage_url': storage_url} if storage_url else {}
            content_result = _call_cloud_api(f'/api/email/message/{message_key}', params=params)
            
            if content_result and content_result.get('success'):
                msg = content_result.get('message', {})
                event_id = target_email.get('event_id', target_email.get('id', 'N/A'))
                
                lines = []
                lines.append("=" * 60)
                lines.append(f"📧 EMAIL CONTENT (ID: {event_id})")
                lines.append("=" * 60)
                lines.append(f"From: {msg.get('from', target_email.get('sender', 'Unknown'))}")
                lines.append(f"To: {msg.get('to', target_email.get('recipient', ''))}")
                lines.append(f"Subject: {msg.get('subject', target_email.get('subject', '(No subject)'))}")
                lines.append(f"Date: {msg.get('date', target_email.get('received_at', ''))}")
                lines.append("-" * 60)
                
                # Body content
                body = msg.get('body-plain', msg.get('body', msg.get('stripped-text', '')))
                if body:
                    lines.append("\n" + body.strip())
                else:
                    html_body = msg.get('body-html', msg.get('html', ''))
                    if html_body:
                        # Strip HTML tags for text display
                        import re
                        text = re.sub(r'<[^>]+>', ' ', html_body)
                        text = re.sub(r'\s+', ' ', text).strip()
                        lines.append(f"\n{text}")
                    else:
                        lines.append("\n(No message body)")
                
                # Attachments
                attachments = msg.get('attachments', [])
                if attachments:
                    lines.append("\n" + "-" * 60)
                    lines.append(f"📎 ATTACHMENTS ({len(attachments)}):")
                    for att in attachments:
                        att_id = att.get('attachment_id', 'N/A')
                        name = att.get('filename', att.get('name', 'Unknown'))
                        size = att.get('size', 0)
                        lines.append(f"   - ID {att_id}: {name} ({size} bytes)")
                    lines.append(f"\nUse list_email_attachments with email ID {event_id} for more details.")
                    lines.append("Use read_attachment with the attachment ID to extract text content.")
                
                lines.append("\n" + "=" * 60)
                lines.append("Use reply_to_email to respond to this message.")
                
                return '\n'.join(lines)
        
        # Fallback: return basic info from the poll response
        sender_email = target_email.get('sender_email', '')
        sender_name = target_email.get('sender_name', '')
        if sender_name:
            sender_display = f"{sender_name} <{sender_email}>"
        else:
            sender_display = sender_email or 'Unknown'
        
        lines = []
        lines.append("=" * 60)
        lines.append(f"📧 EMAIL (Limited Preview)")
        lines.append("=" * 60)
        lines.append(f"From: {sender_display}")
        lines.append(f"Subject: {target_email.get('subject', '(No subject)')}")
        lines.append(f"Received: {target_email.get('received_at', '')}")
        lines.append("-" * 60)
        lines.append("(Full content not available - email may have expired from storage)")
        lines.append("=" * 60)
        
        return '\n'.join(lines)
        
    except Exception as e:
        logger.error(f"Error reading email: {e}")
        return f"Error reading email: {str(e)}"


@tool
def reply_to_email(email_id: str, reply_message: str, include_original: bool = True, attachment_file_path: Optional[str] = None) -> str:
    """
    Reply to an email in your inbox with an optional file attachment.

    Use this tool to respond to an email you've received. The reply will be sent
    from your agent email address. You can optionally attach a file (PDF, Excel,
    CSV, etc.) by providing the full file path.

    ### Parameters:
    - email_id: The ID of the email to reply to (from check_my_inbox)
    - reply_message: Your reply message content
    - include_original: If True, quote the original message (default: True)
    - attachment_file_path: Optional absolute path to a file to attach

    ### Returns:
    Confirmation that the reply was sent, or an error message.

    ### Example Usage:
    - "Reply to email #123 with: Thank you for your inquiry..."
    - "Send a response to the email from john@example.com"
    - "Reply to email #123 with the report and attach the PDF at C:/path/to/report.pdf"
    """
    try:
        ctx = get_email_tool_context()
        if not ctx.get('email_address'):
            return "Error: Email not configured for this agent."

        if not email_id or not reply_message:
            return "Error: Please provide both the email ID and your reply message."

        # Prepare attachment if provided
        attachment_info = None
        if attachment_file_path:
            attachment_info = _prepare_attachment(attachment_file_path)
            if attachment_info is None:
                return f"Error: Could not read attachment file '{attachment_file_path}'. Check that the file exists."

        # Get the original email details
        result = _call_cloud_api('/api/email/poll', params={'limit': 100})

        if not result or not result.get('success'):
            return "Unable to access email service."

        # Find the original email
        original_email = None
        for email in result.get('emails', []):
            if str(email.get('event_id', '')) == str(email_id) or str(email.get('id', '')) == str(email_id):
                original_email = email
                break

        if not original_email:
            return f"Email with ID '{email_id}' not found. Use check_my_inbox to see current emails."

        # Build the reply - use sender_email for the reply-to address
        original_sender = original_email.get('sender_email', '')
        original_sender_name = original_email.get('sender_name', '')
        original_subject = original_email.get('subject', '')

        if not original_sender:
            return "Cannot reply: Original sender email address not found."

        # Format sender display for quoting
        if original_sender_name:
            sender_display = f"{original_sender_name} <{original_sender}>"
        else:
            sender_display = original_sender

        # Ensure subject has "Re:" prefix
        if not original_subject.lower().startswith('re:'):
            reply_subject = f"Re: {original_subject}"
        else:
            reply_subject = original_subject

        # Build reply body
        reply_body = reply_message

        if include_original:
            original_date = original_email.get('received_at', original_email.get('timestamp', ''))
            reply_body += f"\n\n---\nOn {original_date}, {sender_display} wrote:\n"

            # Try to get original body
            message_key = original_email.get('message_key', '')
            if message_key:
                content_result = _call_cloud_api(f'/api/email/message/{message_key}')
                if content_result and content_result.get('success'):
                    msg = content_result.get('message', {})
                    original_body = msg.get('body-plain', msg.get('stripped-text', ''))
                    if original_body:
                        # Indent original message
                        indented = '\n'.join(['> ' + line for line in original_body.split('\n')[:20]])
                        reply_body += indented

        # Send the reply
        email_data = {
            'to': [original_sender],
            'subject': reply_subject,
            'body': reply_body,
            'from_address': ctx['email_address'],
            'from_name': ctx.get('from_name', 'AI Agent'),
            'agent_id': ctx.get('agent_id')
        }

        if attachment_info:
            email_data['attachments'] = [attachment_info]

        send_result = _call_cloud_api('/api/notifications/email', method='POST', data=email_data)

        if send_result and send_result.get('success'):
            attachment_note = f"\nAttachment: {attachment_info['filename']}" if attachment_info else ""
            return f"✅ Reply sent successfully to {original_sender}!\n\nSubject: {reply_subject}{attachment_note}\n\nYour message has been delivered."
        else:
            error = send_result.get('error', 'Unknown error') if send_result else 'Send failed'
            return f"❌ Failed to send reply: {error}"

    except Exception as e:
        logger.error(f"Error sending reply: {e}")
        return f"Error sending reply: {str(e)}"


@tool
def send_email(to_address: str, subject: str, message: str, attachment_file_path: Optional[str] = None) -> str:
    """
    Compose and send a new email with an optional file attachment.

    Use this tool to send a new email to any recipient. The email will be sent
    from your agent email address. You can optionally attach a file (PDF, Excel,
    CSV, etc.) by providing the full file path.

    ### Parameters:
    - to_address: The recipient's email address
    - subject: The email subject line
    - message: The email body content
    - attachment_file_path: Optional absolute path to a file to attach

    ### Returns:
    Confirmation that the email was sent, or an error message.

    ### Example Usage:
    - "Send an email to john@example.com about the project update"
    - "Email the customer at support@company.com with the invoice details"
    - "Send the report to john@example.com and attach the PDF at C:/path/to/report.pdf"
    """
    try:
        ctx = get_email_tool_context()
        if not ctx.get('email_address'):
            return "Error: Email not configured for this agent."

        if not to_address or not subject or not message:
            return "Error: Please provide the recipient address, subject, and message."

        # Validate email format
        import re
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', to_address):
            return f"Error: '{to_address}' is not a valid email address."

        # Prepare attachment if provided
        attachment_info = None
        if attachment_file_path:
            attachment_info = _prepare_attachment(attachment_file_path)
            if attachment_info is None:
                return f"Error: Could not read attachment file '{attachment_file_path}'. Check that the file exists."

        # Send the email
        email_data = {
            'to': [to_address],
            'subject': subject,
            'body': message,
            'from_address': ctx['email_address'],
            'from_name': ctx.get('from_name', 'AI Agent'),
            'agent_id': ctx.get('agent_id')
        }

        if attachment_info:
            email_data['attachments'] = [attachment_info]

        send_result = _call_cloud_api('/api/notifications/email', method='POST', data=email_data)

        if send_result and send_result.get('success'):
            attachment_note = f"\nAttachment: {attachment_info['filename']}" if attachment_info else ""
            return f"✅ Email sent successfully!\n\nTo: {to_address}\nSubject: {subject}{attachment_note}\n\nYour message has been delivered."
        else:
            error = send_result.get('error', 'Unknown error') if send_result else 'Send failed'

            # Check for rate limiting
            if send_result and send_result.get('blocked_by_limit'):
                current = send_result.get('current_usage', 0)
                max_allowed = send_result.get('max_allowed', 0)
                return f"❌ Email not sent: Daily limit reached ({current}/{max_allowed}). Please try again tomorrow."

            return f"❌ Failed to send email: {error}"

    except Exception as e:
        logger.error(f"Error sending email: {e}")
        return f"Error sending email: {str(e)}"


@tool
def search_inbox(search_query: str, search_field: str = "all", limit: int = 10) -> str:
    """
    Search your email inbox for specific messages.
    
    Use this tool to find emails matching specific criteria like sender name,
    subject keywords, or content.
    
    ### Parameters:
    - search_query: The text to search for
    - search_field: Where to search - "sender", "subject", or "all" (default: "all")
    - limit: Maximum results to return (default: 10)
    
    ### Returns:
    A list of matching emails, or a message if none found.
    
    ### Example Usage:
    - "Search my inbox for emails from john@example.com"
    - "Find emails with 'invoice' in the subject"
    - "Look for messages about the project deadline"
    """
    try:
        ctx = get_email_tool_context()
        if not ctx.get('email_address'):
            return "Error: Email not configured for this agent."
        
        if not search_query:
            return "Error: Please provide a search query."
        
        agent_email = ctx['email_address']
        search_lower = search_query.lower()
        search_field = search_field.lower()
        limit = min(max(1, limit), 50)
        
        # Get all emails
        result = _call_cloud_api('/api/email/poll', params={'limit': 100})
        
        if not result or not result.get('success'):
            return "Unable to access email service for search."
        
        # Filter to this agent's emails
        my_emails = [
            e for e in result.get('emails', [])
            if (e.get('recipient_email', '') or e.get('recipient', '')).lower() == agent_email.lower()
        ]
        
        # Search
        matches = []
        for email in my_emails:
            # Cloud API returns sender_email and sender_name
            sender_email_field = (email.get('sender_email', '') or '').lower()
            sender_name_field = (email.get('sender_name', '') or '').lower()
            subject = (email.get('subject', '') or '').lower()
            
            if search_field == 'sender':
                if search_lower in sender_email_field or search_lower in sender_name_field:
                    matches.append(email)
            elif search_field == 'subject':
                if search_lower in subject:
                    matches.append(email)
            else:  # 'all'
                if search_lower in sender_email_field or search_lower in sender_name_field or search_lower in subject:
                    matches.append(email)
        
        if not matches:
            return f"🔍 No emails found matching '{search_query}' in {search_field} fields."
        
        # Limit results
        matches = matches[:limit]
        
        # Format output
        lines = []
        lines.append(f"🔍 Found {len(matches)} email(s) matching '{search_query}':\n")
        
        for idx, email in enumerate(matches, 1):
            # Format sender display
            sender_email_val = email.get('sender_email', '')
            sender_name_val = email.get('sender_name', '')
            if sender_name_val:
                sender = f"{sender_name_val} <{sender_email_val}>"
            else:
                sender = sender_email_val or 'Unknown'
            
            subject = email.get('subject', '(No subject)')
            event_id = email.get('event_id', email.get('id', 'N/A'))
            received = email.get('received_at', email.get('timestamp', 'Unknown'))
            
            lines.append(f"{'─' * 50}")
            lines.append(f"📧 Email ID: {event_id}")
            lines.append(f"   From: {sender}")
            lines.append(f"   Subject: {subject}")
            lines.append(f"   Received: {received}")
        
        lines.append(f"\n{'─' * 50}")
        lines.append("Use read_email with the Email ID (or subject text) to see full content.")
        
        return '\n'.join(lines)
        
    except Exception as e:
        logger.error(f"Error searching inbox: {e}")
        return f"Error searching inbox: {str(e)}"


@tool
def get_inbox_summary() -> str:
    """
    Get a quick summary of your email inbox status.
    
    Use this tool to get a high-level overview of your inbox including
    total messages, unread count, and recent senders.
    
    ### Returns:
    A summary of your inbox status including counts and highlights.
    
    ### Example Usage:
    - "How many emails do I have?"
    - "Give me an inbox summary"
    - "What's the status of my inbox?"
    """
    try:
        ctx = get_email_tool_context()
        if not ctx.get('email_address'):
            return "Error: Email not configured for this agent."
        
        agent_email = ctx['email_address']
        
        # Get emails
        result = _call_cloud_api('/api/email/poll', params={'limit': 100, 'include_counts': 'true'})
        
        if not result or not result.get('success'):
            return "Unable to access email service."
        
        # Filter to this agent's emails
        all_emails = result.get('emails', [])
        my_emails = [
            e for e in all_emails
            if (e.get('recipient_email', '') or e.get('recipient', '')).lower() == agent_email.lower()
        ]
        
        total_count = len(my_emails)
        
        if total_count == 0:
            return f"""📊 INBOX SUMMARY
{'=' * 40}
Email Address: {agent_email}
Status: Empty inbox (0 messages)

You have no pending emails at this time."""
        
        # Analyze the emails
        senders = {}
        subjects_today = []
        now = datetime.now()
        today = now.date()
        
        for email in my_emails:
            # Cloud API returns sender_email and sender_name
            sender_email_val = email.get('sender_email', '')
            sender_name_val = email.get('sender_name', '')
            # Use name if available, otherwise email
            if sender_name_val:
                sender_display = f"{sender_name_val} <{sender_email_val}>"
            else:
                sender_display = sender_email_val or 'Unknown'
            
            senders[sender_display] = senders.get(sender_display, 0) + 1
            
            received = email.get('received_at', email.get('timestamp', ''))
            if received:
                try:
                    dt = datetime.fromisoformat(received.replace('Z', '+00:00'))
                    if dt.date() == today:
                        subjects_today.append(email.get('subject', '(No subject)'))
                except:
                    pass
        
        # Build summary
        lines = []
        lines.append(f"📊 INBOX SUMMARY")
        lines.append("=" * 40)
        lines.append(f"Email Address: {agent_email}")
        lines.append(f"Total Messages: {total_count}")
        lines.append(f"Messages Today: {len(subjects_today)}")
        
        # Top senders
        if senders:
            lines.append(f"\n📤 Top Senders:")
            top_senders = sorted(senders.items(), key=lambda x: x[1], reverse=True)[:5]
            for sender, count in top_senders:
                lines.append(f"   • {sender}: {count} email(s)")
        
        # Recent subjects
        if subjects_today:
            lines.append(f"\n📋 Today's Emails:")
            for subject in subjects_today[:5]:
                lines.append(f"   • {subject[:50]}{'...' if len(subject) > 50 else ''}")
        
        lines.append(f"\n{'=' * 40}")
        lines.append("Use check_my_inbox for the full email list.")
        
        return '\n'.join(lines)
        
    except Exception as e:
        logger.error(f"Error getting inbox summary: {e}")
        return f"Error getting inbox summary: {str(e)}"


# ============================================================================
# Attachment Tools
# ============================================================================

@tool
def list_email_attachments(email_id: str) -> str:
    """
    List all attachments for a specific email.
    
    Use this tool to see what files are attached to an email before reading them.
    Shows filename, file type, size, and whether text can be extracted.
    
    ### Parameters:
    - email_id: The Email ID (e.g., "12345") OR the email subject text (e.g., "test with scanned pdf")
    
    ### Returns:
    A list of attachments with their details, or a message if no attachments.
    
    ### Example Usage:
    - "What attachments are in email 12345?"
    - "List the files attached to the email about test with scanned pdf"
    - "Show me what files were sent with the invoice email"
    """
    try:
        ctx = get_email_tool_context()
        if not ctx.get('email_address'):
            return "Error: Email not configured for this agent."
        
        if not email_id:
            return "Error: Please provide an email ID or subject. Use check_my_inbox first to see available emails."
        
        agent_email = ctx['email_address']
        
        # Poll to get emails
        result = _call_cloud_api('/api/email/poll', params={'limit': 100})
        
        if not result or not result.get('success'):
            return "Unable to access email service."
        
        # Filter to this agent's emails
        my_emails = [
            e for e in result.get('emails', [])
            if (e.get('recipient_email', '') or e.get('recipient', '')).lower() == agent_email.lower()
        ]
        
        # Find the email by ID or subject
        target_email = _find_email_by_id_or_subject(email_id, my_emails)
        
        if not target_email:
            return f"Email '{email_id}' not found. Use check_my_inbox to see current emails."
        
        event_id = target_email.get('event_id')
        subject = target_email.get('subject', '(No subject)')
        
        # Check if email has attachments
        if not target_email.get('has_attachments') and target_email.get('attachment_count', 0) == 0:
            return f"📎 Email '{subject}' (ID: {event_id}) has no attachments."
        
        # Get attachment list from Cloud API
        attachments_result = _call_cloud_api(f'/api/email/attachments/{event_id}')
        
        if not attachments_result or not attachments_result.get('success'):
            # Fallback: try to get from message content
            message_key = target_email.get('message_key', '')
            if message_key:
                content_result = _call_cloud_api(f'/api/email/message/{message_key}', 
                                                  params={'event_id': event_id})
                if content_result and content_result.get('success'):
                    attachments = content_result.get('message', {}).get('attachments', [])
                    if attachments:
                        return _format_attachments_list(event_id, subject, attachments)
            
            return f"📎 Email '{subject}' (ID: {event_id}) has {target_email.get('attachment_count', 0)} attachment(s), but details are not available."
        
        attachments = attachments_result.get('attachments', [])
        
        if not attachments:
            return f"📎 Email '{subject}' (ID: {event_id}) has no attachments."
        
        return _format_attachments_list(event_id, subject, attachments)
        
    except Exception as e:
        logger.error(f"Error listing attachments: {e}")
        return f"Error listing attachments: {str(e)}"


def _format_attachments_list(event_id: str, subject: str, attachments: list) -> str:
    """Format attachments list for display."""
    lines = []
    lines.append(f"📎 ATTACHMENTS for Email: {subject}")
    lines.append(f"   Email ID: {event_id}")
    lines.append("=" * 50)
    lines.append(f"Found {len(attachments)} attachment(s):\n")
    
    for idx, att in enumerate(attachments, 1):
        filename = att.get('filename', 'Unknown')
        content_type = att.get('content_type', 'Unknown')
        size = att.get('size', 0)
        att_id = att.get('attachment_id', 'N/A')
        can_extract = att.get('can_extract_text', _can_extract_locally(filename, content_type))
        
        # Format size
        if size < 1024:
            size_str = f"{size} bytes"
        elif size < 1024 * 1024:
            size_str = f"{size / 1024:.1f} KB"
        else:
            size_str = f"{size / 1024 / 1024:.1f} MB"
        
        # File type icon
        icon = _get_file_icon(content_type, filename)
        
        lines.append(f"{icon} Attachment ID: {att_id}")
        lines.append(f"   Filename: {filename}")
        lines.append(f"   Type: {content_type}")
        lines.append(f"   Size: {size_str}")
        lines.append(f"   Text extractable: {'✅ Yes' if can_extract else '❌ No'}")
        lines.append("")
    
    lines.append("─" * 50)
    lines.append("To read an attachment, use: read_attachment(attachment_id=<ID>)")
    lines.append("Example: read_attachment(attachment_id=45)")
    lines.append("Note: Use the numeric Attachment ID, NOT the filename.")
    
    return '\n'.join(lines)


def _get_file_icon(content_type: str, filename: str) -> str:
    """Get an emoji icon for the file type."""
    ct = (content_type or '').lower()
    fn = (filename or '').lower()
    
    if 'pdf' in ct or fn.endswith('.pdf'):
        return '📄'
    elif 'word' in ct or 'document' in ct or fn.endswith('.docx') or fn.endswith('.doc'):
        return '📝'
    elif 'excel' in ct or 'spreadsheet' in ct or fn.endswith('.xlsx') or fn.endswith('.xls'):
        return '📊'
    elif 'image' in ct or any(fn.endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif']):
        return '🖼️'
    elif 'text' in ct or fn.endswith('.txt') or fn.endswith('.md'):
        return '📃'
    elif 'csv' in ct or fn.endswith('.csv'):
        return '📈'
    elif 'html' in ct or fn.endswith('.html'):
        return '🌐'
    elif 'zip' in ct or 'archive' in ct or fn.endswith('.zip'):
        return '📦'
    else:
        return '📎'


def _can_extract_locally(filename: str, content_type: str) -> bool:
    """Check if we can extract text from this file type."""
    fn = (filename or '').lower()
    ct = (content_type or '').lower()
    
    extractable_extensions = {
        '.pdf', '.docx', '.doc', '.xlsx', '.xls', 
        '.txt', '.md', '.csv', '.tsv', '.html', '.htm',
        '.json', '.xml', '.yaml', '.yml', '.rtf'
    }
    
    for ext in extractable_extensions:
        if fn.endswith(ext):
            return True
    
    extractable_types = ['text/', 'application/pdf', 'application/json', 'application/xml']
    for t in extractable_types:
        if t in ct:
            return True
    
    return False


@tool
def read_attachment(attachment_id: str, max_length: int = int(MAX_ATTACHMENT_CHARS)) -> str:
    """
    Extract and read the text content from an email attachment.
    
    Use this tool after list_email_attachments to read the content of a specific file.
    Supports PDF, Word documents, Excel spreadsheets, text files, CSV, and HTML.
    
    ### Parameters:
    - attachment_id: The attachment ID (from list_email_attachments)
    - max_length: Maximum characters to return (default: 50000)
    
    ### Returns:
    The extracted text content of the attachment, or an error if extraction fails.
    
    ### Supported File Types:
    - PDF (.pdf) - Text extraction from PDF documents
    - Word (.docx, .doc) - Microsoft Word documents
    - Excel (.xlsx, .xls) - Spreadsheets converted to tables
    - Text (.txt, .md, .json, .xml) - Plain text files
    - CSV/TSV (.csv, .tsv) - Data files as tables
    - HTML (.html) - Web pages with tags stripped
    
    ### Example Usage:
    - "Read attachment #5 from the email"
    - "Extract the content from the PDF attachment"
    - "What does the Excel spreadsheet contain?"
    """
    try:
        ctx = get_email_tool_context()
        if not ctx.get('email_address'):
            return "Error: Email not configured for this agent."
        
        if not attachment_id:
            return "Error: Please provide an attachment ID. Use list_email_attachments first."
        
        attachment_id_str = str(attachment_id).strip()
        if not attachment_id_str.isdigit():
            return (f"Error: '{attachment_id}' is not a valid attachment ID. "
                    f"Attachment IDs are numeric (e.g., 45, 123). "
                    f"Use list_email_attachments first to see the attachment IDs for an email.")
        
        attachment_id_int = int(attachment_id_str)
        
        # Validate max_length
        max_length = min(max(1000, max_length), 500000)  # Between 1K and 500K
        
        # Get attachment info and bytes from local database
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", (os.environ.get('API_KEY', ''),))
            
            cursor.execute("""
                SELECT 
                    filename,
                    content_type,
                    size,
                    content
                FROM InboundEmailAttachments
                WHERE attachment_id = ?
            """, (attachment_id_int,))
            
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if not row:
                return f"❌ Attachment #{attachment_id_int} not found. It may have expired (attachments are stored for 3 days)."
            
            filename = row[0]
            content_type = row[1]
            size = row[2]
            content_bytes = row[3]
            
        except Exception as db_err:
            logger.error(f"Database error fetching attachment: {db_err}")
            return f"Error: Could not fetch attachment information: {db_err}"
        
        # Get file bytes - content is stored directly in database
        file_bytes = content_bytes
        
        if not file_bytes:
            return f"❌ Attachment '{filename}' content not available. It may not have been stored."
        
        # Extract text locally
        try:
            from attachment_text_extractor import extract_text_from_attachment
            
            result = extract_text_from_attachment(
                file_bytes=file_bytes,
                filename=filename,
                content_type=content_type,
                max_chars=max_length,
                allow_ocr_fallback=True
            )
            
            if not result['success']:
                error = result.get('error', 'Unknown error')
                if 'not installed' in error.lower() or 'import' in error.lower():
                    return f"❌ Cannot extract text from '{filename}': Required library not installed. {error}"
                elif 'not supported' in error.lower() or 'not fully supported' in error.lower():
                    return f"❌ Cannot extract text from '{filename}': {error}"
                else:
                    return f"❌ Could not extract text from '{filename}': {error}"
            
            text = result.get('text', '')
            
            # Format output
            lines = []
            lines.append("=" * 60)
            lines.append(f"📄 ATTACHMENT CONTENT: {filename}")
            lines.append("=" * 60)
            
            # Show metadata
            file_type = result.get('file_type', 'unknown')
            extraction_method = result.get('extraction_method', 'unknown')
            lines.append(f"File type: {file_type}")
            lines.append(f"Extraction method: {extraction_method}")
            
            original_length = result.get('original_length', len(text))
            lines.append(f"Characters: {original_length:,}")
            
            if result.get('truncated'):
                lines.append(f"⚠️ Content truncated (showing first {max_length:,} of {original_length:,} characters)")
            
            lines.append("-" * 60)
            lines.append("")
            lines.append(text)
            lines.append("")
            lines.append("=" * 60)
            
            return '\n'.join(lines)
            
        except ImportError as e:
            return f"❌ Text extraction module not available: {e}. Please install: pip install PyMuPDF python-docx openpyxl"
        
    except Exception as e:
        logger.error(f"Error reading attachment: {e}", exc_info=True)
        return f"Error reading attachment: {str(e)}"


# ============================================================================
# Tool Factory Function
# ============================================================================

def create_email_inbox_tools(agent_id: int) -> List:
    """
    Create email inbox tools bound to a specific agent.
    
    This function checks if the agent has email configured and inbox tools enabled,
    then returns the appropriate tools.
    
    Args:
        agent_id: The agent ID to create tools for
        
    Returns:
        List of tool functions, or empty list if email not configured/enabled
    """
    try:
        config = _get_agent_email_config(agent_id)
        
        if not config:
            logger.debug(f"Agent {agent_id} has no email configuration")
            return []
        
        if not config.get('inbox_tools_enabled'):
            logger.debug(f"Agent {agent_id} has inbox_tools_enabled=False")
            return []
        
        if not config.get('email_address'):
            logger.debug(f"Agent {agent_id} has no email address")
            return []
        
        # Set the context for this agent
        set_email_tool_context(
            agent_id=agent_id,
            email_address=config['email_address'],
            from_name=config.get('from_name')
        )
        
        logger.info(f"Creating email inbox tools for agent {agent_id} ({config['email_address']})")
        
        return [
            check_my_inbox,
            read_email,
            reply_to_email,
            send_email,
            search_inbox,
            get_inbox_summary,
            list_email_attachments,
            read_attachment
        ]
        
    except Exception as e:
        logger.error(f"Error creating email inbox tools for agent {agent_id}: {e}")
        return []


def get_email_tools_system_prompt_addition(agent_id: int) -> str:
    """
    Get the system prompt addition for email tools.
    
    Returns a string to append to the agent's system prompt describing
    the email capabilities.
    
    Args:
        agent_id: The agent ID
        
    Returns:
        System prompt addition string, or empty string if no email configured
    """
    try:
        config = _get_agent_email_config(agent_id)
        
        if not config or not config.get('inbox_tools_enabled') or not config.get('email_address'):
            return ""
        
        return f"""

## Email Capabilities

You have access to an email inbox at: {config['email_address']}

You can:
- Check for new emails using check_my_inbox
- Read the full content of emails using read_email
- Reply to received emails using reply_to_email
- Send new emails using send_email
- Search for specific emails using search_inbox
- Get an inbox summary using get_inbox_summary
- List attachments on an email using list_email_attachments
- Read/extract text from attachments using read_attachment (supports PDF, Word, Excel, text files, CSV)

When users ask about emails or communication, use these tools to help them manage their correspondence.
When emails have attachments, you can list them and read their contents to answer questions about attached documents.
Display name for outgoing emails: {config.get('from_name', 'AI Agent')}
"""
    except Exception as e:
        logger.error(f"Error getting email system prompt for agent {agent_id}: {e}")
        return ""


# ============================================================================
# Export list for easy importing
# ============================================================================

__all__ = [
    'check_my_inbox',
    'read_email', 
    'reply_to_email',
    'send_email',
    'search_inbox',
    'get_inbox_summary',
    'list_email_attachments',
    'read_attachment',
    'create_email_inbox_tools',
    'set_email_tool_context',
    'get_email_tool_context',
    'clear_email_tool_context',
    'get_email_tools_system_prompt_addition'
]
