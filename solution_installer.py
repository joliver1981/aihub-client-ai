"""
Solutions Gallery — installer.

Unpacks a solution bundle, dispatches each asset class to its handler
(mostly by calling the existing import routes via Flask's test client),
and returns a structured result describing what was created.

Design constraints:
  - Does NOT modify any existing route or function
  - Gated at the route layer behind the `solutions_enabled` feature flag
  - Idempotent where possible: re-installing the same solution should
    update-in-place, not create duplicates, via the conflict-resolution
    modes of the existing import routes
  - Fails soft: an error in one asset class is recorded but doesn't abort
    the remaining installs (ops can review the result and retry)
"""

from __future__ import annotations

import io
import json
import logging
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from solution_manifest import (
    MANIFEST_FILENAME,
    PREVIEW_DIR,
    SolutionManifest,
    resolve_placeholders,
)
from solution_seed_loader import SeedResult, load_seed_data

logger = logging.getLogger(__name__)


@dataclass
class AssetResult:
    kind: str        # "agent" | "tool" | "workflow" | "integration" | "connection" | "environment" | "knowledge" | "seed"
    name: str
    status: str      # "installed" | "updated" | "skipped" | "failed"
    detail: str = ""
    resource_id: Optional[Any] = None  # whatever identifier the platform assigned

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "resource_id": self.resource_id,
        }


@dataclass
class InstallResult:
    solution_id: str
    solution_name: str
    success: bool = True
    assets: List[AssetResult] = field(default_factory=list)
    seed_result: Optional[SeedResult] = None
    post_install: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "solution_id": self.solution_id,
            "solution_name": self.solution_name,
            "success": self.success,
            "assets": [a.to_dict() for a in self.assets],
            "seed_result": self.seed_result.to_dict() if self.seed_result else None,
            "post_install": list(self.post_install),
            "errors": list(self.errors),
        }


@dataclass
class InstallOptions:
    credentials: Dict[str, str] = field(default_factory=dict)
    target_connection: Optional[Dict[str, Any]] = None  # for seed loader
    name_suffix: str = ""  # e.g. "_test" for test-installs
    conflict_mode: str = "rename"  # "rename" | "overwrite" | "skip"


