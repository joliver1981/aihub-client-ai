"""
Solutions Gallery — author (creator) routes.

Produces solution `.zip` bundles from existing tenant content. This is the
creator side of the gallery: the consumer side lives in `solution_routes.py`.

Page routes:
  GET  /solutions/author                 — list drafts + published bundles
  GET  /solutions/author/new             — multi-step build wizard
  GET  /solutions/author/edit/<draft_id> — edit an existing draft

Draft persistence (JSON files under SOLUTIONS_DRAFTS_DIR):
  GET    /api/solutions/drafts
  GET    /api/solutions/drafts/<draft_id>
  POST   /api/solutions/drafts
  PUT    /api/solutions/drafts/<draft_id>
  DELETE /api/solutions/drafts/<draft_id>

Asset pickers (feeds the wizard's "Pick Assets" step):
  GET /api/solutions/author/assets

Build / validate / test-install:
  POST /api/solutions/validate       — run manifest.validate() on a payload
  POST /api/solutions/build          — stream a .zip download
  POST /api/solutions/build/publish  — write .zip to SOLUTIONS_BUILTIN_DIR
  POST /api/solutions/test_install   — build then install into current tenant

All routes are 404 when `solutions_enabled` is off. Author routes additionally
require developer-or-above role.
"""

from __future__ import annotations

import io
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import (
    Blueprint, abort, current_app, jsonify, render_template, request, send_file,
)
from flask_login import login_required

from solution_bundler import SolutionBundler
from solution_catalog import list_bundled_solutions
from solution_installer import InstallOptions, SolutionInstaller
from solution_manifest import (
    CredentialPrompt,
    PostInstallAction,
    SolutionManifest,
    extract_placeholders,
)

logger = logging.getLogger(__name__)

solution_builder_bp = Blueprint("solution_author", __name__)


# ════════════════════════════════════════════════════════════════
# Gating
# ════════════════════════════════════════════════════════════════

def _require_flag():
    try:
        from feature_flags import is_feature_enabled  # type: ignore
        if not is_feature_enabled("solutions_enabled"):
            abort(404)
    except Exception:
        abort(404)


try:
    from role_decorators import developer_required  # type: ignore
except Exception:  # pragma: no cover — role module optional during isolated tests
    def developer_required(api: bool = False):  # type: ignore
        def _wrap(fn):
            return fn
        return _wrap


# ════════════════════════════════════════════════════════════════
# Paths / config
# ════════════════════════════════════════════════════════════════

def _cfg_path(attr: str, fallback: str) -> Path:
    try:
        import config as cfg  # type: ignore
        return Path(getattr(cfg, attr, fallback))
    except Exception:
        return Path(fallback)


def _drafts_dir() -> Path:
    d = _cfg_path("SOLUTIONS_DRAFTS_DIR", "data/solutions_drafts")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _builtin_dir() -> Path:
    return _cfg_path("SOLUTIONS_BUILTIN_DIR", "solutions_builtin")


def _auth_headers_from_request() -> Dict[str, str]:
    headers: Dict[str, str] = {}
    cookie = request.headers.get("Cookie")
    if cookie:
        headers["Cookie"] = cookie
    api_key = request.headers.get("X-API-Key")
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


# ════════════════════════════════════════════════════════════════
# Page routes
# ════════════════════════════════════════════════════════════════

@solution_builder_bp.route("/solutions/author", methods=["GET"])
@login_required
@developer_required()
def author_list_page():
    _require_flag()
    return render_template("solutions_author_list.html")


@solution_builder_bp.route("/solutions/author/new", methods=["GET"])
@login_required
@developer_required()
def author_new_page():
    _require_flag()
    return render_template("solutions_author_wizard.html", draft_id="")


@solution_builder_bp.route("/solutions/author/edit/<draft_id>", methods=["GET"])
@login_required
@developer_required()
def author_edit_page(draft_id: str):
    _require_flag()
    if not _is_valid_draft_id(draft_id):
        abort(404)
    return render_template("solutions_author_wizard.html", draft_id=draft_id)


# ════════════════════════════════════════════════════════════════
# Drafts CRUD
# ════════════════════════════════════════════════════════════════

