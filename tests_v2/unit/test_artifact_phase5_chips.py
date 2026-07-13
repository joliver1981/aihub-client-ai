"""Unit tests — artifact-sharing Phase 5 items 1 & 2 (chip survival).

P5-1 (backend, CC converse): renderable chips salvaged from MIXED tool output
so a delegated agent's [text, artifact] no longer gets paraphrased away; the
delegated data/general tools return real artifact blocks.

P5-2 (frontend, command-center.js): stored assistant messages that carry chips
render as blocks on history reload instead of being flattened to markdown; and
_unwrapJsonContent degrades a chip to a clickable link rather than dropping it.

The converse loop lives inside a huge closure that needs the CC service stack
to import, so the backend is covered by a salvage-logic double + source guards.
The frontend is exercised for REAL by loading command-center.js in Node.

NOTE: repo .gitignore ignores test*.py — commit with `git add -f`.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── P5-1: salvage/dedup logic double (mirrors nodes.py _salvage_chips) ────

_DIRECT = ("map", "artifact", "table", "image", "kpi", "action")


def _salvage(tool_result_strings):
    """Reimplements the converse salvage: pull renderable blocks out of each
    tool result (pure OR mixed arrays, single dicts), preserving order."""
    chips = []
    for content in tool_result_strings:
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict) and parsed.get("type") in _DIRECT:
            chips.append(parsed)
        elif isinstance(parsed, list):
            for b in parsed:
                if isinstance(b, dict) and b.get("type") in _DIRECT:
                    chips.append(b)
    # dedup by artifact_id/url (as the final-append does)
    seen, out = set(), []
    for c in chips:
        k = c.get("artifact_id") or c.get("url") or c.get("download_url") or id(c)
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def test_salvage_from_mixed_array():
    mixed = json.dumps([{"type": "text", "content": "Here are the results"},
                        {"type": "artifact", "artifact_id": "a1", "download_url": "/d/a1"}])
    chips = _salvage([mixed])
    assert len(chips) == 1 and chips[0]["artifact_id"] == "a1"


def test_salvage_ignores_pure_text():
    assert _salvage([json.dumps([{"type": "text", "content": "no chips"}])]) == []


def test_salvage_single_dict_and_dedup():
    single = json.dumps({"type": "artifact", "artifact_id": "z", "download_url": "/d/z"})
    dup = json.dumps([{"type": "text", "content": "x"},
                      {"type": "artifact", "artifact_id": "z", "download_url": "/d/z"}])
    chips = _salvage([single, dup])
    assert len(chips) == 1 and chips[0]["artifact_id"] == "z"


def test_salvage_preserves_action_and_table():
    mixed = json.dumps([{"type": "text", "content": "t"},
                        {"type": "table", "headers": ["a"], "rows": [[1]]},
                        {"type": "action", "url": "/go", "label": "Open"}])
    chips = _salvage([mixed])
    assert [c["type"] for c in chips] == ["table", "action"]


# ── P5-1 source guards (nodes.py needs the CC stack to import) ────────────

def _src(rel):
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_converse_salvages_and_appends_chips():
    s = _src("command_center_service/graph/nodes.py")
    assert "preserved_chips" in s
    assert "def _salvage_chips(" in s
    # mixed-array branch calls the salvage in both the first pass and round loop
    assert s.count("_salvage_chips(parsed)") >= 2
    # salvaged chips appended to the final text answer as blocks
    assert "if preserved_chips:" in s
    assert "AIMessage(content=json.dumps(_blocks))" in s


def test_delegated_tools_return_real_artifact_chips():
    s = _src("command_center_service/graph/nodes.py")
    # both delegated tools emit a mixed [text, artifact] JSON array when files exist
    assert s.count('dict(a, type="artifact") for a in artifacts') >= 2


# ── P5-2: exercise the REAL command-center.js functions in Node ──────────

_NODE = shutil.which("node")

_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
// Run the real command-center.js in an isolated VM context with DOM stubs.
const sandbox = {
  document: { addEventListener: () => {},
              getElementById: () => ({ appendChild(){}, scrollTop:0, scrollHeight:0, innerHTML:'' }),
              createElement: () => ({ className:'', innerHTML:'', textContent:'', style:{}, appendChild(){} }) },
  window: {}, marked: null,
  localStorage: { getItem(){return null;}, setItem(){}, removeItem(){} },
  CCRenderers: { renderBlocks(){} }, console,
};
vm.createContext(sandbox);
vm.runInContext(src + '\nthis.__CC = CC;', sandbox);
const CC = sandbox.__CC;
const fails = [];
const ok = (c, m) => { if (!c) fails.push(m); };

const mixed = JSON.stringify([{type:'text',content:'hi'},
  {type:'artifact',name:'x.csv',artifact_id:'abc',download_url:'/api/artifacts/abc/download'}]);

// 1. renderable-array detection
ok(Array.isArray(CC._tryRenderableBlocks(mixed)), 'detect mixed artifact array');
ok(CC._tryRenderableBlocks(JSON.stringify([{type:'text',content:'hi'}])) === null, 'no chips -> null');
ok(CC._tryRenderableBlocks('just text') === null, 'plain string -> null');

// 2. _addMessage routes chip arrays to _addRichMessage (never flattens)
let routed = null;
CC._addRichMessage = (b) => { routed = b; };
CC._addMessage('assistant', mixed);
ok(routed && routed.length === 2, '_addMessage routes chips to _addRichMessage');

// a pure-text assistant message must NOT route to blocks
routed = null;
CC._addMessage('assistant', 'plain answer');
ok(routed === null, 'plain text stays on markdown path');

// 3. _unwrapJsonContent degrades chips to links instead of dropping them
const md = CC._unwrapJsonContent(mixed);
ok(md.indexOf('](/api/artifacts/abc/download)') !== -1, 'artifact -> link in unwrap: ' + md);
ok(md.indexOf('hi') !== -1, 'keeps text in unwrap');

const md2 = CC._unwrapJsonContent(JSON.stringify([{type:'artifact',name:'only.csv',download_url:'/d2'}]));
ok(md2.indexOf('](/d2)') !== -1, 'pure-artifact unwrap -> link, not raw JSON: ' + md2);

if (fails.length) { console.error('FAILS:\n' + fails.join('\n')); process.exit(1); }
console.log('ALL_OK');
"""


@pytest.mark.skipif(_NODE is None, reason="node not available to exercise the JS")
def test_history_reload_preserves_chips_real_js(tmp_path):
    js = REPO_ROOT / "command_center_service" / "static" / "js" / "command-center.js"
    harness = tmp_path / "harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    r = subprocess.run([_NODE, str(harness), str(js)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"JS behavior test failed:\n{r.stdout}\n{r.stderr}"
    assert "ALL_OK" in r.stdout


def test_js_source_has_p52_guards():
    """Belt-and-suspenders if node is unavailable in CI."""
    s = _src("command_center_service/static/js/command-center.js")
    assert "_tryRenderableBlocks" in s
    assert "_RENDERABLE_BLOCK_TYPES" in s
    # unwrap degrades artifact/action to links
    assert "b.type === 'artifact'" in s and "b.download_url" in s
