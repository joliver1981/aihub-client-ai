import os
import logging
from typing import List, Optional, Union
from pathlib import Path
from azure.communication.email import EmailClient
from azure.core.exceptions import AzureError
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.utils import formatdate
import config as cfg

def send_email_azure(
    recipients: Union[str, List[str]],
    subject: str,
    body: str,
    attachment_path: Optional[str] = None,
    html_content: bool = False
) -> bool:
    """
    Send an email using Azure Communication Services with optional attachment.
    
    Args:
        recipients: Single recipient email or list of recipient emails
        subject: Email subject
        body: Email body content
        attachment_path: Optional path to attachment file
        html_content: Boolean indicating if body contains HTML (default False)
    
    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        # Admin Email Settings (UI) wins; .env is the fallback.
        from email_settings import get_email_config
        _conf = get_email_config()
        connection_string = _conf['azure_conn_str'] or cfg.API_AZURE_EMAIL_CONN_STR
        sender = _conf['azure_sender'] or cfg.API_AZURE_EMAIL_SENDER

        # Initialize the email client
        email_client = EmailClient.from_connection_string(connection_string)
        
        # Prepare recipients list
        if isinstance(recipients, str):
            recipients = [recipients]
            
        # Prepare message content
        message = {
            "senderAddress": sender,
            "recipients": {
                "to": [{"address": recipient} for recipient in recipients]
            },
            "content": {
                "subject": subject,
                "plainText" if not html_content else "html": body
            }
        }
        
        # Add attachment if provided
        if attachment_path:
            if not os.path.exists(attachment_path):
                raise FileNotFoundError(f"Attachment not found: {attachment_path}")
                
            with open(attachment_path, 'rb') as file:
                file_content = file.read()
                
            attachment = {
                "name": Path(attachment_path).name,
                "contentType": "application/octet-stream",
                "contentInBase64": file_content
            }
            
            message["attachments"] = [attachment]
        
        # Send the email
        poller = email_client.begin_send(message)
        response = poller.result()
        
        return True
        
    except Exception as e:
        logging.error(f"Error sending Azure email: {str(e)}")
        return False

def send_email_smtp(
    recipients: Union[str, List[str]],
    subject: str,
    body: str,
    attachment_path: Optional[str] = None,
    html_content: bool = False,
    smtp_host: Optional[str] = None,
    smtp_port: Optional[int] = None,
    smtp_user: Optional[str] = None,
    smtp_password: Optional[str] = None,
    smtp_use_tls: Optional[bool] = None,
    smtp_from: Optional[str] = None
) -> bool:
    """
    Send an email using SMTP server with optional attachment.
    
    Args:
        recipients: Single recipient email or list of recipient emails
        subject: Email subject
        body: Email body content
        attachment_path: Optional path to attachment file
        html_content: Boolean indicating if body contains HTML (default False)
        smtp_host: SMTP server hostname
        smtp_port: SMTP server port
        smtp_user: SMTP username
        smtp_password: SMTP password
        smtp_use_tls: Whether to use TLS for SMTP connection
        smtp_from: Sender email address
    
    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        # Resolve config at CALL time (admin Email Settings page wins, .env is
        # the fallback). Explicit keyword args from a caller still override.
        # This replaces the old import-time default-arg binding, which froze
        # .env values until a service restart.
        from email_settings import get_email_config
        _conf = get_email_config()
        smtp_host = _conf['smtp_host'] if smtp_host is None else smtp_host
        smtp_port = _conf['smtp_port'] if smtp_port is None else smtp_port
        smtp_user = _conf['smtp_user'] if smtp_user is None else smtp_user
        smtp_password = _conf['smtp_password'] if smtp_password is None else smtp_password
        smtp_use_tls = _conf['smtp_use_tls'] if smtp_use_tls is None else smtp_use_tls
        smtp_from = _conf['smtp_from'] if smtp_from is None else smtp_from

        # Convert single recipient to list
        if isinstance(recipients, str):
            recipients = [recipients]

        # Create message container
        msg = MIMEMultipart()
        msg['Subject'] = subject
        msg['From'] = smtp_from
        msg['To'] = ', '.join(recipients)
        msg['Date'] = formatdate(localtime=True)
        
        # Add body
        if html_content:
            msg.attach(MIMEText(body, 'html'))
        else:
            msg.attach(MIMEText(body, 'plain'))
            
        # Add attachment if provided
        if attachment_path:
            if not os.path.exists(attachment_path):
                raise FileNotFoundError(f"Attachment not found: {attachment_path}")
                
            with open(attachment_path, 'rb') as file:
                part = MIMEApplication(file.read(), Name=os.path.basename(attachment_path))
                
            # Add header for attachment
            part['Content-Disposition'] = f'attachment; filename="{os.path.basename(attachment_path)}"'
            msg.attach(part)
        
        # Connect to SMTP server
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if smtp_use_tls:
                server.starttls()
            
            # Login if credentials provided
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            
            # Send email
            server.send_message(msg)
            
        logging.info("SMTP email sent successfully")
        return True
        
    except Exception as e:
        logging.error(f"Error sending SMTP email: {str(e)}")
        return False

def send_email(
    recipients: Union[str, List[str]],
    subject: str,
    body: str,
    attachment_path: Optional[str] = None,
    html_content: bool = False
) -> bool:
    """
    Send an email using the configured email provider (Azure or SMTP).
    This is the main entry point for sending emails in the application.
    
    Args:
        recipients: Single recipient email or list of recipient emails
        subject: Email subject
        body: Email body content
        attachment_path: Optional path to attachment file
        html_content: Boolean indicating if body contains HTML (default False)
    
    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        # Provider from the admin Email Settings page when configured there,
        # else the .env EMAIL_PROVIDER — resolved per call, no restart needed.
        from email_settings import get_email_config
        if get_email_config()['provider'] == 'smtp':
            return send_email_smtp(
                recipients=recipients,
                subject=subject,
                body=body,
                attachment_path=attachment_path,
                html_content=html_content
            )
        else:  # Default to Azure
            return send_email_azure(
                recipients=recipients,
                subject=subject,
                body=body,
                attachment_path=attachment_path,
                html_content=html_content
            )
    except Exception as e:
        logging.error(f"Error in send_email: {str(e)}")
        return False 