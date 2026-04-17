"""
Builder Data Service — Connection Routes
===========================================
Proxy endpoints to the main app's connection system.
"""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connections")

connection_bridge = None


def init_connection_routes(_connection_bridge):
    global connection_bridge
    connection_bridge = _connection_bridge


class QueryRequest(BaseModel):
    query: str
    max_rows: int = 1000


@router.get("/")
async def list_connections():
    """List all available connections from the main app."""
    if connection_bridge is None:
        raise HTTPException(status_code=503, detail="Connection bridge not initialized")

    connections = await connection_bridge.list_connections()
    return {"connections": connections}


@router.get("/{connection_id}/schema")
async def get_schema(connection_id: int):
    """Get table/column schema metadata for a connection."""
    if connection_bridge is None:
        raise HTTPException(status_code=503, detail="Connection bridge not initialized")

    schema_yaml = await connection_bridge.get_schema_metadata(connection_id)
    if not schema_yaml:
        return {"schema": "", "message": "No schema metadata available"}
    return {"schema": schema_yaml}


@router.get("/{connection_id}/tables")
async def list_tables(connection_id: int):
    """List all tables for a connection."""
    if connection_bridge is None:
        raise HTTPException(status_code=503, detail="Connection bridge not initialized")

    tables = await connection_bridge.get_tables(connection_id)
    return {"tables": tables}


@router.post("/{connection_id}/query")
async def execute_query(connection_id: int, request: QueryRequest):
    """Execute a query against a connection and return results."""
    if connection_bridge is None:
        raise HTTPException(status_code=503, detail="Connection bridge not initialized")

    try:
        conn_str, conn_id, db_type = await connection_bridge.get_connection_string(connection_id)

        # Inject TOP/LIMIT for safety
        query = request.query.strip()
        if request.max_rows and query.upper().startswith("SELECT"):
            # Check if TOP already present (SQL Server)
            if "TOP " not in query.upper()[:30]:
                query = query.replace("SELECT ", f"SELECT TOP {request.max_rows} ", 1)

        df, error = connection_bridge.execute_query_sync(query, conn_str)
        if error:
            raise HTTPException(status_code=500, detail=error)

        return {
            "columns": list(df.columns),
            "rows": df.values.tolist(),
            "row_count": len(df),
            "preview": df.head(50).to_dict(orient="records"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Query execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
