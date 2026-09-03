"""
app_onedir.spec must bundle every module imported by the source files app.py
loads BY PATH (importlib.util.spec_from_file_location), because PyInstaller
never analyses those files.

Regression for the installed-box defect of 2026-09-02: every Command Center
delegation to a data agent on 10.0.0.6 returned
    "Agent returned status 500: No module named 'command_center.artifacts.data_export'"
The module is imported only by routes/data_explorer.py (a path-loaded file), so
the built app.exe carried 18 command_center modules and not that one. Source
runs cannot reproduce it. See scripts/dynamic_route_imports.py.
"""
import ast
import importlib.util
import os
import re

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HELPER = os.path.join(REPO, "scripts", "dynamic_route_imports.py")
SPEC = os.path.join(REPO, "app_onedir.spec")
APP_PY = os.path.join(REPO, "app.py")
DATA_EXPLORER = os.path.join(REPO, "routes", "data_explorer.py")


def _helper():
    spec = importlib.util.spec_from_file_location("dynamic_route_imports", HELPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _path_loaded_sources_in_app_py():
    """Every file app.py loads via spec_from_file_location(..., os.path.join(
    os.path.dirname(__file__), <parts...>)), as repo-relative posix paths."""
    with open(APP_PY, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=APP_PY)
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "spec_from_file_location" and len(node.args) >= 2):
            continue
        target = node.args[1]
        if isinstance(target, ast.Call) and isinstance(target.func, ast.Attribute) \
                and target.func.attr == "join":
            parts = [a.value for a in target.args[1:] if isinstance(a, ast.Constant)]
            if parts:
                found.append("/".join(parts))
    return found


def _spec_path_loaded_sources():
    with open(SPEC, "r", encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"PATH_LOADED_SOURCES\s*=\s*\[(.*?)\]", text, re.S)
    assert m, "app_onedir.spec lost its PATH_LOADED_SOURCES list"
    return re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))


# --------------------------------------------------------------------------- helper

def test_data_export_is_derived_from_the_route_file():
    """The exact module that was missing on the installed box must come out of
    the derivation, so the spec cannot drop it again."""
    h = _helper()
    names = h.hidden_imports_for(DATA_EXPLORER, REPO)
    assert "command_center.artifacts.data_export" in names
    # and the derivation sees nested imports, not only module-level ones
    assert "nlq_engine_factory" in names
    assert "role_decorators" in names


def test_every_first_party_import_of_the_route_file_exists_on_disk():
    h = _helper()
    names = h.hidden_imports_for(DATA_EXPLORER, REPO)
    fp = h.first_party(names, REPO)
    assert "command_center.artifacts.data_export" in fp
    assert "flask" not in fp          # third-party is left to PyInstaller
    for n in fp:
        assert h.resolve_repo_module(n, REPO), n


def test_verify_bundled_flags_the_missing_module_and_nothing_else():
    h = _helper()
    names = h.hidden_imports_for(DATA_EXPLORER, REPO)
    complete = set(h.first_party(names, REPO))
    assert h.verify_bundled(names, complete, REPO) == []
    # The 2026-09-02 build: the artifacts package present, data_export absent.
    without = complete - {"command_center.artifacts.data_export"}
    assert h.verify_bundled(names, without, REPO) == ["command_center.artifacts.data_export"]


def test_verify_bundled_tolerates_modules_shipped_loose(tmp_path):
    """user_config / user_prompts ship loose next to the exe by design."""
    h = _helper()
    (tmp_path / "user_config.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "helper_mod.py").write_text("Y = 2\n", encoding="utf-8")
    src = tmp_path / "loaded_by_path.py"
    src.write_text("def f():\n    import user_config\n    from helper_mod import Y\n",
                   encoding="utf-8")
    names = h.hidden_imports_for(str(src), str(tmp_path))
    assert names == ["helper_mod", "user_config"]
    assert h.verify_bundled(names, set(), str(tmp_path)) == ["helper_mod"]


def test_from_package_import_submodule_yields_the_submodule(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sub.py").write_text("Z = 3\n", encoding="utf-8")
    src = tmp_path / "loaded_by_path.py"
    src.write_text("from pkg import sub, missing_symbol\nfrom . import rel\n",
                   encoding="utf-8")
    h = _helper()
    names = h.hidden_imports_for(str(src), str(tmp_path))
    assert names == ["pkg", "pkg.sub"]   # relative import skipped, symbol not a module


# ------------------------------------------------------------------------ spec drift

def test_spec_lists_every_file_app_py_loads_by_path():
    """A new spec_from_file_location load in app.py must be added to the spec's
    PATH_LOADED_SOURCES, or its imports fall into the same hole."""
    in_app = _path_loaded_sources_in_app_py()
    assert "routes/data_explorer.py" in in_app, in_app
    in_spec = _spec_path_loaded_sources()
    missing = sorted(set(in_app) - set(in_spec))
    assert not missing, f"app.py path-loads {missing} but app_onedir.spec does not list them"


def test_spec_wires_the_helper_and_the_build_guard():
    with open(SPEC, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert "dynamic_route_imports.py" in text
    assert "path_loaded_hiddenimports" in text
    # hidden imports are appended to Analysis(...)
    assert re.search(r"hiddenimports\s*=\s*\[.*?\]\s*\+\s*all_collected_hiddenimports\s*\+\s*path_loaded_hiddenimports",
                     text, re.S), "path_loaded_hiddenimports is not appended to hiddenimports"
    # and the post-Analysis guard fails the build
    assert "verify_bundled(" in text and "raise SystemExit" in text
