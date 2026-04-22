"""
Solutions Gallery — knowledge document install wrapper.

Thin wrapper around the existing document-upload path so the Solutions
installer can stage seed knowledge without touching any existing route.
Gated behind `solutions_enabled`.

For the MVP this writes the document bytes into the same location the
platform's document store reads from. A future iteration can route
through the full `/document/upload` endpoint including OCR / vector
indexing.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict

from flask import Blueprint, abort, jsonify, request
from flask_login import login_required

logger = logging.getLogger(__name__)

knowledge_install_bp = Blueprint("solution_knowledge_install", __name__)

_NAME_RE = re.compile(r"^[A-Za-z0-9 _()\-\.]{1,200}$")


def _require_flag():
    try:
        from feature_flags import is_feature_enabled  # type: ignore
        if not is_feature_enabled("solutions_enabled"):
            abort(404)
    except Exception:
        abort(404)


def _knowledge_root() -> Path:
    try:
        import config as cfg  # type: ignore
        return Path(getattr(cfg, "APP_ROOT", ".")) / "data" / "solutions_knowledge"
    except Exception:
        return Path("data/solutions_knowledge")


@knowledge_install_bp.route("/api/solutions/knowledge/import", methods=["POST"])
@login_required
def import_knowledge():
    _require_flag()
    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify({"error": "file is required"}), 400
    if not _NAME_RE.match(f.filename):
        return jsonify({"error": "unsafe filename"}), 400

    meta_raw = request.form.get("metadata") or "{}"
    try:
        meta = json.loads(meta_raw)
        if not isinstance(meta, dict):
            meta = {}
    except json.JSONDecodeError:
        meta = {}

    root = _knowledge_root()
    root.mkdir(parents=True, exist_ok=True)
    target = root / f.filename
    # Rename-on-conflict so re-installs don't overwrite
    final = target
    i = 2
    while final.exists():
        final = root / f"{target.stem}_{i}{target.suffix}"
        i += 1

    try:
        f.save(str(final))
        if meta:
            (final.with_suffix(final.suffix + ".meta.json")).write_text(
                json.dumps(meta, indent=2), encoding="utf-8"
            )
    except OSError as e:
        return jsonify({"error": f"could not write knowledge file: {e}"}), 500

    logger.info("Installed knowledge doc → %s", final)
    return jsonify({
        "status": "installed",
        "filename": final.name,
        "path": str(final),
    }), 201
