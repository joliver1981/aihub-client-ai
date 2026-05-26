"""
Builder wizard routes.

Page route + APIs for creating and editing data collection schemas.
Builder sessions are kept in-process (mirroring `workflow_builder_routes.py`).
The schema persists to disk only on Save.
"""

import logging
import os
import uuid
from logging.handlers import WatchedFileHandler
from typing import Dict

from flask import Blueprint, jsonify, render_template, request

from CommonUtils import rotate_logs_on_startup, get_log_path

from ..branding import resolve_branding, branding_to_style_block, safe_url
from ..identity import require_admin
from ..schema_loader import (
    delete_schema as fs_delete_schema,
    list_schemas,
    load_schema,
    save_schema,
)
from .builder_agent import SchemaBuilderAgent, empty_schema, BuilderPhase
from .schema_validator import validate_schema

rotate_logs_on_startup(os.getenv('DCA_BUILDER_ROUTES_LOG', get_log_path('dca_builder_routes_log.txt')))

logger = logging.getLogger("DataCollectionBuilderRoutes")
log_level = getattr(logging, os.getenv('LOG_LEVEL', 'DEBUG'), logging.DEBUG)
logger.setLevel(log_level)
_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
_handler = WatchedFileHandler(
    filename=os.getenv('DCA_BUILDER_ROUTES_LOG', get_log_path('dca_builder_routes_log.txt')),
    encoding='utf-8',
)
_handler.setFormatter(_formatter)
logger.addHandler(_handler)


# Auth — same fallback pattern as routes.py
try:
    from role_decorators import api_key_or_session_required as _auth_decorator
except Exception:
    def _auth_decorator(*_args, **_kwargs):
        def wrap(fn):
            return fn
        return wrap


# In-process builder sessions (one per wizard tab)
_builder_sessions: Dict[str, SchemaBuilderAgent] = {}


def _get_or_create_session(session_id: str, initial_schema: dict = None) -> SchemaBuilderAgent:
    if session_id in _builder_sessions:
        return _builder_sessions[session_id]
    agent = SchemaBuilderAgent(session_id=session_id, initial_schema=initial_schema)
    _builder_sessions[session_id] = agent
    return agent


