"""
Builder Agent - AI Resolution
===============================
AI-powered domain and capability resolution.
Replaces keyword-based matching with LLM decision-making
for reliable intent understanding.
"""

from .resolver import AIResolver, ResolvedDomain, ResolvedCapability, ResolutionResult

__all__ = [
    'AIResolver',
    'ResolvedDomain',
    'ResolvedCapability',
    'ResolutionResult',
]
