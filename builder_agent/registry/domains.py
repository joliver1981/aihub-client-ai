"""
Builder Agent - Domain Definitions
====================================
Typed dataclass representations of platform domains, capabilities, and entities.
These are the building blocks of the registry.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EntityDefinition:
    """
    Represents a data entity within a domain.
    Maps to database tables and API resources.
    """
    name: str
    description: str
    key_fields: List[str] = field(default_factory=list)
    relationships: Dict[str, str] = field(default_factory=dict)
    # relationship format: {"field_name": "target_domain.entity"}

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "key_fields": self.key_fields,
            "relationships": self.relationships,
        }


@dataclass
class CapabilityDefinition:
    """
    Represents something that can be done within a domain.
    Each capability maps to one or more API actions (defined in Layer 3).
    
    Categories:
        create   - Creating new resources
        read     - Querying/listing resources
        update   - Modifying existing resources
        delete   - Removing resources
        execute  - Running/triggering processes
        configure - Setting up configurations
        query    - Complex data queries
    """
    id: str                    # e.g., "agents.create"
    name: str                  # Human-readable: "Create Agent"
    description: str           # What this capability does
    category: str              # One of the standard categories
    requires_domains: List[str] = field(default_factory=list)
    required_context: List[str] = field(default_factory=list)
    # What info the AI needs before using this capability
    tier_requirement: Optional[str] = None  # Minimum tier needed
    required_role: Optional[int] = None  # Minimum user role: 1=User, 2=Developer, 3=Admin
    tags: List[str] = field(default_factory=list)
    # Searchable tags for capability discovery

    def to_dict(self) -> dict:
        result = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
        }
        if self.requires_domains:
            result["requires_domains"] = self.requires_domains
        if self.required_context:
            result["required_context"] = self.required_context
        if self.tier_requirement:
            result["tier_requirement"] = self.tier_requirement
        if self.required_role is not None:
            result["required_role"] = self.required_role
        if self.tags:
            result["tags"] = self.tags
        return result


@dataclass
class DomainDefinition:
    """
    Represents a major area of the platform.
    This is the top-level organizational unit the builder agent uses
    to understand what the platform can do.
    """
    id: str                     # e.g., "agents"
    name: str                   # "AI Agents"
    description: str            # What this domain is about
    version: str                # "1.0"

    # Core content
    capabilities: List[CapabilityDefinition] = field(default_factory=list)
    entities: List[EntityDefinition] = field(default_factory=list)

    # Relationships
    depends_on: List[str] = field(default_factory=list)
    # Other domain IDs this domain references

    # Discovery hints for the AI
    key_concepts: List[str] = field(default_factory=list)
    # Terms/phrases that suggest this domain is relevant

    # Contextual information
    context_notes: str = ""
    # Important things the AI should know about this domain

    # Enable/disable this domain
    enabled: bool = True
    # When disabled, this domain and its capabilities are hidden from the planner

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "capabilities": [c.to_dict() for c in self.capabilities],
            "entities": [e.to_dict() for e in self.entities],
            "depends_on": self.depends_on,
            "key_concepts": self.key_concepts,
            "context_notes": self.context_notes,
        }

    def get_capability(self, capability_id: str) -> Optional[CapabilityDefinition]:
        """Find a capability by its ID."""
        for cap in self.capabilities:
            if cap.id == capability_id:
                return cap
        return None

    def get_capabilities_by_category(self, category: str) -> List[CapabilityDefinition]:
        """Get all capabilities in a given category."""
        return [c for c in self.capabilities if c.category == category]

    def get_entity(self, entity_name: str) -> Optional[EntityDefinition]:
        """Find an entity by name."""
        for entity in self.entities:
            if entity.name == entity_name:
                return entity
        return None

    @property
    def capability_ids(self) -> List[str]:
        return [c.id for c in self.capabilities]

    @property
    def summary(self) -> str:
        """Brief summary for the AI's initial domain discovery."""
        cap_categories = {}
        for cap in self.capabilities:
            cap_categories.setdefault(cap.category, []).append(cap.name)
        
        parts = [f"{self.name}: {self.description}"]
        for cat, names in cap_categories.items():
            parts.append(f"  [{cat}]: {', '.join(names)}")
        return "\n".join(parts)
