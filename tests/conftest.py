"""Shared pytest helpers for the unit test suite.

NOTE: tests/regression/ has its own conftest.py — don't touch it. This
file only covers the older tests/test_*.py files (test_api_crud,
test_projects, test_services, test_v2).

Key responsibility: retry TRUNCATE operations on Postgres deadlock. The
autouse `clean_state` fixtures in those files do a bulk TRUNCATE between
tests, which can race with the previous test's background worker
connection and trip a `DeadlockDetected`. We expose a helper that wraps
the TRUNCATE with 3 retries + small backoff so CI stops flaking.
"""
from __future__ import annotations

import time

import pytest


def truncate_with_retry(cur, sql: str, attempts: int = 5) -> None:
    """Run a TRUNCATE statement with retry-on-deadlock. Call the cursor
    inside an already-open transaction; we rollback + retry on a
    DeadlockDetected / SerializationFailure and let the last attempt
    re-raise so CI still sees genuine bugs."""
    last = None
    for i in range(attempts):
        try:
            cur.execute(sql)
            return
        except Exception as e:  # noqa: BLE001
            last = e
            # psycopg raises these on lock races. Match by class name so we
            # don't need a direct import + work across psycopg 2/3.
            name = type(e).__name__
            if name not in ("DeadlockDetected", "SerializationFailure"):
                raise
            try:
                cur.connection.rollback()
            except Exception:
                pass
            time.sleep(0.1 * (i + 1))
    if last is not None:
        raise last
