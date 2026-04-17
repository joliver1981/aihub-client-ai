"""
Builder Agent - Action Registry
==================================
Stores ActionDefinition instances and provides lookup by capability ID.
Links to the DomainRegistry to validate that every action maps to a
real capability.

Usage:
    registry = DomainRegistry()
    registry.register_domains(get_platform_domains())

    action_registry = ActionRegistry(registry)
    action_registry.register_actions(get_platform_actions())

    # Look up how to execute a capability
    action = action_registry.get_action("agents.create")
    print(action.primary_route.path)  # "/add/agent"
    print(action.primary_route.input_fields)  # [FieldSchema(...), ...]
"""

import logging
from typing import Dict, List, Optional, Set, Tuple

from .definitions import ActionDefinition, FieldSchema, FieldType
from ..registry.domain_registry import DomainRegistry
from ..validation.validators import ValidationResult

logger = logging.getLogger(__name__)


class ActionRegistry:
    """
    Stores and retrieves action definitions.

    Every registered action must reference a capability that exists
    in the domain registry. This ensures the planner and action layer
    stay in sync — you can't define an action for a capability that
    doesn't exist, and the planner won't reference capabilities
    that have no action mapping.
    """

    def __init__(self, domain_registry: DomainRegistry):
        self._domain_registry = domain_registry
        self._actions: Dict[str, ActionDefinition] = {}
        self._initialized = False

    # ─── Registration ─────────────────────────────────────────────────

    def register_action(self, action: ActionDefinition) -> ValidationResult:
        """
        Register a single action definition.
        Validates the action against the domain registry.
        """
        result = ValidationResult()

        # Self-validation
        errors = action.validate()
        for err in errors:
            result.add_error(action.capability_id, err, "ACTION_INVALID")

        if not result.is_valid:
            return result

        # Verify capability exists in domain registry
        cap_entry = self._domain_registry.get_capability(action.capability_id)
        if not cap_entry:
            result.add_error(
                action.capability_id,
                f"Action references capability '{action.capability_id}' "
                f"which does not exist in the domain registry",
                "CAPABILITY_NOT_FOUND"
            )
            return result

        domain_id, cap = cap_entry
        if domain_id != action.domain_id:
            result.add_error(
                action.capability_id,
                f"Action domain_id '{action.domain_id}' does not match "
                f"capability's domain '{domain_id}'",
                "DOMAIN_MISMATCH"
            )
            return result

        # Check for duplicates
        if action.capability_id in self._actions:
            result.add_warning(
                action.capability_id,
                f"Replacing existing action for '{action.capability_id}'",
                "ACTION_REPLACED"
            )

        # Validate references to other capabilities
        for precheck in action.suggested_prechecks:
            if not self._domain_registry.get_capability(precheck):
                result.add_warning(
                    action.capability_id,
                    f"Suggested precheck '{precheck}' not found in registry",
                    "PRECHECK_NOT_FOUND"
                )
        for followup in action.suggested_followups:
            if not self._domain_registry.get_capability(followup):
                result.add_warning(
                    action.capability_id,
                    f"Suggested followup '{followup}' not found in registry",
                    "FOLLOWUP_NOT_FOUND"
                )
        if action.discovery_capability:
            if not self._domain_registry.get_capability(action.discovery_capability):
                result.add_warning(
                    action.capability_id,
                    f"Discovery capability '{action.discovery_capability}' "
                    f"not found in registry",
                    "DISCOVERY_NOT_FOUND"
                )

        self._actions[action.capability_id] = action
        result.validated_data = {"capability_id": action.capability_id}
        return result

    def register_actions(self, actions: List[ActionDefinition]) -> ValidationResult:
        """
        Register multiple actions at once.
        Returns combined validation results.
        """
        combined = ValidationResult()

        for action in actions:
            result = self.register_action(action)
            combined.merge(result)

        if combined.is_valid:
            self._initialized = True
            logger.info(
                f"Action registry initialized: {len(self._actions)} actions"
            )

        # Check for unmapped capabilities (warnings only)
        self._check_coverage(combined)

        return combined

    # ─── Lookup ───────────────────────────────────────────────────────

    def get_action(self, capability_id: str) -> Optional[ActionDefinition]:
        """Get the action definition for a capability."""
        return self._actions.get(capability_id)

    def get_actions_for_domain(self, domain_id: str) -> List[ActionDefinition]:
        """Get all action definitions for a domain."""
        return [
            a for a in self._actions.values()
            if a.domain_id == domain_id
        ]

    def get_all_actions(self) -> Dict[str, ActionDefinition]:
        """Get all registered actions."""
        return dict(self._actions)

    def has_action(self, capability_id: str) -> bool:
        """Check if an action exists for a capability."""
        return capability_id in self._actions

    # ─── Discovery Helpers ────────────────────────────────────────────

    def get_input_requirements(self, capability_id: str) -> Optional[List[FieldSchema]]:
        """
        Get the input fields needed for a capability.
        This is what the AI uses to ask the user the right questions.
        """
        action = self._actions.get(capability_id)
        if not action:
            return None
        return action.all_input_fields

    def get_required_inputs(self, capability_id: str) -> Optional[List[FieldSchema]]:
        """Get only the required input fields for a capability."""
        fields = self.get_input_requirements(capability_id)
        if fields is None:
            return None
        return [f for f in fields if f.required]

    def get_discovery_chain(self, capability_id: str) -> List[str]:
        """
        Get the chain of discovery capabilities needed before
        executing a capability.

        For example, "agents.update" needs an agent_id, so its
        discovery_capability is "agents.list". If "agents.list"
        also had a discovery capability, it would be included too.

        Returns capability IDs in order (first = run first).
        """
        chain = []
        visited = set()
        current = capability_id

        while current:
            if current in visited:
                break  # Prevent cycles
            visited.add(current)

            action = self._actions.get(current)
            if not action or not action.discovery_capability:
                break

            chain.insert(0, action.discovery_capability)
            current = action.discovery_capability

        return chain

    def get_reference_fields(self, capability_id: str) -> List[Tuple[FieldSchema, str]]:
        """
        Get fields that reference other domains.
        Returns (field, referenced_domain) pairs.

        This helps the AI understand which IDs need to be resolved
        by looking up entities in other domains first.
        """
        action = self._actions.get(capability_id)
        if not action:
            return []

        refs = []
        for f in action.all_input_fields:
            if f.field_type == FieldType.REFERENCE and f.reference_domain:
                refs.append((f, f.reference_domain))
        return refs

    # ─── Coverage Analysis ────────────────────────────────────────────

    def get_coverage_report(self) -> Dict:
        """
        Analyze which capabilities have action mappings and which don't.
        Useful for tracking completeness of the action layer.
        """
        all_caps = set()
        for domain_id, domain in self._domain_registry.get_all_domains().items():
            for cap in domain.capabilities:
                all_caps.add(cap.id)

        mapped = set(self._actions.keys())
        unmapped = all_caps - mapped
        extra = mapped - all_caps  # Actions without capabilities (shouldn't happen)

        by_domain = {}
        for domain_id, domain in self._domain_registry.get_all_domains().items():
            domain_caps = {c.id for c in domain.capabilities}
            domain_mapped = domain_caps & mapped
            domain_unmapped = domain_caps - mapped
            by_domain[domain_id] = {
                "total": len(domain_caps),
                "mapped": len(domain_mapped),
                "unmapped": sorted(domain_unmapped),
                "coverage": (
                    len(domain_mapped) / len(domain_caps) * 100
                    if domain_caps else 100
                ),
            }

        return {
            "total_capabilities": len(all_caps),
            "mapped": len(mapped),
            "unmapped": sorted(unmapped),
            "extra": sorted(extra),
            "coverage_pct": (
                len(mapped) / len(all_caps) * 100
                if all_caps else 100
            ),
            "by_domain": by_domain,
        }

    # ─── Validation ───────────────────────────────────────────────────

    def validate(self) -> ValidationResult:
        """Full validation of the action registry."""
        result = ValidationResult()

        for cap_id, action in self._actions.items():
            # Re-validate each action
            errors = action.validate()
            for err in errors:
                result.add_error(cap_id, err, "ACTION_INVALID")

            # Verify capability still exists
            if not self._domain_registry.get_capability(cap_id):
                result.add_error(
                    cap_id,
                    f"Action '{cap_id}' references removed capability",
                    "ORPHANED_ACTION"
                )

        self._check_coverage(result)

        if result.is_valid:
            result.add_info(
                "_action_registry",
                f"Action registry valid: {len(self._actions)} actions",
                "REGISTRY_VALID"
            )

        return result

    # ─── Serialization ────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize the action registry."""
        return {
            "actions": {
                cap_id: action.to_dict()
                for cap_id, action in self._actions.items()
            },
            "stats": {
                "action_count": len(self._actions),
                "initialized": self._initialized,
            },
        }

    # ─── For the AI: Readable Summaries ───────────────────────────────

    def describe_action(self, capability_id: str) -> Optional[str]:
        """
        Get a human/AI-readable description of how to execute a capability.
        This is what the AI reads to understand what inputs are needed.
        """
        action = self._actions.get(capability_id)
        if not action:
            return None

        lines = [
            f"=== Action: {capability_id} ===",
            f"Description: {action.description}",
        ]

        if action.notes:
            lines.append(f"Notes: {action.notes}")

        if action.is_simple:
            route = action.primary_route
            lines.append(f"\nAPI Call: {route.method} {route.path}")
            lines.append(f"Encoding: {route.encoding.value}")

            if route.required_fields:
                lines.append("\nRequired Inputs:")
                for f in route.required_fields:
                    line = f"  * {f.name} ({f.field_type.value})"
                    if f.description:
                        line += f" — {f.description}"
                    if f.choices:
                        line += f" [choices: {', '.join(f.choices)}]"
                    if f.reference_domain:
                        line += f" [ref: {f.reference_domain}]"
                    lines.append(line)

            if route.optional_fields:
                lines.append("\nOptional Inputs:")
                for f in route.optional_fields:
                    line = f"  * {f.name} ({f.field_type.value})"
                    if f.default is not None:
                        line += f" = {f.default}"
                    if f.description:
                        line += f" — {f.description}"
                    lines.append(line)

            if route.response_mappings:
                lines.append("\nOutputs:")
                for m in route.response_mappings:
                    lines.append(
                        f"  → {m.output_name} ({m.field_type.value})"
                        f" from response.{m.source_path}"
                    )

        elif action.is_sequence:
            lines.append(f"\nMulti-step action ({len(action.sequence.steps)} calls):")
            for i, step in enumerate(action.sequence.steps):
                lines.append(f"\n  Step {i+1}: {step.method} {step.path}")
                lines.append(f"  {step.description}")
                if step.required_fields:
                    fields = ", ".join(f.name for f in step.required_fields)
                    lines.append(f"  Inputs: {fields}")
                if step.response_mappings:
                    outputs = ", ".join(m.output_name for m in step.response_mappings)
                    lines.append(f"  Outputs: {outputs}")

        if action.suggested_prechecks:
            lines.append(
                f"\nPre-checks: {', '.join(action.suggested_prechecks)}"
            )
        if action.suggested_followups:
            lines.append(
                f"Follow-ups: {', '.join(action.suggested_followups)}"
            )

        if action.is_destructive:
            lines.append("\n⚠ DESTRUCTIVE: This action cannot be undone")
        if action.requires_confirmation:
            lines.append("⚠ Requires user confirmation before execution")

        return "\n".join(lines)

    def describe_domain_actions(self, domain_id: str) -> Optional[str]:
        """Get a summary of all actions available in a domain."""
        actions = self.get_actions_for_domain(domain_id)
        if not actions:
            return None

        lines = [f"=== Actions for domain: {domain_id} ===\n"]

        by_category = {}
        for action in actions:
            cap_entry = self._domain_registry.get_capability(action.capability_id)
            if cap_entry:
                _, cap = cap_entry
                category = cap.category
            else:
                category = "unknown"
            by_category.setdefault(category, []).append(action)

        for category in sorted(by_category.keys()):
            lines.append(f"[{category.upper()}]")
            for action in by_category[category]:
                route_info = ""
                if action.is_simple:
                    r = action.primary_route
                    route_info = f"{r.method} {r.path}"
                elif action.is_sequence:
                    route_info = f"{len(action.sequence.steps)}-step sequence"

                required_count = len([
                    f for f in action.all_input_fields if f.required
                ])
                lines.append(
                    f"  {action.capability_id}: {action.description}"
                )
                lines.append(
                    f"    → {route_info} "
                    f"({required_count} required inputs)"
                )
            lines.append("")

        return "\n".join(lines)

    # ─── Internal ─────────────────────────────────────────────────────

    def _check_coverage(self, result: ValidationResult):
        """Add warnings for unmapped capabilities."""
        all_caps = set()
        for domain_id, domain in self._domain_registry.get_all_domains().items():
            for cap in domain.capabilities:
                all_caps.add(cap.id)

        mapped = set(self._actions.keys())
        unmapped = all_caps - mapped

        if unmapped:
            result.add_info(
                "_coverage",
                f"{len(unmapped)} capabilities without action mappings: "
                f"{', '.join(sorted(unmapped))}",
                "UNMAPPED_CAPABILITIES"
            )

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def action_count(self) -> int:
        return len(self._actions)
