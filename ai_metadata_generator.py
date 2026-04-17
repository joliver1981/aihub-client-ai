"""
AI Metadata Generator for NLQ Data Dictionary - DATABASE AGNOSTIC VERSION
Supports SQL Server, PostgreSQL, Oracle, MySQL, and other ODBC databases.
Properly handles app database queries with tenant context.

IMPORTANT: 
- Uses INFORMATION_SCHEMA which is standardized across most databases
- Queries app database with tenant context
- Queries target databases via execute_sql_query_v2
"""

import json
import re
import logging
import os
import pyodbc
from typing import List, Dict, Optional, Tuple
from AppUtils import azureQuickPrompt
from CommonUtils import get_db_connection


# =============================================
# APP DATABASE QUERY HELPER
# =============================================

def query_app_database(query: str, params: tuple = None) -> List[Dict]:
    """
    Query the application's metadata database with proper tenant context.
    This is for querying tables like Connections, llm_Tables, llm_Columns.
    
    Args:
        query: SQL query string (can use ? for parameters)
        params: Optional tuple of parameters
        
    Returns:
        List of dictionaries with results
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context - CRITICAL for RLS
        api_key = os.getenv('API_KEY')
        cursor.execute("EXEC tenant.sp_setTenantContext ?", api_key)
        
        # Execute query
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        
        # Fetch results if SELECT
        if query.strip().upper().startswith('SELECT'):
            columns = [column[0] for column in cursor.description]
            results = []
            for row in cursor.fetchall():
                results.append(dict(zip(columns, row)))
            return results
        else:
            # For INSERT/UPDATE/DELETE
            conn.commit()
            return []
            
    except Exception as e:
        logging.error(f"Error querying app database: {str(e)}")
        raise
    finally:
        cursor.close()
        conn.close()


def execute_app_database_command(query: str, params: tuple = None) -> int:
    """
    Execute INSERT/UPDATE/DELETE on app database with tenant context.
    Returns number of rows affected.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        api_key = os.getenv('API_KEY')
        cursor.execute("EXEC tenant.sp_setTenantContext ?", api_key)
        
        # Execute command
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        
        conn.commit()
        rows_affected = cursor.rowcount
        
        return rows_affected
        
    except Exception as e:
        logging.error(f"Error executing app database command: {str(e)}")
        raise
    finally:
        cursor.close()
        conn.close()


# =============================================
# DATABASE TYPE DETECTION
# =============================================

def detect_database_type(connection_string: str) -> str:
    """
    Detect database type from connection string.
    
    Returns: 'sqlserver', 'postgresql', 'mysql', 'oracle', or 'unknown'
    """
    conn_lower = connection_string.lower()
    
    if 'sql server' in conn_lower or 'sqlserver' in conn_lower:
        return 'sqlserver'
    elif 'postgresql' in conn_lower or 'postgres' in conn_lower:
        return 'postgresql'
    elif 'mysql' in conn_lower:
        return 'mysql'
    elif 'oracle' in conn_lower:
        return 'oracle'
    else:
        return 'unknown'


# =============================================
# DATABASE-AGNOSTIC DISCOVERY QUERIES
# =============================================
    
def get_table_discovery_query(db_type: str) -> str:
    """
    Get database-agnostic query to discover tables.
    Uses INFORMATION_SCHEMA which is supported by most databases.
    Returns TABLE_SCHEMA so we can build fully qualified names.
    """
    
    if db_type == 'sqlserver':
        return """
            SELECT 
                t.TABLE_SCHEMA,
                t.TABLE_NAME,
                t.TABLE_TYPE,
                (SELECT COUNT(*) 
                 FROM INFORMATION_SCHEMA.COLUMNS c 
                 WHERE c.TABLE_NAME = t.TABLE_NAME 
                 AND c.TABLE_SCHEMA = t.TABLE_SCHEMA) as column_count
            FROM INFORMATION_SCHEMA.TABLES t
            WHERE t.TABLE_SCHEMA NOT IN ('sys', 'INFORMATION_SCHEMA', 'guest', 'db_owner', 'db_accessadmin')
            AND t.TABLE_TYPE = 'BASE TABLE'
            ORDER BY t.TABLE_SCHEMA, t.TABLE_NAME
        """
    
    elif db_type == 'postgresql':
        return """
            SELECT 
                t.table_schema AS "TABLE_SCHEMA",
                t.table_name AS "TABLE_NAME",
                t.table_type AS "TABLE_TYPE",
                (SELECT COUNT(*) 
                 FROM information_schema.columns c 
                 WHERE c.table_name = t.table_name 
                 AND c.table_schema = t.table_schema) as column_count
            FROM information_schema.tables t
            WHERE t.table_schema NOT IN ('pg_catalog', 'information_schema')
            AND t.table_type = 'BASE TABLE'
            ORDER BY t.table_schema, t.table_name
        """
    
    elif db_type == 'mysql':
        return """
            SELECT 
                t.TABLE_SCHEMA,
                t.TABLE_NAME,
                t.TABLE_TYPE,
                (SELECT COUNT(*) 
                 FROM INFORMATION_SCHEMA.COLUMNS c 
                 WHERE c.TABLE_NAME = t.TABLE_NAME 
                 AND c.TABLE_SCHEMA = t.TABLE_SCHEMA) as column_count
            FROM INFORMATION_SCHEMA.TABLES t
            WHERE t.TABLE_SCHEMA NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
            AND t.TABLE_TYPE = 'BASE TABLE'
            ORDER BY t.TABLE_SCHEMA, t.TABLE_NAME
        """
    
    elif db_type == 'oracle':
        return """
            SELECT 
                owner AS "TABLE_SCHEMA",
                table_name AS "TABLE_NAME",
                'BASE TABLE' AS "TABLE_TYPE",
                (SELECT COUNT(*) 
                 FROM all_tab_columns c 
                 WHERE c.table_name = t.table_name 
                 AND c.owner = t.owner) as column_count
            FROM all_tables t
            WHERE owner = USER
            ORDER BY owner, table_name
        """
    
    else:
        # Default to standard INFORMATION_SCHEMA (works for most databases)
        return """
            SELECT 
                TABLE_SCHEMA,
                TABLE_NAME,
                TABLE_TYPE,
                (SELECT COUNT(*) 
                 FROM INFORMATION_SCHEMA.COLUMNS c 
                 WHERE c.TABLE_NAME = t.TABLE_NAME
                 AND c.TABLE_SCHEMA = t.TABLE_SCHEMA) as column_count
            FROM INFORMATION_SCHEMA.TABLES t
            WHERE TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_SCHEMA, TABLE_NAME
        """
    

