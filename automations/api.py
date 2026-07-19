"""
Automations REST API — Developer+ gated, mirrors agent_environments gating.

    GET    /automations/api/list
    POST   /automations/api/create            {name, description, environment_id? | provision_environment?}
    GET    /automations/api/<id>
    DELETE /automations/api/<id>
    GET    /automations/api/<id>/code         ?version=N
    PUT    /automations/api/<id>/code         {code, manifest?}   -> saves new immutable version
    GET    /automations/api/<id>/manifest     ?version=N
    POST   /automations/api/<id>/promote      {version?}          -> default: promote latest
    POST   /automations/api/<id>/run          {inputs?, version?, dry_run?, wait?}
    GET    /automations/api/<id>/runs
    GET    /automations/api/runs/<run_id>
    GET    /automations/api/runs/<run_id>/log
    POST   /automations/api/internal/run      X-API-Key           -> scheduler seam (P1 job type)

Gating: login + role in {2,3} (Developer/Admin) + cfg.AUTOMATIONS_ENABLED.
Runs execute the PINNED version; dry-runs the latest edit. Concurrent runs
are skipped (recorded as status='skipped').
"""

import json
import logging
import os
import threading
import time
import uuid
from functools import wraps
from typing import Dict, Optional

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

import config as cfg
from .manager import AutomationManager, validate_manifest
from .runner import AutomationRunner

logger = logging.getLogger(__name__)

automations_bp = Blueprint("automations", __name__, url_prefix="/automations")

_manager = None
_runner = None
_tables_ensured = False


def _get_manager() -> AutomationManager:
    global _manager, _tables_ensured
    if _manager is None:
        _manager = AutomationManager()
    if not _tables_ensured:
        _manager.ensure_tables()
        _tables_ensured = True
    return _manager


def _get_runner() -> AutomationRunner:
    global _runner
    if _runner is None:
        _runner = AutomationRunner(manager=_get_manager())
    return _runner


def _service_key_ok(provided: Optional[str]) -> bool:
    """Service-to-service auth for the internal endpoints. Accepts EITHER the
    raw tenant API_KEY (the scheduler's convention) OR the machine-derived
    internal service key (what the CC service sends when AI_HUB_API_KEY isn't
    pinned in its env — AIHUB-0031 F1: all Studio proxy calls 502'd because
    only the raw key was accepted). Constant-time compares."""
    import hmac as _hmac
    if not provided:
        return False
    raw = os.getenv("API_KEY", "")
    if raw and _hmac.compare_digest(provided, raw):
        return True
    try:
        from role_decorators import get_internal_api_key
        derived = get_internal_api_key()
        if derived and _hmac.compare_digest(provided, derived):
            return True
    except Exception as e:
        logger.debug(f"internal-key derivation unavailable: {e}")
    return False


