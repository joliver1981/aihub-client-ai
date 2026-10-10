"""Workflow file drops (2026-10-10).

Workflows that take files in and put files out run on the server, while the
people who own those files work in a browser. File drops give each such
workflow a managed set of folders on the server and a way to reach them from
the web:

    <WORKFLOW_FILES_ROOT>/<workflow id>/
        inbox       files waiting to be processed (people drop files here)
        processed   files the workflow finished with
        failed      files the workflow set aside
        outbox      what the workflow produced (people collect results here)

Setting a workflow up points its folder variables at these folders, the same
change a Designer edit makes; a power user can still point a variable at a
network share or SharePoint instead. Per-workflow settings, who dropped which
file and one record per run live OUTSIDE the files root, under
<APP_ROOT>/data/workflow_file_drops/<workflow id>/: the files root may be a
share people can write to, and the access list must not be.

Access: Developers and Admins (the Workflow Designer's bar) on every workflow;
anyone else only when the workflow is shared with them or with a group they
belong to. People it is shared with can drop files, see the folders, collect
results, retry a failed file and start a run, without Designer access.

Results come back to people: the executor calls run_started() and
run_finished() around every run of a set-up workflow. run_finished() records
what the run took from the inbox and what it produced, sends a My Work notice
with download links to whoever started the run or dropped the files it
processed (the owner hears about runs nobody started), copies new outputs to
SharePoint when that is configured, and starts the run someone asked for
while this one was busy, so two runs never work the same inbox at once.

Everything the executor calls is best effort: a failure is logged and never
changes a run. Kill switch WORKFLOW_FILE_DROPS_ENABLED=false hides the feature
everywhere; folders, files and settings stay where they are.
"""
import datetime
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import uuid
from typing import Optional

logger = logging.getLogger("WorkflowFileDrops")

FOLDERS = ("inbox", "processed", "failed", "outbox")
FOLDER_LABELS = {"inbox": "Waiting", "processed": "Processed", "failed": "Failed", "outbox": "Results"}
ROLE_LABELS = {"inbox": "the folder it reads new files from",
               "processed": "the folder it moves finished files to",
               "failed": "the folder it moves files it could not process to",
               "outbox": "where it writes its results"}


def enabled() -> bool:
    return (os.getenv("WORKFLOW_FILE_DROPS_ENABLED", "true") or "true").strip().lower() \
        not in ("false", "0", "no", "off")


# ------------------------------------------------------------------ locations
def _app_root() -> str:
    explicit = os.getenv("APP_ROOT")
    if explicit and os.path.isdir(explicit):
        return os.path.abspath(explicit)
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


def files_root() -> str:
    """Where the managed folders live (WORKFLOW_FILES_ROOT, default
    <APP_ROOT>/data/workflow_files)."""
    return os.path.abspath(os.getenv("WORKFLOW_FILES_ROOT")
                           or os.path.join(_app_root(), "data", "workflow_files"))


def state_root() -> str:
    """Settings, upload records and run records (never inside files_root)."""
    return os.path.join(_app_root(), "data", "workflow_file_drops")


def workflow_root(workflow_id) -> str:
    return os.path.join(files_root(), str(int(workflow_id)))


def folder_path(workflow_id, folder: str) -> str:
    if folder not in FOLDERS:
        raise ValueError(f"Unknown folder '{folder}' (use one of: {', '.join(FOLDERS)}).")
    return os.path.join(workflow_root(workflow_id), folder)


def _incoming_dir(workflow_id) -> str:
    """Uploads are written here first and then renamed into the inbox, so a
    run never picks up a half-written file."""
    return os.path.join(workflow_root(workflow_id), ".incoming")


def _state_dir(workflow_id, *parts) -> str:
    return os.path.join(state_root(), str(int(workflow_id)), *parts)


def ensure_folders(workflow_id) -> dict:
    paths = {f: folder_path(workflow_id, f) for f in FOLDERS}
    for p in list(paths.values()) + [_incoming_dir(workflow_id)]:
        os.makedirs(p, exist_ok=True)
    return paths


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{uuid.uuid4().hex}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, default=str)
    os.replace(tmp, path)


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning(f"could not read {path}: {e}")
        return None


# ------------------------------------------------------------------- settings
def load_settings(workflow_id) -> Optional[dict]:
    try:
        return _read_json(_state_dir(workflow_id, "settings.json"))
    except (TypeError, ValueError):
        return None


def save_settings(workflow_id, settings: dict) -> dict:
    settings = dict(settings or {})
    settings["workflow_id"] = int(workflow_id)
    settings["updated_at"] = _now()
    _write_json(_state_dir(workflow_id, "settings.json"), settings)
    return settings


def is_set_up(workflow_id) -> bool:
    s = load_settings(workflow_id)
    return bool(enabled() and s and s.get("enabled"))


