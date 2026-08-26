"""AI Hub Scenario Console — day-in-the-life scenario operations (http://127.0.0.1:7742).

One screen to run, monitor and demo the business-role scenario packs: live status for
every source a scenario depends on (services, databases, SFTP, watched folders, email
drops), a one-button batch builder that mints a fresh set of invoices and fans them
across the intake channels, a data explorer over the seeded book, and every pack action
with its output streamed back.

Everything is driven by scenarios.json — add a scenario or a source there and the
console picks it up on restart.

Port 7742 is deliberately clear of the AI Hub range (5001–5111), the builder (8100),
the demo control panel (3100), the portal server (3000) and the SFTP server (2222).

Run:  Start_Scenario_Console.bat   (aihub2.1 python; Flask + requests + pyodbc)
"""
from __future__ import annotations

import datetime as _dt
import glob as _glob
import json
import os
import random
import socket
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Flask, jsonify, request, send_from_directory

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_DEFAULT = os.path.abspath(os.path.join(HERE, "..", ".."))

with open(os.path.join(HERE, "scenarios.json"), encoding="utf-8") as fh:
    REG = json.load(fh)

SET = REG.get("settings", {})
REPO = SET.get("repo") or REPO_DEFAULT
PORT = int(os.getenv("SCENARIO_CONSOLE_PORT", SET.get("port", 7742)))
PYTHONS = SET.get("pythons", {})
SQL = SET.get("sql", {})
SCENARIOS = {s["id"]: s for s in REG.get("scenarios", [])}

app = Flask(__name__, static_folder=None)

JOBS: dict[str, dict] = {}
ACTIVITY: list[dict] = []
_LOCK = threading.Lock()
_POOL = ThreadPoolExecutor(max_workers=8)
_SOURCE_CACHE: dict[str, dict] = {}


def now() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")


def log_activity(kind: str, text: str, scenario: str = ""):
    with _LOCK:
        ACTIVITY.insert(0, {"ts": now(), "kind": kind, "text": text, "scenario": scenario})
        del ACTIVITY[200:]


def abspath(rel: str) -> str:
    return rel if os.path.isabs(rel) else os.path.join(REPO, rel)


# ----------------------------------------------------------------- source checks
def _check_http(src) -> dict:
    try:
        r = requests.get(src["url"], timeout=4, allow_redirects=True)
        ok = r.status_code < 500
        return {"state": "up" if ok else "down", "note": f"HTTP {r.status_code}"}
    except Exception as e:                                            # noqa: BLE001
        return {"state": "down", "note": type(e).__name__}


def _check_tcp(src) -> dict:
    s = socket.socket()
    s.settimeout(3)
    try:
        s.connect((src.get("host", "127.0.0.1"), int(src["port"])))
        return {"state": "up", "note": "listening"}
    except Exception as e:                                            # noqa: BLE001
        return {"state": "down", "note": type(e).__name__}
    finally:
        s.close()


def _check_dir(src) -> dict:
    p = abspath(src["path"])
    if not os.path.isdir(p):
        return {"state": "empty", "note": "folder does not exist", "count": 0, "path": p}
    n = len(_glob.glob(os.path.join(p, src.get("glob", "*"))))
    extra = 0
    if src.get("extra_glob"):
        extra = len(_glob.glob(os.path.join(p, src["extra_glob"])))
    newest = None
    files = _glob.glob(os.path.join(p, src.get("glob", "*")))
    if files:
        newest = _dt.datetime.fromtimestamp(
            max(os.path.getmtime(f) for f in files)).replace(microsecond=0).isoformat(sep=" ")
    return {"state": "up" if n else "empty", "count": n, "extra": extra,
            "note": f"{n} file{'s' if n != 1 else ''}", "path": p, "newest": newest}


def _sql_conn(database: str):
    import pyodbc                                                     # noqa: PLC0415
    return pyodbc.connect(
        "DRIVER={ODBC Driver 17 for SQL Server};"
        f"SERVER={SQL.get('server')};DATABASE={database};"
        f"UID={SQL.get('user')};PWD={SQL.get('password')};TrustServerCertificate=yes",
        timeout=8)


