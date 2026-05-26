"""
Coverage gap detector for AI Hub.

Walks Python source with the `ast` module to enumerate production surfaces
(HTTP routes, env vars, role decorators), then string-matches those surfaces
against the project's test corpus. Emits a Markdown report.

Stdlib only. No new third-party deps.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Paths / config
# ---------------------------------------------------------------------------

THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent.parent

SKIP_DIR_NAMES = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    "node_modules",
    "Output",
    ".pytest_cache",
    ".mypy_cache",
    ".idea",
    ".vscode",
    "logs",
    "archives",
    "site-packages",
    "Lib",
    "Scripts",
    "agent_environments",  # vendored per-tenant Python envs
}

# Source roots we treat as production code (we exclude tests/ and tests_v2/).
DEFAULT_SOURCE_ROOTS = [PROJECT_ROOT]

# Test roots — scanned for references to routes/env-vars.
DEFAULT_TEST_ROOTS = [
    PROJECT_ROOT / "tests",
    PROJECT_ROOT / "tests_v2",
    PROJECT_ROOT / "builder_mcp" / "tests",
    PROJECT_ROOT / "builder_service" / "tests",
    PROJECT_ROOT / "builder_data" / "tests",
    PROJECT_ROOT / "builder_agent" / "tests",
    PROJECT_ROOT / "data_collection_agent" / "tests",
    PROJECT_ROOT / "command_center_service" / "tests",
    PROJECT_ROOT / "e2e_app_tests",
]

# When walking source, also skip these path *prefixes* (relative to root):
SKIP_PATH_PREFIXES = {
    "tests",
    "tests_v2",
    "MCP",  # earlier prototypes, per CLAUDE.md
    "Output",
    "agent_environments",  # per-tenant venvs
    "archives",
}

ROLE_DECORATORS = {
    "admin_required",
    "developer_required",
    "login_required",
    "api_key_or_session_required",
    "role_required",
}

FASTAPI_METHODS = {"get", "post", "put", "delete", "patch", "options", "head"}

BASELINE_PATH = THIS_DIR / "baseline.json"
REPORT_PATH = THIS_DIR / "REPORT.md"

# Critical routes that we never want to slip out of the test net.
CRITICAL_ROUTES = [
    ("POST", "/login"),
    ("GET", "/logout"),
    ("POST", "/api/workflow/run"),
    ("GET", "/api/compliance/retailers"),
    ("POST", "/api/compliance/retailers"),
    ("GET", "/api/compliance/retailers/{retailer_id}"),
    ("GET", "/api/ops/kpis"),
    ("POST", "/api/sessions"),
    ("GET", "/api/sessions"),
    ("POST", "/chat"),
    ("GET", "/health"),
    ("POST", "/api/integrations/{integration_id}/test"),
    ("GET", "/admin/identity/providers"),
    ("POST", "/admin/identity/providers"),
    ("DELETE", "/admin/identity/providers/{provider_id}"),
]

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RouteDef:
    file: str
    line: int
    method: str
    path: str  # normalized: /api/foo/{id}
    raw_path: str  # original: /api/foo/<int:id>
    function_name: str
    blueprint_or_app: str  # name of the @decorator target (app, router, compliance_bp, ...)

    def key(self) -> str:
        return f"{self.method} {self.path}"


@dataclass(frozen=True)
class EnvVarRef:
    name: str
    file: str
    line: int


@dataclass(frozen=True)
class DecoratorUse:
    decorator: str
    file: str
    line: int
    function_name: str


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _should_skip_dir(p: Path, project_root: Path) -> bool:
    name = p.name
    if name in SKIP_DIR_NAMES:
        return True
    try:
        rel = p.relative_to(project_root).as_posix()
    except ValueError:
        return False
    first = rel.split("/", 1)[0]
    return first in SKIP_PATH_PREFIXES


def iter_python_files(root: Path, project_root: Optional[Path] = None) -> List[Path]:
    project_root = project_root or root
    out: List[Path] = []
    if not root.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        # prune in-place
        dirnames[:] = [
            d for d in dirnames if not _should_skip_dir(dp / d, project_root)
        ]
        for fn in filenames:
            if fn.endswith(".py") and not fn.endswith(".pyc"):
                out.append(dp / fn)
    return out


def iter_test_files(roots: List[Path]) -> List[Path]:
    out: List[Path] = []
    seen: set = set()
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
            # Never include the coverage_gaps folder in its own corpus —
            # otherwise the generated REPORT.md would "cover" every route.
            if Path(dirpath).resolve() == THIS_DIR:
                continue
            for fn in filenames:
                low = fn.lower()
                if not low.endswith(
                    (".py", ".md", ".json", ".txt", ".yml", ".yaml")
                ):
                    continue
                # Skip our own generated artifacts.
                if fn in ("REPORT.md", "baseline.json"):
                    continue
                p = (Path(dirpath) / fn).resolve()
                if p in seen:
                    continue
                seen.add(p)
                out.append(Path(dirpath) / fn)
    return out


def _safe_parse(path: Path) -> Optional[ast.AST]:
    try:
        src = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        try:
            src = path.read_text(encoding="latin-1")
        except Exception:
            return None
    try:
        return ast.parse(src, filename=str(path))
    except (SyntaxError, ValueError):
        return None


def _str_const(node: ast.AST) -> Optional[str]:
    """Return the str value if node is a string Constant, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    # py3.7 compatibility
    if isinstance(node, ast.Str):  # type: ignore[attr-defined]
        return node.s  # type: ignore[attr-defined]
    return None


