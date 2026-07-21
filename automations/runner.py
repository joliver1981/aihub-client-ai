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

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pyodbc

from CommonUtils import get_app_path, get_db_connection_string
from .manager import AutomationManager, DEFAULT_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

_RUN_LOG_NAME = "run.log"
_INPUTS_FILE = "_inputs.json"
_EVENTS_FILE = "events.jsonl"
_SKIP_GRACE_SECONDS = 600  # a 'running' row older than timeout+grace is stale, not live

# Statuses that mean "this run is alive right now" — the skip-guard, the
# runtime-resolve token check, and the active-runs endpoint all share it.
# waiting  = paused at an aihub.checkpoint() gate
# aborting = a human asked to stop; the runner will kill the child shortly
LIVE_STATUSES = ("running", "waiting", "aborting")

# How often the supervision loop polls (child exit, egress tail) and how often
# it pays for a DB read of the run status (abort/checkpoint signals).
_SUPERVISE_TICK_SECONDS = 0.25
_STATUS_POLL_SECONDS = 2.0
# Reaper liveness (james 2026-07-21, after 4 orphaned-run incidents in one
# day): the supervising process touches <workdir>/_heartbeat while a run is
# live; a non-terminal DB row whose heartbeat is stale/absent has NO living
# supervisor anywhere (main app, scheduler engine, or executor — whichever
# process supervises writes the same file) and is safe to reap.
_HEARTBEAT_NAME = "_heartbeat"
_REAP_GRACE_SECONDS = 300      # never reap a run younger than this
_REAP_STALE_SECONDS = 180      # heartbeat older than this = supervisor is dead

# Declared step/automation `packages` are installed (AIHUB-0036) into a cached
# per-package-set directory via `pip install --target` (no venv overhead) and
# injected on PYTHONPATH — so a code step that imports e.g. pdfplumber works
# without a pre-provisioned environment. Cached by hash(sorted(packages)+base
# interpreter), so the install cost is paid once per unique dep set.
_PKG_INSTALL_TIMEOUT_SECONDS = int(os.getenv("AUTOMATIONS_PKG_INSTALL_TIMEOUT", "600"))
_PKG_INSTALL_MARKER = ".installed"
_pkg_install_locks: Dict[str, threading.Lock] = {}
_pkg_install_guard = threading.Lock()


def _pkg_install_lock(key: str) -> threading.Lock:
    with _pkg_install_guard:
        lk = _pkg_install_locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _pkg_install_locks[key] = lk
        return lk


