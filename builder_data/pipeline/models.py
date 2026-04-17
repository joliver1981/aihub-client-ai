"""
Builder Data — Pipeline Data Models
======================================
Defines the core data structures for pipeline definitions,
step configurations, and execution results.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class StepType(Enum):
    """Types of pipeline steps."""
    SOURCE = "source"
    TRANSFORM = "transform"
    FILTER = "filter"
    COMPARE = "compare"
    SCRUB = "scrub"
    DESTINATION = "destination"


class SourceType(Enum):
    """How data is read in a SOURCE step."""
    SQL_QUERY = "sql_query"
    TABLE = "table"
    CSV_UPLOAD = "csv_upload"
    API_ENDPOINT = "api_endpoint"
    DATAFRAME = "dataframe"


class DestinationType(Enum):
    """How data is written in a DESTINATION step."""
    SQL_TABLE = "sql_table"
    SQL_INSERT = "sql_insert"
    CSV_DOWNLOAD = "csv_download"
    API_POST = "api_post"
    DATAFRAME = "dataframe"


class WriteMode(Enum):
    """How to handle existing data in the destination."""
    REPLACE = "replace"
    APPEND = "append"
    FAIL = "fail"


# ─── Step Definition ────────────────────────────────────────────────────────

@dataclass
class StepDefinition:
    """
    A single step in a pipeline.

    The `config` dict varies by step_type:

    SOURCE:
        connection_id: int
        source_type: str  (sql_query | table | csv_upload | api_endpoint)
        query: str        (for sql_query)
        table_name: str   (for table)
        file_path: str    (for csv_upload)
        url: str          (for api_endpoint)

    TRANSFORM:
        operations: list[dict]
        Each dict: {type, column, ...}
        Types: rename, cast, map_values, derive, drop_columns, split, merge_columns

    FILTER:
        conditions: list[dict]
        Each dict: {column, operator, value}
        Operators: ==, !=, >, <, >=, <=, in, not_in, contains, is_null, not_null
        aggregation: optional dict {group_by: list, aggregations: list[{column, function}]}

    COMPARE:
        key_columns: list[str]
        compare_columns: list[str] | None  (None = compare all)
        tolerance: float  (for numeric comparison)
        case_sensitive: bool

    SCRUB:
        dedup_columns: list[str] | None
        dedup_strategy: str  (exact | fuzzy)
        fuzzy_threshold: float
        keep: str  (first | last)
        cleanse_rules: list[dict]
        Each dict: {column, operation, params}

    DESTINATION:
        connection_id: int
        dest_type: str  (sql_table | sql_insert | csv_download | api_post)
        table_name: str
        write_mode: str  (replace | append | fail)
    """
    step_id: str
    step_type: StepType
    name: str
    description: str = ""
    config: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_type": self.step_type.value,
            "name": self.name,
            "description": self.description,
            "config": self.config,
            "depends_on": self.depends_on,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StepDefinition":
        return cls(
            step_id=data["step_id"],
            step_type=StepType(data["step_type"]),
            name=data["name"],
            description=data.get("description", ""),
            config=data.get("config", {}),
            depends_on=data.get("depends_on", []),
            enabled=data.get("enabled", True),
        )


# ─── Pipeline Definition ────────────────────────────────────────────────────

@dataclass
class PipelineDefinition:
    """
    A complete pipeline — an ordered DAG of steps.
    """
    pipeline_id: str
    name: str
    description: str = ""
    steps: List[StepDefinition] = field(default_factory=list)
    created_by: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def get_execution_order(self) -> List[StepDefinition]:
        """
        Topological sort of steps based on depends_on.
        Returns steps in the order they should be executed.
        Raises ValueError if the graph has a cycle.
        """
        step_map = {s.step_id: s for s in self.steps if s.enabled}
        in_degree = {sid: 0 for sid in step_map}
        adjacency: Dict[str, List[str]] = {sid: [] for sid in step_map}

        for step in step_map.values():
            for dep_id in step.depends_on:
                if dep_id in step_map:
                    adjacency[dep_id].append(step.step_id)
                    in_degree[step.step_id] += 1

        # Kahn's algorithm
        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        result: List[StepDefinition] = []

        while queue:
            sid = queue.pop(0)
            result.append(step_map[sid])
            for neighbor in adjacency[sid]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(result) != len(step_map):
            raise ValueError("Pipeline contains a cycle in step dependencies")

        return result

    def validate(self) -> List[str]:
        """Validate pipeline structure. Returns list of error messages."""
        errors: List[str] = []

        if not self.pipeline_id:
            errors.append("Pipeline must have an ID")
        if not self.name:
            errors.append("Pipeline must have a name")
        if not self.steps:
            errors.append("Pipeline must have at least one step")

        step_ids = {s.step_id for s in self.steps}
        for step in self.steps:
            if not step.step_id:
                errors.append(f"Step must have an ID")
            for dep_id in step.depends_on:
                if dep_id not in step_ids:
                    errors.append(f"Step '{step.step_id}' depends on unknown step '{dep_id}'")

        # Check for cycles
        try:
            self.get_execution_order()
        except ValueError as e:
            errors.append(str(e))

        # Validate COMPARE steps have exactly 2 dependencies
        for step in self.steps:
            if step.step_type == StepType.COMPARE and len(step.depends_on) != 2:
                errors.append(
                    f"COMPARE step '{step.step_id}' must have exactly 2 dependencies, "
                    f"got {len(step.depends_on)}"
                )

        return errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "name": self.name,
            "description": self.description,
            "steps": [s.to_dict() for s in self.steps],
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PipelineDefinition":
        return cls(
            pipeline_id=data["pipeline_id"],
            name=data["name"],
            description=data.get("description", ""),
            steps=[StepDefinition.from_dict(s) for s in data.get("steps", [])],
            created_by=data.get("created_by"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            metadata=data.get("metadata", {}),
        )


# ─── Execution Results ──────────────────────────────────────────────────────

@dataclass
class StepResult:
    """Result of executing a single pipeline step."""
    step_id: str
    status: str = "pending"  # pending, success, failed, skipped
    row_count: int = 0
    columns: List[str] = field(default_factory=list)
    preview: Optional[List[Dict[str, Any]]] = None  # First N rows as list of dicts
    quality_score: Optional[float] = None
    duration_ms: int = 0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "status": self.status,
            "row_count": self.row_count,
            "columns": self.columns,
            "preview": self.preview,
            "quality_score": self.quality_score,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


@dataclass
class PipelineResult:
    """Result of executing an entire pipeline."""
    pipeline_id: str
    status: str = "pending"  # pending, success, partial, failed
    step_results: Dict[str, StepResult] = field(default_factory=dict)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    total_duration_ms: int = 0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "status": self.status,
            "step_results": {k: v.to_dict() for k, v in self.step_results.items()},
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_duration_ms": self.total_duration_ms,
            "error": self.error,
        }
