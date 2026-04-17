"""
Builder Agent - Validators
===========================
Core validation primitives used across all layers.
Provides composable validation with clear error reporting.
"""

import re
import logging
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class ValidationSeverity(Enum):
    """Severity level for validation issues."""
    ERROR = "error"        # Blocks execution
    WARNING = "warning"    # Allows execution but flags concern
    INFO = "info"          # Informational only


@dataclass
class ValidationIssue:
    """A single validation issue found during checking."""
    severity: ValidationSeverity
    field: str
    message: str
    code: str  # Machine-readable code like "MISSING_REQUIRED_FIELD"
    context: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        result = {
            "severity": self.severity.value,
            "field": self.field,
            "message": self.message,
            "code": self.code,
        }
        if self.context:
            result["context"] = self.context
        return result


@dataclass
class ValidationResult:
    """
    Result of a validation operation.
    Aggregates issues and provides clear pass/fail status.
    """
    issues: List[ValidationIssue] = field(default_factory=list)
    validated_data: Optional[Dict[str, Any]] = None

    @property
    def is_valid(self) -> bool:
        """True if no ERROR-level issues exist."""
        return not any(i.severity == ValidationSeverity.ERROR for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(i.severity == ValidationSeverity.WARNING for i in self.issues)

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.ERROR]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.WARNING]

    def add_error(self, field: str, message: str, code: str,
                  context: Optional[Dict] = None):
        self.issues.append(ValidationIssue(
            severity=ValidationSeverity.ERROR,
            field=field, message=message, code=code, context=context
        ))

    def add_warning(self, field: str, message: str, code: str,
                    context: Optional[Dict] = None):
        self.issues.append(ValidationIssue(
            severity=ValidationSeverity.WARNING,
            field=field, message=message, code=code, context=context
        ))

    def add_info(self, field: str, message: str, code: str,
                 context: Optional[Dict] = None):
        self.issues.append(ValidationIssue(
            severity=ValidationSeverity.INFO,
            field=field, message=message, code=code, context=context
        ))

    def merge(self, other: 'ValidationResult') -> 'ValidationResult':
        """Merge another result into this one. Returns self for chaining."""
        self.issues.extend(other.issues)
        if other.validated_data and self.validated_data:
            self.validated_data.update(other.validated_data)
        elif other.validated_data:
            self.validated_data = dict(other.validated_data)
        return self

    def to_dict(self) -> dict:
        return {
            "valid": self.is_valid,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [i.to_dict() for i in self.issues],
        }

    def __repr__(self) -> str:
        status = "VALID" if self.is_valid else "INVALID"
        return (f"ValidationResult({status}, "
                f"errors={len(self.errors)}, warnings={len(self.warnings)})")


# Sentinel for distinguishing "no value" from None
class _SentinelType:
    def __repr__(self):
        return "<UNSET>"

_SENTINEL = _SentinelType()


# ─── Field-Level Validation ───────────────────────────────────────────────