def get_schema_discovery_query(db_type: str, table_name: str) -> str:
    """
    Get database-agnostic query to discover table schema (columns).
    Handles schema-qualified table names (e.g., "TS.Customers").
    """
    
    # Extract schema and table if qualified
    if '.' in table_name:
        parts = table_name.split('.')
        schema = parts[0]
        table = parts[1]
    else:
        if db_type == 'sqlserver':
            schema = 'dbo'
        elif db_type == 'postgresql':
            schema = 'public'
        elif db_type == 'snowflake':
            schema = 'PUBLIC'
        elif str(db_type).__contains__('redshift'):
            schema = 'public'
        elif str(db_type).__contains__('amazon'):
            schema = 'public'
        else:
            schema = ''
        table = table_name
    
    if db_type == 'sqlserver':
        return f"""
            SELECT 
                c.COLUMN_NAME,
                c.DATA_TYPE,
                c.CHARACTER_MAXIMUM_LENGTH,
                c.NUMERIC_PRECISION,
                c.NUMERIC_SCALE,
                c.IS_NULLABLE,
                c.COLUMN_DEFAULT,
                CASE 
                    WHEN pk.COLUMN_NAME IS NOT NULL THEN 1 
                    ELSE 0 
                END as IS_PRIMARY_KEY,
                CASE 
                    WHEN fk.COLUMN_NAME IS NOT NULL THEN 1 
                    ELSE 0 
                END as IS_FOREIGN_KEY,
                fk.REFERENCED_TABLE_NAME,
                fk.REFERENCED_COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS c
            LEFT JOIN (
                SELECT ku.TABLE_SCHEMA, ku.TABLE_NAME, ku.COLUMN_NAME
                FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
                JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku
                    ON tc.CONSTRAINT_NAME = ku.CONSTRAINT_NAME
                    AND tc.TABLE_SCHEMA = ku.TABLE_SCHEMA
                WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
            ) pk ON c.TABLE_SCHEMA = pk.TABLE_SCHEMA 
                AND c.TABLE_NAME = pk.TABLE_NAME 
                AND c.COLUMN_NAME = pk.COLUMN_NAME
            LEFT JOIN (
                SELECT 
                    ku.TABLE_SCHEMA,
                    ku.TABLE_NAME,
                    ku.COLUMN_NAME,
                    ku2.TABLE_NAME as REFERENCED_TABLE_NAME,
                    ku2.COLUMN_NAME as REFERENCED_COLUMN_NAME
                FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
                JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku
                    ON tc.CONSTRAINT_NAME = ku.CONSTRAINT_NAME
                    AND tc.TABLE_SCHEMA = ku.TABLE_SCHEMA
                JOIN INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
                    ON tc.CONSTRAINT_NAME = rc.CONSTRAINT_NAME
                JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku2
                    ON rc.UNIQUE_CONSTRAINT_NAME = ku2.CONSTRAINT_NAME
                WHERE tc.CONSTRAINT_TYPE = 'FOREIGN KEY'
            ) fk ON c.TABLE_SCHEMA = fk.TABLE_SCHEMA 
                AND c.TABLE_NAME = fk.TABLE_NAME 
                AND c.COLUMN_NAME = fk.COLUMN_NAME
            WHERE c.TABLE_SCHEMA = '{schema}'
            AND c.TABLE_NAME = '{table}'
            ORDER BY c.ORDINAL_POSITION
        """
    
    elif db_type == 'postgresql':
        return f"""
            SELECT 
                c.column_name AS "COLUMN_NAME",
                c.data_type AS "DATA_TYPE",
                c.character_maximum_length AS "CHARACTER_MAXIMUM_LENGTH",
                c.numeric_precision AS "NUMERIC_PRECISION",
                c.numeric_scale AS "NUMERIC_SCALE",
                c.is_nullable AS "IS_NULLABLE",
                c.column_default AS "COLUMN_DEFAULT",
                CASE 
                    WHEN pk.column_name IS NOT NULL THEN 1 
                    ELSE 0 
                END as "IS_PRIMARY_KEY",
                CASE 
                    WHEN fk.column_name IS NOT NULL THEN 1 
                    ELSE 0 
                END as "IS_FOREIGN_KEY",
                fk.foreign_table_name AS "REFERENCED_TABLE_NAME",
                fk.foreign_column_name AS "REFERENCED_COLUMN_NAME"
            FROM information_schema.columns c
            LEFT JOIN (
                SELECT kcu.table_schema, kcu.table_name, kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                WHERE tc.constraint_type = 'PRIMARY KEY'
            ) pk ON c.table_schema = pk.table_schema 
                AND c.table_name = pk.table_name 
                AND c.column_name = pk.column_name
            LEFT JOIN (
                SELECT 
                    kcu.table_schema,
                    kcu.table_name,
                    kcu.column_name,
                    ccu.table_name as foreign_table_name,
                    ccu.column_name as foreign_column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu
                    ON tc.constraint_name = ccu.constraint_name
                    AND tc.table_schema = ccu.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
            ) fk ON c.table_schema = fk.table_schema 
                AND c.table_name = fk.table_name 
                AND c.column_name = fk.column_name
            WHERE c.table_schema = '{schema}'
            AND c.table_name = '{table}'
            ORDER BY c.ordinal_position
        """
    
    elif db_type == 'mysql':
        return f"""
            SELECT 
                c.COLUMN_NAME,
                c.DATA_TYPE,
                c.CHARACTER_MAXIMUM_LENGTH,
                c.NUMERIC_PRECISION,
                c.NUMERIC_SCALE,
                c.IS_NULLABLE,
                c.COLUMN_DEFAULT,
                CASE 
                    WHEN c.COLUMN_KEY = 'PRI' THEN 1 
                    ELSE 0 
                END as IS_PRIMARY_KEY,
                CASE 
                    WHEN c.COLUMN_KEY = 'MUL' THEN 1 
                    ELSE 0 
                END as IS_FOREIGN_KEY,
                kcu.REFERENCED_TABLE_NAME,
                kcu.REFERENCED_COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS c
            LEFT JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
                ON c.TABLE_SCHEMA = kcu.TABLE_SCHEMA
                AND c.TABLE_NAME = kcu.TABLE_NAME
                AND c.COLUMN_NAME = kcu.COLUMN_NAME
                AND kcu.REFERENCED_TABLE_NAME IS NOT NULL
            WHERE c.TABLE_NAME = '{table}'
            ORDER BY c.ORDINAL_POSITION
        """
    
    elif db_type == 'oracle':
        if schema:
            return f"""
                SELECT 
                    column_name AS "COLUMN_NAME",
                    data_type AS "DATA_TYPE",
                    data_length AS "CHARACTER_MAXIMUM_LENGTH",
                    data_precision AS "NUMERIC_PRECISION",
                    data_scale AS "NUMERIC_SCALE",
                    CASE nullable WHEN 'Y' THEN 'YES' ELSE 'NO' END AS "IS_NULLABLE",
                    data_default AS "COLUMN_DEFAULT",
                    0 as "IS_PRIMARY_KEY",
                    0 as "IS_FOREIGN_KEY",
                    NULL as "REFERENCED_TABLE_NAME",
                    NULL as "REFERENCED_COLUMN_NAME"
                FROM all_tab_columns
                WHERE owner = '{schema}'
                AND table_name = '{table}'
                ORDER BY column_id
            """
        else:
            return f"""
                SELECT 
                    column_name AS "COLUMN_NAME",
                    data_type AS "DATA_TYPE",
                    data_length AS "CHARACTER_MAXIMUM_LENGTH",
                    data_precision AS "NUMERIC_PRECISION",
                    data_scale AS "NUMERIC_SCALE",
                    CASE nullable WHEN 'Y' THEN 'YES' ELSE 'NO' END AS "IS_NULLABLE",
                    data_default AS "COLUMN_DEFAULT",
                    0 as "IS_PRIMARY_KEY",
                    0 as "IS_FOREIGN_KEY",
                    NULL as "REFERENCED_TABLE_NAME",
                    NULL as "REFERENCED_COLUMN_NAME"
                FROM all_tab_columns
                WHERE table_name = '{table}'
                ORDER BY column_id
            """
    else:
        return f"""
            SELECT 
                COLUMN_NAME,
                DATA_TYPE,
                CHARACTER_MAXIMUM_LENGTH,
                NUMERIC_PRECISION,
                NUMERIC_SCALE,
                IS_NULLABLE,
                COLUMN_DEFAULT,
                0 as IS_PRIMARY_KEY,
                0 as IS_FOREIGN_KEY,
                NULL as REFERENCED_TABLE_NAME,
                NULL as REFERENCED_COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = '{table}'
            ORDER BY ORDINAL_POSITION
        """


