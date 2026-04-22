"""
Solutions Gallery — bundler.

Packages selected tenant assets into a solution `.zip` ready for the
gallery or to be shared elsewhere. The bundler **does not modify any
existing routes or export helpers** — it makes internal HTTP calls via
Flask's test client to the existing `/export/agent/<id>`, `/api/tool/export/<name>`,
and `/environments/api/<id>/export` endpoints, and it writes its own
JSON for asset classes that don't have export routes yet (workflows,
integrations, connections, knowledge).

Usage:
    bundler = SolutionBundler(flask_app)
    zip_bytes = bundler.build(
        manifest,
        auth_headers={"X-API-Key": current_user_api_key},
        agent_ids=[42], tool_names=["format_report"],
        workflow_names=["customer_onboarding_ai_guided_v4"],
        integration_names=["stripe"],
        connection_ids=[5],
        environment_ids=[7],
        knowledge_document_ids=[101, 102],
        seed_files={"schema.sql": b"...", "seeds/customers.csv": b"..."},
        sample_input_files={"customer_form.pdf": b"..."},
        branding={"display_name": "..."},
        readme_md="# ...",
        preview_files={"icon.png": b"...", "screenshot1.png": b"..."},
    )

The returned bytes are a full solution bundle zip.
"""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from solution_manifest import (
    BRANDING_FILENAME,
    MANIFEST_FILENAME,
    PREVIEW_DIR,
    README_FILENAME,
    BrandingOverrides,
    CredentialPrompt,
    PostInstallAction,
    SolutionAssets,
    SolutionManifest,
    extract_placeholders,
)

logger = logging.getLogger(__name__)


