"""
Command Center — Memory Models
=================================
Pydantic models for per-user memory entries.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class MemoryEntry(BaseModel):
    """A single memory entry for a user."""
    id: Optional[int] = None
    user_id: int
    memory_type: str  # preference, pattern, context, faq
    memory_key: str
    memory_value: Dict[str, Any]
    usage_count: int = 1
    smart_label: Optional[str] = None  # AI-generated short label like "Sales by Region"
    success_count: int = 0  # times this query returned real data
    fail_count: int = 0  # times this query errored/failed
    last_used: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def semantic_summary(self) -> str:
        """One-line summary for dedup comparison and display."""
        label = self.smart_label or ""
        insight = self.memory_value.get("key_insight", "")
        if insight:
            return f"{label} -- {insight}" if label else insight
        if self.memory_type == "preference":
            val = self.memory_value.get("value", "")
            domain = self.memory_value.get("domain", "")
            domain_str = f" ({domain})" if domain else ""
            return f"{label or self.memory_key}: {val}{domain_str}"
        return label or self.memory_value.get("query_template", self.memory_key)

    @property
    def success_rate(self) -> float:
        """Success rate as a fraction 0.0-1.0."""
        total = self.success_count + self.fail_count
        return self.success_count / total if total > 0 else 1.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "memory_type": self.memory_type,
            "memory_key": self.memory_key,
            "memory_value": self.memory_value,
            "usage_count": self.usage_count,
            "smart_label": self.smart_label,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "success_rate": self.success_rate,
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class SuggestionItem(BaseModel):
    """A prepared prompt suggestion for the user."""
    prompt: str
    description: str
    score: float = 0.0  # Ranking score
    source: str = "pattern"  # pattern, preference, recent
    smart_label: Optional[str] = None  # the AI label if available
    success_rate: float = 1.0  # success_count / (success_count + fail_count)
    pattern_key: Optional[str] = None  # DB key used for deletion

    def to_dict(self) -> dict:
        return {
            "prompt": self.prompt,
            "description": self.description,
            "score": self.score,
            "source": self.source,
            "smart_label": self.smart_label,
            "success_rate": self.success_rate,
            "pattern_key": self.pattern_key,
        }


class UserPreferences(BaseModel):
    """User preferences extracted from memory."""
    preferred_chart_type: str = "bar"
    preferred_agents: List[str] = []
    preferred_output_format: str = "rich"
    timezone: Optional[str] = None
    department: Optional[str] = None
    role_context: Optional[str] = None