class FieldValidator:
    """
    Validates individual field values with composable rules.
    
    Usage:
        v = FieldValidator("agent_name")
        v.required().string().min_length(1).max_length(100)
        result = v.validate("My Agent")
    """

    def __init__(self, field_name: str):
        self.field_name = field_name
        self._rules: List[Tuple[Callable, str, str]] = []
        self._is_required = False
        self._default = _SENTINEL

    def required(self) -> 'FieldValidator':
        self._is_required = True
        return self

    def optional(self, default=_SENTINEL) -> 'FieldValidator':
        self._is_required = False
        if default is not _SENTINEL:
            self._default = default
        return self

    def string(self) -> 'FieldValidator':
        self._rules.append((
            lambda v: isinstance(v, str),
            f"{self.field_name} must be a string",
            "INVALID_TYPE_STRING"
        ))
        return self

    def integer(self) -> 'FieldValidator':
        self._rules.append((
            lambda v: isinstance(v, int) and not isinstance(v, bool),
            f"{self.field_name} must be an integer",
            "INVALID_TYPE_INTEGER"
        ))
        return self

    def boolean(self) -> 'FieldValidator':
        self._rules.append((
            lambda v: isinstance(v, bool),
            f"{self.field_name} must be a boolean",
            "INVALID_TYPE_BOOLEAN"
        ))
        return self

    def number(self) -> 'FieldValidator':
        self._rules.append((
            lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            f"{self.field_name} must be a number",
            "INVALID_TYPE_NUMBER"
        ))
        return self

    def list_of(self, item_type: type = None) -> 'FieldValidator':
        def check(v):
            if not isinstance(v, list):
                return False
            if item_type and v:
                return all(isinstance(i, item_type) for i in v)
            return True
        type_name = item_type.__name__ if item_type else "any"
        self._rules.append((
            check,
            f"{self.field_name} must be a list of {type_name}",
            "INVALID_TYPE_LIST"
        ))
        return self

    def dict_type(self) -> 'FieldValidator':
        self._rules.append((
            lambda v: isinstance(v, dict),
            f"{self.field_name} must be a dictionary",
            "INVALID_TYPE_DICT"
        ))
        return self

    def min_length(self, n: int) -> 'FieldValidator':
        self._rules.append((
            lambda v: hasattr(v, '__len__') and len(v) >= n,
            f"{self.field_name} must have minimum length {n}",
            "MIN_LENGTH"
        ))
        return self

    def max_length(self, n: int) -> 'FieldValidator':
        self._rules.append((
            lambda v: hasattr(v, '__len__') and len(v) <= n,
            f"{self.field_name} must have maximum length {n}",
            "MAX_LENGTH"
        ))
        return self

    def one_of(self, values: list) -> 'FieldValidator':
        self._rules.append((
            lambda v: v in values,
            f"{self.field_name} must be one of: {values}",
            "INVALID_ENUM_VALUE"
        ))
        return self

    def matches(self, pattern: str, description: str = "pattern") -> 'FieldValidator':
        compiled = re.compile(pattern)
        self._rules.append((
            lambda v: isinstance(v, str) and bool(compiled.match(v)),
            f"{self.field_name} must match {description}",
            "PATTERN_MISMATCH"
        ))
        return self

    def custom(self, check_fn: Callable[[Any], bool], message: str,
               code: str = "CUSTOM_VALIDATION") -> 'FieldValidator':
        """Add a custom validation rule."""
        self._rules.append((check_fn, message, code))
        return self

    def validate(self, value: Any) -> ValidationResult:
        result = ValidationResult()

        # Handle missing/None values
        if value is None or value is _SENTINEL:
            if self._is_required:
                result.add_error(
                    self.field_name,
                    f"{self.field_name} is required",
                    "MISSING_REQUIRED_FIELD"
                )
            elif self._default is not _SENTINEL:
                result.validated_data = {self.field_name: self._default}
            return result

        # Run all rules
        for check_fn, message, code in self._rules:
            try:
                if not check_fn(value):
                    result.add_error(self.field_name, message, code)
            except Exception as e:
                result.add_error(
                    self.field_name,
                    f"Validation error on {self.field_name}: {str(e)}",
                    "VALIDATION_EXCEPTION"
                )

        if result.is_valid:
            result.validated_data = {self.field_name: value}

        return result


# ─── Schema-Level Validation ─────────────────────────────────────────────

