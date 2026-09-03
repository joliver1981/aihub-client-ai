"""
Pack 20 — T-* per-tool smoke for The Agent (2026-09-03, james: "GREAT IDEA").

WHY: The Agent mounts ~95 tools across 15 modules and the pack drives maybe two
thirds of them through real journeys. The embarrassing class of defect on an
installed build is not a wrong answer, it is a whole tool that is simply gone
or dead — a module the frozen build never bundled, a handler that raises on
first use. This group asks the model to call EVERY tool once with fixed,
harmless arguments and grades each tool on its own `tool` + `tool_result`
events: was it called at all, did the handler answer with its documented
shape (an honest not-found is fine), or did it crash (traceback / missing
module / internal error = FAIL).

HOW IT STAYS SAFE
  read       real call, read-only (lists, schemas, SELECT 1, print(6*7))
  lookup     a mutating tool called on a NONEXISTENT id/name — proves the
             tool is registered and its handler runs, changes nothing
  lifecycle  create -> use -> delete of a throwaway object named pack20-smoke-*
  pair       remember_preference -> forget_preference
  elsewhere  tools that send mail, raise work, save skills/portals or spend
             money are NOT probed here; the row names the group that covers
             them (A2/A3/A6/PT/R-10) so nothing is silently unaccounted for

INVENTORY: builds after 2026-09-03 emit an `init` event with the mounted tool
names (brain.py); T-0 compares it with this file's expected set. On older
builds the row says the event is not available.

USED BY runner.py after the R-* rows (PACK20_SKIP_TOOL_SMOKE=1 to leave it
out) and standalone:
    <aihub-agent python> tool_smoke_checks.py
    AIHUB_TARGET_HOST=10.0.0.6 API_KEY=<box key> ... tool_smoke_checks.py
"""
import io
import json
import os
import re
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
FIX_PNG = os.path.join(HERE, "fixtures", "r6_bars.png")
FIX_PDF = os.path.join(APP_ROOT, "test_human", "04_Planning", "fixtures", "P2_annual_SOP.pdf")
TURN_TIMEOUT = 600
SMOKE_SECRET = "PACK20_SMOKE_SECRET"

CRASH = re.compile(r"Traceback \(most recent|No module named|ImportError|NameError:|TypeError:|"
                   r"AttributeError:|KeyError:|Internal Server Error|internal error|"
                   r"UnboundLocalError|IndentationError", re.I)


# ------------------------------------------------------------------ probes
# (tool, args, kind, expect_ok) — expect_ok: True = must succeed, False = an
# honest error is the expected shape, None = either is fine (env-dependent).
# Batches are lists run in ONE turn, in order (lifecycles depend on order).