class SolutionBundler:
    """Builds a solution bundle `.zip` from selected tenant assets."""

    def __init__(self, flask_app):
        self._app = flask_app

    # ────────────────────────────────────────────────────────────────
    # Public API
    # ────────────────────────────────────────────────────────────────

    def build(
        self,
        manifest: SolutionManifest,
        *,
        auth_headers: Optional[Dict[str, str]] = None,
        agent_ids: Optional[List[int]] = None,
        tool_names: Optional[List[str]] = None,
        workflow_names: Optional[List[str]] = None,
        integration_names: Optional[List[str]] = None,
        connection_ids: Optional[List[int]] = None,
        environment_ids: Optional[List[int]] = None,
        knowledge_document_ids: Optional[List[int]] = None,
        seed_schema_sql: Optional[bytes] = None,
        seed_csvs: Optional[Dict[str, bytes]] = None,          # {filename: bytes}
        sample_input_files: Optional[Dict[str, bytes]] = None,  # {filename: bytes}
        branding: Optional[Dict[str, Any]] = None,
        readme_md: Optional[str] = None,
        preview_files: Optional[Dict[str, bytes]] = None,       # {filename: bytes}
    ) -> bytes:
        """Build the bundle and return its bytes."""
        manifest = self._clone_manifest(manifest)
        auth_headers = auth_headers or {}
        seed_csvs = seed_csvs or {}
        sample_input_files = sample_input_files or {}
        preview_files = preview_files or {}

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # ── Agents ────────────────────────────────────────────
            for agent_id in (agent_ids or []):
                agent_entries = self._pack_agent(auth_headers, agent_id)
                for inner_rel_path, data in agent_entries:
                    zf.writestr(f"agents/{inner_rel_path}", data)
                # Add agent name to manifest assets
                name = self._get_agent_name(agent_id) or f"agent_{agent_id}"
                if name not in manifest.assets.agents:
                    manifest.assets.agents.append(name)

            # ── Custom tools ──────────────────────────────────────
            for tool_name in (tool_names or []):
                tool_entries = self._pack_tool(auth_headers, tool_name)
                for inner_rel_path, data in tool_entries:
                    zf.writestr(f"tools/{inner_rel_path}", data)
                if tool_name not in manifest.assets.tools:
                    manifest.assets.tools.append(tool_name)

            # ── Workflows ─────────────────────────────────────────
            for wf_name in (workflow_names or []):
                wf_bytes = self._pack_workflow(wf_name)
                if wf_bytes is None:
                    logger.warning("Workflow %s not found, skipping", wf_name)
                    continue
                # Normalise: strip any existing .json so we don't end up with
                # "name.json.json" on disk or in the manifest. The validator
                # compares post_install targets against the basename, so both
                # the zip entry and the manifest entry need to agree.
                stem = wf_name[:-5] if wf_name.lower().endswith(".json") else wf_name
                safe_stem = _safe_filename(stem)
                zf.writestr(f"workflows/{safe_stem}.json", wf_bytes)
                entry = f"{safe_stem}.json"
                if entry not in manifest.assets.workflows:
                    manifest.assets.workflows.append(entry)

            # ── Integration templates ─────────────────────────────
            for itg_name in (integration_names or []):
                itg_bytes, placeholders = self._pack_integration(itg_name)
                if itg_bytes is None:
                    logger.warning("Integration %s not found, skipping", itg_name)
                    continue
                zf.writestr(f"integrations/{_safe_filename(itg_name)}.json", itg_bytes)
                if itg_name not in manifest.assets.integrations:
                    manifest.assets.integrations.append(f"{_safe_filename(itg_name)}.json")
                # Auto-declare credentials for discovered placeholders
                self._ensure_credentials_for_placeholders(manifest, placeholders)

            # ── Connections (scaffolds, credentials stripped) ─────
            for conn_id in (connection_ids or []):
                conn_bytes, placeholders = self._pack_connection(conn_id)
                if conn_bytes is None:
                    logger.warning("Connection %s not found, skipping", conn_id)
                    continue
                display = self._get_connection_name(conn_id) or f"connection_{conn_id}"
                zf.writestr(f"connections/{_safe_filename(display)}.json", conn_bytes)
                if display not in manifest.assets.connections:
                    manifest.assets.connections.append(f"{_safe_filename(display)}.json")
                self._ensure_credentials_for_placeholders(manifest, placeholders)

            # ── Environments ──────────────────────────────────────
            for env_id in (environment_ids or []):
                env_zip = self._pack_environment(auth_headers, env_id)
                if env_zip is None:
                    logger.warning("Environment %s not found, skipping", env_id)
                    continue
                display = self._get_environment_name(env_id) or f"environment_{env_id}"
                zf.writestr(f"environments/{_safe_filename(display)}.zip", env_zip)
                if display not in manifest.assets.environments:
                    manifest.assets.environments.append(f"{_safe_filename(display)}.zip")

            # ── Knowledge documents ───────────────────────────────
            for doc_id in (knowledge_document_ids or []):
                entries = self._pack_knowledge(doc_id)
                if not entries:
                    logger.warning("Knowledge doc %s not found, skipping", doc_id)
                    continue
                for inner_rel_path, data in entries:
                    zf.writestr(f"knowledge/{inner_rel_path}", data)
                    if inner_rel_path not in manifest.assets.knowledge:
                        manifest.assets.knowledge.append(inner_rel_path)

            # ── Seed data (schema + CSVs + sample inputs) ─────────
            if seed_schema_sql:
                zf.writestr("data/schema.sql", seed_schema_sql)
                manifest.assets.data["schema_sql"] = True

            if seed_csvs:
                seeds_list = list(manifest.assets.data.get("seeds") or [])
                for fname, body in seed_csvs.items():
                    zf.writestr(f"data/seeds/{_safe_filename(fname)}", body)
                    if fname not in seeds_list:
                        seeds_list.append(fname)
                manifest.assets.data["seeds"] = seeds_list

            if sample_input_files:
                samples_list = list(manifest.assets.data.get("sample_inputs") or [])
                for fname, body in sample_input_files.items():
                    zf.writestr(f"data/sample_inputs/{_safe_filename(fname)}", body)
                    if fname not in samples_list:
                        samples_list.append(fname)
                manifest.assets.data["sample_inputs"] = samples_list

            # ── Preview (icon + screenshots) ─────────────────────
            for fname, body in (preview_files or {}).items():
                zf.writestr(f"{PREVIEW_DIR}/{_safe_filename(fname)}", body)

            # ── README ────────────────────────────────────────────
            if readme_md:
                zf.writestr(README_FILENAME, readme_md.encode("utf-8"))

            # ── Branding ──────────────────────────────────────────
            if branding:
                try:
                    br = BrandingOverrides.from_dict(branding)
                    zf.writestr(
                        BRANDING_FILENAME,
                        json.dumps(asdict(br), indent=2).encode("utf-8"),
                    )
                except Exception as e:
                    logger.warning("Could not write branding.json: %s", e)

            # ── Manifest (last, now that assets list is populated) ─
            errors = manifest.validate()
            if errors:
                # Validation errors are written alongside for visibility but
                # do not block the build — the user can fix and re-export.
                logger.warning("Solution %s has validation warnings: %s", manifest.id, errors)
            zf.writestr(MANIFEST_FILENAME, manifest.to_json().encode("utf-8"))

        buf.seek(0)
        return buf.getvalue()

    # ────────────────────────────────────────────────────────────────
    # Per-asset packing helpers
    # ────────────────────────────────────────────────────────────────
    #
    # For classes where an existing export route exists we call it via the
    # Flask test client; for everything else we read data directly from
    # disk / DB helpers.

    def _pack_agent(
        self, auth_headers: Dict[str, str], agent_id: int
    ) -> List[Tuple[str, bytes]]:
        """Invoke the existing /export/agent/<id> route and extract the zip's
        inner files so they can be re-embedded under agents/<name>/ in the
        solution bundle. Returns list of (inner_rel_path, bytes)."""
        resp = self._app.test_client().post(
            f"/export/agent/{agent_id}",
            headers=auth_headers,
        )
        if resp.status_code != 200:
            logger.warning("export_agent returned %s for id=%s", resp.status_code, agent_id)
            return []

        # Existing route returns a zip with top-level folder `agent_<id>/…`.
        # We rename that to the agent's name so solutions read more nicely.
        agent_name = self._get_agent_name(agent_id) or f"agent_{agent_id}"
        safe_name = _safe_filename(agent_name)

        try:
            inner_zip = zipfile.ZipFile(io.BytesIO(resp.data))
        except zipfile.BadZipFile:
            logger.warning("export_agent returned non-zip for id=%s", agent_id)
            return []

        out: List[Tuple[str, bytes]] = []
        prefix = f"agent_{agent_id}/"
        for info in inner_zip.infolist():
            if info.is_dir():
                continue
            rel = info.filename
            if rel.startswith(prefix):
                rel = rel[len(prefix):]
            out.append((f"{safe_name}/{rel}", inner_zip.read(info)))
        return out

    def _pack_tool(
        self, auth_headers: Dict[str, str], tool_name: str
    ) -> List[Tuple[str, bytes]]:
        """Call the existing single-tool export route and embed the zip's
        contents under tools/<tool_name>/."""
        resp = self._app.test_client().get(
            f"/api/tool/export/{tool_name}",
            headers=auth_headers,
        )
        if resp.status_code != 200:
            logger.warning("tool export returned %s for %s", resp.status_code, tool_name)
            return []

        safe_name = _safe_filename(tool_name)
        try:
            inner_zip = zipfile.ZipFile(io.BytesIO(resp.data))
        except zipfile.BadZipFile:
            # Some variants return JSON + file separately; fall back to a raw
            # single-file embedding.
            return [(f"{safe_name}/raw.bin", resp.data)]

        out: List[Tuple[str, bytes]] = []
        for info in inner_zip.infolist():
            if info.is_dir():
                continue
            out.append((f"{safe_name}/{info.filename}", inner_zip.read(info)))
        return out

    def _pack_workflow(self, workflow_name: str) -> Optional[bytes]:
        """Workflows live as JSON files in `workflows/`. Just copy the file."""
        # Look up app root from the Flask app config; fall back to CWD.
        root = self._workflows_root()
        # Workflow filenames may lack `.json`; try both.
        for candidate in (workflow_name, f"{workflow_name}.json"):
            p = root / candidate
            if p.is_file():
                try:
                    return p.read_bytes()
                except OSError:
                    return None
        return None

    def _pack_integration(
        self, integration_name: str
    ) -> Tuple[Optional[bytes], List[str]]:
        """Integrations live as JSON files in integrations/builtin/. Return
        the bytes AND any ${PLACEHOLDER}s found so the bundler can auto-
        declare credentials."""
        root = self._integrations_root()
        candidates = [
            root / f"{integration_name}.json",
            root / integration_name,
        ]
        for p in candidates:
            if p.is_file():
                try:
                    data = p.read_bytes()
                    placeholders = extract_placeholders(data.decode("utf-8", errors="ignore"))
                    return data, placeholders
                except OSError:
                    return None, []
        return None, []

    def _pack_connection(
        self, connection_id: int
    ) -> Tuple[Optional[bytes], List[str]]:
        """Produce a connection scaffold JSON (credentials stripped / replaced
        with ${PLACEHOLDERS}). Uses the existing DB helper.

        Placeholder naming mirrors the wizard's `syncConnectionCredentials`
        client-side helper: `CONN_<UPPER_SNAKE_NAME>_<FIELD>`. This keeps the
        scaffold and the manifest.credentials rows in agreement — without
        that, the installer rejects the install with "required credentials
        missing" because the scaffold references tokens nobody declared."""
        try:
            from AppUtils import get_connection_by_id  # type: ignore
        except ImportError:
            get_connection_by_id = None

        conn = None
        if get_connection_by_id:
            try:
                conn = get_connection_by_id(connection_id)
            except Exception as e:
                logger.warning("get_connection_by_id(%s) failed: %s", connection_id, e)

        if not conn:
            # Fallback: call /get/connections and find by id.
            try:
                with self._app.test_client() as client:
                    resp = client.get("/get/connections")
                    if resp.status_code == 200:
                        data = resp.get_json()
                        if isinstance(data, str):
                            data = json.loads(data)
                        for c in data or []:
                            if int(c.get("id") or c.get("connection_id") or 0) == int(connection_id):
                                conn = c
                                break
            except Exception as e:
                logger.warning("connection fallback failed for id=%s: %s", connection_id, e)

        if not conn:
            return None, []

        conn = dict(conn)  # copy
        display = conn.get("connection_name") or conn.get("name") or f"Connection {connection_id}"
        safe = re.sub(r"[^A-Z0-9]+", "_", str(display).upper()).strip("_") or "CONN"
        placeholders = [
            f"CONN_{safe}_SERVER",
            f"CONN_{safe}_DATABASE",
            f"CONN_{safe}_USER",
            f"CONN_{safe}_PASSWORD",
        ]
        scaffold: Dict[str, Any] = {
            "connection_name": display,
            "database_type": conn.get("database_type") or conn.get("type") or "",
            "server":        "${" + placeholders[0] + "}",
            "database_name": "${" + placeholders[1] + "}",
            "user_name":     "${" + placeholders[2] + "}",
            "password":      "${" + placeholders[3] + "}",
            "port": conn.get("port"),
            "parameters": conn.get("parameters") or "",
        }
        data = json.dumps(scaffold, indent=2).encode("utf-8")
        return data, placeholders

    def _pack_environment(
        self, auth_headers: Dict[str, str], env_id: int
    ) -> Optional[bytes]:
        """Call the existing environment-export route, embed the whole zip."""
        resp = self._app.test_client().get(
            f"/environments/api/{env_id}/export",
            headers=auth_headers,
        )
        if resp.status_code != 200:
            logger.warning("env export returned %s for id=%s", resp.status_code, env_id)
            return None
        return resp.data

    def _pack_knowledge(self, doc_id: int) -> List[Tuple[str, bytes]]:
        """Read a knowledge document's file bytes + a small metadata JSON
        so the installer can re-upload with the right filename/type."""
        try:
            from AppUtils import get_document_by_id  # type: ignore
        except ImportError:
            return []
        try:
            doc = get_document_by_id(doc_id)
        except Exception as e:
            logger.warning("get_document_by_id(%s) failed: %s", doc_id, e)
            return []
        if not doc:
            return []

        doc = doc[0] if isinstance(doc, list) and doc else doc
        if not isinstance(doc, dict):
            return []

        filename = doc.get("filename") or f"document_{doc_id}.bin"
        safe_name = _safe_filename(filename)
        meta = {
            "document_id": doc_id,
            "filename": filename,
            "content_type": doc.get("content_type") or "application/octet-stream",
            "pages": doc.get("pages"),
        }
        out: List[Tuple[str, bytes]] = [
            (f"{safe_name}.meta.json", json.dumps(meta, indent=2).encode("utf-8")),
        ]
        # If the doc has a binary blob, include it.
        blob = doc.get("file_bytes") or doc.get("content_bytes")
        if isinstance(blob, (bytes, bytearray)):
            out.append((safe_name, bytes(blob)))
        elif isinstance(blob, str):
            # Occasionally stored as text
            out.append((safe_name, blob.encode("utf-8")))
        return out

    # ────────────────────────────────────────────────────────────────
    # Small utilities
    # ────────────────────────────────────────────────────────────────

    def _clone_manifest(self, m: SolutionManifest) -> SolutionManifest:
        return SolutionManifest.from_dict(m.to_dict())

    def _ensure_credentials_for_placeholders(
        self, manifest: SolutionManifest, placeholders: Iterable[str]
    ) -> None:
        """Add a CredentialPrompt entry for any placeholder not already
        declared. The user can refine labels/descriptions in the author UI."""
        declared = {c.placeholder for c in manifest.credentials}
        for ph in placeholders:
            if ph and ph not in declared:
                manifest.credentials.append(
                    CredentialPrompt(
                        placeholder=ph,
                        label=_humanize_placeholder(ph),
                        required=True,
                    )
                )
                declared.add(ph)

    def _workflows_root(self) -> Path:
        try:
            import config as cfg  # type: ignore
            root = Path(getattr(cfg, "APP_ROOT", ".")) / "workflows"
        except Exception:
            root = Path("workflows")
        return root

    def _integrations_root(self) -> Path:
        # Tenant-specific integration files live elsewhere in richer setups.
        # For the MVP we read from the ship-with-app library.
        try:
            import config as cfg  # type: ignore
            root = Path(getattr(cfg, "APP_ROOT", ".")) / "integrations" / "builtin"
        except Exception:
            root = Path("integrations/builtin")
        return root

    def _get_agent_name(self, agent_id: int) -> Optional[str]:
        try:
            from AppUtils import get_agent_by_id  # type: ignore
            result = get_agent_by_id(agent_id)
            if isinstance(result, list) and result:
                result = result[0]
            if isinstance(result, dict):
                # Agents table uses `description` as the display name — not `agent_name`.
                # Fall through to agent_name / name for resilience against future helper shapes.
                for k in ("agent_description", "description", "agent_name", "name"):
                    v = result.get(k)
                    if v and str(v).strip():
                        return str(v).strip()
        except Exception:
            pass
        return None

    def _get_connection_name(self, connection_id: int) -> Optional[str]:
        try:
            from AppUtils import get_connection_by_id  # type: ignore
            result = get_connection_by_id(connection_id)
            if isinstance(result, list) and result:
                result = result[0]
            if isinstance(result, dict):
                return str(result.get("connection_name") or result.get("name") or "").strip() or None
        except Exception:
            pass
        return None

    def _get_environment_name(self, env_id: int) -> Optional[str]:
        try:
            from agent_environments.environment_api import get_environment_by_id  # type: ignore
            env = get_environment_by_id(env_id)
            if isinstance(env, dict):
                return str(env.get("name") or "").strip() or None
        except Exception:
            pass
        return None


# ────────────────────────────────────────────────────────────────
# Module-level helpers
# ────────────────────────────────────────────────────────────────

_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._\- ]+")


def _safe_filename(name: str) -> str:
    """Sanitise filenames for zip entries. Keeps letters, digits, dots,
    underscores, hyphens and single spaces. No path separators."""
    s = str(name or "").strip()
    s = _UNSAFE_RE.sub("_", s)
    s = s.replace("/", "_").replace("\\", "_")
    return s or "unnamed"


def _humanize_placeholder(ph: str) -> str:
    """PUBLIC_API_KEY → Public API Key."""
    parts = ph.split("_")
    return " ".join(p.capitalize() if not p.isupper() or len(p) <= 3 else p for p in parts)
