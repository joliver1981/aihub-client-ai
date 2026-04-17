"""
Builder Agent - Build Planner
==============================
Takes user intent and the domain registry to produce validated,
dependency-ordered execution plans.

The planner is responsible for:
1. Identifying which domains are relevant to a user request
2. Determining what capabilities are needed
3. Ordering steps by their dependencies
4. Validating the plan is executable before handing it off

Every stage includes validation. Plans that don't validate are
never executed.
"""

import logging
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field

from ..registry.domain_registry import DomainRegistry
from ..registry.domains import CapabilityDefinition
from ..validation.validators import (
    PlanValidator,
    ValidationResult,
    FieldValidator,
    SchemaValidator,
)

logger = logging.getLogger(__name__)


class StepStatus(Enum):
    """Status of a plan step through its lifecycle."""
    PENDING = "pending"         # Not yet ready to execute
    READY = "ready"             # Dependencies met, can execute
    EXECUTING = "executing"     # Currently running
    COMPLETED = "completed"     # Successfully done
    FAILED = "failed"           # Execution failed
    SKIPPED = "skipped"         # Skipped (e.g., conditional branch)
    ROLLED_BACK = "rolled_back" # Was completed, then undone


class PlanStatus(Enum):
    """Overall plan status."""
    DRAFT = "draft"           # Being constructed
    VALIDATED = "validated"   # Passed validation
    EXECUTING = "executing"   # In progress
    COMPLETED = "completed"   # All steps done
    FAILED = "failed"         # A step failed
    CANCELLED = "cancelled"   # User cancelled


@dataclass
class StepInput:
    """Describes an input required by a plan step."""
    name: str
    description: str
    source: Optional[str] = None
    # Source format: "step:<step_id>.<output_name>" or "user:<param>" or "context:<key>"
    required: bool = True
    default: Any = None

    def to_dict(self) -> dict:
        result = {
            "name": self.name,
            "description": self.description,
            "required": self.required,
        }
        if self.source:
            result["source"] = self.source
        if self.default is not None:
            result["default"] = self.default
        return result


@dataclass
class StepOutput:
    """Describes an output produced by a plan step."""
    name: str
    description: str

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description}


@dataclass
class PlanStep:
    """
    A single step in an execution plan.
    
    Each step maps to a capability in the domain registry and includes
    all the context needed for execution.
    """
    step_id: str
    domain: str
    action: str  # Capability ID (e.g., "agents.create")
    description: str
    
    # Dependencies and data flow
    depends_on: List[str] = field(default_factory=list)
    inputs: List[StepInput] = field(default_factory=list)
    outputs: List[StepOutput] = field(default_factory=list)
    
    # Execution parameters (filled in by the AI or defaults)
    parameters: Dict[str, Any] = field(default_factory=dict)
    
    # Lifecycle
    status: StepStatus = StepStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    
    # Metadata
    estimated_duration: Optional[str] = None  # "fast", "medium", "slow"
    is_reversible: bool = False
    rollback_action: Optional[str] = None  # Capability ID for undo

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "domain": self.domain,
            "action": self.action,
            "description": self.description,
            "depends_on": self.depends_on,
            "inputs": [i.to_dict() for i in self.inputs],
            "outputs": [o.name for o in self.outputs],
            "parameters": self.parameters,
            "status": self.status.value,
            "estimated_duration": self.estimated_duration,
            "is_reversible": self.is_reversible,
        }

    @property
    def is_terminal(self) -> bool:
        """Whether this step is in a final state."""
        return self.status in (
            StepStatus.COMPLETED, StepStatus.FAILED,
            StepStatus.SKIPPED, StepStatus.ROLLED_BACK
        )