def probes(ctx):
    png, pdf = ctx.get("png_id") or "r6_bars.png", ctx.get("pdf_id") or "P2_annual_SOP.pdf"
    return [
        # --- platform / data
        [("list_data_connections", {}, "read", True),
         ("get_connection_schema", {"connection": "AIRDB2"}, "read", True),
         ("probe_connection_query", {"connection": "AIRDB2", "sql": "SELECT 1 AS one"}, "read", True),
         ("list_playbooks", {}, "read", True),
         ("list_recent_runs", {}, "read", True),
         ("list_secret_names", {}, "read", True),
         ("get_my_contact_info", {}, "read", True),
         ("find_user_contact", {"name": "admin"}, "read", None),
         ("ask_agent", {"agent_id": 999999, "question": "smoke"}, "lookup", False)],
        # --- agent builder
        [("list_agents", {}, "read", True),
         ("get_agent_builder_options", {}, "read", True),
         ("get_agent_config", {"agent": "zz-pack20-none"}, "lookup", False),
         ("create_general_agent", {"name": "pack20-smoke-agent"}, "lifecycle", True),
         # real schema props (the first dev run passed 'description' and got a
         # validation error): update takes name/objective/enabled; tools take
         # core_tools/custom_tools/mode
         ("update_general_agent", {"agent": "pack20-smoke-agent", "objective": "pack20 smoke objective"}, "lifecycle", True),
         ("set_agent_tools", {"agent": "pack20-smoke-agent", "core_tools": [], "mode": "replace"}, "lifecycle", None),
         ("set_agent_document_types", {"agent": "pack20-smoke-agent", "document_types": []}, "lifecycle", None),
         ("assign_agent_groups", {"agent": "pack20-smoke-agent", "group_ids": []}, "lifecycle", None),
         ("add_agent_knowledge", {"agent": "pack20-smoke-agent", "path": "C:/pack20-smoke-none.txt"}, "lookup", False),
         ("delete_agent_knowledge", {"knowledge_id": 999999}, "lookup", False),
         # confirmed=true: the delete tools are two-step by design; without it the
         # throwaway object would outlive the smoke
         ("delete_general_agent", {"agent": "pack20-smoke-agent", "confirmed": True}, "lifecycle", True)],
        # --- automations
        [("create_automation", {"name": "pack20_smoke_auto"}, "lifecycle", True),
         ("save_automation_code", {"automation_id": "<id returned by create_automation>",
                                   "code": "print('pack20 smoke')"}, "lifecycle", True),
         ("get_automation", {"automation_id": "<same id>"}, "lifecycle", True),
         ("dry_run_automation", {"automation_id": "<same id>"}, "lifecycle", None),
         ("check_automation_run", {"run_id": 999999}, "lookup", False),
         ("run_automation", {"automation_id": 999999}, "lookup", False),
         ("promote_automation", {"automation_id": 999999}, "lookup", False),
         ("schedule_automation", {"automation_id": 999999, "cron": "0 9 * * 1"}, "lookup", False),
         ("decide_automation_checkpoint", {"run_id": 999999, "checkpoint_id": 999999, "decision": "reject"}, "lookup", False),
         ("delete_automation", {"automation_id": "<same id from create_automation>", "confirmed": True}, "lifecycle", True)],
        # --- code flows
        [("list_code_flows", {}, "read", True),
         ("get_code_flow", {"name": "zz-pack20-none"}, "lookup", False),
         ("create_code_flow", {"name": "pack20-smoke-flow"}, "lifecycle", True),
         ("add_code_step", {"name": "pack20-smoke-flow", "step_name": "s1", "code": "print('s1')"}, "lifecycle", True),
         ("add_code_step", {"name": "pack20-smoke-flow", "step_name": "s2", "code": "print('s2')"}, "lifecycle", True),
         # from/to are step IDS (returned by add_code_step), not step names —
         # the first box run passed the names and got an honest 400.
         ("wire_steps", {"name": "pack20-smoke-flow", "from_step": "<step id of s1>", "to_step": "<step id of s2>"}, "lifecycle", True),
         ("unwire_steps", {"name": "pack20-smoke-flow", "from_step": "<step id of s1>", "to_step": "<step id of s2>"}, "lifecycle", True),
         ("update_step_code", {"name": "pack20-smoke-flow", "step_id": "<id of s1>", "code": "print('s1b')"}, "lifecycle", None),
         ("remove_code_step", {"name": "pack20-smoke-flow", "step_id": "<id of s2>"}, "lifecycle", None),
         ("dry_run_code_flow", {"name": "pack20-smoke-flow"}, "lifecycle", None),
         ("run_code_flow", {"name": "zz-pack20-none"}, "lookup", False),
         ("schedule_code_flow", {"name": "zz-pack20-none", "cron": "0 9 * * 1"}, "lookup", False),
         ("delete_code_flow", {"name": "pack20-smoke-flow", "confirmed": True}, "lifecycle", True)],
        # --- documents / files
        [("list_documents", {}, "read", True),
         ("query_document_records", {}, "read", None),
         ("search_documents", {"query": "pack20 smoke"}, "read", None),
         ("get_document", {"document_id": 999999}, "lookup", False),
         ("list_server_files", {"path": "."}, "read", None),
         ("import_documents", {"path": "C:/pack20-smoke-none"}, "lookup", False),
         ("read_file", {"path": png}, "read", True),
         ("offer_file_download", {"server_path": "C:/pack20-smoke-none.txt"}, "lookup", False),
         ("manipulate_pdf", {"operation": "info", "path": pdf}, "read", True)],
        # --- code / export / rich
        [("run_python", {"code": "print(6*7)"}, "read", True),
         ("export_data", {"name": "pack20_smoke", "format": "csv", "rows_json": "[{\"a\": 1}, {\"a\": 2}]"}, "read", True),
         ("render_map", {"title": "pack20 smoke", "regions_json": "[{\"name\": \"NJ\", \"value\": 1}]"}, "read", True),
         ("geocode_places", {"places": "Austin TX"}, "read", None),
         ("search_web", {"query": "AI Hub regression smoke"}, "read", None)],
        # --- views
        [("list_saved_views", {}, "read", True),
         ("get_view", {"name": "zz-pack20-none"}, "lookup", False),
         ("save_view", {"name": "pack20-smoke-view", "tiles_json": "[]"}, "lifecycle", None),
         ("rename_view", {"name": "pack20-smoke-view", "new_name": "pack20-smoke-view2"}, "lifecycle", None),
         ("schedule_view_refresh", {"name": "zz-pack20-none"}, "lookup", False),
         ("schedule_view_email", {"name": "zz-pack20-none", "to": "nobody@example.invalid"}, "lookup", False),
         ("delete_view", {"name": "pack20-smoke-view2", "confirmed": True}, "lifecycle", None),
         ("delete_view", {"name": "pack20-smoke-view", "confirmed": True}, "lifecycle", None)],
        # --- integrations / mcp
        [("list_integrations", {}, "read", True),
         ("list_mcp_servers", {}, "read", True),
         ("get_integration_operations", {"integration_id": 999999}, "lookup", False),
         ("execute_integration_operation", {"integration_id": 999999, "operation": "noop"}, "lookup", False),
         ("assign_integration_groups", {"integration_id": 999999, "group_ids": []}, "lookup", False)],
        # --- portals
        [("lookup_portal", {}, "read", None),
         ("list_portal_workflows", {}, "read", True),
         ("describe_portal_workflow", {"name": "zz-pack20-none"}, "lookup", False),
         ("run_portal_workflow", {"name": "zz-pack20-none"}, "lookup", False),
         ("schedule_portal_workflow", {"name": "zz-pack20-none", "cron": "0 9 * * 1"}, "lookup", False),
         ("cancel_portal_workflow_schedule", {"name": "zz-pack20-none"}, "lookup", False),
         ("check_portal_run", {"run_id": 999999}, "lookup", False),
         ("portal_fetch", {"portal_name": "zz-pack20-none", "task": "read the title"}, "lookup", False)],
        # --- email (read side) / work / preferences / secrets
        [("get_agent_email_status", {}, "read", None),
         ("list_my_email", {}, "read", None),
         ("read_email", {"event_id": 999999}, "lookup", False),
         ("list_email_attachments", {"event_id": 999999}, "lookup", False),
         ("read_attachment", {"event_id": 999999, "attachment_id": 999999}, "lookup", False),
         ("save_attachment", {"event_id": 999999, "attachment_id": 999999}, "lookup", False),
         ("list_my_work", {}, "read", True),
         ("list_skills", {}, "read", True),
         ("remember_preference", {"preference": "pack20 smoke: prefers one-line answers"}, "pair", True),
         ("forget_preference", {"preference": "pack20 smoke: prefers one-line answers"}, "pair", None),
         ("store_platform_secret", {"name": SMOKE_SECRET, "value": "smoke-value"}, "lifecycle", True)],
    ]


