"""
Data Explorer v2 — Flask Blueprint
====================================
New streaming-capable route for the Data Explorer UI.
Wraps the existing LLMDataEngine with SSE status events
so the frontend gets immediate feedback while the
synchronous engine processes.

All existing data assistant code remains untouched.
"""

import json
import logging
import os
import pickle
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from flask import (
    Blueprint,
    Response,
    jsonify,
    render_template,
    request,
    session,
    url_for,
)
from flask_cors import cross_origin
from flask_login import login_required, current_user

import config as cfg
import pandas as pd

logger = logging.getLogger(__name__)

data_explorer_bp = Blueprint(
    "data_explorer_bp",
    __name__,
    template_folder="../templates",
    static_folder="../static",
)

# Thread pool for running the synchronous engine off the request thread
_executor = ThreadPoolExecutor(max_workers=4)


# ─── Helpers (duplicated from app.py to avoid circular imports) ───────────

def _remove_html_tags(text):
    clean_text = re.sub(r"<.*?>", "", str(text))
    clean_text = clean_text.replace("&quot;", '"')
    return clean_text


def _clean_input_text(text):
    clean_text = re.sub(r"<.*?>", "", str(text))
    clean_text = re.sub(r"<[^>]*>", "", clean_text)
    clean_text = re.sub(r"\&nbsp;.*$", "", clean_text)
    clean_text = clean_text.replace("&quot;", '"')
    return clean_text


def _get_engine_store():
    """Get the global llm_data_engines dict from app.py (imported at call‑time to avoid circular import)."""
    import app as main_app
    return main_app.llm_data_engines


def _get_enhancement_deps():
    """Import enhancement helpers at call-time."""
    from engine_enhancements import enhance_engines
    import app as main_app
    return enhance_engines, main_app.nlq_systems


def _get_session_engine(session_id):
    """Deserialize the LLMDataEngine for a session."""
    store = _get_engine_store()
    serialized = store.get(session_id)
    if serialized is None:
        return None
    try:
        return pickle.loads(serialized)
    except Exception as e:
        logger.error(f"Error deserializing engine for session {session_id}: {e}")
        return None


def _save_session_engine(session_id, engine):
    """Serialize and store the engine back."""
    store = _get_engine_store()
    try:
        store[session_id] = pickle.dumps(engine)
    except Exception as e:
        logger.error(f"Error serializing engine for session {session_id}: {e}")


def _serialize_answer(answer, answer_type, format_as_json=True):
    """Convert the engine answer to a JSON-safe string + structured data for tables."""
    table_data = None

    if answer_type in ("dataframe", "multi_dataframe"):
        if isinstance(answer, pd.DataFrame):
            # Build structured table data for the frontend
            headers = list(answer.columns)
            rows = answer.values.tolist()
            table_data = {"headers": headers, "rows": rows}
            return answer.to_html(), table_data
        else:
            return str(answer), table_data
    else:
        return str(answer), table_data


# ─── SSE Helpers ─────────────────────────────────────────────────────────

def _sse_event(event_type, data):
    """Format a single SSE event."""
    payload = json.dumps(data, default=str)
    return f"event: {event_type}\ndata: {payload}\n\n"


# ─── Routes ──────────────────────────────────────────────────────────────

@data_explorer_bp.route("/data_explorer")
@login_required
def data_explorer():
    """Serve the Data Explorer page and initialize the engine."""
    try:
        if "session_id" not in session:
            session["session_id"] = str(uuid.uuid4())

        from LLMDataEngineV2 import LLMDataEngine
        enhance_engines, nlq_systems = _get_enhancement_deps()

        engine = LLMDataEngine(provider=cfg.NLQ_PROVIDER)
        enhanced_qe, enhanced_ae = enhance_engines(engine, nlq_systems)
        engine.query_engine = enhanced_qe
        engine.analytical_engine = enhanced_ae
        _save_session_engine(session["session_id"], engine)
    except Exception as e:
        logger.error(f"Error initializing Data Explorer engine: {e}")

    return render_template("data_explorer.html")


