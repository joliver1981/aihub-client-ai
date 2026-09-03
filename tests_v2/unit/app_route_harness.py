"""Load individual app.py route functions WITHOUT importing app.py.

app.py is ~17k lines and imports the whole platform at module scope (pyodbc,
the LLM clients, every service), so a route-level unit test cannot import it.
This helper parses app.py with `ast`, lifts the SOURCE of the named top-level
functions / classes — decorators DROPPED, so the auth / CORS / in-flight
decorators (covered by their own tests) never run — and executes them into a
namespace the test controls: Flask's real `request` + `jsonify`, a stub
`logger`, and whatever other module-level names the route touches.

Because the source is compiled from app.py itself, a test exercises the exact
code that ships rather than a copy that can drift.

Usage:
    ns = {"request": request, "jsonify": jsonify, "logger": logging.getLogger("t")}
    load_app_symbols(["_InvalidUserAssertion", "_caller_identity",
                      "internal_document_records"], ns)
    app.add_url_rule("/api/internal/document-records", "records",
                     ns["internal_document_records"], methods=["POST"])
"""
import ast
import os

APP_PY = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "app.py"))


def app_source(path=APP_PY):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def extract_symbols(names, path=APP_PY):
    """{name: source} for the named TOP-LEVEL defs/classes, decorators dropped."""
    src = app_source(path)
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src, filename=path)
    wanted = set(names)
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and node.name in wanted:
            # node.lineno is the `def` / `class` line itself (decorators sit
            # above with their own linenos, Python >= 3.8), so slicing from
            # here is what drops them.
            out[node.name] = "".join(lines[node.lineno - 1:node.end_lineno])
    missing = wanted - set(out)
    if missing:
        raise LookupError(f"not found at the top level of app.py: {sorted(missing)}")
    return out


def load_app_symbols(names, namespace, path=APP_PY):
    """exec the named app.py symbols, in the given order, into `namespace`."""
    found = extract_symbols(names, path)
    for name in names:
        code = compile(found[name], f"{path}::{name}", "exec")
        exec(code, namespace)
    return namespace
