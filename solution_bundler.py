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
    safe_filename,
)

logger = logging.getLogger(__name__)


class SolutionBundler:
    """Builds a solution bundle `.zip` from selected tenant assets.

    After `build()` returns, `last_report` describes what actually happened:
      {"packed": {kind: [entry, ...]}, "skipped": [{"kind", "name", "reason"}],
       "validation_warnings": [...]}
    Callers should surface `skipped` to the author — a skipped asset means
    the bundle does NOT contain something they selected.
    """

    def __init__(self, flask_app):
        self._app = flask_app
        self.last_report: Dict[str, Any] = {}

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

        # What actually made it into the zip vs. what had to be skipped.
        # `packed` becomes the manifest's asset inventory verbatim — the
        # manifest must never claim files the zip doesn't contain.
        packed: Dict[str, List[str]] = {
            k: [] for k in (
                "agents", "tools", "workflows", "integrations",
                "connections", "environments", "knowledge",
            )
        }
        skipped: List[Dict[str, str]] = []

        def _skip(kind: str, name: Any, reason: str) -> None:
            logger.warning("Solution build: %s %r skipped — %s", kind, name, reason)
            skipped.append({"kind": kind, "name": str(name), "reason": reason})

        def _packed(kind: str, entry: str) -> None:
            if entry not in packed[kind]:
                packed[kind].append(entry)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # ── Agents ────────────────────────────────────────────
            for agent_id in (agent_ids or []):
                name = self._get_agent_name(agent_id) or f"agent_{agent_id}"
                agent_entries = self._pack_agent(auth_headers, agent_id)
                if not agent_entries:
                    _skip("agents", name, "agent export failed or agent not found")
                    continue
                for inner_rel_path, data in agent_entries:
                    zf.writestr(f"agents/{inner_rel_path}", data)
                _packed("agents", name)

            # ── Custom tools ──────────────────────────────────────
            for tool_name in (tool_names or []):
                tool_entries = self._pack_tool(auth_headers, tool_name)
                if not tool_entries:
                    _skip("tools", tool_name, "tool export failed or tool not found")
                    continue
                for inner_rel_path, data in tool_entries:
                    zf.writestr(f"tools/{inner_rel_path}", data)
                _packed("tools", tool_name)

            # ── Workflows ─────────────────────────────────────────
            for wf_name in (workflow_names or []):
                wf_bytes = self._pack_workflow(wf_name)
                if wf_bytes is None:
                    _skip("workflows", wf_name, "workflow file not found in workflows/")
                    continue
                # Normalise: strip any existing .json so we don't end up with
                # "name.json.json" on disk or in the manifest. The validator
                # compares post_install targets against the basename, so both
                # the zip entry and the manifest entry need to agree.
                stem = wf_name[:-5] if wf_name.lower().endswith(".json") else wf_name
                safe_stem = _safe_filename(stem)
                zf.writestr(f"workflows/{safe_stem}.json", wf_bytes)
                _packed("workflows", f"{safe_stem}.json")

            # ── Integrations (configured instance or builtin template) ─
            for itg_name in (integration_names or []):
                itg_bytes, cred_prompts = self._pack_integration(auth_headers, itg_name)
                if itg_bytes is None:
                    _skip(
                        "integrations", itg_name,
                        "no configured integration or builtin template with this name",
                    )
                    continue
                zf.writestr(f"integrations/{_safe_filename(itg_name)}.json", itg_bytes)
                _packed("integrations", f"{_safe_filename(itg_name)}.json")
                # Auto-declare credentials for discovered placeholders
                self._ensure_credentials_for_placeholders(manifest, cred_prompts)

            # ── Connections (scaffolds, credentials stripped) ─────
            for conn_id in (connection_ids or []):
                conn_bytes, placeholders = self._pack_connection(conn_id)
                if conn_bytes is None:
                    _skip("connections", f"connection_{conn_id}", "connection not found")
                    continue
                display = self._get_connection_name(conn_id) or f"connection_{conn_id}"
                zf.writestr(f"connections/{_safe_filename(display)}.json", conn_bytes)
                _packed("connections", f"{_safe_filename(display)}.json")
                self._ensure_credentials_for_placeholders(manifest, placeholders)

            # ── Environments ──────────────────────────────────────
            for env_id in (environment_ids or []):
                env_zip = self._pack_environment(auth_headers, env_id)
                if env_zip is None:
                    _skip("environments", f"environment_{env_id}", "environment export failed")
                    continue
                display = self._get_environment_name(env_id) or f"environment_{env_id}"
                zf.writestr(f"environments/{_safe_filename(display)}.zip", env_zip)
                _packed("environments", f"{_safe_filename(display)}.zip")

            # ── Knowledge documents ───────────────────────────────
            for doc_id in (knowledge_document_ids or []):
                entries = self._pack_knowledge(doc_id)
                if not entries:
                    _skip("knowledge", f"document_{doc_id}", "knowledge document not found")
                    continue
                for inner_rel_path, data in entries:
                    zf.writestr(f"knowledge/{inner_rel_path}", data)
                    _packed("knowledge", inner_rel_path)

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

            # ── Manifest (last) — reconcile assets with the zip ───
            # The wizard pre-populates manifest.assets with optimistic
            # display names; replace them wholesale with what was actually
            # written so the manifest can never claim an asset the bundle
            # doesn't contain (and never lists the same asset twice under
            # sanitised + unsanitised spellings).
            for kind, entries in packed.items():
                setattr(manifest.assets, kind, list(entries))

            errors = manifest.validate()
            if errors:
                # Validation errors are written alongside for visibility but
                # do not block the build — the user can fix and re-export.
                logger.warning("Solution %s has validation warnings: %s", manifest.id, errors)
            zf.writestr(MANIFEST_FILENAME, manifest.to_json().encode("utf-8"))

        self.last_report = {
            "packed": packed,
            "skipped": skipped,
            "validation_warnings": errors,
        }
        buf.seek(0)
        return buf.getvalue()

    def discover_credentials(
        self,
        *,
        auth_headers: Optional[Dict[str, str]] = None,
        integration_names: Optional[List[str]] = None,
        connection_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """Dry-run the credential-bearing packers to predict what
        manifest.credentials a build of these selections would auto-declare.

        Powers the author wizard's "Rescan selections" button, so the
        Credentials step previews exactly the prompts the installed bundle
        will ask for. Nothing is written; returns
        {"credentials": [prompt dicts], "unresolved": [names we couldn't inspect]}.
        """
        auth_headers = auth_headers or {}
        scratch = SolutionManifest.from_dict({"id": "scan", "name": "scan"})
        unresolved: List[str] = []

        for name in (integration_names or []):
            data, prompts = self._pack_integration(auth_headers, name)
            if data is None:
                unresolved.append(str(name))
                continue
            self._ensure_credentials_for_placeholders(scratch, prompts)

        for cid in (connection_ids or []):
            data, placeholders = self._pack_connection(cid)
            if data is None:
                unresolved.append(f"connection_{cid}")
                continue
            self._ensure_credentials_for_placeholders(scratch, placeholders)

        return {
            "credentials": [asdict(c) for c in scratch.credentials],
            "unresolved": unresolved,
        }

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
        self, auth_headers: Dict[str, str], integration_name: str
    ) -> Tuple[Optional[bytes], List[Any]]:
        """Pack an integration for the bundle.

        Preferred path: the tenant's *configured* integration instance (the
        DB-backed rows served by /api/integrations — what the wizard's picker
        actually lists). Exported WITHOUT secrets: each credential field
        becomes a ${ITG_<NAME>_<FIELD>} placeholder declared in
        manifest.credentials so the install wizard can prompt for it.

        Fallback: a raw template file in integrations/builtin/ (legacy
        template-only bundles). Returns (bytes|None, credential prompts —
        CredentialPrompt objects or bare placeholder strings)."""
        doc, prompts = self._pack_integration_instance(auth_headers, integration_name)
        if doc is not None:
            return doc, prompts

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

    def _pack_integration_instance(
        self, auth_headers: Dict[str, str], integration_name: str
    ) -> Tuple[Optional[bytes], List[CredentialPrompt]]:
        """Serialise a configured integration instance to a portable JSON doc
        the installer can recreate via POST /api/integrations."""
        inst = self._find_integration_instance(auth_headers, integration_name)
        if not inst:
            return None, []

        template_key = str(inst.get("template_key") or "").strip()
        instance_config = inst.get("instance_config")
        if isinstance(instance_config, str):
            try:
                instance_config = json.loads(instance_config or "{}")
            except json.JSONDecodeError:
                instance_config = {}
        if not isinstance(instance_config, dict):
            instance_config = {}

        cred_fields, needs_user_oauth = self._credential_fields_for_template(
            template_key, inst.get("auth_type")
        )

        safe = re.sub(r"[^A-Z0-9]+", "_", str(integration_name).upper()).strip("_") or "INTEGRATION"
        credentials: Dict[str, str] = {}
        prompts: List[CredentialPrompt] = []
        for field in cred_fields:
            ph = f"ITG_{safe}_{field.upper()}"
            credentials[field] = "${" + ph + "}"
            prompts.append(CredentialPrompt(
                placeholder=ph,
                label=f"{integration_name} — {_humanize_field(field)}",
                required=False,
                description=(
                    "Leave blank to finish setup on the Integrations page after install."
                ),
            ))

        doc: Dict[str, Any] = {
            "kind": "integration_instance",
            "format_version": 1,
            "template_key": template_key,
            "integration_name": str(integration_name),
            "description": inst.get("description") or "",
            "auth_type": inst.get("auth_type") or "",
            "instance_config": instance_config,
            "credentials": credentials,
        }
        if needs_user_oauth:
            doc["post_install_note"] = (
                "This integration uses a user OAuth sign-in — re-authorize it "
                "on the Integrations page after install."
            )
        return json.dumps(doc, indent=2).encode("utf-8"), prompts

    def _find_integration_instance(
        self, auth_headers: Dict[str, str], integration_name: str
    ) -> Optional[Dict[str, Any]]:
        """Look up a configured integration instance by display name via the
        existing /api/integrations list route (forwards the caller's auth)."""
        try:
            with self._app.test_client() as client:
                resp = client.get("/api/integrations", headers=auth_headers)
                if resp.status_code != 200:
                    logger.warning(
                        "integration instance lookup returned %s for %r",
                        resp.status_code, integration_name,
                    )
                    return None
                data = resp.get_json(silent=True) or {}
        except Exception as e:
            logger.warning("integration instance lookup failed for %r: %s", integration_name, e)
            return None
        items = data.get("integrations") if isinstance(data, dict) else data
        wanted = str(integration_name).strip().lower()
        for r in (items or []):
            if isinstance(r, dict) and str(r.get("integration_name") or "").strip().lower() == wanted:
                return r
        return None

    def _credential_fields_for_template(
        self, template_key: str, auth_type: Any
    ) -> Tuple[List[str], bool]:
        """Which credential fields an integration of this template needs at
        install time, and whether it additionally requires an interactive
        user OAuth sign-in (tokens are never exportable)."""
        template: Optional[Dict[str, Any]] = None
        if template_key:
            try:
                from integration_manager import get_integration_manager  # type: ignore
                template = get_integration_manager().get_template(template_key)
            except Exception:
                template = None
            if template is None:
                p = self._integrations_root() / f"{template_key}.json"
                if p.is_file():
                    try:
                        template = json.loads(p.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        template = None

        auth_config = (template or {}).get("auth_config") or {}
        at = str((template or {}).get("auth_type") or auth_type or "").strip().lower()

        cred_defs = auth_config.get("credential_fields") or []
        if cred_defs:
            fields = [str(fd.get("field") or "").strip() for fd in cred_defs if isinstance(fd, dict)]
            return [f for f in fields if f], False

        if at == "oauth2":
            grant = str(auth_config.get("grant_type") or "").strip().lower()
            # authorization-code flows need a user sign-in on the new tenant;
            # client_credentials (app-only) works headless once creds are set.
            return ["client_id", "client_secret"], grant != "client_credentials"
        if at in ("api_key", "apikey"):
            return ["api_key"], False
        if at == "basic":
            return ["username", "password"], False
        if at in ("bearer", "bearer_token", "token"):
            return ["bearer_token"], False
        return [], False

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
        self, manifest: SolutionManifest, placeholders: Iterable[Any]
    ) -> None:
        """Add a CredentialPrompt entry for any placeholder not already
        declared. Accepts bare placeholder strings or ready-made
        CredentialPrompt objects. The user can refine labels/descriptions in
        the author UI."""
        declared = {c.placeholder for c in manifest.credentials}
        for ph in placeholders:
            if isinstance(ph, CredentialPrompt):
                if ph.placeholder and ph.placeholder not in declared:
                    manifest.credentials.append(ph)
                    declared.add(ph.placeholder)
            elif ph and ph not in declared:
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

# Shared with solution_manifest / solution_installer so all three agree on
# how display names map to zip entry names.
_safe_filename = safe_filename


def _humanize_placeholder(ph: str) -> str:
    """PUBLIC_API_KEY → Public API Key."""
    parts = ph.split("_")
    return " ".join(p.capitalize() if not p.isupper() or len(p) <= 3 else p for p in parts)


_FIELD_ACRONYMS = {"id", "api", "url", "uri"}


def _humanize_field(field: str) -> str:
    """client_id → Client ID, api_key → API Key, bearer_token → Bearer Token."""
    return " ".join(
        p.upper() if p.lower() in _FIELD_ACRONYMS else p.capitalize()
        for p in str(field).replace("-", "_").split("_") if p
    )
