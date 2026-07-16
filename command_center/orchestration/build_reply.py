"""
Honest build-reply helpers for the Workflow Builder delegation (AIHUB-0034).

The user-facing build reply is recomposed by the CC's follow-up LLM, seeded by
the LLM *plan* — which lists steps the user ASKED for (e.g. an SFTP upload) even
when the visual builder has no node for them and never compiled them. That is
how a workflow whose persisted nodes are only Database/Set Variable/File gets
reported as "✅ created / Verified: SFTP upload".

Fix (arbiter directive): build the reply's step list from the ACTUALLY-PERSISTED
nodes (a read-back emitted by the builder), so a step that isn't in the saved
`workflow_data.nodes` can never be listed as built. Dependency-free + pure, so
it is unit-testable and used verbatim (not LLM-narrated).
"""
import re as _re

# Capabilities the visual builder has NO node for — if the plan asked for one and
# no persisted node covers it, it was dropped and must go to a Code Flow.
_UNSUPPORTED = [
    (_re.compile(r"\b(sftp|ftps?)\b", _re.I), "SFTP/FTP file transfer"),
    (_re.compile(r"\bupload\b[^.\n]{0,40}\b(server|remote|sftp|ftp|host|bucket|s3)\b", _re.I),
     "remote upload/transfer"),
    (_re.compile(r"\brun\b[^.\n]{0,20}\b(code|script|python)\b|\bcustom\s+code\b", _re.I),
     "custom code execution"),
]

# Persisted node types that DO represent a transfer/upload capability (none today,
# but future-proof: if such a node type is ever added, don't flag a false drop).
_TRANSFER_NODE_TYPES = {"sftp", "ftp", "file transfer", "upload", "code step", "automation"}


def unsupported_capability(text):
    """Return a label if `text` (a plan/step description) asks for a capability
    the visual builder has no node for; else None."""
    t = text or ""
    for pat, label in _UNSUPPORTED:
        if pat.search(t):
            return label
    return None


def dropped_capability(node_types, plan_text):
    """Return a label when the plan asked for an unsupported capability AND no
    persisted node covers it (i.e. it was silently dropped); else None. This is
    the confabulation case — the builder's own narration will claim it was built."""
    label = unsupported_capability(plan_text)
    if not label:
        return None
    covered = any((t or "").strip().lower() in _TRANSFER_NODE_TYPES for t in (node_types or []))
    return None if covered else label


def persisted_steps_block(workflow_id, status, node_types, plan_text=""):
    """Build the AUTHORITATIVE, deterministic step block from the ACTUALLY-PERSISTED
    node types. Lists only real nodes; if the plan requested an unsupported
    capability that no persisted node covers, discloses it was NOT built and
    steers it to a Code Flow. This is prepended to the builder reply so the reply
    can never headline a step that isn't in the saved workflow."""
    node_types = [t for t in (node_types or []) if t]
    header_id = f" (ID {workflow_id})" if workflow_id else ""
    verb = "saved as a DRAFT (not yet runnable)" if status == "draft" else "saved"
    lines = [f"**Actual saved workflow{header_id} — {verb}. It contains exactly these "
             f"{len(node_types)} step(s):**"]
    lines += [f"- {t}" for t in node_types] or ["- (no nodes were persisted)"]

    dropped = dropped_capability(node_types, plan_text)
    if dropped:
        lines.append(
            f"\n⚠️ The **{dropped}** you asked for is NOT in this workflow — the visual builder has "
            f"no node for it, so it was left out. This workflow is NOT a complete replacement for what "
            f"you asked for. Build the {dropped} part as a **Code Flow / Automation** (it can reference "
            f"the same connections and secrets by name).")
    lines.append("\n(Report ONLY the steps listed above as built — do not describe any step that is "
                 "not in this list, and do not call it 'verified' or 'ready' beyond this saved state.)")
    return "\n".join(lines)
