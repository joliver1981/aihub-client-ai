"""
Destination Step — write data to a connection or file.
"""

import logging
import os
import time
import uuid
from typing import Dict, Tuple

import pandas as pd

from pipeline.models import StepDefinition, StepResult, DestinationType, WriteMode
from pipeline.steps.base import BaseStep

logger = logging.getLogger(__name__)


class DestinationStep(BaseStep):
    """
    Write data to a destination connection.

    Config:
        connection_id: int            — target connection
        dest_type: str                — sql_table | sql_insert | csv_download | api_post
        table_name: str               — for sql_table / sql_insert
        write_mode: str               — replace | append | fail
        schema: str                   — SQL schema (default: dbo)
        batch_size: int               — for sql_insert (default: 1000)
        file_path: str                — for csv_download
    """

    def __init__(self, connection_bridge):
        self.connection_bridge = connection_bridge

    async def execute(
        self,
        step: StepDefinition,
        input_frames: Dict[str, pd.DataFrame],
    ) -> Tuple[pd.DataFrame, StepResult]:
        start = time.time()

        try:
            df = self._get_single_input(step, input_frames)
            config = step.config
            dest_type = DestinationType(config.get("dest_type", "sql_table"))

            if dest_type == DestinationType.SQL_TABLE:
                await self._write_sql_table(df, config)
            elif dest_type == DestinationType.SQL_INSERT:
                await self._write_sql_insert(df, config)
            elif dest_type == DestinationType.CSV_DOWNLOAD:
                self._write_csv(df, config)
            elif dest_type == DestinationType.API_POST:
                await self._write_api(df, config)
            elif dest_type == DestinationType.DATAFRAME:
                pass  # Keep in memory, no write needed
            else:
                raise ValueError(f"Unknown destination type: {dest_type}")

            duration = int((time.time() - start) * 1000)
            logger.info(f"Destination step '{step.step_id}': wrote {len(df)} rows via {dest_type.value}")
            return df, self._build_result(step, df, duration_ms=duration)

        except Exception as e:
            duration = int((time.time() - start) * 1000)
            logger.error(f"Destination step '{step.step_id}' failed: {e}")
            return pd.DataFrame(), self._build_result(
                step, pd.DataFrame(), status="failed", duration_ms=duration, error=str(e)
            )

    async def _write_sql_table(self, df: pd.DataFrame, config: dict):
        """Write DataFrame to a SQL table using pandas to_sql."""
        import pyodbc
        from sqlalchemy import create_engine

        connection_id = config.get("connection_id")
        table_name = config.get("table_name")
        write_mode = config.get("write_mode", "replace")
        schema = config.get("schema", "dbo")

        if not connection_id or not table_name:
            raise ValueError("SQL table destination requires 'connection_id' and 'table_name'")

        conn_str, conn_id, db_type = await self.connection_bridge.get_connection_string(connection_id)

        # Use pyodbc connection string with SQLAlchemy for to_sql
        engine = create_engine(f"mssql+pyodbc:///?odbc_connect={conn_str}")
        df.to_sql(
            name=table_name,
            con=engine,
            schema=schema,
            if_exists=write_mode,
            index=False,
        )

    async def _write_sql_insert(self, df: pd.DataFrame, config: dict):
        """Write DataFrame using batch INSERT statements."""
        import pyodbc

        connection_id = config.get("connection_id")
        table_name = config.get("table_name")
        batch_size = config.get("batch_size", 1000)
        schema = config.get("schema", "dbo")

        if not connection_id or not table_name:
            raise ValueError("SQL insert destination requires 'connection_id' and 'table_name'")

        conn_str, conn_id, db_type = await self.connection_bridge.get_connection_string(connection_id)
        full_table = f"{schema}.{table_name}" if schema else table_name
        columns = ", ".join(df.columns)
        placeholders = ", ".join(["?"] * len(df.columns))
        sql = f"INSERT INTO {full_table} ({columns}) VALUES ({placeholders})"

        conn = pyodbc.connect(conn_str)
        try:
            cursor = conn.cursor()
            cursor.fast_executemany = True

            # Process in batches
            rows = df.values.tolist()
            for i in range(0, len(rows), batch_size):
                batch = rows[i:i + batch_size]
                cursor.executemany(sql, batch)

            conn.commit()
        finally:
            conn.close()

    def _write_csv(self, df: pd.DataFrame, config: dict):
        """Write DataFrame to a CSV file."""
        file_path = config.get("file_path")
        if not file_path:
            # Generate a temp file path
            temp_dir = config.get("temp_dir", "./temp/data_pipelines")
            os.makedirs(temp_dir, exist_ok=True)
            file_path = os.path.join(temp_dir, f"export_{uuid.uuid4().hex[:8]}.csv")

        encoding = config.get("encoding", "utf-8")
        df.to_csv(file_path, index=False, encoding=encoding)
        logger.info(f"CSV written to: {file_path}")

    async def _write_api(self, df: pd.DataFrame, config: dict):
        """POST data to an API endpoint."""
        import httpx

        url = config.get("url")
        if not url:
            raise ValueError("API destination requires 'url'")

        headers = config.get("headers", {"Content-Type": "application/json"})
        batch_size = config.get("batch_size", 100)

        records = df.to_dict(orient="records")

        async with httpx.AsyncClient(timeout=60.0) as client:
            for i in range(0, len(records), batch_size):
                batch = records[i:i + batch_size]
                response = await client.post(url, headers=headers, json=batch)
                response.raise_for_status()
