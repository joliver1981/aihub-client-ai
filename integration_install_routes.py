"""
Solutions Gallery — integration template install / export routes.

Integration templates (Stripe, Shopify, Walmart, etc.) live as JSON
files in `integrations/builtin/`. These new endpoints let the Solutions
installer drop a fully-resolved (credentials-substituted) integration
template into a tenant-specific configuration, without modifying the
shipped read-only builtin templates.

Gated behind the `solutions_enabled` flag.

NOTE: In the current platform, tenant-specific integration configurations
live in the `integrations/builtin/` directory OR are read at runtime by
the integration registry. For the MVP we persist installed integration
configs alongside the builtin ones (with a sidecar `.installed` marker),
so the existing integrations loader continues to pick them up without
any code change. A future iteration can route these into a per-tenant
table / folder.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Blueprint, abort, jsonify, request
from flask_login import login_required

logger = logging.getLogger(__name__)

integration_install_bp = Blueprint("solution_integration_install", __name__)

_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\.]{1,80}$")


def _require_flag():
    try:
        from feature_flags import is_feature_enabled  # type: ignore
        if not is_feature_enabled("solutions_enabled"):
            abort(404)
    except Exception:
        abort(404)


def _integrations_root() -> Path:
    try:
        import config as cfg  # type: ignore
        return Path(getattr(cfg, "APP_ROOT", ".")) / "integrations" / "builtin"
    except Exception:
        return Path("integrations/builtin")


# ────────────────────────────────────────────────────────────────
# List installed + builtin integrations
# GET /api/solutions/integrations/list
# ────────────────────────────────────────────────────────────────

@integration_install_bp.route("/api/solutions/integrations/list", methods=["GET"])
@login_required
def list_integrations():
    _require_flag()
    root = _integrations_root()
    if not root.exists():
        return jsonify({"integrations": []})
    items = []
    for p in sorted(root.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items.append({
            "name": p.stem,
            "filename": p.name,
            "display_name": data.get("name") or p.stem,
            "builtin": not (root / f"{p.stem}.installed").exists(),
        })
    return jsonify({"integrations": items})


# ────────────────────────────────────────────────────────────────
# Export a single template (verbatim JSON, no credential resolution)
# GET /api/solutions/integrations/export/<name>
# ────────────────────────────────────────────────────────────────

@integration_install_bp.route("/api/solutions/integrations/export/<string:name>", methods=["GET"])
@login_required
def export_integration(name: str):
    _require_flag()
    if not _NAME_RE.match(name or ""):
        return jsonify({"error": "invalid integration name"}), 400
    root = _integrations_root()
    p = (root / f"{name}.json").resolve()
    try:
        p.relative_to(root.resolve())
    except ValueError:
        return jsonify({"error": "path traversal rejected"}), 400
    if not p.is_file():
        return jsonify({"error": "integration not found"}), 404
    try:
        return jsonify(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as e:
        return jsonify({"error": f"could not read integration: {e}"}), 500


# ────────────────────────────────────────────────────────────────
# Install a resolved integration config
# POST /api/solutions/integrations/install
# Body: {"name": str, "config": <json>, "conflict_mode": ...}
# ────────────────────────────────────────────────────────────────

@integration_install_bp.route("/api/solutions/integrations/install", methods=["POST"])
@login_required
def install_integration():
    _require_flag()
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    config = data.get("config")
    conflict_mode = str(data.get("conflict_mode") or "rename").lower()

    if not name or not _NAME_RE.match(name):
        return jsonify({"error": "invalid integration name"}), 400
    if not isinstance(config, dict):
        return jsonify({"error": "config must be an object"}), 400

    root = _integrations_root()
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{name}.json"

    final_target = target
    if target.exists():
        if conflict_mode == "skip":
            return jsonify({"status": "skipped", "name": name}), 200
        if conflict_mode == "overwrite":
            final_target = target
        else:
            i = 2
            while True:
                candidate = root / f"{name}_{i}.json"
                if not candidate.exists():
                    final_target = candidate
                    break
                i += 1

    try:
        final_target.write_text(json.dumps(config, indent=2), encoding="utf-8")
        # Sidecar marker so list_integrations() can distinguish installed
        # from read-only builtin templates.
        (root / f"{final_target.stem}.installed").write_text("", encoding="utf-8")
    except OSError as e:
        return jsonify({"error": f"could not write integration: {e}"}), 500

    logger.info("Installed integration %s → %s", name, final_target.name)
    return jsonify({
        "status": "installed",
        "name": final_target.stem,
        "filename": final_target.name,
    }), 201