class RunEventLog:
    """Append-only per-run event sidecar (events.jsonl in the workdir) — the
    Studio's live feed. Best-effort by design: an emit failure never affects
    the run. Events carry a monotonically increasing seq so the UI can poll
    with ?after=N."""

    def __init__(self, workdir: str):
        self.path = os.path.join(workdir, _EVENTS_FILE)
        self._lock = threading.Lock()
        # continue the sequence if the file already has events (the checkpoint
        # endpoint appends from another process/worker than the runner)
        self.seq = 0
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        self.seq += 1
        except FileNotFoundError:
            pass

    def emit(self, event_type: str, **fields):
        with self._lock:
            self.seq += 1
            record = {"seq": self.seq,
                      "ts": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
                      "type": event_type, **fields}
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")
            except Exception:
                pass

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
                   secret_resolver=None, output_files: Optional[List[str]] = None
                   ) -> Tuple[str, List[Dict]]:
    """Check every declared output. Returns (component_outcome, report) where
    component_outcome is 'success' | 'failed' | 'unverified'.

    secret_resolver(name) -> value enables independent remote verification of
    sftp_upload/ftp_upload outputs (verify: {"remote_listing": true}); without
    it remote outputs are honestly 'unverified'.

    output_files: the step's swept produced files (workdir-relative). AIHUB-0044:
    remote verification checks these basenames as fallback candidates, so a real
    upload isn't failed just because the declared output NAME was a symbolic
    label rather than the uploaded filename."""
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
            # AIHUB-0044: the declared NAME is often a symbolic label, not the
            # uploaded filename (live: name 'store_headcount_upload' while the
            # step really uploaded store_headcount_2026-07.csv — a REAL upload
            # was failed). Verify against CANDIDATES in order: explicit
            # remote_path basename, the substituted name, then the step's
            # actually-produced local files' basenames. Verified if ANY is on
            # the remote; failed only when NONE is (no weakening — a file that
            # was never uploaded matches no candidate).
            candidates = []
            _rp = (out.get("remote_path") or "").replace("\\", "/").rsplit("/", 1)[-1]
            if _rp:
                candidates.append(_substitute(_rp, inputs))
            if out.get("name"):
                candidates.append(_substitute(out["name"], inputs))
            for f_rel in (output_files or []):
                b = os.path.basename(f_rel)
                if b:
                    candidates.append(b)
            seen = set()
            candidates = [c for c in candidates
                          if c and not (c in seen or seen.add(c))]
            secret_value = secret_resolver(out.get("secret", ""))
            ok, note = None, "no candidate filename to check"
            for cand in candidates:
                ok, note = check_remote_output(kind, secret_value, out.get("remote_dir", "/"),
                                               cand, verify)
                if ok is True:
                    entry["name"] = cand
                    note = f"found '{cand}' in {out.get('remote_dir', '/')}" + \
                           (f" ({note})" if note else "")
                    break
                if ok is None:
                    # couldn't check at all (bad secret / connect error) — the
                    # server won't answer differently for another name
                    break
            else:
                if candidates:
                    entry["name"] = candidates[0]
                    note = (f"none of {candidates} found in {out.get('remote_dir', '/')} "
                            f"on the remote")
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
        placeholders = ", ".join("?" for _ in LIVE_STATUSES)
        cursor.execute(
            f"""SELECT COUNT(*) FROM AutomationRuns
               WHERE automation_id = ? AND status IN ({placeholders})
                 AND started_at > DATEADD(SECOND, -?, GETUTCDATE())""",
            automation_id, *LIVE_STATUSES, max_age_seconds,
        )
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0

    def _db_get_run_field(self, run_id: str, field: str) -> Optional[str]:
        """Read one column of a run row (supervision-loop status poll)."""
        if field not in ("status",):
            raise ValueError(f"field '{field}' not readable here")
        conn = self._db_conn()
        cursor = conn.cursor()
        cursor.execute(f"SELECT {field} FROM AutomationRuns WHERE run_id = ?", run_id)
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None

    def _db_set_run_status(self, run_id: str, status: str,
                           only_if_in: Optional[Tuple[str, ...]] = None) -> bool:
        """Flip a run's status (checkpoint waiting/running, abort request).
        With only_if_in, the update is conditional and returns whether a row
        changed — that makes abort/decide idempotent and race-safe."""
        conn = self._db_conn()
        cursor = conn.cursor()
        if only_if_in:
            placeholders = ", ".join("?" for _ in only_if_in)
            cursor.execute(
                f"UPDATE AutomationRuns SET status = ? WHERE run_id = ? AND status IN ({placeholders})",
                status, run_id, *only_if_in,
            )
        else:
            cursor.execute("UPDATE AutomationRuns SET status = ? WHERE run_id = ?", status, run_id)
        changed = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return changed

    # ------------------------------------------------------------- control

    def request_abort(self, run_id: str) -> Tuple[bool, str]:
        """Ask a live run to stop. The supervision loop sees 'aborting' on its
        next status poll and kills the child; the final outcome is 'aborted'.
        Works across workers — the DB row carries the signal."""
        run = self._db_get_run(run_id)
        if not run:
            return False, "run not found"
        if run.get("status") not in LIVE_STATUSES:
            return False, f"run is not live (status: {run.get('status')})"
        if self._db_set_run_status(run_id, "aborting", only_if_in=("running", "waiting")):
            return True, "abort requested — the run will stop within a few seconds"
        return False, "run just finished or abort already requested"

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
        # Every finalize passes through here (the chokepoint), so this is
        # where a dead run's still-Pending bridged approval rows get
        # cancelled — My Approvals must never hold a gate nobody can answer.
        try:
            self._cancel_open_checkpoint_approvals(run_id)
        except Exception as e:
            logger.warning(f"open-approval cancel on finish failed for {run_id}: {e}")

    def _cancel_open_checkpoint_approvals(self, run_id: str):
        run = self._db_get_run(run_id)
        log_path = (run or {}).get("log_path")
        workdir = os.path.dirname(log_path) if log_path else None
        if not workdir or not os.path.isdir(workdir):
            return
        from .checkpoints import list_checkpoints
        open_ids = [c.get("approval_request_id") for c in list_checkpoints(workdir)
                    if not c.get("decision") and c.get("approval_request_id")]
        if not open_ids:
            return
        from . import approval_store
        for rid in open_ids:
            approval_store.settle_row(self.manager.base_path, rid,
                                      "Cancelled", "system:run-finished")

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

    def list_active_runs(self) -> List[Dict]:
        """All live runs (running / waiting / aborting) with automation names —
        Mission Control's board."""
        conn = self._db_conn()
        cursor = conn.cursor()
        placeholders = ", ".join("?" for _ in LIVE_STATUSES)
        cursor.execute(
            f"""SELECT r.run_id, r.automation_id, a.name, r.version, r.trigger_source,
                       r.status, r.started_at, r.log_path
                FROM AutomationRuns r
                JOIN Automations a ON a.automation_id = r.automation_id
                WHERE r.status IN ({placeholders})
                ORDER BY r.started_at DESC""",
            *LIVE_STATUSES,
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                "run_id": r.run_id, "automation_id": r.automation_id, "name": r.name,
                "version": r.version, "trigger_source": r.trigger_source, "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "log_path": r.log_path,
            }
            for r in rows
        ]

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

    def _ensure_packages(self, packages, base_python: str) -> Tuple[Optional[str], Optional[str]]:
        """Make declared pip `packages` importable by the step (AIHUB-0036).

        Installs them via `pip install --target <cache>` into a directory keyed
        by hash(sorted(packages) + base interpreter), reused across runs, and
        returns (pythonpath_dir | None, error | None). No venv — the target dir
        is prepended to the step's PYTHONPATH. Cross-process safe: install into a
        temp dir, then atomically rename into place (a loser reuses the winner's).
        Honest: a pip failure returns an error string (the caller fails the run);
        it never silently proceeds. Disable with AUTOMATIONS_PKG_AUTO_INSTALL=false
        (then a missing dep just fails at import time, still honest)."""
        pkgs = [p.strip() for p in (packages or []) if isinstance(p, str) and p.strip()]
        if not pkgs:
            return None, None
        if os.getenv("AUTOMATIONS_PKG_AUTO_INSTALL", "true").lower() != "true":
            return None, None
        if not base_python:
            return None, "no interpreter to install packages with"

        key = hashlib.sha256(("\n".join(sorted(pkgs)) + "|" + base_python).encode("utf-8")).hexdigest()[:16]
        cache_dir = get_app_path("automations", "_pkg_cache", key)
        marker = os.path.join(cache_dir, _PKG_INSTALL_MARKER)
        if os.path.isfile(marker):
            return cache_dir, None

        with _pkg_install_lock(key):
            if os.path.isfile(marker):            # another thread finished while we waited
                return cache_dir, None
            os.makedirs(os.path.dirname(cache_dir), exist_ok=True)
            tmp = cache_dir + ".tmp-" + uuid.uuid4().hex[:8]
            os.makedirs(tmp, exist_ok=True)
            cmd = [base_python, "-m", "pip", "install", "--no-input",
                   "--disable-pip-version-check", "--target", tmp, *pkgs]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=_PKG_INSTALL_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                shutil.rmtree(tmp, ignore_errors=True)
                return None, f"installing packages {pkgs} timed out after {_PKG_INSTALL_TIMEOUT_SECONDS}s"
            except Exception as e:
                shutil.rmtree(tmp, ignore_errors=True)
                return None, f"could not install packages {pkgs}: {e}"
            if proc.returncode != 0:
                tail = ((proc.stderr or "") + (proc.stdout or "")).strip()[-800:]
                shutil.rmtree(tmp, ignore_errors=True)
                return None, f"pip install of {pkgs} failed: {tail}"
            open(os.path.join(tmp, _PKG_INSTALL_MARKER), "w", encoding="utf-8").close()
            try:
                os.rename(tmp, cache_dir)         # atomic publish
            except OSError:
                shutil.rmtree(tmp, ignore_errors=True)  # someone published first — use theirs
            return (cache_dir if os.path.isfile(marker) else tmp), None

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
        """Asset path: load the pinned/latest code (+ dry-run samples) and hand
        off to the shared code executor. Thin wrapper so the working Automations
        path is byte-for-byte unchanged while inline Code Flow steps reuse the
        same _execute_code core."""
        automation_id = auto["automation_id"]
        code = self.manager.get_code(automation_id, version)
        if code is None:
            return {"status": "failed", "error": f"code for v{version} missing on disk"}
        samples_dir = None
        if dry_run:
            samples_dir = os.path.join(self.manager.version_dir(automation_id, version), "samples")
        return self._execute_code(
            code=code, manifest=manifest,
            identity={"id": automation_id, "name": auto["name"], "version": version,
                      "environment_id": auto.get("environment_id")},
            run_id=run_id, workdir=workdir, inputs=inputs, timeout=timeout,
            dry_run=dry_run, samples_dir=samples_dir)

    def _execute_code(self, code: str, manifest: Dict, identity: Dict, run_id: str,
                      workdir: str, inputs: Dict, timeout: int, dry_run: bool,
                      samples_dir: Optional[str] = None,
                      force_env_inject: bool = False,
                      checkpoints_supported: bool = True) -> Dict:
        """Shared executor for BOTH a promoted Automation and an inline Code
        Flow step. `identity` = {id, name, version, environment_id}. Runs the
        code in an environment with the aihub_runtime SDK, supervises it (live
        events, honest cancellation), verifies declared outputs, returns the
        honest tri-state outcome. `force_env_inject` delivers credential VALUES
        as env vars — the Code Flow step path uses it because a workflow step
        has no live AutomationRuns row for the token/resolve endpoint (v0).
        `checkpoints_supported` is False for that same Code Flow path: without a
        live run row there is nothing to pause/resume against, so aihub.checkpoint()
        auto-approves with an honest log line instead of 403-ing at the gate."""
        ident = identity["id"]
        name = identity.get("name") or ident
        version = identity.get("version", 1)
        os.makedirs(workdir, exist_ok=True)

        # -- seed: inputs file + (dry-run) sample files
        with open(os.path.join(workdir, _INPUTS_FILE), "w", encoding="utf-8") as f:
            json.dump(inputs, f, indent=2)
        if samples_dir and os.path.isdir(samples_dir):
            for sname in os.listdir(samples_dir):
                src = os.path.join(samples_dir, sname)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(workdir, sname))

        entrypoint = manifest.get("entrypoint", "main.py")
        with open(os.path.join(workdir, entrypoint), "w", encoding="utf-8") as f:
            f.write(_PREAMBLE + (code or ""))

        pre_run_files = set()
        for root, _dirs, files in os.walk(workdir):
            for fn in files:
                pre_run_files.add(os.path.relpath(os.path.join(root, fn), workdir))

        # -- credentials: pre-flight existence check ALWAYS; delivery is the
        # signed run token + aihub_runtime SDK, or (force_env_inject / the
        # AUTOMATIONS_ENV_CRED_INJECTION flag) credential VALUES as env vars.
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["AIHUB_RUN_ID"] = run_id
        env["AIHUB_AUTOMATION_ID"] = ident
        env["AIHUB_INPUTS_PATH"] = os.path.join(workdir, _INPUTS_FILE)
        # Signal whether human-approval gates can actually pause this run. Code
        # Flow steps have no live AutomationRuns row → checkpoint() auto-approves.
        env["AIHUB_CHECKPOINTS_ENABLED"] = "1" if checkpoints_supported else "0"
        sdk_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sdk")
        env["PYTHONPATH"] = sdk_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

        conn_names = manifest.get("connections", [])
        secret_names = manifest.get("secrets", [])
        env_inject = force_env_inject or bool(getattr(_load_cfg(), "AUTOMATIONS_ENV_CRED_INJECTION", False))
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
            error = "unresolvable at run time: " + ", ".join(missing)
            self._write_log(log_dir=workdir, header=self._log_header(name, ident, version, None),
                            stdout="", stderr=error, footer="outcome: failed (pre-flight)")
            return {"status": "failed", "error": error, "workdir": workdir}

        token = self._mint_run_token(ident, run_id, conn_names, secret_names, ttl_seconds=timeout + 900)
        if token:
            env["AIHUB_RUN_TOKEN"] = token
            env["AIHUB_RUNTIME_URL"] = self._runtime_base_url()
        elif (conn_names or secret_names) and not env_inject:
            error = ("cannot provide credentials to the run: run-token signing is "
                     "unavailable (no JWT secret) and AUTOMATIONS_ENV_CRED_INJECTION is off")
            self._write_log(log_dir=workdir, header=self._log_header(name, ident, version, None),
                            stdout="", stderr=error, footer="outcome: failed (pre-flight)")
            return {"status": "failed", "error": error, "workdir": workdir}

        python_exe = self._resolve_python(identity.get("environment_id"))
        if not python_exe:
            return {"status": "failed", "error": "no usable Python interpreter found", "workdir": workdir}

        # -- declared packages: install (cached) and inject on PYTHONPATH so the
        # step can import a non-base dep (e.g. pdfplumber) — AIHUB-0036. A pip
        # failure is an honest run failure, never a silent proceed.
        pkg_dir, pkg_err = self._ensure_packages(manifest.get("packages"), python_exe)
        if pkg_err:
            self._write_log(log_dir=workdir, header=self._log_header(name, ident, version, python_exe),
                            stdout="", stderr=pkg_err, footer="outcome: failed (package install)")
            return {"status": "failed", "error": pkg_err, "workdir": workdir}
        if pkg_dir:
            env["PYTHONPATH"] = pkg_dir + os.pathsep + env["PYTHONPATH"]

        # -- execute under supervision: incremental output, live events, honest cancellation
        events = RunEventLog(workdir)
        events.emit("run_started", automation=name, automation_id=ident,
                    run_id=run_id, version=version, dry_run=dry_run, timeout=timeout)
        exit_code, stdout, stderr, timed_out, aborted = self._supervise(
            [python_exe, entrypoint], workdir, env, timeout, run_id, events)

        # -- sweep new files (before verification so the report can reference them)
        output_files = []
        for root, _dirs, files in os.walk(workdir):
            for fn in files:
                rel = os.path.relpath(os.path.join(root, fn), workdir)
                if (rel not in pre_run_files
                        and rel not in (_RUN_LOG_NAME, _EGRESS_LOG_NAME, _EVENTS_FILE)
                        and not os.path.basename(rel).startswith("checkpoint_")):
                    output_files.append(rel)
        output_files.sort()
        for f_rel in output_files:
            events.emit("output_file", path=f_rel)

        egress = ""
        try:
            with open(os.path.join(workdir, _EGRESS_LOG_NAME), "r", encoding="utf-8") as f:
                egress = f.read().strip()
        except FileNotFoundError:
            pass

        # -- honest outcome
        if aborted:
            status, verify_report, error = "aborted", None, "aborted by user"
        elif timed_out:
            status, verify_report, error = "failed", None, f"timed out after {timeout}s"
        elif exit_code != 0:
            status, verify_report, error = "failed", None, f"exit code {exit_code}"
        else:
            status, verify_report = verify_outputs(manifest, workdir, inputs,
                                                   secret_resolver=self._resolve_secret,
                                                   output_files=output_files)
            error = "declared output verification failed" if status == "failed" else None

        # AIHUB-0040: deterministic transfer-claim evidence. If the step declared a
        # remote-transfer output but the egress hook recorded NO network connection,
        # nothing was transferred — say so explicitly (this is what caught the live
        # "# Simulated upload placeholder" step: unverified outcome, zero egress).
        # We do NOT flip an independently-VERIFIED success (remote_listing saw the
        # file) — absence of egress there is a hook artifact, not evidence.
        no_egress_transfer = False
        _declared_transfer = any(
            isinstance(o, dict) and o.get("kind") in ("sftp_upload", "ftp_upload")
            for o in manifest.get("outputs", []))
        if _declared_transfer and not egress and status in ("unverified", "success"):
            _independently_verified = any(
                c.get("check") == "remote_listing" and c.get("ok") is True
                for entry in (verify_report or []) for c in entry.get("checks", []))
            if not _independently_verified:
                no_egress_transfer = True
                if verify_report is None:
                    verify_report = []
                verify_report.append({
                    "kind": "egress", "checks": [{
                        "check": "network_egress", "ok": False,
                        "note": ("step declares a remote transfer but NO network egress was "
                                 "observed — nothing was transferred")}]})
                if status == "success":
                    status, error = "failed", "declared a remote transfer but no network egress occurred"
                elif status == "unverified":
                    error = "no network egress — the declared transfer did not happen"

        for entry in (verify_report or []):
            for check in entry.get("checks", []):
                events.emit("verify", kind=entry.get("kind"),
                            target=entry.get("path") or entry.get("name"),
                            check=check.get("check"), ok=check.get("ok"), note=check.get("note"))
        events.emit("finished", status=status, exit_code=exit_code, error=error)

        self._write_log(
            log_dir=workdir,
            header=self._log_header(name, ident, version, python_exe),
            stdout=stdout, stderr=stderr,
            footer=(f"exit_code: {exit_code}  outcome: {status}" + (f"  ({error})" if error else "")
                    + (f"\n\n===== network egress =====\n{egress}" if egress else "")),
        )

        return {
            "status": status, "exit_code": exit_code, "error": error, "version": version,
            "verify_report": verify_report, "output_files": output_files,
            "stdout_tail": stdout[-2000:], "stderr_tail": stderr[-2000:],
            "workdir": workdir,
            # AIHUB-0040: transfer-claim evidence for honest chat reporting —
            # egress destinations recorded by the socket hook, and the explicit
            # "declared a transfer but nothing left the box" flag.
            "egress": [ln for ln in egress.splitlines() if ln.strip()][:20],
            "no_egress_transfer": no_egress_transfer,
        }

    def run_code_step(self, code: str, manifest: Dict, step_name: str,
                      inputs: Optional[Dict] = None, environment_id: Optional[str] = None,
                      run_id: Optional[str] = None, workdir: Optional[str] = None) -> Dict:
        """Execute an INLINE Code Flow step — LLM-authored Python that lives in
        a workflow node, not a promoted Automation asset — through the shared
        executor. No AutomationRuns row (the workflow engine tracks the step);
        credentials are delivered as env vars (v0), since there is no live run
        row backing the token/resolve endpoint for this ephemeral execution."""
        run_id = run_id or str(uuid.uuid4())
        if workdir is None:
            workdir = get_app_path("automations", f"tenant_{self.tenant_id}",
                                   "_codeflow_runs", run_id)
        timeout = int(manifest.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        resolved_inputs, err = resolve_inputs(manifest, inputs)
        if err:
            return {"status": "error", "error": err, "run_id": run_id, "workdir": workdir}
        result = self._execute_code(
            code=code, manifest=manifest,
            identity={"id": f"codestep-{run_id}", "name": step_name, "version": 1,
                      "environment_id": environment_id},
            run_id=run_id, workdir=workdir, inputs=resolved_inputs, timeout=timeout,
            dry_run=False, samples_dir=None, force_env_inject=True,
            checkpoints_supported=False)
        result["run_id"] = run_id
        return result

    def _supervise(self, cmd: List[str], workdir: str, env: Dict, timeout: int,
                   run_id: str, events: RunEventLog) -> Tuple[Optional[int], str, str, bool, bool]:
        """Run the child under a supervision loop instead of a blocking wait:
        stdout/stderr stream to the event sidecar line-by-line as they happen,
        new egress destinations are surfaced live, and the loop honors an
        abort request (run status flipped to 'aborting' by the abort endpoint
        — works across workers because the DB carries the signal).

        Returns (exit_code, stdout, stderr, timed_out, aborted)."""
        out_lines: List[str] = []
        err_lines: List[str] = []

        try:
            proc = subprocess.Popen(
                cmd, cwd=workdir, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
            )
        except Exception as e:
            return None, "", f"could not start interpreter: {e}", False, False

        def _pump(pipe, sink, stream_name):
            try:
                for line in iter(pipe.readline, ""):
                    sink.append(line)
                    events.emit("log", stream=stream_name, line=line.rstrip("\n")[:2000])
            except Exception:
                pass
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        readers = [
            threading.Thread(target=_pump, args=(proc.stdout, out_lines, "out"), daemon=True),
            threading.Thread(target=_pump, args=(proc.stderr, err_lines, "err"), daemon=True),
        ]
        for t in readers:
            t.start()

        deadline = time.monotonic() + timeout
        egress_pos = 0
        next_status_poll = 0.0
        timed_out = aborted = False
        egress_path = os.path.join(workdir, _EGRESS_LOG_NAME)
        self._touch_heartbeat(workdir)

        while proc.poll() is None:
            now = time.monotonic()
            if now > deadline:
                timed_out = True
                self._kill(proc)
                break
            if now >= next_status_poll:
                next_status_poll = now + _STATUS_POLL_SECONDS
                self._touch_heartbeat(workdir)
                try:
                    if self._db_get_run_field(run_id, "status") == "aborting":
                        aborted = True
                        events.emit("abort", note="abort requested by user — terminating")
                        self._kill(proc)
                        break
                except Exception:
                    pass  # a status-poll hiccup must never kill a healthy run
            # stream any new egress destinations
            try:
                with open(egress_path, "r", encoding="utf-8") as f:
                    f.seek(egress_pos)
                    for line in f:
                        if line.strip():
                            events.emit("egress", dest=line.strip()[:300])
                    egress_pos = f.tell()
            except FileNotFoundError:
                pass
            except Exception:
                pass
            time.sleep(_SUPERVISE_TICK_SECONDS)

        for t in readers:
            t.join(timeout=5)
        exit_code = proc.poll()
        return exit_code, "".join(out_lines), "".join(err_lines), timed_out, aborted

    @staticmethod
    def _kill(proc):
        try:
            proc.kill()
        except Exception:
            pass

    # ------------------------------------------------------------- reaper

    @staticmethod
    def _touch_heartbeat(workdir):
        try:
            with open(os.path.join(workdir, _HEARTBEAT_NAME), "w", encoding="utf-8") as f:
                f.write(str(time.time()))
        except Exception:
            pass  # a heartbeat hiccup must never affect a healthy run

    def _db_list_nonterminal_runs(self) -> List[Dict]:
        conn = self._db_conn()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT run_id, automation_id, status, log_path, started_at
               FROM AutomationRuns WHERE status IN ('running', 'waiting', 'aborting')""")
        rows = cursor.fetchall()
        conn.close()
        return [{"run_id": r.run_id, "automation_id": r.automation_id, "status": r.status,
                 "log_path": r.log_path, "started_at": r.started_at} for r in rows]

    def reap_orphan_runs(self, grace_s: int = _REAP_GRACE_SECONDS,
                         stale_s: int = _REAP_STALE_SECONDS) -> List[Dict]:
        """Finalize non-terminal runs whose supervisor is dead (james
        2026-07-21: 4 orphan incidents in one day — restarts leave runs stuck
        in waiting/running/aborting forever, haunting Live Now and the
        approvals trail).

        Liveness = the <workdir>/_heartbeat file every supervising process
        maintains — so a run legitimately supervised by ANOTHER process
        (scheduler engine, executor service) has a fresh heartbeat and is
        never touched. A run younger than grace_s is skipped (its workdir/
        heartbeat may not exist yet). Finalizing goes through _db_finish_run,
        the chokepoint that also cancels the run's undecided My Approvals
        rows; undecided checkpoint files additionally get an abort decision
        so a half-alive script polling its gate terminates itself.
        Returns a report of the reaped runs."""
        import datetime as _dt
        reaped = []
        for run in self._db_list_nonterminal_runs():
            started = run.get("started_at")
            if started is not None:
                age = (_dt.datetime.utcnow() - started).total_seconds()
                if age < grace_s:
                    continue
            workdir = os.path.dirname(run.get("log_path") or "") or None
            hb_age = None
            if workdir and os.path.isdir(workdir):
                hb = os.path.join(workdir, _HEARTBEAT_NAME)
                if os.path.isfile(hb):
                    hb_age = time.time() - os.path.getmtime(hb)
                    if hb_age < stale_s:
                        continue  # a living supervisor owns this run — leave it
            note = (f"orphaned run reaped: no living supervisor "
                    f"(heartbeat {'%.0fs stale' % hb_age if hb_age is not None else 'absent'}; "
                    f"typically a service restart mid-run)")
            # decide any open gates as aborted so zombie scripts self-terminate
            if workdir and os.path.isdir(workdir):
                try:
                    from .checkpoints import list_checkpoints, decide_checkpoint
                    for c in list_checkpoints(workdir):
                        if not c.get("decision"):
                            decide_checkpoint(workdir, c["checkpoint_id"], "abort",
                                              "system:reaper")
                except Exception as e:
                    logger.warning(f"reaper checkpoint-abort failed for {run['run_id']}: {e}")
            try:
                # the finalize chokepoint also cancels bridged approval rows
                self._db_finish_run(run["run_id"], "aborted", None, None, None, note)
                logger.info(f"[reaper] finalized orphaned run {run['run_id']} "
                            f"({run['status']}) — {note}")
                reaped.append({"run_id": run["run_id"],
                               "automation_id": run["automation_id"],
                               "was_status": run["status"], "note": note})
            except Exception as e:
                logger.error(f"reaper could not finalize {run['run_id']}: {e}")
        return reaped
        try:
            proc.wait(timeout=10)
        except Exception:
            pass

    @staticmethod
    def _log_header(name: str, ident: str, version: int, python_exe: Optional[str]) -> str:
        return (f"automation: {name} ({ident})\n"
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