def get_schema_discovery_query_legacy(db_type: str, table_name: str) -> str:
    """
    Get database-agnostic query to discover table schema.
    """
    
    if db_type == 'sqlserver':
        return f"""
            SELECT 
                c.COLUMN_NAME,
                c.DATA_TYPE,
                c.CHARACTER_MAXIMUM_LENGTH,
                c.NUMERIC_PRECISION,
                c.NUMERIC_SCALE,
                c.IS_NULLABLE,
                c.COLUMN_DEFAULT,
                CASE 
                    WHEN pk.COLUMN_NAME IS NOT NULL THEN 1 
                    ELSE 0 
                END as IS_PRIMARY_KEY,
                CASE 
                    WHEN fk.COLUMN_NAME IS NOT NULL THEN 1 
                    ELSE 0 
                END as IS_FOREIGN_KEY,
                fk.REFERENCED_TABLE_NAME,
                fk.REFERENCED_COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS c
            LEFT JOIN (
                SELECT ku.TABLE_NAME, ku.COLUMN_NAME
                FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
                JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku
                    ON tc.CONSTRAINT_NAME = ku.CONSTRAINT_NAME
                WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
            ) pk ON c.TABLE_NAME = pk.TABLE_NAME AND c.COLUMN_NAME = pk.COLUMN_NAME
            LEFT JOIN (
                SELECT 
                    ku.TABLE_NAME,
                    ku.COLUMN_NAME,
                    ku2.TABLE_NAME as REFERENCED_TABLE_NAME,
                    ku2.COLUMN_NAME as REFERENCED_COLUMN_NAME
                FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
                JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku
                    ON tc.CONSTRAINT_NAME = ku.CONSTRAINT_NAME
                JOIN INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
                    ON tc.CONSTRAINT_NAME = rc.CONSTRAINT_NAME
                JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku2
                    ON rc.UNIQUE_CONSTRAINT_NAME = ku2.CONSTRAINT_NAME
                WHERE tc.CONSTRAINT_TYPE = 'FOREIGN KEY'
            ) fk ON c.TABLE_NAME = fk.TABLE_NAME AND c.COLUMN_NAME = fk.COLUMN_NAME
            WHERE c.TABLE_NAME = '{table_name}'
            ORDER BY c.ORDINAL_POSITION
        """
    
    elif db_type == 'postgresql':
        return f"""
            SELECT 
                c.column_name AS "COLUMN_NAME",
                c.data_type AS "DATA_TYPE",
                c.character_maximum_length AS "CHARACTER_MAXIMUM_LENGTH",
                c.numeric_precision AS "NUMERIC_PRECISION",
                c.numeric_scale AS "NUMERIC_SCALE",
                c.is_nullable AS "IS_NULLABLE",
                c.column_default AS "COLUMN_DEFAULT",
                CASE 
                    WHEN pk.column_name IS NOT NULL THEN 1 
                    ELSE 0 
                END as "IS_PRIMARY_KEY",
                CASE 
                    WHEN fk.column_name IS NOT NULL THEN 1 
                    ELSE 0 
                END as "IS_FOREIGN_KEY",
                fk.foreign_table_name AS "REFERENCED_TABLE_NAME",
                fk.foreign_column_name AS "REFERENCED_COLUMN_NAME"
            FROM information_schema.columns c
            LEFT JOIN (
                SELECT kcu.table_name, kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                WHERE tc.constraint_type = 'PRIMARY KEY'
            ) pk ON c.table_name = pk.table_name AND c.column_name = pk.column_name
            LEFT JOIN (
                SELECT 
                    kcu.table_name,
                    kcu.column_name,
                    ccu.table_name as foreign_table_name,
                    ccu.column_name as foreign_column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                JOIN information_schema.constraint_column_usage ccu
                    ON tc.constraint_name = ccu.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
            ) fk ON c.table_name = fk.table_name AND c.column_name = fk.column_name
            WHERE c.table_name = '{table_name}'
            ORDER BY c.ordinal_position
        """
    
    elif db_type == 'mysql':
        return f"""
            SELECT 
                c.COLUMN_NAME,
                c.DATA_TYPE,
                c.CHARACTER_MAXIMUM_LENGTH,
                c.NUMERIC_PRECISION,
                c.NUMERIC_SCALE,
                c.IS_NULLABLE,
                c.COLUMN_DEFAULT,
                CASE 
                    WHEN c.COLUMN_KEY = 'PRI' THEN 1 
                    ELSE 0 
                END as IS_PRIMARY_KEY,
                CASE 
                    WHEN c.COLUMN_KEY = 'MUL' THEN 1 
                    ELSE 0 
                END as IS_FOREIGN_KEY,
                kcu.REFERENCED_TABLE_NAME,
                kcu.REFERENCED_COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS c
            LEFT JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
                ON c.TABLE_NAME = kcu.TABLE_NAME 
                AND c.COLUMN_NAME = kcu.COLUMN_NAME
                AND kcu.REFERENCED_TABLE_NAME IS NOT NULL
            WHERE c.TABLE_NAME = '{table_name}'
            ORDER BY c.ORDINAL_POSITION
        """
    
    elif db_type == 'oracle':
        return f"""
            SELECT 
                c.column_name AS "COLUMN_NAME",
                c.data_type AS "DATA_TYPE",
                c.data_length AS "CHARACTER_MAXIMUM_LENGTH",
                c.data_precision AS "NUMERIC_PRECISION",
                c.data_scale AS "NUMERIC_SCALE",
                c.nullable AS "IS_NULLABLE",
                c.data_default AS "COLUMN_DEFAULT",
                CASE 
                    WHEN pk.column_name IS NOT NULL THEN 1 
                    ELSE 0 
                END as "IS_PRIMARY_KEY",
                CASE 
                    WHEN fk.column_name IS NOT NULL THEN 1 
                    ELSE 0 
                END as "IS_FOREIGN_KEY",
                fk.r_table_name AS "REFERENCED_TABLE_NAME",
                fk.r_column_name AS "REFERENCED_COLUMN_NAME"
            FROM all_tab_columns c
            LEFT JOIN (
                SELECT cols.table_name, cols.column_name
                FROM all_constraints cons
                JOIN all_cons_columns cols
                    ON cons.constraint_name = cols.constraint_name
                WHERE cons.constraint_type = 'P'
            ) pk ON c.table_name = pk.table_name AND c.column_name = pk.column_name
            LEFT JOIN (
                SELECT 
                    cols.table_name,
                    cols.column_name,
                    r_cols.table_name as r_table_name,
                    r_cols.column_name as r_column_name
                FROM all_constraints cons
                JOIN all_cons_columns cols
                    ON cons.constraint_name = cols.constraint_name
                JOIN all_cons_columns r_cols
                    ON cons.r_constraint_name = r_cols.constraint_name
                WHERE cons.constraint_type = 'R'
            ) fk ON c.table_name = fk.table_name AND c.column_name = fk.column_name
            WHERE c.table_name = '{table_name}'
            ORDER BY c.column_id
        """
    
    else:
        # Default fallback
        return f"""
            SELECT 
                COLUMN_NAME,
                DATA_TYPE,
                CHARACTER_MAXIMUM_LENGTH,
                NUMERIC_PRECISION,
                NUMERIC_SCALE,
                IS_NULLABLE,
                COLUMN_DEFAULT,
                0 as IS_PRIMARY_KEY,
                0 as IS_FOREIGN_KEY,
                NULL as REFERENCED_TABLE_NAME,
                NULL as REFERENCED_COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = '{table_name}'
            ORDER BY ORDINAL_POSITION
        """