# Convert /api/agents/<int:id> -> /api/agents/{id}
_FLASK_PARAM_RE = re.compile(r"<(?:[a-zA-Z_][a-zA-Z0-9_]*:)?([a-zA-Z_][a-zA-Z0-9_]*)>")


def normalize_path(p: str) -> str:
    return _FLASK_PARAM_RE.sub(lambda m: "{" + m.group(1) + "}", p)


# ---------------------------------------------------------------------------
# Blueprint url_prefix discovery
# ---------------------------------------------------------------------------


def _find_blueprint_prefixes(tree: ast.AST) -> Dict[str, str]:
    """Find `name = Blueprint(..., url_prefix='...')` assignments in this file."""
    prefixes: Dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        target_name = node.targets[0].id
        val = node.value
        if not isinstance(val, ast.Call):
            continue
        func = val.func
        func_name = None
        if isinstance(func, ast.Name):
            func_name = func.id
        elif isinstance(func, ast.Attribute):
            func_name = func.attr
        if func_name not in ("Blueprint", "APIRouter"):
            continue
        for kw in val.keywords:
            if kw.arg in ("url_prefix", "prefix"):
                s = _str_const(kw.value)
                if s:
                    prefixes[target_name] = s
                    break
    return prefixes


# ---------------------------------------------------------------------------
# Route discovery
# ---------------------------------------------------------------------------


def _extract_decorator_route(
    dec: ast.AST,
) -> Optional[Tuple[str, str, List[str]]]:
    """
    If `dec` is a route-registering decorator call, return
    (target_name, path, methods_list). Otherwise None.

    Handles:
        @app.route('/p')                                  -> ('app', '/p', ['GET'])
        @app.route('/p', methods=['POST'])                -> ('app', '/p', ['POST'])
        @bp.route('/p', methods=['GET','POST'])
        @app.get('/p')                                    -> ('app', '/p', ['GET'])
        @router.post('/p')
    """
    if not isinstance(dec, ast.Call):
        return None
    func = dec.func
    if not isinstance(func, ast.Attribute):
        return None
    if not isinstance(func.value, ast.Name):
        # could be nested attribute, skip
        return None
    target = func.value.id
    attr = func.attr

    # Path is always the first positional arg.
    if not dec.args:
        return None
    path = _str_const(dec.args[0])
    if not path:
        return None

    if attr == "route":
        methods: List[str] = ["GET"]
        for kw in dec.keywords:
            if kw.arg == "methods":
                if isinstance(kw.value, (ast.List, ast.Tuple)):
                    extracted = []
                    for elt in kw.value.elts:
                        s = _str_const(elt)
                        if s:
                            extracted.append(s.upper())
                    if extracted:
                        methods = extracted
        return target, path, methods

    if attr in FASTAPI_METHODS:
        return target, path, [attr.upper()]

    return None