class SchemaValidator:
    """
    Validates a dictionary against a schema of FieldValidators.
    
    Usage:
        schema = SchemaValidator()
        schema.add("name", FieldValidator("name").required().string().min_length(1))
        schema.add("enabled", FieldValidator("enabled").optional(True).boolean())
        result = schema.validate({"name": "Test"})
    """

    def __init__(self, allow_extra_fields: bool = False):
        self._fields: Dict[str, FieldValidator] = {}
        self._allow_extra = allow_extra_fields
        self._cross_validators: List[Tuple[Callable, str, str]] = []

    def add(self, name: str, validator: FieldValidator) -> 'SchemaValidator':
        self._fields[name] = validator
        return self

    def cross_validate(self, check_fn: Callable[[dict], bool],
                       message: str, code: str = "CROSS_VALIDATION") -> 'SchemaValidator':
        """Add a validation rule that checks across multiple fields."""
        self._cross_validators.append((check_fn, message, code))
        return self

    def validate(self, data: Any) -> ValidationResult:
        result = ValidationResult(validated_data={})

        if not isinstance(data, dict):
            result.add_error("_root", "Input must be a dictionary", "INVALID_INPUT_TYPE")
            return result

        # Validate each declared field
        for name, validator in self._fields.items():
            value = data.get(name, _SENTINEL)
            field_result = validator.validate(value)
            result.merge(field_result)

        # Check for extra fields
        if not self._allow_extra:
            extra = set(data.keys()) - set(self._fields.keys())
            for field_name in extra:
                result.add_warning(
                    field_name,
                    f"Unexpected field: {field_name}",
                    "UNEXPECTED_FIELD"
                )

        # Cross-field validation (only if individual fields passed)
        if result.is_valid and result.validated_data:
            for check_fn, message, code in self._cross_validators:
                try:
                    if not check_fn(result.validated_data):
                        result.add_error("_cross", message, code)
                except Exception as e:
                    result.add_error(
                        "_cross",
                        f"Cross-validation error: {str(e)}",
                        "CROSS_VALIDATION_EXCEPTION"
                    )

        return result


# ─── Domain-Level Validation ─────────────────────────────────────────────

