"""
The General Agent and Command Center get back up to 500,000 characters of what
Python code prints (2026-11-25; it was 50,000).

Both surfaces cut the output of the code they run: the shared lane
(code_exec.executor, the General Agent's) and Command Center's own interpreter
(command_center.tools.code_interpreter). Both read
CODE_INTERPRETER_MAX_OUTPUT_CHARS and default to the same number. Behind the
limit there were three smaller cuts of the same output, removed with it:
Command Center showed the first 8,000 characters when the code also made a
file, and the last 15 lines of an error; the General Agent the last 4,000
characters of an error.

The runs here are REAL (a python subprocess through the lane).

Run: python -m pytest tests_v2/unit/test_python_output_limit.py -p no:cacheprovider
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code_exec import envbuild, executor, preamble  # noqa: E402

pytestmark = pytest.mark.unit

ENV_NAME = "CODE_INTERPRETER_MAX_OUTPUT_CHARS"

# 39 characters a line with its line end
PRINT_LINES = "for i in range({n}):\n    print(f'row {{i:06d}} of the listing, forty chars')\nprint('THE-END-MARK')\n"


def _run(tmp_path, monkeypatch, code, **kw):
    monkeypatch.setenv("APP_ROOT", str(tmp_path))
    workdir = tmp_path / "run"
    workdir.mkdir(exist_ok=True)
    pre = preamble.build_preamble(sdk_dir=None, pkg_dir=None, denylist_path=None, constraints_path=None)
    env = envbuild.build_child_env(str(workdir))
    return executor.run_script(code, str(workdir), sys.executable, timeout=120, env=env, preamble=pre, **kw)


# ----------------------------------------------------------------- the shared lane (General Agent)
def test_the_lane_default_is_500000_and_the_setting_overrides_it(monkeypatch):
    monkeypatch.delenv(ENV_NAME, raising=False)
    assert executor._DEFAULT_MAX_CHARS == 500_000
    assert executor._max_chars() == 500_000
    monkeypatch.setenv(ENV_NAME, "120000")
    assert executor._max_chars() == 120_000
    monkeypatch.setenv(ENV_NAME, "plenty")                    # unreadable: the default, never a crash
    assert executor._max_chars() == 500_000


def test_300000_characters_of_output_come_back_whole(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_NAME, raising=False)
    res = _run(tmp_path, monkeypatch, PRINT_LINES.format(n=8000))        # 312,000 characters: six times the old limit
    assert res["returncode"] == 0, res["stderr"]
    out = res["stdout"]
    assert len(out) > 300_000 and "truncated" not in out
    assert out.startswith("row 000000 of the listing") and "row 007999 of the listing" in out
    assert out.rstrip().endswith("THE-END-MARK")


def test_output_past_500000_is_cut_there_and_says_so(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_NAME, raising=False)
    res = _run(tmp_path, monkeypatch, PRINT_LINES.format(n=15000))       # 585,000 characters
    assert res["returncode"] == 0, res["stderr"]
    out = res["stdout"]
    assert out.endswith("\n... [truncated at 500000 characters]")
    assert len(out) == 500_000 + len("\n... [truncated at 500000 characters]")
    assert "THE-END-MARK" not in out


def test_a_long_error_text_comes_back_whole_too(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_NAME, raising=False)
    code = "import sys\nsys.stderr.write('E' * 90000 + '\\nValueError: the real cause\\n')\nsys.exit(2)\n"
    res = _run(tmp_path, monkeypatch, code)
    assert res["returncode"] == 2
    assert res["stderr"].startswith("E" * 90000) and "ValueError: the real cause" in res["stderr"]


# ----------------------------------------------------------------- Command Center's interpreter
def test_command_center_has_the_same_default_and_setting(monkeypatch):
    from command_center.tools import code_interpreter as ci
    monkeypatch.delenv(ENV_NAME, raising=False)
    assert ci._DEFAULT_MAX_STREAM_CHARS == 500000 == executor._DEFAULT_MAX_CHARS
    assert ci._max_stream_chars() == 500000
    text = "x" * 300_000 + "END"
    assert ci._truncate(text) == text                         # whole (it was cut at 50,000)
    cut = ci._truncate("y" * 700_000)
    assert "characters omitted" in cut and len(cut) < 500_200
    assert cut.startswith("y" * 1000) and cut.endswith("y" * 1000)       # beginning and end are kept
    monkeypatch.setenv(ENV_NAME, "0")                         # 0 = no limit at all
    assert ci._truncate("z" * 900_000) == "z" * 900_000


# ----------------------------------------------------------------- the smaller cuts behind the limit
def test_the_smaller_cuts_of_the_same_output_are_gone():
    nodes = (REPO_ROOT / "command_center_service" / "graph" / "nodes.py").read_text(encoding="utf-8")
    start = nodes.index("    async def run_python(code: str) -> str:")
    body = nodes[start:nodes.index("    async def read_artifact(", start)]
    assert "stdout[:8000]" not in body and "splitlines()[-15:]" not in body
    assert 'captured = f"```\\n{stdout}\\n```"' in body
    assert 'tail = stderr if stderr else "Unknown error."' in body
    ga = (REPO_ROOT / "GeneralAgent.py").read_text(encoding="utf-8")
    assert "(res['stderr'] or '').strip()[-4000:]" not in ga
    assert "stderr_tail = (res['stderr'] or '').strip()\n" in ga
    # and the General Agent's tool no longer cuts its whole result once more at 18,000
    # characters, which it did without a word (found by calling the tool for real)
    assert "result = result[:18000]" not in ga


def test_an_upgraded_install_gets_the_new_number():
    iss = (REPO_ROOT / "AIHub_Setup_Script_v7_OneDir_Dev.iss").read_text(encoding="utf-8", errors="replace")
    assert "ForceEnvKeyValue(EnvConfigFile, 'CODE_INTERPRETER_MAX_OUTPUT_CHARS', '500000')" in iss
