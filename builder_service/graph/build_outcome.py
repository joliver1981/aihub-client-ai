"""
Honest build-result messages for the Workflow Builder (AIHUB-0034 F2).

Dependency-free so it is unit-testable in isolation. The compile handler in
graph.nodes composes the user-facing reply by appending a status block to the
agent's `response_text` — but that response_text is the LLM's SPECULATIVE message
written before the compile result was known, so it can say "✅ Workflow created /
Verified configuration" for a workflow that actually landed as a broken DRAFT.

These builders LEAD with the authoritative verdict and DEMOTE the speculative
agent preamble below an explicit "the status above is authoritative" note, so a
draft/failed build can never headline a false success (the AIHUB-0021
honest-outcome rule).
"""

_AUTHORITATIVE_NOTE = ("\n\n---\n_Agent notes below may describe the intended workflow as if it "
                       "were finished — the validated status above is authoritative:_\n\n")


def _with_notes(message: str, agent_notes: str) -> str:
    if (agent_notes or "").strip():
        return message + _AUTHORITATIVE_NOTE + agent_notes
    return message


def draft_message(workflow_name, workflow_id, errors=None, agent_notes="", is_edit=False) -> str:
    """A workflow saved as a DRAFT (failed validation) — not ready to run."""
    verb = "updated" if is_edit else "created"
    err_lines = "\n".join(f"- {e}" for e in (errors or [])[:8]) or \
        "- (validation reported errors; see the workflow details)"
    body = (f"**⚠️ Workflow saved as a DRAFT — NOT {verb}/verified and not ready to run.**\n"
            f"- **Name:** {workflow_name}\n"
            f"- **ID:** {workflow_id}\n\n"
            f"It did NOT pass validation, so it is not usable yet. These issues must be fixed first:\n"
            f"{err_lines}\n\n"
            f"Tell me how you'd like to fix these and I'll update the workflow.")
    return _with_notes(body, agent_notes)


def error_message(error_text, agent_notes="", is_edit=False) -> str:
    """The build failed outright (compile error / exception) — nothing created."""
    verb = "updated" if is_edit else "created"
    body = (f"**❌ Workflow NOT {verb} — the build failed.**\n"
            f"{error_text or 'Unknown error'}\n\n"
            f"You may need to refine the requirements or try again.")
    return _with_notes(body, agent_notes)
