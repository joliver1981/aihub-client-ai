"""
Code Flow manager — authoring CRUD over a code flow that is PERSISTED as a
workflow in the Workflows table (marked kind='code_flow'), so it runs and
schedules on the existing engine with zero new storage.

The authored DEFINITION (steps + edges — the source of truth for editing) is
embedded in the same workflow_data blob under 'definition', alongside the
compiled nodes/connections the engine executes. One row, one source of truth:
save() compiles the definition into Code Step nodes; load() hands back both.

DB access is isolated in _db_* methods so unit tests stub them.
"""

import json
import logging
import os
import threading
from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple

import pyodbc

from CommonUtils import get_app_path, get_db_connection_string
from . import compiler

logger = logging.getLogger(__name__)

# Serialize the read-modify-write of a single flow's definition WITHIN a process
# so two overlapping authoring calls (add_step/wire/update_step_code) can't lose
# each other's edit (last-writer-wins). Keyed by (tenant, flow name). Cross-
# process durability would need an optimistic version check on the MERGE; the
# only cross-process writer (the scheduler) never edits definitions.
_EDIT_LOCKS: Dict[tuple, threading.Lock] = {}
_EDIT_LOCKS_GUARD = threading.Lock()


@contextmanager
def _edit_lock(tenant_id, name):
    key = (tenant_id, name)
    with _EDIT_LOCKS_GUARD:
        lock = _EDIT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _EDIT_LOCKS[key] = lock
    with lock:
        yield