ELSEWHERE = {
    "send_email": "A6-6 (sends real mail)", "draft_email_reply": "A6-3", "setup_agent_email": "A6-7",
    "raise_work_item": "A2-1", "schedule_agent_task": "PT-11 / M-2", "save_skill": "A3-1",
    "save_portal": "PT-4", "generate_image": "R-10 (opt-in, real money)",
}


# ------------------------------------------------------------------ client

class Agent:
    def __init__(self, base, token):
        self.base = base.rstrip("/")
        self.hdr = {"Authorization": f"Bearer {token}"}

    def turn(self, message, timeout=TURN_TIMEOUT):
        """-> (calls, reply, init) where calls = [{name, input, ok, preview}] in order."""
        r = requests.post(f"{self.base}/api/chat",
                          json={"message": message, "session_id": None, "timezone": "America/New_York"},
                          headers=self.hdr, stream=True, timeout=(15, timeout))
        r.raise_for_status()
        calls, by_id, texts, init = [], {}, [], None
        for raw in r.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data: "):
                continue
            try:
                ev = json.loads(raw[6:])
            except Exception:
                continue
            t = ev.get("type")
            if t == "init":
                init = ev
            elif t == "tool":
                c = {"name": ev.get("name", "").replace("mcp__aihub__", ""),
                     "input": ev.get("input") or {}, "ok": None, "preview": ""}
                calls.append(c)
                by_id[ev.get("id")] = c
            elif t == "tool_result":
                c = by_id.get(ev.get("id"))
                if c:
                    c["ok"] = ev.get("ok")
                    c["preview"] = str(ev.get("preview") or "")
            elif t == "text":
                texts.append(ev.get("text", ""))
            elif t == "done":
                break
        return calls, "\n".join(texts).strip(), init

    def upload(self, name, data):
        r = requests.post(f"{self.base}/api/uploads", data=data,
                          headers={**self.hdr, "X-File-Name": name}, timeout=60)
        r.raise_for_status()
        return r.json().get("file_id")