def analyze_bundle(bundle_path: Path, flask_app=None, auth_headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Inspect a bundle and return the manifest plus a conflict preview.

    This is a read-only operation — no platform state is touched. When a
    Flask app is passed, also looks up existing tenant content by name to
    flag what would conflict at install time so the wizard can show it.
    """
    manifest = _load_manifest(bundle_path)
    if manifest is None:
        return {"valid": False, "error": f"No valid {MANIFEST_FILENAME} in bundle"}
    errors = manifest.validate()
    result: Dict[str, Any] = {
        "valid": True,
        "manifest": manifest.to_dict(),
        "validation_errors": errors,
        "placeholders": [c.placeholder for c in manifest.credentials],
        "conflicts": {},
    }
    if flask_app is not None:
        try:
            result["conflicts"] = _detect_conflicts(manifest, flask_app, auth_headers or {})
        except Exception as e:
            logger.warning("conflict detection failed: %s", e)
            result["conflicts"] = {}
    return result


def _detect_conflicts(
    manifest: SolutionManifest, flask_app, auth_headers: Dict[str, str]
) -> Dict[str, List[str]]:
    """For each asset kind in the manifest, return the names that already
    exist in the tenant and would therefore be renamed/skipped/overwritten
    according to the user's chosen conflict_mode."""
    cookie = auth_headers.get("Cookie", "")
    api_key = auth_headers.get("X-API-Key", "")
    headers: Dict[str, str] = {}
    if cookie:  headers["Cookie"] = cookie
    if api_key: headers["X-API-Key"] = api_key

    def _get(path: str):
        try:
            with flask_app.test_client() as client:
                resp = client.get(path, headers=headers)
                if resp.status_code != 200:
                    return None
                return resp.get_json()
        except Exception:
            return None

    # Workflows: compare against files in workflows/ via our own list route.
    existing_workflows = set()
    wf_data = _get("/api/solutions/workflows/list")
    for w in ((wf_data or {}).get("workflows") or []):
        if isinstance(w, dict):
            existing_workflows.add((w.get("name") or "").lower())

    # Agents: by description (what the tenant uses as the display name).
    existing_agents = set()
    ag_data = _get("/get/agents")
    for a in (((ag_data or {}).get("data") if isinstance(ag_data, dict) else ag_data) or []):
        if isinstance(a, dict):
            existing_agents.add((a.get("agent_description") or a.get("description") or "").lower())

    # Connections: by connection_name.
    existing_conns = set()
    c_data = _get("/get/connections")
    for c in (c_data or []) if isinstance(c_data, list) else ((c_data or {}).get("data") or []):
        if isinstance(c, dict):
            existing_conns.add((c.get("connection_name") or "").lower())

    # Integrations: by integration_name.
    existing_integrations = set()
    i_data = _get("/api/integrations")
    for i in ((i_data or {}).get("integrations") or []):
        if isinstance(i, dict):
            existing_integrations.add((i.get("integration_name") or "").lower())

    def _conflicts_in(candidates: List[str], existing: set) -> List[str]:
        out: List[str] = []
        for c in candidates or []:
            stem = _basename_no_ext(c)
            if stem and stem.lower() in existing:
                out.append(stem)
        return out

    return {
        "workflows":    _conflicts_in(manifest.assets.workflows, existing_workflows),
        "agents":       _conflicts_in(manifest.assets.agents, existing_agents),
        "connections":  _conflicts_in(manifest.assets.connections, existing_conns),
        "integrations": _conflicts_in(manifest.assets.integrations, existing_integrations),
    }


def _basename_no_ext(p: str) -> str:
    name = str(p or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name


class SolutionInstaller:
    """Install a solution bundle into the current tenant."""

    def __init__(self, flask_app):
        self._app = flask_app

    # ────────────────────────────────────────────────────────────
    # Public API
    # ────────────────────────────────────────────────────────────

    def install(
        self,
        bundle_path: Path,
        *,
        options: Optional[InstallOptions] = None,
        auth_headers: Optional[Dict[str, str]] = None,
    ) -> InstallResult:
        options = options or InstallOptions()
        auth_headers = auth_headers or {}

        # Unpack into a temp dir we own for the duration of install.
        with tempfile.TemporaryDirectory(prefix="sol_install_") as tmp:
            tmp_path = Path(tmp)
            staging_root = self._stage_bundle(bundle_path, tmp_path)
            if staging_root is None:
                return InstallResult(
                    solution_id="",
                    solution_name="",
                    success=False,
                    errors=[f"Could not open or stage bundle: {bundle_path}"],
                )

            manifest_path = staging_root / MANIFEST_FILENAME
            if not manifest_path.exists():
                return InstallResult(
                    solution_id="",
                    solution_name="",
                    success=False,
                    errors=[f"No {MANIFEST_FILENAME} in bundle"],
                )

            manifest = SolutionManifest.from_json_file(manifest_path)
            result = InstallResult(
                solution_id=manifest.id, solution_name=manifest.name,
            )

            # Verify required credentials are present.
            missing = [
                c.placeholder
                for c in manifest.credentials
                if c.required and not (options.credentials or {}).get(c.placeholder)
            ]
            if missing:
                result.success = False
                result.errors.append(
                    f"Required credentials missing: {', '.join(missing)}"
                )
                return result

            # ── Dispatch per asset class ──
            self._install_agents(manifest, staging_root, auth_headers, options, result)
            self._install_tools(manifest, staging_root, auth_headers, options, result)
            self._install_workflows(manifest, staging_root, auth_headers, options, result)
            self._install_integrations(manifest, staging_root, auth_headers, options, result)
            self._install_connections(manifest, staging_root, auth_headers, options, result)
            self._install_environments(manifest, staging_root, auth_headers, options, result)
            self._install_knowledge(manifest, staging_root, auth_headers, options, result)
            self._install_seed_data(manifest, staging_root, options, result)

            # ── Build post-install actions list ──
            for action in manifest.post_install:
                result.post_install.append({
                    "type": action.type,
                    "target": action.target,
                    "label": action.label,
                })

            # Any asset that failed? Mark overall not-success but preserve
            # partial results so the UI can show what did install.
            if any(a.status == "failed" for a in result.assets):
                result.success = False
            if result.seed_result and result.seed_result.errors:
                result.success = False

            return result

    # ────────────────────────────────────────────────────────────
    # Staging
    # ────────────────────────────────────────────────────────────

    def _stage_bundle(self, bundle_path: Path, tmp: Path) -> Optional[Path]:
        """Extract (or copy) the bundle into a working directory.
        Accepts either a .zip file or an already-unzipped folder."""
        if bundle_path.is_dir():
            # Already unzipped (dev-mode bundled solution). Return as-is.
            return bundle_path
        if not bundle_path.is_file():
            logger.warning("Bundle not found: %s", bundle_path)
            return None
        try:
            with zipfile.ZipFile(bundle_path) as zf:
                # Validate no path traversal: reject entries that resolve
                # outside tmp.
                for info in zf.infolist():
                    target = (tmp / info.filename).resolve()
                    if tmp.resolve() not in target.parents and target != tmp.resolve():
                        logger.warning("Refusing unsafe path in bundle: %s", info.filename)
                        return None
                zf.extractall(tmp)
            return tmp
        except zipfile.BadZipFile as e:
            logger.warning("Bad bundle zip: %s", e)
            return None

    # ────────────────────────────────────────────────────────────
    # Asset installers
    # Each one is best-effort: records a result row, never raises.
    # ────────────────────────────────────────────────────────────

    def _install_agents(self, manifest, root, auth, options, result):
        agents_dir = root / "agents"
        if not agents_dir.exists():
            return
        for entry in sorted(agents_dir.iterdir()):
            if not entry.is_dir():
                continue
            name = entry.name
            final_name = name + options.name_suffix
            try:
                # Re-zip the per-agent folder in the format the existing
                # /import/agent/* routes expect (top-level agent_N/ folder).
                inner_zip = io.BytesIO()
                with zipfile.ZipFile(inner_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                    for f in entry.rglob("*"):
                        if f.is_file():
                            rel = f.relative_to(entry)
                            # existing route expects top-level agent_N/; we
                            # preserve the folder shape by using the agent
                            # name as the top-level key, which the existing
                            # importer handles for any top-level folder.
                            zf.write(f, f"{final_name}/{rel}")
                inner_zip.seek(0)

                # First analyze to detect name conflicts.
                analyze_resp = self._app.test_client().post(
                    "/import/agent/analyze",
                    data={"file": (inner_zip, f"{final_name}.zip")},
                    content_type="multipart/form-data",
                    headers=auth,
                )
                # analyze is informational; proceed to execute regardless.
                inner_zip.seek(0)
                exec_resp = self._app.test_client().post(
                    "/import/agent/execute",
                    data={
                        "file": (inner_zip, f"{final_name}.zip"),
                        "conflict_resolution": options.conflict_mode,
                    },
                    content_type="multipart/form-data",
                    headers=auth,
                )
                if exec_resp.status_code in (200, 201):
                    result.assets.append(
                        AssetResult(kind="agent", name=final_name, status="installed")
                    )
                else:
                    result.assets.append(
                        AssetResult(
                            kind="agent", name=final_name, status="failed",
                            detail=f"HTTP {exec_resp.status_code}: {exec_resp.data[:200]!r}",
                        )
                    )
            except Exception as e:
                logger.exception("agent install failed for %s", name)
                result.assets.append(
                    AssetResult(kind="agent", name=final_name, status="failed", detail=str(e))
                )

    def _install_tools(self, manifest, root, auth, options, result):
        tools_dir = root / "tools"
        if not tools_dir.exists():
            return
        for entry in sorted(tools_dir.iterdir()):
            if not entry.is_dir():
                continue
            name = entry.name
            final_name = name + options.name_suffix
            try:
                inner_zip = io.BytesIO()
                with zipfile.ZipFile(inner_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                    for f in entry.rglob("*"):
                        if f.is_file():
                            rel = f.relative_to(entry)
                            zf.write(f, f"{final_name}/{rel}")
                inner_zip.seek(0)
                resp = self._app.test_client().post(
                    "/api/tool/import",
                    data={"file": (inner_zip, f"{final_name}.zip")},
                    content_type="multipart/form-data",
                    headers=auth,
                )
                if resp.status_code in (200, 201):
                    result.assets.append(
                        AssetResult(kind="tool", name=final_name, status="installed")
                    )
                else:
                    result.assets.append(
                        AssetResult(
                            kind="tool", name=final_name, status="failed",
                            detail=f"HTTP {resp.status_code}: {resp.data[:200]!r}",
                        )
                    )
            except Exception as e:
                logger.exception("tool install failed for %s", name)
                result.assets.append(
                    AssetResult(kind="tool", name=final_name, status="failed", detail=str(e))
                )

    def _install_workflows(self, manifest, root, auth, options, result):
        wf_dir = root / "workflows"
        if not wf_dir.exists():
            return
        for entry in sorted(wf_dir.iterdir()):
            if not entry.is_file() or entry.suffix.lower() != ".json":
                continue
            final_name = entry.stem + options.name_suffix
            try:
                workflow_json = entry.read_text(encoding="utf-8")
                resp = self._app.test_client().post(
                    "/api/solutions/workflows/import",
                    json={
                        "name": final_name,
                        "workflow": json.loads(workflow_json),
                        "conflict_mode": options.conflict_mode,
                    },
                    headers=auth,
                )
                if resp.status_code in (200, 201):
                    result.assets.append(
                        AssetResult(kind="workflow", name=final_name, status="installed")
                    )
                else:
                    result.assets.append(
                        AssetResult(
                            kind="workflow", name=final_name, status="failed",
                            detail=f"HTTP {resp.status_code}: {resp.data[:200]!r}",
                        )
                    )
            except Exception as e:
                logger.exception("workflow install failed for %s", entry.name)
                result.assets.append(
                    AssetResult(kind="workflow", name=final_name, status="failed", detail=str(e))
                )

    def _install_integrations(self, manifest, root, auth, options, result):
        itg_dir = root / "integrations"
        if not itg_dir.exists():
            return
        for entry in sorted(itg_dir.iterdir()):
            if not entry.is_file() or entry.suffix.lower() != ".json":
                continue
            final_name = entry.stem + options.name_suffix
            try:
                raw = entry.read_text(encoding="utf-8")
                resolved = resolve_placeholders(raw, options.credentials or {})
                config = json.loads(resolved)
                resp = self._app.test_client().post(
                    "/api/solutions/integrations/install",
                    json={"name": final_name, "config": config, "conflict_mode": options.conflict_mode},
                    headers=auth,
                )
                if resp.status_code in (200, 201):
                    result.assets.append(
                        AssetResult(kind="integration", name=final_name, status="installed")
                    )
                else:
                    result.assets.append(
                        AssetResult(
                            kind="integration", name=final_name, status="failed",
                            detail=f"HTTP {resp.status_code}: {resp.data[:200]!r}",
                        )
                    )
            except Exception as e:
                logger.exception("integration install failed for %s", entry.name)
                result.assets.append(
                    AssetResult(kind="integration", name=final_name, status="failed", detail=str(e))
                )

    def _install_connections(self, manifest, root, auth, options, result):
        conn_dir = root / "connections"
        if not conn_dir.exists():
            return
        for entry in sorted(conn_dir.iterdir()):
            if not entry.is_file() or entry.suffix.lower() != ".json":
                continue
            final_name = entry.stem + options.name_suffix
            try:
                raw = entry.read_text(encoding="utf-8")
                resolved = resolve_placeholders(raw, options.credentials or {})
                scaffold = json.loads(resolved)
                # Ensure final connection name reflects suffix
                scaffold["connection_name"] = final_name
                resp = self._app.test_client().post(
                    "/api/solutions/connections/install",
                    json={
                        "scaffold": scaffold,
                        "conflict_mode": options.conflict_mode,
                        "solution_id": getattr(manifest, "id", "solution"),
                    },
                    headers=auth,
                )
                if resp.status_code in (200, 201):
                    body = resp.get_json() or {}
                    result.assets.append(
                        AssetResult(
                            kind="connection", name=final_name, status="installed",
                            resource_id=body.get("connection_id"),
                        )
                    )
                else:
                    result.assets.append(
                        AssetResult(
                            kind="connection", name=final_name, status="failed",
                            detail=f"HTTP {resp.status_code}: {resp.data[:200]!r}",
                        )
                    )
            except Exception as e:
                logger.exception("connection install failed for %s", entry.name)
                result.assets.append(
                    AssetResult(kind="connection", name=final_name, status="failed", detail=str(e))
                )

    def _install_environments(self, manifest, root, auth, options, result):
        env_dir = root / "environments"
        if not env_dir.exists():
            return
        for entry in sorted(env_dir.iterdir()):
            if not entry.is_file() or entry.suffix.lower() != ".zip":
                continue
            final_name = entry.stem + options.name_suffix
            try:
                body = entry.read_bytes()
                resp = self._app.test_client().post(
                    "/environments/api/import",
                    data={"file": (io.BytesIO(body), f"{final_name}.zip")},
                    content_type="multipart/form-data",
                    headers=auth,
                )
                if resp.status_code in (200, 201):
                    result.assets.append(
                        AssetResult(kind="environment", name=final_name, status="installed")
                    )
                else:
                    result.assets.append(
                        AssetResult(
                            kind="environment", name=final_name, status="failed",
                            detail=f"HTTP {resp.status_code}: {resp.data[:200]!r}",
                        )
                    )
            except Exception as e:
                logger.exception("environment install failed for %s", entry.name)
                result.assets.append(
                    AssetResult(kind="environment", name=final_name, status="failed", detail=str(e))
                )

    def _install_knowledge(self, manifest, root, auth, options, result):
        know_dir = root / "knowledge"
        if not know_dir.exists():
            return
        # Knowledge entries come in pairs: <name>.meta.json + <name>.
        for meta_file in sorted(know_dir.glob("*.meta.json")):
            name = meta_file.name[: -len(".meta.json")]
            blob_file = know_dir / name
            if not blob_file.exists():
                result.assets.append(
                    AssetResult(kind="knowledge", name=name, status="skipped", detail="blob missing")
                )
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                body = blob_file.read_bytes()
                resp = self._app.test_client().post(
                    "/api/solutions/knowledge/import",
                    data={
                        "file": (io.BytesIO(body), meta.get("filename") or name),
                        "metadata": json.dumps(meta),
                    },
                    content_type="multipart/form-data",
                    headers=auth,
                )
                if resp.status_code in (200, 201):
                    result.assets.append(
                        AssetResult(kind="knowledge", name=name, status="installed")
                    )
                else:
                    result.assets.append(
                        AssetResult(
                            kind="knowledge", name=name, status="failed",
                            detail=f"HTTP {resp.status_code}: {resp.data[:200]!r}",
                        )
                    )
            except Exception as e:
                logger.exception("knowledge install failed for %s", name)
                result.assets.append(
                    AssetResult(kind="knowledge", name=name, status="failed", detail=str(e))
                )

    def _install_seed_data(self, manifest, root, options, result):
        data_dir = root / "data"
        if not data_dir.exists():
            return

        schema_path = data_dir / "schema.sql"
        schema_bytes: Optional[bytes] = None
        if schema_path.is_file():
            try:
                schema_bytes = schema_path.read_bytes()
            except OSError as e:
                result.errors.append(f"could not read schema.sql: {e}")

        seeds_dir = data_dir / "seeds"
        seeds: Dict[str, bytes] = {}
        if seeds_dir.is_dir():
            for f in sorted(seeds_dir.glob("*.csv")):
                try:
                    seeds[f.name] = f.read_bytes()
                except OSError as e:
                    result.errors.append(f"could not read seed {f.name}: {e}")

        # Also copy sample_inputs to a known per-solution folder the
        # installer can point workflows at. For MVP we leave them staged
        # for the user to wire up; future work can auto-bind.
        sample_dir = data_dir / "sample_inputs"
        if sample_dir.is_dir():
            try:
                import shutil
                target = _sample_inputs_dest(manifest.id)
                target.mkdir(parents=True, exist_ok=True)
                for f in sample_dir.glob("*"):
                    if f.is_file():
                        shutil.copy2(f, target / f.name)
                result.assets.append(
                    AssetResult(
                        kind="seed", name="sample_inputs", status="installed",
                        detail=f"copied to {target}",
                    )
                )
            except Exception as e:
                logger.exception("sample_inputs copy failed")
                result.assets.append(
                    AssetResult(kind="seed", name="sample_inputs", status="failed", detail=str(e))
                )

        if schema_bytes or seeds:
            seed_result = load_seed_data(
                schema_sql=schema_bytes,
                seed_csvs=seeds,
                target_connection=options.target_connection,
            )
            result.seed_result = seed_result
            if seed_result.skipped:
                result.assets.append(
                    AssetResult(
                        kind="seed", name="database", status="skipped",
                        detail="no target connection (sandbox mode)",
                    )
                )
            else:
                status = "installed" if not seed_result.errors else "failed"
                detail = (
                    f"schema stmts: {seed_result.schema_statements_run}, "
                    f"seed rows: {sum(seed_result.seeds_loaded.values())}"
                )
                if seed_result.errors:
                    detail += f", errors: {len(seed_result.errors)}"
                result.assets.append(
                    AssetResult(kind="seed", name="database", status=status, detail=detail)
                )


# ────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────

def _load_manifest(bundle_path: Path) -> Optional[SolutionManifest]:
    if bundle_path.is_dir():
        mp = bundle_path / MANIFEST_FILENAME
        if not mp.exists():
            return None
        try:
            return SolutionManifest.from_json_file(mp)
        except Exception:
            return None
    if not bundle_path.is_file():
        return None
    try:
        with zipfile.ZipFile(bundle_path) as zf:
            if MANIFEST_FILENAME not in zf.namelist():
                return None
            with zf.open(MANIFEST_FILENAME) as f:
                return SolutionManifest.from_dict(json.loads(f.read().decode("utf-8")))
    except (zipfile.BadZipFile, json.JSONDecodeError, OSError):
        return None


def _sample_inputs_dest(solution_id: str) -> Path:
    """Where bundled sample inputs get staged after install.

    Reads config lazily so tests can override APP_ROOT.
    """
    try:
        import config as cfg  # type: ignore
        root = Path(getattr(cfg, "APP_ROOT", "."))
    except Exception:
        root = Path(".")
    return root / "data" / "solutions_installed" / solution_id / "sample_inputs"
