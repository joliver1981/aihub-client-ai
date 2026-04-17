# config_db_client.py
import logging
import os
from contextlib import contextmanager
from CommonUtils import get_db_connection

# logging.basicConfig(filename='./logs/config_db_client_log.txt', level=logging.DEBUG, format='%(asctime)s [%(levelname)s] - %(message)s')

logger = logging.getLogger(__name__)

class ConfigDatabaseClient:
    """Enhanced database client with improved connection handling"""
    
    def __init__(self):
        """Initialize the client with lazy connection"""
        self._conn = None
        logger.debug("ConfigDatabaseClient initialized (connection deferred)")
    
    def _ensure_connection(self):
        """Ensure we have a valid connection, creating if needed"""
        if self._conn is None:
            try:
                self._conn = get_db_connection()
                self._set_tenant_context()
                logger.debug("New database connection established")
            except Exception as e:
                logger.error(f"Failed to establish database connection: {e}")
                raise

        # Test if the connection is still valid
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
        except Exception as e:
            logger.warning(f"Connection test failed, reconnecting: {e}")
            self.close()  # Explicitly close the broken connection
            self._conn = get_db_connection()
            self._set_tenant_context()
            logger.debug("Reconnected to database after connection failure")

    def _set_tenant_context(self):
        """Sets the tenant context using a stored procedure (for RLS)"""
        api_key = os.getenv("API_KEY")
        if not api_key:
            logger.error("API_KEY environment variable is not set.")
            raise EnvironmentError("API_KEY not set for tenant context.")
        
        try:
            cursor = self._conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", (api_key,))
            cursor.close()
            logger.debug("Tenant context set successfully")
        except Exception as e:
            logger.error(f"Failed to set tenant context: {e}")
            raise

    @contextmanager
    def cursor(self):
        """Context manager for database cursor with improved error handling"""
        self._ensure_connection()
        cursor = None
        try:
            cursor = self._conn.cursor()
            # Refresh tenant context on each operation for security
            api_key = os.getenv("API_KEY")
            cursor.execute("EXEC tenant.sp_setTenantContext ?", (api_key,))
            yield cursor
            self._conn.commit()
        except Exception as e:
            if self._conn:
                try:
                    self._conn.rollback()
                except Exception as rollback_error:
                    logger.warning(f"Error during rollback: {rollback_error}")
            
            # Check if this is a connection-related error
            error_code = getattr(e, 'args', [None])[0]
            connection_error_codes = ('08S01', '08007', '01000', 'HYT00', 'HY000')
            
            if isinstance(error_code, str) and any(code in error_code for code in connection_error_codes):
                logger.error(f"Connection error detected ({error_code}): {e}")
                self.close()  # Force reconnection on next operation
            else:
                logger.error(f"Database error during query: {e}")
            raise
        finally:
            if cursor:
                try:
                    cursor.close()
                except Exception as cursor_error:
                    logger.warning(f"Error closing cursor: {cursor_error}")

    def execute_query(self, sql, params=None):
        """Execute a query without returning results"""
        params = params or ()
        with self.cursor() as cur:
            cur.execute(sql, params)

    def fetch_query(self, sql, params=None):
        """Execute a query and return all results"""
        params = params or ()
        with self.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def fetch_one(self, sql, params=None):
        """Execute a query and return the first result"""
        params = params or ()
        with self.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()  # Returns first row or None

    def close(self):
        """Close the connection, handling exceptions"""
        if self._conn:
            try:
                self._conn.close()
                logger.debug("Database connection closed successfully")
            except Exception as e:
                logger.warning(f"Error closing database connection: {e}")
            finally:
                self._conn = None

    def __del__(self):
        """Destructor to ensure connection is closed"""
        self.close()
