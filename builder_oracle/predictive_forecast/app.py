"""
PredictiveForecast - Flask Application Factory
Single Page Application served at root, all data via /api/* JSON endpoints.
"""
import os
import sys
import logging
from flask import Flask, send_from_directory

# Ensure the project root is on sys.path for both normal and frozen execution
_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from config.settings import Config


def create_app():
    """Flask application factory."""
    Config.ensure_directories()

    app = Flask(
        __name__,
        template_folder=Config.TEMPLATE_FOLDER,
        static_folder=Config.STATIC_FOLDER,
    )

    app.config['MAX_CONTENT_LENGTH'] = Config.MAX_CONTENT_LENGTH
    app.config['UPLOAD_FOLDER'] = Config.UPLOAD_FOLDER
    app.config['MODEL_FOLDER'] = Config.MODEL_FOLDER
    app.secret_key = os.getenv('FC_SECRET_KEY', 'predictive-forecast-dev-key')

    # Configure logging
    log_level = logging.DEBUG if Config.DEBUG else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
        handlers=[
            logging.FileHandler(os.path.join(Config.LOG_DIR, 'forecast.log'), encoding='utf-8'),
            logging.StreamHandler(),
        ]
    )

    # ── Register the unified API blueprint ──────────────────────────────────
    from routes.api import api_bp
    app.register_blueprint(api_bp)

    # ── SPA catch-all: serve index.html for the root and any non-API route ──
    @app.route('/')
    def index():
        return send_from_directory(Config.TEMPLATE_FOLDER, 'index.html')

    @app.errorhandler(404)
    def not_found(e):
        """For any non-API 404, return the SPA shell so client-side routing works."""
        return send_from_directory(Config.TEMPLATE_FOLDER, 'index.html')

    logging.getLogger(__name__).info(
        f'PredictiveForecast started | Models: {Config.MODEL_FOLDER} | Uploads: {Config.UPLOAD_FOLDER}'
    )

    return app


# For development: python app.py
if __name__ == '__main__':
    app = create_app()
    app.run(host=Config.HOST, port=Config.PORT, debug=True)
