"""
Source Step — reads data from a connection or file.
"""

import logging
import time
from typing import Dict, Tuple

import pandas as pd

from pipeline.models import StepDefinition, StepResult, SourceType
from pipeline.steps.base import BaseStep

logger = logging.getLogger(__name__)


class SourceStep(BaseStep):
    """
    Reads data from a configured connection source.
    Supports SQL queries, table reads, CSV uploads, and API endpoints.
    """

    def __init__(self, connection_bridge):
        self.connection_bridge = connection_bridge

    async def execute(
        self,
        step: StepDefinition,
        input_frames: Dict[str, pd.DataFrame],
    ) -> Tuple[pd.DataFrame, StepResult]:
        start = time.time()
        config = step.config
        source_type = SourceType(config.get("source_type", "sql_query"))

        try:
            if source_type == SourceType.SQL_QUERY:
                df = await self._read_sql_query(config)
            elif source_type == SourceType.TABLE:
                df = await self._read_table(config)
            elif source_type == SourceType.CSV_UPLOAD:
                df = self._read_csv(config)
            elif source_type == SourceType.API_ENDPOINT:
                df = await self._read_api(config)
            elif source_type == SourceType.DATAFRAME:
                # Read from a previous step's output (already in input_frames)
                dep_id = config.get("dataframe_step_id")
                if dep_id and dep_id in input_frames:
                    df = input_frames[dep_id]
                else:
                    raise ValueError(f"DataFrame step '{dep_id}' not found in inputs")
            else:
                raise ValueError(f"Unknown source type: {source_type}")

            duration = int((time.time() - start) * 1000)
            logger.info(f"Source step '{step.step_id}' read {len(df)} rows, {len(df.columns)} columns")
            return df, self._build_result(step, df, duration_ms=duration)

        except Exception as e:
            duration = int((time.time() - start) * 1000)
            logger.error(f"Source step '{step.step_id}' failed: {e}")
            return pd.DataFrame(), self._build_result(
                step, pd.DataFrame(), status="failed", duration_ms=duration, error=str(e)
            )

    async def _read_sql_query(self, config: dict) -> pd.DataFrame:
        """Execute a SQL query via the connection bridge."""
        connection_id = config.get("connection_id")
        query = config.get("query")
        if not connection_id or not query:
            raise ValueError("SQL query source requires 'connection_id' and 'query'")

        conn_str, conn_id, db_type = await self.connection_bridge.get_connection_string(connection_id)
        df, error = self.connection_bridge.execute_query_sync(query, conn_str)
        if error:
            raise RuntimeError(f"SQL query failed: {error}")
        return df

    async def _read_table(self, config: dict) -> pd.DataFrame:
        """Read all rows from a table."""
        connection_id = config.get("connection_id")
        table_name = config.get("table_name")
        max_rows = config.get("max_rows")
        if not connection_id or not table_name:
            raise ValueError("Table source requires 'connection_id' and 'table_name'")

        query = f"SELECT * FROM {table_name}"
        if max_rows:
            query = f"SELECT TOP {max_rows} * FROM {table_name}"

        conn_str, conn_id, db_type = await self.connection_bridge.get_connection_string(connection_id)
        df, error = self.connection_bridge.execute_query_sync(query, conn_str)
        if error:
            raise RuntimeError(f"Table read failed: {error}")
        return df

    def _read_csv(self, config: dict) -> pd.DataFrame:
        """Read a CSV file."""
        file_path = config.get("file_path")
        if not file_path:
            raise ValueError("CSV source requires 'file_path'")

        encoding = config.get("encoding", "utf-8")
        delimiter = config.get("delimiter", ",")
        return pd.read_csv(file_path, encoding=encoding, delimiter=delimiter)

    async def _read_api(self, config: dict) -> pd.DataFrame:
        """Read data from an API endpoint."""
        import httpx

        url = config.get("url")
        if not url:
            raise ValueError("API source requires 'url'")

        method = config.get("method", "GET").upper()
        headers = config.get("headers", {})
        params = config.get("params", {})
        json_path = config.get("json_path")  # JSONPath-like key to extract data

        async with httpx.AsyncClient(timeout=60.0) as client:
            if method == "GET":
                response = await client.get(url, headers=headers, params=params)
            elif method == "POST":
                body = config.get("body", {})
                response = await client.post(url, headers=headers, json=body)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()
            data = response.json()

        # Navigate to nested data if json_path specified
        if json_path:
            for key in json_path.split("."):
                if isinstance(data, dict):
                    data = data[key]
                elif isinstance(data, list) and key.isdigit():
                    data = data[int(key)]

        if isinstance(data, list):
            return pd.DataFrame(data)
        elif isinstance(data, dict):
            return pd.DataFrame([data])
        else:
            raise ValueError(f"API response at '{json_path}' is not a list or dict")