def all_settings() -> list:
    """Every workflow that has file drops settings (enabled or not)."""
    out = []
    root = state_root()
    try:
        names = os.listdir(root)
    except FileNotFoundError:
        return out
    for name in names:
        if name.isdigit():
            s = _read_json(os.path.join(root, name, "settings.json"))
            if isinstance(s, dict):
                out.append(s)
    return out


def default_settings(owner: dict) -> dict:
    return {
        "enabled": True,
        "owner": {"user_id": owner.get("user_id"), "name": owner.get("name") or ""},
        "created_at": _now(),
        "variables": {},
        "previous_values": {},
        "grants": {"users": [], "groups": []},
        "run_on_upload": False,
        "notify_owner": True,
        "email_to_inbox": True,
        "sharepoint": None,
    }


# --------------------------------------------------------------------- access
def can_manage(user: dict) -> bool:
    """Set up, change settings and share: Developers and Admins (and trusted
    service callers), the Workflow Designer's bar."""
    if (user or {}).get("service"):
        return True
    try:
        return int((user or {}).get("role") or 0) >= 2
    except (TypeError, ValueError):
        return False


def can_use(user: dict, settings: Optional[dict], group_ids=None) -> bool:
    """Drop files, collect results, retry and run: managers, plus the people
    and groups the workflow is shared with."""
    if can_manage(user):
        return True
    if not settings or not settings.get("enabled"):
        return False
    grants = settings.get("grants") or {}
    try:
        uid = int((user or {}).get("user_id") or 0)
    except (TypeError, ValueError):
        return False
    if uid and uid in {int(u) for u in (grants.get("users") or []) if str(u).lstrip("-").isdigit()}:
        return True
    shared_groups = {int(g) for g in (grants.get("groups") or []) if str(g).lstrip("-").isdigit()}
    return bool(shared_groups & {int(g) for g in (group_ids or [])})


# ---------------------------------------------------------------- file names
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
# Windows paths stop at 260 characters unless long paths are enabled; the
# managed folders sit ~60 characters deep, so a name keeps to what fits.
_NAME_MAX = 180


def safe_name(name) -> str:
    """The file name as stored: its last path component, without characters
    Windows forbids or a device name. Raises ValueError when nothing is left."""
    raw = str(name or "").replace("\\", "/").split("/")[-1]
    clean = _BAD_CHARS.sub("_", raw).strip().rstrip(". ")
    if not clean or clean in (".", ".."):
        raise ValueError("The file needs a name.")
    if clean.split(".")[0].upper() in _RESERVED:
        clean = "_" + clean
    if len(clean) > _NAME_MAX:
        stem, ext = os.path.splitext(clean)
        clean = stem[:_NAME_MAX - len(ext)] + ext
    return clean


def _inside(path: str, folder: str) -> bool:
    path, folder = os.path.normcase(path), os.path.normcase(folder).rstrip(os.sep)
    return path == folder or path.startswith(folder + os.sep)


def resolve_in_folder(workflow_id, folder: str, name: str) -> str:
    """Absolute path of `name` inside one managed folder; refuses anything
    that would leave it (subfolders of the outbox are allowed)."""
    base = os.path.realpath(folder_path(workflow_id, folder))
    rel = str(name or "").replace("\\", "/").strip("/")
    # ':' would be a drive or an NTFS alternate data stream, never a file here
    if not rel or ":" in rel or any(part in ("", ".", "..") for part in rel.split("/")):
        raise ValueError("That is not a file in this folder.")
    target = os.path.realpath(os.path.join(base, *rel.split("/")))
    if target == base or not _inside(target, base):
        raise ValueError("That is not a file in this folder.")
    return target


def _move_unique(src: str, folder: str, name: str) -> str:
    """Move `src` into `folder` as `name`, adding ' (2)', ' (3)', ... when the
    name is taken: never overwrites a file someone else dropped, and the file
    appears whole, in one step. Returns the new path."""
    os.makedirs(folder, exist_ok=True)
    stem, ext = os.path.splitext(name)
    i = 1
    while True:
        dst = os.path.join(folder, name if i == 1 else f"{stem} ({i}){ext}")
        try:
            if os.name == "nt":
                os.rename(src, dst)          # refuses an existing name
            else:
                os.link(src, dst)            # likewise; rename() would overwrite
                os.remove(src)
            return dst
        except FileExistsError:
            i += 1


# ------------------------------------------------------------- upload records
def _ledger_dir(workflow_id) -> str:
    return _state_dir(workflow_id, "uploads")


def _ledger_key(name: str) -> str:
    return hashlib.sha1(name.lower().encode("utf-8")).hexdigest()[:20]


def _record_upload(workflow_id, name: str, size: int, by: dict) -> None:
    _write_json(os.path.join(_ledger_dir(workflow_id), _ledger_key(name) + ".json"), {
        "name": name, "size": size, "at": _now(), "at_ts": time.time(),
        "user_id": by.get("user_id"), "user_name": by.get("name") or "",
        "email": by.get("email") or "", "via": by.get("via") or "upload"})


