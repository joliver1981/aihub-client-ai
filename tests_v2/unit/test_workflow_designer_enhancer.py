"""The workflow designer adds a "variables" button to text boxes in the node
dialogs (static/js/workflow.js, enhanceConfigTemplates).

Until 2026-11-24 the textarea rule wrapped only the textarea's OPENING tag, so
the button and the wrapper's closing tag ended up inside the textarea as its
text and the wrapper stayed open: every field after it was nested one level
deeper (in the Loop dialog that hid Output Mode), and the button markup could
be saved as the field's value.

These tests run the real function from workflow.js in Node. They are skipped
on a machine without Node.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WORKFLOW_JS = Path(__file__).resolve().parents[2] / "static" / "js" / "workflow.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="Node is not installed on this machine")

BUTTON = ('<button type="button" class="btn btn-outline-secondary" '
          'onclick="showVariableSelector(this)"><i class="bi bi-braces"></i></button>')


def _enhancer_source():
    src = WORKFLOW_JS.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    found = re.search(r"^function enhanceConfigTemplates\(\) \{\n.*?^\}$", src, re.S | re.M)
    assert found, "enhanceConfigTemplates() not found in workflow.js"
    return found.group(0)


def _enhance(tmp_path, templates):
    """{type: html} -> {type: html after enhanceConfigTemplates()}"""
    data = tmp_path / "templates.json"
    data.write_text(json.dumps({k: {"template": v} for k, v in templates.items()}), encoding="utf-8")
    script = tmp_path / "run.js"
    script.write_text(
        "const fs = require('fs');\n"
        "const nodeConfigTemplates = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));\n"
        + _enhancer_source() + "\n"
        "enhanceConfigTemplates();\n"
        "process.stdout.write(JSON.stringify(nodeConfigTemplates));\n",
        encoding="utf-8")
    done = subprocess.run([NODE, str(script), str(data)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return {k: v["template"] for k, v in json.loads(done.stdout).items()}


def _balanced(html):
    return len(re.findall(r"<div\b", html)) == html.count("</div>")


def _nothing_inside_a_textarea(html):
    """No markup between a textarea's opening and closing tag."""
    return all("<" not in body for body in re.findall(r"<textarea\b[^>]*>(.*?)</textarea>", html, re.S))


class TestTextareaVariableButton:

    def test_button_follows_the_closing_tag(self, tmp_path):
        before = ('<div class="mb-3"><label>Headers</label>'
                  '<textarea class="form-control" name="headers" rows="3">{}</textarea></div>'
                  '<div class="mb-3"><select name="after"></select></div>')
        after = _enhance(tmp_path, {"Server": before})["Server"]
        assert ('<div class="input-group"><textarea class="form-control" name="headers" rows="3">{}'
                '</textarea>' + BUTTON + '</div>') in after
        assert _nothing_inside_a_textarea(after)
        assert _balanced(after)
        # the field after it is untouched and still a sibling block
        assert after.endswith('</div><div class="mb-3"><select name="after"></select></div>')

    def test_default_text_is_kept_as_written(self, tmp_path):
        after = _enhance(tmp_path, {"X": '<textarea name="a">line 1\nline 2</textarea>'})["X"]
        assert '<textarea name="a">line 1\nline 2</textarea>' in after
        assert after.count("showVariableSelector") == 1

    def test_tag_written_over_several_lines(self, tmp_path):
        before = ('<div id="default-value-group" class="mb-3" style="display: none;">\n'
                  '    <textarea class="form-control" name="defaultArray" rows="2" \n'
                  "              placeholder='[\"default1\", \"default2\"]'></textarea>\n"
                  '</div>\n<div class="mb-3"><select name="outputMode"></select></div>')
        after = _enhance(tmp_path, {"Loop": before})["Loop"]
        assert after.count("showVariableSelector") == 1
        assert _nothing_inside_a_textarea(after)
        assert _balanced(after)
        hidden_group = after[:after.index('<div class="mb-3"><select name="outputMode">')]
        assert _balanced(hidden_group), "the hidden group must close before the next field"

    def test_textarea_that_already_has_its_button_is_left_alone(self, tmp_path):
        before = ('<div class="input-group">\n'
                  '    <textarea class="form-control" name="messageTemplate" rows="3"></textarea>\n'
                  '    <button type="button" class="btn btn-outline-secondary" '
                  'onclick="showVariableSelector(this)">\n'
                  '        <i class="bi bi-braces"></i>\n    </button>\n</div>')
        assert _enhance(tmp_path, {"Alert": before})["Alert"] == before

    def test_opt_out_attribute_is_respected(self, tmp_path):
        before = '<div class="mb-3"><textarea name="code" rows="16" data-no-enhance></textarea></div>'
        assert _enhance(tmp_path, {"X": before})["X"] == before

    def test_small_textarea_gets_a_small_group(self, tmp_path):
        before = '<textarea class="form-control form-control-sm" name="approvalData" rows="2"></textarea>'
        after = _enhance(tmp_path, {"Human Approval": before})["Human Approval"]
        assert after == '<div class="input-group input-group-sm">' + before + BUTTON + '</div>'

    def test_every_textarea_in_a_template_gets_one_button(self, tmp_path):
        before = ('<div class="row"><div class="col-6"><textarea name="a"></textarea></div></div>'
                  '<div class="mb-2"><textarea name="b"></textarea></div><input type="hidden" name="c">')
        after = _enhance(tmp_path, {"X": before})["X"]
        assert after.count("showVariableSelector") == 2
        assert _nothing_inside_a_textarea(after)
        assert _balanced(after)

    def test_types_that_carry_their_own_buttons_are_not_touched(self, tmp_path):
        before = '<textarea name="prompt"></textarea><input type="text" name="x">'
        assert _enhance(tmp_path, {"Document": before})["Document"] == before

    def test_text_inputs_are_wrapped_as_before(self, tmp_path):
        before = '<div class="mb-3"><input type="text" class="form-control" name="url"></div>'
        after = _enhance(tmp_path, {"Server": before})["Server"]
        assert after == ('<div class="mb-3"><div class="input-group">'
                         '<input type="text" class="form-control" name="url">' + BUTTON + '</div></div>')


class TestShippedTemplates:
    """The node dialogs as they are written in workflow.js today."""

    def test_no_shipped_textarea_ends_up_with_markup_inside(self, tmp_path):
        src = WORKFLOW_JS.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        # every block of markup that holds a textarea, taken as the template text between backticks
        blocks = [b for b in re.findall(r"`([^`]*<textarea\b[^`]*)`", src) if "${" not in b.replace("$\\{", "")]
        assert len(blocks) >= 5, "expected to find the node dialog templates"
        after = _enhance(tmp_path, {f"t{i}": b for i, b in enumerate(blocks)})
        for name, html in after.items():
            assert _nothing_inside_a_textarea(html), name
            assert _balanced(html) == _balanced(blocks[int(name[1:])]), name
