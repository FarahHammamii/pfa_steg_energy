"""
utils/db.py
Neon Postgres connection pool and helper utilities.
"""
import contextlib
import logging
from typing import Generator, List, Tuple

import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extras import RealDictCursor, execute_values

from config.settings import NEON_DATABASE_URL

logger = logging.getLogger(__name__)

# Global connection pool (lazy-initialized)
_pool: pg_pool.ThreadedConnectionPool | None = None


def get_pool() -> pg_pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = pg_pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=5,
            dsn=NEON_DATABASE_URL,
        )
        logger.info("Neon connection pool initialized")
    return _pool


@contextlib.contextmanager
def get_conn():
    """Context manager: yields a connection from the pool, auto-returns it."""
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


@contextlib.contextmanager
def get_cursor(dict_cursor: bool = False) -> Generator:
    """Context manager: yields a cursor."""
    cursor_factory = RealDictCursor if dict_cursor else None
    with get_conn() as conn:
        with conn.cursor(cursor_factory=cursor_factory) as cur:
            yield cur


def execute_batch(sql: str, data: List[Tuple], page_size: int = 1000) -> int:
    """
    Bulk insert using execute_values for performance.
    Works with both single %s placeholder or multiple placeholders.
    
    For single placeholder: INSERT INTO table VALUES %s
    For multiple placeholders: INSERT INTO table (col1, col2) VALUES (%s, %s)
    """
    if not data:
        return 0
    
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Check if the query has multiple %s placeholders (for executemany)
            if sql.count('%s') > 1:
                # Use executemany for queries with multiple placeholders
                cur.executemany(sql, data)
                conn.commit()  # executemany doesn't auto-commit
                return len(data)
            else:
                # Use execute_values for single placeholder (more efficient)
                execute_values(cur, sql, data, page_size=page_size)
                return cur.rowcount


def execute_many(sql: str, data: List[Tuple]) -> int:
    """
    Simple batch insert using executemany for queries with multiple placeholders.
    This is an alternative to execute_batch if you prefer separate functions.
    """
    if not data:
        return 0
    
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, data)
            conn.commit()
            return len(data)


def table_row_count(schema: str, table: str) -> int:
    with get_cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {schema}.{table}")
        return cur.fetchone()[0]


def close_pool():
    global _pool
    if _pool:
        _pool.closeall()
        _pool = None
        logger.info("Neon connection pool closed")