@dataclass
class BuildPlan:
    """
    A complete execution plan with metadata, steps, and validation state.
    """
    plan_id: str
    goal: str
    description: str
    steps: List[PlanStep] = field(default_factory=list)
    status: PlanStatus = PlanStatus.DRAFT
    
    # Discovery results
    relevant_domains: List[str] = field(default_factory=list)
    required_context: Dict[str, Any] = field(default_factory=dict)
    
    # Validation
    validation_result: Optional[ValidationResult] = None
    
    # Metadata
    created_by: str = "builder_agent"

    def to_dict(self) -> dict:
        result = {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "description": self.description,
            "status": self.status.value,
            "relevant_domains": self.relevant_domains,
            "required_context": self.required_context,
            "steps": [s.to_dict() for s in self.steps],
            "step_count": len(self.steps),
        }
        if self.validation_result:
            result["validation"] = self.validation_result.to_dict()
        return result

    def get_step(self, step_id: str) -> Optional[PlanStep]:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None

    def get_ready_steps(self) -> List[PlanStep]:
        """Get steps whose dependencies are all satisfied."""
        completed_ids = {
            s.step_id for s in self.steps
            if s.status == StepStatus.COMPLETED
        }
        ready = []
        for step in self.steps:
            if step.status != StepStatus.PENDING:
                continue
            deps_met = all(d in completed_ids for d in step.depends_on)
            if deps_met:
                ready.append(step)
        return ready

    @property
    def is_complete(self) -> bool:
        return all(s.is_terminal for s in self.steps)

    @property
    def progress(self) -> float:
        if not self.steps:
            return 0.0
        completed = sum(1 for s in self.steps if s.status == StepStatus.COMPLETED)
        return completed / len(self.steps)

    @property
    def summary(self) -> str:
        lines = [
            f"Plan: {self.goal} ({self.status.value})",
            f"Steps: {len(self.steps)} | Progress: {self.progress:.0%}",
        ]
        for step in self.steps:
            status_icon = {
                "pending": "○",
                "ready": "◉",
                "executing": "▶",
                "completed": "✓",
                "failed": "✗",
                "skipped": "–",
                "rolled_back": "↩",
            }.get(step.status.value, "?")
            lines.append(f"  {status_icon} [{step.domain}] {step.description}")
        return "\n".join(lines)