_DRAFT_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _is_valid_draft_id(draft_id: str) -> bool:
    return bool(_DRAFT_ID_RE.match(draft_id or ""))


def _draft_path(draft_id: str) -> Path:
    return _drafts_dir() / f"{draft_id}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_draft(draft_id: str) -> Optional[Dict[str, Any]]:
    p = _draft_path(draft_id)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not read draft %s: %s", draft_id, e)
        return None


@solution_builder_bp.route("/api/solutions/drafts", methods=["GET"])
@login_required
@developer_required(api=True)
def list_drafts():
    _require_flag()
    out: List[Dict[str, Any]] = []
    for p in sorted(_drafts_dir().glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        manifest = data.get("manifest") or {}
        out.append({
            "draft_id": p.stem,
            "id": manifest.get("id") or "",
            "name": manifest.get("name") or "",
            "version": manifest.get("version") or "",
            "updated_at": data.get("updated_at") or "",
        })
    return jsonify({"drafts": out})


@solution_builder_bp.route("/api/solutions/drafts/<draft_id>", methods=["GET"])
@login_required
@developer_required(api=True)
def get_draft(draft_id: str):
    _require_flag()
    if not _is_valid_draft_id(draft_id):
        return jsonify({"error": "invalid draft_id"}), 400
    data = _read_draft(draft_id)
    if data is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(data)


@solution_builder_bp.route("/api/solutions/drafts", methods=["POST"])
@login_required
@developer_required(api=True)
def create_draft():
    _require_flag()
    body = request.get_json(silent=True) or {}
    draft_id = uuid.uuid4().hex[:16]
    doc = {
        "draft_id": draft_id,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "manifest": body.get("manifest") or {},
        "selections": body.get("selections") or {},
        "branding": body.get("branding") or {},
        "readme": body.get("readme") or "",
    }
    _draft_path(draft_id).write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return jsonify(doc), 201


@solution_builder_bp.route("/api/solutions/drafts/<draft_id>", methods=["PUT"])
@login_required
@developer_required(api=True)
def update_draft(draft_id: str):
    _require_flag()
    if not _is_valid_draft_id(draft_id):
        return jsonify({"error": "invalid draft_id"}), 400
    existing = _read_draft(draft_id)
    if existing is None:
        return jsonify({"error": "not found"}), 404
    body = request.get_json(silent=True) or {}
    for k in ("manifest", "selections", "branding", "readme"):
        if k in body:
            existing[k] = body[k]
    existing["updated_at"] = _now_iso()
    _draft_path(draft_id).write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return jsonify(existing)


@solution_builder_bp.route("/api/solutions/drafts/<draft_id>", methods=["DELETE"])
@login_required
@developer_required(api=True)
def delete_draft(draft_id: str):
    _require_flag()
    if not _is_valid_draft_id(draft_id):
        return jsonify({"error": "invalid draft_id"}), 400
    p = _draft_path(draft_id)
    if not p.is_file():
        return jsonify({"error": "not found"}), 404
    try:
        p.unlink()
    except OSError as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"status": "deleted", "draft_id": draft_id})


# ════════════════════════════════════════════════════════════════
# Asset picker — feeds the wizard
# ════════════════════════════════════════════════════════════════

@solution_builder_bp.route("/api/solutions/author/assets", methods=["GET"])
@login_required
@developer_required(api=True)
def list_available_assets():
    """Return the assets in the current tenant that can be bundled. Each list
    entry is `{id|name, display, tooltip?}` so the wizard can render a picker."""
    _require_flag()

    return jsonify({
        "agents":       _safe_list(_list_agents),
        "data_agents":  _safe_list(_list_data_agents),
        "tools":        _safe_list(_list_tools),
        "workflows":    _safe_list(_list_workflows),
        "integrations": _safe_list(_list_integrations),
        "connections": _safe_list(_list_connections),
        "environments": _safe_list(_list_environments),
        "knowledge":    _safe_list(_list_knowledge),
    })


def _safe_list(fn) -> List[Dict[str, Any]]:
    try:
        return fn() or []
    except Exception as e:
        logger.warning("asset picker %s failed: %s", fn.__name__, e)
        return []


