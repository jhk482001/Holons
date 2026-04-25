"""Shared pytest helpers for the unit test suite.

NOTE: tests/regression/ has its own conftest.py — don't touch it. This
file only covers the older tests/test_*.py files (test_api_crud,
test_projects, test_services, test_v2).

Key responsibilities:

  1. Refuse to run if DATABASE_URL points at the developer's live working
     Postgres. Those tests' `autouse` fixtures TRUNCATE most tables, so
     accidentally running them against the dev DB wipes demo data
     (this exact mistake nuked 1300+ rows and was only recoverable
     because we had a fresh pg_dump backup). The opt-out env flag is
     intentionally awkward (`HOLONS_ALLOW_LIVE_DB=1`) so it can't be
     set casually.

  2. Retry TRUNCATE on Postgres deadlock — the autouse fixtures bulk
     TRUNCATE between tests, which races with background workers'
     transactions and trips DeadlockDetected. The retry helper below
     wraps each TRUNCATE with rollback + small backoff.
"""
from __future__ import annotations

import os
import time

import pytest


# A list of DATABASE_URL substrings that we treat as "definitely the
# developer's live working DB — destructive ops are disallowed unless
# the user explicitly opted in via HOLONS_ALLOW_LIVE_DB=1". Match on
# the canonical demo creds + port + dbname combo so a CI-provisioned
# Postgres on a different port / different password slips through fine.
_LIVE_DB_FINGERPRINTS = (
    "agent_company:devpassword@localhost:5432/agent_company",
    "agent_company:devpassword@127.0.0.1:5432/agent_company",
)


def _looks_like_live_dev_db(url: str) -> bool:
    return any(fp in url for fp in _LIVE_DB_FINGERPRINTS)


def pytest_configure(config: pytest.Config) -> None:
    """Hard-stop before any test runs if we'd be TRUNCATEing the live
    dev Postgres. Caller can opt in via `HOLONS_ALLOW_LIVE_DB=1` if
    they really do mean to point the test suite at the demo DB
    (rare — usually you want a throwaway test DB instead)."""
    url = os.environ.get("DATABASE_URL", "")
    allow = os.environ.get("HOLONS_ALLOW_LIVE_DB", "") in ("1", "true", "yes")
    if not allow and _looks_like_live_dev_db(url):
        msg = (
            "\n"
            "═══════════════════════════════════════════════════════════════\n"
            " REFUSING TO RUN TESTS AGAINST THE LIVE DEV POSTGRES\n"
            "═══════════════════════════════════════════════════════════════\n"
            f" DATABASE_URL = {url}\n"
            "\n"
            " The `autouse` clean_state fixtures in tests/test_*.py call\n"
            " TRUNCATE on ~25 tables. Running them against your demo DB\n"
            " (jay / molly / admin + their agents, runs, projects, …) will\n"
            " wipe the lot.\n"
            "\n"
            " To run the test suite, point at a throwaway DB:\n"
            "   docker run --rm -p 5433:5432 -e POSTGRES_PASSWORD=test \\\n"
            "       -e POSTGRES_USER=test -e POSTGRES_DB=test ankane/pgvector\n"
            "   DATABASE_URL=postgresql://test:test@localhost:5433/test \\\n"
            "       pytest tests/test_services.py\n"
            "\n"
            " Or — if you really do want to truncate the live dev DB —\n"
            " explicitly opt in:\n"
            "   HOLONS_ALLOW_LIVE_DB=1 pytest …\n"
            "═══════════════════════════════════════════════════════════════\n"
        )
        pytest.exit(msg, returncode=2)


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