def get_row_count_query(db_type: str, table_name: str) -> str:
    """
    Get database-appropriate query for row count.
    Handles schema-qualified table names (e.g., "TS.Customers").
    
    Args:
        db_type: Database type (sqlserver, postgresql, mysql, oracle)
        table_name: Table name (may be schema-qualified)
        
    Returns:
        SQL query string to get row count
    """
    if db_type == 'sqlserver':
        # Handle schema-qualified names
        if '.' in table_name:
            parts = table_name.split('.')
            schema = parts[0]
            table = parts[1]
            qualified_name = f"[{schema}].[{table}]"
        else:
            qualified_name = f"[{table_name}]"
        
        return f"SELECT COUNT(*) as row_count FROM {qualified_name}"
        
    elif db_type == 'postgresql':
        if '.' not in table_name:
            table_name = f"public.{table_name}"
        return f"SELECT COUNT(*) as row_count FROM {table_name}"
    
    elif db_type == 'snowflake':
        if '.' not in table_name:
            table_name = f"PUBLIC.{table_name}"
        return f"SELECT COUNT(*) as row_count FROM {table_name}"
    
    elif str(db_type).__contains__('redshift'):
        if '.' not in table_name:
            table_name = f"public.{table_name}"
        return f"SELECT COUNT(*) as row_count FROM {table_name}"
    
    elif str(db_type).__contains__('amazon'):
        if '.' not in table_name:
            table_name = f"public.{table_name}"
        return f"SELECT COUNT(*) as row_count FROM {table_name}"
        
    elif db_type == 'mysql':
        return f"SELECT COUNT(*) as row_count FROM {table_name}"
        
    elif db_type == 'oracle':
        if '.' in table_name:
            return f"SELECT COUNT(*) as row_count FROM {table_name}"
        else:
             return f"""
                SELECT num_rows as row_count
                FROM all_tables
                WHERE table_name = '{table_name}'
            """
    else:
        return f"SELECT COUNT(*) as row_count FROM {table_name}"
    