def _check_sql(src) -> dict:
    try:
        cn = _sql_conn(src["database"])
        cur = cn.cursor()
        metrics = []
        for m in src.get("metrics", []):
            try:
                cur.execute(m["sql"])
                metrics.append({"label": m["label"], "value": cur.fetchone()[0]})
            except Exception as e:                                    # noqa: BLE001
                metrics.append({"label": m["label"], "value": None,
                                "error": type(e).__name__})
        cn.close()
        return {"state": "up", "note": "connected", "metrics": metrics}
    except Exception as e:                                            # noqa: BLE001
        return {"state": "down", "note": f"{type(e).__name__}"}


def _check_agent_email(src) -> dict:
    """Read The Agent's own inbound-email ledger.

    This channel is not a folder of files — it is a real mailbox. What matters
    is how many of the batch's messages the agent has actually RECEIVED and
    processed, which only the ledger knows. Read-only, over a ro: URI.
    """
    db = abspath(src.get("db", r"data\agent\mywork.db"))
    if not os.path.exists(db):
        return {"state": "down", "note": "no agent ledger on this box", "count": 0}
    import sqlite3                                                    # noqa: PLC0415
    match = src.get("match", "CG-VINV")
    try:
        cn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=6)
        cn.row_factory = sqlite3.Row
        try:
            addrs = [dict(r) for r in cn.execute(
                "SELECT email_address, cooldown_minutes FROM user_email_addresses "
                "WHERE is_active = 1")]
            rows = [dict(r) for r in cn.execute(
                "SELECT outcome, processed_at FROM processed_emails "
                "WHERE subject LIKE ? OR subject LIKE ? "
                "ORDER BY processed_at DESC", (f"%{match}%", "%invoices attached%"))]
        finally:
            cn.close()
    except Exception as e:                                            # noqa: BLE001
        return {"state": "down", "note": f"{type(e).__name__}", "count": 0}

    handled = [r for r in rows if r["outcome"] in ("processed", "reply_drafted")]
    newest = rows[0]["processed_at"][:19].replace("T", " ") if rows else None
    if not addrs:
        return {"state": "empty", "count": 0, "note": "no agent mailbox set up",
                "mailbox": None, "newest": None}
    slow = [a for a in addrs if a["cooldown_minutes"] is None or int(a["cooldown_minutes"]) > 0]
    return {"state": "up" if handled else "empty",
            "count": len(handled),
            "note": f"{len(handled)} received" if handled else "nothing received yet",
            "mailbox": addrs[0]["email_address"],
            "cooldown_warning": bool(slow),
            "newest": newest}


CHECKERS = {"http": _check_http, "tcp": _check_tcp, "dir": _check_dir,
            "sql": _check_sql, "agent_email": _check_agent_email}


def check_source(src: dict) -> dict:
    fn = CHECKERS.get(src.get("kind", ""))
    started = time.time()
    res = fn(src) if fn else {"state": "unknown", "note": "no checker"}
    res.update({"id": src["id"], "name": src.get("name", src["id"]),
                "group": src.get("group", "Other"), "kind": src.get("kind"),
                "detail": src.get("detail", ""), "open": src.get("open"),
                "fix": src.get("fix"),
                # can_start drives the UI's Start button. It must reflect what the
                # backend can actually do -- a "fix" hint alone is not launchable.
                "can_start": bool(src.get("start")),
                "ms": int((time.time() - started) * 1000), "checked": now()})
    return res


@app.get("/api/sources")
def api_sources():
    srcs = REG.get("sources", [])
    out = list(_POOL.map(check_source, srcs))
    with _LOCK:
        for r in out:
            _SOURCE_CACHE[r["id"]] = r
    return jsonify({"sources": out, "checked": now()})


@app.get("/api/scenario/<sid>/channels")
def api_channels(sid):
    sc = SCENARIOS.get(sid)
    if not sc:
        return jsonify({"error": "unknown scenario"}), 404
    chans = [dict(check_source({**c, "kind": c.get("kind", "dir")}),
                  role="channel") for c in sc.get("channels", [])]
    arts = [dict(check_source({**a, "kind": a.get("kind", "dir")}),
                 role="artifact") for a in sc.get("artifacts", [])]
    manifest = _read_manifest(sc)
    return jsonify({"channels": chans, "artifacts": arts, "manifest": manifest})


