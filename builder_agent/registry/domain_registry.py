"""
Builder Agent - Domain Registry
=================================
The central registry that holds all platform domain definitions.
Provides discovery, search, and dependency resolution for the builder agent.

This is Layer 1 - the first thing the agent consults to understand
what areas of the platform exist and how they relate.
"""

import logging
from typing import Dict, List, Optional, Set, Tuple
from .domains import DomainDefinition, CapabilityDefinition, EntityDefinition
from ..validation.validators import (
    DomainValidator,
    ValidationResult,
    ValidationSeverity,
)

logger = logging.getLogger(__name__)


class DomainRegistry:
    """
    Central registry of all platform domains.
    
    The registry is the builder agent's map of the platform. It answers:
    - What areas of the platform exist?
    - What can be done in each area?
    - How do areas relate to each other?
    - What's needed to work in a given area?
    
    Thread-safe for reads after initialization. Registration should
    happen at startup before the agent begins processing requests.
    """

    def __init__(self):
        self._domains: Dict[str, DomainDefinition] = {}
        self._initialized = False
        self._capability_index: Dict[str, Tuple[str, CapabilityDefinition]] = {}
        # Maps capability_id -> (domain_id, capability)
        self._concept_index: Dict[str, Set[str]] = {}
        # Maps concept keyword -> set of domain_ids
        self._tag_index: Dict[str, Set[str]] = {}
        # Maps tag -> set of capability_ids

    # ─── Registration ─────────────────────────────────────────────────

    def register_domain(self, domain: DomainDefinition) -> ValidationResult:
        """
        Register a domain in the registry.
        Validates the domain before registration and rejects invalid entries.
        
        Returns:
            ValidationResult indicating success or failure with details.
        """
        # Validate the domain data
        result = DomainValidator.validate_domain(domain.to_dict())

        if not result.is_valid:
            logger.error(
                f"Domain registration failed for '{domain.id}': "
                f"{[e.message for e in result.errors]}"
            )
            return result

        # Check for duplicate registration
        if domain.id in self._domains:
            existing = self._domains[domain.id]
            result.add_warning(
                "id",
                f"Domain '{domain.id}' already registered (v{existing.version}), "
                f"replacing with v{domain.version}",
                "DOMAIN_REPLACEMENT"
            )

        # Store the domain
        self._domains[domain.id] = domain

        # Rebuild indexes
        self._rebuild_indexes()

        logger.info(
            f"Registered domain: {domain.id} v{domain.version} "
            f"({len(domain.capabilities)} capabilities)"
        )
        result.validated_data = {"domain_id": domain.id, "registered": True}
        return result

    def register_domains(self, domains: List[DomainDefinition]) -> ValidationResult:
        """
        Register multiple domains at once with full cross-validation.
        
        This is the preferred method for bulk registration at startup
        since it validates cross-domain dependencies after all domains
        are registered.
        """
        combined_result = ValidationResult()

        # Register each domain individually first
        for domain in domains:
            result = self.register_domain(domain)
            combined_result.merge(result)

        # Cross-validate the entire registry
        if combined_result.is_valid:
            registry_data = {d.id: d.to_dict() for d in domains}
            cross_result = DomainValidator.validate_registry(registry_data)
            combined_result.merge(cross_result)

        if combined_result.is_valid:
            self._initialized = True
            logger.info(
                f"Registry initialized with {len(self._domains)} domains, "
                f"{len(self._capability_index)} total capabilities"
            )

        return combined_result

    # ─── Domain Discovery ─────────────────────────────────────────────

    def get_domain(self, domain_id: str) -> Optional[DomainDefinition]:
        """Get a specific domain by ID."""
        return self._domains.get(domain_id)

    def get_all_domains(self) -> Dict[str, DomainDefinition]:
        """Get all registered domains."""
        return dict(self._domains)

    def get_domain_ids(self) -> Set[str]:
        """Get the set of all registered domain IDs."""
        return set(self._domains.keys())

    def get_domain_summary(self) -> str:
        """
        Get a concise summary of all domains for the AI's initial orientation.
        This is what the agent reads first to understand the platform landscape.
        """
        lines = ["=== AI Hub Platform Domains ===\n"]
        for domain_id in sorted(self._domains.keys()):
            domain = self._domains[domain_id]
            lines.append(domain.summary)
            if domain.depends_on:
                lines.append(f"  [requires]: {', '.join(domain.depends_on)}")
            lines.append("")
        return "\n".join(lines)

    def get_domain_detail(self, domain_id: str) -> Optional[str]:
        """
        Get detailed information about a specific domain.
        This is what the agent reads when it needs to work within a domain.
        """
        domain = self._domains.get(domain_id)
        if not domain:
            return None

        lines = [
            f"=== Domain: {domain.name} (v{domain.version}) ===",
            f"Description: {domain.description}",
        ]

        if domain.context_notes:
            lines.append(f"\nImportant Notes:\n{domain.context_notes}")

        if domain.entities:
            lines.append("\nEntities:")
            for entity in domain.entities:
                lines.append(f"  - {entity.name}: {entity.description}")
                if entity.key_fields:
                    lines.append(f"    Fields: {', '.join(entity.key_fields)}")
                if entity.relationships:
                    rels = [f"{k} -> {v}" for k, v in entity.relationships.items()]
                    lines.append(f"    Relationships: {', '.join(rels)}")

        if domain.capabilities:
            lines.append("\nCapabilities:")
            by_category = {}
            for cap in domain.capabilities:
                by_category.setdefault(cap.category, []).append(cap)

            for cat in sorted(by_category.keys()):
                lines.append(f"\n  [{cat.upper()}]:")
                for cap in by_category[cat]:
                    lines.append(f"    {cap.id}: {cap.description}")
                    if cap.required_context:
                        lines.append(
                            f"      Requires: {', '.join(cap.required_context)}"
                        )
                    if cap.requires_domains:
                        lines.append(
                            f"      Cross-domain: {', '.join(cap.requires_domains)}"
                        )

        if domain.depends_on:
            lines.append(f"\nDepends on: {', '.join(domain.depends_on)}")

        return "\n".join(lines)

    # ─── Capability Discovery ─────────────────────────────────────────

    def get_capability(self, capability_id: str) -> Optional[Tuple[str, CapabilityDefinition]]:
        """
        Find a capability by its full ID.
        Returns (domain_id, capability) or None.
        """
        return self._capability_index.get(capability_id)

    def find_capabilities_by_category(self, category: str) -> List[Tuple[str, CapabilityDefinition]]:
        """Find all capabilities across all domains matching a category."""
        results = []
        for cap_id, (domain_id, cap) in self._capability_index.items():
            if cap.category == category:
                results.append((domain_id, cap))
        return results

    def find_capabilities_by_tag(self, tag: str) -> List[Tuple[str, CapabilityDefinition]]:
        """Find all capabilities tagged with a specific tag."""
        cap_ids = self._tag_index.get(tag.lower(), set())
        results = []
        for cap_id in cap_ids:
            entry = self._capability_index.get(cap_id)
            if entry:
                results.append(entry)
        return results

    def search_capabilities(self, query: str) -> List[Tuple[str, CapabilityDefinition, float]]:
        """
        Search for capabilities matching a natural language query.
        Returns list of (domain_id, capability, relevance_score) tuples,
        sorted by relevance descending.
        
        Uses keyword matching against capability names, descriptions, 
        tags, and domain key concepts. This is a lightweight search;
        the AI itself does the heavy lifting for intent understanding.
        """
        query_terms = set(query.lower().split())
        scored = []

        for cap_id, (domain_id, cap) in self._capability_index.items():
            score = 0.0

            # Match against capability name
            cap_name_terms = set(cap.name.lower().split())
            name_overlap = query_terms & cap_name_terms
            score += len(name_overlap) * 3.0

            # Match against description
            desc_terms = set(cap.description.lower().split())
            desc_overlap = query_terms & desc_terms
            score += len(desc_overlap) * 1.0

            # Match against tags
            cap_tags = set(t.lower() for t in cap.tags)
            tag_overlap = query_terms & cap_tags
            score += len(tag_overlap) * 2.0

            # Match against domain key concepts
            domain = self._domains.get(domain_id)
            if domain:
                concepts = set(c.lower() for c in domain.key_concepts)
                concept_overlap = query_terms & concepts
                score += len(concept_overlap) * 1.5

            if score > 0:
                scored.append((domain_id, cap, score))

        scored.sort(key=lambda x: x[2], reverse=True)
        return scored

    # ─── Dependency Resolution ────────────────────────────────────────

    def get_dependencies(self, domain_id: str, recursive: bool = True) -> List[str]:
        """
        Get all domains that a given domain depends on.
        
        Args:
            domain_id: The domain to check
            recursive: If True, resolves full dependency tree
            
        Returns:
            List of domain IDs in dependency order (dependencies first)
        """
        domain = self._domains.get(domain_id)
        if not domain:
            return []

        if not recursive:
            return list(domain.depends_on)

        # BFS for full dependency tree
        visited = set()
        ordered = []
        queue = list(domain.depends_on)

        while queue:
            dep_id = queue.pop(0)
            if dep_id in visited:
                continue
            visited.add(dep_id)

            dep_domain = self._domains.get(dep_id)
            if dep_domain:
                # Add this domain's dependencies first
                for sub_dep in dep_domain.depends_on:
                    if sub_dep not in visited:
                        queue.append(sub_dep)
                ordered.append(dep_id)

        return ordered

    def get_dependents(self, domain_id: str) -> List[str]:
        """Get all domains that depend on the given domain."""
        dependents = []
        for did, domain in self._domains.items():
            if domain_id in domain.depends_on:
                dependents.append(did)
        return dependents

    def get_required_domains_for_capability(self, capability_id: str) -> Set[str]:
        """
        Get all domains needed to execute a capability.
        Includes the capability's own domain plus any cross-domain requirements.
        """
        entry = self._capability_index.get(capability_id)
        if not entry:
            return set()

        domain_id, cap = entry
        required = {domain_id}

        # Add explicitly required domains
        required.update(cap.requires_domains)

        # Add transitive dependencies
        for req_domain in list(required):
            deps = self.get_dependencies(req_domain, recursive=True)
            required.update(deps)

        return required

    # ─── Concept Matching (for AI Intent Resolution) ──────────────────

    def find_domains_by_concept(self, concept: str) -> List[str]:
        """
        Find domains related to a concept keyword.
        Used by the planner to identify which domains are relevant to a request.
        """
        return list(self._concept_index.get(concept.lower(), set()))

    def find_relevant_domains(self, query_terms: List[str]) -> List[Tuple[str, int]]:
        """
        Given a list of terms from a user request, find which domains 
        are most relevant. Returns (domain_id, match_count) sorted by relevance.
        """
        domain_scores: Dict[str, int] = {}
        normalized_terms = [t.lower() for t in query_terms]

        for term in normalized_terms:
            matching_domains = self._concept_index.get(term, set())
            for domain_id in matching_domains:
                domain_scores[domain_id] = domain_scores.get(domain_id, 0) + 1

        scored = sorted(domain_scores.items(), key=lambda x: x[1], reverse=True)
        return scored

    # ─── Validation ───────────────────────────────────────────────────

    def validate(self) -> ValidationResult:
        """
        Full validation of the registry state.
        Call after all domains are registered to ensure consistency.
        """
        registry_data = {d_id: d.to_dict() for d_id, d in self._domains.items()}
        result = DomainValidator.validate_registry(registry_data)

        # Additional runtime checks
        for domain_id, domain in self._domains.items():
            for cap in domain.capabilities:
                for req_domain in cap.requires_domains:
                    if req_domain not in self._domains:
                        result.add_error(
                            f"{domain_id}.capabilities.{cap.id}",
                            f"Capability '{cap.id}' requires unregistered "
                            f"domain: '{req_domain}'",
                            "UNRESOLVED_CAPABILITY_DOMAIN"
                        )

        if result.is_valid:
            result.add_info(
                "_registry",
                f"Registry valid: {len(self._domains)} domains, "
                f"{len(self._capability_index)} capabilities",
                "REGISTRY_VALID"
            )

        return result

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def domain_count(self) -> int:
        return len(self._domains)

    @property
    def capability_count(self) -> int:
        return len(self._capability_index)

    # ─── Internal Index Building ──────────────────────────────────────

    def _rebuild_indexes(self):
        """Rebuild all lookup indexes after domain changes."""
        self._capability_index.clear()
        self._concept_index.clear()
        self._tag_index.clear()

        for domain_id, domain in self._domains.items():
            # Index capabilities
            for cap in domain.capabilities:
                self._capability_index[cap.id] = (domain_id, cap)

                # Index tags
                for tag in cap.tags:
                    tag_lower = tag.lower()
                    if tag_lower not in self._tag_index:
                        self._tag_index[tag_lower] = set()
                    self._tag_index[tag_lower].add(cap.id)

            # Index key concepts
            for concept in domain.key_concepts:
                concept_lower = concept.lower()
                if concept_lower not in self._concept_index:
                    self._concept_index[concept_lower] = set()
                self._concept_index[concept_lower].add(domain_id)

    # ─── Serialization ────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize the entire registry to a dictionary."""
        return {
            "domains": {d_id: d.to_dict() for d_id, d in self._domains.items()},
            "stats": {
                "domain_count": self.domain_count,
                "capability_count": self.capability_count,
                "initialized": self._initialized,
            }
        }
