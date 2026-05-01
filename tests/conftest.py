"""Shared pytest helpers for the unit test suite.

NOTE: tests/regression/ has its own conftest.py — don't touch it. This
file only covers the older tests/test_*.py files (test_api_crud,
test_projects, test_services, test_v2, test_group_chat_history).

Key responsibilities:

  1. Refuse to run if the test would TRUNCATE the developer's live
     working Postgres. Those tests' `autouse` fixtures TRUNCATE most
     tables, so accidentally running them against the dev DB wipes
     demo data. This has happened **twice** now:

       - Once before the v0.5 audit (the "1300+ rows" incident).
       - Again on 2026-04-30 — the original guard checked only the
         shell env's DATABASE_URL, but `backend.db.init()` loads
         `.env` via dotenv at runtime, so a shell with empty
         DATABASE_URL bypassed the guard while the actual connection
         still hit the live DB.

     The current guard is defense-in-depth — it sniffs every plausible
     source for a live-DB connection target and refuses unless the
     caller has explicitly opted in:

       - shell env DATABASE_URL
       - `.env` file at the project root (the file dotenv reads)
       - the actual connection string after backend.db is initialised
         (checked from inside `clean_state` as a last-ditch interlock)

     Caller opts in via `HOLONS_ALLOW_LIVE_DB=1`. The flag is
     intentionally awkward so it can't be set casually.

  2. Retry TRUNCATE on Postgres deadlock — the autouse fixtures bulk
     TRUNCATE between tests, which races with background workers'
     transactions and trips DeadlockDetected. The retry helper below
     wraps each TRUNCATE with rollback + small backoff.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

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


def _read_env_file_database_url() -> str:
    """Read DATABASE_URL from the project's .env file, if present.

    The original guard only checked os.environ — but backend.db.init()
    loads .env via python-dotenv at runtime, so a shell session with
    no DATABASE_URL set still ends up connecting to whatever .env
    points to. Sniff the file directly so we catch that case before
    pytest_configure returns.
    """
    here = Path(__file__).resolve().parent
    env = here.parent / ".env"
    if not env.is_file():
        return ""
    try:
        text = env.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""
    # Strip comments and find the most relevant DATABASE_URL line.
    # Last assignment wins (matches dotenv semantics).
    found = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(?:export\s+)?DATABASE_URL\s*=\s*(.+?)\s*$", line)
        if not m:
            continue
        val = m.group(1)
        # Trim surrounding quotes if present.
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        found = val
    return found


def _allowed_to_truncate() -> bool:
    return os.environ.get("HOLONS_ALLOW_LIVE_DB", "") in ("1", "true", "yes")


def _abort_message(source: str, url: str) -> str:
    return (
        "\n"
        "═══════════════════════════════════════════════════════════════\n"
        " REFUSING TO RUN TESTS AGAINST THE LIVE DEV POSTGRES\n"
        "═══════════════════════════════════════════════════════════════\n"
        f" Source : {source}\n"
        f" URL    : {url}\n"
        "\n"
        " The `autouse` clean_state fixtures in tests/test_*.py call\n"
        " TRUNCATE on ~25 tables. Running them against your demo DB\n"
        " (jay / molly / admin + their agents, runs, projects, …) will\n"
        " wipe the lot. This guard exists because that exact mistake\n"
        " has now happened twice.\n"
        "\n"
        " To run the test suite, point at a throwaway DB:\n"
        "   docker run --rm -p 5433:5432 -e POSTGRES_PASSWORD=test \\\n"
        "       -e POSTGRES_USER=test -e POSTGRES_DB=test ankane/pgvector\n"
        "   DATABASE_URL=postgresql://test:test@localhost:5433/test \\\n"
        "       pytest tests/test_services.py\n"
        "\n"
        " Or — if you really do want to truncate the live dev DB —\n"
        " explicitly opt in (NOT RECOMMENDED):\n"
        "   HOLONS_ALLOW_LIVE_DB=1 pytest …\n"
        "═══════════════════════════════════════════════════════════════\n"
    )


def pytest_configure(config: pytest.Config) -> None:
    """Hard-stop before any test runs if we'd be TRUNCATEing the live
    dev Postgres. The check resolves the *effective* URL the way
    backend.db will: shell env DATABASE_URL wins (python-dotenv's
    default `load_dotenv()` does not override existing env vars), then
    falls back to the .env file if the shell didn't set one.
    """
    if _allowed_to_truncate():
        return

    shell_url = os.environ.get("DATABASE_URL", "")
    if shell_url:
        # Shell env wins — only block if THIS is the live DB. A test
        # run with shell DATABASE_URL pointing at a throwaway DB
        # should proceed even when .env still names the live DB.
        if _looks_like_live_dev_db(shell_url):
            pytest.exit(_abort_message("shell env DATABASE_URL", shell_url), returncode=2)
        return

    # No shell override → backend.db will pick up whatever .env has.
    file_url = _read_env_file_database_url()
    if _looks_like_live_dev_db(file_url):
        pytest.exit(
            _abort_message(".env file DATABASE_URL (no shell override)", file_url),
            returncode=2,
        )


def assert_safe_to_truncate() -> None:
    """Last-ditch interlock — call this from inside clean_state after
    backend.db.init() has resolved the actual connection string. Catches
    the (unlikely) case where neither the shell nor .env has a live-DB
    URL but db.init() picked one up from somewhere else (process env
    inherited from a parent, programmatic override, etc.)."""
    if _allowed_to_truncate():
        return
    try:
        from backend import db
        url = getattr(db, "DATABASE_URL", None) or db._resolved_database_url() if hasattr(db, "_resolved_database_url") else None
    except Exception:  # noqa: BLE001
        url = None
    if not url:
        # Couldn't introspect — fall through. Worst case the earlier
        # checks already covered both env sources.
        return
    if _looks_like_live_dev_db(url):
        pytest.exit(
            _abort_message("backend.db live connection", url),
            returncode=2,
        )


def truncate_with_retry(cur, sql: str, attempts: int = 5) -> None:
    """Run a TRUNCATE statement with retry-on-deadlock. Call the cursor
    inside an already-open transaction; we rollback + retry on a
    DeadlockDetected / SerializationFailure and let the last attempt
    re-raise so CI still sees genuine bugs.

    The truncate is preceded by a final safety check (see
    assert_safe_to_truncate). After the previous incident this is
    cheap insurance against future regressions in the upstream
    pytest_configure guard.
    """
    assert_safe_to_truncate()
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
