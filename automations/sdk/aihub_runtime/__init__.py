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

import atexit as _atexit
import json as _json
import os as _os
import re as _re
import sys as _sys
import urllib.error as _urlerror
import urllib.request as _urlrequest

__all__ = ["connection", "secret", "input", "inputs", "log", "checkpoint", "query",
           "review_item", "review_decisions", "review_decisions_detailed",
           "send_email", "llm", "ai_extract",
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


# --- dead-predicate detection -------------------------------------------------
# A filter written against a value that does not exist returns zero rows forever,
# raises nothing, and silently disables whatever rule it implements. Live failure:
# a dunning automation filtered `activity_type = 'promise_to_pay'` on a column
# holding 'ptp'; the promise-to-pay hold never fired and a customer who had
# already promised to pay was sent a dunning letter.
#
# Schema/value grounding prevents that at authoring time. This catches it at RUN
# time, which is the half grounding cannot reach: values discovered once, months
# ago, go stale when someone adds a new code.
#
# Signal, not noise: one execution returning nothing is ordinary ("any exceptions
# today? none"). The same statement returning nothing on EVERY one of several
# executions is a filter that cannot match.
#
# The verdict is deliberately deferred to END OF RUN. Warning inline the moment a
# statement crosses the run threshold would fire on a per-row loop whose first
# three iterations legitimately find nothing and whose fourth hits -- a false
# positive, and false positives are how a warning gets ignored. Only once every
# execution is in can "never matched" be asserted.
_QUERY_STATS = {}
_DEAD_QUERY_MIN_RUNS = 3


def _note_query_result(sql, row_count):
    try:
        key = " ".join(str(sql).split())[:400]
        st = _QUERY_STATS.setdefault(key, {"runs": 0, "hits": 0})
        st["runs"] += 1
        if row_count:
            st["hits"] += 1
    except Exception:
        pass  # instrumentation must never break a run


def _report_dead_queries():
    """Emit the end-of-run verdict. Registered with atexit, so it lands in the
    run log after the script's own output and is visible in the dry-run."""
    try:
        dead = [(k, s) for k, s in _QUERY_STATS.items()
                if s["hits"] == 0 and s["runs"] >= _DEAD_QUERY_MIN_RUNS]
        if not dead:
            return
        log(f"WARNING: {len(dead)} quer{'y' if len(dead) == 1 else 'ies'} ran repeatedly "
            f"and matched NOTHING, every time. A filter comparing against a value that "
            f"does not exist raises no error and silently disables the rule it implements "
            f"— check these literals against the column's real values "
            f"(get_connection_schema lists them):")
        for key, st in dead:
            log(f"  - {st['runs']}x, 0 rows every time: {key[:240]}")
    except Exception:
        pass


# Guard the registration: a reload (tests, or any host that re-imports the SDK)
# re-executes this module in its existing namespace, and an unguarded register()
# would stack a duplicate reporter on every pass.
if not globals().get("_DEAD_QUERY_REPORTER_REGISTERED"):
    _atexit.register(_report_dead_queries)
    _DEAD_QUERY_REPORTER_REGISTERED = True


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
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        _note_query_result(sql, len(rows))
        return rows
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


def _ai_call(body):
    token = _os.environ.get("AIHUB_RUN_TOKEN")
    if not token:
        raise AutomationRuntimeError("aihub.llm/ai_extract require the run token "
                                     "(AIHUB_RUN_TOKEN missing)")
    body["token"] = token
    try:
        res = _runtime_post("/automations/api/runtime/ai", body)
    except AutomationRuntimeError:
        raise
    except Exception as e:
        raise AutomationRuntimeError(f"AI call failed: {e}") from None
    if res.get("error"):
        raise AutomationRuntimeError(f"AI call failed: {res['error']}")
    return res


def llm(prompt, system=None, images=None, model=None, max_tokens=1500):
    """Ask the platform's LLM a PLAIN prompt and get the text back.

    The call is brokered by the AI Hub application: it supplies the tenant's
    API key and resolves the model centrally (override chain: this call's
    `model` > the platform's AUTOMATIONS_AI_MODEL > the platform default), so
    scripts carry no key and no model id that can go stale.

        summary = aihub.llm("Summarize this log in two sentences:\\n" + log_text)

    images: optional list of workdir-relative image paths (vision).
    Use ai_extract() when you need structured JSON back."""
    body = {"prompt": str(prompt), "max_tokens": max_tokens}
    if system:
        body["system"] = str(system)
    if images:
        body["images"] = [str(f) for f in images]
    if model:
        body["model"] = str(model)
    return _ai_call(body).get("text", "")


def ai_extract(prompt, images=None, schema=None, system=None, model=None, max_tokens=1500):
    """Ask the platform's LLM for STRUCTURED data — returns a parsed dict.

    Same central key/model brokering as llm(). If `schema` (a JSON-schema-ish
    dict) is given it is enforced in the instructions; either way the server
    parses the reply as JSON (with one self-repair retry) so you never handle
    fences or bad JSON yourself.

        data = aihub.ai_extract("Read this form page.", images=["page1.png"],
                                schema={"employee_number": "string", "confidence": "number"})
    """
    body = {"prompt": str(prompt), "json": True, "max_tokens": max_tokens}
    if schema is not None:
        body["schema"] = schema
    if system:
        body["system"] = str(system)
    if images:
        body["images"] = [str(f) for f in images]
    if model:
        body["model"] = str(model)
    return _ai_call(body).get("json")


def review_item(message, title=None, files=None, assignee=None, assignee_group=None,
                correctable=None):
    """Send a NON-BLOCKING review item to the My Approvals queue and continue.

    Use for per-document exceptions in a batch: the run keeps going while a
    human reviews each kicked-out item ("kick the exceptions to the queue and
    move on"). Returns the queue request_id, or None if the queue is
    unavailable (the failure is logged; a review item must never break the
    batch). files are workdir-relative paths attached for the reviewer (e.g.
    the problem PDF); assignee is an optional user id (defaults to the user
    who started the run); assignee_group is an optional platform GROUP name
    or id — any member sees and works the item (wins over assignee).

    correctable: optional {field: current value} dict (BRD §10 fix-and-
    approve). The approvals UI renders these as editable inputs; the
    reviewer's values come back via review_decisions_detailed() under
    'corrections'. ALWAYS re-validate corrected values before using them.

    For a BLOCKING gate that pauses the run, use checkpoint() instead."""
    import os as __os
    if __os.environ.get("AIHUB_CHECKPOINTS_ENABLED") == "0":
        log(f"review item skipped (unsupervised context): {message}")
        return None
    token = __os.environ.get("AIHUB_RUN_TOKEN")
    if not token:
        log("review item skipped: no run token")
        return None
    body = {"token": token, "message": str(message)}
    if title:
        body["title"] = str(title)
    if files:
        body["files"] = [str(f) for f in files]
    if assignee is not None:
        body["assignee"] = assignee
    if assignee_group is not None:
        body["assignee_group"] = assignee_group
    if correctable is not None:
        body["correctable"] = {str(k): ("" if v is None else str(v))
                               for k, v in dict(correctable).items()}
    try:
        res = _runtime_post("/automations/api/runtime/review_item", body)
        rid = res.get("request_id")
        log(f"review item queued for approval review: {title or message} ({rid})")
        return rid
    except Exception as e:
        log(f"review item could not be queued (continuing): {e}")
        return None


def review_decisions(request_ids):
    """Poll the decisions of review items THIS run created with review_item().

    Returns {request_id: 'pending' | 'approved' | 'rejected' | 'cancelled' |
    'missing'} — or None when the queue can't be reached (transport error /
    no run token). Callers treat None as "still pending" and keep their own
    deadline: a decision flow must fail SAFE (undecided = excluded), never
    guess. Non-blocking — call it inside your own wait loop."""
    import os as __os
    ids = [str(r) for r in (request_ids or []) if r]
    if not ids:
        return {}
    token = __os.environ.get("AIHUB_RUN_TOKEN")
    if not token:
        return None
    try:
        res = _runtime_post("/automations/api/runtime/review_items_status",
                            {"token": token, "request_ids": ids})
        statuses = res.get("statuses") or {}
        return {rid: (statuses.get(rid) or {}).get("status", "missing") for rid in ids}
    except Exception as e:
        log(f"review_decisions poll failed (treating as pending): {e}")
        return None


def review_decisions_detailed(request_ids):
    """Like review_decisions(), but returns the full decision record per id:

        {request_id: {"status": 'pending'|'approved'|'rejected'|'cancelled'|'missing',
                      "corrections": {field: value} or None,   # reviewer-entered fixes
                      "responded_by": str or None,
                      "comments": str or None}}

    or None when the queue can't be reached (treat as still-pending, keep your
    own deadline). 'corrections' carries the reviewer's fix-and-approve values
    (BRD §10) — they are RAW USER INPUT: re-validate against your reference
    data before including anything in an output."""
    import os as __os
    ids = [str(r) for r in (request_ids or []) if r]
    if not ids:
        return {}
    token = __os.environ.get("AIHUB_RUN_TOKEN")
    if not token:
        return None
    try:
        res = _runtime_post("/automations/api/runtime/review_items_status",
                            {"token": token, "request_ids": ids})
        statuses = res.get("statuses") or {}
        out = {}
        for rid in ids:
            s = statuses.get(rid) or {}
            out[rid] = {"status": s.get("status", "missing"),
                        "corrections": s.get("corrections") if isinstance(s.get("corrections"), dict) else None,
                        "responded_by": s.get("responded_by"),
                        "comments": s.get("comments")}
        return out
    except Exception as e:
        log(f"review_decisions poll failed (treating as pending): {e}")
        return None


def send_email(to, subject, body="", html_body=None, files=None):
    """Send a notification email THROUGH the platform and continue.

    The application holds the mail credentials and sends on the script's
    behalf, so an automation never carries them (same seam as ai_extract for
    model keys). `to` is an address, a list, or a ';'/',' separated string;
    `files` are workdir-relative attachments (<=10 files, 8 MB total).

    Returns True if the platform accepted the send, else False — delivery
    failure is REPORTED, never fatal, because a batch that produced a good CSV
    must not be lost to a mail outage (BRD 7.3 treats email delivery failure as
    a reportable exception)."""
    import os as __os
    if isinstance(to, str):
        to = [p.strip() for p in _re.split(r"[;,]", to) if p.strip()]
    to = [t for t in (to or []) if t]
    if not to:
        log("email skipped: no recipients configured")
        return False
    token = __os.environ.get("AIHUB_RUN_TOKEN")
    if not token:
        log(f"email skipped (no run token): {subject}")
        return False
    payload = {"token": token, "to": to, "subject": str(subject), "body": str(body or "")}
    if html_body:
        payload["html_body"] = str(html_body)
    if files:
        payload["files"] = [str(f) for f in files]
    try:
        res = _runtime_post("/automations/api/runtime/notify_email", payload)
        if res.get("sent"):
            log(f"email sent to {len(to)} recipient(s): {subject}")
            return True
        log(f"email NOT sent ({res.get('error', 'unknown error')}): {subject}")
        return False
    except Exception as e:
        log(f"email could not be sent (continuing): {e}")
        return False


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


def checkpoint(message, poll_seconds=2, files=None, assignee=None, assignee_group=None):
    """PAUSE the run at a human-judgment gate and wait for a decision.

    Shows `message` to the user in Mission Control / the Studio panel (keep it
    concrete: "About to upload 1,240 rows to acme-sftp — 3x larger than last
    run"). Blocks until a Developer clicks Proceed (returns True) or Abort
    (raises AutomationAborted). The automation's overall timeout still
    applies while waiting — an unanswered gate times the run out honestly.

    The gate ALSO lands in the platform's My Approvals queue (same queue as
    the workflow Human Approval node), so it can be decided from either place.

    files: optional list of paths (relative to the run's working directory)
    the approver can download while deciding — e.g. the report you are about
    to send: aihub.checkpoint("Send this?", files=["out/report.xlsx"]).
    assignee: optional user id (int) to route the approval to; defaults to
    the user who started the run. assignee_group: optional platform GROUP
    name or id — any member of the group sees and can decide the approval
    (wins over assignee when both are given).

    Use before irreversible steps: uploads, deletions, sends, anything that
    crosses a system boundary with unusual data."""
    import time as _time

    # Human-approval gates need a SUPERVISED live run to pause/resume against
    # (Mission Control shows the gate; a Developer clicks Proceed/Abort). A Code
    # Flow step runs without an AutomationRuns row backing it, so the runner
    # signals AIHUB_CHECKPOINTS_ENABLED=0 for that context. Rather than 403 at
    # the gate, auto-approve and say so plainly — the gate takes effect once the
    # process is promoted to an Automation (which IS supervised).
    if _os.environ.get("AIHUB_CHECKPOINTS_ENABLED") == "0":
        log(f"checkpoint auto-approved (not a supervised Automation run — human "
            f"gates apply once this is promoted to an Automation): {message}")
        return True

    token = _os.environ.get("AIHUB_RUN_TOKEN")
    if not token:
        raise AutomationRuntimeError(
            "checkpoint() requires the run token (AIHUB_RUN_TOKEN missing)")
    body = {"token": token, "message": str(message)}
    if files:
        if not isinstance(files, (list, tuple)):
            raise AutomationRuntimeError("checkpoint(files=...) must be a list of paths")
        body["files"] = [str(f) for f in files]
    if assignee is not None:
        body["assignee"] = assignee
    if assignee_group is not None:
        body["assignee_group"] = assignee_group
    try:
        created = _runtime_post("/automations/api/runtime/checkpoint", body)
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
        except _urlerror.HTTPError as e:
            # If the platform says this run is no longer live (e.g. it was
            # reaped as an orphan after a service restart), STOP — polling
            # forever as a zombie is how ghost runs haunted Live Now.
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                body = ""
            if "does not match a live run" in body:
                log("checkpoint gate closed: the platform no longer considers this "
                    "run live (likely reaped after a restart) — aborting")
                raise AutomationAborted(str(message))
            continue  # other HTTP hiccups are transient — the gate stands
        except Exception:
            continue  # transient poll failure — the gate stands; keep waiting
        if decision == "proceed":
            log("checkpoint approved — continuing")
            return True
        if decision == "abort":
            log("checkpoint declined — aborting")
            raise AutomationAborted(str(message))