def _read_manifest(sc) -> dict | None:
    """The scenario's own SEED_MANIFEST.json, if it has one."""
    pack = sc.get("pack")
    if not pack:
        return None
    p = os.path.join(abspath(pack), "SEED_MANIFEST.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            m = json.load(fh)
        anchor = m.get("anchor")
        if anchor:
            age = (_dt.date.today() - _dt.date.fromisoformat(anchor)).days
            m["age_days"] = age
        return m
    except Exception:                                                 # noqa: BLE001
        return None


# ----------------------------------------------------------------- jobs
def _run_job(job_id: str, cmd: list[str], cwd: str, label: str, scenario: str):
    with _LOCK:
        JOBS[job_id].update(status="running", started=now())
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              timeout=1800, encoding="utf-8", errors="replace")
        out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        ok = proc.returncode == 0
        with _LOCK:
            JOBS[job_id].update(status="done" if ok else "failed",
                                returncode=proc.returncode,
                                output=out.strip() or "(no output)", ended=now())
        log_activity("ok" if ok else "fail",
                     f"{label} — {'completed' if ok else f'exit {proc.returncode}'}", scenario)
    except subprocess.TimeoutExpired:
        with _LOCK:
            JOBS[job_id].update(status="failed", output="Timed out after 30 minutes.",
                                ended=now())
        log_activity("fail", f"{label} — timed out", scenario)
    except Exception as e:                                            # noqa: BLE001
        with _LOCK:
            JOBS[job_id].update(status="failed", output=f"{type(e).__name__}: {e}",
                                ended=now())
        log_activity("fail", f"{label} — {type(e).__name__}", scenario)


def start_job(cmd: list[str], cwd: str, label: str, scenario: str = "") -> str:
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        JOBS[job_id] = {"id": job_id, "label": label, "scenario": scenario,
                        "cmd": " ".join(cmd), "status": "queued", "output": "",
                        "created": now()}
    log_activity("run", f"{label} — started", scenario)
    _POOL.submit(_run_job, job_id, cmd, cwd, label, scenario)
    return job_id


@app.post("/api/scenario/<sid>/action/<aid>")
def api_action(sid, aid):
    sc = SCENARIOS.get(sid)
    if not sc:
        return jsonify({"error": "unknown scenario"}), 404
    action = next((a for a in sc.get("actions", []) if a["id"] == aid), None)
    if not action:
        return jsonify({"error": "unknown action"}), 404
    py = PYTHONS.get("aihub21", sys.executable)
    scripts = abspath(sc["scripts"])
    cmd = [py, os.path.join(scripts, action["script"]), *action.get("args", [])]
    job_id = start_job(cmd, scripts, f"{sc['name']}: {action['label']}", sid)
    return jsonify({"job": job_id})


