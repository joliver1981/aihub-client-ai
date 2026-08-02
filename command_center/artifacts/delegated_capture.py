"""One implementation of the delegated-agent artifact capture, shared by every
service that can host a delegated agent run.

WHY THIS EXISTS. A delegated run executes in one of two processes:

  * in-process — when ``USE_AGENT_API=False``, app.py holds a real GeneralAgent;
  * in the agent-API service — when ``USE_AGENT_API=True`` (the shipped default),
    app.py holds an ``AgentAPIAdapter``, which is an HTTP proxy.

``produced_sink`` is a ContextVar, so a capture only ever sees tool calls made in
the SAME process that started it. The original wiring called begin_capture() in
app.py only, which meant that under the default USE_AGENT_API=True the agent and
all its tools ran on the far side of an HTTP hop and the sink was permanently
inactive — every delegated file was silently dropped.

The fix is symmetric: BOTH services call begin()/finish() around their own run,
and the orchestrating side additionally diffs the shared artifact store
(snapshot_ids + new_blocks_since) to pick up whatever the far side registered.
The store is a shared folder whose metadata is read back from on-disk sidecars,
so cross-process discovery works without shipping bytes over HTTP — which
matters, since a 50MB attachment would be ~67MB once base64'd.

Whichever process produced the files, the caller ends up with the same content
blocks. merge_blocks() de-duplicates by artifact_id so a run that somehow
registers on both sides reports each file once.
"""

import logging
from typing import Any, Dict, Iterable, List, Optional, Set

logger = logging.getLogger(__name__)


def scope_for(cc_session_id: Any, caller_user_id: Any = None) -> str:
    """Storage scope for a delegated run. Mirrors the export tool's layout so
    ArtifactManager.list_artifacts finds these by bare session id too."""
    if caller_user_id is not None:
        return f"{caller_user_id}/{cc_session_id}"
    return str(cc_session_id)


def begin(cc_session_id: Any):
    """Start capturing files produced in THIS process. Returns an opaque token,
    or None when this is not a delegated run (or the sink is unavailable)."""
    if not cc_session_id:
        return None
    try:
        from . import produced_sink
        return produced_sink.begin_capture()
    except Exception as e:
        logger.warning(f"[delegated_capture] sink unavailable, not capturing: {e}")
        return None


def collect(token) -> List[Dict[str, Any]]:
    """End the capture and return the raw produced entries. Never raises."""
    if token is None:
        return []
    try:
        from . import produced_sink
        produced = produced_sink.collected()
        produced_sink.end_capture(token)
        return produced
    except Exception as e:
        logger.warning(f"[delegated_capture] could not collect produced files: {e}")
        return []


def register(produced: Iterable[Dict[str, Any]], agent_id: Any,
             cc_session_id: Any, caller_user_id: Any = None) -> List[Dict[str, Any]]:
    """Write produced files into the SHARED artifact store, scoped to the
    delegating session. Returns content blocks (empty on nothing/failure)."""
    produced = list(produced or [])
    if not produced or not cc_session_id:
        return []
    try:
        from .artifact_manager import get_shared_artifact_manager
        from .artifact_models import ArtifactType
    except Exception as e:
        logger.warning(f"[delegated_capture] artifact store unavailable: {e}")
        return []

    mgr = get_shared_artifact_manager()
    scope = scope_for(cc_session_id, caller_user_id)
    blocks = []
    for p in produced:
        try:
            try:
                atype = ArtifactType(p.get("type", "text"))
            except ValueError:
                atype = ArtifactType.TEXT
            meta = mgr.create(
                p.get("name", "file"),
                atype,
                p.get("bytes", b""),
                scope,
                producing_agent=f"agent:{agent_id}",
                source=p.get("source"),
            )
            blocks.append(meta.to_content_block())
        except Exception as e:
            logger.warning(f"[delegated_capture] could not register artifact: {e}")
    if blocks:
        logger.info(f"[delegated_capture] registered {len(blocks)} artifact(s) "
                    f"for agent {agent_id} session {cc_session_id}")
    return blocks


def finish(token, agent_id: Any, cc_session_id: Any,
           caller_user_id: Any = None) -> List[Dict[str, Any]]:
    """End the capture started by begin() and register whatever it caught."""
    return register(collect(token), agent_id, cc_session_id, caller_user_id)


def snapshot_ids(cc_session_id: Any, caller_user_id: Any = None) -> Set[str]:
    """Artifact ids already in the store for this run, BEFORE it starts.

    Used to attribute only genuinely new files to the run — a session can
    legitimately already hold artifacts from earlier turns.
    """
    if not cc_session_id:
        return set()
    try:
        from .artifact_manager import get_shared_artifact_manager
        mgr = get_shared_artifact_manager()
        return {a.get("artifact_id") for a in mgr.list_artifacts(str(cc_session_id))
                if a.get("artifact_id")}
    except Exception as e:
        logger.warning(f"[delegated_capture] could not snapshot store: {e}")
        return set()


def new_blocks_since(before_ids: Optional[Set[str]], cc_session_id: Any,
                     caller_user_id: Any = None) -> List[Dict[str, Any]]:
    """Content blocks for artifacts that appeared since snapshot_ids().

    This is how the orchestrating process discovers files registered by a
    DIFFERENT process (the agent-API service), where a ContextVar cannot reach.
    """
    if not cc_session_id:
        return []
    before = before_ids or set()
    try:
        from .artifact_manager import get_shared_artifact_manager
        mgr = get_shared_artifact_manager()
        blocks = []
        for a in mgr.list_artifacts(str(cc_session_id)):
            aid = a.get("artifact_id")
            if not aid or aid in before:
                continue
            meta = mgr.get_metadata(aid)
            if meta is not None:
                blocks.append(meta.to_content_block())
        return blocks
    except Exception as e:
        logger.warning(f"[delegated_capture] could not diff store: {e}")
        return []


def merge_blocks(*block_lists: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Union of content blocks, de-duplicated by artifact_id, order preserved."""
    seen = set()
    merged = []
    for blocks in block_lists:
        for b in (blocks or []):
            aid = b.get("artifact_id")
            if aid and aid in seen:
                continue
            if aid:
                seen.add(aid)
            merged.append(b)
    return merged
