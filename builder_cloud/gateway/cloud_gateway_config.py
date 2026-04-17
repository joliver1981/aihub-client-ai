"""
Cloud Storage Gateway Configuration
"""
import os

# Service settings
CLOUD_GATEWAY_PORT = int(os.getenv('CLOUD_GATEWAY_PORT', '5081'))
_APP_ROOT = os.path.abspath(os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__))))
CLOUD_GATEWAY_LOG = os.getenv('CLOUD_GATEWAY_LOG', os.path.join(_APP_ROOT, 'logs', 'cloud_gateway_log.txt'))
LOG_LEVEL = os.getenv('LOG_LEVEL', 'DEBUG')

# File transfer limits (in MB)
MAX_UPLOAD_SIZE_MB = int(os.getenv('CLOUD_MAX_UPLOAD_MB', '50'))
MAX_DOWNLOAD_SIZE_MB = int(os.getenv('CLOUD_MAX_DOWNLOAD_MB', '50'))

# SAS / presigned URL defaults
DEFAULT_SAS_URL_EXPIRY_SECONDS = int(os.getenv('CLOUD_DEFAULT_SAS_EXPIRY', '3600'))

# Request timeout (seconds)
DEFAULT_TIMEOUT = int(os.getenv('CLOUD_TIMEOUT', '60'))
