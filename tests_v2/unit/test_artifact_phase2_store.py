"""Unit tests — artifact-sharing Phase 2 core (shared store as a library).

Covers:
  * resolve_shared_artifacts_dir: AIHUB_ARTIFACTS_DIR override + same-tree default.
  * The artifacts package imports WITHOUT the CC service stack (subprocess
    proof: no fastapi) — required for the main app (aihub2.1) to write to the
    shared store as a plain library.
  * create() with provenance/shape enrichment -> sidecar -> a SECOND manager
    instance (i.e., another process) sees the artifact and its metadata.
  * Sidecar backward compatibility (old sidecars without the new keys load).

NOTE: repo .gitignore ignores test*.py — commit with `git add -f`.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from command_center.artifacts.artifact_manager import (
    ArtifactManager,
    resolve_shared_artifacts_dir,
)
from command_center.artifacts.artifact_models import ArtifactMetadata, ArtifactType


# ── shared-dir resolution ────────────────────────────────────────────────

def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("AIHUB_ARTIFACTS_DIR", r"D:\shared\artifacts")
    assert resolve_shared_artifacts_dir() == r"D:\shared\artifacts"


def test_default_is_cc_service_data_dir(monkeypatch):
    monkeypatch.delenv("AIHUB_ARTIFACTS_DIR", raising=False)
    d = Path(resolve_shared_artifacts_dir())
    assert d.parts[-3:] == ("command_center_service", "data", "artifacts")
    # anchored at the repo root, so every process in this tree agrees
    assert d.parent.parent.parent == REPO_ROOT


def test_import_is_lightweight_no_fastapi():
    """The main app imports this as a library — it must not drag in the CC
    service stack (fastapi/uvicorn are absent from aihub2.1)."""
    code = (
        "import sys; "
        "import command_center.artifacts.artifact_manager; "
        "import command_center.artifacts.artifact_models; "
        "banned = [m for m in ('fastapi', 'uvicorn', 'langchain') if m in sys.modules]; "
        "sys.exit(1 if banned else 0)"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO_ROOT),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"artifacts package pulled in service deps: {r.stdout} {r.stderr}"


# ── create -> sidecar -> visible to a second instance ───────────────────

def test_create_roundtrip_with_enrichment(tmp_path):
    mgr = ArtifactManager(str(tmp_path))
    meta = mgr.create(
        "big_result",
        ArtifactType.CSV,
        b"id,val\n1,x\n2,y\n",
        session_id="7/sess-abc",
        producing_agent="data_agent:281",
        source="SELECT id, val FROM t",
        row_count=248391,
        columns=["id", "val"],
    )
    # file on disk under the scoped session dir
    fp = mgr.get_file_path(meta.artifact_id)
    assert fp is not None and fp.read_bytes().startswith(b"id,val")
    assert fp.parent == tmp_path / "7" / "sess-abc"

    # sidecar carries the enrichment
    sidecar = json.loads((fp.parent / f"{meta.artifact_id}.meta.json").read_text(encoding="utf-8"))
    assert sidecar["producing_agent"] == "data_agent:281"
    assert sidecar["source"] == "SELECT id, val FROM t"
    assert sidecar["row_count"] == 248391
    assert sidecar["columns"] == ["id", "val"]

    # a SECOND manager on the same folder (≈ another process) sees it
    mgr2 = ArtifactManager(str(tmp_path))
    meta2 = mgr2.get_metadata(meta.artifact_id)
    assert meta2 is not None
    assert meta2.row_count == 248391
    assert meta2.producing_agent == "data_agent:281"
    assert mgr2.get_file_path(meta.artifact_id) == fp

    # list_artifacts matches the bare session id against the scoped form
    listed = mgr2.list_artifacts("sess-abc")
    assert any(a["artifact_id"] == meta.artifact_id for a in listed)
    listed_entry = next(a for a in listed if a["artifact_id"] == meta.artifact_id)
    assert listed_entry.get("row_count") == 248391


def test_content_block_carries_row_count(tmp_path):
    mgr = ArtifactManager(str(tmp_path))
    meta = mgr.create("r", ArtifactType.CSV, b"a\n1\n", session_id="s", row_count=10)
    block = meta.to_content_block()
    assert block["type"] == "artifact"
    assert block["row_count"] == 10
    assert block["download_url"].endswith(f"/{meta.artifact_id}/download")


def test_old_sidecar_without_new_keys_still_loads():
    meta = ArtifactMetadata.from_persist({
        "artifact_id": "abc123",
        "name": "old.csv",
        "artifact_type": "csv",
        "size_bytes": 12,
        "session_id": "5/sess-1",
        "created_at": "2026-07-01T00:00:00",
    })
    assert meta.artifact_id == "abc123"
    assert meta.producing_agent is None
    assert meta.row_count is None
    assert meta.columns is None
    # and persisting it again does not invent the keys
    d = meta.persist_dict()
    assert "row_count" not in d and "producing_agent" not in d


def test_create_without_enrichment_unchanged(tmp_path):
    """Existing callers (export tool, run_python, portal fetch) pass only the
    original four args — behavior must be identical."""
    mgr = ArtifactManager(str(tmp_path))
    meta = mgr.create("plain", ArtifactType.TEXT, b"hello", "sess-x")
    assert meta.row_count is None
    d = meta.persist_dict()
    assert set(d.keys()) == {"artifact_id", "name", "artifact_type",
                             "size_bytes", "session_id", "created_at"}