def get_row_count_query_legacy(db_type: str, table_name: str) -> str:
    """
    Get database-agnostic query to estimate row count.
    """
    
    if db_type == 'sqlserver':
        return f"""
            SELECT SUM(p.rows) as row_count
            FROM sys.tables t
            INNER JOIN sys.partitions p ON t.object_id = p.object_id
            WHERE t.name = '{table_name}'
            AND p.index_id IN (0,1)
        """
    
    elif db_type == 'postgresql':
        return f"""
            SELECT reltuples::bigint AS row_count
            FROM pg_class
            WHERE relname = '{table_name}'
        """
    
    elif db_type == 'mysql':
        return f"""
            SELECT table_rows as row_count
            FROM information_schema.tables
            WHERE table_name = '{table_name}'
        """
    
    elif db_type == 'oracle':
        return f"""
            SELECT num_rows as row_count
            FROM all_tables
            WHERE table_name = '{table_name}'
        """
    
    else:
        # Fallback to COUNT (slower but works everywhere)
        return f"SELECT COUNT(*) as row_count FROM {table_name}"


# =============================================
# DATABASE DISCOVERY FUNCTIONS
# =============================================

def discover_tables_from_database(execute_query_func, connection_string: str) -> List[Dict]:
    """
    Query the actual database to discover all tables.
    Works with SQL Server, PostgreSQL, MySQL, Oracle, and other ODBC databases.
    Returns fully qualified table names (schema.table) for databases that support schemas.
    
    Args:
        execute_query_func: The execute_sql_query_v2 function
        connection_string: Database connection string
        
    Returns:
        List of dictionaries with table information including qualified names
    """
    try:
        # Detect database type
        db_type = detect_database_type(connection_string)
        logging.info(f"Detected database type: {db_type}")
        
        # Get appropriate query
        query = get_table_discovery_query(db_type)
        
        # Execute query
        result_df, error = execute_query_func(query, connection_string)
        
        if error:
            logging.error(f"Error discovering tables: {error}")
            return []
        
        if result_df is None or result_df.empty:
            return []
        
        # Convert DataFrame to list of dicts and add qualified names
        tables = []
        for row in result_df.to_dict('records'):
            table_schema = row.get('TABLE_SCHEMA', 'dbo')
            table_name = row['TABLE_NAME']
            
            # Build fully qualified name based on database type
            if db_type in ('sqlserver', 'postgresql', 'oracle'):
                # These databases support schemas
                qualified_name = f"{table_schema}.{table_name}"
            else:
                # MySQL and others - just use table name
                qualified_name = table_name
            
            tables.append({
                'TABLE_NAME': qualified_name,  # Use qualified name
                'TABLE_SCHEMA': table_schema,   # Keep original schema
                'TABLE_TYPE': row.get('TABLE_TYPE', 'BASE TABLE'),
                'column_count': row.get('column_count', 0)
            })
        
        logging.info(f"Discovered {len(tables)} tables")
        return tables
        
    except Exception as e:
        logging.error(f"Error discovering tables: {str(e)}", exc_info=True)
        return []


def get_table_schema_from_database(execute_query_func, table_name: str, connection_string: str) -> List[Dict]:
    """
    Get detailed schema information for a specific table.
    Works across multiple database types.
    """
    try:
        # Detect database type
        db_type = detect_database_type(connection_string)
        
        # Get appropriate query
        query = get_schema_discovery_query(db_type, table_name)
        
        # Execute query
        result_df, error = execute_query_func(query, connection_string)
        
        if error:
            logging.error(f"Error getting schema for {table_name}: {error}")
            return []
        
        if result_df is None or result_df.empty:
            return []
        
        # Convert DataFrame to list of dicts
        return result_df.to_dict('records')
        
    except Exception as e:
        logging.error(f"Error getting schema for {table_name}: {str(e)}", exc_info=True)
        return []

def get_sample_data(execute_query_func, table_name: str, connection_string: str, num_rows: int = 10) -> List[Dict]:
    """
    Get sample data from the table for AI analysis.
    Uses database-appropriate syntax.
    Handles schema-qualified table names (e.g., "TS.Customers").
    
    Args:
        execute_query_func: The execute_sql_query_v2 function
        table_name: Table name (may be schema-qualified like "TS.Customers")
        connection_string: Database connection string
        num_rows: Number of sample rows to retrieve
        
    Returns:
        List of dictionaries with sample data
    """
    try:
        # Detect database type
        db_type = detect_database_type(connection_string)
        
        # Build query based on database type and handle schema qualification
        if db_type == 'sqlserver':
            # SQL Server: Handle schema-qualified names
            if '.' in table_name:
                # Already qualified (e.g., "TS.Customers")
                parts = table_name.split('.')
                schema = parts[0]
                table = parts[1]
                qualified_name = f"[{schema}].[{table}]"
            else:
                # No schema specified, bracket the name
                qualified_name = f"[{table_name}]"
            
            query = f"SELECT TOP {num_rows} * FROM {qualified_name}"
            
        elif db_type == 'postgresql':
            # PostgreSQL: Schema-qualified names supported
            if '.' not in table_name:
                table_name = f"public.{table_name}"
            query = f"SELECT * FROM {table_name} LIMIT {num_rows}"
            
        elif db_type == 'mysql':
            # MySQL: No schema qualification needed in query
            query = f"SELECT * FROM {table_name} LIMIT {num_rows}"
            
        elif db_type == 'oracle':
            # Oracle: Schema-qualified names supported
            query = f"SELECT * FROM {table_name} WHERE ROWNUM <= {num_rows}"
            
        else:
            # Generic fallback
            query = f"SELECT * FROM {table_name} LIMIT {num_rows}"
        
        logging.info(f"Sample data query: {query}")
        
        # Execute query
        result_df, error = execute_query_func(query, connection_string)
        
        if error:
            logging.warning(f"Could not get sample data for {table_name}: {error}")
            return []
        
        if result_df is None or result_df.empty:
            return []
        
        # Convert DataFrame to list of dicts
        return result_df.to_dict('records')
        
    except Exception as e:
        logging.warning(f"Could not get sample data for {table_name}: {str(e)}")
        return []


