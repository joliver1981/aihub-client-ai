"""
prompt_registry.py
------------------
Read-only DISCOVERY of every LLM system/user prompt defined in this codebase.

The catalog is built by a STATIC AST SCAN of source files on disk. Nothing is
imported, so this is safe to call from the web process:
  * no heavy service dependencies get pulled in (langgraph, playwright, torch...)
  * no module-level side effects are triggered
  * it works even for prompts that live in services this process never runs

Two tiers of entry are produced:

  EDITABLE   A module-level constant assigned a PLAIN STRING LITERAL, living in
             one of the curated files in EDITABLE_SOURCES. These are the ~160
             prompts the admin screen can override (see prompt_overrides.py).

  READ-ONLY  Everything else we can find, surfaced purely so an admin can trace
             where a prompt lives:
               - f-string / computed constants  (value depends on runtime data)
               - prompt constants in files not yet enabled for editing
               - inline prompts passed straight into a call site
                 (system="You are ...")
               - external .txt/.md prompt assets

Nothing here mutates anything. This module is import-safe and side-effect free.

Used by:
    - system_prompts_admin_routes.py : GET /settings/api/system-prompts
    - prompt_overrides.py            : allow-list of overridable keys
"""
from __future__ import annotations

import ast
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# App root resolution — mirrors model_overrides.py:41-45 so frozen PyInstaller
# builds resolve to the install root rather than the temp extraction dir.
# -----------------------------------------------------------------------------
_APP_ROOT = Path(os.getenv('APP_ROOT', '')) if os.getenv('APP_ROOT') else None
if _APP_ROOT is None or not _APP_ROOT.is_dir():
    _APP_ROOT = Path(__file__).resolve().parent

APP_ROOT = _APP_ROOT


# -----------------------------------------------------------------------------
# Curated source lists
# -----------------------------------------------------------------------------
# Files whose plain-string prompt constants are EDITABLE. A file only belongs
# here once a matching override hook has been appended to the bottom of it
# (see prompt_overrides.apply_prompt_overrides). Keep the two in sync.
EDITABLE_SOURCES: Tuple[str, ...] = (
    'system_prompts.py',
    'data_prompts.py',
    'command_center_service/cc_config.py',
    'builder_service/builder_config.py',
    'builder_data/builder_data_config.py',
    'builder_agent/ai/prompts.py',
    'builder_data/ai/prompts.py',
    'command_center_service/graph/nodes.py',
    'builder_service/graph/nodes.py',
    'builder_data/graph/nodes.py',
    'command_center/memory/route_memory.py',
    'universal_assistant.py',
    'CommandGenerator.py',
    'TextChunker_LLM.py',
    'app_knowledge_api.py',
)

# External prompt assets (read-only in the UI).
EXTERNAL_PROMPT_FILES: Tuple[str, ...] = (
    'command_generator_system_prompt.txt',
    'fix_prompt.md',
    'fix_schedules_prompt.md',
)

# Directories never worth scanning — build output, caches, vendored deps,
# tests, and generated/tenant code. Without these the catalog fills up with
# the bundled CPython standard library and per-run automation copies.
_EXCLUDED_DIR_PARTS = frozenset({
    '.git', '.venv', 'venv', 'env', 'node_modules', '__pycache__',
    'site-packages', 'dist', 'build', 'out', '_pkg_cache', '.pytest_cache',
    'model_tester', 'migrations', '.idea', '.vscode', 'htmlcov', '.mypy_cache',
    # Bundled interpreters shipped for the code-interpreter / agent sandboxes.
    'agent_environments', 'python-bundle', 'Scripts',
    # Test corpora and suites — not product prompts.
    'tests', 'tests_v2', 'test_human', 'testing', 'fixtures',
    # Generated automation working dirs.
    'runs', 'versions', 'checkpoints', 'backup', 'backups', 'archive',
})

# Directory-name PREFIXES to skip (tenant sandboxes are named tenant_<guid>).
_EXCLUDED_DIR_PREFIXES = ('tenant_', 'temp_', 'tmp_', '~')

# Individual files that would otherwise catalog themselves.
_EXCLUDED_FILES = frozenset({
    'prompt_registry.py', 'prompt_overrides.py', 'user_prompts.py',
})

# Don't try to parse anything enormous.
_MAX_SCAN_BYTES = 3 * 1024 * 1024


