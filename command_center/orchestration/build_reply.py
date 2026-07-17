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


def dropped_capability(node_types, plan_text, nodes=None):
    """Return a label when the plan asked for an unsupported capability AND no
    persisted node covers it (i.e. it was silently dropped); else None. This is
    the confabulation case — the builder's own narration will claim it was built.

    AIHUB-0038 R2 (F1): coverage is EVIDENCE-based when per-node info is
    available (`nodes` = [{type, configured}], from the true read-back): a
    transfer-capable node counts ONLY if it is configured. A hollow placeholder
    (live: an 'Automation' node with empty data, no secret/host/path) must not
    suppress the drop disclosure. Without `nodes` (legacy event shape), fall
    back to type-name coverage."""
    label = unsupported_capability(plan_text)
    if not label:
        return None
    if nodes is not None:
        covered = any(
            (n.get("type") or "").strip().lower() in _TRANSFER_NODE_TYPES
            and bool(n.get("configured"))
            for n in nodes if isinstance(n, dict))
    else:
        covered = any((t or "").strip().lower() in _TRANSFER_NODE_TYPES for t in (node_types or []))
    return None if covered else label


def persisted_steps_block(workflow_id, status, node_types, plan_text="", nodes=None):
    """Build the AUTHORITATIVE, deterministic step block from the ACTUALLY-PERSISTED
    node types. Lists only real nodes; if the plan requested an unsupported
    capability that no persisted node covers, discloses it was NOT built and
    steers it to a Code Flow. This is prepended to the builder reply so the reply
    can never headline a step that isn't in the saved workflow.

    With per-node info (`nodes` = [{type, configured}], AIHUB-0038 R2), an
    UNCONFIGURED transfer-capable node is marked as a placeholder in the list,
    and coverage for the drop disclosure is evidence-based."""
    if nodes is not None:
        node_types = [n.get("type") for n in nodes
                      if isinstance(n, dict) and n.get("type")]
    node_types = [t for t in (node_types or []) if t]
    header_id = f" (ID {workflow_id})" if workflow_id else ""
    verb = "saved as a DRAFT (not yet runnable)" if status == "draft" else "saved"
    lines = [f"**Actual saved workflow{header_id} — {verb}. It contains exactly these "
             f"{len(node_types)} step(s):**"]
    if nodes is not None:
        for n in nodes:
            if not (isinstance(n, dict) and n.get("type")):
                continue
            t = n["type"]
            if (t or "").strip().lower() in _TRANSFER_NODE_TYPES and not n.get("configured"):
                lines.append(f"- {t} (UNCONFIGURED placeholder — carries no transfer "
                             f"settings and will not perform any upload)")
            else:
                lines.append(f"- {t}")
        if not node_types:
            lines.append("- (no nodes were persisted)")
    else:
        lines += [f"- {t}" for t in node_types] or ["- (no nodes were persisted)"]

    dropped = dropped_capability(node_types, plan_text, nodes=nodes)
    if dropped:
        lines.append(
            f"\n⚠️ The **{dropped}** you asked for is NOT in this workflow — the visual builder has "
            f"no node for it, so it was left out. This workflow is NOT a complete replacement for what "
            f"you asked for. Build the {dropped} part as a **Code Flow / Automation** (it can reference "
            f"the same connections and secrets by name).")
    lines.append("\n(Report ONLY the steps listed above as built — do not describe any step that is "
                 "not in this list, and do not call it 'verified' or 'ready' beyond this saved state.)")
    return "\n".join(lines)