def batch_prompt(batch):
    lines = ["TOOL SMOKE TEST (throwaway test tenant; I authorize every call below).",
             "Call each tool exactly once with EXACTLY the given JSON arguments, in this order, "
             "even if you expect it to fail — an error result is a valid outcome. Do not skip or "
             "substitute any call, do not call tools that are not listed, and do not ask for "
             "confirmation. Where an argument says <...> use the value returned by the earlier call. "
             "When every call is done, reply with one line per tool: `<tool>: ok|error`.", ""]
    for i, (tool, args, _k, _e) in enumerate(batch, 1):
        lines.append(f"{i}. {tool} {json.dumps(args)}")
    return "\n".join(lines)


def single_prompt(tool, args):
    return (f"TOOL SMOKE TEST (throwaway test tenant; I authorize this call). Call the tool "
            f"`{tool}` exactly once with EXACTLY these JSON arguments: {json.dumps(args)} — "
            f"an error result is a valid outcome; do not substitute, do not call any other tool, "
            f"do not ask for confirmation. Then reply `{tool}: ok|error`.")


def grade(tool, expect_ok, call):
    """-> (ok, evidence) for one tool given its (first) call, or None if never called."""
    if call is None:
        return False, "NOT CALLED"
    prev = call["preview"]
    crashed = bool(CRASH.search(prev))
    if crashed:
        return False, f"handler CRASHED: ok={call['ok']} preview={prev[:160]!r}"
    if expect_ok is True and call["ok"] is False:
        return False, f"expected ok, got error: {prev[:160]!r}"
    shape = "ok" if call["ok"] else "honest-error"
    return True, f"{shape}: {prev[:110]!r}"


# ------------------------------------------------------------------ rows

