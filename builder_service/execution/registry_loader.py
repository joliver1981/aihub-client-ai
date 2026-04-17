"""
Registry Loader
================
Imports and initializes the domain and action registries
from builder_agent at startup.

The registries are singletons — loaded once and reused.
"""

import logging
import os
import sys

logger = logging.getLogger(__name__)

# Add parent directory to path for builder_agent imports
PARENT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

# Singleton instances
_domain_registry = None
_action_registry = None
_initialized = False


def load_registries():
    """
    Load the domain and action registries from builder_agent.
    
    Call this once at startup. After this, use get_domain_registry()
    and get_action_registry() to access them.
    """
    global _domain_registry, _action_registry, _initialized
    
    if _initialized:
        logger.info("Registries already loaded")
        return True
    
    try:
        # Import builder_agent components
        logger.info(f"Looking for builder_agent in: {PARENT_DIR}")
        logger.info(f"sys.path includes: {sys.path[:3]}...")

        from builder_agent.registry import DomainRegistry
        from builder_agent.registry.platform_domains import get_platform_domains
        from builder_agent.actions import ActionRegistry
        from builder_agent.actions.platform_actions import get_platform_actions

        logger.info("Successfully imported builder_agent modules")
        
        # Initialize domain registry
        logger.info("Loading domain registry...")
        _domain_registry = DomainRegistry()
        domains = get_platform_domains()
        result = _domain_registry.register_domains(domains)
        
        if not result.is_valid:
            logger.error(f"Domain registry validation failed: {result.errors}")
            return False
        
        domain_count = len(_domain_registry.get_all_domains())
        cap_count = sum(
            len(d.capabilities) 
            for d in _domain_registry.get_all_domains().values()
        )
        logger.info(f"Domain registry loaded: {domain_count} domains, {cap_count} capabilities")
        
        # Initialize action registry
        logger.info("Loading action registry...")
        _action_registry = ActionRegistry(_domain_registry)
        actions = get_platform_actions()
        result = _action_registry.register_actions(actions)
        
        if not result.is_valid:
            logger.error(f"Action registry validation failed: {result.errors}")
            return False
        
        action_count = _action_registry.action_count
        logger.info(f"Action registry loaded: {action_count} action mappings")
        
        _initialized = True
        return True
        
    except ImportError as e:
        logger.error(f"Failed to import builder_agent: {e}")
        logger.error("Make sure builder_agent/ is in the parent directory")
        return False
    except Exception as e:
        logger.error(f"Failed to load registries: {e}", exc_info=True)
        return False


def get_domain_registry():
    """Get the domain registry (must call load_registries first)."""
    if not _initialized:
        raise RuntimeError("Registries not loaded. Call load_registries() first.")
    return _domain_registry


def get_action_registry():
    """Get the action registry (must call load_registries first)."""
    if not _initialized:
        raise RuntimeError("Registries not loaded. Call load_registries() first.")
    return _action_registry


def is_initialized() -> bool:
    """Check if registries are loaded."""
    return _initialized