def discover_routes(source_root: Path) -> List[RouteDef]:
    routes: List[RouteDef] = []
    files = iter_python_files(source_root, project_root=source_root)
    for f in files:
        tree = _safe_parse(f)
        if tree is None:
            continue
        prefixes = _find_blueprint_prefixes(tree)
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            for dec in node.decorator_list:
                extracted = _extract_decorator_route(dec)
                if extracted is None:
                    continue
                target, raw_path, methods = extracted
                prefix = prefixes.get(target, "")
                full_raw = (prefix.rstrip("/") + raw_path) if prefix else raw_path
                if not full_raw.startswith("/"):
                    full_raw = "/" + full_raw
                norm = normalize_path(full_raw)
                for m in methods:
                    routes.append(
                        RouteDef(
                            file=str(f.relative_to(source_root)).replace("\\", "/"),
                            line=dec.lineno,
                            method=m.upper(),
                            path=norm,
                            raw_path=full_raw,
                            function_name=node.name,
                            blueprint_or_app=target,
                        )
                    )
    # de-dup
    seen = set()
    out: List[RouteDef] = []
    for r in routes:
        k = (r.file, r.line, r.method, r.path)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Env-var discovery
# ---------------------------------------------------------------------------


def _is_attr_chain(node: ast.AST, chain: Tuple[str, ...]) -> bool:
    """Check whether `node` matches `chain[0].chain[1].chain[2]...`."""
    parts = list(chain)
    cur = node
    while len(parts) > 1:
        if not isinstance(cur, ast.Attribute):
            return False
        if cur.attr != parts[-1]:
            return False
        parts.pop()
        cur = cur.value
    return isinstance(cur, ast.Name) and cur.id == parts[0]


def discover_env_vars(source_root: Path) -> List[EnvVarRef]:
    refs: List[EnvVarRef] = []
    files = iter_python_files(source_root, project_root=source_root)
    for f in files:
        tree = _safe_parse(f)
        if tree is None:
            continue
        for node in ast.walk(tree):
            name = None
            lineno = getattr(node, "lineno", 0)
            # os.getenv('NAME')
            if isinstance(node, ast.Call):
                func = node.func
                # os.getenv(...)
                if _is_attr_chain(func, ("os", "getenv")):
                    if node.args:
                        name = _str_const(node.args[0])
                # os.environ.get(...)
                elif (
                    isinstance(func, ast.Attribute)
                    and func.attr == "get"
                    and _is_attr_chain(func.value, ("os", "environ"))
                ):
                    if node.args:
                        name = _str_const(node.args[0])
            elif isinstance(node, ast.Subscript):
                # os.environ['NAME']
                if _is_attr_chain(node.value, ("os", "environ")):
                    # py3.9+: slice is the expression itself
                    sl = node.slice
                    if isinstance(sl, ast.Index):  # type: ignore[attr-defined]
                        sl = sl.value  # type: ignore[attr-defined]
                    name = _str_const(sl)

            if not name:
                continue
            if name.startswith("__") and name.endswith("__"):
                continue
            refs.append(
                EnvVarRef(
                    name=name,
                    file=str(f.relative_to(source_root)).replace("\\", "/"),
                    line=lineno,
                )
            )
    return refs


# ---------------------------------------------------------------------------
# Role decorator discovery
# ---------------------------------------------------------------------------