def get_table_row_count(execute_query_func, table_name: str, connection_string: str) -> int:
    """
    Get approximate row count for a table.
    Uses database-appropriate method.
    """
    try:
        # Detect database type
        db_type = detect_database_type(connection_string)
        
        # Get appropriate query
        query = get_row_count_query(db_type, table_name)
        
        # Execute query
        result_df, error = execute_query_func(query, connection_string)
        
        if error:
            logging.warning(f"Could not get row count for {table_name}: {error}")
            return 0
        
        if result_df is None or result_df.empty:
            return 0
        
        row_count = result_df.iloc[0]['row_count']
        return int(row_count) if row_count is not None else 0
        
    except Exception as e:
        logging.warning(f"Could not get row count for {table_name}: {str(e)}")
        return 0

def get_column_discovery_query(db_type: str, table_name: str) -> str:
    """
    Get database-agnostic query to discover columns for a table.
    Handles schema-qualified table names.
    """
    
    # Extract schema and table if qualified
    if '.' in table_name:
        parts = table_name.split('.')
        schema = parts[0]
        table = parts[1]
    else:
        schema = 'dbo' if db_type == 'sqlserver' else 'public'
        table = table_name
    
    if db_type == 'sqlserver':
        return f"""
            SELECT 
                c.COLUMN_NAME,
                c.DATA_TYPE,
                c.CHARACTER_MAXIMUM_LENGTH,
                c.NUMERIC_PRECISION,
                c.NUMERIC_SCALE,
                c.IS_NULLABLE,
                c.COLUMN_DEFAULT,
                c.ORDINAL_POSITION,
                CASE 
                    WHEN pk.COLUMN_NAME IS NOT NULL THEN 1 
                    ELSE 0 
                END as IS_PRIMARY_KEY,
                CASE 
                    WHEN fk.COLUMN_NAME IS NOT NULL THEN 1 
                    ELSE 0 
                END as IS_FOREIGN_KEY,
                fk.REFERENCED_TABLE_SCHEMA,
                fk.REFERENCED_TABLE_NAME,
                fk.REFERENCED_COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS c
            LEFT JOIN (
                SELECT ku.TABLE_SCHEMA, ku.TABLE_NAME, ku.COLUMN_NAME
                FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
                JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku
                    ON tc.CONSTRAINT_NAME = ku.CONSTRAINT_NAME
                    AND tc.TABLE_SCHEMA = ku.TABLE_SCHEMA
                WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
            ) pk ON c.TABLE_SCHEMA = pk.TABLE_SCHEMA 
                AND c.TABLE_NAME = pk.TABLE_NAME 
                AND c.COLUMN_NAME = pk.COLUMN_NAME
            LEFT JOIN (
                SELECT 
                    ku.TABLE_SCHEMA,
                    ku.TABLE_NAME,
                    ku.COLUMN_NAME,
                    ku2.TABLE_SCHEMA as REFERENCED_TABLE_SCHEMA,
                    ku2.TABLE_NAME as REFERENCED_TABLE_NAME,
                    ku2.COLUMN_NAME as REFERENCED_COLUMN_NAME
                FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
                JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku
                    ON rc.CONSTRAINT_NAME = ku.CONSTRAINT_NAME
                    AND rc.CONSTRAINT_SCHEMA = ku.CONSTRAINT_SCHEMA
                JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku2
                    ON rc.UNIQUE_CONSTRAINT_NAME = ku2.CONSTRAINT_NAME
                    AND rc.UNIQUE_CONSTRAINT_SCHEMA = ku2.CONSTRAINT_SCHEMA
            ) fk ON c.TABLE_SCHEMA = fk.TABLE_SCHEMA 
                AND c.TABLE_NAME = fk.TABLE_NAME 
                AND c.COLUMN_NAME = fk.COLUMN_NAME
            WHERE c.TABLE_SCHEMA = '{schema}'
            AND c.TABLE_NAME = '{table}'
            ORDER BY c.ORDINAL_POSITION
        """
    
    elif db_type == 'postgresql':
        return f"""
            SELECT 
                c.column_name AS "COLUMN_NAME",
                c.data_type AS "DATA_TYPE",
                c.character_maximum_length AS "CHARACTER_MAXIMUM_LENGTH",
                c.numeric_precision AS "NUMERIC_PRECISION",
                c.numeric_scale AS "NUMERIC_SCALE",
                c.is_nullable AS "IS_NULLABLE",
                c.column_default AS "COLUMN_DEFAULT",
                c.ordinal_position AS "ORDINAL_POSITION",
                CASE 
                    WHEN pk.column_name IS NOT NULL THEN 1 
                    ELSE 0 
                END as "IS_PRIMARY_KEY",
                CASE 
                    WHEN fk.column_name IS NOT NULL THEN 1 
                    ELSE 0 
                END as "IS_FOREIGN_KEY",
                fk.foreign_table_schema AS "REFERENCED_TABLE_SCHEMA",
                fk.foreign_table_name AS "REFERENCED_TABLE_NAME",
                fk.foreign_column_name AS "REFERENCED_COLUMN_NAME"
            FROM information_schema.columns c
            LEFT JOIN (
                SELECT ku.table_schema, ku.table_name, ku.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage ku
                    ON tc.constraint_name = ku.constraint_name
                    AND tc.table_schema = ku.table_schema
                WHERE tc.constraint_type = 'PRIMARY KEY'
            ) pk ON c.table_schema = pk.table_schema 
                AND c.table_name = pk.table_name 
                AND c.column_name = pk.column_name
            LEFT JOIN (
                SELECT 
                    kcu.table_schema,
                    kcu.table_name,
                    kcu.column_name,
                    ccu.table_schema as foreign_table_schema,
                    ccu.table_name as foreign_table_name,
                    ccu.column_name as foreign_column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu
                    ON ccu.constraint_name = tc.constraint_name
                    AND ccu.table_schema = tc.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
            ) fk ON c.table_schema = fk.table_schema 
                AND c.table_name = fk.table_name 
                AND c.column_name = fk.column_name
            WHERE c.table_schema = '{schema}'
            AND c.table_name = '{table}'
            ORDER BY c.ordinal_position
        """
    
    elif db_type == 'mysql':
        return f"""
            SELECT 
                c.COLUMN_NAME,
                c.DATA_TYPE,
                c.CHARACTER_MAXIMUM_LENGTH,
                c.NUMERIC_PRECISION,
                c.NUMERIC_SCALE,
                c.IS_NULLABLE,
                c.COLUMN_DEFAULT,
                c.ORDINAL_POSITION,
                CASE 
                    WHEN c.COLUMN_KEY = 'PRI' THEN 1 
                    ELSE 0 
                END as IS_PRIMARY_KEY,
                CASE 
                    WHEN c.COLUMN_KEY = 'MUL' THEN 1 
                    ELSE 0 
                END as IS_FOREIGN_KEY,
                kcu.REFERENCED_TABLE_SCHEMA,
                kcu.REFERENCED_TABLE_NAME,
                kcu.REFERENCED_COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS c
            LEFT JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
                ON c.TABLE_SCHEMA = kcu.TABLE_SCHEMA
                AND c.TABLE_NAME = kcu.TABLE_NAME
                AND c.COLUMN_NAME = kcu.COLUMN_NAME
                AND kcu.REFERENCED_TABLE_NAME IS NOT NULL
            WHERE c.TABLE_SCHEMA = '{schema}'
            AND c.TABLE_NAME = '{table}'
            ORDER BY c.ORDINAL_POSITION
        """
    
    elif db_type == 'oracle':
        return f"""
            SELECT 
                column_name AS "COLUMN_NAME",
                data_type AS "DATA_TYPE",
                data_length AS "CHARACTER_MAXIMUM_LENGTH",
                data_precision AS "NUMERIC_PRECISION",
                data_scale AS "NUMERIC_SCALE",
                CASE nullable WHEN 'Y' THEN 'YES' ELSE 'NO' END AS "IS_NULLABLE",
                data_default AS "COLUMN_DEFAULT",
                column_id AS "ORDINAL_POSITION",
                0 as "IS_PRIMARY_KEY",
                0 as "IS_FOREIGN_KEY",
                NULL as "REFERENCED_TABLE_SCHEMA",
                NULL as "REFERENCED_TABLE_NAME",
                NULL as "REFERENCED_COLUMN_NAME"
            FROM all_tab_columns
            WHERE owner = '{schema}'
            AND table_name = '{table}'
            ORDER BY column_id
        """
    
    else:
        # Generic fallback
        return f"""
            SELECT 
                COLUMN_NAME,
                DATA_TYPE,
                CHARACTER_MAXIMUM_LENGTH,
                NUMERIC_PRECISION,
                NUMERIC_SCALE,
                IS_NULLABLE,
                COLUMN_DEFAULT,
                ORDINAL_POSITION,
                0 as IS_PRIMARY_KEY,
                0 as IS_FOREIGN_KEY,
                NULL as REFERENCED_TABLE_SCHEMA,
                NULL as REFERENCED_TABLE_NAME,
                NULL as REFERENCED_COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = '{schema}'
            AND TABLE_NAME = '{table}'
            ORDER BY ORDINAL_POSITION
        """
    
