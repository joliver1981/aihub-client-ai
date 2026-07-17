"""
Dependency-free read-back of the persisted compile result from the builder's
final graph state (AIHUB-0034).

Kept import-light (stdlib only) so it is unit-testable without pulling in the
FastAPI/sse_starlette stack that `chat.py` needs.

A compile can surface in three places in `final_state`, depending on which node
ran, so the honest `workflow_saved` read-back must look in all of them:
  1. top-level `compile_result` (now a declared BuilderState channel — the
     handle_agent_response resume path),
  2. inside a plan step's result (`current_plan.steps[i].result.compile_result`
     — the execute() first-message-full-plan path),
  3. inside `execution_results[i].compile_result` (belt).
We only report a compile that actually saved a workflow (status success/draft)
AND carries `workflow_data.nodes`, so the read-back node types are real.
"""


def _saved_compile(cr):
    return (isinstance(cr, dict)
            and cr.get("status") in ("success", "draft")
            and isinstance(cr.get("workflow_data"), dict))


def resolve_user_facing_workflow_id(final_state, compile_result):
    """AIHUB-0038 R2 (F2): the row the USER opens is not always the row the
    compile wrote. The executor's plan step creates/tracks the NAMED row
    (saved_workflow_id), while the WorkflowAgent compile can land in its own
    row — live: named 'truth-test-2' = 1261, compile row = 1260, and the
    read-back vouched 1260. Prefer the tracked named row; fall back to the
    compile row."""
    if isinstance(final_state, dict):
        plan = final_state.get("current_plan") or {}
        for step in reversed(plan.get("steps", []) or []):
            if isinstance(step, dict):
                sid = (step.get("result") or {}).get("saved_workflow_id")
                if sid:
                    return sid
        created = (final_state.get("created_resources") or {}).get("workflows") or []
        for entry in reversed(created):
            if isinstance(entry, dict) and entry.get("id"):
                return entry["id"]
    return (compile_result or {}).get("workflow_id")


def shape_nodes(raw_nodes):
    """Shape persisted nodes into [{type, configured}]. A node is 'configured'
    only when it carries non-empty settings (data) — AIHUB-0038 R2 (F1): a
    hollow placeholder (e.g. an 'Automation' node with empty data) must not
    count as covering a transfer capability."""
    shaped = []
    for n in (raw_nodes or []):
        if isinstance(n, dict) and n.get("type"):
            shaped.append({"type": n["type"],
                           "configured": bool(n.get("data") or n.get("config") or n.get("properties"))})
    return shaped


def fetch_workflow_readback(workflow_id, base_url, api_key, timeout=8):
    """TRUE read-back: fetch the PERSISTED row from the platform and shape it
    for the workflow_saved event. Returns {workflow_id, node_types, nodes,
    source:'db_readback'} or None on any failure (caller falls back to the
    compile-result echo). Stdlib-only on purpose."""
    import json as _json
    import urllib.request as _rq
    try:
        req = _rq.Request(
            f"{str(base_url).rstrip('/')}/get/workflow/{int(workflow_id)}",
            headers={"X-API-Key": api_key or ""})
        with _rq.urlopen(req, timeout=timeout) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, dict) or "nodes" not in data:
            return None
        nodes = shape_nodes(data.get("nodes"))
        return {
            "workflow_id": int(workflow_id),
            "node_types": [n["type"] for n in nodes],
            "nodes": nodes,
            "source": "db_readback",
        }
    except Exception:
        return None


def extract_compile_result(final_state):
    """Return the most recent saved compile result in `final_state`, or None."""
    if not isinstance(final_state, dict):
        return None

    top = final_state.get("compile_result")
    if _saved_compile(top):
        return top

    plan = final_state.get("current_plan") or {}
    # later steps win (most recent build in a multi-step plan)
    for step in reversed(plan.get("steps", []) or []):
        cr = (step.get("result") or {}).get("compile_result") if isinstance(step, dict) else None
        if _saved_compile(cr):
            return cr

    for res in reversed(final_state.get("execution_results", []) or []):
        cr = res.get("compile_result") if isinstance(res, dict) else None
        if _saved_compile(cr):
            return cr

    return None
