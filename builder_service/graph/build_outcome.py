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

import re as _re

_AUTHORITATIVE_NOTE = ("\n\n---\n_Agent notes below may describe the intended workflow as if it "
                       "were finished — the validated status above is authoritative:_\n\n")

# Capabilities the VISUAL workflow builder has NO node for. When the user asks
# for one of these, the builder cannot build it — it must be a Code Flow. If the
# builder "succeeds" anyway it has silently dropped the step (AIHUB-0034 F2c/F4c:
# the SFTP upload was dropped and confabulated as a verified step).
_UNSUPPORTED_PATTERNS = [
    (_re.compile(r"\b(sftp|ftps?)\b", _re.I), "SFTP/FTP file transfer"),
    (_re.compile(r"\bupload\b[^.\n]{0,40}\b(server|remote|sftp|ftp|host|bucket|s3)\b", _re.I),
     "upload/transfer to a remote server"),
    (_re.compile(r"\brun\b[^.\n]{0,20}\b(code|script|python)\b|\bcustom\s+code\b", _re.I),
     "custom code execution"),
]


def detect_unsupported_capability(text):
    """Return a short label if the text requests a capability the visual builder
    has NO node for (SFTP/FTP transfer, remote upload, custom code); else None."""
    t = text or ""
    for pat, label in _UNSUPPORTED_PATTERNS:
        if pat.search(t):
            return label
    return None


def success_with_dropped_step_message(workflow_name, workflow_id, node_count, dropped, is_edit=False):
    """Honest SUCCESS reply when a requested capability had no node and was left
    out — credit what WAS built, disclose what was NOT, steer to a Code Flow.
    Deterministic so the reply cannot confabulate the dropped step as 'verified'."""
    verb = "updated" if is_edit else "created"
    name = f' "{workflow_name}"' if workflow_name else ""
    return (f"**✅ Workflow{name} {verb} (ID {workflow_id}, {node_count} node(s)) — but it does NOT "
            f"include the {dropped} you asked for.**\n\n"
            f"The visual workflow builder has no node for that, so that step was left out — the rest "
            f"(e.g. the query and a local export) was built. It is NOT a complete replacement for what "
            f"you asked for.\n\n"
            f"To do the {dropped} part, build it as a **Code Flow / Automation** — it can reference the "
            f"same connections and secrets by name. Want me to hand that part off to a Code Flow?")


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
