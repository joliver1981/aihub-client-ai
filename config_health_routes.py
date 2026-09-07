"""
config_health_routes.py
-----------------------
Read-only admin screen that answers one question: "is this install's
user_config.py actually doing anything, and is any of it a trap?"

    Page   GET  /settings/config-health
    API    GET  /settings/api/config-health        full report (JSON)

Admin-only (role >= 3), matching the other settings screens.

WHY THIS EXISTS
    config.py's load_user_config() compiles the WHOLE file in one pass and
    swallows any failure with a bare print():

        except Exception as e:
            print(f"Failed to load user_config: {e}")

    One bad line therefore discards EVERY setting in the file, with no error in
    the UI and nothing but a line in a service log nobody reads. A client ran
    with 0 of 23 settings applied and the only visible symptom was an unrelated
    error in chat. This panel makes that state obvious in one click.

NOTHING HERE WRITES. It never edits user_config.py, never sets an env var, and
never returns a secret value - only whether a secret-shaped key is present.

The file is read with ast.parse, NOT exec: the syntax check is identical (both
raise SyntaxError from the same compile step) but nothing in the file runs, and
the AST additionally gives per-key line numbers and duplicate detection that an
exec into a namespace cannot.

Register in app.py alongside the other blueprints:
    from config_health_routes import config_health_bp
    app.register_blueprint(config_health_bp)
"""
from __future__ import annotations

import ast
import io
import logging
import os
import re
import time

from flask import Blueprint, jsonify, render_template

from role_decorators import admin_required

logger = logging.getLogger(__name__)

config_health_bp = Blueprint('config_health', __name__, url_prefix='/settings')

# Key-name fragments that mean "this holds a credential". Used ONLY to decide
# whether to warn and to redact - a matching value is never read or returned.
_SECRET_HINTS = ('API_KEY', 'SECRET', 'PASSWORD', 'PWD', 'TOKEN', 'CONNECTION_STRING')

# Directories skipped by the consumer scan. agent_environments holds vendored
# per-tenant site-packages (tens of thousands of files) and would dominate.
_SCAN_SKIP_DIRS = {
    'agent_environments', 'node_modules', '__pycache__', '.git', 'dist',
    'build', 'temp', 'tmp', 'out', 'logs', 'uploads', 'test_human', 'tests_v2',
    'tests', 'automations', 'Output', '_history',
}
_SCAN_DIRS = ('.', 'routes', 'agent_service', 'command_center_service', 'doc_search_v2',
              'doc_search_v3', 'nlq_agentic')

# Consumer scan is process-cached: the source tree does not change under a
# running service, and a rescan is one page reload with ?rescan=1 away.
_consumer_cache: dict | None = None


# -----------------------------------------------------------------------------
# Locating and reading the file
# -----------------------------------------------------------------------------
def _app_root() -> str:
    """The directory config.py resolved as APP_ROOT - the only place it looks."""
    try:
        import config as cfg
        root = getattr(cfg, '_APP_ROOT', None)
        if root:
            return str(root)
    except Exception:
        pass
    return os.path.dirname(os.path.abspath(__file__))


def _user_config_path() -> str:
    return os.path.join(_app_root(), 'user_config.py')


def _read_source(path: str):
    """Return (text, error). Mirrors load_user_config's open() - text mode."""
    try:
        with io.open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read(), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


# -----------------------------------------------------------------------------
# Static analysis of the file
# -----------------------------------------------------------------------------
def _literal(node):
    """(value_repr, is_literal). Non-literals are reported, never evaluated."""
    try:
        return repr(ast.literal_eval(node)), True
    except Exception:
        try:
            return f"({type(node).__name__} expression)", False
        except Exception:
            return '(unreadable)', False


