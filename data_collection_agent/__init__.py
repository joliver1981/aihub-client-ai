"""
Data Collection Agent — reusable platform component for AI-guided data collection.

Solution authors define a JSON schema describing what data to collect, how the AI should
behave during collection, what reference/lookup data is available, and what completion
actions to fire on submit. End users interact with a conversational chat experience that
includes a progress panel, the ability to edit any section, validation, recap, and
configurable submission actions.

The agent lives as a standalone Flask Blueprint with its own page, agent class, and
state management using JSON files (no SQL Server changes).
"""

from flask import Blueprint


def create_dca_blueprint():
    """
    Blueprint factory for the data collection agent.

    Returns a single Flask Blueprint that exposes:
      - The runtime page and its API routes (data_collection_agent/routes.py)
      - The schema builder wizard page and its API routes (data_collection_agent/builder/builder_routes.py)

    Register in app.py with:
        from data_collection_agent import create_dca_blueprint
        app.register_blueprint(create_dca_blueprint())
    """
    dca_bp = Blueprint(
        'data_collection_agent',
        __name__,
        template_folder='templates',
        static_folder='static',
        static_url_path='/data-collection/static',
    )

    # Register runtime routes (collection page + APIs)
    from .routes import register_routes
    register_routes(dca_bp)

    # Register builder wizard routes (schema authoring + APIs)
    from .builder.builder_routes import register_builder_routes
    register_builder_routes(dca_bp)

    # Register built-in completion action handlers with the ActionRegistry
    from .actions import register_builtin_actions
    register_builtin_actions()

    return dca_bp


__all__ = ['create_dca_blueprint']
