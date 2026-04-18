"""Database facade — dispatches to Postgres or SQLite based on config.

Set `DB_BACKEND=sqlite` in environment or config to use SQLite (personal mode).
Default is `postgres` (enterprise mode).

All other modules import from this file — never from db_postgres or db_sqlite
directly. The public API is identical regardless of backend:

  - init(), close()
  - fetch_one(sql, params) → dict | None
  - fetch_all(sql, params) → list[dict]
  - execute(sql, params) → int
  - execute_returning(sql, params, col='id') → scalar
  - get_conn() → context manager
  - txn_cursor() → context manager yielding (conn, cur)
  - pool() → ConnectionPool or None
"""
from __future__ import annotations

import os

from .config import CFG

_BACKEND = CFG.get("DB_BACKEND", os.environ.get("DB_BACKEND", "postgres"))

if _BACKEND == "sqlite":
    pass
else:
    pass
