"""Unit tests — artifact-sharing Phase 1 (big results become CSV artifacts).

Covers command_center.artifacts.data_export:
  * threshold gating (single dataframe, multi_dataframe, non-dataframe types)
  * full-fidelity CSV round-trip (every row persisted, not the preview)
  * content block shape (artifact type, row_count, download_url, description)
  * provenance recorded in the sidecar (producing_agent, source, columns)
  * failure isolation — a persist error returns [] and never raises
  * config default + disable switch (threshold<=0)

Uses a real ArtifactManager over tmp_path (no CC service / no DB).

NOTE: repo .gitignore ignores test*.py — commit with `git add -f`.
"""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from command_center.artifacts.artifact_manager import ArtifactManager
from command_center.artifacts import data_export


@pytest.fixture
def mgr(tmp_path):
    return ArtifactManager(str(tmp_path))


def _df(n, cols=("id", "val")):
    return pd.DataFrame({c: list(range(n)) for c in cols})


# ── threshold gating ─────────────────────────────────────────────────────

def test_below_threshold_no_artifact(mgr):
    blocks = data_export.maybe_persist_result_artifacts(
        _df(100), "dataframe", "7/s", threshold=10000, manager=mgr)
    assert blocks == []


def test_above_threshold_persists(mgr):
    blocks = data_export.maybe_persist_result_artifacts(
        _df(25000), "dataframe", "7/s", name_hint="all orders",
        producing_agent="data_agent:281", source="SELECT * FROM orders",
        threshold=10000, manager=mgr)
    assert len(blocks) == 1
    b = blocks[0]
    assert b["type"] == "artifact"
    assert b["artifactType"] == "csv"
    assert b["row_count"] == 25000
    assert b["download_url"].endswith("/download")
    assert "25,000 rows" in b["description"]


def test_non_dataframe_answer_types_ignored(mgr):
    for atype, ans in [("string", "hello"), ("number", 42), ("chart", "<png>")]:
        assert data_export.maybe_persist_result_artifacts(
            ans, atype, "7/s", threshold=10, manager=mgr) == []


def test_multi_dataframe_each_over_threshold(mgr):
    answer = [_df(20000), _df(50), _df(30000)]  # only 1st and 3rd exceed
    blocks = data_export.maybe_persist_result_artifacts(
        answer, "multi_dataframe", "7/s", threshold=10000, manager=mgr)
    assert len(blocks) == 2
    assert {b["row_count"] for b in blocks} == {20000, 30000}


def test_threshold_disabled_returns_empty(mgr):
    blocks = data_export.maybe_persist_result_artifacts(
        _df(99999), "dataframe", "7/s", threshold=0, manager=mgr)
    assert blocks == []


# ── AIHUB-0023: reachability + observability of the skip paths ───────────

def test_default_threshold_sits_below_the_live_sql_cap():
    """The live NLQ engines cap SQL fetches at 10k rows; with the old 10000
    default the strict len>threshold gate could NEVER fire — Scenario A was
    unreachable at defaults. The default must stay strictly below the cap."""
    import re
    src = (REPO_ROOT / "config.py").read_text(encoding="utf-8")
    m = re.search(r"ARTIFACT_EXPORT_ROW_THRESHOLD = int\(os\.getenv\("
                  r"'ARTIFACT_EXPORT_ROW_THRESHOLD', '(\d+)'\)\)", src)
    assert m, "threshold default not found in config.py"
    assert int(m.group(1)) < 10000, (
        f"default {m.group(1)} >= the 10k SQL row cap — the export gate "
        f"(len > threshold) can never fire at defaults")


def test_below_threshold_skip_is_logged(mgr, caplog):
    """The e2e round failed on an unloaded .env threshold with NOTHING in the
    logs to say so. The skip line doubles as a one-probe check of the value
    the RUNNING process actually loaded."""
    import logging
    with caplog.at_level(logging.INFO, logger="command_center.artifacts.data_export"):
        data_export.maybe_persist_result_artifacts(
            _df(30), "dataframe", "7/s", threshold=25000, manager=mgr)
    assert any("below export threshold" in r.message
               and "rows=30" in r.message and "threshold=25000" in r.message
               for r in caplog.records)