def register_builder_routes(bp: Blueprint):
    """Attach builder routes to the data collection blueprint."""

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------
    def _builder_branding_ctx(schema=None):
        """Build the common branding context for builder pages — uses
        app-level branding by default (the wizard isn't tied to one schema's
        branding), but if editing a specific schema we also expose its
        branding so authors see what they configured."""
        b = resolve_branding(schema=schema)
        return {
            'branding': b,
            'branding_style': branding_to_style_block(b),
            'safe_logo_url': safe_url(b.get('logo_url')),
            'safe_favicon_url': safe_url(b.get('favicon_url')),
        }

    @bp.route('/data-collection/builder', methods=['GET'])
    @_auth_decorator()
    @require_admin
    def builder_new_page():
        """New schema wizard."""
        return render_template(
            'builder/builder.html',
            mode='new',
            config_id='',
            initial_schema_json='null',
            **_builder_branding_ctx(),
        )

    @bp.route('/data-collection/builder/<config_id>', methods=['GET'])
    @_auth_decorator()
    @require_admin
    def builder_edit_page(config_id):
        """Edit an existing schema."""
        schema = load_schema(config_id, resolve_lookups=False)
        if not schema:
            # Treat as new with the config_id pre-filled
            return render_template(
                'builder/builder.html',
                mode='new',
                config_id=config_id,
                initial_schema_json='null',
                **_builder_branding_ctx(),
            )
        import json as _json
        return render_template(
            'builder/builder.html',
            mode='edit',
            config_id=config_id,
            initial_schema_json=_json.dumps(schema, default=str),
            **_builder_branding_ctx(schema=schema),
        )

    # ------------------------------------------------------------------
    # Conversation
    # ------------------------------------------------------------------
    @bp.route('/api/data-collection/builder/message', methods=['POST'])
    @_auth_decorator()
    @require_admin
    def builder_message():
        data = request.get_json() or {}
        session_id = data.get('session_id')
        message = (data.get('message') or '').strip()
        initial_schema = data.get('initial_schema')  # optional, only used to seed a new session

        if not session_id:
            session_id = str(uuid.uuid4())
        if not message:
            return jsonify({'status': 'error', 'error': 'message is required'}), 400

        agent = _get_or_create_session(session_id, initial_schema=initial_schema)

        try:
            response_text, metadata = agent.process_message(message)
        except Exception as e:
            logger.error(f"Builder process_message error: {e}", exc_info=True)
            return jsonify({'status': 'error', 'error': str(e)}), 500

        return jsonify({
            'status': 'success',
            'session_id': session_id,
            'response': response_text,
            'metadata': metadata,
        })

    @bp.route('/api/data-collection/builder/session/<session_id>', methods=['GET'])
    @_auth_decorator()
    @require_admin
    def builder_get_session(session_id):
        agent = _builder_sessions.get(session_id)
        if not agent:
            return jsonify({'status': 'error', 'error': 'Session not found'}), 404
        validation = validate_schema(agent.schema)
        return jsonify({
            'status': 'success',
            'session_id': session_id,
            'schema': agent.schema,
            'phase': agent.phase.value,
            'validation': validation,
        })

    @bp.route('/api/data-collection/builder/session/<session_id>', methods=['DELETE'])
    @_auth_decorator()
    @require_admin
    def builder_close_session(session_id):
        _builder_sessions.pop(session_id, None)
        return jsonify({'status': 'success'})

    # ------------------------------------------------------------------
    # Direct schema mutation (form-based editor — no chat round-trip)
    # ------------------------------------------------------------------
    @bp.route('/api/data-collection/builder/session/<session_id>/schema', methods=['PUT'])
    @_auth_decorator()
    @require_admin
    def builder_replace_schema(session_id):
        """Replace the in-memory schema for a session (called when the user edits the form)."""
        data = request.get_json() or {}
        schema = data.get('schema')
        if not isinstance(schema, dict):
            return jsonify({'status': 'error', 'error': 'schema must be an object'}), 400
        agent = _get_or_create_session(session_id, initial_schema=schema)
        agent.schema = schema
        # Make sure the agent's next turn sees the new state
        agent._set_system_prompt()
        agent._build_agent_executor()
        return jsonify({
            'status': 'success',
            'schema': agent.schema,
            'validation': validate_schema(agent.schema),
        })

    # ------------------------------------------------------------------
    # Validate / save / list / delete / duplicate
    # ------------------------------------------------------------------
    @bp.route('/api/data-collection/builder/validate', methods=['POST'])
    @_auth_decorator()
    @require_admin
    def builder_validate():
        data = request.get_json() or {}
        schema = data.get('schema') or {}
        existing_ids = {s['id'] for s in list_schemas() if s.get('id')}
        is_new = bool(data.get('is_new'))
        return jsonify({
            'status': 'success',
            'result': validate_schema(schema, existing_ids=existing_ids, is_new=is_new),
        })

    @bp.route('/api/data-collection/builder/save', methods=['POST'])
    @_auth_decorator()
    @require_admin
    def builder_save():
        data = request.get_json() or {}
        schema = data.get('schema') or {}
        is_new = bool(data.get('is_new', False))

        existing_ids = {s['id'] for s in list_schemas() if s.get('id')}
        result = validate_schema(schema, existing_ids=existing_ids, is_new=is_new)
        if not result['valid']:
            return jsonify({
                'status': 'error',
                'error': 'Schema has validation errors',
                'result': result,
            }), 400

        config_id = schema.get('id')
        ok, err = save_schema(config_id, schema)
        if not ok:
            return jsonify({'status': 'error', 'error': err or 'Save failed'}), 500
        return jsonify({
            'status': 'success',
            'config_id': config_id,
            'result': result,
        })

    @bp.route('/api/data-collection/builder/list', methods=['GET'])
    @_auth_decorator()
    @require_admin
    def builder_list():
        return jsonify({'status': 'success', 'schemas': list_schemas()})

    @bp.route('/api/data-collection/builder/<config_id>', methods=['DELETE'])
    @_auth_decorator()
    @require_admin
    def builder_delete(config_id):
        ok = fs_delete_schema(config_id)
        return jsonify({'status': 'success' if ok else 'error', 'deleted': ok})

    @bp.route('/api/data-collection/builder/<config_id>/duplicate', methods=['POST'])
    @_auth_decorator()
    @require_admin
    def builder_duplicate(config_id):
        data = request.get_json() or {}
        new_id = data.get('new_id')
        if not new_id:
            return jsonify({'status': 'error', 'error': 'new_id is required'}), 400

        original = load_schema(config_id, resolve_lookups=False)
        if not original:
            return jsonify({'status': 'error', 'error': 'Source schema not found'}), 404

        existing_ids = {s['id'] for s in list_schemas() if s.get('id')}
        if new_id in existing_ids:
            return jsonify({'status': 'error', 'error': f'Schema id "{new_id}" already exists'}), 400

        import copy
        new_schema = copy.deepcopy(original)
        new_schema['id'] = new_id
        new_schema['name'] = (new_schema.get('name') or '') + ' (copy)'

        ok, err = save_schema(new_id, new_schema)
        if not ok:
            return jsonify({'status': 'error', 'error': err or 'Save failed'}), 500
        return jsonify({'status': 'success', 'config_id': new_id, 'schema': new_schema})

    # ------------------------------------------------------------------
    # Pickers — workflows / agents lists for the action editor
    # ------------------------------------------------------------------
    @bp.route('/api/data-collection/builder/workflows', methods=['GET'])
    @_auth_decorator()
    @require_admin
    def builder_list_workflows():
        try:
            from AppUtils import get_db_connection
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", (os.environ.get('API_KEY', ''),))
            cursor.execute("SELECT id, workflow_name FROM Workflows ORDER BY workflow_name")
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            return jsonify({
                'status': 'success',
                'workflows': [{'id': r[0], 'name': r[1]} for r in rows],
            })
        except Exception as e:
            logger.warning(f"Could not list workflows: {e}")
            return jsonify({'status': 'success', 'workflows': []})

    @bp.route('/api/data-collection/builder/agents', methods=['GET'])
    @_auth_decorator()
    @require_admin
    def builder_list_agents():
        try:
            from AppUtils import get_db_connection
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", (os.environ.get('API_KEY', ''),))
            cursor.execute(
                "SELECT id, description FROM Agents WHERE enabled = 1 ORDER BY description"
            )
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            return jsonify({
                'status': 'success',
                'agents': [{'id': r[0], 'name': r[1]} for r in rows],
            })
        except Exception as e:
            logger.warning(f"Could not list agents: {e}")
            return jsonify({'status': 'success', 'agents': []})

    # ------------------------------------------------------------------
    # Empty schema helper (for "new" wizard state)
    # ------------------------------------------------------------------
    @bp.route('/api/data-collection/builder/connections', methods=['GET'])
    @_auth_decorator()
    @require_admin
    def builder_list_connections():
        """List the platform's database connections so the schema
        builder can offer a dropdown for `lookup_data.<ref>.source =
        "database"`. Returns id + name + database_type so the UI can
        show enough context for the author to pick the right one.
        Falls back to an empty list if the platform DB plumbing isn't
        reachable."""
        try:
            from AppUtils import get_db_connection
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", (os.environ.get('API_KEY', ''),))
            cursor.execute(
                "SELECT connection_id, connection_name, server, database_name, database_type "
                "FROM Connections ORDER BY connection_name"
            )
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            return jsonify({
                'status': 'success',
                'connections': [
                    {
                        'id': r[0],
                        'name': r[1],
                        'server': r[2],
                        'database': r[3],
                        'type': r[4],
                    }
                    for r in rows
                ],
            })
        except Exception as e:
            logger.warning(f"Could not list connections: {e}")
            return jsonify({'status': 'success', 'connections': []})

    @bp.route('/api/data-collection/builder/connections/<int:connection_id>/columns', methods=['GET'])
    @_auth_decorator()
    @require_admin
    def builder_view_columns(connection_id):
        """For a given connection + view (or table), return the column
        names. Used by the schema builder to populate the
        `select_columns` multi-select after the author picks a view."""
        view = (request.args.get('view') or '').strip()
        if not view:
            return jsonify({'status': 'error', 'error': 'view query param required'}), 400
        # Validate identifier — same rule the runtime resolver uses
        from data_collection_agent.db_lookup import _is_safe_identifier
        if not _is_safe_identifier(view):
            return jsonify({
                'status': 'error',
                'error': f"Unsafe view name: {view!r}. Use schema.table or table only.",
            }), 400
        try:
            from DataUtils import get_database_connection_string
            import pyodbc
            conn_str, _, _ = get_database_connection_string(connection_id)
            if not conn_str:
                return jsonify({
                    'status': 'error',
                    'error': f'No connection string for connection_id={connection_id}',
                }), 404
            # SELECT TOP 0 returns just metadata — no rows transferred
            sql = f"SELECT TOP 0 * FROM {view}"
            with pyodbc.connect(conn_str) as conn:
                cur = conn.cursor()
                cur.execute(sql)
                cols = [c[0] for c in cur.description]
            return jsonify({'status': 'success', 'columns': cols})
        except Exception as e:
            logger.warning(f"Could not list columns for {view}@{connection_id}: {e}")
            return jsonify({
                'status': 'success',
                'columns': [],
                'warning': f'Could not query view: {e}',
            })

    @bp.route('/api/data-collection/builder/custom-tools', methods=['GET'])
    @_auth_decorator()
    @require_admin
    def builder_list_custom_tools():
        """List the platform's custom tools (folders under the
        configured tools directory). Returns each tool's name +
        description so the schema builder can offer a multi-select. The
        author opts a schema in by listing tool names in
        `schema.custom_tools`."""
        try:
            import json as _json
            try:
                import config as cfg
                tools_folder = cfg.CUSTOM_TOOLS_FOLDER
            except Exception as e:
                logger.warning(f"Custom tools folder not resolvable: {e}")
                return jsonify({'status': 'success', 'tools': []})
            if not os.path.isdir(tools_folder):
                return jsonify({'status': 'success', 'tools': []})
            out = []
            for name in sorted(os.listdir(tools_folder)):
                folder = os.path.join(tools_folder, name)
                if not os.path.isdir(folder):
                    continue
                cfg_path = os.path.join(folder, 'config.json')
                if not os.path.isfile(cfg_path):
                    continue
                try:
                    with open(cfg_path, 'r', encoding='utf-8') as f:
                        c = _json.load(f) or {}
                    out.append({
                        'name': name,
                        'function_name': c.get('function_name') or name,
                        'description': c.get('description', ''),
                        'parameters': c.get('parameters', []),
                    })
                except Exception as e:
                    logger.debug(f"Skipping unreadable custom tool {name}: {e}")
            return jsonify({'status': 'success', 'tools': out})
        except Exception as e:
            logger.warning(f"Could not list custom tools: {e}")
            return jsonify({'status': 'success', 'tools': []})

    @bp.route('/api/data-collection/builder/empty-schema', methods=['GET'])
    @_auth_decorator()
    @require_admin
    def builder_empty_schema():
        return jsonify({'status': 'success', 'schema': empty_schema()})