def uploads(workflow_id) -> dict:
    """{stored name: upload record} for files dropped into the inbox."""
    out = {}
    d = _ledger_dir(workflow_id)
    try:
        names = os.listdir(d)
    except FileNotFoundError:
        return out
    for n in names:
        if n.endswith(".json"):
            rec = _read_json(os.path.join(d, n))
            if isinstance(rec, dict) and rec.get("name"):
                out[rec["name"]] = rec
    return out


def _forget_upload(workflow_id, name: str) -> None:
    try:
        os.remove(os.path.join(_ledger_dir(workflow_id), _ledger_key(name) + ".json"))
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.debug(f"upload record for {name}: {e}")


def store_stream(workflow_id, filename: str, stream, by: dict, chunk: int = 1024 * 1024) -> dict:
    """Write an uploaded file into the inbox: streamed to .incoming first, then
    renamed in, so a run never sees half a file. Returns {name, size}."""
    name = safe_name(filename)
    ensure_folders(workflow_id)
    tmp = os.path.join(_incoming_dir(workflow_id), f"{uuid.uuid4().hex}.part")
    size = 0
    try:
        with open(tmp, "wb") as fh:
            while True:
                block = stream.read(chunk)
                if not block:
                    break
                fh.write(block)
                size += len(block)
        final = _move_unique(tmp, folder_path(workflow_id, "inbox"), name)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    stored = os.path.basename(final)
    _record_upload(workflow_id, stored, size, by)
    return {"name": stored, "size": size}


def store_bytes(workflow_id, filename: str, data: bytes, by: dict) -> dict:
    import io
    return store_stream(workflow_id, filename, io.BytesIO(data or b""), by)


def delete_file(workflow_id, folder: str, name: str) -> None:
    path = resolve_in_folder(workflow_id, folder, name)
    if not os.path.isfile(path):
        raise FileNotFoundError(name)
    os.remove(path)
    if folder == "inbox":
        _forget_upload(workflow_id, name)


def retry_file(workflow_id, name: str, by: dict) -> str:
    """Move a failed file back into the inbox (renamed if that name is
    waiting). Returns the name it has in the inbox."""
    src = resolve_in_folder(workflow_id, "failed", name)
    if not os.path.isfile(src):
        raise FileNotFoundError(name)
    final = _move_unique(src, folder_path(workflow_id, "inbox"), safe_name(os.path.basename(src)))
    stored = os.path.basename(final)
    _record_upload(workflow_id, stored, os.path.getsize(final), dict(by or {}, via="retry"))
    return stored


# ------------------------------------------------------------------- listings
def _listing(folder: str, recursive: bool = False) -> dict:
    """{relative path: [mtime_ns, size]} of the files in `folder`."""
    out = {}
    if not os.path.isdir(folder):
        return out
    if not recursive:
        with os.scandir(folder) as it:
            for e in it:
                if e.is_file():
                    st = e.stat()
                    out[e.name] = [st.st_mtime_ns, st.st_size]
        return out
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for f in filenames:
            p = os.path.join(dirpath, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out[os.path.relpath(p, folder).replace(os.sep, "/")] = [st.st_mtime_ns, st.st_size]
    return out


def counts(workflow_id) -> dict:
    out = {}
    for f in FOLDERS:
        try:
            out[f] = len(_listing(folder_path(workflow_id, f), recursive=(f == "outbox")))
        except OSError:
            out[f] = 0
    return out


def list_files(workflow_id, folder: str) -> list:
    """Files in one folder, newest first: [{name, size, modified, uploaded_by}]."""
    ledger = uploads(workflow_id) if folder == "inbox" else {}
    rows = []
    for rel, (mtime_ns, size) in _listing(folder_path(workflow_id, folder),
                                          recursive=(folder == "outbox")).items():
        rec = ledger.get(rel) or {}
        rows.append({
            "name": rel, "size": size,
            "modified": datetime.datetime.fromtimestamp(mtime_ns / 1e9, datetime.timezone.utc)
                                        .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "uploaded_by": rec.get("user_name") or rec.get("email") or "",
            "_mtime": mtime_ns})
    rows.sort(key=lambda r: r["_mtime"], reverse=True)
    for r in rows:
        r.pop("_mtime", None)
    return rows


# ------------------------------------------------------------ variable setup
_SUGGEST = {
    "inbox": ("input", "inbox", "incoming", "source", "drop", "watch", "pickup", "in"),
    "processed": ("processed", "archive", "done", "complete", "finished", "success"),
    "failed": ("failed", "fail", "error", "reject", "setaside", "exception", "bad", "quarantine"),
    "outbox": ("output", "outbox", "result", "export", "report", "workbook", "out"),
}


def _var_value(spec) -> str:
    if isinstance(spec, dict):
        v = spec.get("defaultValue", spec.get("value", ""))
    else:
        v = spec
    return "" if v is None else str(v)


def _looks_like_path(value: str) -> bool:
    return value == "" or "\\" in value or "/" in value


def suggest_mapping(workflow_data: dict) -> dict:
    """A starting point for the setup form (the person confirms it): for each
    folder, a path-like variable whose name says what it is for."""
    variables = (workflow_data or {}).get("variables") or {}
    taken, out = set(), {}
    for role in ("failed", "processed", "outbox", "inbox"):
        for name, spec in variables.items():
            if name in taken or not _looks_like_path(_var_value(spec)):
                continue
            words = re.findall(r"[a-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", name).lower())
            if any(w in _SUGGEST[role] for w in words):
                out[role] = name
                taken.add(name)
                break
    return {r: out.get(r, "") for r in FOLDERS}