# =============================================
# AI PROMPT CONSTRUCTION (UNCHANGED)
# =============================================

def build_table_analysis_prompt(table_name: str, schema_info: List[Dict], 
                                sample_data: List[Dict], row_count: int) -> str:
    """Build a comprehensive prompt for AI to analyze a table."""
    
    # Build schema description
    schema_desc = "TABLE SCHEMA:\n"
    for col in schema_info:
        pk = " [PRIMARY KEY]" if col.get('IS_PRIMARY_KEY') else ""
        fk_info = ""
        if col.get('IS_FOREIGN_KEY') and col.get('REFERENCED_TABLE_NAME'):
            fk_info = f" [FOREIGN KEY -> {col['REFERENCED_TABLE_NAME']}.{col['REFERENCED_COLUMN_NAME']}]"
        
        nullable = "NULL" if col.get('IS_NULLABLE') == 'YES' else "NOT NULL"
        
        data_type = col.get('DATA_TYPE', 'unknown')
        if col.get('CHARACTER_MAXIMUM_LENGTH'):
            data_type += f"({col['CHARACTER_MAXIMUM_LENGTH']})"
        elif col.get('NUMERIC_PRECISION'):
            scale = col.get('NUMERIC_SCALE', 0)
            data_type += f"({col['NUMERIC_PRECISION']},{scale})"
        
        schema_desc += f"  - {col['COLUMN_NAME']}: {data_type} {nullable}{pk}{fk_info}\n"
    
    # Build sample data description
    sample_desc = "SAMPLE DATA (first 3 rows):\n"
    if sample_data:
        for i, row in enumerate(sample_data[:3], 1):
            truncated = {k: (str(v)[:50] + '...' if len(str(v)) > 50 else v) 
                        for k, v in row.items()}
            sample_desc += f"  Row {i}: {truncated}\n"
    else:
        sample_desc += "  (No sample data available)\n"
    
    # Build the comprehensive prompt (same as before)
    prompt = f"""
Analyze this database table and provide comprehensive metadata to help an AI assistant generate accurate SQL queries from natural language questions.

TABLE NAME: {table_name}
ROW COUNT: {row_count:,} rows

{schema_desc}

{sample_desc}

Based on the schema and sample data, provide a comprehensive analysis in the following JSON format:

{{
  "table_metadata": {{
    "table_description": "Clear, business-friendly description",
    "table_type": "fact|dimension|bridge|lookup|aggregate",
    "table_category": "business domain",
    "primary_key_columns": "comma-separated PK columns",
    "refresh_frequency": "real-time|hourly|daily|weekly|monthly|static",
    "common_filters": {{
      "recommended": ["WHERE clauses"],
      "required": ["always-applied WHERE clauses"]
    }},
    "business_rules": {{
      "rules": ["business rules"],
      "defaults": {{"column": "default_value"}}
    }}
  }},
  "columns": [
    {{
      "column_name": "exact_column_name",
      "column_description": "what it contains",
      "semantic_type": "identifier|name|email|phone|date|amount|etc",
      "value_format": "currency|percentage|date|etc",
      "units": "USD|kg|etc",
      "common_aggregations": "SUM,AVG,COUNT",
      "synonyms": "alternatives",
      "examples": "example values",
      "is_sensitive": true/false,
      "value_range": "range"
    }}
  ],
  "calculated_metrics": [
    {{
      "metric_name": "virtual metric",
      "calculation_formula": "SQL expression",
      "calculation_dependencies": ["columns"],
      "description": "what it represents",
      "semantic_type": "amount|percentage",
      "value_format": "currency|percentage",
      "units": "USD|etc"
    }}
  ],
  "related_tables": {{
    "commonly_joined_with": [
      {{
        "table": "TableName",
        "frequency": "high|medium|low",
        "join_type": "INNER|LEFT|RIGHT",
        "description": "why joined"
      }}
    ]
  }}
}}

Return ONLY valid JSON with no additional text.
"""
    
    return prompt


