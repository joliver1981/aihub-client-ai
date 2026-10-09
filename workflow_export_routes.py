"""
Solutions Gallery — workflow export / import routes.

Workflows are stored today as JSON files in `workflows/<name>` (no
export route exists). These endpoints add serialize / deserialize
operations that the Solutions bundler and installer use. All routes
are gated behind the `solutions_enabled` feature flag.

No existing workflow routes are modified — this module is purely
additive. The storage layer (the `workflows/` directory) is shared
but only appended to.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, abort, jsonify, request
from flask_login import login_required

logger = logging.getLogger(__name__)

workflow_export_bp = Blueprint("solution_workflow_export", __name__)

_NAME_RE = re.compile(r"^[A-Za-z0-9 _()\-\.]{1,120}$")


def _require_flag():
    """Return 404 when the experimental Solutions feature is disabled."""
    try:
        from feature_flags import is_feature_enabled  # type: ignore
        if not is_feature_enabled("solutions_enabled"):
            abort(404)
    except Exception:
        # If feature_flags itself is unavailable, fail-closed.
        abort(404)


def _workflows_root() -> Path:
    try:
        import config as cfg  # type: ignore
        return Path(getattr(cfg, "APP_ROOT", ".")) / "workflows"
    except Exception:
        return Path("workflows")


def _safe_workflow_path(name: str) -> Optional[Path]:
    """Map a workflow display-name to a safe path under workflows/."""
    name = (name or "").strip()
    if not name or not _NAME_RE.match(name):
        return None
    root = _workflows_root().resolve()
    # Workflow files can be stored with or without .json extension.
    for candidate in (name, f"{name}.json"):
        p = (root / candidate).resolve()
        try:
            if p.relative_to(root) and (p.is_file() or True):
                return p
        except ValueError:
            return None
    return None


def _workflow_body(doc: Any) -> Optional[Dict[str, Any]]:
    """The dict that holds nodes/variables: either the doc itself or a
    {"workflow": {...}} wrapper (both shapes occur in bundles)."""
    if not isinstance(doc, dict):
        return None
    inner = doc.get("workflow")
    return inner if isinstance(inner, dict) else doc


def merge_preserved_variables(new_doc: Any, old_doc: Any) -> List[str]:
    """Overwrite-as-upgrade: keep THIS system's value for every workflow
    variable that still exists (same name, same type) in the incoming
    version, so re-installing a solution doesn't wipe settings the client
    made (folder paths, recipients…). New variables get the bundle's
    default; removed ones go. Mutates new_doc; returns the names whose
    value was kept (i.e. differed from the bundle's default)."""
    new_body, old_body = _workflow_body(new_doc), _workflow_body(old_doc)
    new_vars = (new_body or {}).get("variables")
    old_vars = (old_body or {}).get("variables")
    if not isinstance(new_vars, dict) or not isinstance(old_vars, dict):
        return []
    kept: List[str] = []
    for var_name, nv in new_vars.items():
        ov = old_vars.get(var_name)
        if not isinstance(nv, dict) or not isinstance(ov, dict) or "defaultValue" not in ov:
            continue
        if (ov.get("type") or "string") != (nv.get("type") or "string"):
            continue  # the variable changed meaning — the old value may not fit
        if ov["defaultValue"] != nv.get("defaultValue"):
            nv["defaultValue"] = ov["defaultValue"]
            kept.append(var_name)
    return kept


def _load_existing_workflow_db(name: str) -> Optional[Dict[str, Any]]:
    """The Designer's copy (the [Workflows] row) — what the client edits."""
    import os
    from CommonUtils import get_db_connection  # type: ignore
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
        cur.execute("SELECT workflow_data FROM Workflows WHERE workflow_name = ?", (name,))
        row = cur.fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        return None
    data = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    return data if isinstance(data, dict) else None


def _load_existing_workflow(name: str, path: Path) -> Optional[Dict[str, Any]]:
    """Existing workflow by name: the DB row first, else the file."""
    try:
        doc = _load_existing_workflow_db(name)
        if doc is not None:
            return doc
    except Exception as e:  # noqa: BLE001
        logger.warning("Solution import: could not read existing workflow %r from DB: %s", name, e)
    try:
        if path.is_file():
            doc = json.loads(path.read_text(encoding="utf-8"))
            return doc if isinstance(doc, dict) else None
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Solution import: could not read existing workflow file %s: %s", path, e)
    return None


# ────────────────────────────────────────────────────────────────
# Export: GET /api/solutions/workflows/export/<name>
# ────────────────────────────────────────────────────────────────

@workflow_export_bp.route("/api/solutions/workflows/export/<path:name>", methods=["GET"])
@login_required
def export_workflow(name: str):
    _require_flag()
    p = _safe_workflow_path(name)
    if p is None or not p.is_file():
        return jsonify({"error": "workflow not found"}), 404
    try:
        data = p.read_text(encoding="utf-8")
        # Confirm it's valid JSON so the bundler gets clean output
        parsed = json.loads(data)
    except (OSError, json.JSONDecodeError) as e:
        return jsonify({"error": f"could not read workflow: {e}"}), 500
    return jsonify({"name": p.stem, "workflow": parsed})


# ────────────────────────────────────────────────────────────────
# List: GET /api/solutions/workflows/list
# ────────────────────────────────────────────────────────────────

@workflow_export_bp.route("/api/solutions/workflows/list", methods=["GET"])
@login_required
def list_workflows():
    _require_flag()
    root = _workflows_root()
    if not root.exists():
        return jsonify({"workflows": []})
    names = []
    for entry in sorted(root.iterdir()):
        if entry.is_file():
            stem = entry.stem if entry.suffix == ".json" else entry.name
            names.append({"name": stem, "filename": entry.name})
    return jsonify({"workflows": names})


# ────────────────────────────────────────────────────────────────
# Import: POST /api/solutions/workflows/import
# Body: {"name": str, "workflow": <json>, "conflict_mode": "rename"|"overwrite"|"skip"}
# ────────────────────────────────────────────────────────────────

@workflow_export_bp.route("/api/solutions/workflows/import", methods=["POST"])
@login_required
def import_workflow():
    _require_flag()
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    workflow = data.get("workflow")
    conflict_mode = str(data.get("conflict_mode") or "rename").lower()

    if not name or not _NAME_RE.match(name):
        return jsonify({"error": "invalid workflow name"}), 400
    if not isinstance(workflow, (dict, list)):
        return jsonify({"error": "workflow payload must be object or array"}), 400

    root = _workflows_root()
    root.mkdir(parents=True, exist_ok=True)

    # Target filename: preserve the name verbatim (folder-style legacy OK)
    target = root / name
    if target.suffix.lower() != ".json":
        # Allow legacy no-extension files to coexist; we always write fresh
        # imports as <name>.json to avoid ambiguity.
        target = root / f"{name}.json"

    final_target = target
    if target.exists():
        if conflict_mode == "skip":
            return jsonify({"status": "skipped", "name": name}), 200
        if conflict_mode == "overwrite":
            final_target = target
        else:  # rename
            stem = target.stem
            suffix = target.suffix
            i = 2
            while True:
                candidate = root / f"{stem}_{i}{suffix}"
                if not candidate.exists():
                    final_target = candidate
                    break
                i += 1

    # Overwrite = upgrade: carry over the values this system set for
    # variables the new version still has (the client's folder paths etc.).
    overwritten = False
    preserved_variables: List[str] = []
    if conflict_mode == "overwrite":
        existing_doc = _load_existing_workflow(final_target.stem, final_target)
        if existing_doc is not None:
            overwritten = True
            preserved_variables = merge_preserved_variables(workflow, existing_doc)
    # Overwrite replaces a file the client already has — keep its bytes so a
    # failed DB save restores it instead of deleting it.
    previous_file_bytes = None
    if conflict_mode == "overwrite" and final_target.is_file():
        try:
            previous_file_bytes = final_target.read_bytes()
        except OSError:
            previous_file_bytes = None

    try:
        final_target.write_text(
            json.dumps(workflow, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        return jsonify({"error": f"could not write workflow: {e}"}), 500

    # ──────────────────────────────────────────────────────────────
    # Persist into the [Workflows] table so the Workflow Designer
    # actually lists this workflow.
    #
    # The Designer's list comes from `SQL_SELECT_WORKFLOWS` (a query
    # against [dbo].[Workflows]); a file under workflows/ alone is
    # invisible to it. The regular /save/workflow route writes both
    # the file AND the DB row — solution import was only doing the
    # file half, so imported workflows silently never showed up.
    #
    # Mirror the regular save path by calling save_workflow_to_database()
    # with the stem (no .json extension), exactly as /save/workflow does.
    # Late import to avoid circular dependency with app.py.
    # ──────────────────────────────────────────────────────────────
    db_workflow_id = None
    if isinstance(workflow, dict):
        try:
            from app import save_workflow_to_database  # type: ignore
            db_workflow_id = save_workflow_to_database(final_target.stem, workflow)
        except Exception as e:  # noqa: BLE001
            logger.error(
                "Solution import: wrote workflow file %s but DB save failed: %s",
                final_target.name, e,
            )
            # Roll back the file so the caller sees a clean failure rather
            # than a half-imported workflow that's invisible in the Designer
            # and blocks the next import attempt with a name conflict.
            try:
                if previous_file_bytes is not None:
                    final_target.write_bytes(previous_file_bytes)
                else:
                    final_target.unlink()
            except OSError:
                pass
            return jsonify({
                "error": f"could not save workflow to database: {e}",
            }), 500
    else:
        # Legacy bundles allowed list payloads; the Designer's schema
        # expects a dict (with nodes/connections). Log loudly so this
        # doesn't recur as a silent miss.
        logger.warning(
            "Solution import: workflow %s payload is %s, not dict — "
            "file written but skipped DB save (will not appear in Designer).",
            final_target.name, type(workflow).__name__,
        )

    logger.info(
        "Installed workflow %s → %s (db id=%s)",
        name, final_target.name, db_workflow_id,
    )
    return jsonify({
        "status": "installed",
        "name": final_target.stem,
        "filename": final_target.name,
        "workflow_id": db_workflow_id,
        "overwritten": overwritten,
        "preserved_variables": preserved_variables,
    }), 201