@app.post("/api/scenario/<sid>/batch")
def api_batch(sid):
    """Mint a fresh batch: re-seed the book, re-render every document, fan it out."""
    sc = SCENARIOS.get(sid)
    if not sc or not sc.get("batch_builder", {}).get("enabled"):
        return jsonify({"error": "no batch builder for this scenario"}), 400
    body = request.get_json(silent=True) or {}
    anchor = body.get("anchor") or _dt.date.today().isoformat()
    scale = str(int(body.get("scale") or 1))
    seed = body.get("seed")
    if seed in (None, "", "random"):
        seed = random.randint(1000, 999999)
    seed = str(int(seed))

    py = PYTHONS.get("aihub21", sys.executable)
    scripts = abspath(sc["scripts"])
    steps = sc["batch_builder"]["steps"]
    cmds = []
    for st in steps:
        args = [a.format(anchor=anchor, seed=seed, scale=scale) for a in st["args"]]
        cmds.append([py, os.path.join(scripts, st["script"]), *args])

    job_id = uuid.uuid4().hex[:12]
    label = f"{sc['name']}: new batch (seed {seed}, scale {scale}, anchor {anchor})"
    with _LOCK:
        JOBS[job_id] = {"id": job_id, "label": label, "scenario": sid,
                        "cmd": " && ".join(" ".join(c) for c in cmds),
                        "status": "queued", "output": "", "created": now(),
                        "seed": seed, "scale": scale, "anchor": anchor}
    log_activity("run", label, sid)

    def _chain():
        buf = []
        for i, cmd in enumerate(cmds, start=1):
            with _LOCK:
                JOBS[job_id].update(status="running",
                                    output="\n".join(buf) + f"\n\n[{i}/{len(cmds)}] "
                                           f"{os.path.basename(cmd[1])} ...")
            try:
                p = subprocess.run(cmd, cwd=scripts, capture_output=True, text=True,
                                   timeout=1800, encoding="utf-8", errors="replace")
            except Exception as e:                                    # noqa: BLE001
                buf.append(f"[{i}/{len(cmds)}] {type(e).__name__}: {e}")
                with _LOCK:
                    JOBS[job_id].update(status="failed", output="\n".join(buf), ended=now())
                log_activity("fail", f"{label} — step {i} {type(e).__name__}", sid)
                return
            buf.append(f"$ {os.path.basename(cmd[1])} {' '.join(cmd[2:])}\n"
                       f"{(p.stdout or '').strip()}")
            if p.stderr:
                buf.append(p.stderr.strip())
            if p.returncode != 0:
                with _LOCK:
                    JOBS[job_id].update(status="failed", returncode=p.returncode,
                                        output="\n\n".join(buf), ended=now())
                log_activity("fail", f"{label} — step {i} exit {p.returncode}", sid)
                return
        with _LOCK:
            JOBS[job_id].update(status="done", returncode=0,
                                output="\n\n".join(buf), ended=now())
        log_activity("ok", f"{label} — batch ready", sid)

    _POOL.submit(_chain)
    return jsonify({"job": job_id, "seed": seed, "scale": scale, "anchor": anchor})


def _inject_cli(sc, args: list[str]) -> dict:
    """Run inject.py --json and hand back its parsed result."""
    py = PYTHONS.get("aihub21", sys.executable)
    scripts = abspath(sc["scripts"])
    script = os.path.join(scripts, "inject.py")
    if not os.path.exists(script):
        return {"error": "this scenario has no injector"}
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        p = subprocess.run([py, script, *args, "--json"], cwd=scripts, env=env,
                           capture_output=True, text=True, timeout=180,
                           encoding="utf-8", errors="replace")
    except Exception as e:                                            # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    if p.returncode != 0:
        return {"error": (p.stderr or p.stdout or "").strip()[:600]}
    try:
        return json.loads(p.stdout)
    except Exception:                                                 # noqa: BLE001
        return {"error": f"unparseable output: {(p.stdout or '')[:300]}"}


