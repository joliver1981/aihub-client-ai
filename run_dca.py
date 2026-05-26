"""
Standalone runner for the Data Collection Agent.

Spins up a minimal Flask app that registers ONLY the data_collection_agent
blueprint, on its own port, with a stripped-down base.html. No platform nav,
no flask-login, no DB hits unless you actually trigger an action that needs
them (workflow trigger, agent delegation).

Use the dca.bat helper next to this file to start/stop/open the UI.

Environment:
  - DCA_PORT  — port to bind (default 5099)
  - DCA_HOST  — host to bind (default 0.0.0.0). The DCA is a user-facing
                standalone service — unlike the internal CC/Agent/Builder
                services it must be reachable from other hosts. Override
                to 127.0.0.1 only for fully local development.
  - For the LLM to actually answer: OPENAI_API_KEY (or AZURE_OPENAI_*) must
    be set in the environment, or in a .env file next to this script, or in
    the platform's secure_config store.
"""

import logging
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# ---- Best-effort secrets / config bootstrap -----------------------------
# Same precedence the main app uses: secure_config first (registry / encrypted
# secrets store), then a .env file alongside this script.

try:
    from secure_config import load_secure_config
    load_secure_config()
    print("[dca] secure_config loaded")
except Exception as e:
    print(f"[dca] secure_config not loaded ({e}); falling back to .env / environment")

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, '.env'))
except Exception:
    pass

# ---- Bypass platform auth for standalone testing ------------------------
# By default, every route in data_collection_agent uses
# role_decorators.api_key_or_session_required, which 401s without a session.
# Standalone mode is for local testing — we monkey-patch the decorator to a
# no-op BEFORE importing the blueprint, so routes import the no-op version.
# Set DCA_REQUIRE_AUTH=1 to keep the platform auth in place.
if os.environ.get('DCA_REQUIRE_AUTH', '0').lower() not in ('1', 'true', 'yes'):
    try:
        import role_decorators

        def _noop_decorator(*_args, **_kwargs):
            def _wrap(fn):
                return fn
            return _wrap
        role_decorators.api_key_or_session_required = _noop_decorator
        print("[dca] Auth bypassed for standalone mode "
              "(set DCA_REQUIRE_AUTH=1 to require platform auth)")
    except Exception as e:
        print(f"[dca] Could not patch role_decorators: {e}")

# ---- Flask + minimal base.html override ---------------------------------

from flask import Flask, redirect, url_for, jsonify  # noqa: E402
from jinja2 import DictLoader, ChoiceLoader  # noqa: E402

# A stripped-down base.html that satisfies our two templates' `{% extends "base.html" %}`
# without dragging in platform navigation, current_user, url_for routes, etc.
STANDALONE_BASE = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Data Collection Agent</title>
    <link rel="stylesheet"
          href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.2/css/all.min.css">
    <style>
        html, body { margin: 0; padding: 0; height: 100%; }
        body { font-family: 'Outfit', sans-serif; background: #0a0a0c; color: #fafafa; }
    </style>
</head>
<body>
    {% block content %}{% endblock %}
</body>
</html>
"""


def create_app() -> Flask:
    app = Flask(
        __name__,
        # Static folder isn't strictly needed by our templates (we only ever
        # reference URLs under /data-collection/static/* via the blueprint).
        # Point it at the project root so any /static/* references in shared
        # CSS resolve as a fallback.
        static_folder=os.path.join(BASE_DIR, 'static'),
        template_folder=os.path.join(BASE_DIR, 'templates'),
    )

    # Override base.html for THIS app only. The blueprint's own templates
    # (data_collection.html, builder/builder.html) keep extending "base.html"
    # and now resolve to our minimal version.
    app.jinja_loader = ChoiceLoader([
        DictLoader({'base.html': STANDALONE_BASE}),
        app.jinja_loader,
    ])

    # Register the data collection agent blueprint
    from data_collection_agent import create_dca_blueprint
    app.register_blueprint(create_dca_blueprint())

    # Friendly landing page -> end-user gallery (where they pick which agent to chat with)
    @app.route('/')
    def home():
        return redirect('/data-collection/')

    @app.route('/healthz')
    def healthz():
        return jsonify({'status': 'ok', 'app': 'data_collection_agent_standalone'})

    return app


if __name__ == '__main__':
    logging.basicConfig(
        level=os.environ.get('LOG_LEVEL', 'INFO'),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )

    # DCA is user-facing — bind all interfaces by default so remote clients
    # (and the platform host name they were told to use) can actually reach
    # it. Internal-only microservices (CC, Agent, Builder, etc.) still bind
    # 127.0.0.1 — DCA is the documented exception.
    host = os.environ.get('DCA_HOST', '0.0.0.0')
    port = int(os.environ.get('DCA_PORT', '5099'))

    app = create_app()

    # ---- Honor the admin-configured HTTPS posture ----------------------
    # The admin UI at /data-collection/admin/https writes to
    # data/_https_config.json; we read it on startup to decide how to bind.
    try:
        from data_collection_agent.https_config import effective_runtime_settings
        runtime_settings = effective_runtime_settings()
    except Exception as e:
        print(f"[dca] Could not load HTTPS config ({e}); using plain HTTP")
        runtime_settings = {'mode': 'none', 'enable_proxy_fix': False,
                            'ssl_context': None, 'protocol': 'http', 'warnings': []}

    if runtime_settings.get('enable_proxy_fix'):
        # Trust X-Forwarded-Proto / X-Forwarded-Host / X-Forwarded-For from a
        # SINGLE upstream proxy (Caddy / Nginx / IIS).
        try:
            from werkzeug.middleware.proxy_fix import ProxyFix
            app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1, x_for=1, x_prefix=1)
            print("[dca] ProxyFix enabled — trusting X-Forwarded-* from one upstream hop")
        except Exception as e:
            print(f"[dca] WARNING: could not enable ProxyFix: {e}")

    ssl_context = runtime_settings.get('ssl_context')  # tuple (cert_path, key_path) or None
    scheme = 'https' if ssl_context else 'http'

    print()
    print(f"  Data Collection Agent - standalone mode")
    print(f"  ------------------------------------------------------")
    print(f"  HTTPS mode      : {runtime_settings.get('mode', 'none')}")
    if ssl_context:
        print(f"  Cert            : {ssl_context[0]}")
        print(f"  Key             : {ssl_context[1]}")
    print(f"  Listening on    : {scheme}://{host}:{port}")
    print(f"  Agent gallery   : {scheme}://{host}:{port}/data-collection/")
    print(f"  Schema builder  : {scheme}://{host}:{port}/data-collection/builder")
    print(f"  Admin           : {scheme}://{host}:{port}/data-collection/admin")
    print(f"  Health check    : {scheme}://{host}:{port}/healthz")
    for w in runtime_settings.get('warnings') or []:
        print(f"  WARNING         : {w}")
    print()

    # use_reloader=False so the dca.bat stop command can find a single PID
    app.run(
        host=host, port=port, debug=False, use_reloader=False, threaded=True,
        ssl_context=ssl_context,
    )
