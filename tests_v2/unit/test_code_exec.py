"""
Unit tests for the shared code-interpreter backend (code_exec/) plus the
chat-lane run-token and SDK help() additions.

docs/code-interpreter-unification-plan.md — Phase 1.
"""

import io
import json
import os
import sys
import textwrap
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code_exec import envbuild, executor, interpreter, preamble  # noqa: E402


# ─── envbuild: denylist secret-scrub ─────────────────────────────────────────

def _fresh_dotenv_cache():
    envbuild._dotenv_cache.clear()


def test_scrub_drops_dotenv_keys_patterns_and_python_vars(tmp_path, monkeypatch):
    _fresh_dotenv_cache()
    app_root = tmp_path / "approot"
    app_root.mkdir()
    (app_root / ".env").write_text(
        "PLAIN_CONFIG_VAR=hello\nexport EXPORTED_VAR=x\n# comment\nDB_THING=1\n",
        encoding="utf-8")
    monkeypatch.setenv("APP_ROOT", str(app_root))

    monkeypatch.setenv("PLAIN_CONFIG_VAR", "fromenv")       # .env-derived -> dropped
    monkeypatch.setenv("EXPORTED_VAR", "fromenv")           # .env-derived -> dropped
    monkeypatch.setenv("MY_API_KEY", "sekrit")              # pattern KEY -> dropped
    monkeypatch.setenv("SOME_TOKEN", "sekrit")              # pattern TOKEN -> dropped
    monkeypatch.setenv("SQL_CONNECTION_STRING", "sekrit")   # pattern CONN -> dropped
    monkeypatch.setenv("USER_PWD", "sekrit")                # pattern PWD -> dropped
    monkeypatch.setenv("PYTHONPATH", "C:/frozen/stuff")     # always-drop
    monkeypatch.setenv("PYTHONHOME", "C:/frozen")           # always-drop
    monkeypatch.setenv("TOTALLY_BENIGN", "keepme")          # passes

    workdir = tmp_path / "wd"
    workdir.mkdir()
    child = envbuild.build_child_env(str(workdir))

    for gone in ("PLAIN_CONFIG_VAR", "EXPORTED_VAR", "MY_API_KEY", "SOME_TOKEN",
                 "SQL_CONNECTION_STRING", "USER_PWD", "PYTHONPATH", "PYTHONHOME"):
        assert gone not in child, gone
    assert child["TOTALLY_BENIGN"] == "keepme"
    assert "PATH" in child  # default-open: everything else passes
    assert child["TEMP"] == str(workdir)
    assert child["TMP"] == str(workdir)
    assert child["MPLBACKEND"] == "Agg"
    assert child["PYTHONIOENCODING"] == "utf-8"
    assert Path(child["MPLCONFIGDIR"]).is_dir()


def test_scrub_extra_grants_are_added_after_the_scrub(tmp_path, monkeypatch):
    _fresh_dotenv_cache()
    monkeypatch.setenv("APP_ROOT", str(tmp_path))  # no .env here — fine
    workdir = tmp_path / "wd2"
    workdir.mkdir()
    child = envbuild.build_child_env(
        str(workdir),
        extra={"AIHUB_RUN_TOKEN": "tok", "PYTHONPATH": "C:/sdk"})
    # deliberate grants survive even though their names match the drop rules
    assert child["AIHUB_RUN_TOKEN"] == "tok"
    assert child["PYTHONPATH"] == "C:/sdk"


# ─── interpreter resolution ──────────────────────────────────────────────────

def test_resolver_prefers_existing_explicit_and_skips_stale(tmp_path, monkeypatch):
    monkeypatch.delenv("CODE_INTERPRETER_PYTHON", raising=False)
    monkeypatch.delenv("APP_ROOT", raising=False)
    real = tmp_path / "python.exe"
    real.write_text("stub")
    assert interpreter.resolve_interpreter(explicit=str(real)) == str(real)
    # stale explicit falls through to the dev fallback (sys.executable)
    assert interpreter.resolve_interpreter(explicit=str(tmp_path / "gone.exe")) == sys.executable


def test_resolver_prefer_candidate_wins(tmp_path):
    venv_py = tmp_path / "venvpy.exe"
    venv_py.write_text("stub")
    assert interpreter.resolve_interpreter(prefer=str(venv_py)) == str(venv_py)


def test_resolver_refuses_frozen_bootloader(monkeypatch, tmp_path):
    monkeypatch.delenv("CODE_INTERPRETER_PYTHON", raising=False)
    monkeypatch.setenv("APP_ROOT", str(tmp_path))  # no bundle inside
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert interpreter.resolve_interpreter() is None
    monkeypatch.delattr(sys, "frozen", raising=False)


# ─── executor + preamble end-to-end (real subprocess) ────────────────────────

