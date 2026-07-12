"""Agentic NLQ engine (V3) — OpenAI tool-loop implementation.

Additive sibling to the legacy LLMDataEngine (LLMDataEngineV2.py). Never imports
from the V2 engine modules; borrowed logic is copied, not imported (see the
plan's borrowing policy). Constructed only via nlq_engine_factory.create_nlq_engine
when the resolved mode is 'agentic'.

Public surface: AgenticNLQEngine (drop-in for LLMDataEngine as the entry points
use it).
"""
from .engine import AgenticNLQEngine

__all__ = ["AgenticNLQEngine"]