def _decorator_name(dec: ast.AST) -> Optional[str]:
    if isinstance(dec, ast.Call):
        return _decorator_name(dec.func)
    if isinstance(dec, ast.Name):
        return dec.id
    if isinstance(dec, ast.Attribute):
        return dec.attr
    return None


def discover_role_decorators(source_root: Path) -> List[DecoratorUse]:
    out: List[DecoratorUse] = []
    files = iter_python_files(source_root, project_root=source_root)
    for f in files:
        tree = _safe_parse(f)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                name = _decorator_name(dec)
                if name and name in ROLE_DECORATORS:
                    out.append(
                        DecoratorUse(
                            decorator=name,
                            file=str(f.relative_to(source_root)).replace(
                                "\\", "/"
                            ),
                            line=getattr(dec, "lineno", 0),
                            function_name=node.name,
                        )
                    )
    return out


# ---------------------------------------------------------------------------
# Test corpus matching
# ---------------------------------------------------------------------------


# Strip "{var}" segments out of a path so we can substring-match against
# f-strings like f"/api/agents/{aid}/...".
_PARAM_TOKEN = re.compile(r"\{[^/}]+\}")


def _candidate_substrings(path: str) -> List[str]:
    """Generate the substrings we'll look for in test files."""
    # We don't try to be clever — just take the longest static prefix and
    # any static segment after the first param. In practice substring
    # match on the longest static prefix is robust enough.
    pieces = _PARAM_TOKEN.split(path)
    candidates = []
    # longest static prefix (before first {param})
    if pieces and pieces[0]:
        prefix = pieces[0].rstrip("/")
        if prefix and prefix != "/":
            candidates.append(prefix)
    # full normalized path (so docs/JSON that mention /foo/{id} still match)
    candidates.append(path)
    # raw flask form, as a courtesy
    return candidates


def _load_test_corpus(test_roots: List[Path]) -> List[Tuple[Path, str]]:
    corpus: List[Tuple[Path, str]] = []
    for f in iter_test_files(test_roots):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        corpus.append((f, text))
    return corpus


def find_route_references_in_tests(
    test_roots: List[Path], routes: List[RouteDef]
) -> Dict[str, List[str]]:
    """
    Returns a dict keyed by RouteDef.key() -> list of test files that
    reference the route's path. Capped at 5 per route.
    """
    corpus = _load_test_corpus(test_roots)
    out: Dict[str, List[str]] = {}
    # Pre-compute candidates per route once.
    route_candidates: Dict[str, List[str]] = {}
    for r in routes:
        cands = set(_candidate_substrings(r.path))
        cands.add(r.raw_path)
        # also the flask form
        route_candidates[r.key()] = [c for c in cands if c and len(c) >= 3]

    for r in routes:
        cands = route_candidates[r.key()]
        hits: List[str] = []
        for path, text in corpus:
            if any(c in text for c in cands):
                hits.append(str(path))
                if len(hits) >= 5:
                    break
        out[r.key()] = hits
    return out


def find_env_var_references_in_tests(
    test_roots: List[Path], env_vars: List[EnvVarRef]
) -> Dict[str, List[str]]:
    corpus = _load_test_corpus(test_roots)
    names = sorted({e.name for e in env_vars})
    out: Dict[str, List[str]] = {}
    for name in names:
        # We require the literal name to appear in test source. We don't
        # care if it's quoted or not — test files reference env vars by
        # name in many ways (monkeypatch.setenv, os.environ[...], docs).
        hits: List[str] = []
        for path, text in corpus:
            if name in text:
                hits.append(str(path))
                if len(hits) >= 5:
                    break
        out[name] = hits
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _route_risk_score(r: RouteDef) -> int:
    """Higher = more concerning when untested. Used to sort the report."""
    score = 0
    p = r.path.lower()
    m = r.method
    if m in ("POST", "PUT", "DELETE", "PATCH"):
        score += 5
    if "/admin" in p:
        score += 4
    if "/api/" in p:
        score += 2
    for verb in ("delete", "remove", "purge", "drop", "reset", "wipe"):
        if verb in p:
            score += 4
    if "provision" in p or "execute" in p or "/run" in p:
        score += 2
    return score


