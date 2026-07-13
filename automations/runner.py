"""
Automation runner — executes a FROZEN version of an automation in its
dedicated agent environment, with honest tri-state outcomes.

Execution contract (P0):
  * skip-if-running: if a live run exists for the automation, the trigger is
    recorded as status='skipped' and nothing executes (James's decision:
    no concurrency handling, keep it simple).
  * scheduled/API runs execute the PINNED version; dry-runs execute
    current_version (or an explicit version) and seed the version's samples/.
  * credentials are injected as subprocess env vars resolved just-in-time —
    AIHUB_CONN_<NAME> from the Connections registry, AIHUB_SECRET_<NAME> from
    the local encrypted secrets store. This is the documented P0 shortcut;
    the aihub_runtime SDK + run-token flow replaces it in P2. Credentials
    never appear in code, argv, or on disk.
  * outcome: 'failed' on nonzero exit / timeout / a declared output missing;
    'unverified' on exit 0 with a declared output we cannot check yet (e.g.
    remote upload listing); 'success' only when exit 0 AND every declared
    output verified. Never success from the absence of an exception.

DB access is isolated in _db_* methods so unit tests can stub them.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from typing import Dict, List, Optional, Tuple

import pyodbc

from CommonUtils import get_app_path, get_db_connection_string
from .manager import AutomationManager, DEFAULT_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

_RUN_LOG_NAME = "run.log"
_INPUTS_FILE = "_inputs.json"
_SKIP_GRACE_SECONDS = 600  # a 'running' row older than timeout+grace is stale, not live

# Same preamble idea as the CC code interpreter: headless matplotlib + the
# conda-extracted bundle's native DLLs for compiled extensions. Plus
# best-effort EGRESS LOGGING: unattended scheduled code talks to the network,
# so every socket connect is appended to _egress.log in the workdir (folded
# into run.log afterwards). Best-effort by design — a failure to log must
# never break the script.
_EGRESS_LOG_NAME = "_egress.log"
_PREAMBLE = (
    "import os as _os, sys as _sys\n"
    "_os.environ.setdefault('MPLBACKEND', 'Agg')\n"
    "try:\n"
    "    _libbin = _os.path.join(_os.path.dirname(_sys.executable), 'Library', 'bin')\n"
    "    if hasattr(_os, 'add_dll_directory') and _os.path.isdir(_libbin):\n"
    "        _os.add_dll_directory(_libbin)\n"
    "except Exception:\n"
    "    pass\n"
    "try:\n"
    "    import socket as _sock\n"
    "    _orig_connect = _sock.socket.connect\n"
    "    def _aihub_logged_connect(self, addr, _oc=_orig_connect):\n"
    "        try:\n"
    "            with open('_egress.log', 'a', encoding='utf-8') as _f:\n"
    "                _f.write(repr(addr) + '\\n')\n"
    "        except Exception:\n"
    "            pass\n"
    "        return _oc(self, addr)\n"
    "    _sock.socket.connect = _aihub_logged_connect\n"
    "except Exception:\n"
    "    pass\n"
)

# One guard per automation so two near-simultaneous triggers in this process
# can't both pass the running-row check. Cross-process the DB row is the guard
# (single-box, low concurrency — accepted for P0).
_run_locks: Dict[str, threading.Lock] = {}
_run_locks_guard = threading.Lock()


def _load_cfg():
    """Lazy config access (tests monkeypatch this; import stays side-effect
    free until a run actually happens)."""
    import config
    return config


def _env_var_name(prefix: str, name: str) -> str:
    return prefix + re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def _substitute(template: str, inputs: Dict) -> str:
    """Fill {input_name} placeholders in an output path template; unknown
    placeholders are left intact (they'll fail the existence check honestly)."""
    try:
        return template.format(**inputs)
    except (KeyError, IndexError, ValueError):
        return template


def resolve_inputs(manifest: Dict, provided: Optional[Dict]) -> Tuple[Optional[Dict], Optional[str]]:
    """Apply manifest defaults; error on a declared input with no value and no
    default. Undeclared extras are rejected (typo protection)."""
    provided = dict(provided or {})
    declared = {i["name"]: i for i in manifest.get("inputs", [])}
    extras = set(provided) - set(declared)
    if extras:
        return None, f"undeclared inputs: {sorted(extras)}"
    resolved = {}
    for name, spec in declared.items():
        if name in provided:
            resolved[name] = provided[name]
        elif "default" in spec:
            resolved[name] = spec["default"]
        else:
            return None, f"missing required input '{name}' (no default)"
    return resolved, None


def verify_outputs(manifest: Dict, workdir: str, inputs: Dict,
                   secret_resolver=None) -> Tuple[str, List[Dict]]:
    """Check every declared output. Returns (component_outcome, report) where
    component_outcome is 'success' | 'failed' | 'unverified'.

    secret_resolver(name) -> value enables independent remote verification of
    sftp_upload/ftp_upload outputs (verify: {"remote_listing": true}); without
    it remote outputs are honestly 'unverified'."""
    report = []
    any_failed = False
    any_unchecked = False
    for out in manifest.get("outputs", []):
        kind = out.get("kind")
        verify = out.get("verify", {}) or {}
        entry = {"kind": kind, "checks": []}
        if kind == "file":
            rel = _substitute(out.get("path", ""), inputs)
            entry["path"] = rel
            path = os.path.join(workdir, rel)
            exists = os.path.isfile(path)
            entry["checks"].append({"check": "exists", "ok": exists})
            if not exists:
                any_failed = True
            elif "min_rows" in verify:
                # data rows: non-empty lines, minus one header line for .csv
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        lines = sum(1 for ln in f if ln.strip())
                    rows = max(0, lines - 1) if rel.lower().endswith(".csv") else lines
                    ok = rows >= int(verify["min_rows"])
                    entry["checks"].append({"check": "min_rows", "expected": verify["min_rows"], "actual": rows, "ok": ok})
                    if not ok:
                        any_failed = True
                except Exception as e:
                    entry["checks"].append({"check": "min_rows", "ok": False, "error": str(e)})
                    any_failed = True
        elif kind in ("sftp_upload", "ftp_upload") and verify.get("remote_listing") and secret_resolver:
            from .remote_verify import check_remote_output
            filename = _substitute(out.get("name", ""), inputs)
            entry["name"] = filename
            secret_value = secret_resolver(out.get("secret", ""))
            ok, note = check_remote_output(kind, secret_value, out.get("remote_dir", "/"),
                                           filename, verify)
            entry["checks"].append({"check": "remote_listing", "ok": ok, "note": note})
            if ok is False:
                any_failed = True
            elif ok is None:
                any_unchecked = True
        else:
            # no verifier requested/possible for this output — be honest
            # that we did not check it rather than implying success
            entry["checks"].append({"check": "remote", "ok": None, "note": "not verified"})
            any_unchecked = True
        report.append(entry)
    if any_failed:
        return "failed", report
    if any_unchecked:
        return "unverified", report
    return "success", report


class AutomationRunner:
    def __init__(self, manager: Optional[AutomationManager] = None,
                 tenant_id: Optional[str] = None, connection_string: Optional[str] = None):
        self.manager = manager or AutomationManager(tenant_id=tenant_id, connection_string=connection_string)
        self.tenant_id = self.manager.tenant_id
        self.connection_string = connection_string or get_db_connection_string()

    # --------------------------------------------------------------------- db

    def _db_conn(self):
        conn = pyodbc.connect(self.connection_string)
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
        cursor.close()
        return conn

    def _db_has_live_run(self, automation_id: str, max_age_seconds: int) -> bool:
        conn = self._db_conn()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT COUNT(*) FROM AutomationRuns
               WHERE automation_id = ? AND status = 'running'
                 AND started_at > DATEADD(SECOND, -?, GETUTCDATE())""",
            automation_id, max_age_seconds,
        )
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0

    def _db_insert_run(self, row: Dict):
        conn = self._db_conn()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO AutomationRuns
               (run_id, automation_id, version, trigger_source, status,
                requested_by, inputs_json, log_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            row["run_id"], row["automation_id"], row["version"], row["trigger_source"],
            row["status"], row.get("requested_by"), row.get("inputs_json"), row.get("log_path"),
        )
        conn.commit()
        conn.close()

    def _db_finish_run(self, run_id: str, status: str, exit_code: Optional[int],
                       verify_report: Optional[str], output_files: Optional[str],
                       error: Optional[str]):
        conn = self._db_conn()
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE AutomationRuns
               SET status = ?, exit_code = ?, verify_report = ?, output_files = ?,
                   error = ?, finished_at = GETUTCDATE()
               WHERE run_id = ?""",
            status, exit_code, verify_report, output_files, error, run_id,
        )
        conn.commit()
        conn.close()

    def _db_get_run(self, run_id: str) -> Optional[Dict]:
        conn = self._db_conn()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT run_id, automation_id, version, trigger_source, status, exit_code,
                      requested_by, inputs_json, verify_report, output_files, log_path,
                      error, started_at, finished_at
               FROM AutomationRuns WHERE run_id = ?""",
            run_id,
        )
        r = cursor.fetchone()
        conn.close()
        if not r:
            return None
        return {
            "run_id": r.run_id, "automation_id": r.automation_id, "version": r.version,
            "trigger_source": r.trigger_source, "status": r.status, "exit_code": r.exit_code,
            "requested_by": r.requested_by,
            "inputs": json.loads(r.inputs_json) if r.inputs_json else {},
            "verify_report": json.loads(r.verify_report) if r.verify_report else None,
            "output_files": json.loads(r.output_files) if r.output_files else [],
            "log_path": r.log_path, "error": r.error,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }

    def _db_list_runs(self, automation_id: str, limit: int = 50) -> List[Dict]:
        conn = self._db_conn()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT TOP (?) run_id, version, trigger_source, status, exit_code,
                      started_at, finished_at
               FROM AutomationRuns WHERE automation_id = ?
               ORDER BY started_at DESC""",
            limit, automation_id,
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                "run_id": r.run_id, "version": r.version, "trigger_source": r.trigger_source,
                "status": r.status, "exit_code": r.exit_code,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in rows
        ]

    # ---------------------------------------------------------- cred resolution

    def _resolve_connection(self, name: str) -> Optional[str]:
        from DataUtils import get_connection_string_by_name
        return get_connection_string_by_name(name)

    def _resolve_secret(self, name: str) -> Optional[str]:
        from local_secrets import get_local_secret
        return get_local_secret(name)

    def _mint_run_token(self, automation_id: str, run_id: str,
                        connections: List[str], secrets: List[str],
                        ttl_seconds: int) -> Optional[str]:
        """Sign a run-scoped credential token. None (with a warning) when the
        signing secret/PyJWT is unavailable — the caller decides whether the
        run can proceed."""
        try:
            from shared_auth import sign_automation_run_token
            return sign_automation_run_token(automation_id, run_id,
                                             connections, secrets, ttl_seconds)
        except Exception as e:
            logger.warning(f"run-token signing unavailable: {e}")
            return None

    @staticmethod
    def _runtime_base_url() -> str:
        """Base URL the SDK calls back to for credential resolution."""
        override = os.getenv("AUTOMATIONS_RUNTIME_URL")
        if override:
            return override.rstrip("/")
        from CommonUtils import get_base_url
        return get_base_url().rstrip("/")

    # ------------------------------------------------------------- interpreter

    def _resolve_python(self, environment_id: Optional[str]) -> Optional[str]:
        """Dedicated env venv -> CODE_INTERPRETER_PYTHON -> shipped bundle ->
        sys.executable (dev fallback). Returns None only if nothing exists."""
        if environment_id:
            env_python = get_app_path(
                "agent_environments", f"tenant_{self.tenant_id}", environment_id,
                "Scripts", "python.exe",
            )
            if os.name != "nt":
                env_python = get_app_path(
                    "agent_environments", f"tenant_{self.tenant_id}", environment_id,
                    "bin", "python",
                )
            if os.path.isfile(env_python):
                return env_python
            logger.warning(f"automation env {environment_id} has no interpreter at {env_python}; falling back")

        configured = os.getenv("CODE_INTERPRETER_PYTHON")
        if configured and os.path.isfile(configured):
            return configured

        bundle = get_app_path("agent_environments", "python-bundle",
                              "python.exe" if os.name == "nt" else "python")
        if os.path.isfile(bundle):
            return bundle

        return sys.executable  # dev machines

    # -------------------------------------------------------------------- run

    def run(self, automation_id: str, inputs: Optional[Dict] = None,
            trigger: str = "manual", version: Optional[int] = None,
            requested_by: Optional[int] = None, dry_run: bool = False,
            run_id: Optional[str] = None) -> Dict:
        """Execute an automation synchronously. Returns the finished run dict
        (or the 'skipped' record). The API layer threads this for async runs,
        pre-allocating run_id so the caller can poll before the row lands."""
        auto = self.manager.get_automation(automation_id)
        if not auto:
            return {"status": "error", "error": "automation not found"}

        # -- resolve version: dry-runs test the latest edit; real runs are pinned
        if version is None:
            version = auto["current_version"] if dry_run else auto["pinned_version"]
        if version < 1:
            return {"status": "error",
                    "error": "no runnable version — save code first" if dry_run
                    else "nothing promoted — dry-run and promote a version first"}
        if version not in self.manager.list_versions(automation_id):
            return {"status": "error", "error": f"version v{version} does not exist"}

        manifest = self.manager.get_manifest(automation_id, version) or {}
        timeout = int(manifest.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))

        resolved_inputs, err = resolve_inputs(manifest, inputs)
        if err:
            return {"status": "error", "error": err}

        run_id = run_id or str(uuid.uuid4())
        workdir = os.path.join(self.manager.runs_dir(automation_id), run_id)
        log_path = os.path.join(workdir, _RUN_LOG_NAME)

        # -- skip-if-running guard (in-process lock around check + insert)
        with _run_locks_guard:
            lock = _run_locks.setdefault(automation_id, threading.Lock())
        with lock:
            if not dry_run and self._db_has_live_run(automation_id, timeout + _SKIP_GRACE_SECONDS):
                self._db_insert_run({
                    "run_id": run_id, "automation_id": automation_id, "version": version,
                    "trigger_source": trigger, "status": "skipped",
                    "requested_by": requested_by,
                    "inputs_json": json.dumps(resolved_inputs),
                })
                logger.info(f"automation {automation_id}: run skipped (already running)")
                return {"run_id": run_id, "status": "skipped",
                        "note": "a run is already in progress — skipped (concurrent runs are not allowed)"}
            self._db_insert_run({
                "run_id": run_id, "automation_id": automation_id, "version": version,
                "trigger_source": "dry_run" if dry_run else trigger, "status": "running",
                "requested_by": requested_by,
                "inputs_json": json.dumps(resolved_inputs),
                "log_path": log_path,
            })

        try:
            result = self._execute(auto, manifest, version, run_id, workdir,
                                   resolved_inputs, timeout, dry_run)
        except Exception as e:
            logger.exception(f"automation {automation_id} run {run_id} crashed in runner")
            self._db_finish_run(run_id, "failed", None, None, None, f"runner error: {e}")
            return {"run_id": run_id, "status": "failed", "error": f"runner error: {e}"}

        self._db_finish_run(
            run_id,
            result["status"],
            result.get("exit_code"),
            json.dumps(result.get("verify_report")) if result.get("verify_report") is not None else None,
            json.dumps(result.get("output_files", [])),
            result.get("error"),
        )
        result["run_id"] = run_id
        return result

    def _execute(self, auto: Dict, manifest: Dict, version: int, run_id: str,
                 workdir: str, inputs: Dict, timeout: int, dry_run: bool) -> Dict:
        automation_id = auto["automation_id"]
        os.makedirs(workdir, exist_ok=True)

        # -- seed: inputs file + (dry-run) the version's sample files
        with open(os.path.join(workdir, _INPUTS_FILE), "w", encoding="utf-8") as f:
            json.dump(inputs, f, indent=2)
        if dry_run:
            samples = os.path.join(self.manager.version_dir(automation_id, version), "samples")
            if os.path.isdir(samples):
                for name in os.listdir(samples):
                    src = os.path.join(samples, name)
                    if os.path.isfile(src):
                        shutil.copy2(src, os.path.join(workdir, name))

        # -- write the frozen script
        code = self.manager.get_code(automation_id, version)
        if code is None:
            return {"status": "failed", "error": f"code for v{version} missing on disk"}
        entrypoint = manifest.get("entrypoint", "main.py")
        script_path = os.path.join(workdir, entrypoint)
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(_PREAMBLE + code)

        pre_run_files = set()
        for root, _dirs, files in os.walk(workdir):
            for name in files:
                pre_run_files.add(os.path.relpath(os.path.join(root, name), workdir))

        # -- credentials: pre-flight existence check ALWAYS; delivery is the
        # signed run token + aihub_runtime SDK (values resolved server-side at
        # use time). Env-var VALUE injection is the legacy P0/P1 path, kept
        # behind AUTOMATIONS_ENV_CRED_INJECTION (default off).
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["AIHUB_RUN_ID"] = run_id
        env["AIHUB_AUTOMATION_ID"] = automation_id
        env["AIHUB_INPUTS_PATH"] = os.path.join(workdir, _INPUTS_FILE)
        sdk_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sdk")
        env["PYTHONPATH"] = sdk_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

        conn_names = manifest.get("connections", [])
        secret_names = manifest.get("secrets", [])
        env_inject = bool(getattr(_load_cfg(), "AUTOMATIONS_ENV_CRED_INJECTION", False))
        missing = []
        for conn_name in conn_names:
            conn_str = self._resolve_connection(conn_name)
            if not conn_str:
                missing.append(f"connection '{conn_name}'")
            elif env_inject:
                env[_env_var_name("AIHUB_CONN_", conn_name)] = conn_str
        for secret_name in secret_names:
            value = self._resolve_secret(secret_name)
            if not value:
                missing.append(f"secret '{secret_name}'")
            elif env_inject:
                env[_env_var_name("AIHUB_SECRET_", secret_name)] = value
        if missing:
            # fail fast and honestly — do not let the script discover it mid-way
            error = "unresolvable at run time: " + ", ".join(missing)
            self._write_log(log_dir=workdir, header=self._log_header(auto, version, None),
                            stdout="", stderr=error, footer="outcome: failed (pre-flight)")
            return {"status": "failed", "error": error}

        if conn_names or secret_names:
            token = self._mint_run_token(automation_id, run_id, conn_names, secret_names,
                                         ttl_seconds=timeout + 900)
            if token:
                env["AIHUB_RUN_TOKEN"] = token
                env["AIHUB_RUNTIME_URL"] = self._runtime_base_url()
            elif not env_inject:
                error = ("cannot provide credentials to the run: run-token signing is "
                         "unavailable (no JWT secret) and AUTOMATIONS_ENV_CRED_INJECTION is off")
                self._write_log(log_dir=workdir, header=self._log_header(auto, version, None),
                                stdout="", stderr=error, footer="outcome: failed (pre-flight)")
                return {"status": "failed", "error": error}

        python_exe = self._resolve_python(auto.get("environment_id"))
        if not python_exe:
            return {"status": "failed", "error": "no usable Python interpreter found"}

        # -- execute
        timed_out = False
        try:
            proc = subprocess.run(
                [python_exe, entrypoint],
                cwd=workdir, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
            exit_code, stdout, stderr = proc.returncode, proc.stdout or "", proc.stderr or ""
        except subprocess.TimeoutExpired as te:
            timed_out = True
            exit_code = None
            stdout = (te.stdout or b"").decode("utf-8", "replace") if isinstance(te.stdout, bytes) else (te.stdout or "")
            stderr = (te.stderr or b"").decode("utf-8", "replace") if isinstance(te.stderr, bytes) else (te.stderr or "")

        # -- sweep new files (before verification so the report can reference them)
        output_files = []
        for root, _dirs, files in os.walk(workdir):
            for name in files:
                rel = os.path.relpath(os.path.join(root, name), workdir)
                if rel not in pre_run_files and rel not in (_RUN_LOG_NAME, _EGRESS_LOG_NAME):
                    output_files.append(rel)
        output_files.sort()

        # -- fold the egress log (network destinations the script connected to)
        egress = ""
        try:
            with open(os.path.join(workdir, _EGRESS_LOG_NAME), "r", encoding="utf-8") as f:
                egress = f.read().strip()
        except FileNotFoundError:
            pass

        # -- honest outcome
        if timed_out:
            status, verify_report, error = "failed", None, f"timed out after {timeout}s"
        elif exit_code != 0:
            status, verify_report, error = "failed", None, f"exit code {exit_code}"
        else:
            status, verify_report = verify_outputs(manifest, workdir, inputs,
                                                   secret_resolver=self._resolve_secret)
            error = "declared output verification failed" if status == "failed" else None

        self._write_log(
            log_dir=workdir,
            header=self._log_header(auto, version, python_exe),
            stdout=stdout, stderr=stderr,
            footer=(f"exit_code: {exit_code}  outcome: {status}" + (f"  ({error})" if error else "")
                    + (f"\n\n===== network egress =====\n{egress}" if egress else "")),
        )

        return {
            "status": status, "exit_code": exit_code, "error": error,
            "verify_report": verify_report, "output_files": output_files,
            "stdout_tail": stdout[-2000:], "stderr_tail": stderr[-2000:],
            "workdir": workdir,
        }

    @staticmethod
    def _log_header(auto: Dict, version: int, python_exe: Optional[str]) -> str:
        return (f"automation: {auto['name']} ({auto['automation_id']})\n"
                f"version: v{version}\npython: {python_exe or 'unresolved'}\n")

    @staticmethod
    def _write_log(log_dir: str, header: str, stdout: str, stderr: str, footer: str):
        try:
            with open(os.path.join(log_dir, _RUN_LOG_NAME), "w", encoding="utf-8") as f:
                f.write(header)
                f.write("\n===== stdout =====\n")
                f.write(stdout or "(empty)\n")
                f.write("\n===== stderr =====\n")
                f.write(stderr or "(empty)\n")
                f.write("\n===== result =====\n")
                f.write(footer + "\n")
        except Exception as e:
            logger.error(f"could not write run log in {log_dir}: {e}")

    # ---------------------------------------------------------------- queries

    def get_run(self, run_id: str) -> Optional[Dict]:
        return self._db_get_run(run_id)

    def list_runs(self, automation_id: str, limit: int = 50) -> List[Dict]:
        return self._db_list_runs(automation_id, limit)

    def get_run_log(self, run_id: str, tail_chars: int = 20000) -> Optional[str]:
        run = self._db_get_run(run_id)
        if not run or not run.get("log_path"):
            return None
        try:
            with open(run["log_path"], "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return content[-tail_chars:] if tail_chars else content
        except FileNotFoundError:
            return None