def _agent_knowledge_map() -> Dict[int, List[int]]:
    """Per-agent list of attached document_ids, for dependency expansion in
    the wizard. One SQL query, tenant-scoped."""
    try:
        from DataUtils import _execute_sql  # type: ignore
    except ImportError:
        return {}
    try:
        df = _execute_sql(
            "SELECT agent_id, document_id FROM AgentKnowledge "
            "WHERE is_active = 1"
        )
    except Exception as e:
        logger.warning("agent knowledge map query failed: %s", e)
        return {}
    if df is None:
        return {}
    out: Dict[int, List[int]] = {}
    try:
        rows = df.to_dict("records") if hasattr(df, "to_dict") else list(df)
    except Exception:
        return {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        aid = r.get("agent_id")
        did = r.get("document_id")
        if aid is None or did is None:
            continue
        try:
            out.setdefault(int(aid), []).append(int(did))
        except (TypeError, ValueError):
            continue
    return out


def _list_agents() -> List[Dict[str, Any]]:
    """Custom (non-data) agents. Schema: {id, description, objective, enabled}.
    Use description as the label; objective as hover tooltip. Includes
    dependency info (custom tools + knowledge docs) so the wizard can cascade-
    select them when the agent is picked."""
    try:
        from DataUtils import select_all_agents_and_tools  # type: ignore
    except ImportError:
        return []
    rows = select_all_agents_and_tools() or []
    knowledge_map = _agent_knowledge_map()
    out: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        aid = r.get("agent_id") or r.get("id")
        desc = (r.get("agent_description") or r.get("description") or "").strip()
        obj = (r.get("agent_objective") or r.get("objective") or "").strip()
        if aid is None:
            continue
        aid_int = int(aid)
        # Only include *custom* tool names as dependencies — core tools
        # ship with the platform and don't need to be bundled.
        tool_names = r.get("tool_names") or []
        is_custom = r.get("custom_tool") or []
        custom_tools: List[str] = []
        for i, tname in enumerate(tool_names):
            if i < len(is_custom) and is_custom[i]:
                if tname:
                    custom_tools.append(str(tname))
        out.append({
            "id": aid_int,
            "display": desc or f"Agent {aid_int}",
            "tooltip": obj,
            "deps": {
                "tool_names": custom_tools,
                "knowledge_document_ids": knowledge_map.get(aid_int, []),
            },
        })
    return out


def _list_data_agents() -> List[Dict[str, Any]]:
    """Data agents (is_data_agent = 1). Separate picker group. Each row
    includes the connection_ids it uses so the wizard can auto-select them."""
    try:
        from DataUtils import select_all_agents_and_connections  # type: ignore
    except ImportError:
        return []
    df = select_all_agents_and_connections()
    if df is None:
        return []
    try:
        rows = df.to_dict("records") if hasattr(df, "to_dict") else list(df)
    except Exception:
        return []
    # Group connection ids by agent
    by_agent: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        aid = r.get("agent_id") or r.get("id")
        if aid is None:
            continue
        aid_int = int(aid)
        if aid_int not in by_agent:
            by_agent[aid_int] = {
                "id": aid_int,
                "display": (r.get("agent_description") or "").strip() or f"Data Agent {aid_int}",
                "tooltip": (r.get("agent_objective") or "").strip(),
                "deps": {"connection_ids": []},
            }
        cid = r.get("connection_id")
        if cid is not None:
            try:
                cid_int = int(cid)
                if cid_int not in by_agent[aid_int]["deps"]["connection_ids"]:
                    by_agent[aid_int]["deps"]["connection_ids"].append(cid_int)
            except (TypeError, ValueError):
                pass
    return list(by_agent.values())


def _get_via_test_client(path: str) -> Any:
    """Invoke an existing route via Flask's test client, forwarding the
    caller's session cookie so tenant context is preserved. Returns parsed
    JSON (may be list, dict, or None on error)."""
    try:
        cookie = request.headers.get("Cookie", "")
        with current_app.test_client() as client:
            resp = client.get(path, headers={"Cookie": cookie} if cookie else {})
            if resp.status_code != 200:
                return None
            try:
                data = resp.get_json()
            except Exception:
                data = None
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    return None
            return data
    except Exception as e:
        logger.warning("test_client GET %s failed: %s", path, e)
        return None


def _list_tools() -> List[Dict[str, Any]]:
    """Custom tools live as folders under tools/, each containing
    config.json + function.py (or code.py). Use config.json description as
    the hover tooltip."""
    root = _cfg_path("APP_ROOT", ".") / "tools"
    if not root.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        cfg_path = child / "config.json"
        if not (cfg_path.is_file() or (child / "function.py").is_file()):
            continue
        tooltip = ""
        if cfg_path.is_file():
            try:
                cfg_data = json.loads(cfg_path.read_text(encoding="utf-8"))
                tooltip = str(cfg_data.get("description") or cfg_data.get("tool_description") or "").strip()
            except (OSError, json.JSONDecodeError):
                pass
        out.append({"name": child.name, "display": child.name, "tooltip": tooltip})
    return out


_WORKFLOW_AGENT_RE = re.compile(
    r'"agent_id"\s*:\s*"?(\d+)"?'
)


def _list_workflows() -> List[Dict[str, Any]]:
    """Workflows live in workflows/. Every regular file is a workflow — some
    have .json, some don't. We include them all, and scan each file for
    `agent_id` references so the wizard can cascade-select referenced
    agents when the workflow is picked."""
    root = _cfg_path("APP_ROOT", ".") / "workflows"
    if not root.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for p in sorted(root.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        name = p.name
        display = p.stem if name.lower().endswith(".json") else name

        agent_ids: List[int] = []
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
            for m in _WORKFLOW_AGENT_RE.finditer(text):
                try:
                    aid = int(m.group(1))
                    if aid not in agent_ids:
                        agent_ids.append(aid)
                except (TypeError, ValueError):
                    continue
        except OSError:
            pass

        out.append({
            "name": name,
            "display": display,
            "deps": {"agent_ids": agent_ids} if agent_ids else {},
        })
    return out


def _list_integrations() -> List[Dict[str, Any]]:
    """Configured integration instances in this tenant (not templates).
    Pulls from the /api/integrations route via the test client. The real
    schema is (integration_id, integration_name, description, platform_name,
    template_key)."""
    data = _get_via_test_client("/api/integrations")
    items = (data or {}).get("integrations") if isinstance(data, dict) else data
    out: List[Dict[str, Any]] = []
    for r in (items or []):
        if not isinstance(r, dict):
            continue
        iid = r.get("integration_id") or r.get("id") or r.get("instance_id")
        name = (r.get("integration_name") or r.get("instance_name") or r.get("name") or "").strip()
        desc = (r.get("description") or "").strip()
        platform = (r.get("platform_name") or r.get("template_key") or "").strip()
        if iid is None and not name:
            continue
        display = name or f"Integration {iid}"
        tooltip_bits = [b for b in [platform, desc] if b]
        out.append({
            "id": iid,
            "name": name or str(iid),
            "display": display,
            "tooltip": " · ".join(tooltip_bits),
        })
    return out


def _list_connections() -> List[Dict[str, Any]]:
    """Call existing /get/connections via test_client (forwards session).
    Shows database type + server as hover tooltip."""
    data = _get_via_test_client("/get/connections")
    if isinstance(data, dict):
        data = data.get("data") or data.get("connections") or []
    out: List[Dict[str, Any]] = []
    for r in (data or []):
        if not isinstance(r, dict):
            continue
        cid = r.get("connection_id") or r.get("id")
        name = (r.get("connection_name") or r.get("name") or "").strip()
        if cid is None:
            continue
        dbtype = (r.get("database_type") or r.get("type") or "").strip()
        server = (r.get("server") or "").strip()
        dbname = (r.get("database_name") or r.get("database") or "").strip()
        tooltip_bits = [b for b in [dbtype, server, dbname] if b]
        out.append({
            "id": int(cid),
            "display": name or f"Connection {cid}",
            "tooltip": " · ".join(tooltip_bits),
        })
    return out


def _list_environments() -> List[Dict[str, Any]]:
    """Agent environments — /environments/api/list. Returns
    {status, environments: [...]}"""
    data = _get_via_test_client("/environments/api/list")
    items = (data or {}).get("environments") if isinstance(data, dict) else data
    out: List[Dict[str, Any]] = []
    for r in (items or []):
        if not isinstance(r, dict):
            continue
        # Env id may be a string UUID or int. Keep it as-is.
        eid = r.get("environment_id") or r.get("id")
        name = (r.get("name") or r.get("display_name") or "").strip()
        if eid is None and not name:
            continue
        tooltip = (r.get("description") or r.get("python_version") or "").strip()
        out.append({
            "id": eid,
            "display": name or f"Environment {eid}",
            "tooltip": tooltip,
        })
    return out


def _list_knowledge() -> List[Dict[str, Any]]:
    """Knowledge documents — Documents table rows with is_knowledge_document=1.
    Use a direct tenant-aware SQL query (no list endpoint exists)."""
    try:
        from DataUtils import _execute_sql  # type: ignore
    except ImportError:
        return []
    query = """
        SELECT TOP 500 document_id, filename, document_type, document_date
        FROM Documents
        WHERE is_knowledge_document = 1
        ORDER BY document_date DESC, filename
    """
    try:
        df = _execute_sql(query)
    except Exception as e:
        logger.warning("knowledge query failed: %s", e)
        return []
    if df is None:
        return []
    try:
        rows = df.to_dict("records") if hasattr(df, "to_dict") else list(df)
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        did = r.get("document_id")
        fn = (r.get("filename") or "").strip()
        if did is None and not fn:
            continue
        tooltip_bits = []
        if r.get("document_type"):
            tooltip_bits.append(str(r.get("document_type")))
        if r.get("document_date"):
            tooltip_bits.append(str(r.get("document_date"))[:10])
        out.append({
            "id": did,
            "display": fn or f"Document {did}",
            "tooltip": " · ".join(tooltip_bits),
        })
    return out


# ════════════════════════════════════════════════════════════════
# Validate / build / test-install
# ════════════════════════════════════════════════════════════════

@solution_builder_bp.route("/api/solutions/validate", methods=["POST"])
@login_required
@developer_required(api=True)
def validate_manifest():
    _require_flag()
    body = request.get_json(silent=True) or {}
    try:
        m = SolutionManifest.from_dict(body.get("manifest") or {})
    except Exception as e:
        return jsonify({"valid": False, "errors": [f"parse: {e}"]}), 200
    errors = m.validate()
    # Also surface discovered placeholders from declared credentials vs. any
    # free-form text the wizard sent.
    scan_text = body.get("scan_text") or ""
    discovered = extract_placeholders(scan_text) if scan_text else []
    return jsonify({
        "valid": not errors,
        "errors": errors,
        "discovered_placeholders": discovered,
    })


def _build_zip_from_request_body() -> bytes:
    """Common path for /build and /build/publish and /test_install. Reads
    the JSON body, converts to a SolutionManifest + selections, runs the
    bundler, returns the zip bytes."""
    body = request.get_json(silent=True) or {}
    manifest_raw = body.get("manifest") or {}
    selections = body.get("selections") or {}
    branding = body.get("branding") or None
    readme = body.get("readme") or ""
    preview_files_b64 = body.get("preview_files") or {}
    seed_schema_sql_b64 = body.get("seed_schema_sql") or ""
    seed_csvs_b64 = body.get("seed_csvs") or {}
    sample_inputs_b64 = body.get("sample_inputs") or {}

    manifest = SolutionManifest.from_dict(manifest_raw)

    # base64 decode file maps (the wizard uploads via JSON; smaller for MVP)
    import base64
    def _decode(mapping: Dict[str, str]) -> Dict[str, bytes]:
        out: Dict[str, bytes] = {}
        for k, v in (mapping or {}).items():
            if not isinstance(v, str):
                continue
            try:
                out[str(k)] = base64.b64decode(v)
            except (ValueError, TypeError):
                continue
        return out

    preview_files = _decode(preview_files_b64)
    seed_csvs = _decode(seed_csvs_b64)
    sample_inputs = _decode(sample_inputs_b64)
    seed_schema = None
    if seed_schema_sql_b64:
        try:
            import base64 as _b64
            seed_schema = _b64.b64decode(seed_schema_sql_b64)
        except (ValueError, TypeError):
            seed_schema = None

    # Agent export route handles both custom and data agents; combine them
    # for the bundler.
    all_agent_ids = []
    for x in (selections.get("agent_ids") or []):
        try: all_agent_ids.append(int(x))
        except (TypeError, ValueError): pass
    for x in (selections.get("data_agent_ids") or []):
        try: all_agent_ids.append(int(x))
        except (TypeError, ValueError): pass

    # Integrations are selected by id; resolve to names for the bundler.
    integration_ids = [x for x in (selections.get("integration_ids") or []) if x is not None]
    integration_names = _resolve_integration_names(integration_ids)

    bundler = SolutionBundler(current_app)
    return bundler.build(
        manifest,
        auth_headers=_auth_headers_from_request(),
        agent_ids=all_agent_ids,
        tool_names=[str(x) for x in (selections.get("tool_names") or [])],
        workflow_names=[str(x) for x in (selections.get("workflow_names") or [])],
        integration_names=integration_names,
        connection_ids=[int(x) for x in (selections.get("connection_ids") or []) if str(x).lstrip("-").isdigit()],
        environment_ids=[x for x in (selections.get("environment_ids") or []) if x is not None],
        knowledge_document_ids=[int(x) for x in (selections.get("knowledge_document_ids") or []) if str(x).lstrip("-").isdigit()],
        seed_schema_sql=seed_schema,
        seed_csvs=seed_csvs,
        sample_input_files=sample_inputs,
        branding=branding,
        readme_md=readme,
        preview_files=preview_files,
    )


def _resolve_integration_names(ids: List[Any]) -> List[str]:
    """Look up configured integration instance names by id."""
    if not ids:
        return []
    data = _get_via_test_client("/api/integrations")
    items = (data or {}).get("integrations") if isinstance(data, dict) else data
    by_id: Dict[str, str] = {}
    for r in (items or []):
        if not isinstance(r, dict):
            continue
        iid = r.get("integration_id") or r.get("id") or r.get("instance_id")
        name = r.get("instance_name") or r.get("name") or r.get("display_name")
        if iid is not None and name:
            by_id[str(iid)] = str(name)
    return [by_id[str(i)] for i in ids if str(i) in by_id]


@solution_builder_bp.route("/api/solutions/build", methods=["POST"])
@login_required
@developer_required(api=True)
def build_bundle():
    """Build a bundle and return it as a downloadable .zip."""
    _require_flag()
    try:
        zip_bytes = _build_zip_from_request_body()
    except Exception as e:
        logger.exception("build failed")
        return jsonify({"error": str(e)}), 500

    # Use the manifest id in the filename.
    body = request.get_json(silent=True) or {}
    m_id = str((body.get("manifest") or {}).get("id") or "solution").strip() or "solution"
    m_version = str((body.get("manifest") or {}).get("version") or "1.0.0").strip()
    safe = re.sub(r"[^A-Za-z0-9._\-]+", "_", f"{m_id}_v{m_version}.zip")

    return send_file(
        io.BytesIO(zip_bytes),
        mimetype="application/zip",
        as_attachment=True,
        download_name=safe,
    )


@solution_builder_bp.route("/api/solutions/build/publish", methods=["POST"])
@login_required
@developer_required(api=True)
def build_and_publish():
    """Build and also write the zip to SOLUTIONS_BUILTIN_DIR so it shows up
    in the local gallery immediately (useful while iterating)."""
    _require_flag()
    try:
        zip_bytes = _build_zip_from_request_body()
    except Exception as e:
        logger.exception("publish build failed")
        return jsonify({"error": str(e)}), 500

    body = request.get_json(silent=True) or {}
    m_id = str((body.get("manifest") or {}).get("id") or "").strip()
    m_version = str((body.get("manifest") or {}).get("version") or "1.0.0").strip()
    if not m_id:
        return jsonify({"error": "manifest.id is required"}), 400

    builtin = _builtin_dir()
    builtin.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._\-]+", "_", f"{m_id}_v{m_version}.zip")
    out_path = builtin / safe
    out_path.write_bytes(zip_bytes)

    return jsonify({
        "status": "published",
        "path": str(out_path),
        "bytes": len(zip_bytes),
    })


@solution_builder_bp.route("/api/solutions/test_install", methods=["POST"])
@login_required
@developer_required(api=True)
def test_install():
    """Build the bundle, install it into the current tenant with a `_test`
    name-suffix so created names don't clash with production content.

    Because test installs run against the author's own tenant, we can resolve
    placeholder credentials automatically — the user already has the real
    connection. The install wizard (which does not have this context) is the
    right place to prompt for credentials on a fresh tenant."""
    _require_flag()

    try:
        zip_bytes = _build_zip_from_request_body()
    except Exception as e:
        logger.exception("test_install build failed")
        return jsonify({"error": f"build failed: {e}"}), 500

    # Write the zip to a temp file and feed it to the installer.
    import tempfile
    body = request.get_json(silent=True) or {}
    suffix = str(body.get("name_suffix") or "_test")
    conflict = str(body.get("conflict_mode") or "rename")
    credentials = dict(body.get("credentials") or {})
    target_connection = body.get("target_connection")

    # Auto-resolve credentials the author didn't explicitly fill in, so they
    # aren't forced to retype things the platform already knows.
    selections = (body.get("selections") or {}) if isinstance(body, dict) else {}
    selected_connection_ids = [x for x in (selections.get("connection_ids") or []) if x is not None]
    resolved = _autofill_credentials_for_connections(selected_connection_ids)
    for k, v in resolved.items():
        credentials.setdefault(k, v)

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(zip_bytes)
        tmp_path = Path(tmp.name)

    try:
        options = InstallOptions(
            credentials={str(k): str(v) for k, v in credentials.items()},
            target_connection=target_connection,
            name_suffix=suffix,
            conflict_mode=conflict,
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


def _autofill_credentials_for_connections(connection_ids: List[Any]) -> Dict[str, str]:
    """Look up the selected tenant connections and emit the four
    `CONN_<NAME>_<FIELD>` values per connection (server, database, user,
    password) so the test install can run without the author having to
    retype anything. Sensitive fields stored as `{{LOCAL_SECRET:...}}` are
    resolved via LocalSecrets."""
    if not connection_ids:
        return {}

    # Pull the tenant's connections via the existing /get/connections route
    # — that's the authoritative helper and it forwards the caller's session.
    rows_by_id: Dict[int, Dict[str, Any]] = {}
    data = _get_via_test_client("/get/connections")
    items = data
    if isinstance(data, dict):
        items = data.get("data") or data.get("connections") or []
    for r in (items or []):
        if not isinstance(r, dict):
            continue
        rid = r.get("connection_id") or r.get("id")
        if rid is None:
            continue
        try:
            rows_by_id[int(rid)] = r
        except (TypeError, ValueError):
            continue

    try:
        from local_secrets import get_local_secret  # type: ignore
    except ImportError:
        def get_local_secret(name, default=None):  # type: ignore
            return default

    def _resolve(val):
        if not isinstance(val, str):
            return "" if val is None else str(val)
        if "{{LOCAL_SECRET:" in val:
            m = re.search(r"\{\{LOCAL_SECRET:(.+?)\}\}", val)
            if m:
                return get_local_secret(m.group(1), "") or ""
        return val

    out: Dict[str, str] = {}
    for cid in connection_ids:
        try:
            cid_int = int(cid)
        except (TypeError, ValueError):
            continue
        row = rows_by_id.get(cid_int)
        if not row:
            logger.warning("test_install: connection id=%s not found in tenant", cid)
            continue
        display = str(row.get("connection_name") or row.get("name") or f"Connection {cid_int}")
        safe = re.sub(r"[^A-Z0-9]+", "_", display.upper()).strip("_") or "CONN"
        out[f"CONN_{safe}_SERVER"]   = str(row.get("server") or "")
        out[f"CONN_{safe}_DATABASE"] = str(row.get("database_name") or row.get("database") or "")
        out[f"CONN_{safe}_USER"]     = _resolve(row.get("user_name") or row.get("user") or "")
        out[f"CONN_{safe}_PASSWORD"] = _resolve(row.get("password") or "")
    return out


# ════════════════════════════════════════════════════════════════
# Published list — surfaces bundles already in SOLUTIONS_BUILTIN_DIR
# ════════════════════════════════════════════════════════════════

@solution_builder_bp.route("/api/solutions/author/published", methods=["GET"])
@login_required
@developer_required(api=True)
def list_published():
    _require_flag()
    entries = list_bundled_solutions(_builtin_dir())
    return jsonify({"published": [e.to_dict() for e in entries]})
