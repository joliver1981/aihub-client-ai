"""
Builder Agent - Domain Registry
=================================
Layer 1: The entry point for the builder agent.
Provides high-level awareness of all platform domains.
"""

from .domain_registry import DomainRegistry
from .domains import DomainDefinition, CapabilityDefinition, EntityDefinition

__all__ = [
    'DomainRegistry',
    'DomainDefinition',
    'CapabilityDefinition', 
    'EntityDefinition',
]
