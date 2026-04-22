"""
Feature Flags — Two-Tier Resolution System
============================================
All features use: effective = cloud_flag AND local_flag
Both must be true for a feature to be enabled.

Cloud flags come from the SaaS-level tier/subscription API.
Local flags are admin-controlled via a JSON file on disk.
"""

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Local flag storage ─────────────────────────────────────────────────────

_FLAGS_DIR = Path(__file__).parent / "data"
_FLAGS_FILE = _FLAGS_DIR / "feature_flags.json"
_flags_lock = threading.RLock()  # RLock allows re-entrant calls (set_local_flags → get_local_flags)

# All feature flags with their defaults (all ON)
DEFAULT_FLAGS = {
    "command_center_enabled": True,
    "builder_enabled": True,
    "environments_enabled": True,
    "mcp_servers_enabled": True,
    "integrations_enabled": True,
    "solutions_enabled": True,
    "image_gen_enabled": True,
    "web_search_enabled": True,
}

# Human-readable labels
FLAG_LABELS = {
    "command_center_enabled": "Command Center",
    "builder_enabled": "Builder",
    "environments_enabled": "Environments",
    "mcp_servers_enabled": "MCP Servers",
    "integrations_enabled": "Integrations",
    "solutions_enabled": "Solutions Gallery",
    "image_gen_enabled": "Image Generation (DALL-E)",
    "web_search_enabled": "Web Search (Tavily)",
}

# Categorization
EXPERIMENTAL_FLAGS = [
    "command_center_enabled",
    "builder_enabled",
    "environments_enabled",
    "mcp_servers_enabled",
    "integrations_enabled",
    "solutions_enabled",
]
CAPABILITY_FLAGS = [
    "image_gen_enabled",
    "web_search_enabled",
]


def _ensure_flags_file():
    """Create the flags file with defaults if it doesn't exist."""
    _FLAGS_DIR.mkdir(parents=True, exist_ok=True)
    if not _FLAGS_FILE.exists():
        _FLAGS_FILE.write_text(json.dumps(DEFAULT_FLAGS, indent=2), encoding="utf-8")
        logger.info(f"Created feature flags file: {_FLAGS_FILE}")


def get_local_flags() -> dict:
    """Read local admin-controlled flags from disk."""
    with _flags_lock:
        _ensure_flags_file()
        try:
            data = json.loads(_FLAGS_FILE.read_text(encoding="utf-8"))
            # Merge with defaults (in case new flags were added)
            merged = {**DEFAULT_FLAGS, **data}
            return merged
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to read feature flags: {e}")
            return dict(DEFAULT_FLAGS)


def set_local_flags(flags: dict) -> dict:
    """Write local flags to disk. Only accepts known flag keys."""
    with _flags_lock:
        _ensure_flags_file()
        current = get_local_flags()
        for key, value in flags.items():
            if key in DEFAULT_FLAGS:
                current[key] = bool(value)
            else:
                logger.warning(f"Ignoring unknown flag: {key}")
        _FLAGS_FILE.write_text(json.dumps(current, indent=2), encoding="utf-8")
        logger.info(f"Updated feature flags: {flags}")
        return current


def get_cloud_flags() -> dict:
    """
    Fetch feature flags from the cloud tier/subscription API.
    Returns a dict of flag_name -> bool.
    Missing flags default to True (enabled).
    
    Uses cached tier data only — does NOT trigger a fresh cloud API/DB call.
    The tier page load populates the cache; flags just read from it.
    """
    try:
        from admin_tier_usage import _tier_cache
        
        # Use cached tier data if available — never block on a fresh fetch
        tier_data = _tier_cache.get('data')
        if not tier_data:
            logger.warning("Cloud tier data unavailable (cache empty) — defaulting all cloud flags to True")
            return {k: True for k in DEFAULT_FLAGS}

        tier_features = tier_data.get("tier_features", {})

        cloud = {}
        for flag_key in DEFAULT_FLAGS:
            # Cloud tier features use the same key names
            cloud[flag_key] = tier_features.get(flag_key, True)  # Default True if not found

        return cloud
    except Exception as e:
        logger.warning(f"Failed to fetch cloud flags: {e} — defaulting all to True")
        return {k: True for k in DEFAULT_FLAGS}


def get_effective_flags() -> dict:
    """
    Resolve effective flags: effective = cloud_flag AND local_flag.
    Both must be true for a feature to be enabled.
    Returns dict with full resolution details.
    """
    local = get_local_flags()
    cloud = get_cloud_flags()

    result = {}
    for key in DEFAULT_FLAGS:
        local_val = local.get(key, True)
        cloud_val = cloud.get(key, True)
        effective = local_val and cloud_val

        result[key] = {
            "effective": effective,
            "local": local_val,
            "cloud": cloud_val,
            "label": FLAG_LABELS.get(key, key),
            "category": "experimental" if key in EXPERIMENTAL_FLAGS else "capability",
        }

    return result


def is_feature_enabled(flag_key: str) -> bool:
    """
    Quick check: is a feature effectively enabled?
    Uses two-tier resolution (cloud AND local).
    """
    if flag_key not in DEFAULT_FLAGS:
        logger.warning(f"Unknown feature flag: {flag_key}")
        return True  # Unknown flags default to enabled

    local = get_local_flags()
    cloud = get_cloud_flags()

    return local.get(flag_key, True) and cloud.get(flag_key, True)
