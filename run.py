from waitress import serve
from app import app  # Import your Flask app instance


serve(app, host='10.0.0.49', port=5000, threads=8)