@app.post("/api/scenario/<sid>/advance")
def api_advance(sid):
    """Move the scenario's clock forward a day.

    Same book, same seed — but later goods receipts post and the next slice of
    documents lands on the channels. Parked work should clear itself on the next
    run without anyone touching it.
    """
    sc = SCENARIOS.get(sid)
    clock = (sc or {}).get("clock") or {}
    if not sc or not clock.get("enabled"):
        return jsonify({"error": "no clock for this scenario"}), 400
    m = _read_manifest(sc) or {}
    if not m:
        return jsonify({"error": "nothing seeded yet — build a batch first"}), 400
    body = request.get_json(silent=True) or {}
    day = int(body.get("day", int(m.get("day", 0)) + 1))
    anchor = m.get("anchor")
    seed = str(m.get("seed") or "")
    scale = str(m.get("scale") or 1)

    py = PYTHONS.get("aihub21", sys.executable)
    scripts = abspath(sc["scripts"])
    cmds = []
    for st in clock["steps"]:
        args = [a.format(anchor=anchor, seed=seed, scale=scale, day=str(day))
                for a in st["args"]]
        cmds.append([py, os.path.join(scripts, st["script"]), *args])

    job_id = uuid.uuid4().hex[:12]
    label = f"{sc['name']}: advance to day {day}"
    with _LOCK:
        JOBS[job_id] = {"id": job_id, "label": label, "scenario": sid, "day": day,
                        "cmd": " && ".join(" ".join(c) for c in cmds),
                        "status": "queued", "output": "", "created": now()}
    log_activity("run", label, sid)

    def _chain():
        buf = []
        for i, cmd in enumerate(cmds, start=1):
            with _LOCK:
                JOBS[job_id].update(status="running",
                                    output="\n\n".join(buf) +
                                           f"\n\n[{i}/{len(cmds)}] {os.path.basename(cmd[1])} ...")
            try:
                p = subprocess.run(cmd, cwd=scripts, capture_output=True, text=True,
                                   timeout=1800, encoding="utf-8", errors="replace")
            except Exception as e:                                    # noqa: BLE001
                buf.append(f"[{i}] {type(e).__name__}: {e}")
                with _LOCK:
                    JOBS[job_id].update(status="failed", output="\n\n".join(buf), ended=now())
                log_activity("fail", f"{label} — {type(e).__name__}", sid)
                return
            buf.append(f"$ {os.path.basename(cmd[1])} {' '.join(cmd[2:])}\n"
                       f"{(p.stdout or '').strip()}")
            if p.stderr:
                buf.append(p.stderr.strip())
            if p.returncode != 0:
                with _LOCK:
                    JOBS[job_id].update(status="failed", returncode=p.returncode,
                                        output="\n\n".join(buf), ended=now())
                log_activity("fail", f"{label} — step {i} exit {p.returncode}", sid)
                return
        with _LOCK:
            JOBS[job_id].update(status="done", returncode=0,
                                output="\n\n".join(buf), ended=now())
        log_activity("ok", f"{label} — later receipts posted, new documents dropped", sid)

    _POOL.submit(_chain)
    return jsonify({"job": job_id, "day": day})


@app.post("/api/scenario/<sid>/inject")
def api_inject(sid):
    """Drop ONE document onto a channel — the poke-it-and-watch path."""
    sc = SCENARIOS.get(sid)
    if not sc or not sc.get("injector", {}).get("enabled"):
        return jsonify({"error": "no injector for this scenario"}), 400
    b = request.get_json(silent=True) or {}
    args = ["invoice", "--kind", str(b.get("kind") or "clean"),
            "--channel", str(b.get("channel") or "sftp")]
    if b.get("vendor"):
        args += ["--vendor", str(b["vendor"])]
    if b.get("po"):
        args += ["--po", str(b["po"])]
    if b.get("note"):
        args += ["--note", str(b["note"])]
    res = _inject_cli(sc, args)
    it = res.get("item") or {}
    log_activity("fail" if res.get("error") else "ok",
                 res.get("error") or
                 f"Injected {it.get('kind')} invoice {it.get('invoice')} -> {it.get('channel')}"
                 + ("" if it.get("has_receipt") else " (no receipt — should park)"), sid)
    return jsonify(res)


@app.post("/api/scenario/<sid>/receipt")
def api_receipt(sid):
    """Post the goods receipt that should let a parked invoice clear itself."""
    sc = SCENARIOS.get(sid)
    po = (request.get_json(silent=True) or {}).get("po")
    if not sc or not po:
        return jsonify({"error": "po is required"}), 400
    res = _inject_cli(sc, ["receipt", "--po", str(po)])
    log_activity("fail" if res.get("error") else "ok",
                 res.get("error") or f"Goods receipt posted for {po} — parked work should clear",
                 sid)
    return jsonify(res)


@app.get("/api/scenario/<sid>/awaiting")
def api_awaiting(sid):
    sc = SCENARIOS.get(sid)
    if not sc or not sc.get("injector", {}).get("enabled"):
        return jsonify({"open_pos": []})
    return jsonify(_inject_cli(sc, ["open-pos"]))


