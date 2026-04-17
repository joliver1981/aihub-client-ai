"""
Builder Data Test Configuration
================================

Shared fixtures for builder_data pipeline and quality module tests.
"""

import pytest
import os
import sys
from unittest.mock import MagicMock, AsyncMock
import pandas as pd

# Ensure the app root AND builder_data dir are importable.
# builder_data modules use non-prefixed imports (e.g. "from quality.comparator import ...")
# because the package runs as its own service with builder_data/ on sys.path.
APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BUILDER_DATA_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BUILDER_DATA_ROOT not in sys.path:
    sys.path.insert(0, BUILDER_DATA_ROOT)
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)


# =============================================================================
# DATAFRAME FIXTURES
# =============================================================================

@pytest.fixture
def sample_df():
    """A simple DataFrame for basic tests."""
    return pd.DataFrame({
        "name": ["Alice", "Bob", "Charlie", "Diana"],
        "age": [30, 25, 35, 28],
        "city": ["NYC", "LA", "Chicago", "NYC"],
        "email": ["alice@test.com", "bob@test.com", "charlie@test.com", "diana@test.com"],
    })


@pytest.fixture
def sample_df_with_nulls():
    """DataFrame with null values for validation/cleansing tests."""
    return pd.DataFrame({
        "name": ["Alice", None, "Charlie", "Diana"],
        "age": [30, 25, None, 28],
        "city": ["NYC", "LA", "Chicago", None],
        "score": [95.5, None, 87.3, 91.0],
    })


@pytest.fixture
def sample_df_with_duplicates():
    """DataFrame with duplicate rows for deduplication tests."""
    return pd.DataFrame({
        "name": ["Alice", "Bob", "Alice", "Charlie", "Bob"],
        "age": [30, 25, 30, 35, 25],
        "city": ["NYC", "LA", "NYC", "Chicago", "LA"],
    })


@pytest.fixture
def sample_df_numeric():
    """DataFrame with numeric columns for range/statistical tests."""
    return pd.DataFrame({
        "id": [1, 2, 3, 4, 5],
        "value": [10.5, 20.3, 15.7, 30.1, 25.0],
        "category": ["A", "B", "A", "C", "B"],
        "count": [100, 200, 150, 300, 250],
    })


@pytest.fixture
def sample_df_pair():
    """A pair of DataFrames for comparison tests."""
    df_a = pd.DataFrame({
        "id": [1, 2, 3, 4],
        "value": [10, 20, 30, 40],
        "name": ["Alice", "Bob", "Charlie", "Diana"],
    })
    df_b = pd.DataFrame({
        "id": [1, 2, 3, 5],
        "value": [10, 25, 30, 50],
        "name": ["Alice", "Bobby", "Charlie", "Eve"],
    })
    return df_a, df_b


# =============================================================================
# PIPELINE MODEL FIXTURES
# =============================================================================

@pytest.fixture
def step_definition_factory():
    """Factory for creating StepDefinition instances."""
    from pipeline.models import StepDefinition, StepType

    def _make(step_id="step_1", step_type=StepType.TRANSFORM, name="Test Step",
              config=None, depends_on=None, enabled=True):
        return StepDefinition(
            step_id=step_id,
            step_type=step_type,
            name=name,
            config=config or {},
            depends_on=depends_on or [],
            enabled=enabled,
        )
    return _make


@pytest.fixture
def pipeline_definition_factory(step_definition_factory):
    """Factory for creating PipelineDefinition instances."""
    from pipeline.models import PipelineDefinition

    def _make(pipeline_id="pipe_1", name="Test Pipeline", steps=None):
        return PipelineDefinition(
            pipeline_id=pipeline_id,
            name=name,
            steps=steps or [],
        )
    return _make


# =============================================================================
# CONNECTION BRIDGE MOCK
# =============================================================================

@pytest.fixture
def mock_connection_bridge():
    """A mocked ConnectionBridge for pipeline step tests."""
    bridge = MagicMock()
    bridge.get_connection_string = AsyncMock(
        return_value=("Driver={ODBC Driver 17};Server=test;Database=testdb;", 1, "Test Connection")
    )
    bridge.list_connections = AsyncMock(return_value=[
        {"id": 1, "name": "Test DB", "type": "sql_server"},
    ])
    bridge.get_schema_metadata = AsyncMock(return_value="table1(col1 INT, col2 VARCHAR)")
    bridge.get_tables = AsyncMock(return_value=[
        {"name": "table1", "schema": "dbo"},
    ])
    bridge.execute_query_sync = MagicMock(return_value=(
        pd.DataFrame({"col1": [1, 2], "col2": ["a", "b"]}), None
    ))
    bridge.execute_write_sync = MagicMock(return_value=(2, None))
    bridge.close = AsyncMock()
    return bridge