# -----------------------------------------------------------------------------
# What counts as a "prompt" constant
# -----------------------------------------------------------------------------
# UPPER_SNAKE, optionally leading-underscore (graph/nodes.py uses _FOO_PROMPT).
_PROMPT_NAME_RE = re.compile(r'^_{0,2}[A-Z][A-Z0-9_]*$')
_PROMPT_NAME_TOKENS = ('PROMPT', 'SYSTEM', 'INSTRUCTION', 'TEMPLATE', 'OBJECTIVE')

# A real prompt is prose: it has whitespace and some length. This keeps things
# like SYSTEM_NAME = "aihub" or PROMPT_TIMEOUT = 30 out of the catalog.
_MIN_PROMPT_CHARS = 20

# Keyword arguments that carry an inline prompt at a call site.
_INLINE_PROMPT_KWARGS = frozenset({
    'system', 'system_prompt', 'system_message', 'instructions', 'sys_prompt',
})

# Only simple identifier placeholders — these are the ones str.format() would
# require. Deliberately does NOT match raw JSON braces like {"commands": ...},
# which appear in many prompts and are handled with .replace() instead.
_PLACEHOLDER_RE = re.compile(r'(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})')


def extract_placeholders(text: str) -> List[str]:
    """Return the sorted, de-duplicated {identifier} placeholders in `text`.

    Used both for display and — critically — by prompt_overrides to refuse an
    override that would drop a placeholder the call site passes to .format().
    """
    if not isinstance(text, str):
        return []
    return sorted(set(_PLACEHOLDER_RE.findall(text)))


def _is_prompt_name(name: str) -> bool:
    if not name or not _PROMPT_NAME_RE.match(name):
        return False
    return any(tok in name for tok in _PROMPT_NAME_TOKENS)


