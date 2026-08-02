"""Tests for command_center.artifacts.delegated_capture.

The delegated-artifact path has to work in BOTH deployment modes:
  * in-process (USE_AGENT_API=False) - the local produced_sink capture catches it;
  * across the agent-API HTTP hop (the default) - the ContextVar cannot reach the
    tool, so the far side registers into the shared store and the orchestrator
    discovers the files by diffing that store.

These tests pin the store-diff behaviour, which is what makes the cross-process
case work, and the de-duplication that keeps a file from being reported twice.
"""

import pytest

from command_center.artifacts import delegated_capture as dc
from command_center.artifacts import produced_sink
from command_center.artifacts.artifact_manager import ArtifactManager
from command_center.artifacts.artifact_models import ArtifactType


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the shared store at a temp dir. delegated_capture imports
    get_shared_artifact_manager lazily inside each function, so patching the
    source module is enough."""
    mgr = ArtifactManager(str(tmp_path))
    import command_center.artifacts.artifact_manager as am
    monkeypatch.setattr(am, "get_shared_artifact_manager", lambda: mgr)
    return mgr


class TestScope:
    def test_scope_includes_user_when_known(self):
        assert dc.scope_for("sess-1", 7) == "7/sess-1"

    def test_scope_falls_back_to_bare_session(self):
        assert dc.scope_for("sess-1", None) == "sess-1"


class TestBeginCollect:
    def test_begin_returns_none_without_a_session(self):
        assert dc.begin(None) is None
        assert dc.begin("") is None

    def test_begin_activates_the_sink_and_collect_ends_it(self):
        token = dc.begin("sess-1")
        assert token is not None
        assert produced_sink.is_active() is True
        produced_sink.capture("a.csv", "csv", b"x,y\n")
        got = dc.collect(token)
        assert [g["name"] for g in got] == ["a.csv"]
        assert produced_sink.is_active() is False

    def test_collect_of_none_token_is_empty_and_safe(self):
        assert dc.collect(None) == []


class TestRegister:
    def test_registers_and_returns_blocks(self, store):
        blocks = dc.register(
            [{"name": "r.csv", "type": "csv", "bytes": b"a,b\n1,2\n", "source": "email_attachment"}],
            agent_id=31, cc_session_id="sess-1", caller_user_id=7)
        assert len(blocks) == 1
        assert blocks[0]["name"] == "r.csv"
        assert blocks[0]["artifactType"] == "csv"
        assert blocks[0]["artifact_id"]

    def test_preserves_bytes_exactly(self, store):
        raw = b"%PDF-1.4 binary \x00\xff bytes"
        blocks = dc.register([{"name": "s.pdf", "type": "pdf", "bytes": raw}],
                             agent_id=1, cc_session_id="sess-2", caller_user_id=7)
        path = store.get_file_path(blocks[0]["artifact_id"])
        assert path.read_bytes() == raw

    def test_unknown_type_degrades_to_text(self, store):
        blocks = dc.register([{"name": "x.weird", "type": "nonsense", "bytes": b"hi"}],
                             agent_id=1, cc_session_id="sess-3", caller_user_id=7)
        assert blocks[0]["artifactType"] == ArtifactType.TEXT.value

    def test_nothing_produced_is_empty(self, store):
        assert dc.register([], 1, "sess-1", 7) == []
        assert dc.register(None, 1, "sess-1", 7) == []

    def test_no_session_means_no_registration(self, store):
        assert dc.register([{"name": "a.csv", "type": "csv", "bytes": b"x"}], 1, None, 7) == []


class TestStoreDiff:
    """This is the cross-process mechanism: the agent-API service registers into
    the shared store, and the orchestrator finds the files by diffing it."""

    def test_finds_artifacts_registered_by_another_process(self, store):
        before = dc.snapshot_ids("sess-x", 7)
        assert before == set()

        # Stand-in for the agent-API service writing on its own side of the hop.
        dc.register([{"name": "remote.csv", "type": "csv", "bytes": b"a\n1\n"}],
                    agent_id=31, cc_session_id="sess-x", caller_user_id=7)

        found = dc.new_blocks_since(before, "sess-x", 7)
        assert [b["name"] for b in found] == ["remote.csv"]

    def test_ignores_artifacts_that_predate_the_run(self, store):
        dc.register([{"name": "old.csv", "type": "csv", "bytes": b"a\n"}],
                    agent_id=31, cc_session_id="sess-y", caller_user_id=7)
        before = dc.snapshot_ids("sess-y", 7)

        dc.register([{"name": "new.csv", "type": "csv", "bytes": b"b\n"}],
                    agent_id=31, cc_session_id="sess-y", caller_user_id=7)

        found = dc.new_blocks_since(before, "sess-y", 7)
        assert [b["name"] for b in found] == ["new.csv"]

    def test_other_sessions_are_not_picked_up(self, store):
        before = dc.snapshot_ids("sess-mine", 7)
        dc.register([{"name": "theirs.csv", "type": "csv", "bytes": b"a\n"}],
                    agent_id=31, cc_session_id="sess-theirs", caller_user_id=7)
        assert dc.new_blocks_since(before, "sess-mine", 7) == []

    def test_no_session_returns_nothing(self, store):
        assert dc.snapshot_ids(None, 7) == set()
        assert dc.new_blocks_since(set(), None, 7) == []


class TestMergeBlocks:
    def test_dedupes_by_artifact_id(self):
        a = {"artifact_id": "1", "name": "a.csv"}
        b = {"artifact_id": "2", "name": "b.csv"}
        assert dc.merge_blocks([a, b], [a]) == [a, b]

    def test_preserves_order_and_handles_empties(self):
        a = {"artifact_id": "1", "name": "a"}
        b = {"artifact_id": "2", "name": "b"}
        assert dc.merge_blocks([a], None, [], [b]) == [a, b]

    def test_blocks_without_ids_are_kept(self):
        x = {"name": "no-id"}
        assert dc.merge_blocks([x], [x]) == [x, x]


class TestBothModesTogether:
    """A single run must not double-report when both mechanisms see the file."""

    def test_local_capture_and_store_diff_report_once(self, store):
        before = dc.snapshot_ids("sess-z", 7)
        token = dc.begin("sess-z")
        produced_sink.capture("dual.csv", "csv", b"a\n1\n")
        local = dc.finish(token, 31, "sess-z", 7)
        remote = dc.new_blocks_since(before, "sess-z", 7)

        merged = dc.merge_blocks(local, remote)
        assert len(local) == 1 and len(remote) == 1
        assert len(merged) == 1, "the same artifact must not be reported twice"
