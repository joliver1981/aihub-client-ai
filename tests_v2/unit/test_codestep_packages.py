"""
AIHUB-0036 — declared step `packages` are installed and made importable to the
code step. Network-free: `subprocess.run` (the pip call) is mocked to drop a
module into the --target dir; the STEP subprocess still runs for real (Popen),
so it proves the install-dir is actually injected on PYTHONPATH.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

from automations.runner import AutomationRunner
import automations.runner as runner_mod

pytestmark = pytest.mark.unit


class _CfgStub:
    AUTOMATIONS_ENV_CRED_INJECTION = False


@pytest.fixture(autouse=True)
def _stub_cfg(monkeypatch):
    monkeypatch.setattr(runner_mod, "_load_cfg", lambda: _CfgStub)


def _runner(tmp_path, monkeypatch):
    # redirect get_app_path so the pkg cache + run dirs land under tmp
    monkeypatch.setattr(runner_mod, "get_app_path",
                        lambda *parts: os.path.join(str(tmp_path), *parts))
    r = AutomationRunner.__new__(AutomationRunner)
    r.manager = None
    r.tenant_id = "pkgtest"
    r.connection_string = "stub"
    r._resolve_python = lambda env_id: sys.executable
    r._resolve_connection = lambda n: None
    r._resolve_secret = lambda n: None
    return r


def _fake_pip(module_name, body="VALUE = 42\n"):
    """A subprocess.run stand-in that simulates `pip install --target DIR pkg`
    by writing module_name.py into DIR and reporting success."""
    def _run(cmd, capture_output=True, text=True, timeout=None):
        target = cmd[cmd.index("--target") + 1]
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(target, module_name + ".py"), "w", encoding="utf-8") as f:
            f.write(body)
        return types.SimpleNamespace(returncode=0, stdout="Successfully installed", stderr="")
    return _run


class TestEnsurePackages:
    def test_no_packages_is_noop(self, tmp_path, monkeypatch):
        r = _runner(tmp_path, monkeypatch)
        assert r._ensure_packages([], sys.executable) == (None, None)
        assert r._ensure_packages(None, sys.executable) == (None, None)

    def test_installs_then_caches(self, tmp_path, monkeypatch):
        r = _runner(tmp_path, monkeypatch)
        calls = []
        run = _fake_pip("widget")
        monkeypatch.setattr(runner_mod.subprocess, "run",
                            lambda *a, **k: (calls.append(a[0]) or run(*a, **k)))
        d1, e1 = r._ensure_packages(["widget"], sys.executable)
        assert e1 is None and d1 and os.path.isfile(os.path.join(d1, "widget.py"))
        assert len(calls) == 1
        # second call for the same dep set: cached (no new pip)
        d2, e2 = r._ensure_packages(["widget"], sys.executable)
        assert e2 is None and d2 == d1 and len(calls) == 1

    def test_pip_failure_is_honest_error(self, tmp_path, monkeypatch):
        r = _runner(tmp_path, monkeypatch)
        monkeypatch.setattr(runner_mod.subprocess, "run",
                            lambda *a, **k: types.SimpleNamespace(
                                returncode=1, stdout="", stderr="No matching distribution found for nope"))
        d, e = r._ensure_packages(["nope"], sys.executable)
        assert d is None
        assert "pip install" in e and "No matching distribution" in e

    def test_auto_install_disabled_is_noop(self, tmp_path, monkeypatch):
        r = _runner(tmp_path, monkeypatch)
        monkeypatch.setenv("AUTOMATIONS_PKG_AUTO_INSTALL", "false")
        assert r._ensure_packages(["widget"], sys.executable) == (None, None)


class TestCodeStepUsesInstalledPackage:
    def test_declared_package_is_importable_in_the_step(self, tmp_path, monkeypatch):
        r = _runner(tmp_path, monkeypatch)
        monkeypatch.setattr(runner_mod.subprocess, "run",
                            _fake_pip("acmelib", "def greet():\n    return 'hello-from-acme'\n"))
        code = ("import acmelib\n"
                "open('out.txt', 'w').write(acmelib.greet())\n")
        manifest = {"packages": ["acmelib"],
                    "outputs": [{"kind": "file", "path": "out.txt"}], "timeout_seconds": 60}
        res = r.run_code_step(code, manifest, "needs-pkg", workdir=str(tmp_path / "run1"))
        assert res["status"] == "success", res
        assert open(os.path.join(res["workdir"], "out.txt")).read() == "hello-from-acme"

    def test_pip_failure_fails_the_step_before_running(self, tmp_path, monkeypatch):
        r = _runner(tmp_path, monkeypatch)
        monkeypatch.setattr(runner_mod.subprocess, "run",
                            lambda *a, **k: types.SimpleNamespace(
                                returncode=1, stdout="", stderr="No matching distribution found for ghost"))
        res = r.run_code_step("print('should not run')\n",
                              {"packages": ["ghost"], "outputs": [], "timeout_seconds": 30},
                              "badpkg", workdir=str(tmp_path / "run2"))
        assert res["status"] == "failed" and "pip install" in (res.get("error") or "")

    def test_missing_dep_without_autoinstall_fails_at_import(self, tmp_path, monkeypatch):
        r = _runner(tmp_path, monkeypatch)
        monkeypatch.setenv("AUTOMATIONS_PKG_AUTO_INSTALL", "false")
        res = r.run_code_step("import definitely_not_installed_xyz\n",
                              {"packages": ["definitely_not_installed_xyz"], "outputs": [], "timeout_seconds": 30},
                              "missing", workdir=str(tmp_path / "run3"))
        assert res["status"] == "failed"   # honest ModuleNotFoundError -> nonzero exit
