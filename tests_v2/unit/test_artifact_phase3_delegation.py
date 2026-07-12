"""Unit tests — artifact-sharing Phase 3 (files across general-agent delegation).

Covers:
  * produced_sink contextvar capture semantics (active/inactive, isolation).
  * The create_* chokepoint captures bytes when a sink is active and reports
    success even with no active conversation (delegated run).
  * DOCX artifact type round-trips through the shared store.
  * app._register_delegated_artifacts: re-registers captured files into the
    shared store scoped to the CC session, returns chips, never raises.
  * Source-level wiring guards (route capture, delegator session_id, converse
    surfacing, aggregate preservation) — importing app.py needs the live DB.

NOTE: repo .gitignore ignores test*.py — commit with `git add -f`.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from command_center.artifacts import produced_sink
from command_center.artifacts.artifact_manager import ArtifactManager
from command_center.artifacts.artifact_models import ArtifactType


def _load_chokepoint():
    """Import GeneralAgent._save_artifact_and_block, or skip. GeneralAgent (via
    AppUtils) opens a DB connection at import time, so on a box without the test
    DB this must skip rather than error."""
    try:
        from GeneralAgent import _save_artifact_and_block
        return _save_artifact_and_block
    except Exception as e:  # pragma: no cover - env-dependent
        pytest.skip(f"GeneralAgent import unavailable (DB at import): {e}")


# ── produced_sink semantics ──────────────────────────────────────────────

def test_sink_inactive_by_default():
    # A fresh context has no sink; capture is a silent no-op.
    assert produced_sink.is_active() is False
    produced_sink.capture("x.csv", "csv", b"a,b\n")  # must not raise
    assert produced_sink.collected() == []


def test_sink_captures_when_active():
    token = produced_sink.begin_capture()
    try:
        assert produced_sink.is_active() is True
        produced_sink.capture("a.csv", "csv", b"1,2\n", source="q")
        produced_sink.capture("b.docx", "docx", b"PK...")
        got = produced_sink.collected()
        assert [g["name"] for g in got] == ["a.csv", "b.docx"]
        assert got[0]["type"] == "csv" and got[0]["bytes"] == b"1,2\n"
        assert got[0]["source"] == "q"
    finally:
        produced_sink.end_capture(token)
    # reset restores inactivity
    assert produced_sink.is_active() is False


def test_sink_end_capture_restores_previous():
    assert not produced_sink.is_active()
    tok = produced_sink.begin_capture()
    produced_sink.capture("f", "text", b"x")
    produced_sink.end_capture(tok)
    assert not produced_sink.is_active()
    assert produced_sink.collected() == []


# ── the create_* chokepoint (GeneralAgent._save_artifact_and_block) ──────

def test_chokepoint_captures_and_reports_success_without_conversation(monkeypatch):
    """A delegated run has no active conversation; with a sink active the
    chokepoint must capture the bytes AND report success (not the 'no active
    conversation' error) so the agent doesn't retry."""
    import json
    import active_chat_context
    _save_artifact_and_block = _load_chokepoint()

    monkeypatch.setattr(active_chat_context, "get_active_conversation_id", lambda: None)

    token = produced_sink.begin_capture()
    try:
        out = _save_artifact_and_block("report.csv", b"a,b\n1,2\n", "csv")
        parsed = json.loads(out)
        assert parsed[0]["type"] == "artifact"
        assert parsed[0]["delivered_via"] == "delegation"
        captured = produced_sink.collected()
        assert len(captured) == 1 and captured[0]["bytes"] == b"a,b\n1,2\n"
    finally:
        produced_sink.end_capture(token)


def test_chokepoint_without_sink_and_no_conversation_errors(monkeypatch):
    """Normal path unchanged: no sink + no conversation -> the original error."""
    import active_chat_context
    _save_artifact_and_block = _load_chokepoint()
    monkeypatch.setattr(active_chat_context, "get_active_conversation_id", lambda: None)
    out = _save_artifact_and_block("x.csv", b"1", "csv")
    assert "no active conversation" in out.lower()


# ── DOCX round-trips through the shared store ────────────────────────────

def test_docx_type_roundtrip(tmp_path):
    mgr = ArtifactManager(str(tmp_path))
    meta = mgr.create("memo.docx", ArtifactType.DOCX, b"PKzip", "7/s")
    assert meta.extension == ".docx"
    assert "wordprocessingml" in meta.mime_type
    assert mgr.get_file_path(meta.artifact_id).suffix == ".docx"


# ── _register_delegated_artifacts (imported lazily to avoid app import) ──

def _register(produced, agent_id, cc_session_id, user_id, mgr):
    """Re-implements the app helper's contract against an injected manager so
    we can unit-test the mapping without importing app.py (live DB at import).
    Kept in lockstep with app._register_delegated_artifacts via the source
    guard test below."""
    from command_center.artifacts.artifact_models import ArtifactType
    if not produced or not cc_session_id:
        return []
    scope = f"{user_id}/{cc_session_id}" if user_id is not None else str(cc_session_id)
    blocks = []
    for p in produced:
        try:
            atype = ArtifactType(p.get("type", "text"))
        except ValueError:
            atype = ArtifactType.TEXT
        meta = mgr.create(p.get("name", "file"), atype, p.get("bytes", b""), scope,
                          producing_agent=f"agent:{agent_id}", source=p.get("source"))
        blocks.append(meta.to_content_block())
    return blocks


def test_register_delegated_artifacts_scopes_and_maps(tmp_path):
    mgr = ArtifactManager(str(tmp_path))
    produced = [
        {"name": "out.csv", "type": "csv", "bytes": b"a\n1\n", "source": None},
        {"name": "memo.docx", "type": "docx", "bytes": b"PK", "source": None},
        {"name": "weird.bin", "type": "nonsense", "bytes": b"..", "source": None},  # -> TEXT
    ]
    blocks = _register(produced, 42, "sess-9", 7, mgr)
    assert len(blocks) == 3
    assert all(b["type"] == "artifact" for b in blocks)
    # scoped {user}/{session} so the CC download gate can enforce ownership
    for b in blocks:
        meta = mgr.get_metadata(b["artifact_id"])
        assert meta.session_id == "7/sess-9"
        assert meta.producing_agent == "agent:42"


def test_register_empty_or_no_session():
    from command_center.artifacts.artifact_manager import ArtifactManager
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        mgr = ArtifactManager(d)
        assert _register([], 1, "s", 1, mgr) == []
        assert _register([{"name": "x", "type": "csv", "bytes": b"1"}], 1, None, 1, mgr) == []


# ── wiring guards (importing app.py needs the DB) ────────────────────────

def _src(rel):
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_app_route_captures_and_registers():
    s = _src("app.py")
    assert "def _register_delegated_artifacts(" in s
    assert "produced_sink" in s and "begin_capture()" in s
    assert "'artifacts': artifacts or None" in s
    # capture only when a CC session is supplied (normal UI path untouched)
    assert "cc_session_id = data.get('session_id')" in s


def test_app_helper_matches_test_double():
    """Guard: the real helper must map unknown types to TEXT and scope by
    {user}/{session} exactly like the tested double above."""
    s = _src("app.py")
    assert "except ValueError:" in s and "ArtifactType.TEXT" in s
    assert '{caller_user_id}/{cc_session_id}' in s


def test_delegator_sends_session_id_for_general():
    s = _src("command_center/orchestration/delegator.py")
    assert 'payload["session_id"] = session_id' in s


def test_converse_and_aggregate_surface_general_artifacts():
    s = _src("command_center_service/graph/nodes.py")
    # converse general path lists produced files
    assert "File created:" in s
    # aggregate preserves the separate artifacts list as chips
    assert 'result.get("artifacts") or []' in s
    assert 'attached below, do NOT reproduce' in s
