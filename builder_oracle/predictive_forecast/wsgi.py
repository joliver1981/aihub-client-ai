"""
Production WSGI entry point for PredictiveForecast.
Uses Waitress as the production server (matches main app pattern).
"""
import os
import sys
import logging

# Ensure project root is on path
_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from waitress import serve
from app import create_app
from config.settings import Config

# Configure production logging — reconfigure stdout for UTF-8 on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(Config.LOG_DIR, 'forecast.log'), encoding='utf-8'),
        logging.StreamHandler(),
    ]
)

app = create_app()


def start_server():
    """Start the production server using Waitress."""
    host = Config.HOST
    port = Config.PORT
    threads = Config.THREADS

    logging.info(f'Starting PredictiveForecast on {host}:{port} with {threads} threads')
    serve(
        app,
        host=host,
        port=port,
        threads=threads,
        url_scheme='http',
    )


if __name__ == '__main__':
    Config.ensure_directories()
    start_server()
