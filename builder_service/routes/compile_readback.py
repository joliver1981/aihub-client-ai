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