@app.post("/api/scenario/<sid>/reset-injected")
def api_reset_injected(sid):
    sc = SCENARIOS.get(sid)
    if not sc:
        return jsonify({"error": "unknown scenario"}), 404
    res = _inject_cli(sc, ["reset"])
    log_activity("ok", f"Injected documents cleared: {res.get('removed')}", sid)
    return jsonify(res)


@app.post("/api/scenario/<sid>/run-now")
def api_run_now(sid):
    """Trigger the process by hand: one headless turn on The Agent."""
    sc = SCENARIOS.get(sid)
    runner = (sc or {}).get("run_now") or {}
    if not sc or not runner.get("enabled"):
        return jsonify({"error": "no manual run configured for this scenario"}), 400
    prompt = (request.get_json(silent=True) or {}).get("prompt") or runner.get("prompt")
    key = os.getenv("API_KEY") or _api_key_from_env_file()
    if not key:
        return jsonify({"error": "API_KEY not found — cannot authenticate to The Agent"}), 400
    url = runner.get("url", "http://127.0.0.1:5111/api/run")
    body = {"prompt": prompt}
    if runner.get("user_id"):
        body["user_id"] = runner["user_id"]
    log_activity("run", "Manual run triggered on The Agent", sid)
    try:
        r = requests.post(url, json=body, headers={"X-API-Key": key}, timeout=900)
    except Exception as e:                                            # noqa: BLE001
        log_activity("fail", f"Manual run failed: {type(e).__name__}", sid)
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 502
    ok = r.status_code < 400
    log_activity("ok" if ok else "fail",
                 f"Manual run finished — HTTP {r.status_code}", sid)
    try:
        payload = r.json()
    except Exception:                                                 # noqa: BLE001
        payload = {"text": r.text[:4000]}
    return jsonify({"status": r.status_code, "ok": ok, "result": payload})


def _api_key_from_env_file() -> str:
    """The repo .env is the same source the services read."""
    p = os.path.join(REPO, ".env")
    if not os.path.exists(p):
        return ""
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("API_KEY=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:                                                 # noqa: BLE001
        pass
    return ""


@app.post("/api/source/<src_id>/start")
def api_start_source(src_id):
    """Launch a long-running server in its own console window.

    Two forms are supported, because not every test server is a Python script:
        {"command": "C:\\...\\start.cmd"}   a .cmd/.bat/.exe launcher
        {"python": "testftp", "script": "..."}   a Python entry point
    """
    src = next((s for s in REG.get("sources", []) if s["id"] == src_id), None)
    if not src or "start" not in src:
        return jsonify({"error": "no start command for this source"}), 400
    st = src["start"]
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)

    if st.get("command"):
        target = abspath(st["command"])
        if not os.path.exists(target):
            return jsonify({"error": f"launcher not found: {target}"}), 400
        cwd = st.get("cwd") and abspath(st["cwd"]) or os.path.dirname(target)
        # .cmd/.bat need a shell; cmd.exe /c keeps the window owned by the child.
        args = ([os.environ.get("COMSPEC", "cmd.exe"), "/c", target]
                if target.lower().endswith((".cmd", ".bat")) else [target])
    else:
        py = PYTHONS.get(st.get("python", "aihub21"), sys.executable)
        target = abspath(st["script"])
        if not os.path.exists(target):
            return jsonify({"error": f"script not found: {target}"}), 400
        cwd = st.get("cwd") and abspath(st["cwd"]) or os.path.dirname(target)
        args = [py, target]

    try:
        subprocess.Popen(args, cwd=cwd, creationflags=flags, close_fds=True)
    except Exception as e:                                            # noqa: BLE001
        log_activity("fail", f"Start {src.get('name', src_id)} — {type(e).__name__}: {e}")
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

    log_activity("run", f"Started {src.get('name', src_id)} — {os.path.basename(target)}")
    return jsonify({"started": src_id, "launched": target})


@app.get("/api/job/<job_id>")
def api_job(job_id):
    with _LOCK:
        j = JOBS.get(job_id)
    return jsonify(j) if j else (jsonify({"error": "unknown job"}), 404)