class BuildPlanner:
    """
    The main planning engine.
    
    Takes a user's intent (as structured input from the AI) and the
    domain registry to produce a validated execution plan.
    
    Workflow:
        1. validate_intent() - Check the input is well-formed
        2. discover_domains() - Find relevant platform areas
        3. resolve_capabilities() - Map intent to specific capabilities
        4. build_plan() - Assemble ordered steps
        5. validate_plan() - Full plan validation
    
    Each method returns a ValidationResult so the AI can see exactly
    what passed or failed and adjust accordingly.
    """

    def __init__(self, registry: DomainRegistry):
        self.registry = registry
        self._intent_schema = self._build_intent_schema()

    # ─── Step 1: Validate Intent ──────────────────────────────────────

    def validate_intent(self, intent: dict) -> ValidationResult:
        """
        Validate that the structured intent from the AI is well-formed.
        
        Expected intent format:
        {
            "goal": "Create an agent that can process invoices",
            "details": "Should extract amounts, dates, and vendor info",
            "target_domains": ["agents", "documents"],  # optional hints
            "constraints": {                             # optional
                "tier": "professional",
                "must_include": ["document_extraction"],
            }
        }
        """
        return self._intent_schema.validate(intent)

    # ─── Step 2: Discover Relevant Domains ────────────────────────────

    def discover_domains(self, intent: dict) -> Tuple[List[str], ValidationResult]:
        """
        Identify which platform domains are relevant to the intent.
        Uses concept matching, explicit hints, and dependency expansion.
        
        Returns:
            Tuple of (domain_ids, validation_result)
        """
        result = ValidationResult()
        discovered = set()

        # 1. Use explicit domain hints if provided
        target_domains = intent.get("target_domains", [])
        for domain_id in target_domains:
            if self.registry.get_domain(domain_id):
                discovered.add(domain_id)
            else:
                result.add_warning(
                    "target_domains",
                    f"Hinted domain '{domain_id}' not found in registry",
                    "UNKNOWN_HINTED_DOMAIN"
                )

        # 2. Concept matching from goal and details text
        text_to_search = (
            intent.get("goal", "") + " " +
            intent.get("details", "")
        )
        terms = [t.lower().strip() for t in text_to_search.split() if len(t) > 2]
        concept_matches = self.registry.find_relevant_domains(terms)
        for domain_id, score in concept_matches:
            discovered.add(domain_id)

        # 3. Expand with dependencies
        expanded = set()
        for domain_id in discovered:
            deps = self.registry.get_dependencies(domain_id, recursive=True)
            expanded.update(deps)
        discovered.update(expanded)

        # 4. Validate we found something
        if not discovered:
            result.add_error(
                "domains",
                "Could not identify any relevant platform domains for this intent. "
                "Consider providing target_domains hints.",
                "NO_DOMAINS_FOUND"
            )
        else:
            result.add_info(
                "domains",
                f"Discovered {len(discovered)} relevant domains: "
                f"{', '.join(sorted(discovered))}",
                "DOMAINS_DISCOVERED"
            )

        result.validated_data = {"domains": sorted(discovered)}
        return sorted(discovered), result

    # ─── Step 3: Resolve Capabilities ─────────────────────────────────

    def resolve_capabilities(self, intent: dict,
                              domains: List[str]) -> Tuple[List[Tuple[str, CapabilityDefinition]], ValidationResult]:
        """
        Given the intent and relevant domains, determine which specific
        capabilities should be used.
        
        Returns capabilities with their domain IDs plus validation results.
        This is a suggestion engine - the AI makes final decisions.
        """
        result = ValidationResult()
        suggested_caps = []

        # Search for capabilities matching the intent
        search_text = intent.get("goal", "") + " " + intent.get("details", "")
        search_results = self.registry.search_capabilities(search_text)

        # Filter to only capabilities in relevant domains
        for domain_id, cap, score in search_results:
            if domain_id in domains:
                suggested_caps.append((domain_id, cap))

        # Check constraints
        constraints = intent.get("constraints", {})
        must_include = constraints.get("must_include", [])
        
        found_cap_ids = {cap.id for _, cap in suggested_caps}
        for required_tag in must_include:
            tag_caps = self.registry.find_capabilities_by_tag(required_tag)
            tag_found = any(cap.id in found_cap_ids for _, cap in tag_caps)
            if not tag_found and tag_caps:
                # Auto-add matching capabilities
                for domain_id, cap in tag_caps:
                    if domain_id in domains and cap.id not in found_cap_ids:
                        suggested_caps.append((domain_id, cap))
                        found_cap_ids.add(cap.id)
                        result.add_info(
                            "capabilities",
                            f"Added capability '{cap.id}' to satisfy "
                            f"constraint: {required_tag}",
                            "CAPABILITY_ADDED_BY_CONSTRAINT"
                        )

        # Tier checking
        tier = constraints.get("tier", "")
        for domain_id, cap in suggested_caps:
            if cap.tier_requirement and tier:
                tier_order = ["starter", "professional", "enterprise"]
                if tier.lower() in tier_order and cap.tier_requirement.lower() in tier_order:
                    user_tier_idx = tier_order.index(tier.lower())
                    req_tier_idx = tier_order.index(cap.tier_requirement.lower())
                    if user_tier_idx < req_tier_idx:
                        result.add_warning(
                            f"capabilities.{cap.id}",
                            f"Capability '{cap.id}' requires tier "
                            f"'{cap.tier_requirement}' but user tier is '{tier}'",
                            "TIER_INSUFFICIENT"
                        )

        if not suggested_caps:
            result.add_warning(
                "capabilities",
                "No specific capabilities matched. The AI should select "
                "capabilities manually from the discovered domains.",
                "NO_CAPABILITIES_MATCHED"
            )
        else:
            result.add_info(
                "capabilities",
                f"Resolved {len(suggested_caps)} capabilities",
                "CAPABILITIES_RESOLVED"
            )

        result.validated_data = {
            "capabilities": [(d, c.id) for d, c in suggested_caps]
        }
        return suggested_caps, result

    # ─── Step 4: Build Plan ───────────────────────────────────────────

    def build_plan(self, intent: dict, domains: List[str],
                   capabilities: List[Tuple[str, CapabilityDefinition]],
                   user_steps: Optional[List[dict]] = None) -> Tuple[BuildPlan, ValidationResult]:
        """
        Assemble a BuildPlan from resolved capabilities.
        
        If user_steps is provided, it uses those directly (AI-crafted steps).
        Otherwise, it auto-generates steps from the capabilities list.
        
        Returns the plan and validation results.
        """
        result = ValidationResult()

        plan = BuildPlan(
            plan_id=str(uuid.uuid4()),
            goal=intent.get("goal", ""),
            description=intent.get("details", ""),
            relevant_domains=domains,
        )

        if user_steps:
            # AI provided explicit steps - validate and adopt them
            steps_result = self._build_steps_from_user(user_steps, domains)
            result.merge(steps_result)
            if steps_result.validated_data:
                plan.steps = steps_result.validated_data.get("steps", [])
        else:
            # Auto-generate from capabilities
            steps_result = self._auto_generate_steps(capabilities)
            result.merge(steps_result)
            if steps_result.validated_data:
                plan.steps = steps_result.validated_data.get("steps", [])

        # Collect required context across all steps
        for step in plan.steps:
            cap_entry = self.registry.get_capability(step.action)
            if cap_entry:
                _, cap = cap_entry
                for ctx in cap.required_context:
                    if ctx not in plan.required_context:
                        plan.required_context[ctx] = None  # Needs to be filled

        result.validated_data = {"plan": plan}
        return plan, result

    # ─── Step 5: Validate Plan ────────────────────────────────────────

    def validate_plan(self, plan: BuildPlan) -> ValidationResult:
        """
        Full validation of an assembled plan.
        This is the final gate before execution can begin.
        """
        # Convert plan to the format PlanValidator expects
        plan_data = {
            "goal": plan.goal,
            "steps": [s.to_dict() for s in plan.steps],
        }

        domain_ids = self.registry.get_domain_ids()
        result = PlanValidator.validate_plan(plan_data, domain_ids)

        # Additional semantic validations
        result.merge(self._validate_capability_references(plan))
        result.merge(self._validate_context_availability(plan))
        result.merge(self._validate_cross_domain_coherence(plan))

        # Store validation result in the plan
        plan.validation_result = result
        if result.is_valid:
            plan.status = PlanStatus.VALIDATED
            logger.info(
                f"Plan '{plan.plan_id}' validated: "
                f"{len(plan.steps)} steps, "
                f"{len(plan.relevant_domains)} domains"
            )
        else:
            logger.warning(
                f"Plan '{plan.plan_id}' validation failed: "
                f"{len(result.errors)} errors"
            )

        return result

    # ─── Full Pipeline ────────────────────────────────────────────────

    def create_plan(self, intent: dict,
                    user_steps: Optional[List[dict]] = None) -> Tuple[BuildPlan, ValidationResult]:
        """
        Full planning pipeline: validate intent → discover → resolve → build → validate.
        
        This is the main entry point for the builder agent.
        Returns the plan and combined validation results from all stages.
        """
        combined = ValidationResult()

        # 1. Validate intent
        intent_result = self.validate_intent(intent)
        combined.merge(intent_result)
        if not intent_result.is_valid:
            return BuildPlan(
                plan_id=str(uuid.uuid4()),
                goal=intent.get("goal", "INVALID"),
                description="Intent validation failed",
            ), combined

        # 2. Discover domains
        domains, discover_result = self.discover_domains(intent)
        combined.merge(discover_result)
        if not discover_result.is_valid:
            return BuildPlan(
                plan_id=str(uuid.uuid4()),
                goal=intent.get("goal", ""),
                description="Domain discovery failed",
            ), combined

        # 3. Resolve capabilities
        capabilities, cap_result = self.resolve_capabilities(intent, domains)
        combined.merge(cap_result)
        # Capability resolution failures are warnings, not blockers

        # 4. Build plan
        plan, build_result = self.build_plan(intent, domains, capabilities, user_steps)
        combined.merge(build_result)
        if not build_result.is_valid:
            return plan, combined

        # 5. Validate plan
        validate_result = self.validate_plan(plan)
        combined.merge(validate_result)

        return plan, combined

    # ─── Internal Helpers ─────────────────────────────────────────────

    def _build_intent_schema(self) -> SchemaValidator:
        """Build the validation schema for intent input."""
        schema = SchemaValidator(allow_extra_fields=True)
        schema.add("goal", FieldValidator("goal").required().string().min_length(5))
        schema.add("details", FieldValidator("details").optional("").string())
        schema.add("target_domains", FieldValidator("target_domains").optional([]).list_of(str))
        schema.add("constraints", FieldValidator("constraints").optional({}).dict_type())
        return schema

    def _build_steps_from_user(self, user_steps: list,
                                domains: list) -> ValidationResult:
        """Convert AI-provided step dicts into PlanStep objects with validation."""
        result = ValidationResult()
        steps = []

        step_schema = SchemaValidator(allow_extra_fields=True)
        step_schema.add("action", FieldValidator("action").required().string())
        step_schema.add("description", FieldValidator("description").required().string())
        step_schema.add("domain", FieldValidator("domain").required().string())

        for i, step_data in enumerate(user_steps):
            # Validate step structure
            step_result = step_schema.validate(step_data)
            if not step_result.is_valid:
                for error in step_result.errors:
                    error.field = f"steps[{i}].{error.field}"
                result.merge(step_result)
                continue

            step = PlanStep(
                step_id=step_data.get("step_id", f"step_{i+1}"),
                domain=step_data["domain"],
                action=step_data["action"],
                description=step_data["description"],
                depends_on=step_data.get("depends_on", []),
                parameters=step_data.get("parameters", {}),
                estimated_duration=step_data.get("estimated_duration"),
                is_reversible=step_data.get("is_reversible", False),
                rollback_action=step_data.get("rollback_action"),
            )

            # Build inputs
            for inp_data in step_data.get("inputs", []):
                if isinstance(inp_data, dict):
                    step.inputs.append(StepInput(
                        name=inp_data.get("name", ""),
                        description=inp_data.get("description", ""),
                        source=inp_data.get("source"),
                        required=inp_data.get("required", True),
                    ))

            # Build outputs
            for out_data in step_data.get("outputs", []):
                if isinstance(out_data, dict):
                    step.outputs.append(StepOutput(
                        name=out_data.get("name", ""),
                        description=out_data.get("description", ""),
                    ))
                elif isinstance(out_data, str):
                    step.outputs.append(StepOutput(name=out_data, description=""))

            steps.append(step)

        result.validated_data = {"steps": steps}
        return result

    def _auto_generate_steps(
        self, capabilities: List[Tuple[str, CapabilityDefinition]]
    ) -> ValidationResult:
        """Auto-generate plan steps from resolved capabilities."""
        result = ValidationResult()
        steps = []
        step_id_map = {}  # capability_id -> step_id

        for i, (domain_id, cap) in enumerate(capabilities):
            step_id = f"step_{i+1}"
            step_id_map[cap.id] = step_id

            # Auto-resolve dependencies based on capability requirements
            depends_on = []
            for req_domain in cap.requires_domains:
                # Find earlier steps that operate on the required domain
                for prev_cap_id, prev_step_id in step_id_map.items():
                    prev_entry = self.registry.get_capability(prev_cap_id)
                    if prev_entry and prev_entry[0] == req_domain:
                        if prev_step_id != step_id:
                            depends_on.append(prev_step_id)

            step = PlanStep(
                step_id=step_id,
                domain=domain_id,
                action=cap.id,
                description=cap.description,
                depends_on=depends_on,
                inputs=[
                    StepInput(
                        name=ctx,
                        description=f"Required context: {ctx}",
                        required=True,
                    )
                    for ctx in cap.required_context
                ],
                outputs=[
                    StepOutput(
                        name=f"{cap.id.split('.')[-1]}_result",
                        description=f"Result of {cap.name}",
                    )
                ],
            )
            steps.append(step)

        result.validated_data = {"steps": steps}
        return result

    def _validate_capability_references(self, plan: BuildPlan) -> ValidationResult:
        """Verify all plan steps reference valid capabilities in the registry."""
        result = ValidationResult()
        for step in plan.steps:
            cap_entry = self.registry.get_capability(step.action)
            if not cap_entry:
                result.add_error(
                    f"steps.{step.step_id}.action",
                    f"Step '{step.step_id}' references unknown "
                    f"capability: '{step.action}'",
                    "UNKNOWN_CAPABILITY"
                )
        return result

    def _validate_context_availability(self, plan: BuildPlan) -> ValidationResult:
        """Check that required context values are available or will be produced."""
        result = ValidationResult()
        
        # Context can come from: plan.required_context, step outputs, or parameters
        available_context = set(plan.required_context.keys())
        
        for step in plan.steps:
            # Add outputs from completed/preceding steps
            for output in step.outputs:
                available_context.add(output.name)
        
        for step in plan.steps:
            for inp in step.inputs:
                if inp.required and not inp.source and inp.name not in available_context:
                    if inp.name not in step.parameters:
                        result.add_warning(
                            f"steps.{step.step_id}.inputs.{inp.name}",
                            f"Required input '{inp.name}' has no identified "
                            f"source. Must be provided at execution time.",
                            "UNRESOLVED_INPUT"
                        )

        return result

    def _validate_cross_domain_coherence(self, plan: BuildPlan) -> ValidationResult:
        """Verify cross-domain dependencies are respected in step ordering."""
        result = ValidationResult()
        
        # For each step, check if its capability's required domains
        # have at least one preceding step in that domain
        step_domains_before: Dict[str, Set[str]] = {}
        
        for step in plan.steps:
            # Record what domains have been touched before this step
            step_domains_before[step.step_id] = set()
            for dep_id in step.depends_on:
                dep_step = plan.get_step(dep_id)
                if dep_step:
                    step_domains_before[step.step_id].add(dep_step.domain)
                    # Include transitive domains
                    if dep_id in step_domains_before:
                        step_domains_before[step.step_id].update(
                            step_domains_before[dep_id]
                        )

            cap_entry = self.registry.get_capability(step.action)
            if cap_entry:
                _, cap = cap_entry
                for req_domain in cap.requires_domains:
                    prior_domains = step_domains_before.get(step.step_id, set())
                    if req_domain not in prior_domains and req_domain != step.domain:
                        # Check if any earlier step (by position) covers it
                        covered = False
                        for earlier in plan.steps:
                            if earlier.step_id == step.step_id:
                                break
                            if earlier.domain == req_domain:
                                covered = True
                                break
                        
                        if not covered:
                            result.add_warning(
                                f"steps.{step.step_id}",
                                f"Capability '{step.action}' requires domain "
                                f"'{req_domain}' but no preceding step "
                                f"operates in that domain",
                                "MISSING_CROSS_DOMAIN_STEP"
                            )

        return result
