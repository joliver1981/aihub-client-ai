"""
Cloud Storage Gateway Configuration
"""
import os
import sys

# Service settings
CLOUD_GATEWAY_PORT = int(os.getenv('CLOUD_GATEWAY_PORT', '5081'))


def _find_app_root():
    """Resolve the AIHub installation root, where the shared logs/ folder lives.

    Three-step fallback:
      1. APP_ROOT env var if explicitly set.
      2. PyInstaller frozen mode — walk up from sys.executable. Each service exe
         lives at <AIHub>/<service>/<service>.exe, so grandparent = AIHub root.
         This must work even before .env is loaded.
      3. Dev mode — fall back to this file's directory.
    """
    explicit = os.getenv('APP_ROOT')
    if explicit:
        return os.path.abspath(explicit)
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


_APP_ROOT = _find_app_root()
CLOUD_GATEWAY_LOG = os.getenv('CLOUD_GATEWAY_LOG', os.path.join(_APP_ROOT, 'logs', 'cloud_gateway_log.txt'))
LOG_LEVEL = os.getenv('LOG_LEVEL', 'DEBUG')

# File transfer limits (in MB)
MAX_UPLOAD_SIZE_MB = int(os.getenv('CLOUD_MAX_UPLOAD_MB', '50'))
MAX_DOWNLOAD_SIZE_MB = int(os.getenv('CLOUD_MAX_DOWNLOAD_MB', '50'))

# SAS / presigned URL defaults
DEFAULT_SAS_URL_EXPIRY_SECONDS = int(os.getenv('CLOUD_DEFAULT_SAS_EXPIRY', '3600'))

# Request timeout (seconds)
DEFAULT_TIMEOUT = int(os.getenv('CLOUD_TIMEOUT', '60'))
