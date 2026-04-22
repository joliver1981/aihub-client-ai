"""
Solutions Gallery — connection scaffold install / export routes.

A "connection scaffold" is a credential-less description of a database
connection (type, server, database name, column roles). The Solutions
bundler strips real credentials and replaces them with ${PLACEHOLDER}s.
At install time, the wizard prompts the user for the real values and
this route creates the connection in the platform.

Gated behind the `solutions_enabled` flag. Does not modify or replace
the existing `/add/connection` route — it calls it internally via the
Flask test client.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional

from flask import Blueprint, abort, current_app, jsonify, request
from flask_login import login_required

logger = logging.getLogger(__name__)

connection_install_bp = Blueprint("solution_connection_install", __name__)


def _require_flag():
    try:
        from feature_flags import is_feature_enabled  # type: ignore
        if not is_feature_enabled("solutions_enabled"):
            abort(404)
    except Exception:
        abort(404)


_NAME_RE = re.compile(r"^[A-Za-z0-9 _()\-\.]{1,120}$")


# ────────────────────────────────────────────────────────────────
# Export: return a connection scaffold (no credentials) by id
# GET /api/solutions/connections/export/<id>
# ────────────────────────────────────────────────────────────────

@connection_install_bp.route("/api/solutions/connections/export/<int:connection_id>", methods=["GET"])
@login_required
def export_connection(connection_id: int):
    _require_flag()
    try:
        from AppUtils import get_connection_by_id  # type: ignore
    except ImportError:
        return jsonify({"error": "connection helper unavailable"}), 500
    try:
        conn = get_connection_by_id(connection_id)
    except Exception as e:
        logger.exception("get_connection_by_id failed")
        return jsonify({"error": f"lookup failed: {e}"}), 500
    if isinstance(conn, list):
        conn = conn[0] if conn else None
    if not conn:
        return jsonify({"error": "connection not found"}), 404

    scaffold = {
        "connection_name": conn.get("connection_name") or conn.get("name") or f"Connection {connection_id}",
        "database_type": conn.get("database_type") or conn.get("type") or "",
        "server": "${DB_SERVER}",
        "database_name": conn.get("database_name") or conn.get("database") or "",
        "user_name": "${DB_USER}",
        "password": "${DB_PASSWORD}",
        "port": conn.get("port"),
        "parameters": conn.get("parameters") or "",
    }
    return jsonify(scaffold)


# ────────────────────────────────────────────────────────────────
# Install: create a new connection from a scaffold + resolved credentials
# POST /api/solutions/connections/install
# Body: {"scaffold": {...}, "conflict_mode": ...}
# ────────────────────────────────────────────────────────────────

_SENSITIVE_FIELDS = ("password", "token", "api_key")


def _stash_secrets_and_replace(
    name: str, scaffold: Dict[str, Any], solution_id: str
) -> Dict[str, Any]:
    """For each sensitive field with a real value, store that value in
    LocalSecrets under a deterministic key and replace the field with a
    `{{LOCAL_SECRET:<key>}}` reference the DB-resolver already understands.

    This keeps plaintext credentials out of the Connections table; the
    runtime resolver in DataUtils.get_db_conn_str pulls them back on demand.
    """
    try:
        from local_secrets import get_secrets_manager  # type: ignore
    except ImportError:
        return dict(scaffold)

    manager = get_secrets_manager()
    out = dict(scaffold)
    safe_conn = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or "connection"
    safe_sol = re.sub(r"[^A-Za-z0-9_]+", "_", solution_id or "solution").strip("_") or "solution"

    for field in _SENSITIVE_FIELDS:
        val = out.get(field)
        if not val or not isinstance(val, str):
            continue
        # Don't re-wrap something that's already a LOCAL_SECRET reference.
        if "{{LOCAL_SECRET:" in val:
            continue
        secret_key = f"SOL_{safe_sol}_{safe_conn}_{field.upper()}"
        try:
            manager.set(
                secret_key, val,
                description=f"Auto-created by solution '{solution_id}' for connection '{name}' ({field})",
                category="solutions",
            )
            out[field] = "{{LOCAL_SECRET:" + secret_key + "}}"
        except Exception as e:
            logger.warning("Could not stash %s for %s as LocalSecret: %s", field, name, e)
    return out


@connection_install_bp.route("/api/solutions/connections/install", methods=["POST"])
@login_required
def install_connection():
    _require_flag()
    data = request.get_json(silent=True) or {}
    scaffold = data.get("scaffold") or {}
    conflict_mode = str(data.get("conflict_mode") or "rename").lower()
    solution_id = str(data.get("solution_id") or "solution")

    if not isinstance(scaffold, dict):
        return jsonify({"error": "scaffold must be object"}), 400

    name = str(scaffold.get("connection_name") or "").strip()
    if not name or not _NAME_RE.match(name):
        return jsonify({"error": "invalid connection_name"}), 400

    # If a connection with this name already exists, apply conflict mode.
    existing_id = _find_connection_id_by_name(name)
    if existing_id is not None:
        if conflict_mode == "skip":
            return jsonify({"status": "skipped", "connection_id": existing_id}), 200
        if conflict_mode == "rename":
            # Append _2, _3, … until we find a free name.
            i = 2
            while True:
                candidate = f"{name}_{i}"
                if _find_connection_id_by_name(candidate) is None:
                    name = candidate
                    break
                i += 1
            scaffold["connection_name"] = name
            existing_id = None

    # Move any resolved sensitive values into LocalSecrets and replace them
    # with {{LOCAL_SECRET:…}} references before we hand the scaffold to the
    # existing /add/connection route.
    scaffold = _stash_secrets_and_replace(name, scaffold, solution_id)

    # Build the body the existing /add/connection expects.
    body = {
        "connection_id": existing_id or 0,  # 0 = create new per the existing route
        "connection_name": name,
        "server": scaffold.get("server") or "",
        "port": scaffold.get("port") or 0,
        "database_name": scaffold.get("database_name") or "",
        "database_type": scaffold.get("database_type") or "",
        "user_name": scaffold.get("user_name") or "",
        "password": scaffold.get("password") or "",
        "parameters": scaffold.get("parameters") or "",
        "connection_string": "",
        "odbc_driver": scaffold.get("odbc_driver") or "",
        "instance_url": scaffold.get("instance_url") or "",
        "token": scaffold.get("token") or "",
        "api_key": scaffold.get("api_key") or "",
        "dsn": scaffold.get("dsn") or "",
    }

    # Call the existing /add/connection route via Flask's test client so
    # we reuse all its storage logic without touching it. We pass through
    # the session cookie so the existing auth decorator is satisfied.
    try:
        with current_app.test_client() as client:
            # Forward session cookie from the incoming request.
            cookie_header = request.headers.get("Cookie", "")
            resp = client.post(
                "/add/connection",
                json=body,
                headers={"Cookie": cookie_header} if cookie_header else {},
            )
        if resp.status_code not in (200, 201):
            return jsonify({
                "error": "underlying /add/connection failed",
                "status_code": resp.status_code,
                "detail": resp.data[:500].decode("utf-8", errors="ignore"),
            }), 500
    except Exception as e:
        logger.exception("install_connection failed")
        return jsonify({"error": str(e)}), 500

    new_id = _find_connection_id_by_name(name)
    return jsonify({
        "status": "installed",
        "connection_name": name,
        "connection_id": new_id,
    }), 201


def _find_connection_id_by_name(name: str) -> Optional[int]:
    try:
        from AppUtils import get_connections  # type: ignore
        conns = get_connections()
    except Exception:
        conns = None

    if conns:
        for c in conns:
            if str(c.get("connection_name") or c.get("name") or "").strip() == name:
                try:
                    return int(c.get("connection_id") or c.get("id"))
                except (TypeError, ValueError):
                    return None
    # Fallback: call /get/connections via test client.
    try:
        with current_app.test_client() as client:
            resp = client.get("/get/connections")
            if resp.status_code == 200:
                data = resp.get_json()
                if isinstance(data, str):
                    data = json.loads(data)
                for c in (data or []):
                    if str(c.get("connection_name") or c.get("name") or "").strip() == name:
                        try:
                            return int(c.get("id") or c.get("connection_id"))
                        except (TypeError, ValueError):
                            return None
    except Exception:
        pass
    return None
