"""
aihub_runtime — the in-script SDK for AI Hub Automations.

Generated automation code imports this to reach platform resources WITHOUT
credentials ever appearing in the code, argv, or (by default) the process
environment:

    import aihub_runtime as aihub

    conn_str = aihub.connection("ERPDB")     # ODBC connection string
    sftp_url = aihub.secret("ACME_SFTP")     # value from the local secrets store
    period   = aihub.input("period", "current")
    aihub.log("extracted 214 employees")

How it works: the runner injects AIHUB_RUN_TOKEN (a signed token scoped to this
one run, carrying an allowlist of the manifest-declared connection/secret
names) and AIHUB_RUNTIME_URL. connection()/secret() POST the opaque token to
the main app's /automations/api/runtime/resolve endpoint, which verifies the
signature + scope and resolves the value server-side. Values are cached in
process memory only.

Stdlib-only ON PURPOSE — this module is PYTHONPATH-injected into minimal
per-automation venvs, so it must not require pip installs.

Back-compat: when the platform runs with AUTOMATIONS_ENV_CRED_INJECTION
enabled, credentials are also present as AIHUB_CONN_<NAME>/AIHUB_SECRET_<NAME>
env vars; this SDK prefers those when set (no HTTP round-trip).
"""

import json as _json
import os as _os
import re as _re
import sys as _sys
import urllib.error as _urlerror
import urllib.request as _urlrequest

__all__ = ["connection", "secret", "input", "inputs", "log", "checkpoint", "query",
           "AutomationRuntimeError", "AutomationAborted"]

_RESOLVE_PATH = "/automations/api/runtime/resolve"
_HTTP_TIMEOUT = int(_os.getenv("AIHUB_RUNTIME_HTTP_TIMEOUT", "30") or "30")

_cache = {}
_inputs_cache = None


class AutomationRuntimeError(RuntimeError):
    """A platform resource could not be resolved for this run."""


class AutomationAborted(SystemExit):
    """A human declined a checkpoint — the run is being stopped. Exits with
    code 75 if uncaught; the platform records the honest outcome 'aborted'
    regardless (the supervisor terminates the process)."""

    def __init__(self, message):
        super().__init__(75)
        self.message = message