@data_explorer_bp.route("/data_explorer/chat", methods=["POST"])
@cross_origin()
@login_required
def data_explorer_chat():
    """
    Chat endpoint — standard JSON request/response.

    Wraps the synchronous LLMDataEngine.get_answer().
    Status animation is handled client-side since Waitress
    (WSGI) buffers responses and cannot stream SSE chunks.
    """
    import ast as _ast

    data = request.get_json()
    agent_id = data.get("agent_id")
    question = data.get("question", "")
    conversation_history_raw = data.get("history", "[]")

    session_id = session.get("session_id")
    if not session_id:
        return jsonify({"error": "No session"}), 400

    query_id = str(uuid.uuid4())[:8]

    engine = _get_session_engine(session_id)
    if engine is None:
        return jsonify({"error": "Session expired. Please refresh the page."}), 400

    # Parse conversation history
    conv_history = []
    try:
        raw = str(conversation_history_raw)
        if raw and raw not in ("", "[]", "None"):
            conv_history = _ast.literal_eval(raw)
    except Exception:
        conv_history = []

    # Replay history into engine
    engine.clear_chat_hist()
    for entry in conv_history:
        is_user = entry.get("role") == "Q"
        engine.add_message_to_hist(entry.get("content", ""), is_user=is_user)

    cleaned_question = _clean_input_text(question)

    try:
        result = engine.get_answer(agent_id, cleaned_question)
    except Exception as e:
        logger.error(f"Engine error: {e}")
        _save_session_engine(session_id, engine)
        return jsonify({"error": f"Error processing query: {str(e)}"}), 500

    # Unpack result (handles both dict and tuple formats)
    if isinstance(result, dict):
        answer = result["answer"]
        explain = result.get("explain", "")
        clarify = result.get("clarify", "")
        answer_type = result.get("answer_type", "string")
        special_message = result.get("special_message", "")
        query = result.get("query", "")
        rich_content = result.get("rich_content")
        rich_content_enabled = result.get("rich_content_enabled", False)
    else:
        answer, explain, clarify, answer_type, special_message, _, _, query = result
        rich_content = None
        rich_content_enabled = False

    # Try rich content rendering
    if not rich_content_enabled:
        try:
            rich_content, _ = engine.format_response_with_rich_content(
                answer, answer_type, {"question": cleaned_question}
            )
            rich_content_enabled = True
        except Exception:
            rich_content_enabled = False

    # Handle chart images in special_message (matplotlib generates base64 PNG)
    if special_message and str(special_message).strip():
        sm = str(special_message)
        if "data:image" in sm:
            # Extract the image src from the <img> tag or use raw data URI
            import re as _re
            img_match = _re.search(r'src=["\']([^"\']+)["\']', sm)
            img_src = img_match.group(1) if img_match else sm.strip()
            
            chart_image_block = {
                "type": "chart_image",
                "content": img_src,
                "metadata": {"title": "Chart", "source": "matplotlib"}
            }
            
            if rich_content_enabled and isinstance(rich_content, dict) and "blocks" in rich_content:
                # Replace placeholder "See chart..." blocks with the actual image
                new_blocks = []
                replaced = False
                for block in rich_content["blocks"]:
                    if block.get("type") == "chart" and isinstance(block.get("content"), str):
                        new_blocks.append(chart_image_block)
                        replaced = True
                    else:
                        new_blocks.append(block)
                if not replaced:
                    new_blocks.insert(0, chart_image_block)
                rich_content["blocks"] = new_blocks
            else:
                rich_content = {
                    "type": "rich_content",
                    "blocks": [chart_image_block]
                }
                rich_content_enabled = True

    # Serialize the answer for JSON transport
    answer_str, table_data = _serialize_answer(answer, answer_type)

    # Build the SQL string for display
    sql_display = ""
    if query:
        sql_display = str(query)

    response_payload = {
        "answer": answer_str,
        "answer_type": answer_type,
        "explanation": explain,
        "clarification": clarify,
        "special_message": str(special_message) if special_message else "",
        "query": sql_display,
        "query_id": query_id,
        "rich_content": rich_content if rich_content_enabled else None,
        "rich_content_enabled": rich_content_enabled,
        "table_data": table_data,
    }

    # Save engine state
    _save_session_engine(session_id, engine)

    return jsonify(response_payload)