def test_run_script_stages_computes_and_harvests(tmp_path, monkeypatch):
    _fresh_dotenv_cache()
    monkeypatch.setenv("APP_ROOT", str(tmp_path))
    monkeypatch.setenv("PLANTED_FAKE_APIKEY", "leak-me-not")
    workdir = tmp_path / "run"
    workdir.mkdir()
    (workdir / "data.csv").write_text("a,b\n1,2\n3,4\n5,6\n", encoding="utf-8")

    code = textwrap.dedent("""
        import csv, os
        rows = list(csv.reader(open('data.csv', encoding='utf-8')))
        print('ROWS', len(rows) - 1)
        print('LEAK', 'PLANTED_FAKE_APIKEY' in os.environ)
        with open('result.txt', 'w', encoding='utf-8') as f:
            f.write('done')
    """)
    pre = preamble.build_preamble(sdk_dir=None, pkg_dir=None,
                                 denylist_path=None, constraints_path=None)
    env = envbuild.build_child_env(str(workdir))
    baseline = executor.snapshot(str(workdir))
    res = executor.run_script(code, str(workdir), sys.executable,
                              timeout=60, env=env, preamble=pre)

    assert res["returncode"] == 0, res["stderr"]
    assert "ROWS 3" in res["stdout"]
    assert "LEAK False" in res["stdout"]          # scrub verified from inside
    produced = executor.new_files(str(workdir), baseline)
    assert [p.name for p in produced] == ["result.txt"]  # script + csv excluded


def test_preamble_install_blocked_by_denylist(tmp_path, monkeypatch):
    _fresh_dotenv_cache()
    monkeypatch.setenv("APP_ROOT", str(tmp_path))
    deny = tmp_path / "deny.txt"
    deny.write_text("# comment\nevilpkg\n", encoding="utf-8")
    pkg_dir = tmp_path / "pkgs"
    workdir = tmp_path / "run2"
    workdir.mkdir()

    pre = preamble.build_preamble(sdk_dir=None, pkg_dir=str(pkg_dir),
                                 denylist_path=str(deny), constraints_path=None)
    code = "print('BLOCKED', install('EvilPkg==1.0') is False)\n"
    env = envbuild.build_child_env(str(workdir))
    res = executor.run_script(code, str(workdir), sys.executable,
                              timeout=60, env=env, preamble=pre)
    assert res["returncode"] == 0, res["stderr"]
    assert "blocked by the platform package denylist" in res["stdout"]
    assert "BLOCKED True" in res["stdout"]


def test_run_script_timeout_is_honest(tmp_path, monkeypatch):
    _fresh_dotenv_cache()
    monkeypatch.setenv("APP_ROOT", str(tmp_path))
    workdir = tmp_path / "run3"
    workdir.mkdir()
    env = envbuild.build_child_env(str(workdir))
    res = executor.run_script("import time; time.sleep(30)", str(workdir),
                              sys.executable, timeout=2, env=env)
    assert res["timed_out"] is True
    assert res["returncode"] == -1


# ─── chat-lane run token ─────────────────────────────────────────────────────

def test_code_run_token_round_trip_and_audience_isolation():
    pytest.importorskip("jwt")
    from shared_auth import (sign_code_run_token, verify_automation_run_token,
                             verify_code_run_token)
    secret = "unit-test-secret"
    tok = sign_code_run_token("general-agent", "run123",
                              connections=["ERPDB", "AIRDB"], ttl_seconds=60,
                              user_id=7, agent_id=42, secret=secret)
    claims, err = verify_code_run_token(tok, secret=secret)
    assert err is None
    assert claims["surface"] == "general-agent"
    assert claims["connections"] == ["ERPDB", "AIRDB"]
    assert claims["user_id"] == 7 and claims["agent_id"] == 42
    # an automation endpoint must NEVER accept a chat token (distinct audience)
    _, aut_err = verify_automation_run_token(tok, secret=secret)
    assert aut_err is not None


# ─── aihub_runtime.help() ────────────────────────────────────────────────────

def test_sdk_help_lists_verbs_and_token_scope(monkeypatch):
    sdk_dir = REPO_ROOT / "automations" / "sdk"
    if str(sdk_dir) not in sys.path:
        sys.path.insert(0, str(sdk_dir))
    import base64

    import aihub_runtime

    payload = base64.urlsafe_b64encode(json.dumps(
        {"connections": ["ERPDB"], "secrets": []}).encode()).decode().rstrip("=")
    monkeypatch.setenv("AIHUB_RUN_TOKEN", f"hdr.{payload}.sig")
    buf = io.StringIO()
    with redirect_stdout(buf):
        aihub_runtime.help()
    out = buf.getvalue()
    assert "aihub.query(" in out
    assert "aihub.checkpoint(" in out
    assert "ERPDB" in out
    assert "(none)" in out  # no secrets in scope