def plan_variables(workflow_id, workflow_data: dict, mapping: dict) -> tuple:
    """Point the mapped variables at the managed folders. Returns
    (new workflow_data, {variable: (old, new)}, errors). An output variable
    that names a FILE (a workbook the workflow appends to) keeps its file
    name inside the outbox."""
    data = json.loads(json.dumps(workflow_data or {}))
    variables = data.setdefault("variables", {})
    if not isinstance(variables, dict):
        return data, {}, ["The workflow's variables are not in the expected form."]
    errors, changes, used = [], {}, {}
    for role in FOLDERS:
        var = str((mapping or {}).get(role) or "").strip()
        if not var:
            continue
        if var in used:
            errors.append(f"'{var}' is chosen for both {used[var]} and {role}.")
            continue
        used[var] = role
        if var not in variables:
            errors.append(f"The workflow has no variable named '{var}' "
                          f"(it has: {', '.join(sorted(variables)) or 'none'}).")
            continue
        spec = variables[var]
        if not isinstance(spec, dict):
            spec = {"name": var, "type": "string", "defaultValue": _var_value(spec)}
            variables[var] = spec
        old = _var_value(spec)
        new = folder_path(workflow_id, role)
        if role == "outbox":
            base = os.path.basename(old.replace("/", "\\").rstrip("\\"))
            if base and os.path.splitext(base)[1] and not os.path.isdir(old):
                new = os.path.join(new, base)
        spec["defaultValue"] = new
        spec.pop("value", None)
        if not spec.get("type"):
            spec["type"] = "string"
        changes[var] = (old, new)
    if not changes and not errors:
        errors.append("Choose at least the variable the workflow reads new files from.")
    return data, changes, errors


def variable_roles(settings: dict, workflow_data: dict) -> dict:
    """For each folder: the mapped variable, its current value and whether it
    still points at the managed folder (a person may have moved it)."""
    variables = (workflow_data or {}).get("variables") or {}
    wid = settings.get("workflow_id")
    out = {}
    for role in FOLDERS:
        var = (settings.get("variables") or {}).get(role) or ""
        value = _var_value(variables.get(var)) if var in variables else ""
        managed = folder_path(wid, role) if wid is not None else ""
        points_here = bool(var and value and managed
                           and _inside(os.path.abspath(value), os.path.abspath(managed)))
        out[role] = {"variable": var, "value": value, "managed": points_here,
                     "missing": bool(var and var not in variables)}
    return out


# ---------------------------------------------------------------- run records
def _runs_dir(workflow_id) -> str:
    return _state_dir(workflow_id, "runs")


def _run_path(workflow_id, execution_id) -> str:
    eid = re.sub(r"[^A-Za-z0-9_-]", "", str(execution_id))
    if not eid:
        raise ValueError("execution id required")
    return os.path.join(_runs_dir(workflow_id), eid + ".json")


def load_run(workflow_id, execution_id) -> Optional[dict]:
    try:
        return _read_json(_run_path(workflow_id, execution_id))
    except ValueError:
        return None


def recent_runs(workflow_id, limit: int = 20) -> list:
    """The latest run records, newest first (`limit` is a page size)."""
    d = _runs_dir(workflow_id)
    try:
        entries = [e for e in os.scandir(d) if e.name.endswith(".json")]
    except FileNotFoundError:
        return []
    # A record is rewritten a few seconds after its run ends (notices,
    # SharePoint), so file times only shortlist; the start time orders.
    limit = max(1, int(limit))
    entries.sort(key=lambda e: e.stat().st_mtime, reverse=True)
    out = []
    for e in entries[:limit + 10]:
        rec = _read_json(e.path)
        if isinstance(rec, dict):
            out.append(rec)
    out.sort(key=lambda r: (str(r.get("started_at") or ""), float(r.get("started_ts") or 0)), reverse=True)
    return out[:limit]


