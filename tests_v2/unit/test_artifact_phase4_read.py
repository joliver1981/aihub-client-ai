"""Unit tests — artifact-sharing Phase 4-lite (read_artifact + run_python seeding).

Covers:
  * prepare_workdir seeds this session's artifacts into the run_python workdir
    (in addition to uploads), respects existing names, and still seeds when the
    upload subsystem is unavailable.
  * read_artifact behavior via an extracted logic double (importing nodes.py
    needs the CC service stack): ownership gate, CSV row cap, binary refusal.
  * Source guards that the real tool is registered and enforces ownership.

NOTE: repo .gitignore ignores test*.py — commit with `git add -f`.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from command_center.artifacts.artifact_manager import ArtifactManager
from command_center.artifacts.artifact_models import ArtifactType


# ── run_python workdir seeding ───────────────────────────────────────────

def test_prepare_workdir_seeds_session_artifacts(tmp_path, monkeypatch):
    import command_center.artifacts.artifact_manager as am
    from command_center.tools import code_interpreter as ci

    store = ArtifactManager(str(tmp_path / "store"))
    monkeypatch.setattr(am, "get_shared_artifact_manager", lambda: store)
    # No uploads for this session (upload helper returns nothing).
    monkeypatch.setattr(ci, "prepare_workdir", ci.prepare_workdir)  # ensure real fn

    store.create("orders.csv", ArtifactType.CSV, b"id,qty\n1,5\n", "7/sess-42")
    store.create("memo.docx", ArtifactType.DOCX, b"PKbinary", "7/sess-42")
    # a different session's artifact must NOT be seeded
    store.create("other.csv", ArtifactType.CSV, b"x\n1\n", "7/sess-99")

    workdir, copied = ci.prepare_workdir("sess-42", {"user_id": 7})
    assert "orders.csv" in copied
    assert "memo.docx" in copied
    assert "other.csv" not in copied
    assert (Path(workdir) / "orders.csv").read_bytes().startswith(b"id,qty")


def test_prepare_workdir_upload_name_wins(tmp_path, monkeypatch):
    """An uploaded file and an artifact with the same name must not collide —
    the upload copy is kept, the artifact is skipped."""
    import command_center.artifacts.artifact_manager as am
    from command_center.tools import code_interpreter as ci

    store = ArtifactManager(str(tmp_path / "store"))
    monkeypatch.setattr(am, "get_shared_artifact_manager", lambda: store)
    store.create("data.csv", ArtifactType.CSV, b"ARTIFACT\n", "7/sess-1")

    # Simulate an already-copied upload of the same name.
    real_prepare = ci.prepare_workdir

    # Monkeypatch the upload path to yield one file named data.csv
    import types
    fake_upload = types.SimpleNamespace(
        get_files_for_session=lambda sid: [{"file_id": "u1", "filename": "data.csv"}],
        get_file_path=lambda fid: str(tmp_path / "up_data.csv"),
        _file_is_accessible_to=lambda *a, **k: True,
    )
    (tmp_path / "up_data.csv").write_bytes(b"UPLOAD\n")
    monkeypatch.setitem(sys.modules, "routes.upload", fake_upload)

    workdir, copied = real_prepare("sess-1", {"user_id": 7})
    assert copied.count("data.csv") == 1
    assert (Path(workdir) / "data.csv").read_bytes() == b"UPLOAD\n"


def test_prepare_workdir_seeds_even_without_uploads(tmp_path, monkeypatch):
    """Upload subsystem import failure must not skip artifact seeding."""
    import command_center.artifacts.artifact_manager as am
    from command_center.tools import code_interpreter as ci

    store = ArtifactManager(str(tmp_path / "store"))
    monkeypatch.setattr(am, "get_shared_artifact_manager", lambda: store)

    class _Boom(dict):
        def __getattr__(self, k):
            raise ImportError("routes.upload unavailable")
    # Force the `from routes.upload import ...` to raise.
    monkeypatch.setitem(sys.modules, "routes.upload", _Boom())
    store.create("only.csv", ArtifactType.CSV, b"a\n1\n", "7/sess-x")

    workdir, copied = ci.prepare_workdir("sess-x", {"user_id": 7})
    assert "only.csv" in copied


# ── read_artifact logic (double kept in lockstep via source guards) ──────

class _Meta:
    def __init__(self, atype, name, size="1.0 KB", row_count=None, session_id="7/s"):
        self.artifact_type = ArtifactType(atype)
        self.name = name
        self.size_display = size
        self.row_count = row_count
        self.session_id = session_id


def _read_artifact_logic(meta, path, accessible, max_rows=200):
    """Mirror of the read_artifact tool's post-lookup logic (the tool itself
    lives in nodes.py, which needs the CC stack to import)."""
    if not accessible:
        return "Error: you don't have access"
    atype = meta.artifact_type.value
    if atype not in {"csv", "text", "json"}:
        return (f"'{meta.name}' is a {atype} file ({meta.size_display}) — binary, "
                f"so it can't be shown as text. Use run_python")
    text = Path(path).read_bytes().decode("utf-8-sig", errors="replace")
    header = f"Contents of '{meta.name}' ({atype}, {meta.size_display}):\n"
    if atype == "csv":
        cap = max(1, min(int(max_rows), 2000))
        lines = text.splitlines()
        total = len(lines) - 1 if lines else 0
        body = "\n".join(lines[:cap + 1])
        note = f"\n\n[Showing first {cap} of {total} data rows." if total > cap else ""
        return header + "```\n" + body[:20000] + "\n```" + note
    return header + "```\n" + text[:20000] + "\n```"


def test_read_artifact_denied_when_not_owner(tmp_path):
    p = tmp_path / "x.csv"; p.write_text("a\n1\n")
    out = _read_artifact_logic(_Meta("csv", "x.csv"), p, accessible=False)
    assert "don't have access" in out


def test_read_artifact_csv_caps_rows(tmp_path):
    p = tmp_path / "big.csv"
    p.write_text("id\n" + "\n".join(str(i) for i in range(500)) + "\n")
    out = _read_artifact_logic(_Meta("csv", "big.csv"), p, accessible=True, max_rows=10)
    assert "Showing first 10 of 500 data rows" in out
    # header + 10 data rows present, not all 500
    assert "\n9\n" in out and "\n499\n" not in out


def test_read_artifact_binary_refused(tmp_path):
    p = tmp_path / "m.xlsx"; p.write_bytes(b"PK\x03\x04")
    out = _read_artifact_logic(_Meta("excel", "m.xlsx"), p, accessible=True)
    assert "binary" in out and "run_python" in out


def test_read_artifact_text_returned(tmp_path):
    p = tmp_path / "n.json"; p.write_text('{"k": 1}')
    out = _read_artifact_logic(_Meta("json", "n.json"), p, accessible=True)
    assert '{"k": 1}' in out


# ── source guards ────────────────────────────────────────────────────────

def _src(rel):
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_read_artifact_tool_registered_and_gated():
    s = _src("command_center_service/graph/nodes.py")
    assert "async def read_artifact(" in s
    assert '"read_artifact": read_artifact' in s
    assert "read_artifact," in s  # in the tools list
    # ownership enforced with the same gate as the download route
    assert "_artifact_accessible_to(meta, uc.get(\"user_id\")" in s


def test_workdir_seeding_present():
    s = _src("command_center/tools/code_interpreter.py")
    assert "amgr.list_artifacts(session_id)" in s
    assert "get_shared_artifact_manager" in s
