"""
Builder Agent - AI Resolver
==============================
AI-powered domain and capability resolution.

Replaces brittle keyword matching with LLM-based intent understanding.
The resolver reads the domain registry to build compact catalogs,
sends them to the AI with the user's request, and parses structured
responses back into validated results.

Usage:
    from builder_agent.ai import AIResolver
    from builder_agent.registry import DomainRegistry
    
    resolver = AIResolver(registry, prompt_fn=azureMiniQuickPrompt)
    
    # Two-phase (more focused, 2 API calls):
    domains = resolver.resolve_domains("create a chatbot for sales data")
    capabilities = resolver.resolve_capabilities("create a chatbot for sales data", domains)
    
    # Single-pass (faster, 1 API call):
    result = resolver.resolve("create a chatbot for sales data")

The prompt_fn must have the signature:
    prompt_fn(prompt: str, system: str, temp: float) -> str
This matches azureQuickPrompt, azureMiniQuickPrompt, and quickPrompt from AppUtils.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..registry.domain_registry import DomainRegistry
from ..registry.domains import CapabilityDefinition
from . import prompts

logger = logging.getLogger(__name__)


# ─── Result Dataclasses ───────────────────────────────────────────────────

@dataclass
class ResolvedDomain:
    """A domain identified as relevant by the AI."""
    id: str
    relevance: str  # "primary" or "supporting"
    reasoning: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "relevance": self.relevance,
            "reasoning": self.reasoning,
        }


@dataclass
class ResolvedCapability:
    """A capability selected by the AI for execution."""
    id: str
    purpose: str
    order: int
    needs_user_input: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "purpose": self.purpose,
            "order": self.order,
            "needs_user_input": self.needs_user_input,
        }


@dataclass
class ResolutionResult:
    """Complete resolution result from the AI."""
    domains: List[ResolvedDomain] = field(default_factory=list)
    capabilities: List[ResolvedCapability] = field(default_factory=list)
    context_needed: List[str] = field(default_factory=list)
    clarification_needed: Optional[str] = None
    notes: Optional[str] = None
    raw_response: Optional[str] = None
    errors: List[str] = field(default_factory=list)

    @property
    def is_successful(self) -> bool:
        """True if resolution produced results without errors."""
        return len(self.errors) == 0 and (
            len(self.domains) > 0 or self.clarification_needed is not None
        )

    @property
    def domain_ids(self) -> List[str]:
        """Convenience: just the domain IDs."""
        return [d.id for d in self.domains]

    @property
    def primary_domains(self) -> List[ResolvedDomain]:
        """Domains marked as primary relevance."""
        return [d for d in self.domains if d.relevance == "primary"]

    @property
    def supporting_domains(self) -> List[ResolvedDomain]:
        """Domains marked as supporting relevance."""
        return [d for d in self.domains if d.relevance == "supporting"]

    @property
    def capability_ids(self) -> List[str]:
        """Convenience: just the capability IDs in order."""
        return [c.id for c in sorted(self.capabilities, key=lambda c: c.order)]

    @property
    def needs_clarification(self) -> bool:
        """True if the AI couldn't resolve without more info."""
        return self.clarification_needed is not None

    def to_dict(self) -> dict:
        result = {
            "domains": [d.to_dict() for d in self.domains],
            "capabilities": [c.to_dict() for c in sorted(self.capabilities, key=lambda c: c.order)],
            "context_needed": self.context_needed,
            "is_successful": self.is_successful,
        }
        if self.clarification_needed:
            result["clarification_needed"] = self.clarification_needed
        if self.notes:
            result["notes"] = self.notes
        if self.errors:
            result["errors"] = self.errors
        return result


# ─── AI Resolver ──────────────────────────────────────────────────────────

