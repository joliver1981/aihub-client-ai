"""
Automation asset manager — CRUD, immutable versioning, promote, secret scan.

Layout on disk (inside APP_ROOT, mirroring agent_environments):

    automations/tenant_<tenant>/<automation_id>/
        main.py            working copy (== versions/v<current_version>/)
        manifest.json      working copy
        versions/v<N>/     immutable snapshot per save (code + manifest + samples/)
        runs/<run_id>/     per-run workdir (created by runner.py)

Versions are append-only: a save always creates v<current_version+1>; an
existing v<N> is never rewritten. Schedules and API runs execute the PINNED
version (promote moves the pin) so editing code never silently changes what a
schedule runs.

DB access is isolated in _db_* methods so unit tests can stub them.
"""

import json
import logging
import os
import re
import shutil
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pyodbc

from CommonUtils import get_app_path, get_db_connection_string

logger = logging.getLogger(__name__)

MAX_TIMEOUT_SECONDS = 86400
DEFAULT_TIMEOUT_SECONDS = 600

VALID_OUTPUT_KINDS = {"file", "sftp_upload", "ftp_upload", "http_upload"}
VALID_INPUT_TYPES = {"string", "int", "float", "bool", "path"}
VALID_TRIGGERS = {"manual", "api", "dry_run", "schedule", "workflow", "email", "webhook"}

# Credential material must never appear in automation code — scripts read
# connections/secrets from the env vars the runner injects (P0) / the
# aihub_runtime SDK (P2). Patterns are deliberately few and high-signal.
_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(pwd|password|passwd)\s*=\s*[\"'][^\"']{3,}[\"']"),
    re.compile(r"(?i)\b(pwd|password|passwd)\s*=\s*(?![\"']?\{)[A-Za-z0-9!@#$%^&*_+-]{3,}\s*;"),  # ODBC "PWD=...;"
    re.compile(r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token)\s*=\s*[\"'][A-Za-z0-9_\-]{16,}[\"']"),
    # URL with embedded LITERAL credentials, e.g. sftp://user:pass@host
    # (AIHUB-0033 F5) — the value form of an SFTP/DB secret; read it via
    # aihub.secret() instead. Excludes {}$%() from the user:pass segments so a
    # URL BUILT from variables (f-string {x}, %s, $x, .format()) is not a false
    # positive — only hard-coded literals match.
    re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*)://[^/\s:@\"'{}$%()]+:[^/\s:@\"'{}$%()]+@"),
]


def scan_for_secrets(code: str) -> List[str]:
    """Return human-readable descriptions of credential-looking literals in
    `code` (empty list = clean). Values read from os.environ are fine."""
    findings = []
    for lineno, line in enumerate((code or "").splitlines(), 1):
        if "os.environ" in line or "getenv" in line:
            continue  # reading injected env vars is the sanctioned pattern
        for pat in _SECRET_PATTERNS:
            m = pat.search(line)
            if m:
                findings.append(f"line {lineno}: credential-looking literal ({m.group(1)}=...)")
                break
    return findings


def validate_manifest(manifest: Dict) -> Tuple[bool, List[str]]:
    """Validate an automation manifest. Returns (ok, errors).

    Unknown top-level keys are tolerated (forward compatibility); wrong types
    on known keys are errors.
    """
    errors: List[str] = []
    if not isinstance(manifest, dict):
        return False, ["manifest must be a JSON object"]

    name = manifest.get("name")
    if not name or not isinstance(name, str):
        errors.append("'name' is required and must be a string")

    entrypoint = manifest.get("entrypoint", "main.py")
    if not isinstance(entrypoint, str) or not entrypoint.endswith(".py") or "/" in entrypoint or "\\" in entrypoint:
        errors.append("'entrypoint' must be a bare .py filename (no paths)")

    timeout = manifest.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(timeout, int) or not (1 <= timeout <= MAX_TIMEOUT_SECONDS):
        errors.append(f"'timeout_seconds' must be an int in [1, {MAX_TIMEOUT_SECONDS}]")

    for key in ("connections", "secrets", "packages"):
        val = manifest.get(key, [])
        if not isinstance(val, list) or not all(isinstance(v, str) and v.strip() for v in val):
            errors.append(f"'{key}' must be a list of non-empty strings")

    inputs = manifest.get("inputs", [])
    if not isinstance(inputs, list):
        errors.append("'inputs' must be a list")
    else:
        for i, inp in enumerate(inputs):
            if not isinstance(inp, dict) or not isinstance(inp.get("name"), str) or not inp.get("name"):
                errors.append(f"inputs[{i}]: needs a string 'name'")
            elif inp.get("type", "string") not in VALID_INPUT_TYPES:
                errors.append(f"inputs[{i}] ('{inp.get('name')}'): type must be one of {sorted(VALID_INPUT_TYPES)}")

    outputs = manifest.get("outputs", [])
    if not isinstance(outputs, list):
        errors.append("'outputs' must be a list")
    else:
        for i, out in enumerate(outputs):
            if not isinstance(out, dict):
                errors.append(f"outputs[{i}]: must be an object")
                continue
            kind = out.get("kind")
            if kind not in VALID_OUTPUT_KINDS:
                errors.append(f"outputs[{i}]: 'kind' must be one of {sorted(VALID_OUTPUT_KINDS)}")
            if kind == "file" and not isinstance(out.get("path"), str):
                errors.append(f"outputs[{i}]: file outputs need a string 'path' (relative to the run workdir)")
            verify = out.get("verify", {})
            if verify and not isinstance(verify, dict):
                errors.append(f"outputs[{i}]: 'verify' must be an object")

    return (not errors), errors


