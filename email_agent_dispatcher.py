"""
email_agent_dispatcher.py - Email-triggered Agent Processing Service

This module provides a background service that:
1. Polls for incoming emails via Cloud API
2. Routes emails to appropriate agents based on recipient address
3. Triggers auto-responses or workflows based on agent configuration
4. Tracks all processing in AgentProcessedEmails table

Usage:
    # In app_executor_service.py:
    from email_agent_dispatcher import EmailAgentDispatcher
    
    dispatcher = EmailAgentDispatcher(poll_interval=60, flask_app=app)
    dispatcher.start()

Configuration:
    Agents must have email configured in AgentEmailAddresses with:
    - inbound_enabled = 1
    - auto_respond_enabled = 1 (for AI responses)
    - workflow_trigger_enabled = 1 (for workflow triggers)
"""

import os
import logging
import threading
import time
import json
import traceback
from datetime import datetime, date
from typing import Dict, List, Optional, Any
from logging.handlers import WatchedFileHandler

from CommonUtils import get_db_connection, rotate_logs_on_startup, get_agent_api_base_url, get_log_path

# Configure logging
rotate_logs_on_startup(log_file=os.getenv('EMAIL_DISPATCHER_LOG', get_log_path('email_dispatcher_log.txt')))

logger = logging.getLogger("EmailAgentDispatcher")
log_level_name = os.getenv('LOG_LEVEL', 'INFO')
log_level = getattr(logging, log_level_name, logging.INFO)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=os.getenv('EMAIL_DISPATCHER_LOG', get_log_path('email_dispatcher_log.txt')), encoding='utf-8')
handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(handler)


