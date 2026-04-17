"""
Connection Bridge — fetches connection strings from the main Flask app
via internal API, and executes SQL queries using pyodbc.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pandas as pd

logger = logging.getLogger(__name__)


class ConnectionBridge:
    """
    Bridges to the existing connection system in the main Flask app.
    Uses the internal API key for service-to-service authentication.
    """

    def __init__(self, main_app_url: str, api_key: str):
        self.main_app_url = main_app_url.rstrip("/")
        self.api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "X-API-Key": self.api_key,
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ─── Connection Retrieval ────────────────────────────────────────────

    async def get_connection_string(self, connection_id: int) -> Tuple[str, int, str]:
        """
        Get connection string for a database connection.
        Calls main app's internal endpoint.

        Returns:
            Tuple of (connection_string, connection_id, database_type)
        """
        client = await self._get_client()
        try:
            response = await client.get(
                f"{self.main_app_url}/api/internal/connection-string/{connection_id}"
            )
            response.raise_for_status()
            data = response.json()
            return (
                data.get("connection_string", ""),
                data.get("connection_id", connection_id),
                data.get("database_type", "sql_server"),
            )
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to get connection string for ID {connection_id}: {e}")
            raise RuntimeError(f"Could not retrieve connection string: {e}")
        except httpx.RequestError as e:
            logger.error(f"Connection error getting connection string: {e}")
            raise RuntimeError(f"Could not connect to main app: {e}")

    async def list_connections(self) -> List[Dict[str, Any]]:
        """Get all available connections from the main app."""
        client = await self._get_client()
        try:
            response = await client.get(f"{self.main_app_url}/api/connections")
            response.raise_for_status()
            data = response.json()
            return data.get("connections", [])
        except Exception as e:
            logger.error(f"Failed to list connections: {e}")
            return []

    async def get_schema_metadata(self, connection_id: int) -> str:
        """Get table/column metadata as YAML for a connection."""
        client = await self._get_client()
        try:
            response = await client.get(
                f"{self.main_app_url}/api/internal/connection-schema/{connection_id}"
            )
            response.raise_for_status()
            data = response.json()
            return data.get("schema_yaml", "")
        except Exception as e:
            logger.error(f"Failed to get schema for connection {connection_id}: {e}")
            return ""

    async def get_tables(self, connection_id: int) -> List[Dict[str, Any]]:
        """Get table list for a connection."""
        client = await self._get_client()
        try:
            response = await client.get(
                f"{self.main_app_url}/api/internal/connection-tables/{connection_id}"
            )
            response.raise_for_status()
            data = response.json()
            return data.get("tables", [])
        except Exception as e:
            logger.error(f"Failed to get tables for connection {connection_id}: {e}")
            return []

    # ─── Query Execution ────────────────────────────────────────────────

    def execute_query_sync(
        self,
        query: str,
        conn_str: str,
    ) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
        """
        Execute a SQL query synchronously using pyodbc.
        Mirrors execute_sql_query_v2() from AppUtils.py.

        Returns:
            Tuple of (DataFrame, error_message)
            - If successful: (DataFrame, None)
            - If failed: (None, error_string)
        """
        import pyodbc

        conn = None
        try:
            conn = pyodbc.connect(conn_str, timeout=30)
            result_df = pd.read_sql_query(query, conn)
            return result_df, None
        except pyodbc.Error as e:
            return None, f"Database error: {str(e)}"
        except Exception as e:
            return None, f"Query error: {str(e)}"
        finally:
            if conn:
                conn.close()

    def execute_write_sync(
        self,
        query: str,
        conn_str: str,
        params: Optional[list] = None,
    ) -> Tuple[int, Optional[str]]:
        """
        Execute a write SQL statement (INSERT, UPDATE, DELETE).

        Returns:
            Tuple of (rows_affected, error_message)
        """
        import pyodbc

        conn = None
        try:
            conn = pyodbc.connect(conn_str, timeout=30)
            cursor = conn.cursor()
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            rows_affected = cursor.rowcount
            conn.commit()
            return rows_affected, None
        except pyodbc.Error as e:
            return 0, f"Database error: {str(e)}"
        except Exception as e:
            return 0, f"Write error: {str(e)}"
        finally:
            if conn:
                conn.close()
