"""
scripts/stage_cc_tools_subset.ps1 gives the SOURCE-RUN services (The Agent, Browser Use)
a service-local copy of the command_center.tools modules they import, instead of the
partial loose {app}/command_center package earlier installers shipped (which shadowed the
Command Center exe's bundled package under PyInstaller's path-based finder and killed every
CC chat with "No module named 'command_center.orchestration'", 2026-09-01).

These tests pin the three properties that keep clients working:
  1. the staged copy is exactly the declared set, and that set is CLOSED: every
     command_center.* import inside it resolves to another shipped module, or is a
     guarded optional import (inside try/except) that degrades;
  2. the helper refuses to stage when a new import escapes the set;
  3. portal_fetch resolves the Browser Use / main-app URLs WITHOUT CommonUtils (the
     source-run services do not ship it), using the same env rules CommonUtils applies.
"""
import ast
import os
import subprocess
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HELPER = os.path.join(REPO, "scripts", "stage_cc_tools_subset.ps1")
SHIPPED = [
    "command_center/__init__.py",
    "command_center/tools/__init__.py",
    "command_center/tools/portal_workflows.py",
    "command_center/tools/portal_registry.py",
    "command_center/tools/portal_fetch.py",
    "command_center/tools/portal_workflow_run.py",
]
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="PowerShell staging helper")


def _run_helper(dest, repo=REPO):
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", HELPER,
         "-Dest", dest, "-Repo", repo],
        capture_output=True, text=True, timeout=120,
    )


def _dotted(rel):
    d = rel.replace("/", ".").replace("\\", ".")[:-3]
    return d[:-len(".__init__")] if d.endswith(".__init__") else d


def _cc_imports_with_guard(path):
    """Yield (dotted target, guarded) for every command_center.* import in the file.
    guarded = the import statement sits inside a `try:` whose handlers catch it."""
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def guarded(n):
        while n in parents:
            n = parents[n]
            if isinstance(n, ast.Try) and n.handlers:
                return True
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("command_center"):
                    yield a.name, guarded(node)
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("command_center"):
            base = node.module
            if base in {_dotted(r) for r in SHIPPED if not r.endswith("__init__.py")}:
                continue  # symbols from a shipped module
            for a in node.names:
                yield f"{base}.{a.name}", guarded(node)


@windows_only
def test_staged_copy_is_exactly_the_closed_set(tmp_path):
    dest = tmp_path / "agent_service"
    dest.mkdir()
    r = _run_helper(str(dest))
    assert r.returncode == 0, r.stdout + r.stderr
    staged = sorted(
        os.path.relpath(os.path.join(d, f), dest).replace("\\", "/")
        for d, _, fs in os.walk(dest) for f in fs
    )
    assert staged == sorted(SHIPPED)
    for rel in SHIPPED:  # byte-identical to the repo copy
        with open(os.path.join(REPO, rel), "rb") as a, open(dest / rel, "rb") as b:
            assert a.read() == b.read(), rel

    shipped = {_dotted(r) for r in SHIPPED}
    for rel in SHIPPED:
        for target, is_guarded in _cc_imports_with_guard(os.path.join(REPO, rel)):
            assert target in shipped or is_guarded, (
                f"{rel}: `{target}` is neither shipped nor guarded - on a client it raises "
                f"ModuleNotFoundError. Add it to $Files in {HELPER} or guard the import.")


@windows_only
def test_helper_fails_when_an_import_escapes_the_set(tmp_path):
    fake_repo = tmp_path / "repo"
    for rel in SHIPPED:
        p = fake_repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
    (fake_repo / "command_center/tools/portal_fetch.py").write_text(
        "from command_center.tools import tool_factory\n", encoding="utf-8")
    dest = tmp_path / "svc"
    dest.mkdir()
    r = _run_helper(str(dest), repo=str(fake_repo))
    assert r.returncode != 0
    assert "closure check failed" in (r.stdout + r.stderr)
    assert "tool_factory" in (r.stdout + r.stderr)


def test_browser_use_url_resolves_without_commonutils(monkeypatch):
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    from command_center.tools import portal_fetch as pf

    monkeypatch.setitem(sys.modules, "CommonUtils", None)   # import raises -> fallback path
    for var in ("PROTOCOL", "INTERNAL_HOST", "HOST", "BROWSER_USE_PORT", "HOST_PORT", "APP_PUBLIC_BASE_URL"):
        monkeypatch.delenv(var, raising=False)

    assert pf.browser_use_base_url() == "http://127.0.0.1:5101"          # defaults: HOST_PORT 5001 + 100
    monkeypatch.setenv("HOST_PORT", "6001")
    assert pf.browser_use_base_url() == "http://127.0.0.1:6101"
    monkeypatch.setenv("BROWSER_USE_PORT", "7000")                        # explicit override wins
    assert pf.browser_use_base_url() == "http://127.0.0.1:7000"
    monkeypatch.setenv("BROWSER_USE_PORT", "not-a-port")
    assert pf.browser_use_base_url() == "http://127.0.0.1:5101"          # CommonUtils' bad-value default

    assert pf.main_app_base_url() == "http://localhost:6001"
    assert pf.cobrowse_link("r1") == "http://localhost:6001/portal-workflows/cobrowse/r1"
    monkeypatch.setenv("APP_PUBLIC_BASE_URL", "https://hub.example.com/")
    assert pf.cobrowse_link("r1") == "https://hub.example.com/portal-workflows/cobrowse/r1"


def test_url_helpers_prefer_commonutils_when_importable(monkeypatch):
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    import types
    from command_center.tools import portal_fetch as pf

    fake = types.ModuleType("CommonUtils")
    fake.get_browser_use_api_base_url = lambda: "http://cu-browser:1"
    fake.get_base_url = lambda: "http://cu-app:2"
    monkeypatch.setitem(sys.modules, "CommonUtils", fake)
    monkeypatch.delenv("APP_PUBLIC_BASE_URL", raising=False)
    assert pf.browser_use_base_url() == "http://cu-browser:1"
    assert pf.main_app_base_url() == "http://cu-app:2"
    assert pf.cobrowse_link("x") == "http://cu-app:2/portal-workflows/cobrowse/x"