def _requester(requested_by) -> dict:
    r = requested_by if isinstance(requested_by, dict) else {}
    out = {k: r.get(k) for k in ("user_id", "name", "email", "via") if r.get(k) not in (None, "")}
    if "notify" in r:
        out["notify"] = bool(r.get("notify"))
    return out


def run_started(workflow_id, execution_id, workflow_name: str = "", initiator: str = "",
                requested_by=None) -> Optional[dict]:
    """Called by the executor as a run of a set-up workflow begins. Returns the
    snapshot run_finished() needs, or None (not set up, or switched off).
    Never raises."""
    try:
        if not enabled():
            return None
        settings = load_settings(workflow_id)
        if not (settings and settings.get("enabled")):
            return None
        wid = int(workflow_id)
        snap = {
            "workflow_id": wid, "execution_id": str(execution_id),
            "workflow_name": workflow_name or "", "initiator": initiator or "",
            "requested_by": _requester(requested_by),
            "started_at": _now(), "started_ts": time.time(),
            "before": {f: _listing(folder_path(wid, f), recursive=(f == "outbox")) for f in FOLDERS},
        }
        _write_json(_run_path(wid, execution_id), {
            "workflow_id": wid, "execution_id": str(execution_id),
            "workflow_name": snap["workflow_name"], "initiator": snap["initiator"],
            "requested_by": snap["requested_by"], "started_at": snap["started_at"],
            "started_ts": snap["started_ts"],
            "status": "Running", "waiting_at_start": len(snap["before"]["inbox"])})
        return snap
    except Exception as e:
        logger.warning(f"file drops: run start bookkeeping failed for workflow {workflow_id}: {e}")
        return None


def _changed(before: dict, after: dict) -> list:
    return sorted(k for k, v in after.items() if before.get(k) != v)


def summarize_run(snap: dict, status: str, settings: Optional[dict] = None) -> dict:
    """What a run did to the folders: taken from the inbox (with who dropped
    each file), new or updated in processed / failed / outbox."""
    wid = snap["workflow_id"]
    after = {f: _listing(folder_path(wid, f), recursive=(f == "outbox")) for f in FOLDERS}
    before = snap.get("before") or {}
    ledger = uploads(wid)
    taken = set(before.get("inbox") or {}) - set(after["inbox"])
    # files dropped while the run was going and already taken by it
    for name, rec in ledger.items():
        if name not in after["inbox"] and float(rec.get("at_ts") or 0) >= float(snap.get("started_ts") or 0):
            taken.add(name)
    consumed = []
    for name in sorted(taken):
        rec = ledger.get(name) or {}
        consumed.append({"name": name, "uploaded_by": rec.get("user_name") or rec.get("email") or "",
                         "user_id": rec.get("user_id"), "email": rec.get("email") or "",
                         "via": rec.get("via") or ""})
    outputs = []
    for rel in _changed(before.get("outbox") or {}, after["outbox"]):
        outputs.append({"name": rel, "size": after["outbox"][rel][1],
                        "new": rel not in (before.get("outbox") or {})})
    return {
        "workflow_id": wid, "execution_id": snap["execution_id"],
        "workflow_name": snap.get("workflow_name") or "", "initiator": snap.get("initiator") or "",
        "requested_by": snap.get("requested_by") or {},
        "started_at": snap.get("started_at"), "started_ts": snap.get("started_ts"),
        "finished_at": _now(), "status": status or "Unknown",
        "consumed": consumed,
        "processed": _changed(before.get("processed") or {}, after["processed"]),
        "failed": _changed(before.get("failed") or {}, after["failed"]),
        "outputs": outputs,
        "waiting_at_start": len(before.get("inbox") or {}),
        "waiting_now": len(after["inbox"]),
    }


# --------------------------------------------------------------- delivery
def _requester_wants_notice(req: dict) -> bool:
    return bool(req.get("user_id")) and req.get("notify", True) is not False


def recipients(record: dict, settings: dict) -> dict:
    """{user_id: why} — the person who started the run, the people whose files
    it processed, and the owner when nobody else hears about it."""
    people = {}
    req = record.get("requested_by") or {}
    quiet = set()
    if req.get("user_id") and req.get("notify", True) is False:
        quiet.add(str(req["user_id"]))     # The Agent delivers in chat instead
    if _requester_wants_notice(req):
        people[str(req["user_id"])] = "started"
    for c in record.get("consumed") or []:
        uid = c.get("user_id")
        if uid and str(uid) not in quiet:
            people.setdefault(str(uid), "dropped")
    if not people and not quiet and (settings or {}).get("notify_owner", True):
        owner = ((settings or {}).get("owner") or {}).get("user_id")
        if owner:
            people[str(owner)] = "owner"
    return people


