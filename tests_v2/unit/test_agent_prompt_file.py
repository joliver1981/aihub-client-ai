"""The system prompt travels by FILE, never on the command line
(docs/handoff-argv-limit-agent-down.md, 2026-09-06).

An inline system_prompt rides on the CLI's argv; Windows caps a command line
at 32,767 chars, so a ~32.4k prompt made every spawn fail with WinError 206
— relabelled by the SDK as "Claude Code not found" — while /health said ok.
These tests pin the file form, the per-turn freshness check, the startup
measurement, and that the measurement would have caught the inline shape.

Runs standalone (aihub-agent python test_agent_prompt_file.py) or under
pytest; self-skips without the SDK.
"""
import os
import sys

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import brain as B                          # noqa: E402
    from claude_agent_sdk import ClaudeAgentOptions  # noqa: E402
    HAVE_SDK = True
except ImportError as e:
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(
            reason=f"needs the aihub-agent env (claude_agent_sdk): {_IMPORT_ERR}")
    except ImportError:
        pass


def test_options_carry_the_prompt_by_file_and_the_file_is_current():
    opts = B._make_options(None, "full", None, 2, ["aihub-portals"])
    assert isinstance(opts.system_prompt, dict)
    assert opts.system_prompt["type"] == "file"
    path = opts.system_prompt["path"]
    assert path == B.SYSTEM_PROMPT_PATH and os.path.isfile(path)
    with open(path, encoding="utf-8") as fh:
        assert fh.read() == B.SYSTEM_PROMPT
    # the read-scope side-thread options use the same file form
    ro = B._make_options(None, "read", None, 2, [])
    assert ro.system_prompt == {"type": "file", "path": B.SYSTEM_PROMPT_PATH}


def test_stale_or_missing_prompt_file_is_rewritten_before_a_turn():
    with open(B.SYSTEM_PROMPT_PATH, "w", encoding="utf-8") as fh:
        fh.write("stale prompt from an older build")
    assert B.ensure_system_prompt_file() == B.SYSTEM_PROMPT_PATH
    with open(B.SYSTEM_PROMPT_PATH, encoding="utf-8") as fh:
        assert fh.read() == B.SYSTEM_PROMPT
    os.remove(B.SYSTEM_PROMPT_PATH)
    B.ensure_system_prompt_file()
    assert os.path.isfile(B.SYSTEM_PROMPT_PATH)


def test_command_line_is_measured_and_well_under_the_windows_limit():
    ready = B.spawn_readiness()
    assert ready["ok"], ready
    assert ready["prompt_file_ok"] and ready["argv_chars"] is not None
    assert ready["argv_chars"] < 8000, ready          # ~1.6k today; 20x headroom
    assert ready["argv_chars"] <= B.ARGV_BUDGET < B.ARGV_LIMIT
    assert ready["prompt_chars"] == len(B.SYSTEM_PROMPT)
    assert ready["argv_measured"], "the SDK's own _build_command should be measurable here"


def test_the_measurement_would_have_caught_the_inline_prompt():
    # The shape that took the service down: the whole prompt as an argument.
    opts = B._make_options(None, "full", None, 2, [])
    inline = ClaudeAgentOptions(**{**{f: getattr(opts, f) for f in opts.__dataclass_fields__},
                                   "system_prompt": B.SYSTEM_PROMPT})
    n = B.measure_argv_chars(inline)
    if n is None:
        n = B.estimate_argv_chars(inline)
    assert n > B.ARGV_LIMIT, n
    # and the fallback estimator agrees in both directions
    assert B.estimate_argv_chars(inline) > B.ARGV_LIMIT
    assert B.estimate_argv_chars(opts) < B.ARGV_BUDGET


def test_startup_guard_refuses_an_over_budget_shape():
    from unittest import mock
    with mock.patch.object(B, "ARGV_BUDGET", 10):
        try:
            B.assert_spawn_budget()
        except RuntimeError as e:
            assert "cannot spawn turns" in str(e) and "WinError 206" in str(e)
        else:
            raise AssertionError("startup guard did not fire")
    assert B.assert_spawn_budget()["ok"]


if __name__ == "__main__":
    if not HAVE_SDK:
        print(f"SKIP-ALL: {_IMPORT_ERR}")
        sys.exit(0)
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in fns:
        try:
            f()
            print(f"PASS  {n}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {n}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {n}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
