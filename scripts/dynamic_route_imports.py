"""
dynamic_route_imports.py - hidden-import closure for source files the main app
loads BY FILE PATH (importlib.util.spec_from_file_location) instead of importing.

Why this exists
---------------
app.py loads routes/data_explorer.py by path on purpose, so that ``routes/`` never
becomes a package that shadows builder_service/routes and builder_data/routes.
PyInstaller only walks imports through modules it can import BY NAME, so a file
loaded this way ships as a raw data file and its own imports are never analysed.
Any module imported ONLY from such a file is silently left out of the frozen
bundle, and the client build fails at request time instead of build time:

    2026-09-02, installed box 10.0.0.6: every Command Center delegation to a data
    agent returned
        "Agent returned status 500: No module named
         'command_center.artifacts.data_export'"
    because the main app imports that module from routes/data_explorer.py only.
    From source it cannot reproduce (the file is on disk); the built app.exe
    carried 18 command_center modules and not that one.

What it does
------------
* hidden_imports_for(file, repo_root) - every absolute import in the file
  (module level or nested in functions / try blocks) as dotted names that
  app_onedir.spec appends to ``hiddenimports``. PyInstaller then analyses each
  of them by name, which also pulls in THEIR imports the normal way.
* verify_bundled(names, bundled, repo_root) - after Analysis, the first-party
  names (they resolve to a .py under the repo) that are still NOT in the
  bundle. The spec fails the build on a non-empty result.

Stdlib only (ast + os) so it can be exec'd from a .spec and imported by tests.
ASCII-only on purpose: the installer build box reads scripts as cp1252.
"""
import ast
import os

# Modules the installer ships LOOSE next to the exe (never bundled, by design):
# the frozen app finds them on sys.path at runtime, so their absence from the
# bundle is not a defect.
SHIPPED_LOOSE = frozenset({"user_config", "user_prompts"})


def resolve_repo_module(dotted, repo_root):
    """Path of the first-party module/package ``dotted`` under repo_root, or None."""
    rel = os.path.join(*dotted.split("."))
    for cand in (os.path.join(repo_root, rel + ".py"),
                 os.path.join(repo_root, rel, "__init__.py")):
        if os.path.isfile(cand):
            return cand
    return None


def iter_imports(path):
    """Yield (dotted_module, imported_names) for every absolute import statement
    in the file, wherever it sits. ``imported_names`` is () for ``import X`` and
    the tuple of names for ``from X import a, b``. Relative imports are skipped:
    a path-loaded file has no package, so it cannot use them anyway."""
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, ()
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue
            yield node.module, tuple(a.name for a in node.names)


def hidden_imports_for(path, repo_root):
    """Sorted dotted names to add to PyInstaller hiddenimports for ``path``.

    ``import X.Y``          -> X.Y
    ``from X import a, b``  -> X, plus X.a / X.b when they are modules on disk
                               under repo_root (first-party submodule imports);
                               third-party names are left to PyInstaller's own
                               analysis of X.
    """
    names = set()
    for module, imported in iter_imports(path):
        names.add(module)
        for n in imported:
            if n == "*":
                continue
            if resolve_repo_module(module + "." + n, repo_root):
                names.add(module + "." + n)
    return sorted(names)


def first_party(names, repo_root):
    """The subset of ``names`` that resolve to a module or package under repo_root."""
    return sorted(n for n in names if resolve_repo_module(n, repo_root))


def verify_bundled(names, bundled, repo_root, allow_missing=SHIPPED_LOOSE):
    """First-party ``names`` that are NOT in ``bundled`` (a set of dotted module
    names from Analysis.pure + Analysis.binaries), minus ``allow_missing``.
    A non-empty result means the frozen app raises ModuleNotFoundError the first
    time the path-loaded file reaches that import."""
    bundled = set(bundled)
    return [n for n in first_party(names, repo_root)
            if n not in bundled and n.split(".")[0] not in allow_missing]


if __name__ == "__main__":  # ad-hoc: python scripts/dynamic_route_imports.py routes/data_explorer.py
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for src in sys.argv[1:] or ["routes/data_explorer.py"]:
        hidden = hidden_imports_for(os.path.join(root, src), root)
        fp = first_party(hidden, root)
        print(f"{src}: {len(hidden)} hidden imports, {len(fp)} first-party")
        for n in hidden:
            print("  " + ("* " if n in fp else "  ") + n)