def worth_telling(record: dict) -> bool:
    """A run that took nothing, produced nothing and finished cleanly (a
    schedule finding an empty inbox) is not news."""
    return bool(record.get("consumed") or record.get("outputs") or record.get("failed")
                or str(record.get("status") or "").lower() not in ("completed",))


def notice_text(record: dict, why: str) -> tuple:
    """(title, summary) for a My Work notice about one run."""
    name = record.get("workflow_name") or f"Workflow {record.get('workflow_id')}"
    status = str(record.get("status") or "Unknown")
    outputs = record.get("outputs") or []
    failed = record.get("failed") or []
    if status.lower() == "completed" and outputs:
        title = f"{name}: results ready"
    elif status.lower() == "completed":
        title = f"{name}: run finished"
    else:
        title = f"{name}: run {status.lower()}"
    lines = []
    lead = {"started": "The run you started", "dropped": "A run that processed your files",
            "owner": "A run of your workflow"}.get(why, "A run")
    lines.append(f"{lead} finished with status {status}.")
    consumed = record.get("consumed") or []
    if consumed:
        lines.append("Taken from Waiting: " + ", ".join(c["name"] for c in consumed) + ".")
    if record.get("processed"):
        lines.append("Processed: " + ", ".join(record["processed"]) + ".")
    if failed:
        lines.append("Failed (set aside): " + ", ".join(failed)
                     + ". Retry them from the workflow's Files once the cause is fixed.")
    if outputs:
        lines.append("Results: " + ", ".join(o["name"] for o in outputs) + ".")
    if record.get("waiting_now"):
        lines.append(f"Still waiting: {record['waiting_now']} file(s).")
    if record.get("sharepoint"):
        ok = [s["name"] for s in record["sharepoint"] if s.get("ok")]
        bad = [s for s in record["sharepoint"] if not s.get("ok")]
        if ok:
            lines.append("Copied to SharePoint: " + ", ".join(ok) + ".")
        for s in bad:
            lines.append(f"SharePoint copy of {s['name']} failed: {s.get('error') or 'unknown error'}")
    lines.append("Open Workflow Files in AI Hub's menu (or Files in The Agent) "
                 "for every file and earlier runs.")
    return title, "\n".join(lines)


def _send_my_work(user_id, title: str, summary: str, files: list, payload: dict, priority: int) -> bool:
    if os.getenv("THE_AGENT_ENABLED", "false").lower() != "true":
        return False
    try:
        import requests
        from CommonUtils import get_agent_service_api_base_url
        r = requests.post(
            f"{get_agent_service_api_base_url()}/api/work/internal/raise",
            json={"user_id": str(user_id), "verb": "acknowledge", "title": title, "summary": summary,
                  "payload": payload, "files": files, "priority": int(priority or 0),
                  "from_kind": "platform", "from_ref": "workflow_files", "created_by": "workflow_files"},
            headers={"X-API-Key": os.getenv("API_KEY", "")}, timeout=15)
        if r.status_code != 200:
            logger.warning(f"file drops: My Work notice for user {user_id} got HTTP {r.status_code}: "
                           f"{r.text[:200]}")
        return r.status_code == 200
    except Exception as e:
        logger.warning(f"file drops: My Work notice for user {user_id} failed: {e}")
        return False


def _handoff_copies(workflow_id, execution_id, paths: list) -> tuple:
    """(paths The Agent can hand over, temp folder to remove afterwards).
    The Agent only hands over files under APP_ROOT, so results kept on another
    drive or a share (WORKFLOW_FILES_ROOT=D:\\...) are copied under APP_ROOT
    for the hand-off and removed right after it."""
    root = os.path.abspath(_app_root())
    out, tmp = [], None
    for i, p in enumerate(paths):
        if _inside(os.path.abspath(p), root):
            out.append(p)
            continue
        try:
            if tmp is None:
                tmp = _state_dir(workflow_id, "handoff", re.sub(r"[^A-Za-z0-9_-]", "", str(execution_id))
                                 or uuid.uuid4().hex)
            dst = os.path.join(tmp, str(i), os.path.basename(p))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(p, dst)
            out.append(dst)
        except OSError as e:
            logger.warning(f"file drops: could not prepare {p} for the notice: {e}")
    return out, tmp


def notify(record: dict, settings: dict, people: dict) -> list:
    """Send each person a My Work notice; the outputs ride along as download
    links (the notice names the rest when there are more than it carries)."""
    sent = []
    wid = record["workflow_id"]
    files = []
    for o in record.get("outputs") or []:
        try:
            files.append(resolve_in_folder(wid, "outbox", o["name"]))
        except ValueError:
            continue
    priority = 0 if (str(record.get("status")).lower() == "completed" and not record.get("failed")) else 1
    payload = {"kind": "workflow_files_result", "workflow_id": wid,
               "execution_id": record.get("execution_id")}
    links, tmp = _handoff_copies(wid, record.get("execution_id"), files[:20])
    try:
        for uid, why in people.items():
            title, summary = notice_text(record, why)
            if len(files) > 20:   # the notice carries 20 links; the rest are named above
                summary += f"\n{len(files) - 20} more result file(s) are in Files."
            if _send_my_work(uid, title, summary, links, payload, priority):
                sent.append(uid)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    return sent