def _parse(text: str):
    """Return (assignments, syntax_error). assignments preserve file order."""
    try:
        tree = ast.parse(text, filename='user_config.py')
    except SyntaxError as e:
        return None, {
            'type': 'SyntaxError',
            'message': str(e.msg or e),
            'line': e.lineno,
            'offset': e.offset,
            # e.text is the offending source line, already newline-terminated
            'source_line': (e.text or '').rstrip('\n'),
        }
    except Exception as e:  # ValueError on NUL bytes, etc.
        return None, {'type': type(e).__name__, 'message': str(e),
                      'line': None, 'offset': None, 'source_line': ''}

    out = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                value_repr, is_literal = _literal(node.value)
                out.append({
                    'key': target.id,
                    'line': node.lineno,
                    'value': value_repr,
                    'is_literal': is_literal,
                    'node': node.value,
                })
    return out, None


def _looks_like_env_line(line: str) -> bool:
    """A bare KEY=value line pasted from a .env - the single most common break.

    .env has no quoting, so a path or bare word on the right is a SyntaxError in
    Python. Matches KEY=<something not starting with a quote/digit/bracket>.
    """
    return bool(re.match(r"^\s*[A-Z_][A-Z0-9_]*\s*=\s*[^\s'\"0-9\[({+-]", line or ''))


def _bare_bool_name(node) -> str | None:
    """`KEY=true` - valid syntax, NameError at exec, so AST-detectable only."""
    if isinstance(node, ast.Name) and node.id in ('true', 'false', 'null', 'none'):
        return node.id
    return None


# -----------------------------------------------------------------------------
# Consumer scan: how does the rest of the codebase READ each key?
# -----------------------------------------------------------------------------
def _scan_consumers(keys: list) -> dict:
    """Map key -> {'env': n, 'attr': n} by counting how consumers read it.

    This is the difference between a setting that works here and one that is
    silently inert, and it cannot be inferred from config.py alone:

      attr only  cfg.X / getattr(cfg,'X')  -> user_config.py DOES set it
      env only   os.getenv('X')            -> user_config.py does NOTHING
                                              (it sets a module attribute, never
                                               os.environ) - belongs in .env
      both       -> half-applies; the two halves can even carry different
                    defaults (WORKFLOW_TRAINING_CAPTURE_ENABLED does)

    Best-effort and advisory: a miss degrades to "unknown", never to a wrong
    claim, and any failure drops the column rather than breaking the page.
    """
    if not keys:
        return {}
    env_pat = re.compile(
        r"os\.(?:getenv|environ\.get)\(\s*['\"](" + '|'.join(map(re.escape, keys)) + r")['\"]")
    attr_pat = re.compile(
        r"(?:\bcfg\.|\b_cfg\.|getattr\(\s*(?:cfg|_cfg|config|current_module)\s*,\s*['\"])("
        + '|'.join(map(re.escape, keys)) + r")\b")

    counts = {k: {'env': 0, 'attr': 0} for k in keys}
    root = _app_root()
    for rel in _SCAN_DIRS:
        d = os.path.join(root, rel) if rel != '.' else root
        if not os.path.isdir(d):
            continue
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            if not name.endswith('.py') or name in ('config.py', 'user_config.py',
                                                    'config_health_routes.py'):
                continue
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            try:
                with io.open(p, 'r', encoding='utf-8', errors='ignore') as f:
                    src = f.read()
            except OSError:
                continue
            for m in env_pat.finditer(src):
                counts[m.group(1)]['env'] += 1
            for m in attr_pat.finditer(src):
                counts[m.group(1)]['attr'] += 1
    return counts


def _consumers_for(keys: list, rescan: bool) -> dict:
    global _consumer_cache
    if rescan or _consumer_cache is None:
        try:
            _consumer_cache = _scan_consumers(keys)
        except Exception as e:
            logger.warning(f"[config-health] consumer scan failed, omitting: {e}")
            _consumer_cache = {}
    return _consumer_cache


# -----------------------------------------------------------------------------
# The report
# -----------------------------------------------------------------------------
def _finding(level, title, detail, fix=None, line=None):
    return {'level': level, 'title': title, 'detail': detail, 'fix': fix, 'line': line}