def test_dataframe_typed_non_dataframe_answer_warns(mgr, caplog):
    """answer_type='dataframe' with a stringified answer is a shape anomaly
    (the export silently no-ops on it) — it must WARN, not vanish."""
    import logging
    with caplog.at_level(logging.WARNING, logger="command_center.artifacts.data_export"):
        blocks = data_export.maybe_persist_result_artifacts(
            "   id  val\n0   1    2", "dataframe", "7/s", threshold=10, manager=mgr)
    assert blocks == []
    assert any("shape anomaly" in r.message for r in caplog.records)


def test_default_threshold_from_config(mgr, monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "ARTIFACT_EXPORT_ROW_THRESHOLD", 100, raising=False)
    assert data_export.maybe_persist_result_artifacts(_df(50), "dataframe", "7/s", manager=mgr) == []
    assert len(data_export.maybe_persist_result_artifacts(_df(500), "dataframe", "7/s", manager=mgr)) == 1


# ── full-fidelity CSV round-trip ─────────────────────────────────────────

def test_full_result_persisted_not_preview(mgr):
    df = _df(12345)
    blocks = data_export.maybe_persist_result_artifacts(
        df, "dataframe", "9/sess", threshold=1000, manager=mgr)
    art_id = blocks[0]["artifact_id"]
    path = mgr.get_file_path(art_id)
    # every row is on disk (utf-8-sig BOM + header + 12345 data rows)
    back = pd.read_csv(path)
    assert len(back) == 12345
    assert list(back.columns) == ["id", "val"]


def test_sidecar_records_provenance(mgr):
    blocks = data_export.maybe_persist_result_artifacts(
        _df(20000), "dataframe", "3/abc", name_hint="quarterly revenue",
        producing_agent="data_agent:42", source="SELECT ...",
        threshold=1000, manager=mgr)
    meta = mgr.get_metadata(blocks[0]["artifact_id"])
    assert meta.producing_agent == "data_agent:42"
    assert meta.source == "SELECT ..."
    assert meta.columns == ["id", "val"]
    assert meta.session_id == "3/abc"


def test_source_is_truncated(mgr):
    huge = "SELECT " + "x," * 5000
    blocks = data_export.maybe_persist_result_artifacts(
        _df(20000), "dataframe", "s", source=huge, threshold=1000, manager=mgr)
    meta = mgr.get_metadata(blocks[0]["artifact_id"])
    assert len(meta.source) <= 4000


# ── failure isolation ────────────────────────────────────────────────────

def test_persist_failure_returns_empty(monkeypatch):
    class _BoomMgr:
        def create(self, *a, **k):
            raise RuntimeError("disk full")
    # must NOT raise — the data answer has to survive an artifact failure
    blocks = data_export.maybe_persist_result_artifacts(
        _df(20000), "dataframe", "s", threshold=1000, manager=_BoomMgr())
    assert blocks == []


def test_persist_dataframe_artifact_direct(mgr):
    block = data_export.persist_dataframe_artifact(
        _df(5), "s", name_hint="tiny")  # no threshold — always writes
    assert block is not None and block["row_count"] == 5


# ── wiring guards (source-level: importing the endpoint/nodes pulls the DB
#    connection / CC service stack, so assert the glue exists textually) ───

def _src(rel):
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_internal_query_endpoint_persists_and_returns_artifacts():
    s = _src("routes/data_explorer.py")
    assert "maybe_persist_result_artifacts" in s
    assert '"artifacts": artifacts or None' in s
    # scope must carry the caller user id so the CC download gate can own it
    assert "caller_user_id" in s and "{caller_user_id}/{caller_session_id}" in s


def test_delegator_forwards_artifacts():
    s = _src("command_center/orchestration/delegator.py")
    assert 'data.get("artifacts")' in s
    assert 'result["artifacts"]' in s


def test_gather_data_builds_artifact_chips():
    s = _src("command_center_service/graph/nodes.py")
    # _build_response_blocks appends the artifact chip + preview note
    assert 'result.get("artifacts")' in s
    assert 'art_block["type"] = "artifact"' in s
    # converse path informs the user of the download
    assert "available to download" in s
    # delegated user_context now flows so artifacts are owner-scoped
    assert 'user_context=state.get("user_context")' in s


def test_renderer_shows_preview_banner():
    s = _src("command_center_service/static/js/cc-renderers.js")
    assert "cc-table-preview-banner" in s
    assert "block.truncated" in s