def report(
    routes: List[RouteDef],
    env_vars: List[EnvVarRef],
    decorators: List[DecoratorUse],
    route_refs: Dict[str, List[str]],
    env_refs: Dict[str, List[str]],
) -> str:
    lines: List[str] = []

    # ---- Routes
    untested_routes = [r for r in routes if not route_refs.get(r.key())]
    tested_routes = [r for r in routes if route_refs.get(r.key())]

    # Sort untested by risk score desc, then by path.
    untested_sorted = sorted(
        untested_routes, key=lambda r: (-_route_risk_score(r), r.file, r.path)
    )

    # ---- Env vars
    env_names = sorted({e.name for e in env_vars})
    untested_env = [n for n in env_names if not env_refs.get(n)]

    # ---- Role decorators
    dec_counter = Counter(d.decorator for d in decorators)

    # ---- Files with the most untested routes
    file_untested_counter = Counter(r.file for r in untested_routes)

    lines.append("# Coverage Gap Report")
    lines.append("")
    lines.append("Static analysis of production surfaces vs. the test corpus.")
    lines.append("")
    lines.append("## Headline numbers")
    lines.append("")
    lines.append(f"- Total routes discovered: **{len(routes)}**")
    lines.append(
        f"- Routes with at least one test reference: **{len(tested_routes)}**"
    )
    lines.append(f"- **Untested routes: {len(untested_routes)}**")
    if routes:
        pct = 100.0 * len(tested_routes) / len(routes)
        lines.append(f"- Route coverage: **{pct:.1f}%**")
    lines.append("")
    lines.append(f"- Total env-var references: **{len(env_vars)}**")
    lines.append(f"- Distinct env vars: **{len(env_names)}**")
    lines.append(f"- **Untested env vars: {len(untested_env)}**")
    lines.append("")
    lines.append("- Role-decorator usages:")
    for name in sorted(ROLE_DECORATORS):
        lines.append(f"  - `@{name}`: {dec_counter.get(name, 0)}")
    lines.append("")

    # ---- Top files
    lines.append("## Files with the most untested routes")
    lines.append("")
    if not file_untested_counter:
        lines.append("(none)")
    else:
        for fname, cnt in file_untested_counter.most_common(15):
            lines.append(f"- `{fname}` — {cnt} untested")
    lines.append("")

    # ---- Untested route list
    lines.append("## Untested routes (sorted by risk score)")
    lines.append("")
    if not untested_sorted:
        lines.append("None! Every discovered route appears in at least one test file.")
    else:
        lines.append("| Method | Path | Function | File:Line | Risk |")
        lines.append("| --- | --- | --- | --- | --- |")
        for r in untested_sorted:
            lines.append(
                f"| {r.method} | `{r.path}` | `{r.function_name}` "
                f"| `{r.file}:{r.line}` | {_route_risk_score(r)} |"
            )
    lines.append("")

    # ---- Tested routes (small sample)
    lines.append("## Tested routes (sample, with first 5 referencing files)")
    lines.append("")
    if not tested_routes:
        lines.append("(none)")
    else:
        # Group by method then path
        sample = sorted(tested_routes, key=lambda r: (r.method, r.path))[:40]
        for r in sample:
            refs = route_refs.get(r.key(), [])[:5]
            short = [Path(p).name for p in refs]
            lines.append(
                f"- **{r.method}** `{r.path}` ({r.function_name}) — "
                f"refs: {', '.join(short) if short else '—'}"
            )
        if len(tested_routes) > 40:
            lines.append("")
            lines.append(
                f"...and {len(tested_routes) - 40} more (omitted for brevity)."
            )
    lines.append("")

    # ---- Untested env vars
    lines.append("## Untested env vars")
    lines.append("")
    if not untested_env:
        lines.append("None — every env var read by production code has at least one test reference.")
    else:
        # Group by file of first occurrence
        env_files: Dict[str, List[str]] = defaultdict(list)
        first_seen: Dict[str, EnvVarRef] = {}
        for e in env_vars:
            first_seen.setdefault(e.name, e)
        for name in sorted(untested_env):
            e = first_seen.get(name)
            env_files[e.file if e else "?"].append(name)
        for fname in sorted(env_files):
            lines.append(f"### `{fname}`")
            lines.append("")
            for n in env_files[fname]:
                lines.append(f"- `{n}`")
            lines.append("")
    lines.append("")

    # ---- Role decorator coverage detail
    lines.append("## Role decorators in use")
    lines.append("")
    by_dec = defaultdict(list)
    for d in decorators:
        by_dec[d.decorator].append(d)
    for name in sorted(by_dec):
        uses = by_dec[name]
        lines.append(f"### `@{name}` — {len(uses)} use(s)")
        for d in uses[:20]:
            lines.append(f"- `{d.file}:{d.line}` `{d.function_name}`")
        if len(uses) > 20:
            lines.append(f"- ... and {len(uses) - 20} more.")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public convenience
