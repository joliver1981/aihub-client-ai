"""
scripts/stage_code_exec.ps1 gives the SOURCE-RUN Agent service a service-local copy of the
shared code_exec package (the code-interpreter backend). code_exec/ lives at the repo root
and inside the frozen exes; nothing shipped it for the Agent, so on every install
run_python / export_data / manipulate_pdf died with "No module named 'code_exec'"
(pack-20 per-tool smoke against Latest7, 2026-09-03).

These tests pin what keeps clients working:
  1. the staged copy is exactly the package's module set and every code_exec.* import
     inside it resolves within the set (the helper fails the build otherwise);
  2. the staged copy imports and resolves an interpreter WITHOUT the repo root on
     sys.path and WITHOUT CommonUtils (a client layout);
  3. sdkwire finds the installed SDK through APP_ROOT and derives the platform base URL
     from the same env rules the other source-run modules use when CommonUtils is absent.
"""
import glob
import os
import subprocess
import sys
import textwrap

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HELPER = os.path.join(REPO, "scripts", "stage_code_exec.ps1")
AGENT_PY = os.path.join(os.path.expanduser("~"), "miniconda3", "envs", "aihub-agent", "python.exe")
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="PowerShell staging helper")


def _run_helper(dest, repo=REPO):
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", HELPER,
         "-Dest", dest, "-Repo", repo],
        capture_output=True, text=True, timeout=120)


def _stage(tmp_path):
    dest = tmp_path / "agent_service"
    dest.mkdir()
    r = _run_helper(str(dest))
    assert r.returncode == 0, r.stdout + r.stderr
    return dest


@windows_only
def test_staged_set_is_the_whole_package_and_closed(tmp_path):
    dest = _stage(tmp_path)
    staged = sorted(os.path.basename(p) for p in glob.glob(str(dest / "code_exec" / "*.py")))
    source = sorted(os.path.basename(p) for p in glob.glob(os.path.join(REPO, "code_exec", "*.py")))
    assert staged == source
    assert "__init__.py" in staged and "sdkwire.py" in staged
    assert not (dest / "code_exec" / "__pycache__").exists()


@windows_only
def test_helper_refuses_an_undeclared_module(tmp_path):
    """A module added to code_exec/ but not to $Files must fail the build, not ship
    a package that imports it."""
    import shutil
    repo_copy = tmp_path / "repo"
    (repo_copy / "scripts").mkdir(parents=True)
    shutil.copytree(os.path.join(REPO, "code_exec"), str(repo_copy / "code_exec"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(HELPER, str(repo_copy / "scripts" / "stage_code_exec.ps1"))
    (repo_copy / "code_exec" / "newthing.py").write_text("X = 1\n")
    dest = tmp_path / "svc"
    dest.mkdir()
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(repo_copy / "scripts" / "stage_code_exec.ps1"), "-Dest", str(dest),
         "-Repo", str(repo_copy)], capture_output=True, text=True, timeout=120)
    assert r.returncode != 0
    assert "newthing" in (r.stdout + r.stderr)
    assert not (dest / "code_exec").exists()


@windows_only
@pytest.mark.skipif(not os.path.isfile(AGENT_PY), reason="aihub-agent env not present")
def test_staged_copy_imports_in_a_client_layout(tmp_path):
    """Client layout: APP_ROOT without code_exec/ or CommonUtils; the staged copy
    inside the service dir is what resolves, and the interpreter resolves from
    CODE_INTERPRETER_PYTHON exactly as on an install."""
    dest = _stage(tmp_path)
    app_root = tmp_path / "app"
    (app_root / "automations" / "sdk" / "aihub_runtime").mkdir(parents=True)
    probe = textwrap.dedent(f"""
        import os, sys
        sys.path = [r"{dest}"] + [p for p in sys.path if r"{REPO}" not in p]
        os.environ["APP_ROOT"] = r"{app_root}"
        os.environ["CODE_INTERPRETER_PYTHON"] = sys.executable
        os.environ["SERVICE_HOST"] = "10.9.9.9"; os.environ["HOST_PORT"] = "5678"
        import code_exec
        from code_exec import resolve_interpreter, sdkwire
        assert code_exec.__file__.startswith(r"{dest}"), code_exec.__file__
        assert resolve_interpreter() == sys.executable
        assert sdkwire.sdk_dir() == os.path.join(r"{app_root}", "automations", "sdk"), sdkwire.sdk_dir()
        assert sdkwire.runtime_base_url() == "http://10.9.9.9:5678", sdkwire.runtime_base_url()
        try:
            import CommonUtils  # must NOT be reachable in this layout
            raise SystemExit("CommonUtils leaked into the client layout")
        except ImportError:
            pass
        print("CLIENT-LAYOUT-OK")
    """)
    r = subprocess.run([AGENT_PY, "-c", probe], capture_output=True, text=True, timeout=120,
                       cwd=str(dest))
    assert r.returncode == 0 and "CLIENT-LAYOUT-OK" in r.stdout, r.stdout + r.stderr


def test_sdkwire_prefers_source_tree_then_app_root(monkeypatch, tmp_path):
    """Dev tree: the repo's own automations/sdk wins (unchanged behaviour); an
    APP_ROOT candidate only matters when the source tree has none."""
    sys.path.insert(0, REPO)
    from code_exec import sdkwire
    assert sdkwire.sdk_dir() == os.path.join(REPO, "automations", "sdk")
    monkeypatch.setenv("AUTOMATIONS_RUNTIME_URL", "http://override:1/")
    assert sdkwire.runtime_base_url() == "http://override:1"
