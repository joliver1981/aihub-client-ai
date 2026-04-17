"""
Builder Service - Execution Engine
====================================
Bridges the builder_service (LangGraph conversation) with
builder_agent (domain/action knowledge) and AI Hub (Flask API).

This module:
1. Initializes the domain and action registries from builder_agent
2. Provides ActionExecutor to execute plan steps via HTTP
3. Maps step descriptions to concrete API calls
"""

from .registry_loader import load_registries, get_action_registry, get_domain_registry, is_initialized
from .executor import ActionExecutor, ExecutionResult

__all__ = [
    'load_registries',
    'get_action_registry', 
    'get_domain_registry',
    'is_initialized',
    'ActionExecutor',
    'ExecutionResult',
]