class EmailAgentDispatcher:
    """
    Background service that polls for emails and dispatches to agents.
    
    Features:
    - Polls Cloud API for new emails at configurable interval
    - Routes emails to correct agent based on recipient address
    - Prevents duplicate processing via database tracking
    - Supports auto-response and workflow triggering
    - Tracks all processing for audit/debugging
    """
    
    def __init__(self, poll_interval: int = 60, max_emails_per_poll: int = 50, flask_app=None):
        """
        Initialize the dispatcher.
        
        Args:
            poll_interval: Seconds between poll cycles (default: 60, minimum: 60)
            max_emails_per_poll: Maximum emails to process per cycle (default: 50)
            flask_app: Flask application instance for app context (optional but recommended)
        """
        # Enforce minimum poll interval of 60 seconds to prevent overwhelming Cloud API
        self.poll_interval = max(poll_interval, 60)
        if poll_interval < 60:
            logger.warning(f"Poll interval {poll_interval}s is below minimum. Using 60s.")
        
        self.max_emails_per_poll = max_emails_per_poll
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._flask_app = flask_app
        
        # Cache for enterprise feature check (refresh every 5 minutes)
        self._enterprise_enabled_cache = None
        self._enterprise_cache_time = None
        self._enterprise_cache_ttl = 300  # 5 minutes
        
        # Stats
        self.stats = {
            'started_at': None,
            'last_poll': None,
            'total_polls': 0,
            'total_processed': 0,
            'total_errors': 0
        }
        
        # Import email client lazily to avoid circular imports
        self._email_client = None
    
    def set_flask_app(self, flask_app):
        """Set the Flask app for context management."""
        self._flask_app = flask_app
    
    def _get_db_connection(self):
        """Get database connection with tenant context."""
        conn = get_db_connection()
        cursor = conn.cursor()
        api_key = os.environ.get('API_KEY', '')
        cursor.execute("EXEC tenant.sp_setTenantContext ?", (api_key,))
        return conn, cursor
    
    def _get_email_client(self):
        """Lazy-load the email receive client."""
        if self._email_client is None:
            from email_receive_client import EmailReceiveClient
            self._email_client = EmailReceiveClient()
        return self._email_client
    
    def _is_enterprise_enabled(self) -> bool:
        """
        Check if enterprise features are enabled for this tenant.
        Email processing is an enterprise feature.
        Uses caching to avoid checking on every poll cycle (refreshes every 5 minutes).
        """
        # Check cache first
        if self._enterprise_enabled_cache is not None and self._enterprise_cache_time is not None:
            cache_age = (datetime.now() - self._enterprise_cache_time).total_seconds()
            if cache_age < self._enterprise_cache_ttl:
                return self._enterprise_enabled_cache
        
        try:
            from admin_tier_usage import get_cached_tier_data
            
            # Use Flask app context if available
            if self._flask_app is not None:
                with self._flask_app.app_context():
                    tier_data = get_cached_tier_data()
            else:
                tier_data = get_cached_tier_data()
            
            if not tier_data:
                logger.warning("Unable to verify enterprise feature flag")
                return True  # Fail open - allow processing if can't verify
            
            tier_features = tier_data.get('tier_features', {})
            enabled = tier_features.get('enterprise_features_enabled', False)
            
            # Update cache
            self._enterprise_enabled_cache = enabled
            self._enterprise_cache_time = datetime.now()
            
            return enabled
            
        except ImportError:
            # admin_tier_usage not available, assume enabled
            return True
        except Exception as e:
            logger.error(f"Error checking enterprise feature flag: {e}")
            # Use cached value if available, otherwise fail open
            if self._enterprise_enabled_cache is not None:
                return self._enterprise_enabled_cache
            return True  # Fail open
    
    # =========================================================================
    # Lifecycle Methods
    # =========================================================================
    
    def start(self):
        """Start the background polling thread."""
        with self._lock:
            if self._running:
                logger.warning("Dispatcher already running")
                return
            
            self._running = True
            self.stats['started_at'] = datetime.now().isoformat()
            self._thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._thread.start()
            logger.info(f"EmailAgentDispatcher started (poll_interval={self.poll_interval}s)")
    
    def stop(self):
        """Stop the background polling thread."""
        with self._lock:
            if not self._running:
                return
            
            self._running = False
            logger.info("EmailAgentDispatcher stopping...")
        
        # Wait for thread to finish (with timeout)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        
        logger.info("EmailAgentDispatcher stopped")
    
    def is_running(self) -> bool:
        """Check if dispatcher is running."""
        return self._running
    
    def get_stats(self) -> Dict[str, Any]:
        """Get dispatcher statistics."""
        return {
            **self.stats,
            'running': self._running,
            'poll_interval': self.poll_interval
        }
    
    # =========================================================================
    # Main Poll Loop
    # =========================================================================
    
    def _poll_loop(self):
        """Main polling loop - runs in background thread."""
        logger.info("Poll loop started")
        
        while self._running:
            try:
                self._poll_and_process()
            except Exception as e:
                logger.error(f"Error in poll loop: {e}")
                logger.error(traceback.format_exc())
                self.stats['total_errors'] += 1
            
            # Sleep in small increments so we can stop quickly
            for _ in range(self.poll_interval):
                if not self._running:
                    break
                time.sleep(1)
        
        logger.info("Poll loop ended")
    
    def _poll_and_process(self):
        """Single poll cycle - fetch emails and process them."""
        self.stats['last_poll'] = datetime.now().isoformat()
        self.stats['total_polls'] += 1
        
        # Check if enterprise features are enabled (can be disabled mid-session)
        if not self._is_enterprise_enabled():
            logger.debug("Email inbound processing is disabled - enterprise features not enabled")
            return
        
        # Get active agent email configurations
        agent_configs = self._get_active_agent_configs()
        
        if not agent_configs:
            logger.debug("No agents with inbound email enabled")
            return
        
        # Build lookup by email address
        email_to_config = {
            config['email_address'].lower(): config 
            for config in agent_configs
        }
        
        # Poll for emails from Cloud API
        client = self._get_email_client()
        emails = client.poll_for_emails(limit=self.max_emails_per_poll)
        
        if not emails:
            logger.debug("No pending emails")
            return
        
        logger.info(f"Found {len(emails)} pending email(s)")
        
        # Process each email
        processed_count = 0
        for email in emails:
            try:
                recipient = (email.get('recipient_email') or email.get('recipient', '')).lower()
                
                # Find matching agent config
                config = email_to_config.get(recipient)
                
                if not config:
                    logger.debug(f"No agent configured for {recipient}, skipping")
                    continue
                
                # Process the email
                success = self._process_email(config, email)
                
                if success:
                    processed_count += 1
                    self.stats['total_processed'] += 1
                    
            except Exception as e:
                logger.error(f"Error processing email {email.get('event_id')}: {e}")
                self.stats['total_errors'] += 1
        
        if processed_count > 0:
            logger.info(f"Processed {processed_count} email(s)")
    
    # =========================================================================
    # Email Processing
    # =========================================================================
    
    def _process_email(self, config: Dict, email: Dict) -> bool:
        """
        Process a single email for an agent.
        
        Args:
            config: Agent email configuration from database
            email: Email data from Cloud API
            
        Returns:
            True if processed successfully
        """
        agent_id = config['agent_id']
        event_id = email.get('event_id')
        
        if not event_id:
            logger.warning("Email missing event_id, skipping")
            return False
        
        # Check if already processed
        if self._is_already_processed(agent_id, event_id):
            logger.debug(f"Email {event_id} already processed for agent {agent_id}")
            return True  # Not an error, just skip
        
        # Check filter rules (only for workflow triggers)
        if config.get('workflow_trigger_enabled') and config.get('workflow_filter_rules'):
            if not self._matches_filter_rules(email, config['workflow_filter_rules']):
                # Record as skipped
                self._record_processing(
                    agent_id=agent_id,
                    email=email,
                    processing_type='skipped',
                    status='completed'
                )
                logger.debug(f"Email {event_id} doesn't match filter rules, skipped")
                return True
        
        start_time = time.time()
        
        try:
            # Fetch full content if needed
            content = None
            if config.get('auto_respond_enabled') or config.get('workflow_trigger_enabled'):
                message_key = email.get('message_key', '')
                storage_url = email.get('storage_url')
                if message_key:
                    content = self._get_email_client().get_message_content(message_key, storage_url)
            
            # Process based on configuration - workflow and auto-response are independent
            workflow_result = None
            auto_response_result = None
            
            # Trigger workflow first if enabled (this is typically the primary action)
            if config.get('workflow_trigger_enabled') and config.get('workflow_id'):
                workflow_result = self._trigger_workflow(config, email, content)
                logger.info(f"Workflow trigger for email {event_id}: success={workflow_result.get('success')}, "
                           f"execution_id={workflow_result.get('execution_id')}")
            
            # Then trigger auto-response if enabled
            if config.get('auto_respond_enabled'):
                auto_response_result = self._trigger_auto_response(config, email, content)
                logger.info(f"Auto-response for email {event_id}: success={auto_response_result.get('success')}, "
                           f"pending_approval={auto_response_result.get('pending_approval', False)}")
            
            duration_ms = int((time.time() - start_time) * 1000)
            
            # Determine primary result and processing type for recording
            if workflow_result:
                # Workflow was triggered - use workflow result as primary
                result = workflow_result
                processing_type = 'workflow_trigger'
            elif auto_response_result:
                result = auto_response_result
                # Check if it's pending approval
                if auto_response_result.get('pending_approval'):
                    processing_type = 'pending_approval'
                else:
                    processing_type = 'auto_response'
            else:
                # Inbound enabled but no action configured - just record receipt
                result = {'success': True}
                processing_type = 'received_only'
            
            # Determine status
            if result.get('pending_approval'):
                status = 'pending_approval'
            elif result.get('success'):
                status = 'completed'
            else:
                status = 'failed'
            
            # Record the processing
            self._record_processing(
                agent_id=agent_id,
                email=email,
                processing_type=processing_type,
                status=status,
                response_message_id=result.get('message_id'),
                workflow_execution_id=result.get('execution_id'),
                error_message=result.get('error'),
                duration_ms=duration_ms
            )
            
            # Send notifications if configured
            notification_email = config.get('notification_email')
            if notification_email:
                try:
                    sender_email = email.get('from', email.get('sender', 'unknown'))
                    email_subject = email.get('subject', '(no subject)')
                    agent_name = config.get('agent_name', f"Agent #{agent_id}")
                    
                    # Notify on new email received
                    if config.get('notify_on_receive'):
                        from notification_client import send_email_notification
                        send_email_notification(
                            to=[notification_email],
                            subject=f"[AI Hub] New email received for {agent_name}",
                            body=(
                                f"A new email was received and processed by {agent_name}.\n\n"
                                f"From: {sender_email}\n"
                                f"Subject: {email_subject}\n"
                                f"Processing: {processing_type}\n"
                                f"Status: {status}\n"
                            ),
                            agent_id=agent_id
                        )
                        logger.info(f"Sent 'new email' notification to {notification_email} for agent {agent_id}")
                    
                    # Notify on auto-reply sent
                    if config.get('notify_on_auto_reply') and processing_type in ('auto_response', 'pending_approval'):
                        from notification_client import send_email_notification
                        if processing_type == 'pending_approval':
                            notify_body = (
                                f"An auto-reply was drafted by {agent_name} and is pending approval.\n\n"
                                f"To: {sender_email}\n"
                                f"Original Subject: {email_subject}\n"
                            )
                        else:
                            notify_body = (
                                f"An auto-reply was sent by {agent_name}.\n\n"
                                f"To: {sender_email}\n"
                                f"Original Subject: {email_subject}\n"
                                f"Status: {status}\n"
                            )
                        send_email_notification(
                            to=[notification_email],
                            subject=f"[AI Hub] Auto-reply {'pending approval' if processing_type == 'pending_approval' else 'sent'} by {agent_name}",
                            body=notify_body,
                            agent_id=agent_id
                        )
                        logger.info(f"Sent 'auto-reply' notification to {notification_email} for agent {agent_id}")
                except Exception as notify_err:
                    logger.warning(f"Failed to send email notification: {notify_err}")
            
            return result.get('success', False) or result.get('pending_approval', False)
            
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            
            # Record failed processing
            self._record_processing(
                agent_id=agent_id,
                email=email,
                processing_type='auto_response' if config.get('auto_respond_enabled') else 'workflow_trigger',
                status='failed',
                error_message=error_msg,
                duration_ms=duration_ms
            )
            
            logger.error(f"Error processing email {event_id}: {e}")
            return False
    
    def _trigger_auto_response(self, config: Dict, email: Dict, content: Dict) -> Dict:
        """
        Trigger an AI agent to generate and send an auto-response.
        
        Returns:
            Dict with 'success', optionally 'message_id' or 'error'
        """
        agent_id = config['agent_id']
        
        try:
            # Check rate limits
            if not self._check_rate_limit(agent_id, config):
                return {'success': False, 'error': 'Rate limit exceeded'}
            
            # Check if approval is required
            if config.get('require_approval', True):
                # Queue for approval instead of sending immediately
                return self._queue_for_approval(config, email, content)
            
            # Build the prompt for the agent
            sender_email = email.get('sender_email', 'Unknown')
            sender_name = email.get('sender_name', '')
            subject = email.get('subject', '(No subject)')
            body = content.get('body_text', '') if content else email.get('body_preview', '')
            
            instructions = config.get('auto_respond_instructions', '')
            style = config.get('auto_respond_style', 'professional')
            
            prompt = f"""You received an email that requires a response.

From: {sender_name} <{sender_email}>
Subject: {subject}

Email Body:
{body}

---
Please compose a {style} response to this email.
{f'Additional instructions: {instructions}' if instructions else ''}

Respond with ONLY the email body text, no subject line or headers."""

            # Call the agent API
            from agent_api_client import AgentAPIClient
            
            client = AgentAPIClient(get_agent_api_base_url())
            response = client.chat(
                agent_id=agent_id,
                prompt=prompt,
                use_smart_render=False
            )
            
            if not response.get('response', None):
                return {'success': False, 'error': response.get('error', 'Agent execution failed')}
            
            agent_response = response.get('response', '')
            
            # Send the reply
            from notification_client import send_email_notification
            
            # Case-insensitive check for RE: prefix
            reply_subject = f"RE: {subject}" if not subject.upper().startswith('RE:') else subject
            
            send_result = send_email_notification(
                to=[sender_email],
                subject=reply_subject,
                body=agent_response,
                agent_id=agent_id
            )
            
            if send_result.get('success'):
                # Increment daily counter
                self._increment_daily_counter(agent_id)
                return {
                    'success': True,
                    'message_id': send_result.get('message_id')
                }
            else:
                return {'success': False, 'error': send_result.get('error', 'Failed to send reply')}
                
        except Exception as e:
            logger.error(f"Auto-response error: {e}")
            return {'success': False, 'error': str(e)}
    
    def _trigger_workflow(self, config: Dict, email: Dict, content: Dict) -> Dict:
        """
        Trigger a workflow with the email as input.
        
        Returns:
            Dict with 'success', optionally 'execution_id' or 'error'
        """
        workflow_id = config.get('workflow_id')
        
        if not workflow_id:
            return {'success': False, 'error': 'No workflow configured'}
        
        try:
            import requests
            from CommonUtils import get_executor_api_base_url
            
            # Get email body - prefer content if available, fall back to preview
            email_body = ''
            if content:
                email_body = content.get('body_text', '') or content.get('stripped_text', '') or content.get('body_plain', '')
            if not email_body:
                email_body = email.get('body_preview', '') or email.get('stripped_text', '') or ''
            
            # Prepare email data as workflow variables
            # All values must be strings for workflow variable injection
            variables = {
                'email_event_id': str(email.get('event_id', '')),
                'email_sender': email.get('sender_email', ''),
                'email_sender_name': email.get('sender_name', ''),
                'email_subject': email.get('subject', ''),
                'email_body': email_body,
                'email_received_at': email.get('received_at', ''),
                'email_has_attachments': 'true' if email.get('attachment_count', 0) > 0 or email.get('has_attachments') else 'false',
                'email_attachment_count': str(email.get('attachment_count', 0)),
                'email_recipient': email.get('recipient_email', config.get('email_address', ''))
            }
            
            # Add attachment info if present
            if content and content.get('attachments'):
                variables['email_attachments'] = json.dumps(content['attachments'])
            
            # Call workflow executor API directly (not through main app)
            api_url = f"{get_executor_api_base_url()}/api/workflow/run"
            
            response = requests.post(api_url, json={
                'workflow_id': workflow_id,
                'initiator': 'email_trigger',
                'variables': variables
            }, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                return {
                    'success': True,
                    'execution_id': result.get('execution_id')
                }
            else:
                error_msg = f"Workflow API returned {response.status_code}"
                try:
                    error_data = response.json()
                    error_msg = error_data.get('message', error_msg)
                except:
                    pass
                return {'success': False, 'error': error_msg}
                
        except Exception as e:
            logger.error(f"Workflow trigger error: {e}")
            return {'success': False, 'error': str(e)}
    
    def _queue_for_approval(self, config: Dict, email: Dict, content: Dict) -> Dict:
        """Queue an auto-response for human approval before sending."""
        # For now, just record that approval is needed
        # Full approval workflow would require additional UI
        return {
            'success': True,
            'pending_approval': True
        }
    
    # =========================================================================
    # Database Operations
    # =========================================================================
    
    def _get_active_agent_configs(self) -> List[Dict]:
        """Get all agents with inbound email processing enabled."""
        try:
            conn, cursor = self._get_db_connection()
            
            cursor.execute("""
                SELECT 
                    agent_id, 
                    email_address, 
                    from_name, 
                    inbound_enabled, 
                    auto_respond_enabled, 
                    auto_respond_instructions,
                    auto_respond_style, 
                    require_approval, 
                    workflow_trigger_enabled,
                    workflow_id, 
                    workflow_filter_rules, 
                    max_auto_responses_per_day,
                    cooldown_minutes, 
                    auto_responses_today,
                    auto_responses_reset_date,
                    notify_on_receive,
                    notify_on_auto_reply,
                    notification_email
                FROM AgentEmailAddresses
                WHERE is_active = 1 AND inbound_enabled = 1
            """)
            
            configs = []
            today = date.today()
            
            for row in cursor.fetchall():
                # Parse filter rules JSON
                filter_rules = None
                if row[10]:
                    try:
                        filter_rules = json.loads(row[10])
                    except:
                        pass
                
                # Check if we need to reset daily counter
                reset_date = row[14]  # auto_responses_reset_date
                auto_responses_today = row[13] or 0
                
                # If reset_date is in the past, the counter should be treated as 0
                if reset_date and reset_date < today:
                    auto_responses_today = 0
                
                configs.append({
                    'agent_id': row[0],
                    'email_address': row[1],
                    'from_name': row[2],
                    'inbound_enabled': bool(row[3]),
                    'auto_respond_enabled': bool(row[4]),
                    'auto_respond_instructions': row[5],
                    'auto_respond_style': row[6] or 'professional',
                    'require_approval': bool(row[7]) if row[7] is not None else True,
                    'workflow_trigger_enabled': bool(row[8]),
                    'workflow_id': row[9],
                    'workflow_filter_rules': filter_rules,
                    'max_auto_responses_per_day': row[11] or 50,
                    'cooldown_minutes': row[12] or 15,
                    'auto_responses_today': auto_responses_today,
                    'notify_on_receive': bool(row[15]) if len(row) > 15 and row[15] is not None else True,
                    'notify_on_auto_reply': bool(row[16]) if len(row) > 16 and row[16] is not None else True,
                    'notification_email': row[17] if len(row) > 17 else None
                })
            
            cursor.close()
            conn.close()
            return configs
            
        except Exception as e:
            logger.error(f"Error getting agent configs: {e}")
            return []
    
    def _is_already_processed(self, agent_id: int, event_id: int) -> bool:
        """Check if we've already processed this email for this agent."""
        try:
            conn, cursor = self._get_db_connection()
            
            cursor.execute("""
                SELECT 1 FROM AgentProcessedEmails 
                WHERE agent_id = ? AND event_id = ?
            """, (agent_id, event_id))
            
            result = cursor.fetchone() is not None
            
            cursor.close()
            conn.close()
            return result
            
        except Exception as e:
            logger.error(f"Error checking processed status: {e}")
            return False
    
    def _record_processing(self, agent_id: int, email: Dict, processing_type: str,
                          status: str, response_message_id: str = None,
                          workflow_execution_id: str = None, error_message: str = None,
                          duration_ms: int = None):
        """Record email processing in the AgentProcessedEmails table."""
        try:
            conn, cursor = self._get_db_connection()
            
            # Parse received_at
            received_at = None
            received_str = email.get('received_at') or email.get('timestamp')
            if received_str:
                try:
                    if isinstance(received_str, str):
                        received_at = datetime.fromisoformat(received_str.replace('Z', '+00:00'))
                    else:
                        received_at = received_str
                except:
                    pass
            
            cursor.execute("""
                INSERT INTO AgentProcessedEmails (
                    agent_id, event_id, message_key, recipient_email,
                    sender_email, sender_name, subject, received_at,
                    processing_type, processing_status, response_message_id,
                    workflow_execution_id, error_message, processing_duration_ms,
                    created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                agent_id,
                email.get('event_id'),
                email.get('message_key'),
                email.get('recipient_email') or email.get('recipient'),
                email.get('sender_email'),
                email.get('sender_name'),
                email.get('subject'),
                received_at,
                processing_type,
                status,
                response_message_id,
                workflow_execution_id,
                error_message,
                duration_ms,
                'email_dispatcher'
            ))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.debug(f"Recorded processing: agent={agent_id}, event={email.get('event_id')}, "
                        f"type={processing_type}, status={status}")
            
        except Exception as e:
            logger.error(f"Error recording processing: {e}")
    
    def _check_rate_limit(self, agent_id: int, config: Dict) -> bool:
        """Check if agent is within rate limits for auto-responses."""
        max_per_day = config.get('max_auto_responses_per_day', 50)
        current_count = config.get('auto_responses_today', 0)
        return current_count < max_per_day
    
    def _increment_daily_counter(self, agent_id: int):
        """Increment the daily auto-response counter for an agent."""
        try:
            conn, cursor = self._get_db_connection()
            
            today = date.today()
            
            # Reset counter if date changed, otherwise increment
            cursor.execute("""
                UPDATE AgentEmailAddresses
                SET auto_responses_today = CASE 
                        WHEN auto_responses_reset_date IS NULL OR auto_responses_reset_date < ?
                        THEN 1
                        ELSE ISNULL(auto_responses_today, 0) + 1
                    END,
                    auto_responses_reset_date = ?
                WHERE agent_id = ?
            """, (today, today, agent_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
        except Exception as e:
            logger.error(f"Error incrementing daily counter: {e}")
    
    def _matches_filter_rules(self, email: Dict, rules: List[Dict]) -> bool:
        """
        Check if email matches configured filter rules.
        
        Rules format:
        [
            {"field": "subject", "operator": "contains", "value": "invoice"},
            {"field": "from", "operator": "ends_with", "value": "@example.com"}
        ]
        
        All rules must match (AND logic).
        """
        if not rules:
            return True
        
        for rule in rules:
            field = rule.get('field', 'subject')
            operator = rule.get('operator', 'contains')
            value = rule.get('value', '').lower()
            
            # Get field value from email
            if field == 'subject':
                field_value = (email.get('subject') or '').lower()
            elif field == 'from' or field == 'sender':
                field_value = (email.get('sender_email') or '').lower()
            elif field == 'body':
                # Try multiple possible keys for body content
                field_value = (
                    email.get('body_preview') or 
                    email.get('stripped_text') or 
                    email.get('body_plain') or 
                    ''
                ).lower()
            else:
                continue
            
            # Apply operator
            if operator == 'contains':
                if value not in field_value:
                    return False
            elif operator == 'not_contains':
                if value in field_value:
                    return False
            elif operator == 'equals':
                if field_value != value:
                    return False
            elif operator == 'starts_with':
                if not field_value.startswith(value):
                    return False
            elif operator == 'ends_with':
                if not field_value.endswith(value):
                    return False
        
        return True


# =============================================================================
# Singleton Instance
# =============================================================================

_dispatcher_instance: Optional[EmailAgentDispatcher] = None


def get_dispatcher(flask_app=None) -> EmailAgentDispatcher:
    """Get the singleton dispatcher instance."""
    global _dispatcher_instance
    if _dispatcher_instance is None:
        poll_interval = int(os.environ.get('EMAIL_POLL_INTERVAL', 60))
        _dispatcher_instance = EmailAgentDispatcher(poll_interval=poll_interval, flask_app=flask_app)
    elif flask_app is not None and _dispatcher_instance._flask_app is None:
        # Update flask_app if not already set
        _dispatcher_instance.set_flask_app(flask_app)
    return _dispatcher_instance


def start_dispatcher(flask_app=None):
    """Start the email dispatcher."""
    dispatcher = get_dispatcher(flask_app)
    if not dispatcher.is_running():
        dispatcher.start()
    return dispatcher


def stop_dispatcher():
    """Stop the email dispatcher."""
    global _dispatcher_instance
    if _dispatcher_instance and _dispatcher_instance.is_running():
        _dispatcher_instance.stop()