def build_report(rescan: bool = False) -> dict:
    """Everything the panel renders. Never raises; never writes; never leaks a value."""
    import config as cfg

    path = _user_config_path()
    report = {
        'path': path,
        'exists': os.path.isfile(path),
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'loaded': False,
        'error': None,
        'keys': [],
        'findings': [],
        'counts': {'applied': 0, 'inert': 0, 'unknown': 0, 'total': 0},
    }

    if not report['exists']:
        report['findings'].append(_finding(
            'info', 'No user_config.py on this install',
            f"config.py looked for it at {path} and found nothing, so every "
            f"setting is coming from .env or the built-in default.",
            fix="Nothing to do unless you expected overrides here."))
        return report

    try:
        report['mtime'] = time.strftime(
            '%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(path)))
    except OSError:
        report['mtime'] = None

    text, read_err = _read_source(path)
    if text is None:
        report['error'] = {'type': 'ReadError', 'message': read_err,
                           'line': None, 'offset': None, 'source_line': ''}
        report['findings'].append(_finding(
            'critical', 'user_config.py cannot be read',
            f"{read_err}. config.py hits the same error and silently keeps its "
            f"built-in defaults.",
            fix="Check file permissions and encoding."))
        return report

    assigns, syn = _parse(text)

    # ---- fatal: the whole file is discarded -------------------------------
    if syn is not None:
        report['error'] = syn
        line_txt = syn.get('source_line') or ''
        detail = (
            f"config.py compiles this file in ONE pass, so this single error "
            f"discards EVERY setting in it. The app is running entirely on .env "
            f"values and built-in defaults right now. The only other trace is "
            f"one line in the service log: "
            f"\"Failed to load user_config: {syn['message']}\".")
        fix = "Fix the line below and restart the AI Hub services."
        if _looks_like_env_line(line_txt):
            fix = ("This looks like a line pasted from .env. user_config.py is "
                   "PYTHON, not a .env file: text needs quotes "
                   "(KEY = './some/path'), and booleans are True/False.")
        report['findings'].append(_finding(
            'critical',
            f"user_config.py is not valid Python - all settings are being ignored",
            detail, fix=fix, line=syn.get('line')))
        return report

    report['loaded'] = True

    # ---- per-key table ----------------------------------------------------
    seen: dict = {}
    for a in assigns:
        seen.setdefault(a['key'], []).append(a['line'])

    keys = [a['key'] for a in assigns]
    consumers = _consumers_for(sorted(set(keys)), rescan)

    MISSING = object()
    for a in assigns:
        key = a['key']
        is_secret = any(h in key.upper() for h in _SECRET_HINTS)
        eff = getattr(cfg, key, MISSING)
        known = eff is not MISSING
        # Compare reprs: the file's literal against what config.py holds now.
        effective_repr = '(not a config setting)' if not known else repr(eff)
        applied = known and a['is_literal'] and effective_repr == a['value']

        c = consumers.get(key) or {}
        env_n, attr_n = c.get('env', 0), c.get('attr', 0)
        if not c:
            category = 'unknown'
        elif env_n and attr_n:
            category = 'split'
        elif env_n:
            category = 'env-only'
        elif attr_n:
            category = 'config-attr'
        else:
            category = 'unused'

        report['keys'].append({
            'key': key,
            'line': a['line'],
            'value': '(hidden)' if is_secret else a['value'],
            'effective': '(hidden)' if is_secret else effective_repr,
            'known': known,
            'applied': bool(applied),
            'secret': is_secret,
            'category': category,
            'env_consumers': env_n,
            'attr_consumers': attr_n,
            'in_environ': key in os.environ,
        })

    # ---- findings ---------------------------------------------------------
    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    for k, lines in sorted(dupes.items()):
        report['findings'].append(_finding(
            'warning', f"{k} is assigned more than once",
            f"Lines {', '.join(map(str, lines))}. Python keeps the LAST "
            f"assignment; the earlier ones look active but do nothing.",
            fix="Delete the assignments you did not mean to keep.",
            line=lines[0]))

    for row in report['keys']:
        k = row['key']
        # Order matters: an env-only key is NOT unrecognised. It is a real
        # platform setting that simply has no config.py attribute (nothing reads
        # it that way), so "not a recognised setting" would be both wrong and
        # unhelpful - THE_AGENT_ENABLED is the obvious example. Test the
        # category first and only fall through to "unrecognised" for a key with
        # no readers of either kind.
        if row['category'] == 'env-only':
            report['findings'].append(_finding(
                'warning', f"{k} has no effect in this file",
                f"Every consumer reads this via os.getenv(), and user_config.py "
                f"sets a config attribute - it never touches os.environ. "
                f"Setting it here does nothing at all.",
                fix=f"Move it to .env as {k}=<value>, then restart.",
                line=row['line']))
        elif row['category'] == 'split':
            report['findings'].append(_finding(
                'warning', f"{k} is only half-applied",
                f"{row['attr_consumers']} consumer(s) read it as a config "
                f"attribute (which this file sets) and {row['env_consumers']} "
                f"read it via os.getenv() (which it does not). The two halves "
                f"can disagree, and may not even share a default.",
                fix=f"Set it in BOTH places, or set it only in .env.",
                line=row['line']))
        elif not row['known']:
            report['findings'].append(_finding(
                'warning', f"{k} is not a recognised setting",
                "config.py has no setting by this name and nothing in the "
                "scanned tree reads it either. Usually a typo, or a setting "
                "that was renamed or removed in a later release.",
                fix="Check the spelling against config.py, or delete the line.",
                line=row['line']))
        elif not row['applied']:
            report['findings'].append(_finding(
                'info', f"{k} does not match the running value",
                "The value in this file differs from what config.py currently "
                "holds. Normal if the file changed since the last restart, or "
                "if the value is an expression rather than a plain literal.",
                fix="Restart the AI Hub services and re-check.",
                line=row['line']))

        if row['secret']:
            report['findings'].append(_finding(
                'warning', f"{k} is a credential stored in a plain-text file",
                "Keys belong in Settings -> API Keys (BYOK), where they are "
                "stored encrypted. While BYOK is enabled a key here is ignored "
                "entirely, so it is a stale secret on disk for no benefit.",
                fix="Move it to the API Keys screen, delete the line, and "
                    "rotate the key - it has been readable on disk.",
                line=row['line']))

    for row in report['keys']:
        if row['in_environ'] and row['known'] and row['category'] != 'env-only':
            report['findings'].append(_finding(
                'info', f"{row['key']} is also set in .env",
                "Both this file and the environment set it. user_config.py is "
                "applied last, so this file wins - which can be surprising when "
                "someone edits .env and sees no change.",
                fix="Keep one source of truth for this setting.",
                line=row['line']))

    report['counts'] = {
        'total': len(report['keys']),
        'applied': sum(1 for r in report['keys'] if r['applied']),
        'inert': sum(1 for r in report['keys']
                     if not r['known'] or r['category'] in ('env-only', 'unused')),
        'unknown': sum(1 for r in report['keys'] if r['category'] == 'unknown'),
    }
    order = {'critical': 0, 'warning': 1, 'info': 2}
    report['findings'].sort(key=lambda f: (order.get(f['level'], 9), f['line'] or 0))
    return report


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@config_health_bp.route('/config-health')
@admin_required()
def config_health_page():
    """Render the Config Health screen."""
    return render_template('config_health.html')


@config_health_bp.route('/api/config-health', methods=['GET'])
@admin_required(api=True)
def config_health_api():
    """The full report. Read-only; a failure here reports itself, never 500s."""
    from flask import request
    rescan = request.args.get('rescan') == '1'
    try:
        return jsonify(build_report(rescan=rescan))
    except Exception as e:
        logger.exception("[config-health] report failed")
        return jsonify({
            'path': _user_config_path(), 'exists': False, 'loaded': False,
            'error': {'type': type(e).__name__, 'message': str(e),
                      'line': None, 'offset': None, 'source_line': ''},
            'keys': [], 'counts': {'total': 0, 'applied': 0, 'inert': 0, 'unknown': 0},
            'findings': [_finding('critical', 'Could not build the report', str(e))],
        }), 200
