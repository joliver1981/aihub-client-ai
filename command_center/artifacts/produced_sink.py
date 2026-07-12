"""Capture files a delegated agent produces, so the orchestrator can collect them.

When the Command Center delegates to a general agent, the agent's create_csv /
create_excel / create_word_doc / create_text_file tools write files into the
main app's per-conversation store — which the CC service can't reach, and which
never comes back through the text-only /api/agents/<id>/chat reply. This
contextvar sink lets the delegated chat route wrap agent_instance.run(): it
begins a capture, the create_* chokepoint records each produced file's bytes
into it, and the route then re-registers those bytes into the SHARED artifact
store (scoped to the CC session) and returns them as handles.

Contextvar-based so it is per-request and thread/async safe. When no capture is
active (the normal agent-UI path), capture() is a no-op and nothing changes.
See docs/agent-artifact-sharing-plan.md Phase 3.
"""
from contextvars import ContextVar
from typing import List, Optional

_sink: ContextVar[Optional[list]] = ContextVar("cc_produced_artifact_sink", default=None)


def begin_capture():
    """Start capturing produced artifacts for this context. Returns a token to
    pass to end_capture()."""
    return _sink.set([])


def end_capture(token) -> None:
    _sink.reset(token)


def is_active() -> bool:
    return _sink.get() is not None


def capture(name: str, artifact_type: str, content_bytes: bytes, source: Optional[str] = None) -> None:
    """Record one produced file. No-op when no capture is active."""
    lst = _sink.get()
    if lst is None:
        return
    lst.append({
        "name": name,
        "type": artifact_type,
        "bytes": content_bytes,
        "source": source,
    })


def collected() -> List[dict]:
    """Return the artifacts captured so far (empty list if none / inactive)."""
    return list(_sink.get() or [])