def _looks_like_prompt_text(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return len(stripped) >= _MIN_PROMPT_CHARS and any(c.isspace() for c in stripped)


def _classify_kind(name: str) -> str:
    """'system' for the system-role text, 'template' for the user-message body."""
    if name.endswith('_SYSTEM') or 'SYSTEM_PROMPT' in name or name.endswith('_SYSTEM_PROMPT'):
        return 'system'
    return 'template'


def _service_for(rel_path: str) -> str:
    """Human-friendly grouping used by the UI's service filter."""
    p = rel_path.replace('\\', '/')
    if p.startswith('command_center_service/') or p.startswith('command_center/'):
        return 'Command Center'
    if p.startswith('builder_service/'):
        return 'Workflow Builder'
    if p.startswith('builder_data/'):
        return 'Data Builder'
    if p.startswith('builder_agent/'):
        return 'Builder Agent'
    if p.startswith('data_collection_agent/'):
        return 'Data Collection Agent'
    if p.startswith('browser_use_service/'):
        return 'Browser Automation'
    if p.startswith('nlq_agentic/'):
        return 'NLQ Engine'
    if p in ('system_prompts.py', 'data_prompts.py', 'user_prompts.py'):
        return 'Core Prompt Registry'
    return 'Application'


def make_key(rel_path: str, name: str) -> str:
    """Catalog key. MUST be module-scoped: several services each define their
    own INTENT_CLASSIFICATION_PROMPT, so a bare name is ambiguous."""
    return f"{rel_path.replace(chr(92), '/')}::{name}"


# -----------------------------------------------------------------------------
# AST helpers
# -----------------------------------------------------------------------------
def _string_literal_value(node: ast.AST) -> Optional[str]:
    """Return the str value if `node` is a plain string literal, else None.

    CPython folds adjacent string literals ("a" "b") into a single Constant at
    parse time, so implicit concatenation is handled for free. Explicit `+`
    concatenation and f-strings deliberately return None — they are computed,
    and we never allow those to be overridden.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _computed_reason(node: ast.AST) -> str:
    if isinstance(node, ast.JoinedStr):
        return 'f-string — value is interpolated at runtime'
    if isinstance(node, ast.BinOp):
        return 'built by concatenation at runtime'
    if isinstance(node, ast.Call):
        return 'built by a function call at runtime'
    if isinstance(node, (ast.Name, ast.Attribute)):
        return 'aliases another value'
    return 'computed expression — not a fixed string'


def _assign_targets(node: ast.AST) -> List[str]:
    names: List[str] = []
    if isinstance(node, ast.Assign):
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                names.append(tgt.id)
    elif isinstance(node, ast.AnnAssign):
        if isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return names


def _read_source(path: Path) -> Optional[str]:
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > _MAX_SCAN_BYTES:
            return None
        return path.read_text(encoding='utf-8', errors='replace')
    except OSError as e:
        logger.debug(f"prompt_registry: cannot read {path}: {e}")
        return None


def _parse(source: str, path: Path) -> Optional[ast.Module]:
    try:
        return ast.parse(source, filename=str(path))
    except (SyntaxError, ValueError) as e:
        logger.debug(f"prompt_registry: cannot parse {path}: {e}")
        return None


# -----------------------------------------------------------------------------
# Per-file scanners
# -----------------------------------------------------------------------------
def _scan_named_constants(rel_path: str, tree: ast.Module, source: str,
                          editable_file: bool) -> List[Dict[str, Any]]:
    """Module-level prompt constants.

    Only TOP-LEVEL assignments (tree.body) are eligible to be editable. A name
    bound inside an if/try/function is not reliably overridable by a
    bottom-of-module hook, so it is catalogued read-only instead.
    """
    entries: List[Dict[str, Any]] = []
    seen_top_level: set = set()

    for node in tree.body:
        for name in _assign_targets(node):
            if not _is_prompt_name(name):
                continue
            value_node = node.value
            text = _string_literal_value(value_node)
            if text is not None and _looks_like_prompt_text(text):
                seen_top_level.add(name)
                entries.append({
                    'key': make_key(rel_path, name),
                    'name': name,
                    'module': rel_path,
                    'service': _service_for(rel_path),
                    'kind': _classify_kind(name),
                    'category': 'named',
                    'editable': bool(editable_file),
                    'reason': '' if editable_file else (
                        'this file is not enabled for admin editing — '
                        'change it in code or via config/.env'),
                    'placeholders': extract_placeholders(text),
                    'source_path': rel_path,
                    'line': getattr(node, 'lineno', 0),
                    'default_text': text,
                    'char_count': len(text),
                })
            elif text is None:
                seen_top_level.add(name)
                preview = _computed_preview(value_node, source)
                entries.append({
                    'key': make_key(rel_path, name),
                    'name': name,
                    'module': rel_path,
                    'service': _service_for(rel_path),
                    'kind': _classify_kind(name),
                    'category': 'named',
                    'editable': False,
                    'reason': _computed_reason(value_node),
                    'placeholders': extract_placeholders(preview),
                    'source_path': rel_path,
                    'line': getattr(node, 'lineno', 0),
                    'default_text': preview,
                    'char_count': len(preview),
                })

    # Prompt-named constants bound somewhere other than module top level.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        for name in _assign_targets(node):
            if not _is_prompt_name(name) or name in seen_top_level:
                continue
            text = _string_literal_value(node.value)
            if text is None or not _looks_like_prompt_text(text):
                continue
            seen_top_level.add(name)
            entries.append({
                'key': make_key(rel_path, name),
                'name': name,
                'module': rel_path,
                'service': _service_for(rel_path),
                'kind': _classify_kind(name),
                'category': 'named',
                'editable': False,
                'reason': 'defined inside a function/conditional block — requires code change',
                'placeholders': extract_placeholders(text),
                'source_path': rel_path,
                'line': getattr(node, 'lineno', 0),
                'default_text': text,
                'char_count': len(text),
            })

    return entries


def _inline_entry(rel_path: str, line: int, label: str, snippet: str,
                  reason: str) -> Dict[str, Any]:
    return {
        'key': f"{rel_path}::inline@{line}",
        'name': f"{label} (line {line})",
        'module': rel_path,
        'service': _service_for(rel_path),
        'kind': 'system',
        'category': 'inline',
        'editable': False,
        'reason': reason,
        'placeholders': extract_placeholders(snippet),
        'source_path': rel_path,
        'line': line,
        'default_text': snippet,
        'char_count': len(snippet),
    }


def _scan_inline_prompts(rel_path: str, tree: ast.Module,
                         claimed_lines: Optional[set] = None) -> List[Dict[str, Any]]:
    """Anonymous prompts written straight into the code.

    Two signatures, both read-only:
      1. a prompt kwarg at a call site  -> system="You are ..."
      2. any string literal / f-string that opens with "You are ..." — the most
         reliable marker of a prompt in this codebase, and the one that catches
         prompts assigned to local variables inside functions.

    These are the hardest prompts to trace by hand, which is exactly why they
    are worth surfacing even though they can never be safely edited from a UI.

    `claimed_lines` holds lines already represented by a named constant, so a
    prompt is never listed twice.
    """
    entries: List[Dict[str, Any]] = []
    seen_lines: set = set(claimed_lines or ())

    def _emit(line: int, label: str, snippet: str, reason: str) -> None:
        if not line or line in seen_lines:
            return
        seen_lines.add(line)
        entries.append(_inline_entry(rel_path, line, label, snippet, reason))

    # 1. Explicit prompt kwargs at a call site.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg not in _INLINE_PROMPT_KWARGS:
                continue
            text = _string_literal_value(kw.value)
            is_fstring = isinstance(kw.value, ast.JoinedStr)
            if text is None and not is_fstring:
                continue
            if text is not None and not _looks_like_prompt_text(text):
                continue
            line = getattr(kw.value, 'lineno', getattr(node, 'lineno', 0))
            if is_fstring:
                _emit(line, f'{kw.arg}=', _fstring_preview(kw.value),
                      'inline f-string at call site — requires code change')
            else:
                _emit(line, f'{kw.arg}=', text or '',
                      'inline literal at call site — requires code change')

    # 2. Any "You are ..." string anywhere in the file.
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            snippet = node.value
        elif isinstance(node, ast.JoinedStr):
            snippet = _fstring_preview(node)
        else:
            continue
        stripped = snippet.lstrip()
        if len(stripped) < 40 or not stripped[:9].lower().startswith('you are'):
            continue
        _emit(getattr(node, 'lineno', 0), 'inline prompt', snippet,
              'inline prompt in code — requires code change')

    return entries


def _computed_preview(node: ast.AST, source: str) -> str:
    """Best-effort readable rendering of a non-literal prompt value.

    f-strings render with {expr} in place of each interpolation; anything else
    falls back to its source text so the admin can still see what it is.
    """
    if isinstance(node, ast.JoinedStr):
        return _fstring_preview(node)
    try:
        segment = ast.get_source_segment(source, node)
        if segment:
            return segment
    except Exception:
        pass
    try:
        return ast.unparse(node)
    except Exception:
        return ''


def _fstring_preview(node: ast.JoinedStr) -> str:
    """Render an f-string back to readable text, with {…} for each expression."""
    parts: List[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        elif isinstance(value, ast.FormattedValue):
            expr = ''
            try:
                expr = ast.unparse(value.value)
            except Exception:
                expr = '...'
            parts.append('{' + expr + '}')
    return ''.join(parts)


def _scan_external_files() -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for rel in EXTERNAL_PROMPT_FILES:
        path = APP_ROOT / rel
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        entries.append({
            'key': f"{rel}::<file>",
            'name': rel,
            'module': rel,
            'service': 'External Prompt Files',
            'kind': 'system',
            'category': 'external',
            'editable': False,
            'reason': 'external prompt file — edit on disk',
            'placeholders': extract_placeholders(text),
            'source_path': rel,
            'line': 1,
            'default_text': text,
            'char_count': len(text),
        })
    return entries


# -----------------------------------------------------------------------------
# Repo walk
# -----------------------------------------------------------------------------
def _iter_python_files() -> List[str]:
    """Repo-relative paths of every .py file worth scanning."""
    found: List[str] = []
    root = APP_ROOT
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in _EXCLUDED_DIR_PARTS
            and not d.startswith('.')
            and not d.startswith(_EXCLUDED_DIR_PREFIXES)
        ]
        for fn in filenames:
            if not fn.endswith('.py') or fn in _EXCLUDED_FILES:
                continue
            full = Path(dirpath) / fn
            try:
                rel = full.relative_to(root).as_posix()
            except ValueError:
                continue
            found.append(rel)
    return found


# -----------------------------------------------------------------------------
# Catalog build (cached)
# -----------------------------------------------------------------------------
_CACHE: Dict[str, Any] = {'signature': None, 'catalog': None}


def _cache_signature() -> Tuple:
    """Cheap fingerprint of the curated sources so edits invalidate the cache."""
    sig: List[Tuple[str, float, int]] = []
    for rel in EDITABLE_SOURCES + EXTERNAL_PROMPT_FILES:
        p = APP_ROOT / rel
        try:
            st = p.stat()
            sig.append((rel, st.st_mtime, st.st_size))
        except OSError:
            sig.append((rel, 0.0, -1))
    return tuple(sig)


def build_catalog(force_refresh: bool = False) -> Dict[str, Any]:
    """Scan the repo and return the full prompt catalog.

    Returns:
        {
          'entries':  [ {key, name, module, service, kind, category,
                         editable, reason, placeholders, source_path,
                         line, default_text, char_count}, ... ],
          'services': [ ... ],
          'stats':    {total, editable, read_only, named, inline, external},
          'app_root': str,
        }
    """
    if not force_refresh:
        sig = _cache_signature()
        if _CACHE['signature'] == sig and _CACHE['catalog'] is not None:
            return _CACHE['catalog']

    editable_set = {p.replace('\\', '/') for p in EDITABLE_SOURCES}
    entries: List[Dict[str, Any]] = []
    seen_keys: set = set()

    def _add(items: List[Dict[str, Any]]) -> None:
        for item in items:
            if item['key'] in seen_keys:
                continue
            seen_keys.add(item['key'])
            entries.append(item)

    def _scan_one(rel: str, editable_file: bool) -> None:
        path = APP_ROOT / rel
        source = _read_source(path)
        if source is None:
            if editable_file:
                logger.info(f"prompt_registry: editable source not found, skipping: {rel}")
            return
        # Cheap pre-filter: skip files with no prompt-ish signal at all.
        if not editable_file and ('PROMPT' not in source and 'SYSTEM' not in source
                                  and 'You are' not in source and 'system=' not in source):
            return
        tree = _parse(source, path)
        if tree is None:
            return
        named = _scan_named_constants(rel, tree, source, editable_file=editable_file)
        _add(named)
        # Never list a prompt twice: a named constant already covers its line.
        claimed = {e['line'] for e in named}
        _add(_scan_inline_prompts(rel, tree, claimed_lines=claimed))

    # 1. Curated editable sources first, so they win the key de-dupe.
    for rel in EDITABLE_SOURCES:
        _scan_one(rel, editable_file=True)

    # 2. Everything else in the repo, read-only, for traceability.
    for rel in _iter_python_files():
        if rel in editable_set:
            continue
        _scan_one(rel, editable_file=False)

    # 3. External .txt/.md assets.
    _add(_scan_external_files())

    entries.sort(key=lambda e: (e['service'], e['module'], e['name'].lower()))

    services = sorted({e['service'] for e in entries})
    catalog = {
        'entries': entries,
        'services': services,
        'modules': sorted({e['module'] for e in entries}),
        'stats': {
            'total': len(entries),
            'editable': sum(1 for e in entries if e['editable']),
            'read_only': sum(1 for e in entries if not e['editable']),
            'named': sum(1 for e in entries if e['category'] == 'named'),
            'inline': sum(1 for e in entries if e['category'] == 'inline'),
            'external': sum(1 for e in entries if e['category'] == 'external'),
        },
        'app_root': str(APP_ROOT),
    }

    _CACHE['signature'] = _cache_signature()
    _CACHE['catalog'] = catalog
    return catalog


def get_entry(key: str) -> Optional[Dict[str, Any]]:
    """Look up a single catalog entry by its `module::NAME` key."""
    for entry in build_catalog()['entries']:
        if entry['key'] == key:
            return entry
    return None


def editable_keys() -> Dict[str, Dict[str, Any]]:
    """{key: entry} for every prompt the admin UI is allowed to override.

    This is the allow-list consumed by prompt_overrides.save_overrides().
    """
    return {e['key']: e for e in build_catalog()['entries'] if e['editable']}


def keys_for_namespace(namespace: str) -> Dict[str, Dict[str, Any]]:
    """{NAME: entry} for one module — used when applying overrides at import."""
    ns = namespace.replace('\\', '/')
    return {
        e['name']: e
        for e in build_catalog()['entries']
        if e['editable'] and e['module'].replace('\\', '/') == ns
    }


if __name__ == '__main__':  # pragma: no cover - manual inspection helper
    import json as _json
    cat = build_catalog(force_refresh=True)
    print(_json.dumps(cat['stats'], indent=2))
    print(f"services: {cat['services']}")
    for _e in cat['entries'][:10]:
        print(f"  {_e['key']}  editable={_e['editable']}  ph={_e['placeholders']}")