def copy_to_sharepoint(record: dict, settings: dict) -> list:
    """Copy this run's new and updated results to the SharePoint folder set
    on the workflow. Returns [{name, ok, error}]."""
    sp = (settings or {}).get("sharepoint") or {}
    if not (sp.get("integration_id") and sp.get("drive_id")) or not record.get("outputs"):
        return []
    try:
        from integration_manager import execute_integration_operation
    except Exception as e:
        return [{"name": o["name"], "ok": False, "error": f"integrations unavailable: {e}"}
                for o in record["outputs"]]
    out = []
    base = str(sp.get("folder_path") or "").strip("/")
    for o in record["outputs"]:
        rel_dir = os.path.dirname(o["name"]).replace("\\", "/")
        params = {"drive_id": sp["drive_id"],
                  "folder_path": "/".join(p for p in (base, rel_dir) if p),
                  "source_file_path": resolve_in_folder(record["workflow_id"], "outbox", o["name"]),
                  "filename": os.path.basename(o["name"]),
                  "conflict_behavior": sp.get("conflict_behavior") or "replace"}
        if sp.get("parent_item_id") and not rel_dir:
            params["parent_item_id"] = sp["parent_item_id"]
        try:
            res = execute_integration_operation(int(sp["integration_id"]), "upload_file", params,
                                                workflow_execution_id=record.get("execution_id"))
            ok = bool((res or {}).get("success"))
            out.append({"name": o["name"], "ok": ok,
                        "error": "" if ok else str((res or {}).get("error") or res)[:300]})
        except Exception as e:
            out.append({"name": o["name"], "ok": False, "error": str(e)[:300]})
    return out


def _flag_path(workflow_id, execution_id) -> str:
    return _run_path(workflow_id, execution_id)[:-5] + ".notify"


