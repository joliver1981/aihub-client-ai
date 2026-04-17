from waitress import serve
from app import app  # Import your Flask app instance
import os

host = os.getenv('HOST_IP', '0.0.0.0')
port = int(os.getenv('HOST_PORT', 5000))
debug = os.getenv('HOST_DEBUG', 'false').lower() in ['true', '1', 't', 'y', 'yes']
threads = int(os.getenv('SERVER_THREADS', 8))

serve(app, host=host, port=port, threads=threads)
