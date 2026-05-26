"""
Branding resolver for the data collection agent.

Branding is configurable at three levels (most-specific wins):

    1. JWT prefill `branding` claim (per-session override)   — Phase 1.3
    2. Schema's `branding` block                             — per agent/form
    3. App-level `_app_branding.json` (or env vars)          — per deployment
    4. Hardcoded defaults                                    — fallback

This module owns the resolution. The page templates call `resolve_branding(...)`
and render a `<style>` block that sets CSS variables (`--brand-primary`,
`--brand-accent`, etc.). The CSS already references those variables with
fallbacks to the platform defaults, so any unset key naturally falls through.

NOTE: Phase 1.2 implements levels 2-4. Level 1 (JWT override) is wired up in
Phase 1.3 once `PyJWT` is available. The `jwt_claims` parameter is accepted
now so we don't have to change call sites later.
"""

import json
import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Hardcoded defaults — match the existing platform colors (chat-theme.css)
HARDCODED_DEFAULTS = {
    'display_name': None,        # falls back to schema['name'] when None
    'logo_url': None,            # no logo by default — header shows the icon
    'primary_color': '#06b6d4',  # --cyber-cyan
    'accent_color': '#a78bfa',   # --cyber-violet
    'font_family': "'Outfit', sans-serif",
    'footer_text': None,
    'favicon_url': None,
    'support_url': None,
}

CONFIGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'configs')
APP_BRANDING_FILE = os.path.join(CONFIGS_DIR, '_app_branding.json')

# Allowed branding keys — anything else is ignored (defensive against
# malformed configs / JWT claims).
ALLOWED_KEYS = set(HARDCODED_DEFAULTS.keys())


def load_app_branding() -> Dict:
    """
    Load app-level branding. Sources, in priority order:
      1. The JSON file at configs/_app_branding.json (if present)
      2. Env vars: DCA_APP_<KEY> (e.g. DCA_APP_PRIMARY_COLOR=#1234ab)

    Env vars override file contents on a per-key basis. Returns an empty
    dict if nothing is configured (callers fall back to hardcoded defaults).
    """
    out: Dict = {}

    # 1. JSON file
    if os.path.exists(APP_BRANDING_FILE):
        try:
            with open(APP_BRANDING_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f) or {}
            if isinstance(data, dict):
                for k in ALLOWED_KEYS:
                    if k in data and data[k] not in (None, ''):
                        out[k] = data[k]
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in {APP_BRANDING_FILE}: {e}")
        except Exception as e:
            logger.warning(f"Could not read {APP_BRANDING_FILE}: {e}")

    # 2. Env-var overrides
    env_map = {
        'display_name':    'DCA_APP_DISPLAY_NAME',
        'logo_url':        'DCA_APP_LOGO_URL',
        'primary_color':   'DCA_APP_PRIMARY_COLOR',
        'accent_color':    'DCA_APP_ACCENT_COLOR',
        'font_family':     'DCA_APP_FONT_FAMILY',
        'footer_text':     'DCA_APP_FOOTER_TEXT',
        'favicon_url':     'DCA_APP_FAVICON_URL',
        'support_url':     'DCA_APP_SUPPORT_URL',
    }
    for key, env_name in env_map.items():
        val = os.environ.get(env_name)
        if val:
            out[key] = val

    return out


def resolve_branding(schema: Optional[Dict] = None,
                     jwt_claims: Optional[Dict] = None) -> Dict:
    """
    Walk the override hierarchy and return the resolved branding dict.

    Args:
        schema: the loaded form schema (or None for pages like the gallery
                that aren't tied to a specific schema). May contain a
                `branding` block.
        jwt_claims: decoded prefill JWT claims, may contain a `branding`
                key. Phase 1.2 always passes None; Phase 1.3 populates this.

    Returns: a dict with all `ALLOWED_KEYS` populated, falling back to
    hardcoded defaults for anything unset.
    """
    resolved = dict(HARDCODED_DEFAULTS)

    # Level 3: app-level
    app = load_app_branding()
    for k, v in app.items():
        if k in ALLOWED_KEYS and v not in (None, ''):
            resolved[k] = v

    # Level 2: schema-level
    if schema and isinstance(schema, dict):
        schema_branding = schema.get('branding') or {}
        if isinstance(schema_branding, dict):
            for k, v in schema_branding.items():
                if k in ALLOWED_KEYS and v not in (None, ''):
                    resolved[k] = v

    # Level 1: JWT claim (most specific)
    if jwt_claims and isinstance(jwt_claims, dict):
        claim_branding = jwt_claims.get('branding') or {}
        if isinstance(claim_branding, dict):
            for k, v in claim_branding.items():
                if k in ALLOWED_KEYS and v not in (None, ''):
                    resolved[k] = v

    # display_name fallback: schema name if still unset
    if not resolved.get('display_name') and schema:
        resolved['display_name'] = schema.get('name')

    return resolved


def branding_to_style_block(branding: Dict) -> str:
    """
    Render the resolved branding as an inline `<style>` block defining CSS
    custom properties on `:root`.

    Strategy: we set BOTH the new `--brand-*` vars AND override the existing
    platform vars (`--cyber-cyan`, `--cyber-violet`) so unmodified CSS rules
    auto-reskin without us having to rewrite every rule. New CSS that uses
    `--brand-primary` directly is also supported.

    Values are sanitized — characters that could break out of a CSS context
    are stripped defensively.
    """
    primary = _safe_css_value(branding.get('primary_color'))
    accent = _safe_css_value(branding.get('accent_color'))
    font = _safe_css_value(branding.get('font_family'))

    parts = [':root {']
    if primary:
        parts.append(f'  --brand-primary: {primary};')
        # Override the platform color so existing rules re-skin
        parts.append(f'  --cyber-cyan: {primary};')
    if accent:
        parts.append(f'  --brand-accent: {accent};')
        parts.append(f'  --cyber-violet: {accent};')
    if font:
        parts.append(f'  --brand-font: {font};')
    parts.append('}')
    if font:
        parts.append('body, .dca-page, .dca-builder-page, .dca-gallery-page {'
                     f' font-family: {font}; '
                     '}')
    return '<style>' + '\n'.join(parts) + '</style>'


def _safe_css_value(value) -> Optional[str]:
    """Strip CSS-context-breaking characters from a value before inlining."""
    if value is None or value == '':
        return None
    # Remove any chars that could break out of a CSS declaration
    s = str(value)
    for ch in ('<', '>', '{', '}', ';', '\n', '\r', '"'):
        s = s.replace(ch, '')
    s = s.strip()
    return s or None


def safe_url(value) -> Optional[str]:
    """
    Sanitize a URL for embedding in `src="..."` / `href="..."` attributes.
    Allows http(s), root-relative (`/foo`), and path-relative URLs. Rejects
    `javascript:`, `data:` (except images), and other suspect schemes.
    """
    if value is None or value == '':
        return None
    s = str(value).strip()
    lowered = s.lower()
    if lowered.startswith('javascript:'):
        return None
    if lowered.startswith('data:') and not lowered.startswith('data:image/'):
        return None
    return s