class DomainValidator:
    """
    Validates domain registration entries.
    Ensures each domain declaration is structurally correct and complete.
    """

    REQUIRED_DOMAIN_FIELDS = {"id", "name", "description", "capabilities", "version"}
    REQUIRED_CAPABILITY_FIELDS = {"id", "name", "description", "category"}
    VALID_CATEGORIES = {"create", "read", "update", "delete", "execute", "configure", "query"}

    @classmethod
    def validate_domain(cls, domain_data: dict) -> ValidationResult:
        """Validate a single domain registration entry."""
        result = ValidationResult()

        if not isinstance(domain_data, dict):
            result.add_error("_root", "Domain must be a dictionary", "INVALID_DOMAIN_TYPE")
            return result

        # Check required top-level fields
        for field_name in cls.REQUIRED_DOMAIN_FIELDS:
            if field_name not in domain_data:
                result.add_error(
                    field_name,
                    f"Domain missing required field: {field_name}",
                    "MISSING_DOMAIN_FIELD"
                )

        if not result.is_valid:
            return result

        # Validate ID format
        domain_id = domain_data.get("id", "")
        if not re.match(r'^[a-z][a-z0-9_]*$', domain_id):
            result.add_error(
                "id",
                f"Domain ID must be lowercase alphanumeric with underscores, got: '{domain_id}'",
                "INVALID_DOMAIN_ID_FORMAT"
            )

        # Validate version
        version = domain_data.get("version", "")
        if not re.match(r'^\d+\.\d+$', str(version)):
            result.add_error(
                "version",
                f"Version must be in format 'X.Y', got: '{version}'",
                "INVALID_VERSION_FORMAT"
            )

        # Validate capabilities
        capabilities = domain_data.get("capabilities", [])
        if not isinstance(capabilities, list):
            result.add_error(
                "capabilities",
                "Capabilities must be a list",
                "INVALID_CAPABILITIES_TYPE"
            )
        elif len(capabilities) == 0:
            result.add_warning(
                "capabilities",
                "Domain has no capabilities defined",
                "EMPTY_CAPABILITIES"
            )
        else:
            cap_ids = set()
            for i, cap in enumerate(capabilities):
                cap_result = cls._validate_capability(cap, i)
                result.merge(cap_result)
                # Check for duplicate capability IDs
                cap_id = cap.get("id", "")
                if cap_id in cap_ids:
                    result.add_error(
                        f"capabilities[{i}].id",
                        f"Duplicate capability ID: '{cap_id}'",
                        "DUPLICATE_CAPABILITY_ID"
                    )
                cap_ids.add(cap_id)

        # Validate dependencies if present
        dependencies = domain_data.get("depends_on", [])
        if not isinstance(dependencies, list):
            result.add_error(
                "depends_on",
                "depends_on must be a list",
                "INVALID_DEPENDENCIES_TYPE"
            )

        # Validate entities if present
        entities = domain_data.get("entities", [])
        if isinstance(entities, list):
            for i, entity in enumerate(entities):
                if not isinstance(entity, dict):
                    result.add_error(
                        f"entities[{i}]",
                        "Entity must be a dictionary",
                        "INVALID_ENTITY_TYPE"
                    )
                elif "name" not in entity:
                    result.add_error(
                        f"entities[{i}]",
                        "Entity missing required field: name",
                        "MISSING_ENTITY_NAME"
                    )

        if result.is_valid:
            result.validated_data = domain_data

        return result

    @classmethod
    def _validate_capability(cls, cap: Any, index: int) -> ValidationResult:
        """Validate a single capability entry within a domain."""
        result = ValidationResult()
        prefix = f"capabilities[{index}]"

        if not isinstance(cap, dict):
            result.add_error(prefix, "Capability must be a dictionary", "INVALID_CAPABILITY_TYPE")
            return result

        for field_name in cls.REQUIRED_CAPABILITY_FIELDS:
            if field_name not in cap:
                result.add_error(
                    f"{prefix}.{field_name}",
                    f"Capability missing required field: {field_name}",
                    "MISSING_CAPABILITY_FIELD"
                )

        # Validate category
        category = cap.get("category", "")
        if category and category not in cls.VALID_CATEGORIES:
            result.add_warning(
                f"{prefix}.category",
                f"Non-standard category: '{category}'. "
                f"Standard categories: {cls.VALID_CATEGORIES}",
                "NON_STANDARD_CATEGORY"
            )

        # Validate capability ID format
        cap_id = cap.get("id", "")
        if cap_id and not re.match(r'^[a-z][a-z0-9_.]*$', cap_id):
            result.add_error(
                f"{prefix}.id",
                f"Capability ID must be lowercase with dots/underscores, got: '{cap_id}'",
                "INVALID_CAPABILITY_ID_FORMAT"
            )

        return result

    @classmethod
    def validate_registry(cls, domains: Dict[str, dict]) -> ValidationResult:
        """Validate an entire domain registry."""
        result = ValidationResult()

        if not isinstance(domains, dict):
            result.add_error("_root", "Registry must be a dictionary", "INVALID_REGISTRY_TYPE")
            return result

        all_domain_ids = set()
        all_dependencies = set()

        for key, domain_data in domains.items():
            # Key must match domain ID
            domain_id = domain_data.get("id", "")
            if key != domain_id:
                result.add_error(
                    key,
                    f"Registry key '{key}' does not match domain ID '{domain_id}'",
                    "KEY_ID_MISMATCH"
                )

            domain_result = cls.validate_domain(domain_data)
            result.merge(domain_result)

            all_domain_ids.add(domain_id)
            deps = domain_data.get("depends_on", [])
            for dep in deps:
                all_dependencies.add((domain_id, dep))

        # Validate all dependency references resolve
        for source, target in all_dependencies:
            if target not in all_domain_ids:
                result.add_error(
                    f"{source}.depends_on",
                    f"Domain '{source}' depends on unknown domain: '{target}'",
                    "UNRESOLVED_DEPENDENCY"
                )

        # Check for circular dependencies
        circular = cls._detect_circular_deps(domains)
        if circular:
            result.add_error(
                "_registry",
                f"Circular dependency detected: {' -> '.join(circular)}",
                "CIRCULAR_DEPENDENCY"
            )

        return result

    @classmethod
    def _detect_circular_deps(cls, domains: Dict[str, dict]) -> Optional[List[str]]:
        """Detect circular dependencies using DFS. Returns cycle path or None."""
        UNVISITED, IN_PROGRESS, DONE = 0, 1, 2
        state = {d: UNVISITED for d in domains}
        path = []

        def dfs(node: str) -> Optional[List[str]]:
            state[node] = IN_PROGRESS
            path.append(node)
            for dep in domains.get(node, {}).get("depends_on", []):
                if dep not in state:
                    continue
                if state[dep] == IN_PROGRESS:
                    cycle_start = path.index(dep)
                    return path[cycle_start:] + [dep]
                if state[dep] == UNVISITED:
                    result = dfs(dep)
                    if result:
                        return result
            path.pop()
            state[node] = DONE
            return None

        for node in domains:
            if state[node] == UNVISITED:
                cycle = dfs(node)
                if cycle:
                    return cycle
        return None