@data_explorer_bp.route("/data_explorer/reset", methods=["POST"])
@cross_origin()
@login_required
def data_explorer_reset():
    """Reset the Data Explorer session — create a fresh engine."""
    try:
        from LLMDataEngineV2 import LLMDataEngine
        enhance_engines, nlq_systems = _get_enhancement_deps()

        session_id = session.get("session_id", str(uuid.uuid4()))
        session["session_id"] = session_id

        engine = LLMDataEngine(provider=cfg.NLQ_PROVIDER)
        enhanced_qe, enhanced_ae = enhance_engines(engine, nlq_systems)
        engine.query_engine = enhanced_qe
        engine.analytical_engine = enhanced_ae
        _save_session_engine(session_id, engine)

        return jsonify({"status": "ok", "message": "Session reset."})
    except Exception as e:
        logger.error(f"Error resetting session: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@data_explorer_bp.route("/data_explorer/refresh", methods=["POST"])
@cross_origin()
@login_required
def data_explorer_refresh_query():
    """
    Re-execute a stored SQL query and return fresh data.
    Used by the dashboard refresh feature — no LLM call needed.
    """
    data = request.get_json()
    sql = data.get("sql", "")
    agent_id = data.get("agent_id", "")
    query_id = data.get("query_id", "")

    if not sql or not agent_id:
        return jsonify({"error": "Missing sql or agent_id"}), 400

    session_id = session.get("session_id")
    engine = _get_session_engine(session_id)
    if engine is None:
        return jsonify({"error": "Session expired"}), 400

    try:
        # Use the query engine to re-execute the SQL
        engine.query_engine._set_target_database(agent_id)
        df = engine.query_engine._load_query(sql)

        if df is not None and isinstance(df, pd.DataFrame):
            headers = list(df.columns)
            rows = df.values.tolist()
            return jsonify({
                "status": "ok",
                "query_id": query_id,
                "table_data": {"headers": headers, "rows": rows},
                "row_count": len(df),
            })
        else:
            return jsonify({"error": "Query returned no data"}), 400
    except Exception as e:
        logger.error(f"Refresh query error: {e}")
        return jsonify({"error": str(e)}), 500


# ─── Dashboard DB helpers (with RLS tenant context) ──────────────────────

def _dashboard_db_execute(query, params=None, fetch=False):
    """
    Execute a query against llm_Dashboards with proper RLS context.

    Pattern matches DataUtils.py:
      1. get_db_connection()
      2. EXEC tenant.sp_setTenantContext with API_KEY
      3. Execute the actual query
      4. Commit + close
    """
    from CommonUtils import get_db_connection

    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # Set RLS tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.environ.get("API_KEY", ""))

        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)

        result = None
        if fetch:
            result = cursor.fetchall()

        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@data_explorer_bp.route("/data_explorer/dashboard/save", methods=["POST"])
@cross_origin()
@login_required
def save_dashboard():
    """Save a dashboard layout JSON to the database."""
    data = request.get_json()
    title = data.get("title", "Untitled Dashboard")
    layout = data.get("layout", {})
    dashboard_id = data.get("dashboard_id")  # None for new, existing for update

    try:
        user_id = current_user.id if current_user.is_authenticated else None
        layout_json = json.dumps(layout, default=str)

        if dashboard_id:
            # Update existing (scoped to user via WHERE + RLS)
            _dashboard_db_execute(
                "UPDATE llm_Dashboards SET title = ?, layout_json = ?, updated_at = GETDATE() WHERE id = ? AND user_id = ?",
                [title, layout_json, dashboard_id, user_id],
            )
        else:
            # Insert new
            dashboard_id = str(uuid.uuid4())[:8]
            _dashboard_db_execute(
                "INSERT INTO llm_Dashboards (id, user_id, title, layout_json, created_at, updated_at) VALUES (?, ?, ?, ?, GETDATE(), GETDATE())",
                [dashboard_id, user_id, title, layout_json],
            )

        return jsonify({"status": "ok", "dashboard_id": dashboard_id})
    except Exception as e:
        logger.error(f"Save dashboard error: {e}")
        return jsonify({"error": str(e)}), 500


