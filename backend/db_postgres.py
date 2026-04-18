"""Postgres database layer using psycopg 3 with connection pool.

Keeps the legacy helper API (`get_conn`, `q`, `exec_`) for backward
compatibility, but adds v2 helpers:
  - `fetch_one(sql, params)`  → dict | None
  - `fetch_all(sql, params)`  → list[dict]
  - `execute(sql, params)`    → None
  - `execute_returning(sql, params, col='id')` → scalar (for INSERT ... RETURNING)
  - `transaction()` context manager for multi-statement txns

All queries use `%s` psycopg-style placeholders.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import DATABASE_URL

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def init(database_url: str | None = None, min_size: int = 2, max_size: int = 10) -> None:
    """Initialize the global connection pool. Safe to call multiple times."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            return
        url = database_url or DATABASE_URL
        _pool = ConnectionPool(
            conninfo=url,
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row},
            open=True,
        )
        # Probe the pool
        with _pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
    # Apply schema on first init
    from .schema import create_all
    create_all()


def close() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


def pool() -> ConnectionPool:
    if _pool is None:
        init()
    assert _pool is not None
    return _pool


# ---------- Legacy compatibility (sync cursor style) ----------

@contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    """Context manager yielding a psycopg connection from the pool.
    Auto-commits on success, rolls back on exception.
    """
    with pool().connection() as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def q(sql: str, params: tuple | list | dict = (), one: bool = False):
    """Legacy shorthand for SELECT queries. Returns dict or list[dict]."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if one:
                return cur.fetchone()
            return cur.fetchall()


def exec_(sql: str, params: tuple | list | dict = ()) -> Any:
    """Legacy shorthand for INSERT/UPDATE/DELETE.

    If the SQL contains ``RETURNING`` the first column of the first
    returned row is returned. Otherwise returns rowcount.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description:  # RETURNING clause produced rows
                row = cur.fetchone()
                if row is None:
                    return None
                # row is a dict; return the first value
                return next(iter(row.values()))
            return cur.rowcount


# ---------- v2 helpers ----------

def fetch_one(sql: str, params: tuple | list | dict = ()) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()


def fetch_all(sql: str, params: tuple | list | dict = ()) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def execute(sql: str, params: tuple | list | dict = ()) -> int:
    """Execute a statement (INSERT/UPDATE/DELETE without RETURNING)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount


def execute_returning(sql: str, params: tuple | list | dict = (), col: str = "id") -> Any:
    """Execute INSERT ... RETURNING <col>; return the scalar value."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row.get(col) if row else None


@contextmanager
def transaction() -> Iterator[psycopg.Connection]:
    """Explicit transaction — same as get_conn but named for clarity."""
    with get_conn() as conn:
        yield conn


# ---------- Convenience for SELECT FOR UPDATE SKIP LOCKED ----------

@contextmanager
def txn_cursor():
    """Yield (conn, cur) for row-locking queries; caller manages commit/rollback
    via the surrounding transaction context.
    """
    with pool().connection() as conn:
        try:
            with conn.cursor() as cur:
                yield conn, cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