def build_table_analysis_system_prompt() -> str:
    """System prompt to guide the AI's analysis behavior."""
    return """You are an expert database metadata analyst. Generate comprehensive metadata for database tables to help an AI assistant understand the data and generate accurate SQL queries from natural language.

Focus on:
1. Business-friendly descriptions
2. Common user questions and metrics
3. Proper classifications and semantic types
4. Relationships and join patterns

Return ONLY valid JSON with no additional text."""


# =============================================
# AI ANALYSIS (UNCHANGED)
# =============================================

def analyze_table_with_ai(execute_query_func, table_name: str, connection_string: str) -> Dict:
    """Use AI to analyze a table and generate comprehensive metadata."""
    
    logging.info(f"Starting AI analysis of table: {table_name}")
    
    # Gather table information
    schema_info = get_table_schema_from_database(execute_query_func, table_name, connection_string)
    if not schema_info:
        raise ValueError(f"Could not retrieve schema for table: {table_name}")
    
    sample_data = get_sample_data(execute_query_func, table_name, connection_string, num_rows=10)
    row_count = get_table_row_count(execute_query_func, table_name, connection_string)
    
    # Build AI prompts
    prompt = build_table_analysis_prompt(table_name, schema_info, sample_data, row_count)
    system_prompt = build_table_analysis_system_prompt()
    
    # Call AI
    logging.info(f"Calling Azure OpenAI for table analysis: {table_name}")
    response = azureQuickPrompt(prompt=prompt, system=system_prompt, use_alternate_api=True)
    
    # Parse response
    metadata = parse_ai_response(response, table_name)
    
    # Add row count and enrich with schema
    metadata['row_count'] = row_count
    metadata = enrich_metadata_with_schema(metadata, schema_info)
    
    logging.info(f"Successfully analyzed table: {table_name}")
    
    return metadata


def parse_ai_response(response_text: str, table_name: str) -> Dict:
    """Parse and validate AI response."""
    
    # Remove markdown code blocks
    response_text = response_text.strip()
    response_text = re.sub(r'^```json\s*', '', response_text, flags=re.IGNORECASE)
    response_text = re.sub(r'^```\s*', '', response_text)
    response_text = re.sub(r'\s*```$', '', response_text)
    response_text = response_text.strip()
    
    # Parse JSON
    try:
        metadata = json.loads(response_text)
    except json.JSONDecodeError as e:
        logging.error(f"Failed to parse AI response for {table_name}: {str(e)}")
        raise ValueError(f"AI returned invalid JSON for {table_name}: {str(e)}")
    
    # Validate structure
    required_keys = ['table_metadata', 'columns']
    for key in required_keys:
        if key not in metadata:
            raise ValueError(f"AI response missing required key '{key}' for {table_name}")
    
    return metadata


def enrich_metadata_with_schema(metadata: Dict, schema_info: List[Dict]) -> Dict:
    """Enrich AI-generated metadata with actual schema information."""
    
    schema_lookup = {col['COLUMN_NAME']: col for col in schema_info}
    
    for col_meta in metadata['columns']:
        col_name = col_meta['column_name']
        
        if col_name in schema_lookup:
            db_col = schema_lookup[col_name]
            
            data_type = db_col['DATA_TYPE']
            if db_col.get('CHARACTER_MAXIMUM_LENGTH'):
                col_meta['data_type_precision'] = f"{data_type}({db_col['CHARACTER_MAXIMUM_LENGTH']})"
            elif db_col.get('NUMERIC_PRECISION'):
                scale = db_col.get('NUMERIC_SCALE', 0)
                col_meta['data_type_precision'] = f"{data_type}({db_col['NUMERIC_PRECISION']},{scale})"
            else:
                col_meta['data_type_precision'] = data_type
            
            col_meta['data_type'] = data_type
            col_meta['is_nullable'] = 1 if db_col['IS_NULLABLE'] == 'YES' or db_col['IS_NULLABLE'] == 'Y' else 0
            col_meta['is_primary_key'] = db_col.get('IS_PRIMARY_KEY', 0)
            col_meta['is_foreign_key'] = db_col.get('IS_FOREIGN_KEY', 0)
            
            if col_meta['is_foreign_key']:
                col_meta['foreign_key_table'] = db_col.get('REFERENCED_TABLE_NAME')
                col_meta['foreign_key_column'] = db_col.get('REFERENCED_COLUMN_NAME')
            
            if db_col.get('COLUMN_DEFAULT'):
                col_meta['default_value'] = db_col['COLUMN_DEFAULT']
    
    return metadata


if __name__ == "__main__":
    print("AI Metadata Generator Module - Database Agnostic Version")
    print("\nSupports:")
    print("  - SQL Server")
    print("  - PostgreSQL")
    print("  - MySQL")
    print("  - Oracle")
    print("  - Other ODBC databases")
    print("\nUses:")
    print("  - INFORMATION_SCHEMA for discovery")
    print("  - Proper tenant context for app database")
    print("  - Database-specific optimizations")