@data_explorer_bp.route("/data_explorer/dashboard/list", methods=["GET"])
@cross_origin()
@login_required
def list_dashboards():
    """List saved dashboards for the current user."""
    try:
        user_id = current_user.id if current_user.is_authenticated else None
        rows = _dashboard_db_execute(
            "SELECT id, title, created_at, updated_at FROM llm_Dashboards WHERE user_id = ? ORDER BY updated_at DESC",
            [user_id],
            fetch=True,
        )
        dashboards = []
        if rows:
            for row in rows:
                dashboards.append({
                    "id": str(row[0]),
                    "title": str(row[1]),
                    "created_at": str(row[2]),
                    "updated_at": str(row[3]),
                })
        return jsonify({"dashboards": dashboards})
    except Exception as e:
        logger.warning(f"List dashboards: {e}")
        # Table may not exist yet — return empty
        return jsonify({"dashboards": []})


@data_explorer_bp.route("/data_explorer/dashboard/<dashboard_id>", methods=["GET"])
@cross_origin()
@login_required
def load_dashboard(dashboard_id):
    """Load a saved dashboard by ID."""
    try:
        user_id = current_user.id if current_user.is_authenticated else None
        rows = _dashboard_db_execute(
            "SELECT id, title, layout_json FROM llm_Dashboards WHERE id = ? AND user_id = ?",
            [dashboard_id, user_id],
            fetch=True,
        )
        if rows and len(rows) > 0:
            row = rows[0]
            return jsonify({
                "id": str(row[0]),
                "title": str(row[1]),
                "layout": json.loads(row[2]) if row[2] else {},
            })
        return jsonify({"error": "Not found"}), 404
    except Exception as e:
        logger.error(f"Load dashboard error: {e}")
        return jsonify({"error": str(e)}), 500


@data_explorer_bp.route("/data_explorer/dashboard/<dashboard_id>", methods=["DELETE"])
@cross_origin()
@login_required
def delete_dashboard(dashboard_id):
    """Delete a saved dashboard."""
    try:
        user_id = current_user.id if current_user.is_authenticated else None
        _dashboard_db_execute(
            "DELETE FROM llm_Dashboards WHERE id = ? AND user_id = ?",
            [dashboard_id, user_id],
        )
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Delete dashboard error: {e}")
        return jsonify({"error": str(e)}), 500


@data_explorer_bp.route("/data_explorer/dashboard/<dashboard_id>/rename", methods=["POST"])
@cross_origin()
@login_required
def rename_dashboard(dashboard_id):
    """Rename a saved dashboard."""
    try:
        data = request.get_json(force=True)
        new_title = (data.get("title") or "").strip()
        if not new_title:
            return jsonify({"error": "Title required"}), 400
        user_id = current_user.id if current_user.is_authenticated else None
        _dashboard_db_execute(
            "UPDATE llm_Dashboards SET title = ? WHERE id = ? AND user_id = ?",
            [new_title, dashboard_id, user_id],
        )
        return jsonify({"status": "ok", "title": new_title})
    except Exception as e:
        logger.error(f"Rename dashboard error: {e}")
        return jsonify({"error": str(e)}), 500


# ─── Internal API (for Command Center delegation) ────────────────────────

# Persistent engine cache for internal API (keyed by caller-supplied session_id)
_internal_engines = {}