def _claim(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def notify_when_done(workflow_id, execution_id) -> str:
    """The Agent stopped waiting for a run it started: tell the person through
    My Work when it finishes. Returns 'sent' (it had already finished),
    'queued', or 'nothing' (it finished with nothing to report)."""
    path = _flag_path(workflow_id, execution_id)
    _write_json(path, {"at": _now()})
    rec = load_run(workflow_id, execution_id) or {}
    if rec.get("finished_at") and _claim(path):     # it ended while we were writing the flag
        return _deliver_late(rec)
    return "queued"


def _deliver_late(record: dict) -> str:
    req = record.get("requested_by") or {}
    if not req.get("user_id") or not worth_telling(record):
        return "nothing"
    settings = load_settings(record["workflow_id"]) or {}
    sent = notify(record, settings, {str(req["user_id"]): "started"})
    return "sent" if sent else "nothing"


def request_run_when_free(workflow_id, requested_by: dict) -> None:
    """A run was asked for while one is going: the running one starts it when
    it ends (one run at a time works an inbox)."""
    _write_json(_state_dir(workflow_id, "run_next.json"),
                {"requested_by": _requester(requested_by), "at": _now()})


def _claim_run_next(workflow_id) -> Optional[dict]:
    path = _state_dir(workflow_id, "run_next.json")
    rec = _read_json(path)
    if rec is None or not _claim(path):
        return None
    return rec


def start_or_queue(workflow_id, initiator: str, requested_by: dict, variables: Optional[dict] = None) -> dict:
    """For callers outside the main app (the email dispatcher): start a run of
    a set-up workflow through the executor, or, while one is going, queue it
    behind that one. {'execution_id': ...} | {'queued': True} | {'error': ...}."""
    import requests
    from CommonUtils import get_executor_api_base_url
    base = get_executor_api_base_url()
    try:
        r = requests.get(f"{base}/api/workflow/executions/active", timeout=10)
        active = ((r.json() or {}).get("active_executions") or []) if r.status_code == 200 else []
    except Exception as e:
        logger.warning(f"file drops: could not read active runs ({e}); starting the run")
        active = []
    if any(str(a.get("workflow_id")) == str(workflow_id) for a in active):
        request_run_when_free(workflow_id, requested_by)
        return {"queued": True}
    body = {"workflow_id": int(workflow_id), "initiator": initiator, "requested_by": requested_by}
    if variables:
        body["variables"] = variables
    try:
        r = requests.post(f"{base}/api/workflow/run", json=body, timeout=30)
        data = r.json() if r.content else {}
    except Exception as e:
        return {"error": str(e)}
    if r.status_code == 200 and data.get("execution_id"):
        return {"execution_id": data["execution_id"]}
    return {"error": data.get("message") or f"Workflow API returned {r.status_code}"}


def _start_next_run(workflow_id, requested_by: dict) -> Optional[str]:
    try:
        import requests
        from CommonUtils import get_executor_api_base_url
        r = requests.post(f"{get_executor_api_base_url()}/api/workflow/run",
                          json={"workflow_id": int(workflow_id),
                                "initiator": "workflow_files:queued",
                                "requested_by": requested_by}, timeout=30)
        if r.status_code == 200:
            return (r.json() or {}).get("execution_id")
        logger.warning(f"file drops: queued run of workflow {workflow_id} got HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"file drops: queued run of workflow {workflow_id} failed to start: {e}")
    return None


def _email_user_ids(emails) -> dict:
    """{email: user id} for senders who are platform users."""
    emails = sorted({str(e).strip().lower() for e in emails if e})
    if not emails:
        return {}
    try:
        from CommonUtils import get_db_connection
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
            marks = ",".join("?" for _ in emails)
            cur.execute(f"SELECT id, LOWER(email) FROM [User] WHERE LOWER(email) IN ({marks})", *emails)
            return {r[1]: r[0] for r in cur.fetchall()}
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"file drops: could not match email senders to users: {e}")
        return {}


def run_finished(snap: Optional[dict], status: str) -> Optional[dict]:
    """Called by the executor (in its own thread) when a run of a set-up
    workflow ends. Records the outcome, delivers it, and starts a run that was
    asked for meanwhile. Never raises."""
    if not snap:
        return None
    wid, eid = snap.get("workflow_id"), snap.get("execution_id")
    try:
        settings = load_settings(wid) or {}
        record = summarize_run(snap, status, settings)
        # email senders who are platform users hear about their own files
        by_email = _email_user_ids([c.get("email") for c in record["consumed"] if not c.get("user_id")]
                                   + [record["requested_by"].get("email")])
        for c in record["consumed"]:
            if not c.get("user_id") and c.get("email") and c["email"].lower() in by_email:
                c["user_id"] = by_email[c["email"].lower()]
        req = record["requested_by"]
        if not req.get("user_id") and req.get("email") and req["email"].lower() in by_email:
            req["user_id"] = by_email[req["email"].lower()]
        for c in record["consumed"]:
            _forget_upload(wid, c["name"])
        # The outcome is on disk before anything slow happens: The Agent and
        # the Files panel read it as soon as the run's status turns final.
        path = _run_path(wid, eid)
        _write_json(path, record)
        record["sharepoint"] = copy_to_sharepoint(record, settings)
        people = recipients(record, settings) if worth_telling(record) else {}
        record["notified"] = notify(record, settings, people) if people else []
        _write_json(path, record)
        if _claim(_flag_path(wid, eid)) and _deliver_late(record) == "sent":
            record["notified"].append(str(req.get("user_id")))   # The Agent stopped waiting
            _write_json(path, record)
        nxt = _claim_run_next(wid)
        if nxt is not None and os.listdir(folder_path(wid, "inbox")):
            record["started_next"] = _start_next_run(wid, nxt.get("requested_by") or {})
            _write_json(path, record)
        return record
    except Exception as e:
        logger.warning(f"file drops: run end bookkeeping failed for workflow {wid} run {eid}: {e}")
        return None


def run_finished_async(snap: Optional[dict], status: str) -> None:
    if not snap:
        return
    threading.Thread(target=run_finished, args=(snap, status), daemon=True,
                     name=f"file-drops-{snap.get('execution_id')}").start()


# ------------------------------------------------------------------- email in
def save_email_attachments(workflow_id, email: dict) -> list:
    """Before an email-triggered run of a set-up workflow: save the email's
    attachments into its inbox as real files. Returns the stored names.
    Never raises."""
    try:
        settings = load_settings(workflow_id)
        if not (enabled() and settings and settings.get("enabled") and settings.get("email_to_inbox", True)):
            return []
        if not (email.get("attachment_count") or email.get("has_attachments")):
            return []
        import agent_email_attachments
        stored = []
        by = {"email": email.get("sender_email") or "", "name": email.get("sender_name") or "",
              "via": "email"}
        for att in agent_email_attachments.fetch_attachments_for_event(email.get("event_id")):
            if att.get("inline") or not att.get("content"):
                continue
            try:
                stored.append(store_bytes(workflow_id, att.get("filename") or "attachment",
                                          att["content"], by)["name"])
            except Exception as e:
                logger.warning(f"file drops: could not save attachment {att.get('filename')!r} "
                               f"for workflow {workflow_id}: {e}")
        if stored:
            logger.info(f"file drops: email {email.get('event_id')} put {len(stored)} file(s) "
                        f"into workflow {workflow_id}'s inbox")
        return stored
    except Exception as e:
        logger.warning(f"file drops: email attachments for workflow {workflow_id} not saved: {e}")
        return []
