"""
Production WSGI server entry point.
This file should be used for production deployments with Waitress.
"""
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, rely on environment variables being set

from waitress import serve
from app_executor_service import app
import logging

# Configure logging for production
def _find_app_root():
    """Resolve the AIHub installation root, where the shared logs/ folder lives.

    Three-step fallback:
      1. APP_ROOT env var if explicitly set.
      2. PyInstaller frozen mode — walk up from sys.executable. Each service exe
         lives at <AIHub>/<service>/<service>.exe, so grandparent = AIHub root.
         This path must work even before .env is loaded (NSSM doesn't inherit
         our .env automatically, and the entry script can't reliably load it
         before this log setup runs).
      3. Dev mode — wsgi files sit at the project root.
    """
    explicit = os.getenv('APP_ROOT')
    if explicit:
        return os.path.abspath(explicit)
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))

_APP_ROOT = _find_app_root()
_default_log = os.path.join(_APP_ROOT, 'logs', 'wsgi_executor_service_log.txt')
os.makedirs(os.path.dirname(_default_log), exist_ok=True)

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] - %(message)s',
    handlers=[
        logging.FileHandler(os.getenv('WSGI_EXECUTOR_SERVICE_LOG', _default_log), encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# Get configuration from environment variables with sensible defaults
# Internal service — bind to loopback by default. Override via INTERNAL_HOST only if
# you actually need this service reachable from another machine. Single-machine
# installs (the standard case) should leave this as 127.0.0.1.
host = os.getenv('INTERNAL_HOST', '127.0.0.1')
port = int(os.getenv('HOST_PORT', 5001)) + 60
threads = int(os.getenv('EXECUTOR_SERVICE_THREADS', 12))
connection_limit = int(os.getenv('SERVER_CONNECTION_LIMIT', 500))

channel_timeout = int(os.getenv('WAITRESS_CHANNEL_TIMEOUT', 3600))

def start_server():
    """Start the production server using Waitress"""
    logging.info(f"Starting production Executor Service on {host}:{port} with {threads} threads (channel_timeout={channel_timeout}s)")
    serve(
        app,
        host=host,
        port=port,
        threads=threads,
        connection_limit=connection_limit,
        channel_timeout=channel_timeout,
        url_scheme='http'
    )

if __name__ == "__main__":
    start_server()