def automations_gate(f):
    """Feature flag + Developer/Admin role, same shape as agent_environments."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not getattr(cfg, "AUTOMATIONS_ENABLED", False):
            return jsonify({"error": "Automations feature is disabled"}), 403
        if not hasattr(current_user, "role") or current_user.role not in [2, 3]:
            return jsonify({"error": "Access denied — Developer role required"}), 403
        return f(*args, **kwargs)
    return decorated


# ----------------------------------------------------------------- runs UI

_RUNS_PAGE = """<!DOCTYPE html>
<html><head><title>Automations — Mission Control</title>
<style>
 :root{--bg:#0b1114;--sf:#111a1f;--sf2:#16232a;--ln:#20323b;--tx:#dbe7ed;--mut:#7d95a1;--dim:#597079;
       --cy:#22d3ee;--ok:#34d399;--bad:#f87171;--wn:#fbbf24}
 *{box-sizing:border-box}
 body{font-family:Segoe UI,Arial,sans-serif;margin:0;background:var(--bg);color:var(--tx)}
 .wrap{max-width:1100px;margin:0 auto;padding:26px 22px 60px}
 h1{font-size:19px;margin:0 0 2px;display:flex;align-items:center;gap:10px}
 h2{font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:var(--mut);margin:28px 0 10px}
 .muted{color:var(--dim);font-size:12px}
 table{border-collapse:collapse;width:100%;background:var(--sf);border:1px solid var(--ln);border-radius:10px;overflow:hidden}
 th,td{padding:8px 12px;border-bottom:1px solid var(--ln);text-align:left;font-size:13px}
 th{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mut);background:var(--sf2)}
 tr:last-child td{border-bottom:none} tbody tr:hover td{background:var(--sf2);cursor:pointer}
 .pill{display:inline-block;font-size:11px;font-weight:600;border-radius:99px;padding:1px 9px}
 .success{background:rgba(52,211,153,.13);color:var(--ok)} .failed{background:rgba(248,113,113,.13);color:var(--bad)}
 .unverified{background:rgba(251,191,36,.13);color:var(--wn)} .aborted{background:rgba(248,113,113,.10);color:#e8a0a0}
 .skipped{background:rgba(125,149,161,.13);color:var(--mut)}
 .running,.waiting,.aborting{background:rgba(34,211,238,.13);color:var(--cy)}
 pre{background:#05080a;color:#c9d8e0;border:1px solid var(--ln);border-radius:8px;padding:10px 12px;
     overflow:auto;max-height:380px;font-size:12px;font-family:Cascadia Code,Consolas,monospace;white-space:pre-wrap}
 .card{background:var(--sf);border:1px solid rgba(34,211,238,.35);border-radius:10px;padding:14px 16px;margin:10px 0}
 .card .head{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;font-weight:600}
 .bar{height:6px;border-radius:99px;background:#14212a;overflow:hidden;margin:10px 0 4px}
 .fill{height:100%;width:0%;background:linear-gradient(90deg,#0e7490,var(--cy));transition:width 1s linear}
 .cap{display:flex;justify-content:space-between;font-size:10.5px;color:var(--dim);font-family:Consolas,monospace}
 .chips{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}
 .chip{font-family:Consolas,monospace;font-size:10.5px;padding:2px 8px;border-radius:6px;background:var(--sf2);
       border:1px solid var(--ln);color:#9cc3d2}
 .gate{margin-top:10px;border:1px solid rgba(251,191,36,.45);background:rgba(251,191,36,.06);
       border-radius:8px;padding:10px 12px}
 .gate .msg{font-size:13px;color:#f2dcae;margin-bottom:8px}
 button{font-size:12px;font-weight:600;padding:6px 14px;border-radius:6px;border:1px solid transparent;
        cursor:pointer;font-family:inherit}
 .go{background:#0e7490;color:#eafcff} .go:hover{background:var(--cy);color:#06282e}
 .stop{background:transparent;border-color:rgba(248,113,113,.5);color:#f0a8a8} .stop:hover{background:rgba(248,113,113,.12)}
 .dot{width:8px;height:8px;border-radius:50%;background:var(--cy);display:inline-block;
      animation:pulse 1.7s ease-in-out infinite}
 @keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}
 @media (prefers-reduced-motion: reduce){.dot{animation:none}.fill{transition:none}}
 .livelog{max-height:200px}
</style></head><body><div class="wrap">
<h1><span class="dot"></span>Automations — Mission Control</h1>
<div class="muted">Live runs update automatically. Build and manage automations in Command Center.</div>

<h2>Live now</h2>
<div id="live"><p class="muted">No runs in flight.</p></div>

<h2>Automations</h2>
<div id="autos"></div>
<h2 id="runsTitle" style="display:none">Run history</h2><div id="runs"></div>
<h2 id="logTitle" style="display:none">Run log</h2><pre id="log" style="display:none"></pre>

<script>
const cursors={}, meta={}, egress={};
async function j(u,opts){const r=await fetch(u,opts);return r.json();}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function pill(s){return `<span class="pill ${esc(s)}">${esc(s)}</span>`;}

async function loadAutos(){
 const d=await j('/automations/api/list');const a=d.automations||[];
 let h='<table><thead><tr><th>Name</th><th>Description</th><th>Latest</th><th>Live version</th></tr></thead><tbody>';
 for(const x of a){h+=`<tr onclick="loadRuns('${esc(x.automation_id)}','${esc(x.name)}')"><td>${esc(x.name)}</td><td>${esc(x.description||'')}</td><td>v${x.current_version}</td><td>${x.pinned_version?('v'+x.pinned_version):'—'}</td></tr>`;}
 document.getElementById('autos').innerHTML=h+'</tbody></table>'+(a.length?'':'<p class="muted">No automations yet.</p>');}

async function loadRuns(id,name){
 const d=await j('/automations/api/'+id+'/runs');const rs=d.runs||[];
 const t=document.getElementById('runsTitle');t.style.display='block';t.textContent='Run history — '+name;
 let h='<table><thead><tr><th>Started</th><th>Trigger</th><th>Version</th><th>Outcome</th><th>Exit</th></tr></thead><tbody>';
 for(const r of rs){h+=`<tr onclick="loadLog('${esc(r.run_id)}')"><td>${esc(r.started_at||'')}</td><td>${esc(r.trigger_source)}</td><td>v${r.version}</td><td>${pill(r.status)}</td><td>${r.exit_code??''}</td></tr>`;}
 document.getElementById('runs').innerHTML=h+'</tbody></table>'+(rs.length?'':'<p class="muted">No runs yet.</p>');
 document.getElementById('log').style.display='none';document.getElementById('logTitle').style.display='none';}

async function loadLog(runId){
 const d=await j('/automations/api/runs/'+runId+'/log');
 document.getElementById('logTitle').style.display='block';
 const el=document.getElementById('log');el.style.display='block';
 el.textContent=d.log||d.error||'(no log)';}

// ── live board ─────────────────────────────────────────────────────────
async function refreshLive(){
 let d;try{d=await j('/automations/api/active');}catch(e){return;}
 const live=document.getElementById('live');const runs=d.active||[];
 for(const id of Object.keys(cursors)) if(!runs.find(r=>r.run_id===id)){delete cursors[id];delete meta[id];delete egress[id];}
 if(!runs.length){live.innerHTML='<p class="muted">No runs in flight.</p>';return;}
 for(const r of runs){
   let card=document.getElementById('run-'+r.run_id);
   if(!card){
     card=document.createElement('div');card.className='card';card.id='run-'+r.run_id;
     card.innerHTML=`<div class="head"><span>${esc(r.name)} <span class="muted">v${r.version} · ${esc(r.trigger_source)}</span></span>
       <span>${pill(r.status)} <button class="stop" onclick="abortRun('${esc(r.run_id)}')">Abort</button></span></div>
       <div class="bar"><div class="fill" id="fill-${esc(r.run_id)}"></div></div>
       <div class="cap"><span id="el-${esc(r.run_id)}"></span><span id="to-${esc(r.run_id)}"></span></div>
       <pre class="livelog" id="log-${esc(r.run_id)}"></pre><div class="chips" id="eg-${esc(r.run_id)}"></div>
       <div class="gate" id="gate-${esc(r.run_id)}" style="display:none"></div>`;
     if(live.querySelector('.muted'))live.innerHTML='';
     live.appendChild(card);cursors[r.run_id]=0;meta[r.run_id]={};egress[r.run_id]=new Set();
   }
   card.querySelector('.pill').outerHTML=pill(r.status);
   pollEvents(r.run_id);
 }
 for(const el of live.querySelectorAll('.card')) if(!runs.find(r=>'run-'+r.run_id===el.id)) el.remove();
}

async function pollEvents(runId){
 let d;try{d=await j(`/automations/api/runs/${runId}/events?after=${cursors[runId]||0}`);}catch(e){return;}
 cursors[runId]=d.next||cursors[runId];
 const logEl=document.getElementById('log-'+runId), egEl=document.getElementById('eg-'+runId);
 for(const ev of (d.events||[])){
   if(ev.type==='run_started'){meta[runId]={start:Date.parse(ev.ts)||Date.now(),timeout:ev.timeout||600};}
   else if(ev.type==='log'&&logEl){logEl.textContent+=(ev.line+'\\n');
     const lines=logEl.textContent.split('\\n');if(lines.length>200)logEl.textContent=lines.slice(-200).join('\\n');
     logEl.scrollTop=logEl.scrollHeight;}
   else if(ev.type==='egress'&&egEl&&!egress[runId].has(ev.dest)){egress[runId].add(ev.dest);
     egEl.insertAdjacentHTML('beforeend',`<span class="chip">▸ ${esc(ev.dest)}</span>`);}
 }
 const m=meta[runId];
 if(m&&m.start){const s=Math.floor((Date.now()-m.start)/1000);
   const f=document.getElementById('fill-'+runId);if(f)f.style.width=Math.min(100,s/m.timeout*100)+'%';
   const e1=document.getElementById('el-'+runId);if(e1)e1.textContent=`elapsed ${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;
   const e2=document.getElementById('to-'+runId);if(e2)e2.textContent=`timeout ${Math.floor(m.timeout/60)}:${String(m.timeout%60).padStart(2,'0')}`;}
 const gate=document.getElementById('gate-'+runId);
 const p=d.pending_checkpoint;
 if(gate){ if(p&&!p.decision){gate.style.display='';
   gate.innerHTML=`<div class="msg">⏸ Waiting for you — ${esc(p.message)}</div>
     <button class="go" onclick="decide('${runId}','${esc(p.checkpoint_id)}','proceed')">Proceed</button>
     <button class="stop" onclick="decide('${runId}','${esc(p.checkpoint_id)}','abort')">Abort</button>`;}
  else gate.style.display='none';}
}

async function decide(runId,cid,decision){
 await j(`/automations/api/runs/${runId}/checkpoints/${cid}/decision`,
   {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision})});
 refreshLive();
}
async function abortRun(runId){
 if(!confirm('Abort this run? It stops within a few seconds and records the outcome "aborted".'))return;
 await j(`/automations/api/runs/${runId}/abort`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
 refreshLive();
}

loadAutos();refreshLive();
setInterval(refreshLive,3000);
</script></div></body></html>"""


@automations_bp.route("/", methods=["GET"])
@automations_gate
def runs_ui():
    """Mission Control: live runs (event feed, budget bar, egress, checkpoint
    gates, abort) + run history with honest outcomes. Building/managing
    happens in Command Center."""
    return _RUNS_PAGE


# ------------------------------------------------------------------- CRUD

@automations_bp.route("/api/list", methods=["GET"])
@automations_gate
def list_automations():
    return jsonify({"automations": _get_manager().list_automations()})


@automations_bp.route("/api/create", methods=["POST"])
@automations_gate
def create_automation():
    data = request.get_json(silent=True) or {}
    name = data.get("name", "")
    description = data.get("description", "")
    environment_id = data.get("environment_id")

    ok, auto, error = _get_manager().create_automation(
        name=name, description=description,
        owner_user_id=current_user.id, environment_id=environment_id,
    )
    if not ok:
        return jsonify({"error": error}), 400

    warning = None
    if not environment_id and data.get("provision_environment", True):
        # one dedicated agent environment per automation (design decision).
        # Provisioning builds a real venv (slow — tester finding AIHUB-0027 F3),
        # so it runs in the BACKGROUND: create returns immediately and the
        # environment_id is patched in when ready. Until then (or on failure)
        # the runner falls back to the bundled Python — honest and non-fatal.
        _provision_environment_async(auto)
        warning = ("dedicated environment is being provisioned in the background; "
                   "runs use the bundled Python until it is ready")

    resp = {"automation": auto}
    if warning:
        resp["warning"] = warning
    return jsonify(resp), 201


def _provision_environment_async(auto):
    def _work():
        env_id, warn = _provision_environment(auto)
        if env_id:
            try:
                _get_manager().set_environment(auto["automation_id"], env_id)
                logger.info(f"automation {auto['automation_id']}: dedicated environment {env_id} ready")
            except Exception as e:
                logger.warning(f"automation {auto['automation_id']}: could not record environment: {e}")
        elif warn:
            logger.warning(f"automation {auto['automation_id']}: {warn}")
    threading.Thread(target=_work, daemon=True).start()


def _provision_environment(auto):
    """Create the automation's dedicated agent environment. Returns
    (environment_id | None, warning | None)."""
    try:
        from agent_environments.environment_api import get_manager as get_env_manager
        env_manager = get_env_manager()
        env_name = f"automation-{auto['name']}"[:100]
        success, env_id, message = env_manager.create_environment(
            name=env_name,
            description=f"Dedicated environment for automation '{auto['name']}'",
            created_by=auto["owner_user_id"],
        )
        if success:
            return env_id, None
        return None, f"environment not provisioned ({message}); runs will use the bundled Python"
    except Exception as e:
        logger.warning(f"environment provisioning failed for automation {auto['automation_id']}: {e}")
        return None, f"environment not provisioned ({e}); runs will use the bundled Python"


@automations_bp.route("/api/<automation_id>", methods=["GET"])
@automations_gate
def get_automation(automation_id):
    auto = _get_manager().get_automation(automation_id)
    if not auto:
        return jsonify({"error": "not found"}), 404
    auto["versions"] = _get_manager().list_versions(automation_id)
    return jsonify({"automation": auto})


@automations_bp.route("/api/<automation_id>", methods=["DELETE"])
@automations_gate
def delete_automation(automation_id):
    ok, error = _get_manager().delete_automation(automation_id)
    if not ok:
        return jsonify({"error": error}), 404
    return jsonify({"deleted": automation_id})


# ---------------------------------------------------------- code / manifest

@automations_bp.route("/api/<automation_id>/code", methods=["GET"])
@automations_gate
def get_code(automation_id):
    version = request.args.get("version", type=int)
    code = _get_manager().get_code(automation_id, version)
    if code is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"code": code, "version": version})


@automations_bp.route("/api/<automation_id>/code", methods=["PUT"])
@automations_gate
def save_code(automation_id):
    data = request.get_json(silent=True) or {}
    code = data.get("code")
    if not isinstance(code, str) or not code.strip():
        return jsonify({"error": "'code' is required"}), 400
    manifest = data.get("manifest")  # optional: update manifest in the same version
    if manifest is not None:
        ok, errors = validate_manifest(manifest)
        if not ok:
            return jsonify({"error": "invalid manifest", "details": errors}), 400
    ok, new_version, errors = _get_manager().save_version(automation_id, code, manifest)
    if not ok:
        return jsonify({"error": errors[0] if errors else "save failed", "details": errors}), 400
    return jsonify({"version": new_version,
                    "note": "saved but not promoted — dry-run then promote to make it live"})


@automations_bp.route("/api/<automation_id>/manifest", methods=["GET"])
@automations_gate
def get_manifest(automation_id):
    version = request.args.get("version", type=int)
    manifest = _get_manager().get_manifest(automation_id, version)
    if manifest is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"manifest": manifest, "version": version})


@automations_bp.route("/api/<automation_id>/promote", methods=["POST"])
@automations_gate
def promote(automation_id):
    data = request.get_json(silent=True) or {}
    ok, version, error = _get_manager().promote(automation_id, data.get("version"))
    if not ok:
        return jsonify({"error": error}), 400
    return jsonify({"pinned_version": version})


# --------------------------------------------------------------------- runs

# AIHUB-0058: budget for the INLINE (wait/dry-run) path. The old code blocked
# in runner.run() until the run terminated — a script pausing at a human-
# approval checkpoint therefore blocked the HTTP request INDEFINITELY: the CC
# client read-timed out ("Read timed out"), the agent told the user the run
# "could not start" (it had — it was WAITING for their decision), and the
# runner eventually killed the run at the manifest timeout with the approval
# question never surfaced. Live: expense-audit v2-v5, "seen before" by james.
_INLINE_WAIT_CAP_S = int(os.getenv("AUTOMATIONS_INLINE_WAIT_CAP_S", "240"))
_INLINE_POLL_S = 0.5


def _await_inline_result(runner, run_id, holder, thread,
                         cap_s=None, poll_s=None, clock=time, live_payload=None):
    """Poll a threaded run until it finishes, pauses on a checkpoint, or the
    inline budget elapses. Returns (payload, http_code). Fast runs return the
    runner's EXACT result (byte-compatible with the old blocking path); a
    pending checkpoint returns immediately with the question so the caller can
    surface it to the human; the cap returns an honest still-running payload.
    clock/live_payload injectable for tests."""
    cap_s = _INLINE_WAIT_CAP_S if cap_s is None else cap_s
    poll_s = _INLINE_POLL_S if poll_s is None else poll_s
    live_payload = live_payload or _run_live_payload
    deadline = clock.time() + cap_s
    while True:
        if not thread.is_alive() and holder.get("result") is not None:
            result = holder["result"]
            return result, (200 if result.get("status") != "error" else 400)
        run = None
        try:
            run = runner.get_run(run_id)
        except Exception:
            pass
        if run and run.get("status") == "waiting":
            try:
                pc = (live_payload(run, 0) or {}).get("pending_checkpoint")
            except Exception:
                pc = None
            if pc:
                return {"status": "waiting", "run_id": run_id,
                        "waiting_on_checkpoint": True,
                        "pending_checkpoint": pc,
                        "note": ("run PAUSED at a human-approval checkpoint — "
                                 "surface the question and decide via "
                                 "checkpoint_decision (proceed|abort); the run "
                                 "is NOT failed and has NOT timed out")}, 200
        if clock.time() >= deadline:
            return {"status": "running", "run_id": run_id,
                    "inline_wait_elapsed": True,
                    "note": (f"still executing after {cap_s}s — NOT a failure; "
                             f"poll run_events/runs for the outcome")}, 200
        clock.sleep(poll_s)


def _run_inline(automation_id, inputs, trigger, version, requested_by, dry_run):
    """Start a run in a worker thread and wait inline per _await_inline_result.
    Shared by the user route and the internal manage endpoint."""
    runner = _get_runner()
    run_id = str(uuid.uuid4())
    holder = {}

    def _target():
        try:
            holder["result"] = runner.run(
                automation_id, inputs=inputs, trigger=trigger, version=version,
                requested_by=requested_by, dry_run=dry_run, run_id=run_id)
        except Exception as e:  # never leave the waiter spinning on a crash
            holder["result"] = {"status": "error", "error": str(e)}

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    return _await_inline_result(runner, run_id, holder, thread)


def _start_run(automation_id, inputs, trigger, version, requested_by, dry_run, wait):
    """Shared by the user route and the internal (scheduler) route."""
    runner = _get_runner()
    if wait or dry_run:  # dry-run UX needs the result inline
        payload, code = _run_inline(automation_id, inputs, trigger, version,
                                    requested_by, dry_run)
        return jsonify(payload), code

    run_id = str(uuid.uuid4())
    thread = threading.Thread(
        target=runner.run, daemon=True,
        kwargs=dict(automation_id=automation_id, inputs=inputs, trigger=trigger,
                    version=version, requested_by=requested_by,
                    dry_run=False, run_id=run_id),
    )
    thread.start()
    return jsonify({"run_id": run_id, "status": "started",
                    "note": f"poll /automations/api/runs/{run_id}"}), 202


@automations_bp.route("/api/<automation_id>/run", methods=["POST"])
@automations_gate
def run_automation(automation_id):
    data = request.get_json(silent=True) or {}
    return _start_run(
        automation_id,
        inputs=data.get("inputs") or {},
        trigger="manual",
        version=data.get("version"),
        requested_by=current_user.id,
        dry_run=bool(data.get("dry_run")),
        wait=bool(data.get("wait")),
    )


@automations_bp.route("/api/<automation_id>/runs", methods=["GET"])
@automations_gate
def list_runs(automation_id):
    limit = min(request.args.get("limit", default=50, type=int), 500)
    return jsonify({"runs": _get_runner().list_runs(automation_id, limit)})


@automations_bp.route("/api/runs/<run_id>", methods=["GET"])
@automations_gate
def get_run(run_id):
    run = _get_runner().get_run(run_id)
    if not run:
        return jsonify({"error": "not found"}), 404
    return jsonify({"run": run})


@automations_bp.route("/api/runs/<run_id>/log", methods=["GET"])
@automations_gate
def get_run_log(run_id):
    log = _get_runner().get_run_log(run_id)
    if log is None:
        return jsonify({"error": "no log for this run"}), 404
    return jsonify({"log": log})


# ---------------------------------------------------------------- scheduling

@automations_bp.route("/api/<automation_id>/schedule", methods=["POST"])
@automations_gate
def schedule_automation(automation_id):
    """Create a scheduler job (job_type='automation') + schedule for this
    automation. Reuses the platform scheduler's tables and _create_schedule
    helper; the engine's _execute_automation_job fires it. Requires a
    PROMOTED version — a schedule must never point at nothing. The automation
    GUID travels in ScheduledJobParameters (TargetId is int-typed).
    Body: {"schedule": {"type": "cron"|"interval"|"date", ...}, "inputs": {}}"""
    auto = _get_manager().get_automation(automation_id)
    if not auto:
        return jsonify({"error": "not found"}), 404
    if auto["pinned_version"] < 1:
        return jsonify({"error": "nothing promoted — dry-run and promote a version before scheduling"}), 400

    data = request.get_json(silent=True) or {}
    payload, code = _create_automation_schedule(
        auto, data.get("schedule"), data.get("inputs") or {},
        user_id=current_user.id,
        username=str(getattr(current_user, "username", current_user.id)),
    )
    return jsonify(payload), code


def _create_automation_schedule(auto, schedule_data, inputs, user_id, username):
    """Write the ScheduledJobs + params + ScheduleDefinitions rows for an
    automation schedule. Shared by the user route and the CC internal manage
    endpoint. Returns (payload_dict, http_code)."""
    automation_id = auto["automation_id"]
    if not isinstance(schedule_data, dict):
        return {"error": "'schedule' object is required (type: cron|interval|date)"}, 400

    from scheduler_routes import _create_schedule
    conn = _get_manager()._db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO ScheduledJobs
               (JobName, JobType, TargetId, Description, CreatedBy, CreatedAt, IsActive)
               VALUES (?, 'automation', 0, ?, ?, getutcdate(), 1)""",
            f"Automation: {auto['name']}",
            f"Scheduled run of automation '{auto['name']}' ({automation_id}), pinned v{auto['pinned_version']}",
            username,
        )
        cursor.execute("SELECT @@IDENTITY")
        job_id = int(cursor.fetchone()[0])

        params = {
            "automation_id": (automation_id, "string"),
            "inputs": (json.dumps(inputs or {}), "json"),
            "user_id": (str(user_id), "int"),
        }
        for name, (value, ptype) in params.items():
            cursor.execute(
                """INSERT INTO ScheduledJobParameters
                   (ScheduledJobId, ParameterName, ParameterValue, ParameterType)
                   VALUES (?, ?, ?, ?)""",
                job_id, name, value, ptype,
            )

        schedule_id = _create_schedule(cursor, job_id, schedule_data)
        if not schedule_id:
            conn.rollback()
            return {"error": "invalid schedule definition"}, 400
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"schedule_automation({automation_id}) failed: {e}")
        return {"error": str(e)}, 500
    finally:
        conn.close()

    return {
        "scheduled_job_id": job_id,
        "schedule_id": schedule_id,
        "pinned_version": auto["pinned_version"],
        "note": "the scheduler engine picks this up on its next poll "
                "(engine restart required if it predates the 'automation' job type)",
    }, 201


@automations_bp.route("/api/<automation_id>/schedules", methods=["GET"])
@automations_gate
def list_schedules(automation_id):
    """List scheduler jobs/schedules attached to this automation."""
    conn = _get_manager()._db_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT j.ScheduledJobId, j.JobName, j.IsActive,
                      s.ScheduleId, s.ScheduleType, s.CronExpression,
                      s.NextRunTime, s.LastRunTime, s.IsActive AS ScheduleActive
               FROM ScheduledJobs j
               JOIN ScheduledJobParameters p
                 ON p.ScheduledJobId = j.ScheduledJobId
                AND p.ParameterName = 'automation_id' AND p.ParameterValue = ?
               LEFT JOIN ScheduleDefinitions s ON s.ScheduledJobId = j.ScheduledJobId
               WHERE j.JobType = 'automation'
               ORDER BY j.ScheduledJobId""",
            automation_id,
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    return jsonify({"schedules": [
        {
            "scheduled_job_id": r.ScheduledJobId, "job_name": r.JobName,
            "job_active": bool(r.IsActive), "schedule_id": r.ScheduleId,
            "schedule_type": r.ScheduleType, "cron_expression": r.CronExpression,
            "next_run_time": r.NextRunTime.isoformat() if r.NextRunTime else None,
            "last_run_time": r.LastRunTime.isoformat() if r.LastRunTime else None,
            "schedule_active": bool(r.ScheduleActive) if r.ScheduleActive is not None else None,
        }
        for r in rows
    ]})


# --------------------------------------------------- live runs (Studio feed)

def _run_workdir(run: Dict) -> Optional[str]:
    log_path = (run or {}).get("log_path")
    return os.path.dirname(log_path) if log_path else None


def _read_events(run: Dict, after: int = 0, limit: int = 500):
    """Tail the run's events.jsonl sidecar starting after seq `after`."""
    workdir = _run_workdir(run)
    if not workdir:
        return []
    path = os.path.join(workdir, "events.jsonl")
    events = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("seq", 0) > after:
                    events.append(ev)
                    if len(events) >= limit:
                        break
    except FileNotFoundError:
        pass
    return events


def _run_live_payload(run: Dict, after: int = 0) -> Dict:
    """The Studio's poll payload: run state + new events + open checkpoints."""
    from .checkpoints import list_checkpoints
    events = _read_events(run, after)
    workdir = _run_workdir(run)
    checkpoints = list_checkpoints(workdir) if workdir else []
    return {
        "run": run,
        "events": events,
        "next": events[-1]["seq"] if events else after,
        "checkpoints": checkpoints,
        "pending_checkpoint": next((c for c in checkpoints if not c.get("decision")), None),
    }


@automations_bp.route("/api/runs/<run_id>/events", methods=["GET"])
@automations_gate
def run_events(run_id):
    run = _get_runner().get_run(run_id)
    if not run:
        return jsonify({"error": "not found"}), 404
    after = request.args.get("after", default=0, type=int)
    return jsonify(_run_live_payload(run, after))


@automations_bp.route("/api/active", methods=["GET"])
@automations_gate
def active_runs():
    return jsonify({"active": _get_runner().list_active_runs()})


@automations_bp.route("/api/runs/<run_id>/abort", methods=["POST"])
@automations_gate
def abort_run(run_id):
    ok, note = _get_runner().request_abort(run_id)
    return jsonify({"ok": ok, "note": note}), (200 if ok else 409)


@automations_bp.route("/api/runs/<run_id>/checkpoints/<checkpoint_id>/decision", methods=["POST"])
@automations_gate
def checkpoint_decision(run_id, checkpoint_id):
    """Human decision on a checkpoint gate: proceed resumes the script's poll
    loop; abort flips the run to 'aborting' (the supervision loop kills it)."""
    decision = (request.get_json(silent=True) or {}).get("decision")
    if decision not in ("proceed", "abort"):
        return jsonify({"error": "decision must be 'proceed' or 'abort'"}), 400
    result, code = _decide_checkpoint(run_id, checkpoint_id, decision, current_user.id)
    return jsonify(result), code


def _decide_checkpoint(run_id, checkpoint_id, decision, decided_by):
    from .checkpoints import decide_checkpoint
    runner = _get_runner()
    run = runner.get_run(run_id)
    if not run:
        return {"error": "run not found"}, 404
    workdir = _run_workdir(run)
    if not workdir:
        return {"error": "run has no workdir"}, 409
    checkpoint = decide_checkpoint(workdir, checkpoint_id, decision, decided_by)
    if checkpoint is None:
        return {"error": "checkpoint not found"}, 404
    if decision == "proceed":
        runner._db_set_run_status(run_id, "running", only_if_in=("waiting",))
    else:
        runner._db_set_run_status(run_id, "aborting", only_if_in=("waiting", "running"))
    try:
        from .runner import RunEventLog
        RunEventLog(workdir).emit("checkpoint_decided",
                                  checkpoint_id=checkpoint_id, decision=decision)
    except Exception:
        pass
    return {"checkpoint": checkpoint}, 200


# --------------------------------------------- runtime checkpoint (SDK side)

@automations_bp.route("/api/runtime/checkpoint", methods=["POST", "GET"])
def runtime_checkpoint():
    """SDK side of a checkpoint gate (run-token auth, like runtime/resolve).

    POST {token, message} → creates the gate, flips the run to 'waiting',
    notifies the requesting user (in-app always; SMS/email behind env flags),
    returns {checkpoint_id}.
    GET ?token=...&checkpoint_id=... → {decision: null|proceed|abort} for the
    SDK's poll loop."""
    if not getattr(cfg, "AUTOMATIONS_ENABLED", False):
        return jsonify({"error": "Automations feature is disabled"}), 403
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        token, message = data.get("token"), data.get("message") or "Checkpoint"
    else:
        token, message = request.args.get("token"), None

    from shared_auth import verify_automation_run_token
    claims, err = verify_automation_run_token(token or "")
    if err:
        return jsonify({"error": f"invalid run token: {err}"}), 403
    run = _get_runner().get_run(claims.get("run_id", ""))
    from .runner import LIVE_STATUSES
    if (not run or run.get("automation_id") != claims.get("automation_id")
            or run.get("status") not in LIVE_STATUSES):
        return jsonify({"error": "run token does not match a live run"}), 403
    workdir = _run_workdir(run)
    if not workdir:
        return jsonify({"error": "run has no workdir"}), 409

    from . import checkpoints as cp
    if request.method == "GET":
        checkpoint = cp.get_checkpoint(workdir, request.args.get("checkpoint_id", ""))
        if checkpoint is None:
            return jsonify({"error": "checkpoint not found"}), 404
        return jsonify({"decision": checkpoint.get("decision")})

    checkpoint = cp.create_checkpoint(workdir, message)
    _get_runner()._db_set_run_status(run["run_id"], "waiting", only_if_in=("running",))
    try:
        from .runner import RunEventLog
        RunEventLog(workdir).emit("checkpoint", checkpoint_id=checkpoint["checkpoint_id"],
                                  message=checkpoint["message"])
    except Exception:
        pass
    _notify_checkpoint(run, checkpoint)
    return jsonify({"checkpoint_id": checkpoint["checkpoint_id"], "poll_seconds": 2})


def _notify_checkpoint(run: Dict, checkpoint: Dict):
    """In-app is implicit (Mission Control + Studio show 'waiting' instantly).
    SMS/email are opt-in via env flags and go to the run's requesting user.
    Never fatal — a notification failure must not affect the gate."""
    auto = _get_manager().get_automation(run.get("automation_id", "")) or {}
    text = (f"AI Hub: automation '{auto.get('name', run.get('automation_id'))}' is paused "
            f"and waiting on you: {checkpoint.get('message')} — decide in Mission Control.")
    user_id = run.get("requested_by")
    if not user_id:
        return
    email, phone = _user_contact(user_id)
    if os.getenv("AUTOMATIONS_CHECKPOINT_NOTIFY_SMS", "false").lower() == "true" and phone:
        try:
            from AppUtils import sms_text_message_alert
            sms_text_message_alert(text, phone)
        except Exception as e:
            logger.warning(f"checkpoint SMS notify failed: {e}")
    if os.getenv("AUTOMATIONS_CHECKPOINT_NOTIFY_EMAIL", "false").lower() == "true" and email:
        try:
            from AppUtils import send_email_notification
            send_email_notification(email, "Automation waiting on your decision", text)
        except Exception as e:
            logger.warning(f"checkpoint email notify failed: {e}")


def _user_contact(user_id):
    """(email, phone) for a user id; missing columns/rows are simply None."""
    email = phone = None
    try:
        conn = _get_manager()._db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM [User] WHERE id = ?", int(user_id))
        row = cursor.fetchone()
        email = row[0] if row else None
        for col in ("phone", "phone_number", "mobile"):
            try:
                cursor.execute(f"SELECT {col} FROM [User] WHERE id = ?", int(user_id))
                row = cursor.fetchone()
                if row and row[0]:
                    phone = row[0]
                    break
            except Exception:
                continue
        conn.close()
    except Exception as e:
        logger.warning(f"user contact lookup failed for {user_id}: {e}")
    return email, phone


# ------------------------------------------------------------ webhook trigger

def _webhook_token(automation_id: str) -> Optional[str]:
    """Derived (never stored) per-automation webhook token:
    HMAC(jwt_secret, automation_id). Rotating CC_JWT_SECRET rotates all hooks."""
    import hashlib
    import hmac as _hmac
    try:
        from shared_auth import get_jwt_secret
        secret = get_jwt_secret()
    except Exception:
        secret = None
    if not secret:
        return None
    return _hmac.new(secret.encode("utf-8"),
                     f"automation-hook:{automation_id}".encode("utf-8"),
                     hashlib.sha256).hexdigest()[:32]


@automations_bp.route("/api/<automation_id>/webhook", methods=["GET"])
@automations_gate
def get_webhook(automation_id):
    """Return the automation's webhook trigger path (derived token, no storage)."""
    if not _get_manager().get_automation(automation_id):
        return jsonify({"error": "not found"}), 404
    token = _webhook_token(automation_id)
    if not token:
        return jsonify({"error": "webhook unavailable: no JWT secret configured"}), 503
    return jsonify({
        "url_path": f"/automations/api/hook/{automation_id}/{token}",
        "method": "POST",
        "body": {"inputs": {"<declared input>": "<value>"}},
        "note": "POST JSON to this path to trigger the promoted version; "
                "rotating CC_JWT_SECRET rotates the token",
    })


@automations_bp.route("/api/hook/<automation_id>/<token>", methods=["POST"])
def webhook_trigger(automation_id, token):
    """External webhook trigger (P4): fire the PROMOTED version. Auth is the
    derived per-automation token in the path — constant-time compared, no
    session. Runs async; responds 202 with the run_id to poll. Skip-if-running
    and input validation behave exactly like any other trigger."""
    import hmac as _hmac
    if not getattr(cfg, "AUTOMATIONS_ENABLED", False):
        return jsonify({"error": "Automations feature is disabled"}), 403
    expected = _webhook_token(automation_id)
    if not expected or not _hmac.compare_digest(token, expected):
        return jsonify({"error": "invalid webhook token"}), 403
    auto = _get_manager().get_automation(automation_id)
    if not auto:
        return jsonify({"error": "not found"}), 404
    if auto["pinned_version"] < 1:
        return jsonify({"error": "nothing promoted"}), 409

    inputs = (request.get_json(silent=True) or {}).get("inputs") or {}
    # validate inputs BEFORE going async so the caller gets a real 400
    from .runner import resolve_inputs
    manifest = _get_manager().get_manifest(automation_id, auto["pinned_version"]) or {}
    _, err = resolve_inputs(manifest, inputs)
    if err:
        return jsonify({"error": err}), 400

    run_id = str(uuid.uuid4())
    threading.Thread(
        target=_get_runner().run, daemon=True,
        kwargs=dict(automation_id=automation_id, inputs=inputs, trigger="webhook",
                    run_id=run_id),
    ).start()
    return jsonify({"run_id": run_id, "status": "started"}), 202


# ------------------------------------------------ runtime credential resolve

@automations_bp.route("/api/runtime/resolve", methods=["POST"])
def runtime_resolve():
    """Resolve ONE connection/secret for a live automation run (P2 SDK path).

    Called by the aihub_runtime SDK inside the automation subprocess. Auth is
    the signed run token itself (scoped to one run + an allowlist of names) —
    no session. Defense in depth: signature + audience + expiry (shared_auth),
    name-in-allowlist, and the run must still be 'running' in AutomationRuns
    (a leaked token is useless once the run finishes). Values are returned
    once over localhost and never logged."""
    if not getattr(cfg, "AUTOMATIONS_ENABLED", False):
        return jsonify({"error": "Automations feature is disabled"}), 403
    data = request.get_json(silent=True) or {}
    token, kind, name = data.get("token"), data.get("kind"), data.get("name")
    if not token or kind not in ("connection", "secret") or not name:
        return jsonify({"error": "token, kind (connection|secret), and name are required"}), 400

    from shared_auth import verify_automation_run_token
    claims, err = verify_automation_run_token(token)
    if err:
        return jsonify({"error": f"invalid run token: {err}"}), 403

    allowed = claims.get("connections" if kind == "connection" else "secrets") or []
    if name not in allowed:
        logger.warning(f"runtime_resolve: run {claims.get('run_id')} asked for undeclared {kind} '{name}'")
        return jsonify({"error": f"{kind} '{name}' is not declared in this automation's manifest"}), 403

    from .runner import LIVE_STATUSES
    run = _get_runner().get_run(claims.get("run_id", ""))
    if (not run or run.get("status") not in LIVE_STATUSES
            or run.get("automation_id") != claims.get("automation_id")):
        return jsonify({"error": "run token does not match a live run"}), 403

    if kind == "connection":
        value = _get_runner()._resolve_connection(name)
    else:
        value = _get_runner()._resolve_secret(name)
    if not value:
        return jsonify({"error": f"{kind} '{name}' could not be resolved"}), 404
    return jsonify({"value": value})


# --------------------------------------------- internal (CC builder tools)

@automations_bp.route("/api/internal/manage", methods=["POST"])
def internal_manage():
    """Service-to-service management dispatch for the Command Center's
    automation tools (P3). Auth: X-API-Key (service trust anchor) + the
    CC-verified user_context; role >= 2 (Developer) is enforced HERE too so
    the chokepoint doesn't rely on CC-side gating alone (same lesson as
    CC_BUILD_ALLOW_ALL_USERS).

    Body: {"action": ..., "user_context": {"user_id": int, "role": int,
           "username": str}, "payload": {...}}"""
    if not _service_key_ok(request.headers.get("X-API-Key")):
        return jsonify({"error": "unauthorized"}), 401
    if not getattr(cfg, "AUTOMATIONS_ENABLED", False):
        return jsonify({"error": "Automations feature is disabled"}), 403
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    uc = data.get("user_context") or {}
    payload = data.get("payload") or {}
    try:
        role = int(uc.get("role") or 0)
        user_id = int(uc.get("user_id") or 0)
    except (TypeError, ValueError):
        role, user_id = 0, 0
    if role < 2 or user_id <= 0:
        return jsonify({"error": "Developer role required"}), 403
    username = str(uc.get("username") or user_id)

    mgr = _get_manager()
    runner = _get_runner()
    aid = payload.get("automation_id")

    def _need_auto():
        a = mgr.get_automation(aid) if aid else None
        return a

    try:
        if action == "list":
            return jsonify({"automations": mgr.list_automations()})

        if action == "get":
            auto = _need_auto()
            if not auto:
                return jsonify({"error": "automation not found"}), 404
            auto["versions"] = mgr.list_versions(aid)
            auto["manifest"] = mgr.get_manifest(aid)
            auto["code"] = mgr.get_code(aid)
            return jsonify({"automation": auto})

        if action == "create":
            ok, auto, error = mgr.create_automation(
                name=payload.get("name", ""), description=payload.get("description", ""),
                owner_user_id=user_id, environment_id=payload.get("environment_id"))
            if not ok:
                return jsonify({"error": error}), 400
            warning = None
            if not payload.get("environment_id") and payload.get("provision_environment", True):
                _provision_environment_async(auto)
                warning = ("dedicated environment is being provisioned in the background; "
                           "runs use the bundled Python until it is ready")
            resp = {"automation": auto}
            if warning:
                resp["warning"] = warning
            return jsonify(resp), 201

        if action == "save_code":
            code = payload.get("code")
            if not isinstance(code, str) or not code.strip():
                return jsonify({"error": "'code' is required"}), 400
            manifest = payload.get("manifest")
            if manifest is not None:
                ok, errors = validate_manifest(manifest)
                if not ok:
                    return jsonify({"error": "invalid manifest", "details": errors}), 400
            ok, new_version, errors = mgr.save_version(aid, code, manifest)
            if not ok:
                return jsonify({"error": errors[0] if errors else "save failed",
                                "details": errors}), 400
            return jsonify({"version": new_version})

        if action == "promote":
            ok, version, error = mgr.promote(aid, payload.get("version"))
            if not ok:
                return jsonify({"error": error}), 400
            return jsonify({"pinned_version": version})

        if action in ("dry_run", "run"):
            auto = _need_auto()
            if not auto:
                return jsonify({"error": "automation not found"}), 404
            # AIHUB-0058: shared inline path — returns early with the pending
            # checkpoint question instead of blocking until the client times out.
            result, code = _run_inline(aid, payload.get("inputs") or {},
                                       "manual", payload.get("version"),
                                       user_id, dry_run=(action == "dry_run"))
            return jsonify(result), code

        if action == "runs":
            runs = runner.list_runs(aid, min(int(payload.get("limit", 20)), 100))
            return jsonify({"runs": runs})

        if action == "run_log":
            log = runner.get_run_log(payload.get("run_id", ""))
            if log is None:
                return jsonify({"error": "no log for this run"}), 404
            return jsonify({"log": log})

        if action == "active":
            return jsonify({"active": runner.list_active_runs()})

        if action == "run_events":
            run = runner.get_run(payload.get("run_id", ""))
            if not run:
                return jsonify({"error": "run not found"}), 404
            return jsonify(_run_live_payload(run, int(payload.get("after", 0))))

        if action == "abort":
            ok, note = runner.request_abort(payload.get("run_id", ""))
            return jsonify({"ok": ok, "note": note}), (200 if ok else 409)

        if action == "checkpoint_decision":
            decision = payload.get("decision")
            if decision not in ("proceed", "abort"):
                return jsonify({"error": "decision must be 'proceed' or 'abort'"}), 400
            result, code = _decide_checkpoint(payload.get("run_id", ""),
                                              payload.get("checkpoint_id", ""),
                                              decision, user_id)
            return jsonify(result), code

        if action == "schedule":
            auto = _need_auto()
            if not auto:
                return jsonify({"error": "automation not found"}), 404
            if auto["pinned_version"] < 1:
                return jsonify({"error": "nothing promoted — dry-run and promote first"}), 400
            resp, code = _create_automation_schedule(
                auto, payload.get("schedule"), payload.get("inputs") or {},
                user_id=user_id, username=username)
            return jsonify(resp), code

        return jsonify({"error": f"unknown action '{action}'"}), 400
    except Exception as e:
        logger.exception(f"internal_manage action={action} failed")
        return jsonify({"error": str(e)}), 500


# ------------------------------------------------- internal (scheduler seam)

@automations_bp.route("/api/internal/run", methods=["POST"])
def internal_run():
    """Service-to-service run trigger for the P1 'automation' scheduler job
    type. X-API-Key auth, same as the CC scheduled-run pattern. Runs the
    PINNED version, waits, and returns the honest outcome so the scheduler
    can record it."""
    if not _service_key_ok(request.headers.get("X-API-Key")):
        return jsonify({"error": "unauthorized"}), 401
    if not getattr(cfg, "AUTOMATIONS_ENABLED", False):
        return jsonify({"error": "Automations feature is disabled"}), 403
    data = request.get_json(silent=True) or {}
    automation_id = data.get("automation_id")
    if not automation_id:
        return jsonify({"error": "'automation_id' is required"}), 400
    result = _get_runner().run(
        automation_id,
        inputs=data.get("inputs") or {},
        trigger=data.get("trigger", "schedule"),
        requested_by=data.get("requested_by"),
    )
    code = 200 if result.get("status") != "error" else 400
    return jsonify(result), code