@data_explorer_bp.route("/data_explorer/internal/query", methods=["POST"])
@cross_origin()
def data_explorer_internal_query():
    """
    Internal query endpoint for Command Center delegation.

    Uses API key auth (X-API-Key header) instead of Flask session.
    Maintains a persistent engine cache so data agents don't need
    to re-initialize on every request (the root cause of failures
    when using /api/agents/{id}/chat).

    Request body:
        {
            "agent_id": 231,
            "question": "Show me total sales by state",
            "session_id": "cc-session-abc123",  (optional, for engine reuse)
            "history": []  (optional)
        }
    """
    import ast as _ast
    from role_decorators import validate_api_key

    # Validate API key (same auth as /api/agents/{id}/chat)
    api_key = request.headers.get("X-API-Key", "") or request.headers.get("X-Internal-API-Key", "")
    if not api_key or not validate_api_key(api_key).get("valid"):
        return jsonify({"error": "Unauthorized — valid API key required"}), 401

    data = request.get_json()
    agent_id = data.get("agent_id")
    question = data.get("question", "")
    caller_session_id = data.get("session_id", "internal-default")
    conversation_history_raw = data.get("history", "[]")

    if agent_id is None or question is None or str(question).strip() == "":
        return jsonify({"error": "agent_id and question are required"}), 400

    # Normalize agent_id
    try:
        agent_id = int(agent_id)
    except Exception:
        return jsonify({"error": f"agent_id must be an integer (got: {agent_id})"}), 400

    # Get or create a persistent engine for this session
    engine = _internal_engines.get(caller_session_id)
    if engine is None:
        try:
            from LLMDataEngineV2 import LLMDataEngine
            enhance_engines, nlq_systems = _get_enhancement_deps()
            engine = LLMDataEngine(provider=cfg.NLQ_PROVIDER)
            enhanced_qe, enhanced_ae = enhance_engines(engine, nlq_systems)
            engine.query_engine = enhanced_qe
            engine.analytical_engine = enhanced_ae
            _internal_engines[caller_session_id] = engine
            logger.info(f"[internal_query] Created new engine for session {caller_session_id}")
        except Exception as e:
            logger.error(f"[internal_query] Engine init failed: {e}")
            return jsonify({"error": f"Engine initialization failed: {str(e)}"}), 500

    # Parse conversation history
    conv_history = []
    try:
        raw = str(conversation_history_raw)
        if raw and raw not in ("", "[]", "None"):
            conv_history = _ast.literal_eval(raw) if isinstance(conversation_history_raw, str) else conversation_history_raw
    except Exception:
        conv_history = []

    # Replay history
    engine.clear_chat_hist()
    for entry in conv_history:
        if isinstance(entry, dict):
            is_user = entry.get("role") == "Q"
            engine.add_message_to_hist(entry.get("content", ""), is_user=is_user)

    cleaned_question = _clean_input_text(question)

    try:
        result = engine.get_answer(agent_id, cleaned_question)
    except Exception as e:
        logger.error(f"[internal_query] Engine error: {e}")
        return jsonify({"error": f"Query error: {str(e)}"}), 500

    # Unpack result
    special_message = ""
    if isinstance(result, dict):
        answer = result["answer"]
        answer_type = result.get("answer_type", "string")
        query = result.get("query", "")
        rich_content = result.get("rich_content")
        special_message = result.get("special_message", "")
    else:
        answer = result[0] if isinstance(result, tuple) else str(result)
        answer_type = result[3] if isinstance(result, tuple) and len(result) > 3 else "string"
        special_message = result[4] if isinstance(result, tuple) and len(result) > 4 else ""
        query = result[7] if isinstance(result, tuple) and len(result) > 7 else ""
        rich_content = None

    # Handle chart images in special_message (matplotlib base64 PNG)
    if special_message and str(special_message).strip():
        import re as _re
        sm = str(special_message)
        if "data:image" in sm:
            img_match = _re.search(r'src=["\']([^"\']+)["\']', sm)
            img_src = img_match.group(1) if img_match else sm.strip()

            chart_image_block = {
                "type": "chart_image",
                "content": img_src,
                "metadata": {"title": "Chart", "source": "matplotlib"}
            }

            if isinstance(rich_content, dict) and rich_content.get("blocks"):
                new_blocks = []
                replaced = False
                for block in rich_content["blocks"]:
                    if block.get("type") == "chart" and isinstance(block.get("content"), str):
                        new_blocks.append(chart_image_block)
                        replaced = True
                    else:
                        new_blocks.append(block)
                if not replaced:
                    new_blocks.insert(0, chart_image_block)
                rich_content["blocks"] = new_blocks
            else:
                rich_content = {
                    "type": "rich_content",
                    "blocks": [chart_image_block]
                }

    return jsonify({
        "status": "success",
        "response": str(answer),
        "answer_type": answer_type,
        "query": str(query),
        "rich_content": rich_content,
        "agent_type": "data",
    })