def _env_var_name(prefix, name):
    return prefix + _re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def _resolve(kind, name):
    key = (kind, name)
    if key in _cache:
        return _cache[key]

    # env-var fast path (only present when the platform enables it)
    env_val = _os.environ.get(_env_var_name(
        "AIHUB_CONN_" if kind == "connection" else "AIHUB_SECRET_", name))
    if env_val:
        _cache[key] = env_val
        return env_val

    token = _os.environ.get("AIHUB_RUN_TOKEN")
    base_url = (_os.environ.get("AIHUB_RUNTIME_URL") or "").rstrip("/")
    if not token or not base_url:
        raise AutomationRuntimeError(
            f"cannot resolve {kind} '{name}': this process was not started by the "
            "automation runner (AIHUB_RUN_TOKEN/AIHUB_RUNTIME_URL missing)")

    body = _json.dumps({"token": token, "kind": kind, "name": name}).encode("utf-8")
    req = _urlrequest.Request(
        base_url + _RESOLVE_PATH, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with _urlrequest.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
    except _urlerror.HTTPError as e:
        try:
            detail = _json.loads(e.read().decode("utf-8")).get("error", "")
        except Exception:
            detail = ""
        raise AutomationRuntimeError(
            f"could not resolve {kind} '{name}': HTTP {e.code} {detail}".strip()) from None
    except Exception as e:
        raise AutomationRuntimeError(
            f"could not resolve {kind} '{name}': {e}") from None

    value = payload.get("value")
    if value is None:
        raise AutomationRuntimeError(
            f"could not resolve {kind} '{name}': {payload.get('error', 'no value returned')}")
    _cache[key] = value
    return value


def connection(name):
    """Return the connection string for a platform Connection by name.
    The name must be declared in the automation manifest's "connections"."""
    return _resolve("connection", name)


def secret(name):
    """Return the value of a local secret by name.
    The name must be declared in the automation manifest's "secrets"."""
    return _resolve("secret", name)


def query(connection_name, sql, params=None):
    """Run SQL against a platform Connection BY NAME and return the rows as a
    list of dicts (column name -> value). Use this instead of hand-rolling
    pyodbc / SQLAlchemy — it resolves the connection (which must be declared in
    the step's `connections`), opens a pyodbc connection to that ODBC string,
    executes `sql` with optional `params`, and returns the result set.

        for row in aihub.query("AIRDB", "SELECT id, name FROM employees WHERE dept = ?", ["Sales"]):
            print(row["id"], row["name"])

    A non-SELECT statement is committed and returns []. `params` is a sequence
    bound to the statement's `?` placeholders (use them — never string-format
    values into SQL). Needs the 'pyodbc' package (present in the standard run
    environments; otherwise declare 'pyodbc' in the step's packages)."""
    conn_str = connection(connection_name)   # resolves + enforces the manifest allowlist
    try:
        import pyodbc  # lazy: keep this module stdlib-only at import time
    except ImportError:
        raise AutomationRuntimeError(
            "aihub.query needs the 'pyodbc' package — declare 'pyodbc' in the step's packages"
        ) from None
    try:
        cn = pyodbc.connect(conn_str)
    except Exception as e:
        raise AutomationRuntimeError(
            f"aihub.query could not connect to '{connection_name}': {e}") from None
    try:
        cur = cn.cursor()
        if params:
            cur.execute(sql, list(params))
        else:
            cur.execute(sql)
        if cur.description is None:            # non-SELECT (INSERT/UPDATE/DDL)
            cn.commit()
            return []
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        raise AutomationRuntimeError(
            f"aihub.query failed on '{connection_name}': {e}") from None
    finally:
        try:
            cn.close()
        except Exception:
            pass


def inputs():
    """Return this run's resolved inputs as a dict (manifest defaults applied)."""
    global _inputs_cache
    if _inputs_cache is None:
        path = _os.environ.get("AIHUB_INPUTS_PATH")
        if path and _os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                _inputs_cache = _json.load(f)
        else:
            _inputs_cache = {}
    return dict(_inputs_cache)


def input(name, default=None):  # noqa: A001 - deliberate, reads naturally in scripts
    """Return one run input by name (manifest defaults already applied)."""
    return inputs().get(name, default)


def log(message):
    """Structured progress line; lands in the run log (stdout)."""
    print(f"[aihub] {message}", flush=True)


def _runtime_post(path, body):
    base_url = (_os.environ.get("AIHUB_RUNTIME_URL") or "").rstrip("/")
    if not base_url:
        raise AutomationRuntimeError(
            "not started by the automation runner (AIHUB_RUNTIME_URL missing)")
    req = _urlrequest.Request(
        base_url + path, data=_json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with _urlrequest.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        return _json.loads(resp.read().decode("utf-8"))


def checkpoint(message, poll_seconds=2):
    """PAUSE the run at a human-judgment gate and wait for a decision.

    Shows `message` to the user in Mission Control / the Studio panel (keep it
    concrete: "About to upload 1,240 rows to acme-sftp — 3x larger than last
    run"). Blocks until a Developer clicks Proceed (returns True) or Abort
    (raises AutomationAborted). The automation's overall timeout still
    applies while waiting — an unanswered gate times the run out honestly.

    Use before irreversible steps: uploads, deletions, sends, anything that
    crosses a system boundary with unusual data."""
    import time as _time

    token = _os.environ.get("AIHUB_RUN_TOKEN")
    if not token:
        raise AutomationRuntimeError(
            "checkpoint() requires the run token (AIHUB_RUN_TOKEN missing)")
    try:
        created = _runtime_post("/automations/api/runtime/checkpoint",
                                {"token": token, "message": str(message)})
    except AutomationRuntimeError:
        raise
    except Exception as e:
        raise AutomationRuntimeError(f"could not open checkpoint: {e}") from None
    checkpoint_id = created.get("checkpoint_id")
    if not checkpoint_id:
        raise AutomationRuntimeError(
            f"could not open checkpoint: {created.get('error', 'no id returned')}")
    log(f"checkpoint: {message} — waiting for a decision")

    base_url = (_os.environ.get("AIHUB_RUNTIME_URL") or "").rstrip("/")
    from urllib.parse import urlencode as _urlencode
    query = _urlencode({"token": token, "checkpoint_id": checkpoint_id})
    while True:
        _time.sleep(max(1, int(created.get("poll_seconds", poll_seconds))))
        try:
            with _urlrequest.urlopen(
                    base_url + "/automations/api/runtime/checkpoint?" + query,
                    timeout=_HTTP_TIMEOUT) as resp:
                decision = _json.loads(resp.read().decode("utf-8")).get("decision")
        except Exception:
            continue  # transient poll failure — the gate stands; keep waiting
        if decision == "proceed":
            log("checkpoint approved — continuing")
            return True
        if decision == "abort":
            log("checkpoint declined — aborting")
            raise AutomationAborted(str(message))