class AIResolver:
    """
    AI-powered domain and capability resolution.
    
    Uses LLM calls to understand user intent and map it to
    platform domains and capabilities. Replaces keyword-based
    matching with natural language understanding.
    
    Args:
        registry: The populated DomainRegistry to resolve against.
        prompt_fn: Callable with signature (prompt, system, temp) -> str.
                   Defaults to None (must be set before calling resolve methods).
        temperature: Temperature for AI calls. Default 0.0 for deterministic results.
    """

    def __init__(self, registry: DomainRegistry,
                 prompt_fn: Optional[Callable] = None,
                 temperature: float = 0.0):
        self.registry = registry
        self.prompt_fn = prompt_fn
        self.temperature = temperature
        
        # Cache the catalogs since registry doesn't change after init
        self._domain_catalog: Optional[str] = None
        self._capability_catalogs: Dict[str, str] = {}  # domain_id -> catalog
        self._full_catalog: Optional[str] = None

    # ─── Public API ───────────────────────────────────────────────────

    def resolve_domains(self, user_request: str) -> ResolutionResult:
        """
        Phase 1: Identify which platform domains are relevant to the request.
        
        Uses a compact domain catalog (names + descriptions + dependencies)
        to let the AI decide which domains are needed. This is a lightweight
        call since it only sends domain summaries, not full capability lists.
        
        Args:
            user_request: The user's natural language request.
            
        Returns:
            ResolutionResult with domains populated (capabilities empty).
        """
        self._ensure_prompt_fn()
        result = ResolutionResult()

        catalog = self._get_domain_catalog()
        prompt = prompts.DOMAIN_RESOLUTION_USER.format(
            domain_catalog=catalog,
            user_request=user_request,
        )

        try:
            raw = self.prompt_fn(
                prompt,
                system=prompts.DOMAIN_RESOLUTION_SYSTEM,
                temp=self.temperature,
            )
            result.raw_response = raw
            parsed = self._parse_json(raw)

            if parsed is None:
                result.errors.append(f"Failed to parse AI response as JSON: {raw[:200]}")
                return result

            # Extract clarification request
            if "clarification_needed" in parsed:
                result.clarification_needed = str(parsed["clarification_needed"])

            # Extract and validate domains
            for d in parsed.get("domains", []):
                domain_id = d.get("id", "")
                if self.registry.get_domain(domain_id):
                    result.domains.append(ResolvedDomain(
                        id=domain_id,
                        relevance=d.get("relevance", "primary"),
                        reasoning=d.get("reasoning", ""),
                    ))
                else:
                    logger.warning(
                        f"AI suggested unknown domain '{domain_id}', skipping"
                    )
                    result.errors.append(
                        f"AI suggested unknown domain: '{domain_id}'"
                    )

            # Auto-expand dependencies
            self._expand_dependencies(result)

        except Exception as e:
            logger.error(f"Domain resolution failed: {e}")
            result.errors.append(f"Domain resolution failed: {str(e)}")

        return result

    def resolve_capabilities(self, user_request: str,
                              domain_ids: Optional[List[str]] = None,
                              domains_result: Optional[ResolutionResult] = None
                              ) -> ResolutionResult:
        """
        Phase 2: Identify specific capabilities needed within the given domains.
        
        Uses a focused capability catalog containing only the relevant domains,
        so the AI isn't overwhelmed with all 84 capabilities.
        
        Args:
            user_request: The user's natural language request.
            domain_ids: List of domain IDs to search within. If None, uses
                       domains_result.domain_ids.
            domains_result: Optional result from resolve_domains() to build upon.
                          If provided, domains and their metadata are preserved.
            
        Returns:
            ResolutionResult with capabilities populated.
        """
        self._ensure_prompt_fn()

        # Build on existing result or create new
        if domains_result:
            result = domains_result
            if domain_ids is None:
                domain_ids = domains_result.domain_ids
        else:
            result = ResolutionResult()

        if not domain_ids:
            result.errors.append("No domains provided for capability resolution")
            return result

        catalog = self._get_capability_catalog(domain_ids)
        prompt = prompts.CAPABILITY_RESOLUTION_USER.format(
            capability_catalog=catalog,
            user_request=user_request,
        )

        try:
            raw = self.prompt_fn(
                prompt,
                system=prompts.CAPABILITY_RESOLUTION_SYSTEM,
                temp=self.temperature,
            )
            result.raw_response = raw
            parsed = self._parse_json(raw)

            if parsed is None:
                result.errors.append(f"Failed to parse AI response as JSON: {raw[:200]}")
                return result

            # Extract capabilities
            for c in parsed.get("capabilities", []):
                cap_id = c.get("id", "")
                registry_entry = self.registry.get_capability(cap_id)
                if registry_entry:
                    reg_domain_id, _ = registry_entry
                    if reg_domain_id in domain_ids:
                        result.capabilities.append(ResolvedCapability(
                            id=cap_id,
                            purpose=c.get("purpose", ""),
                            order=c.get("order", 0),
                            needs_user_input=c.get("needs_user_input", []),
                        ))
                    else:
                        logger.warning(
                            f"AI suggested capability '{cap_id}' from domain "
                            f"'{reg_domain_id}' which is outside the resolved "
                            f"domains {domain_ids}. Adding domain."
                        )
                        # Auto-add the domain if AI found a valid capability
                        # outside the initial domain set
                        if reg_domain_id not in [d.id for d in result.domains]:
                            result.domains.append(ResolvedDomain(
                                id=reg_domain_id,
                                relevance="supporting",
                                reasoning=f"Added because capability {cap_id} was selected",
                            ))
                            domain_ids.append(reg_domain_id)
                        result.capabilities.append(ResolvedCapability(
                            id=cap_id,
                            purpose=c.get("purpose", ""),
                            order=c.get("order", 0),
                            needs_user_input=c.get("needs_user_input", []),
                        ))
                else:
                    logger.warning(
                        f"AI suggested unknown capability '{cap_id}', skipping"
                    )
                    result.errors.append(
                        f"AI suggested unknown capability: '{cap_id}'"
                    )

            # Extract context needs
            result.context_needed = parsed.get("context_needed", [])
            if parsed.get("notes"):
                result.notes = str(parsed["notes"])

        except Exception as e:
            logger.error(f"Capability resolution failed: {e}")
            result.errors.append(f"Capability resolution failed: {str(e)}")

        return result

    def resolve(self, user_request: str) -> ResolutionResult:
        """
        Single-pass resolution: domains + capabilities in one AI call.
        
        More efficient (1 API call vs 2) but sends the full catalog.
        Best for straightforward requests. For complex multi-domain
        requests, the two-phase approach may be more accurate.
        
        Args:
            user_request: The user's natural language request.
            
        Returns:
            Complete ResolutionResult with domains and capabilities.
        """
        self._ensure_prompt_fn()
        result = ResolutionResult()

        catalog = self._get_full_catalog()
        prompt = prompts.COMBINED_RESOLUTION_USER.format(
            full_catalog=catalog,
            user_request=user_request,
        )

        try:
            raw = self.prompt_fn(
                prompt,
                system=prompts.COMBINED_RESOLUTION_SYSTEM,
                temp=self.temperature,
            )
            result.raw_response = raw
            parsed = self._parse_json(raw)

            if parsed is None:
                result.errors.append(f"Failed to parse AI response as JSON: {raw[:200]}")
                return result

            # Extract domains
            for d in parsed.get("domains", []):
                domain_id = d.get("id", "")
                if self.registry.get_domain(domain_id):
                    result.domains.append(ResolvedDomain(
                        id=domain_id,
                        relevance=d.get("relevance", "primary"),
                        reasoning=d.get("reasoning", ""),
                    ))
                else:
                    logger.warning(f"AI suggested unknown domain '{domain_id}', skipping")

            # Auto-expand dependencies
            self._expand_dependencies(result)

            # Extract capabilities (validate against resolved domains)
            resolved_domain_ids = result.domain_ids
            for c in parsed.get("capabilities", []):
                cap_id = c.get("id", "")
                registry_entry = self.registry.get_capability(cap_id)
                if registry_entry:
                    reg_domain_id, _ = registry_entry
                    if reg_domain_id not in resolved_domain_ids:
                        # Auto-add domain
                        result.domains.append(ResolvedDomain(
                            id=reg_domain_id,
                            relevance="supporting",
                            reasoning=f"Added because capability {cap_id} was selected",
                        ))
                        resolved_domain_ids.append(reg_domain_id)

                    result.capabilities.append(ResolvedCapability(
                        id=cap_id,
                        purpose=c.get("purpose", ""),
                        order=c.get("order", 0),
                        needs_user_input=c.get("needs_user_input", []),
                    ))
                else:
                    logger.warning(f"AI suggested unknown capability '{cap_id}', skipping")

            # Extract context needs and notes
            result.context_needed = parsed.get("context_needed", [])
            if parsed.get("notes"):
                result.notes = str(parsed["notes"])
            if parsed.get("clarification_needed"):
                result.clarification_needed = str(parsed["clarification_needed"])

        except Exception as e:
            logger.error(f"Combined resolution failed: {e}")
            result.errors.append(f"Combined resolution failed: {str(e)}")

        return result

    def resolve_two_phase(self, user_request: str) -> ResolutionResult:
        """
        Two-phase resolution: domains first, then capabilities.
        
        More accurate for complex requests because the capability
        prompt is focused on only the relevant domains. Costs 2 API calls.
        
        Args:
            user_request: The user's natural language request.
            
        Returns:
            Complete ResolutionResult with domains and capabilities.
        """
        # Phase 1: Resolve domains
        result = self.resolve_domains(user_request)

        if not result.is_successful or result.needs_clarification:
            return result

        # Phase 2: Resolve capabilities within those domains
        result = self.resolve_capabilities(
            user_request,
            domains_result=result,
        )

        return result

    # ─── Catalog Building ─────────────────────────────────────────────

    def _get_domain_catalog(self) -> str:
        """
        Build a compact text catalog of all domains.
        Used for domain resolution (Phase 1).
        
        Format is optimized for LLM readability:
        concise, structured, no fluff.
        """
        if self._domain_catalog is not None:
            return self._domain_catalog

        lines = []
        for domain_id in sorted(self.registry.get_domain_ids()):
            domain = self.registry.get_domain(domain_id)
            if not domain:
                continue

            dep_str = ""
            if domain.depends_on:
                dep_str = f" [depends on: {', '.join(domain.depends_on)}]"

            cap_summary = ", ".join(
                c.name for c in domain.capabilities
            )

            lines.append(
                f"- {domain.id}: {domain.name} — {domain.description}{dep_str}\n"
                f"  Can: {cap_summary}"
            )

        self._domain_catalog = "\n".join(lines)
        return self._domain_catalog

    def _get_capability_catalog(self, domain_ids: List[str]) -> str:
        """
        Build a focused capability catalog for specific domains.
        Used for capability resolution (Phase 2).
        
        Includes full detail: IDs, descriptions, categories,
        required context, cross-domain needs.
        """
        lines = []

        for domain_id in sorted(domain_ids):
            domain = self.registry.get_domain(domain_id)
            if not domain:
                continue

            lines.append(f"Domain: {domain.id} — {domain.name}")

            if domain.context_notes:
                lines.append(f"  Notes: {domain.context_notes}")

            for cap in domain.capabilities:
                parts = [f"  - {cap.id} [{cap.category}]: {cap.description}"]

                if cap.required_context:
                    parts.append(
                        f"    Requires context: {', '.join(cap.required_context)}"
                    )
                if cap.requires_domains:
                    parts.append(
                        f"    Cross-domain: {', '.join(cap.requires_domains)}"
                    )
                if cap.tier_requirement:
                    parts.append(f"    Tier: {cap.tier_requirement}")

                lines.append("\n".join(parts))

            lines.append("")  # blank line between domains

        return "\n".join(lines)

    def _get_full_catalog(self) -> str:
        """
        Build a complete catalog of all domains + capabilities.
        Used for single-pass resolution.
        """
        if self._full_catalog is not None:
            return self._full_catalog

        all_domain_ids = sorted(self.registry.get_domain_ids())
        self._full_catalog = self._get_capability_catalog(all_domain_ids)
        return self._full_catalog

    def invalidate_cache(self):
        """
        Clear cached catalogs. Call if the registry is modified
        after the resolver is created (rare in production).
        """
        self._domain_catalog = None
        self._capability_catalogs.clear()
        self._full_catalog = None

    # ─── Internal Helpers ─────────────────────────────────────────────

    def _ensure_prompt_fn(self):
        """Raise if no prompt function is configured."""
        if self.prompt_fn is None:
            raise RuntimeError(
                "AIResolver requires a prompt_fn. Pass one at construction "
                "or set resolver.prompt_fn before calling resolve methods. "
                "Example: resolver.prompt_fn = azureMiniQuickPrompt"
            )

    def _parse_json(self, raw: str) -> Optional[dict]:
        """
        Parse JSON from AI response, handling common formatting issues.
        
        The AI may wrap JSON in markdown fences or include preamble text.
        azureQuickPrompt already strips ```json fences, but we handle
        edge cases defensively.
        """
        if not raw or not raw.strip():
            return None

        text = raw.strip()

        # Strip markdown fences if present
        if text.startswith("```"):
            # Remove opening fence (with optional language tag)
            first_newline = text.find("\n")
            if first_newline > 0:
                text = text[first_newline + 1:]
            # Remove closing fence
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3].rstrip()

        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON object in the text
        # (handles cases where AI adds preamble text)
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            try:
                return json.loads(text[brace_start:brace_end + 1])
            except json.JSONDecodeError:
                pass

        logger.error(f"Could not parse JSON from AI response: {text[:200]}")
        return None

    def _expand_dependencies(self, result: ResolutionResult):
        """
        Auto-expand resolved domains to include their dependencies.
        
        If the AI picks "agents" (which depends on "tools" and "connections"),
        we automatically add those as supporting domains so the planner
        knows the full picture.
        """
        existing_ids = set(result.domain_ids)
        to_add = []

        for domain in list(result.domains):
            deps = self.registry.get_dependencies(domain.id, recursive=True)
            for dep_id in deps:
                if dep_id not in existing_ids:
                    to_add.append(ResolvedDomain(
                        id=dep_id,
                        relevance="supporting",
                        reasoning=f"Dependency of {domain.id}",
                    ))
                    existing_ids.add(dep_id)

        result.domains.extend(to_add)
