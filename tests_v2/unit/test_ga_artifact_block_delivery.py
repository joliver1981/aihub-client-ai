"""GA inline-plot delivery — deterministic side channel + echo strip.

run_python_code used to deliver its artifact/image blocks by asking the model
to echo a JSON array verbatim into the reply, which then had to survive the
smart-renderer's mini-LLM restructuring hop (two probabilistic echo steps).
2026-08-27: the tool now TEES the blocks into RichContentManager (the proven
create_excel_chart side channel, harvested deterministically by run()), and
run() strips any echoed JSON back out of the reply via
_extract_embedded_artifact_blocks, keeping the parsed blocks only as a
fallback for a failed tee.

These tests cover the two deterministic pieces in isolation (no LLM, no app):
the extractor and the side-channel ordering contract.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


# ---------------------------------------------------------------------------
# _extract_embedded_artifact_blocks — loaded by source extraction so the test
# does not import GeneralAgent's full dependency surface.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def extract():
    src = (REPO / "GeneralAgent.py").read_text(encoding="utf-8")
    m = re.search(
        r"def _extract_embedded_artifact_blocks\(text: str\):.*?"
        r"(?=\ndef _save_artifact_and_block)",
        src, re.DOTALL,
    )
    assert m, "_extract_embedded_artifact_blocks not found in GeneralAgent.py"
    ns: dict = {}
    exec(m.group(0), ns)  # noqa: S102 - own source, test-only
    return ns["_extract_embedded_artifact_blocks"]


ART = [
    {"type": "artifact", "name": "chart.png", "artifactType": "png",
     "size": "24 KB", "artifact_id": "abc-123", "download_url": "/files/abc-123"},
    {"type": "image", "content": "/files/abc-123",
     "metadata": {"caption": "chart.png"}},
]
JS = json.dumps(ART)


class TestExtractor:
    def test_bare_array_extracted_and_text_cleaned(self, extract):
        cleaned, blocks = extract(f"Here is the chart.\n\n{JS}")
        assert cleaned == "Here is the chart."
        assert blocks == ART

    def test_fenced_array_extracted_with_fence_removed(self, extract):
        cleaned, blocks = extract(f"Chart!\n\n```json\n{JS}\n```\n\nEnjoy.")
        assert "```" not in cleaned
        assert "Chart!" in cleaned and "Enjoy." in cleaned
        assert len(blocks) == 2

    def test_plain_prose_untouched(self, extract):
        t = "Total is 42. See [docs](http://x) for details."
        assert extract(t) == (t, [])

    def test_unrelated_json_array_left_in_place(self, extract):
        t = 'Sample rows: [{"region": "North", "rev": 5}].'
        assert extract(t) == (t, [])

    def test_non_artifact_block_types_left_in_place(self, extract):
        t = 'Blocks: [{"type": "table", "content": []}]'
        assert extract(t) == (t, [])

    def test_multiple_arrays_all_extracted(self, extract):
        t = f"a\n{JS}\nb\n{json.dumps([ART[0]])}\nc"
        cleaned, blocks = extract(t)
        assert len(blocks) == 3
        for frag in ("a", "b", "c"):
            assert frag in cleaned

    def test_markdown_link_brackets_do_not_confuse_scanner(self, extract):
        cleaned, blocks = extract(f"See [the chart](/f/x).\n{JS}\nDone.")
        assert len(blocks) == 2
        assert "[the chart](/f/x)" in cleaned and "Done." in cleaned

    def test_empty_input(self, extract):
        assert extract("") == ("", [])


# ---------------------------------------------------------------------------
# RichContentManager — sorted load so block_id ordering is honored (the tee
# writes runpy<timestamp><index> ids: image block first, then its card).
# ---------------------------------------------------------------------------

class TestSideChannelOrdering:
    def test_load_returns_blocks_in_block_id_order(self, tmp_path):
        from RichContentManager import RichContentManager
        mgr = RichContentManager(temp_dir=str(tmp_path))
        uid = "77"
        mgr.save({"type": "image", "content": "/f/1"}, uid,
                 block_id="runpy000000000100")
        mgr.save({"type": "artifact", "artifact_id": "a1"}, uid,
                 block_id="runpy000000000101")
        mgr.save({"type": "artifact", "artifact_id": "a0"}, uid,
                 block_id="runpy000000000002")
        blocks = mgr.load_all_and_delete(uid)
        assert [b.get("artifact_id") or b.get("content") for b in blocks] == \
            ["a0", "/f/1", "a1"]
        # and the files are consumed
        assert mgr.load_all_and_delete(uid) == []

    def test_users_do_not_cross(self, tmp_path):
        from RichContentManager import RichContentManager
        mgr = RichContentManager(temp_dir=str(tmp_path))
        mgr.save({"type": "image", "content": "/f/mine"}, "1", block_id="x")
        assert mgr.load_all_and_delete("2") == []
        assert len(mgr.load_all_and_delete("1")) == 1