class AutomationManager:
    """Registry + filesystem owner for Automation assets."""

    def __init__(self, tenant_id: Optional[str] = None, connection_string: Optional[str] = None):
        self.tenant_id = tenant_id if tenant_id is not None else os.getenv("API_KEY")
        self.connection_string = connection_string or get_db_connection_string()
        self.base_path = get_app_path("automations", f"tenant_{self.tenant_id}")
        os.makedirs(self.base_path, exist_ok=True)

    # ------------------------------------------------------------------ paths

    def automation_dir(self, automation_id: str) -> str:
        return os.path.join(self.base_path, automation_id)

    def version_dir(self, automation_id: str, version: int) -> str:
        return os.path.join(self.automation_dir(automation_id), "versions", f"v{version}")

    def runs_dir(self, automation_id: str) -> str:
        return os.path.join(self.automation_dir(automation_id), "runs")

    # --------------------------------------------------------------------- db

    def _db_conn(self):
        conn = pyodbc.connect(self.connection_string)
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
        cursor.close()
        return conn

    def ensure_tables(self):
        """Idempotently create Automations/AutomationRuns (dev convenience;
        migrations/014_automations.sql is the production record)."""
        ddl = """
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[Automations]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[Automations] (
        automation_id   VARCHAR(36)    NOT NULL,
        name            NVARCHAR(200)  NOT NULL,
        description     NVARCHAR(MAX)  NULL,
        owner_user_id   INT            NOT NULL,
        environment_id  VARCHAR(100)   NULL,
        current_version INT            NOT NULL DEFAULT 0,
        pinned_version  INT            NOT NULL DEFAULT 0,
        status          VARCHAR(20)    NOT NULL DEFAULT 'active',
        created_at      DATETIME       NOT NULL DEFAULT GETUTCDATE(),
        updated_at      DATETIME       NOT NULL DEFAULT GETUTCDATE(),
        TenantId        INT            NULL DEFAULT (CONVERT([int], session_context(N'TenantId'))),
        CONSTRAINT PK_Automations PRIMARY KEY CLUSTERED (automation_id ASC)
    );
    CREATE UNIQUE NONCLUSTERED INDEX UX_Automations_TenantName
        ON [dbo].[Automations] (TenantId, name) WHERE status = 'active';
END
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[AutomationRuns]') AND type = 'U')
BEGIN
    CREATE TABLE [dbo].[AutomationRuns] (
        run_id          VARCHAR(36)    NOT NULL,
        automation_id   VARCHAR(36)    NOT NULL,
        version         INT            NOT NULL,
        trigger_source  VARCHAR(20)    NOT NULL,
        status          VARCHAR(20)    NOT NULL DEFAULT 'running',
        exit_code       INT            NULL,
        requested_by    INT            NULL,
        inputs_json     NVARCHAR(MAX)  NULL,
        verify_report   NVARCHAR(MAX)  NULL,
        output_files    NVARCHAR(MAX)  NULL,
        log_path        NVARCHAR(500)  NULL,
        error           NVARCHAR(MAX)  NULL,
        started_at      DATETIME       NOT NULL DEFAULT GETUTCDATE(),
        finished_at     DATETIME       NULL,
        TenantId        INT            NULL DEFAULT (CONVERT([int], session_context(N'TenantId'))),
        CONSTRAINT PK_AutomationRuns PRIMARY KEY CLUSTERED (run_id ASC),
        CONSTRAINT FK_AutomationRuns_Automation FOREIGN KEY (automation_id)
            REFERENCES [dbo].[Automations] (automation_id) ON DELETE CASCADE
    );
    CREATE NONCLUSTERED INDEX IX_AutomationRuns_Automation
        ON [dbo].[AutomationRuns] (automation_id, started_at DESC);
    CREATE NONCLUSTERED INDEX IX_AutomationRuns_Running
        ON [dbo].[AutomationRuns] (automation_id, status) INCLUDE (started_at)
        WHERE status = 'running';
END
"""
        try:
            conn = self._db_conn()
            cursor = conn.cursor()
            cursor.execute(ddl)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"ensure_tables failed: {e}")

    def _db_insert_automation(self, row: Dict):
        conn = self._db_conn()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO Automations
               (automation_id, name, description, owner_user_id, environment_id)
               VALUES (?, ?, ?, ?, ?)""",
            row["automation_id"], row["name"], row.get("description"),
            row["owner_user_id"], row.get("environment_id"),
        )
        conn.commit()
        conn.close()

    def _db_get_automation(self, automation_id: str) -> Optional[Dict]:
        conn = self._db_conn()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT automation_id, name, description, owner_user_id, environment_id,
                      current_version, pinned_version, status, created_at, updated_at
               FROM Automations WHERE automation_id = ? AND status <> 'deleted'""",
            automation_id,
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return {
            "automation_id": row.automation_id,
            "name": row.name,
            "description": row.description,
            "owner_user_id": row.owner_user_id,
            "environment_id": row.environment_id,
            "current_version": row.current_version,
            "pinned_version": row.pinned_version,
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    def _db_list_automations(self) -> List[Dict]:
        conn = self._db_conn()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT automation_id, name, description, owner_user_id, environment_id,
                      current_version, pinned_version, status, created_at, updated_at
               FROM Automations WHERE status <> 'deleted' ORDER BY name"""
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                "automation_id": r.automation_id,
                "name": r.name,
                "description": r.description,
                "owner_user_id": r.owner_user_id,
                "environment_id": r.environment_id,
                "current_version": r.current_version,
                "pinned_version": r.pinned_version,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]

    def _db_update_automation(self, automation_id: str, fields: Dict):
        if not fields:
            return
        allowed = {"description", "environment_id", "current_version", "pinned_version", "status"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"not updatable: {sorted(bad)}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn = self._db_conn()
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE Automations SET {sets}, updated_at = GETUTCDATE() WHERE automation_id = ?",
            *fields.values(), automation_id,
        )
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------- CRUD

    def create_automation(self, name: str, description: str, owner_user_id: int,
                          environment_id: Optional[str] = None) -> Tuple[bool, Optional[Dict], Optional[str]]:
        """Create the DB row + folder skeleton. Returns (ok, automation, error).

        environment_id: pass an existing agent-environment id, or None — the
        API layer provisions a dedicated environment (one env per automation)
        and PATCHes it in; runs without one fall back to the bundle python.
        """
        name = (name or "").strip()
        if not name or len(name) > 200:
            return False, None, "name is required (max 200 chars)"
        if any(a["name"].lower() == name.lower() for a in self._db_list_automations()):
            return False, None, f"an automation named '{name}' already exists"

        automation_id = str(uuid.uuid4())
        adir = self.automation_dir(automation_id)
        try:
            os.makedirs(os.path.join(adir, "versions"), exist_ok=False)
            os.makedirs(os.path.join(adir, "runs"), exist_ok=True)
            skeleton_manifest = {
                "name": name,
                "description": description or "",
                "entrypoint": "main.py",
                "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
                "inputs": [],
                "connections": [],
                "secrets": [],
                "packages": [],
                "outputs": [],
            }
            with open(os.path.join(adir, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(skeleton_manifest, f, indent=2)
            with open(os.path.join(adir, "main.py"), "w", encoding="utf-8") as f:
                f.write("# Automation: " + name + "\n# Generated code goes here.\n")

            self._db_insert_automation({
                "automation_id": automation_id,
                "name": name,
                "description": description,
                "owner_user_id": owner_user_id,
                "environment_id": environment_id,
            })
        except Exception as e:
            shutil.rmtree(adir, ignore_errors=True)
            logger.error(f"create_automation('{name}') failed: {e}")
            return False, None, str(e)

        return True, self._db_get_automation(automation_id), None

    def get_automation(self, automation_id: str) -> Optional[Dict]:
        return self._db_get_automation(automation_id)

    def list_automations(self) -> List[Dict]:
        return self._db_list_automations()

    def delete_automation(self, automation_id: str) -> Tuple[bool, Optional[str]]:
        """Soft delete: DB row survives for run-history joins; files stay on
        disk (the folder is the audit trail). Frees the name for reuse."""
        if not self._db_get_automation(automation_id):
            return False, "not found"
        self._db_update_automation(automation_id, {"status": "deleted"})
        return True, None

    def set_environment(self, automation_id: str, environment_id: str):
        self._db_update_automation(automation_id, {"environment_id": environment_id})

    # ------------------------------------------------------------- versioning

    def save_version(self, automation_id: str, code: str,
                     manifest: Optional[Dict] = None) -> Tuple[bool, Optional[int], List[str]]:
        """Save code (+ optionally a new manifest) as the next immutable
        version. Returns (ok, new_version, errors). Does NOT move the pin."""
        auto = self._db_get_automation(automation_id)
        if not auto:
            return False, None, ["not found"]

        findings = scan_for_secrets(code)
        if findings:
            return False, None, (
                ["Credential material must not be hard-coded in automation code. "
                 "Read connections/secrets from the env vars the runner injects "
                 "(AIHUB_CONN_<NAME> / AIHUB_SECRET_<NAME>)."] + findings
            )

        adir = self.automation_dir(automation_id)
        if manifest is None:
            manifest = self.get_manifest(automation_id) or {}
        ok, errors = validate_manifest(manifest)
        if not ok:
            return False, None, errors

        new_version = auto["current_version"] + 1
        vdir = self.version_dir(automation_id, new_version)
        if os.path.exists(vdir):
            # versions are append-only; an existing folder means DB/disk drift
            return False, None, [f"version folder v{new_version} already exists — disk/DB drift, refusing to overwrite"]

        try:
            os.makedirs(os.path.join(vdir, "samples"), exist_ok=True)
            entrypoint = manifest.get("entrypoint", "main.py")
            with open(os.path.join(vdir, entrypoint), "w", encoding="utf-8") as f:
                f.write(code)
            with open(os.path.join(vdir, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
            # refresh the working copy
            with open(os.path.join(adir, entrypoint), "w", encoding="utf-8") as f:
                f.write(code)
            with open(os.path.join(adir, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
            self._db_update_automation(automation_id, {"current_version": new_version})
        except Exception as e:
            shutil.rmtree(vdir, ignore_errors=True)
            logger.error(f"save_version({automation_id}) failed: {e}")
            return False, None, [str(e)]

        return True, new_version, []

    def get_code(self, automation_id: str, version: Optional[int] = None) -> Optional[str]:
        auto = self._db_get_automation(automation_id)
        if not auto:
            return None
        manifest = self.get_manifest(automation_id, version) or {}
        entrypoint = manifest.get("entrypoint", "main.py")
        if version:
            path = os.path.join(self.version_dir(automation_id, version), entrypoint)
        else:
            path = os.path.join(self.automation_dir(automation_id), entrypoint)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def get_manifest(self, automation_id: str, version: Optional[int] = None) -> Optional[Dict]:
        if version:
            path = os.path.join(self.version_dir(automation_id, version), "manifest.json")
        else:
            path = os.path.join(self.automation_dir(automation_id), "manifest.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def list_versions(self, automation_id: str) -> List[int]:
        vroot = os.path.join(self.automation_dir(automation_id), "versions")
        if not os.path.isdir(vroot):
            return []
        versions = []
        for name in os.listdir(vroot):
            if name.startswith("v") and name[1:].isdigit():
                versions.append(int(name[1:]))
        return sorted(versions)

    def add_sample(self, automation_id: str, version: int, filename: str, content: bytes) -> Tuple[bool, Optional[str]]:
        """Attach a sample input file to a version (dry-runs seed from these,
        so every version's test is reproducible)."""
        safe = os.path.basename(filename)
        if not safe or safe != filename:
            return False, "filename must be a bare name"
        sdir = os.path.join(self.version_dir(automation_id, version), "samples")
        if not os.path.isdir(os.path.dirname(sdir)):
            return False, f"version v{version} does not exist"
        os.makedirs(sdir, exist_ok=True)
        with open(os.path.join(sdir, safe), "wb") as f:
            f.write(content)
        return True, None

    # ---------------------------------------------------------------- promote

    def promote(self, automation_id: str, version: Optional[int] = None) -> Tuple[bool, Optional[int], Optional[str]]:
        """Move the pin. Default: promote current_version (\"promote latest\").
        Scheduled/API runs always execute the pinned version."""
        auto = self._db_get_automation(automation_id)
        if not auto:
            return False, None, "not found"
        target = version if version is not None else auto["current_version"]
        if target < 1:
            return False, None, "nothing to promote — save a version first"
        if target not in self.list_versions(automation_id):
            return False, None, f"version v{target} does not exist"
        self._db_update_automation(automation_id, {"pinned_version": target})
        return True, target, None
