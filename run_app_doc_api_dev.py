"""
Development server script.
Use this for local development only, not for production.
"""
import os
from app_doc_api import app

if __name__ == "__main__":
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('HOST_PORT', 5001)) + 10
    debug = True  # Always use debug mode in development
    
    print("=" * 80)
    print(f"DEVELOPMENT SERVER - DO NOT USE IN PRODUCTION")
    print(f"Running on http://{host}:{port} (Press CTRL+C to quit)")
    print("Debug mode: ON")
    print("=" * 80)
    
    app.run(host=host, port=port, debug=debug)
    