def run(check, base, token, app_base=None):
    ag = Agent(base, token)
    ctx = {}
    try:
        with open(FIX_PNG, "rb") as fh:
            ctx["png_id"] = ag.upload("r6_bars.png", fh.read())
        with open(FIX_PDF, "rb") as fh:
            ctx["pdf_id"] = ag.upload("P2_annual_SOP.pdf", fh.read())
    except Exception as e:
        check("T-0u", "smoke fixtures uploaded", False, e)

    batches = probes(ctx)
    expected = sorted({t for b in batches for t, *_ in b} | set(ELSEWHERE))
    seen = {}          # tool -> first call dict
    init_ev = None
    t0 = time.time()
    for bi, batch in enumerate(batches, 1):
        try:
            calls, reply, init = ag.turn(batch_prompt(batch))
            init_ev = init_ev or init
            for c in calls:
                seen.setdefault(c["name"], c)
        except Exception as e:
            check(f"T-b{bi}", f"smoke batch {bi} turn", False, e)

    # second pass: anything the model skipped gets one dedicated turn
    missed = [(t, a, k, e) for b in batches for (t, a, k, e) in b if t not in seen]
    for tool, args, _k, _e in missed:
        try:
            calls, reply, init = ag.turn(single_prompt(tool, args))
            init_ev = init_ev or init
            for c in calls:
                seen.setdefault(c["name"], c)
            if tool not in seen:
                seen[tool] = None
                seen[f"__reply__{tool}"] = reply[:160]
        except Exception as e:
            seen[tool] = None
            seen[f"__reply__{tool}"] = f"turn error: {e}"

    # T-0 inventory (builds that emit the init event)
    if init_ev and init_ev.get("tools"):
        mounted = sorted(set(init_ev["tools"]) - {"Skill"})
        missing = sorted(set(expected) - set(mounted))
        extra = sorted(set(mounted) - set(expected))
        check("T-0", f"inventory: every expected tool is mounted on the target ({len(expected)} expected)",
              not missing, f"mounted={len(mounted)} missing={missing or 'none'} "
                           f"not-in-expected-set={extra or 'none'} model={init_ev.get('model')} "
                           f"mcp={init_ev.get('mcp_servers')}")
    else:
        check("T-0", "inventory (init event)", True,
              f"SKIP: this build does not emit the init tool inventory (added 2026-09-03); "
              f"{len(expected)} tools expected, graded individually below")

    # one row per tool
    graded = set()
    for batch in batches:
        for tool, args, kind, expect_ok in batch:
            if tool in graded:
                continue
            graded.add(tool)
            call = seen.get(tool)
            ok, ev = grade(tool, expect_ok, call)
            if call is None:
                ev += f" — reply={seen.get('__reply__' + tool, '')!r}"
            check(f"T-{tool}", f"{kind}: {tool}", ok, ev)
    for tool, where in ELSEWHERE.items():
        check(f"T-{tool}", f"elsewhere: {tool}", True, f"SKIP: exercised by {where}")

    # cleanup: the secret has no delete tool; use the main app when reachable
    if app_base:
        try:
            s = _app_session(app_base)
            if s:
                s.delete(f"{app_base}/api/local-secrets/{SMOKE_SECRET}", timeout=30)
        except Exception:
            pass
    check("T-Σ", f"per-tool smoke: {len(graded)} probed + {len(ELSEWHERE)} elsewhere in {time.time() - t0:.0f}s",
          True, f"not-called={[t for t in graded if seen.get(t) is None] or 'none'}")


def _app_session(base):
    s = requests.Session()
    r = s.get(f"{base}/login", timeout=20)
    hid = dict(re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', r.text))
    hid.update(dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"', r.text)))
    d = {"username": "admin", "password": "admin", "submit": "Login"}
    d.update(hid)
    r = s.post(f"{base}/login", data=d, allow_redirects=True, timeout=30)
    return s if "/login" not in r.url else None


# --------------------------------------------------------------- standalone

def _standalone():
    sys.path.insert(0, APP_ROOT)
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    from dotenv import load_dotenv
    load_dotenv(os.path.join(APP_ROOT, ".env"))
    try:
        import secure_config
        secure_config.load_secure_config()
    except Exception:
        pass
    import shared_auth
    host = os.getenv("AIHUB_TARGET_HOST", "127.0.0.1")
    base = f"http://{host}:{os.getenv('AGENT_SERVICE_PORT') or int(os.getenv('HOST_PORT', '5001')) + 110}"
    app_host = "localhost" if host in ("127.0.0.1", "localhost") else host
    app_base = os.getenv("REGP_BASE") or f"http://{app_host}:{os.getenv('HOST_PORT', '5001')}"
    token = shared_auth.sign_cc_token({"user_id": 1, "role": 3, "tenant_id": os.getenv("TENANT_ID", ""),
                                       "username": "pack20-runner", "name": "Pack 20 Runner"})
    rows = []

    def check(cid, name, ok, evidence):
        rows.append((cid, name, bool(ok), str(evidence)[:600]))
        print(f"[{'PASS' if ok else 'FAIL'}] {cid} {name} — {str(evidence)[:300]}", flush=True)

    print(f"target={base} app={app_base}")
    run(check, base, token, app_base=app_base)
    passed = sum(1 for r in rows if r[2])
    print(f"\nT-* {passed}/{len(rows)} PASS")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(_standalone())