# ─── Plan-Level Validation ───────────────────────────────────────────────

class PlanValidator:
    """
    Validates execution plans before they are run.
    Checks step ordering, dependency satisfaction, and resource availability.
    """

    VALID_STEP_STATUSES = {"pending", "ready", "executing", "completed", "failed", "skipped"}
    REQUIRED_STEP_FIELDS = {"step_id", "action", "domain"}

    @classmethod
    def validate_plan(cls, plan: dict, registry_domains: Set[str]) -> ValidationResult:
        """
        Validate an entire execution plan.
        
        Args:
            plan: The plan dictionary with metadata and steps
            registry_domains: Set of valid domain IDs from the registry
        """
        result = ValidationResult()

        if not isinstance(plan, dict):
            result.add_error("_root", "Plan must be a dictionary", "INVALID_PLAN_TYPE")
            return result

        # Validate plan metadata
        if "goal" not in plan:
            result.add_error("goal", "Plan must have a goal description", "MISSING_PLAN_GOAL")

        steps = plan.get("steps", [])
        if not isinstance(steps, list):
            result.add_error("steps", "Steps must be a list", "INVALID_STEPS_TYPE")
            return result

        if len(steps) == 0:
            result.add_error("steps", "Plan must have at least one step", "EMPTY_PLAN")
            return result

        # Validate each step
        step_ids = set()
        completed_outputs = set()

        for i, step in enumerate(steps):
            step_result = cls._validate_step(step, i, registry_domains, step_ids)
            result.merge(step_result)

            step_id = step.get("step_id", "")
            step_ids.add(step_id)

            # Track outputs for dependency checking
            outputs = step.get("outputs", [])
            if isinstance(outputs, list):
                completed_outputs.update(outputs)

        # Validate step dependencies form a valid DAG
        for i, step in enumerate(steps):
            depends_on = step.get("depends_on", [])
            if isinstance(depends_on, list):
                for dep in depends_on:
                    if dep not in step_ids:
                        result.add_error(
                            f"steps[{i}].depends_on",
                            f"Step '{step.get('step_id')}' depends on "
                            f"unknown step: '{dep}'",
                            "UNRESOLVED_STEP_DEPENDENCY"
                        )

        # Check for circular step dependencies
        if result.is_valid:
            circular = cls._detect_step_cycles(steps)
            if circular:
                result.add_error(
                    "steps",
                    f"Circular step dependency: {' -> '.join(circular)}",
                    "CIRCULAR_STEP_DEPENDENCY"
                )

        # Validate input requirements are satisfiable
        if result.is_valid:
            input_result = cls._validate_input_chain(steps)
            result.merge(input_result)

        if result.is_valid:
            result.validated_data = plan

        return result

    @classmethod
    def _validate_step(cls, step: Any, index: int,
                       registry_domains: Set[str],
                       seen_ids: Set[str]) -> ValidationResult:
        """Validate a single plan step."""
        result = ValidationResult()
        prefix = f"steps[{index}]"

        if not isinstance(step, dict):
            result.add_error(prefix, "Step must be a dictionary", "INVALID_STEP_TYPE")
            return result

        # Check required fields
        for field_name in cls.REQUIRED_STEP_FIELDS:
            if field_name not in step:
                result.add_error(
                    f"{prefix}.{field_name}",
                    f"Step missing required field: {field_name}",
                    "MISSING_STEP_FIELD"
                )

        step_id = step.get("step_id", "")

        # Check for duplicate step IDs
        if step_id in seen_ids:
            result.add_error(
                f"{prefix}.step_id",
                f"Duplicate step ID: '{step_id}'",
                "DUPLICATE_STEP_ID"
            )

        # Validate domain reference
        domain = step.get("domain", "")
        if domain and domain not in registry_domains:
            result.add_error(
                f"{prefix}.domain",
                f"Step references unknown domain: '{domain}'",
                "UNKNOWN_STEP_DOMAIN"
            )

        # Validate step has description
        if "description" not in step:
            result.add_warning(
                f"{prefix}.description",
                f"Step '{step_id}' has no description",
                "MISSING_STEP_DESCRIPTION"
            )

        # Validate inputs/outputs structure
        for list_field in ("inputs", "outputs", "depends_on"):
            val = step.get(list_field, [])
            if val is not None and not isinstance(val, list):
                result.add_error(
                    f"{prefix}.{list_field}",
                    f"{list_field} must be a list",
                    f"INVALID_{list_field.upper()}_TYPE"
                )

        return result

    @classmethod
    def _detect_step_cycles(cls, steps: list) -> Optional[List[str]]:
        """Detect circular dependencies in step ordering."""
        step_map = {s.get("step_id", ""): s for s in steps}
        UNVISITED, IN_PROGRESS, DONE = 0, 1, 2
        state = {sid: UNVISITED for sid in step_map}
        path = []

        def dfs(node: str) -> Optional[List[str]]:
            state[node] = IN_PROGRESS
            path.append(node)
            for dep in step_map.get(node, {}).get("depends_on", []):
                if dep not in state:
                    continue
                if state[dep] == IN_PROGRESS:
                    cycle_start = path.index(dep)
                    return path[cycle_start:] + [dep]
                if state[dep] == UNVISITED:
                    cycle_result = dfs(dep)
                    if cycle_result:
                        return cycle_result
            path.pop()
            state[node] = DONE
            return None

        for node in step_map:
            if state[node] == UNVISITED:
                cycle = dfs(node)
                if cycle:
                    return cycle
        return None

    @classmethod
    def _validate_input_chain(cls, steps: list) -> ValidationResult:
        """
        Validate that each step's required inputs can be satisfied
        by outputs of preceding steps or are explicitly provided.
        """
        result = ValidationResult()
        available_outputs = set()

        # Build execution order (topological sort)
        step_map = {s["step_id"]: s for s in steps}
        ordered = cls._topological_sort(steps)

        for step_id in ordered:
            step = step_map[step_id]
            required_inputs = step.get("inputs", [])

            for inp in (required_inputs or []):
                if isinstance(inp, dict):
                    source = inp.get("source")
                    if source and source.startswith("step:"):
                        ref_step = source.split(":", 1)[1].split(".")[0]
                        if ref_step not in step_map:
                            result.add_error(
                                f"steps.{step_id}.inputs",
                                f"Input references unknown step: '{ref_step}'",
                                "UNRESOLVED_INPUT_SOURCE"
                            )
                        # Check the referenced step comes before this one
                        deps = step.get("depends_on", [])
                        if ref_step not in (deps or []):
                            result.add_warning(
                                f"steps.{step_id}.inputs",
                                f"Input references step '{ref_step}' but doesn't "
                                f"declare it as a dependency",
                                "IMPLICIT_DEPENDENCY"
                            )

            # Register this step's outputs
            for out in (step.get("outputs", []) or []):
                available_outputs.add(f"step:{step_id}.{out}")

        return result

    @classmethod
    def _topological_sort(cls, steps: list) -> List[str]:
        """Return step IDs in dependency-respecting order."""
        step_map = {s["step_id"]: s for s in steps}
        in_degree = {s["step_id"]: 0 for s in steps}
        adj: Dict[str, List[str]] = {s["step_id"]: [] for s in steps}

        for step in steps:
            for dep in (step.get("depends_on") or []):
                if dep in adj:
                    adj[dep].append(step["step_id"])
                    in_degree[step["step_id"]] += 1

        # BFS topological sort
        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        ordered = []

        while queue:
            node = queue.pop(0)
            ordered.append(node)
            for neighbor in adj.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return ordered


# ─── Convenience Functions ───────────────────────────────────────────────

def validate_fields(data: dict, field_specs: Dict[str, FieldValidator]) -> ValidationResult:
    """
    Quick validation of a dict against field specifications.
    
    Usage:
        result = validate_fields(data, {
            "name": FieldValidator("name").required().string(),
            "count": FieldValidator("count").optional(0).integer(),
        })
    """
    schema = SchemaValidator()
    for name, validator in field_specs.items():
        schema.add(name, validator)
    return schema.validate(data)


def validate_schema(data: dict, schema: SchemaValidator) -> ValidationResult:
    """Validate data against a SchemaValidator instance."""
    return schema.validate(data)