class CodeFlowManager:
    def __init__(self, tenant_id: Optional[str] = None, connection_string: Optional[str] = None):
        self.tenant_id = tenant_id if tenant_id is not None else os.getenv("API_KEY")
        self.connection_string = connection_string or get_db_connection_string()

    # --------------------------------------------------------------------- db

    def _db_conn(self):
        conn = pyodbc.connect(self.connection_string)
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
        cursor.close()
        return conn

    def _db_save(self, name: str, workflow_data: Dict) -> int:
        """MERGE the code flow into Workflows by name (upsert), return its id.
        Mirrors app.save_workflow_to_database's MERGE-on-workflow_name."""
        blob = json.dumps(workflow_data)
        conn = self._db_conn()
        cursor = conn.cursor()
        cursor.execute(
            """MERGE INTO Workflows AS target
               USING (SELECT ? AS workflow_name, ? AS workflow_data) AS source
               ON target.workflow_name = source.workflow_name
               WHEN MATCHED THEN UPDATE SET workflow_data = source.workflow_data,
                    last_modified = GETUTCDATE(), version = ISNULL(target.version, 0) + 1
               WHEN NOT MATCHED THEN INSERT (workflow_name, workflow_data, last_modified, version)
                    VALUES (source.workflow_name, source.workflow_data, GETUTCDATE(), 1);""",
            name, blob,
        )
        conn.commit()
        cursor.execute("SELECT id FROM Workflows WHERE workflow_name = ?", name)
        row = cursor.fetchone()
        conn.close()
        return int(row[0]) if row else 0

    def _db_exists(self, name: str) -> bool:
        """Does ANY Workflows row with this name exist? Parse-independent (a row
        with corrupt/NULL workflow_data still counts) so create_code_flow's dup
        guard can't be defeated by an unparseable same-name row it would then
        clobber via the name-keyed MERGE."""
        conn = self._db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM Workflows WHERE workflow_name = ?", name)
        row = cursor.fetchone()
        conn.close()
        return row is not None

    def _db_load(self, name: str) -> Optional[Tuple[int, Dict]]:
        conn = self._db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT id, workflow_data FROM Workflows WHERE workflow_name = ?", name)
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        try:
            return int(row[0]), json.loads(row[1])
        except (ValueError, TypeError):
            return None

    def _db_list(self) -> List[Dict]:
        conn = self._db_conn()
        cursor = conn.cursor()
        # JSON_VALUE filters to code flows without a new table or name prefix.
        cursor.execute(
            "SELECT id, workflow_name, workflow_data FROM Workflows "
            "WHERE JSON_VALUE(workflow_data, '$.kind') = 'code_flow' ORDER BY workflow_name"
        )
        rows = cursor.fetchall()
        conn.close()
        out = []
        for r in rows:
            try:
                data = json.loads(r[2])
            except (ValueError, TypeError):
                data = {}
            defn = data.get("definition") or {}
            out.append({"workflow_id": int(r[0]), "name": r[1],
                        "step_count": len(defn.get("steps") or []),
                        "description": defn.get("description", "")})
        return out

    def _db_delete(self, name: str) -> bool:
        conn = self._db_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM Workflows WHERE workflow_name = ? "
                       "AND JSON_VALUE(workflow_data, '$.kind') = 'code_flow'", name)
        changed = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return changed

    # ------------------------------------------------------------- authoring

    def _save_definition(self, defn: Dict) -> Tuple[int, Dict]:
        """Compile the definition, embed it, persist, return (workflow_id, workflow_data)."""
        wf = compiler.compile_to_workflow(defn)
        wf["definition"] = defn
        wid = self._db_save(defn["name"], wf)
        return wid, wf

    def create_code_flow(self, name: str, description: str = "") -> Tuple[bool, Optional[Dict], Optional[str]]:
        name = (name or "").strip()
        if not name:
            return False, None, "name is required"
        if self._db_exists(name):
            return False, None, f"a code flow (or workflow) named '{name}' already exists"
        defn = {"name": name, "description": description or "", "steps": [], "edges": []}
        wid, _ = self._save_definition(defn)
        return True, {"name": name, "workflow_id": wid, "steps": [], "edges": []}, None

    def _load_defn(self, name: str) -> Optional[Tuple[int, Dict]]:
        loaded = self._db_load(name)
        if not loaded:
            return None
        wid, data = loaded
        if data.get("kind") != compiler.CODE_FLOW_KIND:  # never treat a plain workflow as a code flow
            return None
        defn = data.get("definition")
        if not isinstance(defn, dict):
            return None
        return wid, defn

    def add_step(self, name: str, step_name: str, code: str,
                 connections: Optional[List[str]] = None, secrets: Optional[List[str]] = None,
                 packages: Optional[List[str]] = None, inputs: Optional[List[Dict]] = None,
                 outputs: Optional[List[Dict]] = None, timeout: int = 600,
                 continue_on_error: bool = False,
                 allow_unverified: bool = False,
                 unverified_consent: bool = False) -> Tuple[bool, Optional[str], Optional[str]]:
        # save-time credential-literal scan (reuse the automations scanner) —
        # outside the lock since it only inspects the incoming code arg.
        from automations.manager import scan_for_secrets
        findings = scan_for_secrets(code)
        if findings:
            return False, None, ("code contains credential-looking literals — read connections/"
                                 "secrets via aihub.connection()/secret() instead: " + "; ".join(findings))
        lint = compiler.lint_input_names(code, inputs)   # AIHUB-0037 name-mismatch guard
        if lint:
            return False, None, lint
        lint = compiler.lint_transfer_honesty(code, outputs)   # AIHUB-0040 placeholder guard
        if lint:
            return False, None, lint
        # AIHUB-0040: allow_unverified suppresses output verification — it cannot be
        # self-granted by the authoring agent. It requires explicit user consent,
        # which is recorded on the step for audit.
        if allow_unverified and not unverified_consent:
            return False, None, (
                "allow_unverified=True disables output verification and cannot be set without "
                "the user's explicit consent. Ask the user to approve skipping verification for "
                "this step; only after they explicitly agree, retry with "
                "user_approved_unverified=true (their consent is recorded on the step). "
                "Prefer fixing verification instead: declare the output with a 'name' and "
                '"verify": {"remote_listing": true}.')
        with _edit_lock(self.tenant_id, name):
            loaded = self._load_defn(name)
            if not loaded:
                return False, None, "code flow not found"
            _wid, defn = loaded
            step = {
                "id": compiler.new_step_id(), "name": step_name or "step", "code": code,
                "connections": connections or [], "secrets": secrets or [],
                "packages": packages or [], "inputs": inputs or [], "outputs": outputs or [],
                "timeout": int(timeout or 600), "continueOnError": bool(continue_on_error),
                "allowUnverified": bool(allow_unverified),
            }
            if allow_unverified:
                step["unverifiedConsent"] = "user"   # AIHUB-0040 audit marker
            defn.setdefault("steps", []).append(step)
            if not defn.get("start"):
                defn["start"] = step["id"]
            self._save_definition(defn)
            return True, step["id"], None

    def update_step_code(self, name: str, step_id: str, code: str) -> Tuple[bool, Optional[str]]:
        from automations.manager import scan_for_secrets
        findings = scan_for_secrets(code)
        if findings:
            return False, "code contains credential-looking literals: " + "; ".join(findings)
        with _edit_lock(self.tenant_id, name):
            loaded = self._load_defn(name)
            if not loaded:
                return False, "code flow not found"
            _wid, defn = loaded
            for s in defn.get("steps", []):
                if s.get("id") == step_id:
                    lint = compiler.lint_input_names(code, s.get("inputs"))  # AIHUB-0037
                    if lint:
                        return False, lint
                    lint = compiler.lint_transfer_honesty(code, s.get("outputs"))  # AIHUB-0040
                    if lint:
                        return False, lint
                    s["code"] = code
                    self._save_definition(defn)
                    return True, None
            return False, f"step '{step_id}' not found"

    def wire(self, name: str, from_step: str, to_step: str, on: str = "pass") -> Tuple[bool, Optional[str]]:
        if on not in ("pass", "fail", "complete"):
            return False, "'on' must be pass, fail, or complete"
        with _edit_lock(self.tenant_id, name):
            loaded = self._load_defn(name)
            if not loaded:
                return False, "code flow not found"
            _wid, defn = loaded
            ids = {s["id"] for s in defn.get("steps", [])}
            if from_step not in ids or to_step not in ids:
                return False, "both from and to must be existing step ids"
            defn.setdefault("edges", []).append({"from": from_step, "to": to_step, "on": on})
            ok, errors = compiler.validate_definition(defn)
        if not ok:
            return False, "; ".join(errors)
        self._save_definition(defn)
        return True, None

    def get_code_flow(self, name: str) -> Optional[Dict]:
        loaded = self._db_load(name)
        if not loaded:
            return None
        wid, data = loaded
        if data.get("kind") != compiler.CODE_FLOW_KIND:  # don't expose a plain workflow's graph via this API
            return None
        return {"name": name, "workflow_id": wid,
                "definition": data.get("definition"),
                "nodes": data.get("nodes"), "connections": data.get("connections")}

    def list_code_flows(self) -> List[Dict]:
        return self._db_list()

    def delete_code_flow(self, name: str) -> Tuple[bool, Optional[str]]:
        return (True, None) if self._db_delete(name) else (False, "not found")

    # ------------------------------------------------------------------- run

    def dry_run(self, name: str, runner=None) -> Dict:
        loaded = self._load_defn(name)
        if not loaded:
            return {"status": "error", "error": "code flow not found"}
        _wid, defn = loaded
        if not defn.get("steps"):
            return {"status": "error", "error": "code flow has no steps yet"}
        if runner is None:
            from automations.runner import AutomationRunner
            runner = AutomationRunner(tenant_id=self.tenant_id, connection_string=self.connection_string)
        workdir = get_app_path("automations", f"tenant_{self.tenant_id}", "_codeflow_runs",
                               compiler.new_step_id())
        return compiler.dry_run_walk(defn, runner, workdir)

    def run(self, name: str, runner=None) -> Dict:
        """v0 interactive run == the in-process walk (synchronous full trace).
        The DURABLE/scheduled path runs the saved code-flow WORKFLOW through the
        engine (JobType 'workflow', TargetId = this flow's workflow_id)."""
        return self.dry_run(name, runner=runner)

    def workflow_id(self, name: str) -> Optional[int]:
        loaded = self._db_load(name)
        return loaded[0] if loaded else None