@app.get("/api/jobs")
def api_jobs():
    with _LOCK:
        js = sorted(JOBS.values(), key=lambda j: j["created"], reverse=True)[:30]
    return jsonify({"jobs": js})


@app.get("/api/activity")
def api_activity():
    with _LOCK:
        return jsonify({"activity": ACTIVITY[:60]})


# ----------------------------------------------------------------- explorer
@app.get("/api/scenario/<sid>/explore/<eid>")
def api_explore(sid, eid):
    sc = SCENARIOS.get(sid)
    if not sc:
        return jsonify({"error": "unknown scenario"}), 404
    view = next((e for e in sc.get("explorer", []) if e["id"] == eid), None)
    if not view:
        return jsonify({"error": "unknown view"}), 404

    if view.get("source") == "book":
        return jsonify(_explore_book(sc))

    try:
        cn = _sql_conn(view["database"])
        cur = cn.cursor()
        cur.execute(view["sql"])
        cols = [d[0] for d in cur.description]
        rows = [[_jsonable(v) for v in r] for r in cur.fetchall()]
        cn.close()
        return jsonify({"columns": cols, "rows": rows, "count": len(rows)})
    except Exception as e:                                            # noqa: BLE001
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


def _jsonable(v):
    from decimal import Decimal                                       # noqa: PLC0415
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat(sep=" ") if isinstance(v, _dt.datetime) else v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return v.hex()[:32]
    return v


def _explore_book(sc) -> dict:
    """The planted exception set, straight out of the scenario's own book module."""
    scripts = abspath(sc["scripts"])
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        import importlib                                              # noqa: PLC0415
        mod = importlib.import_module("ap_book")
        importlib.reload(mod)
        m = _read_manifest(sc) or {}
        book = mod.build(_dt.date.fromisoformat(m["anchor"]) if m.get("anchor") else None,
                         m.get("scale", 1), m.get("seed"))
        cols = ["Invoice", "Kind", "Class", "Vendor", "Channel", "Render",
                "Total", "PO", "Variance", "Expected cause"]
        rows = []
        for i in book.batch:
            if i.kind == "clean":
                continue
            rows.append([i.inv_no, i.kind, i.klass, i.lifnr, i.channel, i.render,
                         float(i.total), i.po_ref or "",
                         float(i.expected_variance) if i.expected_variance is not None else None,
                         i.expected_cause or ""])
        rows.sort(key=lambda r: (r[1] != "exception", r[2] or "", r[0]))
        return {"columns": cols, "rows": rows, "count": len(rows),
                "summary": book.summary()}
    except Exception as e:                                            # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}", "columns": [], "rows": []}


@app.get("/api/scenario/<sid>/doc")
def api_doc(sid):
    sc = SCENARIOS.get(sid)
    rel = request.args.get("path", "")
    if not sc or not rel:
        return jsonify({"error": "bad request"}), 400
    full = os.path.join(abspath(sc["pack"]), rel)
    if not os.path.commonpath([os.path.abspath(full), abspath(sc["pack"])]) == abspath(sc["pack"]):
        return jsonify({"error": "outside the pack"}), 400
    if not os.path.exists(full):
        return jsonify({"error": "not found", "path": full}), 404
    with open(full, encoding="utf-8", errors="replace") as fh:
        return jsonify({"path": rel, "text": fh.read()})


@app.get("/api/config")
def api_config():
    return jsonify({
        "scenarios": REG.get("scenarios", []),
        "settings": {"port": PORT, "repo": REPO,
                     "sql_server": SQL.get("server")},
        "generated": now(),
    })


@app.get("/")
def index():
    return send_from_directory(os.path.join(HERE, "ui"), "index.html")


@app.get("/ui/<path:fn>")
def ui_files(fn):
    return send_from_directory(os.path.join(HERE, "ui"), fn)


if __name__ == "__main__":
    print(f"AI Hub Scenario Console  ->  http://127.0.0.1:{PORT}")
    print(f"  repo      {REPO}")
    print(f"  scenarios {', '.join(SCENARIOS)}")
    app.run(host="127.0.0.1", port=PORT, threaded=True, debug=False)