# ---------------------------------------------------------------------------


def run_all(
    source_root: Optional[Path] = None,
    test_roots: Optional[List[Path]] = None,
) -> dict:
    source_root = source_root or PROJECT_ROOT
    test_roots = test_roots or DEFAULT_TEST_ROOTS
    routes = discover_routes(source_root)
    env_vars = discover_env_vars(source_root)
    decorators = discover_role_decorators(source_root)
    route_refs = find_route_references_in_tests(test_roots, routes)
    env_refs = find_env_var_references_in_tests(test_roots, env_vars)
    return {
        "routes": routes,
        "env_vars": env_vars,
        "decorators": decorators,
        "route_refs": route_refs,
        "env_refs": env_refs,
    }


def untested_route_count(data: dict) -> int:
    return sum(1 for r in data["routes"] if not data["route_refs"].get(r.key()))


def write_baseline(count: int, path: Path = BASELINE_PATH) -> None:
    path.write_text(
        json.dumps({"untested_routes": count}, indent=2), encoding="utf-8"
    )


def read_baseline(path: Path = BASELINE_PATH) -> Optional[int]:
    if not path.exists():
        return None
    try:
        return int(json.loads(path.read_text(encoding="utf-8"))["untested_routes"])
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="AI Hub coverage gap detector — finds routes/env-vars/role decorators with no test references."
    )
    ap.add_argument("--report", action="store_true", help="Print markdown report.")
    ap.add_argument("--output", default=None, help="Write report to this path.")
    ap.add_argument(
        "--update-baseline",
        action="store_true",
        help="Write current untested-route count to baseline.json.",
    )
    ap.add_argument(
        "--source-root", default=None, help="Root of source to scan (default: project root)"
    )
    args = ap.parse_args(argv)

    src = Path(args.source_root) if args.source_root else PROJECT_ROOT
    data = run_all(source_root=src)

    if args.update_baseline:
        n = untested_route_count(data)
        write_baseline(n)
        print(f"Baseline written: {n} untested routes -> {BASELINE_PATH}")

    if args.report or args.output:
        md = report(
            data["routes"],
            data["env_vars"],
            data["decorators"],
            data["route_refs"],
            data["env_refs"],
        )
        if args.output:
            Path(args.output).write_text(md, encoding="utf-8")
            print(f"Report written to {args.output}")
        else:
            print(md)

    if not (args.report or args.output or args.update_baseline):
        # default: just print headline numbers
        n_routes = len(data["routes"])
        n_untested = untested_route_count(data)
        n_env = len({e.name for e in data["env_vars"]})
        n_env_untested = sum(
            1 for name in {e.name for e in data["env_vars"]} if not data["env_refs"].get(name)
        )
        print(
            f"routes={n_routes} untested={n_untested} "
            f"env_vars={n_env} env_untested={n_env_untested}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(_main())
