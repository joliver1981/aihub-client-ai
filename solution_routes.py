"""
Solutions Gallery — consumer-facing routes.

Page routes:
  GET  /solutions                       — gallery tile grid
  GET  /solutions/install/<solution_id> — install wizard page

API routes:
  GET  /api/solutions/catalog           — list bundled + remote solutions
  GET  /api/solutions/<id>              — manifest + preview metadata
  GET  /api/solutions/<id>/preview/<path:asset_path>  — icon / screenshot bytes
  GET  /api/solutions/<id>/readme       — README markdown text
  POST /api/solutions/<id>/analyze      — validate bundle, return manifest + conflicts
  POST /api/solutions/<id>/install      — install a solution by id from the catalog
  POST /api/solutions/install_upload    — install an uploaded .zip

All routes are 404 when `solutions_enabled` is off.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import (
    Blueprint, abort, current_app, jsonify, render_template, request, send_file,
)
from flask_login import current_user, login_required

from solution_catalog import (
    CatalogEntry,
    get_bundle_path,
    list_all_solutions,
    read_bundle_manifest,
    read_bundle_preview_asset,
)
from solution_installer import (
    InstallOptions,
    SolutionInstaller,
    analyze_bundle,
)

logger = logging.getLogger(__name__)

solution_bp = Blueprint("solution_consumer", __name__)


def _require_flag():
    try:
        from feature_flags import is_feature_enabled  # type: ignore
        if not is_feature_enabled("solutions_enabled"):
            abort(404)
    except Exception:
        abort(404)


def _config_dirs() -> Dict[str, Path]:
    try:
        import config as cfg  # type: ignore
    except Exception:
        cfg = None
    def _d(attr: str, fallback: str) -> Path:
        return Path(getattr(cfg, attr, fallback)) if cfg else Path(fallback)
    return {
        "builtin": _d("SOLUTIONS_BUILTIN_DIR", "solutions_builtin"),
        "cache":   _d("SOLUTIONS_CACHE_DIR", "data/solutions_cache"),
        "staging": _d("SOLUTIONS_STAGING_DIR", "data/solutions_staging"),
    }


_STAGED_PREFIX = "staged_"


def _staged_entry(staging_id: str) -> Optional[CatalogEntry]:
    """Build a synthetic CatalogEntry for a previously-uploaded bundle that's
    sitting in the staging dir. Lets the install wizard (detail / analyze /
    install / preview routes) reuse the same code path tiles use."""
    dirs = _config_dirs()
    staging_dir: Path = dirs["staging"]
    # Only accept ids shaped like [A-Za-z0-9_-]{8,64} to prevent any traversal.
    import re as _re
    if not _re.match(r"^[A-Za-z0-9_\-]{8,64}$", staging_id or ""):
        return None
    bundle = staging_dir / f"{staging_id}.zip"
    if not bundle.is_file():
        return None
    manifest = read_bundle_manifest(bundle)
    if manifest is None:
        return None
    return CatalogEntry(
        id=f"{_STAGED_PREFIX}{staging_id}",
        name=manifest.name,
        version=manifest.version,
        description=manifest.description,
        vertical=manifest.vertical,
        tags=list(manifest.tags),
        author=manifest.author,
        source="bundled",
        local_path=str(bundle),
    )


def _catalog_url() -> str:
    try:
        import config as cfg  # type: ignore
        return str(getattr(cfg, "SOLUTIONS_CATALOG_URL", "") or "")
    except Exception:
        return ""


def _find_entry(solution_id: str) -> Optional[CatalogEntry]:
    # Staged uploads look like "staged_<id>" — they live in the staging dir,
    # not the catalog, so they can flow through the normal install wizard.
    if solution_id and solution_id.startswith(_STAGED_PREFIX):
        return _staged_entry(solution_id[len(_STAGED_PREFIX):])
    dirs = _config_dirs()
    for e in list_all_solutions(dirs["builtin"], _catalog_url(), dirs["cache"]):
        if e.id == solution_id:
            return e
    return None


def _auth_headers_from_request() -> Dict[str, str]:
    """Pick up the caller's auth so installer sub-requests satisfy the
    existing decorators on /import/agent/*, /api/tool/import, etc."""
    headers: Dict[str, str] = {}
    # Forward the session cookie for session-based auth.
    cookie = request.headers.get("Cookie")
    if cookie:
        headers["Cookie"] = cookie
    # Forward API key if provided.
    api_key = request.headers.get("X-API-Key")
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


# ════════════════════════════════════════════════════════════════
# Page routes
# ════════════════════════════════════════════════════════════════

@solution_bp.route("/solutions", methods=["GET"])
@login_required
def gallery_page():
    _require_flag()
    return render_template("solutions_gallery.html")


@solution_bp.route("/solutions/install/<solution_id>", methods=["GET"])
@login_required
def install_page(solution_id: str):
    _require_flag()
    entry = _find_entry(solution_id)
    if not entry:
        abort(404)
    return render_template("solutions_install_wizard.html", solution_id=solution_id)


# ════════════════════════════════════════════════════════════════
# API
# ════════════════════════════════════════════════════════════════

@solution_bp.route("/api/solutions/catalog", methods=["GET"])
@login_required
def api_catalog():
    _require_flag()
    dirs = _config_dirs()
    entries = list_all_solutions(dirs["builtin"], _catalog_url(), dirs["cache"])
    return jsonify({"solutions": [e.to_dict() for e in entries]})


@solution_bp.route("/api/solutions/<solution_id>", methods=["GET"])
@login_required
def api_solution_detail(solution_id: str):
    _require_flag()
    entry = _find_entry(solution_id)
    if not entry:
        return jsonify({"error": "not found"}), 404
    dirs = _config_dirs()
    bundle = get_bundle_path(entry, dirs["cache"])
    manifest = read_bundle_manifest(bundle) if bundle else None
    out: Dict[str, Any] = {"entry": entry.to_dict()}
    if manifest:
        out["manifest"] = manifest.to_dict()
    return jsonify(out)


@solution_bp.route("/api/solutions/<solution_id>/preview/<path:asset_path>", methods=["GET"])
@login_required
def api_preview_asset(solution_id: str, asset_path: str):
    _require_flag()
    entry = _find_entry(solution_id)
    if not entry:
        abort(404)
    dirs = _config_dirs()
    bundle = get_bundle_path(entry, dirs["cache"])
    if not bundle:
        abort(404)
    data = read_bundle_preview_asset(Path(bundle), f"preview/{asset_path}")
    if data is None:
        abort(404)
    # Best-effort MIME guess
    import mimetypes
    mime, _ = mimetypes.guess_type(asset_path)
    from io import BytesIO
    return send_file(BytesIO(data), mimetype=mime or "application/octet-stream")


@solution_bp.route("/api/solutions/<solution_id>/readme", methods=["GET"])
@login_required
def api_readme(solution_id: str):
    _require_flag()
    entry = _find_entry(solution_id)
    if not entry:
        abort(404)
    dirs = _config_dirs()
    bundle = get_bundle_path(entry, dirs["cache"])
    if not bundle:
        abort(404)
    data = read_bundle_preview_asset(Path(bundle), "README.md")
    text = data.decode("utf-8", errors="replace") if data else ""
    return jsonify({"readme": text})


@solution_bp.route("/api/solutions/<solution_id>/analyze", methods=["POST"])
@login_required
def api_analyze(solution_id: str):
    _require_flag()
    entry = _find_entry(solution_id)
    if not entry:
        return jsonify({"error": "not found"}), 404
    dirs = _config_dirs()
    bundle = get_bundle_path(entry, dirs["cache"])
    if not bundle:
        return jsonify({"error": "bundle unavailable"}), 500
    return jsonify(analyze_bundle(
        Path(bundle),
        flask_app=current_app,
        auth_headers=_auth_headers_from_request(),
    ))


@solution_bp.route("/api/solutions/<solution_id>/install", methods=["POST"])
@login_required
def api_install(solution_id: str):
    _require_flag()
    entry = _find_entry(solution_id)
    if not entry:
        return jsonify({"error": "not found"}), 404
    dirs = _config_dirs()
    bundle = get_bundle_path(entry, dirs["cache"])
    if not bundle:
        return jsonify({"error": "bundle unavailable"}), 500

    body = request.get_json(silent=True) or {}
    options = InstallOptions(
        credentials={str(k): str(v) for k, v in (body.get("credentials") or {}).items()},
        target_connection=body.get("target_connection"),
        name_suffix=str(body.get("name_suffix") or ""),
        conflict_mode=str(body.get("conflict_mode") or "rename"),
    )

    installer = SolutionInstaller(current_app)
    result = installer.install(
        Path(bundle),
        options=options,
        auth_headers=_auth_headers_from_request(),
    )
    return jsonify(result.to_dict()), (200 if result.success else 207)


@solution_bp.route("/api/solutions/upload_stage", methods=["POST"])
@login_required
def api_upload_stage():
    """Save an uploaded .zip into the staging dir so it can flow through the
    normal install wizard (preview → credentials → install → post-install).

    The upload button on the gallery used to POST directly to install_upload
    with an empty credentials dict, which fails for any bundle that declares
    required credentials. This staging step lets the wizard collect them the
    same way it does for catalog tiles."""
    _require_flag()
    f = request.files.get("file")
    if f is None:
        return jsonify({"error": "file is required"}), 400

    import uuid
    staging_id = uuid.uuid4().hex  # 32 chars, safe for URL
    dirs = _config_dirs()
    staging_dir: Path = dirs["staging"]
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged_path = staging_dir / f"{staging_id}.zip"
    f.save(str(staged_path))

    # Validate the bundle is parseable before we redirect — no point sending
    # the user into a wizard that'll 404.
    entry = _staged_entry(staging_id)
    if entry is None:
        try:
            staged_path.unlink()
        except OSError:
            pass
        return jsonify({"error": "upload is not a valid solution bundle"}), 400

    return jsonify({
        "staging_id": staging_id,
        "solution_id": f"{_STAGED_PREFIX}{staging_id}",
        "install_url": f"/solutions/install/{_STAGED_PREFIX}{staging_id}",
    })


@solution_bp.route("/api/solutions/install_upload", methods=["POST"])
@login_required
def api_install_upload():
    _require_flag()
    f = request.files.get("file")
    if f is None:
        return jsonify({"error": "file is required"}), 400

    body_cred = request.form.get("credentials") or "{}"
    try:
        credentials = json.loads(body_cred)
        if not isinstance(credentials, dict):
            credentials = {}
    except json.JSONDecodeError:
        credentials = {}

    suffix = request.form.get("name_suffix") or ""
    conflict = request.form.get("conflict_mode") or "rename"

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        f.save(tmp.name)
        tmp_path = Path(tmp.name)

    try:
        options = InstallOptions(
            credentials={str(k): str(v) for k, v in credentials.items()},
            name_suffix=str(suffix),
            conflict_mode=str(conflict),
        )
        installer = SolutionInstaller(current_app)
        result = installer.install(
            tmp_path,
            options=options,
            auth_headers=_auth_headers_from_request(),
        )
        return jsonify(result.to_dict()), (200 if result.success else 207)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
