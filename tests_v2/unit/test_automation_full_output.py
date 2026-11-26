"""
An automation run hands out its whole output to a caller that asks (2026-11-25).

Every run result carries the last 2,000 characters of stdout and stderr. A View
tile reads its table from the run's last output line, so a tile's table had to
fit in 2,000 characters; The Agent could only report the end of what a run
printed. With full_output the result also carries 'stdout' and 'stderr' whole.
Nobody else's result changes: workflows, schedules and the Automations screen
get exactly what they got.

The runs here are REAL (a python subprocess through the runner), with the
database seams stubbed as in test_automations.py.

Run: python -m pytest tests_v2/unit/test_automation_full_output.py -p no:cacheprovider
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)

from test_automations import StubManager, StubRunner, _make_automation  # noqa: E402

pytestmark = pytest.mark.unit

# 3,000 progress lines, a warning on stderr, then the tile's table as the last line: 400 rows
CODE = (
    "import json, sys\n"
    "for i in range(3000):\n"
    "    print(f'progress line {i:05d}')\n"
    "sys.stderr.write('W' * 6000 + '\\nlast warning line\\n')\n"
    "rows = [[f'ORD-{i:05d}', 'open', i * 3] for i in range(400)]\n"
    "print(json.dumps({'columns': ['order', 'status', 'qty'], 'rows': rows}))\n"
)


@pytest.fixture
def mgr(tmp_path):
    return StubManager(str(tmp_path / "automations_data"))


def test_a_caller_that_asks_gets_the_whole_output_and_the_tails_as_well(mgr):
    aid = _make_automation(mgr, CODE, {"name": "tiledata"})
    result = StubRunner(mgr).run(aid, full_output=True)
    assert result["status"] in ("success", "unverified"), result
    out = result["stdout"]
    assert out.startswith("progress line 00000") and "progress line 02999" in out
    table = json.loads(out.strip().splitlines()[-1])
    assert len(table["rows"]) == 400 and table["rows"][-1] == ["ORD-00399", "open", 1197]
    assert len(out) > 60_000                                   # far past 2,000 characters
    assert result["stderr"].startswith("W" * 6000) and "last warning line" in result["stderr"]
    # the tails every caller gets are still there, unchanged
    assert result["stdout_tail"] == out[-2000:] and len(result["stdout_tail"]) == 2000
    assert result["stderr_tail"] == result["stderr"][-2000:]
    assert "_output" not in result
    json.dumps(result)                                         # the result is still plain JSON


def test_everyone_else_gets_exactly_what_they_got(mgr):
    aid = _make_automation(mgr, CODE, {"name": "tiledata2"})
    result = StubRunner(mgr).run(aid)
    assert result["status"] in ("success", "unverified"), result
    assert "stdout" not in result and "stderr" not in result and "_output" not in result
    assert len(result["stdout_tail"]) == 2000 and result["stdout_tail"].endswith("]]}\n")
    # the tile's line does not fit in the tail: this is what a tile had to work with
    with pytest.raises(ValueError):
        json.loads(result["stdout_tail"].strip().splitlines()[-1])
    json.dumps(result)


def test_a_run_that_fails_hands_out_its_whole_error_text_when_asked(mgr):
    code = "import sys\nprint('started')\nsys.stderr.write('E' * 9000 + '\\nValueError: bad input\\n')\nsys.exit(3)\n"
    aid = _make_automation(mgr, code, {"name": "failing"})
    result = StubRunner(mgr).run(aid, full_output=True)
    assert result["status"] == "failed" and result["exit_code"] == 3
    assert result["stderr"].startswith("E" * 9000) and "ValueError: bad input" in result["stderr"]
    assert result["stdout"].strip() == "started"


def test_an_inline_code_step_of_a_workflow_is_unchanged(mgr, tmp_path):
    runner = StubRunner(mgr)
    manifest = {"name": "step", "entrypoint": "main.py", "timeout_seconds": 60, "inputs": [],
                "connections": [], "secrets": [], "packages": [], "outputs": []}
    result = runner.run_code_step("print('x' * 5000)\nprint('done')\n", manifest, "step one",
                                  workdir=str(tmp_path / "step"))
    assert result["status"] in ("success", "unverified"), result
    assert "_output" not in result and "stdout" not in result
    assert result["stdout_tail"].endswith("done\n") and len(result["stdout_tail"]) == 2000
    json.dumps(result)


def test_the_manage_seam_passes_the_option_only_when_asked():
    api = open(os.path.join(REPO, "automations", "api.py"), encoding="utf-8").read()
    assert 'whole_output = {"full_output": True} if payload.get("full_output") else {}' in api
    assert "**whole_output)" in api
    assert "full_output=bool(full_output))" in api                 # _run_inline -> runner.run
    # the user route and the scheduler start runs without it
    start = api.index("def _start_run(")
    assert "full_output" not in api[start:api.index("@automations_bp.route", start)]
