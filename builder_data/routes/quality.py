"""
Builder Data Service — Quality Routes
========================================
Endpoints for data comparison, scrubbing, deduplication,
profiling, and validation.
"""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Dict, List, Optional

from quality.comparator import DataComparator
from quality.deduplicator import Deduplicator, DeduplicationStrategy
from quality.cleanser import DataCleanser, CleanseRule
from quality.standardizer import DataStandardizer, StandardizeRule
from quality.validator import DataValidator, ValidationRule
from quality.report import QualityReport

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/quality")

connection_bridge = None


def init_quality_routes(_connection_bridge):
    global connection_bridge
    connection_bridge = _connection_bridge


# ─── Request Models ──────────────────────────────────────────────────────

class DataSourceSpec(BaseModel):
    """Specifies a data source — either a connection+query or a stored DataFrame."""
    connection_id: Optional[int] = None
    query: Optional[str] = None
    table_name: Optional[str] = None

    def get_query(self) -> str:
        if self.query:
            return self.query
        if self.table_name:
            return f"SELECT * FROM {self.table_name}"
        raise ValueError("Must specify either 'query' or 'table_name'")


class CompareRequest(BaseModel):
    source_a: DataSourceSpec
    source_b: DataSourceSpec
    key_columns: List[str]
    compare_columns: Optional[List[str]] = None
    tolerance: float = 0.0
    case_sensitive: bool = True


class ScrubRequest(BaseModel):
    source: DataSourceSpec
    cleanse_rules: List[Dict[str, Any]] = []
    standardize_rules: List[Dict[str, Any]] = []


class DeduplicateRequest(BaseModel):
    source: DataSourceSpec
    key_columns: List[str]
    strategy: str = "exact"
    fuzzy_threshold: float = 0.85
    keep: str = "first"


class ProfileRequest(BaseModel):
    source: DataSourceSpec


class ValidateRequest(BaseModel):
    source: DataSourceSpec
    rules: List[Dict[str, Any]]


# ─── Helper ──────────────────────────────────────────────────────────────

async def _load_source(spec: DataSourceSpec):
    """Load a DataFrame from a DataSourceSpec via connection bridge."""
    if connection_bridge is None:
        raise HTTPException(status_code=503, detail="Connection bridge not initialized")

    if spec.connection_id is None:
        raise HTTPException(status_code=400, detail="connection_id is required")

    query = spec.get_query()
    conn_str, conn_id, db_type = await connection_bridge.get_connection_string(spec.connection_id)
    df, error = connection_bridge.execute_query_sync(query, conn_str)
    if error:
        raise HTTPException(status_code=500, detail=f"Query failed: {error}")
    return df


# ─── Endpoints ───────────────────────────────────────────────────────────

@router.post("/compare")
async def compare_data(request: CompareRequest):
    """Compare two data sources and return detailed diff."""
    try:
        df_a = await _load_source(request.source_a)
        df_b = await _load_source(request.source_b)

        comparator = DataComparator()
        result = comparator.compare(
            df_a, df_b,
            key_columns=request.key_columns,
            compare_columns=request.compare_columns,
            tolerance=request.tolerance,
            case_sensitive=request.case_sensitive,
        )

        return {
            "result": result.to_dict(),
            "summary": result.summary,
            "column_stats": result.column_stats,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Compare failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scrub")
async def scrub_data(request: ScrubRequest):
    """Apply cleansing and standardization rules to data."""
    try:
        df = await _load_source(request.source)
        total_changes = 0

        # Apply cleansing
        if request.cleanse_rules:
            rules = [CleanseRule.from_dict(r) for r in request.cleanse_rules]
            cleanser = DataCleanser()
            df, changes = cleanser.cleanse(df, rules)
            total_changes += sum(changes.values())

        # Apply standardization
        if request.standardize_rules:
            rules = [StandardizeRule.from_dict(r) for r in request.standardize_rules]
            standardizer = DataStandardizer()
            df, changes = standardizer.standardize(df, rules)
            total_changes += sum(changes.values())

        preview = df.head(50).to_dict(orient="records")

        return {
            "row_count": len(df),
            "total_changes": total_changes,
            "preview": preview,
            "columns": list(df.columns),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Scrub failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/deduplicate")
async def deduplicate_data(request: DeduplicateRequest):
    """Find and remove duplicates."""
    try:
        df = await _load_source(request.source)

        deduplicator = Deduplicator()
        result = deduplicator.deduplicate(
            df,
            key_columns=request.key_columns,
            strategy=DeduplicationStrategy(request.strategy),
            fuzzy_threshold=request.fuzzy_threshold,
            keep=request.keep,
        )

        preview = result.clean_df.head(50).to_dict(orient="records") if result.clean_df is not None else []

        return {
            "result": result.to_dict(),
            "preview": preview,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Deduplicate failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/profile")
async def profile_data(request: ProfileRequest):
    """Generate a data quality profile (nulls, types, distributions)."""
    try:
        df = await _load_source(request.source)

        comparator = DataComparator()
        profile = comparator.profile(df)

        # Generate quality report
        report_gen = QualityReport()
        report = report_gen.generate(df, profile=profile)

        return {
            "profile": profile,
            "row_count": len(df),
            "column_count": len(df.columns),
            "quality_score": report.overall_score,
            "markdown_summary": report.markdown_summary,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Profile failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/validate")
async def validate_data(request: ValidateRequest):
    """Validate data against schema/rules."""
    try:
        df = await _load_source(request.source)

        rules = [ValidationRule.from_dict(r) for r in request.rules]
        validator = DataValidator()
        report = validator.validate(df, rules)

        return {
            "report": report.to_dict(),
            "is_valid": report.is_valid,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Validate failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
