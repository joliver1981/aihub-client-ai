"""
Production WSGI server entry point.
This file should be used for production deployments with Waitress.
"""
#import onnx_loader  # This sets up the external ONNX
import os
import sys
# os.environ['CHROMA_TELEMETRY_IMPL'] = 'none'
# os.environ['ANONYMIZED_TELEMETRY'] = 'false'

from waitress import serve
from app_vector_api import app
import logging

# Configure logging for production
_APP_ROOT = os.path.abspath(os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__))))
_default_log = os.path.join(_APP_ROOT, 'logs', 'doc_vector_api_log.txt')
os.makedirs(os.path.dirname(_default_log), exist_ok=True)

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] - %(message)s',
    handlers=[
        logging.FileHandler(os.getenv('DOC_VECTOR_API_LOG', _default_log), encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# Get configuration from environment variables with sensible defaults
host = os.getenv('HOST', '0.0.0.0')
port = int(os.getenv('HOST_PORT', 5001)) + 30
threads = int(os.getenv('VECTOR_SERVER_THREADS', 2))
connection_limit = int(os.getenv('SERVER_CONNECTION_LIMIT', 1000))

channel_timeout = int(os.getenv('WAITRESS_CHANNEL_TIMEOUT', 3600))

def start_server():
    """Start the production server using Waitress"""
    logging.info(f"Starting production Vector API server on {host}:{port} with {threads} threads (channel_timeout={channel_timeout}s)")
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