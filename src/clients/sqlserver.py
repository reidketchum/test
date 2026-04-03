"""SQL Server database client for querying production transactions."""

import logging
from contextlib import contextmanager

import pyodbc
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class SqlServerClient:
    """Manages SQL Server connections and executes parameterized queries."""

    def __init__(self, connection_string: str):
        self.connection_string = connection_string

    @contextmanager
    def _get_connection(self):
        """Context manager for database connections."""
        conn = None
        try:
            conn = pyodbc.connect(self.connection_string, timeout=30)
            yield conn
        finally:
            if conn:
                conn.close()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def execute_query(self, query: str, params: tuple = ()) -> list[dict]:
        """Execute a parameterized query and return results as list of dicts.

        Args:
            query: SQL query with ? placeholders for parameters.
            params: Tuple of parameter values.

        Returns:
            List of dicts, each representing a row with column names as keys.
        """
        logger.debug("Executing query: %s with params: %s", query[:100], params)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            results = [dict(zip(columns, row)) for row in rows]
            logger.info("Query returned %d rows", len(results))
            return results

    def test_connection(self) -> bool:
        """Test that the database connection works."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
                logger.info("SQL Server connection test successful")
                return True
        except Exception as e:
            logger.error("SQL Server connection test failed: %s", e)
            return False
